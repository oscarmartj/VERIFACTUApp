"""Regresiones de la prueba con dos facturas reales de un mismo emisor (carril B: importes y líneas). Las facturas no se
publican: estas pruebas reproducen cada causa con documentos sintéticos (nombres, NIF y direcciones ficticios).

- B1: cuadro de totales en una fila de rótulos con los valores debajo, por columnas («BASE IMPONIBLE | % IVA | IVA | TOTAL |
  MONEDA»): la columna «IVA» junto a «% IVA» se tomaba también por un tipo y los importes quedaban solo deducidos por
  coherencia aritmética (confianza 0,7). La columna de la moneda («EURO (€)») no es un importe, aunque vaya pegada al total.
- B2: precio por millar («PRECIO €/MILLAR»): importe = precio × cantidad ÷ 1000; la cantidad se expresa en millares para
  que precio × cantidad = importe. Descripción de varios renglones con las cifras en uno intermedio: un solo concepto.
- B3: rótulo «DESCRIPCIÓN» centrado sobre una columna ancha y los conceptos alineados mucho más a la izquierda: la primera
  palabra de los renglones que empiezan más a la izquierda que el rótulo (y un número inicial igual a la cantidad) se perdía.

Revisión adversarial de esos cambios (F1-F4):
- F1/F2: un encabezado de grupo («Pedido nº …», «TRABAJOS DE IMPRESIÓN:», «Rectifica la factura …») encima de un concepto
  de dos renglones con las cifras en el primero se anteponía a ese concepto sin aviso (y, con varias líneas, todas se
  pedían confirmar). Solo se antepone lo que no tiene forma de encabezado, y sin indicio de que la frase siga se pide
  confirmar.
- F3: la marca del millar en un segundo renglón de cabecera solo se leía si era a su vez un rótulo conocido («€/MILLAR»).
- F4: con todos los conceptos cortos (ninguno llega bajo el rótulo centrado) el margen no se encontraba y la tabla se
  cortaba tras la primera línea, sin concepto.
"""
import io
import re
import sqlite3

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, texto
from app.nif import completar_cif

EMI = completar_cif("B", "0012345")   # emisor (la empresa usuaria)
CLI = completar_cif("A", "0054321")   # cliente


