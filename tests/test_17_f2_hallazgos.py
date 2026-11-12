"""Regresiones de los hallazgos S-F3 a S-F8 de la revisión adversarial de series y consulta a la AEAT.

- S-F3: la conciliación renueva el turno del remitente en cada página de la consulta, comprueba dentro de la transacción de
  escritura que el turno sigue siendo suyo y nunca toca un registro «enviando».
- S-F4: identificadores fuera del rango de SQLite → 422 (nunca 500).
- S-F5: ejercicio con 4 cifras ASCII y en un rango razonable → 422 en otro caso.
- S-F6: respuesta cuya Cabecera (NIF del obligado) o PeriodoImputacion no corresponde a lo consultado → aviso y sin conciliar.
- S-F7: avanzar el contador de una serie más allá de números ya usados en la AEAT (administrador, CSRF, confirmación,
  nunca retrocede, auditado y anotado en el diario; sobrevive a una restauración).
- S-F8 (resuelto por la unión de C y S): la consulta con certificado de un tercero lleva IndicadorRepresentante=S.

Todo es SIMULADO o local (simulador de app/simulador.py); los interruptores de comunicación real siguen desactivados y, donde
se recorre el camino de «pruebas», la red está sustituida dentro de la propia prueba. Datos ficticios (NIF de provincia 00).
"""
import json

import pytest
from conftest import Cliente, preparar_empresa
from fastapi.testclient import TestClient
from lxml import etree

import test_16_s_series_consulta as S
from app import almacen, consulta, copias, db, diario, remision, simulador
from app.config import ajustes
from app.nif import completar_cif
from app.verifactu.esquemas_xsd import NS_RCON, NS_SF

PASADO = "2000-01-01T00:00:00+00:00"


