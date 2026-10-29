import random
import requests
from urllib.parse import quote
from flask import Flask, redirect, render_template, request

app = Flask(__name__)

from chave import CHAVE
import random
import requests
from urllib.parse import quote
from flask import Flask, redirect, render_template, request

from chave import CHAVE

app = Flask(__name__)

API = "https://api.themoviedb.org/3"
# Filme e série usam endereços e campos de data diferentes no TMDB
TIPOS = {
    "filme": {"rota": "movie", "data": "primary_release_date"},
    "serie": {"rota": "tv", "data": "first_air_date"},
}

# Cada gênero tem um código para filme e outro para série.
# Se um tipo não aparece, o gênero não existe nesse formato (ex: terror só em filme).
GENEROS = {
    "acao": {"filme": "28", "serie": "10759"},
    "comedia": {"filme": "35", "serie": "35"},
    "drama": {"filme": "18", "serie": "18"},
    "terror": {"filme": "27"},
    "ficcao": {"filme": "878", "serie": "10765"},
    "animacao": {"filme": "16", "serie": "16"},
    "romance": {"filme": "10749"},
    "documentario": {"filme": "99", "serie": "99"},
    "novela": {"serie": "10766"},
}
# "dorama" é tratado à parte na função sortear (séries com idioma coreano)

# Endereço de busca de cada serviço ({} vira o título)
SERVICOS = {
    "netflix": "https://www.netflix.com/search?q={}",
    "prime video": "https://www.primevideo.com/search/?phrase={}",
    "disney": "https://www.disneyplus.com/search?q={}",
    "apple tv": "https://tv.apple.com/br/search?term={}",
    "hbo": "https://play.hbomax.com/search?q={}",
    "paramount": "https://www.paramountplus.com/br/search/?q={}",
    "crunchyroll": "https://www.crunchyroll.com/search?q={}",
    "globoplay": "https://globoplay.globo.com/busca/?q={}",
}

# Plataformas que o site procura pelo NOME na lista do TMDB
# (os códigos delas já mudaram antes, então não fixamos o número)
POR_NOME = {
    "hbomax": ["hbo max", "max"],
    "globoplay": ["globoplay"],
}
PROVEDORES_BR = {}  # nome em minúsculas -> código (preenchido na primeira busca)


def carregar_provedores():
    if PROVEDORES_BR:
        return
    for rota in ("movie", "tv"):
        url = f"{API}/watch/providers/{rota}"
        dados = requests.get(url, params={"api_key": CHAVE, "watch_region": "BR"}).json()
        for p in dados.get("results", []):
            PROVEDORES_BR[p["provider_name"].lower()] = str(p["provider_id"])


def codigos_da_plataforma(valor):
    """Número vira ele mesmo; nome (ex: 'globoplay') é procurado na lista do TMDB."""
    if valor.isdigit():
        return [valor]

    carregar_provedores()
    termos = POR_NOME.get(valor, [valor])
    achados = []
    for nome, codigo in PROVEDORES_BR.items():
        for t in termos:
            if nome == t or (t != "max" and t in nome):
                achados.append(codigo)
                break
    return achados


def normalizar(item, tipo):
    """Deixa filme e série no mesmo formato para o HTML usar."""
    if tipo == "filme":
        titulo = item.get("title")
        data = item.get("release_date")
    else:
        titulo = item.get("name")
        data = item.get("first_air_date")

    return {
        "id": item["id"],
        "tipo": tipo,
        "titulo": titulo,
        "ano": (data or "")[:4],
        "poster": item.get("poster_path"),
        "fundo": item.get("backdrop_path") or item.get("poster_path"),
        "sinopse": item.get("overview"),
        "nota": item.get("vote_average") or 0,
    }


