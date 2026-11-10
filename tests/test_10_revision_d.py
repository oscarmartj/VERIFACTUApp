"""Revisión D: puerta del borrador (total deducido, duplicados archivados, retención de garantía, tabla sin cabecera,
lecturas dudosas sin correspondencia y ediciones con tipos inesperados). Solo datos sintéticos."""
import io
import json

import pytest
from conftest import borrador_listo, preparar_empresa
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app import borradores, db
from app.extraccion import campos, texto

NIF_EMP = "B82991316"  # empresa ficticia de las pruebas (EMPRESA_REPLICA)
W, H = A4


def _pdf(textos) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for x, y, s, tam, al, neg in textos:
        c.setFont("Helvetica-Bold" if neg else "Helvetica", tam)
        (c.drawRightString if al == "r" else c.drawString)(x * mm, H - y * mm, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _factura(numero="166/26", lineas=None, totales=None, notas=(), cabecera_tabla=True, pie="Documento de prueba") -> bytes:
    hoy = db.ahora().strftime("%d/%m/%Y")
    t = [(18, 20, "INGENIERÍA FICTICIA Y ASOCIADOS S.L.U.", 11, "l", True), (18, 26, "C/ Inventada de Pruebas, 99", 9, "l", False),
         (18, 31, "28000 Madrid", 9, "l", False), (18, 36, f"C.I.F.: {NIF_EMP}", 9, "l", False),
         (192, 20, "FACTURA", 18, "r", True), (192, 28, f"Nº: {numero}", 10, "r", False), (192, 34, f"Fecha: {hoy}", 10, "r", False),
         (110, 50, "CLIENTE", 8, "l", True), (110, 56, "PROMOCIONES CLIENTE S.A.", 9, "l", True),
         (110, 61, "Avda. de los Datos Sintéticos, 7", 9, "l", False), (110, 66, "41000 SEVILLA", 9, "l", False),
         (110, 71, "C.I.F.: A87754214", 9, "l", False)]
    y = 100
    if cabecera_tabla:
        t += [(20, y, "CONCEPTO", 9, "l", True), (128, y, "CANTIDAD", 9, "r", True), (165, y, "PRECIO", 9, "r", True),
              (190, y, "IMPORTE", 9, "r", True)]
    y += 9
    for concepto, cant, precio, imp in lineas or [("Asistencia técnica en obra", "1,00", "1.000,00", "1.000,00")]:
        t.append((20, y, concepto, 9, "l", False))
        for x, v in ((128, cant), (165, precio), (190, imp)):
            if v:
                t.append((x, y, v, 9, "r", False))
        y += 6
    y += 12
    for etq, val in totales or [("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00"), ("TOTAL FACTURA", "1.210,00")]:
        t += [(160, y, etq, 9, "r", etq.startswith("TOTAL")), (190, y, val + " €", 9, "r", False)]
        y += 6
    y += 8
    for n in notas:
        t.append((18, y, n, 8.5, "l", False))
        y += 5
    t.append((30, 280, pie, 7, "l", False))
    return _pdf(t)


def _extraer(datos: bytes):
    ext = campos.Extractor(texto.leer(datos), "prueba.pdf", NIF_EMP).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "prueba.pdf")


@pytest.fixture()
def ctx(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    emp = dict(db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), ambito_dict={"puede_emitir": True})
    yield c, eid, con, emp
    con.close()


def _validar(con, emp, d, ext, cambiados=None):
    return borradores.validar(con, emp, None, d, extraccion=ext, cambiados=cambiados,
                              serie={"codigo": "A", "siguiente_numero": 166, "numero_detectado": 166})


def _codigos(inc, nivel=None):
    return {i["codigo"] for i in inc if nivel is None or i["nivel"] == nivel}


# --- INT-2: total no leído --------------------------------------------------------------------------------------------
def test_total_leido_no_pide_confirmacion(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_factura())
    assert d["importe_total"] == "1210.00"
    assert "TOTAL_NO_LEIDO" not in _codigos(_validar(con, emp, d, ext))


def test_total_no_impreso_se_deduce_pero_exige_confirmacion(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_factura(totales=[("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00")]))
    assert ext["total_factura"]["valor"] is None and d["importe_total"] == "1210.00"
    inc = _validar(con, emp, d, ext)
    assert "TOTAL_NO_LEIDO" in _codigos(inc, "confirmar")
    # si el usuario indica el total, ya no es una deducción
    assert "TOTAL_NO_LEIDO" not in _codigos(_validar(con, emp, d, ext, cambiados={"importe_total"}))


def test_total_con_cifras_sin_interpretar_no_se_deduce(ctx):
    """Un total con rótulo no reconocido distinto de base + cuota (incluye una tasa) no se sustituye por base + cuota."""
    _, _, con, emp = ctx
    ext, d = _extraer(_factura(totales=[("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00"),
                                        ("Tasa de residuos", "30,00"), ("IMPORTE", "1.240,00")]))
    assert ext["total_factura"]["valor"] is None and "1240.00" in ext["total_factura"]["sin_clasificar"]
    assert d["importe_total"] is None and d["total_a_pagar"] is None
    inc = _validar(con, emp, d, ext)
    falta = [i for i in inc if i["codigo"] == "TOTAL_FALTA"]
    assert falta and falta[0]["nivel"] == "bloqueo" and "1240.00" in falta[0]["mensaje"]
    # el usuario indica el total impreso: no cuadra con base + cuota y sigue bloqueado (caso no soportado)
    inc = _validar(con, emp, dict(d, importe_total="1240.00"), ext, cambiados={"importe_total"})
    assert "TOTAL_DESCUADRE" in _codigos(inc, "bloqueo")


