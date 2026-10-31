import hmac
import os
import random
import re
import secrets
import sqlite3
import requests
import time
import itertools
from concurrent.futures import ThreadPoolExecutor

tentativas = {}
MAX_TENTATIVAS = 5
JANELA = 10 * 60  # 10 minutos, em segundos


def ip_do_cliente():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    return ip.split(",")[0].strip()


def esta_bloqueado(chave):
    agora = time.time()
    recentes = [t for t in tentativas.get(chave, []) if agora - t < JANELA]
    if recentes:
        tentativas[chave] = recentes
    else:
        tentativas.pop(chave, None)
    return len(recentes) >= MAX_TENTATIVAS


def registrar_erro(chave):
    tentativas.setdefault(chave, []).append(time.time())


from urllib.parse import quote
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for, flash
from werkzeug.security import check_password_hash, generate_password_hash

from chave import CHAVE, SEGREDO

import chave as _chave
ADMIN = getattr(_chave, "ADMIN", "")

app = Flask(__name__)
app.secret_key = SEGREDO
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # o JavaScript da página não consegue ler o cookie
    SESSION_COOKIE_SAMESITE="Lax",  # o navegador não envia o cookie em formulários de outros sites
)

API = "https://api.themoviedb.org/3"

# Filme e série usam endereços e campos de data diferentes no TMDB
TIPOS = {
    "filme": {"rota": "movie", "data": "primary_release_date"},
    "serie": {"rota": "tv", "data": "first_air_date"},
}

