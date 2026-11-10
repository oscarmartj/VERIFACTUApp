"""Generación del PDF de la factura (vista previa de borrador y documento definitivo).

Estrategias:
- superposicion: se conserva el PDF original tal cual y se añade en la primera página, en una zona
  libre detectada automáticamente, el bloque «QR tributario:» + QR + «VERI*FACTU». Solo se usa si
  todo lo impreso en el original coincide con los datos aprobados (número, fecha de expedición,
  partes, importes, título «FACTURA») y hay hueco suficiente sin tapar contenido.
- reconstruccion: se compone una factura nueva con los datos aprobados, reutilizando la identidad
  visual (logotipo recortado del original) y conservando referencias de obra, líneas y condiciones.

Los PDF de borrador llevan la marca «BORRADOR – SIN VALOR FISCAL» y nunca QR tributario.
Los de simulador/pruebas llevan marca de entorno de pruebas y QR al servicio de pruebas de la AEAT.
"""
from __future__ import annotations

import io
import re
import tempfile
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.doctemplate import LayoutError

from .config import APP_NOMBRE, APP_VERSION
from .extraccion.inspeccion import ejecutar
from .importes import fmt_es, fmt_porcentaje
from .verifactu import qr as qrmod

QR_LADO_MM = 32.0          # símbolo; especificación: entre 30x30 y 40x40 mm
QR_SILENCIO_MM = 6.0       # zona en blanco: mínimo 2 mm, recomendado 6 mm
QR_FUENTE = 10             # «QR tributario:» y leyenda: igual o mayor que el resto de datos de la factura
MARGEN_SEGURO_MM = 8.0
# Variantes para buscar hueco en el original (recomendada y mínima admisible)
VARIANTES_QR = [(32.0, 6.0), (30.0, 2.5)]


def dim_bloque(lado_mm: float = QR_LADO_MM, silencio_mm: float = QR_SILENCIO_MM) -> tuple[float, float]:
    """(ancho, alto) en mm del bloque «QR tributario:» + QR con zona en blanco + leyenda."""
    texto = QR_FUENTE * 1.25 / mm  # alto de línea en mm
    return max(lado_mm + 2 * silencio_mm, 46.0), texto + silencio_mm + lado_mm + silencio_mm + texto + 1


BLOQUE_ANCHO_MM, BLOQUE_ALTO_MM = dim_bloque()

MARCAS = {
    "borrador": "BORRADOR – SIN VALOR FISCAL",
    "simulador": "SIMULACIÓN LOCAL – SIN VALIDEZ FISCAL",
    "pruebas": "ENTORNO DE PRUEBAS AEAT – SIN VALIDEZ FISCAL",
}

AZUL = colors.HexColor("#1F3A5F")
GRIS = colors.HexColor("#555555")


def _d(fecha_iso: str | None) -> str:
    if not fecha_iso:
        return ""
    partes = str(fecha_iso).split("-")
    if len(partes) != 3:
        return str(fecha_iso)  # fecha sin formato ISO en un borrador a medio editar (la validación la bloquea)
    a, m, d = partes
    return f"{d}/{m}/{a}"


