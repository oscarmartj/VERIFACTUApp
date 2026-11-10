"""Tercer corpus SINTÉTICO «held-out» (ciego), familia «c»: cuatro facturas españolas con presentación visual poco
habitual para medir la generalización del extractor.

  1. Alquiler audiovisual: cabecera en BANDA OSCURA a todo el ancho con el texto en BLANCO (datos del emisor y título
     «FACTURA» con número, fecha y vencimiento dentro del rectángulo relleno) y totales en una CAJA OSCURA con texto
     blanco. Una línea con descuento del 10 %. Factura propia (documento preparatorio).
  2. Ilustradora autónoma (persona física con DNI), maqueta de «tarjetas»: emisora y cliente lado a lado en cajas
     redondeadas y los conceptos como tarjetas apiladas (descripción en un renglón y debajo «2 x 35,00 € = 70,00 €»),
     sin tabla. Retención IRPF 7 % (inicio de actividad). Factura propia (documento preparatorio).
  3. Taller mecánico: FOTO DE MÓVIL de la página (solo imagen): rasterizada a 150 ppp en gris con pdftoppm, sombra
     degradada de izquierda a derecha, giro leve, borde de la mesa visible, ruido y JPEG. Factura RECIBIDA.
  4. Suministros agrícolas (IVA 10 % y 21 %) recibidos por FAX (solo imagen): rasterizada a 200 ppp, binarizada a
     blanco y negro puro (1 bit, CCITT G4) con umbral, ruido de sal y pimienta y cabecera de transmisión del fax.
     Factura RECIBIDA.

TODOS LOS DATOS SON FICTICIOS: razones sociales, nombres de persona, domicilios, teléfonos (9x0 000 000), correos
(dominio .example), referencias, matrícula e IBAN (ES00 + 20 dígitos: dígitos de control imposibles). Los
NIF/CIF/DNI tienen carácter de control válido (app.nif.completar_cif / letra del DNI) pero son de rangos
evidentemente ficticios: CIF con código de provincia 00 (no asignado a ninguna provincia) y DNI de cifras
repetidas, también en los escaneados (3 y 4), aunque el OCR pueda leer «B00…» como «BO0…»: un CIF inventado de
provincia real podría coincidir con el de una empresa existente y el repositorio es público. Las calles
llevan «Inventado», «Ficticio» o «Imaginario» en el nombre (solo la población y el código postal son reales). Se
comprueba al generar que los NIF no coinciden con los de los demás corpus del repositorio (JSON y texto de los PDF de
tests/corpus*). Estos identificadores y calles sustituyeron a otros verosímiles después de la medición ciega; la
maqueta, los importes y el resto de los textos no cambiaron.

Este corpus se diseñó SIN consultar el código del extractor (app/extraccion/) ni los datos privados (tests/privado/)
y sin ejecutar el evaluador: es un conjunto ciego. No lo ajustes para que el extractor acierte o falle.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/holdout3_c.py [--tmp DIRECTORIO]
        genera tests/corpus_holdout3/h3c_*.pdf + .json y verifica; los PDF limpios de origen de los escaneados y las
        imágenes intermedias se dejan en DIRECTORIO (por defecto, un directorio temporal que se borra al terminar).

Verificación (sale con código 1 si algo falla):
  * aritmética exacta de cada JSON (en los cuatro): suma de líneas = base de cada tipo, cuota = base x tipo
    redondeada half-up, total = bases + cuotas, retención y total a pagar; cada línea = cantidad x precio (menos el
    descuento de la línea, si lo hay); NIF válidos, normalizados y no usados en otros corpus; empresa_nif coherente
    con la situación esperada;
  * PDF con capa de texto (1 y 2), en `pdftotext -layout`: número, fecha, NIF tal como se imprimen, nombres,
    códigos postales, conceptos, cantidades, precios e importes de línea, bases, cuotas, totales, retención,
    referencias, IBAN, forma de pago, rótulos propios, correos .example, teléfonos 9x0 000 000 y ausencia de
    particularidades fiscales no esperadas; además, en el 1, que la banda de cabecera y la caja de totales son
    oscuras en el render; en el 2, la fórmula «n x precio € = importe €» de cada tarjeta y la ausencia de cabecera
    de columnas;
  * escaneados (3 y 4): solo aritmética, más que el PDF no tenga capa de texto ni fuentes y que la imagen tenga la
    resolución, profundidad y codificación previstas (150 ppp gris JPEG; 200 ppp 1 bit CCITT). Como control
    adicional se verifica también el texto del PDF limpio de origen antes de rasterizarlo.

Determinista: canvas de reportlab en modo «invariant», semillas fijas para el ruido (numpy) y fechas fijas en los
metadatos de los PDF de imagen.
Requisitos: reportlab, Pillow, numpy, poppler-utils (pdftotext, pdfinfo, pdftoppm, pdfimages, pdffonts). Fuentes
TrueType de /usr/share/fonts/truetype (Carlito, FreeSans, Liberation, DejaVu); si faltan se sustituyen por las
estándar con un aviso.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, ".")

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import buscar_nifs, completar_cif, es_valido, normalizar  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus_holdout3"
PREFIJO = "h3c_"
SEMILLA = 20261109 + 3

D = Decimal
W, H = A4
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
FECHA_GENERACION = date(2026, 11, 9)
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
    "FreeSans": ("freefont/FreeSans.ttf", "Helvetica"),
    "FreeSans-Bold": ("freefont/FreeSansBold.ttf", "Helvetica-Bold"),
    "LibSans": ("liberation/LiberationSans-Regular.ttf", "Helvetica"),
    "LibSans-Bold": ("liberation/LiberationSans-Bold.ttf", "Helvetica-Bold"),
    "LibSans-Italic": ("liberation/LiberationSans-Italic.ttf", "Helvetica-Oblique"),
    "LibMono": ("liberation/LiberationMono-Regular.ttf", "Courier"),
    "LibMono-Bold": ("liberation/LiberationMono-Bold.ttf", "Courier-Bold"),
    "DejaVuSans": ("dejavu/DejaVuSans.ttf", "Helvetica"),
    "DejaVuSans-Bold": ("dejavu/DejaVuSans-Bold.ttf", "Helvetica-Bold"),
}
FUENTE: dict[str, str] = {}


def registrar_fuentes() -> None:
    if FUENTE:
        return
    base = Path("/usr/share/fonts/truetype")
    for nombre, (rel, reserva) in _TTF.items():
        ruta = base / rel
        if ruta.exists():
            pdfmetrics.registerFont(TTFont(nombre, str(ruta)))
            FUENTE[nombre] = nombre
        else:
            print(f"aviso: falta {ruta}; se usa {reserva} (los PDF no serán idénticos a los publicados)")
            FUENTE[nombre] = reserva


def F(nombre: str) -> str:
    return FUENTE.get(nombre, nombre)


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def dni(n8: int) -> str:
    return f"{n8:08d}{LETRAS_DNI[n8 % 23]}"


def q2(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(x) -> str | None:
    return None if x is None else str(q2(x))


def sin_empate(x) -> bool:
    """El evaluador cuantiza a 2 decimales con el redondeo por defecto (half-even); el JSON usa half-up."""
    x = D(x)
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP) == x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def num(x, nd: int = 2, miles: str = ".") -> str:
    """Formato español: 1.234,56 (nd decimales, punto de miles)."""
    x = D(x).quantize(D(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    ent, _, fr = f"{abs(x):.{nd}f}".partition(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", miles)
    return ("-" if x < 0 else "") + ent + ("," + fr if nd else "")


def cant_es(x) -> str:
    """Cantidad sin ceros sobrantes: 12 / 18,5 / 7,25."""
    x = D(x).normalize()
    nd = max(0, -x.as_tuple().exponent)
    return num(x, nd)


def importe_linea(ln: dict) -> Decimal:
    bruto = D(ln["cantidad"]) * D(ln["precio"])
    dto = D(ln.get("dto") or 0)
    return q2(bruto * (D(100) - dto) / D(100))


def calcular(lineas: list[dict], pct_ret=None) -> dict:
    """Importe de línea = cantidad x precio (- dto %), half-up; desglose por tipo; retención s/ base."""
    for ln in lineas:
        ln.setdefault("iva", D("21"))
        ln["importe"] = importe_linea(ln)
        ln.setdefault("primera", ln["concepto"])
        ln.setdefault("cant_txt", cant_es(ln["cantidad"]))
        ln.setdefault("precio_txt", num(ln["precio"]))
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
def tx(c, x, y, s, fuente="LibSans", tam=9.0, color=black, alin="l"):
    c.setFillColor(color)
    c.setFont(F(fuente), tam)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[alin](x, y, s)


def raya(c, x1, y1, x2, y2, color=black, grosor=0.5):
    c.setStrokeColor(color)
    c.setLineWidth(grosor)
    c.line(x1, y1, x2, y2)


def rect(c, x, y, w, h, relleno=None, borde=None, grosor=0.5):
    if relleno is not None:
        c.setFillColor(relleno)
    if borde is not None:
        c.setStrokeColor(borde)
        c.setLineWidth(grosor)
    c.rect(x, y, w, h, fill=int(relleno is not None), stroke=int(borde is not None))


def rrect(c, x, y, w, h, r, relleno=None, borde=None, grosor=0.6):
    if relleno is not None:
        c.setFillColor(relleno)
    if borde is not None:
        c.setStrokeColor(borde)
        c.setLineWidth(grosor)
    c.roundRect(x, y, w, h, r, fill=int(relleno is not None), stroke=int(borde is not None))


def nuevo_canvas(ruta: Path, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=A4, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    c.setCreator("scripts/corpus/holdout3_c.py")
    return c


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido; CIF de provincia 00 y DNI de cifras repetidas, también en los escaneados: ver el docstring);
# main comprueba que no aparecen en los demás corpus
# ----------------------------------------------------------------------------------------------
CIF_PENAMAYOR = completar_cif("B", "0061572")   # emisor doc 1 = empresa usuaria (audiovisuales, Llanera)
CIF_RIA = completar_cif("B", "0028619")         # cliente doc 1 (congresos y eventos, Avilés)
DNI_MONTESINOS = dni(33333333)                  # emisora doc 2 = empresa usuaria (ilustradora, València)
CIF_ALBUFERA = completar_cif("B", "0051843")    # cliente doc 2 (editorial, València)
CIF_CEBRIAN = completar_cif("B", "0027531")     # emisor doc 3 (taller mecánico, Albacete)
CIF_ALMANSA = completar_cif("B", "0024819")     # destinatario doc 3 = empresa usuaria (transportes, Almansa)
CIF_VEGASEGURA = completar_cif("B", "0073195")  # emisor doc 4 (suministros agrícolas, Molina de Segura)
CIF_COOP = completar_cif("F", "0030621")        # destinatario doc 4 = empresa usuaria (cooperativa, Santomera)
NIF_ESCANEADOS: set = set()  # todos los NIF son de rangos ficticios, también en los escaneados


def nif_ficticio(n: str) -> bool:
    """NIF que no puede ser de nadie: CIF con provincia 00 (no asignada) o DNI/NIE de cifras repetidas."""
    if re.fullmatch(r"[A-HJNP-SUVW]00\d{5}[0-9A-J]", n):
        return True
    return re.fullmatch(r"(\d)\1{7}[A-Z]|[XYZ](\d)\2{6}[A-Z]", n) is not None


def iban_txt(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


# ==============================================================================================
# 1. Banda oscura con texto blanco + caja de totales oscura (Carlito)
# ==============================================================================================
OSCURO_1 = HexColor("#16243A")
ACENTO_1 = HexColor("#E3A21A")
CLARO_1 = HexColor("#C5D0DD")


def doc_01() -> dict:
    lineas = [
        {"concepto": "Pantalla LED modular P3,9 de 4 x 2,5 m",
         "detalle": "Incluye procesador de vídeo y estructura de suspensión", "cantidad": D("2"),
         "precio": D("690.00"), "dto": D("10"), "contiene": "Pantalla LED modular P3,9"},
        {"concepto": "Proyector láser de 12.000 lúmenes con óptica angular", "detalle": None, "cantidad": D("2"),
         "precio": D("285.00"), "contiene": "Proyector láser de 12.000"},
        {"concepto": "Microfonía inalámbrica de mano, kit de 4 unidades", "detalle": None, "cantidad": D("2"),
         "precio": D("96.50"), "contiene": "Microfonía inalámbrica de mano"},
        {"concepto": "Mesa de mezclas digital de 32 canales", "detalle": None, "cantidad": D("2"),
         "precio": D("145.00"), "contiene": "Mesa de mezclas digital"},
        {"concepto": "Técnico de sonido e imagen (horas)",
         "detalle": "Montaje, ensayos y asistencia durante las dos jornadas", "cantidad": D("18.5"),
         "precio": D("32.00"), "contiene": "Técnico de sonido e imagen"},
        {"concepto": "Transporte, montaje y desmontaje del equipo", "detalle": None, "cantidad": D("1"),
         "precio": D("340.00"), "contiene": "Transporte, montaje y desmontaje"},
    ]
    t = calcular(lineas)
    iban = "ES0001824417209366504412"
    return {
        "archivo": "h3c_01_banda_oscura_totales_caja.pdf",
        "render": render_01,
        "estilo": ("Factura de empresa de alquiler audiovisual en Carlito: cabecera en BANDA OSCURA (azul noche) a "
                   "todo el ancho con TODO el texto en blanco: monograma, razón social, actividad, domicilio, CIF y "
                   "contacto del emisor a la izquierda y, a la derecha dentro de la misma banda, título 'FACTURA' y "
                   "rejilla Número / Fecha / Vencimiento; filete ámbar bajo la banda; bloques 'FACTURAR A' y 'DATOS "
                   "DEL SERVICIO' (evento, lugar, fechas del evento, su pedido); tabla con cabecera gris Concepto | "
                   "Cant. | Precio | Dto. | Importe (€), detalle gris bajo algunas líneas y un 10 % de descuento en "
                   "la primera; totales Base imponible / IVA 21 % / TOTAL dentro de una CAJA OSCURA con texto "
                   "blanco; forma de pago e IBAN a la izquierda; pie registral, A4"),
        "empresa_nif": CIF_PENAMAYOR,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "AV-2026-0412",
        "fecha": date(2026, 10, 31),
        "fecha_txt": "31/10/2026",
        "emisor": {"nombre": "Audiovisuales Peña Mayor, S.L.", "nif": CIF_PENAMAYOR, "domicilio_contiene": "33192"},
        "destinatario": {"nombre": "Congresos y Eventos Ría de Avilés, S.L.", "nif": CIF_RIA,
                         "domicilio_contiene": "33402"},
        "nif_txt": {"emisor": f"CIF {CIF_PENAMAYOR}", "destinatario": f"CIF: {CIF_RIA}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"pedido": "EV-26-0188"},
        "iban": iban,
        "iban_txt": iban_txt(iban),
        "forma_pago_contiene": "Transferencia bancaria a 30 días",
        "indicios": ["descuento"],
        "permitidos": [],
        "textos": ["FACTURA", "FACTURAR A", "Base imponible", "IVA 21 %", "TOTAL", "Dto.", "10 %"],
        "zonas_oscuras": [(0, H - 168, W, 168)],   # + caja de totales, que añade el render
        "notas": ("Factura propia de una empresa de alquiler audiovisual (empresa usuaria = emisora) sin marcas "
                  "VERI*FACTU: documento preparatorio. Toda la cabecera va en una banda oscura con texto BLANCO: "
                  "nombre, actividad, domicilio (33192 Llanera), CIF y contacto del emisor a la izquierda y el "
                  "título 'FACTURA' con Número AV-2026-0412, Fecha 31/10/2026 y Vencimiento 30/11/2026 a la "
                  "derecha. Los totales también van en blanco dentro de una caja oscura. La primera línea lleva "
                  "un 10 % en la columna 'Dto.': 2 x 690,00 = 1.380,00 - 10 % = 1.242,00 (precio esperado = "
                  "precio unitario antes del descuento, importe = neto de la línea). Cantidad decimal 18,5 h. "
                  "Distractores: vencimiento 30/11/2026, fechas del evento 28 y 29/10/2026, '4 x 2,5 m', "
                  "'12.000 lúmenes', '32 canales', 'kit de 4 unidades', 'nave 3' y datos registrales en el pie. "
                  "Base 3.227,00 x 21 % = 677,67; total 3.904,67."),
    }


def render_01(ruta: Path, d: dict):
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", d["emisor"]["nombre"])
    m = 40
    banda = 168
    # --- banda oscura con texto blanco
    rect(c, 0, H - banda, W, banda, relleno=OSCURO_1)
    rect(c, 0, H - banda - 4, W, 4, relleno=ACENTO_1)
    rrect(c, m, H - 80, 40, 40, 7, relleno=white)
    tx(c, m + 20, H - 66, "PM", "Carlito-Bold", 17, OSCURO_1, "c")
    tx(c, m + 52, H - 58, d["emisor"]["nombre"], "Carlito-Bold", 19, white)
    tx(c, m + 52, H - 74, "Alquiler y montaje de sonido, imagen e iluminación para eventos", "Carlito", 9.5,
       CLARO_1)
    y = H - 104
    for s in ("Polígono Ficticio, calle Inventada 14, nave 3", "33192 Llanera (Asturias)",
              d["nif_txt"]["emisor"], "Tel. 980 000 000 · facturacion@penamayor-av.example"):
        tx(c, m, y, s, "Carlito", 10, white)
        y -= 13
    tx(c, W - m, H - 66, "FACTURA", "Carlito-Bold", 32, white, "r")
    raya(c, W - m - 150, H - 80, W - m, H - 80, ACENTO_1, 1.2)
    y = H - 104
    for etq, val in (("Número", d["numero"]), ("Fecha", d["fecha_txt"]), ("Vencimiento", "30/11/2026")):
        tx(c, W - m - 150, y, etq, "Carlito", 10, CLARO_1)
        tx(c, W - m, y, val, "Carlito-Bold", 11, white, "r")
        y -= 15
    # --- cliente y datos del servicio
    y0 = H - banda - 34
    tx(c, m, y0, "FACTURAR A", "Carlito-Bold", 9, ACENTO_1)
    tx(c, m, y0 - 17, d["destinatario"]["nombre"], "Carlito-Bold", 12, OSCURO_1)
    y = y0 - 31
    for s in ("Calle Imaginaria 27, 2.º", "33402 Avilés (Asturias)", d["nif_txt"]["destinatario"],
              "Contacto: Rebeca Llaneza Prendes"):
        tx(c, m, y, s, "Carlito", 10, black)
        y -= 12.5
    x2 = 330
    tx(c, x2, y0, "DATOS DEL SERVICIO", "Carlito-Bold", 9, ACENTO_1)
    y = y0 - 17
    for etq, val in (("Evento:", "Jornadas de Innovación Marítima 2026"), ("Lugar:", "Auditorio del Muelle, Avilés"),
                     ("Fechas del evento:", "28 y 29/10/2026"), ("Su pedido:", d["referencias"]["pedido"])):
        tx(c, x2, y, etq, "Carlito", 10, HexColor("#5A6675"))
        tx(c, x2 + 82, y, val, "Carlito", 10, black)
        y -= 13.5
    # --- tabla
    xs = {"cant": 352, "precio": 420, "dto": 466, "imp": W - m}
    yt = y0 - 112
    rect(c, m, yt - 6, W - 2 * m, 21, relleno=HexColor("#E7ECF2"))
    tx(c, m + 6, yt + 1, "Concepto", "Carlito-Bold", 10, OSCURO_1)
    tx(c, xs["cant"], yt + 1, "Cant.", "Carlito-Bold", 10, OSCURO_1, "r")
    tx(c, xs["precio"], yt + 1, "Precio", "Carlito-Bold", 10, OSCURO_1, "r")
    tx(c, xs["dto"], yt + 1, "Dto.", "Carlito-Bold", 10, OSCURO_1, "r")
    tx(c, xs["imp"] - 6, yt + 1, "Importe (€)", "Carlito-Bold", 10, OSCURO_1, "r")
    y = yt - 24
    for ln in d["lineas"]:
        tx(c, m + 6, y, ln["concepto"], "Carlito", 10.5, black)
        tx(c, xs["cant"], y, ln["cant_txt"], "Carlito", 10.5, black, "r")
        tx(c, xs["precio"], y, ln["precio_txt"], "Carlito", 10.5, black, "r")
        if ln.get("dto"):
            tx(c, xs["dto"], y, f"{ln['dto']:.0f} %", "Carlito", 10.5, black, "r")
        tx(c, xs["imp"] - 6, y, num(ln["importe"]), "Carlito-Bold", 10.5, black, "r")
        alto = 22
        if ln.get("detalle"):
            tx(c, m + 6, y - 12, ln["detalle"], "Carlito-Italic", 9, HexColor("#6B7684"))
            alto = 33
        raya(c, m, y - alto + 14, W - m, y - alto + 14, HexColor("#D3DAE3"), 0.6)
        y -= alto
    # --- caja de totales oscura con texto blanco
    t = d["t"]
    bw, bh = 220, 84
    bx, by = W - m - bw, y - 14 - bh
    rect(c, bx, by, bw, bh, relleno=OSCURO_1)
    d["zonas_oscuras"] = [d["zonas_oscuras"][0], (bx, by, bw, bh)]
    yy = by + bh - 20
    for etq, val in (("Base imponible", num(t["base"]) + " €"),
                     (f"IVA {t['desglose'][0]['tipo']:.0f} %", num(t["cuota"]) + " €")):
        tx(c, bx + 14, yy, etq, "Carlito", 10.5, CLARO_1)
        tx(c, bx + bw - 14, yy, val, "Carlito", 10.5, white, "r")
        yy -= 16
    raya(c, bx + 14, yy + 7, bx + bw - 14, yy + 7, ACENTO_1, 0.8)
    tx(c, bx + 14, yy - 10, "TOTAL", "Carlito-Bold", 14, white)
    tx(c, bx + bw - 14, yy - 10, num(t["total"]) + " €", "Carlito-Bold", 15, white, "r")
    # --- pago
    yp = y - 30
    tx(c, m, yp, "Forma de pago", "Carlito-Bold", 10, OSCURO_1)
    tx(c, m, yp - 14, "Transferencia bancaria a 30 días", "Carlito", 10, black)
    tx(c, m, yp - 28, f"IBAN: {d['iban_txt']}", "Carlito", 10, black)
    tx(c, m, yp - 42, "Indique el número de factura en el concepto de la transferencia.", "Carlito-Italic", 9,
       HexColor("#6B7684"))
    # --- pie
    raya(c, m, 58, W - m, 58, HexColor("#D3DAE3"), 0.6)
    tx(c, W / 2, 44, "Audiovisuales Peña Mayor, S.L. · Inscrita en el Registro Mercantil de Asturias, tomo 4.112, "
       "folio 87, hoja AS-51207", "Carlito", 8, HexColor("#6B7684"), "c")
    tx(c, W / 2, 33, "Sus datos se tratan para la gestión de la relación comercial. Puede ejercer sus derechos en "
       "privacidad@penamayor-av.example", "Carlito", 8, HexColor("#6B7684"), "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 2. Tarjetas: emisora y cliente en cajas redondeadas lado a lado, conceptos apilados (FreeSans)
# ==============================================================================================
VERDE_2 = HexColor("#0F6E66")
FONDO_2 = HexColor("#F0F5F4")
BORDE_2 = HexColor("#C8D4D1")
GRIS_2 = HexColor("#56615F")


def doc_02() -> dict:
    lineas = [
        {"concepto": "Ilustración de cubierta para «El faro de las Columbretes»", "cantidad": D("1"),
         "precio": D("680.00"), "contiene": "Ilustración de cubierta para"},
        {"concepto": "Ilustraciones interiores a línea, blanco y negro", "cantidad": D("12"),
         "precio": D("45.00"), "contiene": "Ilustraciones interiores a línea"},
        {"concepto": "Maquetación de páginas interiores", "cantidad": D("96"), "precio": D("3.75"),
         "contiene": "Maquetación de páginas interiores"},
        {"concepto": "Adaptación de la cubierta a libro electrónico", "cantidad": D("1"), "precio": D("120.00"),
         "contiene": "Adaptación de la cubierta"},
        {"concepto": "Rondas de correcciones adicionales", "cantidad": D("2"), "precio": D("35.00"),
         "contiene": "Rondas de correcciones adicionales"},
    ]
    t = calcular(lineas, pct_ret="7")
    iban = "ES0021006734291058827316"
    return {
        "archivo": "h3c_02_tarjetas_conceptos_apilados.pdf",
        "render": render_02,
        "estilo": ("Factura de ilustradora autónoma (persona física con DNI) en FreeSans con maqueta de TARJETAS y "
                   "sin tabla: título 'Factura' en verde y dos píldoras redondeadas 'Nº' y 'Fecha' (dd.mm.aaaa), "
                   "monograma circular; emisora ('EMITIDA POR') y cliente ('FACTURADA A') lado a lado en dos cajas "
                   "redondeadas con fondo claro; sección 'Conceptos' con cada línea en una tarjeta redondeada "
                   "apilada (descripción en negrita en un renglón y debajo 'cantidad x precio € = importe €'), sin "
                   "cabecera de columnas; a la derecha tarjeta de totales Base imponible / IVA 21 % / Total factura "
                   "/ Retención IRPF 7 % (negativa) y píldora verde 'Total a pagar'; a la izquierda cajas 'Pago' "
                   "(IBAN) y 'Referencia' (proyecto), A4"),
        "empresa_nif": DNI_MONTESINOS,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "CM-26/031",
        "fecha": date(2026, 11, 4),
        "fecha_txt": "04.11.2026",
        "emisor": {"nombre": "Clara Montesinos Ribelles", "nif": DNI_MONTESINOS, "domicilio_contiene": "46006"},
        "destinatario": {"nombre": "Editorial Albufera Llibres, S.L.", "nif": CIF_ALBUFERA,
                         "domicilio_contiene": "46024"},
        "nif_txt": {"emisor": f"NIF {DNI_MONTESINOS}", "destinatario": f"CIF {CIF_ALBUFERA}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"proyecto": "ALB-2026-14"},
        "iban": iban,
        "iban_txt": iban_txt(iban),
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "permitidos": [],
        "textos": ["Factura", "EMITIDA POR", "FACTURADA A", "Conceptos", "Total a pagar", "Retención IRPF 7 %",
                   "Total factura"],
        "notas": ("Factura propia de una ilustradora autónoma (persona física con DNI; empresa usuaria = emisora) "
                  "sin marcas VERI*FACTU: documento preparatorio. No hay tabla: emisora y cliente van lado a lado "
                  "en cajas redondeadas y cada concepto es una tarjeta con la descripción en un renglón y debajo la "
                  "fórmula 'cantidad x precio € = importe €' (p. ej. '2 x 35,00 € = 70,00 €'); no hay cabecera de "
                  "columnas. Número y fecha en píldoras ('Nº CM-26/031', 'Fecha 04.11.2026', fecha con puntos). "
                  "Retención IRPF del 7 % (inicio de actividad profesional) en negativo y 'Total a pagar'. "
                  "Distractores: título del libro entre comillas angulares en el primer concepto, 'vol. 3', 'Tel.', "
                  "nota sobre el art. 101.5 de la Ley del IRPF. Base 1.770,00 x 21 % = 371,70; total factura "
                  "2.141,70; IRPF 7 % = 123,90; total a pagar 2.017,80."),
    }


def render_02(ruta: Path, d: dict):
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", d["emisor"]["nombre"])
    m = 40
    # --- título, píldoras y monograma
    tx(c, m, H - 72, "Factura", "FreeSans-Bold", 30, VERDE_2)
    px = m
    for etq, val in (("Nº", d["numero"]), ("Fecha", d["fecha_txt"])):
        ancho = pdfmetrics.stringWidth(f"{etq} ", F("FreeSans"), 9.5) + pdfmetrics.stringWidth(
            val, F("FreeSans-Bold"), 10) + 22
        rrect(c, px, H - 108, ancho, 21, 10.5, relleno=FONDO_2, borde=BORDE_2)
        tx(c, px + 11, H - 101, etq, "FreeSans", 9.5, GRIS_2)
        tx(c, px + 11 + pdfmetrics.stringWidth(f"{etq} ", F("FreeSans"), 9.5), H - 101, val, "FreeSans-Bold", 10,
           black)
        px += ancho + 8
    c.setFillColor(VERDE_2)
    c.circle(W - m - 26, H - 62, 26, fill=1, stroke=0)
    tx(c, W - m - 26, H - 69, "CM", "FreeSans-Bold", 18, white, "c")
    # --- cajas de las partes
    top = H - 128
    ancho = (W - 2 * m - 14) / 2
    alto = 120
    for i, (etq, filas) in enumerate((
            ("EMITIDA POR", [(d["emisor"]["nombre"], "FreeSans-Bold", 11.5, black),
                             ("Ilustración y diseño editorial", "FreeSans", 9.5, GRIS_2),
                             ("Carrer Inventat 58, 2.ª", "FreeSans", 9.5, black),
                             ("46006 València", "FreeSans", 9.5, black),
                             (d["nif_txt"]["emisor"], "FreeSans", 9.5, black),
                             ("clara@montesinos-ilustra.example · Tel. 960 000 000", "FreeSans", 8.5, GRIS_2)]),
            ("FACTURADA A", [(d["destinatario"]["nombre"], "FreeSans-Bold", 11.5, black),
                             ("Departamento de producción", "FreeSans", 9.5, GRIS_2),
                             ("Avinguda Imaginària 112, entresòl", "FreeSans", 9.5, black),
                             ("46024 València", "FreeSans", 9.5, black),
                             (d["nif_txt"]["destinatario"], "FreeSans", 9.5, black),
                             ("produccion@albufera-llibres.example", "FreeSans", 8.5, GRIS_2)]))):
        x = m + i * (ancho + 14)
        rrect(c, x, top - alto, ancho, alto, 12, relleno=FONDO_2, borde=BORDE_2)
        tx(c, x + 14, top - 18, etq, "FreeSans-Bold", 7.5, VERDE_2)
        y = top - 36
        for s, f, tam, col in filas:
            tx(c, x + 14, y, s, f, tam, col)
            y -= 14 if f.endswith("Bold") else 13
    # --- conceptos como tarjetas apiladas
    y = top - alto - 32
    tx(c, m, y, "Conceptos", "FreeSans-Bold", 13, black)
    tx(c, W - m, y, f"{len(d['lineas'])} conceptos · importes en euros", "FreeSans", 8.5, GRIS_2, "r")
    y -= 12
    for ln in d["lineas"]:
        h_t = 42
        rrect(c, m, y - h_t, W - 2 * m, h_t, 9, relleno=white, borde=BORDE_2)
        rect(c, m + 6, y - h_t + 9, 3, h_t - 18, relleno=VERDE_2)
        tx(c, m + 18, y - 17, ln["concepto"], "FreeSans-Bold", 10.5, black)
        ln["formula"] = f"{ln['cant_txt']} x {ln['precio_txt']} € = {num(ln['importe'])} €"
        tx(c, m + 18, y - 32, ln["formula"], "FreeSans", 9.5, GRIS_2)
        y -= h_t + 8
    # --- totales (tarjeta a la derecha)
    t = d["t"]
    tw, th = 236, 124
    tx0, ty0 = W - m - tw, y - 10 - th
    rrect(c, tx0, ty0, tw, th, 12, relleno=FONDO_2, borde=BORDE_2)
    yy = ty0 + th - 20
    for etq, val in (("Base imponible", num(t["base"]) + " €"),
                     (f"IVA {t['desglose'][0]['tipo']:.0f} %", num(t["cuota"]) + " €"),
                     ("Total factura", num(t["total"]) + " €"),
                     (f"Retención IRPF {t['pct_ret']:.0f} %", "-" + num(t["ret"]) + " €")):
        tx(c, tx0 + 14, yy, etq, "FreeSans", 10, GRIS_2 if etq != "Total factura" else black)
        tx(c, tx0 + tw - 14, yy, val, "FreeSans-Bold" if etq == "Total factura" else "FreeSans", 10, black, "r")
        yy -= 17
    rrect(c, tx0 + 8, ty0 + 8, tw - 16, 30, 15, relleno=VERDE_2)
    tx(c, tx0 + 22, ty0 + 19, "Total a pagar", "FreeSans-Bold", 11, white)
    tx(c, tx0 + tw - 22, ty0 + 19, num(t["pagar"]) + " €", "FreeSans-Bold", 13, white, "r")
    # --- pago y referencia (cajas a la izquierda)
    lw = W - 2 * m - tw - 14
    ya = ty0 + th
    for etq, filas in (("Pago", ["Transferencia bancaria", f"IBAN {d['iban_txt']}"]),
                       ("Referencia", [f"Proyecto {d['referencias']['proyecto']}",
                                       "Colección Mar Endins, vol. 3"])):
        rrect(c, m, ya - 56, lw, 56, 10, relleno=white, borde=BORDE_2)
        tx(c, m + 12, ya - 16, etq.upper(), "FreeSans-Bold", 7.5, VERDE_2)
        tx(c, m + 12, ya - 31, filas[0], "FreeSans", 9.5, black)
        tx(c, m + 12, ya - 45, filas[1], "FreeSans", 9.5, black)
        ya -= 68
    # --- pie
    tx(c, m, 58, "Retención del 7 % por inicio de actividad profesional (art. 101.5 de la Ley del IRPF).",
       "FreeSans", 8, GRIS_2)
    tx(c, m, 46, "Gracias por confiar en mi trabajo.", "FreeSans", 8, GRIS_2)
    c.showPage()
    c.save()


# ==============================================================================================
# 3. Taller mecánico — foto de móvil de la página (solo imagen, 150 ppp, sombra degradada)
# ==============================================================================================
def doc_03() -> dict:
    lineas = [
        {"ref": "FR-DEL-311", "concepto": "Pastillas de freno delanteras", "cantidad": D("1"),
         "precio": D("86.40"), "contiene": "Pastillas de freno delanteras"},
        {"ref": "DF-VEN-208", "concepto": "Discos de freno ventilados (par)", "cantidad": D("1"),
         "precio": D("142.90"), "contiene": "Discos de freno ventilados"},
        {"ref": "AC-5W30", "concepto": "Aceite de motor sintético 5W30 (litros)", "cantidad": D("7.5"),
         "precio": D("11.20"), "contiene": "Aceite de motor sintético"},
        {"ref": "FO-114", "concepto": "Filtro de aceite", "cantidad": D("1"), "precio": D("14.35"),
         "contiene": "Filtro de aceite"},
        {"ref": "FA-209", "concepto": "Filtro de aire", "cantidad": D("1"), "precio": D("22.80"),
         "contiene": "Filtro de aire"},
        {"ref": "MO", "concepto": "Mano de obra mecánica (horas)", "cantidad": D("3.5"), "precio": D("48.00"),
         "contiene": "Mano de obra mecánica"},
    ]
    t = calcular(lineas)
    return {
        "archivo": "h3c_03_foto_movil_150ppp_sombra.pdf",
        "render": render_03,
        "escaneado": "foto",
        "ppp": 150,
        "estilo": ("Factura RECIBIDA de taller mecánico FOTOGRAFIADA CON EL MÓVIL (PDF solo imagen JPEG a 150 ppp "
                   "en gris, sin capa de texto): página entera sobre fondo oscuro de mesa, girada 1,3°, sombra "
                   "degradada de izquierda (oscura) a derecha, ligero desenfoque y ruido; maqueta de origen en "
                   "LiberationSans: emisor arriba a la izquierda, recuadro 'FACTURA' con Nº y Fecha (dd-mm-aaaa) a "
                   "la derecha, recuadros 'Cliente' y 'Vehículo' (matrícula, km, O.R., fecha de entrada), tabla con "
                   "rejilla Ref. | Descripción | Cant. | Precio | Importe, totales Base imponible / I.V.A. 21 % / "
                   "TOTAL FACTURA, forma de pago 'Contado' y firma del cliente, A4"),
        "empresa_nif": CIF_ALMANSA,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "2026/T-0758",
        "fecha": date(2026, 10, 27),
        "fecha_txt": "27-10-2026",
        "emisor": {"nombre": "Talleres Cebrián Llanos, S.L.", "nif": CIF_CEBRIAN, "domicilio_contiene": "02007"},
        "destinatario": {"nombre": "Transportes y Mudanzas Llano de Almansa, S.L.", "nif": CIF_ALMANSA,
                         "domicilio_contiene": "02640"},
        "nif_txt": {"emisor": f"C.I.F. {CIF_CEBRIAN}", "destinatario": f"C.I.F.: {CIF_ALMANSA}"},
        "lineas": lineas,
        "t": t,
        "referencias": {},
        "iban": None,
        "iban_txt": None,
        "forma_pago_contiene": "Contado",
        "indicios": [],
        "permitidos": [],
        "textos": ["FACTURA", "Cliente", "Vehículo", "Base imponible", "I.V.A. 21 %", "TOTAL FACTURA"],
        "notas": ("La sube la empresa DESTINATARIA (transportista): factura recibida de un taller, fotografiada con "
                  "el móvil. PDF solo imagen (requiere OCR): rasterizado con pdftoppm a 150 ppp en gris, colocado "
                  "sobre un fondo oscuro (borde de la mesa visible), girado 1,3°, con una sombra que oscurece la "
                  "página de izquierda a derecha (factor 0,50 en el borde izquierdo, 1,0 en el derecho), "
                  "desenfoque gaussiano y ruido, guardado como JPEG calidad 72 en un PDF sin fuentes. Cantidades "
                  "decimales 7,5 l y 3,5 h. Sin IBAN: pago al contado con tarjeta. Sin referencias esperadas (la "
                  "orden de reparación OR-1187 no es un pedido). Distractores: matrícula 4821 LMT, 'Km: 187.430', "
                  "fecha de entrada 24-10-2026, garantía '3 meses o 2.000 km', '5W30'. Base 518,45 x 21 % = "
                  "108,87 (108,8745); total 627,32."),
    }


def render_03(ruta: Path, d: dict):
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", d["emisor"]["nombre"])
    m = 46
    gris = HexColor("#444444")
    # --- emisor
    tx(c, m, H - 70, d["emisor"]["nombre"], "LibSans-Bold", 16)
    tx(c, m, H - 86, "Mecánica · Electricidad · Neumáticos", "LibSans-Italic", 10, gris)
    y = H - 104
    for s in ("Polígono Inventado, calle E, n.º 23", "02007 Albacete", d["nif_txt"]["emisor"],
              "Tel. 960 000 000 · taller@cebrianllanos.example"):
        tx(c, m, y, s, "LibSans", 10)
        y -= 13
    # --- recuadro factura
    bx, bw = W - m - 190, 190
    rect(c, bx, H - 140, bw, 82, borde=black, grosor=1.2)
    rect(c, bx, H - 82, bw, 24, relleno=HexColor("#DDDDDD"), borde=black, grosor=1.2)
    tx(c, bx + bw / 2, H - 76, "FACTURA", "LibSans-Bold", 15, black, "c")
    tx(c, bx + 12, H - 104, "Nº:", "LibSans", 11)
    tx(c, bx + bw - 12, H - 104, d["numero"], "LibSans-Bold", 12, black, "r")
    tx(c, bx + 12, H - 126, "Fecha:", "LibSans", 11)
    tx(c, bx + bw - 12, H - 126, d["fecha_txt"], "LibSans-Bold", 12, black, "r")
    # --- cliente y vehículo
    yb = H - 178
    cw = (W - 2 * m - 16) / 2
    rect(c, m, yb - 84, cw, 84, borde=black, grosor=0.8)
    tx(c, m + 8, yb - 14, "Cliente", "LibSans-Bold", 10)
    y = yb - 30
    for s in (d["destinatario"]["nombre"], "Calle Ficticia 88", "02640 Almansa (Albacete)",
              d["nif_txt"]["destinatario"]):
        tx(c, m + 8, y, s, "LibSans", 10)
        y -= 13
    vx = m + cw + 16
    rect(c, vx, yb - 84, cw, 84, borde=black, grosor=0.8)
    tx(c, vx + 8, yb - 14, "Vehículo", "LibSans-Bold", 10)
    y = yb - 30
    for etq, val in (("Modelo:", "Furgoneta de reparto 3.500 kg"), ("Matrícula:", "4821 LMT"),
                     ("Km:", "187.430"), ("O.R.:", "OR-1187 · entrada 24-10-2026")):
        tx(c, vx + 8, y, etq, "LibSans", 10, gris)
        tx(c, vx + 68, y, val, "LibSans", 10)
        y -= 13
    # --- tabla con rejilla
    cols = [m, m + 78, m + 318, m + 368, m + 430, W - m]
    yt = yb - 112
    filas = len(d["lineas"])
    alto_f = 21
    rect(c, m, yt - 6, W - 2 * m, 20, relleno=HexColor("#DDDDDD"))
    for etq, i, al in (("Ref.", 0, "l"), ("Descripción", 1, "l"), ("Cant.", 3, "r"), ("Precio", 4, "r"),
                       ("Importe", 5, "r")):
        x = cols[i] + 6 if al == "l" else cols[i] - 6
        tx(c, x, yt, etq, "LibSans-Bold", 10, black, al)
    y_fin = yt - 6 - filas * alto_f - 40
    c.setStrokeColor(black)
    c.setLineWidth(0.7)
    c.rect(m, y_fin, W - 2 * m, yt + 14 - y_fin, fill=0, stroke=1)
    raya(c, m, yt - 6, W - m, yt - 6, black, 0.7)
    for x in cols[1:-1]:
        if x == cols[2]:
            continue
        raya(c, x, y_fin, x, yt + 14, black, 0.7)
    y = yt - 22
    for ln in d["lineas"]:
        tx(c, cols[0] + 6, y, ln["ref"], "LibSans", 10)
        tx(c, cols[1] + 6, y, ln["concepto"], "LibSans", 10)
        tx(c, cols[3] - 6, y, ln["cant_txt"], "LibSans", 10, black, "r")
        tx(c, cols[4] - 6, y, ln["precio_txt"], "LibSans", 10, black, "r")
        tx(c, cols[5] - 6, y, num(ln["importe"]), "LibSans", 10, black, "r")
        y -= alto_f
    # --- totales
    t = d["t"]
    yy = y_fin - 22
    for etq, val, f, tam in (("Base imponible", num(t["base"]), "LibSans", 10.5),
                             (f"I.V.A. {t['desglose'][0]['tipo']:.0f} %", num(t["cuota"]), "LibSans", 10.5),
                             ("TOTAL FACTURA", num(t["total"]) + " €", "LibSans-Bold", 12.5)):
        tx(c, W - m - 190, yy, etq, f, tam)
        tx(c, W - m - 6, yy, val, f, tam, black, "r")
        yy -= 18
    raya(c, W - m - 190, yy + 32, W - m, yy + 32, black, 0.8)
    # --- pago, garantía y firma
    tx(c, m, y_fin - 22, "Forma de pago: Contado (tarjeta)", "LibSans", 10.5)
    tx(c, m, y_fin - 38, "Garantía de la reparación: 3 meses o 2.000 km.", "LibSans", 10, gris)
    rect(c, m, y_fin - 132, 210, 70, borde=black, grosor=0.6)
    tx(c, m + 8, y_fin - 76, "Conforme cliente:", "LibSans", 9.5, gris)
    c.setStrokeColor(HexColor("#2A3F8F"))
    c.setLineWidth(1.1)
    c.bezier(m + 40, y_fin - 116, m + 70, y_fin - 92, m + 95, y_fin - 128, m + 150, y_fin - 104)
    tx(c, W / 2, 50, "Talleres Cebrián Llanos, S.L. · Registro Mercantil de Albacete, hoja AB-9184", "LibSans", 8,
       gris, "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 4. Suministros agrícolas — recibida por FAX (solo imagen, 200 ppp, 1 bit, sal y pimienta)
# ==============================================================================================
def doc_04() -> dict:
    lineas = [
        {"ref": "AB-15151525", "concepto": "Abono complejo NPK 15-15-15, saco 25 kg", "cantidad": D("20"),
         "precio": D("18.60"), "iva": D("10"), "contiene": "Abono complejo NPK 15-15-15"},
        {"ref": "SU-SEM70", "concepto": "Sustrato universal para semillero, saco 70 l", "cantidad": D("15"),
         "precio": D("9.80"), "iva": D("10"), "contiene": "Sustrato universal para semillero"},
        {"ref": "SE-TOM1000", "concepto": "Semilla de tomate híbrido, sobre 1.000 ud", "cantidad": D("6"),
         "precio": D("24.30"), "iva": D("10"), "contiene": "Semilla de tomate híbrido"},
        {"ref": "RG-CG16-500", "concepto": "Cinta de riego por goteo 16 mm, rollo 500 m", "cantidad": D("4"),
         "precio": D("62.50"), "iva": D("21"), "contiene": "Cinta de riego por goteo"},
        {"ref": "HT-TIJ-PRO", "concepto": "Tijera de poda profesional", "cantidad": D("3"), "precio": D("27.90"),
         "iva": D("21"), "contiene": "Tijera de poda profesional"},
        {"ref": "EP-GN9-12", "concepto": "Guantes de nitrilo talla 9, caja 12 pares", "cantidad": D("5"),
         "precio": D("16.40"), "iva": D("21"), "contiene": "Guantes de nitrilo talla"},
    ]
    t = calcular(lineas)
    iban = "ES0030580417662095138804"
    return {
        "archivo": "h3c_04_fax_200ppp_binarizado.pdf",
        "render": render_04,
        "escaneado": "fax",
        "ppp": 200,
        "estilo": ("Factura RECIBIDA por FAX (PDF solo imagen en blanco y negro puro de 1 bit, CCITT G4, a 200 ppp, "
                   "sin capa de texto): rasterizada en gris, girada 0,35°, binarizada con umbral, con ruido de sal y "
                   "pimienta y alguna raya horizontal de transmisión; cabecera de fax en monoespaciada con fecha y "
                   "hora de envío, teléfono del remitente y 'PAG. 001/001'; maqueta de origen en DejaVuSans: emisor "
                   "arriba a la izquierda, título 'FACTURA' recuadrado, rejilla Nº factura / Fecha / Cód. cliente / "
                   "Vencimiento, recuadro 'Cliente', tabla con rejilla Artículo | Descripción | Cantidad | Precio | "
                   "% IVA | Importe con tipos 10 % y 21 % mezclados, cuadro de desglose por tipo (Base imponible / "
                   "% IVA / Cuota) y totales a la derecha, forma de pago, IBAN y albarán, A4"),
        "empresa_nif": CIF_COOP,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "FV26-004912",
        "fecha": date(2026, 11, 3),
        "fecha_txt": "03/11/2026",
        "emisor": {"nombre": "Suministros Agrícolas Vega del Segura, S.L.", "nif": CIF_VEGASEGURA,
                   "domicilio_contiene": "30500"},
        "destinatario": {"nombre": "Cooperativa Agrícola Vega de Santomera, S. Coop.", "nif": CIF_COOP,
                         "domicilio_contiene": "30140"},
        "nif_txt": {"emisor": f"CIF: {CIF_VEGASEGURA}", "destinatario": f"CIF: {CIF_COOP}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"albaran": "AL-26-3391"},
        "iban": iban,
        "iban_txt": iban_txt(iban),
        "forma_pago_contiene": "Transferencia a 45 días",
        "indicios": [],
        "permitidos": [r"Abono complejo NPK"],   # «abono» = fertilizante, no una factura de abono
        "textos": ["FACTURA", "Cliente", "Base imponible", "TOTAL FACTURA", "PAG. 001/001"],
        "notas": ("La sube la empresa DESTINATARIA (cooperativa agrícola, CIF con letra F): factura de un "
                  "proveedor recibida por fax. PDF solo imagen (requiere OCR): rasterizado con pdftoppm a 200 ppp "
                  "en gris, girado 0,35°, binarizado a blanco y negro puro (umbral 150 sobre 255), con ruido de sal "
                  "y pimienta y tres rayas horizontales finas de transmisión, guardado como imagen de 1 bit con "
                  "compresión CCITT G4 en un PDF sin fuentes. Dos tipos de IVA mezclados por línea (columna "
                  "'% IVA'): 10 % para abono, sustrato y semillas; 21 % para riego, herramienta y guantes. "
                  "La primera línea empieza por 'Abono complejo NPK': aquí «abono» es el fertilizante, NO una "
                  "factura de abono ni una rectificativa (no hay importes negativos). "
                  "Distractores: cabecera de transmisión del fax con fecha 04/11/2026 12:07 y 'PAG. 001/001', "
                  "fax del emisor, código de cliente C-0417, vencimiento 18/12/2026, albarán de 31/10/2026, "
                  "'15-15-15', '25 kg', '70 l', '1.000 ud', '16 mm', '500 m', 'talla 9', '12 pares'. "
                  "10 %: 664,80 -> 66,48; 21 %: 415,70 -> 87,30 (87,297); base 1.080,50, IVA 153,78, "
                  "total 1.234,28."),
    }


def render_04(ruta: Path, d: dict):
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", d["emisor"]["nombre"])
    m = 44
    # --- cabecera de transmisión del fax
    tx(c, m - 14, H - 20, "04/11/2026 12:07   DE: 970 000 000   SUMIN.AGRICOLAS VEGA SEGURA          PAG. 001/001",
       "LibMono", 9)
    raya(c, m - 14, H - 25, W - m + 14, H - 25, black, 0.5)
    # --- emisor
    tx(c, m, H - 62, d["emisor"]["nombre"], "DejaVuSans-Bold", 13.5)
    y = H - 80
    for s in ("Carretera Inventada 17", "30500 Molina de Segura (Murcia)", d["nif_txt"]["emisor"],
              "Tel. 960 000 000 · Fax 970 000 000", "pedidos@vegasegura-agro.example"):
        tx(c, m, y, s, "DejaVuSans", 10)
        y -= 13.5
    # --- título
    rect(c, W - m - 160, H - 92, 160, 36, borde=black, grosor=1.6)
    tx(c, W - m - 80, H - 81, "FACTURA", "DejaVuSans-Bold", 19, black, "c")
    # --- rejilla de metadatos
    yg = H - 170
    cel = (W - 2 * m) / 4
    etqs = (("Nº FACTURA", d["numero"]), ("FECHA", d["fecha_txt"]), ("CÓD. CLIENTE", "C-0417"),
            ("VENCIMIENTO", "18/12/2026"))
    for i, (etq, val) in enumerate(etqs):
        x = m + i * cel
        rect(c, x, yg - 36, cel, 36, borde=black, grosor=1.0)
        raya(c, x, yg - 16, x + cel, yg - 16, black, 1.0)
        tx(c, x + cel / 2, yg - 12, etq, "DejaVuSans-Bold", 8.5, black, "c")
        tx(c, x + cel / 2, yg - 31, val, "DejaVuSans", 11, black, "c")
    # --- cliente
    yc = yg - 52
    rect(c, m + cel * 2, yc - 82, cel * 2, 82, borde=black, grosor=1.0)
    tx(c, m + cel * 2 + 8, yc - 14, "Cliente:", "DejaVuSans-Bold", 9.5)
    y = yc - 30
    for s in (d["destinatario"]["nombre"], "Calle Imaginaria 140", "30140 Santomera (Murcia)",
              d["nif_txt"]["destinatario"]):
        tx(c, m + cel * 2 + 8, y, s, "DejaVuSans", 9.5)
        y -= 13
    tx(c, m, yc - 30, "Albarán nº AL-26-3391 de 31/10/2026", "DejaVuSans", 10)
    tx(c, m, yc - 46, "Entrega en el almacén de la cooperativa", "DejaVuSans", 10)
    # --- tabla
    cols = [m, m + 88, m + 300, m + 362, m + 420, m + 460, W - m]
    yt = yc - 112
    alto_f = 22
    y_fin = yt - 8 - len(d["lineas"]) * alto_f - 14
    c.setStrokeColor(black)
    c.setLineWidth(1.0)
    c.rect(m, y_fin, W - 2 * m, yt + 14 - y_fin, fill=0, stroke=1)
    raya(c, m, yt - 7, W - m, yt - 7, black, 1.0)
    for x in cols[1:-1]:
        raya(c, x, y_fin, x, yt + 14, black, 0.8)
    for etq, i, al in (("Artículo", 0, "l"), ("Descripción", 1, "l"), ("Cantidad", 3, "r"), ("Precio", 4, "r"),
                       ("% IVA", 5, "r"), ("Importe", 6, "r")):
        x = cols[i] + 5 if al == "l" else cols[i] - 5
        tx(c, x, yt, etq, "DejaVuSans-Bold", 9, black, al)
    y = yt - 24
    for ln in d["lineas"]:
        tx(c, cols[0] + 5, y, ln["ref"], "DejaVuSans", 8.5)
        tx(c, cols[1] + 5, y, ln["concepto"], "DejaVuSans", 8.5)
        tx(c, cols[3] - 5, y, ln["cant_txt"], "DejaVuSans", 9.5, black, "r")
        tx(c, cols[4] - 5, y, ln["precio_txt"], "DejaVuSans", 9.5, black, "r")
        tx(c, cols[5] - 5, y, f"{ln['iva']:.0f}", "DejaVuSans", 9.5, black, "r")
        tx(c, cols[6] - 5, y, num(ln["importe"]), "DejaVuSans", 9.5, black, "r")
        y -= alto_f
    # --- desglose y totales
    t = d["t"]
    yd = y_fin - 24
    dx = [m, m + 102, m + 156, m + 236]
    rect(c, m, yd - 18 - 20 * len(t["desglose"]), dx[3] - m, 18 + 20 * len(t["desglose"]) + 14, borde=black,
         grosor=1.0)
    raya(c, m, yd - 6, dx[3], yd - 6, black, 1.0)
    for etq, i in (("Base imponible", 1), ("% IVA", 2), ("Cuota", 3)):
        tx(c, dx[i] - 6, yd, etq, "DejaVuSans-Bold", 9, black, "r")
    yy = yd - 22
    for g in t["desglose"]:
        tx(c, dx[1] - 6, yy, num(g["base"]), "DejaVuSans", 10, black, "r")
        tx(c, dx[2] - 6, yy, f"{g['tipo']:.0f} %", "DejaVuSans", 10, black, "r")
        tx(c, dx[3] - 6, yy, num(g["cuota"]), "DejaVuSans", 10, black, "r")
        yy -= 20
    rx = W - m - 232
    yy = yd
    for etq, val, f, tam in (("Total base imponible", num(t["base"]), "DejaVuSans", 10.5),
                             ("Total cuota IVA", num(t["cuota"]), "DejaVuSans", 10.5)):
        tx(c, rx, yy, etq, f, tam)
        tx(c, W - m, yy, val, f, tam, black, "r")
        yy -= 18
    rect(c, rx - 8, yy - 14, W - m - rx + 8 + 6, 26, borde=black, grosor=1.6)
    tx(c, rx, yy - 5, "TOTAL FACTURA", "DejaVuSans-Bold", 12)
    tx(c, W - m, yy - 5, num(t["total"]) + " EUR", "DejaVuSans-Bold", 12, black, "r")
    yp = yd - 18 - 20 * len(t["desglose"]) - 34
    tx(c, m, yp, "Forma de pago: Transferencia a 45 días fecha factura", "DejaVuSans", 10)
    tx(c, m, yp - 16, f"IBAN: {d['iban_txt']}", "DejaVuSans", 10)
    tx(c, W / 2, 46, "Suministros Agrícolas Vega del Segura, S.L. · Registro Mercantil de Murcia, hoja MU-77310",
       "DejaVuSans", 7.5, black, "c")
    c.showPage()
    c.save()


DOCUMENTOS = [doc_01, doc_02, doc_03, doc_04]


# ----------------------------------------------------------------------------------------------
# escaneado simulado
# ----------------------------------------------------------------------------------------------
FECHA_PDF = time.struct_time((2026, 11, 4, 12, 9, 0, 2, 308, 0))


def _rasterizar(pdf_limpio: Path, tmp: Path, ppp: int, nombre: str) -> Image.Image:
    prefijo = tmp / nombre
    subprocess.run(["pdftoppm", "-r", str(ppp), "-gray", "-png", "-singlefile", str(pdf_limpio), str(prefijo)],
                   check=True)
    return Image.open(prefijo.with_suffix(".png")).convert("L")


def foto_movil(pdf_limpio: Path, destino: Path, tmp: Path, semilla: int, ppp: int) -> None:
    """150 ppp gris; página sobre fondo de mesa, giro 1,3°, sombra degradada izquierda->derecha, ruido, JPEG."""
    rng = np.random.default_rng(semilla)
    pag = _rasterizar(pdf_limpio, tmp, ppp, "foto_pagina")
    papel, tinta = 238, 36
    pag = pag.point(lambda v: tinta + v * (papel - tinta) // 255)
    mx, my = 44, 56
    w, h = pag.size[0] + 2 * mx, pag.size[1] + 2 * my
    mesa = 68
    img = Image.new("L", (w, h), mesa)
    img.paste(pag, (mx, my))
    img = img.rotate(1.3, resample=Image.BICUBIC, expand=False, fillcolor=mesa)
    arr = np.asarray(img).astype(np.float64)
    xs = np.linspace(0.0, 1.0, w)
    ys = np.linspace(0.0, 1.0, h)
    horiz = 0.50 + 0.50 * xs ** 0.75                    # sombra de izquierda (oscura) a derecha
    vert = 1.0 - 0.07 * ys                              # algo más oscuro abajo
    arr = arr * horiz[None, :] * vert[:, None]
    arr = arr + rng.normal(0.0, 4.0, size=arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(0.6))
    jpg = tmp / "foto.jpg"
    img.save(jpg, "JPEG", quality=72)
    Image.open(jpg).save(destino, "PDF", resolution=float(ppp), title="IMG_20261028_093412",
                         creator="Cámara de teléfono móvil (sintético)", producer="Corpus sintético verifactu-app",
                         subject="Documento sintético de prueba (datos ficticios)", creationDate=FECHA_PDF,
                         modDate=FECHA_PDF)


def fax(pdf_limpio: Path, destino: Path, tmp: Path, semilla: int, ppp: int) -> None:
    """200 ppp; giro leve, umbral a 1 bit, sal y pimienta y rayas de transmisión; CCITT G4."""
    rng = np.random.default_rng(semilla)
    img = _rasterizar(pdf_limpio, tmp, ppp, "fax_pagina")
    img = img.rotate(-0.35, resample=Image.BICUBIC, expand=False, fillcolor=255)
    arr = np.asarray(img)
    negro = arr < 150
    hh, ww = negro.shape
    pimienta = rng.random(negro.shape) < 0.0022        # puntos negros sobre el blanco
    sal = (rng.random(negro.shape) < 0.035) & negro     # huecos blancos dentro del trazo
    negro = (negro | pimienta) & ~sal
    for _ in range(140):                               # grumos de 2x2 px
        y, x = int(rng.integers(0, hh - 2)), int(rng.integers(0, ww - 2))
        negro[y:y + 2, x:x + 2] = True
    # rayas horizontales finas de transmisión, solo en renglones en blanco (no deben parecer tachaduras)
    tinta = (arr < 150).mean(axis=1)
    libres = [y for y in range(int(hh * 0.12), int(hh * 0.95)) if tinta[max(0, y - 12):y + 13].max() < 0.002]
    for _ in range(3):
        y = libres[int(rng.integers(0, len(libres)))]
        x0 = int(rng.integers(0, ww // 2))
        x1 = int(rng.integers(x0 + ww // 6, ww))
        negro[y, x0:x1] = True
    bw = Image.fromarray(np.where(negro, 0, 255).astype(np.uint8), "L").convert("1", dither=Image.NONE)
    bw.save(tmp / "fax.png")
    bw.save(destino, "PDF", resolution=float(ppp), title="FAX_20261104_1207",
            creator="Servidor de fax (sintético)", producer="Corpus sintético verifactu-app",
            subject="Documento sintético de prueba (datos ficticios)", creationDate=FECHA_PDF, modDate=FECHA_PDF)


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
    "recargo_equivalencia": r"recargo\s+(?:de\s+)?equivalencia|\bR\.\s?E\.",
    "suplidos": r"\bsuplidos?\b",
    "descuento": r"\bdescuentos?\b|\bdto\.?\b|\bdiscount\b",
    "rectificativa": r"rectificativ|\babono\b|credit\s+note",
    "intracomunitaria": r"intracomunitari|\bVIES\b|reverse\s+charge",
    "criterio_caja": r"criterio\s+de\s+caja",
    "regimen_especial": r"r[eé]gimen\s+especial|agencias\s+de\s+viaje|bienes\s+usados|\bREAGYP\b",
    "verifactu_existente": r"VERI\*?FACTU|QR\s+tributario|Factura\s+verificable",
    "otro_tipo_documento": r"pro\s*-?\s*forma|presupuesto|quote\b(?!\s+the)",
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
    return re.search(r"(?<![\d.,])" + re.escape(s.casefold()) + r"(?![\d,])", esp) is not None


def pdftotext(ruta: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(ruta), "-"], capture_output=True, text=True,
                          check=True).stdout


def num_paginas(ruta: Path) -> int:
    info = subprocess.run(["pdfinfo", str(ruta)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))


def coherencia(e: dict, d: dict) -> list[str]:
    """Aritmética exacta y coherencia interna del JSON esperado."""
    fallos: list[str] = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    for parte in ("emisor", "destinatario"):
        debe(es_valido(e[parte]["nif"]), f"NIF {parte} inválido {e[parte]['nif']}")
        debe(normalizar(e[parte]["nif"]) == e[parte]["nif"], f"NIF {parte} no normalizado")
        debe(re.fullmatch(r"\d{5}", e[parte]["domicilio_contiene"]) is not None, f"CP {parte}")
        debe(nif_ficticio(e[parte]["nif"]) or e[parte]["nif"] in NIF_ESCANEADOS,
             f"NIF {parte} {e[parte]['nif']} no evidentemente ficticio")
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
    debe(date.fromisoformat(e["fecha"]) <= FECHA_GENERACION, "fecha posterior a la generación")
    debe(e["iban"] is None or re.fullmatch(r"ES00\d{20}", e["iban"]) is not None, "IBAN no evidentemente ficticio")
    debe(e["archivo"].startswith(PREFIJO) and e["archivo"].endswith(".pdf"), "nombre de archivo")

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
        lin = sum((D(x["importe"]) for ln, x in zip(d["lineas"], e["lineas"]) if D(ln["iva"]) == D(g["tipo_iva"])),
                  D("0"))
        debe(lin == D(g["base"]), f"líneas al {g['tipo_iva']} != base del tipo")
    debe(base + cuota == total, "base + cuota != total")
    for ln, x in zip(d["lineas"], e["lineas"]):
        if x["cantidad"] is not None:
            dto = D(ln.get("dto") or 0)
            calc = q2(D(x["cantidad"]) * D(x["precio"]) * (100 - dto) / 100)
            debe(calc == D(x["importe"]), f"línea {x['concepto_contiene']}: {calc} != {x['importe']}")
            debe(sin_empate(ln["cantidad"]) and sin_empate(ln["precio"]),
                 f"empate de redondeo en cantidad/precio de {x['concepto_contiene']}")
            debe(D(x["cantidad"]) == D(ln["cantidad"]) and D(x["precio"]) == D(ln["precio"]),
                 f"cantidad/precio del JSON de {x['concepto_contiene']} (deben ser exactos a 2 decimales)")
        debe((x["cantidad"] is None) == (x["precio"] is None), f"cantidad/precio incompletos {x['concepto_contiene']}")
        debe(_esp(ln["primera"]).startswith(_esp(ln["contiene"])),
             f"concepto_contiene no es el comienzo del primer renglón: {ln['contiene']!r} / {ln['primera']!r}")
    conts = [norm(x["concepto_contiene"]) for x in e["lineas"]]
    debe(len(set(conts)) == len(conts), "concepto_contiene repetido")
    if e["retencion"]:
        r = D(e["retencion"]["importe"])
        debe(q2(base * D(e["retencion"]["porcentaje"]) / 100) == r, "retención mal calculada")
        debe(total - r == D(e["total_a_pagar"]), "total - retención != total a pagar")
    else:
        debe(e["total_a_pagar"] is None, "total_a_pagar debe ser null sin retención")
    return fallos


def verificar_texto(texto: str, e: dict, d: dict) -> list[str]:
    """La capa de texto (pdftotext -layout) debe mostrar todo lo que el JSON espera."""
    fallos: list[str] = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    esp, plano, nt, fmt = _esp(texto), _plano(texto), norm(texto), num
    debe(e["numero"] in texto, f"número {e['numero']}")
    debe(_esp(d["fecha_txt"]) in esp, f"fecha {d['fecha_txt']}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(_esp(d["nif_txt"][parte]) in esp, f"NIF {parte} impreso {d['nif_txt'][parte]!r}")
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        debe(_esp(p["nombre"]) in esp, f"nombre {parte} {p['nombre']}")
        debe(re.search(rf"(?<!\d){p['domicilio_contiene']}(?!\d)", texto) is not None,
             f"domicilio {parte} {p['domicilio_contiene']}")
    impresos = {n for n, _, _ in buscar_nifs(texto)}
    debe(impresos == {e["emisor"]["nif"], e["destinatario"]["nif"]}, f"NIF impresos {impresos}")
    for ln in d["lineas"]:
        debe(_esp(ln["contiene"]) in esp, f"concepto {ln['contiene']}")
        debe(_esp(ln["primera"]) in esp, f"primer renglón {ln['primera']}")
        debe(_hay_importe(fmt(ln["importe"]), esp), f"importe de línea {fmt(ln['importe'])}")
        debe(_hay_importe(ln["precio_txt"], esp), f"precio de línea {ln['precio_txt']}")
        debe(_hay_importe(ln["cant_txt"], esp), f"cantidad {ln['cant_txt']}")
    t = d["t"]
    for g in t["desglose"]:
        debe(_hay_importe(fmt(g["base"]), esp), f"base al {g['tipo']} {fmt(g['base'])}")
        debe(_hay_importe(fmt(g["cuota"]), esp), f"cuota al {g['tipo']} {fmt(g['cuota'])}")
        debe(re.search(rf"(?<![\d,]){g['tipo']:.0f}(?:,00)?\s*%", texto) is not None, f"tipo {g['tipo']:.0f} %")
    for k in ("base", "cuota", "total", "ret", "pagar"):
        if t[k] is not None:
            debe(_hay_importe(fmt(t[k]), esp), f"{k} {fmt(t[k])}")
    if t["pct_ret"] is not None:
        debe(re.search(rf"(?<![\d,]){t['pct_ret']:.0f}\s*%", texto) is not None, "porcentaje de retención")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    if d["iban_txt"]:
        debe(_esp(d["iban_txt"]) in esp, f"IBAN impreso {d['iban_txt']}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    else:
        debe(re.search(r"ES\d{2}(?:\s?\d{4}){5}", texto) is None, "IBAN completo impreso sin esperarlo")
    debe(norm(e["forma_pago_contiene"]) in nt, f"forma de pago {e['forma_pago_contiene']}")
    for s in d.get("textos", []):
        debe(_esp(s) in esp, f"texto {s!r}")
    correos = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", texto)
    debe(bool(correos) and all(x.endswith(".example") for x in correos), f"correos no ficticios {correos}")
    telefonos = re.findall(r"(?<![\d.,])[6789]\d{2} \d{3} \d{3}(?![\d,])", texto)
    debe(bool(telefonos) and all(re.fullmatch(r"9\d0 000 000", x) for x in telefonos), f"teléfonos {telefonos}")
    filtrado = texto
    for p in d.get("permitidos", []):
        filtrado = re.sub(p, " ", filtrado, flags=re.I)
    for k, p in PATRONES_FISCALES.items():
        hay = re.search(p, filtrado, re.I) is not None
        if k in e["indicios_esperados"]:
            debe(hay, f"indicio esperado sin respaldo en el texto: {k}")
        else:
            debe(not hay, f"el texto contiene una particularidad no esperada: {k}")
    return fallos


def verificar_familia(d: dict, e: dict, ruta: Path, tmp: Path) -> list[str]:
    """Requisitos propios de cada diseño del encargo."""
    fallos: list[str] = []
    n = d["archivo"][len(PREFIJO):len(PREFIJO) + 2]
    if n == "01":
        # la banda de cabecera y la caja de totales deben ser oscuras en el render (texto blanco encima)
        prefijo = tmp / "control_01"
        subprocess.run(["pdftoppm", "-r", "36", "-gray", "-png", "-singlefile", str(ruta), str(prefijo)], check=True)
        img = Image.open(prefijo.with_suffix(".png")).convert("L")
        esc = 36 / 72
        for x, y, w, h in d["zonas_oscuras"]:
            caja = (int(x * esc) + 1, int((H - y - h) * esc) + 1, int((x + w) * esc) - 1, int((H - y) * esc) - 1)
            arr = np.asarray(img.crop(caja), dtype=np.float64)
            if not (arr.mean() < 90 and (arr > 150).mean() > 0.01):
                fallos.append(f"familia 1: zona {caja} no es oscura con texto claro (media {arr.mean():.0f})")
        if not any(ln.get("dto") for ln in d["lineas"]):
            fallos.append("familia 1: falta la línea con descuento")
    elif n == "02":
        texto = pdftotext(ruta)
        for ln in d["lineas"]:
            if _esp(ln["formula"]) not in _esp(texto):
                fallos.append(f"familia 2: falta la fórmula {ln['formula']!r}")
            if not re.fullmatch(r"[\d.,]+ x [\d.,]+ € = [\d.,]+ €", ln["formula"]):
                fallos.append(f"familia 2: fórmula mal formada {ln['formula']!r}")
        if re.search(r"\b(Cant\.|Cantidad|Precio|Importe)\b", texto):
            fallos.append("familia 2: hay cabecera de columnas (debe ser maqueta de tarjetas)")
        if not e["retencion"]:
            fallos.append("familia 2: falta la retención")
    return fallos


def verificar_escaneado(pdf: Path, d: dict) -> list[str]:
    fallos = []
    if pdftotext(pdf).strip():
        fallos.append("el PDF escaneado tiene capa de texto")
    fuentes = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True, check=True).stdout
    if len(fuentes.strip().splitlines()) > 2:
        fallos.append("el PDF escaneado declara fuentes")
    if num_paginas(pdf) != 1:
        fallos.append("el PDF escaneado debe tener 1 página")
    lst = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True, check=True).stdout
    filas = [ln.split() for ln in lst.strip().splitlines()[2:]]
    if len(filas) != 1:
        return fallos + [f"el PDF escaneado debe tener exactamente 1 imagen ({len(filas)})"]
    f = filas[0]
    color, bpc, enc, xppi, yppi = f[5], f[7], f[8], f[12], f[13]
    esperado_img = {"foto": ("gray", "8", "jpeg"), "fax": ("gray", "1", "ccitt")}[d["escaneado"]]
    if (color, bpc, enc) != esperado_img:
        fallos.append(f"imagen {color}/{bpc} bpc/{enc} != {esperado_img}")
    if int(xppi) != d["ppp"] or int(yppi) != d["ppp"]:
        fallos.append(f"resolución {xppi}x{yppi} != {d['ppp']}")
    return fallos


def nifs_otros_corpus() -> set[str]:
    """NIF de los demás corpus: en los JSON y en el texto de los PDF de tests/corpus* (excepto esta familia)."""
    nifs: set[str] = set()
    for carpeta in sorted((RAIZ / "tests").glob("corpus*")):
        for js in carpeta.rglob("*.json"):
            if js.name.startswith(PREFIJO):
                continue
            try:
                nifs |= {n for n, _, _ in buscar_nifs(js.read_text(encoding="utf-8"))}
            except OSError:
                continue
        for pdf in carpeta.rglob("*.pdf"):
            if pdf.name.startswith(PREFIJO):
                continue
            try:
                nifs |= {n for n, _, _ in buscar_nifs(pdftotext(pdf))}
            except subprocess.CalledProcessError:
                continue
    return nifs


def generar(tmp: Path) -> int:
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
        for parte in ("emisor", "destinatario"):
            n = e[parte]["nif"]
            if n in otros:
                fallos.append(f"NIF {n} ya usado en otro corpus")
            if vistos.get(n, e[parte]["nombre"]) != e[parte]["nombre"]:
                fallos.append(f"NIF {n} repetido con otro nombre en esta familia")
            vistos[n] = e[parte]["nombre"]
        sub = tmp / f"doc{i:02d}"
        sub.mkdir(parents=True, exist_ok=True)
        if d.get("escaneado"):
            limpio = sub / ("limpio_" + d["archivo"])
            d["render"](limpio, d)
            fallos += [f"(PDF limpio de origen) {x}" for x in verificar_texto(pdftotext(limpio), e, d)]
            simular = {"foto": foto_movil, "fax": fax}[d["escaneado"]]
            simular(limpio, ruta, sub, SEMILLA + i, d["ppp"])
            fallos += verificar_escaneado(ruta, d)
        else:
            d["render"](ruta, d)
            if num_paginas(ruta) != 1:
                fallos.append("se esperaba 1 página")
            fallos += verificar_texto(pdftotext(ruta), e, d)
        fallos += verificar_familia(d, e, ruta, sub)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total_fallos += len(fallos)
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}  ({len(e['lineas'])} líneas, base "
              f"{e['base_imponible']}, total {e['total_factura']}"
              + (f", a pagar {e['total_a_pagar']}" if e["total_a_pagar"] else "") + ")"
              + (f"  -> {fallos}" if fallos else ""))
    print(f"{len(DOCUMENTOS)} documentos en {SALIDA.relative_to(RAIZ)}; fallos: {total_fallos}")
    return 1 if total_fallos else 0


def main(argv: list[str]) -> int:
    if "--tmp" in argv:
        tmp = Path(argv[argv.index("--tmp") + 1])
        tmp.mkdir(parents=True, exist_ok=True)
        return generar(tmp)
    with tempfile.TemporaryDirectory(prefix="holdout3_c_") as td:
        return generar(Path(td))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
