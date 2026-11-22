"""
Geração dos relatórios em PDF (reportlab + Pillow).

Para mudar os textos do cabeçalho, edite as constantes logo abaixo.
"""
import io
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)

try:
    from PIL import Image as PILImage, ImageOps
except ImportError:  # pragma: no cover
    PILImage = None
try:
    import pillow_heif  # opcional: abre fotos HEIC do iPhone
    pillow_heif.register_heif_opener()
except Exception:
    pass

# ---------------------------------------------------------------- textos ----
BRASAO = Path(__file__).resolve().parent / "web" / "brasao.png"
CABECALHO = [  # (texto, tamanho da fonte, negrito)
    ("PREFEITURA MUNICIPAL DE CARATINGA", 13, True),
    ("Secretaria de Administração e Desenvolvimento Econômico", 11, True),
    ("Departamento de Patrimônio", 11, False),
]
RODAPE = "Levantamento visual e fotográfico, sem avaliação técnica mecânica."

AZUL = colors.HexColor("#0b3d6b")
CINZA = colors.HexColor("#e8edf2")
BORDA = colors.HexColor("#aab4be")

ST = {
    "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=14,
                             alignment=TA_CENTER, textColor=AZUL, spaceAfter=4),
    "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=10,
                          alignment=TA_CENTER, spaceAfter=10),
    "secao": ParagraphStyle("secao", fontName="Helvetica-Bold", fontSize=10.5,
                            textColor=colors.white, backColor=AZUL,
                            borderPadding=(3, 4, 3, 4), spaceBefore=10,
                            spaceAfter=7),
    "rotulo": ParagraphStyle("rotulo", fontName="Helvetica-Bold", fontSize=8.5,
                             leading=10.5),
    "valor": ParagraphStyle("valor", fontName="Helvetica", fontSize=9,
                            leading=11),
    "peq": ParagraphStyle("peq", fontName="Helvetica", fontSize=7.5,
                          leading=9.5),
    "cel": ParagraphStyle("cel", fontName="Helvetica", fontSize=7.5,
                          leading=9),
    "corpo": ParagraphStyle("corpo", fontName="Helvetica", fontSize=9.5,
                            leading=12.5, spaceAfter=4),
}


def fmt_data(s):
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(s)


def _p(texto, estilo="valor"):
    texto = "" if texto is None else str(texto)
    return Paragraph(escape(texto) if texto.strip() else "—", ST[estilo])


# ------------------------------------------------------ cabeçalho/rodapé ----
def _decorar(c, doc):
    w, h = A4
    c.saveState()
    # brasão
    if BRASAO.exists():
        iw, ih = ImageReader(str(BRASAO)).getSize()
        alt = 2.5 * cm
        c.drawImage(str(BRASAO), 1.5 * cm, h - 3.95 * cm, width=alt * iw / ih,
                    height=alt, mask="auto")
    # textos
    y = h - 1.9 * cm
    for texto, tam, neg in CABECALHO:
        c.setFont("Helvetica-Bold" if neg else "Helvetica", tam)
        c.setFillColor(AZUL if neg else colors.black)
        c.drawCentredString(w / 2 + 0.8 * cm, y, texto)
        y -= tam + 5
    c.setStrokeColor(AZUL)
    c.setLineWidth(1.2)
    c.line(1.5 * cm, h - 4.1 * cm, w - 1.5 * cm, h - 4.1 * cm)
    # rodapé
    c.setStrokeColor(BORDA)
    c.setLineWidth(0.5)
    c.line(1.5 * cm, 1.6 * cm, w - 1.5 * cm, 1.6 * cm)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#444444"))
    ctrl = getattr(doc, "controle", None)
    direita = w - 1.5 * cm - (1.5 * cm if ctrl else 0)
    c.drawString(1.5 * cm, 1.15 * cm, RODAPE)
    c.drawRightString(direita, 1.15 * cm, f"Página {doc.page}")
    c.drawString(1.5 * cm, 0.8 * cm, getattr(doc, "emitido", ""))
    if ctrl:
        c.setFont("Helvetica-Bold", 7)
        c.drawString(1.5 * cm, 0.45 * cm,
                     f"Controle nº {ctrl['numero']}  ·  Código de autenticidade: {ctrl['codigo']}")
        c.setFont("Helvetica", 6)
        c.drawRightString(direita, 0.45 * cm, "Verifique a autenticidade pelo QR Code")
        try:  # QR Code (URL de verificação)
            qr = QrCodeWidget(ctrl["url"])
            x0, y0, x1, y1 = qr.getBounds()
            tam = 1.25 * cm
            d = Drawing(tam, tam, transform=[tam / (x1 - x0), 0, 0, tam / (y1 - y0), 0, 0])
            d.add(qr)
            renderPDF.draw(d, c, w - 1.5 * cm - tam, 0.28 * cm)
        except Exception:
            pass
    c.restoreState()


