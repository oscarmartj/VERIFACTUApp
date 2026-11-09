"""Regresiones de los hallazgos de la revisión independiente del núcleo fiscal (todas SIMULADAS)."""
import json

import pytest
from conftest import borrador_listo, preparar_empresa
from test_03_flujo import emitir, enviar, preparar_desde_bytes, subir_bytes, variante_pdf

from app import auditoria, copias, db, remision, simulador


def _reg(con, fid, tipo="alta"):
    return db.fila(con, "SELECT * FROM registros WHERE factura_id=? AND tipo=? ORDER BY id DESC LIMIT 1", (fid, tipo))


def test_h1_restauracion_con_datos_posteriores_a_la_copia(app_cliente):
    """Documento importado, factura expedida y anulación DESPUÉS de la copia: se recupera todo sin romper la cadena."""
    c = app_cliente
    eid = preparar_empresa(c)
    b1 = preparar_desde_bytes(c, eid, "f166.pdf", variante_pdf("166/26"))
    con = db.conectar()
    c.ok(emitir(c, b1, "h1-1"))
    enviar(eid)
    copia = copias.crear_copia(con)
    # Todo esto es posterior a la copia: documento nuevo, usuario nuevo, factura y anulación
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "nuevo@ejemplo.test", "nombre": "N", "clave": "clave-nueva-123", "rol": "admin"}))
    b2 = preparar_desde_bytes(c, eid, "f167.pdf", variante_pdf("167/26"))
    f2 = c.ok(emitir(c, b2, "h1-2"))["factura_id"]
    enviar(eid)
    c.ok(c.post(f"/api/facturas/{f2}/anular", {"motivo": "Expedida por error", "confirmo": True}))
    enviar(eid)
    con.close()
    r = copias.restaurar(copia)
    assert r["facturas_recuperadas"] == 1 and r["registros_recuperados"] == 2, r
    assert r["filas_contexto_recuperadas"] >= 2  # documento, extracción y borrador posteriores a la copia
    assert r["base_anterior_apartada"]
    con = db.conectar()
    assert db.fila(con, "SELECT estado FROM facturas WHERE id=?", (f2,))["estado"] == "anulada"
    assert db.fila(con, "SELECT COUNT(*) n FROM documentos")["n"] == 2
    estados = [x["estado_envio"] for x in db.filas(con, "SELECT estado_envio FROM registros ORDER BY secuencia")]
    assert estados == ["aceptado", "aceptado", "aceptado"]
    assert remision.procesar(con, eid, "simulador", forzar_flujo=True)["accion"] == "nada"
    # La numeración continúa en 168 y la cadena enlaza con la anulación
    b3 = preparar_desde_bytes(c, eid, "f168.pdf", variante_pdf("168/26"))
    c.ok(emitir(c, b3, "h1-3"))
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]
    assert r["pdf_irrecuperables"] == [], r


def test_h2_duplicado_tras_caida_durante_envio(app_cliente, monkeypatch):
    """El servicio procesa el registro y el proceso falla antes de recibir la respuesta (excepción no prevista)."""
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    original = simulador.procesar

    def procesa_y_falla(sobre):
        original(sobre)
        raise RuntimeError("caída del proceso tras enviar")
    monkeypatch.setattr(simulador, "procesar", procesa_y_falla)
    r = enviar(eid)
    assert r["resultado"] == "error" and r["estado"] == "sin_confirmar"
    con = db.conectar()
    reg = _reg(con, fid)
    assert reg["estado_envio"] == "sin_confirmar" and reg["posible_entrega"] == 1   # nunca queda «enviando»
    monkeypatch.setattr(simulador, "procesar", original)
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    reg = _reg(con, fid)
    assert reg["estado_envio"] == "aceptado" and "duplicado" in reg["descripcion_error"]


