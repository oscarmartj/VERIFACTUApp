"""Plataforma multiusuario: registro, invitaciones, contraseñas, aislamiento entre usuarios y copias.

Todo es local y SIMULADO (simulador de la AEAT, sin red). Datos ficticios: NIF de provincia 00.
"""
import datetime as dt
import re
import sqlite3

import pytest
from conftest import EMPRESA_DEMO, EMPRESA_REPLICA, Cliente, borrador_listo, configurar
from fastapi.testclient import TestClient
from test_03_flujo import emitir, enviar
from test_16_c_certificados import asignar, legitimar, sello_gestoria, subir_mio

from app import copias, db, diario, multiusuario
from app.nif import completar_cif

CLAVE = "clave-segura-123"
NIF_B = completar_cif("B", "0012345")
NIF_C = completar_cif("B", "0023456")
NIF_D = completar_cif("B", "0034567")


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    # Todas las peticiones de prueba llegan desde la misma «dirección»: el límite por IP se prueba aparte
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def otro(c: Cliente) -> Cliente:
    return Cliente(TestClient(c.tc.app))


def instalar(c: Cliente, registro_abierto=None, empresa=EMPRESA_DEMO) -> int:
    body = {"email": "operador@ejemplo.test", "nombre": "Operador", "clave": CLAVE, "empresa": empresa}
    if registro_abierto is not None:
        body["registro_abierto"] = registro_abierto
    d = c.ok(c.post("/api/instalar", body))
    c.csrf = d["csrf"]
    return d["empresas"][0]["id"]


def registrar(cli: Cliente, email, clave=CLAVE, nombre="Persona Ficticia", invitacion=None):
    body = {"email": email, "nombre": nombre, "clave": clave}
    if invitacion:
        body["invitacion"] = invitacion
    r = cli.post("/api/registro", body)
    if r.status_code == 200:
        cli.csrf = r.json()["csrf"]
    return r


def entrar(cli: Cliente, email, clave=CLAVE):
    r = cli.post("/api/sesion", {"email": email, "clave": clave})
    if r.status_code == 200:
        cli.csrf = r.json()["csrf"]
    return r


def borrar(cli: Cliente, url):
    return cli.tc.delete(url, headers=cli._h())


def abrir_registro(op: Cliente, abierto=True):
    assert op.ok(op.put("/api/plataforma/ajustes", {"registro_abierto": abierto}))["registro_abierto"] is abierto


def uid_de(email):
    return db.fila(db.conectar(), "SELECT id FROM usuarios WHERE email=?", (email,))["id"]


def nueva_empresa(cli: Cliente, nif, razon="EMPRESA FICTICIA S.L.", domicilio="C/ Inventada, 1 - 00000 Villaficticia"):
    return cli.post("/api/empresas", {"nif": nif, "razon_social": razon, "domicilio": domicilio})


# --- U1: registro abierto o cerrado ------------------------------------------------------------------------------
def test_registro_cerrado_por_defecto_y_apertura_por_el_operador(app_cliente):
    op = app_cliente
    instalar(op)
    assert op.ok(op.get("/api/estado"))["registro_abierto"] is False
    anon = otro(op)
    r = registrar(anon, "a@ejemplo.test")
    assert r.status_code == 403 and "cerrado" in r.json()["detail"]
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM usuarios")["n"] == 1
    abrir_registro(op)
    assert op.ok(op.get("/api/estado"))["registro_abierto"] is True
    yo = op.ok(registrar(anon, "a@ejemplo.test"))
    assert yo["usuario"]["superadmin"] == 0 and yo["usuario"]["cambiar_clave"] == 0 and yo["empresas"] == []
    assert anon.ok(anon.get("/api/yo"))["usuario"]["email"] == "a@ejemplo.test"
    # Una cuenta normal no tiene ningún privilegio de la plataforma
    assert anon.get("/api/plataforma").status_code == 403
    assert anon.put("/api/plataforma/ajustes", {"registro_abierto": False}).status_code == 403
    assert anon.put("/api/productor", {"nombre": "X", "nif": NIF_B}).status_code == 403
    assert anon.post("/api/simulador/modo", {"modo": "caido"}).status_code == 403
    assert anon.get("/api/plataforma/usuarios").status_code == 403
    assert anon.post("/api/plataforma/invitaciones", {}).status_code == 403
    abrir_registro(op, False)
    assert registrar(otro(op), "b@ejemplo.test").status_code == 403
    act = [a["accion"] for a in op.ok(op.get("/api/plataforma/actividad"))]
    assert "plataforma.ajuste" in act and "usuario.registro" in act


def test_instalacion_con_registro_abierto(app_cliente):
    op = app_cliente
    instalar(op, registro_abierto=True)
    assert op.ok(op.get("/api/estado"))["registro_abierto"] is True
    assert registrar(otro(op), "a@ejemplo.test").status_code == 200
    assert op.ok(op.get("/api/plataforma"))["ajustes"]["registro_abierto"] is True


def test_registro_errores_uniformes_y_validacion(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    assert registrar(otro(op), "a@ejemplo.test").status_code == 200
    # Correo ya registrado (también con otras mayúsculas), invitación inexistente o ya usada: misma respuesta
    r1 = registrar(otro(op), "A@Ejemplo.test")
    r2 = registrar(otro(op), "nuevo@ejemplo.test", invitacion="x" * 43)
    inv = op.ok(op.post("/api/plataforma/invitaciones", {}))
    assert registrar(otro(op), "usada@ejemplo.test", invitacion=inv["token"]).status_code == 200
    r3 = registrar(otro(op), "otra@ejemplo.test", invitacion=inv["token"])
    assert r1.status_code == r2.status_code == r3.status_code == 409
    assert r1.json() == r2.json() == r3.json() == {"detail": multiusuario.MENSAJE_REGISTRO_NO_POSIBLE}
    # Validaciones que no dependen del estado
    assert registrar(otro(op), "c@ejemplo.test", clave="corta").status_code == 422
    assert registrar(otro(op), "c@ejemplo.test", clave="x" * 257).status_code == 422
    assert registrar(otro(op), "no-es-un-correo").status_code == 422
    assert registrar(otro(op), "c@ejemplo.test", nombre="").status_code == 422
    assert otro(op).post("/api/registro", {"email": "c@ejemplo.test", "nombre": "C", "clave": CLAVE, "invitacion": 5}).status_code == 422
    # Una cuenta registrada nunca hereda el privilegio de la plataforma
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM usuarios WHERE superadmin=1")["n"] == 1


def test_limites_de_registro_por_ip_y_globales(app_cliente, monkeypatch):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 3)
    assert registrar(otro(op), "x@ejemplo.test", clave="corta").status_code == 422   # los intentos fallidos también cuentan
    assert registrar(otro(op), "a1@ejemplo.test").status_code == 200
    assert registrar(otro(op), "a2@ejemplo.test").status_code == 200
    r = registrar(otro(op), "a3@ejemplo.test")
    assert r.status_code == 429
    assert db.fila(db.conectar(), "SELECT 1 FROM usuarios WHERE email='a3@ejemplo.test'") is None
    # Límite global de altas por hora (independiente de la dirección)
    multiusuario.reiniciar_limitadores()
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 100)
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_GLOBAL, "maximo", 2)
    assert registrar(otro(op), "b1@ejemplo.test").status_code == 200
    assert registrar(otro(op), "b2@ejemplo.test").status_code == 200
    assert registrar(otro(op), "b3@ejemplo.test").status_code == 429
    # Y de intentos totales
    multiusuario.reiniciar_limitadores()
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_INTENTOS, "maximo", 1)
    assert registrar(otro(op), "c1@ejemplo.test").status_code == 200
    assert registrar(otro(op), "c2@ejemplo.test").status_code == 429
    # La consulta de enlaces de invitación también está limitada
    monkeypatch.setattr(multiusuario.LIMITE_CONSULTA_INVITACION, "maximo", 2)
    for _ in range(2):
        assert otro(op).post("/api/invitaciones/consulta", {"token": "y" * 43}).json() == {"valida": False}
    assert otro(op).post("/api/invitaciones/consulta", {"token": "y" * 43}).status_code == 429


def test_limitador_ventana_deslizante():
    reloj = [1000.0]
    lim = multiusuario.Limitador(2, ventana_s=60, reloj=lambda: reloj[0])
    assert lim.consumir("a") and lim.consumir("a") and not lim.consumir("a")
    assert lim.consumir("b")
    reloj[0] += 61
    assert lim.disponible("a") and lim.consumir("a")


