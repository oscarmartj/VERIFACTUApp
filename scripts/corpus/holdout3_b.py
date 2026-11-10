"""Tercer corpus SINTÉTICO «held-out» (ciego), familia «b»: cuatro facturas españolas para medir la generalización
del extractor con maquetaciones que no aparecen en los demás corpus.

  1. Almacén de hierros (B2B industrial) a una calderería: 32 líneas en 2 páginas, pesos en kg y t, precios
     unitarios con 3 y 4 decimales, línea de «Portes» por tonelada, cabecera de columnas repetida en la página 2 y
     totales en una fila horizontal. Factura RECIBIDA por la empresa usuaria.
  2. Distribuidor de bebidas y alimentación a una cafetería: IVA 4 %, 10 % y 21 % en la misma tabla, línea negativa
     «Abono envases» y resumen por tipos TRASPUESTO (una columna por tipo de IVA).
  3. Estudio digital de Málaga con plantilla bilingüe inglés/castellano («Invoice / Factura», «Qty», «Unit price»,
     «Subtotal», «VAT 21%», «Total EUR») a un cliente español. Factura RECIBIDA.
  4. Ingeniera técnica (persona física con NIE «Y»), IRPF 15 % («Retención a cuenta IRPF», «Neto a cobrar»), banda
     lateral con los datos de la emisora y fecha en formato largo («2 de noviembre de 2026»).

TODOS LOS DATOS SON FICTICIOS: razones sociales, nombres de persona, domicilios, teléfonos (9x0 000 000), correos
(dominio .example), referencias e IBAN (ES00 + 20 dígitos: dígitos de control imposibles). Los NIF/CIF/NIE tienen
carácter de control válido (app.nif.completar_cif / letra del DNI) pero son de rangos evidentemente ficticios: CIF con
código de provincia 00 (no asignado a ninguna provincia) y NIE de cifras repetidas; las calles llevan «Inventado»,
«Ficticio» o «Imaginario» en el nombre (solo la población y el código postal son reales). Se comprueba al generar que
lo son y que no coinciden con los de los demás corpus del repositorio (JSON y texto de los PDF de tests/corpus*). Estos
identificadores y calles sustituyeron a otros verosímiles después de la medición ciega; la maqueta, los importes y el
resto de los textos no cambiaron.

Este corpus se diseñó SIN consultar el código del extractor (app/extraccion/) ni los datos privados (tests/privado/)
y sin ejecutar el evaluador: es un conjunto ciego. No lo ajustes para que el extractor acierte o falle.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/holdout3_b.py     # genera tests/corpus_holdout3/h3b_*.pdf + .json y verifica

Verificación (sale con código 1 si algo falla):
  * aritmética exacta de cada JSON: suma de líneas = base de cada tipo, cuota = base x tipo redondeada half-up,
    total = bases + cuotas, retención y neto; cada línea = cantidad x precio (valores completos, half-up); que la
    cantidad y el precio con 3-4 decimales no caigan en un empate de redondeo a 2 decimales (half-up = half-even);
  * NIF válidos, normalizados y no usados en otros corpus; empresa_nif coherente con la situación esperada;
  * en el texto de `pdftotext -layout`: número, fecha, NIF tal como se imprimen, nombres, códigos postales,
    conceptos (primer renglón), cantidades, precios e importes de línea, bases, cuotas, totales, retención,
    referencias, IBAN, forma de pago, rótulos propios de cada familia, número de páginas y cabecera de columnas
    repetida en la página 2; correos .example, teléfonos 9x0 000 000 y ausencia de particularidades fiscales no
    esperadas.

Determinista: canvas de reportlab en modo «invariant» y semilla fija (no hay ruido aleatorio en esta familia).
Requisitos: reportlab, poppler-utils (pdftotext, pdfinfo). Fuentes TrueType de /usr/share/fonts/truetype
(Liberation, Carlito, DejaVu); si faltan se sustituyen por las estándar con un aviso.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import unicodedata
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, ".")

from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import buscar_nifs, completar_cif, es_valido, normalizar  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus_holdout3"
PREFIJO = "h3b_"
SEMILLA = 20261109 + 2
random.seed(SEMILLA)

D = Decimal
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
    "LibSans": ("liberation/LiberationSans-Regular.ttf", "Helvetica"),
    "LibSans-Bold": ("liberation/LiberationSans-Bold.ttf", "Helvetica-Bold"),
    "LibSans-Italic": ("liberation/LiberationSans-Italic.ttf", "Helvetica-Oblique"),
    "LibSerif": ("liberation/LiberationSerif-Regular.ttf", "Times-Roman"),
    "LibSerif-Bold": ("liberation/LiberationSerif-Bold.ttf", "Times-Bold"),
    "LibSerif-Italic": ("liberation/LiberationSerif-Italic.ttf", "Times-Italic"),
    "Carlito": ("crosextra/Carlito-Regular.ttf", "Helvetica"),
    "Carlito-Bold": ("crosextra/Carlito-Bold.ttf", "Helvetica-Bold"),
    "Carlito-Italic": ("crosextra/Carlito-Italic.ttf", "Helvetica-Oblique"),
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


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def nie(letra: str, n7: int) -> str:
    num = {"X": 0, "Y": 1, "Z": 2}[letra] * 10_000_000 + n7
    return f"{letra}{n7:07d}{LETRAS_DNI[num % 23]}"


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
    """Cantidad sin ceros sobrantes: 12 / 7,5 / 6,42 / -2."""
    x = D(x).normalize()
    nd = max(0, -x.as_tuple().exponent)
    return num(x, nd)


def calcular(lineas: list[dict], pct_ret=None) -> dict:
    """Importe de línea = cantidad x precio (valores completos, half-up); desglose por tipo; retención s/ base."""
    for ln in lineas:
        ln.setdefault("iva", D("21"))
        if ln.get("importe") is None:
            ln["importe"] = q2(D(ln["cantidad"]) * D(ln["precio"]))
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
def tx(c, x, y, s, fuente="Helvetica", tam=9.0, color=black, alin="l"):
    c.setFillColor(color)
    c.setFont(fuente, tam)
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


def partir(s: str, fuente: str, tam: float, ancho: float) -> list[str]:
    return simpleSplit(s, fuente, tam, ancho)


def nuevo_canvas(ruta: Path, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=A4, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    c.setCreator("scripts/corpus/holdout3_b.py")
    return c


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido; CIF de provincia 00, NIE de cifras repetidas): main comprueba que lo son y que no
# aparecen en los demás corpus
# ----------------------------------------------------------------------------------------------
CIF_JALON = completar_cif("A", "0097413")       # emisor doc 1 (almacén de hierros, Plasencia de Jalón)
CIF_MONCAYO = completar_cif("B", "0028046")     # destinatario doc 1 = empresa usuaria (calderería, Tarazona)
CIF_ORBIGO = completar_cif("B", "0061975")      # emisor doc 2 = empresa usuaria (distribuidor de bebidas)
CIF_OBISPO = completar_cif("B", "0038061")      # cliente doc 2 (cafetería, Astorga)
CIF_LUMBRE = completar_cif("B", "0086407")      # emisor doc 3 (estudio digital, Málaga)
CIF_VIDAL = completar_cif("A", "0007162")       # destinatario doc 3 = empresa usuaria (conservera, Santoña)
NIE_FERRAZ = nie("Y", 4444444)                  # emisora doc 4 = empresa usuaria (ingeniera técnica, Alicante)
CIF_VINALOPO = completar_cif("B", "0027319")    # cliente doc 4 (envases plásticos, Alicante)


def nif_ficticio(n: str) -> bool:
    """NIF que no puede ser de nadie: CIF con provincia 00 (no asignada) o DNI/NIE de cifras repetidas."""
    if re.fullmatch(r"[A-HJNP-SUVW]00\d{5}[0-9A-J]", n):
        return True
    return re.fullmatch(r"(\d)\1{7}[A-Z]|[XYZ](\d)\2{6}[A-Z]", n) is not None


def con_guion(n: str) -> str:
    """A00974139 -> A-00974139 ; Y4444444F -> Y-4444444-F."""
    if n[0] in "XYZ":
        return f"{n[0]}-{n[1:8]}-{n[8]}"
    return f"{n[0]}-{n[1:]}"


# ==============================================================================================
# 1. Almacén de hierros: 32 líneas en 2 páginas, kg/t, precios con 3-4 decimales, portes (LiberationSans)
# ==============================================================================================
_LINEAS_01 = [
    # código, concepto, detalle, colada, cantidad, ud, precio
    ("CLC-06-3015", "Chapa laminada en caliente S275JR e=6 mm 3000x1500",
     "Certificado de inspección 3.1 s/ EN 10204 · 6 chapas", "C26-41187", "1272.30", "kg", "0.8735"),
    ("CLC-10-3015", "Chapa laminada en caliente S275JR e=10 mm 3000x1500", None, "C26-41203", "2120.40", "kg",
     "0.8610"),
    ("CLG-57-2512", "Chapa lagrimada S235JR e=5/7 mm 2500x1250", None, "C26-39915", "612.80", "kg", "0.9420"),
    ("CGZ-15-2010", "Chapa galvanizada Z275 e=1,5 mm 2000x1000", "Paquete de 16 chapas · film protector",
     "G26-08812", "384.00", "kg", "1.1265"),
    ("IPE-200-12", "Perfil IPE 200 S275JR barra 12 m", None, "P26-77031", "1.344", "t", "812.400"),
    ("IPE-240-12", "Perfil IPE 240 S275JR barra 12 m", None, "P26-77058", "1.843", "t", "808.900"),
    ("HEB-160-12", "Perfil HEB 160 S275JR barra 12 m", "Certificado de inspección 3.1 s/ EN 10204", "P26-76990",
     "2.054", "t", "826.750"),
    ("UPN-120-06", "Perfil UPN 120 S275JR barra 6 m", None, "P26-75214", "535.20", "kg", "0.8945"),
    ("ANG-050-05", "Angular L 50x50x5 S275JR barra 6 m", None, "A26-12094", "226.80", "kg", "0.9310"),
    ("ANG-080-08", "Angular L 80x80x8 S275JR barra 6 m", None, "A26-12140", "578.40", "kg", "0.9185"),
    ("PLT-100-10", "Pletina laminada 100x10 S275JR barra 6 m", None, "L26-30477", "471.00", "kg", "0.9065"),
    ("PLT-050-08", "Pletina laminada 50x8 S275JR barra 6 m", None, "L26-30482", "188.40", "kg", "0.9240"),
    ("RCA-040-F1", "Redondo calibrado F-1140 Ø40 h11 barra 3 m", "Cortado a 3 m · 24 barras", "R26-05561",
     "296.04", "kg", "1.3875"),
    ("COR-16-12", "Redondo corrugado B500S Ø16 barra 12 m", None, "B26-90318", "1.896", "t", "694.300"),
    ("COR-12-12", "Redondo corrugado B500S Ø12 barra 12 m", None, "B26-90322", "1.066", "t", "701.850"),
    ("MAL-15-06", "Malla electrosoldada ME 15x15 Ø6-6 B500T 6x2,2 m", None, "", "48", "ud", "21.870"),
    ("TCU-080-04", "Tubo estructural cuadrado 80x80x4 S275J0H barra 6 m", None, "T26-44718", "683.52", "kg",
     "1.0485"),
    ("TRE-120-05", "Tubo estructural rectangular 120x60x5 S275J0H barra 6 m", None, "T26-44731", "773.28", "kg",
     "1.0560"),
    ("TRD-089-03", "Tubo estructural redondo Ø88,9x3,2 S235JRH barra 6 m", None, "T26-44802", "247.20", "kg",
     "1.0915"),
    ("TNG-DN40", "Tubo de acero negro DIN 2440 DN40 barra 6 m", None, "T26-43990", "162.36", "kg", "1.1730"),
    ("INX-304-02", "Chapa de acero inoxidable AISI 304 2B e=2 mm 2000x1000", "Film PVC una cara · 5 chapas",
     "X26-02217", "159.20", "kg", "3.4870"),
    ("ALU-5754-3", "Chapa de aluminio 5754 H111 e=3 mm 2000x1000", None, "N26-00894", "97.20", "kg", "4.1325"),
    ("INX-316-25", "Barra de inoxidable AISI 316L Ø25 calibrada h9", None, "X26-02251", "46.30", "kg", "5.2640"),
    ("PTE-060-07", "Perfil T 60x60x7 S235JR barra 6 m", None, "P26-74102", "225.36", "kg", "0.9790"),
    ("HEA-200-S3", "Viga HEA 200 S355J2 barra 12 m", "Certificado de inspección 3.1 s/ EN 10204", "P26-77114",
     "2.540", "t", "869.250"),
    ("CMZ-020-06", "Cuadrado macizo laminado 20x20 S275JR barra 6 m", None, "Q26-01836", "113.04", "kg", "0.9575"),
    ("ELE-6013-3", "Electrodo rutilo E6013 Ø3,25 (caja 5 kg)", None, "", "8", "ud", "15.946"),
    ("HSG2-10-15", "Hilo de soldadura SG2 Ø1,0 bobina 15 kg", None, "", "12", "ud", "27.4380"),
    ("DCO-230-19", "Disco de corte para acero 230x1,9 mm", None, "", "50", "ud", "1.9640"),
    ("SGR-IMP", "Granallado e imprimación de perfiles", "Imprimación epoxi gris 60 micras", "", "2430.00", "kg",
     "0.1185"),
    ("SCT-CINTA", "Corte a medida con sierra de cinta", None, "", "64", "ud", "1.250"),
]
_CONTIENE_01 = 6  # palabras del primer renglón en concepto_contiene


def doc_01() -> dict:
    lineas = []
    for cod, concepto, detalle, colada, cant, ud, precio in _LINEAS_01:
        lineas.append({"codigo": cod, "concepto": concepto, "detalle": detalle, "colada": colada,
                       "cantidad": D(cant), "ud": ud, "precio": D(precio),
                       "contiene": " ".join(concepto.split()[:_CONTIENE_01])})
    # peso de material (kg y t, sin servicios ni artículos por unidad) = base de los portes
    peso_kg = sum((ln["cantidad"] * (1000 if ln["ud"] == "t" else 1) for ln in lineas
                   if ln["ud"] in ("kg", "t") and not ln["codigo"].startswith("S")), D("0"))
    peso_t = (peso_kg / 1000).quantize(D("0.001"), rounding=ROUND_HALF_UP)
    lineas.append({"codigo": "PORTES", "concepto": "Portes en camión grúa hasta el taller de Borja",
                   "detalle": "Según peso total del material servido", "colada": "", "cantidad": peso_t, "ud": "t",
                   "precio": D("7.850"), "contiene": "Portes en camión grúa"})
    for ln in lineas:
        nd_p = 4 if ln["ud"] == "kg" else 3
        ln["precio_txt"] = num(ln["precio"], nd_p)
        ln["cant_txt"] = {"kg": num(ln["cantidad"], 2), "t": num(ln["cantidad"], 3)}.get(ln["ud"],
                                                                                           num(ln["cantidad"], 0))
    t = calcular(lineas)
    numero = "A/26/004318"
    return {
        "archivo": "h3b_01_industrial_2pag_kg_t_portes.pdf",
        "estilo": ("Factura de almacén de hierros y aceros (ERP industrial, LiberationSans 7,6 pt): logotipo con "
                   "monograma y nombre comercial, datos legales del emisor bajo el logotipo con C.I.F. con guion, "
                   "rejilla de metadatos etiqueta-sobre-valor (Nº factura, Fecha factura, Página, Cód. cliente, Su "
                   "pedido, Albarán), cajas 'DIRECCIÓN DE ENTREGA' y 'CLIENTE' con banda azul, cuerpo con rejilla "
                   "vertical y 7 columnas Código | Descripción | Colada | Cantidad | Ud. | Precio | Importe €, 32 "
                   "líneas repartidas en 2 páginas (23 + 9) con cabecera de columnas repetida en la página 2, "
                   "cantidades en kg (2 decimales) y t (3 decimales), precios con 4 decimales (€/kg) y 3 decimales "
                   "(€/t y ud), detalle en cursiva bajo algunas líneas, línea de portes por tonelada, totales en "
                   "fila horizontal (Suma importes | Base imponible | % IVA | Cuota IVA | TOTAL FACTURA EUR), "
                   "fecha dd-mm-aaaa, A4"),
        "empresa_nif": CIF_MONCAYO,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": numero,
        "fecha": date(2026, 10, 28),
        "fecha_txt": "28-10-2026",
        "emisor": {"nombre": "Hierros y Perfiles del Jalón, S.A.", "nif": CIF_JALON, "domicilio_contiene": "50296"},
        "destinatario": {"nombre": "Calderería y Montajes Moncayo Norte, S.L.", "nif": CIF_MONCAYO,
                         "domicilio_contiene": "50500"},
        "nif_txt": {"emisor": f"C.I.F.: {con_guion(CIF_JALON)}", "destinatario": f"C.I.F.: {con_guion(CIF_MONCAYO)}"},
        "lineas": lineas,
        "t": t,
        "peso_kg": peso_kg,
        "referencias": {"pedido": "OC-MN-2026-0917", "albaran": "ALB-26-11873"},
        "iban": "ES0050961274038816520097",
        "iban_txt": "ES00 5096 1274 0388 1652 0097",
        "forma_pago_contiene": "Transferencia bancaria a 60 días",
        "indicios": [],
        "fmt": num,
        "paginas": 2,
        "cabecera_columnas": ["Código", "Descripción", "Colada", "Cantidad", "Ud.", "Precio", "Importe €"],
        "tipos_txt": {"21.00": ["% IVA", "21,00"]},  # el tipo va bajo el rótulo «% IVA», sin signo junto al número
        "textos": ["Suma importes", "TOTAL FACTURA", "Página 1 de 2", "Página 2 de 2", "27-12-2026",
                   "50540 Borja", f"{num(peso_kg, 2)} kg", "(continuación)"],
        "notas": ("Factura de proveedor RECIBIDA por la empresa usuaria (la calderería destinataria) sin marcas "
                  "VERI*FACTU. 32 líneas: 23 en la página 1 (que termina con 'Continúa en la página 2', sin suma "
                  "parcial) y 9 en la página 2, que repite una cabecera reducida ('FACTURA A/26/004318 "
                  "(continuación)') y la cabecera de columnas. Cantidades en kg con 2 decimales y punto de miles "
                  "(1.272,30) y en t con 3 decimales (1,344); precios con 4 decimales en €/kg (0,8735) y 3 en €/t "
                  "y por unidad (812,400 / 21,870); en el JSON cantidad y precio se redondean a 2 decimales "
                  "(ningún valor cae en empate half-up/half-even). Descripciones con medidas y calidades "
                  "(S275JR, 3000x1500, Ø88,9x3,2, F-1140, DN40) y columna de colada alfanumérica (C26-41187). "
                  f"Línea de portes por peso: {num(peso_t, 3)} t x 7,850 = "
                  f"{num(lineas[-1]['importe'])}. Distractores: dirección de entrega en Borja (50540), código de "
                  "cliente 43.0712, fecha de albarán 26-10-2026, vencimiento 27-12-2026, peso total "
                  f"{num(peso_kg, 2)} kg y 46 bultos, certificados 3.1 EN 10204. Totales solo en la página 2, en "
                  f"una fila horizontal. 21 %: base {num(t['base'])} -> cuota {num(t['cuota'])}; total "
                  f"{num(t['total'])}."),
        "render": render_01,
    }


def render_01(ruta: Path, d: dict):
    W, H = A4
    F, FB, FI = FUENTE["LibSans"], FUENTE["LibSans-Bold"], FUENTE["LibSans-Italic"]
    AZ, GR, CL, LN = HexColor("#1D3557"), HexColor("#5C6670"), HexColor("#E9EDF2"), HexColor("#9AA5B1")
    em, de, t = d["emisor"], d["destinatario"], d["t"]
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", em["nombre"])
    M = 28
    cols = [("Código", 58, "l"), ("Descripción", 214, "l"), ("Colada", 54, "l"), ("Cantidad", 56, "r"),
            ("Ud.", 22, "c"), ("Precio", 58, "r"), ("Importe €", W - 2 * M - 462, "r")]
    xs = [M]
    for _, w, _ in cols:
        xs.append(xs[-1] + w)

    def logo(y_top, grande=True):
        s = 42 if grande else 28
        rect(c, M, y_top - s, s + 4, s, relleno=AZ)
        tx(c, M + (s + 4) / 2, y_top - s / 2 - (5 if grande else 3.5), "HJ", FB, 15 if grande else 10, white, "c")
        tx(c, M + s + 14, y_top - (20 if grande else 13), "HIERROS DEL JALÓN", FB, 16 if grande else 11, AZ)
        if grande:
            tx(c, M + s + 14, y_top - 33, "Almacén de hierros, aceros y perfiles comerciales", F, 8, GR)

    def pie(pag):
        raya(c, M, 52, W - M, 52, LN, 0.4)
        tx(c, M, 41, f"{em['nombre']} · Inscrita en el Registro Mercantil de Zaragoza, tomo 0000, folio 000, "
           f"hoja Z-00000 · {d['nif_txt']['emisor']}", F, 6.3, GR)
        tx(c, W - M, 41, f"Página {pag} de 2", FB, 7, GR, "r")

    def cabecera_tabla(y):
        rect(c, M, y - 16, W - 2 * M, 16, relleno=AZ)
        for i, (k, w, al) in enumerate(cols):
            x = {"l": xs[i] + 4, "r": xs[i + 1] - 4, "c": xs[i] + w / 2}[al]
            tx(c, x, y - 11.2, k, FB, 7.6, white, al)
        return y - 16

    def cuerpo(y_top, y_bot):
        rect(c, M, y_bot, W - 2 * M, y_top - y_bot, borde=LN, grosor=0.6)
        for x in xs[1:-1]:
            raya(c, x, y_bot, x, y_top, LN, 0.4)

    def filas(lns, y):
        for ln in lns:
            y -= 12.5
            vals = [ln["codigo"], ln["concepto"], ln["colada"], ln["cant_txt"], ln["ud"], ln["precio_txt"],
                    num(ln["importe"])]
            for i, (v, (_, w, al)) in enumerate(zip(vals, cols)):
                x = {"l": xs[i] + 4, "r": xs[i + 1] - 4, "c": xs[i] + w / 2}[al]
                tx(c, x, y, v, FB if i == 6 else F, 7.6, black, al)
            if ln["detalle"]:
                y -= 9
                tx(c, xs[1] + 10, y, ln["detalle"], FI, 6.6, GR)
            y -= 4.5
            raya(c, M + 0.5, y, W - M - 0.5, y, CL, 0.4)
        return y

    # ---------------- página 1
    logo(H - 28)
    y = H - 88
    tx(c, M, y, em["nombre"], FB, 8.5)
    for s in ("Pol. Ind. Valle Ficticio, calle C, parcela 22", "50296 Plasencia de Jalón (Zaragoza)",
              d["nif_txt"]["emisor"], "Tel. 900 000 000 · administracion@hierrosjalon.example"):
        y -= 10.5
        tx(c, M, y, s, F, 7.8, GR if "@" in s else black)
    tx(c, W - M, H - 50, "FACTURA", FB, 20, AZ, "r")
    gx, gw, gh = W - M - 282, 94, 25
    celdas = [[("Nº factura", d["numero"]), ("Fecha factura", d["fecha_txt"]), ("Página", "1 de 2")],
              [("Cód. cliente", "43.0712"), ("Su pedido", d["referencias"]["pedido"]),
               ("Albarán", d["referencias"]["albaran"])]]
    for r, fila in enumerate(celdas):
        for k, (lab, val) in enumerate(fila):
            x0, y0 = gx + k * gw, H - 62 - (r + 1) * gh
            rect(c, x0, y0, gw, gh, borde=LN, grosor=0.6)
            tx(c, x0 + 5, y0 + gh - 9, lab, F, 6.5, GR)
            tx(c, x0 + 5, y0 + 5, val, FB, 8.4)
    # cajas de entrega y cliente
    by, bh = H - 138, 82
    rect(c, M, by - bh, 246, bh, borde=LN, grosor=0.6)
    rect(c, M, by - 13, 246, 13, relleno=AZ)
    tx(c, M + 6, by - 9.5, "DIRECCIÓN DE ENTREGA", FB, 7, white)
    yy = by - 27
    for s in (de["nombre"], "Taller 2: Ctra. Inventada de Borja, km 3,2", "50540 Borja (Zaragoza)",
              "Contacto descarga: J. Lasheras · Tel. 910 000 000"):
        tx(c, M + 6, yy, s, FB if s == de["nombre"] else F, 7.8)
        yy -= 11
    cx = W - M - 282
    rect(c, cx, by - bh, 282, bh, borde=LN, grosor=0.6)
    rect(c, cx, by - 13, 282, 13, relleno=AZ)
    tx(c, cx + 6, by - 9.5, "CLIENTE", FB, 7, white)
    yy = by - 27
    for s in (de["nombre"], "Camino Inventado 9, nave 4", "50500 Tarazona (Zaragoza)", d["nif_txt"]["destinatario"]):
        tx(c, cx + 6, yy, s, FB if s == de["nombre"] else F, 8.2)
        yy -= 11.5
    # franja de condiciones
    fy = by - bh - 16
    tx(c, M, fy, f"Forma de pago: Transferencia bancaria a 60 días f.f.    Vencimiento: 27-12-2026    "
       "Fecha albarán: 26-10-2026    Transportista: medios propios", F, 7.4, GR)
    # tabla página 1
    y_tab = fy - 10
    y_fondo = 70
    cuerpo(y_tab, y_fondo)
    yc = cabecera_tabla(y_tab)
    filas(d["lineas"][:23], yc)
    tx(c, W - M - 6, y_fondo + 6, "Continúa en la página 2 >>", FI, 7.2, GR, "r")
    pie(1)
    c.showPage()

    # ---------------- página 2
    logo(H - 28, grande=False)
    tx(c, W - M, H - 40, f"FACTURA {d['numero']} (continuación)", FB, 10.5, AZ, "r")
    tx(c, W - M, H - 53, f"Fecha: {d['fecha_txt']} · Cód. cliente: 43.0712 · Página 2 de 2", F, 7.8, GR, "r")
    tx(c, M, H - 74, f"Cliente: {de['nombre']}", F, 7.8, GR)
    y_tab = H - 84
    y_fondo2 = 520
    cuerpo(y_tab, y_fondo2)
    yc = cabecera_tabla(y_tab)
    filas(d["lineas"][23:], yc)
    # observaciones y totales
    oy = y_fondo2 - 18
    tx(c, M, oy, f"Peso total: {num(d['peso_kg'], 2)} kg    Nº de bultos: 46", FB, 7.8)
    tx(c, M, oy - 12, "Observaciones: material servido contra su pedido según albarán; certificados de inspección "
       "disponibles bajo petición.", F, 7.4, GR)
    ty, th = oy - 34, 34
    tot = [("Suma importes", num(t["base"])), ("Base imponible", num(t["base"])),
           ("% IVA", num(t["desglose"][0]["tipo"])), ("Cuota IVA", num(t["cuota"])),
           ("TOTAL FACTURA EUR", num(t["total"]))]
    tw = (W - 2 * M) / len(tot)
    for i, (k, v) in enumerate(tot):
        x0 = M + i * tw
        ultimo = i == len(tot) - 1
        rect(c, x0, ty - th, tw, th, relleno=AZ if ultimo else None, borde=LN, grosor=0.6)
        rect(c, x0, ty - 13, tw, 13, relleno=CL if not ultimo else HexColor("#2B4A73"), borde=LN, grosor=0.6)
        tx(c, x0 + tw / 2, ty - 9.5, k, FB, 7, white if ultimo else GR, "c")
        tx(c, x0 + tw - 7, ty - th + 6.5, v, FB, 10 if ultimo else 9, white if ultimo else black, "r")
    py = ty - th - 24
    tx(c, M, py, "Forma de pago: Transferencia bancaria a 60 días f.f.", FB, 8)
    tx(c, M, py - 12, f"Rogamos transferencia a la cuenta IBAN {d['iban_txt']} indicando el número de factura.",
       F, 7.8)
    tx(c, M, py - 24, f"Vencimiento: 27-12-2026 · Importe: {num(t['total'])} €", F, 7.8)
    tx(c, M, py - 44, "La mercancía viaja por cuenta y riesgo del comprador desde su carga. Reserva de dominio hasta "
       "el pago total del precio.", FI, 6.8, GR)
    pie(2)
    c.save()


# ==============================================================================================
# 2. Distribuidor de bebidas: IVA 4/10/21 % en una tabla, abono de envases, resumen traspuesto (Carlito)
# ==============================================================================================
def doc_02() -> dict:
    datos = [
        ("10214", "Leche entera UHT brik 1 L (caja 6 ud.)", "12", "5.34", "4", "Leche entera UHT"),
        ("10388", "Huevos frescos clase L (estuche 30 ud.)", "4", "7.95", "4", "Huevos frescos clase L"),
        ("11702", "Queso tierno de vaca, pieza aprox. 3 kg", "6.42", "9.85", "4", "Queso tierno de vaca"),
        ("12050", "Pan de barra precocido 250 g (caja 40 ud.)", "3", "11.60", "4", "Pan de barra precocido"),
        ("13311", "Tomate de rama categoría I (kg)", "8.5", "1.92", "4", "Tomate de rama"),
        ("20417", "Café en grano tueste natural, paquete 1 kg", "6", "14.90", "10", "Café en grano tueste"),
        ("20533", "Azúcar en sobres de 8 g (caja 1.000 ud.)", "2", "17.25", "10", "Azúcar en sobres"),
        ("21046", "Agua mineral natural 0,5 L (pack 24)", "5", "4.68", "10", "Agua mineral natural"),
        ("21190", "Zumo de melocotón 200 ml (caja 24)", "2", "13.44", "10", "Zumo de melocotón"),
        ("30021", "Cerveza rubia barril 30 L", "3", "79.50", "21", "Cerveza rubia barril"),
        ("30158", "Refresco de cola 33 cl vidrio (caja 24)", "4", "14.64", "21", "Refresco de cola"),
        ("30402", "Vino tinto joven D.O. 75 cl (caja 6)", "2", "27.90", "21", "Vino tinto joven"),
        ("90030", "Depósito envase barril 30 L", "3", "30.00", "21", "Depósito envase barril"),
        ("90031", "Abono envases barril 30 L devueltos", "-2", "30.00", "21", "Abono envases"),
    ]
    lineas = [{"codigo": cod, "concepto": con, "cantidad": D(ca), "precio": D(pr), "iva": D(iv), "contiene": cont,
               "cant_txt": cant_es(ca), "precio_txt": num(pr)} for cod, con, ca, pr, iv, cont in datos]
    t = calcular(lineas)
    return {
        "archivo": "h3b_02_tres_iva_abono_envases.pdf",
        "estilo": ("Factura de distribuidor de bebidas y alimentación para hostelería (Carlito, acentos terracota): "
                   "emblema circular con iniciales y nombre comercial, datos del emisor debajo, caja 'FACTURA' a la "
                   "derecha con filas Número / Fecha / Nº cliente / Ruta / Albarán, caja de cliente y punto de "
                   "entrega, tabla en cebra Código | Descripción | Cant. | Precio | % IVA | Importe con IVA 4 %, "
                   "10 % y 21 % mezclados línea a línea (rotulados 4,00 / 10,00 / 21,00), línea de depósito de "
                   "envases y línea NEGATIVA 'Abono envases' con cantidad -2, resumen de IVA TRASPUESTO (una "
                   "columna por tipo: IVA 4 % | IVA 10 % | IVA 21 % | Totales, filas Base imponible / Cuota IVA) y "
                   "caja 'TOTAL FACTURA', pago por recibo domiciliado, A4"),
        "empresa_nif": CIF_ORBIGO,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "F2611-0874",
        "fecha": date(2026, 11, 3),
        "fecha_txt": "03/11/2026",
        "emisor": {"nombre": "Bebidas y Alimentación Ribera del Órbigo, S.L.", "nif": CIF_ORBIGO,
                   "domicilio_contiene": "24350"},
        "destinatario": {"nombre": "Hostelería Puerta Obispo, S.L.", "nif": CIF_OBISPO,
                         "domicilio_contiene": "24700"},
        "nif_txt": {"emisor": f"NIF {CIF_ORBIGO}", "destinatario": f"NIF {CIF_OBISPO}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"albaran": "AL-88412"},
        "iban": None,
        "iban_txt": None,
        "forma_pago_contiene": "Recibo domiciliado",
        "indicios": [],
        "fmt": num,
        "paginas": 1,
        "permitidos": [r"Abono envases"],
        "textos": ["Abono envases", "IVA 4 %", "IVA 10 %", "IVA 21 %", "TOTAL FACTURA", "Resumen de IVA por tipos",
                   "****3312", "03/12/2026"],
        "notas": ("Factura propia del distribuidor (empresa usuaria = emisor) sin marcas VERI*FACTU: documento "
                  "preparatorio. Tres tipos de IVA en la misma tabla, con columna '% IVA' rotulada con dos "
                  "decimales (4,00 / 10,00 / 21,00) y líneas de distinto tipo intercaladas. Envases retornables: "
                  "'Depósito envase barril 30 L' (3 x 30,00 = 90,00) y la línea NEGATIVA 'Abono envases barril "
                  "30 L devueltos' con cantidad -2 x 30,00 = -60,00, ambas al 21 % (siguen al producto). El abono "
                  "de envases reduce la base del 21 %: NO es una factura rectificativa ni un descuento. Cantidades "
                  "decimales en kg (6,42 y 8,5). Resumen de IVA traspuesto: los tipos van en columnas y las bases "
                  "y cuotas en filas, con columna 'Totales'. Pago por recibo domiciliado con la cuenta del cliente "
                  "enmascarada (****3312): no hay IBAN completo. Distractores: Nº cliente 4417, ruta R-07, 52 "
                  "bultos y peso aproximado, vencimiento 03/12/2026, '1.000 ud.' y '0,5 L' en los conceptos. "
                  + "; ".join(f"{g['tipo']:.0f} %: {num(g['base'])} -> {num(g['cuota'])}" for g in t["desglose"])
                  + f"; base {num(t['base'])}, IVA {num(t['cuota'])}, total {num(t['total'])}."),
        "render": render_02,
    }


def render_02(ruta: Path, d: dict):
    W, H = A4
    F, FB, FI = FUENTE["Carlito"], FUENTE["Carlito-Bold"], FUENTE["Carlito-Italic"]
    OR, DK, GR, ZB, LN = (HexColor("#B5541B"), HexColor("#2B2B2B"), HexColor("#6E6E6E"), HexColor("#F7EFE8"),
                          HexColor("#D9C7B8"))
    em, de, t = d["emisor"], d["destinatario"], d["t"]
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", em["nombre"])
    M = 36

    # emblema y emisor
    c.setFillColor(OR)
    c.circle(M + 21, H - 58, 21, stroke=0, fill=1)
    tx(c, M + 21, H - 63, "RÓ", FB, 14, white, "c")
    tx(c, M + 52, H - 54, "RIBERA DEL ÓRBIGO", FB, 19, OR)
    tx(c, M + 52, H - 68, "Bebidas · Alimentación · Hostelería", FI, 10, GR)
    y = H - 100
    tx(c, M, y, em["nombre"], FB, 10)
    for s in ("Avda. Imaginaria 41", "24350 Veguellina de Órbigo (León)",
              f"{d['nif_txt']['emisor']} · Tel. 970 000 000", "pedidos@riberaorbigo.example"):
        y -= 12
        tx(c, M, y, s, F, 9.5, GR if "@" in s else DK)

    # caja FACTURA
    bx, bw, btop = W - M - 215, 215, H - 36
    filas = [("Número", d["numero"]), ("Fecha", d["fecha_txt"]), ("Nº cliente", "4417"),
             ("Ruta / reparto", "R-07 · martes"), ("Albarán", d["referencias"]["albaran"])]
    bh = 24 + 15 * len(filas) + 6
    rect(c, bx, btop - bh, bw, bh, borde=OR, grosor=0.9)
    rect(c, bx, btop - 24, bw, 24, relleno=OR)
    tx(c, bx + bw / 2, btop - 17, "FACTURA", FB, 15, white, "c")
    yy = btop - 24 - 14
    for k, v in filas:
        tx(c, bx + 10, yy, k, F, 9.5, GR)
        tx(c, bx + bw - 10, yy, v, FB, 10, DK, "r")
        yy -= 15

    # cliente y punto de entrega
    cy = H - 190
    tx(c, M, cy, "Cliente", FB, 9, OR)
    raya(c, M, cy - 4, M + 270, cy - 4, OR, 0.8)
    yy = cy - 18
    for s in (de["nombre"], "Calle Inventada de los Arrieros 12", "24700 Astorga (León)", d["nif_txt"]["destinatario"]):
        tx(c, M, yy, s, FB if s == de["nombre"] else F, 10.5 if s == de["nombre"] else 9.5, DK)
        yy -= 12.5
    px = M + 300
    tx(c, px, cy, "Punto de entrega", FB, 9, OR)
    raya(c, px, cy - 4, W - M, cy - 4, OR, 0.8)
    tx(c, px, cy - 18, "Cafetería Puerta Obispo (entrada por almacén)", F, 9.5, DK)
    tx(c, px, cy - 30.5, "Horario de descarga: 8:00 a 10:30", F, 9.5, DK)
    tx(c, px, cy - 43, "Bultos: 52 · Peso aprox.: 389 kg", F, 9.5, GR)

    # tabla de líneas
    cols = [("Código", 46, "l"), ("Descripción", 232, "l"), ("Cant.", 48, "r"), ("Precio", 58, "r"),
            ("% IVA", 46, "r"), ("Importe", W - 2 * M - 430, "r")]
    xs = [M]
    for _, w, _ in cols:
        xs.append(xs[-1] + w)
    ty = H - 266
    rect(c, M, ty - 18, W - 2 * M, 18, relleno=DK)
    for i, (k, w, al) in enumerate(cols):
        tx(c, xs[i] + 5 if al == "l" else xs[i + 1] - 5, ty - 12.5, k, FB, 9.5, white, al)
    y = ty - 18
    for j, ln in enumerate(d["lineas"]):
        if j % 2:
            rect(c, M, y - 17, W - 2 * M, 17, relleno=ZB)
        vals = [ln["codigo"], ln["concepto"], ln["cant_txt"], ln["precio_txt"], num(ln["iva"]), num(ln["importe"])]
        for i, (v, (_, w, al)) in enumerate(zip(vals, cols)):
            tx(c, xs[i] + 5 if al == "l" else xs[i + 1] - 5, y - 12, v, FB if i == 5 else F, 9.5, DK, al)
        y -= 17
    raya(c, M, y, W - M, y, OR, 0.9)

    # resumen traspuesto
    ry = y - 26
    tx(c, M, ry, "Resumen de IVA por tipos", FB, 10, OR)
    cab = ["", *[f"IVA {g['tipo']:.0f} %" for g in t["desglose"]], "Totales"]
    filas_r = [["Base imponible", *[num(g["base"]) for g in t["desglose"]], num(t["base"])],
               ["Cuota IVA", *[num(g["cuota"]) for g in t["desglose"]], num(t["cuota"])]]
    anchos = [84, 66, 66, 66, 70]
    x0s = [M]
    for w in anchos:
        x0s.append(x0s[-1] + w)
    yy = ry - 8
    rect(c, M, yy - 16, sum(anchos), 16, relleno=ZB)
    for i, k in enumerate(cab):
        if k:
            tx(c, x0s[i + 1] - 6, yy - 11.5, k, FB, 9, DK, "r")
    yy -= 16
    for fila in filas_r:
        for i, v in enumerate(fila):
            if i == 0:
                tx(c, M + 4, yy - 12, v, F, 9.5, GR)
            else:
                tx(c, x0s[i + 1] - 6, yy - 12, v, FB if i == len(fila) - 1 else F, 9.5, DK, "r")
        yy -= 16
        raya(c, M, yy, M + sum(anchos), yy, LN, 0.5)
    for x in x0s[1:-1]:
        raya(c, x, ry - 8, x, yy, LN, 0.5)

    # total
    tb_w, tb_x = 150, W - M - 150
    rect(c, tb_x, ry - 46, tb_w, 46, relleno=OR)
    tx(c, tb_x + tb_w / 2, ry - 16, "TOTAL FACTURA", FB, 10, white, "c")
    tx(c, tb_x + tb_w / 2, ry - 36, f"{num(t['total'])} €", FB, 16, white, "c")

    # pago y pie
    py = yy - 30
    tx(c, M, py, "Forma de pago: Recibo domiciliado a 30 días · Vencimiento 03/12/2026 · Cuenta de cargo: ****3312",
       F, 9.5, DK)
    tx(c, M, py - 13, "Envases en poder del cliente tras esta entrega: 1 barril de 30 L. Los envases se abonan al "
       "precio de depósito al ser devueltos en buen estado.", FI, 8.8, GR)
    raya(c, M, 62, W - M, 62, LN, 0.5)
    tx(c, W / 2, 50, f"{em['nombre']} · Registro Mercantil de León, tomo 0000, folio 000, hoja LE-00000 · "
       f"{d['nif_txt']['emisor']}", F, 7.6, GR, "c")
    tx(c, W / 2, 40, "Sus datos se tratan para gestionar la relación comercial. Puede ejercer sus derechos en "
       "privacidad@riberaorbigo.example", F, 7.6, GR, "c")
    c.save()


# ==============================================================================================
# 3. Plantilla bilingüe inglés / castellano de un estudio digital (DejaVuSans)
# ==============================================================================================
def doc_03() -> dict:
    datos = [
        ("UX research & wireframes", "Investigación de usuarios y prototipos de baja fidelidad", "1", "1850.00",
         "UX research & wireframes"),
        ("Front-end development (hours)", "Desarrollo front-end de la nueva web corporativa", "64", "48.00",
         "Front-end development"),
        ("CMS integration & training", "Integración del gestor de contenidos y formación", "1", "1240.00",
         "CMS integration"),
        ("Product photo retouching", "Retoque de fotografías de producto para la tienda", "120", "6.50",
         "Product photo retouching"),
        ("Hosting plan Business, 12 months", "Alojamiento plan Business, 12 meses", "12", "29.90",
         "Hosting plan Business"),
        ("Support hours, October 2026", "Horas de soporte y mantenimiento de octubre", "7.5", "52.00",
         "Support hours"),
    ]
    lineas = [{"concepto": en, "es": es, "cantidad": D(ca), "precio": D(pr), "contiene": cont,
               "cant_txt": cant_es(ca), "precio_txt": num(pr)} for en, es, ca, pr, cont in datos]
    t = calcular(lineas)
    return {
        "archivo": "h3b_03_bilingue_ingles_castellano.pdf",
        "estilo": ("Plantilla bilingüe inglés/castellano de un estudio digital de Málaga (DejaVuSans, azul marino "
                   "y ámbar): marca 'LUMBRE' arriba a la izquierda, 'INVOICE / FACTURA' grande a la derecha con "
                   "metadatos etiqueta: valor alineados a la derecha ('Invoice no. / Nº factura', 'Issue date / "
                   "Fecha', 'Due date / Vencimiento', 'PO number / Nº pedido', 'Project / Proyecto'), bloques "
                   "'From / Emisor' y 'Bill to / Facturar a' con 'Tax ID / NIF', tabla con banda oscura "
                   "'Description / Concepto | Qty | Unit price | Amount' y conceptos en dos renglones (inglés en "
                   "negrita, castellano en gris), totales 'Subtotal', 'VAT 21%' y 'Total EUR', bloque 'Payment / "
                   "Forma de pago' con IBAN, importes en formato español (1.850,00), A4"),
        "empresa_nif": CIF_VIDAL,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "LDS-2026-0193",
        "fecha": date(2026, 11, 5),
        "fecha_txt": "05/11/2026",
        "emisor": {"nombre": "Lumbre Digital Studio, S.L.", "nif": CIF_LUMBRE, "domicilio_contiene": "29001"},
        "destinatario": {"nombre": "Conservas Hermanos Vidal del Cantábrico, S.A.", "nif": CIF_VIDAL,
                         "domicilio_contiene": "39740"},
        "nif_txt": {"emisor": f"Tax ID / NIF: {CIF_LUMBRE}", "destinatario": f"Tax ID / NIF: {CIF_VIDAL}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"pedido": "PO-VID-2026-077", "proyecto": "VID-WEB-2027"},
        "iban": "ES0029100345670012983301",
        "iban_txt": "ES00 2910 0345 6700 1298 3301",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "paginas": 1,
        "textos": ["INVOICE / FACTURA", "Invoice no. / Nº factura", "Issue date / Fecha", "Bill to / Facturar a",
                   "Qty", "Unit price", "Amount", "Subtotal", "VAT 21%", "Total EUR", "Due date / Vencimiento",
                   "05/12/2026"],
        "notas": ("Factura de proveedor RECIBIDA por la empresa usuaria (la conservera destinataria) sin marcas "
                  "VERI*FACTU. Rótulos mezclados inglés/castellano ('Invoice / Factura', 'Bill to / Facturar a', "
                  "'Tax ID / NIF', 'Qty', 'Unit price', 'Amount', 'Subtotal', 'VAT 21%', 'Total EUR'); conceptos en "
                  "dos renglones: el primero en inglés (el que recoge concepto_contiene) y el segundo, castellano en "
                  "gris. Importes en formato español con punto de miles y coma decimal. Cantidad decimal 7,5 horas. "
                  "Emisor y cliente españoles, operación interior sujeta al 21 %. Distractores: fecha de "
                  "vencimiento 05/12/2026 justo debajo de la de emisión, 'October 2026' y '12 months' en los "
                  "conceptos, 'Attn. / A la atención de'. "
                  f"Subtotal {num(t['base'])} x 21 % = {num(t['cuota'])}; Total EUR {num(t['total'])}."),
        "render": render_03,
    }


def render_03(ruta: Path, d: dict):
    W, H = A4
    F, FB = FUENTE["DejaVuSans"], FUENTE["DejaVuSans-Bold"]
    IN, AC, GR, LG = HexColor("#14213D"), HexColor("#E59500"), HexColor("#6B7280"), HexColor("#E5E7EB")
    em, de, t = d["emisor"], d["destinatario"], d["t"]
    c = nuevo_canvas(ruta, f"Invoice {d['numero']}", em["nombre"])
    M = 44

    rect(c, M, H - 66, 10, 22, relleno=AC)
    tx(c, M + 16, H - 64, "LUMBRE", FB, 22, IN)
    tx(c, M + 16, H - 78, "digital studio", F, 9, GR)
    tx(c, W - M, H - 62, "INVOICE / FACTURA", FB, 19, IN, "r")
    meta = [("Invoice no. / Nº factura", d["numero"]), ("Issue date / Fecha", d["fecha_txt"]),
            ("Due date / Vencimiento", "05/12/2026"), ("PO number / Nº pedido", d["referencias"]["pedido"]),
            ("Project / Proyecto", d["referencias"]["proyecto"])]
    yy = H - 88
    for k, v in meta:
        tx(c, W - M - 128, yy, k, F, 8, GR, "r")
        tx(c, W - M, yy, v, FB, 8.6, IN, "r")
        yy -= 13.5

    y = H - 112
    tx(c, M, y, "From / Emisor", F, 8, GR)
    for s, f, tam in ((em["nombre"], FB, 9.6), ("Calle Inventada 14, 3.º B", F, 8.8), ("29001 Málaga, España", F, 8.8),
                      (d["nif_txt"]["emisor"], F, 8.8), ("hello@lumbre.example · +34 950 000 000", F, 8.8)):
        y -= 12.5
        tx(c, M, y, s, f, tam, IN if f == FB else black)

    y = H - 205
    rect(c, M, y - 86, 300, 98, relleno=HexColor("#F5F6F8"))
    tx(c, M + 10, y, "Bill to / Facturar a", F, 8, GR)
    for s, f, tam in ((de["nombre"], FB, 9.6), ("Attn. / A la atención de: Dpto. de Marketing", F, 8.4),
                      ("Barrio Ficticio 7", F, 8.8), ("39740 Santoña (Cantabria)", F, 8.8),
                      (d["nif_txt"]["destinatario"], F, 8.8)):
        y -= 14
        tx(c, M + 10, y, s, f, tam, IN if f == FB else black)

    # tabla
    cols = [("Description / Concepto", 292, "l"), ("Qty", 50, "r"), ("Unit price", 80, "r"),
            ("Amount", W - 2 * M - 422, "r")]
    xs = [M]
    for _, w, _ in cols:
        xs.append(xs[-1] + w)
    ty = H - 330
    rect(c, M, ty - 20, W - 2 * M, 20, relleno=IN)
    for i, (k, w, al) in enumerate(cols):
        tx(c, xs[i] + 8 if al == "l" else xs[i + 1] - 8, ty - 13.5, k, FB, 8.6, white, al)
    y = ty - 20
    for ln in d["lineas"]:
        tx(c, xs[0] + 8, y - 14, ln["concepto"], FB, 8.8, IN)
        tx(c, xs[0] + 8, y - 25.5, ln["es"], F, 7.8, GR)
        for i, v in ((1, ln["cant_txt"]), (2, ln["precio_txt"]), (3, num(ln["importe"]))):
            tx(c, xs[i + 1] - 8, y - 14, v, FB if i == 3 else F, 8.8, IN if i == 3 else black, "r")
        y -= 33
        raya(c, M, y, W - M, y, LG, 0.8)

    # totales
    tyy = y - 22
    lx, vx = W - M - 200, W - M - 8
    for k, v in (("Subtotal", num(t["base"])), ("VAT 21%", num(t["cuota"]))):
        tx(c, lx, tyy, k, F, 9, GR)
        tx(c, vx, tyy, v, F, 9, black, "r")
        tyy -= 17
    rect(c, lx - 10, tyy - 12, W - M - lx + 10, 24, relleno=IN)
    tx(c, lx, tyy - 3.5, "Total EUR", FB, 10.5, white)
    tx(c, vx, tyy - 3.5, num(t["total"]), FB, 11.5, white, "r")

    # pago
    py = y - 22
    tx(c, M, py, "Payment / Forma de pago", F, 8, GR)
    tx(c, M, py - 14, "Transferencia bancaria · Bank transfer", FB, 9, IN)
    tx(c, M, py - 28, f"IBAN: {d['iban_txt']}", F, 8.8)
    tx(c, M, py - 42, "Please quote the invoice number / Indique el nº de factura", F, 8, GR)
    tx(c, M, py - 78, "Thank you for your business! / ¡Gracias por su confianza!", FB, 9, AC)

    raya(c, M, 60, W - M, 60, LG, 0.8)
    tx(c, W / 2, 46, f"{em['nombre']} · Registro Mercantil de Málaga, tomo 0000, folio 00, hoja MA-000000 · "
       f"NIF {CIF_LUMBRE}", F, 6.8, GR, "c")
    c.save()


# ==============================================================================================
# 4. Ingeniera técnica con NIE: IRPF 15 %, «Retención a cuenta IRPF» y «Neto a cobrar» (LiberationSerif)
# ==============================================================================================
_ANCHO_CONCEPTO_04 = 172.0


def doc_04() -> dict:
    datos = [
        ("Proyecto técnico de legalización de la instalación eléctrica de baja tensión de la nave 2", "ud", "1",
         "1480.00", "Proyecto técnico de legalización"),
        ("Dirección técnica de la instalación y certificado final", "ud", "1", "640.00",
         "Dirección técnica de la instalación"),
        ("Horas de ingeniería de detalle y cálculo de líneas", "h", "13.5", "46.40", "Horas de ingeniería de detalle"),
        ("Visitas técnicas a la instalación durante la ejecución", "ud", "5", "85.00", "Visitas técnicas"),
        ("Estudio lumínico de la zona de almacén", "ud", "1", "390.00", "Estudio lumínico"),
    ]
    registrar_fuentes()
    lineas = []
    for con, ud, ca, pr, cont in datos:
        renglones = partir(con, FUENTE["LibSerif"], 9.6, _ANCHO_CONCEPTO_04)
        lineas.append({"concepto": con, "renglones": renglones, "primera": renglones[0], "ud": ud,
                       "cantidad": D(ca), "precio": D(pr), "contiene": cont, "cant_txt": cant_es(ca),
                       "precio_txt": num(pr)})
    t = calcular(lineas, pct_ret=15)
    return {
        "archivo": "h3b_04_ingeniera_nie_irpf15_neto_cobrar.pdf",
        "estilo": ("Factura de ingeniera técnica autónoma (persona física con NIE 'Y'), LiberationSerif: banda "
                   "lateral izquierda verde petróleo a toda altura con nombre en dos renglones, profesión, NIE con "
                   "guiones, colegiación, domicilio, contacto y datos bancarios; a la derecha título 'FACTURA', "
                   "'N.º 2026/017' y lugar y fecha en formato largo ('Alicante, 2 de noviembre de 2026'), bloque "
                   "'Cliente', expediente, tabla con filetes Concepto | Cant. | Ud. | Precio | Importe y conceptos "
                   "multilínea, totales apilados Base imponible / IVA (21 %) / Total factura / 'Retención a cuenta "
                   "IRPF (15 %)' en negativo / 'Neto a cobrar', A4"),
        "empresa_nif": NIE_FERRAZ,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "2026/017",
        "fecha": date(2026, 11, 2),
        "fecha_txt": "2 de noviembre de 2026",
        "emisor": {"nombre": "Joana Ferraz Domingues", "nif": NIE_FERRAZ, "domicilio_contiene": "03001"},
        "destinatario": {"nombre": "Envases Plásticos del Vinalopó, S.L.", "nif": CIF_VINALOPO,
                         "domicilio_contiene": "03114"},
        "nif_txt": {"emisor": con_guion(NIE_FERRAZ), "destinatario": f"NIF: {CIF_VINALOPO}"},
        "nombre_txt": {"emisor": ["Joana Ferraz", "Domingues"]},  # nombre partido en dos renglones
        "lineas": lineas,
        "t": t,
        "referencias": {"expediente": "ING-26-031"},
        "iban": "ES0003117284905561230418",
        "iban_txt": "ES00 0311 7284 9055 6123 0418",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "paginas": 1,
        "textos": ["Retención a cuenta IRPF (15 %)", "Neto a cobrar", "Total factura", "IVA (21 %)",
                   "Ingeniera técnica industrial", "NIE"],
        "notas": ("Factura propia de una ingeniera técnica autónoma (persona física extranjera con NIE que empieza "
                  "por Y, impreso con guiones 'Y-4444444-F'; empresa usuaria = emisora) sin marcas VERI*FACTU: "
                  "documento preparatorio. El nombre de la emisora va partido en dos renglones dentro de una "
                  "banda lateral oscura (texto blanco) junto con el NIE, la colegiación (n.º 4.187), el domicilio "
                  "y el IBAN; la fecha está en formato largo ('Alicante, 2 de noviembre de 2026'). Conceptos "
                  "largos que ocupan 2 renglones (concepto_contiene = comienzo del primero); cantidad decimal "
                  "13,5 h. La retención se rotula 'Retención a cuenta IRPF (15 %)' con importe negativo y el "
                  "líquido 'Neto a cobrar'. Distractores: potencia 180 kW y 'nave 2' en el concepto, plazo de 30 "
                  "días, referencia a la Ley 35/2006. "
                  f"Base {num(t['base'])} x 21 % = {num(t['cuota'])}; total factura {num(t['total'])}; IRPF 15 % = "
                  f"{num(t['ret'])}; neto a cobrar {num(t['pagar'])}."),
        "render": render_04,
    }


def render_04(ruta: Path, d: dict):
    W, H = A4
    F, FB, FI = FUENTE["LibSerif"], FUENTE["LibSerif-Bold"], FUENTE["LibSerif-Italic"]
    TE, CLARO, GR, LN = HexColor("#23474A"), HexColor("#CFE3E1"), HexColor("#5F6B6D"), HexColor("#9DB3B1")
    em, de, t = d["emisor"], d["destinatario"], d["t"]
    c = nuevo_canvas(ruta, f"Factura {d['numero']}", em["nombre"])
    SB = 178
    rect(c, 0, 0, SB, H, relleno=TE)
    x = 22
    tx(c, x, H - 72, "Joana Ferraz", FB, 18, white)
    tx(c, x, H - 92, "Domingues", FB, 18, white)
    tx(c, x, H - 112, "Ingeniera técnica industrial", FI, 10.5, CLARO)
    raya(c, x, H - 124, SB - 22, H - 124, CLARO, 0.6)
    y = H - 146
    for lab, val in (("NIE", d["nif_txt"]["emisor"]), ("Colegiación", "Colegiada n.º 4.187"),
                     ("Domicilio", "Calle Cronista Imaginario 18, 2.º izda."), ("", "03001 Alicante"),
                     ("Contacto", "Tel. 960 000 000"), ("", "joana.ferraz@ingenieria.example")):
        if lab:
            tx(c, x, y, lab.upper(), FB, 7.2, CLARO)
            y -= 12.5
        tx(c, x, y, val, FB if lab == "NIE" else F, 9.6 if lab == "NIE" else 9, white)
        y -= 18
    tx(c, x, 128, "DATOS BANCARIOS", FB, 7.2, CLARO)
    tx(c, x, 114, "IBAN", F, 8.4, CLARO)
    tx(c, x, 101, d["iban_txt"], FB, 8.4, white)

    X0, X1 = SB + 32, W - 36
    tx(c, X0, H - 78, "FACTURA", FB, 28, TE)
    tx(c, X1, H - 64, f"N.º {d['numero']}", FB, 12, TE, "r")
    tx(c, X1, H - 80, f"Alicante, {d['fecha_txt']}", F, 10.5, black, "r")

    y = H - 128
    tx(c, X0, y, "CLIENTE", FB, 8, TE)
    raya(c, X0, y - 4, X1, y - 4, LN, 0.6)
    y -= 20
    for s in (de["nombre"], "Pol. Ind. Altos Ficticios, c/ del Torno 44", "03114 Alicante",
              d["nif_txt"]["destinatario"]):
        tx(c, X0, y, s, FB if s == de["nombre"] else F, 11.5 if s == de["nombre"] else 10)
        y -= 14
    y -= 8
    tx(c, X0, y, f"Expediente: {d['referencias']['expediente']} · Su referencia: ampliación nave 2", FI, 9.8, GR)

    cols = [("Concepto", _ANCHO_CONCEPTO_04 + 6, "l"), ("Cant.", 38, "r"), ("Ud.", 26, "c"), ("Precio", 52, "r"),
            ("Importe", X1 - X0 - _ANCHO_CONCEPTO_04 - 122, "r")]
    xs = [X0]
    for _, w, _ in cols:
        xs.append(xs[-1] + w)
    ty = y - 26
    raya(c, X0, ty, X1, ty, TE, 1.2)
    for i, (k, w, al) in enumerate(cols):
        xx = {"l": xs[i], "r": xs[i + 1], "c": xs[i] + w / 2}[al]
        tx(c, xx, ty - 13, k, FB, 9.6, TE, al)
    yy = ty - 19
    raya(c, X0, yy, X1, yy, TE, 0.6)
    for ln in d["lineas"]:
        yy -= 13.5
        for k, r in enumerate(ln["renglones"]):
            tx(c, X0, yy - k * 12, r, F, 9.6)
        for i, v in ((1, ln["cant_txt"]), (2, ln["ud"]), (3, ln["precio_txt"]), (4, num(ln["importe"]))):
            al = cols[i][2]
            xx = {"l": xs[i], "r": xs[i + 1], "c": xs[i] + cols[i][1] / 2}[al]
            tx(c, xx, yy, v, FB if i == 4 else F, 9.6, black, al)
        yy -= 12 * (len(ln["renglones"]) - 1) + 7
        raya(c, X0, yy, X1, yy, LN, 0.4)
    raya(c, X0, yy, X1, yy, TE, 1.0)

    lx = X1 - 196
    yy -= 22
    for k, v, f in (("Base imponible", num(t["base"]), F), ("IVA (21 %)", num(t["cuota"]), F),
                    ("Total factura", num(t["total"]), FB),
                    (f"Retención a cuenta IRPF ({t['pct_ret']:.0f} %)", num(-t["ret"]), F)):
        tx(c, lx, yy, k, f, 10)
        tx(c, X1, yy, v, f, 10, black, "r")
        yy -= 16
    raya(c, lx, yy + 6, X1, yy + 6, TE, 0.8)
    yy -= 10
    tx(c, lx, yy, "Neto a cobrar", FB, 12.5, TE)
    tx(c, X1, yy, f"{num(t['pagar'])} €", FB, 12.5, TE, "r")

    yy -= 46
    tx(c, X0, yy, "Forma de pago: transferencia bancaria a la cuenta indicada en el plazo de 30 días.", F, 9.6)
    yy -= 30
    for s in partir("Factura sujeta a retención a cuenta del IRPF del 15 % por rendimientos de actividades "
                    "profesionales (art. 101.5 de la Ley 35/2006). Sus datos se tratan únicamente para la gestión "
                    "de esta relación profesional.", FI, 8.6, X1 - X0):
        tx(c, X0, yy, s, FI, 8.6, GR)
        yy -= 11
    c.save()


DOCUMENTOS = [doc_01, doc_02, doc_03, doc_04]


# ----------------------------------------------------------------------------------------------
# JSON esperado (mismo esquema que tests/corpus_holdout2)
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


def pdftotext(ruta: Path, pagina: int | None = None) -> str:
    args = ["pdftotext", "-layout"]
    if pagina:
        args += ["-f", str(pagina), "-l", str(pagina)]
    return subprocess.run(args + [str(ruta), "-"], capture_output=True, text=True, check=True).stdout


def num_paginas(ruta: Path) -> int:
    info = subprocess.run(["pdfinfo", str(ruta)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))


def coherencia(e: dict, d: dict) -> list[str]:
    """Comprobaciones del JSON independientes del PDF: NIF, situación, claves y aritmética exacta."""
    fallos: list[str] = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    for parte in ("emisor", "destinatario"):
        debe(es_valido(e[parte]["nif"]), f"NIF {parte} inválido {e[parte]['nif']}")
        debe(normalizar(e[parte]["nif"]) == e[parte]["nif"], f"NIF {parte} no normalizado")
        debe(re.fullmatch(r"\d{5}", e[parte]["domicilio_contiene"]) is not None, f"CP {parte}")
        debe(nif_ficticio(e[parte]["nif"]), f"NIF {parte} {e[parte]['nif']} no evidentemente ficticio")
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
        lin = sum((ln["importe"] for ln in d["lineas"] if D(ln["iva"]) == D(g["tipo_iva"])), D("0"))
        debe(lin == D(g["base"]), f"líneas al {g['tipo_iva']} != base del tipo")
    debe(base + cuota == total, "base + cuota != total")
    for ln, x in zip(d["lineas"], e["lineas"]):
        if ln.get("cantidad") is not None:
            calc = q2(D(ln["cantidad"]) * D(ln["precio"]))
            debe(calc == D(x["importe"]), f"línea {x['concepto_contiene']}: {calc} != {x['importe']}")
            debe(sin_empate(ln["cantidad"]) and sin_empate(ln["precio"]),
                 f"empate de redondeo en cantidad/precio de {x['concepto_contiene']}")
            debe(D(x["cantidad"]) == q2(ln["cantidad"]) and D(x["precio"]) == q2(ln["precio"]),
                 f"cantidad/precio del JSON de {x['concepto_contiene']}")
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


def verificar_texto(ruta: Path, e: dict, d: dict) -> list[str]:
    """El PDF (capa de texto, pdftotext -layout) debe mostrar todo lo que el JSON espera."""
    fallos: list[str] = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    texto = pdftotext(ruta)
    esp, plano, nt, fmt = _esp(texto), _plano(texto), norm(texto), d["fmt"]
    paginas = num_paginas(ruta)
    debe(paginas == d["paginas"], f"páginas {paginas} != {d['paginas']}")
    debe(e["numero"] in texto, f"número {e['numero']}")
    debe(_esp(d["fecha_txt"]) in esp, f"fecha {d['fecha_txt']}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(_esp(d["nif_txt"][parte]) in esp, f"NIF {parte} impreso {d['nif_txt'][parte]!r}")
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        trozos = (d.get("nombre_txt") or {}).get(parte) or [p["nombre"]]
        debe(all(_esp(x) in esp for x in trozos) and norm("".join(trozos)) == norm(p["nombre"]),
             f"nombre {parte} {p['nombre']}")
        debe(re.search(rf"(?<!\d){p['domicilio_contiene']}(?!\d)", texto) is not None,
             f"domicilio {parte} {p['domicilio_contiene']}")
    # los NIF válidos impresos son exactamente los dos de las partes
    impresos = {n for n, _, _ in buscar_nifs(texto)}
    debe(impresos == {e["emisor"]["nif"], e["destinatario"]["nif"]}, f"NIF impresos {impresos}")
    for ln in d["lineas"]:
        debe(_esp(ln["contiene"]) in esp, f"concepto {ln['contiene']}")
        debe(_esp(ln["primera"]) in esp, f"primer renglón {ln['primera']}")
        debe(_hay_importe(fmt(ln["importe"]), esp), f"importe de línea {fmt(ln['importe'])}")
        if ln.get("precio_txt"):
            debe(_hay_importe(ln["precio_txt"], esp), f"precio de línea {ln['precio_txt']}")
        if ln.get("cant_txt"):
            debe(_hay_importe(ln["cant_txt"], esp), f"cantidad {ln['cant_txt']}")
    t = d["t"]
    for g in t["desglose"]:
        debe(_hay_importe(fmt(g["base"]), esp), f"base al {g['tipo']} {fmt(g['base'])}")
        debe(_hay_importe(fmt(g["cuota"]), esp), f"cuota al {g['tipo']} {fmt(g['cuota'])}")
        tipos_txt = (d.get("tipos_txt") or {}).get(f"{g['tipo']:.2f}")
        if tipos_txt:
            debe(all(_hay_importe(x, esp) if x[0].isdigit() else _esp(x) in esp for x in tipos_txt),
                 f"tipo {g['tipo']:.0f} % ({tipos_txt})")
        else:
            debe(re.search(rf"(?<![\d,]){g['tipo']:.0f}\s*%", texto) is not None, f"tipo {g['tipo']:.0f} %")
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
    # cabecera de columnas en cada página (repetida en la 2)
    if d.get("cabecera_columnas"):
        for pag in range(1, paginas + 1):
            tp = pdftotext(ruta, pag)
            for k in d["cabecera_columnas"]:
                debe(k in tp, f"cabecera '{k}' ausente en la página {pag}")
        # las líneas se reparten entre las dos páginas y los totales están en la última
        ultima = _esp(pdftotext(ruta, paginas))
        debe(_hay_importe(fmt(t["total"]), ultima), "total fuera de la última página")
        primera = _esp(pdftotext(ruta, 1))
        debe(not _hay_importe(fmt(t["total"]), primera), "el total no debe estar en la página 1")
        en_p2 = sum(1 for ln in d["lineas"] if _esp(ln["primera"]) in ultima)
        debe(0 < en_p2 < len(d["lineas"]), f"reparto de líneas entre páginas ({en_p2} en la última)")
    return fallos


def verificar_familia(d: dict, e: dict) -> list[str]:
    """Requisitos propios de cada familia del encargo."""
    fallos = []
    n = d["archivo"][len(PREFIJO):len(PREFIJO) + 2]
    if n == "01":
        if len(e["lineas"]) < 30:
            fallos.append("familia 1: menos de 30 líneas")
        if not any(x["concepto_contiene"].startswith("Portes") for x in e["lineas"]):
            fallos.append("familia 1: falta la línea de portes")
        if not {"kg", "t"} <= {ln["ud"] for ln in d["lineas"]}:
            fallos.append("familia 1: faltan unidades kg y t")
        nds = {len(ln["precio_txt"].split(",")[1]) for ln in d["lineas"]}
        if not {3, 4} <= nds:
            fallos.append(f"familia 1: decimales de precio {nds}")
    elif n == "02":
        if {g["tipo_iva"] for g in e["desglose"]} != {"4.00", "10.00", "21.00"}:
            fallos.append("familia 2: tipos de IVA")
        neg = [x for x in e["lineas"] if D(x["importe"]) < 0]
        if len(neg) != 1 or not neg[0]["concepto_contiene"].startswith("Abono envases"):
            fallos.append("familia 2: línea negativa 'Abono envases'")
    elif n == "03":
        pass  # rótulos en inglés comprobados en 'textos'
    elif n == "04":
        if e["emisor"]["nif"][0] not in "XYZ":
            fallos.append("familia 4: el emisor no tiene NIE")
        if not e["retencion"] or e["retencion"]["porcentaje"] != "15.00":
            fallos.append("familia 4: retención 15 %")
    return fallos


def nifs_otros_corpus() -> set[str]:
    """NIF de los demás corpus: en los JSON y en el texto de los PDF de tests/corpus* (excepto esta familia)."""
    nifs: set[str] = set()
    for carpeta in sorted((RAIZ / "tests").glob("corpus*")):
        for js in carpeta.rglob("*.json"):
            if js.name.startswith(PREFIJO):
                continue
            try:
                txt = js.read_text(encoding="utf-8")
            except OSError:
                continue
            nifs |= {n for n, _, _ in buscar_nifs(txt)}
        for pdf in carpeta.rglob("*.pdf"):
            if pdf.name.startswith(PREFIJO):
                continue
            try:
                nifs |= {n for n, _, _ in buscar_nifs(pdftotext(pdf))}
            except subprocess.CalledProcessError:
                continue
    return nifs


def main(argv: list[str]) -> int:
    registrar_fuentes()
    SALIDA.mkdir(parents=True, exist_ok=True)
    otros = nifs_otros_corpus()
    vistos: dict[str, str] = {}
    total_fallos = 0
    for fabrica in DOCUMENTOS:
        d = fabrica()
        ruta = SALIDA / d["archivo"]
        e = esperado(d)
        fallos = coherencia(e, d) + verificar_familia(d, e)
        for parte in ("emisor", "destinatario"):
            n = e[parte]["nif"]
            if n in otros:
                fallos.append(f"NIF {n} ya usado en otro corpus")
            if vistos.get(n, e[parte]["nombre"]) != e[parte]["nombre"]:
                fallos.append(f"NIF {n} repetido con otro nombre en esta familia")
            vistos[n] = e[parte]["nombre"]
        d["render"](ruta, d)
        fallos += verificar_texto(ruta, e, d)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total_fallos += len(fallos)
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}  ({len(e['lineas'])} líneas, base "
              f"{e['base_imponible']}, total {e['total_factura']})" + (f"  -> {fallos}" if fallos else ""))
    print(f"{len(DOCUMENTOS)} documentos en {SALIDA.relative_to(RAIZ)}; fallos: {total_fallos}")
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
