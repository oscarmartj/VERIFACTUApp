"""API HTTP. Cada operación comprueba sesión, CSRF (si modifica) y rol en la empresa propietaria del recurso."""
from __future__ import annotations

import io
import json
import re
import secrets
import sqlite3
import threading
import zipfile

import anyio
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from . import (almacen, borradores, certificados, db, emision, errores, importacion, multiusuario, nif as nifmod, produccion,
               remision, seguridad, series, simulador)
from .config import APP_NOMBRE, APP_VERSION, ajustes
from . import pdf_final
from .pdf_final import png_paginas

router = APIRouter(prefix="/api")
COOKIE = "sid"


# --- Dependencias -----------------------------------------------------------------------------------
def get_con():
    """Una conexión SQLite por petición (las transacciones no se comparten entre hilos)."""
    con = db.conectar()
    try:
        yield con
    finally:
        con.close()


def sesion(request: Request, con=Depends(get_con)) -> dict:
    s = seguridad.sesion_por_token(con, request.cookies.get(COOKIE))
    if not s:
        raise HTTPException(401, "Sesión no iniciada o caducada")
    if request.method not in ("GET", "HEAD") and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), s["csrf"]):
        raise HTTPException(403, "Token CSRF no válido")
    # Contraseña temporal (fijada por el operador o por un alta directa): hasta cambiarla solo puede hacer eso
    if s.get("cambiar_clave") and request.url.path not in RUTAS_CON_CLAVE_TEMPORAL:
        raise HTTPException(403, "Debe cambiar su contraseña antes de continuar")
    return s


RUTAS_CON_CLAVE_TEMPORAL = ("/api/yo", "/api/yo/clave", "/api/sesion")


def rol(con, s, empresa_id: int, minimo: str = "lector") -> str:
    try:
        return seguridad.exigir_rol(con, s["usuario_id"], empresa_id, minimo)
    except seguridad.AccesoDenegado:
        raise HTTPException(404, "No encontrado")  # no revelar la existencia de recursos ajenos


def _obj(con, tabla: str, oid: int) -> dict:
    o = db.fila(con, f"SELECT * FROM {tabla} WHERE id=?", (oid,))
    if not o:
        raise HTTPException(404, "No encontrado")
    return o


def exigir_superadmin(con, s) -> None:
    """Privilegio de instalación (productor, alta de empresas, simulador). No basta ser admin de una empresa."""
    u = db.fila(con, "SELECT superadmin FROM usuarios WHERE id=?", (s["usuario_id"],))
    if not u or not u["superadmin"]:
        raise HTTPException(403, "Solo el operador de la plataforma puede realizar esta acción")


_NO_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _texto_xml(v, campo: str, maximo: int, minimo: int = 1) -> str:
    t = (v or "").strip()
    if not minimo <= len(t) <= maximo:
        raise HTTPException(422, f"{campo}: debe tener entre {minimo} y {maximo} caracteres")
    if _NO_XML.search(t):
        raise HTTPException(422, f"{campo}: contiene caracteres de control no admitidos")
    return t


def _razon_social(v) -> str:
    return _texto_xml(v, "Razón social", 120)


def _textos(body: dict, campos, mensaje: str) -> None:
    """Campos de texto opcionales del cuerpo: si vienen (no nulos), deben ser textos JSON. Si no, 422 (nunca 500)."""
    if any(body.get(k) is not None and not isinstance(body.get(k), str) for k in campos):
        raise HTTPException(422, mensaje)


_IMPORTACIONES = anyio.CapacityLimiter(2)  # la espera ocurre en el bucle de eventos, no ocupa hilos


def _png(datos: bytes) -> Response:
    return Response(datos, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


def _descarga(datos: bytes, nombre: str, tipo: str) -> Response:
    from urllib.parse import quote
    ascii_ = "".join(c if (c.isascii() and c.isalnum()) or c in "-_." else "_" for c in nombre) or "descarga"
    disp = f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nombre, safe='')}"
    return Response(datos, media_type=tipo, headers={"Content-Disposition": disp, "Cache-Control": "private, no-store"})


def _empresas_de(con, usuario_id):
    return db.filas(con, """SELECT e.id, e.nif, e.razon_social, m.rol FROM miembros m JOIN empresas e ON e.id=m.empresa_id
                           WHERE m.usuario_id=? ORDER BY e.razon_social""", (usuario_id,))


def _entornos():
    a = ajustes()
    return {"simulador": True, "pruebas": a.permitir_pruebas_aeat, "produccion": a.permitir_produccion}


# --- Sesión e instalación -------------------------------------------------------------------------------
@router.get("/estado")
def estado(con=Depends(get_con)):
    n = db.fila(con, "SELECT COUNT(*) n FROM usuarios")["n"]
    return {"instalada": n > 0, "version": APP_VERSION, "nombre": APP_NOMBRE, "entornos_habilitados": _entornos(),
            "registro_abierto": multiusuario.registro_abierto(con)}


@router.post("/instalar")
def instalar(body: dict, response: Response, con=Depends(get_con)):
    emp = body.get("empresa") or {}
    if not isinstance(emp, dict):
        raise HTTPException(422, "Indique los datos de la empresa (NIF, razón social y domicilio)")
    if not all(isinstance(body.get(k), str) for k in ("email", "nombre", "clave")):
        raise HTTPException(422, "Indique su correo electrónico, su nombre y una contraseña")
    _textos(emp, ("nif", "razon_social", "domicilio"), "NIF, razón social y domicilio de la empresa deben ser textos")
    if body.get("registro_abierto") is not None and not isinstance(body.get("registro_abierto"), bool):
        raise HTTPException(422, "registro_abierto debe ser verdadero o falso")
    n = nifmod.normalizar(emp.get("nif"))
    if not nifmod.es_valido(n):
        raise HTTPException(422, "NIF de la empresa no válido")
    rs = _razon_social(emp.get("razon_social"))
    try:
        with db.transaccion(con):
            # Comprobación dentro de la transacción: dos instalaciones simultáneas no crean dos superadministradores
            if db.fila(con, "SELECT COUNT(*) n FROM usuarios")["n"] > 0:
                raise HTTPException(409, "La aplicación ya está instalada")
            uid = seguridad.crear_usuario(con, body["email"], body["nombre"], body["clave"])
            con.execute("UPDATE usuarios SET superadmin=1, alta_origen='instalacion' WHERE id=?", (uid,))
            eid = crear_empresa(con, uid, n, rs, emp.get("domicilio"))
            _asegurar_productor(con)
            # Registro abierto a cualquiera: decisión expresa del operador (por defecto, cerrado)
            multiusuario.fijar_ajuste(con, "registro_abierto", "1" if body.get("registro_abierto") is True else "0", uid)
    except ValueError as e:
        raise HTTPException(422, str(e))
    multiusuario.anotar_usuario(con, uid)
    multiusuario.anotar_ajuste(con, "registro_abierto")
    multiusuario.anotar_verificacion_nif(con, eid)   # la empresa del operador queda verificada al crearla
    return _login_resp(con, uid, response)


def _anotar_miembros(con, eid: int) -> None:
    """Versiona los miembros de la empresa y anota la instantánea (con la fila de la empresa) en el diario."""
    from . import diario
    with db.transaccion(con):
        con.execute("UPDATE empresas SET version_miembros = version_miembros + 1 WHERE id=?", (eid,))
        emp = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
        ms = db.filas(con, "SELECT * FROM miembros WHERE empresa_id=?", (eid,))
    try:
        diario.anotar("miembros", {"empresa_id": eid, "version": emp["version_miembros"], "empresa": emp, "miembros": ms})
    except OSError:
        db.actividad(con, "diario.error", empresa_id=eid, detalle={"anotacion": "miembros"})


class EmpresaYaDadaDeAlta(sqlite3.IntegrityError):
    """El NIF (o una forma equivalente: control con dígito o con letra) ya está dado de alta en la plataforma."""


def crear_empresa(con, uid: int, nif: str, razon: str, domicilio: str | None) -> int:
    if multiusuario.nif_ya_dado_de_alta(con, nif):
        raise EmpresaYaDadaDeAlta(nif)
    cur = con.execute("INSERT INTO empresas (nif, razon_social, domicilio, creado_en) VALUES (?,?,?,?)",
                      (nif, razon.strip(), domicilio, db.ahora_iso()))
    eid = cur.lastrowid
    con.execute("INSERT INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,?, 'admin', 'administrador')", (uid, eid))
    db.actividad(con, "empresa.creada", usuario_id=uid, empresa_id=eid)
    if multiusuario.es_operador_activo(con, uid):   # la da de alta el operador: su NIF queda verificado por él
        con.execute("""UPDATE empresas SET nif_verificado_en=?, nif_verificado_por=?, nif_verificado_motivo='alta_por_operador',
                       nif_verificado_version=1 WHERE id=?""", (db.ahora_iso(), uid, eid))
        db.actividad(con, "empresa.nif_verificado", usuario_id=uid, empresa_id=eid, detalle={"motivo": "alta_por_operador"})
    return eid


