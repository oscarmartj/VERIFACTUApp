"""Plataforma multiusuario: registro de cuentas, invitaciones, ajustes de la plataforma y gestión de contraseñas.

Papeles:
- Operador de la plataforma (usuarios con `superadmin`): productor del SIF, ajustes de la plataforma, cuentas e
  invitaciones de la plataforma y modo del simulador. NO ve los datos de ninguna empresa salvo que sea miembro de ella.
- Usuarios: cada uno crea sus empresas (queda como administrador) o entra en otras por invitación.
- Miembros de cada empresa: administrador, emisor o lector (tabla `miembros`).

Persistencia y recuperación: cada cambio de una cuenta o de un ajuste incrementa su versión en la misma transacción
y, tras confirmarse, se anota en el diario («usuario», «ajuste_plataforma»). Al arrancar se vuelven a anotar los que
falten y la restauración aplica la última versión anotada (una contraseña cambiada o una cuenta desactivada tras la
copia no vuelven a su estado anterior). Las invitaciones pendientes NO se restauran: se anulan al restaurar para que
un enlace ya usado después de la copia no vuelva a ser válido; el uso de cada invitación se anota en el diario
(«invitacion_usada»), de modo que una usada después de la copia figura como usada y no como anulada.

Invitaciones y permisos: una invitación solo vale mientras quien la creó siga pudiendo crearla (cuenta activa y
administrador de la empresa; operador, si es de la plataforma). Al degradar, quitar o desactivar a alguien se anulan
además sus invitaciones pendientes. Con el registro cerrado, una invitación de empresa creada por un usuario normal no
crea cuentas nuevas (solo la acepta una cuenta existente) salvo que el operador lo permita
(`invitaciones_empresa_crean_cuentas`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from . import db, nif as nifmod, seguridad

# --- Ajustes de la plataforma ------------------------------------------------------------------------------------------
# Registro CERRADO por defecto en una instalación nueva; y, con el registro cerrado, las invitaciones de los
# administradores de empresas (usuarios normales) no crean cuentas nuevas salvo que el operador lo permita.
AJUSTES_DEFECTO = {"registro_abierto": "0", "invitaciones_empresa_crean_cuentas": "0"}


def _tiene_tabla(con, nombre: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nombre,)).fetchone())


def ajuste(con, clave: str) -> str:
    if not _tiene_tabla(con, "plataforma"):
        return AJUSTES_DEFECTO.get(clave, "")
    r = db.fila(con, "SELECT valor FROM plataforma WHERE clave=?", (clave,))
    return r["valor"] if r else AJUSTES_DEFECTO.get(clave, "")


def registro_abierto(con) -> bool:
    return ajuste(con, "registro_abierto") == "1"


def invitaciones_empresa_crean_cuentas(con) -> bool:
    return ajuste(con, "invitaciones_empresa_crean_cuentas") == "1"


def es_operador_activo(con, usuario_id) -> bool:
    u = db.fila(con, "SELECT activo, superadmin FROM usuarios WHERE id=?", (usuario_id,))
    return bool(u and u["activo"] and u["superadmin"])


def fijar_ajuste(con, clave: str, valor: str, usuario_id: int | None) -> bool:
    """Dentro de una transacción. Devuelve True si el valor cambia (y entonces incrementa su versión)."""
    if clave not in AJUSTES_DEFECTO:
        raise ValueError("Ajuste desconocido")
    actual = db.fila(con, "SELECT valor FROM plataforma WHERE clave=?", (clave,))
    if actual and actual["valor"] == valor:
        return False
    con.execute("""INSERT INTO plataforma (clave, valor, version, actualizado_en, actualizado_por) VALUES (?,?,1,?,?)
                   ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, version=plataforma.version+1,
                   actualizado_en=excluded.actualizado_en, actualizado_por=excluded.actualizado_por""",
                (clave, valor, db.utc_iso(), usuario_id))
    db.actividad(con, "plataforma.ajuste", usuario_id=usuario_id, detalle={"clave": clave, "valor": valor})
    return True


# --- Limitadores en memoria (como el bloqueo de inicio de sesión, no sobreviven a un reinicio) -------------------------
class Limitador:
    """Ventana deslizante: como mucho `maximo` eventos por clave en `ventana_s` segundos."""

    def __init__(self, maximo: int, ventana_s: float = 3600.0, reloj=time.monotonic):
        self.maximo, self.ventana, self._reloj = maximo, ventana_s, reloj
        self._eventos: dict[str, deque] = {}
        self._cerrojo = threading.Lock()
        self._llamadas = 0

    def _purgar(self, clave: str, ahora: float) -> deque:
        q = self._eventos.setdefault(clave, deque())
        while q and q[0] <= ahora - self.ventana:
            q.popleft()
        return q

    def disponible(self, clave: str = "*", maximo: int | None = None) -> bool:
        with self._cerrojo:
            return len(self._purgar(clave, self._reloj())) < (self.maximo if maximo is None else maximo)

    def consumir(self, clave: str = "*", maximo: int | None = None) -> bool:
        """Anota un evento si cabe en la ventana. False si se ha superado el límite (`maximo`, si se indica, sustituye
        al del limitador)."""
        ahora = self._reloj()
        with self._cerrojo:
            self._llamadas += 1
            if self._llamadas % 1000 == 0:   # memoria acotada: se olvidan las claves sin eventos recientes
                for k in [k for k, q in self._eventos.items() if not q or q[-1] <= ahora - self.ventana]:
                    del self._eventos[k]
            q = self._purgar(clave, ahora)
            if len(q) >= (self.maximo if maximo is None else maximo):
                return False
            q.append(ahora)
            return True

    def reiniciar(self) -> None:
        with self._cerrojo:
            self._eventos.clear()


def _entero_env(nombre: str, defecto: int) -> int:
    try:
        return max(1, int(os.environ.get(nombre, defecto)))
    except ValueError:
        return defecto


LIMITE_REGISTRO_IP = Limitador(_entero_env("VERIFACTU_REGISTRO_MAX_IP", 5))            # intentos por IP y hora
LIMITE_REGISTRO_GLOBAL = Limitador(_entero_env("VERIFACTU_REGISTRO_MAX_GLOBAL", 30))   # cuentas nuevas por hora
LIMITE_REGISTRO_INTENTOS = Limitador(10 * LIMITE_REGISTRO_GLOBAL.maximo)               # intentos totales por hora
LIMITE_CONSULTA_INVITACION = Limitador(30)                                             # consultas de enlace por IP y hora
# Con una invitación vigente, el registro y la consulta del enlace no cuentan en los límites por IP normales (una oficina
# que da de alta a su personal comparte IP) ni en el de intentos de toda la plataforma, sino en los de esa invitación, en
# los de quien la creó (usuario y empresa) y en un techo por IP más alto. Así nadie agota con sus propias invitaciones un
# límite común a todos. Los enlaces no válidos (adivinados, caducados, usados) cuentan por IP como siempre.
LIMITE_POR_INVITACION = Limitador(10)                                                  # usos de cada invitación por hora
LIMITE_INVITACION_IP = Limitador(_entero_env("VERIFACTU_REGISTRO_MAX_IP_INVITACION", 10 * LIMITE_REGISTRO_IP.maximo))
LIMITE_INTENTOS_INVITADOR = Limitador(_entero_env("VERIFACTU_REGISTRO_MAX_POR_INVITADOR", 50))   # intentos por quien invita
LIMITE_ALTAS_INVITADOR = Limitador(_entero_env("VERIFACTU_ALTAS_MAX_POR_INVITADOR", 10))          # cuentas por quien invita


def reiniciar_limitadores() -> None:
    for x in (LIMITE_REGISTRO_IP, LIMITE_REGISTRO_GLOBAL, LIMITE_REGISTRO_INTENTOS, LIMITE_CONSULTA_INVITACION,
              LIMITE_POR_INVITACION, LIMITE_INVITACION_IP, LIMITE_INTENTOS_INVITADOR, LIMITE_ALTAS_INVITADOR):
        x.reiniciar()


def _consumir_todos(eventos: list[tuple[Limitador, str, int | None]]) -> bool:
    """Anota un evento en cada (limitador, clave, máximo o None para el del limitador) solo si caben todos: superar uno
    no gasta los demás."""
    if not all(l.disponible(k, m) for l, k, m in eventos):
        return False
    return all([l.consumir(k, m) for l, k, m in eventos])


def _claves_invitador(con, inv: dict) -> list[str]:
    """Claves de quien creó la invitación (usuario y empresa). El operador de la plataforma no tiene límite propio."""
    if es_operador_activo(con, inv["creada_por"]):
        return []
    return [f"usuario:{inv['creada_por']}"] + ([f"empresa:{inv['empresa_id']}"] if inv["empresa_id"] is not None else [])


def consumir_con_invitacion(con, inv: dict, uso: str, ip: str) -> bool:
    """Petición con una invitación vigente (`uso`: «registro» o «consulta»): cuenta en el límite de la invitación, en el
    techo por IP del tráfico con invitación y, en el registro, en el de quien la creó. Nunca en los límites comunes."""
    eventos = [(LIMITE_POR_INVITACION, f"{uso}:{inv['id']}", None), (LIMITE_INVITACION_IP, f"{uso}:{ip}", None)]
    if uso == "registro":
        eventos += [(LIMITE_INTENTOS_INVITADOR, k, None) for k in _claves_invitador(con, inv)]
    return _consumir_todos(eventos)


def _altas_con_invitacion(con, inv: dict, ip: str) -> list[tuple[Limitador, str, int]]:
    """Cuentas por hora creadas con invitaciones de los usuarios: por quien las creó (usuario y empresa;
    `VERIFACTU_ALTAS_MAX_POR_INVITADOR`) y por dirección IP. Ninguno pasa de la mitad del límite global de altas: ni un
    usuario con sus invitaciones ni un mismo cliente con las de varios usuarios agotan las de toda la plataforma (una
    oficina de 10 personas sí se da de alta desde una IP). Las invitaciones del operador no tienen estos límites."""
    claves = _claves_invitador(con, inv)
    if not claves:
        return []
    mitad = max(1, LIMITE_REGISTRO_GLOBAL.maximo // 2)
    return ([(LIMITE_ALTAS_INVITADOR, k, min(LIMITE_ALTAS_INVITADOR.maximo, mitad)) for k in claves]
            + [(LIMITE_ALTAS_INVITADOR, f"ip:{ip}", mitad)])


def altas_con_invitacion_disponibles(con, inv: dict | None, ip: str) -> bool:
    return not inv or all(l.disponible(k, m) for l, k, m in _altas_con_invitacion(con, inv, ip))


def anotar_alta(con, inv: dict | None, ip: str) -> None:
    """Tras crear una cuenta: cuenta en el límite global de altas y, con invitación, en los de quien la creó y la IP."""
    LIMITE_REGISTRO_GLOBAL.consumir()
    if inv:
        _consumir_todos(_altas_con_invitacion(con, inv, ip))


# --- Cupos por usuario y por empresa (en la base de datos: comunes a todas las instancias y persistentes) ---------------
# No se aplican al operador de la plataforma.
CUPOS = {
    "invitaciones_pendientes_empresa": _entero_env("VERIFACTU_INVITACIONES_PENDIENTES_MAX", 50),
    "invitaciones_dia_usuario": _entero_env("VERIFACTU_INVITACIONES_DIA_MAX", 100),
    "empresas_dia_usuario": _entero_env("VERIFACTU_EMPRESAS_DIA_MAX", 20),
}


class CupoSuperado(Exception):
    pass


def comprobar_cupo_empresas(con, usuario_id: int) -> None:
    """Altas de empresas por usuario en las últimas 24 h (evita la ocupación masiva de NIF y las empresas «tapadera»)."""
    if es_operador_activo(con, usuario_id):
        return
    desde = (db.ahora() - timedelta(days=1)).replace(microsecond=0).isoformat()
    n = db.fila(con, "SELECT COUNT(*) n FROM actividad WHERE accion='empresa.creada' AND usuario_id=? AND en>?",
                (usuario_id, desde))["n"]
    if n >= CUPOS["empresas_dia_usuario"]:
        raise CupoSuperado("Ha alcanzado el máximo de empresas que puede dar de alta en un día. Inténtelo de nuevo más tarde "
                           "o consulte al operador de la plataforma.")


def _comprobar_cupo_invitaciones(con, creada_por: int, empresa_id: int) -> None:
    if es_operador_activo(con, creada_por):
        return
    pend = db.fila(con, """SELECT COUNT(*) n FROM invitaciones WHERE empresa_id=? AND usada_en IS NULL AND revocada_en IS NULL
                           AND expira_en>?""", (empresa_id, _utc()))["n"]
    if pend >= CUPOS["invitaciones_pendientes_empresa"]:
        raise CupoSuperado("La empresa tiene demasiadas invitaciones pendientes: anule las que no se vayan a usar antes de crear otra.")
    dia = db.fila(con, "SELECT COUNT(*) n FROM invitaciones WHERE creada_por=? AND creada_en>?",
                  (creada_por, _utc(-timedelta(days=1))))["n"]
    if dia >= CUPOS["invitaciones_dia_usuario"]:
        raise CupoSuperado("Ha alcanzado el máximo de invitaciones que puede crear en un día. Inténtelo de nuevo más tarde.")


# --- NIF: una sola alta por obligado ---------------------------------------------------------------------------------------
def nifs_equivalentes(nif: str) -> tuple[str, ...]:
    """Formas de un mismo NIF. Las entidades de tipo C, D, F, G, J, U y V admiten como carácter de control el dígito o la
    letra (nif.es_valido acepta ambos): «G0012345x» con dígito y con letra son el mismo obligado tributario."""
    n = nifmod.normalizar(nif)
    if nifmod.tipo(n) == "PERSONA_JURIDICA" and n[0] not in "ABEHNPQRSW":
        digito, letra = nifmod._control_cif(n[1:8])
        return (n[:8] + digito, n[:8] + letra)
    return (n,)


def nif_ya_dado_de_alta(con, nif: str) -> bool:
    v = nifs_equivalentes(nif)
    return bool(db.fila(con, f"SELECT 1 FROM empresas WHERE nif IN ({','.join('?' * len(v))})", v))


# Columnas con NIF que no deberían contener nada más que ASCII (hoy se rechaza; antes se aceptaban cifras de otros alfabetos)
_COLUMNAS_NIF = (("empresas", "nif"), ("productor", "nif"), ("legitimaciones", "titular_nif"), ("facturas", "destinatario_nif"))
_NO_ASCII = "*[^ -~]*"   # GLOB: algún carácter fuera del ASCII imprimible


def avisar_nifs_no_ascii(con) -> dict:
    """Al arrancar: cuenta las filas guardadas con un NIF que no es solo ASCII (p. ej. «B８２…», aceptado por versiones
    anteriores a esta comprobación) y lo anota en la actividad de la plataforma (solo los recuentos) y en la de cada
    empresa afectada. Esas empresas no pueden expedir (la verificación del PDF falla) ni impiden el alta del NIF real.
    Se anota de nuevo solo si los recuentos cambian."""
    cuentas = {}
    for tabla, col in _COLUMNAS_NIF:
        if _tiene_tabla(con, tabla):
            cuentas[f"{tabla}.{col}"] = db.fila(con, f"SELECT COUNT(*) n FROM {tabla} WHERE {col} GLOB ?", (_NO_ASCII,))["n"]
    cuentas = {k: v for k, v in cuentas.items() if v}
    if not cuentas:
        return cuentas
    ult = db.fila(con, "SELECT detalle FROM actividad WHERE accion='plataforma.nif_no_ascii' AND empresa_id IS NULL "
                       "ORDER BY id DESC LIMIT 1")
    detalle = {"filas": cuentas}
    with db.transaccion(con):
        if not ult or ult["detalle"] != json.dumps(detalle, ensure_ascii=False):
            db.actividad(con, "plataforma.nif_no_ascii", detalle=detalle)
        for e in db.filas(con, "SELECT id FROM empresas WHERE nif GLOB ?", (_NO_ASCII,)):
            if not db.fila(con, "SELECT 1 FROM actividad WHERE empresa_id=? AND accion='empresa.nif_no_ascii'", (e["id"],)):
                db.actividad(con, "empresa.nif_no_ascii", empresa_id=e["id"], detalle={
                    "aviso": "El NIF de la empresa contiene caracteres que no son ASCII (por ejemplo, cifras de ancho completo): "
                             "no es un NIF válido y la empresa no puede expedir facturas. Si es un error, dé de alta la empresa "
                             "con su NIF escrito con cifras normales."})
    return cuentas


# --- Validaciones -------------------------------------------------------------------------------------------------------
_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")
_CONTROL = re.compile("[\x00-\x1f\x7f\ud800-\udfff]")


class DatosNoValidos(ValueError):
    pass


def validar_email(email) -> str:
    e = (email or "").strip() if isinstance(email, str) else ""
    if not 3 <= len(e) <= 200 or not _EMAIL.match(e) or _CONTROL.search(e):
        raise DatosNoValidos("Indique un correo electrónico válido")
    return e


def validar_nombre(nombre) -> str:
    n = (nombre or "").strip() if isinstance(nombre, str) else ""
    if not 1 <= len(n) <= 120 or _CONTROL.search(n):
        raise DatosNoValidos("Indique su nombre (hasta 120 caracteres)")
    return n


def validar_clave(clave) -> str:
    """Misma política que seguridad.hash_clave (al menos 10 caracteres) y un máximo razonable."""
    if not isinstance(clave, str) or len(clave) < 10:
        raise DatosNoValidos("La contraseña debe tener al menos 10 caracteres")
    if len(clave) > 256:
        raise DatosNoValidos("La contraseña no puede superar 256 caracteres")
    return clave


# --- Diario: cuentas y ajustes -------------------------------------------------------------------------------------------
CAMPOS_USUARIO = ("id", "email", "nombre", "hash_clave", "activo", "superadmin", "cambiar_clave", "creado_en",
                  "alta_origen", "version")


def _anotar(con, tipo: str, datos: dict) -> None:
    from . import diario
    try:
        diario.anotar(tipo, datos)
    except OSError:
        db.actividad(con, "diario.error", detalle={"anotacion": tipo})


def anotar_usuario(con, usuario_id: int) -> None:
    """Instantánea de la cuenta en el diario (incluye el resumen scrypt de la contraseña, nunca la contraseña)."""
    u = db.fila(con, f"SELECT {', '.join(CAMPOS_USUARIO)} FROM usuarios WHERE id=?", (usuario_id,))
    if u:
        _anotar(con, "usuario", u)


CAMPOS_VERIFICACION_NIF = ("id", "nif", "nif_verificado_en", "nif_verificado_por", "nif_verificado_motivo",
                           "nif_verificado_version")
MOTIVOS_VERIFICACION_NIF = ("operador", "alta_por_operador", "version_anterior")


def anotar_verificacion_nif(con, empresa_id: int) -> None:
    """Verificación del NIF de una empresa (o su retirada) por el operador, con su versión: una restauración aplica la
    más reciente (una retirada posterior a la copia no se deshace). Sin versión (nunca verificada ni retirada), nada."""
    e = db.fila(con, f"SELECT {', '.join(CAMPOS_VERIFICACION_NIF)} FROM empresas WHERE id=?", (empresa_id,))
    if e and e["nif_verificado_version"]:
        _anotar(con, "verificacion_nif", e)


def anotar_ajuste(con, clave: str) -> None:
    a = db.fila(con, "SELECT clave, valor, version, actualizado_en, actualizado_por FROM plataforma WHERE clave=?", (clave,))
    if a:
        _anotar(con, "ajuste_plataforma", a)


def anotar_invitacion_usada(con, inv_id: int) -> None:
    """Uso de una invitación (sin su token ni su resumen): al restaurar una copia anterior figura como usada, no como
    anulada por la restauración."""
    i = db.fila(con, "SELECT id, empresa_id, creada_en, usada_en, usada_por FROM invitaciones WHERE id=? AND usada_en IS NOT NULL",
                (inv_id,))
    if i:
        _anotar(con, "invitacion_usada", i)


def anotar_sesion_cerrada(con, sesion: dict) -> None:
    """Salida (cierre de sesión): las sesiones no se anotan en el diario, pero su cierre sí, para que una restauración no
    vuelva a abrir una sesión que la copia aún tenía. Solo el resumen del token (la sesión ya no vale) y el usuario."""
    _anotar(con, "sesion_cerrada", {"token_hash": sesion["token_hash"], "usuario_id": sesion["usuario_id"]})


def _tocar(con, usuario_id: int) -> None:
    con.execute("UPDATE usuarios SET version = version + 1 WHERE id=?", (usuario_id,))


# --- Invitaciones -----------------------------------------------------------------------------------------------------------
ROLES = ("admin", "emisor", "lector")
RELACIONES = ("administrador", "representante", "empleado_autorizado", "asesor")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat()


def crear_invitacion(con, *, creada_por: int, empresa_id: int | None = None, email: str | None = None,
                     rol: str | None = None, relacion: str | None = None, dias=7) -> dict:
    """Dentro de una transacción. Devuelve la invitación con su token EN CLARO (solo esta vez; se guarda su SHA-256)."""
    try:
        dias = int(dias) if not isinstance(dias, (bool, list, dict)) else None
    except (TypeError, ValueError, OverflowError):
        dias = None
    if dias is None or not 1 <= dias <= 30:
        raise DatosNoValidos("La validez debe ser un número de días entre 1 y 30")
    if email is not None and not isinstance(email, str):
        raise DatosNoValidos("Indique un correo electrónico válido")
    if relacion is not None and not isinstance(relacion, str):
        raise DatosNoValidos("Relación no válida")
    if email:
        email = validar_email(email)
    if empresa_id is not None:
        if not email:
            raise DatosNoValidos("Indique el correo electrónico de la persona invitada")
        if rol not in ROLES:
            raise DatosNoValidos("Rol no válido")
        relacion = relacion if relacion in RELACIONES else "empleado_autorizado"
        _comprobar_cupo_invitaciones(con, creada_por, empresa_id)
    else:
        rol, relacion = None, None
    token = secrets.token_urlsafe(32)
    cur = con.execute("""INSERT INTO invitaciones (token_hash, empresa_id, email, rol, relacion, creada_por, creada_en, expira_en)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (_hash_token(token), empresa_id, email or None, rol, relacion, creada_por, _utc(),
                       _utc(timedelta(days=dias))))
    db.actividad(con, "invitacion.creada", usuario_id=creada_por, empresa_id=empresa_id, objeto="invitacion",
                 objeto_id=cur.lastrowid, detalle={"email": email or None, "rol": rol, "dias": dias})
    inv = db.fila(con, "SELECT * FROM invitaciones WHERE id=?", (cur.lastrowid,))
    # crea_cuenta no depende del correo (no revela si tiene cuenta): solo del registro y de quién invita
    return dict(publica(inv), token=token, enlace=f"/#/registro?invitacion={token}", crea_cuenta=admite_cuenta_nueva(con, inv))


