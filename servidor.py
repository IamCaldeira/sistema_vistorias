"""
Levantamento da Frota Municipal - Prefeitura de Caratinga
VERSÃO 01 - Primeiro servidor

Um servidor web bem simples, só com o Python (nada para instalar).
Ele mostra uma página com uma lista FIXA de veículos, escrita aqui no código.

Uso: python servidor.py   e abra http://localhost:8000 no navegador
"""
from http.server import BaseHTTPRequestHandler, HTTPServer

PORTA = 8000

# Por enquanto os veículos ficam numa lista dentro do programa.
VEICULOS = [
    {"placa": "ABC1D23", "modelo": "Fiat Strada", "secretaria": "Saúde"},
    {"placa": "QWE4R56", "modelo": "VW Gol", "secretaria": "Educação"},
    {"placa": "RTY7U89", "modelo": "Toyota Hilux", "secretaria": "Obras"},
]


def montar_pagina():
    linhas = ""
    for v in VEICULOS:
        linhas += f"<tr><td>{v['placa']}</td><td>{v['modelo']}</td><td>{v['secretaria']}</td></tr>\n"
    return f"""<!doctype html>
<html lang="pt-br">
<head><meta charset="utf-8"><title>Frota Municipal</title></head>
<body>
<h1>Levantamento da Frota Municipal</h1>
<p>Prefeitura Municipal de Caratinga - Departamento de Patrimônio</p>
<table border="1">
<tr><th>Placa</th><th>Modelo</th><th>Secretaria</th></tr>
{linhas}</table>
<p>Total: {len(VEICULOS)} veículos</p>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = montar_pagina().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


if __name__ == "__main__":
    print(f"Servidor rodando em http://localhost:{PORTA}  (Ctrl+C para parar)")
    HTTPServer(("0.0.0.0", PORTA), Handler).serve_forever()