# --- QR ------------------------------------------------------------------------------
def dibujar_bloque_qr(c, x: float, y_sup: float, url: str, leyenda: str = qrmod.LEYENDA_VERIFACTU,
                      lado_mm: float = QR_LADO_MM, silencio_mm: float = QR_SILENCIO_MM) -> dict:
    """Dibuja «QR tributario:», el QR (corrección M) y la leyenda, centrados.
    (x, y_sup) = esquina superior izquierda del bloque en puntos PDF."""
    q = qrmod.matriz(url)
    filas = list(q.matrix)
    n = len(filas)
    lado = lado_mm * mm
    mod = lado / n
    silencio = silencio_mm * mm
    ancho_mm, alto_mm = dim_bloque(lado_mm, silencio_mm)
    ancho_bloque = ancho_mm * mm
    cx = x + ancho_bloque / 2
    linea = QR_FUENTE * 1.25
    c.saveState()
    c.setFillColor(colors.white)
    c.rect(x, y_sup - alto_mm * mm, ancho_bloque, alto_mm * mm, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", QR_FUENTE)
    c.drawCentredString(cx, y_sup - QR_FUENTE, qrmod.LEYENDA_SUPERIOR)
    qx = cx - lado / 2
    qy_sup = y_sup - linea - silencio
    for i, fila in enumerate(filas):
        for j, v in enumerate(fila):
            if v:
                c.rect(qx + j * mod, qy_sup - (i + 1) * mod, mod, mod, stroke=0, fill=1)
    c.drawCentredString(cx, qy_sup - lado - silencio - QR_FUENTE, leyenda)
    c.restoreState()
    return {"lado_mm": lado_mm, "zona_blanco_mm": silencio_mm, "modulos": n, "version": q.version, "error": q.error,
            "x_mm": round(qx / mm, 1), "y_sup_mm": round(qy_sup / mm, 1), "pagina": 1, "fuente_pt": QR_FUENTE}


def dibujar_hueco_qr(c, x, y_sup):
    c.saveState()
    c.setStrokeColor(GRIS)
    c.setDash(3, 3)
    c.rect(x, y_sup - BLOQUE_ALTO_MM * mm, BLOQUE_ANCHO_MM * mm, BLOQUE_ALTO_MM * mm, stroke=1, fill=0)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(GRIS)
    for k, t in enumerate(["Espacio reservado", "para el QR tributario", "(se genera al expedir)"]):
        c.drawCentredString(x + BLOQUE_ANCHO_MM * mm / 2, y_sup - BLOQUE_ALTO_MM * mm / 2 + 8 - 9 * k, t)
    c.restoreState()


def _texto_ajustado(c, texto: str, x_izq: float, y: float, ancho: float, fuente: str, tam: float, color) -> float:
    """Escribe alineado a la derecha dentro de [x_izq, x_izq+ancho]; reduce el cuerpo hasta 7 pt y, si aún no cabe,
    parte el texto en varias líneas. Devuelve la coordenada y de la siguiente línea."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    c.setFillColor(color)
    t = tam
    while t > 7 and stringWidth(texto, fuente, t) > ancho:
        t -= 0.5
    lineas = [texto]
    if stringWidth(texto, fuente, t) > ancho:
        lineas, actual = [], ""
        for ch in texto:
            if stringWidth(actual + ch, fuente, t) > ancho:
                lineas.append(actual)
                actual = ch
            else:
                actual += ch
        lineas.append(actual)
    c.setFont(fuente, t)
    for l in lineas:
        c.drawRightString(x_izq + ancho, y, l)
        y -= t * 1.25
    return y - 2


def _texto_izquierda(c, texto: str, x: float, y: float, ancho: float, fuente: str, tam: float, color) -> float:
    """Como _texto_ajustado pero alineado a la izquierda (reduce hasta 7 pt y, si no cabe, parte por palabras)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    c.setFillColor(color)
    t = tam
    while t > 7 and stringWidth(texto, fuente, t) > ancho:
        t -= 0.5
    larga = max((stringWidth(p, fuente, t) for p in texto.split()), default=0)
    while t > 6 and larga > ancho:   # una palabra no puede invadir el bloque QR
        t -= 0.5
        larga = max((stringWidth(p, fuente, t) for p in texto.split()), default=0)
    palabras = []
    for p in texto.split():
        while stringWidth(p, fuente, t) > ancho and len(p) > 1:   # último recurso: partir la palabra
            k = len(p)
            while k > 1 and stringWidth(p[:k], fuente, t) > ancho:
                k -= 1
            palabras.append(p[:k])
            p = p[k:]
        palabras.append(p)
    lineas, actual = [], ""
    for palabra in palabras:
        prueba = (actual + " " + palabra).strip()
        if stringWidth(prueba, fuente, t) > ancho and actual:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    lineas.append(actual)
    c.setFont(fuente, t)
    for l in lineas[:4]:
        c.drawString(x, y, l)
        y -= t * 1.25
    return y


def _marca_agua(c, texto, ancho, alto):
    c.saveState()
    c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.13))
    c.setFont("Helvetica-Bold", 34)
    c.translate(ancho / 2, alto / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, texto)
    c.restoreState()
    c.saveState()
    c.setFillColor(colors.HexColor("#B00020"))
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(ancho / 2, alto - 5 * mm, texto)
    c.restoreState()


def _ok(r):
    if r.returncode != 0:
        raise RuntimeError(f"Fallo de la herramienta de representación (código {r.returncode})")
    return r


# --- Detección de zona libre --------------------------------------------------------------
def _render_gris(pdf: bytes, dpi: int = 72, pagina: int = 1) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "e.pdf").write_bytes(pdf)
        _ok(ejecutar(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-cropbox", "-f", str(pagina), "-l", str(pagina),
                      str(Path(td) / "e.pdf"), str(Path(td) / "p")], timeout=60))
        img = Image.open(next(Path(td).glob("p*.png"))).convert("L")
        return np.asarray(img)


