"""Carril B — paquete 6: invitaciones sin callejón sin salida. La invitación de un administrador de empresa crea la cuenta
de la persona invitada desde su único enlace (de un solo uso, con caducidad, dirigido a su correo y con los límites y cupos
de G1); el registro abierto a cualquiera sigue cerrado; el operador puede desactivarlo; y quien acepta puede declarar a la
vez que está autorizado a actuar por la empresa."""
import pytest
from conftest import EMPRESA_REPLICA, Cliente
from fastapi.testclient import TestClient
from test_16_u_multiusuario import consulta, instalar, registrar, uid_de

from app import db, multiusuario


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def nuevo(op: Cliente) -> Cliente:
    return Cliente(TestClient(op.tc.app))


def administradora(op: Cliente) -> tuple[Cliente, int]:
    tok = op.ok(op.post("/api/plataforma/invitaciones", {"email": "ana@ejemplo.test"}))["token"]
    a = nuevo(op)
    assert registrar(a, "ana@ejemplo.test", invitacion=tok).status_code == 200
    return a, a.ok(a.post("/api/empresas", EMPRESA_REPLICA))["id"]


def test_la_invitacion_de_la_empresa_crea_la_cuenta_con_un_solo_enlace(app_cliente):
    op = app_cliente
    instalar(op)                                                   # registro cerrado (por defecto)
    assert op.ok(op.get("/api/plataforma"))["ajustes"] == {"registro_abierto": False, "invitaciones_empresa_crean_cuentas": True}
    a, ea = administradora(op)
    inv = a.ok(a.post(f"/api/empresas/{ea}/miembros", {"email": "juan@ejemplo.test", "rol": "emisor"}))["invitacion"]
    assert inv["crea_cuenta"] is True and consulta(op, inv["token"])["crea_cuenta"] is True
    # Un solo enlace: crea la cuenta y la deja en la empresa con el rol de la invitación; declara a la vez su autorización
    juan = nuevo(op)
    r = juan.post("/api/registro", {"email": "juan@ejemplo.test", "nombre": "Juan Ficticio", "clave": "clave-segura-123",
                                    "invitacion": inv["token"], "declaro": True})
    d = juan.ok(r)
    assert [(e["id"], e["rol"]) for e in d["empresas"]] == [(ea, "emisor")]
    m = db.fila(db.conectar(), "SELECT * FROM miembros WHERE usuario_id=? AND empresa_id=?", (uid_de("juan@ejemplo.test"), ea))
    assert m["declaracion_autorizacion_en"] and m["relacion"] == "empleado_autorizado"
    # De un solo uso, dirigido a su correo y con caducidad
    assert nuevo(op).post("/api/registro", {"email": "juan@ejemplo.test", "nombre": "X", "clave": "clave-segura-123",
                                            "invitacion": inv["token"]}).status_code == 409
    inv2 = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "lucia@ejemplo.test", "rol": "lector"}))
    assert registrar(nuevo(op), "otra@ejemplo.test", invitacion=inv2["token"]).status_code == 409
    db.conectar().execute("UPDATE invitaciones SET expira_en='2000-01-01T00:00:00+00:00' WHERE id=?", (inv2["id"],))
    assert registrar(nuevo(op), "lucia@ejemplo.test", invitacion=inv2["token"]).status_code == 409
    # Sin declaración: la cuenta se crea igual y la declaración queda pendiente
    inv3 = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "eva@ejemplo.test", "rol": "lector"}))
    assert registrar(nuevo(op), "eva@ejemplo.test", invitacion=inv3["token"]).status_code == 200
    assert db.fila(db.conectar(), "SELECT declaracion_autorizacion_en d FROM miembros WHERE usuario_id=?",
                   (uid_de("eva@ejemplo.test"),))["d"] is None
    # El registro abierto a cualquiera sigue cerrado
    r = nuevo(op).post("/api/registro", {"email": "libre@ejemplo.test", "nombre": "X", "clave": "clave-segura-123"})
    assert r.status_code == 403 and db.fila(db.conectar(), "SELECT 1 FROM usuarios WHERE email='libre@ejemplo.test'") is None
    assert nuevo(op).post("/api/registro", {"email": "z@ejemplo.test", "nombre": "X", "clave": "clave-segura-123",
                                            "invitacion": inv3["token"], "declaro": "si"}).status_code == 422


def test_el_operador_puede_desactivarlo_y_los_limites_siguen(app_cliente, monkeypatch):
    op = app_cliente
    instalar(op)
    a, ea = administradora(op)
    assert op.ok(op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": False}))[
        "invitaciones_empresa_crean_cuentas"] is False
    inv = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "bot@desechable.test", "rol": "lector"}))
    assert inv["crea_cuenta"] is False
    r = registrar(nuevo(op), "bot@desechable.test", invitacion=inv["token"])
    assert r.status_code == 403 and r.json() == {"detail": multiusuario.MENSAJE_SOLO_CON_CUENTA}
    op.ok(op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": True}))
    # Cupo de invitaciones pendientes por empresa y límite de altas por quien invita (G1) siguen aplicándose
    monkeypatch.setitem(multiusuario.CUPOS, "invitaciones_pendientes_empresa", 2)
    a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "p1@ejemplo.test", "rol": "lector"}))
    assert a.post(f"/api/empresas/{ea}/invitaciones", {"email": "p2@ejemplo.test", "rol": "lector"}).status_code == 429
    monkeypatch.setitem(multiusuario.CUPOS, "invitaciones_pendientes_empresa", 100)
    monkeypatch.setattr(multiusuario.LIMITE_ALTAS_INVITADOR, "maximo", 1)
    t1 = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "q1@ejemplo.test", "rol": "lector"}))["token"]
    t2 = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "q2@ejemplo.test", "rol": "lector"}))["token"]
    assert registrar(nuevo(op), "q1@ejemplo.test", invitacion=t1).status_code == 200
    assert registrar(nuevo(op), "q2@ejemplo.test", invitacion=t2).status_code == 429


def test_aceptar_con_una_cuenta_existente_y_declarar(app_cliente):
    op = app_cliente
    instalar(op)
    a, ea = administradora(op)
    tok = op.ok(op.post("/api/plataforma/invitaciones", {"email": "beto@ejemplo.test"}))["token"]
    beto = nuevo(op)
    registrar(beto, "beto@ejemplo.test", invitacion=tok)
    inv = a.ok(a.post(f"/api/empresas/{ea}/miembros", {"email": "beto@ejemplo.test", "rol": "admin", "relacion": "asesor"}))
    r = beto.ok(beto.post("/api/invitaciones/aceptar", {"token": inv["invitacion"]["token"], "declaro": True}))
    assert r["empresa_id"] == ea
    m = db.fila(db.conectar(), "SELECT * FROM miembros WHERE usuario_id=? AND empresa_id=?", (uid_de("beto@ejemplo.test"), ea))
    assert m["rol"] == "admin" and m["relacion"] == "asesor" and m["declaracion_autorizacion_en"]
    assert "usuario.declaracion_autorizacion" in [x["accion"] for x in a.ok(a.get(f"/api/empresas/{ea}/actividad"))]
