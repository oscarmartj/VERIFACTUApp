"""Regresiones de FIS-2 / FIX-2 (revisión integrada final): factura mixta separada sin respaldo en el documento.

Un SUBTOTAL, una SUMA o una «base total» igual a la suma de las líneas no prueba que la diferencia sin IVA sea una base
exenta: solo dice que no lleva IVA. El borrador solo propone esa base exenta (con la causa del artículo citado) si el
documento la respalda: una base exenta impresa igual a ella, o las líneas de la operación exenta (las que dicen
«exento/a» o comparten el objeto de la frase de la exención) suman exactamente la diferencia y las demás líneas, las bases
sujetas. Con tasas, fianzas, indemnizaciones, provisiones de fondos, gastos por cuenta del cliente o repercutidos, IBI o
basuras en el documento, nunca se separa. Si no, el desglose no cambia y el borrador sigue bloqueado (descuadre). La
confirmación EXENTA_CAUSA dice de qué líneas sale el importe y que es una diferencia calculada.

Reparación (G3V-1): el indicio «importes_ajenos» se busca con las mismas expresiones con las que la extracción propone la
causa («exentos», «Art 20», «artículos 20 y siguientes»), no solo con las del indicio «exencion»; y una línea de conceptos
con un importe ajeno nunca respalda la base exenta, aunque el indicio falte. (G3V-3) En una factura sin IVA (toda la base
exenta), la confirmación EXENTA_CAUSA avisa del renglón con el importe ajeno.

Todos los datos son ficticios (NIF con código de provincia 00). Solo extracción y validación locales.
"""
import pytest

from app import borradores
from app.extraccion import campos
from test_16_x_exentas import codigos, extraer, factura_pdf, sujeta, validar

LOCAL = ("Alquiler local comercial C/ Inventada 3, noviembre", "1,00", "mes", "100,00", "100,00")
PIE_VIVIENDA = ["Los arrendamientos de vivienda están exentos de IVA (art. 20.Uno.23º Ley 37/1992)."]
T_SUBTOTAL_130 = [("SUBTOTAL", "130,00"), ("BASE IMPONIBLE 21%", "100,00"), ("I.V.A. 21%", "21,00"), ("TOTAL FACTURA", "151,00")]
SEGURO = ["Seguro exento de IVA (art. 20.Uno.16º Ley 37/1992)."]
T_SUBTOTAL_750 = [("SUBTOTAL", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "771,00")]
LINEAS_SEGURO = [("Seguro de la instalación", "1,00", "ud", "50,00", "50,00"), ("Mantenimiento", "1,00", "ud", "100,00", "100,00")]
LINEAS_MIXTA = [("Alquiler de la vivienda, noviembre 2026", "1,00", "mes", "650,00", "650,00"),
                ("Plaza de garaje, noviembre 2026", "1,00", "mes", "100,00", "100,00")]
NOTA_ALQUILER = ["Alquiler de vivienda exento de IVA (art. 20.Uno.23º Ley 37/1992)."]
LINEAS_ACADEMIA = [("Curso de inglés noviembre", "1,00", "mes", "650,00", "650,00"),
                   ("Libro de texto", "1,00", "ud", "100,00", "100,00")]
NOTA_ACADEMIA = ["Enseñanza exenta de IVA (art. 20.Uno.9º Ley 37/1992)."]


def _sin_base_exenta(lineas, totales, notas):
    ext, d = extraer(factura_pdf("007/A26", "10/11/2026", lineas, totales, notas=notas))
    assert not [x for x in d["desglose"] if borradores.clase_fila(x) in ("exenta", "sin_calificar")], d["desglose"]
    inc = validar(d, ext)
    assert {"LINEAS_DESCUADRE", "TOTAL_DESCUADRE"} & codigos(inc, "bloqueo"), sorted(codigos(inc, "bloqueo"))
    assert not borradores.puede_emitir(inc)
    return ext, d


