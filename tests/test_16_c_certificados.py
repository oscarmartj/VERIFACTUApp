"""«Mis certificados», titularidad (propio / representante), legitimación por titular, Cabecera/Representante, modo
«no apoderado» del simulador, aislamiento entre usuarios, auditoría y copia/restauración.

Todo es LOCAL o SIMULADO: certificados autofirmados generados en la prueba, NIF ficticios y simulador local. Los
interruptores de remisión real siguen desactivados (conftest)."""
import base64
import datetime as dt
import json

import pytest
from conftest import EMPRESA_REPLICA, Cliente, borrador_listo, preparar_empresa, subir
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from test_03_flujo import emitir, enviar, preparar_desde_bytes, variante_pdf

from app import almacen, certificados, copias, db, diario, produccion, remision, simulador
from app.nif import completar_cif
from app.verifactu import esquemas_xsd, xmlgen

NIF_EMPRESA = EMPRESA_REPLICA["nif"]                     # obligado de las facturas de prueba (sintético)
NIF_GESTORIA = completar_cif("B", "0099887")             # tercero ficticio (gestoría)
NIF_OTRA = completar_cif("A", "0054321")
DNI_PERSONA = "22222222J"
CLAVE_CERT = "Clave-Del-Certificado-Prueba-9"


def p12(*, org_id=None, org=None, serial=None, cn="CERTIFICADO DE PRUEBA", dias=(-30, 365), clave=CLAVE_CERT):
    """PKCS#12 autofirmado (clave EC para que la prueba sea rápida)."""
    k = ec.generate_private_key(ec.SECP256R1())
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if org:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    if org_id:
        attrs.append(x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), f"VATES-{org_id}"))
    if serial:
        attrs.append(x509.NameAttribute(NameOID.SERIAL_NUMBER, f"IDCES-{serial}"))
    nombre = x509.Name(attrs)
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora + dt.timedelta(days=dias[0]))
            .not_valid_after(ahora + dt.timedelta(days=dias[1])).sign(k, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"x", k, cert, None, BestAvailableEncryption(clave.encode()))


def sello_empresa():
    return p12(org_id=NIF_EMPRESA, org=EMPRESA_REPLICA["razon_social"], cn="SELLO EMPRESA PRUEBA")


def sello_gestoria():
    return p12(org_id=NIF_GESTORIA, org="GESTORÍA FICTICIA & ASOCIADOS S.L.", cn="SELLO GESTORIA PRUEBA")


def subir_mio(c, datos, clave=CLAVE_CERT, alias="", codigo=200):
    r = c.tc.post("/api/yo/certificados", files={"archivo": ("c.p12", datos, "application/x-pkcs12")},
                  data={"clave": clave, "alias": alias}, headers={"X-CSRF-Token": c.csrf})
    return c.ok(r, codigo)


def subir_empresa(c, eid, datos, entorno="pruebas"):
    r = c.tc.post(f"/api/empresas/{eid}/certificados", files={"archivo": ("c.p12", datos, "application/x-pkcs12")},
                  data={"clave": CLAVE_CERT, "entorno": entorno}, headers={"X-CSRF-Token": c.csrf})
    return c.ok(r)


def asignar(c, eid, cid, entorno):
    return c.post(f"/api/empresas/{eid}/certificados/asignar", {"certificado_id": cid, "entorno": entorno})


def borrar(c, url):
    return c.tc.delete(url, headers={"X-CSRF-Token": c.csrf})


def entrar(c, email, clave):
    from fastapi.testclient import TestClient
    otro = Cliente(TestClient(c.tc.app))
    r = otro.ok(otro.post("/api/sesion", {"email": email, "clave": clave}))
    otro.csrf = r["csrf"]
    if (r.get("usuario") or r).get("cambiar_clave"):
        # Cuenta creada por el operador con clave temporal: se cambia (y se repone) antes de usarla
        otro.ok(otro.put("/api/yo/clave", {"clave_actual": clave, "clave_nueva": clave + "-provisional"}))
        otro.ok(otro.put("/api/yo/clave", {"clave_actual": clave + "-provisional", "clave_nueva": clave}))
    return otro


def legitimar(c, eid, nif=NIF_GESTORIA, nombre="GESTORÍA FICTICIA & ASOCIADOS S.L.", metodo="colaborador_social"):
    did = subir(c, eid, "demo_recibida.pdf")["documento_id"]       # evidencia (documento de la empresa)
    return c.ok(c.post(f"/api/empresas/{eid}/legitimacion", {"metodo": metodo, "titular_nif": nif, "titular_nombre": nombre,
                                                              "evidencia_documento_id": did}))["id"]


def req(c, eid, clave_lista="requisitos_pruebas"):
    return {x["clave"]: x for x in c.ok(c.get(f"/api/empresas/{eid}/produccion"))[clave_lista]}


def peticion(con, fid):
    reg = db.fila(con, "SELECT * FROM registros WHERE factura_id=? ORDER BY id DESC LIMIT 1", (fid,))
    env = db.fila(con, "SELECT * FROM envios WHERE id=?", (reg["ultimo_envio_id"],))
    return reg, almacen.leer(env["peticion_ruta"])


