"""Generación de los registros de facturación (alta y anulación) y de los mensajes SOAP.

Estructura según SuministroLR.xsd / SuministroInformacion.xsd (IDVersion 1.0).
Los registros se guardan como XML independiente (RegistroAlta / RegistroAnulacion) en el
momento de la expedición; el mensaje de remisión solo los envuelve, de forma que un
reintento reenvía exactamente el mismo registro (misma huella y marca temporal).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from .esquemas_xsd import NS_CON, NS_SF, NS_SFLR, NS_SOAP

ID_VERSION = "1.0"
NSMAP_REG = {"sf": NS_SF}


def _e(padre, nombre: str, texto: str | None = None, ns: str = NS_SF):
    el = etree.SubElement(padre, f"{{{ns}}}{nombre}")
    if texto is not None:
        el.text = texto
    return el


@dataclass
class SistemaInformatico:
    nombre_razon: str
    nif: str
    nombre_sistema: str
    id_sistema: str
    version: str
    numero_instalacion: str
    tipo_uso_solo_verifactu: str = "S"
    tipo_uso_multi_ot: str = "N"
    indicador_multiples_ot: str = "N"


@dataclass
class DatosAlta:
    id_emisor: str
    nombre_razon_emisor: str
    num_serie: str
    fecha_expedicion: str           # dd-mm-aaaa
    tipo_factura: str               # F1, R1...
    descripcion_operacion: str
    desglose: list[dict]            # impuesto, clave_regimen, calificacion, tipo_impositivo, base, cuota
    cuota_total: str
    importe_total: str
    fecha_hora_huso: str
    huella: str
    encadenamiento: dict | None     # None => PrimerRegistro
    sistema: SistemaInformatico
    destinatarios: list[dict] = field(default_factory=list)  # nombre, nif
    fecha_operacion: str | None = None
    tipo_rectificativa: str | None = None
    facturas_rectificadas: list[dict] = field(default_factory=list)
    importe_rectificacion: dict | None = None
    subsanacion: str | None = None
    rechazo_previo: str | None = None
    ref_externa: str | None = None


@dataclass
class DatosAnulacion:
    id_emisor: str
    num_serie: str
    fecha_expedicion: str
    fecha_hora_huso: str
    huella: str
    encadenamiento: dict | None
    sistema: SistemaInformatico
    sin_registro_previo: str | None = None
    rechazo_previo: str | None = None
    generado_por: str | None = None
    ref_externa: str | None = None


def _sistema(padre, s: SistemaInformatico):
    si = _e(padre, "SistemaInformatico")
    _e(si, "NombreRazon", s.nombre_razon)
    _e(si, "NIF", s.nif)
    _e(si, "NombreSistemaInformatico", s.nombre_sistema)
    _e(si, "IdSistemaInformatico", s.id_sistema)
    _e(si, "Version", s.version)
    _e(si, "NumeroInstalacion", s.numero_instalacion)
    _e(si, "TipoUsoPosibleSoloVerifactu", s.tipo_uso_solo_verifactu)
    _e(si, "TipoUsoPosibleMultiOT", s.tipo_uso_multi_ot)
    _e(si, "IndicadorMultiplesOT", s.indicador_multiples_ot)


def _encadenamiento(padre, enc: dict | None):
    e = _e(padre, "Encadenamiento")
    if enc is None:
        _e(e, "PrimerRegistro", "S")
    else:
        ra = _e(e, "RegistroAnterior")
        _e(ra, "IDEmisorFactura", enc["id_emisor"])
        _e(ra, "NumSerieFactura", enc["num_serie"])
        _e(ra, "FechaExpedicionFactura", enc["fecha_expedicion"])
        _e(ra, "Huella", enc["huella"])


def registro_alta(d: DatosAlta) -> etree._Element:
    r = etree.Element(f"{{{NS_SF}}}RegistroAlta", nsmap=NSMAP_REG)
    _e(r, "IDVersion", ID_VERSION)
    idf = _e(r, "IDFactura")
    _e(idf, "IDEmisorFactura", d.id_emisor)
    _e(idf, "NumSerieFactura", d.num_serie)
    _e(idf, "FechaExpedicionFactura", d.fecha_expedicion)
    if d.ref_externa:
        _e(r, "RefExterna", d.ref_externa)
    _e(r, "NombreRazonEmisor", d.nombre_razon_emisor)
    if d.subsanacion:
        _e(r, "Subsanacion", d.subsanacion)
    if d.rechazo_previo:
        _e(r, "RechazoPrevio", d.rechazo_previo)
    _e(r, "TipoFactura", d.tipo_factura)
    if d.tipo_rectificativa:
        _e(r, "TipoRectificativa", d.tipo_rectificativa)
    if d.facturas_rectificadas:
        fr = _e(r, "FacturasRectificadas")
        for f in d.facturas_rectificadas:
            x = _e(fr, "IDFacturaRectificada")
            _e(x, "IDEmisorFactura", f["id_emisor"])
            _e(x, "NumSerieFactura", f["num_serie"])
            _e(x, "FechaExpedicionFactura", f["fecha_expedicion"])
    if d.importe_rectificacion:
        ir = _e(r, "ImporteRectificacion")
        _e(ir, "BaseRectificada", d.importe_rectificacion["base"])
        _e(ir, "CuotaRectificada", d.importe_rectificacion["cuota"])
    if d.fecha_operacion:
        _e(r, "FechaOperacion", d.fecha_operacion)
    _e(r, "DescripcionOperacion", d.descripcion_operacion)
    if d.destinatarios:
        ds = _e(r, "Destinatarios")
        for dest in d.destinatarios:
            x = _e(ds, "IDDestinatario")
            _e(x, "NombreRazon", dest["nombre"])
            _e(x, "NIF", dest["nif"])
    des = _e(r, "Desglose")
    for linea in d.desglose:
        dd = _e(des, "DetalleDesglose")
        _e(dd, "Impuesto", linea.get("impuesto", "01"))
        _e(dd, "ClaveRegimen", linea.get("clave_regimen", "01"))
        _e(dd, "CalificacionOperacion", linea["calificacion"])
        _e(dd, "TipoImpositivo", linea["tipo_impositivo"])
        _e(dd, "BaseImponibleOimporteNoSujeto", linea["base"])
        _e(dd, "CuotaRepercutida", linea["cuota"])
    _e(r, "CuotaTotal", d.cuota_total)
    _e(r, "ImporteTotal", d.importe_total)
    _encadenamiento(r, d.encadenamiento)
    _sistema(r, d.sistema)
    _e(r, "FechaHoraHusoGenRegistro", d.fecha_hora_huso)
    _e(r, "TipoHuella", "01")
    _e(r, "Huella", d.huella)
    return r


def registro_anulacion(d: DatosAnulacion) -> etree._Element:
    r = etree.Element(f"{{{NS_SF}}}RegistroAnulacion", nsmap=NSMAP_REG)
    _e(r, "IDVersion", ID_VERSION)
    idf = _e(r, "IDFactura")
    _e(idf, "IDEmisorFacturaAnulada", d.id_emisor)
    _e(idf, "NumSerieFacturaAnulada", d.num_serie)
    _e(idf, "FechaExpedicionFacturaAnulada", d.fecha_expedicion)
    if d.ref_externa:
        _e(r, "RefExterna", d.ref_externa)
    if d.sin_registro_previo:
        _e(r, "SinRegistroPrevio", d.sin_registro_previo)
    if d.rechazo_previo:
        _e(r, "RechazoPrevio", d.rechazo_previo)
    if d.generado_por:
        _e(r, "GeneradoPor", d.generado_por)
    _encadenamiento(r, d.encadenamiento)
    _sistema(r, d.sistema)
    _e(r, "FechaHoraHusoGenRegistro", d.fecha_hora_huso)
    _e(r, "TipoHuella", "01")
    _e(r, "Huella", d.huella)
    return r


def a_texto(el: etree._Element) -> str:
    return etree.tostring(el, encoding="unicode")


def mensaje_remision(cabecera: dict, registros_xml: list[str]) -> etree._Element:
    """RegFactuSistemaFacturacion con 1..1000 registros ya generados (texto XML)."""
    if not 1 <= len(registros_xml) <= 1000:
        raise ValueError("Un envío debe contener entre 1 y 1000 registros")
    root = etree.Element(f"{{{NS_SFLR}}}RegFactuSistemaFacturacion", nsmap={"sum": NS_SFLR, "sum1": NS_SF})
    cab = _e(root, "Cabecera", ns=NS_SFLR)
    ob = _e(cab, "ObligadoEmision")
    _e(ob, "NombreRazon", cabecera["nombre"])
    _e(ob, "NIF", cabecera["nif"])
    if cabecera.get("representante"):
        rep = _e(cab, "Representante")
        _e(rep, "NombreRazon", cabecera["representante"]["nombre"])
        _e(rep, "NIF", cabecera["representante"]["nif"])
    if cabecera.get("incidencia"):
        rv = _e(cab, "RemisionVoluntaria")
        _e(rv, "Incidencia", "S")
    for rx in registros_xml:
        rf = _e(root, "RegistroFactura", ns=NS_SFLR)
        rf.append(etree.fromstring(rx.encode("utf-8")))
    return root


def mensaje_consulta(*, nif: str, nombre: str, ejercicio: str, periodo: str,
                     num_serie: str | None = None, fecha_expedicion: str | None = None) -> etree._Element:
    root = etree.Element(f"{{{NS_CON}}}ConsultaFactuSistemaFacturacion", nsmap={"con": NS_CON, "sum1": NS_SF})
    cab = _e(root, "Cabecera", ns=NS_CON)
    _e(cab, "IDVersion", ID_VERSION)
    ob = _e(cab, "ObligadoEmision")
    _e(ob, "NombreRazon", nombre)
    _e(ob, "NIF", nif)
    filtro = _e(root, "FiltroConsulta", ns=NS_CON)
    per = _e(filtro, "PeriodoImputacion", ns=NS_CON)
    _e(per, "Ejercicio", ejercicio)
    _e(per, "Periodo", periodo)
    if num_serie:
        _e(filtro, "NumSerieFactura", num_serie, ns=NS_CON)
    if fecha_expedicion:
        fe = _e(filtro, "FechaExpedicionFactura", ns=NS_CON)
        _e(fe, "FechaExpedicionFactura", fecha_expedicion)
    return root


def sobre_soap(cuerpo: etree._Element) -> bytes:
    env = etree.Element(f"{{{NS_SOAP}}}Envelope", nsmap={"soapenv": NS_SOAP})
    etree.SubElement(env, f"{{{NS_SOAP}}}Header")
    body = etree.SubElement(env, f"{{{NS_SOAP}}}Body")
    body.append(cuerpo)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")
