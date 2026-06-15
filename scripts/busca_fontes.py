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

FILOSOFIA DE KEYWORDS (persona-mapped):
  Cada keyword vem de uma dor real mapeada nas personas — não de termos genéricos.
  Filtro duplo: keywords_incluir (ao menos 1) + keywords_excluir (qualquer 1 = descarta).
  Artigos capturados = ouro bruto para Claude estruturar ganchos magnéticos.

CATEGORIAS:
  massa        → viral e cotidiano que cria gancho imediato (P01 + P04)
  juridica     → leis e direitos que afetam quem já TEM título legal (P02 + P03)
  burocratica  → passos práticos semanais: CAF, Sécu, ANEF, imposto (P02)
  academica    → carreira, diplomas, alternance, CPF (P02 + P03)
  civica       → redes de apoio, associações, língua (P02 + P03)
  financas     → dinheiro real: investir, remessas, câmbio, PEA (P03 + P04)
  trendmapping → identidade, choque cultural, saúde mental (P02 + P03)
"""

import os
import re
from datetime import datetime, timezone, timedelta
import feedparser
from notion_client import Client
import anthropic

# ─── Clientes ────────────────────────────────────────────────────────────────
notion = Client(auth=os.environ["NOTION_TOKEN"])
claude  = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
DATABASE_ENTRADA = os.environ["NOTION_DATABASE_ID"]

# ─── Contexto de audiência ───────────────────────────────────────────────────
AUDIENCIA_CONTEXT = open("scripts/audiencia_context.txt", encoding="utf-8").read()

# ─── Parâmetros globais ───────────────────────────────────────────────────────
JANELA_DIAS          = 8
MINIMO_POR_CATEGORIA = 5

# ─── FONTES RSS ───────────────────────────────────────────────────────────────

FONTES_RSS = [

    # ═══════════════════════════════════════════════════════════════════════════
    # MASSA — viral e cotidiano que cria gancho imediato
    # Foco: P01 (curiosidade) e P04 (profundidade)
    # Keywords: dores e eventos que geram pico de atenção imediata
    # ═══════════════════════════════════════════════════════════════════════════
    {
        # Grève = caos que afeta quem mora em Paris — post de rotina e sobrevivência
        "nome": "Google News — Grève RATP & SNCF Transport Paris",
        "url": "https://news.google.com/rss/search?q=gr%C3%A8ve+RATP+OR+gr%C3%A8ve+SNCF+perturbation+transport+Paris+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "massa",
        "personas": ["P01", "P04"],
        "urgencia": "alta",
        "keywords": [
            "grève RATP", "grève SNCF", "grève", "perturbation", "transport",
            "Paris", "RER", "métro", "trafic", "ligne"
        ],
        "keywords_excluir": [],
    },
    {
        # Réforme/Loi immigration = mudanças estruturais que geram pânico e audiência
        "nome": "Google News — Réforme Immigration & Loi Asile",
        "url": "https://news.google.com/rss/search?q=%22r%C3%A9forme+immigration%22+OR+%22loi+immigration%22+France+2026+%C3%A9tranger+s%C3%A9jour&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "massa",
        "personas": ["P01", "P04"],
        "urgencia": "alta",
        "keywords": [
            "réforme immigration", "loi immigration", "étranger", "séjour",
            "ressortissant", "titre", "politique migratoire", "projet de loi"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "Frontex", "Méditerranée"],
    },
    {
        # Visa Paris / Titre IDF = gargalo das prefeituras — conteúdo de utilidade urgente
        "nome": "Google News — Visa Paris & Titre de Séjour Île-de-France",
        "url": "https://news.google.com/rss/search?q=%22visa+Paris%22+OR+%22titre+de+s%C3%A9jour+%C3%8Ele-de-France%22+pr%C3%A9fecture+attente+d%C3%A9lai&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "massa",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "keywords": [
            "visa Paris", "titre de séjour Île-de-France", "préfecture",
            "attente", "délai", "rendez-vous", "dossier", "ANEF"
        ],
        "keywords_excluir": [],
    },
    {
        # Crise du logement = a maior dor de P01 antes de vir e P02 ao chegar
        "nome": "Google News — Crise du Logement & Location Paris",
        "url": "https://news.google.com/rss/search?q=%22crise+du+logement%22+OR+%22location+Paris%22+loyer+propri%C3%A9taire+%C3%A9tranger+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "massa",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "keywords": [
            "crise du logement", "location Paris", "loyer", "propriétaire",
            "garant", "logement", "hébergement", "Paris", "Île-de-France"
        ],
        "keywords_excluir": [],
    },
    {
        # Pouvoir d'achat = gancho de impacto imediato com dados INSEE
        "nome": "Google News — Pouvoir d'achat & Inflation France",
        "url": "https://news.google.com/rss/search?q=%22pouvoir+d%27achat%22+OR+%22inflation+France%22+Paris+prix+co%C3%BBt+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "massa",
        "personas": ["P01", "P03"],
        "urgencia": "media",
        "keywords": [
            "pouvoir d'achat Paris", "inflation France", "prix", "coût",
            "augmentation", "budget", "consommation", "dépenses"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "bourse"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # JURÍDICA — leis e direitos que mudam a vida de quem já TEM título legal
    # Foco: P02 e P03 | Blacklist forte para réfugiés/OQTF em media généraliste
    # Keywords diretas das dores: changement de statut, naturalisation, CDI/CDD
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "GISTI — Droit des Étrangers (curated)",
        "url": "https://www.gisti.org/spip.php?page=backend",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "keywords": [],  # feed curado — aceita tudo; GISTI é 100% sobre direitos legais
        "keywords_excluir": [],
    },
    {
        # La Cimade: foco em acompanhamento jurídico, recours e direitos — não em drama
        "nome": "La Cimade — Droits & Accompagnement Juridique",
        "url": "https://www.lacimade.org/feed/",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "titre de séjour", "séjour", "droits des étrangers", "recours",
            "naturalisation", "régularisation par le travail", "changement de statut",
            "carte de séjour", "ANEF", "contrat de travail CDD CDI", "ressortissant"
        ],
        "keywords_excluir": [
            "réfugié", "asile", "demandeur d'asile", "sans-papiers",
            "Méditerranée", "Frontex", "traversée", "barque", "naufrage",
            "Syrie", "Mali", "Afghanistan", "Libye"
        ],
    },
    {
        # Changement de statut = a transição mais comum de P03 (étudiant → salarié)
        "nome": "Google News — Changement de Statut Étudiant → Salarié",
        "url": "https://news.google.com/rss/search?q=%22changement+de+statut%22+%C3%A9tudiant+salari%C3%A9+France+visa+travail+r%C3%A9gularisation&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "keywords": [
            "changement de statut", "étudiant à salarié", "régularisation par le travail",
            "autorisation de travail", "visa travail", "titre de séjour salarié"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        # Renouvellement ANEF = dor imediata de P02 — "meu récépissé venceu"
        "nome": "Google News — Renouvellement Titre de Séjour ANEF",
        "url": "https://news.google.com/rss/search?q=%22renouvellement+titre+de+s%C3%A9jour%22+ANEF+pr%C3%A9fecture+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "keywords": [
            "renouvellement titre de séjour", "ANEF", "préfecture",
            "récépissé", "carte de séjour", "dossier", "délai"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },
    {
        # Naturalisation = sonho de longo prazo de P03 — condições e critérios
        "nome": "Google News — Naturalisation Française Conditions",
        "url": "https://news.google.com/rss/search?q=%22naturalisation+fran%C3%A7aise+conditions%22+d%C3%A9lai+ressortissant+crit%C3%A8res+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "naturalisation française conditions", "naturalisation",
            "conditions", "délai", "critères", "acquisition", "ressortissant"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        # Passeport talent = visa que P01 quer entender antes de vir
        "nome": "Google News — Passeport Talent & Visa Travail Qualifié",
        "url": "https://news.google.com/rss/search?q=%22passeport+talent%22+OR+%22visa+travail%22+%C3%A9tranger+autoris%C3%A9+France+qualification+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P01", "P03"],
        "urgencia": "alta",
        "keywords": [
            "passeport talent", "visa travail", "autorisation de travail",
            "étranger qualifié", "salarié", "qualification", "titre"
        ],
        "keywords_excluir": ["réfugié", "asile"],
    },
    {
        # Droits travail = CDI/CDD, rupture conventionnelle — o que P03 precisa saber
        "nome": "Google News — Contrat Travail CDD CDI Droits Étrangers",
        "url": "https://news.google.com/rss/search?q=%22contrat+de+travail%22+CDD+CDI+droits+%C3%A9tranger+France+salar%C3%A9+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "contrat de travail CDD CDI", "droits des étrangers",
            "salarié étranger", "rupture conventionnelle", "licenciement",
            "durée légale", "congés payés", "protection"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers"],
    },
    {
        # Légifrance = decretos e circulares — P04 quer a fonte primária
        "nome": "Google News — Légifrance Décrets Étrangers",
        "url": "https://news.google.com/rss/search?q=legifrance+%C3%A9trangers+s%C3%A9jour+naturalisation+CESEDA+d%C3%A9cret&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "juridica",
        "personas": ["P03", "P04"],
        "urgencia": "alta",
        "keywords": [
            "CESEDA", "décret", "circulaire", "étranger", "séjour",
            "naturalisation", "ressortissant", "arrêté", "code entrée séjour"
        ],
        "keywords_excluir": ["réfugié", "asile", "sans-papiers", "OQTF"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # BUROCRÁTICA — os passos que P02 executa toda semana
    # Keywords: termos exatos dos formulários e plataformas reais
    # Gerador principal de salvamentos no Instagram
    # ═══════════════════════════════════════════════════════════════════════════
    {
        # APL = a ajuda que todo recém-chegado precisa — demande APL e plafond CAF
        "nome": "Google News — Demande APL & Plafond CAF Étrangers",
        "url": "https://news.google.com/rss/search?q=%22demande+APL%22+OR+%22plafond+CAF%22+allocation+logement+%C3%A9tranger+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "demande APL", "plafond CAF", "CAF", "APL", "allocation logement",
            "aide", "prestation", "étranger", "résidence"
        ],
        "keywords_excluir": [],
    },
    {
        # ANEF = a plataforma onde tudo acontece — démarches digitais P02
        "nome": "Google News — ANEF Démarches Numériques Étrangers",
        "url": "https://news.google.com/rss/search?q=ANEF+service-public+%C3%A9tranger+d%C3%A9marche+t%C3%A9l%C3%A9proc%C3%A9dure+renouvellement+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P01", "P02"],
        "urgencia": "alta",
        "keywords": [
            "ANEF", "téléservice", "téléprocédure", "démarche en ligne",
            "étranger", "renouvellement", "carte de séjour", "préfecture"
        ],
        "keywords_excluir": [],
    },
    {
        # Numéro sécu provisoire = A MAIOR dor de P02 nos primeiros meses
        "nome": "Google News — Numéro Sécu Provisoire & Carte Vitale Étranger",
        "url": "https://news.google.com/rss/search?q=%22num%C3%A9ro+de+s%C3%A9curit%C3%A9+sociale+provisoire%22+OR+%22attestation+de+droits+ameli%22+%C3%A9tranger+carte+vitale&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02"],
        "urgencia": "alta",
        "keywords": [
            "numéro de sécurité sociale provisoire", "attestation de droits ameli",
            "carte vitale étranger", "ameli", "assurance maladie",
            "remboursement", "mutuelle", "étranger"
        ],
        "keywords_excluir": [],
    },
    {
        # Micro-entreprise = freelancer na França — criação e cotisações URSSAF
        "nome": "Google News — Création Micro-entreprise URSSAF Étranger",
        "url": "https://news.google.com/rss/search?q=%22cr%C3%A9ation+micro-entreprise%22+URSSAF+%22auto-entrepreneur+%C3%A9tranger%22+freelance+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "création micro-entreprise URSSAF", "auto-entrepreneur étranger",
            "URSSAF", "indépendant", "freelance", "cotisations", "auto-entrepreneur"
        ],
        "keywords_excluir": [],
    },
    {
        # Déclaration impôts non-résident = confusão que P02 tem todo ano de abril
        "nome": "Google News — Déclaration Impôts Non-Résident Prélèvement Source",
        "url": "https://news.google.com/rss/search?q=%22d%C3%A9claration+d%27imp%C3%B4ts+non-r%C3%A9sident%22+OR+%22pr%C3%A9l%C3%A8vement+%C3%A0+la+source%22+%C3%A9tranger+France+fiscal&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "burocratica",
        "personas": ["P02", "P03"],
        "urgencia": "alta",
        "keywords": [
            "déclaration d'impôts non-résident", "prélèvement à la source",
            "déclaration impôt", "résident fiscal", "non-résident",
            "impots.gouv", "avis d'imposition", "étranger"
        ],
        "keywords_excluir": [],
    },
    {
        # The Local em inglês = P03 e P04 que consomem em inglês também
        "nome": "The Local France — Expat Admin Guide (EN)",
        "url": "https://www.thelocal.fr/feed/",
        "categoria": "burocratica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "visa", "work permit", "residency", "expat", "foreigner",
            "tax declaration", "health insurance", "titre de séjour",
            "carte vitale", "French administration", "CAF", "housing benefit",
            "social security number", "bank account foreigner"
        ],
        "keywords_excluir": ["refugee", "asylum", "boat", "Mediterranean"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # ACADÊMICA — carreira, diplomas, alternance, CPF
    # Foco: P02 (entrar no mercado) e P03 (subir na carreira)
    # NÃO: pesquisa científica, vestibular, ranking universidade
    # ═══════════════════════════════════════════════════════════════════════════
    {
        # L'Étudiant: foco em estrangeiros e alternance, não em bac/terminale
        "nome": "L'Étudiant — Alternance & Emploi Étranger",
        "url": "https://www.letudiant.fr/rss.xml",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "étranger", "international", "visa étudiant", "alternance",
            "Campus France", "admission", "master", "apprentissage",
            "titre professionnel", "bourses d'études étudiants étrangers"
        ],
        "keywords_excluir": [
            "bac", "terminale", "lycée", "parcoursup", "classes prépa",
            "brevet", "collège", "primaire", "Nobel", "classement"
        ],
    },
    {
        # Alternance = O caminho mais viável para P01/P02 entrar no mercado francês
        "nome": "Google News — Alternance Étranger Contrat Apprentissage",
        "url": "https://news.google.com/rss/search?q=alternance+France+%C3%A9tranger+visa+%22contrat+d%27apprentissage%22+CFA+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "media",
        "keywords": [
            "alternance", "apprentissage", "étranger", "contrat d'apprentissage",
            "visa étudiant", "CFA", "formation", "diplôme", "entreprise"
        ],
        "keywords_excluir": ["bac", "lycée", "parcoursup"],
    },
    {
        # ENIC-NARIC = "meu diploma vale algo aqui?" — dor universal de P02/P03
        "nome": "Google News — Attestation Comparabilité ENIC-NARIC Diplôme",
        "url": "https://news.google.com/rss/search?q=%22attestation+de+comparabilit%C3%A9%22+ENIC-NARIC+dipl%C3%B4me+%C3%A9tranger+France+emploi+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "attestation de comparabilité ENIC-NARIC", "diplôme étranger",
            "reconnaissance", "équivalence", "comparabilité",
            "validation", "VAE", "étranger qualifié"
        ],
        "keywords_excluir": [],
    },
    {
        # Recrutement cadres = P03 quer subir na carreira — salários e vagas reais
        "nome": "Google News — Recrutement Cadres Paris Marché du Travail",
        "url": "https://news.google.com/rss/search?q=%22recrutement+cadres+Paris%22+OR+%22march%C3%A9+du+travail+cadres%22+%C3%A9tranger+salaire+qualification+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "recrutement cadres Paris", "marché du travail cadres",
            "emploi", "cadres", "étranger", "salaire", "qualification",
            "contrat CDI", "APEC", "ingénieur étranger"
        ],
        "keywords_excluir": [],
    },
    {
        # Campus France = processo de entrada P01 — bolsas e admissão
        "nome": "Google News — Campus France Bourses Étudiants Étrangers",
        "url": "https://news.google.com/rss/search?q=%22Campus+France%22+%22bourses+d%27%C3%A9tudes+%C3%A9tudiants+%C3%A9trangers%22+OR+%22visa+%C3%A9tudiant%22+br%C3%A9sil+admission+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P01", "P02"],
        "urgencia": "baixa",
        "keywords": [
            "Campus France", "bourses d'études étudiants étrangers",
            "visa étudiant", "brésil", "admission", "master",
            "université", "dossier", "étudiant étranger"
        ],
        "keywords_excluir": [],
    },
    {
        # CPF = dinheiro de formação que o trabalhador acumula — ninguém explica isso
        "nome": "Google News — CPF Compte Personnel Formation Droits Salarié",
        "url": "https://news.google.com/rss/search?q=%22compte+personnel+de+formation+%28CPF%29+droits%22+salar%C3%A9+%C3%A9tranger+certification+France&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "academica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "compte personnel de formation CPF droits", "CPF",
            "formation professionnelle", "salarié", "étranger",
            "certification", "financement", "droits formation"
        ],
        "keywords_excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # CÍVICA — redes de apoio, língua, integração
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "La Cimade — Vie Civique & Accompagnement",
        "url": "https://www.lacimade.org/feed/",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "droit", "association", "accompagnement", "soutien",
            "intégration", "communauté", "réseau", "aide juridique", "bénévolat"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Associations Intégration Singa CIUP Migrants",
        "url": "https://news.google.com/rss/search?q=association+int%C3%A9gration+migrants+France+r%C3%A9seau+Singa+CIUP+soutien+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "civica",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "intégration", "migrants", "association", "réseau", "soutien",
            "accompagnement", "communauté", "Singa", "CIUP", "bénévolat"
        ],
        "keywords_excluir": [],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # FINANÇAS — inteligência financeira bi-nacional
    # Foco: P03 e P04 que querem fazer o euro render
    # NÃO: CAC 40, Wall Street, bolsa abstrata
    # ═══════════════════════════════════════════════════════════════════════════
    {
        # PEA e Assurance Vie = os dois produtos que qualquer residente fiscal deve ter
        "nome": "Google News — Ouverture PEA & Meilleure Assurance Vie Résident",
        "url": "https://news.google.com/rss/search?q=%22ouverture+PEA%22+OR+%22meilleure+assurance+vie%22+r%C3%A9sident+France+%C3%A9pargne+fiscalit%C3%A9+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "ouverture PEA", "meilleure assurance vie", "PEA",
            "assurance-vie", "épargne", "résident fiscal", "placement",
            "livret", "fiscalité", "patrimoine"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire", "dividende"],
    },
    {
        # Café de la Bourse = análises práticas de investimento para residentes
        "nome": "Café de la Bourse — Investissements Résidents France",
        "url": "https://www.cafedelabourse.com/feed",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "PEA", "assurance vie", "épargne", "investissement",
            "fiscalité", "patrimoine", "livret A", "placement",
            "ouverture PEA", "meilleure assurance vie"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "action", "dividende", "géopolitique"],
    },
    {
        # Fiscalité compte étranger Brésil = bitributação — a dor invisível de P03
        "nome": "Google News — Fiscalité Compte Étranger Brésil Convention",
        "url": "https://news.google.com/rss/search?q=%22fiscalit%C3%A9+compte+%C3%A0+l%27%C3%A9tranger%22+br%C3%A9sil+France+convention+imp%C3%B4t+expatri%C3%A9&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "fiscalité compte à l'étranger Brésil", "convention fiscale",
            "bitributação", "impôt", "non-résident", "expatrié",
            "patrimoine", "déclaration compte étranger"
        ],
        "keywords_excluir": ["CAC 40", "Wall Street", "actionnaire"],
    },
    {
        # Sortie définitive Brésil = a burocracia financeira de quem decidiu ficar
        "nome": "Google News — Déclaration Sortie Définitive Brésil Fiscal",
        "url": "https://news.google.com/rss/search?q=%22d%C3%A9claration+de+sortie+d%C3%A9finitive%22+OR+%22saida+definitiva%22+br%C3%A9sil+fiscal+imp%C3%B4t+%C3%A9tranger&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P03", "P04"],
        "urgencia": "media",
        "keywords": [
            "déclaration de sortie définitive", "saída definitiva",
            "Brésil", "fiscal", "impôt", "non-résident", "Receita Federal",
            "declaração", "capital extérieur"
        ],
        "keywords_excluir": [],
    },
    {
        # Taux de change / Transfert fonds = remessas — quanto custa mandar dinheiro
        "nome": "Google News — Taux de Change Euro Real & Transfert Fonds International",
        "url": "https://news.google.com/rss/search?q=%22taux+de+change+euro+real%22+OR+%22transfert+de+fonds+international%22+Wise+br%C3%A9sil+France+remesse&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P02", "P03"],
        "urgencia": "media",
        "keywords": [
            "taux de change euro real", "transfert de fonds international",
            "virement international", "Brésil", "Wise", "change", "euro",
            "remesse", "banque", "taux"
        ],
        "keywords_excluir": [],
    },
    {
        # INSEE coût de vie = dados duros para ancorar conteúdo — "segundo o INSEE..."
        "nome": "Google News — Indice Prix Consommation INSEE Coût Vie",
        "url": "https://news.google.com/rss/search?q=%22indice+des+prix+%C3%A0+la+consommation%22+INSEE+co%C3%BBt+vie+salaire+logement+France+2026&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "financas",
        "personas": ["P01", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "indice des prix à la consommation INSEE", "INSEE", "coût de la vie",
            "salaire", "logement", "inflation", "pouvoir d'achat", "données"
        ],
        "keywords_excluir": ["CAC 40", "actionnaire"],
    },

    # ═══════════════════════════════════════════════════════════════════════════
    # TRENDMAPPING — identidade, choque cultural, saúde mental
    # O conteúdo que P03 compartilha dizendo "eu sinto exatamente isso"
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "nome": "Google News — Choc Culturel & Adaptation Expatrié France",
        "url": "https://news.google.com/rss/search?q=%22choc+culturel%22+OR+%22adaptation%22+expatri%C3%A9+immigrant+France+quotidien+int%C3%A9gration&hl=fr&gl=FR&ceid=FR:fr",
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
        "nome": "Google News — Identité Biculturelle Entre Deux Cultures",
        "url": "https://news.google.com/rss/search?q=%22identit%C3%A9+biculturelle%22+OR+%22entre+deux+cultures%22+OR+%22appartenance%22+immigrant+France+br%C3%A9sil&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P03", "P04"],
        "urgencia": "baixa",
        "keywords": [
            "identité biculturelle", "entre deux cultures", "appartenance",
            "immigrant", "brésil", "intégration culturelle", "sentiment"
        ],
        "keywords_excluir": [],
    },
    {
        # Syndrome de Paris / santé mentale expatriés = P02 sobrecarregada não fala nisso
        "nome": "Google News — Santé Mentale Expatriés Syndrome de Paris",
        "url": "https://news.google.com/rss/search?q=%22sant%C3%A9+mentale+expatri%C3%A9s%22+OR+%22syndrome+de+paris%22+OR+%22burnout+expatri%C3%A9%22+France+bien-%C3%AAtre&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "santé mentale expatriés", "syndrome de paris",
            "burnout expatrié", "anxiété", "isolement", "dépression", "bien-être"
        ],
        "keywords_excluir": [],
    },
    {
        "nome": "Google News — Travail France Différences Culturelles Brésil",
        "url": "https://news.google.com/rss/search?q=travail+France+%22diff%C3%A9rences+culturelles%22+br%C3%A9sil+expatri%C3%A9+management+int%C3%A9gration&hl=fr&gl=FR&ceid=FR:fr",
        "categoria": "trendmapping",
        "personas": ["P02", "P03"],
        "urgencia": "baixa",
        "keywords": [
            "différences culturelles", "travail France", "brésil",
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
    """Retorna True se ao menos UMA keyword aparecer (lista vazia = aceita tudo)."""
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


def gerar_por_que_interessa(titulo: str, descricao: str, fonte: dict) -> str:
    """Claude Haiku gera 2-3 frases de relevância editorial baseadas nas personas."""
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
    print(f"   {len(FONTES_RSS)} fontes | Janela: {JANELA_DIAS} dias | Mínimo/categoria: {MINIMO_POR_CATEGORIA}\n")

    total_criados = 0
    criados_por_categoria = {}

    # ── 1ª passagem: filtro cirúrgico completo (keywords + blacklist) ──────────
    print("── Passagem 1: filtros por persona ──")
    for fonte in FONTES_RSS:
        qtd = processar_fonte_rss(fonte, criados_por_categoria)
        total_criados += qtd

    # ── 2ª passagem: reforça categorias abaixo do mínimo ──────────────────────
    todas_categorias = set(f["categoria"] for f in FONTES_RSS)
    abaixo = [c for c in todas_categorias if criados_por_categoria.get(c, 0) < MINIMO_POR_CATEGORIA]

    if abaixo:
        print(f"\n── Passagem 2: reforçando {abaixo} ──")
        fontes_por_cat = {}
        for f in FONTES_RSS:
            fontes_por_cat.setdefault(f["categoria"], []).append(f)

        for cat in abaixo:
            print(f"\n  🔄 '{cat}': {criados_por_categoria.get(cat, 0)} → buscando sem filtro de keyword")
            for fonte in fontes_por_cat.get(cat, []):
                if criados_por_categoria.get(cat, 0) >= MINIMO_POR_CATEGORIA:
                    break
                qtd = processar_fonte_rss(fonte, criados_por_categoria, relaxar_keywords=True)
                total_criados += qtd

    # ── Resumo ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"✅ Camada 1 concluída: {total_criados} rascunho(s) criado(s) no Notion")
    for cat in sorted(todas_categorias):
        n = criados_por_categoria.get(cat, 0)
        flag = "✓" if n >= MINIMO_POR_CATEGORIA else "⚠ abaixo do mínimo"
        print(f"   {flag} {cat}: {n}")
    print(f"   Próximo: curadoria manual → Camada 2 estrutura com Claude")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
