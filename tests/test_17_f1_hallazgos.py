"""Hallazgos menores de las revisiones de los bloques U (multiusuario) y C (certificados): U-R7, U-R8, U-R11, U-R12,
U-R13 (b y c), C-F5, C-F6 y C-F7.

Todo es LOCAL o SIMULADO (simulador de la AEAT, sin red) y con datos ficticios (NIF de provincia 00 o sintéticos de los
demás bloques, correos @ejemplo.test). Los interruptores de remisión real siguen desactivados (conftest).
"""
import json
import sys
from pathlib import Path

import pytest
from conftest import EMPRESA_DEMO, EMPRESA_REPLICA, Cliente, borrador_listo, configurar, preparar_empresa, subir
from fastapi.testclient import TestClient
from test_03_flujo import emitir, enviar, preparar_desde_bytes, variante_pdf
from test_16_c_certificados import (NIF_GESTORIA, asignar, legitimar, peticion, sello_empresa, sello_gestoria,
                                    subir_mio)
from test_16_u_multiusuario import CLAVE, entrar, instalar, nueva_empresa, otro, registrar, uid_de

from app import certificados, config, copias, db, diario, emision, multiusuario, simulador

BIG = 2**70


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    # Todas las peticiones de prueba llegan desde la misma «dirección»: el límite por IP se prueba aparte
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def pdf_demo(numero: str) -> bytes:
    """Documento preparatorio sintético de EMPRESA_DEMO (otra empresa emisora que la réplica)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import generar_pdfs_prueba as g
    return g.dibujar(g.demo(numero=numero, fecha=db.ahora().strftime("%d/%m/%Y")))


# --- U-R7: la clave de idempotencia es de cada empresa ----------------------------------------------------------------
def test_u_r7_clave_de_idempotencia_por_empresa(app_cliente, monkeypatch):
    op = app_cliente
    e_op = instalar(op, registro_abierto=True)            # el operador emite para EMPRESA_DEMO
    configurar(op, e_op, patron="PRB-{n}", siguiente=7)
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    ea = a.ok(nueva_empresa(a, EMPRESA_REPLICA["nif"], razon=EMPRESA_REPLICA["razon_social"],
                            domicilio=EMPRESA_REPLICA["domicilio"]))["id"]
    configurar(a, ea)
    _, ba1, _ = borrador_listo(a, ea)
    ba2 = preparar_desde_bytes(a, ea, "f167.pdf", variante_pdf("167/26"))
    # Factura anterior a la corrección: la clave se guardaba tal cual (sigue valiendo, pero solo en su empresa)
    with monkeypatch.context() as m:
        m.setattr(emision, "clave_guardada", lambda eid, clave: clave, raising=False)
        fa1 = a.ok(emitir(a, ba1, "antigua-1"))["factura_id"]
    fa2 = a.ok(emitir(a, ba2, "factura-1"))["factura_id"]
    con = db.conectar()
    assert db.fila(con, "SELECT clave_idempotencia c FROM facturas WHERE id=?", (fa2,))["c"] == f"{ea}:factura-1"
    # Misma empresa: se conserva el comportamiento (doble clic -> misma factura; otro borrador -> 409)
    assert a.ok(emitir(a, ba1, "antigua-1"))["factura_id"] == fa1
    assert a.ok(emitir(a, ba2, "factura-1"))["factura_id"] == fa2
    for bid, clave in ((ba2, "antigua-1"), (ba1, "factura-1")):
        r = emitir(a, bid, clave)
        assert r.status_code == 409 and "otro borrador" in r.json()["detail"]
    # Otra empresa con las mismas claves: ni choca ni revela que otra empresa las usó
    bo1 = preparar_desde_bytes(op, e_op, "p7.pdf", pdf_demo("PRB-7"))
    bo2 = preparar_desde_bytes(op, e_op, "p8.pdf", pdf_demo("PRB-8"))
    fo1 = op.ok(emitir(op, bo1, "factura-1"))["factura_id"]
    fo2 = op.ok(emitir(op, bo2, "antigua-1"))["factura_id"]
    assert len({fa1, fa2, fo1, fo2}) == 4
    assert op.ok(emitir(op, bo1, "factura-1"))["factura_id"] == fo1
    assert a.ok(emitir(a, ba2, "factura-1"))["factura_id"] == fa2


# --- U-R8: las altas con invitación no cuentan en el límite por IP del registro abierto ------------------------------
def test_u_r8_invitaciones_no_cuentan_en_el_limite_por_ip(app_cliente, monkeypatch):
    op = app_cliente
    e = instalar(op, registro_abierto=False)
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 5)            # valor por defecto
    monkeypatch.setattr(multiusuario.LIMITE_CONSULTA_INVITACION, "maximo", 3)
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_GLOBAL, "maximo", 8)
    tokens = [op.ok(op.post(f"/api/empresas/{e}/invitaciones", {"email": f"empleado{i}@ejemplo.test", "rol": "emisor"}))["token"]
              for i in range(9)]
    # Una oficina da de alta a su personal desde la misma IP: ni la consulta del enlace ni el registro se rechazan por IP
    estados = []
    for i, t in enumerate(tokens[:7]):
        cli = otro(op)
        assert cli.ok(cli.post("/api/invitaciones/consulta", {"token": t}))["valida"] is True
        estados.append(registrar(cli, f"empleado{i}@ejemplo.test", invitacion=t).status_code)
    assert estados == [200] * 7
    # Los enlaces no válidos (adivinados) sí cuentan por IP
    falso = "x" * 43
    assert [registrar(otro(op), "nadie@ejemplo.test", invitacion=falso).status_code for _ in range(6)] == [409] * 5 + [429]
    assert [op.post("/api/invitaciones/consulta", {"token": falso}).status_code for _ in range(4)] == [200] * 3 + [429]
    # Una invitación vigente usada una y otra vez (p. ej. con otro correo) se limita por invitación
    t7 = tokens[7]
    intentos = [registrar(otro(op), f"otro{i}@ejemplo.test", invitacion=t7).status_code for i in range(11)]
    assert intentos == [409] * 10 + [429]
    assert registrar(otro(op), "empleado7@ejemplo.test", invitacion=t7).status_code == 429
    # El límite global de altas por hora se mantiene: la octava alta pasa, la novena no
    multiusuario.LIMITE_POR_INVITACION.reiniciar()
    assert registrar(otro(op), "empleado7@ejemplo.test", invitacion=t7).status_code == 200
    r = registrar(otro(op), "empleado8@ejemplo.test", invitacion=tokens[8])
    assert r.status_code == 429 and "máximo de altas por hora" in r.json()["detail"]


# --- U-R11: el alta directa del operador (`clave`) no mete en la empresa a una cuenta existente ----------------------
def test_u_r11_operador_con_clave_invita_a_las_cuentas_existentes(app_cliente):
    op = app_cliente
    e = instalar(op, registro_abierto=True)
    a = otro(op)
    registrar(a, "a@ejemplo.test")
    r = op.ok(op.post(f"/api/empresas/{e}/miembros", {"email": "a@ejemplo.test", "clave": "cualquier-cosa-1", "rol": "admin"}))
    r_no = op.ok(op.post(f"/api/empresas/{e}/miembros", {"email": "sin-cuenta@ejemplo.test", "rol": "admin"}))
    assert r["accion"] == "invitacion" and r.keys() == r_no.keys() and r["invitacion"].keys() == r_no["invitacion"].keys()
    assert e not in [x["id"] for x in a.ok(a.get("/api/yo"))["empresas"]]
    assert a.get(f"/api/empresas/{e}").status_code == 404
    # Su contraseña no cambia (la de la petición no se usa) y no se le exige cambiarla
    assert entrar(otro(op), "a@ejemplo.test", "cualquier-cosa-1").status_code == 401
    assert entrar(otro(op), "a@ejemplo.test", CLAVE).json()["usuario"]["cambiar_clave"] == 0
    # Solo entra si acepta
    assert a.ok(a.post("/api/invitaciones/aceptar", {"token": r["invitacion"]["token"]}))["empresa_id"] == e
    assert a.ok(a.get(f"/api/empresas/{e}"))["rol"] == "admin"
    # El alta de una cuenta NUEVA con contraseña temporal sigue siendo directa
    r = op.ok(op.post(f"/api/empresas/{e}/miembros", {"email": "nuevo@ejemplo.test", "nombre": "N", "clave": "temporal-123",
                                                      "rol": "lector"}))
    assert r["accion"] == "alta" and r["usuario_id"] == uid_de("nuevo@ejemplo.test")


# --- U-R12: una sesión cerrada después de la copia no vuelve a valer al restaurar -----------------------------------
def test_u_r12_sesiones_cerradas_no_vuelven_al_restaurar(app_cliente):
    op = app_cliente
    instalar(op, registro_abierto=True)
    a, b = otro(op), otro(op)
    registrar(a, "a@ejemplo.test")
    registrar(b, "b@ejemplo.test")
    cookie_a, cookie_b = a.tc.cookies.get("sid"), b.tc.cookies.get("sid")
    con = db.conectar()
    copia = copias.crear_copia(con)
    # Después de la copia: A sale (anotado en el diario) y la sesión de B desaparece sin anotarse (p. ej. purgada)
    assert a.tc.delete("/api/sesion", headers={"X-CSRF-Token": a.csrf}).status_code == 200
    assert [x["datos"]["usuario_id"] for x in diario.leer() if x["tipo"] == "sesion_cerrada"] == [uid_de("a@ejemplo.test")]
    con.execute("DELETE FROM sesiones WHERE usuario_id=?", (uid_de("b@ejemplo.test"),))
    con.close()
    r = copias.restaurar(copia)
    assert r["sesiones_cerradas"] == 2

    def vale(cookie):
        cli = otro(op)
        cli.tc.cookies.set("sid", cookie)
        return cli.get("/api/yo").status_code

    assert vale(cookie_a) == 401 and vale(cookie_b) == 401
    assert op.get("/api/yo").status_code == 200        # abierta antes y después de la copia: sigue abierta
    # Base actual perdida: no se puede contrastar, pero las salidas del diario siguen sin volver
    a2 = otro(op)
    assert entrar(a2, "a@ejemplo.test").status_code == 200
    cookie_a2 = a2.tc.cookies.get("sid")
    con = db.conectar()
    copia2 = copias.crear_copia(con, destino=config.ajustes().data_dir.parent / "copias2")
    con.close()
    assert a2.tc.delete("/api/sesion", headers={"X-CSRF-Token": a2.csrf}).status_code == 200
    for suf in ("", "-wal", "-shm"):
        Path(str(config.ajustes().db_path) + suf).unlink(missing_ok=True)
    r = copias.restaurar(copia2)
    assert r["sesiones_cerradas"] == 1 and vale(cookie_a2) == 401
    assert op.get("/api/yo").status_code == 200


# --- U-R13 (b): una invitación usada después de la copia figura como usada al restaurar ------------------------------
def test_u_r13b_invitacion_usada_tras_la_copia(app_cliente):
    op = app_cliente
    e = instalar(op)
    inv1 = op.ok(op.post(f"/api/empresas/{e}/invitaciones", {"email": "uno@ejemplo.test", "rol": "emisor"}))
    op.ok(op.post(f"/api/empresas/{e}/invitaciones", {"email": "dos@ejemplo.test", "rol": "lector"}))
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    nuevo = otro(op)
    assert registrar(nuevo, "uno@ejemplo.test", invitacion=inv1["token"]).status_code == 200
    assert [x["datos"]["id"] for x in diario.leer() if x["tipo"] == "invitacion_usada"] == [inv1["id"]]
    r = copias.restaurar(copia)
    assert r["invitaciones_usadas"] == 1 and r["invitaciones_anuladas"] == 1
    lista = {i["email"]: i for i in op.ok(op.get(f"/api/empresas/{e}/invitaciones"))}
    assert lista["uno@ejemplo.test"]["estado"] == "usada" and lista["uno@ejemplo.test"]["usada_por"] == uid_de("uno@ejemplo.test")
    assert lista["dos@ejemplo.test"]["estado"] == "revocada" and lista["dos@ejemplo.test"]["revocada_motivo"] == "restauracion"
    assert registrar(otro(op), "uno@ejemplo.test", invitacion=inv1["token"]).status_code == 409
    assert entrar(otro(op), "uno@ejemplo.test").json()["empresas"][0]["id"] == e
    # Un uso que no llegó a anotarse (p. ej. disco del diario lleno) se anota al conciliar en el arranque, una sola vez
    con = db.conectar()
    inv3 = op.ok(op.post(f"/api/empresas/{e}/invitaciones", {"email": "tres@ejemplo.test", "rol": "lector"}))
    con.execute("UPDATE invitaciones SET usada_en=?, usada_por=? WHERE id=?",
                (multiusuario._utc(), uid_de("operador@ejemplo.test"), inv3["id"]))
    anotadas = lambda: [x["datos"]["id"] for x in diario.leer() if x["tipo"] == "invitacion_usada"]  # noqa: E731
    copias.reconciliar_diario(con)
    copias.reconciliar_diario(con)
    assert anotadas() == [inv1["id"], inv3["id"]]


# --- U-R13 (c): la actividad de la plataforma no muestra datos de las empresas --------------------------------------
def test_u_r13c_actividad_de_la_plataforma_sin_datos_de_empresas(app_cliente):
    op = app_cliente
    instalar(op)
    op.ok(op.put("/api/plataforma/ajustes", {"registro_abierto": True}))
    assert entrar(otro(op), "operador@ejemplo.test").status_code == 200
    con = db.conectar()
    db.actividad(con, "error.interno", detalle={"ruta": "/api/empresas/7123/facturas/9123/pdf", "tipo": "KeyError"})
    db.actividad(con, "remision.error", detalle={"error": "ValueError: registro 5123 de la empresa 7123 (B00998872)"})
    db.actividad(con, "diario.error", objeto="factura", objeto_id=9123, detalle={"error": "OSError"})
    db.actividad(con, "diario.error", detalle={"registro_id": 5123, "error": "OSError"})
    db.actividad(con, "certificado_usuario.cargado", usuario_id=1, objeto="certificado_usuario", objeto_id=3,
                 detalle={"titular_nif": NIF_GESTORIA, "representado_nif": EMPRESA_REPLICA["nif"]})
    act = op.ok(op.get("/api/plataforma/actividad"))
    texto = json.dumps(act)
    for dato in ("7123", "9123", "5123", "B00998872", NIF_GESTORIA, EMPRESA_REPLICA["nif"]):
        assert dato not in texto
    por_accion = {}
    for x in act:
        por_accion.setdefault(x["accion"], []).append(x)
    assert json.loads(por_accion["error.interno"][0]["detalle"]) == {"ruta": "/api/empresas/{id}/facturas/{id}/pdf", "tipo": "KeyError"}
    assert json.loads(por_accion["remision.error"][0]["detalle"]) == {"error": "ValueError"}
    assert all(x["objeto_id"] is None and json.loads(x["detalle"]) == {"error": "OSError"} for x in por_accion["diario.error"])
    assert "certificado_usuario.cargado" not in por_accion
    # Lo propio de la plataforma se sigue viendo
    assert {"sesion.inicio", "plataforma.ajuste"} <= set(por_accion)


# --- C-F5: tipos JSON erróneos y enteros fuera de rango -> 422 (nunca 500) -----------------------------------------
TEXTO = [12345678, ["x"], {"a": 1}, BIG, True]
ENTERO = ["texto", ["1"], {"a": 1}, BIG, True, 1.5]
BOOL = [1, "si", ["x"], {"a": 1}, BIG]
DIAS = ["texto", [7], {"a": 7}, BIG, True]
IDS = [5, "1", {"a": 1}, [BIG], ["1"], [True]]
TEXTO_O_ENTERO = [12345678, [2026], {"a": 1}, BIG, True]

# (identificador, método, ruta, cuerpo válido, {campo: valores erróneos}, estados admitidos del cuerpo válido)
CUERPOS = [
    ("registro", "POST", "/api/registro", {"email": "nuevo@ejemplo.test", "nombre": "N", "clave": "clave-segura-123"},
     {"email": TEXTO, "nombre": TEXTO, "clave": TEXTO, "invitacion": TEXTO}, {403}),
    ("invitacion_consulta", "POST", "/api/invitaciones/consulta", {"token": "x" * 43}, {"token": TEXTO}, {200}),
    ("invitacion_aceptar", "POST", "/api/invitaciones/aceptar", {"token": "x" * 43}, {"token": TEXTO}, {409}),
    ("empresa_invitar", "POST", "/api/empresas/{eid}/invitaciones",
     {"email": "inv@ejemplo.test", "rol": "lector", "relacion": "asesor", "dias": 7},
     {"email": TEXTO, "rol": TEXTO, "relacion": TEXTO, "dias": DIAS}, {200}),
    ("miembros", "POST", "/api/empresas/{eid}/miembros", {"email": "miembro@ejemplo.test", "rol": "lector"},
     {"email": TEXTO, "rol": TEXTO, "nombre": TEXTO, "clave": TEXTO, "relacion": TEXTO}, {200}),
    ("yo_clave", "PUT", "/api/yo/clave", {"clave_actual": "incorrecta-000", "clave_nueva": "otra-clave-456"},
     {"clave_actual": TEXTO, "clave_nueva": TEXTO}, {403}),
    ("plataforma_ajustes", "PUT", "/api/plataforma/ajustes", {"registro_abierto": False, "invitaciones_empresa_crean_cuentas": False},
     {"registro_abierto": BOOL, "invitaciones_empresa_crean_cuentas": BOOL}, {200}),
    ("plataforma_usuario", "PUT", "/api/plataforma/usuarios/{uid}", {"activo": True}, {"activo": BOOL}, {200}),
    ("plataforma_clave_temporal", "POST", "/api/plataforma/usuarios/{uid}/clave-temporal", {"clave": "temporal-0001"},
     {"clave": TEXTO}, {200}),
    ("plataforma_invitar", "POST", "/api/plataforma/invitaciones", {"email": "plat@ejemplo.test", "dias": 3},
     {"email": TEXTO, "dias": DIAS}, {200}),
    ("certificado_asignar", "POST", "/api/empresas/{eid}/certificados/asignar", {"certificado_id": "{cid}", "entorno": "simulador"},
     {"certificado_id": ENTERO, "entorno": TEXTO}, {200}),
    ("legitimacion", "POST", "/api/empresas/{eid}/legitimacion",
     {"metodo": "colaborador_social", "titular_nif": NIF_GESTORIA, "titular_nombre": "GESTORÍA FICTICIA & ASOCIADOS S.L.",
      "evidencia_documento_id": "{did}", "notas": "Acuerdo de colaboración"},
     {"metodo": TEXTO, "titular_nif": TEXTO, "titular_nombre": TEXTO, "evidencia_documento_id": ENTERO, "notas": TEXTO}, {200}),
    ("series_proponer", "POST", "/api/series/proponer", {"num_serie": "042/A26"}, {"num_serie": TEXTO}, {200}),
    ("consulta", "POST", "/api/empresas/{eid}/consulta", {"entorno": "simulador", "ejercicio": "2026", "periodo": "11"},
     {"entorno": TEXTO, "ejercicio": TEXTO_O_ENTERO, "periodo": TEXTO_O_ENTERO, "num_serie": TEXTO}, {200}),
    ("conciliar", "POST", "/api/empresas/{eid}/consulta/conciliar", {"confirmo": True, "entorno": "simulador", "registro_ids": [999999]},
     {"confirmo": BOOL, "entorno": TEXTO, "registro_ids": IDS}, {404}),
]

# Rutas con identificadores: cada uno, por turno, con un entero fuera del rango de SQLite
RUTAS = [
    ("DELETE", "/api/invitaciones/{iid}", None),
    ("GET", "/api/empresas/{eid}/invitaciones", None),
    ("POST", "/api/empresas/{eid}/invitaciones", {"email": "inv@ejemplo.test", "rol": "lector"}),
    ("POST", "/api/empresas/{eid}/miembros", {"email": "miembro@ejemplo.test", "rol": "lector"}),
    ("DELETE", "/api/empresas/{eid}/miembros/{uid}", None),
    ("PUT", "/api/plataforma/usuarios/{uid}", {"activo": True}),
    ("POST", "/api/plataforma/usuarios/{uid}/clave-temporal", {"clave": "temporal-0001"}),
    ("DELETE", "/api/yo/certificados/{cid}", None),
    ("DELETE", "/api/yo/certificados/{cid}/asignaciones/{aid}", None),
    ("GET", "/api/yo/certificados/{cid}/usos", None),
    ("GET", "/api/empresas/{eid}/certificados", None),
    ("POST", "/api/empresas/{eid}/certificados/asignar", {"certificado_id": "{cid}", "entorno": "simulador"}),
    ("POST", "/api/empresas/{eid}/certificados/{cert_id}/activar", None),
    ("DELETE", "/api/empresas/{eid}/certificados/asignaciones/{aid}", None),
    ("GET", "/api/empresas/{eid}/certificados/seleccion", None),
    ("POST", "/api/empresas/{eid}/legitimacion", {"metodo": "colaborador_social", "titular_nif": NIF_GESTORIA,
                                                  "titular_nombre": "G", "evidencia_documento_id": "{did}"}),
    ("GET", "/api/empresas/{eid}/legitimaciones", None),
    ("DELETE", "/api/empresas/{eid}/legitimaciones/{lid}", None),
    ("POST", "/api/empresas/{eid}/consulta", {"entorno": "simulador", "ejercicio": "2026", "periodo": "11"}),
    ("POST", "/api/empresas/{eid}/consulta/conciliar", {"confirmo": True, "entorno": "simulador", "registro_ids": [1]}),
    ("POST", "/api/empresas/{eid}/certificados", "multipart"),
]


@pytest.fixture(scope="module")
def plataforma(tmp_path_factory):
    """Una instalación para todo el barrido: operador administrador de una empresa configurada, una segunda cuenta, un
    certificado de «Mis certificados» asignado, un documento, una legitimación y una invitación."""
    from app.main import crear_app
    tmp = tmp_path_factory.mktemp("c_f5")
    mp = pytest.MonkeyPatch()
    mp.setenv("VERIFACTU_DATA_DIR", str(tmp / "data"))
    mp.setenv("VERIFACTU_DIARIO_DIR", str(tmp / "diario"))
    mp.setenv("VERIFACTU_CLAVE_MAESTRA_FICHERO", str(tmp / "secretos" / "clave.bin"))
    for nombre in ("LIMITE_REGISTRO_IP", "LIMITE_REGISTRO_INTENTOS", "LIMITE_CONSULTA_INVITACION", "LIMITE_POR_INVITACION"):
        if hasattr(multiusuario, nombre):
            mp.setattr(getattr(multiusuario, nombre), "maximo", 100000)
    config.configurar(config.Ajustes())
    try:
        with TestClient(crear_app(), raise_server_exceptions=False) as tc:
            c = Cliente(tc)
            eid = preparar_empresa(c)
            uid = c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "dos@ejemplo.test", "nombre": "D",
                                                               "clave": "clave-dos-12345", "rol": "lector"}))["usuario_id"]
            cid = subir_mio(c, sello_gestoria())["id"]
            aid = c.ok(asignar(c, eid, cid, "simulador"))["asignacion_id"]
            did = subir(c, eid, "demo_recibida.pdf")["documento_id"]
            lid = legitimar(c, eid)
            iid = c.ok(c.post(f"/api/empresas/{eid}/invitaciones", {"email": "pendiente@ejemplo.test", "rol": "lector"}))["id"]
            ids = {"eid": eid, "uid": uid, "cid": cid, "aid": aid, "did": did, "lid": lid, "iid": iid, "cert_id": 1}
            yield c, ids
    finally:
        mp.undo()
        config.configurar(config.Ajustes())


def _rellenar(v, ids):
    if isinstance(v, str) and v.startswith("{") and v.endswith("}") and v[1:-1] in ids:
        return ids[v[1:-1]]
    return v


def _pedir(c, metodo, ruta, cuerpo):
    if cuerpo == "multipart":
        return c.tc.post(ruta, files={"archivo": ("c.p12", b"x", "application/x-pkcs12")}, data={"clave": "x", "entorno": "pruebas"},
                         headers=c._h())
    return c.tc.request(metodo, ruta, json=cuerpo, headers=c._h())


@pytest.mark.parametrize("caso", CUERPOS + [("ruta",) + r for r in RUTAS], ids=lambda x: x[0] if x[0] != "ruta" else f"{x[1]} {x[2]}")
def test_c_f5_tipos_y_rangos_dan_422(plataforma, caso):
    c, ids = plataforma
    if caso[0] == "ruta":
        _, metodo, plantilla, cuerpo = caso
        cuerpo = {k: _rellenar(v, ids) for k, v in cuerpo.items()} if isinstance(cuerpo, dict) else cuerpo
        malos = []
        for clave in [k for k in ids if "{" + k + "}" in plantilla]:
            ruta = plantilla.format(**dict(ids, **{clave: BIG}))
            r = _pedir(c, metodo, ruta, cuerpo)
            if r.status_code != 422:
                malos.append((clave, r.status_code, r.text[:120]))
        assert not malos, malos
        return
    _, metodo, plantilla, valido, campos, esperado = caso
    ruta = plantilla.format(**ids)
    valido = {k: _rellenar(v, ids) for k, v in valido.items()}
    control = _pedir(c, metodo, ruta, valido)
    assert control.status_code in esperado, (control.status_code, control.text[:200])   # el barrido no es vacuo
    malos = []
    for campo, valores in campos.items():
        for v in valores:
            r = _pedir(c, metodo, ruta, dict(valido, **{campo: v}))
            if r.status_code != 422:
                malos.append((campo, repr(v)[:30], r.status_code, r.text[:120]))
    r = _pedir(c, metodo, ruta, [valido])            # cuerpo que no es un objeto JSON
    if r.status_code != 422:
        malos.append(("<cuerpo lista>", "", r.status_code, r.text[:120]))
    assert not malos, malos


# --- C-F6: el archivo cifrado de un certificado eliminado no queda en la base restaurada ------------------------------
def test_c_f6_cifrado_eliminado_no_queda_en_la_base_restaurada(app_cliente):
    c = app_cliente
    preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    con = db.conectar()
    blob = db.fila(con, "SELECT blob_cifrado FROM certificados_usuario WHERE id=?", (cid,))["blob_cifrado"]
    copia = copias.crear_copia(con)
    con.close()
    assert c.tc.delete(f"/api/yo/certificados/{cid}", headers=c._h()).status_code == 200   # después de la copia
    r = copias.restaurar(copia)
    assert r["certificados"]["conflictos"] == []
    con = db.conectar()
    fila = db.fila(con, "SELECT blob_cifrado, eliminado_en FROM certificados_usuario WHERE id=?", (cid,))
    assert fila["blob_cifrado"] is None and fila["eliminado_en"]
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    dbp = config.ajustes().db_path
    trozo = blob[40:120]
    for suf in ("", "-wal"):
        p = Path(str(dbp) + suf)
        assert not p.exists() or p.read_bytes().count(trozo) == 0, suf


# --- C-F7: nombre del representante tomado del certificado, aviso si difiere y ayudas 4123/4124 ------------------
def test_c_f7_nombre_del_representante_del_certificado_y_ayudas(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]            # O = «GESTORÍA FICTICIA & ASOCIADOS S.L.»
    c.ok(asignar(c, eid, cid, "simulador"))
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    # Mismo nombre escrito de otra forma (tildes, puntuación, mayúsculas): sin aviso
    lid = legitimar(c, eid, nombre="Gestoria Ficticia Asociados, SL", metodo="apoderamiento_aeat")
    leg = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["simulador"]["legitimacion"]
    assert leg["representante"] == {"nif": NIF_GESTORIA, "nombre": "GESTORÍA FICTICIA & ASOCIADOS S.L."}
    assert leg["aviso_nombre"] is None
    # Nombre declarado distinto (p. ej., una errata): se envía el del certificado y se avisa
    assert c.tc.delete(f"/api/empresas/{eid}/legitimaciones/{lid}", headers=c._h()).status_code == 200
    legitimar(c, eid, nombre="GESTORIA FITICIA Y ASOCIADOS SL", metodo="apoderamiento_aeat")
    leg = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["simulador"]["legitimacion"]
    assert leg["representante"]["nombre"] == "GESTORÍA FICTICIA & ASOCIADOS S.L."
    assert "no coincide" in leg["aviso_nombre"] and leg["aviso_nombre"] in leg["detalle"]
    _, rep = certificados.para_envio(con, emp, "simulador")
    assert rep == {"nif": NIF_GESTORIA, "nombre": "GESTORÍA FICTICIA & ASOCIADOS S.L."}
    # Persona física: «APELLIDOS NOMBRE», como en el censo (y no «NOMBRE APELLIDOS»)
    pf = {"tipo": "persona_fisica", "titular_nif": "22222222J", "titular_nombre": "ANA GARCIA LOPEZ",
          "sujeto": "2.5.4.5=IDCES-22222222J,2.5.4.4=GARCIA LOPEZ,2.5.4.42=ANA,CN=GARCIA LOPEZ ANA - 22222222J"}
    assert certificados.nombre_certificado(pf) == "GARCIA LOPEZ ANA"
    assert certificados.nombre_certificado(dict(pf, sujeto="CN=GARCIA LOPEZ ANA - 22222222J")) == "GARCIA LOPEZ ANA"
    # Ayudas en español para los rechazos de la cabecera 4123 y 4124 (como la del 4112)
    for cod in ("4112", "4123", "4124"):
        assert certificados.con_ayuda(f"{cod}: texto de la AEAT").endswith(certificados.AYUDAS_AEAT[cod])
    assert certificados.con_ayuda("4105: otro error") == "4105: otro error"
    # El simulador reproduce el 4124 con representante: los registros quedan bloqueados con la ayuda
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    simulador.fijar_modo("representante_nombre_no_censado", 1)
    assert enviar(eid)["resultado"] == "fault_cliente"
    reg, sobre = peticion(con, fid)
    assert reg["estado_envio"] == "bloqueado" and "4124" in reg["descripcion_error"]
    assert certificados.AYUDA_4124[:60] in reg["descripcion_error"] and b"Representante" in sobre
    # 4123 igual; sin Representante (certificado propio) el modo no afecta
    c.ok(c.post(f"/api/registros/{reg['id']}/reintentar"))
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    simulador.fijar_modo("representante_nif_no_censado", 1)
    enviar(eid)
    reg, _ = peticion(con, fid)
    assert reg["estado_envio"] == "bloqueado" and "4123" in reg["descripcion_error"]
    assert certificados.AYUDA_4123[:60] in reg["descripcion_error"]
    c.ok(asignar(c, eid, subir_mio(c, sello_empresa())["id"], "simulador"))
    c.ok(c.post(f"/api/registros/{reg['id']}/reintentar"))
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    simulador.fijar_modo("representante_nif_no_censado", 1)
    enviar(eid)
    reg, sobre = peticion(con, fid)
    assert reg["estado_envio"] == "aceptado" and b"Representante" not in sobre
