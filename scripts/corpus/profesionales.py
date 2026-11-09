"""Corpus SINTÉTICO de facturas de profesionales (autónomos y pequeñas firmas).

Genera 5 PDF con maquetaciones distintas y, para cada uno, un JSON con los valores esperados
para medir el extractor de datos (app/extraccion). Familia: abogada, arquitecto, diseñadora,
consultora (S.L.) y consultor autónomo.

TODOS LOS DATOS SON FICTICIOS: nombres, domicilios, teléfonos, correos (dominio .example),
números de colegiado y registro, IBAN (ES00… / ES12 3456…). Los NIF/CIF tienen dígito o letra de
control válidos (app.nif.completar_cif / letra DNI), pero no corresponden a ninguna persona o
entidad concreta de forma intencionada.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/profesionales.py            # genera y verifica con pdftotext
    python scripts/corpus/profesionales.py --sin-verificar

Determinista: no hay aleatoriedad (semilla fija por si se amplía) y los PDF se generan en modo
«invariant» de reportlab (sin fecha de creación ni identificador variables).
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4, letter  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import completar_cif, es_valido  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus" / "profesionales"
SEMILLA = 20261109
random.seed(SEMILLA)

D = Decimal
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def dni(n: int) -> str:
    return f"{n:08d}{LETRAS_DNI[n % 23]}"


def q2(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(x) -> str | None:
    return None if x is None else str(q2(x))


def num(x, miles=True, euro=False, sufijo="") -> str:
    """Formato español: 1.234,56 (miles=True) o 1234,56 (miles=False)."""
    x = q2(x)
    ent, dec = f"{abs(x):.2f}".split(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", ".")
    s = f"{'-' if x < 0 else ''}{ent},{dec}"
    if euro:
        s += " €"
    return s + sufijo


def cant(x, decimales=2) -> str:
    x = D(x)
    if decimales == 0:
        return str(int(x))
    return f"{x:.{decimales}f}".replace(".", ",")


def fecha_larga(f: date) -> str:
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"


def calcular(lineas: list[dict], pct_iva=D("21"), pct_ret=None) -> dict:
    for ln in lineas:
        if ln.get("cantidad") is not None and ln.get("precio") is not None:
            bruto = D(ln["cantidad"]) * D(ln["precio"])
            dto = D(ln.get("dto") or 0)
            ln["importe"] = q2(bruto * (D(100) - dto) / D(100))
        else:
            ln["importe"] = q2(ln["importe"])
    base = sum((ln["importe"] for ln in lineas), D("0"))
    cuota = q2(base * pct_iva / D(100))
    total = base + cuota
    ret = q2(base * D(pct_ret) / D(100)) if pct_ret is not None else None
    pagar = (total - ret) if ret is not None else None
    return {"base": base, "cuota": cuota, "total": total, "pct_iva": D(pct_iva),
            "pct_ret": D(pct_ret) if pct_ret is not None else None, "ret": ret, "pagar": pagar}


# ----------------------------------------------------------------------------------------------
# utilidades de dibujo
# ----------------------------------------------------------------------------------------------
def tx(c, x, y, s, fuente="Helvetica", tam=9, color=black, alin="l"):
    c.setFont(fuente, tam)
    c.setFillColor(color)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[alin](x, y, s)


def parrafo(c, x, y, s, ancho, fuente="Helvetica", tam=9, salto=None, color=black) -> float:
    c.setFont(fuente, tam)
    c.setFillColor(color)
    for ln in simpleSplit(s, fuente, tam, ancho):
        c.drawString(x, y, ln)
        y -= salto or tam * 1.3
    return y


def nuevo_canvas(ruta: Path, tam_pagina, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=tam_pagina, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    return c


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido)
# ----------------------------------------------------------------------------------------------
NIF_ABOGADA = dni(50123456)
NIF_ARQUITECTO = dni(21987654)
NIF_DISENADORA = dni(78456123)
NIF_CONSULTOR = dni(30654987)
CIF_INMOBILIARIA = completar_cif("B", "4512345")
CIF_PROMOTORA = completar_cif("B", "4698765")
CIF_SIDRERIA = completar_cif("B", "2034567")
CIF_BREZO = completar_cif("B", "5011223")
CIF_CLINICA = completar_cif("B", "5099887")
CIF_TALLERES = completar_cif("B", "4177889")


# ==============================================================================================
# 01 · Abogada: horas, dos partidas al 21 %, IRPF 15 %
# ==============================================================================================
def doc_abogada() -> dict:
    lineas = [
        {"concepto": "Estudio de la documentación y preparación de la demanda", "cantidad": D("6.50"),
         "precio": D("120.00")},
        {"concepto": "Asistencia a la audiencia previa en el Juzgado de Primera Instancia", "cantidad": D("2.50"),
         "precio": D("95.00")},
    ]
    t = calcular(lineas, pct_ret=15)
    d = {
        "archivo": "prof_01_abogada_horas_irpf15.pdf",
        "estilo": "Abogada autónoma: emisor arriba-izquierda con DNI, caja 'Cliente', tabla Horas/Precio hora, "
                  "dos partidas al 21 %, totales apilados, IRPF 15 %, Helvetica, A4",
        "empresa_nif": NIF_ABOGADA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "2026/045",
        "fecha": date(2026, 10, 14),
        "fecha_txt": "14/10/2026",
        "emisor": {"nombre": "Lucía Ferrándiz Olmedo", "nif": NIF_ABOGADA, "domicilio_contiene": "28014"},
        "destinatario": {"nombre": "Inmobiliaria Arcoíris del Tajo, S.L.", "nif": CIF_INMOBILIARIA,
                         "domicilio_contiene": "45004"},
        "lineas": lineas, "t": t,
        "referencias": {"expediente": "EXP-118/26"},
        "iban": "ES0012345678901234567890",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "notas": "Documento de la propia profesional (usuaria de la aplicación) titulado 'FACTURA' sin marcas "
                 "VERI*FACTU: se espera tratarlo como documento preparatorio. Distractor: fecha de vencimiento "
                 "13/11/2026 bajo la fecha de emisión. IRPF 15 % sobre la base: Total factura 1.231,18 frente a "
                 "Total a pagar 1.078,55. Cuota y retención con redondeo half-up (213,675 y 152,625).",
    }

    def render(ruta: Path):
        W, H = A4
        az = HexColor("#2B3A55")
        gris = HexColor("#E8EBF0")
        c = nuevo_canvas(ruta, A4, "Factura 2026/045", "Lucía Ferrándiz Olmedo")
        x0 = 42
        tx(c, x0, H - 52, "LUCÍA FERRÁNDIZ OLMEDO", "Helvetica-Bold", 14, az)
        y = H - 68
        for s in ["Abogada · Colegiada n.º 99123", "C/ Cedro Azul, 12, 3.º B", "28014 Madrid",
                  f"NIF: {NIF_ABOGADA}", "Tel. 600 000 000 · lferrandiz@despacho.example"]:
            tx(c, x0, y, s, "Helvetica", 9)
            y -= 12
        tx(c, W - 42, H - 56, "FACTURA", "Helvetica-Bold", 24, az, "r")
        c.setStrokeColor(az)
        c.setLineWidth(0.8)
        c.rect(W - 232, H - 140, 190, 62)
        for i, (et, val) in enumerate([("Nº:", d["numero"]), ("Fecha:", d["fecha_txt"]),
                                        ("Vencimiento:", "13/11/2026")]):
            yy = H - 96 - i * 17
            tx(c, W - 222, yy, et, "Helvetica-Bold", 9.5)
            tx(c, W - 52, yy, val, "Helvetica", 9.5, alin="r")
        # caja cliente
        tx(c, x0, H - 172, "Cliente", "Helvetica-Bold", 10, az)
        c.rect(x0, H - 252, 270, 72)
        y = H - 196
        tx(c, x0 + 10, y, d["destinatario"]["nombre"], "Helvetica-Bold", 10)
        for s in ["Avda. de los Pinos Altos, 45, local 2", "45004 Toledo",
                  f"CIF: {CIF_INMOBILIARIA[0]}-{CIF_INMOBILIARIA[1:]}"]:
            y -= 14
            tx(c, x0 + 10, y, s, "Helvetica", 9.5)
        tx(c, x0, H - 280, "Asunto: Reclamación de cantidad (procedimiento ordinario)", "Helvetica", 9)
        tx(c, x0, H - 293, "Ref. expediente: EXP-118/26", "Helvetica", 9)
        # tabla
        yt = H - 330
        c.setFillColor(gris)
        c.rect(x0, yt - 5, W - 84, 18, stroke=0, fill=1)
        tx(c, x0 + 6, yt, "Concepto", "Helvetica-Bold", 9)
        tx(c, 365, yt, "Horas", "Helvetica-Bold", 9, alin="r")
        tx(c, 455, yt, "Precio hora", "Helvetica-Bold", 9, alin="r")
        tx(c, W - 48, yt, "Importe", "Helvetica-Bold", 9, alin="r")
        y = yt - 24
        for ln in lineas:
            y2 = parrafo(c, x0 + 6, y, ln["concepto"], 255, "Helvetica", 9, 12)
            tx(c, 365, y, cant(ln["cantidad"]), "Helvetica", 9, alin="r")
            tx(c, 455, y, num(ln["precio"], euro=True), "Helvetica", 9, alin="r")
            tx(c, W - 48, y, num(ln["importe"], euro=True), "Helvetica", 9, alin="r")
            y = y2 - 8
        c.setStrokeColor(az)
        c.line(x0, y + 4, W - 42, y + 4)
        # totales apilados
        y -= 22
        filas = [("Base imponible", num(t["base"], euro=True)),
                 ("IVA 21%", num(t["cuota"], euro=True)),
                 ("Total factura", num(t["total"], euro=True)),
                 ("Retención IRPF 15%", num(-t["ret"], euro=True)),
                 ("Total a pagar", num(t["pagar"], euro=True))]
        for et, val in filas:
            f = "Helvetica-Bold" if et in ("Total factura", "Total a pagar") else "Helvetica"
            if et == "Total a pagar":
                c.line(W - 250, y + 13, W - 42, y + 13)
            tx(c, W - 250, y, et, f, 10)
            tx(c, W - 48, y, val, f, 10, alin="r")
            y -= 17
        # pago
        yp = 175
        tx(c, x0, yp, "Forma de pago: Transferencia bancaria", "Helvetica", 9)
        tx(c, x0, yp - 13, "IBAN: ES00 1234 5678 9012 3456 7890", "Helvetica", 9)
        tx(c, x0, yp - 26, f"Indique en el concepto el número de factura {d['numero']}.", "Helvetica", 9)
        parrafo(c, x0, 62, "Responsable del tratamiento: Lucía Ferrándiz Olmedo. Sus datos se tratan para la gestión de "
                "la relación profesional y el cumplimiento de obligaciones legales. Puede ejercer sus derechos "
                "dirigiéndose al domicilio profesional indicado.", W - 84, "Helvetica", 6.5, 8, HexColor("#666666"))
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 02 · Arquitecto: concepto multilínea, sin cantidad/precio, IRPF 7 %, totales en cabecera+fila
# ==============================================================================================
def doc_arquitecto() -> dict:
    lineas = [
        {"concepto": "Honorarios profesionales. Fase 2: Proyecto de ejecución de vivienda unifamiliar aislada con "
                     "garaje en la parcela 14 de la urbanización Los Almendros Altos, término municipal de "
                     "Villanueva del Prado. Incluye memoria, planos, pliego de condiciones, mediciones y presupuesto.",
         "cantidad": None, "precio": None, "importe": D("4862.40")},
        {"concepto": "Redacción del estudio básico de seguridad y salud", "cantidad": None, "precio": None,
         "importe": D("615.75")},
    ]
    t = calcular(lineas, pct_ret=7)
    nif_txt = f"{NIF_ARQUITECTO[:2]}.{NIF_ARQUITECTO[2:5]}.{NIF_ARQUITECTO[5:8]}-{NIF_ARQUITECTO[8]}"
    d = {
        "archivo": "prof_02_arquitecto_multilinea_irpf7.pdf",
        "estilo": "Arquitecto autónomo: Times, NIF con puntos (21.987.654-E), 'Facturar a' sin caja, fecha en "
                  "letra, concepto multilínea sin cantidad/precio, totales en fila de cabecera con valores debajo, "
                  "IRPF 7 %, 'Líquido a pagar', A4",
        "empresa_nif": NIF_ARQUITECTO,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "A-26/0112",
        "fecha": date(2026, 11, 3),
        "fecha_txt": fecha_larga(date(2026, 11, 3)),
        "emisor": {"nombre": "Andrés Lucena Barrachina", "nif": NIF_ARQUITECTO, "domicilio_contiene": "46002"},
        "destinatario": {"nombre": "Promociones Ribera Calma, S.L.", "nif": CIF_PROMOTORA,
                         "domicilio_contiene": "46100"},
        "lineas": lineas, "t": t,
        "referencias": {"proyecto": "P-26/07", "expediente": "LU-2026/0412"},
        "iban": "ES1234567890123456789012",
        "forma_pago_contiene": "transferencia a 30 días",
        "indicios": [],
        "notas": "Etiquetas sin separador ('Núm. factura  A-26/0112', 'Fecha de emisión  3 de noviembre de 2026'). "
                 "NIF del emisor escrito con puntos y guion. Primera línea de detalle de 4 renglones. Totales en "
                 "una fila de etiquetas con la fila de importes debajo (retención en negativo). IRPF 7 % por inicio "
                 "de actividad: Total factura 6.628,56 frente a Líquido a pagar 6.245,09. IBAN dentro de una frase.",
    }

    def render(ruta: Path):
        W, H = A4
        c = nuevo_canvas(ruta, A4, "Factura A-26/0112", "Andrés Lucena Barrachina")
        x0 = 56
        tx(c, x0, H - 60, "Andrés Lucena Barrachina", "Times-Bold", 17)
        y = H - 76
        for s in ["ARQUITECTO · Colegiado n.º 9871", "Plaza del Reloj Viejo, 3, 1.º", "46002 València",
                  f"N.I.F.: {nif_txt}", "estudio@lucenaarquitectura.example"]:
            tx(c, x0, y, s, "Times-Roman", 10)
            y -= 13
        tx(c, W - 56, H - 62, "FACTURA", "Times-Bold", 20, alin="r")
        tx(c, W - 200, H - 86, "Núm. factura", "Times-Bold", 10)
        tx(c, W - 56, H - 86, d["numero"], "Times-Roman", 10, alin="r")
        tx(c, W - 200, H - 100, "Fecha de emisión", "Times-Bold", 10)
        tx(c, W - 56, H - 114, d["fecha_txt"], "Times-Roman", 10, alin="r")
        c.setLineWidth(0.6)
        c.line(x0, H - 150, W - 56, H - 150)
        # destinatario sin caja, con barra lateral
        tx(c, x0, H - 172, "Facturar a:", "Times-Italic", 10)
        c.setLineWidth(2)
        c.setStrokeColor(HexColor("#8A6D3B"))
        c.line(x0 + 2, H - 182, x0 + 2, H - 240)
        c.setStrokeColor(black)
        y = H - 190
        tx(c, x0 + 12, y, d["destinatario"]["nombre"], "Times-Bold", 11)
        for s in [f"CIF {CIF_PROMOTORA}", "C/ Maestro Ferrer Nuevo, 21, bajo", "46100 Burjassot (Valencia)"]:
            y -= 14
            tx(c, x0 + 12, y, s, "Times-Roman", 10)
        tx(c, 330, H - 190, "Proyecto: P-26/07", "Times-Roman", 10)
        tx(c, 330, H - 204, "Expediente municipal: LU-2026/0412", "Times-Roman", 10)
        tx(c, 330, H - 218, "Periodo: octubre 2026", "Times-Roman", 10)
        # tabla concepto / importe
        yt = H - 280
        c.setLineWidth(0.8)
        c.line(x0, yt + 14, W - 56, yt + 14)
        tx(c, x0, yt, "Concepto", "Times-Bold", 10.5)
        tx(c, W - 56, yt, "Importe (€)", "Times-Bold", 10.5, alin="r")
        c.line(x0, yt - 6, W - 56, yt - 6)
        y = yt - 24
        for ln in lineas:
            y2 = parrafo(c, x0, y, ln["concepto"], 340, "Times-Roman", 10.5, 13.5)
            tx(c, W - 56, y, num(ln["importe"]), "Times-Roman", 10.5, alin="r")
            y = y2 - 10
        c.setLineWidth(0.4)
        c.line(x0, y + 4, W - 56, y + 4)
        # totales: fila de etiquetas y fila de valores
        y -= 34
        cols = [("Base imponible", num(t["base"])), ("I.V.A. 21 %", num(t["cuota"])),
                ("Total factura", num(t["total"])), ("Retención IRPF 7 %", num(-t["ret"])),
                ("Líquido a pagar", num(t["pagar"]))]
        ancho = (W - 112) / len(cols)
        c.setLineWidth(0.6)
        c.rect(x0, y - 26, W - 112, 44)
        c.line(x0, y - 4, W - 56, y - 4)
        for i, (et, val) in enumerate(cols):
            cx = x0 + ancho * i + ancho / 2
            if i:
                c.line(x0 + ancho * i, y - 26, x0 + ancho * i, y + 18)
            tx(c, cx, y + 4, et, "Times-Bold", 9.5, alin="c")
            tx(c, cx, y - 18, val, "Times-Bold" if i == 4 else "Times-Roman", 10.5, alin="c")
        y -= 60
        y = parrafo(c, x0, y, "Retención del 7 % por inicio de actividad profesional (art. 101.5 de la Ley 35/2006 "
                    "del IRPF).", W - 112, "Times-Italic", 9.5)
        y -= 8
        parrafo(c, x0, y, "Forma de pago: transferencia a 30 días a la cuenta ES12 3456 7890 1234 5678 9012, "
                f"indicando el número de factura.", W - 112, "Times-Roman", 10)
        tx(c, W / 2, 50, "Andrés Lucena Barrachina · Arquitecto · Plaza del Reloj Viejo, 3, 1.º · 46002 València",
           "Times-Roman", 8, HexColor("#555555"), "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 03 · Diseñadora: código + cantidad antes del concepto, Dto. %, sin separador de miles, IRPF 15 %
# ==============================================================================================
def doc_disenadora() -> dict:
    lineas = [
        {"codigo": "ID-01", "concepto": "Diseño de identidad corporativa: logotipo, paleta cromática y manual "
                                        "básico de uso", "cantidad": D("1"), "precio": D("1200.00"), "dto": D("10")},
        {"codigo": "RS-02", "concepto": "Adaptación de piezas gráficas para redes sociales", "cantidad": D("12"),
         "precio": D("45.00"), "dto": D("0")},
        {"codigo": "MQ-03", "concepto": "Maquetación de folleto díptico A5", "cantidad": D("3"),
         "precio": D("85.50"), "dto": D("5")},
    ]
    t = calcular(lineas, pct_ret=15)
    d = {
        "archivo": "prof_03_disenadora_dto_irpf15.pdf",
        "estilo": "Diseñadora autónoma: logotipo + emisor arriba-izquierda con DNI con guion, recuadro 'Datos del "
                  "cliente' a la derecha, tabla Cód./Cant./Descripción/Precio/Dto. %/Importe, importes sin "
                  "separador de miles con €, fecha dd-mm-yy, separador ' - ', IRPF 15 %",
        "empresa_nif": NIF_DISENADORA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "D26-019",
        "fecha": date(2026, 10, 28),
        "fecha_txt": "28-10-26",
        "emisor": {"nombre": "Nerea Goikoetxea Prado", "nif": NIF_DISENADORA, "domicilio_contiene": "48006"},
        "destinatario": {"nombre": "Sidrería Mendi Urdina, S.L.", "nif": CIF_SIDRERIA, "domicilio_contiene": "20180"},
        "lineas": lineas, "t": t,
        "referencias": {"pedido": "PO-7731"},
        "iban": "ES0022223333444455556666",
        "forma_pago_contiene": "transferencia en 15 días",
        "indicios": ["descuento"],
        "notas": "Cantidad antes de la descripción y columnas extra (Cód., Dto. %): el importe de línea ya incluye el "
                 "descuento (1 x 1200,00 -10 % = 1080,00; 3 x 85,50 -5 % = 243,68 con redondeo half-up de 243,675). "
                 "Primera descripción en 2 renglones. 'Subtotal' como etiqueta de base y 'Total' a secas como total "
                 "factura; Total a pagar 1975,50 tras IRPF 15 %. Fecha con año de 2 cifras.",
    }

    def render(ruta: Path):
        W, H = A4
        acento = HexColor("#C2410C")
        claro = HexColor("#FFF1E6")
        c = nuevo_canvas(ruta, A4, "Factura D26-019", "Nerea Goikoetxea Prado")
        x0 = 40
        c.setFillColor(acento)
        c.roundRect(x0, H - 92, 50, 50, 6, stroke=0, fill=1)
        tx(c, x0 + 25, H - 74, "ng", "Helvetica-Bold", 22, white, "c")
        xe = x0 + 62
        tx(c, xe, H - 52, "Nerea Goikoetxea Prado", "Helvetica-Bold", 12)
        y = H - 65
        for s in ["Diseño gráfico y web", "Iturribide Berria kalea, 7 - 48006 Bilbao",
                  f"DNI {NIF_DISENADORA[:8]}-{NIF_DISENADORA[8]}", "hola@nereagp.example · 611 000 000"]:
            tx(c, xe, y, s, "Helvetica", 8.5, HexColor("#333333"))
            y -= 11
        tx(c, W - 40, H - 58, "FACTURA", "Helvetica-Bold", 20, acento, "r")
        tx(c, W - 40, H - 76, f"Número - {d['numero']}", "Helvetica", 9, alin="r")
        tx(c, W - 40, H - 89, f"Fecha factura - {d['fecha_txt']}", "Helvetica", 9, alin="r")
        # recuadro cliente a la derecha
        bx, bw = 315, W - 40 - 315
        c.setFillColor(acento)
        c.rect(bx, H - 140, bw, 16, stroke=0, fill=1)
        tx(c, bx + 8, H - 135, "Datos del cliente", "Helvetica-Bold", 8.5, white)
        c.setStrokeColor(acento)
        c.setFillColor(claro)
        c.rect(bx, H - 215, bw, 75, stroke=1, fill=1)
        y = H - 156
        tx(c, bx + 8, y, d["destinatario"]["nombre"], "Helvetica-Bold", 9)
        for s in [f"CIF: {CIF_SIDRERIA}", "Barrio Arteaga, 22", "20180 Oiartzun (Gipuzkoa)"]:
            y -= 13
            tx(c, bx + 8, y, s, "Helvetica", 8.5)
        tx(c, x0, H - 160, "Pedido: PO-7731", "Helvetica", 8.5)
        tx(c, x0, H - 173, "Encargo: rediseño de marca 2027", "Helvetica", 8.5)
        # tabla
        yt = H - 250
        c.setFillColor(acento)
        c.rect(x0, yt - 6, W - 80, 18, stroke=0, fill=1)
        cab = [(x0 + 4, "Cód.", "l"), (112, "Cant.", "r"), (122, "Descripción", "l"), (420, "Precio", "r"),
               (468, "Dto. %", "r"), (W - 44, "Importe", "r")]
        for x, s, a in cab:
            tx(c, x, yt, s, "Helvetica-Bold", 8.5, white, a)
        y = yt - 22
        for i, ln in enumerate(lineas):
            y2 = parrafo(c, 122, y, ln["concepto"], 245, "Helvetica", 8.5, 11)
            tx(c, x0 + 4, y, ln["codigo"], "Helvetica", 8.5)
            tx(c, 112, y, cant(ln["cantidad"], 0), "Helvetica", 8.5, alin="r")
            tx(c, 420, y, num(ln["precio"], miles=False, euro=True), "Helvetica", 8.5, alin="r")
            tx(c, 468, y, cant(ln["dto"], 0), "Helvetica", 8.5, alin="r")
            tx(c, W - 44, y, num(ln["importe"], miles=False, euro=True), "Helvetica", 8.5, alin="r")
            c.setStrokeColor(HexColor("#DDDDDD"))
            c.line(x0, y2 + 3, W - 40, y2 + 3)
            y = y2 - 9
        # totales apilados
        y -= 20
        filas = [("Subtotal", t["base"]), ("I.V.A. 21%", t["cuota"]), ("Total", t["total"]),
                 ("Retención I.R.P.F. 15%", -t["ret"]), ("Total a pagar", t["pagar"])]
        for et, val in filas:
            fuerte = et in ("Total", "Total a pagar")
            if et == "Total a pagar":
                c.setFillColor(claro)
                c.rect(W - 240, y - 5, 200, 17, stroke=0, fill=1)
            tx(c, W - 234, y, et, "Helvetica-Bold" if fuerte else "Helvetica", 9)
            tx(c, W - 44, y, num(val, miles=False, euro=True), "Helvetica-Bold" if fuerte else "Helvetica", 9,
               alin="r")
            y -= 15
        tx(c, x0, 150, "Pago por transferencia en 15 días", "Helvetica-Bold", 8.5)
        tx(c, x0, 138, "IBAN ES00 2222 3333 4444 5555 6666", "Helvetica", 8.5)
        tx(c, W / 2, 60, "Gracias por confiar en mi trabajo", "Helvetica-Oblique", 8, acento, "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 04 · Pequeña consultora (S.L.): PROFORMA simple sin retención, Courier, emisor en el pie
# ==============================================================================================
def doc_consultora_sl() -> dict:
    lineas = [
        {"concepto": "Auditoría RGPD y plan de adecuación - octubre 2026", "cantidad": D("1"),
         "precio": D("1450.00")},
    ]
    t = calcular(lineas, pct_ret=None)
    d = {
        "archivo": "prof_04_consultora_sl_proforma_simple.pdf",
        "estilo": "Pequeña consultora S.L.: FACTURA PROFORMA en Courier, nombre comercial arriba y datos fiscales "
                  "del emisor en el pie, cliente en recuadro sin etiqueta, separador '. -', fecha dd.mm.yyyy, una "
                  "sola línea, sin retención, 'Base'/'Cuota IVA'/'Importe total', pago por confirming sin IBAN",
        "empresa_nif": CIF_BREZO,
        "tipo_documento": "proforma",
        "situacion_esperada": "preparatorio",
        "numero": "PF-26/0093",
        "fecha": date(2026, 11, 5),
        "fecha_txt": "05.11.2026",
        "emisor": {"nombre": "Brezo Analítica Consultores, S.L.", "nif": CIF_BREZO, "domicilio_contiene": "50005"},
        "destinatario": {"nombre": "Clínica Dental Sonrisa Norte, S.L.P.", "nif": CIF_CLINICA,
                         "domicilio_contiene": "50012"},
        "lineas": lineas, "t": t,
        "referencias": {"contrato": "CT-2026-015"},
        "iban": None,
        "forma_pago_contiene": "confirming a 60 días",
        "indicios": [],
        "notas": "Caso simple sin retención: total_a_pagar es null porque no figura un importe a pagar distinto del "
                 "total. Proforma de la propia empresa usuaria (documento preparatorio). El nombre comercial "
                 "'BREZO ANALÍTICA' aparece arriba sin NIF; la razón social, CIF y domicilio del emisor solo están "
                 "en el pie. El recuadro del cliente no tiene rótulo. Sin IBAN (pago por confirming).",
    }

    def render(ruta: Path):
        W, H = A4
        c = nuevo_canvas(ruta, A4, "Proforma PF-26/0093", "Brezo Analítica Consultores")
        x0 = 50
        tx(c, x0, H - 60, "BREZO ANALÍTICA", "Courier-Bold", 18, HexColor("#3F6212"))
        tx(c, x0, H - 74, "consultores · protección de datos y calidad", "Courier", 8.5, HexColor("#3F6212"))
        tx(c, W - 50, H - 60, "FACTURA PROFORMA", "Courier-Bold", 14, alin="r")
        tx(c, x0, H - 120, f"Número de proforma. - {d['numero']}", "Courier", 9.5)
        tx(c, x0, H - 134, f"Fecha. - {d['fecha_txt']}", "Courier", 9.5)
        tx(c, x0, H - 148, "Contrato: CT-2026-015", "Courier", 9.5)
        # recuadro cliente sin rótulo
        bx = 310
        c.setLineWidth(1)
        c.rect(bx, H - 178, W - 50 - bx, 76)
        y = H - 120
        for i, s in enumerate(["CLÍNICA DENTAL SONRISA NORTE, S.L.P.", "C/ Fuente del Berro Alto, 9",
                               "50012 ZARAGOZA", f"C.I.F. {CIF_CLINICA[0]}-{CIF_CLINICA[1:]}"]):
            tx(c, bx + 8, y, s, "Courier-Bold" if i == 0 else "Courier", 9)
            y -= 14
        # tabla
        yt = H - 230
        sep = "-" * 97
        tx(c, x0, yt + 10, sep, "Courier", 8.5)
        tx(c, x0, yt, "CONCEPTO", "Courier-Bold", 9)
        tx(c, 380, yt, "UDS.", "Courier-Bold", 9, alin="r")
        tx(c, 460, yt, "PRECIO", "Courier-Bold", 9, alin="r")
        tx(c, W - 50, yt, "IMPORTE", "Courier-Bold", 9, alin="r")
        tx(c, x0, yt - 10, sep, "Courier", 8.5)
        y = yt - 26
        for ln in lineas:
            tx(c, x0, y, ln["concepto"], "Courier", 9)
            tx(c, 380, y, cant(ln["cantidad"], 0), "Courier", 9, alin="r")
            tx(c, 460, y, num(ln["precio"]), "Courier", 9, alin="r")
            tx(c, W - 50, y, num(ln["importe"]), "Courier", 9, alin="r")
            y -= 14
        y -= 6
        tx(c, x0, y, sep, "Courier", 8.5)
        y -= 26
        for et, val in [("Base", num(t["base"])), ("Cuota IVA 21%", num(t["cuota"])),
                        ("Importe total", num(t["total"], euro=True))]:
            f = "Courier-Bold" if et == "Importe total" else "Courier"
            tx(c, 330, y, et, f, 10)
            tx(c, W - 50, y, val, f, 10, alin="r")
            y -= 16
        y -= 20
        tx(c, x0, y, "Forma de pago: confirming a 60 días.", "Courier", 9)
        parrafo(c, x0, y - 16, "Documento sin validez fiscal. La factura definitiva se emitirá una vez aceptada "
                "la presente proforma.", W - 100, "Courier-Oblique", 8.5, 11)
        # pie con los datos del emisor
        c.setLineWidth(0.5)
        c.line(x0, 92, W - 50, 92)
        pie = [f"Brezo Analítica Consultores, S.L. - CIF {CIF_BREZO}",
               "Gran Vía Imaginaria, 88, 5.ª planta - 50005 Zaragoza - Tel. 976 000 000",
               "Inscrita en el Registro Mercantil de Zaragoza, Tomo 0000, Folio 00, Hoja Z-00000"]
        y = 78
        for s in pie:
            tx(c, W / 2, y, s, "Courier", 7.5, alin="c")
            y -= 10
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 05 · Consultor autónomo -> factura RECIBIDA por la empresa usuaria (cliente), Letter,
#      cabecera de tabla en dos renglones, totales en frase, IRPF 15 %
# ==============================================================================================
def doc_consultor_recibida() -> dict:
    lineas = [
        {"fecha_srv": "06/10", "concepto": "Análisis de procesos de compras y entrevistas", "cantidad": D("14.50"),
         "precio": D("62.00")},
        {"fecha_srv": "15/10", "concepto": "Sesiones de formación a usuarios clave", "cantidad": D("8.00"),
         "precio": D("62.00")},
        {"fecha_srv": "27/10", "concepto": "Documentación final y reunión de cierre", "cantidad": D("3.25"),
         "precio": D("62.00")},
    ]
    t = calcular(lineas, pct_ret=15)
    d = {
        "archivo": "prof_05_consultor_recibida_frase_irpf15.pdf",
        "estilo": "Consultor autónomo, factura recibida por la empresa cliente: tamaño Letter, emisor arriba-"
                  "izquierda con DNI, bloque 'Destinatario:', tabla con cabecera en dos renglones (Fecha servicio, "
                  "Horas, Precio hora), totales redactados en una frase, importes sin € ni separador de miles, "
                  "fecha dd-mm-yyyy, IRPF 15 %",
        "empresa_nif": CIF_TALLERES,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "117/2026",
        "fecha": date(2026, 10, 31),
        "fecha_txt": "31-10-2026",
        "emisor": {"nombre": "Íñigo Valdés Carrasco", "nif": NIF_CONSULTOR, "domicilio_contiene": "41010"},
        "destinatario": {"nombre": "Talleres Metálicos Guadaíra Sur, S.L.", "nif": CIF_TALLERES,
                         "domicilio_contiene": "41500"},
        "lineas": lineas, "t": t,
        "referencias": {"proyecto": "PRJ-0926"},
        "iban": "ES1234560000111122223333",
        "forma_pago_contiene": "transferencia bancaria a 30 días",
        "indicios": [],
        "notas": "La empresa usuaria es el DESTINATARIO (factura de proveedor). Columna 'Fecha servicio' con fechas "
                 "parciales dd/mm como distractor de la fecha de factura. Totales solo en una frase: base 1596,50, "
                 "IVA 21 % 335,27 (redondeo de 335,265), total factura 1931,77, retención 239,48 (de 239,475), "
                 "líquido a pagar 1692,29. Tamaño de página Letter.",
    }

    def render(ruta: Path):
        W, H = letter
        tinta = HexColor("#1E3A5F")
        c = nuevo_canvas(ruta, letter, "Factura 117/2026", "Íñigo Valdés Carrasco")
        x0 = 54
        tx(c, x0, H - 56, "ÍÑIGO VALDÉS CARRASCO", "Helvetica-Bold", 13, tinta)
        y = H - 71
        for s in ["Consultoría de organización y procesos", "Avda. Montes de Oca Nuevos, 140, 2.º izq.",
                  "41010 Sevilla", f"NIF {NIF_CONSULTOR}", "ivaldes@consultor.example"]:
            tx(c, x0, y, s, "Helvetica", 9)
            y -= 12
        tx(c, W - 54, H - 58, "Factura", "Helvetica-Bold", 22, tinta, "r")
        tx(c, W - 54, H - 78, f"Nº Fra.: {d['numero']}", "Helvetica", 10, alin="r")
        tx(c, W - 54, H - 92, f"Fecha: {d['fecha_txt']}", "Helvetica", 10, alin="r")
        # destinatario
        tx(c, 330, H - 140, "Destinatario:", "Helvetica-Bold", 9, tinta)
        y = H - 155
        for i, s in enumerate([d["destinatario"]["nombre"], "Att. Dpto. de Administración",
                               "Pol. Ind. La Isleta Verde, nave 7", "41500 Alcalá de Guadaíra (Sevilla)",
                               f"N.I.F. {CIF_TALLERES}"]):
            tx(c, 330, y, s, "Helvetica-Bold" if i == 0 else "Helvetica", 9.5)
            y -= 13
        tx(c, x0, H - 155, "Proyecto: PRJ-0926", "Helvetica", 9)
        tx(c, x0, H - 168, "Mejora de procesos de compras", "Helvetica-Oblique", 9)
        # tabla con cabecera en dos renglones
        yt = H - 250
        c.setStrokeColor(tinta)
        c.setLineWidth(1.2)
        c.line(x0, yt + 22, W - 54, yt + 22)
        cab = [(x0, ["Fecha", "servicio"], "l"), (110, ["Descripción del servicio", ""], "l"),
               (400, ["Horas", ""], "r"), (472, ["Precio", "hora"], "r"), (W - 54, ["Importe", ""], "r")]
        for x, (a, b), al in cab:
            tx(c, x, yt + 10, a, "Helvetica-Bold", 9, tinta, al)
            if b:
                tx(c, x, yt, b, "Helvetica-Bold", 9, tinta, al)
        c.setLineWidth(0.6)
        c.line(x0, yt - 6, W - 54, yt - 6)
        y = yt - 22
        for ln in lineas:
            tx(c, x0, y, ln["fecha_srv"], "Helvetica", 9.5)
            tx(c, 110, y, ln["concepto"], "Helvetica", 9.5)
            tx(c, 400, y, cant(ln["cantidad"]), "Helvetica", 9.5, alin="r")
            tx(c, 472, y, num(ln["precio"], miles=False), "Helvetica", 9.5, alin="r")
            tx(c, W - 54, y, num(ln["importe"], miles=False), "Helvetica", 9.5, alin="r")
            y -= 18
        c.line(x0, y + 8, W - 54, y + 8)
        y -= 24
        frase = (f"La presente factura asciende a una base imponible de {num(t['base'], miles=False)} euros, más "
                 f"{num(t['cuota'], miles=False)} euros de IVA (21 %), lo que supone un total factura de "
                 f"{num(t['total'], miles=False)} euros. Deducida la retención a cuenta del IRPF (15 %) de "
                 f"{num(t['ret'], miles=False)} euros, el líquido a pagar es de {num(t['pagar'], miles=False)} euros.")
        y = parrafo(c, x0, y, frase, W - 108, "Helvetica", 10, 14)
        y -= 14
        parrafo(c, x0, y, "Forma de pago: transferencia bancaria a 30 días fecha factura a la cuenta "
                "IBAN ES12 3456 0000 1111 2222 3333.", W - 108, "Helvetica", 9.5, 13)
        tx(c, W / 2, 46, "Íñigo Valdés Carrasco · Consultor · NIF " + NIF_CONSULTOR, "Helvetica", 7.5,
           HexColor("#777777"), "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


DOCUMENTOS = [doc_abogada, doc_arquitecto, doc_disenadora, doc_consultora_sl, doc_consultor_recibida]


# ----------------------------------------------------------------------------------------------
# JSON esperado
# ----------------------------------------------------------------------------------------------
def esperado(d: dict) -> dict:
    t = d["t"]
    lineas = []
    for ln in d["lineas"]:
        concepto = ln["concepto"]
        # subcadena corta de la primera línea visual del concepto (evita depender del ajuste de texto)
        contiene = " ".join(concepto.split()[:4])
        lineas.append({"concepto_contiene": contiene, "cantidad": s2(ln.get("cantidad")),
                       "precio": s2(ln.get("precio")), "importe": s2(ln["importe"])})
    return {
        "archivo": d["archivo"],
        "estilo": d["estilo"],
        "empresa_nif": d["empresa_nif"],
        "tipo_documento": d["tipo_documento"],
        "situacion_esperada": d["situacion_esperada"],
        "numero": d["numero"],
        "fecha": d["fecha"].isoformat() if d["fecha"] else None,
        "emisor": dict(d["emisor"]),
        "destinatario": dict(d["destinatario"]),
        "lineas": lineas,
        "desglose": [{"tipo_iva": f"{t['pct_iva']:.2f}", "base": s2(t["base"]), "cuota": s2(t["cuota"])}],
        "base_imponible": s2(t["base"]),
        "cuota_iva": s2(t["cuota"]),
        "total_factura": s2(t["total"]),
        "retencion": ({"porcentaje": f"{t['pct_ret']:.2f}", "importe": s2(t["ret"])}
                      if t["ret"] is not None else None),
        "total_a_pagar": s2(t["pagar"]),
        "referencias": dict(d["referencias"]),
        "iban": d["iban"],
        "forma_pago_contiene": d["forma_pago_contiene"],
        "indicios_esperados": list(d["indicios"]),
        "notas": d["notas"],
    }


# ----------------------------------------------------------------------------------------------
# verificación con pdftotext -layout
# ----------------------------------------------------------------------------------------------
RE_IMPORTE = re.compile(r"(?<![\d.,])-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?![\d])")


def _importes_texto(texto: str) -> set[Decimal]:
    out = set()
    for m in RE_IMPORTE.finditer(texto):
        out.add(abs(D(m.group(0).replace(".", "").replace(",", "."))))
    return out


def _plano(s: str) -> str:
    return re.sub(r"[\s.\-]", "", s.upper())


def _esp(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verificar(ruta_pdf: Path, e: dict, fecha_txt: str) -> list[str]:
    if not shutil.which("pdftotext"):
        return ["pdftotext no disponible: verificación omitida"]
    texto = subprocess.run(["pdftotext", "-layout", str(ruta_pdf), "-"], capture_output=True, text=True,
                           check=True).stdout
    fallos = []
    esp, plano, importes = _esp(texto), _plano(texto), _importes_texto(texto)

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    debe(e["numero"] in texto, f"número {e['numero']}")
    debe(fecha_txt in texto, f"fecha {fecha_txt}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(es_valido(p["nif"]), f"NIF {parte} inválido {p['nif']}")
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        debe(_esp(p["nombre"]) in esp, f"nombre {parte} {p['nombre']}")
        debe(_esp(p["domicilio_contiene"]) in esp, f"domicilio {parte} {p['domicilio_contiene']}")
    debe(es_valido(e["empresa_nif"]), "empresa_nif inválido")
    debe(e["empresa_nif"] in (e["emisor"]["nif"], e["destinatario"]["nif"]), "empresa_nif no es emisor ni destinatario")
    for ln in e["lineas"]:
        debe(_esp(ln["concepto_contiene"]) in esp, f"concepto {ln['concepto_contiene']}")
        for k in ("precio", "importe"):
            if ln[k] is not None:
                debe(D(ln[k]) in importes, f"línea {k} {ln[k]}")
    for k in ("base_imponible", "cuota_iva", "total_factura", "total_a_pagar"):
        if e[k] is not None:
            debe(D(e[k]) in importes, f"{k} {e[k]}")
    if e["retencion"]:
        debe(D(e["retencion"]["importe"]) in importes, f"retención {e['retencion']['importe']}")
        pct = D(e["retencion"]["porcentaje"]).normalize()
        debe(re.search(rf"\b{pct}\s*%", texto) is not None, f"porcentaje retención {pct}")
    # coherencia aritmética del JSON
    base, cuota, total = D(e["base_imponible"]), D(e["cuota_iva"]), D(e["total_factura"])
    debe(sum(D(x["importe"]) for x in e["lineas"]) == base, "suma de líneas != base")
    debe(q2(base * D("0.21")) == cuota, "cuota != base x 21 %")
    debe(base + cuota == total, "base + cuota != total")
    if e["retencion"]:
        r = D(e["retencion"]["importe"])
        debe(q2(base * D(e["retencion"]["porcentaje"]) / 100) == r, "retención mal calculada")
        debe(total - r == D(e["total_a_pagar"]), "total - retención != total a pagar")
        debe(D(e["total_a_pagar"]) != total, "total a pagar igual a total factura")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    if e["forma_pago_contiene"]:
        debe(_esp(e["forma_pago_contiene"]) in esp, f"forma de pago {e['forma_pago_contiene']}")
    return fallos


def main(argv: list[str]) -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    verificar_ = "--sin-verificar" not in argv
    resumen, total_fallos = [], 0
    for fabrica in DOCUMENTOS:
        d = fabrica()
        ruta = SALIDA / d["archivo"]
        d["render"](ruta)
        e = esperado(d)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fallos = verificar(ruta, e, d["fecha_txt"]) if verificar_ else []
        total_fallos += len(fallos)
        resumen.append({"archivo": d["archivo"], "estilo": d["estilo"], "fallos": fallos})
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}" + (f"  -> {fallos}" if fallos else ""))
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
