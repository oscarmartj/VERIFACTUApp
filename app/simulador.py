"""SIMULADOR LOCAL del servicio VERI*FACTU (no es la AEAT).

Sirve para pruebas técnicas sin red: valida el XML contra el XSD, comprueba coherencia de importes,
recalcula la huella, detecta duplicados por IDFactura y responde con la estructura de
RespuestaSuministro.xsd y los códigos del catálogo oficial (errores.properties). Permite inyectar
fallos (sin respuesta, servicio caído, error 500, rechazo de cabecera) para probar la recuperación.
Acepta Cabecera/Representante (NIF identificado o 4105) y, en el modo «no_apoderado», responde 4112 a los
envíos que lo llevan, como la AEAT cuando el titular del certificado no está apoderado ni es colaborador social; en los
modos «representante_nif_no_censado» y «representante_nombre_no_censado», 4123 y 4124 (NIF o nombre del representante
no identificados en el censo). El simulador no tiene censo: estos modos solo reproducen la respuesta.
Sus respuestas se marcan con el CSV «SIMULADO-…» y nunca se presentan como respuestas de la AEAT.

Los modos de fallo son de cada obligado tributario (NIF de la empresa, único en la instalación): un modo fijado para una
empresa solo lo consumen los envíos (y consultas) cuya Cabecera/ObligadoEmision es ese NIF y, los de representación
(4112/4123/4124), solo los que además llevan Cabecera/Representante; nunca afecta a otra empresa. Sin NIF, fijar_modo deja
un modo para cualquier obligado: solo lo usan las pruebas automáticas desde Python (la API siempre indica la empresa). El
panel del operador solo lista los modos que fijó el operador (no los que fija cada empresa para la suya).

También simula la consulta (ConsultaFactuSistemaFacturacion → RespuestaConsultaLR.xsd) con los registros que tiene,
paginada de TAM_PAGINA_CONSULTA en TAM_PAGINA_CONSULTA, y sus propios modos de fallo (servicio «consulta»). Guarda quién
presentó cada registro (el NIF de Cabecera/Representante o, sin representante, el del obligado) y lo devuelve como
NIFPresentador; con IndicadorRepresentante=S devuelve solo los registros que presentó quien consulta (el titular del
certificado, que el simulador recibe en el propio proceso: no hay TLS). Con MostrarSistemaInformatico=S devuelve el bloque
SistemaInformatico de cada registro. En el modo «solo_como_representante» responde 4140 a la consulta de un tercero sin
IndicadorRepresentante (como haría la AEAT con un tercero que no está apoderado para consultar; pendiente de contrastar en
el entorno de pruebas).
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from lxml import etree

from . import nif as nifmod
from .config import ZONA_HORARIA, ajustes
from .importes import q2
from .verifactu import esquemas_xsd, huella as hmod
from .verifactu.esquemas_xsd import NS_CON, NS_RCON, NS_SF, NS_SFLR, NS_SFR, NS_SOAP
from .verifactu.respuesta import describir_error

_lock = threading.Lock()
MODOS = ("normal", "sin_respuesta", "caido", "error_500", "rechazo", "fault_servidor", "rechazo_registro", "no_apoderado",
         "representante_nif_no_censado", "representante_nombre_no_censado")
# Rechazos de la cabecera que solo afectan a los envíos con Cabecera/Representante
_RECHAZOS_REPRESENTANTE = {"no_apoderado": "4112", "representante_nif_no_censado": "4123",
                           "representante_nombre_no_censado": "4124"}
MODOS_CONSULTA = ("normal", "sin_respuesta", "caido", "error_500", "rechazo", "fault_servidor", "respuesta_invalida",
                  "solo_como_representante")
TAM_PAGINA_CONSULTA = 10000   # máximo de registros por respuesta (RespuestaConsultaLR.xsd: maxOccurs 10000)
_ESTADO_CONSULTA = {"Correcta": "Correcto", "AceptadaConErrores": "AceptadoConErrores", "Anulada": "Anulado"}


class SinRespuesta(Exception):
    """La petición pudo procesarse pero no llegó respuesta (timeout / corte)."""


class NoEnviado(Exception):
    """La conexión no llegó a establecerse: la petición no se procesó."""


def _db() -> sqlite3.Connection:
    ruta = ajustes().data_dir / "simulador_aeat.sqlite3"
    from .db import crear_privado
    crear_privado(ruta)   # 0600: guarda los registros recibidos (datos de las facturas)
    con = sqlite3.connect(ruta, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS sim_registros (nif TEXT, num_serie TEXT, fecha TEXT, estado TEXT,
                   id_peticion TEXT, codigo TEXT, descripcion TEXT, huella TEXT, tipo TEXT, recibido_en TEXT,
                   PRIMARY KEY (nif, num_serie, fecha))""")
    con.execute("CREATE TABLE IF NOT EXISTS sim_config (clave TEXT PRIMARY KEY, valor TEXT)")
    # modo de fallo de cada obligado (nif '*': cualquiera, solo desde las pruebas); sim_config ya no guarda modos.
    # origen: quién lo fijó («operador» desde el panel de la plataforma, «empresa» un administrador para la suya)
    con.execute("""CREATE TABLE IF NOT EXISTS sim_modos (nif TEXT NOT NULL, servicio TEXT NOT NULL, modo TEXT NOT NULL,
                   veces INTEGER NOT NULL, empresa_id INTEGER, fijado_en TEXT, origen TEXT, PRIMARY KEY (nif, servicio))""")
    if "origen" not in {r["name"] for r in con.execute("PRAGMA table_info(sim_modos)")}:
        con.execute("ALTER TABLE sim_modos ADD COLUMN origen TEXT")
    con.execute("""CREATE TABLE IF NOT EXISTS sim_rechazos (nif TEXT, num_serie TEXT, fecha TEXT, tipo TEXT,
                   PRIMARY KEY (nif, num_serie, fecha, tipo))""")
    # XML del último registro recibido de cada factura y de su último alta, para responder a la consulta (tabla aparte:
    # sim_registros conserva su estructura)
    con.execute("""CREATE TABLE IF NOT EXISTS sim_registros_xml (nif TEXT, num_serie TEXT, fecha TEXT, xml TEXT, xml_alta TEXT,
                   PRIMARY KEY (nif, num_serie, fecha))""")
    # Quién presentó el último registro de cada factura (Cabecera/Representante o el obligado): NIFPresentador e
    # IndicadorRepresentante de la consulta
    con.execute("""CREATE TABLE IF NOT EXISTS sim_presentadores (nif TEXT, num_serie TEXT, fecha TEXT, presentador TEXT,
                   PRIMARY KEY (nif, num_serie, fecha))""")
    return con