@pytest.fixture()
def tres(app_cliente):
    """Serie {n:3}/A{aa} confirmada y tres facturas (001, 002, 003) aceptadas en el simulador."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    for n in (1, 2, 3):
        b = S._borrador(c, eid, f"{n:03d}/A{aa}")
        assert b["puede_emitir"], b["incidencias"]
        S._emitir(c, b["id"], f"k{n}")
    assert S._enviar(eid)["aceptado"] == 3
    return c, eid, aa


def _cuarta_sin_respuesta(c, eid, aa, con) -> dict:
    """004 enviada sin respuesta: el simulador (la «AEAT») la tiene; en local queda «sin confirmar»."""
    b4 = S._borrador(c, eid, f"004/A{aa}")
    S._emitir(c, b4["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    assert S._enviar(eid)["estado"] == "sin_confirmar"
    return S._reg(con, f"004/A{aa}")


def _turno(con, eid) -> dict:
    return db.fila(con, "SELECT bloqueado_por, bloqueo_hasta FROM control_flujo WHERE empresa_id=? AND entorno='simulador'", (eid,))


def _estado(con, rid) -> str:
    return db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (rid,))["estado_envio"]


def _conciliar(c, eid, ids, codigo=None):
    r = c.post(f"/api/empresas/{eid}/consulta/conciliar", {"entorno": "simulador", "registro_ids": ids, "confirmo": True})
    if codigo is not None:
        assert r.status_code == codigo, r.text
    return r


# --- S-F3 ---------------------------------------------------------------------------------------------------------------
def test_sf3_conciliacion_no_pisa_el_envio_de_otro_remitente(tres, monkeypatch):
    c, eid, aa = tres
    con = db.conectar()
    r4 = _cuarta_sin_respuesta(c, eid, aa, con)
    original = consulta.consultar_aeat

    def lenta(con_, *a, **k):
        """Repro del revisor: la consulta tarda más que el plazo del turno; al terminar, otro remitente ya ha tomado el
        turno y está enviando el registro."""
        res = original(con_, *a, **k)
        con2 = db.conectar()
        con2.execute("UPDATE control_flujo SET bloqueo_hasta=? WHERE empresa_id=? AND entorno='simulador'", (PASADO, eid))
        assert remision._tomar_turno(con2, eid, "simulador", "remitente") is not None
        con2.execute("UPDATE registros SET estado_envio='enviando' WHERE id=?", (r4["id"],))
        con2.close()
        return res
    monkeypatch.setattr(consulta, "consultar_aeat", lenta)
    r = _conciliar(c, eid, [r4["id"]], 409)
    assert "turno" in r.json()["detail"]
    assert _estado(con, r4["id"]) == "enviando"                       # el registro del otro remitente no se toca
    assert _turno(con, eid)["bloqueado_por"] == "remitente"          # ni se le quita el turno
    assert not db.fila(con, "SELECT 1 FROM actividad WHERE accion='registro.conciliado_aeat'")
    monkeypatch.setattr(consulta, "consultar_aeat", original)

    # Registro «enviando» de un envío interrumpido, con el turno libre: tampoco se concilia (lo recupera la cola)
    remision._soltar_turno(con, eid, "simulador", "remitente")
    fila = next(x for x in S._consultar(c, eid)["distintos"] if x["registro_id"] == r4["id"])
    assert not fila["conciliable"] and "enviando" in fila["motivo"].lower()
    r = _conciliar(c, eid, [r4["id"]], 200).json()
    assert r["conciliados"] == [] and "enviando" in r["no_conciliados"][0]["motivo"].lower()
    assert _estado(con, r4["id"]) == "enviando"
    # Cuando vuelve a «sin confirmar», sí
    con.execute("UPDATE registros SET estado_envio='sin_confirmar' WHERE id=?", (r4["id"],))
    r = _conciliar(c, eid, [r4["id"]], 200).json()
    assert [x["registro_id"] for x in r["conciliados"]] == [r4["id"]] and _estado(con, r4["id"]) == "aceptado"
    assert _turno(con, eid)["bloqueado_por"] is None                  # la conciliación suelta su turno


def test_sf3_el_turno_se_renueva_en_cada_pagina(tres, monkeypatch):
    c, eid, aa = tres
    con = db.conectar()
    r4 = _cuarta_sin_respuesta(c, eid, aa, con)
    monkeypatch.setattr(simulador, "TAM_PAGINA_CONSULTA", 1)           # 4 registros → 4 páginas
    real = simulador.consultar
    vistos = []

    def pagina_lenta(sobre):
        """Cada página tarda más que el plazo del turno: al empezar la siguiente, el plazo ya habría vencido."""
        con2 = db.conectar()
        vistos.append(_turno(con2, eid))
        con2.execute("UPDATE control_flujo SET bloqueo_hasta=? WHERE empresa_id=? AND entorno='simulador'", (PASADO, eid))
        con2.close()
        return real(sobre)
    monkeypatch.setattr(simulador, "consultar", pagina_lenta)
    r = _conciliar(c, eid, [r4["id"]], 200).json()
    assert len(vistos) == 4 and [x["registro_id"] for x in r["conciliados"]] == [r4["id"]]
    for v in vistos:   # en cada página el turno es de la conciliación y con plazo vigente
        assert v["bloqueado_por"].startswith("conciliacion-") and remision._futuro(v["bloqueo_hasta"]), vistos

    # Si entre dos páginas otro remitente toma el turno (plazo vencido), la conciliación se detiene sin cambiar nada
    con.execute("UPDATE registros SET estado_envio='sin_confirmar', descripcion_error=NULL WHERE id=?", (r4["id"],))
    vistos.clear()

    def otro_toma_el_turno(sobre):
        con2 = db.conectar()
        vistos.append(_turno(con2, eid))
        if len(vistos) == 1:
            con2.execute("UPDATE control_flujo SET bloqueo_hasta=? WHERE empresa_id=? AND entorno='simulador'", (PASADO, eid))
            assert remision._tomar_turno(con2, eid, "simulador", "remitente") is not None
        con2.close()
        return real(sobre)
    monkeypatch.setattr(simulador, "consultar", otro_toma_el_turno)
    n_act = db.fila(con, "SELECT COUNT(*) n FROM actividad WHERE accion='registro.conciliado_aeat'")["n"]
    r = _conciliar(c, eid, [r4["id"]], 409)
    assert "turno" in r.json()["detail"] and len(vistos) == 1         # no se pidió la segunda página
    assert _estado(con, r4["id"]) == "sin_confirmar" and _turno(con, eid)["bloqueado_por"] == "remitente"
    assert db.fila(con, "SELECT COUNT(*) n FROM actividad WHERE accion='registro.conciliado_aeat'")["n"] == n_act


# --- S-F4 ---------------------------------------------------------------------------------------------------------------
def test_sf4_identificadores_fuera_de_rango_422(app_cliente):
    c = app_cliente
    eid = S._instalar(c)
    for ids in ([2 ** 70], [2 ** 63], [-(2 ** 63) - 1], [1, 2 ** 64], [0], [-1], [True], [1.0], ["1"]):
        r = _conciliar(c, eid, ids)
        assert r.status_code == 422, (ids, r.status_code, r.text)
    assert _conciliar(c, eid, [2 ** 63 - 1]).status_code == 404       # en rango pero inexistente: 404 como antes
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    with pytest.raises(consulta.ErrorConsulta) as e:                  # también en el módulo, no solo en la API
        consulta.conciliar(con, emp, 1, "simulador", [2 ** 70])
    assert e.value.estado == 422


# --- S-F5 ---------------------------------------------------------------------------------------------------------------
def test_sf5_ejercicio_con_cifras_ascii_y_rango(app_cliente):
    c = app_cliente
    eid = S._instalar(c)
    url = f"/api/empresas/{eid}/consulta"
    for ej in ("２０２６", "٢٠٢٦", "0000", "1999", "2101", "20266", "202", "2O26", ["2026"], True, 2026.0, None):
        r = c.post(url, {"entorno": "simulador", "ejercicio": ej, "periodo": "01"})
        assert r.status_code == 422 and "Ejercicio" in r.json()["detail"], (ej, r.status_code, r.text)
    for per in ("١", "１", "١١", "13", "00", "1.0"):
        r = c.post(url, {"entorno": "simulador", "ejercicio": "2026", "periodo": per})
        assert r.status_code == 422 and "Periodo" in r.json()["detail"], (per, r.status_code, r.text)
    for ej, per in ((2026, 1), ("2026", "1"), ("2000", "12"), ("2100", "01")):
        assert c.post(url, {"entorno": "simulador", "ejercicio": ej, "periodo": per}).status_code == 200, (ej, per)
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    with pytest.raises(consulta.ErrorConsulta) as e:
        consulta.consultar_aeat(con, emp, 1, "simulador", "２０２６", "01", comprobado=True)
    assert e.value.estado == 422


# --- S-F6 ---------------------------------------------------------------------------------------------------------------
def _alterar(cuerpo: bytes, nif=None, ejercicio=None, periodo=None) -> bytes:
    root = etree.fromstring(cuerpo)
    resp = root.find(f".//{{{NS_RCON}}}RespuestaConsultaFactuSistemaFacturacion")
    for ruta, valor in ((f"{{{NS_RCON}}}Cabecera/{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF", nif),
                        (f"{{{NS_RCON}}}PeriodoImputacion/{{{NS_RCON}}}Ejercicio", ejercicio),
                        (f"{{{NS_RCON}}}PeriodoImputacion/{{{NS_RCON}}}Periodo", periodo)):
        if valor:
            resp.find(ruta).text = valor
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def test_sf6_respuesta_de_otro_obligado_o_periodo(tres, monkeypatch):
    c, eid, aa = tres
    con = db.conectar()
    r4 = _cuarta_sin_respuesta(c, eid, aa, con)
    real = simulador.consultar
    hoy = db.ahora()
    otro_mes = "01" if hoy.month != 1 else "02"
    for cambio, texto in (({"nif": completar_cif("B", "0099999")}, "NIF"), ({"ejercicio": str(hoy.year - 1)}, "ejercicio"),
                          ({"periodo": otro_mes}, "periodo")):
        monkeypatch.setattr(simulador, "consultar", lambda s, cambio=cambio: _alterar(real(s), **cambio))
        r = S._consultar(c, eid)
        aviso = " ".join(r["avisos"])
        assert "no corresponde a lo consultado" in aviso and texto in aviso, (cambio, r["avisos"])
        assert r["resumen"]["conciliables"] == 0 and not any(x.get("conciliable") for x in r["distintos"]), cambio
        res = _conciliar(c, eid, [r4["id"]], 502)
        assert "no corresponde a lo consultado" in res.json()["detail"]
        assert _estado(con, r4["id"]) == "sin_confirmar"
    monkeypatch.setattr(simulador, "consultar", real)
    r = S._consultar(c, eid)                                          # la respuesta correcta: sin aviso y conciliable
    assert r["avisos"] == [] and r["resumen"]["conciliables"] == 1


# --- S-F7 ---------------------------------------------------------------------------------------------------------------
def _sim_insertar(aa: str, numeros) -> None:
    """La «AEAT» (simulador) tiene números que aquí no constan (p. ej. expedidos tras la última copia y perdidos junto con
    el diario)."""
    sim = S._sim()
    fila = dict(sim.execute("SELECT * FROM sim_registros WHERE num_serie=?", (f"003/A{aa}",)).fetchone())
    for n in numeros:
        sim.execute("INSERT INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (S.NIF, f"{n:03d}/A{aa}", fila["fecha"], "Correcta", None, None, None, f"{n:064d}", "alta", fila["recibido_en"]))


def test_sf7_avanzar_el_contador_tras_un_desastre(tres):
    c, eid, aa = tres
    con = db.conectar()
    anio = 2000 + int(aa)
    sid = db.fila(con, "SELECT id FROM series WHERE empresa_id=?", (eid,))["id"]
    _sim_insertar(aa, range(4, 10))
    copia = copias.crear_copia(con)                                   # copia anterior al avance

    # La consulta ofrece avanzar el contador desde las filas «solo en la AEAT» con número ya usado
    r = S._consultar(c, eid)
    filas = r["solo_aeat"]
    assert [x["num_serie"] for x in filas] == [f"{n:03d}/A{aa}" for n in range(4, 10)]
    for x in filas:
        assert "Número ya usado en el simulador local" in x["nota"]   # G2/FIS-5: nunca «la AEAT» en el simulador
        assert (x["serie_id"], x["serie_codigo"], x["serie_patron"], x["anio_contador"]) == (sid, "A", S.PATRON, anio)
        assert x["siguiente_local"] == 4 and x["siguiente_propuesto"] == 10   # el mayor número usado + 1, en todas las filas

    # Vista previa (sin cambiar nada): qué números se saltan
    p = c.ok(c.get(f"/api/series/{sid}/avance?anio={anio}&siguiente=10"))
    assert (p["actual"], p["ultimo_expedido"], p["siguiente"], p["siguiente_texto"]) == (4, 3, 10, f"010/A{aa}")
    assert p["saltados"] == {"desde": 4, "hasta": 9, "cantidad": 6, "primero": f"004/A{aa}", "ultimo": f"009/A{aa}",
                             "lista": [f"{n:03d}/A{aa}" for n in range(4, 10)], "completa": True}
    grande = c.ok(c.get(f"/api/series/{sid}/avance?anio={anio}&siguiente=100000"))["saltados"]
    assert grande["cantidad"] == 99996 and not grande["completa"] and len(grande["lista"]) <= 20 and grande["ultimo"] == f"99999/A{aa}"
    assert c.ok(c.get(f"/api/series/{sid}/avance?anio={anio}"))["actual"] == 4
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=?", (sid,))["siguiente"] == 4

    url = f"/api/series/{sid}/avanzar"
    ok = {"anio": anio, "actual": 4, "siguiente": 10, "confirmo": True, "motivo": "Números 004 a 009 usados en la AEAT tras un desastre"}
    # Salvaguardas: confirmación expresa, CSRF, nunca retrocede ni se queda igual, el contador no ha cambiado desde la revisión
    assert c.post(url, dict(ok, confirmo=None)).status_code == 422
    assert c.tc.post(url, json=ok).status_code == 403                 # sin CSRF
    for sig in (4, 3, 1):
        r = c.post(url, dict(ok, siguiente=sig))
        assert r.status_code == 409 and "solo puede avanzar" in r.json()["detail"], (sig, r.text)
    assert c.post(url, dict(ok, actual=3)).status_code == 409         # lo revisado no es el contador actual
    for campo, malo in (("siguiente", 2 ** 70), ("siguiente", "10"), ("siguiente", 10.5), ("siguiente", True), ("siguiente", 0),
                        ("siguiente", -5), ("siguiente", 10 ** 12), ("anio", "２０２６"), ("anio", 1999), ("anio", None),
                        ("actual", 2 ** 70), ("actual", None), ("motivo", 5), ("motivo", "x" * 201), ("motivo", "a\x00b")):
        r = c.post(url, dict(ok, **{campo: malo}))
        assert r.status_code == 422, (campo, malo, r.status_code, r.text)
    for q in ("anio=%EF%BC%92%EF%BC%90%EF%BC%92%EF%BC%96&siguiente=10", f"anio={anio}&siguiente=1e3", f"anio={anio}&siguiente=-1",
              f"anio={anio}&siguiente={2 ** 70}"):
        assert c.get(f"/api/series/{sid}/avance?{q}").status_code == 422, q
    assert c.get(f"/api/series/{2 ** 70}/avance?anio={anio}").status_code == 422
    assert c.post(f"/api/series/{2 ** 70}/avanzar", ok).status_code == 422
    assert c.post("/api/series/999999/avanzar", ok).status_code == 404
    # Solo el administrador de la empresa de la serie: un emisor de la misma empresa o el administrador de otra → 404
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "emisor@ejemplo.test", "nombre": "E", "clave": "clave-emisor-123",
                                                  "rol": "emisor"}))
    em = Cliente(TestClient(c.tc.app))
    em.csrf = em.ok(em.post("/api/sesion", {"email": "emisor@ejemplo.test", "clave": "clave-emisor-123"}))["csrf"]
    em.ok(em.put("/api/yo/clave", {"clave_actual": "clave-emisor-123", "clave_nueva": "clave-emisor-123-definitiva"}))
    assert em.post(url, ok).status_code == 404 and em.get(f"/api/series/{sid}/avance?anio={anio}").status_code == 404
    otra_eid = em.ok(em.post("/api/empresas", {"nif": completar_cif("B", "0054329"), "razon_social": "OTRA FICTICIA S.L.",
                                               "domicilio": "C/ Inventada, 2 - 28000 Madrid"}))["id"]
    assert otra_eid != eid and em.post(url, ok).status_code == 404     # administra otra empresa, no la de la serie
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=?", (sid,))["siguiente"] == 4
    assert not db.fila(con, "SELECT 1 FROM actividad WHERE accion='serie.contador_avanzado'")

    # El avance
    r = c.ok(c.post(url, ok))
    assert (r["anterior"], r["siguiente"], r["anio"], r["saltados"]["cantidad"]) == (4, 10, anio, 6)
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=?", (sid, anio))["siguiente"] == 10
    act = db.fila(con, "SELECT * FROM actividad WHERE accion='serie.contador_avanzado'")
    det = json.loads(act["detalle"])
    assert (act["empresa_id"], act["objeto"], act["objeto_id"]) == (eid, "serie", sid)
    assert (det["anterior"], det["siguiente"], det["anio"], det["saltados"]["primero"], det["saltados"]["ultimo"]) == \
        (4, 10, anio, f"004/A{aa}", f"009/A{aa}") and "desastre" in det["motivo"]
    ult = [e for e in diario.leer() if e["tipo"] == "serie_contador"][-1]["datos"]
    assert (ult["serie_id"], ult["anio"], ult["siguiente"]) == (sid, anio, 10)
    assert c.post(url, ok).status_code == 409                         # repetir: el contador ya va por el 10

    # El 004 ya no se puede expedir; el 010 sí. La consulta ya no avisa de que el contador no ha llegado
    b = S._borrador(c, eid, f"004/A{aa}")
    assert "NUM_NO_CORRELATIVO" in S._codigos(b, "bloqueo") and not b["puede_emitir"]
    b10 = S._borrador(c, eid, f"010/A{aa}")
    assert b10["puede_emitir"], b10["incidencias"]
    filas = S._consultar(c, eid)["solo_aeat"]
    assert all("Número ya usado" not in x["nota"] and "va por el 10" in x["nota"] and not x.get("siguiente_propuesto")
               for x in filas), filas

    # Una serie sin año tiene un único contador: no admite año
    sp = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "P", "patron": "PRB-{n}", "siguiente_numero": 7}))
    assert c.get(f"/api/series/{sp['id']}/avance").status_code == 409  # sin confirmar: no se avanza
    c.ok(c.post(f"/api/series/{sp['id']}/confirmar"))
    assert c.get(f"/api/series/{sp['id']}/avance?anio={anio}").status_code == 422
    pp = c.ok(c.get(f"/api/series/{sp['id']}/avance?siguiente=9"))
    assert (pp["actual"], pp["anio"], pp["saltados"]["lista"]) == (7, 0, ["PRB-7", "PRB-8"])
    c.ok(c.post(f"/api/series/{sp['id']}/avanzar", {"actual": 7, "siguiente": 9, "confirmo": True}))

    # Restaurar la copia anterior al avance: el diario lo conserva (también el de la serie creada después de la copia)
    con.close()
    rr = copias.restaurar(copia)
    assert not rr["entradas_descartadas"] and rr["contadores_avanzados"] == 2
    con = db.conectar()
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=?", (sid, anio))["siguiente"] == 10
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=0", (sp["id"],))["siguiente"] == 9
    assert db.fila(con, "SELECT patron, confirmada_en FROM series WHERE id=?", (sp["id"],))["patron"] == "PRB-{n}"
    b = S._borrador(c, eid, f"005/A{aa}")
    assert "NUM_NO_CORRELATIVO" in S._codigos(b, "bloqueo")


# --- S-F8 (no es un fallo tras la unión) --------------------------------------------------------------------------------
def test_sf8_consulta_con_certificado_de_tercero_lleva_indicador_representante(app_cliente, monkeypatch):
    """La consulta toma el representante de remision._preparar_destino (3 valores, regla de certificados.para_envio). Desde
    G2/FIS-3 no lo declara con IndicadorRepresentante=S salvo que la AEAT rechace la consulta sin él (4140). Interruptor de
    «pruebas» abierto SOLO dentro de remision y de esta prueba, con la red sustituida: el sobre se entrega al simulador
    local."""
    import requests
    from test_16_c_certificados import NIF_GESTORIA, asignar, legitimar, sello_gestoria, subir_mio

    def prohibido(*a, **k):
        raise AssertionError("intento de conexión real")
    monkeypatch.setattr(requests, "post", prohibido)
    monkeypatch.setattr(requests, "get", prohibido)
    monkeypatch.setattr(requests.Session, "request", prohibido)
    destinos = []

    def post_local(url, sobre, cp, kp):
        destinos.append(url)
        return 200, simulador.consultar(sobre)
    monkeypatch.setattr(remision, "_post", post_local)
    real = remision.ajustes()

    class Ajustes:
        def __getattr__(self, k):
            return True if k == "permitir_pruebas_aeat" else getattr(real, k)
    monkeypatch.setattr(remision, "ajustes", lambda: Ajustes())
    assert not ajustes().permitir_pruebas_aeat and not ajustes().permitir_produccion

    c = app_cliente
    eid = preparar_empresa(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    legitimar(c, eid)
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    hoy = db.ahora()
    res = consulta.consultar_aeat(con, emp, 1, "pruebas", str(hoy.year), f"{hoy.month:02d}", comprobado=True)
    assert res["representante"] and destinos == [remision.ENDPOINTS_SOAP["pruebas"]["sello"]]
    peticion = almacen.leer(f"{res['carpeta']}/pagina_001_peticion.xml")
    # G2/FIS-3: sin IndicadorRepresentante (según el XSD limitaría la respuesta a los registros que presentó el
    # representante); solo se añade si la AEAT rechaza la consulta con 4140 (tests/test_18_g2_cadena.py)
    assert b"IndicadorRepresentante" not in peticion and emp["nif"].encode() in peticion and not res["parcial"]
    usos = [json.loads(a["detalle"]) for a in db.filas(con, "SELECT detalle FROM actividad WHERE accion='certificado.uso'")]
    assert [(u["operacion"], u["representante_nif"]) for u in usos] == [("consulta", NIF_GESTORIA)]
