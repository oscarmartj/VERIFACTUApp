"""Expedición de facturas y generación de registros de facturación (alta, anulación, subsanación).

Una única transacción (BEGIN IMMEDIATE) coordina: comprobación de duplicados e idempotencia,
consumo del número, versión inmutable de los datos aprobados, creación del registro encadenado y su
encolado para la remisión. PDF, XML, QR y huella se derivan de esa misma versión inmutable.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import datetime
from decimal import Decimal

from . import almacen, borradores, db, diario, pdf_final, produccion, series
from . import nif as nifmod
from .config import APP_NOMBRE, APP_VERSION
from .importes import fmt_xml, q2
from .verifactu import esquemas_xsd, huella as hmod, qr, xmlgen

NOMBRE_SIF = "FacturaPDF VF"
ID_SIF = "FV"


class ErrorEmision(Exception):
    def __init__(self, mensaje, incidencias=None):
        super().__init__(mensaje)
        self.incidencias = incidencias or []


def canonico(d: dict) -> str:
    return json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_txt(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


# --- Contexto -----------------------------------------------------------------------------
def cargar_empresa(con, empresa_id: int) -> dict:
    e = db.fila(con, "SELECT * FROM empresas WHERE id=?", (empresa_id,))
    e["ambito_dict"] = json.loads(e.get("ambito_resultado") or "{}")
    return e


def cargar_documento(con, documento_id: int | None) -> dict | None:
    if not documento_id:
        return None
    d = db.fila(con, "SELECT * FROM documentos WHERE id=?", (documento_id,))
    d["inspeccion_dict"] = json.loads(d.get("inspeccion") or "{}")
    return d


def extraccion_de(con, borrador: dict) -> dict | None:
    if not borrador.get("extraccion_id"):
        return None
    e = db.fila(con, "SELECT datos FROM extracciones WHERE id=?", (borrador["extraccion_id"],))
    return json.loads(e["datos"]) if e else None


def campos_cambiados(con, borrador_id: int) -> set[str]:
    return {r["campo"] for r in db.filas(con, "SELECT campo FROM cambios_borrador WHERE borrador_id=?", (borrador_id,))}


def obligados_del_usuario(con, empresa_id: int, usuario_id: int | None) -> int:
    """Obligados tributarios que gestiona el usuario del SaaS que genera el registro (FAQ desarrolladores n.º 4: en
    SaaS, IndicadorMultiplesOT se calcula por usuario, no por plataforma): las empresas de las que es miembro. Sin
    usuario, las que gestionan los miembros de la empresa."""
    if usuario_id is not None:
        n = db.fila(con, "SELECT COUNT(DISTINCT empresa_id) n FROM miembros WHERE usuario_id=?",
                    (usuario_id,))["n"]
    else:
        n = db.fila(con, """SELECT COUNT(DISTINCT m2.empresa_id) n FROM miembros m1 JOIN miembros m2 ON m2.usuario_id=m1.usuario_id
                            WHERE m1.empresa_id=?""", (empresa_id,))["n"]
    return max(n, 1)


def sistema_informatico(con, empresa_id: int, usuario_id: int | None = None) -> xmlgen.SistemaInformatico:
    p = produccion.productor(con) or {}
    n_empresas = obligados_del_usuario(con, empresa_id, usuario_id)
    emp = db.fila(con, "SELECT nif, razon_social FROM empresas WHERE id=?", (empresa_id,))
    return xmlgen.SistemaInformatico(
        nombre_razon=(p.get("nombre") or emp["razon_social"])[:120],
        nif=p.get("nif") or emp["nif"],
        nombre_sistema=NOMBRE_SIF,
        id_sistema=ID_SIF,
        version=APP_VERSION,
        numero_instalacion=instalacion(con, empresa_id)["numero_instalacion"],
        tipo_uso_solo_verifactu="S",
        tipo_uso_multi_ot="S",
        indicador_multiples_ot="S" if n_empresas > 1 else "N",
    )


def instalacion(con, empresa_id: int) -> dict:
    i = db.fila(con, "SELECT * FROM instalaciones WHERE empresa_id=?", (empresa_id,))
    if i:
        return i
    p = produccion.productor(con)
    # En un SIF multiobligado cada obligado tributario tiene su propia instalación y cadena (art. 2 OM)
    con.execute("INSERT INTO instalaciones (empresa_id, numero_instalacion, creada_en) VALUES (?,?,?)",
                (empresa_id, f"{p['numero_instalacion']}-OT{empresa_id:04d}", db.ahora_iso()))
    return db.fila(con, "SELECT * FROM instalaciones WHERE empresa_id=?", (empresa_id,))


def validar_borrador(con, borrador: dict, entorno: str) -> tuple[list[dict], dict | None, dict]:
    empresa = cargar_empresa(con, borrador["empresa_id"])
    documento = cargar_documento(con, borrador.get("documento_id"))
    if documento and entorno == "simulador":
        documento = dict(documento, bloqueo_emision=None)  # la factura de referencia solo puede simularse localmente
    datos = json.loads(borrador["datos"])
    hoy = db.ahora().date()
    es_rect = bool(borrador.get("rectifica_factura_id"))
    ns = datos.get("num_serie")  # un valor guardado no textual lo notifica validar() como DATO_NO_VALIDO
    serie = series.identificar(con, empresa["id"], entorno, ns if isinstance(ns, str) else "", hoy.year,
                               "rectificativa" if es_rect else "ordinaria")
    inc = borradores.validar(con, empresa, documento if not es_rect else None, datos,
                             extraccion=extraccion_de(con, borrador), serie=serie, es_rectificativa=es_rect,
                             cambiados=campos_cambiados(con, borrador["id"]))
    if serie and not serie["anio_correcto"]:
        inc.append({"nivel": "bloqueo", "campo": "num_serie", "codigo": "ANIO_SERIE",
                    "mensaje": f"El año del número ({serie['anio_detectado']}) no es el de expedición ({hoy.year})."})
    if serie is None and isinstance(ns, str) and ns.strip():
        # serie con ceros de relleno ({n:3}): el número sin el ancho de la serie no la identifica; se indica cómo escribirlo
        fmt = series.formato_esperado(con, empresa["id"], entorno, ns, hoy.year, "rectificativa" if es_rect else "ordinaria")
        if fmt:
            inc.append({"nivel": "aviso", "campo": "num_serie", "codigo": "SERIE_FORMATO",
                        "mensaje": f"La serie «{fmt['codigo']}» ({fmt['patron']}) escribe este número como «{fmt['esperado']}»: "
                                   "corrija el número de factura si corresponde a esa serie."})
    if es_rect and not datos.get("rectificativa"):
        inc.append({"nivel": "bloqueo", "campo": "rectificativa", "codigo": "RECT_DATOS", "mensaje": "Faltan datos de la rectificación."})
    est = estado_cadena_aeat(con, empresa["id"], entorno)
    if est and est["estado"] == "atrasada":
        from .consulta import mensaje_cadena_atrasada
        inc.append({"nivel": "bloqueo", "campo": "num_serie", "codigo": "CADENA_ATRASADA",
                    "mensaje": mensaje_cadena_atrasada(est, entorno)})
    if es_rect:
        orig = db.fila(con, "SELECT entorno, estado, num_serie FROM facturas WHERE id=?", (borrador["rectifica_factura_id"],))
        if orig["entorno"] != entorno:
            inc.append({"nivel": "bloqueo", "campo": "rectificativa", "codigo": "RECT_ENTORNO",
                        "mensaje": f"La factura rectificada {orig['num_serie']} es del entorno «{orig['entorno']}»: la rectificativa "
                                   f"debe expedirse en ese mismo entorno."})
        if orig["estado"] not in ("emitida", "rectificada"):
            inc.append({"nivel": "bloqueo", "campo": "rectificativa", "codigo": "RECT_ORIGINAL_ESTADO",
                        "mensaje": f"La factura {orig['num_serie']} está {orig['estado']}: no puede rectificarse."})
    return inc, serie, empresa


# --- Datos aprobados (versión inmutable) -----------------------------------------------------------
def datos_aprobados(borrador: dict, empresa: dict, entorno: str, fecha_exp: str, serie: dict, documento: dict | None) -> dict:
    d = copy.deepcopy(json.loads(borrador["datos"]))
    d["fecha_expedicion"] = fecha_exp
    if d.get("fecha_operacion") == fecha_exp:
        d["fecha_operacion"] = None
    d["entorno"] = entorno
    d["emisor"]["nif"] = nifmod.normalizar(empresa["nif"])
    d["emisor"]["nombre"] = empresa["razon_social"]
    d["destinatario"]["nif"] = nifmod.normalizar(d["destinatario"]["nif"])
    for k in ("base_imponible", "cuota_total", "importe_total", "total_a_pagar"):
        if d.get(k) is not None:
            d[k] = fmt_xml(d[k])
    for x in d.get("desglose") or []:
        if borradores.es_exenta(x):
            # base exenta: sin tipo ni cuota; la clave de régimen se deriva de la causa (02 para E2 y E3)
            x.update(tipo_iva=None, cuota=None, calificacion=None, base=fmt_xml(x["base"]), impuesto="01",
                     clave_regimen=borradores.clave_regimen_exenta(x["operacion_exenta"]),
                     motivo_exencion=borradores.normalizar_mencion(x["motivo_exencion"]))
            continue
        x["tipo_iva"] = fmt_xml(x["tipo_iva"])
        x["base"] = fmt_xml(x["base"])
        x["cuota"] = fmt_xml(x["cuota"])
        if not x.get("calificacion"):
            x["calificacion"] = "S1"
        x.setdefault("clave_regimen", "01")
        x.setdefault("impuesto", "01")
        for k in ("operacion_exenta", "motivo_exencion"):
            x.pop(k, None)   # una fila sujeta no lleva datos de exención (p. ej. restos de un cambio de criterio)
    if not d.get("total_a_pagar"):
        d["total_a_pagar"] = d["importe_total"]
    d["numeracion"] = {"serie_id": serie["id"], "serie_codigo": serie["codigo"], "patron": serie["patron"],
                       "numero": serie["numero_detectado"], "anio": serie["anio_contador"]}
    d["origen"] = ({"documento_id": documento["id"], "sha256": documento["sha256"], "nombre_archivo": documento["nombre_archivo"]}
                   if documento else None)
    d["sistema"] = {"nombre": APP_NOMBRE, "version": APP_VERSION}
    d.pop("indicios", None)
    return d


# --- Registros -----------------------------------------------------------------------------------
def estado_cadena_aeat(con, empresa_id: int, entorno: str) -> dict | None:
    """Cadena «atrasada» respecto a la AEAT (no se generan registros) o «anclada» a su último registro (el siguiente
    registro lo usa como anterior). Ver consulta.reanudar_cadena."""
    return db.fila(con, "SELECT * FROM cadenas_aeat WHERE empresa_id=? AND entorno=?", (empresa_id, entorno))


def _cadena(con, empresa_id: int, entorno: str, inst_id: int) -> dict:
    c = db.fila(con, "SELECT * FROM cadenas WHERE empresa_id=? AND entorno=? AND instalacion_id=?", (empresa_id, entorno, inst_id))
    if not c:
        con.execute("INSERT INTO cadenas (empresa_id, entorno, instalacion_id, secuencia) VALUES (?,?,?,0)",
                    (empresa_id, entorno, inst_id))
        c = db.fila(con, "SELECT * FROM cadenas WHERE empresa_id=? AND entorno=? AND instalacion_id=?", (empresa_id, entorno, inst_id))
    return c


def _marca_temporal(cad: dict, ancla: dict | None = None) -> str:
    ahora = db.ahora()
    if cad.get("ultima_fecha_hora") and ahora < datetime.fromisoformat(cad["ultima_fecha_hora"]):
        raise ErrorEmision("El reloj del sistema es anterior al último registro generado. Revise la hora del servidor.")
    if ancla:
        # Registro de la AEAT desde el que se reanudó la cadena (FechaHoraHusoGenRegistro tal como la devolvió la consulta;
        # sin huso se entiende hora peninsular). El nuevo registro no puede ser anterior a él.
        try:
            t_ancla = datetime.fromisoformat(ancla["fecha_hora_huso"])
        except (TypeError, ValueError) as e:
            raise ErrorEmision("La fecha y hora del registro de la AEAT desde el que se reanudó la cadena no es válida: "
                               "vuelva a reanudar la cadena desde la consulta.") from e
        if t_ancla.tzinfo is None:
            t_ancla = t_ancla.replace(tzinfo=db.TZ)
        if ahora.replace(microsecond=0) < t_ancla:
            raise ErrorEmision("El reloj del sistema es anterior al último registro que tiene la AEAT, desde el que se reanudó "
                               "la cadena. Revise la hora del servidor.")
    return hmod.fecha_hora_huso(ahora)


def _insertar_registro(con, *, empresa: dict, entorno: str, factura_id: int, tipo: str, el, cadena_txt: str,
                       huella: str, huella_ant: str, fh: str, inst: dict, cad: dict, campos: dict,
                       ancla: dict | None = None) -> int:
    cab = {"nombre": empresa["razon_social"], "nif": nifmod.normalizar(empresa["nif"])}
    xml_txt = xmlgen.a_texto(el)
    errores = esquemas_xsd.validar(xmlgen.mensaje_remision(cab, [xml_txt]))
    if errores:
        raise ErrorEmision("El registro no cumple el esquema XSD: " + "; ".join(errores[:3]))
    sec = cad["secuencia"] + 1
    ahora = db.ahora_iso()
    cf = db.fila(con, "SELECT incidencia_desde FROM control_flujo WHERE empresa_id=? AND entorno=?", (empresa["id"], entorno))
    incidencia = 1 if cf and cf["incidencia_desde"] else 0
    ancla_txt = canonico({k: ancla[k] for k in ("id_emisor", "num_serie", "fecha_expedicion", "fecha_hora_huso", "huella")}) \
        if ancla else None
    cur = con.execute("""INSERT INTO registros (empresa_id, entorno, instalacion_id, secuencia, factura_id, tipo, subsanacion,
        rechazo_previo, sin_registro_previo, registro_corregido_id, id_emisor, num_serie, fecha_expedicion, tipo_factura,
        cuota_total, importe_total, huella_anterior, fecha_hora_huso, huella, cadena_huella, xml, xml_sha256,
        estado_envio, incidencia, siguiente_intento_en, creado_en, actualizado_en, ancla_aeat)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pendiente',?,?,?,?,?)""",
                      (empresa["id"], entorno, inst["id"], sec, factura_id, tipo, campos.get("subsanacion"),
                       campos.get("rechazo_previo"), campos.get("sin_registro_previo"), campos.get("registro_corregido_id"),
                       campos["id_emisor"], campos["num_serie"], campos["fecha_expedicion"], campos.get("tipo_factura"),
                       campos.get("cuota_total"), campos.get("importe_total"), huella_ant or None, fh, huella, cadena_txt,
                       xml_txt, sha256_txt(xml_txt), incidencia, db.utc_iso(), ahora, db.utc_iso(), ancla_txt))
    rid = cur.lastrowid
    if ancla:
        # El ancla se ha usado: la cadena local continúa desde este registro
        con.execute("DELETE FROM cadenas_aeat WHERE empresa_id=? AND entorno=? AND estado='anclada' AND huella=?",
                    (empresa["id"], entorno, ancla["huella"]))
        db.actividad(con, "cadena.ancla_usada", empresa_id=empresa["id"], objeto="registro", objeto_id=rid,
                     detalle={"entorno": entorno, "ancla": {k: ancla[k] for k in ("num_serie", "fecha_expedicion", "huella")}})
    con.execute("UPDATE cadenas SET ultimo_registro_id=?, secuencia=?, ultima_fecha_hora=? WHERE empresa_id=? AND entorno=? AND instalacion_id=?",
                (rid, sec, fh, empresa["id"], entorno, inst["id"]))
    return rid


