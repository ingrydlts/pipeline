import os
import json
import re
import datetime
from notion_client import Client
import anthropic

# ── Clientes ──────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DATABASE_ID   = os.environ["NOTION_DATABASE_ID"]
HTML_PATH     = "index.html"
BRAND_CONTEXT = open("scripts/brand_context.txt").read()

# ── 1. Buscar pautas com status = "rascunho" ──────────────────────────────────
def buscar_rascunhos():
    response = notion.databases.query(
        database_id=DATABASE_ID,
        filter={
            "property": "Status",
            "select": {"equals": "rascunho"}
        }
    )
    pautas = []
    for page in response["results"]:
        props = page["properties"]
        def texto(prop): 
            return props[prop]["title"][0]["text"]["content"] if props[prop]["title"] else ""
        def select(prop):
            s = props[prop].get("select")
            return s["name"] if s else ""
        def multiselect(prop):
            return [o["name"] for o in props[prop].get("multi_select", [])]
        def url(prop):
            return props[prop].get("url") or ""
        def rich_text(prop):
            rt = props[prop].get("rich_text", [])
            return rt[0]["text"]["content"] if rt else ""

        pautas.append({
            "notion_page_id": page["id"],
            "titulo_bruto":   texto("Título"),
            "categoria":      select("Categoria"),
            "personas":       multiselect("Persona"),
            "urgencia":       select("Urgência"),
            "fonte_url":      url("Fonte"),
            "notas":          rich_text("Notas"),
        })
    return pautas

# ── 2. Estruturar cada pauta com Claude ───────────────────────────────────────
def estruturar_com_claude(pauta):
    personas_str = " e ".join(pauta["personas"]) if pauta["personas"] else "P02"
    
    prompt = f"""Você é o assistente editorial sênior do projeto Por Dentro — canal para brasileiras imigrantes na França.

MANUAL DE MARCA:
{BRAND_CONTEXT}

PAUTA BRUTA:
- Título bruto: {pauta['titulo_bruto']}
- Categoria: {pauta['categoria']}
- Personas-alvo: {personas_str}
- Urgência: {pauta['urgencia']}
- Fonte: {pauta['fonte_url']}
- Notas: {pauta['notas']}

Estruture essa pauta como um objeto JSON com os seguintes campos:
{{
  "id": "slug único em snake_case baseado no título, máximo 40 chars",
  "title": "título editorial finalizado, direto, sem clickbait vazio",
  "hook": "gancho magnético de 1 frase — tensão cognitiva imediata",
  "desc": "descrição da pauta em 2–3 frases, tom de conversa entre amigas",
  "kpi": "Salvamento alto | Compartilhamento alto | Engajamento | Alcance",
  "format": "Carrossel | Vídeo YouTube | Reel | Stories | Newsletter",
  "formatDetail": "instrução de produção em 1 linha (ex: 5 slides, CTA salvamento)",
  "fonte": "nome curto da fonte (ex: Légifrance, CAF, APEC)",
  "urgency": "alta | média | baixa"
}}

Retorne APENAS o JSON, sem markdown, sem explicação."""

    message = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw = message.content[0].text.strip()
    # Limpar markdown se o modelo retornar com ```json
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    
    estruturado = json.loads(raw)
    # Preservar campos do Notion que o Claude não gera
    estruturado["fonteUrl"]    = pauta["fonte_url"]
    estruturado["personas"]    = pauta["personas"]
    estruturado["categoria"]   = pauta["categoria"]
    estruturado["notion_page_id"] = pauta["notion_page_id"]
    return estruturado

# ── 3. Injetar no HTML sem quebrar o código ───────────────────────────────────
def injetar_no_html(pautas_estruturadas):
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    for p in pautas_estruturadas:
        cat = p.get("categoria", "burocratica")
        
        # Formatar o novo item JS
        novo_item = f"""    {{
      id: '{p['id']}',
      title: {json.dumps(p['title'], ensure_ascii=False)},
      hook: {json.dumps(p['hook'], ensure_ascii=False)},
      desc: {json.dumps(p['desc'], ensure_ascii=False)},
      kpi: '{p['kpi']}',
      format: '{p['format']}',
      formatDetail: {json.dumps(p['formatDetail'], ensure_ascii=False)},
      fonte: {json.dumps(p['fonte'], ensure_ascii=False)},
      fonteUrl: '{p['fonteUrl']}',
      personas: {json.dumps(p['personas'])},
      urgency: '{p['urgency']}'
    }},"""

        # Localiza o array da categoria correta no HTML
        # Padrão esperado: NEWS.burocratica = [ ... ]
        pattern = rf'(NEWS\.{cat}\s*=\s*\[)'
        match = re.search(pattern, html)
        
        if match:
            insert_pos = match.end()
            html = html[:insert_pos] + "\n" + novo_item + html[insert_pos:]
            print(f"  ✓ Injetado em NEWS.{cat}: {p['title']}")
        else:
            print(f"  ⚠ Categoria '{cat}' não encontrada no HTML — pulando.")

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

# ── 4. Marcar pautas como "processado" no Notion ─────────────────────────────
def marcar_processado(page_id):
    notion.pages.update(
        page_id=page_id,
        properties={
            "Status": {"select": {"name": "processado"}}
        }
    )

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n🔍 Buscando rascunhos no Notion...")
    rascunhos = buscar_rascunhos()
    
    if not rascunhos:
        print("Nenhum rascunho encontrado. Pipeline encerrado.")
        return
    
    print(f"  → {len(rascunhos)} pauta(s) encontrada(s)\n")
    
    pautas_prontas = []
    for pauta in rascunhos:
        print(f"🤖 Estruturando: {pauta['titulo_bruto']}")
        try:
            estruturada = estruturar_com_claude(pauta)
            pautas_prontas.append(estruturada)
        except Exception as e:
            print(f"  ✗ Erro ao processar '{pauta['titulo_bruto']}': {e}")
            continue

    if not pautas_prontas:
        print("Nenhuma pauta estruturada com sucesso.")
        return

    print(f"\n💉 Injetando {len(pautas_prontas)} pauta(s) no HTML...")
    injetar_no_html(pautas_prontas)

    print(f"\n✅ Marcando como 'processado' no Notion...")
    for p in pautas_prontas:
        marcar_processado(p["notion_page_id"])
        print(f"  ✓ {p['title']}")

    print(f"\n🎉 Pipeline concluído: {len(pautas_prontas)} pauta(s) publicada(s).")

if __name__ == "__main__":
    main()
