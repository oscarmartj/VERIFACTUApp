"""Carril B — paquete 3 y modo práctica: alta de la empresa guiada por su certificado (vista previa cifrada, empresa con
los datos del certificado, certificado único para pruebas y producción), modo práctica a partir de una factura, avisos de
caducidad y cruces de cada factura con el certificado y el domicilio de la empresa.

Todo es LOCAL y SINTÉTICO: certificados autofirmados generados en la prueba, NIF ficticios y el simulador local. Los
interruptores de remisión real siguen desactivados (conftest)."""
import base64
import datetime as dt
import json
import shutil

import pytest
from conftest import EMPRESA_DEMO, EMPRESA_REPLICA, Cliente, borrador_listo, configurar, pdf, preparar_empresa, subir
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from test_03_flujo import emitir
from test_16_c_certificados import CLAVE_CERT, p12, sello_empresa, subir_empresa
from test_16_u_multiusuario import CLAVE, instalar, registrar

from app import api, certificados, config, copias, db, diario, multiusuario, produccion
from app.nif import completar_cif

NIF_REPLICA = EMPRESA_REPLICA["nif"]
NIF_NUEVA = completar_cif("B", "0077123")
DNI_AUTONOMO = "33333333P"


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def cert_persona(*, dni=DNI_AUTONOMO, gn="LUCÍA", sn="FICTICIA PRUEBAS", org_id=None, org=None, dias=(-30, 365)):
    """Certificado de persona física (autónomo) o, con org_id, de representante de persona jurídica (autofirmado)."""
    k = ec.generate_private_key(ec.SECP256R1())
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, f"{sn} {gn} - {dni}"), x509.NameAttribute(NameOID.GIVEN_NAME, gn),
             x509.NameAttribute(NameOID.SURNAME, sn), x509.NameAttribute(NameOID.SERIAL_NUMBER, f"IDCES-{dni}")]
    if org_id:
        attrs += [x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
                  x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), f"VATES-{org_id}")]
    nombre = x509.Name(attrs)
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora + dt.timedelta(days=dias[0]))
            .not_valid_after(ahora + dt.timedelta(days=dias[1])).sign(k, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"x", k, cert, None, BestAvailableEncryption(CLAVE_CERT.encode()))


def usuario(op: Cliente, email: str, nombre="Persona Ficticia") -> Cliente:
    """Cuenta normal (no operador) creada con una invitación de la plataforma."""
    tok = op.ok(op.post("/api/plataforma/invitaciones", {"email": email}))["token"]
    u = Cliente(TestClient(op.tc.app))
    assert registrar(u, email, nombre=nombre, invitacion=tok).status_code == 200
    return u


def previa(c: Cliente, datos: bytes, clave=CLAVE_CERT, codigo=200):
    r = c.tc.post("/api/alta/certificados", files={"archivo": ("empresa.p12", datos, "application/x-pkcs12")},
                  data={"clave": clave}, headers={"X-CSRF-Token": c.csrf})
    return c.ok(r, codigo)


def crear(c: Cliente, token):
    return c.post("/api/alta/empresa", {"previa": token})


def leer_practica(c: Cliente, nombre="replica_166-26.pdf", datos=None):
    return c.tc.post("/api/alta/practica/documentos", files={"archivo": (nombre, datos or pdf(nombre), "application/pdf")},
                     headers={"X-CSRF-Token": c.csrf})