def _montar(destino, story, titulo, emitido, controle=None):
    doc = BaseDocTemplate(str(destino), pagesize=A4, leftMargin=1.5 * cm,
                          rightMargin=1.5 * cm, topMargin=4.4 * cm,
                          bottomMargin=2.0 * cm, title=titulo,
                          author="Prefeitura Municipal de Caratinga")
    doc.emitido = emitido
    doc.controle = controle
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_decorar)])
    doc.build(story)
    return destino


# ------------------------------------------------------------ utilidades ----
def _tabela_campos(itens):
    """itens: lista de (rótulo, valor, largo). Monta tabela de 4 colunas."""
    largura = A4[0] - 3 * cm
    cw = [3.1 * cm, largura / 2 - 3.1 * cm, 3.1 * cm, largura / 2 - 3.1 * cm]
    linhas, cmds, pend = [], [
        ("GRID", (0, 0), (-1, -1), 0.4, BORDA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ], None

    def add(cells, largo=False):
        linhas.append(cells)
        r = len(linhas) - 1
        cmds.append(("BACKGROUND", (0, r), (0, r), CINZA))
        if largo:
            cmds.append(("SPAN", (1, r), (3, r)))
        elif cells[2] != "":
            cmds.append(("BACKGROUND", (2, r), (2, r), CINZA))

    for rot, val, largo in itens:
        celulas = [_p(rot, "rotulo"), _p(val)]
        if largo:
            if pend:
                add(pend + ["", ""])
                pend = None
            add(celulas + ["", ""], largo=True)
        elif pend is None:
            pend = celulas
        else:
            add(pend + celulas)
            pend = None
    if pend:
        add(pend + ["", ""])
    t = Table(linhas, colWidths=cw)
    t.setStyle(TableStyle(cmds))
    return t


def _imagem(caminho, larg_max, alt_max):
    if PILImage is None:
        raise RuntimeError("Pillow não instalado")
    with PILImage.open(caminho) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((1100, 1100))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    buf.seek(0)
    w, h = im.size
    esc = min(larg_max / w, alt_max / h)
    return Image(buf, width=w * esc, height=h * esc)


def _linhas_itens(v):
    """Linhas do quadro 'Dados da vistoria' para estepe/triângulo/macaco/chave de roda."""
    if v.get("situacao") == "Não localizado":
        return []
    o = v.get("itens_obj")
    if not o:
        return [("Itens de segurança", "Não verificado", True)]
    if o.get("na"):
        return [("Itens de segurança", "Não se aplica a este veículo/máquina", True)]
    f = lambda k: "Sim" if o.get(k) else "NÃO POSSUI"
    return [("Estepe", f("estepe"), False), ("Triângulo", f("triangulo"), False),
            ("Macaco", f("macaco"), False), ("Chave de roda", f("chave_roda"), False)]


def _fmt_gps(lat, lon, acc):
    if lat is None or lon is None:
        return ""
    s = f"{lat:.6f}, {lon:.6f}"
    if acc is not None:
        s += f" (precisão ±{acc:.0f} m)"
    return s


# ------------------------------------------------------ relatório do veículo --
def gerar_relatorio_veiculo(v, fotos, etapas, pasta_fotos, destino, controle=None):
    """v: dict do veículo; fotos: lista de dicts; etapas: lista ETAPAS."""
    st = ST
    story = [
        Paragraph("RELATÓRIO FOTOGRÁFICO DE VEÍCULO / MÁQUINA", st["titulo"]),
        Paragraph(f"Identificação: <b>{escape(v['chave'])}</b>", st["sub"]),
        Paragraph("DADOS DO CADASTRO PATRIMONIAL", st["secao"]),
        _tabela_campos([
            ("Placa", v.get("placa"), False),
            ("Patrimônio", v.get("patrimonio"), False),
            ("Tipo", v.get("tipo"), False),
            ("Ano", v.get("ano"), False),
            ("Marca / Modelo", v.get("marca_modelo"), True),
            ("Chassi / Série", v.get("chassi"), False),
            ("Renavam", v.get("renavam"), False),
            ("Secretaria", v.get("secretaria"), True),
            ("Local cadastrado", v.get("local_cadastro"), False),
            ("Responsável", v.get("responsavel"), False),
        ]),
        Paragraph("DADOS DA VISTORIA", st["secao"]),
    ]
    itens = [
        ("Situação aparente", v.get("situacao"), False),
        ("Plotado", v.get("plotado"), False),
        ("Placa legível", v.get("placa_legivel"), False),
        ("Odômetro / Horímetro", v.get("odometro"), False),
        ("Local verificado", v.get("local_visto"), True),
        ("Onde o veículo é utilizado", v.get("uso_local"), True),
        ("Nome do motorista", v.get("motorista"), True),
        *_linhas_itens(v),
        ("Início", fmt_data(v.get("inicio")), False),
        ("Término", fmt_data(v.get("fim")), False),
        ("Localização (GPS)", _fmt_gps(v.get("lat"), v.get("lon"), v.get("acc")), True),
        ("Vistoriador", v.get("vistoriador_nome"), False),
        ("Matrícula", v.get("vistoriador_matricula"), False),
    ]
    if v.get("origem") == "campo":
        itens.append(("Observação do cadastro",
                      "Veículo não constava na lista do patrimônio; cadastrado em campo.", True))
    itens.append(("Observações", v.get("obs"), True))
    story.append(_tabela_campos(itens))

    por_id = {e["id"]: e for e in etapas}
    puladas = [por_id[e]["titulo"] for e in v.get("puladas_lista", []) if e in por_id]
    resumo = []
    if v.get("situacao") == "Não localizado":
        resumo.append("<b>Veículo não localizado</b> durante a vistoria; não há registro fotográfico.")
    if puladas:
        resumo.append("Fotos não realizadas: " + escape("; ".join(puladas)) + ".")
    if v.get("sem_avarias"):
        resumo.append("Avarias: o vistoriador registrou que não há avarias visíveis.")
    if resumo:
        story.append(Spacer(1, 6))
        for linha in resumo:
            story.append(Paragraph(linha, st["corpo"]))

    # ---- registro fotográfico
    if fotos:
        story.append(PageBreak())  # o registro fotográfico começa na 2ª página
        story.append(Paragraph("REGISTRO FOTOGRÁFICO", st["secao"]))
        ordem = {e["id"]: i for i, e in enumerate(etapas)}
        fotos = sorted(fotos, key=lambda f: (ordem.get(f["etapa"], 99), f.get("seq", 1)))
        total_etapa = {}
        for f in fotos:
            total_etapa[f["etapa"]] = total_etapa.get(f["etapa"], 0) + 1
        celulas = []
        largura = (A4[0] - 3 * cm) / 2
        for n, f in enumerate(fotos, 1):
            caminho = Path(pasta_fotos) / f["arquivo"]
            try:
                img = _imagem(caminho, largura - 0.5 * cm, 6.3 * cm)
            except Exception:
                img = Paragraph(f"[Imagem não pôde ser exibida: {escape(f['arquivo'])}]", st["peq"])
            titulo = por_id.get(f["etapa"], {}).get("titulo", f["etapa"])
            if total_etapa[f["etapa"]] > 1:
                titulo += f" ({f.get('seq', 1)}/{total_etapa[f['etapa']]})"
            legenda = f"<b>{n}. {escape(titulo)}</b>"
            if f.get("legenda"):
                legenda += f" — {escape(f['legenda'])}"
            detalhe = fmt_data(f.get("criado_em"))
            gps = _fmt_gps(f.get("lat"), f.get("lon"), f.get("acc"))
            if gps:
                detalhe += f" · GPS {gps}"
            legenda += f"<br/><font size='6.5' color='#555555'>{escape(detalhe)}</font>"
            celulas.append([img, Spacer(1, 2), Paragraph(legenda, st["cel"])])
        if len(celulas) % 2:
            celulas.append("")
        linhas = [celulas[i:i + 2] for i in range(0, len(celulas), 2)]
        t = Table(linhas, colWidths=[largura, largura])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(t)

    # ---- assinaturas
    larg = (A4[0] - 3 * cm - 1.2 * cm) / 2
    assin = Table([
        ["", "", ""],
        [Paragraph(f"<b>{escape(v.get('vistoriador_nome') or '')}</b><br/>"
                   f"Matrícula: {escape(v.get('vistoriador_matricula') or '')}<br/>"
                   "Responsável pela vistoria", st["cel"]), "",
         Paragraph("<b>&nbsp;</b><br/>Nome / Matrícula<br/>"
                   "Responsável do setor (acompanhou a vistoria)", st["cel"])],
    ], colWidths=[larg, 1.2 * cm, larg], rowHeights=[1.6 * cm, None])
    assin.setStyle(TableStyle([
        ("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black),
        ("LINEABOVE", (2, 1), (2, 1), 0.6, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(Spacer(1, 14))
    story.append(KeepTogether([assin]))

    emitido = "Relatório emitido em " + datetime.now().strftime("%d/%m/%Y %H:%M")
    if v.get("vistoriador_nome"):
        emitido += (f" · Vistoriador: {v['vistoriador_nome']}"
                    f" (Mat. {v.get('vistoriador_matricula') or '-'})")
    return _montar(destino, story, f"Relatório fotográfico {v['chave']}", emitido, controle)


# ------------------------------------------------------ relatório consolidado --
NOMES_STATUS = {"pendente": "Pendente", "em_andamento": "Em andamento", "concluido": "Concluído"}


def _tabela_quadro(veiculos):
    larg = A4[0] - 3 * cm
    cab = ["Identificação", "Patrimônio", "Tipo", "Marca / Modelo", "Secretaria",
           "Situação", "Status", "Alertas"]
    cw = [2.4 * cm, 1.7 * cm, 1.9 * cm, 3.1 * cm, 2.9 * cm, 1.9 * cm, 1.7 * cm]
    cw.append(larg - sum(cw))
    dados = [[_p(c, "rotulo") for c in cab]]
    for v in veiculos:
        dados.append([
            _p(v["chave"], "cel"), _p(v["patrimonio"], "cel"), _p(v["tipo"], "cel"),
            _p(v["marca_modelo"], "cel"), _p(v["secretaria"], "cel"),
            _p(v["situacao"], "cel"), _p(NOMES_STATUS.get(v["status"], v["status"]), "cel"),
            _p("; ".join(v.get("alertas_texto", [])), "cel"),
        ])
    t = Table(dados, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, BORDA),
        ("BACKGROUND", (0, 0), (-1, 0), CINZA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _emitido_por(usuario):
    return ("Relatório emitido em " + datetime.now().strftime("%d/%m/%Y %H:%M")
            + f" por {usuario['nome']} (Mat. {usuario['matricula']})")


def gerar_relatorio_geral(resumo, veiculos, usuario, destino, controle=None):
    """resumo: dict de /api/resumo; veiculos: lista de dicts com 'alertas'."""
    st = ST
    story = [
        Paragraph("RELATÓRIO CONSOLIDADO DO LEVANTAMENTO DA FROTA", st["titulo"]),
        Paragraph("Veículos e máquinas de propriedade do Município", st["sub"]),
        Paragraph("RESUMO GERAL", st["secao"]),
        _tabela_campos([
            ("Total de veículos", resumo["total"], False),
            ("Fotos registradas", resumo["total_fotos"], False),
            ("Concluídos", resumo["concluido"], False),
            ("Em andamento", resumo["em_andamento"], False),
            ("Pendentes", resumo["pendente"], False),
            ("Não localizados", resumo["nao_localizado"], False),
            ("Cadastrados em campo", resumo["campo"], False),
            ("Com fotos puladas", resumo["puladas"], False),
            ("Com avarias registradas", resumo["avarias"], False),
            ("Itens de segurança ausentes", resumo["itens"], False),
            ("Placa ilegível", resumo["placa_ilegivel"], False),
            ("Plotados / Não plotados", f"{resumo['plotado_sim']} / {resumo['plotado_nao']}", False),
            ("Sem GPS / GPS impreciso", resumo["gps"], False),
        ]),
        Paragraph("POR SECRETARIA", st["secao"]),
    ]
    cab = ["Secretaria", "Total", "Concluídos", "Em and.", "Pendentes", "Não loc."]
    dados = [[_p(c, "rotulo") for c in cab]]
    for s_ in resumo["por_secretaria"]:
        dados.append([_p(s_["nome"], "cel"), _p(s_["total"], "cel"),
                      _p(s_["concluido"], "cel"), _p(s_["em_andamento"], "cel"),
                      _p(s_["pendente"], "cel"), _p(s_["nao_localizado"], "cel")])
    larg = A4[0] - 3 * cm
    t = Table(dados, colWidths=[larg - 5 * 2.2 * cm] + [2.2 * cm] * 5, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, BORDA),
        ("BACKGROUND", (0, 0), (-1, 0), CINZA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [t, PageBreak(), Paragraph("QUADRO GERAL DA FROTA", st["secao"]), _tabela_quadro(veiculos)]
    return _montar(destino, story, "Relatório consolidado da frota", _emitido_por(usuario), controle)


def gerar_relatorio_secretaria(nome, veiculos, usuario, destino, controle=None):
    """Relatório de uma secretaria: resumo + quadro dos veículos dela."""
    st = ST
    n = lambda f: sum(1 for v in veiculos if f(v))
    story = [
        Paragraph("RELATÓRIO DA FROTA POR SECRETARIA", st["titulo"]),
        Paragraph(f"<b>{escape(nome)}</b>", st["sub"]),
        Paragraph("RESUMO", st["secao"]),
        _tabela_campos([
            ("Total de veículos", len(veiculos), False),
            ("Concluídos", n(lambda v: v["status"] == "concluido"), False),
            ("Em andamento", n(lambda v: v["status"] == "em_andamento"), False),
            ("Pendentes", n(lambda v: v["status"] == "pendente"), False),
            ("Não localizados", n(lambda v: "nao_localizado" in v["alertas"]), False),
            ("Em uso", n(lambda v: v["situacao"] == "Em uso"), False),
            ("Parados", n(lambda v: v["situacao"] == "Parado"), False),
            ("Em oficina", n(lambda v: v["situacao"] == "Em oficina"), False),
            ("Inservíveis / sucata", n(lambda v: v["situacao"] == "Inservível / sucata"), False),
            ("Plotados / Não plotados",
             f"{n(lambda v: v['plotado'] == 'Sim')} / {n(lambda v: v['plotado'] == 'Não')}", False),
            ("Com avarias registradas", n(lambda v: "avarias" in v["alertas"]), False),
            ("Itens de segurança ausentes", n(lambda v: "itens_faltando" in v["alertas"]), False),
        ]),
        Paragraph("VEÍCULOS E MÁQUINAS DA SECRETARIA", st["secao"]),
        _tabela_quadro(veiculos),
    ]
    return _montar(destino, story, f"Frota - {nome}", _emitido_por(usuario), controle)


def gerar_pendencias(grupos, usuario, destino, controle=None):
    """grupos: lista de (secretaria, [veiculos pendentes]). Uma seção por secretaria,
    com campo de ciência do chefe do setor."""
    st = ST
    larg = A4[0] - 3 * cm
    story = []
    for k, (nome, vs) in enumerate(grupos):
        if k:
            story.append(PageBreak())
        story += [
            Paragraph("PENDÊNCIAS DE VISTORIA", st["titulo"]),
            Paragraph(f"<b>{escape(nome)}</b> · {len(vs)} veículo(s)/máquina(s) a vistoriar", st["sub"]),
            Paragraph("Relação dos bens ainda sem vistoria concluída. Favor disponibilizar os veículos "
                      "e máquinas listados para o levantamento fotográfico.", st["corpo"]),
        ]
        cab = ["Identificação", "Patrimônio", "Tipo", "Marca / Modelo", "Local cadastrado",
               "Responsável", "Status"]
        cw = [2.4 * cm, 1.8 * cm, 2.2 * cm, 3.4 * cm, 3.0 * cm, 2.6 * cm]
        cw.append(larg - sum(cw))
        dados = [[_p(c, "rotulo") for c in cab]]
        for v in vs:
            dados.append([_p(v["chave"], "cel"), _p(v["patrimonio"], "cel"), _p(v["tipo"], "cel"),
                          _p(v["marca_modelo"], "cel"), _p(v["local_cadastro"], "cel"),
                          _p(v["responsavel"], "cel"), _p(NOMES_STATUS.get(v["status"], ""), "cel")])
        t = Table(dados, colWidths=cw, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, BORDA), ("BACKGROUND", (0, 0), (-1, 0), CINZA),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        assin = Table([["", ""], [Paragraph("Ciente do chefe do setor (nome e matrícula)", st["cel"]),
                                  Paragraph("Data: ____/____/________", st["cel"])]],
                      colWidths=[larg * 0.62, larg * 0.38], rowHeights=[1.6 * cm, None])
        assin.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 2), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [Spacer(1, 16), KeepTogether([assin])]
    return _montar(destino, story, "Pendências de vistoria", _emitido_por(usuario), controle)
