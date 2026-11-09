"""Corpus sintético «obra»: facturas de servicios de construcción e ingeniería facturados por certificación.

Familia inspirada en las facturas de un project manager / coordinador de seguridad y salud a una promotora:
rejillas tipo Excel, «Certificación nº», «Nº pedido», «Partida presupuestaria», «Obra», «Contrato», párrafo
descriptivo del proyecto, datos registrales del emisor en el PIE, destinatario arriba a la derecha SIN etiqueta,
columna de cantidad ANTES del concepto, resumen en fila de cabecera (Base Imponible | IVA | Total IVA |
TOTAL FACTURA) con los valores en la fila inferior e IBAN escrito con espaciado irregular.

TODOS LOS DATOS SON FICTICIOS: empresas, domicilios, personas, referencias e IBAN inventados. Los NIF tienen dígito
de control válido (app.nif.completar_cif) pero no corresponden a ninguna empresa conocida. Los IBAN son
deliberadamente falsos (ES00..., ES12 3456...).

Genera 5 PDF y, por cada uno, un JSON con los valores esperados (mismo nombre base). Determinista: sin azar y con
canvas(invariant=1), de modo que dos ejecuciones producen ficheros idénticos.

Uso (desde /home/user/verifactu-app):  python scripts/corpus/obra.py [directorio_salida]
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import completar_cif, es_valido  # noqa: E402

SALIDA_DEFECTO = RAIZ / "tests" / "corpus" / "obra"
SEMILLA = 20261109  # no se usa azar; se deja fijada por coherencia con el resto del corpus
AVISO = "Documento sintético para pruebas. Datos ficticios."

D = Decimal
C2 = Decimal("0.01")
GRIS_XL = HexColor("#A6A6A6")      # borde de celda tipo Excel
GRIS_TENUE = HexColor("#DADADA")   # líneas de división impresas de Excel
GRIS_TXT = HexColor("#404040")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


# ----------------------------------------------------------------------------------------------------------------
# Importes, fechas y NIF
# ----------------------------------------------------------------------------------------------------------------
def q2(v) -> Decimal:
    return Decimal(v).quantize(C2, rounding=ROUND_HALF_UP)


def s2(v) -> str:
    """Importe para el JSON esperado: punto decimal y dos decimales."""
    return f"{q2(v):.2f}"


def es(v, miles: bool = True, suf: str = "") -> str:
    """Formato español: 1.234,56 (o 1234,56 sin separador de miles) con sufijo opcional (' €', ' EUR')."""
    v = q2(v)
    ent, dec = f"{abs(v):.2f}".split(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", ".")
    return f"{'-' if v < 0 else ''}{ent},{dec}{suf}"


def fecha_txt(iso: str, estilo: str) -> str:
    a, m, d = (int(x) for x in iso.split("-"))
    return {
        "dd/mm/yyyy": f"{d:02d}/{m:02d}/{a}",
        "dd-mm-yy": f"{d:02d}-{m:02d}-{a % 100:02d}",
        "dd-mm-yyyy": f"{d:02d}-{m:02d}-{a}",
        "largo": f"{d} de {MESES[m - 1]} de {a}",
        "dd.mm.yyyy": f"{d:02d}.{m:02d}.{a}",
    }[estilo]


def nif_txt(nif: str, estilo: str) -> str:
    return {"guion": f"{nif[0]}-{nif[1:]}", "plano": nif, "espacio": f"{nif[0]} {nif[1:]}"}[estilo]


def linea(cant, precio, concepto: str, contiene: str, **extra) -> dict:
    cant, precio = D(cant), D(precio)
    return {"cant": cant, "precio": precio, "importe": q2(cant * precio), "concepto": concepto,
            "contiene": contiene, **extra}


def totales(lineas: list[dict], tipo) -> tuple[Decimal, Decimal, Decimal]:
    base = q2(sum((l["importe"] for l in lineas), D("0")))
    cuota = q2(base * D(tipo) / 100)
    return base, cuota, q2(base + cuota)


# ----------------------------------------------------------------------------------------------------------------
# Empresas ficticias (nombres inventados; NIF con control válido generado)
# ----------------------------------------------------------------------------------------------------------------
EMPRESAS = {
    "olmeral": {"nombre": "OLMERAL GESTIÓN DE PROYECTOS, S.L.", "nif": completar_cif("B", "8761234")},
    "brezomar": {"nombre": "BREZOMAR DESARROLLOS RESIDENCIALES, S.A.", "nif": completar_cif("A", "2894417")},
    "tormaren": {"nombre": "EDIFICACIONES TORMARÉN, S.L.", "nif": completar_cif("B", "9945102")},
    "ardanel": {"nombre": "ARDANEL INVERSIONES INMOBILIARIAS, S.L.", "nif": completar_cif("B", "5033871")},
    "ribazul": {"nombre": "OBRAS Y CONTRATAS RIBAZUL, S.A.", "nif": completar_cif("A", "4178023")},
    "valdorniz": {"nombre": "VALDORNIZ PROMOCIONES, S.L.", "nif": completar_cif("B", "9182456")},
    "quelvar": {"nombre": "QUELVAR INGENIEROS CONSULTORES, S.L.P.", "nif": completar_cif("B", "9871190")},
    "lindamar": {"nombre": "LINDAMAR SUELOS Y PROMOCIONES, S.A.", "nif": completar_cif("A", "4632075")},
    "castredo": {"nombre": "CASTREDO ARQUITECTURA TÉCNICA Y SEGURIDAD, S.L.", "nif": completar_cif("B", "7340218")},
    "alcazen": {"nombre": "GRUPO INMOBILIARIO ALCAZÉN, S.A.", "nif": completar_cif("A", "3058614")},
}
for _e in EMPRESAS.values():
    assert es_valido(_e["nif"]), _e


# ----------------------------------------------------------------------------------------------------------------
# Primitivas de dibujo
# ----------------------------------------------------------------------------------------------------------------
def nuevo_lienzo(ruta: Path, tam, titulo: str, autor: str) -> canvas.Canvas:
    c = canvas.Canvas(str(ruta), pagesize=tam, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject(AVISO)
    c.setCreator("Hoja de cálculo - exportación a PDF (corpus sintético verifactu-app)")
    return c


def txt(c, x, y, s, fuente="Helvetica", tam=9, al="l", color=black):
    c.setFont(fuente, tam)
    c.setFillColor(color)
    if al == "r":
        c.drawRightString(x, y, s)
    elif al == "c":
        c.drawCentredString(x, y, s)
    else:
        c.drawString(x, y, s)


def parrafo(c, x, y, s, fuente, tam, ancho, interl=None, al="l", color=black) -> float:
    interl = interl or tam * 1.25
    for l in simpleSplit(s, fuente, tam, ancho):
        txt(c, x if al == "l" else (x + ancho / 2 if al == "c" else x + ancho), y, l, fuente, tam, al, color)
        y -= interl
    return y


def celdas(c, x0, y, filas, *, alto, fuente, fuente_b, tam, rejilla=GRIS_XL, lw=0.5, sep="",
           relleno_etq=None, pad=4, color=black, fondo=None) -> float:
    """Rejilla de campos. Cada celda es (etiqueta|None, valor|None, ancho).
    Con etiqueta y valor en la misma celda se escribe «etiqueta+sep+valor» (etiqueta en negrita)."""
    c.setLineWidth(lw)
    for fila in filas:
        x = x0
        for etq, val, w in fila:
            relleno = relleno_etq if (relleno_etq is not None and etq and val is None) else fondo
            if relleno is not None:
                c.setFillColor(relleno)
                c.rect(x, y - alto, w, alto, stroke=0, fill=1)
            c.setStrokeColor(rejilla)
            c.rect(x, y - alto, w, alto, stroke=1, fill=0)
            base = y - alto / 2 - tam * 0.35
            tx = x + pad
            if etq:
                e = etq + (sep if val is not None else "")
                txt(c, tx, base, e, fuente_b, tam, color=color)
                tx += stringWidth(e, fuente_b, tam)
            if val is not None:
                txt(c, tx, base, val, fuente, tam, color=color)
            x += w
        y -= alto
    return y


def tabla(c, x0, y, cols, filas, *, fuente, fuente_b, tam, interl=None, rejilla=GRIS_XL, lw=0.5, pad=3,
          cab_relleno=None, cab_color=black, col_ajuste=None, alto_min=0.0, cab_tam=None,
          solo_marco_filas=False, fuente_filas=None, fondo=None) -> float:
    """Tabla tipo hoja de cálculo. cols: [(título con \\n, ancho, alineación)]; filas: listas de textos.
    La columna col_ajuste se parte en varias líneas. Devuelve la y inferior."""
    interl = interl or tam * 1.22
    cab_tam = cab_tam or tam
    anchos = [w for _, w, _ in cols]
    xs = [x0]
    for w in anchos:
        xs.append(xs[-1] + w)

    def x_al(i, al):
        return xs[i] + pad if al == "l" else (xs[i + 1] - pad if al == "r" else (xs[i] + xs[i + 1]) / 2)

    cab = [t.split("\n") for t, _, _ in cols]
    n = max(len(l) for l in cab)
    hh = n * cab_tam * 1.2 + 2 * pad
    plan = []
    for f_i, fila in enumerate(filas):
        fnt = (fuente_filas or {}).get(f_i, fuente)
        partes = simpleSplit(fila[col_ajuste], fnt, tam, anchos[col_ajuste] - 2 * pad) if col_ajuste is not None else []
        plan.append((fila, fnt, partes, max(max(1, len(partes)) * interl + 2 * pad, alto_min)))
    if fondo is not None:
        c.setFillColor(fondo)
        c.rect(xs[0], y - hh - sum(p[3] for p in plan), xs[-1] - xs[0], hh + sum(p[3] for p in plan), stroke=0, fill=1)
    if cab_relleno is not None:
        c.setFillColor(cab_relleno)
        c.rect(xs[0], y - hh, xs[-1] - xs[0], hh, stroke=0, fill=1)
    for i, (lin, (_, _, al)) in enumerate(zip(cab, cols)):
        off = (n - len(lin)) * cab_tam * 1.2 / 2
        for k, l in enumerate(lin):
            txt(c, x_al(i, al), y - pad - off - cab_tam * 0.9 - k * cab_tam * 1.2, l, fuente_b, cab_tam, al,
                cab_color)
    ys = [y, y - hh]
    yf = y - hh
    for fila, fnt, partes, rh in plan:
        yy = yf - pad - tam * 0.85
        for i, v in enumerate(fila):
            al = cols[i][2]
            if i == col_ajuste:
                for k, p in enumerate(partes):
                    txt(c, x_al(i, al), yy - k * interl, p, fnt, tam, al)
            elif v:
                txt(c, x_al(i, al), yy, v, fnt, tam, al)
        yf -= rh
        ys.append(yf)
    c.setStrokeColor(rejilla)
    c.setLineWidth(lw)
    for x in xs:
        c.line(x, ys[0], x, ys[-1])
    for i, yv in enumerate(ys):
        if not solo_marco_filas or i in (0, 1, len(ys) - 1):
            c.line(xs[0], yv, xs[-1], yv)
    return yf


def lineas_division_excel(c, x0, y0, x1, y1, paso_x=64.0, paso_y=15.0):
    """Líneas de división tenues (como al imprimir una hoja de Excel con «Imprimir líneas de división»)."""
    c.setStrokeColor(GRIS_TENUE)
    c.setLineWidth(0.3)
    x = x0
    while x <= x1 + 0.1:
        c.line(x, y0, x, y1)
        x += paso_x
    y = y0
    while y <= y1 + 0.1:
        c.line(x0, y, x1, y)
        y += paso_y


def tapar(c, x, y_sup, ancho, alto, color=white):
    """Rango de celdas con relleno blanco: oculta las líneas de división bajo un bloque de texto libre."""
    c.setFillColor(color)
    c.rect(x, y_sup - alto, ancho, alto, stroke=0, fill=1)


def aviso_sintetico(c, x, y=10):
    txt(c, x, y, AVISO, "Helvetica-Oblique", 5.5, color=HexColor("#8C8C8C"))


# ----------------------------------------------------------------------------------------------------------------
# JSON esperado
# ----------------------------------------------------------------------------------------------------------------
def esperado(*, archivo, estilo, empresa_nif, tipo_documento, situacion, numero, fecha, emisor, emisor_dom,
             destinatario, destinatario_dom, lineas, tipo_iva, base, cuota, total, retencion, total_a_pagar,
             referencias, iban, forma_pago_contiene, indicios, notas) -> dict:
    return {
        "archivo": archivo,
        "estilo": estilo,
        "empresa_nif": empresa_nif,
        "tipo_documento": tipo_documento,
        "situacion_esperada": situacion,
        "numero": numero,
        "fecha": fecha,
        "emisor": {"nombre": emisor["nombre"], "nif": emisor["nif"], "domicilio_contiene": emisor_dom},
        "destinatario": {"nombre": destinatario["nombre"], "nif": destinatario["nif"],
                         "domicilio_contiene": destinatario_dom},
        "lineas": [{"concepto_contiene": l["contiene"], "cantidad": s2(l["cant"]), "precio": s2(l["precio"]),
                    "importe": s2(l["importe"])} for l in lineas],
        "desglose": [{"tipo_iva": s2(tipo_iva), "base": s2(base), "cuota": s2(cuota)}],
        "base_imponible": s2(base),
        "cuota_iva": s2(cuota),
        "total_factura": s2(total),
        "retencion": retencion,
        "total_a_pagar": None if total_a_pagar is None else s2(total_a_pagar),
        "referencias": referencias,
        "iban": iban,
        "forma_pago_contiene": forma_pago_contiene,
        "indicios_esperados": indicios,
        "notas": notas,
    }


# ----------------------------------------------------------------------------------------------------------------
# 1) Dos líneas de certificación (project manager -> promotora), muy próxima al modelo de referencia
# ----------------------------------------------------------------------------------------------------------------
def doc_01(dest: Path) -> dict:
    archivo = "obra_01_dos_lineas_certificacion.pdf"
    em, cl = EMPRESAS["olmeral"], EMPRESAS["brezomar"]
    numero, fecha = "0214/26", "2026-10-30"
    refs = {"obra": "RESIDENCIAL BREZOMAR JARDINES", "contrato": "CT-BRZ-2025/09", "certificacion": "9",
            "pedido": "PC26-00318", "partida_presupuestaria": "G02040117"}
    lineas = [
        linea("1", "2450.00", "Certificación nº 9. Dirección de ejecución material de la obra (DEM) "
              "correspondiente al mes de OCTUBRE de 2026", "Dirección de ejecución material"),
        linea("0.25", "7380.00", "Certificación nº 9. Project monitoring: seguimiento de plazos y costes, "
              "25 % de los honorarios del hito H3 (estructura)", "Project monitoring"),
    ]
    base, cuota, total = totales(lineas, 21)
    assert (base, cuota, total) == (D("4295.00"), D("901.95"), D("5196.95"))
    iban_txt, iban = "ES 00 1234 5678 901234567890", "ES0012345678901234567890"

    W, H = A4
    c = nuevo_lienzo(dest / archivo, A4, "FACTURAS OLMERAL", "Administración")
    # Cabecera del emisor: logotipo + lema + domicilio (sin razón social ni NIF: van al pie)
    c.setFillColor(HexColor("#2E5E4E"))
    c.rect(40, H - 78, 34, 34, stroke=0, fill=1)
    txt(c, 57, H - 67, "OG", "Helvetica-Bold", 15, "c", white)
    txt(c, 82, H - 56, "OLMERAL", "Helvetica-Bold", 15, color=HexColor("#2E5E4E"))
    txt(c, 82, H - 69, "Gestión de Proyectos y Dirección de Obra", "Helvetica-Oblique", 8.5, color=GRIS_TXT)
    txt(c, 40, H - 98, "Calle del Brezo Inventado, nº 7 - Local 2", "Helvetica", 8.5)
    txt(c, 40, H - 109, "Tres Cantos, 28760 Madrid", "Helvetica", 8.5)
    # Destinatario arriba a la derecha, SIN etiqueta
    x = 320
    txt(c, x, H - 50, cl["nombre"], "Helvetica-Bold", 9)
    txt(c, x, H - 62, "Avenida de la Ribera Imaginaria, 41,", "Helvetica", 9)
    txt(c, x, H - 74, "Edificio Norte, planta 2ª, 28050, Madrid.", "Helvetica", 9)
    txt(c, x, H - 98, "CIF:" + nif_txt(cl["nif"], "guion"), "Helvetica", 9)

    txt(c, 150, H - 150, "FACTURA", "Helvetica-Bold", 15)
    y = celdas(c, 40, H - 162, [
        [("Nº FACTURA :", None, 95), (None, numero, 110), ("Nº CONTRATO :", None, 115), (None, refs["contrato"], 195)],
        [("FECHA :", None, 95), (None, fecha_txt(fecha, "dd/mm/yyyy"), 110), ("Nº CERTIFICACIÓN :", None, 115),
         (None, refs["certificacion"], 195)],
        [("OBRA :", None, 95), (None, refs["obra"], 420)],
    ], alto=17, fuente="Helvetica", fuente_b="Helvetica-Bold", tam=8.5)

    y -= 22
    txt(c, 48, y, "DESCRIPCIÓN DE LOS SERVICIOS PROFESIONALES:", "Helvetica-Bold", 9)
    y -= 18
    for s in ["RESIDENCIAL BREZOMAR JARDINES, DE 86 VIVIENDAS, GARAJE, TRASTEROS Y ZONAS COMUNES",
              "PARCELA M-14 DEL PLAN PARCIAL «LOS ALMEZOS NORTE»",
              f"número de pedido. - {refs['pedido']}",
              f"Partida presupuestaria. - {refs['partida_presupuestaria']}",
              "Responsable. - Marta Ibarguren Soler"]:
        txt(c, W / 2, y, s, "Helvetica", 8.5, "c")
        y -= 11.5

    y -= 14
    filas = [[es(l["cant"]), l["concepto"], es(l["precio"], suf=" €"), es(l["importe"], suf=" €")] for l in lineas]
    y = tabla(c, 40, y, [("UDS.", 55, "c"), ("CONCEPTO", 285, "l"), ("PRECIO\nUNITARIO", 85, "r"),
                         ("IMPORTE\nTOTAL", 90, "r")], filas,
              fuente="Helvetica", fuente_b="Helvetica-Bold", tam=8.5, col_ajuste=1, alto_min=34, pad=5)
    y = celdas(c, 380, y, [[("TOTAL", None, 85), (None, "", 90)]], alto=17, fuente="Helvetica",
               fuente_b="Helvetica-Bold", tam=8.5)
    txt(c, 550, y + 17 / 2 - 8.5 * 0.35, es(base, suf=" €"), "Helvetica-Bold", 8.5, "r")

    y -= 26
    y = tabla(c, 160, y, [("Base Imponible", 110, "c"), ("IVA", 70, "c"), ("Total IVA", 100, "c"),
                          ("TOTAL FACTURA", 115, "c")],
              [[es(base, suf=" €"), "21%", es(cuota, suf=" €"), es(total, suf=" €")]],
              fuente="Helvetica", fuente_b="Helvetica-Bold", tam=9, alto_min=20, pad=5)

    y -= 30
    txt(c, 48, y, "Forma de pago:", "Helvetica-Bold", 8.5)
    txt(c, 150, y, "A 60 días por transferencia a la cuenta corriente Banco de Pruebas Ficticio", "Helvetica", 8.5)
    txt(c, 150, y - 11, f"nº IBAN {iban_txt}", "Helvetica", 8.5)

    # Pie con los datos registrales del emisor
    c.setStrokeColor(HexColor("#2E5E4E"))
    c.setLineWidth(0.8)
    c.line(40, 74, W - 40, 74)
    txt(c, 48, 62, em["nombre"], "Helvetica-Bold", 8)
    txt(c, 300, 62, "C.I.F.: " + nif_txt(em["nif"], "guion"), "Helvetica", 8)
    txt(c, 48, 51, "Tfno.: +34 600 000 101", "Helvetica", 8)
    txt(c, 200, 51, "E-mail: administracion@olmeral.example", "Helvetica", 8)
    txt(c, 48, 35, "Inscrita en el Registro Mercantil de Madrid. Tomo 40.112, Folio: 87, Sección: 8, Hoja M-712345, "
                   "Inscripción: 1.", "Helvetica", 7.5)
    aviso_sintetico(c, 48)
    c.showPage()
    c.save()

    return esperado(
        archivo=archivo, estilo="Excel tipo referencia: rejilla Nº FACTURA/FECHA/CONTRATO/CERTIFICACIÓN, 2 líneas de "
        "certificación con UDS. antes del concepto, resumen en fila de cabecera, IBAN 'ES 00 1234 ...', emisor en pie",
        empresa_nif=em["nif"], tipo_documento="factura", situacion="preparatorio", numero=numero, fecha=fecha,
        emisor=em, emisor_dom="28760", destinatario=cl, destinatario_dom="28050", lineas=lineas, tipo_iva=21,
        base=base, cuota=cuota, total=total, retencion=None, total_a_pagar=None, referencias=refs, iban=iban,
        forma_pago_contiene="transferencia a la cuenta corriente", indicios=[],
        notas="Factura propia sin QR tributario subida por el emisor (documento a expedir con la aplicación). Razón "
              "social y NIF del emisor solo en el pie; arriba solo lema y domicilio. Destinatario sin etiqueta "
              "arriba a la derecha. Dos líneas de la certificación nº 9 (segunda con cantidad 0,25). Fila 'TOTAL' "
              "de la tabla repite la base. IBAN con espaciado irregular.")


# ----------------------------------------------------------------------------------------------------------------
# 2) Retención por garantía del 5 % (contratista -> promotora, edificio de oficinas, IVA 21 %)
# ----------------------------------------------------------------------------------------------------------------
def doc_02(dest: Path) -> dict:
    archivo = "obra_02_retencion_garantia.pdf"
    em, cl = EMPRESAS["tormaren"], EMPRESAS["ardanel"]
    numero, fecha = "FV26-00047", "2026-10-15"
    refs = {"obra": "EDIFICIO DE OFICINAS ARDANEL PLAZA (PARCELA T-3)", "contrato": "CO-ARD-2025-014",
            "certificacion": "4", "pedido": "PED/ARD/2611", "partida_presupuestaria": "OF-305-112"}
    lineas = [
        linea("42.50", "118.40", "Hormigón HA-25/B/20/IIa en losas de forjado, vertido con bomba, vibrado y curado",
              "Hormigón HA-25/B/20/IIa", cod="03.02", ud="m³"),
        linea("3860.00", "1.12", "Acero corrugado B 500 S en armaduras, cortado, doblado y colocado",
              "Acero corrugado B 500 S", cod="03.05", ud="kg"),
        linea("186.75", "24.65", "Fábrica de ladrillo cerámico perforado de 1/2 pie, recibida con mortero M-7,5",
              "Fábrica de ladrillo cerámico", cod="04.01", ud="m²"),
        linea("1", "1150.00", "Medios auxiliares y protecciones colectivas (parte proporcional del mes)",
              "Medios auxiliares y protecciones", cod="07.00", ud="PA"),
    ]
    base, cuota, total = totales(lineas, 21)
    garantia = q2(base * D("0.05"))
    liquido = q2(total - garantia)
    assert (base, cuota, total, garantia, liquido) == (D("15108.59"), D("3172.80"), D("18281.39"), D("755.43"),
                                                       D("17525.96"))
    iban_txt, iban = "ES1234 5678 9012 3456 7890 12", "ES1234567890123456789012"

    W, H = A4
    ROJO = HexColor("#7A1F1F")
    c = nuevo_lienzo(dest / archivo, A4, f"Certificación 4 - Factura {numero}", "Tormarén - Administración")
    txt(c, 40, H - 62, "TORMARÉN", "Times-Bold", 26, color=ROJO)
    c.setStrokeColor(ROJO)
    c.setLineWidth(1.2)
    c.line(40, H - 68, 172, H - 68)
    txt(c, 40, H - 80, "edificación · obra civil · rehabilitación", "Times-Italic", 9.5, color=GRIS_TXT)
    x = 330
    txt(c, x, H - 50, cl["nombre"], "Times-Bold", 10.5)
    txt(c, x, H - 63, "C/ Mirador del Cierzo Inventado, 3, bajo", "Times-Roman", 10.5)
    txt(c, x, H - 76, "50012 Zaragoza", "Times-Roman", 10.5)
    txt(c, x, H - 89, "N.I.F. " + nif_txt(cl["nif"], "plano"), "Times-Roman", 10.5)

    c.setFillColor(HexColor("#EFE7E7"))
    c.rect(40, H - 138, W - 80, 24, stroke=0, fill=1)
    txt(c, 48, H - 131, "FACTURA", "Times-Bold", 16, color=ROJO)
    txt(c, W - 48, H - 130, "Certificación ordinaria de obra", "Times-Italic", 10.5, "r", GRIS_TXT)

    y = celdas(c, 40, H - 146, [
        [("Núm. factura", numero, 257.5), ("Certificación nº", refs["certificacion"], 257.5)],
        [("Fecha de emisión", fecha_txt(fecha, "dd-mm-yy"), 257.5), ("Nº pedido", refs["pedido"], 257.5)],
        [("Contrato nº", refs["contrato"], 257.5), ("Partida presupuestaria", refs["partida_presupuestaria"], 257.5)],
        [("Obra", refs["obra"], 515)],
    ], alto=17, fuente="Times-Roman", fuente_b="Times-Bold", tam=10, sep=". - ", rejilla=HexColor("#B9A3A3"))

    y -= 18
    y = parrafo(c, 40, y, "Certificación ordinaria nº 4 de los trabajos ejecutados durante el mes de octubre de 2026 "
                "en la obra de referencia, según relación valorada conformada por la Dirección Facultativa. Conforme "
                "a la cláusula 11ª del contrato se practica una retención del 5 % de la base imponible en concepto de "
                "garantía, que será reintegrada a la recepción definitiva de las obras.", "Times-Roman", 10, 515,
                interl=12.5)

    y -= 10
    filas = [[l["cod"], es(l["cant"]), l["ud"], l["concepto"], es(l["precio"]), es(l["importe"])] for l in lineas]
    y = tabla(c, 40, y, [("Código", 42, "c"), ("Cant.", 52, "r"), ("Ud.", 28, "c"),
                         ("Descripción de la unidad de obra", 235, "l"), ("Precio", 70, "r"), ("Importe", 88, "r")],
              filas, fuente="Times-Roman", fuente_b="Times-Bold", tam=9.5, cab_relleno=ROJO, cab_color=white,
              col_ajuste=3, rejilla=HexColor("#B9A3A3"), pad=4)

    y -= 22
    y = tabla(c, 120, y, [("Base Imponible", 110, "c"), ("% I.V.A.", 70, "c"), ("Cuota I.V.A.", 100, "c"),
                          ("TOTAL FACTURA", 155, "c")],
              [[es(base, suf=" €"), "21 %", es(cuota, suf=" €"), es(total, suf=" €")]],
              fuente="Times-Roman", fuente_b="Times-Bold", tam=10, alto_min=20, pad=5, rejilla=HexColor("#B9A3A3"))
    y -= 8
    y = tabla(c, W - 40 - 275, y, [("Retención garantía 5 % s/ B.I.", 155, "c"), ("LÍQUIDO A PAGAR", 120, "c")],
              [[es(-garantia, suf=" €"), es(liquido, suf=" €")]], fuente="Times-Roman", fuente_b="Times-Bold",
              tam=10, alto_min=20, pad=5, rejilla=HexColor("#B9A3A3"), fuente_filas={0: "Times-Bold"})

    y -= 26
    txt(c, 40, y, "Forma de pago. - Transferencia bancaria a 90 días fecha factura (días de pago: 10 y 25).",
        "Times-Roman", 10)
    txt(c, 40, y - 13, f"Cuenta de abono. - IBAN {iban_txt}", "Times-Roman", 10)

    c.setStrokeColor(ROJO)
    c.setLineWidth(0.6)
    c.line(40, 58, W - 40, 58)
    txt(c, W / 2, 47, f"{em['nombre']} · N.I.F. {nif_txt(em['nif'], 'guion')} · Pol. Ind. El Saucedal Ficticio, "
                      "nave 12 · 50197 Zaragoza · Tel. 976 000 000", "Times-Roman", 8, "c", GRIS_TXT)
    txt(c, W / 2, 37, "Inscrita en el Registro Mercantil de Zaragoza, Tomo 4.381, Folio 152, Hoja Z-61427, "
                      "Inscripción 1ª", "Times-Roman", 8, "c", GRIS_TXT)
    aviso_sintetico(c, 40)
    c.showPage()
    c.save()

    return esperado(
        archivo=archivo, estilo="Contratista en Times: rejilla 'etiqueta. - valor', tabla Código|Cant.|Ud.|Descripción, "
        "resumen en fila de cabecera más segunda fila Retención garantía 5 % | LÍQUIDO A PAGAR, fecha dd-mm-yy",
        empresa_nif=em["nif"], tipo_documento="factura", situacion="preparatorio", numero=numero, fecha=fecha,
        emisor=em, emisor_dom="50197", destinatario=cl, destinatario_dom="50012", lineas=lineas, tipo_iva=21,
        base=base, cuota=cuota, total=total, retencion=None, total_a_pagar=liquido, referencias=refs, iban=iban,
        forma_pago_contiene="Transferencia bancaria a 90 días",
        indicios=[],
        notas="La 'retención por garantía' del 5 % (755,43 € sobre la base) es una retención COMERCIAL pactada en "
              "contrato, NO es IRPF: retencion debe ser null y total_factura sigue siendo 18281.39; el líquido a "
              "pagar (17525.96) = total − garantía. Ejecución de obra directa al promotor (dueño de obra), sin "
              "inversión del sujeto pasivo. Fecha en formato dd-mm-yy. Cantidades con miles (3.860,00) y "
              "unidades m³/m²/kg/PA. El emisor solo figura con logotipo arriba; razón social, NIF y domicilio en "
              "el pie.")


# ----------------------------------------------------------------------------------------------------------------
# 3) IVA reducido 10 % (vivienda): contratista -> promotora; la subida la hace la promotora (recibida)
# ----------------------------------------------------------------------------------------------------------------
def doc_03(dest: Path) -> dict:
    archivo = "obra_03_iva10_vivienda_recibida.pdf"
    em, cl = EMPRESAS["ribazul"], EMPRESAS["valdorniz"]
    numero, fecha = "RB-26-118", "2026-10-31"
    refs = {"obra": "RESIDENCIAL OLIVAR DE VALDORNIZ - 48 VIVIENDAS, GARAJES Y TRASTEROS",
            "contrato": "CEO-VLD-2024/02", "certificacion": "14", "proyecto": "PBE-2024-031"}
    lineas = [
        linea("1240.00", "46.30", "Cubierta plana invertida transitable: impermeabilización bicapa y aislamiento "
              "XPS 80 mm", "Cubierta plana invertida", ud="m2"),
        linea("2315.50", "31.85", "Tabiquería de placa de yeso laminado 15+46+15 con aislamiento de lana mineral",
              "Tabiquería de placa de yeso", ud="m2"),
        linea("96.00", "412.00", "Ventana de PVC oscilobatiente 120x140 cm con doble acristalamiento bajo emisivo",
              "Ventana de PVC oscilobatiente", ud="ud"),
        linea("1", "2180.00", "Seguridad y salud: parte proporcional de la certificación nº 14", "Seguridad y salud",
              ud="PA"),
    ]
    base, cuota, total = totales(lineas, 10)
    assert (base, cuota, total) == (D("172892.68"), D("17289.27"), D("190181.95"))
    iban_txt, iban = "ES 00 9876 5432 10 9876543210", "ES0098765432109876543210"

    W, H = A4
    c = nuevo_lienzo(dest / archivo, A4, f"FRA {numero}", "RIBAZUL ERP")
    txt(c, 40, H - 60, "RIBAZUL", "Courier-Bold", 22)
    txt(c, 40, H - 74, "OBRAS Y CONTRATAS", "Courier", 9.5)
    x = 336
    c.setStrokeColor(GRIS_XL)
    c.setLineWidth(0.6)
    c.roundRect(x - 8, H - 112, 227, 74, 4, stroke=1, fill=0)
    txt(c, x, H - 54, cl["nombre"], "Courier-Bold", 9)
    txt(c, x, H - 67, "Plaza del Olivo Ficticio, 9, 3º", "Courier", 9)
    txt(c, x, H - 80, "41011 SEVILLA", "Courier", 9)
    txt(c, x, H - 101, "C.I.F.: " + nif_txt(cl["nif"], "plano"), "Courier", 9)

    txt(c, 40, H - 142, "FACTURA", "Courier-Bold", 16)
    y = celdas(c, 40, H - 152, [
        [("Número:", None, 110), (None, numero, 405)],
        [("Fecha:", None, 110), (None, fecha_txt(fecha, "largo"), 405)],
        [("Obra:", None, 110), (None, refs["obra"], 405)],
        [("Contrato:", None, 110), (None, refs["contrato"], 405)],
        [("Certificación nº:", None, 110), (None, refs["certificacion"], 405)],
        [("Proyecto:", None, 110), (None, refs["proyecto"], 405)],
    ], alto=15, fuente="Courier", fuente_b="Courier-Bold", tam=8.5, relleno_etq=HexColor("#EDEDED"))

    y -= 16
    y = parrafo(c, 40, y, "Certificación nº 14 correspondiente a los trabajos ejecutados durante el mes de octubre "
                "de 2026 en la promoción de 48 viviendas de la obra de referencia, según relación valorada aprobada "
                "por la Dirección Facultativa (importes a origen deducidas las certificaciones anteriores).",
                "Courier", 8.5, 515, interl=11)

    y -= 8
    filas = [[es(l["cant"], miles=False), l["ud"], l["concepto"], es(l["precio"], miles=False),
              es(l["importe"], miles=False)] for l in lineas]
    y = tabla(c, 40, y, [("Cant.", 60, "r"), ("Ud.", 28, "c"), ("Concepto", 245, "l"), ("Precio", 75, "r"),
                         ("Importe", 107, "r")], filas, fuente="Courier", fuente_b="Courier-Bold", tam=8.5,
              cab_relleno=HexColor("#D9D9D9"), col_ajuste=2, interl=10.5, pad=4)

    y -= 20
    y = tabla(c, 130, y, [("BASE IMPONIBLE", 110, "c"), ("TIPO", 60, "c"), ("CUOTA I.V.A.", 100, "c"),
                          ("TOTAL", 115, "c")],
              [[es(base, miles=False), "10,00%", es(cuota, miles=False), es(total, miles=False, suf=" EUR")]],
              fuente="Courier", fuente_b="Courier-Bold", tam=9, alto_min=19, pad=5, cab_relleno=HexColor("#D9D9D9"))

    y -= 18
    y = parrafo(c, 40, y, "IVA aplicado al tipo reducido del 10 % (art. 91.Uno.3.1º Ley 37/1992): ejecución de obra "
                "de construcción de edificaciones destinadas principalmente a viviendas, contratada directamente "
                "entre el promotor y el contratista.", "Courier", 7.5, 515, interl=9.5)
    y -= 10
    txt(c, 40, y, "Forma de pago: Transferencia a 60 días f.f.", "Courier", 8.5)
    txt(c, 40, y - 12, f"IBAN: {iban_txt}", "Courier", 8.5)

    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    c.line(40, 66, W - 40, 66)
    txt(c, 40, 56, f"{em['nombre']} - C.I.F. {nif_txt(em['nif'], 'guion')} - Ctra. de la Vega Inventada, km 3,5",
        "Courier", 7)
    txt(c, 40, 47, "41500 Alcalá de Guadaíra (Sevilla) - Tel. 954 000 000 - www.ribazul.example", "Courier", 7)
    txt(c, 40, 38, "Reg. Merc. de Sevilla, Tomo 6.112, Folio 45, Hoja SE-98765, Inscripción 3ª", "Courier", 7)
    aviso_sintetico(c, 40)
    c.showPage()
    c.save()

    return esperado(
        archivo=archivo, estilo="ERP monoespaciado (Courier): rejilla etiqueta|valor con ':', fecha 'd de mes de "
        "yyyy', importes sin separador de miles, IVA 10 % vivienda, resumen BASE|TIPO|CUOTA|TOTAL con valores debajo",
        empresa_nif=cl["nif"], tipo_documento="factura", situacion="recibida", numero=numero, fecha=fecha,
        emisor=em, emisor_dom="41500", destinatario=cl, destinatario_dom="41011", lineas=lineas, tipo_iva=10,
        base=base, cuota=cuota, total=total, retencion=None, total_a_pagar=None, referencias=refs, iban=iban,
        forma_pago_contiene="Transferencia a 60 días",
        indicios=[],
        notas="La sube la PROMOTORA destinataria (empresa_nif = destinatario): factura recibida de proveedor. IVA "
              "reducido 10 % por ejecución de obra de vivienda contratada directamente con el promotor (no es "
              "exención ni ISP; la cita al art. 91 no debe disparar indicios). Importes sin separador de miles "
              "(172892,68) y total con sufijo EUR. Datos del emisor (NIF, domicilio, registro) solo en el pie.")


# ----------------------------------------------------------------------------------------------------------------
# 4) FACTURA PROFORMA en A4 apaisado con líneas de división de Excel (coordinador de seguridad y salud)
# ----------------------------------------------------------------------------------------------------------------
def doc_04(dest: Path) -> dict:
    archivo = "obra_04_proforma_apaisada.pdf"
    em, cl = EMPRESAS["quelvar"], EMPRESAS["lindamar"]
    numero, fecha = "PF-031/2026", "2026-11-04"
    refs = {"obra": "RESIDENCIAL LINDAMAR MARE - 64 VIVIENDAS, LOCALES Y GARAJE", "contrato": "CSS-LND-2025-07",
            "certificacion": "11", "pedido": "LND/PO/26-0533", "expediente": "EXP-LND-0458"}
    lineas = [
        linea("1", "1150.00", "Coordinación de seguridad y salud en fase de ejecución de obra – noviembre 2026",
              "Coordinación de seguridad y salud en fase de ejecución"),
        linea("3", "95.00", "Visitas extraordinarias de inspección solicitadas por la propiedad",
              "Visitas extraordinarias de inspección"),
        linea("1", "380.00", "Revisión e informe del anexo nº 2 al Plan de Seguridad y Salud del contratista",
              "Revisión e informe del anexo"),
    ]
    base, cuota, total = totales(lineas, 21)
    assert (base, cuota, total) == (D("1815.00"), D("381.15"), D("2196.15"))
    iban_txt, iban = "ES0022223333 4444 5555 6666", "ES0022223333444455556666"

    tam_pag = landscape(A4)
    W, H = tam_pag
    AZ = HexColor("#1D4E89")
    c = nuevo_lienzo(dest / archivo, tam_pag, f"Proforma {numero}", "QUELVAR")
    lineas_division_excel(c, 36, 60, W - 36, H - 36)
    c.setFillColor(AZ)
    c.rect(36, H - 84, 236, 44, stroke=0, fill=1)
    txt(c, 46, H - 60, "QUELVAR INGENIEROS", "Helvetica-Bold", 14, color=white)
    txt(c, 46, H - 74, "Coordinación de Seguridad y Salud · Project Management", "Helvetica", 7.5, color=white)
    tapar(c, 320, H - 44, 196, 34)
    txt(c, 418, H - 66, "FACTURA PROFORMA", "Helvetica-Bold", 18, "c", AZ)
    x = 585
    tapar(c, x - 4, H - 38, W - 36 - (x - 4), 54)
    txt(c, x, H - 50, cl["nombre"], "Helvetica-Bold", 9.5)
    txt(c, x, H - 62, "Ronda de los Tilos Inventados, 22, entlo.", "Helvetica", 9.5)
    txt(c, x, H - 74, "46020 Valencia", "Helvetica", 9.5)
    txt(c, x, H - 86, "CIF " + nif_txt(cl["nif"], "plano"), "Helvetica", 9.5)

    AZ_CL = HexColor("#DCE6F1")
    y = celdas(c, 36, H - 108, [
        [("Proforma nº", None, 90), (None, numero, 180), ("Fecha", None, 90), (None, fecha_txt(fecha, "dd.mm.yyyy"), 180)],
        [("Contrato", None, 90), (None, refs["contrato"], 180), ("Certificación", None, 90),
         (None, refs["certificacion"], 180)],
        [("Pedido nº", None, 90), (None, refs["pedido"], 180), ("Expediente", None, 90), (None, refs["expediente"], 180)],
        [("Obra", None, 90), (None, refs["obra"], 450)],
    ], alto=16, fuente="Helvetica", fuente_b="Helvetica-Bold", tam=8.5, relleno_etq=AZ_CL, rejilla=HexColor("#8EA9C8"),
               fondo=white)

    y -= 16
    tapar(c, 36, y + 11, W - 72, 26)
    y = parrafo(c, 36, y, "Honorarios por la coordinación de seguridad y salud durante la ejecución de la obra arriba "
                "referenciada, correspondientes al mes de noviembre de 2026, de acuerdo con la propuesta de honorarios "
                "aceptada y el contrato de referencia.", "Helvetica", 8.5, W - 72, interl=11)

    y -= 6
    filas = [[es(l["cant"]), l["concepto"], es(l["precio"], suf=" €"), "21", es(l["importe"], suf=" €")]
             for l in lineas]
    y = tabla(c, 36, y, [("Cant.", 55, "r"), ("Concepto", 460, "l"), ("Precio unit.", 100, "r"), ("IVA %", 55, "c"),
                         ("Importe", 100, "r")], filas, fuente="Helvetica", fuente_b="Helvetica-Bold", tam=8.5,
              cab_relleno=AZ, cab_color=white, col_ajuste=1, rejilla=HexColor("#8EA9C8"), pad=4, alto_min=16,
              fondo=white)

    y -= 16
    y_res = y
    tabla(c, W - 36 - 440, y, [("Base Imponible", 120, "c"), ("Tipo IVA", 80, "c"), ("Cuota IVA", 110, "c"),
                               ("IMPORTE TOTAL", 130, "c")],
          [[es(base, suf=" €"), "21 %", es(cuota, suf=" €"), es(total, suf=" €")]], fuente="Helvetica",
          fuente_b="Helvetica-Bold", tam=9, alto_min=19, pad=5, cab_relleno=AZ_CL, rejilla=HexColor("#8EA9C8"),
          fuente_filas={0: "Helvetica"}, fondo=white)
    # Condiciones de pago a la izquierda del resumen, a la misma altura (habitual en plantillas Excel)
    tapar(c, 36, y_res, 300, 42)
    tapar(c, 36, y_res - 52, 600, 15)
    txt(c, 36, y_res - 10, "Forma de pago", "Helvetica-Bold", 8.5)
    txt(c, 36, y_res - 22, "Transferencia a 30 días f.f. a la cuenta", "Helvetica", 8.5)
    txt(c, 36, y_res - 34, f"IBAN {iban_txt}", "Helvetica", 8.5)
    txt(c, 36, y_res - 62, "Documento PROFORMA sin validez fiscal: no sustituye a la factura. La factura definitiva "
                           "se expedirá una vez recibida la conformidad de la propiedad.", "Helvetica-Oblique", 8)

    c.setStrokeColor(AZ)
    c.setLineWidth(0.8)
    c.line(36, 50, W - 36, 50)
    txt(c, W / 2, 40, f"{em['nombre']} · CIF {nif_txt(em['nif'], 'guion')} · Gran Vía de las Germanías Ficticia, 18, "
                      "46006 Valencia · Tel. 960 000 000 · info@quelvar.example", "Helvetica", 7.5, "c", GRIS_TXT)
    txt(c, W / 2, 30, "Sociedad profesional inscrita en el Registro Mercantil de Valencia, Tomo 10.734, Folio 61, "
                      "Hoja V-189203, Inscripción 1ª", "Helvetica", 7.5, "c", GRIS_TXT)
    aviso_sintetico(c, 36)
    c.showPage()
    c.save()

    return esperado(
        archivo=archivo, estilo="A4 apaisado con líneas de división de Excel: título FACTURA PROFORMA, etiquetas en "
        "celdas sombreadas sin separador, columna IVA %, resumen a la derecha y forma de pago a su izquierda en las "
        "mismas filas, fecha dd.mm.yyyy",
        empresa_nif=em["nif"], tipo_documento="proforma", situacion="preparatorio", numero=numero, fecha=fecha,
        emisor=em, emisor_dom="46006", destinatario=cl, destinatario_dom="46020", lineas=lineas, tipo_iva=21,
        base=base, cuota=cuota, total=total, retencion=None, total_a_pagar=None, referencias=refs, iban=iban,
        forma_pago_contiene="Transferencia a 30 días",
        indicios=[],
        notas="Proforma (documento preparatorio) del propio emisor. Página A4 apaisada. El número se rotula "
              "'Proforma nº' en una celda y el valor en la contigua. La certificación se rotula solo "
              "'Certificación' (sin nº). El bloque de forma de pago comparte filas con el resumen (texto "
              "intercalado en la extracción). Sociedad profesional (S.L.P.): sin retención de IRPF.")


# ----------------------------------------------------------------------------------------------------------------
# 5) FACTURA RECTIFICATIVA por diferencias (importes negativos) que remite a una factura anterior
# ----------------------------------------------------------------------------------------------------------------
def doc_05(dest: Path) -> dict:
    archivo = "obra_05_rectificativa.pdf"
    em, cl = EMPRESAS["castredo"], EMPRESAS["alcazen"]
    numero, fecha = "R-004/26", "2026-11-06"
    rectificada, fecha_rect = "0187/26", "2026-09-30"
    refs = {"obra": "EDIFICIO ALCAZÉN CENTRO (32 VIVIENDAS Y LOCAL COMERCIAL)", "certificacion": "6",
            "pedido": "ALC-PED-26/0091", "partida_presupuestaria": "PP-0402-117"}
    lineas = [
        linea("1", "-640.00", "Abono del exceso certificado en la dirección de ejecución material "
              "(certificación nº 6, septiembre 2026)", "Abono del exceso certificado"),
        linea("-6", "45.00", "Horas de coordinación de seguridad y salud facturadas en exceso",
              "facturadas en exceso"),
    ]
    base, cuota, total = totales(lineas, 21)
    assert (base, cuota, total) == (D("-910.00"), D("-191.10"), D("-1101.10"))
    iban_txt, iban = "ES00 9999 0001 23 4567890123", "ES0099990001234567890123"

    W, H = A4
    VERDE = HexColor("#3B5B2A")
    c = nuevo_lienzo(dest / archivo, A4, f"Rectificativa {numero}", "Castredo")
    txt(c, 40, H - 58, "CASTREDO", "Helvetica-Bold", 20, color=VERDE)
    txt(c, 40, H - 70, "arquitectura técnica · seguridad y salud", "Times-Italic", 9.5, color=GRIS_TXT)
    txt(c, 40, H - 86, "Avda. del Malecón Inventado, 40, 2º D", "Times-Roman", 9)
    txt(c, 40, H - 97, "30004 Murcia", "Times-Roman", 9)
    x = W - 40
    txt(c, x, H - 52, cl["nombre"], "Times-Bold", 10, "r")
    txt(c, x, H - 64, "Calle Cronista Inventado, 15, 5ª planta", "Times-Roman", 10, "r")
    txt(c, x, H - 76, "30008 Murcia", "Times-Roman", 10, "r")
    txt(c, x, H - 88, "N.I.F.: " + nif_txt(cl["nif"], "espacio"), "Times-Roman", 10, "r")

    txt(c, 40, H - 132, "FACTURA RECTIFICATIVA", "Helvetica-Bold", 15, color=VERDE)
    y = celdas(c, 40, H - 142, [
        [("Nº factura", numero, 257.5), ("Fecha", fecha_txt(fecha, "dd/mm/yyyy"), 257.5)],
        [("Factura rectificada", f"{rectificada} de {fecha_txt(fecha_rect, 'dd-mm-yyyy')}", 257.5),
         ("Certificación nº", refs["certificacion"], 257.5)],
        [("Nº pedido", refs["pedido"], 257.5), ("Partida presupuestaria", refs["partida_presupuestaria"], 257.5)],
        [("Obra", refs["obra"], 515)],
    ], alto=17, fuente="Times-Roman", fuente_b="Times-Bold", tam=9.5, sep=" - ", rejilla=HexColor("#9DB08F"))

    y -= 18
    y = parrafo(c, 40, y, f"Se expide la presente factura rectificativa por diferencias, conforme al artículo 80 de la "
                f"Ley 37/1992 del IVA y al artículo 15 del Reglamento de facturación, para corregir la factura nº "
                f"{rectificada} de fecha {fecha_txt(fecha_rect, 'dd-mm-yyyy')} correspondiente a la certificación nº 6. "
                "Motivo: error en la medición certificada; se abona el exceso facturado.", "Times-Roman", 9.5, 515,
                interl=12)

    y -= 10
    filas = [[es(l["cant"]), l["concepto"], es(l["precio"], suf=" €"), es(l["importe"], suf=" €")] for l in lineas]
    y = tabla(c, 40, y, [("Cant.", 50, "r"), ("Concepto", 300, "l"), ("Precio", 80, "r"), ("Importe", 85, "r")],
              filas, fuente="Helvetica", fuente_b="Helvetica-Bold", tam=8.5, cab_relleno=HexColor("#E3EBDD"),
              col_ajuste=1, rejilla=HexColor("#9DB08F"), pad=4, alto_min=18)

    y -= 22
    y = tabla(c, 160, y, [("Base Imponible", 110, "c"), ("IVA", 60, "c"), ("Total IVA", 100, "c"),
                          ("TOTAL FACTURA", 125, "c")],
              [[es(base, suf=" €"), "21%", es(cuota, suf=" €"), es(total, suf=" €")]], fuente="Helvetica",
              fuente_b="Helvetica-Bold", tam=9, alto_min=19, pad=5, rejilla=HexColor("#9DB08F"))
    y -= 18
    txt(c, W - 40, y, f"Importe total de la rectificación: {es(total, suf=' €')} (a favor del cliente).",
        "Times-Italic", 9.5, "r")

    y -= 26
    txt(c, 40, y, "Forma de pago - Compensación en la próxima certificación o, a petición del cliente, reintegro por "
                  "transferencia.", "Times-Roman", 9.5)
    txt(c, 40, y - 12, f"Cuenta del emisor - IBAN {iban_txt}", "Times-Roman", 9.5)

    c.setStrokeColor(VERDE)
    c.setLineWidth(0.6)
    c.line(40, 56, W - 40, 56)
    txt(c, W / 2, 45, f"{em['nombre']} | C.I.F. {nif_txt(em['nif'], 'guion')} | Avda. del Malecón Inventado, 40, "
                      "2º D - 30004 Murcia | Tel. 968 000 000", "Helvetica", 7, "c", GRIS_TXT)
    txt(c, W / 2, 35, "Inscrita en el Registro Mercantil de Murcia, Tomo 3.287, Folio 190, Hoja MU-90412, "
                      "Inscripción 1ª", "Helvetica", 7, "c", GRIS_TXT)
    aviso_sintetico(c, 40)
    c.showPage()
    c.save()

    return esperado(
        archivo=archivo, estilo="Rectificativa por diferencias: rejilla 'etiqueta - valor' con factura rectificada, "
        "importes negativos (cantidad negativa en una línea), resumen en fila de cabecera y frase final con el total",
        empresa_nif=em["nif"], tipo_documento="rectificativa", situacion="preparatorio", numero=numero, fecha=fecha,
        emisor=em, emisor_dom="30004", destinatario=cl, destinatario_dom="30008", lineas=lineas, tipo_iva=21,
        base=base, cuota=cuota, total=total, retencion=None, total_a_pagar=None, referencias=refs, iban=iban,
        forma_pago_contiene="Compensación en la próxima certificación",
        indicios=["rectificativa"],
        notas=f"Rectifica la factura {rectificada} de {fecha_txt(fecha_rect, 'dd-mm-yyyy')} (no hay clave de "
              "referencias para la factura rectificada; su fecha NO es la fecha del documento). Rectificación por "
              "diferencias con importes negativos: línea 1 con precio negativo y línea 2 con cantidad negativa "
              "(-6,00 × 45,00). Emisor y destinatario con domicilio en Murcia (30004 / 30008). El IBAN es del "
              "emisor aunque el importe es a favor del cliente.")


DOCUMENTOS = [doc_01, doc_02, doc_03, doc_04, doc_05]


# ----------------------------------------------------------------------------------------------------------------
# Comprobación con pdftotext -layout
# ----------------------------------------------------------------------------------------------------------------
def _formas_fecha(iso: str) -> list[str]:
    return [fecha_txt(iso, e) for e in ("dd/mm/yyyy", "dd-mm-yy", "dd-mm-yyyy", "largo", "dd.mm.yyyy")]


def comprobar(pdf: Path, esp: dict) -> list[str]:
    """Comprueba que el texto extraíble contiene los valores esperados. Devuelve la lista de fallos."""
    texto = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True,
                           check=True).stdout
    plano = re.sub(r"\s+", " ", texto)
    compacto = re.sub(r"[\s.\-:]", "", texto).upper()
    fallos = []

    def debe(cond, que):
        if not cond:
            fallos.append(que)

    def importe(v, que):
        if v is None:
            return
        d = D(v)
        debe(any(f in plano for f in (es(d), es(d, miles=False))), f"{que}={v}")

    tit = {"factura": "FACTURA", "proforma": "PROFORMA", "rectificativa": "RECTIFICATIVA"}[esp["tipo_documento"]]
    debe(tit in plano.upper(), f"tipo_documento={tit}")
    debe(esp["numero"] in plano, f"numero={esp['numero']}")
    debe(any(f in plano for f in _formas_fecha(esp["fecha"])), f"fecha={esp['fecha']}")
    for parte in ("emisor", "destinatario"):
        p = esp[parte]
        debe(p["nombre"] in plano, f"{parte}.nombre")
        debe(p["nif"] in compacto, f"{parte}.nif={p['nif']}")
        debe(p["domicilio_contiene"] in plano, f"{parte}.domicilio_contiene")
    for i, l in enumerate(esp["lineas"]):
        debe(l["concepto_contiene"] in plano, f"lineas[{i}].concepto_contiene")
        importe(l["cantidad"], f"lineas[{i}].cantidad")
        importe(l["precio"], f"lineas[{i}].precio")
        importe(l["importe"], f"lineas[{i}].importe")
    for k in ("base_imponible", "cuota_iva", "total_factura", "total_a_pagar"):
        importe(esp[k], k)
    for k, v in esp["referencias"].items():
        debe(v in plano, f"referencias.{k}={v}")
    if esp["iban"]:
        debe(esp["iban"] in compacto, f"iban={esp['iban']}")
    if esp["forma_pago_contiene"]:
        debe(esp["forma_pago_contiene"] in plano, "forma_pago_contiene")
    # Coherencia aritmética del propio JSON
    suma = sum((D(l["importe"]) for l in esp["lineas"]), D("0"))
    debe(q2(suma) == D(esp["base_imponible"]), "suma de líneas = base")
    for l in esp["lineas"]:
        debe(q2(D(l["cantidad"]) * D(l["precio"])) == D(l["importe"]), f"cantidad×precio ({l['concepto_contiene']})")
    for dsg in esp["desglose"]:
        debe(q2(D(dsg["base"]) * D(dsg["tipo_iva"]) / 100) == D(dsg["cuota"]), "cuota = base × tipo")
    debe(q2(D(esp["base_imponible"]) + D(esp["cuota_iva"])) == D(esp["total_factura"]), "total = base + cuota")
    return fallos


def main() -> int:
    salida = Path(sys.argv[1]) if len(sys.argv) > 1 else SALIDA_DEFECTO
    salida.mkdir(parents=True, exist_ok=True)
    resumen = []
    hay_pdftotext = shutil.which("pdftotext") is not None
    for gen in DOCUMENTOS:
        esp = gen(salida)
        (salida / esp["archivo"]).with_suffix(".json").write_text(
            json.dumps(esp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fallos = comprobar(salida / esp["archivo"], esp) if hay_pdftotext else ["pdftotext no disponible"]
        resumen.append({"archivo": esp["archivo"], "estilo": esp["estilo"], "fallos": fallos})
    print(json.dumps({"salida": str(salida), "documentos": resumen}, ensure_ascii=False, indent=2))
    return 1 if any(d["fallos"] for d in resumen) else 0


if __name__ == "__main__":
    sys.exit(main())