# --- C1/C2: carga, titularidad propia, asignación ------------------------------------------------------------------
def test_mis_certificados_propio_asignado_a_la_empresa(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    info = subir_mio(c, sello_empresa(), alias="Sello de la empresa")
    assert info["titular"] == {"nif": NIF_EMPRESA, "nombre": EMPRESA_REPLICA["razon_social"]}
    assert info["vigente"] and not info["requiere_recarga"] and info["asignaciones"] == []
    assert "blob_cifrado" not in info and CLAVE_CERT not in json.dumps(info)
    # El mismo certificado otra vez: 409; contraseña errónea: 422; caducado: no se guarda
    subir_mio(c, b"no es un p12", codigo=422)
    r = c.tc.post("/api/yo/certificados", files={"archivo": ("c.p12", sello_gestoria(), "application/x-pkcs12")},
                  data={"clave": "otra"}, headers={"X-CSRF-Token": c.csrf})
    assert r.status_code == 422
    subir_mio(c, p12(org_id=NIF_EMPRESA, dias=(-60, -1)), codigo=422)
    # Asignación a la empresa en pruebas: propio, sin representante
    sel = c.ok(asignar(c, eid, info["id"], "pruebas"))
    assert sel["origen"] == "mis_certificados" and sel["es_mio"]
    assert sel["legitimacion"]["propio"] and sel["legitimacion"]["representante"] is None and sel["legitimacion"]["ok"]
    r = req(c, eid)
    assert r["certificado"]["cumplido"] and "representacion" not in r
    assert "Se usará el certificado" in r["certificado"]["detalle"] and "propio" in r["certificado"]["detalle"]
    assert r["certificado"]["certificado"]["origen"] == "mis_certificados"
    con = db.conectar()
    cert, rep = certificados.para_envio(con, db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), "pruebas")
    assert rep is None and cert["origen"] == "usuario"
    with certificados.ficheros_pem(cert) as (cp, kp):   # se descifra con su contexto (fila y propietario)
        assert open(cp, "rb").read().startswith(b"-----BEGIN CERTIFICATE")
    # Un certificado cargado por la empresa sustituye la asignación; reactivar no requiere volver a cargar
    cid_emp = subir_empresa(c, eid, sello_empresa())["id"]
    assert c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["pruebas"]["origen"] == "empresa"
    lista = c.ok(c.get(f"/api/empresas/{eid}/certificados"))
    asig = [x for x in lista if x["origen"] == "mis_certificados"]
    assert asig and not asig[0]["activo"] and not asig[0]["en_uso"]
    assert any(x["origen"] == "empresa" and x["en_uso"] for x in lista)
    c.ok(asignar(c, eid, info["id"], "pruebas"))
    assert c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["pruebas"]["origen"] == "mis_certificados"
    sel = c.ok(c.post(f"/api/empresas/{eid}/certificados/{cid_emp}/activar"))
    assert sel["origen"] == "empresa" and sel["certificado_id"] == cid_emp
    # El simulador sigue sin necesitar certificado
    assert certificados.para_envio(con, db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), "simulador") == (None, None)


# --- C2/C3: certificado de un tercero, legitimación, Representante y «no apoderado» ------------------------------------
def test_tercero_necesita_legitimacion_y_envia_con_representante(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    sel = c.ok(asignar(c, eid, cid, "pruebas"))
    leg = sel["legitimacion"]
    assert not leg["propio"] and leg["requiere_legitimacion"] and not leg["ok"] and leg["titular"]["nif"] == NIF_GESTORIA
    r = req(c, eid)
    assert r["certificado"]["cumplido"] and not r["representacion"]["cumplido"]
    assert NIF_GESTORIA in r["representacion"]["texto"]
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    with pytest.raises(certificados.CertificadoNoUtilizable, match="tercero"):
        certificados.para_envio(con, emp, "pruebas")
    # Legitimación declarada para OTRO titular: no vale
    legitimar(c, eid, nif=NIF_OTRA, nombre="OTRO TERCERO S.A.")
    assert not req(c, eid)["representacion"]["cumplido"]
    # Datos de la legitimación validados (NIF, nombre, evidencia de la empresa)
    for cuerpo in ({"metodo": "colaborador_social", "titular_nif": "X", "titular_nombre": "A", "evidencia_documento_id": 1},
                   {"metodo": "colaborador_social", "titular_nif": NIF_GESTORIA, "titular_nombre": "", "evidencia_documento_id": 1},
                   {"metodo": "colaborador_social", "titular_nif": NIF_GESTORIA, "titular_nombre": "A"},
                   {"metodo": "otro", "titular_nif": NIF_GESTORIA, "titular_nombre": "A", "evidencia_documento_id": 1}):
        assert c.post(f"/api/empresas/{eid}/legitimacion", cuerpo).status_code == 422
    lid = legitimar(c, eid)
    r = req(c, eid)
    assert r["certificado"]["cumplido"] and r["representacion"]["cumplido"]
    assert f"Representante GESTORÍA FICTICIA & ASOCIADOS S.L. (NIF {NIF_GESTORIA})" in r["certificado"]["detalle"]
    cert, rep = certificados.para_envio(con, emp, "pruebas")
    assert rep == {"nif": NIF_GESTORIA, "nombre": "GESTORÍA FICTICIA & ASOCIADOS S.L."}
    # La cabecera con Representante valida contra el XSD oficial
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    xml_reg = db.fila(con, "SELECT xml FROM registros WHERE factura_id=?", (fid,))["xml"]
    msg = xmlgen.mensaje_remision({"nombre": emp["razon_social"], "nif": emp["nif"], "representante": rep}, [xml_reg])
    assert esquemas_xsd.validar(msg) == []
    assert msg.find(f".//{{{esquemas_xsd.NS_SF}}}Representante/{{{esquemas_xsd.NS_SF}}}NIF").text == NIF_GESTORIA
    # Revocar la legitimación: deja de poder usarse
    assert borrar(c, f"/api/empresas/{eid}/legitimaciones/{lid}").status_code == 200
    assert not req(c, eid)["representacion"]["cumplido"]
    with pytest.raises(certificados.CertificadoNoUtilizable):
        certificados.para_envio(con, emp, "pruebas")
    assert any(x["id"] == lid and x["revocada_en"] for x in c.ok(c.get(f"/api/empresas/{eid}/legitimaciones")))


def test_flujo_simulador_con_representante_y_no_apoderado(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "simulador"))       # en el simulador solo determina la representación (sin TLS)
    con = db.conectar()
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    # Sin legitimación no se envía (no llega a salir) y se explica
    res = enviar(eid)
    assert res["resultado"] == "no_enviado" and "tercero" in res["error"]
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE factura_id=?", (fid,))["estado_envio"] == "pendiente"
    legitimar(c, eid, metodo="apoderamiento_aeat")
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")   # (pasa el tiempo de espera)
    # El titular no está apoderado en la AEAT: 4112, bloqueado, con ayuda
    simulador.fijar_modo("no_apoderado", 1)
    res = enviar(eid)
    assert res["resultado"] == "fault_cliente"
    reg, sobre = peticion(con, fid)
    assert reg["estado_envio"] == "bloqueado" and "4112" in reg["descripcion_error"] and "legitimación" in reg["descripcion_error"]
    assert f"<sum1:NIF>{NIF_GESTORIA}</sum1:NIF>".encode() in sobre and b"Representante" in sobre
    # Corregido en la AEAT: se reintenta y la AEAT (simulada) lo acepta con el representante como presentador
    c.ok(c.post(f"/api/registros/{reg['id']}/reintentar"))
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    res = enviar(eid)
    reg, sobre = peticion(con, fid)
    assert res["accion"] == "enviado" and reg["estado_envio"] == "aceptado", res
    from lxml import etree
    cuerpo = etree.fromstring(sobre).find(f".//{{{esquemas_xsd.NS_SFLR}}}RegFactuSistemaFacturacion")
    assert esquemas_xsd.validar(cuerpo) == []
    env = db.fila(con, "SELECT respuesta_ruta FROM envios WHERE id=?", (reg["ultimo_envio_id"],))
    assert f"<tik:NIFPresentador>{NIF_GESTORIA}</tik:NIFPresentador>".encode() in almacen.leer(env["respuesta_ruta"])
    # Sin Representante el modo «no_apoderado» no afecta: un certificado propio se acepta
    cid2 = subir_mio(c, sello_empresa())["id"]
    c.ok(asignar(c, eid, cid2, "simulador"))
    b2 = preparar_desde_bytes(c, eid, "f167.pdf", variante_pdf("167/26"))
    fid2 = c.ok(emitir(c, b2, "k-2"))["factura_id"]
    simulador.fijar_modo("no_apoderado", 1)
    enviar(eid)
    reg2, sobre2 = peticion(con, fid2)
    assert reg2["estado_envio"] == "aceptado" and b"Representante" not in sobre2
    # Auditoría de cada uso en la actividad de la empresa y en los usos del propietario
    usos = [a for a in db.filas(con, "SELECT * FROM actividad WHERE accion='certificado.uso' AND empresa_id=?", (eid,))]
    assert len(usos) >= 3 and all(a["objeto"] == "certificado_usuario" for a in usos)
    assert any(json.loads(a["detalle"])["representante_nif"] == NIF_GESTORIA for a in usos)
    vistos = c.ok(c.get(f"/api/yo/certificados/{cid}/usos"))
    assert {"certificado.uso", "certificado.asignado", "certificado.asignacion_retirada"} <= {u["accion"] for u in vistos}