# --- U1: invitaciones -------------------------------------------------------------------------------------------------
def test_invitacion_de_la_plataforma_un_solo_uso_y_caducidad(app_cliente):
    op = app_cliente
    instalar(op)
    inv = op.ok(op.post("/api/plataforma/invitaciones", {"email": "inv@ejemplo.test", "dias": 3}))
    assert inv["enlace"].endswith(inv["token"]) and inv["estado"] == "pendiente" and "token_hash" not in inv
    guardada = db.fila(db.conectar(), "SELECT token_hash FROM invitaciones WHERE id=?", (inv["id"],))["token_hash"]
    assert guardada != inv["token"] and inv["token"] not in guardada   # solo se guarda el resumen
    anon = otro(op)
    c = anon.ok(anon.post("/api/invitaciones/consulta", {"token": inv["token"]}))
    assert c == {"valida": True, "email": "inv@ejemplo.test", "empresa": None, "rol": None, "expira_en": inv["expira_en"],
                 "crea_cuenta": True}
    # Registro cerrado: con la invitación sí, pero solo con el correo invitado
    assert registrar(anon, "otro@ejemplo.test", invitacion=inv["token"]).status_code == 409
    yo = anon.ok(registrar(anon, "inv@ejemplo.test", invitacion=inv["token"]))
    assert yo["empresas"] == [] and yo["usuario"]["superadmin"] == 0
    assert db.fila(db.conectar(), "SELECT alta_origen FROM usuarios WHERE email='inv@ejemplo.test'")["alta_origen"] == "invitacion"
    # Un solo uso
    assert registrar(otro(op), "inv2@ejemplo.test", invitacion=inv["token"]).status_code == 409
    assert anon.ok(anon.post("/api/invitaciones/consulta", {"token": inv["token"]})) == {"valida": False}
    # Caducada
    inv2 = op.ok(op.post("/api/plataforma/invitaciones", {}))
    con = db.conectar()
    con.execute("UPDATE invitaciones SET expira_en=? WHERE id=?",
                ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).replace(microsecond=0).isoformat(), inv2["id"]))
    assert registrar(otro(op), "tarde@ejemplo.test", invitacion=inv2["token"]).status_code == 409
    # Anulada
    inv3 = op.ok(op.post("/api/plataforma/invitaciones", {}))
    assert op.ok(borrar(op, f"/api/invitaciones/{inv3['id']}"))["estado"] == "revocada"
    assert borrar(op, f"/api/invitaciones/{inv3['id']}").status_code == 409
    assert registrar(otro(op), "anulada@ejemplo.test", invitacion=inv3["token"]).status_code == 409
    estados = {i["id"]: i["estado"] for i in op.ok(op.get("/api/plataforma/invitaciones"))}
    assert estados == {inv["id"]: "usada", inv2["id"]: "caducada", inv3["id"]: "revocada"}
    assert all("token" not in i and "token_hash" not in i for i in op.ok(op.get("/api/plataforma/invitaciones")))
    # Sin correo: vale para cualquiera (una vez); validez fuera de rango
    inv4 = op.ok(op.post("/api/plataforma/invitaciones", {}))
    assert registrar(otro(op), "libre@ejemplo.test", invitacion=inv4["token"]).status_code == 200
    assert op.post("/api/plataforma/invitaciones", {"dias": 31}).status_code == 422
    assert op.post("/api/plataforma/invitaciones", {"dias": 0}).status_code == 422
    assert op.post("/api/plataforma/invitaciones", {"email": "malo"}).status_code == 422
    # Un usuario normal no crea ni anula invitaciones de la plataforma (ni sabe si existen)
    inv5 = op.ok(op.post("/api/plataforma/invitaciones", {}))
    assert borrar(anon, f"/api/invitaciones/{inv5['id']}").status_code == 404
    assert borrar(anon, "/api/invitaciones/99999").status_code == 404


def test_invitacion_de_empresa_alcance_y_rol(app_cliente):
    op = app_cliente
    e1 = instalar(op)
    abrir_registro(op)
    # El administrador de E1 invita a un correo sin cuenta (alta en «miembros» sin contraseña = invitación)
    r = op.ok(op.post(f"/api/empresas/{e1}/miembros", {"email": "emi@ejemplo.test", "rol": "emisor", "relacion": "empleado_autorizado"}))
    assert r["accion"] == "invitacion" and r["invitacion"]["rol"] == "emisor" and r["invitacion"]["empresa_id"] == e1
    abrir_registro(op, False)   # aunque el registro esté cerrado, la invitación de una empresa permite registrarse
    emi = otro(op)
    yo = emi.ok(registrar(emi, "emi@ejemplo.test", invitacion=r["invitacion"]["token"]))
    assert [(x["id"], x["rol"]) for x in yo["empresas"]] == [(e1, "emisor")]
    assert emi.get(f"/api/empresas/{e1}").status_code == 200
    # Un emisor no invita ni ve las invitaciones
    assert emi.post(f"/api/empresas/{e1}/invitaciones", {"email": "z@ejemplo.test", "rol": "admin"}).status_code == 404
    assert emi.get(f"/api/empresas/{e1}/invitaciones").status_code == 404
    assert emi.post(f"/api/empresas/{e1}/miembros", {"email": "z@ejemplo.test", "rol": "admin"}).status_code == 404
    # Otro usuario, administrador de su propia empresa E2, no puede invitar a E1 ni anular sus invitaciones
    abrir_registro(op)
    b = otro(op)
    registrar(b, "b@ejemplo.test")
    e2 = b.ok(nueva_empresa(b, NIF_B))["id"]
    assert b.post(f"/api/empresas/{e1}/invitaciones", {"email": "z@ejemplo.test", "rol": "admin"}).status_code == 404
    pend = op.ok(op.post(f"/api/empresas/{e1}/invitaciones", {"email": "z@ejemplo.test", "rol": "lector"}))
    assert borrar(b, f"/api/invitaciones/{pend['id']}").status_code == 404
    assert b.ok(b.post(f"/api/empresas/{e2}/invitaciones", {"email": "z2@ejemplo.test", "rol": "lector"}))["empresa_id"] == e2
    assert borrar(op, f"/api/invitaciones/{b.ok(b.get(f'/api/empresas/{e2}/invitaciones'))[0]['id']}").status_code == 404
    assert op.post(f"/api/empresas/{e1}/invitaciones", {"email": "z@ejemplo.test", "rol": "jefe"}).status_code == 422
    assert op.post(f"/api/empresas/{e1}/invitaciones", {"rol": "lector"}).status_code == 422
    # Correo de una cuenta existente que no es miembro: también invitación (misma forma de respuesta; no la mete sin aceptar)
    r_existe = op.ok(op.post(f"/api/empresas/{e1}/miembros", {"email": "b@ejemplo.test", "rol": "lector"}))
    r_no = op.ok(op.post(f"/api/empresas/{e1}/miembros", {"email": "nadie@ejemplo.test", "rol": "lector"}))
    assert r_existe.keys() == r_no.keys() and r_existe["invitacion"].keys() == r_no["invitacion"].keys()
    assert b.get(f"/api/empresas/{e1}").status_code == 404
    # Otra cuenta no puede aceptar una invitación dirigida a otro correo, y la invitación sigue pendiente
    assert emi.post("/api/invitaciones/aceptar", {"token": r_existe["invitacion"]["token"]}).status_code == 409
    acept = b.ok(b.post("/api/invitaciones/aceptar", {"token": r_existe["invitacion"]["token"]}))
    assert acept["empresa_id"] == e1 and {x["id"] for x in acept["empresas"]} == {e1, e2}
    assert b.ok(b.get(f"/api/empresas/{e1}"))["rol"] == "lector"
    assert b.post("/api/invitaciones/aceptar", {"token": r_existe["invitacion"]["token"]}).status_code == 409
    # Una invitación de la plataforma no sirve para «aceptar» (no es de ninguna empresa)
    inv_p = op.ok(op.post("/api/plataforma/invitaciones", {"email": "b@ejemplo.test"}))
    assert b.post("/api/invitaciones/aceptar", {"token": inv_p["token"]}).status_code == 409
    # Cambiar el rol de quien ya es miembro sigue siendo directo
    assert op.ok(op.post(f"/api/empresas/{e1}/miembros", {"email": "b@ejemplo.test", "rol": "emisor"}))["accion"] == "actualizado"
    assert b.ok(b.get(f"/api/empresas/{e1}"))["rol"] == "emisor"
    lista = {i["email"]: i["estado"] for i in op.ok(op.get(f"/api/empresas/{e1}/invitaciones"))}
    assert lista["emi@ejemplo.test"] == "usada" and lista["z@ejemplo.test"] == "pendiente"


