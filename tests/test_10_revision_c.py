"""Regresiones de la revisión de robustez (carril C): coste acotado de la extracción frente a PDF hostiles, texto que
el revisor no ve y memoria del preprocesado del OCR. Datos sintéticos y ficticios; todo local."""
import io
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import RAIZ, pdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, extraccion_proceso, inspeccion, texto
from app.extraccion.texto import Linea, Pagina, Palabra

LIMITE_CPU_S = 20   # holgado: con el código corregido cada caso tarda alrededor de un segundo


def _cpu(f):
    t = time.process_time()
    r = f()
    return r, time.process_time() - t


# --- Coste acotado (F1, F2, F3, F4) -------------------------------------------------------------------------------

def _pdf_escalera(n, paso=2.2, fs=4.0):
    alto = 40 + n * paso
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, alto))
    c.setFont("Helvetica", fs)
    for i in range(n):
        c.drawString(10, alto - 10 - fs - i * paso, "IVA abc def")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_renglones_en_escalera_no_forman_una_fila_ni_disparan_el_coste():
    datos = _pdf_escalera(1600)
    inspeccion.inspeccionar(datos)
    pags = texto.leer(datos)
    (ex, ext), cpu = _cpu(lambda: (lambda ex: (ex, ex.extraer()))(campos.Extractor(pags, "x.pdf", None)))
    assert cpu < LIMITE_CPU_S
    assert len(ex.lineas) == 1600
    assert len(ex.filas) >= 1600 // 8   # antes: una sola fila con los 1600 renglones (coste cúbico, ~5 min)
    for f in ex.filas:
        assert f.bottom - f.top <= campos.MAX_ALTO_FILA * max(b.bottom - b.top for b in f.bloques) + 1e-6


def test_etiqueta_repetida_en_renglones_largos(monkeypatch):
    # renglones larguísimos de letra pequeña (de menos de texto_proceso.CUERPO_MIN_PT, o sin tinta distinguible en la
    # imagen, la página iría al OCR); aquí se mide la extracción del texto nativo
    monkeypatch.setattr(texto, "_marcar_sin_tinta", lambda *a, **k: None)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(16500, 842))
    c.setFont("Helvetica", 3.2)
    for r in range(5):
        c.drawString(5, 742 - r * 12, " ".join(["Fecha"] * 1600))
    c.showPage()
    c.save()
    pags = texto.leer(buf.getvalue())
    assert sum(len(p.palabras) for p in pags) == 8000
    _, cpu = _cpu(lambda: campos.Extractor(pags, "x.pdf", None).extraer())
    assert cpu < LIMITE_CPU_S   # antes: más de 15 minutos


def _palabras_rango_referencia(l, a, b):
    out, k = [], 0
    for p in l.palabras:
        ini = l.texto.find(p.texto, k)
        fin = ini + len(p.texto)
        if fin > a and (b is None or ini < b):
            out.append(p)
        k = fin
    return out


def test_palabras_de_un_tramo_iguales_a_la_busqueda_lineal():
    rnd = random.Random(7)
    voc = ["Fecha", "Total", "a", "IVA", "21%", "1.234,56", "Fecha:", "aa", "a a", ""]
    for _ in range(300):
        pals = [Palabra(rnd.choice(voc), i * 10.0, 0, i * 10.0 + 8, 9) for i in range(rnd.randint(1, 12))]
        l = Linea(1, pals)
        n = len(l.texto)
        for _ in range(10):
            a = rnd.randint(-1, n + 1)
            b = rnd.randint(a, n + 2)
            assert campos.Extractor._palabras_desde(l, a) == _palabras_rango_referencia(l, a, None)
            assert campos.Extractor._palabras_rango(l, a, b) == _palabras_rango_referencia(l, a, b)


def _pdf_tounicode(destino: str, contenido: bytes) -> bytes:
    """PDF mínimo cuya fuente F1 traduce el glifo «A» (/ToUnicode) a la cadena `destino`."""
    hexdst = "".join(f"{ord(ch):04X}" for ch in destino)
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /X def /CMapType 2 def\n"
            b"1 begincodespacerange <00> <FF> endcodespacerange\n"
            b"1 beginbfchar <41> <" + hexdst.encode() + b"> endbfchar\n"
            b"endcmap CMapName currentdict /CMap defineresource pop end end\n")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R /F2 7 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"endstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def test_glifo_con_racha_enorme_de_espacios():
    contenido = (b"BT /F2 10 Tf 50 820 Td (Factura de prueba con texto suficiente para leerla sin OCR) Tj ET "
                 b"BT /F2 10 Tf 50 800 Td (CIF: B12345674 - ) Tj /F1 10 Tf (A) Tj ET")
    datos = _pdf_tounicode("z SA" + " " * 40_000 + "z", contenido)
    inspeccion.inspeccionar(datos)
    pags = texto.leer(datos)
    textos = [p.texto for p in pags[0].palabras]
    assert "z SA z" in textos and not any("  " in t for t in textos)   # espacios internos reducidos a uno
    _, cpu = _cpu(lambda: campos.Extractor(pags, "x.pdf", None).extraer())
    assert cpu < LIMITE_CPU_S


def test_limite_de_texto_por_pagina_cuenta_caracteres_y_no_glifos():
    datos = _pdf_tounicode("x" * 25_000, b"BT /F1 10 Tf 50 800 Td (AAA) Tj ET")   # 3 glifos, 75.000 caracteres
    with pytest.raises(inspeccion.PDFNoValido, match="demasiado texto"):
        texto.leer(datos)


def test_expresiones_lineales_con_rachas_de_espacios():
    sep_antiguo = re.compile(r"\s+[·|•–-]\s+|\s*[·|•]\s*")
    rnd = random.Random(3)
    for _ in range(2000):
        s = "".join(rnd.choice(["a", " ", "  ", "-", "·", "|", "•", "–", "\t", "b"]) for _ in range(rnd.randint(0, 25)))
        assert campos.Extractor.SEP_PIE.split(s) == sep_antiguo.split(s)
    forma_antigua = re.compile(campos.RE_FORMA_JURIDICA.pattern.replace(r"\s*+[.,]?\s*+$", r"\s*[.,]?\s*$"), re.I)
    for s in ["Talleres Ejemplo S.L.", "Talleres Ejemplo, S.L. .", "Comercial Norte SA ,", "Sin forma", "X S. L ", "S.A"]:
        assert bool(campos.RE_FORMA_JURIDICA.search(s)) == bool(forma_antigua.search(s))
    t = time.process_time()
    campos.Extractor.SEP_PIE.split("a" + " " * 50_000 + "b")
    campos.RE_FORMA_JURIDICA.search("SA" + " " * 50_000 + "x")
    assert time.process_time() - t < 2   # antes: 15 s y 1 s


def _paginas_densas(n_paginas, renglones=2400, por_renglon=6):
    pags = []
    for n in range(1, n_paginas + 1):
        pals = []
        for r in range(renglones):
            top = 5 + r * 2.1
            pals += [Palabra("ab", 5 + k * 5.0, top, 7.5 + k * 5.0, top + 1.6) for k in range(por_renglon)]
        pags.append(Pagina(n, 595, 5000, pals))
    return pags


