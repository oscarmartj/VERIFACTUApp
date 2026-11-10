"""Regresiones de la prueba con una factura real del usuario (exportada de una hoja de cálculo). La factura no se
publica: estas pruebas reproducen cada causa con documentos sintéticos.

- Un tabulador dentro de una celda se exporta como el código 9 de una fuente TrueType sin nombre de glifo: pdfminer lo
  da como «(cid:9)» y acababa al final de la referencia de obra (y en el PDF compuesto).
- El recorte del logotipo se llevaba el borde y el texto del recuadro del cliente cuando este empieza antes de la mitad
  de la página.
"""
import io
from pathlib import Path

import numpy as np
import pdfplumber
import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import pdf_final
from app.extraccion import texto

FUENTE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _pdf_truetype_con_tabulador() -> bytes:
    """Una línea con una fuente TrueType incrustada, WinAnsiEncoding y /FirstChar 9: el byte 9 (tabulador) se dibuja con
    el glifo .notdef, como en las exportaciones de hojas de cálculo."""
    ttf = FUENTE.read_bytes()
    cont = b"BT /F1 10 Tf 20 50 Td (Obra Residencial Demo\"\x09) Tj ET\nBT /F1 10 Tf 20 30 Td (Pedido PC00-001) Tj ET"
    anchos = b"[" + b" ".join(b"600" for _ in range(9, 256)) + b"]"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>",
            b"<< /Type /Font /Subtype /TrueType /BaseFont /DejaVuSans /Encoding /WinAnsiEncoding /FirstChar 9 /LastChar 255 "
            b"/Widths " + anchos + b" /FontDescriptor 6 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cont) + cont + b"\nendstream",
            b"<< /Type /FontDescriptor /FontName /DejaVuSans /Flags 32 /FontBBox [-1021 -463 1793 1232] /ItalicAngle 0 "
            b"/Ascent 928 /Descent -236 /CapHeight 729 /StemV 80 /FontFile2 7 0 R >>",
            b"<< /Length %d /Length1 %d >>\nstream\n" % (len(ttf), len(ttf)) + ttf + b"\nendstream"]
    out, offs = bytearray(b"%PDF-1.5\n"), []
    for k, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % k + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offs)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


@pytest.mark.skipif(not FUENTE.exists(), reason="sin la fuente DejaVuSans")
def test_tabulador_de_fuente_truetype_no_queda_como_cid():
    datos = _pdf_truetype_con_tabulador()
    with pdfplumber.open(io.BytesIO(datos)) as pdf:   # la causa: pdfminer lo da como «(cid:9)»
        assert "(cid:9)" in [c["text"] for c in pdf.pages[0].chars]
    from app.extraccion import texto_proceso
    with pdfplumber.open(io.BytesIO(datos)) as pdf:   # lectura del texto nativo (la de texto.leer, en el proceso hijo)
        texto_proceso._leer_ocg(pdf)
        palabras = [w[0] for w in texto_proceso._leer_pagina(pdf.pages[0], 1)["palabras"]]
    assert not any("cid" in p for p in palabras), palabras
    assert 'Demo"' in palabras and "PC00-001" in palabras
    # y en la lectura completa (aquí acaba en OCR porque la fuente de prueba no casa con su codificación)
    assert not any("cid" in w.texto for w in texto.leer(datos)[0].palabras)


def test_recorte_del_logotipo_no_incluye_el_recuadro_del_cliente():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    w, h = A4
    c.setFillGray(0.3)
    c.rect(60, h - 75, 40, 30, fill=1, stroke=0)                          # «logotipo»
    c.setFont("Helvetica-Bold", 14)
    c.drawString(105, h - 60, "EMPRESA DEMO")
    c.setFont("Helvetica", 7)
    c.drawString(60, h - 95, "Calle Inventada 1, 28000 Madrid")
    c.roundRect(0.46 * w, h - 130, 0.42 * w, 80, 10, stroke=1, fill=0)   # recuadro del cliente desde el 46 % del ancho
    c.drawString(0.48 * w, h - 70, "CLIENTE DEMO, S.A.")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.40 * w, h - 170, "FACTURA")
    c.showPage()
    c.save()
    png = pdf_final.recortar_logo(buf.getvalue(), None)
    assert png is not None
    img = Image.open(io.BytesIO(png))
    # el recorte termina antes del recuadro: logotipo y nombre ocupan hasta x ≈ 215 pt; el recuadro empieza en 274 pt
    ancho_pt = img.width / (pdf_final_dpi(buf.getvalue()) / 72)
    assert ancho_pt < 0.46 * w - 60, ancho_pt
    assert np.asarray(img.convert("L")).min() < 128   # el logotipo está


def pdf_final_dpi(datos: bytes) -> int:
    from pypdf import PdfReader
    p1 = PdfReader(io.BytesIO(datos)).pages[0]
    lado = max(float(p1.mediabox.width), float(p1.mediabox.height))
    return max(30, min(200, int(2400 * 72 / max(lado, 1))))