def _pdf(ops, *otras_paginas) -> bytes:
    """ops (y las de cada página más): (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _, alto = A4
    for pagina in (ops,) + otras_paginas:
        for op in pagina:
            x, y, s = op[0], op[1], op[2]
            c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 9)
            (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, alto - y, s)
        c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos: bytes):
    ext = campos.Extractor(texto.leer(datos), "f.pdf", EMI).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "f.pdf")


def _validar(ext, datos):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    empresa = {"id": 1, "nif": EMI, "razon_social": "IMPRESIONES FICTICIAS DEMO S.L.",
               "domicilio": "Calle Inventada 7, 28000 Madrid", "ambito_confirmado_en": "x", "ambito_dict": {"puede_emitir": True}}
    documento = {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}
    serie = {"siguiente_numero": 1, "codigo": "X", "numero_detectado": None}
    return borradores.validar(con, empresa, documento, datos, extraccion=ext, serie=serie)


def _cabecera(numero="2601"):
    return [(312, 60, "CLIENTE IMAGINARIO, S.A.", 10, "Helvetica-Bold"), (314, 74, "Avenida Ficticia, 3"),
            (314, 87, "28000 Madrid"), (443, 112, f"C.I.F.: {CLI}"), (356, 160, "Madrid, 5 de octubre de 2026"),
            (206, 190, f"FACTURA NÚMERO: {numero}", 14, "Helvetica-Bold"),
            (107, 790, f"Impresiones Ficticias Demo, S.L. C.I.F.: {EMI} Calle Inventada, 7 28000 Madrid", 7)]


def _totales(y, base, cuota, total, moneda="EURO (€)", rotulo_moneda="MONEDA", x_moneda=450, x_total=369):
    """Cuadro de totales «rótulos en una fila, valores debajo» (el tipo, «21», sin el signo %)."""
    return [(102, y, "BASE IMPONIBLE", 9, "Helvetica-Bold"), (214, y, "% IVA", 9, "Helvetica-Bold"),
            (290, y, "IVA", 9, "Helvetica-Bold"), (369, y, "TOTAL", 9, "Helvetica-Bold"),
            (450, y, rotulo_moneda, 9, "Helvetica-Bold"),
            (155, y + 14, base, 9, "Helvetica", "r"), (232, y + 14, "21", 9, "Helvetica", "r"),
            (313, y + 14, cuota, 9, "Helvetica", "r"), (x_total, y + 14, total), (x_moneda, y + 14, moneda)]


def _factura_revista(**kw_totales):
    """B1 + B3: precio antes de la cantidad, conceptos de dos renglones alineados a la izquierda del rótulo centrado."""
    ops = _cabecera() + [
        (174, 230, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (337, 230, "PRECIO €", 9, "Helvetica-Bold"),
        (394, 230, "CANTIDAD", 9, "Helvetica-Bold"), (461, 230, "IMPORTE", 9, "Helvetica-Bold"),
        (340, 243, "UNIDAD", 9, "Helvetica-Bold"), (459, 243, "EUROS (€)", 9, "Helvetica-Bold"),
        (85, 270, "Revista Demo Nº 101 Enero"), (373, 270, "300,00", 9, "Helvetica", "r"), (421, 270, "1", 9, "Helvetica", "r"),
        (497, 270, "300,00", 9, "Helvetica", "r"),
        (85, 283, "Ficticia Club – MODELO X1"),
        (85, 297, "120 Kilómetros Ida-Vuelta"), (368, 297, "0,25", 9, "Helvetica", "r"), (427, 297, "120", 9, "Helvetica", "r"),
        (495, 297, "30,00", 9, "Helvetica", "r"),
        (85, 311, "Revista Demo Nº 102 Febrero"), (373, 311, "300,00", 9, "Helvetica", "r"), (421, 311, "1", 9, "Helvetica", "r"),
        (497, 311, "300,00", 9, "Helvetica", "r"),
        (85, 324, "Imaginaria Club – MODELO Z9"),
    ]
    # 630,00 + 21 % = 132,30 → 762,30
    return _pdf(ops + _totales(420, "630,00", "132,30", "762,30", **kw_totales))


# --- B1: cuadro de totales «BASE IMPONIBLE | % IVA | IVA | TOTAL | MONEDA» --------------------------------------------
@pytest.mark.parametrize("kw", [
    {},                                                         # moneda en su columna, separada del total
    {"x_moneda": 405},                                          # «762,30 EURO (€)» en un solo tramo de texto
    {"rotulo_moneda": "DIVISA", "moneda": "EUR"},               # otra forma de rotular la moneda
    {"x_moneda": 400, "moneda": "EUROS (€) EUR - EURO COMUNITARIO"},   # tramo del total y la moneda que se sale del cuadro
])
def test_totales_por_columnas_con_tipo_sin_signo_y_moneda(kw):
    ext, d = _extraer(_factura_revista(**kw))
    for k, v in (("base_imponible", "630.00"), ("cuota_iva", "132.30"), ("total_factura", "762.30"), ("tipo_iva", "21.00")):
        assert ext[k]["valor"] == v, (k, ext[k])
        # leído de su columna, no deducido por coherencia aritmética
        assert ext[k]["confianza"] >= borradores.UMBRAL_CONFIANZA, (k, ext[k])
        assert not any("coherencia" in a for a in ext[k]["avisos"]), (k, ext[k]["avisos"])
    assert [(x["tipo_iva"], x["base"], x["cuota"]) for x in d["desglose"]] == [("21.00", "630.00", "132.30")]


def _cuadro_iva(r_tipo, r_cuota, tipo="21"):
    ops = _cabecera() + [
        (60, 230, "Concepto", 9, "Helvetica-Bold"), (330, 230, "Cantidad", 9, "Helvetica-Bold"), (420, 230, "Precio", 9, "Helvetica-Bold"),
        (540, 230, "Importe", 9, "Helvetica-Bold", "r"),
        (60, 248, "Servicio de maquetación ficticio"), (370, 248, "1", 9, "Helvetica", "r"), (460, 248, "500,00", 9, "Helvetica", "r"),
        (540, 248, "500,00", 9, "Helvetica", "r"),
        (102, 420, "BASE IMPONIBLE", 9, "Helvetica-Bold"), (214, 420, r_tipo, 9, "Helvetica-Bold"),
        (290, 420, r_cuota, 9, "Helvetica-Bold"), (369, 420, "TOTAL", 9, "Helvetica-Bold"),
        (155, 434, "500,00", 9, "Helvetica", "r"), (232, 434, tipo, 9, "Helvetica", "r"), (313, 434, "105,00", 9, "Helvetica", "r"),
        (400, 434, "605,00", 9, "Helvetica", "r")]
    ext, _ = _extraer(_pdf(ops))
    return ext


@pytest.mark.parametrize("r_tipo, r_cuota, tipo", [
    ("IVA %", "IVA", "21"),          # el signo detrás del rótulo del tipo
    ("IVA %", "IVA", "21,00"),
    ("% IVA", "IVA", "21 %"),
    ("TIPO IVA", "IVA", "21"),
    ("IVA", "CUOTA IVA", "21 %"),    # sin rótulo de tipo: «IVA» junto a «Cuota IVA» es el tipo (como antes)
])
def test_columna_del_tipo_y_columna_de_la_cuota(r_tipo, r_cuota, tipo):
    ext = _cuadro_iva(r_tipo, r_cuota, tipo)
    for k, v in (("base_imponible", "500.00"), ("tipo_iva", "21.00"), ("cuota_iva", "105.00"), ("total_factura", "605.00")):
        assert ext[k]["valor"] == v, (k, ext[k])
        assert ext[k]["confianza"] >= borradores.UMBRAL_CONFIANZA, (k, ext[k])


def test_dos_columnas_iva_sin_rotulo_de_tipo_se_piden_confirmar():
    """«IVA | IVA» sin «%» ni «Tipo» en ninguna: no se sabe por el rótulo cuál es el tipo; los importes quedan deducidos por
    coherencia aritmética y se piden confirmar (comportamiento previo)."""
    ext = _cuadro_iva("IVA", "IVA")
    assert (ext["base_imponible"]["valor"], ext["cuota_iva"]["valor"], ext["total_factura"]["valor"]) == ("500.00", "105.00", "605.00")
    assert ext["total_factura"]["confianza"] < borradores.UMBRAL_CONFIANZA


# --- B3: conceptos que empiezan a la izquierda del rótulo centrado --------------------------------------------------
def test_concepto_completo_aunque_empiece_a_la_izquierda_del_rotulo():
    ext, d = _extraer(_factura_revista())
    conceptos = [" ".join(l["concepto"].split()) for l in d["lineas"]]
    assert conceptos == ["Revista Demo Nº 101 Enero Ficticia Club – MODELO X1", "120 Kilómetros Ida-Vuelta",
                         "Revista Demo Nº 102 Febrero Imaginaria Club – MODELO Z9"], conceptos
    assert [(l["cantidad"], l["precio"], l["importe"]) for l in d["lineas"]] == \
        [("1", "300.00", "300.00"), ("120", "0.25", "30.00"), ("1", "300.00", "300.00")]


def test_columna_de_codigo_a_la_izquierda_sigue_separada():
    """Con una columna «Código» rotulada a la izquierda de la descripción, el código no se confunde con la cantidad y la
    descripción empieza tras él (sin cambios)."""
    ops = _cabecera() + [
        (50, 230, "Código", 9, "Helvetica-Bold"), (150, 230, "Descripción", 9, "Helvetica-Bold"),
        (330, 230, "Cantidad", 9, "Helvetica-Bold"), (420, 230, "Precio", 9, "Helvetica-Bold"), (540, 230, "Importe", 9, "Helvetica-Bold", "r"),
        (50, 248, "70011"), (95, 248, "Papel ficticio A4 (caja)"), (370, 248, "3", 9, "Helvetica", "r"),
        (460, 248, "20,00", 9, "Helvetica", "r"), (540, 248, "60,00", 9, "Helvetica", "r"),
        (50, 262, "70012"), (95, 262, "Tóner imaginario negro"), (370, 262, "2", 9, "Helvetica", "r"),
        (460, 262, "45,00", 9, "Helvetica", "r"), (540, 262, "90,00", 9, "Helvetica", "r"),
        (380, 320, "Base imponible"), (540, 320, "150,00", 9, "Helvetica", "r"),
        (380, 334, "IVA 21%"), (540, 334, "31,50", 9, "Helvetica", "r"),
        (380, 348, "Total factura"), (540, 348, "181,50", 9, "Helvetica", "r")]
    _, d = _extraer(_pdf(ops))
    assert [(l["concepto"], l["cantidad"], l["precio"], l["importe"]) for l in d["lineas"]] == \
        [("70011 Papel ficticio A4 (caja)", "3", "20.00", "60.00"), ("70012 Tóner imaginario negro", "2", "45.00", "90.00")]


# --- B2: precio por millar y descripción con las cifras en un renglón intermedio -------------------------------------
_DESC = ["Recepción de ficheros del cliente imaginario, adecuación", "de recursos informáticos para impresión. Papel",
         "Offset 80", "Impresión de carta ficticia en negro 1 cara.", "MANIPULADO: plegado de carta, inserción",
         "en sobre de envío, cerrado y entrega en", "Correos.", "Cantidad; 2.450 Envíos"]


def _factura_millar(rotulo_precio="€/MILLAR", precio="95,20", cantidad="2.450", importe="233,24", base="233,24", cuota="48,98",
                    total="282,22", texto_cifras=True, marca_fila=None, desc=None):
    """Una línea: descripción de ocho renglones, las cifras en el tercero (en un solo tramo de texto, como «95,20 2.450
    233,24») y el precio por millar rotulado bajo «PRECIO». 95,20 €/millar × 2.450 envíos = 233,24."""
    desc = desc or _DESC
    ops = _cabecera("2602") + [
        (173, 230, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (336, 230, "PRECIO", 9, "Helvetica-Bold"),
        (393, 230, "CANTIDAD", 9, "Helvetica-Bold"), (461, 230, "IMPORTE", 9, "Helvetica-Bold"),
        (331, 242, rotulo_precio, 9, "Helvetica-Bold"), (460, 242, "TOTAL (€)", 9, "Helvetica-Bold")]
    y = 268
    for k, s in enumerate(desc):
        if k == 2:
            if texto_cifras:
                ops.append((85, y, s))
            ops += [(366, y, precio, 9, "Helvetica", "r"), (427, y, cantidad, 9, "Helvetica", "r"), (495, y, importe, 9, "Helvetica", "r")]
            if marca_fila:
                ops.append((369, y, marca_fila, 7))
        else:
            ops.append((85, y, s))
        y += 13.4 if k != 3 else 26.8   # salto de párrafo dentro de la descripción
    return _pdf(ops + _totales(y + 30, base, cuota, total, x_total=369))


def test_precio_por_millar_y_descripcion_con_cifras_en_medio():
    ext, d = _extraer(_factura_millar())
    assert len(d["lineas"]) == 1, d["lineas"]
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["precio"], lin["importe"], lin["unidad"]) == ("2.45", "95.20", "233.24", "millar")
    assert " ".join(lin["concepto"].split()) == " ".join(" ".join(_DESC).split())
    assert ext["lineas"][0]["cantidad"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    # «… Papel» / «Offset 80»: nada indica que la frase siga en el renglón de las cifras, y lo mismo se vería con un
    # encabezado sin marca encima de un concepto de dos renglones: el concepto completo se propone, pero se pide confirmar
    assert ext["lineas"][0]["concepto"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("renglón intermedio" in a for a in ext["lineas"][0]["concepto"]["avisos"])
    assert ext["base_imponible"]["valor"] == "233.24" and ext["base_imponible"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    codigos = {i["codigo"] for i in _validar(ext, d)}
    assert not codigos & {"LINEA_DESCUADRE", "LINEAS_DESCUADRE", "SIN_LINEAS"}, codigos


@pytest.mark.parametrize("kw, cantidad, unidad", [
    ({"rotulo_precio": "€/100 UDS", "precio": "9,52", "cantidad": "2.450", "importe": "233,24"}, "24.5", "centenar"),
    ({"rotulo_precio": "POR MIL", "precio": "95,20"}, "2.45", "millar"),
    ({"rotulo_precio": "UNITARIO", "marca_fila": "‰"}, "2.45", "millar"),          # la unidad junto al precio de la fila
    ({"rotulo_precio": "UNITARIO", "marca_fila": "€/millar"}, "2.45", "millar"),
    ({"texto_cifras": False}, "2.45", "millar"),                                    # renglón de solo cifras en medio
])
def test_precio_por_lote_variantes(kw, cantidad, unidad):
    _, d = _extraer(_factura_millar(**kw))
    assert len(d["lineas"]) == 1, d["lineas"]
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["unidad"], lin["importe"]) == (cantidad, unidad, "233.24"), lin
    assert "Recepción de ficheros" in lin["concepto"] and "Correos." in lin["concepto"], lin["concepto"]


@pytest.mark.parametrize("kw, unidad", [
    ({}, None),                                                  # marca solo en el rótulo: puede ser un importe fijo
    ({"rotulo_precio": "UNITARIO", "marca_fila": "€/millar"}, "millar"),   # marca en la propia fila
])
def test_precio_por_millar_con_cantidad_ya_en_millares(kw, unidad):
    """Precio por millar con la cantidad impresa ya en millares (precio × cantidad = importe): no se convierte nada; la
    unidad solo se anota si la marca va en la propia fila."""
    _, d = _extraer(_factura_millar(cantidad="2,450", **kw))
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["precio"], lin["importe"], lin["unidad"]) == ("2.450", "95.20", "233.24", unidad)


def test_sin_unidad_por_lote_no_se_reinterpreta_la_cantidad():
    """Sin rótulo de millar ni centenar no se reinterpreta nada: el descuadre queda para la validación."""
    ext, d = _extraer(_factura_millar(rotulo_precio="UNITARIO"))
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["unidad"]) == ("2450", None)
    assert "LINEA_DESCUADRE" in {i["codigo"] for i in _validar(ext, d)}


def test_porcentaje_no_es_precio_por_centenar():
    """«%» o «por ciento» no son «por centenar»."""
    assert not campos.Extractor.RE_POR_CIEN.search("PRECIO % DTO")
    assert not campos.Extractor.RE_POR_CIEN.search("descuento del 10 por ciento")
    assert not campos.Extractor.RE_POR_MIL.search("€/ml") and not campos.Extractor.RE_POR_MIL.search("precio/milímetro")
    assert campos.Extractor.RE_POR_MIL.search("€/1.000 uds") and campos.Extractor.RE_POR_CIEN.search("€/100 uds")


def test_encabezado_de_grupo_encima_del_primer_concepto_no_es_su_descripcion():
    """Renglón de encabezado («Pedido nº …») justo encima de la primera línea, con las cifras en el primer renglón de cada
    concepto: no se antepone al concepto de la primera línea."""
    ops = _cabecera() + [
        (60, 230, "Descripción", 9, "Helvetica-Bold"), (330, 230, "Cantidad", 9, "Helvetica-Bold"),
        (420, 230, "Precio", 9, "Helvetica-Bold"), (540, 230, "Importe", 9, "Helvetica-Bold", "r"),
        (60, 246, "Pedido nº 00/123 de 01.10.2026"),
        (60, 258, "Carpetas ficticias de cartón"), (370, 258, "10", 9, "Helvetica", "r"), (460, 258, "2,00", 9, "Helvetica", "r"),
        (540, 258, "20,00", 9, "Helvetica", "r"),
        (60, 270, "Sobres imaginarios C5"), (370, 270, "100", 9, "Helvetica", "r"), (460, 270, "0,10", 9, "Helvetica", "r"),
        (540, 270, "10,00", 9, "Helvetica", "r"),
        (380, 320, "Base imponible"), (540, 320, "30,00", 9, "Helvetica", "r"),
        (380, 334, "IVA 21%"), (540, 334, "6,30", 9, "Helvetica", "r"),
        (380, 348, "Total factura"), (540, 348, "36,30", 9, "Helvetica", "r")]
    _, d = _extraer(_pdf(ops))
    assert [l["concepto"] for l in d["lineas"]] == ["Carpetas ficticias de cartón", "Sobres imaginarios C5"]


def test_varias_lineas_con_cifras_en_medio_se_piden_confirmar():
    """Con las cifras en un renglón intermedio y varios conceptos, los renglones entre las cifras de dos líneas pueden ser de
    una o de la otra: se asignan a la anterior y se pide confirmar los conceptos."""
    ops = _cabecera() + [
        (173, 230, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (336, 230, "PRECIO", 9, "Helvetica-Bold"),
        (393, 230, "CANTIDAD", 9, "Helvetica-Bold"), (461, 230, "IMPORTE", 9, "Helvetica-Bold"),
        (460, 242, "TOTAL (€)", 9, "Helvetica-Bold"),
        (85, 268, "Diseño de folleto ficticio"), (85, 281.4, "a dos caras"), (366, 281.4, "120,00", 9, "Helvetica", "r"),
        (427, 281.4, "1", 9, "Helvetica", "r"), (495, 281.4, "120,00", 9, "Helvetica", "r"), (85, 294.8, "con dos revisiones"),
        (85, 308.2, "Impresión de folleto imaginario"), (85, 321.6, "en papel estucado"), (366, 321.6, "0,50", 9, "Helvetica", "r"),
        (427, 321.6, "200", 9, "Helvetica", "r"), (495, 321.6, "100,00", 9, "Helvetica", "r"), (85, 335, "de 135 gramos")]
    ext, _ = _extraer(_pdf(ops + _totales(400, "220,00", "46,20", "266,20")))
    assert [l["importe"]["valor"] for l in ext["lineas"]] == ["120.00", "100.00"]
    assert ext["lineas"][0]["concepto"]["valor"].startswith("Diseño de folleto ficticio")
    for l in ext["lineas"]:
        assert l["concepto"]["confianza"] < borradores.UMBRAL_CONFIANZA
        assert any("cifras en uno intermedio" in a for a in l["concepto"]["avisos"])


def test_pdf_final_muestra_la_cantidad_con_sus_decimales():
    """La cantidad en millares (2,45 de un precio por millar) se imprime con sus decimales en el PDF compuesto: con dos
    decimales, 2,45 × 95,20 no daría el importe; las cantidades con dos decimales o menos se imprimen como antes."""
    from app import pdf_final
    assert pdf_final._cant("2.45") == "2,45" and pdf_final._cant("1.258") == "1,258"
    assert pdf_final._cant("1234.5678") == "1.234,5678" and pdf_final._cant("-0.125") == "-0,125"
    assert pdf_final._cant("3") == "3,00" and pdf_final._cant("2.5") == "2,50" and pdf_final._cant("1258") == "1.258,00"
    assert pdf_final._cant(None) == "" and pdf_final._cant("abc") == "" and pdf_final._cant("Infinity") == ""


# --- Revisión F1/F2: renglones de texto encima de las cifras de la primera línea --------------------------------------
_CAB_TABLA = [(60, 230, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (330, 230, "CANTIDAD", 9, "Helvetica-Bold"),
              (420, 230, "PRECIO", 9, "Helvetica-Bold"), (540, 230, "IMPORTE", 9, "Helvetica-Bold", "r")]


def _totales_en_lineas(y, base, cuota, total):
    return [(380, y, "Base imponible"), (540, y, base, 9, "Helvetica", "r"), (380, y + 14, "IVA 21%"),
            (540, y + 14, cuota, 9, "Helvetica", "r"), (380, y + 28, "Total factura"), (540, y + 28, total, 9, "Helvetica", "r")]


def _tabla(renglones, totales, y=256, cabecera=None, con_cabecera_factura=True):
    """renglones: un texto (renglón de la descripción) o (texto, cantidad, precio, importe) (renglón con las cifras); uno
    cada 13,4 pt, sin salto de párrafo. totales: (base, cuota, total)."""
    ops = (_cabecera() if con_cabecera_factura else []) + list(cabecera or _CAB_TABLA)
    for r in renglones:
        if isinstance(r, tuple):
            t, q, p, i = r
            if t:
                ops.append((60, y, t))
            ops += [(370, y, q, 9, "Helvetica", "r"), (460, y, p, 9, "Helvetica", "r"), (540, y, i, 9, "Helvetica", "r")]
        else:
            ops.append((60, y, r))
        y += 13.4
    return ops + (_totales_en_lineas(y + 30, *totales) if totales else [])


_CARPETAS = [("Carpetas ficticias de cartón", "10", "2,00", "20,00"), "con solapa y gomas"]


@pytest.mark.parametrize("encabezado", [
    "Pedido nº 00/123 de 01.10.2026",
    "TRABAJOS DE IMPRESIÓN:",
    "Rectifica la factura nº 2599 de 01.09.2026",
    "S/Pedido: 4500012345",
    "MATERIAL DE OFICINA",
])
def test_encabezado_encima_de_un_concepto_de_dos_renglones_no_es_su_descripcion(encabezado):
    """Encabezado de grupo, referencia o título de sección justo encima de la única línea, con las cifras en el primer
    renglón de su concepto y la descripción siguiendo debajo: no se antepone al concepto, que queda sin aviso."""
    ext, d = _extraer(_pdf(_tabla([encabezado] + _CARPETAS, ("20,00", "4,20", "24,20"))))
    assert len(ext["lineas"]) == 1, ext["lineas"]
    c = ext["lineas"][0]["concepto"]
    assert c["valor"] == "Carpetas ficticias de cartón\ncon solapa y gomas", c
    assert c["confianza"] >= borradores.UMBRAL_CONFIANZA and not c["avisos"], c


@pytest.mark.parametrize("encabezado", ["Albarán nº 4567 de 02.10.2026", "Según presupuesto nº 26/014 aceptado el 01.10.2026:"])
def test_encabezado_encima_de_varias_lineas_de_dos_renglones(encabezado):
    """Varias líneas con las cifras en el primer renglón y un renglón más debajo, bajo un encabezado: ni se antepone el
    encabezado ni se piden confirmar los conceptos (nada es ambiguo)."""
    renglones = [encabezado] + _CARPETAS + [("Sobres imaginarios C5", "100", "0,10", "10,00"), "blancos autoadhesivos"]
    ext, _ = _extraer(_pdf(_tabla(renglones, ("30,00", "6,30", "36,30"))))
    assert [l["concepto"]["valor"] for l in ext["lineas"]] == \
        ["Carpetas ficticias de cartón\ncon solapa y gomas", "Sobres imaginarios C5\nblancos autoadhesivos"]
    for l in ext["lineas"]:
        assert l["concepto"]["confianza"] >= borradores.UMBRAL_CONFIANZA and not l["concepto"]["avisos"], l["concepto"]


def test_renglon_encima_sin_forma_de_encabezado_se_pide_confirmar():
    """«Material de oficina» encima de un concepto de dos renglones: igual podría ser el comienzo de la descripción (cifras en
    un renglón intermedio) que un encabezado sin marca; se propone como comienzo del concepto, pero se pide confirmar."""
    ext, _ = _extraer(_pdf(_tabla(["Material de oficina"] + _CARPETAS, ("20,00", "4,20", "24,20"))))
    c = ext["lineas"][0]["concepto"]
    assert c["valor"].endswith("Carpetas ficticias de cartón\ncon solapa y gomas"), c
    assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("renglón intermedio" in a for a in c["avisos"]), c


@pytest.mark.parametrize("renglones, totales, concepto", [
    # el renglón de las cifras empieza en minúscula: la frase sigue
    (["Diseño de cartel ficticio", ("formato A2", "1", "180,00", "180,00"), "con dos revisiones"],
     ("180,00", "37,80", "217,80"), "Diseño de cartel ficticio\nformato A2\ncon dos revisiones"),
    # el renglón de encima acaba en preposición
    (["Impresión de catálogo ficticio de", ("64 páginas en papel estucado", "200", "3,50", "700,00"), "Encuadernación rústica"],
     ("700,00", "147,00", "847,00"), "Impresión de catálogo ficticio de\n64 páginas en papel estucado\nEncuadernación rústica"),
    # cifras en el último renglón del concepto
    (["Diseño de logotipo ficticio", "con tres propuestas", ("y manual básico", "1", "300,00", "300,00")],
     ("300,00", "63,00", "363,00"), "Diseño de logotipo ficticio\ncon tres propuestas\ny manual básico"),
])
def test_cifras_en_un_renglon_intermedio_con_la_frase_que_sigue(renglones, totales, concepto):
    ext, _ = _extraer(_pdf(_tabla(renglones, totales)))
    assert len(ext["lineas"]) == 1, ext["lineas"]
    c = ext["lineas"][0]["concepto"]
    assert c["valor"] == concepto, c
    assert c["confianza"] >= borradores.UMBRAL_CONFIANZA and not c["avisos"], c


@pytest.mark.parametrize("renglones, concepto", [
    (["Impresión de catálogo ficticio", ("Papel estucado de 135 g", "200", "3,50", "700,00"), "Encuadernación rústica"],
     "Impresión de catálogo ficticio\nPapel estucado de 135 g\nEncuadernación rústica"),
    # «…:» seguido de minúscula es parte de la frase, pero tiene forma de encabezado: se pide confirmar
    (["Diseño de folleto ficticio que incluye:", ("maquetación y dos revisiones", "200", "3,50", "700,00"), "del texto imaginario"],
     "Diseño de folleto ficticio que incluye:\nmaquetación y dos revisiones\ndel texto imaginario"),
])
def test_cifras_en_un_renglon_intermedio_sin_indicio_claro_se_pide_confirmar(renglones, concepto):
    ext, _ = _extraer(_pdf(_tabla(renglones, ("700,00", "147,00", "847,00"))))
    c = ext["lineas"][0]["concepto"]
    assert c["valor"] == concepto, c
    assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("renglón intermedio" in a for a in c["avisos"]), c


def test_linea_en_otra_pagina_tras_cifras_en_medio_no_se_pide_confirmar():
    """Las cifras de la primera línea en un renglón intermedio y una línea de un solo renglón en la página siguiente (tras la
    cabecera repetida): los renglones de la primera página no pueden ser de la segunda línea; esta no se pide confirmar."""
    p1 = _tabla(["Impresión de catálogo ficticio", ("64 páginas", "200", "3,50", "700,00"), "tapa blanda"], None)
    p1 += [(300, 780, "Sigue en la página siguiente")]
    cab2 = [(x, 60, *resto) for x, _, *resto in _CAB_TABLA]
    p2 = _tabla([("Portes a destino imaginario", "1", "25,00", "25,00")], ("725,00", "152,25", "877,25"), y=86,
                cabecera=cab2, con_cabecera_factura=False)
    ext, _ = _extraer(_pdf(p1, p2))
    assert [l["importe"]["valor"] for l in ext["lineas"]] == ["700.00", "25.00"]
    assert ext["lineas"][0]["concepto"]["valor"] == "Impresión de catálogo ficticio\n64 páginas\ntapa blanda"
    c = ext["lineas"][1]["concepto"]
    assert c["valor"] == "Portes a destino imaginario" and c["confianza"] >= borradores.UMBRAL_CONFIANZA and not c["avisos"], c


# --- Revisión F3: marca del millar sola en el segundo renglón de la cabecera ------------------------------------------
_CAB_PRECIO_ANTES = [(60, 230, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (330, 230, "PRECIO", 9, "Helvetica-Bold"),
                     (400, 230, "CANTIDAD", 9, "Helvetica-Bold"), (540, 230, "IMPORTE", 9, "Helvetica-Bold", "r")]


def _factura_marca_en_cabecera(marca, desc=("Impresión de tarjetas imaginarias",), fila_cifras=0, marca_x=325):
    """31,00 € el millar × 4.000 = 124,00; la marca de la unidad del precio sola en un segundo renglón bajo «PRECIO»."""
    ops = _cabecera() + _CAB_PRECIO_ANTES + [(marca_x, 242, marca, 9, "Helvetica-Bold")]
    y = 268
    for k, s in enumerate(desc):
        ops.append((60, y, s))
        if k == fila_cifras:
            ops += [(365, y, "31,00", 9, "Helvetica", "r"), (440, y, "4.000", 9, "Helvetica", "r"), (540, y, "124,00", 9, "Helvetica", "r")]
        y += 13.4
    return _pdf(ops + _totales_en_lineas(y + 30, "124,00", "26,04", "150,04"))


@pytest.mark.parametrize("marca", ["€/MILLAR", "€/1000", "(€/1.000 uds)", "(por mil)", "‰", "por millar", "(€/millar)",
                                   "MILLAR", "€ / millar"])
def test_marca_del_millar_en_el_segundo_renglon_de_la_cabecera(marca):
    ext, d = _extraer(_factura_marca_en_cabecera(marca))
    assert len(d["lineas"]) == 1, d["lineas"]
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["precio"], lin["importe"], lin["unidad"]) == ("4", "31.00", "124.00", "millar"), lin
    assert lin["concepto"] == "Impresión de tarjetas imaginarias"
    assert not {i["codigo"] for i in _validar(ext, d)} & {"LINEA_DESCUADRE", "LINEAS_DESCUADRE"}


def test_marca_del_millar_en_la_cabecera_y_cifras_en_un_renglon_intermedio():
    """El segundo renglón de cabecera no es el comienzo del concepto, aunque las cifras vayan en un renglón intermedio."""
    ext, d = _extraer(_factura_marca_en_cabecera(
        "(€/1.000 uds)", desc=("Impresión de tarjetas imaginarias", "a dos caras en cartulina", "de 350 gramos"), fila_cifras=1,
        marca_x=320))
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["unidad"], lin["importe"]) == ("4", "millar", "124.00"), lin
    assert ext["lineas"][0]["concepto"]["valor"] == "Impresión de tarjetas imaginarias\na dos caras en cartulina\nde 350 gramos"


def test_segundo_renglon_de_cabecera_sin_marca_no_cambia_la_linea():
    """«(detalle del trabajo)» bajo la descripción y «(€)» bajo el importe: cabecera, sin unidad por lote."""
    ops = _tabla([("Diseño de cartel ficticio", "1", "180,00", "180,00"), "formato A2 con dos revisiones"],
                 ("180,00", "37,80", "217,80"), y=268,
                 cabecera=_CAB_TABLA + [(60, 242, "(detalle del trabajo)", 9, "Helvetica-Bold"), (530, 242, "(€)", 9, "Helvetica-Bold")])
    ext, d = _extraer(_pdf(ops))
    assert len(d["lineas"]) == 1, d["lineas"]
    lin = d["lineas"][0]
    assert (lin["cantidad"], lin["precio"], lin["importe"], lin["unidad"]) == ("1", "180.00", "180.00", None), lin
    c = ext["lineas"][0]["concepto"]
    assert c["valor"] == "Diseño de cartel ficticio\nformato A2 con dos revisiones" and c["confianza"] >= borradores.UMBRAL_CONFIANZA


# --- Revisión F4: conceptos cortos a la izquierda de un rótulo «DESCRIPCIÓN» centrado ----------------------------------
@pytest.mark.parametrize("con_numero_de_orden", [False, True])
def test_conceptos_cortos_a_la_izquierda_del_rotulo_centrado(con_numero_de_orden):
    """Ningún concepto llega bajo el rótulo centrado: el margen de la descripción es donde se alinea el último tramo de texto
    antes de las cifras; un número de orden sin rótulo más a la izquierda no forma parte del concepto."""
    ops = _cabecera() + [(180, 230, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (340, 230, "PRECIO", 9, "Helvetica-Bold"),
                         (400, 230, "CANTIDAD", 9, "Helvetica-Bold"), (540, 230, "IMPORTE", 9, "Helvetica-Bold", "r")]
    filas = [("Cartel ficticio A3", "15,00", "4", "60,00"), ("Folleto imaginario", "0,50", "200", "100,00"),
             ("Lona inventada", "40,00", "1", "40,00")]
    y = 256
    for n, (t, p, q, i) in enumerate(filas, 1):
        if con_numero_de_orden:
            ops += [(45, y, str(n)), (85, y, t)]
        else:
            ops.append((70, y, t))
        ops += [(375, y, p, 9, "Helvetica", "r"), (440, y, q, 9, "Helvetica", "r"), (540, y, i, 9, "Helvetica", "r")]
        y += 17.4
    ext, d = _extraer(_pdf(ops + _totales_en_lineas(y + 30, "200,00", "42,00", "242,00")))
    assert [(l["cantidad"], l["precio"], l["importe"]) for l in d["lineas"]] == \
        [(q, p.replace(",", "."), i.replace(",", ".")) for _, p, q, i in filas]
    for n, (l, (t, *_)) in enumerate(zip(d["lineas"], filas), 1):
        # el concepto entero; el número de orden puede ir delante si el lector lo une al texto en un solo tramo
        assert re.fullmatch(rf"(?:{n} )?{re.escape(t)}", l["concepto"]), l["concepto"]
    assert not {i["codigo"] for i in _validar(ext, d)} & {"DESC_FALTA", "LINEAS_DESCUADRE", "LINEA_DESCUADRE"}
