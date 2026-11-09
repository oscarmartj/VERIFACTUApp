"""Crea una instalación de demostración con datos SINTÉTICOS (sin red, sin AEAT).

Uso: VERIFACTU_DATA_DIR=./data-demo python scripts/sembrar_demo.py
Crea el usuario demo@ejemplo.test / demo-clave-segura, dos empresas ficticias, series del simulador y
documentos de ejemplo. La réplica de la factura de referencia queda marcada como no emitible fuera del simulador.
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("VERIFACTU_TRABAJADOR", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import crear_app  # noqa: E402

FIX = RAIZ / "tests" / "fixtures"


def main():
    with TestClient(crear_app()) as tc:
        st = tc.get("/api/estado").json()
        if st["instalada"]:
            print("Ya instalada; no se modifica.")
            return
        r = tc.post("/api/instalar", json={"email": "demo@ejemplo.test", "nombre": "Usuario demo", "clave": "demo-clave-segura",
                                           "empresa": {"nif": "B00000174", "razon_social": "INGENIERÍA DEMO NORTE S.L.",
                                                       "domicilio": "C/ Ejemplo Sintético, 1 - 48000 Bilbao"}})
        r.raise_for_status()
        h = {"X-CSRF-Token": r.json()["csrf"]}
        e1 = r.json()["empresas"][0]["id"]
        e2 = tc.post("/api/empresas", json={"nif": "B82991316", "razon_social": "INGENIERÍA FICTICIA Y ASOCIADOS S.L.U.",
                                            "domicilio": "C/ Inventada de Pruebas, 99 - 28000 Madrid"}, headers=h).json()["id"]
        for eid, patron, sig in ((e1, "PRB-{n}", 1), (e2, "{n}/{aa}", 166)):
            tc.put(f"/api/empresas/{eid}", json={"tipo_contribuyente": "sociedad_IS", "territorio": "comun", "sii": 0,
                                                 "canarias_ceuta_melilla": 0, "origen_pdf": "plantilla_ofimatica",
                                                 "origen_pdf_detalle": "Plantilla de procesador de textos (sintético)"}, headers=h)
            tc.post(f"/api/empresas/{eid}/ambito/confirmar", headers=h)
            tc.post(f"/api/empresas/{eid}/autorizacion-usuario", json={"relacion": "administrador", "declaro": True}, headers=h)
            s = tc.post(f"/api/empresas/{eid}/series", json={"entorno": "simulador", "codigo": "A", "patron": patron,
                                                             "siguiente_numero": sig, "descripcion": "Serie del simulador"}, headers=h).json()
            tc.post(f"/api/series/{s['id']}/confirmar", headers=h)
        sr = tc.post(f"/api/empresas/{e2}/series", json={"entorno": "simulador", "codigo": "R", "patron": "R{n}/{aa}",
                                                          "tipo": "rectificativa", "siguiente_numero": 1}, headers=h).json()
        tc.post(f"/api/series/{sr['id']}/confirmar", headers=h)
        for eid, nombres in ((e1, ["demo_proforma.pdf", "demo_recibida.pdf", "demo_descuadre.pdf", "demo_retencion.pdf"]),
                             (e2, ["replica_166-26.pdf", "replica_166-26_escaneada.pdf"])):
            files = [("archivos", (n, (FIX / n).read_bytes(), "application/pdf")) for n in nombres]
            res = tc.post(f"/api/empresas/{eid}/documentos", files=files, headers=h).json()
            for x in res:
                if x["nombre"].startswith("replica_166-26"):
                    tc.post(f"/api/documentos/{x['documento_id']}/bloqueo",
                            json={"motivo": "Factura de referencia: no volver a emitir ni remitir sus datos a la AEAT."}, headers=h)
        print("Demo creada. Usuario: demo@ejemplo.test / demo-clave-segura")


if __name__ == "__main__":
    main()
