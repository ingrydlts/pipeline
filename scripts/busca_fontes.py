"""
CAMADA 1 — Por Dentro Content Pipeline
Busca automática em fontes RSS → filtra por audiência → cria rascunhos no Notion

Zero dependência de IA neste script: 100% Python nativo.
Custo de API: apenas Notion (incluso no plano gratuito).

Roda toda sexta-feira às 7h UTC (via GitHub Actions).
"""

import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import feedparser
from notion_client import Client

# ─── Cliente Notion ───────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ─── Parâmetros globais ───────────────────────────────────────────────────────
JANELA_DIAS          = 8
MINIMO_POR_CATEGORIA = 5

# ─── Whitelist de domínios confiáveis ────────────────────────────────────────
DOMINIOS_VALIDADOS = [
    # Governo francês
    "service-public.fr", "legifrance.gouv.fr", "interieur.gouv.fr",
    "impots.gouv.fr", "francetravail.fr", "moncompteformation.gouv.fr",
    "france-education-international.fr", "insee.fr", "education.gouv.fr",
    # Seguridade e saúde
    "caf.fr", "ameli.fr", "urssaf.fr",
    # Educação e carreira
    "campusfrance.org", "apec.fr", "letudiant.fr",
    # Integração e direitos
    "ciup.fr", "lacimade.org", "singafrance.com", "gisti.org",
    "qualitefle.fr", "fle.fr",
    # Guias e aides sociais para estrangeiros
    "studyinfrance.org", "mes-allocs.fr", "solinum.org",
    # Finanças
    "boursedirect.fr", "boursobank.com", "cafedelabourse.com", "bcb.gov.br",
    "lesechos.fr",
    # Governo brasileiro
    "gov.br",
    # Grande mídia francesa
    "francetvinfo.fr", "20minutes.fr", "leparisien.fr", "bfmtv.com",
    "lemonde.fr", "lefigaro.fr", "liberation.fr",
    # Expat em inglês
    "thelocal.fr",
    # Google News (redirect — validação recai sobre entry.source.href)
    "news.google.com",
]

# ─── Filtro de nicho migratório (contexto cruzado obrigatório) ────────────────
FILTRO_NICHO_MIGRATORIO = [
    "étranger", "étrangère", "étrangers", "immigré", "immigrée",
    "immigration", "séjour", "brésil", "brésilien", "brésilienne",
    "titre", "expatrié", "expatriée", "visa", "ressortissant",
    "naturalisation", "régularisation", "non-résident",
]

# ─── Mapeamento de formato por score de conversão ─────────────────────────────
FORMATO_POR_SCORE = {
    5: "🔴 URGENTE — Publicar esta semana. Reel (<60s) com gancho de 3s + Carrossel de salvamento.",
    4: "🟠 PRIORITÁRIO — Publicar em até 10 dias. Carrossel 6-8 slides OU Reel de 90s.",
    3: "🟡 PLANEJADO — Próximo ciclo editorial. Carrossel 4-6 slides.",
    2: "🟢 BANCO DE PAUTAS — Sem urgência. Carrossel evergreen, pode batchar com outros.",
    1: "⚪ REFERÊNCIA — Não publicar solo. Usar como dado de suporte em outro conteúdo.",
}

# ─── Checklist editorial por categoria ───────────────────────────────────────
CHECKLIST_POR_CATEGORIA = {
    "burocratica": [
        "□ Qual é o PASSO 1 concreto? Deixar claro no slide de abertura",
        "□ Quais documentos são necessários? Listar todos em slide próprio",
        "□ Existe prazo ou data-limite? Destacar visualmente se sim",
        "□ Qual é o erro mais comum que brasileiras cometem nessa etapa?",
        "□ A plataforma (ANEF, Ameli, CAF) mudou recentemente? Mencionar",
    ],
    "juridica": [
        "□ O que mudou exatamente? (lei nova versus lei anterior)",
        "□ Quem é afetado: qual tipo de visto ou situação regularizada?",
        "□ Há prazo de entrada em vigor ou de adaptação?",
        "□ O que acontece na prática se ignorar? (consequência objetiva)",
        "□ Existem exceções relevantes? (étudiant vs salarié vs passeport talent)",
    ],
    "academica": [
        "□ Como isso difere do equivalente no Brasil? (ancoragem cultural)",
        "□ Existe plataforma ou site oficial para acessar o benefício?",
        "□ Há condição de elegibilidade específica para estrangeiras?",
        "□ Qual é o prazo ou janela temporal para agir?",
        "□ Quem já usou pode deixar depoimento nos comentários? (CTA)",
    ],
    "financas": [
        "□ Qual é o número concreto? (taxa, prazo, limite em €)",
        "□ O que isso representa em reais? (fazer a conversão)",
        "□ Existe risco ou armadilha que brasileiras precisam evitar?",
        "□ Como começar em 3 passos simples para quem nunca investiu?",
        "□ Qual instituição ou plataforma é a porta de entrada prática?",
    ],
    "civica": [
        "□ Onde fica exatamente? (endereço, arrondissement, URL oficial)",
        "□ É gratuito? Tem lista de espera ou processo seletivo?",
        "□ Público-alvo: estudante, trabalhadora, qualquer imigrante?",
        "□ Como entrar em contato ou se inscrever? (CTA direto)",
        "□ Chamar audiência nos comentários: 'Você já conhecia este espaço?'",
    ],
}

