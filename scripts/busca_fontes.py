"""
CAMADA 1 — Por Dentro Content Pipeline
Gemini 2.5 Flash (Google Search grounding) → filtra por audiência → Notion rascunhos

Zero RSS. Zero listas de domínio. Zero filtros de keyword frágeis.
O Gemini pesquisa o Google em tempo real e retorna os artigos mais relevantes.

Roda toda sexta-feira às 7h UTC (via GitHub Actions).
Resiliência: retry com exponential backoff em erros 429/503.

PRÉ-ANÁLISE EDITORIAL (executada antes de qualquer busca):
  1. Lê a página de Planejamento Estratégico do Instagram no Notion
     → Extrai seção do mês atual + próximo + alertas editoriais permanentes
  2. Carrega pautas e rascunhos já existentes no banco de entrada
     → Deduplicação exata (URL) + semântica (overlap de palavras no título)
  3. Carrega conteúdos já programados no calendário Instagram (se configurado)
  4. Injeta todo esse contexto no prompt do Gemini
     → Busca apenas notícias novas que complementem a estratégia vigente
"""

import os
import re
import json
import time
from datetime import datetime, timezone

from google import genai
from google.genai import types as genai_types
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable
from notion_client import Client

# ─── Clientes ─────────────────────────────────────────────────────────────────
notion         = Client(auth=os.environ["NOTION_TOKEN"])
cliente_gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ── Databases/páginas opcionais — pré-análise editorial ───────────────────────
# ID da página "Planejamento Estratégico" do Instagram no Notion
# Exemplo: 383d602254ce815ba966c093da03eccc
NOTION_STRATEGY_PAGE_ID = os.environ.get("NOTION_STRATEGY_PAGE_ID", "")

# ID do banco de Calendário Editorial (para ler conteúdos programados)
DATABASE_CALENDARIO = os.environ.get("NOTION_CALENDAR_DB_ID", "")

# ─── Parâmetros globais ───────────────────────────────────────────────────────
MINIMO_POR_CATEGORIA  = 5
MAX_ARTIGOS_POR_FONTE = 8
SLEEP_ENTRE_TEMAS     = 5
RETRY_MAX_TENTATIVAS  = 3
RETRY_BASE_SLEEP      = 10   # segundos; dobra a cada retry (10 → 20 → 40)
OVERLAP_MIN_DUPLICATA = 3    # palavras (len > 4) em comum = tema duplicado

# ─── Meses em português ───────────────────────────────────────────────────────
MESES_PT = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL",
    5: "MAIO",    6: "JUNHO",     7: "JULHO", 8: "AGOSTO",
    9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO",
}

# ─── Filtro de nicho migratório ───────────────────────────────────────────────
FILTRO_NICHO_MIGRATORIO = [
    "étranger", "étrangère", "étrangers", "immigré", "immigrée",
    "immigration", "séjour", "brésil", "brésilien", "brésilienne",
    "titre", "expatrié", "expatriée", "visa", "ressortissant",
    "naturalisation", "régularisation", "non-résident",
    "estrangeiro", "estrangeira", "imigrante", "imigração",
    "expatriado", "brasil", "brasileiro", "brasileira",
]

# ─── Mapeamento de valores Notion (case-sensitive) ────────────────────────────
_FORMATO_NOTION   = {5: "Reels", 4: "Reels", 3: "Carrossel", 2: "Carrossel", 1: "Stories"}
_URGENCIA_NOTION  = {"alta": "Alta", "media": "media", "baixa": "Baixa"}
_CATEGORIA_NOTION = {"burocratica": "Burocratica", "civica": "Civica"}

