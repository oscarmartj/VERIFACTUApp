"""Regresiones de la prueba con dos facturas reales más de un mismo emisor (carril C: texto nativo, fecha, referencia y
forma de pago). Las facturas no se publican: cada causa se reproduce con documentos sintéticos (reportlab, datos
ficticios: NIF de la provincia 00, calles inventadas).

- C1: un blanco de una fuente TrueType incrustada cuyo glifo no tiene contornos, cruzado por una línea recta larga (borde
  vertical de un recuadro, separador de columnas de una tabla monoespaciada de un ERP), no «dibuja» una cifra: la palabra
  contigua no es dudosa. Un blanco cuyo glifo sí dibuja una cifra se sigue detectando aunque lo cruce una línea (en
  cualquier fuente: la línea no se descuenta de un glifo con contornos), y lo que se dibuje aparte sobre un blanco vacío
  (una cifra trazada) también.
- C2: la fecha de carta («Villaficticia, 14 de octubre de 2026») sin rótulo ni otra fecha suelta que la contradiga se
  acepta con la confianza normal; con otra fecha sin rótulo, o con un rótulo «Fecha:» cuyo valor no se ha podido usar
  (ilegible, o el renglón dice además otra cosa), sigue a confirmar.
- C3: «REFERENCIA : texto» es la referencia comercial del documento (referencias["referencia"]); la cita de otra factura no.
  Un rótulo vacío no toma como valor un bloque lejano de su derecha (el recuadro del cliente).
- C4: la forma de pago continúa en los renglones de debajo de su valor, en la misma columna, hasta la cuenta o el IBAN;
  el punto final de una abreviatura («F.F.») se conserva. No continúa tras una frase acabada, ni en renglones con rótulo
  propio aunque no lleve dos puntos («BIC …», «Titular …», «OBSERVACIONES», «Portes  15,00»); si un renglón unido no
  habla del pago, la forma de pago se marca para confirmar.
"""
import io
import re
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, texto
from app.nif import completar_cif

EMP = completar_cif("B", "0012345")   # emisor (empresa configurada)
CLI = completar_cif("A", "0054321")   # cliente
NUMERO = "F-2026/101"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
FREESERIF = Path("/usr/share/fonts/truetype/freefont/FreeSerif.ttf")
CARLITO = Path("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf")
AVISO_NO_SE_VE = "puede no verse en la página"


def _fuente(nombre: str, ruta: Path) -> str:
    if not ruta.exists():
        pytest.skip(f"sin la fuente {ruta.name} del sistema")
    if nombre not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(nombre, str(ruta)))
    return nombre


def _lienzo():
    buf = io.BytesIO()
    return buf, canvas.Canvas(buf, pagesize=A4, invariant=1)