def test_documento_denso_de_muchas_paginas():
    pags = _paginas_densas(10)
    (ex, _), cpu = _cpu(lambda: (lambda ex: (ex, ex.extraer()))(campos.Extractor(pags, "x.pdf", None)))
    assert cpu < LIMITE_CPU_S   # antes: varios minutos (agrupación cuadrática en palabras y renglones)
    assert len(ex.lineas) == 10 * 2400


def _lineas_referencia(paginas, tolerancia=3.0):
    """Agrupación en renglones del código anterior (cuadrática), para comprobar que la nueva da lo mismo."""
    out = []
    for pg in paginas:
        pal = sorted(pg.palabras, key=lambda p: (round(p.top), p.x0))
        grupos = []
        for p in pal:
            for g in grupos:
                ref = g[0]
                dc = abs(((p.top + p.bottom) / 2) - ((ref.top + ref.bottom) / 2))
                hp, hr = p.bottom - p.top, ref.bottom - ref.top
                cerca = dc <= tolerancia + 0.25 * min(hp, hr) or (abs(p.bottom - ref.bottom) <= 1.5 and dc <= tolerancia + 0.25 * max(hp, hr))
                if cerca and all(min(p.bottom, q.bottom) - max(p.top, q.top) >= 0.5 * min(hp, q.bottom - q.top) for q in g):
                    g.append(p)
                    break
            else:
                grupos.append([p])
        out.append([[id(p) for p in g] for g in grupos])
    return out


def test_agrupacion_en_renglones_igual_que_la_cuadratica():
    rnd = random.Random(11)
    for _ in range(80):
        pals = []
        for _ in range(rnd.randint(1, 150)):
            top = rnd.uniform(0, 200)
            h = rnd.choice([0.0, 1.5, 8, 9, 10, 12, 30, rnd.uniform(0, 60)])
            x0 = rnd.uniform(0, 5)
            pals.append(Palabra("w", x0, top, x0 + 400, top + h))   # solapadas en horizontal: un bloque por grupo
        pg = Pagina(1, 595, 842, pals)
        ref = sorted(sorted(g) for g in _lineas_referencia([pg])[0])
        assert sorted(sorted(id(p) for p in l.palabras) for l in texto.lineas([pg])) == ref


# --- Texto que el revisor no ve (F5) ----------------------------------------------------------------------------

LINEAS_FACTURA = [(50, 790, "Talleres Ejemplo S.L."), (50, 776, "CIF: B12345674"), (50, 762, "Calle Mayor 1, 28001 Madrid"),
                  (330, 720, "Cliente: Comercial Norte S.A."), (330, 706, "NIF: A28000008"),
                  (50, 660, "FACTURA"), (50, 640, "Factura nº: F-2024-001"), (50, 626, "Fecha: 01/02/2024"),
                  (50, 560, "Concepto"), (450, 560, "Importe"), (50, 540, "Reparación de maquinaria"), (450, 540, "{base}"),
                  (330, 480, "Base imponible"), (330, 466, "IVA 21%"), (330, 452, "Total factura")]
TOTALES = [(480, "{base}"), (466, "{cuota}"), (452, "{total}")]
VISIBLE = dict(base="100,00", cuota="21,00", total="121,00")
OCULTO = dict(base="1.000,00", cuota="210,00", total="1.210,00")
CAMPOS_TOTALES = ("base_imponible", "cuota_iva", "total_factura")


def _factura(antes=None, color_totales=(0, 0, 0), despues=None, vals=VISIBLE, modo=0, imagen=None, totales=True):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if imagen is not None:
        c.drawImage(ImageReader(imagen), 0, 0, width=A4[0], height=A4[1])
    if antes:
        antes(c)
    t = c.beginText()
    t.setTextRenderMode(modo)
    t.setFont("Helvetica", 10)
    for x, y, s in LINEAS_FACTURA:
        t.setTextOrigin(x, y)
        t.textOut(s.format(**vals))
    c.drawText(t)
    if totales:
        c.setFillColorRGB(*color_totales)
        t = c.beginText()
        t.setTextRenderMode(modo)
        t.setFont("Helvetica", 10)
        for y, s in TOTALES:
            t.setTextOrigin(450, y)
            t.textOut(s.format(**vals))
        c.drawText(t)
        c.setFillColorRGB(0, 0, 0)
    if despues:
        despues(c)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos):
    pags = texto.leer(datos)
    return pags, campos.Extractor(pags, "x.pdf", "B12345674").extraer()


def _dudosos(e):
    return {d["campo"] for d in borradores.campos_dudosos(e)}


def test_texto_normal_sigue_con_confianza_alta():
    pags, e = _extraer(_factura())
    assert pags[0].metodo == "texto_pdf" and pags[0].texto_oculto == 0
    for k in CAMPOS_TOTALES:
        assert e[k]["confianza"] == 0.97 and not e[k]["avisos"]
    assert not _dudosos(e) & set(CAMPOS_TOTALES) and "texto_oculto" not in e["paginas"][0]


def test_blanco_sobre_blanco_obliga_a_confirmar():
    _, e = _extraer(_factura(color_totales=(1, 1, 1)))
    assert {e[k]["valor"] for k in CAMPOS_TOTALES} == {"100.00", "21.00", "121.00"}
    assert set(CAMPOS_TOTALES) <= _dudosos(e)
    assert all(e[k]["confianza"] <= texto.CONF_TEXTO_DUDOSO for k in CAMPOS_TOTALES)
    assert any("puede no verse" in a for a in e["total_factura"]["avisos"])


def test_blanco_sobre_banda_oscura_es_visible():
    def banda(c):
        c.setFillColorRGB(0.1, 0.2, 0.4)
        c.rect(440, 445, 100, 50, fill=1, stroke=0)
    _, e = _extraer(_factura(antes=banda, color_totales=(1, 1, 1)))
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES)
    assert not _dudosos(e) & set(CAMPOS_TOTALES)


def test_importes_tapados_por_un_recuadro_opaco():
    def tapa(c):
        c.setFillColorRGB(1, 1, 1)
        c.rect(440, 445, 100, 50, fill=1, stroke=0)
    _, e = _extraer(_factura(despues=tapa))
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


def test_texto_transparente_se_ignora_y_un_resaltado_translucido_no_tapa():
    def transparente(c):
        c.saveState()
        c.setFillAlpha(0)
        c.setFont("Helvetica", 10)
        for y, s in TOTALES:
            c.drawString(450, y, s.format(**OCULTO))
        c.restoreState()
    pags, e = _extraer(_factura(totales=False, despues=transparente))
    assert not {p.texto for p in pags[0].palabras} & {"1.000,00", "210,00", "1.210,00"}
    assert pags[0].texto_oculto == len("1.000,00210,001.210,00")

    def resaltado(c):
        c.saveState()
        c.setFillAlpha(0.3)
        c.setFillColorRGB(1, 1, 0)
        c.rect(440, 445, 100, 50, fill=1, stroke=0)
        c.restoreState()
    _, e = _extraer(_factura(despues=resaltado))
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES)