def _anterior(con, cad: dict, entorno: str) -> tuple[dict | None, str, dict | None]:
    """Registro anterior de la cadena: el último registro local o, si la cadena se reanudó desde la AEAT, el último
    registro que tiene la AEAT (ancla). Con la cadena atrasada respecto a la AEAT no se genera ningún registro."""
    est = estado_cadena_aeat(con, cad["empresa_id"], entorno)
    if est and est["estado"] == "atrasada":
        from .consulta import mensaje_cadena_atrasada
        raise ErrorEmision(mensaje_cadena_atrasada(est, entorno))
    if est and est["estado"] == "anclada":
        return ({"id_emisor": est["id_emisor"], "num_serie": est["num_serie"], "fecha_expedicion": est["fecha_expedicion"],
                 "huella": est["huella"]}, est["huella"], est)
    prev = db.fila(con, "SELECT * FROM registros WHERE id=?", (cad["ultimo_registro_id"],)) if cad.get("ultimo_registro_id") else None
    if prev:
        return {"id_emisor": prev["id_emisor"], "num_serie": prev["num_serie"],
                "fecha_expedicion": prev["fecha_expedicion"], "huella": prev["huella"]}, prev["huella"], None
    return None, "", None


def crear_registro_alta(con, empresa: dict, entorno: str, factura: dict, datos: dict, *, subsanacion=None,
                        rechazo_previo=None, registro_corregido_id=None, usuario_id: int | None = None) -> int:
    inst = instalacion(con, empresa["id"])
    cad = _cadena(con, empresa["id"], entorno, inst["id"])
    enc, huella_ant, ancla = _anterior(con, cad, entorno)
    fh = _marca_temporal(cad, ancla)
    fecha = hmod.fecha_aeat(datos["fecha_expedicion"])
    campos = {"id_emisor": datos["emisor"]["nif"], "num_serie": datos["num_serie"], "fecha_expedicion": fecha,
              "tipo_factura": datos["tipo_factura"], "cuota_total": fmt_xml(datos["cuota_total"]),
              "importe_total": fmt_xml(datos["importe_total"]), "subsanacion": subsanacion,
              "rechazo_previo": rechazo_previo, "registro_corregido_id": registro_corregido_id}
    cadena_txt = hmod.cadena_alta(id_emisor=campos["id_emisor"], num_serie=campos["num_serie"], fecha_expedicion=fecha,
                                  tipo_factura=campos["tipo_factura"], cuota_total=campos["cuota_total"],
                                  importe_total=campos["importe_total"], huella_anterior=huella_ant, fecha_hora_huso=fh)
    h = hmod.sha256_mayus(cadena_txt)
    rect = datos.get("rectificativa") or {}
    alta = xmlgen.DatosAlta(
        id_emisor=campos["id_emisor"], nombre_razon_emisor=datos["emisor"]["nombre"][:120], num_serie=campos["num_serie"],
        fecha_expedicion=fecha, tipo_factura=campos["tipo_factura"],
        descripcion_operacion=datos["descripcion_operacion"][:500],
        desglose=[{"impuesto": x["impuesto"], "clave_regimen": x["clave_regimen"], "calificacion": x.get("calificacion"),
                   "operacion_exenta": x.get("operacion_exenta"), "tipo_impositivo": x.get("tipo_iva"), "base": x["base"],
                   "cuota": x.get("cuota")} for x in datos["desglose"]],
        cuota_total=campos["cuota_total"], importe_total=campos["importe_total"], fecha_hora_huso=fh, huella=h,
        encadenamiento=enc, sistema=sistema_informatico(con, empresa["id"], usuario_id),
        destinatarios=[{"nombre": datos["destinatario"]["nombre"][:120], "nif": datos["destinatario"]["nif"]}],
        fecha_operacion=hmod.fecha_aeat(datos["fecha_operacion"]) if datos.get("fecha_operacion") else None,
        tipo_rectificativa=rect.get("tipo_rectificativa"),
        facturas_rectificadas=[{"id_emisor": campos["id_emisor"], "num_serie": rect["num_serie_rectificada"],
                                "fecha_expedicion": hmod.fecha_aeat(rect["fecha_rectificada"])}] if rect else [],
        subsanacion=subsanacion, rechazo_previo=rechazo_previo,
    )
    return _insertar_registro(con, empresa=empresa, entorno=entorno, factura_id=factura["id"], tipo="alta",
                              el=xmlgen.registro_alta(alta), cadena_txt=cadena_txt, huella=h, huella_ant=huella_ant,
                              fh=fh, inst=inst, cad=cad, campos=campos, ancla=ancla)


