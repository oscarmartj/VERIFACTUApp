"""API de la plataforma multiusuario: registro, invitaciones, contraseñas y panel del operador de la plataforma.

El operador (privilegio `superadmin`) gestiona cuentas, invitaciones de la plataforma, el productor del SIF y el
simulador, pero NO obtiene acceso a los datos de ninguna empresa: eso solo lo da ser miembro de ella (`rol()`).
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import db, errores, multiusuario as mu, nif as nifmod, produccion, seguridad, simulador
from .api import _anotar_miembros, _empresas_de, _login_resp, _texto_xml, exigir_superadmin, get_con, rol, sesion

router = APIRouter(prefix="/api")


def _ip(request: Request) -> str:
    # Detrás de un proxy inverso, uvicorn debe arrancarse con --proxy-headers y --forwarded-allow-ips para que esta sea
    # la dirección real del cliente (si no, todas las peticiones compartirían el límite del proxy).
    return request.client.host if request.client else "desconocida"


def _limite_por_ip_o_invitacion(con, request: Request, token, limite_ip: mu.Limitador, uso: str) -> tuple[bool, dict | None]:
    """Con una invitación vigente, la petición cuenta en los límites de esa invitación, de quien la creó y en un techo por
    IP más alto que el normal (el personal de una oficina se da de alta desde la misma IP), nunca en los comunes a toda la
    plataforma. Sin invitación o con un enlace no válido (adivinado, caducado, usado), cuenta por IP como siempre.
    Devuelve (cabe, invitación vigente o None)."""
    inv = mu.invitacion_vigente(con, token) if isinstance(token, str) else None
    if inv:
        return mu.consumir_con_invitacion(con, inv, uso, _ip(request)), inv
    return limite_ip.consumir(_ip(request)), None


def _es_superadmin(con, s) -> bool:
    u = db.fila(con, "SELECT superadmin FROM usuarios WHERE id=?", (s["usuario_id"],))
    return bool(u and u["superadmin"])


# --- Registro e invitaciones (sin sesión) -----------------------------------------------------------------------------
@router.post("/registro")
def registro(body: dict, request: Request, response: Response, con=Depends(get_con)):
    """Alta de una cuenta normal. Con el registro cerrado solo es posible con una invitación vigente."""
    token = body.get("invitacion")
    token = None if token is None or token == "" else token
    cabe, inv = _limite_por_ip_o_invitacion(con, request, token, mu.LIMITE_REGISTRO_IP, "registro")
    # El límite de intentos de toda la plataforma solo lo gasta el registro sin invitación vigente (ya limitado por IP)
    if not cabe or (inv is None and not mu.LIMITE_REGISTRO_INTENTOS.consumir()):
        raise HTTPException(429, "Demasiados intentos de registro. Inténtelo de nuevo más tarde.")
    try:
        email = mu.validar_email(body.get("email"))
        nombre = mu.validar_nombre(body.get("nombre"))
        clave = mu.validar_clave(body.get("clave"))
    except mu.DatosNoValidos as e:
        raise HTTPException(422, str(e))
    if token is not None and not isinstance(token, str):
        raise HTTPException(422, "Invitación no válida")
    h = seguridad.hash_clave(clave)   # siempre, también si luego falla: el tiempo no revela si el correo existe
    if not mu.LIMITE_REGISTRO_GLOBAL.disponible():
        raise HTTPException(429, "La plataforma ha alcanzado el máximo de altas por hora. Inténtelo de nuevo más tarde.")
    if not mu.altas_con_invitacion_disponibles(con, inv, _ip(request)):
        raise HTTPException(429, "Se ha alcanzado el máximo de altas por hora con invitaciones de quien le ha invitado o "
                                 "desde esta conexión. Inténtelo de nuevo más tarde.")
    try:
        uid, eid = mu.registrar(con, email=email, nombre=nombre, hash_clave=h, token=token)
    except mu.RegistroCerrado:
        raise HTTPException(403, "El registro de nuevas cuentas está cerrado. Solicite una invitación al operador de la "
                                 "plataforma o al administrador de su empresa.")
    except mu.RegistroSoloConCuenta:
        raise HTTPException(403, mu.MENSAJE_SOLO_CON_CUENTA)
    except mu.RegistroNoPosible:
        raise HTTPException(409, mu.MENSAJE_REGISTRO_NO_POSIBLE)
    mu.anotar_alta(con, inv, _ip(request))
    mu.anotar_usuario(con, uid)
    if eid:
        _anotar_miembros(con, eid)
    return _login_resp(con, uid, response)


@router.post("/invitaciones/consulta")
def invitacion_consulta(body: dict, request: Request, con=Depends(get_con)):
    """Datos de una invitación para quien tiene el enlace (el token va en el cuerpo, nunca en la URL)."""
    if not _limite_por_ip_o_invitacion(con, request, body.get("token"), mu.LIMITE_CONSULTA_INVITACION, "consulta")[0]:
        raise HTTPException(429, "Demasiadas consultas. Inténtelo de nuevo más tarde.")
    if not isinstance(body.get("token"), str):
        raise HTTPException(422, "Invitación no válida")
    inv = mu.invitacion_vigente(con, body.get("token"))
    if not inv:
        return {"valida": False}
    emp = db.fila(con, "SELECT razon_social FROM empresas WHERE id=?", (inv["empresa_id"],)) if inv["empresa_id"] else None
    return {"valida": True, "email": inv["email"], "empresa": emp["razon_social"] if emp else None, "rol": inv["rol"],
            "expira_en": inv["expira_en"], "crea_cuenta": mu.admite_cuenta_nueva(con, inv)}


@router.post("/invitaciones/aceptar")
def invitacion_aceptar(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Un usuario con cuenta acepta una invitación a una empresa dirigida a su correo."""
    if not isinstance(body.get("token"), str):
        raise HTTPException(422, "Invitación no válida")
    try:
        eid = mu.aceptar_invitacion(con, usuario_id=s["usuario_id"], token=body.get("token"))
    except mu.RegistroNoPosible:
        raise HTTPException(409, "La invitación no es válida para esta cuenta: puede haber caducado, haberse usado ya o "
                                 "estar dirigida a otro correo.")
    _anotar_miembros(con, eid)
    return {"empresa_id": eid, "empresas": _empresas_de(con, s["usuario_id"])}


