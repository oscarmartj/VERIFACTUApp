"""Regresiones de la revisión integrada final (carril G2: cadena, consulta a la AEAT, contadores y recuperación).

- FIS-1: tras perder la base de datos y el diario y restaurar una copia antigua, la cadena continúa desde el último registro
  que tiene la AEAT (consulta → «cadena_atrasada», bloqueo de la expedición, «Reanudar la cadena…» con ancla externa),
  nunca se bifurca, y el ancla sobrevive a una restauración.
- UI-1: la propuesta de avance del contador comprueba todos los meses del ejercicio desde el consultado hasta el actual y
  no se ofrece desde una consulta de un solo número.
- FIS-3: la consulta va sin IndicadorRepresentante aunque el certificado sea de un tercero; solo tras un 4140 se repite con
  él y el resultado se marca como parcial. El simulador guarda y devuelve quién presentó cada registro.
- FIS-5: la consulta y la conciliación en el simulador nombran «el simulador local», nunca «la AEAT».
- UI-8: Configuración → Series muestra el siguiente número real (contador del año, con el patrón).
- RES-3 / FIX-4: la conciliación del arranque vuelve a anotar en el diario los avances manuales del contador.

Todo es SIMULADO o local (app/simulador.py); los interruptores de comunicación real siguen desactivados y, donde se recorre
el camino de «pruebas», la red está sustituida dentro de la propia prueba. Datos ficticios (NIF de provincia 00).
"""
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import Cliente
from fastapi.testclient import TestClient
from lxml import etree

import test_16_s_series_consulta as S
from app import almacen, auditoria, consulta, copias, db, diario, remision, simulador
from app.config import ajustes
from app.main import crear_app
from app.verifactu import xmlgen
from app.verifactu.esquemas_xsd import NS_SF

NSF = {"sf": NS_SF}
CLAVE = "clave-segura-123"


def _entrar(c: Cliente) -> None:
    c.csrf = c.ok(c.post("/api/sesion", {"email": "admin@ejemplo.test", "clave": CLAVE}))["csrf"]


def _expedir(c: Cliente, eid: int, numero: str) -> int:
    b = S._borrador(c, eid, numero)
    assert b["puede_emitir"], b["incidencias"]
    return S._emitir(c, b["id"], f"k-{numero}")


def _desastre(copia: Path) -> dict:
    """Se pierden la base de datos viva y el diario (p. ej. el mismo disco) y se restaura la copia."""
    a = ajustes()
    for suf in ("", "-wal", "-shm"):
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    shutil.rmtree(diario.directorio())
    return copias.restaurar(copia)


def _cadena_simulador() -> dict:
    """{num_serie: (huella, fecha_hora_huso, huella del RegistroAnterior o None)} tal como lo guarda el simulador."""
    out = {}
    for ns_, xml in S._sim().execute("SELECT num_serie, xml FROM sim_registros_xml"):
        r = etree.fromstring(xml.encode())
        out[ns_] = (r.findtext("sf:Huella", namespaces=NSF), r.findtext("sf:FechaHoraHusoGenRegistro", namespaces=NSF),
                    r.findtext("sf:Encadenamiento/sf:RegistroAnterior/sf:Huella", namespaces=NSF))
    return out


def _tipos_diario(tipo: str) -> list[dict]:
    return [e["datos"] for e in diario.leer() if e["tipo"] == tipo]


