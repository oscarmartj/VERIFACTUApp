"""Regresiones de la prueba con cinco facturas reales más (carril F: fecha y número). Las facturas no se publican: cada
causa se reproduce con documentos sintéticos (reportlab, datos ficticios: NIF de la provincia 00, DNI de cifras
repetidas, calles inventadas) con la misma maqueta de carta: membrete arriba a la izquierda, destinatario a la derecha,
«FACTURA Nº. X», la fecha de carta alineada a la derecha, un párrafo de concepto, totales con puntos guía y un pie de
protección de datos en letra pequeña.

- F1: la fecha de carta «Villaficticia, a 1 de noviembre de 2026» (también «En Villaficticia, a …») se acepta con la
  confianza normal aunque esté por debajo del 40 % de la página (bajo el destinatario o al pie, antes de la firma).
  Una fecha citada en una frase («s/contrato del 3 de marzo de 2024») nunca se toma como la del documento (con ella, la
  de carta se marca para confirmar); un renglón que sigue la frase del de encima no es una fecha de carta; un recibí no
  es un lugar; una fecha con rótulo sigue mandando.
- F2: tras «FACTURA», las lecturas del OCR de «Nº.» («N“.», «N”.», «N".», «N.» con la º perdida) y «Nro.» rotulan el
  número; las grafías que ya funcionaban («Nº.», «N.º», «N°.», «N*.», «No.») siguen funcionando.
- F3: el tipo de documento toma la confianza de la palabra «FACTURA», no la de una abreviatura «Nº» mal leída por el OCR
  en el mismo renglón, y entre los renglones del mismo tipo prefiere el título («FACTURA Nº. X») a la frase del concepto
  («Factura por la gestión…», cuyas palabras miden más por los rasgos que bajan) o al rótulo «TOTAL FACTURA».

Revisión adversarial: al pie, una fecha de carta presentada por un rótulo con dos puntos («Recibido y conforme:»), en
letra pequeña, o con la fecha del documento en blanco («a ___ de ____ de 2026») se marca para confirmar; el año «2.026»
se lee; un rótulo «fecha» dentro de una frase que fecha otra cosa («según el contrato de fecha …») o de otro papel
(«Fecha contrato:») no es la fecha del documento; «Factura nº 45 del 15/10/2026» no es una fecha citada; «FACTURA Nº
CLIENTE» no rotula el número de factura; «2026 / 045» se marca; una mención de «abono», «presupuesto» o «albarán» en la
cabecera no decide el tipo de documento.
"""
import io
import tempfile
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, inspeccion, texto
from app.extraccion.texto import Pagina, Palabra
from app.nif import completar_cif

EMI = completar_cif("B", "0012345")   # emisor ficticio
DNI = "44444444A"                     # destinataria ficticia (persona física)
FECHA = "Villaficticia, a 1 de noviembre de 2026"
UMBRAL = borradores.UMBRAL_CONFIANZA


