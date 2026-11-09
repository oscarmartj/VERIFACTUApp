"""Corpus SINTÉTICO «held-out» de facturas propias para la evaluación final del extractor.

Seis maquetaciones deliberadamente alejadas de las plantillas habituales (y de los corpus erp/obra/profesionales):

  1. Factura de compañía de servicios (telecomunicaciones) con caja-resumen grande, 2 páginas, emisor solo en el pie.
  2. Factura completa de hotel (folio con fechas por línea, IVA 10 % + 21 %, cliente con NIF, huésped distinto).
  3. Factura bilingüe español/inglés a dos columnas («Factura / Invoice»), tamaño Letter, IRPF 15 %.
  4. Factura con el emisor a la DERECHA y el cliente a la IZQUIERDA sin etiqueta (ventana de sobre), Dto. %, IRPF 1 %.
  5. Factura de alquiler de local con el bloque de totales ARRIBA (fila de cabecera y valores debajo), retención 19 %.
  6. Factura de transporte de viajeros con tabla «Ud. | Descripción | P.U. | Total», IVA 10 % y totales en una frase.

Todas son facturas PROPIAS de la empresa usuaria (la empresa es el emisor). TODOS LOS DATOS SON FICTICIOS:
razones sociales, nombres de persona, domicilios, teléfonos (900 000 000…), correos (dominio .example), números de
registro, referencias e IBAN (ES00… / ES12 3456…, claramente falsos). Los NIF/CIF tienen dígito o letra de control
válidos (app.nif.completar_cif / letra del DNI) pero no se corresponden intencionadamente con ninguna persona o
entidad real.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/holdout.py                 # genera PDF + JSON y verifica con pdftotext -layout
    python scripts/corpus/holdout.py --sin-verificar

Determinista: semilla fija (no se usa aleatoriedad en los datos) y canvas de reportlab en modo «invariant».
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

SALIDA = RAIZ / "tests" / "corpus_holdout"
SEMILLA = 26110906
random.seed(SEMILLA)

D = Decimal
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
GRIS = HexColor("#5F6B6D")


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def dni(n: int) -> str:
    return f"{n:08d}{LETRAS_DNI[n % 23]}"


def q2(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(x) -> str | None:
    return None if x is None else str(q2(x))


def num(x, miles=True, euro=False, decimales=2, sufijo="") -> str:
    """Formato español: 1.234,56 (miles=True) o 1234,56 (miles=False)."""
    x = D(x).quantize(D(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    ent, dec = f"{abs(x):.{decimales}f}".split(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", ".")
    s = f"{'-' if x < 0 else ''}{ent},{dec}"
    if euro:
        s += " €"
    return s + sufijo


def cant(x, decimales=0, miles=False) -> str:
    x = D(x)
    s = f"{x:.{decimales}f}"
    ent, _, dec = s.partition(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", ".")
    return ent + (f",{dec}" if dec else "")


def fecha_larga(f: date) -> str:
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"


_UNI = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
        "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno",
        "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho",
        "veintinueve"]
_DEC = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
_CEN = {1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos", 5: "quinientos", 6: "seiscientos",
        7: "setecientos", 8: "ochocientos", 9: "novecientos"}


def _menor_mil(n: int) -> str:
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    partes = [_CEN[c]] if c else []
    if r:
        if r < 30:
            partes.append(_UNI[r])
        else:
            dd, u = divmod(r, 10)
            partes.append(_DEC[dd] + (f" y {_UNI[u]}" if u else ""))
    return " ".join(partes)


def en_letras(n: int) -> str:
    if n == 0:
        return "cero"
    m, r = divmod(n, 1000)
    partes = []
    if m == 1:
        partes.append("mil")
    elif m > 1:
        partes.append(_menor_mil(m) + " mil")
    if r:
        partes.append(_menor_mil(r))
    s = " ".join(partes)
    return re.sub(r"uno$", "un", re.sub(r"veintiuno$", "veintiún", s))


def importe_en_letras(x: Decimal) -> str:
    x = q2(x)
    euros, cent = int(x), int((x - int(x)) * 100)
    s = f"{en_letras(euros)} euros"
    if cent:
        s += f" con {en_letras(cent)} céntimos"
    return s


def calcular(lineas: list[dict], pct_ret=None) -> dict:
    """Importe de línea (cantidad x precio, menos Dto. %), desglose por tipo de IVA, retención sobre la base."""
    for ln in lineas:
        if ln.get("cantidad") is not None and ln.get("precio") is not None:
            bruto = D(ln["cantidad"]) * D(ln["precio"])
            ln["importe"] = q2(bruto * (D(100) - D(ln.get("dto") or 0)) / D(100))
        else:
            ln["importe"] = q2(ln["importe"])
        ln.setdefault("iva", D("21"))
    bases: dict[Decimal, Decimal] = {}
    for ln in lineas:
        bases[D(ln["iva"])] = bases.get(D(ln["iva"]), D("0")) + ln["importe"]
    desglose = [{"tipo": t, "base": b, "cuota": q2(b * t / D(100))} for t, b in sorted(bases.items())]
    base = sum((x["base"] for x in desglose), D("0"))
    cuota = sum((x["cuota"] for x in desglose), D("0"))
    total = base + cuota
    ret = q2(base * D(pct_ret) / D(100)) if pct_ret is not None else None
    pagar = (total - ret) if ret is not None else None
    return {"desglose": desglose, "base": base, "cuota": cuota, "total": total,
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


def partir(s, fuente, tam, ancho) -> list[str]:
    return simpleSplit(s, fuente, tam, ancho)


def puntos(c, x1, x2, y, fuente, tam, color=GRIS):
    """Línea de puntos guía (dot leaders) entre x1 y x2."""
    c.setFont(fuente, tam)
    c.setFillColor(color)
    paso = c.stringWidth(". ", fuente, tam)
    n = max(0, int((x2 - x1) / paso))
    c.drawString(x1, y, ". " * n)


def nuevo_canvas(ruta: Path, tam_pagina, titulo: str, autor: str):
    c = canvas.Canvas(str(ruta), pagesize=tam_pagina, invariant=1)
    c.setTitle(titulo)
    c.setAuthor(autor)
    c.setSubject("Documento sintético de prueba (datos ficticios)")
    c.setCreator("scripts/corpus/holdout.py")
    return c


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido) — ninguno coincide con los de los demás corpus
# ----------------------------------------------------------------------------------------------
CIF_ONDALIA = completar_cif("B", "5073148")       # emisor doc 1 (telecomunicaciones)
CIF_MONCAYO = completar_cif("B", "5091427")       # cliente doc 1
CIF_PENANEGRA = completar_cif("B", "0541729")     # emisor doc 2 (hostal)
CIF_ARAPILES = completar_cif("B", "3765218")      # cliente doc 2
NIF_TRADUCTORA = dni(46271839)                    # emisora doc 3 (traductora autónoma)
CIF_QUERNAL = completar_cif("A", "0826417")       # cliente doc 3
NIF_CARPINTERO = dni(9384726)                     # emisor doc 4 (DNI con cero inicial)
CIF_VALDEPRADOS = completar_cif("B", "2461873")   # cliente doc 4
CIF_SOMOZA = completar_cif("B", "3392417")        # emisor doc 5 (arrendador)
CIF_AZABACHE = completar_cif("B", "3318475")      # cliente doc 5 (arrendataria)
CIF_GARRANDES = completar_cif("B", "0937261")     # emisor doc 6 (autocares)
CIF_ALMIAR = completar_cif("G", "0958316")        # cliente doc 6 (asociación)

IBAN_TRADUCTORA = "ES12 3456 7890 1234 5678 9012"
IBAN_CARPINTERO = "ES00 0000 1111 2222 3333 4444"
IBAN_SOMOZA = "ES12 3456 0000 1111 2222 3333"
IBAN_GARRANDES = "ES00 9999 8888 7777 6666 5555"


def con_guion_dni(n: str) -> str:
    return f"{n[:8]}-{n[8]}"


def con_guion_cif(n: str) -> str:
    return f"{n[0]}-{n[1:]}"


# ==============================================================================================
# 1. Compañía de telecomunicaciones: caja-resumen grande, 2 páginas, emisor solo en el pie
# ==============================================================================================
def doc_01() -> dict:
    d = {
        "archivo": "hold_01_telecom_caja_resumen_2pag.pdf",
        "estilo": ("Compañía de telecomunicaciones (estilo factura de suministros): marca comercial en cabecera y "
                   "razón social SOLO en el pie, gran caja-resumen 'IMPORTE TOTAL' arriba a la derecha con Nº/fecha/"
                   "periodo/contrato, bloque 'Titular del contrato' + dirección de instalación, resumen por "
                   "categorías y gráfico de evolución en pág. 1, detalle de conceptos con precio a 4 decimales en "
                   "pág. 2, línea de descuento negativa, domiciliación con cuenta enmascarada, Helvetica, A4"),
        "empresa_nif": CIF_ONDALIA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "OND-2611-004517",
        "fecha": date(2026, 11, 3),
        "fecha_txt": "03.11.2026",
        "emisor": {"nombre": "Redes Ondalia del Cierzo, S.L.", "nif": CIF_ONDALIA, "domicilio_contiene": "50197"},
        "destinatario": {"nombre": "Gestoría Moncayo Albarracín, S.L.", "nif": CIF_MONCAYO,
                         "domicilio_contiene": "50014"},
        "lineas": [
            {"concepto": "Cuota Fibra Empresa 600 Mb simétricos", "contiene": "Cuota Fibra Empresa 600 Mb",
             "cantidad": D("1"), "precio": D("39.90"), "periodo": "01.10 - 31.10"},
            {"concepto": "Líneas móviles Tarifa Pyme 30 GB", "contiene": "Líneas móviles Tarifa Pyme",
             "cantidad": D("3"), "precio": D("12.50"), "periodo": "01.10 - 31.10"},
            {"concepto": "Centralita virtual en la nube (extensiones)", "contiene": "Centralita virtual en la nube",
             "cantidad": D("5"), "precio": D("4.00"), "periodo": "01.10 - 31.10"},
            {"concepto": "Llamadas fuera de bono (42 min a numeración especial)",
             "contiene": "Llamadas fuera de bono", "importe": D("6.35"), "periodo": "octubre"},
            {"concepto": "Descuento fidelización 10 % sobre cuota de fibra", "contiene": "Descuento fidelización",
             "importe": D("-3.99"), "periodo": "01.10 - 31.10"},
        ],
        "referencias": {"contrato": "CT-77821-B"},
        "iban": None,
        "forma_pago_contiene": "Domiciliación bancaria",
        "indicios": ["descuento"],
        "textos": ["IMPORTE TOTAL", "39,9000", "12,5000", "4,0000", "ES00 **** **** **** **** 4321",
                   "C/ Sobrarbe 41", "s/ 99,76"],
        "notas": ("Factura propia (la empresa usuaria es la operadora) sin marcas VERI*FACTU: documento preparatorio. "
                  "Trampas: la razón social y el CIF del emisor solo aparecen en el pie (la cabecera muestra la marca "
                  "'ondalia'); el importe total aparece en grande en la caja-resumen separado de su etiqueta; hay "
                  "tres direcciones (fiscal del titular 50014, instalación 50015 y la del emisor 50197 en el pie); "
                  "distractores de fecha (periodo 01.10.2026-31.10.2026 y fecha de cargo 13.11.2026), Nº de cliente "
                  "0048213, importes del resumen por categorías (97,40 / 6,35 / -3,99) y del gráfico de evolución "
                  "(meses anteriores). Precios unitarios con 4 decimales (39,9000). La línea de descuento es negativa "
                  "(-3,99) y forma parte de la base. La cuenta de domiciliación está enmascarada: no hay IBAN "
                  "extraíble (iban null). Cuota 99,76 x 21 % = 20,9496 -> 20,95."),
    }
    d["t"] = calcular(d["lineas"])
    d["render"] = lambda ruta: render_01(ruta, d)
    return d


def render_01(ruta: Path, d: dict):
    W, H = A4
    t, em, de = d["t"], d["emisor"], d["destinatario"]
    AZ, CL, NA = HexColor("#0B5563"), HexColor("#E3F1F2"), HexColor("#F28C28")
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "ondalia")
    cuotas = sum((ln["importe"] for ln in d["lineas"][:3]), D("0"))
    consumos, dtos = d["lineas"][3]["importe"], d["lineas"][4]["importe"]

    def pie(pag):
        c.setStrokeColor(AZ)
        c.setLineWidth(0.6)
        c.line(40, 66, W - 40, 66)
        tx(c, 40, 55, f"{em['nombre']} · CIF {em['nif']} · Polígono Empresarial Valdecierzo, nave 14 · "
                      "50197 Zaragoza", tam=6.8, color=GRIS)
        tx(c, 40, 45, "Inscrita en el Registro Mercantil de Zaragoza, tomo 4512, folio 88, hoja Z-61734 · "
                      "Atención al cliente 900 000 000 · clientes@ondalia.example", tam=6.8, color=GRIS)
        tx(c, W - 40, 28, f"Página {pag} de 2", tam=7, color=GRIS, alin="r")

    # ---------------- página 1
    c.setFillColor(AZ)
    c.rect(0, H - 16, W, 16, fill=1, stroke=0)
    tx(c, 40, H - 66, "ondalia", "Helvetica-Bold", 32, AZ)
    c.setFillColor(NA)
    c.circle(170, H - 45, 6, fill=1, stroke=0)
    tx(c, 41, H - 82, "fibra · móvil · centralita para empresas", tam=8, color=GRIS)
    tx(c, 40, H - 120, "Su factura de octubre de 2026", "Helvetica-Bold", 14, AZ)

    x0, w, h = 318, 237, 200
    y0 = H - 36 - h
    c.setFillColor(CL)
    c.roundRect(x0, y0, w, h, 10, fill=1, stroke=0)
    c.setFillColor(AZ)
    c.roundRect(x0, y0 + h - 32, w, 32, 10, fill=1, stroke=0)
    c.rect(x0, y0 + h - 32, w, 12, fill=1, stroke=0)
    tx(c, x0 + 16, y0 + h - 21, "IMPORTE TOTAL", "Helvetica-Bold", 10, white)
    tx(c, x0 + w - 16, y0 + h - 74, num(t["total"], euro=True), "Helvetica-Bold", 30, AZ, "r")
    filas = [("Nº factura:", d["numero"]), ("Fecha de emisión:", d["fecha_txt"]),
             ("Periodo de facturación:", "01.10.2026 - 31.10.2026"), ("Fecha de cargo:", "13.11.2026"),
             ("Nº de contrato:", d["referencias"]["contrato"])]
    y = y0 + h - 104
    for k, v in filas:
        tx(c, x0 + 16, y, k, tam=8.5, color=GRIS)
        tx(c, x0 + w - 16, y, v, "Helvetica-Bold", 8.5, alin="r")
        y -= 17

    y = H - 152
    tx(c, 40, y, "TITULAR DEL CONTRATO", "Helvetica-Bold", 7.5, GRIS)
    y -= 15
    tx(c, 40, y, de["nombre"], "Helvetica-Bold", 10.5)
    y -= 13
    tx(c, 40, y, f"CIF {de['nif']}", tam=9)
    y -= 12
    tx(c, 40, y, "Paseo de la Ribera 23, local 2", tam=9)
    y -= 12
    tx(c, 40, y, "50014 Zaragoza", tam=9)
    y -= 17
    tx(c, 40, y, "Nº de cliente: 0048213", tam=8.5, color=GRIS)
    y -= 20
    tx(c, 40, y, "DIRECCIÓN DE INSTALACIÓN", "Helvetica-Bold", 7.5, GRIS)
    y -= 13
    tx(c, 40, y, "C/ Sobrarbe 41, bajo · 50015 Zaragoza", tam=9)

    # resumen por categorías
    y = H - 290
    tx(c, 40, y, "Resumen de su factura", "Helvetica-Bold", 12, AZ)
    y -= 8
    c.setStrokeColor(AZ)
    c.setLineWidth(1.2)
    c.line(40, y, W - 40, y)
    y -= 18
    for k, v in (("Cuotas fijas mensuales", cuotas), ("Consumos fuera de tarifa", consumos),
                 ("Descuentos y promociones", dtos)):
        tx(c, 52, y, k, tam=9.5)
        tx(c, W - 52, y, num(v, euro=True), tam=9.5, alin="r")
        y -= 17
    c.setStrokeColor(HexColor("#B9CFD2"))
    c.setLineWidth(0.5)
    c.line(52, y + 9, W - 52, y + 9)
    y -= 4
    for k, v in (("Base imponible", t["base"]), ("IVA 21 %", t["cuota"])):
        tx(c, 52, y, k, tam=9.5)
        tx(c, W - 52, y, num(v, euro=True), tam=9.5, alin="r")
        y -= 17
    c.setFillColor(CL)
    c.rect(40, y - 7, W - 80, 22, fill=1, stroke=0)
    tx(c, 52, y, "Total factura", "Helvetica-Bold", 11, AZ)
    tx(c, W - 52, y, num(t["total"], euro=True), "Helvetica-Bold", 11, AZ, "r")

    # gráfico de evolución (importes de meses anteriores: distractores)
    y = H - 505
    tx(c, 40, y, "Evolución de su facturación (importe total, IVA incluido)", "Helvetica-Bold", 9, AZ)
    hist = [("may", D("118.40")), ("jun", D("119.87")), ("jul", D("121.02")), ("ago", D("117.55")),
            ("sep", D("119.87")), ("oct", t["total"])]
    base_y, x = y - 112, 70
    for mes, v in hist:
        alto = float(v - D("100")) * 3.4
        c.setFillColor(NA if mes == "oct" else HexColor("#9DC3C8"))
        c.rect(x, base_y, 34, alto, fill=1, stroke=0)
        tx(c, x + 17, base_y + alto + 4, num(v), tam=7, color=GRIS, alin="c")
        tx(c, x + 17, base_y - 11, mes, tam=7.5, color=GRIS, alin="c")
        x += 58
    c.setStrokeColor(GRIS)
    c.setLineWidth(0.4)
    c.line(60, base_y, 60 + 58 * 6, base_y)

    # forma de pago
    y = H - 690
    c.setStrokeColor(AZ)
    c.setLineWidth(0.8)
    c.roundRect(40, y - 52, W - 80, 66, 6, fill=0, stroke=1)
    tx(c, 52, y, "Forma de pago: Domiciliación bancaria", "Helvetica-Bold", 9)
    tx(c, 52, y - 15, "Cuenta de cargo: ES00 **** **** **** **** 4321", tam=9)
    tx(c, 52, y - 30, "Le cargaremos el importe en su cuenta a partir del 13.11.2026.", tam=8.5, color=GRIS)
    tx(c, 52, y - 44, "El detalle de los conceptos facturados figura en la página 2.", tam=8.5, color=GRIS)
    pie(1)
    c.showPage()

    # ---------------- página 2: detalle
    tx(c, 40, H - 50, "ondalia", "Helvetica-Bold", 16, AZ)
    tx(c, W - 40, H - 46, f"Factura {d['numero']} · {d['fecha_txt']}", tam=8.5, color=GRIS, alin="r")
    tx(c, 40, H - 90, "Detalle de su factura", "Helvetica-Bold", 13, AZ)
    tx(c, 40, H - 106, f"Periodo de facturación 01.10.2026 - 31.10.2026 · Contrato {d['referencias']['contrato']}",
       tam=8.5, color=GRIS)
    y = H - 138
    c.setFillColor(AZ)
    c.rect(40, y - 6, W - 80, 20, fill=1, stroke=0)
    cols = {"concepto": 48, "periodo": 300, "uds": 395, "precio": 478, "importe": W - 48}
    tx(c, cols["concepto"], y, "Concepto", "Helvetica-Bold", 8.5, white)
    tx(c, cols["periodo"], y, "Periodo", "Helvetica-Bold", 8.5, white)
    tx(c, cols["uds"], y, "Unidades", "Helvetica-Bold", 8.5, white, "r")
    tx(c, cols["precio"], y, "Precio unitario", "Helvetica-Bold", 8.5, white, "r")
    tx(c, cols["importe"], y, "Importe", "Helvetica-Bold", 8.5, white, "r")
    y -= 24
    for ln in d["lineas"]:
        trozos = partir(ln["concepto"], "Helvetica", 9, 240)
        tx(c, cols["concepto"], y, trozos[0], tam=9)
        tx(c, cols["periodo"], y, ln["periodo"], tam=8.5, color=GRIS)
        if ln.get("cantidad") is not None:
            tx(c, cols["uds"], y, cant(ln["cantidad"]), tam=9, alin="r")
            tx(c, cols["precio"], y, num(ln["precio"], decimales=4, euro=True), tam=9, alin="r")
        tx(c, cols["importe"], y, num(ln["importe"], euro=True), tam=9, alin="r")
        for extra in trozos[1:]:
            y -= 11
            tx(c, cols["concepto"], y, extra, tam=9)
        c.setStrokeColor(HexColor("#D5E3E5"))
        c.setLineWidth(0.4)
        c.line(40, y - 7, W - 40, y - 7)
        y -= 20
    y -= 4
    for k, v, f in (("Total conceptos (base imponible)", t["base"], "Helvetica"),
                    (f"IVA 21 % s/ {num(t['base'], euro=True)}", t["cuota"], "Helvetica"),
                    ("Total factura", t["total"], "Helvetica-Bold")):
        tx(c, 300, y, k, f, 9.5)
        tx(c, cols["importe"], y, num(v, euro=True), f, 9.5, alin="r")
        y -= 16

    y -= 26
    tx(c, 40, y, "Detalle de consumo por línea móvil", "Helvetica-Bold", 10, AZ)
    y -= 18
    for i, (a, b, cc, dd) in enumerate([("Línea", "Datos", "Voz", "Fuera de bono"),
                                        ("600 000 101", "12,4 GB", "310 min", "0,00 €"),
                                        ("600 000 102", "3,1 GB", "95 min", num(d["lineas"][3]["importe"], euro=True)),
                                        ("600 000 103", "0,8 GB", "12 min", "0,00 €")]):
        f = "Helvetica-Bold" if i == 0 else "Helvetica"
        tx(c, 48, y, a, f, 8.5)
        tx(c, 200, y, b, f, 8.5, alin="r")
        tx(c, 290, y, cc, f, 8.5, alin="r")
        tx(c, 400, y, dd, f, 8.5, alin="r")
        y -= 14
    y -= 16
    parrafo(c, 40, y, "Las cuotas fijas se facturan por meses naturales. Los consumos fuera de bono corresponden al "
                      "periodo de lectura indicado. Puede consultar sus facturas anteriores en el área de clientes.",
            W - 80, tam=7.5, color=GRIS)
    pie(2)
    c.save()


# ==============================================================================================
# 2. Hostal: factura completa con folio por fechas, IVA 10 % + 21 %, huésped distinto del cliente
# ==============================================================================================
def doc_02() -> dict:
    f = date(2026, 11, 7)
    d = {
        "archivo": "hold_02_hostal_folio_iva10_21.pdf",
        "estilo": ("Hostal (factura completa de hostelería): cabecera centrada con nombre comercial + razón social, "
                   "'Facturar a:' a la izquierda, bloque FACTURA a la derecha con etiquetas sin separador (Nº Fra., "
                   "Fecha en letra, habitación, llegada/salida, huésped, reserva, pedido), folio con fecha por línea y "
                   "columna % IVA, cuadro de desglose 10 %/21 % con total por tipo, sello PAGADO, Times, A4"),
        "empresa_nif": CIF_PENANEGRA,
        "tipo_documento": "factura",
        "situacion_esperada": "emitida_propia",
        "numero": "H26/03187",
        "fecha": f,
        "fecha_txt": fecha_larga(f),
        "emisor": {"nombre": "Alojamientos Peñanegra del Tormes, S.L.", "nif": CIF_PENANEGRA,
                   "domicilio_contiene": "05635"},
        "destinatario": {"nombre": "Ingeniería Térmica Arapiles Norte, S.L.", "nif": CIF_ARAPILES,
                         "domicilio_contiene": "37006"},
        "lineas": [
            {"fecha": "04/11/2026", "concepto": "Alojamiento habitación doble uso individual",
             "contiene": "Alojamiento habitación doble", "cantidad": D("3"), "precio": D("78.00"), "iva": D("10")},
            {"fecha": "04/11/2026", "concepto": "Desayuno buffet", "contiene": "Desayuno buffet",
             "cantidad": D("3"), "precio": D("9.50"), "iva": D("10")},
            {"fecha": "05/11/2026", "concepto": "Cena en restaurante (menú del día)", "contiene": "Cena en restaurante",
             "cantidad": D("1"), "precio": D("26.40"), "iva": D("10")},
            {"fecha": "06/11/2026", "concepto": "Servicio de lavandería y planchado",
             "contiene": "Servicio de lavandería", "cantidad": D("1"), "precio": D("14.00"), "iva": D("21")},
            {"fecha": "06/11/2026", "concepto": "Alquiler sala de reuniones Almanzor (media jornada)",
             "contiene": "Alquiler sala de reuniones", "cantidad": D("1"), "precio": D("120.00"), "iva": D("21")},
        ],
        "referencias": {"pedido": "PO-4471/26"},
        "iban": None,
        "forma_pago_contiene": "Tarjeta",
        "indicios": [],
        # el sello «PAGADO» va girado: pdftotext lo devuelve troceado y no se comprueba como texto
        "textos": ["Martín Robledo Gaitán", "RSV-88412", "317,79", "162,14", "Tarjeta de crédito VISA"],
        "notas": ("Factura completa propia del hostal, ya entregada y cobrada con tarjeta al hacer el check-out (sello "
                  "PAGADO): se registra como emitida_propia; sin firma ni QR el clasificador solo puede proponer "
                  "pendiente_confirmar, que el evaluador acepta. Dos tipos de IVA: 10 % (alojamiento, desayuno, cena: "
                  "base 288,90, cuota 28,89) y 21 % (lavandería y sala de reuniones: base 134,00, cuota 28,14). "
                  "Trampas: fecha de factura en letra ('7 de noviembre de 2026') frente a fechas de línea 04-06/11, "
                  "llegada 04/11 y salida 07/11 en formato numérico; huésped (persona) distinto del cliente "
                  "(empresa); nº de reserva y habitación como distractores; totales por tipo IVA incluido (317,79 y "
                  "162,14) en el cuadro de desglose; etiquetas sin separador ('Nº Fra.  H26/03187')."),
    }
    d["t"] = calcular(d["lineas"])
    d["render"] = lambda ruta: render_02(ruta, d)
    return d


def render_02(ruta: Path, d: dict):
    W, H = A4
    t, em, de = d["t"], d["emisor"], d["destinatario"]
    VE, ORO, CREMA = HexColor("#2F4A3A"), HexColor("#9C7A3C"), HexColor("#F6F1E6")
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "Hostal Cumbres de Peñanegra")

    tx(c, W / 2, H - 58, "HOSTAL CUMBRES DE PEÑANEGRA", "Times-Bold", 20, VE, "c")
    tx(c, W / 2, H - 74, "Hoyos del Collado · Sierra de Gredos", "Times-Italic", 10.5, ORO, "c")
    tx(c, W / 2, H - 92, f"{em['nombre']} · C.I.F. {em['nif']}", "Times-Roman", 9.5, alin="c")
    tx(c, W / 2, H - 104, "Camino del Puerto 12 · 05635 Hoyos del Collado (Ávila) · Tel. 920 000 000 · "
                          "reservas@cumbres-penanegra.example", "Times-Roman", 9, alin="c")
    c.setStrokeColor(ORO)
    c.setLineWidth(1.2)
    c.line(40, H - 116, W - 40, H - 116)
    c.setLineWidth(0.4)
    c.line(40, H - 119, W - 40, H - 119)

    # Facturar a (izquierda)
    y = H - 148
    tx(c, 44, y, "Facturar a:", "Times-Bold", 10, ORO)
    y -= 16
    tx(c, 44, y, de["nombre"], "Times-Bold", 11.5)
    y -= 14
    tx(c, 44, y, f"C.I.F.: {de['nif']}", "Times-Roman", 10.5)
    y -= 14
    tx(c, 44, y, "Avenida de Portugal 118, 3º", "Times-Roman", 10.5)
    y -= 14
    tx(c, 44, y, "37006 Salamanca", "Times-Roman", 10.5)
    c.setStrokeColor(HexColor("#C9B793"))
    c.setLineWidth(0.6)
    c.rect(36, y - 12, 262, H - 136 - (y - 12), fill=0, stroke=1)

    # FACTURA (derecha), etiqueta y valor sin separador
    x1, x2 = 330, 410
    y = H - 150
    tx(c, x1, y, "FACTURA", "Times-Bold", 16, VE)
    y -= 20
    datos = [("Nº Fra.", d["numero"]), ("Fecha", d["fecha_txt"]), ("Habitación", "214"),
             ("Llegada", "04/11/2026"), ("Salida", "07/11/2026"), ("Huésped", "Martín Robledo Gaitán"),
             ("Nº reserva", "RSV-88412"), ("Su pedido", d["referencias"]["pedido"])]
    for k, v in datos:
        tx(c, x1, y, k, "Times-Bold" if k in ("Nº Fra.", "Fecha") else "Times-Roman", 10)
        tx(c, x2, y, v, "Times-Bold" if k in ("Nº Fra.", "Fecha") else "Times-Roman", 10)
        y -= 13.5

    # folio
    y = H - 316
    cols = {"fecha": 44, "concepto": 112, "uds": 372, "precio": 430, "iva": 476, "importe": W - 44}
    c.setFillColor(CREMA)
    c.rect(36, y - 6, W - 72, 20, fill=1, stroke=0)
    for k, s, al in (("fecha", "Fecha", "l"), ("concepto", "Concepto", "l"), ("uds", "Uds.", "r"),
                     ("precio", "Precio", "r"), ("iva", "% IVA", "r"), ("importe", "Importe", "r")):
        tx(c, cols[k], y, s, "Times-Bold", 10, VE, al)
    y -= 22
    for ln in d["lineas"]:
        tx(c, cols["fecha"], y, ln["fecha"], "Times-Roman", 10)
        tx(c, cols["concepto"], y, ln["concepto"], "Times-Roman", 10)
        tx(c, cols["uds"], y, cant(ln["cantidad"]), "Times-Roman", 10, alin="r")
        tx(c, cols["precio"], y, num(ln["precio"]), "Times-Roman", 10, alin="r")
        tx(c, cols["iva"], y, cant(ln["iva"]), "Times-Roman", 10, alin="r")
        tx(c, cols["importe"], y, num(ln["importe"]), "Times-Roman", 10, alin="r")
        y -= 17
    c.setStrokeColor(ORO)
    c.setLineWidth(0.6)
    c.line(36, y + 6, W - 36, y + 6)

    # desglose por tipos (con total por tipo IVA incluido: distractor)
    y -= 22
    tx(c, 44, y, "Desglose de impuestos", "Times-Bold", 10.5, VE)
    y -= 18
    dc = {"tipo": 52, "base": 172, "cuota": 246, "total": 318}
    for k, s in (("tipo", "Tipo IVA"), ("base", "Base imponible"), ("cuota", "Cuota IVA"), ("total", "Total")):
        tx(c, dc[k], y, s, "Times-Bold", 9.5, alin="l" if k == "tipo" else "r")
    y -= 14
    for g in t["desglose"]:
        tx(c, dc["tipo"], y, f"{cant(g['tipo'])} %", "Times-Roman", 10)
        tx(c, dc["base"], y, num(g["base"]), "Times-Roman", 10, alin="r")
        tx(c, dc["cuota"], y, num(g["cuota"]), "Times-Roman", 10, alin="r")
        tx(c, dc["total"], y, num(g["base"] + g["cuota"]), "Times-Roman", 10, alin="r")
        y -= 14

    # totales (derecha)
    ty = y + 14 * (len(t["desglose"]) + 1) + 18
    xl = 362
    for k, v, f, tam in (("Total base imponible", t["base"], "Times-Roman", 10.5),
                         ("Total cuotas IVA", t["cuota"], "Times-Roman", 10.5),
                         ("TOTAL FACTURA (EUR)", t["total"], "Times-Bold", 12)):
        tx(c, xl, ty, k, f, tam)
        tx(c, W - 44, ty, num(v), f, tam, alin="r")
        ty -= 18
    c.setStrokeColor(VE)
    c.setLineWidth(0.8)
    c.rect(xl - 8, ty + 11, W - 44 - xl + 16, 22, fill=0, stroke=1)

    # forma de pago y sello
    y -= 30
    tx(c, 44, y, "Forma de pago: Tarjeta de crédito VISA **** **** **** 1234", "Times-Roman", 10.5)
    y -= 14
    tx(c, 44, y, "Cobrado en recepción el 07/11/2026 a las 11:42 h.", "Times-Roman", 10.5)
    c.saveState()
    c.translate(430, y - 30)
    c.rotate(14)
    c.setStrokeColor(HexColor("#B03A2E"))
    c.setLineWidth(2)
    c.roundRect(-58, -14, 116, 34, 6, fill=0, stroke=1)
    tx(c, 0, -3, "PAGADO", "Times-Bold", 20, HexColor("#B03A2E"), "c")
    c.restoreState()

    y -= 70
    parrafo(c, 44, y, "Gracias por su estancia. Los precios incluyen el servicio. Establecimiento inscrito en el "
                      "registro de turismo con el nº HS-AV-0123 (ficticio).", W - 88, "Times-Italic", 9.5, color=GRIS)
    tx(c, W / 2, 40, f"{em['nombre']} · Inscrita en el Registro Mercantil de Ávila, tomo 312, folio 45, hoja "
                     "AV-7731", "Times-Roman", 7.5, GRIS, "c")
    c.save()


# ==============================================================================================
# 3. Traductora autónoma: factura bilingüe español / inglés a dos columnas, Letter, IRPF 15 %
# ==============================================================================================
def doc_03() -> dict:
    d = {
        "archivo": "hold_03_bilingue_es_en_letter_irpf15.pdf",
        "estilo": ("Factura bilingüe español/inglés ('FACTURA / INVOICE'): emisor y cliente en dos columnas "
                   "('Emisor / From', 'Cliente / Bill to'), fila de 4 celdas etiqueta-sobre-valor bilingüe, tabla con "
                   "cabecera en dos líneas (ES arriba, EN abajo) y conceptos bilingües en dos líneas, cantidades con "
                   "separador de miles (5.120 palabras), totales bilingües apilados con IRPF 15 %, condiciones en "
                   "dos columnas ES | EN, fecha dd-mm-aa, CIF del cliente con prefijo ES, Helvetica, US Letter"),
        "empresa_nif": NIF_TRADUCTORA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "IC-2026-0119",
        "fecha": date(2026, 10, 30),
        "fecha_txt": "30-10-26",
        "emisor": {"nombre": "Irene Castañar Wolff", "nif": NIF_TRADUCTORA, "domicilio_contiene": "08011"},
        "destinatario": {"nombre": "Laboratorios Quernal Biofarma, S.A.", "nif": CIF_QUERNAL,
                         "domicilio_contiene": "08290"},
        "lineas": [
            {"concepto": "Traducción al español del manual de usuario del analizador",
             "en": "Spanish translation of the analyser user manual", "contiene": "Traducción al español del manual",
             "cantidad": D("5120"), "unidad": "palabras / words", "precio": D("0.09")},
            {"concepto": "Revisión bilingüe del etiquetado y prospecto",
             "en": "Bilingual proofreading of labelling and leaflet", "contiene": "Revisión bilingüe del etiquetado",
             "cantidad": D("3.5"), "unidad": "horas / hours", "precio": D("35.00")},
            {"concepto": "Traducción jurada del certificado de análisis",
             "en": "Sworn translation of the certificate of analysis", "contiene": "Traducción jurada del certificado",
             "cantidad": D("1"), "unidad": "documento / document", "precio": D("95.00")},
        ],
        "referencias": {"pedido": "4500219873", "proyecto": "PRJ-QB-0457"},
        "iban": IBAN_TRADUCTORA.replace(" ", ""),
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "textos": ["FACTURA / INVOICE", "5.120", "3,50", "29-11-26", "Bill to"],
        "notas": ("Factura propia de una traductora autónoma (persona física, DNI) sin marcas VERI*FACTU: documento "
                  "preparatorio. Etiquetas y conceptos en español e inglés; el cliente lleva el CIF con prefijo "
                  "'ES' (ESA0826417…). Trampas: la cantidad '5.120' (palabras) usa punto de miles y no es 5,12; "
                  "precio por palabra 0,09; fecha corta dd-mm-aa (30-10-26) y vencimiento 29-11-26 como distractor; "
                  "nº de pedido del cliente (4500219873) y proyecto (PRJ-QB-0457) en celdas etiqueta-sobre-valor; "
                  "totales con etiquetas bilingües. IVA 678,30 x 21 % = 142,443 -> 142,44; IRPF 15 % = 101,745 -> "
                  "101,75 (half-up); total factura 820,74 frente a total a pagar 718,99."),
    }
    d["t"] = calcular(d["lineas"], pct_ret=D("15"))
    d["render"] = lambda ruta: render_03(ruta, d)
    return d


def render_03(ruta: Path, d: dict):
    W, H = letter
    t, em, de = d["t"], d["emisor"], d["destinatario"]
    TINTA, SUAVE, LINEA = HexColor("#1F2A44"), HexColor("#EEF1F6"), HexColor("#C5CCD8")
    c = nuevo_canvas(ruta, letter, f"Factura / Invoice {d['numero']}", em["nombre"])
    M = 46

    tx(c, M, H - 58, em["nombre"], "Helvetica-Bold", 17, TINTA)
    tx(c, M, H - 73, "Traducción e interpretación · Translation & Interpreting", tam=9, color=GRIS)
    tx(c, W - M, H - 60, "FACTURA / INVOICE", "Helvetica-Bold", 19, TINTA, "r")
    c.setStrokeColor(TINTA)
    c.setLineWidth(1.5)
    c.line(M, H - 86, W - M, H - 86)

    # dos columnas: emisor | cliente
    colw = (W - 2 * M - 20) / 2
    xs = (M, M + colw + 20)
    top = H - 104
    for x, titulo in zip(xs, ("Emisor / From", "Cliente / Bill to")):
        c.setFillColor(SUAVE)
        c.rect(x, top - 96, colw, 96, fill=1, stroke=0)
        tx(c, x + 10, top - 15, titulo, "Helvetica-Bold", 8.5, GRIS)
    y = top - 32
    for s, f in ((em["nombre"], "Helvetica-Bold"), (f"NIF / Tax ID: {em['nif']}", "Helvetica"),
                 ("C/ de la Diputació 214, 4º 2ª", "Helvetica"), ("08011 Barcelona", "Helvetica"),
                 ("irene.castanar@traduccion.example", "Helvetica")):
        tx(c, xs[0] + 10, y, s, f, 9.5)
        y -= 13
    y = top - 32
    for s, f in ((de["nombre"], "Helvetica-Bold"), (f"CIF / VAT No.: ES{de['nif']}", "Helvetica"),
                 ("Avinguda de la Recerca 17", "Helvetica"), ("08290 Cerdanyola del Vallès (Barcelona)", "Helvetica"),
                 ("Attn.: Dpto. de Compras / Purchasing", "Helvetica")):
        tx(c, xs[1] + 10, y, s, f, 9.5)
        y -= 13

    # fila de 4 celdas etiqueta sobre valor
    y = top - 122
    celdas = [("Nº de factura / Invoice No.", d["numero"]), ("Fecha / Date", d["fecha_txt"]),
              ("Pedido / PO No.", d["referencias"]["pedido"]), ("Proyecto / Project", d["referencias"]["proyecto"])]
    cw = (W - 2 * M) / 4
    c.setStrokeColor(LINEA)
    c.setLineWidth(0.6)
    for i, (k, v) in enumerate(celdas):
        x = M + i * cw
        c.rect(x, y - 30, cw, 40, fill=0, stroke=1)
        tx(c, x + 8, y - 2, k, tam=7.5, color=GRIS)
        tx(c, x + 8, y - 20, v, "Helvetica-Bold", 10.5, TINTA)

    # tabla: cabecera en dos líneas
    y = top - 186
    cols = {"desc": M + 8, "cant": 352, "unidad": 362, "precio": 498, "importe": W - M - 8}
    c.setFillColor(TINTA)
    c.rect(M, y - 18, W - 2 * M, 32, fill=1, stroke=0)
    for k, es, en, al in (("desc", "Descripción", "Description", "l"), ("cant", "Cant.", "Qty", "r"),
                          ("unidad", "Unidad", "Unit", "l"), ("precio", "Precio", "Rate", "r"),
                          ("importe", "Importe", "Amount", "r")):
        tx(c, cols[k], y + 2, es, "Helvetica-Bold", 8.5, white, al)
        tx(c, cols[k], y - 10, en, "Helvetica-Oblique", 8, HexColor("#C9D3E6"), al)
    y -= 36
    for ln in d["lineas"]:
        tx(c, cols["desc"], y, ln["concepto"], tam=9.2)
        tx(c, cols["desc"], y - 12, ln["en"], "Helvetica-Oblique", 8.5, GRIS)
        miles = ln["cantidad"] >= 1000
        dec = 0 if ln["cantidad"] == int(ln["cantidad"]) else 2
        tx(c, cols["cant"], y, cant(ln["cantidad"], dec, miles), tam=9.2, alin="r")
        tx(c, cols["unidad"], y, ln["unidad"], tam=8.2, color=GRIS)
        tx(c, cols["precio"], y, num(ln["precio"], euro=True), tam=9.2, alin="r")
        tx(c, cols["importe"], y, num(ln["importe"], euro=True), tam=9.2, alin="r")
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.5)
        c.line(M, y - 20, W - M, y - 20)
        y -= 36

    # totales bilingües apilados (derecha)
    y -= 6
    xl, xr = 318, W - M - 8
    filas = [("Base imponible / Taxable base", t["base"], "Helvetica"),
             ("IVA 21 % / VAT 21 %", t["cuota"], "Helvetica"),
             ("Total factura / Invoice total", t["total"], "Helvetica-Bold"),
             ("Retención IRPF 15 % / Withholding tax 15 %", -t["ret"], "Helvetica"),
             ("Total a pagar / Amount due", t["pagar"], "Helvetica-Bold")]
    for k, v, f in filas:
        if k.startswith("Total a pagar"):
            c.setFillColor(SUAVE)
            c.rect(xl - 8, y - 6, W - M - xl + 8, 19, fill=1, stroke=0)
        tx(c, xl, y, k, f, 9)
        tx(c, xr, y, num(v, euro=True), f, 9.5, alin="r")
        y -= 19

    # forma de pago (izquierda, a la altura de los totales)
    py = y + 19 * len(filas)
    tx(c, M, py, "Forma de pago / Payment method:", "Helvetica-Bold", 8.5)
    tx(c, M, py - 13, "Transferencia bancaria / Bank transfer", tam=9)
    tx(c, M, py - 30, f"IBAN: {IBAN_TRADUCTORA}", tam=9)
    tx(c, M, py - 43, "BIC: ZZZZESZZXXX", tam=9)
    tx(c, M, py - 60, "Vencimiento / Due date: 29-11-26", tam=9)

    # condiciones en dos columnas ES | EN
    y -= 30
    c.setStrokeColor(LINEA)
    c.line(M, y + 10, W - M, y + 10)
    tx(c, xs[0], y - 4, "Condiciones", "Helvetica-Bold", 8.5, TINTA)
    tx(c, xs[1], y - 4, "Terms", "Helvetica-Bold", 8.5, TINTA)
    parrafo(c, xs[0], y - 18, "Pago a 30 días desde la fecha de factura. Se aplica una retención del 15 % a cuenta del "
                              "IRPF de la emisora, que el cliente ingresará en la Agencia Tributaria. Indique el número "
                              "de factura en la referencia de la transferencia.", colw, tam=8, color=GRIS)
    parrafo(c, xs[1], y - 18, "Payment within 30 days of the invoice date. A 15% Spanish personal income tax "
                              "withholding applies; the client pays it to the Spanish Tax Agency on the issuer's "
                              "behalf. Please quote the invoice number when making the transfer.", colw,
            "Helvetica-Oblique", 8, color=GRIS)
    tx(c, W / 2, 34, "Documento bilingüe: en caso de discrepancia prevalece la versión en español / "
                     "Bilingual document: the Spanish version prevails.", tam=7, color=GRIS, alin="c")
    c.save()


# ==============================================================================================
# 4. Carpintero en módulos: emisor a la DERECHA, cliente a la IZQUIERDA sin etiqueta, Dto. %, IRPF 1 %
# ==============================================================================================
def doc_04() -> dict:
    d = {
        "archivo": "hold_04_emisor_derecha_carpinteria_irpf1.pdf",
        "estilo": ("Carpintero autónomo: bloque del emisor alineado a la DERECHA arriba (nombre comercial + titular + "
                   "DNI con guion), cliente a la IZQUIERDA en ventana de sobre SIN etiqueta y en mayúsculas, datos "
                   "con separador '. -' (Núm. - / Fecha. - / Obra. -), tabla con rejilla Código|Descripción|Cant.|"
                   "Precio|Dto. %|Importe y concepto multilínea, totales apilados con puntos guía, I.V.A. 21 % y "
                   "retención IRPF 1 % (estimación objetiva), Times, A4"),
        "empresa_nif": NIF_CARPINTERO,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "2026-0112",
        "fecha": date(2026, 10, 21),
        "fecha_txt": "21/10/2026",
        "emisor": {"nombre": "Teodoro Arrabal Quintanilla", "nif": NIF_CARPINTERO, "domicilio_contiene": "24400"},
        "destinatario": {"nombre": "Hostelería Valdeprados del Bierzo, S.L.", "nif": CIF_VALDEPRADOS,
                         "domicilio_contiene": "24500"},
        "lineas": [
            {"codigo": "PU-07", "concepto": "Puerta de paso lacada en blanco 72,5 cm con herrajes de latón",
             "contiene": "Puerta de paso lacada", "cantidad": D("4"), "precio": D("185.00"), "dto": D("5")},
            {"codigo": "RV-12", "concepto": "Revestimiento de barra en madera de roble macizo barnizado, incluida "
                                            "colocación y remates", "contiene": "Revestimiento de barra en madera",
             "cantidad": D("6.40"), "precio": D("96.50"), "dto": D("0")},
            {"codigo": "MO-01", "concepto": "Mano de obra de montaje y ajuste en obra",
             "contiene": "Mano de obra de montaje", "cantidad": D("9"), "precio": D("32.00"), "dto": D("0")},
        ],
        "referencias": {"obra": "Reforma del comedor de planta baja"},
        "iban": IBAN_CARPINTERO.replace(" ", ""),
        "forma_pago_contiene": "Transferencia",
        "indicios": ["descuento"],
        "textos": ["CARPINTERÍA ARRABAL", "Dto. %", "6,40", "P-2026-031", "Líquido a pagar"],
        "notas": ("Factura propia de un carpintero autónomo (persona física, DNI con cero inicial impreso con guion "
                  "'09384726-…') sin marcas VERI*FACTU: documento preparatorio. Trampas: el emisor está a la "
                  "derecha y el cliente a la izquierda sin ninguna etiqueta (ventana de sobre, en mayúsculas); el "
                  "nombre comercial 'CARPINTERÍA ARRABAL' precede al nombre del titular; mención a un presupuesto "
                  "aceptado (nº P-2026-031, 02/09/2026) que no convierte el documento en presupuesto; separador "
                  "'. -'; columna Dto. % (5 % en la primera línea: 4 x 185,00 = 740,00 - 5 % = 703,00; el precio "
                  "esperado es el unitario sin descuento); cantidad decimal 6,40 m; totales con puntos guía. "
                  "Retención IRPF 1 % por actividad en estimación objetiva: 1.608,60 x 1 % = 16,086 -> 16,09; "
                  "IVA 337,806 -> 337,81; 'Líquido a pagar' 1.930,32."),
    }
    d["t"] = calcular(d["lineas"], pct_ret=D("1"))
    d["render"] = lambda ruta: render_04(ruta, d)
    return d


def render_04(ruta: Path, d: dict):
    W, H = A4
    t, em, de = d["t"], d["emisor"], d["destinatario"]
    MADERA = HexColor("#6B3E1E")
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "Carpintería Arrabal")
    R = W - 50

    # emisor a la derecha
    y = H - 56
    tx(c, R, y, "CARPINTERÍA ARRABAL", "Times-Bold", 17, MADERA, "r")
    y -= 15
    tx(c, R, y, "Puertas · Armarios · Mobiliario a medida", "Times-Italic", 9.5, GRIS, "r")
    y -= 17
    for s in (em["nombre"], f"N.I.F. {con_guion_dni(em['nif'])}", "C/ Herrerías, 9 - bajo", "24400 PONFERRADA (León)",
              "Tlf. 987 000 000 · taller@carpinteria-arrabal.example"):
        tx(c, R, y, s, "Times-Roman", 10, alin="r")
        y -= 12.5

    # cliente a la izquierda, sin etiqueta (ventana de sobre)
    y = H - 158
    for s, f in ((de["nombre"].upper(), "Times-Bold"), (f"C.I.F. {con_guion_cif(de['nif'])}", "Times-Roman"),
                 ("PLAZA MAYOR, 3", "Times-Roman"), ("24500 VILLAFRANCA DEL BIERZO (LEÓN)", "Times-Roman")):
        tx(c, 62, y, s, f, 10.5)
        y -= 14
    # marcas de esquina de la ventana
    c.setStrokeColor(GRIS)
    c.setLineWidth(0.5)
    for (x, yy, dx, dy) in ((50, H - 146, 1, -1), (345, H - 146, -1, -1), (50, H - 220, 1, 1), (345, H - 220, -1, 1)):
        c.line(x, yy, x + 10 * dx, yy)
        c.line(x, yy, x, yy + 10 * dy)

    # título y datos con separador ". -"
    y = H - 262
    tx(c, 50, y, "FACTURA", "Times-Bold", 20, MADERA)
    y -= 24
    tx(c, 50, y, f"Núm. - {d['numero']}", "Times-Bold", 11)
    tx(c, 210, y, f"Fecha. - {d['fecha_txt']}", "Times-Bold", 11)
    tx(c, 370, y, "Forma de pago. - Transferencia 30 días", "Times-Roman", 10.5)
    y -= 16
    tx(c, 50, y, f"Obra. - {d['referencias']['obra']}", "Times-Roman", 10.5)
    y -= 14
    tx(c, 50, y, "Trabajos según presupuesto nº P-2026-031 aceptado el 02/09/2026.", "Times-Italic", 10, GRIS)

    # tabla con rejilla
    y -= 22
    xs = [50, 100, 318, 362, 428, 470, R]
    cab = ["Código", "Descripción", "Cant.", "Precio", "Dto. %", "Importe"]
    filas = []
    for ln in d["lineas"]:
        trozos = partir(ln["concepto"], "Times-Roman", 10, xs[2] - xs[1] - 10)
        filas.append((ln, trozos))
    alto_cab = 18
    altos = [max(1, len(tz)) * 12 + 8 for _, tz in filas]
    top = y
    bottom = top - alto_cab - sum(altos) - 30
    c.setStrokeColor(black)
    c.setLineWidth(0.7)
    c.rect(xs[0], bottom, R - xs[0], top - bottom, fill=0, stroke=1)
    for x in xs[1:-1]:
        c.line(x, bottom, x, top)
    c.line(xs[0], top - alto_cab, R, top - alto_cab)
    for i, s in enumerate(cab):
        if i in (0, 1):
            tx(c, xs[i] + 5, top - 13, s, "Times-Bold", 10)
        else:
            tx(c, xs[i + 1] - 5, top - 13, s, "Times-Bold", 10, alin="r")
    y = top - alto_cab - 14
    for (ln, trozos), alto in zip(filas, altos):
        tx(c, xs[0] + 5, y, ln["codigo"], "Times-Roman", 10)
        for k, tz in enumerate(trozos):
            tx(c, xs[1] + 5, y - 12 * k, tz, "Times-Roman", 10)
        dec = 0 if ln["cantidad"] == int(ln["cantidad"]) else 2
        tx(c, xs[3] - 5, y, cant(ln["cantidad"], dec), "Times-Roman", 10, alin="r")
        tx(c, xs[4] - 5, y, num(ln["precio"]), "Times-Roman", 10, alin="r")
        tx(c, xs[5] - 5, y, cant(ln["dto"]) if ln["dto"] else "", "Times-Roman", 10, alin="r")
        tx(c, xs[6] - 5, y, num(ln["importe"]), "Times-Roman", 10, alin="r")
        y -= alto

    # totales con puntos guía
    y = bottom - 26
    xl, xv = 330, R - 4
    for k, v, f in (("Base imponible", t["base"], "Times-Roman"),
                    ("I.V.A. 21 %", t["cuota"], "Times-Roman"),
                    ("Total factura", t["total"], "Times-Bold"),
                    ("Retención I.R.P.F. 1 %", -t["ret"], "Times-Roman"),
                    ("Líquido a pagar", t["pagar"], "Times-Bold")):
        tx(c, xl, y, k, f, 11)
        ancho_k = c.stringWidth(k, f, 11)
        ancho_v = c.stringWidth(num(v), f, 11)
        puntos(c, xl + ancho_k + 6, xv - ancho_v - 6, y, "Times-Roman", 11)
        tx(c, xv, y, num(v), f, 11, alin="r")
        y -= 17

    # pago y notas
    y = bottom - 26
    tx(c, 50, y, "Datos bancarios", "Times-Bold", 10.5, MADERA)
    tx(c, 50, y - 15, f"IBAN {IBAN_CARPINTERO}", "Times-Roman", 10.5)
    tx(c, 50, y - 30, "Vencimiento: 20/11/2026", "Times-Roman", 10.5)
    parrafo(c, 50, bottom - 140, "Retención del 1 % a cuenta del IRPF por actividad en estimación objetiva "
                                 "(art. 95.6.2º del Reglamento del IRPF). El material queda en propiedad del "
                                 "carpintero hasta su completo pago.", R - 50, "Times-Italic", 9.5, color=GRIS)
    c.setStrokeColor(MADERA)
    c.setLineWidth(0.6)
    c.line(50, 52, R, 52)
    tx(c, (50 + R) / 2, 40, "Carpintería Arrabal · Ponferrada · Gracias por su confianza", "Times-Italic", 8.5,
       GRIS, "c")
    c.save()


# ==============================================================================================
# 5. Arrendador de local: totales ARRIBA (cabecera con valores debajo), retención 19 %, Courier
# ==============================================================================================
def doc_05() -> dict:
    d = {
        "archivo": "hold_05_alquiler_totales_arriba_ret19.pdf",
        "estilo": ("Factura de alquiler de local comercial emitida por una sociedad patrimonial: bloque de TOTALES "
                   "ARRIBA, justo bajo la cabecera, como tabla de 5 columnas (Base imponible | IVA 21 % | Total "
                   "factura | Retención 19 % | Total a pagar) con los valores en la fila de abajo; datos con "
                   "separador ' - '; 'Arrendatario' con domicilio fiscal distinto del inmueble arrendado; conceptos "
                   "sin cantidad ni precio; importes sin punto de miles; Courier + Helvetica, A4"),
        "empresa_nif": CIF_SOMOZA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "ALQ/2026/011",
        "fecha": date(2026, 11, 1),
        "fecha_txt": "01-11-2026",
        "emisor": {"nombre": "Patrimonial Somoza Ibarrola, S.L.", "nif": CIF_SOMOZA, "domicilio_contiene": "33012"},
        "destinatario": {"nombre": "Veterinaria Nalón Azabache, S.L.P.", "nif": CIF_AZABACHE,
                         "domicilio_contiene": "33930"},
        "lineas": [
            {"concepto": "Renta mensual noviembre 2026 del local comercial", "contiene": "Renta mensual noviembre 2026",
             "importe": D("1150.00")},
            {"concepto": "Gastos de comunidad noviembre 2026", "contiene": "Gastos de comunidad",
             "importe": D("85.00")},
            {"concepto": "Repercusión IBI 2026 (parte proporcional 1/12)", "contiene": "Repercusión IBI 2026",
             "importe": D("46.25")},
        ],
        "referencias": {"contrato": "ARR-OV-2024-03"},
        "iban": IBAN_SOMOZA.replace(" ", ""),
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "textos": ["Calle Peñavieja 21", "15-03-2024", "Arrendatario"],
        "notas": ("Factura propia de una sociedad patrimonial (arrendadora) sin marcas VERI*FACTU: documento "
                  "preparatorio. Trampas: el bloque de totales está ARRIBA, antes de las partes y de los conceptos, "
                  "como fila de cabeceras con los valores en la fila inferior; la retención (19 %, arrendamiento de "
                  "inmueble urbano, a cuenta del Impuesto sobre Sociedades) se rotula 'Retención 19 %' sin 'IRPF' y "
                  "se muestra en negativo; importes sin punto de miles (1281,25); el inmueble arrendado (Calle "
                  "Peñavieja 21, 33012 Oviedo) está junto al domicilio del emisor (Calle Peñavieja 19, 33012) y no es "
                  "el domicilio del arrendatario (33930 Langreo); fecha del contrato 15-03-2024 como distractor. "
                  "Base 1281,25 x 21 % = 269,0625 -> 269,06; retención 243,4375 -> 243,44; total a pagar 1306,87."),
    }
    d["t"] = calcular(d["lineas"], pct_ret=D("19"))
    d["render"] = lambda ruta: render_05(ruta, d)
    return d


def render_05(ruta: Path, d: dict):
    W, H = A4
    t, em, de = d["t"], d["emisor"], d["destinatario"]
    PIZ = HexColor("#333B45")
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", em["nombre"])
    M = 44

    tx(c, M, H - 56, em["nombre"].upper(), "Helvetica-Bold", 13, PIZ)
    y = H - 72
    for s in (f"NIF - {em['nif']}", "Calle Peñavieja 19, 2º", "33012 Oviedo (Asturias)",
              "administracion@somoza-ibarrola.example"):
        tx(c, M, y, s, "Courier", 9)
        y -= 11
    tx(c, W - M, H - 58, "FACTURA", "Helvetica-Bold", 22, PIZ, "r")
    tx(c, W - M, H - 78, f"Número - {d['numero']}", "Courier-Bold", 10, alin="r")
    tx(c, W - M, H - 91, f"Fecha - {d['fecha_txt']}", "Courier-Bold", 10, alin="r")

    # bloque de totales ARRIBA: fila de cabeceras + valores debajo
    top = H - 136
    cab = ["Base imponible", "IVA 21 %", "Total factura", "Retención 19 %", "Total a pagar"]
    vals = [t["base"], t["cuota"], t["total"], -t["ret"], t["pagar"]]
    cw = (W - 2 * M) / 5
    c.setFillColor(PIZ)
    c.rect(M, top - 18, W - 2 * M, 18, fill=1, stroke=0)
    c.setStrokeColor(PIZ)
    c.setLineWidth(0.8)
    c.rect(M, top - 44, W - 2 * M, 44, fill=0, stroke=1)
    for i, (k, v) in enumerate(zip(cab, vals)):
        x = M + i * cw
        if i:
            c.line(x, top - 44, x, top - 18)
        tx(c, x + cw / 2, top - 13, k, "Helvetica-Bold", 8.5, white, "c")
        tx(c, x + cw - 8, top - 36, num(v, miles=False, euro=True), "Courier-Bold" if i == 4 else "Courier", 10,
           alin="r")

    # arrendatario
    y = top - 80
    tx(c, M, y, "Arrendatario", "Helvetica-Bold", 10, PIZ)
    c.setLineWidth(0.5)
    c.line(M, y - 4, M + 250, y - 4)
    y -= 18
    for s, f in ((de["nombre"], "Courier-Bold"), (f"NIF - {de['nif']}", "Courier"),
                 ("Avenida del Nalón 27, bajo", "Courier"), ("33930 Langreo (Asturias)", "Courier")):
        tx(c, M, y, s, f, 9.5)
        y -= 12

    # inmueble y contrato (derecha)
    x2 = 320
    y = top - 80
    tx(c, x2, y, "Inmueble y contrato", "Helvetica-Bold", 10, PIZ)
    c.line(x2, y - 4, W - M, y - 4)
    y -= 18
    for s in ("Local comercial, planta baja", "Calle Peñavieja 21, 33012 Oviedo", "Ref. catastral 0000000AA0000A0001ZZ",
              f"Contrato - {d['referencias']['contrato']}", "Fecha contrato - 15-03-2024",
              "Periodo - noviembre 2026"):
        tx(c, x2, y, s, "Courier", 9)
        y -= 12

    # conceptos
    y -= 26
    c.setFillColor(HexColor("#E9ECEF"))
    c.rect(M, y - 6, W - 2 * M, 18, fill=1, stroke=0)
    tx(c, M + 8, y, "Concepto", "Helvetica-Bold", 9.5, PIZ)
    tx(c, W - M - 8, y, "Importe (EUR)", "Helvetica-Bold", 9.5, PIZ, "r")
    y -= 22
    for ln in d["lineas"]:
        tx(c, M + 8, y, ln["concepto"], "Courier", 9.5)
        tx(c, W - M - 8, y, num(ln["importe"], miles=False), "Courier", 9.5, alin="r")
        y -= 16
    c.setStrokeColor(PIZ)
    c.line(M, y + 8, W - M, y + 8)
    tx(c, W - M - 120, y - 6, "Suma conceptos", "Courier", 9.5, alin="r")
    tx(c, W - M - 8, y - 6, num(t["base"], miles=False), "Courier-Bold", 9.5, alin="r")

    # pago y notas
    y -= 48
    tx(c, M, y, "Forma de pago - Transferencia bancaria antes del día 5 del mes", "Courier", 9.5)
    tx(c, M, y - 13, f"Cuenta - {IBAN_SOMOZA}", "Courier", 9.5)
    y -= 40
    parrafo(c, M, y, "Retención del 19 % sobre la renta y gastos repercutidos, a cuenta del Impuesto sobre "
                     "Sociedades del arrendador, por arrendamiento de inmueble urbano. El arrendatario la ingresará "
                     "en la Agencia Tributaria (modelo 115) y emitirá el certificado anual correspondiente.",
            W - 2 * M, "Courier", 8.5, color=GRIS)
    tx(c, W / 2, 40, f"{em['nombre']} · Inscrita en el Registro Mercantil de Asturias, tomo 4021, folio 12, "
                     "hoja AS-50112", "Helvetica", 7, GRIS, "c")
    c.save()


# ==============================================================================================
# 6. Autocares: tabla «Ud. | Descripción | P.U. | Total», IVA 10 %, totales en frase + importe en letra
# ==============================================================================================
def doc_06() -> dict:
    d = {
        "archivo": "hold_06_autocares_ud_pu_iva10_frase.pdf",
        "estilo": ("Empresa de autocares: banda de marca de color con nombre comercial y razón social debajo, "
                   "rejilla 'Nº FACTURA | FECHA' con valores en la fila inferior, caja 'DATOS DEL CLIENTE', tabla "
                   "'Ud. | Descripción | P.U. | Total' (cantidad primero) con descripciones multilínea, IVA 10 % "
                   "(transporte de viajeros), totales en una frase de texto + importe en letra + caja 'TOTAL "
                   "FACTURA', fecha dd/mm/aa, cliente asociación (NIF G), Helvetica, A4"),
        "empresa_nif": CIF_GARRANDES,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "A-26/0348",
        "fecha": date(2026, 11, 5),
        "fecha_txt": "05/11/26",
        "emisor": {"nombre": "Viajes en Autocar Hermanos Garrandés, S.L.", "nif": CIF_GARRANDES,
                   "domicilio_contiene": "09340"},
        "destinatario": {"nombre": "Asociación Cultural Peña Viajera El Almiar", "nif": CIF_ALMIAR,
                         "domicilio_contiene": "09002"},
        "lineas": [
            {"concepto": "Servicio de autocar de 55 plazas Burgos - Santo Domingo de Silos - Covarrubias - Burgos, "
                         "excursión del 24/10/2026 (ida y vuelta, conductor incluido)",
             "contiene": "Servicio de autocar de 55 plazas", "cantidad": D("2"), "precio": D("640.00"), "iva": D("10")},
            {"concepto": "Microbús de 25 plazas para traslado de guías y material, misma fecha",
             "contiene": "Microbús de 25 plazas", "cantidad": D("1"), "precio": D("310.00"), "iva": D("10")},
            {"concepto": "Hora de espera adicional del conductor fuera del horario contratado",
             "contiene": "Hora de espera adicional", "cantidad": D("6"), "precio": D("28.50"), "iva": D("10")},
        ],
        "referencias": {"pedido": "77/2026"},
        "iban": IBAN_GARRANDES.replace(" ", ""),
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": [],
        "textos": ["P.U.", "Ud.", "MIL NOVECIENTOS TREINTA Y SIETE EUROS CON DIEZ", "24/10/2026", "DATOS DEL CLIENTE"],
        "notas": ("Factura propia de una empresa de autocares sin marcas VERI*FACTU: documento preparatorio. IVA "
                  "único del 10 % (transporte de viajeros): 1.761,00 x 10 % = 176,10; total 1.937,10. Trampas: "
                  "cabecera de tabla abreviada 'Ud. | Descripción | P.U. | Total' con la cantidad en la primera "
                  "columna; descripciones de 2-3 líneas con una fecha de servicio (24/10/2026) y guiones dentro; nº "
                  "de factura y fecha en una rejilla con las etiquetas en la fila superior y los valores debajo; "
                  "fecha corta dd/mm/aa (05/11/26); los totales solo aparecen en una frase de texto y en una caja "
                  "'TOTAL FACTURA', además del importe en letra; la palabra 'Total' es también cabecera de columna; "
                  "nombre comercial 'GARRANDÉS BUS' distinto de la razón social; cliente asociación con NIF G."),
    }
    d["t"] = calcular(d["lineas"])
    d["render"] = lambda ruta: render_06(ruta, d)
    return d


def render_06(ruta: Path, d: dict):
    W, H = A4
    t, em, de = d["t"], d["emisor"], d["destinatario"]
    ROJO, CLARO = HexColor("#A3262A"), HexColor("#F7E9E9")
    c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "Garrandés Bus")
    M = 42

    c.setFillColor(ROJO)
    c.rect(0, H - 92, W, 92, fill=1, stroke=0)
    tx(c, M, H - 50, "GARRANDÉS BUS", "Helvetica-Bold", 26, white)
    tx(c, M, H - 70, "Transporte discrecional de viajeros · Excursiones · Rutas escolares", tam=9, color=white)
    y = H - 112
    tx(c, M, y, em["nombre"], "Helvetica-Bold", 9.5)
    tx(c, M, y - 12, f"CIF {em['nif']} · Avenida de la Estación 31, 09340 Lerma (Burgos)", tam=8.5)
    tx(c, M, y - 24, "Tel. 947 000 000 · reservas@garrandes-bus.example", tam=8.5, color=GRIS)

    # rejilla Nº FACTURA | FECHA (valores debajo)
    gx, gw = W - M - 220, 220
    gy = H - 104
    c.setStrokeColor(ROJO)
    c.setLineWidth(0.8)
    c.rect(gx, gy - 44, gw, 44, fill=0, stroke=1)
    c.setFillColor(CLARO)
    c.rect(gx, gy - 16, gw, 16, fill=1, stroke=0)
    c.line(gx + gw / 2, gy - 44, gx + gw / 2, gy)
    c.line(gx, gy - 16, gx + gw, gy - 16)
    tx(c, gx + gw / 4, gy - 12, "Nº FACTURA", "Helvetica-Bold", 8, ROJO, "c")
    tx(c, gx + 3 * gw / 4, gy - 12, "FECHA", "Helvetica-Bold", 8, ROJO, "c")
    tx(c, gx + gw / 4, gy - 35, d["numero"], "Helvetica-Bold", 11.5, alin="c")
    tx(c, gx + 3 * gw / 4, gy - 35, d["fecha_txt"], "Helvetica-Bold", 11.5, alin="c")
    tx(c, gx, gy - 60, f"Su pedido nº {d['referencias']['pedido']}", tam=9)

    # datos del cliente
    y = H - 196
    c.setFillColor(CLARO)
    c.rect(M, y - 70, W - 2 * M, 84, fill=1, stroke=0)
    tx(c, M + 10, y, "DATOS DEL CLIENTE", "Helvetica-Bold", 8.5, ROJO)
    tx(c, M + 10, y - 17, de["nombre"], "Helvetica-Bold", 10.5)
    tx(c, M + 10, y - 31, f"NIF: {de['nif']}", tam=9.5)
    tx(c, M + 10, y - 45, "Calle de la Calera 11, 1º izda.", tam=9.5)
    tx(c, M + 10, y - 59, "09002 Burgos", tam=9.5)
    tx(c, 330, y - 17, "Persona de contacto: Sra. Begoña Arnáiz Lomas", tam=8.5, color=GRIS)
    tx(c, 330, y - 31, "Tel. 600 000 777", tam=8.5, color=GRIS)

    # tabla Ud. | Descripción | P.U. | Total
    y = H - 300
    xs = [M, M + 44, 400, 475, W - M]
    filas = [(ln, partir(ln["concepto"], "Helvetica", 9.5, xs[2] - xs[1] - 14)) for ln in d["lineas"]]
    altos = [len(tz) * 12 + 12 for _, tz in filas]
    top, cab_h = y, 20
    bottom = top - cab_h - sum(altos) - 10
    c.setFillColor(ROJO)
    c.rect(xs[0], top - cab_h, xs[-1] - xs[0], cab_h, fill=1, stroke=0)
    tx(c, (xs[0] + xs[1]) / 2, top - 14, "Ud.", "Helvetica-Bold", 9.5, white, "c")
    tx(c, xs[1] + 8, top - 14, "Descripción", "Helvetica-Bold", 9.5, white)
    tx(c, xs[3] - 8, top - 14, "P.U.", "Helvetica-Bold", 9.5, white, "r")
    tx(c, xs[4] - 8, top - 14, "Total", "Helvetica-Bold", 9.5, white, "r")
    c.setStrokeColor(ROJO)
    c.setLineWidth(0.6)
    c.rect(xs[0], bottom, xs[-1] - xs[0], top - bottom, fill=0, stroke=1)
    for x in xs[1:-1]:
        c.line(x, bottom, x, top - cab_h)
    y = top - cab_h - 16
    for (ln, trozos), alto in zip(filas, altos):
        tx(c, (xs[0] + xs[1]) / 2, y, cant(ln["cantidad"]), tam=9.5, alin="c")
        for k, tz in enumerate(trozos):
            tx(c, xs[1] + 8, y - 12 * k, tz, tam=9.5)
        tx(c, xs[3] - 8, y, num(ln["precio"]), tam=9.5, alin="r")
        tx(c, xs[4] - 8, y, num(ln["importe"]), tam=9.5, alin="r")
        y -= alto

    # totales en una frase + importe en letra + caja
    y = bottom - 26
    frase = (f"Base imponible {num(t['base'])} euros, más I.V.A. del 10 % ({num(t['cuota'])} euros): importe "
             f"total de la factura {num(t['total'])} euros.")
    y = parrafo(c, M, y, frase, W - 2 * M - 190, tam=10)
    y -= 4
    y = parrafo(c, M, y, f"Son: {importe_en_letras(t['total']).upper()}.", W - 2 * M - 190, "Helvetica-Oblique", 9,
                color=GRIS)
    bx, by = W - M - 170, bottom - 60
    c.setFillColor(ROJO)
    c.rect(bx, by, 170, 44, fill=1, stroke=0)
    tx(c, bx + 10, by + 28, "TOTAL FACTURA", "Helvetica-Bold", 9, white)
    tx(c, bx + 160, by + 10, num(t["total"], euro=True), "Helvetica-Bold", 16, white, "r")

    y = by - 34
    tx(c, M, y, "Forma de pago: Transferencia bancaria a 15 días fecha factura", tam=9.5)
    tx(c, M, y - 14, f"IBAN {IBAN_GARRANDES}", "Helvetica-Bold", 9.5)
    parrafo(c, M, y - 40, "Transporte de viajeros por carretera gravado al tipo reducido del 10 % (art. 91.Uno.2.1º "
                          "de la Ley 37/1992 del IVA). Los peajes y aparcamientos, si los hubiera, se incluyen en el "
                          "precio.", W - 2 * M, tam=8, color=GRIS)
    c.setStrokeColor(ROJO)
    c.line(M, 54, W - M, 54)
    tx(c, W / 2, 42, f"{em['nombre']} · Inscrita en el Registro Mercantil de Burgos, tomo 905, folio 160, hoja "
                     "BU-21877 · Autorización de transporte VD-0000000 (ficticia)", tam=6.8, color=GRIS, alin="c")
    c.save()


DOCUMENTOS = [doc_01, doc_02, doc_03, doc_04, doc_05, doc_06]


# ----------------------------------------------------------------------------------------------
# JSON esperado
# ----------------------------------------------------------------------------------------------
def esperado(d: dict) -> dict:
    t = d["t"]
    lineas = [{"concepto_contiene": ln["contiene"], "cantidad": s2(ln.get("cantidad")),
               "precio": s2(ln.get("precio")), "importe": s2(ln["importe"])} for ln in d["lineas"]]
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
        "desglose": [{"tipo_iva": f"{g['tipo']:.2f}", "base": s2(g["base"]), "cuota": s2(g["cuota"])}
                     for g in t["desglose"]],
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
RE_IMPORTE = re.compile(r"(?<![\d.,])-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2,4}(?!\d)")
INDICIOS = {"exencion", "inversion_sujeto_pasivo", "recargo_equivalencia", "suplidos", "descuento", "rectificativa",
            "intracomunitaria"}
PATRONES_PROHIBIDOS = {  # palabras que activarían particularidades fiscales no previstas en el JSON
    "exencion": r"\bexent[ao]\b|exenci[oó]n|art(?:[ií]culo|\.)\s*20\b",
    "inversion_sujeto_pasivo": r"inversi[oó]n\s+del\s+sujeto\s+pasivo|art(?:[ií]culo|\.)\s*84",
    "no_sujeta": r"\bno\s+sujet[ao]\b",
    "recargo_equivalencia": r"recargo\s+(?:de\s+)?equivalencia",
    "suplidos": r"\bsuplidos?\b",
    "descuento": r"\bdescuento\b|\bdto\.?\b",
    "rectificativa": r"rectificativ|\babono\b",
    "intracomunitaria": r"intracomunitari|\bVIES\b|reverse\s+charge",
    "criterio_caja": r"criterio\s+de\s+caja",
    "regimen_especial": r"r[eé]gimen\s+especial|agencias\s+de\s+viaje|bienes\s+usados|\bREAGYP\b",
    "verifactu_existente": r"VERI\*?FACTU|QR\s+tributario|Factura\s+verificable",
}


def _importes_texto(texto: str) -> set[Decimal]:
    return {abs(D(m.group(0).replace(".", "").replace(",", "."))) for m in RE_IMPORTE.finditer(texto)}


def _plano(s: str) -> str:
    return re.sub(r"[\s.\-]", "", s.upper())


def _esp(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verificar(ruta_pdf: Path, e: dict, d: dict) -> list[str]:
    fallos = []

    def debe(cond, msg):
        if not cond:
            fallos.append(msg)

    # coherencia del JSON (independiente de pdftotext)
    debe(es_valido(e["empresa_nif"]), "empresa_nif inválido")
    debe(e["empresa_nif"] == e["emisor"]["nif"], "la empresa debe ser el emisor (factura propia)")
    debe(e["situacion_esperada"] in ("preparatorio", "emitida_propia"), "situación no admitida")
    debe(set(e["indicios_esperados"]) <= INDICIOS, "indicio desconocido")
    base, cuota, total = D(e["base_imponible"]), D(e["cuota_iva"]), D(e["total_factura"])
    debe(sum(D(x["importe"]) for x in e["lineas"]) == base, "suma de líneas != base")
    debe(sum(D(g["base"]) for g in e["desglose"]) == base, "suma de bases del desglose != base")
    debe(sum(D(g["cuota"]) for g in e["desglose"]) == cuota, "suma de cuotas del desglose != cuota")
    for g in e["desglose"]:
        debe(q2(D(g["base"]) * D(g["tipo_iva"]) / 100) == D(g["cuota"]), f"cuota mal calculada al {g['tipo_iva']}")
    debe(base + cuota == total, "base + cuota != total")
    for x in e["lineas"]:
        if x["cantidad"] is not None and x["precio"] is not None:
            debe(abs(q2(D(x["cantidad"]) * D(x["precio"])) - D(x["importe"])) <= q2(D(x["cantidad"]) * D(x["precio"])) / 10,
                 f"línea incoherente {x['concepto_contiene']}")
    if e["retencion"]:
        r = D(e["retencion"]["importe"])
        debe(q2(base * D(e["retencion"]["porcentaje"]) / 100) == r, "retención mal calculada")
        debe(total - r == D(e["total_a_pagar"]), "total - retención != total a pagar")
    else:
        debe(e["total_a_pagar"] is None, "total_a_pagar debe ser null sin retención")

    if not shutil.which("pdftotext"):
        return fallos + ["pdftotext no disponible: verificación del texto omitida"]
    texto = subprocess.run(["pdftotext", "-layout", str(ruta_pdf), "-"], capture_output=True, text=True,
                           check=True).stdout
    esp, plano, importes = _esp(texto), _plano(texto), _importes_texto(texto)
    debe(e["numero"] in texto, f"número {e['numero']}")
    debe(d["fecha_txt"] in texto, f"fecha {d['fecha_txt']}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(es_valido(p["nif"]), f"NIF {parte} inválido {p['nif']}")
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        debe(_esp(p["nombre"]) in esp, f"nombre {parte} {p['nombre']}")
        debe(_esp(p["domicilio_contiene"]) in esp, f"domicilio {parte} {p['domicilio_contiene']}")
    for ln in e["lineas"]:
        debe(_esp(ln["concepto_contiene"]) in esp, f"concepto {ln['concepto_contiene']}")
        for k in ("precio", "importe"):
            if ln[k] is not None:
                debe(abs(D(ln[k])) in importes, f"línea {k} {ln[k]}")
    for g in e["desglose"]:
        debe(D(g["base"]) in importes and D(g["cuota"]) in importes, f"desglose {g}")
    for k in ("base_imponible", "cuota_iva", "total_factura", "total_a_pagar"):
        if e[k] is not None:
            debe(D(e[k]) in importes, f"{k} {e[k]}")
    if e["retencion"]:
        debe(D(e["retencion"]["importe"]) in importes, f"retención {e['retencion']['importe']}")
        pct = D(e["retencion"]["porcentaje"]).normalize()
        debe(re.search(rf"\b{pct}\s*%", texto) is not None, f"porcentaje retención {pct}")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    if e["forma_pago_contiene"]:
        debe(_esp(e["forma_pago_contiene"]) in esp, f"forma de pago {e['forma_pago_contiene']}")
    for s in d.get("textos", []):
        debe(_esp(s) in esp, f"texto '{s}'")
    # particularidades: las esperadas deben estar en el texto y no debe haber otras
    for k, p in PATRONES_PROHIBIDOS.items():
        hay = re.search(p, texto, re.I) is not None
        if k in e["indicios_esperados"]:
            debe(hay, f"indicio esperado sin texto que lo respalde: {k}")
        else:
            debe(not hay, f"el texto activa un indicio no esperado: {k}")
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
        fallos = verificar(ruta, e, d) if verificar_ else []
        total_fallos += len(fallos)
        resumen.append({"archivo": d["archivo"], "fallos": fallos})
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}" + (f"  -> {fallos}" if fallos else ""))
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