# --- FIS-1 ----------------------------------------------------------------------------------------------------------
def test_fis1_la_cadena_continua_desde_la_aeat_tras_un_desastre(app_cliente):
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    anio = 2000 + int(aa)
    sid = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]["id"]
    _expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)                                   # la última copia: solo tiene el 001
    con.close()
    _expedir(c, eid, f"002/A{aa}")
    _expedir(c, eid, f"003/A{aa}")
    assert S._enviar(eid)["aceptado"] == 2
    h3, fh3, _ = _cadena_simulador()[f"003/A{aa}"]

    rr = _desastre(copia)                                             # base de datos Y diario perdidos
    assert rr["facturas_recuperadas"] == 0
    assert any("sin diario" in a and "consulte a la AEAT" in a for a in rr["avisos"]), rr["avisos"]
    _entrar(c)

    # Registros de otra instalación (SistemaInformatico de la respuesta, pedido con MostrarSistemaInformatico=S): no atrasan
    sim = S._sim()
    originales = {ns_: xml for ns_, xml in sim.execute("SELECT num_serie, xml FROM sim_registros_xml")}
    for ns_ in (f"002/A{aa}", f"003/A{aa}"):
        otro = etree.fromstring(originales[ns_].encode())
        otro.find("sf:SistemaInformatico/sf:NumeroInstalacion", NSF).text = "OTRA-INSTALACION-0001"
        sim.execute("UPDATE sim_registros_xml SET xml=? WHERE num_serie=?", (etree.tostring(otro, encoding="unicode"), ns_))
    r = S._consultar(c, eid)
    peticion = almacen.leer(f"{r['evidencia']}/pagina_001_peticion.xml")
    assert b"MostrarSistemaInformatico>S<" in peticion
    assert r["cadena_atrasada"] is None and "cadena_atrasada" not in r["codigos_aviso"]
    for ns_, xml in originales.items():
        sim.execute("UPDATE sim_registros_xml SET xml=? WHERE num_serie=?", (xml, ns_))

    # La consulta detecta que la AEAT tiene registros de esta instalación posteriores al último registro local
    r = S._consultar(c, eid)
    ca = r["cadena_atrasada"]
    assert (ca["num_serie"], ca["huella"], ca["fecha_hora_huso"], ca["registros_posteriores"]) == (f"003/A{aa}", h3, fh3, 2)
    assert ca["cabeza_local"]["num_serie"] == f"001/A{aa}" and ca["de_esta_instalacion"] is True
    assert "cadena_atrasada" in r["codigos_aviso"] and "Reanudar la cadena" in r["avisos"][0]
    assert all(x["posterior_a_la_cadena"] for x in r["solo_aeat"]) and len(r["solo_aeat"]) == 2
    est = db.fila(db.conectar(), "SELECT * FROM cadenas_aeat WHERE empresa_id=?", (eid,))
    assert (est["estado"], est["huella"], est["num_serie"]) == ("atrasada", h3, f"003/A{aa}")
    assert _tipos_diario("cadena_aeat")[-1]["estado"] == "atrasada"
    assert db.fila(db.conectar(), "SELECT 1 FROM actividad WHERE accion='cadena.atrasada_aeat'")
    n_diario = len(_tipos_diario("cadena_aeat"))
    S._consultar(c, eid)                                              # repetir la consulta no vuelve a anotarlo
    assert len(_tipos_diario("cadena_aeat")) == n_diario

    # El contador se avanza como siempre (el aviso recuerda que eso no reanuda la cadena)
    x = r["solo_aeat"][0]
    assert x["siguiente_propuesto"] == 4 and x["meses_comprobados"] == [f"{db.ahora().month:02d}/{anio}"]
    p = c.ok(c.get(f"/api/series/{sid}/avance?anio={anio}&siguiente=4"))
    assert p["aviso_cadena"] and "Reanudar la cadena" in p["aviso_cadena"]
    c.ok(c.post(f"/api/series/{sid}/avanzar", {"anio": anio, "actual": 2, "siguiente": 4, "confirmo": True}))

    # Mientras la cadena está atrasada no se genera ningún registro en este entorno: ni expedir ni anular
    b4 = S._borrador(c, eid, f"004/A{aa}")
    assert "CADENA_ATRASADA" in S._codigos(b4, "bloqueo") and not b4["puede_emitir"]
    res = c.post(f"/api/borradores/{b4['id']}/emitir", {"entorno": "simulador", "clave_idempotencia": "k4", "confirmo": True})
    assert res.status_code == 409 and "CADENA_ATRASADA" in {i["codigo"] for i in res.json()["incidencias"]}
    f1 = db.fila(db.conectar(), "SELECT id FROM facturas WHERE num_serie=?", (f"001/A{aa}",))["id"]
    res = c.post(f"/api/facturas/{f1}/anular", {"motivo": "prueba", "confirmo": True})
    assert res.status_code == 409 and "Reanudar la cadena" in res.json()["detail"]
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM registros")["n"] == 1

    # Reanudar: solo administradores, con CSRF, confirmación y la huella del registro revisado (nunca la del navegador)
    url = f"/api/empresas/{eid}/consulta/reanudar-cadena"
    ok = {"entorno": "simulador", "huella": h3, "confirmo": True}
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "emisor@ejemplo.test", "nombre": "E", "clave": "clave-emisor-123",
                                                  "rol": "emisor"}))
    em = Cliente(TestClient(c.tc.app))
    em.csrf = em.ok(em.post("/api/sesion", {"email": "emisor@ejemplo.test", "clave": "clave-emisor-123"}))["csrf"]
    em.ok(em.put("/api/yo/clave", {"clave_actual": "clave-emisor-123", "clave_nueva": "clave-emisor-123-definitiva"}))
    assert em.post(url, ok).status_code == 404
    assert c.tc.post(url, json=ok).status_code == 403                 # sin CSRF
    assert c.post(url, dict(ok, confirmo=None)).status_code == 422
    for malo in (None, "x", h3.lower(), h3 + "0", 5):
        assert c.post(url, dict(ok, huella=malo)).status_code == 422, malo
    h2 = _cadena_simulador()[f"002/A{aa}"][0]
    res = c.post(url, dict(ok, huella=h2))                            # no es el último registro de la AEAT
    assert res.status_code == 409 and f"003/A{aa}" in res.json()["detail"]
    assert db.fila(db.conectar(), "SELECT estado FROM cadenas_aeat WHERE empresa_id=?", (eid,))["estado"] == "atrasada"
    rr = c.ok(c.post(url, ok))
    assert rr["reanudada"] and not rr["ya_reanudada"] and rr["ancla"]["huella"] == h3 and rr["ancla"]["num_serie"] == f"003/A{aa}"
    assert rr["meses_consultados"] == [f"{db.ahora().month:02d}/{anio}"]
    est = db.fila(db.conectar(), "SELECT * FROM cadenas_aeat WHERE empresa_id=?", (eid,))
    assert (est["estado"], est["huella"], est["fecha_hora_huso"]) == ("anclada", h3, fh3)
    det = json.loads(db.fila(db.conectar(), "SELECT detalle FROM actividad WHERE accion='cadena.reanudada_aeat'")["detalle"])
    assert det["ancla"]["huella"] == h3 and det["cabeza_local"]["num_serie"] == f"001/A{aa}" and det["evidencia"]
    assert _tipos_diario("cadena_aeat")[-1]["estado"] == "anclada"
    n_diario = len(_tipos_diario("cadena_aeat"))
    rr = c.ok(c.post(url, ok))                                        # idempotente
    assert rr["ya_reanudada"] and len(_tipos_diario("cadena_aeat")) == n_diario
    assert db.fila(db.conectar(), "SELECT COUNT(*) n FROM actividad WHERE accion='cadena.reanudada_aeat'")["n"] == 1

    # El ancla sobrevive a otra restauración de la copia antigua (diario + reproducción)
    rr = copias.restaurar(copia)
    assert rr["cadenas_aeat"] == 1 and not rr["entradas_descartadas"] and rr["avisos"] == []   # con diario: sin aviso
    _entrar(c)
    con = db.conectar()
    assert db.fila(con, "SELECT estado, huella FROM cadenas_aeat WHERE empresa_id=?", (eid,)) == {"estado": "anclada", "huella": h3}
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=?", (sid, anio))["siguiente"] == 4

    # El siguiente registro se encadena al último de la AEAT (003), no al de la copia (001): sin bifurcación
    fid4 = _expedir(c, eid, f"004/A{aa}")
    reg4 = db.fila(con, "SELECT * FROM registros WHERE factura_id=?", (fid4,))
    x4 = etree.fromstring(reg4["xml"].encode())
    assert x4.findtext("sf:Encadenamiento/sf:RegistroAnterior/sf:NumSerieFactura", namespaces=NSF) == f"003/A{aa}"
    assert x4.findtext("sf:Encadenamiento/sf:RegistroAnterior/sf:Huella", namespaces=NSF) == h3 == reg4["huella_anterior"]
    assert json.loads(reg4["ancla_aeat"])["huella"] == h3
    assert not db.fila(con, "SELECT 1 FROM cadenas_aeat WHERE empresa_id=?", (eid,))      # el ancla ya se usó
    assert S._enviar(eid)["aceptado"] == 1
    cadena = _cadena_simulador()
    anteriores = [ant for _, _, ant in cadena.values() if ant]
    assert len(anteriores) == len(set(anteriores)), "dos registros con el mismo anterior: la cadena se bifurca"
    assert cadena[f"004/A{aa}"][2] == h3
    assert auditoria.verificar_cadena(con, eid, "simulador") == {"ok": True, "registros": 2, "errores": []}
    assert S._consultar(c, eid)["cadena_atrasada"] is None
    with pytest.raises(Exception):                                    # el ancla de un registro no cambia
        con.execute("UPDATE registros SET ancla_aeat=NULL WHERE id=?", (reg4["id"],))

    # Una restauración posterior no vuelve a aplicar el ancla ya usada
    con.close()
    rr = copias.restaurar(copia)
    con = db.conectar()
    assert rr["cadenas_aeat"] == 0 and not db.fila(con, "SELECT 1 FROM cadenas_aeat")
    assert db.fila(con, "SELECT huella_anterior FROM registros WHERE num_serie=?", (f"004/A{aa}",))["huella_anterior"] == h3
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]