def buscar_hueco(pdf: bytes, fraccion_superior: float = 0.30) -> tuple[float, float, float, float] | None:
    """Busca, cerca del margen superior, una zona en blanco para el bloque QR (preferiblemente centrada,
    si no hacia la izquierda y después la derecha). Devuelve (x, y_sup, lado_mm, silencio_mm) en puntos PDF."""
    g = _render_gris(pdf, 72)
    alto_px, ancho_px = g.shape
    tinta = (g < 245).astype(np.int32)
    integral = np.pad(tinta.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    marg = int(MARGEN_SEGURO_MM * mm)
    for lado_mm, silencio_mm in VARIANTES_QR:
        bw_mm, bh_mm = dim_bloque(lado_mm, silencio_mm)
        bw, bh = int(bw_mm * mm) + 4, int(bh_mm * mm) + 4
        y_max = min(int(alto_px * fraccion_superior), alto_px - marg - bh)
        centro = (ancho_px - bw) // 2
        xs = sorted(range(marg, ancho_px - marg - bw, 3), key=lambda x: (0 if abs(x - centro) < 60 else 1 if x < centro else 2,
                                                                         abs(x - centro)))
        for y in range(marg, y_max + 1, 3):
            for x in xs:
                s = integral[y + bh, x + bw] - integral[y, x + bw] - integral[y + bh, x] + integral[y, x]
                if s == 0:
                    return float(x + 2), float(alto_px - (y + 2)), lado_mm, silencio_mm
    return None


# --- Recorte de la identidad visual -----------------------------------------------------------
RE_NO_LOGO = re.compile(r"factura|proforma|presupuesto|borrador|n[º°o]\s*[:.]|fecha|\d{1,2}/\d{1,2}/\d{2,4}", re.I)


def recortar_logo(original: bytes, extraccion: dict | None) -> bytes | None:
    """Recorta el logotipo/cabecera izquierda del original para reutilizar su identidad visual (una única
    representación de la primera página, con resolución acotada por su tamaño)."""
    try:
        p1 = PdfReader(io.BytesIO(original)).pages[0]
        lado = max(float(p1.mediabox.width), float(p1.mediabox.height))
        dpi = max(30, min(200, int(2400 * 72 / max(lado, 1))))
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "e.pdf").write_bytes(original)
            _ok(ejecutar(["pdftoppm", "-r", str(dpi), "-png", "-cropbox", "-f", "1", "-l", "1", str(Path(td) / "e.pdf"),
                          str(Path(td) / "c")], timeout=60, fsize=100 * 1024 * 1024))
            img = Image.open(next(Path(td).glob("c*.png"))).convert("RGB")
            img.load()
    except Exception:
        return None
    escala = dpi / 72
    g = np.asarray(img.convert("L"))
    alto_px, ancho_px = g.shape
    sub = g[:int(alto_px * 0.16), :int(ancho_px * 0.5)]
    # El logotipo es el primer grupo de columnas con tinta empezando por la izquierda: termina en un hueco en blanco
    # ancho (2 % de la página). Así no se lleva el borde ni el texto de otro bloque que empiece antes de la mitad de la
    # página (p. ej. el recuadro del cliente); se descartan los grupos finos (filetes y bordes de página)
    tinta = sub < 235
    columnas = tinta.any(axis=0)
    hueco_max, ancho_min = max(4, int(ancho_px * 0.02)), max(4, int(ancho_px * 0.02))
    grupos, ini, blancos = [], None, 0
    for x, c in enumerate(columnas):
        if c:
            ini, blancos = (x if ini is None else ini), 0
            fin = x
        elif ini is not None:
            blancos += 1
            if blancos > hueco_max:
                grupos.append((ini, fin))
                ini = None
    if ini is not None:
        grupos.append((ini, fin))
    grupo = next(((a, b) for a, b in grupos if b - a + 1 >= ancho_min and tinta[:, a:b + 1].sum() >= 50), None)
    if grupo is None:
        return None
    ys = np.where(tinta[:, grupo[0]:grupo[1] + 1].any(axis=1))[0]
    x0, x1, y0, y1 = grupo[0], grupo[1], ys.min(), ys.max()
    # Excluir si el recorte contiene textos de número/fecha/título
    if extraccion:
        for p in extraccion.get("_lineas_cabecera", []):
            bx0, btop, bx1, bbot = (v * escala for v in p["bbox"])
            solapa = bx0 < x1 and bx1 > x0 and btop < y1 and bbot > y0
            if solapa and RE_NO_LOGO.search(p["texto"]):
                return None
    pad = 6
    rec = img.crop((max(0, x0 - pad), max(0, y0 - pad), min(ancho_px, x1 + pad), min(alto_px, y1 + pad)))
    out = io.BytesIO()
    rec.save(out, "PNG")
    return out.getvalue()