def test_alta_directa_con_clave_solo_para_el_operador(app_cliente):
    op = app_cliente
    e1 = instalar(op)
    abrir_registro(op)
    # El operador (que además administra E1) puede crear la cuenta: contraseña temporal
    r = op.ok(op.post(f"/api/empresas/{e1}/miembros", {"email": "dir@ejemplo.test", "nombre": "D", "clave": "temporal-123", "rol": "emisor"}))
    assert r["accion"] == "alta"
    d = otro(op)
    assert entrar(d, "dir@ejemplo.test", "temporal-123").json()["usuario"]["cambiar_clave"] == 1
    assert d.get(f"/api/empresas/{e1}").status_code == 403
    # Un administrador que no es operador no crea cuentas con contraseña: se convierte en invitación
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(nueva_empresa(a, NIF_B))["id"]
    r = a.ok(a.post(f"/api/empresas/{ea}/miembros", {"email": "nuevo@ejemplo.test", "nombre": "N", "clave": "clave-elegida-1", "rol": "admin"}))
    assert r["accion"] == "invitacion"
    assert db.fila(db.conectar(), "SELECT 1 FROM usuarios WHERE email='nuevo@ejemplo.test'") is None
    r = a.ok(a.post(f"/api/empresas/{ea}/miembros", {"email": "dir@ejemplo.test", "clave": "clave-elegida-1", "rol": "admin"}))
    assert r["accion"] == "invitacion"
    assert db.fila(db.conectar(), "SELECT 1 FROM miembros WHERE empresa_id=? AND usuario_id=?", (ea, uid_de("dir@ejemplo.test"))) is None


# --- U2: cualquier usuario da de alta empresas; NIF único ------------------------------------------------------------
def test_alta_de_empresas_por_cualquier_usuario_y_nif_unico(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    emp = a.ok(nueva_empresa(a, NIF_B, razon="ALFA FICTICIA S.L."))
    assert emp["rol"] == "admin" and emp["nif"] == NIF_B
    assert [x["id"] for x in a.ok(a.get("/api/yo"))["empresas"]] == [emp["id"]]
    b = otro(op)
    registrar(b, "b@ejemplo.test")
    for nif in (NIF_B, f"{NIF_B[0]}-{NIF_B[1:]}".lower(), EMPRESA_DEMO["nif"]):
        r = nueva_empresa(b, nif, razon="OTRA RAZÓN S.L.")
        assert r.status_code == 409 and r.json() == {"detail": "Esa empresa ya está dada de alta en la aplicación: pida acceso a uno de sus administradores"}
    assert b.ok(b.get("/api/yo"))["empresas"] == []
    assert nueva_empresa(b, "B12345678").status_code == 422
    assert nueva_empresa(b, NIF_C, domicilio="con\x01control").status_code == 422
    # Los requisitos por usuario y empresa siguen exigiéndose (ámbito y declaración propios de cada miembro)
    eb = b.ok(nueva_empresa(b, NIF_C))["id"]
    req = {x["clave"]: x["cumplido"] for x in b.ok(b.get(f"/api/empresas/{eb}/produccion"))["requisitos_pruebas"]}
    assert req["ambito"] is False and req["autorizacion_usuario"] is False


# --- U3: contraseñas y cuentas -------------------------------------------------------------------------------------------
def test_cambio_de_clave_cierra_las_demas_sesiones(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    a1 = otro(op)
    registrar(a1, "a@ejemplo.test")
    a2 = otro(op)
    entrar(a2, "a@ejemplo.test")
    assert a2.get("/api/yo").status_code == 200
    assert a1.put("/api/yo/clave", {"clave_actual": "incorrecta-000", "clave_nueva": "nueva-clave-456"}).status_code == 403
    assert a1.put("/api/yo/clave", {"clave_actual": CLAVE, "clave_nueva": CLAVE}).status_code == 422
    assert a1.put("/api/yo/clave", {"clave_actual": CLAVE, "clave_nueva": "corta"}).status_code == 422
    assert a1.tc.put("/api/yo/clave", json={"clave_actual": CLAVE, "clave_nueva": "nueva-clave-456"}).status_code == 403   # sin CSRF
    a1.ok(a1.put("/api/yo/clave", {"clave_actual": CLAVE, "clave_nueva": "nueva-clave-456"}))
    assert a1.get("/api/yo").status_code == 200       # la sesión actual sigue
    assert a2.get("/api/yo").status_code == 401       # las demás, no
    assert entrar(otro(op), "a@ejemplo.test").status_code == 401
    assert entrar(otro(op), "a@ejemplo.test", "nueva-clave-456").status_code == 200
    # Fallos repetidos de la contraseña actual cuentan para el bloqueo de la cuenta
    for _ in range(5):
        assert a1.put("/api/yo/clave", {"clave_actual": "incorrecta-000", "clave_nueva": "otra-clave-789"}).status_code == 403
    assert entrar(otro(op), "a@ejemplo.test", "nueva-clave-456").status_code == 401
    assert a1.put("/api/yo/clave", {"clave_actual": "nueva-clave-456", "clave_nueva": "otra-clave-789"}).status_code == 403
    # Nada de la contraseña en la actividad ni en el diario (solo el resumen scrypt)
    texto = diario.ruta().read_text(encoding="utf-8") + str(db.filas(db.conectar(), "SELECT detalle FROM actividad"))
    assert "nueva-clave-456" not in texto and CLAVE not in texto


def test_clave_temporal_obliga_a_cambiarla(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(nueva_empresa(a, NIF_B))["id"]
    uid = uid_de("a@ejemplo.test")
    assert a.post(f"/api/plataforma/usuarios/{uid}/clave-temporal", {"clave": "temporal-0001"}).status_code == 403
    assert op.post(f"/api/plataforma/usuarios/{uid}/clave-temporal", {"clave": "corta"}).status_code == 422
    assert op.post("/api/plataforma/usuarios/99999/clave-temporal", {"clave": "temporal-0001"}).status_code == 404
    assert op.post(f"/api/plataforma/usuarios/{uid_de('operador@ejemplo.test')}/clave-temporal", {"clave": "temporal-0001"}).status_code == 409
    assert op.ok(op.post(f"/api/plataforma/usuarios/{uid}/clave-temporal", {"clave": "temporal-0001"})) == {"ok": True}
    assert a.get("/api/yo").status_code == 401          # sesiones cerradas
    assert entrar(a, "a@ejemplo.test").status_code == 401
    d = a.ok(entrar(a, "a@ejemplo.test", "temporal-0001"))
    assert d["usuario"]["cambiar_clave"] == 1
    assert a.ok(a.get("/api/yo"))["usuario"]["cambiar_clave"] == 1
    # Hasta cambiarla, nada más
    for r in (a.get(f"/api/empresas/{ea}"), a.get(f"/api/empresas/{ea}/facturas"), nueva_empresa(a, NIF_C),
              a.post("/api/invitaciones/aceptar", {"token": "x" * 43})):
        assert r.status_code == 403 and "cambiar su contraseña" in r.json()["detail"]
    a.ok(a.put("/api/yo/clave", {"clave_actual": "temporal-0001", "clave_nueva": "definitiva-0002"}))
    assert a.ok(a.get("/api/yo"))["usuario"]["cambiar_clave"] == 0
    assert a.get(f"/api/empresas/{ea}").status_code == 200
    texto = diario.ruta().read_text(encoding="utf-8") + str(db.filas(db.conectar(), "SELECT detalle FROM actividad"))
    assert "temporal-0001" not in texto and "definitiva-0002" not in texto


def test_usuario_desactivado(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    a1, a2 = otro(op), otro(op)
    registrar(a1, "a@ejemplo.test")
    entrar(a2, "a@ejemplo.test")
    uid = uid_de("a@ejemplo.test")
    assert a1.put(f"/api/plataforma/usuarios/{uid}", {"activo": False}).status_code == 403
    assert op.put(f"/api/plataforma/usuarios/{uid}", {"activo": "no"}).status_code == 422
    u = op.ok(op.put(f"/api/plataforma/usuarios/{uid}", {"activo": False}))
    assert u["activo"] == 0
    assert a1.get("/api/yo").status_code == 401 and a2.get("/api/yo").status_code == 401
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM sesiones WHERE usuario_id=?", (uid,))["n"] == 0
    r = entrar(otro(op), "a@ejemplo.test")
    assert r.status_code == 401 and r.json() == entrar(otro(op), "a@ejemplo.test", "mal-clave-000").json()
    # No puede desactivarse a sí mismo el operador (ni quedarse la plataforma sin operador activo)
    assert op.put(f"/api/plataforma/usuarios/{uid_de('operador@ejemplo.test')}", {"activo": False}).status_code == 409
    assert op.put("/api/plataforma/usuarios/99999", {"activo": False}).status_code == 404
    op.ok(op.put(f"/api/plataforma/usuarios/{uid}", {"activo": True}))
    assert entrar(otro(op), "a@ejemplo.test").status_code == 200


# --- U4: panel del operador; el operador no ve datos de empresas ajenas -----------------------------------------------
def test_panel_de_la_plataforma(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    a = otro(op)
    registrar(a, "a@ejemplo.test", nombre="Ana Ficticia")
    a.ok(nueva_empresa(a, NIF_B))
    a.ok(nueva_empresa(a, NIF_C))
    p = op.ok(op.get("/api/plataforma"))
    assert p["totales"] == {"usuarios": 2, "usuarios_activos": 2, "empresas": 3}
    assert p["productor"]["numero_instalacion"] and p["simulador"] == {"empresas": []}   # (G3, ISO-5) modos por empresa
    usuarios = {u["email"]: u for u in op.ok(op.get("/api/plataforma/usuarios"))}
    assert usuarios["a@ejemplo.test"]["empresas"] == 2 and usuarios["a@ejemplo.test"]["alta_origen"] == "registro"
    assert usuarios["operador@ejemplo.test"]["superadmin"] == 1 and usuarios["operador@ejemplo.test"]["alta_origen"] == "instalacion"
    assert set(usuarios["a@ejemplo.test"]) == {"id", "email", "nombre", "activo", "superadmin", "cambiar_clave", "creado_en",
                                               "alta_origen", "empresas", "ultima_sesion", "bloqueado"}
    assert [u["email"] for u in op.ok(op.get("/api/plataforma/usuarios?q=Ana"))] == ["a@ejemplo.test"]
    # Productor y simulador, como antes, solo el operador
    assert op.ok(op.put("/api/productor", {"nombre": "Productor Ficticio", "nif": NIF_D, "domicilio": "x"}))["nif"] == NIF_D
    # (G3, ISO-5) el modo del simulador es de cada empresa: el operador lo fija para cualquiera, indicando su NIF
    assert op.ok(op.post("/api/simulador/modo", {"nif": NIF_C, "modo": "caido", "veces": 1}))["modo"] == "caido"
    sim = op.ok(op.get("/api/plataforma"))["simulador"]
    assert [(x["nif"], x["modo"]) for x in sim["empresas"]] == [(NIF_C, "caido")]
    op.ok(op.post("/api/simulador/modo", {"nif": NIF_C, "modo": "normal"}))
    # La actividad de la plataforma no incluye la de las empresas
    assert all(x["accion"] != "empresa.creada" for x in op.ok(op.get("/api/plataforma/actividad")))


def _cert_p12(nif):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SELLO FICTICIO"),
                        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), f"VATES-{nif}")])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key()).serial_number(7)
            .not_valid_before(ahora - dt.timedelta(days=1)).not_valid_after(ahora + dt.timedelta(days=30)).sign(k, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"x", k, cert, None, BestAvailableEncryption(b"clave-cert"))