def estado_invitacion(inv: dict) -> str:
    if inv.get("usada_en"):
        return "usada"
    if inv.get("revocada_en"):
        return "revocada"
    if inv["expira_en"] <= _utc():
        return "caducada"
    return "pendiente"


def publica(inv: dict) -> dict:
    """Invitación sin su resumen del token."""
    out = {k: inv.get(k) for k in ("id", "empresa_id", "email", "rol", "relacion", "creada_por", "creada_en", "expira_en",
                                   "usada_en", "usada_por", "revocada_en", "revocada_motivo")}
    out["estado"] = estado_invitacion(inv)
    return out


def creador_vigente(con, inv: dict) -> bool:
    """Quien creó la invitación sigue pudiendo crearla: cuenta activa y administrador de la empresa (u operador, si es de
    la plataforma). Así, un administrador degradado, quitado o desactivado no recupera el acceso con un enlace pendiente."""
    u = db.fila(con, "SELECT activo, superadmin FROM usuarios WHERE id=?", (inv["creada_por"],))
    if not u or not u["activo"]:
        return False
    if inv["empresa_id"] is None:
        return bool(u["superadmin"])
    return seguridad.rol_en_empresa(con, inv["creada_por"], inv["empresa_id"]) == "admin"


def admite_cuenta_nueva(con, inv: dict) -> bool:
    """¿Puede usarse la invitación para CREAR una cuenta? Con el registro cerrado, una invitación de empresa creada por un
    usuario normal solo la acepta una cuenta existente (si no, cualquiera con una empresa propia crearía cuentas sin
    límite), salvo que el operador lo permita. Las de la plataforma y las que crea un operador, siempre."""
    if inv["empresa_id"] is None or registro_abierto(con) or invitaciones_empresa_crean_cuentas(con):
        return True
    u = db.fila(con, "SELECT superadmin FROM usuarios WHERE id=?", (inv["creada_por"],))
    return bool(u and u["superadmin"])


