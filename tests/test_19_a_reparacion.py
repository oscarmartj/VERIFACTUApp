"""Carril A, ronda de reparación (hallazgos del verificador independiente). Solo datos sintéticos.

- A-V1: la numeración propuesta desde el borrador no puede solapar con otra serie de la empresa (leería sus números y su
  contador propondría números ya expedidos), y el arreglo de un clic nunca propone un número ya expedido.
- A-V2: la procedencia de un cambio del historial la comprueba el servidor; si no se cumple, queda como «manual».
- A-V3: la respuesta a la pregunta de la fecha vale para la fecha del documento a la que respondía."""
from datetime import timedelta

from conftest import EMPRESA_REPLICA, borrador_listo, preparar_empresa

from app import db, series


def emitir(c, bid, clave="r-1", entorno="simulador"):
    return c.post(f"/api/borradores/{bid}/emitir", {"entorno": entorno, "clave_idempotencia": clave, "confirmo": True})


def _confirmar(c, bid, b):
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    return c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))


def _num(b):
    return {i["codigo"]: i for i in b["incidencias"] if i["campo"] == "num_serie"}


# --- A-V1 -----------------------------------------------------------------------------------------------------------------
def test_numeracion_que_solapa_con_otra_serie_no_se_propone_ni_se_crea_desde_el_borrador(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c, patron="F-{aaaa}/{n:4}", siguiente=240)
    _, bid, _ = borrador_listo(c, eid, confirmar_todo=False)
    anio = db.ahora().year
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "num_serie", "valor": f"F-{anio}/240"}]}))
    # «F-{aaaa}/{n}» leería los números de la serie A («F-2026/0240» es el 240 de las dos): no se propone
    assert b["serie_propuesta"] is None
    inc = _num(b)
    assert inc["SERIE_FORMATO"]["arreglo"]["valor"] == f"F-{anio}/0240"
    r = c.post(f"/api/borradores/{bid}/serie", {"entorno": "simulador", "patron": "F-{aaaa}/{n}", "siguiente_numero": 240})
    assert r.status_code == 409 and "«A»" in r.json()["detail"]
    s = c.ok(c.get(f"/api/empresas/{eid}/series"))
    assert [(x["codigo"], x["patron"]) for x in s] == [("A", "F-{aaaa}/{n:4}")]
    # una numeración que no se confunde con ninguna sí se propone
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "num_serie", "valor": f"P{anio % 100:02d}-7"}]}))
    assert b["serie_propuesta"] and b["serie_propuesta"]["hay_otras"]
    assert series.solapan("F-{aaaa}/{n}", "F-{aaaa}/{n:4}") and not series.solapan("R-{aaaa}/{n:4}", "F-{aaaa}/{n:4}")


def test_numero_ya_expedido_no_se_propone_como_numeracion_ni_como_arreglo(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c, patron="F-{aaaa}/{n:4}", siguiente=240)
    anio = db.ahora().year
    # Situación ya posible desde Configuración: otra serie que escribe los mismos números sin ceros
    sb = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "B", "patron": "F-{aaaa}/{n}",
                                                     "siguiente_numero": 240}))
    c.ok(c.post(f"/api/series/{sb['id']}/confirmar"))
    _, bid, _ = borrador_listo(c, eid, confirmar_todo=False)
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "num_serie", "valor": f"F-{anio}/240"}]}))
    assert b["serie_detectada"]["codigo"] == "B"
    b = _confirmar(c, bid, b)
    c.ok(emitir(c, bid))                                   # F-…/240 expedida en la serie B
    _, bid2, _ = borrador_listo(c, eid, nombre="replica_166-26_escaneada.pdf", confirmar_todo=False)
    b2 = c.ok(c.patch(f"/api/borradores/{bid2}", {"cambios": [{"campo": "num_serie", "valor": f"F-{anio}/0239"}]}))
    inc = _num(b2)
    # la serie A espera F-…/0240, que ya está expedida (como F-…/240): no se ofrece como arreglo de un clic
    assert b2["serie_detectada"]["codigo"] == "A" and "NUM_NO_CORRELATIVO" in inc
    assert "arreglo" not in inc["NUM_NO_CORRELATIVO"]
    b2 = c.ok(c.patch(f"/api/borradores/{bid2}", {"cambios": [{"campo": "num_serie", "valor": f"F-{anio}/0240"}]}))
    inc = _num(b2)
    assert "DUPLICADA" in inc and "arreglo" not in inc["DUPLICADA"]
    # un número expedido nunca se propone como inicio de una numeración nueva
    con = db.conectar()
    con.execute("UPDATE series SET activa=0 WHERE empresa_id=?", (eid,))
    con.commit()
    b2 = c.ok(c.patch(f"/api/borradores/{bid2}", {"cambios": [{"campo": "num_serie", "valor": f"F-{anio}/240"}]}))
    assert "DUPLICADA" in _num(b2) and b2["serie_propuesta"] is None
    r = c.post(f"/api/borradores/{bid2}/serie", {"entorno": "simulador", "patron": "F-{aaaa}/{n}", "siguiente_numero": 240})
    assert r.status_code == 409


