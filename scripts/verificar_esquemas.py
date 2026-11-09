"""Contrasta los esquemas vendorizados con la publicación oficial de la AEAT (requiere acceso a la AEAT).

Uso: python scripts/verificar_esquemas.py
Si todos coinciden byte a byte, crea app/verifactu/esquemas/CONTRASTADO_AEAT (requisito previo a producción).
"""
import hashlib
import sys
from pathlib import Path

import requests

BASE = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tikeV1.0/cont/ws/"
FICHEROS = ["SuministroLR.xsd", "SuministroInformacion.xsd", "RespuestaSuministro.xsd", "ConsultaLR.xsd",
            "RespuestaConsultaLR.xsd", "SistemaFacturacion.wsdl", "errores.properties"]
DIR = Path(__file__).resolve().parents[1] / "app" / "verifactu" / "esquemas"


def main() -> int:
    ok = True
    informe = []
    for f in FICHEROS:
        local = hashlib.sha256((DIR / f).read_bytes()).hexdigest()
        try:
            r = requests.get(BASE + f, timeout=30)
            r.raise_for_status()
            remoto = hashlib.sha256(r.content).hexdigest()
        except Exception as e:  # noqa: BLE001
            informe.append(f"{f}: NO ACCESIBLE ({type(e).__name__})")
            ok = False
            continue
        igual = local == remoto
        ok &= igual
        informe.append(f"{f}: {'COINCIDE' if igual else 'DIFIERE'} local={local[:16]} oficial={remoto[:16]}")
    print("\n".join(informe))
    marca = DIR / "CONTRASTADO_AEAT"
    if ok:
        marca.write_text("\n".join(informe) + "\n", encoding="utf-8")
        print("Esquemas contrastados: se crea", marca)
        return 0
    print("Los esquemas NO están contrastados. Si difieren, sustitúyalos por los oficiales y vuelva a ejecutar las pruebas.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
