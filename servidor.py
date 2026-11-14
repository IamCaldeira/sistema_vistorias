"""
Levantamento da Frota Municipal - Prefeitura de Caratinga
VERSÃO 03 - Cadastro completo

Novidades:
  - todos os campos do cadastro patrimonial (patrimônio, tipo, ano, chassi, renavam...);
  - identificação única do veículo: a placa ou, se não tiver, "PAT" + nº de patrimônio;
  - editar e excluir veículos;
  - busca por placa, patrimônio, modelo ou secretaria.

Uso: python servidor.py   e abra http://localhost:8000 no navegador
"""
import re
import sqlite3
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

PORTA = 8000
DADOS = Path(__file__).resolve().parent / "dados"
DB_PATH = DADOS / "frota.db"

TIPOS = ["Automóvel", "Caminhonete / Utilitário", "Van / Micro-ônibus", "Ônibus",
         "Ambulância", "Caminhão", "Motocicleta", "Trator", "Retroescavadeira",
         "Motoniveladora", "Pá-carregadeira", "Escavadeira", "Rolo compactador",
         "Implemento / Reboque", "Outro"]
# (coluna no banco, rótulo na tela)
CAMPOS = [("patrimonio", "Nº de patrimônio"), ("placa", "Placa"), ("tipo", "Tipo"),
          ("marca_modelo", "Marca / Modelo"), ("ano", "Ano"), ("chassi", "Chassi / Nº de série"),
          ("renavam", "Renavam"), ("secretaria", "Secretaria"), ("local_cadastro", "Local"),
          ("responsavel", "Responsável / Motorista"), ("situacao_cadastro", "Situação no cadastro")]
NA_LISTA = ["chave", "patrimonio", "tipo", "marca_modelo", "ano", "secretaria"]


class ErroCadastro(Exception):
    pass


def conectar():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row   # permite usar linha["placa"]
    return c


def iniciar_banco():
    DADOS.mkdir(exist_ok=True)
    with conectar() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS veiculos(
                       id INTEGER PRIMARY KEY, chave TEXT NOT NULL UNIQUE,
                       patrimonio TEXT, placa TEXT, tipo TEXT, marca_modelo TEXT, ano TEXT,
                       chassi TEXT, renavam TEXT, secretaria TEXT, local_cadastro TEXT,
                       responsavel TEXT, situacao_cadastro TEXT, criado_em TEXT)""")


def limpar(txt):
    """Só letras maiúsculas e números: 'abc-1d23' vira 'ABC1D23'."""
    return re.sub(r"[^A-Z0-9]", "", str(txt or "").upper())


def calcular_chave(placa, patrimonio):
    p = limpar(placa)
    if p in ("SEMPLACA", "SP", "NA"):
        p = ""
    if p:
        return p
    pat = limpar(patrimonio)
    return "PAT" + pat if pat else ""


def salvar(dados, vid=None):
    """Cadastra (vid=None) ou atualiza um veículo. Devolve a chave."""
    chave = calcular_chave(dados.get("placa"), dados.get("patrimonio"))
    if not chave:
        raise ErroCadastro("Informe a placa ou o número de patrimônio.")
    with conectar() as c:
        repetido = c.execute("SELECT id FROM veiculos WHERE chave=? AND id<>?", (chave, vid or 0)).fetchone()
        if repetido:
            raise ErroCadastro(f"Já existe um veículo com a identificação {chave}.")
        valores = [dados.get(k, "").strip() for k, _ in CAMPOS]
        if vid is None:
            c.execute(f"INSERT INTO veiculos(chave, criado_em, {', '.join(k for k, _ in CAMPOS)}) "
                      f"VALUES(?, ?, {', '.join('?' for _ in CAMPOS)})",
                      [chave, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *valores])
        else:
            c.execute(f"UPDATE veiculos SET chave=?, {', '.join(k + '=?' for k, _ in CAMPOS)} WHERE id=?",
                      [chave, *valores, vid])
    return chave


# ------------------------------------------------------------------ páginas --
def pagina(titulo, conteudo):
    return f"""<!doctype html>
