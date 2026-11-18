"""Carril A (paquetes 1 y 2 de la propuesta «VERI*FACTU sencillo»): una sola pantalla de revisión con confirmaciones ligeras,
clasificación automática de las proformas claras y numeración desde el borrador. Las garantías no cambian: puede_emitir()
y su segunda comprobación al emitir, ningún dato del cliente, importe, tipo, fecha ni número se da por bueno sin que el
usuario lo vea, aislamiento estricto (404) de las rutas nuevas. Solo datos sintéticos."""
import io
import json
from pathlib import Path

import pytest
from conftest import EMPRESA_REPLICA, borrador_listo, preparar_empresa, subir
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app import borradores, db, series

NIF_EMP = EMPRESA_REPLICA["nif"]
RAIZ = Path(__file__).resolve().parents[1]


def emitir(c, bid, clave="a-1", entorno="simulador"):
    return c.post(f"/api/borradores/{bid}/emitir", {"entorno": entorno, "clave_idempotencia": clave, "confirmo": True})


# --- Validación: qué se confirma, qué es un aviso y qué se resuelve solo ----------------------------------------------------
def _campo(valor, conf=0.97, metodo="texto_pdf"):
    return {"valor": valor, "confianza": conf, "metodo": metodo, "pagina": 1, "bbox": None, "avisos": []}


def _ext(**dudosos):
    """Extracción mínima: todo leído con seguridad salvo los campos indicados (ruta → (valor, confianza))."""
    ext = {"numero": _campo("166/26"), "fecha": _campo(db.ahora().date().isoformat()),
           "emisor": {"nombre": _campo(EMPRESA_REPLICA["razon_social"]), "nif": _campo(NIF_EMP),
                      "domicilio": _campo(EMPRESA_REPLICA["domicilio"])},
           "destinatario": {"nombre": _campo("CLIENTE FICTICIO S.A."), "nif": _campo("A87754214"),
                            "domicilio": _campo("Avda. Sintética 7, 41000 Sevilla")},
           "iban": _campo("ES0000000000000000000000"), "forma_pago": _campo("Transferencia"),
           "referencias": {"obra": _campo("OBRA DEMO"), "pedido": _campo("PED-1")}}
    for ruta, (valor, conf) in dudosos.items():
        partes = ruta.split(".")
        nodo = ext
        for p in partes[:-1]:
            nodo = nodo[p]
        nodo[partes[-1]] = _campo(valor, conf)
    return ext


def _datos(ext, **cambios):
    d = {"num_serie": ext["numero"]["valor"], "fecha_documento": ext["fecha"]["valor"], "fecha_operacion": None,
         "descripcion_operacion": "Servicio", "emisor": {k: ext["emisor"][k]["valor"] for k in ("nombre", "nif", "domicilio")},
         "destinatario": {k: ext["destinatario"][k]["valor"] for k in ("nombre", "nif", "domicilio")},
         "lineas": [{"concepto": "Servicio", "cantidad": None, "unidad": None, "precio": None, "importe": "100.00"}],
         "desglose": [{"tipo_iva": "21", "base": "100.00", "cuota": "21.00", "calificacion": "S1"}],
         "base_imponible": "100.00", "cuota_total": "21.00", "importe_total": "121.00", "total_a_pagar": "121.00",
         "retencion": None, "referencias": {}, "iban": ext["iban"]["valor"], "forma_pago": "Transferencia", "indicios": [],
         "confirmaciones": []}
    d.update(cambios)
    return d


@pytest.fixture()
def ctx(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    emp = dict(db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,)), ambito_dict={"puede_emitir": True})
    yield c, eid, con, emp
    con.close()


def _validar(con, emp, d, ext, cambiados=None):
    return borradores.validar(con, emp, {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}, d,
                              extraccion=ext, cambiados=cambiados,
                              serie={"codigo": "A", "siguiente_numero": 166, "numero_detectado": 166})


