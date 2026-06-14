import requests
from datetime import datetime, timedelta

def buscar_fontes_por_dentro(api_key):
    """
    Busca notícias altamente alinhadas com a audiência 'Por Dentro':
    - Finanças práticas (Custo de vida real, inflação cotidiana, habitação).
    - Estudos acadêmicos/sociais humanizados (Adaptação, choque cultural, psicologia da expatriação).
    """
    url = "https://newsapi.org/v2/everything"
    
    # 7 dias atrás para garantir frescor e relevância
    data_limite = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # --- FILTROS REESTRUTURADOS PARA O PÚBLICO-ALVO ---
    # QUERY 1: Finanças Reais e Práticas (Foco em Viver na França, Custo de Vida, Habitação, Emprego)
    # Evita termos puramente macroeconômicos irreais.
    query_financas = (
        '("coût de la vida" OR "prix de l\'immobilier" OR "louer un appartement" OR '
        '"smic" OR "pouvoir d\'achat" OR "inflation france" OR "budget expatrié" OR '
        '"recherche d\'emploi france" OR "recrutement étrangers")'
    )
    
    # QUERY 2: Acadêmico Humano / Social / Cultural (Foco em Adaptação, Identidade e Choque Cultural)
    # Substitui teses genéricas por comportamento e psicologia prática da expatriação.
    query_academico_social = (
        '("choc culturel" OR "intégration des immigrés" OR "vie quotidienne en france" OR '
        '"syndrome de paris" OR "santé mentale expatriés" OR "isolement expatriation" OR '
        '"différences culturelles" OR "identité biculturelle")'
    )
    
    # Combinamos ambas com um operador OR para otimizar a cota da API em uma única chamada assertiva
    query_final = f"{query_financas} OR {query_academico_social}"
    
    # Fontes confiáveis, focadas no cotidiano francês, economia real e análises sociais profundas
    # Le Monde (Nuance/P03), Le Figaro, Capital (Finanças), Libération, Franceinfo
    fontes_alvo = "le-monde,le-figaro,france24,la-tribune"

    params = {
        "q": query_final,
        "from": data_limite,
        "language": "fr",  # focado na realidade local da França
        "domains": "lemonde.fr,lefigaro.fr,capital.fr,liberation.fr,francetvinfo.fr,leparisien.fr",
        "sortBy": "relevance",
        "pageSize": 15,
        "apiKey": api_key
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        dados = response.json()
        
        artigos_filtrados = []
        if "articles" in dados:
            for artigo in dados["articles"]:
                # Curadoria fina: Ignora conteúdos muito corporativos ou puramente políticos de topo de macroeconomia
                blacklist = ["bourse", "cac 40", " Wall Street", "actionnaires", "géopolitique"]
                titulo_desc = (artigo.get("title", "") + " " + artigo.get("description", "")).lower()
                
                if not any(termo in titulo_desc for termo in blacklist):
                    artigos_filtrados.append({
                        "titulo": artigo.get("title"),
                        "descricao": artigo.get("description"),
                        "url": artigo.get("url"),
                        "fonte": artigo.get("source", {}).get("name"),
                        "data": artigo.get("publishedAt")
                    })
        
        return artigos_filtrados
        
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar fontes assertivas: {e}")
        return []

# Exemplo de execução simulada:
if __name__ == "__main__":
    # Substitua pela sua chave real da NewsAPI
    MINHA_API_KEY = "SUA_API_KEY_AQUI"
    noticias = buscar_fontes_por_dentro(MINHA_API_KEY)
    
    print(f"Encontrados {len(noticias)} ganchos altamente magnéticos para o Instagram:\n")
    for i, noti in enumerate(noticias, 1):
        print(f"{i}. [{noti['fonte']}] {noti['titulo']}")
        print(f"   Contexto: {noti['descricao']}")
        print(f"   Link: {noti['url']}\n")