def _escribir(c, ops, fuente="Helvetica"):
    """ops: (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    alto = A4[1]
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 and op[4] else fuente, op[3] if len(op) > 3 else 9)
        (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, alto - y, s)


def _pdf(ops, fuente="Helvetica") -> bytes:
    buf, c = _lienzo()
    _escribir(c, ops, fuente)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos: bytes):
    pags = texto.leer(datos)
    return pags, campos.Extractor(pags, "f.pdf", EMP).extraer()


def _dudosos(e) -> set:
    return {d["campo"] for d in borradores.campos_dudosos(e)}


def _cabecera(fecha=True):
    ops = [(50, 50, "TALLERES FICTICIOS S.L.", 12, "Helvetica-Bold"), (50, 64, "Calle Inventada 7, 00001 Villaficticia"),
           (50, 76, f"CIF: {EMP}"), (380, 50, "FACTURA", 16, "Helvetica-Bold"), (380, 70, f"Nº factura: {NUMERO}")]
    if fecha:
        ops.append((380, 82, "Fecha: 15/10/2026"))
    return ops + [(330, 110, "Cliente:", 9, "Helvetica-Bold"), (330, 122, "COMERCIAL IMAGINARIA S.A."),
                  (330, 134, f"CIF: {CLI}"), (330, 146, "Calle Ficticia 22, 00002 Pueblo Inventado")]


def _cuerpo(y0=300):
    return [(50, y0, "Concepto", 9, "Helvetica-Bold"), (330, y0, "Cantidad", 9, "Helvetica-Bold"),
            (420, y0, "Precio", 9, "Helvetica-Bold"), (540, y0, "Importe", 9, "Helvetica-Bold", "r"),
            (50, y0 + 18, "Mantenimiento de instalaciones"), (370, y0 + 18, "1", 9, None, "r"),
            (460, y0 + 18, "700,00", 9, None, "r"), (540, y0 + 18, "700,00", 9, None, "r"),
            (380, y0 + 60, "Base imponible"), (540, y0 + 60, "700,00", 9, None, "r"),
            (380, y0 + 74, "IVA 21%"), (540, y0 + 74, "147,00", 9, None, "r"),
            (380, y0 + 88, "Total factura"), (540, y0 + 88, "847,00", 9, None, "r")]


# --- C1: blancos cruzados por una línea recta larga --------------------------------------------------------------------
def _recuadro_cliente_con_blancos_en_el_borde(alto_caja=90) -> bytes:
    """Cliente en un recuadro con su texto en una fuente incrustada y alineado a la derecha; el renglón del CIF acaba en
    dos blancos y el borde derecho del recuadro pasa por el centro del segundo (así lo exportan algunos programas). Con
    alto_caja pequeño, el recuadro es de un solo renglón (el del CIF) y los demás datos van encima."""
    f = _fuente("SansIncrustadaC", DEJAVU)
    buf, c = _lienzo()
    alto = A4[1]
    _escribir(c, [(50, 50, "TALLERES FICTICIOS S.L.", 12), (50, 64, "Calle Inventada 7, 00001 Villaficticia"),
                  (50, 76, f"CIF: {EMP}"), (50, 120, "FACTURA", 16), (50, 140, f"Nº factura: {NUMERO}"),
                  (50, 152, "Fecha: 15/10/2026")], f)
    borde = 540.0
    blanco = pdfmetrics.stringWidth(" ", f, 9)
    c.setLineWidth(0.6)
    c.rect(320, alto - (100 + alto_caja), borde - 320, alto_caja, stroke=1, fill=0)
    datos = [(330, 115, "CLIENTE:"), (330, 128, "COMERCIAL IMAGINARIA S.A."), (330, 141, "Calle Ficticia 22, 00002 Pueblo Inventado")]
    if alto_caja < 50:
        datos = [(x, y - 45, t) for x, y, t in datos]
    ycif = 154 if alto_caja >= 50 else 100 + alto_caja / 2 + 3
    _escribir(c, datos + [(borde + 0.5 * blanco, ycif, f"CIF: {CLI}  ", 9, None, "r")], f)
    _escribir(c, _cuerpo(230), f)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize("alto_caja", [90, 14])
def test_blanco_cruzado_por_el_borde_de_un_recuadro_no_hace_dudosa_la_palabra(alto_caja):
    """Antes: el borde vertical dentro de la caja del blanco contaba como tinta del blanco («dibuja algo») y el CIF del
    cliente, contiguo, quedaba en confianza 0,5 con el aviso de texto que puede no verse. El glifo del blanco no tiene
    contornos en la fuente incrustada: la línea larga que lo cruza no es suya (también en un recuadro de un renglón)."""
    pags, e = _extraer(_recuadro_cliente_con_blancos_en_el_borde(alto_caja))
    assert pags[0].metodo == "texto_pdf"
    assert [p.conf for p in pags[0].palabras if CLI in p.texto] == [1.0]
    assert e["destinatario"]["nif"]["valor"] == CLI
    assert not any(AVISO_NO_SE_VE in a for a in e["destinatario"]["nif"]["avisos"])


def _tabla_erp_monoespaciada() -> bytes:
    """Tabla de un ERP en una fuente monoespaciada incrustada: las columnas se rellenan con blancos y los separadores
    verticales se dibujan sobre el blanco que sigue a cada cifra."""
    f = _fuente("MonoIncrustadaC", DEJAVU_MONO)
    buf, c = _lienzo()
    alto, tam = A4[1], 9
    _escribir(c, _cabecera(), f)
    paso = pdfmetrics.stringWidth(" ", f, tam)
    anchos = [24, 7, 10, 11]
    filas = [("Concepto", "Cant.", "Precio", "Importe"), ("Revision de equipos", "2", "350,00", "700,00"),
             ("Desplazamiento", "1", "50,00", "50,00")]
    y = 300
    for fila in filas:
        s = "".join(v.ljust(a) if k == 0 else v.rjust(a - 1) + " " for k, (v, a) in enumerate(zip(fila, anchos)))
        _escribir(c, [(50, y, s, tam)], f)
        y += 14
    c.setLineWidth(0.6)
    for x in [47.0] + [50 + paso * (sum(anchos[:k + 1]) - 0.5) for k in range(len(anchos))]:
        c.line(x, alto - 290, x, alto - (y - 4))
    _escribir(c, [(330, y + 20, "Base imponible      750,00", tam), (330, y + 34, "IVA 21%             157,50", tam),
                  (330, y + 48, "Total factura       907,50", tam)], f)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_separadores_de_columna_sobre_los_blancos_de_relleno_no_hacen_dudosas_las_cifras():
    """Antes: todas las cifras de la tabla (cantidades, precios e importes) quedaban dudosas por los separadores verticales
    que cruzan los blancos de relleno contiguos."""
    pags, e = _extraer(_tabla_erp_monoespaciada())
    assert pags[0].metodo == "texto_pdf"
    assert not [p.texto for p in pags[0].palabras if p.conf < 1.0]
    importes = [l["importe"] for l in e["lineas"] if l["importe"]["valor"] is not None]
    assert [i["valor"] for i in importes] == ["700.00", "50.00"]
    assert all(i["confianza"] >= borradores.UMBRAL_CONFIANZA for i in importes)


IMPORTES_BUENOS = ("4152.00", "871.92", "5023.92")
FUENTES_CIFRA = {"SansIncrustadaC": DEJAVU, "SerifIncrustadaC": FREESERIF, "CarlitoIncrustadaC": CARLITO}


def _totales_con_cifra_oculta_y_lineas(cifra: str, fuente="SansIncrustadaC") -> bytes:
    """Totales en una fuente incrustada con líneas verticales largas que pasan por el centro de cada glifo de `cifra`, y
    el /ToUnicode de ese glifo cambiado por un espacio (se ve la cifra y el texto dice un blanco)."""
    from pypdf import PdfReader, PdfWriter
    f = _fuente(fuente, FUENTES_CIFRA[fuente])
    buf, c = _lienzo()
    alto = A4[1]
    _escribir(c, _cabecera())
    _escribir(c, [(50, 300, "Concepto", 9, "Helvetica-Bold"), (540, 300, "Importe", 9, "Helvetica-Bold", "r"),
                  (50, 318, "Reparación de maquinaria"), (380, 380, "Base imponible"), (380, 394, "IVA 21%"),
                  (380, 408, "Total factura")])
    valores = [(318, "4.152,00"), (380, "4.152,00"), (394, "871,92"), (408, "5.023,92")]
    c.setLineWidth(0.6)
    for y, v in valores:
        _escribir(c, [(540, y, v, 10, f, "r")])
        x0 = 540 - pdfmetrics.stringWidth(v, f, 10)
        for k, ch in enumerate(v):
            if ch == cifra:
                x = x0 + pdfmetrics.stringWidth(v[:k], f, 10) + 0.5 * pdfmetrics.stringWidth(ch, f, 10)
                c.line(x, alto - (y - 30), x, alto - (y + 30))
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    fuentes = w.pages[0]["/Resources"]["/Font"]
    nombre_ps = pdfmetrics.getFont(f).face.name
    nombre_ps = nombre_ps.decode() if isinstance(nombre_ps, bytes) else str(nombre_ps)
    for k in fuentes:
        fd = fuentes[k].get_object()
        if nombre_ps not in str(fd.get("/BaseFont")):
            continue
        tu = fd["/ToUnicode"].get_object()
        cm = tu.get_data().decode("latin-1")
        cod = {u.upper(): c_ for c_, u in re.findall(r"<([0-9A-Fa-f]{2})>\s*<([0-9A-Fa-f]{4})>", cm)}[f"{ord(cifra):04X}"]
        tu.set_data(re.sub(r"<%s>\s*<%04X>" % (cod, ord(cifra)), f"<{cod}> <0020>", cm).encode("latin-1"))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


@pytest.mark.parametrize("fuente, cifra", [("SansIncrustadaC", "1"), ("SansIncrustadaC", "2"), ("SansIncrustadaC", "5"),
                                           ("SerifIncrustadaC", "1"), ("SerifIncrustadaC", "4"), ("CarlitoIncrustadaC", "1"),
                                           ("CarlitoIncrustadaC", "5")])
def test_blanco_que_dibuja_una_cifra_se_detecta_aunque_lo_cruce_una_linea(monkeypatch, fuente, cifra):
    """Una cifra de los importes traducida por un espacio y una línea vertical larga por el centro de cada glifo de esa
    cifra: el glifo tiene contornos, así que toda la tinta de su caja cuenta, también la de la línea. Antes (con la línea
    descontada siempre) el «1» de FreeSerif o de Carlito, cuyo palo coincide con la línea, dejaba de ser dudoso y la base
    salía «52.00» con confianza normal. Sin el contraste de las cifras con el OCR (_verificar_cifras), para que solo
    cuente la comprobación de los blancos."""
    monkeypatch.setattr(texto, "_verificar_cifras", lambda *a, **k: {})
    pags, e = _extraer(_totales_con_cifra_oculta_y_lineas(cifra, fuente))
    assert pags[0].metodo == "texto_pdf"
    for k in ("base_imponible", "cuota_iva", "total_factura"):
        assert e[k]["valor"] is None or e[k]["valor"] in IMPORTES_BUENOS or k in _dudosos(e), (k, e[k])
    assert e["base_imponible"]["valor"] in IMPORTES_BUENOS or "base_imponible" in _dudosos(e)
    assert any(AVISO_NO_SE_VE in a for a in e["base_imponible"]["avisos"]) or e["base_imponible"]["valor"] in IMPORTES_BUENOS
    assert any(p.conf < 1.0 and re.search(r"\d", p.texto) for p in pags[0].palabras)


def _cifra_trazada_sobre_un_blanco(ancho_linea: float, tam: float) -> bytes:
    """Totales en una fuente incrustada con un espacio de verdad (glifo sin contornos) entre el «4» y el «52,00», y un
    «1» trazado aparte (palo y bandera, como trazos de la página) sobre ese espacio: se ve «4152,00». El cuerpo de letra
    es grande para que el espacio (un tercio de cuadratín) deje sitio a la comprobación de tinta, que mira el centro de su
    caja sin los bordes de las palabras contiguas."""
    f = _fuente("SansIncrustadaC", DEJAVU)
    buf, c = _lienzo()
    alto = A4[1]
    _escribir(c, _cabecera())
    _escribir(c, [(50, 300, "Concepto", 9, "Helvetica-Bold"), (540, 300, "Importe", 9, "Helvetica-Bold", "r"),
                  (50, 318, "Reparación de maquinaria"), (330, 380, "Base imponible"), (330, 410, "IVA 21%"),
                  (330, 440, "Total factura")])
    c.setLineWidth(ancho_linea)
    for y, v in [(318, "4 52,00"), (380, "4 52,00"), (410, "871,92"), (440, "5.023,92")]:
        _escribir(c, [(540, y, v, tam, f, "r")])
        if " " in v:
            x0 = 540 - pdfmetrics.stringWidth(v, f, tam) + pdfmetrics.stringWidth("4", f, tam)
            xc = x0 + 0.5 * pdfmetrics.stringWidth(" ", f, tam)
            c.line(xc, alto - y, xc, alto - (y - 0.72 * tam))
            c.line(xc, alto - (y - 0.72 * tam), xc - 0.18 * tam, alto - (y - 0.55 * tam))
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize("ancho_linea, tam", [(1.2, 18), (2.0, 24)])
def test_cifra_trazada_sobre_un_blanco_sin_contornos_sigue_siendo_dudosa(monkeypatch, ancho_linea, tam):
    """Que el glifo del blanco no dibuje nada solo descuenta las líneas rectas largas que cruzan su caja: un trazo del
    tamaño de una cifra dibujado encima sigue haciendo dudosas las cifras contiguas."""
    monkeypatch.setattr(texto, "_verificar_cifras", lambda *a, **k: {})
    pags, e = _extraer(_cifra_trazada_sobre_un_blanco(ancho_linea, tam))
    assert pags[0].metodo == "texto_pdf"
    assert {p.texto for p in pags[0].palabras if p.conf < 1.0} >= {"4", "52,00"}
    assert e["base_imponible"]["valor"] in (None, "4152.00") or "base_imponible" in _dudosos(e)


def test_programa_truetype_distingue_glifos_sin_contornos():
    """Unidad: en el programa TrueType de la fuente, el espacio no tiene contornos y las cifras sí."""
    from app.extraccion import texto_proceso
    if not DEJAVU.exists():
        pytest.skip("sin DejaVuSans")
    prog = texto_proceso._ProgramaTT(DEJAVU.read_bytes())
    cmap = prog.cmaps[(3, 1)]
    assert prog.vacio(cmap(ord(" "))) is True
    assert all(prog.vacio(cmap(ord(ch))) is False for ch in "0123456789")
    assert prog.vacio(0) is None   # .notdef: según el visor, una caja o nada
    assert texto_proceso._ProgramaTT(b"OTTO" + bytes(64)).tablas == {}   # CFF: no se sabe


# --- C2: fecha de carta ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("renglon, iso", [
    ("Villaficticia, 14 de octubre de 2026", "2026-10-14"),
    ("San Martín de la Imaginaria, a 3 de octubre de 2026", "2026-10-03"),
    ("VILLAFICTICIA, 14/10/2026", "2026-10-14"),
    ("Villaficticia, jueves, 1 de octubre de 2026.", "2026-10-01"),
])
def test_fecha_de_carta_sin_otra_fecha_tiene_confianza_normal(renglon, iso):
    """Antes: la fecha se encontraba, pero con confianza 0,6 y «Fecha sin etiqueta: confirme»."""
    _, e = _extraer(_pdf(_cabecera(fecha=False) + [(540, 200, renglon, 9, None, "r")] + _cuerpo()))
    assert e["fecha"]["valor"] == iso
    assert e["fecha"]["confianza"] >= borradores.UMBRAL_CONFIANZA and not e["fecha"]["avisos"]
    assert "fecha" not in _dudosos(e)


@pytest.mark.parametrize("extra", [
    [(50, 220, "Periodo: 01/10/2026 - 31/10/2026")],      # otra fecha sin rótulo de fecha
    [(50, 500, "Pedido 02/10/2026 de la oficina central")],   # «pedido» excluye el renglón como fecha propia: no cuenta
])
def test_fecha_de_carta_con_otras_fechas(extra):
    _, e = _extraer(_pdf(_cabecera(fecha=False) + [(540, 200, "Villaficticia, 14 de octubre de 2026", 9, None, "r")]
                         + extra + _cuerpo()))
    assert e["fecha"]["valor"] == "2026-10-14"
    if "Periodo" in extra[0][2]:
        assert e["fecha"]["confianza"] == 0.6 and "fecha" in _dudosos(e)
    else:
        assert e["fecha"]["confianza"] >= borradores.UMBRAL_CONFIANZA


@pytest.mark.parametrize("renglon", ["Villaficticia 14/10/2026", "Calle Inventada 7, 14/10/2026", "Factura, 14/10/2026",
                                     "Recibido, 14/10/2026", "Pagado en Villaficticia, 14/10/2026",
                                     "Villaficticia, 14/10/2026 a las 10:00"])
def test_fecha_suelta_que_no_es_de_carta_sigue_a_confirmar(renglon):
    """Sin lugar (o con un sello: «Recibido, …») o con más texto que la fecha tras la coma: la fecha sin rótulo se confirma."""
    _, e = _extraer(_pdf(_cabecera(fecha=False) + [(330, 200, renglon)] + _cuerpo()))
    assert e["fecha"]["valor"] == "2026-10-14"
    assert e["fecha"]["confianza"] < borradores.UMBRAL_CONFIANZA and "fecha" in _dudosos(e)


def test_fecha_con_rotulo_prevalece_sobre_la_de_carta():
    _, e = _extraer(_pdf(_cabecera() + [(540, 200, "Villaficticia, 9 de octubre de 2026", 9, None, "r")] + _cuerpo()))
    assert e["fecha"]["valor"] == "2026-10-15" and e["fecha"]["confianza"] >= borradores.UMBRAL_CONFIANZA


CARTA = (540, 200, "Villaficticia, 14 de octubre de 2026", 9, None, "r")


@pytest.mark.parametrize("rotulo", ["Fecha: 15/10/2026 - Entrega en obra", "Fecha: 15/10/2026 Pedido: 4471",
                                    "Fecha: 15-oct-26", "Fecha: 2026/10/15", "FECHA FACTURA 15.X.2026"])
def test_rotulo_de_fecha_sin_leer_impide_aceptar_la_de_carta(rotulo):
    """Antes: la búsqueda con rótulo descartaba el renglón «Fecha: …» (por decir además «entrega» o «pedido», o por no
    poder leer su valor) y la fecha de carta, distinta, se aceptaba con confianza normal y sin aviso."""
    _, e = _extraer(_pdf(_cabecera(fecha=False) + [(380, 82, rotulo), CARTA] + _cuerpo()))
    assert e["fecha"]["valor"] is not None
    assert e["fecha"]["confianza"] < borradores.UMBRAL_CONFIANZA and "fecha" in _dudosos(e)


@pytest.mark.parametrize("otro", ["Fecha de vencimiento: 15/11/2026", "Fecha y firma del cliente",
                                  "Fecha valor 20/10/2026 según condiciones"])
def test_rotulos_de_otras_fechas_no_impiden_la_de_carta(otro):
    _, e = _extraer(_pdf(_cabecera(fecha=False) + [CARTA, (50, 560, otro)] + _cuerpo()))
    assert e["fecha"]["valor"] == "2026-10-14" and e["fecha"]["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_sello_de_ejemplar_no_es_un_lugar():
    _, e = _extraer(_pdf(_cabecera(fecha=False) + [(540, 200, "Original, 20/10/2026", 9, None, "r")] + _cuerpo()))
    assert e["fecha"]["confianza"] < borradores.UMBRAL_CONFIANZA


# --- C3: referencia comercial del documento ----------------------------------------------------------------------------
@pytest.mark.parametrize("ops, valor", [
    ([(100, 230, "REFERENCIA : CAMPAÑA FICTICIA DE OTOÑO 2026")], "CAMPAÑA FICTICIA DE OTOÑO 2026"),
    ([(100, 230, "REFERENCIA :", 10, "Helvetica-Bold"), (200, 230, "BOLETINES IMAGINARIOS DE OTOÑO")],
     "BOLETINES IMAGINARIOS DE OTOÑO"),
    ([(50, 230, "Ref.: PC-00123")], "PC-00123"),
])
def test_referencia_comercial(ops, valor):
    """Antes: «REFERENCIA : texto» no se recogía (referencias vacías)."""
    _, e = _extraer(_pdf(_cabecera() + ops + _cuerpo()))
    assert e["referencias"]["referencia"]["valor"] == valor
    assert e["referencias"]["referencia"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert e["numero"]["valor"] == NUMERO


@pytest.mark.parametrize("renglones", [
    ["Referencia factura nº F-2026/030"],
    ["Ref.: Factura rectificada F-2026/030"],
    ["Su referencia factura nº F-2026/030"],
    ["REFERENCIA : Abono nº 3"],
    ["Factura rectificada", "Referencia: F-2026/030"],
    ["Referencia catastral: 0000000AB0000A0001AA"],
])
def test_la_cita_de_otro_documento_no_es_la_referencia(renglones):
    _, e = _extraer(_pdf(_cabecera() + [(50, 230 + 12 * k, s) for k, s in enumerate(renglones)] + _cuerpo()))
    assert "referencia" not in e["referencias"]
    assert e["numero"]["valor"] == NUMERO


def _cabecera_erp(rotulo, y, valor=None, x_valor=None, x_cliente=330):
    """Datos del documento a la izquierda (con un rótulo de referencia) y el cliente a la derecha, a la misma altura."""
    ops = [(50, 50, "TALLERES FICTICIOS S.L.", 12, "Helvetica-Bold"), (50, 64, "Calle Inventada 7, 00001 Villaficticia"),
           (50, 76, f"CIF: {EMP}"), (50, 110, "FACTURA", 14, "Helvetica-Bold"), (50, 126, f"Nº factura: {NUMERO}"),
           (50, 138, "Fecha: 15/10/2026"), (50, y, rotulo),
           (x_cliente, 126, "COMERCIAL IMAGINARIA S.A.", 9, "Helvetica-Bold"), (x_cliente, 138, f"CIF: {CLI}"),
           (x_cliente, 150, "Calle Ficticia 22"), (x_cliente, 162, "00002 Pueblo Inventado")]
    if valor:
        ops.append((x_valor, y, valor))
    return ops


@pytest.mark.parametrize("rotulo, y, clave, x_cliente", [("Referencia:", 150, "referencia", 330), ("Ref.:", 162, "referencia", 330),
                                                         ("Obra:", 150, "obra", 330), ("Pedido:", 162, "pedido", 330),
                                                         ("Referencia:", 150, "referencia", 175)])
def test_rotulo_vacio_no_toma_el_recuadro_del_cliente(rotulo, y, clave, x_cliente):
    """Antes: «Referencia:» sin valor tomaba el renglón del recuadro del cliente de su derecha («Calle Ficticia 22»),
    a 0,97 y sin aviso; lo mismo «Obra:» y «Pedido:» (este, el código postal de «00002 Pueblo Inventado»). También con el
    recuadro del cliente más cerca del rótulo."""
    _, e = _extraer(_pdf(_cabecera_erp(rotulo, y, x_cliente=x_cliente) + _cuerpo()))
    assert clave not in e["referencias"]
    assert e["numero"]["valor"] == NUMERO


@pytest.mark.parametrize("alineacion", ["l", "r"])
def test_rejilla_de_rotulos_con_valores_lejanos_alineados(alineacion):
    """Rótulos sueltos en una columna y sus valores alineados a la derecha del recuadro, lejos: el valor es el de la fila
    (columna de valores de una rejilla), con la confianza normal. Los rótulos, alineados a la izquierda o a la derecha."""
    filas = [("Nº cliente", "0815"), ("Agente", "Ficticio"), ("Referencia:", "PC-00123"), ("Albarán", "AL-00451")]
    ops = _cabecera()
    for k, (r, v) in enumerate(filas):
        y = 200 + 14 * k
        ops += [(330 if alineacion == "l" else 400, y, r, 9, None, alineacion), (550, y, v, 9, None, "r")]
    _, e = _extraer(_pdf(ops + _cuerpo()))
    for clave, valor in (("referencia", "PC-00123"), ("albaran", "AL-00451")):
        assert e["referencias"][clave]["valor"] == valor
        assert e["referencias"][clave]["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_valor_algo_lejos_del_rotulo_se_confirma():
    """Un rótulo con su valor lejos a la derecha, sin más renglones alineados con él: se toma, pero a confirmar; cerca, con
    la confianza normal."""
    _, e = _extraer(_pdf(_cabecera_erp("Referencia:", 186, "PC-00123", 250) + _cuerpo()))
    r = e["referencias"]["referencia"]
    assert r["valor"] == "PC-00123" and r["confianza"] < borradores.UMBRAL_CONFIANZA and r["avisos"]
    _, e = _extraer(_pdf(_cabecera_erp("Referencia:", 186, "PC-00123", 150) + _cuerpo()))
    r = e["referencias"]["referencia"]
    assert r["valor"] == "PC-00123" and r["confianza"] >= borradores.UMBRAL_CONFIANZA


# --- C4: forma de pago con renglones de continuación -------------------------------------------------------------------
PIE_PAGO = [(150, 552, "C.C.C.: 0000 0000 00 0000000000"), (150, 564, "IBAN: ES00 0000 0000 0000 0000 0000")]


@pytest.mark.parametrize("valor", [
    [(150, 516, "60 DÍAS F.F."), (150, 528, "TRANSFERENCIA BANCARIA"), (150, 540, "BANCO FICTICIO")],
    [(150, 516, "60 DÍAS F.F. TRANSFERENCIA BANCARIA"), (150, 528, "BANCO FICTICIO"), (150, 540, "C.C.C.: 0000 0000 00 0000000000")],
    # otra columna en la fila de la continuación, algo más alta (no corta la continuación)
    [(150, 516, "60 DÍAS F.F."), (150, 528, "TRANSFERENCIA BANCARIA"), (380, 527, "Conserve este documento"),
     (150, 540, "BANCO FICTICIO")],
])
def test_forma_de_pago_con_renglones_de_continuacion(valor):
    """Antes: «60 DÍAS F.F» (sin el punto final de la abreviatura y sin los renglones de debajo)."""
    ops = _cabecera() + _cuerpo() + [(50, 516, "FORMA DE PAGO:", 9, "Helvetica-Bold")] + valor + PIE_PAGO
    _, e = _extraer(_pdf(ops))
    assert e["forma_pago"]["valor"] == "60 DÍAS F.F. TRANSFERENCIA BANCARIA BANCO FICTICIO"
    assert e["forma_pago"]["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_forma_de_pago_no_se_une_al_valor_de_otro_rotulo():
    ops = _cabecera() + _cuerpo() + [(50, 516, "Forma de pago:"), (150, 516, "Transferencia bancaria"), (50, 528, "Banco:"),
                                     (150, 528, "Banco Ficticio"), (50, 540, "Titular:"), (150, 540, "Talleres Ficticios")]
    _, e = _extraer(_pdf(ops))
    assert e["forma_pago"]["valor"] == "Transferencia bancaria"


@pytest.mark.parametrize("renglones, valor", [
    (["Forma de pago: Recibo domiciliado a 60 días f.f.", "Vencimiento: 30/11/2026"], "Recibo domiciliado a 60 días f.f."),
    (["Forma de pago: Contado.", "Gracias por su confianza"], "Contado"),
    (["Forma de pago: Transferencia bancaria", "Rogamos indiquen el número de factura"], "Transferencia bancaria"),
])
def test_forma_de_pago_en_el_renglon_del_rotulo(renglones, valor):
    ops = _cabecera() + _cuerpo() + [(50, 516 + 12 * k, s) for k, s in enumerate(renglones)]
    _, e = _extraer(_pdf(ops))
    assert e["forma_pago"]["valor"] == valor


@pytest.mark.parametrize("valor, esperado", [
    ([(150, 516, "Transferencia bancaria."), (150, 528, "Rogamos indiquen el número de factura en el concepto.")],
     "Transferencia bancaria"),
    ([(150, 516, "Transferencia bancaria"), (150, 528, "BIC FICTESMMXXX"), (150, 540, "IBAN ES00 0000 0000 0000 0000 0000")],
     "Transferencia bancaria"),
    ([(150, 516, "Transferencia bancaria"), (150, 528, "Titular Talleres Ficticios S.L."),
      (150, 540, "ES00 0000 0000 0000 0000 0000")], "Transferencia bancaria"),
    ([(150, 516, "CONTADO"), (150, 528, "OBSERVACIONES"), (150, 540, "ENTREGA EN ALMACÉN IMAGINARIO")], "CONTADO"),
    ([(150, 516, "Recibo domiciliado"), (150, 528, "Vto. 15/11")], "Recibo domiciliado"),
])
def test_forma_de_pago_no_une_lo_que_no_es_del_pago(valor, esperado):
    """Antes: la nota tras un valor acabado en punto, «BIC …» o «Titular …» sin dos puntos, el encabezado
    «OBSERVACIONES» y un vencimiento corto se unían a la forma de pago, a 0,97 y sin aviso."""
    _, e = _extraer(_pdf(_cabecera() + _cuerpo() + [(50, 516, "Forma de pago:")] + valor))
    assert e["forma_pago"]["valor"] == esperado
    assert e["forma_pago"]["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_forma_de_pago_no_une_un_rotulo_de_los_totales():
    """«Portes» bajo el valor, con su importe a la derecha: es una fila de los totales, no la forma de pago."""
    ops = _cabecera() + [
        (50, 300, "Concepto", 9, "Helvetica-Bold"), (540, 300, "Importe", 9, "Helvetica-Bold", "r"),
        (50, 318, "Mantenimiento de instalaciones"), (540, 318, "700,00", 9, None, "r"),
        (300, 360, "Forma de pago:"), (380, 360, "Transferencia"), (380, 372, "Portes"), (540, 372, "15,00", 9, None, "r"),
        (380, 400, "Base imponible"), (540, 400, "715,00", 9, None, "r"), (380, 414, "IVA 21%"), (540, 414, "150,15", 9, None, "r"),
        (380, 428, "Total factura"), (540, 428, "865,15", 9, None, "r")]
    _, e = _extraer(_pdf(ops))
    assert e["forma_pago"]["valor"] == "Transferencia"


@pytest.mark.parametrize("rotulo, valor, siguiente, esperado", [
    ("FORMA DE PAGO: ", "30 DÍAS F.F.", "TRANSFERENCIA BANCARIA", "30 DÍAS F.F. TRANSFERENCIA BANCARIA"),
    ("Forma de pago: ", "Recibo a 60 días f.f.", "Domiciliación en Banco Ficticio",
     "Recibo a 60 días f.f. Domiciliación en Banco Ficticio"),
])
def test_forma_de_pago_en_el_bloque_del_rotulo_con_continuacion(rotulo, valor, siguiente, esperado):
    """Rótulo y valor en un mismo bloque, con la continuación alineada bajo el valor. Antes no se unía: se comparaba con
    el comienzo de la última palabra del rótulo («PAGO:»), que va pegada a los dos puntos."""
    x = 50 + pdfmetrics.stringWidth(rotulo, "Helvetica", 9)
    ops = _cabecera() + _cuerpo() + [(50, 516, rotulo + valor), (x, 528, siguiente),
                                     (50, 540, "IBAN: ES00 0000 0000 0000 0000 0000")]
    _, e = _extraer(_pdf(ops))
    assert e["forma_pago"]["valor"] == esperado
    assert e["forma_pago"]["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_forma_de_pago_con_renglon_unido_ajeno_al_pago_se_confirma():
    """Un renglón en la columna del valor que pasa todas las paradas pero no habla del pago: se une, a confirmar."""
    ops = _cabecera() + _cuerpo() + [(50, 516, "Forma de pago:"), (150, 516, "Transferencia bancaria"),
                                     (150, 528, "Almacén central de Villaficticia")]
    _, e = _extraer(_pdf(ops))
    fp = e["forma_pago"]
    assert fp["valor"] == "Transferencia bancaria Almacén central de Villaficticia"
    assert fp["confianza"] < borradores.UMBRAL_CONFIANZA and fp["avisos"] and "forma_pago" in _dudosos(e)


def test_reglas_que_cruzan_solo_las_lineas_largas():
    """Unidad: en la zona central de la caja de un blanco solo es «línea» la tinta de un segmento recto que la cruza y
    continúa más allá de la caja (en vertical, 1,2 renglones o más; en horizontal, un renglón a cada lado)."""
    import numpy as np
    h = 14                                   # alto del renglón en píxeles
    caja = (100, 100 + h, 200, 204)          # filas y columnas de la caja del blanco
    zona = (102, 109, 201, 203)

    def imagen(*trazos):
        a = np.full((300, 400), 255, dtype=np.uint8)
        for f0, f1, c0, c1 in trazos:
            a[f0:f1, c0:c1] = 0
        return a
    # borde vertical que viene de muy arriba: su columna es línea; la otra columna de la zona, no
    m = texto._reglas_que_cruzan(imagen((40, 114, 202, 203)), 255, zona, caja, h)
    assert m[:, 1].all() and not m[:, 0].any()
    # trazo de una cifra dentro de la caja («1»): no es línea
    assert not texto._reglas_que_cruzan(imagen((101, 112, 202, 203)), 255, zona, caja, h).any()
    # línea horizontal que sigue a ambos lados: su fila es línea; un guion dentro de la caja, no
    m = texto._reglas_que_cruzan(imagen((105, 106, 150, 260)), 255, zona, caja, h)
    assert m[105 - 102].all() and m.sum() == m.shape[1]
    assert not texto._reglas_que_cruzan(imagen((105, 106, 199, 206)), 255, zona, caja, h).any()
