"""
CAMADA 1 — Por Dentro Content Pipeline
Gemini 2.0 Flash (Google Search grounding) → filtra por audiência → Notion rascunhos

Zero RSS. Zero listas de domínio. Zero filtros de keyword frágeis.
O Gemini pesquisa o Google em tempo real e retorna os artigos mais relevantes.

Roda toda sexta-feira às 7h UTC (via GitHub Actions).
Custo: Gemini free tier (15 RPM) + Notion API (gratuito).
"""

import os
import re
import json
import time
from datetime import datetime, timezone
from google import genai
from google.genai import types as genai_types
from notion_client import Client

# ─── Clientes ─────────────────────────────────────────────────────────────────
notion         = Client(auth=os.environ["NOTION_TOKEN"])
cliente_gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ─── Parâmetros globais ───────────────────────────────────────────────────────
MINIMO_POR_CATEGORIA  = 5
MAX_ARTIGOS_POR_FONTE = 8
SLEEP_ENTRE_CHAMADAS  = 5   # segundos entre chamadas Gemini (respeita 15 RPM free tier)

# ─── Filtro de nicho migratório — última linha de defesa de relevância ─────────
# O Gemini já filtra por contexto, mas este filtro garante que o artigo
# mencione explicitamente o público-alvo no título ou resumo.
FILTRO_NICHO_MIGRATORIO = [
    # Francês
    "étranger", "étrangère", "étrangers", "immigré", "immigrée",
    "immigration", "séjour", "brésil", "brésilien", "brésilienne",
    "titre", "expatrié", "expatriée", "visa", "ressortissant",
    "naturalisation", "régularisation", "non-résident",
    # Português
    "estrangeiro", "estrangeira", "imigrante", "imigração",
    "expatriado", "brasil", "brasileiro", "brasileira",
]

# ─── Mapeamento de valores Notion (case-sensitive) ────────────────────────────
_FORMATO_NOTION  = {5: "Reels", 4: "Reels", 3: "Carrossel", 2: "Carrossel", 1: "Stories"}
_URGENCIA_NOTION = {"alta": "Alta", "media": "media", "baixa": "Baixa"}
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
# Substituem FONTES_RSS: em vez de URL de feed, são queries em FR + PT.
# O Gemini pesquisa o Google em tempo real com Google Search grounding.

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def normalizar_texto(texto: str) -> str:
    texto = re.sub(r'<[^>]+>', ' ', texto or '')
    return re.sub(r'\s+', ' ', texto).strip()


def contem_nicho_migratorio(titulo: str, resumo: str) -> bool:
    """Garante que o artigo mencione explicitamente o público-alvo."""
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
    checklist = CHECKLIST_POR_CATEGORIA.get(fonte["categoria"], [])
    return "\n".join(checklist)


# ─── Gemini Search ────────────────────────────────────────────────────────────

