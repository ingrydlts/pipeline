"""
CAMADA 2 — Por Dentro Content Pipeline
Lê rascunhos marcados no Notion → estrutura com Claude → devolve ao Notion como pautas prontas

Roda toda segunda-feira às 08h (via GitHub Actions)
Só processa rascunhos com o checkbox "Enviar para Claude" marcado
"""

import os
import json
import re
from notion_client import Client
import anthropic

# ─── Clientes ────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]
DATABASE_SAIDA   = os.environ["NOTION_DATABASE_PAUTASPRONTAS_ID"]
BRAND_CONTEXT    = open("scripts/brand_context.txt", encoding="utf-8").read()


# ─── Garantir propriedades necessárias no database de entrada ─────────────────
def garantir_propriedades_pipeline():
    """Cria 'Enviar para Claude' (checkbox) se ainda não existir. Idempotente."""
    try:
        notion.databases.update(
            database_id=DATABASE_ENTRADA,
            properties={"Enviar para Claude": {"checkbox": {}}}
        )
        print("  ✓ Propriedade 'Enviar para Claude' verificada.")
    except Exception as e:
        print(f"  ⚠ Não foi possível verificar propriedades: {e}")


# ─── FUNÇÃO 1: Buscar rascunhos com checkbox "Enviar para Claude" marcado ─────
def buscar_para_processar():
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
            "categoria":      select("Categoria") or multiselect("Categoria")[0].lower() if multiselect("Categoria") else "burocratica",
            "personas":       multiselect("Persona"),
            "urgencia":       select("Urgência"),
            "fonte_url":      url("Fonte"),
            "notas":          rich_text("Notas"),
        })

    return pautas


# ─── FUNÇÃO 2: Estruturar com Claude ─────────────────────────────────────────
def estruturar_com_claude(pauta):
    personas_str = " e ".join(pauta["personas"]) if pauta["personas"] else "P02"

    prompt = f"""Você é o assistente editorial sênior do projeto Por Dentro.

MANUAL DE MARCA:
{BRAND_CONTEXT}

PAUTA BRUTA PARA ESTRUTURAR:
- Título: {pauta['titulo_bruto']}
- Categoria: {pauta['categoria']}
- Personas-alvo: {personas_str}
- Urgência: {pauta['urgencia']}
- Fonte: {pauta['fonte_url']}
- Notas adicionais: {pauta['notas']}

Retorne APENAS um JSON válido (sem markdown, sem texto antes ou depois):
{{
  "title": "título editorial finalizado, direto, sem clickbait vazio",
  "hook": "gancho magnético de 1 frase — tensão cognitiva que para o scroll",
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


# ─── FUNÇÃO 3: Escrever pauta pronta no Notion (database de saída) ────────────
def escrever_no_notion(p):
    notion.pages.create(
        parent={"database_id": DATABASE_SAIDA},
        properties={
            "Título": {
                "title": [{"text": {"content": p["title"]}}]
            },
            "Gancho": {
                "rich_text": [{"text": {"content": p["hook"]}}]
            },
            "Instrução de Produção": {
                "rich_text": [{"text": {"content": p["formatDetail"]}}]
            },
            "Formato":   {"select":       {"name": p["format"]}},
            "KPI":       {"select":       {"name": p["kpi"]}},
            "Urgência":  {"select":       {"name": p["urgency"]}},
            "Categoria": {"select":       {"name": p["categoria"]}},
            "Persona":   {"multi_select": [{"name": n} for n in p["personas"]]},
            "Fonte":     {"rich_text":    [{"text": {"content": p["fonte"]}}]},
            "Fonte URL": {"url": p["fonteUrl"] or None},
            "Status":    {"select":       {"name": "Publicado"}},
        }
    )


# ─── FUNÇÃO 4: Desmarcar checkbox e marcar como processado no Notion ──────────
def marcar_processado(page_id):
    notion.pages.update(
        page_id=page_id,
        properties={
            "Enviar para Claude": {"checkbox": False},
            "Status":             {"select": {"name": "Processado"}},
        }
    )


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("\n🔍 CAMADA 2 — Buscando rascunhos marcados para processar...")
    garantir_propriedades_pipeline()
    rascunhos = buscar_para_processar()

    if not rascunhos:
        print("Nenhum rascunho com 'Enviar para Claude' marcado. Encerrando.")
        return

    print(f"  → {len(rascunhos)} pauta(s) marcada(s)\n")

    for pauta in rascunhos:
        print(f"🤖 Estruturando: {pauta['titulo_bruto']}")
        try:
            estruturada = estruturar_com_claude(pauta)
            escrever_no_notion(estruturada)
            marcar_processado(pauta["notion_page_id"])
            print(f"  ✓ Pauta pronta: {estruturada['title']}")
        except Exception as e:
            print(f"  ✗ Erro: {e}")

    print(f"\n🎉 Camada 2 concluída.")


if __name__ == "__main__":
    main()