def invitacion_vigente(con, token) -> dict | None:
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    inv = db.fila(con, "SELECT * FROM invitaciones WHERE token_hash=?", (_hash_token(token),))
    if not inv or estado_invitacion(inv) != "pendiente":
        return None
    if inv["empresa_id"] is not None and not db.fila(con, "SELECT 1 FROM empresas WHERE id=?", (inv["empresa_id"],)):
        return None
    if not creador_vigente(con, inv):
        return None
    return inv


def _usar_invitacion(con, inv: dict, usuario_id: int) -> bool:
    cur = con.execute("""UPDATE invitaciones SET usada_en=?, usada_por=? WHERE id=? AND usada_en IS NULL
                         AND revocada_en IS NULL AND expira_en > ?""", (_utc(), usuario_id, inv["id"], _utc()))
    return cur.rowcount == 1


def _alta_miembro(con, inv: dict, usuario_id: int) -> None:
    antes_de_cambiar_rol(con, empresa_id=inv["empresa_id"], usuario_id=usuario_id, rol_nuevo=inv["rol"], por=usuario_id)
    con.execute("""INSERT INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,?,?,?)
                   ON CONFLICT(usuario_id, empresa_id) DO UPDATE SET rol=excluded.rol, relacion=excluded.relacion""",
                (usuario_id, inv["empresa_id"], inv["rol"], inv["relacion"] or "empleado_autorizado"))
    db.actividad(con, "miembro.alta", usuario_id=usuario_id, empresa_id=inv["empresa_id"],
                 detalle={"usuario": usuario_id, "rol": inv["rol"], "invitacion": inv["id"]})


