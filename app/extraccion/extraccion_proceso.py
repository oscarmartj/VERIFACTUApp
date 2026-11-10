"""Extracción de campos en un proceso hijo con límites de CPU, memoria y tiempo.

Las reglas de extracción trabajan sobre el texto de un documento no fiable. Aunque su coste está acotado, se ejecutan
aisladas (como la lectura del texto, ver texto_proceso.py) para que un PDF hostil no pueda acaparar la CPU, la memoria
o el GIL del proceso del servidor ni bloquear las demás importaciones.

Uso interno: python -m <paquete>.extraccion_proceso <entrada.json>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

TIEMPO_MAX_S = 120   # CPU y tiempo real; una factura normal tarda menos de un segundo


def _paginas_a_json(paginas) -> list[dict]:
    return [{"numero": p.numero, "ancho": p.ancho, "alto": p.alto, "metodo": p.metodo, "conf_media": p.conf_media,
             "texto_oculto": getattr(p, "texto_oculto", 0), "palabras": [asdict(w) for w in p.palabras]} for p in paginas]


def _paginas_de_json(datos: list[dict]):
    from .texto import Pagina, Palabra
    return [Pagina(p["numero"], p["ancho"], p["alto"], [Palabra(**w) for w in p["palabras"]], p["metodo"], p["conf_media"],
                   p.get("texto_oculto", 0)) for p in datos]


def _extraer_local(paginas, nombre_archivo: str, nif_empresa: str | None) -> dict:
    from . import campos, texto
    ext = campos.Extractor(paginas, nombre_archivo, nif_empresa).extraer()
    # Líneas de cabecera para el recorte del logotipo
    lin = texto.lineas(paginas)
    ext["_lineas_cabecera"] = [{"texto": l.texto, "bbox": l.bbox()} for l in lin
                               if l.pagina == 1 and l.top < 0.2 * paginas[0].alto]
    return ext


def extraer(paginas, nombre_archivo: str, nif_empresa: str | None) -> dict:
    """Extracción completa (campos y líneas de cabecera) en un proceso hijo limitado. Si no termina dentro de los
    límites, el documento se rechaza con PDFNoValido: nunca se devuelve una extracción parcial."""
    from .inspeccion import PDFNoValido, ejecutar
    paquete = __package__ or "app.extraccion"
    raiz = Path(__file__).resolve().parents[len(paquete.split("."))]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(raiz)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    with tempfile.TemporaryDirectory() as td:
        ent = Path(td) / "e.json"
        ent.write_text(json.dumps({"paginas": _paginas_a_json(paginas), "nombre": nombre_archivo, "nif": nif_empresa},
                                  ensure_ascii=False), encoding="utf-8")
        try:
            r = ejecutar([sys.executable, "-m", f"{paquete}.extraccion_proceso", str(ent)], timeout=TIEMPO_MAX_S,
                         text=True, env=env)
        except subprocess.TimeoutExpired as e:
            raise PDFNoValido("El documento es demasiado complejo: la extracción de datos supera el tiempo máximo") from e
    try:
        res = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        res = {}
    if r.returncode != 0 or "extraccion" not in res:
        raise PDFNoValido("No se pudieron extraer los datos del documento dentro de los límites de seguridad")
    return res["extraccion"]


def main() -> int:
    entrada = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ext = _extraer_local(_paginas_de_json(entrada["paginas"]), entrada["nombre"], entrada["nif"])
    sys.stdout.write(json.dumps({"extraccion": ext}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