<html lang="pt-br">
<head><meta charset="utf-8"><title>{escape(titulo)} - Frota Municipal</title></head>
<body>
<h1>Levantamento da Frota Municipal</h1>
<p>Prefeitura Municipal de Caratinga - Departamento de Patrimônio</p>
<hr>
{conteudo}
</body>
</html>"""


def formulario(acao, v=None, botao="Salvar"):
    v = v or {}
    html = f'<form method="post" action="{acao}">\n'
    for k, rotulo in CAMPOS:
        valor = escape(v[k] or "") if k in v.keys() else ""
        if k == "tipo":
            opcoes = "".join(f"<option {'selected' if t == valor else ''}>{escape(t)}</option>" for t in [""] + TIPOS)
            html += f'<p>{rotulo}: <select name="tipo">{opcoes}</select></p>\n'
        else:
            html += f'<p>{rotulo}: <input name="{k}" value="{valor}"></p>\n'
    return html + f"<p><button>{botao}</button></p>\n</form>"


def pagina_lista(busca="", erro="", aviso=""):
    with conectar() as c:
        todos = c.execute("SELECT * FROM veiculos ORDER BY chave").fetchall()
    b = busca.lower()
    veiculos = [v for v in todos if not b or b in " ".join(str(v[k] or "") for k in
                                                             ("chave", "patrimonio", "placa", "marca_modelo", "secretaria")).lower()]
    linhas = "".join(
        "<tr>" + "".join(f"<td>{escape(v[k] or '')}</td>" for k in NA_LISTA)
        + f'<td><a href="/editar?id={v["id"]}">editar</a></td></tr>\n' for v in veiculos)
    if not veiculos:
        linhas = f'<tr><td colspan="{len(NA_LISTA) + 1}">Nenhum veículo encontrado.</td></tr>'
    conteudo = f"""
<p><b>{escape(erro)}</b> {escape(aviso)}</p>
<form method="get" action="/">Buscar: <input name="q" value="{escape(busca)}"> <button>Buscar</button> <a href="/">limpar</a></form>
<h2>Veículos e máquinas ({len(veiculos)} de {len(todos)})</h2>
<table border="1">
<tr><th>Identificação</th><th>Patrimônio</th><th>Tipo</th><th>Marca / Modelo</th><th>Ano</th><th>Secretaria</th><th></th></tr>
{linhas}</table>
<h2>Novo veículo / máquina</h2>
{formulario("/novo", botao="Cadastrar")}"""
    return pagina("Veículos", conteudo)


def pagina_editar(vid, erro=""):
    with conectar() as c:
        v = c.execute("SELECT * FROM veiculos WHERE id=?", (vid,)).fetchone()
    if not v:
        return pagina_lista(erro="Veículo não encontrado.")
    conteudo = f"""
<p><a href="/">voltar para a lista</a></p>
<h2>Editar {escape(v["chave"])}</h2>
<p><b>{escape(erro)}</b></p>
{formulario(f"/salvar?id={vid}", v)}
<form method="post" action="/excluir?id={vid}" onsubmit="return confirm('Excluir este veículo?')">
  <button>Excluir veículo</button>
</form>"""
    return pagina("Editar", conteudo)


# ----------------------------------------------------------------- servidor --
class Handler(BaseHTTPRequestHandler):
    def responder(self, html, codigo=200):
        corpo = html.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def ir_para(self, endereco):
        self.send_response(303)
        self.send_header("Location", endereco)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            return self.responder(pagina_lista(q.get("q", [""])[0], aviso=q.get("aviso", [""])[0]))
        if url.path == "/editar":
            return self.responder(pagina_editar(int(q.get("id", ["0"])[0])))
        self.responder(pagina("Erro", "<p>Página não encontrada.</p>"), 404)

    def do_POST(self):
        url = urlparse(self.path)
        vid = int(parse_qs(url.query).get("id", ["0"])[0])
        tamanho = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(tamanho).decode("utf-8"), keep_blank_values=True).items()}
        try:
            if url.path == "/novo":
                chave = salvar(form)
                return self.ir_para("/?aviso=" + quote(f"{chave} cadastrado."))
            if url.path == "/salvar":
                chave = salvar(form, vid)
                return self.ir_para("/?aviso=" + quote(f"{chave} atualizado."))
            if url.path == "/excluir":
                with conectar() as c:
                    c.execute("DELETE FROM veiculos WHERE id=?", (vid,))
                return self.ir_para("/?aviso=" + quote("Veículo excluído."))
        except ErroCadastro as e:
            return self.responder(pagina_editar(vid, str(e)) if vid else pagina_lista(erro=str(e)))
        self.responder(pagina("Erro", "<p>Página não encontrada.</p>"), 404)


if __name__ == "__main__":
    iniciar_banco()
    print(f"Servidor rodando em http://localhost:{PORTA}  (Ctrl+C para parar)")
    HTTPServer(("0.0.0.0", PORTA), Handler).serve_forever()
