"""Genera los PDF sintéticos de prueba (tests/fixtures).

IMPORTANTE: el PDF real de referencia («Nº166 PFDI RESIDENCIAL OLMOS 09-26.pdf») no estaba
disponible. "replica_166-26.pdf" es una RÉPLICA SINTÉTICA construida a partir de los datos de
referencia facilitados (importes, número, fecha, certificación, pedido, partida). Los NIF,
domicilios, IBAN y textos de detalle son FICTICIOS. No es el documento original ni debe
remitirse a la AEAT. Los documentos "demo_*" usan empresas totalmente ficticias.

Uso:  python scripts/generar_pdfs_prueba.py [directorio_salida]
"""
from __future__ import annotations

import io
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageFilter
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.nif import completar_cif

AZUL = HexColor("#1F4E79")
GRIS = HexColor("#555555")

NIF_EMISOR_REPLICA = completar_cif("B", "8299131")      # ficticio
NIF_CLIENTE_REPLICA = completar_cif("A", "8775421")     # ficticio
NIF_DEMO_EMISOR = completar_cif("B", "0000017")         # ficticio
NIF_DEMO_CLIENTE = completar_cif("B", "0000025")        # ficticio
NIF_DEMO_PROVEEDOR = completar_cif("B", "0000033")      # ficticio

REPLICA = {
    "titulo": "FACTURA",
    "logo": ("IFA", "INGENIERÍA Y ASOCIADOS"),
    "numero": "166/26",
    "fecha": "25/09/2026",
    "cliente": ["PROMOCIONES FICTICIAS DESARROLLOS INMOBILIARIOS, S.A.", "C/ Ficticia de Pruebas, 1", "28000 MADRID",
                f"C.I.F.: {NIF_CLIENTE_REPLICA[0]}-{NIF_CLIENTE_REPLICA[1:]}"],
    "referencias": [("Obra", "PFDI RESIDENCIAL OLMOS"), ("Certificación nº", "18"), ("Pedido", "PC24-00987"),
                    ("Partida presupuestaria", "G09876543"), ("Periodo facturado", "Septiembre 2026")],
    "lineas": [("Asistencia técnica y coordinación de seguridad y salud en obra", "1,00", "mes", "850,00", "850,00"),
               ("Residencial Olmos. Certificación nº 18 - septiembre 2026", None, None, None, None)],
    "base": "850,00", "iva_pct": "21", "cuota": "178,50", "total": "1.028,50",
    "retencion": None,
    "pago": ["Forma de pago: transferencia bancaria a 60 días fecha factura.",
             "IBAN: ES00 0000 0000 0000 0000 0000 (ficticio)"],
    "pie": ["INGENIERÍA FICTICIA Y ASOCIADOS S.L.U. - C.I.F. " + f"{NIF_EMISOR_REPLICA[0]}-{NIF_EMISOR_REPLICA[1:]}",
            "C/ Inventada de Pruebas, 99 - 28000 Madrid - Tel. 900 000 000",
            "Inscrita en el Registro Mercantil de Madrid, Tomo 0000, Folio 00, Hoja M-000000"],
    "nota": "RÉPLICA SINTÉTICA PARA PRUEBAS - NO ES EL DOCUMENTO ORIGINAL",
}