# --- INT-5: tabla sin cabecera ----------------------------------------------------------------------------------------
def test_tabla_sin_cabecera_exige_revision_y_precio_sin_cantidad_bloquea(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_factura(cabecera_tabla=False, lineas=[("Horas de consultoría", "12", None, "600,00"),
                                                              ("Desplazamientos", "2", None, "400,00")]))
    assert ext["aviso_lineas"] and ext["lineas"]
    inc = _validar(con, emp, d, ext)
    assert "TABLA_DEDUCIDA" in _codigos(inc, "confirmar")
    sin_cant = [i for i in inc if i["codigo"] == "LINEA_SIN_CANTIDAD"]
    assert {i["campo"] for i in sin_cant} == {"lineas.0.cantidad", "lineas.1.cantidad"} and all(i["nivel"] == "bloqueo" for i in sin_cant)
    # corregidas las líneas, queda solo la confirmación de la tabla deducida
    d2 = json.loads(json.dumps(d))
    d2["lineas"][0].update(cantidad="12", precio="50")
    d2["lineas"][1].update(cantidad="2", precio="200")
    inc = _validar(con, emp, d2, ext)
    assert not _codigos(inc) & {"LINEA_SIN_CANTIDAD", "LINEA_DESCUADRE"} and "TABLA_DEDUCIDA" in _codigos(inc, "confirmar")


def test_linea_sin_cantidad_con_precio_igual_al_importe_es_valida(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_factura())
    d["lineas"] = [{"concepto": "Servicio", "cantidad": None, "unidad": None, "precio": "1000.00", "importe": "1000.00"}]
    assert "LINEA_SIN_CANTIDAD" not in _codigos(_validar(con, emp, d, ext))


# --- INT-7: lecturas dudosas que antes se descartaban -----------------------------------------------------------------
def test_fecha_operacion_y_vencimiento_dudosos_piden_confirmacion(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_factura())
    for k, v in (("fecha_operacion", "2026-10-15"), ("vencimiento", "2026-12-15")):
        ext[k] = {"valor": v, "texto": v, "metodo": "ocr", "confianza": 0.3, "avisos": []}
        d[k] = v
    assert borradores._ruta_borrador("fecha_operacion") == "fecha_operacion"
    assert borradores._ruta_borrador("vencimiento") == "vencimiento"
    # ninguna lectura dudosa se descarta: otras columnas de la línea o campos sin equivalente se confirman igualmente
    assert borradores._ruta_borrador("lineas.2.dto") == "lineas.2"
    assert borradores._ruta_borrador("recargo_equivalencia") == "documento"
    dudosos = {i["campo"] for i in _validar(con, emp, d, ext) if i["codigo"] == "DUDOSO"}
    assert {"fecha_operacion", "vencimiento"} <= dudosos
    # corregida por el usuario, deja de pedirse
    dudosos = {i["campo"] for i in _validar(con, emp, d, ext, cambiados={"fecha_operacion"}) if i["codigo"] == "DUDOSO"}
    assert "fecha_operacion" not in dudosos


# --- INT-4: retención de garantía -------------------------------------------------------------------------------------
def _con_retencion(rotulo, importe, pagar, notas=()):
    return _factura(totales=[("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00"), ("TOTAL FACTURA", "1.210,00"),
                             (rotulo, "-" + importe), ("TOTAL A PAGAR", pagar)], notas=notas)


@pytest.mark.parametrize("rotulo,importe,pagar,notas,bloqueos,confirmar", [
    # nota de garantía y retención sin IRPF con un tipo que no es de IRPF: garantía, no soportada
    ("Retención 5%", "50,00", "1.160,00", ["Retención de garantía: se abonará a la recepción definitiva de la obra."],
     {"RETENCION_GARANTIA"}, set()),
    # el propio rótulo dice garantía
    ("Garantía: retención 5%", "50,00", "1.160,00", [], {"RETENCION_GARANTIA"}, set()),
    # IRPF explícito con una mención a la garantía: se confirma expresamente
    ("Retención IRPF 15%", "150,00", "1.060,00", ["Retención de garantía: no se practica en esta certificación."],
     set(), {"RETENCION_GARANTIA"}),
    # IRPF habitual sin menciones: solo la confirmación general
    ("Retención IRPF 15%", "150,00", "1.060,00", [], set(), set()),
    # porcentaje que no es un tipo habitual de IRPF: confirmación específica
    ("Retención 5%", "50,00", "1.160,00", [], set(), {"RET_TIPO_INUSUAL"}),
])
def test_retencion_garantia_no_se_trata_como_irpf(ctx, rotulo, importe, pagar, notas, bloqueos, confirmar):
    _, _, con, emp = ctx
    ext, d = _extraer(_con_retencion(rotulo, importe, pagar, notas))
    assert d["retencion"] and d["retencion"]["importe"]
    inc = _validar(con, emp, d, ext)
    especificos = {"RETENCION_GARANTIA", "RET_TIPO_INUSUAL"}
    assert _codigos(inc, "bloqueo") & especificos == bloqueos
    assert _codigos(inc, "confirmar") & especificos == confirmar
    assert "RETENCION" in _codigos(inc, "confirmar")


def test_retencion_sin_porcentaje_exige_indicarlo(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_con_retencion("Retención IRPF", "150,00", "1.060,00"))
    assert d["retencion"]["porcentaje"] is None
    assert "RET_PORCENTAJE_FALTA" in _codigos(_validar(con, emp, d, ext), "bloqueo")
    d["retencion"]["porcentaje"] = "15"
    assert not _codigos(_validar(con, emp, d, ext)) & {"RET_PORCENTAJE_FALTA", "RET_DESCUADRE"}