# ─── Checklist editorial por categoria ────────────────────────────────────────
CHECKLIST_POR_CATEGORIA = {
    "burocratica": [
        "□ Qual é o PASSO 1 concreto? Deixar claro no slide de abertura",
        "□ Quais documentos são necessários? Listar todos em slide próprio",
        "□ Existe prazo ou data-limite? Destacar visualmente se sim",
        "□ Qual é o erro mais comum que brasileiras cometem nessa etapa?",
        "□ A plataforma (ANEF, Ameli, CAF) mudou recentemente? Mencionar",
    ],
    "juridica": [
        "□ O que mudou exatamente? (lei nova versus lei anterior)",
        "□ Quem é afetado: qual tipo de visto ou situação regularizada?",
        "□ Há prazo de entrada em vigor ou de adaptação?",
        "□ O que acontece na prática se ignorar? (consequência objetiva)",
        "□ Existem exceções relevantes? (étudiant vs salarié vs passeport talent)",
    ],
    "academica": [
        "□ Como isso difere do equivalente no Brasil? (ancoragem cultural)",
        "□ Existe plataforma ou site oficial para acessar o benefício?",
        "□ Há condição de elegibilidade específica para estrangeiras?",
        "□ Qual é o prazo ou janela temporal para agir?",
        "□ Quem já usou pode deixar depoimento nos comentários? (CTA)",
    ],
    "financas": [
        "□ Qual é o número concreto? (taxa, prazo, limite em €)",
        "□ O que isso representa em reais? (fazer a conversão)",
        "□ Existe risco ou armadilha que brasileiras precisam evitar?",
        "□ Como começar em 3 passos simples para quem nunca investiu?",
        "□ Qual instituição ou plataforma é a porta de entrada prática?",
    ],
    "civica": [
        "□ Onde fica exatamente? (endereço, arrondissement, URL oficial)",
        "□ É gratuito? Tem lista de espera ou processo seletivo?",
        "□ Público-alvo: estudante, trabalhadora, qualquer imigrante?",
        "□ Como entrar em contato ou se inscrever? (CTA direto)",
        "□ Chamar audiência nos comentários: 'Você já conhecia este espaço?'",
    ],
}

