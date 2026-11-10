"""Regresiones de la verificación adversarial (segunda ronda). Todas SIMULADAS o locales."""
import datetime as dt
import io
import json
import os
import zlib

import pytest
from conftest import EMPRESA_DEMO, Cliente, borrador_listo, preparar_empresa, subir
from test_03_flujo import emitir, enviar, preparar_desde_bytes, subir_bytes, variante_pdf

from app import auditoria, copias, db, diario, emision, pdf_final, remision, simulador
from app.extraccion import inspeccion


def _reg(con, fid, tipo="alta"):
    return db.fila(con, "SELECT * FROM registros WHERE factura_id=? AND tipo=? ORDER BY id DESC LIMIT 1", (fid, tipo))


# --- Restauración y diario -------------------------------------------------------------------------
def test_restauracion_nunca_deja_sin_base(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    con.close()
    from app.config import ajustes
    real = os.replace

    def falla_en_el_cambio(a, b):
        if str(b) == str(ajustes().db_path):
            raise OSError("caída simulada durante el cambio")
        return real(a, b)
    monkeypatch.setattr(os, "replace", falla_en_el_cambio)
    with pytest.raises(OSError):
        copias.restaurar(copia)
    monkeypatch.setattr(os, "replace", real)
    assert ajustes().db_path.exists()
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM facturas")["n"] == 1


def test_arranque_bloqueado_si_falta_la_base_con_diario(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    from app.config import ajustes
    for suf in ("", "-wal", "-shm"):
        p = ajustes().db_path.with_name(ajustes().db_path.name + suf)
        if p.exists():
            p.unlink()
    with pytest.raises(RuntimeError, match="No se arranca"):
        copias.comprobar_arranque()


def test_restauracion_empresa_posterior_conserva_miembros_y_envios_no_colisionan(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    # Empresa nueva (superadministrador) con factura y envío posteriores a la copia
    e2 = c.ok(c.post("/api/empresas", {"nif": EMPRESA_DEMO["nif"], "razon_social": EMPRESA_DEMO["razon_social"],
                                         "domicilio": EMPRESA_DEMO["domicilio"]}))["id"]
    from conftest import configurar
    configurar(c, e2, patron="PRB-{n}", siguiente=2)
    d = subir(c, e2, "demo_borrador_factura.pdf")
    c.ok(c.post(f"/api/documentos/{d['documento_id']}/clasificacion", {"clasificacion": "preparatorio", "motivo": "x"}))
    b = c.ok(c.post(f"/api/documentos/{d['documento_id']}/borrador"))["borrador_id"]
    bb = c.ok(c.get(f"/api/borradores/{b}"))
    c.ok(c.post(f"/api/borradores/{b}/confirmaciones", {"claves": [i["clave"] for i in bb["incidencias"] if i["nivel"] == "confirmar"]}))
    c.ok(c.patch(f"/api/borradores/{b}", {"cambios": [{"campo": "fecha_documento", "valor": db.ahora().date().isoformat(), "motivo": "x"}]}))
    bb = c.ok(c.get(f"/api/borradores/{b}"))
    c.ok(c.post(f"/api/borradores/{b}/confirmaciones", {"claves": [i["clave"] for i in bb["incidencias"] if i["nivel"] == "confirmar"]}))
    c.ok(emitir(c, b, "e2-1"))
    enviar(e2)
    con.close()
    r = copias.restaurar(copia)
    assert r["facturas_recuperadas"] == 1
    assert c.get(f"/api/empresas/{e2}").status_code == 200        # el administrador sigue teniendo acceso
    # Nueva factura de la primera empresa: el envío no colisiona con ficheros de envíos posteriores a la copia
    b1 = preparar_desde_bytes(c, eid, "f166.pdf", variante_pdf("166/26"))
    f1 = c.ok(emitir(c, b1, "e1-1"))["factura_id"]
    enviar(eid)
    assert _reg(db.conectar(), f1)["estado_envio"] == "aceptado"


def test_conciliacion_de_estados_perdidos(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "error", "confirmo": True}))
    # Se pierde la última anotación (caída entre la confirmación y el diario)
    lineas = diario.ruta().read_text(encoding="utf-8").splitlines()
    diario.ruta().write_text("\n".join(l for l in lineas if '"estado_factura"' not in l) + "\n", encoding="utf-8")
    con = db.conectar()
    assert copias.reconciliar_diario(con) >= 1
    assert any(e["tipo"] == "estado_factura" and e["datos"]["estado"] == "anulada" for e in diario.leer())


def test_linea_cortada_del_diario(entorno_tmp):
    diario.anotar("prueba", {"a": 1})
    with open(diario.ruta(), "ab") as f:
        f.write(b'{"en": "2026-11-09", "tipo": "regis')   # escritura interrumpida
    diario.anotar("prueba", {"a": 2})
    e = diario.leer()
    assert [x["datos"]["a"] for x in e] == [1, 2] and diario.LINEAS_INVALIDAS


# --- Remisión ------------------------------------------------------------------------------------------
def test_fault_servidor_tras_procesar_se_confirma_por_duplicado(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    original = simulador.procesar
    monkeypatch.setattr(simulador, "procesar", lambda sobre: (original(sobre), simulador._fault("9999", "interno", servidor=True))[1])
    assert enviar(eid)["resultado"] == "fault_servidor"
    monkeypatch.setattr(simulador, "procesar", original)
    con = db.conectar()
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    assert _reg(con, fid)["estado_envio"] == "aceptado"


def test_anulacion_sin_respuesta_confirmada_por_duplicado(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "error", "confirmo": True}))
    simulador.fijar_modo("sin_respuesta", 1)
    enviar(eid)
    con = db.conectar()
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    an = _reg(con, fid, "anulacion")
    assert an["estado_envio"] == "aceptado", an["descripcion_error"]