def test_simulador_representante_con_nif_no_identificado():
    rep_mal = {"nombre": "X", "nif": "B00998871"}       # dígito de control erróneo
    cab = {"nombre": EMPRESA_REPLICA["razon_social"], "nif": NIF_EMPRESA, "representante": rep_mal}
    from app.verifactu import respuesta as respmod
    reg = xmlgen.registro_alta(xmlgen.DatosAlta(
        id_emisor=NIF_EMPRESA, nombre_razon_emisor="E", num_serie="1/26", fecha_expedicion="01-01-2026", tipo_factura="F1",
        descripcion_operacion="Prueba", desglose=[{"impuesto": "01", "clave_regimen": "01", "calificacion": "S1",
                                                   "tipo_impositivo": "21.00", "base": "10.00", "cuota": "2.10"}],
        cuota_total="2.10", importe_total="12.10", fecha_hora_huso="2026-01-01T10:00:00+01:00", huella="A" * 64,
        encadenamiento=None, sistema=xmlgen.SistemaInformatico("P", NIF_EMPRESA, "S", "FV", "1", "1"),
        destinatarios=[{"nombre": "D", "nif": NIF_OTRA}]))
    sobre = xmlgen.sobre_soap(xmlgen.mensaje_remision(cab, [xmlgen.a_texto(reg)]))
    r = respmod.interpretar(simulador.procesar(sobre))
    assert r.tipo == "fault" and "4105" in r.fault_texto