# --- INT-3: factura ya archivada como expedida ------------------------------------------------------------------------
def _subir(c, eid, nombre, datos):
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (nombre, datos, "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    return c.ok(r)[0]


def _clasificar(c, did, clasificacion):
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": clasificacion, "motivo": "prueba"}))


def test_copia_de_factura_archivada_como_expedida_no_se_reexpide(ctx):
    c, eid, _, _ = ctx
    archivada = _subir(c, eid, "166-26_entregada.pdf", _factura(pie="Copia entregada al cliente"))["documento_id"]
    _clasificar(c, archivada, "emitida_propia")
    copia = _subir(c, eid, "166-26.pdf", _factura())
    assert any(x["tipo"] == "documento" and x["clasificacion"] == "emitida_propia" for x in copia["duplicados_fiscales"])
    did = copia["documento_id"]
    assert c.ok(c.get(f"/api/documentos/{did}"))["clasificacion_sugerida"]["sugerida"] == "emitida_propia"
    _clasificar(c, did, "preparatorio")
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert "DUPLICADA_ARCHIVADA" in _codigos(b["incidencias"], "bloqueo") and not b["puede_emitir"]
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    r = c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "dup-1", "confirmo": True})
    assert r.status_code == 409
    # si el otro PDF no era una factura expedida (el usuario lo reclasifica), basta con confirmar que no es la misma
    _clasificar(c, archivada, "desconocido")
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert "DUPLICADA_ARCHIVADA" not in _codigos(b["incidencias"]) and "DUPLICADO_DOCUMENTO" in _codigos(b["incidencias"], "confirmar")


# --- INT-8: ediciones con tipos inesperados ---------------------------------------------------------------------------
@pytest.mark.parametrize("campo,valor", [
    ("desglose", [1]), ("lineas", ["a"]), ("retencion", "x"), ("num_serie", 123), ("importe_total", "1E+30"),
    ("importe_total", "NaN"), ("lineas", [{"concepto": "x", "otro": "y"}]), ("desglose.0.base", {"a": 1}),
    ("emisor.nombre", ["x"]), ("importe_total", True),
])
def test_edicion_con_tipo_inesperado_se_rechaza(app_cliente, campo, valor):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b0 = borrador_listo(c, eid)
    r = c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": campo, "valor": valor, "motivo": "x"}]})
    assert r.status_code == 422, r.text
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert b["datos"] == b0["datos"] and b["puede_emitir"]


def test_edicion_valida_y_retencion_desde_vacia(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b0 = borrador_listo(c, eid)
    assert b0["datos"]["retencion"] is None
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "retencion.porcentaje", "valor": "15", "motivo": "x"},
        {"campo": "observaciones", "valor": "  Nota  ", "motivo": "x"},
        {"campo": "desglose.0.base", "valor": 850, "motivo": "número JSON en un importe"}]}))
    assert b["datos"]["retencion"] == {"porcentaje": "15"} and b["datos"]["observaciones"] == "Nota"
    assert b["datos"]["desglose"][0]["base"] == "850"


def test_datos_guardados_corruptos_no_rompen_la_validacion(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b0 = borrador_listo(c, eid)
    datos = dict(b0["datos"], desglose=[1], lineas=["a"], retencion="x", num_serie=123, emisor="y", importe_total="1E+30")
    con = db.conectar()
    with db.transaccion(con):
        con.execute("UPDATE borradores SET datos=? WHERE id=?", (json.dumps(datos), bid))
    con.close()
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    malos = {i["campo"] for i in b["incidencias"] if i["codigo"] == "DATO_NO_VALIDO"}
    assert {"desglose.0", "lineas.0", "retencion", "num_serie", "emisor"} <= malos and not b["puede_emitir"]
    r = c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "c-1", "confirmo": True})
    assert r.status_code == 409
    # el borrador sigue pudiendo corregirse por la API
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "desglose", "valor": b0["datos"]["desglose"], "motivo": "x"},
                                                            {"campo": "num_serie", "valor": b0["datos"]["num_serie"], "motivo": "x"}]}))
    assert "DATO_NO_VALIDO" in _codigos(b["incidencias"])


# --- Reparación r1 (flujo): variantes restantes ------------------------------------------------------------------------
def test_total_leido_con_importe_mayor_debajo_exige_confirmacion(ctx):
    """INT-2: «TOTAL» reconocido (subtotal) y, debajo, una tasa y el importe final con un rótulo no reconocido."""
    _, _, con, emp = ctx
    for rot_tot, rot_tasa, rot_fin in (("TOTAL", "Tasa de residuos", "IMPORTE"), ("TOTAL FACTURA", "Ecotasa", "LÍQUIDO")):
        ext, d = _extraer(_factura(totales=[("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00"), (rot_tot, "1.210,00"),
                                            (rot_tasa, "30,00"), (rot_fin, "1.240,00")]))
        assert d["importe_total"] == "1210.00" and "1240.00" in ext["total_factura"]["bajo_total"]
        inc = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "TOTAL_CIFRAS_DEBAJO"]
        assert inc and inc[0]["nivel"] == "confirmar" and "1240.00" in inc[0]["mensaje"]
    # un anticipo o un pendiente de pago menores que el total, o importes ya reconocidos, no lo cuestionan
    for filas in ([("TOTAL FACTURA", "1.210,00"), ("Anticipo a cuenta (50 %)", "605,00")],
                  [("TOTAL FACTURA", "1.210,00"), ("Importe pendiente", "1.210,00")]):
        ext, d = _extraer(_factura(totales=[("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00"), *filas]))
        assert d["importe_total"] == "1210.00" and not ext["total_factura"].get("bajo_total")
        assert "TOTAL_CIFRAS_DEBAJO" not in _codigos(_validar(con, emp, d, ext))


