"""QR tributario.

URL del servicio de cotejo de la AEAT con los parámetros nif, numserie, fecha e importe,
codificados como componente de URL. Símbolo ISO/IEC 18004 con nivel de corrección M.
El tamaño físico y la colocación se aplican en app/pdf_final.py.
"""
from __future__ import annotations

from urllib.parse import quote

import segno

from ..config import URL_QR

LEYENDA_SUPERIOR = "QR tributario:"
LEYENDA_VERIFACTU = "VERI*FACTU"
LEYENDA_VERIFACTU_LARGA = "Factura verificable en la sede electrónica de la AEAT"


def url_cotejo(entorno: str, *, nif: str, num_serie: str, fecha_expedicion: str, importe_total: str) -> str:
    """fecha_expedicion en formato dd-mm-aaaa; importe_total con punto decimal (mismo valor que ImporteTotal)."""
    if entorno not in URL_QR:
        raise ValueError(f"Entorno sin QR tributario: {entorno}")
    base = URL_QR[entorno]
    q = "&".join(
        f"{k}={quote(v, safe='')}"
        for k, v in (("nif", nif), ("numserie", num_serie), ("fecha", fecha_expedicion), ("importe", importe_total))
    )
    return f"{base}?{q}"


def matriz(url: str):
    """Devuelve el objeto QR (segno) con corrección M forzada (sin subir de nivel)."""
    return segno.make_qr(url, error="m", boost_error=False, encoding="utf-8")