# --- Aislamiento, pérdida de la administración, eliminación ---------------------------------------------------------
def test_aislamiento_entre_usuarios(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    aid = c.ok(asignar(c, eid, cid, "pruebas"))["asignacion_id"]
    # Otro administrador de la misma empresa y de una segunda empresa
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "admin2@ejemplo.test", "nombre": "A2", "clave": "clave-admin-2-xyz", "rol": "admin"}))
    e2 = c.ok(c.post("/api/empresas", {"nif": NIF_OTRA, "razon_social": "OTRA EMPRESA FICTICIA S.A.", "domicilio": "x"}))["id"]
    c.ok(c.post(f"/api/empresas/{e2}/miembros", {"email": "admin2@ejemplo.test", "rol": "admin"}))
    a2 = entrar(c, "admin2@ejemplo.test", "clave-admin-2-xyz")
    assert a2.ok(a2.get("/api/yo/certificados")) == []
    for ent in ("pruebas", "produccion", "simulador"):
        assert asignar(a2, e2, cid, ent).status_code == 404       # no puede usarlo en otra empresa
        assert asignar(a2, eid, cid, ent).status_code == 404       # ni reasignarlo en esta
    assert borrar(a2, f"/api/yo/certificados/{cid}").status_code == 404
    assert borrar(a2, f"/api/yo/certificados/{cid}/asignaciones/{aid}").status_code == 404
    assert a2.get(f"/api/yo/certificados/{cid}/usos").status_code == 404
    # Ve qué certificado usa su empresa (sin archivo ni contraseña) pero no es suyo
    lista = a2.ok(a2.get(f"/api/empresas/{eid}/certificados"))
    fila = next(x for x in lista if x["origen"] == "mis_certificados")
    assert not fila["es_mio"] and fila["en_uso"] and "blob_cifrado" not in fila and CLAVE_CERT not in json.dumps(lista)
    # Un usuario sin acceso a la empresa no ve nada
    c.ok(c.post(f"/api/empresas/{e2}/miembros", {"email": "ajeno@ejemplo.test", "nombre": "Aj", "clave": "clave-ajeno-xyz1", "rol": "admin"}))
    aj = entrar(c, "ajeno@ejemplo.test", "clave-ajeno-xyz1")
    for url in (f"/api/empresas/{eid}/certificados", f"/api/empresas/{eid}/certificados/seleccion", f"/api/empresas/{eid}/legitimaciones"):
        assert aj.get(url).status_code == 404, url
    assert aj.post(f"/api/empresas/{eid}/legitimacion", {"metodo": "colaborador_social"}).status_code == 404
    # El propietario no puede asignarlo a una empresa donde no es administrador (ni miembro)
    c.ok(c.post(f"/api/empresas/{e2}/miembros", {"email": "admin@ejemplo.test", "rol": "emisor"}))
    assert asignar(c, e2, cid, "pruebas").status_code == 404
    # Un lector de la empresa no puede asignar ni activar
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lector@ejemplo.test", "nombre": "L", "clave": "clave-lector-123", "rol": "lector"}))
    lec = entrar(c, "lector@ejemplo.test", "clave-lector-123")
    lcid = subir_mio(lec, sello_empresa())["id"]
    assert asignar(lec, eid, lcid, "pruebas").status_code == 404
    # Sin CSRF no se carga ni se asigna
    assert c.tc.post(f"/api/empresas/{eid}/certificados/asignar", json={"certificado_id": cid, "entorno": "pruebas"}).status_code == 403
    assert c.tc.post("/api/yo/certificados", files={"archivo": ("c.p12", sello_empresa(), "application/x-pkcs12")},
                     data={"clave": CLAVE_CERT}).status_code == 403


def test_propietario_que_pierde_la_administracion(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_empresa())["id"]
    aid = c.ok(asignar(c, eid, cid, "pruebas"))["asignacion_id"]
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "admin2@ejemplo.test", "nombre": "A2", "clave": "clave-admin-2-xyz", "rol": "admin"}))
    a2 = entrar(c, "admin2@ejemplo.test", "clave-admin-2-xyz")
    a2.ok(a2.post(f"/api/empresas/{eid}/miembros", {"email": "admin@ejemplo.test", "rol": "emisor"}))
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    with pytest.raises(certificados.CertificadoNoUtilizable, match="administrador"):
        certificados.para_envio(con, emp, "pruebas")
    r = req(a2, eid)
    assert not r["certificado"]["cumplido"] and "ya no es administrador" in r["certificado"]["detalle"]
    # Vuelve a ser administrador: vuelve a poder usarse (se comprueba en cada uso)
    a2.ok(a2.post(f"/api/empresas/{eid}/miembros", {"email": "admin@ejemplo.test", "rol": "admin"}))
    assert certificados.para_envio(con, emp, "pruebas")[0]["asignacion_id"] == aid
    # El propietario puede retirarlo aunque ya no administre la empresa
    a2.ok(a2.post(f"/api/empresas/{eid}/miembros", {"email": "admin@ejemplo.test", "rol": "lector"}))
    assert borrar(c, f"/api/yo/certificados/{cid}/asignaciones/{aid}").status_code == 200
    assert c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["pruebas"] is None


def test_eliminar_certificado(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_empresa())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    c.ok(asignar(c, eid, cid, "produccion"))
    r = c.ok(borrar(c, f"/api/yo/certificados/{cid}"))
    assert r["asignaciones_retiradas"] == 2
    con = db.conectar()
    fila = db.fila(con, "SELECT * FROM certificados_usuario WHERE id=?", (cid,))
    assert fila["blob_cifrado"] is None and fila["eliminado_en"]
    assert c.ok(c.get("/api/yo/certificados")) == []
    sel = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))
    assert sel["pruebas"] is None and sel["produccion"] is None
    assert borrar(c, f"/api/yo/certificados/{cid}").status_code == 404
    assert asignar(c, eid, cid, "pruebas").status_code == 404
    assert subir_mio(c, sello_empresa())["id"] != cid     # puede volver a cargarse (fila nueva)