@pytest.mark.parametrize("nota,rotulo,importe,pagar,nivel", [
    ("Retención en concepto de fianza, a devolver a la recepción de la obra.", "Retención 7%", "70,00", "1.140,00", "confirmar"),
    ("Se retiene un 2 % de la certificación en concepto de fianza.", "Retención 2%", "20,00", "1.190,00", "confirmar"),
    ("Importe retenido en concepto de garantía (2 %), a liberar en 12 meses.", "Retención 2%", "20,00", "1.190,00", "confirmar"),
    ("El 2 % retenido se devolverá al finalizar el plazo de garantía de la obra.", "Retención 2%", "20,00", "1.190,00", "confirmar"),
    ("El 5 % retenido se devolverá al finalizar el plazo de garantía de la obra.", "Retención 5%", "50,00", "1.160,00", "bloqueo"),
    # garantía comercial ajena a la retención de IRPF: no es una retención de garantía
    ("Garantía de 2 años en la mano de obra según la normativa vigente.", "Retención I.R.P.F. 15%", "150,00", "1.060,00", None),
    ("Retención a cuenta del IRPF 15 %. Garantía de dos años.", "Retención 15%", "150,00", "1.060,00", None),
])
def test_retencion_garantia_en_nota_de_redaccion_libre(ctx, nota, rotulo, importe, pagar, nivel):
    """INT-4: la garantía o fianza explicada solo en una nota con otra redacción, con un tipo habitual de IRPF."""
    _, _, con, emp = ctx
    ext, d = _extraer(_con_retencion(rotulo, importe, pagar, [nota]))
    assert d["retencion"] and d["retencion"]["importe"]
    inc = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "RETENCION_GARANTIA"]
    assert [i["nivel"] for i in inc] == ([nivel] if nivel else [])


def test_numero_equivalente_con_ceros_de_una_factura_archivada_no_se_reexpide(ctx):
    """INT-3: la copia archivada imprime «166/26» y otro PDF de la misma factura «0166/26» (misma serie y número)."""
    from app.importacion import clave_numero
    assert clave_numero("0166/26") == clave_numero("166/26") == clave_numero(" 166 / 26") == "166/26"
    assert clave_numero("A-0166/2026") == clave_numero("a-166/2026") and clave_numero("166/26") != clave_numero("16/26")
    c, eid, _, _ = ctx
    archivada = _subir(c, eid, "166-26_entregada.pdf", _factura(pie="Copia entregada al cliente"))["documento_id"]
    _clasificar(c, archivada, "emitida_propia")
    copia = _subir(c, eid, "0166-26.pdf", _factura(numero="0166/26"))
    assert any(x["tipo"] == "documento" and x["id"] == archivada for x in copia["duplicados_fiscales"])
    did = copia["documento_id"]
    _clasificar(c, did, "preparatorio")
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert b["datos"]["num_serie"] == "0166/26"
    assert "DUPLICADA_ARCHIVADA" in _codigos(b["incidencias"], "bloqueo") and not b["puede_emitir"]


def test_vista_previa_con_filas_incompletas_o_datos_corruptos(app_cliente):
    """INT-8: «Añadir tipo de IVA» deja una fila vacía; datos guardados por una versión anterior con tipos inesperados."""
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b0 = borrador_listo(c, eid)
    for cambio in ({"campo": "desglose", "valor": b0["datos"]["desglose"] + [{"tipo_iva": None, "base": None, "cuota": None}]},
                   {"campo": "desglose.1.tipo_iva", "valor": "10"}, {"campo": "desglose", "valor": [{}]},
                   {"campo": "fecha_operacion", "valor": "09/11/2026"}):
        c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [dict(cambio, motivo="x")]}))
        assert c.get(f"/api/borradores/{bid}/vista-previa.pdf").status_code == 200
        assert c.get(f"/api/borradores/{bid}/vista-previa/1.png").status_code == 200
    datos = dict(b0["datos"], lineas="abc", retencion=5, destinatario="x", referencias=[1])
    con = db.conectar()
    with db.transaccion(con):
        con.execute("UPDATE borradores SET datos=? WHERE id=?", (json.dumps(datos), bid))
    con.close()
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert "referencias" in {i["campo"] for i in b["incidencias"] if i["codigo"] == "DATO_NO_VALIDO"}
    assert c.get(f"/api/borradores/{bid}/vista-previa.pdf").status_code == 200


