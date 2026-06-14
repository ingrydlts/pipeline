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
import anthropic

# ─── Clientes ────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ─── Contexto de audiência ────────────────────────────────────────────────────
AUDIENCIA_CONTEXT = open("scripts/audiencia_context.txt", encoding="utf-8").read()

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

    # ── Mídia generalista francesa (Google News filtrado por imigração/trabalho) ─
    {
        "nome": "Franceinfo — Immigration & Étrangers",
        "url": "https://news.google.com/rss/search?q=site:francetvinfo.fr+immigration+%C3%A9tranger+s%C3%A9jour&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "séjour", "titre", "visa",
            "naturalisation", "migrant", "expulsion", "préfecture"
        ],
    },
    {
        "nome": "20 Minutes — Immigration France",
        "url": "https://news.google.com/rss/search?q=site:20minutes.fr+immigration+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "migrant", "séjour", "visa",
            "naturalisation", "préfecture", "titre"
        ],
    },
    {
        "nome": "Le Parisien — Immigration & Société",
        "url": "https://news.google.com/rss/search?q=site:leparisien.fr+immigration+%C3%A9tranger+int%C3%A9gration&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "migrant", "intégration",
            "séjour", "naturalisation", "visa"
        ],
    },
    {
        "nome": "BFMTV — Immigration & Lois",
        "url": "https://news.google.com/rss/search?q=site:bfmtv.com+immigration+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P03"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "migrant", "séjour",
            "préfecture", "naturalisation", "loi immigration"
        ],
    },
    {
        "nome": "Le Monde — Immigration & Politique Migratoire",
        "url": "https://news.google.com/rss/search?q=site:lemonde.fr+immigration+%C3%A9tranger+politique+migratoire&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "migrant", "politique migratoire",
            "titre de séjour", "naturalisation", "expulsion", "intégration"
        ],
    },
    {
        "nome": "Le Figaro — Immigration & Travail",
        "url": "https://news.google.com/rss/search?q=site:lefigaro.fr+immigration+%C3%A9tranger+emploi&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "loi", "séjour",
            "naturalisation", "migrant", "expulsion", "emploi"
        ],
    },
    {
        "nome": "Libération — Immigration & Droits",
        "url": "https://news.google.com/rss/search?q=site:liberation.fr+immigration+%C3%A9tranger+droits&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "immigration", "étranger", "droit", "migrant",
            "naturalisation", "expulsion", "rétention", "intégration"
        ],
    },
    {
        "nome": "The Local France (EN)",
        "url": "https://www.thelocal.fr/feed/",
        "categoria": "burocratica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "visa", "residency", "work permit", "immigration", "expat",
            "tax", "health insurance", "bank", "titre de séjour",
            "bureaucracy", "foreigner", "permit", "carte vitale"
        ],
    },

    # ── Fontes institucionais específicas (via Google News) ──────────────────
    {
        "nome": "Google News — Ameli Sécu Étrangers",
        "url": "https://news.google.com/rss/search?q=ameli+assurance+maladie+%C3%A9trangers+carte+vitale+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "ameli", "assurance maladie", "carte vitale", "sécurité sociale",
            "mutuelle", "remboursement", "médecin", "étranger"
        ],
    },
    {
        "nome": "Google News — URSSAF Auto-entrepreneur Étrangers",
        "url": "https://news.google.com/rss/search?q=URSSAF+auto-entrepreneur+ind%C3%A9pendant+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "URSSAF", "auto-entrepreneur", "indépendant", "freelance",
            "micro-entreprise", "cotisations", "étranger", "travailleur"
        ],
    },
    {
        "nome": "Google News — ENIC-NARIC Reconnaissance Diplômes",
        "url": "https://news.google.com/rss/search?q=reconnaissance+dipl%C3%B4me+%C3%A9tranger+France+attestation+ENIC&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "diplôme", "reconnaissance", "attestation", "équivalence",
            "ENIC", "NARIC", "étranger", "comparabilité", "validation"
        ],
    },
    {
        "nome": "Google News — CPF Mon Compte Formation",
        "url": "https://news.google.com/rss/search?q=%22compte+personnel+de+formation%22+CPF+formation+%C3%A9tranger+alternance&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "CPF", "compte personnel de formation", "formation professionnelle",
            "alternance", "certification", "financement", "étranger"
        ],
    },
    {
        "nome": "Google News — APEC Emploi Cadres France",
        "url": "https://news.google.com/rss/search?q=APEC+emploi+cadres+recrutement+salaires+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "APEC", "cadres", "emploi", "recrutement", "salaires",
            "marché du travail", "ingénieur", "manager", "executive"
        ],
    },
    {
        "nome": "Google News — INSEE Coût de Vie France",
        "url": "https://news.google.com/rss/search?q=INSEE+inflation+co%C3%BBt+vie+logement+salaires+pouvoir+achat+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P01", "P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "INSEE", "inflation", "coût de la vie", "logement", "loyer",
            "salaires", "pouvoir d'achat", "statistiques", "prix"
        ],
    },
    {
        "nome": "Google News — Intégration Associations Migrants",
        "url": "https://news.google.com/rss/search?q=Singa+int%C3%A9gration+migrants+France+r%C3%A9seau+professionnel+association&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "intégration", "migrants", "association", "réseau",
            "communauté", "soutien", "accompagnement", "Singa", "CIUP"
        ],
    },
    {
        "nome": "Google News — Investissements Expatriés BoursoBank",
        "url": "https://news.google.com/rss/search?q=BoursoBank+PEA+assurance-vie+investissement+expatri%C3%A9+France+banque&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "BoursoBank", "PEA", "assurance-vie", "investissement",
            "expatrié", "épargne", "banque en ligne", "non-résident"
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


def gerar_por_que_interessa(titulo: str, descricao: str, fonte: dict) -> str:
    """Chama Claude para gerar 2-3 frases explicando por que o artigo importa para a audiência."""
    personas_str = " e ".join(fonte["personas"])
    categoria_str = fonte["categoria"]

    prompt = f"""Você é editora sênior do canal Por Dentro — canal YouTube/Instagram para brasileiras imigrantes na França.

CONTEXTO DE AUDIÊNCIA:
{AUDIENCIA_CONTEXT}

ARTIGO ENCONTRADO:
- Título: {titulo}
- Descrição: {descricao[:400]}
- Categoria editorial: {categoria_str}
- Personas-alvo desta fonte: {personas_str}

Escreva 2 a 3 frases diretas explicando POR QUE esse artigo interessa especificamente para as personas indicadas.
Mencione a persona de forma concreta (ex: "para quem está chegando agora", "para quem já mora há 2+ anos").
Tom: editorial interno, objetivo, sem floreios. NÃO comece com "Este artigo" nem com "Para".
Responda APENAS com o texto das frases, sem formatação extra."""

    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",  # haiku = mais barato para análise rápida
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()[:1000]
    except Exception as e:
        print(f"    ⚠ Erro ao gerar 'por que isso interessa?': {e}")
        return ""


def criar_rascunho_notion(titulo: str, url: str, descricao: str,
                           fonte_nome: str, fonte: dict) -> bool:
    """Cria uma nova página de rascunho no Notion Database de entrada."""
    titulo_limpo    = normalizar_texto(titulo)[:200]
    descricao_limpa = normalizar_texto(descricao)[:500]

    # Gera análise de relevância para a audiência
    por_que_interessa = gerar_por_que_interessa(titulo_limpo, descricao_limpa, fonte)

    properties = {
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

    # Adiciona "por que isso interessa?" se foi gerado com sucesso
    if por_que_interessa:
        properties["por que isso interessa?"] = {
            "rich_text": [{"text": {"content": por_que_interessa}}]
        }

    try:
        notion.pages.create(
            parent={"database_id": DATABASE_ENTRADA},
            properties=properties
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