def _por_campo(inc, codigo="DUDOSO"):
    return {i["campo"]: i["nivel"] for i in inc if i["codigo"] == codigo}


def test_dudas_en_datos_no_fiscales_son_avisos_y_en_los_fiscales_confirmaciones(ctx):
    _, _, con, emp = ctx
    ext = _ext(**{"iban": ("ES0000000000000000000000", 0.6), "forma_pago": ("Transferencia", 0.5),
                  "referencias.obra": ("OBRA DEMO", 0.48), "referencias.pedido": ("PED-1", 0.5),
                  "numero": ("166/26", 0.8), "destinatario.nif": ("A87754214", 0.4), "destinatario.nombre": ("CLIENTE FICTICIO S.A.", 0.5)})
    inc = _validar(con, emp, _datos(ext), ext)
    niveles = _por_campo(inc)
    # IBAN, forma de pago, obra y pedido: se imprimen pero no van a la AEAT → aviso en ámbar, sin confirmación obligatoria
    for campo in ("iban", "forma_pago", "referencias.obra", "referencias.pedido"):
        assert niveles[campo] == "aviso", (campo, niveles)
    # El número y los datos del cliente siguen exigiendo la revisión expresa
    for campo in ("num_serie", "destinatario.nif", "destinatario.nombre"):
        assert niveles[campo] == "confirmar", (campo, niveles)
    assert not borradores.puede_emitir(inc)
    claves = [i["clave"] for i in inc if i["nivel"] == "confirmar"]
    assert borradores.puede_emitir([dict(i, confirmado=i.get("clave") in claves) for i in inc])


def test_nif_y_nombre_del_emisor_se_resuelven_solo_si_coinciden_con_la_empresa(ctx):
    _, _, con, emp = ctx
    ext = _ext(**{"emisor.nif": (NIF_EMP, 0.36), "emisor.nombre": ("Ingeniería ficticia y asociados, S.L.U.", 0.5)})
    niveles = _por_campo(_validar(con, emp, _datos(ext), ext))
    assert "emisor.nif" not in niveles and "emisor.nombre" not in niveles, niveles
    # Otro NIF u otro nombre: la duda sigue (y el NIF distinto, además, bloquea)
    ext = _ext(**{"emisor.nif": ("A87754214", 0.36), "emisor.nombre": ("OTRA EMPRESA S.L.", 0.5)})
    inc = _validar(con, emp, _datos(ext, destinatario={"nombre": "CLIENTE", "nif": "B00000174", "domicilio": "C/ X 1, 28000 Madrid"}), ext)
    assert _por_campo(inc)["emisor.nif"] == "confirmar" and _por_campo(inc)["emisor.nombre"] == "confirmar"
    assert "EMISOR_NO_EMPRESA" in {i["codigo"] for i in inc if i["nivel"] == "bloqueo"}
    # Nunca para el cliente aunque su NIF sea válido; ni si el borrador tiene otro NIF que la lectura
    ext = _ext(**{"emisor.nif": (NIF_EMP, 0.36)})
    inc = _validar(con, emp, _datos(ext, emisor={"nombre": EMPRESA_REPLICA["razon_social"], "nif": "A87754214", "domicilio": "x"}), ext)
    assert _por_campo(inc).get("emisor.nif") == "confirmar"
    assert not borradores.dudoso_resuelto_por_empresa("destinatario.nif", NIF_EMP, emp, {"destinatario": {"nif": NIF_EMP}})
    # Si lo dudoso es el PAPEL (la empresa podría ser el cliente de una factura recibida), la duda se mantiene
    for marca in ({"papel_dudoso": True}, {"avisos": ["Rol deducido: NIF de la empresa configurada, cabecera a la derecha"]}):
        ext = _ext(**{"emisor.nif": (NIF_EMP, 0.75)})
        ext["emisor"]["nif"].update(marca)
        assert _por_campo(_validar(con, emp, _datos(ext), ext)).get("emisor.nif") == "confirmar", marca