def demo(titulo="FACTURA PROFORMA", numero="PRB-0001", fecha="02/11/2026", total="1.028,50", retencion=None,
         emisor=None, cliente=None, base="850,00", cuota="178,50", nota=None):
    emisor = emisor or ("INGENIERÍA DEMO NORTE S.L.", NIF_DEMO_EMISOR)
    cliente = cliente or ("CONSTRUCTORA FICTICIA DEL SUR S.A.", NIF_DEMO_CLIENTE)
    return {
        "titulo": titulo, "logo": ("IDN", "INGENIERÍA DEMO NORTE"), "numero": numero, "fecha": fecha,
        "cliente": [cliente[0], "Avda. de los Datos Sintéticos, 7", "41000 SEVILLA", f"NIF: {cliente[1]}"],
        "referencias": [("Obra", "EDIFICIO DEMOSTRACIÓN 12 VIVIENDAS"), ("Certificación nº", "3"),
                        ("Pedido", "PED-DEMO-0042"), ("Partida presupuestaria", "P0000001"),
                        ("Periodo facturado", "Octubre 2026")],
        "lineas": [("Dirección de ejecución de obra", "1,00", "mes", "600,00", "600,00"),
                   ("Visitas de control de calidad", "2,00", "ud", "125,00", "250,00")],
        "base": base, "iva_pct": "21", "cuota": cuota, "total": total, "retencion": retencion,
        "pago": ["Forma de pago: transferencia a 30 días.", "IBAN: ES00 0000 0000 0000 0000 0000 (ficticio)"],
        "pie": [f"{emisor[0]} - NIF {emisor[1]}", "C/ Ejemplo Sintético, 1 - 48000 Bilbao",
                "Inscrita en el Registro Mercantil (datos ficticios)"],
        "nota": nota or "DOCUMENTO SINTÉTICO DE DEMOSTRACIÓN",
    }


