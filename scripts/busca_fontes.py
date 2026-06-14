"""
CAMADA 1 — Por Dentro Content Pipeline
Busca automática em fontes oficiais francesas → cria rascunhos no Notion

Roda toda sexta-feira às 7h (via GitHub Actions)
Os rascunhos ficam prontos para a Camada 2 processar na segunda-feira
"""

import os
import re
import hashlib
from datetime import datetime, timezone, timedelta
import feedparser
import httpx
from notion_client import Client

# ─── Clientes ────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ─── Configuração de Fontes ───────────────────────────────────────────────────
# Cada fonte tem: nome, url RSS, categoria Notion, personas, urgência padrão
# e keywords de filtro (lista vazia = aceita tudo da fonte)

FONTES_RSS = [
    # ── Fontes com RSS próprio validado ──────────────────────────────────────
    {
        "nome": "La Cimade",
        "url": "https://www.lacimade.org/feed/",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "étranger", "migrant", "séjour", "droit", "expulsion",
            "rétention", "recours", "tribunal", "préfecture", "visa"
        ],
    },
    {
        "nome": "GISTI",
        "url": "https://www.gisti.org/spip.php?page=backend",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "keywords": [],  # feed já é filtrado — aceita tudo
    },
    {
        "nome": "Café de la Bourse",
        "url": "https://www.cafedelabourse.com/feed",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "PEA", "assurance vie", "impôt", "déclaration",
            "investissement", "épargne", "fiscalité", "patrimoine"
        ],
    },

    # ── Google News RSS (fallback para fontes sem RSS próprio) ───────────────
    # Google News RSS é público, gratuito e não requer autenticação
    {
        "nome": "Google News — Légifrance Étrangers",
        "url": "https://news.google.com/rss/search?q=legifrance+%C3%A9trangers+s%C3%A9jour+naturalisation&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "keywords": [
            "étranger", "naturalisation", "séjour", "immigration",
            "titre de séjour", "CESEDA", "asile", "ressortissant"
        ],
    },
    {
        "nome": "Google News — Immigration France",
        "url": "https://news.google.com/rss/search?q=immigration+France+loi+%C3%A9tranger+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P03"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "titre", "naturalisation",
            "préfecture", "visa", "séjour", "loi"
        ],
    },
    {
        "nome": "Google News — CAF APL Étrangers",
        "url": "https://news.google.com/rss/search?q=CAF+APL+allocation+%C3%A9trangers+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "APL", "aide", "allocation", "logement", "CAF",
            "prestation", "revenu", "ressortissant"
        ],
    },
    {
        "nome": "Google News — Service-Public Étrangers",
        "url": "https://news.google.com/rss/search?q=service-public.fr+%C3%A9trangers+d%C3%A9marche&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "keywords": [
            "étranger", "démarche", "titre", "préfecture",
            "renouvellement", "carte de séjour", "ANEF"
        ],
    },
    {
        "nome": "Google News — France Travail Emploi Étrangers",
        "url": "https://news.google.com/rss/search?q=France+Travail+emploi+%C3%A9trangers+alternance&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "emploi", "formation", "alternance", "diplôme",
            "contrat", "recrutement", "travailleur étranger"
        ],
    },
    {
        "nome": "Google News — Campus France Brésil",
        "url": "https://news.google.com/rss/search?q=Campus+France+visa+%C3%A9tudiant+br%C3%A9sil+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "brésil", "visa étudiant", "bourse", "inscription",
            "master", "doctorat", "Campus France"
        ],
    },
    {
        "nome": "Google News — Fiscalité Expatriés France",
        "url": "https://news.google.com/rss/search?q=fiscalit%C3%A9+expatri%C3%A9s+France+imp%C3%B4t+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "impôt", "déclaration", "expatrié", "non-résident",
            "fiscalité", "convention", "Brésil", "patrimoine"
        ],
    },
]

# ─── Janela de tempo: só aceita artigos das últimas 8 dias ───────────────────
JANELA_DIAS = 8


# ─── Helpers ─────────────────────────────────────────────────────────────────

def normalizar_texto(texto: str) -> str:
    """Remove HTML tags e normaliza espaços."""
    texto = re.sub(r'<[^>]+>', ' ', texto or '')
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


def contem_keyword(titulo: str, descricao: str, keywords: list) -> bool:
    """Verifica se o artigo contém pelo menos uma das keywords (case-insensitive)."""
    if not keywords:
        return True
    texto = (titulo + ' ' + descricao).lower()
    return any(kw.lower() in texto for kw in keywords)


