"""Huella (hash) reglamentaria de los registros de facturación VERI*FACTU.

Algoritmo (TipoHuella = "01"): SHA-256 sobre la cadena UTF-8 formada por pares
"Campo=valor" unidos con "&", en el orden fijado por la AEAT, con los valores sin
espacios iniciales/finales; resultado en hexadecimal en mayúsculas (64 caracteres).
En el primer registro de la cadena el valor de "Huella" (anterior) va vacío.

Se calcula sobre los mismos valores textuales que se escriben en el XML (mismo formato
de importes y fechas). El hash del archivo PDF NO sustituye a esta huella.
Contrastado con los ejemplos oficiales en tests/test_huella.py.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

TIPO_HUELLA = "01"


def _v(x: str | None) -> str:
    return (x or "").strip()


def cadena_alta(*, id_emisor: str, num_serie: str, fecha_expedicion: str, tipo_factura: str,
                cuota_total: str, importe_total: str, huella_anterior: str, fecha_hora_huso: str) -> str:
    pares = [
        ("IDEmisorFactura", id_emisor),
        ("NumSerieFactura", num_serie),
        ("FechaExpedicionFactura", fecha_expedicion),
        ("TipoFactura", tipo_factura),
        ("CuotaTotal", cuota_total),
        ("ImporteTotal", importe_total),
        ("Huella", huella_anterior),
        ("FechaHoraHusoGenRegistro", fecha_hora_huso),
    ]
    return "&".join(f"{k}={_v(v)}" for k, v in pares)


def cadena_anulacion(*, id_emisor: str, num_serie: str, fecha_expedicion: str,
                     huella_anterior: str, fecha_hora_huso: str) -> str:
    pares = [
        ("IDEmisorFacturaAnulada", id_emisor),
        ("NumSerieFacturaAnulada", num_serie),
        ("FechaExpedicionFacturaAnulada", fecha_expedicion),
        ("Huella", huella_anterior),
        ("FechaHoraHusoGenRegistro", fecha_hora_huso),
    ]
    return "&".join(f"{k}={_v(v)}" for k, v in pares)


def sha256_mayus(cadena: str) -> str:
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest().upper()


def huella_alta(**kw) -> str:
    return sha256_mayus(cadena_alta(**kw))


def huella_anulacion(**kw) -> str:
    return sha256_mayus(cadena_anulacion(**kw))


def fecha_hora_huso(dt: datetime) -> str:
    """Formato ISO 8601 con desfase horario, p. ej. 2024-01-01T19:20:30+01:00."""
    if dt.tzinfo is None:
        raise ValueError("La marca temporal debe incluir zona horaria")
    return dt.replace(microsecond=0).isoformat()


def fecha_aeat(iso: str) -> str:
    """'2026-09-25' -> '25-09-2026' (formato de fecha de los esquemas AEAT)."""
    a, m, d = iso.split("-")
    return f"{d}-{m}-{a}"