def test_fecha_del_documento_es_una_pregunta_que_resuelve_la_fecha_de_operacion(ctx):
    _, _, con, emp = ctx
    ext = _ext()
    d = _datos(ext, fecha_documento="2026-01-02") if db.ahora().date().isoformat() != "2026-01-02" else _datos(ext, fecha_documento="2026-01-03")
    assert "FECHA_EXPEDICION" in {i["codigo"] for i in _validar(con, emp, d, ext) if i["nivel"] == "confirmar"}
    # El usuario indica (o vacía) la fecha de operación: la pregunta queda respondida
    for fo in (d["fecha_documento"], None):
        inc = _validar(con, emp, dict(d, fecha_operacion=fo), ext, cambiados={"fecha_operacion"})
        assert "FECHA_EXPEDICION" not in {i["codigo"] for i in inc}


def test_retencion_sin_reinterpretar_es_aviso(ctx):
    _, _, con, emp = ctx
    ext = _ext()
    d = _datos(ext, retencion={"porcentaje": "15", "importe": "15.00"}, total_a_pagar="106.00")
    ret = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "RETENCION"]
    assert [i["nivel"] for i in ret] == ["aviso"] and "Confirme" not in ret[0]["mensaje"]
    ext["total_factura"] = dict(_campo("121.00"), liquido_impreso="106.00")
    ret = [i for i in _validar(con, emp, d, ext) if i["codigo"] == "RETENCION"]
    assert [i["nivel"] for i in ret] == ["confirmar"]


# --- API: revisión, nota opcional e historial con procedencia ---------------------------------------------------------------
def test_emitir_sigue_rechazando_confirmaciones_pendientes_y_bloqueos(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b = borrador_listo(c, eid, confirmar_todo=False)
    assert any(i["nivel"] == "confirmar" for i in b["incidencias"]) and not b["puede_emitir"]
    r = emitir(c, bid)
    assert r.status_code == 409 and r.json()["incidencias"]
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM facturas")["n"] == 0
    # «He revisado los campos marcados» registra a la vez todas las claves pendientes y entonces sí se puede emitir
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    b = c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    assert b["puede_emitir"]
    # Cualquier cambio posterior exige revisar de nuevo (el servidor lo vuelve a comprobar al emitir)
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "observaciones", "valor": "Nota"}]}))
    assert not b["puede_emitir"] and emitir(c, bid, "a-2").status_code == 409


def test_cambios_sin_motivo_con_historial_automatico_y_procedencia(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "observaciones", "valor": "Gracias"}]}))
    ult = b["cambios"][-1]
    assert (ult["campo"], ult["origen"], ult["nota"], ult["motivo"], ult["usuario"]) == ("observaciones", "manual", "", "", "Administración")
    assert ult["en"] and json.loads(ult["valor_nuevo"]) == "Gracias"
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "fecha_operacion", "valor": None, "origen": "pregunta",
                                                            "motivo": "La operación es de hoy"}]}))
    assert (b["cambios"][-1]["origen"], b["cambios"][-1]["nota"]) == ("pregunta", "La operación es de hoy")
    guardado = db.fila(db.conectar(), "SELECT motivo FROM cambios_borrador ORDER BY id DESC LIMIT 1")["motivo"]
    assert guardado == "[pregunta] La operación es de hoy"
    assert c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "observaciones", "valor": "x", "origen": "aeat"}]}).status_code == 422
    assert borradores.separar_motivo("Total mal sumado") == ("manual", "Total mal sumado")