def test_texto_que_solo_recorta_obliga_a_confirmar():
    def recorte(c):   # importes en modo 7 (solo recorte): no se pintan
        t = c.beginText()
        t.setTextRenderMode(7)
        t.setFont("Helvetica", 10)
        for y, s in TOTALES:
            t.setTextOrigin(450, y)
            t.textOut(s.format(**VISIBLE))
        c.drawText(t)
    _, e = _extraer(_factura(totales=False, despues=recorte))
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


def test_importes_bajo_una_anotacion_visible():
    _, e = _extraer(_factura(despues=lambda c: c.textAnnotation("nota", Rect=(440, 445, 540, 495))))
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


def test_texto_fuera_de_la_pagina_o_del_recorte_se_ignora():
    def fuera(c):
        c.drawString(450, 900, "999,00")      # por encima de la página
        c.drawString(-300, 466, "888,00")     # a la izquierda
        c.drawString(450, 20, "777,00")       # dentro del MediaBox pero fuera del CropBox
        c.setCropBox((0, 100, 595, 842))
    pags, e = _extraer(_factura(totales=False, despues=fuera))
    textos = {p.texto for p in pags[0].palabras}
    assert not textos & {"999,00", "888,00", "777,00"}
    assert pags[0].texto_oculto == 18 and e["paginas"][0]["texto_oculto"] == 18
    assert all(e[k]["valor"] is None for k in CAMPOS_TOTALES)


def test_capa_invisible_sobre_imagen_se_lee_por_ocr_lo_que_se_ve():
    """Escaneo «con capa de texto» (o PDF manipulado): la imagen muestra unos importes y la capa invisible otros."""
    with tempfile.TemporaryDirectory() as td:
        png = inspeccion.rasterizar(_factura(), Path(td), dpi=200)[0]
        datos = _factura(vals=OCULTO, modo=3, imagen=str(png))
    pags, e = _extraer(datos)
    assert pags[0].metodo == "ocr" and pags[0].texto_oculto > 100
    assert not any(p.texto in ("1.000,00", "210,00", "1.210,00") for p in pags[0].palabras)
    assert e["base_imponible"]["valor"] == "100.00"
    assert e["total_factura"]["valor"] == "121.00"
    assert e["total_factura"]["metodo"] == "ocr"


# --- Extracción aislada (defensa en profundidad de F1-F4) ---------------------------------------------------------

def test_extraccion_en_proceso_hijo_igual_que_local():
    pags = texto.leer(pdf("replica_166-26.pdf"))
    import json
    local = json.loads(json.dumps(extraccion_proceso._extraer_local(pags, "replica_166-26.pdf", "B82991316")))
    assert extraccion_proceso.extraer(pags, "replica_166-26.pdf", "B82991316") == local
    assert local["_lineas_cabecera"]


def test_extraccion_que_excede_el_tiempo_se_rechaza(monkeypatch):
    pags = texto.leer(pdf("replica_166-26.pdf"))
    monkeypatch.setattr(extraccion_proceso, "TIEMPO_MAX_S", 0)
    with pytest.raises(inspeccion.PDFNoValido):
        extraccion_proceso.extraer(pags, "x.pdf", None)


# --- Memoria del preprocesado del OCR (F8) ------------------------------------------------------------------------

