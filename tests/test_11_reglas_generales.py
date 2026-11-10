"""Reglas genéricas añadidas tras estudiar el tercer corpus reservado (que por eso ya no es ciego: ver
test_09_extraccion_corpus): facturas sin tabla (texto corrido con un único importe, conceptos en tarjetas con fórmula),
resúmenes de IVA traspuestos o junto a totales apilados, rótulos bilingües, rejillas de cabecera, cantidades negativas,
«abono» como producto y lecturas OCR (O por 0 en el NIF, rótulo de precio ilegible, coma decimal perdida). Datos
ficticios, distintos de los de ese corpus: nombres, direcciones y NIF inventados."""
import io
import json
import re
from pathlib import Path

import pdfplumber
import pytest
from reportlab.lib.pagesizes import A4, A5
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, texto
from app.extraccion.texto import Pagina, Palabra
from app.nif import buscar_nifs, completar_cif, es_valido, tipo

EMP = completar_cif("B", "1234567")
CLI = completar_cif("A", "8765432")
FUND = completar_cif("G", "3051904")


def _pdf(ops, tam=A4) -> bytes:
    """ops: (x, y_desde_arriba, texto, [tamaño], [fuente], [alineación l|r|c])."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=tam, invariant=1)
    alto = tam[1]
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 9)
        al = op[5] if len(op) > 5 else "l"
        {"r": c.drawRightString, "c": c.drawCentredString}.get(al, c.drawString)(x, alto - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(ops, nif=EMP, tam=A4):
    return campos.Extractor(texto.leer(_pdf(ops, tam)), "f.pdf", nif).extraer()


def _v(c):
    return c.get("valor") if isinstance(c, dict) else None


def _lineas(ext):
    return [(_v(l["concepto"]), _v(l["cantidad"]), _v(l["precio"]), _v(l["importe"])) for l in ext["lineas"]]


def _desglose(ext):
    return sorted((_v(d["tipo_iva"]), _v(d["base"]), _v(d["cuota"])) for d in ext["desglose"])


def _cabecera(emisor="EMPRESA DEMO S.L.", nif_emisor=EMP, cliente="DISTRIBUCIONES NORTE S.A.", nif_cliente=CLI):
    return [(50, 50, emisor, 12, "Helvetica-Bold"), (50, 64, "C/ Mayor 12, 28001 Madrid"), (50, 76, f"NIF: {nif_emisor}"),
            (380, 50, "FACTURA", 18, "Helvetica-Bold"), (380, 72, "Nº factura: F-2026/045"), (380, 84, "Fecha: 15/10/2026"),
            (330, 120, "Cliente:", 9, "Helvetica-Bold"), (330, 132, cliente), (330, 144, "Av. Libertad 5"),
            (330, 156, "46001 Valencia"), (330, 168, f"CIF: {nif_cliente}")]


# --- Factura sin tabla: texto corrido bajo «Concepto» y un único importe ------------------------------------------------
def _prosa(importes_extra=()):
    ops = [(210, 40, "Fundación Demo Ribera Ficticia", 14, "Helvetica-Bold", "c"),
           (210, 56, f"CIF {FUND} · Calle Ficticia del Olmo 3 · 30001 Villademo", 7, "Helvetica", "c"),
           (30, 90, "F A C T U R A", 12, "Helvetica-Bold"), (240, 90, "Facturado a:", 8, "Helvetica-Bold"),
           (240, 102, "EMPRESA DEMO S.L.", 8), (30, 108, "Nº de factura: 2026/031", 8), (240, 112, f"CIF {EMP}", 8),
           (240, 122, "C/ Mayor 12", 8), (240, 132, "28001 Madrid", 8), (30, 120, "Fecha: 04/11/2026", 8),
           (30, 160, "Concepto", 10, "Helvetica-Bold"),
           (30, 178, "Organización de dos jornadas de formación sobre seguridad en el trabajo para la plantilla", 8),
           (30, 188, "del centro de Madrid, celebradas los días 6 y 13 de octubre de 2026.", 8),
           (30, 204, "Incluye materiales y certificado de asistencia para 25 participantes.", 8)]
    y = 230
    for et, v in list(importes_extra) + [("Importe de los servicios . . . . . . . .", "1.200,00 €"),
                                         ("IVA 21 % . . . . . . . . . . . . . . . . .", "252,00 €"),
                                         ("Total factura . . . . . . . . . . . . . .", "1.452,00 €")]:
        ops += [(100, y, et, 8), (380, y, v, 8, "Helvetica", "r")]
        y += 14
    return ops


def test_factura_sin_tabla_linea_unica_con_la_base():
    ext = _extraer(_prosa(), tam=A5)
    assert _lineas(ext) == [("Organización de dos jornadas de formación sobre seguridad en el trabajo para la plantilla "
                             "del centro de Madrid, celebradas los días 6 y 13 de octubre de 2026.", None, None, "1200.00")]
    assert ext["lineas"][0]["importe"]["confianza"] <= 0.7 and ext["aviso_lineas"]
    # «Facturado a:» es el rótulo del cliente y el membrete centrado, el emisor
    assert _v(ext["emisor"]["nif"]) == FUND and _v(ext["destinatario"]["nif"]) == EMP
    assert "membrete centrado" in ext["emisor"]["nif"]["avisos"][0]
    d = borradores.datos_desde_extraccion(ext, "f.pdf")
    assert borradores.clasificar(ext, {"qr": []}, {"nif": EMP})["sugerida"] == "recibida"
    assert d["lineas"][0]["importe"] == "1200.00"


def test_sin_tabla_y_otra_cifra_no_se_deduce_linea():
    # con otra cifra impresa (dos servicios), la línea única no es segura: no se deduce ninguna
    ext = _extraer(_prosa([("Desplazamiento . . . . . . . . . . . . . .", "150,00 €")]), tam=A5)
    assert ext["lineas"] == []


# --- Conceptos en tarjetas con la fórmula «cantidad x precio = importe» ------------------------------------------------
def test_tarjetas_con_formula():
    ops = _cabecera() + [(50, 230, "Conceptos", 12, "Helvetica-Bold")]
    y = 260
    for desc, f in (("Ilustración de cubierta para el libro", "1 x 680,00 € = 680,00 €"),
                    ("Ilustraciones interiores a línea", "12 x 45,00 € = 540,00 €"),
                    ("Corrección de pruebas (no cuadra)", "2 x 35,00 € = 75,00 €")):
        ops += [(60, y, desc, 10, "Helvetica-Bold"), (60, y + 14, f, 9)]
        y += 46
    ops += [(380, 420, "Base imponible"), (540, 420, "1.220,00 €", 9, "Helvetica", "r"),
            (380, 434, "IVA 21 %"), (540, 434, "256,20 €", 9, "Helvetica", "r"),
            (380, 448, "Total factura"), (540, 448, "1.476,20 €", 9, "Helvetica", "r")]
    ext = _extraer(ops)
    # la fórmula que no cuadra no se toma como línea
    assert _lineas(ext) == [("Ilustración de cubierta para el libro", "1", "680.00", "680.00"),
                            ("Ilustraciones interiores a línea", "12", "45.00", "540.00")]
    assert "fórmulas" in ext["aviso_lineas"]


def test_tarjetas_formula_en_el_mismo_renglon_y_apartado_pegado():
    ops = _cabecera() + [(60, 230, "Conceptos", 10, "Helvetica-Bold"),
                         (60, 242, "Proyecto de maquetación del catálogo", 9, "Helvetica-Bold"), (60, 254, "3 x 120,00 € = 360,00 €"),
                         (60, 280, "Revisión de textos 2 x 35,00 € = 70,00 €")]
    ops += [(380, 320, "Base imponible"), (540, 320, "430,00 €", 9, "Helvetica", "r"),
            (380, 334, "IVA 21 %"), (540, 334, "90,30 €", 9, "Helvetica", "r"),
            (380, 348, "Total factura"), (540, 348, "520,30 €", 9, "Helvetica", "r")]
    assert _lineas(_extraer(ops)) == [("Proyecto de maquetación del catálogo", "3", "120.00", "360.00"),
                                      ("Revisión de textos", "2", "35.00", "70.00")]


# --- Resumen de IVA traspuesto y desglose junto a totales apilados -----------------------------------------------------
def test_resumen_iva_traspuesto():
    ops = _cabecera() + [(50, 230, "Concepto", 9, "Helvetica-Bold"), (420, 230, "Importe", 9, "Helvetica-Bold")]
    ops += [(50, 248, "Leche entera, caja"), (470, 248, "100,00", 9, "Helvetica", "r"),
            (50, 262, "Café en grano"), (470, 262, "200,00", 9, "Helvetica", "r"),
            (50, 276, "Cerveza barril"), (470, 276, "300,00", 9, "Helvetica", "r")]
    ops += [(50, 320, "Resumen de IVA", 10, "Helvetica-Bold"),
            (180, 340, "IVA 4 %", 9, "Helvetica", "r"), (250, 340, "IVA 10 %", 9, "Helvetica", "r"),
            (320, 340, "IVA 21 %", 9, "Helvetica", "r"), (390, 340, "Totales", 9, "Helvetica", "r"),
            (440, 336, "TOTAL FACTURA", 9, "Helvetica-Bold"),
            (50, 356, "Base imponible"), (180, 356, "100,00", 9, "Helvetica", "r"), (250, 356, "200,00", 9, "Helvetica", "r"),
            (320, 356, "300,00", 9, "Helvetica", "r"), (390, 356, "600,00", 9, "Helvetica", "r"),
            (520, 354, "687,00 €", 11, "Helvetica-Bold", "r"),
            (50, 372, "Cuota IVA"), (180, 372, "4,00", 9, "Helvetica", "r"), (250, 372, "20,00", 9, "Helvetica", "r"),
            (320, 372, "63,00", 9, "Helvetica", "r"), (390, 372, "87,00", 9, "Helvetica", "r"),
            (50, 400, "Forma de pago: recibo domiciliado a 30 días")]
    ext = _extraer(ops)
    assert _desglose(ext) == [("10.00", "200.00", "20.00"), ("21.00", "300.00", "63.00"), ("4.00", "100.00", "4.00")]
    assert (_v(ext["base_imponible"]), _v(ext["cuota_iva"]), _v(ext["total_factura"])) == ("600.00", "87.00", "687.00")


def _desglose_apilado():
    ops = _cabecera() + [(50, 230, "Concepto", 9, "Helvetica-Bold"), (420, 230, "Importe", 9, "Helvetica-Bold"),
                         (50, 248, "Aceite de oliva, garrafa"), (470, 248, "711,00", 9, "Helvetica", "r"),
                         (50, 262, "Lavavajillas industrial"), (470, 262, "405,95", 9, "Helvetica", "r")]
    # cuadro de desglose a la izquierda; a la derecha, totales apilados a alturas intercaladas con sus filas
    ops += [(42, 400, "Tipo IVA", 7), (150, 400, "Importe", 7, "Helvetica", "r"), (210, 400, "Dto. P.P.", 7, "Helvetica", "r"),
            (284, 400, "Base imponible", 7, "Helvetica", "r"), (350, 400, "Cuota IVA", 7, "Helvetica", "r"),
            (381, 402, "Dto. pronto pago 2 %", 8), (551, 402, "-22,34", 8, "Helvetica", "r"),
            (42, 412, "10,00 %", 7), (150, 412, "711,00", 7, "Helvetica", "r"), (210, 412, "14,22", 7, "Helvetica", "r"),
            (284, 412, "696,78", 7, "Helvetica", "r"), (350, 412, "69,68", 7, "Helvetica", "r"),
            (381, 417, "Base imponible", 8), (551, 417, "1.094,61", 8, "Helvetica", "r"),
            (42, 425, "21,00 %", 7), (150, 425, "405,95", 7, "Helvetica", "r"), (210, 425, "8,12", 7, "Helvetica", "r"),
            (284, 425, "397,83", 7, "Helvetica", "r"), (350, 425, "83,54", 7, "Helvetica", "r"),
            (381, 432, "Total IVA", 8), (551, 432, "153,22", 8, "Helvetica", "r"),
            (381, 448, "TOTAL FACTURA", 9, "Helvetica-Bold"), (551, 448, "1.247,83 €", 9, "Helvetica-Bold", "r")]
    return ops


def test_desglose_con_totales_apilados_al_lado_y_pronto_pago():
    ext = _extraer(_desglose_apilado())
    assert _desglose(ext) == [("10.00", "696.78", "69.68"), ("21.00", "397.83", "83.54")]
    assert (_v(ext["base_imponible"]), _v(ext["cuota_iva"]), _v(ext["total_factura"])) == ("1094.61", "153.22", "1247.83")


def test_rotulo_en_una_frase_no_encabeza_un_cuadro():
    ops = _cabecera() + [(50, 230, "Concepto", 9, "Helvetica-Bold"), (420, 230, "Importe", 9, "Helvetica-Bold"),
                         (50, 248, "Proyecto técnico de legalización"), (470, 248, "1.000,00", 9, "Helvetica", "r")]
    ops += [(360, 300, "Base imponible"), (540, 300, "1.000,00", 9, "Helvetica", "r"),
            (360, 314, "IVA (21 %)"), (540, 314, "210,00", 9, "Helvetica", "r"),
            (360, 328, "Total factura"), (540, 328, "1.210,00", 9, "Helvetica", "r"),
            (360, 342, "Retención a cuenta IRPF (15 %)"), (540, 342, "-150,00", 9, "Helvetica", "r"),
            (360, 362, "Neto a cobrar", 10, "Helvetica-Bold"), (540, 362, "1.060,00 €", 10, "Helvetica-Bold", "r"),
            (50, 400, "Factura sujeta a retención a cuenta del IRPF del 15 % por rendimientos de actividades", 8),
            (50, 410, "profesionales (art. 101.5 de la Ley 35/2006).", 8)]
    ext = _extraer(ops)
    assert _v(ext["retencion"]["importe"]) == "150.00"
    assert _v(ext["total_a_pagar"]) == "1060.00"


# --- Número: «N.º», rejilla con «Albarán» en otra celda ----------------------------------------------------------------
def test_numero_con_n_punto_o():
    ops = [op for op in _cabecera() if not str(op[2]).startswith("Nº factura")] + [(380, 72, "N.º 2026/017")]
    assert _v(_extraer(ops)["numero"]) == "2026/017"


def test_rejilla_de_cabecera_con_albaran_en_otra_celda():
    ops = [(50, 40, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 54, "C/ Mayor 12, 28001 Madrid"), (50, 66, f"CIF: {EMP}"),
           (470, 40, "Factura", 18, "Helvetica-Bold")]
    for k, (et, v) in enumerate([("Número", "FA26-018734"), ("Fecha", "06/11/26"), ("Albarán", "AL-55102")]):
        ops += [(330 + 75 * k, 80, et, 7), (330 + 75 * k, 92, v, 8)]
    ops += [(330, 130, "Cliente", 9, "Helvetica-Bold"), (330, 142, "DISTRIBUCIONES NORTE S.A."), (330, 154, f"CIF: {CLI}")]
    ext = _extraer(ops)
    assert _v(ext["numero"]) == "FA26-018734"
    assert _v(ext["referencias"]["albaran"]) == "AL-55102"


# --- Rótulos bilingües, «Subtotal», «Tax ID», «Project» ------------------------------------------------------------------
def test_plantilla_bilingue():
    ops = [(50, 40, "STUDIO", 20, "Helvetica-Bold"), (550, 45, "INVOICE / FACTURA", 14, "Helvetica-Bold", "r"),
           (420, 70, "Invoice no. / Nº factura", 8, "Helvetica", "r"), (550, 70, "INV-2026-0193", 8, "Helvetica", "r"),
           (420, 82, "Issue date / Fecha", 8, "Helvetica", "r"), (550, 82, "05/11/2026", 8, "Helvetica", "r"),
           (420, 94, "Project / Proyecto", 8, "Helvetica", "r"), (550, 94, "WEB-2027", 8, "Helvetica", "r"),
           (44, 100, "From / Emisor", 8, "Helvetica-Bold"), (44, 112, "Studio Digital Demo, S.L.", 10, "Helvetica-Bold"),
           (44, 124, "Calle Inventada 14"), (44, 136, "29001 Villaejemplo"), (44, 148, f"Tax ID / NIF: {EMP}"),
           (54, 190, "Bill to / Facturar a", 8, "Helvetica-Bold"), (54, 202, "DISTRIBUCIONES NORTE S.A.", 10, "Helvetica-Bold"),
           (54, 214, "Attn. / A la atención de: Dpto. de Marketing", 8), (54, 226, "Calle de la Prueba 7"),
           (54, 238, "39740 Villaprueba (Cantabria)"), (54, 250, f"Tax ID / NIF: {CLI}"),
           (52, 300, "Description / Concepto", 8, "Helvetica-Bold"), (370, 300, "Qty", 8, "Helvetica-Bold"),
           (430, 300, "Unit price", 8, "Helvetica-Bold"), (540, 300, "Amount", 8, "Helvetica-Bold", "r"),
           (52, 318, "Front-end development (hours)"), (378, 318, "64", 9, "Helvetica", "r"),
           (458, 318, "48,00", 9, "Helvetica", "r"), (540, 318, "3.072,00", 9, "Helvetica", "r"),
           (351, 360, "Subtotal"), (540, 360, "3.072,00", 9, "Helvetica", "r"), (44, 361, "Payment / Forma de pago", 8),
           (44, 373, "Transferencia bancaria · Bank transfer", 8),
           (351, 376, "VAT 21%"), (540, 376, "645,12", 9, "Helvetica", "r"),
           (351, 392, "Total EUR", 10, "Helvetica-Bold"), (540, 392, "3.717,12", 10, "Helvetica-Bold", "r")]
    ext = _extraer(ops, nif=CLI)
    assert _v(ext["emisor"]["nombre"]) == "Studio Digital Demo, S.L." and _v(ext["emisor"]["nif"]) == EMP
    assert "29001" in _v(ext["emisor"]["domicilio"])
    assert _v(ext["destinatario"]["nombre"]) == "DISTRIBUCIONES NORTE S.A." and "39740" in _v(ext["destinatario"]["domicilio"])
    # «Subtotal» es la base (no un total) y no es el valor del rótulo «Forma de pago» de su izquierda
    assert (_v(ext["base_imponible"]), _v(ext["cuota_iva"]), _v(ext["total_factura"])) == ("3072.00", "645.12", "3717.12")
    assert not ext["base_imponible"]["avisos"]
    assert _v(ext["forma_pago"]).startswith("Transferencia bancaria")
    assert _v(ext["referencias"]["proyecto"]) == "WEB-2027"


# --- Cantidad negativa y «abono» en una línea ---------------------------------------------------------------------------
def _tabla_envases(titulo="FACTURA"):
    ops = [op for op in _cabecera() if op[2] != "FACTURA"] + [(380, 50, titulo, 18, "Helvetica-Bold")]
    ops += [(50, 230, "Descripción", 9, "Helvetica-Bold"), (330, 230, "Cant.", 9, "Helvetica-Bold"),
            (420, 230, "Precio", 9, "Helvetica-Bold"), (540, 230, "Importe", 9, "Helvetica-Bold", "r")]
    for k, (c, q, p, i) in enumerate([("Cerveza rubia barril 30 L", "3", "79,50", "238,50"),
                                      ("Depósito envase barril", "3", "30,00", "90,00"),
                                      ("Abono envases devueltos", "-2", "30,00", "-60,00")]):
        y = 248 + 14 * k
        ops += [(50, y, c), (350, y, q, 9, "Helvetica", "r"), (460, y, p, 9, "Helvetica", "r"), (540, y, i, 9, "Helvetica", "r")]
    ops += [(380, 320, "Base imponible"), (540, 320, "268,50", 9, "Helvetica", "r"),
            (380, 334, "IVA 21%"), (540, 334, "56,39", 9, "Helvetica", "r"),
            (380, 348, "Total factura"), (540, 348, "324,89", 9, "Helvetica", "r")]
    return ops


def test_cantidad_negativa_y_abono_de_envases_no_es_rectificativa():
    ext = _extraer(_tabla_envases())
    assert ("Abono envases devueltos", "-2", "30.00", "-60.00") in _lineas(ext)
    assert "rectificativa" not in ext["indicios"]
    # el título sí lo indica
    assert "rectificativa" in _extraer(_tabla_envases("FACTURA DE ABONO"))["indicios"]


# --- Nombre en cuerpo grande con monograma, más ancho que el renglón del NIF --------------------------------------------
def test_nombre_con_monograma_en_banda_de_cabecera():
    ops = [(48, 62, "AD", 16, "Helvetica-Bold"), (92, 56, "Audiovisuales Demo Ejemplo, S.L.", 18, "Helvetica-Bold"),
           (92, 72, "Alquiler y montaje de sonido e iluminación para eventos", 8),
           (40, 100, "Polígono Industrial Ficticio, calle 14, nave 3"), (40, 113, "33192 Villademo (Asturias)"), (40, 126, f"CIF {EMP}"),
           (430, 60, "FACTURA", 22, "Helvetica-Bold"), (405, 100, "Número"), (555, 100, "AV-2026-0412", 9, "Helvetica", "r"),
           (405, 114, "Fecha"), (555, 114, "31/10/2026", 9, "Helvetica", "r"),
           (40, 200, "FACTURAR A", 8, "Helvetica-Bold"), (40, 214, "DISTRIBUCIONES NORTE S.A."), (40, 228, "Av. Libertad 5"),
           (40, 240, "46001 Valencia"), (40, 252, f"CIF: {CLI}")]
    ext = _extraer(ops)
    assert _v(ext["emisor"]["nombre"]) == "Audiovisuales Demo Ejemplo, S.L."
    assert "33192" in _v(ext["emisor"]["domicilio"])
    assert _v(ext["destinatario"]["nif"]) == CLI


# --- Lecturas OCR ---------------------------------------------------------------------------------------------------------
def _pagina_ocr(renglones, conf=0.9) -> Pagina:
    """renglones: (x, top, texto[, alto]); cada palabra ocupa 5 pt por carácter y un espacio de 3 pt."""
    pals = []
    for r in renglones:
        x, top, t = r[0], r[1], r[2]
        alto = r[3] if len(r) > 3 else 8
        for w in t.split():
            pals.append(Palabra(w, x, top, x + 5 * len(w), top + alto, conf, "ocr"))
            x += 5 * len(w) + 3
    return Pagina(1, 595, 842, pals, metodo="ocr", conf_media=conf)


def test_ocr_nif_con_letra_o_por_cero():
    emisor = completar_cif("B", "0060123")
    cliente = completar_cif("B", "0090457")
    pg = _pagina_ocr([(60, 60, "Talleres Demo Llanos, S.L.", 12), (60, 80, "Polígono Industrial Demo, calle E 23"),
                      (60, 92, "02007 Villaejemplo"), (60, 104, "C.I.F. " + emisor.replace("0", "O")),
                      (400, 60, "FACTURA", 16), (400, 90, "Fecha: 27-10-2026"),
                      (70, 200, "Cliente"), (70, 214, "Transportes Demo Ejemplo, S.L."), (70, 226, "Calle Inventada 88"),
                      (70, 238, "02640 Villaprueba (Albacete)"), (70, 250, "C.I.F.: " + cliente.replace("0", "O"))])
    ext = campos.Extractor([pg], "f.pdf", cliente).extraer()
    assert _v(ext["emisor"]["nif"]) == emisor and _v(ext["destinatario"]["nif"]) == cliente
    for rol in ("emisor", "destinatario"):
        assert ext[rol]["nif"]["confianza"] <= 0.6
        assert any("letra O" in a for a in ext[rol]["nif"]["avisos"])


def test_ocr_nif_corregido_solo_si_el_control_es_valido():
    valido = completar_cif("B", "0060123")
    malo = valido[:-1] + str((int(valido[-1]) + 1) % 10)
    malo = malo.replace("0", "O")  # con 0 sigue sin ser válido: no se inventa un NIF
    pg = _pagina_ocr([(60, 60, "Talleres Demo Llanos, S.L.", 12), (60, 80, "C.I.F. " + malo), (400, 60, "FACTURA", 16)])
    ext = campos.Extractor([pg], "f.pdf", None).extraer()
    assert _v(ext["emisor"]["nif"]) is None and _v(ext["destinatario"]["nif"]) is None


def _tabla_ocr(precio_rotulo="Ixs", filas=None):
    filas = filas or [("Pastillas de freno delanteras", "86,40", "86,40"), ("Aceite de motor 5W30", "1260", "94,50"),
                      ("Mano de obra mecánica", "48,00", "168,00")]
    r = [(60, 60, "Talleres Demo Llanos, S.L.", 12), (60, 80, "C.I.F. " + EMP), (400, 60, "FACTURA", 16),
         (70, 300, "Descripción"), (405, 300, "Cant."), (462, 300, precio_rotulo), (530, 300, "importe")]
    for k, (c, p, i) in enumerate(filas):
        y = 320 + 20 * k
        r += [(70, y, c), (491 - 5 * len(p), y, p), (564 - 5 * len(i), y, i)]
    return [_pagina_ocr(r)]


def test_ocr_columna_de_precio_ilegible_y_coma_perdida():
    ext = campos.Extractor(_tabla_ocr(), "f.pdf", EMP).extraer()
    assert [(c, q, p, i) for c, q, p, i in _lineas(ext)] == [
        ("Pastillas de freno delanteras", "1.00", "86.40", "86.40"),
        ("Aceite de motor 5W30", "7.50", "12.60", "94.50"),
        ("Mano de obra mecánica", "3.50", "48.00", "168.00")]
    aceite = ext["lineas"][1]
    assert aceite["precio"]["confianza"] <= 0.6 and "coma decimal" in aceite["precio"]["avisos"][0]
    assert aceite["cantidad"]["confianza"] <= 0.6
    assert "precio sin rótulo" in ext["aviso_lineas"]


def test_ocr_coma_perdida_no_se_supone_si_no_cuadra():
    # «1261» con importe 94,50: con la coma (12,61) la cantidad no sale exacta; el precio queda como se leyó y se bloquea
    ext = campos.Extractor(_tabla_ocr(filas=[("Pastillas de freno", "86,40", "86,40"), ("Aceite de motor", "1261", "94,50"),
                                             ("Mano de obra", "48,00", "168,00")]), "f.pdf", EMP).extraer()
    assert _lineas(ext)[1][1:] == (None, "1261", "94.50")


def test_columna_sin_rotulo_que_no_es_precio_no_se_deduce():
    # cifras alineadas entre cantidad e importe que no cuadran como precio (un tipo de IVA sin rótulo): no se deducen
    filas = [("Pastillas de freno", "21", "86,40"), ("Aceite de motor", "21", "84,00"), ("Mano de obra", "10", "168,13")]
    ext = campos.Extractor(_tabla_ocr(filas=filas), "f.pdf", EMP).extraer()
    assert all(p is None for _, _, p, _ in _lineas(ext))


# --- Reglas supuestas o ajustadas con pocos documentos: siempre se piden confirmar ---------------------------------------
def test_ocr_n2_factura_es_un_rotulo_supuesto_que_se_confirma():
    """«N2 FACTURA» (el OCR lee «Nº» como «N2»): rótulo supuesto, como «NO:»; el número se propone pero no con la confianza
    de un rótulo leído."""
    pg = _pagina_ocr([(60, 60, "Talleres Demo Llanos, S.L.", 12), (60, 80, "C.I.F. " + EMP), (400, 60, "FACTURA", 16),
                      (400, 90, "N2 FACTURA FV26-001234"), (400, 104, "Fecha: 27-10-2026")], conf=0.95)
    ext = campos.Extractor([pg], "f.pdf", EMP).extraer()
    assert _v(ext["numero"]) == "FV26-001234" and ext["numero"]["confianza"] <= 0.8
    assert "numero" in {d["campo"] for d in borradores.campos_dudosos(ext)}


def test_monograma_omitido_en_el_destinatario_se_confirma():
    ops = [(50, 50, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 64, "C/ Mayor 12, 28001 Madrid"), (50, 76, f"NIF: {EMP}"),
           (380, 50, "FACTURA", 18, "Helvetica-Bold"), (380, 72, "Nº factura: F-2026/045"), (380, 84, "Fecha: 15/10/2026"),
           (330, 120, "Cliente:", 9, "Helvetica-Bold"), (330, 138, "CD", 12, "Helvetica-Bold"),
           (356, 134, "Comercial Demo Sur, S.L.", 13, "Helvetica-Bold"), (330, 150, "Av. Libertad 5"), (330, 162, "46001 Valencia"),
           (330, 174, f"CIF: {CLI}")]
    nombre = _extraer(ops)["destinatario"]["nombre"]
    assert nombre["valor"] == "Comercial Demo Sur, S.L." and nombre["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("«CD»" in a for a in nombre["avisos"])


# --- Datos del tercer corpus: evidentemente ficticios ----------------------------------------------------------------------
CORPUS_H3 = Path(__file__).resolve().parent / "corpus_holdout3"
RE_VIA = re.compile(r"(?<![\w/])(?:Calle|C/|Avda\.|Avenida|Avinguda|Carrer|Pol\.\s*Ind\.|Pol[ií]gono|Paseo|Passeig|Camino|"
                    r"Carretera|Ctra\.|Barrio|Plaza|Pla[cç]a|Ronda|Traves[ií]a|Glorieta)\s")
# NIF, NIE o DNI con separadores opcionales (puntos de millar, guiones o espacios entre letra, cifras y control)
RE_IDENTIFICADOR = re.compile(r"(?<![\w])(?:ES)?(?:[XYZ][\s.\-]?\d[\s.]?\d{3}[\s.]?\d{3}|\d{2}[\s.]?\d{3}[\s.]?\d{3}|"
                              r"[A-HJNP-SUVW][\s.\-]?\d{2}[\s.]?\d{3}[\s.]?\d{2})[\s.\-]?[0-9A-Z](?![\w])", re.I)
RE_FICTICIA = re.compile(r"Inventad[oa]s?|Inventat|Fictici[oa]s?|Imaginari[oa]s?|Imaginària", re.I)


def _nif_evidentemente_ficticio(n: str) -> bool:
    """CIF con código de provincia 00 (no asignado) o DNI/NIE de cifras repetidas: no puede ser de nadie."""
    return re.fullmatch(r"[A-HJNP-SUVW]00\d{5}[0-9A-J]|(\d)\1{7}[A-Z]|[XYZ](\d)\2{6}[A-Z]", n) is not None


@pytest.mark.skipif(not CORPUS_H3.exists(), reason="sin el tercer corpus reservado")
def test_corpus_holdout3_sin_identificadores_ni_calles_verosimiles():
    """El corpus se publica: ningún NIF, NIE o DNI que aparezca en él (campos y notas de los JSON, texto de los PDF y
    código de los generadores, también con puntos, guiones o espacios: «12.345.678-Z», «X-1234567-L») puede ser de una
    persona o empresa real, ni siquiera el de los escaneados (sin capa de texto, solo lo cubren JSON y generador), y sus
    calles son inventadas."""
    fuentes = sorted(CORPUS_H3.glob("*.json")) + sorted((CORPUS_H3.parent.parent / "scripts" / "corpus").glob("holdout3_*.py"))
    assert len(fuentes) >= 15
    revisados = 0
    for f in fuentes:
        txt = f.read_text(encoding="utf-8")
        if f.suffix == ".json":
            esp = json.loads(txt)
            for n in (esp["emisor"]["nif"], esp["destinatario"]["nif"], esp["empresa_nif"]):
                assert _nif_evidentemente_ficticio(n), (f.name, n)
            if f.with_suffix(".pdf").exists():
                with pdfplumber.open(f.with_suffix(".pdf")) as doc:
                    txt += "\n" + "\n".join(p.extract_text() or "" for p in doc.pages)
        for m in RE_IDENTIFICADOR.finditer(txt):
            n = re.sub(r"^ES(?=[A-Z0-9]{9}$)", "", re.sub(r"[\s.\-]", "", m.group()).upper())
            if es_valido(n):
                assert _nif_evidentemente_ficticio(n), (f.name, m.group())
        if f.suffix != ".json":
            continue
        for linea in txt.splitlines():
            vias = list(RE_VIA.finditer(linea))
            for i, m in enumerate(vias):
                tramo = linea[m.start():vias[i + 1].start() if i + 1 < len(vias) else None]
                assert RE_FICTICIA.search(tramo), (f.name, tramo)
                revisados += 1
    assert revisados >= 10  # el patrón de calles sigue encontrando los domicilios


def test_guardia_de_identificadores_detecta_formas_con_separadores():
    for txt in ("DNI 12.345.678-Z", "impreso con guiones 'X-1234567-L'", "CIF B-12345674", "B 1234567 4",
                "NIF-IVA ESB12345674", "DNI 12 345 678 Z"):
        ns = [re.sub(r"^ES|[\s.\-]", "", re.sub(r"[\s.\-]", "", m.group()).upper()) for m in RE_IDENTIFICADOR.finditer(txt)]
        assert any(es_valido(n) and not _nif_evidentemente_ficticio(n) for n in ns), txt
