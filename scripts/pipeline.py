"""
CAMADA 2 — Por Dentro Content Pipeline
Lê rascunhos marcados no Notion → estrutura com Claude → devolve ao Notion como pautas prontas

Claude analisa cada pauta e decide:
  • CTA Comentário+Automação → tema burocrático/urgente, produto/oferta específica do catálogo
  • Newsletter              → tema analítico/profundo, nutre leads das landing pages
  • Ambos                   → tem as duas camadas
  • Orgânico                → construção de comunidade/confiança, sem CTA de conversão

O script busca páginas ativas em 'Páginas Online', identifica os Produtos Digitais
vinculados e passa esse catálogo para Claude gerar CTAs com o produto/oferta real.

Roda toda segunda-feira às 08h UTC (via GitHub Actions).
Só processa rascunhos com o checkbox "Enviar para Claude" marcado.
"""

import os
import json
import re
from datetime import date, timedelta
from notion_client import Client
import anthropic

# ─── Clientes ─────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DATABASE_ENTRADA     = os.environ["NOTION_DATABASE_ID"].strip()
DATABASE_SAIDA       = os.environ["NOTION_DATABASE_PAUTASPRONTAS_ID"].strip()
PAGINAS_ONLINE_DB_ID = "f16d6022-54ce-8325-a778-87c5e078b5b5"   # Páginas Online (landing pages)

# ── Opcionais — ativam contexto estratégico ───────────────────────────────────
NOTION_STRATEGY_PAGE_ID = os.environ.get("NOTION_STRATEGY_PAGE_ID", "").strip()
NOTION_CALENDAR_DB_ID   = os.environ.get("NOTION_CALENDAR_DB_ID",   "").strip()

# ─── Contextos editoriais (carregados de arquivo — não negociável para qualidade autônoma) ──
_script_dir       = os.path.dirname(os.path.abspath(__file__))
BRAND_CONTEXT     = open(os.path.join(_script_dir, "brand_context.txt"),    encoding="utf-8").read()
AUDIENCIA_CONTEXT = open(os.path.join(_script_dir, "audiencia_context.txt"), encoding="utf-8").read()

# ─── Valores válidos para seletores Notion ────────────────────────────────────
_KPI_VALIDOS       = ["Salvamento alto", "Compartilhamento alto", "Comentário alto", "Alcance"]
_CTA_VALIDOS       = ["Comentário+Automação", "Newsletter", "Ambos", "Orgânico"]
_FORMATO_VALIDOS   = ["Carrossel", "Reels", "Stories"]
_PILAR_VALIDOS     = ["Sistema", "Trajetória", "Identidade", "Sociedade"]
_VOICETONE_VALIDOS = ["Observador", "Explicativo", "Sentimental", "Humor"]
_URGENCIA_VALIDOS  = {"alta": "Alta", "media": "media", "baixa": "Baixa"}

# ─── Calendário sazonal fixo ──────────────────────────────────────────────────
# Usado para antecipar a data sugerida quando o tema é sazonal para o mês atual
_SAZONALIDADE = {
    1:  "voeux, caf, início ano",
    2:  "imposto, renda, ir, declaração",
    3:  "imposto, renda, ir, declaração",
    4:  "imposto, renda, ir, declaração",
    5:  "feriado, verão, férias",
    6:  "letivo, verão, férias",
    7:  "verão, férias, agosto",
    8:  "verão, férias, rentrée",
    9:  "rentrée, volta, aulas, rotina",
    10: "energia, aquecimento, outono",
    11: "black friday, compras",
    12: "natal, réveillon, ano novo",
}


# ─── DATA DE PUBLICAÇÃO SUGERIDA ─────────────────────────────────────────────
#
# Lógica de dia da semana por tipo de conteúdo:
#   Segunda  (0): Reels informativo sério — pessoas engajadas para aprender
#   Terça    (1): Carrossel complementar — aprofunda o que não coube no Reels
#   Quarta   (2): Info positiva/fácil/de valor concreto — endereços, dicas, cases
#   Quinta   (3): Antecipação — abertura de vagas, mudança de regras, alerta
#   Sexta    (4): Boa notícia
#   Sábado   (5): PAUSA — não publicar
#   Domingo  (6): Inspiração, reflexão, se organizar pro futuro
#
_NOMES_DIA = {0: "segunda", 1: "terça", 2: "quarta",
              3: "quinta",  4: "sexta", 5: "sábado", 6: "domingo"}