def test_contrasena_nunca_devuelta_ni_registrada(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    datos = sello_gestoria()
    respuestas = [subir_mio(c, datos)]
    cid = respuestas[0]["id"]
    respuestas += [c.ok(asignar(c, eid, cid, "simulador")), c.ok(c.get("/api/yo/certificados")),
                   c.ok(c.get(f"/api/empresas/{eid}/certificados")), c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion")),
                   c.ok(c.get(f"/api/empresas/{eid}/produccion")), subir_empresa(c, eid, sello_empresa())]
    legitimar(c, eid)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    enviar(eid)
    respuestas += [c.ok(c.get(f"/api/yo/certificados/{cid}/usos")), c.ok(c.get(f"/api/empresas/{eid}/actividad"))]
    texto = json.dumps(respuestas, ensure_ascii=False)
    con = db.conectar()
    actividad = json.dumps(db.filas(con, "SELECT * FROM actividad"), ensure_ascii=False)
    diario_txt = diario.ruta().read_text(encoding="utf-8")
    fragmento = base64.b64encode(datos).decode()[40:80]
    for t in (texto, actividad, diario_txt):
        assert CLAVE_CERT not in t and fragmento not in t and "blob_cifrado" not in t
    assert '"certificados_fila"' in diario_txt and '"con_archivo": true' in diario_txt
    # En la base solo está cifrado (AES-256-GCM) y la clave maestra está fuera de la base de datos
    blob = db.fila(con, "SELECT blob_cifrado FROM certificados_usuario WHERE id=?", (cid,))["blob_cifrado"]
    assert blob[:2] == b"v1" and CLAVE_CERT.encode() not in blob and datos[:64] not in blob


# --- Copia y restauración ---------------------------------------------------------------------------------------------
def test_copia_y_restauracion_de_mis_certificados(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    c1 = subir_mio(c, sello_empresa())["id"]
    c2 = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, c1, "pruebas"))
    c.ok(asignar(c, eid, c2, "simulador"))
    lid_antes = legitimar(c, eid)
    con = db.conectar()
    copia = copias.crear_copia(con)
    # Cambios posteriores a la copia (solo están en el diario)
    c.ok(borrar(c, f"/api/yo/certificados/{c1}"))                    # baja: no debe resucitar
    datos3 = p12(serial=DNI_PERSONA, cn="NOMBRE APELLIDO - 22222222J")
    c3 = subir_mio(c, datos3)["id"]                                   # posterior: vuelve sin archivo
    c.ok(asignar(c, eid, c3, "produccion"))
    c.ok(asignar(c, eid, c2, "pruebas"))
    assert borrar(c, f"/api/empresas/{eid}/legitimaciones/{lid_antes}").status_code == 200   # revocación: no debe deshacerse
    lid_nueva = legitimar(c, eid, metodo="apoderamiento_aeat")
    con.close()
    r = copias.restaurar(copia)
    assert r["certificados"]["conflictos"] == [] and r["certificados"]["filas_aplicadas"] >= 5
    con = db.conectar()
    f1 = db.fila(con, "SELECT * FROM certificados_usuario WHERE id=?", (c1,))
    assert f1["eliminado_en"] and f1["blob_cifrado"] is None
    mios = {x["id"]: x for x in c.ok(c.get("/api/yo/certificados"))}
    assert set(mios) == {c2, c3} and mios[c3]["requiere_recarga"] and not mios[c2]["requiere_recarga"]
    legs = {x["id"]: x for x in c.ok(c.get(f"/api/empresas/{eid}/legitimaciones"))}
    assert legs[lid_antes]["revocada_en"] and not legs[lid_nueva]["revocada_en"]
    sel = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))
    assert sel["pruebas"]["certificado_id"] == c2 and sel["pruebas"]["legitimacion"]["ok"]   # anterior a la copia: se descifra
    assert sel["simulador"]["certificado_id"] == c2
    assert sel["produccion"]["certificado_id"] == c3 and not sel["produccion"]["legitimacion"]["utilizable"]
    assert "volver a cargarse" in sel["produccion"]["legitimacion"]["detalle"]
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    cert, rep = certificados.para_envio(con, emp, "pruebas")
    assert rep["nif"] == NIF_GESTORIA
    with certificados.ficheros_pem(cert) as (cp, _):                 # la clave maestra (fuera de la base) lo descifra
        assert open(cp, "rb").read().startswith(b"-----BEGIN CERTIFICATE")
    # Volver a cargar el mismo archivo le devuelve el archivo y conserva su asignación
    recargado = subir_mio(c, datos3)
    assert recargado["id"] == c3 and not recargado["requiere_recarga"]
    assert c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["produccion"]["legitimacion"]["utilizable"]
    # La conciliación del arranque no duplica anotaciones ya hechas, pero completa las que falten
    entradas = diario.leer()
    assert certificados.reconciliar_diario(con, entradas) == 0
    con.execute("UPDATE legitimaciones SET version=version+1 WHERE id=?", (lid_nueva,))
    assert certificados.reconciliar_diario(con, diario.leer()) == 1


def test_restauracion_aplica_la_ultima_asignacion(app_cliente):
    """Copia con un certificado de la empresa en uso; tras la copia se asigna uno de «Mis certificados»: al restaurar
    gana la asignación (la elección más reciente) y el certificado de la empresa queda inactivo."""
    c = app_cliente
    eid = preparar_empresa(c)
    subir_empresa(c, eid, sello_empresa())
    con = db.conectar()
    copia = copias.crear_copia(con)
    cid = subir_mio(c, sello_empresa())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    con.close()
    copias.restaurar(copia)
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM certificados WHERE empresa_id=? AND activo=1", (eid,))["n"] == 0
    sel = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["pruebas"]
    assert sel["origen"] == "mis_certificados" and sel["certificado_id"] == cid
    # (el certificado es posterior a la copia: hay que volver a cargarlo antes de usarlo)
    assert not sel["legitimacion"]["utilizable"]


def test_produccion_bloqueada_igual_sin_interruptor(app_cliente):
    """Con certificado y legitimación correctos, pruebas/producción siguen bloqueadas por el interruptor del servidor."""
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    legitimar(c, eid)
    r = req(c, eid)
    assert r["certificado"]["cumplido"] and r["representacion"]["cumplido"] and not r["interruptor"]["cumplido"]
    _, bid, _ = borrador_listo(c, eid)
    assert emitir(c, bid, "kp", entorno="pruebas").status_code == 403
    con = db.conectar()
    with pytest.raises(simulador.NoEnviado, match="deshabilitada"):
        remision._preparar_destino(con, "pruebas", eid)


