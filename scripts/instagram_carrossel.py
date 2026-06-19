"""
CAMADA 3 — Por Dentro Instagram Carrossel
Lê pautas prontas com format=Carrossel no Notion → gera wording de slides + prompts Higgsfield
→ cria página na base Instagram e aguarda aprovação de Ingryd

Não executa Higgsfield nem Canva — isso acontece na sessão Cowork após aprovação.
Roda todo domingo às 09h UTC (via GitHub Actions) ou manualmente.
"""

import os
import json
import re
from datetime import date
from notion_client import Client
import anthropic

# ─── Clientes ─────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

DATABASE_PAUTAS    = os.environ["NOTION_DATABASE_PAUTASPRONTAS_ID"].strip()
DATABASE_INSTAGRAM = os.environ["NOTION_DATABASE_INSTAGRAM_ID"].strip()

# ─── Contextos editoriais ─────────────────────────────────────────────────────
_script_dir       = os.path.dirname(os.path.abspath(__file__))
BRAND_CONTEXT     = open(os.path.join(_script_dir, "brand_context.txt"),    encoding="utf-8").read()
AUDIENCIA_CONTEXT = open(os.path.join(_script_dir, "audiencia_context.txt"), encoding="utf-8").read()

# ─── Template Carrossel na base Instagram ────────────────────────────────────
TEMPLATE_CARROSSEL_ID = "5f8d5edd-aba0-4876-9ee4-ced8c983cfde"


# ─── FUNÇÃO 1: Garantir campos na base Instagram ─────────────────────────────
def garantir_campos_instagram():
    """
    Cria Wording Slides, Prompts Imagem e Status Aprovação se ainda não existirem.
    Idempotente.
    """
    try:
        notion.databases.update(
            database_id=DATABASE_INSTAGRAM,
            properties={
                "Wording Slides":   {"rich_text": {}},
                "Prompts Imagem":   {"rich_text": {}},
                "Status Aprovação": {"select": {}},
            }
        )
        print("  ✓ Campos da base Instagram verificados.")
    except Exception as e:
        print(f"  ⚠ Campos Instagram: {e}")


# ─── FUNÇÃO 2: Buscar pautas prontas para carrossel ──────────────────────────
def buscar_pautas_carrossel() -> list[dict]:
    """
    Filtra pautas com:
    - format = "Carrossel"
    - Canva URL vazio (ainda não processada)
    - Data Publicação preenchida (obrigatório)
    """
    try:
        response = notion.databases.query(
            database_id=DATABASE_PAUTAS,
            filter={"property": "Formato", "select": {"equals": "Carrossel"}},
        )
    except Exception as e:
        print(f"  ✗ Erro ao consultar Pautas Prontas: {e}")
        return []

    pautas = []
    for page in response.get("results", []):
        props = page["properties"]

        def _title():
            t = props.get("Name", {}).get("title", [])
            return t[0]["text"]["content"] if t else ""

        def _rt(nome):
            rt = props.get(nome, {}).get("rich_text", [])
            return rt[0]["text"]["content"] if rt else ""

        def _select(nome):
            s = props.get(nome, {}).get("select")
            return s["name"] if s else ""

        def _date(nome):
            d = props.get(nome, {}).get("date")
            return d["start"] if d else None

        canva_url      = props.get("Canva URL", {}).get("url") or ""
        data_publicacao = _date("Data Publicação")

        # Filtros em Python: só processa se Canva URL vazio e data preenchida
        if canva_url:
            continue
        if not data_publicacao:
            print(f"  ↩ Pulando '{_title()}' — sem Data Publicação.")
            continue

        pautas.append({
            "notion_page_id":  page["id"],
            "titulo":          _title(),
            "desc":            _rt("Descricao"),
            "hook":            _rt("Gancho"),
            "cta_copy":        _rt("CTA Copy"),
            "cta_tipo":        _select("CTA Tipo"),
            "format":          _select("Formato"),
            "kpi":             _select("KPI"),
            "urgency":         _select("Urgência"),
            "pilar":           _select("Pilar"),
            "fonte":           _rt("Fonte"),
            "data_publicacao": data_publicacao,
        })

    print(f"  ✓ {len(pautas)} pauta(s) carrossel encontrada(s) para processar.")
    return pautas


