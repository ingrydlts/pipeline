"""
CAMADA 1 — Por Dentro Content Pipeline
Busca automática em fontes RSS → filtra por audiência → cria rascunhos no Notion

Roda toda sexta-feira às 7h UTC (via GitHub Actions)
Os rascunhos ficam prontos para curadoria manual + Camada 2 na segunda-feira

AUDIÊNCIA: Brasileiras imigrantes na França
  P01 Camila   — ainda no Brasil, pesquisando, quer saber se vale a pena
  P02 Larissa  — recém-chegada (<18 meses), sobrecarregada com burocracia
  P03 Renata   — adaptada (2-5 anos), quer otimizar carreira e finanças
  P04 Non-conquis — situações específicas (alternance, duplo diploma, jurídico avançado)
"""

import os
import re
import hashlib
from datetime import datetime, timezone, timedelta
import feedparser
from notion_client import Client
import anthropic

# ─── Clientes ────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ─── Contexto de audiência (usado pelo Claude para "por que isso interessa?") ─
AUDIENCIA_CONTEXT = open("scripts/audiencia_context.txt", encoding="utf-8").read()

# ─── Parâmetros globais ───────────────────────────────────────────────────────
JANELA_DIAS          = 8   # aceita artigos publicados nos últimos 8 dias
MINIMO_POR_CATEGORIA = 5   # garante ao menos 5 rascunhos por categoria

# ─── FONTES RSS ───────────────────────────────────────────────────────────────
#
# Cada fonte define:
#   keywords         → ao menos UMA deve estar no título ou descrição (AND implícito no texto)
#   keywords_excluir → se QUALQUER uma aparecer, o artigo é descartado
#
# FILOSOFIA DE FILTRO:
#   Jurídica   → mudanças que afetam quem já TEM título legal (séjour, renovação, naturalization)
#                NÃO: refugiados, OQTF, sem-papiers, travessias de fronteira
#   Burocrática → passo a passo que P02 executa na semana: CAF, Sécu, ANEF, imposto
#   Acadêmica  → estudar/trabalhar na França como estrangeiro qualificado
#                NÃO: pesquisas universitárias, vestibular francês, rankings de faculdade
#   Cívica     → direitos, redes de apoio, integração social — o que existe para nos ajudar
#   Finanças   → dinheiro real do dia a dia: aluguel, salário, investir em euros, remessas
#                NÃO: bolsa de valores, CAC 40, geopolítica financeira
#   Trendmapping → identidade, choque cultural, saúde mental, vida cotidiana real na França

