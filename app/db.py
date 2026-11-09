"""Acceso a SQLite (modo WAL) con migraciones versionadas y transacciones explícitas."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import BASE_DIR, ZONA_HORARIA, ajustes

MIGRACIONES_DIR = BASE_DIR / "migraciones"
TZ = ZoneInfo(ZONA_HORARIA)


def ahora() -> datetime:
    return datetime.now(TZ)


def ahora_iso() -> str:
    return ahora().replace(microsecond=0).isoformat()


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def conectar(ruta: Path | None = None, crear: bool = True) -> sqlite3.Connection:
    """crear=False abre solo una base existente (herramientas de copia: nunca crean una base vacía)."""
    ruta = ruta or ajustes().db_path
    if not crear and not ruta.exists():
        raise FileNotFoundError(f"No existe la base de datos {ruta}")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=FULL")
    return con


@contextmanager
def transaccion(con: sqlite3.Connection, inmediata: bool = True):
    """BEGIN IMMEDIATE toma el bloqueo de escritura al empezar: serializa numeración y encadenamiento."""
    con.execute("BEGIN IMMEDIATE" if inmediata else "BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def migrar(con: sqlite3.Connection) -> list[str]:
    con.execute("CREATE TABLE IF NOT EXISTS migraciones (nombre TEXT PRIMARY KEY, aplicada_en TEXT NOT NULL)")
    hechas = {r[0] for r in con.execute("SELECT nombre FROM migraciones")}
    aplicadas = []
    for f in sorted(MIGRACIONES_DIR.glob("*.sql")):
        if f.name in hechas:
            continue
        sql = f.read_text(encoding="utf-8")
        con.execute("BEGIN IMMEDIATE")
        try:
            for sentencia in _sentencias(sql):
                con.execute(sentencia)
            con.execute("INSERT INTO migraciones VALUES (?, ?)", (f.name, utc_iso()))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        aplicadas.append(f.name)
    return aplicadas


def _sentencias(sql: str) -> list[str]:
    """Divide el script respetando los bloques BEGIN ... END de los disparadores."""
    out, actual, en_trigger = [], [], False
    for linea in sql.splitlines():
        l = linea.strip()
        if not l or l.startswith("--"):
            continue
        actual.append(linea)
        if l.upper().startswith("CREATE TRIGGER"):
            en_trigger = True
        if en_trigger:
            if l.upper().endswith("END;"):
                out.append("\n".join(actual))
                actual, en_trigger = [], False
        elif l.endswith(";"):
            out.append("\n".join(actual))
            actual = []
    if actual:
        out.append("\n".join(actual))
    return out


def actividad(con: sqlite3.Connection, accion: str, *, usuario_id=None, empresa_id=None,
              objeto=None, objeto_id=None, detalle=None) -> None:
    con.execute(
        "INSERT INTO actividad (en, usuario_id, empresa_id, accion, objeto, objeto_id, detalle) VALUES (?,?,?,?,?,?,?)",
        (ahora_iso(), usuario_id, empresa_id, accion, objeto, objeto_id,
         json.dumps(detalle, ensure_ascii=False) if detalle is not None else None),
    )


def fila(con, sql, params=()) -> dict | None:
    r = con.execute(sql, params).fetchone()
    return dict(r) if r else None


def filas(con, sql, params=()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params).fetchall()]
