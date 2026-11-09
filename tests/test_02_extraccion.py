"""Casos 1, 2, 3 y 4: extracción de la factura de referencia (réplica sintética), emisor en el pie,
referencias conservadas y detección de datos dudosos, ausentes o descuadrados."""
from conftest import pdf

from app import borradores
from app.extraccion import campos, texto


def extraer(nombre, nif_empresa=None):
    return campos.Extractor(texto.leer(pdf(nombre)), nombre, nif_empresa).extraer()


def test_caso1_importes_referencia():
    e = extraer("replica_166-26.pdf")
    assert e["numero"]["valor"] == "166/26"
    assert e["fecha"]["valor"] == "2026-09-25"
    assert e["base_imponible"]["valor"] == "850.00"
    assert e["cuota_iva"]["valor"] == "178.50"
    assert e["tipo_iva"]["valor"] == "21.00"
    assert e["total_factura"]["valor"] == "1028.50"
    # Procedencia: página y posición de cada campo
    for k in ("numero", "base_imponible", "cuota_iva", "total_factura"):
        assert e[k]["pagina"] == 1 and len(e[k]["bbox"]) == 4 and e[k]["metodo"] == "texto_pdf"


def test_caso2_emisor_en_pie_y_destinatario():
    e = extraer("replica_166-26.pdf")
    assert e["emisor"]["nombre"]["valor"] == "INGENIERÍA FICTICIA Y ASOCIADOS S.L.U."
    assert e["emisor"]["nif"]["valor"] == "B82991316"
    assert "pie de página" in " ".join(e["emisor"]["nif"]["avisos"])
    assert e["destinatario"]["nombre"]["valor"] == "PROMOCIONES FICTICIAS DESARROLLOS INMOBILIARIOS, S.A."
    assert e["destinatario"]["nif"]["valor"] == "A87754214"


def test_caso3_referencias_obra():
    e = extraer("replica_166-26.pdf")
    r = {k: v["valor"] for k, v in e["referencias"].items()}
    assert r["certificacion"] == "18"
    assert r["pedido"] == "PC24-00987"
    assert r["partida_presupuestaria"] == "G09876543"
    assert r["obra"] == "PFDI RESIDENCIAL OLMOS"
    assert "60 días" in e["forma_pago"]["valor"]
    assert e["lineas"][0]["unidad"]["valor"] == "mes" and e["lineas"][0]["importe"]["valor"] == "850.00"


def test_caso4_ocr_dudoso_y_ausentes():
    e = extraer("replica_166-26_escaneada.pdf")
    assert e["paginas"][0]["metodo"] == "ocr"
    assert e["total_factura"]["valor"] == "1028.50"   # se lee, pero con confianza OCR
    dudosos = borradores.campos_dudosos(e)
    assert dudosos, "Deben señalarse campos de lectura dudosa"
    # El NIF del pie: o se lee bien, o (si el OCR lo confunde) se señala como no válido; nunca se inventa otro
    assert e["emisor"]["nif"]["valor"] in ("B82991316", None)
    if e["emisor"]["nif"]["valor"] is None:
        assert any(n["texto"].startswith("B") for n in e["nifs_no_validos"])


def test_nif_mal_leido_por_ocr_no_se_inventa():
    """Un NIF con el dígito de control mal leído se señala como no válido y no se usa como NIF del emisor."""
    from app.extraccion import campos
    from app.extraccion.texto import Pagina, Palabra

    def renglon(texto, x, y):
        out = []
        for w in texto.split():
            out.append(Palabra(w, x, y, x + 6 * len(w), y + 9, 0.6, "ocr"))
            x += 6 * len(w) + 4
        return out
    pals = renglon("FACTURA", 400, 60) + renglon("Nº factura: 7/26", 400, 80) + renglon("Fecha: 03/11/2026", 400, 95) + \
        renglon("Cliente:", 40, 140) + renglon("Cliente Ejemplo S.L.", 40, 155) + renglon("NIF: A87754214", 40, 170) + \
        renglon("Total factura 1.028,50", 350, 600) + renglon("INGENIERIA FICTICIA S.L.U. - C.I.F. B-82991317", 40, 800)
    e = campos.Extractor([Pagina(1, 595, 842, pals, "ocr", 0.6)], "x.pdf", None).extraer()
    assert any(n["texto"] == "B82991317" for n in e["nifs_no_validos"])
    assert "B82991317" not in e["nifs_detectados"]
    assert e["emisor"]["nif"]["valor"] != "B82991317" and e["destinatario"]["nif"]["valor"] != "B82991317"


def test_clasificacion():
    from app.extraccion.inspeccion import inspeccionar
    emp = {"nif": "B00000174"}
    r = borradores.clasificar(extraer("demo_proforma.pdf"), inspeccionar(pdf("demo_proforma.pdf")) | {"qr": []}, emp)
    assert r["sugerida"] == "preparatorio"
    r = borradores.clasificar(extraer("demo_recibida.pdf"), {"qr": []}, emp)
    assert r["sugerida"] == "recibida"
    r = borradores.clasificar(extraer("demo_borrador_factura.pdf"), {"qr": []}, emp)
    assert r["sugerida"] == "pendiente_confirmar"
    # Un documento con QR tributario ya es una factura expedida
    qr = [{"tributario": True, "modalidad": "verifactu", "entorno": "produccion", "nif": "B00000174", "numserie": "X"}]
    assert borradores.clasificar(extraer("demo_borrador_factura.pdf"), {"qr": qr}, emp)["sugerida"] == "emitida_propia"


def test_inspeccion_contenido_activo(tmp_path):
    """Un PDF con JavaScript se detecta y nunca se ejecuta (solo se rasteriza en el servidor)."""
    from pypdf import PdfReader, PdfWriter
    from app.extraccion.inspeccion import inspeccionar
    import io
    w = PdfWriter()
    w.append(PdfReader(io.BytesIO(pdf("demo_proforma.pdf"))))
    w.add_js("app.alert('hola');")
    out = io.BytesIO()
    w.write(out)
    i = inspeccionar(out.getvalue())
    assert "javascript" in i["contenido_activo"]