def test_linea_con_descuento_y_sin_cantidad_no_se_bloquea(ctx):
    """Una línea sin columna de cantidad con descuento (columna «DTO.» o «dto. 10 %» en el concepto): precio ≠ importe."""
    _, _, con, emp = ctx
    ext, d = _extraer(_factura())
    d["indicios"] = sorted(set(d["indicios"]) | {"descuento"})
    d["lineas"] = [{"concepto": "Licencia anual de software", "cantidad": None, "unidad": None, "precio": "1000.00", "importe": "900.00"}]
    ext["lineas"] = [{"precio": {"valor": "1000.00"}, "importe": {"valor": "900.00"}, "dto": {"valor": "10"}}]
    d["base_imponible"] = d["desglose"][0]["base"] = "900.00"
    assert "LINEA_SIN_CANTIDAD" not in _codigos(_validar(con, emp, d, ext))
    ext["lineas"] = []
    d["lineas"][0]["concepto"] = "Licencia anual de software (dto. 10 %)"
    assert "LINEA_SIN_CANTIDAD" not in _codigos(_validar(con, emp, d, ext))
    # descuento mencionado que no explica la diferencia: se pide revisar la línea (no se bloquea)
    d["lineas"][0]["concepto"] = "Licencia anual de software"
    assert [i["nivel"] for i in _validar(con, emp, d, ext) if i["codigo"] == "LINEA_SIN_CANTIDAD"] == ["confirmar"]
    # sin descuento en el documento sigue bloqueado (p. ej. la cantidad leída como precio en una columna deducida)
    d["indicios"] = []
    assert [i["nivel"] for i in _validar(con, emp, d, ext) if i["codigo"] == "LINEA_SIN_CANTIDAD"] == ["bloqueo"]


# --- Revisión de diferencias: duplicados, clasificación, total neto de retención y lecturas dudosas -------------------
def test_factura_recibida_con_el_mismo_numero_no_bloquea_como_archivada(ctx):
    """Un documento clasificado como factura recibida en el que se leyó el NIF de la empresa como emisor (p. ej. un error
    de lectura del proveedor) no figura como factura expedida: se pide confirmar que no es la misma, sin bloquear."""
    c, eid, _, _ = ctx
    recibida = _subir(c, eid, "proveedor_166-26.pdf", _factura(pie="Factura de proveedor"))["documento_id"]
    _clasificar(c, recibida, "recibida")
    did = _subir(c, eid, "166-26.pdf", _factura())["documento_id"]
    _clasificar(c, did, "preparatorio")
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    inc = c.ok(c.get(f"/api/borradores/{bid}"))["incidencias"]
    assert "DUPLICADA_ARCHIVADA" not in _codigos(inc)
    dup = [i for i in inc if i["codigo"] == "DUPLICADO_DOCUMENTO"]
    assert [i["nivel"] for i in dup] == ["confirmar"] and "clasificado como factura recibida" in dup[0]["mensaje"]


def test_confirmacion_de_duplicado_no_cubre_un_documento_importado_despues(ctx):
    """La confirmación «no es la misma factura» vale para los documentos que se mostraron: otro PDF con el mismo emisor y
    número importado después la pide de nuevo, con su nombre."""
    c, eid, _, _ = ctx
    otro = _subir(c, eid, "otro_166-26.pdf", _factura(pie="Otro documento"))["documento_id"]
    _clasificar(c, otro, "desconocido")
    did = _subir(c, eid, "166-26.pdf", _factura())["documento_id"]
    _clasificar(c, did, "preparatorio")
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    inc = c.ok(c.get(f"/api/borradores/{bid}"))["incidencias"]
    claves = [i["clave"] for i in inc if i["nivel"] == "confirmar"]
    assert any(k.startswith("DUPLICADO_DOCUMENTO:num_serie:") for k in claves)
    b = c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    assert all(i["confirmado"] for i in b["incidencias"] if i["codigo"] == "DUPLICADO_DOCUMENTO")
    _subir(c, eid, "tercero_166-26.pdf", _factura(pie="Tercer documento"))
    dup = [i for i in c.ok(c.get(f"/api/borradores/{bid}"))["incidencias"] if i["codigo"] == "DUPLICADO_DOCUMENTO"]
    assert len(dup) == 1 and not dup[0]["confirmado"] and "tercero_166-26.pdf" in dup[0]["mensaje"]


def _ext_propia(papel_dudoso=False, tipo="factura", conf_tipo=0.97):
    return {"emisor": {"nif": {"valor": NIF_EMP, "confianza": 0.97, **({"papel_dudoso": True} if papel_dudoso else {})}},
            "destinatario": {"nif": {"valor": "A87754214", "confianza": 0.97}},
            "tipo_documento": {"valor": tipo, "confianza": conf_tipo}, "indicios": {}}


def test_clasificacion_por_duplicado_solo_con_clasificacion_confirmada_y_papel_claro():
    emp = {"nif": NIF_EMP}
    archivado = {"tipo": "documento", "id": 7, "nombre": "166-26_entregada.pdf", "clasificacion": "emitida_propia"}
    # archivado y confirmado como expedido: es otra copia
    assert borradores.clasificar(_ext_propia(), {}, emp, [dict(archivado, confirmado=True)])["sugerida"] == "emitida_propia"
    # mera propuesta sin confirmar: no decide la clasificación, solo se menciona
    r = borradores.clasificar(_ext_propia(), {}, emp, [dict(archivado, confirmado=False)])
    assert r["sugerida"] == "pendiente_confirmar" and any("sin confirmar" in m for m in r["motivos"])
    # papel de emisor dudoso: la coincidencia de número no prueba que sea propia (puede ser una factura recibida)
    r = borradores.clasificar(_ext_propia(papel_dudoso=True), {}, emp, [dict(archivado, confirmado=True)])
    assert r["sugerida"] == "desconocido"
    r = borradores.clasificar(_ext_propia(papel_dudoso=True), {}, emp, [{"tipo": "factura", "num_serie": "166/26"}])
    assert r["sugerida"] == "desconocido"