# --- Revisión adversarial: regresiones C-F1..C-F4 ----------------------------------------------------------------------
def _segunda_empresa(c):
    """Segunda empresa con su propio administrador (b@) y un lector de la primera que no es miembro de la segunda."""
    e2 = c.ok(c.post("/api/empresas", {"nif": NIF_OTRA, "razon_social": "OTRA EMPRESA FICTICIA S.A.", "domicilio": "x"}))["id"]
    c.ok(c.post(f"/api/empresas/{e2}/miembros", {"email": "b@ejemplo.test", "nombre": "B", "clave": "clave-b-123456", "rol": "admin"}))
    return e2


def _sin_documento_en_el_diario():
    """Simula anotaciones de legitimación sin la fila del documento de evidencia (p. ej., de una versión anterior):
    reescribe esas líneas del diario con su suma correcta."""
    ruta = diario.ruta()
    lineas = []
    for bruta in ruta.read_text(encoding="utf-8").splitlines():
        e = json.loads(bruta)
        if e.get("tipo") == "certificados_fila" and e["datos"]["tabla"] == "legitimaciones":
            e.pop("sha")
            e["datos"]["fila"].pop("_documento", None)
            e["sha"] = diario._suma(e)
        lineas.append(json.dumps(e, ensure_ascii=False, sort_keys=True))
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def test_restauracion_recupera_la_evidencia_de_la_legitimacion(app_cliente):
    """C-F1: una legitimación declarada después de la copia vuelve con SU documento de evidencia (fila del diario y archivo
    de la copia espejo), sin referencias colgantes; el siguiente documento de otra empresa no reutiliza su identificador."""
    c = app_cliente
    eid = preparar_empresa(c)
    e2 = _segunda_empresa(c)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lec@ejemplo.test", "nombre": "L", "clave": "clave-lec-123456", "rol": "lector"}))
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    lid = legitimar(c, eid)                                  # evidencia subida DESPUÉS de la copia
    con = db.conectar()
    ev = db.fila(con, "SELECT evidencia_documento_id FROM legitimaciones WHERE id=?", (lid,))["evidencia_documento_id"]
    original = c.get(f"/api/documentos/{ev}/original").content
    con.close()
    r = copias.restaurar(copia)
    assert r["referencias_pendientes"] == [] and r["certificados"]["documentos_evidencia_recuperados"] == 1
    assert r["certificados"]["legitimaciones_sin_evidencia"] == []
    con = db.conectar()
    assert db.fila(con, "SELECT empresa_id FROM documentos WHERE id=?", (ev,))["empresa_id"] == eid
    assert c.get(f"/api/documentos/{ev}/original").content == original
    _, rep = certificados.para_envio(con, db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), "pruebas")
    assert rep["nif"] == NIF_GESTORIA                         # cuenta: su evidencia existe y es de la empresa
    b = entrar(c, "b@ejemplo.test", "clave-b-123456")
    from test_03_flujo import subir_bytes
    from conftest import pdf
    d2 = subir_bytes(b, e2, "Factura-ClienteConfidencial-E2.pdf", pdf("demo_recibida.pdf"))["documento_id"]
    assert d2 != ev
    lec = entrar(c, "lec@ejemplo.test", "clave-lec-123456")
    l = next(x for x in lec.ok(lec.get(f"/api/empresas/{eid}/legitimaciones")) if x["id"] == lid)
    assert l["evidencia_documento_id"] == ev and l["evidencia"] == "demo_recibida.pdf"


def test_legitimacion_sin_evidencia_tras_restaurar_no_cuenta_ni_filtra(app_cliente):
    """C-F1: si el documento de evidencia no puede recuperarse, la legitimación pierde la referencia y deja de contar
    (no se envía con Representante), y el identificador liberado no muestra el documento de otra empresa."""
    c = app_cliente
    eid = preparar_empresa(c)
    e2 = _segunda_empresa(c)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lec@ejemplo.test", "nombre": "L", "clave": "clave-lec-123456", "rol": "lector"}))
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    lid = legitimar(c, eid)
    _sin_documento_en_el_diario()
    r = copias.restaurar(copia)
    assert r["referencias_pendientes"] == [] and r["certificados"]["legitimaciones_sin_evidencia"] == [lid]
    con = db.conectar()
    fila = db.fila(con, "SELECT evidencia_documento_id, revocada_en FROM legitimaciones WHERE id=?", (lid,))
    assert fila["evidencia_documento_id"] is None and fila["revocada_en"] is None
    assert db.fila(con, "SELECT 1 FROM actividad WHERE accion='legitimacion.evidencia_perdida' AND objeto_id=?", (lid,))
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    with pytest.raises(certificados.CertificadoNoUtilizable, match="evidencia"):
        certificados.para_envio(con, emp, "pruebas")
    assert not req(c, eid)["representacion"]["cumplido"]
    # Otra empresa sube un documento: puede recibir el identificador libre, pero no se asocia a la legitimación
    b = entrar(c, "b@ejemplo.test", "clave-b-123456")
    from test_03_flujo import subir_bytes
    from conftest import pdf
    subir_bytes(b, e2, "Factura-ClienteConfidencial-E2.pdf", pdf("demo_recibida.pdf"))
    lec = entrar(c, "lec@ejemplo.test", "clave-lec-123456")
    texto = json.dumps(lec.ok(lec.get(f"/api/empresas/{eid}/legitimaciones")), ensure_ascii=False)
    assert "ClienteConfidencial" not in texto
    with pytest.raises(certificados.CertificadoNoUtilizable):
        certificados.para_envio(con, emp, "pruebas")
    # El nuevo estado (sin evidencia) se anota en el diario al conciliar
    assert certificados.reconciliar_diario(con, diario.leer()) >= 1
    # Vuelve a declararse con su evidencia y cuenta de nuevo
    legitimar(c, eid)
    assert certificados.para_envio(con, emp, "pruebas")[1]["nif"] == NIF_GESTORIA


