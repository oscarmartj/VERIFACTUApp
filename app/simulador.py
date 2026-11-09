"""SIMULADOR LOCAL del servicio VERI*FACTU (no es la AEAT).

Sirve para pruebas técnicas sin red: valida el XML contra el XSD, comprueba coherencia de importes,
recalcula la huella, detecta duplicados por IDFactura y responde con la estructura de
RespuestaSuministro.xsd y los códigos del catálogo oficial (errores.properties). Permite inyectar
fallos (sin respuesta, servicio caído, error 500, rechazo de cabecera) para probar la recuperación.
Sus respuestas se marcan con el CSV «SIMULADO-…» y nunca se presentan como respuestas de la AEAT.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from lxml import etree

from .config import ZONA_HORARIA, ajustes
from .importes import q2
from .verifactu import esquemas_xsd, huella as hmod
from .verifactu.esquemas_xsd import NS_SF, NS_SFLR, NS_SFR, NS_SOAP
from .verifactu.respuesta import describir_error

_lock = threading.Lock()
MODOS = ("normal", "sin_respuesta", "caido", "error_500", "rechazo", "fault_servidor", "rechazo_registro")


class SinRespuesta(Exception):
    """La petición pudo procesarse pero no llegó respuesta (timeout / corte)."""


class NoEnviado(Exception):
    """La conexión no llegó a establecerse: la petición no se procesó."""


def _db() -> sqlite3.Connection:
    ruta = ajustes().data_dir / "simulador_aeat.sqlite3"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS sim_registros (nif TEXT, num_serie TEXT, fecha TEXT, estado TEXT,
                   id_peticion TEXT, codigo TEXT, descripcion TEXT, huella TEXT, tipo TEXT, recibido_en TEXT,
                   PRIMARY KEY (nif, num_serie, fecha))""")
    con.execute("CREATE TABLE IF NOT EXISTS sim_config (clave TEXT PRIMARY KEY, valor TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS sim_rechazos (nif TEXT, num_serie TEXT, fecha TEXT, tipo TEXT,
                   PRIMARY KEY (nif, num_serie, fecha, tipo))""")
    return con


def fijar_modo(modo: str, veces: int = 1) -> None:
    if modo not in MODOS:
        raise ValueError("Modo desconocido")
    con = _db()
    con.execute("INSERT OR REPLACE INTO sim_config VALUES ('modo', ?)", (modo,))
    con.execute("INSERT OR REPLACE INTO sim_config VALUES ('veces', ?)", (str(veces),))


def modo_actual() -> dict:
    con = _db()
    m = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM sim_config")}
    return {"modo": m.get("modo", "normal"), "veces": int(m.get("veces", "0") or 0)}


def _consumir_modo(con) -> str:
    m = {r["clave"]: r["valor"] for r in con.execute("SELECT * FROM sim_config")}
    modo, veces = m.get("modo", "normal"), int(m.get("veces", "0") or 0)
    if modo != "normal":
        veces -= 1
        if veces <= 0:
            con.execute("INSERT OR REPLACE INTO sim_config VALUES ('modo','normal')")
        con.execute("INSERT OR REPLACE INTO sim_config VALUES ('veces', ?)", (str(max(veces, 0)),))
    return modo


def _t(el, ruta):
    x = el.find(ruta, {"sf": NS_SF})
    return x.text if x is not None else None


def _validar_alta(r) -> tuple[str, str | None]:
    """Devuelve (estado, codigo). Reglas de negocio seleccionadas del documento de validaciones."""
    base = sum((Decimal(x.text) for x in r.findall(".//sf:BaseImponibleOimporteNoSujeto", {"sf": NS_SF})), Decimal(0))
    cuotas = sum((Decimal(x.text) for x in r.findall(".//sf:CuotaRepercutida", {"sf": NS_SF})), Decimal(0))
    for det in r.findall(".//sf:DetalleDesglose", {"sf": NS_SF}):
        t, b, c = (_t(det, f"sf:{k}") for k in ("TipoImpositivo", "BaseImponibleOimporteNoSujeto", "CuotaRepercutida"))
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
        modo = _consumir_modo(con)
        if modo == "caido":
            raise NoEnviado("Simulador: servicio no disponible (conexión rechazada)")
        if modo == "error_500":
            return b"HTTP500"
        doc = etree.fromstring(peticion, esquemas_xsd.parser_seguro())
        reg = doc.find(f".//{{{NS_SFLR}}}RegFactuSistemaFacturacion")
        if reg is None or esquemas_xsd.validar(reg):
            return _fault("4102", describir_error("4102"))
        cab = reg.find(f"{{{NS_SFLR}}}Cabecera")
        nif_cab = cab.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF").text
        if modo == "rechazo":
            return _fault("4112", describir_error("4112"))
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
                con.execute("INSERT OR REPLACE INTO sim_registros VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (nif, ns, fe, nuevo, id_peticion, cod, describir_error(cod) if cod else None,
                             _t(r, "sf:Huella"), "alta" if es_alta else "anulacion", datetime.now().isoformat()))
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
        e(dp, "NIFPresentador", cab_peticion.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF").text, NS_SF)
        e(dp, "TimestampPresentacion", hmod.fecha_hora_huso(datetime.now(ZoneInfo(ZONA_HORARIA))), NS_SF)
    c = e(r, "Cabecera")
    ob = e(c, "ObligadoEmision", ns=NS_SF)
    e(ob, "NombreRazon", cab_peticion.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NombreRazon").text, NS_SF)
    e(ob, "NIF", cab_peticion.find(f"{{{NS_SF}}}ObligadoEmision/{{{NS_SF}}}NIF").text, NS_SF)
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