@router.delete("/invitaciones/{iid}")
def invitacion_revocar(iid: int, s=Depends(sesion), con=Depends(get_con)):
    inv = db.fila(con, "SELECT * FROM invitaciones WHERE id=?", (iid,))
    if not inv:
        raise HTTPException(404, "No encontrado")
    if inv["empresa_id"] is None:
        if not _es_superadmin(con, s):
            raise HTTPException(404, "No encontrado")
    else:
        rol(con, s, inv["empresa_id"], "admin")
    with db.transaccion(con):
        if not mu.revocar_invitacion(con, iid, s["usuario_id"]):
            raise HTTPException(409, "La invitación ya se usó o se anuló")
    return mu.publica(db.fila(con, "SELECT * FROM invitaciones WHERE id=?", (iid,)))


# --- Invitaciones de una empresa (administradores de la empresa) -----------------------------------------------------
def invitar_a_empresa(con, s, eid: int, body: dict) -> dict:
    try:
        with db.transaccion(con):
            return mu.crear_invitacion(con, creada_por=s["usuario_id"], empresa_id=eid, email=body.get("email"),
                                       rol=body.get("rol"), relacion=body.get("relacion"), dias=body.get("dias", 7))
    except mu.DatosNoValidos as e:
        raise HTTPException(422, str(e))
    except mu.CupoSuperado as e:
        raise HTTPException(429, str(e))