def crear_registro_anulacion(con, empresa: dict, entorno: str, factura: dict, *, rechazo_previo=None, sin_registro_previo=None,
                             usuario_id: int | None = None) -> int:
    inst = instalacion(con, empresa["id"])
    cad = _cadena(con, empresa["id"], entorno, inst["id"])
    enc, huella_ant, ancla = _anterior(con, cad, entorno)
    fh = _marca_temporal(cad, ancla)
    fecha = hmod.fecha_aeat(factura["fecha_expedicion"])
    campos = {"id_emisor": factura["emisor_nif"], "num_serie": factura["num_serie"], "fecha_expedicion": fecha,
              "rechazo_previo": rechazo_previo, "sin_registro_previo": sin_registro_previo}
    cadena_txt = hmod.cadena_anulacion(id_emisor=campos["id_emisor"], num_serie=campos["num_serie"], fecha_expedicion=fecha,
                                       huella_anterior=huella_ant, fecha_hora_huso=fh)
    h = hmod.sha256_mayus(cadena_txt)
    an = xmlgen.DatosAnulacion(id_emisor=campos["id_emisor"], num_serie=campos["num_serie"], fecha_expedicion=fecha,
                               fecha_hora_huso=fh, huella=h, encadenamiento=enc,
                               sistema=sistema_informatico(con, empresa["id"], usuario_id),
                               rechazo_previo=rechazo_previo, sin_registro_previo=sin_registro_previo)
    return _insertar_registro(con, empresa=empresa, entorno=entorno, factura_id=factura["id"], tipo="anulacion",
                              el=xmlgen.registro_anulacion(an), cadena_txt=cadena_txt, huella=h, huella_ant=huella_ant,
                              fh=fh, inst=inst, cad=cad, campos=campos, ancla=ancla)


