"""Caso 14 (complemento): superposición sobre un original con hueco en la cabecera, sin tapar contenido."""
import io

import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app import pdf_final


def original_con_cabecera_libre() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    W, H = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(15 * mm, H - 75 * mm, "FACTURA Nº PRB-1")
    c.setFont("Helvetica", 10)
    for i, t in enumerate(["Cliente: CONSTRUCTORA FICTICIA DEL SUR S.A.", "Base imponible 850,00", "IVA 21% 178,50", "Total 1.028,50"]):
        c.drawString(15 * mm, H - (85 + 7 * i) * mm, t)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_superposicion_cabecera():
    orig = original_con_cabecera_libre()
    url = "https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=B00000174&numserie=PRB-1&fecha=09-11-2026&importe=1028.50"
    pdf, info = pdf_final.superponer(orig, entorno="pruebas", url_qr=url)
    assert info["estrategia"] == "superposicion"
    assert info["qr"]["lado_mm"] == 32.0 and info["qr"]["zona_blanco_mm"] == 6.0 and info["qr"]["error"] == "M"
    assert info["qr"]["y_sup_mm"] > 230  # próximo al margen superior de un A4 (297 mm)
    assert pdf_final.leer_qrs(pdf, 1)[0] == [url]
    with pdfplumber.open(io.BytesIO(pdf)) as d:
        t = d.pages[0].extract_text()
    for x in ("FACTURA Nº PRB-1", "Total 1.028,50", "QR tributario:", "VERI*FACTU", "ENTORNO DE PRUEBAS"):
        assert x in t


def test_sin_hueco_no_superpone():
    from conftest import pdf as fixture
    assert pdf_final.superponer(fixture("replica_166-26.pdf"), entorno="pruebas", url_qr="https://x") is None
