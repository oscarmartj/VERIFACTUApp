"""Carril B — paquete 4: configuración reducida. «Tres preguntas sobre su empresa» con respuesta expresa (sin valor por
defecto) y la declaración de autorización en un solo paso; «Otras exclusiones» ya no bloquea; tipo de contribuyente
deducido del NIF; aviso de territorio por el código postal; producción resumida en una línea para la empresa; y las rutas
nuevas siguen respondiendo 404 a quien no es miembro."""
import json

import pytest
from conftest import EMPRESA_DEMO, EMPRESA_REPLICA, Cliente, borrador_listo, configurar
from fastapi.testclient import TestClient
from test_16_u_multiusuario import instalar, registrar

from app import borradores, db, multiusuario, produccion
from app.nif import completar_cif

TRES_NO = {"territorio_foral": False, "sii": False, "canarias_ceuta_melilla": False, "declaro": True}


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def usuario_con_empresa(op: Cliente, email="ana@ejemplo.test", empresa=EMPRESA_REPLICA) -> tuple[Cliente, int]:
    tok = op.ok(op.post("/api/plataforma/invitaciones", {"email": email}))["token"]
    u = Cliente(TestClient(op.tc.app))
    assert registrar(u, email, invitacion=tok).status_code == 200
    return u, u.ok(u.post("/api/empresas", empresa))["id"]


def test_evaluar_ambito_tres_preguntas_y_nota_que_no_bloquea():
    base = {"nif": EMPRESA_REPLICA["nif"], "territorio": "comun", "sii": 0, "canarias_ceuta_melilla": 0}
    r = borradores.evaluar_ambito(base)
    assert r["puede_emitir"] and r["tipo_contribuyente"] == "sociedad_IS" and r["tipo_deducido"]   # deducido del NIF
    # «Otras exclusiones» ya no bloquea: hoy escribir «Ninguna» impedía toda la emisión
    r = borradores.evaluar_ambito(base | {"otras_exclusiones": "Ninguna"})
    assert r["puede_emitir"] and "NOTA (no impide emitir): Ninguna" in r["motivos"]
    # Las tres preguntas siguen bloqueando, y sin respuesta tampoco se emite
    for cambio in ({"territorio": "foral"}, {"territorio": "pais_vasco"}, {"territorio": None}, {"sii": 1}, {"sii": None},
                   {"canarias_ceuta_melilla": 1}):
        assert not borradores.evaluar_ambito(base | cambio)["puede_emitir"], cambio
    # Aviso de territorio por el código postal del domicilio configurado (no bloquea)
    r = borradores.evaluar_ambito(base | {"domicilio": "C/ Ejemplo Sintético, 1 - 48000 Bilbao"})
    assert r["puede_emitir"] and any(m.startswith("AVISO") and "48000, Bizkaia" in m for m in r["motivos"])
    assert not any("territorio foral" in m for m in borradores.evaluar_ambito(base | {"domicilio": "C/ X, 1 - 28001 Madrid"})["motivos"]
                   if m.startswith("AVISO"))
    assert [borradores.provincia_foral(borradores.codigo_postal(f"C/ X, 7 - {cp} Ciudad")) for cp in ("01001", "20001", "31001", "48001", "39001")] \
        == ["Álava", "Gipuzkoa", "Navarra", "Bizkaia", None]
    # El domicilio que se propone a partir de una factura, sin el teléfono ni el correo que suelen ir detrás
    assert borradores.domicilio_propuesto("C/ Inventada de Pruebas, 99, 28000 Madrid, Tel. 900 000 000") == \
        "C/ Inventada de Pruebas, 99, 28000 Madrid"
    assert borradores.domicilio_propuesto("Pza. Mayor 2, 41000 Sevilla · info@ejemplo.test") == "Pza. Mayor 2, 41000 Sevilla"
    assert borradores.domicilio_propuesto("Avda. Telefónica 3, 28001 Madrid") == "Avda. Telefónica 3, 28001 Madrid"
    for nif, tipo in ((EMPRESA_REPLICA["nif"], "sociedad_IS"), ("12345678Z", "persona_fisica_IRPF"),
                      (completar_cif("E", "1234567"), "entidad_renta"), (completar_cif("Q", "2826000"), "otro"), ("", "otro")):
        assert borradores.tipo_contribuyente_de_nif(nif) == tipo, nif


