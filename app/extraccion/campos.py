"""Extracción determinista de campos de factura con procedencia y confianza (reglas 2.0).

Diseñada para facturas de cualquier diseño, sin plantillas: combina estrategias genéricas y valida
los resultados entre sí.
- Etiqueta → valor: en el mismo bloque (separadores «:», «-», «. -»), en el bloque de la derecha o en
  la fila de debajo (cuadros con etiquetas en una fila y valores en la siguiente).
- Partes: localiza cada NIF (también si está espaciado, p. ej. «C . I . F . : B - 12345678»), construye
  una ventana de columna para separar bloques contiguos y deduce el papel (emisor/destinatario) con
  indicios ponderados; el NIF de la empresa usuaria es el indicio más fuerte.
- Tabla de conceptos: banda de cabecera de varias líneas, columnas por palabras clave y asignación
  de cada número a su columna por geometría (la cantidad puede ir antes o después del concepto).
- Importes: etiquetas en línea, cuadros de resumen y desgloses por tipo; un comprobador aritmético
  (base × tipo = cuota; bases + cuotas = total) confirma o, como último recurso, propone la combinación
  coherente, siempre marcada como tal.
Cada valor indica página, posición, método (texto del PDF u OCR) y confianza. Lo que no se encuentra
se deja vacío y se señala; nunca se inventa. El contenido del documento se trata como datos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .. import nif as nifmod
from ..importes import parse_importe, q2
from .texto import Linea, Pagina, Palabra, lineas as agrupar_lineas

VERSION_EXTRACTOR = "reglas-2.1"

RE_IMPORTE = re.compile(r"(?<![\d/.,])[-−]?(?:\d{1,3}(?:\.\d{3})+,\d{1,4}|\d{1,3}(?: \d{3})+,\d{1,4}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+,\d{1,4}|\d+\.\d{2})(?![\d/])")
RE_PORCENTAJE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
RE_FECHA = re.compile(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4}|\d{2})\b")
MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                     "septiembre", "octubre", "noviembre", "diciembre"], start=1)}
MESES["setiembre"] = 9
RE_FECHA_LARGA = re.compile(r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")\s+(?:de|del)\s+(\d{4})\b", re.I)
RE_FORMA_JURIDICA = re.compile(
    r"(?:^|[\s,])(S\.?\s?L\.?\s?U\.?|S\.?\s?L\.?\s?L\.?|S\.?\s?L\.?|S\.?\s?A\.?\s?U\.?|S\.?\s?A\.?|S\.?\s?COOP\.?(?:\s?AND\.?)?|"
    r"SOCIEDAD LIMITADA|SOCIEDAD AN[OÓ]NIMA|C\.?\s?B\.?|S\.?\s?C\.?|SLP|S\.?L\.?P\.?|A\.?I\.?E\.?)\s*[.,]?\s*$", re.I)
RE_ENTIDAD = re.compile(r"\b(comunidad\s+de\s+(?:propietarios|bienes|regantes|usuarios)|c\.\s?p\.|asociaci[oó]n|fundaci[oó]n|"
                        r"ayuntamiento|concello|ajuntament|diputaci[oó]n|consorcio|mancomunidad|cooperativa|cofrad[ií]a|federaci[oó]n|"
                        r"universidad|colegio\s+oficial|club|parroquia|congregaci[oó]n|sociedad\s+civil)\b", re.I)
RE_NEGOCIO = re.compile(r"\b(carpinter[ií]a|alimentaci[oó]n|taller(?:es)?|bar|restaurante|caf[eé]|cafeter[ií]a|peluquer[ií]a|farmacia|"
                        r"ferreter[ií]a|panader[ií]a|pasteler[ií]a|librer[ií]a|papeler[ií]a|tienda|comercial|construcciones|reformas|"
                        r"instalaciones|servicios|asesor[ií]a|gestor[ií]a|consultor[ií]a|estudio|cl[ií]nica|academia|hostal|hotel|"
                        r"transportes?|autocares|fruter[ií]a|carnicer[ií]a|pescader[ií]a|supermercado|distribuciones|suministros|"
                        r"electricidad|fontaner[ií]a|pinturas|dise[ñn]o|inmobiliaria|agencia|despacho|bufete|taxi|mudanzas|"
                        r"limpiezas?|jardiner[ií]a|cerrajer[ií]a|imprenta|grupo|centro|bodega|granja|viveros?)\b", re.I)
RE_REGISTRO_MERCANTIL = re.compile(r"registro\s+mercantil|inscrita|\btomo\s*[\d.]|\bfolio\s*:?\s*\d|\bhoja\s+[A-Z]{0,2}-?\d", re.I)
RE_CLIENTE = re.compile(r"^(cliente|destinatario|facturar\s+a|factura\s+a|datos\s+del\s+cliente|datos\s+de\s+facturaci[oó]n|"
                        r"sr\.?|sres\.?|señor(es)?|a\s+la\s+atenci[oó]n|bill\s+to|customer|client)\b", re.I)
RE_EMISOR = re.compile(r"^(emisor|proveedor|datos\s+del\s+emisor|from|vendedor|prestador)\b", re.I)
RE_VIA = re.compile(r"\b(c/|c\.|calle|callej[oó]n|avda\.?|avenida|av\.|plaza|pza\.?|paseo|p[ºo]\.|ps\.?|camino|ctra\.?|"
                          r"carretera|pol[ií]gono|pol\.|urb\.?|urbanizaci[oó]n|ronda|traves[ií]a|glorieta|bulevar|rambla|v[ií]a|barrio|b[ºo]\.?|"
                          r"lugar|paraje|parque|partida|cuesta|costanilla|kalea|carrer|r[uú]a|avinguda|pla[cç]a|passeig)\s", re.I)
RE_DOMICILIO = re.compile(RE_VIA.pattern + r"|\b\d{5}\b", re.I)
RE_CP_CIUDAD = re.compile(r"(?<![\w-])\d{5}\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]")
RE_CONTACTO = re.compile(r"tel[eé]?f|tfno|tlf|m[oó]vil|e-?mail|correo|@|www\.|http|fax", re.I)
RE_NIF_ETIQUETA = re.compile(r"\b(c\.?\s?i\.?\s?f\.?|n\.?\s?i\.?\s?f\.?|n\.?\s?i\.?\s?e\.?|d\.?\s?n\.?\s?i\.?|vat|nif-iva|nif/cif|cif/nif)\b", re.I)
RE_NIF_CANDIDATO = re.compile(r"(?<![A-Z0-9])(?:ES[\s-]?)?([A-Z][\s.-]?\d{7,8}[\s.-]?[0-9A-Z]|\d{8}[\s.-]?[A-Z]|"
                              r"\d{1,2}\.\d{3}\.\d{3}[\s.-]?[A-Z]|[XYZ][\s.-]?\d{1,2}\.?\d{3}\.?\d{3}[\s.-]?[A-Z]|[A-HJ-NP-SUVW][\s.-]?\d{2}\.\d{3}\.\d{3})(?![A-Z0-9])")
SEP = r"[\s:.\-–—=]*"

# Vocabulario de la tabla de conceptos
COLUMNAS = [
    ("cantidad", re.compile(r"^(uds?\.?|unidades|cant\.?|cantidad|n[º°]\s*uds|horas|h\.?|qty|cant|alumnos|participantes|personas|pax|"
                            r"jornadas|d[ií]as|sesiones|kg|kilos|bultos|cajas|unid\.?)$", re.I)),
    ("concepto", re.compile(r"^(concepto|conceptos|descripci[oó]n|detalle|servicio|servicios|art[ií]culo|producto|description)$", re.I)),
    ("precio", re.compile(r"^(precio|p\.?\s?u\.?|p\.\s?unit\.?|unitario|unit\.?|€/ud\.?|tarifa|precio/h|€/h|importe/h|price|"
                          r"precio/\w+|€/\w+|pvp|p\.v\.p\.?)$", re.I)),
    ("dto", re.compile(r"^(dto\.?|desc\.?|descuento|%\s?dto\.?|dto\s?%)$", re.I)),
    ("iva", re.compile(r"^(iva|%\s?iva|iva\s?%|i\.v\.a\.?|tipo)$", re.I)),
    ("importe", re.compile(r"^(importe|total|subtotal|neto|base|amount|importe\s?total)$", re.I)),
    ("codigo", re.compile(r"^(c[oó]digo|ref\.?|referencia|cod\.?)$", re.I)),
    ("otro", re.compile(r"^(periodo|per[ií]odo|fecha|fechas|lote|matr[ií]cula|serie)$", re.I)),
]
RE_SIGUE = re.compile(r"suma\s+y\s+sigue|contin[úu]a\s+en\s+(?:la\s+)?(?:p[áa]gina|hoja)|sigue\s+en\s+(?:la\s+)?(?:p[áa]gina|hoja)|"
                      r"\(contin[úu]a\)|a\s+la\s+vuelta", re.I)
RE_ARRASTRE = re.compile(r"suma\s+anterior|\(continuaci[oó]n\)|viene\s+de\s+(?:la\s+)?(?:p[áa]gina|hoja)|^\s*s(?:uma)?\.?\s+anterior", re.I)
RE_FIN_TABLA = re.compile(r"^(base\s+imponible|b\.\s?imponible|subtotal|suma\s+(?:total|bases?|neto|importes?|(?:de\s+)?conceptos|(?:de\s+)?l[ií]neas|parcial)|"
                          r"total\s+(?:factura|base|bruto|neto|conceptos|l[ií]neas|importe|general|eur|€)|i\.?\s?v\.?\s?a\.?\s+\d|"
                          r"cuota\s+(?:de\s+)?i\.?\s?v\.?\s?a|forma\s+de\s+pago|observaciones|vencimiento|retenci[oó]n|desglose|"
                          r"resumen|la\s+presente\s+factura|importe\s+total)\b|^total\s*:?\s*$", re.I)

ETIQUETAS_IMPORTE = [
    ("pagar", r"total\s+a\s+pagar|l[ií]quido\s+a\s+pagar|neto\s+a\s+pagar|importe\s+a\s+pagar|a\s+pagar|total\s+l[ií]quido|"
              r"amount\s+due|balance\s+due|total\s+due|net\s+payable|total\s+a\s+percibir|l[ií]quido\s+a\s+percibir|a\s+percibir|"
              r"total\s+a\s+cobrar|import\s+a\s+pagar|total\s+a\s+abonar"),
    # retención de garantía (obra): retiene parte del pago, no es IRPF ni cambia la base, la cuota o el total
    ("garantia", r"retenci[oó]n\s+(?:de\s+|por\s+|en\s+concepto\s+de\s+)?garant[ií]a|fondo\s+de\s+garant[ií]a|garant[ií]a\s+(?:\d|del?\b)"),
    ("retencion", r"retenci[oó]n?(?:\s+(?:de\s+)?i\.?\s?r\.?\s?p\.?\s?f\.?)?|i\.?\s?r\.?\s?p\.?\s?f\.?|withholding(?:\s+tax)?"),
    ("recargo", r"total\s+recargo(?:\s+(?:de\s+)?equivalencia)?|recargo\s+(?:de\s+)?equivalencia|cuota\s+r\.\s?e\.|\br\.\s?e\.(?=\s|$|:)"),
    ("total", r"total\s+factura|importe\s+total|import\s+total|invoice\s+total|total\s+amount|total\s+(?:eur|€)|total\s+documento|"
              r"total(?!\s+(?:iva|i\.v\.a|cuota|base|a\s+pagar|l[ií]quido|neto|recargo|r\.\s?e\.|bruto|l[ií]neas|descuentos?|unidades|bultos|kilos|horas))"),
    ("cuota", r"cuota\s+(?:de\s+)?i\.?\s?v\.?\s?a\.?|quota\s+(?:d['’]\s?)?i\.?\s?v\.?\s?a\.?|cota\s+(?:de\s+)?i\.?\s?v\.?\s?[ae]\.?|total\s+i\.?\s?v\.?\s?a\.?|importe\s+i\.?\s?v\.?\s?a\.?|"
              r"suma\s+(?:de\s+)?cuotas?(?:\s+(?:de\s+)?i\.?\s?v\.?\s?a\.?)?|total\s+cuotas?(?:\s+(?:de\s+)?i\.?\s?v\.?\s?a\.?)?|"
              r"\bcuotas?\b(?!\s+(?:fija|fijas|mensual|mensuales|anual|de\s+(?:alta|socio|servicio|mantenimiento|conexi[oó]n|abono)|r\.?\s?e))"),
    ("base", r"base\s+imponible|base\s+imp\.?|total\s+base(?:\s+imponible)?|b\.\s?imponible|importe\s+neto|subtotal|suma\s+neto|"
             r"suma\s+(?:de\s+)?bases?(?:\s+imponibles?)?|total\s+bases?(?:\s+imponibles?)?|taxable\s+(?:base|amount)|^base\b|\bbase\b"),
    ("iva", r"\bi\.?\s?v\.?\s?a\.?(?![a-z])|\biva\b|\bvat\b|\bive\b|\bbez\b"),
]


@dataclass
class Fila:
    """Fila visual: bloques de texto a la misma altura en una página."""
    pagina: int
    bloques: list[Linea]

    @property
    def palabras(self) -> list[Palabra]:
        return sorted([p for b in self.bloques for p in b.palabras], key=lambda p: p.x0)

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palabras)

    @property
    def top(self): return min(b.top for b in self.bloques)

    @property
    def bottom(self): return max(b.bottom for b in self.bloques)


def _campo(valor, *, texto=None, palabras: list[Palabra] | None = None, pagina: int | None = None, linea: Linea | None = None,
           lineas_: list[Linea] | None = None, metodo=None, confianza=None, aviso=None):
    if palabras is None:
        ls = lineas_ or ([linea] if linea else [])
        palabras = [p for l in ls for p in l.palabras]
        pagina = ls[0].pagina if ls else pagina
    c = {"valor": valor, "texto": texto}
    if palabras:
        c["pagina"] = pagina
        c["bbox"] = [round(min(p.x0 for p in palabras), 1), round(min(p.top for p in palabras), 1),
                     round(max(p.x1 for p in palabras), 1), round(max(p.bottom for p in palabras), 1)]
        c["metodo"] = metodo or ("ocr" if any(p.metodo == "ocr" for p in palabras) else "texto_pdf")
        conf = min(p.conf for p in palabras)
        c["confianza"] = round(confianza if confianza is not None else (0.97 if c["metodo"] == "texto_pdf" else conf), 2)
    else:
        c["metodo"] = metodo or "no_encontrado"
        c["confianza"] = confianza if confianza is not None else 0.0
    c["avisos"] = [aviso] if aviso else []
    return c


def _vacio(aviso="No encontrado en el documento"):
    return {"valor": None, "texto": None, "metodo": "no_encontrado", "confianza": 0.0, "avisos": [aviso]}


def _misma_linea(a: Palabra, b: Palabra) -> bool:
    h = min(a.bottom - a.top, b.bottom - b.top)
    return abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) <= max(1.5, 0.35 * h)


def _unir(a: Palabra, b: Palabra, sep: str) -> Palabra:
    return Palabra(a.texto + sep + b.texto, a.x0, min(a.top, b.top), b.x1, max(a.bottom, b.bottom), min(a.conf, b.conf), a.metodo)


def _normalizar_palabras(pals: list[Palabra]) -> list[Palabra]:
    """Une lo que el PDF separa sin serlo: títulos con letras espaciadas («F A C T U R A») e importes con espacio como
    separador de miles («2 450,00», «1 234 567,89»; un teléfono «941 000 000», sin decimales, no se une)."""
    orden = sorted(pals, key=lambda p: (round((p.top + p.bottom) / 2 / 3), p.x0))
    tmp: list[Palabra] = []
    racha: list[Palabra] = []

    def cerrar_racha():
        if len(racha) >= 3:
            w = racha[0]
            for x in racha[1:]:
                w = _unir(w, x, "")
            tmp.append(w)
        else:
            tmp.extend(racha)
        racha.clear()
    for p in orden:
        if re.fullmatch(r"[A-ZÁÉÍÓÚÑÜ]", p.texto):
            h = max(1.0, p.bottom - p.top)
            if racha and (not _misma_linea(racha[-1], p) or not (0 <= p.x0 - racha[-1].x1 <= 1.2 * h)):
                cerrar_racha()
            racha.append(p)
            continue
        cerrar_racha()
        tmp.append(p)
    cerrar_racha()
    out: list[Palabra] = []
    i = 0
    while i < len(tmp):
        p = tmp[i]
        if re.fullmatch(r"-?\d{1,3}", p.texto):
            grupo, j = [p], i
            while j + 1 < len(tmp):
                q, prev = tmp[j + 1], grupo[-1]
                if not (_misma_linea(prev, q) and 0 <= q.x0 - prev.x1 <= 0.5 * max(1.0, prev.bottom - prev.top)):
                    break
                if re.fullmatch(r"\d{3}", q.texto):
                    grupo.append(q)
                    j += 1
                    continue
                if re.fullmatch(r"\d{3}[.,]\d{1,4}€?", q.texto):
                    grupo.append(q)
                    j += 1
                break
            if len(grupo) >= 2 and re.fullmatch(r"\d{3}[.,]\d{1,4}€?", grupo[-1].texto):
                w = grupo[0]
                for x in grupo[1:]:
                    w = _unir(w, x, " ")
                out.append(w)
                i = j + 1
                continue
        out.append(p)
        i += 1
    return out


MESES_ABR = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10,
             "nov": 11, "dic": 12}
RE_FECHA_ABR = re.compile(r"\b(\d{1,2})[\s/.-]+(" + "|".join(MESES_ABR) + r")[a-záéíóú]*\.?[\s/.-]+(?:de\s+)?(\d{4})\b", re.I)
RE_FECHA_ISO = re.compile(r"\b(20\d{2}|19\d{2})-(\d{2})-(\d{2})\b")


def _fecha_iso(texto: str) -> str | None:
    m = RE_FECHA_ISO.search(texto)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = RE_FECHA.search(texto)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y) + (2000 if len(y) == 2 else 0)
        if 1 <= d <= 31 and 1 <= mth <= 12 and 1990 <= y <= 2100:
            return f"{y:04d}-{mth:02d}-{d:02d}"
    m = RE_FECHA_LARGA.search(texto)
    if m:
        return f"{int(m.group(3)):04d}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = RE_FECHA_ABR.search(texto)
    if m and 1 <= int(m.group(1)) <= 31:
        return f"{int(m.group(3)):04d}-{MESES_ABR[m.group(2).lower()[:3]]:02d}-{int(m.group(1)):02d}"
    return None


def _limpiar_cifra(t: str) -> str:
    t = t.replace("\u00a0", " ").replace("\u202f", " ").strip(".:;€ ").replace("−", "-")
    if t.startswith("(") and not t.endswith(")"):
        t = t[1:]
    elif t.endswith(")") and not t.startswith("("):
        t = t[:-1]
    return t.strip(".:;€ ")


def _es_importe(t: str) -> bool:
    return bool(RE_IMPORTE.fullmatch(_limpiar_cifra(t)))


def _num(t: str) -> Decimal | None:
    t = _limpiar_cifra(t)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):   # 5.120 (entero con separador de miles)
        return Decimal(t.replace(".", ""))
    if re.fullmatch(r"-?\d{1,3}(?: \d{3})+,\d{1,4}", t):   # 2 450,00 (espacio como separador de miles)
        t = t.replace(" ", "")
    if t.count(",") > 1 and "." in t and t.rfind(".") > t.rfind(","):   # 1,234.56 (formato inglés)
        t = t.replace(",", "")
    return parse_importe(t)


def _iban_valido(iban: str) -> bool:
    s = iban[4:] + iban[:4]
    try:
        return int("".join(str(int(ch, 36)) for ch in s)) % 97 == 1
    except ValueError:
        return False


class Extractor:
    def __init__(self, paginas: list[Pagina], nombre_archivo: str = "", nif_empresa: str | None = None):
        paginas = [Pagina(pg.numero, pg.ancho, pg.alto, _normalizar_palabras(pg.palabras), pg.metodo, pg.conf_media)
                   if hasattr(pg, "metodo") else pg for pg in paginas]
        self.paginas = paginas
        self.alto = {p.numero: p.alto for p in paginas}
        self.ancho = {p.numero: p.ancho for p in paginas}
        self.lineas = agrupar_lineas(paginas)
        self.filas = self._filas()
        self.nombre_archivo = nombre_archivo
        self.nif_empresa = nifmod.normalizar(nif_empresa) if nif_empresa else None
        self._tabla = None

    # --- geometría ------------------------------------------------------------------------------------
    def _filas(self) -> list[Fila]:
        filas: list[Fila] = []
        for l in self.lineas:
            for f in filas:
                if f.pagina == l.pagina and min(f.bottom, l.bottom) - max(f.top, l.top) > 0.4 * (l.bottom - l.top):
                    f.bloques.append(l)
                    break
            else:
                filas.append(Fila(l.pagina, [l]))
        filas.sort(key=lambda f: (f.pagina, f.top))
        return filas

    def _fila_de(self, linea: Linea) -> Fila:
        for f in self.filas:
            if linea in f.bloques:
                return f
        return Fila(linea.pagina, [linea])

    def _a_la_derecha(self, linea: Linea) -> list[Linea]:
        f = self._fila_de(linea)
        return sorted([b for b in f.bloques if b.x0 >= linea.x1 - 1 and b is not linea], key=lambda b: b.x0)

    def _debajo(self, x0: float, x1: float, linea: Linea, max_dist=28) -> Linea | None:
        cands = [l for l in self.lineas if l.pagina == linea.pagina and 0 < l.top - linea.bottom <= max_dist
                 and min(l.x1, x1) - max(l.x0, x0) > 0]
        return min(cands, key=lambda l: l.top) if cands else None

    @staticmethod
    def _es_etiqueta(texto: str) -> bool:
        t = texto.strip()
        return bool(re.search(r":\s*$", t)) or bool(re.match(
            r"^(n[º°o]\.?|fecha|cliente|contrato|pedido|certificaci|obra|proyecto|vencimiento|forma\s+de\s+pago|iban|"
            r"c\.?i\.?f|n\.?i\.?f|base|iva|total|importe|descripci|partida|expediente|referencia|n[úu]mero|n[úu]m\.|"
            r"cód\.?|c[oó]digo|p[áa]gina|serie|comercial|agente|ruta)\b", t, re.I))

    # --- etiqueta → valor -----------------------------------------------------------------------------------
    def candidatos(self, patron: str, validar, excluir: str | None = None, zona_sup: float | None = None,
                   abajo: bool = True) -> list[tuple]:
        """Todas las apariciones de una etiqueta con el valor asociado. Devuelve (valor, palabras, pagina, texto, estrategia)."""
        re_et = re.compile(patron, re.I)
        re_exc = re.compile(excluir, re.I) if excluir else None
        out = []
        self.tabla()
        en_tabla = {id(b) for f in self.filas if id(f) in self._ids_filas_tabla for b in f.bloques}
        for l in self.lineas:
            if id(l) in en_tabla:
                continue
            if zona_sup is not None and l.top > zona_sup * self.alto[l.pagina]:
                continue
            t = l.texto
            if re_exc and re_exc.search(t):
                continue
            for m in re_et.finditer(t):
                resto = re.sub("^" + SEP, "", t[m.end():])
                resto = re.sub(r"^\([^)\d]{1,14}\)\s*:?\s*", "", resto)  # «(PO)», «(EUR)»
                mt = re.match(r"^/\s*[A-Za-z][A-Za-z .º°#/]*?(?::\s*|\s*$)", resto)
                if mt and not re.search(r"\d", mt.group(0)):
                    resto = resto[mt.end():]  # traducción del rótulo
                # palabras del resto (para la procedencia)
                pal_resto = self._palabras_desde(l, m.end())
                # calificativo del rótulo sin valor («Contrato de suscripción», «Periodo de servicio») con el valor a la derecha
                calificativo = bool(re.fullmatch(r"(?:de|del|de\s+la)\s+[a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?\s*:?", resto.strip(), re.I))
                if calificativo:
                    vec = self._a_la_derecha(l)
                    if vec and validar(vec[0].texto) is not None and not self._es_etiqueta(vec[0].texto) \
                            and id(vec[0]) not in en_tabla:
                        out.append((validar(vec[0].texto), vec[0].palabras, l.pagina, t + " " + vec[0].texto, "derecha"))
                        continue
                v = None if calificativo else validar(resto)
                if v is None:
                    # «Expediente municipal: X», «Fecha de emisión del documento: X»: calificativo antes de los dos puntos
                    mq = re.match(r"^([^\d:]{1,30}?)\s*:\s*(.+)$", resto)
                    if mq and not re.search(r"\d", mq.group(1)):
                        v = validar(mq.group(2))
                        resto = mq.group(2) if v is not None else resto
                if v is not None:
                    out.append((v, pal_resto or l.palabras, l.pagina, t, "mismo_bloque"))
                    continue
                rejilla = bool(resto.strip(" :.-")) and (self._es_etiqueta(resto) or calificativo)
                if resto.strip(" :.-") and not rejilla:
                    continue  # hay texto tras la etiqueta pero no es un valor válido
                vecinos = [x for x in self._a_la_derecha(l) if id(x) not in en_tabla]
                if vecinos and not rejilla and not self._es_etiqueta(vecinos[0].texto):
                    v = validar(vecinos[0].texto)
                    if v is not None:
                        out.append((v, vecinos[0].palabras, l.pagina, t + " " + vecinos[0].texto, "derecha"))
                        continue
                if abajo:
                    pal_et = self._palabras_rango(l, m.start(), m.end())
                    x0 = min(p.x0 for p in pal_et) - 30 if pal_et else l.x0
                    x1 = max(p.x1 for p in pal_et) + 60 if pal_et else l.x1
                    deb = self._debajo(x0, x1, l)
                    if deb and not self._es_etiqueta(deb.texto):
                        pals = [p for p in deb.palabras if x0 - 20 <= (p.x0 + p.x1) / 2 <= x1 + 40]
                        v = validar(" ".join(p.texto for p in pals)) if pals else None
                        if v is not None:
                            out.append((v, pals, l.pagina, t + " / " + deb.texto, "debajo"))
        return out

    @staticmethod
    def _palabras_desde(l: Linea, pos: int) -> list[Palabra]:
        out, k = [], 0
        for p in l.palabras:
            ini = l.texto.find(p.texto, k)
            if ini >= pos or ini + len(p.texto) > pos:
                if ini + len(p.texto) > pos:
                    out.append(p)
            k = ini + len(p.texto)
        return out

    @staticmethod
    def _palabras_rango(l: Linea, a: int, b: int) -> list[Palabra]:
        out, k = [], 0
        for p in l.palabras:
            ini = l.texto.find(p.texto, k)
            fin = ini + len(p.texto)
            if fin > a and ini < b:
                out.append(p)
            k = fin
        return out

    def _mejor(self, cands, preferir=None):
        if not cands:
            return None
        if preferir:
            cands = sorted(cands, key=preferir)
        return cands[0]

    def _campo_de(self, cand, valor=None, conf=None, aviso=None):
        v, pals, pag, texto, estr = cand
        return _campo(valor if valor is not None else v, texto=texto, palabras=pals, pagina=pag, confianza=conf, aviso=aviso)

    # --- validadores --------------------------------------------------------------------------------------------
    @staticmethod
    def _v_codigo(t: str):
        m = re.match(r"^([A-Z0-9][A-Z0-9/\-_.]{0,40})", t.strip(), re.I)
        if not m:
            return None
        v = m.group(1).rstrip(".-/")
        if not re.search(r"\d", v) or re.search(r"[ºª]", t.strip()[len(m.group(1)):len(m.group(1)) + 1] or ""):
            return None
        return v

    @staticmethod
    def _v_fecha(t: str):
        return _fecha_iso(t[:40])

    @staticmethod
    def _v_texto(t: str):
        t = t.strip(" :.-–")
        if not t or len(t) > 200:
            return None
        return t

    @staticmethod
    def _v_entero(t: str):
        m = re.match(r"^\s*(\d{1,6})\b", t)
        return m.group(1) if m else None

    # --- tipo, número y fechas ----------------------------------------------------------------------------------
    def tipo_documento(self):
        orden = [
            ("proforma", r"\bpro[\s-]?forma\b"), ("presupuesto", r"\bpresupuesto\b"),
            ("borrador", r"\bborrador\b"), ("albaran", r"\balbar[aá]n\b"),
            ("rectificativa", r"factura\s+rectificativa|\babono\b|rectificativa"),
            ("simplificada", r"factura\s+simplificada|\bticket\b"), ("factura", r"\bfactura\b|\binvoice\b"),
        ]
        alturas = sorted(p.bottom - p.top for l in self.lineas for p in l.palabras) or [10.0]
        mediana = alturas[len(alturas) // 2]
        pag1 = [l for l in self.lineas if l.pagina == 1]

        def buscar(lineas, pats, cond, por_tamano=False):
            hallados = []
            for prio, (tipo, pat) in enumerate(pats):
                for l in lineas:
                    m = re.search(pat, l.texto, re.I)
                    if m and not RE_REGISTRO_MERCANTIL.search(l.texto) and cond(l, m):
                        if not por_tamano:
                            return _campo(tipo, texto=l.texto, linea=l)
                        hallados.append((-max(p.bottom - p.top for p in l.palabras), l.top, prio, tipo, l))
            if hallados:
                # el título destacado manda sobre los rótulos de una rejilla («ALBARÁN», «PEDIDO»…)
                alto = min(h[0] for h in hallados)
                _, _, _, tipo, l = min((h for h in hallados if h[0] <= alto + 0.5), key=lambda h: (h[2], h[1]))
                return _campo(tipo, texto=l.texto, linea=l)
            return None

        def grande(l, m):  # título destacado: la palabra clave en un cuerpo de letra mayor que el texto normal
            return any(p.bottom - p.top >= 1.3 * mediana and re.search(pat_kw, p.texto, re.I) for p in l.palabras)

        def solo_titulo(l, m):  # la línea es solo el título («FACTURA», «FACTURA PROFORMA», «PRESUPUESTO»)
            return re.fullmatch(r"\s*(factura\s+)?[\w\s-]{0,3}" + re.escape(m.group(0)) + r"(\s+(rectificativa|simplificada|proforma))?\s*", l.texto, re.I) is not None

        pat_kw = r"factura|pro-?forma|presupuesto|borrador|albar[aá]n|rectificativa|abono|simplificada|ticket|invoice"
        r = buscar(pag1, orden, grande, True) or buscar(pag1, orden, solo_titulo, True)
        if r:
            return r
        # Sin título claro: cabecera por encima de la tabla de conceptos, sin las referencias («Presupuesto nº 45»)
        tabla_top = next((f.top for f in self.filas if f.pagina == 1 and len({self._clase(p.texto) for p in f.palabras} - {None}) >= 2),
                         0.4 * self.alto[1])
        cabecera = [l for l in pag1 if l.top < min(tabla_top, 0.4 * self.alto[1])]
        no_ref = lambda l, m: not re.match(r"\s*(n[º°o]\.?|n[úu]m(ero)?\.?|:|[A-Z]{0,4}[-/]?\d)", l.texto[m.end():], re.I)
        r = buscar(cabecera, orden, no_ref)
        if not r:
            for l in pag1:
                if re.search(r"\bfactura\s*(?:n[º°o]\.?|n[úu]m(?:ero)?\.?)\s*:?\s*[A-Z0-9]", l.texto, re.I) and \
                        not re.search(r"\b(?:proforma|rectificad|anterior|original)\b", l.texto, re.I):
                    return _campo("factura", texto=l.texto, linea=l, confianza=0.85)
            r = buscar(cabecera, orden, lambda l, m: True)
        if r:
            return r
        for tipo, pat in orden[:5]:
            for l in self.lineas:
                if re.search(pat, l.texto, re.I):
                    return _campo(tipo, texto=l.texto, linea=l, aviso="Indicado fuera de la cabecera")
        return _vacio("No se identifica el tipo de documento")

    def numero(self):
        excl = (r"certificaci|pedido|partida|tomo|folio|hoja|registro|tel[eé]?f|tfno|iban|cuenta|c\.?\s?i\.?\s?f|n\.?\s?i\.?\s?f|"
                r"cliente\s+n|contrato|expediente|proyecto|albar[aá]n")
        patrones = [
            (0, r"(?:factura|fra\.?|invoice)\s*(?:proforma\s*|rectificativa\s*)?(?:n[º°o*]\.?|n[úu]m(?:ero)?\.?|no\.?|#)"),
            (0, r"n[º°o*]\.?\s*(?:de\s+)?(?:factura|fra\.?)"),
            (0, r"n[úu]m(?:ero)?\.?\s+(?:de\s+)?factura"),
            (1, r"(?:proforma|presupuesto|borrador|documento)\s*(?:n[º°o*]\.?|n[úu]m(?:ero)?\.?)"),
            (1, r"(?:n[úu]mero|n[úu]m\.?|n[º°o*]\.?)\s*(?:de\s+)?(?:la\s+)?(?:proforma|presupuesto|documento|borrador)"),
            (2, r"^n[úu]mero\b|^n[úu]m\.?(?=\s)|^n[º°]\.?(?=\s|:)"),
            (3, r"(?:^|\s)factura\s+(?=[A-Z0-9-]*\d)"),
        ]
        tipo = self.tipo_documento().get("valor")
        cands = []
        for prio, pat in patrones:
            if prio == 1 and tipo not in ("proforma", "presupuesto", "borrador"):
                prio = 4  # en una factura, «Presupuesto nº» es una referencia
            for c in self.candidatos(pat, self._v_codigo, excluir=excl, zona_sup=0.6):
                l_txt = c[3]
                if RE_VIA.search(l_txt) and prio > 0:
                    continue
                cands.append((prio, c))
        if not cands:
            for l in self.lineas:
                if l.pagina == 1 and l.top < 0.35 * self.alto[1] and re.fullmatch(r"\s*(factura|invoice)\s*", l.texto, re.I):
                    deb = self._debajo(l.x0 - 40, l.x1 + 40, l, max_dist=30)
                    if deb:
                        v = self._v_codigo(deb.texto)
                        if v and re.search(r"\d", v) and not _fecha_iso(deb.texto):
                            cands.append((3, (v, deb.palabras, deb.pagina, l.texto + " / " + deb.texto, "debajo_titulo")))
                            break
        if not cands:
            return _vacio("No se encuentra el número de factura")
        digitos_archivo = re.findall(r"\d+", self.nombre_archivo)
        def clave(pc):
            prio, c = pc
            coincide = 0 if (digitos_archivo and re.findall(r"\d+", c[0]) and re.findall(r"\d+", c[0])[0].lstrip("0") in
                             [d.lstrip("0") for d in digitos_archivo]) else 1
            return (prio, coincide, c[1][0].top if c[1] else 0)
        prio, mejor = sorted(cands, key=clave)[0]
        campo = self._campo_de(mejor, conf=None if prio <= 1 else 0.8)
        distintos = {c[0] for p, c in cands if p == prio}
        if len(distintos) > 1:
            campo["avisos"].append(f"Varios números posibles: {', '.join(sorted(distintos))}")
            campo["confianza"] = min(campo["confianza"], 0.7)
        if digitos_archivo and re.findall(r"\d+", mejor[0]) and re.findall(r"\d+", mejor[0])[0].lstrip("0") in [d.lstrip("0") for d in digitos_archivo]:
            campo["avisos"].append("Coincide con el nombre del archivo")
        return campo

    def fecha(self):
        excl = r"vencimiento|operaci[oó]n|entrega|pedido|valor|nacimiento|cargo|firma|validez|caducidad|inicio|fin\b|desde|hasta|alta"
        pats = [r"fecha\s+(?:de\s+)?(?:la\s+)?(?:factura|emisi[oó]n|expedici[oó]n)", r"\bfecha\b", r"\bdate\b", r"\bde\s+fecha\b",
                r"^(?:emitida|expedida|emisi[oó]n|data(?:\s+(?:de\s+)?(?:factura|emissi[oó]))?|data)\b"]
        for pat in pats:
            cands = self.candidatos(pat, self._v_fecha, excluir=excl)
            if cands:
                return self._campo_de(sorted(cands, key=lambda c: (c[2], c[1][0].top if c[1] else 0))[0])
        for l in self.lineas:
            if l.top < 0.4 * self.alto[l.pagina] and not re.search(excl, l.texto, re.I):
                iso = _fecha_iso(l.texto)
                if iso:
                    return _campo(iso, texto=l.texto, linea=l, confianza=0.6, aviso="Fecha sin etiqueta: confirme")
        return _vacio("No se encuentra la fecha")

    def fecha_operacion(self):
        c = self.candidatos(r"fecha\s+(?:de\s+)?(?:la\s+)?(?:operaci[oó]n|prestaci[oó]n|entrega|realizaci[oó]n)", self._v_fecha)
        return self._campo_de(c[0]) if c else None

    # --- partes ---------------------------------------------------------------------------------------------------
    def _apariciones_nif(self):
        """(nif, linea, palabras) incluidos NIF con caracteres espaciados; y NIF con forma válida pero control erróneo."""
        validos, sospechosos = [], []
        for l in self.lineas:
            vistos = set()
            textos = [l.texto, re.sub(r"\s+", "", l.texto)]
            for txt in textos:
                for m in RE_NIF_CANDIDATO.finditer(txt.upper()):
                    n = nifmod.normalizar(m.group(1))
                    if n in vistos:
                        continue
                    if nifmod.es_valido(n):
                        vistos.add(n)
                        pals = [p for p in l.palabras if re.sub(r"[\s.\-]", "", p.texto.upper()) and
                                re.sub(r"[\s.\-]", "", p.texto.upper()) in n or n[-4:] in p.texto.upper()]
                        validos.append((n, l, pals or l.palabras))
                    elif nifmod.tipo(n) and RE_NIF_ETIQUETA.search(l.texto):
                        vistos.add(n)
                        sospechosos.append((n, l))
        return validos, sospechosos

    def _ventana(self, l: Linea, pals: list[Palabra]) -> tuple[float, float]:
        x0 = min(p.x0 for p in pals)
        x1 = max(p.x1 for p in pals)
        # el bloque de la etiqueta del NIF forma parte de la ventana
        return min(x0, l.x0 if (l.x1 - l.x0) < 260 else x0) - 25, max(x1, l.x1 if (l.x1 - l.x0) < 260 else x1) + 25

    def _palabras_en(self, l: Linea, x0: float, x1: float) -> list[Palabra]:
        return [p for p in l.palabras if x0 <= (p.x0 + p.x1) / 2 <= x1]

    def _parece_nombre(self, texto: str) -> bool:
        t = texto.strip()
        if len(t) < 4 or RE_CONTACTO.search(t) or RE_NIF_ETIQUETA.search(t) or RE_CLIENTE.match(t) or RE_EMISOR.match(t):
            return False
        if RE_FORMA_JURIDICA.search(t) or (RE_ENTIDAD.search(t) and not RE_VIA.search(t)):
            return True
        if RE_DOMICILIO.search(t) or re.search(r"\d", t) or self._es_etiqueta(t):
            return False
        palabras = [w for w in re.split(r"\s+", t) if w]
        mayus = [w for w in palabras if w[:1].isupper()]
        return len(palabras) >= 2 and len(mayus) >= max(2, len(palabras) - 2)

    def _bloque_de(self, nif_: str, l: Linea, pals: list[Palabra]) -> dict:
        x0, x1 = self._ventana(l, pals)
        info = {"nif": nif_, "linea": l, "pals": pals, "nombre": None, "nombre_pals": [], "dom": None, "dom_pals": [],
                "x0": x0, "x1": x1}
        t = l.texto
        m = RE_NIF_ETIQUETA.search(t)
        antes = (t[:m.start()] if m else t.upper().split(nif_[:3])[0]).strip(" -–·|,;:")
        if len(antes) >= 4 and self._parece_nombre(antes):
            info["nombre"], info["nombre_pals"] = antes, l.palabras[:max(1, len(antes.split()))]
        if not info["nombre"]:
            # Misma fila, otro bloque con forma jurídica (p. ej. pie con «RAZÓN S.L. | C.I.F.: …»)
            for b in sorted(self._fila_de(l).bloques, key=lambda b: abs(b.x1 - l.x0)):
                if b is not l and RE_FORMA_JURIDICA.search(b.texto) and self._parece_nombre(b.texto):
                    info["nombre"], info["nombre_pals"] = b.texto.strip(), b.palabras
                    break
        def solapa(x):  # el bloque pertenece a la columna del NIF (solapa o comparte alineación izquierda o derecha)
            ov = min(x.x1, x1) - max(x.x0, x0)
            return ov > 0 and (ov >= 0.3 * (x.x1 - x.x0) or abs(x.x0 - l.x0) <= 6 or abs(x.x1 - l.x1) <= 6
                               or abs((x.x0 + x.x1) / 2 - (l.x0 + l.x1) / 2) <= 15)
        superiores = sorted([x for x in self.lineas if x.pagina == l.pagina and 0 < l.top - x.top <= 110 and solapa(x)],
                            key=lambda x: -x.top)
        if not info["nombre"]:
            contiguas, prev = [], l
            for x in superiores:
                if prev.top - x.bottom > max(14.0, 1.6 * (x.bottom - x.top)) or RE_CLIENTE.match(x.texto) or RE_EMISOR.match(x.texto):
                    break
                contiguas.append(x)
                prev = x
            con_forma = [x for x in superiores if RE_FORMA_JURIDICA.search(x.texto) and self._parece_nombre(x.texto)]
            orden = con_forma + contiguas[::-1] + superiores
            if nifmod.tipo(nif_) and not nifmod.es_persona_juridica(nif_):
                # NIF de persona física: su nombre, no el comercial («Carpintería Arrabal», «Alimentación Rosendo»)
                personas = [x for x in contiguas + superiores if self._parece_nombre(x.texto) and not RE_NEGOCIO.search(x.texto)
                            and not RE_FORMA_JURIDICA.search(x.texto) and not RE_ENTIDAD.search(x.texto)]
                orden = personas + orden
            for x in orden:
                if re.match(r"^(att?\.?|a/a|atenci[oó]n|dpto\.?|departamento|attn)\b", x.texto.strip(), re.I):
                    continue
                if self._parece_nombre(x.texto):
                    info["nombre"], info["nombre_pals"] = x.texto.strip(), x.palabras
                    x0, x1 = min(x0, x.x0 - 10), max(x1, x.x1 + 10)
                    info["x0"], info["x1"] = x0, x1
                    break
        # Domicilio: bloques de la columna entre el nombre y el NIF, o justo debajo del NIF
        if info["nombre_pals"]:
            top_n = min(p.top for p in info["nombre_pals"])
            entre = [x for x in self.lineas if x.pagina == l.pagina and top_n + 1 < x.top < l.top - 1 and solapa(x)]
            partes, pals_d = [], []
            for x in sorted(entre, key=lambda x: x.top):
                txt = x.texto.strip()
                if not (self._parece_direccion(txt) or (partes and not self._es_etiqueta(txt) and len(txt) <= 40
                                                        and not re.search(r"\s[-·|]\s", txt))):
                    continue  # lema comercial, actividad («Mecánica · Electricidad»), etc.
                if txt and not RE_CONTACTO.search(txt) and not RE_CLIENTE.match(txt) and not self._parece_nombre(txt):
                    partes.append(txt.rstrip(","))
                    pals_d += x.palabras
            if partes:
                info["dom"], info["dom_pals"] = ", ".join(partes), pals_d
        if not info["dom"]:
            info["dom"], info["dom_pals"] = self._domicilio_en_linea(nif_, l)
        if not info["dom"] and antes and (RE_VIA.search(antes) or RE_CP_CIUDAD.search(antes)) and not self._parece_nombre(antes):
            segs = [x.strip(" ,;.-–·|") for x in self.SEP_PIE.split(antes)]
            segs = [x for x in segs if x and not RE_FORMA_JURIDICA.search(x) and not RE_CONTACTO.search(x)]
            if segs:
                info["dom"], info["dom_pals"] = ", ".join(segs), l.palabras
        if not info["dom"]:
            sig = sorted([x for x in self.lineas if x.pagina == l.pagina and 0 < x.top - l.top <= 45 and solapa(x)
                          and not RE_REGISTRO_MERCANTIL.search(x.texto)], key=lambda x: x.top)
            partes, pals_d, prev = [], [], l
            for x in sig:
                if x.top - prev.bottom > 16 or self._es_etiqueta(x.texto):
                    break
                # «Camino del Puerto 12 · 05635 Hoyos … · Tel. …»: lo que sigue al teléfono o al correo no es domicilio
                trozos = []
                for seg in self.SEP_PIE.split(x.texto):
                    if RE_CONTACTO.search(seg):
                        break
                    trozos.append(seg.strip(" ,;-–·|"))
                txt_d = ", ".join(t for t in trozos if t)
                if not txt_d:
                    break
                if RE_DOMICILIO.search(txt_d) or (partes and re.search(r"\(|\b[A-ZÁÉÍÓÚ][a-záéíóú]+$", txt_d)):
                    partes.append(txt_d)
                    pals_d += x.palabras
                    prev = x
                else:
                    break
            if partes:
                info["dom"], info["dom_pals"] = ", ".join(partes), pals_d
        return info

    @staticmethod
    def _parece_direccion(t: str) -> bool:
        return bool(RE_VIA.search(t) or RE_CP_CIUDAD.search(t) or re.search(r"\b\d{5}\b", t)
                    or re.search(r"^[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.'\s-]{2,40},\s*(?:n[º°]\s*)?\d{1,4}\b", t))

    SEP_PIE = re.compile(r"\s+[·|•–-]\s+|\s*[·|•]\s*")

    def _domicilio_en_linea(self, nif_: str, l: Linea):
        """Domicilio impreso a continuación del NIF en la misma línea y, si sigue, en la línea siguiente (pies de página)."""
        t = l.texto
        fin = None
        for m in RE_NIF_CANDIDATO.finditer(t.upper()):
            if nifmod.normalizar(m.group(1)) == nif_:
                fin = m.end()
                break
        if fin is None:
            return None, []
        partes = []
        resto = [t[fin:]]
        sig = [x for x in self.lineas if x.pagina == l.pagina and 0 < x.top - l.top <= 18 and min(x.x1, l.x1) - max(x.x0, l.x0) > 0]
        if sig and re.match(r"^\s*\d{5}\b", sig[0].texto):
            resto.append(sig[0].texto)
        for r in resto:
            for seg in self.SEP_PIE.split(r):
                seg = seg.strip(" ,;.-–·|")
                if not seg:
                    continue
                if RE_CONTACTO.search(seg) or RE_REGISTRO_MERCANTIL.search(seg) or re.search(r"\b(www\.|https?:|@)", seg, re.I):
                    break
                if RE_DOMICILIO.search(seg) or re.search(r"\b\d{5}\b", seg) or (partes and len(seg) <= 40):
                    partes.append(seg)
        if not partes:
            return None, []
        pals = [p for p in l.palabras if any(p.texto in x for x in partes)]
        return ", ".join(partes), pals or l.palabras

    def _contrastar_nombre_ocr(self, b: dict) -> None:
        """En un escaneo, la razón social puede leerse mejor en otro sitio (p. ej. el pie con los datos registrales)."""
        import difflib
        import unicodedata

        def n(t):
            return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().casefold())

        def conf(pals):  # media de las palabras con letras (la puntuación suelta no cuenta)
            v = [p.conf for p in pals if re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3,}", p.texto)]
            return sum(v) / len(v) if v else 0.0
        actual, c_act = b["nombre"], conf(b["nombre_pals"])
        for l in self.lineas:
            for seg in re.split(r"\s*(?:[·|]|:-|\s-\s|inscrita|registro|n\.?i\.?f|c\.?i\.?f)\s*", l.texto, flags=re.I):
                seg = seg.strip(" ,.:;-")
                if not seg or seg == actual or not RE_FORMA_JURIDICA.search(seg):
                    continue
                r = difflib.SequenceMatcher(None, n(seg), n(actual)).ratio()
                pals = [p for p in l.palabras if p.texto.strip(" ,.:;-") and p.texto.strip(" ,.:;-") in seg]
                if r >= 0.8 and conf(pals) > c_act:
                    b["nombre"], b["nombre_pals"], c_act = seg, pals, conf(pals)

    def partes(self):
        validos, sospechosos = self._apariciones_nif()
        bloques = []
        for n, l, pals in validos:
            b = self._bloque_de(n, l, pals)
            alto = self.alto[l.pagina]
            em, de, motivos = 0.0, 0.0, []
            if l.top > 0.75 * alto:
                em += 3; motivos.append("pie de página")
            vecinas = [x for x in self.lineas if x.pagina == l.pagina and abs(x.top - l.top) < 40]
            if any(RE_REGISTRO_MERCANTIL.search(x.texto) for x in vecinas):
                em += 2; motivos.append("junto a datos registrales")
            sup = [x for x in self.lineas if x.pagina == l.pagina and 0 <= l.top - x.top <= 110
                   and min(x.x1, b["x1"]) - max(x.x0, b["x0"]) > 0]
            if any(RE_CLIENTE.match(" ".join(p.texto for p in self._palabras_en(x, b["x0"] - 40, b["x1"]))) for x in sup):
                de += 4; motivos.append("bloque de cliente")
            if any(RE_EMISOR.match(x.texto) for x in sup):
                em += 3; motivos.append("bloque de emisor")
            if self.nif_empresa and n == self.nif_empresa:
                em += 2.5; motivos.append("NIF de la empresa configurada")
            if l.top < 0.3 * alto and "bloque de cliente" not in motivos:
                if (b["x0"] + b["x1"]) / 2 < self.ancho[l.pagina] * 0.45:
                    em += 0.6
                else:
                    de += 0.6
            b.update(em=em, de=de, motivos=motivos)
            bloques.append(b)
        # Un mismo NIF puede aparecer varias veces: se conserva la aparición más informativa por NIF
        por_nif: dict[str, dict] = {}
        for b in bloques:
            ant = por_nif.get(b["nif"])
            if not ant:
                por_nif[b["nif"]] = b
            else:
                ant["em"] += b["em"] * 0.5
                ant["de"] += b["de"] * 0.5
                ant["motivos"] = sorted(set(ant["motivos"] + b["motivos"]))
                if not ant["nombre"] and b["nombre"]:
                    ant.update(nombre=b["nombre"], nombre_pals=b["nombre_pals"])
                if not ant["dom"] and b["dom"]:
                    ant.update(dom=b["dom"], dom_pals=b["dom_pals"])
        unicos = list(por_nif.values())
        emisor = destinatario = None
        if unicos:
            emisor = max(unicos, key=lambda b: b["em"] - b["de"])
            if emisor["em"] - emisor["de"] <= 0 and len(unicos) > 1:
                # sin indicios de emisor: el que no es claramente cliente
                emisor = max(unicos, key=lambda b: -b["de"])
            otros = [b for b in unicos if b is not emisor]
            if otros:
                destinatario = max(otros, key=lambda b: b["de"] - b["em"])
            elif emisor["de"] > emisor["em"]:
                destinatario, emisor = emisor, None
        # Domicilio del emisor si no está junto a su NIF: líneas de dirección de la cabecera fuera del bloque del cliente
        if emisor and not emisor["dom"]:
            cab = [x for x in self.lineas if x.pagina == 1 and x.top < 0.35 * self.alto[1]]
            partes, pals = [], []
            for x in cab:
                if destinatario and min(x.x1, destinatario["x1"]) - max(x.x0, destinatario["x0"]) > 0.3 * (x.x1 - x.x0):
                    continue  # pertenece al bloque del destinatario
                txt = x.texto.strip()
                etiqueta = re.match(r"^([^\d:]{2,30}?)(?:\.\s*[-–]|:)\s", txt)
                if etiqueta and not re.search(r"domicilio|direcci[oó]n|sede|oficinas?", etiqueta.group(1), re.I):
                    continue
                if txt and (RE_VIA.search(txt) or RE_CP_CIUDAD.search(txt)) and not RE_CONTACTO.search(txt) and not RE_NIF_ETIQUETA.search(txt) \
                        and not RE_FORMA_JURIDICA.search(txt) and not _fecha_iso(txt):
                    partes.append(txt.rstrip(","))
                    pals += x.palabras
            if partes:
                emisor["dom"], emisor["dom_pals"] = ", ".join(partes), pals
        if emisor and emisor["nombre"] and any(p.metodo == "ocr" for p in emisor["nombre_pals"]):
            self._contrastar_nombre_ocr(emisor)
        out = {}
        for rol, b in (("emisor", emisor), ("destinatario", destinatario)):
            if not b:
                out[rol] = {"nombre": _vacio(f"No se identifica el {rol}"), "nif": _vacio(f"NIF del {rol} no encontrado"),
                            "domicilio": _vacio()}
                continue
            l = b["linea"]
            cn = _campo(b["nif"], texto=l.texto, palabras=b["pals"], pagina=l.pagina)
            cn["avisos"].append("Rol deducido: " + ", ".join(b["motivos"] or ["posición"]))
            seguro = (rol == "destinatario" and emisor is not None and self.nif_empresa and emisor["nif"] == self.nif_empresa
                      and len(unicos) == 2) or (rol == "emisor" and self.nif_empresa == b["nif"] and "bloque de cliente" not in b["motivos"])
            if not seguro and (b["em"] - b["de"]) * (1 if rol == "emisor" else -1) < 1:
                cn["confianza"] = min(cn["confianza"], 0.75)
                cn["avisos"].append("Papel deducido con pocos indicios: confirme emisor y destinatario")
            out[rol] = {
                "nombre": _campo(b["nombre"], texto=b["nombre"], palabras=b["nombre_pals"], pagina=l.pagina) if b["nombre"]
                else _vacio("Razón social no encontrada"),
                "nif": cn,
                "domicilio": _campo(b["dom"], texto=b["dom"], palabras=b["dom_pals"], pagina=l.pagina) if b["dom"]
                else _vacio("Domicilio no encontrado"),
            }
        out["nifs_detectados"] = sorted(por_nif)
        out["nifs_no_validos"] = [{"texto": n, "pagina": l.pagina, "bbox": l.bbox(), "linea": l.texto} for n, l in sospechosos]
        return out

    # --- tabla de conceptos ------------------------------------------------------------------------------------------
    def _clase(self, palabra: str) -> str | None:
        t = palabra.strip(" .:").lower()
        for clase, rx in COLUMNAS:
            if rx.match(t) or rx.match(t + "."):
                return clase
        return None

    def tabla(self):
        if self._tabla is not None:
            return self._tabla
        # Banda de cabecera: filas con palabras de al menos dos clases distintas, incluidas concepto o importe
        cab = None
        for f in self.filas:
            clases = {self._clase(p.texto) for p in f.palabras} - {None, "otro"}
            if len(clases) >= 2 and ({"concepto", "importe"} & clases or {"cantidad", "precio"} <= clases):
                if "concepto" not in clases and not {"cantidad", "precio"} <= clases and self._fila_de_totales(f):
                    continue  # «Base imponible | IVA | Total factura | …» encima de la tabla
                if len(f.palabras) <= 14:
                    cab = f
                    break
        self._ids_filas_tabla = set()
        aviso_tabla = None
        if not cab:
            inferida = self._columnas_inferidas()
            if not inferida:
                self._tabla = ([], "No se identifica la tabla de conceptos")
                return self._tabla
            cols, cab = inferida
            banda = [cab]
            aviso_tabla = "Tabla sin cabecera legible: columnas deducidas por la alineación de las cifras; revise las líneas"
        else:
            # cabecera en uno o dos renglones; una fila con cifras ya es una línea (aunque su concepto diga «tipo»)
            banda = [f for f in self.filas if f.pagina == cab.pagina and abs(f.top - cab.top) <= 16
                     and any(self._clase(p.texto) for p in f.palabras)
                     and (f is cab or not any(_es_importe(p.texto) or re.fullmatch(r"\d+", p.texto) for p in f.palabras))]
            cols = self._columnas_cabecera(banda)
        return self._cuerpo_tabla(cab, banda, cols, aviso_tabla)

    def _columnas_inferidas(self):
        """Filas con texto y una cifra al final, antes de los totales: columnas por alineación del borde derecho."""
        filas = self.filas
        fin = next((k for k, f in enumerate(filas) if self._fila_de_totales(f)), len(filas))
        cand = []
        for k, f in enumerate(filas[:fin]):
            pals = f.palabras
            if len(pals) < 2 or not _es_importe(pals[-1].texto) or self._etiquetas_en(pals):
                continue
            nums = [p for p in pals if _es_importe(p.texto) or re.fullmatch(r"\d{1,5}(?:[.,]\d{1,3})?", p.texto)]
            textos = [p for p in pals if p not in nums and self.RE_PALABRA.search(p.texto)]
            if not textos or max(p.x1 for p in textos) > nums[-1].x0 or re.search(r":\s*\S+\s*$", f.texto):
                continue
            cand.append((k, f, nums, textos))
        if not cand:
            return None
        # el grupo más largo de filas próximas
        grupos, g = [], [cand[0]]
        for c in cand[1:]:
            ant = g[-1][1]
            if c[1].pagina == ant.pagina and c[1].top - ant.bottom <= 60:
                g.append(c)
            else:
                grupos.append(g)
                g = [c]
        grupos.append(g)
        g = max(grupos, key=len)
        if len(g) < 2 and len(cand) < 2:
            return None
        clusters = []
        for _, f, nums, _ in g:
            for p in nums:
                for cl in clusters:
                    if abs(cl["x1"] - p.x1) <= 12:
                        cl["n"] += 1
                        cl["x0"], cl["x1r"] = min(cl["x0"], p.x0), max(cl["x1r"], p.x1)
                        break
                else:
                    clusters.append({"x0": p.x0, "x1": p.x1, "x1r": p.x1, "n": 1})
        clusters = sorted([c for c in clusters if c["n"] >= max(1, len(g) // 2)], key=lambda c: c["x1"])
        if not clusters:
            return None
        clases = {1: ["importe"], 2: ["precio", "importe"], 3: ["cantidad", "precio", "importe"]}.get(len(clusters),
                                                                                                  ["cantidad", "precio", "dto", "importe"])
        clusters = clusters[-len(clases):]
        cols = [{"clase": c, "x0": cl["x0"], "x1": cl["x1r"], "texto": ""} for c, cl in zip(clases, clusters)]
        x_txt = min(p.x0 for _, _, _, t in g for p in t)
        cols.append({"clase": "concepto", "x0": x_txt, "x1": min(cl["x0"] for cl in clusters) - 5, "texto": ""})
        cols.sort(key=lambda c: c["x0"])
        primera = g[0][1]
        cab = Fila(primera.pagina, [Linea(primera.pagina, [Palabra("", primera.bloques[0].x0, primera.top - 3, primera.bloques[0].x0,
                                                                    primera.top - 1)])])
        return cols, cab

    def _columnas_cabecera(self, banda):
        cols = []
        for f in banda:
            ant, c_ant = None, None
            for p in f.palabras:
                c = self._clase(p.texto)
                # «Precio ud.», «Precio/unidad», «Importe total»: la segunda palabra pertenece a la misma columna
                if c in ("cantidad", "unidad") and c_ant == "precio" and ant is not None and p.x0 - ant.x1 < 1.0 * (p.bottom - p.top):
                    c = "precio"
                ant, c_ant = p, c
                if not c:
                    continue
                for col in cols:
                    if min(col["x1"], p.x1) - max(col["x0"], p.x0) > -4 and (col["clase"] == c or {col["clase"], c} <= {"precio", "importe"}
                                                                            and abs((col["x0"] + col["x1"]) / 2 - (p.x0 + p.x1) / 2) < 25):
                        # palabras apiladas en la misma columna: «PRECIO / UNITARIO», «IMPORTE / TOTAL»
                        if col["clase"] != c and c == "precio":
                            col["clase"] = "precio"
                        col["x0"], col["x1"] = min(col["x0"], p.x0), max(col["x1"], p.x1)
                        break
                else:
                    cols.append({"clase": c, "x0": p.x0, "x1": p.x1, "texto": p.texto})
        cols.sort(key=lambda c: c["x0"])
        # «Fecha» de una columna lateral de datos (alejada de la descripción) no es columna de la tabla
        conc = next((c for c in cols if c["clase"] == "concepto"), None)
        if conc:
            cols = [c for c in cols if not (c["clase"] == "otro" and c["x1"] < conc["x0"] and conc["x0"] - c["x1"] > 110)]
        # «Cant.» y «Ud.» a la vez: «Ud.» es la columna de unidad de medida, no de cantidad
        cants = [c for c in cols if c["clase"] == "cantidad"]
        if len(cants) >= 2:
            for c in cants:
                if re.fullmatch(r"(ud|u|uds|unid|unidad)\.?", c.get("texto", "").strip(), re.I):
                    c["clase"] = "unidad"
                    break
        # Si hay dos columnas de importe, la de la izquierda suele ser el precio
        imp = [c for c in cols if c["clase"] == "importe"]
        if len(imp) >= 2 and not any(c["clase"] == "precio" for c in cols):
            imp[0]["clase"] = "precio"
        return cols

    def _cuerpo_tabla(self, cab, banda, cols, aviso_tabla=None):
        fondo = max(f.bottom for f in banda)
        numericas = [c for c in cols if c["clase"] in ("cantidad", "precio", "dto", "iva", "importe")]
        concepto_col = next((c for c in cols if c["clase"] == "concepto"), None)
        self._ids_filas_tabla = {id(f) for f in banda}
        # Anchura de la tabla: lo que queda fuera (una columna lateral de datos, p. ej.) no forma parte de las líneas
        izq_cols = [c for c in cols if concepto_col is None or c["x1"] < concepto_col["x0"] + 5]
        tab_x0 = (min(c["x0"] for c in izq_cols) - 20) if izq_cols else ((concepto_col["x0"] - 60) if concepto_col else 0)
        tab_x1 = max(c["x1"] for c in cols) + 40
        lineas_out, paginas_tabla = [], {cab.pagina}
        filas_cuerpo, filas_cuerpo_orig = [], []
        saltando = 0
        for f in self.filas:
            if (f.pagina, f.top) <= (cab.pagina, fondo):
                continue
            if f.pagina != cab.pagina and f.pagina not in paginas_tabla:
                # continuación en otra página: solo si la tabla no terminó y la página repite cabecera o empieza con líneas
                paginas_tabla.add(f.pagina)
            txt = f.texto
            f_orig = f
            f = Fila(f.pagina, [b for b in f.bloques if tab_x0 <= (b.x0 + b.x1) / 2 <= tab_x1 or (b.x0 >= tab_x0 and b.x1 <= tab_x1)])
            if not f.bloques:
                continue
            if saltando:
                if f.pagina > saltando and len({self._clase(p.texto) for p in f.palabras} - {None}) >= 2:
                    saltando = 0  # cabecera repetida en la página siguiente: sigue la tabla
                continue
            if RE_ARRASTRE.search(txt):
                continue
            ultima = f.pagina == max(self.alto)
            if RE_SIGUE.search(txt) or (not ultima and (RE_REGISTRO_MERCANTIL.search(txt) or f.top > 0.9 * self.alto[f.pagina])):
                saltando = f.pagina  # la tabla sigue en la página siguiente, tras la cabecera repetida
                continue
            if self._fila_de_totales(f) or (filas_cuerpo and self._solo_cifras(f)):
                break
            if f.pagina != cab.pagina and len({self._clase(p.texto) for p in f.palabras} - {None}) >= 2:
                continue  # cabecera repetida en una página nueva
            if RE_REGISTRO_MERCANTIL.search(txt) or (filas_cuerpo and f.top - filas_cuerpo[-1].bottom > 140 and f.pagina == filas_cuerpo[-1].pagina):
                break
            filas_cuerpo.append(f)
            filas_cuerpo_orig.append(f_orig)
        self._ids_filas_tabla |= {id(f) for f in filas_cuerpo_orig}
        for f in filas_cuerpo:
            pals = f.palabras
            nums, textos = [], []
            for i, p in enumerate(pals):
                if p.texto in ("€", "EUR", "eur") or re.fullmatch(r"[-_=·.*]{3,}", p.texto):
                    continue
                if _es_importe(p.texto) or re.fullmatch(r"\d{1,6}|\d{1,3}(?:\.\d{3})+", p.texto.strip()) or RE_PORCENTAJE.fullmatch(p.texto.strip()):
                    nums.append(p)
                else:
                    textos.append(p)
            asign = {}
            col_cod = next((c for c in cols if c["clase"] == "codigo"), None)
            if col_cod:
                textos += [p for p in nums if col_cod["x0"] - 6 <= (p.x0 + p.x1) / 2 <= col_cod["x1"] + 6]
                nums = [p for p in nums if p not in textos]
            no_num = [c for c in cols if c["clase"] in ("otro", "unidad", "codigo")]
            dentro = [p for p in nums if any(c["x0"] - 4 <= (p.x0 + p.x1) / 2 <= c["x1"] + 4 for c in no_num)]
            textos += [p for p in dentro if not any(c["clase"] == "otro" and c["x0"] - 4 <= (p.x0 + p.x1) / 2 <= c["x1"] + 4 for c in no_num)]
            nums = [p for p in nums if p not in dentro]
            for p in nums:
                cx = (p.x0 + p.x1) / 2
                mejor, dist = None, 10 ** 9
                for c in numericas:
                    d = 0 if c["x0"] - 6 <= cx <= c["x1"] + 6 else min(abs(cx - c["x0"]), abs(cx - c["x1"]), abs(p.x1 - c["x1"]))
                    if d < dist:
                        mejor, dist = c, d
                ancho_ref = 70
                if mejor and dist <= ancho_ref and mejor["clase"] not in asign:
                    # números de la descripción (p. ej. «132 viviendas») no se asignan si caen en la zona del concepto
                    if concepto_col and concepto_col["x0"] - 5 <= cx and (not numericas or cx < min(c["x0"] for c in numericas
                                                                                                     if c["x0"] > concepto_col["x1"] - 5) - 10):
                        textos.append(p)
                        continue
                    asign[mejor["clase"]] = p
                else:
                    textos.append(p)
            izq = [c["x1"] for c in cols if concepto_col and c is not concepto_col and c["x1"] < concepto_col["x0"] + 5]
            lim_izq = (max(izq) + 2) if izq else ((concepto_col["x0"] - 60) if concepto_col else 0)
            lim_der = min([c["x0"] for c in numericas if concepto_col and c["x0"] > concepto_col["x1"] - 5] or [10 ** 6]) - 2
            textos.sort(key=lambda p: p.x0)
            codigo = [p for p in textos if self._clase_col_de(p, cols) == "codigo"]
            concepto_pals = [p for p in textos if lim_izq <= (p.x0 + p.x1) / 2 <= lim_der and p.texto not in ("€",) and p not in codigo]
            concepto = " ".join(p.texto for p in concepto_pals).strip()
            conf = min((p.conf for p in pals), default=1.0)
            metodo = "ocr" if any(p.metodo == "ocr" for p in pals) else "texto_pdf"
            cf_conf = round(conf if metodo == "ocr" else 0.97, 2)

            def cf(clase, conv=_num):
                p = asign.get(clase)
                if not p:
                    return _vacio("")
                v = conv(p.texto)
                return {"valor": str(v) if v is not None else None, "texto": p.texto, "pagina": f.pagina,
                        "bbox": [round(p.x0, 1), round(p.top, 1), round(p.x1, 1), round(p.bottom, 1)],
                        "metodo": metodo, "confianza": cf_conf, "avisos": []}
            if "importe" not in asign and "precio" not in asign:
                if lineas_out and concepto and f.top - lineas_out[-1]["_bottom"] < 30:
                    lineas_out[-1]["concepto"]["valor"] = (lineas_out[-1]["concepto"]["valor"] + "\n" + concepto).strip()
                    lineas_out[-1]["concepto"]["bbox"][3] = round(f.bottom, 1)
                    lineas_out[-1]["_bottom"] = f.bottom
                continue
            unidad = None
            col_ud = next((c for c in cols if c["clase"] == "unidad"), None)
            if col_ud:
                en_ud = [p for p in textos if col_ud["x0"] - 8 <= (p.x0 + p.x1) / 2 <= col_ud["x1"] + 8 and len(p.texto) <= 6]
                if en_ud:
                    unidad = en_ud[0].texto.strip(".")
                    concepto_pals = [p for p in concepto_pals if p is not en_ud[0]]
                    concepto = " ".join(p.texto for p in concepto_pals).strip()
            elif "cantidad" in asign:
                sig = [p for p in textos if p.x0 > asign["cantidad"].x1 and p.x0 - asign["cantidad"].x1 < 30 and len(p.texto) <= 6
                       and not _es_importe(p.texto)]
                if sig and re.fullmatch(r"(ud|uds|u|h|hrs?|horas|mes|meses|m2|m²|ml|m3|kg|l|d[ií]as?|jornadas?|sesi[oó]n|servicio)\.?", sig[0].texto, re.I):
                    unidad = sig[0].texto.strip(".")
                    concepto = concepto.replace(sig[0].texto, "", 1).strip() if concepto.startswith(sig[0].texto) else concepto
            linea = {
                "concepto": {"valor": (" ".join(p.texto for p in codigo) + " " if codigo else "") + concepto, "texto": concepto,
                             "pagina": f.pagina, "bbox": [round(min((p.x0 for p in concepto_pals), default=pals[0].x0), 1), round(f.top, 1),
                                                          round(max((p.x1 for p in concepto_pals), default=pals[-1].x1), 1), round(f.bottom, 1)],
                             "metodo": metodo, "confianza": cf_conf, "avisos": []},
                "cantidad": cf("cantidad"), "unidad": _campo(unidad, texto=unidad, palabras=[], confianza=cf_conf) if unidad else _vacio(""),
                "precio": cf("precio"), "importe": cf("importe"),
                "dto": cf("dto", conv=lambda t: _num(t.replace("%", "")) if t else None),
                "iva": cf("iva", conv=lambda t: _num(t.replace("%", "")) if t else None),
                "_bottom": f.bottom,
            }
            if linea["importe"]["valor"] is None and linea["precio"]["valor"] is not None and linea["cantidad"]["valor"] is None:
                linea["importe"], linea["precio"] = linea["precio"], _vacio("")
            if linea["cantidad"]["valor"] is not None and metodo == "ocr" and not any(c["clase"] == "dto" for c in cols) \
                    and linea["precio"]["valor"] and linea["importe"]["valor"] and Decimal(linea["precio"]["valor"]) != 0 \
                    and q2(Decimal(linea["cantidad"]["valor"]) * Decimal(linea["precio"]["valor"])) != q2(Decimal(linea["importe"]["valor"])):
                q = Decimal(linea["importe"]["valor"]) / Decimal(linea["precio"]["valor"])
                if q > 0 and q == q.quantize(Decimal("0.01")):
                    leida = linea["cantidad"]["texto"]
                    linea["cantidad"] = _campo(str(q2(q)), metodo="calculado", confianza=0.6,
                                               aviso=f"La cantidad leída por OCR («{leida}») no cuadra con importe ÷ precio: se propone esta")
            if linea["cantidad"]["valor"] is None and any(c["clase"] == "cantidad" for c in cols) and metodo == "ocr" \
                    and linea["precio"]["valor"] and linea["importe"]["valor"] and Decimal(linea["precio"]["valor"]) != 0:
                q = Decimal(linea["importe"]["valor"]) / Decimal(linea["precio"]["valor"])
                if q > 0 and q == q.quantize(Decimal("0.01")) and q2(q * Decimal(linea["precio"]["valor"])) == q2(Decimal(linea["importe"]["valor"])):
                    linea["cantidad"] = _campo(str(q2(q)), metodo="calculado", confianza=0.6,
                                               aviso="Cantidad no legible: deducida de importe ÷ precio")
            lineas_out.append(linea)
        for l in lineas_out:
            l.pop("_bottom", None)
            l["concepto"]["valor"] = re.sub(r"[ \t]+", " ", l["concepto"]["valor"]).strip()
        self._tabla = (lineas_out, (aviso_tabla if lineas_out else "Tabla sin líneas reconocibles"))
        return self._tabla

    def _fila_de_totales(self, f: Fila) -> bool:
        """Fila que inicia el bloque de totales: «Base imponible …», varias etiquetas de importe, o etiqueta + cifra sin concepto
        (en la fila entera o en uno de sus bloques)."""
        if self._totales_en(f):
            return True
        bloques = sorted(f.bloques, key=lambda b: b.x0)
        return len(bloques) > 1 and any(self._totales_en(Fila(f.pagina, bloques[k:])) for k in range(1, len(bloques))
                                         if self._etiquetas_en(bloques[k].palabras))

    def _totales_en(self, f: Fila) -> bool:
        txt = f.texto.strip()
        if RE_FIN_TABLA.match(txt):
            return True
        ets = [e for e in self._etiquetas_en(f.palabras) if e["clase"] in ("base", "total", "pagar", "cuota", "retencion", "iva", "garantia")]
        if not ets:
            return False
        if len({e["clase"] for e in ets}) >= 2:
            return True
        en_etiqueta = {id(p) for e in ets for p in e["pals"]}
        resto = [p for p in f.palabras if id(p) not in en_etiqueta and re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", p.texto)
                 and not re.fullmatch(r"(eur|euros?)\.?", p.texto, re.I)]
        return len(resto) <= 1 and any(_es_importe(p.texto) for p in f.palabras)

    @staticmethod
    def _solo_cifras(f: Fila) -> bool:
        """Fila de solo cifras (valores de un cuadro de totales bajo sus etiquetas)."""
        if any(re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", p.texto) and not re.fullmatch(r"(eur|euros?)\.?", p.texto, re.I) for p in f.palabras):
            return False
        return sum(1 for p in f.palabras if _es_importe(p.texto)) >= 2

    def _clase_col_de(self, p: Palabra, cols) -> str | None:
        cx = (p.x0 + p.x1) / 2
        for c in cols:
            if c["x0"] - 4 <= cx <= c["x1"] + 4:
                return c["clase"]
        return None

    def _fila_con_concepto(self, f: Fila, numericas, concepto_col) -> bool:
        if not concepto_col:
            return False
        return any(concepto_col["x0"] - 10 <= (p.x0 + p.x1) / 2 <= concepto_col["x1"] + 150 and not _es_importe(p.texto)
                   and not RE_FIN_TABLA.match(p.texto) for p in f.palabras)

    def lineas_detalle(self):
        return self.tabla()

    # --- importes ---------------------------------------------------------------------------------------------------
    def _etiquetas_en(self, palabras: list[Palabra]) -> list[dict]:
        """Localiza etiquetas de importes en una secuencia de palabras y devuelve sus tramos (x0, x1). La traducción de un
        rótulo bilingüe («Base imponible / Taxable base») no cuenta como otra etiqueta."""
        out = self._etiquetas_en_bruto(palabras)
        res = []
        for e in out:
            ant = res[-1] if res else None
            if ant is not None and any(p.texto.strip() == "/" for p in palabras if ant["x1"] <= p.x0 <= e["x0"]) and \
                    not any(_es_importe(p.texto) for p in palabras if ant["x1"] <= p.x0 <= e["x0"]):
                ant["x1"] = max(ant["x1"], e["x1"])
                ant["pals"] = ant["pals"] + e["pals"]
                if ant["pct"] is None:
                    ant["pct"] = e["pct"]
                continue
            res.append(e)
        return res

    def _etiquetas_en_bruto(self, palabras: list[Palabra]) -> list[dict]:
        texto, pos = "", []
        for p in palabras:
            if texto:
                texto += " "
            pos.append((len(texto), p))
            texto += p.texto
        usados, out = set(), []
        for clase, pat in ETIQUETAS_IMPORTE:
            for m in re.finditer(pat, texto, re.I):
                rango = set(range(m.start(), m.end()))
                if rango & usados:
                    continue
                pw = [p for ini, p in pos if ini < m.end() and ini + len(p.texto) > m.start()]
                if not pw:
                    continue
                usados |= rango
                # porcentaje pegado a la etiqueta: «IVA 21%», «IRPF (15%)»
                cola = texto[m.end():m.end() + 12]
                cola = texto[m.end():m.end() + 16]
                pm = RE_PORCENTAJE.match(re.sub(r"^[\s(]*(?:al|del|a|de)?\s*", "", cola, flags=re.I))
                out.append({"clase": clase, "x0": min(p.x0 for p in pw), "x1": max(p.x1 for p in pw), "pals": pw,
                            "pct": parse_importe(pm.group(1)) if pm else None, "fin": m.end(), "texto": m.group(0)})
        return sorted(out, key=lambda e: e["x0"])

    RE_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}")
    RE_NEUTRA = re.compile(r"\(?(eur|euros|€)\)?[.:]*|al|del|de|sobre|s/", re.I)

    @staticmethod
    def _es_valor(t: str) -> bool:
        t = t.strip()
        return _es_importe(t) or bool(re.fullmatch(r"-?\d{1,3}(?:[.,]\d{1,2})?\s*%?", t)) or bool(RE_PORCENTAJE.fullmatch(t))

    def _segmentos_cabecera(self, f: Fila, ets: list[dict]) -> list[dict]:
        """Columnas de una fila de etiquetas: las de importe (con su clase) y los demás textos (columnas ajenas)."""
        en_et = {id(p): e for e in ets for p in e["pals"]}
        todas = f.palabras
        for e in ets:
            if e["pct"] is None:
                continue
            fin = e["x1"]
            for p in todas:
                if p.x0 >= fin - 1 and p.x0 - fin < 12 and (self._es_valor(p.texto) or p.texto.strip() == "%"):
                    fin = p.x1
                    en_et[id(p)] = e
            e["x1"] = fin
        segs, run = [], []
        pals = [p for p in todas if (not self._es_valor(p.texto) or p.texto.strip() == "%") and
                not (p.texto.strip() == "%" and id(p) in en_et and p not in en_et[id(p)]["pals"])]

        def cerrar():
            if run:
                segs.append({"clase": None, "x0": run[0].x0, "x1": run[-1].x1, "tipo": False})
                run.clear()
        for k, p in enumerate(pals):
            e = en_et.get(id(p))
            if e is not None:
                cerrar()
                if not segs or segs[-1].get("et") is not e:
                    segs.append({"clase": e["clase"], "x0": e["x0"], "x1": e["x1"], "et": e, "tipo": False})
                continue
            sig = pals[k + 1] if k + 1 < len(pals) else None
            # «% IVA», «Tipo IVA», «% R.E.»: columna del tipo impositivo de cada fila
            prev = pals[k - 1] if k > 0 else None
            pegado_a_cifra = p.texto.strip() == "%" and prev is not None and self._es_valor(prev.texto) and p.x0 - prev.x1 < 8
            if re.fullmatch(r"%|tipo|tipos|t\.", p.texto.strip(), re.I) and not pegado_a_cifra and sig is not None and id(sig) in en_et \
                    and sig.x0 - p.x1 < 15:
                cerrar()
                e2 = en_et[id(sig)]
                e2["x0"] = min(e2["x0"], p.x0)
                e2["tipo"] = True
                continue
            if run and p.x0 - run[-1].x1 > 3 * max(1.0, p.bottom - p.top):
                cerrar()
            run.append(p)
        cerrar()
        for sg in segs:
            if sg.get("et") is not None:
                sg["tipo"] = bool(sg["et"].get("tipo")) or "%" in sg["et"]["texto"]
                sg["x0"] = min(sg["x0"], sg["et"]["x0"])
        hay_cuota = any(sg["clase"] in ("cuota",) for sg in segs) or sum(1 for sg in segs if sg["clase"] == "iva") >= 2
        for sg in segs:
            # «IVA» como columna junto a «Cuota IVA»: es el tipo
            if sg["clase"] == "iva" and hay_cuota and not sg["tipo"]:
                sg["tipo"] = True
        return segs

    def importes(self):
        hechos = []  # (clase, valor Decimal, palabras, pagina, texto, pct, estrategia)
        filas = self.filas
        consumidas: set[int] = set()  # palabras ya usadas como valor de una columna o como tipo de una fila del cuadro
        self.tabla()
        de_tabla = self._ids_filas_tabla
        for i, f in enumerate(filas):
            if id(f) in de_tabla:
                continue  # cabecera y líneas de la tabla de conceptos («Base de hormigón…», columna «IVA %»)
            pals = f.palabras
            ets = [e for e in self._etiquetas_en(pals) if not any(id(p) in consumidas for p in e["pals"])]
            if not ets:
                continue
            pendientes = []
            for k, e in enumerate(ets):
                lim = ets[k + 1]["x0"] if k + 1 < len(ets) else 10 ** 6
                validos = []
                traduccion = False
                for p in sorted(pals, key=lambda p: p.x0):
                    if p.x0 < e["x1"] - 1 or p.x0 >= lim or p in e["pals"] or id(p) in consumidas:
                        continue
                    if p.texto.strip() == "/" and not validos:
                        traduccion = True  # «Total a pagar / Amount due»: rótulo bilingüe
                        continue
                    if _es_importe(p.texto):
                        validos.append(p)
                    elif self.RE_PALABRA.search(p.texto) and not self.RE_NEUTRA.fullmatch(p.texto.strip()) and not traduccion:
                        break  # otro texto entre la etiqueta y la cifra: la cifra no es suya
                if validos:
                    p = validos[-1] if k + 1 == len(ets) else validos[0]
                    v = _num(p.texto)
                    if v is not None:
                        hechos.append((e["clase"], v, [*e["pals"], p], f.pagina, f.texto, e["pct"], "linea"))
                        consumidas.add(id(p))
                        continue
                pendientes.append(e)
            if not pendientes:
                continue
            if any(e["clase"] in ("base", "cuota") for e in pendientes):
                en_et = {id(p) for e in ets for p in e["pals"]}
                for p in pals:
                    if id(p) in en_et or not re.fullmatch(r"tipo|tipos|%|tipo\s*%|%\s*iva", p.texto.strip(), re.I):
                        continue
                    sig = [e for e in ets if 0 <= e["x0"] - p.x1 < 15]
                    if sig:
                        continue  # «Tipo IVA», «% IVA»: ya lo trata el segmento de esa etiqueta
                    e_tipo = {"clase": "iva", "x0": p.x0, "x1": p.x1, "pals": [p], "pct": None, "texto": p.texto, "fin": 0, "tipo": True}
                    ets = sorted(ets + [e_tipo], key=lambda e: e["x0"])
                    pendientes.append(e_tipo)
            # Etiquetas sin cifra en su fila: cuadro con los valores en las filas siguientes, alineados por columnas
            segs = self._segmentos_cabecera(f, ets)
            seg_pend = [sg for sg in segs if sg.get("et") is not None and sg["et"] in pendientes]
            if not seg_pend:
                continue
            x_min = min(sg["x0"] for sg in seg_pend) - 40
            x_max = max(sg["x1"] for sg in seg_pend) + 60
            # sin invadir la columna de una etiqueta ajena (otro cuadro en la misma fila)
            ajenas = [sg["x0"] for sg in segs if sg.get("et") is not None and sg not in seg_pend and sg["x0"] > max(x["x1"] for x in seg_pend)]
            if ajenas:
                x_max = min(x_max, min(ajenas) - 2)
            ant = f
            for f2 in filas[i + 1:i + 10]:
                if f2.pagina != f.pagina or f2.top - ant.bottom > 45:
                    break
                zona = [p for p in f2.palabras if x_min <= (p.x0 + p.x1) / 2 <= x_max and id(p) not in consumidas]
                ets2 = self._etiquetas_en(zona)
                vals = [p for p in zona if self._es_valor(p.texto) and p.texto.strip() != "%"]
                if not vals:
                    break
                asign = []
                for p in vals:
                    cx = (p.x0 + p.x1) / 2
                    sg = min(segs, key=lambda sg: 0 if sg["x0"] - 4 <= cx <= sg["x1"] + 4 else
                             min(abs(cx - (sg["x0"] + sg["x1"]) / 2), abs(p.x1 - sg["x1"]), abs(p.x0 - sg["x0"])))
                    asign.append((p, sg))
                # una etiqueta de la fila fuera de la columna del tipo indica una nueva fila de etiquetas
                tipo_x = [(sg["x0"], sg["x1"]) for sg in segs if sg["tipo"]]
                nuevas = [e for e in ets2 if not any(a - 30 <= (e["x0"] + e["x1"]) / 2 <= b + 30 for a, b in tipo_x)]
                if nuevas and not any(sg.get("et") is not None for _, sg in asign):
                    break
                if nuevas:
                    break
                pct = None
                for p, sg in asign:
                    if sg["tipo"] and sg["clase"] in ("iva", "cuota"):
                        pct = _num(p.texto.replace("%", ""))
                usados = False
                for p, sg in asign:
                    e = sg.get("et")
                    if e is None or e not in pendientes:
                        continue
                    consumidas.add(id(p))
                    usados = True
                    if sg["tipo"]:
                        if sg["clase"] in ("iva", "cuota"):
                            hechos.append(("pct", _num(p.texto.replace("%", "")), [*e["pals"], p], f.pagina, f.texto + " / " + f2.texto, None, "columna"))
                        continue
                    v = _num(p.texto)
                    if v is None:
                        continue
                    clase = "cuota" if e["clase"] == "iva" else e["clase"]
                    hechos.append((clase, v, [*e["pals"], p], f.pagina, f.texto + " / " + f2.texto,
                                   pct if clase in ("base", "cuota") and pct is not None else e["pct"], "columna"))
                for e in ets2:  # «IVA 10 %» en la columna del tipo de cada fila
                    for p in e["pals"]:
                        consumidas.add(id(p))
                if not usados:
                    break
                ant = f2
        return self._consolidar(hechos)

    def _consolidar(self, hechos):
        res = {"desglose": [], "base_imponible": None, "cuota_iva": None, "tipo_iva": None, "total_factura": None,
               "total_a_pagar": None, "retencion": None, "recargo_equivalencia": None}

        def c(h, valor=None, aviso=None, conf=None):
            return _campo(str(q2(valor if valor is not None else h[1])), texto=h[4], palabras=h[2], pagina=h[3], aviso=aviso, confianza=conf)
        por = {}
        for h in hechos:
            por.setdefault(h[0], []).append(h)
        bases = por.get("base", [])
        ivas = por.get("iva", []) + por.get("cuota", [])
        pcts = por.get("pct", [])
        # Desglose por tipo: pares (base, cuota, %) coherentes
        desglose = []
        usados = set()
        tipos_iva = [h for h in ivas if h[5] is not None]
        for hi in tipos_iva:
            t = hi[5]
            mejor_base = None
            for hb in bases:
                if id(hb) in usados:
                    continue
                if q2(hb[1] * t / 100) == q2(hi[1]):
                    mejor_base = hb
                    break
            if mejor_base:
                usados.add(id(mejor_base))
            desglose.append({"tipo_iva": _campo(f"{t:.2f}", texto=hi[4], palabras=hi[2], pagina=hi[3]), "cuota": c(hi),
                             "base": c(mejor_base) if mejor_base else _vacio("Base por tipo no identificada"), "_ok": bool(mejor_base)})
        if not desglose and ivas:
            # cuota sin porcentaje en la etiqueta (p. ej. columna «Total IVA» con «21%» en otra columna)
            hc = next((h for h in ivas if h[0] == "cuota"), ivas[-1])
            t = pcts[0][1] if pcts else None
            hb = bases[0] if bases else None
            if t is None and hb and hb[1]:
                tasa = q2(hc[1] * 100 / hb[1])
                t = tasa if tasa in (Decimal("21.00"), Decimal("10.00"), Decimal("4.00"), Decimal("5.00"), Decimal("2.00"),
                                     Decimal("7.50"), Decimal("0.00")) else None
            if t is not None:
                ok = hb is not None and q2(hb[1] * t / 100) == q2(hc[1])
                ref_t = pcts[0] if pcts else hc
                desglose.append({"tipo_iva": _campo(f"{t:.2f}", texto=ref_t[4], palabras=ref_t[2], pagina=ref_t[3]), "cuota": c(hc),
                                 "base": c(hb) if hb else _vacio("Base no identificada"), "_ok": ok})
        # Base imponible total
        if bases:
            no_usadas = [b for b in bases if id(b) not in usados]
            if len(bases) == 1:
                res["base_imponible"] = c(bases[0])
            else:
                # una base «total» que coincide con la suma de las del desglose, o la última impresa
                suma = sum((Decimal(d["base"]["valor"]) for d in desglose if d["base"]["valor"]), Decimal(0))
                igual = [b for b in bases if desglose and q2(b[1]) == q2(suma)]
                elegida = igual[-1] if igual else (no_usadas[-1] if no_usadas else bases[-1])
                res["base_imponible"] = c(elegida)
        if desglose and len(desglose) == 1 and desglose[0]["base"]["valor"] is None and res["base_imponible"]:
            desglose[0]["base"] = dict(res["base_imponible"])
            desglose[0]["_ok"] = q2(Decimal(desglose[0]["base"]["valor"]) * Decimal(desglose[0]["tipo_iva"]["valor"]) / 100) == \
                q2(Decimal(desglose[0]["cuota"]["valor"]))
        if not res["base_imponible"] and desglose and all(d["base"]["valor"] for d in desglose):
            res["base_imponible"] = _campo(str(q2(sum(Decimal(d["base"]["valor"]) for d in desglose))), metodo="calculado",
                                           confianza=0.85, aviso="Suma de las bases del desglose")
        for d in desglose:
            if not d.pop("_ok", True):
                d["cuota"]["avisos"].append("La cuota no cuadra con base × tipo")
        res["desglose"] = desglose
        if desglose:
            res["tipo_iva"] = desglose[0]["tipo_iva"]
            cuotas = [Decimal(d["cuota"]["valor"]) for d in desglose]
            res["cuota_iva"] = desglose[0]["cuota"] if len(cuotas) == 1 else _campo(
                str(q2(sum(cuotas))), metodo="calculado", confianza=0.9, aviso="Suma de cuotas por tipo")
        totales = por.get("total", [])
        if totales:
            # el total que cuadra con base + cuota; si no, el mayor impreso
            esperado = None
            if res["base_imponible"] and res["cuota_iva"]:
                esperado = q2(Decimal(res["base_imponible"]["valor"]) + Decimal(res["cuota_iva"]["valor"]) +
                              sum((h[1] for h in por.get("recargo", [])[-1:]), Decimal(0)))
            cuadra = [h for h in totales if esperado is not None and q2(h[1]) == esperado]
            res["total_factura"] = c(cuadra[-1] if cuadra else max(totales, key=lambda h: h[1]))
        pagar = por.get("pagar", [])
        if pagar:
            res["total_a_pagar"] = c(pagar[-1])
            if not res["total_factura"] and res["base_imponible"] and res["cuota_iva"] and not por.get("retencion") \
                    and q2(pagar[-1][1]) == q2(Decimal(res["base_imponible"]["valor"]) + Decimal(res["cuota_iva"]["valor"]) +
                                               sum((h[1] for h in por.get("recargo", [])[-1:]), Decimal(0))):
                res["total_factura"] = c(pagar[-1], aviso="Sin «total factura» impreso: se toma el importe a pagar, que cuadra con base + cuota")
        ret = por.get("retencion", [])
        if ret:
            h = ret[-1]
            res["retencion"] = {"porcentaje": _campo(str(h[5]) if h[5] is not None else None, texto=h[4], palabras=h[2], pagina=h[3]),
                                "importe": c(h, valor=abs(h[1]))}
        rec = por.get("recargo", [])
        if rec:
            res["recargo_equivalencia"] = c(rec[-1])
        # Comprobador aritmético: si no hay coherencia por etiquetas, busca la combinación coherente impresa
        if not self._coherente(res):
            alt = self._resolver_aritmetica()
            if alt:
                res.update(alt)
        for k in ("base_imponible", "cuota_iva", "total_factura"):
            if res[k] is None:
                res[k] = _vacio()
        if res["total_a_pagar"] is None:
            res["total_a_pagar"] = _vacio("No consta importe a pagar distinto del total")
        if res["tipo_iva"] is None:
            res["tipo_iva"] = _vacio("Tipo de IVA no identificado")
        return res

    @staticmethod
    def _coherente(res) -> bool:
        try:
            b = Decimal(res["base_imponible"]["valor"])
            cu = Decimal(res["cuota_iva"]["valor"])
            t = Decimal(res["total_factura"]["valor"])
        except (TypeError, KeyError, ValueError, ArithmeticError):
            return False
        return q2(b + cu) == q2(t) and bool(res["desglose"])

    def _resolver_aritmetica(self):
        """Último recurso: busca en la mitad inferior una terna impresa (base, cuota, total) con un tipo vigente."""
        nums = []
        for l in self.lineas:
            if l.top < 0.25 * self.alto[l.pagina]:
                continue
            for p in l.palabras:
                if _es_importe(p.texto):
                    v = _num(p.texto)
                    if v is not None and v > 0:
                        nums.append((v, p, l))
        valores = {}
        for v, p, l in nums:
            valores.setdefault(q2(v), (p, l))
        mejor = None
        for t in (Decimal("21"), Decimal("10"), Decimal("4"), Decimal("5"), Decimal("2")):
            for b in valores:
                cu = q2(b * t / 100)
                if cu in valores and q2(b + cu) in valores and cu > 0:
                    cand = (b + cu, b, cu, t)
                    if not mejor or cand[0] > mejor[0]:
                        mejor = cand
        if not mejor:
            return None
        tot, b, cu, t = mejor

        def cc(v):
            p, l = valores[q2(v)]
            return _campo(str(q2(v)), texto=l.texto, palabras=[p], pagina=l.pagina, confianza=0.7,
                          aviso="Deducido por coherencia aritmética (base × tipo = cuota; base + cuota = total): confirme")
        return {"base_imponible": cc(b), "cuota_iva": cc(cu), "total_factura": cc(tot),
                "tipo_iva": _campo(f"{t:.2f}", metodo="calculado", confianza=0.7, aviso="Tipo deducido por coherencia aritmética"),
                "desglose": [{"tipo_iva": _campo(f"{t:.2f}", metodo="calculado", confianza=0.7, aviso="Tipo deducido"),
                              "base": cc(b), "cuota": cc(cu)}]}

    # --- referencias y pago ----------------------------------------------------------------------------------------------
    def referencias(self):
        defs = {
            "obra": (r"^obra\b|\bobra\s*:|\bproyecto\s+de\s+obra", self._v_texto),
            "certificacion": (r"certificaci[oó]n\s*(?:n[º°o*]\.?|n[úu]m\.?|n[úu]mero)?", self._v_entero),
            "pedido": (r"(?:n[º°úu]\.?\s*(?:de\s+)?|n[úu]mero\s+de\s+)?pedido(?:\s+n[º°]\.?)?|orden\s+de\s+compra|purchase\s+order|\bp\.?o\.?\s*n", self._v_codigo),
            "partida_presupuestaria": (r"partida(?:\s+presupuestaria)?", self._v_codigo),
            "periodo": (r"per[ií]odo(?:\s+(?:facturado|de\s+facturaci[oó]n))?", self._v_texto),
            "contrato": (r"(?:n[º°]\.?\s*(?:de\s+)?)?contrato(?:\s+n[º°]\.?)?", self._v_codigo),
            "proyecto": (r"^proyecto\b(?!\s+de\s+obra)", self._v_texto),
            "expediente": (r"(?:n[º°]\.?\s*(?:de\s+)?)?expediente(?:\s+n[º°]\.?)?", self._v_codigo),
            "albaran": (r"albar[aá]n(?:\s+n[º°]\.?)?", self._v_codigo),
            "responsable": (r"^responsable\b", self._v_texto),
        }
        out = {}
        for k, (et, val) in defs.items():
            cands = self.candidatos(et, val, abajo=k in ("certificacion", "pedido", "contrato", "partida_presupuestaria", "expediente",
                                                                  "proyecto", "periodo", "albaran"))
            if cands:
                out[k] = self._campo_de(cands[0])
        # Descripción de la obra/servicio bajo un encabezado («DESCRIPCIÓN DE LOS SERVICIOS…:»), si no hay «Obra:»
        if "obra" not in out:
            for i, l in enumerate(self.lineas):
                if re.match(r"^descripci[oó]n\s+de\s+(?:los\s+)?(?:servicios|trabajos|la\s+obra)", l.texto, re.I):
                    sig = []
                    for x in self.lineas[i + 1:i + 4]:
                        if x.pagina != l.pagina or x.top - (sig[-1].bottom if sig else l.bottom) > 22 or ":" in x.texto[:40] \
                                or re.search(r"\.\s-\s|pedido|partida|responsable", x.texto, re.I):
                            break
                        sig.append(x)
                    if sig:
                        out["obra"] = _campo(" ".join(x.texto for x in sig), texto=l.texto, lineas_=sig)
                    break
        return out

    def pago(self):
        out = {}
        c = self.candidatos(r"forma\s+de\s+pago|condiciones\s+de\s+pago|modo\s+de\s+pago|medio\s+de\s+pago", self._v_texto)
        if c:
            out["forma_pago"] = self._campo_de(c[0])
        else:
            for l in self.lineas:
                m = re.search(r"\b(?:pago|abono|a\s+pagar)\s+(?:por|mediante|con|a\s+trav[eé]s\s+de|v[ií]a)\s+(.{4,80})", l.texto, re.I) or \
                    re.match(r"^\s*((?:transferencia|domiciliaci[oó]n|recibo|tarjeta|contado|efectivo|cheque|pagar[eé]|confirming)\b.{0,80})$",
                             l.texto, re.I)
                if m:
                    out["forma_pago"] = _campo(m.group(1).strip(" .;"), texto=l.texto, linea=l, confianza=0.85,
                                               aviso="Forma de pago deducida de una frase sin etiqueta")
                    break
        for k, l in enumerate(self.lineas):
            compacto = re.sub(r"[\s.\-]", "", l.texto.upper())
            m = re.search(r"ES[0-9O]{2}[0-9O]{20}", compacto)
            if not m and re.search(r"ES[0-9O]{2}[0-9O]{4,19}$", compacto):
                # IBAN partido en dos renglones
                sig = [x for x in self.lineas[k + 1:k + 6] if x.pagina == l.pagina and 0 < x.top - l.top <= 18
                       and min(x.x1, l.x1 + 40) - max(x.x0, l.x0 - 40) > 0]
                if sig:
                    compacto += re.sub(r"[\s.\-]", "", sig[0].texto.upper())
                    m = re.search(r"ES[0-9O]{2}[0-9O]{20}", compacto)
            if m:
                bruto = m.group(0)
                limpio = bruto[:2] + bruto[2:].replace("O", "0")
                cc = _campo(limpio, texto=l.texto, linea=l)
                if limpio != bruto:
                    cc["avisos"].append("Se corrigió la letra O por el dígito 0 (OCR)")
                    cc["confianza"] = min(cc["confianza"], 0.6)
                if not _iban_valido(limpio):
                    cc["avisos"].append("El dígito de control del IBAN no es válido")
                    cc["confianza"] = min(cc["confianza"], 0.6)
                out["iban"] = cc
                break
        c = self.candidatos(r"vencimiento|fecha\s+de\s+pago|fecha\s+l[ií]mite", self._v_fecha)
        if c:
            out["vencimiento"] = self._campo_de(c[0])
        return out

    def indicios(self):
        """Particularidades fiscales que exigen tratamiento específico (se bloquean si no están implementadas)."""
        pats = {
            "exencion": r"\bexent[ao]\b|exenci[oó]n|art(?:[ií]culo|\.)\s*20\b",
            "inversion_sujeto_pasivo": r"inversi[oó]n\s+del\s+sujeto\s+pasivo|art(?:[ií]culo|\.)\s*84",
            "no_sujeta": r"\bno\s+sujet[ao]\b",
            "recargo_equivalencia": r"recargo\s+(?:de\s+)?equivalencia",
            "suplidos": r"\bsuplidos?\b",
            "descuento": r"\bdescuento\b|\bdto\.?\b",
            "rectificativa": r"rectificativ|(?<!cuenta de )(?<!forma de )(?<!medio de )\babono\b(?!\s+(?:en\s+(?:la\s+)?cuenta|mediante|por\s+transferencia|a\s+(?:la\s+)?cuenta))",
            "intracomunitaria": r"intracomunitari|\bVIES\b|reverse\s+charge",
            "criterio_caja": r"criterio\s+de\s+caja",
            "regimen_especial": r"r[eé]gimen\s+especial|agencias\s+de\s+viaje|bienes\s+usados|\bREAGYP\b",
            "retencion_garantia": r"retenci[oó]n\s+(?:(?:por|de|en)\s+(?:concepto\s+de\s+)?)?garant[ií]a|fondo\s+de\s+garant[ií]a|garant[ií]a\s+del?\s+\d+\s*%",
            "verifactu_existente": r"VERI\*?FACTU|QR\s+tributario|Factura\s+verificable",
        }
        out = {}
        for k, p in pats.items():
            for l in self.lineas:
                if re.search(p, l.texto, re.I):
                    out[k] = {"texto": l.texto, "pagina": l.pagina, "bbox": l.bbox()}
                    break
        return out

    def extraer(self) -> dict:
        lineas_det, aviso_lineas = self.lineas_detalle()
        imp = self.importes()
        partes = self.partes()
        return {
            "version_extractor": VERSION_EXTRACTOR,
            "tipo_documento": self.tipo_documento(),
            "numero": self.numero(),
            "fecha": self.fecha(),
            "fecha_operacion": self.fecha_operacion() or _vacio("No consta fecha de operación distinta"),
            "emisor": partes["emisor"],
            "destinatario": partes["destinatario"],
            "nifs_detectados": partes["nifs_detectados"],
            "nifs_no_validos": partes["nifs_no_validos"],
            "lineas": lineas_det,
            "aviso_lineas": aviso_lineas,
            **imp,
            "referencias": self.referencias(),
            **self.pago(),
            "indicios": self.indicios(),
            "paginas": [{"numero": p.numero, "metodo": p.metodo, "conf_media": p.conf_media,
                         "ancho": p.ancho, "alto": p.alto} for p in self.paginas],
        }
