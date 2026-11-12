"""Series con ceros de relleno ({n:3}) y consulta a la AEAT (ConsultaFactuSistemaFacturacion) con conciliación segura.

Todas las pruebas son SIMULADAS o locales: el destino es app/simulador.py; nunca la AEAT (los interruptores de servidor
están desactivados en conftest.py y se comprueba que no se llega a abrir ninguna conexión).
Datos ficticios: NIF con código de provincia 00 (app.nif.completar_cif).
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import Cliente, configurar
from fastapi.testclient import TestClient
from lxml import etree

from app import consulta, copias, db, diario, importacion, remision, series, simulador
from app.config import ajustes
from app.nif import completar_cif
from app.verifactu import esquemas_xsd, respuesta_consulta, xmlgen
from app.verifactu.esquemas_xsd import NS_RCON

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import generar_pdfs_prueba as gen  # noqa: E402

NIF = completar_cif("B", "0012345")
EMPRESA = {"nif": NIF, "razon_social": "SERVICIOS FICTICIOS CERO S.L.", "domicilio": "C/ Ejemplo Sintético, 1 - 48000 Bilbao"}
PATRON = "{n:3}/A{aa}"


# --- S1: patrones con ceros de relleno ---------------------------------------------------------------------------------
def test_patron_con_ceros_formatea_y_lee_exactamente_ese_ancho():
    assert series.formatear(PATRON, 1, 2026) == "001/A26"
    assert series.formatear(PATRON, 42, 2026) == "042/A26"
    assert series.formatear(PATRON, 1000, 2026) == "1000/A26"      # el contador supera el ancho
    assert series.formatear("A{n:3}/{aa}", 57, 2026) == "A057/26"
    assert series.formatear("{nnn}-{aaaa}", 7, 2026) == "007-2026"  # {nnn} equivale a {n:3}
    assert series.formatear("{n}/{aa}", 166, 2026) == "166/26"      # {n}: sin relleno, como antes
    assert series.formatear("{aaaa}{n:4}", 13, 2026) == "20260013"
    lee = lambda p, t: (lambda r: (r["numero"], r["anio"]) if r["coincide"] else None)(series.probar(p, t))  # noqa: E731
    assert lee(PATRON, "042/A26") == (42, 2026)
    assert lee(PATRON, "999/A26") == (999, 2026)
    assert lee(PATRON, "1000/A26") == (1000, 2026)
    for otro_ancho in ("42/A26", "0042/A26", "0999/A26", "01000/A26"):   # solo ese ancho, o más cifras sin cero inicial
        assert lee(PATRON, otro_ancho) is None, otro_ancho
    assert lee("{nnn}/{aa}", "007/26") == (7, 2026) and lee("{nnn}/{aa}", "7/26") is None
    assert lee("{n}/{aa}", "0166/26") == (166, 2026)                # {n} sigue leyendo con o sin ceros
    # Lectura no estricta (solo para reconocer el mismo número al buscar duplicados)
    assert series.probar(PATRON, "42/A26", estricto=False)["numero"] == 42
    assert series.ancho(PATRON) == 3 and series.ancho("{n}/{aa}") == 0 and series.ancho("{nnnn}") == 4
    for malo in ("{n}{n}", "{aa}{n}{aa}", "SIN-NUMERO", "{n:0}", "{n:13}", "{N}", "{n3}", "{n:3}/{aaaa}/{aa}"):
        with pytest.raises(ValueError):
            series.validar_patron(malo)


@pytest.mark.parametrize("numero,patron,valor,anio", [
    ("042/A26", "{n:3}/A{aa}", 42, 2026), ("A057/26", "A{n:3}/{aa}", 57, 2026), ("166/26", "{n}/{aa}", 166, 2026),
    ("2026-0045", "{aaaa}-{n:4}", 45, 2026), ("F26/000123", "F{aa}/{n:6}", 123, 2026), ("PRB-1", "PRB-{n}", 1, 0),
    ("20260045", "{aaaa}{n:4}", 45, 2026), ("26/26", "{n}/{aa}", 26, 2026), ("FRA-2026-0012-B", "FRA-{aaaa}-{n:4}-B", 12, 2026),
    ("042/A25", "{n:3}/A{aa}", 42, 2025)])
def test_propuesta_de_patron_desde_el_numero_detectado(numero, patron, valor, anio):
    p = series.proponer_patron(numero, 2026)
    assert (p["patron"], p["numero"], p["anio"]) == (patron, valor, anio)
    assert series.formatear(p["patron"], p["numero"], p["anio"] or 2026) == numero   # la propuesta reproduce el número


def test_propuesta_sin_cifras_o_con_llaves():
    assert series.proponer_patron("SIN NUMERO", 2026) is None
    assert series.proponer_patron("{n}1", 2026) is None
    assert series.proponer_patron("", 2026) is None


def _instalar(c: Cliente, patron=PATRON, siguiente=1) -> int:
    d = c.ok(c.post("/api/instalar", {"email": "admin@ejemplo.test", "nombre": "Administración", "clave": "clave-segura-123",
                                      "empresa": EMPRESA}))
    c.csrf = d["csrf"]
    eid = d["empresas"][0]["id"]
    configurar(c, eid, patron=patron, siguiente=siguiente)
    return eid


def test_api_series_con_ceros(app_cliente):
    c = app_cliente
    eid = _instalar(c)
    s = c.ok(c.get(f"/api/empresas/{eid}/series"))[0]
    assert s["patron"] == PATRON and s["confirmada_en"]
    for malo in ("{n}{n}", "{n:13}", "{n3}/{aa}", "{aa}{n}{aa}"):
        r = c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "X", "patron": malo, "siguiente_numero": 1})
        assert r.status_code == 422, (malo, r.text)
    assert c.ok(c.post("/api/series/probar", {"patron": PATRON, "num_serie": "042/A26"})) == {"coincide": True, "numero": 42, "anio": 2026}
    assert c.ok(c.post("/api/series/probar", {"patron": PATRON, "num_serie": "42/A26"})) == {"coincide": False}
    assert c.post("/api/series/probar", {"patron": "{n:99}", "num_serie": "1"}).status_code == 422
    p = c.ok(c.post("/api/series/proponer", {"num_serie": "042/A26"}))
    assert p["patron"] == "{n:3}/A{aa}" and p["numero"] == 42
    assert c.ok(c.post("/api/series/proponer", {"num_serie": "sin cifras"})) == {"patron": None}


def test_duplicados_con_ceros_de_relleno(app_cliente):
    c = app_cliente
    eid = _instalar(c)
    con = db.conectar()
    assert importacion.clave_numero("042/A26") == importacion.clave_numero("42/A26") == importacion.clave_numero("0042/A26")
    _, igual, _ = importacion._equivalentes(con, eid, "042/A26", [PATRON])
    assert igual("42/A26") and igual("0042/A26") and igual("042/a26") and not igual("043/A26") and not igual("042/A25")
    # Patrón sin separador: los ceros quedan dentro del grupo de cifras; la serie (no estricta) los iguala
    _, igual, trozos = importacion._equivalentes(con, eid, "20260013", ["{aaaa}{n:4}"])
    assert igual("2026013") and igual("202613") and not igual("20260014")
    assert "13" in trozos


# --- S1 de principio a fin en el simulador ----------------------------------------------------------------------------
def _pdf(numero: str, total="1.028,50") -> bytes:
    return gen.dibujar(gen.demo(numero=numero, fecha=db.ahora().strftime("%d/%m/%Y"), total=total,
                                emisor=(EMPRESA["razon_social"], NIF)))


def _borrador(c: Cliente, eid: int, numero: str, nombre: str | None = None) -> dict:
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (nombre or f"{numero.replace('/', '-')}.pdf",
                                                                          _pdf(numero), "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    did = c.ok(r)[0]["documento_id"]
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "motivo": "prueba"}))
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    return c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))


def _emitir(c: Cliente, bid: int, clave: str) -> int:
    return c.ok(c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": clave, "confirmo": True}))["factura_id"]


def _enviar(eid: int) -> dict:
    con = db.conectar()
    try:
        return remision.procesar(con, eid, "simulador", forzar_flujo=True)
    finally:
        con.close()


def _codigos(b: dict, nivel: str | None = None) -> set[str]:
    return {i["codigo"] for i in b["incidencias"] if nivel is None or i["nivel"] == nivel}


@pytest.fixture()
def tres_emitidas(app_cliente):
    """Empresa con la serie {n:3}/A{aa} y tres facturas expedidas y aceptadas en el simulador: 001, 002 y 003."""
    c = app_cliente
    eid = _instalar(c)
    aa = db.ahora().strftime("%y")
    fids = []
    for n in (1, 2, 3):
        b = _borrador(c, eid, f"{n:03d}/A{aa}")
        assert b["puede_emitir"], b["incidencias"]
        assert b["serie_detectada"]["numero_detectado"] == n and b["serie_detectada"]["siguiente_numero"] == n
        fids.append(_emitir(c, b["id"], f"k{n}"))
    assert _enviar(eid)["aceptado"] == 3
    return c, eid, aa, fids


def test_serie_con_ceros_de_principio_a_fin(tres_emitidas):
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    fs = db.filas(con, "SELECT num_serie, numero FROM facturas ORDER BY id")
    assert [(f["num_serie"], f["numero"]) for f in fs] == [(f"001/A{aa}", 1), (f"002/A{aa}", 2), (f"003/A{aa}", 3)]
    regs = db.filas(con, "SELECT num_serie, estado_envio, xml FROM registros ORDER BY secuencia")
    assert [r["estado_envio"] for r in regs] == ["aceptado"] * 3
    assert f"<sf:NumSerieFactura>002/A{aa}</sf:NumSerieFactura>" in regs[1]["xml"]   # el número íntegro, con sus ceros
    assert db.fila(con, "SELECT siguiente FROM series_contadores")["siguiente"] == 4
    # Correlatividad: 005 no es el siguiente; «4/A26» no tiene el ancho de la serie (se indica cómo escribirlo)
    b = _borrador(c, eid, f"005/A{aa}")
    assert "NUM_NO_CORRELATIVO" in _codigos(b, "bloqueo") and not b["puede_emitir"]
    b = c.ok(c.patch(f"/api/borradores/{b['id']}", {"cambios": [{"campo": "num_serie", "valor": f"4/A{aa}", "motivo": "prueba"}]}))
    assert "SERIE_NO_CONFIRMADA" in _codigos(b, "bloqueo")
    aviso = next(i for i in b["incidencias"] if i["codigo"] == "SERIE_FORMATO")
    assert f"004/A{aa}" in aviso["mensaje"] and b["serie_propuesta"]["patron"] == "{n}/A{aa}"
    # Mismo número con otros ceros: duplicado de una factura expedida
    b = c.ok(c.patch(f"/api/borradores/{b['id']}", {"cambios": [{"campo": "num_serie", "valor": f"0002/A{aa}", "motivo": "prueba"}]}))
    assert "DUPLICADA" in _codigos(b, "bloqueo")
    # El siguiente correcto se expide
    b = c.ok(c.patch(f"/api/borradores/{b['id']}", {"cambios": [{"campo": "num_serie", "valor": f"004/A{aa}", "motivo": "prueba"}]}))
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    b = c.ok(c.post(f"/api/borradores/{b['id']}/confirmaciones", {"claves": claves}))
    assert b["puede_emitir"], b["incidencias"]
    assert c.ok(c.get(f"/api/facturas/{_emitir(c, b['id'], 'k4')}"))["factura"]["num_serie"] == f"004/A{aa}"


def test_borrador_sin_serie_propone_el_patron(app_cliente):
    c = app_cliente
    eid = _instalar(c, patron="PRB-{n}")
    aa = db.ahora().strftime("%y")
    b = _borrador(c, eid, f"042/A{aa}")
    assert b["serie_detectada"] is None and "SERIE_NO_CONFIRMADA" in _codigos(b, "bloqueo")
    assert b["serie_propuesta"]["patron"] == "{n:3}/A{aa}" and b["serie_propuesta"]["numero"] == 42


# --- S2/S3: consulta a la AEAT (simulador) ---------------------------------------------------------------------------
def _consultar(c, eid, entorno="simulador", periodo=None, num_serie=None, codigo=200):
    hoy = db.ahora()
    body = {"entorno": entorno, "ejercicio": str(hoy.year), "periodo": periodo or f"{hoy.month:02d}"}
    if num_serie:
        body["num_serie"] = num_serie
    r = c.post(f"/api/empresas/{eid}/consulta", body)
    assert r.status_code == codigo, r.text
    return r.json()


def _sim():
    con = sqlite3.connect(ajustes().data_dir / "simulador_aeat.sqlite3", isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def test_consulta_paginada_y_categorias(tres_emitidas, monkeypatch):
    c, eid, aa, fids = tres_emitidas
    monkeypatch.setattr(simulador, "TAM_PAGINA_CONSULTA", 2)
    r = _consultar(c, eid)
    assert r["paginas"] == 2 and r["registros_aeat"] == 3 and r["destino"] == "simulador-local"
    assert r["resumen"] == {"coinciden": 3, "solo_aeat": 0, "solo_local": 0, "distintos": 0, "conciliables": 0, "revisar": 0}
    assert [x["num_serie"] for x in r["coinciden"]] == [f"00{n}/A{aa}" for n in (1, 2, 3)]
    # Evidencias: petición y respuesta de cada página en el almacén; la segunda petición lleva la ClavePaginacion
    carpeta = ajustes().almacen_dir / r["evidencia"]
    ficheros = sorted(p.name for p in carpeta.iterdir())
    assert ficheros == ["pagina_001_peticion.xml", "pagina_001_respuesta.xml", "pagina_002_peticion.xml", "pagina_002_respuesta.xml"]
    pet2 = (carpeta / "pagina_002_peticion.xml").read_bytes()
    assert b"ClavePaginacion" in pet2 and f"002/A{aa}".encode() in pet2
    con = db.conectar()
    act = db.fila(con, "SELECT * FROM actividad WHERE accion='aeat.consulta' ORDER BY id DESC LIMIT 1")
    assert json.loads(act["detalle"])["paginas"] == 2

    # Discrepancias
    # (a) la AEAT tiene una factura que aquí no consta (p. ej. tras perder la base de datos y el diario)
    sim = _sim()
    fila = dict(sim.execute("SELECT * FROM sim_registros WHERE num_serie=?", (f"003/A{aa}",)).fetchone())
    sim.execute("INSERT INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                (NIF, f"009/A{aa}", fila["fecha"], "Correcta", "ABCDEF0123456789", None, None, "F" * 64, "alta",
                 fila["recibido_en"]))
    # (b) huella distinta y (c) estado distinto con la misma huella
    sim.execute("UPDATE sim_registros SET huella=? WHERE num_serie=?", ("0" * 64, f"002/A{aa}"))
    sim.execute("UPDATE sim_registros SET estado='AceptadaConErrores', codigo='2005' WHERE num_serie=?", (f"003/A{aa}",))
    # (d) una factura enviada sin respuesta (la AEAT la tiene: conciliable) y (e) otra aún no enviada (solo en local)
    b4 = _borrador(c, eid, f"004/A{aa}")
    _emitir(c, b4["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    assert _enviar(eid)["estado"] == "sin_confirmar"
    b5 = _borrador(c, eid, f"005/A{aa}")
    _emitir(c, b5["id"], "k5")

    r = _consultar(c, eid)
    res = r["resumen"]
    assert (res["coinciden"], res["solo_aeat"], res["solo_local"], res["distintos"], res["conciliables"]) == (1, 1, 1, 3, 1), r
    assert r["coinciden"][0]["num_serie"] == f"001/A{aa}"
    solo_aeat = r["solo_aeat"][0]
    assert solo_aeat["num_serie"] == f"009/A{aa}" and solo_aeat["revisar"]
    # el contador local (6) aún no ha llegado al 9; en el simulador el texto nunca dice «la AEAT» (G2/FIS-5)
    assert "Número ya usado en el simulador local" in solo_aeat["nota"]
    solo_local = r["solo_local"][0]
    assert solo_local["num_serie"] == f"005/A{aa}" and solo_local["estado_local"] == "pendiente" and not solo_local["revisar"]
    dist = {x["num_serie"]: x for x in r["distintos"]}
    assert "otra huella" in dist[f"002/A{aa}"]["motivo"] and not dist[f"002/A{aa}"]["conciliable"]
    assert "estado distinto" in dist[f"003/A{aa}"]["motivo"] and dist[f"003/A{aa}"]["estado_aeat"] == "AceptadoConErrores"
    d4 = dist[f"004/A{aa}"]
    assert d4["conciliable"] and d4["estado_local"] == "sin_confirmar" and d4["estado_propuesto"] == "aceptado"
    assert d4["huella_local"] == d4["huella_aeat"]
    # Consultar no cambia nada
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (d4["registro_id"],))["estado_envio"] == "sin_confirmar"
    # Filtro por número de factura
    r1 = _consultar(c, eid, num_serie=f"001/A{aa}")
    assert r1["registros_aeat"] == 1 and r1["resumen"]["coinciden"] == 1 and r1["resumen"]["solo_local"] == 0
    # Otro periodo: sin datos ni en la AEAT ni en local
    otro = "01" if db.ahora().month != 1 else "02"
    r2 = _consultar(c, eid, periodo=otro)
    assert r2["registros_aeat"] == 0 and sum(r2["resumen"][k] for k in ("coinciden", "solo_aeat", "solo_local", "distintos")) == 0


def test_conciliacion_segura(tres_emitidas):
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    b4 = _borrador(c, eid, f"004/A{aa}")
    _emitir(c, b4["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    assert _enviar(eid)["estado"] == "sin_confirmar"
    b5 = _borrador(c, eid, f"005/A{aa}")
    _emitir(c, b5["id"], "k5")
    r4 = db.fila(con, "SELECT * FROM registros WHERE num_serie=?", (f"004/A{aa}",))
    r5 = db.fila(con, "SELECT * FROM registros WHERE num_serie=?", (f"005/A{aa}",))
    r1 = db.fila(con, "SELECT * FROM registros WHERE num_serie=?", (f"001/A{aa}",))
    assert r4["estado_envio"] == "sin_confirmar" and r4["posible_entrega"] == 1
    url = f"/api/empresas/{eid}/consulta/conciliar"
    # Confirmación expresa y rol de administrador
    assert c.post(url, {"entorno": "simulador", "registro_ids": [r4["id"]]}).status_code == 422
    assert c.post(url, {"entorno": "simulador", "registro_ids": "x", "confirmo": True}).status_code == 422
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "emisor@ejemplo.test", "nombre": "E", "clave": "clave-emisor-123",
                                                  "rol": "emisor"}))
    em = Cliente(TestClient(c.tc.app))
    em.csrf = em.ok(em.post("/api/sesion", {"email": "emisor@ejemplo.test", "clave": "clave-emisor-123"}))["csrf"]
    em.ok(em.put("/api/yo/clave", {"clave_actual": "clave-emisor-123", "clave_nueva": "clave-emisor-123-definitiva"}))   # clave temporal del operador
    assert em.post(url, {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}).status_code == 404
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r4["id"],))["estado_envio"] == "sin_confirmar"
    # Conciliación: solo el registro sin confirmar que la AEAT tiene con la misma huella
    r = c.ok(c.post(url, {"entorno": "simulador", "registro_ids": [r4["id"], r5["id"], r1["id"]], "confirmo": True}))
    assert [x["registro_id"] for x in r["conciliados"]] == [r4["id"]] and r["conciliados"][0]["estado_nuevo"] == "aceptado"
    motivos = {x["registro_id"]: x["motivo"] for x in r["no_conciliados"]}
    assert "no tiene esta factura" in motivos[r5["id"]] and "estado definitivo" in motivos[r1["id"]]
    a4 = db.fila(con, "SELECT * FROM registros WHERE id=?", (r4["id"],))
    assert a4["estado_envio"] == "aceptado" and a4["siguiente_intento_en"] is None and "Conciliado" in a4["descripcion_error"]
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r5["id"],))["estado_envio"] == "pendiente"
    # Auditado y anotado en el diario de recuperación
    act = db.fila(con, "SELECT * FROM actividad WHERE accion='registro.conciliado_aeat'")
    det = json.loads(act["detalle"])
    assert act["objeto_id"] == r4["id"] and det["estado_anterior"] == "sin_confirmar" and det["estado_nuevo"] == "aceptado"
    ult = [e for e in diario.leer() if e["tipo"] == "estado_registro" and e["datos"]["huella"] == r4["huella"]][-1]
    assert ult["datos"]["estado_envio"] == "aceptado"
    assert copias.reconciliar_diario(con) == 0                       # nada pendiente de anotar
    # La cola no reenvía el registro conciliado: envía el siguiente
    n_envios = db.fila(con, "SELECT COUNT(*) n FROM envio_lineas WHERE registro_id=?", (r4["id"],))["n"]
    res = _enviar(eid)
    assert res["accion"] == "enviado" and res["aceptado"] == 1
    assert db.fila(con, "SELECT COUNT(*) n FROM envio_lineas WHERE registro_id=?", (r4["id"],))["n"] == n_envios
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r5["id"],))["estado_envio"] == "aceptado"
    # Ahora todo coincide
    assert _consultar(c, eid)["resumen"]["coinciden"] == 5
    # Volver a conciliar lo ya conciliado no cambia nada
    r = c.ok(c.post(url, {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}))
    assert r["conciliados"] == [] and "estado definitivo" in r["no_conciliados"][0]["motivo"]


def test_conciliacion_rechaza_huella_distinta_y_envio_en_curso(tres_emitidas):
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    b4 = _borrador(c, eid, f"004/A{aa}")
    _emitir(c, b4["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    _enviar(eid)
    r4 = db.fila(con, "SELECT * FROM registros WHERE num_serie=?", (f"004/A{aa}",))
    url = f"/api/empresas/{eid}/consulta/conciliar"
    # Con el remitente enviando (turno tomado por otro), no se concilia
    remision._tomar_turno(con, eid, "simulador", "otro-remitente")
    assert c.post(url, {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}).status_code == 409
    remision._soltar_turno(con, eid, "simulador", "otro-remitente")
    # La AEAT tiene la factura con otra huella: no se concilia
    _sim().execute("UPDATE sim_registros SET huella=? WHERE num_serie=?", ("1" * 64, f"004/A{aa}"))
    r = c.ok(c.post(url, {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}))
    assert r["conciliados"] == [] and "otra huella" in r["no_conciliados"][0]["motivo"]
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r4["id"],))["estado_envio"] == "sin_confirmar"
    # Registro de otra empresa o de otro entorno: no encontrado
    assert c.post(url, {"entorno": "pruebas", "registro_ids": [r4["id"]], "confirmo": True}).status_code == 403
    assert c.post(url, {"entorno": "simulador", "registro_ids": [999999], "confirmo": True}).status_code == 404


def test_conciliacion_sobrevive_a_una_restauracion(tres_emitidas):
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    b4 = _borrador(c, eid, f"004/A{aa}")
    _emitir(c, b4["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    _enviar(eid)
    r4 = db.fila(con, "SELECT * FROM registros WHERE num_serie=?", (f"004/A{aa}",))
    copia = copias.crear_copia(con)                                 # copia con el registro «sin confirmar»
    c.ok(c.post(f"/api/empresas/{eid}/consulta/conciliar", {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}))
    con.close()
    r = copias.restaurar(copia)
    assert r["estados_aplicados"] >= 1 and not r["entradas_descartadas"]
    con = db.conectar()
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (r4["id"],))["estado_envio"] == "aceptado"
    assert remision.procesar(con, eid, "simulador", forzar_flujo=True)["accion"] == "nada"   # no se reenvía


def test_consulta_de_factura_anulada_y_esquemas(tres_emitidas):
    """Petición (con paginación y representante) y respuesta simulada válidas contra los XSD oficiales, también con
    una factura anulada y una rectificativa."""
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    c.ok(c.post(f"/api/facturas/{fids[0]}/anular", {"motivo": "prueba", "confirmo": True}))
    assert _enviar(eid)["aceptado"] == 1
    r = _consultar(c, eid)
    assert r["resumen"]["coinciden"] == 3 and r["resumen"]["distintos"] == 0
    anulada = next(x for x in r["coinciden"] if x["num_serie"] == f"001/A{aa}")
    assert anulada["estado_aeat"] == "Anulado" and anulada["tipo_registro"] == "anulacion"
    # Petición con ClavePaginacion e IndicadorRepresentante
    el = xmlgen.mensaje_consulta(nif=NIF, nombre=EMPRESA["razon_social"], ejercicio="2026", periodo="11", num_serie="001/A26",
                                 clave_paginacion={"id_emisor": NIF, "num_serie": "001/A26", "fecha_expedicion": "11-11-2026"},
                                 representante=True)
    assert esquemas_xsd.validar(el, "ConsultaLR.xsd") == []
    # Respuesta del simulador (alta, anulación y rectificativa) válida contra RespuestaConsultaLR.xsd
    _registrar_rectificativa(con, eid, aa)
    hoy = db.ahora()
    sobre = xmlgen.sobre_soap(xmlgen.mensaje_consulta(nif=NIF, nombre=EMPRESA["razon_social"], ejercicio=str(hoy.year),
                                                      periodo=f"{hoy.month:02d}"))
    resp = simulador.consultar(sobre)
    raiz = etree.fromstring(resp).find(f".//{{{NS_RCON}}}RespuestaConsultaFactuSistemaFacturacion")
    assert esquemas_xsd.validar(raiz, "RespuestaConsultaLR.xsd") == []
    leida = respuesta_consulta.interpretar(resp)
    assert leida.errores_xsd == [] and leida.indicador_paginacion == "N" and leida.resultado == "ConDatos"
    por_num = {x.num_serie: x for x in leida.registros}
    assert por_num[f"001/A{aa}"].estado == "Anulado" and por_num[f"R001/A{aa}"].tipo_factura == "R1"
    assert por_num[f"002/A{aa}"].importe_total == "1028.50" and por_num[f"002/A{aa}"].id_peticion
    rect = raiz.find(f".//{{{NS_RCON}}}FacturasRectificadas")
    assert rect is not None and len(rect) == 1
    # Sin datos
    vacia = respuesta_consulta.interpretar(simulador.consultar(xmlgen.sobre_soap(xmlgen.mensaje_consulta(
        nif=NIF, nombre=EMPRESA["razon_social"], ejercicio="1999", periodo="01"))))
    assert vacia.resultado == "SinDatos" and vacia.registros == [] and vacia.errores_xsd == []


def _registrar_rectificativa(con, eid, aa):
    """Registra en el simulador, con un envío normal, un alta R1 que rectifica la 002 (sin pasar por la interfaz)."""
    from app.verifactu import huella as hmod
    from app import emision
    ant = db.fila(con, "SELECT * FROM registros ORDER BY secuencia DESC LIMIT 1")
    hoy = db.ahora()
    fecha = hoy.strftime("%d-%m-%Y")
    fh = hmod.fecha_hora_huso(hoy)
    num = f"R001/A{aa}"
    cad = hmod.cadena_alta(id_emisor=NIF, num_serie=num, fecha_expedicion=fecha, tipo_factura="R1", cuota_total="-21.00",
                           importe_total="-121.00", huella_anterior=ant["huella"], fecha_hora_huso=fh)
    alta = xmlgen.DatosAlta(
        id_emisor=NIF, nombre_razon_emisor=EMPRESA["razon_social"], num_serie=num, fecha_expedicion=fecha, tipo_factura="R1",
        descripcion_operacion="Rectificación ficticia", cuota_total="-21.00", importe_total="-121.00", fecha_hora_huso=fh,
        huella=hmod.sha256_mayus(cad), desglose=[{"calificacion": "S1", "tipo_impositivo": "21.00", "base": "-100.00", "cuota": "-21.00"}],
        encadenamiento={"id_emisor": NIF, "num_serie": ant["num_serie"], "fecha_expedicion": ant["fecha_expedicion"], "huella": ant["huella"]},
        sistema=emision.sistema_informatico(con, eid), destinatarios=[{"nombre": "CLIENTE FICTICIO S.A.", "nif": completar_cif("A", "0054321")}],
        tipo_rectificativa="I", facturas_rectificadas=[{"id_emisor": NIF, "num_serie": f"002/A{aa}", "fecha_expedicion": fecha}])
    msg = xmlgen.mensaje_remision({"nombre": EMPRESA["razon_social"], "nif": NIF}, [xmlgen.a_texto(xmlgen.registro_alta(alta))])
    assert esquemas_xsd.validar(msg) == []
    out = simulador.procesar(xmlgen.sobre_soap(msg))
    assert b"Correcto" in out


@pytest.mark.parametrize("modo,texto", [("caido", "No se pudo conectar"), ("sin_respuesta", "no respondió"),
                                        ("error_500", "HTTP 500"), ("rechazo", "4140"), ("fault_servidor", "9999")])
def test_consulta_modos_de_error_del_simulador(tres_emitidas, modo, texto):
    c, eid, aa, fids = tres_emitidas
    simulador.fijar_modo(modo, 1, servicio="consulta")
    assert simulador.modo_actual()["modo"] == "normal"            # la remisión no se ve afectada
    r = _consultar(c, eid, codigo=502)
    assert texto in r["detail"]
    assert _consultar(c, eid)["resumen"]["coinciden"] == 3        # el modo solo afecta al número de consultas indicado


def test_respuesta_fuera_de_esquema(tres_emitidas):
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    b4 = _borrador(c, eid, f"004/A{aa}")
    _emitir(c, b4["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    _enviar(eid)
    rid = db.fila(con, "SELECT id FROM registros WHERE num_serie=?", (f"004/A{aa}",))["id"]
    simulador.fijar_modo("respuesta_invalida", 2, servicio="consulta")
    r = _consultar(c, eid)
    assert r["avisos"] and "RespuestaConsultaLR.xsd" in r["avisos"][0] and r["resumen"]["conciliables"] == 1
    # Con una respuesta fuera de esquema no se concilia nada
    r = c.post(f"/api/empresas/{eid}/consulta/conciliar", {"entorno": "simulador", "registro_ids": [rid], "confirmo": True})
    assert r.status_code == 502 and "no cumple el esquema" in r.json()["detail"]
    assert db.fila(con, "SELECT estado_envio FROM registros WHERE id=?", (rid,))["estado_envio"] == "sin_confirmar"
    # El modo de la consulta del simulador se cambia por la API, para una empresa (G3, ISO-5: el modo es de cada empresa)
    nif = db.fila(con, "SELECT nif FROM empresas WHERE id=?", (eid,))["nif"]
    c.ok(c.post(f"/api/empresas/{eid}/simulador/modo", {"modo": "caido", "veces": 1, "servicio": "consulta"}))
    assert simulador.modo_actual(nif)["consulta"] == {"modo": "caido", "veces": 1}
    assert c.post(f"/api/empresas/{eid}/simulador/modo", {"modo": "rechazo_registro", "servicio": "consulta"}).status_code == 422


# --- Salvaguardas: nunca comunicaciones reales en las pruebas ----------------------------------------------------------
def test_consulta_sin_comunicaciones_reales_ni_acceso_ajeno(tres_emitidas, monkeypatch):
    c, eid, aa, fids = tres_emitidas
    import requests

    def prohibido(*a, **k):
        raise AssertionError("Intento de comunicación real con la AEAT")
    monkeypatch.setattr(requests, "post", prohibido)
    monkeypatch.setattr(remision, "_post", prohibido)
    assert not ajustes().permitir_pruebas_aeat and not ajustes().permitir_produccion
    for entorno in ("pruebas", "produccion"):
        r = _consultar(c, eid, entorno=entorno, codigo=403)
        assert "deshabilitada en el servidor" in r["detail"]
    # Aunque se saltaran las comprobaciones de la API y de los requisitos, el transporte de la remisión se niega
    con = db.conectar()
    emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    hoy = db.ahora()
    with pytest.raises(consulta.ErrorConsulta) as e:
        consulta.consultar_aeat(con, emp, 1, "pruebas", str(hoy.year), f"{hoy.month:02d}", comprobado=True)
    assert "deshabilitada en el servidor" in str(e.value)
    with pytest.raises(consulta.ErrorConsulta):
        consulta.consultar_aeat(con, emp, 1, "produccion", str(hoy.year), f"{hoy.month:02d}", comprobado=True)
    # Validación de la petición
    assert _consultar(c, eid, periodo="13", codigo=422)
    assert c.post(f"/api/empresas/{eid}/consulta", {"entorno": "otro", "ejercicio": "2026", "periodo": "01"}).status_code == 422
    # Un lector no consulta (comunicación con la AEAT); otro usuario sin acceso a la empresa: 404
    c.ok(c.post(f"/api/empresas/{eid}/miembros", {"email": "lector@ejemplo.test", "nombre": "L", "clave": "clave-lector-123",
                                                  "rol": "lector"}))
    lec = Cliente(TestClient(c.tc.app))
    lec.csrf = lec.ok(lec.post("/api/sesion", {"email": "lector@ejemplo.test", "clave": "clave-lector-123"}))["csrf"]
    lec.ok(lec.put("/api/yo/clave", {"clave_actual": "clave-lector-123", "clave_nueva": "clave-lector-123-definitiva"}))   # clave temporal del operador
    assert lec.post(f"/api/empresas/{eid}/consulta", {"entorno": "simulador", "ejercicio": "2026", "periodo": "01"}).status_code == 404
    anon = TestClient(c.tc.app)
    assert anon.post(f"/api/empresas/{eid}/consulta", json={"entorno": "simulador"}).status_code == 401
    assert c.tc.post(f"/api/empresas/{eid}/consulta", json={"entorno": "simulador"}).status_code == 403   # sin CSRF


# --- Revisión adversarial: incidencia de comunicación y duplicado (3000) tras restaurar ---------------------------------
def _incidencia_desde(con, eid):
    return db.fila(con, "SELECT incidencia_desde FROM control_flujo WHERE empresa_id=? AND entorno='simulador'", (eid,))["incidencia_desde"]


def _ultima_peticion(con) -> bytes:
    from app import almacen
    return almacen.leer(db.fila(con, "SELECT peticion_ruta FROM envios ORDER BY id DESC LIMIT 1")["peticion_ruta"])


def _reg(con, numero: str) -> dict:
    return db.fila(con, "SELECT * FROM registros WHERE num_serie=?", (numero,))


def test_conciliar_el_ultimo_pendiente_cierra_la_incidencia(tres_emitidas):
    """S-F1: conciliar el único registro sin respuesta deja la incidencia abierta y la factura siguiente, normal, se remitía
    con RemisionVoluntaria/Incidencia=S. Ahora la conciliación (y la restauración) la cierran si no queda nada pendiente."""
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    url = f"/api/empresas/{eid}/consulta/conciliar"
    # A) 004 sin respuesta: incidencia abierta. Es el único registro sin respuesta definitiva
    _emitir(c, _borrador(c, eid, f"004/A{aa}")["id"], "k4")
    simulador.fijar_modo("sin_respuesta", 1)
    assert _enviar(eid)["estado"] == "sin_confirmar"
    assert _incidencia_desde(con, eid)
    copia = copias.crear_copia(con)                                 # copia con la incidencia abierta y 004 sin confirmar
    r4 = _reg(con, f"004/A{aa}")
    r = c.ok(c.post(url, {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}))
    assert [x["registro_id"] for x in r["conciliados"]] == [r4["id"]] and r["incidencia_cerrada"] is True
    assert _incidencia_desde(con, eid) is None
    act = db.fila(con, "SELECT * FROM actividad WHERE accion='remision.incidencia_cerrada'")
    assert act and json.loads(act["detalle"])["motivo"] == "conciliacion_aeat"
    assert c.ok(c.get(f"/api/empresas/{eid}/remision?entorno=simulador"))["aviso_incidencia"] is None
    # La factura siguiente, expedida con normalidad, no se marca ni se remite con Incidencia=S
    _emitir(c, _borrador(c, eid, f"005/A{aa}")["id"], "k5")
    assert _reg(con, f"005/A{aa}")["incidencia"] == 0
    assert _enviar(eid)["aceptado"] == 1
    assert b"Incidencia>S<" not in _ultima_peticion(con)
    # B) Restaurar la copia (incidencia abierta, 004 sin confirmar): el diario trae 004 y 005 aceptados y la incidencia
    #    de la copia no se arrastra
    con.close()
    copias.restaurar(copia)
    con = db.conectar()
    assert _reg(con, f"004/A{aa}")["estado_envio"] == "aceptado" and _reg(con, f"005/A{aa}")["estado_envio"] == "aceptado"
    assert _incidencia_desde(con, eid) is None
    _emitir(c, _borrador(c, eid, f"006/A{aa}")["id"], "k6")
    assert _reg(con, f"006/A{aa}")["incidencia"] == 0
    assert _enviar(eid)["aceptado"] == 1 and b"Incidencia>S<" not in _ultima_peticion(con)
    # C) Si queda otro registro sin respuesta, la incidencia sigue abierta: el siguiente se remite con Incidencia=S (y el
    #    envío con respuesta la cierra)
    _emitir(c, _borrador(c, eid, f"007/A{aa}")["id"], "k7")
    simulador.fijar_modo("sin_respuesta", 1)
    assert _enviar(eid)["estado"] == "sin_confirmar"
    _emitir(c, _borrador(c, eid, f"008/A{aa}")["id"], "k8")
    assert _reg(con, f"008/A{aa}")["incidencia"] == 1
    r = c.ok(c.post(url, {"entorno": "simulador", "registro_ids": [_reg(con, f"007/A{aa}")["id"]], "confirmo": True}))
    assert len(r["conciliados"]) == 1 and r["incidencia_cerrada"] is False and _incidencia_desde(con, eid)
    assert _enviar(eid)["aceptado"] == 1 and b"Incidencia>S<" in _ultima_peticion(con)
    assert _incidencia_desde(con, eid) is None
    # La restauración no cierra una incidencia con registros aún sin respuesta
    assert remision.cerrar_incidencia_resuelta(con) == 0
    with db.transaccion(con):
        con.execute("UPDATE control_flujo SET incidencia_desde='2026-01-01T00:00:00+00:00' WHERE empresa_id=?", (eid,))
        con.execute("UPDATE registros SET estado_envio='sin_confirmar' WHERE num_serie=?", (f"008/A{aa}",))
        assert remision.cerrar_incidencia_resuelta(con) == 0
        con.execute("UPDATE registros SET estado_envio='aceptado' WHERE num_serie=?", (f"008/A{aa}",))
        assert remision.cerrar_incidencia_resuelta(con) == 1
    con.close()


def test_rechazo_3000_tras_restaurar_sin_diario_se_concilia(tres_emitidas):
    """S-F2: se pierden la base y el diario y se restaura una copia en la que 004 estaba «pendiente», aunque la AEAT ya lo
    tenía. La cola lo reenvía, la AEAT responde «duplicado» (3000) y, sin un envío previo sin respuesta, queda rechazado.
    La consulta devuelve ese mismo registro (misma huella): se ofrece y se aplica la conciliación."""
    import os
    c, eid, aa, fids = tres_emitidas
    con = db.conectar()
    _emitir(c, _borrador(c, eid, f"004/A{aa}")["id"], "k4")
    copia = copias.crear_copia(con)                                 # 004 «pendiente» en la copia
    assert _enviar(eid)["aceptado"] == 1                            # la AEAT (simulador) lo acepta después de la copia
    con.close()
    os.remove(diario.ruta())                                        # desastre: se pierden la base y el diario
    for p in ajustes().db_path.parent.glob(ajustes().db_path.name + "*"):
        os.remove(p)
    copias.restaurar(copia)
    con = db.conectar()
    assert copias.reconciliar_diario(con) > 0                       # como al arrancar: el diario nuevo recoge lo restaurado
    r4 = _reg(con, f"004/A{aa}")
    assert r4["estado_envio"] == "pendiente" and r4["posible_entrega"] == 0
    # El trabajador arranca antes de que nadie consulte: reenvía y la AEAT lo rechaza por duplicado
    res = remision.procesar(con, eid, "simulador", forzar_flujo=True)
    assert res["accion"] == "enviado" and res["rechazado"] == 1
    r4 = _reg(con, f"004/A{aa}")
    assert r4["estado_envio"] == "rechazado" and r4["codigo_error"] == "3000" and "puede conciliarse" in r4["descripcion_error"]
    # La consulta lo ofrece como conciliable (misma huella, la AEAT lo tiene «Correcto»)
    q = _consultar(c, eid)
    assert q["resumen"]["conciliables"] == 1 and q["resumen"]["revisar"] == 0
    fila = next(x for x in q["distintos"] if x["num_serie"] == f"004/A{aa}")
    assert fila["conciliable"] and fila["estado_propuesto"] == "aceptado" and "duplicado (3000)" in fila["motivo"]
    url = f"/api/empresas/{eid}/consulta/conciliar"
    body = {"entorno": "simulador", "registro_ids": [r4["id"]], "confirmo": True}
    # Solo el rechazo por duplicado: otro código de rechazo sigue siendo definitivo
    con.execute("UPDATE registros SET codigo_error='1100' WHERE id=?", (r4["id"],))
    r = c.ok(c.post(url, body))
    assert r["conciliados"] == [] and "estado definitivo" in r["no_conciliados"][0]["motivo"]
    assert _consultar(c, eid)["resumen"]["conciliables"] == 0
    con.execute("UPDATE registros SET codigo_error='3000' WHERE id=?", (r4["id"],))
    # Si la AEAT lo tiene con otra huella, no se concilia
    huella_sim = _sim().execute("SELECT huella FROM sim_registros WHERE num_serie=?", (f"004/A{aa}",)).fetchone()[0]
    _sim().execute("UPDATE sim_registros SET huella=? WHERE num_serie=?", ("2" * 64, f"004/A{aa}"))
    r = c.ok(c.post(url, body))
    assert r["conciliados"] == [] and "otra huella" in r["no_conciliados"][0]["motivo"]
    assert _reg(con, f"004/A{aa}")["estado_envio"] == "rechazado"
    _sim().execute("UPDATE sim_registros SET huella=? WHERE num_serie=?", (huella_sim, f"004/A{aa}"))
    # Conciliación: aceptado, con el motivo, en la actividad y en el diario
    r = c.ok(c.post(url, body))
    assert r["conciliados"] == [{"registro_id": r4["id"], "factura_id": r4["factura_id"], "num_serie": f"004/A{aa}",
                                 "estado_anterior": "rechazado", "estado_nuevo": "aceptado"}]
    a4 = _reg(con, f"004/A{aa}")
    assert a4["estado_envio"] == "aceptado" and a4["codigo_error"] is None
    assert "Conciliado" in a4["descripcion_error"] and "rechazado por duplicado (3000)" in a4["descripcion_error"]
    assert db.fila(con, "SELECT estado FROM facturas WHERE id=?", (r4["factura_id"],))["estado"] == "emitida"
    det = json.loads(db.fila(con, "SELECT detalle FROM actividad WHERE accion='registro.conciliado_aeat'")["detalle"])
    assert det["estado_anterior"] == "rechazado" and det["estado_nuevo"] == "aceptado"
    ult = [e for e in diario.leer() if e["tipo"] == "estado_registro" and e["datos"]["huella"] == r4["huella"]][-1]
    assert ult["datos"]["estado_envio"] == "aceptado"
    assert copias.reconciliar_diario(con) == 0
    q = _consultar(c, eid)
    assert q["resumen"]["coinciden"] == 4 and q["resumen"]["distintos"] == 0
    # Sobrevive a otra restauración de la misma copia (el diario conserva el estado conciliado) y no se reenvía
    con.close()
    copias.restaurar(copia)
    con = db.conectar()
    assert _reg(con, f"004/A{aa}")["estado_envio"] == "aceptado"
    assert remision.procesar(con, eid, "simulador", forzar_flujo=True)["accion"] == "nada"
    con.close()