def revocar_invitacion(con, inv_id: int, usuario_id: int, motivo: str = "revocada") -> bool:
    cur = con.execute("UPDATE invitaciones SET revocada_en=?, revocada_motivo=? WHERE id=? AND usada_en IS NULL AND revocada_en IS NULL",
                      (_utc(), motivo[:200], inv_id))
    if cur.rowcount:
        inv = db.fila(con, "SELECT empresa_id FROM invitaciones WHERE id=?", (inv_id,))
        db.actividad(con, "invitacion.revocada", usuario_id=usuario_id, empresa_id=inv["empresa_id"], objeto="invitacion", objeto_id=inv_id)
    return bool(cur.rowcount)


def revocar_invitaciones_de(con, *, creada_por: int, empresa_id: int | None = None, motivo: str, por: int | None) -> int:
    """Dentro de una transacción. Anula las invitaciones pendientes creadas por un usuario (de una empresa o todas)."""
    sql, p = "SELECT id FROM invitaciones WHERE creada_por=? AND usada_en IS NULL AND revocada_en IS NULL", [creada_por]
    if empresa_id is not None:
        sql += " AND empresa_id=?"
        p.append(empresa_id)
    return sum(revocar_invitacion(con, r["id"], por, motivo) for r in db.filas(con, sql, p))


