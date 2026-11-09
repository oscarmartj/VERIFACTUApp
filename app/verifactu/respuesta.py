"""Interpretación de respuestas del servicio VERI*FACTU y catálogo oficial de errores."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from lxml import etree

from ..config import ESQUEMAS_DIR
from .esquemas_xsd import NS_SF, NS_SFR, NS_SOAP, parser_seguro


@lru_cache(maxsize=1)
def catalogo_errores() -> dict[str, dict]:
    """Lee errores.properties (catálogo oficial AEAT) con la categoría de cada código."""
    texto = (ESQUEMAS_DIR / "errores.properties").read_bytes().decode("latin-1")
    categoria = "desconocida"
    out: dict[str, dict] = {}
    for linea in texto.splitlines():
        l = linea.strip()
        if l.startswith("*"):
            bajo = l.lower()
            if "envío completo" in bajo or "envio completo" in bajo:
                categoria = "rechazo_envio"
            elif "rechazo de la factura" in bajo:
                categoria = "rechazo_registro"
            elif "aceptación" in bajo or "aceptacion" in bajo:
                categoria = "aceptado_con_errores"
            continue
        if "=" in l and l.split("=", 1)[0].strip().isdigit():
            cod, desc = l.split("=", 1)
            out[cod.strip()] = {"descripcion": desc.strip(), "categoria": categoria}
    return out


def describir_error(codigo: str | None) -> str:
    if not codigo:
        return ""
    e = catalogo_errores().get(str(codigo))
    return e["descripcion"] if e else ""


@dataclass
class LineaRespuesta:
    id_emisor: str
    num_serie: str
    fecha_expedicion: str
    tipo_operacion: str
    estado: str                      # Correcto | AceptadoConErrores | Incorrecto
    codigo_error: str | None = None
    descripcion_error: str | None = None
    duplicado: dict | None = None    # IdPeticionRegistroDuplicado, EstadoRegistroDuplicado, ...
    subsanacion: str | None = None
    rechazo_previo: str | None = None


@dataclass
class Respuesta:
    tipo: str                        # "respuesta" | "fault"
    estado_envio: str | None = None  # Correcto | ParcialmenteCorrecto | Incorrecto
    csv: str | None = None
    tiempo_espera: int | None = None
    timestamp_presentacion: str | None = None
    lineas: list[LineaRespuesta] = field(default_factory=list)
    fault_codigo: str | None = None
    fault_texto: str | None = None


def _t(el, ruta: str, ns: dict) -> str | None:
    x = el.find(ruta, ns)
    return x.text.strip() if x is not None and x.text else None


def interpretar(xml_bytes: bytes) -> Respuesta:
    root = etree.fromstring(xml_bytes, parser_seguro())
    ns = {"env": NS_SOAP, "r": NS_SFR, "sf": NS_SF}
    fault = root.find(".//env:Fault", ns)
    if fault is not None:
        return Respuesta(tipo="fault", fault_codigo=_t(fault, "faultcode", ns), fault_texto=_t(fault, "faultstring", ns))
    resp = root.find(".//r:RespuestaRegFactuSistemaFacturacion", ns)
    if resp is None:
        raise ValueError("Respuesta sin RespuestaRegFactuSistemaFacturacion")
    tiempo = _t(resp, "r:TiempoEsperaEnvio", ns)
    out = Respuesta(
        tipo="respuesta",
        estado_envio=_t(resp, "r:EstadoEnvio", ns),
        csv=_t(resp, "r:CSV", ns),
        tiempo_espera=int(tiempo) if tiempo and tiempo.isdigit() else None,
        timestamp_presentacion=_t(resp, "r:DatosPresentacion/sf:TimestampPresentacion", ns),
    )
    for rl in resp.findall("r:RespuestaLinea", ns):
        idf = rl.find("r:IDFactura", ns)
        dup = rl.find("r:RegistroDuplicado", ns)
        duplicado = None
        if dup is not None:
            duplicado = {
                "id_peticion": _t(dup, "sf:IdPeticionRegistroDuplicado", ns),
                "estado": _t(dup, "sf:EstadoRegistroDuplicado", ns),
                "codigo_error": _t(dup, "sf:CodigoErrorRegistro", ns),
                "descripcion_error": _t(dup, "sf:DescripcionErrorRegistro", ns),
            }
        out.lineas.append(LineaRespuesta(
            id_emisor=_t(idf, "sf:IDEmisorFactura", ns) or "",
            num_serie=_t(idf, "sf:NumSerieFactura", ns) or "",
            fecha_expedicion=_t(idf, "sf:FechaExpedicionFactura", ns) or "",
            tipo_operacion=_t(rl, "r:Operacion/sf:TipoOperacion", ns) or "",
            subsanacion=_t(rl, "r:Operacion/sf:Subsanacion", ns),
            rechazo_previo=_t(rl, "r:Operacion/sf:RechazoPrevio", ns),
            estado=_t(rl, "r:EstadoRegistro", ns) or "",
            codigo_error=_t(rl, "r:CodigoErrorRegistro", ns),
            descripcion_error=_t(rl, "r:DescripcionErrorRegistro", ns),
            duplicado=duplicado,
        ))
    return out