def test_evidencia_de_otra_empresa_no_cuenta(app_cliente):
    """C-F1 (defensa en profundidad): una evidencia que apunte a un documento de otra empresa no cuenta ni se muestra."""
    c = app_cliente
    eid = preparar_empresa(c)
    e2 = _segunda_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    lid = legitimar(c, eid)
    b = entrar(c, "b@ejemplo.test", "clave-b-123456")
    from test_03_flujo import subir_bytes
    from conftest import pdf
    d2 = subir_bytes(b, e2, "Documento-Ajeno-E2.pdf", pdf("demo_recibida.pdf"))["documento_id"]
    con = db.conectar()
    con.execute("UPDATE legitimaciones SET evidencia_documento_id=? WHERE id=?", (d2, lid))
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    with pytest.raises(certificados.CertificadoNoUtilizable, match="evidencia"):
        certificados.para_envio(con, emp, "pruebas")
    l = next(x for x in c.ok(c.get(f"/api/empresas/{eid}/legitimaciones")) if x["id"] == lid)
    assert l["evidencia"] is None and l["evidencia_documento_id"] is None


def test_administrador_deja_de_usar_una_asignacion_ajena(app_cliente):
    """C-F2: si el propietario deja de administrar la empresa, el simulador no se bloquea (envía sin certificado, como
    antes de asignarlo) y cualquier administrador puede retirar la asignación (actividad, usos del propietario y diario).
    En pruebas/producción la asignación sigue sin poder usarse."""
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    aid = c.ok(asignar(c, eid, cid, "simulador"))["asignacion_id"]
    aid_p = c.ok(asignar(c, eid, cid, "pruebas"))["asignacion_id"]
    legitimar(c, eid)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "a2@ejemplo.test", "nombre": "A2", "clave": "clave-admin-2-xyz", "rol": "admin"}))
    # cuenta de lectura creada por el operador (un administrador corriente solo puede invitar)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lec@ejemplo.test", "nombre": "L", "clave": "clave-lec-123456", "rol": "lector"}))
    a2 = entrar(c, "a2@ejemplo.test", "clave-admin-2-xyz")
    a2.ok(a2.post(f"/api/empresas/{eid}/miembros", {"email": "admin@ejemplo.test", "rol": "emisor"}))
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    assert certificados.para_envio(con, emp, "simulador") == (None, None)
    with pytest.raises(certificados.CertificadoNoUtilizable, match="administrador"):
        certificados.para_envio(con, emp, "pruebas")
    _, bid, _ = borrador_listo(a2, eid)
    fid = a2.ok(emitir(a2, bid))["factura_id"]
    res = enviar(eid)
    reg, sobre = peticion(con, fid)
    assert res["accion"] == "enviado" and reg["estado_envio"] == "aceptado" and not reg["incidencia"], res
    assert b"Representante" not in sobre
    # Retirada por otro administrador (no es el propietario): sí puede, en su empresa
    assert borrar(a2, f"/api/yo/certificados/{cid}/asignaciones/{aid}").status_code == 404   # «Mis certificados» es del propietario
    lec = entrar(c, "lec@ejemplo.test", "clave-lec-123456")
    assert borrar(lec, f"/api/empresas/{eid}/certificados/asignaciones/{aid}").status_code == 404
    assert borrar(c, f"/api/empresas/{eid}/certificados/asignaciones/{aid}").status_code == 404   # el propietario ya no es admin
    assert a2.tc.delete(f"/api/empresas/{eid}/certificados/asignaciones/{aid}").status_code == 403          # sin CSRF
    e2 = c.ok(c.post("/api/empresas", {"nif": NIF_OTRA, "razon_social": "OTRA EMPRESA FICTICIA S.A.", "domicilio": "x"}))["id"]
    c.ok(c.post(f"/api/empresas/{e2}/miembros", {"email": "a2@ejemplo.test", "rol": "admin"}))
    assert borrar(a2, f"/api/empresas/{e2}/certificados/asignaciones/{aid}").status_code == 404   # no es de esa empresa
    r = a2.ok(borrar(a2, f"/api/empresas/{eid}/certificados/asignaciones/{aid}"))
    assert r["ok"] and r["entorno"] == "simulador" and r["en_uso"] is None
    assert borrar(a2, f"/api/empresas/{eid}/certificados/asignaciones/{aid}").status_code == 404   # ya retirada
    a2.ok(borrar(a2, f"/api/empresas/{eid}/certificados/asignaciones/{aid_p}"))
    sel = a2.ok(a2.get(f"/api/empresas/{eid}/certificados/seleccion"))
    assert sel["simulador"] is None and sel["pruebas"] is None
    usos = c.ok(c.get(f"/api/yo/certificados/{cid}/usos"))
    retiradas = [u for u in usos if u["accion"] == "certificado.asignacion_retirada"]
    assert len(retiradas) == 2 and all("retirada por un administrador de la empresa" in u["detalle"] for u in retiradas)
    anotadas = [e["datos"]["fila"] for e in diario.leer() if e["tipo"] == "certificados_fila"
                and e["datos"]["tabla"] == "certificado_asignaciones" and e["datos"]["fila"]["id"] == aid]
    assert anotadas[-1]["activo"] == 0 and anotadas[-1]["motivo_retirada"] == "retirada por un administrador de la empresa"
    # El certificado sigue en «Mis certificados» de su propietario
    assert any(x["id"] == cid for x in c.ok(c.get("/api/yo/certificados")))