def _sin_lineas_referencia(g):
    """Versión anterior (matrices completas int32/int64), para comprobar que la nueva da exactamente lo mismo."""
    import numpy as np
    from PIL import Image
    a = np.asarray(g)
    tinta = (a < 200).astype(np.int32)
    largo = max(40, g.width // 30)

    def cubiertos(m, eje):
        c = np.cumsum(m, axis=eje)
        c = np.concatenate([np.zeros_like(c.take([0], axis=eje)), c], axis=eje)
        n = m.shape[eje]
        if n <= largo:
            return np.zeros(m.shape, dtype=bool)
        llenas = (c.take(range(largo, n + 1), axis=eje) - c.take(range(0, n - largo + 1), axis=eje)) == largo
        cf = np.cumsum(llenas.astype(np.int32), axis=eje)
        cf = np.concatenate([np.zeros_like(cf.take([0], axis=eje)), cf], axis=eje)
        k = cf.shape[eje] - 1
        return (cf.take(np.minimum(np.arange(n), k - 1) + 1, axis=eje) - cf.take(np.maximum(np.arange(n) - largo + 1, 0), axis=eje)) > 0
    horiz, vert = cubiertos(tinta, 1), cubiertos(tinta, 0)
    t = tinta.astype(bool)
    e = max(6, g.width // 400)
    arriba, abajo = np.zeros_like(t), np.zeros_like(t)
    arriba[e:, :], abajo[:-e, :] = t[:-e, :], t[e:, :]
    horiz &= ~(arriba & abajo)
    izq, der = np.zeros_like(t), np.zeros_like(t)
    izq[:, e:], der[:, :-e] = t[:, :-e], t[:, e:]
    vert &= ~(izq & der)
    if not (horiz.any() or vert.any()):
        return g
    lineas = horiz | vert
    for d in (1, 2):
        lineas[d:, :] |= horiz[:-d, :]
        lineas[:-d, :] |= horiz[d:, :]
        lineas[:, d:] |= vert[:, :-d]
        lineas[:, :-d] |= vert[:, d:]
    b = a.copy()
    b[lineas] = 255
    return Image.fromarray(b)


def test_borrado_de_lineas_por_bandas_identico():
    import numpy as np
    from PIL import Image, ImageDraw
    rnd = random.Random(5)
    for w, h in [(300, 45), (45, 300), (700, 500), (1300, 900)]:
        im = Image.new("L", (w, h), 255)
        d = ImageDraw.Draw(im)
        for _ in range(25):
            x, y = rnd.randrange(w), rnd.randrange(h)
            if rnd.random() < 0.5:
                d.line([(x, y), (x + rnd.randrange(1, w), y)], fill=rnd.randrange(0, 199), width=rnd.randrange(1, 9))
            else:
                d.line([(x, y), (x, y + rnd.randrange(1, h))], fill=rnd.randrange(0, 199), width=rnd.randrange(1, 9))
        for _ in range(30):
            d.text((rnd.randrange(w), rnd.randrange(h)), "Total 1.234,56", fill=0)
        ref = np.asarray(_sin_lineas_referencia(im))
        assert np.array_equal(np.asarray(texto._sin_lineas(im)), ref)
        assert np.array_equal(np.asarray(texto._sin_lineas(im, banda=7)), ref)


def test_memoria_del_preprocesado_de_un_escaneo_grande():
    """La mayor imagen que produce rasterizar() con ancho < 3000 px (se amplía a 4497×6000): antes ~1,1 GB de pico."""
    script = (
        "import sys, resource, tempfile\n"
        f"sys.path.insert(0, {str(RAIZ)!r})\n"
        "from pathlib import Path\n"
        "from PIL import Image, ImageDraw\n"
        "from app.extraccion import texto\n"
        "im = Image.new('L', (2998, 4000), 255); d = ImageDraw.Draw(im)\n"
        "for y in range(50, 4000, 60): d.line([(0, y), (2998, y)], fill=0, width=2)\n"
        "for x in range(50, 2998, 300): d.line([(x, 0), (x, 4000)], fill=0, width=2)\n"
        "td = tempfile.mkdtemp(); p = Path(td) / 'p.png'; im.save(p); del im, d\n"
        "base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "texto._preparar_escaneo(p)\n"
        "print((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - base) // 1024)\n")
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-500:]
    assert int(r.stdout.strip()) < 450   # MB (medido: unos 130)


# --- Reparación (ronda 1): variantes de F1-F5 halladas en la verificación ------------------------------------------

def test_ancla_muy_alta_no_encadena_los_renglones_de_su_lado():
    """Un glifo enorme (ancla) junto a cientos de renglones «IVA IVA …»: antes, una sola fila y coste cuadrático (~74 s)."""
    pals = [Palabra("I", 5, 10, 1200, 4910)]
    for i in range(2200):
        top = 12 + i * 2.2
        pals += [Palabra("IVA", 2000 + k * 8.0 + (i % 2) * 0.3, top, 2006 + k * 8.0 + (i % 2) * 0.3, top + 4) for k in range(5)]
    pags = [Pagina(1, 5000, 5000, pals)]
    (ex, _), cpu = _cpu(lambda: (lambda ex: (ex, ex.extraer()))(campos.Extractor(pags, "x.pdf", None)))
    assert cpu < LIMITE_CPU_S
    assert max(len(f.bloques) for f in ex.filas) <= campos.MAX_BLOQUES_FILA


@pytest.mark.parametrize("palabra", ["IVA", "Total", "Base imponible", "IVA 21%", "Retención", "1.234,56", "Fecha"])
def test_etiqueta_de_importe_repetida_en_un_renglon(palabra):
    pals, x = [], 5.0
    for r in range(3):
        x = 5.0
        for _ in range(3000):
            w = 0.55 * len(palabra)
            pals.append(Palabra(palabra, x, 100 + r * 10, x + w, 101 + r * 10))
            x += w + 0.3
    _, cpu = _cpu(lambda: campos.Extractor([Pagina(1, 5000, 842, pals)], "x.pdf", None).extraer())
    assert cpu < LIMITE_CPU_S   # antes: O(etiquetas × palabras) por fila (IVA ×2000 ×6 renglones: 10 s)


def test_dato_tras_dos_puntos_lineal_e_igual_a_la_expresion():
    antigua = re.compile(r":\s*\S+\s*$")
    rnd = random.Random(5)
    for _ in range(20000):
        s = "".join(rnd.choice([":", " ", "a", "\t", " ", "1", "\x1f", " "]) for _ in range(rnd.randint(0, 10)))
        assert campos._dato_tras_dos_puntos(s) == bool(antigua.search(s)), repr(s)
    pals = [Palabra("Concepto", 5, 100, 40, 108), Palabra(":" * 59_000, 45, 100, 4000, 108), Palabra("1,00", 4010, 100, 4030, 108),
            Palabra("Otro", 5, 120, 40, 128), Palabra("2,00", 4010, 120, 4030, 128)]
    _, cpu = _cpu(lambda: campos.Extractor([Pagina(1, 5000, 842, pals)], "x.pdf", None).extraer())
    assert cpu < 5   # antes: 23 s por página


def test_renglon_inclinado_y_glifos_diminutos_no_hacen_cuadratico_el_agrupado():
    pals = [Palabra("ab", 5 + k * 0.3, 100 + 0.9 * k / 16000, 5.2 + k * 0.3, 102 + 0.9 * k / 16000) for k in range(16000)]
    _, cpu = _cpu(lambda: texto.lineas([Pagina(1, 5000, 842, pals)]))
    assert cpu < 3   # antes: 12 s (cada palabra se comparaba con todas las alturas distintas del renglón)
    pals = [Palabra("ab", 5 + (k % 2000) * 2.4, 100 + (k % 400) * 0.001, 6 + (k % 2000) * 2.4, 100 + (k % 400) * 0.001 + 0.0009)
            for k in range(16000)]
    _, cpu = _cpu(lambda: texto.lineas([Pagina(1, 5000, 842, pals)]))
    assert cpu < 3


def test_miles_de_nif_no_disparan_el_coste_de_las_partes():
    letras = "TRWAGMYFPDXBNJZSQVHLCKE"
    pals = []
    for r in range(3000):
        n = 10_000_000 + r * 7919
        pals += [Palabra("Cliente", 5, 5 + r * 1.6, 20, 6 + r * 1.6), Palabra(f"{n:08d}{letras[n % 23]}", 25, 5 + r * 1.6, 45, 6 + r * 1.6)]
    _, cpu = _cpu(lambda: campos.Extractor([Pagina(1, 595, 5000, pals)], "x.pdf", None).extraer())
    assert cpu < LIMITE_CPU_S


def test_factura_nativa_con_una_linea_invisible_sigue_siendo_nativa():
    """Una marca invisible del programa de facturación (≥ 40 caracteres en modo 3) no manda la página entera al OCR."""
    def marca(c):
        t = c.beginText(20, 8)
        t.setTextRenderMode(3)
        t.setFont("Helvetica", 6)
        t.textLine("Documento generado electronicamente por el programa de facturacion v4.2 - id 8f3a")
        c.drawText(t)
    pags, e = _extraer(_factura(despues=marca))
    assert pags[0].metodo == "texto_pdf" and pags[0].texto_oculto >= 40
    assert {e[k]["valor"] for k in CAMPOS_TOTALES} == {"100.00", "21.00", "121.00"}
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)


def _cmap(extra=None):
    ent = [b"<%02X> <%s>" % (c, "".join(f"{ord(ch):04X}" for ch in (extra or {}).get(chr(c), chr(c))).encode())
           for c in range(32, 127)]
    cuerpo = b"".join(b"%d beginbfchar\n" % len(ent[i:i + 90]) + b"\n".join(ent[i:i + 90]) + b"\nendbfchar\n"
                      for i in range(0, len(ent), 90))
    return (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /X def /CMapType 2 def\n"
            b"1 begincodespacerange <00> <FF> endcodespacerange\n" + cuerpo + b"endcmap CMapName currentdict /CMap "
            b"defineresource pop end end\n")


