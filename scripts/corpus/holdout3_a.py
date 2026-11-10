"""Tercer corpus SINTÉTICO «held-out» (ciego), familia «a»: cuatro facturas españolas con maquetaciones nuevas.

  1. Factura de una S.L. a una ADMINISTRACIÓN PÚBLICA (ayuntamiento): bloque de códigos DIR3 (oficina contable,
     órgano gestor, unidad tramitadora), expediente, contrato y periodo facturado. A4, Liberation Sans.
  2. Factura de una FUNDACIÓN (CIF G) a una empresa: sin tabla, el servicio se describe en párrafos y hay un único
     importe; IVA 21 %, fecha en letra. A5, Liberation Serif. Recibida por la empresa.
  3. Factura de una COMUNIDAD DE BIENES (C.B., CIF E) a una comunidad de propietarios (CIF H): dos páginas, las
     líneas llenan la página 1 y los totales están en la página 2, sin «Suma y sigue» ni «Suma anterior».
     US Letter, JetBrains Mono (aspecto de impresora de líneas).
  4. Factura de un mayorista con DESCUENTO POR PRONTO PAGO sobre toda la factura («Dto. pronto pago 2 %» entre la
     suma de las líneas y la base imponible), IVA 10 % y 21 % con cuadro de desglose por tipo (importe, dto. p.p.,
     base, cuota). A4, DejaVu Sans. Recibida por el restaurante.

TODOS LOS DATOS SON FICTICIOS: razones sociales, personas, domicilios, teléfonos (9x0 000 000 y similares), correos
(dominio .example), códigos DIR3, expedientes, contratos e IBAN (ES00 + dígitos en serie). Los NIF/CIF tienen carácter
de control válido (app.nif.completar_cif / letra del DNI) pero son de rangos evidentemente ficticios: CIF con código
de provincia 00 (no asignado a ninguna provincia) y DNI de cifras repetidas; las calles llevan «Inventado»,
«Ficticio» o «Imaginario» en el nombre (solo la población y el código postal son reales). Se comprueba al generar que
lo son y que no aparecen en los demás corpus del repositorio (JSON y texto de los PDF de tests/corpus*). Estos
identificadores y calles sustituyeron a otros verosímiles después de la medición ciega; la maqueta, los importes y el
resto de los textos no cambiaron.

Conjunto diseñado SIN consultar el extractor (app/extraccion/) ni los datos privados. No lo ajustes al extractor.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/holdout3_a.py     # genera tests/corpus_holdout3/h3a_NN_*.pdf/.json y verifica

Verificación (sale con código 1 si algo falla): aritmética exacta de cada JSON (líneas por tipo = importe bruto del
tipo; sin descuento global, bruto = base; dto. p.p. por tipo redondeado half-up; cuota = base x tipo half-up; total =
bases + cuotas), NIF válidos y no reutilizados, y presencia en `pdftotext -layout` del número, la fecha, los NIF tal
como se imprimen, nombres, códigos postales, conceptos, importes, referencias, IBAN y forma de pago; además, que no
haya en el texto particularidades fiscales no esperadas ni NIF válidos no previstos.

Determinista: canvas de reportlab con invariant=1 y sin datos aleatorios.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from reportlab.lib.colors import HexColor, black, white  # noqa: E402
from reportlab.lib.pagesizes import A4, A5, letter  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import buscar_nifs, completar_cif, es_valido, normalizar  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus_holdout3"
PREFIJO = "h3a_"
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
    "JBMono": ("jetbrains-mono/JetBrainsMono-Regular.ttf", "Courier"),
    "JBMono-Bold": ("jetbrains-mono/JetBrainsMono-Bold.ttf", "Courier-Bold"),
    "DejaVu": ("dejavu/DejaVuSans.ttf", "Helvetica"),
    "DejaVu-Bold": ("dejavu/DejaVuSans-Bold.ttf", "Helvetica-Bold"),
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
    """Formato español: 1.234,56."""
    x = D(x).quantize(D(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    ent, _, fr = f"{abs(x):.{nd}f}".partition(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", miles)
    return ("-" if x < 0 else "") + ent + ("," + fr if nd else "")


def L(concepto, contiene, cant=None, precio=None, iva="21", ud=None, cod=None, importe=None, cant_txt=None) -> dict:
    return {"concepto": concepto, "contiene": contiene, "cantidad": None if cant is None else D(cant),
            "precio": None if precio is None else D(precio), "iva": D(iva), "ud": ud, "cod": cod,
            "importe": None if importe is None else D(importe), "cant_txt": cant_txt}


def calcular(lineas: list[dict], dto_pp=None) -> dict:
    """Importe de línea = cantidad x precio (half-up). Por tipo: bruto, dto. p.p. (half-up), base y cuota."""
    for ln in lineas:
        if ln["importe"] is None:
            ln["importe"] = q2(ln["cantidad"] * ln["precio"])
    pct = D(dto_pp) if dto_pp is not None else None
    desglose = []
    for tipo in sorted({ln["iva"] for ln in lineas}):
        bruto = sum((ln["importe"] for ln in lineas if ln["iva"] == tipo), D("0"))
        dto = q2(bruto * pct / 100) if pct is not None else D("0.00")
        base = bruto - dto
        desglose.append({"tipo": tipo, "bruto": bruto, "dto": dto, "base": base, "cuota": q2(base * tipo / 100)})
    bruto = sum((g["bruto"] for g in desglose), D("0"))
    dto = sum((g["dto"] for g in desglose), D("0"))
    base = sum((g["base"] for g in desglose), D("0"))
    cuota = sum((g["cuota"] for g in desglose), D("0"))
    return {"desglose": desglose, "bruto": bruto, "pct_dto": pct, "dto": dto if pct is not None else None,
            "base": base, "cuota": cuota, "total": base + cuota, "pct_ret": None, "ret": None, "pagar": None}


def partir(texto: str, fuente: str, tam: float, ancho: float) -> list[str]:
    return simpleSplit(texto, fuente, tam, ancho)


# ----------------------------------------------------------------------------------------------
# dibujo
# ----------------------------------------------------------------------------------------------
def ancho(s: str, fuente: str, tam: float, cs: float = 0.0) -> float:
    return pdfmetrics.stringWidth(s, fuente, tam) + cs * max(0, len(s) - 1)


def tx(c, x, y, s, fuente, tam=9.0, color=black, alin="l", cs=0.0):
    c.setFillColor(color)
    if cs:
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


def guias(c, x1, x2, y, fuente, tam, color):
    """Puntos guía entre una etiqueta y su importe (deja hueco a ambos lados)."""
    c.setFont(fuente, tam)
    c.setFillColor(color)
    paso = c.stringWidth(". ", fuente, tam)
    n = max(0, int((x2 - x1) / paso))
    c.drawString(x2 - n * paso, y, ". " * n)


def nuevo_canvas(ruta: Path, tam_pagina, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=tam_pagina, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    c.setCreator("scripts/corpus/holdout3_a.py")
    return c


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido; CIF de provincia 00, DNI de cifras repetidas): main comprueba que lo son y que no
# aparecen en los demás corpus
# ----------------------------------------------------------------------------------------------
CIF_CIERZO = completar_cif("B", "0061837")         # emisor doc 1 (mantenimiento de parques, Zaragoza)
CIF_TORREBERMEJA = completar_cif("P", "0098400")   # destinatario doc 1 (ayuntamiento ficticio)
CIF_LLANO = completar_cif("G", "0051904")          # emisora doc 2 (fundación, Alcantarilla)
CIF_SANGONERA = completar_cif("B", "0062418")      # destinatario doc 2 = empresa usuaria (conservera)
CIF_LASHERAS = completar_cif("E", "0072815")       # emisor doc 3 (comunidad de bienes, Alcañiz)
CIF_ACACIAS = completar_cif("H", "0069027")        # destinatario doc 3 (comunidad de propietarios)
DNI_ANDRES = dni(11111111)                         # comunero doc 3 (distractor)
DNI_RUBEN = dni(22222222)                          # comunero doc 3 (distractor)
CIF_ALBORAN = completar_cif("A", "0058137")        # emisor doc 4 (mayorista hostelero, Almería)
CIF_CABONEGRO = completar_cif("B", "0071692")      # destinatario doc 4 = empresa usuaria (restaurante)


def nif_ficticio(n: str) -> bool:
    """NIF que no puede ser de nadie: CIF con provincia 00 (no asignada) o DNI/NIE de cifras repetidas."""
    if re.fullmatch(r"[A-HJNP-SUVW]00\d{5}[0-9A-J]", n):
        return True
    return re.fullmatch(r"(\d)\1{7}[A-Z]|[XYZ](\d)\2{6}[A-Z]", n) is not None


def con_guion(nif: str) -> str:
    return f"{nif[0]}-{nif[1:]}"


# ==============================================================================================
# 1. S.L. -> ayuntamiento: bloque DIR3, expediente, contrato y periodo (A4, Liberation Sans)
# ==============================================================================================
VERDE = HexColor("#2E6B3F")
VERDE_CLARO = HexColor("#E7F1EA")
GRIS = HexColor("#555555")
GRIS_CLARO = HexColor("#EFEFEF")

DOC1_COL_DESC = 268.0


def doc_01() -> dict:
    F = FUENTE["LibSans"]
    lineas = [
        L("Mantenimiento integral de zonas verdes y arbolado urbano (lote 1), mensualidad de octubre de 2026",
          "Mantenimiento integral de zonas verdes", "1", "6840.00", ud="mes", cant_txt="1,00"),
        L("Desbroce mecanizado de márgenes en caminos municipales y cunetas",
          "Desbroce mecanizado de márgenes", "14500", "0.18", ud="m²", cant_txt="14.500,00"),
        L("Suministro y plantación de flor de temporada en rotondas y medianas",
          "Suministro y plantación de flor", "320", "2.37", ud="ud", cant_txt="320,00"),
        L("Reparación de la red de riego automático del parque del Ejido (mano de obra)",
          "Reparación de la red de riego", "18", "31.50", ud="h", cant_txt="18,00"),
        L("Retirada de restos vegetales a planta de compostaje autorizada",
          "Retirada de restos vegetales", "6", "95.00", ud="viaje", cant_txt="6,00"),
    ]
    for ln in lineas:
        ln["renglones"] = partir(ln["concepto"], F, 8.5, DOC1_COL_DESC)
        ln["primera"] = ln["renglones"][0]
    t = calcular(lineas)
    return {
        "archivo": "h3a_01_ayuntamiento_dir3_expediente.pdf",
        "estilo": ("Factura de empresa de jardinería (S.L.) a un ayuntamiento, maquetación institucional en Liberation "
                   "Sans: banda verde superior, logotipo circular, datos del emisor a la izquierda, caja 'FACTURA' a la "
                   "derecha (Nº de factura / Fecha de factura / Página) y debajo el bloque 'DESTINATARIO "
                   "(ADMINISTRACIÓN PÚBLICA)'; tabla 'Códigos DIR3' con Oficina contable, Órgano gestor y Unidad "
                   "tramitadora (código + denominación), fila de tres celdas Expediente / Contrato / Periodo facturado, "
                   "párrafo con el decreto de adjudicación, tabla Concepto / Ud. / Cantidad / Precio unitario / Importe "
                   "con conceptos de dos renglones, totales en caja a la derecha y pago a la izquierda, A4"),
        "empresa_nif": CIF_CIERZO,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "A26/0412",
        "fecha": date(2026, 11, 2),
        "fecha_txt": "02/11/2026",
        "emisor": {"nombre": "Mantenimientos Urbanos Cierzo Verde, S.L.", "nif": CIF_CIERZO,
                   "domicilio_contiene": "50197"},
        "destinatario": {"nombre": "Ayuntamiento de Torrebermeja del Campo", "nif": CIF_TORREBERMEJA,
                         "domicilio_contiene": "50749"},
        "nif_txt": {"emisor": f"C.I.F. {CIF_CIERZO}", "destinatario": f"NIF: {CIF_TORREBERMEJA}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"expediente": "2026/SEM/000214", "contrato": "CSV-PARQ-2025/07",
                        "periodo": "01/10/2026 - 31/10/2026"},
        "iban": "ES0012123434565678780412",
        "iban_txt": "ES00 1212 3434 5656 7878 0412",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "textos": ["Oficina contable", "Órgano gestor", "Unidad tramitadora", "LA0019843", "L01509846", "LA0019851",
                   "Decreto de Alcaldía 2025/0388"],
        "nifs_distractores": set(),
        "notas": ("Factura propia de la empresa de jardinería (emisora, S.L.) a un ayuntamiento, sin marcas VERI*FACTU: "
                  "documento preparatorio. El destinatario es una entidad local con NIF P. Trampas: bloque de códigos "
                  "DIR3 (LA0019843 Intervención Municipal, L01509846 Alcaldía, LA0019851 Servicio de Medio Ambiente y "
                  "Parques) con aspecto de identificadores; el nombre del ayuntamiento se repite como denominación del "
                  "órgano gestor; referencias Expediente 2026/SEM/000214, Contrato CSV-PARQ-2025/07 y Periodo facturado "
                  "01/10/2026 - 31/10/2026 (fechas que no son la de factura); párrafo con 'Decreto de Alcaldía "
                  "2025/0388 de 14/07/2025'; 'Página 1 de 1' en la caja de la factura; inscripción registral en el pie. "
                  "Cantidad con miles (14.500,00 m²). Base 11.345,40 x 21 % = 2.382,534 -> 2.382,53; total 13.727,93."),
        "render": render_01,
    }


def render_01(ruta: Path, d: dict):
    F, FB, FI = FUENTE["LibSans"], FUENTE["LibSans-Bold"], FUENTE["LibSans-Italic"]
    W, H = A4
    M = 42
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", d["emisor"]["nombre"])
    rect(c, 0, H - 10, W, 10, relleno=VERDE)
    # logotipo y emisor
    c.setFillColor(VERDE)
    c.circle(M + 15, H - 48, 15, fill=1, stroke=0)
    tx(c, M + 15, H - 52, "CV", FB, 11, white, "c")
    tx(c, M + 38, H - 44, "CIERZO VERDE", FB, 17, VERDE)
    tx(c, M + 38, H - 57, "Conservación de parques, jardines y arbolado", FI, 8, GRIS)
    y = H - 84
    for s, f in ((d["emisor"]["nombre"], FB), (d["nif_txt"]["emisor"], F), ("Camino Inventado, 27, nave 4", F),
                 ("50197 Zaragoza", F), ("Tel. 976 000 000", F), ("facturacion@cierzoverde.example", F)):
        tx(c, M, y, s, f, 8.5)
        y -= 11
    # caja de la factura
    bw = 205
    bx = W - M - bw
    yy = H - 30
    rect(c, bx, yy - 20, bw, 20, relleno=VERDE)
    tx(c, bx + bw / 2, yy - 14, "FACTURA", FB, 11, white, "c", cs=2.5)
    yy -= 20
    for k, v in (("Nº de factura", d["numero"]), ("Fecha de factura", d["fecha_txt"]), ("Página", "1 de 1")):
        rect(c, bx, yy - 18, bw, 18, borde=VERDE)
        tx(c, bx + 6, yy - 12, k, F, 8, GRIS)
        tx(c, bx + bw - 6, yy - 12, v, FB, 9.5, black, "r")
        yy -= 18
    # destinatario
    yy -= 16
    tx(c, bx, yy, "DESTINATARIO (ADMINISTRACIÓN PÚBLICA)", FB, 7.5, VERDE)
    yy -= 13
    tx(c, bx, yy, d["destinatario"]["nombre"], FB, 9.5)
    for s in (d["nif_txt"]["destinatario"], "Plaza Imaginaria, 1", "50749 Torrebermeja del Campo (Zaragoza)"):
        yy -= 11
        tx(c, bx, yy, s, F, 8.5)
    # códigos DIR3
    y = min(y, yy) - 22
    rect(c, M, y - 4, W - 2 * M, 16, relleno=VERDE_CLARO)
    tx(c, M + 6, y + 1, "CÓDIGOS DIR3 · FACTURACIÓN ELECTRÓNICA", FB, 8, VERDE)
    xs = (M + 6, M + 130, M + 215)
    y -= 18
    for i, s in enumerate(("Rol", "Código", "Denominación")):
        tx(c, xs[i], y, s, FB, 7.5, GRIS)
    raya(c, M, y - 4, W - M, y - 4, GRIS, 0.4)
    for rol, cod, den in (("Oficina contable", "LA0019843", "Intervención Municipal"),
                          ("Órgano gestor", "L01509846", "Ayuntamiento de Torrebermeja del Campo"),
                          ("Unidad tramitadora", "LA0019851", "Servicio de Medio Ambiente y Parques")):
        y -= 14
        tx(c, xs[0], y, rol, F, 8.5)
        tx(c, xs[1], y, cod, FB, 8.5)
        tx(c, xs[2], y, den, F, 8.5)
    raya(c, M, y - 6, W - M, y - 6, GRIS, 0.4)
    # referencias
    y -= 22
    cw = (W - 2 * M) / 3
    refs = d["referencias"]
    for i, (k, v) in enumerate((("Expediente", refs["expediente"]), ("Contrato", refs["contrato"]),
                                ("Periodo facturado", refs["periodo"]))):
        x = M + i * cw
        rect(c, x, y - 18, cw - 6, 30, borde=GRIS, grosor=0.4)
        tx(c, x + 6, y + 3, k, F, 7.5, GRIS)
        tx(c, x + 6, y - 11, v, FB, 9)
    y -= 36
    tx(c, M, y, "Servicio de conservación de parques y jardines municipales (lote 1), adjudicado por "
           "Decreto de Alcaldía 2025/0388 de 14/07/2025.", FI, 8, GRIS)
    # tabla de conceptos
    y -= 20
    xd, xu, xc, xp, xi = M + 6, M + 300, M + 368, M + 438, W - M - 6
    rect(c, M, y - 5, W - 2 * M, 17, relleno=VERDE)
    for x, s, al in ((xd, "Concepto", "l"), (xu, "Ud.", "c"), (xc, "Cantidad", "r"), (xp, "Precio unitario", "r"),
                     (xi, "Importe", "r")):
        tx(c, x, y, s, FB, 8, white, al)
    y -= 8
    for i, ln in enumerate(d["lineas"]):
        alto = 11 * len(ln["renglones"]) + 7
        if i % 2:
            rect(c, M, y - alto, W - 2 * M, alto, relleno=GRIS_CLARO)
        yl = y - 11
        for r in ln["renglones"]:
            tx(c, xd, yl, r, F, 8.5)
            yl -= 11
        tx(c, xu, y - 11, ln["ud"], F, 8.5, black, "c")
        tx(c, xc, y - 11, ln["cant_txt"], F, 8.5, black, "r")
        tx(c, xp, y - 11, num(ln["precio"]), F, 8.5, black, "r")
        tx(c, xi, y - 11, num(ln["importe"]), F, 8.5, black, "r")
        y -= alto
    raya(c, M, y, W - M, y, VERDE, 1)
    # totales a la derecha
    t = d["t"]
    g = t["desglose"][0]
    tw = 215
    tx0 = W - M - tw
    yt = y - 20
    for k, v, neg in (("Base imponible", num(t["base"]) + " €", False),
                      (f"IVA {g['tipo']:.0f} %", num(g["cuota"]) + " €", False)):
        tx(c, tx0 + 8, yt, k, F, 9)
        tx(c, W - M - 8, yt, v, F, 9, black, "r")
        raya(c, tx0, yt - 5, W - M, yt - 5, GRIS, 0.3)
        yt -= 17
    rect(c, tx0, yt - 7, tw, 20, relleno=VERDE)
    tx(c, tx0 + 8, yt, "TOTAL FACTURA", FB, 10, white)
    tx(c, W - M - 8, yt, num(t["total"]) + " €", FB, 10.5, white, "r")
    # pago a la izquierda
    yp = y - 20
    tx(c, M, yp, "Forma de pago: Transferencia bancaria a 30 días", F, 8.5)
    tx(c, M, yp - 12, f"IBAN: {d['iban_txt']}", FB, 8.5)
    tx(c, M, yp - 24, "Indiquen el número de factura en el concepto de la transferencia.", FI, 7.5, GRIS)
    # pie
    raya(c, M, 44, W - M, 44, VERDE, 0.6)
    tx(c, W / 2, 32, f"{d['emisor']['nombre']} · Inscrita en el Registro Mercantil de Zaragoza, tomo 4.512, folio 87, "
                     f"hoja Z-61203 · {d['nif_txt']['emisor']}", F, 6.8, GRIS, "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 2. Fundación (CIF G) -> empresa: concepto en párrafos e importe único (A5, Liberation Serif)
# ==============================================================================================
GRANATE = HexColor("#7A2631")
DOC2_ANCHO_TEXTO = A5[0] - 2 * 34

DOC2_PARRAFOS = (
    "Diseño e impartición del programa de bienestar laboral «Equipos que cuidan», compuesto por cuatro talleres "
    "presenciales de cuatro horas sobre comunicación asertiva, prevención del estrés y conciliación, dirigidos a la "
    "plantilla del centro de trabajo de Sangonera la Seca y celebrados los días 6, 13, 20 y 27 de octubre de 2026.",
    "El servicio incluye la preparación de los materiales didácticos, la coordinación con el departamento de personas "
    "de la empresa, la evaluación de satisfacción de los 38 participantes y la memoria final de resultados, entregada "
    "el 30 de octubre de 2026.",
)


def doc_02() -> dict:
    F = FUENTE["LibSerif"]
    ln = L(DOC2_PARRAFOS[0], "Diseño e impartición del programa", importe="3172.50")
    ln["renglones"] = partir(ln["concepto"], F, 9.2, DOC2_ANCHO_TEXTO)
    ln["primera"] = ln["renglones"][0]
    lineas = [ln]
    t = calcular(lineas)
    return {
        "archivo": "h3a_02_fundacion_parrafo_importe_unico.pdf",
        "estilo": ("Factura de una fundación (CIF G) a una empresa en formato A5 tipo carta, Liberation Serif: membrete "
                   "centrado con lema en cursiva y datos en una línea, título 'F A C T U R A' espaciado, datos a la "
                   "izquierda (Nº de factura / Fecha de emisión en letra / Su pedido nº) y recuadro 'Facturado a:' a la "
                   "derecha, SIN tabla: apartado 'Concepto' con dos párrafos de texto corrido y un único importe con "
                   "puntos guía ('Importe de los servicios'), IVA 21 % y 'Total factura'; forma de pago en frase, IBAN "
                   "en renglón propio y pie con el registro de fundaciones"),
        "empresa_nif": CIF_SANGONERA,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "2026/017",
        "fecha": date(2026, 11, 4),
        "fecha_txt": "4 de noviembre de 2026",
        "emisor": {"nombre": "Fundación Llano de Almagre", "nif": CIF_LLANO, "domicilio_contiene": "30820"},
        "destinatario": {"nombre": "Conservas Vegetales Sangonera, S.L.", "nif": CIF_SANGONERA,
                         "domicilio_contiene": "30835"},
        "nif_txt": {"emisor": f"CIF {CIF_LLANO}", "destinatario": f"CIF {CIF_SANGONERA}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"pedido": "4500118274"},
        "iban": "ES0023234545676789890017",
        "iban_txt": "ES00 2323 4545 6767 8989 0017",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "fmt": num,
        "textos": ["Importe de los servicios", "Su pedido nº", "Facturado a:"],
        "nifs_distractores": set(),
        "notas": ("Factura RECIBIDA por la empresa usuaria (destinataria, conservera) de una fundación con CIF G. Sin "
                  "tabla de líneas: el servicio se describe en dos párrafos de texto corrido y hay un único importe "
                  "('Importe de los servicios 3.172,50 €'), que es la única línea esperada (sin cantidad ni precio). "
                  "Trampas: fecha de emisión escrita en letra ('4 de noviembre de 2026'); fechas en el texto (6, 13, "
                  "20 y 27 de octubre, 30 de octubre, vencimiento 4 de diciembre de 2026); '38 participantes'; número "
                  "de pedido del cliente de 10 dígitos (4500118274); número de registro de la fundación (312); "
                  "'entidad sin ánimo de lucro' en el pie pese a que la factura lleva IVA 21 %. "
                  "Base 3.172,50 x 21 % = 666,225 -> 666,23 (half-up); total 3.838,73."),
        "render": render_02,
    }


def render_02(ruta: Path, d: dict):
    F, FB, FI = FUENTE["LibSerif"], FUENTE["LibSerif-Bold"], FUENTE["LibSerif-Italic"]
    W, H = A5
    M = 34
    c = nuevo_canvas(ruta, A5, f"Factura {d['numero']}", d["emisor"]["nombre"])
    # membrete
    tx(c, W / 2, H - 42, d["emisor"]["nombre"], FB, 14, GRANATE, "c")
    tx(c, W / 2, H - 54, "Formación, empleo y participación social", FI, 8.5, GRIS, "c")
    tx(c, W / 2, H - 67, f"{d['nif_txt']['emisor']} · Calle Inventada, 14, bajo · 30820 Alcantarilla (Murcia)",
       F, 7.3, black, "c")
    tx(c, W / 2, H - 76, "Tel. 968 000 000 · administracion@llanodealmagre.example", F, 7.3, black, "c")
    raya(c, M, H - 84, W - M, H - 84, GRANATE, 0.8)
    raya(c, M, H - 86.5, W - M, H - 86.5, GRANATE, 0.3)
    # título y datos
    y = H - 110
    tx(c, M, y, "FACTURA", FB, 12.5, GRANATE, "l", cs=3.2)
    y -= 20
    for k, v in (("Nº de factura:", d["numero"]), ("Fecha de emisión:", d["fecha_txt"]),
                 ("Su pedido nº:", d["referencias"]["pedido"])):
        tx(c, M, y, k, FI, 8.5, GRIS)
        tx(c, M + 72, y, v, FB, 8.8)
        y -= 12.5
    # facturado a
    bw = 150
    bx = W - M - bw
    by = H - 100
    rect(c, bx, by - 66, bw, 66, borde=GRANATE, grosor=0.5)
    tx(c, bx + 7, by - 12, "Facturado a:", FI, 8, GRIS)
    tx(c, bx + 7, by - 25, d["destinatario"]["nombre"], FB, 8.6)
    yy = by - 36
    for s in (d["nif_txt"]["destinatario"], "Carretera Ficticia, km 3,2", "30835 Sangonera la Seca (Murcia)"):
        tx(c, bx + 7, yy, s, F, 8.2)
        yy -= 10
    # concepto en párrafos
    y = min(y, by - 66) - 22
    tx(c, M, y, "Concepto", FB, 10, GRANATE)
    raya(c, M, y - 4, M + 46, y - 4, GRANATE, 0.4)
    y -= 18
    for i, p in enumerate(DOC2_PARRAFOS):
        renglones = d["lineas"][0]["renglones"] if i == 0 else partir(p, F, 9.2, DOC2_ANCHO_TEXTO)
        for r in renglones:
            tx(c, M, y, r, F, 9.2)
            y -= 12.2
        y -= 6
    tx(c, M, y, "Los trabajos se han realizado de acuerdo con la propuesta de colaboración aceptada por la empresa.",
       FI, 8.3, GRIS)
    # importe único y totales
    t = d["t"]
    y -= 26
    xl, xr = M + 70, W - M
    filas = (("Importe de los servicios", num(t["base"]) + " €", F),
             (f"IVA {t['desglose'][0]['tipo']:.0f} %", num(t["cuota"]) + " €", F))
    for k, v, f in filas:
        tx(c, xl, y, k, f, 9.4)
        guias(c, xl + ancho(k, f, 9.4) + 6, xr - ancho(v, f, 9.4) - 8, y, F, 9.4, GRIS)
        tx(c, xr, y, v, f, 9.4, black, "r")
        y -= 15
    raya(c, xl, y + 9, xr, y + 9, black, 0.6)
    y -= 3
    k, v = "Total factura", num(t["total"]) + " €"
    tx(c, xl, y, k, FB, 10.5)
    guias(c, xl + ancho(k, FB, 10.5) + 6, xr - ancho(v, FB, 10.5) - 8, y, F, 10.5, GRIS)
    tx(c, xr, y, v, FB, 10.5, black, "r")
    # pago
    y -= 30
    for r in partir("Forma de pago: transferencia bancaria a la cuenta de la Fundación, con vencimiento el 4 de "
                    "diciembre de 2026 e indicando el número de factura.", F, 8.6, DOC2_ANCHO_TEXTO):
        tx(c, M, y, r, F, 8.6)
        y -= 11
    tx(c, M, y - 2, f"IBAN: {d['iban_txt']}", FB, 8.8)
    # pie
    raya(c, M, 46, W - M, 46, GRANATE, 0.3)
    tx(c, W / 2, 35, "Fundación inscrita en el Registro de Fundaciones de la Región de Murcia con el número 312.",
       FI, 7, GRIS, "c")
    tx(c, W / 2, 26, "Entidad sin ánimo de lucro · www.llanodealmagre.example", FI, 7, GRIS, "c")
    c.showPage()
    c.save()


# ==============================================================================================
# 3. Comunidad de bienes (CIF E): líneas en la página 1, totales en la página 2 (Letter, mono)
# ==============================================================================================
DOC3_TAM = 8.0
DOC3_PASO = 14.0
DOC3_COL_DESC = 268.0


def doc_03() -> dict:
    F = FUENTE["JBMono"]
    lineas = [
        L("Mano de obra oficial 1ª electricista", "Mano de obra oficial", "32", "27.50", ud="h", cod="MO-OF1"),
        L("Mano de obra ayudante electricista", "Mano de obra ayudante", "32", "19.80", ud="h", cod="MO-AYU"),
        L("Cable RZ1-K 0,6/1 kV 1x16 mm2 libre de halógenos", "Cable RZ1-K 0,6/1 kV 1x16", "96", "3.42",
          ud="m", cod="EL-0412"),
        L("Cable RZ1-K 0,6/1 kV 1x10 mm2 libre de halógenos", "Cable RZ1-K 0,6/1 kV 1x10", "64", "2.27",
          ud="m", cod="EL-0415"),
        L("Tubo rígido PVC M40 gris, barra de 3 m", "Tubo rígido PVC M40", "22", "4.15", ud="ud", cod="EL-0120"),
        L("Abrazadera metálica M40 con taco", "Abrazadera metálica M40", "60", "0.48", ud="ud", cod="EL-0122"),
        L("Centralización de contadores modular para 8 suministros, con embarrado y bornas de salida",
          "Centralización de contadores modular", "1", "612.00", ud="ud", cod="EL-0801"),
        L("Interruptor general de maniobra 160 A", "Interruptor general de maniobra", "1", "148.70", ud="ud",
          cod="EL-0805"),
        L("Caja general de protección CGP-7 160 A", "Caja general de protección", "1", "96.40", ud="ud",
          cod="EL-0910"),
        L("Fusible NH-00 100 A gG", "Fusible NH-00 100 A", "3", "7.85", ud="ud", cod="EL-0933"),
        L("Pica de toma de tierra acero-cobre 2 m", "Pica de toma de tierra", "2", "18.90", ud="ud", cod="EL-1010"),
        L("Cable de cobre desnudo 35 mm2 para red de tierra", "Cable de cobre desnudo", "18", "4.96", ud="m",
          cod="EL-1015"),
        L("Regleta de conexión y bornas para derivaciones individuales", "Regleta de conexión y bornas", "8",
          "6.35", ud="ud", cod="EL-1102"),
        L("Luminaria LED estanca 36 W para cuarto de contadores", "Luminaria LED estanca 36", "2", "41.60",
          ud="ud", cod="EL-1210"),
        L("Tubo PVC evacuación 110 mm serie B, barra de 3 m", "Tubo PVC evacuación 110", "9", "14.75", ud="ud",
          cod="FO-2040"),
        L("Codo PVC 87º de 110 mm", "Codo PVC 87º", "12", "3.10", ud="ud", cod="FO-2042"),
        L("Injerto simple PVC 110-110 mm a 45º", "Injerto simple PVC", "6", "5.85", ud="ud", cod="FO-2045"),
        L("Manguito de dilatación PVC 110 mm", "Manguito de dilatación PVC", "4", "6.40", ud="ud", cod="FO-2050"),
        L("Abrazadera isofónica 110 mm con varilla roscada y taco", "Abrazadera isofónica 110", "16", "2.95",
          ud="ud", cod="FO-2061"),
        L("Adhesivo para PVC, bote de 500 ml", "Adhesivo para PVC", "2", "11.20", ud="ud", cod="FO-2070"),
        L("Limpiador desengrasante para PVC, 500 ml", "Limpiador desengrasante para PVC", "1", "7.90", ud="ud",
          cod="FO-2075"),
        L("Andamio tubular en patio de luces, alquiler semanal con montaje y desmontaje incluidos",
          "Andamio tubular en patio", "2", "145.00", ud="sem", cod="AL-3001"),
        L("Contenedor de escombros de 5 m3, colocación y retirada", "Contenedor de escombros", "1", "165.00",
          ud="ud", cod="AL-3005"),
        L("Gestión de residuos de obra en gestor autorizado", "Gestión de residuos de obra", "1.35", "38.00",
          ud="t", cod="AL-3010", cant_txt="1,35"),
        L("Albañilería: apertura y cierre de rozas en patinillo de instalaciones", "Albañilería: apertura y cierre",
          "6", "24.50", ud="h", cod="AL-3020"),
        L("Mortero de reparación tixotrópico, saco de 25 kg", "Mortero de reparación tixotrópico", "4", "16.80",
          ud="ud", cod="AL-3022"),
        L("Pintura plástica blanca para remates, bote de 4 l", "Pintura plástica blanca", "2", "23.45", ud="ud",
          cod="AL-3030"),
        L("Revisión final, pruebas de estanqueidad y medición de aislamiento", "Revisión final, pruebas de",
          "1", "85.00", ud="ud", cod="VA-9010"),
        L("Desplazamientos de vehículo y pequeño material", "Desplazamientos de vehículo", "1", "60.00", ud="ud",
          cod="VA-9000"),
    ]
    for ln in lineas:
        ln["renglones"] = partir(ln["concepto"], F, DOC3_TAM, DOC3_COL_DESC)
        ln["primera"] = ln["renglones"][0]
        if ln["cant_txt"] is None:
            ln["cant_txt"] = num(ln["cantidad"])
    t = calcular(lineas)
    return {
        "archivo": "h3a_03_comunidad_bienes_2pag_totales_p2.pdf",
        "estilo": ("Factura de una comunidad de bienes (C.B., CIF E con guion) de electricidad y fontanería a una "
                   "comunidad de propietarios (CIF H), aspecto de impresora de líneas en JetBrains Mono y US Letter: "
                   "cabecera de texto con datos 'Nº ......: / Fecha ...: / Página ..:' con puntos guía, recuadro "
                   "CLIENTE, línea 'Obra:', tabla CÓDIGO / DESCRIPCIÓN / UD / CANTIDAD / PRECIO / IMPORTE entre filetes "
                   "que llena la página 1 (conceptos de dos renglones) y continúa sin 'Suma y sigue'; la página 2 "
                   "repite en una línea el emisor, el número y la fecha y contiene solo los totales con puntos guía, la "
                   "forma de pago, las observaciones y la composición de la C.B. con los DNI de los comuneros; fecha "
                   "dd-mm-aaaa, importes 1.234,56 sin €"),
        "empresa_nif": CIF_LASHERAS,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "LO-2026/093",
        "fecha": date(2026, 10, 30),
        "fecha_txt": "30-10-2026",
        "emisor": {"nombre": "Instalaciones Lasheras Ortín, C.B.", "nif": CIF_LASHERAS, "domicilio_contiene": "44600"},
        "destinatario": {"nombre": "Comunidad de Propietarios Residencial Las Acacias", "nif": CIF_ACACIAS,
                         "domicilio_contiene": "44580"},
        "nif_txt": {"emisor": f"CIF: {con_guion(CIF_LASHERAS)}", "destinatario": f"CIF: {con_guion(CIF_ACACIAS)}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"obra": "Renovación de montantes y bajantes, portal 2"},
        "iban": "ES0031315151717191910093",
        "iban_txt": "ES00 3131 5151 7171 9191 0093",
        "forma_pago_contiene": "Ingreso en cuenta",
        "indicios": [],
        "fmt": num,
        "textos": ["Página 1 de 2", "Página 2 de 2", DNI_ANDRES, DNI_RUBEN],
        "prohibidos": ["suma y sigue", "suma anterior", "transporte", "a cuenta"],
        "nifs_distractores": {DNI_ANDRES, DNI_RUBEN},
        "dos_paginas": True,
        "notas": ("Factura propia de una comunidad de bienes (emisora, CIF E) sin marcas VERI*FACTU: documento "
                  "preparatorio. Dos páginas: las 29 líneas ocupan toda la página 1 y la tabla termina sin 'Suma y "
                  "sigue'; la página 2 solo repite emisor, número y fecha en una línea y contiene la base, el IVA y el "
                  "total. Trampas: CIF del emisor y del cliente con guion (E-..., H-...); en la página 2 figuran los "
                  "DNI de los dos comuneros (Andrés y Rubén Lasheras Ortín, 50 % cada uno), que no son el NIF del "
                  "emisor; nombre comercial 'INSTALACIONES LASHERAS ORTÍN, C.B.' en mayúsculas en la cabecera; "
                  "observaciones con fechas (13-10-2026, 24-10-2026), parte de trabajo nº 4417 y vencimiento "
                  "29-11-2026; cantidad decimal 1,35 t; códigos de artículo con guion. Todas las líneas al 21 %. "
                  f"Base {num(t['base'])} x 21 % = {num(t['base'] * D('0.21'), nd=4)} -> {num(t['cuota'])}; "
                  f"total {num(t['total'])}."),
        "render": render_03,
    }


def render_03(ruta: Path, d: dict):
    F, FB = FUENTE["JBMono"], FUENTE["JBMono-Bold"]
    W, H = letter
    M = 40
    T = DOC3_TAM
    c = nuevo_canvas(ruta, letter, f"Factura {d['numero']}", d["emisor"]["nombre"])
    e, t = d["emisor"], d["t"]
    pie = f"{e['nombre']} · {d['nif_txt']['emisor']}"

    def pie_pagina(n):
        raya(c, M, 46, W - M, 46, black, 0.4)
        tx(c, M, 36, pie, F, 7)
        tx(c, W - M, 36, f"Página {n} de 2", F, 7, black, "r")

    # ---- página 1: cabecera
    y = H - 50
    tx(c, M, y, "INSTALACIONES LASHERAS ORTÍN, C.B.", FB, 11)
    tx(c, M, y - 13, "Electricidad · Fontanería · Mantenimiento de edificios", F, T)
    tx(c, M, y - 25, "C/ Mayor Inventada, 31 · 44600 Alcañiz (Teruel)", F, T)
    tx(c, M, y - 37, f"{d['nif_txt']['emisor']} · Tel. 978 000 000", F, T)
    tx(c, M, y - 49, "lasheras@instalacioneslasheras.example", F, T)
    xr = W - M - 170
    tx(c, xr, y, "F A C T U R A", FB, 11)
    for i, (k, v) in enumerate((("Nº ......:", d["numero"]), ("Fecha ...:", d["fecha_txt"]),
                                ("Página ..:", "1 de 2"))):
        tx(c, xr, y - 15 - 12 * i, k, F, T)
        tx(c, xr + 62, y - 15 - 12 * i, v, FB, T)
    # cliente
    y -= 72
    rect(c, M, y - 58, 300, 66, borde=black, grosor=0.6)
    tx(c, M + 8, y - 4, "CLIENTE", FB, T)
    tx(c, M + 8, y - 17, d["destinatario"]["nombre"], FB, T)
    for i, s in enumerate((d["nif_txt"]["destinatario"], "Avenida Imaginaria, 12", "44580 Valderrobres (Teruel)")):
        tx(c, M + 8, y - 29 - 11 * i, s, F, T)
    tx(c, M + 318, y - 17, "Cód. cliente: 0217", F, T)
    tx(c, M + 318, y - 29, "Forma pago..: Ingreso en cuenta", F, T)
    y -= 80
    tx(c, M, y, f"Obra: {d['referencias']['obra']}", FB, T)
    # tabla
    y -= 16
    xc, xd, xu, xq, xp, xi = M, M + 54, M + 340, M + 405, M + 468, W - M
    raya(c, M, y + 10, W - M, y + 10, black, 0.8)
    for x, s, al in ((xc, "CÓDIGO", "l"), (xd, "DESCRIPCIÓN", "l"), (xu, "UD", "c"), (xq, "CANTIDAD", "r"),
                     (xp, "PRECIO", "r"), (xi, "IMPORTE", "r")):
        tx(c, x, y, s, FB, T, black, al)
    raya(c, M, y - 5, W - M, y - 5, black, 0.8)
    y -= 18
    limite = 62
    for ln in d["lineas"]:
        alto = DOC3_PASO * len(ln["renglones"])
        if y - alto + DOC3_PASO < limite:
            raise RuntimeError("doc 3: las líneas no caben en la página 1; ajuste el paso o el texto")
        tx(c, xc, y, ln["cod"], F, T)
        tx(c, xu, y, ln["ud"], F, T, black, "c")
        tx(c, xq, y, ln["cant_txt"], F, T, black, "r")
        tx(c, xp, y, num(ln["precio"]), F, T, black, "r")
        tx(c, xi, y, num(ln["importe"]), F, T, black, "r")
        for r in ln["renglones"]:
            tx(c, xd, y, r, F, T)
            y -= DOC3_PASO
    raya(c, M, y + DOC3_PASO - 5, W - M, y + DOC3_PASO - 5, black, 0.8)
    d["_hueco_p1"] = y + DOC3_PASO - 5 - limite   # espacio libre bajo la tabla (debe ser menor que el bloque de totales)
    pie_pagina(1)
    c.showPage()
    # ---- página 2: cabecera reducida y totales
    y = H - 50
    tx(c, M, y, "INSTALACIONES LASHERAS ORTÍN, C.B.", FB, 9)
    tx(c, W - M, y, f"Factura {d['numero']} · {d['fecha_txt']} · Página 2 de 2", F, T, black, "r")
    raya(c, M, y - 6, W - M, y - 6, black, 0.8)
    y -= 40
    g = t["desglose"][0]
    xl, xv = M + 250, W - M
    for k, v, f in (("BASE IMPONIBLE", num(t["base"]), F), (f"I.V.A. {g['tipo']:.0f} %", num(g["cuota"]), F)):
        tx(c, xl, y, k, f, 9)
        guias(c, xl + ancho(k, f, 9) + 6, xv - 80, y, F, 9, black)
        tx(c, xv, y, v, f, 9, black, "r")
        y -= 15
    raya(c, xl, y + 7, xv, y + 7, black, 0.6)
    raya(c, xl, y + 5, xv, y + 5, black, 0.6)
    y -= 8
    tx(c, xl, y, "TOTAL FACTURA EUR", FB, 10)
    tx(c, xv, y, num(t["total"]), FB, 10, black, "r")
    y -= 40
    tx(c, M, y, "Forma de pago: Ingreso en cuenta", F, T)
    tx(c, M, y - 12, f"IBAN: {d['iban_txt']}", FB, T)
    tx(c, M, y - 24, "Vencimiento: 29-11-2026", F, T)
    y -= 50
    tx(c, M, y, "OBSERVACIONES", FB, T)
    for i, s in enumerate(("Trabajos realizados entre el 13-10-2026 y el 24-10-2026 según parte de trabajo nº 4417.",
                           "Material retirado entregado en gestor autorizado; certificado de instalación disponible.")):
        tx(c, M, y - 12 - 12 * i, s, F, T)
    y -= 56
    tx(c, M, y, "Comunidad de bienes integrada por:", F, T)
    tx(c, M, y - 12, f"  D. Andrés Lasheras Ortín, NIF {DNI_ANDRES}, participación 50 %", F, T)
    tx(c, M, y - 24, f"  D. Rubén Lasheras Ortín, NIF {DNI_RUBEN}, participación 50 %", F, T)
    y -= 60
    rect(c, M, y - 50, 220, 58, borde=black, grosor=0.5)
    tx(c, M + 8, y - 4, "Conforme cliente (firma y sello):", F, 7)
    pie_pagina(2)
    c.showPage()
    c.save()


# ==============================================================================================
# 4. Mayorista: dto. pronto pago 2 % sobre toda la factura, IVA 10 % y 21 % con desglose (A4, DejaVu Sans)
# ==============================================================================================
AZUL = HexColor("#1F4E79")
AZUL_CLARO = HexColor("#E3ECF5")
DOC4_COL_DESC = 232.0


def doc_04() -> dict:
    F = FUENTE["DejaVu"]
    lineas = [
        L("Aceite de oliva virgen extra, garrafa de 5 l", "Aceite de oliva virgen extra", "6", "38.90", "10",
          cod="AC-1105"),
        L("Arroz bomba, saco de 5 kg", "Arroz bomba, saco de", "4", "17.35", "10", cod="AR-0210"),
        L("Vino blanco D.O. Rueda verdejo, botella 75 cl", "Vino blanco D.O. Rueda", "24", "5.45", "21",
          cod="VB-3307"),
        L("Gamba blanca congelada, caja de 2 kg", "Gamba blanca congelada", "5", "42.80", "10", cod="CG-5521"),
        L("Tomate triturado natural, lata de 3 kg", "Tomate triturado natural", "12", "3.15", "10", cod="CN-0716"),
        L("Cerveza especial botellín 33 cl, caja de 24", "Cerveza especial botellín", "6", "19.90", "21",
          cod="CV-4012"),
        L("Agua mineral natural 1,5 l, pack de 6", "Agua mineral natural 1,5", "20", "1.98", "10", cod="AG-0150"),
        L("Café en grano tueste natural, bolsa de 1 kg", "Café en grano tueste", "8", "14.60", "10", cod="CF-0901"),
        L("Lavavajillas industrial para máquina, garrafa de 20 l", "Lavavajillas industrial para", "3", "31.25",
          "21", cod="LM-7720"),
        L("Rollo de papel secamanos, paquete de 6 uds.", "Rollo de papel secamanos", "5", "12.40", "21",
          cod="PP-6604"),
    ]
    for ln in lineas:
        ln["renglones"] = partir(ln["concepto"], F, 8, DOC4_COL_DESC)
        ln["primera"] = ln["renglones"][0]
        ln["cant_txt"] = num(ln["cantidad"], nd=0)
    t = calcular(lineas, dto_pp="2")
    return {
        "archivo": "h3a_04_pronto_pago_10_21_desglose.pdf",
        "estilo": ("Factura de mayorista de hostelería (S.A.) a un restaurante en DejaVu Sans: emisor con marca en "
                   "azul arriba a la izquierda, título 'Factura' a la derecha con rejilla Número / Fecha (dd/mm/aa) / "
                   "Cód. cliente / Albarán / Ruta, recuadro 'Cliente', tabla Código / Descripción / Cant. / Precio / "
                   "Importe / IVA con tipos 10 % y 21 % mezclados por línea; debajo, a la izquierda, cuadro de desglose "
                   "por tipo (Tipo IVA / Importe / Dto. P.P. / Base imponible / Cuota IVA con fila Totales) y a la "
                   "derecha totales apilados 'Suma importes', 'Dto. pronto pago 2 %' en negativo, 'Base imponible', "
                   "'Total IVA' y 'TOTAL FACTURA', A4"),
        "empresa_nif": CIF_CABONEGRO,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "FA26-018734",
        "fecha": date(2026, 11, 6),
        "fecha_txt": "06/11/26",
        "emisor": {"nombre": "Distribuciones Hosteleras Mar de Alborán, S.A.", "nif": CIF_ALBORAN,
                   "domicilio_contiene": "04710"},
        "destinatario": {"nombre": "Restaurante Brisa de Cabo Negro, S.L.", "nif": CIF_CABONEGRO,
                         "domicilio_contiene": "04140"},
        "nif_txt": {"emisor": f"C.I.F.: {con_guion(CIF_ALBORAN)}", "destinatario": f"N.I.F.: {CIF_CABONEGRO}"},
        "lineas": lineas,
        "t": t,
        "referencias": {"albaran": "AL-55102"},
        "iban": "ES0042426262828202028734",
        "iban_txt": "ES00 4242 6262 8282 0202 8734",
        "forma_pago_contiene": "Transferencia a 10 días",
        "indicios": ["descuento"],
        "fmt": num,
        "textos": ["Dto. pronto pago 2 %", "Suma importes", "Dto. P.P."],
        "nifs_distractores": set(),
        "notas": ("Factura RECIBIDA por la empresa usuaria (destinataria, restaurante) de un mayorista (S.A.). El "
                  "descuento por pronto pago del 2 % se aplica a TODA la factura, no a las líneas: las líneas se "
                  "imprimen por su importe íntegro (cantidad x precio) y suman 1.116,95 ('Suma importes'), después "
                  "'Dto. pronto pago 2 %' -22,34 y 'Base imponible' 1.094,61. Por eso las líneas esperadas suman el "
                  "importe bruto y NO la base; el desglose esperado es el de bases netas del descuento. Desglose: 10 %: "
                  "711,00 - 14,22 = 696,78 -> cuota 69,68 (69,678); 21 %: 405,95 - 8,12 (8,119) = 397,83 -> cuota "
                  "83,54 (83,5443); IVA 153,22; total 1.247,83. Trampas: fecha con año de dos cifras (06/11/26), "
                  "albarán AL-55102 de 04/11/26, vencimiento 16/11/26, código de cliente 004321, ruta R-07, "
                  "'1,5 l', '75 cl', '33 cl' y '5 kg' en los conceptos; cantidades enteras sin decimales."),
        "render": render_04,
    }


def render_04(ruta: Path, d: dict):
    F, FB = FUENTE["DejaVu"], FUENTE["DejaVu-Bold"]
    W, H = A4
    M = 38
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", d["emisor"]["nombre"])
    e, t = d["emisor"], d["t"]
    # emisor
    rect(c, M, H - 70, 6, 40, relleno=AZUL)
    tx(c, M + 14, H - 46, "MAR DE ALBORÁN", FB, 15, AZUL)
    tx(c, M + 14, H - 62, "Distribución para hostelería y restauración", F, 7.5, GRIS)
    y = H - 88
    for s, f in ((e["nombre"], FB), (d["nif_txt"]["emisor"], F), ("Polígono Industrial Ficticio, calle 7, nave 22", F),
                 ("04710 Santa María del Águila (Almería)", F), ("Tel. 950 000 000 · pedidos@maralboran.example", F)):
        tx(c, M, y, s, f, 7.6)
        y -= 10
    # título y rejilla de datos
    xr = W - M
    tx(c, xr, H - 50, "Factura", FB, 20, AZUL, "r")
    gx = W - M - 250
    gy = H - 70
    celdas = (("Número", d["numero"]), ("Fecha", d["fecha_txt"]), ("Cód. cliente", "004321"),
              ("Albarán", d["referencias"]["albaran"]), ("Ruta", "R-07"))
    anchos = (70, 48, 52, 50, 30)
    for i, ((k, v), cw) in enumerate(zip(celdas, anchos)):
        x = gx + sum(anchos[:i])
        rect(c, x, gy - 30, cw, 30, borde=AZUL, grosor=0.5)
        rect(c, x, gy - 12, cw, 12, relleno=AZUL_CLARO)
        tx(c, x + cw / 2, gy - 9, k, F, 6.5, AZUL, "c")
        tx(c, x + cw / 2, gy - 24, v, FB, 7.5, black, "c")
    # cliente
    cy = gy - 44
    rect(c, gx, cy - 66, 250, 66, borde=AZUL, grosor=0.8, radio=3)
    tx(c, gx + 8, cy - 12, "Cliente", FB, 7.5, AZUL)
    tx(c, gx + 8, cy - 25, d["destinatario"]["nombre"], FB, 8.5)
    for i, s in enumerate((d["nif_txt"]["destinatario"], "Paseo Imaginario, 118", "04140 Carboneras (Almería)")):
        tx(c, gx + 8, cy - 37 - 10 * i, s, F, 7.8)
    # tabla
    y = cy - 92
    xc, xd, xq, xp, xi, xv = M + 4, M + 62, M + 340, M + 405, M + 475, W - M - 6
    rect(c, M, y - 5, W - 2 * M, 16, relleno=AZUL)
    for x, s, al in ((xc, "Código", "l"), (xd, "Descripción", "l"), (xq, "Cant.", "r"), (xp, "Precio", "r"),
                     (xi, "Importe", "r"), (xv, "IVA", "r")):
        tx(c, x, y, s, FB, 7.8, white, al)
    y -= 20
    for ln in d["lineas"]:
        tx(c, xc, y, ln["cod"], F, 8)
        tx(c, xq, y, ln["cant_txt"], F, 8, black, "r")
        tx(c, xp, y, num(ln["precio"]), F, 8, black, "r")
        tx(c, xi, y, num(ln["importe"]), F, 8, black, "r")
        tx(c, xv, y, f"{ln['iva']:.0f} %", F, 8, black, "r")
        for r in ln["renglones"]:
            tx(c, xd, y, r, F, 8)
            y -= 11
        raya(c, M, y + 5, W - M, y + 5, AZUL_CLARO, 0.6)
        y -= 5
    raya(c, M, y + 8, W - M, y + 8, AZUL, 1)
    # desglose por tipo (izquierda)
    yb = y - 18
    cols = (M + 4, M + 112, M + 172, M + 246, M + 312)
    tx(c, M, yb + 2, "Desglose por tipo de IVA", FB, 7.5, AZUL)
    yb -= 14
    rect(c, M, yb - 4, 316, 13, relleno=AZUL_CLARO)
    for x, s, al in zip(cols, ("Tipo IVA", "Importe", "Dto. P.P.", "Base imponible", "Cuota IVA"),
                        ("l", "r", "r", "r", "r")):
        tx(c, x, yb, s, FB, 6.8, AZUL, al)
    for g in t["desglose"]:
        yb -= 13
        for x, s, al in zip(cols, (f"{g['tipo']:.2f} %".replace(".", ","), num(g["bruto"]), num(g["dto"]),
                                   num(g["base"]), num(g["cuota"])), ("l", "r", "r", "r", "r")):
            tx(c, x, yb, s, F, 7.6, black, al)
    raya(c, M, yb - 5, M + 316, yb - 5, AZUL, 0.5)
    yb -= 15
    for x, s, al in zip(cols, ("Totales", num(t["bruto"]), num(t["dto"]), num(t["base"]), num(t["cuota"])),
                        ("l", "r", "r", "r", "r")):
        tx(c, x, yb, s, FB, 7.6, black, al)
    # totales apilados (derecha)
    tx0, tw = W - M - 182, 182
    yt = y - 20
    filas = (("Suma importes", num(t["bruto"])), (f"Dto. pronto pago {t['pct_dto']:.0f} %", num(-t["dto"])),
             ("Base imponible", num(t["base"])), ("Total IVA", num(t["cuota"])))
    for k, v in filas:
        tx(c, tx0 + 6, yt, k, F, 8.4)
        tx(c, W - M - 6, yt, v, F, 8.4, black, "r")
        raya(c, tx0, yt - 4, W - M, yt - 4, AZUL_CLARO, 0.6)
        yt -= 15
    rect(c, tx0, yt - 8, tw, 21, relleno=AZUL)
    tx(c, tx0 + 6, yt - 1, "TOTAL FACTURA", FB, 9.5, white)
    tx(c, W - M - 6, yt - 1, num(t["total"]) + " €", FB, 10, white, "r")
    # pago y condiciones
    yp = min(yb, yt) - 34
    tx(c, M, yp, "Forma de pago: Transferencia a 10 días (pronto pago) · Vencimiento: 16/11/26", F, 7.8)
    tx(c, M, yp - 12, f"IBAN: {d['iban_txt']}", FB, 7.8)
    tx(c, M, yp - 26, "El descuento por pronto pago queda condicionado al cobro de la factura dentro del plazo "
                      "indicado.", F, 6.8, GRIS)
    tx(c, M, yp - 36, f"Mercancía servida según albarán {d['referencias']['albaran']} de 04/11/26. "
                      "Comprueben la mercancía a la recepción.", F, 6.8, GRIS)
    # pie
    raya(c, M, 40, W - M, 40, AZUL, 0.5)
    tx(c, W / 2, 29, f"{e['nombre']} · Inscrita en el Registro Mercantil de Almería, tomo 1.873, folio 44, hoja "
                     f"AL-30912 · {d['nif_txt']['emisor']}", F, 6.2, GRIS, "c")
    c.showPage()
    c.save()


DOCUMENTOS = [doc_01, doc_02, doc_03, doc_04]


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
        debe(re.fullmatch(r"\d{5}", e[parte]["domicilio_contiene"]) is not None, f"domicilio {parte} no es un CP")
    for n in d["nifs_distractores"]:
        debe(es_valido(n), f"NIF distractor inválido {n}")
    for n in {e["emisor"]["nif"], e["destinatario"]["nif"]} | set(d["nifs_distractores"]):
        debe(nif_ficticio(n), f"NIF {n} no evidentemente ficticio")
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
    debe(e["iban"] is None or _plano(d["iban_txt"]) == e["iban"], "IBAN impreso distinto del esperado")

    importes = [e["base_imponible"], e["cuota_iva"], e["total_factura"], e["total_a_pagar"]]
    importes += [x for ln in e["lineas"] for x in (ln["cantidad"], ln["precio"], ln["importe"])]
    importes += [x for g in e["desglose"] for x in (g["tipo_iva"], g["base"], g["cuota"])]
    debe(all(x is None or re.fullmatch(r"-?\d+\.\d{2}", x) for x in importes), "importe sin 2 decimales")

    t = d["t"]
    base, cuota, total = D(e["base_imponible"]), D(e["cuota_iva"]), D(e["total_factura"])
    suma_lineas = sum(D(x["importe"]) for x in e["lineas"])
    debe(sum(D(g["base"]) for g in e["desglose"]) == base, "suma de bases del desglose != base")
    debe(sum(D(g["cuota"]) for g in e["desglose"]) == cuota, "suma de cuotas del desglose != cuota")
    for g in e["desglose"]:
        debe(q2(D(g["base"]) * D(g["tipo_iva"]) / 100) == D(g["cuota"]), f"cuota mal calculada al {g['tipo_iva']}")
    pct = t["pct_dto"]
    for g, gi in zip(e["desglose"], t["desglose"]):
        lin = sum((ln["importe"] for ln in d["lineas"] if ln["iva"] == D(g["tipo_iva"])), D("0"))
        if pct is None:
            debe(lin == D(g["base"]), f"líneas al {g['tipo_iva']} != base del tipo")
        else:
            debe(lin == gi["bruto"], f"líneas al {g['tipo_iva']} != importe bruto del tipo")
            debe(q2(lin * pct / 100) == gi["dto"], f"dto. p.p. al {g['tipo_iva']} mal calculado")
            debe(lin - gi["dto"] == D(g["base"]), f"bruto - dto. al {g['tipo_iva']} != base del tipo")
    if pct is None:
        debe(suma_lineas == base, "suma de líneas != base")
    else:
        debe(suma_lineas == t["bruto"], "suma de líneas != suma de importes")
        debe(t["bruto"] - t["dto"] == base, "suma de importes - dto. p.p. != base")
        debe(q2(t["bruto"] * pct / 100) == t["dto"], "dto. p.p. total != suma de los dto. por tipo (redondeo)")
    debe(base + cuota == total, "base + cuota != total")
    debe(e["retencion"] is None and e["total_a_pagar"] is None, "sin retención: retencion/total_a_pagar null")
    for ln, x in zip(d["lineas"], e["lineas"]):
        if x["cantidad"] is not None:
            debe(q2(D(x["cantidad"]) * D(x["precio"])) == D(x["importe"]),
                 f"línea {x['concepto_contiene']}: cantidad x precio != importe")
        debe((x["cantidad"] is None) == (x["precio"] is None), f"cantidad/precio incompletos {x['concepto_contiene']}")
        debe(_esp(ln["primera"]).startswith(_esp(ln["contiene"])),
             f"concepto_contiene no es el comienzo del primer renglón: {ln['contiene']!r} / {ln['primera']!r}")
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
        if t["pct_dto"] is not None:
            debe(_hay_importe(fmt(g["bruto"]), esp), f"bruto al {g['tipo']} {fmt(g['bruto'])}")
            debe(_hay_importe(fmt(g["dto"]), esp), f"dto. al {g['tipo']} {fmt(g['dto'])}")
        debe(re.search(rf"(?<![\d,]){g['tipo']:.0f}\s*%", texto) is not None, f"tipo {g['tipo']:.0f} %")
    for k in ("base", "cuota", "total", "bruto", "dto"):
        if t[k] is not None and not (k in ("bruto", "dto") and t["pct_dto"] is None):
            debe(_hay_importe(fmt(t[k]), esp), f"{k} {fmt(t[k])}")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    debe(_esp(d["iban_txt"]) in esp, f"IBAN impreso {d['iban_txt']}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    debe(norm(e["forma_pago_contiene"]) in nt, f"forma de pago {e['forma_pago_contiene']}")
    for s in d.get("textos", []):
        debe(_esp(s) in esp, f"texto {s!r}")
    for s in d.get("prohibidos", []):
        debe(_esp(s) not in esp, f"texto prohibido presente {s!r}")
    correos = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", texto)
    debe(bool(correos) and all(x.endswith(".example") for x in correos), f"correos no ficticios {correos}")
    webs = re.findall(r"www\.[\w.-]+", texto)
    debe(all(w.endswith(".example") for w in webs), f"webs no ficticias {webs}")
    # NIF válidos presentes en el texto: solo los de las partes y los distractores declarados
    previstos = {e["emisor"]["nif"], e["destinatario"]["nif"]} | set(d["nifs_distractores"])
    hallados = {n for n, _, _ in buscar_nifs(texto)}
    debe(hallados <= previstos, f"NIF válidos no previstos en el texto: {sorted(hallados - previstos)}")
    debe(previstos <= hallados, f"NIF previstos no detectables en el texto: {sorted(previstos - hallados)}")
    for k, p in PATRONES_FISCALES.items():
        hay = re.search(p, texto, re.I) is not None
        if k in e["indicios_esperados"]:
            debe(hay, f"indicio esperado sin respaldo en el texto: {k}")
        else:
            debe(not hay, f"el texto contiene una particularidad no esperada: {k}")
    return fallos


def verificar_paginas(ruta: Path, e: dict, d: dict) -> list[str]:
    """Doc 3: dos páginas, todas las líneas en la página 1 y los totales solo en la página 2."""
    fallos: list[str] = []
    n = num_paginas(ruta)
    if n != (2 if d.get("dos_paginas") else 1):
        fallos.append(f"número de páginas inesperado: {n}")
    if not d.get("dos_paginas"):
        return fallos
    p1, p2 = _esp(pdftotext(ruta, 1)), _esp(pdftotext(ruta, 2))
    fmt, t = d["fmt"], d["t"]
    for ln in d["lineas"]:
        if not _hay_importe(fmt(ln["importe"]), p1):
            fallos.append(f"línea fuera de la página 1: {ln['contiene']}")
        if _esp(ln["primera"]) in p2:
            fallos.append(f"línea repetida en la página 2: {ln['contiene']}")
    for k in ("base", "cuota", "total"):
        if not _hay_importe(fmt(t[k]), p2):
            fallos.append(f"{k} no está en la página 2")
    for s in ("base imponible", "total factura", "i.v.a."):
        if s in p1:
            fallos.append(f"la página 1 contiene '{s}'")
    if d.get("_hueco_p1", 999) > 120:
        fallos.append(f"la tabla no llena la página 1 (quedan {d['_hueco_p1']:.0f} pt libres)")
    return fallos


def nifs_otros_corpus() -> set[str]:
    """NIF válidos presentes en los JSON y en el texto de los PDF de tests/corpus* (salvo los de esta familia)."""
    nifs: set[str] = set()
    for carpeta in sorted((RAIZ / "tests").glob("corpus*")):
        for f in sorted(carpeta.rglob("*")):
            if f.name.startswith(PREFIJO) or f.suffix not in (".json", ".pdf"):
                continue
            try:
                texto = f.read_text(encoding="utf-8") if f.suffix == ".json" else pdftotext(f)
            except (OSError, ValueError, subprocess.CalledProcessError):
                continue
            nifs |= {n for n, _, _ in buscar_nifs(texto)}
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
        fallos = coherencia(e, d)
        for parte in ("emisor", "destinatario"):
            n = e[parte]["nif"]
            if vistos.get(n, e[parte]["nombre"]) != e[parte]["nombre"]:
                fallos.append(f"NIF {n} repetido con otro nombre en esta familia")
            vistos[n] = e[parte]["nombre"]
        for n in {e["emisor"]["nif"], e["destinatario"]["nif"]} | set(d["nifs_distractores"]):
            if n in otros:
                fallos.append(f"NIF {n} ya usado en otro corpus")
        d["render"](ruta, d)
        fallos += verificar_texto(pdftotext(ruta), e, d)
        fallos += verificar_paginas(ruta, e, d)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total_fallos += len(fallos)
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}" + (f"  -> {fallos}" if fallos else ""))
    print(f"{len(DOCUMENTOS)} documentos en {SALIDA.relative_to(RAIZ)}; fallos: {total_fallos}")
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