def buscar_artigos_gemini(fonte: dict) -> list[dict]:
    """
    Chama Gemini 2.0 Flash com Google Search grounding.
    Retorna lista de artigos: {titulo, url, resumo, data_publicacao, publisher}
    """
    excluir_str = ", ".join(fonte.get("excluir", []))
    excluir_instrucao = f"\nNÃO inclua artigos sobre: {excluir_str}." if excluir_str else ""

    prompt = f"""Você é um assistente de pesquisa editorial para um canal sobre imigração na França.

Use o Google Search para encontrar artigos publicados nos últimos 30 dias sobre:

PESQUISA EM FRANCÊS: {fonte['query_fr']}
PESQUISA EM PORTUGUÊS: {fonte['query_pt']}{excluir_instrucao}

Foco: conteúdo útil para brasileiras que vivem ou querem viver na França.
Priorize: fontes oficiais francesas, jornais reconhecidos, guias práticos para imigrantes.

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

    try:
        response = cliente_gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            )
        )
        raw = response.text.strip()
        raw = re.sub(r'^```json\s*|^```|\s*```$', '', raw, flags=re.MULTILINE).strip()
        artigos = json.loads(raw)
        return artigos if isinstance(artigos, list) else []
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON inválido do Gemini: {e}")
        return []
    except Exception as e:
        print(f"  ✗ Erro na chamada Gemini: {e}")
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
                # "Formato" e "Enviar para Claude" — criados manualmente no Notion
            }
        )
    except Exception as e:
        print(f"  ⚠ Não foi possível verificar propriedades: {e}")


def criar_rascunho_notion(artigo: dict, fonte: dict) -> bool:
    titulo   = normalizar_texto(artigo.get("titulo", ""))[:200]
    resumo   = normalizar_texto(artigo.get("resumo", ""))[:500]
    url      = artigo.get("url", "")
    publisher = normalizar_texto(artigo.get("publisher", ""))[:100]
    data_pub  = artigo.get("data_publicacao")

    score         = fonte.get("score_conversao", 3)
    formato       = _FORMATO_NOTION.get(score, "Carrossel")
    urgencia_val  = _URGENCIA_NOTION.get(fonte["urgencia"], fonte["urgencia"])
    categoria_val = _CATEGORIA_NOTION.get(fonte["categoria"], fonte["categoria"])
    checklist     = gerar_template_editorial(fonte)
    palavras_chave = extrair_palavras_chave(titulo, resumo)

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
        "Palavras-chave":     {"rich_text":    [{"text": {"content": palavras_chave}}]},
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

def processar_fonte(fonte: dict, criados_por_categoria: dict,
                    urls_sessao: set) -> int:
    print(f"\n🔍 {fonte['nome']}")
    criados = 0
    cat     = fonte["categoria"]
    excluir = fonte.get("excluir", [])

    artigos = buscar_artigos_gemini(fonte)
    print(f"  → {len(artigos)} artigo(s) encontrado(s) pelo Gemini")

    for artigo in artigos:
        titulo = artigo.get("titulo", "")
        resumo = artigo.get("resumo", "")
        url    = artigo.get("url", "")

        if not url or not titulo:
            continue
        if url in urls_sessao:
            continue
        if contem_excluir(titulo, resumo, excluir):
            continue
        if not contem_nicho_migratorio(titulo, resumo):
            print(f"  ↩ Fora do nicho: {titulo[:60]}")
            continue
        if url_ja_existe_no_notion(url):
            print(f"  ↩ Duplicado: {titulo[:60]}")
            continue

        ok = criar_rascunho_notion(artigo, fonte)
        if ok:
            criados += 1
            urls_sessao.add(url)
            criados_por_categoria[cat] = criados_por_categoria.get(cat, 0) + 1
            print(f"  ✓ [score {fonte['score_conversao']}] [{cat}: {criados_por_categoria[cat]}] {titulo[:60]}")

    return criados


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    data_hoje = datetime.now().strftime('%d/%m/%Y')
    print(f"\n🔍 CAMADA 1 — Por Dentro ({data_hoje})")
    print(f"   {len(FONTES_GEMINI)} temas | Gemini 2.0 Flash + Google Search grounding")
    print(f"   Mínimo por categoria: {MINIMO_POR_CATEGORIA}\n")

    garantir_propriedades_notion()

    total_criados         = 0
    criados_por_categoria = {}
    urls_sessao           = set()   # dedup dentro da mesma execução

    for i, fonte in enumerate(FONTES_GEMINI):
        qtd = processar_fonte(fonte, criados_por_categoria, urls_sessao)
        total_criados += qtd

        # Rate limit: pausa entre chamadas (exceto na última)
        if i < len(FONTES_GEMINI) - 1:
            time.sleep(SLEEP_ENTRE_CHAMADAS)

    # ── Resumo ────────────────────────────────────────────────────────────────
    todas_categorias = set(f["categoria"] for f in FONTES_GEMINI)
    print(f"\n{'═' * 62}")
    print(f"✅ Camada 1 concluída: {total_criados} rascunho(s) criado(s) no Notion")
    for cat in sorted(todas_categorias):
        n    = criados_por_categoria.get(cat, 0)
        flag = "✓" if n >= MINIMO_POR_CATEGORIA else "⚠ abaixo do mínimo"
        print(f"   {flag}  {cat}: {n}")
    print("   Próximo: curadoria no Notion → marcar 'Enviar para Claude' → Camada 2")
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