COMODIN = "*"   # modo para cualquier obligado (solo desde Python: pruebas automáticas)


def _servicio(servicio: str) -> str:
    if servicio not in ("remision", "consulta"):
        raise ValueError("Servicio desconocido")
    return servicio


def fijar_modo(modo: str, veces: int = 1, servicio: str = "remision", nif: str | None = None,
               empresa_id: int | None = None, origen: str | None = None) -> None:
    """Fija el modo de fallo de los próximos `veces` envíos (o consultas) del obligado `nif`. Sin NIF, de cualquier
    obligado (solo pruebas automáticas). «normal» quita el modo. `origen`: «operador» (panel de la plataforma) o «empresa»
    (un administrador para la suya): el panel del operador solo lista los que fijó el operador."""
    _servicio(servicio)
    if modo not in (MODOS if servicio == "remision" else MODOS_CONSULTA):
        raise ValueError("Modo desconocido")
    clave = nif or COMODIN
    con = _db()
    if modo == "normal":
        con.execute("DELETE FROM sim_modos WHERE nif=? AND servicio=?", (clave, servicio))
        return
    con.execute("""INSERT OR REPLACE INTO sim_modos (nif, servicio, modo, veces, empresa_id, fijado_en, origen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (clave, servicio, modo, max(int(veces), 1), empresa_id, datetime.now(ZoneInfo(ZONA_HORARIA)).isoformat(),
                 origen))


def _leer_modo(con, nif: str, servicio: str, origen: str | None = None) -> dict:
    r = con.execute("SELECT modo, veces, origen FROM sim_modos WHERE nif=? AND servicio=?", (nif, servicio)).fetchone()
    if not r or r["veces"] <= 0 or (origen is not None and r["origen"] != origen):
        return {"modo": "normal", "veces": 0}
    return {"modo": r["modo"], "veces": int(r["veces"])}


def modo_actual(nif: str | None = None, origen: str | None = None) -> dict:
    """Modo de fallo pendiente del obligado `nif` (sin NIF: el de cualquier obligado, el de las pruebas). Con `origen`,
    solo los que fijó ese origen (al operador no se le muestran los que la empresa fijó para sí: «normal» en su lugar)."""
    con = _db()
    clave = nif or COMODIN
    return {**_leer_modo(con, clave, "remision", origen), "consulta": _leer_modo(con, clave, "consulta", origen)}


def modos_fijados(origen: str = "operador") -> list[dict]:
    """Modos de fallo pendientes que fijó `origen` (el panel del operador: solo los que fijó el operador, con el NIF que él
    mismo escribió; los que fija cada empresa para la suya no se listan, para no mostrarle el NIF de empresas ajenas)."""
    con = _db()
    return [dict(r) for r in con.execute("""SELECT nif, servicio, modo, veces, fijado_en FROM sim_modos
                                             WHERE nif<>? AND veces>0 AND origen=? ORDER BY nif, servicio""",
                                          (COMODIN, origen))]


def _consumir_modo(con, servicio: str = "remision", nif: str | None = None, con_representante: bool = False) -> str:
    """Modo que se aplica a este envío (o consulta) del obligado `nif`, descontando una vez: el de ese obligado o, si no
    tiene, el de cualquier obligado. Un modo de representación (4112/4123/4124) solo se aplica, y solo se descuenta, en un
    envío con Cabecera/Representante; los demás envíos lo dejan intacto."""
    _servicio(servicio)
    for clave in ([nif] if nif else []) + [COMODIN]:
        m = _leer_modo(con, clave, servicio)
        if m["modo"] == "normal":
            continue
        if m["modo"] in _RECHAZOS_REPRESENTANTE and not con_representante:
            continue
        if m["veces"] <= 1:
            con.execute("DELETE FROM sim_modos WHERE nif=? AND servicio=?", (clave, servicio))
        else:
            con.execute("UPDATE sim_modos SET veces=veces-1 WHERE nif=? AND servicio=?", (clave, servicio))
        return m["modo"]
    return "normal"


def _t(el, ruta):
    x = el.find(ruta, {"sf": NS_SF})
    return x.text if x is not None else None


def _validar_alta(r) -> tuple[str, str | None]:
    """Devuelve (estado, codigo). Reglas de negocio seleccionadas del documento de validaciones."""
    base = sum((Decimal(x.text) for x in r.findall(".//sf:BaseImponibleOimporteNoSujeto", {"sf": NS_SF})), Decimal(0))
    cuotas = sum((Decimal(x.text) for x in r.findall(".//sf:CuotaRepercutida", {"sf": NS_SF})), Decimal(0))
    for det in r.findall(".//sf:DetalleDesglose", {"sf": NS_SF}):
        t, b, c = (_t(det, f"sf:{k}") for k in ("TipoImpositivo", "BaseImponibleOimporteNoSujeto", "CuotaRepercutida"))
        # Operaciones exentas y calificación (documento de validaciones: 1199, 1207, 1208, 1238, 1286, 1289)
        exenta, cal = _t(det, "sf:OperacionExenta"), _t(det, "sf:CalificacionOperacion")
        impuesto, clave = _t(det, "sf:Impuesto") or "01", _t(det, "sf:ClaveRegimen")
        if exenta:
            if any(det.find(f"sf:{k}", {"sf": NS_SF}) is not None
                   for k in ("TipoImpositivo", "CuotaRepercutida", "TipoRecargoEquivalencia", "CuotaRecargoEquivalencia")):
                return "Incorrecto", "1238"
            if impuesto == "01" and clave == "01" and exenta in ("E2", "E3"):
                return "Incorrecto", "1199"
            if impuesto == "01" and exenta == "E5" and r.find("sf:Destinatarios/sf:IDDestinatario/sf:NIF", {"sf": NS_SF}) is not None:
                return "Incorrecto", "1289"
        else:
            if impuesto == "01" and clave == "02":
                return "Incorrecto", "1286"
            if cal == "S1" and (t is None or c is None):
                return "Incorrecto", "1208"
            if cal != "S1" and c and Decimal(c) != 0:
                return "Incorrecto", "1207"
        if t and b and c and abs(q2(Decimal(b) * Decimal(t) / 100) - Decimal(c)) > Decimal("10"):
            return "Incorrecto", "1142"
    if q2(Decimal(_t(r, "sf:CuotaTotal"))) != q2(cuotas):
        return "AceptadoConErrores", "2006"
    if q2(Decimal(_t(r, "sf:ImporteTotal"))) != q2(base + cuotas):
        return "AceptadoConErrores", "2005"
    fecha = _t(r, "sf:IDFactura/sf:FechaExpedicionFactura")
    d, m, a = fecha.split("-")
    hoy = datetime.now(ZoneInfo(ZONA_HORARIA)).date()
    if f"{a}-{m}-{d}" > hoy.isoformat():
        return "Incorrecto", "1112"
    fo = _t(r, "sf:FechaOperacion")
    if fo:
        do, mo, ao = fo.split("-")
        if f"{ao}-{mo}-{do}" > f"{a}-{m}-{d}":
            return "Incorrecto", "1146"
        if int(ao) < hoy.year - 20 or (int(ao) == hoy.year - 20 and f"{mo}-{do}" < hoy.strftime("%m-%d")):
            return "Incorrecto", "1134"
    ant = r.find("sf:Encadenamiento/sf:RegistroAnterior", {"sf": NS_SF})
    huella_ant = _t(ant, "sf:Huella") if ant is not None else ""
    calc = hmod.huella_alta(id_emisor=_t(r, "sf:IDFactura/sf:IDEmisorFactura"), num_serie=_t(r, "sf:IDFactura/sf:NumSerieFactura"),
                            fecha_expedicion=fecha, tipo_factura=_t(r, "sf:TipoFactura"), cuota_total=_t(r, "sf:CuotaTotal"),
                            importe_total=_t(r, "sf:ImporteTotal"), huella_anterior=huella_ant or "",
                            fecha_hora_huso=_t(r, "sf:FechaHoraHusoGenRegistro"))
    if calc != _t(r, "sf:Huella"):
        return "AceptadoConErrores", "2000"
    return "Correcto", None


def procesar(peticion: bytes) -> bytes:
    """Procesa un sobre SOAP RegFactuSistemaFacturacion y devuelve el sobre de respuesta."""
    with _lock:
        con = _db()
        doc = etree.fromstring(peticion, esquemas_xsd.parser_seguro())
        reg = doc.find(f".//{{{NS_SFLR}}}RegFactuSistemaFacturacion")
        # el modo de fallo es del obligado de la cabecera (y los de representación, solo con Cabecera/Representante)
        nif_cab = reg.findtext(f"{{{NS_SFLR}}}Cabecera/{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF") if reg is not None else None
        rep = reg.find(f"{{{NS_SFLR}}}Cabecera/{{{NS_SF}}}Representante/{{{NS_SF}}}NIF") if reg is not None else None
        modo = _consumir_modo(con, "remision", nif_cab, rep is not None)
        if modo == "caido":
            raise NoEnviado("Simulador: servicio no disponible (conexión rechazada)")
        if modo == "error_500":
            return b"HTTP500"
        if reg is None or esquemas_xsd.validar(reg):
            return _fault("4102", describir_error("4102"))
        cab = reg.find(f"{{{NS_SFLR}}}Cabecera")
        if rep is not None and not nifmod.es_valido(rep.text):
            return _fault("4105", describir_error("4105"))
        if modo == "rechazo":
            return _fault("4112", describir_error("4112"))
        if modo in _RECHAZOS_REPRESENTANTE and rep is not None:
            return _fault(_RECHAZOS_REPRESENTANTE[modo], describir_error(_RECHAZOS_REPRESENTANTE[modo]))
        if modo == "fault_servidor":
            return _fault("9999", "Error interno del servicio (simulado)", servidor=True)
        id_peticion = secrets.token_hex(8).upper()
        lineas, estados = [], []
        for rf in reg.findall(f"{{{NS_SFLR}}}RegistroFactura"):
            r = rf[0]
            es_alta = r.tag == f"{{{NS_SF}}}RegistroAlta"
            pre = "" if es_alta else "Anulada"
            nif = _t(r, f"sf:IDFactura/sf:IDEmisorFactura{pre}")
            ns = _t(r, f"sf:IDFactura/sf:NumSerieFactura{pre}")
            fe = _t(r, f"sf:IDFactura/sf:FechaExpedicionFactura{pre}")
            subs = _t(r, "sf:Subsanacion") if es_alta else None
            rech = _t(r, "sf:RechazoPrevio")
            sin_previo = _t(r, "sf:SinRegistroPrevio") if not es_alta else None
            previo = con.execute("SELECT * FROM sim_registros WHERE nif=? AND num_serie=? AND fecha=?", (nif, ns, fe)).fetchone()
            rechazos = {x["tipo"] for x in con.execute("SELECT tipo FROM sim_rechazos WHERE nif=? AND num_serie=? AND fecha=?", (nif, ns, fe))}
            dup = None
            if nif != nif_cab:
                estado, cod = "Incorrecto", "1104"
            elif modo == "rechazo_registro":
                estado, cod = "Incorrecto", "1100"
            elif es_alta:
                # Operativas admisibles (documento de validaciones, anexo)
                if subs != "S" and rech in ("S", "X"):
                    estado, cod = "Incorrecto", "1153"
                elif subs != "S" and previo:
                    estado, cod, dup = "Incorrecto", "3000", previo
                elif subs == "S" and rech == "X" and previo:
                    estado, cod, dup = "Incorrecto", "3000", previo
                elif subs == "S" and rech != "X" and not previo:
                    estado, cod = "Incorrecto", "3002"
                elif subs == "S" and rech == "S" and "alta_subsanacion" not in rechazos:
                    estado, cod = "Incorrecto", "1275"  # «rechazo de subsanación» sin subsanación previa rechazada
                else:
                    estado, cod = _validar_alta(r)
            else:
                if rech == "S" and "anulacion" not in rechazos:
                    estado, cod = "Incorrecto", "1275"  # «anulación por rechazo» sin anulación previa rechazada
                elif sin_previo == "S":
                    estado, cod = ("Incorrecto", "3000") if previo else ("Correcto", None)
                    dup = previo
                elif not previo:
                    estado, cod = "Incorrecto", "3002"
                else:
                    estado, cod = "Correcto", None
            if estado == "Incorrecto" and cod not in ("3000",):
                tipo_r = "anulacion" if not es_alta else ("alta_subsanacion" if subs == "S" else "alta")
                con.execute("INSERT OR IGNORE INTO sim_rechazos VALUES (?,?,?,?)", (nif, ns, fe, tipo_r))
            if estado != "Incorrecto":
                nuevo = "Anulada" if not es_alta else ("Correcta" if estado == "Correcto" else "AceptadaConErrores")
                # recibido_en con su huso (antes, la hora local sin huso del servidor, que _marca leía como de Madrid)
                con.execute("INSERT OR REPLACE INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (nif, ns, fe, nuevo, id_peticion, cod, describir_error(cod) if cod else None,
                             _t(r, "sf:Huella"), "alta" if es_alta else "anulacion",
                             datetime.now(ZoneInfo(ZONA_HORARIA)).isoformat()))
                xml_r = etree.tostring(r, encoding="unicode")
                ant = con.execute("SELECT xml_alta FROM sim_registros_xml WHERE nif=? AND num_serie=? AND fecha=?",
                                  (nif, ns, fe)).fetchone()
                con.execute("INSERT OR REPLACE INTO sim_registros_xml VALUES (?,?,?,?,?)",
                            (nif, ns, fe, xml_r, xml_r if es_alta else (ant["xml_alta"] if ant else None)))
                con.execute("INSERT OR REPLACE INTO sim_presentadores VALUES (?,?,?,?)",
                            (nif, ns, fe, rep.text if rep is not None else nif_cab))
            estados.append(estado)
            lineas.append((nif, ns, fe, "Alta" if es_alta else "Anulacion", subs, _t(r, "sf:RechazoPrevio"), estado, cod, dup))
        if all(e == "Correcto" for e in estados):
            env = "Correcto"
        elif all(e == "Incorrecto" for e in estados):
            env = "Incorrecto"
        else:
            env = "ParcialmenteCorrecto"
        resp = _respuesta(cab, env, lineas, csv="SIMULADO-" + id_peticion if env != "Incorrecto" else None)
        if modo == "sin_respuesta":
            raise SinRespuesta("Simulador: la petición se procesó pero la respuesta no llegó")
        return resp


def _fault(codigo: str, texto: str, servidor: bool = False) -> bytes:
    env = etree.Element(f"{{{NS_SOAP}}}Envelope", nsmap={"env": NS_SOAP})
    body = etree.SubElement(env, f"{{{NS_SOAP}}}Body")
    f = etree.SubElement(body, f"{{{NS_SOAP}}}Fault")
    etree.SubElement(f, "faultcode").text = "env:Server" if servidor else "env:Client"
    etree.SubElement(f, "faultstring").text = f"Codigo[{codigo}].{texto}"
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _respuesta(cab_peticion, estado_envio, lineas, csv=None) -> bytes:
    env = etree.Element(f"{{{NS_SOAP}}}Envelope", nsmap={"env": NS_SOAP, "tikR": NS_SFR, "tik": NS_SF})
    body = etree.SubElement(env, f"{{{NS_SOAP}}}Body")
    r = etree.SubElement(body, f"{{{NS_SFR}}}RespuestaRegFactuSistemaFacturacion")

    def e(p, n, t=None, ns=NS_SFR):
        x = etree.SubElement(p, f"{{{ns}}}{n}")
        if t is not None:
            x.text = t
        return x
    if csv:
        e(r, "CSV", csv)
        dp = e(r, "DatosPresentacion")
        presentador = cab_peticion.find(f"{{{NS_SF}}}Representante/{{{NS_SF}}}NIF")  # quien presenta: el representante, si lo hay
        if presentador is None:
            presentador = cab_peticion.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF")
        e(dp, "NIFPresentador", presentador.text, NS_SF)
        e(dp, "TimestampPresentacion", hmod.fecha_hora_huso(datetime.now(ZoneInfo(ZONA_HORARIA))), NS_SF)
    c = e(r, "Cabecera")
    ob = e(c, "ObligadoEmision", ns=NS_SF)
    e(ob, "NombreRazon", cab_peticion.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NombreRazon").text, NS_SF)
    e(ob, "NIF", cab_peticion.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF").text, NS_SF)
    rp = cab_peticion.find(f"{{{NS_SF}}}Representante")
    if rp is not None:  # se devuelve la cabecera del envío, con su representante
        re_ = e(c, "Representante", ns=NS_SF)
        e(re_, "NombreRazon", rp.find(f"{{{NS_SF}}}NombreRazon").text, NS_SF)
        e(re_, "NIF", rp.find(f"{{{NS_SF}}}NIF").text, NS_SF)
    e(r, "TiempoEsperaEnvio", "60")
    e(r, "EstadoEnvio", estado_envio)
    for nif, ns, fe, op, subs, rech, estado, cod, dup in lineas:
        rl = e(r, "RespuestaLinea")
        idf = e(rl, "IDFactura")
        e(idf, "IDEmisorFactura", nif, NS_SF)
        e(idf, "NumSerieFactura", ns, NS_SF)
        e(idf, "FechaExpedicionFactura", fe, NS_SF)
        o = e(rl, "Operacion")
        e(o, "TipoOperacion", op, NS_SF)
        if subs:
            e(o, "Subsanacion", subs, NS_SF)
        if rech:
            e(o, "RechazoPrevio", rech, NS_SF)
        e(rl, "EstadoRegistro", estado)
        if cod:
            e(rl, "CodigoErrorRegistro", cod)
            e(rl, "DescripcionErrorRegistro", describir_error(cod))
        if dup is not None:
            d = e(rl, "RegistroDuplicado")
            e(d, "IdPeticionRegistroDuplicado", dup["id_peticion"], NS_SF)
            e(d, "EstadoRegistroDuplicado", dup["estado"], NS_SF)
            if dup["codigo"]:
                e(d, "CodigoErrorRegistro", dup["codigo"], NS_SF)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


# --- Consulta (ConsultaFactuSistemaFacturacion) ---------------------------------------------------------------------
def consultar(peticion: bytes, quien: str | None = None) -> bytes:
    """Procesa un sobre SOAP ConsultaFactuSistemaFacturacion y devuelve RespuestaConsultaFactuSistemaFacturacion con
    los registros que tiene el simulador del obligado y periodo pedidos (ordenados por número y fecha), paginados de
    TAM_PAGINA_CONSULTA en TAM_PAGINA_CONSULTA con IndicadorPaginacion y ClavePaginacion. No modifica nada.

    `quien`: NIF del titular del certificado con el que se consulta cuando es un tercero (representante); None si
    consulta el propio obligado. Con IndicadorRepresentante=S solo se devuelven los registros que presentó `quien`."""
    with _lock:
        con = _db()
        try:
            doc = etree.fromstring(peticion, esquemas_xsd.parser_seguro())
        except etree.XMLSyntaxError:
            doc = None
        q = doc.find(f".//{{{NS_CON}}}ConsultaFactuSistemaFacturacion") if doc is not None else None
        nif_q = q.findtext(f"{{{NS_CON}}}Cabecera/{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF") if q is not None else None
        modo = _consumir_modo(con, "consulta", nif_q)
        if modo == "caido":
            raise NoEnviado("Simulador: servicio de consulta no disponible (conexión rechazada)")
        if modo == "error_500":
            return b"HTTP500"
        if doc is None:
            return _fault("4103", describir_error("4103"))
        if q is None or esquemas_xsd.validar(q, "ConsultaLR.xsd"):
            return _fault("4102", describir_error("4102"))
        if modo == "rechazo":
            return _fault("4140", describir_error("4140"))
        if modo == "fault_servidor":
            return _fault("9999", "Error interno del servicio de consulta (simulado)", servidor=True)
        sf, c = {"sf": NS_SF, "c": NS_CON}, q
        nif = c.findtext("c:Cabecera/sf:ObligadoEmision/sf:NIF", namespaces=sf)
        indicador = c.findtext("c:Cabecera/sf:IndicadorRepresentante", namespaces=sf) == "S"
        mostrar_si = c.findtext("c:DatosAdicionalesRespuesta/c:MostrarSistemaInformatico", namespaces=sf) == "S"
        tercero = bool(quien) and quien != nif
        if modo == "solo_como_representante" and tercero and not indicador:
            return _fault("4140", describir_error("4140"))
        ejercicio = c.findtext("c:FiltroConsulta/c:PeriodoImputacion/sf:Ejercicio", namespaces=sf)
        periodo = c.findtext("c:FiltroConsulta/c:PeriodoImputacion/sf:Periodo", namespaces=sf)
        num_serie = c.findtext("c:FiltroConsulta/c:NumSerieFactura", namespaces=sf)
        cp = c.find("c:FiltroConsulta/c:ClavePaginacion", sf)
        sql = """SELECT r.*, x.xml, x.xml_alta, COALESCE(p.presentador, r.nif) AS presentador
                 FROM sim_registros r LEFT JOIN sim_registros_xml x
                 ON x.nif=r.nif AND x.num_serie=r.num_serie AND x.fecha=r.fecha
                 LEFT JOIN sim_presentadores p ON p.nif=r.nif AND p.num_serie=r.num_serie AND p.fecha=r.fecha
                 WHERE r.nif=? AND substr(r.fecha, 7, 4)=? AND substr(r.fecha, 4, 2)=?"""
        args: list = [nif, ejercicio, periodo]
        if indicador:
            # «Permite, a quien realiza la consulta, obtener los registros de facturación en los que figura como
            # representante» (SuministroInformacion.xsd): solo los que presentó quien consulta
            sql += " AND COALESCE(p.presentador, r.nif)=?"
            args.append(quien or "")
        if num_serie:
            sql += " AND r.num_serie=?"
            args.append(num_serie)
        filas = sorted(con.execute(sql, args).fetchall(), key=lambda x: (x["num_serie"], x["fecha"]))
        if cp is not None:
            desde = (cp.findtext("sf:NumSerieFactura", namespaces=sf), cp.findtext("sf:FechaExpedicionFactura", namespaces=sf))
            filas = [x for x in filas if (x["num_serie"], x["fecha"]) > desde]
        pagina, mas = filas[:TAM_PAGINA_CONSULTA], len(filas) > TAM_PAGINA_CONSULTA
        resp = _respuesta_consulta(c, ejercicio, periodo, pagina, mas, invalida=(modo == "respuesta_invalida"),
                                   mostrar_si=mostrar_si)
        if modo == "sin_respuesta":
            raise SinRespuesta("Simulador: la consulta se procesó pero la respuesta no llegó")
        return resp


def _marca(iso: str | None) -> str:
    """Fecha y hora con huso de Madrid de una marca guardada. Las guardadas por versiones anteriores no tienen huso: eran la
    hora local del servidor, que en los contenedores (Docker) es UTC, así que se leen como UTC."""
    try:
        d = datetime.fromisoformat(iso or "")
        d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return hmod.fecha_hora_huso(d.astimezone(ZoneInfo(ZONA_HORARIA)))
    except ValueError:
        return hmod.fecha_hora_huso(datetime.now(ZoneInfo(ZONA_HORARIA)))


def _datos_registro(padre, fila, mostrar_si: bool = False) -> None:
    """DatosRegistroFacturacion (RespuestaConsultaLR.xsd) a partir del registro recibido. Para una factura anulada, los
    datos de la factura salen del último alta y la huella y el encadenamiento, del registro de anulación."""
    ns = {"sf": NS_SF}

    def e(p, n, t=None, nsx=NS_RCON):
        x = etree.SubElement(p, f"{{{nsx}}}{n}")
        if t is not None:
            x.text = t
        return x

    def leer(xml):
        try:
            return etree.fromstring(xml.encode("utf-8"), esquemas_xsd.parser_seguro()) if xml else None
        except etree.XMLSyntaxError:
            return None
    ultimo, alta = leer(fila["xml"]), leer(fila["xml_alta"])
    d = e(padre, "DatosRegistroFacturacion")
    if alta is not None:
        for campo in ("Subsanacion", "RechazoPrevio"):
            v = alta.findtext(f"sf:{campo}", namespaces=ns)
            if v and (fila["tipo"] == "alta"):
                e(d, campo, v)
        if ultimo is not None and fila["tipo"] == "anulacion":
            v = ultimo.findtext("sf:SinRegistroPrevio", namespaces=ns)
            if v:
                e(d, "SinRegistroPrevio", v)
        for campo in ("TipoFactura", "TipoRectificativa"):
            v = alta.findtext(f"sf:{campo}", namespaces=ns)
            if v:
                e(d, campo, v)
        fr = alta.find("sf:FacturasRectificadas", ns)
        if fr is not None:
            fr2 = e(d, "FacturasRectificadas")
            for x in fr.findall("sf:IDFacturaRectificada", ns):
                y = e(fr2, "IDFacturaRectificada")
                y.extend([_copia(h) for h in x])
        ir = alta.find("sf:ImporteRectificacion", ns)
        if ir is not None:
            y = e(d, "ImporteRectificacion")
            y.extend([_copia(h) for h in ir])
        for campo in ("FechaOperacion", "DescripcionOperacion"):
            v = alta.findtext(f"sf:{campo}", namespaces=ns)
            if v:
                e(d, campo, v)
        ds = alta.find("sf:Destinatarios", ns)
        if ds is not None:
            ds2 = e(d, "Destinatarios")
            for x in ds.findall("sf:IDDestinatario", ns):
                y = e(ds2, "IDDestinatario")
                y.extend([_copia(h) for h in x])
        de = alta.find("sf:Desglose", ns)
        if de is not None:
            y = e(d, "Desglose")
            y.extend([_copia(h) for h in de])
        for campo in ("CuotaTotal", "ImporteTotal"):
            v = alta.findtext(f"sf:{campo}", namespaces=ns)
            if v:
                e(d, campo, v)
    if ultimo is not None:
        enc = ultimo.find("sf:Encadenamiento", ns)
        if enc is not None:
            y = e(d, "Encadenamiento")
            ra = enc.find("sf:RegistroAnterior", ns)
            if ra is not None:
                z = e(y, "RegistroAnterior")
                z.extend([_copia(h) for h in ra])
            else:
                e(y, "PrimerRegistro", "S")
        si = ultimo.find("sf:SistemaInformatico", ns)
        if mostrar_si and si is not None:   # MostrarSistemaInformatico=S: el bloque del registro, tal como se recibió
            y = e(d, "SistemaInformatico")
            y.extend([_copia(h) for h in si])
        v = ultimo.findtext("sf:FechaHoraHusoGenRegistro", namespaces=ns)
        if v:
            e(d, "FechaHoraHusoGenRegistro", v)
        e(d, "TipoHuella", ultimo.findtext("sf:TipoHuella", namespaces=ns) or "01")
    if fila["huella"]:
        e(d, "Huella", fila["huella"])


def _copia(el):
    from copy import deepcopy
    return deepcopy(el)


def _respuesta_consulta(q, ejercicio, periodo, filas, mas: bool, invalida: bool = False, mostrar_si: bool = False) -> bytes:
    sf = {"sf": NS_SF, "c": NS_CON}
    env = etree.Element(f"{{{NS_SOAP}}}Envelope", nsmap={"env": NS_SOAP, "tikLRRC": NS_RCON, "tik": NS_SF})
    body = etree.SubElement(env, f"{{{NS_SOAP}}}Body")
    r = etree.SubElement(body, f"{{{NS_RCON}}}RespuestaConsultaFactuSistemaFacturacion")

    def e(p, n, t=None, ns=NS_RCON):
        x = etree.SubElement(p, f"{{{ns}}}{n}")
        if t is not None:
            x.text = t
        return x
    cab = e(r, "Cabecera")
    e(cab, "IDVersion", "1.0", NS_SF)
    ob = e(cab, "ObligadoEmision", ns=NS_SF)
    nombre = q.findtext("c:Cabecera/sf:ObligadoEmision/sf:NombreRazon", namespaces=sf)
    nif = q.findtext("c:Cabecera/sf:ObligadoEmision/sf:NIF", namespaces=sf)
    e(ob, "NombreRazon", nombre, NS_SF)
    e(ob, "NIF", nif, NS_SF)
    per = e(r, "PeriodoImputacion")
    e(per, "Ejercicio", ejercicio)
    e(per, "Periodo", periodo)
    if not invalida:   # respuesta_invalida: falta IndicadorPaginacion (no cumple RespuestaConsultaLR.xsd)
        e(r, "IndicadorPaginacion", "S" if mas else "N")
    e(r, "ResultadoConsulta", "ConDatos" if filas else "SinDatos")
    for f in filas:
        rr = e(r, "RegistroRespuestaConsultaFactuSistemaFacturacion")
        idf = e(rr, "IDFactura")
        e(idf, "IDEmisorFactura", f["nif"], NS_SF)
        e(idf, "NumSerieFactura", f["num_serie"], NS_SF)
        e(idf, "FechaExpedicionFactura", f["fecha"], NS_SF)
        _datos_registro(rr, f, mostrar_si)
        if f["id_peticion"]:
            dp = e(rr, "DatosPresentacion")
            e(dp, "NIFPresentador", f["presentador"] or f["nif"], NS_SF)
            e(dp, "TimestampPresentacion", _marca(f["recibido_en"]), NS_SF)
            e(dp, "IdPeticion", f["id_peticion"], NS_SF)
        er = e(rr, "EstadoRegistro")
        e(er, "TimestampUltimaModificacion", _marca(f["recibido_en"]))
        e(er, "EstadoRegistro", _ESTADO_CONSULTA.get(f["estado"], f["estado"]))
        if f["codigo"]:
            e(er, "CodigoErrorRegistro", f["codigo"])
            if f["descripcion"]:
                e(er, "DescripcionErrorRegistro", f["descripcion"][:500])
    if mas and filas:
        cp = e(r, "ClavePaginacion")
        e(cp, "IDEmisorFactura", filas[-1]["nif"], NS_SF)
        e(cp, "NumSerieFactura", filas[-1]["num_serie"], NS_SF)
        e(cp, "FechaExpedicionFactura", filas[-1]["fecha"], NS_SF)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")