# --- Reconstrucción -----------------------------------------------------------------------------
def _estilos():
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=8.5, leading=10.5)
    return {
        "base": base,
        "peq": ParagraphStyle("peq", parent=base, fontSize=7.5, leading=9, textColor=GRIS),
        "neg": ParagraphStyle("neg", parent=base, fontName="Helvetica-Bold"),
        "der": ParagraphStyle("der", parent=base, alignment=TA_RIGHT),
        "der_neg": ParagraphStyle("dern", parent=base, alignment=TA_RIGHT, fontName="Helvetica-Bold"),
        "tit": ParagraphStyle("tit", parent=base, fontName="Helvetica-Bold", fontSize=9, textColor=AZUL, spaceAfter=2),
    }


def _p(texto, estilo):
    from xml.sax.saxutils import escape
    return Paragraph(escape(str(texto or "")).replace("\n", "<br/>"), estilo)


def _imp(v, moneda: bool = True) -> str:
    """Importe para mostrar; vacío si falta o no es un número (vista previa de un borrador a medio editar)."""
    try:
        return fmt_es(v, moneda=moneda)
    except (ArithmeticError, TypeError, ValueError):
        return ""


def _imp_negativo(v) -> str:
    try:
        return fmt_es(-Decimal(str(v)))
    except (ArithmeticError, TypeError, ValueError):
        return ""


def _pct(v) -> str:
    try:
        return fmt_porcentaje(v) if v not in (None, "") else "?"
    except (ArithmeticError, TypeError, ValueError):
        return "?"


def _filas(v) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


class DocumentoDemasiadoLargo(ValueError):
    """El borrador ocuparía demasiadas páginas: su vista previa no se compone (ver borradores.MAX_RENGLONES_PDF; la
    expedición la impide la validación, y una factura ya expedida siempre se puede volver a componer)."""