def buscar_plataformas(item):
    rota = TIPOS[item["tipo"]]["rota"]
    url = f"{API}/{rota}/{item['id']}/watch/providers"
    dados = requests.get(url, params={"api_key": CHAVE}).json()
    brasil = dados.get("results", {}).get("BR", {})
    link_padrao = brasil.get("link", "#")
    titulo = quote(item["titulo"] or "")

    plataformas = []
    vistos = set()
    for p in brasil.get("flatrate", []):
        nome = p["provider_name"].lower()
        if nome == "max":
            nome = "hbo max"

        servico = None
        for chave in SERVICOS:
            if chave in nome:
                servico = chave
                break

        if servico:
            if servico in vistos:
                continue
            vistos.add(servico)
            link = SERVICOS[servico].format(titulo)
        else:
            link = link_padrao

        plataformas.append({
            "nome": p["provider_name"],
            "logo": p["logo_path"],
            "link": link,
        })
    return plataformas


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/resultado", methods=["GET", "POST"])
def sortear():
    # Se alguém abrir /resultado direto pelo endereço, volta para o início
    if request.method == "GET":
        return redirect("/")

    tipo = request.form.get("tipo", "filme")
    if tipo not in TIPOS:
        tipo = "filme"
    config = TIPOS[tipo]

    selecionados = request.form.getlist("generos")
    plataformas = request.form.getlist("plataformas")
    ano_de = request.form.get("ano_de", "").strip()
    ano_ate = request.form.get("ano_ate", "").strip()
    pagina = request.form.get("pagina", "")        # vem preenchida ao clicar num pôster
    escolhido = request.form.get("escolhido", "")  # id do pôster clicado

    params = {
        "api_key": CHAVE,
        "language": "pt-BR",
        "watch_region": "BR",
        "with_watch_monetization_types": "flatrate",
        "page": 1,
    }

    # Converte os gêneros marcados nos códigos do TMDB para o tipo escolhido
    ids = []
    for g in selecionados:
        id_genero = GENEROS.get(g, {}).get(tipo)
        if id_genero:
            ids.append(id_genero)

    # Doramas = séries com idioma original coreano
    if tipo == "serie" and "dorama" in selecionados:
        params["with_original_language"] = "ko"
        if not ids:
            ids = ["18"]  # sem outro gênero marcado, usa Drama

    if ids:
        params["with_genres"] = "|".join(ids)

    if plataformas:
        codigos = []
        for valor in plataformas:
            codigos += codigos_da_plataforma(valor)
        # "0" não existe: se nenhuma plataforma foi encontrada, não sorteia nada
        params["with_watch_providers"] = "|".join(codigos) if codigos else "0"

    campo = config["data"]
    if ano_de.isdigit():
        params[f"{campo}.gte"] = f"{ano_de}-01-01"
    if ano_ate.isdigit():
        params[f"{campo}.lte"] = f"{ano_ate}-12-31"

    url = f"{API}/discover/{config['rota']}"

    # Descobre qual página usar: a mesma de antes (clique no pôster) ou uma aleatória
    if pagina.isdigit() and int(pagina) > 0:
        numero_pagina = int(pagina)
    else:
        dados = requests.get(url, params=params).json()
        total_paginas = min(dados.get("total_pages", 0), 500)
        numero_pagina = random.randint(1, total_paginas) if total_paginas > 0 else 0

    resultados = []
    if numero_pagina > 0:
        params["page"] = numero_pagina
        resultados = requests.get(url, params=params).json().get("results", [])

    item = None
    relacionados = []
    if resultados:
        # Se clicou num pôster, usa ele; senão, sorteia um da lista
        bruto = next((r for r in resultados if str(r["id"]) == escolhido), None)
        if bruto is None:
            bruto = random.choice(resultados)

        item = normalizar(bruto, tipo)
        relacionados = [
            normalizar(r, tipo)
            for r in resultados
            if r["id"] != bruto["id"] and r.get("poster_path")
        ][:6]

    onde_assistir = []
    meias_estrelas = 0
    if item:
        onde_assistir = buscar_plataformas(item)
        meias_estrelas = int(item["nota"] + 0.5)

    # Guarda os filtros para os botões da página de resultado reenviarem
    filtros = {
        "tipo": tipo,
        "generos": selecionados,
        "plataformas": plataformas,
        "ano_de": ano_de,
        "ano_ate": ano_ate,
        "pagina": numero_pagina,
    }

    return render_template(
        "resultado.html",
        filme=item,
        relacionados=relacionados,
        filtros=filtros,
        onde_assistir=onde_assistir,
        porcentagem=meias_estrelas * 10,
        estrelas=meias_estrelas / 2,
    )


if __name__ == "__main__":
    app.run(debug=True)