# --- Trampas de los revisores: no se separa ninguna base exenta ---------------------------------------------------------------
@pytest.mark.parametrize("lineas, notas", [
    # FIS-2: alquiler de un local (sujeto) y una tasa de basuras repercutida; el pie de la plantilla cita el art. 20 para
    # las viviendas y el cuadro imprime un SUBTOTAL (130 = suma de las líneas)
    ([LOCAL, ("Tasa municipal de basuras repercutida", "1,00", "ud", "30,00", "30,00")], PIE_VIVIENDA),
    ([("Alquiler local comercial", "1,00", "mes", "100,00", "100,00"),
      ("Tasa de basuras repercutida", "1,00", "ud", "30,00", "30,00")], ["Alquiler de vivienda exento de IVA (art. 20.Uno.23º Ley 37/1992)."]),
    ([LOCAL, ("IBI repercutido 2026 (parte proporcional)", "1,00", "ud", "30,00", "30,00")], PIE_VIVIENDA),
    # la trampa de los portes de test_x_r3 con un SUBTOTAL añadido
    ([LOCAL, ("Portes", "1,00", "ud", "30,00", "30,00")], PIE_VIVIENDA),
], ids=["subtotal_tasa", "subtotal_tasa_s4", "subtotal_ibi", "portes_subtotal"])
def test_fis2_subtotal_no_respalda_la_base_exenta(lineas, notas):
    ext, d = _sin_base_exenta(lineas, T_SUBTOTAL_130, notas)
    assert d["desglose"] == [sujeta("21.00", "100.00", "21.00")]


@pytest.mark.parametrize("concepto", ["Tasa municipal de basuras", "Tasa pagada por cuenta del cliente",
                                      "Indemnización por daños", "IBI repercutido", "Gastos de notaría (provisión de fondos)",
                                      "Fianza"])
def test_fix2_importe_ajeno_no_se_convierte_en_base_exenta(concepto):
    """FIX-2: un seguro exento (50) citado en una nota no hace exenta la línea de 600 sin IVA (tasa, indemnización, IBI,
    provisión de fondos, fianza): «SUBTOTAL 750 / BASE 21 % 100 / IVA 21 % 21 / TOTAL 771»."""
    ext, _ = _sin_base_exenta(LINEAS_SEGURO + [(concepto, "1,00", "ud", "600,00", "600,00")], T_SUBTOTAL_750, SEGURO)
    assert "importes_ajenos" in ext["indicios"]


@pytest.mark.parametrize("lineas, totales", [
    # ninguna línea es de la operación exenta: la nota del seguro no cubre nada de la factura
    ([("Mantenimiento", "1,00", "ud", "100,00", "100,00"), ("Indemnización por daños", "1,00", "ud", "650,00", "650,00")],
     T_SUBTOTAL_750),
    ([("Mantenimiento", "1,00", "ud", "100,00", "100,00"), ("Reparación de la puerta", "1,00", "ud", "650,00", "650,00")],
     [("BASE IMPONIBLE", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "771,00")]),
    # conceptos sin indicio: la única línea de la operación exenta (el seguro, 50) no suma la diferencia (650)
    (LINEAS_SEGURO + [("Mobiliario", "1,00", "ud", "600,00", "600,00")], T_SUBTOTAL_750),
    (LINEAS_SEGURO + [("Gastos de comunidad", "1,00", "ud", "600,00", "600,00")], T_SUBTOTAL_750),
])
def test_fix2_lineas_que_no_respaldan_la_diferencia(lineas, totales):
    _sin_base_exenta(lineas, totales, SEGURO)