def test_fis1_reanudar_consulta_tambien_los_meses_de_facturas_anteriores(app_cliente):
    """El último registro de la cadena puede ser la anulación o la subsanación de una factura de un mes anterior al de la
    copia (su periodo es el de esa factura): reanudar la cadena consulta también esos meses y se ancla a él."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    _expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    _expedir(c, eid, f"002/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    _desastre(copia)
    _entrar(c)
    # Una factura local de hace 5 meses (expedida antes de la copia) y, en la «AEAT», un registro posterior de ella
    hoy = db.ahora()
    a5, m5 = (hoy.year, hoy.month - 5) if hoy.month > 5 else (hoy.year - 1, hoy.month + 7)
    fecha5 = f"{a5:04d}-{m5:02d}-10"
    con = db.conectar()
    f1 = db.fila(con, "SELECT * FROM facturas WHERE num_serie=?", (f"001/A{aa}",))
    b1 = db.fila(con, "SELECT * FROM borradores WHERE id=?", (f1["borrador_id"],))
    with db.transaccion(con):
        con.execute("INSERT INTO borradores (id, empresa_id, datos, estado, creado_en, actualizado_en) VALUES (900, ?, ?, 'emitido', ?, ?)",
                    (eid, b1["datos"], b1["creado_en"], b1["creado_en"]))
        cols = [k for k in f1 if k not in ("id",)]
        vals = dict(f1, borrador_id=900, clave_idempotencia="antigua-900", num_serie=f"900/A{aa}", numero=None,
                    serie_id=None, fecha_expedicion=fecha5)
        con.execute(f"INSERT INTO facturas ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [vals[k] for k in cols])
    meses = consulta.meses_cadena(con, eid, "simulador")
    assert (f"{a5:04d}", f"{m5:02d}") in meses and meses[-1] == (f"{hoy.year:04d}", f"{hoy.month:02d}")
    sim = S._sim()
    xml2 = sim.execute("SELECT xml FROM sim_registros_xml WHERE num_serie=?", (f"002/A{aa}",)).fetchone()[0]
    h2, fh2, _ = _cadena_simulador()[f"002/A{aa}"]
    otro = etree.fromstring(xml2.encode())
    otro.find("sf:IDFactura/sf:NumSerieFactura", NSF).text = f"900/A{aa}"
    otro.find("sf:IDFactura/sf:FechaExpedicionFactura", NSF).text = f"10-{m5:02d}-{a5:04d}"
    otro.find("sf:Encadenamiento/sf:RegistroAnterior/sf:Huella", NSF).text = h2
    otro.find("sf:FechaHoraHusoGenRegistro", NSF).text = (consulta._dt(fh2) + timedelta(seconds=1)).isoformat()
    h900 = "9" * 64
    otro.find("sf:Huella", NSF).text = h900
    fecha_sim = f"10-{m5:02d}-{a5:04d}"
    sim.execute("INSERT INTO sim_registros SELECT nif, ?, ?, estado, id_peticion, codigo, descripcion, ?, tipo, "
                "recibido_en FROM sim_registros WHERE num_serie=?", (f"900/A{aa}", fecha_sim, h900, f"002/A{aa}"))
    sim.execute("INSERT INTO sim_registros_xml VALUES (?,?,?,?,?)",
                (S.NIF, f"900/A{aa}", fecha_sim, etree.tostring(otro, encoding="unicode"), None))
    # La consulta del mes actual solo ve el 002; reanudar desde él no vale: el último es el de hace 5 meses
    r = S._consultar(c, eid)
    assert r["cadena_atrasada"]["huella"] == h2
    url = f"/api/empresas/{eid}/consulta/reanudar-cadena"
    res = c.post(url, {"entorno": "simulador", "huella": h2, "confirmo": True})
    assert res.status_code == 409 and f"900/A{aa}" in res.json()["detail"]
    assert db.fila(con, "SELECT estado, huella FROM cadenas_aeat WHERE empresa_id=?", (eid,)) == {"estado": "atrasada", "huella": h900}
    rr = c.ok(c.post(url, {"entorno": "simulador", "huella": h900, "confirmo": True}))
    assert rr["reanudada"] and f"{m5:02d}/{a5:04d}" in rr["meses_consultados"]
    sid = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]["id"]
    c.ok(c.post(f"/api/series/{sid}/avanzar", {"anio": hoy.year, "actual": 2, "siguiente": 3, "confirmo": True}))
    fid = _expedir(c, eid, f"003/A{aa}")
    assert db.fila(con, "SELECT huella_anterior FROM registros WHERE factura_id=?", (fid,))["huella_anterior"] == h900


# --- UI-1 -----------------------------------------------------------------------------------------------------------
def test_ui1_la_propuesta_de_avance_cubre_los_meses_siguientes_y_no_sale_de_un_numero(app_cliente):
    hoy = db.ahora()
    if hoy.month == 1:
        pytest.skip("En enero no hay un mes anterior del mismo ejercicio")
    c = app_cliente
    eid = S._instalar(c)
    aa = hoy.strftime("%y")
    anterior = f"{hoy.month - 1:02d}"
    simulador._db()
    sim = S._sim()
    # La «AEAT» tiene 001–003 del mes anterior y 004–006 de este mes; aquí no consta ninguno (p. ej. tras un desastre)
    for n, fecha in [(n, f"15-{anterior}-{hoy.year}") for n in (1, 2, 3)] + [(n, f"01-{hoy.month:02d}-{hoy.year}") for n in (4, 5, 6)]:
        sim.execute("INSERT INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (S.NIF, f"{n:03d}/A{aa}", fecha, "Correcta", f"{n:016d}", None, None, f"{n:064d}", "alta",
                     hoy.replace(day=1).isoformat()))
    r = S._consultar(c, eid, periodo=anterior)
    filas = r["solo_aeat"]
    assert [x["num_serie"] for x in filas] == [f"00{n}/A{aa}" for n in (1, 2, 3)]
    meses = [f"{m:02d}/{hoy.year}" for m in range(hoy.month - 1, hoy.month + 1)]
    for x in filas:                                                   # el mayor de TODOS los meses (006) + 1, y cuáles
        assert x["siguiente_propuesto"] == 7 and x["meses_comprobados"] == meses, x
    assert r["avance"] == {"meses_comprobados": meses, "fiable": True, "motivo": None}
    assert len(r["evidencias"]) == 2
    # Con «Número de factura» no se sabe cuál es el mayor: no se propone avanzar
    for periodo, numero in ((anterior, f"002/A{aa}"), (f"{hoy.month:02d}", f"005/A{aa}")):
        r = S._consultar(c, eid, periodo=periodo, num_serie=numero)
        assert [x["num_serie"] for x in r["solo_aeat"]] == [numero]
        assert "siguiente_propuesto" not in r["solo_aeat"][0] and "Número ya usado" in r["solo_aeat"][0]["nota"]
        assert "consulte el mes completo" in r["solo_aeat"][0]["nota"]
    # Este mes: la propuesta sale de este mes (es el actual)
    r = S._consultar(c, eid)
    assert {x["siguiente_propuesto"] for x in r["solo_aeat"]} == {7}
    assert r["solo_aeat"][0]["meses_comprobados"] == [f"{hoy.month:02d}/{hoy.year}"]
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "el mayor usado en la AEAT + 1" not in js and "meses: x.meses_comprobados" in js and "Meses comprobados" in js


def test_ui1_serie_sin_anio_sigue_en_los_anios_siguientes(app_cliente):
    """Una serie sin año tiene un único contador: la propuesta tiene en cuenta los meses siguientes también de los años
    siguientes, hasta el actual (la de una serie con año se queda en su ejercicio)."""
    c = app_cliente
    eid = S._instalar(c)
    hoy = db.ahora()
    sp = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "R", "patron": "R-{n}", "siguiente_numero": 1}))
    c.ok(c.post(f"/api/series/{sp['id']}/confirmar"))
    simulador._db()
    sim = S._sim()
    dic = f"15-12-{hoy.year - 1}"
    filas = [("R-3", dic), ("R-8", f"01-{hoy.month:02d}-{hoy.year}"), (f"005/A{(hoy.year - 1) % 100:02d}", dic)]
    for i, (ns_, fecha) in enumerate(filas, 1):
        sim.execute("INSERT INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (S.NIF, ns_, fecha, "Correcta", f"{i:016d}", None, None, f"{i:064d}", "alta", hoy.replace(day=1).isoformat()))
    r = c.ok(c.post(f"/api/empresas/{eid}/consulta", {"entorno": "simulador", "ejercicio": str(hoy.year - 1), "periodo": "12"}))
    por = {x["num_serie"]: x for x in r["solo_aeat"]}
    meses = [f"12/{hoy.year - 1}"] + [f"{m:02d}/{hoy.year}" for m in range(1, hoy.month + 1)]
    assert por["R-3"]["siguiente_propuesto"] == 9 and por["R-3"]["meses_comprobados"] == meses   # R-8 de este año
    assert por[f"005/A{(hoy.year - 1) % 100:02d}"]["siguiente_propuesto"] == 6                # su ejercicio: solo diciembre

    # G2V-4: hace 3 años quedan fuera de los 24 meses que se comprueban meses hasta el actual (R-30 es de este mes): la
    # propuesta no sería fiable, así que no se propone nada y se dice por qué (sin consultar los meses siguientes)
    hace3 = f"15-{hoy.month:02d}-{hoy.year - 3}"
    for i, (ns_, fecha) in enumerate((("R-20", hace3), ("R-30", f"02-{hoy.month:02d}-{hoy.year}")), 10):
        sim.execute("INSERT INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (S.NIF, ns_, fecha, "Correcta", f"{i:016d}", None, None, f"{i:064d}", "alta", hoy.replace(day=1).isoformat()))
    r = c.ok(c.post(f"/api/empresas/{eid}/consulta", {"entorno": "simulador", "ejercicio": str(hoy.year - 3),
                                                       "periodo": f"{hoy.month:02d}"}))
    x = next(x for x in r["solo_aeat"] if x["num_serie"] == "R-20")
    assert "Número ya usado" in x["nota"] and "siguiente_propuesto" not in x and "meses_comprobados" not in x
    ultimo = consulta._meses_avance(str(hoy.year - 3), f"{hoy.month:02d}", True)[0][-1]
    assert r["avance"]["fiable"] is False and r["avance"]["meses_comprobados"] == []
    assert f"no se han comprobado los meses posteriores a {ultimo[1]}/{ultimo[0]}" in r["avance"]["motivo"]
    assert "la serie «R» no lleva año" in r["avance"]["motivo"]
    assert any(a.startswith("No se propone avanzar el contador de las series: no se han comprobado") for a in r["avisos"])
    assert len(r["evidencias"]) == 1


# --- FIS-3 ----------------------------------------------------------------------------------------------------------
def test_fis3_consulta_sin_indicador_representante_y_parcial_solo_tras_4140(app_cliente):
    from test_16_c_certificados import NIF_GESTORIA, asignar, legitimar, sello_gestoria, subir_mio
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    _expedir(c, eid, f"001/A{aa}")                                    # presentado por el propio obligado
    assert S._enviar(eid)["aceptado"] == 1
    cid = subir_mio(c, sello_gestoria())["id"]                        # la empresa pasa a usar el certificado de una gestoría
    c.ok(asignar(c, eid, cid, "simulador"))
    legitimar(c, eid)
    _expedir(c, eid, f"002/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1                            # presentado por la gestoría (Cabecera/Representante)

    # Sin IndicadorRepresentante (como la remisión, la consulta del simulador usa el certificado de la gestoría): lo devuelve
    # todo, también lo que se presentó antes del cambio de certificado, y NIFPresentador dice quién lo presentó
    r = S._consultar(c, eid)
    resp = etree.fromstring(almacen.leer(f"{r['evidencia']}/pagina_001_respuesta.xml"))
    presentadores = {e.findtext(".//sf:NumSerieFactura", namespaces=NSF): e.findtext(".//sf:NIFPresentador", namespaces=NSF)
                     for e in resp.iter("{*}RegistroRespuestaConsultaFactuSistemaFacturacion")}
    assert presentadores == {f"001/A{aa}": S.NIF, f"002/A{aa}": NIF_GESTORIA}
    peticion = almacen.leer(f"{r['evidencia']}/pagina_001_peticion.xml")
    assert b"IndicadorRepresentante" not in peticion
    assert not r["parcial"] and r["codigos_aviso"] == []
    assert sorted(x["num_serie"] for x in r["coinciden"]) == [f"001/A{aa}", f"002/A{aa}"] and r["solo_local"] == []

    # El simulador respeta el indicador: solo los registros que presentó quien consulta
    hoy = db.ahora()
    sobre = xmlgen.sobre_soap(xmlgen.mensaje_consulta(nif=S.NIF, nombre=S.EMPRESA["razon_social"], ejercicio=str(hoy.year),
                                                      periodo=f"{hoy.month:02d}", representante=True))
    devueltos = etree.fromstring(simulador.consultar(sobre, NIF_GESTORIA)).findall(".//{*}RegistroRespuestaConsultaFactuSistemaFacturacion/{*}IDFactura/{*}NumSerieFactura")
    assert [x.text for x in devueltos] == [f"002/A{aa}"]

    # Un tercero sin apoderamiento para consultar (4140): se repite con el indicador y el resultado es PARCIAL
    simulador.fijar_modo("solo_como_representante", 5, servicio="consulta")
    r = S._consultar(c, eid)
    assert r["parcial"] and r["indicador_representante"] and "consulta_parcial_representante" in r["codigos_aviso"]
    assert any("PARCIAL" in a for a in r["avisos"])
    assert [x["num_serie"] for x in r["coinciden"]] == [f"002/A{aa}"]
    falta = r["solo_local"][0]
    assert falta["num_serie"] == f"001/A{aa}" and not falta["revisar"] and "parcial" in falta["nota"]
    assert r["resumen"]["revisar"] == 0 and r["cadena_atrasada"] is None
    carpeta = ajustes().almacen_dir / r["evidencia"]
    assert sorted(p.name for p in carpeta.iterdir()) == [
        "pagina_001_peticion.xml", "pagina_001_respuesta.xml",
        "representante_pagina_001_peticion.xml", "representante_pagina_001_respuesta.xml"]
    assert b"4140" in (carpeta / "pagina_001_respuesta.xml").read_bytes()
    assert b"IndicadorRepresentante>S<" in (carpeta / "representante_pagina_001_peticion.xml").read_bytes()
    simulador.fijar_modo("normal", 1, servicio="consulta")
    # Con una respuesta parcial no se reanuda la cadena
    res = c.post(f"/api/empresas/{eid}/consulta/reanudar-cadena", {"entorno": "simulador", "huella": "A" * 64, "confirmo": True})
    assert res.status_code == 409


def test_fis3_en_pruebas_la_consulta_va_sin_indicador_y_solo_lo_repite_tras_4140(app_cliente, monkeypatch):
    """El camino de «pruebas» con la red sustituida (el sobre se entrega al simulador local en el propio proceso, con el NIF
    del titular del certificado) y el interruptor abierto SOLO dentro de remision y de esta prueba."""
    import requests
    from test_16_c_certificados import NIF_GESTORIA, asignar, legitimar, sello_gestoria, subir_mio

    def prohibido(*a, **k):
        raise AssertionError("intento de conexión real")
    for obj, attr in ((requests, "post"), (requests, "get"), (requests.Session, "request")):
        monkeypatch.setattr(obj, attr, prohibido)
    destinos = []

    def post_local(url, sobre, cp, kp):
        destinos.append(url)
        return 200, simulador.consultar(sobre, NIF_GESTORIA)
    monkeypatch.setattr(remision, "_post", post_local)
    real = remision.ajustes()

    class Ajustes:
        def __getattr__(self, k):
            return True if k == "permitir_pruebas_aeat" else getattr(real, k)
    monkeypatch.setattr(remision, "ajustes", lambda: Ajustes())
    assert not ajustes().permitir_pruebas_aeat and not ajustes().permitir_produccion

    c = app_cliente
    eid = S._instalar(c)
    cid = subir_mio(c, sello_gestoria())["id"]
    c.ok(asignar(c, eid, cid, "pruebas"))
    legitimar(c, eid)
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    hoy = db.ahora()
    res = consulta.consultar_aeat(con, emp, 1, "pruebas", str(hoy.year), f"{hoy.month:02d}", comprobado=True)
    assert res["representante"] and not res["parcial"] and destinos == [remision.ENDPOINTS_SOAP["pruebas"]["sello"]]
    assert b"IndicadorRepresentante" not in almacen.leer(f"{res['carpeta']}/pagina_001_peticion.xml")
    simulador.fijar_modo("solo_como_representante", 1, servicio="consulta")
    res = consulta.consultar_aeat(con, emp, 1, "pruebas", str(hoy.year), f"{hoy.month:02d}", comprobado=True)
    assert res["parcial"] and res["indicador_representante"] and len(destinos) == 3
    assert any("4140" in a and "PARCIAL" in a for a in res["avisos"])
    assert b"IndicadorRepresentante>S<" in almacen.leer(f"{res['carpeta']}/representante_pagina_001_peticion.xml")


# --- FIS-5 ----------------------------------------------------------------------------------------------------------
def test_fis5_la_consulta_y_la_conciliacion_del_simulador_no_dicen_la_aeat(app_cliente):
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    _expedir(c, eid, f"001/A{aa}")
    simulador.fijar_modo("sin_respuesta", 1)
    assert S._enviar(eid)["estado"] == "sin_confirmar"
    r = S._consultar(c, eid)
    fila = r["distintos"][0]
    assert fila["conciliable"] and "AEAT" not in fila["motivo"], fila["motivo"]
    assert fila["motivo"].startswith("El simulador local ya tiene este registro") and r["servicio"] == "el simulador local"
    res = c.ok(c.post(f"/api/empresas/{eid}/consulta/conciliar",
                      {"entorno": "simulador", "registro_ids": [fila["registro_id"]], "confirmo": True}))
    assert len(res["conciliados"]) == 1
    desc = db.fila(db.conectar(), "SELECT descripcion_error FROM registros WHERE id=?", (fila["registro_id"],))["descripcion_error"]
    assert desc.startswith("Conciliado con la consulta al simulador local") and "AEAT" not in desc, desc
    det = json.loads(db.fila(db.conectar(), "SELECT detalle FROM actividad WHERE accion='registro.conciliado_aeat'")["detalle"])
    assert det["servicio"] == "el simulador local" and det["entorno"] == "simulador"
    # Fuera del simulador el texto sigue nombrando a la AEAT
    assert consulta.servicio("pruebas", mayuscula=True) == "La AEAT" and consulta.servicio("produccion", prep="a") == "a la AEAT"
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function nombresServicio(" in js and "stat(`Solo en ${sv.donde}`" in js


# --- UI-8 -----------------------------------------------------------------------------------------------------------
def test_ui8_series_muestra_el_siguiente_numero_real(app_cliente):
    c = app_cliente
    eid = S._instalar(c, siguiente=7)
    aa = db.ahora().strftime("%y")
    anio = 2000 + int(aa)
    s = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]
    assert (s["siguiente_actual"], s["siguiente_texto"], s["con_anio"], s["anio_actual"]) == (7, f"007/A{aa}", True, anio)
    assert s["anio_confirmacion"] == anio and s["siguiente_numero"] == 7
    _expedir(c, eid, f"007/A{aa}")
    s = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]
    assert (s["siguiente_actual"], s["siguiente_texto"]) == (8, f"008/A{aa}")
    assert s["contadores"] == [{"anio": anio, "siguiente": 8, "siguiente_texto": f"008/A{aa}"}]
    c.ok(c.post(f"/api/series/{s['id']}/avanzar", {"anio": anio, "actual": 8, "siguiente": 10, "confirmo": True}))
    s = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]
    assert (s["siguiente_actual"], s["siguiente_texto"], s["siguiente_numero"]) == (10, f"010/A{aa}", 7)
    # Serie sin año: un único contador
    sp = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "P", "patron": "R-{n}", "siguiente_numero": 5}))
    c.ok(c.post(f"/api/series/{sp['id']}/confirmar"))
    c.ok(c.post(f"/api/series/{sp['id']}/avanzar", {"actual": 5, "siguiente": 9, "confirmo": True}))
    p = next(x for x in c.ok(c.get(f"/api/empresas/{eid}/series")) if x["id"] == sp["id"])
    assert (p["siguiente_texto"], p["con_anio"], p["contadores"]) == ("R-9", False, [{"anio": 0, "siguiente": 9, "siguiente_texto": "R-9"}])
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "s.siguiente_texto" in js and "Primer número de cada año" in js


# --- RES-3 / FIX-4 --------------------------------------------------------------------------------------------------
def test_res3_el_arranque_vuelve_a_anotar_los_avances_del_contador(app_cliente):
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    anio = 2000 + int(aa)
    sid = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]["id"]
    for n in (1, 2, 3):
        _expedir(c, eid, f"{n:03d}/A{aa}")
    assert S._enviar(eid)["aceptado"] == 3
    con = db.conectar()
    copia = copias.crear_copia(con)                                   # copia anterior al avance
    con.close()
    c.ok(c.post(f"/api/series/{sid}/avanzar", {"anio": anio, "actual": 4, "siguiente": 10, "confirmo": True}))

    # El diario se pierde (otro volumen, otro servidor) y el arranque lo rehace desde la base de datos
    shutil.rmtree(diario.directorio())
    for _ in range(2):                                                # dos arranques: se anota una sola vez
        with TestClient(crear_app()):
            pass
    anotados = _tipos_diario("serie_contador")
    assert [(d["serie_id"], d["anio"], d["siguiente"]) for d in anotados] == [(sid, anio, 10)]
    assert anotados[0]["serie"]["id"] == sid and anotados[0]["empresa_id"] == eid and anotados[0]["anterior"] == 4
    assert copias.reconciliar_diario(db.conectar()) == 0

    # Más tarde se pierde la base de datos y se restaura la copia anterior al avance: el avance se conserva
    a = ajustes()
    for suf in ("", "-wal", "-shm"):
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    rr = copias.restaurar(copia)
    assert rr["contadores_avanzados"] == 1 and not rr["entradas_descartadas"]
    con = db.conectar()
    assert db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=?", (sid, anio))["siguiente"] == 10
    _entrar(c)
    b = S._borrador(c, eid, f"004/A{aa}")
    assert "NUM_NO_CORRELATIVO" in S._codigos(b, "bloqueo") and not b["puede_emitir"]


# --- Reparación tras la verificación (G2V-1 … G2V-5) -----------------------------------------------------------------
TEXTO_POSTERIOR_SIM = ("Registro posterior al último registro de esta instalación: la cadena local está atrasada respecto "
                       "al simulador local (vea el aviso).")


def _sin_bifurcacion(cadena: dict) -> None:
    anteriores = [ant for _, _, ant in cadena.values() if ant]
    assert len(anteriores) == len(set(anteriores)), "dos registros con el mismo anterior: la cadena se bifurca"


def test_g2v1_reanudar_sigue_el_encadenamiento_con_registros_del_mismo_segundo(app_cliente, monkeypatch):
    """FechaHoraHusoGenRegistro solo tiene segundos: si los últimos registros perdidos se generan en el mismo segundo (el 003
    y la anulación del 001, p. ej. dos usuarios a la vez), el último es el que ningún otro tiene como RegistroAnterior, no el
    que la respuesta lista al final."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    anio = 2000 + int(aa)
    sid = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]["id"]
    f1 = _expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    _expedir(c, eid, f"002/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    real = db.ahora
    fijo = real().replace(microsecond=0)
    monkeypatch.setattr(db, "ahora", lambda: fijo)
    _expedir(c, eid, f"003/A{aa}")
    c.ok(c.post(f"/api/facturas/{f1}/anular", {"motivo": "prueba", "confirmo": True}))
    monkeypatch.setattr(db, "ahora", real)
    assert S._enviar(eid)["aceptado"] == 2
    antes = _cadena_simulador()
    h_anul, fh_anul, ant_anul = antes[f"001/A{aa}"]                   # el simulador guarda el último registro: la anulación
    h3, fh3, _ = antes[f"003/A{aa}"]
    assert fh_anul == fh3 and ant_anul == h3                           # mismo segundo; la anulación es la última

    _desastre(copia)
    _entrar(c)
    r = S._consultar(c, eid)
    ca = r["cadena_atrasada"]
    assert (ca["num_serie"], ca["tipo"], ca["huella"], ca["registros_posteriores"]) == (f"001/A{aa}", "anulacion", h_anul, 3)
    assert ca["ambigua"] is False and ca["candidatos"] == []
    assert "cadena_ambigua" not in r["codigos_aviso"] and "anulacion_sin_huella" not in r["codigos_aviso"]
    # G2V-2: el texto de cada fila nombra el servicio con su contracción («respecto al simulador local»)
    assert sorted(x["num_serie"] for x in r["solo_aeat"]) == [f"002/A{aa}", f"003/A{aa}"]
    for x in r["solo_aeat"]:
        assert x["nota"].endswith(" " + TEXTO_POSTERIOR_SIM), x["nota"]
    d1 = r["distintos"][0]
    assert d1["num_serie"] == f"001/A{aa}" and d1["posterior_a_la_cadena"] and d1["motivo"].endswith(" " + TEXTO_POSTERIOR_SIM)
    assert "respecto a el " not in json.dumps(r, ensure_ascii=False)

    rr = c.ok(c.post(f"/api/empresas/{eid}/consulta/reanudar-cadena", {"entorno": "simulador", "huella": h_anul, "confirmo": True}))
    assert rr["ancla"]["huella"] == h_anul and rr["ancla"]["num_serie"] == f"001/A{aa}"
    c.ok(c.post(f"/api/series/{sid}/avanzar", {"anio": anio, "actual": 2, "siguiente": 4, "confirmo": True}))
    fid4 = _expedir(c, eid, f"004/A{aa}")
    con = db.conectar()
    reg4 = db.fila(con, "SELECT * FROM registros WHERE factura_id=?", (fid4,))
    x4 = etree.fromstring(reg4["xml"].encode())
    assert x4.findtext("sf:Encadenamiento/sf:RegistroAnterior/sf:NumSerieFactura", namespaces=NSF) == f"001/A{aa}"
    assert reg4["huella_anterior"] == h_anul
    assert S._enviar(eid)["aceptado"] == 1
    despues = _cadena_simulador()
    _sin_bifurcacion(despues)
    assert despues[f"004/A{aa}"][2] == h_anul
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]
    assert S._consultar(c, eid)["cadena_atrasada"] is None