def test_tres_preguntas_en_un_solo_paso(app_cliente):
    op = app_cliente
    instalar(op)
    u, eid = usuario_con_empresa(op)
    e = u.ok(u.get(f"/api/empresas/{eid}"))
    # Recién creada: nada respondido ni confirmado; el tipo de contribuyente ya está deducido del NIF
    assert e["territorio"] is None and e["sii"] is None and e["ambito_confirmado_en"] is None
    assert e["tipo_contribuyente"] == "sociedad_IS" and e["declaracion_autorizacion_en"] is None
    # Sin respuesta expresa a las tres (o sin la declaración), nada cambia
    for falta in ("territorio_foral", "sii", "canarias_ceuta_melilla", "declaro"):
        cuerpo = {k: v for k, v in TRES_NO.items() if k != falta}
        assert u.post(f"/api/empresas/{eid}/tres-preguntas", cuerpo).status_code == 422, falta
    assert u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"sii": 0}).status_code == 422      # sí o no, no 0/1
    assert u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"declaro": False}).status_code == 422
    assert u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"relacion": "jefe"}).status_code == 422
    assert u.ok(u.get(f"/api/empresas/{eid}"))["ambito_confirmado_en"] is None
    # Un paso: respuestas, evaluación, confirmación y declaración; la nota no bloquea
    e = u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"nota": "Ninguna"}))
    assert e["ambito_confirmado_en"] and e["ambito"]["puede_emitir"] and e["declaracion_autorizacion_en"]
    assert (e["territorio"], e["sii"], e["canarias_ceuta_melilla"], e["otras_exclusiones"]) == ("comun", 0, 0, "Ninguna")
    req = {x["clave"]: x for x in u.ok(u.get(f"/api/empresas/{eid}/produccion"))["requisitos"]}
    assert req["ambito"]["cumplido"] and req["autorizacion_usuario"]["cumplido"]
    acciones = [a["accion"] for a in u.ok(u.get(f"/api/empresas/{eid}/actividad"))]
    assert {"empresa.configuracion", "empresa.ambito_confirmado", "usuario.declaracion_autorizacion"} <= set(acciones)
    # Territorio foral: se guarda como tal y el ámbito queda bloqueado (confirmado, pero no soportado)
    e = u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"territorio_foral": True}))
    assert e["territorio"] == "foral" and not e["ambito"]["puede_emitir"]
    # SII o Canarias: también bloquean
    assert not u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"sii": True}))["ambito"]["puede_emitir"]
    assert not u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas",
                           TRES_NO | {"canarias_ceuta_melilla": True}))["ambito"]["puede_emitir"]
    # Un emisor no puede responderlas (solo administradores)
    inv = u.ok(u.post(f"/api/empresas/{eid}/invitaciones", {"email": "emi@ejemplo.test", "rol": "emisor"}))
    emi = Cliente(TestClient(op.tc.app))
    assert registrar(emi, "emi@ejemplo.test", invitacion=inv["token"]).status_code == 200
    assert emi.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO).status_code == 404


def test_las_tres_preguntas_permiten_emitir_y_el_borrador_lo_refleja(app_cliente):
    op = app_cliente
    instalar(op)
    u, eid = usuario_con_empresa(op)
    s = u.ok(u.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "A", "patron": "{n}/{aa}",
                                                     "siguiente_numero": 166}))
    u.ok(u.post(f"/api/series/{s['id']}/confirmar"))
    _, bid, b = borrador_listo(u, eid)
    assert "AMBITO_SIN_CONFIRMAR" in {i["codigo"] for i in b["incidencias"]} and not b["puede_emitir"]
    u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO))
    b = u.ok(u.get(f"/api/borradores/{bid}"))
    assert not {"AMBITO_SIN_CONFIRMAR", "AMBITO_NO_SOPORTADO"} & {i["codigo"] for i in b["incidencias"]}
    # Con una nota guardada por el camino anterior (PUT + confirmar), tampoco se bloquea
    u.ok(u.put(f"/api/empresas/{eid}", {"otras_exclusiones": "Ninguna"}))
    u.ok(u.post(f"/api/empresas/{eid}/ambito/confirmar", {k: False for k in ("territorio_foral", "sii", "canarias_ceuta_melilla")}))
    b = u.ok(u.get(f"/api/borradores/{bid}"))
    assert "AMBITO_NO_SOPORTADO" not in {i["codigo"] for i in b["incidencias"]}