# --- Expedición -------------------------------------------------------------------------------------
def clave_guardada(empresa_id: int, clave: str) -> str:
    """La clave de idempotencia es de cada empresa: se guarda precedida del identificador de la empresa. La columna sigue
    siendo UNIQUE en toda la plataforma, pero la misma clave usada por otra empresa ni choca ni revela nada."""
    return f"{empresa_id}:{clave}"


def _factura_por_clave(con, empresa_id: int, clave: str, borrador_id: int | None = None) -> dict | None:
    """Factura de ESTA empresa con esa clave (o de ese borrador). Las facturas anteriores guardan la clave tal cual:
    también cuentan, pero solo dentro de su empresa."""
    sql = "SELECT id, borrador_id, entorno FROM facturas WHERE (empresa_id=? AND clave_idempotencia IN (?, ?))"
    p: list = [empresa_id, clave_guardada(empresa_id, clave), clave]
    if borrador_id is not None:
        sql += " OR borrador_id=?"
        p.append(borrador_id)
    return db.fila(con, sql, p)


def emitir(con, *, borrador_id: int, usuario_id: int, entorno: str, clave_idempotencia: str) -> int:
    if not clave_idempotencia or len(clave_idempotencia) > 100:
        raise ErrorEmision("Falta la clave de idempotencia")
    b = db.fila(con, "SELECT * FROM borradores WHERE id=?", (borrador_id,))
    if not b:
        raise ErrorEmision("Borrador inexistente")
    previa = _factura_por_clave(con, b["empresa_id"], clave_idempotencia)
    if previa:
        if previa["borrador_id"] != borrador_id:
            raise ErrorEmision("La clave de idempotencia ya se usó para otro borrador")
        if previa["entorno"] != entorno:
            raise ErrorEmision(f"Este borrador ya se expidió en el entorno «{previa['entorno']}»")
        _asegurar_diario(con, previa["id"])
        return previa["id"]
    if b.get("factura_id"):
        ya = db.fila(con, "SELECT entorno FROM facturas WHERE id=?", (b["factura_id"],))
        if ya and ya["entorno"] != entorno:
            raise ErrorEmision(f"Este borrador ya se expidió en el entorno «{ya['entorno']}»")
        _asegurar_diario(con, b["factura_id"])
        return b["factura_id"]
    if b["estado"] == "descartado":
        raise ErrorEmision("El borrador está descartado")
    empresa = cargar_empresa(con, b["empresa_id"])
    produccion.comprobar(con, empresa, usuario_id, entorno)
    inc, serie, _ = validar_borrador(con, b, entorno)
    if not borradores.puede_emitir(inc):
        raise ErrorEmision("El borrador tiene incidencias pendientes", inc)
    documento = cargar_documento(con, b.get("documento_id"))
    original = almacen.leer(documento["ruta"]) if documento else None
    fecha_exp = db.ahora().date().isoformat()
    datos = datos_aprobados(b, empresa, entorno, fecha_exp, serie, documento)
    url = qr.url_cotejo(entorno, nif=datos["emisor"]["nif"], num_serie=datos["num_serie"],
                        fecha_expedicion=hmod.fecha_aeat(fecha_exp), importe_total=datos["importe_total"])
    # PDF y verificación antes de tomar el bloqueo (no dependen de la cadena)
    ext = extraccion_de(con, b)
    qr_original = bool((documento or {}).get("inspeccion_dict", {}).get("qr"))
    pdf, info_pdf = pdf_final.generar(datos, modo="definitivo", entorno=entorno, url_qr=url, original=original,
                                      extraccion=ext, fecha_expedicion=fecha_exp,
                                      cambios=len(campos_cambiados(con, borrador_id)), qr_en_original=qr_original)
    ver = pdf_final.verificar(pdf, datos, url, fecha_exp)
    if not ver["ok"] and info_pdf["estrategia"] == "superposicion":
        # La superposición no superó la verificación: se compone la factura de nuevo
        pdf, info_pdf = pdf_final.generar(datos, modo="definitivo", entorno=entorno, url_qr=url, original=original,
                                          extraccion=ext, fecha_expedicion=fecha_exp, forzar_estrategia="reconstruccion")
        ver = pdf_final.verificar(pdf, datos, url, fecha_exp)
    if not ver["ok"]:
        raise ErrorEmision("El PDF generado no supera la verificación: " + json.dumps(ver["comprobaciones"]))
    datos["pdf"] = {"estrategia": info_pdf["estrategia"], "motivo": info_pdf.get("motivo"), "qr": info_pdf.get("qr")}
    canon = canonico(datos)
    with db.transaccion(con):
        previa = _factura_por_clave(con, empresa["id"], clave_idempotencia, borrador_id)
        if previa:
            if previa["borrador_id"] != borrador_id:
                raise ErrorEmision("La clave de idempotencia ya se usó para otro borrador")
            if previa["entorno"] != entorno:
                raise ErrorEmision(f"Este borrador ya se expidió en el entorno «{previa['entorno']}»")
            return previa["id"]
        # El borrador no puede haber cambiado (edición, descarte) desde que se validó y se generó el PDF
        actual = db.fila(con, "SELECT estado, datos, actualizado_en FROM borradores WHERE id=?", (borrador_id,))
        if actual["estado"] in ("descartado", "emitido") or actual["datos"] != b["datos"] or actual["actualizado_en"] != b["actualizado_en"]:
            raise ErrorEmision("El borrador ha cambiado mientras se preparaba la expedición; revíselo y vuelva a intentarlo")
        if db.ahora().date().isoformat() != fecha_exp:
            raise ErrorEmision("Ha cambiado la fecha durante la expedición; vuelva a intentarlo")
        serie_tx = series.identificar(con, empresa["id"], entorno, datos["num_serie"], int(fecha_exp[:4]),
                                      "rectificativa" if b.get("rectifica_factura_id") else "ordinaria")
        if not serie_tx or serie_tx["numero_detectado"] != serie_tx["siguiente_numero"]:
            raise ErrorEmision("La numeración ha cambiado (otra factura se expidió a la vez). Revise el número.")
        from .importacion import facturas_mismo_numero
        dup = facturas_mismo_numero(con, empresa["id"], datos["emisor"]["nif"], datos["num_serie"])
        if dup:
            raise ErrorEmision(f"Ya existe la factura {dup[0]['num_serie']}")
        ruta_pdf = f"empresa_{empresa['id']}/{entorno}/facturas/{sha256_txt(canon)[:16]}.pdf"
        try:
            cur = con.execute("""INSERT INTO facturas (empresa_id, entorno, borrador_id, documento_id, clave_idempotencia, serie_id,
                numero, num_serie, fecha_expedicion, tipo_factura, emisor_nif, destinatario_nif, destinatario_nombre, cuota_total,
                importe_total, datos, datos_sha256, url_qr, pdf_ruta, pdf_sha256, pdf_estrategia, estado, rectifica_factura_id,
                creada_por, creada_en) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'emitida',?,?,?)""",
                              (empresa["id"], entorno, borrador_id, b.get("documento_id"),
                               clave_guardada(empresa["id"], clave_idempotencia), serie["id"],
                               serie["numero_detectado"], datos["num_serie"], fecha_exp, datos["tipo_factura"], datos["emisor"]["nif"],
                               datos["destinatario"]["nif"], datos["destinatario"]["nombre"], datos["cuota_total"],
                               datos["importe_total"], canon, sha256_txt(canon), url, ruta_pdf, almacen.sha256(pdf),
                               info_pdf["estrategia"], b.get("rectifica_factura_id"), usuario_id, db.ahora_iso()))
        except sqlite3.IntegrityError:
            # Solo posible si una factura antigua de otra empresa guardó tal cual una clave con esta misma forma
            raise ErrorEmision("No se puede usar esta clave de idempotencia: genere otra y vuelva a intentarlo")
        fid = cur.lastrowid
        factura = db.fila(con, "SELECT * FROM facturas WHERE id=?", (fid,))
        rid = crear_registro_alta(con, empresa, entorno, factura, datos, usuario_id=usuario_id)
        series.consumir(con, serie_tx)
        con.execute("UPDATE borradores SET estado='emitido', factura_id=?, actualizado_en=? WHERE id=?", (fid, db.ahora_iso(), borrador_id))
        if b.get("rectifica_factura_id"):
            cur_r = con.execute("""UPDATE facturas SET estado='rectificada' WHERE id=? AND entorno=? AND empresa_id=?
                                   AND estado IN ('emitida','rectificada')""", (b["rectifica_factura_id"], entorno, empresa["id"]))
            if cur_r.rowcount != 1:
                raise ErrorEmision("La factura a rectificar no está vigente en este entorno (¿anulada o de otro entorno?)")
        almacen.guardar(ruta_pdf, pdf)
        db.actividad(con, "factura.expedida", usuario_id=usuario_id, empresa_id=empresa["id"], objeto="factura", objeto_id=fid,
                     detalle={"num_serie": datos["num_serie"], "entorno": entorno, "registro_id": rid,
                              "pdf": info_pdf["estrategia"], "verificacion_pdf": ver["comprobaciones"]})
    _asegurar_diario(con, fid)
    return fid


