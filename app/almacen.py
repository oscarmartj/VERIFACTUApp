"""Almacenamiento privado de documentos (fuera de la raíz web; acceso solo vía API autorizada)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .config import ajustes


def _raiz() -> Path:
    r = ajustes().almacen_dir
    r.mkdir(parents=True, exist_ok=True)
    os.chmod(r, 0o700)
    return r


def ruta_absoluta(relativa: str) -> Path:
    raiz = _raiz().resolve()
    p = (raiz / relativa).resolve()
    if raiz not in p.parents:
        raise ValueError("Ruta fuera del almacén")
    return p


def guardar(relativa: str, datos: bytes, *, sobrescribir: bool = False) -> str:
    p = ruta_absoluta(relativa)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not sobrescribir:
        if p.read_bytes() == datos:
            return relativa
        raise FileExistsError(relativa)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return relativa


def leer(relativa: str) -> bytes:
    return ruta_absoluta(relativa).read_bytes()


def existe(relativa: str) -> bool:
    return ruta_absoluta(relativa).exists()


def sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()