# ─── FUNÇÃO 3: Gerar wording + prompts com Claude ────────────────────────────
def gerar_conteudo_carrossel(pauta: dict) -> dict:
    """
    Claude Haiku decide número de slides e gera:
    - wording de cada slide (título + subtitle)
    - prompt Higgsfield por slide
    Retorna dict com 'wording' e 'prompts' (strings prontas para salvar no Notion).
    """

    cta_instrucao = {
        "Comentário+Automação": (
            "Último slide: CTA pedindo para comentar uma palavra-chave. "
            f"Use o copy: \"{pauta['cta_copy']}\" (adapt se necessário)."
        ),
        "Newsletter": (
            "Último slide: CTA apontando para newsletter com link na bio. "
            f"Use o copy: \"{pauta['cta_copy']}\"."
        ),
        "Ambos": (
            "Penúltimo slide: CTA de comentário. Último slide: CTA de newsletter. "
            f"Copy base: \"{pauta['cta_copy']}\"."
        ),
        "Orgânico": (
            "Sem CTA explícito. Último slide: gancho para engajamento orgânico nos comentários. "
            f"Copy base: \"{pauta['cta_copy']}\"."
        ),
    }.get(pauta["cta_tipo"], f"CTA copy: \"{pauta['cta_copy']}\"")

    prompt = f"""{BRAND_CONTEXT}

{AUDIENCIA_CONTEXT}

---
PAUTA PARA CARROSSEL INSTAGRAM:
Título: {pauta['titulo']}
Pilar: {pauta['pilar']} | KPI alvo: {pauta['kpi']} | Urgência: {pauta['urgency']}
Hook: {pauta['hook']}
Descrição: {pauta['desc']}
Fonte: {pauta['fonte']}

REGRA DE SLIDES:
- Pautas comparativas (vs/APL/ALS): 5–7 slides
- Pautas de passo a passo: 4–8 slides
- Pautas de urgência/dado único: 3–4 slides
- Decida o número ideal com base no conteúdo acima.
- Slide 1 = capa (hook forte, desperta curiosidade)
- Slides intermediários = desenvolvimento (cada um com 1 insight acionável)
- {cta_instrucao}

REGRAS DE TOM:
- "você" direto, nunca distante
- Termos técnicos franceses em itálico quando necessário
- Sem romantismo, sem alarmismo, sem julgamentos
- Cada slide: identificação imediata + ação clara + descoberta útil
- Título: max 6 palavras. Subtitle: max 15 palavras.

PROMPTS DE IMAGEM (campo Prompts Imagem):
- 1 prompt por slide, em inglês
- Padrão: fotografia editorial realista, luz natural, ambiência francesa
- Sem texto visível, cores neutras/quentes (não disputam com texto branco sobreposto)
- Formato 4:5 (1080×1350px Instagram Post)
- Exemplos de referência:
  • "Open paper calendar on a wooden desk, pen and coffee, natural window light, flat lay photography, warm tones, editorial"
  • "Apartment keys on a white marble table near a window with blurred Paris street view, morning soft light, minimalist lifestyle photography"

Retorne APENAS JSON válido (sem markdown, sem texto extra):
{{
  "num_slides": <int>,
  "slides": [
    {{"numero": 1, "titulo": "...", "subtitle": "...", "prompt_imagem": "..."}},
    ...
  ]
}}"""

    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Extrair JSON se vier com markdown
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data  = json.loads(match.group(0)) if match else json.loads(raw)

    slides = data.get("slides", [])

    # Montar strings no formato esperado pelo Notion / sessão Cowork
    wording_lines = []
    prompt_lines  = []
    for s in slides:
        n = s["numero"]
        wording_lines.append(f"Slide {n} título: {s['titulo']}")
        wording_lines.append(f"Slide {n} subtitle: {s['subtitle']}")
        prompt_lines.append(f"Slide {n}: {s['prompt_imagem']}")

    return {
        "wording":  "\n".join(wording_lines),
        "prompts":  "\n".join(prompt_lines),
        "num_slides": len(slides),
    }


# ─── FUNÇÃO 4: Verificar se já existe página Instagram para esta pauta ────────
def ja_existe_pagina_instagram(pauta_id: str) -> bool:
    """
    Evita duplicatas: verifica se já há uma página na base Instagram
    vinculada à pauta (via relation 'Pautas Prontas por dentro').
    """
    response = notion.databases.query(
        database_id=DATABASE_INSTAGRAM,
        filter={
            "property": "Pautas Prontas por dentro",
            "relation": {"contains": pauta_id}
        }
    )
    return len(response.get("results", [])) > 0


