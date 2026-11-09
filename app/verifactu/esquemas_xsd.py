"""Validación XML contra los esquemas oficiales vendorizados (sin acceso a red)."""
from __future__ import annotations

from functools import lru_cache

from lxml import etree

from ..config import ESQUEMAS_DIR

NS_SF = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/SuministroInformacion.xsd"
NS_SFLR = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/SuministroLR.xsd"
NS_SFR = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/RespuestaSuministro.xsd"
NS_CON = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/ConsultaLR.xsd"
NS_RCON = "https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/RespuestaConsultaLR.xsd"
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"


class _ResolutorLocal(etree.Resolver):
    def resolve(self, url, pubid, context):
        nombre = url.rsplit("/", 1)[-1]
        ruta = ESQUEMAS_DIR / nombre
        if ruta.exists():
            return self.resolve_filename(str(ruta), context)
        return None  # sin red: si no está en local, falla la carga del esquema


def _parser() -> etree.XMLParser:
    p = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    p.resolvers.add(_ResolutorLocal())
    return p


@lru_cache(maxsize=None)
def esquema(nombre: str) -> etree.XMLSchema:
    doc = etree.parse(str(ESQUEMAS_DIR / nombre), _parser())
    return etree.XMLSchema(doc)


def validar(elemento: etree._Element, nombre_esquema: str = "SuministroLR.xsd") -> list[str]:
    """Devuelve la lista de errores (vacía si valida)."""
    xs = esquema(nombre_esquema)
    doc = etree.ElementTree(etree.fromstring(etree.tostring(elemento)))
    if xs.validate(doc):
        return []
    return [f"línea {e.line}: {e.message}" for e in xs.error_log]


def parser_seguro() -> etree.XMLParser:
    """Parser para XML recibido (respuestas): sin entidades externas ni red."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
