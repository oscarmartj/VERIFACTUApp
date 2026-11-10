"""Proceso hijo de extracción de texto nativo (pdfplumber), ejecutado con límites de memoria y CPU.

Uso interno: python texto_proceso.py <pdf> <max_paginas>
Escribe en la salida estándar un JSON con, por página, su tamaño y sus palabras con posición. Es un script
autónomo (no importa la aplicación) para poder ejecutarse aislado del proceso del servidor.

Se intenta leer solo el texto que el revisor puede ver en la imagen de la página (la que se representa con -cropbox). El
filtro reconoce los mecanismos de ocultación siguientes; ninguno es exhaustivo, y lo que se le escape lo cubre en parte la
comprobación de tinta de texto.py (palabras sin contraste en la imagen representada: dudosas):
- se descarta el texto invisible (modo de representación 3, o transparencia ca/CA casi nula), el que cae fuera de
  la zona visible o de su trazado de recorte (W, /BBox de un Form XObject), el de un contenido opcional (OCG)
  desactivado y el de los glifos Type3 que no dibujan nada;
- se marca como dudoso (palabra con un 6.º elemento: 2 si solo pinta del color del papel sin un fondo de color
  reconocido debajo, lo que texto.py comprueba en la imagen de la página; 1 en los demás casos) ese texto, el que solo
  recorta (modo 7: se ve únicamente si después se pinta algo a través), el tapado por una imagen (también con
  máscara, si es opaca donde está el texto) o un rectángulo opaco dibujados después, el que queda
  bajo una anotación visible, el de un glifo cuyo /ToUnicode no corresponde a lo que dibuja la fuente (varias cifras
  o signos para un glifo, u otra cifra que la de su codificación) y el que no se puede leer por su tamaño (cuerpo de
  menos de CUERPO_MIN_PT o glifos deformados: muy estrechos o muy anchos para su altura). Los valores que salgan de ese
  texto deben confirmarse;
- para que texto.py los contraste con la imagen de la página, se pasan las cajas del texto invisible sobre imágenes
  (una capa de OCR sobre un escaneo, frente a una marca invisible sobre un fondo liso), los tramos numéricos dibujados
  con fuentes de dibujo libre (incrustadas, Type3, CID: su /ToUnicode puede no decir lo que dibujan) y los caracteres en
  blanco junto a cifras cuyo glifo puede dibujar algo.
Depende de detalles internos de pdfminer.six (fijado en requirements.txt): al cambiar de versión hay que revisar este
módulo. Si falta alguno de los métodos que se amplían, o si al leer un PDF de comprobación conocido alguno de los
mecanismos anteriores deja de detectarse (_autocomprobacion, en cada ejecución), el proceso falla y la importación se
rechaza, en vez de leer sin filtrar.
"""
import json
import math
import re
import sys
import unicodedata

import pdfplumber
import pdfplumber.page as _pagina_pdfplumber
from pdfminer import pdfinterp as _interprete
from pdfminer.layout import LTChar, LTComponent, LTContainer, LTCurve, LTImage, LTRect
from pdfminer.pdffont import PDFCIDFont, PDFType3Font
from pdfminer.pdftypes import PDFObjRef, PDFStream, resolve1
from pdfminer.psparser import PSLiteral, literal_name
from pdfminer.utils import apply_matrix_pt, apply_matrix_rect, mult_matrix

MAX_CARACTERES_PAGINA = 50_000
MAX_TEXTO_PAGINA = 60_000   # caracteres de texto: con /ToUnicode un glifo puede traducirse a una cadena larga
MODO_INVISIBLE = 3          # Tr 3: ni relleno ni trazo
MODO_SOLO_RECORTE = 7       # Tr 7: solo añade el texto al recorte
TOLERANCIA_PT = 0.5
ALFA_MIN = 0.05             # opacidad por debajo de la cual lo pintado no se ve
CUERPO_MIN_PT = 3.0         # altura de letra representada por debajo de la cual el texto no se lee
PROPORCION_MIN, PROPORCION_MAX = 0.35, 4.0   # ancho/alto del cuadratín representado (1 en texto normal; Tz o cm deformantes)
MAX_COMPROBACIONES_TAPA = 200_000   # carácter × objeto dibujado encima, por página (una factura real: pocos miles)
MAX_TRAMOS_INVISIBLES = 5_000       # tramos de texto invisible sobre imágenes cuya tinta se comprueba (texto.py)
MAX_CIFRAS_PAGINA = 5_000           # palabras con cifras de dibujo libre por página que se pasan a comprobar (texto.py)
MAX_BLANCOS_PAGINA = 5_000          # caracteres en blanco junto a cifras cuyo dibujo se comprueba (texto.py)
_SIGNOS_CIFRA = re.compile(r"[0-9.,'/%-]")   # caracteres del tramo numérico de una palabra (uno por glifo)


VISIBLE, DUDOSO, OCULTO = 0, 1, 2
# Marca de las palabras dudosas (6.º elemento): 1, por su forma de dibujarse; 2, solo por ser del color del papel sin un
# fondo reconocido debajo (texto.py las da por visibles si la imagen de la página las muestra claras sobre fondo oscuro)
DUDOSO_FIJO, DUDOSO_SOBRE_PAPEL = 1, 2
_OCG_OCULTOS: set = set()   # objid de los OCG desactivados en la configuración por defecto del documento
_OCG_VISIBLES: set = set()  # con /BaseState /OFF, solo se ven los de /ON
_OCG_BASE_OFF = False