# Rutas con identificadores que el barrido debe encontrar (si falta alguna, se ha renombrado o el barrido no la prueba).
# Todas, y cualquier otra ruta /api con identificadores, deben responder exactamente 404 a quien no es miembro.
RUTAS_BASE = {
    "/api/empresas/{eid}", "/api/empresas/{eid}/ambito/confirmar", "/api/empresas/{eid}/autorizacion-usuario",
    "/api/empresas/{eid}/miembros", "/api/empresas/{eid}/auditoria", "/api/empresas/{eid}/series", "/api/series/{sid}/confirmar",
    "/api/empresas/{eid}/documentos", "/api/documentos/{did}", "/api/documentos/{did}/paginas/{n}.png",
    "/api/documentos/{did}/original", "/api/documentos/{did}/clasificacion", "/api/documentos/{did}/bloqueo",
    "/api/documentos/{did}/borrador", "/api/borradores/{bid}", "/api/borradores/{bid}/confirmaciones",
    "/api/borradores/{bid}/vista-previa.pdf", "/api/borradores/{bid}/vista-previa/{n}.png", "/api/borradores/{bid}/emitir",
    "/api/borradores/{bid}/descartar", "/api/empresas/{eid}/facturas", "/api/facturas/{fid}", "/api/facturas/{fid}/pdf",
    "/api/facturas/{fid}/pdf/{n}.png", "/api/registros/{rid}/xml", "/api/empresas/{eid}/facturas-lote.zip",
    "/api/facturas/{fid}/rectificativa", "/api/facturas/{fid}/anular", "/api/registros/{rid}/subsanar",
    "/api/registros/{rid}/reintentar", "/api/empresas/{eid}/remision", "/api/empresas/{eid}/certificados",
    "/api/empresas/{eid}/produccion", "/api/empresas/{eid}/produccion/autorizacion", "/api/empresas/{eid}/legitimacion",
    "/api/empresas/{eid}/actividad", "/api/invitaciones/{iid}", "/api/empresas/{eid}/invitaciones",
    "/api/empresas/{eid}/miembros/{uid}",
    # «Mis certificados» de otra persona ({cid}: id de «Mis certificados»; {aid}: su asignación), certificado de la empresa
    # ({cert_id}), legitimaciones ({lid}) y avance del contador de una serie ({sid})
    "/api/yo/certificados/{cid}", "/api/yo/certificados/{cid}/usos", "/api/yo/certificados/{cid}/asignaciones/{aid}",
    "/api/empresas/{eid}/certificados/{cert_id}/activar", "/api/empresas/{eid}/certificados/asignaciones/{aid}",
    "/api/empresas/{eid}/legitimaciones/{lid}", "/api/series/{sid}/avance", "/api/series/{sid}/avanzar",
    # Consulta a la AEAT y conciliación, selección y asignación de certificados, legitimaciones de la empresa
    "/api/empresas/{eid}/consulta", "/api/empresas/{eid}/consulta/conciliar", "/api/empresas/{eid}/certificados/asignar",
    "/api/empresas/{eid}/certificados/seleccion", "/api/empresas/{eid}/legitimaciones",
}

# Excepciones explícitas al 404 estricto: {(método, ruta): estado que recibe quien no es miembro}, cada una con su motivo.
# Hoy no hay ninguna: toda ruta con identificadores comprueba el acceso antes de validar el cuerpo y responde 404 a quien
# no es miembro (no revela si el objeto existe). Las del panel de la plataforma responden 403 a quien no es operador.
EXCEPCIONES_404: dict[tuple[str, str], int] = {}


def _esperado(metodo: str, ruta: str) -> int:
    if ruta.startswith("/api/plataforma/"):
        return 403
    return EXCEPCIONES_404.get((metodo, ruta), 404)


def huella_datos() -> dict:
    """Contenido de todas las tablas salvo las sesiones (guardan el último acceso): un intruso no debe cambiar nada."""
    con = db.conectar()
    try:
        tablas = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: sorted(repr(tuple(r)) for r in con.execute(f"SELECT * FROM {t}")) for t in tablas if t != "sesiones"}
    finally:
        con.close()


def fallos_barrido(res: dict) -> list[tuple[str, str, int]]:
    """Rutas del barrido que no responden lo esperado a quien no es miembro: 404 (403 en el panel de la plataforma)."""
    return [(m, ruta, est) for (m, ruta), est in sorted(res.items()) if est != _esperado(m, ruta)]


def _rutas_api(app):
    pend, out = list(app.routes), []
    while pend:
        r = pend.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            pend.extend(sub.routes)
            continue
        p = getattr(r, "path", None)
        if p and p.startswith("/api/") and getattr(r, "methods", None):
            out.append((p, set(r.methods) - {"HEAD", "OPTIONS"}))
    return out


