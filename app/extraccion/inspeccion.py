"""Inspección de seguridad y estructura del PDF antes de procesarlo.

El original nunca se modifica ni se ejecuta: se analiza como datos. Se detectan contenido
activo (JavaScript, acciones automáticas, adjuntos, XFA), cifrado, firmas digitales y QR
tributarios ya presentes. La visualización en la interfaz usa imágenes rasterizadas por el
servidor, de modo que el navegador nunca interpreta el PDF original.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader

from ..config import ajustes

PATRONES_ACTIVOS = {
    "javascript": rb"/(JavaScript|JS)\b",
    "accion_apertura": rb"/OpenAction\b",
    "acciones_automaticas": rb"/AA\b",
    "lanzar_aplicacion": rb"/Launch\b",
    "adjuntos": rb"/EmbeddedFiles?\b",
    "multimedia": rb"/RichMedia\b",
    "formulario_xfa": rb"/XFA\b",
    "envio_formulario": rb"/SubmitForm\b",
}


class PDFNoValido(Exception):
    pass


def validar_basico(datos: bytes, nombre: str) -> None:
    a = ajustes()
    if len(datos) == 0:
        raise PDFNoValido("El archivo está vacío")
    if len(datos) > a.max_mb_pdf * 1024 * 1024:
        raise PDFNoValido(f"El archivo supera el límite de {a.max_mb_pdf} MB")
    if b"%PDF-" not in datos[:1024]:
        raise PDFNoValido("El archivo no es un PDF")
    if not nombre.lower().endswith(".pdf"):
        raise PDFNoValido("La extensión del archivo debe ser .pdf")


MAX_DESCOMPRIMIDO = 200 * 1024 * 1024    # 200 MB de flujos descomprimidos como máximo
MAX_LADO_PAGINA_PT = 5000                  # ~1,76 m: páginas mayores se rechazan (protección de memoria)
MAX_PX_LADO = 4000                         # lado máximo de una página rasterizada
MAX_PX_IMAGEN = 50_000_000                 # 50 megapíxeles por imagen incrustada
MEMORIA_HERRAMIENTAS_MB = 1536             # límite de memoria de qpdf, pdftoppm, tesseract y zbarimg


def ejecutar(cmd: list[str], *, timeout: int = 120, mem_mb: int = MEMORIA_HERRAMIENTAS_MB, fsize: int | None = None,
             text: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    """Ejecuta una herramienta externa sobre datos no fiables con límites de memoria, CPU y tamaño de fichero."""
    def limites():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 5))
        if fsize:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    return subprocess.run(cmd, capture_output=True, timeout=timeout, preexec_fn=limites, text=text, env=env)


def _buscar_activos(datos: bytes) -> list[str]:
    """Busca nombres de contenido activo en la versión con flujos expandidos, leyendo por bloques y con límites
    de tamaño, memoria y CPU (protección frente a «bombas» de compresión). Si qpdf no puede procesarlo, se rechaza."""
    with tempfile.TemporaryDirectory() as td:
        ent, sal = Path(td) / "e.pdf", Path(td) / "s.pdf"
        ent.write_bytes(datos)
        try:
            r = ejecutar(["qpdf", "--qdf", "--object-streams=disable", "--decrypt", str(ent), str(sal)],
                         timeout=60, fsize=MAX_DESCOMPRIMIDO)
        except subprocess.TimeoutExpired as e:
            raise PDFNoValido("El PDF tarda demasiado en analizarse") from e
        if r.returncode not in (0, 3) or not sal.exists():
            raise PDFNoValido("El PDF no puede analizarse con seguridad (estructura dañada o flujos desproporcionados)")
        encontrados: set[str] = set()
        solape = 64
        previo = b""
        with open(sal, "rb") as f:
            while True:
                bloque = f.read(4 * 1024 * 1024)
                if not bloque:
                    break
                ventana = previo + bloque
                for k, pat in PATRONES_ACTIVOS.items():
                    if k not in encontrados and re.search(pat, ventana):
                        encontrados.add(k)
                previo = ventana[-solape:]
        return sorted(encontrados)


def info_poppler(datos: bytes, max_paginas: int) -> dict:
    """Número y tamaño de páginas según el motor que realmente rasteriza (poppler), para que pypdf y poppler no
    puedan discrepar (p. ej. con un trailer duplicado)."""
    with tempfile.TemporaryDirectory() as td:
        ent = Path(td) / "e.pdf"
        ent.write_bytes(datos)
        r = ejecutar(["pdfinfo", "-f", "1", "-l", str(max_paginas), "-box", str(ent)], timeout=30, text=True)
    if r.returncode != 0:
        raise PDFNoValido("El PDF no puede leerse con el motor de representación")
    paginas, cajas = None, {}
    for linea in r.stdout.splitlines():
        m = re.match(r"^Pages:\s+(\d+)", linea)
        if m:
            paginas = int(m.group(1))
        m = re.match(r"^Page\s+(\d+)\s+(MediaBox|CropBox):\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", linea)
        if m:
            x0, y0, x1, y1 = (float(m.group(k)) for k in range(3, 7))
            cajas.setdefault(int(m.group(1)), {})[m.group(2)] = (x0, y0, x1, y1)
        m = re.match(r"^Page\s+(\d+)\s+rot:\s+(\d+)", linea)
        if m:
            cajas.setdefault(int(m.group(1)), {})["rot"] = int(m.group(2))
    return {"paginas": paginas, "cajas": cajas}


def _comprobar_imagenes(reader: PdfReader) -> None:
    """Rechaza imágenes incrustadas desproporcionadas (p. ej. JPX que descomprimen gigas al rasterizar)."""
    vistos = set()

    def recorrer(recursos, profundidad=0):
        if not recursos or profundidad > 5:
            return
        xobjs = recursos.get("/XObject")
        if not xobjs:
            return
        xobjs = xobjs.get_object()
        for _, ref in xobjs.items():
            clave = getattr(ref, "idnum", None)
            if clave in vistos:
                continue
            vistos.add(clave)
            o = ref.get_object()
            if o.get("/Subtype") == "/Image":
                try:
                    w, h_ = int(o.get("/Width", 0)), int(o.get("/Height", 0))
                except (TypeError, ValueError):
                    raise PDFNoValido("Imagen incrustada con dimensiones no válidas")
                if w * h_ > MAX_PX_IMAGEN or w > 60000 or h_ > 60000:
                    raise PDFNoValido(f"Imagen incrustada desproporcionada ({w}×{h_} px)")
            elif o.get("/Subtype") == "/Form":
                recorrer(o.get("/Resources"), profundidad + 1)
    for p in reader.pages:
        try:
            recorrer(p.get("/Resources"))
        except PDFNoValido:
            raise
        except Exception:
            continue


def _firmas(reader: PdfReader) -> list[dict]:
    out = []
    try:
        campos = reader.get_fields() or {}
    except Exception:
        campos = {}
    for nombre, campo in campos.items():
        if campo.get("/FT") == "/Sig":
            v = campo.get("/V")
            firmado = v is not None
            info = {"campo": nombre, "firmado": firmado}
            if firmado:
                try:
                    vv = v.get_object()
                    info["subfiltro"] = str(vv.get("/SubFilter", ""))
                    info["nombre"] = str(vv.get("/Name", ""))
                    info["fecha"] = str(vv.get("/M", ""))
                except Exception:
                    pass
            out.append(info)
    return out


def detectar_qr(ruta_png: Path) -> list[dict]:
    """Lee los QR de una imagen de página con zbarimg y señala los que apuntan al cotejo de la AEAT."""
    try:
        r = ejecutar(["zbarimg", "--quiet", "--raw", "-Sqrcode.enable", str(ruta_png)], timeout=60, text=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    out = []
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        info = {"contenido": linea[:500], "tributario": False}
        try:
            u = urlparse(linea)
            if u.scheme == "https" and ("aeat.es" in u.netloc or "agenciatributaria" in u.netloc) and "ValidarQR" in u.path:
                q = parse_qs(u.query)
                info.update({
                    "tributario": True,
                    "modalidad": "no_verifactu" if "NoVerifactu" in u.path else "verifactu",
                    "entorno": "pruebas" if u.netloc.startswith("prewww") else "produccion",
                    "nif": q.get("nif", [""])[0], "numserie": q.get("numserie", [""])[0],
                    "fecha": q.get("fecha", [""])[0], "importe": q.get("importe", [""])[0],
                })
        except ValueError:
            pass
        out.append(info)
    return out


def rasterizar(datos: bytes, destino: Path, dpi: int = 110, paginas: int | None = None, gris: bool = False,
               primera: int | None = None, ultima: int | None = None, max_px: int = MAX_PX_LADO,
               max_bytes: int = 200 * 1024 * 1024, png: bool = True) -> list[Path]:
    """Rasteriza con límites: resolución reducida si la página es grande (máx. `max_px` por lado según poppler),
    memoria/CPU limitadas y tamaño de salida acotado (`max_bytes` en total). png=False: PGM/PPM sin comprimir (mucho
    más rápido de escribir, para un uso interno inmediato)."""
    destino.mkdir(parents=True, exist_ok=True)
    ini = primera or 1
    fin = ultima or paginas or ajustes().max_paginas_pdf
    info = info_poppler(datos, fin)
    lados = [max(abs(c["MediaBox"][2] - c["MediaBox"][0]), abs(c["MediaBox"][3] - c["MediaBox"][1]))
             for n, c in info["cajas"].items() if "MediaBox" in c and ini <= n <= fin]
    lado = max(lados) if lados else 842.0
    dpi = max(30, min(dpi, int(max_px * 72 / max(lado, 1))))
    with tempfile.TemporaryDirectory() as td:
        ent = Path(td) / "e.pdf"
        ent.write_bytes(datos)
        cmd = ["pdftoppm", "-r", str(dpi)] + (["-png"] if png else []) + ["-cropbox", "-f", str(ini), "-l", str(fin)]
        if gris:
            cmd.append("-gray")
        cmd += [str(ent), str(destino / "p")]
        r = ejecutar(cmd, timeout=180, fsize=max_bytes)
        if r.returncode != 0:
            raise PDFNoValido("No se pudo representar el PDF dentro de los límites de memoria, tiempo y tamaño")
    ext = "png" if png else ("pgm" if gris else "ppm")
    salida = sorted(destino.glob(f"p-*.{ext}"), key=lambda p: int(re.findall(r"(\d+)\.\w+$", p.name)[0]))
    if sum(p.stat().st_size for p in salida) > max_bytes:
        raise PDFNoValido("La representación del PDF supera el tamaño admitido")
    return salida


def tamanos_pagina(reader) -> list[tuple[float, float]]:
    out = []
    for p in reader.pages:
        mb = p.mediabox
        out.append((abs(float(mb.width)), abs(float(mb.height))))
    return out


def inspeccionar(datos: bytes) -> dict:
    try:
        reader = PdfReader(io.BytesIO(datos), strict=False)
        cifrado = reader.is_encrypted
        if cifrado:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise PDFNoValido("El PDF está protegido con contraseña")
        n = len(reader.pages)
    except PDFNoValido:
        raise
    except Exception as e:
        raise PDFNoValido(f"No se puede leer la estructura del PDF: {type(e).__name__}") from e
    if n == 0:
        raise PDFNoValido("El PDF no tiene páginas")
    if n > ajustes().max_paginas_pdf:
        raise PDFNoValido(f"El PDF tiene {n} páginas; el límite es {ajustes().max_paginas_pdf}")
    for w, h_ in tamanos_pagina(reader):
        if w > MAX_LADO_PAGINA_PT or h_ > MAX_LADO_PAGINA_PT or w <= 0 or h_ <= 0:
            raise PDFNoValido(f"Tamaño de página no admitido ({w:.0f}×{h_:.0f} pt)")
    pop = info_poppler(datos, ajustes().max_paginas_pdf)
    if pop["paginas"] != n:
        raise PDFNoValido("El PDF es ambiguo: los lectores no coinciden en su número de páginas")
    for num, cajas in pop["cajas"].items():
        mb = cajas.get("MediaBox")
        if mb and max(abs(mb[2] - mb[0]), abs(mb[3] - mb[1])) > MAX_LADO_PAGINA_PT:
            raise PDFNoValido("Tamaño de página no admitido")
    _comprobar_imagenes(reader)
    activos = _buscar_activos(datos)
    p1 = pop["cajas"].get(1, {})
    cajas_p1 = {"rotacion": p1.get("rot", 0), "mediabox": p1.get("MediaBox"), "cropbox": p1.get("CropBox")}
    firmas = _firmas(reader)
    meta = {}
    try:
        for k, v in (reader.metadata or {}).items():
            meta[str(k).lstrip("/")] = str(v)[:200]
    except Exception:
        pass
    tamanos = [list(t) for t in tamanos_pagina(reader)]
    return {
        "paginas": n,
        "cifrado": bool(cifrado),
        "contenido_activo": activos,
        "firmas": firmas,
        "firmado": any(f.get("firmado") for f in firmas),
        "metadatos": meta,
        "tamanos_pagina": tamanos,
        "productor": meta.get("Producer", ""),
        "pagina1": cajas_p1,
        "creador": meta.get("Creator", ""),
    }