FONTES_RSS = [

    # ═══════════════════════════════════════════════════════════════════════════
    # JURÍDICA — direitos e legislação que mudam a vida de quem já está na França
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "GISTI — Droit des Étrangers",
        "url": "https://www.gisti.org/spip.php?page=backend",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "keywords": [],  # feed já é curado — aceita tudo
        "keywords_excluir": [],
    },
    {
        "nome": "La Cimade — Droits des Migrants",
        "url": "https://www.lacimade.org/feed/",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "titre de séjour", "séjour", "préfecture", "droit", "recours",
            "naturalisation", "visa", "ressortissant", "carte de séjour", "ANEF"
        ],
        "keywords_excluir": [
            "réfugié", "asile", "demandeur d'asile", "sans-papiers", "OQTF",
            "Méditerranée", "Frontex", "traversée", "barque", "naufrage",
            "Syrie", "Mali", "Afghanistan", "Libye"
        ],
    },
    {
        # Foco: o que muda para quem tem titre de séjour e precisa renovar
        "nome": "Google News — Titre de Séjour & ANEF",
        "url": "https://news.google.com/rss/search?q=%22titre+de+s%C3%A9jour%22+renouvellement+ANEF+pr%C3%A9fecture+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "keywords": [
            "titre de séjour", "renouvellement", "préfecture", "ANEF",
            "récépissé", "carte de séjour", "dossier", "demande"
        ],
        "keywords_excluir": [
            "réfugié", "asile", "sans-papiers", "OQTF", "Méditerranée",
            "Frontex", "traversée", "naufrage"
        ],
    },
    {
        # Foco: condições, prazos, critérios — o que P03 está planejando
        "nome": "Google News — Naturalisation Française",
        "url": "https://news.google.com/rss/search?q=naturalisation+fran%C3%A7aise+conditions+d%C3%A9lai+ressortissant+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "naturalisation", "française", "conditions", "délai",
            "critères", "acquisition", "ressortissant", "décret"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        # Foco: vistos de trabalho que P01 pesquisa antes de vir
        "nome": "Google News — Passeport Talent & Visa Travail",
        "url": "https://news.google.com/rss/search?q=%22passeport+talent%22+OR+%22visa+travail%22+%C3%A9tranger+autorisation+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P03"],
        "urgencia": "alta",
        "keywords": [
            "passeport talent", "visa travail", "salarié", "autorisation de travail",
            "qualification", "ressortissant", "étranger qualifié", "titre"
        ],
        "keywords_excluir": ["réfugié", "asile"],
    },
    {
        # Foco: mudanças de lei que afetam imigrantes com status legal
        "nome": "Google News — Légifrance Séjour & Naturalisation",
        "url": "https://news.google.com/rss/search?q=legifrance+%C3%A9trangers+s%C3%A9jour+naturalisation+ressortissant&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "keywords": [
            "étranger", "naturalisation", "séjour", "ressortissant",
            "CESEDA", "titre", "décret", "circulaire"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # BUROCRÁTICA — os passos práticos que P02 executa toda semana
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Google News — CAF APL Logement Étrangers",
        "url": "https://news.google.com/rss/search?q=CAF+APL+allocation+logement+%C3%A9trangers+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "CAF", "APL", "allocation", "logement", "aide", "prestation",
            "revenu", "ressortissant", "étranger"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — ANEF Service-Public Démarches Étrangers",
        "url": "https://news.google.com/rss/search?q=ANEF+service-public+%C3%A9tranger+d%C3%A9marche+renouvellement+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "keywords": [
            "étranger", "démarche", "ANEF", "préfecture",
            "renouvellement", "carte de séjour", "téléprocédure"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Ameli Sécu Carte Vitale Étrangers",
        "url": "https://news.google.com/rss/search?q=ameli+assurance+maladie+%C3%A9trangers+carte+vitale+s%C3%A9cu+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "ameli", "assurance maladie", "carte vitale", "sécurité sociale",
            "mutuelle", "remboursement", "numéro de sécu", "étranger"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — URSSAF Auto-entrepreneur Étranger",
        "url": "https://news.google.com/rss/search?q=URSSAF+auto-entrepreneur+freelance+ind%C3%A9pendant+%C3%A9tranger+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "URSSAF", "auto-entrepreneur", "indépendant", "freelance",
            "micro-entreprise", "cotisations", "étranger", "travailleur"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Impôts Déclaration Étrangers France",
        "url": "https://news.google.com/rss/search?q=imp%C3%B4ts+d%C3%A9claration+%C3%A9trangers+France+r%C3%A9sident+fiscal&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "keywords": [
            "impôt", "déclaration", "résident fiscal", "étranger",
            "impots.gouv", "IR", "avis d'imposition", "non-résident"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "The Local France — Expat Practical Guide (EN)",
        "url": "https://www.thelocal.fr/feed/",
        "categoria": "burocratica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "visa", "residency", "work permit", "expat", "foreigner",
            "tax", "health insurance", "bank account", "titre de séjour",
            "permit", "carte vitale", "French administration", "bureaucracy",
            "social security", "CAF", "housing benefit"
        ],
        "keywords_excluir": ["refugee", "asylum", "boat", "Mediterranean"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # ACADÊMICA — estudar E trabalhar na França como estrangeiro qualificado
    # NÃO é sobre pesquisa científica ou vestibular francês — é sobre a jornada
    # de quem quer validar seu diploma, conseguir alternance ou subir na carreira
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "L'Étudiant — Études & Emploi en France",
        "url": "https://www.letudiant.fr/rss.xml",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "étranger", "international", "visa étudiant", "alternance",
            "Campus France", "admission", "master", "apprentissage",
            "titre professionnel", "inscription", "formation"
        ],
        "keywords_excluir": [
            "bac", "terminale", "lycée", "parcoursup", "concours prépa",
            "classes prépa", "brevet", "collège", "primaire"
        ],
    },
    {
        # P01 e P02: alternance é O caminho mais viável para entrar no mercado
        "nome": "Google News — Alternance Étrangers France 2026",
        "url": "https://news.google.com/rss/search?q=alternance+France+%C3%A9tranger+visa+contrat+apprentissage+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "alternance", "apprentissage", "étranger", "contrat",
            "visa étudiant", "CFA", "formation", "diplôme", "entreprise"
        ],
        "keywords_excluir": ["bac", "lycée", "parcoursup"],
    },
    {
        # P02 e P03: "meu diploma vale algo aqui?"
        "nome": "Google News — Reconnaissance Diplôme Étranger France",
        "url": "https://news.google.com/rss/search?q=reconnaissance+dipl%C3%B4me+%C3%A9tranger+France+attestation+emploi+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "diplôme étranger", "reconnaissance", "attestation",
            "équivalence", "ENIC-NARIC", "comparabilité",
            "validation", "VAE", "étranger qualifié"
        ],
        "keywords_excluir": [],
    },
    {
        # P03 e P04: evoluir na carreira francesa — salários, recrutamento, cadres
        "nome": "Google News — APEC Emploi Cadres Étrangers Qualification",
        "url": "https://news.google.com/rss/search?q=emploi+cadres+%C3%A9tranger+qualification+recrutement+salaire+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "emploi", "cadres", "étranger", "recrutement", "salaire",
            "qualification", "contrat CDI", "marché du travail", "APEC"
        ],
        "keywords_excluir": [],
    },
    {
        # P01: "como entro no sistema universitário francês?"
        "nome": "Google News — Campus France Visa Étudiant Brésil",
        "url": "https://news.google.com/rss/search?q=Campus+France+visa+%C3%A9tudiant+br%C3%A9sil+admission+master+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "baixa",
        "keywords": [
            "Campus France", "visa étudiant", "brésil", "admission",
            "master", "université", "bourse", "dossier", "étudiant étranger"
        ],
        "keywords_excluir": [],
    },
    {
        # P02 e P03: créditos de formação que qualquer trabalhador acumula
        "nome": "Google News — CPF Formation Professionnelle Étranger",
        "url": "https://news.google.com/rss/search?q=%22compte+personnel+de+formation%22+CPF+%C3%A9tranger+formation+salari%C3%A9+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "CPF", "compte personnel de formation", "formation professionnelle",
            "salarié", "étranger", "certification", "financement", "apprentissage"
        ],
        "keywords_excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CÍVICA — o que existe para nos apoiar: redes, direitos, comunidade
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "La Cimade — Vie Civique & Droits",
        "url": "https://www.lacimade.org/feed/",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "droit", "association", "accompagnement", "soutien",
            "intégration", "communauté", "réseau", "aide juridique"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Associations Intégration Migrants France",
        "url": "https://news.google.com/rss/search?q=association+int%C3%A9gration+migrants+France+r%C3%A9seau+soutien+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "intégration", "migrants", "association", "réseau", "soutien",
            "accompagnement", "communauté", "Singa", "bénévolat"
        ],
        "keywords_excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FINANÇAS — dinheiro real: aluguel, salário, investir em euros, remessas
    # NÃO é sobre bolsa de valores ou macroeconomia abstrata
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Café de la Bourse — Investissements Pratiques",
        "url": "https://www.cafedelabourse.com/feed",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "PEA", "assurance vie", "déclaration", "investissement",
            "épargne", "fiscalité", "patrimoine", "livret", "placement"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "action", "dividende", "géopolitique"],
    },
    {
        # Custo de vida REAL — o que P01 quer saber antes de vir, P03 quer comparar
        "nome": "Google News — Coût de Vie Loyer Expatrié France",
        "url": "https://news.google.com/rss/search?q=co%C3%BBt+de+la+vie+loyer+logement+salaire+expatri%C3%A9+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P01", "P03"],
        "urgencia": "media",
        "keywords": [
            "coût de la vie", "loyer", "logement", "salaire", "smic",
            "pouvoir d'achat", "prix", "budget", "expatrié", "inflation"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "bourse", "actionnaire"],
    },
    {
        # Fiscalidade binacional — a dor de P03 que mora entre dois países
        "nome": "Google News — Fiscalité Expatriés Brésil France",
        "url": "https://news.google.com/rss/search?q=fiscalit%C3%A9+expatri%C3%A9s+France+imp%C3%B4t+%C3%A9tranger+convention+br%C3%A9sil&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "impôt", "déclaration", "expatrié", "non-résident",
            "fiscalité", "convention fiscale", "brésil", "patrimoine", "sortie définitive"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },
    {
        # Investimentos disponíveis para quem mora na França (residente fiscal)
        "nome": "Google News — PEA Assurance-Vie Expatrié Résident France",
        "url": "https://news.google.com/rss/search?q=PEA+assurance-vie+%C3%A9pargne+r%C3%A9sident+France+expatri%C3%A9+placement&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "PEA", "assurance-vie", "épargne", "placement", "résident fiscal",
            "expatrié", "livret", "fiscalité", "rendement"
        ],
        "keywords_excluir": ["CAC 40", "actionnaire", "dividende"],
    },
    {
        # Remessas Brasil-França — envio de dinheiro entre países
        "nome": "Google News — Remessas Virement Brésil France Change",
        "url": "https://news.google.com/rss/search?q=virement+international+br%C3%A9sil+France+change+remise+r%C3%A8gles&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "virement international", "brésil", "change", "euro", "remise",
            "transfert argent", "Wise", "banque", "taux de change"
        ],
        "keywords_excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # TRENDMAPPING — identidade, choque cultural, vida cotidiana REAL na França
    # É o conteúdo que P03 compartilha dizendo "eu sinto exatamente isso"
    # ═══════════════════════════════════════════════════════════════════════════
    {
        # Choque cultural, adaptação — o que P02 vive e P03 já viveu
        "nome": "Google News — Choc Culturel Expatrié Vie France",
        "url": "https://news.google.com/rss/search?q=%22choc+culturel%22+OR+%22vie+d%27expatri%C3%A9%22+OR+%22adaptation%22+immigrant+France+quotidien&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "choc culturel", "expatrié", "adaptation", "vie quotidienne",
            "immigrant", "intégration", "différences culturelles", "isolement"
        ],
        "keywords_excluir": [],
    },
    {
        # Identidade — a dor invisível que P03 sente mas não consegue nomear
        "nome": "Google News — Identité Biculturelle Immigrant France",
        "url": "https://news.google.com/rss/search?q=identit%C3%A9+biculturelle+OR+%22entre+deux+cultures%22+OR+%22appartenance%22+immigrant+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "identité", "biculturel", "entre deux cultures", "appartenance",
            "immigrant", "brésil", "intégration culturelle", "sentiment"
        ],
        "keywords_excluir": [],
    },
    {
        # Saúde mental — P02 sobrecarregada, P03 questionando escolhas
        "nome": "Google News — Santé Mentale Expatriés Anxiété France",
        "url": "https://news.google.com/rss/search?q=sant%C3%A9+mentale+expatri%C3%A9s+OR+%22syndrome+de+paris%22+OR+%22burnout+expatri%C3%A9%22+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "santé mentale", "expatrié", "anxiété", "burnout",
            "syndrome de paris", "isolement", "dépression", "bien-être"
        ],
        "keywords_excluir": [],
    },
    {
        # Mercado de trabalho real — diferenças culturais que P03 enfrenta no escritório
        "nome": "Google News — Travail France Différences Culturelles Brésil",
        "url": "https://news.google.com/rss/search?q=travail+France+diff%C3%A9rences+culturelles+br%C3%A9sil+expatri%C3%A9+int%C3%A9gration&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "travail", "France", "différences culturelles", "brésil",
            "expatrié", "collègues", "management", "intégration professionnelle"
        ],
        "keywords_excluir": [],
    },
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def normalizar_texto(texto: str) -> str:
    texto = re.sub(r'<[^>]+>', ' ', texto or '')
    return re.sub(r'\s+', ' ', texto).strip()