def _barrer(cli: Cliente, ids: dict, operador: bool = False) -> dict:
    """Llama a todas las rutas /api con identificadores (con los de otra persona). Devuelve {(método, ruta): estado}.

    Falla si alguna ruta con identificadores queda sin probar porque `ids` no trae alguno de sus parámetros: una ruta nueva
    no puede escapar del barrido. Las del panel de la plataforma se prueban con quien no es operador (403); para el operador
    son su función (no se llaman)."""
    res, omitidas = {}, []
    for ruta, metodos in _rutas_api(cli.tc.app):
        params = re.findall(r"{(\w+)}", ruta)
        if not params or (operador and ruta.startswith("/api/plataforma/")):
            continue
        if any(p not in ids for p in params):
            omitidas.append(ruta)
            continue
        url = ruta
        for p in params:
            url = url.replace("{" + p + "}", str(ids[p]))
        url += f"?ids={ids['fid']}&entorno=simulador"
        h = {"X-CSRF-Token": cli.csrf}
        for m in sorted(metodos):
            if m == "GET":
                r = cli.tc.get(url)
            elif m == "POST" and ruta.endswith("/documentos"):
                r = cli.tc.post(url, files=[("archivos", ("x.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))], headers=h)
            elif m == "POST" and ruta.endswith("/certificados"):
                r = cli.tc.post(url, files={"archivo": ("c.p12", b"x", "application/x-pkcs12")},
                                data={"clave": "x", "entorno": "pruebas"}, headers=h)
            else:
                r = cli.tc.request(m, url, json={"motivo": "x", "confirmo": True, "email": "z@ejemplo.test", "rol": "admin",
                                                 "cambios": [], "claves": [], "entorno": "simulador", "clave_idempotencia": "k",
                                                 "tipo": "R1", "clasificacion": "preparatorio", "texto_confirmacion": "x",
                                                 "metodo": "colaborador_social", "patron": "{n}", "relacion": "administrador",
                                                 "declaro": True, "razon_social": "X", "token": "x" * 43}, headers=h)
            res[(m, ruta)] = r.status_code
    assert not omitidas, f"Rutas con identificadores sin probar en el barrido de aislamiento: {omitidas}"
    return res


def escenario_aislamiento(op: Cliente) -> dict:
    """Instala con el operador (con su propia empresa, EMPRESA_DEMO) y prepara la empresa de la usuaria A con un objeto de
    cada clase, y la usuaria B con su propia empresa. Devuelve los clientes y los identificadores de A."""
    instalar(op)                           # el operador tiene su propia empresa (EMPRESA_DEMO)
    abrir_registro(op)
    # La usuaria A da de alta su empresa y trabaja con ella: documento, borradores, factura, registro, certificado…
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(a.post("/api/empresas", EMPRESA_REPLICA))["id"]
    configurar(a, ea)
    did, bid, _ = borrador_listo(a, ea)
    fid = a.ok(emitir(a, bid))["factura_id"]
    rid = a.ok(a.get(f"/api/facturas/{fid}"))["registros"][0]["id"]
    sid = a.ok(a.get(f"/api/empresas/{ea}/series"))[0]["id"]
    _, bid2, _ = borrador_listo(a, ea, nombre="demo_proforma.pdf", confirmar_todo=False)
    r = a.tc.post(f"/api/empresas/{ea}/certificados", files={"archivo": ("c.p12", _cert_p12(EMPRESA_REPLICA["nif"]), "application/x-pkcs12")},
                  data={"clave": "clave-cert", "entorno": "pruebas"}, headers={"X-CSRF-Token": a.csrf})
    cert_id = a.ok(r)["id"]                                             # certificado de la empresa
    mio = subir_mio(a, sello_gestoria())["id"]                          # «Mis certificados» de A…
    aid = a.ok(asignar(a, ea, mio, "simulador"))["asignacion_id"]       # …asignado a su empresa
    lid = legitimar(a, ea)                                              # legitimación del titular (gestoría)
    iid = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "z@ejemplo.test", "rol": "lector"}))["id"]
    ids = {"eid": ea, "did": did, "bid": bid2, "fid": fid, "rid": rid, "sid": sid, "iid": iid, "cid": mio, "cert_id": cert_id,
           "aid": aid, "lid": lid, "n": 1, "uid": uid_de("a@ejemplo.test")}
    # El usuario B (con su propia empresa) y el operador de la plataforma (sin ser miembro de la empresa de A)
    b = otro(op)
    registrar(b, "b@ejemplo.test")
    eb = b.ok(nueva_empresa(b, NIF_B))["id"]
    return {"a": a, "b": b, "ea": ea, "eb": eb, "ids": ids, "bid": bid, "fid": fid, "bid2": bid2, "iid": iid, "lid": lid,
            "aid": aid, "mio": mio, "cert_id": cert_id}


def test_aislamiento_entre_usuarios_y_operador_sin_membresia(app_cliente):
    op = app_cliente
    esc = escenario_aislamiento(op)
    a, b, ea, eb, ids = esc["a"], esc["b"], esc["ea"], esc["eb"], esc["ids"]
    bid, fid, bid2, iid, lid, aid, mio, cert_id = (esc[k] for k in ("bid", "fid", "bid2", "iid", "lid", "aid", "mio", "cert_id"))
    antes = db.fila(db.conectar(), "SELECT (SELECT COUNT(*) FROM actividad) a, (SELECT COUNT(*) FROM facturas) f, "
                                   "(SELECT COUNT(*) FROM documentos) d, (SELECT COUNT(*) FROM registros) r")
    huella = huella_datos()
    for intruso in (b, op):
        res = _barrer(intruso, ids, operador=intruso is op)
        assert len(res) >= 55 and {r for _, r in res} >= RUTAS_BASE, RUTAS_BASE - {r for _, r in res}
        # 404 estricto en toda ruta con identificadores (403 en el panel de la plataforma), no solo «nunca éxito»: una ruta
        # que valida el cuerpo antes de comprobar el acceso (422) también se detecta
        assert not fallos_barrido(res), (intruso is op, fallos_barrido(res))
        # El primer borrador (ya emitido) y la vista previa del documento, también con los identificadores directos
        assert intruso.get(f"/api/borradores/{bid}").status_code == 404
        assert intruso.get(f"/api/empresas/{ea}/facturas-lote.zip?ids={fid}").status_code == 404
        assert ea not in [x["id"] for x in intruso.ok(intruso.get("/api/yo"))["empresas"]]
    # B, administrador de SU empresa, con los identificadores de A en sus rutas (el objeto debe ser de esa empresa)
    for m, url in (("DELETE", f"/api/empresas/{eb}/legitimaciones/{lid}"), ("POST", f"/api/empresas/{eb}/certificados/{cert_id}/activar"),
                   ("DELETE", f"/api/empresas/{eb}/certificados/asignaciones/{aid}"),
                   ("DELETE", f"/api/yo/certificados/{mio}/asignaciones/{aid}"), ("GET", f"/api/yo/certificados/{mio}/usos")):
        assert b.tc.request(m, url, headers={"X-CSRF-Token": b.csrf}).status_code == 404, (m, url)
    # Nada ha cambiado en la base de datos (salvo las sesiones): ni en la empresa de A ni en ninguna otra parte. Detecta
    # también una ruta que escribe sin comprobar el acceso aunque después responda 404 (p. ej. al devolver el objeto)
    despues_h = huella_datos()
    assert [t for t in huella if huella[t] != despues_h.get(t)] == []
    # Nada ha cambiado en la empresa de A: ni actividad de los intrusos ni objetos nuevos
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM actividad WHERE empresa_id=? AND usuario_id IN (?,?)",
                   (ea, uid_de("b@ejemplo.test"), uid_de("operador@ejemplo.test")))["n"] == 0
    despues = db.fila(con, "SELECT (SELECT COUNT(*) FROM facturas) f, (SELECT COUNT(*) FROM documentos) d, (SELECT COUNT(*) FROM registros) r")
    assert (despues["f"], despues["d"], despues["r"]) == (antes["f"], antes["d"], antes["r"])
    assert db.fila(con, "SELECT estado FROM facturas WHERE id=?", (fid,))["estado"] == "emitida"
    assert db.fila(con, "SELECT estado FROM borradores WHERE id=?", (bid2,))["estado"] != "descartado"
    assert db.fila(con, "SELECT revocada_en FROM invitaciones WHERE id=?", (iid,))["revocada_en"] is None
    assert db.fila(con, "SELECT rol FROM miembros WHERE empresa_id=? AND usuario_id=?", (ea, uid_de("a@ejemplo.test")))["rol"] == "admin"
    assert db.fila(con, "SELECT revocada_en FROM legitimaciones WHERE id=?", (lid,))["revocada_en"] is None
    assert db.fila(con, "SELECT activo FROM certificado_asignaciones WHERE id=?", (aid,))["activo"] == 1
    assert db.fila(con, "SELECT eliminado_en FROM certificados_usuario WHERE id=?", (mio,))["eliminado_en"] is None
    assert db.fila(con, "SELECT activo FROM certificados WHERE id=?", (cert_id,))["activo"] == 1
    # A sigue accediendo a todo lo suyo
    assert a.get(f"/api/facturas/{fid}/pdf").status_code == 200 and a.get(f"/api/empresas/{ea}/certificados").status_code == 200
    # El operador ve que A existe y cuántas empresas tiene, pero no cuáles ni su contenido
    u = next(x for x in op.ok(op.get("/api/plataforma/usuarios")) if x["email"] == "a@ejemplo.test")
    assert u["empresas"] == 1 and EMPRESA_REPLICA["razon_social"] not in str(op.ok(op.get("/api/plataforma")))