# Cada gênero tem um código para filme e outro para série.
# Se um tipo não aparece, o gênero não existe nesse formato.
GENEROS = {
    "acao": {"filme": "28", "serie": "10759"},
    "comedia": {"filme": "35", "serie": "35"},
    "drama": {"filme": "18", "serie": "18"},
    "terror": {"filme": "27"},  # em série, usamos tags (veja codigos_de_terror)
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
POR_NOME = {
    "hbomax": ["hbo max", "max"],
    "globoplay": ["globoplay"],
}
PROVEDORES_BR = {}  # nome em minúsculas -> código (preenchido na primeira busca)

# Séries não têm o gênero "terror" no TMDB, então usamos as tags (palavras-chave) dele
TERMOS_TERROR = ["horror", "slasher", "zombie"]
PALAVRAS_CHAVE = {}  # termo -> código (preenchido na primeira busca)


# ================= BANCO DE DADOS (SQLite) =================
CAMINHO_BANCO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banco.db")


def conectar():
    conexao = sqlite3.connect(CAMINHO_BANCO)
    conexao.row_factory = sqlite3.Row
    return conexao


def executar(sql, parametros=()):
    """Roda um comando que altera o banco (INSERT, UPDATE, DELETE...)."""
    conexao = conectar()
    try:
        with conexao:
            conexao.execute(sql, parametros)
    finally:
        conexao.close()


def buscar_um(sql, parametros=()):
    """Devolve uma linha (ou None)."""
    conexao = conectar()
    try:
        return conexao.execute(sql, parametros).fetchone()
    finally:
        conexao.close()


def buscar_todos(sql, parametros=()):
    """Devolve uma lista de linhas."""
    conexao = conectar()
    try:
        return conexao.execute(sql, parametros).fetchall()
    finally:
        conexao.close()


def iniciar_banco():

    LIMITE_COMENTARIO = 600


LIMITE_COMENTARIO = 600


def criar_tabelas_extras():
    executar(
        """
        CREATE TABLE IF NOT EXISTS avaliacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            tipo TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            ano TEXT,
            poster TEXT,
            nota INTEGER NOT NULL CHECK (nota BETWEEN 1 AND 5),
            comentario TEXT NOT NULL DEFAULT '',
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (usuario_id, tipo, tmdb_id)
        )
        """
    )


def iniciar_banco():
    executar(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
            senha_hash TEXT NOT NULL,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    executar(
        """
        CREATE TABLE IF NOT EXISTS assistidos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            tipo TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            ano TEXT,
            poster TEXT,
            assistido_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (usuario_id, tipo, tmdb_id)
        )
        """
    )
    executar(
        """
        CREATE TABLE IF NOT EXISTS avaliacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            tipo TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            ano TEXT,
            poster TEXT,
            nota INTEGER NOT NULL CHECK (nota BETWEEN 1 AND 5),
            comentario TEXT NOT NULL DEFAULT '',
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (usuario_id, tipo, tmdb_id)
        )
        """
    )


def atualizar_banco():
    colunas = [c["name"] for c in buscar_todos("PRAGMA table_info(avaliacoes)")]
    if "escondido" not in colunas:
        executar("ALTER TABLE avaliacoes ADD COLUMN escondido INTEGER NOT NULL DEFAULT 0")
    executar(
        """
        CREATE TABLE IF NOT EXISTS denuncias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            avaliacao_id INTEGER NOT NULL REFERENCES avaliacoes(id) ON DELETE CASCADE,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (avaliacao_id, usuario_id)
        )
        """,
        (),
    )


iniciar_banco()
atualizar_banco()


MAX_TENTATIVAS = 5
JANELA_MINUTOS = 15


def criar_tabela_tentativas():
    executar(
        """
        CREATE TABLE IF NOT EXISTS tentativas_login (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL COLLATE NOCASE,
            em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


criar_tabela_tentativas()


def criar_tabela_quero():
    executar(
        """
        CREATE TABLE IF NOT EXISTS quero_assistir (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            tipo TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            ano TEXT,
            poster TEXT,
            adicionado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (usuario_id, tipo, tmdb_id)
        )
        """
    )


criar_tabela_quero()


def tentativas_recentes(nome):
    """Quantos erros de senha esse usuário teve nos últimos minutos."""
    linha = buscar_um(
        "SELECT COUNT(*) AS total FROM tentativas_login WHERE nome = ? AND em > datetime('now', ?)",
        (nome, f"-{JANELA_MINUTOS} minutes"),
    )
    return linha["total"]

# ================= SEGURANÇA DOS FORMULÁRIOS =================
@app.before_request
def proteger_formularios():
    """Todo formulário que muda algo precisa trazer o código secreto da sessão."""
    if request.method == "POST" and request.endpoint != "sortear":
        enviado = request.form.get("csrf", "").encode()
        esperado = session.get("csrf", "").encode()
        if not esperado or not hmac.compare_digest(enviado, esperado):
            abort(400)


@app.context_processor
def dados_comuns():
    """Variáveis disponíveis em todos os HTMLs."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {
        "csrf_token": session["csrf"],
        "usuario": session.get("usuario"),
        "usuario_admin": eh_admin(),
    }

def entrar_na_conta(usuario):
    session.clear()
    session["csrf"] = secrets.token_hex(16)
    session["usuario_id"] = usuario["id"]
    session["usuario"] = usuario["nome"]


# ================= TMDB =================
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


def codigos_de_terror():
    """Procura no TMDB o código das tags de terror (e guarda para não repetir)."""
    achados = []
    for termo in TERMOS_TERROR:
        if termo not in PALAVRAS_CHAVE:
            dados = requests.get(
                f"{API}/search/keyword",
                params={"api_key": CHAVE, "query": termo},
            ).json()
            for r in dados.get("results", []):
                if r["name"].lower() == termo:
                    PALAVRAS_CHAVE[termo] = str(r["id"])
                    break
        if termo in PALAVRAS_CHAVE:
            achados.append(PALAVRAS_CHAVE[termo])
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


def buscar_detalhes(tipo, tmdb_id):
    """Busca um título pelo código no TMDB (os dados vêm de lá, não do navegador)."""
    rota = TIPOS[tipo]["rota"]
    resposta = requests.get(
        f"{API}/{rota}/{tmdb_id}",
        params={"api_key": CHAVE, "language": "pt-BR"},
    )
    if resposta.status_code != 200:
        return None
    return normalizar(resposta.json(), tipo)


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

def buscar_em_paralelo(item):
    """Busca as plataformas e o trailer ao mesmo tempo (em vez de um depois do outro)."""
    with ThreadPoolExecutor(max_workers=2) as grupo_de_tarefas:
        plataformas = grupo_de_tarefas.submit(buscar_plataformas, item)
        trailer = grupo_de_tarefas.submit(buscar_trailer, item)
        return plataformas.result(), trailer.result()

def buscar_trailer(item):
    """Procura no TMDB o trailer do título no YouTube (ou None se não achar)."""
    rota = TIPOS[item["tipo"]]["rota"]
    for idioma in ("pt-BR", "en-US"):
        try:
            dados = requests.get(
                f"{API}/{rota}/{item['id']}/videos",
                params={"api_key": CHAVE, "language": idioma},
                timeout=10,
            ).json()
        except requests.RequestException:
            continue
        videos = [v for v in dados.get("results", []) if v.get("site") == "YouTube"]
        for tipo_video in ("Trailer", "Teaser"):
            for v in videos:
                if v.get("type") == tipo_video:
                    return f"https://www.youtube.com/watch?v={v['key']}"
    return None

def nota_da_comunidade(tipo, tmdb_id):
    """Média e quantidade das avaliações do site para um título (ou None se não tem)."""
    linha = buscar_um(
        """
        SELECT AVG(nota) AS media, COUNT(*) AS total
        FROM avaliacoes
        WHERE tipo = ? AND tmdb_id = ? AND escondido = 0
        """,
        (tipo, tmdb_id),
    )
    if not linha or not linha["total"]:
        return None

    total = linha["total"]
    media = f"{linha['media']:.1f}".replace(".", ",")
    palavra = "avaliação" if total == 1 else "avaliações"
    return {"texto": f"{media} de 5 · {total} {palavra}"}


def comentarios_do_titulo(tipo, tmdb_id):
    """Avaliações do site para um título (as escondidas pela moderação ficam de fora)."""
    linhas = buscar_todos(
        """
        SELECT a.id, a.usuario_id, a.nota, a.comentario, a.atualizado_em, u.nome AS autor
        FROM avaliacoes a
        JOIN usuarios u ON u.id = a.usuario_id
        WHERE a.tipo = ? AND a.tmdb_id = ? AND a.escondido = 0
        ORDER BY a.atualizado_em DESC, a.id DESC
        LIMIT 20
        """,
        (tipo, tmdb_id),
    )
    meu_id = session.get("usuario_id")
    lista = []
    for l in linhas:
        d = l["atualizado_em"]
        lista.append({
            "id": l["id"],
            "autor": l["autor"],
            "nota": l["nota"],
            "comentario": l["comentario"],
            "minha": l["usuario_id"] == meu_id,
            "data": f"{d[8:10]}/{d[5:7]}/{d[0:4]}",
        })
    return lista


# ================= PÁGINAS =================
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    erro = None
    nome = ""
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar", "")

        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", nome):
            erro = "O usuário deve ter de 3 a 20 caracteres, só letras, números e _ (sem espaços)."
        elif len(senha) < 8:
            erro = "A senha precisa ter pelo menos 8 caracteres."
        elif len(senha) > 128:
            erro = "A senha pode ter no máximo 128 caracteres."
        elif senha != confirmar:
            erro = "As senhas não são iguais."
        elif buscar_um("SELECT id FROM usuarios WHERE nome = ?", (nome,)):
            erro = "Esse usuário já existe. Escolha outro."
        else:
            try:
                executar(
                    "INSERT INTO usuarios (nome, senha_hash) VALUES (?, ?)",
                    (nome, generate_password_hash(senha)),
                )
            except sqlite3.IntegrityError:
                erro = "Esse usuário já existe. Escolha outro."
            else:
                novo = buscar_um("SELECT id, nome FROM usuarios WHERE nome = ?", (nome,))
                entrar_na_conta(novo)
                return redirect("/")

    return render_template("cadastro.html", erro=erro, nome=nome)


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    erro = None
    nome = ""
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()[:20]
        senha = request.form.get("senha", "")

        if tentativas_recentes(nome) >= MAX_TENTATIVAS:
            erro = f"Muitas tentativas erradas. Aguarde {JANELA_MINUTOS} minutos e tente de novo."
        else:
            pessoa = buscar_um(
                "SELECT id, nome, senha_hash FROM usuarios WHERE nome = ?", (nome,)
            )
            if pessoa and check_password_hash(pessoa["senha_hash"], senha):
                executar("DELETE FROM tentativas_login WHERE nome = ?", (nome,))
                entrar_na_conta(pessoa)
                return redirect("/")

            # Errou: anota a tentativa e limpa registros com mais de um dia
            executar("INSERT INTO tentativas_login (nome) VALUES (?)", (nome,))
            executar("DELETE FROM tentativas_login WHERE em < datetime('now', '-1 day')")
            erro = "Usuário ou senha incorretos."

    return render_template("entrar.html", erro=erro, nome=nome)


@app.route("/sair", methods=["POST"])
def sair():
    session.clear()
    return redirect("/")


@app.route("/assistido", methods=["POST"])
def alternar_assistido():
    """Marca ou desmarca um título como assistido (responde em JSON para o JavaScript)."""
    if "usuario_id" not in session:
        return jsonify({"erro": "Entre na sua conta."}), 401

    tipo = request.form.get("tipo", "")
    tmdb_id = request.form.get("tmdb_id", "")
    if tipo not in TIPOS or not tmdb_id.isdigit():
        return jsonify({"erro": "Pedido inválido."}), 400

    usuario_id = session["usuario_id"]
    tmdb_id = int(tmdb_id)

    ja_tem = buscar_um(
        "SELECT id FROM assistidos WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
        (usuario_id, tipo, tmdb_id),
    )

    if ja_tem:
        executar("DELETE FROM assistidos WHERE id = ?", (ja_tem["id"],))
        return jsonify({"assistido": False})

    item = buscar_detalhes(tipo, tmdb_id)
    if not item:
        return jsonify({"erro": "Não encontrei esse título."}), 404

    executar(
        """
        INSERT OR IGNORE INTO assistidos (usuario_id, tipo, tmdb_id, titulo, ano, poster)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (usuario_id, tipo, tmdb_id, item["titulo"] or "Sem título", item["ano"], item["poster"]),
    )
    return jsonify({"assistido": True})


@app.route("/quero", methods=["POST"])
def alternar_quero():
    """Adiciona ou tira um título da lista 'Quero assistir' (responde em JSON)."""
    if "usuario_id" not in session:
        return jsonify({"erro": "Entre na sua conta."}), 401

    tipo = request.form.get("tipo", "")
    tmdb_id = request.form.get("tmdb_id", "")
    if tipo not in TIPOS or not tmdb_id.isdigit():
        return jsonify({"erro": "Pedido inválido."}), 400

    usuario_id = session["usuario_id"]
    tmdb_id = int(tmdb_id)

    ja_tem = buscar_um(
        "SELECT id FROM quero_assistir WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
        (usuario_id, tipo, tmdb_id),
    )
    if ja_tem:
        executar("DELETE FROM quero_assistir WHERE id = ?", (ja_tem["id"],))
        return jsonify({"quero": False})

    item = buscar_detalhes(tipo, tmdb_id)
    if not item:
        return jsonify({"erro": "Não encontrei esse título."}), 404

    executar(
        """
        INSERT OR IGNORE INTO quero_assistir (usuario_id, tipo, tmdb_id, titulo, ano, poster)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (usuario_id, tipo, tmdb_id, item["titulo"] or "Sem título", item["ano"], item["poster"]),
    )
    return jsonify({"quero": True})


@app.route("/quero-assistir")
def quero_assistir():
    if "usuario_id" not in session:
        return redirect("/entrar")

    lista = buscar_todos(
        """
        SELECT tipo, tmdb_id, titulo, ano, poster
        FROM quero_assistir
        WHERE usuario_id = ?
        ORDER BY adicionado_em DESC, id DESC
        LIMIT 100
        """,
        (session["usuario_id"],),
    )
    return render_template("quero_assistir.html", lista=lista)


@app.route("/remover-quero", methods=["POST"])
def remover_quero():
    if "usuario_id" not in session:
        return redirect("/entrar")

    tipo = request.form.get("tipo", "")
    tmdb_id = request.form.get("tmdb_id", "")
    if tipo in TIPOS and tmdb_id.isdigit():
        executar(
            "DELETE FROM quero_assistir WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, int(tmdb_id)),
        )
    return redirect("/quero-assistir")

@app.route("/quero-assistir/sortear", methods=["POST"])
def sortear_quero():
    """Sorteia um título só entre os que a pessoa salvou em 'Quero assistir'."""
    if "usuario_id" not in session:
        return redirect("/entrar")

    linhas = buscar_todos(
        "SELECT tipo, tmdb_id, poster FROM quero_assistir WHERE usuario_id = ?",
        (session["usuario_id"],),
    )
    if not linhas:
        return redirect("/quero-assistir")

    escolhida = random.choice(linhas)
    pool = [
        l["poster"] for l in linhas
        if l["poster"] and not (l["tipo"] == escolhida["tipo"] and l["tmdb_id"] == escolhida["tmdb_id"])
    ]
    random.shuffle(pool)
    return pagina_titulo(escolhida["tipo"], escolhida["tmdb_id"], pool[:8], True)

@app.route("/recentes")
def recentes():
    if "usuario_id" not in session:
        return redirect("/entrar")

    linhas = buscar_todos(
        """
        SELECT tipo, tmdb_id, titulo, ano, poster, assistido_em
        FROM assistidos
        WHERE usuario_id = ?
        ORDER BY assistido_em DESC, id DESC
        LIMIT 60
        """,
        (session["usuario_id"],),
    )

    lista = []
    for linha in linhas:
        d = linha["assistido_em"]  # formato 2026-10-30 14:05:00
        lista.append({
            "tipo": linha["tipo"],
            "tmdb_id": linha["tmdb_id"],
            "titulo": linha["titulo"],
            "ano": linha["ano"],
            "poster": linha["poster"],
            "data": f"{d[8:10]}/{d[5:7]}/{d[0:4]}",
        })

    return render_template("recentes.html", lista=lista)


@app.route("/remover-assistido", methods=["POST"])
def remover_assistido():
    if "usuario_id" not in session:
        return redirect("/entrar")

    tipo = request.form.get("tipo", "")
    tmdb_id = request.form.get("tmdb_id", "")

    if tipo in TIPOS and tmdb_id.isdigit():
        executar(
            "DELETE FROM assistidos WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, int(tmdb_id)),
        )

    return redirect("/recentes")


@app.route("/avaliar/<tipo>/<int:tmdb_id>", methods=["GET", "POST"])
def avaliar(tipo, tmdb_id):
    if "usuario_id" not in session:
        return redirect("/entrar")
    if tipo not in TIPOS:
        abort(404)

    item = buscar_detalhes(tipo, tmdb_id)
    if not item:
        abort(404)

    usuario_id = session["usuario_id"]
    erro = None

    atual = buscar_um(
        "SELECT nota, comentario FROM avaliacoes WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
        (usuario_id, tipo, tmdb_id),
    )
    nota = atual["nota"] if atual else 0
    comentario = atual["comentario"] if atual else ""

    if request.method == "POST":
        nota_texto = request.form.get("nota", "")
        comentario = request.form.get("comentario", "").strip()

        if nota_texto not in ("1", "2", "3", "4", "5"):
            erro = "Escolha de 1 a 5 estrelas."
            nota = 0
        elif len(comentario) > LIMITE_COMENTARIO:
            erro = f"O comentário pode ter no máximo {LIMITE_COMENTARIO} caracteres."
            nota = int(nota_texto)
        else:
            nota = int(nota_texto)
            titulo = item["titulo"] or "Sem título"

            # Se já existe uma avaliação dessa pessoa para esse título, atualiza
            executar(
                """
                INSERT INTO avaliacoes (usuario_id, tipo, tmdb_id, titulo, ano, poster, nota, comentario)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (usuario_id, tipo, tmdb_id) DO UPDATE SET
                    nota = excluded.nota,
                    comentario = excluded.comentario,
                    atualizado_em = CURRENT_TIMESTAMP
                """,
                (usuario_id, tipo, tmdb_id, titulo, item["ano"], item["poster"], nota, comentario),
            )

            # Quem avalia, assistiu: coloca também nos recentes
            executar(
                """
                INSERT OR IGNORE INTO assistidos (usuario_id, tipo, tmdb_id, titulo, ano, poster)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (usuario_id, tipo, tmdb_id, titulo, item["ano"], item["poster"]),
            )
            return redirect("/avaliacoes")

    return render_template(
        "avaliar.html",
        item=item,
        nota=nota,
        comentario=comentario,
        erro=erro,
        ja_avaliou=bool(atual),
        limite=LIMITE_COMENTARIO,
    )


@app.route("/excluir-avaliacao", methods=["POST"])
def excluir_avaliacao():
    if "usuario_id" not in session:
        return redirect("/entrar")

    tipo = request.form.get("tipo", "")
    tmdb_id = request.form.get("tmdb_id", "")

    if tipo in TIPOS and tmdb_id.isdigit():
        executar(
            "DELETE FROM avaliacoes WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, int(tmdb_id)),
        )

    return redirect("/avaliacoes")


@app.route("/avaliacoes")
def avaliacoes():
    linhas = buscar_todos(
        """
        SELECT a.id, a.usuario_id, a.tipo, a.tmdb_id, a.titulo, a.ano, a.poster,
               a.nota, a.comentario, a.atualizado_em, u.nome AS autor
        FROM avaliacoes a
        JOIN usuarios u ON u.id = a.usuario_id
        WHERE a.escondido = 0
        ORDER BY a.atualizado_em DESC, a.id DESC
        LIMIT 50
        """
    )

    meu_id = session.get("usuario_id")
    lista = []
    for linha in linhas:
        d = linha["atualizado_em"]
        lista.append({
            "id": linha["id"],
            "tipo": linha["tipo"],
            "tmdb_id": linha["tmdb_id"],
            "titulo": linha["titulo"],
            "ano": linha["ano"],
            "poster": linha["poster"],
            "nota": linha["nota"],
            "porcentagem": linha["nota"] * 20,
            "comentario": linha["comentario"],
            "autor": linha["autor"],
            "minha": linha["usuario_id"] == meu_id,
            "data": f"{d[8:10]}/{d[5:7]}/{d[0:4]}",
        })

    return render_template("avaliacoes.html", lista=lista)


@app.route("/buscar")
def buscar_titulo():
    q = request.args.get("q", "").strip()[:100]
    resultados = []
    if q:
        resposta = requests.get(
            f"{API}/search/multi",
            params={"api_key": CHAVE, "language": "pt-BR", "query": q, "include_adult": "false"},
            timeout=10,
        )
        if resposta.status_code == 200:
            for r in resposta.json().get("results", []):
                tipo = {"movie": "filme", "tv": "serie"}.get(r.get("media_type"))
                if tipo and r.get("poster_path"):
                    resultados.append(normalizar(r, tipo))
    return render_template("buscar.html", q=q, resultados=resultados[:20])


@app.route("/titulo/<tipo>/<int:tmdb_id>")
def pagina_titulo(tipo, tmdb_id, pool=None, da_lista=False):
    """Mostra um título escolhido na busca, reaproveitando o resultado.html."""
    if tipo not in TIPOS:
        abort(404)
    item = buscar_detalhes(tipo, tmdb_id)
    if not item:
        abort(404)

    ja_assistiu = False
    ja_quero = False
    if "usuario_id" in session:
        ja_assistiu = bool(buscar_um(
            "SELECT id FROM assistidos WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, tmdb_id),
        ))
        ja_quero = bool(buscar_um(
            "SELECT id FROM quero_assistir WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, tmdb_id),
        ))

    meias_estrelas = int(item["nota"] + 0.5)

    filtros = {"tipo": tipo, "generos": [], "plataformas": [],
               "ano_de": "", "ano_ate": "", "pagina": 0}


    return render_template(
        "resultado.html",
        filme=item,
        relacionados=[],
        filtros=filtros,
        onde_assistir=buscar_plataformas(item),
        ja_assistiu=ja_assistiu,
        ja_quero=ja_quero,
        comunidade=nota_da_comunidade(tipo, tmdb_id),
        comentarios=comentarios_do_titulo(tipo, tmdb_id),
        porcentagem=meias_estrelas * 10,
        estrelas=meias_estrelas / 2,
    )

NOMES_GENERO = {
    "acao": "Ação", "comedia": "Comédia", "drama": "Drama", "terror": "Terror",
    "ficcao": "Ficção científica", "animacao": "Animação", "romance": "Romance",
    "documentario": "Documentário", "novela": "Novela",
}


def contexto_do_item(item, tipo):
    """Dados extras que o resultado.html mostra para um título."""
    ctx = {
        "onde_assistir": [], "ja_assistiu": False, "ja_quero": False,
        "comunidade": None, "comentarios": [], "trailer": None,
        "porcentagem": 0, "estrelas": 0,
    }
    if not item:
        return ctx

    meias = int(item["nota"] + 0.5)
    onde, trailer = buscar_em_paralelo(item)
    ctx.update(
        onde_assistir=onde,
        comunidade=nota_da_comunidade(tipo, item["id"]),
        comentarios=comentarios_do_titulo(tipo, item["id"]),
        trailer=trailer,
        porcentagem=meias * 10,
        estrelas=meias / 2,
    )

    if "usuario_id" in session:
        ctx["ja_assistiu"] = bool(buscar_um(
            "SELECT id FROM assistidos WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, item["id"]),
        ))
        ctx["ja_quero"] = bool(buscar_um(
            "SELECT id FROM quero_assistir WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
            (session["usuario_id"], tipo, item["id"]),
        ))
    return ctx


@app.route("/grupo", methods=["GET", "POST"])
def grupo():
    """Cada pessoa marca seus gêneros e o site procura algo que agrade a todas."""
    lista_generos = [
        {"chave": g, "nome": NOMES_GENERO[g], "tipos": ",".join(GENEROS[g])}
        for g in GENEROS
    ]
    if request.method == "GET":
        return render_template("grupo.html", generos=lista_generos)

    tipo = request.form.get("tipo", "filme")
    if tipo not in TIPOS:
        tipo = "filme"
    config = TIPOS[tipo]
    validos = {g for g in GENEROS if tipo in GENEROS[g]}

    n = request.form.get("pessoas", "2")
    n = int(n) if n in ("2", "3", "4") else 2

    escolhas = []
    for i in range(1, n + 1):
        marcados = [g for g in request.form.getlist(f"p{i}") if g in validos]
        if not marcados:
            flash(f"A pessoa {i} precisa marcar pelo menos um gênero.")
            return redirect("/grupo")
        escolhas.append(marcados)

    base = {
        "api_key": CHAVE,
        "language": "pt-BR",
        "watch_region": "BR",
        "with_watch_monetization_types": "flatrate",
        "vote_count.gte": "50",
    }
    url = f"{API}/discover/{config['rota']}"

    def buscar(generos_ids):
        params = dict(base)
        params["with_genres"] = generos_ids
        params["page"] = 1
        dados = requests.get(url, params=params, timeout=10).json()
        resultados = dados.get("results", [])
        total = min(dados.get("total_pages", 0), 3)
        if total > 1:
            params["page"] = random.randint(1, total)
            if params["page"] != 1:
                resultados = requests.get(url, params=params, timeout=10).json().get("results", resultados)
        return resultados

    # Combinações com um gênero de cada pessoa (as com gêneros repetidos primeiro).
    # Vírgula no TMDB = "todos esses gêneros ao mesmo tempo".
    combos = sorted(
        {frozenset(c) for c in itertools.product(*escolhas)},
        key=lambda s: (len(s), random.random()),
    )[:6]

    vitrine = []
    candidatos = []
    agradam = 0

    for combo in combos:
        achados = buscar(",".join(str(GENEROS[g][tipo]) for g in combo))
        if achados:
            vitrine = candidatos = achados
            agradam = n
            break

    # Plano B: junta todos os gêneros e escolhe o que agrada mais pessoas
    if not candidatos:
        uniao = {g for lista in escolhas for g in lista}
        achados = buscar("|".join(str(GENEROS[g][tipo]) for g in uniao))
        if achados:
            por_id = {str(GENEROS[g][tipo]): g for g in validos}

            def quantas(r):
                chaves = {por_id.get(str(i)) for i in r.get("genre_ids", [])}
                return sum(1 for lista in escolhas if chaves & set(lista))

            melhor = max(quantas(r) for r in achados)
            candidatos = [r for r in achados if quantas(r) == melhor]
            vitrine = achados
            agradam = melhor

    item = None
    relacionados = []
    if candidatos:
        bruto = random.choice(candidatos)
        item = normalizar(bruto, tipo)
        relacionados = [
            normalizar(r, tipo)
            for r in vitrine
            if r["id"] != bruto["id"] and r.get("poster_path")
        ][:6]

    filtros = {
        "tipo": tipo,
        "generos": sorted({g for lista in escolhas for g in lista}),
        "plataformas": [],
        "ano_de": "",
        "ano_ate": "",
        "pagina": 0,
        "esconder": False,
        "nota_min": "",
        "duracao_max": "",
        "classificacao": "",
    }

    campos = [("tipo", tipo), ("pessoas", str(n))]
    for i, lista in enumerate(escolhas, start=1):
        campos += [(f"p{i}", g) for g in lista]

    if agradam == n:
        mensagem = "🎉 Esse agrada todo mundo!"
    else:
        mensagem = f"Não achei nada que agrade todos. Esse agrada {agradam} de {n} pessoas."

    return render_template(
        "resultado.html",
        filme=item,
        relacionados=relacionados,
        filtros=filtros,
        pool=[],
        da_lista=False,
        grupo_msg=mensagem if item else None,
        grupo_campos=campos,
        **contexto_do_item(item, tipo),
    )

@app.route("/ranking")
def ranking():
    """Títulos mais bem avaliados pela comunidade do site."""
    linhas = buscar_todos(
        """
        SELECT tipo, tmdb_id, titulo, ano, poster,
               AVG(nota) AS media, COUNT(*) AS total
        FROM avaliacoes
        WHERE escondido = 0
        GROUP BY tipo, tmdb_id
        ORDER BY media DESC, total DESC, titulo ASC
        LIMIT 20
        """
    )
    lista = []
    for l in linhas:
        lista.append({
            "tipo": l["tipo"],
            "tmdb_id": l["tmdb_id"],
            "titulo": l["titulo"],
            "ano": l["ano"],
            "poster": l["poster"],
            "media": f"{l['media']:.1f}".replace(".", ","),
            "porcentagem": round(l["media"] * 20),
            "total": l["total"],
        })
    return render_template("ranking.html", lista=lista)


@app.route("/estatisticas")
def estatisticas():
    if "usuario_id" not in session:
        return redirect("/entrar")

    uid = session["usuario_id"]

    por_tipo = {
        l["tipo"]: l["total"]
        for l in buscar_todos(
            "SELECT tipo, COUNT(*) AS total FROM assistidos WHERE usuario_id = ? GROUP BY tipo",
            (uid,),
        )
    }
    filmes = por_tipo.get("filme", 0)
    series = por_tipo.get("serie", 0)
    total = filmes + series
    pct_filmes = round(filmes * 100 / total) if total else 0

    av = buscar_um(
        "SELECT COUNT(*) AS total, AVG(nota) AS media FROM avaliacoes WHERE usuario_id = ?",
        (uid,),
    )
    media = f"{av['media']:.1f}".replace(".", ",") if av["total"] else None

    contagem = {n: 0 for n in range(1, 6)}
    for l in buscar_todos(
        "SELECT nota, COUNT(*) AS total FROM avaliacoes WHERE usuario_id = ? GROUP BY nota",
        (uid,),
    ):
        contagem[l["nota"]] = l["total"]
    maior = max(contagem.values()) or 1
    distribuicao = [
        {"nota": n, "total": contagem[n], "pct": round(contagem[n] * 100 / maior)}
        for n in range(5, 0, -1)
    ]

    quero = buscar_um(
        "SELECT COUNT(*) AS total FROM quero_assistir WHERE usuario_id = ?", (uid,)
    )["total"]

    melhores = buscar_todos(
        """
        SELECT tipo, tmdb_id, titulo, ano, poster, nota
        FROM avaliacoes
        WHERE usuario_id = ?
          AND nota = (SELECT MAX(nota) FROM avaliacoes WHERE usuario_id = ?)
        ORDER BY atualizado_em DESC
        LIMIT 6
        """,
        (uid, uid),
    )

    conta = buscar_um("SELECT criado_em FROM usuarios WHERE id = ?", (uid,))
    d = conta["criado_em"] if conta else ""
    desde = f"{d[8:10]}/{d[5:7]}/{d[0:4]}" if d else ""

    return render_template(
        "estatisticas.html",
        total=total,
        filmes=filmes,
        series=series,
        pct_filmes=pct_filmes,
        avaliacoes=av["total"],
        media=media,
        distribuicao=distribuicao,
        quero=quero,
        melhores=melhores,
        desde=desde,
    )

@app.route("/minha-conta")
def minha_conta():
    if "usuario_id" not in session:
        return redirect("/entrar")

    uid = session["usuario_id"]
    minhas = buscar_todos(
        """
        SELECT tipo, tmdb_id, titulo, ano, nota, comentario
        FROM avaliacoes
        WHERE usuario_id = ?
        ORDER BY atualizado_em DESC, id DESC
        """,
        (uid,),
    )
    return render_template("minha_conta.html", minhas=minhas, admin=eh_admin())


@app.route("/minha-conta/senha", methods=["POST"])
def trocar_senha():
    if "usuario_id" not in session:
        return redirect("/entrar")

    uid = session["usuario_id"]
    nome = session["usuario"]
    atual = request.form.get("atual", "")
    nova = request.form.get("nova", "")
    confirmar = request.form.get("confirmar", "")

    if tentativas_recentes(nome) >= MAX_TENTATIVAS:
        flash(f"Muitas tentativas erradas. Aguarde {JANELA_MINUTOS} minutos e tente de novo.")
        return redirect("/minha-conta")

    pessoa = buscar_um("SELECT senha_hash FROM usuarios WHERE id = ?", (uid,))
    if not pessoa or not check_password_hash(pessoa["senha_hash"], atual):
        executar("INSERT INTO tentativas_login (nome) VALUES (?)", (nome,))
        flash("A senha atual está incorreta.")
    elif len(nova) < 8:
        flash("A nova senha precisa ter pelo menos 8 caracteres.")
    elif len(nova) > 128:
        flash("A nova senha pode ter no máximo 128 caracteres.")
    elif nova != confirmar:
        flash("As senhas novas não são iguais.")
    else:
        executar(
            "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
            (generate_password_hash(nova), uid),
        )
        flash("Senha alterada com sucesso.")
    return redirect("/minha-conta")


@app.route("/minha-conta/excluir", methods=["POST"])
def excluir_conta():
    if "usuario_id" not in session:
        return redirect("/entrar")

    uid = session["usuario_id"]
    nome = session["usuario"]
    senha = request.form.get("senha", "")

    if eh_admin():
        flash("A conta de administrador não pode ser excluída por aqui.")
        return redirect("/minha-conta")

    if tentativas_recentes(nome) >= MAX_TENTATIVAS:
        flash(f"Muitas tentativas erradas. Aguarde {JANELA_MINUTOS} minutos e tente de novo.")
        return redirect("/minha-conta")

    pessoa = buscar_um("SELECT senha_hash FROM usuarios WHERE id = ?", (uid,))
    if not pessoa or not check_password_hash(pessoa["senha_hash"], senha):
        executar("INSERT INTO tentativas_login (nome) VALUES (?)", (nome,))
        flash("Senha incorreta. A conta não foi excluída.")
        return redirect("/minha-conta")

    # Apaga tudo da pessoa de uma vez: ou apaga tudo, ou nada
    conexao = conectar()
    try:
        with conexao:
            conexao.execute(
                """
                DELETE FROM denuncias
                WHERE usuario_id = ?
                   OR avaliacao_id IN (SELECT id FROM avaliacoes WHERE usuario_id = ?)
                """,
                (uid, uid),
            )
            conexao.execute("DELETE FROM avaliacoes WHERE usuario_id = ?", (uid,))
            conexao.execute("DELETE FROM assistidos WHERE usuario_id = ?", (uid,))
            conexao.execute("DELETE FROM quero_assistir WHERE usuario_id = ?", (uid,))
            conexao.execute("DELETE FROM tentativas_login WHERE nome = ?", (nome,))
            conexao.execute("DELETE FROM usuarios WHERE id = ?", (uid,))
    finally:
        conexao.close()

    session.clear()
    flash("Sua conta e todos os seus dados foram excluídos.")
    return redirect("/")


def ids_assistidos(tipo):
    """Códigos dos títulos que a pessoa logada já marcou como assistidos."""
    if "usuario_id" not in session:
        return set()
    linhas = buscar_todos(
        "SELECT tmdb_id FROM assistidos WHERE usuario_id = ? AND tipo = ?",
        (session["usuario_id"], tipo),
    )
    return {l["tmdb_id"] for l in linhas}


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
    nota_min = request.form.get("nota_min", "").strip()
    duracao_max = request.form.get("duracao_max", "").strip()
    classificacao = request.form.get("classificacao", "").strip()
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

    # Terror em série: usa as tags de terror (série não tem esse gênero no TMDB)
    if tipo == "serie" and "terror" in selecionados:
        codigos_terror = codigos_de_terror()
        # "0" não existe: se as tags não forem encontradas, não sorteia nada
        params["with_keywords"] = "|".join(codigos_terror) if codigos_terror else "0"

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

    if nota_min in ("6", "7", "8"):
        params["vote_average.gte"] = nota_min
        params["vote_count.gte"] = "50"  # evita títulos com poucas notas
    if duracao_max in ("90", "120", "150"):
        params["with_runtime.lte"] = duracao_max
    if tipo == "filme" and classificacao in ("L", "10", "12", "14", "16"):
        params["certification_country"] = "BR"
        params["certification.lte"] = classificacao

    if nota_min in ("6", "7", "8"):
        params["vote_average.gte"] = nota_min
        params["vote_count.gte"] = "50"  # evita títulos com poucas notas
    if duracao_max in ("90", "120", "150"):
        params["with_runtime.lte"] = duracao_max
    if tipo == "filme" and classificacao in ("L", "10", "12", "14", "16"):
        params["certification_country"] = "BR"
        params["certification.lte"] = classificacao

    url = f"{API}/discover/{config['rota']}"

    # Descobre qual página usar: a mesma de antes (clique no pôster) ou uma aleatória
    esconder = request.form.get("esconder_vistos") == "1"
    vistos = ids_assistidos(tipo) if esconder else set()

    def sem_vistos(lista):
        """Tira os já assistidos (mas mantém o pôster clicado, se foi esse o pedido)."""
        return [r for r in lista if r["id"] not in vistos or str(r["id"]) == escolhido]

    resultados = []
    numero_pagina = 0

    if pagina.isdigit() and int(pagina) > 0:
        # Clicou num pôster: reaproveita a mesma página
        numero_pagina = int(pagina)
        params["page"] = numero_pagina
        resultados = sem_vistos(requests.get(url, params=params).json().get("results", []))
    else:
        dados = requests.get(url, params=params).json()
        total_paginas = min(dados.get("total_pages", 0), 500)
        if total_paginas > 0:
            lista = []
            # Tenta até 4 páginas aleatórias até achar algo que a pessoa ainda não viu
            for _ in range(4 if vistos else 1):
                numero_pagina = random.randint(1, total_paginas)
                params["page"] = numero_pagina
                lista = requests.get(url, params=params).json().get("results", [])
                resultados = sem_vistos(lista)
                if resultados:
                    break
            # Se tudo o que apareceu já foi assistido, sorteia mesmo assim
            if not resultados:
                resultados = lista

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
    ja_assistiu = False
    ja_quero = False
    trailer = None
    if item:
     onde_assistir, trailer = buscar_em_paralelo(item)
     meias_estrelas = int(item["nota"] + 0.5)

     if "usuario_id" in session:
            ja_assistiu = bool(buscar_um(
                "SELECT id FROM assistidos WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
                (session["usuario_id"], tipo, item["id"]),
            ))
            ja_quero = bool(buscar_um(
                "SELECT id FROM quero_assistir WHERE usuario_id = ? AND tipo = ? AND tmdb_id = ?",
                (session["usuario_id"], tipo, item["id"]),
            ))

    # Guarda os filtros para os botões da página de resultado reenviarem
    filtros = {
        "tipo": tipo,
        "generos": selecionados,
        "plataformas": plataformas,
        "ano_de": ano_de,
        "ano_ate": ano_ate,
        "pagina": numero_pagina,
        "esconder": esconder,
        "nota_min": nota_min,
        "duracao_max": duracao_max,
        "classificacao": classificacao,
    }

    return render_template(
        "resultado.html",
        filme=item,
        relacionados=relacionados,
        filtros=filtros,
        onde_assistir=onde_assistir,
        ja_assistiu=ja_assistiu,
        ja_quero=ja_quero,
        comunidade=nota_da_comunidade(tipo, item["id"]) if item else None,
        comentarios=comentarios_do_titulo(tipo, item["id"]) if item else [],
        trailer=trailer,
        pool=[],
        da_lista=False,
        porcentagem=meias_estrelas * 10,
        estrelas=meias_estrelas / 2,
    )

def eh_admin():
    uid = session.get("usuario_id")
    if not uid or not ADMIN:
        return False
    p = buscar_um("SELECT nome FROM usuarios WHERE id = ?", (uid,))
    return bool(p) and p["nome"].lower() == ADMIN.lower()

@app.route("/avaliacoes/<int:avaliacao_id>/denunciar", methods=["POST"])
def denunciar_avaliacao(avaliacao_id):
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return redirect(url_for("login"))

    linhas = buscar_todos(
        "SELECT usuario_id FROM avaliacoes WHERE id = ?", (avaliacao_id,)
    )
    
    if not linhas:
        abort(404)
    if linhas[0]["usuario_id"] == usuario_id:
        flash("Você não pode denunciar a sua própria avaliação.")
        return redirect(url_for("avaliacoes"))

    executar(
        "INSERT OR IGNORE INTO denuncias (avaliacao_id, usuario_id) VALUES (?, ?)",
        (avaliacao_id, usuario_id),
    )

    total = buscar_todos(
        "SELECT COUNT(*) AS total FROM denuncias WHERE avaliacao_id = ?",
        (avaliacao_id,),
    )[0]["total"]
    if total >= LIMITE_DENUNCIAS:
        executar("UPDATE avaliacoes SET escondido = 1 WHERE id = ?", (avaliacao_id,))

    flash("Denúncia enviada. Obrigado!")
    return redirect(url_for("avaliacoes"))


@app.route("/moderacao")
def moderacao():
    if not eh_admin():
        abort(404)
    lista = buscar_todos(
        """
        SELECT a.id, a.titulo, a.nota, a.comentario, a.escondido, u.nome AS autor,
               (SELECT COUNT(*) FROM denuncias d WHERE d.avaliacao_id = a.id) AS total
        FROM avaliacoes a
        JOIN usuarios u ON u.id = a.usuario_id
        WHERE a.escondido = 1
           OR EXISTS (SELECT 1 FROM denuncias d WHERE d.avaliacao_id = a.id)
        ORDER BY total DESC, a.atualizado_em DESC
        """
    )
    return render_template("moderacao.html", lista=lista)


@app.route("/moderacao/<int:avaliacao_id>/<acao>", methods=["POST"])
def moderar(avaliacao_id, acao):
    if not eh_admin():
        abort(404)
    if acao == "esconder":
        executar("UPDATE avaliacoes SET escondido = 1 WHERE id = ?", (avaliacao_id,))
    elif acao == "mostrar":
        executar("UPDATE avaliacoes SET escondido = 0 WHERE id = ?", (avaliacao_id,))
        executar("DELETE FROM denuncias WHERE avaliacao_id = ?", (avaliacao_id,))
    elif acao == "limpar":
        executar("DELETE FROM denuncias WHERE avaliacao_id = ?", (avaliacao_id,))
    else:
        abort(404)
    return redirect("/moderacao")


LIMITE_DENUNCIAS = 3


if __name__ == "__main__":
    app.run(debug=True)