def contem_keyword(titulo: str, descricao: str, keywords: list) -> bool:
    """Retorna True se ao menos uma keyword aparecer no texto (lista vazia = aceita tudo)."""
    if not keywords:
        return True
    texto = (titulo + ' ' + descricao).lower()
    return any(kw.lower() in texto for kw in keywords)


def contem_blacklist(titulo: str, descricao: str, blacklist: list) -> bool:
    """Retorna True se alguma palavra da blacklist aparecer — artigo deve ser descartado."""
    if not blacklist:
        return False
    texto = (titulo + ' ' + descricao).lower()
    return any(kw.lower() in texto for kw in blacklist)


def dentro_da_janela(entry) -> bool:
    published = entry.get('published_parsed') or entry.get('updated_parsed')
    if not published:
        return True
    dt_entry = datetime(*published[:6], tzinfo=timezone.utc)
    dt_limite = datetime.now(timezone.utc) - timedelta(days=JANELA_DIAS)
    return dt_entry >= dt_limite


def url_ja_existe_no_notion(url: str) -> bool:
    try:
        response = notion.databases.query(
            database_id=DATABASE_ENTRADA,
            filter={"property": "Fonte", "url": {"equals": url}}
        )
        return len(response["results"]) > 0
    except Exception:
        return False