def test_numero_no_correlativo_con_arreglo_de_un_clic_que_no_avanza_el_contador(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c, siguiente=170)          # la serie espera el 170; el documento trae el 166
    _, bid, b = borrador_listo(c, eid, confirmar_todo=False)
    inc = next(i for i in b["incidencias"] if i["codigo"] == "NUM_NO_CORRELATIVO")
    aa = db.ahora().strftime("%y")
    assert inc["arreglo"] == {"campo": "num_serie", "valor": f"170/{aa}", "origen": "serie",
                              "texto": f"Usar el siguiente número de la serie: 170/{aa}"}
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [{"campo": "num_serie", "valor": inc["arreglo"]["valor"], "origen": "serie"}]}))
    assert "NUM_NO_CORRELATIVO" not in {i["codigo"] for i in b["incidencias"]} and b["datos"]["num_serie"] == f"170/{aa}"
    assert b["cambios"][-1]["origen"] == "serie"
    sid = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]["id"]
    assert series.siguiente(db.conectar(), sid, 2000 + int(aa)) == 170          # el contador no se ha tocado


def test_numero_de_proforma_sin_serie_propone_el_siguiente_de_la_unica_serie(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c, patron="F-{aaaa}/{n:4}", siguiente=240)
    _, bid, b = borrador_listo(c, eid, confirmar_todo=False)       # «166/26» no es de la serie F-…
    inc = next(i for i in b["incidencias"] if i["codigo"] == "SERIE_NO_CONFIRMADA")
    anio = db.ahora().year
    assert inc["arreglo"]["valor"] == f"F-{anio}/0240"
    # y también la propuesta de una numeración nueva con ese número (con la indicación de que ya hay otra serie)
    p = b["serie_propuesta"]
    assert p["patron"] == "{n}/{aa}" and p["hay_otras"] and p["ejemplos"][0] == f"166/{anio % 100:02d}"


# --- Serie desde el borrador ---------------------------------------------------------------------------------------------
def _sin_serie(c):
    r = c.post("/api/instalar", {"email": "admin@ejemplo.test", "nombre": "Administración", "clave": "clave-segura-123",
                                 "empresa": EMPRESA_REPLICA})
    d = c.ok(r)
    c.csrf = d["csrf"]
    eid = d["empresas"][0]["id"]
    c.ok(c.put(f"/api/empresas/{eid}", {"tipo_contribuyente": "sociedad_IS", "territorio": "comun", "sii": 0,
                                        "canarias_ceuta_melilla": 0, "otras_exclusiones": "", "origen_pdf": "plantilla_ofimatica"}))
    c.ok(c.post(f"/api/empresas/{eid}/ambito/confirmar", {"territorio_foral": False, "sii": False, "canarias_ceuta_melilla": False}))
    return eid


def test_serie_propuesta_se_confirma_desde_el_borrador_en_una_accion(app_cliente):
    c = app_cliente
    eid = _sin_serie(c)
    did = subir(c, eid, "replica_166-26.pdf")["documento_id"]
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio"}))   # sin motivo
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    p = b["serie_propuesta"]
    aa = db.ahora().strftime("%y")
    assert (p["patron"], p["numero"], p["codigo"], p["tipo"], p["entorno"]) == ("{n}/{aa}", 166, "A", "ordinaria", "simulador")
    assert p["ejemplos"] == [f"166/{aa}", f"167/{aa}"] and not p["hay_otras"]
    assert "SERIE_NO_CONFIRMADA" in {i["codigo"] for i in b["incidencias"]}
    # Lo visto por el usuario debe coincidir con la propuesta actual
    assert c.post(f"/api/borradores/{bid}/serie", {"entorno": "simulador", "patron": "{n}", "siguiente_numero": 166}).status_code == 409
    assert c.post(f"/api/borradores/{bid}/serie", {"entorno": "x", "patron": p["patron"], "siguiente_numero": 166}).status_code == 422
    b = c.ok(c.post(f"/api/borradores/{bid}/serie", {"entorno": "simulador", "patron": p["patron"], "siguiente_numero": 166}))
    assert b["serie_detectada"]["patron"] == "{n}/{aa}" and b["serie_propuesta"] is None
    assert "SERIE_NO_CONFIRMADA" not in {i["codigo"] for i in b["incidencias"]}
    s = c.ok(c.get(f"/api/empresas/{eid}/series"))
    assert len(s) == 1 and s[0]["confirmada_en"] and s[0]["siguiente_numero"] == 166 and s[0]["codigo"] == "A"
    # Repetirlo no crea otra serie
    assert c.post(f"/api/borradores/{bid}/serie", {"entorno": "simulador", "patron": p["patron"], "siguiente_numero": 166}).status_code == 409
    acc = [a["accion"] for a in db.filas(db.conectar(), "SELECT accion FROM actividad WHERE objeto='serie'")]
    assert acc == ["serie.creada", "serie.confirmada"]