def _dia_ideal(formato: str, categoria: str, urgencia: str,
               pilar: str, kpi: str, palavras_chave: str) -> int:
    """
    Retorna o dia da semana ideal (0=seg … 6=dom) para este conteúdo.
    Sábado (5) é sempre excluído — dia de pausa no canal.
    """
    cat  = categoria.lower()
    kwds = palavras_chave.lower()
    urg  = urgencia.lower().strip()

    # Quinta: conteúdo de antecipação — alerta, mudança de regra, abertura
    _antecipacao = any(w in kwds for w in [
        "mudança", "nova lei", "alteração", "abertura", "vagas", "prazo",
        "alerta", "atenção", "novo decreto", "portaria", "circular",
    ])
    if _antecipacao or (urg == "alta" and cat in ("juridica",)):
        return 3  # quinta

    # Segunda ou Quarta: Reels informativo (burocrática/jurídica)
    # Segunda = urgente/sério; Quarta = positivo/concreto/fácil
    if formato == "Reels" and cat in ("juridica", "burocratica"):
        return 0  # segunda — tom sério
    if formato == "Reels" and cat == "civica":
        return 2  # quarta — tom positivo/leve

    # Terça: Carrossel complementar/aprofundamento
    if formato == "Carrossel" and cat in ("academica", "financas"):
        return 1  # terça

    # Quarta: positivo, concreto, fácil de consumir
    if cat == "civica" or pilar == "Identidade" or kpi == "Compartilhamento alto":
        return 2  # quarta

    # Domingo: inspiração / trajetória / reflexão
    if pilar == "Trajetória":
        return 6  # domingo

    # Sexta: boa notícia (fallback positivo)
    if kpi in ("Compartilhamento alto", "Alcance"):
        return 4  # sexta

    # Fallback por urgência → distribui pela semana
    if urg == "alta":
        return 0   # segunda
    elif urg in ("media", "média"):
        return 2   # quarta
    else:
        return 6   # domingo


def calcular_data_publicacao(
    resultado_claude: dict,
    palavras_chave: str,
    datas_usadas: dict,   # {date_iso: count} — modificado in-place para evitar empilhamento
) -> str:
    """
    Retorna a data ISO do próximo slot disponível para este conteúdo,
    respeitando o dia ideal da semana e evitando empilhar múltiplos
    posts no mesmo dia.

    Sábado é sempre pulado. Se o dia ideal já tiver 1 post nesta rodada,
    tenta o mesmo dia na semana seguinte (max 4 semanas de antecedência).
    Sazonalidade ainda promove urgência "baixa" → "media".
    """
    hoje         = date.today()
    urgencia     = resultado_claude.get("urgency", "baixa")
    categoria    = resultado_claude.get("categoria", "")
    formato      = resultado_claude.get("format", "Carrossel")
    pilar        = resultado_claude.get("pilar", "Sistema")
    kpi          = resultado_claude.get("kpi", "Salvamento alto")

    # Verificar sazonalidade — promove urgência se tema bate com mês atual
    palavras_sazonais  = _SAZONALIDADE.get(hoje.month, "").split(", ")
    tema_sazonal_ativo = any(
        p in palavras_chave.lower()
        for p in palavras_sazonais if len(p) > 3
    )
    urgencia_norm = urgencia.lower().strip()
    if tema_sazonal_ativo and urgencia_norm == "baixa":
        urgencia_norm = "media"

    # Offset de semana por urgência
    if urgencia_norm == "alta":
        semana_offset = 0
    elif urgencia_norm in ("media", "média"):
        semana_offset = 1
    else:
        semana_offset = 2

    # Dia ideal para este tipo de conteúdo
    dia_alvo = _dia_ideal(formato, categoria, urgencia_norm, pilar, kpi, palavras_chave)

    # Encontra próxima ocorrência do dia alvo com slot disponível
    for extra_semanas in range(semana_offset, semana_offset + 4):
        dias_ate_alvo = (dia_alvo - hoje.weekday()) % 7
        if dias_ate_alvo < 2:      # prazo mínimo de produção = 2 dias
            dias_ate_alvo += 7
        candidata = hoje + timedelta(days=dias_ate_alvo) + timedelta(weeks=extra_semanas)
        data_iso  = candidata.isoformat()

        if datas_usadas.get(data_iso, 0) < 1:   # slot livre
            datas_usadas[data_iso] = datas_usadas.get(data_iso, 0) + 1
            return data_iso

    # Fallback: aceita empilhamento se nenhum slot livre em 4 semanas
    datas_usadas[data_iso] = datas_usadas.get(data_iso, 0) + 1
    return data_iso