def _asegurar_productor(con):
    if not db.fila(con, "SELECT id FROM productor WHERE id=1"):
        con.execute("INSERT INTO productor (id, numero_instalacion) VALUES (1, ?)", ("INST-" + secrets.token_hex(4).upper(),))


def _login_resp(con, uid: int, response: Response):
    token, csrf = seguridad.crear_sesion(con, uid)
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=ajustes().cookie_segura,
                        max_age=ajustes().sesion_horas * 3600, path="/")
    u = db.fila(con, "SELECT id, email, nombre, superadmin, cambiar_clave, alta_origen FROM usuarios WHERE id=?", (uid,))
    u["clave_restablecida_en"] = multiusuario.clave_restablecida_en(con, uid)
    return {"usuario": u, "csrf": csrf, "empresas": _empresas_de(con, uid)}


@router.post("/sesion")
def login(body: dict, response: Response, con=Depends(get_con)):
    if not isinstance(body.get("email", ""), str) or not isinstance(body.get("clave", ""), str):
        raise HTTPException(422, "Indique su correo electrónico y su contraseña")
    u = seguridad.login(con, body.get("email", ""), body.get("clave", ""))
    if not u:
        raise HTTPException(401, "Credenciales no válidas")
    db.actividad(con, "sesion.inicio", usuario_id=u["id"])
    return _login_resp(con, u["id"], response)


@router.get("/yo")
def yo(s=Depends(sesion), con=Depends(get_con)):
    u = db.fila(con, "SELECT superadmin, alta_origen FROM usuarios WHERE id=?", (s["usuario_id"],))
    return {"usuario": {"id": s["usuario_id"], "email": s["email"], "nombre": s["nombre"], "superadmin": u["superadmin"],
                        "cambiar_clave": s.get("cambiar_clave", 0), "alta_origen": u["alta_origen"],
                        "clave_restablecida_en": multiusuario.clave_restablecida_en(con, s["usuario_id"])}, "csrf": s["csrf"],
            "empresas": _empresas_de(con, s["usuario_id"])}


@router.delete("/sesion")
def logout(request: Request, response: Response, s=Depends(sesion), con=Depends(get_con)):
    seguridad.cerrar_sesion(con, request.cookies.get(COOKIE))
    multiusuario.anotar_sesion_cerrada(con, s)   # la restauración de una copia anterior no la vuelve a abrir
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# --- Empresa ---------------------------------------------------------------------------------------------
def _empresa_out(con, eid, uid):
    e = emision.cargar_empresa(con, eid)
    m = db.fila(con, "SELECT rol, relacion, declaracion_autorizacion_en FROM miembros WHERE usuario_id=? AND empresa_id=?", (uid, eid))
    e["ambito"] = borradores.evaluar_ambito(e)
    e.pop("ambito_dict", None)
    e["rol"] = m["rol"]
    e["relacion_usuario"] = m["relacion"]
    e["declaracion_autorizacion_en"] = m["declaracion_autorizacion_en"]
    e["productor"] = produccion.productor(con)
    e["casos_soportados"] = borradores.CASOS_SOPORTADOS
    e["avisos"] = [a for a in (remision.estado_cola(con, eid, ent)["aviso_incidencia"] for ent in ("simulador", "pruebas", "produccion")) if a]
    return e