def reconstruir(datos: dict, *, modo: str, entorno: str | None, url_qr: str | None, logo_png: bytes | None,
                fecha_expedicion: str | None) -> tuple[bytes, dict]:
    est = _estilos()
    W, H = A4
    info: dict = {"estrategia": "reconstruccion"}
    em, de = datos.get("emisor") or {}, datos.get("destinatario") or {}
    marca = MARCAS["borrador"] if modo == "borrador" else MARCAS.get(entorno or "")
    cabecera_alto = (BLOQUE_ALTO_MM + 14) * mm
    es_rect = (datos.get("tipo_factura") or "F1").startswith("R")
    titulo = "FACTURA RECTIFICATIVA" if es_rect else "FACTURA"
    fecha_txt = _d(fecha_expedicion) if fecha_expedicion else "(se asignará al expedir)"

    def primera(c, doc):
        c.saveState()
        if logo_png:
            img = ImageReader(io.BytesIO(logo_png))
            iw, ih = img.getSize()
            esc = min(55 * mm / iw, 24 * mm / ih)
            c.drawImage(img, 15 * mm, H - 12 * mm - ih * esc, iw * esc, ih * esc)
        else:
            # Sin logotipo: la razón social completa, ajustada al espacio a la izquierda del bloque QR
            _texto_izquierda(c, em.get("nombre") or "", 15 * mm, H - 18 * mm,
                             W / 2 - BLOQUE_ANCHO_MM * mm / 2 - 3 * mm - 15 * mm, "Helvetica-Bold", 11, AZUL)
        # QR al principio de la factura, próximo al margen superior y centrado (especificación AEAT)
        xq, yq = W / 2 - BLOQUE_ANCHO_MM * mm / 2, H - 8 * mm
        if modo == "definitivo" and url_qr:
            info["qr"] = dibujar_bloque_qr(c, xq, yq, url_qr)
        else:
            dibujar_hueco_qr(c, xq, yq)
        # Textos de cabecera a la derecha del bloque QR (con 3 mm de separación), sin invadirlo nunca
        x_izq = xq + BLOQUE_ANCHO_MM * mm + 3 * mm
        ancho = W - 15 * mm - x_izq
        y = H - 18 * mm
        y = _texto_ajustado(c, titulo, x_izq, y, ancho, "Helvetica-Bold", 16 if es_rect else 18, AZUL)
        for t in ([f"Nº: {datos.get('num_serie') or ''}", f"Fecha de expedición: {fecha_txt}"]
                  + ([f"Fecha de operación: {_d(datos['fecha_operacion'])}"] if datos.get("fecha_operacion") else [])):
            y = _texto_ajustado(c, t, x_izq, y - 2, ancho, "Helvetica", 9.5, colors.black)
        _pie(c, doc)
        if marca:
            _marca_agua(c, marca, W, H)
        c.restoreState()

    def siguientes(c, doc):
        c.saveState()
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(AZUL)
        c.drawString(15 * mm, H - 15 * mm, f"{titulo} Nº {datos.get('num_serie') or ''} (continuación)")
        _pie(c, doc)
        if marca:
            _marca_agua(c, marca, W, H)
        c.restoreState()

    def _pie(c, doc):
        c.setStrokeColor(AZUL)
        c.line(15 * mm, 17 * mm, W - 15 * mm, 17 * mm)
        c.setFont("Helvetica", 6.8)
        c.setFillColor(GRIS)
        c.drawCentredString(W / 2, 13 * mm, f"{em.get('nombre') or ''} · NIF {em.get('nif') or ''} · {em.get('domicilio') or ''}"[:160])
        c.drawRightString(W - 15 * mm, 8 * mm, f"Página {doc.page}")
        c.drawString(15 * mm, 8 * mm, f"Documento generado con {APP_NOMBRE} {APP_VERSION}")

    def _componer(partir: int) -> bytes:
        buf = io.BytesIO()
        story = []
        # Partes
        partes = Table([
            [_p("EMISOR", est["tit"]), _p("CLIENTE", est["tit"])],
            [_p(em.get("nombre"), est["neg"]), _p(de.get("nombre"), est["neg"])],
            [_p(f"NIF: {em.get('nif') or ''}", est["base"]), _p(f"NIF: {de.get('nif') or ''}", est["base"])],
            [_p(em.get("domicilio"), est["base"]), _p(de.get("domicilio"), est["base"])],
        ], colWidths=[90 * mm, 90 * mm], splitInRow=partir)
        partes.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (1, 0), (1, -1), 0.6, AZUL),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 4)]))
        story += [partes, Spacer(1, 4 * mm)]
        # Referencias comerciales (se conservan aunque no formen parte del registro)
        etiquetas = {"obra": "Obra", "certificacion": "Certificación nº", "pedido": "Pedido",
                     "partida_presupuestaria": "Partida presupuestaria", "periodo": "Periodo facturado",
                     "contrato": "Contrato", "proyecto": "Proyecto", "expediente": "Expediente"}
        r_datos = datos.get("referencias") if isinstance(datos.get("referencias"), dict) else {}
        orden = list(etiquetas) + sorted(k for k in r_datos if k not in etiquetas)  # orden fijo: PDF reproducible
        refs = [(etiquetas.get(k, k.replace("_", " ").capitalize()), r_datos[k]) for k in orden
                if isinstance(k, str) and isinstance(r_datos.get(k), str) and r_datos[k]]
        if refs:
            t = Table([[_p(f"{k}:", est["neg"]), _p(v, est["base"])] for k, v in refs], colWidths=[42 * mm, 138 * mm], splitInRow=partir)
            t.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [t, Spacer(1, 4 * mm)]
        if es_rect and datos.get("rectificativa"):
            r = datos["rectificativa"]
            story += [_p(f"Rectifica la factura {r.get('num_serie_rectificada')} de fecha {_d(r.get('fecha_rectificada'))}. "
                         f"Motivo: {r.get('motivo') or ''}", est["base"]), Spacer(1, 3 * mm)]
        # Líneas
        filas = [[_p("CONCEPTO", est["neg"]), _p("CANTIDAD", est["der_neg"]), _p("UD.", est["neg"]),
                  _p("PRECIO", est["der_neg"]), _p("IMPORTE", est["der_neg"])]]
        for l in _filas(datos.get("lineas")):
            filas.append([_p(l.get("concepto"), est["base"]),
                          _p(_imp(l.get("cantidad"), moneda=False), est["der"]),
                          _p(l.get("unidad") or "", est["base"]),
                          _p(_imp(l.get("precio"), moneda=False), est["der"]),
                          _p(_imp(l.get("importe"), moneda=False), est["der"])])
        tl = Table(filas, colWidths=[96 * mm, 20 * mm, 14 * mm, 24 * mm, 26 * mm], repeatRows=1, splitInRow=partir)
        tl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")),
                                ("LINEBELOW", (0, 0), (-1, 0), 0.6, AZUL), ("LINEBELOW", (0, -1), (-1, -1), 0.4, GRIS),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [tl, Spacer(1, 4 * mm)]
        # Totales
        tot = []
        # (un borrador a medio editar puede tener filas incompletas: la vista previa las muestra en blanco, nunca falla)
        desglose = _filas(datos.get("desglose"))
        for d in desglose:
            if len(desglose) > 1:
                tot.append([_p(f"Base imponible al {_pct(d.get('tipo_iva'))} %", est["der"]), _p(_imp(d.get("base")), est["der"])])
            tot.append([_p(f"IVA {_pct(d.get('tipo_iva'))} %", est["der"]), _p(_imp(d.get("cuota")), est["der"])])
        tot.insert(0, [_p("BASE IMPONIBLE", est["der"]), _p(_imp(datos.get("base_imponible")), est["der"])])
        tot.append([_p("TOTAL FACTURA", est["der_neg"]), _p(_imp(datos.get("importe_total")), est["der_neg"])])
        ret = datos.get("retencion") if isinstance(datos.get("retencion"), dict) else None
        if ret and ret.get("importe"):
            tot.append([_p(f"Retención IRPF {_pct(ret.get('porcentaje') or '0')} %", est["der"]),
                        _p(_imp_negativo(ret["importe"]), est["der"])])
            tot.append([_p("TOTAL A PAGAR", est["der_neg"]), _p(_imp(datos.get("total_a_pagar")), est["der_neg"])])
        tt = Table(tot, colWidths=[150 * mm, 30 * mm], splitInRow=partir)
        tt.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.6, AZUL)]))
        story += [tt, Spacer(1, 5 * mm)]
        # Condiciones de pago
        pago = []
        if datos.get("forma_pago"):
            pago.append(f"Forma de pago: {datos['forma_pago']}")
        if datos.get("iban"):
            iban = re.sub(r"\s", "", datos["iban"])
            pago.append("IBAN: " + " ".join(iban[i:i + 4] for i in range(0, len(iban), 4)))
        if datos.get("vencimiento"):
            pago.append(f"Vencimiento: {_d(datos['vencimiento'])}")
        for t in pago:
            story.append(_p(t, est["base"]))
        if datos.get("observaciones"):
            story += [Spacer(1, 3 * mm), _p(datos["observaciones"], est["base"])]
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                                topMargin=cabecera_alto, bottomMargin=22 * mm,
                                title=f"{titulo} {datos.get('num_serie') or ''}", author=em.get("nombre") or "",
                                creator=f"{APP_NOMBRE} {APP_VERSION}", invariant=1)
        doc.build(story, onFirstPage=primera, onLaterPages=lambda c, d: (setattr(d, "topMargin", 25 * mm), siguientes(c, d)))
        return buf.getvalue()

    try:
        pdf = _componer(0)
    except LayoutError:
        # una fila más alta que la página (un concepto, un nombre o un domicilio muy largos): sin partir las filas entre
        # páginas no se puede maquetar; solo entonces se parten (el resto de facturas no cambia)
        pdf = _componer(1)
    info["paginas"] = len(PdfReader(io.BytesIO(pdf)).pages)
    return pdf, info