def test_fix2_portes_con_subtotal_en_alquiler_de_vivienda():
    """La vivienda (650) sí es de la operación exenta, pero la diferencia (680) incluye unos portes: no cuadra."""
    _sin_base_exenta(LINEAS_MIXTA + [("Portes", "1,00", "ud", "30,00", "30,00")],
                     [("SUBTOTAL", "780,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "801,00")],
                     NOTA_ALQUILER)


# --- Los diseños correctos se siguen separando, con el origen en la confirmación --------------------------------------------
@pytest.mark.parametrize("lineas, totales, notas, tipo, cuota, total, impresa", [
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "771,00")],
     NOTA_ALQUILER, "21.00", "21.00", "771.00", False),
    (LINEAS_MIXTA, [("BASE EXENTA", "650,00"), ("BASE IMPONIBLE 21%", "100,00"), ("CUOTA IVA 21%", "21,00"),
                    ("TOTAL BASE IMPONIBLE", "750,00"), ("TOTAL FACTURA", "771,00")], NOTA_ALQUILER, "21.00", "21.00", "771.00", True),
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("I.V.A. 21% s/ 100,00", "21,00"), ("TOTAL FACTURA", "771,00")],
     NOTA_ALQUILER, "21.00", "21.00", "771.00", False),
    # un SUBTOTAL con las líneas de la vivienda que suman exactamente la diferencia: sí
    (LINEAS_MIXTA, T_SUBTOTAL_750, NOTA_ALQUILER, "21.00", "21.00", "771.00", False),
    (LINEAS_ACADEMIA, [("BASE IMPONIBLE", "750,00"), ("IVA 4% s/ 100,00", "4,00"), ("TOTAL FACTURA", "754,00")],
     NOTA_ACADEMIA, "4.00", "4.00", "754.00", False),
    (LINEAS_ACADEMIA, [("BASE EXENTA", "650,00"), ("BASE 4%", "100,00"), ("IVA 4%", "4,00"), ("TOTAL FACTURA", "754,00")],
     NOTA_ACADEMIA, "4.00", "4.00", "754.00", True),
], ids=["vivienda_base_imponible", "vivienda_base_exenta", "vivienda_iva_sobre", "vivienda_subtotal", "academia_base_imponible",
        "academia_base_exenta"])
def test_disenos_correctos_se_separan_y_la_confirmacion_dice_de_donde_sale(lineas, totales, notas, tipo, cuota, total, impresa):
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", lineas, totales, notas=notas))
    assert d["desglose"] == [sujeta(tipo, "100.00", cuota), borradores.fila_exenta("650.00", "E1", ext["exencion"]["motivo"])]
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("750.00", cuota, total)
    inc = validar(d, ext)
    assert not codigos(inc, "bloqueo"), [i for i in inc if i["nivel"] == "bloqueo"]
    conf = next(i for i in inc if i["codigo"] == "EXENTA_CAUSA")
    assert "se ha calculado como diferencia" in conf["mensaje"]
    assert f"corresponde a la línea «{lineas[0][0]}» (650.00)" in conf["mensaje"]
    assert ("coincide con la base exenta impresa" in conf["mensaje"]) == impresa
    assert not borradores.puede_emitir(inc)   # la causa se confirma siempre


def test_origen_solo_para_la_base_propuesta_por_diferencia():
    """La frase del origen solo acompaña a la base propuesta como diferencia: si el usuario la cambia, no se cita una cifra
    que ya no es; en una factura sin IVA (toda la base exenta) no hay diferencia que explicar."""
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEAS_MIXTA,
                                 [("BASE IMPONIBLE", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"),
                                  ("TOTAL FACTURA", "771,00")], notas=NOTA_ALQUILER))
    assert "diferencia" in next(i for i in validar(d, ext) if i["codigo"] == "EXENTA_CAUSA")["mensaje"]
    d["desglose"][1]["base"] = "640.00"
    assert "diferencia" not in next(i for i in validar(d, ext) if i["codigo"] == "EXENTA_CAUSA")["mensaje"]
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", [LINEAS_MIXTA[0]],
                                 [("BASE IMPONIBLE", "650,00"), ("TOTAL FACTURA", "650,00")], notas=NOTA_ALQUILER))
    assert [borradores.clase_fila(x) for x in d["desglose"]] == ["exenta"]
    assert "diferencia" not in next(i for i in validar(d, ext) if i["codigo"] == "EXENTA_CAUSA")["mensaje"]