def gerar_por_que_interessa(titulo: str, descricao: str, fonte: dict) -> str:
    """Chama Claude Haiku para gerar 2-3 frases de relevância editorial baseadas na audiência."""
    personas_str = " e ".join(fonte["personas"])
    prompt = f"""Você é editora sênior do canal Por Dentro — canal YouTube/Instagram para brasileiras imigrantes na França.

CONTEXTO DE AUDIÊNCIA:
{AUDIENCIA_CONTEXT}

ARTIGO ENCONTRADO:
- Título: {titulo}
- Descrição: {descricao[:400]}
- Categoria editorial: {fonte['categoria']}
- Personas-alvo: {personas_str}

Escreva 2 a 3 frases diretas explicando POR QUE esse artigo interessa para as personas indicadas.
Mencione a persona de forma concreta (ex: "quem está chegando agora", "quem já mora há 2+ anos").
Tom: editorial interno, objetivo, sem floreios. NÃO comece com "Este artigo" nem com "Para".
Responda APENAS com o texto das frases, sem formatação extra."""

    try:
        msg = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()[:1000]
    except Exception as e:
        print(f"    ⚠ Erro ao gerar 'por que isso interessa?': {e}")
        return ""


def criar_rascunho_notion(titulo: str, url: str, descricao: str,
                           fonte_nome: str, fonte: dict) -> bool:
    titulo_limpo    = normalizar_texto(titulo)[:200]
    descricao_limpa = normalizar_texto(descricao)[:500]
    por_que_interessa = gerar_por_que_interessa(titulo_limpo, descricao_limpa, fonte)

    properties = {
        "Título":    {"title":       [{"text": {"content": titulo_limpo}}]},
        "Categoria": {"multi_select": [{"name": fonte["categoria"]}]},
        "Persona":   {"multi_select": [{"name": p} for p in fonte["personas"]]},
        "Urgência":  {"select":       {"name": fonte["urgencia"]}},
        "Fonte":     {"url": url},
        "Notas":     {"rich_text":   [{"text": {"content": descricao_limpa}}]},
        "Status":    {"select":       {"name": "rascunho"}},
    }
    if por_que_interessa:
        properties["por que isso interessa?"] = {
            "rich_text": [{"text": {"content": por_que_interessa}}]
        }

    try:
        notion.pages.create(parent={"database_id": DATABASE_ENTRADA}, properties=properties)
        return True
    except Exception as e:
        print(f"    ✗ Erro ao criar no Notion: {e}")
        return False