def _interseccion(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _dentro(c, caja, t=0.5) -> bool:
    """El centro del objeto cae en la caja (con tolerancia)."""
    cx, cy = (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2
    return caja[0] - t <= cx <= caja[2] + t and caja[1] - t <= cy <= caja[3] + t


class _Sombreado(LTComponent):
    """Zona pintada por un sombreado (operador sh): solo sirve de posible fondo y se retira antes de leer el texto."""

    def __init__(self, bbox, papel: bool | None):
        super().__init__(bbox)
        self.papel = papel   # True: del color del papel; None: colores desconocidos


_OPS_PINTAR = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"S", b"s", b"sh", b"Do", b"EI", b"Tj", b"TJ", b"'", b'"'}


def _glifo_vacio(font, cid) -> bool:
    """Glifo Type3 cuyo procedimiento no pinta nada (ni trazados, ni imágenes, ni texto)."""
    cache = getattr(font, "_glifos_vacios", None)
    if cache is None:
        cache = font._glifos_vacios = {}
    if cid in cache:
        return cache[cid]
    vacio = False
    try:
        spec = getattr(font, "_spec", None) or {}
        nombre, cod = None, None
        enc = resolve1(spec.get("Encoding"))
        if isinstance(enc, dict):
            for d in resolve1(enc.get("Differences")) or []:
                d = resolve1(d)
                if isinstance(d, int):
                    cod = d
                elif isinstance(d, PSLiteral) and cod is not None:
                    if cod == cid:
                        nombre = d.name
                    cod += 1
        flujo = resolve1((resolve1(spec.get("CharProcs")) or {}).get(nombre)) if nombre is not None else None
        if isinstance(flujo, PDFStream):
            datos = re.sub(rb"%[^\r\n]*", b"", flujo.get_data()[:200_000])
            datos = re.sub(rb"\((?:\\.|[^\\)])*\)", b" (x) ", datos)   # cadenas
            vacio = not (set(re.findall(rb"[A-Za-z'\"*]+", datos)) & _OPS_PINTAR)
    except Exception:
        vacio = False
    cache[cid] = vacio
    return vacio


def _ilegible(matriz, cuerpo, escala) -> bool:
    """Glifo que no se puede leer en la página: letra diminuta o cuadratín deformado (comprimido o estirado en una sola
    dirección, p. ej. con Tz 5 % o una matriz que aplasta el texto). matriz: Tm × CTM; escala: Tz / 100."""
    try:
        alto = abs(float(cuerpo)) * math.hypot(matriz[2], matriz[3])
        ancho = abs(float(cuerpo) * float(escala)) * math.hypot(matriz[0], matriz[1])
    except (TypeError, ValueError, IndexError):
        return False
    return alto < CUERPO_MIN_PT or not (PROPORCION_MIN * alto <= ancho <= PROPORCION_MAX * alto)


_CIFRA_O_SIGNO = re.compile(r"[\d.,]")


def _unicode_sospechoso(font, cid, texto: str) -> bool:
    """El /ToUnicode traduce el glifo a algo distinto de lo que dibuja: varias cifras o signos para un solo glifo, o, en una
    fuente simple no incrustada (se dibuja el glifo que nombra su codificación), una cifra o un signo distinto del de
    su codificación. En una fuente incrustada los códigos pueden ser arbitrarios y el /ToUnicode es la única traducción."""
    if len(texto) > 1 and _CIFRA_O_SIGNO.search(texto):
        return True
    base = getattr(font, "cid2unicode", None)
    if getattr(font, "unicode_map", None) is None or not isinstance(base, dict) or cid not in base:
        return False
    desc = getattr(font, "descriptor", None)
    if not isinstance(desc, dict) or any(k in desc for k in ("FontFile", "FontFile2", "FontFile3")):
        return False
    propio = base[cid]
    return propio != texto and bool(_CIFRA_O_SIGNO.search(propio + texto))


class _Agregador(_pagina_pdfplumber.PDFPageAggregatorWithMarkedContent):
    """Anota en cada carácter su modo de representación (Tr) y si se ve (recorte, contenido opcional, glifo vacío,
    /ToUnicode dudoso), y en rellenos e imágenes su opacidad y su recorte (pdfplumber no los expone)."""

    def render_string(self, textstate, seq, ncs, graphicstate):
        self._modo_tr = textstate.render
        return super().render_string(textstate, seq, ncs, graphicstate)

    def render_char(self, *args, **kwargs):
        adv = super().render_char(*args, **kwargs)
        c = self.cur_item._objs[-1]
        c.modo_tr = getattr(self, "_modo_tr", 0)
        font, cid = args[1], args[5]
        # Código de control (tabulador, salto de línea…) de una fuente simple sin nombre de glifo: pdfminer lo da como
        # «(cid:9)»; en el documento era un blanco (las hojas de cálculo exportan así el tabulador de una celda)
        if (not isinstance(font, PDFCIDFont) and isinstance(cid, int) and cid < 32
                and re.fullmatch(r"\(cid:\d+\)", c.get_text() or "")):
            c._text = " "
        gs = args[7] if len(args) > 7 else kwargs.get("graphicstate")
        vis = max(self._pila_oc) if getattr(self, "_pila_oc", None) else VISIBLE
        rec = getattr(gs, "recorte", None)
        if rec is not None and not _dentro(c, rec):
            vis = OCULTO   # fuera de su trazado de recorte: no se pinta
        if vis != OCULTO and isinstance(font, PDFType3Font) and _glifo_vacio(font, cid):
            vis = OCULTO
        if vis == VISIBLE and _unicode_sospechoso(font, cid, c.get_text()):
            vis = DUDOSO
        if vis == VISIBLE and c.get_text().strip() and _ilegible(args[0], args[2], args[3]):
            vis = DUDOSO
        if _dibujo_libre(font):
            c.glifo = f"{_clave_fuente(font)}:{cid}"
            # un glifo que dibuja algo y cuyo texto es vacío o un carácter de control: lo que se lee no es lo que se ve
            t = c.get_text()
            if vis == VISIBLE and (t == "" or any(unicodedata.category(ch) == "Cc" and not ch.isspace() for ch in t)):
                vis = DUDOSO
        c.visibilidad = vis
        return adv

    def paint_path(self, gstate, stroke, fill, evenodd, path):
        n = len(self.cur_item._objs)
        super().paint_path(gstate, stroke, fill, evenodd, path)
        for o in self.cur_item._objs[n:]:
            o.alfa_relleno = getattr(gstate, "alfa_relleno", 1.0)
            o.recorte = getattr(gstate, "recorte", None)

    def render_image(self, name, stream):
        super().render_image(name, stream)
        o = self.cur_item._objs[-1]
        o.alfa_relleno = getattr(self, "_alfa_relleno", 1.0)
        o.recorte = getattr(self, "_recorte_imagen", None)
        o.oculta = bool(getattr(self, "_pila_oc", None)) and max(self._pila_oc) == OCULTO


# Los métodos de pdfminer que se amplían deben existir con estos nombres: si una versión los renombra, la ampliación no
# se aplicaría y el texto oculto se leería sin aviso. Se falla al importar (el proceso devuelve error)
for _cls, _nombres in ((_pagina_pdfplumber.PDFPageAggregatorWithMarkedContent, ("render_string", "render_char", "paint_path",
                                                                             "render_image")),
                       (_interprete.PDFPageInterpreter, ("do_Do", "do_EI", "do_W", "do_BMC", "do_BDC", "do_EMC", "init_state",
                                                         "dup", "render_contents")),
                       (_interprete.PDFGraphicState, ("copy",)), (_interprete.PDFResourceManager, ("get_font",))):
    for _n in _nombres:
        if not callable(getattr(_cls, _n, None)):
            raise ImportError(f"pdfminer sin {_cls.__name__}.{_n}: versión no compatible con texto_proceso")
if not isinstance(getattr(_pagina_pdfplumber, "ALL_ATTRS", None), (set, frozenset)):
    raise ImportError("pdfplumber sin page.ALL_ATTRS: versión no compatible con texto_proceso")

_pagina_pdfplumber.PDFPageAggregatorWithMarkedContent = _Agregador

# pdfminer no aplica la transparencia constante del estado gráfico (operador gs, claves ca y CA): se guarda en el
# estado (y en sus copias) para saber si el texto y los rellenos se ven
_copiar_estado = _interprete.PDFGraphicState.copy


def _copiar_estado_con_alfa(self):
    nuevo = _copiar_estado(self)
    nuevo.alfa_relleno = getattr(self, "alfa_relleno", 1.0)
    nuevo.alfa_trazo = getattr(self, "alfa_trazo", 1.0)
    nuevo.recorte = getattr(self, "recorte", None)
    return nuevo


def _caja_trazado(ctm, trazado):
    """Rectángulo (en coordenadas de la página) que contiene el trazado actual, con sus puntos de control."""
    xs, ys = [], []
    for seg in trazado:
        nums = [v for v in seg[1:] if isinstance(v, (int, float))]
        for i in range(0, len(nums) - 1, 2):
            x, y = apply_matrix_pt(ctm, (nums[i], nums[i + 1]))
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _do_W(self):
    """W / W*: el recorte pasa a ser la intersección con la caja del trazado (aproximación por exceso: solo se descarta
    lo que seguro queda fuera)."""
    caja = _caja_trazado(self.ctm, self.curpath)
    if caja is not None:
        self.graphicstate.recorte = _interseccion(getattr(self.graphicstate, "recorte", None), caja)


def _visibilidad_oc(props, resources) -> int:
    """Visibilidad de un contenido opcional (/OC) en la configuración por defecto: OCG u OCMD."""
    try:
        if isinstance(props, PSLiteral):
            props = (resolve1(resources.get("Properties")) or {}).get(props.name)
        d = resolve1(props)
        if not isinstance(d, dict):
            return VISIBLE

        def oculto(ref) -> bool:
            oid = ref.objid if isinstance(ref, PDFObjRef) else None
            return oid not in _OCG_VISIBLES if _OCG_BASE_OFF else oid in _OCG_OCULTOS
        if d.get("Type") is None or literal_name(d.get("Type")) != "OCMD":
            return OCULTO if oculto(props) else VISIBLE
        if d.get("VE") is not None:
            return DUDOSO   # expresión de visibilidad: no se evalúa
        grupos = d.get("OCGs")
        if not isinstance(resolve1(grupos), list):
            grupos = [grupos]
        ocultos = [oculto(g) for g in resolve1(grupos) if g is not None]
        if not ocultos:
            return VISIBLE
        pol = literal_name(d.get("P")) if d.get("P") is not None else "AnyOn"
        visible = {"AllOn": not any(ocultos), "AnyOff": any(ocultos), "AllOff": all(ocultos)}.get(pol, not all(ocultos))
        return VISIBLE if visible else OCULTO
    except Exception:
        return DUDOSO


def _do_BMC(metodo):
    def envoltura(self, tag):
        self.device._pila_oc = getattr(self.device, "_pila_oc", []) + [VISIBLE]
        return metodo(self, tag)
    return envoltura


def _do_BDC(metodo):
    def envoltura(self, tag, props):
        vis = _visibilidad_oc(props, self.resources) if isinstance(tag, PSLiteral) and tag.name == "OC" else VISIBLE
        self.device._pila_oc = getattr(self.device, "_pila_oc", []) + [vis]
        return metodo(self, tag, props)
    return envoltura


def _do_EMC(metodo):
    def envoltura(self):
        self.device._pila_oc = getattr(self.device, "_pila_oc", [])[:-1]
        return metodo(self)
    return envoltura


_iniciar_estado = _interprete.PDFPageInterpreter.init_state


def _init_state_heredado(self, ctm):
    """Un Form XObject hereda el estado gráfico de quien lo invoca (transparencia, color, recorte), no el de defecto."""
    _iniciar_estado(self, ctm)
    heredado = getattr(self.device, "_estado_heredado", None)
    if heredado is not None:
        self.device._estado_heredado = None
        self.graphicstate = heredado


def _do_gs(self, nombre):
    try:
        egs = resolve1(resolve1(self.resources.get("ExtGState") or {}).get(literal_name(nombre)))
    except Exception:
        return
    if not isinstance(egs, dict):
        return
    for clave, attr in (("ca", "alfa_relleno"), ("CA", "alfa_trazo")):
        v = resolve1(egs.get(clave))
        if isinstance(v, (int, float)):
            setattr(self.graphicstate, attr, float(v))


def _con_alfa_de_imagen(metodo):
    # un solo operando, como los originales (pdfminer cuenta los parámetros para sacarlos de la pila)
    def envoltura(self, operando):
        self.device._alfa_relleno = getattr(self.graphicstate, "alfa_relleno", 1.0)
        self.device._recorte_imagen = getattr(self.graphicstate, "recorte", None)
        return metodo(self, operando)
    return envoltura


def _con_estado_de_formulario(metodo):
    """Do de un Form XObject: su contenido hereda el estado gráfico, queda recortado por su /BBox y, si el propio
    formulario es contenido opcional desactivado, no se ve."""
    def envoltura(self, operando):
        heredado, vis = None, VISIBLE
        try:
            xobj = resolve1(self.xobjmap.get(literal_name(operando)))
            if isinstance(xobj, PDFStream) and literal_name(xobj.get("Subtype")) == "Form" and "BBox" in xobj:
                heredado = self.graphicstate.copy()
                matriz = tuple(resolve1(xobj.get("Matrix")) or (1, 0, 0, 1, 0, 0))
                caja = apply_matrix_rect(mult_matrix(matriz, self.ctm), tuple(resolve1(xobj["BBox"])))
                heredado.recorte = _interseccion(heredado.recorte, caja)
                if xobj.get("OC") is not None:
                    vis = _visibilidad_oc(xobj.get("OC"), self.resources)
        except Exception:
            heredado, vis = None, VISIBLE
        self.device._estado_heredado = heredado
        self.device._pila_oc = getattr(self.device, "_pila_oc", []) + [vis]
        try:
            return metodo(self, operando)
        finally:
            self.device._estado_heredado = None
            self.device._pila_oc = self.device._pila_oc[:-1]
    return envoltura


def _do_sh(self, nombre):
    """sh pinta el sombreado en toda la zona de recorte: puede ser el fondo de un texto claro."""
    rec = getattr(self.graphicstate, "recorte", None)
    if rec is None or rec[0] > rec[2] or rec[1] > rec[3]:
        return
    papel = None
    try:
        sh = resolve1((resolve1(self.resources.get("Shading")) or {}).get(literal_name(nombre)))
        sh = sh.attrs if isinstance(sh, PDFStream) else sh
        cs = resolve1(sh.get("ColorSpace"))
        n = {"DeviceGray": 1, "DeviceRGB": 3, "DeviceCMYK": 4}.get(literal_name(cs) if isinstance(cs, PSLiteral) else "")
        funcs, colores = [resolve1(sh.get("Function"))], []
        while funcs and colores is not None and len(colores) < 64:
            f = funcs.pop()
            f = f.attrs if isinstance(f, PDFStream) else f
            if isinstance(f, list):
                funcs += [resolve1(x) for x in f]
            elif isinstance(f, dict) and f.get("FunctionType") == 2:
                colores += [list(resolve1(f.get("C0")) or [0.0]), list(resolve1(f.get("C1")) or [1.0])]
            elif isinstance(f, dict) and f.get("FunctionType") == 3:
                funcs += [resolve1(x) for x in resolve1(f.get("Functions")) or []]
            else:
                colores = None   # función muestreada o de otro tipo: colores desconocidos
        if n and colores:
            papel = all(len(c) == n and _es_papel(tuple(resolve1(v) for v in c)) for c in colores)
    except Exception:
        papel = None
    if getattr(self.graphicstate, "alfa_relleno", 1.0) <= ALFA_MIN:
        papel = True
    self.device.cur_item.add(_Sombreado(rec, papel))


_crear_fuente = _interprete.PDFResourceManager.get_font


def _dibujo_libre(font) -> bool:
    """Lo que dibuja la fuente para cada código no se deduce de una codificación estándar: fuente incrustada (su programa
    decide el dibujo de cada glifo), Type3 o CID. Su texto (/ToUnicode o nombres de glifo) solo se puede contrastar con
    la imagen de la página (texto.py, _verificar_cifras)."""
    v = getattr(font, "_es_dibujo_libre", None)
    if v is None:
        desc = getattr(font, "descriptor", None)
        v = isinstance(font, (PDFType3Font, PDFCIDFont)) or not isinstance(desc, dict) \
            or any(k in desc for k in ("FontFile", "FontFile2", "FontFile3"))
        try:
            font._es_dibujo_libre = v
        except Exception:
            pass
    return v


_CLAVES_FUENTE: dict = {}


def _clave_fuente(font) -> str:
    """Identificador de la fuente en el documento (la misma en todas sus páginas si pdfminer la reutiliza)."""
    c = getattr(font, "_clave_glifos", None)
    if c is None:
        c = _CLAVES_FUENTE.setdefault(id(font), f"f{len(_CLAVES_FUENTE)}")
        try:
            font._clave_glifos = c
        except Exception:
            pass
    return c


def _tramo_numerico(chars) -> dict | None:
    """Del primer al último dígito de la palabra, si solo hay cifras y signos y alguna cifra se dibuja con una fuente de
    dibujo libre: su caja, los dígitos que dice el texto, las claves de los glifos de dibujo libre de las cifras y de
    los signos, y el final del carácter anterior y el principio del siguiente de la palabra (acotan el recorte)."""
    pos = [i for i, ch in enumerate(chars) if re.search(r"[0-9]", ch.get("text") or "")]
    if not pos:
        return None
    tramo = [ch for ch in chars[pos[0]:pos[-1] + 1] if (ch.get("text") or "").strip()]
    if any(not _SIGNOS_CIFRA.fullmatch(ch["text"]) for ch in tramo):
        return None   # letras entre las cifras (códigos, referencias): no es un número
    cifras = sorted({ch["glifo"] for ch in tramo if ch.get("glifo") and ch["text"].isdigit()})
    if not cifras:
        return None
    antes, despues = chars[:pos[0]], chars[pos[-1] + 1:]
    return {"caja": [round(min(ch["x0"] for ch in tramo), 2), round(min(ch["top"] for ch in tramo), 2),
                     round(max(ch["x1"] for ch in tramo), 2), round(max(ch["bottom"] for ch in tramo), 2)],
            "digitos": "".join(ch["text"] for ch in tramo if ch["text"].isdigit()), "glifos": cifras,
            "signos": sorted({ch["glifo"] for ch in tramo if ch.get("glifo") and not ch["text"].isdigit()}),
            "izq": round(max(ch["x1"] for ch in antes), 2) if antes else None,
            "der": round(min(ch["x0"] for ch in despues), 2) if despues else None}


def _blancos_junto_a_cifras(page, chars, descartar) -> list | None:
    """Caracteres cuyo texto es un blanco (o nada), dibujados junto a una cifra, cuyo glifo puede dibujar
    algo: de una fuente de dibujo libre, o con un /ToUnicode que contradice su codificación (el «1» de «1.250,00»
    traducido por un espacio deja «.250,00»). [x0, top, x1, bottom, seguro] en las coordenadas de las palabras; seguro=1
    si el /ToUnicode ya contradice el glifo, 0 si texto.py debe mirar si hay tinta en la imagen. None si son demasiados."""
    alto = float(page.height)
    mx, mt = (float(v) for v in page.mediabox[:2])
    out = []
    for k, (_, c) in enumerate(chars):
        t = c.get_text()
        if t.strip() or id(c) in descartar:
            continue
        seguro = getattr(c, "visibilidad", VISIBLE) == DUDOSO
        if not seguro and getattr(c, "glifo", None) is None:
            continue
        if not any(re.search(r"[0-9]", chars[j][1].get_text()) for j in (k - 2, k - 1, k + 1, k + 2) if 0 <= j < len(chars)):
            continue   # ni a uno ni a dos caracteres de una cifra («1.250,00» con el «1» en blanco: le sigue el punto)
        if len(out) >= MAX_BLANCOS_PAGINA:
            return None
        out.append([round(float(c.x0) + mx, 2), round(alto - float(c.y1) + mt, 2), round(float(c.x1) + mx, 2),
                    round(alto - float(c.y0) + mt, 2), int(seguro)])
    return out


def _get_font_con_spec(self, objid, spec):
    """Se guarda el diccionario de la fuente (pdfminer no lo conserva) para examinar sus glifos Type3."""
    fuente = _crear_fuente(self, objid, spec)
    if getattr(fuente, "_spec", None) is None:
        try:
            fuente._spec = spec
        except Exception:
            pass
    return fuente


_interprete.PDFGraphicState.copy = _copiar_estado_con_alfa
_interprete.PDFPageInterpreter.do_gs = _do_gs
_interprete.PDFPageInterpreter.do_Do = _con_estado_de_formulario(_con_alfa_de_imagen(_interprete.PDFPageInterpreter.do_Do))
_interprete.PDFPageInterpreter.do_EI = _con_alfa_de_imagen(_interprete.PDFPageInterpreter.do_EI)
_interprete.PDFPageInterpreter.do_W = _do_W
_interprete.PDFPageInterpreter.do_W_a = _do_W
_interprete.PDFPageInterpreter.do_sh = _do_sh
_interprete.PDFPageInterpreter.do_BMC = _do_BMC(_interprete.PDFPageInterpreter.do_BMC)
_interprete.PDFPageInterpreter.do_BDC = _do_BDC(_interprete.PDFPageInterpreter.do_BDC)
_interprete.PDFPageInterpreter.do_EMC = _do_EMC(_interprete.PDFPageInterpreter.do_EMC)
_interprete.PDFPageInterpreter.init_state = _init_state_heredado
_interprete.PDFResourceManager.get_font = _get_font_con_spec
# pdfplumber solo copia a cada carácter los atributos de esta lista: se añade la marca de texto dudoso
_pagina_pdfplumber.ALL_ATTRS = _pagina_pdfplumber.ALL_ATTRS | {"texto_dudoso", "glifo"}


def _es_papel(color, espacio=None) -> bool:
    """Color que no deja tinta sobre papel blanco (según el espacio de color o, si no se conoce, sus componentes)."""
    if isinstance(color, (int, float)):
        color = (color,)
    if not isinstance(color, (tuple, list)) or not color or not all(isinstance(v, (int, float)) for v in color):
        return False
    nombre = str(getattr(espacio, "name", "") or "")
    if nombre in ("Separation", "DeviceN"):
        return all(v <= 0.01 for v in color)
    if nombre in ("Indexed", "Pattern", "Lab"):
        return False
    if len(color) == 1:
        return color[0] >= 0.99
    if len(color) == 3:
        return min(color) >= 0.99
    if len(color) == 4:
        return max(color) <= 0.01
    return False


def _rellena(c) -> bool:
    return c.modo_tr in (0, 2, 4, 6) and getattr(c.graphicstate, "alfa_relleno", 1.0) > ALFA_MIN


def _traza(c) -> bool:
    return c.modo_tr in (1, 2, 5, 6) and getattr(c.graphicstate, "alfa_trazo", 1.0) > ALFA_MIN


def _deja_tinta(c) -> bool:
    gs = c.graphicstate
    return (_rellena(c) and not _es_papel(gs.ncolor, getattr(gs, "ncs", None) or c.ncs)) or \
        (_traza(c) and not _es_papel(gs.scolor, getattr(gs, "scs", None)))


def _caja(o, m=0.0):
    """Caja del objeto (ampliada en m) limitada a su recorte."""
    return _interseccion((o.x0 - m, o.y0 - m, o.x1 + m, o.y1 + m), getattr(o, "recorte", None))


def _muestras(flujo, max_bytes=64_000_000):
    """(ancho, alto, componentes, bytes) de una imagen de 8 bits descomprimible por pdfminer, o None."""
    try:
        filtros = flujo.get_filters()
        if any(literal_name(f) not in ("FlateDecode", "Fl", "LZWDecode", "LZW", "RunLengthDecode", "RL", "ASCIIHexDecode",
                                         "AHx", "ASCII85Decode", "A85") for f, _ in filtros):
            return None   # JPEG, JBIG2…: no se descomprime aquí
        w, h = int(resolve1(flujo.get("Width"))), int(resolve1(flujo.get("Height")))
        if int(resolve1(flujo.get("BitsPerComponent")) or 8) != 8 or w <= 0 or h <= 0:
            return None
        cs = resolve1(flujo.get("ColorSpace"))
        nombre = literal_name(cs[0] if isinstance(cs, list) and cs else cs) if cs is not None else "DeviceGray"
        n = {"DeviceGray": 1, "CalGray": 1, "Indexed": 1, "Separation": 1, "DeviceRGB": 3, "CalRGB": 3, "Lab": 3,
             "DeviceCMYK": 4}.get(nombre)
        if nombre == "ICCBased":
            n = int(resolve1(resolve1(cs[1]).get("N")))
        if not n or w * h * n > max_bytes:
            return None
        datos = flujo.get_data()
        return (w, h, n, datos) if len(datos) >= w * h * n else None
    except Exception:
        return None


_MASCARAS: dict = {}   # por imagen de la página en curso: matriz de los píxeles que tapan lo de debajo


def _mapa_opaco(o):
    """Matriz booleana (alto × ancho) de los píxeles de la imagen o que tapan lo que hay debajo, según su máscara
    (/SMask o /Mask de clave de color), o None si no se puede saber."""
    import numpy as np
    st = o.stream
    smask, mask = resolve1(st.get("SMask")), resolve1(st.get("Mask"))
    try:
        if isinstance(smask, PDFStream):
            m = _muestras(smask)
            if m is None:
                return None
            w, h, _, datos = m
            a = np.frombuffer(datos, dtype=np.uint8, count=w * h).reshape(h, w)
            dec = resolve1(smask.get("Decode"))
            if isinstance(dec, list) and len(dec) == 2 and dec[0] > dec[1]:
                a = 255 - a
            return a >= 230
        if isinstance(mask, list):
            m = _muestras(st)
            if m is None:
                return None
            w, h, n, datos = m
            a = np.frombuffer(datos, dtype=np.uint8, count=w * h * n).reshape(h, w, n)
            rangos = [resolve1(v) for v in mask]
            transparente = np.ones((h, w), dtype=bool)
            for k in range(min(n, len(rangos) // 2)):
                transparente &= (a[:, :, k] >= rangos[2 * k]) & (a[:, :, k] <= rangos[2 * k + 1])
            return ~transparente
    except Exception:
        return None
    return None   # máscara explícita (otra imagen) u otra: no se evalúa


def _fraccion_opaca(o, c) -> float:
    """Fracción de la zona del carácter c que tapa la imagen o (dibujada encima), según su máscara. 1.0 si no tiene
    máscara o no se puede saber (lo dudoso se trata como tapado)."""
    st = o.stream
    if st.get("SMask") is None and st.get("Mask") is None:
        return 1.0
    if id(st) not in _MASCARAS:   # por flujo: una imagen dibujada muchas veces se descomprime una sola vez
        _MASCARAS[id(st)] = _mapa_opaco(o)
    m = _MASCARAS[id(st)]
    if m is None:
        return 1.0
    h, w = m.shape
    ax, ay = (o.x1 - o.x0) or 1.0, (o.y1 - o.y0) or 1.0
    i0, i1 = max(0, int((c.x0 - o.x0) / ax * w)), min(w, int((c.x1 - o.x0) / ax * w) + 1)
    j0, j1 = max(0, int((o.y1 - c.y1) / ay * h)), min(h, int((o.y1 - c.y0) / ay * h) + 1)
    if i1 <= i0 or j1 <= j0:
        return 1.0
    return float(m[j0:j1, i0:i1].mean())


def _tapa(o, c) -> bool:
    """El objeto o, dibujado después del carácter c, lo tapa entero (imagen o rectángulo opacos donde está)."""
    t = TOLERANCIA_PT
    caja = _caja(o, t)
    if not (caja[0] <= c.x0 and c.x1 <= caja[2] and caja[1] <= c.y0 and c.y1 <= caja[3]):
        return False
    if isinstance(o, LTRect):
        return bool(o.fill) and getattr(o, "alfa_relleno", 1.0) >= 0.95
    try:
        if getattr(o, "alfa_relleno", 1.0) < 0.95 or getattr(o, "oculta", False) or o.imagemask:
            return False
        return _fraccion_opaca(o, c) >= 0.5
    except Exception:
        return True


def _fondo(o):
    """Rectángulo (x0, y0, x1, y1) que pinta de color bajo lo que se dibuje después, o None."""
    if isinstance(o, LTImage):
        return _caja(o) if getattr(o, "alfa_relleno", 1.0) > ALFA_MIN and not getattr(o, "oculta", False) else None
    if isinstance(o, _Sombreado):
        return (o.x0, o.y0, o.x1, o.y1) if o.papel is False else None
    if isinstance(o, LTCurve):
        if o.fill and getattr(o, "alfa_relleno", 1.0) > ALFA_MIN and not _es_papel(o.non_stroking_color):
            return _caja(o)
        if o.stroke and (o.linewidth or 0) >= 2 and not _es_papel(o.stroking_color):
            return _caja(o, o.linewidth / 2)
    return None


class _Rejilla:
    """Índice espacial de caracteres (por el centro) para no comparar cada figura con todos los caracteres."""

    def __init__(self, x0, y0, x1, y1, n=48):
        self.x0, self.y0, self.n = x0, y0, n
        self.tx, self.ty = max((x1 - x0) / n, 1.0), max((y1 - y0) / n, 1.0)
        self.celdas: dict = {}
        self.total = 0

    def _celda(self, x, y):
        return (min(self.n - 1, max(0, int((x - self.x0) / self.tx))), min(self.n - 1, max(0, int((y - self.y0) / self.ty))))

    def poner(self, c):
        self.celdas.setdefault(self._celda((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2), []).append(c)
        self.total += 1

    def sacar(self, caja, cumple) -> list:
        """Quita y devuelve los caracteres con el centro en la caja que cumplen la condición."""
        if not self.total:
            return []
        (i0, j0), (i1, j1) = self._celda(caja[0], caja[1]), self._celda(caja[2], caja[3])
        out = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                lista = self.celdas.get((i, j))
                if lista:
                    quedan = []
                    for c in lista:
                        cx, cy = (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2
                        (out if caja[0] <= cx <= caja[2] and caja[1] <= cy <= caja[3] and cumple(c) else quedan).append(c)
                    self.celdas[(i, j)] = quedan
        self.total -= len(out)
        return out


def _en_orden(cont, objs, chars):
    """Recorre la página en el orden de dibujo: caracteres (con su contenedor), imágenes y trazados."""
    for o in cont:
        if isinstance(o, LTChar):
            objs.append(o)
            chars.append((cont, o))
        elif isinstance(o, (LTImage, LTCurve, _Sombreado)):
            objs.append(o)
        elif isinstance(o, LTContainer):
            _en_orden(o, objs, chars)


def _zona_visible(page, lay):
    """Zona de la página que se representa (CropBox ∩ MediaBox), en las coordenadas de pdfminer."""
    po = page.page_obj
    x0, y0, x1, y1 = po.mediabox
    ctm = {90: (0, -1, 1, 0, -y0, x1), 180: (-1, 0, 0, -1, x1, y1), 270: (0, 1, -1, 0, y1, -x0)}.get(po.rotate, (1, 0, 0, 1, -x0, -y0))
    cx0, cy0, cx1, cy1 = apply_matrix_rect(ctm, po.cropbox)
    return max(lay.x0, min(cx0, cx1)), max(lay.y0, min(cy0, cy1)), min(lay.x1, max(cx0, cx1)), min(lay.y1, max(cy0, cy1))


def _recorte_en_pagina(page, lay) -> list[float]:
    """Zona visible (la que representa pdftoppm -cropbox) en las coordenadas de las palabras (x0, top, x1, bottom).
    Se calcula con la misma matriz que los caracteres: page.cropbox de pdfplumber no es correcto en páginas giradas."""
    vx0, vy0, vx1, vy1 = _zona_visible(page, lay)
    alto = float(page.height)
    mx, mt = (float(v) for v in page.mediabox[:2])   # pdfplumber desplaza así las coordenadas de los caracteres
    return [float(vx0) + mx, alto - float(vy1) + mt, float(vx1) + mx, alto - float(vy0) + mt]


def _clasificar(page, lay, objs, chars) -> tuple[set, set, set, int, int, list]:
    """Devuelve (ids a descartar, ids dudosos, ids del color del papel sin un fondo reconocido debajo, n.º invisibles,
    n.º fuera de la zona visible, caracteres invisibles sobre una imagen)."""
    vx0, vy0, vx1, vy1 = _zona_visible(page, lay)
    descartar, dudosos = set(), set()
    invisibles = fuera = 0
    _MASCARAS.clear()
    sobre_imagen = _Rejilla(lay.x0, lay.y0, lay.x1, lay.y1)
    for _, c in chars:
        if getattr(c, "modo_tr", None) is None:
            raise RuntimeError("No se pudo determinar la visibilidad del texto")
        cx, cy = (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2
        vis = getattr(c, "visibilidad", VISIBLE)
        if c.modo_tr == MODO_INVISIBLE or (c.modo_tr != MODO_SOLO_RECORTE and not (_rellena(c) or _traza(c))) \
                or vis == OCULTO:
            # invisible: modo 3, transparente, recortado, en un contenido opcional desactivado o glifo vacío
            descartar.add(id(c))
            if c.get_text().strip():
                invisibles += 1
                sobre_imagen.poner(c)
        elif not (vx0 - TOLERANCIA_PT <= cx <= vx1 + TOLERANCIA_PT and vy0 - TOLERANCIA_PT <= cy <= vy1 + TOLERANCIA_PT):
            descartar.add(id(c))
            fuera += bool(c.get_text().strip())
        elif c.modo_tr == MODO_SOLO_RECORTE or vis == DUDOSO:
            dudosos.add(id(c))
    # Tapados por una imagen (opaca donde está el carácter, también con máscara) o un rectángulo relleno dibujados
    # después (cubren el carácter entero)
    # Presupuesto de comprobaciones carácter × objeto: miles de imágenes con máscara (o recuadros) que no tapan del todo
    # obligarían a examinar cada carácter muchas veces; agotado (documento hostil), lo que cae dentro se da por tapado
    presupuesto = [MAX_COMPROBACIONES_TAPA]

    def tapado(o, c) -> bool:
        if presupuesto[0] <= 0:
            return True
        presupuesto[0] -= 1
        return _tapa(o, c)
    pendientes = _Rejilla(lay.x0, lay.y0, lay.x1, lay.y1)
    for o in objs:
        if isinstance(o, LTChar):
            if id(o) not in descartar:
                pendientes.poner(o)
        elif isinstance(o, (LTImage, LTRect)) and pendientes.total:
            caja = _caja(o, TOLERANCIA_PT)
            if caja[0] <= caja[2] and caja[1] <= caja[3]:
                dentro = (lambda c: caja[0] <= c.x0 and c.x1 <= caja[2] and caja[1] <= c.y0 and c.y1 <= caja[3])
                for c in pendientes.sacar(caja, lambda c: dentro(c) and tapado(o, c)):
                    dudosos.add(id(c))
    # Del color del papel sin un fondo de color dibujado antes debajo (blanco sobre blanco)
    sin_fondo = _Rejilla(lay.x0, lay.y0, lay.x1, lay.y1)
    for o in reversed(objs):
        if isinstance(o, LTChar):
            if id(o) not in descartar and id(o) not in dudosos and not _deja_tinta(o):
                sin_fondo.poner(o)
        elif sin_fondo.total:
            caja = _fondo(o)
            if caja:
                sin_fondo.sacar(caja, lambda c: True)
    # (también dudosos; texto.py comprueba en la imagen de la página si se ven claros sobre un fondo más oscuro que no
    # se ha reconocido: degradados muestreados o calculados, mallas, modos de fusión…)
    sobre_papel = {id(c) for lista in sin_fondo.celdas.values() for c in lista}
    # Texto invisible sobre una imagen: capa de búsqueda de un escaneo «con OCR» (lo que se ve es la imagen). El texto
    # invisible fuera de imágenes (marcas del programa de facturación, metadatos) no tapa nada: se descarta sin más
    en_imagen = []
    for o in objs:
        if isinstance(o, LTImage) and sobre_imagen.total:
            t = TOLERANCIA_PT
            en_imagen += sobre_imagen.sacar((o.x0 - t, o.y0 - t, o.x1 + t, o.y1 + t), lambda c: True)
    return descartar, dudosos, sobre_papel, invisibles, fuera, en_imagen


def _cajas_tramos(page, chars) -> list | None:
    """Cajas (x0, top, x1, bottom, n.º de caracteres), en las coordenadas de las palabras, de los tramos de un mismo
    renglón que forman los caracteres dados. None si salen demasiadas (documento hostil: no se examinan)."""
    alto = float(page.height)
    mx, mt = (float(v) for v in page.mediabox[:2])
    out = []
    for c in sorted(chars, key=lambda c: (round(c.y0), c.x0)):
        h = max(c.y1 - c.y0, 1.0)
        caja = [float(c.x0) + mx, alto - float(c.y1) + mt, float(c.x1) + mx, alto - float(c.y0) + mt, 1]
        u = out[-1] if out else None
        if u and abs(u[3] - caja[3]) <= 0.3 * h and abs(u[1] - caja[1]) <= 0.3 * h and -0.5 <= caja[0] - u[2] <= h:
            u[2], u[1], u[3], u[4] = max(u[2], caja[2]), min(u[1], caja[1]), max(u[3], caja[3]), u[4] + 1
        else:
            if len(out) >= MAX_TRAMOS_INVISIBLES:
                return None
            out.append(caja)
    return [[round(v, 2) for v in u[:4]] + [u[4]] for u in out]


def _leer_ocg(pdf) -> None:
    """Contenidos opcionales desactivados en la configuración por defecto (/OCProperties /D)."""
    global _OCG_BASE_OFF
    _OCG_OCULTOS.clear()
    _OCG_VISIBLES.clear()
    _OCG_BASE_OFF = False
    try:
        d = resolve1(resolve1(resolve1(pdf.doc.catalog.get("OCProperties")) or {}).get("D")) or {}
        _OCG_BASE_OFF = d.get("BaseState") is not None and literal_name(d.get("BaseState")) == "OFF"
        for clave, conjunto in (("OFF", _OCG_OCULTOS), ("ON", _OCG_VISIBLES)):
            for r in resolve1(d.get(clave)) or []:
                if isinstance(r, PDFObjRef):
                    conjunto.add(r.objid)
    except Exception:
        _OCG_BASE_OFF = False


def _retirar_sombreados(cont) -> None:
    """Los sombreados solo sirven para clasificar el texto: pdfplumber no debe verlos como objetos de la página."""
    if any(isinstance(o, _Sombreado) for o in cont._objs):
        cont._objs = [o for o in cont._objs if not isinstance(o, _Sombreado)]
    for o in cont._objs:
        if isinstance(o, LTContainer):
            _retirar_sombreados(o)


def _anotaciones_visibles(page) -> list:
    out = []
    try:
        anots = page.annots
    except Exception:
        return out
    for a in anots:
        datos = a.get("data") or {}
        tipo = str(getattr(datos.get("Subtype"), "name", datos.get("Subtype")) or "")
        try:
            oculta = int(datos.get("F") or 0) & (2 | 32)   # Hidden, NoView
        except (TypeError, ValueError):
            oculta = 0
        if tipo not in ("Link", "Popup") and not oculta:
            out.append((a["x0"], a["top"], a["x1"], a["bottom"]))
    return out


def _leer_pagina(page, i: int) -> dict:
    """Palabras visibles (y dudosas, con un 6.º elemento = 1) de una página, con sus recuentos de texto oculto."""
    lay = page.layout
    objs, chars = [], []
    _en_orden(lay, objs, chars)
    _retirar_sombreados(lay)
    if len(chars) > MAX_CARACTERES_PAGINA or sum(len(c.get_text()) for _, c in chars) > MAX_TEXTO_PAGINA:
        raise _DemasiadoTexto(f"La página {i} tiene demasiado texto")
    descartar, dudosos, sobre_papel, invisibles, fuera, sobre_imagen = _clasificar(page, lay, objs, chars)
    for cont, c in chars:
        if id(c) in dudosos:
            c.texto_dudoso = DUDOSO_FIJO
        elif id(c) in sobre_papel:
            c.texto_dudoso = DUDOSO_SOBRE_PAPEL
    for cont in {id(cont): cont for cont, c in chars if id(c) in descartar}.values():
        cont._objs = [o for o in cont._objs if id(o) not in descartar]
    anots = _anotaciones_visibles(page)
    palabras, n_dudosos, cifras = [], 0, []
    for w in page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2,
                                return_chars=True):
        # espacios internos (un glifo puede traducirse a texto con espacios) reducidos a uno
        t = " ".join(w["text"].split())
        if t:
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            marcas = {ch.get("texto_dudoso") for ch in w["chars"]} - {None, 0}
            if any(a[0] <= cx <= a[2] and a[1] <= cy <= a[3] for a in anots):
                marcas.add(DUDOSO_FIJO)
            dudosa = min(marcas) if marcas else 0
            n_dudosos += len(t) if dudosa else 0
            tramo = _tramo_numerico(w["chars"])
            if tramo and cifras is not None:
                if len(cifras) >= MAX_CIFRAS_PAGINA:
                    cifras = None   # demasiadas para contrastarlas: texto.py las da todas por dudosas
                else:
                    tramo["i"] = len(palabras)
                    cifras.append(tramo)
            palabras.append([t, w["x0"], w["top"], w["x1"], w["bottom"]] + ([dudosa] if dudosa else []))
    if (dudosos or sobre_papel) and not any(ch.get("texto_dudoso") for ch in page.chars):
        raise RuntimeError("No se pudo marcar el texto dudoso")
    return {"numero": i, "ancho": float(page.width), "alto": float(page.height), "palabras": palabras,
            "invisibles": invisibles, "invisibles_imagen": len(sobre_imagen),
            "cajas_invisibles_imagen": _cajas_tramos(page, sobre_imagen), "cifras_dibujo_libre": cifras,
            "blancos_junto_a_cifras": _blancos_junto_a_cifras(page, chars, descartar), "fuera": fuera,
            "dudosos": n_dudosos, "recorte": _recorte_en_pagina(page, lay)}


class _DemasiadoTexto(Exception):
    pass


def _pdf_comprobacion() -> bytes:
    """PDF mínimo con una letra visible (A), dos dudosas (H: blanco sobre el papel; I: comprimida con Tz 5 %) y una por
    cada mecanismo de ocultación que se reconoce ampliando pdfminer: transparencia (gs ca 0), que debe sobrevivir a
    q/Q, recorte, Form XObject que hereda la transparencia, contenido opcional desactivado y modo 3."""
    cont = (b"BT /F1 10 Tf 10 50 Td (A) Tj ET\n"
            b"q /G0 gs BT /F1 10 Tf 40 50 Td (B) Tj ET Q\n"
            b"q /G0 gs q Q BT /F1 10 Tf 70 50 Td (C) Tj ET Q\n"
            b"q 0 0 1 1 re W n BT /F1 10 Tf 100 50 Td (D) Tj ET Q\n"
            b"q /G0 gs /Fm Do Q\n"
            b"/OC /oc1 BDC BT /F1 10 Tf 160 50 Td (F) Tj ET EMC\n"
            b"q BT 3 Tr /F1 10 Tf 190 50 Td (G) Tj ET Q\n"
            b"q 1 g BT /F1 10 Tf 220 50 Td (H) Tj ET Q\n"
            b"q BT /F1 10 Tf 5 Tz 250 50 Td (I) Tj ET Q\n")
    forma = b"BT /F1 10 Tf 130 50 Td (E) Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [6 0 R] /D << /OFF [6 0 R] >> >> >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> "
            b"/ExtGState << /G0 << /Type /ExtGState /ca 0 /CA 0 >> >> /XObject << /Fm 7 0 R >> "
            b"/Properties << /oc1 6 0 R >> >> >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Length %d >>\nstream\n" % len(cont) + cont + b"\nendstream",
            b"<< /Type /OCG /Name (x) >>",
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 300 100] /Resources << /Font << /F1 4 0 R >> >> /Length %d >>"
            b"\nstream\n" % len(forma) + forma + b"\nendstream"]
    out, offs = bytearray(b"%PDF-1.5\n"), []
    for k, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % k + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % x for x in offs)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _autocomprobacion() -> str | None:
    """Comprueba con un PDF conocido que las ampliaciones de pdfminer siguen surtiendo efecto (una versión distinta de
    pdfminer.six puede dejar de llamar a alguno de los métodos ampliados sin dar error). Devuelve el fallo o None."""
    import io
    try:
        with pdfplumber.open(io.BytesIO(_pdf_comprobacion())) as pdf:
            _leer_ocg(pdf)
            res = _leer_pagina(pdf.pages[0], 1)
        leido = {w[0]: len(w) > 5 for w in res["palabras"]}
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    if leido != {"A": False, "H": True, "I": True}:
        return f"lectura inesperada del PDF de comprobación: {sorted(leido.items())}"
    return None


def main() -> int:
    ruta, max_paginas = sys.argv[1], int(sys.argv[2])
    fallo = _autocomprobacion()
    if fallo:
        # fallar cerrado: sin el filtro, el texto oculto se leería como visible
        print(json.dumps({"error": "La versión instalada de pdfminer.six no es compatible con el filtro de texto oculto "
                                   f"({fallo}): no se puede leer el PDF con seguridad"}, ensure_ascii=False))
        return 4
    salida = []
    with pdfplumber.open(ruta) as pdf:
        _leer_ocg(pdf)
        for i, page in enumerate(pdf.pages[:max_paginas], start=1):
            try:
                salida.append(_leer_pagina(page, i))
            except _DemasiadoTexto as e:
                print(json.dumps({"error": str(e)}, ensure_ascii=False))
                return 3
            page.close()
    print(json.dumps({"paginas": salida}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
