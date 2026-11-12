"""Regresiones de ISO-5 y UI-6 (revisión integrada final): simulador local de la AEAT.

- ISO-5: el modo de fallo del simulador es de cada obligado (la empresa, por su NIF). Un modo fijado para una empresa
  solo lo consumen los envíos (y consultas) de esa empresa; los de representación (4112/4123/4124), solo los envíos que
  llevan Cabecera/Representante. Un administrador de la empresa lo fija para la suya; el operador, para cualquiera. Un
  modo de una empresa nunca afecta a otra. G3V-2: el panel del operador solo lista los modos que fijó el operador (no
  muestra el NIF de empresas de las que no es miembro).
- UI-6: el simulador guarda la hora de recepción con su huso y la consulta la devuelve bien en un servidor en UTC: el
  «Presentado» de la consulta nunca es anterior a la generación del registro.

Todos los datos son ficticios (NIF con código de provincia 00 o de los fixtures; correos @ejemplo.test). Solo se usa el
simulador local.
"""
import os
import time
from datetime import datetime

import pytest
from conftest import EMPRESA_REPLICA, Cliente, borrador_listo, configurar, preparar_empresa
from fastapi.testclient import TestClient
from test_03_flujo import emitir, enviar, preparar_desde_bytes, variante_pdf
from test_16_c_certificados import asignar, legitimar, peticion, sello_gestoria, subir_mio
from test_16_u_multiusuario import NIF_B, abrir_registro, instalar, nueva_empresa, registrar
from test_16_x_exentas import _borrador, _confirmar_y_emitir, factura_pdf

from app import db, multiusuario, simulador


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def _otro(c: Cliente, email: str, nombre: str) -> Cliente:
    o = Cliente(TestClient(c.tc.app))
    assert registrar(o, email, nombre=nombre).status_code == 200
    o.email = email
    return o


def _modo(c, eid):
    return c.ok(c.get(f"/api/empresas/{eid}/remision?entorno=simulador"))["simulador"]


def _registro(fid):
    return db.fila(db.conectar(), "SELECT * FROM registros WHERE factura_id=? ORDER BY id DESC LIMIT 1", (fid,))


TOTALES_21 = [("BASE IMPONIBLE", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "121,00")]
LINEA_21 = [("Servicio de mantenimiento, noviembre 2026", "1,00", "ud", "100,00", "100,00")]


