"""Lane G1 de la revisión integrada: cuentas, límites del registro y robustez de la API.

- ISO-1/FIX-1: las invitaciones de un usuario no agotan los límites comunes del registro (intentos y altas por hora).
- RES-1: las cuentas de la versión anterior (contraseña fijada por otra persona) deben elegir una nueva al entrar.
- ISO-2: un NIF solo tiene caracteres ASCII (las cifras de otros alfabetos no burlan el NIF único).
- ISO-3/ISO-4: tipos erróneos → 422, serie duplicada → 409; errores internos agregados por minuto, anotados también en
  la empresa y con un mensaje que no remite a un registro que no se puede ver.
- FIX-3: identificadores con «cifras» Unicode → 422.
- ISO-6: el primer envío real con el certificado de un tercero exige el NIF verificado por el operador.
- ISO-7: el barrido de aislamiento cubre todas las rutas con identificadores y detecta una ruta sin control de acceso.

Todo local: simulador de la AEAT, sin red; el camino de «pruebas» solo se abre dentro de una prueba con el transporte
sustituido (como en test_16_c_certificados). Datos ficticios: NIF de provincia 00 o de las pruebas existentes.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
from conftest import EMPRESA_DEMO, EMPRESA_REPLICA, Cliente, borrador_listo, configurar, preparar_empresa, subir
from fastapi import APIRouter
from fastapi.testclient import TestClient
from test_03_flujo import emitir
from test_16_c_certificados import (NIF_GESTORIA, asignar, legitimar, pruebas_sin_red, sello_empresa, sello_gestoria,  # noqa: F401
                                    subir_empresa, subir_mio)
from test_16_u_multiusuario import CLAVE, instalar, registrar, uid_de

from app import api, api_certificados, certificados, db, diario, multiusuario, nif as nifmod, produccion, remision, seguridad
from app.nif import completar_cif

NIF_PROPIA = completar_cif("B", "0045678")      # empresa que da de alta un usuario cualquiera


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    """Límites por defecto (los de una instalación sin variables de entorno)."""
    valores = {"LIMITE_REGISTRO_IP": 5, "LIMITE_REGISTRO_GLOBAL": 30, "LIMITE_REGISTRO_INTENTOS": 300,
               "LIMITE_CONSULTA_INVITACION": 30, "LIMITE_POR_INVITACION": 10, "LIMITE_INVITACION_IP": 50,
               "LIMITE_INTENTOS_INVITADOR": 50, "LIMITE_ALTAS_INVITADOR": 10}
    for nombre, v in valores.items():
        if hasattr(multiusuario, nombre):
            monkeypatch.setattr(getattr(multiusuario, nombre), "maximo", v)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


@pytest.fixture()
def minuto(monkeypatch):
    """Minuto de los errores agregados (app/errores.py) controlado por la prueba: [valor actual]."""
    from app import errores
    m = ["2026-01-05T10:00"]
    monkeypatch.setattr(errores, "_minuto", lambda: m[0])
    return m


def desde(c: Cliente, ip: str, errores=True) -> Cliente:
    """Otro cliente de la misma aplicación con su propia dirección IP (y sin sesión)."""
    return Cliente(TestClient(c.tc.app, client=(ip, 50000), raise_server_exceptions=errores))


def invitacion_plataforma(op: Cliente) -> str:
    return op.ok(op.post("/api/plataforma/invitaciones", {"dias": 7}))["token"]


def usuario_con_empresa(op: Cliente, email: str, nif: str, ip: str) -> tuple[Cliente, int]:
    u = desde(op, ip)
    assert registrar(u, email, invitacion=invitacion_plataforma(op)).status_code == 200
    eid = u.ok(u.post("/api/empresas", {"nif": nif, "razon_social": "EMPRESA FICTICIA S.L.", "domicilio": "C/ Inventada, 1"}))["id"]
    return u, eid


def invitar(u: Cliente, eid: int, n: int, prefijo: str) -> list[str]:
    return [u.ok(u.post(f"/api/empresas/{eid}/invitaciones", {"email": f"{prefijo}{i}@ejemplo.test", "rol": "lector"}))["token"]
            for i in range(n)]


# --- ISO-1 / FIX-1 ------------------------------------------------------------------------------------------------------
def test_iso1_invitaciones_de_un_usuario_no_agotan_los_intentos_de_toda_la_plataforma(app_cliente):
    """Un usuario cualquiera con 30 invitaciones de su empresa martillea el registro desde UNA IP: antes gastaba los 300
    intentos por hora de toda la plataforma y nadie más podía registrarse (también desde otras IP)."""
    op = app_cliente
    instalar(op)                                          # registro cerrado (por defecto)
    a, ea = usuario_con_empresa(op, "a@ejemplo.test", NIF_PROPIA, "192.0.2.10")
    tokens = invitar(a, ea, 30, "invitado")
    atacante = desde(op, "192.0.2.66")
    estados = {}
    for t in tokens:
        for _ in range(10):
            r = atacante.post("/api/registro", {"invitacion": t})          # cuerpo incompleto: 422 si pasa los límites
            estados[r.status_code] = estados.get(r.status_code, 0) + 1
    assert estados.get(429, 0) >= 250 and set(estados) <= {422, 429}, estados
    # Los límites comunes siguen disponibles para los demás, desde otras IP
    v1 = desde(op, "192.0.2.77")
    assert registrar(v1, "invitada1@ejemplo.test", invitacion=invitacion_plataforma(op)).status_code == 200
    abrir = op.put("/api/plataforma/ajustes", {"registro_abierto": True})
    assert abrir.status_code == 200
    assert registrar(desde(op, "192.0.2.78"), "libre@ejemplo.test").status_code == 200
    # El techo por IP del tráfico con invitación también protege con invitaciones de varios creadores
    assert multiusuario.LIMITE_REGISTRO_INTENTOS.disponible()


def test_iso1_altas_con_las_invitaciones_de_un_usuario_y_oficina_desde_una_ip(app_cliente):
    """Con las invitaciones de empresa autorizadas a crear cuentas, una oficina de 9 personas se da de alta desde una
    misma IP (U-R8), pero un solo usuario ya no consume todas las altas por hora de la plataforma."""
    op = app_cliente
    instalar(op)
    assert op.ok(op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": True}))
    a, ea = usuario_con_empresa(op, "a@ejemplo.test", NIF_PROPIA, "192.0.2.10")
    oficina = invitar(a, ea, 9, "oficina")
    for i, t in enumerate(oficina):
        cli = desde(op, "192.0.2.90")
        assert cli.ok(cli.post("/api/invitaciones/consulta", {"token": t}))["crea_cuenta"] is True
        assert registrar(cli, f"oficina{i}@ejemplo.test", invitacion=t).status_code == 200
    mas = invitar(a, ea, 20, "extra")
    res = [registrar(desde(op, "192.0.2.66"), f"extra{i}@ejemplo.test", invitacion=t) for i, t in enumerate(mas)]
    altas = sum(r.status_code == 200 for r in res)
    assert altas == 1, [r.status_code for r in res]          # 10 cuentas por hora con las invitaciones de un usuario
    assert "quien le ha invitado" in res[-1].json()["detail"]
    # Las altas de toda la plataforma no se han agotado: una persona invitada por el operador, desde otra IP
    assert registrar(desde(op, "192.0.2.77"), "invitada@ejemplo.test", invitacion=invitacion_plataforma(op)).status_code == 200


def test_iso1_un_cliente_con_invitaciones_de_varios_usuarios_no_agota_las_altas(app_cliente):
    """Desde una sola IP, con invitaciones de varios usuarios que crean cuentas: como mucho la mitad de las altas por hora
    de la plataforma (15 de 30); el resto de la plataforma sigue dándose de alta."""
    op = app_cliente
    instalar(op)
    assert op.ok(op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": True}))
    invitaciones = []
    for i, nif in enumerate((NIF_PROPIA, completar_cif("B", "0045679"), completar_cif("B", "0045680"))):
        u, e = usuario_con_empresa(op, f"creador{i}@ejemplo.test", nif, f"192.0.2.{11 + i}")
        invitaciones += [(f"c{i}-{n}@ejemplo.test", t) for n, t in enumerate(invitar(u, e, 8, f"c{i}-"))]
    cli = desde(op, "192.0.2.66")
    res = [registrar(cli, email, invitacion=t).status_code for email, t in invitaciones]
    assert res.count(200) == 15 and set(res) == {200, 429}, res
    assert registrar(desde(op, "192.0.2.77"), "invitada@ejemplo.test", invitacion=invitacion_plataforma(op)).status_code == 200


# --- RES-1 ------------------------------------------------------------------------------------------------------------
def _base_version_anterior(entorno_tmp):
    """Base de datos como la deja la versión pública anterior (esquema 0001-0004): operador, un administrador de empresa
    y una asesora cuya contraseña fijó ese administrador (no había forma de cambiarla)."""
    viejas = entorno_tmp / "migraciones_previas"
    viejas.mkdir()
    for f in sorted(db.MIGRACIONES_DIR.glob("000[1-4]_*.sql")):
        shutil.copy(f, viejas / f.name)
    con = db.conectar()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db, "MIGRACIONES_DIR", viejas)
        assert db.migrar(con) == ["0001_inicial.sql", "0002_remision.sql", "0003_superadmin.sql", "0004_version_miembros.sql"]
    ahora = db.ahora_iso()
    for email, nombre, clave in (("operador@ejemplo.test", "Operador", CLAVE), ("admin1@ejemplo.test", "Admin E1", "clave-admin1-123"),
                                 ("asesora@ejemplo.test", "Asesora", "clave-fijada-por-admin1")):
        con.execute("INSERT INTO usuarios (email, nombre, hash_clave, creado_en) VALUES (?,?,?,?)",
                    (email, nombre, seguridad.hash_clave(clave), ahora))
    con.execute("UPDATE usuarios SET superadmin=1 WHERE email='operador@ejemplo.test'")
    con.execute("INSERT INTO empresas (nif, razon_social, creado_en) VALUES (?,?,?)",
                (EMPRESA_REPLICA["nif"], EMPRESA_REPLICA["razon_social"], ahora))
    for uid, r in ((1, "admin"), (2, "admin"), (3, "lector")):
        con.execute("INSERT INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,1,?,'empleado_autorizado')", (uid, r))
    con.close()


def test_res1_cuentas_de_la_version_anterior_deben_cambiar_la_clave(entorno_tmp):
    _base_version_anterior(entorno_tmp)
    from app.main import crear_app
    with TestClient(crear_app()) as tc:                 # al arrancar: migraciones 0005 y siguientes y conciliación
        con = db.conectar()
        u = {x["email"]: x for x in db.filas(con, "SELECT email, superadmin, cambiar_clave, alta_origen, version FROM usuarios")}
        assert u["operador@ejemplo.test"]["cambiar_clave"] == 0 and u["operador@ejemplo.test"]["alta_origen"] is None
        for email in ("admin1@ejemplo.test", "asesora@ejemplo.test"):
            assert (u[email]["cambiar_clave"], u[email]["alta_origen"], u[email]["version"]) == (1, "version_anterior", 1)
        # Anotado en el diario por la conciliación del arranque (una restauración no lo deshace)
        anotado = {x["datos"]["email"]: x["datos"] for x in diario.leer() if x["tipo"] == "usuario"}
        assert anotado["asesora@ejemplo.test"]["cambiar_clave"] == 1 and anotado["asesora@ejemplo.test"]["version"] == 1
        # La asesora (o quien conozca su contraseña) solo puede cambiarla; el aviso explica el motivo
        c = Cliente(tc)
        r = c.ok(c.post("/api/sesion", {"email": "asesora@ejemplo.test", "clave": "clave-fijada-por-admin1"}))
        c.csrf = r["csrf"]
        assert r["usuario"]["cambiar_clave"] == 1 and r["usuario"]["alta_origen"] == "version_anterior"
        assert c.get("/api/empresas/1/documentos").status_code == 403
        assert c.post("/api/empresas", {"nif": NIF_PROPIA, "razon_social": "OTRA S.L.", "domicilio": "C/ X, 1"}).status_code == 403
        assert c.ok(c.get("/api/yo"))["usuario"]["alta_origen"] == "version_anterior"
        c.ok(c.put("/api/yo/clave", {"clave_actual": "clave-fijada-por-admin1", "clave_nueva": "nueva-clave-asesora-1"}))
        assert c.get("/api/empresas/1/documentos").status_code == 200
        # El operador entra sin cambio obligatorio; la empresa de la versión anterior (alta del operador) tiene el NIF verificado
        op = Cliente(TestClient(tc.app))
        assert op.ok(op.post("/api/sesion", {"email": "operador@ejemplo.test", "clave": CLAVE}))["usuario"]["cambiar_clave"] == 0
        e = db.fila(con, "SELECT nif_verificado_en, nif_verificado_motivo FROM empresas WHERE id=1")
        assert e["nif_verificado_en"] and e["nif_verificado_motivo"] == "version_anterior"
        # Una segunda puesta en marcha no vuelve a exigirlo (la migración se aplica una sola vez)
    with TestClient(crear_app()):
        assert db.fila(db.conectar(), "SELECT cambiar_clave FROM usuarios WHERE email='asesora@ejemplo.test'")["cambiar_clave"] == 0


# --- ISO-2 ------------------------------------------------------------------------------------------------------------
NO_ASCII = ["B８２９９１３１6", "B٨٢٩٩١٣١6", "B८२९९१३१6", "B𝟾𝟸𝟿𝟿𝟷𝟹𝟷6", "ｂ82991316", "B82991316٣"]


def test_iso2_nif_solo_con_caracteres_ascii(app_cliente):
    op = app_cliente
    instalar(op, registro_abierto=True, empresa=EMPRESA_REPLICA)
    b = desde(op, "192.0.2.20")
    registrar(b, "b@ejemplo.test")
    for nif in NO_ASCII:
        assert not nifmod.es_valido(nif), nif
        r = b.post("/api/empresas", {"nif": nif, "razon_social": "COPIA S.L.", "domicilio": "C/ X, 1"})
        assert r.status_code == 422, (nif, r.status_code, r.text)
    assert b.post("/api/empresas", {"nif": " b-82.991.316 ", "razon_social": "COPIA S.L.", "domicilio": "C/ X, 1"}).status_code == 409
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM empresas")["n"] == 1
    # Titular de una legitimación y productor: el mismo validador
    eb = b.ok(b.post("/api/empresas", {"nif": NIF_PROPIA, "razon_social": "PROPIA S.L.", "domicilio": "C/ X, 1"}))["id"]
    did = subir(b, eb, "demo_recibida.pdf")["documento_id"]
    r = b.post(f"/api/empresas/{eb}/legitimacion", {"metodo": "colaborador_social", "titular_nif": "B００９９８８７" + NIF_GESTORIA[-1],
                                                    "titular_nombre": "G", "evidencia_documento_id": did})
    assert r.status_code == 422
    assert op.put("/api/productor", {"nombre": "P", "nif": "B８２９９１３１6"}).status_code == 422
    assert nifmod.buscar_nifs("CIF: B８２９９１３１6") == []


def test_iso2_aviso_al_arrancar_de_nif_no_ascii_guardados(app_cliente):
    """Filas guardadas por versiones anteriores con un NIF no ASCII: aviso al operador (solo recuentos) y en la empresa."""
    op = app_cliente
    instalar(op)
    con = db.conectar()
    con.execute("INSERT INTO empresas (nif, razon_social, creado_en) VALUES (?,?,?)", ("B８２９９１３１6", "COPIA S.L.", db.ahora_iso()))
    eid = con.execute("SELECT id FROM empresas WHERE nif=?", ("B８２９９１３１6",)).fetchone()[0]
    from app.main import crear_app
    for _ in range(2):                                    # dos arranques: el aviso no se repite si nada cambia
        with TestClient(crear_app()):
            pass
    plat = db.filas(con, "SELECT detalle FROM actividad WHERE accion='plataforma.nif_no_ascii' AND empresa_id IS NULL")
    assert [json.loads(x["detalle"]) for x in plat] == [{"filas": {"empresas.nif": 1}}]
    emp = db.filas(con, "SELECT detalle FROM actividad WHERE accion='empresa.nif_no_ascii' AND empresa_id=?", (eid,))
    assert len(emp) == 1 and "no es un NIF válido" in json.loads(emp[0]["detalle"])["aviso"]
    assert any(a["accion"] == "plataforma.nif_no_ascii" and "B８" not in a["detalle"]
               for a in op.ok(op.get("/api/plataforma/actividad")))


# --- ISO-3 / ISO-4 / FIX-3 ----------------------------------------------------------------------------------------------
def test_iso3_tipos_erroneos_sin_sesion_dan_422(app_cliente):
    op = app_cliente
    anon = desde(op, "192.0.2.30", errores=False)
    instalacion = [{"empresa": "x"}, {"empresa": [1]}, {"empresa": {"nif": 7}},
                   {"email": "o@ejemplo.test", "nombre": "O", "clave": CLAVE},                     # sin empresa
                   {"empresa": EMPRESA_DEMO, "nombre": "O", "clave": CLAVE},                       # sin correo
                   {"empresa": EMPRESA_DEMO, "email": 7, "nombre": "O", "clave": CLAVE},
                   {"empresa": EMPRESA_DEMO, "email": "o@ejemplo.test", "nombre": "O", "clave": CLAVE, "registro_abierto": "si"}]
    for cuerpo in instalacion:                            # antes y después de instalar
        assert anon.post("/api/instalar", cuerpo).status_code == 422, cuerpo
    instalar(op)
    for cuerpo in instalacion:
        assert anon.post("/api/instalar", cuerpo).status_code == 422, cuerpo
    for cuerpo in ({"email": 5, "clave": "x"}, {"email": "a@ejemplo.test", "clave": 5}, {"email": None, "clave": "x"},
                   {"email": ["a"], "clave": "x"}, {"email": "a@ejemplo.test", "clave": {"a": 1}}):
        assert anon.post("/api/sesion", cuerpo).status_code == 422, cuerpo
    assert anon.post("/api/sesion", {"email": "nadie@ejemplo.test", "clave": "x"}).status_code == 401
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM actividad WHERE accion='error.interno'")["n"] == 0


def test_iso3_errores_internos_agregados_sin_desplazar_la_auditoria(app_cliente, monkeypatch, minuto):
    op = app_cliente
    instalar(op)
    v = desde(op, "192.0.2.40")
    assert registrar(v, "v@ejemplo.test", invitacion=invitacion_plataforma(op)).status_code == 200
    vid = uid_de("v@ejemplo.test")
    op.ok(op.post(f"/api/plataforma/usuarios/{vid}/clave-temporal", {"clave": "temporal-0001"}))
    op.ok(op.put(f"/api/plataforma/usuarios/{vid}", {"activo": False}))

    def roto(con):
        raise RuntimeError("fallo simulado")
    monkeypatch.setattr(multiusuario, "registro_abierto", roto)        # GET /api/estado (sin sesión) falla
    anon = desde(op, "192.0.2.41", errores=False)
    for _ in range(300):
        r = anon.get("/api/estado")
        assert r.status_code == 500
    assert "registro de actividad" not in r.json()["detail"] and "operador de la plataforma" in r.json()["detail"]
    con = db.conectar()
    assert len(op.ok(op.get("/api/plataforma/actividad"))) >= 1        # consultarla no escribe el minuto en curso…
    assert db.fila(con, "SELECT COUNT(*) n FROM actividad WHERE accion='error.interno'")["n"] == 0
    minuto[0] = "2026-01-05T10:01"                                      # …sí, una vez terminado
    op.ok(op.get("/api/plataforma/actividad"))
    filas = [json.loads(x["detalle"]) for x in db.filas(con, "SELECT detalle FROM actividad WHERE accion='error.interno'")]
    assert len(filas) == 1 and filas[0]["veces"] == 300 and filas[0]["minuto"] == "2026-01-05T10:00"   # una fila, con contador
    assert {(f["ruta"], f["tipo"]) for f in filas} == {("/api/estado", "RuntimeError")}
    assert all(set(f) == {"ruta", "tipo", "veces", "minuto"} for f in filas)
    # Aunque hubiera muchos errores distintos, la auditoría de la plataforma sigue a la vista
    for i in range(700):
        db.actividad(con, "error.interno", detalle={"ruta": f"/api/x{i}", "tipo": "KeyError", "veces": 1})
    act = op.ok(op.get("/api/plataforma/actividad"))
    acciones = [a["accion"] for a in act]
    assert {"usuario.clave_temporal", "usuario.desactivado", "invitacion.creada", "usuario.registro"} <= set(acciones)
    assert acciones.count("error.interno") == 100
    assert any(json.loads(a["detalle"]).get("veces") for a in act if a["accion"] == "error.interno")


def test_iso4_tipos_erroneos_y_serie_duplicada(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    rid = c.ok(c.get(f"/api/facturas/{fid}"))["registros"][0]["id"]
    did = c.ok(c.get(f"/api/empresas/{eid}/documentos"))[0]["id"]
    s = Cliente(TestClient(c.tc.app, raise_server_exceptions=False))
    s.tc.cookies.set("sid", c.tc.cookies.get("sid"))
    s.csrf = c.csrf
    r = s.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "A", "patron": "{n}/{aa}"})
    assert r.status_code == 409 and "Ya existe una serie con el código «A»" in r.json()["detail"]
    casos = [
        ("POST", f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "Z", "patron": "Z{n}", "siguiente_numero": "diez"}),
        ("POST", f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": 7, "patron": "Z{n}"}),
        ("POST", f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "Z", "patron": "Z{n}", "tipo": "otra"}),
        ("PUT", f"/api/empresas/{eid}", {"razon_social": 7}),
        ("PUT", f"/api/empresas/{eid}", {"domicilio": 7}),
        ("PUT", f"/api/empresas/{eid}", {"territorio": ["comun"]}),
        ("POST", f"/api/facturas/{fid}/anular", {"motivo": 7, "confirmo": True}),
        ("POST", f"/api/facturas/{fid}/rectificativa", {"motivo": 7, "tipo": "R1"}),
        ("POST", f"/api/registros/{rid}/subsanar", {"motivo": 7, "confirmo": True}),
        ("POST", f"/api/documentos/{did}/bloqueo", {"motivo": [1]}),
        ("POST", f"/api/empresas/{eid}/produccion/autorizacion", {"texto_confirmacion": 7}),
        ("POST", "/api/series/probar", {"patron": "{n}", "num_serie": 7}),
        ("PUT", "/api/productor", {"nombre": "X", "nif": 7}),
        ("POST", "/api/simulador/modo", {"modo": "normal", "veces": [1]}),
        ("POST", "/api/simulador/modo", {"modo": 7}),
    ]
    malos = []
    for m, url, cuerpo in casos:
        r = s.tc.request(m, url, json=cuerpo, headers=s._h())
        if r.status_code != 422:
            malos.append((m, url, cuerpo, r.status_code, r.text[:100]))
    assert not malos, malos
    assert s.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "Z", "patron": "Z{n}", "siguiente_numero": "12"}).status_code == 200
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM actividad WHERE accion='error.interno'")["n"] == 0


def test_iso4_error_interno_en_la_actividad_de_la_empresa(app_cliente, monkeypatch, minuto):
    """Un 500 en una ruta de una empresa queda (sin datos) en la actividad de esa empresa si quien pide es miembro, y el
    mensaje remite a lo que esa persona puede ver."""
    op = app_cliente
    eop = instalar(op, registro_abierto=True)
    a = desde(op, "192.0.2.50", errores=False)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(a.post("/api/empresas", {"nif": NIF_PROPIA, "razon_social": "PROPIA S.L.", "domicilio": "C/ X, 1"}))["id"]

    def roto(*args, **kw):
        raise ZeroDivisionError()
    monkeypatch.setattr(remision, "estado_cola", roto)
    for _ in range(3):
        r = a.get(f"/api/empresas/{ea}/remision")
    assert r.status_code == 500 and "actividad de la empresa" in r.json()["detail"]
    minuto[0] = "2026-01-05T10:01"                                      # la fila del minuto se escribe al terminar
    filas = [json.loads(x["detalle"]) for x in a.ok(a.get(f"/api/empresas/{ea}/actividad")) if x["accion"] == "error.interno"]
    assert len(filas) == 1 and filas[0].pop("veces") == 3                # una fila por minuto, con contador
    assert {k: v for k, v in filas[0].items() if k != "minuto"} == {"ruta": "/api/empresas/{eid}/remision", "tipo": "ZeroDivisionError"}
    # El operador (miembro de su propia empresa) recibe el mensaje que remite a su actividad
    op2 = Cliente(TestClient(op.tc.app, raise_server_exceptions=False))
    op2.tc.cookies.set("sid", op.tc.cookies.get("sid"))
    assert "actividad de la empresa" in op2.get(f"/api/empresas/{eop}/remision").json()["detail"]
    monkeypatch.setattr(multiusuario, "registro_abierto", roto)
    assert op2.get("/api/estado").json()["detail"] == "Error interno. Consulte la actividad de la plataforma."
    assert "operador de la plataforma" in a.get("/api/estado").json()["detail"]
    # En la actividad de la plataforma: solo la plantilla de la ruta y el tipo
    minuto[0] = "2026-01-05T10:02"
    plat = [json.loads(x["detalle"]) for x in op.ok(op.get("/api/plataforma/actividad")) if x["accion"] == "error.interno"]
    assert sum(f["veces"] for f in plat if f["ruta"] == "/api/empresas/{eid}/remision" and f["tipo"] == "ZeroDivisionError") == 4


def test_g1v2_consultar_la_actividad_no_rompe_la_agregacion_ni_desplaza_la_auditoria(app_cliente, monkeypatch, minuto):
    """Alternar un error y una consulta de la actividad ya no escribe una fila por error (antes, consultar escribía el
    minuto en curso: 510 errores, 510 filas), y en la actividad de la empresa los errores van aparte de la auditoría."""
    op = app_cliente
    instalar(op, registro_abierto=True)
    a = desde(op, "192.0.2.50", errores=False)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(a.post("/api/empresas", {"nif": NIF_PROPIA, "razon_social": "PROPIA S.L.", "domicilio": "C/ X, 1"}))["id"]

    def roto(*args, **kw):
        raise ZeroDivisionError()
    monkeypatch.setattr(remision, "estado_cola", roto)        # cualquier 500 futuro en una ruta de la empresa
    for _ in range(150):
        assert a.get(f"/api/empresas/{ea}/remision").status_code == 500
        a.ok(a.get(f"/api/empresas/{ea}/actividad"))
    op.ok(op.get("/api/plataforma/actividad"))
    con = db.conectar()
    n = "SELECT COUNT(*) n FROM actividad WHERE accion='error.interno' AND empresa_id IS ?"
    assert db.fila(con, n, (ea,))["n"] == 0 and db.fila(con, n, (None,))["n"] == 0
    minuto[0] = "2026-01-05T10:01"
    errores_ = [json.loads(x["detalle"]) for x in a.ok(a.get(f"/api/empresas/{ea}/actividad")) if x["accion"] == "error.interno"]
    assert [(f["ruta"], f["veces"]) for f in errores_] == [("/api/empresas/{eid}/remision", 150)]
    assert db.fila(con, n, (None,))["n"] == 1
    # Aunque hubiera muchas filas de errores (rutas y minutos distintos), la auditoría de la empresa sigue a la vista
    with db.transaccion(con):
        for i in range(700):
            db.actividad(con, "error.interno", empresa_id=ea, detalle={"ruta": f"/api/x{i}", "tipo": "KeyError", "veces": 1})
    acc = [x["accion"] for x in a.ok(a.get(f"/api/empresas/{ea}/actividad"))]
    assert "empresa.creada" in acc and acc.count("error.interno") == 100


def test_g1v3_entradas_de_las_cuentas_no_desplazan_la_auditoria_de_la_plataforma(app_cliente):
    """Una cuenta cualquiera entra (o cambia su contraseña) cientos de veces: el operador sigue viendo las contraseñas
    temporales y las bajas (antes: 500 «sesion.inicio» y nada más)."""
    op = app_cliente
    instalar(op)
    v = desde(op, "192.0.2.40")
    assert registrar(v, "v@ejemplo.test", invitacion=invitacion_plataforma(op)).status_code == 200
    vid = uid_de("v@ejemplo.test")
    op.ok(op.post(f"/api/plataforma/usuarios/{vid}/clave-temporal", {"clave": "temporal-0001"}))
    op.ok(op.put(f"/api/plataforma/usuarios/{vid}", {"activo": False}))
    x = desde(op, "192.0.2.41")
    assert registrar(x, "x@ejemplo.test", invitacion=invitacion_plataforma(op)).status_code == 200
    xid = uid_de("x@ejemplo.test")
    for _ in range(3):                                           # cada entrada anota «sesion.inicio» sin empresa…
        assert desde(op, "192.0.2.41").tc.post("/api/sesion", json={"email": "x@ejemplo.test", "clave": CLAVE}).status_code == 200
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM actividad WHERE accion='sesion.inicio' AND usuario_id=? AND empresa_id IS NULL",
                   (xid,))["n"] == 3
    with db.transaccion(con):                                   # …y así cientos de veces (lo mismo que anota la ruta)
        for i in range(600):
            db.actividad(con, "sesion.inicio" if i % 2 else "usuario.clave_cambiada", usuario_id=xid)
    act = op.ok(op.get("/api/plataforma/actividad"))
    acc = [a["accion"] for a in act]
    assert {"usuario.clave_temporal", "usuario.desactivado", "invitacion.creada", "plataforma.ajuste"} <= set(acc)
    assert acc.count("sesion.inicio") + acc.count("usuario.clave_cambiada") + acc.count("usuario.registro") == 500


def test_fix3_identificadores_con_cifras_unicode_dan_422(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    s = Cliente(TestClient(c.tc.app, raise_server_exceptions=False))
    s.tc.cookies.set("sid", c.tc.cookies.get("sid"))
    s.csrf = c.csrf
    for v in ("²", "①", "３", "٣", " 1 ²"):
        r = s.post(f"/api/empresas/{eid}/certificados/asignar", {"certificado_id": v, "entorno": "simulador"})
        assert r.status_code == 422, (v, r.status_code)
        r = s.post(f"/api/empresas/{eid}/legitimacion", {"metodo": "colaborador_social", "titular_nif": NIF_GESTORIA,
                                                         "titular_nombre": "G", "evidencia_documento_id": v})
        assert r.status_code == 422, (v, r.status_code)
    assert s.get(f"/api/empresas/{eid}/facturas-lote.zip?ids=²,①,99999999999999999999").status_code == 200


# --- ISO-6 ------------------------------------------------------------------------------------------------------------
def _requisito(cli, eid, entorno="pruebas"):
    clave = "requisitos_pruebas" if entorno == "pruebas" else "requisitos"
    return {x["clave"]: x for x in cli.ok(cli.get(f"/api/empresas/{eid}/produccion"))[clave]}["nif_verificado"]


def test_iso6_primer_envio_real_con_certificado_de_tercero_exige_nif_verificado(app_cliente, pruebas_sin_red):
    op = app_cliente
    eop = instalar(op, registro_abierto=True)
    assert _requisito(op, eop)["cumplido"]                          # la dio de alta el operador
    # Un usuario cualquiera da de alta una empresa con un NIF libre y usa el sello de «su» gestoría con una legitimación
    # declarada: la aplicación no puede comprobarlo
    g = desde(op, "192.0.2.60")
    registrar(g, "g@ejemplo.test")
    eg = g.ok(g.post("/api/empresas", EMPRESA_REPLICA))["id"]
    configurar(g, eg)
    mio = subir_mio(g, sello_gestoria())["id"]
    for ent in ("simulador", "pruebas"):
        g.ok(asignar(g, eg, mio, ent))
    legitimar(g, eg)
    req = _requisito(g, eg)
    assert not req["cumplido"] and "operador de la plataforma" in req["detalle"]
    assert not _requisito(g, eg, "produccion")["cumplido"]
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eg,))
    with pytest.raises(certificados.CertificadoNoUtilizable, match="no está acreditado"):
        certificados.para_envio(con, emp, "pruebas")             # también al enviar (el certificado puede cambiarse)
    with pytest.raises(produccion.EntornoNoHabilitado, match="NIF de la empresa acreditado"):
        produccion.comprobar(con, emp, uid_de("g@ejemplo.test"), "pruebas")
    # El simulador nunca se bloquea por esto
    cert, rep = certificados.para_envio(con, emp, "simulador")
    assert rep == {"nif": NIF_GESTORIA, "nombre": "GESTORÍA FICTICIA & ASOCIADOS S.L."}
    # Solo el operador lo verifica; queda en su actividad y en la de la empresa
    url = "/api/plataforma/empresas/verificacion-nif"
    assert g.post(url, {"nif": EMPRESA_REPLICA["nif"], "verificado": True}).status_code == 403
    assert op.post(url, {"nif": "B８２９９１３１6", "verificado": True}).status_code == 422
    assert op.post(url, {"nif": NIF_GESTORIA, "verificado": True}).status_code == 404
    assert op.post(url, {"nif": EMPRESA_REPLICA["nif"], "verificado": "si"}).status_code == 422
    r = op.ok(op.post(url, {"nif": EMPRESA_REPLICA["nif"].lower(), "verificado": True, "nota": "Escritura y DNI comprobados"}))
    assert r["verificado"] and r["razon_social"] == EMPRESA_REPLICA["razon_social"]
    assert _requisito(g, eg)["cumplido"] and certificados.para_envio(con, emp, "pruebas")[1]["nif"] == NIF_GESTORIA
    assert any(a["accion"] == "empresa.nif_verificado" for a in g.ok(g.get(f"/api/empresas/{eg}/actividad")))
    assert any(a["accion"] == "empresa.nif_verificado" for a in op.ok(op.get("/api/plataforma/actividad")))
    assert not op.ok(op.post(url, {"nif": EMPRESA_REPLICA["nif"], "verificado": False}))["verificado"]
    assert not _requisito(g, eg)["cumplido"]
    # Con un certificado de la propia empresa no hace falta: la AEAT comprueba su titularidad en cada envío…
    subir_empresa(g, eg, sello_empresa(), "pruebas")
    assert _requisito(g, eg)["cumplido"]
    # …y una vez que la AEAT acepta un envío hecho con él, el NIF queda acreditado aunque luego se use el de un tercero
    _, bid, _ = borrador_listo(g, eg)
    fid = g.ok(emitir(g, bid))["factura_id"]
    remision._enviar(con, eg, "pruebas", db.filas(con, "SELECT * FROM registros WHERE factura_id=?", (fid,)))
    assert pruebas_sin_red == [remision.ENDPOINTS_SOAP["pruebas"]["sello"]]
    g.ok(asignar(g, eg, mio, "pruebas"))
    assert certificados.nif_acreditado(con, emp)["motivo"] == "envio_certificado_propio"
    assert _requisito(g, eg)["cumplido"] and certificados.para_envio(con, emp, "pruebas")[1]["nif"] == NIF_GESTORIA


def test_g1v4_restaurar_una_copia_no_deshace_una_verificacion_retirada_despues(app_cliente):
    """El operador retira la verificación del NIF después de una copia: al restaurarla sigue retirada (antes volvía a
    quedar verificado sin aviso); una verificación posterior a la copia también se conserva, y el resultado de la
    restauración y la actividad de la plataforma dicen qué empresas cambian."""
    from app import copias
    op = app_cliente
    instalar(op, registro_abierto=True)
    g = desde(op, "192.0.2.60")
    registrar(g, "g@ejemplo.test")
    eg = g.ok(g.post("/api/empresas", EMPRESA_REPLICA))["id"]
    h = desde(op, "192.0.2.61")
    registrar(h, "h@ejemplo.test")
    eh = h.ok(h.post("/api/empresas", {"nif": NIF_PROPIA, "razon_social": "PROPIA S.L.", "domicilio": "C/ X, 1"}))["id"]
    url = "/api/plataforma/empresas/verificacion-nif"
    op.ok(op.post(url, {"nif": EMPRESA_REPLICA["nif"], "verificado": True}))
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    assert not op.ok(op.post(url, {"nif": EMPRESA_REPLICA["nif"], "verificado": False, "nota": "No actúa por la empresa"}))["verificado"]
    assert op.ok(op.post(url, {"nif": NIF_PROPIA, "verificado": True}))["verificado"]
    con = db.conectar()
    # cada verificación o retirada (también la de la empresa del operador, al crearla) ya está en el diario
    assert multiusuario.reconciliar_diario(con, diario.leer()) == 0
    con.close()
    r = copias.restaurar(copia)
    assert sorted((x["empresa_id"], x["verificado"]) for x in r["verificaciones_nif_cambiadas"]) == [(eg, False), (eh, True)]
    assert not op.ok(op.get(f"{url}?nif={EMPRESA_REPLICA['nif']}"))["verificado"]
    assert op.ok(op.get(f"{url}?nif={NIF_PROPIA}"))["verificado"]
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eg,))
    assert not certificados.nif_acreditado(con, emp)["ok"]
    restauradas = [json.loads(a["detalle"]) for a in op.ok(op.get("/api/plataforma/actividad"))
                   if a["accion"] == "empresa.nif_verificacion_restaurada"]
    assert sorted((d["nif"], d["verificado"]) for d in restauradas) == sorted([(EMPRESA_REPLICA["nif"], False), (NIF_PROPIA, True)])
    assert any(a["accion"] == "empresa.nif_verificacion_restaurada" for a in g.ok(g.get(f"/api/empresas/{eg}/actividad")))
    # Volver a restaurar la misma copia da el mismo estado (la versión anotada sigue siendo la más reciente)
    copias.restaurar(copia)
    assert not op.ok(op.get(f"{url}?nif={EMPRESA_REPLICA['nif']}"))["verificado"]
    assert op.ok(op.get(f"{url}?nif={NIF_PROPIA}"))["verificado"]


# --- ISO-7 ------------------------------------------------------------------------------------------------------------
def test_iso7_el_barrido_falla_si_una_ruta_con_identificadores_no_se_prueba(app_cliente):
    import test_16_u_multiusuario as T
    from app.main import crear_app
    extra = APIRouter(prefix="/api")

    @extra.get("/empresas/{eid}/cosas/{xid}")
    def cosa(eid: int, xid: int):
        return {"ok": True}
    app = crear_app()
    app.include_router(extra)
    cli = Cliente(TestClient(app))
    cli.csrf = "x"
    ids = {k: 1 for k in ("eid", "did", "bid", "fid", "rid", "sid", "iid", "cid", "cert_id", "aid", "lid", "n", "uid")}
    with pytest.raises(AssertionError, match=r"/api/empresas/\{eid\}/cosas/\{xid\}"):
        T._barrer(cli, ids)


def _llamadas_a_rol() -> set[str]:
    """«módulo.función» de cada función de app/ que comprueba el rol en una empresa (llama a rol()), según el código."""
    import ast
    out = set()
    for f in sorted((Path(api.__file__).parent).glob("*.py")):
        for nodo in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "rol" for c in ast.walk(nodo)):
                out.add(f"{f.stem}.{nodo.name}")
    return out


def _puntos_finales(app) -> dict:
    """{código de la función de la ruta: plantilla de la ruta} de las rutas /api."""
    pend, out = list(app.routes), {}
    while pend:
        r = pend.pop(0)
        if getattr(r, "original_router", None) is not None:
            pend.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith("/api/") and getattr(r, "endpoint", None) is not None:
            out[r.endpoint.__code__] = r.path
    return out


def test_iso7_el_barrido_detecta_cada_control_de_acceso_que_se_quite(app_cliente, monkeypatch):
    """Mutaciones: se quita, de una en una, la comprobación del rol de CADA función que llama a rol() (y la del propietario
    de «Mis certificados» y la del operador), y el barrido de aislamiento debe detectarlo: una respuesta distinta de 404
    (403 en el panel) en una ruta que pasa por esa función, o un cambio en la base de datos. Antes, las rutas que no
    estaban en la lista aceptaban 403/404/422, y una ruta que sin control de acceso respondía 422 al validar el cuerpo
    pasaba el barrido (certificado_asignar, consulta_aeat, consulta_conciliar…)."""
    import test_16_u_multiusuario as T
    from app import api_multiusuario
    from app.config import ajustes
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    esc = T.escenario_aislamiento(app_cliente)
    app = app_cliente.tc.app
    b = Cliente(TestClient(app, raise_server_exceptions=False))      # un 500 también es un fallo del barrido
    b.tc.cookies.set("sid", esc["b"].tc.cookies.get("sid"))
    b.csrf = esc["b"].csrf
    modulos = (api, api_certificados, api_multiusuario)
    real_rol = api.rol
    rutas = _puntos_finales(app)

    def ruta_actual() -> str | None:
        f = sys._getframe(2)
        while f is not None:
            if f.f_code in rutas:
                return rutas[f.f_code]
            f = f.f_back
        return None

    # 1) Sin mutación: el barrido pasa y alcanza todas las funciones que comprueban el rol
    vistas: dict = {}

    def espia(con, s, eid, minimo="lector"):
        vistas.setdefault(sys._getframe(1).f_code, set()).add(ruta_actual())
        return real_rol(con, s, eid, minimo)
    for m in modulos:
        monkeypatch.setattr(m, "rol", espia)
    res = T._barrer(b, esc["ids"])
    assert not T.fallos_barrido(res), T.fallos_barrido(res)
    nombres = {f"{c.co_filename.rsplit('/', 1)[-1][:-3]}.{c.co_name}": c for c in vistas}
    assert _llamadas_a_rol() <= set(nombres), f"Funciones que comprueban el rol y el barrido no alcanza: {_llamadas_a_rol() - set(nombres)}"
    assert len(nombres) >= 50

    # 2) Cada mutación por separado, sobre los mismos datos (se restauran tras cada una)
    datos = ajustes().data_dir
    foto = datos.parent / "foto_datos"
    shutil.copytree(datos, foto)
    huella = T.huella_datos()
    real_propio, real_super = certificados.propio_de, api.exigir_superadmin

    def sin_propietario(con, usuario_id, cid):
        cu = db.fila(con, "SELECT * FROM certificados_usuario WHERE id=? AND eliminado_en IS NULL", (cid,))
        if not cu:
            raise LookupError("No encontrado")
        return cu
    mutantes = [(n, c) for n, c in sorted(nombres.items())] + [("certificados.propio_de", None), ("api.exigir_superadmin", None)]
    sobreviven = []
    for nombre, codigo in mutantes:
        def mutante(con, s, eid, minimo="lector", _c=codigo):
            return "admin" if sys._getframe(1).f_code is _c else real_rol(con, s, eid, minimo)
        for m in modulos:
            monkeypatch.setattr(m, "rol", mutante)
        monkeypatch.setattr(certificados, "propio_de", sin_propietario if nombre == "certificados.propio_de" else real_propio)
        for m in (api, api_multiusuario):
            monkeypatch.setattr(m, "exigir_superadmin", (lambda con, s: None) if nombre == "api.exigir_superadmin" else real_super)
        fallos = T.fallos_barrido(T._barrer(b, esc["ids"]))
        if codigo is not None:
            fallos = [f for f in fallos if f[1] in vistas[codigo]]          # en una ruta que pasa por esa función
        # …o algo ha cambiado en la base de datos (una ruta que escribe sin control y luego responde 404)
        if not fallos and T.huella_datos() == huella:
            sobreviven.append(nombre)
        shutil.rmtree(datos)
        shutil.copytree(foto, datos)
    assert not sobreviven, f"Controles de acceso que el barrido no detecta si se quitan: {sobreviven}"
