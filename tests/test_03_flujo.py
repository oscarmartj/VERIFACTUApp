"""Casos 5, 6, 8, 9, 10, 11, 12, 13 y 14 sobre el recorrido completo (simulador local, sin red).

Todas estas pruebas son SIMULADAS: el destino es app/simulador.py, no la AEAT.
"""
import io
import json
import os
import threading
from pathlib import Path

import pytest
from conftest import EMPRESA_DEMO, Cliente, borrador_listo, preparar_empresa, subir

from app import auditoria, copias, db, remision, simulador
from app.pdf_final import leer_qrs

EVID = Path(__file__).resolve().parents[1] / "docs" / "evidencias"


def guardar_evidencia(nombre, datos):
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / nombre).write_bytes(datos if isinstance(datos, bytes) else datos.encode("utf-8"))


def emitir(c, bid, clave="k-1", entorno="simulador"):
    return c.post(f"/api/borradores/{bid}/emitir", {"entorno": entorno, "clave_idempotencia": clave, "confirmo": True})


def enviar(eid, entorno="simulador", forzar=True):
    con = db.conectar()
    try:
        return remision.procesar(con, eid, entorno, forzar_flujo=forzar)
    finally:
        con.close()


def variante_pdf(numero, total="1.028,50", nombre=None):
    """Genera otro documento preparatorio con la misma plantilla y otro número."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import generar_pdfs_prueba as g
    d = dict(g.REPLICA, numero=numero, total=total, fecha=db.ahora().strftime("%d/%m/%Y"))
    return g.dibujar(d)


def subir_bytes(c, eid, nombre, datos):
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (nombre, datos, "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    return c.ok(r)[0]


def preparar_desde_bytes(c, eid, nombre, datos):
    d = subir_bytes(c, eid, nombre, datos)
    c.ok(c.post(f"/api/documentos/{d['documento_id']}/clasificacion", {"clasificacion": "preparatorio", "motivo": "prueba"}))
    bid = c.ok(c.post(f"/api/documentos/{d['documento_id']}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    return bid


# --- Caso 6 y 14: coherencia PDF ↔ datos ↔ XML ↔ QR; revisión visual y decodificación del QR ----------------
def test_caso6_14_coherencia_y_qr(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b = borrador_listo(c, eid)
    assert b["puede_emitir"], b["incidencias"]
    fid = c.ok(emitir(c, bid))["factura_id"]
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert all(f["verificacion"].values())
    con = db.conectar()
    coh = auditoria.coherencia_factura(con, fid)
    assert coh["ok"], coh
    pdf = c.get(f"/api/facturas/{fid}/pdf").content
    qrs = leer_qrs(pdf, 1)
    assert qrs[0] == [f["url_qr"]]
    assert f["url_qr"].startswith("https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=B82991316&numserie=166%2F26&fecha=")
    assert f["url_qr"].endswith("&importe=1028.50")
    # Referencias comerciales conservadas en el PDF definitivo (caso 3)
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as d:
        txt = d.pages[0].extract_text()
    for t in ("PC24-00987", "G09876543", "PFDI RESIDENCIAL OLMOS", "Certificación nº", "1.028,50", "QR tributario:", "VERI*FACTU",
              "SIMULACIÓN LOCAL"):
        assert t in txt, t
    guardar_evidencia("factura_simulada_166-26.pdf", pdf)
    guardar_evidencia("factura_simulada_166-26_p1.png", c.get(f"/api/facturas/{fid}/pdf/1.png").content)
    reg = f["registros"][0]
    guardar_evidencia("registro_alta_166-26.xml", c.get(f"/api/registros/{reg['id']}/xml").content)
    guardar_evidencia("coherencia_166-26.json", json.dumps({"verificacion_pdf": f["verificacion"], "coherencia": coh,
                                                             "qr_decodificado": qrs, "cadena_huella": reg["cadena_huella"],
                                                             "huella": reg["huella"]}, ensure_ascii=False, indent=1))
    # El borrador (vista previa) no lleva QR
    prev = c.get(f"/api/borradores/{bid}/vista-previa.pdf").content
    assert leer_qrs(prev, 1) == [[]]


# --- Caso 4 (bloqueo de emisión) y 5 (casos tributarios) ----------------------------------------------
def test_caso4_descuadre_bloquea(app_cliente):
    c = app_cliente
    r = c.post("/api/instalar", {"email": "a@demo.test", "nombre": "A", "clave": "clave-segura-123", "empresa": EMPRESA_DEMO})
    c.csrf = c.ok(r)["csrf"]
    eid = c.ok(r)["empresas"][0]["id"]
    from conftest import configurar
    configurar(c, eid, patron="PRB-{n}", siguiente=3)
    d = subir(c, eid, "demo_descuadre.pdf")
    c.ok(c.post(f"/api/documentos/{d['documento_id']}/clasificacion", {"clasificacion": "preparatorio", "motivo": "x"}))
    bid = c.ok(c.post(f"/api/documentos/{d['documento_id']}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    codigos = {i["codigo"] for i in b["incidencias"] if i["nivel"] == "bloqueo"}
    assert "TOTAL_DESCUADRE" in codigos and not b["puede_emitir"]
    r = emitir(c, bid)
    assert r.status_code == 409
    # Corrección registrada en el historial y desbloqueo
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "importe_total", "valor": "1028.50", "motivo": "Total mal sumado"}]}))
    assert b["cambios"][-1]["motivo"] == "Total mal sumado"
    assert "TOTAL_DESCUADRE" not in {i["codigo"] for i in b["incidencias"]}


def test_caso4_escaneado_requiere_revision(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    d = subir(c, eid, "replica_166-26_escaneada.pdf")
    c.ok(c.post(f"/api/documentos/{d['documento_id']}/clasificacion", {"clasificacion": "preparatorio", "motivo": "x"}))
    bid = c.ok(c.post(f"/api/documentos/{d['documento_id']}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    niveles = {(i["nivel"], i["codigo"]) for i in b["incidencias"]}
    # Lectura OCR: los valores dudosos se piden confirmar y no se puede expedir sin revisarlos
    assert any(n == "confirmar" and cod == "DUDOSO" for n, cod in niveles)
    assert not b["puede_emitir"]
    # El NIF del emisor: o se lee bien, o se bloquea por ilegible; nunca se inventa
    nif_emisor = (b["datos"].get("emisor") or {}).get("nif")
    assert nif_emisor in ("B82991316", None, "")
    if not nif_emisor:
        assert ("bloqueo", "EMISOR_FALTA") in niveles and any(cod == "NIF_ILEGIBLE" for _, cod in niveles)


def test_caso5_casos_tributarios(app_cliente):
    """Tipos múltiples, retención IRPF (soportados) y casos no soportados (bloqueados)."""
    c = app_cliente
    r = c.post("/api/instalar", {"email": "a@demo.test", "nombre": "A", "clave": "clave-segura-123", "empresa": EMPRESA_DEMO})
    c.csrf = c.ok(r)["csrf"]
    eid = c.ok(r)["empresas"][0]["id"]
    from conftest import configurar
    configurar(c, eid, patron="PRB-{n}", siguiente=4)
    # Retención: ImporteTotal = base + cuota; total a pagar = total − retención
    d = subir(c, eid, "demo_retencion.pdf")
    c.ok(c.post(f"/api/documentos/{d['documento_id']}/clasificacion", {"clasificacion": "preparatorio", "motivo": "x"}))
    bid = c.ok(c.post(f"/api/documentos/{d['documento_id']}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert b["datos"]["importe_total"] == "1028.50" and b["datos"]["total_a_pagar"] == "901.00"
    assert b["datos"]["retencion"]["importe"] == "127.50"
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    assert any(k.startswith("RETENCION") for k in claves)
    b = c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    assert b["puede_emitir"], [i for i in b["incidencias"] if i["nivel"] != "aviso"]
    fid = c.ok(emitir(c, bid, "ret-1"))["factura_id"]
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert f["url_qr"].endswith("importe=1028.50")
    assert "<sf:ImporteTotal>1028.50</sf:ImporteTotal>" in c.get(f"/api/registros/{f['registros'][0]['id']}/xml").text
    guardar_evidencia("factura_simulada_retencion_p1.png", c.get(f"/api/facturas/{fid}/pdf/1.png").content)
    # Varios tipos impositivos (10 % + 21 %) en un borrador corregido a mano
    from app import borradores
    datos = {"num_serie": "PRB-9", "emisor": {"nif": "B00000174", "nombre": "X", "domicilio": "d"},
             "destinatario": {"nif": "B00000257", "nombre": "Y", "domicilio": "d"}, "descripcion_operacion": "Servicios",
             "desglose": [{"tipo_iva": "10", "base": "100.00", "cuota": "10.00"}, {"tipo_iva": "21", "base": "200.00", "cuota": "42.00"}],
             "base_imponible": "300.00", "cuota_total": "52.00", "importe_total": "352.00", "total_a_pagar": "352.00",
             "lineas": [{"importe": "100.00"}, {"importe": "200.00"}], "indicios": []}
    con = db.conectar()
    emp = dict(db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), ambito_dict={"puede_emitir": True})
    inc = borradores.validar(con, emp, None, datos, serie={"codigo": "A", "siguiente_numero": 9, "numero_detectado": 9})
    assert not [i for i in inc if i["nivel"] == "bloqueo"], inc
    # Casos no soportados: exención, ISP, tipo no vigente
    for ind, cod in (("exencion", "NO_SOPORTADO_EXENCION"), ("inversion_sujeto_pasivo", "NO_SOPORTADO_INVERSION_SUJETO_PASIVO"),
                     ("suplidos", "NO_SOPORTADO_SUPLIDOS")):
        inc = borradores.validar(con, emp, None, dict(datos, indicios=[ind]), serie={"codigo": "A", "siguiente_numero": 9, "numero_detectado": 9})
        assert cod in {i["codigo"] for i in inc if i["nivel"] == "bloqueo"}
    d2 = json.loads(json.dumps(datos))
    d2["desglose"][0]["tipo_iva"] = "7.5"
    d2["desglose"][0]["cuota"] = "7.50"
    d2["cuota_total"], d2["importe_total"], d2["total_a_pagar"] = "49.50", "349.50", "349.50"
    inc = borradores.validar(con, emp, None, d2, serie={"codigo": "A", "siguiente_numero": 9, "numero_detectado": 9})
    assert "TIPO_NO_SOPORTADO" in {i["codigo"] for i in inc}


# --- Caso 8: duplicados ----------------------------------------------------------------------------
def test_caso8_duplicados(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    did, bid, _ = borrador_listo(c, eid)
    # Reimportación del mismo archivo
    r = subir(c, eid, "replica_166-26.pdf")
    assert r["duplicado"] and r["documento_id"] == did
    # Doble clic: misma clave de idempotencia -> misma factura
    f1 = c.ok(emitir(c, bid, "doble"))["factura_id"]
    f2 = c.ok(emitir(c, bid, "doble"))["factura_id"]
    assert f1 == f2
    # Otro clic con otra clave sobre el mismo borrador -> devuelve la ya expedida, no crea otra
    assert c.ok(emitir(c, bid, "otra"))["factura_id"] == f1
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM facturas")["n"] == 1
    assert db.fila(con, "SELECT COUNT(*) n FROM registros")["n"] == 1
    # Misma identidad fiscal en un PDF distinto (otro archivo con el mismo número)
    otro = variante_pdf("166/26") + b"\n%variacion"
    d = subir_bytes(c, eid, "copia_166.pdf", otro)
    assert not d["duplicado"]
    assert any(x["tipo"] == "factura" for x in d["duplicados_fiscales"])
    c.ok(c.post(f"/api/documentos/{d['documento_id']}/clasificacion", {"clasificacion": "preparatorio", "motivo": "x"}))
    bid2 = c.ok(c.post(f"/api/documentos/{d['documento_id']}/borrador"))["borrador_id"]
    b2 = c.ok(c.get(f"/api/borradores/{bid2}"))
    assert "DUPLICADA" in {i["codigo"] for i in b2["incidencias"]}
    assert emitir(c, bid2, "x2").status_code == 409
    # Reintento de comunicación: no crea nuevos registros
    simulador.fijar_modo("sin_respuesta", 1)
    assert enviar(eid)["resultado"] == "sin_respuesta"
    enviar(eid)
    assert db.fila(con, "SELECT COUNT(*) n FROM registros")["n"] == 1
    assert db.fila(con, "SELECT COUNT(*) n FROM facturas")["n"] == 1


# --- Caso 9: emisiones concurrentes ------------------------------------------------------------------------
def test_caso9_concurrencia(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    bids = [preparar_desde_bytes(c, eid, f"f{n}.pdf", variante_pdf(f"{n}/26")) for n in range(166, 172)]
    resultados = {}

    def lanzar(bid):
        resultados[bid] = emitir(c, bid, f"conc-{bid}").status_code
    hilos = [threading.Thread(target=lanzar, args=(b,)) for b in reversed(bids)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    # Los números deben expedirse en orden correlativo: los que llegan fuera de orden se rechazan y se reintentan
    for _ in range(6):
        for b in bids:
            if resultados[b] != 200:
                resultados[b] = emitir(c, b, f"conc-{b}-r").status_code
    assert all(v == 200 for v in resultados.values()), resultados
    con = db.conectar()
    regs = db.filas(con, "SELECT secuencia, num_serie FROM registros ORDER BY secuencia")
    assert [r["secuencia"] for r in regs] == list(range(1, 7))
    assert sorted(int(r["num_serie"].split("/")[0]) for r in regs) == list(range(166, 172))
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]
    # Mismo número desde dos borradores a la vez: solo uno se expide
    b1 = preparar_desde_bytes(c, eid, "a.pdf", variante_pdf("172/26"))
    b2 = preparar_desde_bytes(c, eid, "b.pdf", variante_pdf("172/26") + b"%x")
    res = []
    hs = [threading.Thread(target=lambda b=b: res.append(emitir(c, b, f"dup-{b}").status_code)) for b in (b1, b2)]
    [h.start() for h in hs]
    [h.join() for h in hs]
    assert sorted(res) == [200, 409]
    assert db.fila(con, "SELECT COUNT(*) n FROM facturas WHERE num_serie='172/26'")["n"] == 1


# --- Caso 10: reinicios, cortes, rechazos, respuestas parciales, sin confirmar ---------------------------------------
def test_caso10_recuperacion(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    bids = [preparar_desde_bytes(c, eid, f"f{n}.pdf", variante_pdf(f"{n}/26")) for n in range(166, 169)]
    con = db.conectar()
    fid = c.ok(emitir(c, bids[0], "r1"))["factura_id"]
    # a) Servicio caído: la factura sigue emitida, el registro queda pendiente con incidencia
    simulador.fijar_modo("caido", 1)
    r = enviar(eid)
    assert r["resultado"] == "no_enviado"
    reg = db.fila(con, "SELECT * FROM registros WHERE factura_id=?", (fid,))
    assert reg["estado_envio"] == "pendiente" and reg["incidencia"] == 1
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert f["factura"]["estado"] == "emitida"
    assert "Incidencia" in (c.ok(c.get(f"/api/empresas/{eid}"))["avisos"] or [""])[0]
    # Se puede seguir facturando durante la incidencia (el nuevo registro nace marcado)
    fid2 = c.ok(emitir(c, bids[1], "r2"))["factura_id"]
    assert db.fila(con, "SELECT incidencia FROM registros WHERE factura_id=?", (fid2,))["incidencia"] == 1
    # b) Sin respuesta (procesado en la AEAT pero sin confirmación) -> sin_confirmar, luego duplicado confirma
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    simulador.fijar_modo("sin_respuesta", 1)
    r = enviar(eid)
    assert r["resultado"] == "sin_respuesta"
    assert {x["estado_envio"] for x in db.filas(con, "SELECT estado_envio FROM registros")} == {"sin_confirmar"}
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    r = enviar(eid)
    assert r["accion"] == "enviado"
    regs = db.filas(con, "SELECT * FROM registros ORDER BY id")
    assert all(x["estado_envio"] == "aceptado" for x in regs), regs
    assert "duplicado" in regs[0]["descripcion_error"]
    peticion = (Path(os.environ["VERIFACTU_DATA_DIR"]) / "almacen" / db.fila(con, "SELECT peticion_ruta FROM envios ORDER BY id DESC LIMIT 1")["peticion_ruta"]).read_bytes()
    assert b"<sum1:Incidencia>S</sum1:Incidencia>" in peticion
    # c) Error 500 -> sin confirmar (no se presupone inexistencia)
    fid3 = c.ok(emitir(c, bids[2], "r3"))["factura_id"]
    simulador.fijar_modo("error_500", 1)
    assert enviar(eid)["resultado"] == "respuesta_ilegible"
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE factura_id=?", (fid3,))["estado_envio"] == "sin_confirmar"
    # d) Reinicio a mitad de envío: 'enviando' -> 'sin_confirmar'
    con.execute("UPDATE control_flujo SET bloqueo_hasta=NULL")
    con.execute("UPDATE registros SET estado_envio='enviando' WHERE factura_id=?", (fid3,))
    assert remision.recuperar_interrumpidos(con) == 1
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE factura_id=?", (fid3,))["estado_envio"] == "sin_confirmar"
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE factura_id=?", (fid3,))["estado_envio"] == "aceptado"
    # e) Rechazo del envío completo (certificado no autorizado) -> bloqueado, no "rechazado"
    b4 = preparar_desde_bytes(c, eid, "f169.pdf", variante_pdf("169/26"))
    fid4 = c.ok(emitir(c, b4, "r4"))["factura_id"]
    simulador.fijar_modo("rechazo", 1)
    assert enviar(eid)["resultado"] == "fault_cliente"
    reg4 = db.fila(con, "SELECT * FROM registros WHERE factura_id=?", (fid4,))
    assert reg4["estado_envio"] == "bloqueado" and "4112" in reg4["descripcion_error"]
    rr = c.ok(c.post(f"/api/registros/{reg4['id']}/reintentar"))
    assert remision._futuro(rr["siguiente_intento_en"])    # no antes de 60 s desde el último intento
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")  # (pasa el tiempo)
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE factura_id=?", (fid4,))["estado_envio"] == "aceptado"
    # f) Control de flujo: tras un envío no se vuelve a enviar antes de TiempoEsperaEnvio
    b5 = preparar_desde_bytes(c, eid, "f170.pdf", variante_pdf("170/26"))
    c.ok(emitir(c, b5, "r5"))
    assert remision.procesar(con, eid, "simulador")["accion"] == "esperando_control_flujo"
    assert db.fila(con, "SELECT tiempo_espera FROM control_flujo")["tiempo_espera"] == 60
    # g) Respuesta parcial (falta una línea): el registro queda sin confirmar
    from app.verifactu import respuesta as respmod
    cola = db.filas(con, "SELECT * FROM registros WHERE estado_envio='pendiente'")
    with db.transaccion(con):
        eidn = con.execute("INSERT INTO envios (empresa_id, entorno, destino, iniciado_en, num_registros) VALUES (?,?,?,?,1)",
                           (eid, "simulador", "prueba", db.ahora_iso())).lastrowid
    remision._aplicar_respuesta(con, eidn, eid, "simulador", cola, respmod.Respuesta(tipo="respuesta", estado_envio="Correcto", tiempo_espera=60))
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (cola[0]["id"],))["estado_envio"] == "sin_confirmar"
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]


# --- Caso 11: subsanación, rectificación y anulación ---------------------------------------------------------------
def test_caso11_procedimientos(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    con = db.conectar()
    alta = db.fila(con, "SELECT * FROM registros WHERE factura_id=?", (fid,))
    assert alta["estado_envio"] == "aceptado"
    # Subsanación: nuevo registro encadenado, el original intacto
    rid = c.ok(c.post(f"/api/registros/{alta['id']}/subsanar", {"motivo": "Corregir descripción", "confirmo": True}))["registro_id"]
    sub = db.fila(con, "SELECT * FROM registros WHERE id=?", (rid,))
    assert sub["subsanacion"] == "S" and sub["rechazo_previo"] is None and sub["huella_anterior"] == alta["huella"]
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (rid,))["estado_envio"] == "aceptado"
    # Editar o borrar lo expedido está prohibido por la base de datos
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE facturas SET importe_total='1.00' WHERE id=?", (fid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM registros WHERE id=?", (rid,))
    # Rectificativa por diferencias (R1, serie específica)
    s = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "R", "patron": "R{n}/{aa}", "tipo": "rectificativa", "siguiente_numero": 1}))
    c.ok(c.post(f"/api/series/{s['id']}/confirmar"))
    rb = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R1", "motivo": "Error en la base imponible"}))["borrador_id"]
    anio = db.ahora().strftime("%y")
    cambios = [{"campo": "num_serie", "valor": f"R1/{anio}", "motivo": "n"},
               {"campo": "desglose.0.base", "valor": "-100.00", "motivo": "d"}, {"campo": "desglose.0.cuota", "valor": "-21.00", "motivo": "d"},
               {"campo": "base_imponible", "valor": "-100.00", "motivo": "d"}, {"campo": "cuota_total", "valor": "-21.00", "motivo": "d"},
               {"campo": "importe_total", "valor": "-121.00", "motivo": "d"}, {"campo": "total_a_pagar", "valor": "-121.00", "motivo": "d"}]
    b = c.ok(c.patch(f"/api/borradores/{rb}", {"cambios": cambios}))
    assert b["puede_emitir"], [i for i in b["incidencias"] if i["nivel"] != "aviso"]
    rf = c.ok(emitir(c, rb, "rect-1"))["factura_id"]
    xml = c.get(f"/api/registros/{c.ok(c.get(f'/api/facturas/{rf}'))['registros'][0]['id']}/xml").text
    assert "<sf:TipoFactura>R1</sf:TipoFactura>" in xml and "<sf:TipoRectificativa>I</sf:TipoRectificativa>" in xml
    assert "<sf:NumSerieFactura>166/26</sf:NumSerieFactura>" in xml  # factura rectificada
    assert c.ok(c.get(f"/api/facturas/{fid}"))["factura"]["estado"] == "rectificada"
    guardar_evidencia("rectificativa_simulada_p1.png", c.get(f"/api/facturas/{rf}/pdf/1.png").content)
    enviar(eid)
    # Anulación de registro (factura que no debió expedirse)
    an = c.ok(c.post(f"/api/facturas/{rf}/anular", {"motivo": "Expedida por error", "confirmo": True}))["registro_id"]
    ra = db.fila(con, "SELECT * FROM registros WHERE id=?", (an,))
    assert ra["tipo"] == "anulacion" and ra["sin_registro_previo"] is None
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (an,))["estado_envio"] == "aceptado"
    assert c.ok(c.get(f"/api/facturas/{rf}"))["factura"]["estado"] == "anulada"
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]
    guardar_evidencia("cadena_simulada.json", json.dumps(db.filas(con, """SELECT secuencia, tipo, num_serie, subsanacion, rechazo_previo,
        huella_anterior, huella, estado_envio FROM registros ORDER BY secuencia"""), indent=1))


# --- Caso 12: copia y restauración sin perder continuidad ni reenviar --------------------------------------------
def test_caso12_copia_restauracion(app_cliente, entorno_tmp):
    c = app_cliente
    eid = preparar_empresa(c)
    bids = [preparar_desde_bytes(c, eid, f"f{n}.pdf", variante_pdf(f"{n}/26")) for n in range(166, 169)]
    con = db.conectar()
    c.ok(emitir(c, bids[0], "c1"))
    enviar(eid)
    copia = copias.crear_copia(con)
    assert copias.verificar_copia(copia)["ok"]
    c.ok(emitir(c, bids[1], "c2"))   # posterior a la copia
    enviar(eid)
    envios_antes = db.fila(con, "SELECT COUNT(*) n FROM envios")["n"]
    con.close()
    # Desastre: se pierde la base de datos -> restaurar copia + reproducir diario
    r = copias.restaurar(copia)
    assert r["facturas_recuperadas"] == 1 and r["registros_recuperados"] == 1
    con = db.conectar()
    regs = db.filas(con, "SELECT secuencia, num_serie, estado_envio FROM registros ORDER BY secuencia")
    assert [x["estado_envio"] for x in regs] == ["aceptado", "aceptado"]
    # Nada que reenviar
    assert remision.procesar(con, eid, "simulador", forzar_flujo=True)["accion"] == "nada"
    # La numeración y la cadena continúan
    assert r["pdf_irrecuperables"] == [], r
    c.ok(emitir(c, bids[2], "c3"))
    regs = db.filas(con, "SELECT secuencia, huella, huella_anterior FROM registros ORDER BY secuencia")
    assert regs[2]["huella_anterior"] == regs[1]["huella"]
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]
    assert envios_antes >= 2


# --- Caso 13: aislamiento entre usuarios y empresas ---------------------------------------------------------------
def test_caso13_acceso_ajeno(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    did, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    rid = c.ok(c.get(f"/api/facturas/{fid}"))["registros"][0]["id"]
    # Segundo usuario con su propia empresa
    con = db.conectar()
    from app import seguridad
    from app.api import crear_empresa
    with db.transaccion(con):
        uid = seguridad.crear_usuario(con, "otro@ejemplo.test", "Otro", "otra-clave-segura")
        crear_empresa(con, uid, "B00000257", "CONSTRUCTORA FICTICIA DEL SUR S.A.", "Sevilla")
    from fastapi.testclient import TestClient
    otro = Cliente(TestClient(c.tc.app))
    d = otro.ok(otro.post("/api/sesion", {"email": "otro@ejemplo.test", "clave": "otra-clave-segura"}))
    otro.csrf = d["csrf"]
    for url in (f"/api/documentos/{did}", f"/api/documentos/{did}/original", f"/api/documentos/{did}/paginas/1.png",
                f"/api/borradores/{bid}", f"/api/facturas/{fid}", f"/api/facturas/{fid}/pdf", f"/api/registros/{rid}/xml",
                f"/api/empresas/{eid}", f"/api/empresas/{eid}/facturas", f"/api/empresas/{eid}/facturas-lote.zip?ids={fid}"):
        assert otro.get(url).status_code == 404, url
    assert otro.patch(f"/api/borradores/{bid}", {"cambios": []}).status_code == 404
    assert otro.post(f"/api/facturas/{fid}/anular", {"motivo": "x", "confirmo": True}).status_code == 404
    # Sin sesión y sin CSRF
    anon = TestClient(c.tc.app)
    assert anon.get(f"/api/facturas/{fid}").status_code == 401
    assert c.tc.post(f"/api/facturas/{fid}/anular", json={"motivo": "x", "confirmo": True}).status_code == 403
    # Un lector no puede emitir
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lector@ejemplo.test", "nombre": "L", "clave": "clave-lector-123", "rol": "lector"}))
    lec = Cliente(TestClient(c.tc.app))
    lec.csrf = lec.ok(lec.post("/api/sesion", {"email": "lector@ejemplo.test", "clave": "clave-lector-123"}))["csrf"]
    assert lec.get(f"/api/facturas/{fid}").status_code == 200
    assert lec.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R1", "motivo": "x"}).status_code == 404


# --- Separación de entornos y salvaguardas de producción ------------------------------------------------------------
def test_entornos_bloqueados_sin_requisitos(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    for ent in ("pruebas", "produccion"):
        r = emitir(c, bid, f"e-{ent}", entorno=ent)
        assert r.status_code == 403, r.text
    p = c.ok(c.get(f"/api/empresas/{eid}/produccion"))
    assert not p["habilitada"]
    faltan = {x["clave"] for x in p["requisitos"] if not x["cumplido"]}
    assert {"productor", "certificado", "declaracion", "interruptor", "autorizacion_primer_envio", "prueba_aeat"} <= faltan
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM facturas")["n"] == 0


def test_documento_referencia_solo_simulable(app_cliente):
    """La factura de referencia queda marcada: nunca se expide fuera del simulador local."""
    c = app_cliente
    eid = preparar_empresa(c)
    did, bid, _ = borrador_listo(c, eid)
    c.ok(c.post(f"/api/documentos/{did}/bloqueo", {"motivo": "Factura de referencia: no volver a emitir ni remitir a la AEAT"}))
    b = c.ok(c.get(f"/api/borradores/{bid}?entorno=pruebas"))
    assert "DOC_BLOQUEADO" in {i["codigo"] for i in b["incidencias"]}
    b = c.ok(c.get(f"/api/borradores/{bid}?entorno=simulador"))
    assert "DOC_BLOQUEADO" not in {i["codigo"] for i in b["incidencias"]}