# --- Superposición ----------------------------------------------------------------------------
def superponer(original: bytes, *, entorno: str, url_qr: str) -> tuple[bytes, dict] | None:
    reader = PdfReader(io.BytesIO(original))
    p1 = reader.pages[0]
    # Solo páginas «simples»: sin rotación y con las cajas coincidentes y en el origen. En otro caso las coordenadas
    # del hueco no corresponden a lo que muestran los visores y se recompone la factura.
    try:
        rot = int(p1.get("/Rotate", 0) or 0) % 360
        mb = [float(v) for v in p1.mediabox]
        cajas = [[float(v) for v in getattr(p1, k)] for k in ("cropbox", "trimbox", "bleedbox", "artbox")]
    except Exception:
        return None
    if rot or mb[0] != 0 or mb[1] != 0 or any(cj != mb for cj in cajas):
        return None
    if p1.get("/Annots"):
        return None  # las anotaciones pueden imprimirse sobre el QR sin verse en pantalla
    hueco = buscar_hueco(original)
    if not hueco:
        return None
    ancho, alto = float(p1.mediabox.width), float(p1.mediabox.height)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(ancho, alto), invariant=1)
    qinfo = dibujar_bloque_qr(c, hueco[0], hueco[1], url_qr, lado_mm=hueco[2], silencio_mm=hueco[3])
    marca = MARCAS.get(entorno)
    if marca:
        _marca_agua(c, marca, ancho, alto)
    c.showPage()
    c.save()
    capa = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
    w = PdfWriter()
    for i, pg in enumerate(reader.pages):
        if i == 0:
            pg.merge_page(capa)
        w.add_page(pg)
    w.add_metadata({"/Producer": f"{APP_NOMBRE} {APP_VERSION} (superposición sobre el documento preparatorio)"})
    out = io.BytesIO()
    w.write(out)
    resultado = out.getvalue()
    # Comprobación visual: fuera del hueco la página debe ser idéntica al original y dentro debe haber tinta (el QR).
    # Detecta, p. ej., un contenido original que deja el estado gráfico desequilibrado y desplaza la capa.
    try:
        a, b = _render_gris(original, 72).astype(int), _render_gris(resultado, 72).astype(int)
    except Exception:
        return None
    if a.shape != b.shape:
        return None
    bw, bh = dim_bloque(hueco[2], hueco[3])
    x0, y0 = int(hueco[0]) - 2, int(alto - hueco[1]) - 2
    x1, y1 = x0 + int(bw * mm) + 4, y0 + int(bh * mm) + 4
    marca_y = int(12 * mm)  # banda superior de la marca de entorno (si la hay)
    mascara = np.ones(a.shape, dtype=bool)
    mascara[max(0, y0):y1, max(0, x0):x1] = False
    if marca:
        mascara[:marca_y, :] = False
        diff_fuera = np.abs(a - b)[mascara]
        if (diff_fuera > 60).mean() > 0.02:   # la marca de agua diagonal es tenue; se tolera
            return None
    elif np.abs(a - b)[mascara].max(initial=0) > 8:
        return None
    if (b[max(0, y0):y1, max(0, x0):x1] < 128).sum() < 200:
        return None
    return resultado, {"estrategia": "superposicion", "qr": qinfo, "paginas": len(reader.pages),
                       "hueco_pt": [round(hueco[0], 1), round(hueco[1], 1)]}