def dibujar(d: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"{d['titulo']} {d['numero']}")
    c.setSubject(d.get("nota", ""))
    W, H = A4
    # Identidad visual: bloque de logotipo
    c.setFillColor(AZUL)
    c.roundRect(18 * mm, H - 38 * mm, 42 * mm, 22 * mm, 3 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(39 * mm, H - 27 * mm, d["logo"][0])
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(39 * mm, H - 33 * mm, d["logo"][1])
    # Título y datos del documento
    c.setFillColor(AZUL)
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(W - 18 * mm, H - 25 * mm, d["titulo"])
    c.setFillColor(GRIS)
    c.setFont("Helvetica", 10)
    c.drawRightString(W - 18 * mm, H - 33 * mm, f"Nº: {d['numero']}")
    c.drawRightString(W - 18 * mm, H - 39 * mm, f"Fecha: {d['fecha']}")
    # Cliente
    y = H - 55 * mm
    c.setStrokeColor(AZUL)
    c.rect(105 * mm, y - 25 * mm, 87 * mm, 30 * mm, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(AZUL)
    c.drawString(108 * mm, y + 1 * mm, "CLIENTE")
    c.setFillColor(HexColor("#000000"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(108 * mm, y - 5 * mm, d["cliente"][0])
    c.setFont("Helvetica", 9)
    for i, t in enumerate(d["cliente"][1:]):
        c.drawString(108 * mm, y - (10 + 5 * i) * mm, t)
    # Referencias de obra
    y = H - 62 * mm
    c.setFont("Helvetica", 9)
    for etq, val in d["referencias"]:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(18 * mm, y, f"{etq}:")
        c.setFont("Helvetica", 9)
        c.drawString(58 * mm, y, val)
        y -= 5.5 * mm
    # Tabla de conceptos
    y = H - 100 * mm
    c.setFillColor(AZUL)
    c.rect(18 * mm, y - 2 * mm, W - 36 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "CONCEPTO")
    c.drawRightString(128 * mm, y, "CANTIDAD")
    c.drawString(131 * mm, y, "UD.")
    c.drawRightString(165 * mm, y, "PRECIO")
    c.drawRightString(W - 20 * mm, y, "IMPORTE")
    c.setFillColor(HexColor("#000000"))
    c.setFont("Helvetica", 9)
    y -= 9 * mm
    for concepto, cant, ud, precio, imp in d["lineas"]:
        c.drawString(20 * mm, y, concepto)
        if cant:
            c.drawRightString(128 * mm, y, cant)
            c.drawString(131 * mm, y, ud)
            c.drawRightString(165 * mm, y, precio)
            c.drawRightString(W - 20 * mm, y, imp)
        y -= 6 * mm
    c.setStrokeColor(GRIS)
    c.line(18 * mm, y, W - 18 * mm, y)
    # Totales
    y -= 10 * mm
    filas = [("BASE IMPONIBLE", d["base"]), (f"I.V.A. {d['iva_pct']}%", d["cuota"]), ("TOTAL FACTURA", d["total"])]
    if d.get("retencion"):
        filas.insert(2, (f"RETENCIÓN I.R.P.F. {d['retencion'][0]}%", "-" + d["retencion"][1]))
        filas[-1] = ("TOTAL A PAGAR", d["total"])
    for etq, val in filas:
        bold = etq.startswith("TOTAL")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        c.drawRightString(160 * mm, y, etq)
        c.drawRightString(W - 20 * mm, y, val + " €")
        y -= 6 * mm
    # Pago
    y -= 6 * mm
    c.setFont("Helvetica", 8.5)
    for t in d["pago"]:
        c.drawString(18 * mm, y, t)
        y -= 4.5 * mm
    # Pie con los datos fiscales del emisor
    c.setStrokeColor(AZUL)
    c.line(18 * mm, 24 * mm, W - 18 * mm, 24 * mm)
    c.setFont("Helvetica", 7)
    c.setFillColor(GRIS)
    for i, t in enumerate(d["pie"]):
        c.drawCentredString(W / 2, (20 - 3.5 * i) * mm, t)
    if d.get("nota"):
        c.setFont("Helvetica-Oblique", 6)
        c.drawString(18 * mm, 6 * mm, d["nota"])
    c.showPage()
    c.save()
    return buf.getvalue()


def escanear(pdf: bytes, semilla: int = 7) -> bytes:
    """Simula un escaneo: rasteriza, gira ligeramente, añade ruido y guarda solo imagen."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "e.pdf").write_bytes(pdf)
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", str(Path(td) / "e.pdf"), str(Path(td) / "p")], check=True)
        img = Image.open(sorted(Path(td).glob("p*.png"))[0]).convert("L")
        img = img.rotate(0.4, expand=False, fillcolor=255, resample=Image.BICUBIC)
        rnd = random.Random(semilla)
        px = img.load()
        for _ in range(4000):
            x, y = rnd.randrange(img.width), rnd.randrange(img.height)
            px[x, y] = rnd.choice((0, 255, 200))
        img = img.filter(ImageFilter.GaussianBlur(0.3))
        out = io.BytesIO()
        img.save(out, "PDF", resolution=200.0)
        return out.getvalue()


def main():
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    dest.mkdir(parents=True, exist_ok=True)
    rep = dibujar(REPLICA)
    (dest / "replica_166-26.pdf").write_bytes(rep)
    (dest / "replica_166-26_escaneada.pdf").write_bytes(escanear(rep))
    (dest / "demo_proforma.pdf").write_bytes(dibujar(demo()))
    (dest / "demo_borrador_factura.pdf").write_bytes(dibujar(demo(titulo="FACTURA", numero="PRB-0002")))
    (dest / "demo_descuadre.pdf").write_bytes(dibujar(demo(titulo="FACTURA", numero="PRB-0003", total="1.030,50")))
    (dest / "demo_retencion.pdf").write_bytes(dibujar(demo(titulo="FACTURA", numero="PRB-0004", total="901,00",
                                                         retencion=("15", "127,50"))))
    (dest / "demo_recibida.pdf").write_bytes(dibujar(demo(
        titulo="FACTURA", numero="A-2026-881", emisor=("SUMINISTROS FICTICIOS S.L.", NIF_DEMO_PROVEEDOR),
        cliente=("INGENIERÍA DEMO NORTE S.L.", NIF_DEMO_EMISOR))))
    print("Generados en", dest)
    print("NIF ficticios:", {"emisor_replica": NIF_EMISOR_REPLICA, "cliente_replica": NIF_CLIENTE_REPLICA,
                             "demo_emisor": NIF_DEMO_EMISOR, "demo_cliente": NIF_DEMO_CLIENTE,
                             "demo_proveedor": NIF_DEMO_PROVEEDOR})


if __name__ == "__main__":
    main()
