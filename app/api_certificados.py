"""API de «Mis certificados», asignación a empresas, certificado en uso y legitimaciones de terceros.

Reglas de acceso:
- «Mis certificados» (`/api/yo/certificados…`): solo el propietario los ve, asigna, retira, elimina o consulta sus usos;
  a cualquier otro usuario un certificado ajeno le responde 404 (no se revela que exista).
- Asignar exige además ser administrador de la empresa destino. Usarlo exige que el propietario lo siga siendo (se
  comprueba en cada envío, en `certificados.evaluar`).
- Cualquier administrador de la empresa puede dejar de usar en ella un certificado asignado (aunque no sea suyo): solo
  retira la asignación de esa empresa; queda en la actividad, en los usos del propietario y en el diario.
- Ni el archivo ni la contraseña se devuelven nunca, ni se escriben en la actividad o en el diario.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from . import certificados, db
from . import nif as nifmod
from .api import _texto_xml, get_con, rol, sesion

router = APIRouter(prefix="/api")
MAX_CERT = 2_000_000
MAX_ID = 2**63 - 1   # mayor entero de SQLite


def _identificador(v, mensaje: str) -> int:
    """Identificador de un objeto enviado en el cuerpo: entero positivo (o texto de cifras ASCII) en el rango de SQLite.
    Un texto con otras «cifras» («²», «①», «３», «٣») no es un identificador: 422."""
    if isinstance(v, str) and re.fullmatch(r"[0-9]{1,19}", v.strip()):
        v = int(v.strip())
    if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= MAX_ID:
        raise HTTPException(422, mensaje)
    return v


def _texto_opcional(v, campo: str) -> str | None:
    if v is not None and not isinstance(v, str):
        raise HTTPException(422, f"{campo}: debe ser un texto")
    return v


def _cambios_anotados(con, cambios) -> None:
    certificados.anotar(con, cambios)


def _registrar_retiradas(con, s, ids, motivo: str) -> None:
    """Cada asignación retirada (también al sustituirla) queda en la actividad de la empresa y en los usos del certificado."""
    for i in ids:
        a = db.fila(con, "SELECT * FROM certificado_asignaciones WHERE id=?", (i,))
        db.actividad(con, "certificado.asignacion_retirada", usuario_id=s["usuario_id"], empresa_id=a["empresa_id"],
                     objeto="certificado_usuario", objeto_id=a["certificado_usuario_id"],
                     detalle={"entorno": a["entorno"], "asignacion_id": i, "motivo": motivo})


# --- «Mis certificados» -----------------------------------------------------------------------------------------------
@router.get("/yo/certificados")
def mis_certificados(s=Depends(sesion), con=Depends(get_con)):
    return certificados.mis_certificados(con, s["usuario_id"])


@router.post("/yo/certificados")
async def mis_certificados_subir(archivo: UploadFile = File(...), clave: str = Form(""), alias: str = Form(""),
                                 s=Depends(sesion), con=Depends(get_con)):
    datos = await archivo.read(MAX_CERT + 1)
    if len(datos) > MAX_CERT:
        raise HTTPException(422, "El archivo del certificado supera 2 MB")
    try:
        with db.transaccion(con):
            cid, recargado = certificados.guardar_usuario(con, usuario_id=s["usuario_id"], p12=datos, clave=clave, alias=alias)
            cu = db.fila(con, "SELECT titular_nif, representado_nif, tipo, valido_hasta FROM certificados_usuario WHERE id=?", (cid,))
            db.actividad(con, "certificado_usuario.recargado" if recargado else "certificado_usuario.cargado",
                         usuario_id=s["usuario_id"], objeto="certificado_usuario", objeto_id=cid, detalle=cu)
    except certificados.CertificadoDuplicado as e:
        raise HTTPException(409, str(e))
    except certificados.CertificadoNoValido as e:
        raise HTTPException(422, str(e))
    _cambios_anotados(con, [("certificados_usuario", cid)])
    return next(c for c in certificados.mis_certificados(con, s["usuario_id"]) if c["id"] == cid)


@router.delete("/yo/certificados/{cid}")
def mis_certificados_eliminar(cid: int, s=Depends(sesion), con=Depends(get_con)):
    con.execute("PRAGMA secure_delete=ON")  # el archivo cifrado se sobrescribe en la base, no solo se desenlaza
    try:
        with db.transaccion(con):
            cambios = certificados.eliminar_usuario(con, usuario_id=s["usuario_id"], cid=cid)
            asignadas = [i for t, i in cambios if t == "certificado_asignaciones"]
            db.actividad(con, "certificado_usuario.eliminado", usuario_id=s["usuario_id"], objeto="certificado_usuario",
                         objeto_id=cid, detalle={"asignaciones_retiradas": len(asignadas)})
            _registrar_retiradas(con, s, asignadas, "certificado eliminado por su propietario")
    except LookupError:
        raise HTTPException(404, "No encontrado")
    _cambios_anotados(con, cambios)
    return {"ok": True, "asignaciones_retiradas": len(asignadas)}


@router.delete("/yo/certificados/{cid}/asignaciones/{aid}")
def mis_certificados_retirar(cid: int, aid: int, s=Depends(sesion), con=Depends(get_con)):
    """El propietario retira su certificado de una empresa (aunque ya no sea administrador de ella)."""
    try:
        with db.transaccion(con):
            certificados.propio_de(con, s["usuario_id"], cid)
            a = db.fila(con, "SELECT * FROM certificado_asignaciones WHERE id=? AND certificado_usuario_id=? AND activo=1", (aid, cid))
            if not a:
                raise LookupError
            certificados.retirar(con, usuario_id=s["usuario_id"], motivo="retirada por el propietario", asignacion_id=aid)
            _registrar_retiradas(con, s, [aid], "retirada por el propietario")
    except LookupError:
        raise HTTPException(404, "No encontrado")
    _cambios_anotados(con, [("certificado_asignaciones", aid)])
    return {"ok": True}


@router.get("/yo/certificados/{cid}/usos")
def mis_certificados_usos(cid: int, s=Depends(sesion), con=Depends(get_con)):
    """Auditoría del certificado para su propietario: asignaciones, retiradas y cada uso en un envío."""
    try:
        certificados.propio_de(con, s["usuario_id"], cid)
    except LookupError:
        raise HTTPException(404, "No encontrado")
    return db.filas(con, """SELECT a.id, a.en, a.accion, a.detalle, e.id empresa_id, e.nif empresa_nif, e.razon_social empresa_razon
                           FROM actividad a LEFT JOIN empresas e ON e.id=a.empresa_id
                           WHERE a.objeto='certificado_usuario' AND a.objeto_id=? ORDER BY a.id DESC LIMIT 300""", (cid,))


# --- Certificados de la empresa ---------------------------------------------------------------------------------------
@router.post("/empresas/{eid}/certificados")
async def certificado_subir(eid: int, archivo: UploadFile = File(...), clave: str = Form(""), entorno: str = Form(...),
                            s=Depends(sesion), con=Depends(get_con)):
    """Carga un certificado para la empresa y entorno (lo deja en uso; retira la asignación de «Mis certificados»)."""
    rol(con, s, eid, "admin")
    if entorno not in certificados.ENTORNOS_CERT:
        raise HTTPException(422, "Entorno no válido")
    datos = await archivo.read(MAX_CERT + 1)
    if len(datos) > MAX_CERT:
        raise HTTPException(422, "El archivo del certificado supera 2 MB")
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    retiradas: list[int] = []
    cambios: list[tuple[str, int]] = []
    try:
        with db.transaccion(con):
            cid = certificados.guardar(con, empresa=empresa, entorno=entorno, p12=datos, clave=clave, usuario_id=s["usuario_id"],
                                       retiradas=retiradas, cambios=cambios)
            _registrar_retiradas(con, s, retiradas, "sustituida por un certificado de la empresa")
            db.actividad(con, "certificado.cargado", usuario_id=s["usuario_id"], empresa_id=eid, objeto="certificado", objeto_id=cid,
                         detalle={"entorno": entorno, "asignaciones_retiradas": retiradas})
    except certificados.CertificadoNoValido as e:
        raise HTTPException(422, str(e))
    _cambios_anotados(con, [("certificado_asignaciones", i) for i in retiradas] + cambios)
    return _cert_out(con, db.fila(con, "SELECT * FROM certificados WHERE id=?", (cid,)), empresa)


def _cert_out(con, c: dict, empresa: dict, en_uso: dict | None = None) -> dict:
    """Certificado cargado por la empresa, con su legitimación evaluada ahora (no la del momento de la carga)."""
    c = dict(c)
    c.pop("blob_cifrado", None)
    leg = certificados.evaluar(con, certificados._vista_empresa(c), empresa)
    c["legitimacion"] = leg
    c["titular"] = leg["titular"]
    c["origen"] = "empresa"
    u = (en_uso or {}).get(c["entorno"]) or {}
    c["en_uso"] = u.get("origen") == "empresa" and u.get("id") == c["id"]
    return c


@router.get("/empresas/{eid}/certificados")
def certificados_list(eid: int, s=Depends(sesion), con=Depends(get_con)):
    """Certificados de la empresa y asignaciones de «Mis certificados» (origen, titular, legitimación y si está en uso)."""
    rol(con, s, eid)
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (eid,))
    en_uso = {ent: certificados.seleccionado(con, eid, ent) for ent in certificados.ENTORNOS_ASIGNACION}
    out = [_cert_out(con, c, empresa, en_uso) for c in db.filas(con, "SELECT * FROM certificados WHERE empresa_id=? ORDER BY id DESC", (eid,))]
    for a in db.filas(con, "SELECT * FROM certificado_asignaciones WHERE empresa_id=? ORDER BY activo DESC, id DESC LIMIT 50", (eid,)):
        cu = db.fila(con, "SELECT * FROM certificados_usuario WHERE id=?", (a["certificado_usuario_id"],))
        if not cu:
            continue
        v = certificados._vista_asignacion(a, cu)
        v["legitimacion_dict"] = certificados.evaluar(con, v, empresa)
        r = certificados.resumen(v, s["usuario_id"])
        u = en_uso.get(a["entorno"]) or {}
        r.update(id=None, activo=a["activo"], asignado_en=a["asignado_en"], retirado_en=a["retirado_en"],
                 motivo_retirada=a["motivo_retirada"], en_uso=u.get("asignacion_id") == a["id"])
        out.append(r)
    return out


# --- Certificado en uso por la empresa --------------------------------------------------------------------------------
@router.post("/empresas/{eid}/certificados/asignar")
def certificado_asignar(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """Asigna un certificado de «Mis certificados» (solo su propietario, y solo si administra la empresa)."""
    rol(con, s, eid, "admin")
    entorno = body.get("entorno")
    if entorno not in certificados.ENTORNOS_ASIGNACION:
        raise HTTPException(422, "Entorno no válido")
    cid = _identificador(body.get("certificado_id"), "Indique el certificado")
    try:
        with db.transaccion(con):
            aid, cambios = certificados.asignar(con, usuario_id=s["usuario_id"], cid=cid, empresa_id=eid, entorno=entorno)
            _registrar_retiradas(con, s, [i for _, i in cambios if i != aid], "sustituida por otro certificado")
            if cambios:
                v = certificados.seleccionado(con, eid, entorno)
                leg = v["legitimacion_dict"]
                db.actividad(con, "certificado.asignado", usuario_id=s["usuario_id"], empresa_id=eid, objeto="certificado_usuario",
                             objeto_id=cid, detalle={"entorno": entorno, "asignacion_id": aid, "propio": leg["propio"],
                                                     "titular_nif": leg["titular"]["nif"]})
    except LookupError:
        raise HTTPException(404, "No encontrado")
    except certificados.CertificadoNoUtilizable as e:
        raise HTTPException(409, str(e))
    _cambios_anotados(con, cambios)
    return certificados.resumen(certificados.seleccionado(con, eid, entorno), s["usuario_id"])


@router.post("/empresas/{eid}/certificados/{cert_id}/activar")
def certificado_activar(eid: int, cert_id: int, s=Depends(sesion), con=Depends(get_con)):
    """Vuelve a usar un certificado cargado para la empresa (sustituye la asignación de «Mis certificados»)."""
    rol(con, s, eid, "admin")
    try:
        with db.transaccion(con):
            retiradas, cambios = certificados.activar_empresa(con, empresa_id=eid, certificado_id=cert_id,
                                                              usuario_id=s["usuario_id"])
            _registrar_retiradas(con, s, retiradas, "sustituida por un certificado de la empresa")
            c = db.fila(con, "SELECT entorno FROM certificados WHERE id=?", (cert_id,))
            db.actividad(con, "certificado.activado", usuario_id=s["usuario_id"], empresa_id=eid, objeto="certificado",
                         objeto_id=cert_id, detalle={"entorno": c["entorno"], "asignaciones_retiradas": retiradas})
    except LookupError:
        raise HTTPException(404, "No encontrado")
    _cambios_anotados(con, [("certificado_asignaciones", i) for i in retiradas] + cambios)
    return certificados.resumen(certificados.seleccionado(con, eid, c["entorno"]), s["usuario_id"])


@router.delete("/empresas/{eid}/certificados/asignaciones/{aid}")
def certificado_dejar_de_usar(eid: int, aid: int, s=Depends(sesion), con=Depends(get_con)):
    """Un administrador de la empresa deja de usar en ella un certificado de «Mis certificados» (suyo o de otro usuario,
    p. ej. de un propietario que ya no administra la empresa). Solo retira esa asignación: el certificado sigue siendo
    de su propietario, que lo ve en «Ver usos»."""
    rol(con, s, eid, "admin")
    with db.transaccion(con):
        a = db.fila(con, "SELECT * FROM certificado_asignaciones WHERE id=? AND empresa_id=? AND activo=1", (aid, eid))
        if not a:
            raise HTTPException(404, "No encontrado")
        motivo = "retirada por un administrador de la empresa"
        certificados.retirar(con, usuario_id=s["usuario_id"], motivo=motivo, asignacion_id=aid)
        _registrar_retiradas(con, s, [aid], motivo)
    _cambios_anotados(con, [("certificado_asignaciones", aid)])
    return {"ok": True, "entorno": a["entorno"],
            "en_uso": certificados.resumen(certificados.seleccionado(con, eid, a["entorno"]), s["usuario_id"])}


@router.get("/empresas/{eid}/certificados/seleccion")
def certificado_seleccion(eid: int, s=Depends(sesion), con=Depends(get_con)):
    """Certificado que se usará en cada entorno: origen, titular, vigencia, propio o representante y legitimación."""
    rol(con, s, eid)
    return {ent: certificados.resumen(certificados.seleccionado(con, eid, ent), s["usuario_id"])
            for ent in certificados.ENTORNOS_ASIGNACION}


# --- Legitimaciones de terceros ---------------------------------------------------------------------------------------
def declarar_legitimacion(con, s, eid: int, body: dict) -> int:
    """Alta de una legitimación para el titular de un certificado de un tercero (lo usa POST /empresas/{eid}/legitimacion)."""
    if body.get("metodo") not in certificados.METODOS_TERCERO:
        raise HTTPException(422, "Método no válido")
    nif_t = nifmod.normalizar(_texto_opcional(body.get("titular_nif"), "NIF del titular"))
    if not nifmod.es_valido(nif_t):
        raise HTTPException(422, "Indique el NIF válido del titular del certificado (quien presenta en nombre de la empresa)")
    nombre = certificados.limpio(_texto_xml(_texto_opcional(body.get("titular_nombre"), "Nombre o razón social del titular"),
                                            "Nombre o razón social del titular", 120))
    did = _identificador(body.get("evidencia_documento_id"),
                         "Indique el documento que acredita el apoderamiento o la colaboración social")
    d = db.fila(con, "SELECT empresa_id FROM documentos WHERE id=?", (did,))
    if not d or d["empresa_id"] != eid:
        raise HTTPException(404, "Documento no encontrado")
    notas = (_texto_opcional(body.get("notas"), "Notas") or "")[:500] or None
    with db.transaccion(con):
        lid = con.execute("""INSERT INTO legitimaciones (empresa_id, metodo, evidencia_documento_id, declarado_por, declarado_en, notas,
                             titular_nif, titular_nombre, version) VALUES (?,?,?,?,?,?,?,?,1)""",
                          (eid, body["metodo"], did, s["usuario_id"], db.ahora_iso(), notas, nif_t, nombre)).lastrowid
        db.actividad(con, "legitimacion.declarada", usuario_id=s["usuario_id"], empresa_id=eid, objeto="legitimacion", objeto_id=lid,
                     detalle={"metodo": body["metodo"], "titular_nif": nif_t, "titular_nombre": nombre})
    _cambios_anotados(con, [("legitimaciones", lid)])
    return lid


@router.post("/empresas/{eid}/legitimacion")
def legitimacion_declarar(eid: int, body: dict, s=Depends(sesion), con=Depends(get_con)):
    """{metodo, titular_nif, titular_nombre, evidencia_documento_id, notas}: el titular del certificado de un tercero que
    presentará en nombre de la empresa (su NIF y nombre irán en Cabecera/Representante)."""
    rol(con, s, eid, "admin")
    return {"ok": True, "id": declarar_legitimacion(con, s, eid, body)}


@router.get("/empresas/{eid}/legitimaciones")
def legitimaciones_list(eid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid)
    # La evidencia solo se muestra si es un documento de ESTA empresa (nunca el nombre del de otra)
    return db.filas(con, """SELECT l.id, l.metodo, l.titular_nif, l.titular_nombre, d.id evidencia_documento_id,
                           d.nombre_archivo evidencia, l.declarado_en, u.nombre declarado_por, l.notas, l.revocada_en
                           FROM legitimaciones l LEFT JOIN documentos d ON d.id=l.evidencia_documento_id AND d.empresa_id=l.empresa_id
                           LEFT JOIN usuarios u ON u.id=l.declarado_por WHERE l.empresa_id=? ORDER BY l.id DESC""", (eid,))


@router.delete("/empresas/{eid}/legitimaciones/{lid}")
def legitimacion_revocar(eid: int, lid: int, s=Depends(sesion), con=Depends(get_con)):
    rol(con, s, eid, "admin")
    with db.transaccion(con):
        cur = con.execute("""UPDATE legitimaciones SET revocada_en=?, revocada_por=?, version=version+1
                             WHERE id=? AND empresa_id=? AND revocada_en IS NULL""", (db.ahora_iso(), s["usuario_id"], lid, eid))
        if cur.rowcount != 1:
            raise HTTPException(404, "No encontrado")
        db.actividad(con, "legitimacion.revocada", usuario_id=s["usuario_id"], empresa_id=eid, objeto="legitimacion", objeto_id=lid)
    _cambios_anotados(con, [("legitimaciones", lid)])
    return {"ok": True}
