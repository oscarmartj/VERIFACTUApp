import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
FIX = RAIZ / "tests" / "fixtures"

# Nunca comunicaciones reales durante las pruebas
os.environ["VERIFACTU_PERMITIR_PRUEBAS_AEAT"] = "0"
os.environ["VERIFACTU_PERMITIR_PRODUCCION"] = "0"
os.environ["VERIFACTU_TRABAJADOR"] = "0"


@pytest.fixture()
def entorno_tmp(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setenv("VERIFACTU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIFACTU_DIARIO_DIR", str(tmp_path / "diario"))
    monkeypatch.setenv("VERIFACTU_CLAVE_MAESTRA_FICHERO", str(tmp_path / "secretos" / "clave.bin"))
    config.configurar(config.Ajustes())
    yield tmp_path
    config.configurar(config.Ajustes())


def pdf(nombre: str) -> bytes:
    return (FIX / nombre).read_bytes()


class Cliente:
    def __init__(self, tc):
        self.tc = tc
        self.csrf = None

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.tc.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.tc.post(url, json=json, headers=self._h(), **kw)

    def put(self, url, json=None):
        return self.tc.put(url, json=json, headers=self._h())

    def patch(self, url, json=None):
        return self.tc.patch(url, json=json, headers=self._h())

    def ok(self, r, codigo=200):
        assert r.status_code == codigo, f"{r.status_code}: {r.text[:800]}"
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r


@pytest.fixture()
def app_cliente(entorno_tmp):
    from fastapi.testclient import TestClient
    from app.main import crear_app
    app = crear_app()
    with TestClient(app) as tc:
        yield Cliente(tc)


EMPRESA_REPLICA = {"nif": "B82991316", "razon_social": "INGENIERÍA FICTICIA Y ASOCIADOS S.L.U.",
                   "domicilio": "C/ Inventada de Pruebas, 99 - 28000 Madrid"}
EMPRESA_DEMO = {"nif": "B00000174", "razon_social": "INGENIERÍA DEMO NORTE S.L.", "domicilio": "C/ Ejemplo Sintético, 1 - 48000 Bilbao"}


def preparar_empresa(c: Cliente, empresa=EMPRESA_REPLICA, email="admin@ejemplo.test", patron="{n}/{aa}", siguiente=166):
    r = c.post("/api/instalar", {"email": email, "nombre": "Administración", "clave": "clave-segura-123", "empresa": empresa})
    d = c.ok(r)
    c.csrf = d["csrf"]
    eid = d["empresas"][0]["id"]
    configurar(c, eid, patron, siguiente)
    return eid


def configurar(c: Cliente, eid: int, patron="{n}/{aa}", siguiente=166, entorno="simulador"):
    c.ok(c.put(f"/api/empresas/{eid}", {"tipo_contribuyente": "sociedad_IS", "territorio": "comun", "sii": 0,
                                        "canarias_ceuta_melilla": 0, "otras_exclusiones": "", "origen_pdf": "plantilla_ofimatica"}))
    c.ok(c.post(f"/api/empresas/{eid}/ambito/confirmar"))
    c.ok(c.post(f"/api/empresas/{eid}/autorizacion-usuario", {"relacion": "administrador", "declaro": True}))
    s = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": entorno, "codigo": "A", "patron": patron, "siguiente_numero": siguiente}))
    c.ok(c.post(f"/api/series/{s['id']}/confirmar"))


def subir(c: Cliente, eid: int, nombre: str):
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (nombre, pdf(nombre), "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    return c.ok(r)[0]


def borrador_listo(c: Cliente, eid: int, nombre="replica_166-26.pdf", confirmar_todo=True):
    d = subir(c, eid, nombre)
    did = d["documento_id"]
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "motivo": "Borrador generado con la plantilla"}))
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    if confirmar_todo:
        claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
        b = c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    return did, bid, b