def _asegurar_diario(con, fid: int) -> None:
    """Anota la factura, sus registros y su PDF en el diario si aún no constan (idempotente y de mejor esfuerzo:
    la factura ya está confirmada; si el diario falla se registra y la conciliación del arranque lo completa)."""
    try:
        entradas = diario.leer()
        ids = {e["datos"]["id"] for e in entradas if e["tipo"] == "factura"}
        huellas = {e["datos"]["huella"] for e in entradas if e["tipo"] == "registro"}
        f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (fid,))
        if fid not in ids:
            diario.anotar_factura(con, fid)
        for r in db.filas(con, "SELECT id, huella FROM registros WHERE factura_id=?", (fid,)):
            if r["huella"] not in huellas:
                diario.anotar_registro(con, r["id"])
        if f.get("pdf_ruta") and almacen.existe(f["pdf_ruta"]):
            diario.guardar_binario(f["pdf_ruta"], almacen.leer(f["pdf_ruta"]))  # copia espejo del PDF definitivo
        if f.get("rectifica_factura_id"):
            orig = db.fila(con, "SELECT estado FROM facturas WHERE id=?", (f["rectifica_factura_id"],))
            diario.anotar("estado_factura", {"id": f["rectifica_factura_id"], "estado": orig["estado"]})
    except Exception as e:  # noqa: BLE001
        try:
            db.actividad(con, "diario.error", objeto="factura", objeto_id=fid, detalle={"error": type(e).__name__})
        except Exception:  # noqa: BLE001
            pass