def superposicion_viable(datos: dict, extraccion: dict | None, fecha_expedicion: str, cambios: int) -> tuple[bool, str]:
    if not extraccion:
        return False, "Sin documento de origen"
    if any(p.get("metodo") == "ocr" for p in extraccion.get("paginas", [])):
        return False, "El original es una imagen escaneada"
    if cambios:
        return False, "Se corrigieron datos durante la revisión: lo impreso en el original ya no coincide"
    tipo = (extraccion.get("tipo_documento") or {}).get("valor")
    if tipo != "factura":
        return False, f"El original se titula «{tipo}»"
    if datos.get("fecha_documento") != fecha_expedicion:
        return False, "La fecha impresa no coincide con la fecha de expedición"
    if datos.get("retencion"):
        return False, "Factura con retención: se compone para mostrar total y total a pagar diferenciados"
    return True, "Lo impreso coincide con los datos aprobados"


def generar(datos: dict, *, modo: str, entorno: str | None, url_qr: str | None, original: bytes | None,
            extraccion: dict | None, fecha_expedicion: str | None, cambios: int = 0,
            forzar_estrategia: str | None = None, qr_en_original: bool = False) -> tuple[bytes, dict]:
    if modo == "definitivo":
        viable, motivo = superposicion_viable(datos, extraccion, fecha_expedicion or "", cambios)
        if viable and qr_en_original:
            viable, motivo = False, "El original ya contiene un código QR: el QR tributario debe ser el primero y único"
        if forzar_estrategia:
            viable, motivo = forzar_estrategia == "superposicion", "Regeneración con la estrategia registrada"
        if viable and original and url_qr:
            r = superponer(original, entorno=entorno or "", url_qr=url_qr)
            if r:
                r[1]["motivo"] = motivo
                return r
            motivo = "No hay espacio libre suficiente para el QR sin tapar contenido"
    else:
        motivo = "Vista previa de borrador"
    logo = recortar_logo(original, extraccion) if original else None
    pdf, info = reconstruir(datos, modo=modo, entorno=entorno, url_qr=url_qr, logo_png=logo, fecha_expedicion=fecha_expedicion)
    info["motivo"] = motivo
    info["logo_original"] = bool(logo)
    return pdf, info