def test_interfaz_no_pregunta_la_numeracion_con_el_numero_duplicado_o_de_otra_serie():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    ini = js.index("const preguntaSerie = ")
    linea = js[ini:js.index(";\n", ini)]
    assert "DUPLICADA" in linea and "SERIE_FORMATO" in linea


# --- A-V2 -----------------------------------------------------------------------------------------------------------------
def test_la_procedencia_del_historial_la_comprueba_el_servidor(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b = borrador_listo(c, eid)
    d = b["datos"]
    # afirmaciones falsas: quedan como cambios manuales
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "importe_total", "valor": "999.00", "origen": "calculado"},
        {"campo": "destinatario.nif", "valor": "A87754214", "origen": "certificado"},
        {"campo": "num_serie", "valor": "999/99", "origen": "serie"},
        {"campo": "observaciones", "valor": "x", "origen": "pregunta"},
        {"campo": "destinatario.nombre", "valor": "OTRO CLIENTE FICTICIO", "origen": "ocr"}]}))
    assert [x["origen"] for x in b["cambios"][-5:]] == ["manual"] * 5
    # afirmaciones que el servidor comprueba: se conservan
    total = f"{float(d['base_imponible']) + float(d['cuota_total']):.2f}"
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "num_serie", "valor": d["num_serie"], "origen": "ocr"},
        {"campo": "destinatario.nif", "valor": d["destinatario"]["nif"], "origen": "ocr"},
        {"campo": "destinatario.nombre", "valor": d["destinatario"]["nombre"], "origen": "ocr"},
        {"campo": "importe_total", "valor": total, "origen": "calculado"},
        {"campo": "emisor.nif", "valor": EMPRESA_REPLICA["nif"], "origen": "certificado"},
        {"campo": "fecha_operacion", "valor": d["fecha_documento"], "origen": "pregunta"}]}))
    assert [(x["campo"], x["origen"]) for x in b["cambios"][-6:]] == [
        ("num_serie", "ocr"), ("destinatario.nif", "ocr"), ("destinatario.nombre", "ocr"), ("importe_total", "calculado"),
        ("emisor.nif", "certificado"), ("fecha_operacion", "pregunta")]


# --- A-V3 -----------------------------------------------------------------------------------------------------------------
def test_la_pregunta_de_la_fecha_vuelve_si_cambia_la_fecha_del_documento(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b = borrador_listo(c, eid, confirmar_todo=False)
    hoy = db.ahora().date()
    fd = (hoy - timedelta(days=40)).isoformat()
    otra = (hoy - timedelta(days=70)).isoformat()

    def pregunta(b):
        return "FECHA_EXPEDICION" in {i["codigo"] for i in b["incidencias"]}
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_documento", "valor": fd}]}))
    assert pregunta(b)
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_operacion", "valor": fd, "origen": "pregunta"}]}))
    assert not pregunta(b)
    # la fecha del documento cambia después: la respuesta ya no vale y se pregunta de nuevo
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_documento", "valor": otra}]}))
    assert pregunta(b) and b["datos"]["fecha_operacion"] == fd
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_operacion", "valor": None, "origen": "pregunta"}]}))
    assert not pregunta(b)
    # las dos fechas en un mismo guardado (en cualquier orden): la fecha de operación responde a la nueva fecha del documento
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_operacion", "valor": fd},
                                                          {"campo": "fecha_documento", "valor": fd}]}))
    assert not pregunta(b)
    assert [x["campo"] for x in b["cambios"][-2:]] == ["fecha_documento", "fecha_operacion"]