def dentro_da_janela(entry) -> bool:
    """Verifica se o artigo foi publicado nos últimos JANELA_DIAS dias."""
    published = entry.get('published_parsed') or entry.get('updated_parsed')
    if not published:
        return True  # sem data → aceita (melhor incluir do que perder)
    dt_entry = datetime(*published[:6], tzinfo=timezone.utc)
    dt_limite = datetime.now(timezone.utc) - timedelta(days=JANELA_DIAS)
    return dt_entry >= dt_limite


def gerar_id_dedup(url: str) -> str:
    """Gera hash curto da URL para deduplicação."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def url_ja_existe_no_notion(url: str) -> bool:
    """Verifica se já existe um rascunho com essa URL no Notion."""
    try:
        response = notion.databases.query(
            database_id=DATABASE_ENTRADA,
            filter={
                "property": "Fonte",
                "url": {"equals": url}
            }
        )
        return len(response["results"]) > 0
    except Exception:
        return False  # em caso de erro, tenta criar mesmo assim


def criar_rascunho_notion(titulo: str, url: str, descricao: str,
                           fonte_nome: str, fonte: dict) -> bool:
    """Cria uma nova página de rascunho no Notion Database de entrada."""
    titulo_limpo    = normalizar_texto(titulo)[:200]
    descricao_limpa = normalizar_texto(descricao)[:500]

    try:
        notion.pages.create(
            parent={"database_id": DATABASE_ENTRADA},
            properties={
                "Título": {
                    "title": [{"text": {"content": titulo_limpo}}]
                },
                "Categoria": {
                    "multi_select": [{"name": fonte["categoria"]}]
                },
                "Persona": {
                    "multi_select": [{"name": p} for p in fonte["personas"]]
                },
                "Urgência": {
                    "select": {"name": fonte["urgencia"]}
                },
                "Fonte": {
                    "url": url
                },
                "Notas": {
                    "rich_text": [{"text": {"content": descricao_limpa}}]
                },
                "Status": {
                    "select": {"name": "rascunho"}
                },
            }
        )
        return True
    except Exception as e:
        print(f"    ✗ Erro ao criar no Notion: {e}")
        return False


# ─── Processador de RSS ───────────────────────────────────────────────────────

def processar_fonte_rss(fonte: dict) -> int:
    """Lê um feed RSS, filtra e cria rascunhos no Notion. Retorna qtd criada."""
    print(f"\n📡 {fonte['nome']}")
    criados = 0

    try:
        # feedparser aceita URL direto — faz o fetch internamente
        feed = feedparser.parse(fonte["url"])

        if feed.bozo and not feed.entries:
            print(f"  ⚠ Feed inválido ou inacessível: {fonte['url']}")
            return 0

        print(f"  → {len(feed.entries)} artigo(s) encontrado(s) no feed")

        for entry in feed.entries:
            titulo     = entry.get("title", "")
            url        = entry.get("link", "")
            descricao  = entry.get("summary", "") or entry.get("description", "")

            # Filtros
            if not url:
                continue
            if not dentro_da_janela(entry):
                continue
            if not contem_keyword(titulo, descricao, fonte["keywords"]):
                continue
            if url_ja_existe_no_notion(url):
                print(f"  ↩ Já existe: {titulo[:60]}...")
                continue

            # Criar no Notion
            ok = criar_rascunho_notion(titulo, url, descricao, fonte["nome"], fonte)
            if ok:
                criados += 1
                print(f"  ✓ Rascunho criado: {titulo[:70]}")

    except Exception as e:
        print(f"  ✗ Erro ao processar feed: {e}")

    return criados


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    data_hoje = datetime.now().strftime('%d/%m/%Y')
    print(f"\n🔍 CAMADA 1 — Busca automática de fontes ({data_hoje})")
    print(f"   Janela: últimos {JANELA_DIAS} dias\n")

    total_criados = 0

    for fonte in FONTES_RSS:
        qtd = processar_fonte_rss(fonte)
        total_criados += qtd

    print(f"\n{'═'*50}")
    print(f"✅ Camada 1 concluída: {total_criados} rascunho(s) criado(s) no Notion")
    print(f"   Próximo passo: Camada 2 roda na segunda-feira e estrutura tudo com Claude")
    print(f"{'═'*50}\n")


if __name__ == "__main__":
    main()