# ─── FUNÇÃO 5: Criar página Instagram no Notion ──────────────────────────────
def criar_pagina_instagram(pauta: dict, conteudo: dict) -> str:
    """
    Cria nova página na base Instagram com wording + prompts + metadados.
    Retorna o ID da página criada.
    """
    # Mapear pilar → Pilars (multi_select na base Instagram)
    pilar_map = {
        "Sistema":     "Sistema",
        "Trajetória":  "Trajetória",
        "Identidade":  "Identidade",
        "Sociedade":   "Sociedade",
    }
    pilar_instagram = pilar_map.get(pauta["pilar"], pauta["pilar"])

    # Mapear KPI → META (multi_select)
    kpi_map = {
        "Salvamento alto":       "SAVE",
        "Compartilhamento alto": "SHARE",
        "Comentário alto":       "COMMENTS",
        "Alcance":               "REACH",
    }
    meta_val = kpi_map.get(pauta["kpi"], pauta["kpi"])

    # Legenda = desc + CTA
    legenda = pauta["desc"]
    if pauta["cta_copy"]:
        legenda += f"\n\n{pauta['cta_copy']}"

    properties = {
        "Nom": {
            "title": [{"text": {"content": pauta["titulo"]}}]
        },
        "Pautas Prontas por dentro": {
            "relation": [{"id": pauta["notion_page_id"]}]
        },
        "Format": {
            "multi_select": [{"name": "Carrousel"}]
        },
        "Stage": {
            "status": {"name": "Design"}
        },
        "Legenda": {
            "rich_text": [{"text": {"content": legenda[:2000]}}]
        },
        "Promessa do conteudo": {
            "rich_text": [{"text": {"content": pauta["hook"][:2000]}}]
        },
        "Pilars": {
            "multi_select": [{"name": pilar_instagram}]
        },
        "META": {
            "multi_select": [{"name": meta_val}]
        },
        "Wording Slides": {
            "rich_text": [{"text": {"content": conteudo["wording"][:2000]}}]
        },
        "Prompts Imagem": {
            "rich_text": [{"text": {"content": conteudo["prompts"][:2000]}}]
        },
        "Status Aprovação": {
            "select": {"name": "Aguardando"}
        },
    }

    # Data de publicação
    if pauta["data_publicacao"]:
        properties["Posting Date"] = {
            "date": {"start": pauta["data_publicacao"]}
        }

    page = notion.pages.create(
        parent={"database_id": DATABASE_INSTAGRAM},
        properties=properties,
    )
    return page["id"]


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("\n=== CAMADA 3 — Instagram Carrossel ===")
    print(f"Data: {date.today().isoformat()}\n")

    print("▶ Garantindo campos na base Instagram...")
    garantir_campos_instagram()

    print("\n▶ Buscando pautas prontas (Carrossel, sem Canva URL, com data)...")
    pautas = buscar_pautas_carrossel()

    if not pautas:
        print("  ℹ Nenhuma pauta nova para processar. Encerrando.")
        return

    processadas = 0
    erros = 0

    for i, pauta in enumerate(pautas, 1):
        titulo = pauta["titulo"] or f"(sem título — {pauta['notion_page_id'][:8]})"
        print(f"\n[{i}/{len(pautas)}] {titulo}")

        # Evitar duplicatas
        if ja_existe_pagina_instagram(pauta["notion_page_id"]):
            print("  ↩ Página Instagram já existe para esta pauta. Pulando.")
            continue

        try:
            print("  ⏳ Gerando wording + prompts com Claude...")
            conteudo = gerar_conteudo_carrossel(pauta)
            print(f"  ✓ {conteudo['num_slides']} slides gerados.")

            print("  ⏳ Criando página na base Instagram...")
            page_id = criar_pagina_instagram(pauta, conteudo)
            print(f"  ✓ Página criada: {page_id}")
            print(f"  ✓ Status Aprovação = Aguardando")

            processadas += 1

        except Exception as e:
            print(f"  ✗ Erro: {e}")
            erros += 1

    print(f"\n=== Resumo ===")
    print(f"  Processadas: {processadas}")
    print(f"  Erros:       {erros}")
    print(f"  Aguardando revisão no Notion: base 'INSTA TO POR DENTRO'")
    print("  ⏸ Execução encerrada — aguardando aprovação de Ingryd.\n")


if __name__ == "__main__":
    main()