# ─── CONTEXTO ESTRATÉGICO ────────────────────────────────────────────────────
def carregar_estrategia() -> str:
    """
    Lê a página de Planejamento Estratégico no Notion e extrai o texto relevante.
    Retorna string vazia se NOTION_STRATEGY_PAGE_ID não estiver configurado.
    O conteúdo é injetado no prompt do Claude para sugestões alinhadas ao plano Q3/Q4.
    """
    if not NOTION_STRATEGY_PAGE_ID:
        print("  ℹ NOTION_STRATEGY_PAGE_ID não configurado — estratégia não carregada.")
        return ""
    try:
        blocks = notion.blocks.children.list(block_id=NOTION_STRATEGY_PAGE_ID, page_size=50)
        linhas = []
        for b in blocks.get("results", []):
            bt = b.get("type", "")
            rich = b.get(bt, {}).get("rich_text", [])
            texto = "".join(r.get("plain_text", "") for r in rich).strip()
            if texto:
                linhas.append(texto)
        conteudo = "\n".join(linhas)[:3000]   # cap para não explodir o prompt
        print(f"  ✓ Estratégia carregada: {len(linhas)} blocos, {len(conteudo)} chars.")
        return conteudo
    except Exception as e:
        print(f"  ⚠ Não foi possível carregar estratégia: {e}")
        return ""


def carregar_calendario_instagram() -> dict:
    """
    Lê o calendário editorial do Instagram e retorna {date_iso: count}
    com as datas que já têm post programado (Data Publicação preenchida).

    Esse dict é usado para pré-popular datas_usadas em main(), garantindo
    que as novas pautas não sejam sugeridas para dias já ocupados.
    """
    datas: dict = {}
    if not NOTION_CALENDAR_DB_ID:
        print("  ℹ NOTION_CALENDAR_DB_ID não configurado — calendário não carregado.")
        return datas
    try:
        resp = notion.databases.query(
            database_id=NOTION_CALENDAR_DB_ID,
            filter={
                "and": [
                    {"property": "Status",           "status": {"does_not_equal": "Publicado"}},
                    {"property": "Plataforma",        "multi_select": {"contains": "Instagram"}},
                    {"property": "Data Publicação",   "date": {"is_not_empty": True}},
                ]
            },
            page_size=100,
        )
        for page in resp.get("results", []):
            dp = page["properties"].get("Data Publicação", {}).get("date")
            if dp and dp.get("start"):
                data_iso = dp["start"][:10]
                datas[data_iso] = datas.get(data_iso, 0) + 1

        print(f"  ✓ Calendário Instagram: {len(datas)} data(s) já ocupada(s).")
        return datas
    except Exception as e:
        print(f"  ⚠ Não foi possível carregar calendário Instagram: {e}")
        return {}