# --- Paquete 3: alta guiada por el certificado ---------------------------------------------------------------------------
def test_alta_con_el_certificado_de_la_empresa(app_cliente):
    op = app_cliente
    instalar(op)
    marta = usuario(op, "marta@ejemplo.test", "Marta Ficticia")
    datos = sello_empresa()
    v = previa(marta, datos)
    # La vista previa dice qué empresa identifica el certificado y hasta cuándo vale; nunca devuelve secretos
    assert v["empresa"] == {"nif": NIF_REPLICA, "razon_social": EMPRESA_REPLICA["razon_social"], "relacion": "administrador"}
    assert v["puede_crear"] and v["vigente"] and v["avisos"] == [] and "sin acreditar" in v["nota"]
    assert CLAVE_CERT not in json.dumps(v) and "blob" not in json.dumps(v) and v["minutos"] == certificados.MINUTOS_PREVIA
    con = db.conectar()
    fila = db.fila(con, "SELECT * FROM certificados_previos")
    assert fila["token_hash"] != v["previa"] and fila["blob_cifrado"][:2] == b"v1"
    assert CLAVE_CERT.encode() not in fila["blob_cifrado"] and datos[:64] not in fila["blob_cifrado"]
    assert db.fila(con, "SELECT COUNT(*) n FROM empresas WHERE nif=?", (NIF_REPLICA,))["n"] == 0   # aún no se crea nada
    # «Crear la empresa con estos datos»
    e = marta.ok(crear(marta, v["previa"]))
    assert (e["nif"], e["razon_social"], e["alta_origen"], e["tipo_contribuyente"]) == (
        NIF_REPLICA, EMPRESA_REPLICA["razon_social"], "certificado", "sociedad_IS")
    assert e["rol"] == "admin" and e["relacion_usuario"] == "administrador" and not e["modo_practica"]
    assert db.fila(con, "SELECT COUNT(*) n FROM certificados_previos")["n"] == 0                   # usada: se borra
    # Un solo certificado para los dos entornos de la AEAT, propio de la empresa
    c = db.fila(con, "SELECT * FROM certificados WHERE empresa_id=?", (e["id"],))
    assert c["entorno"] == "todos" and c["activo"] == 1 and CLAVE_CERT.encode() not in c["blob_cifrado"]
    sel = marta.ok(marta.get(f"/api/empresas/{e['id']}/certificados/seleccion"))
    for ent in ("pruebas", "produccion"):
        assert sel[ent]["certificado_id"] == c["id"] and sel[ent]["legitimacion"]["propio"], sel
    assert sel["simulador"] is None                                    # el simulador no usa certificado
    req = {x["clave"]: x for x in marta.ok(marta.get(f"/api/empresas/{e['id']}/produccion"))["requisitos"]}
    assert req["certificado"]["cumplido"] and "propio de la empresa" in req["certificado"]["detalle"]
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (e["id"],))
    cert, rep = certificados.para_envio(con, empresa, "produccion")
    assert cert["id"] == c["id"] and rep is None
    with certificados.ficheros_pem(cert) as (cp, _):                    # se descifra con su contexto («todos»)
        assert open(cp, "rb").read().startswith(b"-----BEGIN CERTIFICATE")
    # Un certificado autofirmado no acredita el NIF: sigue «sin acreditar» hasta el operador o una aceptación de la AEAT
    assert empresa["nif_verificado_en"] is None and not certificados.nif_acreditado(con, empresa)["ok"]
    # El identificador ya no vale
    assert crear(marta, v["previa"]).status_code == 410
    # Ni la contraseña ni el archivo aparecen en la actividad, el diario ni las respuestas
    txt = json.dumps(db.filas(con, "SELECT * FROM actividad"), ensure_ascii=False) + diario.ruta().read_text(encoding="utf-8")
    fragmento = base64.b64encode(datos).decode()[40:80]
    assert CLAVE_CERT not in txt and fragmento not in txt and "blob_cifrado" not in txt
    assert '"tabla": "certificados"' in txt and '"entorno": "todos"' in txt     # en el diario, como los demás