PENDIENTES = ("pendiente", "enviando", "sin_confirmar", "bloqueado")


def existe_en_aeat(con, factura_id: int) -> bool:
    """¿Consta en la AEAT algún registro (alta o anulación) con la identificación de esta factura?
    Cuenta: altas o anulaciones aceptadas (con o sin errores) y rechazos por duplicado 3000 en los que la
    AEAT informó de un registro vigente (Correcta, AceptadaConErrores o Anulada)."""
    for r in db.filas(con, "SELECT estado_envio, codigo_error, duplicado FROM registros WHERE factura_id=?", (factura_id,)):
        if r["estado_envio"] in ("aceptado", "aceptado_con_errores"):
            return True
        if r["estado_envio"] == "rechazado" and r["codigo_error"] == "3000":
            dup = json.loads(r["duplicado"] or "{}")
            if dup.get("estado") in ("Correcta", "AceptadaConErrores", "Anulada"):
                return True
    return False


def recalcular_estado_factura(con, factura_id: int) -> str | None:
    """Deriva el estado de la factura de sus registros y de sus rectificativas vigentes.
    anulada: el último registro de anulación no fue rechazado; rectificada: tiene alguna rectificativa no anulada;
    emitida: en otro caso. Devuelve el nuevo estado si ha cambiado."""
    f = db.fila(con, "SELECT estado FROM facturas WHERE id=?", (factura_id,))
    ult = db.fila(con, "SELECT tipo, estado_envio FROM registros WHERE factura_id=? ORDER BY secuencia DESC LIMIT 1", (factura_id,))
    an = db.fila(con, "SELECT estado_envio FROM registros WHERE factura_id=? AND tipo='anulacion' ORDER BY secuencia DESC LIMIT 1", (factura_id,))
    if an and an["estado_envio"] != "rechazado" and ult and ult["tipo"] == "anulacion":
        nuevo = "anulada"
    elif db.fila(con, "SELECT 1 FROM facturas WHERE rectifica_factura_id=? AND estado<>'anulada'", (factura_id,)):
        nuevo = "rectificada"
    else:
        nuevo = "emitida"
    if nuevo != f["estado"]:
        con.execute("UPDATE facturas SET estado=? WHERE id=?", (nuevo, factura_id))
        return nuevo
    return None