def test_restauracion_conserva_el_certificado_de_empresa_en_uso(app_cliente):
    """C-F3: reactivar un certificado de la empresa tras la copia sobrevive a la restauración (se anota su estado «en
    uso», nunca el archivo). Uno cargado tras la copia no puede recrearse: se informa y NO se vuelve a usar en silencio el
    que sustituyó."""
    c = app_cliente
    eid = preparar_empresa(c)
    cemp = subir_empresa(c, eid, sello_empresa())["id"]
    cid = subir_mio(c, sello_empresa())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))                   # el de la empresa queda sin uso
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    sel = c.ok(c.post(f"/api/empresas/{eid}/certificados/{cemp}/activar"))
    assert (sel["origen"], sel["certificado_id"]) == ("empresa", cemp)
    r = copias.restaurar(copia)
    assert r["certificados"]["conflictos"] == [] and r["certificados"]["certificados_empresa_por_recargar"] == []
    sel = c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["pruebas"]
    assert (sel["origen"], sel["certificado_id"]) == ("empresa", cemp) and sel["legitimacion"]["ok"]
    con = db.conectar()
    cert, _ = certificados.para_envio(con, db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), "pruebas")
    with certificados.ficheros_pem(cert) as (cp, _):         # anterior a la copia: se descifra
        assert open(cp, "rb").read().startswith(b"-----BEGIN CERTIFICATE")
    # Sustituido por otro cargado DESPUÉS de una copia: tras restaurarla, se pide volver a cargarlo
    from app import config
    copia2 = copias.crear_copia(con, config.ajustes().copias_dir / "segunda")
    con.close()
    nuevo = subir_empresa(c, eid, sello_empresa())["id"]
    r = copias.restaurar(copia2)
    assert [x["id"] for x in r["certificados"]["certificados_empresa_por_recargar"]] == [nuevo]
    assert c.ok(c.get(f"/api/empresas/{eid}/certificados/seleccion"))["pruebas"] is None
    con = db.conectar()
    assert db.fila(con, "SELECT activo FROM certificados WHERE id=?", (cemp,))["activo"] == 0
    # El administrador decide: puede volver a usar el anterior
    assert c.ok(c.post(f"/api/empresas/{eid}/certificados/{cemp}/activar"))["certificado_id"] == cemp
    # El diario guarda el estado «en uso» de los certificados de la empresa, nunca su archivo ni la contraseña
    txt = diario.ruta().read_text(encoding="utf-8")
    assert '"tabla": "certificados"' in txt and "blob_cifrado" not in txt and CLAVE_CERT not in txt


@pytest.fixture()
def pruebas_sin_red(monkeypatch):
    """Solo en esta prueba: el interruptor de pruebas se ve abierto DENTRO de remision, con todo acceso a la red
    sustituido (requests y el transporte de remision). Nada sale de la máquina."""
    import requests

    def prohibido(*a, **k):
        raise AssertionError("intento de conexión real")
    monkeypatch.setattr(requests, "post", prohibido)
    monkeypatch.setattr(requests, "get", prohibido)
    monkeypatch.setattr(requests.Session, "request", prohibido)
    llamadas = []

    def post_local(url, sobre, cp, kp):
        llamadas.append(url)
        return 200, simulador.procesar(sobre)
    monkeypatch.setattr(remision, "_post", post_local)
    real = remision.ajustes()

    class Ajustes:
        def __getattr__(self, k):
            return True if k == "permitir_pruebas_aeat" else getattr(real, k)
    monkeypatch.setattr(remision, "ajustes", lambda: Ajustes())
    assert not real.permitir_pruebas_aeat and not real.permitir_produccion
    return llamadas


def test_consulta_con_preparar_destino_lleva_representante_y_se_audita(app_cliente, pruebas_sin_red, monkeypatch):
    """C-F4: la consulta a la AEAT (otro servicio) obtiene destino, certificado y REPRESENTANTE con
    remision._preparar_destino(con, entorno, eid) — la misma regla que la remisión — y el uso del certificado queda
    auditado al descifrarlo (una vez por conexión). La remisión sigue anotando un solo uso por envío."""
    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    legitimar(c, eid)
    con = db.conectar()
    # Igual que el adaptador de la consulta: toma el representante de res[2] si existe
    res = remision._preparar_destino(con, "pruebas", eid)
    assert len(res) == 3 and res[0] == remision.ENDPOINTS_SOAP["pruebas"]["sello"]
    assert res[2] == {"nif": NIF_GESTORIA, "nombre": "GESTORÍA FICTICIA & ASOCIADOS S.L."}
    usos = lambda: [json.loads(a["detalle"]) for a in db.filas(con, "SELECT detalle FROM actividad WHERE accion='certificado.uso'")]
    assert usos() == []                                      # obtener el representante no es un uso
    with certificados.ficheros_pem(res[1]) as (cp, _):
        assert open(cp, "rb").read().startswith(b"-----BEGIN CERTIFICATE")
    u = usos()
    assert len(u) == 1 and u[0]["operacion"] == "consulta" and u[0]["envio_id"] is None
    assert u[0]["representante_nif"] == NIF_GESTORIA and u[0]["tls"]
    vistos = c.ok(c.get(f"/api/yo/certificados/{cid}/usos"))
    assert any(v["accion"] == "certificado.uso" and '"consulta"' in v["detalle"] for v in vistos)
    # Remisión por el camino de pruebas (transporte local): exactamente un uso, con el envío
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    cola = db.filas(con, "SELECT * FROM registros WHERE factura_id=?", (fid,))
    remision._enviar(con, eid, "pruebas", cola)
    assert pruebas_sin_red == [remision.ENDPOINTS_SOAP["pruebas"]["sello"]]
    u = usos()
    assert len(u) == 2 and u[1]["operacion"] == "remision" and u[1]["envio_id"] and u[1]["representante_nif"] == NIF_GESTORIA
    # Con el interruptor cerrado (como en el resto de pruebas) no se llega a elegir certificado
    from app import config
    monkeypatch.setattr(remision, "ajustes", config.ajustes)
    with pytest.raises(simulador.NoEnviado, match="deshabilitada"):
        remision._preparar_destino(con, "pruebas", eid)
    assert len(usos()) == 2