def _pdf_oculto(mecanismo, png=None, remap=None):
    """La página muestra la factura VISIBLE (como imagen, o como texto en «remap») y lleva una capa de texto con los
    importes OCULTO escondida con `mecanismo`."""
    import zlib
    from PIL import Image
    objs = {5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"}

    def flujo(dic, datos, comprimir=True):
        if comprimir:
            datos, dic = zlib.compress(datos), dic + b" /Filter /FlateDecode"
        return b"<< " + dic + b" /Length %d >>\nstream\n" % len(datos) + datos + b"\nendstream"

    def capa(vals, fuente=b"/F1", fuente_cifras=None):
        out = [b"BT"]
        for x, y, s in LINEAS_FACTURA + [(450, y, s) for y, s in TOTALES]:
            f = fuente_cifras if (fuente_cifras and s.startswith("{")) else fuente
            t = s.format(**vals).encode("cp1252").replace(b"(", b"\\(").replace(b")", b"\\)")
            out.append(b"%s 10 Tf 1 0 0 1 %d %d Tm (%s) Tj" % (f, x, y, t))
        return b"\n".join(out + [b"ET"])
    xobj, extra_rec, cat, fuentes = [], b"", b"", [b"/F1 5 0 R"]
    if png is not None:
        im = Image.open(png).convert("L")
        dic = b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8" % im.size
        mas = b""
        if mecanismo == "smask":
            objs[9] = flujo(dic, b"\xff" * (im.size[0] * im.size[1]))
            mas = b" /SMask 9 0 R"
        elif mecanismo == "colorkey":
            im = im.point(lambda v: 5 if v == 3 else v)
            mas = b" /Mask [3 3]"
        objs[6] = flujo(dic + mas, im.tobytes())
        xobj.append(b"/Im1 6 0 R")
    imagen = b"q 595.2756 0 0 841.8898 0 0 cm /Im1 Do Q\n"
    oculto = capa(OCULTO)
    if mecanismo == "clip":
        cont = imagen + b"q 0 0 1 1 re W n\n" + oculto + b"\nQ"
    elif mecanismo == "ocg":
        objs[10] = b"<< /Type /OCG /Name (capa) >>"
        cat = b" /OCProperties << /OCGs [10 0 R] /D << /Order [10 0 R] /OFF [10 0 R] >> >>"
        extra_rec = b" /Properties << /oc1 10 0 R >>"
        cont = imagen + b"/OC /oc1 BDC\n" + oculto + b"\nEMC"
    elif mecanismo in ("smask", "colorkey"):
        cont = oculto + b"\n" + imagen   # la imagen (opaca) tapa el texto
    elif mecanismo == "type3":
        objs[12] = flujo(b"", b"500 0 d0", comprimir=False)   # glifos que no dibujan nada
        objs[11] = (b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix [0.001 0 0 0.001 0 0] /CharProcs << "
                    + b" ".join(b"/g%d 12 0 R" % c for c in range(32, 256)) + b" >> /Encoding << /Type /Encoding /Differences [32 "
                    + b" ".join(b"/g%d" % c for c in range(32, 256)) + b"] >> /FirstChar 32 /LastChar 255 /Widths ["
                    + b" ".join(b"500" for _ in range(32, 256)) + b"] /ToUnicode 13 0 R /Resources << >> >>")
        objs[13] = flujo(b"", _cmap())
        fuentes.append(b"/F3 11 0 R")
        cont = imagen + capa(OCULTO, fuente=b"/F3")
    elif mecanismo == "formbbox":
        objs[14] = flujo(b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources << /Font << /F1 5 0 R >> >>", oculto)
        xobj.append(b"/Fm1 14 0 R")
        cont = imagen + b"q /Fm1 Do Q"
    else:   # remap: sin imagen; el «,» de la fuente de las cifras se traduce por «0,»
        objs[15] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding /ToUnicode 16 0 R >>"
        objs[16] = flujo(b"", _cmap(remap or {",": "0,"}))
        fuentes.append(b"/F4 15 0 R")
        cont = capa(VISIBLE, fuente_cifras=b"/F4")
    objs[4] = flujo(b"", cont)
    rec = (b"<< /Font << " + b" ".join(fuentes) + b" >>" + (b" /XObject << " + b" ".join(xobj) + b" >>" if xobj else b"")
           + extra_rec + b" >>")
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R" + cat + b" >>"
    objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[3] = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.2756 841.8898] /Contents 4 0 R /Resources " + rec + b" >>"
    out, offs = io.BytesIO(), {}
    out.write(b"%PDF-1.5\n")
    for i in range(1, max(objs) + 1):
        if i in objs:
            offs[i] = out.tell()
            out.write(b"%d 0 obj\n" % i + objs[i] + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (max(objs) + 1))
    for i in range(1, max(objs) + 1):
        out.write(b"%010d 00000 n \n" % offs[i] if i in offs else b"0000000000 65535 f \n")
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (max(objs) + 1, xref))
    return out.getvalue()


@pytest.mark.parametrize("mecanismo", ["clip", "ocg", "smask", "colorkey", "type3", "formbbox"])
def test_capa_oculta_por_otros_mecanismos_se_lee_lo_que_se_ve(mecanismo):
    """Recorte de 1×1 pt, OCG desactivado, imagen opaca con máscara encima, glifos Type3 vacíos, /BBox de un Form: la
    capa de texto no se ve y la página se lee por OCR (antes: los importes ocultos con confianza 0,97 y sin aviso)."""
    with tempfile.TemporaryDirectory() as td:
        png = inspeccion.rasterizar(_factura(), Path(td), dpi=200)[0]
        datos = _pdf_oculto(mecanismo, png)
    inspeccion.inspeccionar(datos)
    pags, e = _extraer(datos)
    assert pags[0].metodo == "ocr"
    assert not any(p.texto in ("1.000,00", "210,00", "1.210,00") for p in pags[0].palabras)
    assert e["base_imponible"]["valor"] == "100.00" and e["total_factura"]["valor"] == "121.00"


def test_tounicode_que_cambia_las_cifras_obliga_a_confirmar():
    """Se ve «100,00» pero el /ToUnicode traduce «,» por «0,» (el texto dice 1000,00): los importes son dudosos."""
    pags, e = _extraer(_pdf_oculto("remap"))
    assert pags[0].metodo == "texto_pdf"
    assert set(CAMPOS_TOTALES) <= _dudosos(e)
    assert all(e[k]["confianza"] <= texto.CONF_TEXTO_DUDOSO for k in CAMPOS_TOTALES)


def test_rotulos_blancos_sobre_degradado_son_visibles():
    from reportlab.lib.colors import Color

    def degradado(c, colores):
        c.saveState()
        p = c.beginPath()
        p.rect(440, 445, 100, 50)
        c.clipPath(p, stroke=0, fill=0)
        c.linearGradient(440, 445, 540, 445, colores, extend=False)
        c.restoreState()
    _, e = _extraer(_factura(antes=lambda c: degradado(c, (Color(0.1, 0.2, 0.5), Color(0.2, 0.3, 0.6))), color_totales=(1, 1, 1)))
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)
    # un degradado blanco no es fondo: blanco sobre blanco
    _, e = _extraer(_factura(antes=lambda c: degradado(c, (Color(1, 1, 1), Color(1, 1, 1))), color_totales=(1, 1, 1)))
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


def test_sello_translucido_con_mascara_no_tapa_los_importes():
    """Un sello PNG con transparencia (SMask casi toda transparente) sobre los totales no los oculta."""
    from PIL import Image, ImageDraw
    sello = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    ImageDraw.Draw(sello).ellipse((10, 10, 390, 190), outline=(200, 0, 0, 255), width=6)
    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "sello.png"
        sello.save(ruta)
        _, e = _extraer(_factura(despues=lambda c: c.drawImage(str(ruta), 430, 440, width=120, height=60, mask="auto")))
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)