def test_anulacion_rechazada_permite_reanular_con_rechazo_previo(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "error", "confirmo": True}))
    simulador.fijar_modo("rechazo_registro", 1)
    enviar(eid)
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert f["factura"]["estado"] == "emitida" and "anular" in f["acciones_permitidas"]
    rid = c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "de nuevo", "confirmo": True}))["registro_id"]
    con = db.conectar()
    assert db.fila(con, "SELECT rechazo_previo FROM registros WHERE id=?", (rid,))["rechazo_previo"] == "S"
    enviar(eid)
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (rid,))["estado_envio"] == "aceptado"
    assert c.ok(c.get(f"/api/facturas/{fid}"))["factura"]["estado"] == "anulada"


def test_simulador_rechazo_previo_sin_rechazo(entorno_tmp):
    from app.verifactu import huella, xmlgen
    si = xmlgen.SistemaInformatico("P", "B82991316", "FacturaPDF VF", "FV", "0.1.0", "I")
    fh = huella.fecha_hora_huso(db.ahora())
    hoy = db.ahora().strftime("%d-%m-%Y")
    an = xmlgen.registro_anulacion(xmlgen.DatosAnulacion("B82991316", "X-1", hoy, fh, "A" * 64, None, si,
                                                         rechazo_previo="S", sin_registro_previo="S"))
    sobre = xmlgen.sobre_soap(xmlgen.mensaje_remision({"nombre": "E", "nif": "B82991316"}, [xmlgen.a_texto(an)]))
    from app.verifactu import respuesta
    r = respuesta.interpretar(simulador.procesar(sobre))
    assert r.lineas[0].estado == "Incorrecto" and r.lineas[0].codigo_error == "1275"


# --- Emisión, rectificativas y estados ---------------------------------------------------------------
def _serie_rect(c, eid):
    s = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "R", "patron": "R{n}/{aa}", "tipo": "rectificativa", "siguiente_numero": 1}))
    c.ok(c.post(f"/api/series/{s['id']}/confirmar"))


def _rect(c, fid, n, base="-10.00", cuota="-2.10", total="-12.10"):
    rb = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R1", "motivo": "x"}))["borrador_id"]
    anio = db.ahora().strftime("%y")
    cambios = [{"campo": k, "valor": v, "motivo": "m"} for k, v in (("num_serie", f"R{n}/{anio}"), ("desglose.0.base", base),
               ("desglose.0.cuota", cuota), ("base_imponible", base), ("cuota_total", cuota), ("importe_total", total), ("total_a_pagar", total))]
    c.ok(c.patch(f"/api/borradores/{rb}", {"cambios": cambios}))
    return rb


