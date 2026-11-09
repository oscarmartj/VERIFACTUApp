"""Caso 7: huellas contra los ejemplos oficiales de la AEAT; XML contra los XSD; QR; NIF; importes."""
from decimal import Decimal

from lxml import etree

from app import nif
from app.importes import fmt_es, fmt_xml, parse_importe
from app.verifactu import esquemas_xsd, huella, qr, xmlgen
from app.verifactu.respuesta import catalogo_errores

# Ejemplos del documento AEAT «Detalle de las especificaciones técnicas para generación de la huella o hash»
H1 = "3C464DAF61ACB827C65FDA19F352A4E3BDC2C640E9E9FC4CC058073F38F12F60"
H2 = "F7B94CFD8924EDFF273501B01EE5153E4CE8F259766F88CF6ACB8935802A2B97"
H3 = "177547C0D57AC74748561D054A9CEC14B4C4EA23D1BEFD6F2E69E3A388F90C68"


def test_huella_ejemplos_oficiales():
    h1 = huella.huella_alta(id_emisor="89890001K", num_serie="12345678/G33", fecha_expedicion="01-01-2024", tipo_factura="F1",
                            cuota_total="12.35", importe_total="123.45", huella_anterior="", fecha_hora_huso="2024-01-01T19:20:30+01:00")
    assert h1 == H1
    h2 = huella.huella_alta(id_emisor="89890001K", num_serie="12345679/G34", fecha_expedicion="01-01-2024", tipo_factura="F1",
                            cuota_total="12.35", importe_total="123.45", huella_anterior=h1, fecha_hora_huso="2024-01-01T19:20:35+01:00")
    assert h2 == H2
    h3 = huella.huella_anulacion(id_emisor="89890001K", num_serie="12345679/G34", fecha_expedicion="01-01-2024",
                                 huella_anterior=h2, fecha_hora_huso="2024-01-01T19:20:40+01:00")
    assert h3 == H3


def test_huella_recorta_espacios():
    a = huella.cadena_alta(id_emisor=" 89890001K ", num_serie="A ", fecha_expedicion="01-01-2024", tipo_factura="F1",
                           cuota_total="1.00", importe_total="2.00", huella_anterior="", fecha_hora_huso="2024-01-01T00:00:00+01:00")
    assert a.startswith("IDEmisorFactura=89890001K&NumSerieFactura=A&")


def _si():
    return xmlgen.SistemaInformatico("PRODUCTOR", "89890001K", "FacturaPDF VF", "FV", "0.1.0", "INST-1")


def test_xml_alta_y_anulacion_validan_xsd():
    alta = xmlgen.registro_alta(xmlgen.DatosAlta(
        id_emisor="89890001K", nombre_razon_emisor="Emisor", num_serie="166/26", fecha_expedicion="09-11-2026", tipo_factura="F1",
        descripcion_operacion="Servicios", desglose=[{"calificacion": "S1", "tipo_impositivo": "21.00", "base": "850.00", "cuota": "178.50"}],
        cuota_total="178.50", importe_total="1028.50", fecha_hora_huso="2026-11-09T10:00:00+01:00", huella=H1, encadenamiento=None,
        sistema=_si(), destinatarios=[{"nombre": "Cliente", "nif": "B82991316"}]))
    an = xmlgen.registro_anulacion(xmlgen.DatosAnulacion("89890001K", "166/26", "09-11-2026", "2026-11-09T10:00:05+01:00", H2,
                                                         {"id_emisor": "89890001K", "num_serie": "166/26", "fecha_expedicion": "09-11-2026", "huella": H1},
                                                         _si(), sin_registro_previo="S"))
    msg = xmlgen.mensaje_remision({"nombre": "Emisor", "nif": "89890001K", "incidencia": True},
                                  [xmlgen.a_texto(alta), xmlgen.a_texto(an)])
    assert esquemas_xsd.validar(msg) == []
    soap = xmlgen.sobre_soap(msg)
    assert b"RegFactuSistemaFacturacion" in soap and b"<sum1:Incidencia>S</sum1:Incidencia>" in soap


def test_xml_invalido_detectado():
    alta = xmlgen.registro_alta(xmlgen.DatosAlta(
        id_emisor="89890001K", nombre_razon_emisor="E", num_serie="1", fecha_expedicion="2026-11-09", tipo_factura="F1",
        descripcion_operacion="x", desglose=[{"calificacion": "S1", "tipo_impositivo": "21.00", "base": "1,00", "cuota": "0.21"}],
        cuota_total="0.21", importe_total="1.21", fecha_hora_huso="2026-11-09T10:00:00+01:00", huella=H1, encadenamiento=None, sistema=_si()))
    errores = esquemas_xsd.validar(xmlgen.mensaje_remision({"nombre": "E", "nif": "89890001K"}, [xmlgen.a_texto(alta)]))
    assert errores  # fecha con formato incorrecto e importe con coma


def test_url_qr():
    u = qr.url_cotejo("pruebas", nif="89890001K", num_serie="12345678&G33", fecha_expedicion="01-09-2024", importe_total="241.40")
    assert u == "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=89890001K&numserie=12345678%26G33&fecha=01-09-2024&importe=241.40"
    assert qr.url_cotejo("produccion", nif="A", num_serie="1/2", fecha_expedicion="x", importe_total="1").startswith(
        "https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR?")
    assert qr.matriz(u).error == "M"


def test_nif_e_importes():
    assert nif.es_valido("B82991316") and nif.es_valido("12345678Z") and nif.es_valido("X1234567L")
    assert not nif.es_valido("B32991316")
    assert parse_importe("1.028,50 €") == Decimal("1028.50")
    assert parse_importe("850,00") == Decimal("850.00")
    assert parse_importe("1028.50") == Decimal("1028.50")
    assert parse_importe("25/09/2026") is None
    assert fmt_xml("178.5") == "178.50" and fmt_es("1028.5") == "1.028,50 €"


def test_catalogo_errores_oficial():
    c = catalogo_errores()
    assert c["3000"]["categoria"] == "rechazo_registro"
    assert c["2000"]["categoria"] == "aceptado_con_errores"
    assert c["4112"]["categoria"] == "rechazo_envio"


def test_ambito_emisor():
    from app.borradores import evaluar_ambito
    base = {"tipo_contribuyente": "sociedad_IS", "territorio": "comun", "sii": 0, "canarias_ceuta_melilla": 0}
    assert evaluar_ambito(base)["puede_emitir"]
    assert not evaluar_ambito(base | {"sii": 1})["puede_emitir"]                  # SII excluido (art. 3 RRSIF)
    assert not evaluar_ambito(base | {"territorio": "pais_vasco"})["puede_emitir"]  # territorio foral
    assert not evaluar_ambito(base | {"territorio": "navarra"})["puede_emitir"]
    assert not evaluar_ambito(base | {"canarias_ceuta_melilla": 1})["puede_emitir"]  # IGIC/IPSI no implementado
    assert not evaluar_ambito(base | {"sii": None})["puede_emitir"]               # sin respuesta no se emite
    r = evaluar_ambito(base | {"origen_pdf": "programa_facturacion"})
    assert r["puede_emitir"] and any("programa de facturación" in m for m in r["motivos"])