def test_importes_sin_tinta_en_la_pagina_obligan_a_confirmar():
    """Mecanismo de ocultación no reconocido por el análisis del PDF (aquí, un círculo blanco dibujado encima, que no
    es un rectángulo): la imagen de la página no tiene tinta donde están los importes, que pasan a dudosos."""
    def tapa(c):
        c.setFillColorRGB(1, 1, 1)
        c.circle(490, 470, 45, stroke=0, fill=1)
    pags, e = _extraer(_factura(despues=tapa))
    assert pags[0].metodo == "texto_pdf"
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


@pytest.mark.parametrize("giro", [0, 90, 180, 270])
def test_comprobacion_de_tinta_en_paginas_giradas_y_recortadas(giro):
    """La caja de cada palabra se lleva a la imagen -cropbox con la misma matriz que los caracteres (MediaBox con
    origen distinto de 0, CropBox y /Rotate): ninguna palabra visible pasa a dudosa."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    for i in range(40):
        c.drawString(60 + (i % 3) * 150, 780 - i * 18, f"Linea {i} importe {i * 3},50 B12345674")
    c.showPage()
    c.save()
    for caja, recorte in [(None, None), ((30, 50, 625, 892), (40, 100, 560, 800)), ((-20, -40, 575, 802), (10, 5, 300, 400))]:
        pg = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
        if caja:
            pg.mediabox = RectangleObject(caja)
            pg.cropbox = RectangleObject(recorte)
        if giro:
            pg.rotate(giro)
        w = PdfWriter()
        w.add_page(pg)
        out = io.BytesIO()
        w.write(out)
        p = texto.leer(out.getvalue())[0]
        assert p.metodo == "texto_pdf" and p.palabras and all(x.conf == 1.0 for x in p.palabras)


# --- Revisión de diferencias: otras ocultaciones, autocomprobación del filtro y aviso de texto oculto ----------------
def _totales_escondidos(modo):
    """Dibuja los importes OCULTO en la zona de los totales escondidos de otra forma (la factura no lleva los visibles)."""
    def escribir(c):
        for y, s in TOTALES:
            c.drawString(450, y, s.format(**OCULTO))

    def dibujar(c):
        c.setFont("Helvetica", 10)
        if modo in ("form_alfa", "form_blanco"):   # Form XObject que hereda la transparencia o el color de quien lo invoca
            c.beginForm("tot")
            c.setFont("Helvetica", 10)
            escribir(c)
            c.endForm()
            c.saveState()
            c.setFillAlpha(0) if modo == "form_alfa" else c.setFillColorRGB(1, 1, 1)
            c.doForm("tot")
            c.restoreState()
        elif modo == "poligono":   # tapados por un polígono blanco (un trazado, no un rectángulo)
            escribir(c)
            c.setFillColorRGB(1, 1, 1)
            p = c.beginPath()
            for i, (x, y) in enumerate([(440, 445), (520, 445), (535, 470), (520, 495), (440, 495)]):
                p.moveTo(x, y) if i == 0 else p.lineTo(x, y)
            p.close()
            c.drawPath(p, stroke=0, fill=1)
        elif modo == "gris97":
            c.setFillColorRGB(0.97, 0.97, 0.97)
            escribir(c)
        elif modo == "diminuto":
            c.setFont("Helvetica", 0.3)
            escribir(c)
        elif modo in ("tz5", "aplastado"):   # cuadratín deformado: Tz 5 % o una matriz que aplasta el texto
            c.saveState()
            if modo == "aplastado":
                c.transform(1, 0, 0, 0.05, 0, 0)
            t = c.beginText()
            t.setFont("Helvetica", 10)
            if modo == "tz5":
                t.setHorizScale(5)
            for y, s in TOTALES:
                t.setTextOrigin(450, y / 0.05 if modo == "aplastado" else y)
                t.textOut(s.format(**OCULTO))
            c.drawText(t)
            c.restoreState()
        elif modo in ("tz80", "letra5"):   # legítimos: texto condensado al 80 % o letra pequeña de 5 pt
            t = c.beginText()
            t.setFont("Helvetica", 5 if modo == "letra5" else 10)
            if modo == "tz80":
                t.setHorizScale(80)
            for y, s in TOTALES:
                t.setTextOrigin(450, y)
                t.textOut(s.format(**OCULTO))
            c.drawText(t)
        c.setFillColorRGB(0, 0, 0)
    return dibujar


@pytest.mark.parametrize("modo", ["form_alfa", "form_blanco", "poligono", "gris97", "diminuto", "tz5", "aplastado"])
def test_importes_que_no_se_ven_nunca_se_leen_con_confianza_alta(modo):
    """Variantes de la revisión: se representan sin tinta (o ilegibles) en la zona de los totales. Antes se leían con
    confianza 0,97 y sin aviso; ahora no se leen o se leen como dudosos (hay que confirmarlos)."""
    _, e = _extraer(_factura(totales=False, despues=_totales_escondidos(modo)))
    dud = _dudosos(e)
    for k in CAMPOS_TOTALES:
        assert e[k]["valor"] is None or (k in dud and e[k]["confianza"] <= texto.CONF_TEXTO_DUDOSO), (k, e[k])


@pytest.mark.parametrize("modo", ["tz80", "letra5"])
def test_texto_condensado_o_pequeno_legible_no_es_dudoso(modo):
    _, e = _extraer(_factura(totales=False, despues=_totales_escondidos(modo)))
    assert {e[k]["valor"] for k in CAMPOS_TOTALES} == {"1000.00", "210.00", "1210.00"}
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)


def test_filtro_de_texto_oculto_falla_cerrado_si_pdfminer_cambia(tmp_path):
    """Si una versión de pdfminer deja de llamar a una de las ampliaciones (aquí: el operador gs, la transparencia), la
    autocomprobación con el PDF conocido lo detecta y el proceso hijo se niega a leer, en vez de leer sin filtrar."""
    ent = tmp_path / "f.pdf"
    ent.write_bytes(_factura())
    proc = Path(texto.__file__).with_name("texto_proceso.py")
    for parche in ("pass", "t._interprete.PDFPageInterpreter.do_gs = lambda self, nombre: None",
                   "t._interprete.PDFGraphicState.copy = t._copiar_estado",
                   "t._interprete.PDFPageInterpreter.do_W = lambda self: None"):
        codigo = (f"import sys; sys.path.insert(0, {str(proc.parent)!r}); import texto_proceso as t; {parche}; "
                  f"sys.argv = ['x', {str(ent)!r}, '5']; sys.exit(t.main())")
        r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=120)
        if parche == "pass":
            assert r.returncode == 0 and "paginas" in r.stdout
        else:
            assert r.returncode == 4 and "pdfminer" in r.stdout and "paginas" not in r.stdout, (parche, r.stdout)


def test_pdfminer_fijado_en_la_version_instalada():
    """texto_proceso amplía clases internas de pdfminer.six: la versión instalada es la fijada en requirements.txt."""
    from importlib.metadata import version
    fijadas = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", (RAIZ / "requirements.txt").read_text(), re.M))
    assert fijadas.get("pdfminer.six") == version("pdfminer.six")
    assert fijadas.get("pdfplumber") == version("pdfplumber")


def test_texto_oculto_se_avisa_en_el_borrador(monkeypatch):
    """El texto del PDF que no se ve se ignora al leer, pero se avisa: puede ser una capa manipulada."""
    from app import db
    monkeypatch.setattr(db, "fila", lambda *a, **k: None)
    monkeypatch.setattr(db, "filas", lambda *a, **k: [])

    def incidencias(e):
        return borradores.validar(None, {"id": 1, "nif": "B12345674"}, None, borradores.datos_desde_extraccion(e, "x.pdf"),
                                  extraccion=e)

    def capa(c):
        t = c.beginText(450, 300)
        t.setTextRenderMode(3)
        t.setFont("Helvetica", 10)
        t.textLine("Total factura 1.210,00 Base imponible 1.000,00 IVA 210,00")
        c.drawText(t)
    _, e = _extraer(_factura(despues=capa))
    aviso = [i for i in incidencias(e) if i["codigo"] == "TEXTO_OCULTO"]
    assert [i["nivel"] for i in aviso] == ["aviso"] and "página 1" in aviso[0]["mensaje"]
    _, e = _extraer(_factura())
    assert not [i for i in incidencias(e) if i["codigo"] == "TEXTO_OCULTO"]
    # en una página leída por OCR la capa invisible es la de búsqueda de un escaneo: lo normal, sin aviso
    e["paginas"][0].update(metodo="ocr", texto_oculto=500)
    assert not [i for i in incidencias(e) if i["codigo"] == "TEXTO_OCULTO"]


# --- Robustez r2: fondos de página, degradados de otros tipos y fuentes incrustadas --------------------------------

def test_pie_invisible_sobre_imagen_de_fondo_sigue_siendo_nativa():
    """Membrete como imagen de fondo a página completa (casi blanca, dibujada antes que el texto) y una línea invisible
    del programa de facturación en el pie: la capa invisible no está sobre nada que se vea, la página sigue nativa."""
    from PIL import Image, ImageDraw
    fondo = Image.new("RGB", (595, 842), (252, 252, 250))
    ImageDraw.Draw(fondo).rectangle([0, 0, 595, 6], fill=(30, 60, 120))

    def marca(c):
        t = c.beginText(20, 8)
        t.setTextRenderMode(3)
        t.setFont("Helvetica", 6)
        t.textLine("Documento generado electronicamente por el programa de facturacion v4.2 - id 8f3a")
        c.drawText(t)
    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "fondo.png"
        fondo.save(ruta)
        pags, e = _extraer(_factura(imagen=str(ruta), despues=marca))
    assert pags[0].metodo == "texto_pdf" and pags[0].texto_oculto >= 40
    assert {e[k]["valor"] for k in CAMPOS_TOTALES} == {"100.00", "21.00", "121.00"}
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)


def test_capa_invisible_sobre_los_totales_escaneados_se_lee_por_ocr():
    """Factura mixta: cabecera nativa visible y el cuadro de totales como imagen (escaneo) con una capa invisible que
    dice otros importes. La capa está sobre tinta de la imagen: se lee lo que se ve."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("L", (900, 300), 255)
    d = ImageDraw.Draw(im)
    try:
        letra = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        letra = ImageFont.load_default(size=40)
    for i, (k, v) in enumerate([("Base imponible", "100,00"), ("IVA 21%", "21,00"), ("Total factura", "121,00")]):
        d.text((10, 10 + 90 * i), k, font=letra, fill=0)
        d.text((600, 10 + 90 * i), v, font=letra, fill=0)
    with tempfile.TemporaryDirectory() as td:
        ruta = Path(td) / "totales.png"
        im.save(ruta)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setFont("Helvetica", 10)
        for x, y, s in LINEAS_FACTURA[:-3]:
            c.drawString(x, y, s.format(**VISIBLE))
        c.drawImage(ImageReader(str(ruta)), 330, 400, width=225, height=75)
        t = c.beginText()
        t.setTextRenderMode(3)
        t.setFont("Helvetica", 10)
        for y, s in [(463, "Base imponible            1.000,00"), (440, "IVA 21%                   210,00"),
                     (418, "Total factura             1.210,00")]:
            t.setTextOrigin(332, y)
            t.textOut(s)
        c.drawText(t)
        c.showPage()
        c.save()
    pags, e = _extraer(buf.getvalue())
    assert pags[0].metodo == "ocr"
    assert not any(p.texto in ("1.000,00", "210,00", "1.210,00") for p in pags[0].palabras)
    assert e["total_factura"]["valor"] == "121.00"


