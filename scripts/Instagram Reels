"""
CAMADA 3B — Por Dentro Instagram Reels
Lê pautas prontas com format=Reels no Notion → gera 3 variações de roteiro (curto/médio/longo)
→ cria página na base Instagram e aguarda aprovação de Ingryd

Não executa gravação nem edição — isso acontece na sessão Cowork após aprovação.
Roda todo domingo às 09h UTC (via GitHub Actions, junto com instagram_carrossel.py).
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


# ─── FUNÇÃO 1: Garantir campos na base Instagram ─────────────────────────────
def garantir_campos_instagram():
    """
    Cria Roteiro Reel e Status Aprovação se ainda não existirem.
    Idempotente.
    """
    try:
        notion.databases.update(
            database_id=DATABASE_INSTAGRAM,
            properties={
                "Roteiro Reel":     {"rich_text": {}},
                "Status Aprovação": {"select": {}},
            }
        )
        print("  ✓ Campos da base Instagram verificados.")
    except Exception as e:
        print(f"  ⚠ Campos Instagram: {e}")


# ─── FUNÇÃO 2: Buscar pautas prontas para reels ───────────────────────────────
def buscar_pautas_reels() -> list[dict]:
    """
    Filtra pautas com:
    - format = "Reels"
    - Canva URL vazio (ainda não processada)
    - Data Publicação preenchida (obrigatório)
    """
    try:
        response = notion.databases.query(
            database_id=DATABASE_PAUTAS,
            filter={"property": "format", "select": {"equals": "Reels"}},
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

        canva_url       = props.get("Canva URL", {}).get("url") or ""
        data_publicacao = _date("Data Publicação")

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
            "format":          _select("format"),
            "kpi":             _select("KPI"),
            "urgency":         _select("Urgência"),
            "pilar":           _select("Pilar"),
            "fonte":           _rt("Fonte"),
            "data_publicacao": data_publicacao,
        })

    print(f"  ✓ {len(pautas)} pauta(s) reels encontrada(s) para processar.")
    return pautas


# ─── FUNÇÃO 3: Gerar roteiros (3 variações) com Claude ───────────────────────
def gerar_roteiros_reel(pauta: dict) -> dict:
    """
    Claude Haiku gera 3 variações de roteiro (curto/médio/longo) seguindo
    a estrutura tc-social-reel-scripter: Gancho → Contexto → Info Útil → CTA.

    Retorna dict com 'roteiro' (string pronta para salvar no Notion).
    """

    cta_instrucao = {
        "Comentário+Automação": (
            "CTA: peça para comentar uma PALAVRA-CHAVE específica nos comentários. "
            f"A pessoa receberá DM automático. Copy base: \"{pauta['cta_copy']}\". "
            "Versão curta: só 'salva esse vídeo'. Médio/longo: palavra-chave nos comentários."
        ),
        "Newsletter": (
            "CTA: direcione para newsletter com link na bio. "
            f"Copy base: \"{pauta['cta_copy']}\". Versão curta: 'link na bio'. Médio/longo: explica o que recebe."
        ),
        "Ambos": (
            "CTA duplo: palavra-chave nos comentários + link na bio pra newsletter. "
            f"Copy base: \"{pauta['cta_copy']}\"."
        ),
        "Orgânico": (
            "CTA orgânico: 'salva e manda pra quem precisa' ou pergunta que gera comentário natural. "
            f"Copy base: \"{pauta['cta_copy']}\". Sem palavra-chave. Sem forçar follow."
        ),
    }.get(pauta["cta_tipo"], f"CTA copy base: \"{pauta['cta_copy']}\"")

    gancho_por_pilar = {
        "Sistema":    "dado inesperado, mudança que já entrou em vigor, prazo que a maioria não sabe",
        "Trajetória": "a pergunta que a pessoa tem mas nunca soube como perguntar",
        "Identidade": "a situação que todo mundo reconhece mas ninguém nomeou",
        "Sociedade":  "o fato que muda o cenário / a decisão que foi tomada",
    }.get(pauta["pilar"], "dado ou situação que para o scroll imediatamente")

    prompt = f"""{BRAND_CONTEXT}

{AUDIENCIA_CONTEXT}