def test_titulo_de_proforma_dudoso_rebaja_la_propuesta():
    emp = {"nif": NIF_EMP}
    assert borradores.clasificar(_ext_propia(tipo="proforma"), {}, emp)["confianza"] == 0.9
    r = borradores.clasificar(_ext_propia(tipo="proforma", conf_tipo=0.4), {}, emp)
    assert r["sugerida"] == "preparatorio" and r["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("lectura dudosa" in m for m in r["motivos"])


def test_total_impreso_neto_de_retencion_se_explica_en_la_confirmacion(ctx):
    """«TOTAL» = base + cuota − retención: el total de la factura se calcula y el impreso pasa a importe a pagar; la
    confirmación de la retención lo dice expresamente (el total calculado queda justo en el umbral de confianza)."""
    _, _, con, emp = ctx
    ext, d = _extraer(_factura(totales=[("BASE IMPONIBLE", "1.000,00"), ("I.V.A. 21%", "210,00"),
                                        ("Retención IRPF 15%", "-150,00"), ("TOTAL", "1.060,00")]))
    assert (d["importe_total"], d["total_a_pagar"]) == ("1210.00", "1060.00")
    ret = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "RETENCION"]
    assert [i["nivel"] for i in ret] == ["confirmar"]
    assert "imprime como total 1060.00" in ret[0]["mensaje"] and "base + cuota (1210.00)" in ret[0]["mensaje"]
    ret = [i for i in _validar(con, emp, d, ext, cambiados={"importe_total"}) if i["codigo"] == "RETENCION"]
    assert "imprime como total" not in ret[0]["mensaje"]
    # total impreso normal (no neto): sin esa explicación
    ext, d = _extraer(_con_retencion("Retención IRPF 15%", "150,00", "1.060,00"))
    ret = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "RETENCION"]
    assert ret and "imprime como total" not in ret[0]["mensaje"]


def test_tipo_de_iva_dudoso_se_confirma_una_sola_vez(ctx):
    _, _, con, emp = ctx
    ext, d = _extraer(_factura())
    dudoso = {"valor": "21.00", "texto": "21", "metodo": "calculado", "confianza": 0.7, "avisos": ["Tipo deducido"]}
    ext["tipo_iva"] = dict(dudoso)
    inc = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "DUDOSO" and "tipo_iva" in i["campo"]]
    assert [i["campo"] for i in inc] == ["desglose.0.tipo_iva"]
    ext["desglose"][0]["tipo_iva"] = dict(dudoso)   # la misma lectura en el resumen y en el desglose: una confirmación
    inc = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "DUDOSO" and "tipo_iva" in i["campo"]]
    assert [i["campo"] for i in inc] == ["desglose.0.tipo_iva"]
    assert not [i for i in _validar(con, emp, d, ext, cambiados={"desglose.0.tipo_iva"}) if i["codigo"] == "DUDOSO"
                and "tipo_iva" in i["campo"]]


# --- Reparación r2 (flujo): descuentos de línea, total en otro sitio, patrones sin separador, notas de garantía ---------
def test_linea_sin_cantidad_con_descuento_en_otras_redacciones(ctx):
    """Regresión: «dcto. 10 %», «(-10 %)», «10 % dto.» o «bonif. 10 %» en el concepto, o una columna «DCTO.»/«BONIF.»,
    explican que el importe sea menor que el precio sin columna de cantidad: no se bloquea."""
    _, _, con, emp = ctx
    ext, d = _extraer(_factura())
    d["indicios"] = []
    ext["lineas"] = []
    d["base_imponible"] = d["desglose"][0]["base"] = "900.00"

    def niveles(concepto):
        d["lineas"] = [{"concepto": concepto, "cantidad": None, "unidad": None, "precio": "1000.00", "importe": "900.00"}]
        return [i["nivel"] for i in _validar(con, emp, d, ext) if i["codigo"] == "LINEA_SIN_CANTIDAD"]
    for concepto in ("Licencia anual de software, dcto. 10 %", "Licencia anual de software (-10 %)",
                     "Licencia anual de software 10 % dto.", "Licencia anual de software, bonif. 10%"):
        assert niveles(concepto) == [], concepto
    # un descuento rotulado que no explica la diferencia: se pide revisar la línea; un porcentaje que no la explica, bloqueo
    assert niveles("Licencia anual de software, dcto. especial") == ["confirmar"]
    assert niveles("Licencia anual de software (-5 %)") == ["bloqueo"]
    assert niveles("Licencia anual de software, IVA 21 %") == ["bloqueo"]
    # columna de descuento con otros rótulos habituales: se lee el porcentaje de la línea
    for rotulo in ("DCTO.", "BONIF."):
        t = [(18, 20, "INGENIERÍA FICTICIA Y ASOCIADOS S.L.U.", 11, "l", True), (18, 36, f"C.I.F.: {NIF_EMP}", 9, "l", False),
             (192, 28, "Nº: 166/26", 10, "r", False), (110, 71, "C.I.F.: A87754214", 9, "l", False),
             (20, 100, "CONCEPTO", 9, "l", True), (140, 100, "PRECIO", 9, "r", True), (165, 100, rotulo, 9, "r", True),
             (190, 100, "IMPORTE", 9, "r", True), (20, 109, "Licencia anual de software", 9, "l", False),
             (140, 109, "1.000,00", 9, "r", False), (165, 109, "10%", 9, "r", False), (190, 109, "900,00", 9, "r", False),
             (160, 127, "BASE IMPONIBLE", 9, "r", False), (190, 127, "900,00 €", 9, "r", False),
             (160, 133, "I.V.A. 21%", 9, "r", False), (190, 133, "189,00 €", 9, "r", False),
             (160, 139, "TOTAL FACTURA", 9, "r", True), (190, 139, "1.089,00 €", 9, "r", False)]
        ext2, d2 = _extraer(_pdf(t))
        assert [(l["precio"], l["importe"]) for l in d2["lineas"]] == [("1000.00", "900.00")]
        assert ext2["lineas"][0]["dto"]["valor"] == "10"
        assert "LINEA_SIN_CANTIDAD" not in _codigos(_validar(con, emp, d2, ext2)), rotulo