def anular(con, *, factura_id: int, usuario_id: int, motivo: str) -> int:
    with db.transaccion(con):
        f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (factura_id,))
        ult_an = db.fila(con, "SELECT * FROM registros WHERE factura_id=? AND tipo='anulacion' ORDER BY id DESC LIMIT 1", (factura_id,))
        if f["estado"] == "anulada" and not (ult_an and ult_an["estado_envio"] == "rechazado"):
            raise ErrorEmision("La factura ya está anulada")
        if db.fila(con, f"SELECT id FROM registros WHERE factura_id=? AND estado_envio IN {PENDIENTES}", (factura_id,)):
            raise ErrorEmision("Hay un registro de esta factura pendiente de respuesta o bloqueado; resuélvalo antes de anular")
        empresa = cargar_empresa(con, f["empresa_id"])
        produccion.comprobar(con, empresa, usuario_id, f["entorno"])
        # Tabla de operativas (doc. Validaciones, anexo): sin alta vigente en la AEAT -> SinRegistroPrevio=S;
        # anulación anterior rechazada -> RechazoPrevio=S (ambos pueden coincidir).
        sin_previo = None if existe_en_aeat(con, factura_id) else "S"
        rechazo = "S" if ult_an and ult_an["estado_envio"] == "rechazado" else None
        rid = crear_registro_anulacion(con, empresa, f["entorno"], f, rechazo_previo=rechazo, sin_registro_previo=sin_previo,
                                       usuario_id=usuario_id)
        cambios = {factura_id: "anulada"}
        con.execute("UPDATE facturas SET estado='anulada' WHERE id=?", (factura_id,))
        if f.get("rectifica_factura_id"):
            e2 = recalcular_estado_factura(con, f["rectifica_factura_id"])
            if e2:
                cambios[f["rectifica_factura_id"]] = e2
        db.actividad(con, "factura.anulada", usuario_id=usuario_id, empresa_id=empresa["id"], objeto="factura",
                     objeto_id=factura_id, detalle={"motivo": motivo, "registro_id": rid})
    diario.anotar_registro(con, rid)
    for fid_, est in cambios.items():
        diario.anotar("estado_factura", {"id": fid_, "estado": est})
    return rid


