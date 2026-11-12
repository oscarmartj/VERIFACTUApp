"""Cola persistente de remisión a la AEAT (o al simulador local), con control de flujo.

- La cola son los propios registros en estado pendiente / sin_confirmar: sobrevive a reinicios.
- Orden estricto de generación: si el registro más antiguo sin respuesta definitiva no puede enviarse
  todavía (espera de reintento) o está bloqueado, no se envía ninguno posterior (art. 16.4 OM).
- Un único remitente por empresa y entorno (bloqueo con caducidad en control_flujo).
- Control de flujo: tras cada envío que pudo llegar a la AEAT se respeta TiempoEsperaEnvio, salvo que se
  hayan acumulado 1000 registros. Máximo 1000 registros por envío. Reintentos al menos cada hora.
- Reintentar una comunicación reenvía exactamente el mismo XML (misma huella y marca temporal): no
  crea factura, número ni registro nuevos.
- Sin respuesta ≠ rechazo: el registro queda «sin confirmar» y marcado como de posible entrega; si al
  reenviarlo la AEAT responde «duplicado» (3000) con estado Correcta/AceptadaConErrores, eso confirma el
  resultado. Un 3000 sin intento previo de posible entrega se trata como rechazo que requiere revisión.
- Todas las marcas temporales de planificación se guardan en UTC (evita errores en el cambio de hora).
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
from datetime import datetime, timedelta, timezone

import requests
from urllib3.exceptions import NewConnectionError

from . import almacen, certificados, db, diario, simulador
from .config import ENDPOINTS_SOAP, ajustes
from .verifactu import esquemas_xsd, respuesta as respmod, xmlgen

ESTADOS_COLA = ("pendiente", "sin_confirmar")
ESTADOS_NO_DEFINITIVOS = ("pendiente", "sin_confirmar", "bloqueado", "enviando")
TEXTO_ESTADO = {
    "pendiente": "Envío pendiente", "enviando": "Enviando", "sin_confirmar": "Resultado sin confirmar",
    "aceptado": "Registro aceptado", "aceptado_con_errores": "Aceptado con errores", "rechazado": "Rechazado",
    "bloqueado": "Bloqueado (requiere acción)",
}
REINTENTO_MAX_S = 3600  # «al menos una vez cada hora» (art. 16.4 OM)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _ts(v: str | None) -> datetime | None:
    """Interpreta una marca ISO 8601 con desfase (UTC o local antigua) para comparar instantes reales."""
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _futuro(v: str | None) -> bool:
    d = _ts(v)
    return bool(d and d > _ahora())


def _anotar_estado(con, rid: int) -> None:
    """Anotación en el diario tras confirmar en la base: si falla (disco), no altera el resultado ya registrado;
    la conciliación del arranque completará el diario."""
    try:
        diario.anotar_estado(con, rid)
    except Exception as e:  # noqa: BLE001
        try:
            db.actividad(con, "diario.error", detalle={"registro_id": rid, "error": type(e).__name__})
        except Exception:  # noqa: BLE001
            pass


def _backoff(intentos: int) -> int:
    return min(60 * 2 ** max(intentos - 1, 0), REINTENTO_MAX_S)


# --- Transporte --------------------------------------------------------------------------------
def _post(url: str, sobre: bytes, cp: str, kp: str) -> tuple[int, bytes]:
    """POST SOAP con certificado cliente. Clasifica los fallos según si la petición pudo llegar o no."""
    a = ajustes()
    try:
        r = requests.post(url, data=sobre, cert=(cp, kp), timeout=(15, a.timeout_http_s),
                          headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": ""})
    except requests.exceptions.ConnectTimeout as e:
        raise simulador.NoEnviado("No se pudo establecer la conexión (tiempo agotado)") from e
    except requests.exceptions.ProxyError as e:
        raise simulador.NoEnviado(f"El proxy no pudo conectar con el servicio: {type(e).__name__}") from e
    except requests.exceptions.SSLError as e:
        texto = str(e).lower()
        if any(k in texto for k in ("handshake", "certificate", "certificado", "alert", "tlsv1", "wrong version")):
            raise simulador.NoEnviado(f"Fallo en la negociación TLS: {type(e).__name__}") from e
        raise simulador.SinRespuesta(f"Conexión TLS interrumpida: {type(e).__name__}") from e
    except requests.exceptions.ConnectionError as e:
        razon = getattr(e.args[0], "reason", None) if e.args else None
        if isinstance(razon, NewConnectionError) or isinstance(razon, (ConnectionRefusedError, socket.gaierror)):
            raise simulador.NoEnviado(f"Conexión rechazada o servidor no resoluble: {type(razon).__name__}") from e
        raise simulador.SinRespuesta(f"Conexión interrumpida: {type(razon or e).__name__}") from e
    except requests.exceptions.ReadTimeout as e:
        raise simulador.SinRespuesta("Tiempo de espera agotado sin respuesta") from e
    except requests.exceptions.RequestException as e:
        raise simulador.SinRespuesta(f"Error de comunicación: {type(e).__name__}") from e
    return r.status_code, r.content


def _preparar_destino(con, entorno: str, empresa_id: int, cert: dict | None = None, error_cert: str | None = None,
                      operacion: str = "consulta") -> tuple[str, dict, dict | None]:
    """(URL, certificado, representante) para comunicarse con la AEAT, tras los interruptores del servidor.

    Con `cert` (el que dio la cabecera del mensaje de remisión) no se vuelve a elegir: TLS y Cabecera/Representante
    corresponden siempre al mismo certificado; quien lo pasa audita el uso (`registrar_uso` con el envío).
    Sin `cert` (otros servicios, p. ej. la consulta a la AEAT) se elige con la misma regla, `certificados.para_envio`, y
    se devuelve también el representante ({nif, nombre}; la consulta va sin IndicadorRepresentante y solo lo pone al
    repetirse tras un 4140, ver consulta.py). El uso se audita
    solo, como `operacion`, cuando el certificado se descifra en `certificados.ficheros_pem` justo antes de TLS (una
    anotación por conexión; no hace falta llamar a `registrar_uso`)."""
    a = ajustes()
    if entorno == "pruebas" and not a.permitir_pruebas_aeat:
        raise simulador.NoEnviado("Remisión al entorno de pruebas deshabilitada en el servidor")
    if entorno == "produccion" and not a.permitir_produccion:
        raise simulador.NoEnviado("Remisión a producción deshabilitada en el servidor")
    if error_cert:
        raise simulador.NoEnviado(error_cert)
    if cert is None:
        try:
            cert, _ = certificados.para_envio(con, db.fila(con, "SELECT * FROM empresas WHERE id=?", (empresa_id,)), entorno)
        except certificados.CertificadoNoUtilizable as e:
            raise simulador.NoEnviado(str(e)) from e
        certificados.auditar_al_usar(con, cert, empresa_id, entorno, operacion)
    representante = (cert.get("legitimacion_dict") or {}).get("representante")
    return ENDPOINTS_SOAP[entorno]["sello" if cert["tipo"] == "persona_juridica" else "certificado"], cert, representante


# --- Remitente ---------------------------------------------------------------------------------
def _tomar_turno(con, empresa_id: int, entorno: str, quien: str) -> dict | None:
    ahora = _ahora()
    with db.transaccion(con):
        cf = db.fila(con, "SELECT * FROM control_flujo WHERE empresa_id=? AND entorno=?", (empresa_id, entorno))
        if not cf:
            con.execute("INSERT INTO control_flujo (empresa_id, entorno, tiempo_espera) VALUES (?,?,?)",
                        (empresa_id, entorno, ajustes().espera_inicial_s))
            cf = db.fila(con, "SELECT * FROM control_flujo WHERE empresa_id=? AND entorno=?", (empresa_id, entorno))
        if cf["bloqueo_hasta"] and _futuro(cf["bloqueo_hasta"]) and cf["bloqueado_por"] != quien:
            return None
        con.execute("UPDATE control_flujo SET bloqueo_hasta=?, bloqueado_por=? WHERE empresa_id=? AND entorno=?",
                    (_iso(ahora + timedelta(seconds=ajustes().timeout_http_s + 60)), quien, empresa_id, entorno))
        return cf


def _soltar_turno(con, empresa_id, entorno, quien):
    con.execute("UPDATE control_flujo SET bloqueo_hasta=NULL, bloqueado_por=NULL WHERE empresa_id=? AND entorno=? AND bloqueado_por=?",
                (empresa_id, entorno, quien))


def recuperar_interrumpidos(con) -> int:
    """Registros que quedaron «enviando» con el bloqueo del remitente caducado (reinicio o caída a mitad de envío):
    pasan a «sin confirmar» con posible entrega (no se sabe si llegaron), con espera de reintento y respetando el
    control de flujo. Se ejecuta en cada ciclo."""
    ids = []
    ahora = _ahora()
    with db.transaccion(con):
        for r in db.filas(con, """SELECT r.id, r.empresa_id, r.entorno, r.intentos, c.bloqueo_hasta, c.tiempo_espera FROM registros r
                                 LEFT JOIN control_flujo c ON c.empresa_id=r.empresa_id AND c.entorno=r.entorno
                                 WHERE r.estado_envio='enviando'"""):
            if r["bloqueo_hasta"] and _futuro(r["bloqueo_hasta"]):
                continue
            con.execute("""UPDATE registros SET estado_envio='sin_confirmar', posible_entrega=1, incidencia=1, siguiente_intento_en=?,
                           actualizado_en=?, descripcion_error='Envío interrumpido sin respuesta; se reenviará' WHERE id=? AND estado_envio='enviando'""",
                        (_iso(ahora + timedelta(seconds=_backoff(r["intentos"]))), db.utc_iso(), r["id"]))
            con.execute("UPDATE control_flujo SET proximo_envio_en=?, incidencia_desde=COALESCE(incidencia_desde, ?) WHERE empresa_id=? AND entorno=?",
                        (_iso(ahora + timedelta(seconds=r["tiempo_espera"] or 60)), _iso(ahora), r["empresa_id"], r["entorno"]))
            ids.append(r["id"])
        for e in db.filas(con, "SELECT id, iniciado_en FROM envios WHERE finalizado_en IS NULL"):
            ini = _ts(e["iniciado_en"])
            if ini and ini < ahora - timedelta(seconds=ajustes().timeout_http_s + 60):
                con.execute("UPDATE envios SET finalizado_en=?, resultado='interrumpido' WHERE id=?", (_iso(ahora), e["id"]))
    for i_ in ids:
        _anotar_estado(con, i_)
    return len(ids)


def _lote(con, empresa_id: int, entorno: str) -> tuple[list[dict], dict | None]:
    """Prefijo enviable de la cola en orden de generación. Devuelve (lote, motivo_de_espera)."""
    pendientes = db.filas(con, f"""SELECT * FROM registros WHERE empresa_id=? AND entorno=? AND estado_envio IN {ESTADOS_NO_DEFINITIVOS}
                                  ORDER BY secuencia LIMIT ?""", (empresa_id, entorno, ajustes().max_registros_envio))
    if not pendientes:
        return [], None
    primero = pendientes[0]
    if primero["estado_envio"] == "bloqueado":
        return [], {"accion": "bloqueado", "registro_id": primero["id"]}
    if primero["estado_envio"] == "enviando":
        return [], {"accion": "ocupado"}
    if _futuro(primero["siguiente_intento_en"]):
        return [], {"accion": "esperando_reintento", "siguiente_intento_en": primero["siguiente_intento_en"]}
    lote = []
    for r in pendientes:
        if r["estado_envio"] not in ESTADOS_COLA or _futuro(r["siguiente_intento_en"]):
            break
        if r["incidencia"] != primero["incidencia"]:  # los registros remitidos tras una incidencia van con Incidencia=S
            break
        lote.append(r)
    return lote, None


def procesar(con, empresa_id: int, entorno: str, *, quien: str = "remitente", forzar_flujo: bool = False) -> dict:
    """Realiza como máximo un envío para la empresa/entorno si el orden y el control de flujo lo permiten."""
    cf = _tomar_turno(con, empresa_id, entorno, quien)
    if cf is None:
        return {"accion": "ocupado"}
    try:
        lote, espera = _lote(con, empresa_id, entorno)
        if espera:
            return espera
        if not lote:
            return {"accion": "nada"}
        total_pend = db.fila(con, f"SELECT COUNT(*) n FROM registros WHERE empresa_id=? AND entorno=? AND estado_envio IN {ESTADOS_COLA}",
                             (empresa_id, entorno))["n"]
        if not forzar_flujo and _futuro(cf["proximo_envio_en"]) and total_pend < ajustes().max_registros_envio:
            return {"accion": "esperando_control_flujo", "proximo_envio_en": cf["proximo_envio_en"]}
        return _enviar(con, empresa_id, entorno, lote)
    finally:
        _soltar_turno(con, empresa_id, entorno, quien)


def _enviar(con, empresa_id: int, entorno: str, cola: list[dict]) -> dict:
    empresa = db.fila(con, "SELECT * FROM empresas WHERE id=?", (empresa_id,))
    # Certificado elegido y representante (certificado de un tercero legitimado): misma regla que las consultas
    try:
        cert, representante = certificados.para_envio(con, empresa, entorno)
        error_cert = None
    except certificados.CertificadoNoUtilizable as e:
        cert, representante, error_cert = None, None, str(e)
    cab = {"nombre": empresa["razon_social"], "nif": empresa["nif"], "incidencia": bool(cola[0]["incidencia"]),
           "representante": representante}
    try:
        cuerpo = xmlgen.mensaje_remision(cab, [r["xml"] for r in cola])
        errores = esquemas_xsd.validar(cuerpo)
    except Exception as e:  # noqa: BLE001 — p. ej. caracteres no admitidos en XML en los datos de la empresa
        cuerpo, errores = None, [f"{type(e).__name__}: {e}"[:200]]
    if errores:
        # p. ej. la razón social de la empresa ya no cumple el esquema: no se envía y se avisa (no se reintenta en bucle)
        msg = "El mensaje de remisión no valida contra el XSD (revise los datos de la empresa): " + "; ".join(errores[:2])
        with db.transaccion(con):
            con.executemany("UPDATE registros SET estado_envio='bloqueado', descripcion_error=?, actualizado_en=? WHERE id=?",
                            [(msg[:500], db.utc_iso(), r["id"]) for r in cola])
        for r in cola:
            _anotar_estado(con, r["id"])
        return {"accion": "fallo", "resultado": "xml_no_valido", "estado": "bloqueado", "error": msg}
    sobre = xmlgen.sobre_soap(cuerpo)
    ahora = _iso(_ahora())
    ids = [r["id"] for r in cola]
    carpeta = f"empresa_{empresa_id}/{entorno}/envios/{_ahora().strftime('%Y%m%d')}-{secrets.token_hex(8)}"
    try:
        envio_id = _preparar_envio(con, empresa_id, entorno, cola, carpeta, sobre, ahora, ids)
    except Exception as e:  # noqa: BLE001 — la petición no llegó a salir: espera, incidencia y aviso (no bucle cada ciclo)
        sig_dt = _ahora()
        with db.transaccion(con):
            for r in cola:
                con.execute("""UPDATE registros SET siguiente_intento_en=?, incidencia=1, descripcion_error=?, actualizado_en=?
                               WHERE id=? AND estado_envio IN ('pendiente','sin_confirmar')""",
                            (_iso(sig_dt + timedelta(seconds=_backoff(r["intentos"] + 1))),
                             f"No se pudo preparar el envío: {type(e).__name__}", db.utc_iso(), r["id"]))
            con.execute("UPDATE control_flujo SET incidencia_desde=COALESCE(incidencia_desde, ?) WHERE empresa_id=? AND entorno=?",
                        (_iso(sig_dt), empresa_id, entorno))
        return {"accion": "fallo", "resultado": "no_enviado", "estado": "pendiente", "error": f"preparación: {type(e).__name__}"}
    return _transmitir(con, empresa_id, entorno, cola, envio_id, carpeta, sobre, ids, cert=cert, error_cert=error_cert)


def _preparar_envio(con, empresa_id, entorno, cola, carpeta, sobre, ahora, ids) -> int:
    with db.transaccion(con):
        cur = con.execute("INSERT INTO envios (empresa_id, entorno, destino, iniciado_en, num_registros) VALUES (?,?,?,?,?)",
                          (empresa_id, entorno, "pendiente", ahora, len(cola)))
        envio_id = cur.lastrowid
        ruta_pet = almacen.guardar(f"{carpeta}/peticion.xml", sobre)
        con.execute("UPDATE envios SET peticion_ruta=? WHERE id=?", (ruta_pet, envio_id))
        con.executemany("UPDATE registros SET estado_envio='enviando', intentos=intentos+1, ultimo_envio_id=?, actualizado_en=? WHERE id=?",
                        [(envio_id, db.utc_iso(), i) for i in ids])
        con.executemany("INSERT INTO envio_lineas (envio_id, registro_id) VALUES (?,?)", [(envio_id, i) for i in ids])
    for i in ids:  # si se perdiera la base de datos durante el envío, la restauración lo tratará como posible entrega
        _anotar_estado(con, i)
    return envio_id


def _transmitir(con, empresa_id, entorno, cola, envio_id, carpeta, sobre, ids, cert=None, error_cert=None) -> dict:
    fase = "preparacion"
    try:
        if entorno == "simulador":
            if error_cert:
                raise simulador.NoEnviado(error_cert)
            if cert:  # solo determina el representante (sin TLS); también se audita
                certificados.registrar_uso(con, cert, empresa_id, entorno, envio_id)
            fase = "envio"
            cuerpo_resp = simulador.procesar(sobre)
            status, destino = (500, "simulador-local") if cuerpo_resp == b"HTTP500" else (200, "simulador-local")
        else:
            url, cert, _ = _preparar_destino(con, entorno, empresa_id, cert, error_cert, operacion="remision")
            cert.pop("_auditar_uso", None)  # se audita aquí, con el envío (una sola anotación)
            certificados.registrar_uso(con, cert, empresa_id, entorno, envio_id)
            with certificados.ficheros_pem(cert) as (cp, kp):
                fase = "envio"
                status, cuerpo_resp = _post(url, sobre, cp, kp)
            destino = url
    except simulador.NoEnviado as e:
        return _tras_fallo(con, envio_id, cola, "no_enviado", str(e), estado="pendiente", posible_entrega=False)
    except simulador.SinRespuesta as e:
        return _tras_fallo(con, envio_id, cola, "sin_respuesta", str(e), estado="sin_confirmar", posible_entrega=True)
    except Exception as e:  # noqa: BLE001 — ningún fallo puede dejar registros «enviando»
        if fase == "preparacion":
            return _tras_fallo(con, envio_id, cola, "no_enviado", f"Error al preparar el envío: {type(e).__name__}",
                               estado="pendiente", posible_entrega=False)
        return _tras_fallo(con, envio_id, cola, "error", f"Error inesperado durante el envío: {type(e).__name__}",
                           estado="sin_confirmar", posible_entrega=True)
    try:
        ruta_resp = almacen.guardar(f"{carpeta}/respuesta.xml", cuerpo_resp)
        con.execute("UPDATE envios SET destino=?, http_estado=?, respuesta_ruta=? WHERE id=?", (destino, status, ruta_resp, envio_id))
    except Exception as e:  # noqa: BLE001 — la respuesta llegó pero no pudo guardarse (disco lleno...): se reenviará
        return _tras_fallo(con, envio_id, cola, "error", f"No se pudo guardar la respuesta: {type(e).__name__}",
                           estado="sin_confirmar", posible_entrega=True, tras_contacto=True)
    try:
        resp = respmod.interpretar(cuerpo_resp)
    except Exception as e:  # noqa: BLE001
        # Respuesta ilegible o HTTP 5xx: no sabemos si se registró -> sin confirmar
        return _tras_fallo(con, envio_id, cola, "respuesta_ilegible", f"HTTP {status}: {type(e).__name__}",
                           estado="sin_confirmar", posible_entrega=True, tras_contacto=True)
    if resp.tipo == "fault":
        texto = f"{resp.fault_codigo}: {resp.fault_texto}"
        if "server" in (resp.fault_codigo or "").lower():
            # Fallo del servicio: se trata como «sin respuesta» (pudo registrarse) y se reintenta con incidencia
            return _tras_fallo(con, envio_id, cola, "fault_servidor", texto, estado="sin_confirmar", posible_entrega=True,
                               tras_contacto=True)
        # Rechazo del envío completo (cabecera, certificado no autorizado...): los registros no se han registrado;
        # quedan bloqueados hasta corregir la causa. No es un rechazo de la factura.
        texto = certificados.con_ayuda(texto)   # 4112, 4123 y 4124: certificado y representante
        return _tras_fallo(con, envio_id, cola, "fault_cliente", texto, estado="bloqueado", posible_entrega=False,
                           tras_contacto=True)
    return _aplicar_respuesta(con, envio_id, empresa_id, entorno, cola, resp)


def _tras_fallo(con, envio_id, cola, resultado, error, *, estado, posible_entrega, tras_contacto=False):
    ahora = _ahora()
    with db.transaccion(con):
        con.execute("UPDATE envios SET finalizado_en=?, resultado=?, error=? WHERE id=?", (_iso(ahora), resultado, error[:500], envio_id))
        for r in cola:
            sig = _iso(ahora + timedelta(seconds=_backoff(r["intentos"] + 1))) if estado != "bloqueado" else None
            con.execute("""UPDATE registros SET estado_envio=?, siguiente_intento_en=?, descripcion_error=?, actualizado_en=?,
                           posible_entrega=MAX(posible_entrega, ?), incidencia=1
                           WHERE id=? AND estado_envio='enviando'""",
                        (estado, sig, error[:500], db.utc_iso(), int(posible_entrega), r["id"]))
        e = db.fila(con, "SELECT empresa_id, entorno FROM envios WHERE id=?", (envio_id,))
        cf = db.fila(con, "SELECT tiempo_espera FROM control_flujo WHERE empresa_id=? AND entorno=?", (e["empresa_id"], e["entorno"]))
        con.execute("UPDATE control_flujo SET incidencia_desde=COALESCE(incidencia_desde, ?) WHERE empresa_id=? AND entorno=?",
                    (_iso(ahora), e["empresa_id"], e["entorno"]))
        if posible_entrega or tras_contacto:
            # El envío pudo llegar a la AEAT: se respeta el tiempo de espera antes del siguiente
            con.execute("UPDATE control_flujo SET proximo_envio_en=? WHERE empresa_id=? AND entorno=?",
                        (_iso(ahora + timedelta(seconds=(cf or {}).get("tiempo_espera") or 60)), e["empresa_id"], e["entorno"]))
    for r in cola:
        _anotar_estado(con, r["id"])
    return {"accion": "fallo", "resultado": resultado, "estado": estado, "envio_id": envio_id, "error": error}


def _aplicar_respuesta(con, envio_id, empresa_id, entorno, cola, resp: respmod.Respuesta) -> dict:
    ahora = _ahora()
    espera = resp.tiempo_espera if resp.tiempo_espera is not None else ajustes().espera_inicial_s
    por_clave = {}
    for r in cola:
        op = "Alta" if r["tipo"] == "alta" else "Anulacion"
        por_clave.setdefault((r["num_serie"], r["fecha_expedicion"], op), []).append(r)
    resumen = {"aceptado": 0, "aceptado_con_errores": 0, "rechazado": 0, "sin_linea": 0}
    with db.transaccion(con):
        con.execute("UPDATE envios SET finalizado_en=?, resultado=?, csv=?, tiempo_espera=? WHERE id=?",
                    (_iso(ahora), resp.estado_envio, resp.csv, espera, envio_id))
        con.execute("""UPDATE control_flujo SET tiempo_espera=?, proximo_envio_en=?, incidencia_desde=NULL
                       WHERE empresa_id=? AND entorno=?""", (espera, _iso(ahora + timedelta(seconds=espera)), empresa_id, entorno))
        tratados = set()
        for l in resp.lineas:
            regs = por_clave.get((l.num_serie, l.fecha_expedicion, l.tipo_operacion)) or []
            reg = next((x for x in regs if x["id"] not in tratados), None)
            if not reg:
                continue
            tratados.add(reg["id"])
            codigo, desc = l.codigo_error, l.descripcion_error
            if l.estado == "Correcto":
                estado = "aceptado"
            elif l.estado == "AceptadoConErrores":
                estado = "aceptado_con_errores"
            elif l.codigo_error == "3000" and l.duplicado:
                est_dup = l.duplicado.get("estado")
                if reg["posible_entrega"] and reg["tipo"] == "anulacion" and est_dup == "Anulada":
                    estado, codigo = "aceptado", None
                    desc = (f"Confirmado por respuesta de duplicado (3000): la AEAT ya tenía la anulación tras un envío sin "
                            f"respuesta (IdPeticion {l.duplicado.get('id_peticion')}).")
                elif reg["posible_entrega"] and reg["tipo"] == "alta" and est_dup in ("Correcta", "AceptadaConErrores"):
                    # Un intento anterior de ESTE registro pudo llegar sin que recibiéramos respuesta: la AEAT ya lo tenía.
                    estado = "aceptado" if est_dup == "Correcta" else "aceptado_con_errores"
                    codigo = l.duplicado.get("codigo_error") if est_dup == "AceptadaConErrores" else None
                    desc = (f"Confirmado por respuesta de duplicado (3000): la AEAT ya tenía el registro tras un envío sin "
                            f"respuesta (IdPeticion {l.duplicado.get('id_peticion')}, estado {est_dup}).")
                else:
                    estado = "rechazado"
                    desc = ((desc or "") + " — La AEAT ya tiene un registro con esta identificación (estado "
                            f"{est_dup}) y no consta un envío previo sin respuesta de este registro: requiere revisión (si la "
                            "consulta a la AEAT devuelve este mismo registro, con la misma huella, puede conciliarse).")
            else:
                estado = "rechazado"
            resumen[estado] += 1
            con.execute("""UPDATE envio_lineas SET estado_registro=?, codigo_error=?, descripcion_error=?, duplicado=?
                           WHERE envio_id=? AND registro_id=?""",
                        (l.estado, l.codigo_error, l.descripcion_error, json.dumps(l.duplicado) if l.duplicado else None, envio_id, reg["id"]))
            con.execute("""UPDATE registros SET estado_envio=?, codigo_error=?, descripcion_error=?, csv=?, siguiente_intento_en=NULL,
                           duplicado=?, actualizado_en=? WHERE id=?""",
                        (estado, codigo, desc, resp.csv, json.dumps(l.duplicado) if l.duplicado else None, db.utc_iso(), reg["id"]))
        for r in cola:
            if r["id"] not in tratados:
                # Respuesta parcial: sin línea para este registro -> sin confirmar (pudo registrarse), se reenviará
                resumen["sin_linea"] += 1
                con.execute("""UPDATE registros SET estado_envio='sin_confirmar', posible_entrega=1, siguiente_intento_en=?,
                               descripcion_error=?, actualizado_en=? WHERE id=?""",
                            (_iso(ahora + timedelta(seconds=espera)), "La respuesta no incluía este registro", db.utc_iso(), r["id"]))
        cambios_f = {}
        from .emision import recalcular_estado_factura
        for fid in {r["factura_id"] for r in cola}:
            nuevo = recalcular_estado_factura(con, fid)
            if nuevo:
                cambios_f[fid] = nuevo
            orig = db.fila(con, "SELECT rectifica_factura_id FROM facturas WHERE id=?", (fid,))
            if orig and orig["rectifica_factura_id"]:
                n2 = recalcular_estado_factura(con, orig["rectifica_factura_id"])
                if n2:
                    cambios_f[orig["rectifica_factura_id"]] = n2
    for r in cola:
        _anotar_estado(con, r["id"])
    for fid, est in cambios_f.items():
        try:
            diario.anotar("estado_factura", {"id": fid, "estado": est})
        except Exception:  # noqa: BLE001 — la conciliación del arranque lo completará
            pass
    return {"accion": "enviado", "envio_id": envio_id, "estado_envio": resp.estado_envio, "csv": resp.csv,
            "tiempo_espera": espera, **resumen}


def cerrar_incidencia_resuelta(con, empresa_id: int | None = None, entorno: str | None = None) -> int:
    """Cierra la incidencia de comunicación (control_flujo.incidencia_desde) de cada empresa/entorno (o solo del indicado)
    que ya no tiene ningún registro sin respuesta definitiva. Un envío con respuesta la cierra en _aplicar_respuesta;
    esto cubre los casos en que no queda nada que enviar: la conciliación con la consulta a la AEAT del último registro
    pendiente y la restauración de una copia. Si no, las facturas posteriores se remitirían con Incidencia=S sin que
    hubiera incidencia. Debe llamarse dentro de una transacción. Devuelve cuántas incidencias se han cerrado."""
    sql = f"""UPDATE control_flujo SET incidencia_desde=NULL WHERE incidencia_desde IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM registros r WHERE r.empresa_id=control_flujo.empresa_id
                              AND r.entorno=control_flujo.entorno AND r.estado_envio IN {ESTADOS_NO_DEFINITIVOS})"""
    args: list = []
    if empresa_id is not None:
        sql += " AND empresa_id=? AND entorno=?"
        args += [empresa_id, entorno]
    return con.execute(sql, args).rowcount


def estado_cola(con, empresa_id: int, entorno: str) -> dict:
    cf = db.fila(con, "SELECT * FROM control_flujo WHERE empresa_id=? AND entorno=?", (empresa_id, entorno))
    cuenta = {r["estado_envio"]: r["n"] for r in db.filas(con, """SELECT estado_envio, COUNT(*) n FROM registros
               WHERE empresa_id=? AND entorno=? GROUP BY estado_envio""", (empresa_id, entorno))}
    envios = db.filas(con, "SELECT * FROM envios WHERE empresa_id=? AND entorno=? ORDER BY id DESC LIMIT 20", (empresa_id, entorno))
    pend = sum(cuenta.get(k, 0) for k in ESTADOS_NO_DEFINITIVOS)
    aviso = None
    if cuenta.get("bloqueado"):
        aviso = (f"{cuenta['bloqueado']} registro(s) bloqueado(s): la AEAT rechazó el envío completo. Mientras no se corrija "
                 f"la causa y se reintente, no se remite ningún registro posterior ({pend} pendiente(s) en total).")
    elif cf and cf.get("incidencia_desde") and pend:
        aviso = (f"Incidencia de comunicación desde el {db.legible(cf['incidencia_desde'])}: {pend} registro(s) pendiente(s) de "
                 "remitir o de confirmar. La facturación puede continuar; se reintenta automáticamente al menos cada hora.")
    return {"control_flujo": cf, "por_estado": cuenta, "pendientes": cuenta.get("pendiente", 0),
            "sin_confirmar": cuenta.get("sin_confirmar", 0), "bloqueados": cuenta.get("bloqueado", 0),
            "enviando": cuenta.get("enviando", 0), "aviso_incidencia": aviso, "ultimos_envios": envios}


# --- Bucle del trabajador ------------------------------------------------------------------------
class Trabajador:
    """Hilo de remisión. Se despierta cada pocos segundos o cuando se expide una factura."""

    def __init__(self, intervalo: float = 3.0, nombre: str | None = None):
        self.intervalo = intervalo
        self.nombre = nombre or f"remitente-{socket.gethostname()}-{id(self)}"
        self._evento = threading.Event()
        self._parar = threading.Event()
        self.hilo: threading.Thread | None = None

    def despertar(self):
        self._evento.set()

    def iniciar(self):
        self.hilo = threading.Thread(target=self._bucle, name=self.nombre, daemon=True)
        self.hilo.start()

    def parar(self):
        self._parar.set()
        self._evento.set()
        if self.hilo:
            self.hilo.join(timeout=10)

    def _bucle(self):
        con = db.conectar()
        while not self._parar.is_set():
            try:
                self.ciclo(con)
            except Exception as e:  # noqa: BLE001 — el trabajador no debe morir; queda registrado
                try:
                    db.actividad(con, "remision.error", detalle={"error": f"{type(e).__name__}: {e}"[:300]})
                except Exception:  # noqa: BLE001
                    pass
            self._evento.wait(self.intervalo)
            self._evento.clear()

    def ciclo(self, con) -> list[dict]:
        recuperar_interrumpidos(con)
        out = []
        pares = db.filas(con, f"""SELECT DISTINCT empresa_id, entorno FROM registros WHERE estado_envio IN {ESTADOS_COLA}""")
        for p in pares:
            try:
                out.append(procesar(con, p["empresa_id"], p["entorno"], quien=self.nombre))
            except Exception as e:  # noqa: BLE001 — un fallo de una empresa no detiene la remisión de las demás
                db.actividad(con, "remision.error", empresa_id=p["empresa_id"],
                             detalle={"entorno": p["entorno"], "error": f"{type(e).__name__}: {e}"[:300]})
                out.append({"accion": "error", "empresa_id": p["empresa_id"]})
        return out