# --- Copias y diario ----------------------------------------------------------------------------------------------------
def test_copia_y_restauracion_de_cuentas_ajustes_e_invitaciones(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    u1, u3 = otro(op), otro(op)
    registrar(u1, "u1@ejemplo.test")
    registrar(u3, "u3@ejemplo.test")
    inv1 = op.ok(op.post("/api/plataforma/invitaciones", {}))
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    # Después de la copia: se cierra el registro, se usa la invitación, u1 cambia su contraseña, u3 se desactiva,
    # se crea otra invitación pendiente y una cuenta nueva por invitación de empresa
    abrir_registro(op, False)
    u2 = otro(op)
    assert registrar(u2, "u2@ejemplo.test", clave="clave-u2-0001", invitacion=inv1["token"]).status_code == 200
    u1.ok(u1.put("/api/yo/clave", {"clave_actual": CLAVE, "clave_nueva": "clave-u1-nueva"}))
    op.ok(op.put(f"/api/plataforma/usuarios/{uid_de('u3@ejemplo.test')}", {"activo": False}))
    inv2 = op.ok(op.post("/api/plataforma/invitaciones", {}))
    e1 = op.ok(op.get("/api/yo"))["empresas"][0]["id"]
    invm = op.ok(op.post(f"/api/empresas/{e1}/miembros", {"email": "m@ejemplo.test", "rol": "lector"}))["invitacion"]
    m = otro(op)
    registrar(m, "m@ejemplo.test", invitacion=invm["token"])
    r = copias.restaurar(copia)
    # (la invitación de la copia se usó después: figura como usada, no anulada)
    assert r["cuentas_recuperadas"] >= 4 and r["ajustes_recuperados"] == 1 and r["invitaciones_usadas"] == 1, r
    assert r["usuarios_provisionales"] == [] and r["cuentas_descartadas"] == []
    # El ajuste posterior a la copia se conserva (no se reabre el registro)
    assert op.ok(op.get("/api/estado"))["registro_abierto"] is False
    # Cuenta creada después de la copia: entra con su contraseña; miembro por invitación: conserva la empresa
    assert entrar(otro(op), "u2@ejemplo.test", "clave-u2-0001").status_code == 200
    mm = otro(op)
    assert [x["id"] for x in mm.ok(entrar(mm, "m@ejemplo.test"))["empresas"]] == [e1]
    # Contraseña cambiada después de la copia: vale la nueva, no la antigua; sus sesiones anteriores ya no valen
    assert entrar(otro(op), "u1@ejemplo.test").status_code == 401
    assert entrar(otro(op), "u1@ejemplo.test", "clave-u1-nueva").status_code == 200
    assert u1.get("/api/yo").status_code == 401
    # Cuenta desactivada después de la copia: sigue desactivada
    assert entrar(otro(op), "u3@ejemplo.test").status_code == 401
    # Ninguna invitación anterior vuelve a valer: ni la usada tras la copia ni las pendientes
    for tok in (inv1["token"], inv2["token"], invm["token"]):
        assert registrar(otro(op), "nuevo@ejemplo.test", invitacion=tok).status_code == 409
    # El operador conserva su sesión y su privilegio
    assert op.ok(op.get("/api/plataforma"))["totales"]["usuarios"] == 5


def test_diario_no_concede_privilegio_de_plataforma(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    registrar(otro(op), "a@ejemplo.test")
    con = db.conectar()
    copia = copias.crear_copia(con)
    # Una línea del diario manipulada que diera el privilegio de la plataforma a una cuenta no se aplica
    snap = db.fila(con, f"SELECT {', '.join(multiusuario.CAMPOS_USUARIO)} FROM usuarios WHERE email='a@ejemplo.test'")
    diario.anotar("usuario", dict(snap, superadmin=1, version=snap["version"] + 5))
    con.close()
    copias.restaurar(copia)
    assert db.fila(db.conectar(), "SELECT superadmin FROM usuarios WHERE email='a@ejemplo.test'")["superadmin"] == 0


def test_conciliacion_anota_cuentas_y_ajustes(app_cliente):
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    registrar(otro(op), "a@ejemplo.test")
    tipos = [e["tipo"] for e in diario.leer()]
    assert tipos.count("usuario") == 2 and tipos.count("ajuste_plataforma") >= 2
    # Se pierden las anotaciones de cuentas (p. ej. caída entre la confirmación y el diario) y una cuenta cambia sin anotarse
    lineas = diario.ruta().read_text(encoding="utf-8").splitlines()
    diario.ruta().write_text("\n".join(x for x in lineas if '"tipo": "usuario"' not in x) + "\n", encoding="utf-8")
    con = db.conectar()
    con.execute("UPDATE usuarios SET version=version+1 WHERE email='a@ejemplo.test'")
    assert copias.reconciliar_diario(con) >= 2
    ult = {e["datos"]["email"]: e["datos"] for e in diario.leer() if e["tipo"] == "usuario"}
    assert set(ult) == {"operador@ejemplo.test", "a@ejemplo.test"}
    assert ult["a@ejemplo.test"]["version"] == db.fila(con, "SELECT version FROM usuarios WHERE email='a@ejemplo.test'")["version"]
    assert copias.reconciliar_diario(con) == 0


def test_migracion_registro_cerrado_y_tablas(entorno_tmp):
    con = db.conectar()
    db.migrar(con)
    assert "0005_multiusuario.sql" in {r[0] for r in con.execute("SELECT nombre FROM migraciones")}
    assert multiusuario.registro_abierto(con) is False
    cols = {c[1] for c in con.execute("PRAGMA table_info(usuarios)")}
    assert {"cambiar_clave", "version", "alta_origen"} <= cols
    assert db.fila(con, "SELECT COUNT(*) n FROM plataforma")["n"] == 0


# --- Revisión adversarial (U-R1 … U-R6) -------------------------------------------------------------------------------------
def consulta(cli: Cliente, token):
    return cli.ok(otro(cli).post("/api/invitaciones/consulta", {"token": token}))


def invitaciones_de(cli: Cliente, eid):
    return {i["id"]: i for i in cli.ok(cli.get(f"/api/empresas/{eid}/invitaciones"))}


def test_r1_admin_degradado_o_desactivado_no_conserva_sus_invitaciones(app_cliente):
    op = app_cliente
    instalar(op, registro_abierto=True)
    o = otro(op)
    registrar(o, "dueno@ejemplo.test")
    e = o.ok(o.post("/api/empresas", EMPRESA_REPLICA))["id"]
    x = otro(op)
    registrar(x, "x@ejemplo.test", invitacion=o.ok(o.post(f"/api/empresas/{e}/invitaciones", {"email": "x@ejemplo.test", "rol": "admin"}))["token"])
    # X prepara una invitación de administrador a un correo desechable antes de ser degradado
    t2 = x.ok(x.post(f"/api/empresas/{e}/invitaciones", {"email": "calcetin@desechable.test", "rol": "admin"}))
    assert consulta(op, t2["token"])["valida"] is True
    o.ok(o.post(f"/api/empresas/{e}/miembros", {"email": "x@ejemplo.test", "rol": "lector"}))     # degradado
    inv = invitaciones_de(o, e)[t2["id"]]
    assert inv["estado"] == "revocada" and inv["revocada_motivo"] == "administrador_degradado"
    s = otro(op)
    assert registrar(s, "calcetin@desechable.test", invitacion=t2["token"]).status_code == 409
    assert consulta(op, t2["token"]) == {"valida": False}
    assert db.fila(db.conectar(), "SELECT 1 FROM usuarios WHERE email='calcetin@desechable.test'") is None
    assert o.ok(o.get(f"/api/empresas/{e}"))["rol"] == "admin"
    # Aunque una invitación siguiera pendiente (p. ej. creada antes de esta comprobación), solo vale mientras su creador
    # sea administrador activo de la empresa: se comprueba al consultarla, al registrarse y al aceptarla
    o.ok(o.post(f"/api/empresas/{e}/miembros", {"email": "x@ejemplo.test", "rol": "admin"}))
    t3 = x.ok(x.post(f"/api/empresas/{e}/invitaciones", {"email": "nuevo@ejemplo.test", "rol": "admin"}))
    b = otro(op)
    registrar(b, "b@ejemplo.test")
    t4 = x.ok(x.post(f"/api/empresas/{e}/invitaciones", {"email": "b@ejemplo.test", "rol": "admin"}))
    con = db.conectar()
    con.execute("UPDATE miembros SET rol='lector' WHERE usuario_id=? AND empresa_id=?", (uid_de("x@ejemplo.test"), e))
    assert consulta(op, t3["token"]) == {"valida": False}
    assert registrar(otro(op), "nuevo@ejemplo.test", invitacion=t3["token"]).status_code == 409
    assert b.post("/api/invitaciones/aceptar", {"token": t4["token"]}).status_code == 409
    assert b.get(f"/api/empresas/{e}").status_code == 404
    con.execute("UPDATE miembros SET rol='admin' WHERE usuario_id=? AND empresa_id=?", (uid_de("x@ejemplo.test"), e))
    assert consulta(op, t3["token"])["valida"] is True
    # Desactivar la cuenta anula todas sus invitaciones pendientes
    op.ok(op.put(f"/api/plataforma/usuarios/{uid_de('x@ejemplo.test')}", {"activo": False}))
    invs = invitaciones_de(o, e)
    assert invs[t3["id"]]["estado"] == invs[t4["id"]]["estado"] == "revocada"
    assert invs[t3["id"]]["revocada_motivo"] == "cuenta_desactivada"
    assert registrar(otro(op), "nuevo@ejemplo.test", invitacion=t3["token"]).status_code == 409
    # La empresa nunca se queda sin un administrador activo (X está desactivado: no cuenta)
    r = o.post(f"/api/empresas/{e}/miembros", {"email": "dueno@ejemplo.test", "rol": "lector"})
    assert r.status_code == 409 and "al menos un administrador" in r.json()["detail"]
    # … ni aceptando una invitación propia con otro rol (la invitación sigue pendiente)
    t5 = o.ok(o.post(f"/api/empresas/{e}/invitaciones", {"email": "dueno@ejemplo.test", "rol": "lector"}))
    assert o.post("/api/invitaciones/aceptar", {"token": t5["token"]}).status_code == 409
    assert o.ok(o.get(f"/api/empresas/{e}"))["rol"] == "admin" and invitaciones_de(o, e)[t5["id"]]["estado"] == "pendiente"
    # Las de la plataforma: solo valen mientras su creador siga siendo operador activo
    tp = op.ok(op.post("/api/plataforma/invitaciones", {}))
    con.execute("UPDATE usuarios SET superadmin=0 WHERE email='operador@ejemplo.test'")
    assert consulta(op, tp["token"]) == {"valida": False}
    con.execute("UPDATE usuarios SET superadmin=1 WHERE email='operador@ejemplo.test'")
    assert consulta(op, tp["token"])["valida"] is True


def test_r2_indicador_multiples_ot_por_usuario_del_saas(app_cliente):
    from app import emision
    op = app_cliente
    instalar(op, registro_abierto=True)                 # la empresa del operador: otro «inquilino»
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(a.post("/api/empresas", EMPRESA_REPLICA))["id"]
    configurar(a, ea)
    _, bid, _ = borrador_listo(a, ea)
    fid = a.ok(emitir(a, bid))["factura_id"]
    rid = a.ok(a.get(f"/api/facturas/{fid}"))["registros"][0]["id"]
    ind = re.compile(r"IndicadorMultiplesOT>([SN])<")
    assert ind.search(a.get(f"/api/registros/{rid}/xml").text).group(1) == "N"   # A gestiona una sola empresa
    # Con una segunda empresa de A, sus registros siguientes llevan S (anulación de la misma factura)
    a.ok(nueva_empresa(a, NIF_B))
    enviar(ea)
    rid_an = a.ok(a.post(f"/api/facturas/{fid}/anular", {"motivo": "Prueba", "confirmo": True}))["registro_id"]
    assert ind.search(a.get(f"/api/registros/{rid_an}/xml").text).group(1) == "S"
    # Se cuenta por usuario: un miembro que solo gestiona esa empresa, 1; el operador, solo las suyas
    b = otro(op)
    registrar(b, "b@ejemplo.test", invitacion=a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "b@ejemplo.test", "rol": "emisor"}))["token"])
    con = db.conectar()
    assert emision.obligados_del_usuario(con, ea, uid_de("a@ejemplo.test")) == 2
    assert emision.obligados_del_usuario(con, ea, uid_de("b@ejemplo.test")) == 1
    assert emision.obligados_del_usuario(con, ea, uid_de("operador@ejemplo.test")) == 1
    assert emision.sistema_informatico(con, ea, uid_de("b@ejemplo.test")).indicador_multiples_ot == "N"
    # Sin usuario (no ocurre hoy): las que gestionan los miembros de la empresa
    assert emision.obligados_del_usuario(con, ea, None) == 2
    assert db.fila(con, "SELECT COUNT(*) n FROM empresas")["n"] == 3


