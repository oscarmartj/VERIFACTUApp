"""Consulta a la AEAT (ConsultaFactuSistemaFacturacion) y comparación con los registros locales.

- Usa el MISMO transporte y las MISMAS salvaguardas que la remisión: el simulador local siempre; el entorno de pruebas
  o producción solo con su interruptor de servidor (VERIFACTU_PERMITIR_PRUEBAS_AEAT / VERIFACTU_PERMITIR_PRODUCCION),
  los requisitos de la empresa (produccion.comprobar) y su certificado vigente (remision._preparar_destino).
- Recorre todas las páginas de la respuesta (IndicadorPaginacion / ClavePaginacion), con un máximo de MAX_PAGINAS.
- Compara lo que tiene la AEAT con los registros locales del periodo: coinciden, solo en la AEAT, solo en local, y
  estado o huella distintos. NO cambia nada por sí misma: la única corrección que ofrece es la conciliación segura de un
  registro local sin respuesta definitiva (o rechazado por duplicado 3000) que la AEAT tiene con la misma huella
  (acción expresa de un administrador, que vuelve a consultar antes de aplicarla, se anota en el diario y en la
  actividad). Si después no queda nada sin respuesta definitiva, se cierra la incidencia de comunicación.
- Las peticiones y respuestas se guardan en el almacén como evidencia (empresa_X/<entorno>/consultas/...).
- Se consulta SIN IndicadorRepresentante aunque el certificado sea de un tercero: según el XSD, el indicador limita la
  respuesta a los registros en los que quien consulta figura como representante. Solo si la AEAT rechaza la consulta con
  4140 (sin apoderamiento para consultar) se repite con IndicadorRepresentante=S y el resultado se marca como PARCIAL: no
  se dice que falte lo que no devuelve, ni se proponen avances de contador ni reanudar la cadena a partir de él.
- Cadena (tras un desastre): si la AEAT tiene registros de esta instalación posteriores al último registro local, la
  consulta lo avisa («cadena_atrasada») y lo guarda (tabla cadenas_aeat, anotada en el diario): no se generan registros
  en ese entorno hasta que un administrador reanuda la cadena desde el último registro de la AEAT (`reanudar_cadena`).
- Todos los textos nombran el servicio consultado: «el simulador local» o «la AEAT».
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone

from . import almacen, certificados, db, diario, produccion, remision, series, simulador
from .config import ajustes
from .verifactu import esquemas_xsd, respuesta_consulta as rcmod, xmlgen

MAX_PAGINAS = 50
EJERCICIO_MIN, EJERCICIO_MAX = 2000, 2100
PERIODOS = tuple(f"{m:02d}" for m in range(1, 13))
MAX_ID = 2 ** 63 - 1          # mayor entero de SQLite: un identificador mayor nunca existe (y SQLite no lo admite)
# Sin respuesta definitiva de la AEAT (la cola los enviará o reenviará)
ESTADOS_SIN_RESPUESTA = ("pendiente", "sin_confirmar", "enviando", "bloqueado")
# Conciliables: los anteriores salvo «enviando», que puede estar en manos de un remitente (o quedó interrumpido y la cola
# lo pasará a «sin confirmar»); la conciliación nunca lo toca.
ESTADOS_CONCILIABLES = ("pendiente", "sin_confirmar", "bloqueado")
# Además, un rechazo por duplicado (3000): la AEAT ya tenía un registro con esa identificación. La respuesta de duplicado
# no trae la huella, así que la remisión solo lo confirma si hubo un intento previo sin respuesta (posible_entrega); si la
# consulta devuelve ESTE registro (misma huella), el rechazo se debió a un reenvío de lo que ya constaba (p. ej. un
# registro restaurado como «pendiente» de una copia sin el diario) y puede conciliarse igual.
SQL_CONCILIABLE = f"(estado_envio IN {ESTADOS_CONCILIABLES} OR (estado_envio='rechazado' AND codigo_error='3000'))"
ACEPTADOS = ("aceptado", "aceptado_con_errores")
CODIGO_SOLO_REPRESENTANTE = "4140"   # «No puede acceder a la consulta de facturas al no estar apoderado…» (errores.properties)
MAX_MESES_CADENA = 120               # meses que se vuelven a consultar, como máximo, para reanudar la cadena
MAX_MESES_AVANCE = 24                # meses que cubre, como máximo, la propuesta de avance del contador
_CERROJOS: dict[tuple[int, str], threading.Lock] = {}
_CERROJO_GLOBAL = threading.Lock()


class ErrorConsulta(Exception):
    def __init__(self, mensaje: str, estado: int = 502):
        super().__init__(mensaje)
        self.estado = estado


def servicio(entorno: str, mayuscula: bool = False, prep: str = "") -> str:
    """Nombre del servicio consultado en los textos: el simulador local nunca se presenta como la AEAT. `prep`: «de» o
    «a» con su contracción («del simulador local», «al simulador local», «de la AEAT», «a la AEAT»)."""
    sim = entorno == "simulador"
    t = {"": "el simulador local" if sim else "la AEAT", "de": "del simulador local" if sim else "de la AEAT",
         "a": "al simulador local" if sim else "a la AEAT"}[prep]
    return t[0].upper() + t[1:] if mayuscula else t


# --- Destino y transporte ------------------------------------------------------------------------------------------
def certificado_para_consulta(con, empresa_id: int, entorno: str) -> tuple[str, dict, dict | None]:
    """Destino, certificado y representante para consultar a la AEAT, con las MISMAS comprobaciones que la remisión
    (interruptores del servidor y la regla de `certificados.para_envio`: certificado propio de la empresa o de un tercero
    legitimado). Con certificado de un tercero devuelve el representante ({nif, nombre}); aun así la consulta va sin
    IndicadorRepresentante y solo lo pone al repetirse tras un 4140 (_consultar_paginas). El uso del certificado se
    audita al descifrarlo (operación «consulta»), no aquí."""
    url, cert, representante = remision._preparar_destino(con, entorno, empresa_id, operacion="consulta")
    return url, cert, representante


def _comprobar_entorno(con, empresa: dict, usuario_id: int, entorno: str) -> None:
    if entorno not in ("simulador", "pruebas", "produccion"):
        raise ErrorConsulta("Entorno no válido", 422)
    a = ajustes()
    if entorno == "pruebas" and not a.permitir_pruebas_aeat:
        raise ErrorConsulta("La comunicación con el entorno de pruebas de la AEAT está deshabilitada en el servidor "
                            "(VERIFACTU_PERMITIR_PRUEBAS_AEAT).", 403)
    if entorno == "produccion" and not a.permitir_produccion:
        raise ErrorConsulta("La comunicación con la AEAT (producción) está deshabilitada en el servidor "
                            "(VERIFACTU_PERMITIR_PRODUCCION).", 403)
    try:
        produccion.comprobar(con, empresa, usuario_id, entorno)
    except produccion.EntornoNoHabilitado as e:
        raise ErrorConsulta(str(e), 403) from e


def _transportar(con, empresa_id: int, entorno: str, sobre: bytes, quien: str | None = None) -> tuple[int, bytes, str]:
    if entorno == "simulador":
        # quien: NIF del tercero cuyo certificado se usa (en el simulador no hay TLS: se le pasa en el propio proceso)
        cuerpo = simulador.consultar(sobre, quien) if quien else simulador.consultar(sobre)
        return (500, cuerpo, "simulador-local") if cuerpo == b"HTTP500" else (200, cuerpo, "simulador-local")
    url, cert, _ = certificado_para_consulta(con, empresa_id, entorno)
    from . import certificados
    with certificados.ficheros_pem(cert) as (cp, kp):
        status, cuerpo = remision._post(url, sobre, cp, kp)
    return status, cuerpo, url


def _cerrojo(empresa_id: int, entorno: str) -> threading.Lock:
    with _CERROJO_GLOBAL:
        return _CERROJOS.setdefault((empresa_id, entorno), threading.Lock())


def validar_periodo(ejercicio, periodo) -> None:
    """Ejercicio con 4 cifras ASCII (no vale «２０２６» ni «٢٠٢٦», que str.isdigit() y el XSD aceptan) entre EJERCICIO_MIN y
    EJERCICIO_MAX, y periodo «01» a «12»."""
    if not (isinstance(ejercicio, str) and re.fullmatch(r"[0-9]{4}", ejercicio)
            and EJERCICIO_MIN <= int(ejercicio) <= EJERCICIO_MAX):
        raise ErrorConsulta(f"Ejercicio no válido: indique el año con 4 cifras, de {EJERCICIO_MIN} a {EJERCICIO_MAX}", 422)
    if periodo not in PERIODOS:
        raise ErrorConsulta("Periodo no válido (01 a 12)", 422)


def consultar_aeat(con, empresa: dict, usuario_id: int, entorno: str, ejercicio: str, periodo: str,
                   num_serie: str | None = None, *, comprobado: bool = False, renovar=None) -> dict:
    """Envía la consulta (todas las páginas) y devuelve {registros, paginas, destino, carpeta, avisos, errores_xsd,
    respuesta_ajena}. Lanza ErrorConsulta si no puede completarse. No modifica la base de datos.
    `renovar` (opcional): se llama antes de pedir cada página y, si devuelve False, la consulta se detiene (409); la
    conciliación lo usa para renovar el turno del remitente mientras consulta."""
    if not comprobado:
        _comprobar_entorno(con, empresa, usuario_id, entorno)
    validar_periodo(ejercicio, periodo)
    if num_serie is not None and not (1 <= len(num_serie) <= 60):
        raise ErrorConsulta("Número de factura no válido", 422)
    cerrojo = _cerrojo(empresa["id"], entorno)
    if not cerrojo.acquire(blocking=False):
        raise ErrorConsulta("Ya hay una consulta en curso para esta empresa y entorno; espere a que termine.", 409)
    try:
        return _consultar_paginas(con, empresa, entorno, ejercicio, periodo, num_serie, renovar)
    finally:
        cerrojo.release()


def _cabecera_ajena(resp: rcmod.RespuestaConsulta, nif: str, ejercicio: str, periodo: str) -> list[str]:
    """Diferencias entre la Cabecera/PeriodoImputacion de la respuesta y lo consultado (obligado, ejercicio y periodo)."""
    out = []
    for etq, recibido, pedido in (("NIF del obligado", resp.nif, nif), ("ejercicio", resp.ejercicio, ejercicio),
                                  ("periodo", resp.periodo, periodo)):
        if (recibido or "").strip().upper() != pedido.strip().upper():
            out.append(f"{etq} «{(recibido or 'no indicado')[:20]}» en lugar de «{pedido}»")
    return out


def _codigo_fault(resp: rcmod.RespuestaConsulta) -> str | None:
    """Código de error de un SOAP Fault («Codigo[4140].…» en el faultstring de la AEAT)."""
    m = re.search(r"Codigo\[(\d+)\]", resp.fault_texto or "") or re.search(r"\b(\d{4})\b", resp.fault_texto or "")
    return m.group(1) if m else None


def _representante_consulta(con, empresa: dict, entorno: str) -> dict | None:
    """Representante ({nif, nombre}) si el certificado de la empresa en ese entorno es de un tercero legitimado; None si
    es propio. Misma regla que la remisión (`certificados.para_envio`), también en el simulador."""
    try:
        if entorno == "simulador":
            return certificados.para_envio(con, empresa, "simulador")[1]
        return certificado_para_consulta(con, empresa["id"], entorno)[2]
    except (simulador.NoEnviado, certificados.CertificadoNoUtilizable) as e:
        # interruptor del servidor o certificado: no se llega a conectar
        raise ErrorConsulta(f"No se puede consultar: {e}", 403) from e


def _consultar_paginas(con, empresa, entorno, ejercicio, periodo, num_serie, renovar=None) -> dict:
    carpeta = (f"empresa_{empresa['id']}/{entorno}/consultas/"
               f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}")
    representante = _representante_consulta(con, empresa, entorno)
    quien = representante["nif"] if representante else None
    serv = servicio(entorno, mayuscula=True)
    por_clave: dict[tuple[str, str], rcmod.RegistroConsultado] = {}
    clave, destino, errores_xsd, ajena = None, None, [], []
    paginas = 0
    # Sin IndicadorRepresentante: con él, la respuesta solo trae los registros que presentó quien consulta (XSD)
    indicador = False
    while True:
        paginas += 1
        if paginas > MAX_PAGINAS:
            raise ErrorConsulta(f"La consulta supera {MAX_PAGINAS} páginas: acótela por número de factura.")
        if renovar is not None and not renovar():
            raise ErrorConsulta("Otro proceso ha tomado el turno del remitente durante la consulta: no se ha cambiado nada. "
                                "Vuelva a intentarlo en unos segundos.", 409)
        el = xmlgen.mensaje_consulta(nif=empresa["nif"], nombre=empresa["razon_social"], ejercicio=ejercicio,
                                     periodo=periodo, num_serie=num_serie or None, clave_paginacion=clave,
                                     representante=indicador, mostrar_sistema=True)
        errores = esquemas_xsd.validar(el, "ConsultaLR.xsd")
        if errores:
            raise ErrorConsulta("La consulta no cumple el esquema ConsultaLR.xsd (revise los datos de la empresa): "
                                + "; ".join(errores[:2]), 422)
        sobre = xmlgen.sobre_soap(el)
        nombre = f"{'representante_' if indicador else ''}pagina_{paginas:03d}"
        try:
            almacen.guardar(f"{carpeta}/{nombre}_peticion.xml", sobre)
        except Exception:  # noqa: BLE001 — la evidencia no impide consultar
            pass
        try:
            status, cuerpo, destino = _transportar(con, empresa["id"], entorno, sobre, quien)
        except simulador.NoEnviado as e:
            raise ErrorConsulta(f"No se pudo conectar con el servicio de consulta: {e}") from e
        except simulador.SinRespuesta as e:
            raise ErrorConsulta(f"El servicio de consulta no respondió: {e}") from e
        except Exception as e:  # noqa: BLE001 — p. ej. el certificado no se puede descifrar: nunca se detalla
            raise ErrorConsulta(f"No se pudo preparar la consulta: {type(e).__name__}") from e
        try:
            almacen.guardar(f"{carpeta}/{nombre}_respuesta.xml", cuerpo)
        except Exception:  # noqa: BLE001
            pass
        try:
            resp = rcmod.interpretar(cuerpo)
        except Exception as e:  # noqa: BLE001 — respuesta ilegible o HTTP 5xx
            raise ErrorConsulta(f"Respuesta ilegible del servicio de consulta (HTTP {status}): {type(e).__name__}") from e
        if resp.tipo == "fault":
            if (representante and not indicador and paginas == 1
                    and _codigo_fault(resp) == CODIGO_SOLO_REPRESENTANTE):
                # Un tercero sin apoderamiento para consultar: se repite con IndicadorRepresentante=S, que solo devuelve
                # los registros que presentó como representante (resultado PARCIAL)
                indicador, paginas, clave = True, 0, None
                continue
            que = "no pudo atender" if "server" in (resp.fault_codigo or "").lower() else "rechazó"
            raise ErrorConsulta(f"{serv} {que} la consulta: {resp.fault_codigo}: {resp.fault_texto}")
        errores_xsd += resp.errores_xsd
        for d in _cabecera_ajena(resp, empresa["nif"], ejercicio, periodo):
            if d not in ajena:
                ajena.append(d)
        for r in resp.registros:
            por_clave[r.clave()] = r
        if resp.indicador_paginacion != "S":
            break
        nueva = resp.clave_paginacion or (
            {"id_emisor": resp.registros[-1].id_emisor, "num_serie": resp.registros[-1].num_serie,
             "fecha_expedicion": resp.registros[-1].fecha_expedicion} if resp.registros else None)
        if not nueva or nueva == clave:
            raise ErrorConsulta("La respuesta indica más páginas pero no permite continuar (ClavePaginacion).")
        clave = nueva
    avisos = []
    if ajena:
        avisos.append("La respuesta no corresponde a lo consultado (" + "; ".join(ajena) + "): la comparación no es fiable "
                      "y no se ofrece conciliar. Compruebe el certificado y los datos de la empresa antes de actuar.")
    if errores_xsd:
        avisos.append("La respuesta no cumple del todo el esquema RespuestaConsultaLR.xsd: "
                      + "; ".join(dict.fromkeys(errores_xsd[:3])) + ". Compruebe los datos antes de actuar.")
    if indicador:
        avisos.append(f"{serv} rechazó la consulta sin IndicadorRepresentante (4140: el titular del certificado, "
                      f"{representante['nombre'] or representante['nif']}, no está apoderado para consultar) y se repitió "
                      "con él. El resultado es PARCIAL: solo incluye los registros que presentó ese representante. Que no "
                      "aparezca un registro no significa que no conste; no se proponen avances de contador ni reanudar la "
                      "cadena a partir de esta consulta.")
    return {"registros": list(por_clave.values()), "paginas": paginas, "destino": destino, "carpeta": carpeta,
            "avisos": avisos, "errores_xsd": errores_xsd, "respuesta_ajena": ajena, "representante": bool(representante),
            "indicador_representante": indicador, "parcial": indicador}


# --- Comparación ----------------------------------------------------------------------------------------------------
def _fecha_aeat(iso: str) -> str:
    a, m, d = iso[:10].split("-")
    return f"{d}-{m}-{a}"


def _coherente(tipo: str, estado_local: str, estado_aeat: str) -> bool:
    """¿El estado que da la AEAT corresponde al registro local (aceptado o pendiente de confirmar)?"""
    if tipo == "anulacion":
        return estado_aeat == "Anulado"
    if estado_local == "aceptado":
        return estado_aeat == "Correcto"
    if estado_local == "aceptado_con_errores":
        return estado_aeat == "AceptadoConErrores"
    return estado_aeat in ("Correcto", "AceptadoConErrores")


def _conciliable_local(r: dict) -> bool:
    return r["estado_envio"] in ESTADOS_CONCILIABLES or (r["estado_envio"] == "rechazado" and r.get("codigo_error") == "3000")


def _estado_propuesto(tipo: str, estado_aeat: str) -> str:
    return "aceptado_con_errores" if (tipo == "alta" and estado_aeat == "AceptadoConErrores") else "aceptado"


def _local(con, empresa_id: int, entorno: str, ejercicio: str, periodo: str, num_serie: str | None) -> dict:
    sql = """SELECT id, num_serie, fecha_expedicion, estado, importe_total FROM facturas
             WHERE empresa_id=? AND entorno=? AND substr(fecha_expedicion, 1, 7)=?"""
    args: list = [empresa_id, entorno, f"{ejercicio}-{periodo}"]
    if num_serie:
        sql += " AND num_serie=?"
        args.append(num_serie)
    out = {}
    for f in db.filas(con, sql + " ORDER BY id", args):
        regs = db.filas(con, """SELECT id, tipo, secuencia, huella, estado_envio, codigo_error, subsanacion, id_emisor
                                FROM registros WHERE factura_id=? ORDER BY secuencia""", (f["id"],))
        if regs:
            out[(f["num_serie"], _fecha_aeat(f["fecha_expedicion"]))] = {"factura": f, "registros": regs}
    return out


def _fila_local(f: dict, r: dict | None) -> dict:
    return {"factura_id": f["id"], "num_serie": f["num_serie"], "fecha_expedicion": _fecha_aeat(f["fecha_expedicion"]),
            "estado_factura": f["estado"], "importe_total": f.get("importe_total"),
            "registro_id": r["id"] if r else None, "tipo_registro": r["tipo"] if r else None,
            "estado_local": r["estado_envio"] if r else None,
            "estado_local_texto": remision.TEXTO_ESTADO.get(r["estado_envio"], r["estado_envio"]) if r else None,
            "huella_local": r["huella"] if r else None}


def _fila_aeat(a: rcmod.RegistroConsultado) -> dict:
    return {"estado_aeat": a.estado, "huella_aeat": a.huella, "codigo_error_aeat": a.codigo_error,
            "descripcion_error_aeat": a.descripcion_error, "id_peticion": a.id_peticion,
            "timestamp_presentacion": a.timestamp_presentacion, "importe_total_aeat": a.importe_total,
            "cuota_total_aeat": a.cuota_total, "tipo_factura_aeat": a.tipo_factura}


def clasificar(local: dict, a: rcmod.RegistroConsultado, entorno: str = "produccion") -> dict:
    """Clasifica una factura que está a la vez en local y en la AEAT (o en el simulador local, según `entorno`). Devuelve
    la fila con «categoria» (coinciden o distintos), «nota»/«motivo», «revisar» y, si procede, «conciliable» con el
    estado propuesto."""
    serv, Serv = servicio(entorno), servicio(entorno, mayuscula=True)
    f, regs = local["factura"], local["registros"]
    ultimo = regs[-1]
    por_huella = {r["huella"]: r for r in regs}
    r = por_huella.get(a.huella or "")
    out = dict(_fila_local(f, r or ultimo), **_fila_aeat(a), conciliable=False, revisar=False, nota=None)
    if r is None:
        out.update(categoria="distintos", revisar=True,
                   motivo=f"{Serv} tiene esta factura con otra huella: puede proceder de otro sistema o de un registro "
                          "que no consta en esta instalación. Revíselo antes de actuar.")
        return out
    if r is ultimo:
        if r["estado_envio"] in ACEPTADOS:
            if _coherente(r["tipo"], r["estado_envio"], a.estado):
                out.update(categoria="coinciden")
            else:
                out.update(categoria="distintos", revisar=True,
                           motivo=f"Misma huella, estado distinto: en local «{out['estado_local_texto']}», en {serv} «{a.estado}».")
            return out
        if r["estado_envio"] == "enviando":
            out.update(categoria="distintos",
                       motivo=f"{Serv} ya tiene este registro (misma huella, estado «{a.estado}»), pero en local se está "
                              "enviando (o su envío se interrumpió y la cola lo reintentará): no se concilia mientras tanto. "
                              "Espere a que termine y vuelva a consultar.")
            return out
        if _conciliable_local(r) and _coherente(r["tipo"], r["estado_envio"], a.estado):
            nuevo = _estado_propuesto(r["tipo"], a.estado)
            if r["estado_envio"] == "rechazado":
                motivo = (f"En local figura «Rechazado» por duplicado (3000), pero {serv} tiene este mismo registro (misma "
                          f"huella) con estado «{a.estado}»: el rechazo se debió a reenviar un registro que ya constaba (p. ej. "
                          f"tras restaurar una copia). Puede conciliarse: marcarlo como «{remision.TEXTO_ESTADO[nuevo]}».")
            else:
                motivo = (f"{Serv} ya tiene este registro (misma huella) con estado «{a.estado}», pero en local figura "
                          f"«{out['estado_local_texto']}». Puede conciliarse: marcarlo como «{remision.TEXTO_ESTADO[nuevo]}» "
                          "evita reenviarlo.")
            out.update(categoria="distintos", conciliable=True, estado_propuesto=nuevo,
                       estado_propuesto_texto=remision.TEXTO_ESTADO[nuevo], motivo=motivo)
            return out
        out.update(categoria="distintos", revisar=True,
                   motivo=f"En local figura «{out['estado_local_texto']}», pero {serv} tiene este registro (misma huella) "
                          f"con estado «{a.estado}». Revíselo.")
        return out
    # La AEAT tiene un registro anterior de la factura
    aceptados = [x for x in regs if x["estado_envio"] in ACEPTADOS]
    esperado = aceptados[-1] if aceptados else None
    posteriores = [x for x in regs if x["secuencia"] > r["secuencia"]]
    if r is esperado and all(x["estado_envio"] in ("rechazado", *ESTADOS_SIN_RESPUESTA) for x in posteriores) \
            and _coherente(r["tipo"], r["estado_envio"], a.estado):
        pend = [x for x in posteriores if x["estado_envio"] in ESTADOS_SIN_RESPUESTA]
        out.update(categoria="coinciden",
                   nota=(f"Hay un registro posterior ({'anulación' if pend[-1]['tipo'] == 'anulacion' else 'alta'}, "
                         f"«{remision.TEXTO_ESTADO.get(pend[-1]['estado_envio'])}») que aún no consta en {serv}; la cola "
                         "de remisión lo enviará.") if pend else
                        f"El registro posterior fue rechazado; {serv} conserva el anterior.")
        return out
    if a.estado == "Anulado" and ultimo["tipo"] == "anulacion" and r["tipo"] == "alta":
        if ultimo["estado_envio"] in ACEPTADOS:
            out.update(_fila_local(f, ultimo), categoria="coinciden",
                       nota=f"{Serv} muestra la factura anulada con la huella de su registro de alta.")
        else:
            out.update(_fila_local(f, ultimo), categoria="distintos", revisar=True,
                       motivo=f"{Serv} muestra la factura anulada, pero con la huella del registro de alta: no puede "
                              "comprobarse por la huella que la anulación registrada sea la de esta instalación. Revíselo; "
                              f"si la cola la reenvía y ya consta, {serv} responderá «duplicado» y se confirmará.")
        out.update(_fila_aeat(a))
        return out
    out.update(_fila_local(f, ultimo), categoria="distintos", revisar=True,
               motivo=f"{Serv} tiene un registro anterior de esta factura (secuencia {r['secuencia']}); el último registro "
                      f"local (secuencia {ultimo['secuencia']}, «{remision.TEXTO_ESTADO.get(ultimo['estado_envio'])}») no "
                      f"consta en {'él' if entorno == 'simulador' else 'ella'}. Revíselo.")
    out.update(_fila_aeat(a))
    return out


# --- Cadena: registros de esta instalación posteriores al último registro local (tras un desastre) -----------------
def _dt(fh: str | None) -> datetime | None:
    """FechaHoraHusoGenRegistro como fecha y hora con huso (sin huso: hora peninsular)."""
    try:
        d = datetime.fromisoformat(fh) if fh else None
    except (TypeError, ValueError):
        return None
    return d.replace(tzinfo=db.TZ) if d is not None and d.tzinfo is None else d


def estado_cadena(con, empresa_id: int, entorno: str) -> dict | None:
    """Fila de cadenas_aeat (cadena «atrasada» respecto a la AEAT, o «anclada» a su último registro) o None."""
    return db.fila(con, "SELECT * FROM cadenas_aeat WHERE empresa_id=? AND entorno=?", (empresa_id, entorno))


def cabeza_local(con, empresa_id: int, entorno: str) -> dict:
    """Último registro de la cadena local de la instalación de la empresa en ese entorno y, si hay un ancla pendiente (la
    cadena se reanudó desde la AEAT), esa ancla: el registro siguiente se encadenará a ella."""
    inst = db.fila(con, "SELECT id, numero_instalacion FROM instalaciones WHERE empresa_id=?", (empresa_id,))
    cad = db.fila(con, "SELECT * FROM cadenas WHERE empresa_id=? AND entorno=? AND instalacion_id=?",
                  (empresa_id, entorno, inst["id"])) if inst else None
    if inst is None:
        # Copia anterior al primer registro de la empresa: la instalación se creará con este mismo número
        # (emision.instalacion), así que los registros de la AEAT con él son de esta cadena
        p = produccion.productor(con)
        inst = {"id": None, "numero_instalacion": f"{p['numero_instalacion']}-OT{empresa_id:04d}"} \
            if p and p.get("numero_instalacion") else None
    reg = db.fila(con, """SELECT id, num_serie, fecha_expedicion, fecha_hora_huso, huella FROM registros WHERE id=?""",
                  (cad["ultimo_registro_id"],)) if cad and cad.get("ultimo_registro_id") else None
    est = estado_cadena(con, empresa_id, entorno)
    ancla = est if est and est["estado"] == "anclada" else None
    fh = reg["fecha_hora_huso"] if reg else None
    if ancla and (fh is None or (_dt(ancla["fecha_hora_huso"]) or datetime.min.replace(tzinfo=timezone.utc)) > _dt(fh)):
        fh = ancla["fecha_hora_huso"]
    return {"registro": reg, "ancla": ancla, "estado": est, "fecha_hora_huso": fh,
            "instalacion": inst["numero_instalacion"] if inst else None}


def _de_esta_instalacion(a: rcmod.RegistroConsultado, instalacion: str | None) -> bool | None:
    """¿El registro es de este sistema informático e instalación? None si la respuesta no trae SistemaInformatico."""
    if not (a.sistema_id or a.sistema_instalacion):
        return None
    from .emision import ID_SIF
    return a.sistema_id == ID_SIF and a.sistema_instalacion == instalacion


_T_MIN = datetime.min.replace(tzinfo=timezone.utc)


def _raiz(cab: dict) -> str | None:
    """Huella desde la que continúa la cadena: el ancla pendiente (la cadena se reanudó desde la AEAT) o el último registro
    local."""
    return (cab["ancla"] or {}).get("huella") or (cab["registro"] or {}).get("huella")


def _huellas_conocidas(con, empresa: dict, entorno: str, cab: dict, registros_aeat: list) -> set[str]:
    """Huellas de la cadena que ya constan aquí: los registros locales, las anclas (usadas o pendientes) y, siguiendo hacia
    atrás el Encadenamiento/RegistroAnterior que devuelve la consulta, los registros anteriores a un ancla (perdidos, pero
    ya revisados al reanudar la cadena desde ella)."""
    conocidas = {r["huella"] for r in db.filas(con, "SELECT huella FROM registros WHERE empresa_id=? AND entorno=?",
                                                 (empresa["id"], entorno))}
    anclas = {r["h"] for r in db.filas(con, """SELECT json_extract(ancla_aeat, '$.huella') h FROM registros
                                               WHERE empresa_id=? AND entorno=? AND ancla_aeat IS NOT NULL
                                               AND json_valid(ancla_aeat)""", (empresa["id"], entorno)) if r["h"]}
    if cab["ancla"]:
        anclas.add(cab["ancla"]["huella"])
    conocidas |= anclas
    por_huella = {a.huella: a for a in registros_aeat if a.id_emisor == empresa["nif"] and a.huella}
    pendientes = list(anclas)
    while pendientes:
        a = por_huella.get(pendientes.pop())
        if a is not None and a.huella_anterior and a.huella_anterior not in conocidas:
            conocidas.add(a.huella_anterior)
            pendientes.append(a.huella_anterior)
    return conocidas


def posteriores_a_la_cabeza(con, empresa: dict, entorno: str, registros_aeat: list[rcmod.RegistroConsultado]) -> list:
    """Registros de la AEAT del mismo obligado, de esta instalación (o sin SistemaInformatico en la respuesta), con una
    huella que no consta aquí y posteriores al último registro local (o al ancla pendiente), del más antiguo al más
    reciente. Son posteriores:
    - los encadenados (Encadenamiento/RegistroAnterior) a ese registro, y los encadenados a ellos, aunque se generaran en
      el mismo segundo (FechaHoraHusoGenRegistro solo tiene segundos);
    - los generados en el mismo segundo que el último registro local o después: todo lo anterior a él consta aquí. Con un
      ancla pendiente, solo los generados después de ella (los de su mismo segundo se revisaron al reanudar).
    Sin cadena local, solo los que la respuesta identifica como de esta instalación (sin ese dato podrían ser de otro
    sistema que el obligado usaba antes)."""
    cab = cabeza_local(con, empresa["id"], entorno)
    conocidas = _huellas_conocidas(con, empresa, entorno, cab, registros_aeat)
    t0 = _dt(cab["fecha_hora_huso"])
    cand: dict[str, rcmod.RegistroConsultado] = {}
    for a in registros_aeat:
        if (a.id_emisor == empresa["nif"] and a.huella and a.huella not in conocidas and a.huella not in cand
                and _de_esta_instalacion(a, cab["instalacion"]) is not False):
            cand[a.huella] = a
    post: dict[str, rcmod.RegistroConsultado] = {}
    eslabones = {h for h in (_raiz(cab),) if h}
    nuevos = True
    while nuevos:
        nuevos = False
        for h, a in cand.items():
            if h not in post and a.huella_anterior in eslabones:
                post[h] = a
                eslabones.add(h)
                nuevos = True
    for h, a in cand.items():
        t = _dt(a.fecha_hora_huso)
        if h in post or t is None:
            continue
        if t0 is None:
            if _de_esta_instalacion(a, cab["instalacion"]) is not True:
                continue
        elif t < t0 or (t == t0 and cab["ancla"] is not None):
            continue
        post[h] = a
    return sorted(post.values(), key=lambda x: (_dt(x.fecha_hora_huso) or _T_MIN, x.num_serie, x.huella))


def ultimo_de_la_cadena(post: list, raiz: str | None) -> tuple[rcmod.RegistroConsultado | None, list]:
    """Último registro de la cadena entre los posteriores a la cabeza: el que ningún otro tiene como RegistroAnterior (la
    consulta solo devuelve el último registro de cada factura, así que una subsanación o una anulación deja huecos y puede
    haber varios así). Entre varios, el generado más tarde; si coinciden en el segundo, se descarta el que termina el tramo
    que empieza en `raiz` (ese tramo es el primero). Devuelve (último o None si no puede saberse con seguridad, candidatos)."""
    referenciadas = {a.huella_anterior for a in post if a.huella_anterior}
    cands = [a for a in post if a.huella not in referenciadas]
    if len(cands) > 1:
        tmax = max(_dt(a.fecha_hora_huso) or _T_MIN for a in cands)
        cands = [a for a in cands if (_dt(a.fecha_hora_huso) or _T_MIN) == tmax]
    if len(cands) > 1:
        por_huella = {a.huella: a for a in post}

        def inicio(a):
            vistos = set()
            while a.huella_anterior in por_huella and a.huella not in vistos:
                vistos.add(a.huella)
                a = por_huella[a.huella_anterior]
            return a
        resto = [a for a in cands if inicio(a).huella_anterior != raiz]
        if resto:
            cands = resto
    if len(cands) == 1 and _dt(cands[0].fecha_hora_huso) is not None:
        return cands[0], cands
    return None, (cands or list(post))


def _resumen_cadena(post: list, cab: dict) -> dict:
    ult, cands = ultimo_de_la_cadena(post, _raiz(cab))
    ambigua = ult is None
    if ambigua:   # para el aviso y el bloqueo; reanudar_cadena no la acepta
        ult = max(cands, key=lambda x: (_dt(x.fecha_hora_huso) or _T_MIN, x.num_serie, x.huella))
    reg = cab["registro"]
    return {"id_emisor": ult.id_emisor, "num_serie": ult.num_serie, "fecha_expedicion": ult.fecha_expedicion,
            "fecha_hora_huso": ult.fecha_hora_huso or "", "huella": ult.huella, "estado_aeat": ult.estado,
            "tipo": "anulacion" if ult.estado == "Anulado" else "alta", "registros_posteriores": len(post),
            "de_esta_instalacion": _de_esta_instalacion(ult, cab["instalacion"]),
            "ambigua": ambigua,
            "candidatos": [{"num_serie": a.num_serie, "fecha_expedicion": a.fecha_expedicion,
                            "fecha_hora_huso": a.fecha_hora_huso, "huella": a.huella} for a in cands] if ambigua else [],
            "cabeza_local": ({"num_serie": reg["num_serie"], "fecha_expedicion": reg["fecha_expedicion"],
                              "fecha_hora_huso": reg["fecha_hora_huso"], "huella": reg["huella"]} if reg else None)}


def _dia(fecha) -> str:
    """FechaExpedicion de la AEAT («dd-mm-aaaa») como la muestra la aplicación («dd/mm/aaaa»)."""
    return fecha.replace("-", "/") if isinstance(fecha, str) and re.fullmatch(r"\d{2}-\d{2}-\d{4}", fecha) else str(fecha)


def texto_ambigua(res: dict, entorno: str) -> str:
    cands = "; ".join(f"{c['num_serie']} del {_dia(c['fecha_expedicion'])}, generado el "
                      f"{db.legible(c['fecha_hora_huso']) if c['fecha_hora_huso'] else 'sin fecha'}, "
                      f"huella {(c['huella'] or '')[:16]}…" for c in res["candidatos"])
    return (f"No se puede saber con seguridad cuál es el último registro de la cadena en {servicio(entorno)}: la respuesta "
            f"no permite ordenar estos registros (se generaron en el mismo segundo o falta su fecha y hora): {cands}. No "
            "se reanuda la cadena a partir de ninguno de ellos; revíselo con el operador de la plataforma.")


def anulaciones_sin_huella(con, empresa: dict, entorno: str, registros_aeat: list) -> list:
    """Facturas que la AEAT da por anuladas con la huella de su registro de alta cuando en esta instalación no consta la
    anulación (el último registro local de la factura es ese alta): la anulación es un registro de la cadena que la
    consulta no deja ver (ni su huella ni su fecha y hora), así que no puede saberse si es el último."""
    altas = {r["huella"] for r in db.filas(con, """SELECT r.huella FROM registros r WHERE r.empresa_id=? AND r.entorno=?
                                                  AND r.tipo='alta' AND r.secuencia=(SELECT MAX(x.secuencia) FROM registros x
                                                  WHERE x.factura_id=r.factura_id)""", (empresa["id"], entorno))}
    return [a for a in registros_aeat if a.id_emisor == empresa["nif"] and a.estado == "Anulado" and a.huella in altas]


def texto_anulaciones_sin_huella(anuladas: list, entorno: str) -> str:
    facturas = ", ".join(dict.fromkeys(f"{a.num_serie} del {_dia(a.fecha_expedicion)}" for a in anuladas))
    return (f"{servicio(entorno, mayuscula=True)} da por anulada(s) {facturas} con la huella de su registro de alta, y en "
            "esta instalación no consta esa anulación: es un registro de la cadena que la consulta no deja ver (ni su "
            "huella ni su fecha y hora). Si se perdió con la base de datos (p. ej. tras restaurar una copia), la cadena "
            "local está atrasada y no puede reanudarse con seguridad desde la consulta: no expida, anule ni subsane "
            "facturas en este entorno hasta aclararlo con el operador de la plataforma.")


def mensaje_cadena_atrasada(est: dict, entorno: str) -> str:
    serv = servicio(entorno)
    return (f"{servicio(entorno, mayuscula=True)} tiene registros de esta empresa posteriores al último registro de esta "
            f"instalación (el último, {est['num_serie']} del {_dia(est['fecha_expedicion'])}, generado el "
            f"{db.legible(est['fecha_hora_huso'])}), probablemente generados después de la copia que se restauró y perdidos con la base "
            "de datos y el diario. No se pueden expedir, anular ni subsanar facturas en este entorno hasta reanudar la "
            f"cadena: un administrador debe usar «Reanudar la cadena desde el último registro {servicio(entorno, prep='de')}» en "
            f"la página Consulta a la AEAT. Así el siguiente registro se encadenará al último que tiene {serv} y no al de la "
            "copia.")


def _snapshot(con, empresa_id: int, entorno: str) -> dict | None:
    return estado_cadena(con, empresa_id, entorno)


def _anotar_estado_cadena(d: dict) -> None:
    """Diario de recuperación (tipo `cadena_aeat`): la última anotación de cada empresa y entorno es la que vale."""
    diario.anotar("cadena_aeat", d)


def registrar_cadena_atrasada(con, empresa: dict, usuario_id: int | None, entorno: str, resumen: dict,
                              evidencia: list[str]) -> bool:
    """Guarda (y anota en el diario) que la cadena de la empresa en ese entorno está atrasada respecto a la AEAT. Solo si
    cambia (otro último registro); devuelve True si se guardó."""
    with db.transaccion(con):
        est = estado_cadena(con, empresa["id"], entorno)
        if est and est["estado"] == "atrasada" and est["huella"] == resumen["huella"]:
            return False
        cab = resumen.get("cabeza_local") or {}
        con.execute("""INSERT INTO cadenas_aeat (empresa_id, entorno, estado, id_emisor, num_serie, fecha_expedicion,
                       fecha_hora_huso, huella, tipo, registros_posteriores, cabeza_local, detectada_en, evidencia,
                       anclada_en, anclada_por) VALUES (?,?,'atrasada',?,?,?,?,?,?,?,?,?,?,NULL,NULL)
                       ON CONFLICT(empresa_id, entorno) DO UPDATE SET estado='atrasada', id_emisor=excluded.id_emisor,
                       num_serie=excluded.num_serie, fecha_expedicion=excluded.fecha_expedicion,
                       fecha_hora_huso=excluded.fecha_hora_huso, huella=excluded.huella, tipo=excluded.tipo,
                       registros_posteriores=excluded.registros_posteriores, cabeza_local=excluded.cabeza_local,
                       detectada_en=excluded.detectada_en, evidencia=excluded.evidencia, anclada_en=NULL, anclada_por=NULL""",
                    (empresa["id"], entorno, resumen["id_emisor"], resumen["num_serie"], resumen["fecha_expedicion"],
                     resumen["fecha_hora_huso"], resumen["huella"], resumen["tipo"], resumen["registros_posteriores"],
                     cab.get("fecha_hora_huso"), db.ahora_iso(), json.dumps(evidencia)))
        db.actividad(con, "cadena.atrasada_aeat", usuario_id=usuario_id, empresa_id=empresa["id"],
                     detalle={"entorno": entorno, "ultimo_registro_aeat": {k: resumen[k] for k in (
                         "num_serie", "fecha_expedicion", "fecha_hora_huso", "huella", "estado_aeat")},
                              "registros_posteriores": resumen["registros_posteriores"], "cabeza_local": cab or None,
                              "evidencia": evidencia})
        snap = _snapshot(con, empresa["id"], entorno)
    try:   # el bloqueo ya rige; si el diario falla, la conciliación del arranque lo anotará
        _anotar_estado_cadena(snap)
    except Exception:  # noqa: BLE001
        pass
    return True


# --- Comparación ------------------------------------------------------------------------------------------------------
def comparar(con, empresa: dict, entorno: str, ejercicio: str, periodo: str, num_serie: str | None,
             registros_aeat: list[rcmod.RegistroConsultado], parcial: bool = False) -> dict:
    """Compara lo que devuelve la consulta con los registros locales del periodo. `parcial`: la respuesta solo trae los
    registros presentados por el representante (IndicadorRepresentante=S): lo que no devuelve puede constar igualmente."""
    nif = empresa["nif"]
    serv, Serv = servicio(entorno), servicio(entorno, mayuscula=True)
    local = _local(con, empresa["id"], entorno, ejercicio, periodo, num_serie)
    out = {"coinciden": [], "solo_aeat": [], "solo_local": [], "distintos": []}
    vistos = set()
    usados: dict[tuple[int, int], list[dict]] = {}   # (serie, año del contador) -> filas con número ya usado en la AEAT
    for a in sorted(registros_aeat, key=lambda x: (x.fecha_expedicion[-4:] + x.fecha_expedicion[3:5] + x.fecha_expedicion[:2], x.num_serie)):
        k = a.clave()
        if a.id_emisor == nif and k in local:
            vistos.add(k)
            fila = clasificar(local[k], a, entorno)
            out[fila.pop("categoria")].append(fila)
            continue
        fila = {"num_serie": a.num_serie, "fecha_expedicion": a.fecha_expedicion, "id_emisor": a.id_emisor,
                **_fila_aeat(a), "revisar": True}
        if a.id_emisor != nif:
            fila["nota"] = "Registro de otro emisor."
        else:
            anio = int(a.fecha_expedicion[-4:]) if a.fecha_expedicion[-4:].isdigit() else 0
            s = series.identificar(con, empresa["id"], entorno, a.num_serie, anio)
            if s and s["numero_detectado"] >= s["siguiente_numero"]:
                fila["nota"] = (f"Número ya usado en {serv} que no consta en esta instalación; el contador local de la serie "
                                f"«{s['codigo']}» va por el {s['siguiente_numero']}: no expida ese número (un administrador "
                                "puede avanzar el contador de la serie).")
                fila.update(serie_id=s["id"], serie_codigo=s["codigo"], serie_patron=s["patron"],
                            anio_contador=s["anio_contador"], numero_aeat=s["numero_detectado"],
                            siguiente_local=s["siguiente_numero"])
                usados.setdefault((s["id"], s["anio_contador"]), []).append(fila)
            elif s:
                fila["nota"] = (f"No consta en esta instalación (¿expedida con otro sistema o perdida en un desastre?). El "
                                f"contador local de la serie «{s['codigo']}» ya va por el {s['siguiente_numero']}: ese número "
                                "no se volverá a expedir.")
            else:
                fila["nota"] = "No consta en esta instalación (¿expedida con otro sistema o perdida en un desastre?)."
        out["solo_aeat"].append(fila)
    # Propuesta para avanzar el contador: el mayor número de la serie en lo consultado + 1. Solo con una consulta del mes
    # completo y no parcial; consultar_y_comparar la completa con los meses siguientes del ejercicio (hasta el actual).
    if not num_serie and not parcial:
        for filas_s in usados.values():
            propuesto = max(f["numero_aeat"] for f in filas_s) + 1
            if propuesto <= series.MAX_NUMERO:
                for f in filas_s:
                    f["siguiente_propuesto"] = propuesto
    out["_usados"] = usados
    for k, loc in local.items():
        if k in vistos:
            continue
        regs = loc["registros"]
        ultimo = regs[-1]
        fila = _fila_local(loc["factura"], ultimo)
        if parcial:
            fila.update(revisar=False, nota=f"La respuesta {servicio(entorno, prep='de')} es parcial (solo los registros que presentó el "
                                            "representante): que no aparezca no significa que no conste.")
        elif any(x["estado_envio"] in ACEPTADOS for x in regs):
            fila.update(revisar=True, nota=f"En local consta aceptado, pero {serv} no lo devuelve. Revíselo.")
        elif ultimo["estado_envio"] in ESTADOS_SIN_RESPUESTA:
            fila.update(revisar=False, nota=f"Aún no consta en {serv} («{fila['estado_local_texto']}»): la cola de "
                                            "remisión lo enviará.")
        elif ultimo["estado_envio"] == "rechazado" and ultimo["codigo_error"] == "3000":
            fila.update(revisar=True, nota=f"Rechazado por duplicado (3000): {serv} dijo tener un registro con esta "
                                           "identificación, pero la consulta no lo devuelve. Revíselo.")
        else:
            fila.update(revisar=False, nota=f"Rechazado: no consta en {serv} (es lo esperado).")
        out["solo_local"].append(fila)
    out["solo_local"].sort(key=lambda x: (x["fecha_expedicion"][-4:] + x["fecha_expedicion"][3:5] + x["fecha_expedicion"][:2], x["num_serie"]))
    _resumir(out)
    return out


def _resumir(out: dict) -> None:
    out["resumen"] = {k: len(out[k]) for k in ("coinciden", "solo_aeat", "solo_local", "distintos")}
    out["resumen"]["conciliables"] = sum(1 for x in out["distintos"] if x.get("conciliable"))
    out["resumen"]["revisar"] = sum(1 for k in ("coinciden", "solo_aeat", "solo_local", "distintos") for x in out[k] if x.get("revisar"))


def _meses_avance(ejercicio: str, periodo: str, sin_anio: bool) -> tuple[list[tuple[str, str]], bool]:
    """(ejercicio, periodo) que cubre la propuesta de avance del contador: el mes consultado y los siguientes hasta el
    actual, dentro del ejercicio (la numeración de las series con año empieza de nuevo cada año). Si alguna serie afectada
    no lleva año, su contador sigue en los años siguientes: se continúa hasta el mes actual, como mucho MAX_MESES_AVANCE
    meses. Devuelve (meses, completo): completo es False si ese límite deja fuera meses hasta el actual."""
    hoy = db.ahora()
    a, m = int(ejercicio), int(periodo)
    fin = (hoy.year, hoy.month) if sin_anio else min((a, 12), (hoy.year, hoy.month))
    out = [(ejercicio, periodo)]
    while True:
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
        if (a, m) > fin:
            return out, True
        if len(out) >= MAX_MESES_AVANCE:
            return out, False
        out.append((f"{a:04d}", f"{m:02d}"))


def _proponer_avance(con, empresa: dict, usuario_id: int, entorno: str, ejercicio: str, periodo: str, cmp_: dict,
                     evidencias: list[str], registros: list) -> None:
    """Completa la propuesta de avance del contador con los meses siguientes hasta el actual (del mismo ejercicio; y de
    los siguientes si la serie no lleva año), todas las páginas: la propuesta es el mayor número de esa serie y año del
    contador que consta en todos esos meses + 1, y cada fila dice qué meses se comprobaron. Si alguno no puede
    consultarse de forma completa y fiable, no se propone nada (y se dice por qué)."""
    usados = cmp_["_usados"]
    if not usados:
        return
    meses, completo = _meses_avance(ejercicio, periodo, any(k[1] == 0 for k in usados))
    mayores = {k: max(f["numero_aeat"] for f in filas) for k, filas in usados.items()}
    motivo = None
    if not completo:
        # Una serie sin año sigue numerando en los años siguientes: sin comprobar hasta el mes actual, el mayor número
        # usado puede estar en un mes que no se ha mirado
        sin_anio = sorted({f"«{f['serie_codigo']}»" for k, filas in usados.items() if k[1] == 0 for f in filas})
        cuales = (f"la serie {sin_anio[0]} no lleva año" if len(sin_anio) == 1 else
                  f"las series {', '.join(sin_anio)} no llevan año")
        ej, m = meses[-1]
        motivo = (f"no se han comprobado los meses posteriores a {m}/{ej} (la comprobación abarca como mucho "
                  f"{MAX_MESES_AVANCE} meses y {cuales}: su numeración sigue en los años siguientes). Consulte un mes "
                  "más reciente para que se proponga el siguiente número")
        meses = []
    for ej, m in meses[1:]:
        try:
            res = consultar_aeat(con, empresa, usuario_id, entorno, ej, m, comprobado=True)
        except ErrorConsulta as e:
            motivo = f"no se pudo consultar el mes {m}/{ej} ({e}). Vuelva a consultar"
            break
        evidencias.append(res["carpeta"])
        if res["parcial"] or res["respuesta_ajena"] or res["errores_xsd"]:
            motivo = f"la respuesta del mes {m}/{ej} no es completa o no corresponde a lo consultado. Vuelva a consultar"
            break
        registros.extend(res["registros"])
        for a in res["registros"]:
            if a.id_emisor != empresa["nif"]:
                continue
            anio = int(a.fecha_expedicion[-4:]) if a.fecha_expedicion[-4:].isdigit() else 0
            s = series.identificar(con, empresa["id"], entorno, a.num_serie, anio)
            if s and (s["id"], s["anio_contador"]) in mayores:
                k = (s["id"], s["anio_contador"])
                mayores[k] = max(mayores[k], s["numero_detectado"])
    textos = [f"{m}/{ej}" for ej, m in meses]
    for k, filas in usados.items():
        for f in filas:
            f.pop("siguiente_propuesto", None)
            if motivo is None and mayores[k] + 1 <= series.MAX_NUMERO:
                f.update(siguiente_propuesto=mayores[k] + 1, meses_comprobados=textos)
    cmp_["avance"] = {"meses_comprobados": textos if motivo is None else [], "fiable": motivo is None, "motivo": motivo}


def consultar_y_comparar(con, empresa: dict, usuario_id: int, entorno: str, ejercicio: str, periodo: str,
                         num_serie: str | None = None) -> dict:
    res = consultar_aeat(con, empresa, usuario_id, entorno, ejercicio, periodo, num_serie)
    parcial = res["parcial"]
    cmp_ = comparar(con, empresa, entorno, ejercicio, periodo, num_serie, res["registros"], parcial=parcial)
    evidencias = [res["carpeta"]]
    registros = list(res["registros"])
    fiable = not (parcial or res["respuesta_ajena"] or res["errores_xsd"])
    if fiable and not num_serie:
        _proponer_avance(con, empresa, usuario_id, entorno, ejercicio, periodo, cmp_, evidencias, registros)
    else:
        # Una consulta de un solo número, parcial o no fiable no dice cuál es el mayor número usado: no se propone avanzar
        for x in cmp_["solo_aeat"]:
            x.pop("siguiente_propuesto", None)
            if num_serie and x.get("serie_id") and fiable:
                x["nota"] += (" Para que se proponga el siguiente número, consulte el mes completo (sin «Número de "
                              "factura»): se comprueban también los meses siguientes.")
    cmp_.pop("_usados", None)
    if res["respuesta_ajena"]:
        # La respuesta es de otro obligado o periodo: nada se ofrece conciliar (la conciliación también lo rechaza)
        for x in cmp_["distintos"]:
            if x.get("conciliable"):
                x.update(conciliable=False, revisar=True, estado_propuesto=None, estado_propuesto_texto=None,
                         motivo=x["motivo"] + " No se ofrece conciliar: la respuesta no corresponde a lo consultado.")
        _resumir(cmp_)
    avisos = list(res["avisos"])
    if (cmp_.get("avance") or {}).get("motivo"):
        avisos.append(f"No se propone avanzar el contador de las series: {cmp_['avance']['motivo']}.")
    codigos = ["consulta_parcial_representante"] if parcial else []
    cadena = None
    if fiable:
        # Registros de esta instalación posteriores al último registro local: la cadena está atrasada
        post = posteriores_a_la_cabeza(con, empresa, entorno, registros)
        if post:
            cadena = _resumen_cadena(post, cabeza_local(con, empresa["id"], entorno))
            huellas = {a.huella for a in post}
            for k in ("solo_aeat", "distintos"):
                for x in cmp_[k]:
                    if x.get("huella_aeat") in huellas:
                        x["posterior_a_la_cadena"] = True
                        x["revisar"] = True
                        texto = (f"Registro posterior al último registro de esta instalación: la cadena local está "
                                 f"atrasada respecto {servicio(entorno, prep='a')} (vea el aviso).")
                        x["nota" if k == "solo_aeat" else "motivo"] = (x.get("nota" if k == "solo_aeat" else "motivo") or "") + " " + texto
            _resumir(cmp_)
            registrar_cadena_atrasada(con, empresa, usuario_id, entorno, cadena, evidencias)
            codigos.append("cadena_atrasada")
            avisos.insert(0, mensaje_cadena_atrasada(cadena, entorno) + (
                f" {servicio(entorno, mayuscula=True)} tiene {cadena['registros_posteriores']} registro(s) posteriores.")
                + (" " + texto_ambigua(cadena, entorno) if cadena["ambigua"] else ""))
            if cadena["ambigua"]:
                codigos.append("cadena_ambigua")
        anuladas = anulaciones_sin_huella(con, empresa, entorno, registros)
        if anuladas:
            codigos.append("anulacion_sin_huella")
            avisos.append(texto_anulaciones_sin_huella(anuladas, entorno))
    est = estado_cadena(con, empresa["id"], entorno)
    out = {"entorno": entorno, "ejercicio": ejercicio, "periodo": periodo, "num_serie": num_serie or None,
           "consultado_en": db.ahora_iso(), "paginas": res["paginas"], "destino": res["destino"],
           "servicio": servicio(entorno), "evidencia": res["carpeta"], "evidencias": evidencias, "avisos": avisos,
           "codigos_aviso": codigos, "respuesta_ajena": res["respuesta_ajena"], "parcial": parcial,
           "indicador_representante": res["indicador_representante"], "cadena_atrasada": cadena,
           "estado_cadena": ({k: est[k] for k in ("estado", "num_serie", "fecha_expedicion", "fecha_hora_huso", "huella")}
                             if est else None),
           "registros_aeat": len(res["registros"]), **cmp_}
    with db.transaccion(con):
        db.actividad(con, "aeat.consulta", usuario_id=usuario_id, empresa_id=empresa["id"],
                     detalle={"entorno": entorno, "ejercicio": ejercicio, "periodo": periodo, "num_serie": num_serie or None,
                              "paginas": res["paginas"], "resumen": cmp_["resumen"], "evidencia": res["carpeta"],
                              "parcial": parcial, "cadena_atrasada": bool(cadena),
                              "meses_avance": (cmp_.get("avance") or {}).get("meses_comprobados")})
    if fiable and not num_serie:
        _anotar_consulta_tras_restauracion(empresa["id"], entorno, ejercicio, periodo)
    return out


def _anotar_consulta_tras_restauracion(empresa_id: int, entorno: str, ejercicio: str, periodo: str) -> None:
    """Si la empresa estaba pendiente de consultar tras una restauración sin diario (diario.pendientes_sin_diario), anota
    en el diario el mes que ha consultado. Solo se llama con una consulta de un mes completo y fiable (ni parcial, ni
    ajena, ni fuera de esquema): las demás no pueden detectar lo que se perdió. Cuando ha consultado todos los meses desde
    el de la copia en sus entornos, el aviso deja de repetirse en las restauraciones siguientes. Si no puede anotarse, el
    aviso solo se repite de más (nunca de menos): no se interrumpe la consulta."""
    try:
        if empresa_id in diario.pendientes_actuales():
            diario.anotar("consulta_aeat", {"empresa_id": empresa_id, "entorno": entorno, "mes": f"{ejercicio}-{periodo}"})
    except (OSError, ValueError):
        pass


# --- Conciliación segura ---------------------------------------------------------------------------------------------
def conciliar(con, empresa: dict, usuario_id: int, entorno: str, registro_ids: list[int]) -> dict:
    """Marca como aceptados los registros locales sin respuesta definitiva (o rechazados por duplicado 3000) que la AEAT
    tiene con la misma huella.
    Vuelve a consultar a la AEAT (no se fía de una consulta anterior ni de lo que envíe el navegador), lo hace con el
    turno del remitente tomado (ningún envío de esa empresa y entorno en curso), que renueva antes de cada página y
    comprueba que sigue siendo suyo dentro de la transacción en la que escribe, y solo cambia los registros que siguen
    en un estado no definitivo (nunca uno «enviando»). Cada cambio se anota en la actividad y en el diario."""
    if not isinstance(registro_ids, list) or not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= MAX_ID
                                                     for x in registro_ids):
        raise ErrorConsulta("«registro_ids» debe ser una lista de identificadores (enteros positivos)", 422)
    _comprobar_entorno(con, empresa, usuario_id, entorno)
    ids = list(dict.fromkeys(registro_ids))
    if not ids or len(ids) > 1000:
        raise ErrorConsulta("Indique entre 1 y 1000 registros", 422)
    regs = {}
    for rid in ids:
        r = db.fila(con, "SELECT * FROM registros WHERE id=?", (rid,))
        if not r or r["empresa_id"] != empresa["id"] or r["entorno"] != entorno:
            raise ErrorConsulta("Registro no encontrado", 404)
        regs[rid] = r
    serv, Serv = servicio(entorno), servicio(entorno, mayuscula=True)
    quien = f"conciliacion-{secrets.token_hex(4)}"
    cf = remision._tomar_turno(con, empresa["id"], entorno, quien)
    if cf is None:
        raise ErrorConsulta("Hay un envío en curso para esta empresa y entorno; inténtelo de nuevo en unos segundos.", 409)
    conciliados, no_conciliados, cambios_f, incidencia_cerrada = [], [], {}, False

    def renovar() -> bool:
        """Renueva el plazo del turno antes de cada página (una consulta larga no lo deja caducar); False si otro lo tomó."""
        return remision._tomar_turno(con, empresa["id"], entorno, quien) is not None
    try:
        periodos = sorted({(r["fecha_expedicion"][6:10], r["fecha_expedicion"][3:5]) for r in regs.values()})
        aeat: dict[tuple[str, str], rcmod.RegistroConsultado] = {}
        evidencias = []
        parcial = False
        for ejercicio, periodo in periodos:
            res = consultar_aeat(con, empresa, usuario_id, entorno, ejercicio, periodo, comprobado=True, renovar=renovar)
            if res["errores_xsd"]:
                raise ErrorConsulta(f"La respuesta {servicio(entorno, prep='de')} no cumple el esquema RespuestaConsultaLR.xsd: no se concilia "
                                    "nada. " + "; ".join(res["errores_xsd"][:2]))
            if res["respuesta_ajena"]:
                raise ErrorConsulta(f"La respuesta {servicio(entorno, prep='de')} no corresponde a lo consultado (" + "; ".join(res["respuesta_ajena"])
                                    + "): no se concilia nada.")
            parcial = parcial or res["parcial"]
            evidencias.append(res["carpeta"])
            for a in res["registros"]:
                if a.id_emisor == empresa["nif"]:
                    aeat[a.clave()] = a
        with db.transaccion(con):
            # Dentro de la transacción de escritura (BEGIN IMMEDIATE: nadie puede tomar el turno mientras tanto), el turno
            # tiene que seguir siendo de esta conciliación. Si caducó y otro remitente lo tomó (aunque ya lo haya soltado),
            # no se cambia nada: ese envío pudo cambiar los registros después de la consulta.
            cf = db.fila(con, "SELECT bloqueado_por FROM control_flujo WHERE empresa_id=? AND entorno=?", (empresa["id"], entorno))
            if not cf or cf["bloqueado_por"] != quien:
                raise ErrorConsulta("El turno del remitente pasó a otro proceso durante la consulta: no se ha conciliado nada. "
                                    "Vuelva a intentarlo en unos segundos.", 409)
            for rid, r0 in regs.items():
                r = db.fila(con, "SELECT * FROM registros WHERE id=?", (rid,))
                ultimo = db.fila(con, "SELECT id FROM registros WHERE factura_id=? ORDER BY secuencia DESC LIMIT 1", (r["factura_id"],))
                a = aeat.get((r["num_serie"], r["fecha_expedicion"]))
                motivo = None
                if r["estado_envio"] == "enviando":
                    motivo = ("Se está enviando (o su envío se interrumpió y la cola lo reintentará): no se concilia mientras "
                              "tanto. Vuelva a consultar cuando termine.")
                elif not _conciliable_local(r):
                    motivo = f"El registro ya tiene estado definitivo («{remision.TEXTO_ESTADO.get(r['estado_envio'])}»)."
                elif ultimo["id"] != rid:
                    motivo = "No es el último registro de su factura."
                elif a is None:
                    motivo = (f"La respuesta {servicio(entorno, prep='de')} es parcial (solo los registros que presentó el representante) y no "
                              "devuelve esta factura: no se concilia." if parcial else f"{Serv} no tiene esta factura.")
                elif a.huella != r["huella"]:
                    motivo = f"{Serv} tiene esta factura con otra huella."
                elif not _coherente(r["tipo"], r["estado_envio"], a.estado):
                    motivo = f"El estado en {serv} («{a.estado}») no corresponde a este registro ({r['tipo']})."
                if motivo:
                    no_conciliados.append({"registro_id": rid, "num_serie": r["num_serie"], "motivo": motivo})
                    continue
                nuevo = _estado_propuesto(r["tipo"], a.estado)
                desc = (f"Conciliado con la consulta {servicio(entorno, prep='a')} del {db.legible(db.ahora_iso())}: {serv} tiene este registro con la misma "
                        f"huella y estado «{a.estado}»" + (f" (IdPeticion {a.id_peticion})" if a.id_peticion else "") + ".")
                if a.descripcion_error:
                    desc += f" {a.descripcion_error}"
                if r["estado_envio"] == "rechazado":
                    desc += " Antes figuraba rechazado por duplicado (3000) al reenviarlo."
                cur = con.execute(f"""UPDATE registros SET estado_envio=?, codigo_error=?, descripcion_error=?, siguiente_intento_en=NULL,
                                      actualizado_en=? WHERE id=? AND {SQL_CONCILIABLE}""",
                                  (nuevo, a.codigo_error if nuevo == "aceptado_con_errores" else None, desc[:500], db.utc_iso(), rid))
                if cur.rowcount != 1:
                    no_conciliados.append({"registro_id": rid, "num_serie": r["num_serie"], "motivo": "El estado cambió durante la conciliación."})
                    continue
                from .emision import recalcular_estado_factura
                est_f = recalcular_estado_factura(con, r["factura_id"])
                if est_f:
                    cambios_f[r["factura_id"]] = est_f
                orig = db.fila(con, "SELECT rectifica_factura_id FROM facturas WHERE id=?", (r["factura_id"],))
                if orig and orig["rectifica_factura_id"]:      # como la remisión: la factura rectificada también
                    est_o = recalcular_estado_factura(con, orig["rectifica_factura_id"])
                    if est_o:
                        cambios_f[orig["rectifica_factura_id"]] = est_o
                db.actividad(con, "registro.conciliado_aeat", usuario_id=usuario_id, empresa_id=empresa["id"], objeto="registro",
                             objeto_id=rid, detalle={"factura_id": r["factura_id"], "num_serie": r["num_serie"],
                                                     "entorno": entorno, "servicio": serv,
                                                     "estado_anterior": r0["estado_envio"], "estado_nuevo": nuevo,
                                                     "huella": r["huella"], "estado_aeat": a.estado,
                                                     "id_peticion": a.id_peticion, "evidencia": evidencias})
                conciliados.append({"registro_id": rid, "factura_id": r["factura_id"], "num_serie": r["num_serie"],
                                    "estado_anterior": r0["estado_envio"], "estado_nuevo": nuevo})
            # La AEAT ha respondido: si ya no queda nada sin respuesta definitiva, la incidencia de comunicación queda
            # cerrada, como tras un envío con respuesta (si no, las facturas siguientes irían con Incidencia=S).
            incidencia_cerrada = remision.cerrar_incidencia_resuelta(con, empresa["id"], entorno) > 0
            if incidencia_cerrada:
                db.actividad(con, "remision.incidencia_cerrada", usuario_id=usuario_id, empresa_id=empresa["id"],
                             detalle={"entorno": entorno, "motivo": "conciliacion_aeat", "evidencia": evidencias})
    finally:
        remision._soltar_turno(con, empresa["id"], entorno, quien)
    # Diario de recuperación: el nuevo estado sobrevive a una restauración (como cualquier respuesta de la AEAT)
    from . import diario
    for c in conciliados:
        remision._anotar_estado(con, c["registro_id"])
    for fid, est in cambios_f.items():
        try:
            diario.anotar("estado_factura", {"id": fid, "estado": est})
        except Exception:  # noqa: BLE001 — la conciliación del arranque lo completará
            pass
    return {"conciliados": conciliados, "no_conciliados": no_conciliados, "incidencia_cerrada": incidencia_cerrada}


# --- Reanudar la cadena desde el último registro de la AEAT ---------------------------------------------------------
def meses_cadena(con, empresa_id: int, entorno: str) -> list[tuple[str, str]]:
    """(ejercicio, periodo) que hay que consultar para conocer el último registro de la cadena en la AEAT: todos los meses
    desde el del último registro local (o del ancla) hasta el actual, porque lo perdido se expidió después; los meses de
    las facturas locales anteriores, porque una anulación o una subsanación posterior de una de ellas también es un
    registro de la cadena (con la fecha de expedición de esa factura); y el del registro ya detectado. Como mucho
    MAX_MESES_CADENA, los más recientes."""
    hoy = db.ahora()
    cab = cabeza_local(con, empresa_id, entorno)
    meses: set[tuple[int, int]] = set()
    t0 = _dt(cab["fecha_hora_huso"])
    if t0 is None:
        # Sin registros locales (copia anterior al primero): desde el alta de la empresa en la aplicación
        emp = db.fila(con, "SELECT creado_en FROM empresas WHERE id=?", (empresa_id,))
        t0 = _dt((emp or {}).get("creado_en")) or hoy
    a, m = t0.year, t0.month
    while (a, m) <= (hoy.year, hoy.month):
        meses.add((a, m))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    for f in db.filas(con, """SELECT DISTINCT substr(fecha_expedicion, 1, 7) ym FROM facturas WHERE empresa_id=? AND entorno=?""",
                      (empresa_id, entorno)):
        if re.fullmatch(r"\d{4}-\d{2}", f["ym"] or ""):
            meses.add((int(f["ym"][:4]), int(f["ym"][5:7])))
    est = cab["estado"]
    if est and re.fullmatch(r"\d{2}-\d{2}-\d{4}", est["fecha_expedicion"] or ""):
        meses.add((int(est["fecha_expedicion"][6:10]), int(est["fecha_expedicion"][3:5])))
    validos = sorted(x for x in meses if EJERCICIO_MIN <= x[0] <= EJERCICIO_MAX and x <= (hoy.year, hoy.month))
    return [(f"{a:04d}", f"{m:02d}") for a, m in validos[-MAX_MESES_CADENA:]]


def reanudar_cadena(con, empresa: dict, usuario_id: int, entorno: str, huella_revisada) -> dict:
    """Reanuda la cadena de la empresa en ese entorno desde el último registro que tiene la AEAT (tras perder la base de
    datos y el diario y restaurar una copia antigua). Vuelve a consultar en el servidor los meses de `meses_cadena` (desde
    el del último registro local hasta el actual, los de las facturas locales y el del registro detectado), todas las
    páginas, sin IndicadorRepresentante; nunca usa datos del navegador salvo la huella que el
    administrador revisó, que debe coincidir con la del último registro posterior que devuelve la consulta. Guarda el
    ancla (cadenas_aeat, estado «anclada»): el siguiente registro la usa como RegistroAnterior y la fila desaparece al
    encadenarse. Se audita (`cadena.reanudada_aeat`) y se anota en el diario (`cadena_aeat`) en la misma transacción.
    Idempotente: repetirlo con la misma huella no cambia nada."""
    if not isinstance(huella_revisada, str) or not re.fullmatch(r"[0-9A-F]{64}", huella_revisada):
        raise ErrorConsulta("Indique la huella (64 caracteres hexadecimales en mayúsculas) del registro que ha revisado", 422)
    _comprobar_entorno(con, empresa, usuario_id, entorno)
    serv, Serv = servicio(entorno), servicio(entorno, mayuscula=True)
    cab = cabeza_local(con, empresa["id"], entorno)
    est = cab["estado"]
    meses = meses_cadena(con, empresa["id"], entorno)
    consultados = [f"{m}/{a}" for a, m in meses]
    registros, evidencias = [], []
    for ejercicio, periodo in meses:
        res = consultar_aeat(con, empresa, usuario_id, entorno, ejercicio, periodo, comprobado=True)
        evidencias.append(res["carpeta"])
        if res["parcial"]:
            raise ErrorConsulta(f"La respuesta {servicio(entorno, prep='de')} es parcial (solo los registros que presentó el representante): no "
                                "permite saber cuál es el último registro de la cadena. Consulte con un certificado propio "
                                "de la empresa o de un apoderado.", 409)
        if res["respuesta_ajena"] or res["errores_xsd"]:
            raise ErrorConsulta(f"La respuesta {servicio(entorno, prep='de')} no corresponde a lo consultado o no cumple el esquema: no se "
                                "reanuda la cadena.", 409)
        registros.extend(res["registros"])
    post = posteriores_a_la_cabeza(con, empresa, entorno, registros)
    ancla_actual = cab["ancla"]
    if not post:
        if ancla_actual and ancla_actual["huella"] == huella_revisada:
            return {"reanudada": True, "ya_reanudada": True, "ancla": _publico(ancla_actual), "evidencias": evidencias,
                    "meses_consultados": consultados}
        if est and est["estado"] == "atrasada":
            try:
                with db.transaccion(con):
                    con.execute("DELETE FROM cadenas_aeat WHERE empresa_id=? AND entorno=? AND estado='atrasada'",
                                (empresa["id"], entorno))
                    db.actividad(con, "cadena.atrasada_resuelta", usuario_id=usuario_id, empresa_id=empresa["id"],
                                 detalle={"entorno": entorno, "motivo": "sin_registros_posteriores", "evidencia": evidencias})
                    diario.anotar("cadena_aeat", {"empresa_id": empresa["id"], "entorno": entorno, "estado": "resuelta",
                                                  "huella": est["huella"], "resuelta_en": db.ahora_iso()})
            except OSError as e:
                raise ErrorConsulta("No se pudo anotar el cambio en el diario de recuperación: no se ha cambiado nada.", 503) from e
            return {"reanudada": False, "ya_reanudada": False, "ancla": None, "evidencias": evidencias,
                    "meses_consultados": consultados, "mensaje": f"{Serv} ya no tiene registros posteriores al último de esta instalación: no hace falta "
                               "reanudar la cadena."}
        raise ErrorConsulta(f"{Serv} no tiene registros de esta instalación posteriores al último registro local: no hay "
                            "nada que reanudar.", 409)
    cab_res = _resumen_cadena(post, cab)
    anuladas = anulaciones_sin_huella(con, empresa, entorno, registros)
    if cab_res["ambigua"] or anuladas:
        registrar_cadena_atrasada(con, empresa, usuario_id, entorno, cab_res, evidencias)
        raise ErrorConsulta(texto_ambigua(cab_res, entorno) if cab_res["ambigua"] else
                            texto_anulaciones_sin_huella(anuladas, entorno) + " No se reanuda la cadena.", 409)
    if cab_res["huella"] != huella_revisada:
        registrar_cadena_atrasada(con, empresa, usuario_id, entorno, cab_res, evidencias)
        raise ErrorConsulta(f"El último registro posterior que tiene {serv} es ahora {cab_res['num_serie']} del "
                            f"{_dia(cab_res['fecha_expedicion'])} (generado el {db.legible(cab_res['fecha_hora_huso'])}, huella "
                            f"{cab_res['huella'][:16]}…), no el que revisó. Vuelva a consultar y revíselo antes de reanudar.", 409)
    try:
        with db.transaccion(con):
            # Nada se ha encadenado aquí desde la consulta (la cabeza local sigue siendo la misma)
            ahora = cabeza_local(con, empresa["id"], entorno)
            if (ahora["registro"] or {}).get("id") != (cab["registro"] or {}).get("id"):
                raise ErrorConsulta("Se ha generado un registro en esta instalación durante la consulta: vuelva a consultar "
                                    "antes de reanudar la cadena.", 409)
            con.execute("""INSERT INTO cadenas_aeat (empresa_id, entorno, estado, id_emisor, num_serie, fecha_expedicion,
                           fecha_hora_huso, huella, tipo, registros_posteriores, cabeza_local, detectada_en, evidencia,
                           anclada_en, anclada_por) VALUES (?,?,'anclada',?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(empresa_id, entorno) DO UPDATE SET estado='anclada', id_emisor=excluded.id_emisor,
                           num_serie=excluded.num_serie, fecha_expedicion=excluded.fecha_expedicion,
                           fecha_hora_huso=excluded.fecha_hora_huso, huella=excluded.huella, tipo=excluded.tipo,
                           registros_posteriores=excluded.registros_posteriores, cabeza_local=excluded.cabeza_local,
                           evidencia=excluded.evidencia, anclada_en=excluded.anclada_en, anclada_por=excluded.anclada_por""",
                        (empresa["id"], entorno, cab_res["id_emisor"], cab_res["num_serie"], cab_res["fecha_expedicion"],
                         cab_res["fecha_hora_huso"], cab_res["huella"], cab_res["tipo"], cab_res["registros_posteriores"],
                         (cab_res["cabeza_local"] or {}).get("fecha_hora_huso"), db.ahora_iso(), json.dumps(evidencias),
                         db.ahora_iso(), usuario_id))
            db.actividad(con, "cadena.reanudada_aeat", usuario_id=usuario_id, empresa_id=empresa["id"],
                         detalle={"entorno": entorno, "servicio": serv, "ancla": {k: cab_res[k] for k in (
                             "num_serie", "fecha_expedicion", "fecha_hora_huso", "huella", "estado_aeat")},
                                  "registros_posteriores": cab_res["registros_posteriores"],
                                  "cabeza_local": cab_res["cabeza_local"], "evidencia": evidencias})
            # Diario de recuperación, antes de confirmar: si no puede anotarse, no se cambia nada
            diario.anotar("cadena_aeat", estado_cadena(con, empresa["id"], entorno))
    except OSError as e:
        raise ErrorConsulta("No se pudo anotar la reanudación en el diario de recuperación: no se ha cambiado nada.", 503) from e
    return {"reanudada": True, "ya_reanudada": False, "ancla": _publico(estado_cadena(con, empresa["id"], entorno)),
            "registros_posteriores": cab_res["registros_posteriores"], "cabeza_local": cab_res["cabeza_local"],
            "evidencias": evidencias, "meses_consultados": consultados}


def _publico(est: dict | None) -> dict | None:
    return {k: est[k] for k in ("estado", "id_emisor", "num_serie", "fecha_expedicion", "fecha_hora_huso", "huella",
                                "anclada_en")} if est else None
