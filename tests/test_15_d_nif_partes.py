"""Regresiones de la prueba con más facturas reales del usuario (carril D: NIF y partes). Las facturas no se publican:
cada causa se reproduce con documentos sintéticos (nombres, NIF y direcciones ficticios).

- NIF impreso con barra tras el código de provincia («CIF: B00/123455») o con la raya separada por espacios
  («CIF: A – 00/543215»): no se reconocía y, sin él, el emisor del membrete no se encontraba (documento «desconocido»).
  La barra solo vale detrás del rótulo del NIF: sin él, la misma forma es la de un número de factura.
- Lectura OCR de un NIF con una letra donde va una cifra («A00B12347» por «A00812347») o al revés: se corrige si una única
  variante tiene el control válido, a confirmar; nunca en texto nativo. Rótulos leídos «N.LF.», «C.LF.», «N.l.F.».
- La raya o el guion entre espacios dentro de un NIF («A – 00543215», «44444444 – A») solo valen tras el rótulo: sin él son
  un número de factura, una serie o una referencia («Pedido 20261105 – Z»).
- Plantilla de carta: membrete del emisor arriba a la izquierda, destinatario a la derecha (un particular cuya dirección no
  lleva código postal, o una sociedad con el mismo domicilio que el emisor) y un pie de protección de datos que nombra a
  otra sociedad dentro de una frase, que no es ninguna de las partes. Los renglones de domicilio más abajo de lo habitual
  bajo el NIF solo completan un domicilio sin código postal ni población, y siempre a confirmar; una calle sin tipo de vía
  necesita un renglón de domicilio que la confirme y también se pide confirmarla.
"""
import io
import re
import sqlite3

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import borradores
from app import nif as nifmod
from app.extraccion import campos, inspeccion, texto
from app.extraccion.texto import Pagina, Palabra
from app.nif import completar_cif

EMP = completar_cif("B", "0012345")      # B00123455: emisor, impreso «B00/123455»
EMP2 = completar_cif("A", "0054321")     # A00543215: emisor, impreso «A – 00/543215»
CLI = completar_cif("B", "0067890")      # B00678904: cliente sociedad
CLI_OCR = completar_cif("A", "0081234")  # A00812347: cliente leído por el OCR con la B en lugar del 8
DNI = "44444444A"
NIE = "X4444444Q"
RAZON = "INMOBILIARIA FICTICIA, S.L."
OTRA = "Sociedad Imaginaria del Pie, S.A."


def _barra(n: str) -> str:
    return f"{n[0]}{n[1:3]}/{n[3:]}"