def _totales_con_tasa(disposicion):
    """Factura de 1.000 + IVA 210 = 1.210 con una tasa de 30 y un importe final de 1.240 con un rótulo no reconocido,
    impresos fuera de la columna bajo el total."""
    t = [(18, 20, "INGENIERÍA FICTICIA Y ASOCIADOS S.L.U.", 11, "l", True), (18, 26, "C/ Inventada de Pruebas, 99", 9, "l", False),
         (18, 31, "28000 Madrid", 9, "l", False), (18, 36, f"C.I.F.: {NIF_EMP}", 9, "l", False),
         (192, 20, "FACTURA", 18, "r", True), (192, 28, "Nº: 166/26", 10, "r", False),
         (110, 56, "PROMOCIONES CLIENTE S.A.", 9, "l", True), (110, 71, "C.I.F.: A87754214", 9, "l", False),
         (20, 100, "CONCEPTO", 9, "l", True), (128, 100, "CANTIDAD", 9, "r", True), (165, 100, "PRECIO", 9, "r", True),
         (190, 100, "IMPORTE", 9, "r", True), (20, 109, "Servicio de mantenimiento", 9, "l", False),
         (128, 109, "1,00", 9, "r", False), (165, 109, "1.000,00", 9, "r", False), (190, 109, "1.000,00", 9, "r", False)]
    columna = [(160, 130, "BASE IMPONIBLE", 9, "r", False), (190, 130, "1.000,00 €", 9, "r", False),
               (160, 136, "I.V.A. 21%", 9, "r", False), (190, 136, "210,00 €", 9, "r", False),
               (160, 142, "TOTAL", 9, "r", True), (190, 142, "1.210,00 €", 9, "r", False),
               (160, 148, "Tasa de residuos", 9, "r", False), (190, 148, "30,00 €", 9, "r", False)]
    if disposicion == "horizontal":
        t += [(30, 135, "BASE IMPONIBLE", 8, "l", True), (70, 135, "IVA 21%", 8, "l", True), (100, 135, "TOTAL", 8, "l", True),
              (125, 135, "TASA RESIDUOS", 8, "l", True), (165, 135, "IMPORTE", 8, "l", True),
              (30, 141, "1.000,00 €", 9, "l", False), (70, 141, "210,00 €", 9, "l", False), (100, 141, "1.210,00 €", 9, "l", False),
              (125, 141, "30,00 €", 9, "l", False), (165, 141, "1.240,00 €", 9, "l", True)]
    elif disposicion == "izquierda":
        t += columna + [(20, 154, "IMPORTE A ABONAR", 10, "l", True), (95, 154, "1.240,00 €", 10, "r", True)]
    elif disposicion == "hueco":
        t += columna + [(160, 166, "IMPORTE", 11, "r", True), (190, 166, "1.240,00 €", 11, "r", True)]
    elif disposicion == "cabecera":
        t += columna + [(18, 80, "IMPORTE: 1.240,00 €", 12, "l", True)]
    elif disposicion == "control":   # una cifra mayor que el total que no es el total más otra cifra
        t += columna[:6] + [(30, 280, "Capital social 3.006,00 €", 7, "l", False)]
    return _pdf(t)


@pytest.mark.parametrize("disposicion", ["horizontal", "izquierda", "hueco", "cabecera"])
def test_total_leido_mas_otra_cifra_impresa_en_otro_sitio_exige_confirmacion(ctx, disposicion):
    """INT-2: el importe final (total leído + tasa) impreso en un cuadro horizontal, en la columna izquierda, tras un hueco
    o en la cabecera: el total leído puede ser un subtotal y se pide confirmarlo."""
    _, _, con, emp = ctx
    ext, d = _extraer(_totales_con_tasa(disposicion))
    assert d["importe_total"] == "1210.00"
    inc = [i for i in _validar(con, emp, d, ext) if i["codigo"] in ("TOTAL_OTRA_CIFRA", "TOTAL_CIFRAS_DEBAJO")]
    assert [i["nivel"] for i in inc] == ["confirmar"] and "1240.00" in inc[0]["mensaje"]
    assert not [i for i in _validar(con, emp, d, ext, cambiados={"importe_total"}) if i["codigo"] == "TOTAL_OTRA_CIFRA"]


def test_total_leido_sin_otra_cifra_que_lo_sume_no_pide_confirmacion(ctx):
    _, _, con, emp = ctx
    for datos in (_totales_con_tasa("control"), _factura()):
        ext, d = _extraer(datos)
        assert not ext["total_factura"].get("suma_otra")
        assert not _codigos(_validar(con, emp, d, ext)) & {"TOTAL_OTRA_CIFRA", "TOTAL_CIFRAS_DEBAJO"}


@pytest.mark.parametrize("patron,archivado,nuevo", [
    ("{aaaa}{n}", "20260166", "2026166"), ("{aaaa}{n}", "2026166", "20260166"), ("F{aa}{n}", "F260166", "F26166")])
