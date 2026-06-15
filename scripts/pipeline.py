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
from notion_client import Client
import anthropic

# ─── Clientes ─────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DATABASE_ENTRADA     = os.environ["NOTION_DATABASE_ID"].strip()
DATABASE_SAIDA       = os.environ["NOTION_DATABASE_PAUTASPRONTAS_ID"].strip()
PAGINAS_ONLINE_DB_ID = "f16d6022-54ce-8325-a778-87c5e078b5b5"   # Páginas Online (landing pages)

# ─── Contextos editoriais (carregados de arquivo — não negociável para qualidade autônoma) ──
_script_dir       = os.path.dirname(os.path.abspath(__file__))
BRAND_CONTEXT     = open(os.path.join(_script_dir, "brand_context.txt"),    encoding="utf-8").read()
AUDIENCIA_CONTEXT = open(os.path.join(_script_dir, "audiencia_context.txt"), encoding="utf-8").read()

# ─── Valores válidos para seletores Notion ────────────────────────────────────
_KPI_VALIDOS      = ["Salvamento alto", "Compartilhamento alto", "Comentário alto", "Alcance"]
_CTA_VALIDOS      = ["Comentário+Automação", "Newsletter", "Ambos", "Orgânico"]
_FORMATO_VALIDOS  = ["Carrossel", "Reels", "Stories"]
_PILAR_VALIDOS    = ["Sistema", "Trajetória", "Identidade", "Sociedade"]
_VOICETONE_VALIDOS = ["Observador", "Explicativo", "Sentimental", "Humor"]
_URGENCIA_VALIDOS = {"alta": "Alta", "media": "media", "baixa": "Baixa"}


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

        # Buscar cada Produto Digital vinculado
        for prod_id in _relation_ids("Produtos Digitais"):
            try:
                prod_page  = notion.pages.retrieve(page_id=prod_id)
                prod_props = prod_page["properties"]

                nome_list = prod_props.get("Nome do Produto", {}).get("title", [])
                nome      = nome_list[0]["text"]["content"] if nome_list else ""

                status_s  = prod_props.get("Status", {}).get("select")
                status    = status_s["name"] if status_s else ""

                obj_rt    = prod_props.get("Objetivo de Negócio", {}).get("rich_text", [])
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
                # ── Propriedades base (existentes ou recriadas) ──────────────
                "Gancho":                {"rich_text": {}},
                "Instrução de Produção": {"rich_text": {}},
                "Fonte":                 {"rich_text": {}},
                "Fonte URL":             {"url": {}},
                "Score Conversão":       {"number": {}},
                "Palavras-chave":        {"rich_text": {}},
                "Descricao":             {"rich_text": {}},
                # ── CTA & newsletter ─────────────────────────────────────────
                "CTA Tipo":              {"select": {}},
                "CTA Copy":              {"rich_text": {}},
                "Ângulo Newsletter":     {"rich_text": {}},
                # ── Produto e landing page ────────────────────────────────────
                "Produto Sugerido":      {"rich_text": {}},
                "Landing Page URL":      {"url": {}},
                # Nota: "🌐 Páginas Online (1)" já existe como relation nativa desta base.
                # Não recriamos — apenas usamos na escrita de páginas.
                # ── Pilar editorial e tom de voz (espelham INSTA TO POR DENTRO) ──
                "Pilar":      {"select": {}},
                "Voice Tone": {"select": {}},
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
def estruturar_com_claude(pauta: dict, catalogo: list[dict]) -> dict:
    personas_str     = " e ".join(pauta["personas"]) if pauta["personas"] else "P02"
    formato_sugerido = pauta["formato_sugerido"] or "Carrossel"
    score            = pauta["score_conversao"]

    # ── Catálogo compacto: uma linha por entrada ─────────────────────────────
    if catalogo:
        entradas = []
        for p in catalogo:
            prods = " / ".join(pr["nome"] for pr in p["produtos"]) or "sem produto"
            entradas.append(f'- {p["oferta"] or p["objetivo"]} | produto: {prods} | url: {p["pagina_url"]}')
        catalogo_str = "PÁGINAS ATIVAS:\n" + "\n".join(entradas)
    else:
        catalogo_str = "PÁGINAS ATIVAS: nenhuma. Use CTA genérico."

    prompt = f"""{BRAND_CONTEXT}

{AUDIENCIA_CONTEXT}

---
PAUTA PARA ESTRUTURAR:
título: {pauta['titulo_bruto']}
categoria: {pauta['categoria']} | personas: {personas_str} | urgência: {pauta['urgencia']} | score: {score}
formato: {formato_sugerido} | publisher: {pauta['publisher']}
keywords: {pauta['palavras_chave']}
resumo: {pauta['notas'][:300]}

{catalogo_str}

REGRAS CTA:
• Comentário+Automação → burocrática/jurídica, urgência alta, score≥4. Copy: "💬 Comenta '[PALAVRA]' que te mando [PRODUTO/OFERTA do catálogo]". Use a página ativa mais relevante.
• Newsletter → analítico/finanças/jurídico profundo, P03/P04, score 3-4. Copy: "📩 Assina a newsletter — link na bio."
• Ambos → tem as duas camadas. Dois CTAs separados por \\n\\n.
• Orgânico → cívico, score≤2. Copy: "Você já passou por isso? Conta nos comentários 👇"

PILAR: Sistema(burocracia/dinheiro) | Trajetória(carreira/estudos) | Identidade(pertencimento/cultura) | Sociedade(política/direitos)
VOICE TONE: Observador | Explicativo | Sentimental | Humor

Retorne APENAS JSON válido:
{{"title":"","hook":"","desc":"","kpi":"Salvamento alto|Compartilhamento alto|Comentário alto|Alcance","format":"{formato_sugerido}","formatDetail":"","fonte":"","urgency":"Alta|media|Baixa","cta_tipo":"Comentário+Automação|Newsletter|Ambos|Orgânico","cta_copy":"","angulo_newsletter":"","pilar":"","voice_tone":"","produto_sugerido":"","pagina_url_relevante":""}}"""

    msg = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```json\s*|^```|\s*```$', '', raw, flags=re.MULTILINE).strip()
    # Localiza o início do objeto JSON (ignora texto de prefácio)
    start = raw.find('{')
    if start > 0:
        raw = raw[start:]
    # raw_decode para no fim do primeiro objeto JSON — ignora texto extra após o JSON
    decoder = json.JSONDecoder()
    result, _ = decoder.raw_decode(raw)

    # ── Enriquecer com metadados da pauta ────────────────────────────────────
    result["fonteUrl"]      = pauta["fonte_url"]
    result["personas"]      = pauta["personas"]
    result["categoria"]     = pauta["categoria"]
    result["score"]         = score
    result["palavras_chave"] = pauta["palavras_chave"]

    # ── Resolver pagina_id a partir da URL retornada pelo Claude ─────────────
    pagina_url = result.get("pagina_url_relevante", "")
    pagina_id  = None
    if pagina_url and catalogo:
        for p in catalogo:
            if p["pagina_url"] and p["pagina_url"].rstrip("/") == pagina_url.rstrip("/"):
                pagina_id = p["pagina_id"]
                break
    result["pagina_id_relevante"] = pagina_id

    # ── Validar selects para não quebrar o Notion ─────────────────────────────
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

    # Título da base é "Name" (não "Título") nesta base de dados
    properties = {
        "Name": {
            "title": [{"text": {"content": p["title"]}}]
        },
        "Gancho": {
            "rich_text": [{"text": {"content": p.get("hook", "")}}]
        },
        "Descricao": {
            "rich_text": [{"text": {"content": p.get("desc", "")}}]
        },
        "Instrução de Produção": {
            "rich_text": [{"text": {"content": p.get("formatDetail", "")}}]
        },
        "Formato":           {"select":       {"name": p["format"]}},
        "KPI":               {"select":       {"name": p["kpi"]}},
        "Urgência":          {"select":       {"name": urgencia_val}},
        "Categoria":         {"select":       {"name": p["categoria"]}},
        "Persona":           {"multi_select": [{"name": n} for n in p["personas"]]},
        "Fonte":             {"rich_text":    [{"text": {"content": p.get("fonte", "")}}]},
        "Fonte URL":         {"url": p["fonteUrl"] or None},
        "Score Conversão":   {"number": p["score"]},
        "Palavras-chave":    {"rich_text":    [{"text": {"content": p.get("palavras_chave", "")}}]},
        # CTA
        "CTA Tipo":          {"select":       {"name": p["cta_tipo"]}},
        "CTA Copy":          {"rich_text":    [{"text": {"content": p.get("cta_copy", "")}}]},
        "Ângulo Newsletter": {"rich_text":    [{"text": {"content": p.get("angulo_newsletter", "")}}]},
        # Produto / landing page
        "Produto Sugerido":  {"rich_text":    [{"text": {"content": p.get("produto_sugerido", "")}}]},
        "Landing Page URL":  {"url": p.get("pagina_url_relevante") or None},
        # Pilar e voice tone
        "Pilar":             {"select":       {"name": p["pilar"]}},
        "Voice Tone":        {"select":       {"name": p["voice_tone"]}},
        # Status
        "Status":            {"select":       {"name": "Pronta"}},
    }

    # Relation a Páginas Online — usa a propriedade já existente "🌐 Páginas Online (1)"
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

    # ── Verificação antecipada do banco de saída (testa READ + WRITE) ────────
    # Testa acesso ANTES de chamar Claude, para não desperdiçar tokens.
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

    # Testa ESCRITA criando uma página temporária e arquivando imediatamente
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
        print("   → Verifique em notion.so/profile/integrations → Capabilities → Insert content.")
        print("   → Ou reconecte a integração no Notion: ••• → Connections → por-dentro-pipeline.")
        print("   Encerrando sem chamar Claude.\n")
        return

    garantir_propriedades_saida()

    print("\n📦 Carregando catálogo de produtos e landing pages...")
    catalogo = carregar_catalogo_produtos()

    print("\n🔍 Buscando rascunhos marcados com 'Enviar para Claude'...")
    rascunhos = buscar_para_processar()

    if not rascunhos:
        print("   Nenhum rascunho marcado. Encerrando.")
        return

    print(f"   → {len(rascunhos)} pauta(s) para processar\n")

    processados = 0
    erros       = 0

    for pauta in rascunhos:
        print(f"🤖 [{pauta['categoria']} | score {pauta['score_conversao']}] "
              f"{pauta['titulo_bruto'][:70]}")
        try:
            estruturada = estruturar_com_claude(pauta, catalogo)
            escrever_no_notion(estruturada)
            marcar_processado(pauta["notion_page_id"])

            produto_log = f" → produto: {estruturada['produto_sugerido']}" if estruturada.get("produto_sugerido") else ""
            print(f"  ✓ [{estruturada['cta_tipo']}] [{estruturada['pilar']}] "
                  f"{estruturada['title'][:55]}{produto_log}")
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