def test_vista_previa_no_revela_empresas_existentes_y_el_409_es_el_de_siempre(app_cliente):
    op = app_cliente
    instalar(op)
    a = usuario(op, "a@ejemplo.test")
    a.ok(a.post("/api/empresas", EMPRESA_REPLICA))                     # la empresa ya existe (de A)
    b = usuario(op, "b@ejemplo.test")
    libre = previa(b, p12(org_id=NIF_NUEVA, org="EMPRESA LIBRE FICTICIA S.L."))
    ocupada = previa(b, sello_empresa())
    assert set(libre) == set(ocupada) and ocupada["puede_crear"] and ocupada["avisos"] == []
    r = crear(b, ocupada["previa"])
    manual = b.post("/api/empresas", EMPRESA_REPLICA)
    assert r.status_code == manual.status_code == 409 and r.json() == manual.json() == {"detail": api.EMPRESA_YA_DADA_DE_ALTA}
    con = db.fila  # noqa: F841
    c = db.conectar()
    assert db.fila(c, "SELECT COUNT(*) n FROM certificados_previos WHERE usuario_id=(SELECT id FROM usuarios WHERE email='b@ejemplo.test')"
                   )["n"] == 1                                         # solo la libre; la usada se borró
    assert db.fila(c, "SELECT COUNT(*) n FROM certificados")["n"] == 0 and b.ok(b.get("/api/yo"))["empresas"] == []
    # La vista previa de otro usuario no sirve
    assert crear(a, libre["previa"]).status_code == 410
    assert b.ok(crear(b, libre["previa"]))["nif"] == NIF_NUEVA


def test_representante_autonomo_caducidad_y_no_vigente(app_cliente):
    op = app_cliente
    instalar(op)
    u = usuario(op, "rep@ejemplo.test", "Otra Persona Distinta")
    # Representante de persona jurídica: empresa = la representada; relación «representante»; aviso por el nombre
    v = previa(u, cert_persona(dni="44444444A", org_id=NIF_NUEVA, org="TALLERES FICTICIOS NORTE S.L."))
    assert v["empresa"] == {"nif": NIF_NUEVA, "razon_social": "TALLERES FICTICIOS NORTE S.L.", "relacion": "representante"}
    assert v["representante"] == {"nif": "44444444A", "nombre": "FICTICIA PRUEBAS LUCÍA"}
    assert any("representante «FICTICIA PRUEBAS LUCÍA»" in x and "Otra Persona Distinta" in x for x in v["avisos"]), v
    e = u.ok(crear(u, v["previa"]))
    assert e["relacion_usuario"] == "representante"
    # El mismo nombre (en otro orden y sin tildes) no avisa
    u2 = usuario(op, "lucia@ejemplo.test", "Lucia Ficticia Pruebas")
    v2 = previa(u2, cert_persona(dni="55555555K", org_id=completar_cif("B", "0066111"), org="OTRA FICTICIA S.L."))
    assert v2["avisos"] == [], v2
    # Autónomo (persona física): su NIF y su nombre; tipo de contribuyente deducido del NIF
    v3 = previa(u2, cert_persona(dias=(-30, 12)))
    assert v3["empresa"] == {"nif": DNI_AUTONOMO, "razon_social": "FICTICIA PRUEBAS LUCÍA", "relacion": "administrador"}
    assert any("caduca el" in x and "dentro de 1" in x for x in v3["avisos"]), v3      # aviso 30 días antes
    e3 = u2.ok(crear(u2, v3["previa"]))
    assert e3["tipo_contribuyente"] == "persona_fisica_IRPF" and any("caduca el" in x for x in e3["avisos_configuracion"])
    req = {x["clave"]: x for x in u2.ok(u2.get(f"/api/empresas/{e3['id']}/produccion"))["requisitos_pruebas"]}
    assert "caduca el" in req["certificado"]["detalle"]
    # Caducado: se ve en la vista previa y no se crea nada
    v4 = previa(u2, cert_persona(dni="66666666Q", dias=(-60, -1)))
    assert not v4["puede_crear"] and any("no está vigente" in x for x in v4["avisos"])
    r = crear(u2, v4["previa"])
    assert r.status_code == 422 and db.fila(db.conectar(), "SELECT 1 FROM empresas WHERE nif='66666666Q'") is None
    # Contraseña errónea: 422 y nada guardado
    antes = db.fila(db.conectar(), "SELECT COUNT(*) n FROM certificados_previos")["n"]
    previa(u2, sello_empresa(), clave="otra", codigo=422)
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM certificados_previos")["n"] == antes