# ─── CATÁLOGO DE PRODUTOS ─────────────────────────────────────────────────────
def carregar_catalogo_produtos() -> list[dict]:
    """
    Busca Páginas Online com Status 'Ativa' ou 'Em produção' no Notion.
    Para cada página, busca os Produtos Digitais vinculados e retorna um catálogo:
    [{pagina_id, pagina_url, oferta, cta, objetivo, produtos: [{nome, status, objetivo_negocio}]}]

    O catálogo é passado para Claude, que usa o nome real do produto/oferta no CTA copy.
    """
    catalogo = []
    try:
        resp = notion.databases.query(
            database_id=PAGINAS_ONLINE_DB_ID,
            filter={
                "or": [
                    {"property": "Status", "status": {"equals": "Ativa"}},
                    {"property": "Status", "status": {"equals": "Em produção"}},
                    {"property": "Status", "status": {"equals": "Em teste A/B"}},
                ]
            }
        )
    except Exception as e:
        print(f"  ⚠ Não foi possível carregar catálogo de produtos: {e}")
        return []

    for page in resp.get("results", []):
        props = page["properties"]

        def _text(nome):
            rt = props.get(nome, {}).get("rich_text", [])
            return rt[0]["text"]["content"] if rt else ""

        def _url(nome):
            return props.get(nome, {}).get("url") or ""

        def _select(nome):
            s = props.get(nome, {}).get("select")
            return s["name"] if s else ""

        def _relation_ids(nome):
            return [r["id"] for r in props.get(nome, {}).get("relation", [])]

        pagina = {
            "pagina_id":  page["id"],
            "pagina_url": _url("URL"),
            "oferta":     _text("Oferta/Isca"),
            "cta":        _text("CTA"),
            "objetivo":   _select("Objetivo"),
            "produtos":   [],
        }

        for prod_id in _relation_ids("Produtos Digitais"):
            try:
                prod_page  = notion.pages.retrieve(page_id=prod_id)
                prod_props = prod_page["properties"]

                nome_list = prod_props.get("Nome do Produto", {}).get("title", [])
                nome      = nome_list[0]["text"]["content"] if nome_list else ""

                status_s  = prod_props.get("Status", {}).get("select")
                status    = status_s["name"] if status_s else ""

                obj_rt      = prod_props.get("Objetivo de Negócio", {}).get("rich_text", [])
                obj_negocio = obj_rt[0]["text"]["content"] if obj_rt else ""

                if nome:
                    pagina["produtos"].append({
                        "nome":             nome,
                        "status":           status,
                        "objetivo_negocio": obj_negocio,
                    })
            except Exception as e:
                print(f"  ⚠ Erro ao buscar produto {prod_id}: {e}")

        catalogo.append(pagina)

    n = len(catalogo)
    total_prods = sum(len(p["produtos"]) for p in catalogo)
    print(f"  ✓ Catálogo carregado: {n} página(s) ativa(s), {total_prods} produto(s) vinculado(s)")
    return catalogo


# ─── Garantir propriedades no banco de ENTRADA ────────────────────────────────
def garantir_propriedades_entrada():
    """Cria 'Enviar para Claude' (checkbox) se ainda não existir. Idempotente."""
    try:
        notion.databases.update(
            database_id=DATABASE_ENTRADA,
            properties={"Enviar para Claude": {"checkbox": {}}}
        )
        print("  ✓ Banco de entrada: propriedades verificadas.")
    except Exception as e:
        print(f"  ⚠ Banco de entrada: {e}")


# ─── Garantir propriedades no banco de SAÍDA ──────────────────────────────────
def garantir_propriedades_saida():
    """
    Cria/atualiza todas as propriedades necessárias no banco 'Pautas Prontas'.
    Idempotente — pode rodar toda vez sem efeito colateral.
    """
    try:
        notion.databases.update(
            database_id=DATABASE_SAIDA,
            properties={
                "Formato":    {"select": {}},
                "KPI":        {"select": {}},
                "Urgência":   {"select": {}},
                "Categoria":  {"select": {}},
                "Persona":    {"multi_select": {}},
                "Status":     {"select": {}},
                "Gancho":                {"rich_text": {}},
                "Descricao":             {"rich_text": {}},
                "Instrução de Produção": {"rich_text": {}},
                "Fonte":                 {"rich_text": {}},
                "Fonte URL":             {"url": {}},
                "Score Conversão":       {"number": {}},
                "Palavras-chave":        {"rich_text": {}},
                "CTA Tipo":          {"select": {}},
                "CTA Copy":          {"rich_text": {}},
                "Ângulo Newsletter": {"rich_text": {}},
                "Produto Sugerido":  {"rich_text": {}},
                "Landing Page URL":  {"url": {}},
                "Pilar":      {"select": {}},
                "Voice Tone": {"select": {}},
                "Data Publicação": {"date": {}},
                # ── Campos de navegação rápida ────────────────────────────────
                "Vira Newsletter?": {"select": {}},
                "Dia Sugerido":     {"rich_text": {}},
            }
        )
        print("  ✓ Banco de saída: propriedades verificadas.")
    except Exception as e:
        print(f"  ⚠ Banco de saída: {e}")