# --- Miembros: cambio de rol y baja ------------------------------------------------------------------------------------
def _otros_admins_activos(con, empresa_id: int, usuario_id: int) -> int:
    return db.fila(con, """SELECT COUNT(*) n FROM miembros m JOIN usuarios u ON u.id=m.usuario_id
                           WHERE m.empresa_id=? AND m.rol='admin' AND m.usuario_id<>? AND u.activo=1""",
                   (empresa_id, usuario_id))["n"]


def antes_de_cambiar_rol(con, *, empresa_id: int, usuario_id: int, rol_nuevo: str, por: int | None) -> None:
    """Dentro de una transacción, antes de cambiar el rol de un miembro existente. Un administrador que deja de serlo
    pierde sus invitaciones pendientes de esa empresa, y la empresa nunca se queda sin administrador activo."""
    m = db.fila(con, "SELECT rol FROM miembros WHERE empresa_id=? AND usuario_id=?", (empresa_id, usuario_id))
    if not m or m["rol"] != "admin" or rol_nuevo == "admin":
        return
    if _otros_admins_activos(con, empresa_id, usuario_id) == 0:
        raise OperacionNoPermitida("La empresa debe conservar al menos un administrador con la cuenta activa: "
                                   "nombre antes a otro administrador.")
    revocar_invitaciones_de(con, creada_por=usuario_id, empresa_id=empresa_id, motivo="administrador_degradado", por=por)


def quitar_miembro(con, *, empresa_id: int, usuario_id: int, por: int) -> None:
    """Dentro de una transacción. Quita el acceso de una persona a la empresa (no borra su cuenta ni lo que expidió)."""
    m = db.fila(con, "SELECT rol FROM miembros WHERE empresa_id=? AND usuario_id=?", (empresa_id, usuario_id))
    if not m:
        raise LookupError()
    if m["rol"] == "admin" and _otros_admins_activos(con, empresa_id, usuario_id) == 0:
        raise OperacionNoPermitida("La empresa debe conservar al menos un administrador con la cuenta activa: "
                                   "nombre antes a otro administrador.")
    con.execute("DELETE FROM miembros WHERE empresa_id=? AND usuario_id=?", (empresa_id, usuario_id))
    revocar_invitaciones_de(con, creada_por=usuario_id, empresa_id=empresa_id, motivo="miembro_quitado", por=por)
    db.actividad(con, "miembro.baja", usuario_id=por, empresa_id=empresa_id, objeto="usuario", objeto_id=usuario_id,
                 detalle={"usuario": usuario_id, "rol": m["rol"], "propia": por == usuario_id})


# --- Registro -----------------------------------------------------------------------------------------------------------
class RegistroNoPosible(Exception):
    """Fallo que depende del estado (correo ya registrado, invitación no válida…): mensaje único, sin distinguir la causa."""


class RegistroCerrado(Exception):
    pass


class RegistroSoloConCuenta(Exception):
    """Registro cerrado e invitación de empresa que no crea cuentas: solo puede aceptarla una cuenta existente."""


MENSAJE_SOLO_CON_CUENTA = ("El registro de nuevas cuentas está cerrado: esta invitación solo puede aceptarla quien ya tenga "
                           "una cuenta. Si aún no la tiene, pida al operador de la plataforma una invitación para crearla y "
                           "después acepte esta desde su cuenta.")


MENSAJE_REGISTRO_NO_POSIBLE = ("No se ha podido completar el registro con estos datos. Si ya tiene una cuenta, inicie sesión; "
                               "si usa una invitación, compruebe que el enlace sigue vigente y que el correo es aquel al que "
                               "se dirigió la invitación.")


