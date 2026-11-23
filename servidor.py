"""
Levantamento da Frota Municipal - Prefeitura de Caratinga
Servidor local (painel no PC + vistoria no celular).

Uso:        python servidor.py
Preparação: python servidor.py --preparar   (cria o certificado)
"""
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from html import escape as esc_html
from urllib.parse import parse_qs, quote, unquote, urlparse

try:
    import relatorio
except Exception as _e:  # reportlab/Pillow ausentes
    relatorio = None
    ERRO_RELATORIO = str(_e)
else:
    ERRO_RELATORIO = ""
try:
    import segno
except ImportError:
    segno = None
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
REL_DIR = DADOS / "relatorios"
MINI_DIR = DADOS / "miniaturas"
EXCL_DIR = DADOS / "excluidos"
CERT_DIR = DADOS / "certificado"
DB_PATH = DADOS / "frota.db"
PORTA_HTTP = 8000
PORTA_HTTPS = 8443
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
VIST = ["situacao", "odometro", "local_visto", "uso_local", "motorista", "plotado", "placa_legivel", "obs"]
ITENS = [("estepe", "Estepe"), ("triangulo", "Triângulo"), ("macaco", "Macaco"),
         ("chave_roda", "Chave de roda")]
PERFIS = ("admin", "vistoriador")
GPS_RUIM = 50  # metros
# O certificado local só vale para estas redes (segurança: não serve para outros sites)
REDES_PERMITIDAS = ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
TENTATIVAS = {}  # matrícula -> (erros, bloqueado_até)

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
ROTULO_FLAGS = {
    "nao_localizado": "Não localizado", "campo": "Cadastrado em campo",
    "sem_gps": "Sem GPS", "gps_impreciso": "GPS impreciso",
    "puladas": "Fotos puladas", "avarias": "Com avarias",
    "itens_faltando": "Itens de segurança ausentes", "placa_ilegivel": "Placa ilegível",
}

# ---------------------------------------------------------------- utilidades --
def agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", txt.lower())


def limpar_chave(txt):
    return re.sub(r"[^A-Z0-9]", "", str(txt or "").upper())[:20]


def chave_mat(m):
    """Matrícula só com letras e números, para comparar: '22.222-2' e '222222' são a mesma."""
    return re.sub(r"[^A-Z0-9]", "", str(m or "").upper())


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


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def segredo():
    f = DADOS / "segredo.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip().encode()