def test_serie_desde_el_borrador_solo_para_administradores_y_404_a_otros(app_cliente):
    import test_16_u_multiusuario as T
    c = app_cliente
    eid = _sin_serie(c)
    did = subir(c, eid, "replica_166-26.pdf")["documento_id"]
    b0 = c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "crear_borrador": True}))
    bid = b0["borrador_id"]
    assert bid
    cuerpo = {"entorno": "simulador", "patron": "{n}/{aa}", "siguiente_numero": 166}
    # Un emisor de la empresa (no administrador) y una persona ajena: 404, sin crear nada
    T.abrir_registro(c)
    for email, rol_ in (("emi@ejemplo.test", "emisor"), ("ajena@ejemplo.test", None)):
        otro = T.otro(c)
        assert T.registrar(otro, email).status_code == 200
        if rol_:
            con = db.conectar()
            with db.transaccion(con):
                con.execute("INSERT INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,?,?, 'empleado_autorizado')",
                            (T.uid_de(email), eid, rol_))
            con.close()
            assert otro.get(f"/api/borradores/{bid}").status_code == 200
        assert otro.post(f"/api/borradores/{bid}/serie", cuerpo).status_code == 404
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM series")["n"] == 0
    # La ruta nueva entra en el barrido de aislamiento (identificador {bid})
    assert ("/api/borradores/{bid}/serie", {"POST"}) in T._rutas_api(c.tc.app)


# --- Clasificación automática y pregunta llana ---------------------------------------------------------------------------
def test_proforma_clara_se_abre_como_borrador_y_cambiar_descarta_el_borrador(app_cliente):
    """Proforma sintética de la empresa: se abre su borrador; «Cambiar» a «ya entregada» la archiva y descarta el borrador."""
    c = app_cliente
    eid = preparar_empresa(c)
    datos = _proforma()
    r = c.ok(c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", ("pro.pdf", datos, "application/pdf"))],
                       headers={"X-CSRF-Token": c.csrf}))[0]
    assert r.get("clasificacion_automatica") and r["borrador_id"], r
    bid, did = r["borrador_id"], r["documento_id"]
    d0 = c.ok(c.get(f"/api/documentos/{did}"))
    assert d0["documento"]["clasificacion"] == "preparatorio" and d0["documento"]["clasificacion_confirmada_en"]
    assert d0["clasificacion_sugerida"]["confirmacion"]["automatica"] is True
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert b["clasificacion_automatica"] and not b["puede_emitir"]          # sigue necesitando la revisión y «Emitir»
    act = db.fila(db.conectar(), "SELECT detalle FROM actividad WHERE accion='documento.clasificado' AND objeto_id=?", (did,))
    assert json.loads(act["detalle"])["automatica"] is True
    doc = c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "emitida_propia"}))
    assert doc["documento"]["clasificacion"] == "emitida_propia" and doc["borrador_id"] is None
    assert db.fila(db.conectar(), "SELECT estado FROM borradores WHERE id=?", (bid,))["estado"] == "descartado"
    # Volver a «proforma» con «crear_borrador» abre un borrador nuevo en la misma acción
    doc = c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "crear_borrador": True}))
    assert doc["borrador_id"] and doc["borrador_id"] != bid