def test_g2v1_registro_perdido_del_mismo_segundo_que_la_copia(app_cliente, monkeypatch):
    """Un registro perdido generado en el mismo segundo que el último registro de la copia también es posterior."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    real = db.ahora
    fijo = real().replace(microsecond=0)
    monkeypatch.setattr(db, "ahora", lambda: fijo)
    _expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    _expedir(c, eid, f"002/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    monkeypatch.setattr(db, "ahora", real)
    cad = _cadena_simulador()
    h2, fh2, _ = cad[f"002/A{aa}"]
    assert fh2 == cad[f"001/A{aa}"][1]
    _desastre(copia)
    _entrar(c)
    r = S._consultar(c, eid)
    assert r["cadena_atrasada"] and (r["cadena_atrasada"]["num_serie"], r["cadena_atrasada"]["huella"]) == (f"002/A{aa}", h2)
    b = S._borrador(c, eid, f"002/A{aa}")
    assert "CADENA_ATRASADA" in S._codigos(b, "bloqueo")


def test_g2v1_ultimo_de_la_cadena_por_el_encadenamiento():
    from app.verifactu.respuesta_consulta import RegistroConsultado as R

    def reg(n, seg, ant):
        return R(id_emisor=S.NIF, num_serie=n, fecha_expedicion="01-01-2026", estado="Correcto", huella=n * 64,
                 fecha_hora_huso=f"2026-01-01T10:00:{seg:02d}+01:00", huella_anterior=ant)
    H = "H" * 64
    a, b, c_ = reg("A", 10, H), reg("B", 10, "A" * 64), reg("C", 10, "B" * 64)
    for orden in ([a, b, c_], [c_, b, a], [b, c_, a]):                 # mismo segundo: manda el encadenamiento
        ult, _ = consulta.ultimo_de_la_cadena(orden, H)
        assert ult is c_
    # Hueco (la consulta solo devuelve el último registro de cada factura): el generado más tarde
    a, c_ = reg("A", 10, H), reg("C", 12, "X" * 64)
    assert consulta.ultimo_de_la_cadena([c_, a], H)[0] is c_
    # Mismo segundo y hueco: el tramo que empieza en el último registro local es el primero
    a, c_ = reg("A", 10, H), reg("C", 10, "X" * 64)
    assert consulta.ultimo_de_la_cadena([c_, a], H)[0] is c_
    # Tres tramos en el mismo segundo: no puede saberse cuál es el último
    a, c_, d = reg("A", 10, H), reg("C", 10, "X" * 64), reg("D", 10, "Y" * 64)
    ult, cands = consulta.ultimo_de_la_cadena([a, c_, d], H)
    assert ult is None and {x.num_serie for x in cands} == {"C", "D"}
    # Sin fecha y hora del último: tampoco
    sin_fecha = R(id_emisor=S.NIF, num_serie="E", fecha_expedicion="01-01-2026", estado="Correcto", huella="E" * 64,
                  huella_anterior=H)
    assert consulta.ultimo_de_la_cadena([sin_fecha], H)[0] is None


def test_g2v1_sin_orden_seguro_no_se_reanuda(app_cliente):
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    _expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    for n in (2, 3, 4):
        _expedir(c, eid, f"{n:03d}/A{aa}")
    assert S._enviar(eid)["aceptado"] == 3
    _desastre(copia)
    _entrar(c)
    # La respuesta no permite ordenarlos: los tres del mismo segundo y el 003 y el 004 tras sendos huecos
    sim = S._sim()
    fh = _cadena_simulador()[f"004/A{aa}"][1]
    for ns_, ant in ((f"002/A{aa}", None), (f"003/A{aa}", "A" * 64), (f"004/A{aa}", "B" * 64)):
        x = etree.fromstring(sim.execute("SELECT xml FROM sim_registros_xml WHERE num_serie=?", (ns_,)).fetchone()[0].encode())
        x.find("sf:FechaHoraHusoGenRegistro", NSF).text = fh
        if ant:
            x.find("sf:Encadenamiento/sf:RegistroAnterior/sf:Huella", NSF).text = ant
        sim.execute("UPDATE sim_registros_xml SET xml=? WHERE num_serie=?", (etree.tostring(x, encoding="unicode"), ns_))
    r = S._consultar(c, eid)
    ca = r["cadena_atrasada"]
    assert ca["ambigua"] and {x["num_serie"] for x in ca["candidatos"]} == {f"003/A{aa}", f"004/A{aa}"}
    assert "cadena_ambigua" in r["codigos_aviso"] and "No se puede saber con seguridad" in r["avisos"][0]
    assert "CADENA_ATRASADA" in S._codigos(S._borrador(c, eid, f"002/A{aa}"), "bloqueo")   # sigue bloqueada
    for ns_ in (f"003/A{aa}", f"004/A{aa}"):
        res = c.post(f"/api/empresas/{eid}/consulta/reanudar-cadena",
                     {"entorno": "simulador", "huella": _cadena_simulador()[ns_][0], "confirmo": True})
        assert res.status_code == 409 and "No se puede saber con seguridad" in res.json()["detail"], res.text
    assert db.fila(db.conectar(), "SELECT estado FROM cadenas_aeat WHERE empresa_id=?", (eid,))["estado"] == "atrasada"
    assert not db.fila(db.conectar(), "SELECT 1 FROM actividad WHERE accion='cadena.reanudada_aeat'")
    # La pantalla no ofrece reanudar: muestra los candidatos y el aviso
    from conftest import RAIZ
    js = (RAIZ / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Candidatos a último" in js and "includes('anulacion_sin_huella')" in js and "const sinReanudar" in js


def test_g2v5_anulacion_que_la_aeat_muestra_con_la_huella_del_alta(app_cliente):
    """Si la AEAT devolviera una factura anulada con la huella y la fecha y hora de su alta (pendiente de contrastar en
    pruebas), una anulación perdida no se ve en la consulta: se avisa y no se reanuda la cadena."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    f1 = _expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    _expedir(c, eid, f"002/A{aa}")
    c.ok(c.post(f"/api/facturas/{f1}/anular", {"motivo": "prueba", "confirmo": True}))
    assert S._enviar(eid)["aceptado"] == 2
    _desastre(copia)
    _entrar(c)
    h1 = db.fila(db.conectar(), "SELECT huella FROM registros WHERE num_serie=?", (f"001/A{aa}",))["huella"]
    sim = S._sim()
    sim.execute("UPDATE sim_registros SET huella=? WHERE num_serie=?", (h1, f"001/A{aa}"))
    sim.execute("UPDATE sim_registros_xml SET xml=xml_alta WHERE num_serie=?", (f"001/A{aa}",))
    r = S._consultar(c, eid)
    assert "anulacion_sin_huella" in r["codigos_aviso"] and "cadena_atrasada" in r["codigos_aviso"]
    aviso = next(a for a in r["avisos"] if "con la huella de su registro de alta" in a)
    assert f"001/A{aa}" in aviso and aviso.startswith("El simulador local da por anulada(s)")
    h2 = r["cadena_atrasada"]["huella"]
    res = c.post(f"/api/empresas/{eid}/consulta/reanudar-cadena", {"entorno": "simulador", "huella": h2, "confirmo": True})
    assert res.status_code == 409 and "No se reanuda la cadena" in res.json()["detail"], res.text
    assert not db.fila(db.conectar(), "SELECT 1 FROM actividad WHERE accion='cadena.reanudada_aeat'")


def test_g2v3_la_documentacion_no_dice_que_la_consulta_lleve_el_indicador():
    from conftest import RAIZ as raiz
    viejo = "IndicadorRepresentante = \"S\"` cuando hay representante"
    for f in ("docs/arquitectura.md", "app/certificados.py"):
        assert viejo not in (raiz / f).read_text(encoding="utf-8"), f
    for f in ("app/remision.py", "app/consulta.py"):
        t = (raiz / f).read_text(encoding="utf-8")
        assert "→ IndicadorRepresentante=S en las consultas" not in t and "la consulta lleva\n    IndicadorRepresentante=S" not in t, f