def _pdf(ops) -> bytes:
    """ops: (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 and op[4] else "Helvetica", op[3] if len(op) > 3 else 11)
        (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, A4[1] - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos: bytes):
    return campos.Extractor(texto.leer(datos), "carta.pdf", EMI).extraer()


def _dudosos(e) -> set:
    return {d["campo"] for d in borradores.campos_dudosos(e)}


MEMBRETE = [(85, 75, "TALLERES FICTICIOS, S.L.", 18, "Helvetica-Bold"), (85, 113, f"CIF: {EMI}"),
            (85, 133, "C/ Inventada, 7 – Local 2"), (85, 153, "00001 – Villaficticia"),
            (85, 173, "Telf: 900 000 000"), (85, 194, "Fax: 900 000 001"),
            (260, 221, "Dña. Persona Imaginaria Ejemplo"), (260, 241, f"N.I.F.: {DNI}"),
            (260, 261, "Calle Ficticia, 14"), (260, 282, "00002 Pueblo Inventado")]
CONCEPTO = ["Factura por la gestión del almacén situado en", "Calle Ficticia, nº 14, de Pueblo Inventado,",
            "durante el mes de noviembre de 2026."]
TOTALES = [(156, 542, "Importe de la gestión ...............", 10), (480, 542, "600,00 €", 10, None, "r"),
           (191, 592, "TOTAL FACTURA ............................", 10), (480, 592, "600,00 €", 10, None, "r"),
           (85, 643, "Se cargará en la cuenta habitual del cliente.", 10)]
PIE = ["Protección de datos: sus datos se usan para gestionar la relación comercial y emitir las facturas, y se",
       "guardan solo durante los plazos que exige la ley. No se comunican a nadie salvo obligación legal. Puede",
       "pedir el acceso, la corrección o el borrado de sus datos, y saber si Empresa Imaginaria, S.A. los trata,",
       "escribiendo a la dirección del membrete. Esta nota informativa no forma parte del detalle de la factura y",
       "se incluye en todos los documentos que la empresa envía a sus clientes y proveedores habituales."]


def _carta(fecha=FECHA, y_fecha=397, concepto=CONCEPTO, titulo="FACTURA Nº. Z71/26", extra=()):
    ops = list(MEMBRETE) + [(85, 372, titulo, 10)]
    if fecha:
        ops.append((510, y_fecha, fecha, 10, None, "r"))
    ops += [(85, 448 + 17 * i, s, 10) for i, s in enumerate(concepto)]
    ops += TOTALES + [(85, 731 + 10 * i, s, 7) for i, s in enumerate(PIE)] + list(extra)
    return _pdf(ops)


# --- F1: fecha de carta ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("renglon", [FECHA, "En Villaficticia, a 1 de noviembre de 2026",
                                     "En San Martín de la Imaginaria, a 1 de noviembre de 2026",
                                     "Villaficticia, 1 de noviembre de 2026"])
@pytest.mark.parametrize("y", [397, 690])
def test_fecha_de_carta_bajo_el_destinatario_o_al_pie(renglon, y):
    """Antes: por debajo del 40 % de la página la fecha de carta no se buscaba (fecha vacía)."""
    e = _extraer(_carta(renglon, y_fecha=y))
    assert e["fecha"]["valor"] == "2026-11-01"
    assert e["fecha"]["confianza"] >= UMBRAL and not e["fecha"]["avisos"]
    assert "fecha" not in _dudosos(e)


def test_en_lugar_a_fecha_en_la_cabecera():
    """Antes: «En» se contaba a la vez como palabra con mayúscula y como partícula y el renglón no era fecha de carta."""
    e = _extraer(_carta("En Villaficticia, a 3 de octubre de 2026", y_fecha=200))
    assert e["fecha"]["valor"] == "2026-10-03" and e["fecha"]["confianza"] >= UMBRAL


def test_fecha_citada_en_el_concepto_no_es_la_del_documento():
    """La fecha de un contrato citada en el concepto contradice la de carta: esta se toma, pero a confirmar."""
    concepto = ["Factura por la gestión del almacén de la", "Calle Inventada, 3 de Villaficticia",
                "s/contrato del 3 de marzo de 2024, durante", "el mes de noviembre de 2026."]
    e = _extraer(_carta(concepto=concepto))
    assert e["fecha"]["valor"] == "2026-11-01"
    assert e["fecha"]["confianza"] < UMBRAL and "fecha" in _dudosos(e)


@pytest.mark.parametrize("concepto", [
    ["Factura por la gestión del almacén de la Calle Inventada, 3", "s/contrato del 3 de marzo de 2024, durante",
     "el mes de noviembre de 2026."],
    ["Factura por la gestión del almacén de la Calle Inventada, 3 según el contrato",
     "del 3 de marzo de 2024, durante el mes de noviembre de 2026."],
])
def test_sin_fecha_de_carta_la_citada_en_una_frase_no_se_toma(concepto):
    """Antes: con el concepto en la parte superior, la fecha del contrato citado se proponía como fecha del documento."""
    ops = [(85, 75, "TALLERES FICTICIOS, S.L.", 14, "Helvetica-Bold"), (85, 95, f"CIF: {EMI}"),
           (85, 110, "C/ Inventada, 10 – 00001 Villaficticia"), (85, 140, "FACTURA Nº. Z71/26", 10)]
    ops += [(85, 170 + 17 * i, s, 10) for i, s in enumerate(concepto)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["fecha"]["valor"] is None


@pytest.mark.parametrize("renglon", ["Factura del 15/10/2026", "Esta factura ha sido emitida el 15/10/2026"])
def test_encabezado_corto_con_fecha_sin_rotulo_sigue_a_confirmar(renglon):
    """Salvaguarda: un encabezado corto («Factura del …») o una frase sobre la emisión del propio documento no citan la
    fecha de otra cosa: la fecha se propone, a confirmar."""
    ops = [(85, 75, "TALLERES FICTICIOS, S.L.", 14, "Helvetica-Bold"), (85, 95, f"CIF: {EMI}"),
           (85, 140, "FACTURA Nº. Z71/26", 10), (85, 155, renglon, 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["fecha"]["valor"] == "2026-10-15" and e["fecha"]["confianza"] < UMBRAL


def test_renglon_que_sigue_una_frase_no_es_fecha_de_carta():
    """Antes: el renglón «Villaficticia, 1 de octubre de 2026.» de un párrafo se aceptaba como fecha de carta."""
    ops = [(85, 75, "TALLERES FICTICIOS, S.L.", 14, "Helvetica-Bold"), (85, 95, f"CIF: {EMI}"),
           (85, 140, "FACTURA Nº. Z71/26", 10), (85, 170, "Servicios de mantenimiento prestados en el local situado en", 10),
           (85, 183, "Villaficticia, 1 de octubre de 2026.", 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["fecha"]["confianza"] < UMBRAL and "fecha" in _dudosos(e)


def test_recibi_no_es_un_lugar():
    """Antes: «Recibí en Villaficticia, …» pasaba por fecha de carta (lugar «Recibí en Villaficticia»)."""
    e = _extraer(_carta("Recibí en Villaficticia, 1 de noviembre de 2026", y_fecha=200))
    assert e["fecha"]["confianza"] < UMBRAL and "fecha" in _dudosos(e)


@pytest.mark.parametrize("otro", ["Copia, 1 de noviembre de 2026", "Original, 1 de noviembre de 2026"])
def test_marca_de_ejemplar_baja_no_es_fecha_de_carta(otro):
    """Salvaguarda: lejos de la cabecera una marca de ejemplar sigue sin ser un lugar."""
    e = _extraer(_carta(otro))
    assert e["fecha"]["valor"] is None or e["fecha"]["confianza"] < UMBRAL


def test_fecha_con_rotulo_prevalece_sobre_la_de_carta_baja():
    """Salvaguarda: una fecha rotulada en otra parte del documento manda sobre la de carta, esté donde esté."""
    e = _extraer(_carta(extra=[(400, 113, "Fecha: 15/10/2026")]))
    assert e["fecha"]["valor"] == "2026-10-15" and e["fecha"]["confianza"] >= UMBRAL


# --- F2: lecturas del OCR de «Nº» tras «FACTURA» -----------------------------------------------------------------------
@pytest.mark.parametrize("abreviatura", ["N“.", "N”.", 'N".', "N.", "Nro.", "N'."])
def test_numero_tras_factura_con_lectura_ocr_de_n_grado(abreviatura):
    """Antes: «FACTURA N“. Z72/26» o «FACTURA N. Z73/26» no daban número."""
    e = _extraer(_carta(titulo=f"FACTURA {abreviatura} Z72/26"))
    assert e["numero"]["valor"] == "Z72/26"


@pytest.mark.parametrize("abreviatura", ["Nº.", "N.º", "N°.", "N*.", "No.", "Nº"])
def test_numero_tras_factura_con_grafias_conocidas(abreviatura):
    """Salvaguarda: las grafías que ya funcionaban siguen funcionando, con la confianza normal."""
    e = _extraer(_carta(titulo=f"FACTURA {abreviatura} Z71/26"))
    assert e["numero"]["valor"] == "Z71/26" and e["numero"]["confianza"] >= UMBRAL


@pytest.mark.parametrize("abreviatura", ["NC.", "Ne", "N9"])
def test_titulo_factura_con_la_o_volada_leida_como_otro_signo(abreviatura):
    """«FACTURA Nº. Z72/26» con la º leída como otra letra o cifra: el número se toma, pero a confirmar; el tipo es factura
    (antes: ni número ni tipo de documento, con el título por debajo del 40 % de la página)."""
    e = _extraer(_carta(titulo=f"FACTURA {abreviatura} Z72/26"))
    assert e["numero"]["valor"] == "Z72/26"
    assert e["numero"]["confianza"] < UMBRAL and "numero" in _dudosos(e)
    assert e["tipo_documento"]["valor"] == "factura" and e["tipo_documento"]["confianza"] >= UMBRAL


@pytest.mark.parametrize("titulo", ["FACTURA NIF Z72/26", "FACTURA NC. Z72/26 de la obra"])
def test_n_y_un_signo_solo_si_sigue_solo_el_numero(titulo):
    """La N y un carácter más solo rotulan el número si les sigue únicamente el número (no «NIF», no una frase)."""
    e = _extraer(_carta(titulo=titulo))
    assert e["numero"]["valor"] != "Z72/26" or e["numero"]["confianza"] < UMBRAL


def test_n_punto_de_un_nif_no_es_el_rotulo_del_numero():
    """«N.» seguido de otra inicial con punto es un rótulo de NIF, no la abreviatura de «número»."""
    assert campos.re.search(campos.NUM_ABR_OCR, "N.I.F.: 44444444A", campos.re.I) is None
    assert campos.re.match(campos.NUM_ABR_OCR, "N. Z73/26", campos.re.I)


# --- F2/F3: renglones leídos por OCR (palabras sintéticas con su confianza y su caja) ----------------------------------
def _renglon(palabras, x, y, conf=0.96):
    """palabras: [(texto, alto[, confianza])]; caja de 6 pt por carácter, base común."""
    out = []
    for w in palabras:
        t, h, cf = w[0], w[1], (w[2] if len(w) > 2 else conf)
        out.append(Palabra(t, x, y + 11 - h, x + 6 * len(t), y + 11, cf, "ocr"))
        x += 6 * len(t) + 4
    return out


def _pie_ocr():
    pals = []
    for i in range(6):
        pals += _renglon([(w, 5.5) for w in ("datos personales que nos facilita para prestarles el servicio "
                                              "solicitado y la facturación del mismo").split()], 90, 716 + 10 * i)
    return pals


@pytest.mark.parametrize("abreviatura, conf", [("N“.", 0.19), ("N.", 0.51), ("N”.", 0.3)])
def test_titulo_factura_con_abreviatura_mal_leida_en_un_escaneo(abreviatura, conf):
    """Antes: el tipo tomaba la confianza de la peor palabra del renglón (la «Nº» mal leída: 0,19 o 0,51) o se quedaba
    con la frase del concepto, cuya palabra «gestión» mide más; y el número no se encontraba."""
    pals = _renglon([("TALLERES", 19.7), ("FICTICIOS,", 19.7), ("S.L.", 19.7)], 88, 86)
    pals += _renglon([("CIF:", 10), (EMI, 10)], 89, 121) + _renglon([("C/", 10), ("Inventada,", 10), ("10", 10)], 89, 140)
    pals += _renglon([("FACTURA", 8.8, 0.93), (abreviatura, 8.5, conf), ("Z72/26", 11.0, 0.91)], 90, 348)
    pals += _renglon([("Villaficticia,", 10.7), ("a", 7), ("1", 10), ("de", 10), ("noviembre", 10), ("de", 10),
                      ("2026", 10)], 327, 371)
    pals += _renglon([("Factura", 9.1), ("por", 9.1), ("la", 8.8), ("gestión", 12.0), ("del", 8.8), ("almacén", 9)], 90, 396)
    pals += _renglon([("durante", 8), ("el", 7), ("mes", 7), ("de", 8), ("noviembre", 8), ("de", 8), ("2026.", 9)], 90, 413)
    pals += _renglon([("TOTAL", 9.0), ("FACTURA", 9.6)], 160, 609) + _renglon([("850,00", 10), ("€", 10)], 454, 609)
    pals += _pie_ocr()
    ext = campos.Extractor([Pagina(1, 595, 836, pals, "ocr", 0.9)], "escaneo.pdf", None)
    tipo = ext.tipo_documento()
    assert tipo["valor"] == "factura" and tipo["confianza"] >= UMBRAL
    assert 340 <= tipo["bbox"][1] <= 360   # el título, no la frase del concepto ni «TOTAL FACTURA»
    num = ext.numero()
    assert num["valor"] == "Z72/26"
    fecha = ext.fecha()
    assert fecha["valor"] == "2026-11-01" and fecha["confianza"] >= UMBRAL


def test_carta_escaneada():
    """Escaneo de la maqueta de carta (200 ppp, sin capa de texto): fecha de carta, número y tipo."""
    with tempfile.TemporaryDirectory() as td:
        png = inspeccion.rasterizar(_carta(titulo="FACTURA Nº. A47/26"), Path(td), dpi=200)[0]
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.drawImage(ImageReader(str(png)), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
        c.save()
    pags = texto.leer(buf.getvalue())
    assert pags[0].metodo == "ocr"
    e = campos.Extractor(pags, "escaneo.pdf", EMI).extraer()
    assert e["fecha"]["valor"] == "2026-11-01"
    assert e["numero"]["valor"] == "A47/26"
    assert e["tipo_documento"]["valor"] == "factura" and e["tipo_documento"]["confianza"] >= UMBRAL


# --- Revisión adversarial: fechas de otros actos, rótulos en frases, encabezados, otros números y otros tipos ------------
# Plantilla de Word con la fecha por rellenar: la fecha del documento está en blanco
BLANCO = "Villaficticia, a ___ de ______________ de 2026"
CABECERA = [(85, 75, "TALLERES FICTICIOS, S.L.", 14, "Helvetica-Bold"), (85, 95, f"CIF: {EMI}"),
            (85, 110, "C/ Inventada, 10 – 00001 Villaficticia")]


@pytest.mark.parametrize("fecha, extra", [
    # la fecha del documento en blanco y la letra pequeña del pie fechada
    (BLANCO, [(85, 781, "Pueblo Inventado, 25 de mayo de 2018", 7)]),
    # la fecha del documento en blanco y un «Recibido y conforme:» fechado
    (BLANCO, [(85, 674, "Recibido y conforme:", 10), (85, 690, "Pueblo Inventado, 5 de noviembre de 2026", 10)]),
    # sin fecha del documento: la de una orden de domiciliación al pie, en letra menor y tras un rótulo con dos puntos
    (None, [(85, 674, "Orden de domiciliación firmada por el deudor:", 8), (85, 686, "Villaficticia, 1 de marzo de 2019", 8)]),
    # cada indicio por separado: solo la letra pequeña del pie
    (None, [(85, 781, "Pueblo Inventado, 25 de mayo de 2018", 7)]),
    # solo el rótulo con dos puntos, en letra normal
    (None, [(85, 674, "Recibido y conforme:", 10), (85, 690, "Pueblo Inventado, 5 de noviembre de 2026", 10)]),
    # solo la fecha del documento en blanco (la otra, en letra normal, sin rótulo, tras la despedida)
    (BLANCO, [(85, 674, "Atentamente,", 10), (85, 690, "Pueblo Inventado, 5 de noviembre de 2026", 10)]),
])
def test_fecha_de_otro_acto_al_pie_no_vale_sin_mas(fecha, extra):
    """Antes: una fecha de carta a cualquier altura se tomaba con la confianza normal, aunque fuera la del acuse de recibo,
    la del mandato o la de la letra pequeña del pie y la del documento estuviera en blanco."""
    e = _extraer(_carta(fecha=fecha, extra=extra))
    assert e["fecha"]["confianza"] < UMBRAL and "fecha" in _dudosos(e)
    if fecha == BLANCO:
        assert any("en blanco" in a for a in e["fecha"]["avisos"])


def test_renglon_del_concepto_tras_dos_puntos_no_es_fecha_de_carta():
    """Antes: «… en la finca:» ⏎ «Finca Imaginaria, 3 de octubre de 2026» del concepto era la fecha del documento."""
    concepto = ["Mantenimiento de las zonas verdes en la finca:", "Finca Imaginaria, 3 de octubre de 2026"]
    e = _extraer(_carta(fecha=None, concepto=concepto))
    assert e["fecha"]["confianza"] < UMBRAL and "fecha" in _dudosos(e)


def test_borrador_con_la_fecha_en_blanco():
    """Sin ninguna otra fecha, la del documento en blanco se señala (no «No se encuentra la fecha»)."""
    e = _extraer(_carta(fecha=BLANCO))
    assert e["fecha"]["valor"] is None and "en blanco" in e["fecha"]["avisos"][0]


@pytest.mark.parametrize("extra", [[(85, 674, "Atentamente,", 10), (85, 690, FECHA, 10)],
                                   [(85, 674, "Quedamos a su disposición.", 10), (85, 690, FECHA, 10)]])
def test_fecha_de_carta_al_pie_antes_de_la_firma(extra):
    """Salvaguarda: la fecha de carta del documento al pie, en letra normal y tras la despedida, vale sin más."""
    e = _extraer(_carta(fecha=None, extra=extra))
    assert e["fecha"]["valor"] == "2026-11-01" and e["fecha"]["confianza"] >= UMBRAL


def test_anio_con_punto_de_millar():
    """Antes: «a 1 de noviembre de 2.026» (el año como lo deja Word) no se leía."""
    e = _extraer(_carta(fecha="Villaficticia, a 1 de noviembre de 2.026"))
    assert e["fecha"]["valor"] == "2026-11-01" and e["fecha"]["confianza"] >= UMBRAL


def test_fecha_de_carta_bajo_el_correo_del_membrete():
    """Antes (carril): el correo del membrete («info@…», sin punto final) se tomaba por una frase que seguía en la fecha."""
    for contacto in ("info@talleresficticios.es", "www.talleresficticios.es"):
        ops = CABECERA + [(85, 125, contacto, 10), (85, 141, "Villaficticia, 1 de noviembre de 2026", 10),
                          (85, 200, "FACTURA Nº. Z71/26", 10)] + TOTALES
        e = _extraer(_pdf(ops))
        assert e["fecha"]["valor"] == "2026-11-01" and e["fecha"]["confianza"] >= UMBRAL


def test_fecha_de_carta_escaneada_y_fecha_del_pie_en_letra_pequena():
    """Escaneo (palabras del OCR): sin fecha del documento, la fecha de la letra pequeña del pie no se toma sin más."""
    pals = _renglon([("TALLERES", 19.7), ("FICTICIOS,", 19.7), ("S.L.", 19.7)], 88, 86)
    pals += _renglon([("CIF:", 10), (EMI, 10)], 89, 121) + _renglon([("C/", 10), ("Inventada,", 10), ("10", 10)], 89, 140)
    pals += _renglon([("FACTURA", 10.5, 0.93), ("Nº.", 10.5), ("Z71/26", 10.5)], 90, 348)
    pals += _renglon([("Factura", 9.1), ("por", 9.1), ("la", 8.8), ("gestión", 12.0), ("del", 8.8), ("almacén", 9)], 90, 396)
    pals += _renglon([("durante", 8), ("el", 7), ("mes", 7), ("de", 8), ("noviembre", 8), ("de", 8), ("2026.", 9)], 90, 413)
    pals += _renglon([("TOTAL", 9.0), ("FACTURA", 9.6)], 160, 609) + _renglon([("850,00", 10), ("€", 10)], 454, 609)
    pals += _pie_ocr() + _renglon([(w, 5.5) for w in ("Pueblo", "Inventado,", "25", "de", "mayo", "de", "2018")], 380, 776)
    fecha = campos.Extractor([Pagina(1, 595, 836, pals, "ocr", 0.9)], "escaneo.pdf", None).fecha()
    assert fecha["valor"] is None or fecha["confianza"] < UMBRAL


@pytest.mark.parametrize("concepto", [
    ["Factura por la gestión del almacén según el contrato de fecha 3 de marzo de 2024,",
     "durante el mes de noviembre de 2026."],
    ["Factura por la gestión del almacén según el contrato",
     "de fecha 3 de marzo de 2024, durante el mes de noviembre de 2026."],
    ["Factura por la gestión del almacén según el contrato de",
     "fecha 3 de marzo de 2024, durante el mes de noviembre de 2026."],
])
def test_fecha_de_un_contrato_citado_con_rotulo_en_la_frase(concepto):
    """Antes: «… el contrato de fecha 3 de marzo de 2024» pasaba por fecha rotulada y mandaba sobre la de carta."""
    e = _extraer(_carta(concepto=concepto))
    assert e["fecha"]["valor"] == "2026-11-01"
    e = _extraer(_carta(fecha=None, concepto=concepto))
    assert e["fecha"]["valor"] is None


def test_rotulo_de_la_fecha_de_otro_papel():
    """Antes: «Fecha contrato: 01/07/2025» era la fecha del documento; ahora manda la de carta."""
    e = _extraer(_carta(extra=[(85, 320, "Fecha contrato: 01/07/2025", 10)]))
    assert e["fecha"]["valor"] == "2026-11-01" and e["fecha"]["confianza"] >= UMBRAL


@pytest.mark.parametrize("renglon", ["Le remitimos la factura nº Z71/26 de fecha 15/10/2026 por los trabajos.",
                                     "Lugar y fecha: Villaficticia, 15/10/2026",
                                     "Con fecha 15/10/2026 le facturamos los trabajos."])
def test_fecha_rotulada_del_propio_documento_en_una_frase(renglon):
    """Salvaguarda: la frase que fecha el propio documento sigue siendo su fecha rotulada."""
    ops = CABECERA + [(85, 160, "FACTURA Nº. Z71/26", 11), (85, 190, renglon, 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["fecha"]["valor"] == "2026-10-15" and e["fecha"]["confianza"] >= UMBRAL


@pytest.mark.parametrize("renglon", ["Factura nº 45 del 15/10/2026", "Fra. n.º 45 de 15 de octubre de 2026",
                                     "Factura núm. 45 del 15/10/2026", "Factura no 45 del 15/10/2026"])
def test_encabezado_con_numero_y_fecha_en_minuscula(renglon):
    """Antes (carril): «nº», «n.º» o «núm.» contaban como palabras de una frase y la fecha del encabezado se daba por citada
    (con un «Vto.» debajo se proponía el vencimiento). Sigue a confirmar, como cualquier fecha sin rótulo."""
    ops = CABECERA + [(85, 140, renglon, 10), (85, 158, "Vto. 15/11/2026", 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["fecha"]["valor"] == "2026-10-15" and e["fecha"]["confianza"] < UMBRAL


@pytest.mark.parametrize("rotulo", ["FACTURA N. CLIENTE", "FACTURA Nro. CLIENTE", "FACTURA Nº CLIENTE",
                                    "FACTURA Nº. DE CLIENTE"])
def test_abreviatura_de_otro_numero_tras_factura(rotulo):
    """Antes: «FACTURA Nº CLIENTE» (dos rótulos de una rejilla leídos juntos) rotulaba el número de cliente de debajo como
    número de factura, con la confianza normal."""
    ops = CABECERA + [(85, 160, rotulo, 10, "Helvetica-Bold"), (300, 160, "FECHA", 10, "Helvetica-Bold"),
                      (85, 176, "4300012", 10), (300, 176, "15/10/2026", 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["numero"]["valor"] != "4300012" or e["numero"]["confianza"] < UMBRAL


def test_rejilla_factura_n_y_fecha():
    """Salvaguarda: «FACTURA Nº» seguido de otro rótulo de la rejilla («FECHA») sigue rotulando el número de debajo."""
    ops = CABECERA + [(85, 160, "FACTURA Nº", 10, "Helvetica-Bold"), (300, 160, "FECHA", 10, "Helvetica-Bold"),
                      (85, 176, "Z71/26", 10), (300, 176, "15/10/2026", 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["numero"]["valor"] == "Z71/26" and e["numero"]["confianza"] >= UMBRAL


@pytest.mark.parametrize("abreviatura", ["Nº.", "N."])
def test_numero_seguido_de_barra_y_mas_cifras(abreviatura):
    """Antes: de «FACTURA Nº. 2026 / 045» se tomaba «2026» con la confianza normal."""
    e = _extraer(_carta(titulo=f"FACTURA {abreviatura} 2026 / 045"))
    assert e["numero"]["confianza"] < UMBRAL and "numero" in _dudosos(e)


@pytest.mark.parametrize("mencion", ["Forma de pago: abono en cuenta", "Según presupuesto aceptado por el cliente",
                                     "Presupuesto aceptado el 01/10/2026", "Albarán de fecha 01/10/2026"])
@pytest.mark.parametrize("abreviatura", ["Nº.", "N."])
def test_mencion_de_otro_tipo_en_la_cabecera(mencion, abreviatura):
    """Antes: con el título «FACTURA Nº. X» (algo mayor que el texto), una mención de «abono», «presupuesto» o «albarán» en
    la cabecera decidía el tipo de documento (rectificativa, presupuesto, albarán) con la confianza normal."""
    ops = CABECERA + [(85, 140, f"FACTURA {abreviatura} Z71/26", 12, "Helvetica-Bold"), (85, 160, mencion, 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["tipo_documento"]["valor"] == "factura"


@pytest.mark.parametrize("titulo, tipo", [("ABONO Nº Z5/26", "rectificativa"), ("Nota de abono Z5/26", "rectificativa"),
                                          ("PRESUPUESTO 45/26", "presupuesto")])
def test_titulo_de_otro_tipo_en_la_cabecera(titulo, tipo):
    """Salvaguarda: el título del documento en la cabecera sigue decidiendo el tipo."""
    ops = CABECERA + [(85, 160, titulo, 11), (85, 176, "Fecha: 15/10/2026", 10)] + TOTALES
    assert _extraer(_pdf(ops))["tipo_documento"]["valor"] == tipo


def test_solo_una_mencion_en_la_cabecera_se_confirma():
    """Sin título ni otra palabra clave, el tipo deducido de una mención en la cabecera se marca para confirmar."""
    ops = CABECERA + [(85, 140, "Z71/26", 11), (85, 160, "Forma de pago: abono en cuenta", 10)] + TOTALES
    tipo = _extraer(_pdf(ops))["tipo_documento"]
    assert tipo["valor"] != "rectificativa" or tipo["confianza"] < UMBRAL


@pytest.mark.parametrize("ultimo", ["Pueblo Inventado, bajo", "Pueblo Inventado, izda"])
def test_fecha_de_carta_bajo_una_direccion_acabada_en_minuscula(ultimo):
    """Antes (carril): el último renglón de la dirección del destinatario («…, bajo») se tomaba por una frase que seguía en
    la fecha de debajo, y la fecha se marcaba para confirmar."""
    ops = MEMBRETE[:-1] + [(260, 282, ultimo), (260, 302, FECHA, 10)] + [(85, 372, "FACTURA Nº. Z71/26", 10)]
    ops += [(85, 448 + 17 * i, s, 10) for i, s in enumerate(CONCEPTO)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["fecha"]["valor"] == "2026-11-01" and e["fecha"]["confianza"] >= UMBRAL


@pytest.mark.parametrize("titulo", ["N. FACTURA: Z71/26", "Nº FACTURA: Z71/26"])
def test_titulo_con_el_rotulo_del_numero_delante(titulo):
    """Salvaguarda: «Nº FACTURA: X» en la cabecera da el tipo con la confianza normal (no como una mención)."""
    ops = CABECERA + [(85, 140, titulo, 11), (85, 160, "Forma de pago: transferencia", 10)] + TOTALES
    e = _extraer(_pdf(ops))
    assert e["tipo_documento"]["valor"] == "factura" and e["tipo_documento"]["confianza"] >= UMBRAL


# --- Cierre: una frase partida que empieza por la palabra clave en minúscula no es el título -------------------------
def test_frase_partida_que_empieza_por_presupuesto_no_es_el_titulo():
    """Carta: «…conforme a nuestro ⏎ presupuesto P-12/26 de 2 de octubre…» en la cabecera; el asunto dice «Factura n* X»
    (la º leída como asterisco). El tipo es factura, no presupuesto."""
    from app.nif import completar_cif
    from tests.test_10_revision_a import _extraer, _pdf
    emp, cli = completar_cif("B", "0012345"), completar_cif("A", "0054321")
    ops = [(50, 50, "TALLER INVENTADO, S.L.", 12, "Helvetica-Bold"), (50, 64, "Calle Ficticia 1, 26000 Logroño"),
           (50, 76, f"CIF {emp}"), (50, 110, "Logroño, 3 de noviembre de 2026"),
           (300, 130, "D. Cliente Imaginario"), (300, 142, "Calle Inventada 2, 26200 Haro"), (300, 154, f"NIF {cli}"),
           (50, 190, "Asunto: Factura n* 2026-045"),
           (50, 220, "Adjunto le remitimos la factura de los trabajos realizados conforme a nuestro"),
           (50, 232, "presupuesto P-12/26 de 2 de octubre de 2026, que usted tuvo a bien aceptar. El importe"),
           (50, 244, "asciende a 1.000,00 euros de base imponible más 210,00 euros de IVA (21 %), en total"),
           (50, 256, "1.210,00 euros.")]
    ext, _ = _extraer(_pdf(ops), nif_empresa=emp)
    assert ext["tipo_documento"]["valor"] == "factura"
