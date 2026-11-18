"""
Levantamento da Frota Municipal - Prefeitura de Caratinga
Servidor local (painel no PC + vistoria no celular).

Uso:        python servidor.py
"""
import csv
import io
import json
import mimetypes
import re
import shutil
import sqlite3
import threading
import time
import traceback
import unicodedata
import webbrowser
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    from PIL import Image as PILImage, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
except ImportError:
    PILImage = None

RAIZ = Path(__file__).resolve().parent
WEB = RAIZ / "web"
DADOS = RAIZ / "dados"
FOTOS_DIR = DADOS / "fotos"
MINI_DIR = DADOS / "miniaturas"
EXCL_DIR = DADOS / "excluidos"
DB_PATH = DADOS / "frota.db"
PORTA_HTTP = 8000
LIMITE_UPLOAD = 60 * 1024 * 1024


# ------------------------------------------------------------- definições ----
ETAPAS = [
    {"id": "frente", "titulo": "Frente do veículo"},
    {"id": "traseira", "titulo": "Traseira do veículo"},
    {"id": "lateral_esquerda", "titulo": "Lateral esquerda"},
    {"id": "lateral_direita", "titulo": "Lateral direita"},
    {"id": "placa_patrimonio", "titulo": "Placa e/ou plaqueta de patrimônio"},
    {"id": "chassi_serie", "titulo": "Chassi ou plaqueta de série",
     "dica": "Quando estiver acessível."},
    {"id": "painel", "titulo": "Painel (odômetro/horímetro)"},
    {"id": "interior", "titulo": "Interior (bancos/cabine)"},
    {"id": "pneus", "titulo": "Pneus", "multi": True,
     "dica": "Recomendado: 2 fotos (lado esquerdo e lado direito). "
             "Máquinas e caminhões: quantas forem necessárias. Legenda opcional."},
    {"id": "avarias", "titulo": "Avarias visíveis", "multi": True, "avarias": True,
     "dica": "Uma foto por avaria, com legenda opcional (ex.: lanterna traseira "
             "quebrada). Se não houver, toque em “Sem avarias visíveis”."},
    {"id": "motor", "titulo": "Compartimento do motor (visão geral)"},
]
SITUACOES = ["Em uso", "Parado", "Em oficina", "Inservível / sucata", "Não localizado"]
TIPOS = ["Automóvel", "Caminhonete / Utilitário", "Van / Micro-ônibus", "Ônibus",
         "Ambulância", "Caminhão", "Motocicleta", "Trator", "Retroescavadeira",
         "Motoniveladora", "Pá-carregadeira", "Escavadeira", "Rolo compactador",
         "Implemento / Reboque", "Outro"]
CAD = ["patrimonio", "placa", "tipo", "marca_modelo", "ano", "chassi", "renavam",
       "secretaria", "local_cadastro", "responsavel", "situacao_cadastro"]
VIST = ["situacao", "odometro", "local_visto", "obs"]

ALIASES = {
    "patrimonio": ["patrimonio", "npatrimonio", "numpatrimonio", "numeropatrimonio",
                   "nrpatrimonio", "nopatrimonio", "tombo", "plaqueta", "codigo",
                   "codigopatrimonio", "patrimonionumero"],
    "placa": ["placa", "placas"],
    "tipo": ["tipo", "especie", "categoria", "tipodoveiculo", "tipoveiculo"],
    "marca_modelo": ["marcamodelo", "marcaemodelo", "veiculo", "descricao",
                     "modeloveiculo", "descricaodobem"],
    "_marca": ["marca", "fabricante"],
    "_modelo": ["modelo"],
    "ano": ["ano", "anofabricacao", "anomodelo", "anofab", "fabricacao", "anomodfab"],
    "chassi": ["chassi", "chassis", "chassiserie", "chassinserie", "chassiouserie",
               "nserie", "numerodeserie", "numeroserie", "serie", "nroserie", "nodeserie",
               "chassinumeroserie", "numerochassi", "nchassi"],
    "renavam": ["renavam"],
    "secretaria": ["secretaria", "orgao", "lotacao", "setor", "departamento",
                   "unidade", "secretariadestino", "secretariaresponsavel"],
    "local_cadastro": ["local", "localizacao", "garagem", "endereco", "localatual"],
    "responsavel": ["responsavel", "motorista", "operador", "condutor", "detentor"],
    "situacao_cadastro": ["situacao", "situacaocadastro", "status", "condicao",
                          "estado", "situacaoatual"],
}
# Segunda tentativa de reconhecer cabeçalhos: se o nome CONTÉM a palavra-chave
CONTEM = [("chassi", "chassi"), ("serie", "chassi"), ("renavam", "renavam"),
          ("patrimonio", "patrimonio"), ("placa", "placa"), ("secretaria", "secretaria"),
          ("responsavel", "responsavel"), ("motorista", "responsavel")]