# ─── FUNÇÃO 1: Buscar rascunhos marcados ──────────────────────────────────────
def buscar_para_processar() -> list[dict]:
    response = notion.databases.query(
        database_id=DATABASE_ENTRADA,
        filter={
            "property": "Enviar para Claude",
            "checkbox": {"equals": True}
        }
    )

    pautas = []
    for page in response["results"]:
        props = page["properties"]

        def _titulo():
            t = props.get("Título", {}).get("title", [])
            return t[0]["text"]["content"] if t else ""

        def _select(nome):
            s = props.get(nome, {}).get("select")
            return s["name"] if s else ""

        def _multiselect(nome):
            return [o["name"] for o in props.get(nome, {}).get("multi_select", [])]

        def _url(nome):
            return props.get(nome, {}).get("url") or ""

        def _rich_text(nome):
            rt = props.get(nome, {}).get("rich_text", [])
            return rt[0]["text"]["content"] if rt else ""

        def _number(nome):
            return props.get(nome, {}).get("number") or 0

        categorias = _multiselect("Categoria")
        categoria  = categorias[0] if categorias else "Burocratica"

        pautas.append({
            "notion_page_id":   page["id"],
            "titulo_bruto":     _titulo(),
            "categoria":        categoria,
            "personas":         _multiselect("Persona"),
            "urgencia":         _select("Urgência"),
            "fonte_url":        _url("Fonte"),
            "notas":            _rich_text("Notas"),
            "score_conversao":  _number("Score Conversão"),
            "palavras_chave":   _rich_text("Palavras-chave"),
            "formato_sugerido": _select("Formato"),
            "publisher":        _rich_text("Publisher"),
        })

    return pautas