def test_rectificativas_multiples_y_anulacion_de_rectificativa(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    _serie_rect(c, eid)
    rb = _rect(c, fid, 1)
    b = c.ok(c.get(f"/api/borradores/{rb}"))
    assert b["datos"]["fecha_operacion"]                      # FechaOperacion de la factura original
    assert c.ok(c.get(f"/api/borradores/{rb}?entorno=pruebas"))["incidencias"]  # (entorno distinto)
    assert "RECT_ENTORNO" in {i["codigo"] for i in c.ok(c.get(f"/api/borradores/{rb}?entorno=pruebas"))["incidencias"]}
    r1 = c.ok(emitir(c, rb, "r1"))["factura_id"]
    enviar(eid)
    orig = c.ok(c.get(f"/api/facturas/{fid}"))["factura"]
    assert b["datos"]["fecha_operacion"] == orig["fecha_expedicion"]
    xml = c.get(f"/api/registros/{c.ok(c.get(f'/api/facturas/{r1}'))['registros'][0]['id']}/xml").text
    # Si la operación original es de hoy coincide con la expedición y no se informa; si es anterior, sí
    assert ("<sf:FechaOperacion>" in xml) == (orig["fecha_expedicion"] != db.ahora().date().isoformat())
    # Segunda rectificativa sobre la misma factura
    rb2 = _rect(c, fid, 2, "-5.00", "-1.05", "-6.05")
    r2 = c.ok(emitir(c, rb2, "r2"))["factura_id"]
    enviar(eid, forzar=True)
    # Se anulan las dos rectificativas: la original vuelve a «emitida»
    for r in (r1, r2):
        c.ok(c.post(f"/api/facturas/{r}/anular", {"motivo": "por error", "confirmo": True}))
        enviar(eid)
    assert c.ok(c.get(f"/api/facturas/{fid}"))["factura"]["estado"] == "emitida"
    assert auditoria.verificar_cadena(db.conectar(), eid, "simulador")["ok"]


def test_emitir_en_otro_entorno_tras_expedir(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid, "k"))
    r = emitir(c, bid, "k", entorno="produccion")
    assert r.status_code in (403, 409)
    assert c.post(f"/api/borradores/{bid}/emitir", {"entorno": "pruebas", "clave_idempotencia": "otra", "confirmo": True}).status_code in (403, 409)


def test_borrador_modificado_durante_la_expedicion(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    real = pdf_final.verificar

    def con_cambio(*a, **k):
        cc = db.conectar()
        cc.execute("UPDATE borradores SET actualizado_en='2000-01-01' WHERE id=?", (bid,))
        cc.close()
        return real(*a, **k)
    monkeypatch.setattr(pdf_final, "verificar", con_cambio)
    r = emitir(c, bid)
    assert r.status_code == 409 and "cambiado" in r.json()["detail"]
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM facturas")["n"] == 0


def test_transiciones_de_borrador_emitido(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    assert c.post(f"/api/borradores/{bid}/descartar", {"motivo": "x"}).status_code == 409
    assert c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "observaciones", "valor": "x", "motivo": "x"}]}).status_code == 409


def test_instalaciones_distintas_por_obligado(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    e2 = c.ok(c.post("/api/empresas", {"nif": EMPRESA_DEMO["nif"], "razon_social": EMPRESA_DEMO["razon_social"], "domicilio": "x"}))["id"]
    con = db.conectar()
    a = emision.instalacion(con, eid)["numero_instalacion"]
    b = emision.instalacion(con, e2)["numero_instalacion"]
    assert a != b


# --- Seguridad --------------------------------------------------------------------------------------------
def test_privilegios_de_instalacion(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lec@ejemplo.test", "nombre": "L", "clave": "clave-lector-123", "rol": "lector"}))
    from fastapi.testclient import TestClient
    lec = Cliente(TestClient(c.tc.app))
    lec.csrf = lec.ok(lec.post("/api/sesion", {"email": "lec@ejemplo.test", "clave": "clave-lector-123"}))["csrf"]
    assert lec.post("/api/empresas", {"nif": "B00000257", "razon_social": "X", "domicilio": "y"}).status_code == 403
    assert lec.put("/api/productor", {"nombre": "X", "nif": "B00000257"}).status_code == 403
    assert lec.post("/api/simulador/modo", {"modo": "caido"}).status_code == 403
    assert c.ok(c.put("/api/productor", {"nombre": "Productor", "nif": "B82991316", "domicilio": "x"}))["nif"] == "B82991316"
    assert c.put(f"/api/empresas/{eid}", {"razon_social": ""}).status_code == 422
    assert c.put(f"/api/empresas/{eid}", {"razon_social": "X" * 121}).status_code == 422


def test_bloqueo_de_inicio_de_sesion(app_cliente):
    c = app_cliente
    preparar_empresa(c)
    for _ in range(5):
        assert c.post("/api/sesion", {"email": "admin@ejemplo.test", "clave": "mal"}).status_code == 401
    assert c.post("/api/sesion", {"email": "admin@ejemplo.test", "clave": "clave-segura-123"}).status_code == 401


def test_descarga_con_nombre_no_latino(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    from conftest import pdf
    d = subir_bytes(c, eid, "Factura_Łódź_发.pdf", pdf("demo_proforma.pdf"))
    r = c.get(f"/api/documentos/{d['documento_id']}/original")
    assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]


def test_certificado_caducado_no_cuenta(app_cliente):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID
    c = app_cliente
    eid = preparar_empresa(c)
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SELLO PRUEBA"),
                        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), "VATES-B82991316")])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key())
            .serial_number(1).not_valid_before(ahora - dt.timedelta(days=30)).not_valid_after(ahora - dt.timedelta(days=1))
            .sign(k, hashes.SHA256()))
    p12 = pkcs12.serialize_key_and_certificates(b"x", k, cert, None, BestAvailableEncryption(b"clave"))
    r = c.tc.post(f"/api/empresas/{eid}/certificados", files={"archivo": ("c.p12", p12, "application/x-pkcs12")},
                  data={"clave": "clave", "entorno": "pruebas"}, headers={"X-CSRF-Token": c.csrf})
    cert_info = c.ok(r)
    assert "blob_cifrado" not in cert_info and cert_info["representado_nif"] == "B82991316"
    req = {x["clave"]: x for x in c.ok(c.get(f"/api/empresas/{eid}/produccion"))["requisitos_pruebas"]}
    assert not req["certificado"]["cumplido"] and "vigente" in req["certificado"]["detalle"]