def _degradado_con_funcion(datos: bytes, funcion: str, codigo: bytes) -> bytes:
    """Cambia la función de los degradados del PDF por una de tipo 4 (calculadora PostScript, la que emite Chrome/Skia
    para un degradado CSS) o de tipo 0 (muestreada), con colores RGB."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject, NumberObject
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(datos)))
    res = w.pages[0]["/Resources"]
    for k in list(res["/Shading"]):
        sh = res["/Shading"][k].get_object()
        f = DecodedStreamObject()
        f.set_data(codigo)
        f.update({NameObject("/FunctionType"): NumberObject(4 if funcion == "tipo4" else 0),
                  NameObject("/Domain"): ArrayObject([FloatObject(0), FloatObject(1)]),
                  NameObject("/Range"): ArrayObject([FloatObject(v) for v in (0, 1, 0, 1, 0, 1)])})
        if funcion == "tipo0":
            f.update({NameObject("/Size"): ArrayObject([NumberObject(2)]), NameObject("/BitsPerSample"): NumberObject(8)})
        sh[NameObject("/Function")] = w._add_object(f)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


@pytest.mark.parametrize("funcion,codigo", [
    ("tipo4", b"{ dup 0.1 mul 0.1 add exch dup 0.1 mul 0.2 add exch 0.1 mul 0.5 add }"),
    ("tipo0", bytes([25, 51, 128, 51, 77, 153]))])
def test_rotulos_blancos_sobre_degradado_muestreado_o_calculado_son_visibles(funcion, codigo):
    """Banda oscura en degradado con función de tipo 4 o 0 (colores que el análisis del PDF no calcula): la imagen de
    la página muestra el texto blanco sobre fondo oscuro, que es visible."""
    from reportlab.lib.colors import Color

    def degradado(c):
        c.saveState()
        p = c.beginPath()
        p.rect(440, 445, 100, 50)
        c.clipPath(p, stroke=0, fill=0)
        c.linearGradient(440, 445, 540, 445, (Color(0.1, 0.2, 0.5), Color(0.2, 0.3, 0.6)), extend=False)
        c.restoreState()
    datos = _degradado_con_funcion(_factura(antes=degradado, color_totales=(1, 1, 1)), funcion, codigo)
    pags, e = _extraer(datos)
    assert pags[0].metodo == "texto_pdf" and all(p.conf == 1.0 for p in pags[0].palabras)
    assert {e[k]["valor"] for k in CAMPOS_TOTALES} == {"100.00", "21.00", "121.00"}
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)
    # el mismo degradado calculado en blanco no es un fondo: blanco sobre blanco
    blanco = b"{ pop 1 1 1 }" if funcion == "tipo4" else bytes([255] * 6)
    _, e = _extraer(_degradado_con_funcion(_factura(antes=degradado, color_totales=(1, 1, 1)), funcion, blanco))
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


def test_blanco_sobre_blanco_con_rayas_debajo_sigue_dudoso():
    """El texto blanco sin fondo solo vuelve a ser seguro si se ve claro sobre un fondo más oscuro: unas rayas de la
    tabla que cruzan su caja no lo hacen visible."""
    def rayas(c):
        c.setLineWidth(1.5)
        for y in (465, 479, 493):
            c.line(440, y, 545, y)
    _, e = _extraer(_factura(antes=rayas, color_totales=(1, 1, 1)))
    assert set(CAMPOS_TOTALES) <= _dudosos(e)
    assert all(e[k]["confianza"] <= texto.CONF_TEXTO_DUDOSO for k in CAMPOS_TOTALES)


FUENTE_INCRUSTADA = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _factura_fuente_incrustada(cambios: dict | None = None) -> bytes:
    """Factura con los importes en una fuente TrueType incrustada; `cambios` altera su /ToUnicode (cifra que se ve ->
    texto que dice, o un código Unicode de 4 cifras hexadecimales)."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if not FUENTE_INCRUSTADA.exists():
        pytest.skip("sin la fuente DejaVuSans del sistema")
    pdfmetrics.registerFont(TTFont("CifrasPrueba", str(FUENTE_INCRUSTADA)))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    for x, y, s in LINEAS_FACTURA:
        c.drawString(x, y, re.sub(r"\{\w+\}", "", s))
    c.setFont("CifrasPrueba", 10)
    for y, v in [(540, "1.250,00"), (480, "1.250,00"), (466, "262,50"), (452, "1.512,50")]:
        c.drawRightString(520, y, v)
    c.showPage()
    c.save()
    if not cambios:
        return buf.getvalue()
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    fuentes = w.pages[0]["/Resources"]["/Font"]
    for k in fuentes:
        f = fuentes[k].get_object()
        if "DejaVu" not in str(f.get("/BaseFont")):
            continue
        tu = f["/ToUnicode"].get_object()
        cm = tu.get_data().decode("latin-1")
        codigo = {v.upper(): c_ for c_, v in re.findall(r"<([0-9A-Fa-f]{2})>\s*<([0-9A-Fa-f]{4})>", cm)}
        for de, a in cambios.items():
            cod = codigo[f"{ord(de):04X}"]
            cm = re.sub(r"<%s>\s*<%04X>" % (cod, ord(de)), f"<{cod}> <{a if len(a) == 4 else '%04X' % ord(a)}>", cm)
        tu.set_data(cm.encode("latin-1"))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def test_cifras_de_fuente_incrustada_legitima_no_son_dudosas():
    pags, e = _extraer(_factura_fuente_incrustada())
    assert pags[0].metodo == "texto_pdf" and all(p.conf == 1.0 for p in pags[0].palabras)
    assert (e["base_imponible"]["valor"], e["total_factura"]["valor"]) == ("1250.00", "1512.50")
    assert all(e[k]["confianza"] == 0.97 for k in CAMPOS_TOTALES) and not _dudosos(e) & set(CAMPOS_TOTALES)