# --- Indicio «importes_ajenos» -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("texto, ajeno", [
    ("Tasa municipal de basuras", True), ("Tasas de examen", True), ("Fianza (dos mensualidades)", True),
    ("Indemnización por daños", True), ("Provisión de fondos para notaría", True),
    ("Gastos pagados por cuenta del cliente", True), ("IBI repercutido", True), ("I.B.I. 2026", True),
    ("Impuesto sobre Bienes Inmuebles", True), ("Gastos de comunidad repercutidos", True), ("Recogida de basura", True),
    ("IVA repercutido 21%", False), ("Cuota repercutida", False), ("Alquiler de la vivienda", False),
    ("Ibi (Alicante)", False), ("Mantenimiento", False),
])
def test_indicio_importes_ajenos(texto, ajeno):
    assert campos.Extractor._importe_ajeno(texto) is ajeno


def test_indicio_solo_si_se_menciona_una_exencion():
    """El indicio se busca solo si el documento menciona una exención (es lo único que cambia); sin ella, la extracción no
    varía."""
    lineas = [LOCAL, ("Tasa de basuras repercutida", "1,00", "ud", "30,00", "30,00")]
    ext, _ = extraer(factura_pdf("007/A26", "10/11/2026", lineas, T_SUBTOTAL_130, notas=PIE_VIVIENDA))
    assert {"exencion", "importes_ajenos"} <= set(ext["indicios"])
    assert "Tasa de basuras" in ext["indicios"]["importes_ajenos"]["texto"]
    ext, _ = extraer(factura_pdf("007/A26", "10/11/2026", lineas, T_SUBTOTAL_130, notas=[]))
    assert "exencion" not in ext["indicios"] and "importes_ajenos" not in ext["indicios"]


# --- G3V-1: redacciones de la exención que el indicio «exencion» no reconoce -----------------------------------------------
VIVIENDA_500 = ("Alquiler vivienda C/ Ficticia 5, noviembre", "1,00", "mes", "500,00", "500,00")
NOTAS_G3V1 = {
    "plural_Art_20": "Los arrendamientos de vivienda están exentos de IVA (Art 20.Uno.23º Ley 37/1992).",
    "articulos_20": "Arrendamientos de vivienda exentos de IVA según artículos 20 y siguientes de la Ley 37/1992.",
    "ley_art_20": "Operaciones exentas de IVA, Ley 37/1992 art 20.Uno.23",
    "singular_art_punto": "Los arrendamientos de vivienda están exentos de IVA (art. 20.Uno.23º Ley 37/1992).",
}


@pytest.mark.parametrize("nota", list(NOTAS_G3V1.values()), ids=list(NOTAS_G3V1))
@pytest.mark.parametrize("concepto", ["Fianza vivienda (dos mensualidades)", "Indemnización vivienda",
                                      "Tasa de basuras vivienda", "Depósito de garantía vivienda"])
def test_g3v1_importe_ajeno_con_cualquier_redaccion_de_la_exencion(nota, concepto):
    """«Alquiler local» 100 (21 %) + «Alquiler vivienda» 500 + una fianza (o indemnización, tasa, depósito de garantía) de
    150 que nombra la vivienda; SUBTOTAL 750 / BASE 21 % 100 / IVA 21 / TOTAL 771: nunca una base exenta de 650."""
    ext, d = _sin_base_exenta([LOCAL, VIVIENDA_500, (concepto, "1,00", "ud", "150,00", "150,00")], T_SUBTOTAL_750, [nota])
    assert ext["exencion"]["causa"] == "E1"
    assert "importes_ajenos" in ext["indicios"] and concepto.split()[0] in ext["indicios"]["importes_ajenos"]["texto"]
    assert d["desglose"] == [sujeta("21.00", "100.00", "21.00")]