# --- ISO-5 ---------------------------------------------------------------------------------------------------------------
def test_iso5_el_modo_de_una_empresa_no_lo_consume_otra(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    # Dos empresas de dos personas distintas, cada una con una factura en la cola del simulador
    a = _otro(op, "a@ejemplo.test", "Ana Ficticia")
    ea = a.ok(nueva_empresa(a, EMPRESA_REPLICA["nif"], EMPRESA_REPLICA["razon_social"], EMPRESA_REPLICA["domicilio"]))["id"]
    configurar(a, ea)
    _, bid, _ = borrador_listo(a, ea)
    fa = a.ok(emitir(a, bid, clave="k-a"))["factura_id"]
    b = _otro(op, "b@ejemplo.test", "Bruno Ficticio")
    eb = b.ok(nueva_empresa(b, NIF_B, "INMUEBLES FICTICIOS, S.L.", "C/ Inventada, 3 - 28000 Villaficticia"))["id"]
    configurar(b, eb, patron="ALQ-{n}", siguiente=7)
    bid_b, _ = _borrador(b, eb, "f.pdf", factura_pdf("ALQ-7", "10/11/2026", LINEA_21, TOTALES_21))
    fb = _confirmar_y_emitir(b, bid_b, "k-b")
    # El operador prepara una prueba de fallo para la empresa B (por su NIF)
    r = op.ok(op.post("/api/simulador/modo", {"nif": NIF_B, "modo": "rechazo_registro", "veces": 1}))
    assert (r["modo"], r["veces"]) == ("rechazo_registro", 1)
    assert _modo(a, ea)["modo"] == "normal"                       # A no ve el modo de B
    assert "empresa_id" not in r and r["nif"] == NIF_B           # al operador no se le da el identificador interno
    assert (_modo(b, eb)["modo"], _modo(b, eb)["veces"]) == ("rechazo_registro", 1)
    # La cola de A se procesa antes: su factura se acepta y la prueba de B sigue pendiente
    enviar(ea)
    assert _registro(fa)["estado_envio"] == "aceptado"
    assert (_modo(b, eb)["modo"], _modo(b, eb)["veces"]) == ("rechazo_registro", 1)
    enviar(eb)
    rb = _registro(fb)
    assert rb["estado_envio"] == "rechazado" and rb["codigo_error"] == "1100"
    assert _modo(b, eb)["modo"] == "normal"
    # Un administrador de la empresa fija el modo de la suya (no el de otra)
    r = a.ok(a.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "caido", "veces": 2}))
    assert (r["modo"], r["veces"]) == ("caido", 2) and _modo(a, ea)["veces"] == 2
    assert a.post(f"/api/empresas/{eb}/simulador/modo", {"modo": "caido"}).status_code == 404
    # la ruta del panel (empresa en el cuerpo) es solo del operador: un administrador usa la de su empresa
    assert a.post("/api/simulador/modo", {"nif": NIF_B, "modo": "caido"}).status_code == 403
    assert a.post("/api/simulador/modo", {"modo": "caido"}).status_code == 403   # sin empresa: solo el operador
    assert a.post("/api/simulador/modo", {"empresa_id": ea, "modo": "caido", "veces": 1}).status_code == 403
    assert _modo(a, ea)["veces"] == 2
    assert a.ok(a.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "caido", "veces": 1}))["veces"] == 1
    assert _modo(b, eb)["modo"] == "normal"
    # el operador no es miembro de la empresa de A: sus rutas le responden 404, pero fija el modo desde la plataforma
    assert op.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "caido"}).status_code == 404
    # G3V-2: su panel solo lista los modos que él fijó (por el NIF que escribió), no el NIF de A ni el modo que A fijó
    pendientes = op.ok(op.get("/api/plataforma"))["simulador"]["empresas"]
    assert pendientes == [] and EMPRESA_REPLICA["nif"] not in str(op.get("/api/plataforma").json())
    op.ok(op.post("/api/simulador/modo", {"nif": NIF_B, "modo": "error_500", "veces": 3}))
    pendientes = op.ok(op.get("/api/plataforma"))["simulador"]["empresas"]
    assert [(x["nif"], x["servicio"], x["modo"], x["veces"]) for x in pendientes] == [(NIF_B, "remision", "error_500", 3)]
    assert "empresa_id" not in pendientes[0]
    # si el administrador de B lo cambia para la suya, deja de listarse (ya no es el que fijó el operador)
    b.ok(b.post(f"/api/empresas/{eb}/simulador/modo", {"modo": "caido", "veces": 1}))
    assert op.ok(op.get("/api/plataforma"))["simulador"]["empresas"] == []
    # y la respuesta al operador no le enseña los modos que la empresa fijó para sí (el de la consulta, aquí)
    b.ok(b.post(f"/api/empresas/{eb}/simulador/modo", {"modo": "caido", "veces": 2, "servicio": "consulta"}))
    r = op.ok(op.post("/api/simulador/modo", {"nif": NIF_B, "modo": "error_500", "veces": 1}))
    assert (r["modo"], r["veces"], r["consulta"]) == ("error_500", 1, {"modo": "normal", "veces": 0})
    assert _modo(b, eb)["consulta"] == {"modo": "caido", "veces": 2}
    op.ok(op.post("/api/simulador/modo", {"nif": NIF_B, "modo": "normal"}))
    assert _modo(a, ea)["modo"] == "caido"
    # Valores no válidos
    assert a.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "caido", "veces": "3"}).status_code == 422
    assert a.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "caido", "veces": 21}).status_code == 422
    assert a.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "inventado"}).status_code == 422
    # Un lector de la empresa no puede cambiarlo (como el resto de la API, sin el papel necesario responde 404)
    inv = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "l@ejemplo.test", "rol": "lector"}))
    lec = _otro(op, "l@ejemplo.test", "Luis Ficticio")
    lec.ok(lec.post("/api/invitaciones/aceptar", {"token": inv["token"]}))
    assert lec.post(f"/api/empresas/{ea}/simulador/modo", {"modo": "normal"}).status_code == 404
    assert lec.post("/api/simulador/modo", {"empresa_id": ea, "modo": "normal"}).status_code == 403
    assert _modo(lec, ea)["veces"] == 1


