"""Interpretación de la respuesta del servicio de consulta (RespuestaConsultaFactuSistemaFacturacion,
RespuestaConsultaLR.xsd). El XML recibido se trata como datos: parser sin entidades externas ni red."""
from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from . import esquemas_xsd
from .esquemas_xsd import NS_RCON, NS_SF, NS_SOAP, parser_seguro

NS = {"env": NS_SOAP, "rc": NS_RCON, "sf": NS_SF}
ESTADOS_AEAT = ("Correcto", "AceptadoConErrores", "Anulado")


@dataclass
class RegistroConsultado:
    id_emisor: str
    num_serie: str
    fecha_expedicion: str                 # dd-mm-aaaa
    estado: str                           # Correcto | AceptadoConErrores | Anulado
    codigo_error: str | None = None
    descripcion_error: str | None = None
    timestamp_modificacion: str | None = None
    huella: str | None = None
    fecha_hora_huso: str | None = None
    tipo_factura: str | None = None
    cuota_total: str | None = None
    importe_total: str | None = None
    huella_anterior: str | None = None
    primer_registro: bool = False
    id_peticion: str | None = None
    timestamp_presentacion: str | None = None
    nif_presentador: str | None = None
    # Bloque SistemaInformatico (solo si se pidió con MostrarSistemaInformatico=S y la respuesta lo trae)
    sistema_nif: str | None = None
    sistema_id: str | None = None
    sistema_instalacion: str | None = None

    def clave(self) -> tuple[str, str]:
        return (self.num_serie, self.fecha_expedicion)

    def a_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RespuestaConsulta:
    tipo: str                             # "respuesta" | "fault"
    nif: str | None = None
    ejercicio: str | None = None
    periodo: str | None = None
    indicador_paginacion: str | None = None
    resultado: str | None = None          # ConDatos | SinDatos
    registros: list[RegistroConsultado] = field(default_factory=list)
    clave_paginacion: dict | None = None
    errores_xsd: list[str] = field(default_factory=list)
    fault_codigo: str | None = None
    fault_texto: str | None = None


def _t(el, ruta: str) -> str | None:
    if el is None:
        return None
    x = el.find(ruta, NS)
    return x.text.strip() if x is not None and x.text else None


def interpretar(xml_bytes: bytes) -> RespuestaConsulta:
    """Lee la respuesta (sobre SOAP o el elemento suelto). Valida contra RespuestaConsultaLR.xsd y anota los errores en
    `errores_xsd` (quien la usa decide si los admite). Lanza ValueError si no es una respuesta de consulta."""
    root = etree.fromstring(xml_bytes, parser_seguro())
    fault = root.find(".//env:Fault", NS)
    if fault is not None:
        return RespuestaConsulta(tipo="fault", fault_codigo=_t(fault, "faultcode"), fault_texto=_t(fault, "faultstring"))
    resp = root if root.tag == f"{{{NS_RCON}}}RespuestaConsultaFactuSistemaFacturacion" else \
        root.find(".//rc:RespuestaConsultaFactuSistemaFacturacion", NS)
    if resp is None:
        raise ValueError("Respuesta sin RespuestaConsultaFactuSistemaFacturacion")
    out = RespuestaConsulta(
        tipo="respuesta",
        nif=_t(resp, "rc:Cabecera/sf:ObligadoEmision/sf:NIF"),
        ejercicio=_t(resp, "rc:PeriodoImputacion/rc:Ejercicio"),
        periodo=_t(resp, "rc:PeriodoImputacion/rc:Periodo"),
        indicador_paginacion=_t(resp, "rc:IndicadorPaginacion"),
        resultado=_t(resp, "rc:ResultadoConsulta"),
        errores_xsd=esquemas_xsd.validar(resp, "RespuestaConsultaLR.xsd"),
    )
    for r in resp.findall("rc:RegistroRespuestaConsultaFactuSistemaFacturacion", NS):
        idf = r.find("rc:IDFactura", NS)
        datos = r.find("rc:DatosRegistroFacturacion", NS)
        est = r.find("rc:EstadoRegistro", NS)
        pres = r.find("rc:DatosPresentacion", NS)
        enc = datos.find("rc:Encadenamiento", NS) if datos is not None else None
        si = datos.find("rc:SistemaInformatico", NS) if datos is not None else None
        out.registros.append(RegistroConsultado(
            id_emisor=_t(idf, "sf:IDEmisorFactura") or "",
            num_serie=_t(idf, "sf:NumSerieFactura") or "",
            fecha_expedicion=_t(idf, "sf:FechaExpedicionFactura") or "",
            estado=_t(est, "rc:EstadoRegistro") or "",
            codigo_error=_t(est, "rc:CodigoErrorRegistro"),
            descripcion_error=_t(est, "rc:DescripcionErrorRegistro"),
            timestamp_modificacion=_t(est, "rc:TimestampUltimaModificacion"),
            huella=_t(datos, "rc:Huella"),
            fecha_hora_huso=_t(datos, "rc:FechaHoraHusoGenRegistro"),
            tipo_factura=_t(datos, "rc:TipoFactura"),
            cuota_total=_t(datos, "rc:CuotaTotal"),
            importe_total=_t(datos, "rc:ImporteTotal"),
            huella_anterior=_t(enc, "rc:RegistroAnterior/sf:Huella"),
            primer_registro=_t(enc, "rc:PrimerRegistro") == "S",
            id_peticion=_t(pres, "sf:IdPeticion"),
            timestamp_presentacion=_t(pres, "sf:TimestampPresentacion"),
            nif_presentador=_t(pres, "sf:NIFPresentador"),
            sistema_nif=_t(si, "sf:NIF"),
            sistema_id=_t(si, "sf:IdSistemaInformatico"),
            sistema_instalacion=_t(si, "sf:NumeroInstalacion"),
        ))
    cp = resp.find("rc:ClavePaginacion", NS)
    if cp is not None:
        out.clave_paginacion = {"id_emisor": _t(cp, "sf:IDEmisorFactura") or "", "num_serie": _t(cp, "sf:NumSerieFactura") or "",
                                "fecha_expedicion": _t(cp, "sf:FechaExpedicionFactura") or ""}
    return out