def test_no_se_clasifica_sola_con_firma_qr_duplicado_o_titulo_de_factura(app_cliente):
    sug = {"sugerida": "preparatorio", "confianza": 0.9}
    assert borradores.admite_clasificacion_automatica(sug, {"qr": [], "firmas": []}, {}, [])
    assert not borradores.admite_clasificacion_automatica(sug, {"firmado": True}, {}, [])
    assert not borradores.admite_clasificacion_automatica(sug, {"firmas": [{"firmado": True}]}, {}, [])
    assert not borradores.admite_clasificacion_automatica(sug, {"qr": [{"tributario": True}]}, {}, [])
    assert not borradores.admite_clasificacion_automatica(sug, {}, {"indicios": {"verifactu_existente": {}}}, [])
    assert not borradores.admite_clasificacion_automatica(sug, {}, {}, [{"tipo": "documento", "id": 1}])
    assert not borradores.admite_clasificacion_automatica({"sugerida": "preparatorio", "confianza": 0.6}, {}, {}, [])
    assert not borradores.admite_clasificacion_automatica({"sugerida": "pendiente_confirmar", "confianza": 0.5}, {}, {}, [])
    # Una factura titulada «FACTURA» de la empresa: se pregunta (no se crea borrador)
    c = app_cliente
    eid = preparar_empresa(c)
    r = subir(c, eid, "replica_166-26.pdf")
    assert not r.get("borrador_id") and r["clasificacion_sugerida"] == "pendiente_confirmar"
    # La misma proforma dos veces (otro PDF, mismo número): la segunda no se clasifica sola
    r1 = c.ok(c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", ("a.pdf", _proforma(), "application/pdf"))],
                        headers={"X-CSRF-Token": c.csrf}))[0]
    r2 = c.ok(c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", ("b.pdf", _proforma(pie="Copia"), "application/pdf"))],
                        headers={"X-CSRF-Token": c.csrf}))[0]
    assert r1.get("borrador_id") and not r2.get("borrador_id") and r2["duplicados_fiscales"]


def test_clasificacion_con_factura_expedida_no_se_cambia(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    did, bid, _ = borrador_listo(c, eid)
    c.ok(emitir(c, bid))
    assert c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "emitida_propia"}).status_code == 409
    otro = subir(c, eid, "demo_recibida.pdf")["documento_id"]
    assert c.post(f"/api/documentos/{otro}/clasificacion", {"clasificacion": "recibida", "motivo": 7}).status_code == 422
    assert db.fila(db.conectar(), "SELECT estado FROM borradores WHERE id=?", (bid,))["estado"] == "emitido"


def _proforma(pie="Documento de prueba") -> bytes:
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=A4, invariant=1)
    W, H = A4
    hoy = db.ahora().strftime("%d/%m/%Y")
    for x, y, s, tam, der, neg in (
            (18, 20, EMPRESA_REPLICA["razon_social"], 11, False, True), (18, 26, "C/ Inventada de Pruebas, 99", 9, False, False),
            (18, 31, "28000 Madrid", 9, False, False), (18, 36, f"C.I.F.: {NIF_EMP}", 9, False, False),
            (192, 20, "PROFORMA", 18, True, True), (192, 28, "Proforma nº: P-0007", 10, True, False), (192, 34, f"Fecha: {hoy}", 10, True, False),
            (110, 50, "CLIENTE", 8, False, True), (110, 56, "PROMOCIONES CLIENTE S.A.", 9, False, True),
            (110, 61, "Avda. de los Datos Sintéticos, 7", 9, False, False), (110, 66, "41000 SEVILLA", 9, False, False),
            (110, 71, "C.I.F.: A87754214", 9, False, False),
            (20, 100, "CONCEPTO", 9, False, True), (190, 100, "IMPORTE", 9, True, True),
            (20, 109, "Asistencia técnica en obra", 9, False, False), (190, 109, "1.000,00", 9, True, False),
            (160, 130, "BASE IMPONIBLE", 9, True, False), (190, 130, "1.000,00 €", 9, True, False),
            (160, 136, "I.V.A. 21%", 9, True, False), (190, 136, "210,00 €", 9, True, False),
            (160, 142, "TOTAL", 9, True, True), (190, 142, "1.210,00 €", 9, True, False), (30, 280, pie, 7, False, False)):
        cv.setFont("Helvetica-Bold" if neg else "Helvetica", tam)
        (cv.drawRightString if der else cv.drawString)(x * mm, H - y * mm, s)
    cv.showPage()
    cv.save()
    return buf.getvalue()