def test_datos_y_compatibilidad_del_camino_anterior(app_cliente):
    """El PUT de la empresa y la declaración por separado siguen funcionando (configurar() de las pruebas los usa)."""
    op = app_cliente
    instalar(op)
    u, eid = usuario_con_empresa(op, empresa=EMPRESA_DEMO | {"nif": completar_cif("B", "0001234")})
    configurar(u, eid)
    e = u.ok(u.get(f"/api/empresas/{eid}"))
    assert e["ambito_confirmado_en"] and e["ambito"]["puede_emitir"] and e["declaracion_autorizacion_en"]
    assert any("48000, Bizkaia" in m for m in e["ambito"]["motivos"])        # aviso de territorio por el CP


def test_produccion_en_una_linea_para_la_empresa(app_cliente):
    op = app_cliente
    eid_op = instalar(op)
    u, eid = usuario_con_empresa(op)
    p = u.ok(u.get(f"/api/empresas/{eid}/produccion"))
    assert p["plataforma"] == {"lista": False, "pendientes": 4, "texto": produccion.TEXTO_PLATAFORMA_PENDIENTE["produccion"]}
    assert p["plataforma"]["texto"] == "La plataforma aún no está habilitada para producción (lo gestiona el operador)."
    assert p["plataforma_pruebas"]["pendientes"] == 2 and not p["plataforma_pruebas"]["lista"]
    de = {x["clave"]: x["de"] for x in p["requisitos"]}
    assert {k for k, v in de.items() if v == "plataforma"} == {"productor", "declaracion", "esquemas", "interruptor"}
    assert de["autorizacion_usuario"] == "usuario" and de["certificado"] == de["serie"] == "empresa"
    textos = " ".join(x["texto"] for x in p["requisitos"] + p["requisitos_pruebas"])
    assert "para producción" in textos and "para produccion" not in textos and "Certificado de produccion" not in textos
    # El operador confirma el productor: el resumen lo refleja
    op.ok(op.put("/api/productor", {"nombre": "OPERADOR FICTICIO S.L.", "nif": completar_cif("B", "0000999"), "domicilio": "x"}))
    assert u.ok(u.get(f"/api/empresas/{eid}/produccion"))["plataforma"]["pendientes"] == 3
    assert eid_op


def test_rutas_nuevas_de_la_empresa_responden_404_a_quien_no_es_miembro(app_cliente):
    op = app_cliente
    instalar(op)
    a, ea = usuario_con_empresa(op, "a@ejemplo.test")
    b, _ = usuario_con_empresa(op, "b@ejemplo.test", EMPRESA_DEMO | {"nif": completar_cif("B", "0001235")})
    con = db.conectar()
    antes = json.dumps(db.filas(con, "SELECT * FROM empresas WHERE id=?", (ea,)))
    for intruso in (b, op):                           # otro usuario y el operador (que no es miembro)
        assert intruso.get(f"/api/empresas/{ea}/datos-propuestos").status_code == 404
        assert intruso.post(f"/api/empresas/{ea}/tres-preguntas", TRES_NO).status_code == 404
        assert intruso.get(f"/api/empresas/{ea}/produccion").status_code == 404
        r = intruso.tc.post(f"/api/empresas/{ea}/certificados", files={"archivo": ("c.p12", b"x", "application/x-pkcs12")},
                            data={"clave": "x", "entorno": "todos"}, headers={"X-CSRF-Token": intruso.csrf})
        assert r.status_code == 404
    assert json.dumps(db.filas(con, "SELECT * FROM empresas WHERE id=?", (ea,))) == antes
    # Las del alta guiada no llevan identificadores (la empresa aún no existe) y exigen sesión
    anon = Cliente(TestClient(op.tc.app))
    assert anon.post("/api/alta/empresa", {"previa": "x" * 43}).status_code == 401
    assert anon.tc.post("/api/alta/certificados", files={"archivo": ("c.p12", b"x", "application/x-pkcs12")}).status_code == 401
    assert anon.tc.post("/api/alta/practica/documentos", files={"archivo": ("x.pdf", b"x", "application/pdf")}).status_code == 401
    assert a.ok(a.get(f"/api/empresas/{ea}/datos-propuestos"))["domicilio"] is None


def test_barrido_de_aislamiento_incluye_las_rutas_nuevas(app_cliente):
    """El barrido de aislamiento (test_16_u) recorre TODAS las rutas /api con identificadores: también las nuevas de este
    carril, que responden exactamente 404 a quien no es miembro."""
    import test_16_u_multiusuario as T
    esc = T.escenario_aislamiento(app_cliente)
    res = T._barrer(esc["b"], esc["ids"])
    for clave in (("POST", "/api/empresas/{eid}/tres-preguntas"), ("GET", "/api/empresas/{eid}/datos-propuestos")):
        assert res[clave] == 404, (clave, res.get(clave))
    assert not T.fallos_barrido(res)