@pytest.mark.parametrize("nota", list(NOTAS_G3V1.values()), ids=list(NOTAS_G3V1))
def test_g3v1_la_linea_ajena_no_respalda_aunque_falte_el_indicio(nota):
    """Defensa en el borrador: aunque la extracción no traiga el indicio (p. ej. guardada por otra versión), una línea de
    conceptos con una fianza no forma parte de la base exenta."""
    ext, _ = extraer(factura_pdf("007/A26", "10/11/2026", [LOCAL, VIVIENDA_500,
                                                           ("Fianza vivienda", "1,00", "ud", "150,00", "150,00")],
                                 T_SUBTOTAL_750, notas=[nota]))
    ext["indicios"].pop("importes_ajenos", None)
    d = borradores.datos_desde_extraccion(ext)
    assert [borradores.clase_fila(x) for x in d["desglose"]] == ["S1"]
    assert borradores.origen_base_exenta(ext) is None


@pytest.mark.parametrize("nota", list(NOTAS_G3V1.values()), ids=list(NOTAS_G3V1))
def test_g3v1_el_diseno_correcto_se_sigue_separando(nota):
    """Sin importes ajenos, la vivienda (650) con esas mismas redacciones sí es la base exenta."""
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", [LOCAL, ("Alquiler vivienda C/ Ficticia 5, noviembre", "1,00",
                                                                  "mes", "650,00", "650,00")],
                                 T_SUBTOTAL_750, notas=[nota]))
    assert "importes_ajenos" not in ext["indicios"]
    assert d["desglose"] == [sujeta("21.00", "100.00", "21.00"), borradores.fila_exenta("650.00", "E1", ext["exencion"]["motivo"])]
    inc = validar(d, ext)
    assert not codigos(inc, "bloqueo"), [i for i in inc if i["nivel"] == "bloqueo"]
    assert "corresponde a la línea «Alquiler vivienda" in next(i for i in inc if i["codigo"] == "EXENTA_CAUSA")["mensaje"]


@pytest.mark.parametrize("texto", ["Depósito de garantía", "Deposito en garantía", "Garantía adicional (art. 36 LAU)"])
def test_g3v1_depositos_de_garantia_son_importes_ajenos(texto):
    assert campos.Extractor._importe_ajeno(texto) is True


# --- G3V-3: factura sin IVA (toda la base exenta) con un importe ajeno -----------------------------------------------------
def test_g3v3_factura_sin_iva_con_fianza_avisa_en_la_confirmacion():
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", [VIVIENDA_500, ("Fianza (dos mensualidades)", "1,00", "ud",
                                                                        "1000,00", "1000,00")],
                                 [("BASE IMPONIBLE", "1500,00"), ("TOTAL FACTURA", "1500,00")],
                                 notas=["Arrendamiento de vivienda exento de IVA (art. 20.Uno.23º Ley 37/1992)."]))
    assert [borradores.clase_fila(x) for x in d["desglose"]] == ["exenta"]
    assert "importes_ajenos" in ext["indicios"]
    conf = next(i for i in validar(d, ext) if i["codigo"] == "EXENTA_CAUSA")["mensaje"]
    assert "importes que no suelen formar parte de una base exenta" in conf and "«Fianza (dos mensualidades)" in conf
    # sin importes ajenos, la confirmación no lo menciona
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", [VIVIENDA_500], [("BASE IMPONIBLE", "500,00"),
                                                                          ("TOTAL FACTURA", "500,00")],
                                 notas=["Arrendamiento de vivienda exento de IVA (art. 20.Uno.23º Ley 37/1992)."]))
    conf = next(i for i in validar(d, ext) if i["codigo"] == "EXENTA_CAUSA")["mensaje"]
    assert "importes que no suelen formar parte" not in conf