def test_iso5_modos_de_representacion_solo_con_representante(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    f1 = c.ok(emitir(c, bid))["factura_id"]
    c.ok(c.post(f"/api/empresas/{eid}/simulador/modo", {"modo": "no_apoderado", "veces": 1}))
    # Un envío sin Cabecera/Representante no se ve afectado ni gasta la prueba
    enviar(eid)
    reg, sobre = peticion(db.conectar(), f1)
    assert reg["estado_envio"] == "aceptado" and b"Representante" not in sobre
    assert (_modo(c, eid)["modo"], _modo(c, eid)["veces"]) == ("no_apoderado", 1)
    # Con el certificado de un tercero legitimado (representante), el envío recibe el 4112 y la prueba se consume
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "simulador"))
    legitimar(c, eid, metodo="apoderamiento_aeat")
    f2 = c.ok(emitir(c, preparar_desde_bytes(c, eid, "f167.pdf", variante_pdf("167/26")), "k-2"))["factura_id"]
    enviar(eid)
    reg, sobre = peticion(db.conectar(), f2)
    assert reg["estado_envio"] == "bloqueado" and "4112" in reg["descripcion_error"] and b"Representante" in sobre
    assert _modo(c, eid)["modo"] == "normal"


def test_iso5_consulta_solo_la_de_esa_empresa(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    c.ok(c.post(f"/api/empresas/{eid}/simulador/modo", {"modo": "caido", "veces": 1, "servicio": "consulta"}))
    nif = EMPRESA_REPLICA["nif"]
    assert simulador.modo_actual(nif)["consulta"] == {"modo": "caido", "veces": 1}
    assert simulador.modo_actual(NIF_B)["consulta"] == {"modo": "normal", "veces": 0}
    hoy = db.ahora()
    cuerpo = {"entorno": "simulador", "ejercicio": str(hoy.year), "periodo": f"{hoy.month:02d}"}
    assert c.post(f"/api/empresas/{eid}/consulta", cuerpo).status_code == 502   # servicio caído (simulado)
    assert simulador.modo_actual(nif)["consulta"]["modo"] == "normal"
    c.ok(c.post(f"/api/empresas/{eid}/consulta", cuerpo))
    # la ruta del panel del operador (con el NIF), con la misma validación
    r = c.ok(c.post("/api/simulador/modo", {"nif": nif, "modo": "respuesta_invalida", "servicio": "consulta", "veces": 2}))
    assert r["consulta"] == {"modo": "respuesta_invalida", "veces": 2} and r["modo"] == "normal"
    assert c.post("/api/simulador/modo", {"nif": nif, "modo": "no_apoderado", "servicio": "consulta"}).status_code == 422
    assert c.post("/api/simulador/modo", {"empresa_id": eid, "modo": "caido"}).status_code == 422   # solo con el NIF
    assert c.post("/api/simulador/modo", {"nif": 7, "modo": "caido"}).status_code == 422


# --- UI-6 ----------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def servidor_en_utc():
    """El proceso en UTC, como en el contenedor Docker (sin TZ): datetime.now() sin huso da la hora UTC."""
    antes = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if antes is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = antes
    time.tzset()


def test_ui6_presentado_no_es_anterior_a_la_generacion(servidor_en_utc, app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    gen = datetime.fromisoformat(f["registros"][-1]["fecha_hora_huso"])
    hoy = db.ahora()
    q = c.ok(c.post(f"/api/empresas/{eid}/consulta",
                    {"entorno": "simulador", "ejercicio": str(hoy.year), "periodo": f"{hoy.month:02d}"}))
    fila = next(x for x in q["coinciden"] if x.get("timestamp_presentacion"))
    pres = datetime.fromisoformat(fila["timestamp_presentacion"])
    assert pres.utcoffset() is not None
    assert 0 <= (pres - gen).total_seconds() < 120, (gen, pres)
    # la hora guardada lleva su huso
    con = simulador._db()
    assert datetime.fromisoformat(con.execute("SELECT recibido_en FROM sim_registros").fetchone()[0]).tzinfo is not None


def test_ui6_marcas_antiguas_sin_huso_se_leen_como_utc():
    """Las guardadas por versiones anteriores (hora local del servidor sin huso; en Docker, UTC)."""
    assert simulador._marca("2026-11-11T15:54:10") == "2026-11-11T16:54:10+01:00"
    assert simulador._marca("2026-07-01T12:00:00") == "2026-07-01T14:00:00+02:00"
    assert simulador._marca("2026-11-11T16:54:10+01:00") == "2026-11-11T16:54:10+01:00"