def hash_senha(senha, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return h, salt


def confere_senha(senha, h, salt):
    if not h or not salt:
        return False
    return hmac.compare_digest(hash_senha(senha, salt)[0], h)


def iniciar_banco():
    for d in (DADOS, FOTOS_DIR, REL_DIR, MINI_DIR, EXCL_DIR, CERT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    with conexao() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios(
            id INTEGER PRIMARY KEY, nome TEXT NOT NULL, matricula TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS sessoes(
            token TEXT PRIMARY KEY, nome TEXT, matricula TEXT, criado TEXT);
        CREATE TABLE IF NOT EXISTS veiculos(
            id INTEGER PRIMARY KEY, chave TEXT NOT NULL UNIQUE,
            patrimonio TEXT, placa TEXT, tipo TEXT, marca_modelo TEXT, ano TEXT,
            chassi TEXT, renavam TEXT, secretaria TEXT, local_cadastro TEXT,
            responsavel TEXT, situacao_cadastro TEXT,
            origem TEXT DEFAULT 'importado', criado_em TEXT,
            status TEXT DEFAULT 'pendente', situacao TEXT, odometro TEXT,
            local_visto TEXT, obs TEXT, vistoriador_nome TEXT,
            vistoriador_matricula TEXT, inicio TEXT, fim TEXT,
            lat REAL, lon REAL, acc REAL,
            puladas TEXT DEFAULT '[]', sem_avarias INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fotos(
            id INTEGER PRIMARY KEY, veiculo_id INTEGER NOT NULL, etapa TEXT,
            seq INTEGER DEFAULT 1, arquivo TEXT, legenda TEXT,
            lat REAL, lon REAL, acc REAL, criado_em TEXT, usuario TEXT);
        CREATE TABLE IF NOT EXISTS relatorios(
            id INTEGER PRIMARY KEY, numero TEXT UNIQUE, tipo TEXT, referencia TEXT,
            arquivo TEXT, sha256 TEXT, codigo TEXT, gerado_em TEXT,
            usuario_nome TEXT, usuario_matricula TEXT);
        """)
        # migração: colunas acrescentadas depois da primeira versão
        def garantir(tabela, colunas):
            existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabela})")}
            for nome, tipo in colunas:
                if nome not in existentes:
                    c.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
        garantir("veiculos", [("uso_local", "TEXT"), ("motorista", "TEXT"),
                              ("plotado", "TEXT"), ("itens", "TEXT"),
                              ("placa_legivel", "TEXT")])
        garantir("usuarios", [("senha_hash", "TEXT"), ("salt", "TEXT"),
                              ("perfil", "TEXT DEFAULT 'vistoriador'"), ("ativo", "INTEGER DEFAULT 1"),
                              ("matricula_chave", "TEXT")])
        for r in c.execute("SELECT id, matricula FROM usuarios WHERE matricula_chave IS NULL").fetchall():
            c.execute("UPDATE usuarios SET matricula_chave=? WHERE id=?", (chave_mat(r["matricula"]), r["id"]))
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_matchave ON usuarios(matricula_chave)")
        except sqlite3.IntegrityError:
            print("  [!] Há usuários com matrículas equivalentes (ex.: '12.345' e '12345'); ajuste pelo painel.")
    segredo()


# ---------------------------------------------------------- rede / certificado --
def ips_locais():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    ips = [i for i in ips if not i.startswith("127.") and not i.startswith("169.254.")]

    def ordem(ip):
        if ip.startswith("192.168.137."):
            return 0
        return 1 if ip.startswith("192.168.") else 2
    ips.sort(key=lambda i: (ordem(i), i))
    return ips or ["192.168.137.1"]


def preparar_certificado():
    """Cria a autoridade certificadora local (uma vez) e o certificado do servidor.

    O celular instala a autoridade UMA vez (arquivo ca.pem) e passa a confiar no
    endereço https do notebook: sem avisos, com GPS e com modo offline (PWA).
    Se o IP mudar, só o certificado do servidor é refeito; o celular não precisa
    instalar nada de novo. Retorna (cert, chave) ou None.
    """
    try:
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError:
        return None
    try:
        ca_crt, ca_key = CERT_DIR / "ca.pem", CERT_DIR / "ca-chave.pem"
        cert, chave, lista = CERT_DIR / "cert.pem", CERT_DIR / "chave.pem", CERT_DIR / "ips.json"
        t0 = datetime.now(timezone.utc)
        redes = [ipaddress.ip_network(r) for r in REDES_PERMITIDAS]
        pem = serialization.Encoding.PEM

        if not (ca_crt.exists() and ca_key.exists()):
            cakey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            nome = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Autoridade Local - Frota Caratinga"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Prefeitura Municipal de Caratinga")])
            ca = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
                  .public_key(cakey.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(t0 - timedelta(days=1)).not_valid_after(t0 + timedelta(days=3650))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                  .add_extension(x509.KeyUsage(
                      digital_signature=False, content_commitment=False, key_encipherment=False,
                      data_encipherment=False, key_agreement=False, key_cert_sign=True,
                      crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
                  .add_extension(x509.NameConstraints(
                      permitted_subtrees=[x509.DNSName("localhost")] +
                                         [x509.IPAddress(r) for r in redes],
                      excluded_subtrees=None), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(cakey.public_key()),
                                 critical=False)
                  .sign(cakey, hashes.SHA256()))
            ca_key.write_bytes(cakey.private_bytes(
                pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            ca_crt.write_bytes(ca.public_bytes(pem))
            for f in (cert, chave, lista):  # certificado antigo deixa de valer
                f.unlink(missing_ok=True)

        ca_cert = x509.load_pem_x509_certificate(ca_crt.read_bytes())
        ca_priv = serialization.load_pem_private_key(ca_key.read_bytes(), password=None)
        ips = [i for i in sorted(set(ips_locais()) | {"127.0.0.1", "192.168.137.1"})
               if any(ipaddress.ip_address(i) in r for r in redes)]

        valido = False
        if cert.exists() and chave.exists() and lista.exists():
            try:
                info = json.loads(lista.read_text())
                atual = x509.load_pem_x509_certificate(cert.read_bytes())
                fim = getattr(atual, "not_valid_after_utc", None) or \
                    atual.not_valid_after.replace(tzinfo=timezone.utc)
                valido = (isinstance(info, dict) and set(ips) <= set(info.get("ips", []))
                          and fim - t0 > timedelta(days=30))
            except Exception:
                valido = False
        if not valido:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Levantamento da Frota - Caratinga")])
            san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
            crt = (x509.CertificateBuilder().subject_name(nome).issuer_name(ca_cert.subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(t0 - timedelta(days=1))
                   .not_valid_after(t0 + timedelta(days=800))  # iPhone exige no máximo 825 dias
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.KeyUsage(
                       digital_signature=True, content_commitment=False, key_encipherment=True,
                       data_encipherment=False, key_agreement=False, key_cert_sign=False,
                       crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .add_extension(x509.SubjectAlternativeName(san), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_priv.public_key()),
                                  critical=False)
                   .sign(ca_priv, hashes.SHA256()))
            chave.write_bytes(key.private_bytes(
                pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
            cert.write_bytes(crt.public_bytes(pem) + ca_cert.public_bytes(pem))
            lista.write_text(json.dumps({"ips": ips}))
        return cert, chave
    except Exception as e:
        print(f"  [!] Falha ao preparar o certificado: {e}")
        return None


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


def limpar_itens(d):
    """Valida o dicionário de itens de segurança e devolve JSON (ou None)."""
    if not isinstance(d, dict):
        return None
    r = {k: bool(d.get(k)) for k, _ in ITENS}
    r["na"] = bool(d.get("na"))
    return json.dumps(r)


def calcular_flags(v, n_avarias):
    f = []
    if v["situacao"] == "Não localizado":
        f.append("nao_localizado")
    if v["origem"] == "campo":
        f.append("campo")
    if v["status"] != "pendente" and v["situacao"] != "Não localizado" and v["lat"] is None:
        f.append("sem_gps")
    if v["acc"] is not None and v["acc"] > GPS_RUIM:
        f.append("gps_impreciso")
    if json.loads(v["puladas"] or "[]"):
        f.append("puladas")
    if n_avarias:
        f.append("avarias")
    if v["placa_legivel"] == "Não":
        f.append("placa_ilegivel")
    if v["itens"] and v["situacao"] != "Não localizado":
        o = json.loads(v["itens"])
        if not o.get("na") and not all(o.get(k) for k, _ in ITENS):
            f.append("itens_faltando")
    return f


def veiculo_dict(c, v, completo=False):
    fotos = fotos_do_veiculo(c, v["id"])
    d = dict(v)
    d["puladas_lista"] = json.loads(d.get("puladas") or "[]")
    d["itens_obj"] = json.loads(d["itens"]) if d.get("itens") else None
    n_av = sum(1 for f in fotos if f["etapa"] == "avarias")
    d["n_fotos"] = len(fotos)
    d["n_avarias"] = n_av
    etapas = status_etapas(v, fotos)
    d["etapas_feitas"] = sum(1 for e in etapas if e["status"] != "pendente")
    d["etapas_total"] = len(etapas)
    d["alertas"] = calcular_flags(v, n_av)
    d["alertas_texto"] = [ROTULO_FLAGS[a] for a in d["alertas"]]
    d["pdf"] = pdf_path(v["chave"]).exists()
    if completo:
        d["etapas"] = etapas
        for f in fotos:
            f["url"] = f"/fotos/{v['chave']}/{f['arquivo']}"
        d["fotos"] = fotos
    return d


def pdf_path(chave):
    return REL_DIR / f"{chave}_relatorio.pdf"


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
        old_pdf = pdf_path(v["chave"])
        if old_pdf.exists():
            old_pdf.unlink()
        novos["chave"] = chave
    if novos:
        sets = ", ".join(f"{k}=?" for k in novos)
        c.execute(f"UPDATE veiculos SET {sets} WHERE id=?", [*novos.values(), v["id"]])


def url_base():
    ip = ips_locais()[0]
    return f"https://{ip}:{PORTA_HTTPS}" if HTTPS_ATIVO[0] else f"http://{ip}:{PORTA_HTTP}"


def registrar_relatorio(c, tipo, ref, usuario):
    """Reserva o número de controle do relatório (e o código de autenticidade)."""
    t = agora()
    cur = c.execute("INSERT INTO relatorios(tipo, referencia, gerado_em, usuario_nome, usuario_matricula) "
                    "VALUES(?,?,?,?,?)", (tipo, ref, t, usuario["nome"], usuario["matricula"]))
    rid = cur.lastrowid
    numero = f"{datetime.now().year}/{rid:05d}"
    cod = hmac.new(segredo(), f"{numero}|{tipo}|{ref}|{t}".encode("utf-8"), hashlib.sha256).hexdigest()[:10].upper()
    cod = f"{cod[:5]}-{cod[5:]}"
    c.execute("UPDATE relatorios SET numero=?, codigo=? WHERE id=?", (numero, cod, rid))
    return {"id": rid, "numero": numero, "codigo": cod,
            "url": f"{url_base()}/verificar?n={quote(numero)}&c={cod}"}


def finalizar_relatorio(c, ctrl, destino):
    sha = hashlib.sha256(Path(destino).read_bytes()).hexdigest()
    c.execute("UPDATE relatorios SET arquivo=?, sha256=? WHERE id=?", (Path(destino).name, sha, ctrl["id"]))


def gerar_pdf_veiculo(c, vid, usuario):
    if relatorio is None:
        raise RotaErro("Gerador de PDF indisponível (instale: reportlab e pillow). " + ERRO_RELATORIO)
    v = achar(c, vid)
    d = veiculo_dict(c, v)
    fotos = fotos_do_veiculo(c, vid)
    destino = pdf_path(v["chave"])
    ctrl = registrar_relatorio(c, "Relatório fotográfico", v["chave"], usuario)
    relatorio.gerar_relatorio_veiculo(d, fotos, ETAPAS, pasta_fotos(v["chave"]), destino, ctrl)
    finalizar_relatorio(c, ctrl, destino)
    return destino


def nome_secretaria(v):
    return txt(v["secretaria"]) or "(sem secretaria)"


def resumo_geral(c):
    vs = [veiculo_dict(c, v) for v in c.execute("SELECT * FROM veiculos ORDER BY chave")]
    r = {"total": len(vs), "total_fotos": sum(v["n_fotos"] for v in vs)}
    for s in ("pendente", "em_andamento", "concluido"):
        r[s] = sum(1 for v in vs if v["status"] == s)
    r["nao_localizado"] = sum(1 for v in vs if "nao_localizado" in v["alertas"])
    r["campo"] = sum(1 for v in vs if "campo" in v["alertas"])
    r["avarias"] = sum(1 for v in vs if "avarias" in v["alertas"])
    r["puladas"] = sum(1 for v in vs if "puladas" in v["alertas"])
    r["itens"] = sum(1 for v in vs if "itens_faltando" in v["alertas"])
    r["placa_ilegivel"] = sum(1 for v in vs if "placa_ilegivel" in v["alertas"])
    r["gps"] = sum(1 for v in vs if "sem_gps" in v["alertas"] or "gps_impreciso" in v["alertas"])
    sec = {}
    for v in vs:
        nome = nome_secretaria(v)
        s = sec.setdefault(nome, {"nome": nome, "total": 0, "pendente": 0,
                                  "em_andamento": 0, "concluido": 0, "nao_localizado": 0})
        s["total"] += 1
        s[v["status"]] += 1
        if "nao_localizado" in v["alertas"]:
            s["nao_localizado"] += 1
    r["por_secretaria"] = sorted(sec.values(), key=lambda s: -s["total"])
    sit = Counter(v["situacao"] for v in vs if v["situacao"])
    r["por_situacao"] = [{"nome": s, "n": sit.get(s, 0)} for s in SITUACOES]
    r["plotado_sim"] = sum(1 for v in vs if v["plotado"] == "Sim")
    r["plotado_nao"] = sum(1 for v in vs if v["plotado"] == "Não")
    return r, vs


def zerar_banco():
    """Apaga veículos e fotos do banco. Antes, guarda cópia do .db e move fotos/PDFs."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bkp = DADOS / "backups"
    bkp.mkdir(exist_ok=True)
    copia = bkp / f"frota_{ts}.db"
    with conexao() as c:
        qtd = c.execute("SELECT COUNT(*) FROM veiculos").fetchone()[0]
        dest = sqlite3.connect(copia)
        c.backup(dest)
        dest.close()
        c.execute("DELETE FROM fotos")
        c.execute("DELETE FROM veiculos")
    pasta = EXCL_DIR / f"zerado_{ts}"
    for origem in (FOTOS_DIR, REL_DIR):
        if origem.exists() and any(origem.iterdir()):
            pasta.mkdir(parents=True, exist_ok=True)
            shutil.move(str(origem), str(pasta / origem.name))
        origem.mkdir(exist_ok=True)
    for m in MINI_DIR.glob("*"):
        m.unlink(missing_ok=True)
    print(f"[!] Banco zerado ({qtd} veículos). Cópia em {copia}")
    return {"apagados": qtd, "copia": str(copia)}


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


QUANDO_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")


def quando_valido(s):
    """Hora informada pelo celular (vistoria feita offline); senão, a do servidor."""
    s = txt(s)
    return s if QUANDO_RE.match(s) else agora()


def admins_ativos(c, excluir_id=0):
    return c.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='admin' AND ativo=1 "
                     "AND senha_hash IS NOT NULL AND id<>?", (excluir_id,)).fetchone()[0]


def tem_admin(c):
    return admins_ativos(c) > 0


def codigo_relatorio(numero, tipo, ref, quando):
    cod = hmac.new(segredo(), f"{numero}|{tipo}|{ref}|{quando}".encode("utf-8"),
                   hashlib.sha256).hexdigest()[:10].upper()
    return f"{cod[:5]}-{cod[5:]}"


def pagina_verificacao(n, cod):
    estilo = ""
    corpo = ('<img src="/web/brasao.png" alt=""><h1>Prefeitura Municipal de Caratinga</h1>'
             '<div>Departamento de Patrimônio · Verificação de relatório</div><hr>')
    if n:
        with conexao() as c:
            r = c.execute("SELECT * FROM relatorios WHERE numero=?", (n,)).fetchone()
        valido = bool(r and r["codigo"] and hmac.compare_digest(
            codigo_relatorio(r["numero"], r["tipo"], r["referencia"], r["gerado_em"]), cod.upper().strip())
            and r["codigo"] == cod.upper().strip())
        if valido:
            arq = ""
            if r["arquivo"] and (REL_DIR / r["arquivo"]).exists():
                igual = hashlib.sha256((REL_DIR / r["arquivo"]).read_bytes()).hexdigest() == r["sha256"]
                arq = ("O arquivo PDF arquivado confere com o original." if igual else
                       "Atenção: o arquivo PDF arquivado é diferente deste (foi substituído por uma emissão "
                       "mais recente ou alterado).")
            elif r["arquivo"]:
                arq = "O arquivo PDF não está mais na pasta de relatórios."
            corpo += ('<div class="ok">✔ Relatório autêntico</div><table>'
                      f'<tr><td>Número de controle</td><td><b>{esc_html(r["numero"])}</b></td></tr>'
                      f'<tr><td>Tipo</td><td>{esc_html(r["tipo"])}</td></tr>'
                      f'<tr><td>Referência</td><td>{esc_html(r["referencia"] or "")}</td></tr>'
                      f'<tr><td>Emitido em</td><td>{esc_html(r["gerado_em"])}</td></tr>'
                      f'<tr><td>Emitido por</td><td>{esc_html(r["usuario_nome"])} (Mat. {esc_html(r["usuario_matricula"])})</td></tr>'
                      f'</table><p>{esc_html(arq)}</p>')
        else:
            corpo += '<div class="no">✖ Relatório não encontrado ou código inválido</div>'
        corpo += '<hr>'
    corpo += ('<form method="get" action="/verificar"><div>Verificar outro relatório</div>'
              f'<input name="n" placeholder="Nº (ex.: 2026/00012)" value="{esc_html(n or "")}"><br>'
              f'<input name="c" placeholder="Código (ex.: 1A2B3-C4D5E)" value="{esc_html(cod or "")}"><br>'
              '<button>Verificar</button></form>')
    return f'<!doctype html><html lang="pt-br"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Verificação</title><style>{estilo}</style><div class="c">{corpo}</div></html>'


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ctx_tls = None

    def setup(self):
        if self.ctx_tls is not None:  # handshake TLS na thread da conexão
            self.request = self.ctx_tls.wrap_socket(self.request, server_side=True)
        super().setup()

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

    # ---- sessão e permissões
    def sessao(self):
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" not in ck:
            return None
        with conexao() as c:
            s = c.execute("SELECT u.nome, u.matricula, u.perfil FROM sessoes s "
                          "JOIN usuarios u ON u.matricula=s.matricula "
                          "WHERE s.token=? AND u.ativo=1", (ck["sid"].value,)).fetchone()
        return dict(s) if s else None

    def exigir(self):
        if not self.usuario:
            raise RotaErro("Faça login para continuar.", 401)
        return self.usuario

    def exigir_admin(self):
        u = self.exigir()
        if u["perfil"] != "admin":
            raise RotaErro("Apenas o administrador pode fazer isso.", 403)
        return u

    def corpo_json(self):
        try:
            return json.loads(self.corpo.decode("utf-8") or "{}")
        except ValueError:
            raise RotaErro("Dados inválidos.")

    def abrir_sessao(self, c, nome, mat):
        token = secrets.token_hex(24)
        c.execute("INSERT INTO sessoes VALUES(?,?,?,?)", (token, nome, mat, agora()))
        return {"Set-Cookie": f"sid={token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"}

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
            self.usuario = self.sessao()
            (self.rota_get if metodo == "GET" else self.rota_post)()
        except RotaErro as e:
            self.json({"ok": False, "erro": str(e)}, e.codigo)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
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
        if p == "/sw.js":  # service worker na raiz: controla todo o site
            return self.arquivo(WEB / "sw.js", extra={"Service-Worker-Allowed": "/"})
        if p == "/manifest.webmanifest":
            return self.arquivo(WEB / "manifest.webmanifest", tipo="application/manifest+json")
        if p == "/certificado":
            return self.arquivo(WEB / "certificado.html")
        if p == "/ca.crt":
            return self.arquivo(CERT_DIR / "ca.pem", nome_download="Frota-Caratinga-CA.crt",
                                tipo="application/x-x509-ca-cert")
        if p == "/verificar":
            return self.enviar(pagina_verificacao(self.q.get("n", ""), self.q.get("c", "")).encode("utf-8"),
                               "text/html; charset=utf-8", 200, {"Cache-Control": "no-store"})
        if p.startswith("/web/"):
            alvo = (WEB / p[5:]).resolve()
            if WEB.resolve() not in alvo.parents:
                raise RotaErro("Não permitido", 403)
            return self.arquivo(alvo)
        if p.startswith("/fotos/"):
            self.exigir()
            return self.servir_foto(p)
        if p.startswith("/relatorios/"):
            self.exigir()
            nome = p.split("/", 2)[2]
            if not re.fullmatch(r"[\w.\-]+\.pdf", nome):
                raise RotaErro("Não permitido", 403)
            return self.arquivo(REL_DIR / nome, inline_nome=nome)
        if p == "/qr.svg":
            if segno is None:
                raise RotaErro("QR Code indisponível (instale: pip install segno)", 501)
            buf = io.BytesIO()
            segno.make(self.q.get("url", ""), error="m").save(buf, kind="svg", scale=6, border=2)
            return self.enviar(buf.getvalue(), "image/svg+xml")
        if p == "/api/login_info":
            with conexao() as c:
                return self.json({"ok": True, "precisa_admin": not tem_admin(c)})
        if p == "/api/eu":
            u = self.exigir()
            return self.json({"ok": True, "usuario": u})
        if p == "/api/config":
            return self.json(self.config())
        self.exigir()
        if p == "/api/resumo":
            with conexao() as c:
                r, _ = resumo_geral(c)
            return self.json({"ok": True, **r})
        if p == "/api/veiculos":
            return self.json({"ok": True, "veiculos": self.listar()})
        m = re.fullmatch(r"/api/veiculos/(\d+)", p)
        if m:
            with conexao() as c:
                return self.json({"ok": True, "veiculo": veiculo_dict(c, achar(c, int(m[1])), True)})
        if p == "/api/usuarios":
            self.exigir_admin()
            with conexao() as c:
                rows = c.execute("SELECT id, nome, matricula, perfil, ativo, (senha_hash IS NOT NULL) AS tem_senha "
                                 "FROM usuarios ORDER BY nome").fetchall()
            return self.json({"ok": True, "usuarios": [dict(r) for r in rows]})
        if p == "/api/modelo.csv":
            corpo = csv_bytes(["Patrimonio", "Placa", "Tipo", "Marca/Modelo", "Ano", "Chassi/Serie",
                               "Renavam", "Secretaria", "Local", "Responsavel", "Situacao"], [])
            return self.enviar(corpo, "text/csv; charset=utf-8", 200,
                               {"Content-Disposition": 'attachment; filename="modelo_importacao.csv"'})
        if p == "/api/exportar.csv":
            return self.exportar()
        raise RotaErro("Rota não encontrada", 404)

    def config(self):
        urls = []
        for ip in ips_locais():
            urls.append({"ip": ip,
                         "celular": (f"https://{ip}:{PORTA_HTTPS}/m" if HTTPS_ATIVO[0]
                                     else f"http://{ip}:{PORTA_HTTP}/m"),
                         "certificado": f"http://{ip}:{PORTA_HTTP}/certificado",
                         "http": f"http://{ip}:{PORTA_HTTP}/m",
                         "https": f"https://{ip}:{PORTA_HTTPS}/m" if HTTPS_ATIVO[0] else ""})
        return {"ok": True, "etapas": ETAPAS, "situacoes": SITUACOES, "tipos": TIPOS,
                "itens": [{"id": k, "titulo": t} for k, t in ITENS],
                "https_ativo": HTTPS_ATIVO[0], "urls": urls, "pdf_ok": relatorio is not None,
                "qr_ok": segno is not None, "gps_ruim": GPS_RUIM,
                "ca_ok": (CERT_DIR / "ca.pem").exists()}

    def listar(self):
        q, filtro, sec = norm(self.q.get("q", "")), self.q.get("filtro", "todos"), self.q.get("secretaria", "")
        limite = int(self.q.get("limite") or 0)
        completo = self.q.get("completo") == "1"
        with conexao() as c:
            vs = [veiculo_dict(c, v, completo) for v in c.execute("SELECT * FROM veiculos ORDER BY chave")]
        out = []
        for v in vs:
            if sec and nome_secretaria(v) != sec:
                continue
            if q and q not in norm(" ".join(txt(v[k]) for k in
                                            ("chave", "patrimonio", "placa", "marca_modelo", "secretaria", "tipo"))):
                continue
            if filtro in ("pendente", "em_andamento", "concluido") and v["status"] != filtro:
                continue
            if filtro in ("nao_localizado", "campo", "avarias", "puladas") and filtro not in v["alertas"]:
                continue
            if filtro == "itens" and "itens_faltando" not in v["alertas"]:
                continue
            if filtro == "gps" and not ({"sem_gps", "gps_impreciso"} & set(v["alertas"])):
                continue
            out.append(v)
        return out[:limite] if limite else out

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
               "onde_utilizado", "motorista", "plotado", "placa_legivel", "estepe", "triangulo", "macaco",
               "chave_de_roda", "observacoes", "vistoriador", "matricula", "inicio", "fim",
               "latitude", "longitude", "precisao_m", "n_fotos", "etapas_puladas", "alertas", "origem"]

        def dec(x):
            return "" if x is None else str(round(x, 7)).replace(".", ",")

        def sn(v, k):
            o = v["itens_obj"]
            return "" if not o else ("N/A" if o.get("na") else ("Sim" if o.get(k) else "Não"))
        linhas = []
        for v in vs:
            linhas.append([v["chave"], v["patrimonio"], v["placa"], v["tipo"], v["marca_modelo"],
                           v["ano"], v["chassi"], v["renavam"], v["secretaria"], v["local_cadastro"],
                           v["responsavel"], v["situacao_cadastro"], v["status"], v["situacao"],
                           v["odometro"], v["local_visto"], v["uso_local"], v["motorista"],
                           v["plotado"], v["placa_legivel"], sn(v, "estepe"), sn(v, "triangulo"), sn(v, "macaco"),
                           sn(v, "chave_roda"), v["obs"], v["vistoriador_nome"],
                           v["vistoriador_matricula"], v["inicio"], v["fim"], dec(v["lat"]),
                           dec(v["lon"]), dec(v["acc"]), v["n_fotos"], ",".join(v["puladas_lista"]),
                           "; ".join(v["alertas_texto"]), v["origem"]])
        nome = "frota_" + datetime.now().strftime("%Y%m%d_%H%M") + ".csv"
        self.enviar(csv_bytes(cab, linhas), "text/csv; charset=utf-8", 200,
                    {"Content-Disposition": f'attachment; filename="{nome}"'})

    # ---- POST
    def rota_post(self):
        p = self.caminho
        if p == "/api/login":
            return self.login()
        if p == "/api/setup":
            return self.setup_admin()
        if p == "/api/logout":
            ck = SimpleCookie(self.headers.get("Cookie", ""))
            if "sid" in ck:
                with conexao() as c:
                    c.execute("DELETE FROM sessoes WHERE token=?", (ck["sid"].value,))
            return self.json({"ok": True})
        u = self.exigir()
        if p == "/api/minha_senha":
            return self.minha_senha(u)
        if p == "/api/foto":
            return self.salvar_foto(u)
        if p == "/api/importar":
            self.exigir_admin()
            nome = self.q.get("nome", "planilha.csv")
            with conexao() as c:
                return self.json({"ok": True, **importar(c, nome, self.corpo)})
        if p == "/api/zerar_banco":
            self.exigir_admin()
            if self.corpo_json().get("confirmacao") != "ZERAR":
                raise RotaErro("Confirmação incorreta.")
            return self.json({"ok": True, **zerar_banco()})
        if p in ("/api/relatorio_geral", "/api/relatorio_secretaria", "/api/relatorio_pendencias"):
            return self.relatorio_extra(p, u)
        if p == "/api/usuarios":
            self.exigir_admin()
            return self.criar_usuario()
        m = re.fullmatch(r"/api/usuarios/(\d+)/(\w+)", p)
        if m:
            return self.acao_usuario(int(m[1]), m[2], self.exigir_admin())
        if p == "/api/veiculos":
            return self.criar_veiculo(u)
        m = re.fullmatch(r"/api/foto/(\d+)/(apagar|legenda)", p)
        if m:
            return self.acao_foto(int(m[1]), m[2])
        m = re.fullmatch(r"/api/veiculos/(\d+)/(\w+)", p)
        if m:
            return self.acao_veiculo(int(m[1]), m[2], u)
        raise RotaErro("Rota não encontrada", 404)

    # ---- login, usuários, perfis
    def login(self):
        d = self.corpo_json()
        mat, senha = chave_mat(d.get("matricula")), str(d.get("senha") or "")
        t = TENTATIVAS.get(mat)
        if t and t[1] > time.time():
            raise RotaErro(f"Muitas tentativas erradas. Aguarde {int((t[1] - time.time()) // 60) + 1} minuto(s).", 429)
        with conexao() as c:
            u = c.execute("SELECT * FROM usuarios WHERE matricula_chave=?", (mat,)).fetchone()
            if u and u["ativo"] and not u["senha_hash"]:
                raise RotaErro("Este usuário ainda não tem senha. Peça ao administrador para definir uma.")
            if not u or not u["ativo"] or not confere_senha(senha, u["senha_hash"], u["salt"]):
                n = (t[0] if t else 0) + 1
                TENTATIVAS[mat] = (n, time.time() + 300 if n >= 5 else 0)
                raise RotaErro("Matrícula ou senha incorretos.")
            TENTATIVAS.pop(mat, None)
            cab = self.abrir_sessao(c, u["nome"], u["matricula"])
        self.json({"ok": True, "usuario": {"nome": u["nome"], "matricula": u["matricula"],
                                           "perfil": u["perfil"]}}, 200, cab)

    def setup_admin(self):
        d = self.corpo_json()
        nome, mat, senha = " ".join(txt(d.get("nome")).split()), txt(d.get("matricula")), str(d.get("senha") or "")
        with conexao() as c:
            if tem_admin(c):
                raise RotaErro("O sistema já tem administrador. Entre com sua matrícula e senha.", 403)
            if len(nome) < 3 or not chave_mat(mat) or len(senha) < 4:
                raise RotaErro("Informe nome completo, matrícula e uma senha de pelo menos 4 caracteres.")
            h, salt = hash_senha(senha)
            ex = c.execute("SELECT id FROM usuarios WHERE matricula_chave=?", (chave_mat(mat),)).fetchone()
            if ex:
                c.execute("UPDATE usuarios SET nome=?, matricula=?, senha_hash=?, salt=?, perfil='admin', ativo=1 WHERE id=?",
                          (nome, mat, h, salt, ex["id"]))
            else:
                c.execute("INSERT INTO usuarios(nome, matricula, matricula_chave, senha_hash, salt, perfil, ativo) "
                          "VALUES(?,?,?,?,?,'admin',1)", (nome, mat, chave_mat(mat), h, salt))
            c.execute("DELETE FROM sessoes")  # sessões do sistema antigo (sem senha) deixam de valer
            cab = self.abrir_sessao(c, nome, mat)
        self.json({"ok": True, "usuario": {"nome": nome, "matricula": mat, "perfil": "admin"}}, 200, cab)

    def minha_senha(self, u):
        d = self.corpo_json()
        atual, nova = str(d.get("atual") or ""), str(d.get("nova") or "")
        if len(nova) < 4:
            raise RotaErro("A nova senha precisa ter pelo menos 4 caracteres.")
        with conexao() as c:
            r = c.execute("SELECT * FROM usuarios WHERE matricula=?", (u["matricula"],)).fetchone()
            if not confere_senha(atual, r["senha_hash"], r["salt"]):
                raise RotaErro("A senha atual está incorreta.")
            h, salt = hash_senha(nova)
            c.execute("UPDATE usuarios SET senha_hash=?, salt=? WHERE id=?", (h, salt, r["id"]))
        self.json({"ok": True})

    def criar_usuario(self):
        d = self.corpo_json()
        nome, mat = " ".join(txt(d.get("nome")).split()), txt(d.get("matricula"))
        perfil, senha = txt(d.get("perfil")) or "vistoriador", str(d.get("senha") or "")
        if len(nome) < 3 or not chave_mat(mat):
            raise RotaErro("Informe nome completo e matrícula.")
        if perfil not in PERFIS:
            raise RotaErro("Perfil inválido.")
        if len(senha) < 4:
            raise RotaErro("A senha precisa ter pelo menos 4 caracteres.")
        with conexao() as c:
            if c.execute("SELECT 1 FROM usuarios WHERE matricula_chave=?", (chave_mat(mat),)).fetchone():
                raise RotaErro("Já existe um usuário com essa matrícula (use 'Redefinir senha').")
            h, salt = hash_senha(senha)
            c.execute("INSERT INTO usuarios(nome, matricula, matricula_chave, senha_hash, salt, perfil, ativo) "
                      "VALUES(?,?,?,?,?,?,1)", (nome, mat, chave_mat(mat), h, salt, perfil))
        self.json({"ok": True})

    def acao_usuario(self, uid, acao, eu):
        d = self.corpo_json()
        with conexao() as c:
            alvo = c.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
            if not alvo:
                raise RotaErro("Usuário não encontrado", 404)
            proprio = alvo["matricula"] == eu["matricula"]
            if acao == "senha":
                s = str(d.get("senha") or "")
                if len(s) < 4:
                    raise RotaErro("A senha precisa ter pelo menos 4 caracteres.")
                h, salt = hash_senha(s)
                c.execute("UPDATE usuarios SET senha_hash=?, salt=? WHERE id=?", (h, salt, uid))
                if not proprio:
                    c.execute("DELETE FROM sessoes WHERE matricula=?", (alvo["matricula"],))
            elif acao == "perfil":
                p = txt(d.get("perfil"))
                if p not in PERFIS:
                    raise RotaErro("Perfil inválido.")
                if proprio:
                    raise RotaErro("Você não pode alterar o seu próprio perfil.")
                if alvo["perfil"] == "admin" and p != "admin" and admins_ativos(c, uid) == 0:
                    raise RotaErro("O sistema precisa de pelo menos um administrador.")
                c.execute("UPDATE usuarios SET perfil=? WHERE id=?", (p, uid))
            elif acao == "ativo":
                ativo = 1 if d.get("ativo") else 0
                if proprio:
                    raise RotaErro("Você não pode desativar o seu próprio usuário.")
                if not ativo and alvo["perfil"] == "admin" and admins_ativos(c, uid) == 0:
                    raise RotaErro("O sistema precisa de pelo menos um administrador ativo.")
                c.execute("UPDATE usuarios SET ativo=? WHERE id=?", (ativo, uid))
                if not ativo:
                    c.execute("DELETE FROM sessoes WHERE matricula=?", (alvo["matricula"],))
            elif acao == "nome":
                nome = " ".join(txt(d.get("nome")).split())
                if len(nome) < 3:
                    raise RotaErro("Informe o nome completo.")
                c.execute("UPDATE usuarios SET nome=? WHERE id=?", (nome, uid))
            else:
                raise RotaErro("Ação inválida.", 404)
        self.json({"ok": True})

    # ---- relatórios consolidados
    def relatorio_extra(self, p, u):
        if relatorio is None:
            raise RotaErro("Gerador de PDF indisponível. " + ERRO_RELATORIO)
        d = self.corpo_json() if self.corpo else {}
        alvo = txt(d.get("secretaria"))
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        with conexao() as c:
            r, vs = resumo_geral(c)
            if p == "/api/relatorio_geral":
                nome, tipo, ref = f"CONSOLIDADO_{ts}.pdf", "Relatório consolidado da frota", "geral"
            elif p == "/api/relatorio_secretaria":
                vs = [v for v in vs if nome_secretaria(v) == alvo]
                if not vs:
                    raise RotaErro("Escolha uma secretaria com veículos cadastrados.")
                nome = f"SECRETARIA_{(norm(alvo) or 'sem')[:30]}_{ts}.pdf"
                tipo, ref = "Relatório da frota por secretaria", alvo
            else:
                grupos = {}
                for v in vs:
                    if v["status"] == "concluido" or (alvo and nome_secretaria(v) != alvo):
                        continue
                    grupos.setdefault(nome_secretaria(v), []).append(v)
                if not grupos:
                    raise RotaErro("Não há veículos pendentes" + (" nessa secretaria." if alvo else "."))
                nome = f"PENDENCIAS_{(norm(alvo) or 'todas')[:30]}_{ts}.pdf"
                tipo, ref = "Pendências de vistoria", alvo or "todas as secretarias"
            destino = REL_DIR / nome
            ctrl = registrar_relatorio(c, tipo, ref, u)
            if p == "/api/relatorio_geral":
                relatorio.gerar_relatorio_geral(r, vs, u, destino, ctrl)
            elif p == "/api/relatorio_secretaria":
                relatorio.gerar_relatorio_secretaria(alvo, vs, u, destino, ctrl)
            else:
                relatorio.gerar_pendencias(sorted(grupos.items()), u, destino, ctrl)
            finalizar_relatorio(c, ctrl, destino)
        self.json({"ok": True, "url": f"/relatorios/{nome}", "numero": ctrl["numero"], "codigo": ctrl["codigo"]})

    # ---- veículos
    def criar_veiculo(self, u):
        d = self.corpo_json()
        origem = "campo" if d.get("origem") == "campo" else "painel"
        if origem != "campo":
            self.exigir_admin()
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

    def acao_veiculo(self, vid, acao, u):
        d = self.corpo_json() if self.corpo else {}
        if acao in ("salvar", "excluir") and u["perfil"] != "admin":
            raise RotaErro("Apenas o administrador pode fazer isso.", 403)
        with conexao() as c:
            v = achar(c, vid)
            if acao == "salvar":
                gravar_cadastro(c, v, d)
                sets = {k: txt(d[k]) for k in VIST if k in d}
                if "itens" in d:
                    sets["itens"] = limpar_itens(d["itens"])
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
                if isinstance(d.get("itens"), dict):
                    sets["itens"] = limpar_itens(d["itens"])
                sets["status"] = "em_andamento" if v["status"] != "concluido" else v["status"]
                if v["status"] == "pendente" or not v["vistoriador_nome"]:
                    sets["vistoriador_nome"], sets["vistoriador_matricula"] = u["nome"], u["matricula"]
                if not v["inicio"]:
                    sets["inicio"] = quando_valido(d.get("quando"))
                if num(d.get("lat")) is not None and num(d.get("lon")) is not None:
                    sets["lat"], sets["lon"], sets["acc"] = num(d["lat"]), num(d["lon"]), num(d.get("acc"))
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
                sets = {"status": "concluido", "fim": quando_valido(d.get("quando"))}
                if not v["vistoriador_nome"]:
                    sets["vistoriador_nome"], sets["vistoriador_matricula"] = u["nome"], u["matricula"]
                c.execute(f"UPDATE veiculos SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                          [*sets.values(), vid])
                c.commit()
                try:
                    gerar_pdf_veiculo(c, vid, u)
                    return self.json({"ok": True, "pdf": f"/relatorios/{achar(c, vid)['chave']}_relatorio.pdf"})
                except Exception as e:
                    traceback.print_exc()
                    return self.json({"ok": True, "pdf": None, "pdf_erro": str(e)})
            elif acao == "pdf":
                gerar_pdf_veiculo(c, vid, u)
                return self.json({"ok": True, "url": f"/relatorios/{achar(c, vid)['chave']}_relatorio.pdf"
                                  f"?t={int(time.time())}"})
            elif acao == "reiniciar":
                for f in c.execute("SELECT * FROM fotos WHERE veiculo_id=?", (vid,)).fetchall():
                    apagar_arquivo_foto(v["chave"], f["arquivo"])
                c.execute("DELETE FROM fotos WHERE veiculo_id=?", (vid,))
                c.execute("UPDATE veiculos SET status='pendente', situacao=NULL, odometro=NULL, local_visto=NULL, "
                          "uso_local=NULL, motorista=NULL, plotado=NULL, placa_legivel=NULL, itens=NULL, obs=NULL, "
                          "vistoriador_nome=NULL, vistoriador_matricula=NULL, inicio=NULL, fim=NULL, "
                          "lat=NULL, lon=NULL, acc=NULL, puladas='[]', sem_avarias=0 WHERE id=?", (vid,))
                pdf_path(v["chave"]).unlink(missing_ok=True)
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

    def salvar_foto(self, u):
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
            c.execute("INSERT INTO fotos(veiculo_id, etapa, seq, arquivo, legenda, lat, lon, acc, criado_em, usuario) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (vid, etapa, seq, nome, txt(self.q.get("legenda")), num(self.q.get("lat")),
                       num(self.q.get("lon")), num(self.q.get("acc")), quando_valido(self.q.get("quando")),
                       u["nome"]))
            lista = [x for x in json.loads(v["puladas"] or "[]") if x != etapa]
            c.execute("UPDATE veiculos SET puladas=?, sem_avarias=CASE WHEN ?='avarias' THEN 0 ELSE sem_avarias END, "
                      "status=CASE WHEN status='pendente' THEN 'em_andamento' ELSE status END WHERE id=?",
                      (json.dumps(lista), etapa, vid))
            print(f"   {v['chave']}/{nome} ({len(self.corpo) // 1024} KB) - {u['nome']}")
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
    request_queue_size = 32

    def handle_error(self, request, client_address):
        pass  # handshakes TLS recusados (aviso de certificado) são normais


HTTPS_ATIVO = [False]


def principal():
    iniciar_banco()
    preparar = "--preparar" in sys.argv
    print("=" * 62)
    print("  LEVANTAMENTO DA FROTA MUNICIPAL - PREFEITURA DE CARATINGA")
    print("=" * 62)
    cert = preparar_certificado()
    if preparar:
        print("Preparação concluída." if cert else "Preparação concluída com avisos (veja acima).")
        return
    if relatorio is None:
        print(f"  [!] PDF desativado: {ERRO_RELATORIO}\n      Rode instalar.bat (precisa de internet).")
    if segno is None:
        print("  [!] QR Code desativado (instale: pip install segno).")

    http = Servidor(("0.0.0.0", PORTA_HTTP), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()

    if cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert[0]), str(cert[1]))

        class HandlerTLS(Handler):
            ctx_tls = ctx
        try:
            https = Servidor(("0.0.0.0", PORTA_HTTPS), HandlerTLS)
            threading.Thread(target=https.serve_forever, daemon=True).start()
            HTTPS_ATIVO[0] = True
        except OSError as e:
            print(f"  [!] HTTPS não iniciou (porta {PORTA_HTTPS} ocupada?): {e}")
    else:
        print("  [!] HTTPS desativado: instale 'cryptography' (rode instalar.bat). Sem HTTPS o GPS não funciona.")

    ip = ips_locais()[0]
    print(f"\n  Painel (neste PC):  http://localhost:{PORTA_HTTP}")
    if HTTPS_ATIVO[0]:
        print(f"  Celular (com GPS):  https://{ip}:{PORTA_HTTPS}/m")
    else:
        print(f"  Celular (sem GPS):  http://{ip}:{PORTA_HTTP}/m")
    if HTTPS_ATIVO[0]:
        print(f"  1º acesso do celular (instalar certificado, uma vez): http://{ip}:{PORTA_HTTP}/certificado")
    print(f"  Dados salvos em:    {DADOS}")
    with conexao() as c:
        if not tem_admin(c):
            print("\n  >> PRIMEIRO USO: no painel, crie o usuário ADMINISTRADOR (nome, matrícula e senha).")
    print("\n  Deixe esta janela aberta durante o levantamento. Ctrl+C para encerrar.\n")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORTA_HTTP}")).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    principal()
