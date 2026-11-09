"""Corpus SINTÉTICO de facturas con estética de software de facturación / ERP.

Genera 5 PDF con maquetaciones distintas y, para cada uno, un JSON con los valores esperados para medir el
extractor de datos (app/extraccion). Rasgos comunes de la familia: logotipo de texto arriba a la izquierda,
rejilla de metadatos a la derecha (Número / Fecha / Vencimiento / Forma de pago), tabla de líneas con
Código | Descripción | Cant. | Precio | Dto. % | IVA % | Importe (el importe de línea ya lleva aplicado el
descuento: importe = cant. x precio x (1 - dto)) y cuadro de desglose de IVA con varios tipos.

    01  Reformas (S.L. emisora = usuaria): 10 % + 21 %, rejilla 2x2, cuadro de desglose + totales apilados.
    02  Mayorista de alimentación (recibida): 2 páginas con «Suma y sigue», 10 % + 21 %, emisor solo en el pie.
    03  Catering y eventos (proforma): A4 apaisado, columna «Bruto» antes del descuento, desglose en frases.
    04  Librería-papelería (Courier, estilo impresora matricial): 4 % + 21 %, «Líquido a pagar».
    05  Distribuidor a minorista en recargo de equivalencia (recibida): IVA 10 % + 21 % con R.E. 1,4 % y 5,2 %.

TODOS LOS DATOS SON FICTICIOS: razones sociales, personas, domicilios (calles inventadas), teléfonos (000 000),
correos y webs (dominio .example), registros mercantiles (ceros) e IBAN (ES00… / ES12 3456…). Los NIF/CIF tienen
dígito o letra de control válidos (app.nif.completar_cif / letra del DNI) pero no se corresponden de forma
intencionada con ninguna persona o entidad.

Uso (desde /home/user/verifactu-app):
    python scripts/corpus/erp.py                 # genera y verifica con pdftotext -layout
    python scripts/corpus/erp.py --sin-verificar

Determinista: sin aleatoriedad (semilla fija por si se amplía) y PDF en modo «invariant» de reportlab.
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
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.utils import simpleSplit  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.nif import completar_cif, es_valido  # noqa: E402

SALIDA = RAIZ / "tests" / "corpus" / "erp"
SEMILLA = 20261110
random.seed(SEMILLA)

D = Decimal
LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
# tipos de recargo de equivalencia vigentes por tipo de IVA (art. 161 LIVA)
TIPOS_RE = {D("21"): D("5.2"), D("10"): D("1.4"), D("4"): D("0.5")}
GRIS = HexColor("#666666")


# ----------------------------------------------------------------------------------------------
# utilidades numéricas y de formato
# ----------------------------------------------------------------------------------------------
def dni(n: int) -> str:
    return f"{n:08d}{LETRAS_DNI[n % 23]}"


def q2(x) -> Decimal:
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def s2(x) -> str | None:
    return None if x is None else str(q2(x))


def num(x, miles=True, euro=False) -> str:
    """Formato español: 1.234,56 (miles=True) o 1234,56 (miles=False); opcionalmente con « €»."""
    x = q2(x)
    ent, dec = f"{abs(x):.2f}".split(".")
    if miles:
        ent = f"{int(ent):,}".replace(",", ".")
    s = f"{'-' if x < 0 else ''}{ent},{dec}"
    return s + (" €" if euro else "")


def cant(x, decimales=2) -> str:
    x = D(x)
    if decimales == 0:
        return str(int(x))
    return f"{x:.{decimales}f}".replace(".", ",")


def pct(x, decimales=2) -> str:
    return cant(x, decimales)


def fecha_larga(f: date) -> str:
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"


def calcular(lineas: list[dict], recargo: bool = False) -> dict:
    """Importe de línea = cant. x precio x (1 - dto/100) redondeado una sola vez (half-up).
    Desglose por tipo: base = suma de importes; cuota = base x tipo redondeada; R.E. igual sobre la base."""
    for ln in lineas:
        ln["cantidad"], ln["precio"] = D(ln["cantidad"]), D(ln["precio"])
        ln["dto"], ln["iva"] = D(ln.get("dto") or 0), D(ln["iva"])
        ln["bruto"] = q2(ln["cantidad"] * ln["precio"])
        ln["importe"] = q2(ln["cantidad"] * ln["precio"] * (D(100) - ln["dto"]) / D(100))
    tipos = []
    for ln in lineas:
        if ln["iva"] not in tipos:
            tipos.append(ln["iva"])
    desglose = []
    for t in sorted(tipos):
        base = sum((ln["importe"] for ln in lineas if ln["iva"] == t), D("0"))
        fila = {"tipo": t, "base": base, "cuota": q2(base * t / D(100))}
        if recargo:
            fila["tipo_re"] = TIPOS_RE[t]
            fila["cuota_re"] = q2(base * TIPOS_RE[t] / D(100))
        desglose.append(fila)
    base = sum((f["base"] for f in desglose), D("0"))
    cuota = sum((f["cuota"] for f in desglose), D("0"))
    re_total = sum((f.get("cuota_re", D("0")) for f in desglose), D("0"))
    bruto = sum((ln["bruto"] for ln in lineas), D("0"))
    return {"desglose": desglose, "base": base, "cuota": cuota, "recargo": re_total if recargo else None,
            "total": base + cuota + re_total, "bruto": bruto, "descuentos": bruto - base}


# ----------------------------------------------------------------------------------------------
# utilidades de dibujo
# ----------------------------------------------------------------------------------------------
def tx(c, x, y, s, fuente="Helvetica", tam=9, color=black, alin="l"):
    c.setFont(fuente, tam)
    c.setFillColor(color)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[alin](x, y, s)


def lineas_de(s: str, fuente: str, tam: float, ancho: float) -> list[str]:
    return simpleSplit(s, fuente, tam, ancho)


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
    c.setCreator("Generador de corpus ERP (pruebas)")
    return c


def nif_guion(n: str) -> str:
    return f"{n[0]}-{n[1:]}" if n[0].isalpha() else f"{n[:8]}-{n[8]}"


# ----------------------------------------------------------------------------------------------
# NIF ficticios (control válido)
# ----------------------------------------------------------------------------------------------
CIF_PENACLARA = completar_cif("B", "8234517")      # 01 emisor (usuaria)
NIF_QUIROGA = dni(47382915)                          # 01 cliente persona física
CIF_RIBERASECA = completar_cif("A", "0951374")      # 02 emisor (proveedor)
CIF_ALCAZABA = completar_cif("B", "4718263")        # 02 destinatario (usuaria)
CIF_MIRADOR = completar_cif("B", "5023794")         # 03 emisor (usuaria)
CIF_TRAMONTANA = completar_cif("B", "8761245")      # 03 cliente
CIF_ALMENDROS = completar_cif("B", "0412378")       # 04 emisor (usuaria)
CIF_ALCANTARA = completar_cif("F", "1093847")       # 04 cliente (cooperativa)
CIF_SOMONTANO = completar_cif("A", "2216390")       # 05 emisor (proveedor)
NIF_ROSENDO = dni(73519264)                          # 05 destinatario (usuario, minorista en R.E.)


# ==============================================================================================
# 01 · Reformas: rejilla 2x2, tipos 10 % y 21 %, descuentos por línea, desglose + totales apilados
# ==============================================================================================
def doc_reformas() -> dict:
    lineas = [
        {"codigo": "MO-DEM", "concepto": "Demolición de alicatado y solado existente en baño, incluida retirada de "
                                         "escombros a vertedero autorizado", "cantidad": "1", "precio": "480.00",
         "dto": "0", "iva": "10"},
        {"codigo": "MO-FON", "concepto": "Instalación de fontanería: tomas de agua fría y caliente y desagües",
         "cantidad": "1", "precio": "650.00", "dto": "5", "iva": "10"},
        {"codigo": "MAT-AZ3", "concepto": "Azulejo porcelánico 30x60 gris perla (m²)", "cantidad": "18.50",
         "precio": "24.90", "dto": "10", "iva": "10"},
        {"codigo": "MO-ALI", "concepto": "Mano de obra de alicatado y solado (m²)", "cantidad": "26.00",
         "precio": "32.00", "dto": "0", "iva": "10"},
        {"codigo": "MO-COC", "concepto": "Montaje de muebles de cocina y encimera de cuarzo", "cantidad": "1",
         "precio": "720.00", "dto": "0", "iva": "10"},
        {"codigo": "MO-ELE", "concepto": "Electricista: línea independiente para placa de inducción (horas)",
         "cantidad": "3.50", "precio": "38.50", "dto": "0", "iva": "10"},
        {"codigo": "SUM-CAM", "concepto": "Campana extractora de acero inoxidable 90 cm (suministro)",
         "cantidad": "1", "precio": "289.00", "dto": "15", "iva": "21"},
        {"codigo": "SUM-PLA", "concepto": "Placa de inducción de 3 zonas (suministro)", "cantidad": "1",
         "precio": "415.00", "dto": "15", "iva": "21"},
    ]
    t = calcular(lineas)
    f = date(2026, 11, 3)
    d = {
        "archivo": "erp_01_reformas_rejilla_10_21_dto.pdf",
        "estilo": "ERP de reformas: logotipo arriba-izquierda, rejilla 2x2 (Número/Fecha/Vencimiento/Forma de pago) "
                  "a la derecha, caja 'Cliente' persona física, tabla Código/Descripción/Cant./Precio/Dto. %/IVA %/"
                  "Importe con conceptos multilínea, cuadro de desglose 10 % + 21 % y totales apilados con importe "
                  "bruto y descuentos, 1.234,56 €, Helvetica, A4",
        "empresa_nif": CIF_PENACLARA,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "F-2026-00318",
        "fecha": f,
        "fecha_txt": "03/11/2026",
        "emisor": {"nombre": "Obras y Reformas Peñaclara, S.L.", "nif": CIF_PENACLARA, "domicilio_contiene": "28947"},
        "destinatario": {"nombre": "Marta Quiroga Bermúdez", "nif": NIF_QUIROGA, "domicilio_contiene": "28019"},
        "lineas": lineas, "t": t,
        "referencias": {"obra": "Reforma integral de baño y cocina"},
        "iban": "ES0098765432109876543210",
        "forma_pago_contiene": "Transferencia bancaria",
        "indicios": ["descuento"],
        "notas": "Factura de la propia empresa usuaria (emisora) generada con un ERP, sin marcas VERI*FACTU: "
                 "documento preparatorio. Dos tipos: 10 % (renovación de vivienda de particular) y 21 % "
                 "(electrodomésticos). Importes de línea con el descuento ya aplicado (18,50 x 24,90 -10 % = 414,585 "
                 "-> 414,59 half-up). Distractores: 'Importe bruto' y 'Descuentos' antes de la base, vencimiento "
                 "03/12/2026, 'Según presupuesto P-2026-0144 aceptado el 02/09/2026' (el documento es una factura, "
                 "no un presupuesto) y dirección de la obra repetida. Valores de la rejilla debajo de su rótulo.",
    }

    def render(ruta: Path):
        W, H = A4
        ac = HexColor("#1F6F5C")
        claro = HexColor("#E7F2EF")
        c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", d["emisor"]["nombre"])
        x0, xr = 40, W - 40
        # logotipo
        c.setFillColor(ac)
        c.roundRect(x0, H - 92, 46, 46, 7, stroke=0, fill=1)
        tx(c, x0 + 23, H - 76, "OR", "Helvetica-Bold", 18, white, "c")
        tx(c, x0 + 56, H - 66, "PEÑACLARA", "Helvetica-Bold", 20, ac)
        tx(c, x0 + 56, H - 82, "obras y reformas", "Helvetica", 10, GRIS)
        y = H - 114
        tx(c, x0, y, d["emisor"]["nombre"], "Helvetica-Bold", 9)
        for s in ["C/ Sierra de Albarracín, 41, nave 3", "28947 Fuenlabrada (Madrid)", f"CIF: {CIF_PENACLARA}",
                  "Tel. 910 000 000 · administracion@penaclara.example"]:
            y -= 11.5
            tx(c, x0, y, s, "Helvetica", 8.5)
        # título y rejilla 2x2
        tx(c, xr, H - 60, "FACTURA", "Helvetica-Bold", 22, ac, "r")
        gx, gy, cw, ch = xr - 250, H - 74, 125, 32
        celdas = [("Número", d["numero"]), ("Fecha", d["fecha_txt"]), ("Vencimiento", "03/12/2026"),
                  ("Forma de pago", "Transferencia bancaria")]
        c.setStrokeColor(ac)
        c.setLineWidth(0.7)
        for i, (et, val) in enumerate(celdas):
            x, yt = gx + (i % 2) * cw, gy - (i // 2) * ch
            c.setFillColor(claro)
            c.rect(x, yt - ch, cw, ch, stroke=1, fill=1)
            tx(c, x + 6, yt - 11, et, "Helvetica", 7.5, GRIS)
            tx(c, x + 6, yt - 25, val, "Helvetica-Bold", 9.5)
        # cliente
        tx(c, gx, H - 158, "CLIENTE", "Helvetica-Bold", 8, ac)
        c.rect(gx, H - 236, 250, 70)
        y = H - 180
        tx(c, gx + 8, y, d["destinatario"]["nombre"], "Helvetica-Bold", 10)
        for s in ["C/ del Mimbre, 7, 2.º A", "28019 Madrid", f"DNI: {NIF_QUIROGA}"]:
            y -= 13
            tx(c, gx + 8, y, s, "Helvetica", 9)
        # obra
        tx(c, x0, H - 196, "Obra: Reforma integral de baño y cocina", "Helvetica", 8.5)
        tx(c, x0, H - 208, "Dirección de la obra: C/ del Mimbre, 7, 2.º A", "Helvetica", 8.5)
        tx(c, x0, H - 220, "Según presupuesto P-2026-0144 aceptado el 02/09/2026", "Helvetica", 8.5, GRIS)
        # tabla
        yt = H - 264
        c.setFillColor(ac)
        c.rect(x0, yt - 6, xr - x0, 18, stroke=0, fill=1)
        cols = [("Código", x0 + 5, "l"), ("Descripción", 98, "l"), ("Cant.", 352, "r"), ("Precio", 407, "r"),
                ("Dto. %", 447, "r"), ("IVA %", 487, "r"), ("Importe", xr - 5, "r")]
        for et, x, al in cols:
            tx(c, x, yt, et, "Helvetica-Bold", 8.5, white, al)
        y = yt - 22
        c.setStrokeColor(HexColor("#C9D6D2"))
        c.setLineWidth(0.4)
        for ln in lineas:
            partes = lineas_de(ln["concepto"], "Helvetica", 8.5, 210)
            tx(c, x0 + 5, y, ln["codigo"], "Helvetica", 8)
            for k, p in enumerate(partes):
                tx(c, 98, y - k * 10.5, p, "Helvetica", 8.5)
            tx(c, 352, y, cant(ln["cantidad"]), "Helvetica", 8.5, alin="r")
            tx(c, 407, y, num(ln["precio"]), "Helvetica", 8.5, alin="r")
            tx(c, 447, y, pct(ln["dto"]), "Helvetica", 8.5, alin="r")
            tx(c, 487, y, pct(ln["iva"], 0), "Helvetica", 8.5, alin="r")
            tx(c, xr - 5, y, num(ln["importe"], euro=True), "Helvetica", 8.5, alin="r")
            y -= 10.5 * (len(partes) - 1) + 8
            c.line(x0, y, xr, y)
            y -= 11
        # desglose de IVA (izquierda)
        yb = y - 26
        tx(c, x0, yb, "Desglose de IVA", "Helvetica-Bold", 9, ac)
        yb -= 16
        c.setFillColor(claro)
        c.rect(x0, yb - 4, 250, 14, stroke=0, fill=1)
        for et, x, al in [("Tipo IVA", x0 + 5, "l"), ("Base imponible", x0 + 160, "r"), ("Cuota IVA", x0 + 245, "r")]:
            tx(c, x, yb, et, "Helvetica-Bold", 8, alin=al)
        for fila in t["desglose"]:
            yb -= 14
            tx(c, x0 + 5, yb, f"IVA {pct(fila['tipo'], 0)} %", "Helvetica", 8.5)
            tx(c, x0 + 160, yb, num(fila["base"], euro=True), "Helvetica", 8.5, alin="r")
            tx(c, x0 + 245, yb, num(fila["cuota"], euro=True), "Helvetica", 8.5, alin="r")
        # totales apilados (derecha)
        yt2 = y - 26
        filas = [("Importe bruto", num(t["bruto"], euro=True)), ("Descuentos", num(-t["descuentos"], euro=True)),
                 ("Base imponible", num(t["base"], euro=True)), ("Total IVA", num(t["cuota"], euro=True))]
        for et, val in filas:
            tx(c, 345, yt2, et, "Helvetica", 9)
            tx(c, xr - 5, yt2, val, "Helvetica", 9, alin="r")
            yt2 -= 15
        yt2 -= 6
        c.setFillColor(ac)
        c.rect(340, yt2 - 7, xr - 340, 20, stroke=0, fill=1)
        tx(c, 345, yt2, "TOTAL FACTURA", "Helvetica-Bold", 10.5, white)
        tx(c, xr - 5, yt2, num(t["total"], euro=True), "Helvetica-Bold", 10.5, white, "r")
        # pie
        tx(c, x0, 150, "Datos bancarios para transferencia: IBAN ES00 9876 5432 1098 7654 3210", "Helvetica", 8.5)
        tx(c, x0, 138, "Indique el número de factura en el concepto de la transferencia.", "Helvetica", 8.5)
        parrafo(c, x0, 84, "Obras y Reformas Peñaclara, S.L. Inscrita en el Registro Mercantil de Madrid, tomo 00000, "
                "folio 000, hoja M-000000. Sus datos personales se tratan para la gestión de la relación comercial y "
                "el cumplimiento de obligaciones legales; puede ejercer sus derechos en el domicilio social.",
                xr - x0, "Helvetica", 6.5, 8, GRIS)
        tx(c, xr, 40, "Página 1 de 1", "Helvetica", 7, GRIS, "r")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 02 · Mayorista de alimentación: 2 páginas, «Suma y sigue», albaranes, emisor solo en el pie
# ==============================================================================================
def doc_mayorista() -> dict:
    A1 = "Albarán nº 26/7781 de 02.11.2026"
    A2 = "Albarán nº 26/7802 de 04.11.2026"
    lineas = [
        {"codigo": "10421", "concepto": "Aceite de girasol alto oleico, garrafa 5 l", "cantidad": "6", "ud": "ud",
         "precio": "9.85", "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "10530", "concepto": "Vinagre de vino blanco 1 l", "cantidad": "12", "ud": "ud",
         "precio": "0.79", "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "11002", "concepto": "Azúcar blanco 1 kg", "cantidad": "10", "ud": "ud", "precio": "1.15",
         "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "12210", "concepto": "Atún claro en aceite de girasol, lata 1 kg", "cantidad": "8", "ud": "ud",
         "precio": "11.40", "dto": "5", "iva": "10", "alb": A1},
        {"codigo": "13005", "concepto": "Chorizo de León sarta (peso variable)", "cantidad": "4.35", "ud": "kg",
         "precio": "8.95", "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "13140", "concepto": "Jamón serrano bodega deshuesado", "cantidad": "7.82", "ud": "kg",
         "precio": "10.60", "dto": "3", "iva": "10", "alb": A1},
        {"codigo": "14020", "concepto": "Lomo de cerdo fresco", "cantidad": "12.5", "ud": "kg", "precio": "6.45",
         "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "14310", "concepto": "Pechuga de pollo fileteada", "cantidad": "9.75", "ud": "kg",
         "precio": "7.20", "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "15002", "concepto": "Merluza congelada en rodajas, caja 5 kg", "cantidad": "3", "ud": "caja",
         "precio": "34.50", "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "16101", "concepto": "Café molido mezcla 70/30, paquete 1 kg", "cantidad": "5", "ud": "ud",
         "precio": "12.30", "dto": "10", "iva": "10", "alb": A1},
        {"codigo": "17001", "concepto": "Agua mineral 1,5 l (pack 6)", "cantidad": "10", "ud": "pack",
         "precio": "2.10", "dto": "0", "iva": "10", "alb": A1},
        {"codigo": "17110", "concepto": "Refresco de cola lata 33 cl (caja 24)", "cantidad": "4", "ud": "caja",
         "precio": "11.90", "dto": "5", "iva": "10", "alb": A1},
        {"codigo": "18205", "concepto": "Vino tinto D.O. crianza, botella 75 cl", "cantidad": "24", "ud": "ud",
         "precio": "4.75", "dto": "8", "iva": "21", "alb": A1},
        {"codigo": "18310", "concepto": "Cerveza lager barril 30 l", "cantidad": "2", "ud": "ud", "precio": "68.00",
         "dto": "0", "iva": "21", "alb": A1},
        {"codigo": "19050", "concepto": "Detergente lavavajillas industrial 12 kg", "cantidad": "2", "ud": "ud",
         "precio": "27.40", "dto": "0", "iva": "21", "alb": A1},
        {"codigo": "19077", "concepto": "Lejía apta para la desinfección de agua, garrafa 5 l", "cantidad": "6",
         "ud": "ud", "precio": "2.35", "dto": "0", "iva": "21", "alb": A2},
        {"codigo": "19210", "concepto": "Servilletas de papel 2 capas 40x40 (caja 1.200 uds.)", "cantidad": "3",
         "ud": "caja", "precio": "18.90", "dto": "0", "iva": "21", "alb": A2},
        {"codigo": "19305", "concepto": "Film transparente 45 cm x 300 m", "cantidad": "4", "ud": "ud",
         "precio": "6.80", "dto": "0", "iva": "21", "alb": A2},
        {"codigo": "19410", "concepto": "Bolsas de basura 85x105 (rollo 10 uds.)", "cantidad": "15", "ud": "rollo",
         "precio": "1.95", "dto": "10", "iva": "21", "alb": A2},
        {"codigo": "19520", "concepto": "Guantes de nitrilo talla M (caja 100)", "cantidad": "5", "ud": "caja",
         "precio": "7.45", "dto": "0", "iva": "21", "alb": A2},
        {"codigo": "13210", "concepto": "Salchichón ibérico (peso variable)", "cantidad": "2.18", "ud": "kg",
         "precio": "14.30", "dto": "0", "iva": "10", "alb": A2},
        {"codigo": "11210", "concepto": "Sal marina gruesa 1 kg", "cantidad": "6", "ud": "ud", "precio": "0.65",
         "dto": "0", "iva": "10", "alb": A2},
        {"codigo": "16220", "concepto": "Chocolate a la taza 1 kg", "cantidad": "3", "ud": "ud", "precio": "6.10",
         "dto": "0", "iva": "10", "alb": A2},
        {"codigo": "15110", "concepto": "Calamar limpio congelado, caja 4 kg", "cantidad": "2", "ud": "caja",
         "precio": "29.80", "dto": "5", "iva": "10", "alb": A2},
        {"codigo": "14450", "concepto": "Carne picada de ternera", "cantidad": "6.4", "ud": "kg", "precio": "9.15",
         "dto": "0", "iva": "10", "alb": A2},
        {"codigo": "17205", "concepto": "Néctar de naranja 1 l (caja 12)", "cantidad": "3", "ud": "caja",
         "precio": "10.20", "dto": "0", "iva": "10", "alb": A2},
        {"codigo": "18120", "concepto": "Vino blanco D.O. verdejo, botella 75 cl", "cantidad": "18", "ud": "ud",
         "precio": "5.20", "dto": "8", "iva": "21", "alb": A2},
        {"codigo": "12330", "concepto": "Mejillones en escabeche, lata 550 g", "cantidad": "12", "ud": "ud",
         "precio": "3.95", "dto": "0", "iva": "10", "alb": A2},
    ]
    t = calcular(lineas)
    filas_por_pagina1 = 24  # el ERP imprime un número fijo de renglones por página
    d = {
        "archivo": "erp_02_mayorista_2paginas_recibida.pdf",
        "estilo": "ERP de mayorista (Times): 2 páginas, metadatos en rejilla de cabeceras con valores debajo, "
                  "'Facturar a' + 'Dirección de entrega', líneas agrupadas por albarán con columna Ud., cantidades "
                  "en kg con 3 decimales, 'Suma y sigue'/'Suma anterior', desglose 10 % + 21 % y fila de totales "
                  "en la página 2, emisor (razón social, CIF, domicilio) solo en el pie, fecha dd.mm.yyyy, importes "
                  "1.234,56 sin €",
        "empresa_nif": CIF_ALCAZABA,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "FV26-004512",
        "fecha": date(2026, 11, 5),
        "fecha_txt": "05.11.2026",
        "emisor": {"nombre": "Ultramarinos Mayoristas Riberaseca, S.A.", "nif": CIF_RIBERASECA,
                   "domicilio_contiene": "09007"},
        "destinatario": {"nombre": "Casa de Comidas La Alcazaba de Tiedra, S.L.", "nif": CIF_ALCAZABA,
                         "domicilio_contiene": "47123"},
        "lineas": lineas, "t": t,
        "referencias": {"pedido": "PC-88213"},
        "iban": "ES1234567890123456789012",
        "forma_pago_contiene": "Transferencia 30 días",
        "indicios": ["descuento"],
        "notas": "Factura de proveedor recibida por la empresa usuaria (destinataria, CIF con guion). 28 líneas en "
                 "dos albaranes; la página 1 termina con 'Suma y sigue' (importe parcial que NO es la base) y la "
                 "página 2 empieza con 'Suma anterior' y repite la cabecera. Distractores: dirección de entrega en "
                 "Tordesillas (47100), código de cliente 430.0217, fechas de albarán, vencimiento 05.12.2026. "
                 "Cantidades por peso con 3 decimales (12,500 kg x 6,45 = 80,625 -> 80,63 half-up). El nombre "
                 "comercial 'RIBERASECA' encabeza; la razón social, el CIF y el domicilio fiscal del emisor solo "
                 "figuran en el pie de cada página.",
        "filas_p1": filas_por_pagina1,
    }

    def render(ruta: Path):
        W, H = A4
        x0, xr = 40, W - 40
        oscuro = HexColor("#5A2E0E")
        sombra = HexColor("#F1E7DC")
        c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "Ultramarinos Mayoristas Riberaseca, S.A.")
        F, FB, FI = "Times-Roman", "Times-Bold", "Times-Italic"

        def cabecera_logo():
            tx(c, x0, H - 60, "RIBERASECA", FB, 24, oscuro)
            c.setStrokeColor(oscuro)
            c.setLineWidth(1.4)
            c.line(x0, H - 66, x0 + 158, H - 66)
            tx(c, x0, H - 78, "ULTRAMARINOS MAYORISTAS · DESDE 1987", FI, 9, oscuro)
            tx(c, x0, H - 90, "pedidos@riberaseca.example · Tel. 947 000 000", F, 8.5)
            tx(c, x0, H - 101, "www.riberaseca.example", F, 8.5)

        def pie():
            c.setStrokeColor(oscuro)
            c.setLineWidth(0.5)
            c.line(x0, 74, xr, 74)
            tx(c, W / 2, 62, f"Ultramarinos Mayoristas Riberaseca, S.A. · CIF {CIF_RIBERASECA} · Polígono Industrial "
               "Los Brezos, parcela 14 · 09007 Burgos", F, 8, alin="c")
            tx(c, W / 2, 51, "Inscrita en el Registro Mercantil de Burgos, tomo 000, folio 00, hoja BU-0000, "
               "inscripción 1.ª", F, 7.5, GRIS, "c")

        cols = [("Código", x0 + 3, "l"), ("Descripción", 84, "l"), ("Cant.", 320, "r"), ("Ud.", 326, "l"),
                ("Precio", 396, "r"), ("Dto. %", 436, "r"), ("IVA %", 472, "r"), ("Importe", xr - 3, "r")]

        def cabecera_tabla(yt):
            c.setFillColor(sombra)
            c.rect(x0, yt - 5, xr - x0, 16, stroke=0, fill=1)
            c.setStrokeColor(oscuro)
            c.setLineWidth(0.6)
            c.line(x0, yt + 11, xr, yt + 11)
            c.line(x0, yt - 5, xr, yt - 5)
            for et, x, al in cols:
                tx(c, x, yt, et, FB, 9, alin=al)
            return yt - 19

        # ---------- página 1
        cabecera_logo()
        tx(c, xr, H - 56, "FACTURA", FB, 18, oscuro, "r")
        mx = xr - 255
        c.setLineWidth(0.6)

        def rejilla(y_top, celdas):
            x = mx
            for et, val, w in celdas:
                c.setFillColor(sombra)
                c.rect(x, y_top - 13, w, 13, stroke=1, fill=1)
                c.setFillColor(white)
                c.rect(x, y_top - 30, w, 17, stroke=1, fill=1)
                tx(c, x + w / 2, y_top - 10, et, FB, 8.5, alin="c")
                tx(c, x + w / 2, y_top - 25, val, F, 9.5, alin="c")
                x += w
        c.setStrokeColor(oscuro)
        rejilla(H - 70, [("Nº factura", d["numero"], 85), ("Fecha", d["fecha_txt"], 85),
                         ("Vencimiento", "05.12.2026", 85)])
        rejilla(H - 106, [("Forma de pago", "Transferencia 30 días", 170), ("Cód. cliente", "430.0217", 85)])
        tx(c, xr, H - 150, "Página 1 de 2", FI, 8.5, alin="r")
        # facturar a / entrega
        tx(c, x0, H - 168, "Facturar a:", FB, 9.5)
        c.rect(x0, H - 236, 250, 62)
        y = H - 188
        tx(c, x0 + 7, y, d["destinatario"]["nombre"], FB, 10)
        for s in ["Plaza Mayor, 3", "47123 Tiedra (Valladolid)", f"CIF: {nif_guion(CIF_ALCAZABA)}"]:
            y -= 12.5
            tx(c, x0 + 7, y, s, F, 9.5)
        tx(c, mx, H - 168, "Dirección de entrega:", FB, 9.5)
        c.rect(mx, H - 236, 255, 62)
        y = H - 188
        for s in ["La Alcazaba de Tiedra (segundo local)", "Avda. de Zamora, 22", "47100 Tordesillas (Valladolid)",
                  "Horario de descarga: 8:00 a 11:00"]:
            tx(c, mx + 7, y, s, F, 9.5)
            y -= 12.5
        tx(c, x0, H - 256, "Su pedido: PC-88213", F, 9)
        tx(c, 165, H - 256, "Ruta: 12 - Valladolid Sur", F, 9)
        tx(c, mx, H - 256, "Transportista: medios propios", F, 9)
        y = cabecera_tabla(H - 282)
        # filas: encabezado de albarán + líneas
        filas = []
        alb_actual = None
        for ln in lineas:
            if ln["alb"] != alb_actual:
                alb_actual = ln["alb"]
                filas.append(("alb", alb_actual))
            filas.append(("ln", ln))
        suma = D("0")
        pagina = 1
        for i, (tipo, obj) in enumerate(filas):
            if pagina == 1 and i == d["filas_p1"]:
                # cierre de la página 1
                c.setLineWidth(0.6)
                c.line(x0, y + 6, xr, y + 6)
                tx(c, 436, y - 8, "Suma y sigue ......", FI, 9.5, alin="r")
                tx(c, xr - 3, y - 8, num(suma), FB, 9.5, alin="r")
                tx(c, x0, 96, "Continúa en la página siguiente", FI, 8.5, GRIS)
                pie()
                c.showPage()
                pagina = 2
                cabecera_logo()
                tx(c, xr, H - 56, f"FACTURA {d['numero']} (continuación)", FB, 12, oscuro, "r")
                tx(c, xr, H - 72, f"Fecha {d['fecha_txt']} · Página 2 de 2", F, 9, alin="r")
                y = cabecera_tabla(H - 126)
                tx(c, 436, y, "Suma anterior ......", FI, 9.5, alin="r")
                tx(c, xr - 3, y, num(suma), FB, 9.5, alin="r")
                y -= 17
            if tipo == "alb":
                tx(c, 84, y, obj, "Times-BoldItalic", 9)
                y -= 14
                continue
            ln = obj
            tx(c, x0 + 3, y, ln["codigo"], F, 9)
            tx(c, 84, y, ln["concepto"], F, 9)
            dec = 3 if ln["ud"] == "kg" else 0
            tx(c, 320, y, cant(ln["cantidad"], dec), F, 9, alin="r")
            tx(c, 326, y, ln["ud"], F, 9)
            tx(c, 396, y, num(ln["precio"]), F, 9, alin="r")
            tx(c, 436, y, pct(ln["dto"]), F, 9, alin="r")
            tx(c, 472, y, pct(ln["iva"]), F, 9, alin="r")
            tx(c, xr - 3, y, num(ln["importe"]), F, 9, alin="r")
            suma += ln["importe"]
            y -= 14
        c.setLineWidth(0.6)
        c.line(x0, y + 6, xr, y + 6)
        tx(c, x0, y - 8, f"Total líneas: {len(lineas)} · Importes en euros", FI, 8.5, GRIS)
        # desglose por tipo
        yb = y - 34
        tx(c, x0, yb, "Desglose del IVA", FB, 9.5)
        yb -= 6
        anchos = [("Base imponible", 110), ("% IVA", 60), ("Cuota IVA", 100)]
        x = x0
        for et, w in anchos:
            c.setFillColor(sombra)
            c.rect(x, yb - 14, w, 14, stroke=1, fill=1)
            tx(c, x + w / 2, yb - 10, et, FB, 8.5, alin="c")
            x += w
        for fila in t["desglose"]:
            yb -= 16
            x = x0
            for (et, w), val in zip(anchos, [num(fila["base"]), pct(fila["tipo"]), num(fila["cuota"])]):
                c.rect(x, yb - 14, w, 16, stroke=1, fill=0)
                tx(c, x + w - 6, yb - 9, val, F, 9.5, alin="r")
                x += w
        # fila de totales: cabeceras con valores debajo
        yt = yb - 44
        bloques = [("Importe bruto", num(t["bruto"])), ("Descuentos", num(-t["descuentos"])),
                   ("Base imponible", num(t["base"])), ("Cuota IVA", num(t["cuota"])),
                   ("TOTAL FACTURA", num(t["total"]))]
        w = (xr - x0) / len(bloques)
        for k, (et, val) in enumerate(bloques):
            x = x0 + k * w
            ultimo = k == len(bloques) - 1
            c.setFillColor(oscuro if ultimo else sombra)
            c.rect(x, yt, w, 15, stroke=1, fill=1)
            c.setFillColor(white)
            c.rect(x, yt - 20, w, 20, stroke=1, fill=1)
            tx(c, x + w / 2, yt + 4.5, et, FB, 8.5, white if ultimo else black, "c")
            tx(c, x + w / 2, yt - 14, val, FB if ultimo else F, 10.5 if ultimo else 9.5, alin="c")
        yp = yt - 50
        tx(c, x0, yp, "Forma de pago: Transferencia 30 días fecha factura.", F, 9.5)
        tx(c, x0, yp - 13, "Cuenta para transferencias: IBAN ES12 3456 7890 1234 5678 9012", F, 9.5)
        tx(c, x0, yp - 26, "La mercancía viaja por cuenta y riesgo del comprador una vez entregada.", FI, 8.5, GRIS)
        pie()
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 03 · Catering y eventos: A4 apaisado, proforma, columna «Bruto», desglose en frases
# ==============================================================================================
def doc_catering() -> dict:
    lineas = [
        {"codigo": "CAT-COF", "concepto": "Coffee break de media mañana (café, zumos y bollería)",
         "fecha_s": "26/11/2026", "cantidad": "120", "precio": "6.50", "dto": "0", "iva": "10"},
        {"codigo": "CAT-ALM", "concepto": "Almuerzo tipo cóctel con bebidas incluidas", "fecha_s": "26/11/2026",
         "cantidad": "120", "precio": "28.00", "dto": "5", "iva": "10"},
        {"codigo": "CAT-COF", "concepto": "Coffee break de media mañana (café, zumos y bollería)",
         "fecha_s": "27/11/2026", "cantidad": "110", "precio": "6.50", "dto": "0", "iva": "10"},
        {"codigo": "CAT-CEN", "concepto": "Cena de gala: menú degustación de cinco pases con maridaje de vinos "
                                          "de la tierra", "fecha_s": "27/11/2026", "cantidad": "85",
         "precio": "54.00", "dto": "7.5", "iva": "10"},
        {"codigo": "AV-PROY", "concepto": "Alquiler de equipo audiovisual: proyector láser, pantalla de 4 m y "
                                          "megafonía (por día)", "fecha_s": "26/11/2026", "cantidad": "2",
         "precio": "640.00", "dto": "10", "iva": "21"},
        {"codigo": "DEC-FLO", "concepto": "Decoración floral de mesas y escenario", "fecha_s": "27/11/2026",
         "cantidad": "1", "precio": "485.00", "dto": "0", "iva": "21"},
        {"codigo": "PER-SAL", "concepto": "Personal de sala y azafatas (horas)", "fecha_s": "26/11/2026",
         "cantidad": "36", "precio": "22.50", "dto": "0", "iva": "21"},
        {"codigo": "TRA-MOB", "concepto": "Transporte, montaje y desmontaje de mobiliario", "fecha_s": "25/11/2026",
         "cantidad": "1", "precio": "260.00", "dto": "0", "iva": "21"},
    ]
    t = calcular(lineas)
    f = date(2026, 11, 6)
    anticipo = q2(t["total"] / 2)
    d = {
        "archivo": "erp_03_catering_apaisado_proforma.pdf",
        "estilo": "ERP de catering en A4 apaisado: FACTURA PROFORMA, logotipo + emisor arriba-izquierda, caja "
                  "'Datos del cliente' en el centro, metadatos apilados 'Etiqueta: valor' a la derecha, tabla ancha "
                  "con Fecha serv. y columna Bruto antes de Dto. %, importes 1234,56 € sin separador de miles, "
                  "fecha en letra, desglose en frases y anticipo del 50 % como distractor",
        "empresa_nif": CIF_MIRADOR,
        "tipo_documento": "proforma",
        "situacion_esperada": "preparatorio",
        "numero": "PF-2026-0087",
        "fecha": f,
        "fecha_txt": fecha_larga(f),
        "emisor": {"nombre": "Catering y Eventos Mirador del Jalón, S.L.", "nif": CIF_MIRADOR,
                   "domicilio_contiene": "50300"},
        "destinatario": {"nombre": "Congresos Tramontana Azul, S.L.", "nif": CIF_TRAMONTANA,
                         "domicilio_contiene": "28045"},
        "lineas": lineas, "t": t,
        "referencias": {"proyecto": "Jornadas Técnicas de Otoño 2026", "pedido": "OC-2026-0412"},
        "iban": "ES1234560011223344556677",
        "forma_pago_contiene": "Transferencia (50 % anticipo)",
        "indicios": ["descuento"],
        "notas": "Proforma de la empresa usuaria (documento preparatorio). Página A4 apaisada. La columna 'Bruto' "
                 "(cant. x precio) precede a 'Dto. %' e 'Importe neto': el importe de línea es el neto (85 x 54,00 "
                 "-7,5 % = 4245,75). Dos líneas con el mismo código y concepto (coffee break de dos días). "
                 "Distractores: fechas de servicio por línea, vencimiento 20 de noviembre de 2026, 'Anticipo a la "
                 f"confirmación (50 %)' = {num(anticipo, False, True)} que no es total a pagar (total_a_pagar null). "
                 "Desglose de IVA escrito en frases ('Base imponible al 10 %: ... · Cuota IVA 10 %: ...').",
    }

    def render(ruta: Path):
        W, H = landscape(A4)
        x0, xr = 36, W - 36
        ac = HexColor("#7A3E9D")
        claro = HexColor("#F3ECF7")
        c = nuevo_canvas(ruta, landscape(A4), f"Proforma {d['numero']}", d["emisor"]["nombre"])
        # logotipo
        p = c.beginPath()
        p.moveTo(x0, H - 74)
        p.lineTo(x0 + 18, H - 46)
        p.lineTo(x0 + 28, H - 60)
        p.lineTo(x0 + 36, H - 52)
        p.lineTo(x0 + 50, H - 74)
        p.close()
        c.setFillColor(ac)
        c.drawPath(p, stroke=0, fill=1)
        tx(c, x0 + 58, H - 60, "MIRADOR DEL JALÓN", "Helvetica-Bold", 16, ac)
        tx(c, x0 + 58, H - 73, "catering & eventos", "Helvetica-Oblique", 9, GRIS)
        y = H - 96
        tx(c, x0, y, d["emisor"]["nombre"], "Helvetica-Bold", 8.5)
        for s in [f"CIF {CIF_MIRADOR}", "Ctra. de Calatayud, km 3,2 · 50300 Calatayud (Zaragoza)",
                  "Tel. 976 000 000 · eventos@miradordeljalon.example"]:
            y -= 11
            tx(c, x0, y, s, "Helvetica", 8.5)
        # datos del cliente (centro)
        cx0 = 300
        tx(c, cx0, H - 44, "Datos del cliente", "Helvetica-Bold", 9, ac)
        c.setStrokeColor(ac)
        c.setLineWidth(0.7)
        c.roundRect(cx0, H - 132, 245, 80, 5)
        y = H - 70
        tx(c, cx0 + 9, y, d["destinatario"]["nombre"], "Helvetica-Bold", 9.5)
        for s in ["C/ de los Ceramistas, 18, 4.ª planta", "28045 Madrid", f"NIF: {CIF_TRAMONTANA}",
                  "Att.: Departamento de Administración"]:
            y -= 12.5
            tx(c, cx0 + 9, y, s, "Helvetica", 9)
        # título y metadatos apilados
        tx(c, xr, H - 50, "FACTURA PROFORMA", "Helvetica-Bold", 16, ac, "r")
        mx = 590
        y = H - 74
        for et, val in [("Núm. proforma:", d["numero"]), ("Fecha:", d["fecha_txt"]),
                        ("Vencimiento:", fecha_larga(date(2026, 11, 20))),
                        ("Forma de pago:", "Transferencia (50 % anticipo)")]:
            tx(c, mx, y, et, "Helvetica-Bold", 9)
            tx(c, xr, y, val, "Helvetica", 9, alin="r")
            y -= 14
        # referencias
        tx(c, x0, H - 156, "Proyecto: Jornadas Técnicas de Otoño 2026 (Zaragoza, 26 y 27 de noviembre)",
           "Helvetica", 9)
        tx(c, x0, H - 169, "Su pedido: OC-2026-0412", "Helvetica", 9)
        tx(c, cx0 + 250, H - 156, "Lugar del evento: Palacio de Congresos de Valdeazor (sala Moncayo)",
           "Helvetica", 9, GRIS)
        # tabla ancha
        yt = H - 196
        c.setFillColor(ac)
        c.rect(x0, yt - 6, xr - x0, 18, stroke=0, fill=1)
        cols = [("Código", x0 + 5, "l"), ("Descripción", 100, "l"), ("Fecha serv.", 372, "l"),
                ("Cant.", 472, "r"), ("Precio unit.", 545, "r"), ("Bruto", 615, "r"), ("Dto. %", 662, "r"),
                ("IVA %", 705, "r"), ("Importe neto", xr - 5, "r")]
        for et, x, al in cols:
            tx(c, x, yt, et, "Helvetica-Bold", 8.5, white, al)
        y = yt - 21
        c.setStrokeColor(HexColor("#D9C8E3"))
        c.setLineWidth(0.4)
        for k, ln in enumerate(lineas):
            partes = lineas_de(ln["concepto"], "Helvetica", 8.5, 262)
            if k % 2:
                c.setFillColor(claro)
                c.rect(x0, y - 10.5 * (len(partes) - 1) - 5, xr - x0, 10.5 * len(partes) + 7, stroke=0, fill=1)
            tx(c, x0 + 5, y, ln["codigo"], "Helvetica", 8)
            for j, ptxt in enumerate(partes):
                tx(c, 100, y - j * 10.5, ptxt, "Helvetica", 8.5)
            tx(c, 372, y, ln["fecha_s"], "Helvetica", 8.5)
            tx(c, 472, y, cant(ln["cantidad"], 0), "Helvetica", 8.5, alin="r")
            tx(c, 545, y, num(ln["precio"], False, True), "Helvetica", 8.5, alin="r")
            tx(c, 615, y, num(ln["bruto"], False, True), "Helvetica", 8.5, alin="r")
            tx(c, 662, y, pct(ln["dto"]), "Helvetica", 8.5, alin="r")
            tx(c, 705, y, pct(ln["iva"], 0), "Helvetica", 8.5, alin="r")
            tx(c, xr - 5, y, num(ln["importe"], False, True), "Helvetica", 8.5, alin="r")
            y -= 10.5 * (len(partes) - 1) + 14
        c.setStrokeColor(ac)
        c.setLineWidth(0.8)
        c.line(x0, y + 7, xr, y + 7)
        # resumen de impuestos en frases (izquierda)
        yb = y - 16
        tx(c, x0, yb, "Resumen de impuestos", "Helvetica-Bold", 9, ac)
        for fila in t["desglose"]:
            yb -= 13
            tipo = pct(fila["tipo"], 0)
            tx(c, x0, yb, f"Base imponible al {tipo} %: {num(fila['base'], False, True)} · Cuota IVA {tipo} %: "
               f"{num(fila['cuota'], False, True)}", "Helvetica", 9)
        # totales (derecha)
        yt2 = y - 16
        for et, val, fnt in [("Base imponible", num(t["base"], False, True), "Helvetica"),
                             ("Cuota IVA", num(t["cuota"], False, True), "Helvetica"),
                             ("Importe total", num(t["total"], False, True), "Helvetica-Bold")]:
            tx(c, 600, yt2, et, fnt, 10)
            tx(c, xr - 5, yt2, val, fnt, 10, alin="r")
            yt2 -= 15
        c.setFillColor(claro)
        c.rect(595, yt2 - 6, xr - 595, 18, stroke=0, fill=1)
        tx(c, 600, yt2, "Anticipo a la confirmación (50 %)", "Helvetica-Oblique", 9, ac)
        tx(c, xr - 5, yt2, num(anticipo, False, True), "Helvetica-Oblique", 9, ac, "r")
        # pie
        tx(c, x0, 62, "Pago por transferencia a IBAN ES12 3456 0011 2233 4455 6677 indicando el número de proforma.",
           "Helvetica", 8.5)
        tx(c, x0, 50, "Documento sin validez fiscal: la factura definitiva se expedirá tras la celebración del evento.",
           "Helvetica-Oblique", 8, GRIS)
        tx(c, xr, 36, "Catering y Eventos Mirador del Jalón, S.L. · Registro Mercantil de Zaragoza, hoja Z-00000",
           "Helvetica", 7, GRIS, "r")
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 04 · Librería-papelería: Courier (impresora matricial), 4 % + 21 %, «Líquido a pagar»
# ==============================================================================================
def doc_libreria() -> dict:
    lineas = [
        {"codigo": "LT0341", "concepto": "Matemáticas 3.º ESO. Libro del alumno (edición 2026)", "cantidad": "25",
         "precio": "38.90", "dto": "5", "iva": "4"},
        {"codigo": "LT0342", "concepto": "Lengua Castellana y Literatura 3.º ESO", "cantidad": "25",
         "precio": "36.40", "dto": "5", "iva": "4"},
        {"codigo": "LT0415", "concepto": "Atlas geográfico escolar de España y del mundo", "cantidad": "12",
         "precio": "19.95", "dto": "5", "iva": "4"},
        {"codigo": "LT0520", "concepto": "Diccionario escolar de la lengua española", "cantidad": "10",
         "precio": "14.50", "dto": "0", "iva": "4"},
        {"codigo": "PA1102", "concepto": "Cuaderno espiral A4 cuadrícula 4 mm, 80 hojas", "cantidad": "150",
         "precio": "1.85", "dto": "12", "iva": "21"},
        {"codigo": "PA1210", "concepto": "Bolígrafo tinta gel azul (caja 12 uds.)", "cantidad": "20",
         "precio": "9.60", "dto": "12", "iva": "21"},
        {"codigo": "PA2033", "concepto": "Papel multifunción A4 80 g (caja 5 paquetes)", "cantidad": "8",
         "precio": "21.75", "dto": "0", "iva": "21"},
        {"codigo": "PA3105", "concepto": "Calculadora científica 240 funciones", "cantidad": "15",
         "precio": "13.90", "dto": "0", "iva": "21"},
        {"codigo": "PA4002", "concepto": "Tóner compatible impresora láser negro", "cantidad": "3",
         "precio": "42.35", "dto": "0", "iva": "21"},
    ]
    t = calcular(lineas)
    d = {
        "archivo": "erp_04_libreria_courier_4_21.pdf",
        "estilo": "ERP antiguo de librería-papelería en Courier (estilo impresora matricial, mayúsculas): rótulos "
                  "con puntos de relleno ('NÚM. FACTURA..: '), caja del cliente sin rótulo, tabla con guiones "
                  "CÓDIGO/DESCRIPCIÓN/CANT./PRECIO/%DTO/%IVA/IMPORTE, desglose 4 % + 21 % en columnas monoespaciadas, "
                  "'LÍQUIDO A PAGAR (EUR)' como total, fecha dd-mm-yy, A4",
        "empresa_nif": CIF_ALMENDROS,
        "tipo_documento": "factura",
        "situacion_esperada": "preparatorio",
        "numero": "A26-001947",
        "fecha": date(2026, 11, 7),
        "fecha_txt": "07-11-26",
        "emisor": {"nombre": "Librería Papelería Los Almendros de Oria, S.L.", "nif": CIF_ALMENDROS,
                   "domicilio_contiene": "04810"},
        "destinatario": {"nombre": "Centro de Formación Alcántara del Sur, S. Coop.", "nif": CIF_ALCANTARA,
                         "domicilio_contiene": "10500"},
        "lineas": lineas, "t": t,
        "referencias": {"pedido": "PED-4471", "contrato": "CS-2026/03"},
        "iban": "ES0013572468000011223344",
        "forma_pago_contiene": "TRANSF. 30 DÍAS",
        "indicios": ["descuento"],
        "notas": "Factura de la empresa usuaria (emisora) impresa por un ERP antiguo: todo en mayúsculas y Courier. "
                 "Libros al 4 % y material de papelería al 21 %. El total se rotula 'LÍQUIDO A PAGAR (EUR)' aunque "
                 "no hay retención (total_a_pagar null; total_factura = ese importe). Rótulos con puntos de relleno, "
                 "fecha con año de 2 cifras (07-11-26), caja del destinatario sin rótulo ('N.I.F.:' de cooperativa "
                 "F). Distractores: 'CÓD. CLIENTE..: 000312', vencimiento 07-12-26, suma de bases y de cuotas antes "
                 "del total. Redondeo half-up: 25 x 38,90 -5 % = 923,875 -> 923,88.",
    }

    def render(ruta: Path):
        W, H = A4
        x0, xr = 40, W - 40
        F, FB = "Courier", "Courier-Bold"
        c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", "LOS ALMENDROS")
        c.setLineWidth(1)
        c.rect(x0, H - 72, 168, 32)
        tx(c, x0 + 84, H - 60, "LOS ALMENDROS", FB, 15, alin="c")
        tx(c, x0, H - 84, "LIBRERÍA · PAPELERÍA · MATERIAL ESCOLAR", F, 7.5)
        y = H - 104
        for s in [d["emisor"]["nombre"].upper(), "C/ REAL, 58", "04810 ORIA (ALMERÍA)", f"C.I.F.: {CIF_ALMENDROS}",
                  "TEL. 950 000 000"]:
            tx(c, x0, y, s, F, 8.5)
            y -= 11
        # bloque de metadatos con puntos de relleno
        bx = 300
        c.rect(bx, H - 128, xr - bx, 88)
        tx(c, bx + 8, H - 56, "FACTURA", FB, 13)
        y = H - 74
        for et, val in [("NÚM. FACTURA..: ", d["numero"]), ("FECHA.........: ", d["fecha_txt"]),
                        ("VENCIMIENTO...: ", "07-12-26"), ("FORMA DE PAGO.: ", "TRANSF. 30 DÍAS F.F."),
                        ("CÓD. CLIENTE..: ", "000312")]:
            tx(c, bx + 8, y, et + val, F, 8.5)
            y -= 11
        # cliente sin rótulo
        c.rect(bx, H - 214, xr - bx, 74)
        y = H - 156
        for s in [d["destinatario"]["nombre"].upper(), "AVDA. DE LA CONSTITUCIÓN, 9",
                  "10500 VALENCIA DE ALCÁNTARA (CÁCERES)", f"N.I.F.: {CIF_ALCANTARA}"]:
            tx(c, bx + 8, y, s, F, 8)
            y -= 12
        tx(c, x0, H - 186, "SU PEDIDO Nº: PED-4471", F, 8.5)
        tx(c, x0, H - 198, "CONTRATO Nº: CS-2026/03", F, 8.5)
        tx(c, x0, H - 210, "PÁGINA: 1/1", F, 8.5)
        # tabla monoespaciada
        regla = "-" * 107
        yt = H - 238
        tx(c, x0, yt + 10, regla, F, 8)
        cols = [("CÓDIGO", x0, "l"), ("DESCRIPCIÓN", 82, "l"), ("CANT.", 330, "r"), ("PRECIO", 385, "r"),
                ("%DTO", 420, "r"), ("%IVA", 455, "r"), ("IMPORTE", xr, "r")]
        for et, x, al in cols:
            tx(c, x, yt, et, FB, 8, alin=al)
        tx(c, x0, yt - 10, regla, F, 8)
        y = yt - 24
        for ln in lineas:
            partes = lineas_de(ln["concepto"].upper(), F, 8, 238)
            tx(c, x0, y, ln["codigo"], F, 8)
            for j, ptxt in enumerate(partes):
                tx(c, 82, y - j * 10, ptxt, F, 8)
            tx(c, 330, y, cant(ln["cantidad"], 0), F, 8, alin="r")
            tx(c, 385, y, num(ln["precio"]), F, 8, alin="r")
            tx(c, 420, y, pct(ln["dto"]), F, 8, alin="r")
            tx(c, 455, y, pct(ln["iva"]), F, 8, alin="r")
            tx(c, xr, y, num(ln["importe"]), F, 8, alin="r")
            y -= 10 * len(partes) + 3
        tx(c, x0, y, regla, F, 8)
        # desglose monoespaciado
        y -= 18
        tx(c, 150, y, "B.IMPONIBLE", FB, 8, alin="r")
        tx(c, 210, y, "%IVA", FB, 8, alin="r")
        tx(c, 290, y, "CUOTA IVA", FB, 8, alin="r")
        for fila in t["desglose"]:
            y -= 11
            tx(c, 150, y, num(fila["base"]), F, 8, alin="r")
            tx(c, 210, y, pct(fila["tipo"]), F, 8, alin="r")
            tx(c, 290, y, num(fila["cuota"]), F, 8, alin="r")
        y -= 12
        tx(c, x0, y, regla, F, 8)
        y -= 16
        for et, val, fnt in [("SUMA BASES.............:", num(t["base"]), F),
                             ("SUMA CUOTAS IVA........:", num(t["cuota"]), F),
                             ("LÍQUIDO A PAGAR (EUR)..:", num(t["total"]), FB)]:
            tx(c, 310, y, et, fnt, 9)
            tx(c, xr, y, val, fnt, 9, alin="r")
            y -= 13
        # pie
        tx(c, x0, 132, "FORMA DE PAGO: TRANSFERENCIA A 30 DÍAS FECHA FACTURA A LA CUENTA", F, 8)
        tx(c, x0, 121, "IBAN ES00 1357 2468 0000 1122 3344", F, 8)
        tx(c, x0, 104, "LOS LIBROS DE TEXTO TRIBUTAN AL TIPO SUPERREDUCIDO DEL 4 %.", F, 7.5)
        parrafo(c, x0, 70, "INSCRITA EN EL REGISTRO MERCANTIL DE ALMERÍA, TOMO 0000, FOLIO 00, HOJA AL-00000. "
                "SUS DATOS SE TRATAN PARA GESTIONAR LA RELACIÓN COMERCIAL. PUEDE EJERCER SUS DERECHOS EN EL "
                "DOMICILIO SOCIAL.", xr - x0, F, 6.5, 8, GRIS)
        c.showPage()
        c.save()

    d["render"] = render
    return d


# ==============================================================================================
# 05 · Distribuidor a minorista en recargo de equivalencia (recibida)
# ==============================================================================================
def doc_recargo() -> dict:
    lineas = [
        {"codigo": "30110", "concepto": "Refresco de naranja 2 l (pack 6)", "cantidad": "10", "precio": "6.30",
         "dto": "4", "iva": "10"},
        {"codigo": "30125", "concepto": "Agua mineral natural 1,5 l (pack 6)", "cantidad": "20", "precio": "1.98",
         "dto": "0", "iva": "10"},
        {"codigo": "30240", "concepto": "Galletas tipo maría, caja 800 g", "cantidad": "24", "precio": "1.62",
         "dto": "0", "iva": "10"},
        {"codigo": "30310", "concepto": "Café soluble descafeinado 200 g", "cantidad": "12", "precio": "5.85",
         "dto": "5", "iva": "10"},
        {"codigo": "30610", "concepto": "Aceitunas manzanilla rellenas de anchoa, lata 350 g", "cantidad": "12",
         "precio": "1.45", "dto": "0", "iva": "10"},
        {"codigo": "30422", "concepto": "Cerveza rubia lata 33 cl (pack 24)", "cantidad": "6", "precio": "13.45",
         "dto": "3", "iva": "21"},
        {"codigo": "30510", "concepto": "Vino tinto de mesa brik 1 l", "cantidad": "24", "precio": "1.09",
         "dto": "0", "iva": "21"},
        {"codigo": "40120", "concepto": "Detergente líquido para ropa 3 l", "cantidad": "8", "precio": "6.75",
         "dto": "5", "iva": "21"},
        {"codigo": "40135", "concepto": "Suavizante concentrado 2 l", "cantidad": "8", "precio": "3.20",
         "dto": "0", "iva": "21"},
        {"codigo": "40210", "concepto": "Papel higiénico doble capa (paquete 12 rollos)", "cantidad": "10",
         "precio": "4.35", "dto": "0", "iva": "21"},
    ]
    t = calcular(lineas, recargo=True)
    re_txt = "; ".join(f"R.E. {pct(f['tipo_re'])} % sobre {num(f['base'])} = {num(f['cuota_re'])}"
                       for f in t["desglose"])
    d = {
        "archivo": "erp_05_distribuidor_recargo_equivalencia.pdf",
        "estilo": "ERP de distribución alimentaria: logotipo + emisor arriba-izquierda, metadatos en tabla de dos "
                  "columnas con rótulos sombreados, caja 'Cliente' con código y régimen, columna R.E. % en las "
                  "líneas, cuadro de desglose estilo a3 (Base/% IVA/Cuota IVA/% R.E./Cuota R.E.) con 10 % y 21 %, "
                  "totales apilados con recargo, IBAN del cliente enmascarado, Helvetica 8 pt, A4",
        "empresa_nif": NIF_ROSENDO,
        "tipo_documento": "factura",
        "situacion_esperada": "recibida",
        "numero": "SB26/10592",
        "fecha": date(2026, 10, 30),
        "fecha_txt": "30/10/2026",
        "emisor": {"nombre": "Distribuciones Alimentarias Somontano Bajo, S.A.", "nif": CIF_SOMONTANO,
                   "domicilio_contiene": "22300"},
        "destinatario": {"nombre": "Rosendo Alcaraz Pinilla", "nif": NIF_ROSENDO, "domicilio_contiene": "22430"},
        "lineas": lineas, "t": t,
        "referencias": {"pedido": "PV-26-3318"},
        "iban": None,
        "forma_pago_contiene": "Recibo domiciliado a 60 días",
        "indicios": ["recargo_equivalencia", "descuento"],
        "notas": "Factura de proveedor recibida por un comerciante minorista persona física (usuario, DNI con guion) "
                 "acogido al recargo de equivalencia. El desglose esperado recoge solo IVA (10 % y 21 %); el recargo "
                 f"va aparte ({re_txt}; total R.E. {num(t['recargo'])}) y SÍ forma parte del total_factura "
                 f"({num(t['base'])} + {num(t['cuota'])} + {num(t['recargo'])} = {num(t['total'])}), por lo que "
                 "base + cuota IVA != total. Sin retención (total_a_pagar null). IBAN null: solo figura la cuenta "
                 "de cargo del cliente enmascarada (ES12 3456 **** **** **** 6677). Distractores: código de "
                 "cliente 43001588, nombre comercial 'Alimentación Rosendo', vencimiento 29/12/2026, columna R.E. % "
                 "en cada línea.",
    }

    def render(ruta: Path):
        W, H = A4
        x0, xr = 38, W - 38
        az = HexColor("#0B5394")
        claro = HexColor("#E3EDF7")
        c = nuevo_canvas(ruta, A4, f"Factura {d['numero']}", d["emisor"]["nombre"])
        # logotipo
        c.setFillColor(az)
        c.circle(x0 + 12, H - 58, 12, stroke=0, fill=1)
        c.setFillColor(HexColor("#6FA8DC"))
        c.circle(x0 + 28, H - 58, 12, stroke=0, fill=1)
        tx(c, x0 + 48, H - 58, "somontano", "Helvetica-Bold", 20, az)
        tx(c, x0 + 48, H - 72, "bajo · distribución alimentaria", "Helvetica", 8.5, GRIS)
        y = H - 96
        tx(c, x0, y, d["emisor"]["nombre"], "Helvetica-Bold", 8.5)
        for s in ["Pol. Ind. La Alfranca, calle B, nave 7", "22300 Barbastro (Huesca)",
                  f"NIF {CIF_SOMONTANO} · Tel. 974 000 000", "administracion@somontanobajo.example"]:
            y -= 10.5
            tx(c, x0, y, s, "Helvetica", 8)
        # metadatos: tabla de dos columnas
        tx(c, xr, H - 58, "FACTURA", "Helvetica-Bold", 18, az, "r")
        mx, wl, wv, hf = xr - 230, 90, 140, 15
        y = H - 72
        c.setStrokeColor(az)
        c.setLineWidth(0.5)
        for et, val in [("Núm. factura", d["numero"]), ("Fecha", d["fecha_txt"]), ("Vencimiento", "29/12/2026"),
                        ("Forma de pago", "Recibo domiciliado a 60 días"), ("Comercial", "07 · Ribagorza")]:
            c.setFillColor(claro)
            c.rect(mx, y - hf, wl, hf, stroke=1, fill=1)
            c.setFillColor(white)
            c.rect(mx + wl, y - hf, wv, hf, stroke=1, fill=1)
            tx(c, mx + 5, y - 10.5, et, "Helvetica-Bold", 8)
            tx(c, mx + wl + 5, y - 10.5, val, "Helvetica", 8)
            y -= hf
        # cliente
        yc = H - 168
        tx(c, mx, yc, "Cliente", "Helvetica-Bold", 9, az)
        tx(c, xr, yc, "Cód. cliente 43001588", "Helvetica", 8, GRIS, "r")
        c.rect(mx, yc - 84, 230, 78)
        y = yc - 19
        tx(c, mx + 7, y, d["destinatario"]["nombre"], "Helvetica-Bold", 9.5)
        for s in ["Alimentación Rosendo", "C/ San Hipólito, 12, bajo", "22430 Graus (Huesca)",
                  f"NIF: {nif_guion(NIF_ROSENDO)}", "Régimen IVA: recargo de equivalencia"]:
            y -= 11
            tx(c, mx + 7, y, s, "Helvetica", 8.5)
        tx(c, x0, H - 196, "Su pedido: PV-26-3318 (televenta 28/10/2026)", "Helvetica", 8.5)
        tx(c, x0, H - 208, "Albarán: 26-A-55190 · Entrega: 30/10/2026", "Helvetica", 8.5)
        # tabla
        yt = H - 276
        c.setFillColor(az)
        c.rect(x0, yt - 5, xr - x0, 16, stroke=0, fill=1)
        cols = [("Código", x0 + 4, "l"), ("Descripción", 84, "l"), ("Cant.", 318, "r"), ("Precio", 368, "r"),
                ("Dto. %", 404, "r"), ("IVA %", 440, "r"), ("R.E. %", 476, "r"), ("Importe", xr - 4, "r")]
        for et, x, al in cols:
            tx(c, x, yt, et, "Helvetica-Bold", 8, white, al)
        y = yt - 18
        for k, ln in enumerate(lineas):
            if k % 2:
                c.setFillColor(claro)
                c.rect(x0, y - 4, xr - x0, 14, stroke=0, fill=1)
            tx(c, x0 + 4, y, ln["codigo"], "Helvetica", 8)
            tx(c, 84, y, ln["concepto"], "Helvetica", 8)
            tx(c, 318, y, cant(ln["cantidad"]), "Helvetica", 8, alin="r")
            tx(c, 368, y, num(ln["precio"], euro=True), "Helvetica", 8, alin="r")
            tx(c, 404, y, pct(ln["dto"]), "Helvetica", 8, alin="r")
            tx(c, 440, y, pct(ln["iva"]), "Helvetica", 8, alin="r")
            tx(c, 476, y, pct(TIPOS_RE[ln["iva"]]), "Helvetica", 8, alin="r")
            tx(c, xr - 4, y, num(ln["importe"], euro=True), "Helvetica", 8, alin="r")
            y -= 14
        c.setStrokeColor(az)
        c.setLineWidth(0.8)
        c.line(x0, y + 8, xr, y + 8)
        # cuadro de desglose estilo a3
        yb = y - 22
        anchos = [("Base imponible", 82), ("% IVA", 44), ("Cuota IVA", 66), ("% R.E.", 44), ("Cuota R.E.", 66)]
        x = x0
        c.setLineWidth(0.5)
        for et, w in anchos:
            c.setFillColor(claro)
            c.rect(x, yb - 4, w, 15, stroke=1, fill=1)
            tx(c, x + w / 2, yb, et, "Helvetica-Bold", 7.5, alin="c")
            x += w
        for fila in t["desglose"]:
            yb -= 15
            x = x0
            vals = [num(fila["base"], euro=True), pct(fila["tipo"]), num(fila["cuota"], euro=True),
                    pct(fila["tipo_re"]), num(fila["cuota_re"], euro=True)]
            for (et, w), val in zip(anchos, vals):
                c.rect(x, yb - 4, w, 15, stroke=1, fill=0)
                tx(c, x + w - 4, yb, val, "Helvetica", 8, alin="r")
                x += w
        # totales apilados (derecha)
        yt2 = y - 22
        for et, val in [("Total base imponible", num(t["base"], euro=True)),
                        ("Total cuota IVA", num(t["cuota"], euro=True)),
                        ("Total recargo de equivalencia", num(t["recargo"], euro=True))]:
            tx(c, 372, yt2, et, "Helvetica", 8.5)
            tx(c, xr - 4, yt2, val, "Helvetica", 8.5, alin="r")
            yt2 -= 14
        yt2 -= 6
        c.setFillColor(az)
        c.rect(368, yt2 - 7, xr - 368, 20, stroke=0, fill=1)
        tx(c, 372, yt2, "TOTAL FACTURA", "Helvetica-Bold", 10.5, white)
        tx(c, xr - 4, yt2, num(t["total"], euro=True), "Helvetica-Bold", 10.5, white, "r")
        # textos legales y pago
        yl = min(yb, yt2) - 34
        yl = parrafo(c, x0, yl, "Cliente comerciante minorista acogido al recargo de equivalencia (artículos 154 a 163 "
                     "de la Ley 37/1992 del Impuesto sobre el Valor Añadido). El recargo se repercute junto con el "
                     "IVA de cada tipo.", xr - x0, "Helvetica", 8, 10)
        yl -= 6
        tx(c, x0, yl, "Forma de pago: Recibo domiciliado a 60 días con cargo en la cuenta del cliente "
           "ES12 3456 **** **** **** 6677", "Helvetica", 8.5)
        tx(c, x0, yl - 12, "Las devoluciones de recibos generarán los gastos bancarios correspondientes.",
           "Helvetica", 8, GRIS)
        parrafo(c, x0, 64, "Distribuciones Alimentarias Somontano Bajo, S.A. Inscrita en el Registro Mercantil de "
                "Huesca, tomo 000, folio 00, hoja HU-0000. Responsable del tratamiento de datos: la sociedad emisora; "
                "finalidad: gestión de clientes y facturación.", xr - x0, "Helvetica", 6.5, 8, GRIS)
        c.showPage()
        c.save()

    d["render"] = render
    return d


DOCUMENTOS = [doc_reformas, doc_mayorista, doc_catering, doc_libreria, doc_recargo]


# ----------------------------------------------------------------------------------------------
# JSON esperado
# ----------------------------------------------------------------------------------------------
def esperado(d: dict) -> dict:
    t = d["t"]
    lineas = []
    for ln in d["lineas"]:
        # subcadena corta de la primera línea visual del concepto (evita depender del ajuste de texto)
        contiene = " ".join(ln["concepto"].split()[:4])
        lineas.append({"concepto_contiene": contiene, "cantidad": s2(ln["cantidad"]), "precio": s2(ln["precio"]),
                       "importe": s2(ln["importe"])})
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
        "desglose": [{"tipo_iva": f"{f['tipo']:.2f}", "base": s2(f["base"]), "cuota": s2(f["cuota"])}
                     for f in t["desglose"]],
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


def verificar(ruta_pdf: Path, e: dict, d: dict) -> list[str]:
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
    debe(d["fecha_txt"] in texto, f"fecha {d['fecha_txt']}")
    for parte in ("emisor", "destinatario"):
        p = e[parte]
        debe(es_valido(p["nif"]), f"NIF {parte} inválido {p['nif']}")
        debe(p["nif"] in plano, f"NIF {parte} {p['nif']}")
        debe(_esp(p["nombre"]) in esp, f"nombre {parte} {p['nombre']}")
        debe(_esp(p["domicilio_contiene"]) in esp, f"domicilio {parte} {p['domicilio_contiene']}")
    debe(es_valido(e["empresa_nif"]), "empresa_nif inválido")
    debe(e["empresa_nif"] == (e["destinatario"]["nif"] if e["situacion_esperada"] == "recibida"
                              else e["emisor"]["nif"]), "empresa_nif no cuadra con la situación esperada")
    for ln, lo in zip(e["lineas"], d["lineas"]):
        debe(_esp(ln["concepto_contiene"]) in esp, f"concepto {ln['concepto_contiene']}")
        for k in ("precio", "importe"):
            debe(D(ln[k]) in importes, f"línea {k} {ln[k]}")
        # importe = cant x precio x (1 - dto)
        debe(q2(D(ln["cantidad"]) * D(ln["precio"]) * (100 - lo["dto"]) / 100) == D(ln["importe"]),
             f"importe de línea mal calculado {ln['concepto_contiene']}")
    for k in ("base_imponible", "cuota_iva", "total_factura"):
        debe(D(e[k]) in importes, f"{k} {e[k]}")
    for f in e["desglose"]:
        debe(D(f["base"]) in importes, f"base {f['tipo_iva']} {f['base']}")
        debe(D(f["cuota"]) in importes, f"cuota {f['tipo_iva']} {f['cuota']}")
        debe(q2(D(f["base"]) * D(f["tipo_iva"]) / 100) == D(f["cuota"]), f"cuota {f['tipo_iva']} != base x tipo")
        tipo_txt = f"{D(f['tipo_iva']).normalize():f}"
        debe(re.search(rf"\b{tipo_txt}(?:,00)?\s*%|\b{tipo_txt},00\b", texto) is not None, f"tipo {tipo_txt} %")
    # coherencia aritmética del JSON
    base, cuota, total = D(e["base_imponible"]), D(e["cuota_iva"]), D(e["total_factura"])
    debe(sum(D(x["importe"]) for x in e["lineas"]) == base, "suma de líneas != base")
    debe(sum(D(x["base"]) for x in e["desglose"]) == base, "suma de bases del desglose != base")
    debe(sum(D(x["cuota"]) for x in e["desglose"]) == cuota, "suma de cuotas del desglose != cuota")
    rec = d["t"]["recargo"] or D("0")
    debe(base + cuota + rec == total, "base + cuota (+ R.E.) != total")
    if rec:
        debe(rec in importes, f"recargo {rec}")
        debe("recargo_equivalencia" in e["indicios_esperados"], "falta indicio recargo_equivalencia")
        debe(re.search(r"recargo\s+(?:de\s+)?equivalencia", texto, re.I) is not None, "texto recargo equivalencia")
    if "descuento" in e["indicios_esperados"]:
        debe(re.search(r"\bdto\b|descuento", texto, re.I) is not None, "texto descuento/dto")
    for k, v in e["referencias"].items():
        debe(v in texto, f"referencia {k} {v}")
    if e["iban"]:
        debe(e["iban"] in plano, f"IBAN {e['iban']}")
    if e["forma_pago_contiene"]:
        debe(_esp(e["forma_pago_contiene"]) in esp, f"forma de pago {e['forma_pago_contiene']}")
    # textos que dispararían indicios bloqueantes no previstos (falsos positivos atribuibles al corpus)
    bloqueantes = {
        "exencion": r"\bexent[ao]\b|exenci[oó]n|art(?:[ií]culo|\.)\s*20\b",
        "inversion_sujeto_pasivo": r"inversi[oó]n\s+del\s+sujeto\s+pasivo|art(?:[ií]culo|\.)\s*84",
        "no_sujeta": r"\bno\s+sujet[ao]\b",
        "recargo_equivalencia": r"recargo\s+(?:de\s+)?equivalencia",
        "suplidos": r"\bsuplidos?\b",
        "rectificativa": r"rectificativ|\babono\b",
        "intracomunitaria": r"intracomunitari|\bVIES\b|reverse\s+charge",
        "criterio_caja": r"criterio\s+de\s+caja",
        "regimen_especial": r"r[eé]gimen\s+especial|agencias\s+de\s+viaje|bienes\s+usados|\bREAGYP\b",
        "verifactu_existente": r"VERI\*?FACTU|QR\s+tributario|Factura\s+verificable",
    }
    for k, patron in bloqueantes.items():
        if k not in e["indicios_esperados"]:
            debe(re.search(patron, texto, re.I) is None, f"texto que dispararía el indicio no previsto {k}")
    # páginas
    paginas = texto.count("\f") if texto.endswith("\f") else texto.count("\f") + 1
    if d["archivo"].startswith("erp_02"):
        debe(paginas == 2, f"se esperaban 2 páginas y hay {paginas}")
        debe("Suma y sigue" in texto and "Suma anterior" in texto, "falta 'Suma y sigue'/'Suma anterior'")
        p1, p2 = texto.split("\f")[:2]
        debe(num(D(e["total_factura"])) in p2 and num(D(e["total_factura"])) not in p1, "total no está solo en la pág. 2")
        debe(any(_esp(x["concepto_contiene"]) in _esp(p2) for x in e["lineas"]), "no hay líneas en la página 2")
    return fallos


def main(argv: list[str]) -> int:
    SALIDA.mkdir(parents=True, exist_ok=True)
    verificar_ = "--sin-verificar" not in argv
    total_fallos = 0
    for fabrica in DOCUMENTOS:
        d = fabrica()
        ruta = SALIDA / d["archivo"]
        d["render"](ruta)
        e = esperado(d)
        ruta.with_suffix(".json").write_text(json.dumps(e, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fallos = verificar(ruta, e, d) if verificar_ else []
        total_fallos += len(fallos)
        print(f"{'OK ' if not fallos else 'ERR'} {d['archivo']}" + (f"  -> {fallos}" if fallos else ""))
    return 1 if total_fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
