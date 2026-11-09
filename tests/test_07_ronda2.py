"""Regresiones de la segunda verificación adversarial. Todas SIMULADAS o locales."""
import io
import os
import shutil
import threading

import pytest
from conftest import EMPRESA_DEMO, EMPRESA_REPLICA, Cliente, borrador_listo, preparar_empresa
from test_03_flujo import emitir, enviar, preparar_desde_bytes, variante_pdf
from test_06_verificacion import _rect, _serie_rect

from app import copias, db, diario, emision, pdf_final, remision, simulador
from app.config import ajustes
from app.extraccion import inspeccion
from app.verifactu import respuesta as respmod


def _reg(con, fid, tipo="alta"):
    return db.fila(con, "SELECT * FROM registros WHERE factura_id=? AND tipo=? ORDER BY id DESC LIMIT 1", (fid, tipo))


def _quitar_lineas(pred):
    lineas = diario.ruta().read_text(encoding="utf-8").splitlines()
    diario.ruta().write_text("\n".join(l for l in lineas if not pred(l)) + "\n", encoding="utf-8")


# --- Arranque y copias ----------------------------------------------------------------------------------
def test_arranque_rechaza_base_vacia_o_atrasada(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    con.close()
    p = ajustes().db_path
    # Base atrasada (la de la copia, anterior a la factura)
    shutil.copy2(p, str(p) + ".buena")
    for suf in ("-wal", "-shm"):
        if os.path.exists(str(p) + suf):
            os.unlink(str(p) + suf)
    shutil.copy2(copia / "verifactu.sqlite3", p)
    with pytest.raises(RuntimeError, match="atrasada"):
        copias.comprobar_arranque()
    # Base vacía (0 bytes)
    open(p, "wb").close()
    with pytest.raises(RuntimeError, match="vacía|no se puede leer"):
        copias.comprobar_arranque()
    # La herramienta de copia no crea una base vacía si falta
    os.unlink(p)
    with pytest.raises(FileNotFoundError):
        db.conectar(crear=False)
    assert not p.exists()


def test_restauracion_tras_perder_el_disco_de_datos(app_cliente):
    """Se pierde todo el directorio de datos; solo quedan la copia y el diario (con su espejo de binarios)."""
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    destino_copias = ajustes().data_dir.parent / "copias_externas"
    copia = copias.crear_copia(con, destino_copias)
    b1 = preparar_desde_bytes(c, eid, "f166.pdf", variante_pdf("166/26"))
    f1 = c.ok(emitir(c, b1, "d1"))["factura_id"]
    enviar(eid)
    sha_pdf = db.fila(con, "SELECT pdf_sha256 FROM facturas WHERE id=?", (f1,))["pdf_sha256"]
    con.close()
    shutil.rmtree(ajustes().data_dir)
    r = copias.restaurar(copia)
    assert r["pdf_irrecuperables"] == [] and r["facturas_recuperadas"] == 1, r
    con = db.conectar()
    from app import almacen
    f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (f1,))
    assert almacen.sha256(almacen.leer(f["pdf_ruta"])) == sha_pdf
    doc = db.fila(con, "SELECT ruta FROM documentos WHERE id=?", (f["documento_id"],))
    assert almacen.existe(doc["ruta"])                      # el original también se recupera del espejo


def test_pdf_danado_se_repara_al_restaurar(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    con = db.conectar()
    copia = copias.crear_copia(con)
    from app import almacen
    f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (fid,))
    con.close()
    almacen.ruta_absoluta(f["pdf_ruta"]).write_bytes(b"%PDF-danado")
    r = copias.restaurar(copia)
    assert fid in r["pdf_reparados"]
    assert c.get(f"/api/facturas/{fid}/pdf").status_code == 200


