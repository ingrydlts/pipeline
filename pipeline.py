import os
import json
import re
from notion_client import Client
import anthropic

notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]
DATABASE_SAIDA   = os.environ["NOTION_DATABASE_SAIDA_ID"]
BRAND_CONTEXT    = open("scripts/brand_context.txt", encoding="utf-8").read()


def buscar_rascunhos():
    response = notion.databases.query(
        database_id=DATABASE_ENTRADA,
        filter={"property": "Status", "select": {"equals": "rascunho"}}
    )
    pautas = []
    for page in response["results"]:
        props = page["properties"]

        def titulo():
            t = props.get("Título", {}).get("title", [])
            return t[0]["text"]["content"] if t else ""
        def select(nome):
            s = props.get(nome, {}).get("select")
            return s["name"].lower() if s else ""
        def multiselect(nome):
            return [o["name"] for o in props.get(nome, {}).get("multi_select", [])]
        def url(nome):
            return props.get(nome, {}).get("url") or ""
        def rich_text(nome):
            rt = props.get(nome, {}).get("rich_text", [])
            return rt[0]["text"]["content"] if rt else ""

        pautas.append({
            "notion_page_id": page["id"],
            "titulo_bruto":   titulo(),
            "categoria":      select("Categoria"),
            "personas":       multiselect("Persona"),
            "urgencia":       select("Urgência"),
            "fonte_url":      url("Fonte"),
            "notas":          rich_text("Notas"),
        })
    return pautas


def estruturar_com_claude(pauta):
    personas_str = " e ".join(pauta["personas"]) if pauta["personas"] else "P02"

    prompt = f"""Você é o assistente editorial sênior do projeto Por Dentro.

MANUAL DE MARCA:
{BRAND_CONTEXT}

PAUTA BRUTA:
- Título: {pauta['titulo_bruto']}
- Categoria: {pauta['categoria']}
- Personas: {personas_str}
- Urgência: {pauta['urgencia']}
- Fonte: {pauta['fonte_url']}
- Notas: {pauta['notas']}

Retorne APENAS um JSON válido (sem markdown):
{{
  "title": "título editorial finalizado",
  "hook": "gancho magnético de 1 frase",
  "desc": "descrição em 2 frases, tom conversa entre amigas",
  "kpi": "Salvamento alto",
  "format": "Carrossel",
  "formatDetail": "instrução de produção em 1 linha",
  "fonte": "nome curto da fonte",
  "urgency": "alta"
}}"""

    msg = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```json\s*|^```|\s*```$', '', raw)
    result = json.loads(raw)
    result["fonteUrl"]  = pauta["fonte_url"]
    result["personas"]  = pauta["personas"]
    result["categoria"] = pauta["categoria"]
    return result


def escrever_no_notion(p):
    notion.pages.create(
        parent={"database_id": DATABASE_SAIDA},
        properties={
            "Título":                {"title":     [{"text": {"content": p["title"]}}]},
            "Gancho":                {"rich_text": [{"text": {"content": p["hook"]}}]},
            "Instrução de Produção": {"rich_text": [{"text": {"content": p["formatDetail"]}}]},
            "Formato":   {"select":       {"name": p["format"]}},
            "KPI":       {"select":       {"name": p["kpi"]}},
            "Urgência":  {"select":       {"name": p["urgency"]}},
            "Categoria": {"select":       {"name": p["categoria"]}},
            "Persona":   {"multi_select": [{"name": n} for n in p["personas"]]},
            "Fonte":     {"rich_text":    [{"text": {"content": p["fonte"]}}]},
            "Fonte URL": {"url": p["fonteUrl"]},
            "Status":    {"select": {"name": "pronta"}},
        }
    )


def marcar_processado(page_id):
    notion.pages.update(
        page_id=page_id,
        properties={"Status": {"select": {"name": "processado"}}}
    )


def main():
    print("\n🔍 Buscando rascunhos...")
    rascunhos = buscar_rascunhos()

    if not rascunhos:
        print("Nenhum rascunho encontrado.")
        return

    print(f"  → {len(rascunhos)} pauta(s) encontrada(s)\n")

    for pauta in rascunhos:
        print(f"🤖 Estruturando: {pauta['titulo_bruto']}")
        try:
            estruturada = estruturar_com_claude(pauta)
            escrever_no_notion(estruturada)
            marcar_processado(pauta["notion_page_id"])
            print(f"  ✓ Pauta pronta no Notion: {estruturada['title']}")
        except Exception as e:
            print(f"  ✗ Erro: {e}")

    print(f"\n🎉 Concluído.")

if __name__ == "__main__":
    main()