# ─── FONTES DE PESQUISA ────────────────────────────────────────────────────────
FONTES_GEMINI = [

    # ═══════════════════════════════════════════════════════════════════════════
    # BUROCRÁTICA
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Titre de séjour & ANEF",
        "query_fr": "titre de séjour ANEF renouvellement récépissé étranger France 2026",
        "query_pt": "título residência França ANEF renovação estrangeiro brasileiro 2026",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },
    {
        "nome": "APL & CAF — Aide au Logement",
        "query_fr": "APL CAF allocation logement étranger étudiant France 2025 2026",
        "query_pt": "APL auxílio moradia França estrangeiro estudante CAF",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": [],
    },
    {
        "nome": "Sécurité Sociale & Carte Vitale",
        "query_fr": "sécurité sociale carte vitale numéro étranger Ameli ouverture droits",
        "query_pt": "seguro saúde carte vitale França estrangeiro Ameli número sécu",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": [],
    },
    {
        "nome": "Impôts & Non-Résidents",
        "query_fr": "impôts étranger non-résident France déclaration prélèvement source 2026",
        "query_pt": "imposto renda França estrangeiro não residente declaração 2026",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 4,
        "excluir": [],
    },
    {
        "nome": "Permis de Conduire Étranger",
        "query_fr": "échange permis conduire étranger Brésil France préfecture procédure",
        "query_pt": "troca carteira motorista Brasil França prefeitura procedimento",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # JURÍDICA
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Loi Immigration & Réforme Séjour",
        "query_fr": "loi immigration réforme séjour droits étrangers France 2025 2026",
        "query_pt": "lei imigração reforma visto residência direitos estrangeiros França 2026",
        "categoria": "juridica",
        "personas": ["P01", "P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": ["réfugié", "asile", "Frontex", "Méditerranée", "naufrage"],
    },
    {
        "nome": "Naturalisation Française",
        "query_fr": "naturalisation française conditions délai dossier étranger 2025 2026",
        "query_pt": "naturalização francesa condições prazo documentos estrangeiro",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "excluir": ["réfugié", "asile"],
    },
    {
        "nome": "Régularisation & Changement de Statut",
        "query_fr": "régularisation changement statut étudiant salarié étranger France",
        "query_pt": "regularização mudança status estudante trabalhador estrangeiro França",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "VLS-TS & OFII",
        "query_fr": "VLS-TS OFII validation visa long séjour étranger démarche",
        "query_pt": "VLS-TS OFII validação visto longa estadia França procedimento",
        "categoria": "juridica",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": [],
    },
    {
        "nome": "Droits des Étrangers — Associations",
        "query_fr": "droits étrangers France GISTI Cimade aide juridique séjour",
        "query_pt": "direitos estrangeiros França apoio jurídico associações GISTI",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 3,
        "excluir": ["réfugié", "asile", "OQTF"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # ACADÊMICA
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Reconnaissance de Diplômes — ENIC-NARIC",
        "query_fr": "reconnaissance diplôme étranger France ENIC-NARIC attestation comparabilité",
        "query_pt": "reconhecimento diploma estrangeiro França equivalência ENIC-NARIC",
        "categoria": "academica",
        "personas": ["P01", "P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": [],
    },
    {
        "nome": "CPF & Formation Professionnelle",
        "query_fr": "CPF compte formation étranger résident France droits financement",
        "query_pt": "CPF formação profissional França estrangeiro residente direitos",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "excluir": [],
    },
    {
        "nome": "Emploi Cadres & Marché du Travail",
        "query_fr": "emploi cadres étranger France APEC recrutement marché travail 2026",
        "query_pt": "emprego executivo estrangeiro França mercado trabalho recrutamento 2026",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "excluir": [],
    },
    {
        "nome": "Auto-entrepreneur & Microentreprise",
        "query_fr": "auto-entrepreneur microentreprise étranger France titre séjour URSSAF",
        "query_pt": "microempresa autônomo empreendedor estrangeiro França",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "excluir": [],
    },
    {
        "nome": "Bourses & Études en France",
        "query_fr": "bourses étudiants étrangers Campus France financement études 2025 2026",
        "query_pt": "bolsa estudo França Campus France estudante estrangeiro 2026",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FINANÇAS
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Fiscalité Expatriés Brésil-France",
        "query_fr": "fiscalité expatrié Brésil France convention fiscale non-résident 2026",
        "query_pt": "tributação expatriado Brasil França bitributação saída definitiva imposto",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 5,
        "excluir": [],
    },
    {
        "nome": "PEA & Assurance Vie — Résidents Étrangers",
        "query_fr": "PEA assurance vie étranger résident fiscal France investissement épargne",
        "query_pt": "investimento França residente estrangeiro PEA poupança expatriado",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "excluir": [],
    },
    {
        "nome": "Transfert & Change Euro-Real",
        "query_fr": "transfert Brésil euros change euro real virement international expatrié",
        "query_pt": "transferência Brasil euros câmbio euro real enviar dinheiro",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "excluir": [],
    },
    {
        "nome": "Coût de la Vie & Pouvoir d'Achat",
        "query_fr": "coût vie inflation France INSEE 2025 2026 pouvoir achat expatrié",
        "query_pt": "custo vida França inflação poder compra expatriado 2026",
        "categoria": "financas",
        "personas": ["P01", "P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CÍVICA
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Associations & Réseaux Étrangers",
        "query_fr": "associations intégration étrangers Paris France réseau soutien communauté",
        "query_pt": "associações integração imigrantes Paris França rede apoio comunidade",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "excluir": [],
    },
    {
        "nome": "Français Langue Étrangère & Intégration",
        "query_fr": "cours français langue étrangère FLE intégration étranger France gratuit",
        "query_pt": "aprender francês estrangeiro integração França curso gratuito",
        "categoria": "civica",
        "personas": ["P02"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "excluir": [],
    },
]


# ─── Helpers gerais ───────────────────────────────────────────────────────────

def normalizar_texto(texto: str) -> str:
    texto = re.sub(r'<[^>]+>', ' ', texto or '')
    return re.sub(r'\s+', ' ', texto).strip()


def contem_nicho_migratorio(titulo: str, resumo: str) -> bool:
    texto = (titulo + ' ' + resumo).lower()
    return any(termo in texto for termo in FILTRO_NICHO_MIGRATORIO)


def contem_excluir(titulo: str, resumo: str, excluir: list) -> bool:
    if not excluir:
        return False
    texto = (titulo + ' ' + resumo).lower()
    return any(t.lower() in texto for t in excluir)


def url_ja_existe_no_notion(url: str) -> bool:
    try:
        r = notion.databases.query(
            database_id=DATABASE_ENTRADA,
            filter={"property": "Fonte", "url": {"equals": url}}
        )
        return len(r["results"]) > 0
    except Exception:
        return False


def extrair_palavras_chave(titulo: str, resumo: str) -> str:
    texto = (titulo + ' ' + resumo).lower()
    matches = [t for t in FILTRO_NICHO_MIGRATORIO if t in texto]
    return ", ".join(list(dict.fromkeys(matches))[:6])


def gerar_template_editorial(fonte: dict) -> str:
    return "\n".join(CHECKLIST_POR_CATEGORIA.get(fonte["categoria"], []))


def topico_ja_coberto(titulo: str, titulos_existentes: set) -> bool:
    """
    Retorna True se o título tem OVERLAP_MIN_DUPLICATA ou mais palavras significativas
    (len > 4) em comum com qualquer título já existente no banco.
    """
    palavras_novas = set(w for w in titulo.lower().split() if len(w) > 4)
    if not palavras_novas:
        return False
    for titulo_existente in titulos_existentes:
        palavras_existentes = set(w for w in titulo_existente.split() if len(w) > 4)
        if len(palavras_novas & palavras_existentes) >= OVERLAP_MIN_DUPLICATA:
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# PRÉ-ANÁLISE EDITORIAL
# ═══════════════════════════════════════════════════════════════════════════════

# ── Helpers para leitura de blocos Notion ─────────────────────────────────────

def _rich_text_para_str(rich_list: list) -> str:
    return " ".join(r.get("plain_text", "") for r in rich_list if r.get("plain_text"))


def _blocos_para_linhas(blocks: list) -> list[str]:
    """Converte lista de blocos Notion em linhas de texto."""
    linhas = []
    tipos_texto = (
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item", "callout",
        "quote", "toggle", "to_do",
    )
    for b in blocks:
        btype = b.get("type")
        if not btype:
            continue
        if btype in tipos_texto:
            rich = b.get(btype, {}).get("rich_text", [])
            texto = _rich_text_para_str(rich)
            if texto.strip():
                linhas.append(texto.strip())
        elif btype == "table_row":
            cells = b.get("table_row", {}).get("cells", [])
            row = " | ".join(_rich_text_para_str(c) for c in cells)
            if row.strip():
                linhas.append(row.strip())
        elif btype == "code":
            rich = b.get("code", {}).get("rich_text", [])
            texto = _rich_text_para_str(rich)
            if texto.strip():
                linhas.append(texto.strip())
    return linhas


def _buscar_blocos_recursivo(block_id: str, profundidade: int = 0, max_prof: int = 2) -> list:
    """Busca blocos de um ID de forma recursiva até max_prof níveis."""
    if profundidade > max_prof:
        return []
    todos = []
    try:
        cursor = None
        while True:
            kwargs = {"block_id": block_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            r = notion.blocks.children.list(**kwargs)
            todos.extend(r["results"])
            if not r.get("has_more"):
                break
            cursor = r.get("next_cursor")
        # Filhos de blocos com has_children (tabelas, toggles)
        for bloco in list(todos):
            if bloco.get("has_children"):
                filhos = _buscar_blocos_recursivo(bloco["id"], profundidade + 1, max_prof)
                todos.extend(filhos)
    except Exception as e:
        print(f"  ⚠ Erro ao buscar blocos (depth={profundidade}): {e}")
    return todos


# ── Funções de pré-análise ────────────────────────────────────────────────────

def carregar_pautas_existentes() -> tuple[set, set]:
    """
    Lê TODOS os registros do banco de entrada no Notion (qualquer status).
    Retorna:
      titulos_existentes : set de títulos em lowercase (deduplicação semântica)
      urls_preexistentes : set de URLs (deduplicação exata)
    """
    titulos, urls = set(), set()
    try:
        cursor = None
        while True:
            kwargs = {"database_id": DATABASE_ENTRADA, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            r = notion.databases.query(**kwargs)
            for page in r["results"]:
                t = page["properties"].get("Título", {})
                if t.get("title") and t["title"]:
                    titulos.add(t["title"][0]["plain_text"].lower().strip())
                u = page["properties"].get("Fonte", {})
                if u.get("url"):
                    urls.add(u["url"])
            if not r.get("has_more"):
                break
            cursor = r.get("next_cursor")
        print(f"  📚 {len(titulos)} pautas existentes no banco Notion")
        print(f"  🔗 {len(urls)} URLs já indexadas")
    except Exception as e:
        print(f"  ⚠ Erro ao carregar pautas existentes: {e}")
    return titulos, urls


def carregar_estrategia_instagram() -> str:
    """
    Lê a página de Planejamento Estratégico do Instagram no Notion
    (NOTION_STRATEGY_PAGE_ID = 383d602254ce815ba966c093da03eccc).

    Extrai:
      1. Seção do mês atual + próximo mês (calendário ativo)
      2. Alertas editoriais permanentes (P01 ausente, clusters subexplorados, etc.)

    Retorna string com o contexto resumido para injetar no prompt do Gemini.
    """
    if not NOTION_STRATEGY_PAGE_ID:
        print("  ℹ  NOTION_STRATEGY_PAGE_ID não configurado — estratégia Instagram ignorada")
        return ""

    try:
        agora        = datetime.now()
        mes_atual    = MESES_PT[agora.month]
        mes_seguinte = MESES_PT[(agora.month % 12) + 1]
        outros_meses = {m for m in MESES_PT.values() if m not in (mes_atual, mes_seguinte)}

        print(f"  📖 Lendo página de estratégia Instagram ({mes_atual}/{mes_seguinte})...")
        blocos = _buscar_blocos_recursivo(NOTION_STRATEGY_PAGE_ID, max_prof=2)
        linhas = _blocos_para_linhas(blocos)

        # Percorre as linhas identificando seções
        secao_mes      = []
        secao_alertas  = []
        cap_mes        = False
        cap_alertas    = False

        for linha in linhas:
            upper = linha.upper()

            # Detecta início da seção do mês atual ou seguinte
            if mes_atual in upper or mes_seguinte in upper:
                cap_mes     = True
                cap_alertas = False
            # Detecta seção de alertas editoriais
            elif "ALERTA" in upper and "EDITORIAL" in upper:
                cap_alertas = True
                cap_mes     = False
            # Para a captura do mês ao encontrar outro mês não-alvo
            elif cap_mes and any(m in upper for m in outros_meses):
                cap_mes = False

            if cap_mes and linha.strip():
                secao_mes.append(linha.strip())
            elif cap_alertas and linha.strip():
                secao_alertas.append(linha.strip())

        partes = []
        if secao_mes:
            partes.append(
                f"CALENDÁRIO INSTAGRAM ATIVO ({mes_atual} / {mes_seguinte}):\n"
                + "\n".join(secao_mes[:40])
            )
        if secao_alertas:
            partes.append(
                "ALERTAS EDITORIAIS PERMANENTES DO CANAL:\n"
                + "\n".join(secao_alertas[:12])
            )

        resultado = "\n\n".join(partes)[:1800]
        if resultado:
            print(f"  🎯 Estratégia carregada: {len(secao_mes)} linhas de calendário "
                  f"+ {len(secao_alertas)} alertas")
        else:
            print("  ⚠ Estratégia carregada mas seção do mês não encontrada na página")
        return resultado

    except Exception as e:
        print(f"  ⚠ Erro ao carregar estratégia Instagram: {e}")
        return ""


def carregar_calendario_programado() -> list[str]:
    """
    Busca no banco de Calendário Editorial os conteúdos NÃO publicados para Instagram.
    Retorna lista de títulos para completar o contexto de deduplicação.

    Requer: NOTION_CALENDAR_DB_ID no ambiente.
    Ajuste os nomes das propriedades conforme seu banco Notion se necessário.
    """
    programados = []
    if not DATABASE_CALENDARIO:
        print("  ℹ  NOTION_CALENDAR_DB_ID não configurado — calendário programado ignorado")
        return programados
    try:
        r = notion.databases.query(
            database_id=DATABASE_CALENDARIO,
            filter={
                "and": [
                    {"property": "Status",     "status":       {"does_not_equal": "Publicado"}},
                    {"property": "Plataforma", "multi_select": {"contains": "Instagram"}},
                ]
            },
            page_size=50
        )
        for page in r["results"]:
            t = page["properties"].get("Título", {})
            if t.get("title") and t["title"]:
                programados.append(t["title"][0]["plain_text"])
            tema = page["properties"].get("Tema", {})
            if tema.get("rich_text") and tema["rich_text"]:
                programados.append(tema["rich_text"][0]["plain_text"])
        print(f"  📅 {len(programados)} conteúdo(s) programados no calendário Instagram")
    except Exception as e:
        print(f"  ⚠ Erro ao carregar calendário programado: {e}")
    return programados


def montar_contexto_editorial(
    estrategia_instagram: str,
    programados_calendario: list[str],
) -> str:
    """
    Monta o bloco de contexto editorial que será injetado no prompt do Gemini.
    Instrui o modelo a buscar notícias que complementem a estratégia — sem repetir
    o que já está programado ou mapeado no canal.
    """
    partes = []

    if estrategia_instagram:
        partes.append(estrategia_instagram)

    if programados_calendario:
        lista = "\n".join(f"  - {t}" for t in programados_calendario[:15])
        partes.append(
            f"CONTEÚDOS JÁ PROGRAMADOS NO CALENDÁRIO (NÃO REPETIR ESSES TEMAS):\n{lista}"
        )

    if not partes:
        return ""

    return (
        "\n\n───────────────────────────────────────────────\n"
        "CONTEXTO EDITORIAL DO CANAL POR DENTRO (Instagram):\n"
        "───────────────────────────────────────────────\n"
        + "\n\n".join(partes)
        + "\n\nBUSQUE NOTÍCIAS QUE COMPLEMENTEM A ESTRATÉGIA ACIMA E TRAGAM "
        "ÂNGULOS NOVOS NÃO COBERTOS PELOS TEMAS JÁ PROGRAMADOS."
    )


# ─── Gemini Search com Retry + Exponential Backoff ───────────────────────────

def buscar_artigos_gemini(fonte: dict, contexto_editorial: str = "") -> list[dict]:
    """
    Chama Gemini 2.5 Flash com Google Search grounding.
    Injeta contexto editorial no prompt para evitar repetição de temas já cobertos.
    Implementa retry com exponential backoff para erros 429 e 503.
    """
    excluir_str = ", ".join(fonte.get("excluir", []))
    excluir_instrucao = f"\nNÃO inclua artigos sobre: {excluir_str}." if excluir_str else ""

    prompt = f"""Você é um assistente de pesquisa editorial para um canal sobre imigração na França.

Use o Google Search para encontrar artigos publicados nos últimos 30 dias sobre:

PESQUISA EM FRANCÊS: {fonte['query_fr']}
PESQUISA EM PORTUGUÊS: {fonte['query_pt']}{excluir_instrucao}{contexto_editorial}

Foco: conteúdo útil para brasileiras que vivem ou querem viver na França.
Priorize: fontes oficiais francesas, jornais reconhecidos, guias práticos para imigrantes.
Priorize notícias RECENTES com ÂNGULO NOVO que complementem a estratégia editorial acima.

Retorne um JSON array com até {MAX_ARTIGOS_POR_FONTE} artigos encontrados:
[
  {{
    "titulo": "título original do artigo",
    "url": "URL completa e válida",
    "resumo": "2-3 frases sobre o conteúdo e impacto para brasileiras imigrantes na França",
    "data_publicacao": "YYYY-MM-DD ou null",
    "publisher": "nome curto do site (ex: service-public.fr)"
  }}
]

IMPORTANTE: Retorne APENAS o JSON array. Sem markdown. Sem texto antes ou depois."""

    sleep_atual = RETRY_BASE_SLEEP

    for tentativa in range(1, RETRY_MAX_TENTATIVAS + 1):
        try:
            response = cliente_gemini.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
                )
            )
            raw = response.text.strip()
            raw = re.sub(r'^```json\s*|^```|\s*```$', '', raw, flags=re.MULTILINE).strip()
            artigos = json.loads(raw)
            return artigos if isinstance(artigos, list) else []

        except (ResourceExhausted,):
            if tentativa < RETRY_MAX_TENTATIVAS:
                print(f"  ⚠ Cota atingida (429). Aguardando {sleep_atual}s...")
                time.sleep(sleep_atual)
                sleep_atual *= 2
            else:
                print(f"  ✗ Cota atingida após {RETRY_MAX_TENTATIVAS} tentativas. Pulando.")
                return []

        except (ServiceUnavailable,):
            if tentativa < RETRY_MAX_TENTATIVAS:
                print(f"  ⚠ Serviço indisponível (503). Aguardando {sleep_atual}s...")
                time.sleep(sleep_atual)
                sleep_atual *= 2
            else:
                print(f"  ✗ Serviço indisponível após {RETRY_MAX_TENTATIVAS} tentativas. Pulando.")
                return []

        except json.JSONDecodeError as e:
            print(f"  ✗ JSON inválido do Gemini: {e}")
            return []

        except Exception as e:
            print(f"  ✗ Erro inesperado: {type(e).__name__}: {e}")
            return []

    return []


# ─── Notion ───────────────────────────────────────────────────────────────────

def garantir_propriedades_notion():
    """Cria propriedades customizadas no database se não existirem. Idempotente."""
    try:
        notion.databases.update(
            database_id=DATABASE_ENTRADA,
            properties={
                "Score Conversão":    {"number": {}},
                "Template Editorial": {"rich_text": {}},
                "Palavras-chave":     {"rich_text": {}},
                "Publisher":          {"rich_text": {}},
                "Data da Notícia":    {"date": {}},
            }
        )
    except Exception as e:
        print(f"  ⚠ Não foi possível verificar propriedades: {e}")


def criar_rascunho_notion(artigo: dict, fonte: dict) -> bool:
    titulo    = normalizar_texto(artigo.get("titulo", ""))[:200]
    resumo    = normalizar_texto(artigo.get("resumo", ""))[:500]
    url       = artigo.get("url", "")
    publisher = normalizar_texto(artigo.get("publisher", ""))[:100]
    data_pub  = artigo.get("data_publicacao")

    score         = fonte.get("score_conversao", 3)
    formato       = _FORMATO_NOTION.get(score, "Carrossel")
    urgencia_val  = _URGENCIA_NOTION.get(fonte["urgencia"], fonte["urgencia"])
    categoria_val = _CATEGORIA_NOTION.get(fonte["categoria"], fonte["categoria"])
    checklist     = gerar_template_editorial(fonte)
    palavras_ch   = extrair_palavras_chave(titulo, resumo)

    properties = {
        "Título":             {"title":        [{"text": {"content": titulo}}]},
        "Categoria":          {"multi_select": [{"name": categoria_val}]},
        "Persona":            {"multi_select": [{"name": p} for p in fonte["personas"]]},
        "Urgência":           {"select":        {"name": urgencia_val}},
        "Fonte":              {"url": url},
        "Notas":              {"rich_text":    [{"text": {"content": resumo}}]},
        "Status":             {"select":        {"name": "Rascunho"}},
        "Score Conversão":    {"number": score},
        "Formato":            {"select":        {"name": formato}},
        "Template Editorial": {"rich_text":    [{"text": {"content": checklist}}]},
        "Palavras-chave":     {"rich_text":    [{"text": {"content": palavras_ch}}]},
        "Publisher":          {"rich_text":    [{"text": {"content": publisher}}]},
    }

    if data_pub:
        properties["Data da Notícia"] = {"date": {"start": data_pub}}

    try:
        notion.pages.create(
            parent={"database_id": DATABASE_ENTRADA},
            properties=properties
        )
        return True
    except Exception as e:
        print(f"    ✗ Erro ao criar no Notion: {e}")
        return False


# ─── Processador por fonte ────────────────────────────────────────────────────

def processar_fonte(
    fonte: dict,
    criados_por_categoria: dict,
    urls_sessao: set,
    titulos_existentes: set,
    urls_preexistentes: set,
    contexto_editorial: str,
) -> int:
    print(f"\n🔍 {fonte['nome']}")
    criados = 0
    cat     = fonte["categoria"]

    artigos = buscar_artigos_gemini(fonte, contexto_editorial)
    print(f"  → {len(artigos)} artigo(s) retornado(s) pelo Gemini")

    for artigo in artigos:
        titulo = artigo.get("titulo", "")
        resumo = artigo.get("resumo", "")
        url    = artigo.get("url", "")

        if not url or not titulo:
            continue
        if url in urls_sessao:
            continue
        if url in urls_preexistentes:
            print(f"  ↩ URL já indexada: {titulo[:60]}")
            continue
        if contem_excluir(titulo, resumo, fonte.get("excluir", [])):
            continue
        if not contem_nicho_migratorio(titulo, resumo):
            print(f"  ↩ Fora do nicho: {titulo[:60]}")
            continue
        if topico_ja_coberto(titulo, titulos_existentes):
            print(f"  ↩ Tema já coberto: {titulo[:60]}")
            continue
        if url_ja_existe_no_notion(url):
            print(f"  ↩ Duplicado (Notion check): {titulo[:60]}")
            urls_preexistentes.add(url)
            continue

        ok = criar_rascunho_notion(artigo, fonte)
        if ok:
            criados += 1
            urls_sessao.add(url)
            urls_preexistentes.add(url)
            titulos_existentes.add(titulo.lower().strip())
            criados_por_categoria[cat] = criados_por_categoria.get(cat, 0) + 1
            print(f"  ✓ [score {fonte['score_conversao']}] [{cat}: {criados_por_categoria[cat]}] "
                  f"{titulo[:60]}")

    return criados


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    data_hoje   = datetime.now().strftime('%d/%m/%Y')
    total_temas = len(FONTES_GEMINI)

    print(f"\n{'═' * 62}")
    print(f"🔍 CAMADA 1 — Por Dentro ({data_hoje})")
    print(f"   {total_temas} temas | Gemini 2.5 Flash + Google Search grounding")
    print(f"   Pausa entre temas: {SLEEP_ENTRE_TEMAS}s | "
          f"Retry em 429/503: até {RETRY_MAX_TENTATIVAS}x (backoff {RETRY_BASE_SLEEP}s base)")
    print(f"   Mínimo por categoria: {MINIMO_POR_CATEGORIA}")
    print(f"{'═' * 62}\n")

    # ── PRÉ-ANÁLISE EDITORIAL ──────────────────────────────────────────────────
    print("📋 PRÉ-ANÁLISE EDITORIAL — carregando contexto do Notion...\n")

    # 1. Pautas e URLs já existentes (anti-repetição dupla)
    titulos_existentes, urls_preexistentes = carregar_pautas_existentes()

    # 2. Estratégia Instagram: calendário do mês atual + alertas editoriais permanentes
    estrategia_instagram = carregar_estrategia_instagram()

    # 3. Conteúdos já programados no banco de calendário (se configurado)
    programados_calendario = carregar_calendario_programado()

    # 4. Monta bloco de contexto editorial para o Gemini
    contexto_editorial = montar_contexto_editorial(
        estrategia_instagram,
        programados_calendario,
    )

    if contexto_editorial:
        print(f"\n  ✅ Contexto editorial montado ({len(contexto_editorial)} chars) "
              f"— Gemini buscará apenas temas novos e complementares")
    else:
        print("\n  ℹ  Sem contexto editorial carregado — buscando com critérios padrão")

    print(f"\n{'─' * 62}")
    print("🚀 Iniciando buscas...\n")

    # ── LOOP DE BUSCA ──────────────────────────────────────────────────────────
    garantir_propriedades_notion()

    total_criados         = 0
    criados_por_categoria = {}
    urls_sessao           = set()

    for i, fonte in enumerate(FONTES_GEMINI):
        qtd = processar_fonte(
            fonte,
            criados_por_categoria,
            urls_sessao,
            titulos_existentes,
            urls_preexistentes,
            contexto_editorial,
        )
        total_criados += qtd

        if i < total_temas - 1:
            print(f"  ⏳ Aguardando {SLEEP_ENTRE_TEMAS}s para respeitar a cota da API...")
            time.sleep(SLEEP_ENTRE_TEMAS)

    # ── Resumo final ──────────────────────────────────────────────────────────
    todas_categorias = sorted(set(f["categoria"] for f in FONTES_GEMINI))
    print(f"\n{'═' * 62}")
    print(f"✅ Camada 1 concluída: {total_criados} rascunho(s) criado(s) no Notion")
    for cat in todas_categorias:
        n    = criados_por_categoria.get(cat, 0)
        flag = "✓" if n >= MINIMO_POR_CATEGORIA else "⚠ abaixo do mínimo"
        print(f"   {flag}  {cat}: {n}")
    print("   Próximo: curadoria no Notion → marcar 'Enviar para Claude' → Camada 2")
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
