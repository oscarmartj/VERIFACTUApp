"""Series y numeración. El identificador completo (p. ej. «166/26») se conserva íntegro como
NumSerieFactura; el patrón solo sirve para controlar la correlatividad y debe estar confirmado."""
from __future__ import annotations

import re

from . import db

RE_MARCADOR = re.compile(r"\{(n|aa|aaaa)\}")


def patron_a_regex(patron: str) -> re.Pattern:
    if "{n}" not in patron:
        raise ValueError("El patrón debe contener {n}")
    partes, pos = [], 0
    for m in RE_MARCADOR.finditer(patron):
        partes.append(re.escape(patron[pos:m.start()]))
        partes.append({"n": r"(?P<n>\d{1,12})", "aa": r"(?P<aa>\d{2})", "aaaa": r"(?P<aaaa>\d{4})"}[m.group(1)])
        pos = m.end()
    partes.append(re.escape(patron[pos:]))
    return re.compile("^" + "".join(partes) + "$")


def probar(patron: str, num_serie: str) -> dict:
    m = patron_a_regex(patron).match(num_serie.strip())
    if not m:
        return {"coincide": False}
    g = m.groupdict()
    anio = int(g["aaaa"]) if g.get("aaaa") else (2000 + int(g["aa"]) if g.get("aa") else 0)
    return {"coincide": True, "numero": int(g["n"]), "anio": anio}


def formatear(patron: str, numero: int, anio: int) -> str:
    return patron.replace("{n}", str(numero)).replace("{aaaa}", f"{anio:04d}").replace("{aa}", f"{anio % 100:02d}")


def siguiente(con, serie_id: int, anio: int) -> int:
    c = db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=?", (serie_id, anio))
    if c:
        return c["siguiente"]
    s = db.fila(con, "SELECT siguiente_numero, confirmada_en FROM series WHERE id=?", (serie_id,))
    # Primer uso de un año: el número confirmado vale para el año de la confirmación (o para series sin año);
    # en cualquier otro año la numeración empieza en 1.
    anio_conf = int(s["confirmada_en"][:4]) if s.get("confirmada_en") else None
    if anio == 0 or anio == anio_conf:
        return s["siguiente_numero"]
    return 1


def identificar(con, empresa_id: int, entorno: str, num_serie: str, anio_expedicion: int, tipo: str = "ordinaria") -> dict | None:
    """Devuelve la serie confirmada cuyo patrón encaja con el número, con el número detectado y el esperado."""
    for s in db.filas(con, """SELECT * FROM series WHERE empresa_id=? AND entorno=? AND activa=1 AND tipo=?
                             AND confirmada_en IS NOT NULL ORDER BY id""", (empresa_id, entorno, tipo)):
        r = probar(s["patron"], num_serie or "")
        if r["coincide"]:
            anio = r["anio"] or 0
            s["numero_detectado"] = r["numero"]
            s["anio_detectado"] = anio
            s["anio_contador"] = anio if ("{aa}" in s["patron"] or "{aaaa}" in s["patron"]) else 0
            s["siguiente_numero"] = siguiente(con, s["id"], s["anio_contador"])
            s["anio_correcto"] = (anio == 0) or (anio == anio_expedicion)
            return s
    return None


def consumir(con, serie: dict) -> None:
    """Avanza el contador (dentro de la transacción de expedición)."""
    n = serie["numero_detectado"] + 1
    con.execute("""INSERT INTO series_contadores (serie_id, anio, siguiente) VALUES (?,?,?)
                   ON CONFLICT(serie_id, anio) DO UPDATE SET siguiente=excluded.siguiente""",
                (serie["id"], serie["anio_contador"], n))