def test_numero_con_relleno_pegado_al_anio_de_una_factura_archivada_no_se_reexpide(app_cliente, patron, archivado, nuevo):
    """INT-3: con un patrón sin separador entre el año y el número, los ceros de relleno quedan dentro del mismo grupo de
    cifras; la serie lee los dos números como el mismo (166 de 2026) y la copia archivada bloquea la nueva."""
    from app import series
    c = app_cliente
    eid = preparar_empresa(c, patron=patron)
    a, n = series.probar(patron, archivado), series.probar(patron, nuevo)
    assert (a["numero"], a["anio"]) == (n["numero"], n["anio"]) == (166, 2026)
    archivada = _subir(c, eid, "entregada.pdf", _factura(numero=archivado, pie="Copia entregada al cliente"))["documento_id"]
    _clasificar(c, archivada, "emitida_propia")
    copia = _subir(c, eid, "otra_copia.pdf", _factura(numero=nuevo))
    assert any(x["tipo"] == "documento" and x["id"] == archivada for x in copia["duplicados_fiscales"])
    _clasificar(c, copia["documento_id"], "preparatorio")
    bid = c.ok(c.post(f"/api/documentos/{copia['documento_id']}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert b["datos"]["num_serie"] == nuevo
    assert "DUPLICADA_ARCHIVADA" in _codigos(b["incidencias"], "bloqueo") and not b["puede_emitir"]


@pytest.mark.parametrize("notas,rotulo,importe,pagar,nivel", [
    (["Se retiene el 2 % de cada certificación en concepto de", "fianza, a devolver a la recepción definitiva de la obra."],
     "Retención 2%", "20,00", "1.190,00", "confirmar"),
    (["Se retiene el 2 % del importe de la presente certificación de obra hasta la recepción definitiva, en concepto de "
      "garantía."], "Retención 2%", "20,00", "1.190,00", "confirmar"),
    (["Retención del 2 % s/ B.I. en concepto de garantía de obra."], "Retención 2%", "20,00", "1.190,00", "confirmar"),
    (["Garantía de obra (cláusula 12 del contrato): se retiene el 2 % hasta la recepción."], "Retención 2%", "20,00",
     "1.190,00", "confirmar"),
    (["Retención del 2 % según art. 14 del contrato, en concepto de garantía."], "Retención 2%", "20,00", "1.190,00",
     "confirmar"),
    (["Nota: el importe retenido (7 %) queda como", "garantía de buena ejecución hasta la recepción."], "Retención 7%",
     "70,00", "1.140,00", "confirmar"),
    (["Se retiene el 5 % de la certificación en concepto de", "fianza hasta la recepción de la obra."], "Retención 5%",
     "50,00", "1.160,00", "bloqueo"),
    # IRPF con una garantía comercial en otra frase u otro renglón: no es una retención de garantía
    (["Retención practicada a cuenta del IRPF (15 %).", "Garantía de dos años en la mano de obra."], "Retención 15%",
     "150,00", "1.060,00", None),
    (["Se aplica la retención del 15 % a cuenta del impuesto sobre la renta.", "La garantía de los equipos es de dos años."],
     "Retención 15%", "150,00", "1.060,00", None),
])
def test_retencion_garantia_en_nota_partida_o_con_incisos(ctx, notas, rotulo, importe, pagar, nivel):
    """INT-4: la nota que liga la retención a una garantía o fianza ocupa dos renglones o tiene incisos («s/ B.I.»,
    «art. 14», una frase larga): se pide confirmar (tipo habitual de IRPF) o se bloquea (otro tipo)."""
    _, _, con, emp = ctx
    ext, d = _extraer(_con_retencion(rotulo, importe, pagar, notas))
    assert d["retencion"] and d["retencion"]["importe"]
    inc = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "RETENCION_GARANTIA"]
    assert [i["nivel"] for i in inc] == ([nivel] if nivel else [])


def test_textos_largos_y_sustitutos_sueltos_no_rompen_el_borrador(app_cliente):
    """INT-8: un concepto o un nombre muy largos (admitidos por la edición) se maquetan en varias páginas en vez de fallar;
    un sustituto UTF-16 suelto se rechaza con 422 sin guardar nada."""
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b0 = borrador_listo(c, eid, confirmar_todo=False)
    largo = ("Suministro e instalación de equipos de climatización en la planta baja del edificio " * 80)[:4990]
    for campo in ("lineas.0.concepto", "emisor.nombre", "destinatario.domicilio", "lineas.0.unidad"):
        valor = largo if campo != "lineas.0.unidad" else "u " * 2490
        c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": campo, "valor": valor, "motivo": "x"}]}))
        assert c.get(f"/api/borradores/{bid}/vista-previa.pdf").status_code == 200, campo
    assert c.get(f"/api/borradores/{bid}/vista-previa/1.png").status_code == 200
    for campo in ("lineas.0.concepto", "observaciones"):
        cuerpo = json.dumps({"cambios": [{"campo": campo, "valor": "Servicio \ud800 técnico", "motivo": "x"}]}, ensure_ascii=True)
        r = c.tc.patch(f"/api/borradores/{bid}", content=cuerpo.encode(),
                       headers={"X-CSRF-Token": c.csrf, "content-type": "application/json"})
        assert r.status_code == 422, campo
    cuerpo = json.dumps({"cambios": [{"campo": "observaciones", "valor": "Nota", "motivo": "x\udfff"}]}, ensure_ascii=True)
    r = c.tc.patch(f"/api/borradores/{bid}", content=cuerpo.encode(), headers={"X-CSRF-Token": c.csrf, "content-type": "application/json"})
    assert r.status_code == 422
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert b["datos"]["lineas"][0]["concepto"] == largo and not b["datos"].get("observaciones")
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    r = c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "largo-1", "confirmo": True})
    assert r.status_code == 200, r.text[:300]
