"""API HTTP. Cada operación comprueba sesión, CSRF (si modifica) y rol en la empresa propietaria del recurso."""
from __future__ import annotations

import io
import json
import re
import secrets
import threading
import zipfile

import anyio
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from . import (almacen, borradores, certificados, db, emision, importacion, nif as nifmod, produccion, remision,
               seguridad, series, simulador)
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
    return s


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
        raise HTTPException(403, "Requiere el privilegio de administración de la instalación")


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
    return {"instalada": n > 0, "version": APP_VERSION, "nombre": APP_NOMBRE, "entornos_habilitados": _entornos()}


@router.post("/instalar")
def instalar(body: dict, response: Response, con=Depends(get_con)):
    emp = body.get("empresa") or {}
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
            con.execute("UPDATE usuarios SET superadmin=1 WHERE id=?", (uid,))
            eid = crear_empresa(con, uid, n, rs, emp.get("domicilio"))
            _asegurar_productor(con)
    except ValueError as e:
        raise HTTPException(422, str(e))
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


def crear_empresa(con, uid: int, nif: str, razon: str, domicilio: str | None) -> int:
    cur = con.execute("INSERT INTO empresas (nif, razon_social, domicilio, creado_en) VALUES (?,?,?,?)",
                      (nif, razon.strip(), domicilio, db.ahora_iso()))
    eid = cur.lastrowid
    con.execute("INSERT INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,?, 'admin', 'administrador')", (uid, eid))
    db.actividad(con, "empresa.creada", usuario_id=uid, empresa_id=eid)
    return eid


def _asegurar_productor(con):
    if not db.fila(con, "SELECT id FROM productor WHERE id=1"):
        con.execute("INSERT INTO productor (id, numero_instalacion) VALUES (1, ?)", ("INST-" + secrets.token_hex(4).upper(),))


def _login_resp(con, uid: int, response: Response):
    token, csrf = seguridad.crear_sesion(con, uid)
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=ajustes().cookie_segura,
                        max_age=ajustes().sesion_horas * 3600, path="/")
    u = db.fila(con, "SELECT id, email, nombre, superadmin FROM usuarios WHERE id=?", (uid,))
    return {"usuario": u, "csrf": csrf, "empresas": _empresas_de(con, uid)}


@router.post("/sesion")
def login(body: dict, response: Response, con=Depends(get_con)):
    u = seguridad.login(con, body.get("email", ""), body.get("clave", ""))
    if not u:
        raise HTTPException(401, "Credenciales no válidas")
    db.actividad(con, "sesion.inicio", usuario_id=u["id"])
    return _login_resp(con, u["id"], response)


@router.get("/yo")
def yo(s=Depends(sesion), con=Depends(get_con)):
    sup = db.fila(con, "SELECT superadmin FROM usuarios WHERE id=?", (s["usuario_id"],))["superadmin"]
    return {"usuario": {"id": s["usuario_id"], "email": s["email"], "nombre": s["nombre"], "superadmin": sup}, "csrf": s["csrf"],
            "empresas": _empresas_de(con, s["usuario_id"])}


@router.delete("/sesion")
def logout(request: Request, response: Response, s=Depends(sesion), con=Depends(get_con)):
    seguridad.cerrar_sesion(con, request.cookies.get(COOKIE))
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


@router.post("/empresas")
def empresa_nueva(body: dict, s=Depends(sesion), con=Depends(get_con)):
    exigir_superadmin(con, s)
    rs = _razon_social(body.get("razon_social"))
    n = nifmod.normalizar(body.get("nif"))
    if not nifmod.es_valido(n):
        raise HTTPException(422, "NIF no válido")
    if db.fila(con, "SELECT id FROM empresas WHERE nif=?", (n,)):
        raise HTTPException(409, "Ya existe una empresa con ese NIF")
    with db.transaccion(con):
        eid = crear_empresa(con, s["usuario_id"], n, rs, body.get("domicilio"))
    _anotar_miembros(con, eid)
    return _empresa_out(con, eid, s["usuario_id"])


CAMPOS_EMPRESA = ("razon_social", "domicilio", "tipo_contribuyente", "territorio", "sii", "canarias_ceuta_melilla",
                  "otras_exclusiones", "origen_pdf", "origen_pdf_detalle")


