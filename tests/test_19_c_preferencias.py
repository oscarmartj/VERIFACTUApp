"""Carril C — «Modo avanzado»: preferencia de cada persona (desactivada por defecto), guardada en el servidor (migración
0010). Solo cambia lo que muestra la interfaz: nunca da ni quita permisos (el servidor sigue respondiendo 404 a lo que el
papel no permite). Datos ficticios; solo el simulador local."""
import shutil
import uuid

import pytest
from conftest import Cliente, borrador_listo, preparar_empresa
from fastapi.testclient import TestClient

from app import db

CLAVE = "clave-segura-123"


def _invitar(admin: Cliente, eid: int, email: str, rol: str) -> Cliente:
    tok = admin.ok(admin.post(f"/api/empresas/{eid}/invitaciones", {"email": email, "rol": rol}))["token"]
    c = Cliente(TestClient(admin.tc.app))
    c.csrf = c.ok(c.post("/api/registro", {"email": email, "nombre": email.split("@")[0], "clave": CLAVE, "invitacion": tok}))["csrf"]
    return c


def test_modo_avanzado_por_persona_y_desactivado_por_defecto(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    yo = c.ok(c.get("/api/yo"))["usuario"]
    assert yo["modo_avanzado"] == 0                                       # desactivado por defecto
    assert c.ok(c.post("/api/sesion", {"email": "admin@ejemplo.test", "clave": CLAVE}))["usuario"]["modo_avanzado"] == 0
    c.csrf = c.ok(c.get("/api/yo"))["csrf"]
    otra = _invitar(c, eid, "lector@ejemplo.test", "lector")
    assert c.ok(c.put("/api/yo/preferencias", {"modo_avanzado": True})) == {"ok": True, "modo_avanzado": 1}
    assert c.ok(c.get("/api/yo"))["usuario"]["modo_avanzado"] == 1
    assert otra.ok(otra.get("/api/yo"))["usuario"]["modo_avanzado"] == 0  # de cada persona, no de la empresa
    # Al volver a entrar se conserva
    assert c.ok(c.post("/api/sesion", {"email": "admin@ejemplo.test", "clave": CLAVE}))["usuario"]["modo_avanzado"] == 1
    c.csrf = c.ok(c.get("/api/yo"))["csrf"]
    assert c.ok(c.put("/api/yo/preferencias", {"modo_avanzado": False}))["modo_avanzado"] == 0
    # Valores no válidos, sin CSRF y sin sesión
    for malo in ({"modo_avanzado": 1}, {"modo_avanzado": "true"}, {}, {"modo_avanzado": True, "superadmin": 1}):
        assert c.put("/api/yo/preferencias", malo).status_code == 422, malo
    assert c.tc.put("/api/yo/preferencias", json={"modo_avanzado": True}).status_code == 403
    anon = Cliente(TestClient(c.tc.app))
    assert anon.put("/api/yo/preferencias", {"modo_avanzado": True}).status_code == 401
    assert c.ok(c.get("/api/yo"))["usuario"]["modo_avanzado"] == 0
    assert db.fila(db.conectar(), "SELECT superadmin FROM usuarios WHERE email='admin@ejemplo.test'")["superadmin"] == 1


def test_el_modo_avanzado_no_da_permisos(app_cliente):
    """Un lector con el modo avanzado sigue sin poder anular, corregir, crear numeraciones ni consultar a la AEAT: 404
    (como siempre: no se dan detalles)."""
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": uuid.uuid4().hex,
                                                         "confirmo": True}))["factura_id"]
    lector = _invitar(c, eid, "lola@ejemplo.test", "lector")
    emisor = _invitar(c, eid, "emilio@ejemplo.test", "emisor")
    for p in (lector, emisor):
        p.ok(p.put("/api/yo/preferencias", {"modo_avanzado": True}))
    assert lector.post(f"/api/facturas/{fid}/anular", {"motivo": "x", "confirmo": True}).status_code == 404
    assert lector.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R4", "motivo": "x"}).status_code == 404
    assert lector.post(f"/api/empresas/{eid}/consulta", {"entorno": "simulador"}).status_code == 404
    assert emisor.post(f"/api/facturas/{fid}/anular", {"motivo": "x", "confirmo": True}).status_code == 404
    assert emisor.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "Z", "patron": "Z{n}",
                                                       "siguiente_numero": 1}).status_code == 404
    assert c.ok(c.get(f"/api/facturas/{fid}"))["factura"]["estado"] == "emitida"
    # Y lo que sí pueden hacer, lo siguen pudiendo hacer (lectura)
    assert lector.get(f"/api/facturas/{fid}").status_code == 200


def test_migracion_0010_anade_la_preferencia_desactivada(entorno_tmp, monkeypatch):
    previas = entorno_tmp / "migraciones_0009"
    previas.mkdir()
    for f in sorted(db.MIGRACIONES_DIR.glob("*.sql")):
        if f.name < "0010":
            shutil.copy(f, previas / f.name)
    con = db.conectar()
    monkeypatch.setattr(db, "MIGRACIONES_DIR", previas)
    db.migrar(con)
    con.execute("INSERT INTO usuarios (email, nombre, hash_clave, creado_en) VALUES ('x@ejemplo.test','X','h','2026-01-01')")
    monkeypatch.undo()
    assert db.migrar(con) == ["0010_preferencias.sql"]
    assert db.fila(con, "SELECT modo_avanzado FROM usuarios")["modo_avanzado"] == 0
    with pytest.raises(Exception):
        con.execute("UPDATE usuarios SET modo_avanzado=2")