def test_orden_de_recalculo_y_enviando_al_restaurar(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    con = db.conectar()
    copia = copias.crear_copia(con)
    _serie_rect(c, eid)
    rb = _rect(c, fid, 1)
    r1 = c.ok(emitir(c, rb, "r1"))["factura_id"]
    enviar(eid, forzar=True)
    c.ok(c.post(f"/api/facturas/{r1}/anular", {"motivo": "error", "confirmo": True}))
    # El envío de la anulación queda a medias («enviando» anotado) y se pierden las anotaciones de estado
    con.execute("UPDATE registros SET estado_envio='enviando' WHERE factura_id=? AND tipo='anulacion'", (r1,))
    diario.anotar_estado(con, _reg(con, r1, "anulacion")["id"])
    _quitar_lineas(lambda l: '"estado_factura"' in l)
    con.close()
    r = copias.restaurar(copia)
    con = db.conectar()
    an = _reg(con, r1, "anulacion")
    assert an["estado_envio"] == "sin_confirmar" and an["incidencia"] == 1 and an["posible_entrega"] == 1
    assert remision._futuro(an["siguiente_intento_en"])
    assert db.fila(con, "SELECT incidencia_desde FROM control_flujo WHERE empresa_id=?", (eid,))["incidencia_desde"]
    estados = {x["id"]: x["estado"] for x in db.filas(con, "SELECT id, estado FROM facturas")}
    assert estados[r1] == "anulada" and estados[fid] == "emitida", estados


def test_linea_alterada_del_diario_no_se_reproduce(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    con.close()
    datos = diario.ruta().read_bytes().splitlines()
    i = next(k for k, l in enumerate(datos) if b'"tipo": "registro"' in l)
    datos[i] = datos[i].replace(b"Asistencia", b"Asistenci\xff")
    diario.ruta().write_bytes(b"\n".join(datos) + b"\n")
    # a) Base viva intacta: restaurar perdería su registro -> se detiene sin tocar nada
    with pytest.raises(RuntimeError, match="se detiene"):
        copias.restaurar(copia)
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM registros")["n"] == 1
    # b) Base perdida: la línea dañada no se reproduce y la restauración exige revisión expresa
    os.unlink(ajustes().db_path)
    with pytest.raises(RuntimeError, match="ilegibles"):
        copias.restaurar(copia)
    r = copias.restaurar(copia, forzar=True)
    # El intento (a) concilió antes la base viva: el registro volvió a anotarse íntegro y se recupera
    assert r["registros_recuperados"] == 1 and r["lineas_diario_ilegibles"]


def test_cambio_de_rol_posterior_a_la_copia(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "x@ejemplo.test", "nombre": "X", "clave": "clave-x-123456", "rol": "admin"}))
    con = db.conectar()
    copia = copias.crear_copia(con)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "x@ejemplo.test", "nombre": "X", "clave": "clave-x-123456", "rol": "lector"}))
    _, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    con.close()
    copias.restaurar(copia)
    con = db.conectar()
    uid = db.fila(con, "SELECT id FROM usuarios WHERE email='x@ejemplo.test'")["id"]
    assert db.fila(con, "SELECT rol FROM miembros WHERE usuario_id=? AND empresa_id=?", (uid, eid))["rol"] == "lector"