def registrar(con, *, email: str, nombre: str, hash_clave: str, token: str | None) -> tuple[int, int | None]:
    """Crea una cuenta normal (sin privilegios de plataforma). Devuelve (usuario_id, empresa_id de la invitación o None).

    El resumen de la contraseña se calcula ANTES (en todos los casos, también cuando el registro no es posible): el tiempo
    de respuesta no revela si el correo existe."""
    with db.transaccion(con):
        inv = None
        if token:
            inv = invitacion_vigente(con, token)
            if not inv:
                raise RegistroNoPosible()
        elif not registro_abierto(con):
            raise RegistroCerrado()
        if inv and inv["email"] and inv["email"].lower() != email.lower():
            raise RegistroNoPosible()
        # Antes de mirar si el correo existe: la respuesta no depende de que tenga cuenta
        if inv and not admite_cuenta_nueva(con, inv):
            raise RegistroSoloConCuenta()
        if db.fila(con, "SELECT 1 FROM usuarios WHERE email=?", (email,)):
            raise RegistroNoPosible()
        cur = con.execute("""INSERT INTO usuarios (email, nombre, hash_clave, creado_en, alta_origen) VALUES (?,?,?,?,?)""",
                          (email, nombre, hash_clave, db.ahora_iso(), "invitacion" if inv else "registro"))
        uid = cur.lastrowid
        eid = None
        if inv:
            if not _usar_invitacion(con, inv, uid):
                raise RegistroNoPosible()
            if inv["empresa_id"] is not None:
                eid = inv["empresa_id"]
                _alta_miembro(con, inv, uid)
        db.actividad(con, "usuario.registro", usuario_id=uid, empresa_id=eid,
                     detalle={"invitacion": inv["id"] if inv else None})
    if inv:
        anotar_invitacion_usada(con, inv["id"])
    return uid, eid


def aceptar_invitacion(con, *, usuario_id: int, token: str) -> int:
    """Un usuario con cuenta acepta la invitación a una empresa dirigida a su correo. Devuelve el id de la empresa."""
    with db.transaccion(con):
        inv = invitacion_vigente(con, token)
        u = db.fila(con, "SELECT email FROM usuarios WHERE id=?", (usuario_id,))
        if not inv or inv["empresa_id"] is None or not inv["email"] or inv["email"].lower() != u["email"].lower():
            raise RegistroNoPosible()
        if not _usar_invitacion(con, inv, usuario_id):
            raise RegistroNoPosible()
        try:
            _alta_miembro(con, inv, usuario_id)
        except OperacionNoPermitida:   # p. ej. el único administrador aceptando una invitación con otro rol
            raise RegistroNoPosible()
    anotar_invitacion_usada(con, inv["id"])
    return inv["empresa_id"]


# --- Contraseñas y cuentas -------------------------------------------------------------------------------------------------
class ClaveIncorrecta(Exception):
    pass


def _fallo_clave(con, usuario_id: int) -> None:
    """Mismo recuento atómico que el inicio de sesión: 5 fallos bloquean la cuenta 15 minutos."""
    bloqueo = (db.ahora() + timedelta(minutes=15)).isoformat()
    con.execute("""UPDATE usuarios SET
                     bloqueado_hasta = CASE WHEN intentos_fallidos + 1 >= 5 THEN ? ELSE bloqueado_hasta END,
                     intentos_fallidos = CASE WHEN intentos_fallidos + 1 >= 5 THEN 0 ELSE intentos_fallidos + 1 END
                   WHERE id=?""", (bloqueo, usuario_id))


def cambiar_clave(con, *, usuario_id: int, sesion_id: int, actual: str, nueva: str) -> None:
    validar_clave(nueva)
    if nueva == actual:
        raise DatosNoValidos("La nueva contraseña debe ser distinta de la actual")
    nuevo_hash = seguridad.hash_clave(nueva)   # fuera de la transacción: no retiene el bloqueo de escritura
    u = db.fila(con, "SELECT hash_clave, bloqueado_hasta, cambiar_clave FROM usuarios WHERE id=?", (usuario_id,))
    bloqueada = bool(u["bloqueado_hasta"] and u["bloqueado_hasta"] > db.ahora().isoformat())
    if not seguridad.verificar_clave(actual if isinstance(actual, str) else "", u["hash_clave"]) or bloqueada:
        if not bloqueada:
            _fallo_clave(con, usuario_id)
        raise ClaveIncorrecta()
    with db.transaccion(con):
        con.execute("""UPDATE usuarios SET hash_clave=?, cambiar_clave=0, intentos_fallidos=0, bloqueado_hasta=NULL
                       WHERE id=?""", (nuevo_hash, usuario_id))
        _tocar(con, usuario_id)
        # Las demás sesiones del usuario dejan de valer (p. ej. una sesión abierta en un equipo ajeno)
        con.execute("DELETE FROM sesiones WHERE usuario_id=? AND id<>?", (usuario_id, sesion_id))
        db.actividad(con, "usuario.clave_cambiada", usuario_id=usuario_id)
        if u["cambiar_clave"]:
            for e in _empresas_de(con, usuario_id):
                db.actividad(con, "usuario.clave_temporal_sustituida", usuario_id=usuario_id, empresa_id=e, objeto="usuario",
                             objeto_id=usuario_id)
    anotar_usuario(con, usuario_id)


class OperacionNoPermitida(Exception):
    pass


def _empresas_de(con, usuario_id: int) -> list[int]:
    return [r["empresa_id"] for r in db.filas(con, "SELECT empresa_id FROM miembros WHERE usuario_id=? ORDER BY empresa_id",
                                             (usuario_id,))]


def clave_restablecida_en(con, usuario_id: int) -> str | None:
    """Última vez que el operador fijó una contraseña temporal a esta cuenta (se muestra a la persona en «Mi cuenta»)."""
    r = db.fila(con, """SELECT MAX(en) en FROM actividad WHERE accion='usuario.clave_temporal' AND objeto='usuario'
                        AND objeto_id=? AND empresa_id IS NULL""", (usuario_id,))
    return r["en"] if r else None


def clave_temporal(con, *, usuario_id: int, clave: str, por: int) -> None:
    """El operador fija una contraseña temporal: se exige cambiarla al entrar y se cierran las sesiones abiertas.

    Quien conoce la contraseña temporal puede entrar como esa persona (y en sus empresas): el restablecimiento queda
    anotado en la actividad de la plataforma y en la de CADA empresa de la cuenta, a la vista de sus miembros."""
    validar_clave(clave)
    if usuario_id == por:
        raise OperacionNoPermitida("Para cambiar su propia contraseña use «Mi cuenta»")
    h = seguridad.hash_clave(clave)
    with db.transaccion(con):
        if not db.fila(con, "SELECT 1 FROM usuarios WHERE id=?", (usuario_id,)):
            raise LookupError()
        con.execute("""UPDATE usuarios SET hash_clave=?, cambiar_clave=1, intentos_fallidos=0, bloqueado_hasta=NULL
                       WHERE id=?""", (h, usuario_id))
        _tocar(con, usuario_id)
        con.execute("DELETE FROM sesiones WHERE usuario_id=?", (usuario_id,))
        db.actividad(con, "usuario.clave_temporal", usuario_id=por, objeto="usuario", objeto_id=usuario_id)
        nombre = db.fila(con, "SELECT nombre FROM usuarios WHERE id=?", (usuario_id,))["nombre"]
        for e in _empresas_de(con, usuario_id):
            db.actividad(con, "usuario.clave_temporal", usuario_id=por, empresa_id=e, objeto="usuario", objeto_id=usuario_id,
                         detalle={"cuenta": nombre, "por": "operador de la plataforma"})
    anotar_usuario(con, usuario_id)


