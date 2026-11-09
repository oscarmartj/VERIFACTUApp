"""Aritmética decimal de importes (nunca float)."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def q2(v: Decimal | str | int) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


_NUM_RE = re.compile(r"^[+-]?[\d.,\s]+$")


def parse_importe(texto: str | None) -> Decimal | None:
    """Interpreta importes en formato español o técnico.

    "1.028,50 €" -> 1028.50 ; "850,00" -> 850.00 ; "1028.50" -> 1028.50 ; "-12,3" -> -12.30
    Devuelve None si el texto no es un número inequívoco.
    """
    if texto is None:
        return None
    t = str(texto).strip().replace("€", "").replace("EUR", "").replace(" ", " ").strip()
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.endswith("-"):
        neg, t = True, t[:-1]
    t = t.replace(" ", "")
    if not t or not _NUM_RE.match(t):
        return None
    if t[0] in "+-":
        neg = neg or t[0] == "-"
        t = t[1:]
    if "," in t and "." in t:
        # El último separador es el decimal
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        partes = t.split(",")
        if len(partes) != 2:
            return None
        t = t.replace(",", ".")
    elif "." in t:
        partes = t.split(".")
        # "1.028" es ambiguo (miles o decimales). Solo aceptamos como miles si hay grupos de 3
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            if all(len(p) == 3 for p in partes[1:]):
                t = t.replace(".", "")
            else:
                return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    return -d if neg else d


def fmt_xml(v: Decimal | str) -> str:
    """Formato para XML y huella: punto decimal, siempre 2 decimales, sin separador de miles."""
    return f"{q2(v):.2f}"


def fmt_es(v: Decimal | str | None, moneda: bool = True) -> str:
    if v is None or v == "":
        return ""
    d = q2(v)
    s = f"{abs(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    s = ("-" if d < 0 else "") + s
    return s + (" €" if moneda else "")


def fmt_porcentaje(v: Decimal | str) -> str:
    d = Decimal(v)
    s = f"{d:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")
