"""
Levantamento da Frota Municipal - Prefeitura de Caratinga
VERSÃO 02 - Banco de dados

Novidades:
  - os veículos agora ficam salvos num banco de dados SQLite (dados/frota.db);
  - um formulário na página cadastra veículos novos.

Uso: python servidor.py   e abra http://localhost:8000 no navegador
"""
import sqlite3
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

PORTA = 8000
DADOS = Path(__file__).resolve().parent / "dados"
DB_PATH = DADOS / "frota.db"


def conectar():
    return sqlite3.connect(DB_PATH)


def iniciar_banco():
    DADOS.mkdir(exist_ok=True)
    with conectar() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS veiculos(
                       id INTEGER PRIMARY KEY,
                       placa TEXT NOT NULL,
                       marca_modelo TEXT,
                       secretaria TEXT)""")


def montar_pagina(erro=""):
    with conectar() as c:
        veiculos = c.execute("SELECT placa, marca_modelo, secretaria FROM veiculos ORDER BY placa").fetchall()
    linhas = ""
    for placa, modelo, secretaria in veiculos:
        # escape() evita que um texto digitado "quebre" a página (ex.: um < no nome)
        linhas += f"<tr><td>{escape(placa)}</td><td>{escape(modelo or '')}</td><td>{escape(secretaria or '')}</td></tr>\n"
    if not veiculos:
        linhas = '<tr><td colspan="3">Nenhum veículo cadastrado.</td></tr>'
    return f"""<!doctype html>
<html lang="pt-br">
<head><meta charset="utf-8"><title>Frota Municipal</title></head>
<body>
<h1>Levantamento da Frota Municipal</h1>
<p>Prefeitura Municipal de Caratinga - Departamento de Patrimônio</p>

<h2>Novo veículo</h2>
<form method="post" action="/novo">
  <p>Placa: <input name="placa"></p>
  <p>Marca / Modelo: <input name="marca_modelo"></p>
  <p>Secretaria: <input name="secretaria"></p>
  <p><button>Cadastrar</button></p>
</form>
<p><b>{escape(erro)}</b></p>

<h2>Veículos cadastrados ({len(veiculos)})</h2>
<table border="1">
<tr><th>Placa</th><th>Marca / Modelo</th><th>Secretaria</th></tr>
{linhas}</table>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def responder(self, html, codigo=200):
        corpo = html.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def voltar_para_inicio(self):
        # depois de salvar, manda o navegador de volta para a lista (evita cadastrar 2x ao atualizar)
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.responder(montar_pagina())

    def do_POST(self):
        if self.path != "/novo":
            return self.responder("Página não encontrada", 404)
        tamanho = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(tamanho).decode("utf-8"))
        placa = form.get("placa", [""])[0].strip().upper()
        modelo = form.get("marca_modelo", [""])[0].strip()
        secretaria = form.get("secretaria", [""])[0].strip()
        if not placa:
            return self.responder(montar_pagina("Informe a placa."))
        with conectar() as c:
            c.execute("INSERT INTO veiculos(placa, marca_modelo, secretaria) VALUES(?,?,?)",
                      (placa, modelo, secretaria))
        self.voltar_para_inicio()


if __name__ == "__main__":
    iniciar_banco()
    print(f"Servidor rodando em http://localhost:{PORTA}  (Ctrl+C para parar)")
    HTTPServer(("0.0.0.0", PORTA), Handler).serve_forever()