# --- Verificación del PDF generado ----------------------------------------------------------
def leer_qrs(pdf: bytes, paginas: int) -> list[list[str]]:
    """Lee los QR de cada página: la primera a 200 ppp (acotada por su tamaño) y el resto, por separado, a 100 ppp."""
    out = []
    reader = PdfReader(io.BytesIO(pdf))
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "e.pdf").write_bytes(pdf)
        for n in range(1, len(reader.pages) + 1):
            pg = reader.pages[n - 1]
            lado = max(float(pg.mediabox.width), float(pg.mediabox.height))
            dpi = max(30, min(200 if n == 1 else 100, int(3000 * 72 / max(lado, 1))))
            pref = Path(td) / f"p{n}"
            _ok(ejecutar(["pdftoppm", "-r", str(dpi), "-png", "-cropbox", "-f", str(n), "-l", str(n), str(Path(td) / "e.pdf"),
                          str(pref)], timeout=120, fsize=100 * 1024 * 1024))
            png = next(Path(td).glob(f"p{n}-*.png"))
            r = ejecutar(["zbarimg", "--quiet", "--raw", "-Sqrcode.enable", str(png)], timeout=60, text=True)
            out.append([x for x in r.stdout.splitlines() if x.strip()])
            png.unlink()
    return out


def verificar(pdf: bytes, datos: dict, url_qr: str | None, fecha_expedicion: str | None) -> dict:
    """Comprueba que el PDF refleja los datos aprobados y que el QR decodifica a la URL registrada."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as d:
        texto = "\n".join((p.extract_text() or "") for p in d.pages)
        n = len(d.pages)
    norm = re.sub(r"\s+", " ", texto)
    compacto = re.sub(r"\s+", "", texto)  # un número largo puede partirse en varias líneas
    comprobaciones = {
        "num_serie": re.sub(r"\s+", "", datos.get("num_serie") or "") in compacto,
        "importe_total": fmt_es(datos.get("importe_total"), moneda=False) in norm,
        "nif_emisor": (datos.get("emisor") or {}).get("nif", "") in norm.replace("-", ""),
        "nif_destinatario": (datos.get("destinatario") or {}).get("nif", "") in norm.replace("-", ""),
        "fecha_expedicion": _d(fecha_expedicion) in norm if fecha_expedicion else True,
        "leyenda_qr": (qrmod.LEYENDA_SUPERIOR in norm) if url_qr else (qrmod.LEYENDA_SUPERIOR not in norm),
    }
    try:
        qrs = leer_qrs(pdf, n)
    except Exception as e:  # noqa: BLE001 — un fallo de verificación nunca debe pasar por bueno
        return {"ok": False, "comprobaciones": dict(comprobaciones, lectura_qr=False), "qr_leidos": [], "paginas": n,
                "error": type(e).__name__}
    if url_qr:
        comprobaciones["qr_primera_pagina"] = url_qr in (qrs[0] if qrs else [])
        comprobaciones["qr_una_sola_vez"] = sum(len(x) for x in qrs) == 1
    else:
        comprobaciones["sin_qr"] = sum(len(x) for x in qrs) == 0
    return {"ok": all(comprobaciones.values()), "comprobaciones": comprobaciones, "qr_leidos": qrs, "paginas": n}


def png_paginas(pdf: bytes, dpi: int = 110, pagina: int | None = None) -> list[bytes]:
    """Imágenes de las páginas (o solo de `pagina`) con resolución acotada por el tamaño de página."""
    reader = PdfReader(io.BytesIO(pdf))
    total = len(reader.pages)
    nums = [pagina] if pagina else list(range(1, total + 1))
    out = []
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "e.pdf").write_bytes(pdf)
        for n in nums:
            if not 1 <= n <= total:
                continue
            pg = reader.pages[n - 1]
            lado = max(float(pg.mediabox.width), float(pg.mediabox.height))
            d = max(30, min(dpi, int(1600 * 72 / max(lado, 1))))
            _ok(ejecutar(["pdftoppm", "-r", str(d), "-png", "-cropbox", "-f", str(n), "-l", str(n), str(Path(td) / "e.pdf"),
                          str(Path(td) / f"p{n}")], timeout=120, fsize=50 * 1024 * 1024))
            png = next(Path(td).glob(f"p{n}-*.png"))
            out.append(png.read_bytes())
    return out