@router.post("/empresas/{eid}/invitaciones")
def empresa_invitar(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    return invitar_a_empresa(con, s, eid, body)


@router.get("/empresas/{eid}/invitaciones")
def empresa_invitaciones(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    return [mu.publica(i) for i in db.filas(con, "SELECT * FROM invitaciones WHERE empresa_id=? ORDER BY id DESC LIMIT 200", (eid,))]


@router.delete("/empresas/{eid}/miembros/{uid}")
def empresa_quitar_miembro(eid: int, uid: int, s=Depends(sesion), con=Depends(get_con)):
    """Quita el acceso de una persona a la empresa (administradores) o el propio («salir de la empresa», cualquier
    miembro). Se anulan sus invitaciones pendientes de la empresa; la empresa conserva siempre un administrador activo.
    No borra la cuenta ni lo que expidió."""
    rol(con, s, eid, "lector" if uid == s["usuario_id"] else "admin")
    try:
        with db.transaccion(con):
            mu.quitar_miembro(con, empresa_id=eid, usuario_id=uid, por=s["usuario_id"])
    except LookupError:
        raise HTTPException(404, "No encontrado")
    except mu.OperacionNoPermitida as e:
        raise HTTPException(409, str(e))
    _anotar_miembros(con, eid)
    return {"ok": True, "empresas": _empresas_de(con, s["usuario_id"])}


# --- Cuenta propia --------------------------------------------------------------------------------------------------------
@router.put("/yo/clave")
def yo_clave(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Cambio de la contraseña propia: exige la actual y cierra las demás sesiones del usuario."""
    if not isinstance(body.get("clave_actual"), str):
        raise HTTPException(422, "Indique su contraseña actual")
    try:
        mu.cambiar_clave(con, usuario_id=s["usuario_id"], sesion_id=s["id"], actual=body["clave_actual"],
                         nueva=body.get("clave_nueva"))
    except mu.DatosNoValidos as e:
        raise HTTPException(422, str(e))
    except mu.ClaveIncorrecta:
        raise HTTPException(403, "La contraseña actual no es correcta")
    return {"ok": True}


# --- Panel del operador de la plataforma ------------------------------------------------------------------------------
@router.get("/plataforma")
def plataforma(s=Depends(sesion), con=Depends(get_con)):
    exigir_superadmin(con, s)
    tot = db.fila(con, """SELECT (SELECT COUNT(*) FROM usuarios) usuarios, (SELECT COUNT(*) FROM usuarios WHERE activo=1) usuarios_activos,
                                 (SELECT COUNT(*) FROM empresas) empresas""")
    # simulador: los modos de fallo pendientes que fijó el operador (por el NIF que escribió); no hay un modo común a todas
    # las empresas, y los que fija cada empresa para la suya no se listan (no se muestra el NIF de empresas ajenas)
    return {"ajustes": _ajustes(con), "productor": produccion.productor(con),
            "simulador": {"empresas": simulador.modos_fijados()},
            "totales": tot, "cupos": dict(mu.CUPOS)}


def _ajustes(con) -> dict:
    return {"registro_abierto": mu.registro_abierto(con),
            "invitaciones_empresa_crean_cuentas": mu.invitaciones_empresa_crean_cuentas(con)}


@router.put("/plataforma/ajustes")
def plataforma_ajustes(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Ajustes booleanos de la plataforma: registro_abierto e invitaciones_empresa_crean_cuentas (al menos uno)."""
    exigir_superadmin(con, s)
    cambios = {k: body[k] for k in mu.AJUSTES_DEFECTO if k in body}
    if not cambios or not all(isinstance(v, bool) for v in cambios.values()):
        raise HTTPException(422, "Indique registro_abierto o invitaciones_empresa_crean_cuentas (verdadero o falso)")
    with db.transaccion(con):
        cambiados = [k for k, v in cambios.items() if mu.fijar_ajuste(con, k, "1" if v else "0", s["usuario_id"])]
    for k in cambiados:
        mu.anotar_ajuste(con, k)
    return _ajustes(con)


@router.get("/plataforma/usuarios")
def plataforma_usuarios(q: str = "", s=Depends(sesion), con=Depends(get_con)):
    exigir_superadmin(con, s)
    return mu.listar_usuarios(con, q.strip()[:100])


@router.put("/plataforma/usuarios/{uid}")
def plataforma_usuario(uid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    exigir_superadmin(con, s)
    if not isinstance(body.get("activo"), bool):
        raise HTTPException(422, "Indique activo (verdadero o falso)")
    try:
        mu.fijar_activo(con, usuario_id=uid, activo=body["activo"], por=s["usuario_id"])
    except LookupError:
        raise HTTPException(404, "No encontrado")
    except mu.OperacionNoPermitida as e:
        raise HTTPException(409, str(e))
    return next(u for u in mu.listar_usuarios(con, uid=uid))


@router.post("/plataforma/usuarios/{uid}/clave-temporal")
def plataforma_clave_temporal(uid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Contraseña temporal escrita por el operador (no se devuelve nunca): el usuario deberá cambiarla al entrar."""
    exigir_superadmin(con, s)
    try:
        mu.clave_temporal(con, usuario_id=uid, clave=body.get("clave"), por=s["usuario_id"])
    except mu.DatosNoValidos as e:
        raise HTTPException(422, str(e))
    except LookupError:
        raise HTTPException(404, "No encontrado")
    except mu.OperacionNoPermitida as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/plataforma/invitaciones")
def plataforma_invitaciones(s=Depends(sesion), con=Depends(get_con)):
    exigir_superadmin(con, s)
    return [mu.publica(i) for i in db.filas(con, "SELECT * FROM invitaciones WHERE empresa_id IS NULL ORDER BY id DESC LIMIT 200")]


@router.post("/plataforma/invitaciones")
def plataforma_invitar(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Invitación de la plataforma: permite crear una cuenta aunque el registro esté cerrado (sin acceso a ninguna empresa)."""
    exigir_superadmin(con, s)
    try:
        with db.transaccion(con):
            return mu.crear_invitacion(con, creada_por=s["usuario_id"], email=body.get("email"), dias=body.get("dias", 7))
    except mu.DatosNoValidos as e:
        raise HTTPException(422, str(e))


# --- Verificación del NIF de una empresa (operador) --------------------------------------------------------------------
def _empresa_por_nif(con, nif) -> dict:
    if not isinstance(nif, str):
        raise HTTPException(422, "Indique el NIF de la empresa")
    n = nifmod.normalizar(nif)
    if not nifmod.es_valido(n):
        raise HTTPException(422, "NIF no válido")
    v = mu.nifs_equivalentes(n)
    e = db.fila(con, f"""SELECT id, nif, razon_social, nif_verificado_en, nif_verificado_motivo FROM empresas
                         WHERE nif IN ({','.join('?' * len(v))})""", v)
    if not e:
        raise HTTPException(404, "No hay ninguna empresa dada de alta con ese NIF")
    return e


def _verificacion_out(e: dict) -> dict:
    return {"nif": e["nif"], "razon_social": e["razon_social"], "verificado": bool(e["nif_verificado_en"]),
            "verificado_en": e["nif_verificado_en"], "motivo": e["nif_verificado_motivo"]}


@router.get("/plataforma/empresas/verificacion-nif")
def plataforma_verificacion_nif(nif: str = "", s=Depends(sesion), con=Depends(get_con)):
    """Estado de la verificación del NIF de una empresa (el operador la busca por su NIF; no ve nada más de ella)."""
    exigir_superadmin(con, s)
    return _verificacion_out(_empresa_por_nif(con, nif))


@router.post("/plataforma/empresas/verificacion-nif")
def plataforma_verificar_nif(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """El operador marca el NIF de una empresa como verificado (o retira la marca), tras comprobar por su cuenta que quien
    la dio de alta actúa por ella. Sin esa marca, el primer envío real a la AEAT con el certificado de un tercero no es
    posible (certificados.nif_acreditado). Queda en la actividad de la plataforma y en la de la empresa."""
    exigir_superadmin(con, s)
    if not isinstance(body.get("verificado"), bool):
        raise HTTPException(422, "Indique si el NIF queda verificado (verdadero o falso)")
    nota = body.get("nota")
    if nota is not None and not isinstance(nota, str):
        raise HTTPException(422, "Nota: debe ser un texto")
    nota = _texto_xml(nota, "Nota", 200) if nota and nota.strip() else None
    e = _empresa_por_nif(con, body.get("nif"))
    accion = "empresa.nif_verificado" if body["verificado"] else "empresa.nif_verificacion_retirada"
    with db.transaccion(con):
        if body["verificado"]:
            con.execute("""UPDATE empresas SET nif_verificado_en=?, nif_verificado_por=?, nif_verificado_motivo='operador',
                           nif_verificado_version=nif_verificado_version+1 WHERE id=?""", (db.ahora_iso(), s["usuario_id"], e["id"]))
        else:
            con.execute("""UPDATE empresas SET nif_verificado_en=NULL, nif_verificado_por=NULL, nif_verificado_motivo=NULL,
                           nif_verificado_version=nif_verificado_version+1 WHERE id=?""", (e["id"],))
        db.actividad(con, accion, usuario_id=s["usuario_id"], detalle={"nif": e["nif"], "nota": nota})
        db.actividad(con, accion, usuario_id=s["usuario_id"], empresa_id=e["id"],
                     detalle={"por": "operador de la plataforma", "nota": nota})
    mu.anotar_verificacion_nif(con, e["id"])   # en el diario: una restauración no deshace este cambio
    return _verificacion_out(_empresa_por_nif(con, e["nif"]))


# Errores técnicos anotados sin empresa: el operador ve que ocurrieron y de qué tipo, pero no la ruta con identificadores
# ni el mensaje (pueden contener identificadores de empresas, facturas, registros o documentos, o datos de su contenido).
_ERRORES_SIN_EMPRESA = ("error.interno", "remision.error", "diario.error")


def _sin_datos_de_empresas(a: dict) -> dict:
    if a["accion"] not in _ERRORES_SIN_EMPRESA:
        return a
    try:
        d = json.loads(a["detalle"] or "{}")
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    limpio: dict = {}
    if isinstance(d.get("ruta"), str):     # /api/empresas/7/facturas -> /api/empresas/{id}/facturas
        limpio["ruta"] = "/".join("{id}" if any(ch.isdigit() for ch in p) else p for p in d["ruta"].split("/"))[:200]
    for k in ("tipo", "error", "anotacion"):   # solo nombres (tipo de excepción, tipo de anotación): «OSError: …» -> «OSError»
        v = d[k].split(":", 1)[0].strip() if isinstance(d.get(k), str) else ""
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", v):
            limpio[k] = v
    if type(d.get("veces")) is int:   # errores agregados por minuto (app/errores.py)
        limpio["veces"] = d["veces"]
    if isinstance(d.get("minuto"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", d["minuto"], re.ASCII):
        limpio["minuto"] = d["minuto"]
    return dict(a, objeto=None, objeto_id=None, detalle=json.dumps(limpio, ensure_ascii=False))


_SQL_ACTIVIDAD_PLATAFORMA = """SELECT a.id, a.en, u.email usuario, a.accion, a.objeto, a.objeto_id, a.detalle FROM actividad a
    LEFT JOIN usuarios u ON u.id=a.usuario_id WHERE a.empresa_id IS NULL AND a.accion NOT GLOB 'certificado_usuario.*'
    AND {} ORDER BY a.id DESC LIMIT {}"""

# Anotaciones sin empresa que cualquier cuenta produce por sí misma y tantas veces como quiera (entrar, cambiar su propia
# contraseña, registrarse). Van en su propia lista: con ellas nadie desplaza la auditoría del operador y del sistema
# (contraseñas temporales, bajas, ajustes, verificaciones de NIF, avisos del arranque…) ni los errores.
ACCIONES_DE_LAS_CUENTAS = ("sesion.inicio", "usuario.clave_cambiada", "usuario.registro")
_EN_CUENTAS = "a.accion IN ({})".format(", ".join(f"'{x}'" for x in ACCIONES_DE_LAS_CUENTAS))


@router.get("/plataforma/actividad")
def plataforma_actividad(s=Depends(sesion), con=Depends(get_con)):
    """Actividad de la plataforma (sin empresa): altas, ajustes, invitaciones, sesiones y errores técnicos (solo su tipo).
    Nunca la de las empresas ni la de «Mis certificados» de cada usuario (titular y entidad representada son datos del
    usuario y de sus empresas; su propietario los ve en «Ver usos»). Tres listas, cada una con su límite, para que
    ninguna desplace a las otras: las 500 anotaciones más recientes de auditoría (operador y sistema), las 500 más
    recientes de las cuentas (inicios de sesión, cambios de la propia contraseña, altas) y los 100 errores internos más
    recientes (agregados por minuto), que puede provocar cualquiera, también sin sesión."""
    exigir_superadmin(con, s)
    errores.volcar()   # errores de minutos ya terminados (nunca el minuto en curso)
    filas = (db.filas(con, _SQL_ACTIVIDAD_PLATAFORMA.format(f"a.accion <> 'error.interno' AND NOT {_EN_CUENTAS}", 500))
             + db.filas(con, _SQL_ACTIVIDAD_PLATAFORMA.format(_EN_CUENTAS, 500))
             + db.filas(con, _SQL_ACTIVIDAD_PLATAFORMA.format("a.accion = 'error.interno'", 100)))
    return [_sin_datos_de_empresas(a) for a in sorted(filas, key=lambda a: a["id"], reverse=True)]