# --- PDF ----------------------------------------------------------------------------------------------------
def test_numero_largo_no_invade_el_qr():
    d = {"num_serie": "RECT-OBRA-DEMO-RESIDENCIAL-ALAMO-CERT-1234567/26-ABCDEFGHIJ", "emisor": {"nombre": "E", "nif": "B82991316"},
         "destinatario": {"nombre": "D", "nif": "A87754214"}, "lineas": [], "tipo_factura": "F1",
         "desglose": [{"tipo_iva": "21.00", "base": "10.00", "cuota": "2.10"}], "base_imponible": "10.00", "cuota_total": "2.10",
         "importe_total": "12.10"}
    url = "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=B82991316&numserie=X&fecha=09-11-2026&importe=12.10"
    pdf, info = pdf_final.reconstruir(d, modo="definitivo", entorno="simulador", url_qr=url, logo_png=None, fecha_expedicion="2026-11-09")
    v = pdf_final.verificar(pdf, d, url, "2026-11-09")
    assert v["ok"], v
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        q, MM = info["qr"], 72 / 25.4
        alto_pag = float(doc.pages[0].height)
        sil = q["zona_blanco_mm"] * MM
        x0, x1 = q["x_mm"] * MM - sil, (q["x_mm"] + q["lado_mm"]) * MM + sil
        y0 = alto_pag - q["y_sup_mm"] * MM - sil              # parte superior del símbolo con su zona en blanco
        y1 = y0 + q["lado_mm"] * MM + 2 * sil
        palabras = [w for w in doc.pages[0].extract_words() if w["x0"] < x1 and w["x1"] > x0 and w["top"] < y1 and w["bottom"] > y0]
        assert not palabras, palabras


def test_superposicion_descartada_si_el_original_tiene_qr():
    from test_04_pdf import original_con_cabecera_libre
    orig = original_con_cabecera_libre()
    datos = {"num_serie": "PRB-1", "fecha_documento": "2026-11-09", "emisor": {"nombre": "E", "nif": "B00000174"},
             "destinatario": {"nombre": "D", "nif": "B00000257"}, "lineas": [], "desglose": [], "importe_total": "1028.50"}
    ext = {"paginas": [{"metodo": "texto_pdf"}], "tipo_documento": {"valor": "factura"}}
    _, info = pdf_final.generar(datos, modo="definitivo", entorno="simulador", url_qr="https://x", original=orig, extraccion=ext,
                                fecha_expedicion="2026-11-09", qr_en_original=True)
    assert info["estrategia"] == "reconstruccion" and "QR" in info["motivo"]


def test_bomba_de_compresion_rechazada():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, StreamObject
    from conftest import pdf
    z = zlib.compress(b"\0" * (260 * 1024 * 1024), 9)
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf("demo_proforma.pdf"))))
    st = StreamObject()
    st._data = z
    st[NameObject("/Filter")] = NameObject("/FlateDecode")
    w._root_object[NameObject("/Bomba")] = w._add_object(st)
    b = io.BytesIO()
    w.write(b)
    with pytest.raises(inspeccion.PDFNoValido):
        inspeccion.inspeccionar(b.getvalue())


def test_fechas_no_validas_bloquean(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    for v, cod in (("01/12/2099", "FECHA_FORMATO"), ("1999-01-15", "FECHA_OP_ANTIGUA"), ("2026-02-30", "FECHA_FORMATO")):
        b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_operacion", "valor": v, "motivo": "x"}]}))
        assert cod in {i["codigo"] for i in b["incidencias"] if i["nivel"] == "bloqueo"}, v
