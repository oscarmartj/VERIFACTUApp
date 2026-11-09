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
from pathlib import Path

import pdfplumber

from ..config import ajustes
from .inspeccion import rasterizar

MIN_CARACTERES_PAGINA = 40
DPI_OCR = 300


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
    pagina: int
    palabras: list[Palabra]

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palabras)

    @property
    def x0(self): return min(p.x0 for p in self.palabras)

    @property
    def x1(self): return max(p.x1 for p in self.palabras)

    @property
    def top(self): return min(p.top for p in self.palabras)

    @property
    def bottom(self): return max(p.bottom for p in self.palabras)

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


def _sin_lineas(g):
    """Borra las líneas largas horizontales y verticales (bordes de tablas y cajas): con ellas Tesseract suele
    saltarse el texto de las celdas. Las letras no tienen trazos rectos tan largos."""
    import numpy as np
    from PIL import Image
    a = np.asarray(g)
    tinta = (a < 200).astype(np.int32)
    largo = max(40, g.width // 30)  # ≈ 7 mm en A4

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
        idx_fin = np.minimum(np.arange(n), k - 1) + 1
        idx_ini = np.maximum(np.arange(n) - largo + 1, 0)
        return (cf.take(idx_fin, axis=eje) - cf.take(idx_ini, axis=eje)) > 0
    horiz, vert = cubiertos(tinta, 1), cubiertos(tinta, 0)
    # solo líneas finas: el trazo de una letra grande es grueso (hay tinta a 6 px a ambos lados)
    t = tinta.astype(bool)
    e = max(6, g.width // 400)
    if horiz.any():
        arriba = np.zeros_like(t)
        abajo = np.zeros_like(t)
        arriba[e:, :] = t[:-e, :]
        abajo[:-e, :] = t[e:, :]
        horiz &= ~(arriba & abajo)
    if vert.any():
        izq = np.zeros_like(t)
        der = np.zeros_like(t)
        izq[:, e:] = t[:, :-e]
        der[:, :-e] = t[:, e:]
        vert &= ~(izq & der)
    if not (horiz.any() or vert.any()):
        return g
    # el borde difuminado de la línea también se borra (2 px a cada lado)
    lineas = horiz | vert
    for d in (1, 2):
        lineas[d:, :] |= horiz[:-d, :]
        lineas[:-d, :] |= horiz[d:, :]
        lineas[:, d:] |= vert[:, :-d]
        lineas[:, :-d] |= vert[:, d:]
    b = a.copy()
    b[lineas] = 255
    return Image.fromarray(b)


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
    for pg_ in res["paginas"]:
        pal = [Palabra(t, x0, top, x1, bottom) for t, x0, top, x1, bottom in pg_["palabras"]]
        pg = Pagina(pg_["numero"], pg_["ancho"], pg_["alto"], pal)
        if sum(len(p.texto) for p in pal) < MIN_CARACTERES_PAGINA:
            necesitan_ocr.append(pg.numero)
        paginas.append(pg)
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


def lineas(paginas: list[Pagina], tolerancia: float = 3.0) -> list[Linea]:
    """Agrupa palabras en líneas visuales (por página, ordenadas de arriba abajo e izquierda a derecha).

    Se separan en bloques distintos los textos de una misma altura muy alejados horizontalmente
    (p. ej. dos columnas), para no mezclar los datos del emisor con los del cliente.
    """
    out: list[Linea] = []
    for pg in paginas:
        pal = sorted(pg.palabras, key=lambda p: (round(p.top), p.x0))
        grupos: list[list[Palabra]] = []
        for p in pal:
            for g in grupos:
                ref = g[0]
                # Con alturas distintas (logotipo grande junto a texto normal) manda la menor, salvo que compartan línea base
                dc = abs(((p.top + p.bottom) / 2) - ((ref.top + ref.bottom) / 2))
                hp, hr = p.bottom - p.top, ref.bottom - ref.top
                cerca = dc <= tolerancia + 0.25 * min(hp, hr) or (abs(p.bottom - ref.bottom) <= 1.5 and dc <= tolerancia + 0.25 * max(hp, hr))
                # y se solapa en vertical con todas las palabras del grupo (dos renglones distintos nunca se mezclan)
                if cerca and all(min(p.bottom, q.bottom) - max(p.top, q.top) >= 0.5 * min(hp, q.bottom - q.top) for q in g):
                    g.append(p)
                    break
            else:
                grupos.append([p])
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