def test_h2_duplicado_sin_envio_previo_requiere_revision(app_cliente):
    """Un 3000 sin intento previo de posible entrega NO se da por bueno."""
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    con = db.conectar()
    # Otro sistema ya registró en la AEAT (simulada) una factura con la misma identificación
    reg = _reg(con, fid)
    simulador._db().execute("INSERT INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (reg["id_emisor"], reg["num_serie"], reg["fecha_expedicion"], "Correcta", "OTRO", None, None, "X", "alta", ""))
    enviar(eid)
    reg = _reg(con, fid)
    assert reg["estado_envio"] == "rechazado" and reg["codigo_error"] == "3000" and "requiere revisión" in reg["descripcion_error"]


def test_h3_h4_orden_y_control_de_flujo_tras_fallo(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    b1 = preparar_desde_bytes(c, eid, "f166.pdf", variante_pdf("166/26"))
    con = db.conectar()
    f1 = c.ok(emitir(c, b1, "o1"))["factura_id"]
    simulador.fijar_modo("sin_respuesta", 1)
    enviar(eid)
    b2 = preparar_desde_bytes(c, eid, "f167.pdf", variante_pdf("167/26"))
    f2 = c.ok(emitir(c, b2, "o2"))["factura_id"]
    # 166/26 está en espera de reintento: 167/26 no puede adelantarse
    r = enviar(eid)
    assert r["accion"] == "esperando_reintento"
    assert _reg(con, f2)["estado_envio"] == "pendiente"
    # Un envío sin respuesta también fija el próximo envío (control de flujo)
    assert db.fila(con, "SELECT proximo_envio_en FROM control_flujo")["proximo_envio_en"]
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    assert _reg(con, f1)["estado_envio"] == "aceptado" and _reg(con, f2)["estado_envio"] == "aceptado"


def test_h5_fault_de_servidor_se_reintenta(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    simulador.fijar_modo("fault_servidor", 1)
    r = enviar(eid)
    assert r["resultado"] == "fault_servidor"
    con = db.conectar()
    reg = _reg(con, fid)
    # Un fault de servidor se trata como «sin respuesta»: pudo registrarse
    assert reg["estado_envio"] == "sin_confirmar" and reg["posible_entrega"] == 1 and reg["incidencia"] == 1 and reg["siguiente_intento_en"]
    # Anular mientras el alta está pendiente o bloqueada no se permite
    assert c.post(f"/api/facturas/{fid}/anular", {"motivo": "x", "confirmo": True}).status_code == 409
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    assert _reg(con, fid)["estado_envio"] == "aceptado"


def test_h6_h7_indicadores_de_subsanacion_y_anulacion(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    con = db.conectar()
    simulador.fijar_modo("rechazo_registro", 1)
    enviar(eid)
    alta = _reg(con, fid)
    assert alta["estado_envio"] == "rechazado"
    # Alta rechazada (no existe en la AEAT): Subsanacion=S, RechazoPrevio=X
    r1 = c.ok(c.post(f"/api/registros/{alta['id']}/subsanar", {"motivo": "corrección", "confirmo": True}))["registro_id"]
    assert db.fila(con, "SELECT rechazo_previo FROM registros WHERE id=?", (r1,))["rechazo_previo"] == "X"
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r1,))["estado_envio"] == "aceptado"
    # Subsanación de un registro aceptado: RechazoPrevio ausente; si esa subsanación se rechaza -> RechazoPrevio=S
    r2 = c.ok(c.post(f"/api/registros/{r1}/subsanar", {"motivo": "otra", "confirmo": True}))["registro_id"]
    assert db.fila(con, "SELECT rechazo_previo FROM registros WHERE id=?", (r2,))["rechazo_previo"] is None
    simulador.fijar_modo("rechazo_registro", 1)
    enviar(eid)
    r3 = c.ok(c.post(f"/api/registros/{r2}/subsanar", {"motivo": "tras rechazo", "confirmo": True}))["registro_id"]
    assert db.fila(con, "SELECT rechazo_previo FROM registros WHERE id=?", (r3,))["rechazo_previo"] == "S"
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r3,))["estado_envio"] == "aceptado"
    # Anulación con alta vigente (aunque la última subsanación se rechazara antes): sin SinRegistroPrevio
    an = c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "error", "confirmo": True}))["registro_id"]
    assert db.fila(con, "SELECT sin_registro_previo FROM registros WHERE id=?", (an,))["sin_registro_previo"] is None
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (an,))["estado_envio"] == "aceptado"
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]


def test_h7_anulacion_sin_registro_previo(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    simulador.fijar_modo("rechazo_registro", 1)
    enviar(eid)
    con = db.conectar()
    an = c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "no debió expedirse", "confirmo": True}))["registro_id"]
    assert db.fila(con, "SELECT sin_registro_previo FROM registros WHERE id=?", (an,))["sin_registro_previo"] == "S"
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (an,))["estado_envio"] == "aceptado"


def test_h8_fecha_operacion_futura_bloquea(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_operacion", "valor": "2099-01-01", "motivo": "x"}]}))
    assert ("bloqueo", "FECHA_OP_FUTURA") in {(i["nivel"], i["codigo"]) for i in b["incidencias"]}


def test_h9_rectificativa_no_pisa_anulacion(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    s = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "R", "patron": "R{n}/{aa}", "tipo": "rectificativa", "siguiente_numero": 1}))
    c.ok(c.post(f"/api/series/{s['id']}/confirmar"))
    rb = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R1", "motivo": "x"}))["borrador_id"]
    c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "error", "confirmo": True}))
    anio = db.ahora().strftime("%y")
    cambios = [{"campo": k, "valor": v, "motivo": "m"} for k, v in (("num_serie", f"R1/{anio}"), ("desglose.0.base", "-10.00"),
               ("desglose.0.cuota", "-2.10"), ("base_imponible", "-10.00"), ("cuota_total", "-2.10"), ("importe_total", "-12.10"),
               ("total_a_pagar", "-12.10"))]
    c.ok(c.patch(f"/api/borradores/{rb}", {"cambios": cambios}))
    assert emitir(c, rb, "rect-x").status_code == 409
    con = db.conectar()
    assert db.fila(con, "SELECT estado FROM facturas WHERE id=?", (fid,))["estado"] == "anulada"
    assert db.fila(con, "SELECT COUNT(*) n FROM facturas")["n"] == 1


def test_h10_reintentar_no_pisa_estado_definitivo(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    con = db.conectar()
    rid = _reg(con, fid)["id"]
    assert c.post(f"/api/registros/{rid}/reintentar").status_code == 409
    assert _reg(con, fid)["estado_envio"] == "aceptado"


def test_h10_serie_anual_empieza_en_1_en_otro_anio(entorno_tmp):
    from app import series
    con = db.conectar()
    db.migrar(con)
    con.execute("INSERT INTO empresas (id, nif, razon_social, creado_en) VALUES (1,'B82991316','X','2026-01-01')")
    con.execute("""INSERT INTO series (id, empresa_id, entorno, codigo, patron, siguiente_numero, confirmada_en)
                   VALUES (1,1,'simulador','A','{n}/{aa}',166,'2026-11-09T10:00:00+01:00')""")
    assert series.siguiente(con, 1, 2026) == 166
    assert series.siguiente(con, 1, 2027) == 1
    con.execute("INSERT INTO series (id, empresa_id, entorno, codigo, patron, siguiente_numero, confirmada_en) VALUES (2,1,'simulador','B','PRB-{n}',7,'2026-11-09')")
    assert series.siguiente(con, 2, 0) == 7
