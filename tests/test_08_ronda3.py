"""Regresiones de la tercera verificación adversarial. Todas SIMULADAS o locales."""
import io
import os

import pytest
from conftest import EMPRESA_DEMO, borrador_listo, preparar_empresa, subir
from test_03_flujo import emitir, enviar, preparar_desde_bytes, subir_bytes, variante_pdf

from app import almacen, copias, db, diario, pdf_final, remision, simulador
from app.config import ajustes


def _reg(con, fid, tipo="alta"):
    return db.fila(con, "SELECT * FROM registros WHERE factura_id=? AND tipo=? ORDER BY id DESC LIMIT 1", (fid, tipo))


def test_restaurar_concilia_antes_la_base_viva(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    real = diario.anotar_factura
    monkeypatch.setattr(diario, "anotar_factura", lambda *a, **k: (_ for _ in ()).throw(OSError("diario no disponible")))
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]          # la factura se confirma aunque el diario falle
    monkeypatch.setattr(diario, "anotar_factura", real)
    enviar(eid)
    con.close()
    r = copias.restaurar(copia)
    con = db.conectar()
    assert db.fila(con, "SELECT num_serie FROM facturas WHERE id=?", (fid,))["num_serie"] == "166/26"
    assert _reg(con, fid)["estado_envio"] == "aceptado"
    assert r["facturas_recuperadas"] == 1


def test_estado_final_sin_inflar_indicadores(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    simulador.fijar_modo("caido", 1)
    enviar(eid)                                        # conexión rechazada: no pudo llegar
    con.close()
    copias.restaurar(copia)
    reg = _reg(db.conectar(), fid)
    assert reg["estado_envio"] == "pendiente" and reg["posible_entrega"] == 0


def test_factura_danada_en_el_diario_detiene_y_reserva(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    b1 = preparar_desde_bytes(c, eid, "f166.pdf", variante_pdf("166/26"))
    c.ok(emitir(c, b1, "x1"))
    enviar(eid)
    con.close()
    datos = diario.ruta().read_bytes().splitlines()
    i = next(k for k, l in enumerate(datos) if b'"tipo": "factura"' in l)
    datos[i] = datos[i].replace(b"166/26", b"166/2X", 1)
    diario.ruta().write_bytes(b"\n".join(datos) + b"\n")
    os.unlink(ajustes().db_path)                       # se perdió la base: solo quedan la copia y el diario
    with pytest.raises(RuntimeError, match="se detiene"):
        copias.restaurar(copia)
    copias.restaurar(copia, forzar=True)
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM registros WHERE factura_id NOT IN (SELECT id FROM facturas)")["n"] == 0
    # El número del registro superviviente queda reservado: la siguiente factura no puede reutilizarlo
    b2 = preparar_desde_bytes(c, eid, "f166b.pdf", variante_pdf("166/26") + b"%x")
    assert emitir(c, b2, "x2").status_code == 409
    b3 = preparar_desde_bytes(c, eid, "f167.pdf", variante_pdf("167/26"))
    assert emitir(c, b3, "x3").status_code == 200


def test_miembros_versionados(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "x@ejemplo.test", "nombre": "X", "clave": "clave-x-123456", "rol": "admin"}))
    real = diario.anotar
    monkeypatch.setattr(diario, "anotar", lambda tipo, d: (_ for _ in ()).throw(OSError("x")) if tipo == "miembros" else real(tipo, d))
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "x@ejemplo.test", "nombre": "X", "clave": "clave-x-123456", "rol": "lector"}))
    monkeypatch.setattr(diario, "anotar", real)
    con = db.conectar()
    copia = copias.crear_copia(con)                    # la copia ya tiene X=lector (versión 3)
    con.close()
    copias.restaurar(copia)
    uid = db.fila(db.conectar(), "SELECT id FROM usuarios WHERE email='x@ejemplo.test'")["id"]
    assert db.fila(db.conectar(), "SELECT rol FROM miembros WHERE usuario_id=? AND empresa_id=?", (uid, eid))["rol"] == "lector"