# --- Remisión ----------------------------------------------------------------------------------------------
def test_fallo_del_diario_no_pisa_la_respuesta(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    real = diario.anotar_estado
    llamadas = {"n": 0}

    def falla_tras_respuesta(con, rid):
        llamadas["n"] += 1
        if llamadas["n"] > 1:            # la marca «enviando» se anota; la anotación posterior a la respuesta falla
            raise OSError("disco lleno")
        return real(con, rid)
    monkeypatch.setattr(diario, "anotar_estado", falla_tras_respuesta)
    enviar(eid)
    con = db.conectar()
    assert _reg(con, fid)["estado_envio"] == "aceptado"


def test_duplicado_de_anulacion_con_alta_vigente_no_se_confirma(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    enviar(eid)
    rid = c.ok(c.post(f"/api/facturas/{fid}/anular", {"motivo": "x", "confirmo": True}))["registro_id"]
    con = db.conectar()
    con.execute("UPDATE registros SET posible_entrega=1, estado_envio='enviando' WHERE id=?", (rid,))
    reg = db.fila(con, "SELECT * FROM registros WHERE id=?", (rid,))
    with db.transaccion(con):
        envio = con.execute("INSERT INTO envios (empresa_id, entorno, destino, iniciado_en, num_registros) VALUES (?,?,?,?,1)",
                            (eid, "simulador", "prueba", db.utc_iso())).lastrowid
    linea = respmod.LineaRespuesta(reg["id_emisor"], reg["num_serie"], reg["fecha_expedicion"], "Anulacion", "Incorrecto", "3000",
                                   "Registro de facturación duplicado.", {"id_peticion": "X", "estado": "Correcta"})
    remision._aplicar_respuesta(con, envio, eid, "simulador", [reg], respmod.Respuesta(tipo="respuesta", estado_envio="Incorrecto",
                                                                                         tiempo_espera=60, lineas=[linea]))
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (rid,))["estado_envio"] == "rechazado"
    assert db.fila(con, "SELECT estado FROM facturas WHERE id=?", (fid,))["estado"] == "emitida"


def test_rechazo_de_envio_completo_marca_incidencia(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    simulador.fijar_modo("rechazo", 1)
    enviar(eid)
    reg = _reg(db.conectar(), fid)
    assert reg["estado_envio"] == "bloqueado" and reg["incidencia"] == 1


def test_caracteres_de_control_en_la_empresa(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    assert c.put(f"/api/empresas/{eid}", {"razon_social": "EMPRESA\x01 S.L."}).status_code == 422


def test_instalacion_concurrente_crea_un_solo_superadmin(entorno_tmp):
    from fastapi.testclient import TestClient
    from app.main import crear_app
    with TestClient(crear_app()) as tc:
        res = []

        def instalar(n):
            res.append(tc.post("/api/instalar", json={"email": f"a{n}@ejemplo.test", "nombre": "A", "clave": "clave-segura-123",
                                                      "empresa": dict(EMPRESA_REPLICA)}).status_code)
        hs = [threading.Thread(target=instalar, args=(n,)) for n in range(4)]
        [h.start() for h in hs]
        [h.join() for h in hs]
        assert sorted(res).count(200) == 1
        assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM usuarios WHERE superadmin=1")["n"] == 1


# --- PDF y entradas hostiles --------------------------------------------------------------------------------------
def test_superposicion_no_aplica_con_rotacion_o_cajas_distintas():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject
    from test_04_pdf import original_con_cabecera_libre
    base = original_con_cabecera_libre()
    for modo in ("rotar", "cropbox"):
        w = PdfWriter()
        w.append(PdfReader(io.BytesIO(base)))
        if modo == "rotar":
            w.pages[0].rotate(90)
        else:
            w.pages[0].cropbox = RectangleObject([50, 50, 545, 792])
        b = io.BytesIO()
        w.write(b)
        assert pdf_final.superponer(b.getvalue(), entorno="pruebas", url_qr="https://x") is None, modo


def test_imagen_desproporcionada_rechazada():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, StreamObject
    from conftest import pdf
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf("demo_proforma.pdf"))))
    img = StreamObject()
    img._data = b"\x00" * 16
    img.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(40000), NameObject("/Height"): NumberObject(40000),
                NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8),
                NameObject("/Filter"): NameObject("/JPXDecode")})
    ref = w._add_object(img)
    res = w.pages[0][NameObject("/Resources")]
    res[NameObject("/XObject")] = DictionaryObject({NameObject("/Im9"): ref})
    b = io.BytesIO()
    w.write(b)
    with pytest.raises(inspeccion.PDFNoValido, match="desproporcionada"):
        inspeccion.inspeccionar(b.getvalue())


def test_razon_social_larga_sin_logo_no_invade_el_qr():
    d = {"num_serie": "1/26", "tipo_factura": "F1", "lineas": [], "desglose": [{"tipo_iva": "21.00", "base": "10.00", "cuota": "2.10"}],
         "emisor": {"nombre": "SOCIEDAD COOPERATIVA DE INGENIERÍA Y ARQUITECTURA DEL NORTE PENINSULAR Y SERVICIOS ASOCIADOS S.L.",
                    "nif": "B82991316"}, "destinatario": {"nombre": "D", "nif": "A87754214"},
         "base_imponible": "10.00", "cuota_total": "2.10", "importe_total": "12.10"}
    url = "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=B82991316&numserie=1%2F26&fecha=09-11-2026&importe=12.10"
    pdf, info = pdf_final.reconstruir(d, modo="definitivo", entorno="simulador", url_qr=url, logo_png=None, fecha_expedicion="2026-11-09")
    import pdfplumber
    q, MM = info["qr"], 72 / 25.4
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        alto = float(doc.pages[0].height)
        sil = q["zona_blanco_mm"] * MM
        x0, x1 = q["x_mm"] * MM - sil, (q["x_mm"] + q["lado_mm"]) * MM + sil
        y0 = alto - q["y_sup_mm"] * MM - sil
        y1 = y0 + q["lado_mm"] * MM + 2 * sil
        palabras = [w for w in doc.pages[0].extract_words() if w["x0"] < x1 and w["x1"] > x0 and w["top"] < y1 and w["bottom"] > y0]
        assert not palabras, palabras
        assert "PENINSULAR" in doc.pages[0].extract_text()   # el nombre completo, no truncado
    assert pdf_final.verificar(pdf, d, url, "2026-11-09")["ok"]