def test_r3_nif_unico_tambien_con_el_control_alternativo(app_cliente):
    from app import api as api_mod
    from app.nif import _control_cif, es_valido
    op = app_cliente
    instalar(op, registro_abierto=True)
    a, b = otro(op), otro(op)
    registrar(a, "a@ejemplo.test")
    registrar(b, "b@ejemplo.test")
    for i, letra in enumerate("CDFGJUV"):
        cuerpo = f"001{i:04d}"
        dig, let = _control_cif(cuerpo)
        primero, segundo = (letra + cuerpo + dig, letra + cuerpo + let) if i % 2 == 0 else (letra + cuerpo + let, letra + cuerpo + dig)
        assert es_valido(primero) and es_valido(segundo)
        assert a.ok(nueva_empresa(a, primero, razon=f"ENTIDAD FICTICIA {letra}"))["nif"] == primero
        for variante in (segundo, f"{segundo[0]}-{segundo[1:]}".lower()):
            r = nueva_empresa(b, variante, razon=f"ENTIDAD FICTICIA {letra}")
            assert r.status_code == 409 and r.json() == {"detail": api_mod.EMPRESA_YA_DADA_DE_ALTA}, (letra, variante)
    assert b.ok(b.get("/api/yo"))["empresas"] == []
    # Los tipos con un solo control posible no cambian
    nif_p = completar_cif("P", "0012345")
    assert multiusuario.nifs_equivalentes(NIF_B) == (NIF_B,) and multiusuario.nifs_equivalentes(nif_p) == (nif_p,)
    assert multiusuario.nifs_equivalentes("00000000T") == ("00000000T",)
    # La comprobación está en crear_empresa: vale para cualquier vía de alta
    con = db.conectar()
    dig, let = _control_cif("0010003")
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaccion(con):
            api_mod.crear_empresa(con, uid_de("b@ejemplo.test"), "G0010003" + let, "X", None)
    # Campos que no son texto: 422, no 500
    assert nueva_empresa(b, NIF_C, domicilio=5).status_code == 422
    assert b.post("/api/empresas", {"nif": 12345, "razon_social": "X S.L."}).status_code == 422


def test_r4_clave_temporal_queda_en_la_actividad_de_sus_empresas(app_cliente):
    op = app_cliente
    instalar(op, registro_abierto=True)
    a = otro(op)
    registrar(a, "a@ejemplo.test", nombre="Ana Ficticia")
    ea1 = a.ok(nueva_empresa(a, NIF_B))["id"]
    ea2 = a.ok(nueva_empresa(a, NIF_C))["id"]
    col = otro(op)
    registrar(col, "col@ejemplo.test", invitacion=a.ok(a.post(f"/api/empresas/{ea1}/invitaciones", {"email": "col@ejemplo.test", "rol": "lector"}))["token"])
    assert a.ok(a.get("/api/yo"))["usuario"]["clave_restablecida_en"] is None
    op.ok(op.post(f"/api/plataforma/usuarios/{uid_de('a@ejemplo.test')}/clave-temporal", {"clave": "temporal-0001"}))
    # Los miembros de cada empresa de A lo ven (quién lo hizo y a qué cuenta), sin la contraseña
    ev = [x for x in col.ok(col.get(f"/api/empresas/{ea1}/actividad")) if x["accion"] == "usuario.clave_temporal"]
    assert len(ev) == 1 and ev[0]["usuario"] == "Operador" and "Ana Ficticia" in ev[0]["detalle"]
    # El operador entra con la contraseña temporal: también queda la sustitución en cada empresa
    entrar(a, "a@ejemplo.test", "temporal-0001")
    a.ok(a.put("/api/yo/clave", {"clave_actual": "temporal-0001", "clave_nueva": "definitiva-0002"}))
    for e in (ea1, ea2):
        acciones = [x["accion"] for x in a.ok(a.get(f"/api/empresas/{e}/actividad"))]
        assert acciones[:2] == ["usuario.clave_temporal_sustituida", "usuario.clave_temporal"], acciones
    # Y la propia persona lo ve en «Mi cuenta»
    assert a.ok(a.get("/api/yo"))["usuario"]["clave_restablecida_en"]
    assert "usuario.clave_temporal" in [x["accion"] for x in op.ok(op.get("/api/plataforma/actividad"))]
    # Un cambio normal de contraseña no se anota en las empresas
    a.ok(a.put("/api/yo/clave", {"clave_actual": "definitiva-0002", "clave_nueva": "otra-clave-0003"}))
    assert [x["accion"] for x in a.ok(a.get(f"/api/empresas/{ea2}/actividad"))].count("usuario.clave_temporal_sustituida") == 1
    texto = str(db.filas(db.conectar(), "SELECT detalle FROM actividad"))
    assert "temporal-0001" not in texto and "definitiva-0002" not in texto