# ─── Mapeamento para os nomes exatos das opções no Notion ────────────────────
# O Notion é case-sensitive: "Rascunho" ≠ "rascunho"
_URGENCIA_NOTION  = {"alta": "Alta", "media": "media", "baixa": "Baixa"}
_CATEGORIA_NOTION = {"burocratica": "Burocratica", "civica": "Civica"}  # outros já batem

# ─── FONTES RSS ───────────────────────────────────────────────────────────────
# score_conversao: 1-5 baseado no potencial de salvamento/compartilhamento Instagram
# 5 = publicar esta semana | 1 = banco de referência
# URLs Google News usam queries curtas (2-4 termos) para garantir retorno de artigos

FONTES_RSS = [

    # ═══════════════════════════════════════════════════════════════════════════
    # ACADÊMICA — Estudos, Mercado Corporativo e Carreira
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Attestation Comparabilité ENIC-NARIC",
        "url": "https://news.google.com/rss/search?q=attestation+comparabilit%C3%A9+dipl%C3%B4me+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "attestation de comparabilité", "ENIC-NARIC", "diplôme étranger",
            "reconnaissance", "équivalence", "validation", "VAE",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — CPF Formation Étranger",
        "url": "https://news.google.com/rss/search?q=CPF+formation+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "compte personnel de formation", "CPF",
            "formation professionnelle", "certification", "financement",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Recrutement Cadres Étranger France",
        "url": "https://news.google.com/rss/search?q=recrutement+cadres+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "recrutement cadres", "emploi", "cadres", "étranger",
            "qualification", "contrat CDI", "APEC",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Micro-entreprise URSSAF Étranger",
        "url": "https://news.google.com/rss/search?q=micro-entreprise+URSSAF+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "micro-entreprise", "URSSAF", "indépendant", "freelance", "cotisations",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Auto-entrepreneur Étranger France",
        "url": "https://news.google.com/rss/search?q=auto-entrepreneur+%C3%A9tranger+France+s%C3%A9jour&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "auto-entrepreneur", "étranger", "titre de séjour",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Salaire Cadres Étranger France",
        "url": "https://news.google.com/rss/search?q=salaire+cadres+%C3%A9tranger+France+r%C3%A9mun%C3%A9ration&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "keywords": [
            "salaire cadres", "rémunération", "étranger", "APEC",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — France Travail Aide Création Étranger",
        "url": "https://news.google.com/rss/search?q=%22France+Travail%22+aide+cr%C3%A9ation+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "France Travail", "ARE", "ARCE", "étranger", "création",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Bourses Étudiants Étrangers France",
        "url": "https://news.google.com/rss/search?q=bourses+%C3%A9tudiants+%C3%A9trangers+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "bourses", "étudiants étrangers", "Campus France",
            "bourse", "master", "étudiant étranger",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "L'Étudiant — Alternance, CPF & Diplôme Étranger",
        "url": "https://www.letudiant.fr/rss.xml",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "alternance", "apprentissage", "étranger", "international",
            "Campus France", "visa étudiant", "auto-entrepreneur",
            "CPF", "diplôme étranger", "reconnaissance",
        ],
        "keywords_excluir": [
            "bac", "terminale", "lycée", "parcoursup", "brevet",
            "collège", "primaire", "Nobel", "classement",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # BUROCRÁTICA — Passos práticos que P02 executa toda semana
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Sécurité Sociale Étranger Ameli",
        "url": "https://news.google.com/rss/search?q=s%C3%A9curit%C3%A9+sociale+%C3%A9tranger+Ameli+num%C3%A9ro&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "sécurité sociale", "étranger", "Ameli", "numéro",
            "CLEISS", "provisoire", "définitif",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — APL CAF Étranger Logement",
        "url": "https://news.google.com/rss/search?q=APL+CAF+%C3%A9tranger+logement&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "APL", "CAF", "étranger", "logement", "allocation",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — CAF Ressources Allocation Étranger",
        "url": "https://news.google.com/rss/search?q=CAF+ressources+allocation+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "CAF", "ressources", "allocation", "étranger", "barème",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Titre Séjour ANEF Renouvellement",
        "url": "https://news.google.com/rss/search?q=titre+s%C3%A9jour+ANEF+renouvellement+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "ANEF", "titre de séjour", "renouvellement", "récépissé",
            "carte de séjour", "délai",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "Google News — Carte Vitale Étranger Ameli",
        "url": "https://news.google.com/rss/search?q=carte+vitale+%C3%A9tranger+Ameli+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "carte vitale", "étranger", "Ameli",
            "assurance maladie", "remboursement",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Prélèvement Source Non-Résident Étranger",
        "url": "https://news.google.com/rss/search?q=pr%C3%A9l%C3%A8vement+source+non-r%C3%A9sident+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "prélèvement à la source", "non-résident", "étranger", "taux",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Non-Résident Impôts France Étranger",
        "url": "https://news.google.com/rss/search?q=non-r%C3%A9sident+imp%C3%B4ts+France+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 4,
        "keywords": [
            "non-résident", "impôts", "étranger", "résident fiscal",
            "avis d'imposition",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Permis Conduire Étranger France Échange",
        "url": "https://news.google.com/rss/search?q=permis+conduire+%C3%A9tranger+France+%C3%A9change&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "permis de conduire", "étranger", "échange",
            "préfecture",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "The Local France — Expat Admin & Practical Guide (EN)",
        "url": "https://www.thelocal.fr/feed/",
        "categoria": "burocratica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 2,
        "keywords": [
            "visa", "residency", "work permit", "expat", "foreigner",
            "tax", "health insurance", "titre de séjour",
            "carte vitale", "CAF", "housing benefit",
            "social security", "driving licence",
        ],
        "keywords_excluir": ["refugee", "asylum", "Mediterranean"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # JURÍDICA — Mudanças de vistos e leis
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Changement Statut Étudiant Salarié",
        "url": "https://news.google.com/rss/search?q=changement+statut+%C3%A9tudiant+salari%C3%A9+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "changement de statut", "autorisation de travail",
            "visa travail", "titre de séjour salarié",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },
    {
        "nome": "Google News — Régularisation Travail Étranger France",
        "url": "https://news.google.com/rss/search?q=r%C3%A9gularisation+travail+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "régularisation", "travail", "étranger",
            "métier en tension", "titre de séjour",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "Google News — Naturalisation Française Étranger Conditions",
        "url": "https://news.google.com/rss/search?q=naturalisation+fran%C3%A7aise+%C3%A9tranger+conditions&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "naturalisation", "étranger", "conditions", "délai",
            "ressortissant",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "Google News — Immigration France Étranger Loi Séjour",
        "url": "https://news.google.com/rss/search?q=immigration+France+%C3%A9tranger+loi+s%C3%A9jour&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P04"],
        "urgencia": "alta",
        "score_conversao": 4,
        "keywords": [
            "immigration", "étranger", "séjour", "loi",
            "politique migratoire",
        ],
        "keywords_excluir": ["réfugié", "asile", "Frontex", "Méditerranée", "naufrage"],
    },
    {
        "nome": "Google News — Titre Séjour Réforme France Étranger",
        "url": "https://news.google.com/rss/search?q=titre+s%C3%A9jour+r%C3%A9forme+France+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 4,
        "keywords": [
            "titre de séjour", "réforme", "ANEF",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "GISTI — Droits des Étrangers Légaux (curated)",
        "url": "https://www.gisti.org/spip.php?page=backend",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 3,
        "keywords": [],  # feed 100% especializado — aceita tudo
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Droits Étrangers France Législation",
        "url": "https://news.google.com/rss/search?q=droits+%C3%A9trangers+France+l%C3%A9gislation&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "droits", "étrangers", "législation", "CESEDA",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },
    {
        "nome": "Google News — VLS-TS OFII Visa Séjour Étranger",
        "url": "https://news.google.com/rss/search?q=VLS-TS+OFII+visa+s%C3%A9jour+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "VLS-TS", "OFII", "visa long séjour", "validation",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FINANÇAS — Inteligência Financeira Bi-Nacional (Euros × Reais)
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Fiscalité Étranger Brésil France",
        "url": "https://news.google.com/rss/search?q=fiscalit%C3%A9+%C3%A9tranger+Br%C3%A9sil+France+imp%C3%B4t&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "fiscalité", "étranger", "Brésil", "convention fiscale",
            "non-résident", "expatrié",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },
    {
        "nome": "Google News — Sortie Définitive Brésil Fiscal Non-Résident",
        "url": "https://news.google.com/rss/search?q=sortie+d%C3%A9finitive+Br%C3%A9sil+fiscal+non-r%C3%A9sident&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "sortie définitive", "Brésil", "non-résident", "fiscal",
            "Receita Federal",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — PEA Étranger France Résident Épargne",
        "url": "https://news.google.com/rss/search?q=PEA+%C3%A9tranger+France+r%C3%A9sident+%C3%A9pargne&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "PEA", "étranger", "résident fiscal", "épargne",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },
    {
        "nome": "Google News — Assurance Vie Étranger France Résident",
        "url": "https://news.google.com/rss/search?q=assurance+vie+%C3%A9tranger+France+r%C3%A9sident&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "assurance-vie", "étranger", "résident", "patrimoine", "expatrié",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },
    {
        "nome": "Café de la Bourse — PEA, Assurance Vie & Épargne Résidents",
        "url": "https://www.cafedelabourse.com/feed",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "keywords": [
            "PEA", "assurance vie", "épargne", "investissement", "fiscalité",
            "patrimoine", "livret A", "placement", "étranger", "expatrié", "résident",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "dividende", "géopolitique"],
    },
    {
        "nome": "Google News — INSEE Inflation France Étranger",
        "url": "https://news.google.com/rss/search?q=INSEE+inflation+France+co%C3%BBt+vie+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P01", "P03"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "keywords": [
            "INSEE", "inflation", "coût de la vie",
        ],
        "keywords_excluir": ["CAC 40", "actionnaire"],
    },
    {
        "nome": "Google News — Euro Real Brésil Change Transfert",
        "url": "https://news.google.com/rss/search?q=euro+real+Br%C3%A9sil+change+transfert&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "euro", "real", "Brésil", "transfert", "change",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Transfert Brésil Euros Virement International",
        "url": "https://news.google.com/rss/search?q=transfert+Br%C3%A9sil+euros+virement+international&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "transfert", "Brésil", "virement", "international",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Placements Épargne Étranger France",
        "url": "https://news.google.com/rss/search?q=placements+%C3%A9pargne+%C3%A9tranger+France+r%C3%A9sident&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "placements", "épargne", "étranger", "résident fiscal",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CÍVICA — Redes de Apoio & Networking
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Cité Internationale Paris Étranger Logement",
        "url": "https://news.google.com/rss/search?q=Cit%C3%A9+internationale+Paris+%C3%A9tranger+logement&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "Cité Internationale Universitaire", "CIUP",
            "étranger", "logement", "réseau",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Singa France Étranger Intégration",
        "url": "https://news.google.com/rss/search?q=Singa+France+%C3%A9tranger+int%C3%A9gration&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "Singa", "étranger", "intégration", "réseau",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Français Langue Étrangère Cours Étranger",
        "url": "https://news.google.com/rss/search?q=%22fran%C3%A7ais+langue+%C3%A9trang%C3%A8re%22+cours+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "français langue étrangère", "cours de français",
            "étranger", "intégration",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Associations Paris Étranger Intégration",
        "url": "https://news.google.com/rss/search?q=associations+Paris+%C3%A9tranger+int%C3%A9gration&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "associations", "étranger", "intégration", "bénévolat",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "La Cimade — Accompagnement Social & Droits Étrangers",
        "url": "https://www.lacimade.org/feed/",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "accompagnement", "droits", "association",
            "soutien", "intégration", "aide juridique", "réseau",
        ],
        "keywords_excluir": [
            "réfugié", "asile", "demandeur d'asile", "sans-papiers",
            "Méditerranée", "Frontex", "traversée", "naufrage",
        ],
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def normalizar_texto(texto: str) -> str:
    texto = re.sub(r'<[^>]+>', ' ', texto or '')
    return re.sub(r'\s+', ' ', texto).strip()


def extrair_dominio_para_whitelist(entry) -> str:
    """
    Extrai o domínio relevante para checar contra DOMINIOS_VALIDADOS.
    Prioriza entry.source.href (publisher real) sobre o redirect do Google News.
    """
    try:
        source_href = entry.get('source', {}).get('href', '')
        if source_href and 'google.com' not in source_href:
            return urlparse(source_href).netloc.lstrip('www.')
    except Exception:
        pass
    link = entry.get('link', '')
    return urlparse(link).netloc.lstrip('www.') if link else ''


def url_em_whitelist(entry) -> bool:
    """Descarta artigos de domínios não validados."""
    dominio = extrair_dominio_para_whitelist(entry)
    if not dominio:
        return False
    return any(d in dominio for d in DOMINIOS_VALIDADOS)


def contem_nicho_migratorio(titulo: str, descricao: str) -> bool:
    """
    Filtro cruzado de contexto: o artigo só é aceito se contiver ao menos
    um termo do FILTRO_NICHO_MIGRATORIO no título ou no resumo.
    """
    texto = (titulo + ' ' + descricao).lower()
    return any(termo in texto for termo in FILTRO_NICHO_MIGRATORIO)


def contem_keyword(titulo: str, descricao: str, keywords: list) -> bool:
    """Retorna True se ao menos UMA keyword aparecer. Lista vazia = aceita tudo."""
    if not keywords:
        return True
    texto = (titulo + ' ' + descricao).lower()
    return any(kw.lower() in texto for kw in keywords)


def contem_blacklist(titulo: str, descricao: str, blacklist: list) -> bool:
    """Retorna True se algum termo da blacklist aparecer — artigo descartado."""
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


def gerar_template_editorial(titulo: str, descricao: str, fonte: dict) -> str:
    """
    Gera um template editorial em Python nativo — zero custo de API.
    Combina metadados da fonte com os dados brutos da notícia.
    """
    cat       = fonte["categoria"]
    score     = fonte.get("score_conversao", 3)
    personas  = " | ".join(fonte["personas"])
    urgencia  = fonte.get("urgencia", "media").upper()
    formato   = FORMATO_POR_SCORE.get(score, FORMATO_POR_SCORE[3])
    checklist = CHECKLIST_POR_CATEGORIA.get(cat, [])

    linhas = [
        f"📋 TEMPLATE — {cat.upper()} | Score: {score}/5 | Urgência: {urgencia}",
        f"🎯 Personas: {personas}",
        f"📌 Formato sugerido: {formato}",
        "",
        "✅ CHECKLIST DE CURADORIA:",
    ]
    linhas.extend(checklist)
    linhas.append("")
    linhas.append(f"📰 Título capturado: {titulo[:120]}")

    desc_curta = (descricao[:200] + "…") if len(descricao) > 200 else descricao
    if desc_curta:
        linhas.append(f"📝 Resumo bruto: {desc_curta}")

    return "\n".join(linhas)


# ─── Notion ───────────────────────────────────────────────────────────────────

def garantir_propriedades_notion():
    """
    Cria automaticamente 'Score Conversão' (Number) e 'Template Editorial' (rich_text)
    no database Notion, caso ainda não existam. Idempotente.
    """
    try:
        notion.databases.update(
            database_id=DATABASE_ENTRADA,
            properties={
                "Score Conversão":    {"number": {}},
                "Template Editorial": {"rich_text": {}},
            }
        )
    except Exception as e:
        print(f"  ⚠ Não foi possível auto-criar propriedades no Notion: {e}")


def criar_rascunho_notion(titulo: str, url: str, descricao: str,
                           fonte_nome: str, fonte: dict) -> bool:
    titulo_limpo     = normalizar_texto(titulo)[:200]
    descricao_limpa  = normalizar_texto(descricao)[:500]
    template         = gerar_template_editorial(titulo_limpo, descricao_limpa, fonte)
    score            = fonte.get("score_conversao", 3)

    # Normaliza para os valores exatos das opções no Notion (case-sensitive)
    urgencia_val  = _URGENCIA_NOTION.get(fonte["urgencia"], fonte["urgencia"])
    categoria_val = _CATEGORIA_NOTION.get(fonte["categoria"], fonte["categoria"])

    properties = {
        "Título":             {"title":        [{"text": {"content": titulo_limpo}}]},
        "Categoria":          {"multi_select": [{"name": categoria_val}]},
        "Persona":            {"multi_select": [{"name": p} for p in fonte["personas"]]},
        "Urgência":           {"select":        {"name": urgencia_val}},
        "Fonte":              {"url": url},
        "Notas":              {"rich_text":    [{"text": {"content": descricao_limpa}}]},
        "Status":             {"select":        {"name": "Rascunho"}},
        "Score Conversão":    {"number": score},
        "Template Editorial": {"rich_text":    [{"text": {"content": template[:1990]}}]},
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
    cat     = fonte["categoria"]

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
            if not url_em_whitelist(entry):
                continue
            if contem_blacklist(titulo, descricao, blacklist):
                continue
            if not contem_keyword(titulo, descricao, keywords):
                continue
            if not contem_nicho_migratorio(titulo, descricao):
                continue
            if url_ja_existe_no_notion(url):
                print(f"  ↩ Duplicado: {titulo[:60]}")
                continue

            ok = criar_rascunho_notion(titulo, url, descricao, fonte["nome"], fonte)
            if ok:
                criados += 1
                criados_por_categoria[cat] = criados_por_categoria.get(cat, 0) + 1
                print(f"  ✓ [score {fonte['score_conversao']}] [{cat}: {criados_por_categoria[cat]}] {titulo[:60]}")

    except Exception as e:
        print(f"  ✗ Erro ao processar feed: {e}")

    return criados


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    data_hoje = datetime.now().strftime('%d/%m/%Y')
    print(f"\n🔍 CAMADA 1 — Por Dentro ({data_hoje})")
    print(f"   {len(FONTES_RSS)} fontes | {len(DOMINIOS_VALIDADOS)} domínios na whitelist")
    print(f"   Janela: {JANELA_DIAS} dias | Mínimo/categoria: {MINIMO_POR_CATEGORIA}\n")

    garantir_propriedades_notion()

    total_criados        = 0
    criados_por_categoria = {}

    # ── 1ª passagem: todos os filtros ativos ──────────────────────────────────
    print("── Passagem 1: filtros completos (whitelist + nicho + keywords) ──")
    for fonte in FONTES_RSS:
        qtd = processar_fonte_rss(fonte, criados_por_categoria)
        total_criados += qtd

    # ── 2ª passagem: relaxa keywords para categorias abaixo do mínimo ─────────
    todas_categorias = set(f["categoria"] for f in FONTES_RSS)
    abaixo = [c for c in todas_categorias
              if criados_por_categoria.get(c, 0) < MINIMO_POR_CATEGORIA]

    if abaixo:
        print(f"\n── Passagem 2: reforçando {abaixo} (keywords relaxadas) ──")
        fontes_por_cat = {}
        for f in FONTES_RSS:
            fontes_por_cat.setdefault(f["categoria"], []).append(f)

        for cat in abaixo:
            atual = criados_por_categoria.get(cat, 0)
            print(f"\n  🔄 '{cat}': {atual} → buscando sem filtro de keyword")
            for fonte in fontes_por_cat.get(cat, []):
                if criados_por_categoria.get(cat, 0) >= MINIMO_POR_CATEGORIA:
                    break
                qtd = processar_fonte_rss(fonte, criados_por_categoria,
                                           relaxar_keywords=True)
                total_criados += qtd

    # ── Resumo ────────────────────────────────────────────────────────────────
    print(f"\n{'═' * 62}")
    print(f"✅ Camada 1 concluída: {total_criados} rascunho(s) criado(s) no Notion")
    for cat in sorted(todas_categorias):
        n    = criados_por_categoria.get(cat, 0)
        flag = "✓" if n >= MINIMO_POR_CATEGORIA else "⚠ abaixo do mínimo"
        print(f"   {flag}  {cat}: {n}")
    print("   Próximo: curadoria manual → Camada 2 estrutura com Claude")
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