@pytest.mark.parametrize("cambios", [{"2": "9"}, {"1": "7"}, {"5": "6"}])
def test_tounicode_cambiado_en_fuente_incrustada_obliga_a_confirmar(cambios):
    """Fuente incrustada solo para los importes con el /ToUnicode de UN glifo de cifra cambiado: la página muestra
    1.250,00 / 262,50 / 1.512,50 y el texto dice otra cosa. Antes: los importes del texto con confianza 0,97 y sin aviso.
    El glifo se contrasta con lo que se lee en la imagen de la página: los importes que lo usan son dudosos."""
    pags, e = _extraer(_factura_fuente_incrustada(cambios))
    assert pags[0].metodo == "texto_pdf"
    for k in CAMPOS_TOTALES:
        assert e[k]["valor"] in ("1250.00", "262.50", "1512.50") or e[k]["confianza"] <= texto.CONF_TEXTO_DUDOSO, k
    malos = [k for k in CAMPOS_TOTALES if e[k]["valor"] not in ("1250.00", "262.50", "1512.50")]
    assert malos and set(malos) <= _dudosos(e)


def test_glifo_de_cifra_traducido_a_un_caracter_de_control_no_se_da_por_bueno():
    """El «1» de los importes traducido por U+0000: el texto dice «.250,00» donde se ve «1.250,00»."""
    pags, e = _extraer(_factura_fuente_incrustada({"1": "0000"}))
    for k in CAMPOS_TOTALES:
        assert e[k]["valor"] is None or e[k]["valor"] in ("1250.00", "262.50", "1512.50") or k in _dudosos(e), k


def test_sin_ocr_las_cifras_de_fuente_incrustada_no_se_dan_por_buenas(monkeypatch):
    """Si no se pueden contrastar con la imagen (el OCR falla), las cifras de fuentes incrustadas son dudosas."""
    real = inspeccion.ejecutar

    def sin_tesseract(cmd, **kw):
        if cmd and cmd[0] == "tesseract":
            return subprocess.CompletedProcess(cmd, 1, "", "sin OCR")
        return real(cmd, **kw)
    monkeypatch.setattr(inspeccion, "ejecutar", sin_tesseract)
    _, e = _extraer(_factura_fuente_incrustada())
    assert set(CAMPOS_TOTALES) <= _dudosos(e)


@pytest.mark.parametrize("fuente", ["incrustada", "estandar"])
def test_cifra_traducida_por_un_espacio_obliga_a_confirmar(fuente):
    """El «1» de los importes traducido por un espacio: se ve «1.250,00» (o «100,00») y el texto dice «.250,00» (o «00,00»):
    el blanco dibuja una cifra (tinta en la imagen, o un /ToUnicode que contradice la codificación de una fuente
    estándar) y los importes contiguos son dudosos."""
    if fuente == "incrustada":
        pags, e = _extraer(_factura_fuente_incrustada({"1": "0020"}))
        buenos = ("1250.00", "262.50", "1512.50")
    else:
        pags, e = _extraer(_pdf_oculto("remap", remap={"1": " "}))
        buenos = ("100.00", "21.00", "121.00")
    assert pags[0].metodo == "texto_pdf"
    for k in CAMPOS_TOTALES:
        assert e[k]["valor"] is None or e[k]["valor"] in buenos or k in _dudosos(e), (k, e[k]["valor"])
