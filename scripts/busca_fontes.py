"""
CAMADA 1 — Por Dentro Content Pipeline
Busca automática em fontes RSS → filtra por audiência → cria rascunhos no Notion

Zero dependência de IA neste script: 100% Python nativo.
Custo de API: apenas Notion (incluso no plano gratuito).

Roda toda sexta-feira às 7h UTC (via GitHub Actions).

PRÉ-REQUISITO NOTION:
Adicione ao database "Rascunhos" as propriedades:
  - "Score Conversão"   → tipo: Number
  - "Template Editorial" → tipo: Text (rich_text)
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
# Artigos de domínios fora desta lista são descartados automaticamente.
DOMINIOS_VALIDADOS = [
    # Governo francês
    "service-public.fr", "legifrance.gouv.fr", "interieur.gouv.fr",
    "impots.gouv.fr", "francetravail.fr", "moncompteformation.gouv.fr",
    "france-education-international.fr", "insee.fr",
    # Seguridade e saúde
    "caf.fr", "ameli.fr", "urssaf.fr",
    # Educação e carreira
    "campusfrance.org", "apec.fr", "letudiant.fr",
    # Integração e direitos
    "ciup.fr", "lacimade.org", "singafrance.com", "gisti.org",
    "qualitefle.fr", "fle.fr",
    # Finanças
    "boursedirect.fr", "boursobank.com", "cafedelabourse.com", "bcb.gov.br",
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
# O artigo só é aceito se tiver ao menos 1 destes termos no título ou resumo.
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

# ─── FONTES RSS ───────────────────────────────────────────────────────────────
# score_conversao: 1-5 baseado no potencial de salvamento/compartilhamento Instagram
# 5 = publicar esta semana | 1 = banco de referência

FONTES_RSS = [

    # ═══════════════════════════════════════════════════════════════════════════
    # ACADÊMICA — Estudos, Mercado Corporativo e Carreira
    # P01 (em pesquisa), P03 (transição para cargos qualificados)
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Attestation Comparabilité ENIC-NARIC Diplôme",
        "url": "https://news.google.com/rss/search?q=%22attestation+de+comparabilit%C3%A9%22+ENIC-NARIC+dipl%C3%B4me+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "attestation de comparabilité", "ENIC-NARIC", "diplôme étranger",
            "reconnaissance", "équivalence", "comparabilité", "validation", "VAE",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Compte Personnel Formation CPF Droits Salarié",
        "url": "https://news.google.com/rss/search?q=%22compte+personnel+de+formation+%28CPF%29+droits%22+salar%C3%A9+%C3%A9tranger+certification&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "compte personnel de formation CPF", "CPF",
            "formation professionnelle", "salarié", "certification", "financement",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Recrutement Cadres Paris Marché du Travail",
        "url": "https://news.google.com/rss/search?q=%22recrutement+cadres+Paris%22+OR+%22march%C3%A9+du+travail+cadres%22+%C3%A9tranger+emploi&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "recrutement cadres Paris", "marché du travail cadres",
            "emploi", "cadres", "étranger", "qualification", "contrat CDI", "APEC",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Création Micro-entreprise URSSAF Étranger",
        "url": "https://news.google.com/rss/search?q=%22cr%C3%A9ation+micro-entreprise%22+URSSAF+freelance+France+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "création micro-entreprise URSSAF", "URSSAF", "micro-entreprise",
            "indépendant", "freelance", "cotisations",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Auto-entrepreneur Étranger Démarches France",
        "url": "https://news.google.com/rss/search?q=%22auto-entrepreneur+%C3%A9tranger+d%C3%A9marches%22+France+titre+s%C3%A9jour&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "auto-entrepreneur étranger démarches", "auto-entrepreneur étranger",
            "auto-entrepreneur", "étranger", "titre de séjour travail",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Grille de Salaire Cadres France Rémunération",
        "url": "https://news.google.com/rss/search?q=%22grille+de+salaire+cadres%22+France+r%C3%A9mun%C3%A9ration+statistiques&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "keywords": [
            "grille de salaire cadres France", "rémunération", "salaire cadres",
            "statistiques", "APEC",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Aide Création Entreprise France Travail ARE",
        "url": "https://news.google.com/rss/search?q=%22aide+%C3%A0+la+cr%C3%A9ation+d%27entreprise%22+%22France+Travail%22+%C3%A9tranger+ARE&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "aide à la création d'entreprise France Travail", "ARE", "ARCE",
            "étranger", "France Travail",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Bourses Étudiants Étrangers Campus France Brésil",
        "url": "https://news.google.com/rss/search?q=%22bourses+d%27%C3%A9tudes+%C3%A9tudiants+%C3%A9trangers%22+France+%22Campus+France%22+br%C3%A9sil&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "bourses d'études étudiants étrangers France", "Campus France",
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
            "contrat d'apprentissage", "CFA", "Campus France", "visa étudiant",
            "auto-entrepreneur", "CPF", "diplôme étranger", "reconnaissance",
        ],
        "keywords_excluir": [
            "bac", "terminale", "lycée", "parcoursup", "brevet",
            "collège", "primaire", "Nobel", "classement",
        ],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # BUROCRÁTICA — Passos práticos que P02 executa toda semana
    # Gerador principal de salvamentos no Instagram
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Numéro Sécurité Sociale Définitif Étranger",
        "url": "https://news.google.com/rss/search?q=%22num%C3%A9ro+de+s%C3%A9curit%C3%A9+sociale+d%C3%A9finitif%22+%C3%A9tranger+France+Ameli+proc%C3%A9dure&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "numéro de sécurité sociale définitif", "numéro de sécurité sociale",
            "étranger", "Ameli", "CLEISS", "sécurité sociale", "provisoire",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Demande APL CAF Allocation Logement Étranger",
        "url": "https://news.google.com/rss/search?q=%22demande+APL+CAF%22+allocation+logement+%C3%A9tranger+montant+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "demande APL CAF", "APL", "allocation logement",
            "CAF", "étranger", "loyer", "montant",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Plafond CAF Ressources Barème Allocations",
        "url": "https://news.google.com/rss/search?q=%22plafond+CAF+ressources%22+allocation+bar%C3%A8me+revenu+%C3%A9ligibilit%C3%A9&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "plafond CAF ressources", "plafond", "barème",
            "allocation", "CAF", "revenus", "éligibilité",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Renouvellement Titre de Séjour ANEF Préfecture",
        "url": "https://news.google.com/rss/search?q=%22renouvellement+titre+de+s%C3%A9jour+ANEF%22+pr%C3%A9fecture+d%C3%A9lai+bug+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "renouvellement titre de séjour ANEF", "ANEF", "préfecture",
            "récépissé", "carte de séjour", "délai", "téléservice",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "Google News — Carte Vitale Dossier Ameli Étranger",
        "url": "https://news.google.com/rss/search?q=%22carte+vitale+dossier+ameli%22+OR+%22carte+vitale+%C3%A9tranger%22+remboursement+proc%C3%A9dure&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "carte vitale dossier Ameli", "carte vitale étranger",
            "Ameli", "assurance maladie", "remboursement", "mutuelle",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Prélèvement à la Source Impôts Salarié Étranger",
        "url": "https://news.google.com/rss/search?q=%22pr%C3%A9l%C3%A8vement+%C3%A0+la+source%22+imp%C3%B4ts+salari%C3%A9+%C3%A9tranger+taux+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "prélèvement à la source impôts", "prélèvement à la source",
            "taux", "salaire net", "non-résident", "étranger",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Déclaration Impôts Non-Résident France",
        "url": "https://news.google.com/rss/search?q=%22d%C3%A9claration+d%27imp%C3%B4ts+non-r%C3%A9sident+France%22+%C3%A9tranger+fiscal+impots.gouv&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 4,
        "keywords": [
            "déclaration d'impôts non-résident France", "non-résident",
            "résident fiscal", "impots.gouv", "avis d'imposition", "étranger",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Échange Permis de Conduire Étranger France",
        "url": "https://news.google.com/rss/search?q=%22%C3%A9change+de+permis+de+conduire+%C3%A9tranger%22+France+proc%C3%A9dure+d%C3%A9lai+Br%C3%A9sil&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "échange de permis de conduire étranger", "permis de conduire étranger",
            "procédure", "délai", "Brésil", "préfecture",
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
            "tax declaration", "health insurance", "titre de séjour",
            "carte vitale", "CAF", "housing benefit",
            "social security", "bank account", "driving licence exchange",
        ],
        "keywords_excluir": ["refugee", "asylum", "Mediterranean"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # JURÍDICA — Mudanças de vistos e leis
    # Alto CTR e compartilhamento em alcance frio (P01) e análise profunda (P04)
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Changement de Statut Étudiant à Salarié",
        "url": "https://news.google.com/rss/search?q=%22changement+de+statut+%C3%A9tudiant+%C3%A0+salari%C3%A9%22+France+proc%C3%A9dure+autorisation+travail&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "changement de statut étudiant à salarié", "changement de statut",
            "autorisation de travail", "visa travail", "titre de séjour salarié",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },
    {
        "nome": "Google News — Régularisation par le Travail France Métiers Tension",
        "url": "https://news.google.com/rss/search?q=%22r%C3%A9gularisation+par+le+travail+France%22+m%C3%A9tier+tension+proc%C3%A9dure+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "régularisation par le travail France", "régularisation",
            "métier en tension", "étranger", "titre de séjour travail",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "Google News — Naturalisation Française Conditions Délais",
        "url": "https://news.google.com/rss/search?q=%22naturalisation+fran%C3%A7aise+conditions%22+d%C3%A9lai+crit%C3%A8res+ressortissant&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "naturalisation française conditions", "naturalisation",
            "délai", "critères", "acquisition", "ressortissant",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        "nome": "Google News — Loi Immigration France Modifications",
        "url": "https://news.google.com/rss/search?q=%22loi+immigration+France%22+%C3%A9tranger+s%C3%A9jour+r%C3%A9forme+vote&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P04"],
        "urgencia": "alta",
        "score_conversao": 4,
        "keywords": [
            "loi immigration France", "loi immigration", "étranger",
            "séjour", "réforme", "politique migratoire",
        ],
        "keywords_excluir": ["réfugié", "asile", "Frontex", "Méditerranée", "naufrage"],
    },
    {
        "nome": "Google News — Réforme Titres de Séjour Validité Émission",
        "url": "https://news.google.com/rss/search?q=%22r%C3%A9forme+des+titres+de+s%C3%A9jour%22+France+validit%C3%A9+%C3%A9mission+ANEF&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "score_conversao": 4,
        "keywords": [
            "réforme des titres de séjour", "titre de séjour",
            "validité", "modification", "ANEF",
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
        "nome": "Google News — Droits des Étrangers France Législation Analyse",
        "url": "https://news.google.com/rss/search?q=%22droits+des+%C3%A9trangers+France%22+l%C3%A9gislation+ressortissant+analyse+garanties&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "droits des étrangers France", "droits", "législation",
            "ressortissant", "garanties", "CESEDA",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },
    {
        "nome": "Google News — Visa Long Séjour VLS-TS Validation OFII",
        "url": "https://news.google.com/rss/search?q=%22visa+de+long+s%C3%A9jour+valant+titre+de+s%C3%A9jour%22+VLS-TS+validation+taxe+OFII&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "visa de long séjour valant titre de séjour", "VLS-TS",
            "validation", "taxe consulaire", "OFII",
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FINANÇAS — Inteligência Financeira Bi-Nacional (Euros × Reais)
    # P03 e P04 — patrimônio, investimento e regularidade fiscal entre dois países
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Fiscalité Compte Étranger Brésil Convention",
        "url": "https://news.google.com/rss/search?q=%22fiscalit%C3%A9+compte+%C3%A0+l%27%C3%A9tranger+Br%C3%A9sil%22+convention+imp%C3%B4t+expatri%C3%A9+patrimoine&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "fiscalité compte à l'étranger Brésil", "convention fiscale",
            "impôt", "non-résident", "expatrié", "patrimoine",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire", "dividende"],
    },
    {
        "nome": "Google News — Déclaration Sortie Définitive Brésil Fiscal",
        "url": "https://news.google.com/rss/search?q=%22d%C3%A9claration+de+sortie+d%C3%A9finitive+Br%C3%A9sil%22+OR+%22saida+definitiva%22+fiscal+non-r%C3%A9sident+Receita&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "score_conversao": 5,
        "keywords": [
            "déclaration de sortie définitive Brésil", "saída definitiva",
            "Receita Federal", "non-résident", "fiscal",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Ouverture PEA Résident France Avantages",
        "url": "https://news.google.com/rss/search?q=%22ouverture+PEA%22+r%C3%A9sident+France+%C3%A9pargne+fiscalit%C3%A9+avantage+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "ouverture PEA", "PEA", "plan d'épargne en actions",
            "résident fiscal", "épargne", "fiscalité", "étranger",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire", "dividende"],
    },
    {
        "nome": "Google News — Meilleure Assurance Vie France Résidents Patrimoine",
        "url": "https://news.google.com/rss/search?q=%22meilleure+assurance+vie+France%22+placement+r%C3%A9sident+patrimoine+succession+expatri%C3%A9&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "meilleure assurance vie France", "assurance-vie",
            "résident", "patrimoine", "succession", "expatrié",
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
        "nome": "Google News — Indice Prix Consommation INSEE Inflation France",
        "url": "https://news.google.com/rss/search?q=%22indice+des+prix+%C3%A0+la+consommation+INSEE%22+France+inflation+co%C3%BBt+logement+expatri%C3%A9&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P01", "P03"],
        "urgencia": "baixa",
        "score_conversao": 3,
        "keywords": [
            "indice des prix à la consommation INSEE", "INSEE",
            "inflation", "coût de la vie", "logement",
        ],
        "keywords_excluir": ["CAC 40", "actionnaire"],
    },
    {
        "nome": "Google News — Taux de Change Euro Real Transfert Remesse",
        "url": "https://news.google.com/rss/search?q=%22taux+de+change+euro+real%22+Br%C3%A9sil+transfert+remesse+Wise&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 4,
        "keywords": [
            "taux de change euro real", "Euro Real", "Brésil",
            "Wise", "remesse", "transfert",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Transfert de Fonds International Brésil Sécurité",
        "url": "https://news.google.com/rss/search?q=%22transfert+de+fonds+international+Br%C3%A9sil%22+frais+s%C3%A9curit%C3%A9+plateforme+virement&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "transfert de fonds international Brésil", "virement international",
            "Brésil", "frais", "sécurité",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Placements Financiers Résidents Fiscaux France",
        "url": "https://news.google.com/rss/search?q=%22placements+financiers+r%C3%A9sidents+fiscaux%22+France+%C3%A9pargne+investir+euros+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "placements financiers résidents fiscaux", "épargne",
            "résident fiscal", "livret", "patrimoine",
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CÍVICA — Redes de Apoio & Networking
    # Sentimento de pertencimento — fideliza P02 e P03
    # ═══════════════════════════════════════════════════════════════════════════

    {
        "nome": "Google News — Cité Internationale Universitaire Paris Admissions",
        "url": "https://news.google.com/rss/search?q=%22Cit%C3%A9+Internationale+Universitaire+de+Paris%22+admission+logement+r%C3%A9sidence+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "score_conversao": 3,
        "keywords": [
            "Cité Internationale Universitaire de Paris", "CIUP",
            "admission", "logement", "international", "réseau",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Réseau Professionnel Singa France Intégration",
        "url": "https://news.google.com/rss/search?q=%22Singa+France%22+r%C3%A9seau+professionnel+%C3%A9tranger+int%C3%A9gration+entrepreneuriat&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "Singa France", "réseau professionnel", "étranger",
            "intégration", "entrepreneuriat", "communauté",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Centres FLE Labellisés Français Langue Étrangère",
        "url": "https://news.google.com/rss/search?q=%22centres+FLE+labellis%C3%A9s%22+OR+%22fran%C3%A7ais+langue+%C3%A9trang%C3%A8re%22+cours+%C3%A9tranger+int%C3%A9gration+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "centres FLE labellisés", "français langue étrangère",
            "cours de français", "étranger", "intégration linguistique",
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Maisons des Associations Paris Networking",
        "url": "https://news.google.com/rss/search?q=%22Maisons+des+Associations+Paris%22+OR+%22maison+de+la+vie+associative%22+%C3%A9tranger+networking+b%C3%A9n%C3%A9volat&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "score_conversao": 2,
        "keywords": [
            "Maisons des Associations Paris", "maison de la vie associative",
            "bénévolat", "networking", "étranger", "arrondissement",
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
            "accompagnement social", "accompagnement", "droits", "association",
            "soutien", "intégration", "aide juridique", "bénévolat", "réseau",
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
    # 1. source.href — domínio do publisher (mais confiável no Google News)
    try:
        source_href = entry.get('source', {}).get('href', '')
        if source_href and 'google.com' not in source_href:
            return urlparse(source_href).netloc.lstrip('www.')
    except Exception:
        pass

    # 2. Fallback: link direto do artigo
    link = entry.get('link', '')
    return urlparse(link).netloc.lstrip('www.') if link else ''


def url_em_whitelist(entry) -> bool:
    """Descarta artigos de domínios não validados."""
    dominio = extrair_dominio_para_whitelist(entry)
    if not dominio:
        return False  # domínio indeterminado → descartar por segurança
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
    Combina metadados da fonte com os dados brutos da notícia e entrega
    um checklist de ação pronto para curadoria no Notion.
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

def criar_rascunho_notion(titulo: str, url: str, descricao: str,
                           fonte_nome: str, fonte: dict) -> bool:
    titulo_limpo     = normalizar_texto(titulo)[:200]
    descricao_limpa  = normalizar_texto(descricao)[:500]
    template         = gerar_template_editorial(titulo_limpo, descricao_limpa, fonte)
    score            = fonte.get("score_conversao", 3)

    properties = {
        "Título":             {"title":        [{"text": {"content": titulo_limpo}}]},
        "Categoria":          {"multi_select": [{"name": fonte["categoria"]}]},
        "Persona":            {"multi_select": [{"name": p} for p in fonte["personas"]]},
        "Urgência":           {"select":        {"name": fonte["urgencia"]}},
        "Fonte":              {"url": url},
        "Notas":              {"rich_text":    [{"text": {"content": descricao_limpa}}]},
        "Status":             {"select":        {"name": "rascunho"}},
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

            # ── Filtro 1: whitelist de domínios ───────────────────────────────
            if not url_em_whitelist(entry):
                continue

            # ── Filtro 2: blacklist da fonte ─────────────────────────────────
            if contem_blacklist(titulo, descricao, blacklist):
                continue

            # ── Filtro 3: keywords específicas da fonte ───────────────────────
            if not contem_keyword(titulo, descricao, keywords):
                continue

            # ── Filtro 4: contexto migratório cruzado (sempre ativo) ──────────
            if not contem_nicho_migratorio(titulo, descricao):
                continue

            # ── Filtro 5: deduplicação no Notion ─────────────────────────────
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

    total_criados        = 0
    criados_por_categoria = {}

    # ── 1ª passagem: todos os filtros ativos ──────────────────────────────────
    print("── Passagem 1: filtros completos (whitelist + nicho + keywords) ──")
    for fonte in FONTES_RSS:
        qtd = processar_fonte_rss(fonte, criados_por_categoria)
        total_criados += qtd

    # ── 2ª passagem: relaxa keywords para categorias abaixo do mínimo ─────────
    # Whitelist e filtro de nicho permanecem ativos.
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