---
PAUTA PARA REEL INSTAGRAM:
Título: {pauta['titulo']}
Pilar: {pauta['pilar']} | KPI alvo: {pauta['kpi']} | Urgência: {pauta['urgency']}
Hook base da pauta: {pauta['hook']}
Descrição: {pauta['desc']}
Fonte: {pauta['fonte']}

SOBRE A INGRYD E O CANAL POR DENTRO:
- Brasileira morando na França, cria conteúdo sobre o sistema francês para brasileiras imigrantes
- Voz: amiga que já passou por isso — direta, calorosa, levemente irônica quando o sistema é absurdo
- Formato: talking head (câmera fixa, fala direta para a câmera)
- NUNCA começa com: "Oi gente", "Ei galera", "Então", "Hoje eu vou falar sobre", "Você sabia que", "Nesse vídeo"
- NUNCA começa com "Eu" como primeira palavra
- Sem romantismo, sem alarmismo, sem postura de guru ou especialista
- Termos técnicos franceses: em itálico no cue (*titre de séjour*, *CAF*, *récépissé*, *ANEF*)
- Específico, não vago: "3 documentos a menos" — não "menos burocracia"
- Palavras banidas: aproveite, incrível, maravilhoso, surpreendente, exclusivo, dica de ouro, segredo, jornada, empoderamento

GANCHO IDEAL PARA O PILAR {pauta['pilar'].upper()}:
→ {gancho_por_pilar}

ESTRUTURA DO REEL (Gancho → Contexto → Info Útil → CTA):
1. GANCHO (0–3 seg): para o scroll. Uma linha. Direto ao ponto.
2. CONTEXTO RÁPIDO (3–8 seg): situa quem está assistindo. "isso é pra você se..."
3. INFORMAÇÃO ÚTIL (8–45 seg, escala com o formato): 1 ideia por cue, cada cue = 1 corte
4. CTA (últimos 3–5 seg): {cta_instrucao}

DURAÇÕES ALVO:
- Curto: ~15–20 segundos (~80–100 palavras faladas), 3–4 cues
- Médio: ~30–45 segundos (~180–220 palavras faladas), 4–5 cues
- Longo: ~45–60 segundos (~270–320 palavras faladas), 5–6 cues

REGRAS DO TEXTO NA TELA:
- Reforça a fala — não repete palavra por palavra
- Máximo 6 palavras por overlay
- Minúsculas para tom conversacional; MAIÚSCULAS só pra dado de impacto (300€, 50%, 1M)
- Itálico nos termos franceses (*titre de séjour*)