def test_r5_registro_cerrado_las_invitaciones_de_empresa_no_crean_cuentas(app_cliente, monkeypatch):
    op = app_cliente
    e_op = instalar(op)                                   # registro cerrado
    a = otro(op)
    assert registrar(a, "a@ejemplo.test", invitacion=op.ok(op.post("/api/plataforma/invitaciones", {"email": "a@ejemplo.test"}))["token"]).status_code == 200
    ea = a.ok(nueva_empresa(a, NIF_B))["id"]
    inv = a.ok(a.post(f"/api/empresas/{ea}/miembros", {"email": "bot0@desechable.test", "rol": "lector"}))["invitacion"]
    assert inv["crea_cuenta"] is False
    assert consulta(op, inv["token"])["crea_cuenta"] is False
    r = registrar(otro(op), "bot0@desechable.test", invitacion=inv["token"])
    assert r.status_code == 403 and r.json() == {"detail": multiusuario.MENSAJE_SOLO_CON_CUENTA}
    assert db.fila(db.conectar(), "SELECT 1 FROM usuarios WHERE email='bot0@desechable.test'") is None
    # La respuesta no depende de que el correo tenga cuenta; una cuenta existente sí puede aceptarla
    b = otro(op)
    registrar(b, "b@ejemplo.test", invitacion=op.ok(op.post("/api/plataforma/invitaciones", {"email": "b@ejemplo.test"}))["token"])
    inv_b = a.ok(a.post(f"/api/empresas/{ea}/miembros", {"email": "b@ejemplo.test", "rol": "lector"}))["invitacion"]
    assert registrar(otro(op), "b@ejemplo.test", invitacion=inv_b["token"]).json() == r.json()
    assert b.ok(b.post("/api/invitaciones/aceptar", {"token": inv_b["token"]}))["empresa_id"] == ea
    # Las invitaciones de empresa del operador sí crean cuentas (él decide quién entra)
    t_op = op.ok(op.post(f"/api/empresas/{e_op}/invitaciones", {"email": "plantilla@ejemplo.test", "rol": "emisor"}))
    assert t_op["crea_cuenta"] is True
    assert registrar(otro(op), "plantilla@ejemplo.test", invitacion=t_op["token"]).status_code == 200
    # El operador puede permitirlo (ajuste propio, solo él)
    assert a.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": True}).status_code == 403
    assert op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": "si"}).status_code == 422
    assert op.put("/api/plataforma/ajustes", {}).status_code == 422
    assert op.ok(op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": True})) == {
        "registro_abierto": False, "invitaciones_empresa_crean_cuentas": True}
    assert op.ok(op.get("/api/plataforma"))["ajustes"]["invitaciones_empresa_crean_cuentas"] is True
    assert registrar(otro(op), "bot0@desechable.test", invitacion=inv["token"]).status_code == 200
    # Cupos (en la base de datos) para los usuarios normales: invitaciones pendientes por empresa…
    monkeypatch.setitem(multiusuario.CUPOS, "invitaciones_pendientes_empresa", 2)
    p1 = a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "p1@ejemplo.test", "rol": "lector"}))
    a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "p2@ejemplo.test", "rol": "lector"}))
    r = a.post(f"/api/empresas/{ea}/miembros", {"email": "p3@ejemplo.test", "rol": "lector"})
    assert r.status_code == 429 and "pendientes" in r.json()["detail"]
    a.ok(borrar(a, f"/api/invitaciones/{p1['id']}"))
    a.ok(a.post(f"/api/empresas/{ea}/invitaciones", {"email": "p3@ejemplo.test", "rol": "lector"}))
    # … invitaciones por persona y día…
    hechas = db.fila(db.conectar(), "SELECT COUNT(*) n FROM invitaciones WHERE creada_por=?", (uid_de("a@ejemplo.test"),))["n"]
    monkeypatch.setitem(multiusuario.CUPOS, "invitaciones_pendientes_empresa", 100)
    monkeypatch.setitem(multiusuario.CUPOS, "invitaciones_dia_usuario", hechas)
    r = a.post(f"/api/empresas/{ea}/invitaciones", {"email": "p4@ejemplo.test", "rol": "lector"})
    assert r.status_code == 429 and "día" in r.json()["detail"]
    # … y altas de empresas por persona y día; el operador no tiene cupos
    monkeypatch.setitem(multiusuario.CUPOS, "empresas_dia_usuario", 2)
    a.ok(nueva_empresa(a, NIF_C))
    r = nueva_empresa(a, NIF_D)
    assert r.status_code == 429 and db.fila(db.conectar(), "SELECT 1 FROM empresas WHERE nif=?", (NIF_D,)) is None
    monkeypatch.setitem(multiusuario.CUPOS, "invitaciones_dia_usuario", 0)
    monkeypatch.setitem(multiusuario.CUPOS, "empresas_dia_usuario", 0)
    op.ok(op.post(f"/api/empresas/{e_op}/invitaciones", {"email": "p5@ejemplo.test", "rol": "lector"}))
    op.ok(nueva_empresa(op, NIF_D))


def test_r6_quitar_miembros_y_salir_de_una_empresa(app_cliente):
    op = app_cliente
    instalar(op, registro_abierto=True)
    o = otro(op)
    registrar(o, "dueno@ejemplo.test")
    e = o.ok(o.post("/api/empresas", EMPRESA_REPLICA))["id"]
    ex, lec, ajeno = otro(op), otro(op), otro(op)
    registrar(ex, "ex@ejemplo.test", invitacion=o.ok(o.post(f"/api/empresas/{e}/invitaciones", {"email": "ex@ejemplo.test", "rol": "admin"}))["token"])
    registrar(lec, "lec@ejemplo.test", invitacion=o.ok(o.post(f"/api/empresas/{e}/invitaciones", {"email": "lec@ejemplo.test", "rol": "lector"}))["token"])
    registrar(ajeno, "ajeno@ejemplo.test")
    ti = ex.ok(ex.post(f"/api/empresas/{e}/invitaciones", {"email": "amigo@ejemplo.test", "rol": "admin"}))
    con = db.conectar()
    copia = copias.crear_copia(con)
    uid_ex, uid_o, uid_lec = uid_de("ex@ejemplo.test"), uid_de("dueno@ejemplo.test"), uid_de("lec@ejemplo.test")
    url = f"/api/empresas/{e}/miembros/{uid_ex}"
    # Ni un lector ni alguien ajeno pueden quitar a nadie (404: no se revela nada); sin CSRF, 403
    assert borrar(lec, url).status_code == 404 and borrar(ajeno, url).status_code == 404 and borrar(op, url).status_code == 404
    assert o.tc.delete(url).status_code == 403
    # Un administrador quita a otro (ex empleado): pierde todo acceso y sus invitaciones pendientes
    o.ok(borrar(o, url))
    for ruta in (f"/api/empresas/{e}", f"/api/empresas/{e}/facturas", f"/api/empresas/{e}/miembros"):
        assert ex.get(ruta).status_code == 404
    assert e not in [x["id"] for x in ex.ok(ex.get("/api/yo"))["empresas"]]
    assert invitaciones_de(o, e)[ti["id"]]["revocada_motivo"] == "miembro_quitado"
    assert registrar(otro(op), "amigo@ejemplo.test", invitacion=ti["token"]).status_code == 409
    assert borrar(o, url).status_code == 404
    baja = [x for x in o.ok(o.get(f"/api/empresas/{e}/actividad")) if x["accion"] == "miembro.baja"]
    assert len(baja) == 1 and str(uid_ex) in baja[0]["detalle"]
    # El último administrador activo no puede irse ni ser quitado
    r = borrar(o, f"/api/empresas/{e}/miembros/{uid_o}")
    assert r.status_code == 409 and "al menos un administrador" in r.json()["detail"]
    # Cualquier miembro puede salir por sí mismo
    assert lec.ok(borrar(lec, f"/api/empresas/{e}/miembros/{uid_lec}"))["empresas"] == []
    assert lec.get(f"/api/empresas/{e}").status_code == 404
    # La baja de miembros y el ajuste de invitaciones sobreviven a una restauración de la copia anterior
    op.ok(op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": True}))
    con.close()
    copias.restaurar(copia)
    assert [m["email"] for m in o.ok(o.get(f"/api/empresas/{e}/miembros"))] == ["dueno@ejemplo.test"]
    assert ex.get(f"/api/empresas/{e}").status_code in (401, 404) and lec.get(f"/api/empresas/{e}").status_code in (401, 404)
    assert op.ok(op.get("/api/plataforma"))["ajustes"]["invitaciones_empresa_crean_cuentas"] is True
    assert registrar(otro(op), "amigo@ejemplo.test", invitacion=ti["token"]).status_code == 409