def test_vista_previa_caduca_y_se_purga(app_cliente):
    op = app_cliente
    instalar(op)
    u = usuario(op, "u@ejemplo.test")
    v = previa(u, sello_empresa())
    con = db.conectar()
    con.execute("UPDATE certificados_previos SET expira_en='2000-01-01T00:00:00+00:00'")
    assert crear(u, v["previa"]).status_code == 410
    assert certificados.purgar_previas(con) == 1 and db.fila(con, "SELECT COUNT(*) n FROM certificados_previos")["n"] == 0
    # Como mucho 3 pendientes por usuario; el límite por hora frena el abuso
    for _ in range(5):
        previa(u, sello_empresa())
    assert db.fila(con, "SELECT COUNT(*) n FROM certificados_previos")["n"] == 3
    multiusuario.LIMITE_ALTA_GUIADA.maximo, maximo = 6, multiusuario.LIMITE_ALTA_GUIADA.maximo
    try:
        assert previa(u, sello_empresa(), codigo=429)
    finally:
        multiusuario.LIMITE_ALTA_GUIADA.maximo = maximo


def test_certificado_para_los_dos_entornos_convive_con_los_de_un_entorno(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    todos = subir_empresa(c, eid, sello_empresa(), entorno="todos")
    assert todos["entorno"] == "todos" and todos["en_uso"]
    pruebas = subir_empresa(c, eid, sello_empresa(), entorno="pruebas")        # prevalece en pruebas…
    sel = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))
    assert sel["pruebas"]["certificado_id"] == pruebas["id"] and sel["produccion"]["certificado_id"] == todos["id"]
    lista = {x["id"]: x for x in c.ok(c.get(f"/api/empresas/{eid}/certificados")) if x.get("id")}
    assert lista[todos["id"]]["en_uso"] and lista[pruebas["id"]]["en_uso"]
    # …y volver a usar el de los dos entornos sustituye al de pruebas
    r = c.ok(c.post(f"/api/empresas/{eid}/certificados/{todos['id']}/activar"))
    assert r["certificado_id"] == todos["id"]
    sel = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))
    assert sel["pruebas"]["certificado_id"] == sel["produccion"]["certificado_id"] == todos["id"]
    # Copia y restauración: el estado «en uso» del certificado de los dos entornos se conserva
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    subir_empresa(c, eid, sello_empresa(), entorno="todos")                  # posterior a la copia
    r = copias.restaurar(copia)
    assert [x["entorno"] for x in r["certificados"]["certificados_empresa_por_recargar"]] == ["todos"]
    assert c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["produccion"] is None   # no vuelve en silencio
    assert c.ok(c.post(f"/api/empresas/{eid}/certificados/{todos['id']}/activar"))["certificado_id"] == todos["id"]


def test_migracion_0009_conserva_los_certificados_de_la_version_anterior(entorno_tmp, monkeypatch):
    previas = entorno_tmp / "migraciones_0008"
    previas.mkdir()
    for f in sorted(db.MIGRACIONES_DIR.glob("*.sql")):
        if f.name < "0009":
            shutil.copy(f, previas / f.name)
    con = db.conectar()
    monkeypatch.setattr(db, "MIGRACIONES_DIR", previas)
    db.migrar(con)
    con.execute("INSERT INTO usuarios (email, nombre, hash_clave, creado_en) VALUES ('x@ejemplo.test','X','h','2026-01-01')")
    con.execute("INSERT INTO empresas (nif, razon_social, creado_en) VALUES (?, 'X', '2026-01-01')", (NIF_REPLICA,))
    con.execute("""INSERT INTO certificados (empresa_id, entorno, sujeto, emisor, numero_serie, valido_desde, valido_hasta, tipo,
                   huella_cert, blob_cifrado, activo, subido_por, subido_en, version) VALUES
                   (1, 'pruebas', 's', 'e', '1', '2026-01-01', '2030-01-01', 'persona_juridica', 'h', X'7631', 1, 1, '2026-01-01', 3)""")
    monkeypatch.undo()
    assert db.migrar(con)[0] == "0009_alta_guiada.sql"            # (y las posteriores: 0010_preferencias.sql…)
    f = db.fila(con, "SELECT * FROM certificados")
    assert (f["entorno"], f["activo"], f["version"], f["blob_cifrado"]) == ("pruebas", 1, 3, b"v1")
    con.execute("UPDATE certificados SET entorno='todos'")
    with pytest.raises(Exception):
        con.execute("UPDATE certificados SET entorno='simulador'")
    assert {"alta_origen"} <= {c[1] for c in con.execute("PRAGMA table_info(empresas)")}