Retorne APENAS JSON válido (sem markdown, sem texto extra):
{{
  "curto": {{
    "duracao": "~XX segundos",
    "angulo": "qual recorte específico do tema",
    "audiencia": "Pré-chegada | Recém-chegada | Adaptada | Todas",
    "gancho_fala": "frase exata de abertura",
    "gancho_tela": "texto overlay do gancho (max 6 palavras)",
    "cues": [
      {{
        "rotulo": "nome da seção",
        "cue": "o pensamento a comunicar — como a Ingryd falaria naturalmente",
        "tela": "texto overlay (max 6 palavras)",
        "imagem": "o que filmar ou mostrar"
      }}
    ],
    "cta_fala": "CTA como fala natural",
    "cta_tela": "texto overlay do CTA",
    "palavra_chave": "PALAVRA ou null"
  }},
  "medio": {{
    "duracao": "~XX segundos",
    "angulo": "qual recorte específico do tema",
    "audiencia": "Pré-chegada | Recém-chegada | Adaptada | Todas",
    "gancho_fala": "frase exata de abertura",
    "gancho_tela": "texto overlay do gancho (max 6 palavras)",
    "cues": [
      {{
        "rotulo": "nome da seção",
        "cue": "o pensamento a comunicar",
        "tela": "texto overlay (max 6 palavras)",
        "imagem": "o que filmar ou mostrar"
      }}
    ],
    "cta_fala": "CTA como fala natural",
    "cta_tela": "texto overlay do CTA",
    "palavra_chave": "PALAVRA ou null"
  }},
  "longo": {{
    "duracao": "~XX segundos",
    "angulo": "qual recorte específico do tema",
    "audiencia": "Pré-chegada | Recém-chegada | Adaptada | Todas",
    "gancho_fala": "frase exata de abertura",
    "gancho_tela": "texto overlay do gancho (max 6 palavras)",
    "cues": [
      {{
        "rotulo": "nome da seção",
        "cue": "o pensamento a comunicar",
        "tela": "texto overlay (max 6 palavras)",
        "imagem": "o que filmar ou mostrar"
      }}
    ],
    "cta_fala": "CTA como fala natural",
    "cta_tela": "texto overlay do CTA",
    "palavra_chave": "PALAVRA ou null"
  }}
}}"""

    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data  = json.loads(match.group(0)) if match else json.loads(raw)

    # Formatar saída legível para o Notion
    linhas = []
    for versao_key, label in [("curto", "CURTO"), ("medio", "MÉDIO"), ("longo", "LONGO")]:
        v = data.get(versao_key, {})
        linhas.append(f"=== 🎬 {label} — {v.get('duracao', '')} ===")
        linhas.append(f"Ângulo: {v.get('angulo', '')}")
        linhas.append(f"Audiência: {v.get('audiencia', '')}")
        linhas.append("")
        linhas.append("⚡ GANCHO")
        linhas.append(f"> {v.get('gancho_fala', '')}")
        linhas.append(f"📺 Tela: {v.get('gancho_tela', '')}")
        linhas.append("")
        linhas.append("🎯 CUES DE FALA")
        for i, cue in enumerate(v.get("cues", []), 1):
            linhas.append(f"{i}. [{cue.get('rotulo', '')}] — {cue.get('cue', '')}")
            linhas.append(f"   📺 Tela: {cue.get('tela', '')}")
            linhas.append(f"   🎥 Imagem: {cue.get('imagem', '')}")
        linhas.append("")
        linhas.append("📣 CTA")
        linhas.append(f"> {v.get('cta_fala', '')}")
        linhas.append(f"📺 Tela: {v.get('cta_tela', '')}")
        pk = v.get("palavra_chave")
        if pk and pk != "null":
            linhas.append(f"🔑 Palavra-chave: {pk}")
        linhas.append("")
        linhas.append("---")
        linhas.append("")

    return {"roteiro": "\n".join(linhas)}


# ─── FUNÇÃO 4: Verificar duplicatas ──────────────────────────────────────────
def ja_existe_pagina_instagram(pauta_id: str) -> bool:
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
    _PILARS_VALIDOS = {"Sistema", "Trajetória", "Identidade", "Sociedade"}
    pilar_raw       = pauta["pilar"]
    pilar_instagram = pilar_raw if pilar_raw in _PILARS_VALIDOS else ""

    kpi_map = {
        "Salvamento alto":       "SAVE",
        "Compartilhamento alto": "SHARE",
        "Comentário alto":       "COMMENTS",
        "Alcance":               "REACH",
    }
    meta_val = kpi_map.get(pauta["kpi"], pauta["kpi"])

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
        "Formato": {
            "select": {"name": "Reels"}
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
        **({"Pilars": {"multi_select": [{"name": pilar_instagram}]}} if pilar_instagram else {}),
        **({"META":   {"multi_select": [{"name": meta_val}]}}        if meta_val else {}),
        "Roteiro Reel": {
            "rich_text": [{"text": {"content": conteudo["roteiro"][:2000]}}]
        },
        "Status Aprovação": {
            "select": {"name": "Aguardando"}
        },
    }

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
    print("\n=== CAMADA 3B — Instagram Reels ===")
    print(f"Data: {date.today().isoformat()}\n")

    print("▶ Garantindo campos na base Instagram...")
    garantir_campos_instagram()

    print("\n▶ Buscando pautas prontas (Reels, sem Canva URL, com data)...")
    pautas = buscar_pautas_reels()

    if not pautas:
        print("  ℹ Nenhuma pauta nova para processar. Encerrando.")
        return

    processadas = 0
    erros = 0

    for i, pauta in enumerate(pautas, 1):
        titulo = pauta["titulo"] or f"(sem título — {pauta['notion_page_id'][:8]})"
        print(f"\n[{i}/{len(pautas)}] {titulo}")

        if ja_existe_pagina_instagram(pauta["notion_page_id"]):
            print("  ↩ Página Instagram já existe para esta pauta. Pulando.")
            continue

        try:
            print("  ⏳ Gerando 3 roteiros (curto/médio/longo) com Claude...")
            conteudo = gerar_roteiros_reel(pauta)
            print("  ✓ Roteiros gerados.")

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
