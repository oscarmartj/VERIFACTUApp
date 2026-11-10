"""Lectura de texto con posiciones: texto nativo del PDF y, si falta, OCR local (Tesseract).

Ningún contenido del documento se envía a servicios externos.
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path

import pdfplumber

from ..config import ajustes
from .inspeccion import rasterizar

MIN_CARACTERES_PAGINA = 40
DUDOSO_SOBRE_PAPEL = 2     # marca de texto_proceso: dudosa solo por ser del color del papel sin fondo reconocido
CONF_TEXTO_DUDOSO = 0.5    # confianza de las palabras del PDF que pueden no verse (blanco sin fondo, tapadas...)
DPI_OCR = 300
DPI_TINTA = 100             # resolución de la comprobación de que el texto nativo se ve en la página
CONTRASTE_MIN_TINTA = 40    # diferencia mínima de gris (0-255) en la caja de una palabra que se ve
# Cotas del agrupado en renglones frente a documentos hostiles (una página real queda muy por debajo)
MAX_TRAMOS_EXACTOS = 64      # alturas distintas de un renglón comparadas una a una
MAX_GRUPOS_ABIERTOS = 64     # renglones abiertos a la vez con los que se prueba cada palabra
# Contraste de las cifras de fuentes de dibujo libre (incrustadas, Type3, CID) con lo que muestra la página
DPI_CIFRAS = 300
VECES_GLIFO = 3              # apariciones de cada glifo que se leen en la imagen
MAX_TRAMOS_CIFRAS = 150      # palabras leídas por documento
MAX_PAGINAS_CIFRAS = 10      # páginas representadas para leerlas (las demás cifras sin contrastar son dudosas)
MAX_ASPECTO_TRAMO = 40       # ancho/alto máximo del recorte de un tramo de cifras (y nunca más de caracteres + 2)
ALTO_TRAMO_PX = 48           # altura a la que se lleva cada palabra antes del OCR
CONF_CONTRADICE = 0.5        # confianza mínima del OCR para que una lectura distinta cuente en contra del glifo


@dataclass
class Palabra:
    texto: str
    x0: float
    top: float
    x1: float
    bottom: float
    conf: float = 1.0          # 0..1 (1 en texto nativo)
    metodo: str = "texto_pdf"  # texto_pdf | ocr


@dataclass
class Linea:
    """Renglón visual. Sus palabras no cambian tras crearlo: texto y geometría se calculan una sola vez."""
    pagina: int
    palabras: list[Palabra]

    @cached_property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palabras)

    @cached_property
    def x0(self): return min(p.x0 for p in self.palabras)

    @cached_property
    def x1(self): return max(p.x1 for p in self.palabras)

    @cached_property
    def top(self): return min(p.top for p in self.palabras)

    @cached_property
    def bottom(self): return max(p.bottom for p in self.palabras)

    @cached_property
    def posiciones(self) -> list[tuple[int, int]]:
        """(inicio, fin) de cada palabra dentro de `texto`."""
        t, out, k = self.texto, [], 0
        for p in self.palabras:
            ini = t.find(p.texto, k)
            k = ini + len(p.texto)
            out.append((ini, k))
        return out

    @cached_property
    def fines_ordenados(self) -> list[int] | None:
        """Fines de palabra si crecen estrictamente (siempre, salvo palabras vacías o raras): permiten búsqueda binaria."""
        pos = self.posiciones
        fines = [f for _, f in pos]
        ok = all(i >= 0 for i, _ in pos) and all(x < y for x, y in zip(fines, fines[1:]))
        return fines if ok else None

    @property
    def conf(self): return min(p.conf for p in self.palabras)

    @property
    def metodo(self): return "ocr" if any(p.metodo == "ocr" for p in self.palabras) else "texto_pdf"

    def bbox(self):
        return [round(self.x0, 1), round(self.top, 1), round(self.x1, 1), round(self.bottom, 1)]


@dataclass
class Pagina:
    numero: int
    ancho: float
    alto: float
    palabras: list[Palabra] = field(default_factory=list)
    metodo: str = "texto_pdf"
    conf_media: float = 1.0
    texto_oculto: int = 0      # caracteres del PDF que no se ven en la página (invisibles o fuera de ella): ignorados


def _palabras_nativas(page) -> list[Palabra]:
    out = []
    for w in page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2):
        t = w["text"].strip()
        if t:
            out.append(Palabra(t, w["x0"], w["top"], w["x1"], w["bottom"]))
    return out


def _angulo_inclinacion(im) -> float:
    """Inclinación del escaneo (−3° a 3°): el ángulo que hace más nítido el perfil horizontal de los renglones."""
    import numpy as np
    from PIL import Image
    peq = im.resize((max(1, im.width // 4), max(1, im.height // 4)))
    tinta = Image.fromarray(((np.asarray(peq) < 128) * 255).astype("uint8"))
    mejor, puntos = 0.0, -1.0
    for paso in range(-30, 31, 2):
        ang = paso / 10
        perfil = (np.asarray(tinta.rotate(ang, resample=Image.NEAREST, fillcolor=0)) > 0).sum(axis=1).astype(float)
        p = float((np.diff(perfil) ** 2).sum())
        if p > puntos:
            mejor, puntos = ang, p
    return mejor


def _preparar_escaneo(png: Path) -> None:
    """Endereza, limpia motas, normaliza el contraste, amplía y borra los bordes de tabla antes del OCR. Las coordenadas
    se reescalan después con el ancho real de la imagen procesada."""
    from PIL import Image, ImageFilter, ImageOps
    with Image.open(png) as im:
        g = im.convert("L")
    ang = _angulo_inclinacion(g)
    if abs(ang) >= 0.2:
        g = g.rotate(ang, resample=Image.BICUBIC, fillcolor=255)
    # Motas fuera, contraste normalizado y ampliación ×1,5 (medido con escaneos simulados: 88,9 % → 89,6 % de campos y
    # mucho mejor en texto mecanografiado fino). Normalizar el fondo de las celdas sombreadas se probó y empeoró.
    g = ImageOps.autocontrast(g.filter(ImageFilter.MedianFilter(3)))
    if g.width < 3000:
        g = g.resize((g.width * 3 // 2, g.height * 3 // 2), Image.LANCZOS)
    g = _sin_lineas(g)
    g.save(png)


def _tramos_largos(m, largo: int):
    """Píxeles de m (bool, filas × columnas) que forman parte de un tramo horizontal de tinta de `largo` píxeles o más.
    Ventanas completas por sumas acumuladas en int32."""
    import numpy as np
    filas, n = m.shape
    if n <= largo:
        return np.zeros(m.shape, dtype=bool)
    c = np.zeros((filas, n + 1), dtype=np.int32)
    np.cumsum(m, axis=1, dtype=np.int32, out=c[:, 1:])
    llenas = (c[:, largo:] - c[:, :n - largo + 1]) == largo
    del c
    k = llenas.shape[1]
    cf = np.zeros((filas, k + 1), dtype=np.int32)
    np.cumsum(llenas, axis=1, dtype=np.int32, out=cf[:, 1:])
    del llenas
    j = np.arange(n)
    return (cf[:, np.minimum(j, k - 1) + 1] - cf[:, np.maximum(j - largo + 1, 0)]) > 0


def _sin_lineas(g, banda: int = 256):
    """Borra las líneas largas horizontales y verticales (bordes de tablas y cajas): con ellas Tesseract suele
    saltarse el texto de las celdas. Las letras no tienen trazos rectos tan largos.
    Se procesa por bandas de filas y de columnas con matrices bool/int32: la memoria queda en unas pocas veces la de
    la imagen de 8 bits (este paso corre en el proceso del servidor, sin los límites de las herramientas externas)."""
    import numpy as np
    from PIL import Image
    a = np.array(g)                      # copia escribible, uint8
    alto, ancho = a.shape
    largo = max(40, ancho // 30)  # ≈ 7 mm en A4
    # solo líneas finas: el trazo de una letra grande es grueso (hay tinta a e px a ambos lados)
    e = max(6, ancho // 400)
    # Líneas verticales, por bandas de columnas; `marca` empieza siendo la máscara de líneas verticales
    marca = np.zeros(a.shape, dtype=bool)
    for j0 in range(0, ancho, banda):
        j1 = min(ancho, j0 + banda)
        v = _tramos_largos((a[:, j0:j1] < 200).T, largo).T
        lo, hi = max(j0, e), min(j1, ancho - e)
        if lo < hi:
            v[:, lo - j0:hi - j0] &= ~((a[:, lo - e:hi - e] < 200) & (a[:, lo + e:hi + e] < 200))
        marca[:, j0:j1] = v
    hay = bool(marca.any())
    # Líneas horizontales por bandas de filas (con 2 filas de margen para el ensanche) y unión con las verticales
    for i0 in range(0, alto, banda):
        i1 = min(alto, i0 + banda)
        r0, r1 = max(0, i0 - 2), min(alto, i1 + 2)
        h = _tramos_largos(a[r0:r1] < 200, largo)
        lo, hi = max(r0, e), min(r1, alto - e)
        if lo < hi:
            h[lo - r0:hi - r0] &= ~((a[lo - e:hi - e] < 200) & (a[lo + e:hi + e] < 200))
        hay = hay or bool(h[i0 - r0:i1 - r0].any())
        v = marca[i0:i1].copy()
        lin = h[i0 - r0:i1 - r0] | v
        # el borde difuminado de la línea también se borra (2 px a cada lado)
        for d in (1, 2):
            if i1 > max(i0, d):
                lin[max(i0, d) - i0:] |= h[max(i0, d) - d - r0:i1 - d - r0]
            if min(i1, alto - d) > i0:
                lin[:min(i1, alto - d) - i0] |= h[i0 + d - r0:min(i1, alto - d) + d - r0]
            lin[:, d:] |= v[:, :-d]
            lin[:, :-d] |= v[:, d:]
        marca[i0:i1] = lin
    if not hay:
        return g
    np.putmask(a, marca, 255)
    return Image.fromarray(a)


def _ocr_pagina(png: Path, ancho_pt: float, alto_pt: float) -> list[Palabra]:
    from .inspeccion import ejecutar
    _preparar_escaneo(png)
    # psm 11 (texto disperso): reconoce cada palabra con su posición sin suponer un orden de lectura; los renglones y
    # columnas los reconstruye después la propia aplicación por geometría (mejor en tablas y bloques en columnas)
    # Un hilo por proceso: con varias importaciones a la vez, los hilos OpenMP de Tesseract compiten, consumen CPU en
    # esperas activas y agotan el límite de CPU del proceso (se probó: 4 OCR simultáneos fallaban)
    import os
    r = ejecutar(["tesseract", str(png), "stdout", "-l", "spa", "--psm", "11", "tsv"], timeout=ajustes().timeout_ocr_s, text=True,
                 env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    if r.returncode != 0:
        raise RuntimeError("Fallo del OCR: " + r.stderr[-300:])
    from PIL import Image
    with Image.open(png) as im:
        escala = ancho_pt / im.width  # escala real (la resolución puede reducirse en páginas grandes)
    out = []
    ant_clave = None
    for fila in csv.DictReader(io.StringIO(r.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        t = (fila.get("text") or "").strip()
        if not t or fila.get("level") != "5":
            continue
        try:
            conf = float(fila["conf"]) / 100.0
        except (TypeError, ValueError):
            conf = 0.0
        x, y, w, h = (int(fila[k]) for k in ("left", "top", "width", "height"))
        clave = (fila.get("block_num"), fila.get("par_num"), fila.get("line_num"))
        p = Palabra(t, x * escala, y * escala, (x + w) * escala, (y + h) * escala, max(conf, 0.0), "ocr")
        ant = out[-1] if out else None
        # «17289, 27» o «10, 00%»: el OCR separa los decimales de un importe; se vuelven a unir
        if ant is not None and ant_clave == clave and re.fullmatch(r"-?[\d.]*\d[,.]", ant.texto) and re.fullmatch(r"\d{1,2}%?|\d{2}[€%]?", t) \
                and p.x0 - ant.x1 < 1.2 * (p.bottom - p.top):
            out[-1] = Palabra(ant.texto + t, ant.x0, min(ant.top, p.top), p.x1, max(ant.bottom, p.bottom), min(ant.conf, p.conf), "ocr")
        else:
            out.append(p)
        ant_clave = clave
    return out


def leer(datos: bytes) -> list[Pagina]:
    """Texto nativo en un proceso hijo con límites de memoria y CPU; OCR página a página (cada una con su resolución)."""
    import json
    import subprocess
    import sys
    from .inspeccion import PDFNoValido, ejecutar
    max_p = ajustes().max_paginas_pdf
    with tempfile.TemporaryDirectory() as td:
        ent = Path(td) / "e.pdf"
        ent.write_bytes(datos)
        try:
            r = ejecutar([sys.executable, str(Path(__file__).with_name("texto_proceso.py")), str(ent), str(max_p)],
                         timeout=120, text=True)
        except subprocess.TimeoutExpired as e:
            raise PDFNoValido("La extracción de texto tarda demasiado") from e
    try:
        res = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        res = {}
    if r.returncode != 0 or "paginas" not in res:
        raise PDFNoValido(res.get("error") or "No se pudo extraer el texto del PDF dentro de los límites de seguridad")
    paginas: list[Pagina] = []
    necesitan_ocr: list[int] = []
    capas_en_imagen: dict[int, list | None] = {}
    sobre_papel: dict[int, list[int]] = {}   # palabras dudosas solo por ser del color del papel sin fondo reconocido
    cifras: dict[int, list | None] = {}       # palabras con cifras de fuentes de dibujo libre (ver _verificar_cifras)
    blancos: dict[int, list | None] = {}      # blancos junto a cifras cuyo glifo puede dibujar algo (ver _marcar_sin_tinta)
    for pg_ in res["paginas"]:
        pal = [Palabra(w[0], w[1], w[2], w[3], w[4], CONF_TEXTO_DUDOSO if (len(w) > 5 and w[5]) or _cifras_no_ascii(w[0]) else 1.0)
               for w in pg_["palabras"]]
        if pg_.get("cifras_dibujo_libre", []) != []:
            cifras[pg_["numero"]] = pg_.get("cifras_dibujo_libre")
        if pg_.get("blancos_junto_a_cifras", []) != []:
            blancos[pg_["numero"]] = pg_.get("blancos_junto_a_cifras")
        if any(len(w) > 5 and w[5] == DUDOSO_SOBRE_PAPEL for w in pg_["palabras"]):
            sobre_papel[pg_["numero"]] = [i for i, w in enumerate(pg_["palabras"]) if len(w) > 5 and w[5] == DUDOSO_SOBRE_PAPEL]
        invisibles = pg_.get("invisibles", 0)
        pg = Pagina(pg_["numero"], pg_["ancho"], pg_["alto"], pal, texto_oculto=invisibles + pg_.get("fuera", 0))
        n = sum(len(p.texto) for p in pal)
        # Sin texto, con una capa de texto invisible sobre lo que muestra una imagen (escaneo «con OCR» o capa oculta)
        # o con más texto invisible que visible, se lee lo que se ve: OCR propio de la imagen de la página. Unas pocas
        # líneas invisibles en una factura nativa (marca del programa de facturación, también sobre una imagen de fondo
        # lisa) no tapan nada: ya se han descartado y el texto visible sigue siendo el nativo
        if n < MIN_CARACTERES_PAGINA or (invisibles >= MIN_CARACTERES_PAGINA and invisibles >= n):
            necesitan_ocr.append(pg.numero)
        elif pg_.get("invisibles_imagen", invisibles) >= MIN_CARACTERES_PAGINA:
            capas_en_imagen[pg.numero] = pg_.get("cajas_invisibles_imagen")
        paginas.append(pg)
    # Texto nativo sin tinta en la imagen de la página (oculto por un mecanismo que no se reconoce: glifos que no
    # dibujan, modos de fusión, máscaras…): dudoso. Con el texto mayoritariamente dudoso, la página se lee por OCR
    necesitan_ocr += _marcar_sin_tinta(datos, [pg for pg in paginas if pg.numero not in necesitan_ocr and pg.palabras],
                                       {pg_["numero"]: pg_.get("recorte") for pg_ in res["paginas"]}, capas_en_imagen,
                                       sobre_papel, blancos) or []
    # Cifras de fuentes cuyo texto no se deduce del dibujo: se leen en la imagen y, si no coinciden, son dudosas
    _verificar_cifras(datos, [pg for pg in paginas if pg.numero not in necesitan_ocr and pg.numero in cifras], cifras,
                      {pg_["numero"]: pg_.get("recorte") for pg_ in res["paginas"]})
    # Las cifras de otros sistemas de escritura siguen dudosas aunque otra comprobación haya rehabilitado la palabra
    for pg in paginas:
        if pg.numero not in necesitan_ocr:
            pg.palabras = [Palabra(w.texto, w.x0, w.top, w.x1, w.bottom, CONF_TEXTO_DUDOSO, w.metodo)
                           if w.conf >= 1.0 and _cifras_no_ascii(w.texto) else w for w in pg.palabras]
    for pg in paginas:
        n = sum(len(p.texto) for p in pg.palabras)
        dudosos = sum(len(p.texto) for p in pg.palabras if p.conf < 1.0)
        if pg.numero not in necesitan_ocr and dudosos >= MIN_CARACTERES_PAGINA and 2 * dudosos >= n:
            necesitan_ocr.append(pg.numero)
    necesitan_ocr.sort()
    limite = ajustes().max_paginas_ocr
    for i in [n for n in necesitan_ocr if n <= limite]:
        with tempfile.TemporaryDirectory() as td:
            pngs = rasterizar(datos, Path(td), dpi=DPI_OCR, primera=i, ultima=i, gris=True)
            if not pngs:
                continue
            pg = paginas[i - 1]
            pg.palabras = _ocr_pagina(pngs[0], pg.ancho, pg.alto)
            pg.metodo = "ocr"
            confs = [p.conf for p in pg.palabras if len(p.texto) > 1]
            pg.conf_media = round(sum(confs) / len(confs), 3) if confs else 0.0
    return paginas


def _marcar_sin_tinta(datos: bytes, paginas: list[Pagina], recortes: dict, capas_en_imagen: dict | None = None,
                      sobre_papel: dict | None = None, blancos: dict | None = None) -> list[int]:
    """Baja a CONF_TEXTO_DUDOSO las palabras nativas en cuya caja la imagen de la página (la que ve el revisor) no tiene
    contraste: no se ven. Una sola representación en gris a baja resolución para todas las páginas.
    capas_en_imagen: por página, las cajas (x0, top, x1, bottom, caracteres) del texto invisible dibujado sobre una
    imagen (None: no se conocen). Devuelve las páginas en las que esa capa está sobre tinta de la imagen que ninguna
    palabra visible explica (lo que se ve es la imagen: escaneo «con OCR» o capa oculta): se leen por OCR.
    sobre_papel: por página, los índices de las palabras dudosas solo por pintar del color del papel sin un fondo
    reconocido debajo (degradado muestreado o calculado, malla, modo de fusión…): vuelven a ser seguras si en la imagen
    se ven claras sobre un fondo más oscuro (sin contar la tinta de las demás palabras).
    blancos: por página, los caracteres de texto en blanco junto a una cifra cuyo glifo puede dibujar algo (x0, top, x1,
    bottom, seguro[, sin_contornos]; None: demasiados). Si dibujan (tinta en la parte central de su caja, fuera de las
    palabras) o su /ToUnicode ya contradice el glifo (seguro), las palabras contiguas del renglón son dudosas: falta una
    cifra que se ve. Si el glifo del blanco no tiene contornos en la fuente incrustada (sin_contornos), la tinta de una
    línea recta larga que cruza su caja no cuenta (_reglas_que_cruzan): es de un recuadro o de una tabla, no del blanco."""
    capas_en_imagen, sobre_papel, blancos = capas_en_imagen or {}, sobre_papel or {}, blancos or {}
    if not paginas:
        return []
    import numpy as np
    from PIL import Image
    al_ocr = []
    primera, ultima = min(p.numero for p in paginas), max(p.numero for p in paginas)
    with tempfile.TemporaryDirectory() as td:
        pngs = rasterizar(datos, Path(td), dpi=DPI_TINTA, gris=True, primera=primera, ultima=ultima)
        for pg in paginas:
            k = pg.numero - primera
            if k >= len(pngs):
                if pg.numero in capas_en_imagen:
                    al_ocr.append(pg.numero)   # sin imagen con la que comprobarlo: se lee lo que se ve
                continue
            with Image.open(pngs[k]) as im:
                a = np.asarray(im.convert("L"))
            cx0, ctop, cx1, _ = recortes.get(pg.numero) or (0.0, 0.0, pg.ancho, pg.alto)
            s = a.shape[1] / max(cx1 - cx0, 1.0)

            def zona(x0, top, x1, bottom):
                m = 0.25 * max(bottom - top, 1.0)   # margen: los anchos de la fuente pueden no cuadrar del todo
                return (max(0, int((x0 - cx0 - m) * s)), max(0, int((top - ctop) * s)),
                        min(a.shape[1], int((x1 - cx0 + m) * s) + 1), min(a.shape[0], int((bottom - ctop) * s) + 1))
            if pg.numero in capas_en_imagen:
                cajas = capas_en_imagen[pg.numero]
                if cajas is None:
                    al_ocr.append(pg.numero)
                    continue
                # Caracteres invisibles sobre tinta de la imagen, sin contar la tinta de las palabras visibles
                visibles = np.zeros(a.shape, dtype=bool)
                for w in pg.palabras:
                    x0, y0, x1, y1 = zona(w.x0, w.top, w.x1, w.bottom)
                    visibles[y0:y1, x0:x1] = True
                con_tinta = 0
                for c in cajas:
                    x0, y0, x1, y1 = zona(*c[:4])
                    if x1 > x0 and y1 > y0:
                        z = a[y0:y1, x0:x1][~visibles[y0:y1, x0:x1]]
                        if z.size and int(z.max()) - int(z.min()) >= CONTRASTE_MIN_TINTA:
                            con_tinta += int(c[4])
                # bastantes caracteres, o la mitad de la capa (una capa de OCR algo desplazada de la imagen)
                if con_tinta >= MIN_CARACTERES_PAGINA or 2 * con_tinta >= sum(int(c[4]) for c in cajas):
                    al_ocr.append(pg.numero)
                    continue
            if sobre_papel.get(pg.numero):
                otras = np.zeros(a.shape, dtype=bool)
                for w in pg.palabras:
                    if w.conf >= 1.0:
                        x0, y0, x1, y1 = zona(w.x0, w.top, w.x1, w.bottom)
                        otras[y0:y1, x0:x1] = True
                for i in sobre_papel[pg.numero]:
                    w = pg.palabras[i] if i < len(pg.palabras) else None
                    if w is None or w.metodo != "texto_pdf":
                        continue
                    x0, y0, x1, y1 = zona(w.x0, w.top, w.x1, w.bottom)
                    if x1 <= x0 or y1 <= y0:
                        continue
                    z = a[y0:y1, x0:x1][~otras[y0:y1, x0:x1]]
                    # letra clara sobre un fondo (la mayoría de la caja) más oscuro: se ve
                    if 2 * z.size >= (x1 - x0) * (y1 - y0) and int(z.max()) - int(np.median(z)) >= CONTRASTE_MIN_TINTA:
                        pg.palabras[i] = Palabra(w.texto, w.x0, w.top, w.x1, w.bottom, 1.0, w.metodo)
            if pg.numero in blancos:
                _blancos_que_dibujan(pg, blancos[pg.numero], a, s, cx0, ctop)
            for i, w in enumerate(pg.palabras):
                if w.conf < 1.0:
                    continue
                x0, y0, x1, y1 = zona(w.x0, w.top, w.x1, w.bottom)
                if x1 <= x0 or y1 <= y0:
                    continue
                z = a[y0:y1, x0:x1]
                if int(z.max()) - int(z.min()) < CONTRASTE_MIN_TINTA:
                    pg.palabras[i] = Palabra(w.texto, w.x0, w.top, w.x1, w.bottom, CONF_TEXTO_DUDOSO, w.metodo)
    return al_ocr


def _blancos_que_dibujan(pg: Pagina, cajas: list | None, a, s: float, cx0: float, ctop: float) -> None:
    """Palabras con cifras contiguas a un carácter en blanco que en la imagen dibuja algo: dudosas (ver _marcar_sin_tinta).
    a: imagen de la página en gris; s: píxeles por punto; (cx0, ctop): origen de la zona representada."""
    import numpy as np
    if cajas is None:   # demasiados para comprobarlos
        dibujan = None
    else:
        # las palabras (con un margen para el suavizado de sus bordes) no cuentan como tinta del blanco
        palabras = np.zeros(a.shape, dtype=bool)
        for w in pg.palabras:
            m = max(1.5 / s, 0.08 * (w.bottom - w.top))
            palabras[max(0, int((w.top - ctop) * s)):max(0, int((w.bottom - ctop) * s) + 1),
                     max(0, int((w.x0 - m - cx0) * s)):max(0, int((w.x1 + m - cx0) * s) + 1)] = True
        dibujan = []
        for c in cajas:
            x0, top, x1, bottom = c[:4]
            if not c[4]:
                h, an = max(bottom - top, 1.0), max(x1 - x0, 0.0)
                caja = (max(0, int((top - ctop) * s)), min(a.shape[0], int((bottom - ctop) * s) + 1),
                        max(0, int((x0 - cx0) * s)), min(a.shape[1], int((x1 - cx0) * s) + 1))
                # parte central: por encima de la línea base (sin subrayados) y sin los bordes laterales
                y0, y1 = max(0, int((top + 0.15 * h - ctop) * s)), min(a.shape[0], int((top + 0.6 * h - ctop) * s) + 1)
                i0, i1 = max(0, int((x0 + 0.2 * an - cx0) * s)), min(a.shape[1], int((x1 - 0.2 * an - cx0) * s) + 1)
                if y1 <= y0 or i1 <= i0 or caja[1] <= caja[0] or caja[3] <= caja[2]:
                    continue
                libre = ~palabras[y0:y1, i0:i1]
                z = a[y0:y1, i0:i1][libre].astype(int)
                fondo = a[caja[0]:caja[1], caja[2]:caja[3]][~palabras[caja[0]:caja[1], caja[2]:caja[3]]]
                if z.size < 4 or not fondo.size:
                    continue
                papel = int(np.median(fondo))
                oscuro = np.abs(z - papel) >= 60
                if len(c) > 5 and c[5]:
                    # el glifo no dibuja nada: la tinta de una línea recta larga que cruza su caja es de otra cosa (borde de
                    # un recuadro, separador de columnas); lo demás que haya encima sigue contando. Con un glifo que dibuja
                    # algo no se quita: el palo de un «1» cruzado por una línea es tinta del glifo
                    oscuro &= ~_reglas_que_cruzan(a, papel, (y0, y1, i0, i1), caja, h * s)[libre]
                if int(oscuro.sum()) < max(3, 0.12 * z.size):
                    continue
            dibujan.append((x0, top, x1, bottom))
    for i, w in enumerate(pg.palabras):
        if w.conf < 1.0 or not re.search(r"[0-9]", w.texto):
            continue
        h = max(w.bottom - w.top, 1.0)
        if dibujan is None or any(min(w.bottom, b) - max(w.top, t) >= 0.5 * min(h, b - t) and w.x0 - h <= x1 and x0 <= w.x1 + h
                                  for x0, t, x1, b in dibujan):
            pg.palabras[i] = Palabra(w.texto, w.x0, w.top, w.x1, w.bottom, CONF_TEXTO_DUDOSO, w.metodo)


def _reglas_que_cruzan(a, papel: int, zona: tuple, caja: tuple, h: float):
    """Máscara de la zona (y0, y1, i0, i1) de la imagen en gris `a` con la tinta de una línea recta larga que cruza la caja
    de un carácter (caja: filas y columnas en píxeles; h: alto del renglón en píxeles; papel: gris del fondo):
    - una columna oscura en toda la altura de la zona cuyo tramo continuo mide 1,2 renglones o más (borde vertical de un
      recuadro, aunque sea de un solo renglón; separador de columnas de una tabla);
    - una fila oscura en todo el ancho de la zona que continúa sin cortes un renglón o más a izquierda y a derecha de la
      caja (línea horizontal).
    Una cifra del tamaño del texto no traza segmentos rectos tan largos (su altura es unos dos tercios del renglón): el
    resto de la tinta de la zona sigue contando. Solo para blancos cuyo glifo no dibuja nada (ver _blancos_que_dibujan)."""
    import numpy as np
    y0, y1, i0, i1 = zona
    alto, ancho = a.shape
    m = np.zeros((y1 - y0, i1 - i0), dtype=bool)
    largo = max(4, int(round(1.2 * h)))
    ya, yb = max(0, y0 - largo), min(alto, y1 + largo)
    osc = np.abs(a[ya:yb, i0:i1].astype(int) - papel) >= 60
    for j in range(i1 - i0):
        c = osc[:, j]
        if not c[y0 - ya:y1 - ya].all():
            continue
        k, q = y0 - ya, y1 - ya
        while k > 0 and c[k - 1]:
            k -= 1
        while q < len(c) and c[q]:
            q += 1
        if q - k >= largo:
            m[:, j] = True
    ext = max(2, int(round(h)))
    xa, xb = max(0, caja[2] - ext), min(ancho, caja[3] + ext)
    osc = np.abs(a[y0:y1, xa:xb].astype(int) - papel) >= 60
    for r in range(y1 - y0):
        f = osc[r]
        if f.all():   # de un extremo a otro de la ventana, que deja un renglón a cada lado de la caja
            m[r, :] = True
    return m


def _cifras_no_ascii(texto: str) -> bool:
    """Cifras de otro sistema de escritura («９», «٩», «𝟗»): Python las lee como números, pero una factura española no
    las usa y su /ToUnicode puede decir una cifra distinta de la que dibuja la fuente sin que el contraste de
    _verificar_cifras (solo cifras ASCII) lo vea. La palabra es dudosa (los superíndices como «m²» no son decimales)."""
    return any(ch.isdecimal() and not "0" <= ch <= "9" for ch in str(texto))


def _verificar_cifras(datos: bytes, paginas: list[Pagina], cifras: dict, recortes: dict) -> dict:
    """Contrasta con la imagen de la página las cifras de las fuentes de dibujo libre (incrustadas, Type3, CID): lo que
    dibuja cada glifo lo decide la propia fuente y su texto lo da el /ToUnicode o el nombre del glifo, que pueden no
    corresponder (se ve «200,00» y el texto dice «900,00»). Un glifo (fuente y código) dibuja siempre lo mismo y dice
    siempre lo mismo: se leen por OCR unas pocas palabras de cada glifo de cifra (VECES_GLIFO) y un glifo es dudoso si la
    lectura lo contradice más veces de las que lo confirma o, si es de una cifra, si no se ha podido contrastar. Las
    palabras con algún glifo dudoso bajan a CONF_TEXTO_DUDOSO. cifras: por página, los tramos numéricos de
    texto_proceso._tramo_numerico (None: demasiados para contrastarlos; todas las palabras con cifras son dudosas).
    Devuelve el detalle de la comprobación."""
    import numpy as np
    from PIL import Image, ImageOps
    candidatos = []   # (prioridad, página, tramo)
    for pg in paginas:
        lista = cifras.get(pg.numero)
        if lista is None:
            for i, w in enumerate(pg.palabras):
                if re.search(r"[0-9]", w.texto):
                    pg.palabras[i] = Palabra(w.texto, w.x0, w.top, w.x1, w.bottom, CONF_TEXTO_DUDOSO, w.metodo)
            continue
        for e in lista:
            if not isinstance(e, dict) or not isinstance(e.get("i"), int) or not 0 <= e["i"] < len(pg.palabras):
                continue
            importe = bool(re.search(r"\d[.,]\d{1,2}%?$|\d%$", pg.palabras[e["i"]].texto))
            candidatos.append(((not importe, -len(e["glifos"]), pg.numero, e["caja"][1]), pg, e))
    if not candidatos:
        return {}
    # Palabras que leer: primero los importes y, entre ellos, las de más glifos, mientras alguno quede por contrastar
    candidatos.sort(key=lambda c: c[0])
    vistas: dict[str, int] = {}
    elegidos: list = []
    usadas: set = set()
    for _, pg, e in candidatos:
        if len(elegidos) >= MAX_TRAMOS_CIFRAS:
            break
        if any(vistas.get(k, 0) < VECES_GLIFO for k in e["glifos"]) and (pg.numero in usadas or len(usadas) < MAX_PAGINAS_CIFRAS):
            usadas.add(pg.numero)
            elegidos.append((pg, e))
            for k in e["glifos"]:
                vistas[k] = vistas.get(k, 0) + 1
    # Cada palabra, recortada de la página representada, en negro sobre blanco y a la misma altura (_leer_tira las lee
    # todas de una vez, un renglón por palabra)
    recs = []
    with tempfile.TemporaryDirectory() as td:
        for num in sorted(usadas):
            try:
                imgs = rasterizar(datos, Path(td) / str(num), dpi=DPI_CIFRAS, gris=True, primera=num, ultima=num, png=False)
            except Exception:  # noqa: BLE001 - sin imagen, sus glifos quedan sin contrastar (dudosos)
                imgs = []
            if not imgs:
                continue
            pg = next(p for p in paginas if p.numero == num)
            with Image.open(imgs[0]) as im:
                g = im.convert("L")
            cx0, ctop, cx1, _ = recortes.get(num) or (0.0, 0.0, pg.ancho, pg.alto)
            esc = g.width / max(cx1 - cx0, 1.0)
            for pg_e, e in elegidos:
                if pg_e.numero != num:
                    continue
                # solo el tramo de cifras: sin los caracteres vecinos de la palabra (letras, «%», «€»)
                x0, top, x1, bottom = e["caja"]
                h = max(bottom - top, 1.0)
                izq, der = x0 - 0.1 * h, x1 + 0.1 * h
                if e.get("izq") is not None:
                    izq = max(izq, (e["izq"] + x0) / 2)
                if e.get("der") is not None:
                    der = min(der, (x1 + e["der"]) / 2)
                caja = (max(0, int((izq - cx0) * esc)), max(0, int((top - ctop - 0.1 * h) * esc)),
                        min(g.width, int((der - cx0) * esc) + 1), min(g.height, int((bottom - ctop + 0.1 * h) * esc) + 1))
                if caja[2] - caja[0] < 2 or caja[3] - caja[1] < 2:
                    continue
                # Un tramo de cifras no es mucho más ancho que alto por carácter: uno estirado (Tz, matriz de texto) daría
                # recortes de miles de píxeles y un OCR de minutos. No se lee: sus glifos quedan sin contrastar (dudosos)
                ancho_max = min(MAX_ASPECTO_TRAMO, len(pg_e.palabras[e["i"]].texto) + 2)
                if (caja[2] - caja[0]) > ancho_max * (caja[3] - caja[1]):
                    continue
                r = g.crop(caja)
                if float(np.median(np.asarray(r))) < 128:
                    r = ImageOps.invert(r)   # letra clara sobre fondo oscuro
                r = ImageOps.autocontrast(r)
                r = r.resize((max(1, round(r.width * ALTO_TRAMO_PX / r.height)), ALTO_TRAMO_PX), Image.LANCZOS)
                recs.append((e, r))
        # Dos lecturas con modelos distintos: la segunda solo de las palabras que la primera no confirma
        lect1 = _leer_tira([r for _, r in recs], Path(td), "spa") or {}
        pend = [i for i, (e, _) in enumerate(recs) if re.sub(r"[^0-9]", "", lect1.get(i, ("", 0.0))[0]) != e["digitos"]]
        lect2 = {}
        if pend:
            otra = _leer_tira([recs[i][1] for i in pend], Path(td), "eng")
            if otra is None:   # sin el modelo inglés: el mismo español a otra escala
                otra = _leer_tira([recs[i][1].resize((max(1, recs[i][1].width * 3 // 4), ALTO_TRAMO_PX * 3 // 4), Image.LANCZOS)
                                   for i in pend], Path(td), "spa") or {}
            lect2 = {pend[j]: v for j, v in otra.items()}
    # Votos por glifo: confirma si alguna lectura da los dígitos del texto; contradice si las dos leen con claridad las
    # mismas otras cifras, tantas como dice el texto (un glifo que dice una cifra y dibuja otra). Las demás lecturas
    # (cifras de más o de menos, sin confianza) no cuentan: son los fallos habituales del OCR
    votos: dict[str, list[int]] = {}
    detalle: dict = {"leidas": [], "palabras_dudosas": []}
    for i, (e, _) in enumerate(recs):
        (t1, c1), (t2, c2) = lect1.get(i, ("", 0.0)), lect2.get(i, ("", 0.0))
        d1, d2 = re.sub(r"[^0-9]", "", t1), re.sub(r"[^0-9]", "", t2)
        detalle["leidas"].append((e["digitos"], t1, round(c1, 2), t2, round(c2, 2)))
        if e["digitos"] in (d1, d2):
            voto = 0
        elif d1 == d2 and len(d1) == len(e["digitos"]) and min(c1, c2) >= CONF_CONTRADICE:
            voto = 1
        else:
            continue
        for k in e["glifos"] + e.get("signos", []):
            votos.setdefault(k, [0, 0])[voto] += 1
    dudosos = {k for _, _, e in candidatos for k in e["glifos"] if k not in votos}
    dudosos |= {k for k, (si, no) in votos.items() if no > si}
    detalle["votos"], detalle["glifos_dudosos"] = votos, sorted(dudosos)
    for _, pg, e in candidatos:
        w = pg.palabras[e["i"]]
        if dudosos.intersection(e["glifos"] + e.get("signos", [])) and w.conf >= 1.0:
            pg.palabras[e["i"]] = Palabra(w.texto, w.x0, w.top, w.x1, w.bottom, CONF_TEXTO_DUDOSO, w.metodo)
            detalle["palabras_dudosas"].append(w.texto)
    return detalle


def _leer_tira(recortes: list, carpeta: Path, idioma: str) -> dict[int, tuple[str, float]] | None:
    """OCR de una sola imagen con los recortes (todos de la misma altura) uno debajo de otro: {n.º de recorte: (texto
    leído, confianza mínima)}. None si el OCR falla (p. ej. sin el modelo del idioma) o agota el tiempo."""
    import os

    from PIL import Image
    from .inspeccion import ejecutar
    if not recortes:
        return {}
    alto = recortes[0].height
    hueco = alto // 2
    tira = Image.new("L", (max(r.width for r in recortes) + 2 * hueco, hueco + len(recortes) * (alto + hueco)), 255)
    for i, r in enumerate(recortes):
        tira.paste(r, (hueco, hueco + i * (alto + hueco)))
    ruta = carpeta / f"tira_{idioma}.png"
    tira.save(ruta)
    try:
        res = ejecutar(["tesseract", str(ruta), "stdout", "-l", idioma, "--psm", "6", "-c",
                        "tessedit_char_whitelist=0123456789.,'/%-", "tsv"], timeout=ajustes().timeout_ocr_s, text=True,
                       env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    except subprocess.TimeoutExpired:
        return None
    if res.returncode != 0:
        return None
    filas: dict[int, list] = {}
    for f in csv.DictReader(io.StringIO(res.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        t = (f.get("text") or "").strip()
        if not t or f.get("level") != "5":
            continue
        try:
            i = int((int(f["top"]) + int(f["height"]) / 2 - hueco / 2) // (alto + hueco))
            conf = float(f["conf"]) / 100.0
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(recortes):
            filas.setdefault(i, []).append((int(f["left"]), t, conf))
    return {i: ("".join(t for _, t, _ in sorted(ws)), min(c for _, _, c in ws)) for i, ws in filas.items()}


def lineas(paginas: list[Pagina], tolerancia: float = 3.0) -> list[Linea]:
    """Agrupa palabras en líneas visuales (por página, ordenadas de arriba abajo e izquierda a derecha).

    Se separan en bloques distintos los textos de una misma altura muy alejados horizontalmente
    (p. ej. dos columnas), para no mezclar los datos del emisor con los del cliente.
    """
    out: list[Linea] = []
    for pg in paginas:
        pal = sorted(pg.palabras, key=lambda p: (round(p.top), p.x0))
        grupos: list[list[Palabra]] = []
        # Coste lineal: solo se prueban los grupos «abiertos». Un grupo cuya primera palabra termina por encima de
        # round(top) − 0,5 ya no puede solaparse con ninguna palabra posterior (vienen ordenadas por round(top)).
        # Con alturas negativas (no deberían darse) se prueban todos, como siempre.
        podar = all(p.bottom >= p.top for p in pal)
        abiertos: list[int] = []
        tramos: list[dict] = []   # por grupo, los (top, bottom) distintos: la condición de solape solo depende de ellos
        nucleo: list[list[float]] = []   # por grupo, [mayor top, menor bottom]: la franja común a todas sus palabras
        r_ant = None
        for p in pal:
            r = round(p.top)
            if podar and r != r_ant:
                abiertos = [i for i in abiertos if grupos[i][0].bottom >= r - 0.5]
                r_ant = r
            hp = p.bottom - p.top
            # Solo los grupos abiertos más recientes: en una página real hay pocos a la vez; muchos (glifos diminutos a
            # alturas distintas, documento hostil) no deben hacer cuadrático el agrupado
            for i in (abiertos[-MAX_GRUPOS_ABIERTOS:] if podar else range(len(grupos))):
                g = grupos[i]
                ref = g[0]
                # Con alturas distintas (logotipo grande junto a texto normal) manda la menor, salvo que compartan línea base
                dc = abs(((p.top + p.bottom) / 2) - ((ref.top + ref.bottom) / 2))
                hr = ref.bottom - ref.top
                cerca = dc <= tolerancia + 0.25 * min(hp, hr) or (abs(p.bottom - ref.bottom) <= 1.5 and dc <= tolerancia + 0.25 * max(hp, hr))
                if not cerca:
                    continue
                # y se solapa en vertical con todas las palabras del grupo (dos renglones distintos nunca se mezclan). Con
                # muchas alturas distintas (renglón inclinado) basta el solape con la franja común, que es más exigente
                if len(tramos[i]) <= MAX_TRAMOS_EXACTOS:
                    solapa = all(min(p.bottom, qb) - max(p.top, qt) >= 0.5 * min(hp, qb - qt) for qt, qb in tramos[i])
                else:
                    solapa = min(p.bottom, nucleo[i][1]) - max(p.top, nucleo[i][0]) >= 0.5 * hp
                if solapa:
                    g.append(p)
                    tramos[i].setdefault((p.top, p.bottom), None)
                    nucleo[i][0], nucleo[i][1] = max(nucleo[i][0], p.top), min(nucleo[i][1], p.bottom)
                    break
            else:
                grupos.append([p])
                tramos.append({(p.top, p.bottom): None})
                nucleo.append([p.top, p.bottom])
                abiertos.append(len(grupos) - 1)
        for g in grupos:
            g.sort(key=lambda p: p.x0)
            # Umbral de corte relativo al espaciado normal de la propia línea: separa columnas aunque estén
            # próximas (p. ej. el texto de un logotipo y la dirección del cliente) sin partir texto justificado.
            huecos = sorted(b.x0 - a.x1 for a, b in zip(g, g[1:]) if b.x0 - a.x1 > 0)
            # espaciado normal de la línea: el cuartil inferior de los huecos (en una rejilla la mayoría son huecos entre columnas)
            mediana = huecos[len(huecos) // 4] if huecos else 3.0
            bloque = [g[0]]
            for p in g[1:]:
                hueco = p.x0 - bloque[-1].x1
                alto = max(1.0, bloque[-1].bottom - bloque[-1].top)
                if hueco > max(40.0, 4.5 * alto) or hueco > max(12.0, 5 * mediana, 1.6 * alto):
                    out.append(Linea(pg.numero, bloque))
                    bloque = [p]
                else:
                    bloque.append(p)
            out.append(Linea(pg.numero, bloque))
    out.sort(key=lambda l: (l.pagina, round(l.top), l.x0))
    return out


def a_dict(paginas: list[Pagina]) -> list[dict]:
    return [{"numero": p.numero, "ancho": p.ancho, "alto": p.alto, "metodo": p.metodo,
             "conf_media": p.conf_media, "n_palabras": len(p.palabras)} for p in paginas]


def palabra_a_dict(p: Palabra) -> dict:
    return asdict(p)
