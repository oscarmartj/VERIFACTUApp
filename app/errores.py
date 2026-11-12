"""Errores internos (respuesta 500): anotación agregada en la actividad y mensaje según quién puede verla.

- Una fila por plantilla de ruta, tipo de excepción, ámbito (plataforma o empresa) y minuto, con un contador («veces»):
  una avalancha de peticiones (también anónimas) no llena la actividad de la plataforma ni desplaza su auditoría. La
  actividad es de solo añadir (no se modifica una fila ya escrita), así que los errores se cuentan en memoria y la fila
  de un minuto se escribe solo cuando ese minuto ya ha terminado (con el siguiente error o al consultar la actividad) o
  al detener la aplicación. Consultar la actividad nunca escribe el minuto en curso: si lo hiciera, alternar un error y
  una consulta escribiría una fila por error. El detalle completo de cada error (la traza) queda siempre en el registro
  del servidor.
- Nunca el mensaje de la excepción ni la ruta con identificadores (pueden contener datos de las empresas): solo la
  plantilla («/api/empresas/{eid}/series») y el nombre del tipo.
- Si la ruta pertenece a una empresa (por {eid} o por el documento, borrador, factura, registro o serie de la ruta) y
  quien hace la petición es miembro de ella, se anota también en la actividad de esa empresa, que sus miembros ven.
- El mensaje solo remite a un registro de actividad que quien lo recibe puede consultar.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from . import db, seguridad
from .config import ajustes

MENSAJE_EMPRESA = "Error interno. Queda anotado en la actividad de la empresa; si se repite, avise al operador de la plataforma."
MENSAJE_OPERADOR = "Error interno. Consulte la actividad de la plataforma."
MENSAJE_GENERAL = "Error interno. Inténtelo de nuevo más tarde y, si se repite, avise al operador de la plataforma."

# Parámetro de la ruta → tabla cuyo objeto pertenece a una empresa
_TABLA_DE = {"did": "documentos", "bid": "borradores", "fid": "facturas", "rid": "registros", "sid": "series"}
_TIPO = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,79}")

# (base de datos, empresa_id o None, plantilla de ruta, tipo, minuto) → veces. La base de datos forma parte de la clave:
# cada fila se escribe en la base en la que ocurrió el error aunque la configuración cambie (pruebas, restauraciones).
_pendientes: dict[tuple[str, int | None, str, str, str], int] = {}
_cerrojo = threading.Lock()


def _minuto() -> str:
    return db.ahora_iso()[:16]


def plantilla_ruta(request) -> str:
    """Plantilla de la ruta («/api/empresas/{eid}/series»); sin ella, la ruta con los segmentos con cifras como {id}."""
    p = getattr(request.scope.get("route"), "path", None)
    if not isinstance(p, str):
        p = "/".join("{id}" if any(ch.isdigit() for ch in s) else s for s in request.url.path.split("/"))
    return p[:200]


def _empresa_de(con, request) -> int | None:
    pp = request.scope.get("path_params") or {}
    try:
        if "eid" in pp:
            return int(pp["eid"])
        for k, tabla in _TABLA_DE.items():
            if k in pp:
                f = db.fila(con, f"SELECT empresa_id FROM {tabla} WHERE id=?", (int(pp[k]),))
                return f["empresa_id"] if f else None
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _contar(base: str, empresa_id: int | None, ruta: str, tipo: str) -> None:
    clave = (base, empresa_id, ruta, tipo, _minuto())
    with _cerrojo:
        _pendientes[clave] = _pendientes.get(clave, 0) + 1


def volcar(todos: bool = False) -> int:
    """Escribe en la actividad las filas pendientes de los minutos ya terminados (antes de mostrar la actividad y con cada
    error) o, con `todos`, también las del minuto en curso: solo al detener la aplicación. Devuelve cuántas filas se han
    escrito. Nunca lanza."""
    minuto = _minuto()
    with _cerrojo:
        listos = {k: _pendientes.pop(k) for k in [k for k in _pendientes if todos or k[4] < minuto]}
    escritas = 0
    for base in {k[0] for k in listos}:
        try:
            con = db.conectar(Path(base), crear=False)
        except Exception:  # noqa: BLE001 — base ya inexistente (p. ej. la de otra prueba)
            continue
        try:
            with db.transaccion(con):
                for (b, empresa_id, ruta, tipo, m), veces in sorted(listos.items(), key=lambda x: (x[0][4], str(x[0]))):
                    if b == base:
                        db.actividad(con, "error.interno", empresa_id=empresa_id,
                                     detalle={"ruta": ruta, "tipo": tipo, "veces": veces, "minuto": m})
                        escritas += 1
        except Exception:  # noqa: BLE001 — la anotación nunca impide responder
            pass
        finally:
            con.close()
    return escritas


def anotar(request, exc: BaseException) -> str:
    """Cuenta el error (fila agregada por minuto) y devuelve el mensaje para quien hizo la petición. Nunca lanza."""
    try:
        ruta = plantilla_ruta(request)
        tipo = type(exc).__name__
        tipo = tipo if _TIPO.fullmatch(tipo) else "Exception"
        base = str(ajustes().db_path)
        sup = eid = None
        if request.cookies.get("sid"):
            con = db.conectar()
            try:
                s = seguridad.sesion_por_token(con, request.cookies.get("sid"))
                if s:
                    sup = bool((db.fila(con, "SELECT superadmin FROM usuarios WHERE id=?", (s["usuario_id"],)) or {}).get("superadmin"))
                    eid = _empresa_de(con, request)
                    if eid is not None and seguridad.rol_en_empresa(con, s["usuario_id"], eid) is None:
                        eid = None   # no es miembro: no se anota en una empresa ajena (ni se revela que exista)
            except Exception:  # noqa: BLE001 — la anotación de la plataforma no depende de la sesión
                sup = eid = None
            finally:
                con.close()
        _contar(base, None, ruta, tipo)
        if eid is not None:
            _contar(base, eid, ruta, tipo)
        volcar(todos=False)   # minutos ya cerrados
        if eid is not None:
            return MENSAJE_EMPRESA
        return MENSAJE_OPERADOR if sup else MENSAJE_GENERAL
    except Exception:  # noqa: BLE001
        return MENSAJE_GENERAL