# --- Modo práctica -------------------------------------------------------------------------------------------------------
def test_modo_practica_propone_los_datos_de_la_factura_y_solo_simula(app_cliente):
    op = app_cliente
    instalar(op)
    u = usuario(op, "practica@ejemplo.test")
    antes = {t: db.fila(db.conectar(), f"SELECT COUNT(*) n FROM {t}")["n"] for t in ("documentos", "extracciones", "empresas")}
    ficheros = sorted(p.name for p in config.ajustes().data_dir.rglob("*") if p.is_file())
    r = u.ok(leer_practica(u))
    # Propone el emisor leído (para confirmar o corregir); no guarda nada
    assert r["emisor"]["nif"] == NIF_REPLICA and "INGENIER" in (r["emisor"]["nombre"] or "").upper(), r
    assert r["emisor"]["domicilio"] and r["confianza"]["nif"] is not None
    despues = {t: db.fila(db.conectar(), f"SELECT COUNT(*) n FROM {t}")["n"] for t in antes}
    assert despues == antes and sorted(p.name for p in config.ajustes().data_dir.rglob("*") if p.is_file()) == ficheros
    assert leer_practica(u, "x.pdf", b"no es un pdf").status_code == 422
    # Confirma (con un cambio) y crea la empresa en modo práctica
    e = u.ok(u.post("/api/empresas", {"nif": r["emisor"]["nif"], "razon_social": EMPRESA_REPLICA["razon_social"],
                                      "domicilio": r["emisor"]["domicilio"], "modo": "practica"}))
    assert e["alta_origen"] == "practica" and e["modo_practica"] and e["tipo_contribuyente"] == "sociedad_IS"
    assert u.post("/api/empresas", {"nif": NIF_NUEVA, "razon_social": "X", "modo": "otro"}).status_code == 422
    p = u.ok(u.get(f"/api/empresas/{e['id']}/produccion"))
    assert p["modo_practica"] and not p["pruebas_habilitadas"]
    req = {x["clave"]: x for x in p["requisitos_pruebas"]}
    assert not req["certificado"]["cumplido"] and req["certificado"]["detalle"] == produccion.TEXTO_MODO_PRACTICA
    # Trabaja en el simulador; pruebas y producción siguen cerradas
    configurar(u, e["id"])
    _, bid, b = borrador_listo(u, e["id"])
    assert emitir(u, bid, "pr-1", entorno="pruebas").status_code == 403
    assert u.ok(emitir(u, bid))["factura_id"]
    # Al cargar el certificado de la empresa (para los dos entornos) sale del modo práctica
    subir_empresa(u, e["id"], sello_empresa(), entorno="todos")
    assert not u.ok(u.get(f"/api/empresas/{e['id']}"))["modo_practica"]


def test_datos_propuestos_desde_la_primera_factura(app_cliente):
    op = app_cliente
    instalar(op)
    u = usuario(op, "datos@ejemplo.test")
    v = previa(u, sello_empresa())
    e = u.ok(crear(u, v["previa"]))
    assert e["domicilio"] is None
    vacio = u.ok(u.get(f"/api/empresas/{e['id']}/datos-propuestos"))
    assert vacio["domicilio"] is None and vacio["origen_pdf"] is None
    subir(u, e["id"], "replica_166-26.pdf")
    d = u.ok(u.get(f"/api/empresas/{e['id']}/datos-propuestos"))
    assert d["domicilio"] and d["documento"]["nombre"] == "replica_166-26.pdf"
    assert d["origen_pdf"]["valor"] in ("plantilla_ofimatica", "programa_facturacion") and d["origen_pdf"]["generador"]
    # Nada se aplica solo: el domicilio sigue vacío hasta que el usuario lo guarda
    assert u.ok(u.get(f"/api/empresas/{e['id']}"))["domicilio"] is None


