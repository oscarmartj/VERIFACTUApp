"""Carril B — reparación tras la verificación independiente.

BV-1: en «Primeros pasos», guardar el domicilio después de responder las tres preguntas borraba las respuestas (el PUT de
la empresa anulaba siempre la confirmación del ámbito). Ahora solo se anula si cambia algo que decide si se puede emitir
o si el domicilio pasa a un código postal foral sin territorio foral (se vuelve a preguntar).

BV-2: el camino anterior (PUT + POST /ambito/confirmar) confirmaba el ámbito sin haber respondido nunca la pregunta de
Canarias, Ceuta o Melilla (columna con 0 por defecto). Ahora /ambito/confirmar exige las tres respuestas expresas."""
import pytest
from conftest import Cliente
from fastapi.testclient import TestClient
from test_16_c_certificados import sello_empresa
from test_16_u_multiusuario import instalar
from test_19_b_alta_guiada import crear, previa, usuario

from app import multiusuario
from app.nif import completar_cif

TRES_NO = {"territorio_foral": False, "sii": False, "canarias_ceuta_melilla": False}


@pytest.fixture(autouse=True)
def _limites(monkeypatch):
    monkeypatch.setattr(multiusuario.LIMITE_REGISTRO_IP, "maximo", 1000)
    multiusuario.reiniciar_limitadores()
    yield
    multiusuario.reiniciar_limitadores()


def _amb(e):
    return e["ambito"]


def test_bv1_guardar_el_domicilio_no_borra_las_tres_respuestas(app_cliente):
    op = app_cliente
    instalar(op)
    u = usuario(op, "dom@ejemplo.test")
    e = u.ok(crear(u, previa(u, sello_empresa())["previa"]))
    eid = e["id"]
    assert e["domicilio"] is None                       # alta por el certificado: sin domicilio todavía
    r = u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"declaro": True}))
    confirmado = r["ambito_confirmado_en"]
    assert confirmado and _amb(r)["puede_emitir"]
    # Primeros pasos: «Usar este domicilio» después de responder (el caso del verificador)
    r = u.ok(u.put(f"/api/empresas/{eid}", {"domicilio": "C/ Falsa 1, 28001 Madrid"}))
    assert r["ambito_confirmado_en"] == confirmado and _amb(r)["puede_emitir"] and r["declaracion_autorizacion_en"]
    # Ni la razón social, ni el origen de los PDF, ni volver a mandar las mismas respuestas
    r = u.ok(u.put(f"/api/empresas/{eid}", {"razon_social": "EMPRESA SELLO FICTICIA S.L.", "origen_pdf": "plantilla_ofimatica",
                                            "territorio": "comun", "sii": 0, "canarias_ceuta_melilla": 0}))
    assert r["ambito_confirmado_en"] == confirmado
    # Un domicilio con código postal foral (Bilbao) y territorio común: aviso y se vuelve a preguntar
    r = u.ok(u.put(f"/api/empresas/{eid}", {"domicilio": "C/ Ficticia 2, 48001 Bilbao"}))
    assert r["ambito_confirmado_en"] is None
    assert any(m.startswith("AVISO") and "48001" in m for m in _amb(r)["motivos"])
    r = u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"declaro": True}))
    confirmado = r["ambito_confirmado_en"]
    # Otro domicilio también foral: ya se respondió con ese aviso a la vista, no se vuelve a preguntar
    r = u.ok(u.put(f"/api/empresas/{eid}", {"domicilio": "C/ Ficticia 3, 48002 Bilbao"}))
    assert r["ambito_confirmado_en"] == confirmado
    # Cambiar una respuesta legal sí anula la confirmación (y con SII ya no se puede emitir)
    r = u.ok(u.put(f"/api/empresas/{eid}", {"sii": 1}))
    assert r["ambito_confirmado_en"] is None and not _amb(r)["puede_emitir"]
    for campo, valor in (("canarias_ceuta_melilla", 1), ("territorio", "navarra"), ("tipo_contribuyente", "otro"),
                         ("otras_exclusiones", "Caso especial")):
        u.ok(u.post(f"/api/empresas/{eid}/tres-preguntas", TRES_NO | {"declaro": True}))
        r = u.ok(u.put(f"/api/empresas/{eid}", {campo: valor}))
        assert r["ambito_confirmado_en"] is None, campo


def test_bv2_confirmar_el_ambito_exige_las_tres_respuestas_expresas(app_cliente):
    op = app_cliente
    instalar(op)
    u = usuario(op, "ant@ejemplo.test")
    e = u.ok(u.post("/api/empresas", {"nif": completar_cif("B", "0099111"), "razon_social": "X FICTICIA S.L.",
                                      "domicilio": "C/ X 1, 28001 Madrid"}))
    eid = e["id"]
    # Camino anterior: PUT sin la pregunta de Canarias y confirmar sin cuerpo → ya no confirma
    u.ok(u.put(f"/api/empresas/{eid}", {"territorio": "comun", "sii": 0}))
    assert u.post(f"/api/empresas/{eid}/ambito/confirmar").status_code == 422
    assert u.post(f"/api/empresas/{eid}/ambito/confirmar", {}).status_code == 422
    for falta in TRES_NO:
        cuerpo = {k: v for k, v in TRES_NO.items() if k != falta}
        assert u.post(f"/api/empresas/{eid}/ambito/confirmar", cuerpo).status_code == 422, falta
    for malo in (0, None, "no", "false"):
        assert u.post(f"/api/empresas/{eid}/ambito/confirmar", TRES_NO | {"canarias_ceuta_melilla": malo}).status_code == 422
    e = u.ok(u.get(f"/api/empresas/{eid}"))
    assert e["ambito_confirmado_en"] is None
    # Con las tres respuestas expresas confirma, y la respuesta «Sí» de Canarias bloquea como siempre
    r = u.ok(u.post(f"/api/empresas/{eid}/ambito/confirmar", TRES_NO | {"canarias_ceuta_melilla": True}))
    assert r["ambito_confirmado_en"] and r["canarias_ceuta_melilla"] == 1 and not _amb(r)["puede_emitir"]
    r = u.ok(u.post(f"/api/empresas/{eid}/ambito/confirmar", TRES_NO))
    assert r["ambito_confirmado_en"] and (r["territorio"], r["sii"], r["canarias_ceuta_melilla"]) == ("comun", 0, 0)
    assert _amb(r)["puede_emitir"]
    # Sin `nota` en el cuerpo no toca la nota guardada; la declaración sigue siendo aparte
    u.ok(u.put(f"/api/empresas/{eid}", {"otras_exclusiones": "Ninguna"}))
    r = u.ok(u.post(f"/api/empresas/{eid}/ambito/confirmar", TRES_NO))
    assert r["otras_exclusiones"] == "Ninguna" and r["declaracion_autorizacion_en"] is None and _amb(r)["puede_emitir"]
    # Solo el administrador, y 404 a quien no es miembro (antes de mirar el cuerpo)
    otro = usuario(op, "otro@ejemplo.test")
    assert otro.post(f"/api/empresas/{eid}/ambito/confirmar", TRES_NO).status_code == 404
    assert otro.post(f"/api/empresas/{eid}/ambito/confirmar").status_code == 404
    assert Cliente(TestClient(op.tc.app)).post(f"/api/empresas/{eid}/ambito/confirmar", TRES_NO).status_code in (401, 403)