def subsanar(con, *, registro_id: int, usuario_id: int, motivo: str) -> int:
    """Nuevo registro de alta (Subsanacion=S) generado a partir de los mismos datos aprobados."""
    with db.transaccion(con):
        r = db.fila(con, "SELECT * FROM registros WHERE id=?", (registro_id,))
        if r["tipo"] != "alta":
            raise ErrorEmision("Solo se subsanan registros de alta")
        if r["estado_envio"] not in ("rechazado", "aceptado_con_errores", "aceptado"):
            raise ErrorEmision("El registro aún no tiene respuesta definitiva de la AEAT")
        posterior = db.fila(con, "SELECT id FROM registros WHERE factura_id=? AND id>? AND tipo='alta'", (r["factura_id"], registro_id))
        if posterior:
            raise ErrorEmision("Ya existe un registro posterior para esta factura")
        f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (r["factura_id"],))
        if f["estado"] == "anulada":
            raise ErrorEmision("La factura está anulada")
        empresa = cargar_empresa(con, f["empresa_id"])
        produccion.comprobar(con, empresa, usuario_id, f["entorno"])
        if db.fila(con, f"SELECT id FROM registros WHERE factura_id=? AND estado_envio IN {PENDIENTES}", (r["factura_id"],)):
            raise ErrorEmision("Hay un registro de esta factura pendiente de respuesta o bloqueado")
        datos = json.loads(f["datos"])
        # Tabla de operativas: no existe en la AEAT -> X; existe y se corrige una subsanación rechazada -> S;
        # existe y se subsana un registro aceptado (o con errores) -> N (se omite).
        if not existe_en_aeat(con, r["factura_id"]):
            rechazo = "X"
        elif r["estado_envio"] == "rechazado" and r["subsanacion"] == "S":
            rechazo = "S"
        else:
            rechazo = None
        rid = crear_registro_alta(con, empresa, f["entorno"], f, datos, subsanacion="S", rechazo_previo=rechazo,
                                  registro_corregido_id=registro_id, usuario_id=usuario_id)
        db.actividad(con, "registro.subsanacion", usuario_id=usuario_id, empresa_id=empresa["id"], objeto="registro",
                     objeto_id=rid, detalle={"motivo": motivo, "corrige": registro_id})
    diario.anotar_registro(con, rid)
    return rid


def borrador_rectificativa(con, *, factura_id: int, usuario_id: int, tipo: str, motivo: str) -> int:
    if tipo not in ("R1", "R4"):
        raise ErrorEmision("Tipo de rectificativa no soportado (R1 o R4)")
    f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (factura_id,))
    if f["estado"] not in ("emitida", "rectificada"):
        raise ErrorEmision(f"La factura está {f['estado']}")
    d = json.loads(f["datos"])
    nuevo = {k: d.get(k) for k in ("emisor", "destinatario", "referencias", "forma_pago", "iban")}
    nuevo.update({
        # FechaOperacion de la rectificativa = la de la factura original (criterio AEAT, procedimientos)
        "tipo_documento_origen": "rectificativa", "num_serie": None, "fecha_documento": None,
        "fecha_operacion": d.get("fecha_operacion") or f["fecha_expedicion"],
        "tipo_factura": tipo, "descripcion_operacion": f"Rectificación de la factura {f['num_serie']}: {motivo}"[:500],
        "lineas": [{"concepto": f"Rectificación de la factura {f['num_serie']}", "cantidad": None, "unidad": None,
                    "precio": None, "importe": None}],
        "desglose": [borradores.fila_exenta("0.00", x["operacion_exenta"], x.get("motivo_exencion"))
                     if borradores.es_exenta(x) else
                     {"tipo_iva": x["tipo_iva"], "base": "0.00", "cuota": "0.00", "calificacion": "S1",
                      "clave_regimen": "01", "impuesto": "01"} for x in d["desglose"]],
        "base_imponible": "0.00", "cuota_total": "0.00", "importe_total": "0.00", "retencion": None, "total_a_pagar": "0.00",
        "observaciones": f"Motivo de la rectificación: {motivo}", "indicios": [], "confirmaciones": [],
        "rectificativa": {"tipo_rectificativa": "I", "num_serie_rectificada": f["num_serie"],
                          "fecha_rectificada": f["fecha_expedicion"], "motivo": motivo,
                          "importes_originales": {"base": d["base_imponible"], "cuota": d["cuota_total"], "total": d["importe_total"]}},
    })
    with db.transaccion(con):
        ahora = db.ahora_iso()
        cur = con.execute("""INSERT INTO borradores (empresa_id, documento_id, datos, estado, rectifica_factura_id, creado_en, actualizado_en)
                             VALUES (?,?,?,?,?,?,?)""", (f["empresa_id"], None, json.dumps(nuevo, ensure_ascii=False),
                                                         "requiere_revision", factura_id, ahora, ahora))
        db.actividad(con, "rectificativa.borrador", usuario_id=usuario_id, empresa_id=f["empresa_id"], objeto="borrador",
                     objeto_id=cur.lastrowid, detalle={"factura": f["num_serie"], "tipo": tipo, "motivo": motivo})
        return cur.lastrowid


def vista_previa(con, borrador_id: int) -> tuple[bytes, dict]:
    b = db.fila(con, "SELECT * FROM borradores WHERE id=?", (borrador_id,))
    documento = cargar_documento(con, b.get("documento_id"))
    original = almacen.leer(documento["ruta"]) if documento else None
    datos = borradores.saneado(json.loads(b["datos"]))  # un dato guardado con un tipo inesperado no impide la vista previa
    renglones = borradores.renglones_estimados(datos)
    if renglones > borradores.MAX_RENGLONES_PDF:
        raise pdf_final.DocumentoDemasiadoLargo(
            f"La factura ocuparía unos {renglones} renglones (máximo {borradores.MAX_RENGLONES_PDF}): acorte los conceptos "
            "o las observaciones o divida la operación en varias facturas.")
    return pdf_final.generar(datos, modo="borrador", entorno=None, url_qr=None, original=original,
                             extraccion=extraccion_de(con, b), fecha_expedicion=None)