def fijar_activo(con, *, usuario_id: int, activo: bool, por: int) -> None:
    with db.transaccion(con):
        u = db.fila(con, "SELECT id, activo, superadmin FROM usuarios WHERE id=?", (usuario_id,))
        if not u:
            raise LookupError()
        if not activo:
            if usuario_id == por:
                raise OperacionNoPermitida("No puede desactivar su propia cuenta")
            if u["superadmin"] and db.fila(con, "SELECT COUNT(*) n FROM usuarios WHERE superadmin=1 AND activo=1 AND id<>?",
                                           (usuario_id,))["n"] == 0:
                raise OperacionNoPermitida("Debe quedar al menos un operador de la plataforma activo")
        if bool(u["activo"]) == bool(activo):
            return
        con.execute("UPDATE usuarios SET activo=? WHERE id=?", (1 if activo else 0, usuario_id))
        _tocar(con, usuario_id)
        if not activo:
            con.execute("DELETE FROM sesiones WHERE usuario_id=?", (usuario_id,))
            revocar_invitaciones_de(con, creada_por=usuario_id, motivo="cuenta_desactivada", por=por)
        db.actividad(con, "usuario.activado" if activo else "usuario.desactivado", usuario_id=por, objeto="usuario",
                     objeto_id=usuario_id)
    anotar_usuario(con, usuario_id)


def listar_usuarios(con, q: str = "", uid: int | None = None) -> list[dict]:
    sql = """SELECT u.id, u.email, u.nombre, u.activo, u.superadmin, u.cambiar_clave, u.creado_en, u.alta_origen,
                    u.bloqueado_hasta,
                    (SELECT COUNT(*) FROM miembros m WHERE m.usuario_id=u.id) empresas,
                    (SELECT MAX(creada_en) FROM sesiones s WHERE s.usuario_id=u.id) ultima_sesion
             FROM usuarios u"""
    p: list = []
    if uid is not None:
        sql += " WHERE u.id=?"
        p = [uid]
    elif q:
        sql += " WHERE u.email LIKE ? OR u.nombre LIKE ?"
        p = [f"%{q}%", f"%{q}%"]
    out = db.filas(con, sql + " ORDER BY u.id LIMIT 2000", p)
    ahora = db.ahora().isoformat()
    for u in out:
        u["bloqueado"] = (u.pop("bloqueado_hasta") or "") > ahora
    return out


# --- Copias: conciliación y reproducción del diario ----------------------------------------------------------------------
def reconciliar_diario(con, entradas: list[dict]) -> int:
    """Al arrancar (y antes de restaurar): anota las cuentas y ajustes cuya versión no figure aún en el diario."""
    if not _tiene_tabla(con, "plataforma"):
        return 0
    v_usuario: dict[int, int] = {}
    v_ajuste: dict[str, int] = {}
    for e in entradas:
        d = e.get("datos") or {}
        if e.get("tipo") == "usuario" and isinstance(d.get("id"), int):
            v_usuario[d["id"]] = max(v_usuario.get(d["id"], -1), d.get("version") or 0)
        elif e.get("tipo") == "ajuste_plataforma" and isinstance(d.get("clave"), str):
            v_ajuste[d["clave"]] = max(v_ajuste.get(d["clave"], -1), d.get("version") or 0)
    usadas = {(e.get("datos") or {}).get("id") for e in entradas if e.get("tipo") == "invitacion_usada"}
    v_nif: dict[int, int] = {}
    for e in entradas:
        d = e.get("datos") or {}
        if e.get("tipo") == "verificacion_nif" and isinstance(d.get("id"), int):
            v_nif[d["id"]] = max(v_nif.get(d["id"], -1), d.get("nif_verificado_version") or 0)
    n = 0
    if "nif_verificado_version" in {c[1] for c in con.execute("PRAGMA table_info(empresas)")}:
        for e in db.filas(con, "SELECT id, nif_verificado_version v FROM empresas WHERE nif_verificado_version > 0 ORDER BY id"):
            if v_nif.get(e["id"], -1) < e["v"]:
                anotar_verificacion_nif(con, e["id"])
                n += 1
    for u in db.filas(con, "SELECT id, version FROM usuarios ORDER BY id"):
        if v_usuario.get(u["id"], -1) < u["version"]:
            anotar_usuario(con, u["id"])
            n += 1
    for a in db.filas(con, "SELECT clave, version FROM plataforma"):
        if v_ajuste.get(a["clave"], -1) < a["version"]:
            anotar_ajuste(con, a["clave"])
            n += 1
    for i in db.filas(con, "SELECT id FROM invitaciones WHERE usada_en IS NOT NULL ORDER BY id"):
        if i["id"] not in usadas:
            anotar_invitacion_usada(con, i["id"])
            n += 1
    return n