def _pdf(ops) -> bytes:
    """ops: (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _, alto = A4
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 10)
        (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, alto - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _carta(cif_texto: str, destinatario: list[str], numero="B07/26") -> bytes:
    """Plantilla de carta: membrete del emisor, bloque del destinatario a la derecha, número, fecha de carta, párrafo del
    concepto, importes con puntos de relleno y un pie de protección de datos que nombra a otra sociedad."""
    ops = [(85, 75, RAZON, 16, "Helvetica-Bold"), (85, 113, cif_texto), (85, 133, "C/ Inventada, 3 – Local"),
           (85, 153, "28000 – Villaficticia"), (85, 173, "Telf: 91 000 00 00"), (85, 193, "Fax: 91 000 00 01")]
    ops += [(260, 221 + 20 * k, s) for k, s in enumerate(destinatario)]
    ops += [(85, 372, f"FACTURA Nº. {numero}", 11, "Helvetica-Bold"),
            (510, 397, "Villaficticia, a 2 de noviembre de 2026", 10, "Helvetica", "r"),
            (85, 447, "Factura por el arrendamiento del inmueble situado en"), (85, 464, "la calle Imaginaria, 7, y"),
            (85, 481, "período facturado: noviembre de 2026."),
            (156, 542, "Importe arrendamiento"), (333, 542, "........................"),
            (480, 542, "500,00 €", 10, "Helvetica", "r"),
            (191, 592, "TOTAL FACTURA ............................ 500,00 €"),
            (85, 643, "Nota.- Pago por domiciliación bancaria."),
            (85, 731, f"Le informamos de que sus datos personales serán tratados por {OTRA} con la finalidad de", 7),
            (85, 741, f"{OTRA} conservará los datos mientras dure la relación comercial y no los cederá a", 7),
            (85, 751, "terceros salvo obligación legal. Puede ejercer sus derechos de acceso, rectificación y supresión.", 7)]
    return _pdf(ops)


def _extraer(datos: bytes, nif_empresa):
    ext = campos.Extractor(texto.leer(datos), "f.pdf", nif_empresa).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "f.pdf")


def _situacion(datos: bytes, ext, nif_empresa) -> str:
    insp = inspeccion.inspeccionar(datos)
    insp["qr"] = []
    return borradores.clasificar(ext, insp, {"nif": nif_empresa})["sugerida"]


def _validar(ext, datos, nif_empresa=EMP, razon=RAZON, domicilio="C/ Inventada, 3 – Local, 28000 – Villaficticia"):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    empresa = {"id": 1, "nif": nif_empresa, "razon_social": razon, "domicilio": domicilio, "ambito_confirmado_en": "x",
               "ambito_dict": {"puede_emitir": True}}
    documento = {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}
    serie = {"siguiente_numero": 1, "codigo": "X", "numero_detectado": None}
    return borradores.validar(con, empresa, documento, datos, extraccion=ext, serie=serie)


# --- D1: NIF con barra tras el código de provincia o con la raya separada por espacios --------------------------------
@pytest.mark.parametrize("impreso, nif", [
    (f"CIF: {_barra(EMP)}", EMP), (f"CIF: {EMP2[0]} – {_barra(EMP2)[1:]}", EMP2),
    (f"C.I.F.: {EMP2[0]} — {EMP2[1:3]}/{EMP2[3:]}", EMP2), (f"NIF: {EMP[0]}-{_barra(EMP)[1:]}", EMP),
    (f"CIF: ES{_barra(EMP)}", EMP), (f"N.I.F.: {DNI[:8]} – {DNI[8]}", DNI), (f"NIE: {NIE[0]} – {NIE[1:8]} – {NIE[8]}", NIE),
])
def test_nif_con_barra_o_raya_se_reconoce(impreso, nif):
    assert [n for n, _, _ in campos._nifs_en(impreso) if nifmod.es_valido(n)] == [nif]
    assert [n for n, _, _ in nifmod.buscar_nifs(impreso)] == [nif]
    assert nifmod.normalizar(impreso.split(": ", 1)[1]) == nif


def test_numero_de_factura_con_barra_no_es_un_nif():
    """La misma forma («F00/012347», con control válido) sin el rótulo del NIF delante es un número de documento."""
    numero = _barra(completar_cif("F", "0001234"))
    assert nifmod.es_valido(numero.replace("/", ""))
    for t in (f"Factura nº {numero}", f"Nº FACTURA : {numero}", numero, f"Pedido {numero} del 05/11/2026",
              f"CIF: {CLI} Factura: {numero}"):
        assert numero.replace("/", "") not in [n for n, _, _ in campos._nifs_en(t)], t
        assert numero.replace("/", "") not in [n for n, _, _ in nifmod.buscar_nifs(t)], t
    assert [n for n, _, _ in campos._nifs_en(f"CIF: {numero}")] == [numero.replace("/", "")]


@pytest.mark.parametrize("t", [
    "03/11/2026", "Fecha: 3/11/26", "FACTURA Nº. B07/26", "Factura 021/C26", "Nº FACTURA : C045/26", "Telf: 91 000 00 00",
    "Fax: 91 000 00 01", "Tomo 1.234, Folio 56, Hoja M-123456, Inscripción 1ª", "Hoja M-123456", "1.234,56 €", "-95,00 €",
    "TOTAL FACTURA ............................ 500,00 €", "28000 – Villaficticia", "C/ Inventada, 3 – Local",
    "Reglamento (UE) 2016/679 – protección de datos", "NIF: 03/11/2026",
])
def test_barras_y_rayas_sin_nif_no_dan_candidatos_validos(t):
    for x in (t, re.sub(r"\s+", "", t)):   # (también como lo examina el extractor, sin espacios)
        assert not [n for n, _, _ in campos._nifs_en(x) if nifmod.es_valido(n)], x
        assert not nifmod.buscar_nifs(x), x


@pytest.mark.parametrize("cif_texto, emisor", [
    (f"CIF: {_barra(EMP)}", EMP),
    (f"CIF: {EMP2[0]} – {EMP2[1:3]}/{EMP2[3:]}", EMP2),
])
def test_carta_emisor_del_membrete_con_cif_con_barra(cif_texto, emisor):
    lineas = ["DISTRIBUCIONES FICTICIAS, S.A.", f"C.I.F.: {CLI}", "Avda. Imaginaria, 12 - Bajo", "30000 - VILLAPRUEBA"]
    datos = _carta(cif_texto, lineas)
    ext, d = _extraer(datos, emisor)
    assert d["emisor"]["nif"] == emisor and d["emisor"]["nombre"] == RAZON
    assert "28000" in d["emisor"]["domicilio"] and "Inventada" in d["emisor"]["domicilio"]
    assert d["destinatario"]["nif"] == CLI and d["destinatario"]["nombre"] == "DISTRIBUCIONES FICTICIAS, S.A."
    assert "30000" in d["destinatario"]["domicilio"]
    assert _situacion(datos, ext, emisor) == "pendiente_confirmar"


# --- D3: partes de la plantilla de carta --------------------------------------------------------------------------------
def test_carta_destinatario_particular_sin_codigo_postal():
    """El domicilio de un particular sin código postal («Residencial …, 7B» ⏎ «Urb. …» ⏎ «LOCALIDAD (Provincia)») se
    toma entero, a confirmar."""
    lineas = ["Dña. Lucía Imaginaria Ejemplo", f"N.I.F.: {DNI}", "Residencial Imaginaria, 7B", "Urb. Ficticia",
              "VILLAIMAGINARIA (Provincia)"]
    datos = _carta(f"CIF: {_barra(EMP)}", lineas)
    ext, d = _extraer(datos, EMP)
    assert d["emisor"]["nif"] == EMP and d["emisor"]["nombre"] == RAZON and "28000" in d["emisor"]["domicilio"]
    assert d["destinatario"]["nif"] == DNI and "Lucía Imaginaria Ejemplo" in d["destinatario"]["nombre"]
    dom = d["destinatario"]["domicilio"]
    assert "Residencial Imaginaria, 7B" in dom and "Urb. Ficticia" in dom and "VILLAIMAGINARIA (Provincia)" in dom
    c = ext["destinatario"]["domicilio"]
    assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("código postal" in a for a in c["avisos"])
    assert _situacion(datos, ext, EMP) == "pendiente_confirmar"
    assert OTRA not in (d["emisor"]["nombre"], d["destinatario"]["nombre"])


def test_calle_sin_via_suelta_bajo_el_nif_no_es_domicilio():
    """«Nombre, número» bajo el NIF sin otro renglón de domicilio que lo confirme no se toma como dirección."""
    lineas = ["Dña. Lucía Imaginaria Ejemplo", f"N.I.F.: {DNI}", "Referencia, 14"]
    ext, d = _extraer(_carta(f"CIF: {_barra(EMP)}", lineas), EMP)
    assert d["destinatario"]["nif"] == DNI
    assert "Referencia" not in (d["destinatario"]["domicilio"] or "")


def test_carta_destinatario_con_el_domicilio_del_emisor():
    """Cliente con el mismo domicilio que el emisor (legítimo): solo se pide confirmarlo, no bloquea las partes."""
    lineas = ["DISTRIBUCIONES FICTICIAS, S.A.", f"C.I.F.: {CLI}", "C/ Inventada, 3 - Local", "28000 - Villaficticia"]
    datos = _carta(f"CIF: {EMP2[0]} – {EMP2[1:3]}/{EMP2[3:]}", lineas, numero="021/C26")
    ext, d = _extraer(datos, EMP2)
    assert d["emisor"]["nif"] == EMP2 and d["emisor"]["nombre"] == RAZON and "28000" in d["emisor"]["domicilio"]
    assert d["destinatario"]["nif"] == CLI and d["destinatario"]["nombre"] == "DISTRIBUCIONES FICTICIAS, S.A."
    assert "28000" in d["destinatario"]["domicilio"]
    inc = _validar(ext, d, nif_empresa=EMP2)
    partes = [i for i in inc if i["campo"].startswith(("emisor.", "destinatario."))]
    assert partes and all(i["nivel"] in ("confirmar", "aviso") for i in partes), partes
    assert {"DEST_DOMICILIO_EMISOR", "EMISOR_DOMICILIO_DESTINATARIO"} & {i["codigo"] for i in partes}
    assert _situacion(datos, ext, EMP2) == "pendiente_confirmar"


def test_carta_nie_con_guiones_y_pie_de_proteccion_de_datos():
    lineas = ["D. Wei Ficticio", f"N.I.F.: {NIE[0]}-{NIE[1:8]}-{NIE[8]}", "Avda. Imaginaria, 12 - Bajo", "30000 - VILLAPRUEBA",
              "(Provincia)"]
    datos = _carta(f"CIF: {_barra(EMP)}", lineas, numero="C045/26")
    ext, d = _extraer(datos, EMP)
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == NIE
    assert sorted(ext["nifs_detectados"]) == sorted([EMP, NIE])
    assert OTRA not in (d["emisor"]["nombre"], d["destinatario"]["nombre"])
    assert "30000" in d["destinatario"]["domicilio"]


# --- D2: confusiones del OCR en el NIF ----------------------------------------------------------------------------------
def _pagina_ocr(renglones, conf=0.9) -> Pagina:
    """renglones: (x, top, texto[, alto]); cada palabra ocupa 5 pt por carácter y un espacio de 3 pt."""
    pals = []
    for r in renglones:
        x, top, t = r[0], r[1], r[2]
        alto = r[3] if len(r) > 3 else 8
        for w in t.split():
            pals.append(Palabra(w, x, top, x + 5 * len(w), top + alto, conf, "ocr"))
            x += 5 * len(w) + 3
    return Pagina(1, 595, 842, pals, metodo="ocr", conf_media=conf)


def _plantilla_ocr(rotulo: str, leido: str):
    """Hoja de cálculo escaneada: emisor a la izquierda, cliente en un recuadro arriba a la derecha."""
    return [(350, 95, "GIMNASIOS FICTICIOS, S.A."), (360, 107, f"{rotulo} {leido}"), (350, 128, "C/ Imaginaria, 9"),
            (366, 147, "29000 Villaprueba"),
            (65, 110, RAZON), (65, 123, "Calle Inventada, 3 Local"), (65, 133, "28000 Villaficticia"),
            (65, 147, f"C.I.F.: {EMP}"), (142, 184, "FACTURA", 12), (83, 212, "Nº FACTURA :"), (173, 212, "C045/26"),
            (83, 227, "FECHA :"), (173, 228, "03/11/2026"), (432, 572, "TOTAL FACTURA"), (240, 573, "Base Imponible"),
            (336, 587, "21% 105,00 605,00"), (233, 593, "500,00")]


@pytest.mark.parametrize("rotulo", ["N.I.F.:", "N.LF.:", "C.LF.:", "N.l.F.:", "NIF.", "C.1.F.:"])
def test_ocr_nif_con_letra_por_cifra_se_corrige_a_confirmar(rotulo):
    leido = CLI_OCR[:3] + "B" + CLI_OCR[4:]   # «A00B12347»: la B en lugar del 8
    assert not nifmod.es_valido(leido)
    ext = campos.Extractor([_pagina_ocr(_plantilla_ocr(rotulo, leido))], "f.pdf", EMP).extraer()
    n = ext["destinatario"]["nif"]
    assert n["valor"] == CLI_OCR
    assert n["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("corregido" in a and leido in a for a in n["avisos"])
    assert ext["destinatario"]["nombre"]["valor"] == "GIMNASIOS FICTICIOS, S.A."
    assert "29000" in ext["destinatario"]["domicilio"]["valor"]
    assert ext["emisor"]["nif"]["valor"] == EMP
    assert not ext["nifs_no_validos"]


def test_ocr_nif_corregido_sin_incidencia_de_nif_ilegible():
    """La lectura con forma de DNI pero control erróneo («50012345E») es el NIF «S0012345E» leído con la S como 5: se
    corrige (única variante válida) y la lectura original no se señala además como NIF ilegible."""
    nif_ = completar_cif("S", "0012345")
    leido = "5" + nif_[1:]
    assert nifmod.tipo(leido) == "DNI" and not nifmod.es_valido(leido)
    ext = campos.Extractor([_pagina_ocr(_plantilla_ocr("N.I.F.:", leido))], "f.pdf", EMP).extraer()
    assert ext["destinatario"]["nif"]["valor"] == nif_
    assert not ext["nifs_no_validos"]
    d = borradores.datos_desde_extraccion(ext, "f.pdf")
    inc = _validar(ext, d)
    assert "NIF_ILEGIBLE" not in {i["codigo"] for i in inc}
    assert ("confirmar", "DUDOSO", "destinatario.nif") in {(i["nivel"], i["codigo"], i["campo"]) for i in inc}


def test_ocr_nif_cifra_por_letra_en_el_control():
    leido = f"{NIE[0]}-{NIE[1:8]}-0"   # «X-4444444-0»: la Q de control leída como cero
    ext = campos.Extractor([_pagina_ocr(_plantilla_ocr("N.I.F.:", leido))], "f.pdf", EMP).extraer()
    assert ext["destinatario"]["nif"]["valor"] == NIE
    assert ext["destinatario"]["nif"]["confianza"] < borradores.UMBRAL_CONFIANZA


def test_ocr_nif_ambiguo_o_con_muchos_cambios_no_se_corrige():
    # «0» + cuerpo + letra: tanto «Q…» como «D…» son válidos con esa letra de control → no se elige ninguno
    cuerpo = "0012345"
    letra = completar_cif("Q", cuerpo)[-1]
    ambiguo = "0" + cuerpo + letra
    assert not nifmod.es_valido(ambiguo) and nifmod.es_valido("Q" + cuerpo + letra) and nifmod.es_valido("D" + cuerpo + letra)
    assert campos._nif_corregido_ocr(ambiguo) is None
    # tres cambios (más de los admitidos)
    assert campos._nif_corregido_ocr("ASOBI2347") is None
    # ya válido o de otra longitud: nada que corregir
    assert campos._nif_corregido_ocr(CLI_OCR) is None and campos._nif_corregido_ocr("A00B1234") is None
    ext = campos.Extractor([_pagina_ocr(_plantilla_ocr("N.I.F.:", ambiguo))], "f.pdf", EMP).extraer()
    variantes = {"Q" + cuerpo + letra, "D" + cuerpo + letra}
    assert ext["destinatario"]["nif"]["valor"] not in variantes and not variantes & set(ext["nifs_detectados"])
    assert any(n["texto"] == ambiguo for n in ext["nifs_no_validos"])   # sigue señalado como NIF ilegible


def test_texto_nativo_no_se_corrige():
    """Las confusiones del OCR no se aplican a texto nativo del PDF: «A00B12347» impreso no es «A00812347»."""
    leido = CLI_OCR[:3] + "B" + CLI_OCR[4:]
    ops = [(o[0], o[1], o[2]) for o in _plantilla_ocr("N.I.F.:", leido)]
    ext, d = _extraer(_pdf(ops), EMP)
    assert d["destinatario"]["nif"] != CLI_OCR
    assert CLI_OCR not in ext["nifs_detectados"]


@pytest.mark.parametrize("rotulo", ["N.LF.:", "C.LF.:", "N.l.F.:", "C.1.F.:", "N.|.F.:"])
def test_ocr_rotulo_nif_mal_leido_se_reconoce(rotulo):
    """Con el rótulo leído «N.LF.», un NIF con forma válida y control erróneo (sin letras por cifras: no se corrige) se
    señala como ilegible, y el rótulo no se toma como parte del nombre."""
    malo = CLI_OCR[:-1] + str((int(CLI_OCR[-1]) + 1) % 10)
    assert not nifmod.es_valido(malo) and campos._nif_corregido_ocr(malo) is None
    assert campos.RE_NIF_ETIQUETA.search(f"{rotulo} {malo}")
    ext = campos.Extractor([_pagina_ocr(_plantilla_ocr(rotulo, malo))], "f.pdf", EMP).extraer()
    assert any(n["texto"] == malo for n in ext["nifs_no_validos"])
    assert ext["destinatario"]["nif"]["valor"] != malo


# --- Revisión: la raya con espacios solo tras el rótulo; renglones lejanos del domicilio, solo de domicilio y a confirmar --
REF8 = "20261105"                                             # referencia de 8 cifras cuya letra de control es válida
REF_DNI = REF8 + nifmod.LETRAS_DNI[int(REF8) % 23]
NUM_CIF = completar_cif("A", "0026001")                       # número de factura con forma de CIF válido


@pytest.mark.parametrize("t", [
    f"Pedido {REF8} – {REF_DNI[8]}", f"Ref. {REF8} – {REF_DNI[8]} cuenta", f"Expediente {REF8} — {REF_DNI[8]}.",
    f"Tel. {REF8} – {REF_DNI[8]}", f"Total {REF8[:2]}.{REF8[2:5]}.{REF8[5:]} – {REF_DNI[8]}",
    f"FACTURA Nº. {NUM_CIF[0]} – {NUM_CIF[1:]}", f"Serie {NUM_CIF[0]} — {NUM_CIF[1:]}", f"Contrato {REF8} – {REF_DNI[8]}",
    f"Nº {NUM_CIF[0]} - {NUM_CIF[1:]}", f"Pedido {REF8} - {REF_DNI[8]}",
])
def test_raya_con_espacios_sin_rotulo_no_es_nif(t):
    """La raya (o el guion) entre espacios dentro de un NIF solo vale detrás del rótulo del NIF: sin él, «A – 00260011» o
    «20261105 – Z» son un número de factura, una serie o una referencia."""
    nifs = {REF_DNI, NUM_CIF}
    assert not nifs & {n for n, _, _ in campos._nifs_en(t)}, t
    assert not nifs & {n for n, _, _ in nifmod.buscar_nifs(t)}, t
    if " - " not in t:   # (sin espacios, el guion es la forma «12345678-Z» de siempre; la raya sigue sin valer)
        x = re.sub(r"\s+", "", t)
        assert not nifs & {n for n, _, _ in campos._nifs_en(x)}, x
        assert not nifs & {n for n, _, _ in nifmod.buscar_nifs(x)}, x


@pytest.mark.parametrize("impreso, nif", [
    (f"C.I.F. nº {EMP2[0]} – {EMP2[1:]}", EMP2), (f"NIF núm.: {DNI[:8]} – {DNI[8]}", DNI),
    (f"CIF: ES {EMP2[0]} — {EMP2[1:]}", EMP2), (f"Cliente: Ficticia, S.L. - C.I.F.: {CLI[0]} - {CLI[1:]}", CLI),
])
def test_raya_con_espacios_tras_el_rotulo_es_nif(impreso, nif):
    assert [n for n, _, _ in campos._nifs_en(impreso) if nifmod.es_valido(n)] == [nif]
    assert [n for n, _, _ in nifmod.buscar_nifs(impreso)] == [nif]


def test_nif_sin_rotulo_seguido_de_raya_se_sigue_reconociendo():
    """Sin rótulo, la raya no entra en el NIF pero tampoco lo tapa: «… B00678904 – C/ Imaginaria» da el NIF."""
    for t in (f"DISTRIBUCIONES FICTICIAS, S.A. {CLI} – C/ Imaginaria, 12", f"{DNI} – Avda. Ficticia, 4"):
        esperado = CLI if CLI in t else DNI
        assert [n for n, _, _ in campos._nifs_en(t) if nifmod.es_valido(n)] == [esperado], t
        assert [n for n, _, _ in nifmod.buscar_nifs(t)] == [esperado], t


@pytest.mark.parametrize("renglones, numero", [
    (["D. Juan Ficticio Ejemplo", "C/ Imaginaria, 7", "30000 VILLAPRUEBA", f"Pedido {REF8} – {REF_DNI[8]}"], "B07/26"),
    (["D. Juan Ficticio Ejemplo", "C/ Imaginaria, 7", "30000 VILLAPRUEBA"], f"{NUM_CIF[0]} – {NUM_CIF[1:]}"),
])
def test_carta_referencia_o_numero_con_raya_no_es_el_nif_del_destinatario(renglones, numero):
    """Particular sin NIF en el bloque: una referencia «20261105 – Z» o el número «A – 00260011» no se toman por su NIF."""
    datos = _carta(f"CIF: {_barra(EMP)}", renglones, numero=numero)
    ext, d = _extraer(datos, EMP)
    assert d["emisor"]["nif"] == EMP
    assert d["destinatario"]["nif"] in (None, "")
    assert not {REF_DNI, NUM_CIF} & set(ext["nifs_detectados"])
    inc = _validar(ext, d)
    assert "DEST_FALTA" in {i["codigo"] for i in inc}


EMPRESA_CLI = ["DISTRIBUCIONES FICTICIAS, S.A.", f"C.I.F.: {CLI}"]


@pytest.mark.parametrize("siguiente", [
    "Ref. 10234", "Entrega: C/ Ficticia, 9, 28000 Villaficticia", "Lugar de entrega C/ Ficticia, 9, 28000 Villaficticia",
    "Pago (Domiciliación)", "30000",
])
def test_carta_renglon_tras_la_poblacion_no_es_domicilio(siguiente):
    """Más abajo de lo habitual (a 60 pt del NIF), tras «LOCALIDAD (Provincia)», lo que sigue (una referencia de cinco
    cifras, una dirección de entrega) ya no es el domicilio."""
    renglones = EMPRESA_CLI + ["Pol. Ind. Ficticio, nave 3", "VILLAPRUEBA (Provincia)", siguiente]
    ext, d = _extraer(_carta(f"CIF: {_barra(EMP)}", renglones), EMP)
    assert d["destinatario"]["nif"] == CLI
    assert d["destinatario"]["domicilio"] == "Pol. Ind. Ficticio, nave 3, VILLAPRUEBA (Provincia)"


@pytest.mark.parametrize("siguiente", ["Póliza 12345", "Ref. 10234", "Entrega: C/ Ficticia, 9, 28000 Villaficticia"])
def test_carta_particular_sin_cp_seguido_de_otro_renglon(siguiente):
    """Particular sin código postal: el domicilio acaba en «LOCALIDAD (Provincia)»; el renglón contiguo que sigue no se
    añade (ni su número de cinco cifras quita el aviso de que falta el código postal)."""
    renglones = ["Dña. Lucía Imaginaria Ejemplo", f"N.I.F.: {DNI}", "Residencial Imaginaria, 7B", "Urb. Ficticia",
                 "VILLAIMAGINARIA (Provincia)", siguiente]
    ext, d = _extraer(_carta(f"CIF: {_barra(EMP)}", renglones), EMP)
    assert d["destinatario"]["domicilio"] == "Residencial Imaginaria, 7B, Urb. Ficticia, VILLAIMAGINARIA (Provincia)"
    c = ext["destinatario"]["domicilio"]
    assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("código postal" in a for a in c["avisos"])


def test_carta_renglon_lejano_del_domicilio_siempre_a_confirmar():
    """Un domicilio completado con un renglón más abajo de lo habitual se pide confirmar aunque lleve código postal."""
    renglones = EMPRESA_CLI + ["Pol. Ind. Ficticio, nave 3", "Avda. Imaginaria, 12", "30000 VILLAPRUEBA"]
    ext, d = _extraer(_carta(f"CIF: {_barra(EMP)}", renglones), EMP)
    assert d["destinatario"]["domicilio"] == "Pol. Ind. Ficticio, nave 3, Avda. Imaginaria, 12, 30000 VILLAPRUEBA"
    c = ext["destinatario"]["domicilio"]
    assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("más abajo" in a for a in c["avisos"])


@pytest.mark.parametrize("poblacion", ["30000 VILLAPRUEBA", "C.P. 30000 VILLAPRUEBA", "30000 - Villaprueba"])
def test_carta_calle_sin_via_con_codigo_postal_a_confirmar(poblacion):
    """«Nombre, número» sin tipo de vía bajo el NIF, confirmado por el código postal: se toma, a confirmar."""
    renglones = EMPRESA_CLI + ["Residencial Imaginaria, 7B", poblacion]
    ext, d = _extraer(_carta(f"CIF: {_barra(EMP)}", renglones), EMP)
    assert d["destinatario"]["domicilio"].startswith("Residencial Imaginaria, 7B, ")
    assert "30000" in d["destinatario"]["domicilio"]
    c = ext["destinatario"]["domicilio"]
    assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("tipo de vía" in a for a in c["avisos"])


@pytest.mark.parametrize("renglones", [
    ["Att. Juan Ejemplo, 2", "C/ Imaginaria, 12", "30000 VILLAPRUEBA"],   # persona de contacto, no la calle
    ["Referencia, 14", "Pedido 12345"],                                    # cinco cifras que no son un código postal
    ["Referencia, 14", "Entrega: C/ Ficticia, 9"],                         # otra dirección rotulada
])
def test_calle_sin_via_sin_renglon_de_domicilio_que_la_confirme(renglones):
    ext, d = _extraer(_carta(f"CIF: {_barra(EMP)}", EMPRESA_CLI + renglones), EMP)
    assert d["destinatario"]["nif"] == CLI
    dom = d["destinatario"]["domicilio"] or ""
    assert "Att." not in dom and "Referencia" not in dom
