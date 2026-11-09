"""Diario de solo-anexado (JSONL) de facturas, registros, miembros y cambios de estado.

Sirve para la recuperación ante desastres: tras restaurar una copia de la base de datos, se reproducen las
entradas posteriores a la copia, de modo que no se pierde la continuidad de la cadena ni se vuelven a enviar
registros ya aceptados. Cada línea lleva su propia suma SHA-256 y se lee de forma estricta: una línea dañada
se descarta y se informa, nunca se reproduce. Junto al diario se guarda una copia espejo de los binarios que
no pueden regenerarse (PDF definitivos y documentos originales). Debe guardarse en un soporte distinto del de
la base de datos (variable VERIFACTU_DIARIO_DIR).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import db
from .config import ajustes


def directorio() -> Path:
    d = Path(os.environ.get("VERIFACTU_DIARIO_DIR", ajustes().data_dir.parent / "diario"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def ruta() -> Path:
    return directorio() / "diario.jsonl"


def ruta_espejo(relativa: str) -> Path:
    base = (directorio() / "almacen").resolve()
    p = (base / relativa).resolve()
    if base not in p.parents:
        raise ValueError("Ruta fuera del espejo")
    return p


def _suma(cuerpo: dict) -> str:
    return hashlib.sha256(json.dumps(cuerpo, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def anotar(tipo: str, datos: dict) -> None:
    cuerpo = {"en": db.utc_iso(), "tipo": tipo, "datos": datos}
    linea = json.dumps(dict(cuerpo, sha=_suma(cuerpo)), ensure_ascii=False, sort_keys=True)
    fd = os.open(ruta(), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        datos_b = (linea + "\n").encode("utf-8")
        tam = os.fstat(fd).st_size
        if tam and os.pread(fd, 1, tam - 1) != b"\n":
            datos_b = b"\n" + datos_b  # la línea anterior quedó cortada: se cierra para no corromper esta
        vista = memoryview(datos_b)
        while vista:
            n = os.write(fd, vista)
            vista = vista[n:]
        os.fsync(fd)
    finally:
        os.close(fd)


def guardar_binario(relativa: str, contenido: bytes) -> None:
    """Copia espejo (en el volumen del diario) de un binario no regenerable: PDF definitivo o documento original."""
    p = ruta_espejo(relativa)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.read_bytes() == contenido:
        return
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(contenido)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def anotar_factura(con, factura_id: int) -> None:
    """Anota la factura con el contexto que referencia (empresa, miembros, serie, instalación, documento,
    extracción y borrador), para poder reconstruirla aunque esos datos sean posteriores a la última copia."""
    f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (factura_id,))
    ctx = {
        "empresa": db.fila(con, "SELECT * FROM empresas WHERE id=?", (f["empresa_id"],)),
        "serie": db.fila(con, "SELECT * FROM series WHERE id=?", (f["serie_id"],)) if f.get("serie_id") else None,
        "instalacion": db.fila(con, "SELECT * FROM instalaciones WHERE empresa_id=?", (f["empresa_id"],)),
        "documento": db.fila(con, "SELECT * FROM documentos WHERE id=?", (f["documento_id"],)) if f.get("documento_id") else None,
        "borrador": db.fila(con, "SELECT * FROM borradores WHERE id=?", (f["borrador_id"],)),
    }
    ctx["miembros"] = db.filas(con, "SELECT * FROM miembros WHERE empresa_id=?", (f["empresa_id"],))
    b = ctx["borrador"]
    ctx["extraccion"] = db.fila(con, "SELECT * FROM extracciones WHERE id=?", (b["extraccion_id"],)) if b and b.get("extraccion_id") else None
    anotar("factura", dict(f, _contexto=ctx))


def anotar_registro(con, registro_id: int) -> None:
    anotar("registro", db.fila(con, "SELECT * FROM registros WHERE id=?", (registro_id,)))


def anotar_estado(con, registro_id: int) -> None:
    r = db.fila(con, """SELECT id, huella, estado_envio, codigo_error, descripcion_error, csv, intentos, posible_entrega,
                        incidencia, duplicado, siguiente_intento_en, ultimo_envio_id, actualizado_en FROM registros WHERE id=?""",
                (registro_id,))
    anotar("estado_registro", r)


LINEAS_INVALIDAS: list[int] = []


def leer() -> list[dict]:
    """Lee el diario de forma estricta (UTF-8 sin sustituciones y suma de cada línea). Las líneas ilegibles o
    alteradas se omiten y se anotan en LINEAS_INVALIDAS en lugar de impedir el arranque o la restauración."""
    p = ruta()
    LINEAS_INVALIDAS.clear()
    if not p.exists():
        return []
    out = []
    with open(p, "rb") as f:
        for n, bruta in enumerate(f, start=1):
            bruta = bruta.strip()
            if not bruta:
                continue
            try:
                e = json.loads(bruta.decode("utf-8"))
                suma = e.pop("sha", None)
                if suma is not None and suma != _suma(e):
                    raise ValueError("suma no coincide")
                out.append(e)
            except (UnicodeDecodeError, ValueError):
                LINEAS_INVALIDAS.append(n)
    return out