@router.get("/empresas/{eid}")
def empresa_get(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    return _empresa_out(con, eid, s["usuario_id"])


EMPRESA_YA_DADA_DE_ALTA = "Esa empresa ya está dada de alta en la aplicación: pida acceso a uno de sus administradores"


@router.post("/empresas")
def empresa_nueva(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Cualquier usuario puede dar de alta sus empresas (queda como administrador). El NIF es único en la plataforma:
    una sola cadena VERI*FACTU por obligado tributario y sistema. No se revela nada de la empresa ya existente."""
    if any(body.get(k) is not None and not isinstance(body.get(k), str) for k in ("nif", "razon_social", "domicilio")):
        raise HTTPException(422, "NIF, razón social y domicilio deben ser textos")
    rs = _razon_social(body.get("razon_social"))
    n = nifmod.normalizar(body.get("nif"))
    if not nifmod.es_valido(n):
        raise HTTPException(422, "NIF no válido")
    dom = _texto_xml(body.get("domicilio"), "Domicilio", 300) if body.get("domicilio") else None
    try:
        with db.transaccion(con):
            multiusuario.comprobar_cupo_empresas(con, s["usuario_id"])
            eid = crear_empresa(con, s["usuario_id"], n, rs, dom)   # NIF único, también en sus formas equivalentes
    except sqlite3.IntegrityError:
        raise HTTPException(409, EMPRESA_YA_DADA_DE_ALTA)
    except multiusuario.CupoSuperado as e:
        raise HTTPException(429, str(e))
    _anotar_miembros(con, eid)
    multiusuario.anotar_verificacion_nif(con, eid)   # solo si la dio de alta el operador (verificada al crearla)
    return _empresa_out(con, eid, s["usuario_id"])


CAMPOS_EMPRESA = ("razon_social", "domicilio", "tipo_contribuyente", "territorio", "sii", "canarias_ceuta_melilla",
                  "otras_exclusiones", "origen_pdf", "origen_pdf_detalle")


@router.put("/empresas/{eid}")
def empresa_put(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    cambios = {k: body[k] for k in CAMPOS_EMPRESA if k in body}
    _textos(cambios, ("razon_social", "domicilio", "tipo_contribuyente", "territorio", "otras_exclusiones", "origen_pdf",
                      "origen_pdf_detalle"), "Los datos de la empresa deben ser textos")
    if any(cambios.get(k) is not None and not isinstance(cambios.get(k), int) for k in ("sii", "canarias_ceuta_melilla")):
        raise HTTPException(422, "SII y Canarias, Ceuta o Melilla deben indicarse con sí o no")
    if "razon_social" in cambios:
        cambios["razon_social"] = _razon_social(cambios["razon_social"])
    if cambios.get("domicilio"):
        cambios["domicilio"] = _texto_xml(cambios["domicilio"], "Domicilio", 300)
    if not cambios:
        raise HTTPException(422, "Nada que actualizar")
    with db.transaccion(con):
        sets = ", ".join(f"{k}=?" for k in cambios)
        con.execute(f"UPDATE empresas SET {sets}, ambito_confirmado_en=NULL, ambito_confirmado_por=NULL WHERE id=?",
                    (*cambios.values(), eid))
        e = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
        con.execute("UPDATE empresas SET ambito_resultado=? WHERE id=?", (json.dumps(borradores.evaluar_ambito(e), ensure_ascii=False), eid))
        db.actividad(con, "empresa.configuracion", usuario_id=s["usuario_id"], empresa_id=eid, detalle=cambios)
    return _empresa_out(con, eid, s["usuario_id"])


@router.post("/empresas/{eid}/ambito/confirmar")
def ambito_confirmar(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    e = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    amb = borradores.evaluar_ambito(e)
    with db.transaccion(con):
        con.execute("UPDATE empresas SET ambito_resultado=?, ambito_confirmado_en=?, ambito_confirmado_por=? WHERE id=?",
                    (json.dumps(amb, ensure_ascii=False), db.ahora_iso(), s["usuario_id"], eid))
        db.actividad(con, "empresa.ambito_confirmado", usuario_id=s["usuario_id"], empresa_id=eid, detalle=amb)
    return _empresa_out(con, eid, s["usuario_id"])


@router.post("/empresas/{eid}/autorizacion-usuario")
def autorizacion_usuario(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    if body.get("declaro") is not True or body.get("relacion") not in ("administrador", "representante", "empleado_autorizado", "asesor"):
        raise HTTPException(422, "Indique la relación y marque la declaración")
    with db.transaccion(con):
        con.execute("UPDATE miembros SET relacion=?, declaracion_autorizacion_en=? WHERE usuario_id=? AND empresa_id=?",
                    (body["relacion"], db.ahora_iso(), s["usuario_id"], eid))
        db.actividad(con, "usuario.declaracion_autorizacion", usuario_id=s["usuario_id"], empresa_id=eid,
                     detalle={"relacion": body["relacion"]})
    _anotar_miembros(con, eid)
    return _empresa_out(con, eid, s["usuario_id"])


@router.post("/empresas/{eid}/miembros")
def miembro_nuevo(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Alta de una persona en la empresa (solo administradores).

    - Ya es miembro: se actualizan su rol y su relación.
    - No es miembro: se crea una invitación de un solo uso dirigida a su correo. La respuesta es la misma exista o no una
      cuenta con ese correo (no revela qué correos están registrados ni mete a nadie en una empresa sin que acepte).
    - Compatibilidad (`clave`): solo el operador de la plataforma puede crear así una cuenta NUEVA (la contraseña así
      fijada es temporal y debe cambiarse al entrar). Si el correo ya tiene cuenta, también se crea una invitación (la
      misma respuesta que sin `clave`): nadie entra en una empresa sin aceptarlo y su contraseña no cambia."""
    rol(con, s, eid, "admin")
    if body.get("rol") not in ("admin", "emisor", "lector"):
        raise HTTPException(422, "Rol no válido")
    if any(body.get(k) is not None and not isinstance(body.get(k), str) for k in ("email", "nombre", "clave", "relacion")):
        raise HTTPException(422, "Correo, nombre, contraseña y relación deben ser textos")
    email = body.get("email", "").strip() if isinstance(body.get("email"), str) else ""
    ya = db.fila(con, "SELECT m.usuario_id FROM miembros m JOIN usuarios u ON u.id=m.usuario_id WHERE m.empresa_id=? AND u.email=?",
                 (eid, email))
    directo = bool(body.get("clave")) and bool(db.fila(con, "SELECT superadmin FROM usuarios WHERE id=?", (s["usuario_id"],))["superadmin"])
    if directo and not ya and db.fila(con, "SELECT 1 FROM usuarios WHERE email=?", (email,)):
        directo = False   # cuenta existente que no es miembro: solo por invitación (consentimiento)
    if not ya and not directo:
        from .api_multiusuario import invitar_a_empresa
        return {"accion": "invitacion", "invitacion": invitar_a_empresa(con, s, eid, body)}
    nuevo = False
    try:
        with db.transaccion(con):
            u = db.fila(con, "SELECT id FROM usuarios WHERE email=?", (email,))
            if u and not ya:   # la cuenta se creó entretanto: tampoco se la mete sin que acepte
                raise multiusuario.OperacionNoPermitida("Esa persona ya tiene una cuenta: vuelva a intentarlo para enviarle "
                                                        "una invitación.")
            if u:
                uid = u["id"]
                if ya:   # degradar a un administrador anula sus invitaciones; nunca sin administrador activo
                    multiusuario.antes_de_cambiar_rol(con, empresa_id=eid, usuario_id=uid, rol_nuevo=body["rol"],
                                                      por=s["usuario_id"])
            else:
                uid = seguridad.crear_usuario(con, body["email"], body.get("nombre") or "", body["clave"])
                con.execute("UPDATE usuarios SET cambiar_clave=1, alta_origen='alta_operador' WHERE id=?", (uid,))
                nuevo = True
            con.execute("INSERT OR REPLACE INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,?,?,?)",
                        (uid, eid, body["rol"], body.get("relacion") or "empleado_autorizado"))
            db.actividad(con, "miembro.alta", usuario_id=s["usuario_id"], empresa_id=eid, detalle={"usuario": uid, "rol": body["rol"]})
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    except multiusuario.OperacionNoPermitida as e:
        raise HTTPException(409, str(e))
    if nuevo:
        multiusuario.anotar_usuario(con, uid)
    _anotar_miembros(con, eid)
    return {"usuario_id": uid, "accion": "actualizado" if ya else "alta"}


@router.get("/empresas/{eid}/miembros")
def miembros_list(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    return db.filas(con, """SELECT u.id, u.email, u.nombre, u.activo, m.rol, m.relacion, m.declaracion_autorizacion_en
                           FROM miembros m JOIN usuarios u ON u.id=m.usuario_id WHERE m.empresa_id=? ORDER BY u.nombre""", (eid,))


@router.get("/empresas/{eid}/auditoria")
def auditoria_cadena(eid: int, entorno: str = "simulador", s=Depends(sesion), con=Depends(get_con)):
    from . import auditoria
    rol(con, s, eid)
    return auditoria.verificar_cadena(con, eid, entorno)


@router.put("/productor")
def productor_put(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Identificación del productor del SIF (privilegio de instalación)."""
    exigir_superadmin(con, s)
    _textos(body, ("nif", "nombre", "domicilio"), "Nombre, NIF y domicilio del productor deben ser textos")
    n = nifmod.normalizar(body.get("nif"))
    if not nifmod.es_valido(n) or not (body.get("nombre") or "").strip():
        raise HTTPException(422, "Indique nombre y NIF válidos del productor")
    with db.transaccion(con):
        _asegurar_productor(con)
        con.execute("UPDATE productor SET nombre=?, nif=?, domicilio=?, confirmado_por=?, confirmado_en=? WHERE id=1",
                    (body["nombre"].strip()[:120], n, body.get("domicilio"), s["usuario_id"], db.ahora_iso()))
        db.actividad(con, "productor.confirmado", usuario_id=s["usuario_id"], detalle={"nif": n, "nombre": body["nombre"]})
    return produccion.productor(con)


# --- Series -------------------------------------------------------------------------------------------------
@router.get("/empresas/{eid}/series")
def series_list(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    out = db.filas(con, "SELECT * FROM series WHERE empresa_id=? ORDER BY entorno, codigo", (eid,))
    anio_hoy = db.ahora().year
    for x in out:
        x["contadores"] = db.filas(con, "SELECT anio, siguiente FROM series_contadores WHERE serie_id=? ORDER BY anio",
                                   (x["id"],))
        # Siguiente número real (el contador del año en curso, o el único de una serie sin año), escrito con el patrón;
        # `siguiente_numero` es solo el número inicial (año de confirmación, o series sin año sin contador aún)
        con_anio = series.tiene_anio(x["patron"])
        anio = anio_hoy if con_anio else 0
        x["con_anio"] = con_anio
        x["anio_actual"] = anio
        x["anio_confirmacion"] = int(x["confirmada_en"][:4]) if x.get("confirmada_en") else None
        x["siguiente_actual"] = series.siguiente(con, x["id"], anio) if x.get("confirmada_en") else x["siguiente_numero"]
        try:
            x["siguiente_texto"] = series.formatear(x["patron"], x["siguiente_actual"], anio or anio_hoy)
            for c in x["contadores"]:
                c["siguiente_texto"] = series.formatear(x["patron"], c["siguiente"], c["anio"] or anio_hoy)
        except ValueError:   # patrón antiguo no válido
            x["siguiente_texto"] = None
    return out


@router.post("/empresas/{eid}/series")
def series_new(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    if body.get("entorno") not in ("simulador", "pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    _textos(body, ("codigo", "patron", "descripcion"), "Código, patrón y descripción de la serie deben ser textos")
    if body.get("tipo", "ordinaria") not in ("ordinaria", "rectificativa"):
        raise HTTPException(422, "Tipo de serie no válido (ordinaria o rectificativa)")
    try:
        series.patron_a_regex(body.get("patron", ""))
    except ValueError as e:
        raise HTTPException(422, str(e))
    sig = body.get("siguiente_numero")
    if sig is None or sig == "" or (type(sig) is int and sig == 0):   # sin indicar: empieza en 1
        n = 1
    else:
        n = _entero(sig, "Siguiente número", 1, series.MAX_NUMERO, texto=True)
    codigo = (body.get("codigo") or "").strip() or body["patron"]
    try:
        with db.transaccion(con):
            cur = con.execute("""INSERT INTO series (empresa_id, entorno, codigo, patron, descripcion, tipo, siguiente_numero)
                                 VALUES (?,?,?,?,?,?,?)""", (eid, body["entorno"], codigo, body["patron"], body.get("descripcion"),
                                                            body.get("tipo", "ordinaria"), n))
            db.actividad(con, "serie.creada", usuario_id=s["usuario_id"], empresa_id=eid, objeto="serie", objeto_id=cur.lastrowid,
                         detalle=body)
    except sqlite3.IntegrityError as e:   # UNIQUE (empresa_id, entorno, codigo)
        if "UNIQUE" not in str(e):
            raise
        raise HTTPException(409, f"Ya existe una serie con el código «{codigo}» en ese entorno: elija otro código.")
    return db.fila(con, "SELECT * FROM series WHERE id=?", (cur.lastrowid,))


@router.post("/series/{sid}/confirmar")
def series_confirmar(sid: int, s=Depends(sesion), con=Depends(get_con)):
    se = _obj(con, "series", sid)
    rol(con, s, se["empresa_id"], "admin")
    with db.transaccion(con):
        cur = con.execute("UPDATE series SET confirmada_por=?, confirmada_en=? WHERE id=? AND confirmada_en IS NULL",
                          (s["usuario_id"], db.ahora_iso(), sid))
        if cur.rowcount != 1:
            raise HTTPException(409, "La serie ya está confirmada")
        db.actividad(con, "serie.confirmada", usuario_id=s["usuario_id"], empresa_id=se["empresa_id"], objeto="serie", objeto_id=sid)
    return db.fila(con, "SELECT * FROM series WHERE id=?", (sid,))


@router.post("/series/probar")
def series_probar(body: dict, s=Depends(sesion)):
    _textos(body, ("patron", "num_serie"), "Patrón y número de factura deben ser textos")
    try:
        return series.probar(body.get("patron", ""), body.get("num_serie", ""))
    except ValueError as e:
        raise HTTPException(422, str(e))


# --- Documentos -------------------------------------------------------------------------------------------------
@router.post("/empresas/{eid}/documentos")
async def documentos_subir(eid: int, archivos: list[UploadFile] = File(...), s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "emisor")
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    limite = ajustes().max_mb_pdf * 1024 * 1024
    out = [{"nombre": a.filename, "estado": "error", "error": "Máximo 20 archivos por envío: vuelva a subirlo en otro lote"}
           for a in archivos[20:]]
    for a in archivos[:20]:
        datos = await a.read(limite + 1)
        if len(datos) > limite:
            out.append({"nombre": a.filename, "estado": "error", "error": f"Supera {ajustes().max_mb_pdf} MB"})
            continue
        try:
            out.append(await anyio.to_thread.run_sync(_importar_hilo, empresa, s["usuario_id"], a.filename or "", datos,
                                                      limiter=_IMPORTACIONES))
        except Exception as e:
            out.append({"nombre": a.filename, "estado": "error", "error": f"No se pudo procesar: {type(e).__name__}"})
    return out


def _importar_hilo(empresa, usuario_id, nombre, datos):
    """Procesa un PDF en un hilo (OCR, rasterizado) con su propia conexión. La concurrencia la limita el
    CapacityLimiter antes de ocupar un hilo."""
    c = db.conectar()
    try:
        return importacion.importar(c, empresa=empresa, usuario_id=usuario_id, nombre=nombre, datos=datos)
    finally:
        c.close()


@router.get("/empresas/{eid}/documentos")
def documentos_list(eid: int, q: str = "", clasificacion: str = "", s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    docs = db.filas(con, """SELECT d.id, d.nombre_archivo, d.subido_en, d.paginas, d.clasificacion, d.clasificacion_confirmada_en,
        d.estado, d.ocr_usado, d.bloqueo_emision,
        (SELECT id FROM borradores b WHERE b.documento_id=d.id AND b.estado<>'descartado' ORDER BY id DESC LIMIT 1) borrador_id,
        (SELECT id FROM facturas f WHERE f.documento_id=d.id LIMIT 1) factura_id,
        x.datos ext FROM documentos d LEFT JOIN extracciones x ON x.id=(SELECT MAX(id) FROM extracciones WHERE documento_id=d.id)
        WHERE d.empresa_id=? ORDER BY d.id DESC""", (eid,))
    out = []
    for d in docs:
        ext = json.loads(d.pop("ext") or "{}")
        d["num_serie"] = (ext.get("numero") or {}).get("valor")
        d["destinatario"] = ((ext.get("destinatario") or {}).get("nombre") or {}).get("valor")
        d["importe_total"] = (ext.get("total_factura") or {}).get("valor")
        texto = " ".join(str(x or "") for x in (d["nombre_archivo"], d["num_serie"], d["destinatario"])).lower()
        if q and q.lower() not in texto:
            continue
        if clasificacion and d["clasificacion"] != clasificacion:
            continue
        out.append(d)
    return out


@router.get("/documentos/{did}")
def documento_get(did: int, s=Depends(sesion), con=Depends(get_con)):
    d = _obj(con, "documentos", did)
    rol(con, s, d["empresa_id"])
    insp = json.loads(d.pop("inspeccion") or "{}")
    sug = json.loads(d.get("clasificacion_motivo") or "{}")
    ext = importacion.extraccion(con, did)
    if ext:
        ext.pop("_lineas_cabecera", None)
    b = db.fila(con, "SELECT id FROM borradores WHERE documento_id=? AND estado<>'descartado' ORDER BY id DESC LIMIT 1", (did,))
    return {"documento": d, "inspeccion": insp, "clasificacion_sugerida": sug, "extraccion": ext,
            "borrador_id": b["id"] if b else None,
            "duplicados_fiscales": importacion.duplicados_fiscales(con, d["empresa_id"], did)}


@router.get("/documentos/{did}/paginas/{n}.png")
def documento_pagina(did: int, n: int, s=Depends(sesion), con=Depends(get_con)):
    d = _obj(con, "documentos", did)
    rol(con, s, d["empresa_id"])
    ruta = f"empresa_{d['empresa_id']}/paginas/{d['sha256']}/{n}.png"
    if not almacen.existe(ruta):
        raise HTTPException(404, "Página no disponible")
    return _png(almacen.leer(ruta))


@router.get("/documentos/{did}/original")
def documento_original(did: int, s=Depends(sesion), con=Depends(get_con)):
    d = _obj(con, "documentos", did)
    rol(con, s, d["empresa_id"])
    return _descarga(almacen.leer(d["ruta"]), d["nombre_archivo"], "application/pdf")


@router.post("/documentos/{did}/clasificacion")
def documento_clasificar(did: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    d = _obj(con, "documentos", did)
    rol(con, s, d["empresa_id"], "emisor")
    c = body.get("clasificacion")
    if c not in ("preparatorio", "emitida_propia", "recibida", "desconocido"):
        raise HTTPException(422, "Clasificación no válida")
    if db.fila(con, "SELECT id FROM facturas WHERE documento_id=?", (did,)):
        raise HTTPException(409, "El documento ya dio lugar a una factura expedida")
    insp = json.loads(d["inspeccion"] or "{}")
    if c == "preparatorio" and any(q.get("tributario") for q in insp.get("qr", [])):
        raise HTTPException(409, "El documento contiene un QR tributario: no puede tratarse como documento preparatorio")
    sug = json.loads(d.get("clasificacion_motivo") or "{}")
    sug["confirmacion"] = {"clasificacion": c, "motivo": body.get("motivo", ""), "usuario_id": s["usuario_id"]}
    with db.transaccion(con):
        con.execute("""UPDATE documentos SET clasificacion=?, clasificacion_motivo=?, clasificacion_confirmada_por=?,
                       clasificacion_confirmada_en=? WHERE id=?""",
                    (c, json.dumps(sug, ensure_ascii=False), s["usuario_id"], db.ahora_iso(), did))
        db.actividad(con, "documento.clasificado", usuario_id=s["usuario_id"], empresa_id=d["empresa_id"], objeto="documento",
                     objeto_id=did, detalle={"clasificacion": c, "motivo": body.get("motivo", "")})
    return documento_get(did, s, con)


@router.post("/documentos/{did}/bloqueo")
def documento_bloqueo(did: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Marca un documento como de referencia/no emitible fuera del simulador local."""
    d = _obj(con, "documentos", did)
    rol(con, s, d["empresa_id"], "admin")
    _textos(body, ("motivo",), "Motivo: debe ser un texto")
    with db.transaccion(con):
        con.execute("UPDATE documentos SET bloqueo_emision=? WHERE id=?", (body.get("motivo") or None, did))
        db.actividad(con, "documento.bloqueo", usuario_id=s["usuario_id"], empresa_id=d["empresa_id"], objeto="documento",
                     objeto_id=did, detalle={"motivo": body.get("motivo")})
    return {"ok": True}


@router.post("/documentos/{did}/borrador")
def documento_borrador(did: int, s=Depends(sesion), con=Depends(get_con)):
    d = _obj(con, "documentos", did)
    rol(con, s, d["empresa_id"], "emisor")
    try:
        return {"borrador_id": importacion.crear_borrador(con, documento_id=did, usuario_id=s["usuario_id"])}
    except ValueError as e:
        raise HTTPException(409, str(e))


# --- Borradores ---------------------------------------------------------------------------------------------------
def _borrador_out(con, b: dict, entorno: str = "simulador") -> dict:
    inc, serie, empresa = emision.validar_borrador(con, b, entorno)
    ext = emision.extraccion_de(con, b)
    if ext:
        ext.pop("_lineas_cabecera", None)
    cambios = db.filas(con, """SELECT c.en, u.nombre usuario, c.campo, c.valor_anterior, c.valor_nuevo, c.motivo
                              FROM cambios_borrador c JOIN usuarios u ON u.id=c.usuario_id WHERE borrador_id=? ORDER BY c.id""", (b["id"],))
    return {"id": b["id"], "estado": b["estado"], "documento_id": b["documento_id"], "factura_id": b["factura_id"],
            "rectifica_factura_id": b["rectifica_factura_id"], "datos": json.loads(b["datos"]), "incidencias": inc,
            "puede_emitir": borradores.puede_emitir(inc), "cambios": cambios, "procedencia": ext,
            "entorno_validado": entorno,
            "serie_detectada": {k: serie[k] for k in ("id", "codigo", "patron", "numero_detectado", "siguiente_numero")} if serie else None,
            "serie_propuesta": _serie_propuesta(json.loads(b["datos"]).get("num_serie")) if not serie else None,
            "entornos_disponibles": [k for k, v in _entornos().items() if v]}


@router.get("/borradores/{bid}")
def borrador_get(bid: int, entorno: str = "simulador", s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"])
    return _borrador_out(con, b, entorno if entorno in ("simulador", "pruebas", "produccion") else "simulador")


@router.patch("/borradores/{bid}")
def borrador_patch(bid: int, body: dict, entorno: str = "simulador", s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"], "emisor")
    with db.transaccion(con):
        b = db.fila(con, "SELECT * FROM borradores WHERE id=?", (bid,))
        if b["estado"] in ("emitido", "descartado") or b["factura_id"]:
            raise HTTPException(409, "El borrador ya no es editable")
        datos = json.loads(b["datos"])
        cambios = body.get("cambios") or []
        if not isinstance(cambios, list):
            raise HTTPException(422, "«cambios» debe ser una lista")
        for c in cambios:
            try:
                if not isinstance(c, dict) or not isinstance(c.get("motivo") or "", str):
                    raise ValueError("Cada cambio debe indicar «campo», «valor» y «motivo» (texto)")
                if not borradores.texto_codificable(c.get("motivo") or ""):
                    raise ValueError("motivo: el texto contiene caracteres no válidos (sustitutos UTF-16 sueltos)")
                anterior, datos = borradores.aplicar_cambio(datos, c["campo"], c.get("valor"))
            except (ValueError, KeyError, IndexError, TypeError) as e:
                raise HTTPException(422, str(e))
            con.execute("""INSERT INTO cambios_borrador (borrador_id, usuario_id, en, campo, valor_anterior, valor_nuevo, motivo)
                           VALUES (?,?,?,?,?,?,?)""", (bid, s["usuario_id"], db.ahora_iso(), c["campo"],
                                                       json.dumps(anterior, ensure_ascii=False), json.dumps(c.get("valor"), ensure_ascii=False),
                                                       (c.get("motivo") or "")[:300]))
        datos["confirmaciones"] = []  # cualquier cambio exige volver a confirmar
        con.execute("UPDATE borradores SET datos=?, actualizado_en=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), db.ahora_iso(), bid))
    return borrador_get(bid, entorno, s, con)


@router.post("/borradores/{bid}/confirmaciones")
def borrador_confirmar(bid: int, body: dict, entorno: str = "simulador", s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"], "emisor")
    with db.transaccion(con):
        b = db.fila(con, "SELECT * FROM borradores WHERE id=?", (bid,))
        if b["estado"] in ("emitido", "descartado") or b["factura_id"]:
            raise HTTPException(409, "El borrador ya no es editable")
        datos = json.loads(b["datos"])
        claves = body.get("claves") or []
        if not isinstance(claves, list) or not all(isinstance(k, str) and len(k) <= 200 for k in claves):
            raise HTTPException(422, "«claves» debe ser una lista de textos")
        previas = [k for k in datos.get("confirmaciones") or [] if isinstance(k, str)]
        datos["confirmaciones"] = sorted(set(previas) | set(claves))
        con.execute("UPDATE borradores SET datos=?, actualizado_en=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), db.ahora_iso(), bid))
        db.actividad(con, "borrador.confirmaciones", usuario_id=s["usuario_id"], empresa_id=b["empresa_id"], objeto="borrador",
                     objeto_id=bid, detalle={"claves": body.get("claves")})
    return borrador_get(bid, entorno, s, con)


_VISTAS = threading.BoundedSemaphore(2)


def _vista_previa_limitada(con, bid):
    if not _VISTAS.acquire(timeout=30):
        raise HTTPException(429, "Demasiadas vistas previas en curso; inténtelo de nuevo en unos segundos")
    try:
        return emision.vista_previa(con, bid)
    except pdf_final.DocumentoDemasiadoLargo as e:
        raise HTTPException(422, str(e)) from e
    finally:
        _VISTAS.release()


@router.get("/borradores/{bid}/vista-previa.pdf")
def borrador_previa_pdf(bid: int, s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"])
    pdf, _ = _vista_previa_limitada(con, bid)
    return _descarga(pdf, f"borrador_{bid}.pdf", "application/pdf")


@router.get("/borradores/{bid}/vista-previa/{n}.png")
def borrador_previa_png(bid: int, n: int, s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"])
    pdf, _ = _vista_previa_limitada(con, bid)
    pags = png_paginas(pdf, pagina=n)
    if not pags:
        raise HTTPException(404, "Página no disponible")
    return _png(pags[0])


@router.post("/borradores/{bid}/emitir")
def borrador_emitir(bid: int, body: dict, request: Request, s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"], "emisor")
    if body.get("confirmo") is not True:
        raise HTTPException(422, "Debe confirmar expresamente la expedición")
    entorno = body.get("entorno")
    if entorno not in ("simulador", "pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    try:
        fid = emision.emitir(con, borrador_id=bid, usuario_id=s["usuario_id"], entorno=entorno,
                             clave_idempotencia=str(body.get("clave_idempotencia") or ""))
    except produccion.EntornoNoHabilitado as e:
        raise HTTPException(403, str(e))
    except emision.ErrorEmision as e:
        return JSONResponse({"detail": str(e), "incidencias": e.incidencias}, status_code=409)
    tr = getattr(request.app.state, "trabajador", None)
    if tr:
        tr.despertar()
    return {"factura_id": fid, "entorno": entorno}


@router.post("/borradores/{bid}/descartar")
def borrador_descartar(bid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    b = _obj(con, "borradores", bid)
    rol(con, s, b["empresa_id"], "emisor")
    with db.transaccion(con):
        cur = con.execute("""UPDATE borradores SET estado='descartado', actualizado_en=? WHERE id=?
                             AND estado NOT IN ('emitido','descartado') AND factura_id IS NULL""", (db.ahora_iso(), bid))
        if cur.rowcount != 1:
            raise HTTPException(409, "El borrador ya está emitido o descartado")
        db.actividad(con, "borrador.descartado", usuario_id=s["usuario_id"], empresa_id=b["empresa_id"], objeto="borrador",
                     objeto_id=bid, detalle={"motivo": body.get("motivo")})
    return {"ok": True}


# --- Facturas ------------------------------------------------------------------------------------------------------
SQL_ESTADO_ENVIO = """(SELECT estado_envio FROM registros r WHERE r.factura_id=f.id ORDER BY r.id DESC LIMIT 1)"""


@router.get("/empresas/{eid}/facturas")
def facturas_list(eid: int, q: str = "", cliente: str = "", desde: str = "", hasta: str = "", estado: str = "", envio: str = "",
                  entorno: str = "", s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    sql = f"""SELECT f.id, f.entorno, f.num_serie, f.fecha_expedicion, f.destinatario_nombre, f.destinatario_nif, f.importe_total,
              f.estado, f.tipo_factura, f.creada_en, {SQL_ESTADO_ENVIO} estado_envio FROM facturas f WHERE f.empresa_id=?"""
    p: list[Any] = [eid]
    if q:
        sql += " AND f.num_serie LIKE ?"
        p.append(f"%{q}%")
    if cliente:
        sql += " AND (f.destinatario_nombre LIKE ? OR f.destinatario_nif LIKE ?)"
        p += [f"%{cliente}%", f"%{cliente}%"]
    if desde:
        sql += " AND f.fecha_expedicion >= ?"
        p.append(desde)
    if hasta:
        sql += " AND f.fecha_expedicion <= ?"
        p.append(hasta)
    if estado:
        sql += " AND f.estado = ?"
        p.append(estado)
    if entorno:
        sql += " AND f.entorno = ?"
        p.append(entorno)
    sql += " ORDER BY f.id DESC"
    out = db.filas(con, sql, p)
    if envio:
        out = [f for f in out if f["estado_envio"] == envio]
    for f in out:
        f["estado_envio_texto"] = remision.TEXTO_ESTADO.get(f["estado_envio"], f["estado_envio"])
    return out


def _factura_ctx(con, s, fid, minimo="lector"):
    f = _obj(con, "facturas", fid)
    rol(con, s, f["empresa_id"], minimo)
    return f


@router.get("/facturas/{fid}")
def factura_get(fid: int, s=Depends(sesion), con=Depends(get_con)):
    f = _factura_ctx(con, s, fid)
    datos = json.loads(f.pop("datos"))
    regs = db.filas(con, """SELECT id, tipo, secuencia, huella, huella_anterior, fecha_hora_huso, estado_envio, codigo_error,
        descripcion_error, csv, intentos, subsanacion, rechazo_previo, sin_registro_previo, registro_corregido_id, cadena_huella,
        siguiente_intento_en FROM registros WHERE factura_id=? ORDER BY id""", (fid,))
    for r in regs:
        r["estado_envio_texto"] = remision.TEXTO_ESTADO.get(r["estado_envio"], r["estado_envio"])
    envios = db.filas(con, """SELECT e.id, e.destino, e.iniciado_en, e.finalizado_en, e.resultado, e.csv, e.http_estado, e.error,
        el.registro_id, el.estado_registro, el.codigo_error, el.descripcion_error FROM envio_lineas el JOIN envios e ON e.id=el.envio_id
        WHERE el.registro_id IN (SELECT id FROM registros WHERE factura_id=?) ORDER BY e.id""", (fid,))
    ultimo = regs[-1] if regs else None
    acciones = []
    if f["estado"] == "emitida":
        acciones += ["rectificativa", "anular"]
    if ultimo and ultimo["tipo"] == "alta" and ultimo["estado_envio"] in ("rechazado", "aceptado_con_errores"):
        acciones.append("subsanar")
    if ultimo and ultimo["estado_envio"] in ("pendiente", "sin_confirmar", "bloqueado"):
        acciones.append("reintentar")
    act = db.filas(con, "SELECT * FROM actividad WHERE objeto='factura' AND objeto_id=? ORDER BY id", (fid,))
    verif = next((json.loads(a["detalle"]).get("verificacion_pdf") for a in act if a["accion"] == "factura.expedida"), None)
    pags = 0
    try:
        from pypdf import PdfReader
        pags = len(PdfReader(io.BytesIO(almacen.leer(f["pdf_ruta"]))).pages)
    except Exception:
        pass
    return {"factura": f, "datos": datos, "registros": regs, "envios": envios, "url_qr": f["url_qr"], "paginas_pdf": pags,
            "verificacion": verif, "acciones_permitidas": acciones,
            "nota": "La aceptación del registro por la AEAT no supone una validación del tratamiento fiscal de la operación."}


@router.get("/facturas/{fid}/pdf")
def factura_pdf(fid: int, s=Depends(sesion), con=Depends(get_con)):
    f = _factura_ctx(con, s, fid)
    datos = almacen.leer(f["pdf_ruta"])
    if almacen.sha256(datos) != f["pdf_sha256"]:
        raise HTTPException(500, "El PDF almacenado no coincide con su huella registrada")
    pref = {"simulador": "SIMULACION_", "pruebas": "PRUEBAS_"}.get(f["entorno"], "")
    return _descarga(datos, f"{pref}factura_{f['num_serie']}.pdf", "application/pdf")


@router.get("/facturas/{fid}/pdf/{n}.png")
def factura_pdf_png(fid: int, n: int, s=Depends(sesion), con=Depends(get_con)):
    f = _factura_ctx(con, s, fid)
    pags = png_paginas(almacen.leer(f["pdf_ruta"]), pagina=n)
    if not pags:
        raise HTTPException(404, "Página no disponible")
    return _png(pags[0])


@router.get("/registros/{rid}/xml")
def registro_xml(rid: int, s=Depends(sesion), con=Depends(get_con)):
    r = _obj(con, "registros", rid)
    rol(con, s, r["empresa_id"])
    return _descarga(r["xml"].encode("utf-8"), f"registro_{r['tipo']}_{r['num_serie']}_{r['secuencia']}.xml", "application/xml")


@router.get("/empresas/{eid}/facturas-lote.zip")
def facturas_lote(eid: int, ids: str, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    lista = [int(x) for x in (x.strip() for x in ids.split(",")) if re.fullmatch(r"[0-9]{1,18}", x)][:500]   # solo cifras ASCII
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fid in lista:
            f = db.fila(con, "SELECT * FROM facturas WHERE id=? AND empresa_id=?", (fid, eid))
            if not f:
                continue
            nombre = f"{f['entorno']}_{f['num_serie'].replace('/', '-')}"
            z.writestr(f"{nombre}.pdf", almacen.leer(f["pdf_ruta"]))
            for r in db.filas(con, "SELECT * FROM registros WHERE factura_id=? ORDER BY id", (fid,)):
                z.writestr(f"{nombre}_registro_{r['secuencia']}_{r['tipo']}.xml", r["xml"])
    return _descarga(buf.getvalue(), "facturas.zip", "application/zip")


@router.post("/facturas/{fid}/rectificativa")
def factura_rectificativa(fid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    _factura_ctx(con, s, fid, "emisor")
    _textos(body, ("motivo", "tipo"), "Motivo y tipo de rectificativa deben ser textos")
    try:
        return {"borrador_id": emision.borrador_rectificativa(con, factura_id=fid, usuario_id=s["usuario_id"],
                                                              tipo=body.get("tipo", "R1"), motivo=(body.get("motivo") or "")[:300])}
    except emision.ErrorEmision as e:
        raise HTTPException(409, str(e))


@router.post("/facturas/{fid}/anular")
def factura_anular(fid: int, body: dict, request: Request, s=Depends(sesion), con=Depends(get_con)):
    _factura_ctx(con, s, fid, "admin")
    _textos(body, ("motivo",), "Motivo: debe ser un texto")
    if body.get("confirmo") is not True or not body.get("motivo"):
        raise HTTPException(422, "Indique el motivo y confirme la anulación")
    try:
        rid = emision.anular(con, factura_id=fid, usuario_id=s["usuario_id"], motivo=body["motivo"][:300])
    except produccion.EntornoNoHabilitado as e:
        raise HTTPException(403, str(e))
    except emision.ErrorEmision as e:
        raise HTTPException(409, str(e))
    if getattr(request.app.state, "trabajador", None):
        request.app.state.trabajador.despertar()
    return {"registro_id": rid}


@router.post("/registros/{rid}/subsanar")
def registro_subsanar(rid: int, body: dict, request: Request, s=Depends(sesion), con=Depends(get_con)):
    r = _obj(con, "registros", rid)
    rol(con, s, r["empresa_id"], "admin")
    _textos(body, ("motivo",), "Motivo: debe ser un texto")
    if body.get("confirmo") is not True:
        raise HTTPException(422, "Confirme la subsanación")
    try:
        nuevo = emision.subsanar(con, registro_id=rid, usuario_id=s["usuario_id"], motivo=(body.get("motivo") or "")[:300])
    except produccion.EntornoNoHabilitado as e:
        raise HTTPException(403, str(e))
    except emision.ErrorEmision as e:
        raise HTTPException(409, str(e))
    if getattr(request.app.state, "trabajador", None):
        request.app.state.trabajador.despertar()
    return {"registro_id": nuevo}


@router.post("/registros/{rid}/reintentar")
def registro_reintentar(rid: int, request: Request, s=Depends(sesion), con=Depends(get_con)):
    r = _obj(con, "registros", rid)
    rol(con, s, r["empresa_id"], "emisor")
    from datetime import datetime, timedelta, timezone
    with db.transaccion(con):
        # Decisión con el estado leído dentro de la transacción (el remitente puede haberlo cambiado)
        actual = db.fila(con, "SELECT * FROM registros WHERE id=?", (rid,))
        if actual["estado_envio"] not in ("pendiente", "sin_confirmar", "bloqueado"):
            raise HTTPException(409, "El registro ya tiene respuesta definitiva o se está enviando")
        # Un reintento manual no adelanta el anterior en menos de 60 s (evita ráfagas contra la AEAT)
        ult = db.fila(con, "SELECT iniciado_en FROM envios WHERE id=?", (actual["ultimo_envio_id"],)) if actual["ultimo_envio_id"] else None
        cuando = datetime.now(timezone.utc)
        if ult and ult["iniciado_en"]:
            try:
                cuando = max(cuando, datetime.fromisoformat(ult["iniciado_en"]) + timedelta(seconds=60))
            except ValueError:
                pass
        sig = cuando.replace(microsecond=0).isoformat()
        if actual["estado_envio"] == "bloqueado":
            # Un rechazo del envío completo bloquea todo el lote: corregida la causa, se reintenta todo en orden
            afectados = [x["id"] for x in db.filas(con, "SELECT id FROM registros WHERE empresa_id=? AND entorno=? AND estado_envio='bloqueado'",
                                                    (r["empresa_id"], r["entorno"]))]
            con.execute("""UPDATE registros SET estado_envio='pendiente', siguiente_intento_en=?, actualizado_en=?
                           WHERE empresa_id=? AND entorno=? AND estado_envio='bloqueado'""",
                        (sig, db.utc_iso(), r["empresa_id"], r["entorno"]))
        else:
            afectados = [rid]
            con.execute("UPDATE registros SET siguiente_intento_en=?, actualizado_en=? WHERE id=?", (sig, db.utc_iso(), rid))
        db.actividad(con, "registro.reintento_manual", usuario_id=s["usuario_id"], empresa_id=r["empresa_id"], objeto="registro",
                     objeto_id=rid, detalle={"afectados": afectados, "siguiente_intento_en": sig})
    from . import diario
    for x in afectados:
        try:
            diario.anotar_estado(con, x)
        except OSError:
            pass
    if getattr(request.app.state, "trabajador", None):
        request.app.state.trabajador.despertar()
    return {"ok": True, "siguiente_intento_en": sig, "afectados": len(afectados)}


# --- Remisión, certificados, producción (certificados y legitimaciones: app/api_certificados.py) ----------------------
@router.get("/empresas/{eid}/remision")
def remision_estado(eid: int, entorno: str = "simulador", s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    out = remision.estado_cola(con, eid, entorno)
    if entorno == "simulador":
        # solo el modo de fallo de esta empresa (el de otra empresa nunca le afecta)
        e = db.fila(con, "SELECT nif FROM empresas WHERE id=?", (eid,))
        out["simulador"] = simulador.modo_actual(e["nif"]) if e else simulador.modo_actual("-")
    return out


def _fijar_modo_simulador(con, s, empresa: dict, body: dict, origen: str) -> dict:
    """Modo de fallo del simulador local para los próximos envíos (o consultas) de UNA empresa (su NIF). `origen`:
    «operador» (panel de la plataforma) o «empresa» (un administrador para la suya)."""
    if not isinstance(body, dict):
        raise HTTPException(422, "Cuerpo no válido")
    modo, servicio, veces = body.get("modo", "normal"), body.get("servicio") or "remision", body.get("veces", 1)
    if not isinstance(modo, str) or not isinstance(servicio, str):
        raise HTTPException(422, "Modo desconocido")
    if isinstance(veces, bool) or not isinstance(veces, int) or not 1 <= veces <= 20:
        raise HTTPException(422, "«veces» debe ser un número entero entre 1 y 20")
    try:
        simulador.fijar_modo(modo, veces, servicio, nif=empresa["nif"], empresa_id=empresa["id"], origen=origen)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.actividad(con, "simulador.modo", usuario_id=s["usuario_id"], empresa_id=empresa["id"],
                 detalle={"modo": modo, "servicio": servicio, "veces": veces})
    # al operador solo se le devuelven los modos que fijó él (no los que la empresa fijó para sí)
    out = simulador.modo_actual(empresa["nif"], "operador" if origen == "operador" else None)
    # al operador no se le devuelve el identificador interno de una empresa de la que no es miembro, solo el NIF que indicó
    return out | ({"nif": empresa["nif"]} if origen == "operador" else {"empresa_id": empresa["id"], "nif": empresa["nif"]})


@router.post("/empresas/{eid}/simulador/modo")
def simulador_modo_empresa(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Un administrador de la empresa fija el modo de fallo del simulador local para los envíos de esa empresa. Nunca afecta
    a otra empresa ni a la AEAT. Como todas las rutas de una empresa, exige ser miembro (el operador de la plataforma usa
    POST /api/simulador/modo)."""
    rol(con, s, eid, "admin")
    e = db.fila(con, "SELECT id, nif FROM empresas WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "No encontrado")
    return _fijar_modo_simulador(con, s, e, body, "empresa")


@router.post("/simulador/modo")
def simulador_modo(body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Modo de fallo del simulador local para una empresa cualquiera, indicada por el NIF que escribe el operador de la
    plataforma (su panel; las dos formas del carácter de control valen, como en la verificación del NIF). Solo el
    operador: un administrador lo fija para la suya con POST /api/empresas/{eid}/simulador/modo, que, como toda ruta de
    una empresa, exige ser miembro y entra en el barrido de aislamiento. No acepta el identificador interno de la empresa:
    recorriéndolo, el operador podría averiguar el NIF de empresas de las que no es miembro."""
    exigir_superadmin(con, s)
    body = body if isinstance(body, dict) else {}
    if "empresa_id" in body or not isinstance(body.get("nif"), str):
        raise HTTPException(422, "Indique el NIF de la empresa: el modo del simulador es de cada empresa")
    n = nifmod.normalizar(body["nif"])
    if not nifmod.es_valido(n):
        raise HTTPException(422, "NIF no válido")
    v = multiusuario.nifs_equivalentes(n)
    e = db.fila(con, f"SELECT id, nif FROM empresas WHERE nif IN ({','.join('?' * len(v))})", v)
    if not e:
        raise HTTPException(404, "No hay ninguna empresa dada de alta con ese NIF")
    return _fijar_modo_simulador(con, s, e, {k: x for k, x in body.items() if k != "nif"}, "operador")


@router.get("/empresas/{eid}/produccion")
def produccion_get(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    e = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    req = produccion.requisitos(con, e, s["usuario_id"], "produccion")
    req_p = produccion.requisitos(con, e, s["usuario_id"], "pruebas")
    return {"requisitos": req, "habilitada": all(r["cumplido"] for r in req),
            "requisitos_pruebas": req_p, "pruebas_habilitadas": all(r["cumplido"] for r in req_p),
            "resumen_primer_envio": dict(produccion.resumen_primer_envio(con, e),
                                         texto_confirmacion_requerido=f"AUTORIZO EL ENVÍO REAL A LA AEAT DE {e['nif']}")}


@router.post("/empresas/{eid}/produccion/autorizacion")
def produccion_autorizar(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    _textos(body, ("texto_confirmacion",), "Escriba el texto de confirmación")
    texto = (body.get("texto_confirmacion") or "").strip()
    e = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    esperado = f"AUTORIZO EL ENVÍO REAL A LA AEAT DE {e['nif']}"
    if texto.upper() != esperado:
        raise HTTPException(422, f"Escriba exactamente: {esperado}")
    with db.transaccion(con):
        con.execute("INSERT INTO autorizaciones_produccion (empresa_id, usuario_id, en, resumen, texto_confirmacion) VALUES (?,?,?,?,?)",
                    (eid, s["usuario_id"], db.ahora_iso(), json.dumps(produccion.resumen_primer_envio(con, e), ensure_ascii=False), texto))
        db.actividad(con, "produccion.autorizada", usuario_id=s["usuario_id"], empresa_id=eid)
    return {"ok": True}


_SQL_ACTIVIDAD_EMPRESA = """SELECT a.id, a.en, u.nombre usuario, a.accion, a.objeto, a.objeto_id, a.detalle FROM actividad a
    LEFT JOIN usuarios u ON u.id=a.usuario_id WHERE a.empresa_id=? AND a.accion {} 'error.interno' ORDER BY a.id DESC LIMIT {}"""


@router.get("/empresas/{eid}/actividad")
def actividad(eid: int, s=Depends(sesion), con=Depends(get_con)):
    """Las 500 anotaciones más recientes de la empresa y, aparte, sus 100 errores internos más recientes (agregados por
    ruta, tipo y minuto): los errores que provoque un miembro no desplazan la auditoría de la empresa."""
    rol(con, s, eid)
    errores.volcar()   # errores internos contados de minutos ya terminados (nunca el minuto en curso)
    filas = (db.filas(con, _SQL_ACTIVIDAD_EMPRESA.format("<>", 500), (eid,))
             + db.filas(con, _SQL_ACTIVIDAD_EMPRESA.format("=", 100), (eid,)))
    return sorted(filas, key=lambda a: a["id"], reverse=True)


@router.get("/declaracion")
def declaracion(s=Depends(sesion), con=Depends(get_con)):
    from .config import BASE_DIR
    d = db.fila(con, "SELECT * FROM declaraciones WHERE version_sif=?", (APP_VERSION,))
    borrador = (BASE_DIR.parent / "docs" / "declaracion-responsable-borrador.md")
    return {"version": APP_VERSION, "estado": d["estado"] if d else "borrador",
            "formalizada_por": d["formalizada_por"] if d else None, "formalizada_en": d["formalizada_en"] if d else None,
            "contenido": d["contenido"] if d else (borrador.read_text(encoding="utf-8") if borrador.exists() else "")}


# --- Series con ceros de relleno y consulta a la AEAT (series-consulta) --------------------------------------------
def _serie_propuesta(num_serie) -> dict | None:
    """Patrón propuesto para crear la serie de un número que no encaja con ninguna serie confirmada."""
    if not isinstance(num_serie, str) or not num_serie.strip():
        return None
    return series.proponer_patron(num_serie, db.ahora().year)


@router.post("/series/proponer")
def series_proponer(body: dict, s=Depends(sesion)):
    """Propone un patrón a partir de un número de factura («042/A26» → «{n:3}/A{aa}»). No crea nada."""
    if body.get("num_serie") is not None and not isinstance(body.get("num_serie"), str):
        raise HTTPException(422, "Número de factura no válido")
    p = _serie_propuesta(body.get("num_serie"))
    return p or {"patron": None}


def _entero(v, campo: str, minimo: int, maximo: int, *, texto: bool = False) -> int:
    """Entero estricto: número JSON (no booleano) o, en la URL (`texto`), solo cifras ASCII. Fuera de rango → 422."""
    if texto and isinstance(v, str) and re.fullmatch(r"[0-9]{1,13}", v):
        v = int(v)
    if not isinstance(v, int) or isinstance(v, bool) or not minimo <= v <= maximo:
        raise HTTPException(422, f"{campo}: indique un número entero de {minimo} a {maximo}")
    return v


def _anio_avance(v, *, texto: bool = False) -> int | None:
    """Año del contador (series con {aa}/{aaaa}); ausente, vacío o 0 en las series sin año."""
    if v is None or (texto and v in ("", "0")) or (type(v) is int and v == 0):
        return None
    return _entero(v, "Año", series.ANIO_MIN, series.ANIO_MAX, texto=texto)


def _serie_de_admin(con, s, sid: int) -> dict:
    """Serie de una empresa que el usuario administra (404 si no existe o no la administra)."""
    from . import consulta
    if not 1 <= sid <= consulta.MAX_ID:
        raise HTTPException(422, "Identificador de serie no válido")
    se = _obj(con, "series", sid)
    rol(con, s, se["empresa_id"], "admin")
    return se


@router.get("/series/{sid}/avance")
def series_avance_previa(sid: int, anio: str | None = None, siguiente: str | None = None, s=Depends(sesion),
                         con=Depends(get_con)):
    """Contador actual de la serie (y último número expedido aquí) y, con `siguiente`, qué números se saltarían al
    avanzarlo. No cambia nada."""
    se = _serie_de_admin(con, s, sid)
    a = _anio_avance(anio, texto=True)
    n = None if siguiente in (None, "") else _entero(siguiente, "Siguiente número", 1, series.MAX_NUMERO, texto=True)
    try:
        out = series.preparar_avance(con, se, a, n)
    except series.AvanceNoValido as e:
        raise HTTPException(e.estado, str(e)) from e
    from . import consulta
    est = consulta.estado_cadena(con, se["empresa_id"], se["entorno"])
    # Avanzar el contador no arregla la cadena: si está atrasada respecto a la AEAT, hay que reanudarla antes de expedir
    out["aviso_cadena"] = consulta.mensaje_cadena_atrasada(est, se["entorno"]) if est and est["estado"] == "atrasada" else None
    return out


@router.post("/series/{sid}/avanzar")
def series_avanzar(sid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Avanza el contador de una serie más allá de números ya usados fuera de esta instalación (p. ej. los que la consulta
    a la AEAT muestra «solo en la AEAT» tras perder la base de datos y el diario). Administrador, con confirmación expresa
    del contador revisado (`actual`): nunca retrocede ni queda por debajo de un número expedido aquí. Queda en la actividad
    de la empresa (`serie.contador_avanzado`) y en el diario de recuperación (`serie_contador`), de modo que una
    restauración lo conserva."""
    from . import diario
    se = _serie_de_admin(con, s, sid)
    if body.get("confirmo") is not True:
        raise HTTPException(422, "Confirme el avance del contador: los números que se saltan no podrán expedirse con esta "
                                 "aplicación")
    a = _anio_avance(body.get("anio"))
    actual = _entero(body.get("actual"), "Contador revisado («actual»)", 1, series.MAX_NUMERO)
    nuevo = _entero(body.get("siguiente"), "Siguiente número", 1, series.MAX_NUMERO)
    motivo = body.get("motivo")
    if motivo is not None and not isinstance(motivo, str):
        raise HTTPException(422, "Motivo: debe ser un texto")
    motivo = _texto_xml(motivo, "Motivo", 200) if motivo and motivo.strip() else None
    try:
        with db.transaccion(con):
            se = db.fila(con, "SELECT * FROM series WHERE id=?", (sid,))
            p = series.preparar_avance(con, se, a, nuevo)
            if p["actual"] != actual:
                raise series.AvanceNoValido(f"El contador ha cambiado desde que lo revisó (ahora va por el "
                                            f"{p['actual_texto']}): revise de nuevo los números que se saltan.")
            series.avanzar(con, sid, p["anio"], nuevo)
            saltados = {k: p["saltados"][k] for k in ("desde", "hasta", "cantidad", "primero", "ultimo")}
            db.actividad(con, "serie.contador_avanzado", usuario_id=s["usuario_id"], empresa_id=se["empresa_id"], objeto="serie",
                         objeto_id=sid, detalle={"entorno": se["entorno"], "codigo": se["codigo"], "patron": se["patron"],
                                                 "anio": p["anio"], "anterior": actual, "siguiente": nuevo,
                                                 "saltados": saltados, "motivo": motivo})
            # Diario de recuperación, antes de confirmar: si no puede anotarse, no se cambia nada
            diario.anotar("serie_contador", {"serie_id": sid, "empresa_id": se["empresa_id"], "anio": p["anio"],
                                             "anterior": actual, "siguiente": nuevo, "serie": se})
    except series.AvanceNoValido as e:
        raise HTTPException(e.estado, str(e)) from e
    except OSError as e:
        raise HTTPException(503, "No se pudo anotar el avance en el diario de recuperación: no se ha cambiado nada.") from e
    return dict(p, anterior=actual)


def _cuerpo_consulta(body: dict) -> tuple[str, str, str, str | None]:
    entorno = body.get("entorno")
    if entorno not in ("simulador", "pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    for k, texto in (("ejercicio", "Ejercicio no válido (aaaa)"), ("periodo", "Periodo no válido (01 a 12)")):
        if body.get(k) is not None and (isinstance(body.get(k), bool) or not isinstance(body.get(k), (str, int))):
            raise HTTPException(422, texto)
    ejercicio = str(body.get("ejercicio") or "").strip()   # la validación estricta (cifras ASCII, rango) es de consulta
    periodo = str(body.get("periodo") or "").strip()
    if re.fullmatch(r"[1-9]", periodo):
        periodo = "0" + periodo
    ns = body.get("num_serie")
    if ns is not None and not isinstance(ns, str):
        raise HTTPException(422, "Número de factura no válido")
    ns = (ns or "").strip() or None
    if ns is not None:
        ns = _texto_xml(ns, "Número de factura", 60)
    return entorno, ejercicio, periodo, ns


@router.post("/empresas/{eid}/consulta")
def consulta_aeat(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Consulta a la AEAT los registros de la empresa en un periodo y los compara con los locales (no cambia nada)."""
    from . import consulta
    rol(con, s, eid, "emisor")
    entorno, ejercicio, periodo, ns = _cuerpo_consulta(body)
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    try:
        return consulta.consultar_y_comparar(con, empresa, s["usuario_id"], entorno, ejercicio, periodo, ns)
    except consulta.ErrorConsulta as e:
        raise HTTPException(e.estado, str(e)) from e


@router.post("/empresas/{eid}/consulta/reanudar-cadena")
def consulta_reanudar_cadena(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Reanuda la cadena desde el último registro que tiene la AEAT (administrador, CSRF, con confirmación y la huella del
    registro revisado). Vuelve a consultar en el servidor; se audita y se anota en el diario. Idempotente."""
    from . import consulta
    rol(con, s, eid, "admin")
    entorno = body.get("entorno")
    if entorno not in ("simulador", "pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    if body.get("confirmo") is not True:
        raise HTTPException(422, f"Confirme que ha revisado el último registro {consulta.servicio(entorno, prep='de')} antes "
                                 "de reanudar la cadena")
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    try:
        return consulta.reanudar_cadena(con, empresa, s["usuario_id"], entorno, body.get("huella"))
    except consulta.ErrorConsulta as e:
        raise HTTPException(e.estado, str(e)) from e


@router.post("/empresas/{eid}/consulta/conciliar")
def consulta_conciliar(eid: int, body: dict, request: Request, s=Depends(sesion), con=Depends(get_con)):
    """Conciliación segura (administrador, con confirmación): marca como aceptados los registros sin respuesta
    definitiva que la AEAT tiene con la misma huella. Vuelve a consultar antes de cambiar nada."""
    from . import consulta
    rol(con, s, eid, "admin")
    if body.get("confirmo") is not True:
        raise HTTPException(422, "Confirme la conciliación")
    entorno = body.get("entorno")
    if entorno not in ("simulador", "pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    ids = body.get("registro_ids")
    if not isinstance(ids, list) or not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= consulta.MAX_ID
                                            for x in ids):
        raise HTTPException(422, "«registro_ids» debe ser una lista de identificadores (enteros positivos)")
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    try:
        r = consulta.conciliar(con, empresa, s["usuario_id"], entorno, ids)
    except consulta.ErrorConsulta as e:
        raise HTTPException(e.estado, str(e)) from e
    tr = getattr(request.app.state, "trabajador", None)
    if tr and r["conciliados"]:
        tr.despertar()
    return r