def test_empresa_posterior_sin_facturas(app_cliente):
    c = app_cliente
    preparar_empresa(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    e2 = c.ok(c.post("/api/empresas", {"nif": EMPRESA_DEMO["nif"], "razon_social": EMPRESA_DEMO["razon_social"], "domicilio": "x"}))["id"]
    con.close()
    copias.restaurar(copia)
    assert c.get(f"/api/empresas/{e2}").status_code == 200
    assert c.post("/api/empresas", {"nif": "B00000257", "razon_social": "OTRA S.L.", "domicilio": "y"}).status_code == 200


def test_subida_excesiva_rechazada_antes_de_leerla(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    limite = 20 * ajustes().max_mb_pdf * 1024 * 1024 + 1024 * 1024
    r = c.tc.post(f"/api/empresas/{eid}/documentos", content=b"x" * 10, headers={"Content-Length": str(limite + 1),
                                                                                  "Content-Type": "multipart/form-data; boundary=z"})
    assert r.status_code == 413


def test_importacion_rechazada_no_deja_ficheros(app_cliente):
    from reportlab.pdfgen import canvas
    c = app_cliente
    eid = preparar_empresa(c)
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(6000, 6000))     # página fuera de límites
    cv.drawString(100, 100, "FACTURA")
    cv.showPage()
    cv.save()
    d = subir_bytes(c, eid, "enorme.pdf", buf.getvalue())
    assert d["estado"] == "error"
    assert not list((ajustes().almacen_dir / f"empresa_{eid}").rglob("*.pdf")) if (ajustes().almacen_dir / f"empresa_{eid}").exists() else True
    assert not list((diario.directorio() / "almacen").rglob("enorme*"))


def test_pagina_con_demasiado_texto_rechazada(app_cliente):
    from reportlab.pdfgen import canvas
    c = app_cliente
    eid = preparar_empresa(c)
    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.setFont("Helvetica", 1)
    for y in range(0, 840, 1):
        cv.drawString(0, y, "X" * 70)
    cv.showPage()
    cv.save()
    d = subir_bytes(c, eid, "texto.pdf", buf.getvalue())
    assert d["estado"] == "error" and "texto" in d["error"]


def test_verificar_no_da_por_bueno_un_fallo(monkeypatch):
    def falla(*a, **k):
        raise RuntimeError("herramienta caída")
    monkeypatch.setattr(pdf_final, "leer_qrs", falla)
    d = {"num_serie": "1", "emisor": {"nif": "B82991316"}, "destinatario": {"nif": "A87754214"}, "importe_total": "1.00"}
    from test_04_pdf import original_con_cabecera_libre
    v = pdf_final.verificar(original_con_cabecera_libre(), d, "https://x", None)
    assert v["ok"] is False


def test_superposicion_con_estado_grafico_desequilibrado_o_anotaciones():
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    url = "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=B00000174&numserie=1&fecha=09-11-2026&importe=1.00"
    # a) contenido original que termina con una transformación sin cerrar (q ... cm sin Q)
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=A4, invariant=1)
    cv.setFont("Helvetica", 10)
    cv.drawString(15 * mm, A4[1] - 80 * mm, "FACTURA Nº PRB-1   Total 1.028,50")
    cv._code.append("Q 0.3 0 0 0.3 0 0 cm")   # un Q de más rompe el aislamiento q/Q y desplazaría la capa
    cv.showPage()
    cv.save()
    assert pdf_final.superponer(buf.getvalue(), entorno="pruebas", url_qr=url) is None  # se recompone la factura
    # b) anotaciones en la primera página: no se superpone
    from test_04_pdf import original_con_cabecera_libre
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(original_con_cabecera_libre())))
    anot = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text")}))
    w.pages[0][NameObject("/Annots")] = ArrayObject([anot])
    b = io.BytesIO()
    w.write(b)
    assert pdf_final.superponer(b.getvalue(), entorno="pruebas", url_qr=url) is None


def test_fallo_al_preparar_el_envio(app_cliente, monkeypatch):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    real = almacen.guardar

    def falla(rel, *a, **k):
        if "/envios/" in rel:
            raise OSError("disco lleno")
        return real(rel, *a, **k)
    monkeypatch.setattr(almacen, "guardar", falla)
    r = enviar(eid)
    assert r["resultado"] == "no_enviado"
    con = db.conectar()
    reg = _reg(con, fid)
    assert reg["estado_envio"] == "pendiente" and reg["incidencia"] == 1 and remision._futuro(reg["siguiente_intento_en"])
    assert remision.procesar(con, eid, "simulador", forzar_flujo=True)["accion"] == "esperando_reintento"
    monkeypatch.setattr(almacen, "guardar", real)
    con.execute("UPDATE registros SET siguiente_intento_en=NULL")
    enviar(eid)
    assert _reg(con, fid)["estado_envio"] == "aceptado"
