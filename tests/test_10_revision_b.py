"""Revisión B: números, fechas e importes (rejillas de cabecera, número citado de otra factura, bases de varios tipos,
combinación coherente con etiqueta, total neto de IRPF, robustez ante cifras y tablas límite). Datos ficticios."""
import io
import sqlite3

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, texto
from app.extraccion.texto import Pagina, Palabra
from app.nif import completar_cif

EMP = completar_cif("B", "1234567")
CLI = completar_cif("A", "8765432")


def _pdf(ops) -> bytes:
    """ops: (x, y_desde_arriba, texto, [tamaño], [fuente], [alineación l|r|c])."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    alto = A4[1]
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 9)
        {"r": c.drawRightString, "c": c.drawCentredString}.get(op[5] if len(op) > 5 else "l", c.drawString)(x, alto - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(ops, nombre="f.pdf", nif=EMP):
    return campos.Extractor(texto.leer(_pdf(ops)), nombre, nif).extraer()


def _validar(ext, nombre="f.pdf", nif=EMP, razon="EMPRESA DEMO S.L.", serie=None):
    d = borradores.datos_desde_extraccion(ext, nombre)
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    empresa = {"id": 1, "nif": nif, "razon_social": razon, "ambito_confirmado_en": "x", "ambito_dict": {"puede_emitir": True}}
    documento = {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}
    inc = borradores.validar(con, empresa, documento, d, extraccion=ext,
                             serie=serie or {"siguiente_numero": 1, "codigo": "X", "numero_detectado": None})
    return d, inc


def _cabecera(numero="Nº factura: F-2026/045", emisor="EMPRESA DEMO S.L.", nif_emisor=EMP):
    return [(50, 50, emisor, 12, "Helvetica-Bold"), (50, 64, "C/ Mayor 12, 28001 Madrid"), (50, 76, f"NIF: {nif_emisor}"),
            (380, 50, "FACTURA", 18, "Helvetica-Bold"), (380, 72, numero), (380, 84, "Fecha: 15/10/2026"),
            (330, 120, "Cliente:", 9, "Helvetica-Bold"), (330, 132, "DISTRIBUCIONES NORTE S.A."), (330, 144, "Av. Libertad 5"),
            (330, 156, "46001 Valencia"), (330, 168, f"CIF: {CLI}")]


def _tabla(filas, y0=230, cantidad="Cantidad"):
    ops = [(50, y0, "Concepto", 9, "Helvetica-Bold"), (330, y0, cantidad, 9, "Helvetica-Bold"),
           (420, y0, "Precio", 9, "Helvetica-Bold"), (540, y0, "Importe", 9, "Helvetica-Bold", "r")]
    for k, (c, q, p, i) in enumerate(filas):
        y = y0 + 18 + 14 * k
        ops += [(50, y, c), (370, y, q, 9, "Helvetica", "r"), (460, y, p, 9, "Helvetica", "r"), (540, y, i, 9, "Helvetica", "r")]
    return ops


def _en_linea(filas, y0=300):
    return [op for k, (et, v) in enumerate(filas) for op in ((380, y0 + 14 * k, et), (540, y0 + 14 * k, v, 9, "Helvetica", "r"))]


# --- F2: rejilla «fila de rótulos / fila de valores» -------------------------------------------------------------------
def _rejilla_cabecera(paso, alineacion="r"):
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
           (380, 40, "FACTURA", 18, "Helvetica-Bold")]
    for k, (et, v) in enumerate([("Nº factura", "F-2026/045"), ("Fecha factura", "05/10/2026"), ("Fecha operación", "28/09/2026")]):
        x = (300 if alineacion == "r" else 230) + k * paso
        ops += [(x, 110, et, 8, "Helvetica-Bold", alineacion), (x, 122, v, 9, "Helvetica", alineacion)]
    ops += [(330, 150, "Cliente:", 9, "Helvetica-Bold"), (330, 162, "DISTRIBUCIONES NORTE S.A."), (330, 174, "Av. Libertad 5"),
            (330, 186, "46001 Valencia"), (330, 198, f"CIF: {CLI}")]
    ops += _tabla([("Suministro de material eléctrico", "1", "700,00", "700,00")], y0=240)
    ops += _en_linea([("Base imponible", "700,00"), ("IVA 21%", "147,00"), ("Total factura", "847,00")])
    return ops


@pytest.mark.parametrize("paso,alineacion", [(110, "r"), (90, "r"), (80, "r"), (75, "l"), (90, "l")])
def test_rejilla_cabecera_cada_valor_con_su_rotulo(paso, alineacion):
    e = _extraer(_rejilla_cabecera(paso, alineacion))
    assert e["fecha"]["valor"] == "2026-10-05"
    fo = e["fecha_operacion"]
    assert fo["valor"] == "2026-09-28", fo
    assert fo["confianza"] >= 0.85 and not fo["avisos"]
    assert e["numero"]["valor"] == "F-2026/045"
    d, inc = _validar(e)
    assert d["fecha_operacion"] == "2026-09-28"
    assert "PLAZO_EXPEDICION" in {i["codigo"] for i in inc}  # la operación de septiembre se ve fuera de plazo


def test_rejilla_cabecera_varios_valores_sin_alinear_es_dudoso():
    ops = _cabecera() + [(300, 200, "Fecha operación", 8, "Helvetica-Bold"), (250, 212, "01/09/2026"), (345, 212, "28/09/2026")]
    ops += _tabla([("Servicio de mantenimiento", "1", "100,00", "100,00")], y0=250)
    ops += _en_linea([("Base imponible", "100,00"), ("IVA 21%", "21,00"), ("Total factura", "121,00")], y0=320)
    fo = _extraer(ops)["fecha_operacion"]
    assert fo["valor"] is not None and fo["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("Varios valores" in a for a in fo["avisos"])


def test_debajo_elige_el_bloque_bajo_el_rotulo():
    """El valor bajo un título se busca en el bloque centrado bajo él, no en el primero por la izquierda."""
    def pal(t, x, y):
        return Palabra(t, x, y, x + 5.5 * len(t), y + 9)
    pals = [pal("FACTURA", 400, 60), pal("REF-77", 330, 76), pal("2026/045", 410, 76), pal("Fecha:", 400, 95), pal("03/11/2026", 440, 95)]
    ex = campos.Extractor([Pagina(1, 595, 842, pals, "texto_pdf", 1.0)], "x.pdf", None)
    titulo = next(l for l in ex.lineas if l.texto == "FACTURA")
    assert ex._debajo(titulo.x0 - 40, titulo.x1 + 40, titulo).texto == "2026/045"


# --- F4: número de otra factura citado en el documento ------------------------------------------------------------------
def _con_referencia(etiqueta_propia):
    ops = _cabecera(numero=etiqueta_propia)
    ops += [(50, 215, "Liquidación final de la obra; el anticipo se abonó con la factura nº F-2026/030.")]
    ops += _tabla([("Reforma integral cocina", "1", "1.000,00", "1.000,00"), ("Anticipo factura F-2026/030", "1", "-300,00", "-300,00")],
                  y0=240)
    ops += _en_linea([("Base imponible", "700,00"), ("IVA 21%", "147,00"), ("Total factura", "847,00")], y0=310)
    return ops


@pytest.mark.parametrize("etiqueta", ["Número: F-2026/045", "Nº: F-2026/045", "FACTURA F-2026/045"])
def test_numero_propio_gana_a_la_factura_citada(etiqueta):
    n = _extraer(_con_referencia(etiqueta), nombre="F-2026-045.pdf")["numero"]
    assert n["valor"] == "F-2026/045"
    assert n["confianza"] < borradores.UMBRAL_CONFIANZA  # rótulo débil y otro número en el documento: se confirma
    assert any("F-2026/030" in a for a in n["avisos"])
    assert "Coincide con el nombre del archivo" in n["avisos"]


def test_numero_citado_no_coincide_con_el_archivo_por_el_anio():
    """Solo la cita de otra factura: se propone con baja confianza y el año común no la «confirma» por el nombre del archivo."""
    ops = _cabecera(numero="Fecha de entrega: 10/10/2026")
    ops += [(50, 215, "Liquidación final de la obra; el anticipo se abonó con la factura nº F-2026/030.")]
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops, nombre="F-2026-045.pdf")["numero"]
    assert n["valor"] == "F-2026/030"
    assert n["confianza"] < borradores.UMBRAL_CONFIANZA
    assert "Coincide con el nombre del archivo" not in n["avisos"]


def test_numero_factura_rectificada_es_una_referencia():
    ops = [op for op in _cabecera() if op[1] not in (72, 84)] + [(380, 72, "Nº factura rectificada: F-2026/030"),
                                                                  (380, 84, "Factura nº: R-2026/007"), (380, 96, "Fecha: 15/10/2026")]
    ops += _tabla([("Devolución de material", "1", "-100,00", "-100,00")], y0=240)
    assert _extraer(ops)["numero"]["valor"] == "R-2026/007"


# --- Rótulo «Nº:» leído por el OCR como «NO:» -----------------------------------------------------------------------------
def _ocr(texto_, x, y, conf=0.9):
    out = []
    for w in texto_.split():
        out.append(Palabra(w, x, y, x + 6 * len(w), y + 9, conf, "ocr"))
        x += 6 * len(w) + 4
    return out


@pytest.mark.parametrize("rotulo", ["NO:", "N0:", "N?:", "No.:"])
def test_numero_con_rotulo_ocr_no(rotulo):
    pals = _ocr("FACTURA", 420, 50) + _ocr(f"{rotulo} 166/26", 440, 80) + _ocr("Fecha: 25/09/2026", 440, 95) + \
        _ocr("Total factura 1.028,50", 350, 600)
    e = campos.Extractor([Pagina(1, 595, 842, pals, "ocr", 0.9)], "x.pdf", None).extraer()
    assert e["numero"]["valor"] == "166/26"
    assert e["numero"]["confianza"] < borradores.UMBRAL_CONFIANZA


def test_no_de_una_frase_no_es_rotulo_de_numero():
    pals = _ocr("FACTURA", 420, 50) + _ocr("No se admiten devoluciones pasados 30 días", 40, 80) + \
        _ocr("No 2 cajas abiertas", 40, 95) + _ocr("Total factura 1.028,50", 350, 600)
    e = campos.Extractor([Pagina(1, 595, 842, pals, "ocr", 0.9)], "x.pdf", None).extraer()
    assert e["numero"]["valor"] is None


def test_replica_escaneada_lee_el_numero():
    from conftest import pdf
    e = campos.Extractor(texto.leer(pdf("replica_166-26_escaneada.pdf")), "replica_166-26_escaneada.pdf", None).extraer()
    assert e["numero"]["valor"] == "166/26"
    assert e["numero"]["confianza"] < borradores.UMBRAL_CONFIANZA  # lectura OCR: se confirma


# --- F5: dos tipos de IVA sin «total base» impreso ---------------------------------------------------------------------------
def _dos_tipos_rejilla():
    ops = []
    for k, c in enumerate(["Base imponible", "% IVA", "Cuota IVA", "Total"]):
        xr = 50 + (k + 1) * 125 - 8
        ops.append((xr, 300, c, 8, "Helvetica-Bold", "r"))
        for j, v in enumerate([["500,00", "21", "105,00", ""], ["200,00", "10", "20,00", "825,00"]]):
            if v[k]:
                ops.append((xr, 312 + 12 * j, v[k], 9, "Helvetica", "r"))
    return ops


@pytest.mark.parametrize("totales", [
    _dos_tipos_rejilla(),
    _en_linea([("Base imponible 21%", "500,00"), ("IVA 21%", "105,00"), ("Base imponible 10%", "200,00"), ("IVA 10%", "20,00"),
               ("Total factura", "825,00")]),
], ids=["rejilla", "en_linea"])
def test_base_de_varios_tipos_es_la_suma(totales):
    ops = _cabecera() + _tabla([("Servicio de consultoría", "10", "50,00", "500,00"), ("Libros técnicos", "2", "100,00", "200,00")])
    e = _extraer(ops + totales)
    assert e["base_imponible"]["valor"] == "700.00" and e["base_imponible"]["metodo"] == "calculado"
    assert e["cuota_iva"]["valor"] == "125.00" and e["total_factura"]["valor"] == "825.00"
    _, inc = _validar(e)
    assert not {"BASE_DESCUADRE", "TOTAL_DESCUADRE", "LINEAS_DESCUADRE"} & {i["codigo"] for i in inc}


# --- F6 (silenciosos): combinación coherente entre los importes con etiqueta ------------------------------------------------
def test_anticipo_sobre_presupuesto_usa_la_combinacion_con_etiqueta():
    ops = _cabecera() + [(50, 210, "FACTURA DE ANTICIPO (30 %) SOBRE EL PRESUPUESTO P-2026/012 ACEPTADO", 9, "Helvetica-Bold")]
    ops += _tabla([("Demolición y retirada de escombros", "1", "2.000,00", "2.000,00"), ("Fontanería y electricidad", "1", "3.000,00",
                   "3.000,00"), ("Alicatado y solados", "1", "5.000,00", "5.000,00")])
    y = 320
    for et, v in (("Base imponible presupuesto", "10.000,00"), ("IVA 21%", "2.100,00"), ("Total presupuesto", "12.100,00"), (None, None),
                  ("Anticipo 30 % (base imponible)", "3.000,00"), ("Cuota IVA (21%)", "630,00"), ("TOTAL FACTURA", "3.630,00")):
        if et:
            ops += [(330, y, et), (540, y, v, 9, "Helvetica", "r")]
        y += 14 if et else 10
    e = _extraer(ops)
    assert (e["base_imponible"]["valor"], e["cuota_iva"]["valor"], e["total_factura"]["valor"]) == ("3000.00", "630.00", "3630.00")
    for k in ("base_imponible", "cuota_iva", "total_factura"):
        assert e[k]["confianza"] < borradores.UMBRAL_CONFIANZA  # hay otros importes con etiqueta: se confirma
    assert any("2100.00" in a or "12100.00" in a for a in e["total_factura"]["avisos"])
    assert [(d["tipo_iva"]["valor"], d["base"]["valor"], d["cuota"]["valor"]) for d in e["desglose"]] == [("21.00", "3000.00", "630.00")]


def test_resolver_prefiere_la_terna_con_total_etiquetado():
    def pal(t, x, y):
        return Palabra(t, x, y, x + 5.5 * len(t), y + 9)
    pals = [pal(v, 400, 400 + 14 * k) for k, v in enumerate(["1.000,00", "210,00", "1.210,00", "3.000,00", "630,00", "3.630,00"])]
    ex = campos.Extractor([Pagina(1, 595, 842, pals, "texto_pdf", 1.0)], "x.pdf", None)
    assert ex._resolver_aritmetica()["total_factura"]["valor"] == "3630.00"  # sin etiquetas: la mayor
    r = ex._resolver_aritmetica({"total": {campos.q2(campos.Decimal("1210"))}})
    assert (r["base_imponible"]["valor"], r["total_factura"]["valor"]) == ("1000.00", "1210.00")
    assert r["total_factura"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("3630.00" in a for a in r["total_factura"]["avisos"])


# --- F7: «TOTAL» neto de IRPF ---------------------------------------------------------------------------------------------------
def _irpf_rejilla():
    ops = []
    for k, (c, v) in enumerate(zip(["Base imponible", "% IVA", "Cuota IVA", "% IRPF", "Retención", "TOTAL"],
                                   ["700,00", "21", "147,00", "15", "105,00", "742,00"])):
        xr = 50 + (k + 1) * 85 - 8
        ops += [(xr, 300, c, 8, "Helvetica-Bold", "r"), (xr, 312, v, 9, "Helvetica", "r")]
    return ops


@pytest.mark.parametrize("totales", [
    _irpf_rejilla(),
    _en_linea([("Base imponible", "700,00"), ("IVA 21%", "147,00"), ("IRPF 15%", "-105,00"), ("TOTAL", "742,00")]),
], ids=["rejilla", "en_linea"])
def test_total_neto_de_irpf_es_el_importe_a_pagar(totales):
    nif = "50123456" + "TRWAGMYFPDXBNJZSQVHLCKE"[50123456 % 23]
    ops = _cabecera(numero="Nº factura: 2026-045", emisor="Laura Gómez Ruiz", nif_emisor=nif)
    ops += _tabla([("Diseño de identidad corporativa", "14", "50,00", "700,00")], cantidad="Horas")
    e = _extraer(ops + totales, nif=nif)
    assert e["total_factura"]["valor"] == "847.00" and e["total_factura"]["metodo"] == "calculado"
    assert e["total_a_pagar"]["valor"] == "742.00"
    assert e["retencion"]["importe"]["valor"] == "105.00" and e["retencion"]["porcentaje"]["valor"] == "15"
    _, inc = _validar(e, nif=nif, razon="Laura Gómez Ruiz")
    codigos = {i["codigo"] for i in inc if i["nivel"] == "bloqueo"}
    assert not {"TOTAL_DESCUADRE", "PAGAR_DESCUADRE", "RET_DESCUADRE"} & codigos
    assert "RETENCION" in {i["codigo"] for i in inc}


# --- F6 (robustez): la extracción no aborta ----------------------------------------------------------------------------------------
def _pdf_lineas(lineas):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 9)
    y = 800
    for t in lineas:
        c.drawString(40, y, t)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def test_tabla_con_cantidad_a_la_izquierda_y_cifra_en_el_concepto():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    c.drawString(50, 800, "Talleres Ejemplo S.L.   CIF: B12345674")
    c.drawString(50, 780, "Factura nº 2024-001   Fecha: 01/02/2024")
    c.drawString(50, 700, "Uds")
    c.drawString(100, 700, "Descripción")
    c.drawString(55, 680, "2")
    c.drawString(100, 680, "Tornillos")
    c.drawString(100, 665, "Caja 50")
    c.drawString(50, 600, "Base imponible 100,00")
    c.drawString(50, 585, "IVA 21% 21,00")
    c.drawString(50, 570, "Total factura 121,00")
    c.showPage()
    c.save()
    e = campos.Extractor(texto.leer(buf.getvalue()), "x.pdf", None).extraer()
    assert e["numero"]["valor"] == "2024-001" and e["total_factura"]["valor"] == "121.00"


@pytest.mark.parametrize("lineas", [
    ["Talleres Ejemplo S.L. CIF: B12345674", "Factura nº 2024-001  Fecha: 01/02/2024",
     "Base imponible 1234567890123456789012345678,00", "IVA 21% 21,00", "Total factura 121,00"],
    ["Talleres Ejemplo S.L. CIF: B12345674", "Factura nº 2024-001", *[""] * 20,
     "Observaciones: referencia interna 99999999999999999999999999999,99"],
], ids=["base_enorme", "cifra_suelta"])
def test_cifras_desmesuradas_no_abortan(lineas):
    e = campos.Extractor(texto.leer(_pdf_lineas(lineas)), "x.pdf", None).extraer()
    assert e["numero"]["valor"] == "2024-001"
    assert e["base_imponible"]["valor"] is None  # una cifra imposible no se toma como importe


def test_num_acota_los_importes():
    assert campos._num("1.234,56") == campos.Decimal("1234.56")
    assert campos._num("1" * 30 + ",00") is None
    assert campos._num("1.000.000.000.000.000.000.000.000.000,00") is None


def test_fallo_en_una_seccion_no_impide_el_resto(monkeypatch):
    def falla(self):
        raise ValueError("fallo simulado")
    monkeypatch.setattr(campos.Extractor, "importes", falla)
    monkeypatch.setattr(campos.Extractor, "_cuerpo_tabla", lambda self, *a: falla(self))
    ops = _cabecera() + _tabla([("Servicio", "1", "100,00", "100,00")]) + _en_linea([("Base imponible", "100,00"), ("Total", "121,00")])
    e = _extraer(ops)
    assert e["numero"]["valor"] == "F-2026/045" and e["emisor"]["nif"]["valor"] == EMP
    assert e["base_imponible"]["valor"] is None and any("Extracción parcial" in a for a in e["base_imponible"]["avisos"])
    assert e["lineas"] == [] and "Extracción parcial" in e["aviso_lineas"]
    _, inc = _validar(e)
    assert {"BASE_FALTA", "TOTAL_FALTA"} <= {i["codigo"] for i in inc if i["nivel"] == "bloqueo"}


# --- Reparación (números e importes): rejillas en casillas, valores pegados, número citado, cuota redondeada -----------------
def _cabecera_rejilla(rejilla):
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
           (380, 40, "FACTURA", 18, "Helvetica-Bold")] + rejilla
    ops += [(330, 150, "Cliente:", 9, "Helvetica-Bold"), (330, 162, "DISTRIBUCIONES NORTE S.A."), (330, 174, "Av. Libertad 5"),
            (330, 186, "46001 Valencia"), (330, 198, f"CIF: {CLI}")]
    ops += _tabla([("Suministro de material eléctrico", "1", "700,00", "700,00")], y0=240)
    ops += _en_linea([("Base imponible", "700,00"), ("IVA 21%", "147,00"), ("Total factura", "847,00")])
    return ops


@pytest.mark.parametrize("ancho", [90, 120])
@pytest.mark.parametrize("alin_valor", ["l", "c", "r"])
@pytest.mark.parametrize("tercera", [("Vencimiento", "04/11/2026"), ("Fecha operación", "28/09/2026")])
def test_rejilla_en_casillas_rotulo_arriba_izquierda(ancho, alin_valor, tercera):
    """Casillas con el rótulo arriba a la izquierda y el valor alineado a la derecha (o centrado) de la casilla: el valor
    no se alinea con ningún rótulo y se atribuye por el reparto en columnas de toda la fila."""
    cols = [("Nº factura", "F-2026/045"), ("Fecha factura", "05/10/2026"), tercera]
    ops = []
    for k, (et, v) in enumerate(cols):
        x0 = 200 + k * ancho
        ops.append((x0 + 3, 110, et, 7, "Helvetica-Bold"))
        xv = {"l": x0 + 3, "c": x0 + ancho / 2, "r": x0 + ancho - 4}[alin_valor]
        ops.append((xv, 123, v, 9, "Helvetica", alin_valor))
    e = _extraer(_cabecera_rejilla(ops))
    assert e["numero"]["valor"] == "F-2026/045" and e["numero"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert e["fecha"]["valor"] == "2026-10-05" and e["fecha"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    fo = e["fecha_operacion"]
    if tercera[0] == "Fecha operación":
        assert fo["valor"] == "2026-09-28" and fo["confianza"] >= borradores.UMBRAL_CONFIANZA
    else:
        assert fo["valor"] is None
    _, inc = _validar(e)
    assert not {i["codigo"] for i in inc if i["nivel"] == "bloqueo"}


@pytest.mark.parametrize("extra", [[(50, 123, "info@empresademo.es")], [(50, 110, "Tel. 910 000 000"), (50, 123, "Pol. Ind. Sur, nave 4")]],
                         ids=["texto_en_fila_de_valores", "texto_en_ambas_filas"])
def test_rejilla_en_casillas_con_otro_texto_en_la_fila(extra):
    """Otro texto en las mismas filas, lejos de la rejilla (el bloque del emisor a la izquierda), no rompe el reparto."""
    ops = list(extra)
    for k, (et, v) in enumerate([("Nº factura", "F-2026/045"), ("Fecha factura", "05/10/2026"), ("Fecha operación", "28/09/2026")]):
        x0 = 230 + k * 110
        ops += [(x0 + 3, 110, et, 7, "Helvetica-Bold"), (x0 + 106, 123, v, 9, "Helvetica", "r")]
    e = _extraer(_cabecera_rejilla(ops))
    assert (e["numero"]["valor"], e["fecha"]["valor"], e["fecha_operacion"]["valor"]) == ("F-2026/045", "2026-10-05", "2026-09-28")
    assert min(e[k]["confianza"] for k in ("numero", "fecha", "fecha_operacion")) >= borradores.UMBRAL_CONFIANZA


@pytest.mark.parametrize("alin,paso,t_rot,t_val", [("c", 55, 7, 10), ("c", 58, 7, 10), ("r", 58, 7, 10), ("l", 58, 7, 10),
                                                   ("r", 52, 6, 9), ("c", 52, 6, 9)])
def test_rejilla_valores_pegados_se_separan_por_rotulo(alin, paso, t_rot, t_val):
    """Valores de columnas vecinas tan juntos que forman un solo tramo («05/10/2026 28/09/2026»): cada uno va a su rótulo."""
    ops = []
    for k, (et, v) in enumerate([("Nº factura", "F-2026/045"), ("Fecha factura", "05/10/2026"), ("Fecha entrega", "28/09/2026")]):
        x = 300 + k * paso
        ops += [(x, 110, et, t_rot, "Helvetica-Bold", alin), (x, 122, v, t_val, "Helvetica", alin)]
    e = _extraer(_cabecera_rejilla(ops))
    fo = e["fecha_operacion"]
    assert fo["valor"] == "2026-09-28", fo
    assert e["fecha"]["valor"] == "2026-10-05"
    d, inc = _validar(e)
    assert d["fecha_operacion"] == "2026-09-28" and "PLAZO_EXPEDICION" in {i["codigo"] for i in inc}


def test_valor_de_varias_palabras_no_se_parte():
    """Un valor de varias palabras bajo un rótulo de la rejilla no se trocea aunque alguna palabra caiga bajo otro rótulo."""
    ops = [(300, 110, "Fecha operación", 8, "Helvetica-Bold"), (390, 110, "Vencimiento", 8, "Helvetica-Bold"),
           (300, 122, "15 de septiembre de 2026"), (420, 122, "04/11/2026")]
    e = _extraer(_cabecera_rejilla(ops))
    assert e["fecha_operacion"]["valor"] == "2026-09-15"


REFERENCIAS_A_OTRA = [
    [(50, 203, "Liquidación final de la obra; el anticipo se abonó con la"), (50, 215, "factura nº F-2026/030.")],
    [(50, 215, "Anticipo: factura nº F-2026/030")],
    [(50, 215, "Descontado anticipo factura nº F-2026/030 (300,00 €)")],
    [(50, 215, "Regularización s/ factura nº F-2026/030")],
    [(50, 215, "Ref. factura nº F-2026/030")],
    [(50, 215, "Factura nº F-2026/030 (anticipo) ya abonada")],
    [(50, 215, "Anticipo cobrado: Fra. nº F-2026/030")],
    [(330, 185, "Anticipo factura nº F-2026/030")],
]


@pytest.mark.parametrize("referencia", REFERENCIAS_A_OTRA, ids=lambda r: r[-1][2][:30])
@pytest.mark.parametrize("propio", ["Número: F-2026/045", "Nº: F-2026/045", "FACTURA F-2026/045"])
@pytest.mark.parametrize("archivo", ["F-2026-045.pdf", "factura.pdf"])
def test_numero_citado_en_otras_redacciones_no_gana_al_propio(referencia, propio, archivo):
    ops = [op for op in _cabecera(numero=propio)] + referencia
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    ops += _en_linea([("Base imponible", "700,00"), ("IVA 21%", "147,00"), ("Total factura", "847,00")], y0=310)
    n = _extraer(ops, nombre=archivo)["numero"]
    assert n["valor"] == "F-2026/045", n
    assert n["confianza"] < borradores.UMBRAL_CONFIANZA and any("F-2026/030" in a for a in n["avisos"])


@pytest.mark.parametrize("linea", ["Duplicado de factura nº F-2026/045", "Copia de la factura nº F-2026/045",
                                   "Nº factura anticipo: F-2026/045", "Nº factura original: F-2026/045",
                                   "Nº factura (original): F-2026/045"])
@pytest.mark.parametrize("extra", [[], [(50, 100, "Nº cliente: 4471")]], ids=["solo", "con_n_cliente"])
def test_numero_propio_de_duplicado_o_anticipo_no_es_una_cita(linea, extra):
    ops = [op for op in _cabecera() if op[1] != 72] + [(330, 72, linea)] + extra
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops)["numero"]
    assert n["valor"] == "F-2026/045" and n["confianza"] >= borradores.UMBRAL_CONFIANZA and not n["avisos"]


def test_numero_calificado_con_otro_numero_debil_se_confirma():
    """«Nº factura anticipo: X» y además un «Nº: Y» sin calificar: gana el de factura, pero se pide confirmar."""
    ops = [op for op in _cabecera() if op[1] != 72] + [(330, 72, "Nº factura anticipo: F-2026/045"), (330, 96, "Nº: 2026-0981")]
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops)["numero"]
    assert n["valor"] == "F-2026/045" and n["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("2026-0981" in a for a in n["avisos"])


@pytest.mark.parametrize("totales", [
    _en_linea([("Base imponible 21%", "501,00"), ("IVA 21%", "105,22"), ("Base imponible 10%", "200,00"), ("IVA 10%", "20,00"),
               ("Total factura", "826,22")]),
], ids=["en_linea"])
def test_cuota_redondeada_por_lineas_con_dos_tipos(totales):
    """Cuota del 21 % calculada línea a línea (un céntimo más que base × tipo): la base del 21 % sigue emparejada, la base
    imponible es la suma y el céntimo se pide confirmar como en una factura de un solo tipo."""
    ops = _cabecera() + _tabla([("Licencia anual software", "1", "250,50", "250,50"), ("Soporte técnico", "1", "250,50", "250,50"),
                                ("Libros técnicos", "2", "100,00", "200,00")])
    e = _extraer(ops + totales)
    assert [(x["tipo_iva"]["valor"], x["base"]["valor"]) for x in e["desglose"]] == [("21.00", "501.00"), ("10.00", "200.00")]
    assert e["base_imponible"]["valor"] == "701.00"
    _, inc = _validar(e)
    assert not {i["codigo"] for i in inc if i["nivel"] == "bloqueo"}
    assert "CUOTA_DESCUADRE" in {i["codigo"] for i in inc if i["nivel"] == "confirmar"}


def test_base_de_un_tipo_no_se_toma_por_la_total():
    """Desglose incompleto (la cuota del 21 % no cuadra con ninguna base): la base de un solo tipo no pasa por la total."""
    ops = _cabecera() + _tabla([("Licencia anual software", "1", "501,00", "501,00"), ("Libros técnicos", "2", "100,00", "200,00")])
    ops += _en_linea([("Base imponible 21%", "501,00"), ("IVA 21%", "110,00"), ("Base imponible 10%", "200,00"), ("IVA 10%", "20,00"),
                      ("Total factura", "831,00")])
    b = _extraer(ops)["base_imponible"]
    assert b["valor"] != "200.00" and b["confianza"] < borradores.UMBRAL_CONFIANZA


@pytest.mark.parametrize("metodo,campo", [("fecha_operacion", "fecha_operacion"), ("pago", "forma_pago"),
                                          ("referencias", "documento")])
def test_fallo_en_una_seccion_opcional_se_pide_revisar(monkeypatch, metodo, campo):
    def falla(self):
        raise ValueError("fallo simulado")
    monkeypatch.setattr(campos.Extractor, metodo, falla)
    ops = _cabecera() + _tabla([("Servicio", "1", "100,00", "100,00")]) + \
        _en_linea([("Base imponible", "100,00"), ("IVA 21%", "21,00"), ("Total", "121,00")])
    e = _extraer(ops)
    assert [p["campo"] for p in e["extraccion_parcial"]] == [campo]
    d, inc = _validar(e)
    assert ("confirmar", campo) in {(i["nivel"], i["campo"]) for i in inc if i["codigo"] == "EXTRACCION_PARCIAL"}
    # corregido el campo afectado, deja de pedirse
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    inc2 = borradores.validar(con, {"id": 1, "nif": EMP, "razon_social": "EMPRESA DEMO S.L.", "ambito_confirmado_en": "x",
                                    "ambito_dict": {"puede_emitir": True}},
                              {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}, d, extraccion=e,
                              serie={"siguiente_numero": 1, "codigo": "X", "numero_detectado": None}, cambiados={campo})
    assert "EXTRACCION_PARCIAL" not in {i["codigo"] for i in inc2}


def test_extraccion_completa_no_anota_secciones():
    e = _extraer(_cabecera() + _tabla([("Servicio", "1", "100,00", "100,00")]) +
                 _en_linea([("Base imponible", "100,00"), ("IVA 21%", "21,00"), ("Total", "121,00")]))
    assert e["extraccion_parcial"] == []


# --- Ronda 2: cita de otra factura con un rótulo limpio, rótulos propios con texto delante, base de un solo tipo --------
REFERENCIAS_LIMPIAS = [
    [(50, 203, "Facturas a cuenta descontadas:"), (50, 215, "- Factura nº F-2026/030: 300,00 €")],
    [(50, 203, "Rectifica a:"), (50, 215, "Factura nº F-2026/030")],
    [(50, 203, "Documentos relacionados", 9, "Helvetica-Bold"), (50, 215, "Factura nº F-2026/030")],
    [(50, 203, "Observaciones:", 9, "Helvetica-Bold"), (50, 215, "Factura nº F-2026/030 queda saldada.")],
    [(50, 203, "Liquidación final de la obra; el anticipo se abonó con la"), (50, 215, "Factura nº F-2026/030.")],
    [(50, 215, "Factura nº F-2026/030 pagada el 01/09/2026")],
    [(50, 215, "Factura nº: F-2026/030 (pro forma aceptada)")],
]


@pytest.mark.parametrize("referencia", REFERENCIAS_LIMPIAS, ids=lambda r: r[-1][2][:30])
@pytest.mark.parametrize("propio", ["Número: F-2026/045", "Nº: F-2026/045", "FACTURA F-2026/045"])
def test_cita_con_rotulo_limpio_bajo_el_numero_propio_debil_no_pasa_sin_confirmar(referencia, propio):
    """El propio número con un rótulo débil en la cabecera y, más abajo, otra factura citada con un rótulo limpio en una
    redacción no reconocida: si se propone la citada, se pide confirmar con los dos números a la vista."""
    ops = _cabecera(numero=propio) + referencia
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    ops += _en_linea([("Base imponible", "700,00"), ("IVA 21%", "147,00"), ("Total factura", "847,00")], y0=310)
    n = _extraer(ops, nombre="factura.pdf")["numero"]
    if n["valor"] != "F-2026/045":
        assert n["confianza"] < borradores.UMBRAL_CONFIANZA
        assert any("F-2026/030" in a and "F-2026/045" in a for a in n["avisos"])


@pytest.mark.parametrize("cabecera", [
    [(380, 66, "Rectifica a: Factura nº F-2026/030"), (380, 80, "Nº: F-2026/045")],
    [(380, 62, "Anticipo:"), (380, 72, "Factura nº F-2026/030"), (380, 84, "Nº: F-2026/045")],
], ids=["rotulo_delante", "encabezado_encima"])
def test_cita_encima_del_numero_propio_debil_se_confirma(cabecera):
    ops = [op for op in _cabecera() if op[1] not in (72, 84)] + cabecera + [(380, 96, "Fecha: 15/10/2026")]
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops)["numero"]
    assert n["valor"] == "F-2026/045" or (n["confianza"] < borradores.UMBRAL_CONFIANZA and
                                          any("F-2026/045" in a for a in n["avisos"]))


def test_cita_en_frase_partida_con_rotulo_en_mayuscula_es_una_referencia():
    ops = _cabecera() + [(50, 203, "Se descuenta el anticipo correspondiente a"), (50, 215, "Factura nº F-2026/030 (300,00 €).")]
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops)["numero"]
    assert n["valor"] == "F-2026/045" and n["confianza"] >= borradores.UMBRAL_CONFIANZA and not n["avisos"]


@pytest.mark.parametrize("propio", [
    [(380, 72, "Nuestra factura nº F-2026/045")], [(380, 72, "Su factura nº F-2026/045")],
    [(380, 72, "Servicios de reforma y"), (380, 84, "factura nº F-2026/045")],
    [(300, 72, "Página 1 de 1    Factura nº F-2026/045")], [(300, 72, "Madrid, 15/10/2026 - Factura nº F-2026/045")],
    [(300, 72, "Copia cliente   Factura nº F-2026/045")], [(380, 72, "Factura nº F-2026/045 - anticipo 30 %")],
], ids=lambda p: p[-1][2][:28])
@pytest.mark.parametrize("extra", [[], [(50, 100, "Nº cliente: 4471")], [(50, 100, "Pedido nº PO-7781")],
                                   [(50, 100, "Nº: 2026-0981")]], ids=["solo", "n_cliente", "pedido", "otro_n_debajo"])
def test_rotulo_propio_con_texto_delante_o_detras_no_pide_confirmar(propio, extra):
    """Rótulos del propio número con un posesivo, otra casilla delante o un calificativo detrás: igual que sin ellos, y un
    «Nº:» suelto impreso debajo no los pone en duda."""
    ops = [op for op in _cabecera() if op[1] not in (72, 84)] + propio + [(380, 96, "Fecha: 15/10/2026")] + extra
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops)["numero"]
    assert n["valor"] == "F-2026/045" and n["confianza"] >= borradores.UMBRAL_CONFIANZA and not n["avisos"], n


def test_numero_debil_encima_del_rotulo_fuerte_se_confirma():
    """Un «Nº:» suelto impreso encima del «Factura nº»: puede ser el propio; se pide confirmar."""
    ops = [op for op in _cabecera() if op[1] != 72] + [(50, 30, "Nº: 2026-0981"), (380, 72, "Factura nº F-2026/045")]
    ops += _tabla([("Reforma integral cocina", "1", "700,00", "700,00")], y0=240)
    n = _extraer(ops)["numero"]
    assert n["confianza"] < borradores.UMBRAL_CONFIANZA and any("2026-0981" in a for a in n["avisos"])


@pytest.mark.parametrize("rotulo_10", ["B.I. 10%", "Base reducida 10%", "Base IVA 10%", "Neto 10%", "Bases 10%"])
def test_varios_tipos_con_una_sola_base_impresa_se_confirma(rotulo_10):
    """Dos tipos sin base total impresa y la base del 10 % con un rótulo no reconocido: la única base leída es la del 21 %,
    no la de la factura."""
    ops = _cabecera() + _tabla([("Servicio de consultoría", "10", "50,00", "500,00"), ("Libros técnicos", "2", "100,00", "200,00")])
    ops += _en_linea([("Base imponible 21%", "500,00"), ("IVA 21%", "105,00"), (rotulo_10, "200,00"), ("IVA 10%", "20,00"),
                      ("Total factura", "825,00")])
    b = _extraer(ops)["base_imponible"]
    assert b["valor"] == "700.00" or (b["confianza"] < borradores.UMBRAL_CONFIANZA and b["avisos"])


def test_varios_tipos_con_solo_la_base_total_no_se_confirma():
    ops = _cabecera() + _tabla([("Servicio de consultoría", "10", "50,00", "500,00"), ("Libros técnicos", "2", "100,00", "200,00")])
    ops += _en_linea([("Base imponible", "700,00"), ("IVA 21%", "105,00"), ("IVA 10%", "20,00"), ("Total factura", "825,00")])
    b = _extraer(ops)["base_imponible"]
    assert b["valor"] == "700.00" and b["confianza"] >= borradores.UMBRAL_CONFIANZA and not b["avisos"]
