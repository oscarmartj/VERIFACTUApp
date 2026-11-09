"""Segundo corpus SINTÉTICO «held-out» (ciego) de facturas españolas para medir la generalización del extractor.

Ocho maquetaciones nuevas, distintas de las de los corpus erp/obra/profesionales/variados/holdout:

  1. Factura bilingüe catalán/castellano («Data / Fecha», «Base imposable / Base imponible»), imprenta de Barcelona.
  2. Factura «suiza» minimalista con separador de miles por ESPACIO («10 653,32 €») y fechas «3 nov. 2026» / ISO.
  3. Distribuidor alimentario: columna de IVA por línea (4/10/21 %), desglose por tipo bajo la tabla y descuento negativo.
  4. Suscripción SaaS con bloques «Facturar a» / «Enviar a», columna de periodo y licencias (US Letter).
  5. Fotógrafa autónoma (persona física con DNI), IRPF 15 % y «Total a percibir».
  6. Factura RECIBIDA de una comercializadora eléctrica: resumen, detalle de cálculo, lecturas e histórico (2 páginas).
  7. Factura apaisada con 8 columnas (código, descripción multilínea, lote, uds., precio, dto. %, IVA %, importe).
  8. Factura RECIBIDA escaneada (talonario rellenado a máquina): solo imagen, giro, desenfoque y motas, sin capa de texto.

TODOS LOS DATOS SON FICTICIOS: razones sociales, nombres de persona, domicilios, teléfonos (9x0 000 000), correos
(dominio .example), CUPS, contratos, referencias e IBAN (ES00 + dígitos en serie, dígito de control imposible). Los
NIF/CIF tienen carácter de control válido (app.nif.completar_cif / letra del DNI) pero se han inventado y no coinciden
con los de los demás corpus del repositorio (se comprueba al generar).

Este corpus se diseñó SIN consultar el código del extractor (app/extraccion/) ni los datos privados: es un conjunto
ciego. No lo ajustes para que el extractor acierte o falle.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/holdout2.py      # genera PDF + JSON en tests/corpus_holdout2/ y verifica

Verificación: coherencia aritmética de cada JSON (líneas = bases, cuota = base x tipo redondeada half-up, total,
retención y total a pagar) y, salvo en el escaneado, presencia en el texto de `pdftotext -layout` del número, la
fecha, los NIF tal como se imprimen, nombres, códigos postales, conceptos, importes, referencias, IBAN y forma de
pago. Del escaneado se verifica la aritmética, que no tenga capa de texto y (además) el texto del PDF limpio de
origen. Sale con código 1 si algo falla.

Requisitos: reportlab, Pillow, pypdf, poppler-utils (pdftotext, pdftoppm). Fuentes TrueType opcionales (Carlito,
Caladea, DejaVu Sans en /usr/share/fonts/truetype); si faltan se sustituyen por Helvetica/Times con un aviso y los PDF
no serán byte a byte idénticos a los publicados.

Determinista: canvas de reportlab en modo «invariant», semilla fija para el ruido del escaneado y fechas fijas en
los metadatos del PDF-imagen.
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
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from PIL import Image, ImageChops, ImageDraw, ImageFilter  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape, letter  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import completar_cif, es_valido, normalizar  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus_holdout2"
SEMILLA = 20261109
random.seed(SEMILLA)

D = Decimal
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
REFS_PERMITIDAS = {"obra", "certificacion", "pedido", "partida_presupuestaria", "periodo", "contrato", "proyecto",
                   "expediente", "albaran", "responsable"}
INDICIOS = {"exencion", "inversion_sujeto_pasivo", "recargo_equivalencia", "suplidos", "descuento", "rectificativa",
            "intracomunitaria"}


# ----------------------------------------------------------------------------------------------
# fuentes
# ----------------------------------------------------------------------------------------------
_TTF = {
    "Carlito": ("crosextra/Carlito-Regular.ttf", "Helvetica"),
    "Carlito-Bold": ("crosextra/Carlito-Bold.ttf", "Helvetica-Bold"),
    "Carlito-Italic": ("crosextra/Carlito-Italic.ttf", "Helvetica-Oblique"),
    "Caladea": ("crosextra/Caladea-Regular.ttf", "Times-Roman"),
    "Caladea-Bold": ("crosextra/Caladea-Bold.ttf", "Times-Bold"),
    "Caladea-Italic": ("crosextra/Caladea-Italic.ttf", "Times-Italic"),
    "DejaVuSans": ("dejavu/DejaVuSans.ttf", "Helvetica"),
    "DejaVuSans-Bold": ("dejavu/DejaVuSans-Bold.ttf", "Helvetica-Bold"),
}
FUENTE: dict[str, str] = {}


def registrar_fuentes() -> None:
    base = Path("/usr/share/fonts/truetype")
    for nombre, (rel, reserva) in _TTF.items():
        ruta = base / rel
        if ruta.exists():
            pdfmetrics.registerFont(TTFont(nombre, str(ruta)))
            FUENTE[nombre] = nombre
        else:
            print(f"aviso: falta {ruta}; se usa {reserva} (los PDF no serán idénticos a los publicados)")
            FUENTE[nombre] = reserva


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def dni(n: int) -> str:
    return f"{n:08d}{LETRAS_DNI[n % 23]}"


def q2(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(x) -> str | None:
    return None if x is None else str(q2(x))


def num(x, miles: str = ".", nd: int = 2) -> str:
    """Formato español: 1.234,56 (miles='.'), 1 234,56 (miles=' ') o 1234,56 (miles='')."""
    x = D(x).quantize(D(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    ent, _, fr = f"{abs(x):.{nd}f}".partition(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", miles)
    return ("-" if x < 0 else "") + ent + ("," + fr if nd else "")


def num_esp(x) -> str:
    return num(x, miles=" ")


def calcular(lineas: list[dict], pct_ret=None) -> dict:
    """Importe de línea = cantidad x precio menos Dto. % (half-up); desglose por tipo; retención sobre la base."""
    for ln in lineas:
        ln.setdefault("iva", D("21"))
        ln.setdefault("dto", D("0"))
        if ln.get("importe") is None:
            ln["importe"] = q2(D(ln["cantidad"]) * D(ln["precio"]) * (D(100) - D(ln["dto"])) / D(100))
        else:
            ln["importe"] = q2(ln["importe"])
        ln.setdefault("primera", ln["concepto"])
    bases: dict[Decimal, Decimal] = {}
    for ln in lineas:
        bases[D(ln["iva"])] = bases.get(D(ln["iva"]), D("0")) + ln["importe"]
    desglose = [{"tipo": t, "base": b, "cuota": q2(b * t / D(100))} for t, b in sorted(bases.items())]
    base = sum((g["base"] for g in desglose), D("0"))
    cuota = sum((g["cuota"] for g in desglose), D("0"))
    total = base + cuota
    ret = q2(base * D(pct_ret) / D(100)) if pct_ret is not None else None
    return {"desglose": desglose, "base": base, "cuota": cuota, "total": total,
            "pct_ret": D(pct_ret) if pct_ret is not None else None, "ret": ret,
            "pagar": (total - ret) if ret is not None else None}


# ----------------------------------------------------------------------------------------------
# utilidades de dibujo
# ----------------------------------------------------------------------------------------------
def ancho(s: str, fuente: str, tam: float, cs: float = 0.0) -> float:
    return pdfmetrics.stringWidth(s, fuente, tam) + cs * max(0, len(s) - 1)


def tx(c, x, y, s, fuente="Helvetica", tam=9.0, color=black, alin="l", cs=0.0):
    c.setFillColor(color)
    if cs:
        # el espaciado (operador Tc) pertenece al estado gráfico: se aísla para que no afecte al resto del texto
        w = ancho(s, fuente, tam, cs)
        x0 = {"l": x, "r": x - w, "c": x - w / 2}[alin]
        c.saveState()
        to = c.beginText(x0, y)
        to.setFont(fuente, tam)
        to.setCharSpace(cs)
        to.textOut(s)
        to.setCharSpace(0)
        c.drawText(to)
        c.restoreState()
        return
    c.setFont(fuente, tam)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[alin](x, y, s)


def raya(c, x1, y1, x2, y2, color=black, grosor=0.5, discontinua=None):
    c.setStrokeColor(color)
    c.setLineWidth(grosor)
    c.setDash(*(discontinua or ()))
    c.line(x1, y1, x2, y2)
    c.setDash()


def rect(c, x, y, w, h, relleno=None, borde=None, grosor=0.5, radio=0.0):
    if relleno is not None:
        c.setFillColor(relleno)
    if borde is not None:
        c.setStrokeColor(borde)
        c.setLineWidth(grosor)
    f, s = int(relleno is not None), int(borde is not None)
    if radio:
        c.roundRect(x, y, w, h, radio, fill=f, stroke=s)
    else:
        c.rect(x, y, w, h, fill=f, stroke=s)


def puntos(c, x1, x2, y, fuente, tam, color):
    c.setFont(fuente, tam)
    c.setFillColor(color)
    paso = c.stringWidth(". ", fuente, tam)
    c.drawString(x1, y, ". " * max(0, int((x2 - x1) / paso)))


def nuevo_canvas(ruta: Path, tam_pagina, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=tam_pagina, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    c.setCreator("scripts/corpus/holdout2.py")
    return c


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido); ninguno aparece en los demás corpus (se comprueba en main)
# ----------------------------------------------------------------------------------------------
CIF_FONTANELLA = completar_cif("B", "0861537")   # emisor doc 1 (imprenta, Barcelona)
CIF_VALLMORENA = completar_cif("B", "0827419")   # cliente doc 1 (bodega, Vilafranca del Penedès)
CIF_EBROVALLE = completar_cif("B", "2648193")    # emisor doc 2 (mantenimiento industrial, Logroño)
CIF_CERROLOBO = completar_cif("A", "2617302")    # cliente doc 2 (bodega, Haro)
CIF_GENIL = completar_cif("B", "1863027")        # emisor doc 3 (distribuidor alimentario, Albolote)
CIF_COLLADO = completar_cif("B", "1839154")      # cliente doc 3 (mesón, Lanjarón)
CIF_NUBARIA = completar_cif("B", "4659318")      # emisor doc 4 (SaaS, València)
CIF_TURIANOS = completar_cif("B", "9734261")     # cliente doc 4 (asesoría, València)
NIF_FOTOGRAFA = dni(74213958)                    # emisora doc 5 (fotógrafa autónoma, Málaga)
CIF_AZAHAR = completar_cif("B", "1206748")       # cliente doc 5 (editorial, Castelló)
CIF_VALDELUZ = completar_cif("A", "8729145")     # emisor doc 6 (comercializadora eléctrica, Madrid)
CIF_TRIGAL = completar_cif("B", "0941875")       # destinatario doc 6 = empresa usuaria (obrador, Aranda)
CIF_TIERRA_BARROS = completar_cif("F", "0647281")  # emisor doc 7 (cooperativa agraria, Almendralejo)
CIF_ZURBARAN = completar_cif("B", "0619573")     # cliente doc 7 (viveros, Lobón)
CIF_PISUERGA = completar_cif("B", "4728356")     # emisor doc 8 (fontanería, Valladolid)
CIF_CAMPOS = completar_cif("B", "4791602")       # destinatario doc 8 = empresa usuaria (asesoría laboral)


# ==============================================================================================
# 1. Bilingüe catalán / castellano — imprenta de Barcelona (Carlito, banda de color, celdas)
# ==============================================================================================
def doc_01() -> dict:
    lineas = [
        {"concepto": "Impressió de catàleg comercial 2027, 48 pàgines, A4",
         "es": "Impresión de catálogo comercial 2027, 48 páginas, A4",
         "contiene": "Impressió de catàleg comercial", "cantidad": D("1500"), "precio": D("1.84"),
         "cant_txt": "1.500"},
        {"concepto": "Etiquetes adhesives per a ampolla, paper verjurat",
         "es": "Etiquetas adhesivas para botella, papel verjurado",
         "contiene": "Etiquetes adhesives per a ampolla", "cantidad": D("12000"), "precio": D("0.12"),
         "cant_txt": "12.000"},
        {"concepto": "Disseny i maquetació de fitxes de producte",
         "es": "Diseño y maquetación de fichas de producto",
         "contiene": "Disseny i maquetació de fitxes", "cantidad": D("16"), "precio": D("35.00"), "cant_txt": "16"},
        {"concepto": "Proves de color certificades (format A3)",
         "es": "Pruebas de color certificadas (formato A3)",
         "contiene": "Proves de color certificades", "cantidad": D("4"), "precio": D("22.50"), "cant_txt": "4"},
        {"concepto": "Ports i lliurament a Vilafranca del Penedès",
         "es": "Portes y entrega en Vilafranca del Penedès",
         "contiene": "Ports i lliurament", "cantidad": D("1"), "precio": D("45.00"), "cant_txt": "1"},
    ]
    t = calcular(lineas)
    return {
        "archivo": "hold2_01_bilingue_catalan_castellano.pdf",
        "estilo": ("Factura bilingüe catalán/castellano de una imprenta de Barcelona: banda superior de color con la "
                   "razón social en blanco y 'FACTURA / FACTURA', fila de 4 celdas con borde etiqueta-sobre-valor "
                   "('Núm. factura / Nº factura', 'Data / Fecha', 'Venciment / Vencimiento', 'Comanda / Pedido'), "
                   "caja 'Client / Cliente' con fondo y caja 'Forma de pagament / Forma de pago' con el IBAN junto a "
                   "ella, tabla con cabecera en dos idiomas y conceptos en dos líneas (catalán arriba, castellano en "
                   "cursiva debajo), totales 'Base imposable / Base imponible', 'Quota d'IVA 21 % / Cuota de IVA "
                   "21 %' y 'Total factura' en banda de color, fuente Carlito (TrueType), A4"),
        "empresa_nif": CIF_FONTANELLA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "FB-2026/0412",
        "fecha": date(2026, 11, 4),
        "fecha_txt": "04/11/2026",
        "emisor": {"nombre": "Impremta Fontanella Badia, S.L.", "nif": CIF_FONTANELLA, "domicilio_contiene": "08025"},
        "destinatario": {"nombre": "Celler Vallmorena del Penedès, S.L.", "nif": CIF_VALLMORENA,
                         "domicilio_contiene": "08720"},
        "nif_txt": {"emisor": f"NIF: {CIF_FONTANELLA}", "destinatario": f"NIF: {CIF_VALLMORENA}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"pedido": "VMP-2026-118", "albaran": "AF-26-0981"},
        "iban": "ES0011112222333344440801",
        "iban_txt": "ES00 1111 2222 3333 4444 0801",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "textos": ["Data / Fecha", "Base imposable / Base imponible", "Client / Cliente", "04/12/2026",
                   "Albarà / Albarán: AF-26-0981"],
        "notas": ("Factura propia de la imprenta (empresa usuaria = emisor) sin marcas VERI*FACTU: documento "
                  "preparatorio. Todas las etiquetas van en catalán y castellano separadas por ' / '. Los conceptos "
                  "se imprimen en dos líneas: la primera en catalán (la que recoge concepto_contiene) y la segunda, "
                  "traducción al castellano en cursiva gris. Cantidades con punto de miles (1.500 y 12.000 "
                  "unidades) y precio unitario 0,12. Distractores: fecha de vencimiento 04/12/2026 junto a la fecha "
                  "de emisión, fecha de entrega 30/10/2026 en observaciones, referencia de albarán 'Albarà / Albarán: "
                  "AF-26-0981' en observaciones (al pie de la tabla) y el IBAN arriba, al lado del cliente. "
                  f"Base {num(t['base'])} x 21 % = {num(t['cuota'])}; total {num(t['total'])}."),
        "render": render_01,
    }


def render_01(ruta: Path, d: dict):
    W, H = A4
    F, FB, FI = FUENTE["Carlito"], FUENTE["Carlito-Bold"], FUENTE["Carlito-Italic"]
    R, G, CL, INK = HexColor("#8C1C13"), HexColor("#6B6B6B"), HexColor("#F6ECEA"), HexColor("#1F1F1F")
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", em["nombre"])
    M = 42

    rect(c, 0, H - 92, W, 92, relleno=R)
    tx(c, M, H - 46, em["nombre"], FB, 21, white)
    tx(c, M, H - 64, "Arts gràfiques i impressió digital · Artes gráficas e impresión digital", F, 10, white)
    tx(c, W - M, H - 50, "FACTURA / FACTURA", FB, 22, white, "r")
    tx(c, W - M, H - 66, "Original", FI, 9.5, white, "r")
    tx(c, M, H - 112, "Carrer del Rosselló 377, baixos · 08025 Barcelona", F, 10, G)
    tx(c, M, H - 125, f"{d['nif_txt']['emisor']} · Tel. 930 000 000 · factures@fontanella.example", F, 10, G)

    celdas = [("Núm. factura / Nº factura", d["numero"]), ("Data / Fecha", d["fecha_txt"]),
              ("Venciment / Vencimiento", "04/12/2026"), ("Comanda / Pedido", d["referencias"]["pedido"])]
    y0, h = H - 192, 46
    w = (W - 2 * M) / 4
    for i, (k, v) in enumerate(celdas):
        x = M + i * w
        rect(c, x, y0, w, h, borde=R, grosor=0.8)
        tx(c, x + 8, y0 + h - 14, k, F, 8.5, G)
        tx(c, x + 8, y0 + 12, v, FB, 13, INK)

    yb, hb = H - 214, 94
    wb = (W - 2 * M - 16) / 2
    rect(c, M, yb - hb, wb, hb, relleno=CL)
    tx(c, M + 10, yb - 16, "Client / Cliente", FB, 10, R)
    tx(c, M + 10, yb - 34, de["nombre"], FB, 12, INK)
    tx(c, M + 10, yb - 49, d["nif_txt"]["destinatario"], F, 10.5, INK)
    tx(c, M + 10, yb - 63, "Carrer Major 12, 1r", F, 10.5, INK)
    tx(c, M + 10, yb - 77, "08720 Vilafranca del Penedès (Barcelona)", F, 10.5, INK)
    xp = M + wb + 16
    rect(c, xp, yb - hb, wb, hb, borde=R, grosor=0.8)
    tx(c, xp + 10, yb - 16, "Forma de pagament / Forma de pago", FB, 10, R)
    tx(c, xp + 10, yb - 33, "Transferència bancària / Transferencia bancaria", F, 10.5, INK)
    tx(c, xp + 10, yb - 50, "IBAN:", F, 9, G)
    tx(c, xp + 10, yb - 64, d["iban_txt"], FB, 11, INK)
    tx(c, xp + 10, yb - 82, "Indiqueu el número de factura / Indique el número de factura", FI, 8.5, G)

    yt = yb - hb - 28
    xq, xpu, xi = 400, 478, W - M - 8
    rect(c, M, yt - 32, W - 2 * M, 32, relleno=R)
    for cat, es, x, al in [("Descripció", "Descripción", M + 8, "l"), ("Quantitat", "Cantidad", xq, "r"),
                           ("Preu unitari", "Precio unitario", xpu, "r"), ("Import", "Importe", xi, "r")]:
        tx(c, x, yt - 13, cat, FB, 9.5, white, al)
        tx(c, x, yt - 25, es, FI, 8.5, white, al)
    y = yt - 32
    for ln in d["lineas"]:
        tx(c, M + 8, y - 16, ln["concepto"], F, 10.5, INK)
        tx(c, M + 8, y - 28, ln["es"], FI, 9, G)
        tx(c, xq, y - 16, ln["cant_txt"], F, 10.5, INK, "r")
        tx(c, xpu, y - 16, fmt(ln["precio"]) + " €", F, 10.5, INK, "r")
        tx(c, xi, y - 16, fmt(ln["importe"]) + " €", F, 10.5, INK, "r")
        y -= 36
        raya(c, M, y, W - M, y, HexColor("#D9C4C1"), 0.6)

    # observaciones a la izquierda y totales a la derecha
    yo = y - 24
    tx(c, M, yo, "Observacions / Observaciones", FB, 9.5, R)
    tx(c, M, yo - 14, "Albarà / Albarán: AF-26-0981", F, 9.5, INK)
    tx(c, M, yo - 27, "Lliurat el 30/10/2026 / Entregado el 30/10/2026", F, 9.5, INK)
    tx(c, M, yo - 40, "Tiratge revisat i aprovat pel client / Tirada revisada y aprobada", FI, 9, G)
    xl, xv = 318, W - M - 8
    yy = y - 24
    for k, v in [("Base imposable / Base imponible", t["base"]),
                 ("Quota d'IVA 21 % / Cuota de IVA 21 %", t["cuota"])]:
        tx(c, xl, yy, k, F, 10.5, INK)
        tx(c, xv, yy, fmt(v) + " €", F, 11, INK, "r")
        yy -= 19
    rect(c, xl - 8, yy - 10, W - M - xl + 8, 28, relleno=R)
    tx(c, xl, yy, "Total factura / Total factura", FB, 12, white)
    tx(c, xv, yy, fmt(t["total"]) + " €", FB, 13.5, white, "r")

    raya(c, M, 76, W - M, 76, HexColor("#D9C4C1"), 0.6)
    tx(c, W / 2, 63, "Impremta Fontanella Badia, S.L. · Inscrita al Registre Mercantil de Barcelona, tom 47.215, "
                     "foli 33, full B-551203", F, 8, G, "c")
    tx(c, W / 2, 52, "Les dades personals es tracten per gestionar la relació comercial / Los datos personales se "
                     "tratan para gestionar la relación comercial.", F, 8, G, "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 2. Minimalista «suizo»: miles con espacio y fechas «3 nov. 2026» / ISO
# ==============================================================================================
def doc_02() -> dict:
    lineas = [
        {"fecha": "2026-10-21", "concepto": "Revisión anual de prensa neumática de membrana",
         "contiene": "Revisión anual de prensa neumática", "cantidad": D("1"), "precio": D("2450.00"), "cant_txt": "1"},
        {"fecha": "2026-10-22", "concepto": "Sustitución de bomba de trasiego inox 7,5 kW",
         "contiene": "Sustitución de bomba de trasiego", "cantidad": D("1"), "precio": D("4180.00"), "cant_txt": "1"},
        {"fecha": "2026-10-22", "concepto": "Horas de técnico especialista",
         "contiene": "Horas de técnico especialista", "cantidad": D("36"), "precio": D("48.50"), "cant_txt": "36 h"},
        {"fecha": "2026-10-22", "concepto": "Juego de juntas y retenes alimentarios",
         "contiene": "Juego de juntas y retenes", "cantidad": D("6"), "precio": D("38.90"), "cant_txt": "6"},
        {"fecha": "2026-10-23", "concepto": "Desplazamiento y dietas (jornadas)",
         "contiene": "Desplazamiento y dietas", "cantidad": D("3"), "precio": D("65.00"), "cant_txt": "3"},
    ]
    t = calcular(lineas)
    return {
        "archivo": "hold2_02_miles_espacio_fecha_abreviada.pdf",
        "estilo": ("Factura minimalista de estilo suizo de una empresa de mantenimiento industrial: logotipo "
                   "tipográfico, número grande a la derecha bajo un rótulo 'FACTURA' espaciado, fila de 5 campos con "
                   "etiqueta en MAYÚSCULAS pequeñas sobre el valor (EMITIDA / VENCIMIENTO / PEDIDO / ALBARÁN / "
                   "CLIENTE Nº), bloque 'FACTURAR A', importe total grande en color arriba a la derecha, tabla sin "
                   "rejilla con columna de FECHA ISO por línea, importes con ESPACIO como separador de miles "
                   "('10 653,32 €'), fecha de emisión '3 nov. 2026' y vencimiento ISO '2026-12-03', Helvetica, A4"),
        "empresa_nif": CIF_EBROVALLE,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "EV-26-1093",
        "fecha": date(2026, 11, 3),
        "fecha_txt": "3 nov. 2026",
        "emisor": {"nombre": "Mantenimientos Hidráulicos Ebrovalle, S.L.", "nif": CIF_EBROVALLE,
                   "domicilio_contiene": "26009"},
        "destinatario": {"nombre": "Bodegas Cerrolobo Alto, S.A.", "nif": CIF_CERROLOBO,
                         "domicilio_contiene": "26200"},
        "nif_txt": {"emisor": f"CIF {CIF_EBROVALLE[0]}-{CIF_EBROVALLE[1:]}", "destinatario": f"CIF {CIF_CERROLOBO}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"pedido": "OC-26/1182", "albaran": "AT-26-0457"},
        "iban": "ES0022223333444455552601",
        "iban_txt": "ES00 2222 3333 4444 5555 2601",
        "forma_pago_contiene": "Transferencia a 30 días",
        "indicios": [],
        "fmt": num_esp,
        "textos": ["EMITIDA", "FACTURAR A", "2026-12-03", "2026-10-21", num_esp(t["total"]) + " €"],
        "notas": ("Factura propia de la empresa de mantenimiento sin marcas VERI*FACTU: documento preparatorio. "
                  "Trampas: todos los importes usan ESPACIO como separador de miles y coma decimal ('2 450,00', "
                  f"'{num_esp(t['total'])} €'); la fecha de emisión está abreviada ('3 nov. 2026') y el vencimiento y "
                  "las fechas de servicio de cada línea van en ISO (2026-12-03, 2026-10-21...); las etiquetas son "
                  "MAYÚSCULAS pequeñas encima del valor; el total aparece dos veces (arriba en grande con 'IVA "
                  "incluido' y en el pie de la tabla); 'CLIENTE Nº 4471' y la potencia '7,5 kW' del concepto son "
                  "distractores numéricos. El CIF del emisor se imprime con guion (B-…). "
                  f"Base {num(t['base'])} x 21 % = 1.848,924 -> {num(t['cuota'])}; total {num(t['total'])}."),
        "render": render_02,
    }


def render_02(ruta: Path, d: dict):
    W, H = A4
    M = 50
    HR, HB = "Helvetica", "Helvetica-Bold"
    INK, G, AC, L = HexColor("#141414"), HexColor("#8A8A8A"), HexColor("#E4572E"), HexColor("#DADADA")
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "Ebrovalle")

    rect(c, M, H - 68, 9, 28, relleno=AC)
    tx(c, M + 17, H - 64, "EBROVALLE", HB, 25, INK)
    tx(c, M, H - 86, em["nombre"], HR, 9.5, INK)
    tx(c, M, H - 98, "Calle Laurel Viejo 18, Polígono Cantabria", HR, 8.5, G)
    tx(c, M, H - 109, "26009 Logroño (La Rioja)", HR, 8.5, G)
    tx(c, M, H - 120, f"{d['nif_txt']['emisor']} · 941 000 000 · taller@ebrovalle.example", HR, 8.5, G)
    tx(c, W - M, H - 50, "FACTURA", HR, 8, G, "r", cs=2.2)
    tx(c, W - M, H - 74, d["numero"], HB, 20, INK, "r")

    campos = [("EMITIDA", d["fecha_txt"]), ("VENCIMIENTO", "2026-12-03"), ("PEDIDO", d["referencias"]["pedido"]),
              ("ALBARÁN", d["referencias"]["albaran"]), ("CLIENTE Nº", "4471")]
    y = H - 168
    raya(c, M, y + 24, W - M, y + 24, L, 0.6)
    paso = (W - 2 * M) / 5
    for i, (k, v) in enumerate(campos):
        x = M + i * paso
        tx(c, x, y + 8, k, HR, 6.8, G)
        tx(c, x, y - 8, v, HR, 11, INK)
    raya(c, M, y - 20, W - M, y - 20, L, 0.6)

    y = H - 222
    tx(c, M, y, "FACTURAR A", HR, 6.8, G)
    tx(c, M, y - 18, de["nombre"], HB, 12.5, INK)
    tx(c, M, y - 33, "Carretera de Briñas km 2,3", HR, 9.5, INK)
    tx(c, M, y - 46, "26200 Haro (La Rioja)", HR, 9.5, INK)
    tx(c, M, y - 59, d["nif_txt"]["destinatario"], HR, 9.5, INK)
    tx(c, W - M, y, "IMPORTE TOTAL", HR, 6.8, G, "r")
    tx(c, W - M, y - 32, fmt(t["total"]) + " €", HB, 27, AC, "r")
    tx(c, W - M, y - 48, "IVA incluido", HR, 8, G, "r")

    y = H - 325
    xf, xc, xq, xp, xi = M, M + 80, 395, 475, W - M
    for k, x, al in [("FECHA", xf, "l"), ("CONCEPTO", xc, "l"), ("CANTIDAD", xq, "r"), ("PRECIO", xp, "r"),
                     ("IMPORTE", xi, "r")]:
        tx(c, x, y, k, HR, 6.8, G, al)
    raya(c, M, y - 8, W - M, y - 8, INK, 0.9)
    y -= 8
    for ln in d["lineas"]:
        yb = y - 17
        tx(c, xf, yb, ln["fecha"], HR, 9, G)
        tx(c, xc, yb, ln["concepto"], HR, 9.5, INK)
        tx(c, xq, yb, ln["cant_txt"], HR, 9.5, INK, "r")
        tx(c, xp, yb, fmt(ln["precio"]), HR, 9.5, INK, "r")
        tx(c, xi, yb, fmt(ln["importe"]), HR, 9.5, INK, "r")
        y -= 27
        raya(c, M, y, W - M, y, L, 0.5)

    xl = 360
    y -= 24
    for k, v in [("SUBTOTAL", t["base"]), ("IVA 21 %", t["cuota"])]:
        tx(c, xl, y, k, HR, 8, G)
        tx(c, xi, y, fmt(v) + " €", HR, 10, INK, "r")
        y -= 18
    raya(c, xl, y + 6, W - M, y + 6, INK, 0.9)
    tx(c, xl, y - 12, "TOTAL", HB, 9, INK)
    tx(c, xi, y - 12, fmt(t["total"]) + " €", HB, 15, INK, "r")

    y = 190
    tx(c, M, y, "PAGO", HR, 6.8, G)
    tx(c, M, y - 15, "Transferencia a 30 días fecha factura", HR, 9.5, INK)
    tx(c, M, y - 29, f"IBAN {d['iban_txt']}", HR, 9.5, INK)
    tx(c, 330, y, "NOTAS", HR, 6.8, G)
    yy = y - 15
    for s in simpleSplit("Garantía de 6 meses en las piezas sustituidas. Trabajos realizados en la nave de crianza "
                         "de la bodega según parte firmado por el responsable de mantenimiento.", HR, 8.5, W - M - 330):
        tx(c, 330, yy, s, HR, 8.5, INK)
        yy -= 11.5
    raya(c, M, 66, W - M, 66, L, 0.5)
    tx(c, M, 52, "Mantenimientos Hidráulicos Ebrovalle, S.L. · Registro Mercantil de La Rioja, tomo 812, folio 44, "
                 "hoja LO-13377", HR, 7, G)
    tx(c, W - M, 52, "1/1", HR, 7, G, "r")
    c.showPage()
    c.save()


# ==============================================================================================
# 3. Distribuidor alimentario: IVA por línea (4/10/21 %), desglose por tipo y descuento negativo
# ==============================================================================================
def doc_03() -> dict:
    lineas = [
        {"concepto": "Pan de hogaza artesano 800 g", "contiene": "Pan de hogaza artesano", "cantidad": D("40"),
         "precio": D("2.15"), "ud": "ud", "iva": D("4"), "cant_txt": "40"},
        {"concepto": "Leche entera UHT brik 1 L (caja 6)", "contiene": "Leche entera UHT", "cantidad": D("20"),
         "precio": D("6.30"), "ud": "caja", "iva": D("4"), "cant_txt": "20"},
        {"concepto": "Descuento promoción lácteos octubre (5 % s/ leche)", "contiene": "Descuento promoción lácteos",
         "importe": D("-6.30"), "iva": D("4")},
        {"concepto": "Huevos camperos talla L (estuche 30)", "contiene": "Huevos camperos talla L",
         "cantidad": D("8"), "precio": D("7.90"), "ud": "est.", "iva": D("4"), "cant_txt": "8"},
        {"concepto": "Pechuga de pollo fileteada", "contiene": "Pechuga de pollo fileteada", "cantidad": D("18.5"),
         "precio": D("7.40"), "ud": "kg", "iva": D("10"), "cant_txt": "18,5"},
        {"concepto": "Agua mineral natural 1,5 L (pack 6)", "contiene": "Agua mineral natural", "cantidad": D("15"),
         "precio": D("2.64"), "ud": "pack", "iva": D("10"), "cant_txt": "15"},
        {"concepto": "Detergente lavavajillas industrial 20 L", "contiene": "Detergente lavavajillas industrial",
         "cantidad": D("2"), "precio": D("34.50"), "ud": "garrafa", "iva": D("21"), "cant_txt": "2"},
        {"concepto": "Servilletas de papel 2 capas (caja 2.400 uds.)", "contiene": "Servilletas de papel 2 capas",
         "cantidad": D("3"), "precio": D("18.75"), "ud": "caja", "iva": D("21"), "cant_txt": "3"},
    ]
    t = calcular(lineas)
    g4, g10, g21 = t["desglose"]
    return {
        "archivo": "hold2_03_iva_por_linea_desglose_descuento.pdf",
        "estilo": ("Factura de distribuidor alimentario a un mesón, estilo 'Etiqueta:' en Times: cabecera con "
                   "monograma, título FACTURA con filete verde, caja 'Datos de la factura:' (Número:/Fecha:/Fecha de "
                   "entrega:/Albarán:/Código de cliente:) y caja 'Datos del cliente:', tabla rayada en cebra "
                   "Artículo | Cant. | Ud. | Precio | % IVA | Importe con un tipo de IVA distinto por línea (4, 10 y "
                   "21 %) y una línea de descuento negativa sin cantidad ni precio, debajo un cuadro 'Desglose por "
                   "tipo de IVA:' con tres filas y fila Total, y a la derecha caja de totales, A4"),
        "empresa_nif": CIF_GENIL,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "GA-26/04417",
        "fecha": date(2026, 10, 29),
        "fecha_txt": "29-10-2026",
        "emisor": {"nombre": "Distribuciones Alimentarias Genil Alto, S.L.", "nif": CIF_GENIL,
                   "domicilio_contiene": "18220"},
        "destinatario": {"nombre": "Mesón Collado Alpujarreño, S.L.", "nif": CIF_COLLADO,
                         "domicilio_contiene": "18420"},
        "nif_txt": {"emisor": f"C.I.F.: {CIF_GENIL}", "destinatario": f"C.I.F.: {CIF_COLLADO}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"albaran": "AV-7712"},
        "iban": "ES0033334444555566661801",
        "iban_txt": "ES00 3333 4444 5555 6666 1801",
        "forma_pago_contiene": "Transferencia a 15 días",
        "indicios": ["descuento"],
        "fmt": num,
        "textos": ["Desglose por tipo de IVA:", "Fecha de entrega: 27-10-2026", "% IVA", "TOTAL FACTURA:"],
        "notas": ("Factura propia del distribuidor sin marcas VERI*FACTU: documento preparatorio. Cada línea lleva su "
                  "propio tipo de IVA en una columna '% IVA' (4 % pan, leche y huevos; 10 % pollo y agua; 21 % "
                  "limpieza y menaje). La línea 'Descuento promoción lácteos octubre (5 % s/ leche)' es negativa "
                  "(-6,30), no tiene cantidad ni precio y tributa al 4 %: reduce la base del 4 %. Cantidad decimal "
                  "18,5 kg. Debajo de la tabla hay un desglose por tipo con fila Total y a la derecha los totales. "
                  "Distractores: fecha de entrega 27-10-2026, vencimiento 13-11-2026, código de cliente 00318, "
                  "'2.400 uds.' y '1,5 L' dentro de los conceptos. "
                  f"4 %: {num(g4['base'])} -> {num(g4['cuota'])} (10,756); 10 %: {num(g10['base'])} -> "
                  f"{num(g10['cuota'])}; 21 %: {num(g21['base'])} -> {num(g21['cuota'])} (26,3025); base "
                  f"{num(t['base'])}, IVA {num(t['cuota'])}, total {num(t['total'])}."),
        "render": render_03,
    }


def render_03(ruta: Path, d: dict):
    W, H = A4
    M = 40
    TR, TB, TI = "Times-Roman", "Times-Bold", "Times-Italic"
    VE, VL, G, INK = HexColor("#2E7D32"), HexColor("#EAF4EA"), HexColor("#555555"), black
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", em["nombre"])

    tx(c, M, H - 56, em["nombre"], TB, 15, INK)
    for i, s in enumerate(["Polígono Juncaril, calle Loja 31", "18220 Albolote (Granada)",
                           d["nif_txt"]["emisor"], "Tel.: 958 000 000 · pedidos@genilalto.example"]):
        tx(c, M, H - 72 - 12 * i, s, TR, 10, G)
    rect(c, W - M - 74, H - 96, 74, 50, relleno=VE, radio=8)
    tx(c, W - M - 37, H - 80, "GA", TB, 26, white, "c")
    tx(c, W - M - 37, H - 108, "genil alto", TI, 9, VE, "c")

    tx(c, M, H - 142, "FACTURA", TB, 20, VE)
    raya(c, M, H - 150, W - M, H - 150, VE, 1.6)

    yb, hb = H - 162, 96
    wl = 255
    rect(c, M, yb - hb, wl, hb, borde=VE, grosor=0.8)
    tx(c, M + 10, yb - 15, "Datos de la factura:", TB, 10.5, VE)
    filas = [("Número:", d["numero"]), ("Fecha:", d["fecha_txt"]), ("Fecha de entrega:", "27-10-2026"),
             ("Albarán:", d["referencias"]["albaran"]), ("Código de cliente:", "00318")]
    for i, (k, v) in enumerate(filas):
        tx(c, M + 10, yb - 31 - 14 * i, k, TB, 10, INK)
        tx(c, M + 112, yb - 31 - 14 * i, v, TR, 10.5, INK)
    xr = M + wl + 14
    rect(c, xr, yb - hb, W - M - xr, hb, borde=VE, grosor=0.8)
    tx(c, xr + 10, yb - 15, "Datos del cliente:", TB, 10.5, VE)
    for i, (s, f) in enumerate([(de["nombre"], TB), (d["nif_txt"]["destinatario"], TR),
                                ("Avenida de la Alpujarra 6", TR), ("18420 Lanjarón (Granada)", TR),
                                ("Tel.: 958 000 001", TR)]):
        tx(c, xr + 10, yb - 31 - 14 * i, s, f, 10.5, INK)

    y = yb - hb - 24
    xa, xq, xu, xp, xv, xi = M + 6, 330, 338, 422, 468, W - M - 6
    rect(c, M, y - 18, W - 2 * M, 18, relleno=VE)
    for k, x, al in [("Artículo", xa, "l"), ("Cant.", xq, "r"), ("Ud.", xu, "l"), ("Precio", xp, "r"),
                     ("% IVA", xv, "r"), ("Importe", xi, "r")]:
        tx(c, x, y - 13, k, TB, 10, white, al)
    y -= 18
    for i, ln in enumerate(d["lineas"]):
        if i % 2:
            rect(c, M, y - 19, W - 2 * M, 19, relleno=VL)
        yb2 = y - 13.5
        neg = ln["importe"] < 0
        tx(c, xa, yb2, ln["concepto"], TI if neg else TR, 10.5, INK)
        if ln.get("cantidad") is not None:
            tx(c, xq, yb2, ln["cant_txt"], TR, 10.5, INK, "r")
            tx(c, xu, yb2, ln["ud"], TR, 10.5, INK)
            tx(c, xp, yb2, fmt(ln["precio"]), TR, 10.5, INK, "r")
        tx(c, xv, yb2, f"{ln['iva']:.0f}", TR, 10.5, INK, "r")
        tx(c, xi, yb2, fmt(ln["importe"]), TR, 10.5, INK, "r")
        y -= 19
    raya(c, M, y, W - M, y, VE, 1.0)

    y -= 26
    tx(c, M, y, "Desglose por tipo de IVA:", TB, 10.5, VE)
    y -= 8
    x1, x2, x3 = M + 6, M + 160, M + 250
    rect(c, M, y - 16, 256, 16, relleno=HexColor("#DDDDDD"))
    for k, x, al in [("% IVA", x1, "l"), ("Base imponible", x2, "r"), ("Cuota IVA", x3, "r")]:
        tx(c, x, y - 12, k, TB, 9.5, INK, al)
    yy = y - 16
    for g in t["desglose"]:
        yy -= 15
        tx(c, x1, yy + 4, f"{g['tipo']:.0f} %", TR, 10, INK)
        tx(c, x2, yy + 4, fmt(g["base"]), TR, 10, INK, "r")
        tx(c, x3, yy + 4, fmt(g["cuota"]), TR, 10, INK, "r")
    raya(c, M, yy, M + 256, yy, INK, 0.6)
    yy -= 15
    tx(c, x1, yy + 4, "Total", TB, 10, INK)
    tx(c, x2, yy + 4, fmt(t["base"]), TB, 10, INK, "r")
    tx(c, x3, yy + 4, fmt(t["cuota"]), TB, 10, INK, "r")

    xb, wb = W - M - 220, 220
    rect(c, xb, y - 74, wb, 74, borde=VE, grosor=0.8)
    tx(c, xb + 10, y - 18, "Total base imponible:", TR, 10.5, INK)
    tx(c, xb + wb - 10, y - 18, fmt(t["base"]) + " €", TR, 10.5, INK, "r")
    tx(c, xb + 10, y - 36, "Total cuota IVA:", TR, 10.5, INK)
    tx(c, xb + wb - 10, y - 36, fmt(t["cuota"]) + " €", TR, 10.5, INK, "r")
    rect(c, xb, y - 74, wb, 26, relleno=VE)
    tx(c, xb + 10, y - 65, "TOTAL FACTURA:", TB, 12, white)
    tx(c, xb + wb - 10, y - 65, fmt(t["total"]) + " €", TB, 13, white, "r")

    y = yy - 40
    tx(c, M, y, "Forma de pago:", TB, 10.5, INK)
    tx(c, M + 80, y, "Transferencia a 15 días", TR, 10.5, INK)
    tx(c, M, y - 14, "Vencimiento:", TB, 10.5, INK)
    tx(c, M + 80, y - 14, "13-11-2026", TR, 10.5, INK)
    tx(c, M, y - 28, "IBAN:", TB, 10.5, INK)
    tx(c, M + 80, y - 28, d["iban_txt"], TR, 10.5, INK)
    tx(c, M, y - 50, "Observaciones: mercancía entregada en la cámara frigorífica del cliente. Conserve el albarán "
                     "firmado.", TI, 9.5, G)

    raya(c, M, 62, W - M, 62, VE, 0.6)
    tx(c, W / 2, 49, f"{em['nombre']} — Inscrita en el Registro Mercantil de Granada, tomo 1.876, folio 101, "
                     "hoja GR-55120", TR, 8, G, "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 4. Suscripción SaaS: «Facturar a» / «Enviar a», columna de periodo y licencias (US Letter, DejaVu Sans)
# ==============================================================================================
def doc_04() -> dict:
    mes = "01/11/2026 - 30/11/2026"
    lineas = [
        {"concepto": "Plan Profesional (licencia de usuario)", "detalle": "Suscripción mensual · acceso completo",
         "contiene": "Plan Profesional", "periodo": mes, "cantidad": D("25"), "precio": D("18.00"), "cant_txt": "25"},
        {"concepto": "Licencias adicionales prorrateadas", "detalle": "Alta de 4 usuarios el 16/10/2026 (16 días)",
         "contiene": "Licencias adicionales prorrateadas", "periodo": "16/10/2026 - 31/10/2026", "cantidad": D("4"),
         "precio": D("9.00"), "cant_txt": "4"},
        {"concepto": "Módulo de conciliación bancaria", "detalle": "Complemento mensual por empresa",
         "contiene": "Módulo de conciliación bancaria", "periodo": mes, "cantidad": D("1"), "precio": D("59.00"),
         "cant_txt": "1"},
        {"concepto": "Almacenamiento adicional 100 GB", "detalle": "Bloques de 100 GB",
         "contiene": "Almacenamiento adicional 100 GB", "periodo": mes, "cantidad": D("3"), "precio": D("4.50"),
         "cant_txt": "3"},
        {"concepto": "Implantación y formación remota", "detalle": "Sesiones por videoconferencia (horas)",
         "contiene": "Implantación y formación remota", "periodo": "octubre 2026", "cantidad": D("6"),
         "precio": D("55.00"), "cant_txt": "6"},
    ]
    t = calcular(lineas)
    return {
        "archivo": "hold2_04_saas_facturar_enviar_periodo.pdf",
        "estilo": ("Factura de suscripción SaaS al estilo de las plataformas de facturación: logotipo cuadrado con "
                   "marca 'nubaria', título 'Factura' grande a la derecha con bloque de metadatos etiqueta a la "
                   "izquierda / valor alineado a la derecha (Nº de factura, Fecha de factura en formato largo, Fecha "
                   "de vencimiento, ID de cliente, Nº de pedido (PO), Contrato de suscripción, Periodo de servicio), "
                   "dos bloques 'Facturar a' / 'Enviar a' con direcciones distintas, tabla Descripción | Periodo | "
                   "Cant. | Precio unit. | Importe con descripción en negrita y detalle gris, NIF-IVA del emisor con "
                   "prefijo ES, 'Importe adeudado' en banda de color, DejaVu Sans (TrueType), US Letter"),
        "empresa_nif": CIF_NUBARIA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "NB-2026-110482",
        "fecha": date(2026, 11, 1),
        "fecha_txt": "1 de noviembre de 2026",
        "emisor": {"nombre": "Nubaria Software de Gestión, S.L.", "nif": CIF_NUBARIA, "domicilio_contiene": "46024"},
        "destinatario": {"nombre": "Asesores Turianos Albufera, S.L.", "nif": CIF_TURIANOS,
                         "domicilio_contiene": "46005"},
        "nif_txt": {"emisor": f"NIF-IVA: ES{CIF_NUBARIA}", "destinatario": f"NIF: {CIF_TURIANOS}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"pedido": "PO-ATA-2611", "contrato": "SUB-2025-00841", "periodo": mes},
        "iban": "ES0044445555666677774601",
        "iban_txt": "ES00 4444 5555 6666 7777 4601",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "textos": ["Facturar a", "Enviar a", "46988", "Importe adeudado", "16 de noviembre de 2026"],
        "notas": ("Factura propia de la empresa de software sin marcas VERI*FACTU: documento preparatorio. Trampas: "
                  "bloques 'Facturar a' (domicilio fiscal 46005 València, el esperado) y 'Enviar a' (oficina de "
                  "Paterna 46988, distractor) con el mismo nombre de cliente; fecha en formato largo '1 de noviembre "
                  "de 2026' y vencimiento '16 de noviembre de 2026'; columna Periodo con fechas en cada línea (una "
                  "línea prorrateada del mes anterior, 16/10/2026 - 31/10/2026, y otra 'octubre 2026'); el NIF del "
                  "emisor se imprime como NIF-IVA con prefijo ES (ESB…) y sin prefijo en el pie; ID de cliente "
                  "CUS-00841 y BIC como distractores. Referencias: pedido del cliente (PO), contrato de suscripción "
                  "y periodo de servicio del bloque de metadatos. "
                  f"Base {num(t['base'])} x 21 % = 186,585 -> {num(t['cuota'])} (redondeo half-up; half-even daría "
                  f"186,58); total {num(t['total'])}."),
        "render": render_04,
    }


def render_04(ruta: Path, d: dict):
    W, H = letter
    M = 48
    F, FB = FUENTE["DejaVuSans"], FUENTE["DejaVuSans-Bold"]
    AC, G, L, INK = HexColor("#0F766E"), HexColor("#6B7280"), HexColor("#F1F5F9"), HexColor("#111827")
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, letter, f"Factura {d['numero']}", "nubaria")

    rect(c, M, H - 88, 36, 36, relleno=AC, radio=7)
    tx(c, M + 18, H - 78, "n", FB, 22, white, "c")
    tx(c, M + 46, H - 70, "nubaria", FB, 19, INK)
    tx(c, M + 46, H - 83, "software de gestión para despachos", F, 7.5, G)
    filas = [(em["nombre"], FB), ("Carrer de Joan Verdeguer 16, planta 3", F), ("46024 València", F),
             (d["nif_txt"]["emisor"], F), ("facturacion@nubaria.example", F)]
    for i, (s, f) in enumerate(filas):
        tx(c, M, H - 112 - 12 * i, s, f, 8, INK if i == 0 else G)

    tx(c, W - M, H - 76, "Factura", FB, 28, INK, "r")
    meta = [("Nº de factura", d["numero"]), ("Fecha de factura", d["fecha_txt"]),
            ("Fecha de vencimiento", "16 de noviembre de 2026"), ("ID de cliente", "CUS-00841"),
            ("Nº de pedido (PO)", d["referencias"]["pedido"]),
            ("Contrato de suscripción", d["referencias"]["contrato"]),
            ("Periodo de servicio", d["referencias"]["periodo"])]
    for i, (k, v) in enumerate(meta):
        yy = H - 104 - 13.5 * i
        tx(c, W - 298, yy, k, F, 8, G)
        tx(c, W - M, yy, v, FB if i == 0 else F, 8, INK, "r")

    y = H - 222
    bloques = [("Facturar a", [de["nombre"], d["nif_txt"]["destinatario"], "Calle Doctor Sumsi 22, 3.º",
                               "46005 València", "admin@turianos.example"]),
               ("Enviar a", [de["nombre"], "A/A: Departamento de Sistemas", "Calle Ciudad de Sevilla 8, oficina 2",
                             "46988 Paterna (València)"])]
    for i, (tit, lineas) in enumerate(bloques):
        x = M + i * 262
        tx(c, x, y, tit, FB, 9.5, AC)
        raya(c, x, y - 5, x + 230, y - 5, AC, 0.8)
        for j, s in enumerate(lineas):
            tx(c, x, y - 20 - 12.5 * j, s, FB if j == 0 else F, 8.5, INK)

    y = H - 330
    xd, xper, xq, xp, xi = M + 8, 296, 438, 504, W - M - 8
    rect(c, M, y - 20, W - 2 * M, 20, relleno=L)
    for k, x, al in [("Descripción", xd, "l"), ("Periodo", xper, "l"), ("Cant.", xq, "r"), ("Precio unit.", xp, "r"),
                     ("Importe", xi, "r")]:
        tx(c, x, y - 14, k, FB, 8, G, al)
    y -= 20
    for ln in d["lineas"]:
        tx(c, xd, y - 15, ln["concepto"], FB, 8.5, INK)
        tx(c, xd, y - 27, ln["detalle"], F, 7.3, G)
        tx(c, xper, y - 15, ln["periodo"], F, 7.5, INK)
        tx(c, xq, y - 15, ln["cant_txt"], F, 8.5, INK, "r")
        tx(c, xp, y - 15, fmt(ln["precio"]) + " €", F, 8.5, INK, "r")
        tx(c, xi, y - 15, fmt(ln["importe"]) + " €", F, 8.5, INK, "r")
        y -= 36
        raya(c, M, y, W - M, y, HexColor("#E5E7EB"), 0.7)

    xl = 352
    y -= 20
    for k, v in [("Subtotal", t["base"]), ("IVA 21 %", t["cuota"]), ("Total", t["total"])]:
        tx(c, xl, y, k, FB if k == "Total" else F, 8.5, INK)
        tx(c, xi, y, fmt(v) + " €", FB if k == "Total" else F, 8.5, INK, "r")
        y -= 17
    rect(c, xl - 10, y - 12, W - M - xl + 10, 26, relleno=AC, radio=4)
    tx(c, xl, y - 3, "Importe adeudado", FB, 9.5, white)
    tx(c, xi, y - 3, fmt(t["total"]) + " €", FB, 11, white, "r")

    y = 168
    tx(c, M, y, "Forma de pago: Transferencia bancaria", FB, 8.5, INK)
    tx(c, M, y - 14, f"IBAN: {d['iban_txt']}", F, 8.5, INK)
    tx(c, M, y - 28, "BIC: NUBXESV1XXX", F, 8.5, INK)
    tx(c, M, y - 42, "Indique el número de factura en el concepto de la transferencia.", F, 7.5, G)
    tx(c, M, y - 54, "La suscripción se renueva automáticamente cada mes salvo cancelación desde el panel de cliente.",
       F, 7.5, G)
    raya(c, M, 66, W - M, 66, HexColor("#E5E7EB"), 0.7)
    tx(c, M, 53, f"{em['nombre']} · NIF {CIF_NUBARIA} · Inscrita en el Registro Mercantil de Valencia, tomo 10.448, "
                 "folio 12, hoja V-188204", F, 6.5, G)
    tx(c, M, 43, "¿Preguntas sobre esta factura? Escríbanos a facturacion@nubaria.example", F, 6.5, G)
    tx(c, W - M, 43, "Página 1 de 1", F, 6.5, G, "r")
    c.showPage()
    c.save()


# ==============================================================================================
# 5. Fotógrafa autónoma (DNI), IRPF 15 % y «Total a percibir» (Caladea, etiquetas «Etiqueta —»)
# ==============================================================================================
def doc_05() -> dict:
    lineas = [
        {"concepto": "Sesión fotográfica de producto en estudio",
         "detalle": "Jornada completa (8 h) · 21/10/2026 · 32 referencias de cerámica",
         "contiene": "Sesión fotográfica de producto", "cantidad": D("1"), "precio": D("640.00"), "cant_txt": "1"},
        {"concepto": "Retoque avanzado y entrega en alta resolución",
         "detalle": "Imágenes seleccionadas por el cliente (TIFF 16 bits + JPEG web)",
         "contiene": "Retoque avanzado y entrega", "cantidad": D("45"), "precio": D("7.50"), "cant_txt": "45"},
        {"concepto": "Licencia de uso editorial y web (2 años)",
         "detalle": "Catálogo impreso, web y redes sociales; ámbito nacional",
         "contiene": "Licencia de uso editorial", "cantidad": D("1"), "precio": D("280.00"), "cant_txt": "1"},
        {"concepto": "Desplazamiento en vehículo propio",
         "detalle": "Málaga - Castelló - Málaga, 0,26 € por km",
         "contiene": "Desplazamiento en vehículo propio", "cantidad": D("1124"), "precio": D("0.26"),
         "cant_txt": "1.124 km"},
    ]
    t = calcular(lineas, pct_ret="15")
    return {
        "archivo": "hold2_05_fotografa_irpf15_total_percibir.pdf",
        "estilo": ("Factura de fotógrafa autónoma (persona física con DNI), maquetación editorial elegante: nombre "
                   "en versalitas espaciadas centrado con subtítulo en cursiva, título 'FACTURA' muy espaciado, "
                   "bloques 'Emisora —' y 'Cliente —' a dos columnas, etiquetas con raya ('NIF —', 'Factura nº —', "
                   "'Fecha —', 'Proyecto —', 'IBAN —'), tabla sin rejilla con detalle en cursiva bajo cada concepto, "
                   "totales apilados con IRPF 15 % en negativo y 'Total a percibir' en lugar de 'Total a pagar', "
                   "fuente Caladea (TrueType), A4"),
        "empresa_nif": NIF_FOTOGRAFA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "NC-031/26",
        "fecha": date(2026, 10, 27),
        "fecha_txt": "27/10/2026",
        "emisor": {"nombre": "Nerea Cifuentes Albaladejo", "nif": NIF_FOTOGRAFA, "domicilio_contiene": "29012"},
        "destinatario": {"nombre": "Comunicación Costa Azahar Ediciones, S.L.", "nif": CIF_AZAHAR,
                         "domicilio_contiene": "12003"},
        "nif_txt": {"emisor": f"NIF — {NIF_FOTOGRAFA}", "destinatario": f"CIF — {CIF_AZAHAR}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"proyecto": "CCP-27-04"},
        "iban": "ES0055556666777788882901",
        "iban_txt": "ES00 5555 6666 7777 8888 2901",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "textos": ["Total a percibir", "Retención IRPF 15 %", "Emisora —", "1.124 km"],
        "notas": ("Factura propia de una fotógrafa autónoma (persona física, DNI) sin marcas VERI*FACTU: documento "
                  "preparatorio. Trampas: el nombre comercial de la cabecera ('NEREA CIFUENTES', espaciado) es "
                  "parcial; el nombre completo y el NIF están en el bloque 'Emisora —'; etiquetas separadas del valor "
                  "por una raya larga (—); cantidad con punto de miles y unidad ('1.124 km') con precio 0,26; "
                  "detalle de cada línea en cursiva con fechas y números (21/10/2026, 8 h, 32 referencias, 16 bits); "
                  "la retención se rotula 'Retención IRPF 15 %' con importe negativo y el líquido se llama 'Total a "
                  f"percibir'. Base {num(t['base'])} x 21 % = 325,4454 -> {num(t['cuota'])}; total factura "
                  f"{num(t['total'])}; IRPF 15 % = 232,461 -> {num(t['ret'])}; total a percibir {num(t['pagar'])}."),
        "render": render_05,
    }


def render_05(ruta: Path, d: dict):
    W, H = A4
    M = 64
    F, FB, FI = FUENTE["Caladea"], FUENTE["Caladea-Bold"], FUENTE["Caladea-Italic"]
    INK, G, AC = HexColor("#262626"), HexColor("#7A7A7A"), HexColor("#9C7A3C")
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", em["nombre"])

    tx(c, W / 2, H - 72, "NEREA CIFUENTES", F, 25, INK, "c", cs=2.5)
    tx(c, W / 2, H - 91, "fotografía de producto y editorial", FI, 11, AC, "c")
    raya(c, W / 2 - 60, H - 104, W / 2 + 60, H - 104, AC, 0.7)
    tx(c, W / 2, H - 140, "FACTURA", F, 13, INK, "c", cs=7)

    y = H - 186
    tx(c, M, y, "Emisora —", FI, 10, G)
    for i, (s, f) in enumerate([(em["nombre"], FB), (d["nif_txt"]["emisor"], F),
                                ("Calle Compás de la Victoria 7, 2.º B", F), ("29012 Málaga", F),
                                ("Tel. — 600 000 000", F)]):
        tx(c, M, y - 17 - 14 * i, s, f, 11, INK)
    xr = W / 2 + 14
    tx(c, xr, y, "Cliente —", FI, 10, G)
    for i, (s, f) in enumerate([(de["nombre"], FB), (d["nif_txt"]["destinatario"], F),
                                ("Avenida del Mar 41, entresuelo", F), ("12003 Castelló de la Plana", F)]):
        tx(c, xr, y - 17 - 14 * i, s, f, 11, INK)

    y = H - 300
    raya(c, M, y + 16, W - M, y + 16, AC, 0.5)
    meta = [("Factura nº —", d["numero"]), ("Fecha —", d["fecha_txt"]), ("Proyecto —", d["referencias"]["proyecto"])]
    paso = (W - 2 * M) / 3
    for i, (k, v) in enumerate(meta):
        x = M + i * paso
        tx(c, x, y, k, FI, 10, G)
        tx(c, x + ancho(k, FI, 10) + 5, y, v, F, 11.5, INK)
    raya(c, M, y - 10, W - M, y - 10, AC, 0.5)

    y = H - 344
    xq, xp, xi = 384, 458, W - M
    for k, x, al in [("Concepto", M, "l"), ("Cantidad", xq, "r"), ("Precio", xp, "r"), ("Importe", xi, "r")]:
        tx(c, x, y, k, FI, 10, G, al)
    raya(c, M, y - 7, W - M, y - 7, INK, 0.4)
    y -= 7
    for ln in d["lineas"]:
        tx(c, M, y - 20, ln["concepto"], F, 11.5, INK)
        tx(c, M, y - 33, ln["detalle"], FI, 9, G)
        tx(c, xq, y - 20, ln["cant_txt"], F, 11.5, INK, "r")
        tx(c, xp, y - 20, fmt(ln["precio"]), F, 11.5, INK, "r")
        tx(c, xi, y - 20, fmt(ln["importe"]) + " €", F, 11.5, INK, "r")
        y -= 46
    raya(c, M, y, W - M, y, INK, 0.4)

    xl = 318
    y -= 24
    filas = [("Base imponible", fmt(t["base"]) + " €"), ("IVA 21 %", fmt(t["cuota"]) + " €"),
             ("Total factura", fmt(t["total"]) + " €"), ("Retención IRPF 15 %", "-" + fmt(t["ret"]) + " €")]
    for k, v in filas:
        tx(c, xl, y, k, F, 11, INK)
        tx(c, xi, y, v, F, 11, INK, "r")
        y -= 17
    raya(c, xl, y + 7, W - M, y + 7, AC, 0.8)
    tx(c, xl, y - 10, "Total a percibir", FB, 13.5, INK)
    tx(c, xi, y - 10, fmt(t["pagar"]) + " €", FB, 13.5, INK, "r")

    y = 170
    tx(c, M, y, "Forma de pago —", FI, 10, G)
    tx(c, M + ancho("Forma de pago —", FI, 10) + 5, y, "Transferencia bancaria a 15 días", F, 11, INK)
    tx(c, M, y - 16, "IBAN —", FI, 10, G)
    tx(c, M + ancho("IBAN —", FI, 10) + 5, y - 16, d["iban_txt"], F, 11, INK)
    yy = y - 40
    for s in simpleSplit("Las imágenes se entregan mediante enlace de descarga privado. La licencia de uso queda "
                         "condicionada al pago íntegro de la presente factura.", FI, 9.5, W - 2 * M):
        tx(c, M, yy, s, FI, 9.5, G)
        yy -= 12
    tx(c, W / 2, 50, "nerea@cifuentesfoto.example · cifuentesfoto.example · Málaga", F, 8.5, G, "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 6. RECIBIDA: comercializadora eléctrica (resumen, detalle de cálculo, lecturas, histórico; 2 páginas)
# ==============================================================================================
PEAJES_06 = D("91.80")   # importe informativo de peajes y cargos (no es una línea)
HISTORICO_06 = [("oct-25", 1712), ("nov-25", 1655), ("dic-25", 1890), ("ene-26", 2040), ("feb-26", 1820),
                ("mar-26", 1704), ("abr-26", 1588), ("may-26", 1490), ("jun-26", 1532), ("jul-26", 1602),
                ("ago-26", 1210), ("sep-26", 1585), ("oct-26", 1640)]


def doc_06() -> dict:
    dias, kw = D("31"), D("9.90")
    p1, p2 = D("0.086301"), D("0.009129")
    e1, e2, e3 = (D("412"), D("0.187420")), (D("538"), D("0.141360")), (D("690"), D("0.112050"))
    bono, alq, iee_pct = D("0.038455"), D("0.026630"), D("5.11269632")
    lineas = [
        {"concepto": "Término de potencia P1", "calculo": f"{num(kw)} kW x 31 días x {num(p1, nd=6)} €/kW día",
         "importe": q2(kw * dias * p1)},
        {"concepto": "Término de potencia P2", "calculo": f"{num(kw)} kW x 31 días x {num(p2, nd=6)} €/kW día",
         "importe": q2(kw * dias * p2)},
        {"concepto": "Término de energía P1 (punta)", "calculo": f"{e1[0]} kWh x {num(e1[1], nd=6)} €/kWh",
         "importe": q2(e1[0] * e1[1])},
        {"concepto": "Término de energía P2 (llano)", "calculo": f"{e2[0]} kWh x {num(e2[1], nd=6)} €/kWh",
         "importe": q2(e2[0] * e2[1])},
        {"concepto": "Término de energía P3 (valle)", "calculo": f"{e3[0]} kWh x {num(e3[1], nd=6)} €/kWh",
         "importe": q2(e3[0] * e3[1])},
        {"concepto": "Financiación del bono social", "calculo": f"31 días x {num(bono, nd=6)} €/día",
         "importe": q2(dias * bono)},
    ]
    sub_iee = sum((ln["importe"] for ln in lineas), D("0"))
    lineas.append({"concepto": "Impuesto sobre la electricidad",
                   "calculo": f"{num(iee_pct, nd=8)} % s/ {num(sub_iee)} €", "importe": q2(sub_iee * iee_pct / 100)})
    lineas.append({"concepto": "Alquiler del equipo de medida", "calculo": f"31 días x {num(alq, nd=6)} €/día",
                   "importe": q2(dias * alq)})
    for ln, cont in zip(lineas, ["Término de potencia P1", "Término de potencia P2", "Término de energía P1",
                                 "Término de energía P2", "Término de energía P3", "Financiación del bono social",
                                 "Impuesto sobre la electricidad", "Alquiler del equipo de medida"]):
        ln["contiene"] = cont
    t = calcular(lineas)
    pot = lineas[0]["importe"] + lineas[1]["importe"]
    ene = sum((ln["importe"] for ln in lineas[2:5]), D("0"))
    return {
        "archivo": "hold2_06_recibida_suministro_electrico.pdf",
        "estilo": ("Factura RECIBIDA de una comercializadora eléctrica (2 páginas): marca 'valdeluz energía' en "
                   "cabecera, caja 'Datos de la factura' y caja verde 'Importe total de la factura' grande, bloque "
                   "'Titular del contrato' con dirección fiscal y dirección de suministro distintas y CUPS, 'Resumen "
                   "de la factura' por conceptos agregados con puntos guía, barra '¿A qué se destina el importe?' "
                   "con importes y porcentajes, gráfico de barras de 13 meses de consumo; en pág. 2 'Detalle de la "
                   "factura' Concepto | Cálculo | Importe con precios de 6 decimales, tabla de lecturas, maxímetro, "
                   "datos del contrato y domiciliación con cuenta enmascarada, CIF del emisor solo en el pie, "
                   "Helvetica, A4"),
        "empresa_nif": CIF_TRIGAL,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "FE26-0338271",
        "fecha": date(2026, 11, 5),
        "fecha_txt": "05/11/2026",
        "emisor": {"nombre": "Valdeluz Energía Comercializadora, S.A.", "nif": CIF_VALDELUZ,
                   "domicilio_contiene": "28050"},
        "destinatario": {"nombre": "Obrador Panadero Trigal de Aranda, S.L.", "nif": CIF_TRIGAL,
                         "domicilio_contiene": "09400"},
        "nif_txt": {"emisor": f"C.I.F. {CIF_VALDELUZ}", "destinatario": f"NIF: {CIF_TRIGAL}"},
        "lineas": lineas,
        "t": t,
        "pot": pot,
        "ene": ene,
        "referencias": {"contrato": "VLZ-2041-778120", "periodo": "01/10/2026 - 31/10/2026"},
        "iban": None,
        "iban_txt": "ES00 **** **** **** **** 7788",
        "forma_pago_contiene": "Domiciliación bancaria",
        "indicios": [],
        "fmt": num,
        "textos": ["Resumen de la factura", "Detalle de la factura", "CUPS: ES0999000011112222XY0F",
                   f"Por potencia contratada", num(pot), num(ene), "Dirección de suministro", "09471"],
        "notas": ("La sube la empresa DESTINATARIA (obrador, titular del contrato): factura recibida "
                  "(empresa_nif = destinatario). Las líneas son las 8 filas del 'Detalle de la factura' (pág. 2) "
                  "cuyo cálculo combina potencia x días x precio o kWh x precio con 6 decimales: por eso cantidad y "
                  "precio son null. El Impuesto sobre la electricidad y el alquiler del equipo forman parte de la "
                  "base del IVA (21 %). Números que NO son líneas: resumen agregado (potencia "
                  f"{num(pot)}, energía {num(ene)}), base del impuesto eléctrico ({num(sub_iee)}), destino del "
                  f"importe (peajes {num(PEAJES_06)}, impuestos, energía y comercialización, porcentajes), consumo "
                  "1.640 kWh, medias diarias (52,90 kWh y "
                  f"{num(q2(t['total'] / 31))} €), lecturas de contador, histórico de 13 meses, maxímetro "
                  "(8,74 / 5,12 kW), potencias contratadas 9,90 kW, mix de origen y emisiones. Fechas distractoras: "
                  "periodo 01/10/2026 - 31/10/2026, fecha de cargo 20/11/2026, lecturas 30/09/2026 y 31/10/2026, "
                  "alta 14/02/2024. Dos direcciones del titular: fiscal (09400 Aranda de Duero, la esperada) y de "
                  "suministro (09471 Fuentespina). La cuenta de domiciliación está enmascarada: iban null. El CIF del "
                  f"emisor solo aparece en el pie. Base {num(t['base'])} x 21 % = 57,8004 -> {num(t['cuota'])}; "
                  f"total {num(t['total'])}."),
        "render": render_06,
    }


def render_06(ruta: Path, d: dict):
    W, H = A4
    M = 38
    HR, HB = "Helvetica", "Helvetica-Bold"
    VE, VL, G, INK = HexColor("#1E7F4F"), HexColor("#E8F4EC"), HexColor("#5F6368"), HexColor("#1B1B1B")
    em, de, t, fmt, L = d["emisor"], d["destinatario"], d["t"], d["fmt"], d["lineas"]
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "valdeluz")
    ref = d["referencias"]

    def cabecera():
        tx(c, M, H - 54, "valdeluz", HB, 26, VE)
        tx(c, M + ancho("valdeluz", HB, 26) + 7, H - 54, "energía", HR, 26, G)
        tx(c, M, H - 68, em["nombre"], HR, 7.5, G)
        tx(c, W - M, H - 44, "FACTURA DE ELECTRICIDAD", HB, 11, INK, "r")
        tx(c, W - M, H - 58, f"Nº factura: {d['numero']}", HR, 8.5, INK, "r")
        tx(c, W - M, H - 70, f"Fecha de emisión: {d['fecha_txt']}", HR, 8.5, INK, "r")
        raya(c, M, H - 80, W - M, H - 80, VE, 1.2)

    def pie(pag):
        raya(c, M, 60, W - M, 60, G, 0.4)
        tx(c, M, 48, f"{em['nombre']} · {d['nif_txt']['emisor']} · Avenida de los Prados Altos 112, 28050 Madrid",
           HR, 6.8, G)
        tx(c, M, 39, "Inscrita en el Registro Mercantil de Madrid, tomo 40.112, folio 1, hoja M-700001 · Atención al "
                     "cliente 900 000 000 · clientes@valdeluz.example", HR, 6.8, G)
        tx(c, W - M, 26, f"Página {pag} de 2", HR, 7, G, "r")

    # ---------------- página 1
    cabecera()
    y0, h = H - 206, 112
    rect(c, M, y0, 272, h, relleno=VL, radio=6)
    tx(c, M + 12, y0 + h - 18, "Datos de la factura", HB, 10, VE)
    filas = [("Nº de factura", d["numero"]), ("Fecha de emisión", d["fecha_txt"]),
             ("Periodo de facturación", ref["periodo"]), ("Días facturados", "31"),
             ("Nº de contrato", ref["contrato"]), ("Fecha de cargo", "20/11/2026")]
    for i, (k, v) in enumerate(filas):
        yy = y0 + h - 36 - 13 * i
        tx(c, M + 12, yy, k + ":", HR, 8.5, G)
        tx(c, M + 260, yy, v, HB, 8.5, INK, "r")
    xb = M + 288
    wb = W - M - xb
    rect(c, xb, y0, wb, h, relleno=VE, radio=6)
    tx(c, xb + 14, y0 + h - 22, "Importe total de la factura", HR, 10, white)
    tx(c, xb + wb - 14, y0 + h - 66, fmt(t["total"]) + " €", HB, 31, white, "r")
    tx(c, xb + 14, y0 + 16, "Se cargará en su cuenta el 20/11/2026", HR, 8, white)

    y = y0 - 26
    tx(c, M, y, "Titular del contrato", HB, 10, VE)
    for i, (s, f) in enumerate([
            (de["nombre"], HB), (d["nif_txt"]["destinatario"], HR),
            ("Dirección fiscal: Calle Isilla 14, 2.º, 09400 Aranda de Duero (Burgos)", HR),
            ("Dirección de suministro: Camino de la Vega s/n, nave 3, 09471 Fuentespina (Burgos)", HR),
            ("CUPS: ES0999000011112222XY0F", HR),
            ("Producto: Tarifa Negocio Tres Periodos · Peaje de acceso 2.0TD · Potencia contratada P1 9,90 kW / "
             "P2 9,90 kW", HR)]):
        tx(c, M, y - 15 - 12.5 * i, s, f, 8.5 if i else 9.5, INK)

    y = y - 112
    tx(c, M, y, "Resumen de la factura", HB, 10, VE)
    resumen = [("Por potencia contratada", d["pot"]), ("Por energía consumida", d["ene"]),
               ("Financiación del bono social", L[5]["importe"]), ("Impuesto sobre la electricidad", L[6]["importe"]),
               ("Alquiler de equipos de medida y control", L[7]["importe"]), ("IVA (21 %)", t["cuota"])]
    xv = M + 268
    yy = y - 17
    for k, v in resumen:
        s = fmt(v) + " €"
        tx(c, M, yy, k, HR, 8.8, INK)
        puntos(c, M + ancho(k, HR, 8.8) + 4, xv - ancho(s, HR, 8.8) - 4, yy, HR, 8.8, G)
        tx(c, xv, yy, s, HR, 8.8, INK, "r")
        yy -= 14
    raya(c, M, yy + 8, xv, yy + 8, INK, 0.6)
    tx(c, M, yy - 5, "TOTAL IMPORTE FACTURA", HB, 9.5, INK)
    tx(c, xv, yy - 5, fmt(t["total"]) + " €", HB, 9.5, INK, "r")

    xd = M + 300
    wd = W - M - xd
    tx(c, xd, y, "¿A qué se destina el importe de su factura?", HB, 9, VE)
    impuestos = L[6]["importe"] + t["cuota"]
    energia = t["total"] - impuestos - PEAJES_06
    cats = [("Energía y comercialización", energia, HexColor("#1E7F4F")),
            ("Peajes y cargos regulados", PEAJES_06, HexColor("#7FBF98")),
            ("Impuestos", impuestos, HexColor("#C9E4D3"))]
    xx = xd
    for _, v, col in cats:
        w = float(v / t["total"]) * wd
        rect(c, xx, y - 34, w, 14, relleno=col)
        xx += w
    for i, (k, v, col) in enumerate(cats):
        yy2 = y - 52 - 14 * i
        rect(c, xd, yy2 - 1, 7, 7, relleno=col)
        pct = (v / t["total"] * 100).quantize(D("0.1"), rounding=ROUND_HALF_UP)
        tx(c, xd + 12, yy2, f"{k}: {fmt(v)} € ({num(pct, nd=1)} %)", HR, 8, INK)

    y = yy - 46
    tx(c, M, y, "Evolución de su consumo (kWh)", HB, 10, VE)
    base_y, alto, xx = y - 120, 90, M + 6
    vmax = max(v for _, v in HISTORICO_06)
    paso = (W - 2 * M - 12) / len(HISTORICO_06)
    raya(c, M, base_y, W - M, base_y, G, 0.5)
    for i, (mes, v) in enumerate(HISTORICO_06):
        hh = alto * v / vmax
        x = xx + i * paso + 6
        rect(c, x, base_y, paso - 12, hh, relleno=VE if i == len(HISTORICO_06) - 1 else HexColor("#A8D5BA"))
        tx(c, x + (paso - 12) / 2, base_y + hh + 3, num(v, nd=0), HR, 6.5, INK, "c")
        tx(c, x + (paso - 12) / 2, base_y - 10, mes, HR, 6.5, G, "c")
    tx(c, M, base_y - 30, f"Consumo en este periodo: 1.640 kWh · Consumo medio diario: 52,90 kWh · Coste medio "
                          f"diario: {fmt(q2(t['total'] / 31))} €", HR, 8.5, INK)
    pie(1)
    c.showPage()

    # ---------------- página 2
    cabecera()
    y = H - 112
    tx(c, M, y, "Detalle de la factura", HB, 12, VE)
    y -= 14
    xc, xi = M + 196, W - M - 8
    rect(c, M, y - 18, W - 2 * M, 18, relleno=VL)
    for k, x, al in [("Concepto", M + 8, "l"), ("Cálculo", xc, "l"), ("Importe", xi, "r")]:
        tx(c, x, y - 12.5, k, HB, 8.5, INK, al)
    y -= 18
    for ln in L:
        y -= 16
        tx(c, M + 8, y + 4, ln["concepto"], HR, 9, INK)
        tx(c, xc, y + 4, ln["calculo"], HR, 8.5, G)
        tx(c, xi, y + 4, fmt(ln["importe"]) + " €", HR, 9, INK, "r")
    raya(c, M, y - 4, W - M, y - 4, INK, 0.6)
    for k, calc, v, f in [("Base imponible", "", t["base"], HB), ("IVA 21 %", f"21 % s/ {fmt(t['base'])} €",
                                                                     t["cuota"], HR),
                          ("Total factura", "", t["total"], HB)]:
        y -= 16
        tx(c, M + 8, y, k, f, 9, INK)
        tx(c, xc, y, calc, HR, 8.5, G)
        tx(c, xi, y, fmt(v) + " €", f, 9, INK, "r")

    y -= 40
    tx(c, M, y, "Lecturas y consumos", HB, 10, VE)
    y -= 8
    cols = [("Periodo horario", M + 8, "l"), ("Lectura anterior (30/09/2026)", M + 250, "r"),
            ("Lectura actual (31/10/2026)", M + 390, "r"), ("Consumo (kWh)", W - M - 8, "r")]
    rect(c, M, y - 16, W - 2 * M, 16, relleno=VL)
    for k, x, al in cols:
        tx(c, x, y - 11.5, k, HB, 8, INK, al)
    y -= 16
    for fila in [("P1 (punta)", 6210, 6622), ("P2 (llano)", 7904, 8442), ("P3 (valle)", 4328, 5018)]:
        y -= 14
        vals = [fila[0], num(fila[1], nd=0), num(fila[2], nd=0), num(fila[2] - fila[1], nd=0)]
        for (k, x, al), v in zip(cols, vals):
            tx(c, x, y + 3, v, HR, 8.5, INK, al)
    y -= 14
    tx(c, M + 8, y + 3, "Total", HB, 8.5, INK)
    tx(c, W - M - 8, y + 3, "1.640", HB, 8.5, INK, "r")
    tx(c, M, y - 14, "Tipo de lectura: real (telegestión) · Nº de contador: CNT-0000-4521 · Potencia máxima "
                     "demandada: P1 8,74 kW · P2 5,12 kW", HR, 8, G)

    y -= 50
    tx(c, M, y, "Datos del contrato y del pago", HB, 10, VE)
    for i, s in enumerate(["Forma de pago: Domiciliación bancaria",
                           f"Cuenta de cargo: {d['iban_txt']} · Referencia del mandato SEPA: VLZ-MND-778120",
                           "Fecha de alta del contrato: 14/02/2024 · Fin de contrato: 13/02/2027 (renovación "
                           "automática)",
                           "Potencias contratadas: P1 9,90 kW · P2 9,90 kW · Peaje de acceso 2.0TD",
                           "Empresa distribuidora: Redes Eléctricas del Alto Duero, S.A.U. · Averías 24 h: "
                           "900 000 000"]):
        tx(c, M, y - 15 - 12.5 * i, s, HR, 8.5, INK)

    y -= 100
    tx(c, M, y, "Origen de la electricidad e impacto ambiental", HB, 10, VE)
    tx(c, M, y - 15, "Origen de la electricidad comercializada: renovable 54,2 % · nuclear 19,3 % · ciclo combinado "
                     "18,7 % · cogeneración 6,1 % · otras 1,7 %", HR, 8.5, INK)
    tx(c, M, y - 28, "Emisiones de dióxido de carbono: 0,11 kg/kWh (clase C, en una escala de A a G)", HR, 8.5, INK)
    pie(2)
    c.showPage()
    c.save()


# ==============================================================================================
# 7. Apaisada: 8 columnas con descripciones multilínea, lote, Dto. % e IVA % por línea
# ==============================================================================================
DESC7 = ("Helvetica", 8.5, 300.0)   # fuente, tamaño y ancho útil de la columna Descripción


def doc_07() -> dict:
    datos = [
        ("SEM-TOM-0412", "Semilla de tomate híbrido tipo pera de crecimiento indeterminado, sobre de 1.000 semillas "
                         "peletizadas y tratadas, apta para invernadero y aire libre", "Semilla de tomate híbrido",
         "L26-0871", "12", "38.50", "5", "10"),
        ("PLA-PIM-0096", "Plantón de pimiento italiano injertado sobre patrón vigoroso, bandeja de 96 unidades con "
                         "cepellón, pasaporte fitosanitario incluido", "Plantón de pimiento italiano",
         "P2610-14", "25", "21.60", "0", "10"),
        ("SUS-COC-0070", "Sustrato de fibra de coco prensado en saco de 70 litros, granulometría media, lavado y "
                         "tamponado, conductividad eléctrica inferior a 0,5 mS/cm", "Sustrato de fibra de coco",
         "C2610-33", "40", "9.75", "10", "21"),
        ("BAN-ALV-0104", "Bandeja de alveolos de poliestireno expandido de 104 huecos, reutilizable, medidas "
                         "60 x 40 cm", "Bandeja de alveolos de poliestireno", "B-5521", "150", "1.18", "0", "21"),
        ("FER-NPK-1515", "Fertilizante hidrosoluble NPK 15-15-15 con microelementos quelatados, saco de 25 kg, "
                         "apto para fertirrigación", "Fertilizante hidrosoluble NPK", "F26-1902", "8", "46.20",
         "7.5", "21"),
        ("RIE-GOT-1633", "Tubería de riego por goteo de 16 mm con gotero integrado autocompensante de 2 L/h cada "
                         "33 cm, rollo de 500 m", "Tubería de riego por goteo", "R-7741", "3", "112.40", "0", "21"),
    ]
    lineas = []
    for cod, desc, cont, lote, cant, precio, dto, iva in datos:
        partes = simpleSplit(desc, *DESC7)
        lineas.append({"codigo": cod, "concepto": desc, "partes": partes, "primera": partes[0], "contiene": cont,
                       "lote": lote, "cantidad": D(cant), "precio": D(precio), "dto": D(dto), "iva": D(iva),
                       "cant_txt": cant})
    t = calcular(lineas)
    bruto = sum((q2(ln["cantidad"] * ln["precio"]) for ln in lineas), D("0"))
    g10, g21 = t["desglose"]
    return {
        "archivo": "hold2_07_apaisada_8_columnas_lotes.pdf",
        "estilo": ("Factura APAISADA (A4 horizontal) de una cooperativa de suministros agrarios: razón social en "
                   "mayúsculas arriba a la izquierda, 'FACTURA' y metadatos con separador ' - ' alineados a la "
                   "derecha (Nº factura - / Fecha - / Su pedido - / Albarán - / Vencimiento -), cajas 'DESTINATARIO' y "
                   "'DIRECCIÓN DE ENTREGA', tabla con rejilla completa de 8 columnas Código | Descripción | Lote | "
                   "Uds. | Precio ud. | Dto. % | IVA % | Importe con descripciones largas de 2-3 líneas, cuadro de "
                   "IVA por tipo (10 % y 21 %), suma bruta y descuentos como distractores, caja de totales, "
                   "fecha dd.mm.aaaa, Helvetica 8,5 pt"),
        "empresa_nif": CIF_TIERRA_BARROS,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "TB/26/02871",
        "fecha": date(2026, 10, 30),
        "fecha_txt": "30.10.2026",
        "emisor": {"nombre": "Suministros Agrarios Tierra de Barros, S. Coop.", "nif": CIF_TIERRA_BARROS,
                   "domicilio_contiene": "06200"},
        "destinatario": {"nombre": "Viveros Hermanos Zurbarán Lobón, S.L.", "nif": CIF_ZURBARAN,
                         "domicilio_contiene": "06498"},
        "nif_txt": {"emisor": f"CIF {CIF_TIERRA_BARROS}", "destinatario": f"CIF: {CIF_ZURBARAN}"},
        "lineas": lineas,
        "t": t,
        "bruto": bruto,
        "referencias": {"pedido": "PED-VZ-0458", "albaran": "ALB-26-3312"},
        "iban": "ES0066667777888899990601",
        "iban_txt": "ES00 6666 7777 8888 9999 0601",
        "forma_pago_contiene": "Transferencia bancaria a 60 días",
        "indicios": ["descuento"],
        "fmt": num,
        "textos": ["Dto. %", "IVA %", "Lote", "L26-0871", "06480", num(bruto), "-" + num(bruto - t["base"])],
        "notas": ("Factura propia de la cooperativa sin marcas VERI*FACTU: documento preparatorio. Página apaisada "
                  "con 8 columnas y descripciones largas que ocupan 2-3 renglones dentro de la celda (concepto_"
                  "contiene = comienzo del primer renglón); columna de lote alfanumérico (L26-0871...) y de código "
                  "(SEM-TOM-0412...) junto a la descripción; descripciones con números (1.000 semillas, 0,5 mS/cm, "
                  "15-15-15, 60 x 40 cm, 2 L/h). Columna Dto. % en tres líneas (5,00 / 10,00 / 7,50): el precio "
                  "esperado es el unitario sin descuento y el importe es neto (12 x 38,50 = 462,00 - 5 % = 438,90; "
                  "40 x 9,75 = 390,00 - 10 % = 351,00; 8 x 46,20 = 369,60 - 7,5 % = 341,88). Dos tipos de IVA por "
                  f"línea (10 % semillas y plantones; 21 % resto). Distractores: suma bruta {num(bruto)} y "
                  f"descuentos -{num(bruto - t['base'])}, dirección de entrega en otra localidad (06480 Montijo), "
                  "bultos y peso, vencimiento 29.12.2026. "
                  f"10 %: {num(g10['base'])} -> {num(g10['cuota'])}; 21 %: {num(g21['base'])} x 21 % = 253,4868 -> "
                  f"{num(g21['cuota'])}; base {num(t['base'])}, IVA {num(t['cuota'])}, total {num(t['total'])}."),
        "render": render_07,
    }


def render_07(ruta: Path, d: dict):
    W, H = landscape(A4)
    M = 40
    HR, HB = "Helvetica", "Helvetica-Bold"
    DK, LG, G, INK = HexColor("#23412B"), HexColor("#EEF3EE"), HexColor("#5A5A5A"), black
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, landscape(A4), f"Factura {d['numero']}", em["nombre"])
    ref = d["referencias"]

    tx(c, M, H - 46, em["nombre"].upper(), HB, 13, DK)
    tx(c, M, H - 60, "Carretera de Villafranca, km 1,2 · 06200 Almendralejo (Badajoz)", HR, 8.5, G)
    tx(c, M, H - 71, f"{d['nif_txt']['emisor']} · Tel. 924 000 000 · ventas@tierradebarros.example", HR, 8.5, G)
    tx(c, M, H - 82, "Inscrita en el Registro de Sociedades Cooperativas de Extremadura con el n.º BA-0000-SC",
       HR, 7.5, G)
    tx(c, W - M, H - 48, "FACTURA", HB, 22, DK, "r")
    meta = [("Nº factura", d["numero"]), ("Fecha", d["fecha_txt"]), ("Su pedido", ref["pedido"]),
            ("Albarán", ref["albaran"]), ("Vencimiento", "29.12.2026")]
    for i, (k, v) in enumerate(meta):
        tx(c, W - M, H - 62 - 10.5 * i, f"{k} - {v}", HB if i == 0 else HR, 8.5, INK, "r")

    yb, hb = H - 118, 66
    wb = 380
    rect(c, M, yb - hb, wb, hb, borde=DK, grosor=0.8)
    tx(c, M + 8, yb - 12, "DESTINATARIO", HB, 7.5, DK)
    for i, (s, f) in enumerate([(de["nombre"], HB), (d["nif_txt"]["destinatario"], HR),
                                ("Calle Huertas Bajas 9", HR), ("06498 Lobón (Badajoz)", HR)]):
        tx(c, M + 8, yb - 25 - 11 * i, s, f, 8.8, INK)
    x2 = M + wb + 16
    rect(c, x2, yb - hb, W - M - x2, hb, borde=DK, grosor=0.8)
    tx(c, x2 + 8, yb - 12, "DIRECCIÓN DE ENTREGA", HB, 7.5, DK)
    for i, s in enumerate(["Finca El Caño, polígono 7, parcela 112", "06480 Montijo (Badajoz)",
                           "Transporte: medios propios · Bultos: 9 · Peso: 1.245 kg"]):
        tx(c, x2 + 8, yb - 25 - 11 * i, s, HR, 8.8, INK)

    bordes = [M, 108, 416, 476, 522, 590, 638, 684, W - M]
    cab = [("Código", "l"), ("Descripción", "l"), ("Lote", "l"), ("Uds.", "r"), ("Precio ud.", "r"),
           ("Dto. %", "r"), ("IVA %", "r"), ("Importe", "r")]

    def xcol(i, al):
        return bordes[i] + 5 if al == "l" else bordes[i + 1] - 5

    ytop = yb - hb - 14
    rect(c, M, ytop - 17, W - 2 * M, 17, relleno=DK)
    for i, (k, al) in enumerate(cab):
        tx(c, xcol(i, al), ytop - 12, k, HB, 8, white, al)
    y = ytop - 17
    for ln in d["lineas"]:
        alto = 10.5 * len(ln["partes"]) + 7
        yt = y - 11
        tx(c, xcol(0, "l"), yt, ln["codigo"], HR, 8, INK)
        for j, s in enumerate(ln["partes"]):
            tx(c, xcol(1, "l"), yt - 10.5 * j, s, DESC7[0], DESC7[1], INK)
        vals = [ln["lote"], ln["cant_txt"], fmt(ln["precio"]), fmt(ln["dto"]) if ln["dto"] else "",
                f"{ln['iva']:.0f}", fmt(ln["importe"])]
        for i, v in enumerate(vals, start=2):
            tx(c, xcol(i, cab[i][1]), yt, v, HR, 8.5, INK, cab[i][1])
        y -= alto
        raya(c, M, y, W - M, y, G, 0.4)
    c.setStrokeColor(G)
    c.setLineWidth(0.4)
    for x in bordes:
        c.line(x, ytop, x, y)
    raya(c, M, ytop, W - M, ytop, DK, 0.8)

    ys = y - 22
    tx(c, M, ys, "Cuadro de IVA", HB, 8.5, DK)
    x1, x2b, x3 = M + 6, M + 150, M + 240
    rect(c, M, ys - 19, 250, 13, relleno=LG)
    for k, x, al in [("Tipo", x1, "l"), ("Base imponible", x2b, "r"), ("Cuota", x3, "r")]:
        tx(c, x, ys - 15, k, HB, 8, INK, al)
    yy = ys - 19
    for g in t["desglose"]:
        yy -= 12
        tx(c, x1, yy + 3, f"{g['tipo']:.0f} %", HR, 8.5, INK)
        tx(c, x2b, yy + 3, fmt(g["base"]), HR, 8.5, INK, "r")
        tx(c, x3, yy + 3, fmt(g["cuota"]), HR, 8.5, INK, "r")
    raya(c, M, yy - 1, M + 250, yy - 1, G, 0.4)
    yy -= 13
    tx(c, x1, yy + 3, "Total", HB, 8.5, INK)
    tx(c, x2b, yy + 3, fmt(t["base"]), HB, 8.5, INK, "r")
    tx(c, x3, yy + 3, fmt(t["cuota"]), HB, 8.5, INK, "r")

    xm = M + 290
    tx(c, xm, ys - 15, f"Suma bruta: {fmt(d['bruto'])} €", HR, 8.5, INK)
    tx(c, xm, ys - 28, f"Descuentos: -{fmt(d['bruto'] - t['base'])} €", HR, 8.5, INK)
    tx(c, xm, ys - 48, "Forma de pago - Transferencia bancaria a 60 días", HB, 8.5, INK)
    tx(c, xm, ys - 61, f"IBAN - {d['iban_txt']}", HR, 8.5, INK)

    xt, wt = W - M - 210, 210
    rect(c, xt, ys - 62, wt, 62, borde=DK, grosor=0.8)
    tx(c, xt + 8, ys - 15, "Base imponible", HR, 9, INK)
    tx(c, xt + wt - 8, ys - 15, fmt(t["base"]) + " €", HR, 9, INK, "r")
    tx(c, xt + 8, ys - 30, "Total IVA", HR, 9, INK)
    tx(c, xt + wt - 8, ys - 30, fmt(t["cuota"]) + " €", HR, 9, INK, "r")
    rect(c, xt, ys - 62, wt, 22, relleno=DK)
    tx(c, xt + 8, ys - 55, "TOTAL FACTURA", HB, 10, white)
    tx(c, xt + wt - 8, ys - 55, fmt(t["total"]) + " €", HB, 11, white, "r")

    tx(c, M, 40, "Conserve el pasaporte fitosanitario de los plantones durante al menos tres años. La mercancía viaja "
                 "por cuenta y riesgo del destinatario una vez entregada.", HR, 7, G)
    tx(c, W - M, 28, "Página 1 de 1", HR, 7, G, "r")
    c.showPage()
    c.save()


# ==============================================================================================
# 8. RECIBIDA escaneada: talonario de facturas rellenado a máquina (solo imagen)
# ==============================================================================================
def doc_08() -> dict:
    lineas = [
        {"concepto": "Sustitución de grifo monomando cocina", "contiene": "Sustitución de grifo monomando",
         "cantidad": D("1"), "precio": D("68.00"), "cant_txt": "1"},
        {"concepto": "Latiguillos flexibles 40 cm", "contiene": "Latiguillos flexibles", "cantidad": D("2"),
         "precio": D("4.75"), "cant_txt": "2"},
        {"concepto": "Mano de obra oficial fontanero (h)", "contiene": "Mano de obra oficial fontanero",
         "cantidad": D("2.5"), "precio": D("32.00"), "cant_txt": "2,5"},
        {"concepto": "Desplazamiento", "contiene": "Desplazamiento", "cantidad": D("1"), "precio": D("18.00"),
         "cant_txt": "1"},
    ]
    t = calcular(lineas)
    return {
        "archivo": "hold2_08_escaneada_talonario_recibida.pdf",
        "estilo": ("Factura RECIBIDA ESCANEADA (solo imagen JPEG a 200 ppp en gris, sin capa de texto, giro de "
                   "-0,8°, desenfoque, ruido y motas): talonario preimpreso con caja del emisor, caja 'FACTURA' con "
                   "celdas Nº y FECHA, campos 'Sr./Sres.:', 'Domicilio:', 'C.P. y Población:' y 'N.I.F.:' con línea "
                   "de puntos, tabla con rejilla CANTIDAD | CONCEPTO | PRECIO | IMPORTE y renglones vacíos, caja "
                   "de totales SUMA / I.V.A. 21 % / TOTAL €, valores mecanografiados en Courier, A4"),
        "empresa_nif": CIF_CAMPOS,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "26-0347",
        "fecha": date(2026, 10, 28),
        "fecha_txt": "28/10/2026",
        "emisor": {"nombre": "Fontanería y Calefacción Pisuerga Norte, S.L.", "nif": CIF_PISUERGA,
                   "domicilio_contiene": "47012"},
        "destinatario": {"nombre": "Asesoría Laboral Campos Góticos, S.L.", "nif": CIF_CAMPOS,
                         "domicilio_contiene": "47001"},
        "nif_txt": {"emisor": f"C.I.F. {CIF_PISUERGA}", "destinatario": CIF_CAMPOS},
        "lineas": lineas,
        "t": t,
        "referencias": {},
        "iban": "ES0077778888999900004701",
        "iban_txt": "ES00 7777 8888 9999 0000 4701",
        "forma_pago_contiene": "Transferencia",
        "indicios": [],
        "fmt": num,
        "escaneado": True,
        "textos": ["SUMA", "Sr./Sres.:", "TOTAL €"],
        "notas": ("La sube la empresa DESTINATARIA (asesoría laboral): factura recibida de un proveedor en papel, "
                  "escaneada. PDF solo imagen (requiere OCR): rasterizado a 200 ppp en escala de grises, girado "
                  "-0,8°, con desenfoque gaussiano, ruido, motas y sombra del borde del escáner, guardado como JPEG "
                  "calidad 70 dentro de un PDF sin fuentes. Talonario preimpreso (etiquetas en Helvetica, emisor en "
                  "mayúsculas) con los datos mecanografiados en Courier; el NIF del cliente va en el campo 'N.I.F.:' "
                  "sin prefijo, y el del emisor como 'C.I.F.'. Cantidad decimal 2,5 h. Sin referencias. "
                  f"Base {num(t['base'])} x 21 % = 36,855 -> {num(t['cuota'])}; total {num(t['total'])}."),
        "render": render_08,
    }


def render_08(ruta: Path, d: dict):
    W, H = A4
    M = 46
    HR, HB, HO = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
    CR = "Courier-Bold"
    AZ, TY = HexColor("#1F3A5F"), HexColor("#151515")
    em, de, t, fmt = d["emisor"], d["destinatario"], d["t"], d["fmt"]
    c = nuevo_canvas(ruta, A4, "SCAN", "talonario")

    rect(c, M, H - 152, 318, 106, borde=AZ, grosor=1.2)
    tx(c, M + 10, H - 70, em["nombre"].upper(), HB, 11, AZ)
    tx(c, M + 10, H - 86, "Instalaciones · Reparaciones · Calderas de gas", HO, 8.5, AZ)
    tx(c, M + 10, H - 104, "C/ Tudela Vieja, 22 - 47012 VALLADOLID", HR, 8.5, AZ)
    tx(c, M + 10, H - 117, "Tel. 983 000 000 - fontaneria@pisuerganorte.example", HR, 8.5, AZ)
    tx(c, M + 10, H - 132, d["nif_txt"]["emisor"], HB, 9, AZ)
    xr = M + 334
    wr = W - M - xr
    rect(c, xr, H - 152, wr, 106, borde=AZ, grosor=1.2)
    tx(c, xr + wr / 2, H - 72, "FACTURA", HB, 19, AZ, "c")
    for i, (k, v) in enumerate([("Nº", d["numero"]), ("FECHA", d["fecha_txt"])]):
        yc = H - 108 - 26 * i
        rect(c, xr + 10, yc - 6, wr - 20, 22, borde=AZ, grosor=0.6)
        tx(c, xr + 16, yc + 2, k, HB, 8, AZ)
        tx(c, xr + 62, yc + 1, v, CR, 11.5, TY)

    yb = H - 172
    rect(c, M, yb - 98, W - 2 * M, 98, borde=AZ, grosor=1.0)
    campos = [("Sr./Sres.:", de["nombre"]), ("Domicilio:", "Plaza de la Rinconada Alta 3, 1º"),
              ("C.P. y Población:", "47001 Valladolid"), ("N.I.F.:", d["nif_txt"]["destinatario"])]
    for i, (k, v) in enumerate(campos):
        yy = yb - 22 - 22 * i
        tx(c, M + 10, yy, k, HR, 9, AZ)
        x0 = M + 12 + ancho(k, HR, 9)
        raya(c, x0, yy - 2, W - M - 10, yy - 2, AZ, 0.5, discontinua=(1, 2))
        tx(c, x0 + 8, yy + 1, v, CR, 10.5, TY)

    ytop = yb - 118
    bordes = [M, M + 70, M + 340, M + 418, W - M]
    rect(c, M, ytop - 18, W - 2 * M, 18, relleno=AZ)
    for i, k in enumerate(["CANTIDAD", "CONCEPTO", "PRECIO", "IMPORTE"]):
        tx(c, (bordes[i] + bordes[i + 1]) / 2, ytop - 13, k, HB, 8.5, white, "c")
    filas = 14
    alto = 20
    yfin = ytop - 18 - filas * alto
    c.setStrokeColor(AZ)
    c.setLineWidth(0.6)
    for x in bordes:
        c.line(x, ytop, x, yfin)
    for i in range(filas + 1):
        c.line(M, ytop - 18 - i * alto, W - M, ytop - 18 - i * alto)
    for i, ln in enumerate(d["lineas"]):
        yy = ytop - 18 - i * alto - 14
        tx(c, bordes[1] - 8, yy, ln["cant_txt"], CR, 10.5, TY, "r")
        tx(c, bordes[1] + 8, yy, ln["concepto"], CR, 10.5, TY)
        tx(c, bordes[3] - 8, yy, fmt(ln["precio"]), CR, 10.5, TY, "r")
        tx(c, bordes[4] - 8, yy, fmt(ln["importe"]), CR, 10.5, TY, "r")

    yt = yfin - 14
    xt = bordes[2]
    wt = W - M - xt
    for i, (k, v) in enumerate([("SUMA", t["base"]), ("I.V.A. 21 %", t["cuota"]), ("TOTAL €", t["total"])]):
        yy = yt - 24 * i
        rect(c, xt, yy - 20, wt, 22, borde=AZ, grosor=0.8)
        tx(c, xt + 8, yy - 13, k, HB, 9, AZ)
        tx(c, W - M - 8, yy - 14, fmt(v), CR, 11, TY, "r")
    rect(c, M, yt - 68, bordes[2] - M - 16, 70, borde=AZ, grosor=0.8)
    tx(c, M + 8, yt - 14, "FORMA DE PAGO:", HB, 8.5, AZ)
    tx(c, M + 96, yt - 14, "Transferencia", CR, 10.5, TY)
    tx(c, M + 8, yt - 34, "IBAN", HB, 8.5, AZ)
    tx(c, M + 40, yt - 34, d["iban_txt"], CR, 10, TY)
    tx(c, M + 8, yt - 56, "Recibí:", HO, 8.5, AZ)

    tx(c, W / 2, 50, "Inscrita en el Registro Mercantil de Valladolid, Tomo 1.234, Folio 56, Hoja VA-12345",
       HR, 7, AZ, "c")
    c.showPage()
    c.save()


DOCUMENTOS = [doc_01, doc_02, doc_03, doc_04, doc_05, doc_06, doc_07, doc_08]


# ----------------------------------------------------------------------------------------------
# escaneado simulado
# ----------------------------------------------------------------------------------------------
PPP_ESCANEO = 200


def escanear(pdf_limpio: Path, destino: Path, tmp: Path, semilla: int) -> Path:
    """Rasteriza a 200 ppp en gris, gira -0,8°, desenfoca, añade ruido y motas y guarda un PDF solo-imagen."""
    if not shutil.which("pdftoppm"):
        raise SystemExit("pdftoppm (poppler-utils) es necesario para generar el documento escaneado")
    prefijo = tmp / "pagina"
    subprocess.run(["pdftoppm", "-r", str(PPP_ESCANEO), "-gray", "-png", "-singlefile", str(pdf_limpio),
                    str(prefijo)], check=True)
    rng = random.Random(semilla)
    img = Image.open(prefijo.with_suffix(".png")).convert("L")
    papel, tinta = 233, 34
    img = img.point(lambda v: tinta + v * (papel - tinta) // 255)
    img = img.rotate(-0.8, resample=Image.BICUBIC, expand=False, fillcolor=papel, translate=(-7, 11))
    img = img.filter(ImageFilter.GaussianBlur(0.75))
    w, h = img.size
    ruido = Image.frombytes("L", (w, h), rng.randbytes(w * h)).point(lambda v: v * 30 // 255)
    img = ImageChops.add(img, ruido, scale=1.0, offset=-15)
    dib = ImageDraw.Draw(img)
    for _ in range(650):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.choice([0, 1, 1, 1, 2, 2])
        dib.ellipse([x - r, y - r, x + r, y + r], fill=rng.randrange(60, 170))
    for i in range(18):                     # sombra del borde superior de la tapa del escáner
        dib.line([(0, i), (w, i)], fill=80 + i * 8)
    jpg = tmp / "escaneo.jpg"
    img.save(jpg, "JPEG", quality=70)
    fijo = time.struct_time((2026, 10, 30, 10, 41, 0, 4, 303, 0))
    Image.open(jpg).save(destino, "PDF", resolution=float(PPP_ESCANEO), title="SCAN_20261030_0003",
                         creator="Escáner de documentos (sintético)", producer="Corpus sintético verifactu-app",
                         subject="Documento sintético de prueba (datos ficticios)", creationDate=fijo, modDate=fijo)
    return jpg


# ----------------------------------------------------------------------------------------------
# JSON esperado
# ----------------------------------------------------------------------------------------------
def esperado(d: dict) -> dict:
    t = d["t"]
    return {
        "archivo": d["archivo"],
        "estilo": d["estilo"],
        "empresa_nif": d["empresa_nif"],
        "tipo_documento": d["tipo_documento"],
        "situacion_esperada": d["situacion_esperada"],
        "numero": d["numero"],
        "fecha": d["fecha"].isoformat(),
        "emisor": dict(d["emisor"]),
        "destinatario": dict(d["destinatario"]),
        "lineas": [{"concepto_contiene": ln["contiene"], "cantidad": s2(ln.get("cantidad")),
                    "precio": s2(ln.get("precio")), "importe": s2(ln["importe"])} for ln in d["lineas"]],
        "desglose": [{"tipo_iva": f"{g['tipo']:.2f}", "base": s2(g["base"]), "cuota": s2(g["cuota"])}
                     for g in t["desglose"]],
        "base_imponible": s2(t["base"]),
        "cuota_iva": s2(t["cuota"]),
        "total_factura": s2(t["total"]),
        "retencion": ({"porcentaje": f"{t['pct_ret']:.2f}", "importe": s2(t["ret"])} if t["ret"] is not None
                      else None),
        "total_a_pagar": s2(t["pagar"]),
        "referencias": dict(d["referencias"]),
        "iban": d["iban"],
        "forma_pago_contiene": d["forma_pago_contiene"],
        "indicios_esperados": list(d["indicios"]),
        "notas": d["notas"],
    }


# ----------------------------------------------------------------------------------------------
# verificación
# ----------------------------------------------------------------------------------------------
PATRONES_FISCALES = {  # particularidades fiscales: solo pueden aparecer si el JSON las espera
    "exencion": r"\bexent[ao]s?\b|exenci[oó]n|art(?:[ií]culo|\.)\s*20\b",
    "inversion_sujeto_pasivo": r"inversi[oó]n\s+del\s+sujeto\s+pasivo|art(?:[ií]culo|\.)\s*84",
    "no_sujeta": r"\bno\s+sujet[ao]s?\b",
    "recargo_equivalencia": r"recargo\s+(?:de\s+)?equivalencia",
    "suplidos": r"\bsuplidos?\b",
    "descuento": r"\bdescuentos?\b|\bdto\.?\b",
    "rectificativa": r"rectificativ|\babono\b",
    "intracomunitaria": r"intracomunitari|\bVIES\b|reverse\s+charge",
    "criterio_caja": r"criterio\s+de\s+caja",
    "regimen_especial": r"r[eé]gimen\s+especial|agencias\s+de\s+viaje|bienes\s+usados|\bREAGYP\b",
    "verifactu_existente": r"VERI\*?FACTU|QR\s+tributario|Factura\s+verificable",
    "otro_tipo_documento": r"pro\s*-?\s*forma|presupuesto",
}


def norm(t) -> str:
    """Igual que la normalización del evaluador: sin acentos, minúsculas, solo [a-z0-9]."""
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]", "", t)


def _esp(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def _plano(s: str) -> str:
    return re.sub(r"[\s.\-/]", "", s.upper())


def _hay_importe(s: str, esp: str) -> bool:
    return re.search(r"(?<![\d.,])" + re.escape(s.casefold()) + r"(?!\d)", esp) is not None


def pdftotext(ruta: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(ruta), "-"], capture_output=True, text=True,
                          check=True).stdout


def coherencia(e: dict, d: dict) -> list[str]:
    """Comprobaciones del JSON independientes del PDF: NIF, situación, claves y aritmética exacta."""
    fallos: list[str] = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    for parte in ("emisor", "destinatario"):
        debe(es_valido(e[parte]["nif"]), f"NIF {parte} inválido {e[parte]['nif']}")
        debe(normalizar(e[parte]["nif"]) == e[parte]["nif"], f"NIF {parte} no normalizado")
    debe(es_valido(e["empresa_nif"]), "empresa_nif inválido")
    if e["situacion_esperada"] == "recibida":
        debe(e["empresa_nif"] == e["destinatario"]["nif"], "recibida: empresa_nif debe ser el destinatario")
    else:
        debe(e["situacion_esperada"] == "preparatorio", f"situación no prevista {e['situacion_esperada']}")
        debe(e["empresa_nif"] == e["emisor"]["nif"], "factura propia: empresa_nif debe ser el emisor")
    debe(e["emisor"]["nif"] != e["destinatario"]["nif"], "emisor y destinatario con el mismo NIF")
    debe(e["tipo_documento"] == "factura", "tipo_documento")
    debe(set(e["referencias"]) <= REFS_PERMITIDAS, f"referencias no permitidas {set(e['referencias'])}")
    debe(set(e["indicios_esperados"]) <= INDICIOS, "indicio desconocido")
    debe(date.fromisoformat(e["fecha"]) <= date(2026, 11, 9), "fecha posterior a la generación")
    debe(e["iban"] is None or re.fullmatch(r"ES00\d{20}", e["iban"]) is not None, "IBAN no evidentemente ficticio")

    importes = [e["base_imponible"], e["cuota_iva"], e["total_factura"], e["total_a_pagar"]]
    importes += [x for ln in e["lineas"] for x in (ln["cantidad"], ln["precio"], ln["importe"])]
    importes += [x for g in e["desglose"] for x in (g["tipo_iva"], g["base"], g["cuota"])]
    if e["retencion"]:
        importes += [e["retencion"]["porcentaje"], e["retencion"]["importe"]]
    debe(all(x is None or re.fullmatch(r"-?\d+\.\d{2}", x) for x in importes), "importe sin 2 decimales")

    base, cuota, total = D(e["base_imponible"]), D(e["cuota_iva"]), D(e["total_factura"])
    debe(sum(D(x["importe"]) for x in e["lineas"]) == base, "suma de líneas != base")
    debe(sum(D(g["base"]) for g in e["desglose"]) == base, "suma de bases del desglose != base")
    debe(sum(D(g["cuota"]) for g in e["desglose"]) == cuota, "suma de cuotas del desglose != cuota")
    for g in e["desglose"]:
        debe(q2(D(g["base"]) * D(g["tipo_iva"]) / 100) == D(g["cuota"]), f"cuota mal calculada al {g['tipo_iva']}")
    for g in e["desglose"]:
        lin = sum((ln["importe"] for ln in d["lineas"] if D(ln["iva"]) == D(g["tipo_iva"])), D("0"))
        debe(lin == D(g["base"]), f"líneas al {g['tipo_iva']} != base del tipo")
    debe(base + cuota == total, "base + cuota != total")
    for ln, x in zip(d["lineas"], e["lineas"]):
        if x["cantidad"] is not None:
            calc = q2(D(x["cantidad"]) * D(x["precio"]) * (D(100) - D(ln.get("dto") or 0)) / 100)
            debe(calc == D(x["importe"]), f"línea {x['concepto_contiene']}: {calc} != {x['importe']}")
        debe((x["cantidad"] is None) == (x["precio"] is None), f"cantidad/precio incompletos {x['concepto_contiene']}")
        debe(_esp(ln["primera"]).startswith(_esp(ln["contiene"])),
             f"concepto_contiene no es el comienzo del primer renglón: {ln['contiene']!r} / {ln['primera']!r}")
    if e["retencion"]:
        r = D(e["retencion"]["importe"])
        debe(q2(base * D(e["retencion"]["porcentaje"]) / 100) == r, "retención mal calculada")
        debe(total - r == D(e["total_a_pagar"]), "total - retención != total a pagar")
    else:
        debe(e["total_a_pagar"] is None, "total_a_pagar debe ser null sin retención")
    return fallos


def verificar_texto(texto: str, e: dict, d: dict) -> list[str]:
    """El PDF (capa de texto, pdftotext -layout) debe mostrar todo lo que el JSON espera."""
    fallos: list[str] = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    esp, plano, nt, fmt = _esp(texto), _plano(texto), norm(texto), d["fmt"]
    debe(e["numero"] in texto, f"número {e['numero']}")
    debe(_esp(d["fecha_txt"]) in esp, f"fecha {d['fecha_txt']}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(_esp(d["nif_txt"][parte]) in esp, f"NIF {parte} impreso {d['nif_txt'][parte]!r}")
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        debe(_esp(p["nombre"]) in esp, f"nombre {parte} {p['nombre']}")
        debe(_esp(p["domicilio_contiene"]) in esp, f"domicilio {parte} {p['domicilio_contiene']}")
    for ln in d["lineas"]:
        debe(_esp(ln["contiene"]) in esp, f"concepto {ln['contiene']}")
        debe(_esp(ln["primera"]) in esp, f"primer renglón {ln['primera']}")
        debe(_hay_importe(fmt(ln["importe"]), esp), f"importe de línea {fmt(ln['importe'])}")
        if ln.get("precio") is not None:
            debe(_hay_importe(fmt(ln["precio"]), esp), f"precio de línea {fmt(ln['precio'])}")
        if ln.get("cant_txt"):
            debe(_esp(ln["cant_txt"]) in esp, f"cantidad {ln['cant_txt']}")
    t = d["t"]
    for g in t["desglose"]:
        debe(_hay_importe(fmt(g["base"]), esp), f"base al {g['tipo']} {fmt(g['base'])}")
        debe(_hay_importe(fmt(g["cuota"]), esp), f"cuota al {g['tipo']} {fmt(g['cuota'])}")
    for k in ("base", "cuota", "total", "ret", "pagar"):
        if t[k] is not None:
            debe(_hay_importe(fmt(t[k]), esp), f"{k} {fmt(t[k])}")
    if t["pct_ret"] is not None:
        debe(re.search(rf"(?<![\d,]){t['pct_ret']:.0f}\s*%", texto) is not None, "porcentaje de retención")
    for g in t["desglose"]:
        debe(re.search(rf"(?<![\d,]){g['tipo']:.0f}\s*%", texto) is not None, f"tipo {g['tipo']:.0f} %")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    debe(_esp(d["iban_txt"]) in esp, f"IBAN impreso {d['iban_txt']}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    debe(norm(e["forma_pago_contiene"]) in nt, f"forma de pago {e['forma_pago_contiene']}")
    for s in d.get("textos", []):
        debe(_esp(s) in esp, f"texto {s!r}")
    correos = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", texto)
    debe(bool(correos) and all(x.endswith(".example") for x in correos), f"correos no ficticios {correos}")
    for k, p in PATRONES_FISCALES.items():
        hay = re.search(p, texto, re.I) is not None
        if k in e["indicios_esperados"]:
            debe(hay, f"indicio esperado sin respaldo en el texto: {k}")
        else:
            debe(not hay, f"el texto contiene una particularidad no esperada: {k}")
    return fallos


def verificar_escaneado(pdf: Path) -> list[str]:
    fallos = []
    texto = pdftotext(pdf)
    if texto.strip():
        fallos.append(f"el PDF escaneado tiene capa de texto: {texto.strip()[:60]!r}")
    lector = PdfReader(str(pdf))
    for pag in lector.pages:
        recursos = pag.get("/Resources") or {}
        if "/Font" in recursos:
            fallos.append("el PDF escaneado declara fuentes")
        if "/XObject" not in recursos:
            fallos.append("el PDF escaneado no contiene imagen")
    return fallos


def ocr_informativo(jpg: Path, e: dict, d: dict) -> dict:
    """Solo informativo (no falla): qué datos clave reconoce tesseract en la imagen escaneada."""
    if not shutil.which("tesseract"):
        return {}
    ocr = subprocess.run(["tesseract", str(jpg), "stdout", "-l", "spa", "--psm", "4"], capture_output=True,
                         text=True).stdout
    plano = _plano(ocr)
    claves = {"numero": e["numero"], "fecha": d["fecha_txt"], "nif_emisor": e["emisor"]["nif"],
              "nif_destinatario": e["destinatario"]["nif"], "base": num(D(e["base_imponible"])),
              "cuota": num(D(e["cuota_iva"])), "total": num(D(e["total_factura"]))}
    return {"ocr": {k: _plano(v) in plano for k, v in claves.items()}}


def nifs_otros_corpus() -> set[str]:
    nifs: set[str] = set()
    for js in list((RAIZ / "tests" / "corpus").rglob("*.json")) + list((RAIZ / "tests" / "corpus_holdout").glob("*.json")):
        try:
            x = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k in ("emisor", "destinatario"):
            if (x.get(k) or {}).get("nif"):
                nifs.add(normalizar(x[k]["nif"]))
    return nifs


def main(argv: list[str]) -> int:
    registrar_fuentes()
    SALIDA.mkdir(parents=True, exist_ok=True)
    otros = nifs_otros_corpus()
    vistos: dict[str, str] = {}
    total_fallos = 0
    for i, fabrica in enumerate(DOCUMENTOS, 1):
        d = fabrica()
        ruta = SALIDA / d["archivo"]
        e = esperado(d)
        fallos = coherencia(e, d)
        info: dict = {}
        for parte in ("emisor", "destinatario"):
            n = e[parte]["nif"]
            if n in otros:
                fallos.append(f"NIF {n} ya usado en otro corpus")
            if vistos.get(n, e[parte]["nombre"]) != e[parte]["nombre"]:
                fallos.append(f"NIF {n} repetido con otro nombre en este corpus")
            vistos[n] = e[parte]["nombre"]
        if d.get("escaneado"):
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                limpio = tmp / "limpio.pdf"
                d["render"](limpio, d)
                fallos += [f"(PDF limpio de origen) {x}" for x in verificar_texto(pdftotext(limpio), e, d)]
                jpg = escanear(limpio, ruta, tmp, SEMILLA + i)
                fallos += verificar_escaneado(ruta)
                info = ocr_informativo(jpg, e, d)
        else:
            d["render"](ruta, d)
            fallos += verificar_texto(pdftotext(ruta), e, d)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total_fallos += len(fallos)
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}" + (f"  -> {fallos}" if fallos else "")
              + (f"  {info}" if info else ""))
    print(f"{len(DOCUMENTOS)} documentos en {SALIDA.relative_to(RAIZ)}; fallos: {total_fallos}")
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