@router.put("/empresas/{eid}")
def empresa_put(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    cambios = {k: body[k] for k in CAMPOS_EMPRESA if k in body}
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
    """Alta de un usuario en la empresa (solo administradores). Si el usuario existe, se le da acceso."""
    rol(con, s, eid, "admin")
    if body.get("rol") not in ("admin", "emisor", "lector"):
        raise HTTPException(422, "Rol no válido")
    try:
        with db.transaccion(con):
            u = db.fila(con, "SELECT id FROM usuarios WHERE email=?", (body.get("email", "").strip(),))
            uid = u["id"] if u else seguridad.crear_usuario(con, body["email"], body.get("nombre", ""), body["clave"])
            con.execute("INSERT OR REPLACE INTO miembros (usuario_id, empresa_id, rol, relacion) VALUES (?,?,?,?)",
                        (uid, eid, body["rol"], body.get("relacion", "empleado_autorizado")))
            db.actividad(con, "miembro.alta", usuario_id=s["usuario_id"], empresa_id=eid, detalle={"usuario": uid, "rol": body["rol"]})
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    _anotar_miembros(con, eid)
    return {"usuario_id": uid}


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
    n = nifmod.normalizar(body.get("nif"))
    if not nifmod.es_valido(n) or not body.get("nombre"):
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
    for x in out:
        x["contadores"] = db.filas(con, "SELECT anio, siguiente FROM series_contadores WHERE serie_id=?", (x["id"],))
    return out


@router.post("/empresas/{eid}/series")
def series_new(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    if body.get("entorno") not in ("simulador", "pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    try:
        series.patron_a_regex(body.get("patron", ""))
    except ValueError as e:
        raise HTTPException(422, str(e))
    n = int(body.get("siguiente_numero") or 1)
    if n < 1:
        raise HTTPException(422, "El siguiente número debe ser ≥ 1")
    with db.transaccion(con):
        cur = con.execute("""INSERT INTO series (empresa_id, entorno, codigo, patron, descripcion, tipo, siguiente_numero)
                             VALUES (?,?,?,?,?,?,?)""", (eid, body["entorno"], body.get("codigo", "").strip() or body["patron"],
                                                        body["patron"], body.get("descripcion"), body.get("tipo", "ordinaria"), n))
        db.actividad(con, "serie.creada", usuario_id=s["usuario_id"], empresa_id=eid, objeto="serie", objeto_id=cur.lastrowid, detalle=body)
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
    lista = [int(x) for x in ids.split(",") if x.strip().isdigit()][:500]
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
    try:
        return {"borrador_id": emision.borrador_rectificativa(con, factura_id=fid, usuario_id=s["usuario_id"],
                                                              tipo=body.get("tipo", "R1"), motivo=(body.get("motivo") or "")[:300])}
    except emision.ErrorEmision as e:
        raise HTTPException(409, str(e))


@router.post("/facturas/{fid}/anular")
def factura_anular(fid: int, body: dict, request: Request, s=Depends(sesion), con=Depends(get_con)):
    _factura_ctx(con, s, fid, "admin")
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


# --- Remisión, certificados, producción ----------------------------------------------------------------------------
@router.get("/empresas/{eid}/remision")
def remision_estado(eid: int, entorno: str = "simulador", s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    out = remision.estado_cola(con, eid, entorno)
    if entorno == "simulador":
        out["simulador"] = simulador.modo_actual()
    return out


@router.post("/simulador/modo")
def simulador_modo(body: dict, s=Depends(sesion), con=Depends(get_con)):
    exigir_superadmin(con, s)
    try:
        simulador.fijar_modo(body.get("modo", "normal"), int(body.get("veces", 1)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.actividad(con, "simulador.modo", usuario_id=s["usuario_id"], detalle=body)
    return simulador.modo_actual()


@router.post("/empresas/{eid}/certificados")
async def certificado_subir(eid: int, archivo: UploadFile = File(...), clave: str = Form(""), entorno: str = Form(...),
                            s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    if entorno not in ("pruebas", "produccion"):
        raise HTTPException(422, "Entorno no válido")
    datos = await archivo.read(2_000_001)
    if len(datos) > 2_000_000:
        raise HTTPException(422, "El archivo del certificado supera 2 MB")
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    try:
        with db.transaccion(con):
            cid = certificados.guardar(con, empresa=empresa, entorno=entorno, p12=datos, clave=clave, usuario_id=s["usuario_id"])
            db.actividad(con, "certificado.cargado", usuario_id=s["usuario_id"], empresa_id=eid, objeto="certificado", objeto_id=cid,
                         detalle={"entorno": entorno})
    except certificados.CertificadoNoValido as e:
        raise HTTPException(422, str(e))
    return _cert_out(db.fila(con, "SELECT * FROM certificados WHERE id=?", (cid,)))


def _cert_out(c):
    c = dict(c)
    c.pop("blob_cifrado", None)
    c["legitimacion"] = json.loads(c.get("legitimacion") or "{}")
    return c


@router.get("/empresas/{eid}/certificados")
def certificados_list(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    return [_cert_out(c) for c in db.filas(con, "SELECT * FROM certificados WHERE empresa_id=? ORDER BY id DESC", (eid,))]


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


@router.post("/empresas/{eid}/legitimacion")
def legitimacion_declarar(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    if body.get("metodo") not in ("apoderamiento_aeat", "colaborador_social"):
        raise HTTPException(422, "Método no válido")
    did = body.get("evidencia_documento_id")
    if did:
        d = db.fila(con, "SELECT empresa_id FROM documentos WHERE id=?", (did,))
        if not d or d["empresa_id"] != eid:
            raise HTTPException(404, "Documento no encontrado")
    with db.transaccion(con):
        con.execute("""INSERT INTO legitimaciones (empresa_id, metodo, evidencia_documento_id, declarado_por, declarado_en, notas)
                       VALUES (?,?,?,?,?,?)""", (eid, body["metodo"], did, s["usuario_id"], db.ahora_iso(), body.get("notas")))
        db.actividad(con, "legitimacion.declarada", usuario_id=s["usuario_id"], empresa_id=eid, detalle={"metodo": body["metodo"]})
    return {"ok": True}


@router.get("/empresas/{eid}/actividad")
def actividad(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    return db.filas(con, """SELECT a.id, a.en, u.nombre usuario, a.accion, a.objeto, a.objeto_id, a.detalle FROM actividad a
                           LEFT JOIN usuarios u ON u.id=a.usuario_id WHERE a.empresa_id=? ORDER BY a.id DESC LIMIT 500""", (eid,))


@router.get("/declaracion")
def declaracion(s=Depends(sesion), con=Depends(get_con)):
    from .config import BASE_DIR
    d = db.fila(con, "SELECT * FROM declaraciones WHERE version_sif=?", (APP_VERSION,))
    borrador = (BASE_DIR.parent / "docs" / "declaracion-responsable-borrador.md")
    return {"version": APP_VERSION, "estado": d["estado"] if d else "borrador",
            "formalizada_por": d["formalizada_por"] if d else None, "formalizada_en": d["formalizada_en"] if d else None,
            "contenido": d["contenido"] if d else (borrador.read_text(encoding="utf-8") if borrador.exists() else "")}