# --- Rectificativas: «Corregir esta factura» ------------------------------------------------------------------------------
def test_corregir_factura_r4_por_defecto_con_numero_de_la_serie_r(app_cliente):
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, _ = borrador_listo(c, eid)
    fid = c.ok(emitir(c, bid))["factura_id"]
    aa = db.ahora().strftime("%y")
    rb = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"motivo": "Error en el precio"}))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{rb}"))
    # Sin serie de rectificativas: se propone «R{n}/{aa}» (la de la factura con «R» delante) y el número 1
    assert b["datos"]["tipo_factura"] == "R4" and b["datos"]["num_serie"] == f"R1/{aa}"
    p = b["serie_propuesta"]
    assert (p["patron"], p["numero"], p["tipo"], p["codigo"]) == ("R{n}/{aa}", 1, "rectificativa", "R")
    b = c.ok(c.post(f"/api/borradores/{rb}/serie", {"entorno": "simulador", "patron": p["patron"], "siguiente_numero": 1}))
    assert b["serie_detectada"] and "SERIE_NO_CONFIRMADA" not in {i["codigo"] for i in b["incidencias"]}
    tipos = {s["codigo"]: s["tipo"] for s in c.ok(c.get(f"/api/empresas/{eid}/series"))}
    assert tipos == {"A": "ordinaria", "R": "rectificativa"}
    # Con la serie confirmada, la siguiente corrección toma el siguiente número de su contador
    rb2 = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"motivo": "Otro error", "tipo": "R1"}))["borrador_id"]
    b2 = c.ok(c.get(f"/api/borradores/{rb2}"))
    assert b2["datos"]["tipo_factura"] == "R1" and b2["datos"]["num_serie"] == f"R1/{aa}"   # aún no se ha expedido la primera


def test_patron_y_codigo_propuestos():
    assert series.patron_rectificativa("{n}/{aa}") == "R{n}/{aa}"
    assert series.patron_rectificativa("F-{aaaa}/{n:4}") == "R-{aaaa}/{n:4}"
    assert series.patron_rectificativa("PRB-{n}") == "R-{n}"
    assert series.patron_rectificativa("R{n}") not in (None, "R{n}")
    assert series.patron_rectificativa("{x}") is None
    assert series.ejemplos("F-{aaaa}/{n:4}", 240, 2026) == ["F-2026/0240", "F-2026/0241"]


# --- Interfaz (sin navegador): lo que la pantalla ya no pide ---------------------------------------------------------------
def test_interfaz_revision_unica_sin_motivo_obligatorio_ni_reescribir_el_numero_fuera_de_produccion():
    js = (RAIZ / "app" / "static" / "app.js").read_text(encoding="utf-8")
    ini = js.index("async function paginaBorrador(ctx)")
    pag = js[ini:js.index("\n}\n", ini)]
    assert "Motivo del cambio (obligatorio)" not in js and "Nota (opcional)" in pag
    assert "He revisado los campos marcados" in pag and "Más opciones" in pag and "Ver original" in pag
    # escribir el número solo en producción (una única confirmación fuerte); sin casilla adicional
    assert "real ? campo(`Para confirmar, escriba el número de la factura" in pag and "He revisado los datos y confirmo" not in pag
    assert "¿Ya entregó esta factura a su cliente?" in js and "Corregir esta factura" in js
    assert ".innerHTML" not in js and "style=" not in pag
