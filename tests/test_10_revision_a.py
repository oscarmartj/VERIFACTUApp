"""Regresiones de la revisión (carril A): emisor y destinatario.

Facturas sintéticas generadas con reportlab (nombres y NIF ficticios, con dígito de control válido salvo donde se indica).
"""
import io
import sqlite3

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, texto
from app.nif import completar_cif

EMP = completar_cif("B", "1234567")        # empresa configurada (emisor de sus borradores)
CLI = completar_cif("A", "8765432")        # cliente
TER = completar_cif("A", "9990001")        # tercero citado en el documento
PROV = completar_cif("B", "0000033")       # proveedor de una factura recibida
DNI = "12345678Z"


def _pdf(ops) -> bytes:
    """ops: (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _, alto = A4
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 9)
        (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, alto - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos: bytes, nif_empresa=EMP):
    ext = campos.Extractor(texto.leer(datos), "f.pdf", nif_empresa).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "f.pdf")


def _validar(ext, datos, cambiados=None, razon="EMPRESA DEMO S.L.", domicilio="C/ Mayor 12, 28001 Madrid"):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    empresa = {"id": 1, "nif": EMP, "razon_social": razon, "domicilio": domicilio, "ambito_confirmado_en": "x",
               "ambito_dict": {"puede_emitir": True}}
    documento = {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}
    serie = {"siguiente_numero": 1, "codigo": "X", "numero_detectado": None}
    return borradores.validar(con, empresa, documento, datos, extraccion=ext, serie=serie, cambiados=cambiados)


def _cuerpo(y0=180):
    return [(50, y0, "Concepto", 9, "Helvetica-Bold"), (330, y0, "Cantidad", 9, "Helvetica-Bold"),
            (420, y0, "Precio", 9, "Helvetica-Bold"), (540, y0, "Importe", 9, "Helvetica-Bold", "r"),
            (50, y0 + 18, "Mantenimiento de instalaciones"), (370, y0 + 18, "1", 9, "Helvetica", "r"),
            (460, y0 + 18, "700,00", 9, "Helvetica", "r"), (540, y0 + 18, "700,00", 9, "Helvetica", "r"),
            (380, y0 + 60, "Base imponible"), (540, y0 + 60, "700,00", 9, "Helvetica", "r"),
            (380, y0 + 74, "IVA 21%"), (540, y0 + 74, "147,00", 9, "Helvetica", "r"),
            (380, y0 + 88, "Total factura"), (540, y0 + 88, "847,00", 9, "Helvetica", "r")]


def _factura_columna(lineas_cliente):
    """Plantilla sencilla: cabecera del emisor arriba a la izquierda y el bloque del cliente justo debajo."""
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
           (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"), (380, 74, "Fecha: 15/10/2026")]
    ops += [(50, 96 + 12 * k, s) for k, s in enumerate(lineas_cliente)]
    return _pdf(ops + _cuerpo())


# --- silent:F1 — el nombre y el domicilio del destinatario no se toman del bloque del emisor -------------------------
@pytest.mark.parametrize("lineas, nif, nombre, domicilio", [
    ([f"Cliente: DISTRIBUCIONES NORTE S.A. - CIF: {CLI}", "Domicilio: Av. Libertad 5, 46001 Valencia"],
     CLI, "DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5, 46001 Valencia"),
    (["Cliente: DISTRIBUCIONES NORTE S.A.", f"CIF: {CLI}", "Av. Libertad 5, 46001 Valencia"],
     CLI, "DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5, 46001 Valencia"),
    (["Sr. D. Juan Pérez García", "C/ Sol 3, 28002 Madrid", f"NIF: {DNI}"], DNI, "Juan Pérez García", "C/ Sol 3, 28002 Madrid"),
    (["Cliente:", "AYUNTAMIENTO DE VILLAEJEMPLO", "Plaza de España 1, 45600 Villaejemplo", f"CIF: {completar_cif('P', '1234560')}"],
     completar_cif("P", "1234560"), "AYUNTAMIENTO DE VILLAEJEMPLO", "Plaza de España 1, 45600 Villaejemplo"),
    (["COMUNIDAD DE PROPIETARIOS CALLE SOL 3", "C/ Sol 3, 28002 Madrid", f"CIF: {completar_cif('H', '7654321')}"],
     completar_cif("H", "7654321"), "COMUNIDAD DE PROPIETARIOS CALLE SOL 3", "C/ Sol 3, 28002 Madrid"),
    # la persona de contacto no es la razón social ni cierra el bloque
    (["DISTRIBUCIONES NORTE S.A.", "A la atención de: Departamento de Compras", "Av. Libertad 5, 46001 Valencia", f"CIF: {CLI}"],
     CLI, "DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5, 46001 Valencia"),
    (["Cliente:", "DISTRIBUCIONES NORTE", "A la atención de D. Juan Pérez", "Av. Libertad 5, 46001 Valencia", f"CIF: {CLI}"],
     CLI, "DISTRIBUCIONES NORTE", "Av. Libertad 5, 46001 Valencia"),
])
def test_destinatario_no_toma_el_bloque_del_emisor(lineas, nif, nombre, domicilio):
    ext, d = _extraer(_factura_columna(lineas))
    assert d["emisor"]["nif"] == EMP and d["emisor"]["nombre"] == "EMPRESA DEMO S.L."
    assert d["destinatario"]["nif"] == nif
    assert d["destinatario"]["nombre"] == nombre
    assert d["destinatario"]["domicilio"] == domicilio
    inc = _validar(ext, d)
    assert not [i for i in inc if i["campo"].startswith("destinatario")]


def test_rotulo_emisor_bajo_el_titulo_no_corta_el_nombre():
    """La razón social del emisor como título encima de su rótulo «Emisor»; el cliente en la columna de al lado."""
    ops = [(50, 57, "TRADUCCIONES EJEMPLO, S.L.", 16, "Helvetica-Bold"), (460, 50, "Factura", 14),
           (50, 105, "Emisor"), (218, 105, "Facturar a"), (50, 118, "Calle Pintor Ejemplo, 9"), (218, 118, "DISTRIBUCIONES NORTE S.A."),
           (50, 130, "46004 Valencia"), (218, 131, "Paseo Ejemplo, 120"), (50, 142, f"N.I.F. {EMP}"), (218, 144, "50015 Zaragoza"),
           (218, 157, f"NIF {CLI}"), (50, 190, "Factura núm."), (50, 201, "MA-0187/26"), (50, 224, "Fecha"), (50, 236, "27.10.2026")]
    ext, d = _extraer(_pdf(ops + _cuerpo(300)))
    assert d["emisor"]["nif"] == EMP and d["emisor"]["nombre"] == "TRADUCCIONES EJEMPLO, S.L."
    assert d["destinatario"]["nif"] == CLI and d["destinatario"]["nombre"] == "DISTRIBUCIONES NORTE S.A."
    assert "50015" in d["destinatario"]["domicilio"] and "46004" not in d["destinatario"]["domicilio"]


def test_validar_bloquea_destinatario_con_los_datos_del_emisor():
    """Red de seguridad: un destinatario con la razón social o el domicilio del emisor no pasa sin revisión."""
    ext, d = _extraer(_factura_columna(["Cliente: DISTRIBUCIONES NORTE S.A.", f"CIF: {CLI}", "Av. Libertad 5, 46001 Valencia"]))
    d["destinatario"]["nombre"] = "Empresa Demo, S.L."
    d["destinatario"]["domicilio"] = "C/ Mayor 12, 28001 Madrid, Av. Libertad 5, 46001 Valencia"
    cod = {(i["nivel"], i["codigo"]) for i in _validar(ext, d)}
    assert ("bloqueo", "DEST_NOMBRE_ES_EMISOR") in cod
    assert ("confirmar", "DEST_DOMICILIO_EMISOR") in cod


def test_misma_razon_social_no_se_asigna_a_las_dos_partes():
    """Emisor sin NIF en la cabecera (solo en el pie): la cabecera no es el nombre del cliente que va debajo."""
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"),
           (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"), (380, 74, "Fecha: 15/10/2026"),
           (50, 66, "DISTRIBUCIONES NORTE"), (50, 78, "Av. Libertad 5, 46001 Valencia"), (50, 90, f"CIF: {CLI}"),
           (50, 800, f"EMPRESA DEMO S.L. - CIF: {EMP} - Inscrita en el Registro Mercantil de Madrid, Tomo 1, Folio 2", 7)]
    ext, d = _extraer(_pdf(ops + _cuerpo()))
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == CLI
    assert d["destinatario"]["nombre"] != d["emisor"]["nombre"]
    assert "Mayor 12" not in (d["destinatario"]["domicilio"] or "")


# --- silent:F3 — un tercero sin indicios de destinatario no es «seguro» -----------------------------------------------
def _factura_cesion(con_nif_cliente: bool) -> bytes:
    ops = [(50, 50, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 64, "C/ Mayor 12, 28001 Madrid"), (50, 76, f"CIF: {EMP}"),
           (380, 50, "FACTURA", 18, "Helvetica-Bold"), (380, 72, "Nº factura: F-2026/045"), (380, 84, "Fecha: 15/10/2026"),
           (330, 120, "Cliente:", 9, "Helvetica-Bold"), (330, 132, "DISTRIBUCIONES NORTE S.A."), (330, 144, "Av. Libertad 5"),
           (330, 156, "46001 Valencia")]
    if con_nif_cliente:
        ops.append((330, 168, f"CIF: {CLI}"))
    ops += _cuerpo(230) + [(50, 360, "Crédito cedido a BANCO FACTORING EJEMPLO S.A."), (50, 372, f"CIF: {TER}"),
                           (50, 384, "Paseo de la Castellana 100, 28046 Madrid")]
    return _pdf(ops)


def test_tercero_citado_no_es_destinatario_seguro():
    ext, d = _extraer(_factura_cesion(True))
    assert d["destinatario"]["nif"] == CLI and ext["destinatario"]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    ext, d = _extraer(_factura_cesion(False))
    cn = ext["destinatario"]["nif"]
    assert cn["valor"] == TER
    assert cn["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("pocos indicios" in a for a in cn["avisos"])
    assert not any(a == "Rol deducido: posición" for a in cn["avisos"])
    inc = _validar(ext, d)
    assert any(i["codigo"] == "DUDOSO" and i["campo"] == "destinatario.nif" for i in inc)


# --- robust:F7 — un NIF seguido de un carácter suelto no se pierde ---------------------------------------------------
@pytest.mark.parametrize("cabecera", [f"CIF: {EMP} - C/ Mayor 1, 28001 Madrid", f"CIF: {EMP} C/ Mayor 1, 28001 Madrid",
                                      f"CIF: {EMP} y teléfono 941000000", f"CIF: {EMP} 1ª planta, 28001 Madrid"])
def test_nif_seguido_de_un_caracter_suelto(cabecera):
    ops = [(50, 40, "TALLERES EJEMPLO S.L."), (50, 54, cabecera), (50, 120, "Cliente: COMERCIAL NORTE S.A."), (50, 134, f"NIF: {CLI}")]
    ext, d = _extraer(_pdf(ops + _cuerpo(220)))
    assert EMP in ext["nifs_detectados"]
    assert d["emisor"]["nif"] == EMP
    if "C/ Mayor" in cabecera:
        assert "Mayor 1" in (d["emisor"]["domicilio"] or "")


def test_nifs_en_reintenta_la_forma_corta():
    assert [n for n, _, _ in campos._nifs_en(f"CIF {EMP} C/ Mayor")] == [EMP]
    assert [n for n, _, _ in campos._nifs_en(f"cif {EMP.lower()} y más")] == [EMP]
    assert [n for n, _, _ in campos._nifs_en(f"NIF {DNI} y más")] == [DNI]


# --- integration:INT-1 — NIF del cliente con control erróneo y un tercero con NIF válido ------------------------------
def _factura_nif_cliente_erroneo(extra) -> bytes:
    malo = CLI[:-1] + str((int(CLI[-1]) + 1) % 10)      # mismo NIF con el dígito de control mal
    ops = [(50, 50, "EMPRESA DEMO S.L.", 11, "Helvetica-Bold"), (50, 64, "C/ Mayor 12"), (50, 76, "28001 Madrid"),
           (50, 88, f"C.I.F.: {EMP}"), (540, 50, "FACTURA", 18, "Helvetica-Bold", "r"), (540, 70, "Nº: 166/26", 10, "Helvetica", "r"),
           (540, 84, "Fecha: 09/11/2026", 10, "Helvetica", "r"),
           (310, 140, "CLIENTE", 8, "Helvetica-Bold"), (310, 154, "PROMOCIONES CLIENTE S.A.", 9, "Helvetica-Bold"),
           (310, 168, "Avda. de los Datos Sintéticos, 7"), (310, 182, "41000 SEVILLA"), (310, 196, f"C.I.F.: {malo}")]
    ops += _cuerpo(280) + [(50, 420 + 14 * k, s, 8) for k, s in enumerate(extra)]
    return _pdf(ops), malo


def test_nif_ilegible_del_cliente_no_se_sustituye_por_un_tercero():
    datos, malo = _factura_nif_cliente_erroneo([f"Obra promovida por GESTORA TERCERA S.L., NIF {TER}, C/ Mayor 3, 28013 Madrid"])
    ext, d = _extraer(datos)
    assert [n["texto"] for n in ext["nifs_no_validos"]] == [malo]
    assert d["destinatario"]["nif"] is None                       # nunca el tercero
    assert d["destinatario"]["nombre"] == "PROMOCIONES CLIENTE S.A."
    assert "Datos Sintéticos" in d["destinatario"]["domicilio"]
    cod = {(i["nivel"], i["codigo"], i["campo"]) for i in _validar(ext, d)}
    assert ("bloqueo", "DEST_FALTA", "destinatario.nif") in cod
    assert ("confirmar", "NIF_ILEGIBLE", "destinatario.nif") in cod
    # Una vez escrito el NIF del destinatario, el NIF ilegible queda como aviso
    d["destinatario"]["nif"] = CLI
    cod = {(i["nivel"], i["codigo"]) for i in _validar(ext, d, cambiados={"destinatario.nif"})}
    assert ("aviso", "NIF_ILEGIBLE") in cod and ("bloqueo", "DEST_FALTA") not in cod


def test_nif_ilegible_parecido_a_uno_detectado_es_solo_aviso():
    """Un NIF mal leído que se parece al de la empresa (o a otro detectado) no pone en duda al destinatario."""
    datos, _ = _factura_nif_cliente_erroneo([])
    ext, d = _extraer(datos)
    ext["nifs_no_validos"] = [{"texto": EMP[:-1] + ("1" if EMP[-1] != "1" else "2"), "pagina": 1, "bbox": None, "linea": "x"}]
    inc = _validar(ext, dict(d, destinatario={"nombre": "PROMOCIONES CLIENTE S.A.", "nif": CLI, "domicilio": "C/ Sol 3"}))
    assert [(i["nivel"], i["campo"]) for i in inc if i["codigo"] == "NIF_ILEGIBLE"] == [("aviso", "documento")]


# --- integration:INT-6 — factura de proveedor con la empresa en un bloque sin rótulo ----------------------------------
def _recibida(rotulo=None) -> bytes:
    ops = [(50, 57, "SUMINISTROS FICTICIOS S.L.", 11, "Helvetica-Bold"), (50, 74, "Polígono Industrial Sur, nave 4"),
           (50, 88, "41010 Sevilla"), (50, 102, f"NIF: {PROV}"), (545, 57, "FACTURA", 18, "Helvetica-Bold", "r"),
           (545, 80, "Nº: A-2026-881", 10, "Helvetica", "r"), (545, 96, "Fecha: 09/11/2026", 10, "Helvetica", "r")]
    if rotulo:
        ops.append((312, 142, rotulo, 8, "Helvetica-Bold"))
    ops += [(312, 159, "EMPRESA DEMO S.L.", 9, "Helvetica-Bold"), (312, 173, "C/ Mayor 12"), (312, 187, "28001 Madrid"),
            (312, 201, f"C.I.F.: {EMP}")]
    return _pdf(ops + _cuerpo(283))


@pytest.mark.parametrize("rotulo", [None, "Datos fiscales:"])
def test_recibida_sin_rotulo_no_se_propone_como_propia(rotulo):
    ext, d = _extraer(_recibida(rotulo))
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] < borradores.UMBRAL_CONFIANZA
    sug = borradores.clasificar(ext, {"qr": [], "firmas": []}, {"nif": EMP})
    assert sug["sugerida"] not in ("pendiente_confirmar", "emitida_propia", "preparatorio")
    sug = borradores.clasificar(ext, {"qr": [], "firmas": [{"firmado": True}]}, {"nif": EMP})
    assert sug["sugerida"] != "emitida_propia"
    inc = _validar(ext, d)
    assert {i["campo"] for i in inc if i["codigo"] == "DUDOSO"} >= {"emisor.nif", "destinatario.nif"}


@pytest.mark.parametrize("rotulo", ["CLIENTE", "Para:", "Comprador:", "Adquirente:", "Datos del comprador", "Sold to:",
                                    "Invoice to:"])
def test_recibida_con_rotulo_cliente_sigue_siendo_recibida(rotulo):
    ext, _ = _extraer(_recibida(rotulo))
    assert ext["emisor"]["nif"]["valor"] == PROV and ext["destinatario"]["nif"]["valor"] == EMP
    assert borradores.clasificar(ext, {"qr": []}, {"nif": EMP})["sugerida"] == "recibida"


def test_emisor_a_la_derecha_en_lo_alto_no_es_dudoso():
    """Borrador propio con la cabecera del emisor arriba a la derecha y el cliente debajo, a la izquierda."""
    ops = [(545, 42, "EMPRESA DEMO S.L.", 14, "Helvetica-Bold", "r"), (545, 60, "C/ Mayor 12", 9, "Helvetica", "r"),
           (545, 72, "28001 Madrid", 9, "Helvetica", "r"), (545, 84, f"CIF: {EMP}", 9, "Helvetica", "r"),
           (62, 150, "DISTRIBUCIONES NORTE S.A."), (62, 164, f"C.I.F. {CLI}"), (62, 178, "Av. Libertad 5"),
           (62, 192, "46001 Valencia"), (50, 246, "FACTURA", 14, "Helvetica-Bold"), (50, 262, "Núm. - F-2026/045"),
           (210, 262, "Fecha. - 15/10/2026")]
    ext, d = _extraer(_pdf(ops + _cuerpo(300)))
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == CLI
    assert ext["emisor"]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert ext["destinatario"]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert borradores.clasificar(ext, {"qr": []}, {"nif": EMP})["sugerida"] == "pendiente_confirmar"


# --- reparación (partes): cabecera a dos columnas con el nombre del cliente a la altura del NIF del emisor ------------
def _dos_columnas(con_nombre_emisor=True):
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold")] if con_nombre_emisor else []
    ops += [(50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"), (50, 78, "Tel. 910 000 000"),
            (380, 20, "FACTURA", 18, "Helvetica-Bold"), (380, 30, "Nº factura: F-2026/045", 8), (480, 30, "Fecha: 15/10/2026", 8),
            (330, 54, "Cliente:", 9, "Helvetica-Bold"), (330, 66, "DISTRIBUCIONES NORTE S.A."),
            (330, 78, "Av. Libertad 5, 46001 Valencia"), (330, 90, f"CIF: {CLI}")]
    return _pdf(ops + _cuerpo(230))


@pytest.mark.parametrize("con_nombre_emisor", [True, False])
def test_nombre_del_cliente_a_la_altura_del_nif_del_emisor(con_nombre_emisor):
    """El bloque de la misma fila que el NIF del emisor encabeza la columna del cliente: es del cliente, no del emisor."""
    ext, d = _extraer(_dos_columnas(con_nombre_emisor))
    assert d["destinatario"]["nif"] == CLI and d["destinatario"]["nombre"] == "DISTRIBUCIONES NORTE S.A."
    assert d["destinatario"]["domicilio"] == "Av. Libertad 5, 46001 Valencia"
    assert d["emisor"]["nombre"] == ("EMPRESA DEMO S.L." if con_nombre_emisor else None)
    assert not [i for i in _validar(ext, d) if i["campo"].startswith("destinatario")]


def test_nif_del_emisor_en_la_columna_de_datos_de_la_factura():
    """Cabecera del emisor sin NIF a la izquierda, su CIF bajo el número y la fecha a la derecha, a la altura del nombre
    del cliente, que va debajo de la cabecera sin rótulo: cada nombre con su parte y sin dudas de papel."""
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, "Tel. 910 000 000"),
           (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"), (380, 74, "Fecha: 15/10/2026"),
           (380, 86, f"CIF: {EMP}"), (50, 90, "DISTRIBUCIONES NORTE S.A."), (50, 102, "Av. Libertad 5, 46001 Valencia"),
           (50, 114, f"CIF: {CLI}")]
    ext, d = _extraer(_pdf(ops + _cuerpo(230)))
    assert d["emisor"]["nombre"] == "EMPRESA DEMO S.L."
    assert d["destinatario"]["nombre"] == "DISTRIBUCIONES NORTE S.A."
    assert d["destinatario"]["domicilio"] == "Av. Libertad 5, 46001 Valencia"
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert not [i for i in _validar(ext, d) if i["campo"].startswith(("destinatario", "emisor.nombre", "emisor.nif"))]


# --- reparación (partes) F1: nombre del destinatario tomado por encima del hueco de su bloque --------------------------
_CABECERA_COMERCIAL = [(50, 40, "DEMO INSTALACIONES", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"),
                       (50, 66, "Tel. 910 000 000"), (380, 40, "FACTURA", 18, "Helvetica-Bold"),
                       (380, 62, "Nº factura: F-2026/045"), (380, 74, "Fecha: 15/10/2026"),
                       (50, 800, f"EMPRESA DEMO S.L. · CIF {EMP} · Inscrita en el Registro Mercantil de Madrid, Tomo 1, Folio 2", 7)]


@pytest.mark.parametrize("primera", ["MERCADONIA", "TELEFONÍA Y REDES DEL NORTE", "TALLERES 2000", None])
def test_destinatario_sin_nombre_reconocible_no_toma_la_cabecera(primera):
    """Cliente sin rótulo bajo la cabecera comercial del emisor (NIF del emisor solo en el pie), con una razón social que no
    se reconoce como tal: el nombre queda vacío (se pide escribirlo); sin ninguna, el de la cabecera se marca dudoso. El
    domicilio es el del bloque del cliente."""
    lineas = ([primera] if primera else []) + ["C/ Sol 3, 28002 Madrid", f"CIF: {CLI}"]
    ext, d = _extraer(_pdf(_CABECERA_COMERCIAL + [(50, 96 + 12 * k, s) for k, s in enumerate(lineas)] + _cuerpo(230)))
    assert d["destinatario"]["nif"] == CLI
    assert d["destinatario"]["domicilio"] == "C/ Sol 3, 28002 Madrid"
    inc = _validar(ext, d)
    if primera:
        assert d["destinatario"]["nombre"] is None
        assert ("bloqueo", "DEST_NOMBRE") in {(i["nivel"], i["codigo"]) for i in inc}
    else:
        assert ext["destinatario"]["nombre"]["confianza"] < borradores.UMBRAL_CONFIANZA
        assert any(i["codigo"] == "DUDOSO" and i["campo"] == "destinatario.nombre" for i in inc)


@pytest.mark.parametrize("lineas", [
    [(60, "DISTRIBUCIONES NORTE S.A."), (72, "Av. Libertad 5, 46001 Valencia"), (96, "Persona de contacto: Juan Pérez"), (108, f"CIF: {CLI}")],
    [(60, "DISTRIBUCIONES NORTE S.A."), (84, "Edificio Norte, planta 2"), (96, "Av. Libertad 5, 46001 Valencia"), (108, f"CIF: {CLI}")],
    [(60, "DISTRIBUCIONES NORTE S.A."), (72, "Av. Libertad 5"), (84, "46001 Valencia"), (106, "Tel. 600 000 000"), (118, f"CIF: {CLI}")],
])
def test_bloque_del_cliente_con_renglones_en_blanco(lineas):
    """Bloque del cliente sin rótulo con un renglón en blanco entre su nombre, su dirección o su NIF: es un único bloque."""
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
           (380, 160, "FACTURA", 16, "Helvetica-Bold"), (380, 176, "Nº factura: F-2026/045"), (380, 188, "Fecha: 15/10/2026")]
    ext, d = _extraer(_pdf(ops + [(330, y, t) for y, t in lineas] + _cuerpo(230)))
    assert d["destinatario"]["nombre"] == "DISTRIBUCIONES NORTE S.A." and "Libertad 5" in d["destinatario"]["domicilio"]
    assert not [i for i in _validar(ext, d) if i["campo"].startswith("destinatario")]


def test_destinatario_prefiere_el_nombre_de_su_bloque_al_de_la_cabecera():
    """Una razón social con forma jurídica en la cabecera no gana al nombre contiguo al NIF del cliente."""
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, "Tel. 910 000 000"),
           (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"), (380, 74, "Fecha: 15/10/2026"),
           (50, 100, "TALLERES HERMANOS RUIZ"), (50, 112, "C/ Sol 3, 28002 Madrid"), (50, 124, f"CIF: {completar_cif('E', '3332221')}"),
           (50, 800, f"CIF {EMP} · Inscrita en el Registro Mercantil de Madrid, Tomo 1, Folio 2", 7)]
    ext, d = _extraer(_pdf(ops + _cuerpo(230)))
    assert d["destinatario"]["nombre"] == "TALLERES HERMANOS RUIZ"
    assert d["destinatario"]["domicilio"] == "C/ Sol 3, 28002 Madrid"


def test_nif_del_cliente_tras_un_renglon_en_blanco_no_es_dudoso():
    """Nombre y dirección del cliente y, tras un renglón en blanco, su CIF: es un único bloque, sin dudas."""
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
           (330, 40, "DISTRIBUCIONES NORTE S.A."), (330, 52, "Av. Libertad 5,"), (330, 64, "46001 Valencia"), (330, 88, f"CIF: {CLI}"),
           (380, 140, "FACTURA", 16, "Helvetica-Bold"), (380, 156, "Nº factura: F-2026/045"), (380, 168, "Fecha: 15/10/2026")]
    ext, d = _extraer(_pdf(ops + _cuerpo(230)))
    assert d["destinatario"]["nombre"] == "DISTRIBUCIONES NORTE S.A." and "46001" in d["destinatario"]["domicilio"]
    assert not [i for i in _validar(ext, d) if i["campo"].startswith("destinatario")]


# --- reparación (partes) F3 / INT-1: tercero con NIF en la cabecera, encima de la tabla -------------------------------
def _cabecera_tercero(extra, rotulo=True, nif_cliente=None):
    ops = [(50, 50, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 64, "C/ Mayor 12, 28001 Madrid"), (50, 76, f"CIF: {EMP}"),
           (380, 50, "FACTURA", 18, "Helvetica-Bold"), (380, 72, "Nº factura: F-2026/045"), (380, 84, "Fecha: 15/10/2026")]
    cliente = (["Cliente:"] if rotulo else []) + ["DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5", "46001 Valencia"]
    cliente += [nif_cliente] if nif_cliente else []
    ops += [(330, 120 + 12 * k, s) for k, s in enumerate(cliente)]
    return _pdf(ops + extra + _cuerpo(250))


_CESION = [(50, 190, "Crédito cedido a BANCO FACTORING EJEMPLO S.A."), (50, 202, f"CIF: {TER}"),
           (50, 214, "Paseo de la Castellana 100, 28046 Madrid")]
_CESION_LINEA = [(50, 200, f"Pago al cesionario BANCO FACTORING EJEMPLO S.A., CIF {TER}, Paseo de la Castellana 100, 28046 Madrid", 8)]


@pytest.mark.parametrize("extra, rotulo", [(_CESION, True), (_CESION_LINEA, True), (_CESION, False), (_CESION_LINEA, False),
                                           ([(330, 180, "Obra: Reforma de nave industrial"), (330, 192, "Promotor: GESTORA TERCERA S.L."),
                                             (330, 204, f"CIF: {TER}"), (330, 216, "C/ Mayor 3, 28013 Madrid")], True)])
def test_tercero_en_la_cabecera_no_es_destinatario_seguro(extra, rotulo):
    ext, d = _extraer(_cabecera_tercero(extra, rotulo))
    cn = ext["destinatario"]["nif"]
    assert cn["valor"] == TER and cn["confianza"] < borradores.UMBRAL_CONFIANZA and cn.get("papel_dudoso")
    campos_dudosos = {i["campo"] for i in _validar(ext, d) if i["codigo"] == "DUDOSO"}
    assert {"destinatario.nif", "destinatario.nombre"} <= campos_dudosos


def test_cliente_rotulado_con_su_nif_no_se_pone_en_duda():
    ext, d = _extraer(_cabecera_tercero([], True, f"CIF: {CLI}"))
    assert d["destinatario"]["nif"] == CLI and ext["destinatario"]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert not [i for i in _validar(ext, d) if i["campo"].startswith("destinatario")]


@pytest.mark.parametrize("linea_nif", ["C.I.F.: {malo} C/ Sol 3, 41000 Sevilla", "C.I.F.: {malo_s}", "{malo}"])
def test_nif_erroneo_del_cliente_y_tercero_en_la_cabecera(linea_nif):
    """NIF del cliente con control erróneo seguido de la calle, ilegible (control fuera de rango) o sin rótulo, y un tercero
    con NIF válido en la cabecera: nunca se toma el tercero como destinatario sin pedir confirmación."""
    malo = CLI[:-1] + str((int(CLI[-1]) + 1) % 10)
    ops = [(50, 50, "EMPRESA DEMO S.L.", 11, "Helvetica-Bold"), (50, 64, "C/ Mayor 12"), (50, 76, "28001 Madrid"),
           (50, 88, f"C.I.F.: {EMP}"), (540, 50, "FACTURA", 18, "Helvetica-Bold", "r"), (540, 70, "Nº: 166/26", 10, "Helvetica", "r"),
           (540, 84, "Fecha: 09/11/2026", 10, "Helvetica", "r"),
           (310, 140, "CLIENTE", 8, "Helvetica-Bold"), (310, 154, "PROMOCIONES CLIENTE S.A.", 9, "Helvetica-Bold"),
           (310, 168, "Avda. de los Datos Sintéticos, 7"), (310, 182, "41000 SEVILLA"),
           (310, 196, linea_nif.format(malo=malo, malo_s=malo[:-1] + "S")),
           (50, 232, f"Obra promovida por GESTORA TERCERA S.L., NIF {TER}, C/ Mayor 3, 28013 Madrid", 8.5)]
    ext, d = _extraer(_pdf(ops + _cuerpo(280)))
    inc = _validar(ext, d)
    if d["destinatario"]["nif"] is None:   # el NIF erróneo se reconoce: bloque del cliente con el NIF por escribir
        assert [n["texto"] for n in ext["nifs_no_validos"]] == [malo[:-1] + "S" if "{malo_s}" in linea_nif else malo]
        assert ("bloqueo", "DEST_FALTA") in {(i["nivel"], i["codigo"]) for i in inc}
    else:
        assert ext["destinatario"]["nif"]["confianza"] < borradores.UMBRAL_CONFIANZA
        assert any(i["codigo"] == "DUDOSO" and i["campo"] == "destinatario.nif" for i in inc)
    if linea_nif.startswith("C.I.F.: {malo} "):
        assert d["destinatario"]["nif"] is None


def test_nifs_en_forma_corta_solo_tras_un_espacio():
    malo = CLI[:-1] + str((int(CLI[-1]) + 1) % 10)
    assert [n for n, _, _ in campos._nifs_en(f"C.I.F.: {malo} C/ Sol 3")] == [malo]
    # «N.I.F.12345678-Z» sin espacios: la «F» del rótulo no forma otro NIF con las cifras del DNI
    assert "F" + DNI[:8] not in [n for n, _, _ in campos._nifs_en(f"N.I.F.{DNI[:8]}-{DNI[8]}")]


# --- reparación (partes) INT-6: el nombre destacado decide entre maquetaciones simétricas sin rótulos -----------------
_TABLA_R = _cuerpo(300)


def _bloque(x, y, lineas, tam, al="l"):
    return [(x, y + (14 if k else 0) + 12 * max(0, k - 1), s, tam if k == 0 else 9, "Helvetica-Bold" if k == 0 else "Helvetica", al)
            for k, s in enumerate(lineas)]


_PROV = ["SUMINISTROS FICTICIOS S.L.", "Polígono Industrial Sur, nave 4", "41010 Sevilla", f"NIF: {PROV}"]
_EMPRESA = ["EMPRESA DEMO S.L.", "C/ Mayor 12", "28001 Madrid", f"C.I.F.: {EMP}"]
_TIT_DER = [(545, 180, "FACTURA", 18, "Helvetica-Bold", "r"), (545, 200, "Nº: A-2026-881", 10, "Helvetica", "r"),
            (545, 214, "Fecha: 09/11/2026", 10, "Helvetica", "r")]


@pytest.mark.parametrize("ops", [
    _bloque(50, 96, _PROV, 11) + _bloque(312, 62, _EMPRESA, 9) + _TIT_DER,            # empresa a la derecha, más arriba
    _bloque(545, 50, _PROV, 11, "r") + _bloque(50, 128, _EMPRESA, 9) + _TIT_DER,       # proveedor arriba a la derecha
])
def test_recibida_con_el_nombre_del_proveedor_destacado(ops):
    ext, _ = _extraer(_pdf(ops + _TABLA_R))
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert borradores.clasificar(ext, {"qr": [], "firmas": []}, {"nif": EMP})["sugerida"] not in (
        "pendiente_confirmar", "emitida_propia", "preparatorio")


def test_propia_con_el_emisor_destacado_a_la_derecha_no_es_dudosa():
    """Espejo de la anterior: borrador propio con la cabecera del emisor (nombre mayor) arriba a la derecha y el cliente
    sin rótulo arriba a la izquierda, a la misma altura."""
    ops = _bloque(545, 50, _EMPRESA, 11, "r") + _bloque(50, 50, ["PROMOCIONES CLIENTE S.A.", "Avda. de los Datos Sintéticos, 7",
                                                                 "41000 SEVILLA", f"C.I.F.: {CLI}"], 9) + _TIT_DER
    ext, d = _extraer(_pdf(ops + _TABLA_R))
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == CLI
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert borradores.clasificar(ext, {"qr": []}, {"nif": EMP})["sugerida"] == "pendiente_confirmar"


# --- reparación (partes) ronda 2 -----------------------------------------------------------------------------------
_CAB_EMISOR = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
               (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"), (380, 74, "Fecha: 15/10/2026")]


def _columna(lineas, x=330, y0=100, dy=12):
    return [(x, y0 + dy * k, s) for k, s in enumerate(lineas) if s is not None]


@pytest.mark.parametrize("lineas, x, y0", [
    (["DISTRIBUCIONES NORTE S.A.", None, None, "Juan Pérez García", "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"], 330, 100),
    (["DISTRIBUCIONES NORTE S.A.", None, None, "Delegación Levante", "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"], 330, 100),
    (["DISTRIBUCIONES NORTE S.A.", None, None, "Juan Pérez García", "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"], 50, 100),
    (["DISTRIBUCIONES NORTE S.A.", None, None, "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"], 330, 100),
])
def test_razon_social_separada_de_su_bloque_por_dos_renglones(lineas, x, y0):
    """La razón social del cliente, en el cuerpo de su bloque y separada de él por dos renglones en blanco, es el título del
    bloque: no la sustituye la persona de contacto o la delegación, ni se pide confirmarla."""
    ext, d = _extraer(_pdf(_CAB_EMISOR + _columna(lineas, x, y0) + _cuerpo(230)))
    assert d["destinatario"]["nombre"] == "DISTRIBUCIONES NORTE S.A."
    assert ext["destinatario"]["nombre"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert not [i for i in _validar(ext, d) if i["campo"].startswith("destinatario")]


@pytest.mark.parametrize("membrete, cliente, esperado", [
    ((50, 40, "DEMO INSTALACIONES S.L.", 12, "Helvetica-Bold"), "TALLERES HERMANOS RUIZ", "TALLERES HERMANOS RUIZ"),
    ((50, 40, "DEMO INSTALACIONES S.L."), "TALLERES HERMANOS RUIZ", "TALLERES HERMANOS RUIZ"),
    ((50, 40, "DEMO INSTALACIONES S.L."), "MERCADONIA", None),
])
def test_membrete_suelto_encima_del_cliente_se_pide_confirmar(membrete, cliente, esperado):
    """Membrete del emisor sin dirección (NIF del emisor solo en el pie) encima del bloque del cliente: el nombre del
    destinatario nunca queda sin confirmar."""
    e = completar_cif("E", "3332221")
    ops = [membrete, (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"),
           (380, 74, "Fecha: 15/10/2026"), (50, 800, f"EMPRESA DEMO S.L. · CIF {EMP} · Inscrita en el Registro Mercantil", 7)]
    ext, d = _extraer(_pdf(ops + _columna([cliente, "C/ Sol 3, 28002 Madrid", f"CIF: {e}"], 50, 100) + _cuerpo(230)))
    assert d["destinatario"]["nif"] == e
    if esperado:
        assert d["destinatario"]["nombre"] == esperado
    assert any(i["codigo"] == "DUDOSO" and i["campo"] == "destinatario.nombre" for i in _validar(ext, d))


_CAB_COMERCIAL = [(50, 40, "DEMO INSTALACIONES", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"),
                  (50, 66, "Tel. 910 000 000"), (380, 40, "FACTURA", 18, "Helvetica-Bold"), (380, 62, "Nº factura: F-2026/045"),
                  (380, 74, "Fecha: 15/10/2026"),
                  (50, 800, f"EMPRESA DEMO S.L. · CIF {EMP} · Inscrita en el Registro Mercantil de Madrid, Tomo 1, Folio 2", 7)]


@pytest.mark.parametrize("bloque", [
    [(50, 90, "MERCADONIA"), (50, 102, f"CIF: {CLI}"), (50, 114, "C/ Valencia 5, 46001 Valencia")],
    [(50, 102, "TALLERES 2000"), (50, 114, f"CIF: {CLI}"), (50, 126, "C/ Valencia 5, 46001 Valencia")],
    [(50, 90, f"MERCADONIA  CIF: {CLI}"), (50, 102, "C/ Valencia 5, 46001 Valencia")],
])
def test_cliente_con_el_nif_antes_de_la_direccion_no_toma_la_cabecera(bloque):
    """Bloque del cliente sin rótulo, con un nombre que no se reconoce y el NIF antes de la dirección, bajo la cabecera
    comercial del emisor: el nombre queda por escribir y el domicilio es el del cliente."""
    ext, d = _extraer(_pdf(_CAB_COMERCIAL + bloque + _cuerpo(230)))
    assert d["destinatario"]["nif"] == CLI
    assert d["destinatario"]["nombre"] is None
    assert d["destinatario"]["domicilio"] == "C/ Valencia 5, 46001 Valencia"
    assert ("bloqueo", "DEST_NOMBRE") in {(i["nivel"], i["codigo"]) for i in _validar(ext, d)}


@pytest.mark.parametrize("bloque", [
    [(50, 78, "MERCADONIA"), (50, 90, "C/ Valencia 5, 46001 Valencia"), (50, 102, f"CIF: {CLI}")],
    [(50, 78, "MERCADONIA"), (50, 90, f"CIF: {CLI}"), (50, 102, "C/ Valencia 5, 46001 Valencia")],
    [(50, 90, "MERCADONIA", 10, "Helvetica-Bold"), (50, 102, f"CIF: {CLI}"), (50, 114, "C/ Valencia 5, 46001 Valencia")],
])
def test_bloque_del_cliente_pegado_a_la_cabecera_se_pide_confirmar(bloque):
    """Cliente pegado a la cabecera del emisor, sin renglón en blanco: el bloque reúne dos direcciones y la razón social y el
    domicilio del destinatario se piden confirmar."""
    ext, d = _extraer(_pdf(_CAB_COMERCIAL + bloque + _cuerpo(230)))
    dudosos = {i["campo"] for i in _validar(ext, d) if i["codigo"] == "DUDOSO" or i["nivel"] == "bloqueo"}
    assert {"destinatario.nombre", "destinatario.domicilio"} <= dudosos


def _sin_rotulo_cliente(extra):
    ops = [(50, 50, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 64, "C/ Mayor 12, 28001 Madrid"), (50, 76, f"CIF: {EMP}"),
           (380, 50, "FACTURA", 18, "Helvetica-Bold"), (380, 72, "Nº factura: F-2026/045"), (380, 84, "Fecha: 15/10/2026"),
           (330, 120, "DISTRIBUCIONES NORTE S.A."), (330, 132, "Av. Libertad 5"), (330, 144, "46001 Valencia")]
    return _pdf(ops + extra + _cuerpo(250))


@pytest.mark.parametrize("datos", [
    _sin_rotulo_cliente([(50, 180, "Cesionario:"), (50, 192, "BANCO FACTORING EJEMPLO S.A."), (50, 204, f"CIF: {TER}"),
                         (50, 216, "Paseo de la Castellana 100, 28046 Madrid")]),
    _sin_rotulo_cliente([(50, 180, "Crédito cedido a:"), (50, 192, "BANCO FACTORING EJEMPLO S.A."), (50, 204, f"CIF: {TER}"),
                         (50, 216, "Paseo de la Castellana 100, 28046 Madrid")]),
    _sin_rotulo_cliente([(50, 192, f"Entidad cesionaria BANCO FACTORING EJEMPLO S.A. CIF {TER}"),
                         (50, 204, "Paseo de la Castellana 100, 28046 Madrid")]),
    _sin_rotulo_cliente([(50, 192, "BANCO FACTORING EJEMPLO S.A."), (50, 204, f"CIF: {TER}"),
                         (50, 216, "Paseo de la Castellana 100, 28046 Madrid")]),
    _cabecera_tercero([(330, 168, "Promotor: GESTORA TERCERA S.L."), (330, 180, f"CIF: {TER}")]),
    _cabecera_tercero([(330, 168, f"CIF cesionario: {TER}")]),
    _cabecera_tercero([(330, 168, f"Pagadero a BANCO FACTORING EJEMPLO S.A. (CIF {TER})", 8)]),
])
def test_tercero_con_rotulo_de_papel_o_cliente_sin_nif(datos):
    """Tercero con su NIF rotulado por su papel («Cesionario:», «CIF cesionario:», «Promotor:»), en una frase, o sin
    rótulo mientras el bloque del cliente no tiene NIF: el destinatario siempre se pide confirmar."""
    ext, d = _extraer(datos)
    cn = ext["destinatario"]["nif"]
    assert cn["valor"] == TER and cn["confianza"] < borradores.UMBRAL_CONFIANZA
    assert "destinatario.nif" in {i["campo"] for i in _validar(ext, d) if i["codigo"] == "DUDOSO"}


@pytest.mark.parametrize("lineas", [
    ["Cliente:", "Materiales para Construcción ALBA S.L.", "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"],
    ["Servicios Integrales a Domicilio SAD S.L.", "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"],
    ["Datos fiscales:", "DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5", "46001 Valencia", f"NIF del cliente: {CLI}"],
    ["DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5, 46001 Valencia", f"CIF: {CLI}", "Envío: C/ Puerto 3, 46002 Valencia"],
])
def test_cliente_ordinario_sin_dudas_de_tercero(lineas):
    """Razones sociales con mayúsculas iniciales y preposición, rótulos neutros y una dirección de envío rotulada: el cliente
    no se pone en duda."""
    ext, d = _extraer(_pdf(_CAB_EMISOR + _columna(lineas) + _cuerpo(230)))
    assert d["destinatario"]["nif"] == CLI
    assert not [i for i in _validar(ext, d) if i["campo"].startswith("destinatario")]


@pytest.mark.parametrize("linea_nif, texto", [("C.I.F.: {s}", "{s}"), ("C.I.F.: {corto}", "{corto}"), ("{malo}", "{malo}")])
def test_nif_ilegible_del_cliente_sin_rotulo_y_tercero_rotulado(linea_nif, texto):
    """Bloque del cliente sin rótulo con su NIF ilegible (control leído como letra, una cifra perdida o sin rótulo) y un
    tercero rotulado por su papel en la cabecera: el NIF ilegible se señala y el tercero se pide confirmar."""
    malo = CLI[:-1] + str((int(CLI[-1]) + 1) % 10)
    v = {"s": CLI[:-1] + "S", "corto": CLI[:-1], "malo": malo}
    ops = [(50, 50, "EMPRESA DEMO S.L.", 11, "Helvetica-Bold"), (50, 64, "C/ Mayor 12"), (50, 76, "28001 Madrid"),
           (50, 88, f"C.I.F.: {EMP}"), (540, 50, "FACTURA", 18, "Helvetica-Bold", "r"), (540, 70, "Nº: 166/26", 10, "Helvetica", "r"),
           (540, 84, "Fecha: 09/11/2026", 10, "Helvetica", "r"),
           (310, 154, "PROMOCIONES CLIENTE S.A.", 9, "Helvetica-Bold"), (310, 168, "Avda. de los Datos Sintéticos, 7"),
           (310, 182, "41000 SEVILLA"), (310, 196, linea_nif.format(**v)),
           (50, 220, "Promotor: GESTORA TERCERA S.L.", 8.5), (50, 234, f"NIF: {TER}", 8.5), (50, 248, "C/ Mayor 3, 28013 Madrid", 8.5)]
    ext, d = _extraer(_pdf(ops + _cuerpo(290)))
    assert texto.format(**v) in [n["texto"] for n in ext["nifs_no_validos"]]
    assert ext["destinatario"]["nif"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert "destinatario.nif" in {i["campo"] for i in _validar(ext, d) if i["codigo"] == "DUDOSO"}


@pytest.mark.parametrize("ops", [
    _bloque(50, 57, _PROV, 8) + _bloque(312, 142, _EMPRESA, 10) + _TIT_DER,       # proveedor menor arriba, empresa debajo
    _bloque(50, 96, _PROV, 9) + _bloque(312, 62, _EMPRESA, 9) + _TIT_DER,         # empresa a la derecha, algo más arriba
])
def test_recibida_sin_rotulo_con_la_empresa_destacada_o_mas_arriba(ops):
    ext, _ = _extraer(_pdf(ops + _TABLA_R))
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert borradores.clasificar(ext, {"qr": [], "firmas": []}, {"nif": EMP})["sugerida"] not in (
        "pendiente_confirmar", "emitida_propia", "preparatorio")


_CLIENTE_P = ["PROMOCIONES CLIENTE S.A.", "Avda. de los Datos Sintéticos, 7", "41000 SEVILLA", f"C.I.F.: {CLI}"]


@pytest.mark.parametrize("ops", [
    _bloque(50, 57, _EMPRESA, 8) + _bloque(312, 57, _CLIENTE_P, 10) + _TIT_DER,           # empresa a la izquierda, menor
    _bloque(545, 50, _EMPRESA, 9, "r") + _bloque(50, 128, _CLIENTE_P, 10) + _TIT_DER,      # empresa arriba a la derecha
])
def test_propia_con_el_cliente_destacado_no_es_dudosa(ops):
    ext, d = _extraer(_pdf(ops + _TABLA_R))
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == CLI
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert borradores.clasificar(ext, {"qr": []}, {"nif": EMP})["sugerida"] == "pendiente_confirmar"


def test_muchos_rotulos_cliente_no_cuestan_un_barrido_por_rotulo(monkeypatch):
    """Los renglones de debajo de cada rótulo «Cliente» se buscan en el índice por altura, no en todo el documento."""
    paginas = [_CAB_EMISOR + _columna(["DISTRIBUCIONES NORTE S.A.", "Av. Libertad 5", "46001 Valencia", f"CIF: {CLI}"])
               + _cuerpo(230)] + [[(30 + 140 * (j % 4), 20 + 4 * (j // 4), "Cliente:", 3) for j in range(400)] for _ in range(3)]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for pg in paginas:
        for op in pg:
            c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 9)
            (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(op[0], A4[1] - op[1], op[2])
        c.showPage()
    c.save()
    ex = campos.Extractor(texto.leer(buf.getvalue()), "f.pdf", EMP)
    vistos = []
    original = ex._entre_tops
    monkeypatch.setattr(ex, "_entre_tops", lambda *a: vistos.append(1) or original(*a))
    assert ex._clientes_rotulados() == []
    assert len(vistos) >= 1200 and len(ex.lineas) > 1200