# ---------------------------------------------------------------- utilidades --
def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", txt.lower())


def limpar_chave(txt):
    return re.sub(r"[^A-Z0-9]", "", str(txt or "").upper())[:20]


def calcular_chave(placa, patrimonio):
    p = limpar_chave(placa)
    if p in ("", "SEMPLACA", "SP", "NA", "NAO", "NT", "SPLACA"):
        p = ""
    if p:
        return p
    pat = limpar_chave(patrimonio)
    return "PAT" + pat if pat else ""


def txt(v):
    return "" if v is None else str(v).strip()


@contextmanager
def conexao():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


class RotaErro(Exception):
    def __init__(self, msg, codigo=400):
        super().__init__(msg)
        self.codigo = codigo


def iniciar_banco():
    for d in (DADOS, FOTOS_DIR, MINI_DIR, EXCL_DIR):
        d.mkdir(parents=True, exist_ok=True)
    with conexao() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS veiculos(
            id INTEGER PRIMARY KEY, chave TEXT NOT NULL UNIQUE,
            patrimonio TEXT, placa TEXT, tipo TEXT, marca_modelo TEXT, ano TEXT,
            chassi TEXT, renavam TEXT, secretaria TEXT, local_cadastro TEXT,
            responsavel TEXT, situacao_cadastro TEXT,
            origem TEXT DEFAULT 'importado', criado_em TEXT,
            status TEXT DEFAULT 'pendente', situacao TEXT, odometro TEXT,
            local_visto TEXT, obs TEXT, vistoriador_nome TEXT,
            vistoriador_matricula TEXT, inicio TEXT, fim TEXT,
            puladas TEXT DEFAULT '[]', sem_avarias INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fotos(
            id INTEGER PRIMARY KEY, veiculo_id INTEGER NOT NULL, etapa TEXT,
            seq INTEGER DEFAULT 1, arquivo TEXT, legenda TEXT,
            criado_em TEXT, usuario TEXT);
        """)


# ------------------------------------------------------------- regras de dados --
def fotos_do_veiculo(c, vid):
    return [dict(f) for f in c.execute(
        "SELECT * FROM fotos WHERE veiculo_id=? ORDER BY id", (vid,))]


def status_etapas(v, fotos):
    cont = Counter(f["etapa"] for f in fotos)
    puladas = set(json.loads(v["puladas"] or "[]"))
    res = []
    for e in ETAPAS:
        n = cont.get(e["id"], 0)
        if n:
            s = "ok"
        elif e.get("avarias") and v["sem_avarias"]:
            s = "sem_avarias"
        elif e["id"] in puladas:
            s = "pulada"
        else:
            s = "pendente"
        res.append({"id": e["id"], "titulo": e["titulo"], "status": s, "n": n,
                    "multi": bool(e.get("multi")), "avarias": bool(e.get("avarias")),
                    "dica": e.get("dica", "")})
    return res


def veiculo_dict(c, v, completo=False):
    fotos = fotos_do_veiculo(c, v["id"])
    d = dict(v)
    d["puladas_lista"] = json.loads(d.get("puladas") or "[]")
    d["n_fotos"] = len(fotos)
    etapas = status_etapas(v, fotos)
    d["etapas_feitas"] = sum(1 for e in etapas if e["status"] != "pendente")
    d["etapas_total"] = len(etapas)
    if completo:
        d["etapas"] = etapas
        for f in fotos:
            f["url"] = f"/fotos/{v['chave']}/{f['arquivo']}"
        d["fotos"] = fotos
    return d


def pasta_fotos(chave):
    return FOTOS_DIR / chave


def achar(c, vid):
    v = c.execute("SELECT * FROM veiculos WHERE id=?", (vid,)).fetchone()
    if not v:
        raise RotaErro("Veículo não encontrado", 404)
    return v


def apagar_arquivo_foto(chave, arquivo):
    (pasta_fotos(chave) / arquivo).unlink(missing_ok=True)
    for m in MINI_DIR.glob(f"{chave}__*__{arquivo}.jpg"):
        m.unlink(missing_ok=True)


def apagar_fotos_etapa(c, v, etapa):
    for f in c.execute("SELECT * FROM fotos WHERE veiculo_id=? AND etapa=?", (v["id"], etapa)).fetchall():
        apagar_arquivo_foto(v["chave"], f["arquivo"])
    c.execute("DELETE FROM fotos WHERE veiculo_id=? AND etapa=?", (v["id"], etapa))


def gravar_cadastro(c, v, dados):
    """Atualiza campos do cadastro. Renomeia a chave/pasta se a placa mudar."""
    novos = {k: txt(dados[k]) for k in CAD if k in dados}
    placa = novos.get("placa", v["placa"])
    pat = novos.get("patrimonio", v["patrimonio"])
    chave = calcular_chave(placa, pat)
    if not chave:
        raise RotaErro("Informe a placa ou o número de patrimônio.")
    if chave != v["chave"]:
        if c.execute("SELECT 1 FROM veiculos WHERE chave=? AND id<>?", (chave, v["id"])).fetchone():
            raise RotaErro(f"Já existe outro veículo com a identificação {chave}.")
        antiga, nova = pasta_fotos(v["chave"]), pasta_fotos(chave)
        if antiga.exists() and not nova.exists():
            antiga.rename(nova)
        novos["chave"] = chave
    if novos:
        sets = ", ".join(f"{k}=?" for k in novos)
        c.execute(f"UPDATE veiculos SET {sets} WHERE id=?", [*novos.values(), v["id"]])


# --------------------------------------------------------------- importação ----
def ler_planilha(nome, dados):
    """Retorna lista de listas (linhas) a partir de CSV ou XLSX."""
    if nome.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError:
            raise RotaErro("Para ler .xlsx instale o openpyxl (pip install openpyxl) "
                           "ou salve a planilha como CSV.")
        wb = openpyxl.load_workbook(io.BytesIO(dados), data_only=True, read_only=True)
        ws = wb.worksheets[0]
        return [["" if x is None else x for x in row] for row in ws.iter_rows(values_only=True)]
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = dados.decode("cp1252", errors="replace")
    primeira = texto.splitlines()[0] if texto.strip() else ""
    delim = max([";", ",", "\t"], key=primeira.count)
    return list(csv.reader(io.StringIO(texto), delimiter=delim))


def importar(c, nome, dados):
    linhas = ler_planilha(nome, dados)
    if len(linhas) < 2:
        raise RotaErro("A planilha está vazia ou só tem o cabeçalho.")
    mapa = {}
    for i, cab in enumerate(linhas[0]):
        n = norm(cab)
        for campo, nomes in ALIASES.items():
            if n in nomes and campo not in mapa:
                mapa[campo] = i
                break
    usadas = set(mapa.values())
    for i, cab in enumerate(linhas[0]):  # 2ª passada: nome que contém a palavra-chave
        if i in usadas or not txt(cab):
            continue
        n = norm(cab)
        for chave_busca, campo in CONTEM:
            if chave_busca in n and campo not in mapa:
                mapa[campo] = i
                usadas.add(i)
                break
    reconhecidas = {txt(linhas[0][i]): campo.lstrip("_") for campo, i in mapa.items()}
    ignoradas = [txt(cab) for i, cab in enumerate(linhas[0]) if i not in usadas and txt(cab)]
    if not any(k in mapa for k in ("placa", "patrimonio")):
        raise RotaErro("Não encontrei coluna de Placa nem de Patrimônio no cabeçalho. "
                       "Baixe o modelo de planilha para ver os nomes esperados.")
    ins = atu = com_chassi = com_renavam = 0
    erros = []
    for n, lin in enumerate(linhas[1:], start=2):
        if not any(txt(x) for x in lin):
            continue
        reg = {k: txt(lin[i]) if i < len(lin) else "" for k, i in mapa.items()}
        for k, val in reg.items():  # números vindos do Excel ("2020.0" -> "2020")
            if re.fullmatch(r"\d+\.0", val):
                reg[k] = val[:-2]
        if reg.get("renavam", "").isdigit() and len(reg["renavam"]) < 11:
            reg["renavam"] = reg["renavam"].zfill(11)  # Excel come o zero à esquerda
        com_chassi += bool(reg.get("chassi"))
        com_renavam += bool(reg.get("renavam"))
        if not reg.get("marca_modelo"):
            reg["marca_modelo"] = " ".join(x for x in (reg.get("_marca"), reg.get("_modelo")) if x)
        if reg.get("ano", "").endswith(".0"):
            reg["ano"] = reg["ano"][:-2]
        chave = calcular_chave(reg.get("placa"), reg.get("patrimonio"))
        if not chave:
            erros.append(f"Linha {n}: sem placa e sem patrimônio.")
            continue
        existente = c.execute("SELECT * FROM veiculos WHERE chave=?", (chave,)).fetchone()
        campos = {k: reg[k] for k in CAD if reg.get(k)}
        if existente:
            if campos:
                sets = ", ".join(f"{k}=?" for k in campos)
                c.execute(f"UPDATE veiculos SET {sets} WHERE id=?", [*campos.values(), existente["id"]])
            atu += 1
        else:
            cols = ["chave", "criado_em", "origem", *campos]
            c.execute(f"INSERT INTO veiculos({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [chave, agora(), "importado", *campos.values()])
            ins += 1
    return {"inseridos": ins, "atualizados": atu, "erros": erros[:30], "n_erros": len(erros),
            "com_chassi": com_chassi, "com_renavam": com_renavam,
            "reconhecidas": reconhecidas, "ignoradas": ignoradas}


def csv_bytes(cabecalho, linhas):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cabecalho)
    w.writerows(linhas)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# -------------------------------------------------------------------- servidor --
def tipo_imagem(dados):
    if dados[:2] == b"\xff\xd8":
        return "jpg"
    if dados[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if dados[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypheim", b"ftypheis", b"ftyphevc"):
        return "heic"
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "webp"
    return "jpg"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- respostas
    def enviar(self, corpo, tipo, codigo=200, extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def json(self, dados, codigo=200, extra=None):
        self.enviar(json.dumps(dados, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8", codigo, extra)

    def arquivo(self, caminho, nome_download=None, inline_nome=None, extra=None, tipo=None):
        caminho = Path(caminho)
        if not caminho.is_file():
            raise RotaErro("Arquivo não encontrado", 404)
        ext = caminho.suffix.lower()
        tipos = {".woff2": "font/woff2", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf"}
        tipo = tipo or tipos.get(ext) or mimetypes.guess_type(str(caminho))[0] or "application/octet-stream"
        cab = {"Cache-Control": "no-cache" if ext in (".html", ".js", ".css") else "max-age=600"}
        cab.update(extra or {})
        if nome_download:
            cab["Content-Disposition"] = f'attachment; filename="{nome_download}"'
        elif inline_nome:
            cab["Content-Disposition"] = f'inline; filename="{inline_nome}"'
        self.enviar(caminho.read_bytes(), tipo, 200, cab)

    def corpo_json(self):
        try:
            return json.loads(self.corpo.decode("utf-8") or "{}")
        except ValueError:
            raise RotaErro("Dados inválidos.")

    # ---- despacho
    def do_GET(self):
        self.despachar("GET")

    def do_POST(self):
        self.despachar("POST")

    def despachar(self, metodo):
        try:
            url = urlparse(self.path)
            self.caminho = unquote(url.path)
            self.q = {k: v[0] for k, v in parse_qs(url.query).items()}
            self.corpo = b""
            if metodo == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                if n > LIMITE_UPLOAD:
                    raise RotaErro("Arquivo grande demais.", 413)
                self.corpo = self.rfile.read(n)
            (self.rota_get if metodo == "GET" else self.rota_post)()
        except RotaErro as e:
            self.json({"ok": False, "erro": str(e)}, e.codigo)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self.json({"ok": False, "erro": f"Erro interno: {e}"}, 500)

    # ---- GET
    def rota_get(self):
        p = self.caminho
        if p == "/":
            return self.arquivo(WEB / "painel.html")
        if p in ("/m", "/m/"):
            return self.arquivo(WEB / "celular.html")
        if p.startswith("/web/"):
            alvo = (WEB / p[5:]).resolve()
            if WEB.resolve() not in alvo.parents:
                raise RotaErro("Não permitido", 403)
            return self.arquivo(alvo)
        if p.startswith("/fotos/"):
            return self.servir_foto(p)
        if p == "/api/config":
            return self.json(self.config())
        if p == "/api/veiculos":
            return self.json({"ok": True, "veiculos": self.listar()})
        m = re.fullmatch(r"/api/veiculos/(\d+)", p)
        if m:
            with conexao() as c:
                return self.json({"ok": True, "veiculo": veiculo_dict(c, achar(c, int(m[1])), True)})
        if p == "/api/modelo.csv":
            corpo = csv_bytes(["Patrimonio", "Placa", "Tipo", "Marca/Modelo", "Ano", "Chassi/Serie",
                               "Renavam", "Secretaria", "Local", "Responsavel", "Situacao"], [])
            return self.enviar(corpo, "text/csv; charset=utf-8", 200,
                               {"Content-Disposition": 'attachment; filename="modelo_importacao.csv"'})
        if p == "/api/exportar.csv":
            return self.exportar()
        raise RotaErro("Rota não encontrada", 404)

    def config(self):
        return {"ok": True, "etapas": ETAPAS, "situacoes": SITUACOES, "tipos": TIPOS}

    def listar(self):
        q, filtro = norm(self.q.get("q", "")), self.q.get("filtro", "todos")
        with conexao() as c:
            vs = [veiculo_dict(c, v) for v in c.execute("SELECT * FROM veiculos ORDER BY chave")]
        out = []
        for v in vs:
            if q and q not in norm(" ".join(txt(v[k]) for k in
                                            ("chave", "patrimonio", "placa", "marca_modelo", "secretaria", "tipo"))):
                continue
            if filtro in ("pendente", "em_andamento", "concluido") and v["status"] != filtro:
                continue
            out.append(v)
        return out

    def servir_foto(self, p):
        partes = p.split("/")
        if len(partes) != 4 or not re.fullmatch(r"[A-Z0-9]+", partes[2]) or not re.fullmatch(r"[\w.\-]+", partes[3]):
            raise RotaErro("Não permitido", 403)
        chave, arq = partes[2], partes[3]
        origem = pasta_fotos(chave) / arq
        w = int(self.q.get("w") or 0)
        if w and PILImage is not None and origem.is_file():
            mini = MINI_DIR / f"{chave}__{w}__{arq}.jpg"
            if not mini.exists():
                try:
                    with PILImage.open(origem) as im:
                        im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((w, w))
                    im.save(mini, "JPEG", quality=80)
                except Exception:
                    return self.arquivo(origem)
            return self.arquivo(mini)
        return self.arquivo(origem)

    def exportar(self):
        with conexao() as c:
            vs = [veiculo_dict(c, v) for v in c.execute("SELECT * FROM veiculos ORDER BY chave")]
        cab = ["identificacao", "patrimonio", "placa", "tipo", "marca_modelo", "ano", "chassi",
               "renavam", "secretaria", "local_cadastro", "responsavel", "situacao_cadastro",
               "status", "situacao_aparente", "odometro_horimetro", "local_verificado",
               "observacoes", "vistoriador", "matricula", "inicio", "fim",
               "n_fotos", "etapas_puladas", "origem"]
        linhas = []
        for v in vs:
            linhas.append([v["chave"], v["patrimonio"], v["placa"], v["tipo"], v["marca_modelo"],
                           v["ano"], v["chassi"], v["renavam"], v["secretaria"], v["local_cadastro"],
                           v["responsavel"], v["situacao_cadastro"], v["status"], v["situacao"],
                           v["odometro"], v["local_visto"], v["obs"], v["vistoriador_nome"],
                           v["vistoriador_matricula"], v["inicio"], v["fim"], v["n_fotos"], ",".join(v["puladas_lista"]), v["origem"]])
        nome = "frota_" + datetime.now().strftime("%Y%m%d_%H%M") + ".csv"
        self.enviar(csv_bytes(cab, linhas), "text/csv; charset=utf-8", 200,
                    {"Content-Disposition": f'attachment; filename="{nome}"'})

    # ---- POST
    def rota_post(self):
        p = self.caminho
        if p == "/api/foto":
            return self.salvar_foto()
        if p == "/api/importar":
            nome = self.q.get("nome", "planilha.csv")
            with conexao() as c:
                return self.json({"ok": True, **importar(c, nome, self.corpo)})
        if p == "/api/veiculos":
            return self.criar_veiculo()
        m = re.fullmatch(r"/api/foto/(\d+)/(apagar|legenda)", p)
        if m:
            return self.acao_foto(int(m[1]), m[2])
        m = re.fullmatch(r"/api/veiculos/(\d+)/(\w+)", p)
        if m:
            return self.acao_veiculo(int(m[1]), m[2])
        raise RotaErro("Rota não encontrada", 404)

    # ---- veículos
    def criar_veiculo(self):
        d = self.corpo_json()
        origem = "campo" if d.get("origem") == "campo" else "painel"
        chave = calcular_chave(d.get("placa"), d.get("patrimonio"))
        if not chave:
            raise RotaErro("Informe a placa ou o número de patrimônio.")
        campos = {k: txt(d[k]) for k in CAD if txt(d.get(k))}
        with conexao() as c:
            if c.execute("SELECT 1 FROM veiculos WHERE chave=?", (chave,)).fetchone():
                raise RotaErro(f"Já existe um veículo com a identificação {chave}.")
            cols = ["chave", "criado_em", "origem", *campos]
            cur = c.execute(f"INSERT INTO veiculos({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                            [chave, agora(), origem, *campos.values()])
            vid = cur.lastrowid
        self.json({"ok": True, "id": vid, "chave": chave})

    def acao_veiculo(self, vid, acao):
        d = self.corpo_json() if self.corpo else {}
        # quem está vistoriando: nome e matrícula digitados no celular
        u = {"nome": txt(d.get("vistoriador_nome")), "matricula": txt(d.get("vistoriador_matricula"))}
        with conexao() as c:
            v = achar(c, vid)
            if acao == "salvar":
                gravar_cadastro(c, v, d)
                sets = {k: txt(d[k]) for k in VIST if k in d}
                if sets:
                    c.execute(f"UPDATE veiculos SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                              [*sets.values(), vid])
            elif acao == "iniciar":
                if isinstance(d.get("cadastro"), dict):
                    gravar_cadastro(c, v, d["cadastro"])
                    v = achar(c, vid)
                if not txt(d.get("situacao")):
                    raise RotaErro("Informe a situação aparente do veículo.")
                sets = {k: txt(d.get(k)) for k in VIST}
                sets["status"] = "em_andamento" if v["status"] != "concluido" else v["status"]
                if u["nome"] and (v["status"] == "pendente" or not v["vistoriador_nome"]):
                    sets["vistoriador_nome"], sets["vistoriador_matricula"] = u["nome"], u["matricula"]
                if not v["inicio"]:
                    sets["inicio"] = agora()
                c.execute(f"UPDATE veiculos SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                          [*sets.values(), vid])
            elif acao == "pular":
                etapa = d.get("etapa")
                if etapa not in {e["id"] for e in ETAPAS}:
                    raise RotaErro("Etapa inválida.")
                lista = set(json.loads(v["puladas"] or "[]")) | {etapa}
                c.execute("UPDATE veiculos SET puladas=? WHERE id=?", (json.dumps(sorted(lista)), vid))
            elif acao == "sem_avarias":
                if c.execute("SELECT 1 FROM fotos WHERE veiculo_id=? AND etapa='avarias'", (vid,)).fetchone():
                    raise RotaErro("Já existem fotos de avarias para este veículo.")
                c.execute("UPDATE veiculos SET sem_avarias=1 WHERE id=?", (vid,))
            elif acao == "refazer_etapa":
                etapa = d.get("etapa")
                apagar_fotos_etapa(c, v, etapa)
                lista = [e for e in json.loads(v["puladas"] or "[]") if e != etapa]
                c.execute("UPDATE veiculos SET puladas=?, status=CASE WHEN status='concluido' THEN 'em_andamento' ELSE status END, "
                          "sem_avarias=CASE WHEN ?='avarias' THEN 0 ELSE sem_avarias END WHERE id=?",
                          (json.dumps(lista), etapa, vid))
            elif acao == "concluir":
                if not txt(v["situacao"]):
                    raise RotaErro("Informe a situação aparente antes de concluir.")
                if v["situacao"] != "Não localizado":
                    faltam = [e["titulo"] for e in status_etapas(v, fotos_do_veiculo(c, vid))
                              if e["status"] == "pendente"]
                    if faltam:
                        raise RotaErro("Faltam etapas: " + "; ".join(faltam))
                sets = {"status": "concluido", "fim": agora()}
                if not v["vistoriador_nome"] and u["nome"]:
                    sets["vistoriador_nome"], sets["vistoriador_matricula"] = u["nome"], u["matricula"]
                c.execute(f"UPDATE veiculos SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                          [*sets.values(), vid])
            elif acao == "reiniciar":
                for f in c.execute("SELECT * FROM fotos WHERE veiculo_id=?", (vid,)).fetchall():
                    apagar_arquivo_foto(v["chave"], f["arquivo"])
                c.execute("DELETE FROM fotos WHERE veiculo_id=?", (vid,))
                c.execute("UPDATE veiculos SET status='pendente', situacao=NULL, odometro=NULL, local_visto=NULL, "
                          "obs=NULL, "
                          "vistoriador_nome=NULL, vistoriador_matricula=NULL, inicio=NULL, fim=NULL, "
                          "puladas='[]', sem_avarias=0 WHERE id=?", (vid,))
            elif acao == "excluir":
                pasta = pasta_fotos(v["chave"])
                if pasta.exists():
                    shutil.move(str(pasta), str(EXCL_DIR / f"{v['chave']}_{int(time.time())}"))
                c.execute("DELETE FROM fotos WHERE veiculo_id=?", (vid,))
                c.execute("DELETE FROM veiculos WHERE id=?", (vid,))
                return self.json({"ok": True})
            else:
                raise RotaErro("Ação inválida.", 404)
            return self.json({"ok": True, "veiculo": veiculo_dict(c, achar(c, vid), True)})

    def salvar_foto(self):
        vid, etapa = int(self.q.get("id") or 0), self.q.get("etapa", "")
        idx = next((i for i, e in enumerate(ETAPAS) if e["id"] == etapa), None)
        if idx is None:
            raise RotaErro("Etapa inválida.")
        if not self.corpo:
            raise RotaErro("Foto vazia.")
        with conexao() as c:
            v = achar(c, vid)
            e = ETAPAS[idx]
            if not e.get("multi"):
                apagar_fotos_etapa(c, v, etapa)
            seq = (c.execute("SELECT COALESCE(MAX(seq),0) FROM fotos WHERE veiculo_id=? AND etapa=?",
                             (vid, etapa)).fetchone()[0]) + 1
            ext = tipo_imagem(self.corpo)
            nome = (f"{idx + 1:02d}_{etapa}_{seq:02d}.{ext}" if e.get("multi")
                    else f"{idx + 1:02d}_{etapa}.{ext}")
            pasta = pasta_fotos(v["chave"])
            pasta.mkdir(parents=True, exist_ok=True)
            (pasta / nome).write_bytes(self.corpo)
            c.execute("INSERT INTO fotos(veiculo_id, etapa, seq, arquivo, legenda, criado_em, usuario) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (vid, etapa, seq, nome, txt(self.q.get("legenda")), agora(),
                       txt(self.q.get("vistoriador"))))
            lista = [x for x in json.loads(v["puladas"] or "[]") if x != etapa]
            c.execute("UPDATE veiculos SET puladas=?, sem_avarias=CASE WHEN ?='avarias' THEN 0 ELSE sem_avarias END, "
                      "status=CASE WHEN status='pendente' THEN 'em_andamento' ELSE status END WHERE id=?",
                      (json.dumps(lista), etapa, vid))
            print(f"   {v['chave']}/{nome} ({len(self.corpo) // 1024} KB)")
            self.json({"ok": True, "veiculo": veiculo_dict(c, achar(c, vid), True)})

    def acao_foto(self, fid, acao):
        d = self.corpo_json() if self.corpo else {}
        with conexao() as c:
            f = c.execute("SELECT * FROM fotos WHERE id=?", (fid,)).fetchone()
            if not f:
                raise RotaErro("Foto não encontrada", 404)
            v = achar(c, f["veiculo_id"])
            if acao == "apagar":
                apagar_arquivo_foto(v["chave"], f["arquivo"])
                c.execute("DELETE FROM fotos WHERE id=?", (fid,))
            else:
                c.execute("UPDATE fotos SET legenda=? WHERE id=?", (txt(d.get("legenda")), fid))
            self.json({"ok": True, "veiculo": veiculo_dict(c, achar(c, v["id"]), True)})


class Servidor(ThreadingHTTPServer):
    daemon_threads = True


def principal():
    iniciar_banco()
    print("=" * 62)
    print("  LEVANTAMENTO DA FROTA MUNICIPAL - PREFEITURA DE CARATINGA")
    print("=" * 62)

    http = Servidor(("0.0.0.0", PORTA_HTTP), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()

    print(f"\n  Painel (neste PC):  http://localhost:{PORTA_HTTP}")
    print(f"  Celular:            http://IP-DO-NOTEBOOK:{PORTA_HTTP}/m   (veja o IP com o comando ipconfig)")
    print(f"  Dados salvos em:    {DADOS}")
    print("\n  Deixe esta janela aberta durante o levantamento. Ctrl+C para encerrar.\n")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORTA_HTTP}")).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    principal()
