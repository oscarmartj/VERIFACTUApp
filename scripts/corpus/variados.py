"""Corpus SINTÉTICO de facturas variadas (familia «miscelánea»).

Genera 6 PDF con maquetaciones muy distintas entre sí y, para cada uno, un JSON con los valores
esperados para medir el extractor de datos (app/extraccion):

  01 · Carta tipo procesador de textos: «Factura nº 2026-045 de fecha 3 de noviembre de 2026» dentro
       de un párrafo y totales como renglones de texto (carpintería → particular).
  02 · Minimalista en Times a dos columnas (barra lateral con los datos de la factura), fechas dd.mm.yyyy.
  03 · Factura RECIBIDA: un proveedor factura a la empresa usuaria (empresa_nif = destinatario).
  04 · Formación exenta «Exenta de IVA art. 20.Uno.9º LIVA», papel carta (Letter), desglose vacío.
  05 · Presupuesto en Courier tipo ERP antiguo (documento preparatorio).
  06 · Factura ESCANEADA: el PDF de texto se rasteriza a 200 ppp, se gira ligeramente, se le añade ruido
       y se guarda como PDF solo-imagen (sin capa de texto). Recibida por la empresa usuaria.

TODOS LOS DATOS SON FICTICIOS: razones sociales, personas, domicilios, teléfonos, correos (dominio
.example), registros mercantiles y matrículas inventados; IBAN claramente falsos (ES00… / ES12 3456…).
Los NIF/CIF tienen dígito o letra de control válidos (app.nif.completar_cif / letra del DNI) pero no se
corresponden intencionadamente con ninguna persona o entidad.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/variados.py              # genera y verifica (pdftotext; OCR informativo)
    python scripts/corpus/variados.py --sin-verificar

Requisitos: reportlab, Pillow, pypdf y las utilidades de poppler (pdftoppm para rasterizar el documento
escaneado; pdftotext para verificar). tesseract es opcional (solo informa de la legibilidad del escaneado).

Determinista: sin aleatoriedad salvo el ruido del escaneado (semilla fija); los PDF se generan en modo
«invariant» de reportlab (sin fechas ni identificadores variables).
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from PIL import Image, ImageChops, ImageDraw, ImageFilter  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4, letter  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import completar_cif, es_valido  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus" / "variados"
SEMILLA = 20261103
random.seed(SEMILLA)

D = Decimal
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
GRIS = HexColor("#555555")
GRIS_CLARO = HexColor("#E6E6E6")


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def dni(n: int) -> str:
    return f"{n:08d}{LETRAS_DNI[n % 23]}"


def q2(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(x) -> str | None:
    return None if x is None else str(q2(x))


def num(x, miles=True, dec=2, pre="", suf="") -> str:
    """Formato español: 1.234,56 (miles=True) o 1234,56 (miles=False)."""
    x = D(x)
    ent, frac = f"{abs(x):.{dec}f}".split(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", ".")
    return f"{'-' if x < 0 else ''}{pre}{ent},{frac}{suf}"


def cant(x, dec: int | None = None) -> str:
    """Cantidad tal y como se rotula: 3 / 3,5 / 2,25 (dec=None) o con decimales fijos."""
    x = D(x)
    if dec is not None:
        return f"{x:.{dec}f}".replace(".", ",")
    if x == x.to_integral_value():
        return str(int(x))
    return f"{x.normalize():f}".replace(".", ",")


def calcular(lineas: list[dict], pct_iva) -> dict:
    for ln in lineas:
        if ln.get("cantidad") is not None and ln.get("precio") is not None:
            bruto = D(ln["cantidad"]) * D(ln["precio"])
            ln["bruto"] = q2(bruto)
            ln["importe"] = q2(bruto * (D(100) - D(ln.get("dto") or 0)) / D(100))
        else:
            ln["importe"] = q2(ln["importe"])
            ln["bruto"] = ln["importe"]
    base = sum((ln["importe"] for ln in lineas), D("0"))
    cuota = q2(base * D(pct_iva) / D(100))
    return {"base": base, "cuota": cuota, "total": base + cuota, "pct_iva": D(pct_iva),
            "bruto": sum((ln["bruto"] for ln in lineas), D("0"))}


# ----------------------------------------------------------------------------------------------
# utilidades de dibujo
# ----------------------------------------------------------------------------------------------
def tx(c, x, y, s, fuente="Helvetica", tam=9, color=black, alin="l"):
    c.setFont(fuente, tam)
    c.setFillColor(color)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[alin](x, y, s)


def parrafo(c, x, y, s, ancho, fuente="Helvetica", tam=9, salto=None, color=black) -> float:
    for ln in simpleSplit(s, fuente, tam, ancho):
        tx(c, x, y, ln, fuente, tam, color)
        y -= salto or tam * 1.3
    return y


def nuevo_canvas(ruta: Path, tam_pagina, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=tam_pagina, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    return c


# ----------------------------------------------------------------------------------------------
# partes ficticias (NIF con control válido)
# ----------------------------------------------------------------------------------------------
CIF_CARPINTERIA = completar_cif("B", "2611234")      # La Rioja
NIF_PARTICULAR = dni(16612345)
CIF_ZARZALERA = completar_cif("B", "3145678")        # Navarra
CIF_MERIDIANO = completar_cif("B", "4687123")        # Valencia
CIF_CIERZO = completar_cif("A", "5023456")           # Zaragoza
CIF_ALCOTAN = completar_cif("B", "3779012")          # Salamanca
CIF_MIRANDILLA = completar_cif("B", "0945612")       # Burgos
CIF_BRANAVELLA = completar_cif("B", "2733445")       # Lugo
CIF_COMUNIDAD = completar_cif("H", "2766123")        # comunidad de propietarios, Lugo
CIF_CARDONCILLO = completar_cif("B", "2955667")      # Málaga
CIF_PINSAPAR = completar_cif("B", "2988110")         # Málaga

CARPINTERIA = {"nombre": "Carpintería Hermanos Valdeoliva, S.L.", "nif": CIF_CARPINTERIA,
               "domicilio_contiene": "26006"}


def guion(n: str, pos=1) -> str:
    return f"{n[:pos]}-{n[pos:]}"


# ==============================================================================================
# 01 · Carta tipo procesador de textos
# ==============================================================================================
def doc_carta() -> dict:
    lineas = [
        {"concepto": "Fabricación e instalación de armario empotrado de roble macizo, tres cuerpos con "
                     "puertas correderas y altillo", "contiene": "Fabricación e instalación de armario",
         "cantidad": D("1"), "precio": D("1480.00")},
        {"concepto": "Puertas de paso lacadas en blanco, incluido herraje y colocación",
         "contiene": "Puertas de paso lacadas", "cantidad": D("3"), "precio": D("215.50")},
        {"concepto": "Desplazamiento y retirada de residuos al punto limpio", "contiene": "Desplazamiento y retirada",
         "cantidad": None, "precio": None, "importe": D("85.00")},
    ]
    t = calcular(lineas, 21)
    numero, fecha, fecha_txt = "2026-045", date(2026, 11, 3), "3 de noviembre de 2026"
    frase = f"Factura nº {numero} de fecha {fecha_txt}"
    d = {
        "archivo": "var_01_carta_frase_totales_texto.pdf",
        "estilo": "Carta de procesador de textos (Helvetica, A4): membrete, lugar y fecha, destinatario particular sin "
                  "etiqueta, 'Factura nº 2026-045 de fecha 3 de noviembre de 2026' dentro de un párrafo, tabla Word "
                  "con rejilla y totales como renglones de texto en euros",
        "empresa_nif": CIF_CARPINTERIA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": numero, "fecha": fecha, "fecha_txt": fecha_txt,
        "emisor": dict(CARPINTERIA),
        "destinatario": {"nombre": "Ernesto Galarreta Ruiz", "nif": NIF_PARTICULAR, "domicilio_contiene": "26200"},
        "lineas": lineas, "t": t,
        "referencias": {},
        "iban": "ES1234567890123456789012",
        "forma_pago_contiene": "transferencia bancaria",
        "indicios": [],
        "literal": [frase, "IBAN ES12 3456 7890 1234 5678 9012", "Base imponible: 2.211,50 euros.", "Cuota de IVA al 21 %: 464,42 euros.",
                    "Importe total de la factura: 2.675,92 euros."],
        "impresos": ["16612345-C", "1.480,00 €", "215,50 €", "85,00 €"],
        "notas": "Documento de la propia empresa (usuaria) redactado como carta y sin marcas VERI*FACTU: se espera "
                 "tratarlo como documento preparatorio. Número y fecha solo dentro de la frase del primer párrafo (la "
                 "fecha también encabeza la carta: 'Logroño, 3 de noviembre de 2026'). Distractores: presupuesto "
                 "P-118/26 de 2 de octubre de 2026 citado en el texto (no es el número ni el tipo de documento) y "
                 "firmante persona física al pie. Destinatario particular con 'D.' delante y NIF con guion "
                 "(16612345-C). Tercera línea sin cantidad ni precio. Totales sin tabla, como frases terminadas en "
                 "'euros.'; cuota con redondeo half-up (464,415 -> 464,42).",
    }

    def render(ruta: Path):
        W, H = A4
        x0, xd = 72, W - 72
        c = nuevo_canvas(ruta, A4, f"Factura {numero}", "Carpintería Hermanos Valdeoliva")
        verde = HexColor("#3E5C3A")
        tx(c, x0, H - 62, "CARPINTERÍA HERMANOS VALDEOLIVA, S.L.", "Helvetica-Bold", 13, verde)
        tx(c, x0, H - 76, "Carpintería de madera a medida · Cocinas · Armarios · Puertas", "Helvetica-Oblique", 8.5, GRIS)
        tx(c, x0, H - 89, "Calle del Sauce Rojo, 17, bajo · 26006 Logroño (La Rioja)", "Helvetica", 8.5)
        tx(c, x0, H - 100, f"CIF {CIF_CARPINTERIA} · Tel. 941 000 000 · taller@valdeoliva.example", "Helvetica", 8.5)
        c.setStrokeColor(verde)
        c.setLineWidth(1.2)
        c.line(x0, H - 110, xd, H - 110)
        tx(c, xd, H - 142, f"Logroño, {fecha_txt}", "Helvetica", 10.5, alin="r")
        y = H - 180
        for s in ["D. Ernesto Galarreta Ruiz", "Avenida de la Vega Alta, 22, 4.º izda.", "26200 Haro (La Rioja)",
                  f"NIF {NIF_PARTICULAR[:8]}-{NIF_PARTICULAR[8]}"]:
            tx(c, x0, y, s, "Helvetica", 10.5)
            y -= 14
        tx(c, x0, H - 262, f"Asunto: Factura nº {numero}", "Helvetica-Bold", 10.5)
        tx(c, x0, H - 290, "Estimado Sr. Galarreta:", "Helvetica", 10.5)
        y = H - 312
        tx(c, x0, y, f"Adjunto le remitimos la {frase},", "Helvetica", 10.5)
        y = parrafo(c, x0, y - 15, "correspondiente a los trabajos de carpintería realizados en su vivienda conforme a "
                    "nuestro presupuesto P-118/26 de 2 de octubre de 2026, que usted tuvo a bien aceptar. El detalle de "
                    "los conceptos facturados es el siguiente:", xd - x0, "Helvetica", 10.5, 15)
        # tabla tipo Word con rejilla
        cols = [x0, 300, 365, 445, xd]
        y -= 6
        c.setStrokeColor(black)
        c.setLineWidth(0.5)
        c.setFillColor(GRIS_CLARO)
        c.rect(x0, y - 18, xd - x0, 18, stroke=1, fill=1)
        for i, cab in enumerate(["Concepto", "Cantidad", "Precio unitario", "Importe"]):
            if i == 0:
                tx(c, cols[0] + 5, y - 13, cab, "Helvetica-Bold", 9.5)
            else:
                tx(c, cols[i + 1] - 5, y - 13, cab, "Helvetica-Bold", 9.5, alin="r")
        y -= 18
        for ln in lineas:
            partes = simpleSplit(ln["concepto"], "Helvetica", 9.5, cols[1] - cols[0] - 10)
            alto = 8 + 12 * len(partes)
            c.rect(x0, y - alto, xd - x0, alto, stroke=1, fill=0)
            yy = y - 13
            for p in partes:
                tx(c, cols[0] + 5, yy, p, "Helvetica", 9.5)
                yy -= 12
            if ln["cantidad"] is not None:
                tx(c, cols[2] - 5, y - 13, cant(ln["cantidad"]), "Helvetica", 9.5, alin="r")
                tx(c, cols[3] - 5, y - 13, num(ln["precio"], suf=" €"), "Helvetica", 9.5, alin="r")
            tx(c, cols[4] - 5, y - 13, num(ln["importe"], suf=" €"), "Helvetica", 9.5, alin="r")
            y -= alto
        ytab = y
        for xc in cols[1:-1]:
            c.line(xc, ytab, xc, ytab + sum(8 + 12 * len(simpleSplit(ln["concepto"], "Helvetica", 9.5,
                                                                       cols[1] - cols[0] - 10)) for ln in lineas) + 18)
        # totales como renglones de texto
        y -= 26
        tx(c, x0, y, f"Base imponible: {num(t['base'])} euros.", "Helvetica", 10.5)
        tx(c, x0, y - 15, f"Cuota de IVA al 21 %: {num(t['cuota'])} euros.", "Helvetica", 10.5)
        tx(c, x0, y - 30, f"Importe total de la factura: {num(t['total'])} euros.", "Helvetica-Bold", 10.5)
        y -= 58
        for s in ["Le rogamos efectúe el pago mediante transferencia bancaria en el plazo de 30 días desde la",
                  "fecha de la factura a nuestra cuenta IBAN ES12 3456 7890 1234 5678 9012, indicando su",
                  "número en el concepto."]:
            tx(c, x0, y, s, "Helvetica", 10.5)
            y -= 15
        tx(c, x0, y - 12, "Sin otro particular, reciba un cordial saludo.", "Helvetica", 10.5)
        tx(c, x0, y - 40, "Atentamente,", "Helvetica", 10.5)
        tx(c, x0, y - 92, "Javier Valdeoliva Sáenz", "Helvetica", 10.5)
        tx(c, x0, y - 106, "Administrador", "Helvetica-Oblique", 9.5, GRIS)
        c.setStrokeColor(verde)
        c.setLineWidth(0.6)
        c.line(x0, 52, xd, 52)
        tx(c, W / 2, 40, f"Carpintería Hermanos Valdeoliva, S.L. · CIF {CIF_CARPINTERIA} · Inscrita en el Registro "
           "Mercantil de La Rioja, tomo 000, folio 00, hoja LO-00000", "Helvetica", 6.5, GRIS, "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 02 · Minimalista a dos columnas en Times
# ==============================================================================================
def doc_minimalista() -> dict:
    lineas = [
        {"concepto": "Traducción jurada inglés-español de escrituras de constitución y estatutos sociales "
                     "(4 documentos, recuento por palabra)", "contiene": "Traducción jurada inglés-español",
         "cantidad": D("12500"), "precio": D("0.09")},
        {"concepto": "Revisión terminológica y maquetación (horas)", "contiene": "Revisión terminológica",
         "cantidad": D("3.75"), "precio": D("38.00")},
        {"concepto": "Legalización y envío por mensajería certificada", "contiene": "Legalización y envío",
         "cantidad": D("1"), "precio": D("46.00")},
    ]
    t = calcular(lineas, 21)
    numero, fecha, fecha_txt = "MA-0187/26", date(2026, 10, 27), "27.10.2026"
    d = {
        "archivo": "var_02_minimalista_times_dos_columnas.pdf",
        "estilo": "Minimalista en Times a dos columnas: barra lateral con etiquetas en cursiva y valor debajo "
                  "(Factura núm., Fecha dd.mm.yyyy, Vencimiento, Su pedido, IBAN), columna principal con 'Facturar a', "
                  "tabla Descripción/Cant./Precio/Importe sin € y totales Subtotal/IVA/Total sin separador de miles",
        "empresa_nif": CIF_MERIDIANO,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": numero, "fecha": fecha, "fecha_txt": fecha_txt,
        "emisor": {"nombre": "Traducciones Meridiano Austral, S.L.", "nif": CIF_MERIDIANO, "domicilio_contiene": "46004"},
        "destinatario": {"nombre": "Ingeniería Hidráulica Cierzo Bajo, S.A.", "nif": CIF_CIERZO,
                         "domicilio_contiene": "50015"},
        "lineas": lineas, "t": t,
        "referencias": {"pedido": "PED-HID-4471"},
        "iban": "ES0022223333444455556666",
        "forma_pago_contiene": "Transferencia a 30 días",
        "indicios": [],
        "literal": ["Factura núm.", "1125,00", "1313,50", "275,84", "1589,34 €"],
        "impresos": ["12500", "3,75", "0,09", "38,00", "46,00", "26.11.2026", "B-46.871.232"],
        "notas": "Documento propio sin marcas VERI*FACTU (preparatorio). Barra lateral izquierda y columna principal "
                 "comparten renglones en la extracción por líneas (texto intercalado). Etiquetas en una línea y valor "
                 "en la siguiente. Distractor: vencimiento 26.11.2026. NIF del emisor con guion y puntos "
                 "(B-46.871.232). IBAN partido en dos renglones por la estrechez de la barra lateral "
                 "('ES00 2222 3333 4444' / '5555 6666'). Cantidad 12500 palabras a 0,09 sin separador de miles; importes sin separador de "
                 "miles y sin € salvo el total. Cuota 275,835 -> 275,84 (half-up).",
    }

    def render(ruta: Path):
        W, H = A4
        c = nuevo_canvas(ruta, A4, f"Factura {numero}", "Traducciones Meridiano Austral")
        tinta = HexColor("#222222")
        tx(c, 50, H - 70, "Traducciones Meridiano Austral, S.L.", "Times-Bold", 17, tinta)
        tx(c, W - 50, H - 70, "Factura", "Times-Italic", 26, tinta, "r")
        c.setStrokeColor(HexColor("#999999"))
        c.setLineWidth(0.4)
        c.line(50, H - 86, W - 50, H - 86)
        c.line(200, H - 100, 200, 120)

        def bloque(y, etiqueta, valores, tam=10.5):
            tx(c, 50, y, etiqueta, "Times-Italic", 8.5, GRIS)
            y -= 13
            for v in valores:
                tx(c, 50, y, v, "Times-Roman", tam, tinta)
                y -= 12.5
            return y - 9

        y = H - 112
        y = bloque(y, "Emisor", ["Calle Pintor Azahar Gris, 9, 2.º", "46004 Valencia",
                                 f"N.I.F. {CIF_MERIDIANO[0]}-{CIF_MERIDIANO[1:3]}.{CIF_MERIDIANO[3:6]}.{CIF_MERIDIANO[6:]}",
                                 "Tel. 960 000 000", "hola@meridiano.example"], 9.5)
        y = bloque(y, "Factura núm.", [numero])
        y = bloque(y, "Fecha", [fecha_txt])
        y = bloque(y, "Vencimiento", ["26.11.2026"])
        y = bloque(y, "Su pedido", ["PED-HID-4471"])
        y = bloque(y, "Forma de pago", ["Transferencia a 30 días"])
        bloque(y, "IBAN", ["ES00 2222 3333 4444", "5555 6666"])
        # columna principal
        xm, xd = 218, W - 50
        tx(c, xm, H - 112, "Facturar a", "Times-Italic", 8.5, GRIS)
        yy = H - 127
        for s, f in [("Ingeniería Hidráulica Cierzo Bajo, S.A.", "Times-Bold"),
                     ("Paseo de los Almendros Rojos, 120", "Times-Roman"), ("50015 Zaragoza", "Times-Roman"),
                     (f"NIF {CIF_CIERZO}", "Times-Roman"), ("A la atención de: Departamento de Compras", "Times-Italic")]:
            tx(c, xm, yy, s, f, 10.5, tinta)
            yy -= 13
        yt = H - 230
        tx(c, xm, yt, "Descripción", "Times-Italic", 9, GRIS)
        tx(c, 425, yt, "Cant.", "Times-Italic", 9, GRIS, "r")
        tx(c, 478, yt, "Precio", "Times-Italic", 9, GRIS, "r")
        tx(c, xd, yt, "Importe", "Times-Italic", 9, GRIS, "r")
        c.line(xm, yt - 6, xd, yt - 6)
        y = yt - 22
        for ln in lineas:
            partes = simpleSplit(ln["concepto"], "Times-Roman", 10.5, 165)
            tx(c, 425, y, cant(ln["cantidad"]), "Times-Roman", 10.5, tinta, "r")
            tx(c, 478, y, num(ln["precio"], miles=False), "Times-Roman", 10.5, tinta, "r")
            tx(c, xd, y, num(ln["importe"], miles=False), "Times-Roman", 10.5, tinta, "r")
            for p in partes:
                tx(c, xm, y, p, "Times-Roman", 10.5, tinta)
                y -= 13
            y -= 9
        c.line(xm, y + 6, xd, y + 6)
        y -= 14
        for et, val, f, tam in [("Subtotal", num(t["base"], miles=False), "Times-Roman", 10.5),
                                ("IVA 21 %", num(t["cuota"], miles=False), "Times-Roman", 10.5)]:
            tx(c, 400, y, et, f, tam, tinta)
            tx(c, xd, y, val, f, tam, tinta, "r")
            y -= 15
        c.line(400, y + 8, xd, y + 8)
        y -= 6
        tx(c, 400, y, "Total", "Times-Bold", 13, tinta)
        tx(c, xd, y, num(t["total"], miles=False, suf=" €"), "Times-Bold", 13, tinta, "r")
        tx(c, xm, 150, "Gracias por su confianza.", "Times-Italic", 10, GRIS)
        tx(c, W / 2, 60, "Traducciones Meridiano Austral, S.L. – Registro Mercantil de Valencia, tomo 0000, hoja "
           "V-000000", "Times-Roman", 7.5, GRIS, "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 03 · Factura RECIBIDA de proveedor (la sube la empresa destinataria)
# ==============================================================================================
def doc_recibida() -> dict:
    lineas = [
        {"codigo": "BIS-3540", "concepto": "Bisagra cazoleta 35 mm acodada con freno", "contiene": "Bisagra cazoleta 35 mm",
         "cantidad": D("200"), "precio": D("0.85"), "dto": D("10"), "alb": 0},
        {"codigo": "COR-450", "concepto": "Guía de cajón extracción total 450 mm (par)", "contiene": "Guía de cajón extracción",
         "cantidad": D("24"), "precio": D("11.40"), "dto": D("10"), "alb": 0},
        {"codigo": "TIR-128", "concepto": "Tirador aluminio anodizado 128 mm", "contiene": "Tirador aluminio anodizado",
         "cantidad": D("60"), "precio": D("2.35"), "dto": D("0"), "alb": 0},
        {"codigo": "TOR-4X40", "concepto": "Tornillo rosca madera 4x40 (caja 500 uds.)", "contiene": "Tornillo rosca madera",
         "cantidad": D("6"), "precio": D("9.95"), "dto": D("5"), "alb": 1},
        {"codigo": "COL-PVA5", "concepto": "Cola vinílica D3 profesional, garrafa 5 kg", "contiene": "Cola vinílica D3",
         "cantidad": D("3"), "precio": D("18.60"), "dto": D("0"), "alb": 1},
    ]
    t = calcular(lineas, 21)
    numero, fecha, fecha_txt = "A-26/03418", date(2026, 10, 30), "30/10/2026"
    d = {
        "archivo": "var_03_recibida_proveedor_dto.pdf",
        "estilo": "Factura RECIBIDA de proveedor tipo ERP (Helvetica): logotipo en caja, emisor arriba con C.I.F. con "
                  "guion, caja 'DATOS DEL CLIENTE' y 'DIRECCIÓN DE ENTREGA', rejilla de cabecera con valores en la fila "
                  "de abajo, líneas agrupadas por albarán con Código y Dto. %, resumen en fila de cabecera",
        "empresa_nif": CIF_CARPINTERIA,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": numero, "fecha": fecha, "fecha_txt": fecha_txt,
        "emisor": {"nombre": "Herrajes y Suministros Zarzalera, S.L.", "nif": CIF_ZARZALERA, "domicilio_contiene": "31500"},
        "destinatario": dict(CARPINTERIA),
        "lineas": lineas, "t": t,
        "referencias": {"pedido": "CHV-0915"},
        "iban": "ES1234560000111122223333",
        "forma_pago_contiene": "Transferencia 30 días",
        "indicios": ["descuento"],
        "literal": ["Albarán AL-26/1187 de 14/10/2026", "Albarán AL-26/1243 de 22/10/2026"],
        "impresos": ["0,850", "11,400", "2,350", "9,950", "18,600", "700,10", "47,34", "29/11/2026", "004127",
                     guion(CIF_ZARZALERA), "26140"],
        "notas": "La sube la empresa DESTINATARIA (Carpintería Hermanos Valdeoliva, la misma usuaria del documento 01): "
                 "factura de proveedor, situación 'recibida'. Precios unitarios con 3 decimales (0,850) y descuento "
                 "por línea: el importe de línea ya lo descuenta (200 x 0,85 -10 % = 153,00; 6 x 9,95 -5 % = 56,715 "
                 "-> 56,72). Distractores: dirección de entrega distinta (26140) junto a la fiscal (26006), fechas de "
                 "albarán (14/10/2026, 22/10/2026) y vencimiento 29/11/2026, importe bruto 700,10 y descuentos 47,34 "
                 "en el resumen antes de la base. Cuota 137,0796 -> 137,08.",
    }

    def render(ruta: Path):
        W, H = A4
        x0, xd = 40, W - 40
        oscuro = HexColor("#24543B")
        c = nuevo_canvas(ruta, A4, f"Factura {numero}", "Herrajes y Suministros Zarzalera")
        c.setFillColor(oscuro)
        c.rect(x0, H - 92, 150, 52, stroke=0, fill=1)
        tx(c, x0 + 75, H - 66, "ZARZALERA", "Helvetica-Bold", 19, white, "c")
        tx(c, x0 + 75, H - 81, "HERRAJES Y SUMINISTROS", "Helvetica", 7, white, "c")
        y = H - 108
        for s, f in [("Herrajes y Suministros Zarzalera, S.L.", "Helvetica-Bold"),
                     ("Polígono Industrial La Serna Baja, nave 14", "Helvetica"), ("31500 Tudela (Navarra)", "Helvetica"),
                     (f"C.I.F.: {guion(CIF_ZARZALERA)}", "Helvetica"),
                     ("Tel. 948 000 000 · pedidos@zarzalera.example", "Helvetica")]:
            tx(c, x0, y, s, f, 8.5)
            y -= 11
        tx(c, xd, H - 62, "FACTURA", "Helvetica-Bold", 22, oscuro, "r")
        # caja cliente
        bx, by, bw, bh = 320, H - 190, xd - 320, 92
        c.setStrokeColor(oscuro)
        c.setLineWidth(0.8)
        c.rect(bx, by, bw, bh)
        c.setFillColor(oscuro)
        c.rect(bx, by + bh - 14, bw, 14, stroke=0, fill=1)
        tx(c, bx + 6, by + bh - 10.5, "DATOS DEL CLIENTE", "Helvetica-Bold", 7.5, white)
        yy = by + bh - 30
        for s, f in [("Carpintería Hermanos Valdeoliva, S.L.", "Helvetica-Bold"),
                     ("Calle del Sauce Rojo, 17, bajo", "Helvetica"), ("26006 Logroño (La Rioja)", "Helvetica"),
                     (f"CIF: {CIF_CARPINTERIA}", "Helvetica")]:
            tx(c, bx + 8, yy, s, f, 9)
            yy -= 13
        tx(c, x0, H - 178, "DIRECCIÓN DE ENTREGA", "Helvetica-Bold", 7.5, oscuro)
        tx(c, x0, H - 190, "Nave 3, Camino de las Huertas Bajas, s/n", "Helvetica", 8.5)
        tx(c, x0, H - 201, "26140 Lardero (La Rioja)", "Helvetica", 8.5)
        # rejilla de cabecera: etiquetas arriba, valores abajo
        celdas = [("Nº FACTURA", numero, 85), ("FECHA", fecha_txt, 70), ("CÓD. CLIENTE", "004127", 70),
                  ("SU PEDIDO", "CHV-0915", 75), ("FORMA DE PAGO", "Transferencia 30 días", 125),
                  ("VENCIMIENTO", "29/11/2026", xd - x0 - 425)]
        yg = H - 228
        x = x0
        for et, val, ancho in celdas:
            c.setFillColor(GRIS_CLARO)
            c.rect(x, yg - 14, ancho, 14, stroke=1, fill=1)
            c.rect(x, yg - 32, ancho, 18, stroke=1, fill=0)
            tx(c, x + ancho / 2, yg - 10, et, "Helvetica-Bold", 7, alin="c")
            tx(c, x + ancho / 2, yg - 26, val, "Helvetica", 8.5, alin="c")
            x += ancho
        # tabla de líneas
        cols = [x0, 105, 330, 375, 432, 472, xd]
        cabs = ["CÓDIGO", "DESCRIPCIÓN", "CANT.", "PRECIO", "DTO. %", "IMPORTE"]
        yt = H - 290
        c.setFillColor(oscuro)
        c.rect(x0, yt - 4, xd - x0, 15, stroke=0, fill=1)
        for i, cab in enumerate(cabs):
            if i < 2:
                tx(c, cols[i] + 4, yt, cab, "Helvetica-Bold", 7.5, white)
            else:
                tx(c, cols[i + 1] - 4, yt, cab, "Helvetica-Bold", 7.5, white, "r")
        y = yt - 20
        albaranes = ["Albarán AL-26/1187 de 14/10/2026", "Albarán AL-26/1243 de 22/10/2026"]
        actual = None
        for ln in lineas:
            if ln["alb"] != actual:
                actual = ln["alb"]
                tx(c, cols[1] + 4, y, albaranes[actual], "Helvetica-Oblique", 8, GRIS)
                y -= 15
            tx(c, cols[0] + 4, y, ln["codigo"], "Helvetica", 8.5)
            tx(c, cols[1] + 4, y, ln["concepto"], "Helvetica", 8.5)
            tx(c, cols[3] - 4, y, cant(ln["cantidad"]), "Helvetica", 8.5, alin="r")
            tx(c, cols[4] - 4, y, num(ln["precio"], dec=3), "Helvetica", 8.5, alin="r")
            tx(c, cols[5] - 4, y, num(ln["dto"]) if ln["dto"] else "", "Helvetica", 8.5, alin="r")
            tx(c, cols[6] - 4, y, num(ln["importe"]), "Helvetica", 8.5, alin="r")
            y -= 15
        c.setStrokeColor(oscuro)
        c.line(x0, y + 4, xd, y + 4)
        # resumen: fila de cabecera y valores debajo
        ys = 200
        resumen = [("IMPORTE BRUTO", num(t["bruto"])), ("DESCUENTOS", num(t["bruto"] - t["base"])),
                   ("BASE IMPONIBLE", num(t["base"])), ("% IVA", num(t["pct_iva"])),
                   ("CUOTA IVA", num(t["cuota"])), ("TOTAL FACTURA", num(t["total"], suf=" €"))]
        ancho = (xd - x0) / len(resumen)
        for i, (et, val) in enumerate(resumen):
            x = x0 + i * ancho
            c.setFillColor(GRIS_CLARO if i < 5 else oscuro)
            c.rect(x, ys, ancho, 14, stroke=1, fill=1)
            c.rect(x, ys - 20, ancho, 20, stroke=1, fill=0)
            tx(c, x + ancho / 2, ys + 4, et, "Helvetica-Bold", 7, black if i < 5 else white, "c")
            tx(c, x + ancho / 2, ys - 14, val, "Helvetica-Bold" if i == 5 else "Helvetica", 9.5, alin="c")
        tx(c, x0, 140, "Datos bancarios para transferencia: ES12 3456 0000 1111 2222 3333", "Helvetica", 8.5)
        tx(c, x0, 128, f"Rogamos indiquen el número de factura {numero} en el concepto de la transferencia.",
           "Helvetica", 8.5)
        parrafo(c, x0, 70, "Herrajes y Suministros Zarzalera, S.L. Inscrita en el Registro Mercantil de Navarra, tomo "
                "0000, folio 00, hoja NA-00000. Sus datos se tratan para la gestión de la relación comercial; puede "
                "ejercer sus derechos en el domicilio social indicado.", xd - x0, "Helvetica", 6.5, 8, GRIS)
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 04 · Formación exenta (art. 20.Uno.9º LIVA), papel carta
# ==============================================================================================
def doc_exenta() -> dict:
    lineas = [
        {"codigo": "PRL-B60", "concepto": "Prevención de Riesgos Laborales, nivel básico (60 horas)",
         "detalle": "Modalidad mixta · del 01/09/2026 al 30/09/2026", "contiene": "Prevención de Riesgos Laborales",
         "cantidad": D("8"), "precio": D("145.00")},
        {"codigo": "CAR-08", "concepto": "Operador de carretillas elevadoras (8 horas)",
         "detalle": "Presencial · 05/10/2026 · instalaciones del cliente", "contiene": "Operador de carretillas elevadoras",
         "cantidad": D("5"), "precio": D("95.00")},
    ]
    t = calcular(lineas, 0)
    numero, fecha, fecha_txt = "F-2026/0233", date(2026, 10, 15), "15-10-2026"
    leyenda = "Exenta de IVA art. 20.Uno.9º LIVA"
    d = {
        "archivo": "var_04_formacion_exenta_letter.pdf",
        "estilo": "Centro de formación, papel carta (Letter) en Helvetica: banda de color con razón social, datos con "
                  "'Núm. factura'/'Fecha de emisión' dd-mm-yyyy, caja 'CLIENTE', tabla Código/Acción formativa/Alumnos/"
                  "Precio/IVA con 'Exento', conceptos en dos renglones, totales con ':' y leyenda de exención",
        "empresa_nif": CIF_ALCOTAN,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": numero, "fecha": fecha, "fecha_txt": fecha_txt,
        "emisor": {"nombre": "Centro de Formación Alcotán Norte, S.L.", "nif": CIF_ALCOTAN, "domicilio_contiene": "37008"},
        "destinatario": {"nombre": "Frigoríficos Logísticos Mirandilla, S.L.", "nif": CIF_MIRANDILLA,
                         "domicilio_contiene": "09200"},
        "lineas": lineas, "t": t, "exenta": True,
        "referencias": {"pedido": "PO-55120", "expediente": "EXP-FOR-26/0712"},
        "iban": "ES0011112222333344445555",
        "forma_pago_contiene": "Transferencia a la vista",
        "indicios": ["exencion"],
        "literal": [leyenda, "Base imponible: 1.635,00 €", "Cuota IVA: 0,00 €", "Importe total: 1.635,00 €",
                    "Forma de pago - Transferencia a la vista"],
        "impresos": ["ES00-1111-2222-3333-4444-5555", "145,00 €", "95,00 €", "Exento"],
        "notas": "Factura propia de un centro de formación autorizado (preparatorio) con operación EXENTA: leyenda "
                 "'Exenta de IVA art. 20.Uno.9º LIVA'; la columna IVA dice 'Exento' y la cuota es 0,00 (desglose vacío, "
                 "cuota_iva 0.00, total = base). Debe aparecer el indicio de exención. Página US Letter. Distractores: "
                 "fechas de impartición del curso en el segundo renglón de cada concepto, IBAN con guiones "
                 "(ES00-1111-...) y separador ' - ' en la forma de pago.",
    }

    def render(ruta: Path):
        W, H = letter
        x0, xd = 46, W - 46
        azul = HexColor("#1F5F7A")
        c = nuevo_canvas(ruta, letter, f"Factura {numero}", "Centro de Formación Alcotán Norte")
        c.setFillColor(azul)
        c.rect(0, H - 78, W, 78, stroke=0, fill=1)
        tx(c, x0, H - 40, "CENTRO DE FORMACIÓN ALCOTÁN NORTE, S.L.", "Helvetica-Bold", 15, white)
        tx(c, x0, H - 56, "Centro autorizado de formación profesional para el empleo", "Helvetica", 8.5, white)
        tx(c, xd, H - 46, "FACTURA", "Helvetica-Bold", 20, white, "r")
        y = H - 100
        for s in ["Ronda de los Vencejos, 31", "37008 Salamanca", f"NIF: {CIF_ALCOTAN}",
                  "formacion@alcotan.example · 923 000 000"]:
            tx(c, x0, y, s, "Helvetica", 9)
            y -= 12
        y = H - 100
        for et, val in [("Núm. factura:", numero), ("Fecha de emisión:", fecha_txt), ("Su pedido nº:", "PO-55120"),
                        ("Expediente:", "EXP-FOR-26/0712")]:
            tx(c, 380, y, et, "Helvetica-Bold", 9)
            tx(c, xd, y, val, "Helvetica", 9, alin="r")
            y -= 13
        # cliente
        tx(c, x0, H - 168, "CLIENTE", "Helvetica-Bold", 9, azul)
        c.setStrokeColor(azul)
        c.setLineWidth(0.7)
        c.rect(x0, H - 236, 290, 60)
        yy = H - 192
        for s, f in [("Frigoríficos Logísticos Mirandilla, S.L.", "Helvetica-Bold"),
                     ("Carretera de las Eras Viejas, km 3,2", "Helvetica"), ("09200 Miranda de Ebro (Burgos)", "Helvetica"),
                     (f"CIF: {CIF_MIRANDILLA}", "Helvetica")]:
            tx(c, x0 + 8, yy, s, f, 9)
            yy -= 12
        # tabla
        cols = [x0, 102, 330, 380, 455, 505, xd]
        cabs = ["Código", "Acción formativa", "Alumnos", "Precio/alumno", "IVA", "Importe"]
        yt = H - 270
        c.setFillColor(HexColor("#DCEBF1"))
        c.rect(x0, yt - 5, xd - x0, 17, stroke=0, fill=1)
        for i, cab in enumerate(cabs):
            if i < 2:
                tx(c, cols[i] + 4, yt, cab, "Helvetica-Bold", 8.5, azul)
            else:
                tx(c, cols[i + 1] - 4, yt, cab, "Helvetica-Bold", 8.5, azul, "r")
        y = yt - 22
        for ln in lineas:
            tx(c, cols[0] + 4, y, ln["codigo"], "Helvetica", 9)
            tx(c, cols[1] + 4, y, ln["concepto"], "Helvetica", 9)
            tx(c, cols[1] + 4, y - 11, ln["detalle"], "Helvetica-Oblique", 7.5, GRIS)
            tx(c, cols[3] - 4, y, cant(ln["cantidad"]), "Helvetica", 9, alin="r")
            tx(c, cols[4] - 4, y, num(ln["precio"], suf=" €"), "Helvetica", 9, alin="r")
            tx(c, cols[5] - 4, y, "Exento", "Helvetica", 9, alin="r")
            tx(c, cols[6] - 4, y, num(ln["importe"], suf=" €"), "Helvetica", 9, alin="r")
            c.setStrokeColor(GRIS_CLARO)
            c.line(x0, y - 18, xd, y - 18)
            y -= 32
        # totales apilados con ":"
        y -= 10
        for et, val, f in [("Base imponible:", num(t["base"], suf=" €"), "Helvetica"),
                           ("Cuota IVA:", num(t["cuota"], suf=" €"), "Helvetica"),
                           ("Importe total:", num(t["total"], suf=" €"), "Helvetica-Bold")]:
            tx(c, 380, y, et, f, 10)
            tx(c, xd, y, val, f, 10, alin="r")
            y -= 16
        # leyenda de exención
        c.setStrokeColor(azul)
        c.setFillColor(HexColor("#F2F7F9"))
        c.rect(x0, y - 44, xd - x0, 36, stroke=1, fill=1)
        tx(c, x0 + 10, y - 22, leyenda, "Helvetica-Bold", 9.5, azul)
        tx(c, x0 + 10, y - 35, "Formación profesional impartida por centro autorizado (Ley 37/1992, artículo 20, "
           "apartado Uno, número 9º).", "Helvetica", 8)
        y -= 72
        tx(c, x0, y, "Forma de pago - Transferencia a la vista", "Helvetica", 9)
        tx(c, x0, y - 13, "IBAN - ES00-1111-2222-3333-4444-5555", "Helvetica", 9)
        tx(c, W / 2, 40, f"Centro de Formación Alcotán Norte, S.L. · NIF {CIF_ALCOTAN} · Registro Mercantil de "
           "Salamanca, hoja SA-00000", "Helvetica", 6.5, GRIS, "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 05 · Presupuesto en Courier (ERP de impresión matricial)
# ==============================================================================================
def doc_presupuesto() -> dict:
    lineas = [
        {"concepto": "CALDERA DE CONDENSACIÓN A GAS NATURAL 120 KW\nMODELO CN-120, CON CENTRALITA DE REGULACIÓN",
         "contiene": "CALDERA DE CONDENSACIÓN A GAS NATURAL", "cantidad": D("1"), "precio": D("4850.00")},
        {"concepto": "KIT DE EVACUACIÓN DE HUMOS Y ADAPTACIÓN DE\nCHIMENEA EXISTENTE (ACERO INOX. DOBLE PARED)",
         "contiene": "KIT DE EVACUACIÓN DE HUMOS", "cantidad": D("1"), "precio": D("690.00")},
        {"concepto": "MANO DE OBRA OFICIAL INSTALADOR (HORAS)", "contiene": "MANO DE OBRA OFICIAL INSTALADOR",
         "cantidad": D("14.5"), "precio": D("36.00")},
        {"concepto": "PUESTA EN MARCHA, LEGALIZACIÓN Y CERTIFICADO\nDE INSTALACIÓN TÉRMICA",
         "contiene": "PUESTA EN MARCHA, LEGALIZACIÓN", "cantidad": D("1"), "precio": D("358.00")},
    ]
    t = calcular(lineas, 21)
    numero, fecha, fecha_txt = "P-2026-0091", date(2026, 11, 6), "06/11/26"
    d = {
        "archivo": "var_05_presupuesto_courier.pdf",
        "estilo": "PRESUPUESTO en Courier (ERP de impresión matricial): renglones de ancho fijo, datos 'ETIQUETA : valor', "
                  "cliente en ventana sin etiqueta, tabla CANT./DESCRIPCIÓN/P.UNIT./IMPORTE con cantidad delante y "
                  "conceptos en dos renglones, totales con I.V.A. y sufijo EUR, fecha dd/mm/yy",
        "empresa_nif": CIF_BRANAVELLA,
        "tipo_documento": "presupuesto",
        "situacion_esperada": "preparatorio",
        "numero": numero, "fecha": fecha, "fecha_txt": fecha_txt,
        "emisor": {"nombre": "Instalaciones Térmicas Brañavella, S.L.", "nif": CIF_BRANAVELLA, "domicilio_contiene": "27003"},
        "destinatario": {"nombre": "Comunidad de Propietarios Olmo Blanco 14", "nif": CIF_COMUNIDAD,
                         "domicilio_contiene": "27004"},
        "lineas": lineas, "t": t,
        "referencias": {"obra": "SUSTITUCIÓN DE CALDERA COMUNITARIA"},
        "iban": None,
        "forma_pago_contiene": "40 % a la aceptación",
        "indicios": [],
        "literal": ["PRESUPUESTO Nº : P-2026-0091", "TOTAL PRESUPUESTO :", "7.768,20 EUR", "I.V.A. 21%"],
        "impresos": ["14,50", "4.850,00", "06/12/26", "000318"],
        "notas": "Presupuesto propio (documento preparatorio, tipo 'presupuesto'): no es una factura y no lleva IBAN. "
                 "Todo en Courier con columnas de ancho fijo y mayúsculas. Cliente comunidad de propietarios (CIF H) "
                 "en ventana sin etiqueta. Distractores: 'VÁLIDO HASTA : 06/12/26', código de cliente 000318 y la "
                 "advertencia 'ESTE DOCUMENTO NO TIENE VALIDEZ COMO FACTURA'. El total se rotula 'TOTAL PRESUPUESTO'.",
    }

    def render(ruta: Path):
        W, H = A4
        x0 = 48
        f, tam = "Courier", 9
        c = nuevo_canvas(ruta, A4, f"Presupuesto {numero}", "Instalaciones Térmicas Brañavella")

        def ln_(y, s, fuente=f, t_=tam):
            tx(c, x0, y, s, fuente, t_)
            return y - 11.5

        y = H - 56
        y = ln_(y, "INSTALACIONES TÉRMICAS BRAÑAVELLA, S.L.", "Courier-Bold", 11)
        y = ln_(y, "Rúa do Muíño Vello, 8, baixo - 27003 LUGO")
        y = ln_(y, f"C.I.F. {CIF_BRANAVELLA} - Tel. 982 000 000 - oficina@branavella.example")
        y = ln_(y, "=" * 82)
        tx(c, W / 2, y - 8, "PRESUPUESTO", "Courier-Bold", 15, alin="c")
        # ventana del cliente (sin etiqueta)
        c.setLineWidth(0.6)
        c.rect(318, H - 212, 230, 74)
        yy = H - 156
        for s in ["COMUNIDAD DE PROPIETARIOS OLMO BLANCO 14", "CALLE OLMO BLANCO, 14", "27004 LUGO",
                  f"CIF {CIF_COMUNIDAD}"]:
            tx(c, 326, yy, s, f, 8.6)
            yy -= 12
        y = H - 156
        for s in [f"PRESUPUESTO Nº : {numero}", f"FECHA          : {fecha_txt}", "VÁLIDO HASTA   : 06/12/26",
                  "CÓD. CLIENTE   : 000318", "PÁGINA         : 1 DE 1"]:
            y = ln_(y, s)
        y = H - 240
        y = ln_(y, "OBRA           : SUSTITUCIÓN DE CALDERA COMUNITARIA")
        y -= 6
        sep = "-" * 82
        y = ln_(y, sep)
        y = ln_(y, f"{'CANT.':>8} {'DESCRIPCIÓN':<46}{'P.UNIT.':>13}{'IMPORTE':>14}", "Courier-Bold")
        y = ln_(y, sep)
        for ln in lineas:
            partes = ln["concepto"].split("\n")
            y = ln_(y, f"{cant(ln['cantidad'], 2):>8} {partes[0]:<46}{num(ln['precio']):>13}{num(ln['importe']):>14}")
            for p in partes[1:]:
                y = ln_(y, f"{'':>8} {p}")
        y = ln_(y, sep)
        y -= 4
        for et, val, suf in [("BASE IMPONIBLE :", num(t["base"]), ""), ("I.V.A. 21%     :", num(t["cuota"]), ""),
                             ("TOTAL PRESUPUESTO :", num(t["total"]), " EUR")]:
            y = ln_(y, f"{'':>38}{et:<20}{val:>16}{suf}", "Courier-Bold" if "TOTAL" in et else f)
        y -= 18
        for s in ["CONDICIONES:",
                  "- FORMA DE PAGO: 40 % A LA ACEPTACIÓN Y 60 % A LA FINALIZACIÓN DE LOS TRABAJOS.",
                  "- PLAZO DE EJECUCIÓN: 5 DÍAS LABORABLES DESDE LA ACEPTACIÓN.",
                  "- VALIDEZ DE LA OFERTA: 30 DÍAS DESDE LA FECHA DEL PRESUPUESTO.",
                  "- NO INCLUYE OBRA CIVIL NI TRABAJOS NO DESCRITOS EXPRESAMENTE.",
                  "",
                  "ESTE DOCUMENTO NO TIENE VALIDEZ COMO FACTURA.",
                  "",
                  "",
                  "CONFORME EL CLIENTE (FIRMA Y SELLO):          POR LA EMPRESA:"]:
            y = ln_(y, s)
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 06 · Factura ESCANEADA (recibida): PDF solo-imagen
# ==============================================================================================
def doc_escaneada() -> dict:
    lineas = [
        {"codigo": "FA-112", "concepto": "Filtro de aceite", "contiene": "Filtro de aceite",
         "cantidad": D("1"), "precio": D("12.40")},
        {"codigo": "AC-5W30", "concepto": "Aceite motor 5W30 sintético (litros)", "contiene": "Aceite motor 5W30",
         "cantidad": D("5.5"), "precio": D("9.80")},
        {"codigo": "PF-220", "concepto": "Pastillas de freno delanteras", "contiene": "Pastillas de freno delanteras",
         "cantidad": D("1"), "precio": D("68.75")},
        {"codigo": "MO", "concepto": "Mano de obra mecánica (horas)", "contiene": "Mano de obra mecánica",
         "cantidad": D("2.25"), "precio": D("42.00")},
    ]
    t = calcular(lineas, 21)
    numero, fecha, fecha_txt = "T-26/0912", date(2026, 11, 2), "02-11-26"
    d = {
        "archivo": "var_06_escaneada_taller_recibida.pdf",
        "estilo": "Factura ESCANEADA de taller mecánico (solo imagen, 200 ppp, gris, giro 0,6°, ruido y motas, JPEG): "
                  "cabecera centrada, 'Cliente:' a la izquierda y caja FACTURA a la derecha, tabla con rejilla Ref./"
                  "Descripción/Cant./P. unit./Importe, 'TOTAL A PAGAR' sin retención",
        "empresa_nif": CIF_PINSAPAR,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": numero, "fecha": fecha, "fecha_txt": fecha_txt,
        "emisor": {"nombre": "Talleres Mecánicos Cardoncillo, S.L.", "nif": CIF_CARDONCILLO, "domicilio_contiene": "29590"},
        "destinatario": {"nombre": "Transportes Urgentes Pinsapar, S.L.", "nif": CIF_PINSAPAR,
                         "domicilio_contiene": "29004"},
        "lineas": lineas, "t": t, "escaneado": True,
        "referencias": {},
        "iban": None,
        "forma_pago_contiene": "Tarjeta de crédito",
        "indicios": [],
        "literal": ["Nº Factura: T-26/0912", "Fecha factura: 02-11-26", "TOTAL A PAGAR", "277,76 €"],
        "impresos": ["5,5", "2,25", "148.300", "31-10-26", "2.000 km"],
        "notas": "PDF escaneado SIN capa de texto (requiere OCR): se rasterizó a 200 ppp en gris, con giro de 0,6°, "
                 "ruido, motas y compresión JPEG. La sube la empresa DESTINATARIA (transportista) -> 'recibida'. "
                 "Valores esperados tal como están impresos. 'TOTAL A PAGAR' es el total de la factura (no hay "
                 "retención: total_a_pagar null). Distractores: kilometraje 148.300, fecha de entrada del vehículo "
                 "31-10-26, garantía '3 meses o 2.000 km'. Cuota 48,2055 -> 48,21.",
    }

    def render(ruta: Path):
        W, H = A4
        x0, xd = 50, W - 50
        c = nuevo_canvas(ruta, A4, f"Factura {numero}", "Talleres Mecánicos Cardoncillo")
        tx(c, W / 2, H - 60, "TALLERES MECÁNICOS CARDONCILLO, S.L.", "Helvetica-Bold", 16, alin="c")
        tx(c, W / 2, H - 76, "Mecánica · Electricidad · Neumáticos · Diagnosis", "Helvetica", 9.5, alin="c")
        tx(c, W / 2, H - 90, f"Calle Biela, 12 · 29590 Málaga · CIF {CIF_CARDONCILLO} · Tel. 952 000 000",
           "Helvetica", 9.5, alin="c")
        c.setLineWidth(1.4)
        c.line(x0, H - 100, xd, H - 100)
        tx(c, x0, H - 126, "Cliente:", "Helvetica-Bold", 10.5)
        y = H - 142
        for s in ["Transportes Urgentes Pinsapar, S.L.", "Avenida del Puerto Seco, 77", "29004 Málaga",
                  f"CIF: {CIF_PINSAPAR}"]:
            tx(c, x0, y, s, "Helvetica", 10.5)
            y -= 14
        c.setLineWidth(1)
        c.rect(340, H - 196, xd - 340, 82)
        tx(c, 352, H - 134, "FACTURA", "Helvetica-Bold", 15)
        tx(c, 352, H - 154, f"Nº Factura: {numero}", "Helvetica", 10.5)
        tx(c, 352, H - 170, f"Fecha factura: {fecha_txt}", "Helvetica", 10.5)
        tx(c, 352, H - 186, "Orden de reparación: OR-5521", "Helvetica", 10.5)
        tx(c, x0, H - 222, "Vehículo: Furgoneta modelo genérico 2.0   Matrícula: 0000 XYZ   Km: 148.300   "
           "Entrada: 31-10-26", "Helvetica", 9.5)
        cols = [x0, 120, 340, 395, 465, xd]
        cabs = ["Ref.", "Descripción", "Cant.", "P. unit.", "Importe"]
        yt = H - 250
        filas = len(lineas)
        alto_f = 22
        c.setLineWidth(0.8)
        c.rect(x0, yt - 22 - filas * alto_f, xd - x0, 22 + filas * alto_f)
        c.line(x0, yt - 22, xd, yt - 22)
        for xc in cols[1:-1]:
            c.line(xc, yt, xc, yt - 22 - filas * alto_f)
        for i, cab in enumerate(cabs):
            if i < 2:
                tx(c, cols[i] + 6, yt - 15, cab, "Helvetica-Bold", 10)
            else:
                tx(c, cols[i + 1] - 6, yt - 15, cab, "Helvetica-Bold", 10, alin="r")
        y = yt - 22 - 15
        for ln in lineas:
            tx(c, cols[0] + 6, y, ln["codigo"], "Helvetica", 10)
            tx(c, cols[1] + 6, y, ln["concepto"], "Helvetica", 10)
            tx(c, cols[3] - 6, y, cant(ln["cantidad"]), "Helvetica", 10, alin="r")
            tx(c, cols[4] - 6, y, num(ln["precio"]), "Helvetica", 10, alin="r")
            tx(c, cols[5] - 6, y, num(ln["importe"]), "Helvetica", 10, alin="r")
            y -= alto_f
        y -= 24
        c.rect(330, y - 50, xd - 330, 66)
        for et, val, fnt in [("Base imponible", num(t["base"]), "Helvetica"),
                             ("IVA 21%", num(t["cuota"]), "Helvetica"),
                             ("TOTAL A PAGAR", num(t["total"], suf=" €"), "Helvetica-Bold")]:
            tx(c, 342, y, et, fnt, 11)
            tx(c, xd - 10, y, val, fnt, 11, alin="r")
            y -= 20
        tx(c, x0, y - 30, "Forma de pago: Tarjeta de crédito", "Helvetica", 10)
        tx(c, x0, y - 46, "Garantía de la reparación: 3 meses o 2.000 km (R.D. 1457/1986).", "Helvetica", 9)
        tx(c, x0, 70, "Firma del cliente:", "Helvetica", 9)
        tx(c, W / 2, 40, "Talleres Mecánicos Cardoncillo, S.L. · Inscrita en el Registro Mercantil de Málaga, "
           "hoja MA-00000", "Helvetica", 7, GRIS, "c")
        c.showPage()
        c.save()

    d["render"] = render
    return d


DOCUMENTOS = [doc_carta, doc_minimalista, doc_recibida, doc_exenta, doc_presupuesto, doc_escaneada]


# ----------------------------------------------------------------------------------------------
# escaneado simulado
# ----------------------------------------------------------------------------------------------
PPP_ESCANEO = 200


def escanear(pdf_texto: Path, destino: Path, tmp: Path, semilla: int) -> Path:
    """Rasteriza el PDF de texto a 200 ppp y lo guarda como PDF solo-imagen con giro, ruido y motas."""
    if not shutil.which("pdftoppm"):
        raise SystemExit("pdftoppm (poppler-utils) es necesario para generar el documento escaneado")
    prefijo = tmp / "pagina"
    subprocess.run(["pdftoppm", "-r", str(PPP_ESCANEO), "-gray", "-png", "-singlefile", str(pdf_texto), str(prefijo)],
                   check=True)
    rng = random.Random(semilla)
    img = Image.open(prefijo.with_suffix(".png")).convert("L")
    papel, tinta = 236, 30
    img = img.point(lambda v: tinta + v * (papel - tinta) // 255)        # papel ligeramente gris, tinta no negra
    img = img.filter(ImageFilter.GaussianBlur(0.65))                    # desenfoque del sensor
    img = img.rotate(0.6, resample=Image.BICUBIC, expand=False, fillcolor=papel, translate=(9, 14))
    w, h = img.size
    ruido = Image.frombytes("L", (w, h), rng.randbytes(w * h)).point(lambda v: v * 26 // 255)
    img = ImageChops.add(img, ruido, scale=1.0, offset=-13)
    dib = ImageDraw.Draw(img)
    for _ in range(420):                                                # motas de polvo
        x, y, r = rng.randrange(w), rng.randrange(h), rng.choice([0, 1, 1, 1, 2])
        dib.ellipse([x - r, y - r, x + r, y + r], fill=rng.randrange(70, 160))
    for i in range(14):                                                 # sombra del borde izquierdo del escáner
        dib.line([(i, 0), (i, h)], fill=90 + i * 10)
    jpg = tmp / "escaneo.jpg"
    img.save(jpg, "JPEG", quality=72)
    # PDF solo-imagen como el de un escáner: una página con la imagen JPEG (DCTDecode), sin fuentes ni texto.
    fijo = time.struct_time((2026, 11, 3, 9, 12, 0, 1, 307, 0))
    img.save(destino, "PDF", resolution=float(PPP_ESCANEO), quality=72, title="SCAN_0001",
             creator="Escáner de documentos (sintético)", producer="Corpus sintético verifactu-app",
             subject="Documento sintético de prueba (datos ficticios)", creationDate=fijo, modDate=fijo)
    return jpg


# ----------------------------------------------------------------------------------------------
# JSON esperado
# ----------------------------------------------------------------------------------------------
def esperado(d: dict) -> dict:
    t = d["t"]
    lineas = [{"concepto_contiene": ln["contiene"], "cantidad": s2(ln.get("cantidad")), "precio": s2(ln.get("precio")),
               "importe": s2(ln["importe"])} for ln in d["lineas"]]
    desglose = [] if d.get("exenta") else [{"tipo_iva": f"{t['pct_iva']:.2f}", "base": s2(t["base"]),
                                            "cuota": s2(t["cuota"])}]
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
        "desglose": desglose,
        "base_imponible": s2(t["base"]),
        "cuota_iva": s2(t["cuota"]),
        "total_factura": s2(t["total"]),
        "retencion": None,
        "total_a_pagar": None,
        "referencias": dict(d["referencias"]),
        "iban": d["iban"],
        "forma_pago_contiene": d["forma_pago_contiene"],
        "indicios_esperados": list(d["indicios"]),
        "notas": d["notas"],
    }


# ----------------------------------------------------------------------------------------------
# verificación
# ----------------------------------------------------------------------------------------------
RE_IMPORTE = re.compile(r"(?<![\d.,])-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2,3}(?!\d)")


def _importes(texto: str) -> set[Decimal]:
    return {abs(D(m.group(0).replace(".", "").replace(",", "."))) for m in RE_IMPORTE.finditer(texto)}


def _plano(s: str) -> str:
    return re.sub(r"[\s.\-]", "", s.upper())


def _esp(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def pdftotext(ruta: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(ruta), "-"], capture_output=True, text=True,
                          check=True).stdout


def coherencia(e: dict, d: dict) -> list[str]:
    """Comprobaciones aritméticas y de formato del JSON esperado (independientes del PDF)."""
    fallos = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    base, cuota, total = D(e["base_imponible"]), D(e["cuota_iva"]), D(e["total_factura"])
    debe(sum(D(x["importe"]) for x in e["lineas"]) == base, "suma de líneas != base")
    for ln, x in zip(d["lineas"], e["lineas"]):
        if x["cantidad"] is not None:
            esperado_ln = q2(D(x["cantidad"]) * D(x["precio"]) * (100 - D(ln.get("dto") or 0)) / 100)
            debe(esperado_ln == D(x["importe"]), f"importe de línea {x['concepto_contiene']}")
    if e["desglose"]:
        debe(sum(D(x["base"]) for x in e["desglose"]) == base, "desglose: bases != base imponible")
        for x in e["desglose"]:
            debe(q2(D(x["base"]) * D(x["tipo_iva"]) / 100) == D(x["cuota"]), "desglose: cuota mal calculada")
        debe(sum(D(x["cuota"]) for x in e["desglose"]) == cuota, "desglose: cuotas != cuota_iva")
    else:
        debe(cuota == 0, "sin desglose pero cuota != 0")
    debe(base + cuota == total, "base + cuota != total")
    for parte in ("emisor", "destinatario"):
        debe(es_valido(e[parte]["nif"]), f"NIF {parte} inválido {e[parte]['nif']}")
    debe(es_valido(e["empresa_nif"]), "empresa_nif inválido")
    debe(e["empresa_nif"] == (e["destinatario"] if e["situacion_esperada"] == "recibida" else e["emisor"])["nif"],
         "empresa_nif no coincide con la parte que corresponde a la situación")
    if e["iban"]:
        debe(e["iban"].startswith(("ES00", "ES123456")) and len(e["iban"]) == 24, f"IBAN no ficticio {e['iban']}")
    return fallos


def verificar_texto(texto: str, e: dict, d: dict) -> list[str]:
    fallos = []
    esp, plano, importes = _esp(texto), _plano(texto), _importes(texto)

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    debe(e["numero"] in texto, f"número {e['numero']}")
    debe(_esp(d["fecha_txt"]) in esp, f"fecha {d['fecha_txt']}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        debe(_esp(p["nombre"]) in esp, f"nombre {parte} {p['nombre']}")
        debe(_esp(p["domicilio_contiene"]) in esp, f"domicilio {parte} {p['domicilio_contiene']}")
    for ln in e["lineas"]:
        debe(_esp(ln["concepto_contiene"]) in esp, f"concepto {ln['concepto_contiene']}")
        for k in ("precio", "importe"):
            if ln[k] is not None:
                debe(D(ln[k]) in importes, f"línea {k} {ln[k]}")
    for ln in d["lineas"]:
        if ln.get("cantidad") is not None:
            debe(any(re.search(rf"(?<![\d,.]){re.escape(s)}(?![\d,])", texto) for s in
                     (cant(ln["cantidad"]), cant(ln["cantidad"], 2))), f"cantidad {ln['cantidad']}")
    for k in ("base_imponible", "cuota_iva", "total_factura"):
        debe(D(e[k]) in importes or (D(e[k]) == 0 and "0,00" in texto), f"{k} {e[k]}")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    if e["forma_pago_contiene"]:
        debe(_esp(e["forma_pago_contiene"]) in esp, f"forma de pago {e['forma_pago_contiene']}")
    renglones = [re.sub(r"\s+", " ", r) for r in texto.splitlines()]
    for s in d.get("literal", []):                       # cadenas que deben salir en un mismo renglón
        debe(any(s in r for r in renglones), f"literal en un renglón: {s!r}")
    for s in d.get("impresos", []):
        debe(_esp(s) in esp, f"impreso {s!r}")
    for ind in e["indicios_esperados"]:
        if ind == "exencion":
            debe(re.search(r"exent[ao]", texto, re.I) is not None, "falta la mención de exención")
        if ind == "descuento":
            debe(re.search(r"dto|descuento", texto, re.I) is not None, "falta la columna de descuento")
    if e["tipo_documento"] != "factura":
        debe(e["tipo_documento"].upper() in texto.upper(), f"título {e['tipo_documento']}")
    return fallos


def verificar_escaneado(pdf: Path, jpg: Path, e: dict, d: dict) -> tuple[list[str], dict]:
    fallos, info = [], {}
    texto = pdftotext(pdf)
    if texto.strip():
        fallos.append(f"el PDF escaneado tiene capa de texto: {texto.strip()[:60]!r}")
    pagina = PdfReader(str(pdf)).pages[0]
    recursos = pagina.get("/Resources") or {}
    if "/Font" in recursos:
        fallos.append("el PDF escaneado declara fuentes")
    if "/XObject" not in recursos:
        fallos.append("el PDF escaneado no contiene imagen")
    if shutil.which("tesseract"):                         # informativo: legibilidad por OCR
        ocr = subprocess.run(["tesseract", str(jpg), "stdout", "-l", "spa", "--psm", "3"], capture_output=True,
                             text=True).stdout
        claves = {"numero": e["numero"], "fecha": d["fecha_txt"], "nif_emisor": e["emisor"]["nif"],
                  "nif_destinatario": e["destinatario"]["nif"], "base": num(D(e["base_imponible"])),
                  "cuota": num(D(e["cuota_iva"])), "total": num(D(e["total_factura"]))}
        plano = _plano(ocr)
        info["ocr_encontrados"] = {k: (_plano(v) in plano) for k, v in claves.items()}
    return fallos, info


def main(argv: list[str]) -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    verificar_ = "--sin-verificar" not in argv
    if verificar_ and not shutil.which("pdftotext"):
        print("pdftotext no disponible: se omite la verificación")
        verificar_ = False
    resumen, total_fallos = [], 0
    for i, fabrica in enumerate(DOCUMENTOS, 1):
        d = fabrica()
        ruta = SALIDA / d["archivo"]
        e = esperado(d)
        fallos, info = coherencia(e, d), {}
        if d.get("escaneado"):
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                fuente = tmp / "fuente_texto.pdf"
                d["render"](fuente)
                if verificar_:
                    fallos += [f"(fuente) {x}" for x in verificar_texto(pdftotext(fuente), e, d)]
                jpg = escanear(fuente, ruta, tmp, SEMILLA + i)
                if verificar_:
                    f2, info = verificar_escaneado(ruta, jpg, e, d)
                    fallos += f2
        else:
            d["render"](ruta)
            if verificar_:
                fallos += verificar_texto(pdftotext(ruta), e, d)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total_fallos += len(fallos)
        resumen.append({"archivo": d["archivo"], "fallos": fallos} | info)
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}" + (f"  -> {fallos}" if fallos else "")
              + (f"  {info}" if info else ""))
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