# ─── FUNÇÃO 2: Estruturar com Claude ─────────────────────────────────────────
def estruturar_com_claude(pauta: dict, catalogo: list[dict],
                          estrategia: str = "") -> dict:
    personas_str     = " e ".join(pauta["personas"]) if pauta["personas"] else "P02"
    formato_sugerido = pauta["formato_sugerido"] or "Carrossel"
    score            = pauta["score_conversao"]

    if catalogo:
        entradas = []
        for p in catalogo:
            prods = " / ".join(pr["nome"] for pr in p["produtos"]) or "sem produto"
            entradas.append(f'- {p["oferta"] or p["objetivo"]} | produto: {prods} | url: {p["pagina_url"]}')
        catalogo_str = "PÁGINAS ATIVAS:\n" + "\n".join(entradas)
    else:
        catalogo_str = "PÁGINAS ATIVAS: nenhuma. Use CTA genérico."

    estrategia_bloco = (
        f"\nPLANEJAMENTO ESTRATÉGICO DO CANAL (Q3/Q4 — use para alinhar ângulo, pilar e CTA):\n{estrategia}\n"
        if estrategia else ""
    )

    prompt = f"""{BRAND_CONTEXT}

{AUDIENCIA_CONTEXT}

{estrategia_bloco}
---
PAUTA PARA ESTRUTURAR:
título: {pauta['titulo_bruto']}
categoria: {pauta['categoria']} | personas: {personas_str} | urgência: {pauta['urgencia']} | score: {score}
formato sugerido: {formato_sugerido} | publisher: {pauta['publisher']}
keywords: {pauta['palavras_chave']}
resumo: {pauta['notas'][:300]}

{catalogo_str}

REGRAS CTA:
• Comentário+Automação → burocrática/jurídica, urgência alta, score≥4. Copy: "💬 Comenta '[PALAVRA]' que te mando [PRODUTO/OFERTA do catálogo]". Use a página ativa mais relevante.
• Newsletter → analítico/finanças/jurídico profundo, P03/P04, score 3-4. Copy: "📩 Assina a newsletter — link na bio."
• Ambos → tem as duas camadas. Dois CTAs separados por \\n\\n.
• Orgânico → cívico, score≤2. Copy: "Você já passou por isso? Conta nos comentários 👇"

LÓGICA DE FORMATO POR DIA DE PUBLICAÇÃO (use para escolher format e voice_tone):
• Segunda: Reels informativo sério (jurídico, burocrático, urgente) — pessoas engajadas para aprender. Formato: Reels. Tom: Explicativo.
• Terça: Carrossel com informação adicional ou o que não coube no Reels. Formato: Carrossel. Tom: Explicativo.
• Quarta: Informação positiva, fácil ou de valor concreto (endereços, dicas, cases, cívico). Pode ser Reels curto e leve OU Carrossel. Tom: Observador ou Sentimental.
• Quinta: Antecipação — abertura de vagas, mudança de regras, alertas. Formato: Carrossel ou Reels. Tom: Observador.
• Sexta: Boa notícia. Formato: Reels ou Stories. Tom: Sentimental.
• Domingo: Inspiração, reflexão, começo de organização. Formato: Carrossel ou Reels. Tom: Sentimental.

ANÁLISE NEWSLETTER (campo "angulo_newsletter"):
• Se cta_tipo for "Newsletter" ou "Ambos": escreva UM parágrafo específico explicando QUAL ÂNGULO aprofundado este tema teria na newsletter — o que a newsletter entregaria além do post (dados adicionais, contexto histórico, casos práticos, etc).
• Se cta_tipo for "Orgânico" ou "Comentário+Automação": escreva "N/A — conteúdo não indicado para newsletter neste momento." Explique em 1 frase por quê (ex: tema muito operacional, sem profundidade analítica suficiente).

PILAR: Sistema(burocracia/dinheiro/processos) | Trajetória(carreira/estudos/vida na França) | Identidade(pertencimento/cultura/emoção) | Sociedade(política/direitos/notícias)
VOICE TONE: Observador(jornalístico, neutro) | Explicativo(professor, passo-a-passo) | Sentimental(emocional, pessoal) | Humor(leve, irônico)

Retorne APENAS JSON válido:
{{"title":"","hook":"","desc":"","kpi":"Salvamento alto|Compartilhamento alto|Comentário alto|Alcance","format":"Carrossel|Reels|Stories","formatDetail":"","fonte":"","urgency":"Alta|media|Baixa","cta_tipo":"Comentário+Automação|Newsletter|Ambos|Orgânico","cta_copy":"","angulo_newsletter":"","pilar":"","voice_tone":"","produto_sugerido":"","pagina_url_relevante":""}}"""

    msg = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```json\s*|^```|\s*```$', '', raw, flags=re.MULTILINE).strip()
    start = raw.find('{')
    if start > 0:
        raw = raw[start:]
    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(raw)

    result["fonteUrl"]       = pauta["fonte_url"]
    result["personas"]       = pauta["personas"]
    result["categoria"]      = pauta["categoria"]
    result["score"]          = score
    result["palavras_chave"] = pauta["palavras_chave"]
    # data_publicacao será calculada em main() com datas_usadas para evitar empilhamento

    pagina_url = result.get("pagina_url_relevante", "")
    pagina_id  = None
    if pagina_url and catalogo:
        for p in catalogo:
            if p["pagina_url"] and p["pagina_url"].rstrip("/") == pagina_url.rstrip("/"):
                pagina_id = p["pagina_id"]
                break
    result["pagina_id_relevante"] = pagina_id

    if result.get("kpi") not in _KPI_VALIDOS:
        result["kpi"] = "Salvamento alto"
    if result.get("cta_tipo") not in _CTA_VALIDOS:
        result["cta_tipo"] = "Orgânico"
    if result.get("format") not in _FORMATO_VALIDOS:
        result["format"] = formato_sugerido or "Carrossel"
    if result.get("pilar") not in _PILAR_VALIDOS:
        result["pilar"] = "Sistema"
    if result.get("voice_tone") not in _VOICETONE_VALIDOS:
        result["voice_tone"] = "Explicativo"

    return result


# ─── FUNÇÃO 3: Escrever pauta pronta no banco de saída ────────────────────────
def escrever_no_notion(p: dict):
    urgencia_raw = p.get("urgency", "")
    urgencia_val = _URGENCIA_VALIDOS.get(urgencia_raw.lower(), urgencia_raw) or "media"

    properties = {
        "Name": {
            "title": [{"text": {"content": p["title"]}}]
        },
        "Gancho":                {"rich_text": [{"text": {"content": p.get("hook") or ""}}]},
        "Descricao":             {"rich_text": [{"text": {"content": p.get("desc") or ""}}]},
        "Instrução de Produção": {"rich_text": [{"text": {"content": p.get("formatDetail") or ""}}]},
        "Formato":               {"select":    {"name": p["format"]}},
        "KPI":                   {"select":    {"name": p["kpi"]}},
        "Urgência":              {"select":    {"name": urgencia_val}},
        "Categoria":             {"select":    {"name": p["categoria"]}},
        "Persona":               {"multi_select": [{"name": n} for n in p["personas"]]},
        "Fonte":                 {"rich_text": [{"text": {"content": p.get("fonte") or ""}}]},
        "Fonte URL":             {"url": p["fonteUrl"] or None},
        "Score Conversão":       {"number": p["score"]},
        "Palavras-chave":        {"rich_text": [{"text": {"content": p.get("palavras_chave") or ""}}]},
        "CTA Tipo":          {"select":    {"name": p["cta_tipo"]}},
        "CTA Copy":          {"rich_text": [{"text": {"content": p.get("cta_copy") or ""}}]},
        "Ângulo Newsletter": {"rich_text": [{"text": {"content": p.get("angulo_newsletter") or ""}}]},
        "Produto Sugerido":  {"rich_text": [{"text": {"content": p.get("produto_sugerido") or ""}}]},
        "Landing Page URL":  {"url": p.get("pagina_url_relevante") or None},
        "Pilar":      {"select": {"name": p["pilar"]}},
        "Voice Tone": {"select": {"name": p["voice_tone"]}},
        "Status":     {"select": {"name": "Pronta"}},
    }

    if p.get("data_publicacao"):
        properties["Data Publicação"] = {"date": {"start": p["data_publicacao"]}}

    # "Vira Newsletter?" — derivado do cta_tipo para filtro rápido no Notion
    cta = p.get("cta_tipo", "")
    vira_nl = "Sim" if cta in ("Newsletter", "Ambos") else "Não"
    properties["Vira Newsletter?"] = {"select": {"name": vira_nl}}

    # "Dia Sugerido" — nome legível do dia da semana para kanban visual
    if p.get("data_publicacao"):
        try:
            from datetime import date as _date
            dia_num = _date.fromisoformat(p["data_publicacao"]).weekday()
            properties["Dia Sugerido"] = {
                "rich_text": [{"text": {"content": _NOMES_DIA.get(dia_num, "")}}]
            }
        except Exception:
            pass

    if p.get("pagina_id_relevante"):
        properties["🌐 Páginas Online (1)"] = {
            "relation": [{"id": p["pagina_id_relevante"]}]
        }

    notion.pages.create(
        parent={"database_id": DATABASE_SAIDA},
        properties=properties
    )


# ─── FUNÇÃO 4: Marcar rascunho como processado no banco de entrada ─────────────
def marcar_processado(page_id: str):
    notion.pages.update(
        page_id=page_id,
        properties={
            "Enviar para Claude": {"checkbox": False},
            "Status":             {"select": {"name": "Processado"}},
        }
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("\n🤖 CAMADA 2 — Por Dentro Pipeline")
    print("   Verificando propriedades dos bancos...")
    garantir_propriedades_entrada()

    print(f"  ℹ DATABASE_SAIDA ID: {DATABASE_SAIDA[:8]}...{DATABASE_SAIDA[-4:]} ({len(DATABASE_SAIDA)} chars)")
    try:
        notion.databases.retrieve(database_id=DATABASE_SAIDA)
        print("  ✓ Banco de saída: leitura OK.")
    except Exception as e:
        print(f"\n🚨 ERRO CRÍTICO: Banco de saída inacessível para leitura — {e}")
        print("   → Compartilhe 'Pautas Prontas por dentro' com a integração 'por-dentro-pipeline'.")
        print("   → No Notion: abra a base → ••• → Connections → adicione a integração.")
        print("   Encerrando sem chamar Claude.\n")
        return

    try:
        _test = notion.pages.create(
            parent={"database_id": DATABASE_SAIDA},
            properties={"Name": {"title": [{"text": {"content": "_PIPELINE_WRITE_TEST_"}}]}}
        )
        notion.pages.update(page_id=_test["id"], archived=True)
        print("  ✓ Banco de saída: escrita confirmada.")
    except Exception as e:
        print(f"\n🚨 ERRO CRÍTICO: Sem permissão de escrita no banco de saída — {e}")
        print("   → A integração 'por-dentro-pipeline' precisa de permissão INSERT.")
        print("   Encerrando sem chamar Claude.\n")
        return

    garantir_propriedades_saida()

    print("\n📦 Carregando catálogo de produtos e landing pages...")
    catalogo = carregar_catalogo_produtos()

    print("\n📅 Carregando calendário Instagram e planejamento estratégico...")
    datas_usadas = carregar_calendario_instagram()   # pré-popula datas já ocupadas
    estrategia   = carregar_estrategia()

    print("\n🔍 Buscando rascunhos marcados com 'Enviar para Claude'...")
    rascunhos = buscar_para_processar()

    if not rascunhos:
        print("   Nenhum rascunho marcado. Encerrando.")
        return

    print(f"   → {len(rascunhos)} pauta(s) para processar\n")

    processados = 0
    erros       = 0
    # datas_usadas já inicializado com calendário Instagram acima

    for pauta in rascunhos:
        print(f"🤖 [{pauta['categoria']} | score {pauta['score_conversao']}] "
              f"{pauta['titulo_bruto'][:70]}")
        try:
            estruturada = estruturar_com_claude(pauta, catalogo, estrategia)

            # Calcular data DEPOIS de Claude, com controle de slots entre pautas
            estruturada["data_publicacao"] = calcular_data_publicacao(
                estruturada,
                pauta.get("palavras_chave", ""),
                datas_usadas,
            )

            escrever_no_notion(estruturada)
            marcar_processado(pauta["notion_page_id"])

            # Log rico com dia da semana
            dp   = estruturada.get("data_publicacao", "")
            dia  = ""
            if dp:
                try:
                    from datetime import date as _d
                    dia = f" ({_NOMES_DIA[_d.fromisoformat(dp).weekday()]})"
                except Exception:
                    pass
            produto_log = f" → {estruturada['produto_sugerido']}" if estruturada.get("produto_sugerido") else ""
            nl_log      = " 📩 NL" if estruturada.get("cta_tipo") in ("Newsletter", "Ambos") else ""
            print(f"  ✓ [{estruturada['cta_tipo']}] [{estruturada['pilar']}] [{estruturada['format']}] "
                  f"{estruturada['title'][:50]}{produto_log}{nl_log}"
                  f"\n     📅 {dp}{dia}")
            processados += 1
        except Exception as e:
            print(f"  ✗ Erro: {e}")
            erros += 1

    print(f"\n{'═' * 62}")
    print(f"✅ Camada 2 concluída: {processados} pauta(s) pronta(s), {erros} erro(s)")
    print("   Banco 'Pautas Prontas' atualizado no Notion.")
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