def reproducir_diario(con, entradas: list[dict]) -> dict:
    """Sobre la base que se está restaurando (claves foráneas desactivadas): aplica la última versión anotada de cada
    cuenta y de cada ajuste si es posterior a la copia, cierra las sesiones de las cuentas así actualizadas, marca como
    usadas las invitaciones que se usaron después de la copia y anula las demás pendientes (un enlace usado después de la
    copia no debe volver a valer)."""
    ult_u: dict[int, dict] = {}
    ult_a: dict[str, dict] = {}
    ult_nif: dict[int, dict] = {}
    usadas: dict[int, dict] = {}
    for e in entradas:
        d = e.get("datos") or {}
        if (e.get("tipo") == "verificacion_nif" and isinstance(d.get("id"), int) and isinstance(d.get("nif"), str)
                and type(d.get("nif_verificado_version")) is int
                and (d.get("nif_verificado_motivo") is None or d.get("nif_verificado_motivo") in MOTIVOS_VERIFICACION_NIF)
                and (d.get("nif_verificado_en") is None or isinstance(d.get("nif_verificado_en"), str))
                and (d.get("nif_verificado_por") is None or type(d.get("nif_verificado_por")) is int)):
            if d["nif_verificado_version"] >= ult_nif.get(d["id"], {}).get("nif_verificado_version", -1):
                ult_nif[d["id"]] = d
        if (e.get("tipo") == "invitacion_usada" and isinstance(d.get("id"), int) and isinstance(d.get("usada_en"), str)
                and isinstance(d.get("usada_por"), int)):
            usadas[d["id"]] = d
        if e.get("tipo") == "usuario" and isinstance(d.get("id"), int) and all(k in d for k in ("email", "hash_clave")):
            if (d.get("version") or 0) >= (ult_u.get(d["id"], {}).get("version") or -1):
                ult_u[d["id"]] = d
        elif e.get("tipo") == "ajuste_plataforma" and d.get("clave") in AJUSTES_DEFECTO:
            if (d.get("version") or 0) >= (ult_a.get(d["clave"], {}).get("version") or -1):
                ult_a[d["clave"]] = d
    cuentas, descartadas, ajustes_ = 0, [], 0
    with db.transaccion(con):
        hay_superadmin = bool(db.fila(con, "SELECT 1 FROM usuarios WHERE superadmin=1"))
        for uid, d in sorted(ult_u.items()):
            actual = db.fila(con, "SELECT id, version, superadmin FROM usuarios WHERE id=?", (uid,))
            if actual and (d.get("version") or 0) <= actual["version"]:
                continue
            otro = db.fila(con, "SELECT id FROM usuarios WHERE email=? AND id<>?", (d["email"], uid))
            if otro:
                descartadas.append({"usuario": uid, "motivo": "correo en uso por otra cuenta"})
                continue
            # El diario nunca concede el privilegio de plataforma (no hay operación que lo conceda tras la instalación),
            # salvo al restaurar una copia anterior a la propia instalación.
            sup = 1 if d.get("superadmin") and (not hay_superadmin or (actual and actual["superadmin"])) else 0
            valores = (d["email"], d.get("nombre") or "", d["hash_clave"], 1 if d.get("activo") else 0, sup,
                       1 if d.get("cambiar_clave") else 0, d.get("creado_en") or db.ahora_iso(), d.get("alta_origen"),
                       d.get("version") or 0)
            if actual:
                con.execute("""UPDATE usuarios SET email=?, nombre=?, hash_clave=?, activo=?, superadmin=?, cambiar_clave=?,
                               creado_en=?, alta_origen=?, version=? WHERE id=?""", (*valores, uid))
            else:
                con.execute("""INSERT INTO usuarios (email, nombre, hash_clave, activo, superadmin, cambiar_clave, creado_en,
                               alta_origen, version, id) VALUES (?,?,?,?,?,?,?,?,?,?)""", (*valores, uid))
            con.execute("DELETE FROM sesiones WHERE usuario_id=?", (uid,))
            hay_superadmin = hay_superadmin or bool(sup)
            cuentas += 1
        for clave, d in ult_a.items():
            actual = db.fila(con, "SELECT version FROM plataforma WHERE clave=?", (clave,))
            if actual and (d.get("version") or 0) <= actual["version"]:
                continue
            con.execute("""INSERT INTO plataforma (clave, valor, version, actualizado_en, actualizado_por) VALUES (?,?,?,?,?)
                           ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, version=excluded.version,
                           actualizado_en=excluded.actualizado_en, actualizado_por=excluded.actualizado_por""",
                        (clave, str(d.get("valor")), d.get("version") or 0, d.get("actualizado_en"), d.get("actualizado_por")))
            ajustes_ += 1
        usadas_ = sum(con.execute("""UPDATE invitaciones SET usada_en=?, usada_por=? WHERE id=? AND creada_en IS ?
                                     AND usada_en IS NULL AND revocada_en IS NULL""",
                                  (d["usada_en"], d["usada_por"], iid, d.get("creada_en"))).rowcount
                      for iid, d in sorted(usadas.items()))
        anuladas = con.execute("""UPDATE invitaciones SET revocada_en=?, revocada_motivo='restauracion'
                                  WHERE usada_en IS NULL AND revocada_en IS NULL""", (_utc(),)).rowcount
        nifs = _reproducir_verificaciones_nif(con, ult_nif)
    return {"cuentas_recuperadas": cuentas, "cuentas_descartadas": descartadas, "ajustes_recuperados": ajustes_,
            "invitaciones_usadas": usadas_, "invitaciones_anuladas": anuladas, "verificaciones_nif_cambiadas": nifs}


def _reproducir_verificaciones_nif(con, ultimas: dict[int, dict]) -> list[dict]:
    """La verificación (o retirada) del NIF más reciente de cada empresa, si es posterior a la de la copia y la empresa
    tiene el mismo NIF. Devuelve las empresas cuyo estado cambia; cada una queda en la actividad de la plataforma y en la
    de la empresa, para que el operador sepa qué ha cambiado la restauración."""
    if not ultimas or "nif_verificado_version" not in {c[1] for c in con.execute("PRAGMA table_info(empresas)")}:
        return []
    cambiadas = []
    for eid, d in sorted(ultimas.items()):
        e = db.fila(con, "SELECT id, nif, nif_verificado_en, nif_verificado_version FROM empresas WHERE id=?", (eid,))
        if not e or e["nif"] != d["nif"] or d["nif_verificado_version"] <= e["nif_verificado_version"]:
            continue
        con.execute("""UPDATE empresas SET nif_verificado_en=?, nif_verificado_por=?, nif_verificado_motivo=?,
                       nif_verificado_version=? WHERE id=?""",
                    (d.get("nif_verificado_en"), d.get("nif_verificado_por"), d.get("nif_verificado_motivo"),
                     d["nif_verificado_version"], eid))
        antes, ahora = bool(e["nif_verificado_en"]), bool(d.get("nif_verificado_en"))
        if antes != ahora:
            detalle = {"nif": e["nif"], "verificado": ahora, "motivo": "restauración: cambio posterior a la copia"}
            db.actividad(con, "empresa.nif_verificacion_restaurada", detalle=detalle)
            db.actividad(con, "empresa.nif_verificacion_restaurada", empresa_id=eid,
                         detalle={"verificado": ahora, "motivo": detalle["motivo"]})
            cambiadas.append({"empresa_id": eid, "nif": e["nif"], "verificado": ahora})
    return cambiadas