# --- Cruces de cada factura ---------------------------------------------------------------------------------------------
def test_cruces_de_la_factura_con_el_certificado_y_el_domicilio(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    datos = {"emisor": {"nif": NIF_REPLICA, "nombre": EMPRESA_REPLICA["razon_social"], "domicilio": "C/ Uno, 2 - 28000 Madrid"}}
    assert certificados.cruces_factura(con, empresa, datos, "simulador") == []
    # Sin certificado no hay cruce de nombre; con el certificado propio, un nombre distinto avisa (no bloquea)
    subir_empresa(c, eid, p12(org_id=NIF_REPLICA, org="INGENIERIA FICTICIA Y ASOCIADOS, S.L.U.", dias=(-30, 20)), entorno="todos")
    assert certificados.cruces_factura(con, empresa, datos, "simulador") == []            # mismas palabras: sin aviso
    otro_nombre = {"emisor": dict(datos["emisor"], nombre="INGENIERÍA FICTICIA ANTIGUA S.L.")}
    inc = certificados.cruces_factura(con, empresa, otro_nombre, "simulador")
    assert [(i["nivel"], i["codigo"], i["campo"]) for i in inc] == [("aviso", "EMISOR_NOMBRE_CERTIFICADO", "emisor.nombre")]
    # Código postal foral con territorio común: aviso y se pide revisar la respuesta; distinto del configurado: aviso
    foral = {"emisor": dict(datos["emisor"], domicilio="C/ Ejemplo, 1 - 48000 Bilbao")}
    cod = {i["codigo"]: i for i in certificados.cruces_factura(con, empresa, foral, "simulador")}
    assert set(cod) == {"EMISOR_CP_FORAL", "EMISOR_CP_DISTINTO"} and "Tres preguntas" in cod["EMISOR_CP_FORAL"]["mensaje"]
    # En pruebas y producción, el certificado que caduca en 30 días o menos
    assert "CERTIFICADO_CADUCA" in {i["codigo"] for i in certificados.cruces_factura(con, empresa, datos, "pruebas")}
    # Datos malformados: nunca falla
    assert certificados.cruces_factura(con, empresa, {"emisor": "x"}, "simulador") == []
    # En el borrador: los avisos llegan como incidencias y no cambian si se puede emitir
    _, bid, b = borrador_listo(c, eid)
    assert b["puede_emitir"] and all(i["nivel"] != "bloqueo" for i in b["incidencias"])
    con.execute("UPDATE empresas SET domicilio='C/ Otra, 3 - 08001 Barcelona' WHERE id=?", (eid,))
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert "EMISOR_CP_DISTINTO" in {i["codigo"] for i in b["incidencias"] if i["nivel"] == "aviso"} and b["puede_emitir"]


def test_cruce_foral_con_la_empresa_de_bilbao(app_cliente):
    """Lo que en el recorrido pasó en silencio: emisor en Bilbao (48000) con el territorio respondido como común."""
    c = app_cliente
    eid = preparar_empresa(c, empresa=EMPRESA_DEMO, patron="PRB-{n:4}", siguiente=1)
    _, bid, b = borrador_listo(c, eid, nombre="demo_proforma.pdf")
    avisos = {i["codigo"] for i in b["incidencias"] if i["nivel"] == "aviso"}
    assert "EMISOR_CP_FORAL" in avisos, b["incidencias"]
    amb = c.ok(c.get(f"/api/empresas/{eid}"))["ambito"]
    assert amb["puede_emitir"] and any("territorio foral" in m and m.startswith("AVISO") for m in amb["motivos"])