# ─── Processador de RSS ───────────────────────────────────────────────────────

def processar_fonte_rss(fonte: dict, criados_por_categoria: dict,
                         relaxar_keywords: bool = False) -> int:
    print(f"\n📡 {fonte['nome']}" + (" [2ª passagem]" if relaxar_keywords else ""))
    criados = 0
    cat = fonte["categoria"]

    try:
        feed = feedparser.parse(fonte["url"])
        if feed.bozo and not feed.entries:
            print(f"  ⚠ Feed inacessível: {fonte['url']}")
            return 0

        print(f"  → {len(feed.entries)} artigo(s) no feed")
        keywords  = [] if relaxar_keywords else fonte["keywords"]
        blacklist = fonte.get("keywords_excluir", [])

        for entry in feed.entries:
            titulo    = entry.get("title", "")
            url       = entry.get("link", "")
            descricao = entry.get("summary", "") or entry.get("description", "")

            if not url:
                continue
            if not dentro_da_janela(entry):
                continue
            if contem_blacklist(titulo, descricao, blacklist):
                continue
            if not contem_keyword(titulo, descricao, keywords):
                continue
            if url_ja_existe_no_notion(url):
                print(f"  ↩ Duplicado: {titulo[:60]}")
                continue

            ok = criar_rascunho_notion(titulo, url, descricao, fonte["nome"], fonte)
            if ok:
                criados += 1
                criados_por_categoria[cat] = criados_por_categoria.get(cat, 0) + 1
                print(f"  ✓ [{cat}: {criados_por_categoria[cat]}] {titulo[:65]}")

    except Exception as e:
        print(f"  ✗ Erro ao processar feed: {e}")

    return criados


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    data_hoje = datetime.now().strftime('%d/%m/%Y')
    print(f"\n🔍 CAMADA 1 — Por Dentro ({data_hoje})")
    print(f"   Janela: últimos {JANELA_DIAS} dias | Mínimo por categoria: {MINIMO_POR_CATEGORIA}\n")

    total_criados = 0
    criados_por_categoria = {}

    # ── 1ª passagem: filtros completos (keywords + blacklist) ─────────────────
    print("── Passagem 1: filtros por audiência ──")
    for fonte in FONTES_RSS:
        qtd = processar_fonte_rss(fonte, criados_por_categoria)
        total_criados += qtd

    # ── 2ª passagem: reforça categorias abaixo do mínimo (sem keyword filter) ─
    todas_categorias = set(f["categoria"] for f in FONTES_RSS)
    abaixo = [c for c in todas_categorias if criados_por_categoria.get(c, 0) < MINIMO_POR_CATEGORIA]

    if abaixo:
        print(f"\n── Passagem 2: reforçando {abaixo} ──")
        fontes_por_cat = {}
        for f in FONTES_RSS:
            fontes_por_cat.setdefault(f["categoria"], []).append(f)

        for cat in abaixo:
            faltam = MINIMO_POR_CATEGORIA - criados_por_categoria.get(cat, 0)
            print(f"\n  🔄 '{cat}': {criados_por_categoria.get(cat, 0)} → buscando mais {faltam}")
            for fonte in fontes_por_cat.get(cat, []):
                if criados_por_categoria.get(cat, 0) >= MINIMO_POR_CATEGORIA:
                    break
                qtd = processar_fonte_rss(fonte, criados_por_categoria, relaxar_keywords=True)
                total_criados += qtd

    # ── Resumo ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*55}")
    print(f"✅ Camada 1 concluída: {total_criados} rascunho(s) criado(s) no Notion")
    for cat in sorted(todas_categorias):
        n = criados_por_categoria.get(cat, 0)
        flag = "✓" if n >= MINIMO_POR_CATEGORIA else "⚠ abaixo do mínimo"
        print(f"   {flag} {cat}: {n}")
    print(f"   Próximo: Camada 2 na segunda-feira estrutura com Claude")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    main()
