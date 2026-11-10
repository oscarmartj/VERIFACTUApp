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

import bisect
import itertools
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from .. import nif as nifmod
from ..importes import parse_importe, q2
from .texto import CONF_TEXTO_DUDOSO, Linea, Pagina, Palabra, lineas as agrupar_lineas

VERSION_EXTRACTOR = "reglas-2.2"

RE_IMPORTE = re.compile(r"(?<![\d/.,])[-−]?(?:\d{1,3}(?:\.\d{3})+,\d{1,4}|\d{1,3}(?: \d{3})+,\d{1,4}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+,\d{1,4}|\d+\.\d{2})(?![\d/])")
RE_PORCENTAJE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
RE_FECHA = re.compile(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4}|\d{2})\b")
MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                     "septiembre", "octubre", "noviembre", "diciembre"], start=1)}
MESES["setiembre"] = 9
# El año de una fecha larga, también con el punto de millar que pone Word al teclearlo («31 de octubre de 2.026»)
RE_FECHA_LARGA = re.compile(r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")\s+(?:de|del)\s+(\d{4}|\d\.\d{3})\b", re.I)
RE_FORMA_JURIDICA = re.compile(
    r"(?:^|[\s,])(S\.?\s?L\.?\s?U\.?|S\.?\s?L\.?\s?L\.?|S\.?\s?L\.?|S\.?\s?A\.?\s?U\.?|S\.?\s?A\.?|S\.?\s?COOP\.?(?:\s?AND\.?)?|"
    r"SOCIEDAD LIMITADA|SOCIEDAD AN[OÓ]NIMA|C\.?\s?B\.?|S\.?\s?C\.?|SLP|S\.?L\.?P\.?|A\.?I\.?E\.?)\s*+[.,]?\s*+$", re.I)
RE_ENTIDAD = re.compile(r"\b(comunidad\s+de\s+(?:propietarios|bienes|regantes|usuarios)|c\.\s?p\.|asociaci[oó]n|fundaci[oó]n|"
                        r"ayuntamiento|concello|ajuntament|diputaci[oó]n|consorcio|mancomunidad|cooperativa|cofrad[ií]a|federaci[oó]n|"
                        r"universidad|colegio\s+oficial|club|parroquia|congregaci[oó]n|sociedad\s+civil)\b", re.I)
RE_NEGOCIO = re.compile(r"\b(carpinter[ií]a|alimentaci[oó]n|taller(?:es)?|bar|restaurante|caf[eé]|cafeter[ií]a|peluquer[ií]a|farmacia|"
                        r"ferreter[ií]a|panader[ií]a|pasteler[ií]a|librer[ií]a|papeler[ií]a|tienda|comercial|construcciones|reformas|"
                        r"instalaciones|servicios|asesor[ií]a|gestor[ií]a|consultor[ií]a|estudio|cl[ií]nica|academia|hostal|hotel|"
                        r"transportes?|autocares|fruter[ií]a|carnicer[ií]a|pescader[ií]a|supermercado|distribuciones|suministros|"
                        r"electricidad|fontaner[ií]a|pinturas|dise[ñn]o|inmobiliaria|agencia|despacho|bufete|taxi|mudanzas|"
                        r"limpiezas?|jardiner[ií]a|cerrajer[ií]a|imprenta|grupo|centro|bodega|granja|viveros?)\b", re.I)
# Tercero citado en una frase, no un bloque de parte: «Crédito cedido a BANCO X S.A.», «Obra promovida por GESTORA Y S.L.»,
# «Pago al cesionario BANCO X S.A.» (introducción en minúsculas con a / al / por / para y la razón social en mayúsculas)
RE_MENCION_TERCERO = re.compile(r"^(?P<intro>.*?[a-záéíóúñ].*?\b(?:a|al|por|para)\b.*?)\s+"
                                r"(?P<nombre>[A-ZÁÉÍÓÚÑ0-9]{3,}[A-ZÁÉÍÓÚÑÜ0-9.,&'ºª\s/-]*)$")
_PALABRAS_FUNCION = {"a", "al", "de", "del", "la", "las", "el", "los", "en", "y", "e", "o", "u", "por", "para", "con", "sin"}

# Rótulo de un papel delante de la razón social o solo en el renglón de encima («Promotor:», «Cesionario:», «Crédito
# cedido a:», «Dirección facultativa:»): nombra a otra parte. Los neutros nombran los datos de la propia parte o su
# dirección; los de cliente son los de RE_CLIENTE
RE_ROTULO = re.compile(r"^\s*(?P<rot>[^\W\d_]+(?:[ .'/-]+[^\W\d_]+){0,4})\.?\s*:\s*(?P<resto>.*)$")
RE_ROTULO_NEUTRO = re.compile(
    r"^(?:(?:nombre|denominaci[oó]n)(?:\s+(?:o|y|/)\s+raz[oó]n\s+social|\s+(?:fiscal|comercial|social|completo)|\s+y\s+apellidos)?|"
    r"raz[oó]n\s+social|empresa|sociedad|entidad|titular|datos(?:\s+fiscales)?|facturaci[oó]n|"
    r"(?:direcci[oó]n|domicilio)(?:\s+(?:de\s+)?(?:facturaci[oó]n|fiscal|social|postal|env[ií]o|entrega))?|"
    r"env[ií]o|entrega|(?:lugar|punto)\s+de\s+entrega|(?:enviar|entregar|remitir)\s+a|comprador|adquirente|receptor)$", re.I)
# Palabras que pueden acompañar al rótulo del NIF sin nombrar a otra parte («NIF del cliente», «VAT number»)
_CALIFICADOR_NEUTRO = {"cliente", "destinatario", "comprador", "adquirente", "receptor", "empresa", "sociedad", "titular",
                       "del", "de", "la", "el", "intracomunitario", "intracomunitaria", "comunitario", "iva", "fiscal", "number",
                       "numero", "número", "num", "núm", "nr", "nro", "no", "n", "nº", "n°", "o", "y", "es", "id", "nie", "dni",
                       "nif", "cif", "reg", "registration", "identificación", "identification", "tributario", "ue", "eu", "vies"}
RE_REGISTRO_MERCANTIL = re.compile(r"registro\s+mercantil|inscrita|\btomo\s*[\d.]|\bfolio\s*:?\s*\d|\bhoja\s+[A-Z]{0,2}-?\d", re.I)
RE_CLIENTE = re.compile(r"^(cliente|destinatario|facturar\s+a|factura\s+a|facturad[oa]\s+a(?=\s*[:/]|\s*$)|datos\s+del\s+cliente|datos\s+de\s+facturaci[oó]n|"
                        r"sr\.?|sres\.?|señor(es)?|a\s+la\s+atenci[oó]n|bill\s+to|customer|client|para(?=\s*:)|comprador|adquirente|"
                        r"datos\s+del\s+(?:comprador|adquirente)|sold\s+to|invoice\s+to)\b", re.I)
RE_EMISOR = re.compile(r"^(emisor|proveedor|datos\s+del\s+emisor|from|vendedor|prestador|(?:emitid[oa]|expedid[oa])\s+por(?=\s*[:/]|\s*$))\b", re.I)
RE_ATENCION = re.compile(r"^(att?\.?|a/a|a\s+la\s+atenci[oó]n|atenci[oó]n|dpto\.?|departamento|attn)\b", re.I)  # persona de contacto
RE_CORTESIA = re.compile(r"^(?:(?:sr|sra|srta|sres|sras|d|dña|dª)\.\s+|dª\s+)+", re.I)
RE_ROTULO_DOM = re.compile(r"^(?:domicilio|direcci[oó]n)(?:\s+(?:fiscal|social|postal))?\s*[:.]\s*", re.I)
RE_VIA = re.compile(r"\b(c/|c\.|calle|callej[oó]n|avda\.?|avenida|av\.|plaza|pza\.?|paseo|p[ºo]\.|ps\.?|camino|ctra\.?|"
                          r"carretera|pol[ií]gono|pol\.|urb\.?|urbanizaci[oó]n|ronda|traves[ií]a|glorieta|bulevar|rambla|v[ií]a|barrio|b[ºo]\.?|"
                          r"lugar|paraje|parque|partida|cuesta|costanilla|kalea|carrer|r[uú]a|avinguda|pla[cç]a|passeig)\s", re.I)
RE_DOMICILIO = re.compile(RE_VIA.pattern + r"|\b\d{5}\b", re.I)
RE_CP_CIUDAD = re.compile(r"(?<![\w-])\d{5}\s+[A-Za-zÁÉÍÓÚÑáéíóúñ]")
RE_CONTACTO = re.compile(r"tel[eé]?f|tfno|tlf|m[oó]vil|e-?mail|correo|@|www\.|http|fax", re.I)
RE_NIF_ETIQUETA = re.compile(r"\b(" + nifmod.ROTULO_NIF + r")\b", re.I)  # (también como lo lee el OCR: «N.LF.»)


def _re_nif_candidato(s: str, barra: bool) -> re.Pattern:
    return re.compile(r"(?<![A-Z0-9])(?:ES[\s-]?)?([A-Z]" + s + r"\d{7,8}" + s + r"[0-9A-Z]|\d{8}" + s + r"[A-Z]|"
                      r"\d{1,2}\.\d{3}\.\d{3}" + s + r"[A-Z]|[XYZ]" + s + r"\d{1,2}\.?\d{3}\.?\d{3}" + s + r"[A-Z]|"
                      r"[A-HJ-NP-SUVW]" + s + r"\d{2}\.\d{3}\.\d{3}"
                      + (r"|[A-HJ-NP-SUVW]" + s + r"\d{2}/\d{3}\.?\d{2}" + s + r"[0-9A-J]" if barra else "") + r")(?![A-Z0-9])")


# Candidato a NIF: separadores de espacio, punto o guion; y, solo detrás del rótulo del NIF (nifmod.forma_admitida), la raya
# «–», «—» o el guion separados por espacios («CIF: A – 00543215») y la barra tras el código de provincia («B00/123455»)
RE_NIF_CANDIDATO = _re_nif_candidato(nifmod.SEP, barra=True)
RE_NIF_CANDIDATO_SIMPLE = _re_nif_candidato(nifmod.SEP_SIMPLE, barra=False)
SEP = r"[\s:.\-–—=]*"
# Abreviatura de «número»: «nº», «n.º» (la forma recomendada) y «n*» (lectura OCR de «nº»)
NUM_ABR = r"n(?:\.?[º°]|\*)\.?"
# Otras lecturas de «Nº» tras el rótulo del documento («FACTURA Nº. Z72/26»): las del OCR de un escaneo («N“.», «N”»,
# «N"», «N'», «N.» con la º perdida, «N. º») y «Nro.». «N.» no si sigue otra inicial con punto («N.I.F.»)
NUM_ABR_OCR = r"(?:n[“”\"″'’]\.?|n\.?\s[º°]\.?|n\.(?!\s?[^\W\d_]\.)|nro\.?)"
# «FACTURA Nº. X» al comienzo del renglón con la º leída por el OCR como otro signo cualquiera («NC.», «Ne», «N9»): la N y
# un solo carácter más, seguidos del número (letras y cifras con «/» o «-»). Rótulo supuesto: el número se confirma
RE_TITULO_NUM_DUDOSO = r"^\s*factura\s+n\S\.?(?=\s+[A-Z0-9][\w/.-]*\d[\w/.-]*\s*$)"
# La abreviatura numera otra cosa si la sigue su nombre («FACTURA Nº CLIENTE», «FACTURA N. DE PEDIDO»: dos rótulos de una
# rejilla leídos en un renglón): no rotula el número de la factura, ni en el mismo renglón ni debajo
NO_NUMERA_OTRA = (r"(?![\s.:]*(?:de(?:l)?\s+(?:la\s+|el\s+)?)?(?:cliente|socio|abonado|proveedor|pedido|albar[aá]n|contrato|"
                  r"cuenta|expediente|proyecto|p[oó]liza|presupuesto|obra|lote)\b)")


def _clave(t: str) -> str:
    """Texto comparable: sin tildes, mayúsculas, espacios ni puntuación."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().casefold())


def _mismo_nombre(a: str | None, b: str | None) -> bool:
    """Misma razón social (o una contiene a la otra: «Cliente: X S.A.» frente a «X S.A.»)."""
    a, b = _clave(a), _clave(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) > 6 and (a in b or b in a)))


def _nifs_en(txt: str):
    """(nif_normalizado, inicio, fin) de cada candidato a NIF en el texto (válido o no). Si el candidato no es válido y
    termina en separador + un carácter, ese carácter es lo siguiente del texto y no el control: «B12345674 C/ Mayor»,
    «B12345674 y …», «B12345674 1ª planta» son el NIF «B12345674» seguido de otra cosa."""
    t = txt.upper()
    # («F26/000123», «A – 26000125», «Pedido 20261105 – Z» sin rótulo de NIF delante: un número de documento o una referencia)
    for m in nifmod.candidatos(RE_NIF_CANDIDATO, RE_NIF_CANDIDATO_SIMPLE, t):
        n, ini, fin = nifmod.normalizar(m.group(1)), m.start(1), m.end(1)
        if not nifmod.es_valido(n) and (c := re.search(r"(?:\s?[\-–—]\s?|[\s.])[0-9A-Z]$", m.group(1))):
            corto = nifmod.normalizar(m.group(1)[:c.start()])
            # también si solo el corto tiene forma de NIF («A87754215 C/ Sol»: NIF con control erróneo y la calle)
            if nifmod.es_valido(corto) or (not nifmod.tipo(n) and nifmod.tipo(corto) and re.search(r"\s[0-9A-Z]$", m.group(1))):
                n, fin = corto, ini + c.start()
        yield n, ini, fin


# Confusiones habituales del OCR entre letras y cifras: la letra leída donde el NIF lleva una cifra, y al revés
_OCR_A_CIFRA = {"B": "8", "O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "i": "1", "l": "1", "|": "1", "S": "5", "s": "5",
                "Z": "2", "z": "2", "G": "6", "T": "7", "A": "4"}
_OCR_A_LETRA = {"8": "B", "0": "ODQ", "1": "I", "|": "I", "5": "S", "2": "Z", "6": "G", "7": "T", "4": "A"}
_CIFRAS = "0123456789"
_LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Formas de NIF, carácter a carácter: DNI, NIE, NIF especial (K, L, M) y persona jurídica
_FORMAS_NIF = ((_CIFRAS,) * 8 + (_LETRAS,), ("XYZ",) + (_CIFRAS,) * 7 + (_LETRAS,), ("KLM",) + (_CIFRAS,) * 7 + (_LETRAS,),
               ("ABCDEFGHJNPQRSUVW",) + (_CIFRAS,) * 7 + (_CIFRAS + "ABCDEFGHIJ",))
# Lectura tras el rótulo del NIF: nueve caracteres (letras, cifras o «|»), con separadores sueltos pero sin espacios
RE_LECTURA_TRAS_ROTULO = re.compile(r"[\s:.\-–—]{0,6}(?:ES[\s-]?)?([0-9A-Za-z|](?:[.\-–—/]?[0-9A-Za-z|]){8})(?![0-9A-Za-z|])")
MAX_CAMBIOS_OCR_NIF = 2


def _nif_corregido_ocr(lectura: str) -> str | None:
    """NIF de una lectura OCR que no es válida, cambiando las letras que ocupan el lugar de una cifra por la cifra que el OCR
    confunde con ellas (B→8, O→0, S→5…) y las cifras que ocupan el lugar de una letra por la letra (8→B, 0→O…), como mucho
    MAX_CAMBIOS_OCR_NIF caracteres. Solo si una única variante tiene el dígito de control válido; si hay varias (o ninguna),
    no se elige: el NIF queda ilegible."""
    crudo = re.sub(r"[.\-–—/]", "", lectura)
    if len(crudo) != 9 or nifmod.es_valido(crudo.upper()):
        return None
    validos = set()
    for forma in _FORMAS_NIF:
        opciones, cambios = [], 0
        for c, admitidos in zip(crudo, forma):
            if c.upper() in admitidos and c != "|":
                opciones.append((c.upper(),))
                continue
            alt = tuple(x for x in (_OCR_A_CIFRA.get(c, "") + _OCR_A_LETRA.get(c, "")) if x in admitidos)
            if not alt:
                break
            opciones.append(alt)
            cambios += 1
        else:
            if 0 < cambios <= MAX_CAMBIOS_OCR_NIF:
                validos.update(n for n in map("".join, itertools.product(*opciones)) if nifmod.es_valido(n))
    return validos.pop() if len(validos) == 1 else None


def _intro_de_frase(intro: str) -> bool:
    """La introducción es una frase si acaba en la preposición («Crédito cedido a», «Pagadero a») o lleva alguna palabra
    con contenido en minúscula («Pago al cesionario»); no si es el comienzo de una razón social con mayúsculas iniciales
    («Materiales para Construcción ALBA S.L.», «Servicios Integrales a Domicilio SAD S.L.»)."""
    pals = re.findall(r"[^\W\d_]+", intro)
    return bool(pals) and (pals[-1].lower() in ("a", "al", "por", "para")
                           or any(p.islower() and len(p) >= 3 and p not in _PALABRAS_FUNCION for p in pals))


def _rotulo_de_papel(texto: str, solo: bool = False) -> str | None:
    """Rótulo de otro papel al comienzo de `texto` («Promotor: GESTORA S.L.»); con `solo`, el renglón es solo el rótulo."""
    m = RE_ROTULO.match(texto or "")
    if not m or bool(m["resto"].strip()) == solo:
        return None
    rot = m["rot"].strip()
    if RE_ROTULO_NEUTRO.match(rot) or RE_CLIENTE.match(rot + ":") or RE_ATENCION.match(rot) or RE_NIF_ETIQUETA.search(rot):
        return None
    return rot


def _calificador_nif(texto: str, nif_: str) -> str | None:
    """Palabras entre el rótulo del NIF y el NIF que nombran a otra parte: «CIF cesionario: A…» → «cesionario»."""
    ini = next((i for n, i, _ in _nifs_en(texto) if n == nif_), None)
    m = RE_NIF_ETIQUETA.search(texto[:ini]) if ini is not None else None
    if not m:
        return None
    pals = [p for p in re.findall(r"[^\W\d_]+", RE_NIF_ETIQUETA.sub(" ", texto[m.end():ini])) if p.lower() not in _CALIFICADOR_NEUTRO]
    return " ".join(pals) or None

# Vocabulario de la tabla de conceptos
COLUMNAS = [
    ("cantidad", re.compile(r"^(uds?\.?|unidades|cant\.?|cantidad|n[º°]\s*uds|horas|h\.?|qty|cant|alumnos|participantes|personas|pax|"
                            r"jornadas|d[ií]as|sesiones|kg|kilos|bultos|cajas|unid\.?)$", re.I)),
    ("concepto", re.compile(r"^(concepto|conceptos|descripci[oó]n|detalle|servicio|servicios|art[ií]culo|producto|description)$", re.I)),
    ("precio", re.compile(r"^(precio|p\.?\s?u\.?|p\.\s?unit\.?|unitario|unit\.?|€/ud\.?|tarifa|precio/h|€/h|importe/h|price|"
                          r"precio/\w+|€/\w+|pvp|p\.v\.p\.?)$", re.I)),
    ("dto", re.compile(r"^(dto\.?|dcto\.?|desc\.?|descuento|bonif\.?|%\s?d(?:c)?to\.?|d(?:c)?to\s?%)$", re.I)),
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
              r"total\s+a\s+cobrar|(?:neto|l[ií]quido|importe)\s+a\s+cobrar|import\s+a\s+pagar|total\s+a\s+abonar"),
    # retención de garantía (obra): retiene parte del pago, no es IRPF ni cambia la base, la cuota o el total
    ("garantia", r"retenci[oó]n\s+(?:de\s+|por\s+|en\s+concepto\s+de\s+)?garant[ií]a|fondo\s+de\s+garant[ií]a|garant[ií]a\s+(?:\d|del?\b)"),
    ("retencion", r"retenci[oó]n?(?:\s+(?:de\s+)?i\.?\s?r\.?\s?p\.?\s?f\.?)?|i\.?\s?r\.?\s?p\.?\s?f\.?|withholding(?:\s+tax)?"),
    ("recargo", r"total\s+recargo(?:\s+(?:de\s+)?equivalencia)?|recargo\s+(?:de\s+)?equivalencia|cuota\s+r\.\s?e\.|\br\.\s?e\.(?=\s|$|:)"),
    ("total", r"total\s+factura|importe\s+total|import\s+total|invoice\s+total|total\s+amount|total\s+(?:eur|€)|total\s+documento|"
              r"(?<![a-záéíóúñ])total(?!\s+(?:iva|i\.v\.a|cuota|base|a\s+pagar|l[ií]quido|neto|recargo|r\.\s?e\.|bruto|l[ií]neas|descuentos?|unidades|bultos|kilos|horas))"),
    ("cuota", r"cuota\s+(?:de\s+)?i\.?\s?v\.?\s?a\.?|quota\s+(?:d['’]\s?)?i\.?\s?v\.?\s?a\.?|cota\s+(?:de\s+)?i\.?\s?v\.?\s?[ae]\.?|total\s+i\.?\s?v\.?\s?a\.?|importe\s+i\.?\s?v\.?\s?a\.?|"
              r"suma\s+(?:de\s+)?cuotas?(?:\s+(?:de\s+)?i\.?\s?v\.?\s?a\.?)?|total\s+cuotas?(?:\s+(?:de\s+)?i\.?\s?v\.?\s?a\.?)?|"
              r"\bcuotas?\b(?!\s+(?:fija|fijas|mensual|mensuales|anual|de\s+(?:alta|socio|servicio|mantenimiento|conexi[oó]n|abono)|r\.?\s?e))"),
    ("base", r"base\s+imponible|base\s+imp\.?|total\s+base(?:\s+imponible)?|b\.\s?imponible|importe\s+neto|subtotal|suma\s+neto|"
             r"suma\s+(?:de\s+)?bases?(?:\s+imponibles?)?|total\s+bases?(?:\s+imponibles?)?|taxable\s+(?:base|amount)|^base\b|\bbase\b"),
    ("iva", r"\bi\.?\s?v\.?\s?a\.?(?![a-z])|\biva\b|\bvat\b|\bive\b|\bbez\b"),
]

# Cotas de trabajo frente a documentos hostiles (una factura real queda muy por debajo): sufijos de una fila que se
# prueban como inicio del bloque de totales y apariciones de una misma etiqueta que se examinan en un renglón
MAX_SUFIJOS_TOTALES = 24
MAX_ETIQUETAS_RENGLON = 8
MAX_ETIQUETAS_FILA = 40   # etiquetas de importe en una fila (en el corpus no pasan de 6)
MAX_CRUCES_CUADRO = 20_000   # cifras × columnas que se emparejan en una fila de un cuadro de totales
MAX_ALTO_FILA = 3.0   # alto máximo de una fila, en múltiplos de su bloque más alto (en el corpus no pasa de 2)
MAX_BLOQUES_FILA = 64   # bloques de una fila (en el corpus no pasan de 8)
MAX_LARGO_NOMBRE = 200   # caracteres de una razón social en la que se busca una mención a un tercero
MAX_NIFS_RENGLON = 8   # NIF distintos examinados en un renglón
MAX_APARICIONES_POR_NIF = 24   # apariciones de un mismo NIF examinadas (una por página en un documento largo)
MAX_APARICIONES_NIF = 120   # apariciones de NIF examinadas en todo el documento
MAX_BLOQUES_SIN_NIF = 40   # razones sociales de la cabecera examinadas como bloque sin NIF
MAX_RENGLONES_BASE = 8     # renglones sin rótulo encima del cuadro de totales que se suman como base imponible
UMBRAL_SEGURO = 0.85       # confianza de lectura a partir de la cual una cifra no se pide confirmar (borradores.UMBRAL_CONFIANZA)

# Puntos guía entre un rótulo y su cifra («IVA 21% ………… 315,00 €»): solo puntos, puntos suspensivos, guiones bajos o rayas
RE_RELLENO = re.compile(r"[.…·•‥_]{2,}|…|[-–—]{3,}")
RE_MONEDA = re.compile(r"€|eur|euros?|\(?€\)?", re.I)
RE_COMA_DECIMAL = re.compile(r"[-−]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}")
RE_PUNTO_DECIMAL = re.compile(r"[-−]?\d+\.\d{2}")
# Renglón de un impuesto aunque su rótulo no sea uno conocido («Impuesto 21%», «Tax 21%», «lVA 21%» leído por OCR, «I.V.A. al
# 21 por 100»): un porcentaje o una palabra de impuesto. Nunca es un renglón de la base, y en el cuadro descarta la factura
# sin IVA
_LETRA = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
RE_PALABRA_IMPUESTO = re.compile(rf"(?<![{_LETRA}])(?:impuestos?|tax(?:es)?|vat|tva|igic|ipsi|i\.?\s?v\.?\s?a\.?|[l1|!]\.?\s?v\.?\s?a\.?)"
                                 rf"(?![{_LETRA}])", re.I)
RE_PORCENTAJE_ROTULO = re.compile(r"%|°/o|\bo/o\b|\bpor\s*(?:100|ciento)\b", re.I)
# exención o no sujeción declarada: compatible con una factura sin IVA (no la descarta)
RE_SIN_IVA_DECLARADO = re.compile(r"exent[oa]s?|exenci[oó]n|no\s+sujet[oa]s?|inversi[oó]n\s+del\s+sujeto\s+pasivo", re.I)
# el total incluye el IVA («IVA incluido», «impuestos incluidos», «incluye IVA»): el documento sí repercute IVA
RE_IVA_INCLUIDO = re.compile(r"(?:\biva|i\.\s?v\.\s?a\.?|\bimpuestos?)\s*(?:incl\.?|incluid[oa]s?|inclu[íi]d[oa]s?)(?![a-z])|"
                             r"\binclu(?:ye|ido|idos)\s+(?:el\s+)?(?:iva|i\.\s?v\.\s?a|impuestos?)\b|\bcon\s+iva\b|\biva\s+inc\b", re.I)
# Fila de puntos guía leída por OCR como letras pequeñas («...oo.ooposor.oooopo», «econo...»): signos sin trazos altos
CARACTERES_BAJOS = set(".,:;·•…‥'`’”\"°º-_~oO0acemnrsuvwxzpqg")


def _rotulo_de_impuesto(t: str) -> bool:
    """Texto con un porcentaje o una palabra de impuesto (ver RE_PALABRA_IMPUESTO)."""
    return bool(RE_PORCENTAJE_ROTULO.search(t) or RE_PALABRA_IMPUESTO.search(t))


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
    if m and ("." not in m.group(3) or 1990 <= int(m.group(3).replace(".", "")) <= 2100):
        return f"{int(m.group(3).replace('.', '')):04d}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
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


def _dato_tras_dos_puntos(t: str) -> bool:
    r"""Equivale a re.search(r":\s*\S+\s*$", t) («Rótulo: valor» al final del texto), en tiempo lineal: la expresión es
    cuadrática con una racha larga de caracteres sin espacios."""
    s = t.rstrip()
    j = len(s)
    while j > 0 and not s[j - 1].isspace():
        j -= 1
    ultima = s[j:]   # último tramo sin espacios
    return bool(ultima) and (":" in ultima[:-1] or s[:j].rstrip().endswith(":"))


def _es_importe(t: str) -> bool:
    return bool(RE_IMPORTE.fullmatch(_limpiar_cifra(t)))


IMPORTE_MAXIMO = Decimal(10) ** 12  # una cifra mayor no es un importe de factura (y desbordaría la aritmética decimal)


def _num(t: str) -> Decimal | None:
    t = _limpiar_cifra(t)
    if len(t) > 40:
        return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):   # 5.120 (entero con separador de miles)
        v = Decimal(t.replace(".", ""))
    else:
        if re.fullmatch(r"-?\d{1,3}(?: \d{3})+,\d{1,4}", t):   # 2 450,00 (espacio como separador de miles)
            t = t.replace(" ", "")
        if t.count(",") > 1 and "." in t and t.rfind(".") > t.rfind(","):   # 1,234.56 (formato inglés)
            t = t.replace(",", "")
        v = parse_importe(t)
    return v if v is not None and v.is_finite() and abs(v) < IMPORTE_MAXIMO else None


def _iban_valido(iban: str) -> bool:
    s = iban[4:] + iban[:4]
    try:
        return int("".join(str(int(ch, 36)) for ch in s)) % 97 == 1
    except ValueError:
        return False


class Extractor:
    def __init__(self, paginas: list[Pagina], nombre_archivo: str = "", nif_empresa: str | None = None):
        self._texto_oculto = {pg.numero: getattr(pg, "texto_oculto", 0) for pg in paginas}
        paginas = [Pagina(pg.numero, pg.ancho, pg.alto, _normalizar_palabras(pg.palabras), pg.metodo, pg.conf_media)
                   if hasattr(pg, "metodo") else pg for pg in paginas]
        self.paginas = paginas
        self.alto = {p.numero: p.alto for p in paginas}
        self.ancho = {p.numero: p.ancho for p in paginas}
        self.lineas = agrupar_lineas(paginas)
        self._lineas_por_top = None
        self.filas = self._filas()
        self._indice_filas = None
        self.nombre_archivo = nombre_archivo
        self.nif_empresa = nifmod.normalizar(nif_empresa) if nif_empresa else None
        self._tabla = None

    # --- geometría ------------------------------------------------------------------------------------
    def _filas(self) -> list[Fila]:
        """Agrupa los bloques a la misma altura. Coste lineal: por página, solo se prueban las filas que aún pueden
        solaparse con el renglón (vienen ordenados por round(top)), con su extensión vertical precalculada. Una fila no
        crece más del triple de su bloque más alto: renglones «en escalera» no se encadenan en una fila de toda la página."""
        filas: list[Fila] = []
        pagina, abiertas, r_ant = None, [], None   # abiertas: [fila, top, bottom, altura del bloque más alto]
        podar = all(l.bottom >= l.top for l in self.lineas)
        for l in self.lineas:
            if l.pagina != pagina:
                pagina, abiertas, r_ant = l.pagina, [], None
            r = round(l.top)
            if podar and r != r_ant:
                abiertas = [a for a in abiertas if a[2] > r - 0.5]
                r_ant = r
            h = l.bottom - l.top
            for a in abiertas:
                if min(a[2], l.bottom) - max(a[1], l.top) > 0.4 * h \
                        and max(a[2], l.bottom) - min(a[1], l.top) <= MAX_ALTO_FILA * max(a[3], h):
                    a[0].bloques.append(l)
                    a[1], a[2], a[3] = min(a[1], l.top), max(a[2], l.bottom), max(a[3], h)
                    if len(a[0].bloques) >= MAX_BLOQUES_FILA:
                        # fila completa: un bloque muy alto (un glifo enorme) no encadena todos los renglones de su lado
                        abiertas = [x for x in abiertas if x is not a]
                    break
            else:
                filas.append(Fila(l.pagina, [l]))
                abiertas.append([filas[-1], l.top, l.bottom, h])
        filas.sort(key=lambda f: (f.pagina, f.top))
        return filas

    @staticmethod
    def _clave_linea(l: Linea) -> tuple:
        return (l.pagina, tuple((p.texto, p.x0, p.top, p.x1, p.bottom, p.conf, p.metodo) for p in l.palabras))

    def _fila_de(self, linea: Linea) -> Fila:
        # primera fila con un bloque igual al renglón (como `linea in f.bloques`), con un índice construido una vez
        if self._indice_filas is None:
            self._indice_filas = {}
            for f in self.filas:
                for b in f.bloques:
                    self._indice_filas.setdefault(self._clave_linea(b), f)
        f = self._indice_filas.get(self._clave_linea(linea))
        return f if f is not None else Fila(linea.pagina, [linea])

    def _a_la_derecha(self, linea: Linea) -> list[Linea]:
        f = self._fila_de(linea)
        return sorted([b for b in f.bloques if b.x0 >= linea.x1 - 1 and b is not linea], key=lambda b: b.x0)

    def _lineas_bajo(self, linea: Linea, max_dist: float) -> list[Linea]:
        """Renglones de la página de `linea` que empiezan hasta `max_dist` por debajo de ella, en el orden de self.lineas.
        Índice por página ordenado por top, construido una vez: el coste no crece con el número de renglones."""
        sel = self._entre_tops(linea.pagina, linea.bottom, linea.bottom + max_dist + 1e-6)
        return [self.lineas[k] for k in sorted(sel) if 0 < self.lineas[k].top - linea.bottom <= max_dist]

    def _entre_tops(self, pagina: int, desde: float, hasta: float) -> list[int]:
        """Índices (en self.lineas) de los renglones de la página con desde <= top <= hasta (índice construido una vez)."""
        if self._lineas_por_top is None:
            idx: dict[int, list] = {}
            for k, l in enumerate(self.lineas):
                idx.setdefault(l.pagina, []).append((l.top, k))
            self._lineas_por_top = {pg: ([t for t, _ in v], [k for _, k in v]) for pg, v in
                                    ((pg, sorted(v)) for pg, v in idx.items())}
        tops, ks = self._lineas_por_top.get(pagina, ([], []))
        return ks[bisect.bisect_left(tops, desde):bisect.bisect_right(tops, hasta)]

    def _debajo(self, x0: float, x1: float, linea: Linea, max_dist=28) -> Linea | None:
        cands = [l for l in self._lineas_bajo(linea, max_dist) if min(l.x1, x1) - max(l.x0, x0) > 0]
        if not cands:
            return None
        # en la fila de debajo, el bloque más centrado bajo el rótulo (no el primero por la izquierda)
        top = min(l.top for l in cands)
        fila = [l for l in cands if l.top - top <= max(2.0, 0.4 * (l.bottom - l.top))]
        return min(fila, key=lambda l: abs((l.x0 + l.x1) / 2 - (x0 + x1) / 2))

    @staticmethod
    def _segmentos(pals: list[Palabra]) -> list[list[Palabra]]:
        """Agrupa palabras de un renglón en celdas: un hueco mayor que el espacio normal separa columnas."""
        segs: list[list[Palabra]] = []
        for p in sorted(pals, key=lambda p: p.x0):
            if segs and p.x0 - segs[-1][-1].x1 <= max(6.0, 0.9 * max(p.bottom - p.top, segs[-1][-1].bottom - segs[-1][-1].top)):
                segs[-1].append(p)
            else:
                segs.append([p])
        return segs

    @staticmethod
    def _desalineacion(a: tuple[float, float], b: tuple[float, float]) -> float:
        """Distancia de alineación entre dos tramos horizontales (borde izquierdo, derecho o centro)."""
        return min(abs(a[0] - b[0]), abs(a[1] - b[1]), abs((a[0] + a[1]) / 2 - (b[0] + b[1]) / 2))

    def _valor_debajo(self, l: Linea, pal_et: list[Palabra], validar) -> tuple | None:
        """Rejilla «fila de rótulos / fila de valores»: el valor de la columna de este rótulo en la fila de debajo.

        Cada valor de la fila inferior se atribuye al rótulo con el que está alineado (izquierda, derecha o centro), no al
        primero que cae en la ventana: con columnas próximas, el valor de la columna vecina no es el de este rótulo.
        Devuelve (valor, palabras, texto_del_valor, dudoso) o None."""
        pal_et = pal_et or l.palabras
        ex0, ex1 = min(p.x0 for p in pal_et), max(p.x1 for p in pal_et)
        x0, x1 = ex0 - 30, ex1 + 60
        cands = [b for b in self._lineas_bajo(l, 28) if min(b.x1, x1) - max(b.x0, x0) > 0]
        if not cands:
            return None
        top = min(b.top for b in cands)
        fila = sorted([b for b in cands if b.top - top <= max(2.0, 0.4 * (b.bottom - b.top))], key=lambda b: b.x0)
        # un rótulo en la fila de debajo se queda con lo que tiene a su derecha («Vencimiento: 05/11/2026»)
        bloques = []
        for b in fila:
            if self._es_etiqueta(b.texto):
                break
            bloques.append(b)
        pals = [p for b in bloques for p in b.palabras if x0 - 20 <= (p.x0 + p.x1) / 2 <= x1 + 40]
        if not pals:
            return None
        # Celdas de la fila de rótulos: la propia (la del rótulo encontrado) y las ajenas (otros rótulos u otros textos)
        ids_et = {id(p) for p in pal_et}
        ajenas = []
        for seg in self._segmentos(self._fila_de(l).palabras):
            if not any(id(p) in ids_et for p in seg):
                ajenas.append((seg[0].x0, seg[-1].x1))
                continue
            # en la misma celda, otro rótulo pegado antes o después del encontrado («Fecha factura Fecha operación»)
            antes = [p for p in seg if p.x1 <= ex0 and id(p) not in ids_et]
            despues = [p for p in seg if p.x0 >= ex1 and id(p) not in ids_et]
            for grupo in (antes, despues):
                if grupo and self._es_etiqueta(" ".join(p.texto for p in grupo)):
                    ajenas.append((grupo[0].x0, grupo[-1].x1))
        propia = (ex0, ex1)
        celdas = sorted(ajenas + [propia])
        reparto = None

        def de_esta_columna(seg) -> bool:
            nonlocal reparto
            if not ajenas:
                return True
            tramo = (seg[0].x0, seg[-1].x1)
            d = self._desalineacion(tramo, propia)
            d_aj = min(self._desalineacion(tramo, a) for a in ajenas)
            if min(d, d_aj) > max(4.0, 0.6 * max(p.bottom - p.top for p in seg)):
                # sin alineación clara con ningún rótulo (rótulo arriba a la izquierda de la casilla y valor a su
                # derecha): manda el reparto en columnas de toda la fila, si es inequívoco
                if reparto is None:
                    reparto = self._reparto_columnas(self._fila_de(fila[0]).palabras, celdas, validar)
                col = reparto.get(tuple(id(p) for p in seg))
                if col is not None:
                    return col == propia
            return d <= d_aj  # alineado con otra columna: es su valor

        suyos = [seg for seg in self._partir_valores(self._segmentos(pals), celdas, validar) if de_esta_columna(seg)]
        opciones = []
        for seg in suyos:
            v = validar(" ".join(p.texto for p in seg))
            if v is not None:
                opciones.append((self._desalineacion((seg[0].x0, seg[-1].x1), propia), v, seg))
        if not opciones:
            # valor partido en varios tramos (p. ej. una fecha con espacios): el texto de todos los de esta columna
            propios = [p for seg in suyos for p in seg]
            v = validar(" ".join(p.texto for p in propios)) if propios else None
            return (v, propios, " ".join(p.texto for p in propios), False) if v is not None else None
        opciones.sort(key=lambda o: o[0])
        d, v, seg = opciones[0]
        # varios valores distintos sin uno claramente alineado con el rótulo: dudoso
        tol = max(4.0, 0.6 * max(p.bottom - p.top for p in seg))
        otros = [o for o in opciones[1:] if o[1] != v]
        dudoso = bool(otros) and (d > tol or otros[0][0] <= tol)
        return v, seg, " ".join(p.texto for p in seg), dudoso

    def _partir_valores(self, segs: list[list[Palabra]], celdas: list[tuple], validar) -> list[list[Palabra]]:
        """Valores de columnas vecinas pegados en un tramo («05/10/2026 28/09/2026» bajo «Fecha factura | Fecha entrega»):
        se separan si cada trozo está alineado con un rótulo distinto (en el mismo orden), al menos dos trozos son valores
        y el validador del tramo entero solo tomaría el primero. Un valor de varias palabras («15 de octubre de 2026»,
        «Transferencia a 30 días») no se parte."""
        out = []
        for seg in segs:
            n, mejor = len(seg), None
            v_todo = validar(" ".join(p.texto for p in seg)) if 1 < n <= 8 and len(celdas) > 1 else None
            if v_todo is not None:
                tol = max(4.0, 0.6 * max(p.bottom - p.top for p in seg))
                alin, vals = {}, {}
                for i in range(n):
                    for j in range(i + 1, n + 1):
                        ds = [self._desalineacion((seg[i].x0, seg[j - 1].x1), c) for c in celdas]
                        k = min(range(len(celdas)), key=ds.__getitem__)
                        alin[i, j] = (k, ds[k]) if ds[k] <= tol else None
                for cortes in itertools.product((False, True), repeat=n - 1):
                    lim = [0] + [k for k, c in enumerate(cortes, 1) if c] + [n]
                    if len(lim) < 3:
                        continue
                    tramos = list(zip(lim, lim[1:]))
                    al = [alin[t] for t in tramos]
                    if None in al or any(b[0] <= a[0] for a, b in zip(al, al[1:])):
                        continue  # algún trozo sin rótulo claro, o dos trozos en la misma columna o desordenados
                    for t in tramos:
                        if t not in vals:
                            vals[t] = validar(" ".join(p.texto for p in seg[t[0]:t[1]]))
                    validos = [vals[t] for t in tramos if vals[t] is not None]
                    if len(validos) >= 2 and validos[0] == v_todo:
                        clave = (-len(tramos), sum(a[1] for a in al))
                        if mejor is None or clave < mejor[0]:
                            mejor = (clave, [seg[a:b] for a, b in tramos])
            out.extend(mejor[1] if mejor else [seg])
        return out

    def _reparto_columnas(self, palabras: list[Palabra], celdas: list[tuple], validar) -> dict:
        """Reparto de la fila de valores de una rejilla entre las columnas de la fila de rótulos («celdas», ordenadas): cada
        columna se delimita por los bordes izquierdos de los rótulos, por los derechos o por los puntos medios entre sus
        centros. Una hipótesis es coherente si cada tramo de valores cae entero en una columna y ninguna recibe dos.
        Devuelve {ids de las palabras del tramo: celda} con los tramos en que todas las hipótesis coherentes coinciden."""
        # lo que queda lejos de la rejilla (más allá del menor hueco entre rótulos) es otro texto de la fila, no un valor
        hueco = max(10.0, min((b[0] - a[1] for a, b in zip(celdas, celdas[1:])), default=10.0))
        tramos = [t for t in self._partir_valores(self._segmentos(palabras), celdas, validar)
                  if t[-1].x1 >= celdas[0][0] - hueco and t[0].x0 <= celdas[-1][1] + hueco]
        n, inf = len(celdas), float("inf")
        medios = [((a[0] + a[1]) / 2 + (b[0] + b[1]) / 2) / 2 for a, b in zip(celdas, celdas[1:])]
        hipotesis = ([(c[0], celdas[k + 1][0] if k + 1 < n else inf) for k, c in enumerate(celdas)],
                     [(celdas[k - 1][1] if k else -inf, c[1]) for k, c in enumerate(celdas)],
                     [(medios[k - 1] if k else -inf, medios[k] if k < n - 1 else inf) for k in range(n)])
        repartos = []
        for lims in hipotesis:
            asig = []
            for t in tramos:
                k = next((k for k, (lo, hi) in enumerate(lims) if t[0].x0 >= lo - 1 and t[-1].x1 <= hi + 1), None)
                if k is None or k in asig:
                    break
                asig.append(k)
            else:
                repartos.append(asig)
        out = {}
        for i, t in enumerate(tramos):
            ks = {r[i] for r in repartos}
            if len(ks) == 1:
                out[tuple(id(p) for p in t)] = celdas[ks.pop()]
        return out

    @staticmethod
    def _es_etiqueta(texto: str) -> bool:
        t = texto.strip()
        return bool(re.search(r":\s*$", t)) or bool(re.match(
            r"^(n(?:\.?[º°]|o)\.?|fecha|cliente|contrato|pedido|certificaci|obra|proyecto|vencimiento|forma\s+de\s+pago|iban|"
            r"c\.?i\.?f|n\.?i\.?f|base|iva|total|importe|descripci|partida|expediente|referencia|n[úu]mero|n[úu]m\.|"
            r"cód\.?|c[oó]digo|p[áa]gina|serie|comercial|agente|ruta)\b", t, re.I))

    def _rotulo_importe(self, l: Linea) -> bool:
        """El bloque empieza por el rótulo de un importe («Subtotal», «Total», «IVA 21 %»): es otra columna del documento
        (p. ej. el cuadro de totales a la derecha), no el valor del rótulo de su izquierda."""
        ets = self._etiquetas_en(l.palabras)
        return bool(ets) and ets[0]["pals"][0] is l.palabras[0]

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
            excluida = bool(re_exc and re_exc.search(t))
            celdas = self._segmentos(l.palabras) if excluida else []
            if excluida and len(celdas) < 2:
                continue
            for m in itertools.islice(re_et.finditer(t), MAX_ETIQUETAS_RENGLON):
                if excluida:
                    # Renglón de rótulos de una rejilla («Número Fecha Cód. cliente Albarán»): la exclusión se mira en la
                    # celda del rótulo, no en las de las otras columnas
                    ids = {id(p) for p in self._palabras_rango(l, m.start(), m.end())}
                    celda = next((c for c in celdas if any(id(p) in ids for p in c)), None)
                    if celda is None or re_exc.search(" ".join(p.texto for p in celda)):
                        continue
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
                            and id(vec[0]) not in en_tabla and not self._rotulo_importe(vec[0]):
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
                if vecinos and not rejilla and not self._es_etiqueta(vecinos[0].texto) and not self._rotulo_importe(vecinos[0]):
                    v = validar(vecinos[0].texto)
                    if v is not None:
                        out.append((v, vecinos[0].palabras, l.pagina, t + " " + vecinos[0].texto, "derecha"))
                        continue
                if abajo:
                    r = self._valor_debajo(l, self._palabras_rango(l, m.start(), m.end()), validar)
                    if r is not None:
                        v, pals, txt_deb, dudoso = r
                        out.append((v, pals, l.pagina, t + " / " + txt_deb, "debajo_dudoso" if dudoso else "debajo"))
        return out

    @staticmethod
    def _palabras_desde(l: Linea, pos: int) -> list[Palabra]:
        return Extractor._palabras_rango(l, pos, None)

    @staticmethod
    def _palabras_rango(l: Linea, a: int, b: int | None) -> list[Palabra]:
        """Palabras de la línea que tocan el tramo [a, b) de su texto (b=None: hasta el final). Las posiciones de las
        palabras se calculan una vez por línea; con ellas ordenadas basta una búsqueda binaria."""
        pos, fines = l.posiciones, l.fines_ordenados
        if fines is not None:
            k = bisect.bisect_right(fines, a)
            if b is None:
                return l.palabras[k:]
            out = []
            while k < len(pos) and pos[k][0] < b:
                out.append(l.palabras[k])
                k += 1
            return out
        return [p for p, (ini, fin) in zip(l.palabras, pos) if fin > a and (b is None or ini < b)]

    def _mejor(self, cands, preferir=None):
        if not cands:
            return None
        if preferir:
            cands = sorted(cands, key=preferir)
        return cands[0]

    def _campo_de(self, cand, valor=None, conf=None, aviso=None):
        v, pals, pag, texto, estr = cand
        c = _campo(valor if valor is not None else v, texto=texto, palabras=pals, pagina=pag, confianza=conf, aviso=aviso)
        if estr == "debajo_dudoso":
            c["confianza"] = min(c["confianza"], 0.7)
            c["avisos"].append("Varios valores bajo el rótulo y ninguno claramente alineado con él: confirme")
        return c

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

    # Abreviatura de letras con punto al final del texto («a 60 días f.f.», «EMPRESA S.L.»): su último punto no es puntuación
    RE_ABREVIATURA_FINAL = re.compile(r"(?:^|\s)(?:[^\W\d_]{1,3}\.){2,}$")

    @staticmethod
    def _v_texto(t: str):
        abrev = Extractor.RE_ABREVIATURA_FINAL.search(t.strip(" :-–"))
        t = t.strip(" :.-–")
        if not t or len(t) > 200:
            return None
        return t + "." if abrev else t

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

        def campo(tipo, l, m, **kw):
            # procedencia y confianza de la palabra clave, no las del resto del renglón (el número que la sigue o una
            # abreviatura «Nº» mal leída por el OCR no hacen dudoso el tipo)
            return _campo(tipo, texto=l.texto, palabras=self._palabras_rango(l, m.start(), m.end()) or l.palabras,
                          pagina=l.pagina, **kw)

        def titular(l, m):  # el renglón empieza por la palabra clave y solo le sigue, si acaso, el número («FACTURA Nº. X»)
            return not l.texto[:m.start()].strip() and re.fullmatch(
                r"\s*(?:(?:n(?:\.?[º°]|[o*])\.?|n[úu]m(?:ero)?\.?|" + NUM_ABR_OCR + r")\s*)?[:.]?\s*(?:[A-Z0-9][\w/.-]*)?\s*",
                l.texto[m.end():], re.I) is not None

        def buscar(lineas, pats, cond, por_tamano=False):
            hallados = []
            for prio, (tipo, pat) in enumerate(pats):
                for l in lineas:
                    m = re.search(pat, l.texto, re.I)
                    if m and not RE_REGISTRO_MERCANTIL.search(l.texto) and cond(l, m):
                        if not por_tamano:
                            return campo(tipo, l, m)
                        hallados.append((-max(p.bottom - p.top for p in l.palabras), l.top, prio, tipo, l, m))
            if hallados:
                # el título destacado manda sobre los rótulos de una rejilla («ALBARÁN», «PEDIDO»…)
                alto = min(h[0] for h in hallados)
                tipo = min((h for h in hallados if h[0] <= alto + 0.5), key=lambda h: (h[2], h[1]))[3]
                # De ese tipo y con letra de ese tamaño, el renglón que es un título antes que una frase («Factura por la
                # gestión…») o un rótulo («TOTAL FACTURA»). En un escaneo la caja de las palabras varía con los rasgos
                # que suben o bajan («gestión» mide más que «FACTURA»): el margen de tamaño es mayor
                afines = [h for h in hallados if h[3] == tipo and
                          h[0] <= alto + (0.25 * -alto if any(p.metodo == "ocr" for p in h[4].palabras) else 0.5)]
                _, _, _, tipo, l, m = min(afines, key=lambda h: (not titular(h[4], h[5]), h[2], h[1]))
                return campo(tipo, l, m)
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
        no_ref = lambda l, m: not re.match(r"\s*(n(?:\.?[º°]|o)\.?|n[úu]m(ero)?\.?|" + NUM_ABR_OCR + r"|:|[A-Z]{0,4}[-/]?\d)",
                                           l.texto[m.end():], re.I)

        def encabeza(l, m):
            # La palabra clave abre su renglón o su celda (detrás, si acaso, de «Factura», «Nota de», «Nº» o «Tipo de documento:»):
            # es el nombre del documento, no una mención dentro de un dato o una frase («Forma de pago: abono en cuenta»,
            # «Según presupuesto aceptado por el cliente»)
            pals = self._palabras_rango(l, m.start(), m.end())
            seg = next((g for g in self._segmentos(l.palabras) if pals and any(p is pals[0] for p in g)), None)
            ini = l.posiciones[next(i for i, p in enumerate(l.palabras) if p is seg[0])][0] if seg else 0
            fin = ini + len(" ".join(p.texto for p in seg)) if seg else len(l.texto)
            # ni la sigue una frase en minúscula («Presupuesto aceptado el …», «abono en cuenta») o la fecha de otro papel
            # («Albarán de fecha 01/10/2026», «Presupuesto del 01/10/2026»)
            despues = l.texto[m.end():fin]
            # en minúsculas al comienzo del renglón es una frase que continúa la del renglón anterior («…conforme a
            # nuestro ⏎ presupuesto P-118/26 de …»), no el título del documento
            if m.group().islower() and not l.texto[:m.start()].strip():
                return False
            return re.fullmatch(r"(?:\s*(?:factura|fra\.?|nota|documento|tipo(?:\s+de\s+documento)?|n(?:\.?\s?[º°o])?\.?|"
                                r"n[úu]m(?:ero)?\.?|nro\.?)(?:\s+de)?\s*:?)*\s*", l.texto[max(ini, 0):m.start()], re.I) is not None and \
                not re.match(r"\s+(?!(?:de|del|n[úu]m|n[úu]mero)\b)[a-záéíóúñ]{2,}\b", despues) and \
                not re.match(r"\s+(?:de|del|con)\s+(?:fecha\b|\d)", despues, re.I)

        r = buscar(cabecera, orden, lambda l, m: no_ref(l, m) and encabeza(l, m))
        if not r:
            for l in pag1:
                m = re.search(r"\bfactura(?=\s*(?:n(?:\.?[º°]|o)\.?|n[úu]m(?:ero)?\.?|" + NUM_ABR + r"|(?<=\s)" + NUM_ABR_OCR +
                              r")\s*:?\s*[A-Z0-9])", l.texto, re.I)
                if m is None and re.match(RE_TITULO_NUM_DUDOSO, l.texto, re.I):
                    m = re.search(r"factura", l.texto, re.I)  # «FACTURA NC. Z72/26»: el título con la º mal leída
                if m and not re.search(r"\b(?:proforma|rectificad|anterior|original)\b", l.texto, re.I):
                    return campo("factura", l, m, confianza=0.85)
            r = buscar(cabecera, orden, encabeza)
            if not r:
                # la palabra clave solo aparece mencionada en la cabecera: el tipo se deduce, a confirmar
                r = buscar(cabecera, orden, lambda l, m: True)
                if r:
                    r["confianza"] = min(r["confianza"], 0.6)
                    r["avisos"].append("Tipo de documento deducido de una mención en la cabecera: confirme")
        if r:
            return r
        for tipo, pat in orden[:5]:
            for l in self.lineas:
                if m := re.search(pat, l.texto, re.I):
                    return campo(tipo, l, m, aviso="Indicado fuera de la cabecera")
        return _vacio("No se identifica el tipo de documento")

    def numero(self):
        excl = (r"certificaci|pedido|partida|tomo|folio|hoja|registro|tel[eé]?f|tfno|iban|cuenta|c\.?\s?i\.?\s?f|n\.?\s?i\.?\s?f|"
                r"cliente\s+n|contrato|expediente|proyecto|albar[aá]n")
        patrones = [
            (0, r"(?:factura|fra\.?|invoice)\s*(?:proforma\s*|rectificativa\s*)?(?:n(?:\.?[º°]|[o*])\.?|n[úu]m(?:ero)?\.?|no\.?|#|"
                r"(?<=\s)" + NUM_ABR_OCR + r")" + NO_NUMERA_OTRA),
            (0, r"(?:n(?:\.?[º°]|[o*])\.?|(?<![\w.])" + NUM_ABR_OCR + r")\s*(?:de\s+)?(?:factura|fra\.?)"),
            (0, r"n[úu]m(?:ero)?\.?\s+(?:de\s+)?factura"),
            (1, r"(?:proforma|presupuesto|borrador|documento)\s*(?:n(?:\.?[º°]|[o*])\.?|n[úu]m(?:ero)?\.?)"),
            (1, r"(?:n[úu]mero|n[úu]m\.?|n(?:\.?[º°]|[o*])\.?)\s*(?:de\s+)?(?:la\s+)?(?:proforma|presupuesto|documento|borrador)"),
            # «Nº:» leído por el OCR como «NO:», «N0:» o «N?:» (solo seguido de dos puntos: nunca el «no» de una frase)
            (2, r"^n[úu]mero\b|^n[úu]m\.?(?=\s)|^n\.?[º°]\.?(?=\s|:)|^n[o0?*]\.?(?=\s*:)"),
            # «Nº FACTURA» leído por el OCR como «N2 FACTURA»: rótulo supuesto, como los anteriores (se confirma)
            (2, r"(?<!\w)n2\s+factura\b"),
            (2, RE_TITULO_NUM_DUDOSO),
            (3, r"(?:^|\s)factura\s+(?=[A-Z0-9-]*\d)"),
        ]
        tipo = self.tipo_documento().get("valor")
        cands = []
        calificados: set[int] = set()  # candidatos con el propio rótulo calificado de forma ambigua («Nº factura anticipo:»)
        for prio, pat in patrones:
            if prio == 1 and tipo not in ("proforma", "presupuesto", "borrador"):
                prio = 4  # en una factura, «Presupuesto nº» es una referencia
            for c in self.candidatos(pat, self._v_codigo, excluir=excl, zona_sup=0.6):
                l_txt = c[3]
                if RE_VIA.search(l_txt) and prio > 0:
                    continue
                cands.append((self._prioridad_numero(prio, pat, c, tipo, calificados), c))
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
        digitos_archivo = [d.lstrip("0") for d in re.findall(r"\d+", self.nombre_archivo)]

        def coincide_archivo(v: str) -> bool:
            # todos los grupos de cifras del número, seguidos, en el nombre del archivo (no solo el primero, que suele ser el año)
            dv = [d.lstrip("0") for d in re.findall(r"\d+", v)]
            return bool(dv) and any(digitos_archivo[i:i + len(dv)] == dv for i in range(len(digitos_archivo) - len(dv) + 1))

        def clave(pc):
            prio, c = pc
            return (prio, 0 if coincide_archivo(c[0]) else 1, c[1][0].top if c[1] else 0)
        prio, mejor = sorted(cands, key=clave)[0]
        campo = self._campo_de(mejor)
        if prio > 1:
            campo["confianza"] = min(campo["confianza"], 0.8)
        # «FACTURA Nº 2026 / 045»: tras el valor sigue una barra con espacios y más cifras; el número puede ser el conjunto
        cola = " ".join(p.texto for p in mejor[1]) if mejor[1] else ""
        k = cola.find(mejor[0])
        if k >= 0 and re.match(r"\s*/\s+\d|\s+/\s*\d", cola[k + len(mejor[0]):]):
            campo["avisos"].append("El número puede seguir tras la barra: confirme")
            campo["confianza"] = min(campo["confianza"], 0.7)

        def encima(c, ref) -> bool:
            # c impreso antes que ref en el orden de lectura (página anterior o renglón claramente más alto)
            if not c[1] or not ref[1]:
                return False
            if c[2] != ref[2]:
                return c[2] < ref[2]
            a, b = c[1][0], ref[1][0]
            return a.top < b.top - 0.5 * (b.bottom - b.top)
        # Con un rótulo débil («Nº: X», «FACTURA X») cualquier otro candidato es una alternativa a confirmar (salvo los de
        # otra cosa: «Nº cliente: 4471»). Con uno fuerte compiten los de su misma clase y los números propios posibles (no las
        # citas ya reconocidas ni los de otra cosa) impresos por encima: el propio con un rótulo débil en la cabecera y, más
        # abajo, la cita de otra factura con un rótulo limpio («Rectifica a:» ⏎ «Factura nº F-030»); estén donde estén si el
        # propio rótulo es ambiguo («Nº factura anticipo: X»: el de esta factura o el de la de anticipo)
        distintos = {c[0] for p, c in cands
                     if p == prio or prio == 5 or (p < 5 if prio >= 2 else
                                                   (p < 4 and (id(mejor) in calificados or encima(c, mejor))))}
        if len(distintos) > 1:
            campo["avisos"].append(f"Varios números posibles: {', '.join(sorted(distintos))}")
            campo["confianza"] = min(campo["confianza"], 0.7)
        if coincide_archivo(mejor[0]):
            campo["avisos"].append("Coincide con el nombre del archivo")
        return campo

    # Palabra justo antes del rótulo que lo mete en una frase o lo presenta como cita («con la factura nº», «s/ factura nº»,
    # «Ref. factura nº»); no «Página 1 de 2 Factura nº». Los posesivos se saltan: «según nuestra factura nº» cita otra;
    # «Nuestra factura nº» a principio de renglón es el rótulo del número propio
    PREVIAS_REFERENCIA = {"la", "el", "las", "los", "de", "del", "con", "en", "a", "al", "según", "segun", "por", "sobre", "y", "e",
                          "o", "u", "que", "mediante", "contra", "s/", "n/", "ref", "refs", "referencia"}
    POSESIVOS = {"nuestra", "nuestro", "nuestras", "nuestros", "su", "sus", "vuestra", "vuestro", "vuestras", "vuestros"}
    # Artículo o preposición en minúscula al final del renglón de encima: el rótulo es su complemento («… se abonó con la» ⏎
    # «Factura nº F-030»). En otro renglón, una conjunción no basta («Servicios de reforma y» ⏎ «factura nº F-045»)
    ENLACES_FRASE = {"la", "el", "las", "los", "de", "del", "con", "en", "a", "al", "según", "segun", "por", "sobre", "mediante",
                     "contra", "s/", "n/"}
    # Calificativos que siempre designan otra factura («Nº factura rectificada», «… ya abonada»)
    RE_REF_OTRA = re.compile(r"rectificad|anterior|sustituid|anulad|abonad|referenciad|relacionad|asociad", re.I)
    # Calificativos ambiguos: el número propio de una factura de anticipo o el de la de anticipo que se descuenta
    RE_REF_AMBIGUA = re.compile(r"anticipo|original", re.I)
    # Ejemplar del propio documento antes del rótulo: «Duplicado de factura nº», «Copia de la factura nº»
    RE_EJEMPLAR = re.compile(r"(?:copia|duplicado|triplicado|original|ejemplar)(?:\s+(?:de|del|de\s+la|para\s+el\s+cliente))?", re.I)

    def _prioridad_numero(self, prio: int, patron: str, cand: tuple, tipo: str | None, calificados: set[int]) -> float:
        """Prioridad efectiva de un candidato a número propio según el contexto del rótulo: 4 si cita otra factura
        («… con la factura nº X», «Nº factura rectificada: X», «Anticipo: factura nº X»); 5 si el rótulo débil numera otra
        cosa («Nº cliente: 4471»); un rótulo fuerte calificado de forma ambigua («Nº factura anticipo: X») o precedido de
        otro texto queda detrás de los limpios pero delante de los débiles. Anota en `calificados` los que pueden ser el
        número de otra factura aunque no lo digan («Rectifica a: Factura nº X», «Anticipos:» ⏎ «Factura nº X»): con ellos,
        cualquier otro número propio posible del documento es una alternativa a confirmar."""
        v, pals, pag, texto, estr = cand
        rot, prev = self._renglon_previo(cand)
        for m in re.finditer(patron, texto, re.I):
            pos = texto.find(v, m.end())
            if pos < 0:
                continue
            antes = texto[:m.start()].split()
            if self.RE_EJEMPLAR.fullmatch(" ".join(antes).strip(" -–:;,.")):
                antes = []  # «Duplicado de factura nº», «Copia de la factura nº»: el propio documento
            al_inicio = not texto[:m.start()].strip()
            # renglón de encima cuya frase sigue en el del rótulo (este empieza por el rótulo)
            continua = prev.texto.split() if al_inicio and prev is not None and not re.search(r"[.:;!?]\s*$", prev.texto) else []
            ult, ult_c = self._ultima_no_posesiva(antes), self._ultima_no_posesiva(continua)
            cita = (ult is not None and ult.strip(",;.()").lower() in self.PREVIAS_REFERENCIA) or \
                (ult_c is not None and ult_c.strip(",;()") in self.ENLACES_FRASE)
            if antes and ult is None:
                antes = []  # solo un posesivo («Nuestra factura nº»): el rótulo del número propio
            en_frase = bool(continua) and texto[m.start():m.start() + 1].islower()
            if en_frase:
                antes = continua  # rótulo en minúscula a principio de renglón: sigue la frase del anterior
            previo = " ".join(antes).strip(" -–:;,.")
            if previo and self.RE_EJEMPLAR.fullmatch(previo):
                previo, antes = "", []
            # encabezado en el renglón de encima que presenta el número («Anticipos:», «Documentos relacionados»): unas pocas
            # palabras sin cifras, no otro dato («Nº factura rectificada: F-030») ni el ejemplar («ORIGINAL»)
            encabezado = prev.texto if al_inicio and prev is not None and not re.search(r"\d", prev.texto) and \
                len(prev.texto.split()) <= 6 and not self.RE_EJEMPLAR.fullmatch(prev.texto.strip(" -–:;,.")) else ""
            entre = texto[m.end():pos]
            despues = texto[pos + len(v):].split(":")[0][:40]
            if self.RE_REF_OTRA.search(entre) or self.RE_REF_OTRA.search(despues) or cita or \
                    self.RE_REF_OTRA.search(previo) or self.RE_REF_AMBIGUA.search(previo) or \
                    self.RE_REF_OTRA.search(encabezado) or (tipo == "rectificativa" and self.RE_REF_AMBIGUA.search(entre)):
                return 4
            if prio >= 2 and re.search(r"[a-záéíóúñ]{3,}", entre, re.I) and \
                    not re.search(r"factura|\bfra\b|invoice|documento|serie", entre, re.I):
                return 5
            if prio > 1:
                return prio
            if self.RE_REF_AMBIGUA.search(entre) or (previo and not en_frase and not self._previo_separado(rot, texto, m.start())) \
                    or re.search(r":\s*$", encabezado) or self.RE_REF_AMBIGUA.search(encabezado):
                # el propio rótulo es ambiguo («Nº factura anticipo:»), es el valor de otro rótulo («Rectifica a: Factura
                # nº», «Abono factura nº») o lo presenta un encabezado («Anticipos:» ⏎ «Factura nº»)
                calificados.add(id(cand))
            if self.RE_REF_AMBIGUA.search(entre) or self.RE_REF_AMBIGUA.search(despues):
                return prio + 0.5
            return prio + 0.7 if previo else prio
        return prio

    def _previo_separado(self, rot: Linea | None, texto: str, ini: int) -> bool:
        """El texto delante del rótulo es otra casilla del renglón, separada por un guion o un hueco («Página 1 de 1    Factura
        nº», «Madrid, 15/10/2026 - Factura nº»), no parte del rótulo («Rectifica a: Factura nº», «Abono factura nº»)."""
        crudo = texto[:ini].rstrip()
        if re.search(r"[-–—|·•]$", crudo):
            return True
        if crudo.endswith(":") or rot is None or ini > len(rot.texto):
            return False
        izq, rotulo = self._palabras_rango(rot, 0, ini), self._palabras_rango(rot, ini, ini + 1)
        if not izq or not rotulo or izq[-1] is rotulo[0]:
            return False
        return rotulo[0].x0 - izq[-1].x1 > 0.6 * (rotulo[0].bottom - rotulo[0].top)

    def _ultima_no_posesiva(self, palabras: list[str]) -> str | None:
        """Última palabra que no es un posesivo («según nuestra» → «según»); None si no queda ninguna."""
        k = len(palabras)
        while k and palabras[k - 1].strip(",;.()").lower() in self.POSESIVOS:
            k -= 1
        return palabras[k - 1] if k else None

    def _renglon_previo(self, cand: tuple) -> tuple[Linea | None, Linea | None]:
        """Renglón del rótulo de un candidato y el de justo encima con su mismo margen izquierdo e interlineado normal (el que
        puede continuar su frase o encabezarlo). Busca en el índice por altura: el coste no crece con el número de renglones."""
        v, pals, pag, texto, estr = cand
        if not pals:
            return None, None
        p0 = pals[0]
        cerca = [self.lineas[k] for k in self._entre_tops(pag, p0.top - 100, p0.top + 1)]
        rot = min((l for l in cerca if texto.startswith(l.texto)), key=lambda l: (p0.top - l.top, abs(l.x0 - p0.x0)), default=None)
        if rot is None:
            return None, None
        h = rot.bottom - rot.top
        encima = [self.lineas[k] for k in self._entre_tops(pag, rot.top - 6 * h, rot.top)]
        sup = [b for b in encima if b is not rot and 0 <= rot.top - b.bottom <= 1.2 * h and abs(b.x0 - rot.x0) <= max(3.0, 0.5 * h)]
        return rot, max(sup, key=lambda b: b.bottom, default=None)

    # Rótulo de la fecha de otro documento o acto («Fecha contrato:», «Fecha del presupuesto»): no es la del documento
    OTROS_ACTOS_FECHA = r"contrato|presupuesto|albar[aá]n|pedido|oferta|mandato|encargo|p[oó]liza|adjudicaci[oó]n|aceptaci[oó]n"
    RE_FECHA_DE_OTRO = re.compile(r"\bfecha\s+(?:de(?:l)?\s+)?(?:la\s+|el\s+)?(?:" + OTROS_ACTOS_FECHA + r")\b", re.I)

    def fecha(self):
        excl = r"vencimiento|operaci[oó]n|entrega|pedido|valor|nacimiento|cargo|firma|validez|caducidad|inicio|fin\b|desde|hasta|alta"
        otro = r"(?!\s*(?:de(?:l)?\s+)?(?:la\s+|el\s+)?(?:" + self.OTROS_ACTOS_FECHA + r")\b)"
        pats = [r"fecha\s+(?:de\s+)?(?:la\s+)?(?:factura|emisi[oó]n|expedici[oó]n)", r"\bfecha\b" + otro, r"\bdate\b",
                r"\bde\s+fecha\b" + otro, r"^(?:emitida|expedida|emisi[oó]n|data(?:\s+(?:de\s+)?(?:factura|emissi[oó]))?|data)\b"]
        for k, pat in enumerate(pats):
            cands = self.candidatos(pat, self._v_fecha, excluir=excl)
            if k in (1, 3):
                # «según el contrato de fecha 3 de marzo de 2024»: el rótulo está en una frase y fecha otra cosa
                cands = [c for c in cands if not self._rotulo_fecha_en_frase(c)]
            if cands:
                return self._campo_de(sorted(cands, key=lambda c: (c[2], c[1][0].top if c[1] else 0))[0])
        # Sin rótulo: la primera fecha de la parte superior, a confirmar; nunca la citada en una frase («s/contrato del 1 de
        # julio de 2025»: la de otro documento). Una fecha de carta («Villaficticia, 14 de octubre de 2026», «En
        # Villaficticia, a 14 de octubre de 2026») es la del documento si ninguna otra fecha sin rótulo (fuera de la tabla
        # de conceptos) la contradice. En las cartas va bajo el destinatario o al pie, antes de la firma; pero al pie hay
        # también fechas de otros actos («Recibido y conforme:» ⏎ «Villaficticia, 5 de noviembre de 2026», el mandato SEPA,
        # la letra pequeña de la protección de datos): la fecha de carta solo vale sin más si no la presenta un rótulo con dos
        # puntos, si por debajo del 40 % de la página no va en letra menor que la del texto, y si el documento no tiene otra
        # fecha de carta en blanco o ilegible («a ___ de ________ de 2026»): la fecha del documento sería entonces esa
        self.tabla()
        en_tabla = {id(b) for f in self.filas if id(f) in self._ids_filas_tabla for b in f.bloques}
        con_fecha = [(l, iso) for l in self.lineas if not re.search(excl, l.texto, re.I) and not self.RE_FECHA_DE_OTRO.search(l.texto)
                     and (iso := _fecha_iso(l.texto))]
        arriba = [(l, iso) for l, iso in con_fecha if l.top < 0.4 * self.alto[l.pagina] and not self._fecha_citada(l)]
        cartas = [(l, iso) for l, iso in con_fecha if id(l) not in en_tabla and self._fecha_de_carta(l.texto)
                  and not self._continua_frase(l)]
        firmes = [c for c in cartas if self._fecha_de_carta_firme(c[0])]
        carta = (firmes or cartas or [None])[0]
        sin_leer = next((l for l in self.lineas if id(l) not in en_tabla and self._fecha_de_carta_sin_leer(l.texto)), None)
        if firmes and sin_leer is None and all(iso == carta[1] for l, iso in con_fecha if id(l) not in en_tabla) \
                and not self._rotulo_de_fecha_sin_leer(en_tabla, excl):
            return _campo(carta[1], texto=carta[0].texto, linea=carta[0])
        if arriba or carta:
            l, iso = arriba[0] if arriba else carta
            c = _campo(iso, texto=l.texto, linea=l, confianza=0.6, aviso="Fecha sin etiqueta: confirme")
            if sin_leer is not None:
                c["avisos"].append(f"La fecha del documento está en blanco o no se lee («{sin_leer.texto[:60]}»): confirme")
            return c
        if sin_leer is not None:
            return _vacio(f"La fecha del documento está en blanco o no se lee («{sin_leer.texto[:60]}»)")
        return _vacio("No se encuentra la fecha")

    # Palabra que mete la fecha en una frase («s/contrato del 3 de marzo de 2024», «servicio prestado el 3/10/2026», «el
    # contrato de fecha 3/3/2024»): es la de otra cosa, no la del documento. «a» no: «Villaficticia a 14 de octubre de 2026»
    # es una fecha de carta
    PREVIAS_FECHA_CITADA = {"de", "del", "el", "la", "los", "al", "en", "con", "por", "para", "y", "e", "o", "u", "s/", "n/",
                            "fecha"}
    # Abreviatura de «número» en minúscula («nº», «no», «núm», «nro»): rotula la cifra que la sigue, no es una palabra de frase
    RE_ABR_NUMERO = re.compile(r"n(?:\.?\s?[º°o*]|[úu]m(?:ero)?|ro)?\.?|ref|p[aá]g", re.I)

    def _palabra_de_frase(self, palabra: str, siguiente: str = "") -> bool:
        """Palabra en minúscula de una frase («contrato», «prestado», «s/contrato»), no una palabra de enlace («de», «el»),
        una abreviatura del número que la sigue («nº 45», «núm. 45», «n.º 45») ni una cifra."""
        t = palabra.strip(",;:()«»\"'¿?¡!")
        if re.match(r"[sn]/[^\W\d_]", t):
            return True  # «s/contrato», «n/pedido»: cita de otro documento
        if not t[:1].islower() or len(t) < 2 or re.search(r"[\d.º°ª/]", t) or t.lower() in self.PREVIAS_FECHA_CITADA:
            return False
        return not (self.RE_ABR_NUMERO.fullmatch(t) and re.search(r"\d", siguiente))

    def _fecha_citada(self, l: Linea) -> bool:
        """La fecha del renglón (la que lee _fecha_iso) está citada dentro de una frase: la precede una palabra de enlace
        («del», «el», «s/», «de fecha») y, antes, otra palabra en minúscula de la frase («s/contrato del …», «prestado el …»;
        no una abreviatura como «nº 45») o la frase del renglón de encima («… según el contrato» ⏎ «del 3 de marzo de
        2024»). No un encabezado corto («Factura del 14/10/2026», «Factura nº 45 del 14/10/2026»)."""
        texto = l.texto
        m = next((m for r in (RE_FECHA_ISO, RE_FECHA, RE_FECHA_LARGA, RE_FECHA_ABR) if (m := r.search(texto))), None)
        antes = texto[:m.start()].split() if m else []
        if not antes or antes[-1].lower() not in self.PREVIAS_FECHA_CITADA or self.RE_FRASE_DE_EMISION.search(texto[:m.start()]):
            return False
        return any(self._palabra_de_frase(p, s) for p, s in zip(antes[:-1], antes[1:])) or \
            (len(antes) == 1 and self._continua_frase(l))

    # La frase habla de la emisión del propio documento («Factura emitida el 14/10/2026»): no es la fecha de otra cosa
    RE_FRASE_DE_EMISION = re.compile(r"\b(?:emitid|expedid|fechad)[oa]s?\b|\b(?:emisi[oó]n|expedici[oó]n)\b", re.I)
    # Enlace entre un nombre y el rótulo de su fecha («el contrato de fecha …», «albarán con fecha …»)
    ENLACES_ROTULO_FECHA = {"de", "con", "en", "y", "e", "por", "para", "a"}
    # El propio documento («Le remitimos factura con fecha …»), salvo que la frase cite otro («Rectifica la factura de fecha …»)
    RE_PROPIO_DOCUMENTO = re.compile(r"factura|fra|documento|recibo|minuta|presente", re.I)
    RE_CITA_OTRO = re.compile(r"^[sn]/|^seg[uú]n$|rectific|anul|sustitu|abon|anterior", re.I)

    def _rotulo_fecha_en_frase(self, cand: tuple) -> bool:
        """El rótulo «fecha» del candidato está dentro de una frase y fecha otra cosa: lo precede un nombre y un enlace
        («según el contrato de fecha …», «… albarán con fecha …»), o es la continuación en minúscula de la frase del renglón de
        encima («… según el contrato» ⏎ «de fecha 3 de marzo de 2024»). No si el nombre es el propio documento («Le
        remitimos factura con fecha …»), si el rótulo lleva dos puntos («Lugar y fecha:») ni si la frase habla de la emisión."""
        v, pals, pag, texto, estr = cand
        m = next((m for m in re.finditer(r"\bfecha\b", texto, re.I) if self._v_fecha(re.sub("^" + SEP, "", texto[m.end():]))),
                 None)
        if m is None or re.match(r"[^\d:]{0,30}:", texto[m.end():]) or self.RE_FRASE_DE_EMISION.search(texto[:m.start()]):
            return False
        antes = texto[:m.start()].split()
        rot, _ = self._renglon_previo(cand)
        if not antes or (len(antes) == 1 and antes[0].lower() in self.ENLACES_ROTULO_FECHA):
            # a principio de renglón en minúscula, siguiendo la frase del de encima
            return texto[(m.start() if not antes else 0)].islower() and rot is not None and self._continua_frase(rot)
        if antes[-1].lower() not in self.ENLACES_ROTULO_FECHA:
            return False
        # el nombre al que va la fecha: la última palabra antes del enlace, sin contar el número y su abreviatura («la
        # Factura nº 2026-045 de fecha …»: «Factura»)
        nombres = [p.strip(",;:()«»\"'").split("/")[-1] for p in antes[:-1]]
        nombre = next((n for n in reversed(nombres) if re.fullmatch(r"[^\W\d_]{2,}", n) and not re.search(r"[º°ª]", n)
                       and not self.RE_ABR_NUMERO.fullmatch(n) and n.lower() not in self.PREVIAS_FECHA_CITADA), None)
        if nombre is None:
            return False
        if re.fullmatch(self.OTROS_ACTOS_FECHA, nombre, re.I):
            return True  # «Albarán de fecha …», «Contrato con fecha …»
        if self.RE_PROPIO_DOCUMENTO.fullmatch(nombre):
            return any(self.RE_CITA_OTRO.search(p.strip(",;:()«»\"'")) for p in antes[:-1])
        return any(self._palabra_de_frase(p, s) for p, s in zip(antes[:-1], antes[1:]))

    def _continua_frase(self, l: Linea) -> bool:
        """El renglón sigue la frase del de encima (mismo margen izquierdo, interlineado normal, y el de encima acaba sin
        punto en una palabra en minúscula): es parte de un párrafo («… del local sito en» ⏎ «Villaficticia, 14 de octubre
        de 2026.»), no un renglón suelto de fecha de carta. Un correo o una web del membrete («info@…», «www.…») o un renglón
        corto de una dirección («Aldea Ficticia, bajo») no son una frase."""
        h = l.bottom - l.top
        encima = [self.lineas[k] for k in self._entre_tops(l.pagina, l.top - 6 * h, l.top)]
        sup = [b for b in encima if b is not l and 0 <= l.top - b.bottom <= 1.2 * h and abs(b.x0 - l.x0) <= max(3.0, 0.5 * h)]
        prev = max(sup, key=lambda b: b.bottom, default=None)
        if prev is None or re.search(r"[.:;!?]\s*$", prev.texto) or len(prev.texto.split()) < 4:
            return False  # con menos de cuatro palabras no es el renglón de un párrafo («Aldea Ficticia, bajo»)
        ult = re.sub(r"^[sn]/", "", prev.texto.split()[-1].strip(","))  # «… según s/contrato» ⏎ «del 3 de marzo …»
        return ult[:1].islower() and re.fullmatch(r"[^\W\d_]+", ult) is not None

    # Rótulo de un dato de contacto del membrete con el valor vacío («Fax:»): no presenta la fecha de debajo
    RE_ROTULO_CONTACTO = re.compile(r"^\s*(?:tel[eé]?f(?:ono)?|tfno|fax|m[oó]vil|e-?mail|correo|web)\b", re.I)

    def _fecha_de_carta_firme(self, l: Linea) -> bool:
        """Una fecha de carta que vale sin más como la del documento: no la presenta un rótulo con dos puntos en el renglón de
        encima («Recibido y conforme:», «Mandato SEPA firmado por el deudor:», «… en la finca:»: es la fecha de ese acto o un
        dato de esa frase) y, por debajo del 40 % de la página, no va en letra menor que la del texto (la letra pequeña del
        pie: protección de datos, condiciones)."""
        h = l.bottom - l.top
        sup = [b for b in (self.lineas[k] for k in self._entre_tops(l.pagina, l.top - 3 * h, l.top))
               if b is not l and b.bottom <= l.top + 0.3 * h and min(b.x1, l.x1) - max(b.x0, l.x0) > 0]
        prev = max(sup, key=lambda b: b.bottom, default=None)
        if prev is not None and prev.texto.rstrip().endswith(":") and not self.RE_ROTULO_CONTACTO.match(prev.texto):
            return False
        return l.top < 0.4 * self.alto[l.pagina] or h >= 0.85 * self._alto_renglon(l.pagina)

    def _alto_renglon(self, pagina: int) -> float:
        """Alto mediano de los renglones de la página (cada renglón cuenta una vez: la letra pequeña de un pie largo no
        rebaja la del texto)."""
        if not hasattr(self, "_altos_renglon"):
            self._altos_renglon = {}
        if pagina not in self._altos_renglon:
            altos = sorted(l.bottom - l.top for l in self.lineas if l.pagina == pagina) or [10.0]
            self._altos_renglon[pagina] = altos[len(altos) // 2]
        return self._altos_renglon[pagina]

    RE_ROTULO_FECHA = re.compile(r"\b(?:fecha|date|data)\b", re.I)

    def _rotulo_de_fecha_sin_leer(self, en_tabla: set, excl: str) -> bool:
        """Algún renglón fuera de la tabla de conceptos rotula una fecha del documento («Fecha:», «Fecha factura», «FECHA
        15-oct-26»; el rótulo al comienzo del renglón o seguido de dos puntos o de una cifra) que la búsqueda con rótulo no ha
        podido usar: su valor no se lee, o el renglón dice además otra cosa («Fecha: 15/10/2026 - Entrega en obra»). La
        fecha de carta no es entonces la única del documento. No cuentan los rótulos de otras fechas («Fecha de
        vencimiento», «Fecha valor», «Fecha y firma», «Fecha contrato»: las palabras de `excl` justo detrás)."""
        otra = re.compile(r"^\s*(?:de\s+(?:la\s+|el\s+)?|del\s+|y\s+|/\s*)?(?:" + excl + "|" + self.OTROS_ACTOS_FECHA + r")", re.I)
        for l in self.lineas:
            if id(l) in en_tabla:
                continue
            for m in itertools.islice(self.RE_ROTULO_FECHA.finditer(l.texto), MAX_ETIQUETAS_RENGLON):
                resto = l.texto[m.end():]
                if otra.match(resto):
                    continue
                if not l.texto[:m.start()].strip() or re.match(r"^[^:\d]{0,30}?[:\d]", resto):
                    return True
        return False

    # Fecha de carta: el lugar (de una a cuatro palabras con mayúscula inicial y sus partículas: «San Sebastián de los
    # Reyes», «L'Hospitalet de Llobregat»; con o sin «En» delante) y, tras la coma, solo la fecha, larga o numérica («a
    # jueves, 3 de octubre de 2026», «a 1 de noviembre de 2026»)
    PARTICULAS_LUGAR = {"de", "del", "la", "las", "los", "el", "y", "i", "e", "d'", "l'", "da", "do", "dos", "das", "en"}
    RE_DIA_SEMANA = re.compile(r"^(?:a\s+)?(?:(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s*,?\s+)?(?:a\s+)?", re.I)
    # Sellos y anotaciones con fecha que no son un lugar («Recibido, 3/10/2026», «Pagado, …», «Conforme, …», «Original, …»)
    RE_NO_LUGAR = re.compile(r"\b(?:recibid|pagad|cobrad|entregad|emitid|expedid|firmad|aprobad|revisad|registrad|contabilizad|"
                             r"vencid|enviad|impres|generad|actualizad|modificad|abonad|anulad|sellad|validad|autorizad|"
                             r"visad|cread)[oa]s?\b|\b(?:conforme|hoy|desde|hasta|v[aá]lido|entrada|salida|original|copia|duplicado|triplicado|"
                             r"ejemplar|recib[ií]|cobr[eé]|pagu[eé]|fdo|firma)\b", re.I)
    # Fecha de carta por rellenar o que no se lee: huecos («a ___ de __________ de 2026», «__/__/2026») o un dato raro en el
    # día, el mes o el año («a 31 de octubre de 2O26»)
    HUECO_FECHA = r"(?:_{2,}|\.{3,}|…+)"
    RE_FECHA_SIN_LEER = re.compile(
        r"(?:\d{1,2}|" + HUECO_FECHA + r")\s*de\s+(?:[^\W\d_]+|" + HUECO_FECHA + r")\s+(?:de|del)\s+(?:\d{2}\s*" + HUECO_FECHA +
        r"?|" + HUECO_FECHA + r"|(?=\S*\d\S*\d)\w[\w.]{1,5})|(?:\d{1,2}|" + HUECO_FECHA + r")\s*[/.-]\s*(?:\d{1,2}|" + HUECO_FECHA +
        r")\s*[/.-]\s*(?:\d{2,4}|\d{2}\s*" + HUECO_FECHA + "|" + HUECO_FECHA + ")", re.I)

    def _lugar_de_carta(self, lugar: str) -> bool:
        pals = lugar.split()
        if len(pals) > 1 and pals[0].lower() == "en":
            pals = pals[1:]  # «En Villaficticia, a 14 de octubre de 2026»
        if not pals or len(pals) > 8 or re.search(r"\d", lugar) or self.RE_ROTULO_DOCUMENTO.search(lugar) \
                or self.RE_NO_LUGAR.search(lugar):
            return False
        mayus = [p for p in pals if p[:1].isupper() and re.fullmatch(r"[^\W\d_]+(?:['’.-][^\W\d_]*)*", p)]
        return 1 <= len(mayus) <= 4 and len(mayus) + sum(p.lower() in self.PARTICULAS_LUGAR for p in pals) == len(pals) \
            and pals[0][:1].isupper()

    def _fecha_de_carta(self, texto: str) -> bool:
        lugar, coma, resto = texto.partition(",")
        if not coma or not self._lugar_de_carta(lugar):
            return False
        resto = self.RE_DIA_SEMANA.sub("", resto.strip().rstrip(".").strip())
        return any(r.fullmatch(resto) for r in (RE_FECHA_LARGA, RE_FECHA, RE_FECHA_ABR, RE_FECHA_ISO)) \
            and _fecha_iso(resto) is not None

    def _fecha_de_carta_sin_leer(self, texto: str) -> bool:
        """Renglón con forma de fecha de carta cuya fecha está en blanco o no se lee («Villaficticia, a ___ de ________ de
        2026», «Villaficticia, a 31 de octubre de 2O26»)."""
        lugar, coma, resto = texto.partition(",")
        if not coma or not self._lugar_de_carta(lugar):
            return False
        resto = self.RE_DIA_SEMANA.sub("", resto.strip().rstrip(".").strip())
        return self.RE_FECHA_SIN_LEER.fullmatch(resto) is not None and _fecha_iso(resto) is None

    def fecha_operacion(self):
        c = self.candidatos(r"fecha\s+(?:de\s+)?(?:la\s+)?(?:operaci[oó]n|prestaci[oó]n|entrega|realizaci[oó]n)", self._v_fecha)
        return self._campo_de(c[0]) if c else None

    # --- partes ---------------------------------------------------------------------------------------------------
    def _apariciones_nif(self):
        """(nif, linea, palabras) incluidos NIF con caracteres espaciados; y NIF con forma válida pero control erróneo."""
        validos, sospechosos = [], []
        self._nifs_corregidos = {}   # NIF corregido de una lectura OCR → lo leído (None: la letra O por el cero)
        for l in self.lineas:
            vistos, leidos = set(), set()
            textos = [l.texto, re.sub(r"\s+", "", l.texto)]
            n_textos = len(textos)
            if l.metodo == "ocr" and RE_NIF_ETIQUETA.search(l.texto):
                # El OCR confunde la letra O con el cero («BO…» por «B0…»): se prueba con 0 entre letras y cifras; solo cuenta si
                # el NIF resultante tiene un dígito de control válido, y se marca para confirmar
                corr = re.sub(r"(?<=[A-Z0-9])O(?=[0-9O])|(?<=[0-9])O", "0", l.texto.upper())
                if corr != l.texto.upper():
                    textos.append(corr)
                # Y otras letras y cifras parecidas («A00B…» por «A008…»): tras el rótulo, una lectura de nueve caracteres no
                # válida con una única variante válida es ese NIF, a confirmar (nunca en texto nativo)
                for m in itertools.islice(RE_NIF_ETIQUETA.finditer(l.texto), MAX_NIFS_RENGLON):
                    tok = RE_LECTURA_TRAS_ROTULO.match(l.texto, m.end())
                    n = _nif_corregido_ocr(tok.group(1)) if tok else None
                    if n and n not in vistos:
                        vistos.add(n)
                        leidos.add(nifmod.normalizar(tok.group(1)))
                        self._nifs_corregidos.setdefault(n, tok.group(1))
                        validos.append((n, l, self._palabras_rango(l, tok.start(1), tok.end(1)) or l.palabras))
            for k_txt, txt in enumerate(textos):
                for n, _, _ in _nifs_en(txt):
                    if n in vistos:
                        continue
                    if len(vistos) >= MAX_NIFS_RENGLON:
                        break  # renglón con decenas de NIF distintos (documento hostil): el resto no se examina
                    if nifmod.es_valido(n):
                        vistos.add(n)
                        if k_txt >= n_textos:
                            self._nifs_corregidos.setdefault(n, None)
                        pals = [p for p in l.palabras if re.sub(r"[\s.\-–—/]", "", p.texto.upper()) and
                                re.sub(r"[\s.\-–—/]", "", p.texto.upper()) in n or n[-4:] in p.texto.upper()]
                        validos.append((n, l, pals or l.palabras))
                    elif nifmod.tipo(n) and RE_NIF_ETIQUETA.search(l.texto) and n not in leidos:
                        vistos.add(n)
                        sospechosos.append((n, l))
            if not vistos:
                n = self._nif_incompleto(l)
                if n:
                    sospechosos.append((n, l))
        return validos, sospechosos

    # Forma de NIF incompleta o con el control fuera de rango (lectura OCR de una cifra como letra, cifra perdida):
    # «A8775421S», «A8775421», «1234567L»
    RE_NIF_INCOMPLETO = re.compile(r"(?:[A-HJ-NP-SUVW]\d{7}[0-9A-Z]?|[XYZKLM]\d{7}[0-9A-Z]?|\d{7,8}[A-Z])")
    RE_TRAS_ROTULO_NIF = re.compile(r"[\s:.\-–—]{0,6}(?:ES[\s-]?)?([A-Z0-9][A-Z0-9.\-–—/]{5,12})(?![A-Z0-9])")

    def _nif_incompleto(self, l: Linea) -> str | None:
        """NIF ilegible de un renglón sin NIF reconocido: tras el rótulo del NIF, una forma de NIF incompleta o con el control
        fuera de rango; o el renglón entero es una forma de NIF (también con el control erróneo) y sigue a la dirección de
        su bloque («41000 SEVILLA» ⏎ «A87754215»), no a un rótulo de datos del documento."""
        t = l.texto.upper()
        for m in itertools.islice(RE_NIF_ETIQUETA.finditer(t), MAX_NIFS_RENGLON):
            tok = self.RE_TRAS_ROTULO_NIF.match(t, m.end())
            n = nifmod.normalizar(tok.group(1)) if tok else ""
            if n and self.RE_NIF_INCOMPLETO.fullmatch(n) and not nifmod.es_valido(n):
                return n
        # (renglón suelto: en la cabecera de la página 1, separadores solo donde los lleva un NIF —tras la letra inicial o
        # antes del control— y una dirección de verdad encima: no un código de lote de una tabla, «C26-39915»)
        if l.pagina != 1 or l.top >= 0.45 * self.alto.get(1, 0) or len(l.palabras) > 2 \
                or not re.fullmatch(r"(?:ES[\s-]?)?[A-Z0-9][\s.-]?\d{7}[\s.-]?[0-9A-Z]?", t.strip(" .,;:")):
            return None
        n = nifmod.normalizar(t.strip(" .,;:"))
        if re.search(r"[A-Z]", n) and (nifmod.tipo(n) or self.RE_NIF_INCOMPLETO.fullmatch(n)) and not nifmod.es_valido(n):
            x = self._renglon_encima(l.top, l.pagina, l.x0, l.x1)
            if x is not None and (RE_VIA.search(x.texto) or RE_CP_CIUDAD.search(x.texto)) and not RE_CONTACTO.search(x.texto):
                return n
        return None

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
        ent = RE_ENTIDAD.search(t)
        # «COMUNIDAD DE PROPIETARIOS CALLE SOL 3»: la entidad abre el texto; la vía forma parte del nombre
        if RE_FORMA_JURIDICA.search(t) or (ent and (not RE_VIA.search(t) or (ent.start() == 0 and "," not in t
                                                                           and not re.search(r"\b\d{5}\b", t)))):
            return True
        if RE_DOMICILIO.search(t) or re.search(r"\d", t) or self._es_etiqueta(t):
            return False
        palabras = [w for w in re.split(r"\s+", t) if w]
        mayus = [w for w in palabras if w[:1].isupper()]
        return len(palabras) >= 2 and len(mayus) >= max(2, len(palabras) - 2)

    @staticmethod
    def _sin_rotulo(texto: str) -> str:
        """Quita el rótulo de parte («Cliente:», «Facturar a:», «Proveedor:») y el tratamiento («Sr. D.») del principio."""
        t = texto.strip().lstrip("/|·•-–— ")
        for _ in range(2):  # rótulo bilingüe: «Bill to / Facturar a», «From / Emisor»
            m = RE_CLIENTE.match(t) or RE_EMISOR.match(t)
            if not m:
                break
            t = t[m.end():].lstrip(" :.-–—")
            if not t.startswith("/"):
                break
            t = t.lstrip("/ ")
        return RE_CORTESIA.sub("", t).strip()

    @staticmethod
    def _pals_de(texto: str, pals: list[Palabra]) -> list[Palabra]:
        return [p for p in pals if p.texto and p.texto in texto] or pals

    def _bloque_de(self, nif_: str, l: Linea, pals: list[Palabra], barreras: frozenset | set = frozenset(),
                   cercano: bool = False) -> dict:
        """Nombre y domicilio del bloque de un NIF. `barreras`: líneas (id) que cierran el bloque hacia arriba
        (las de otro NIF o las ya asignadas a la otra parte). `cercano`: el nombre solo se busca en el tramo contiguo
        al NIF (sin saltar el hueco que lo separa de lo que hay encima). `nombre_lejano` indica un nombre tomado por
        encima de ese hueco, sin rótulo que lo ligue al NIF."""
        x0, x1 = self._ventana(l, pals)
        info = {"nif": nif_, "linea": l, "pals": pals, "nombre": None, "nombre_pals": [], "nombre_linea": None, "dom": None,
                "dom_pals": [], "x0": x0, "x1": x1, "duda_monograma": None}
        t = l.texto
        m = RE_NIF_ETIQUETA.search(t)
        antes = (t[:m.start()] if m else t.upper().split(nif_[:3])[0]).strip(" -–·|,;:([")
        nombre_antes = self._sin_rotulo(antes)  # «Cliente: DISTRIBUCIONES NORTE S.A. - CIF: …»
        if len(nombre_antes) >= 4 and self._parece_nombre(nombre_antes):
            n_antes = max(1, len(antes.split()))
            info["nombre"], info["nombre_linea"] = nombre_antes, l
            info["nombre_pals"] = self._pals_de(nombre_antes, l.palabras[:n_antes])
        if not info["nombre"]:
            # Misma fila, otro bloque con forma jurídica (p. ej. pie con «RAZÓN S.L. | C.I.F.: …»); no si ese bloque
            # encabeza la columna de otro NIF (cabecera a dos columnas con el nombre del cliente a la altura del NIF del
            # emisor: es del cliente)
            otros = [x for x in self.lineas if id(x) in barreras and x.pagina == l.pagina]
            for b in sorted(self._fila_de(l).bloques, key=lambda b: abs(b.x1 - l.x0)):
                if b is not l and id(b) not in barreras and RE_FORMA_JURIDICA.search(b.texto) and self._parece_nombre(b.texto) \
                        and not any(0 < x.top - b.top <= 60 and min(x.x1, b.x1) - max(x.x0, b.x0) > 0 for x in otros):
                    info["nombre"], info["nombre_pals"], info["nombre_linea"] = b.texto.strip(), b.palabras, b
                    break
        def solapa(x):  # el bloque pertenece a la columna del NIF (solapa o comparte alineación izquierda o derecha)
            ov = min(x.x1, x1) - max(x.x0, x0)
            return ov > 0 and (ov >= 0.3 * (x.x1 - x.x0) or abs(x.x0 - l.x0) <= 6 or abs(x.x1 - l.x1) <= 6
                               or abs((x.x0 + x.x1) / 2 - (l.x0 + l.x1) / 2) <= 15)
        # NIF solo en su renglón, alineado al borde de un recuadro, uno o dos renglones por debajo del nombre y la dirección,
        # que no comparten su alineación y solo lo solapan en parte: la columna del NIF se ensancha hasta ellos
        recuadro = self._bloque_sobre_nif_suelto(nif_, l, barreras, solapa)
        if recuadro is not None:
            x0, x1 = min(x0, recuadro[0] - 25), max(x1, recuadro[1] + 25)
            info["x0"], info["x1"] = x0, x1
        # La columna del bloque se ensancha con los renglones alineados con el del NIF (dirección, población): un nombre
        # en cuerpo grande, más ancho que el renglón del NIF, sigue siendo de esta columna
        superiores, techo = [], l.top   # techo: comienzo del tramo contiguo de renglones de la columna
        for x in sorted([x for x in self.lineas if x.pagina == l.pagina and 0 < l.top - x.top <= 110], key=lambda x: -x.top):
            if not solapa(x):
                continue
            superiores.append(x)
            if id(x) in barreras or techo - x.bottom > max(14.0, 1.6 * (x.bottom - x.top)):
                continue
            techo = x.top
            if abs(x.x0 - l.x0) <= 6:
                x1 = max(x1, x.x1 + 25)
            elif abs(x.x1 - l.x1) <= 6:
                x0 = min(x0, x.x0 - 25)
        info["bajo_otro_nif"] = any(id(x) in barreras for x in superiores)

        ascendentes = list(itertools.takewhile(lambda x: id(x) not in barreras, superiores))

        def subir(top):  # comienzo del tramo contiguo de renglones de la columna que acaba en `top`
            for x in ascendentes:
                if x.top >= top:
                    continue
                if top - x.bottom > max(14.0, 1.6 * (x.bottom - x.top)):
                    break
                top = x.top
            return top

        def encima(top):  # primer renglón y comienzo del tramo de justo encima de `top`
            sig = next((x for x in ascendentes if x.top < top - 1), None)
            return sig, (subir(sig.top) if sig is not None else None)

        def es_dir(x):
            return self._parece_direccion(x.texto) and not RE_CONTACTO.search(x.texto)

        def dirs_debajo():  # renglones de dirección del bloque del NIF por debajo de él, contiguos («CIF: …» ⏎ «C/ Valencia 5»)
            out, prev = [], l
            for _ in range(4):
                gap = max(14.0, 1.6 * (prev.bottom - prev.top))
                sig = [y for y in (self.lineas[k] for k in self._entre_tops(l.pagina, prev.top + 1, prev.bottom + gap))
                       if y.top > prev.top and solapa(y) and id(y) not in barreras]
                if not sig:
                    break
                prev = min(sig, key=lambda y: y.top)
                if es_dir(prev):
                    out.append(prev)
            return out
        # Bloque del NIF: el tramo contiguo al renglón del NIF, más (1) el tramo de encima si el del NIF no tiene dirección,
        # ni encima ni justo debajo (NIF suelto o con el teléfono, separado de su bloque por uno o dos renglones en blanco:
        # «…, 28050 Madrid» ⏎⏎ «CIF: …») y (2) un nombre suelto justo encima, separado por un renglón en blanco
        # («DISTRIBUCIONES NORTE S.A.» ⏎ «Av. Libertad 5…»). Lo que queda por encima es de otro bloque.
        alto_l = l.bottom - l.top
        techo_b = info["techo_contiguo"] = subir(l.top)
        sig, ini = encima(techo_b)
        info["dirs_debajo"] = debajo_ = dirs_debajo()
        if sig is not None and not any(es_dir(x) for x in ascendentes if x.top >= techo_b - 1) and not debajo_ \
                and techo_b - sig.bottom <= 4.5 * max(alto_l, sig.bottom - sig.top):
            techo_b = ini
            sig, ini = encima(techo_b)
        if sig is not None and techo_b - sig.bottom <= 2.5 * max(alto_l, sig.bottom - sig.top):
            bloque = [x for x in ascendentes if ini - 1 <= x.top < techo_b - 1]
            if len(bloque) <= 2 and any(self._parece_nombre(self._sin_rotulo(x.texto)) for x in bloque) and not any(
                    es_dir(x) or RE_CONTACTO.search(x.texto) or RE_NIF_ETIQUETA.search(x.texto) for x in bloque):
                techo_b = ini
        # Por encima del hueco que cierra el tramo del NIF, otra cabecera de la columna (una razón social y su dirección):
        # el bloque del NIF es el segundo de la columna, como el del cliente bajo la cabecera del emisor
        lejanas = [x for x in ascendentes if x.top < techo_b - 1]
        info["bajo_otra_cabecera"] = any(self._parece_direccion(x.texto) for x in lejanas) and any(
            self._parece_nombre(self._sin_rotulo(x.texto)) and not RE_CLIENTE.match(x.texto.strip()) for x in lejanas)
        if not info["nombre"]:
            # El bloque termina hacia arriba en la línea de otro NIF, en un rótulo con el nombre («Cliente: X S.A.»,
            # «Sr. D. …») o en el rótulo «Cliente:» solo: lo que queda encima es de la otra parte (p. ej. la cabecera del
            # emisor justo encima del bloque del cliente). Un rótulo «Emisor» solo no cierra: la razón social del emisor
            # suele ir como título encima de sus datos.
            region, rotulo, bajo_rotulo = [], [], False
            for x in superiores:
                if id(x) in barreras:
                    break
                if RE_ATENCION.match(x.texto.strip()):
                    region.append(x)
                    continue
                if RE_CLIENTE.match(x.texto.strip()) or RE_EMISOR.match(x.texto.strip()):
                    resto = self._sin_rotulo(x.texto)
                    if resto and self._parece_nombre(resto):
                        rotulo = [x]
                        break
                    if not resto and RE_CLIENTE.match(x.texto.strip()):
                        bajo_rotulo = True  # bloque rotulado «Cliente»: todo lo de debajo es suyo
                        break
                region.append(x)
            contiguas, prev = [], l
            for x in region:
                if prev.top - x.bottom > max(14.0, 1.6 * (x.bottom - x.top)) or RE_CLIENTE.match(x.texto) or RE_EMISOR.match(x.texto):
                    break
                contiguas.append(x)
                prev = x
            cerca = {id(x) for x in rotulo + contiguas + [x for x in region if x.top >= techo_b - 1]}
            if cercano:
                region = [x for x in region if id(x) in cerca]
            con_forma = [x for x in region if RE_FORMA_JURIDICA.search(x.texto) and self._parece_nombre(x.texto)]
            orden = con_forma + rotulo + contiguas[::-1] + region
            alto_tramo = sorted(x.bottom - x.top for x in [l] + [x for x in region if x.top >= techo_b - 1])

            def solo_y_titulo(x):
                """(solo, título) del nombre tomado por encima del hueco: «solo» si forma un tramo propio sin dirección,
                contacto, cifras ni NIF; «título» si además va en el cuerpo de letra del bloque del NIF y tiene texto encima en
                la página (no es el membrete que la abre): es el título del bloque del NIF, separado por renglones en blanco."""
                col = sorted(ascendentes, key=lambda y: y.top)
                i = next((k for k, y in enumerate(col) if y is x), None)
                if i is None:
                    return False, False
                tramo = [x]
                for paso in (-1, 1):
                    j, prev = i + paso, x
                    while 0 <= j < len(col) and col[j].top < techo_b - 1:
                        arr, aba = (col[j], prev) if paso < 0 else (prev, col[j])
                        if aba.top - arr.bottom > max(14.0, 1.6 * (arr.bottom - arr.top)):
                            break
                        tramo.append(col[j])
                        prev, j = col[j], j + paso
                otros = [y for y in tramo if y is not x]
                solo = len(otros) <= 1 and not any(es_dir(y) or RE_CONTACTO.search(y.texto) or RE_NIF_ETIQUETA.search(y.texto)
                                                   or re.search(r"\d", y.texto) for y in otros)
                titulo = solo and x.bottom - x.top <= 1.15 * alto_tramo[len(alto_tramo) // 2] and any(
                    y.pagina == x.pagina and y.bottom <= x.top and min(y.x1, x.x1) - max(y.x0, x.x0) > 0 for y in self.lineas)
                return solo, titulo
            if nifmod.tipo(nif_) and not nifmod.es_persona_juridica(nif_):
                # NIF de persona física: su nombre, no el comercial («Carpintería Arrabal», «Alimentación Rosendo»)
                personas = [x for x in rotulo + contiguas + region if self._parece_nombre(self._sin_rotulo(x.texto))
                            and not RE_NEGOCIO.search(x.texto) and not RE_FORMA_JURIDICA.search(x.texto)
                            and not RE_ENTIDAD.search(x.texto)]
                orden = personas + orden
            for x in orden:
                if RE_ATENCION.match(x.texto.strip()):
                    continue
                pals_x = self._sin_monograma(x.palabras)
                txt = self._sin_rotulo(" ".join(p.texto for p in pals_x))
                if self._parece_nombre(txt):
                    info["nombre"], info["nombre_pals"], info["nombre_linea"] = txt, self._pals_de(txt, pals_x), x
                    # regla geométrica sin validación amplia: lo omitido puede ser parte de la razón social
                    info["duda_monograma"] = (f"Se ha omitido «{x.palabras[0].texto}» delante de la razón social (parece un "
                                              "monograma o logotipo): compruébela") if pals_x is not x.palabras else None
                    lejano = id(x) not in cerca and not bajo_rotulo
                    solo, titulo = solo_y_titulo(x) if lejano else (False, False)
                    info["nombre_lejano"], info["lejano_solo"] = lejano and not titulo, solo
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
        if cercano and not info["nombre"]:
            # Sin nombre en el tramo del NIF: domicilio de sus renglones de dirección y, aparte, el texto que no es dirección
            # ni contacto (una razón social que no se reconoce como tal: «MERCADONIA», «TALLERES 2000»)
            tramo = sorted([x for x in self.lineas if x.pagina == l.pagina and techo_b - 1 <= x.top < l.top - 1 and solapa(x)
                            and id(x) not in barreras], key=lambda x: x.top)
            dirs = [x for x in tramo if self._parece_direccion(x.texto) and not RE_CONTACTO.search(x.texto)]
            if dirs and not info["dom"]:
                info["dom"] = ", ".join(x.texto.strip().rstrip(",") for x in dirs)
                info["dom_pals"] = [p for x in dirs for p in x.palabras]
            ids_dirs = {id(x) for x in dirs}
            # (también el texto delante del NIF en su renglón: «MERCADONIA  CIF: …»)
            textos = [x.texto.strip() for x in tramo if id(x) not in ids_dirs] + ([nombre_antes] if nombre_antes else [])
            info["texto_sin_nombre"] = next((x for x in textos if not self._es_etiqueta(x)
                                             and not RE_NIF_ETIQUETA.search(x) and "@" not in x
                                             and len(re.findall(r"\d", x)) < 6 and re.search(r"[^\W\d_]{3}", x)
                                             and not (RE_CLIENTE.match(x) or RE_EMISOR.match(x) or RE_ATENCION.match(x))), None)
        if not info["dom"]:
            info["dom"], info["dom_pals"], info["duda_dom_pie"] = self._domicilio_en_linea(nif_, l)
        if not info["dom"] and antes and (RE_VIA.search(antes) or RE_CP_CIUDAD.search(antes)) and not self._parece_nombre(antes):
            segs = [x.strip(" ,;.-–·|") for x in self.SEP_PIE.split(antes)]
            segs = [x for x in segs if x and not RE_FORMA_JURIDICA.search(x) and not RE_CONTACTO.search(x)]
            if segs:
                info["dom"], info["dom_pals"] = ", ".join(segs), l.palabras
        if not info["dom"]:
            sig = sorted([x for x in self.lineas if x.pagina == l.pagina and 0 < x.top - l.top <= 90 and solapa(x)
                          and not RE_REGISTRO_MERCANTIL.search(x.texto)], key=lambda x: x.top)
            partes, pals_d, prev = [], [], l
            calle_sin_via = lejos = confirmada = False
            for x in sig:
                if x.top - prev.bottom > 16 or self._es_etiqueta(x.texto):
                    break
                lejano = x.top - l.top > 45
                # más abajo de lo habitual, solo para completar un domicilio aún sin código postal ni población («Urb. …» ⏎
                # «LOCALIDAD (Provincia)» de un particular): tras ellos sigue otra cosa («Ref. 10234», «Póliza 12345»)
                if lejano and (not partes or any(re.search(r"\b\d{5}\b", t_) or self._es_poblacion(t_) for t_ in partes)):
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
                if lejano and not self._renglon_de_domicilio(txt_d):
                    break  # (allí solo un renglón que empieza por la vía o por el código postal y la población, o la población)
                if RE_DOMICILIO.search(txt_d) or (partes and re.search(r"\(|\b[A-ZÁÉÍÓÚ][a-záéíóú]+$", txt_d)):
                    confirmada = confirmada or self._renglon_de_domicilio(txt_d)
                    lejos = lejos or lejano
                    partes.append(txt_d)
                    pals_d += x.palabras
                    prev = x
                elif not partes and self.RE_CALLE_NUMERO.fullmatch(txt_d) and not (RE_ATENCION.match(txt_d)
                                                                                    or RE_ROTULO.match(txt_d)):
                    # calle sin tipo de vía justo bajo el NIF («Residencial Ficticio, 7B»): vale si la sigue otro renglón
                    # de domicilio (que empieza por la vía o por el código postal y la población, o «LOCALIDAD (Provincia)»)
                    calle_sin_via = True
                    partes.append(txt_d)
                    pals_d += x.palabras
                    prev = x
                else:
                    break
            if calle_sin_via and not confirmada:
                partes = []
            if partes:
                info["dom"], info["dom_pals"] = ", ".join(partes), pals_d
                # (reglas de forma, no de contenido: el domicilio así tomado siempre se pide confirmarlo)
                if (calle_sin_via or lejos) and not re.search(r"\b\d{5}\b", info["dom"]):
                    info["duda_dom_debajo"] = ("Domicilio sin código postal, tomado de los renglones de debajo del NIF: "
                                               "confírmelo y complételo")
                elif calle_sin_via:
                    info["duda_dom_debajo"] = (f"El primer renglón del domicilio, «{partes[0]}», no lleva tipo de vía: confirme "
                                               "que es la calle")
                elif lejos:
                    info["duda_dom_debajo"] = ("Domicilio completado con un renglón más abajo de lo habitual bajo el NIF: "
                                               "confírmelo")
        if info["dom"] and RE_ROTULO_DOM.sub("", info["dom"]).strip():
            info["dom"] = RE_ROTULO_DOM.sub("", info["dom"]).strip()  # «Domicilio: Av. Libertad 5, …»
        # Dos códigos postales distintos en el domicilio, o en él y en la dirección que sigue al NIF: el bloque reúne el de
        # otra parte (p. ej. la cabecera del emisor pegada al bloque del cliente, sin renglón en blanco entre ambos)
        # (una dirección rotulada, «Dirección de suministro: …», «Entrega: …», es otra dirección de la misma parte)
        cps = {c for t_ in [info["dom"] or ""] + [x.texto for x in debajo_ if not RE_ROTULO.match(x.texto)]
               for c in re.findall(r"(?<![\w-])(\d{5})\s+[^\W\d_]",
                                   next((x for x in re.split(r"[^\W\d_]{3,}\s*:", t_) if x.strip(" ,;")), ""))}
        if len(cps) >= 2:
            info["duda_bloque"] = (f"El bloque del NIF reúne dos direcciones (códigos postales {', '.join(sorted(cps))}): puede "
                                   "incluir los datos de otra parte. Confirme la razón social y el domicilio")
        return info

    @staticmethod
    def _solo_nif(nif_: str, l: Linea) -> bool:
        """El renglón no lleva más que el NIF y su rótulo («C.I.F.: A-…», «NIF B…»)."""
        t = l.texto.upper()
        for n, i, f in _nifs_en(t):
            if n == nif_:
                t = re.sub(r"(?<![A-Z0-9])ES[\s-]?$", "", t[:i]) + " " + t[f:]  # (con el prefijo «ES» del NIF-IVA)
                break
        else:
            return False
        return not re.search(r"[^\W_]", RE_NIF_ETIQUETA.sub(" ", t))

    def _bloque_sobre_nif_suelto(self, nif_: str, l: Linea, barreras, solapa) -> tuple[float, float] | None:
        """(x0, x1) del bloque de renglones al que pertenece un NIF suelto que va uno o dos renglones por debajo, alineado
        al borde del recuadro y no al margen del bloque (p. ej. «C.I.F.: …» alineado a la derecha bajo el nombre y la
        dirección, alineados a la izquierda): el renglón del NIF solo lleva el NIF, no tiene encima renglones de su propia
        columna, el bloque es un tramo contiguo de renglones con el mismo margen y sin NIF, nada se interpone entre ambos
        y el bloque y el NIF se solapan solo en parte (el NIF sobresale por un lado)."""
        if not self._solo_nif(nif_, l):
            return None
        alto = l.bottom - l.top
        sup = [self.lineas[k] for k in self._entre_tops(l.pagina, l.top - 8 * max(alto, 1.0) - 60, l.top)]
        sup = [x for x in sup if x.bottom <= l.top + 1 and x is not l]
        if any(solapa(x) and l.top - x.bottom <= 2.5 * max(alto, x.bottom - x.top) for x in sup):
            return None  # el NIF ya tiene encima renglones de su columna
        por_top = sorted(sup, key=lambda x: -x.top)
        # (los renglones más cercanos por encima: el último del bloque es uno de ellos; acotado en un documento hostil)
        for b in sorted(sup, key=lambda x: -x.bottom)[:8]:
            if l.top - b.bottom > 3 * max(alto, b.bottom - b.top):  # (unos dos renglones en blanco)
                break
            if id(b) in barreras:
                continue
            bloque, prev = [b], b
            for x in por_top:
                if x.top >= prev.top - 1 or abs(x.x0 - b.x0) > 6:
                    continue
                if prev.top - x.bottom > max(14.0, 1.6 * (x.bottom - x.top)) or len(bloque) >= 6:
                    break
                bloque.append(x)
                prev = x
            bx0, bx1 = min(x.x0 for x in bloque), max(x.x1 for x in bloque)
            parcial = min(bx1, l.x1) - max(bx0, l.x0) > 0 and (l.x1 > bx1 + alto or l.x0 < bx0 - alto)
            if not parcial or any(id(x) in barreras or next(_nifs_en(x.texto), None) or RE_NIF_ETIQUETA.search(x.texto)
                                  for x in bloque):
                continue
            # nada entre el bloque y el NIF en la franja de ambos
            ids = {id(x) for x in bloque}
            if any(id(x) not in ids and x.top > b.top and min(x.x1, max(bx1, l.x1)) - max(x.x0, min(bx0, l.x0)) > 0
                   for x in sup):
                continue
            return bx0, bx1
        return None

    @staticmethod
    def _sin_monograma(pals: list[Palabra]) -> list[Palabra]:
        """Quita un monograma o siglas de logotipo pegados delante del nombre («AB Nombre Ejemplo, S.L.»): de 1 a 4
        mayúsculas en otro cuerpo de letra o desplazadas en vertical, separadas del nombre por más de un espacio. Los
        factores geométricos salen de pocos documentos: en el destinatario, lo omitido se pide confirmar (duda_monograma)."""
        if len(pals) < 3 or not re.fullmatch(r"[A-ZÁÉÍÓÚÑ]{1,4}", pals[0].texto):
            return pals
        m, resto = pals[0], pals[1:]
        h = sorted(p.bottom - p.top for p in resto)[len(resto) // 2]
        cy = sorted((p.top + p.bottom) / 2 for p in resto)[len(resto) // 2]
        otro_cuerpo = abs((m.bottom - m.top) - h) > 0.25 * h or abs((m.top + m.bottom) / 2 - cy) > 0.3 * h
        return resto if otro_cuerpo and resto[0].x0 - m.x1 > 0.6 * h else pals

    @staticmethod
    def _parece_direccion(t: str) -> bool:
        return bool(RE_VIA.search(t) or RE_CP_CIUDAD.search(t) or re.search(r"\b\d{5}\b", t)
                    or re.search(r"^[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.'\s-]{2,40},\s*(?:n[º°]\s*)?\d{1,4}\b", t))

    # «\s+[·|•–-]\s+|\s*[·|•]\s*» sin probar cada posición dentro de una racha de espacios ((?<!\s)\s+): mismo resultado
    # y coste lineal aunque el texto traiga rachas larguísimas de espacios
    SEP_PIE = re.compile(r"(?<!\s)\s+[·|•–-]\s+|(?<!\s)\s+[·|•]\s*|[·|•]\s*")
    RE_CONTACTO_PIE = re.compile(RE_CONTACTO.pattern + r"|\btel\b", re.I)  # también «Tel. 910…», «Tel: …»
    # Población seguida de la provincia entre paréntesis («VILLAFICTICIA (Provincia)»)
    RE_LOCALIDAD_PROVINCIA = re.compile(r"[^\W\d_][^\d()]{1,40}\([^\W\d_][^\d()]{1,30}\)")
    RE_PROVINCIA = re.compile(r"\([^\W\d_][^\d()]{1,30}\)")
    # Código postal y población al comienzo del renglón («30000 VILLAPRUEBA», «30000 - Villaprueba», «C.P. 30000 …»; el
    # renglón partido por SEP_PIE llega como «30000, Villaprueba»)
    RE_CP_POBLACION = re.compile(r"(?:c\.?\s?p\.?\s*:?\s*)?\d{5}(?:\s*[-–—,]\s*|\s+)[^\W\d_]", re.I)
    # Otra dirección de la misma parte, no su domicilio («Lugar de entrega C/ …», «Dirección de envío …», «Obra …»)
    RE_OTRA_DIRECCION = re.compile(r"(?:(?:lugar|punto|direcci[oó]n|domicilio|dir\.)\s+de\s+)?(?:entrega|env[ií]o|suministro|"
                                   r"obra|recogida|carga|descarga|instalaci[oó]n)\b", re.I)

    def _es_poblacion(self, t: str) -> bool:
        """«LOCALIDAD (Provincia)» o «(Provincia)»: el renglón que cierra el domicilio de un particular sin código postal."""
        t = t.strip()
        return bool(self.RE_LOCALIDAD_PROVINCIA.fullmatch(t) or self.RE_PROVINCIA.fullmatch(t))

    def _renglon_de_domicilio(self, t: str) -> bool:
        """Renglón que por sí solo es parte de un domicilio: empieza por un tipo de vía («Urb. …», «C/ …») o por el código
        postal y la población, o es la población con la provincia; no un rótulo («Entrega: …») ni otra dirección («Lugar de
        entrega C/ …»), ni una referencia con cinco cifras («Ref. 10234», «Póliza 12345»)."""
        t = t.strip()
        if self.RE_CP_POBLACION.match(t):
            return True
        if RE_ROTULO.match(t) or self.RE_OTRA_DIRECCION.match(t):
            return False
        return bool(RE_VIA.match(t) or self._es_poblacion(t))
    # Calle sin tipo de vía y su número («Imaginaria, 58», «Pintor Ejemplo, nº 9»): solo vale seguida del código postal
    # (o, justo bajo el NIF, de otro renglón de domicilio)
    RE_CALLE_NUMERO = re.compile(r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñºª.'\s-]{2,40}?,\s*(?:n(?:\.?[º°])?\.?\s*)?\d{1,4}(?:\s?[A-Za-z]|\s?bis)?")
    # Tras la palabra clave de contacto: el resto de la palabra, su abreviatura y su compañera («Teléfono/Fax», «Tel. y
    # fax», «Correo electrónico»), sin ambigüedad en los espacios (coste lineal)
    RE_TRAS_CLAVE_CONTACTO = re.compile(r"[\w-]*\.?(?:\s*(?:/|\by\b|\belectr[oó]nico\b)[\w-]*\.?){0,3}\s*", re.I)
    RE_CORREO_WEB = re.compile(r"[^\s@]*@|www\.|https?:", re.I)
    # Rótulo de contacto al final de lo que precede a un dato de contacto («… 28001 Madrid Tel.», «… Madrid E-mail:»)
    RE_ROTULO_CONTACTO_FINAL = re.compile(r"(?:^|\s)(?:\S*:|tel[eé]?f\w*\.?|tel\.?|tfno\.?|tlf\.?|m[oó]vil|e-?mail|fax|"
                                          r"correo\s+electr[oó]nico|[/y])\s*$", re.I)

    def _corte_contacto(self, seg: str):
        """Primer rótulo o dato de contacto del trozo de pie `seg` (un match de RE_CONTACTO_PIE) y si es seguro.

        Seguro: un correo o una web, o la palabra clave que abre palabra seguida de «:», de un teléfono (nueve cifras o
        más que no acaban en código postal y población), de un correo o de una web («Tel. 910…», «E-mail: …», «Correo
        electrónico info@…»). Dudoso: la palabra clave que abre palabra sin nada de eso detrás, que puede ser parte del
        nombre de la calle («Calle Ficticia del Correo, 4», «Plaza Imaginaria de Correos 2», «Ronda de Telefonía
        Ficticia»). Dentro de una palabra («Automóvil») no cuenta. Devuelve (match seguro o None, hay uno dudoso)."""
        dudoso = False
        for x in self.RE_CONTACTO_PIE.finditer(seg):
            g = x.group(0)
            if "@" in g or g.lower() in ("www.", "http"):
                return x, dudoso
            if x.start() > 0 and seg[x.start() - 1].isalnum():
                continue  # dentro de una palabra
            resto = seg[x.end():x.end() + 80]
            sigue = resto[self.RE_TRAS_CLAVE_CONTACTO.match(resto).end():]
            if sigue.startswith(":") or self.RE_CORREO_WEB.match(sigue):
                return x, dudoso
            tel = re.match(r"\(?\+?[\d\s.()/-]+", sigue)
            if tel and sum(ch.isdigit() for ch in tel.group(0)) >= 9 and not RE_CP_CIUDAD.search(sigue[:tel.end() + 1]):
                return x, dudoso
            dudoso = True
        return None, dudoso

    # Palabras de enlace de los nombres de población («Villa Ficticia de Abajo», «Sant Imaginari del Mar», «Puebla y Villa»)
    _ENLACES_POBLACION = {"de", "del", "la", "las", "los", "el", "y", "i", "e", "les", "sa", "ses", "es", "d'", "l'", "da", "do"}

    def _sin_frase_tras_poblacion(self, t: str) -> tuple[str, bool]:
        """Quita la frase pegada sin separador tras «CP Población» («… 28001 Madrid Síguenos en», «… Madrid Atención al
        cliente»): tras el código postal, la población son palabras en mayúscula, enlaces («de», «la») y la provincia entre
        paréntesis; la primera palabra en minúscula que no es un enlace abre la frase, con la palabra en mayúscula pegada
        delante («Síguenos en»). Devuelve (texto, se ha cortado): el corte es una deducción y se pide confirmarlo."""
        cps = list(re.finditer(r"(?<![\w-])\d{5}\s+", t))
        if not cps:
            return t, False
        ini = cps[-1].end()
        toks = list(re.finditer(r"\S+", t[ini:]))
        for k, w in enumerate(toks):
            p = w.group(0).strip("(),.;-–")
            if not p or p[0].isupper() or p.lower() in self._ENLACES_POBLACION or not p[0].isalpha() \
                    or re.match(r"[dl]['’]", p, re.I):  # (artículo elidido: «Platja d'Imaginari»)
                continue
            if k == 0:
                return t, False  # (la población no puede empezar por la frase)
            corte = k - 1 if k >= 2 and toks[k - 1].group(0)[:1].isupper() else k
            cab = t[:ini + toks[corte].start()].rstrip(" ,;.-–·|")
            while True:  # (sin enlaces sueltos al final: «Puebla Inventada y»)
                u = cab.rsplit(None, 1)
                if len(u) == 2 and u[1].lower() in self._ENLACES_POBLACION:
                    cab = u[0].rstrip(" ,;.-–·|")
                    continue
                break
            return cab, True
        return t, False

    @staticmethod
    def _con_numero(t: str) -> bool:
        """Domicilio con su número o su código postal: una calle sin número («Calle Ficticia del») no es un domicilio."""
        return bool(re.search(r"\b\d{5}\b", t) or (RE_DOMICILIO.search(t) and re.search(r"\d|\bs/n\b", t, re.I)))

    def _domicilio_en_linea(self, nif_: str, l: Linea):
        """Domicilio impreso a continuación del NIF en la misma línea y, si sigue, en la línea siguiente (pies de página)."""
        t = l.texto
        fin = None
        for n, _, f in _nifs_en(t):
            if n == nif_:
                fin = f
                break
        if fin is None:
            return None, [], None
        partes, duda = [], None
        resto = [t[fin:]]
        sig = [x for x in self.lineas if x.pagina == l.pagina and 0 < x.top - l.top <= 18 and min(x.x1, l.x1) - max(x.x0, l.x0) > 0]
        if sig and re.match(r"^\s*\d{5}\b", sig[0].texto):
            resto.append(sig[0].texto)
        trozos = [[s for s in (s.strip(" ,;.-–·|") for s in self.SEP_PIE.split(r)) if s] for r in resto]
        for i, segs in enumerate(trozos):
            for k, seg in enumerate(segs):
                # (el trozo siguiente, también el primero de la línea siguiente, que empieza por el código postal)
                siguiente = segs[k + 1] if k + 1 < len(segs) else \
                    next(iter(trozos[i + 1]), None) if i + 1 < len(trozos) else None
                # el contacto que sigue a la dirección sin separador («…, 58 28001 Ciudad info@…»): vale lo de delante,
                # solo si es un domicilio por sí mismo (con número o código postal), sin el rótulo del contacto y nunca
                # como continuación corta («Síguenos en», «Contacto:», «Pedidos:» no son parte del domicilio)
                m, dudoso = self._corte_contacto(seg)
                corte = seg
                if m:
                    corte = seg[:seg.rfind(" ", 0, m.start()) + 1].strip(" ,;.-–·|")
                    for _ in range(4):
                        r_ = self.RE_ROTULO_CONTACTO_FINAL.search(corte)
                        if not r_:
                            break
                        corte = corte[:r_.start()].strip(" ,;.-–·|")
                    # (sin separador, entre la población y el contacto puede ir una frase: «Síguenos en www…»)
                    corte, recortado = self._sin_frase_tras_poblacion(corte)
                    if recortado:
                        duda = ("El domicilio del pie va seguido, sin separador, de otro texto delante del contacto: se ha "
                                "cortado tras la población. Compruebe que está completo")
                if RE_REGISTRO_MERCANTIL.search(corte):
                    break
                if m or dudoso:
                    # (con una palabra de contacto dudosa, p. ej. «Calle Ficticia del Correo, 4», el trozo vale si es un
                    # domicilio con número, o calle y número seguidos del código postal; si no, se para como con un contacto)
                    vale = bool(corte) and (self._con_numero(corte) or (
                        not m and self.RE_CALLE_NUMERO.fullmatch(corte) and siguiente and RE_CP_CIUDAD.match(siguiente)))
                    if vale:
                        partes.append(corte)
                    if m or not vale:
                        break
                    continue
                if corte and (RE_DOMICILIO.search(corte) or re.search(r"\b\d{5}\b", corte) or (partes and len(corte) <= 40)
                              # calle sin tipo de vía y número, con el código postal y la población en el trozo siguiente
                              # («Imaginaria, 58 · 28001 Ciudad»)
                              or (self.RE_CALLE_NUMERO.fullmatch(corte) and siguiente and RE_CP_CIUDAD.match(siguiente))):
                    partes.append(corte)
        if not partes:
            return None, [], None
        pals = [p for p in l.palabras if any(p.texto in x for x in partes)]
        return ", ".join(partes), pals or l.palabras, duda

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

    def _clientes_rotulados(self) -> list[str]:
        """Razones sociales rotuladas como cliente: «Cliente: X S.A.» o el rótulo solo y el nombre en el renglón de debajo."""
        out = []
        for x in self.lineas:
            t = x.texto.strip()
            if not RE_CLIENTE.match(t) or RE_ATENCION.match(t):
                continue
            m = RE_NIF_ETIQUETA.search(t)
            resto = self._sin_rotulo(t[:m.start()] if m else t).strip(" -–·|,;:")
            if resto and self._parece_nombre(resto):
                out.append(resto)
                continue
            # (con el índice por altura: un documento con miles de rótulos «Cliente» no cuesta un barrido por rótulo)
            cerca = self._entre_tops(x.pagina, x.top, x.bottom + max(14.0, 1.6 * (x.bottom - x.top)))
            debajo = sorted([y for y in (self.lineas[k] for k in cerca) if y.top > x.top
                             and min(y.x1, max(x.x1, x.x0 + 150)) - max(y.x0, x.x0 - 10) > 0], key=lambda y: y.top)
            for y in debajo[:2]:
                if RE_ATENCION.match(y.texto.strip()):
                    continue
                if self._parece_nombre(self._sin_rotulo(y.texto)):
                    out.append(self._sin_rotulo(y.texto))
                break
        return out

    def _bajo_rotulo_cliente(self, sup: list[Linea], x0: float, x1: float) -> bool:
        return any(RE_CLIENTE.match(" ".join(p.texto for p in self._palabras_en(x, x0 - 40, x1))) for x in sup)

    @staticmethod
    def _alineada(y: Linea, x0: float, x1: float) -> bool:
        """Renglón de la columna [x0, x1]: la solapa y comparte con ella la alineación izquierda, derecha o el centro."""
        return min(y.x1, x1) - max(y.x0, x0) > 0 and (abs(y.x0 - x0) <= 8 or abs(y.x1 - x1) <= 8
                                                      or abs((y.x0 + y.x1) / 2 - (x0 + x1) / 2) <= 15)

    def _renglon_encima(self, top: float, pagina: int, x0: float, x1: float) -> Linea | None:
        """Renglón de la columna justo encima de `top` (sin renglón en blanco entre medias)."""
        sup = [self.lineas[k] for k in self._entre_tops(pagina, top - 60, top)]
        sup = [y for y in sup if y.bottom <= top + 1 and top - y.bottom <= max(14.0, 1.6 * (y.bottom - y.top))
               and self._alineada(y, x0, x1)]
        return max(sup, key=lambda y: y.bottom) if sup else None

    RE_ROTULO_DOCUMENTO = re.compile(r"\b(factura|fecha|n[º°]|n[uú]mero|pedido|albar[aá]n|ref|referencia|p[aá]gina|"
                                     r"vencimiento|pago|concepto|serie|periodo|per[ií]odo|total|importe|observaciones|notas)\b", re.I)

    def _rotulo_de_otro_papel(self, b: dict) -> str | None:
        """Rótulo de otro papel en el bloque de un NIF: delante de su razón social («Promotor: GESTORA S.L.»), solo en el
        renglón de encima del bloque, alineado con él («Cesionario:» ⏎ «BANCO X S.A.»), o entre el rótulo del NIF y el NIF
        («CIF cesionario: …»). No cuentan los rótulos de datos del documento («Nº factura:», «Fecha:»)."""
        l = b["linea"]
        rot = _rotulo_de_papel(b["nombre"] or "") or _calificador_nif(l.texto, b["nif"])
        if not rot:
            ln = b["nombre_linea"] if b["nombre_linea"] is not None and b["nombre_linea"].pagina == l.pagina else l
            x = self._renglon_encima(min(ln.top, l.top), l.pagina, min(ln.x0, l.x0), max(ln.x1, l.x1))
            rot = _rotulo_de_papel(x.texto, solo=True) if x is not None else None
        return rot if rot and not self.RE_ROTULO_DOCUMENTO.search(rot) else None

    def _bloque_de_otra_parte(self, x: Linea, emisor: dict, destinatario: dict | None) -> str | None:
        """Motivo por el que el renglón `x` (una dirección de la cabecera) parece del bloque de otra parte y no del emisor:
        el bloque (renglones contiguos de su columna, por encima y por debajo) lleva un NIF distinto del del emisor, va
        rotulado como cliente, o lo encabeza una razón social que no es la del emisor (la del destinatario o, con forma
        jurídica o de entidad, otra; o un nombre cualquiera si el destinatario no tiene su propio bloque completo). La
        razón social del emisor solo cuenta como suya si es segura: la leída de la cabecera cuando el destinatario no tiene
        nombre puede ser la del propio recuadro del destinatario. Y si el NIF del destinatario figura sin razón social ni
        domicilio, cualquier bloque que no encabece la razón social segura del emisor (ni vaya rotulado como del emisor)
        puede ser el suyo, aunque no lleve nombre («AUTOCARES 33», solo la calle y la población)."""
        bloque, y = [x], x
        for _ in range(5):  # hacia arriba, hasta el título del bloque
            y = self._renglon_encima(y.top, y.pagina, x.x0, x.x1)
            if y is None or any(y is z for z in bloque):
                break
            bloque.insert(0, y)
        prev = x
        for _ in range(4):  # hacia abajo (el NIF al pie del bloque)
            gap = max(14.0, 1.6 * (prev.bottom - prev.top))
            sig = [self.lineas[k] for k in self._entre_tops(x.pagina, prev.top + 1, prev.bottom + gap)]
            sig = [z for z in sig if z.top > prev.top and self._alineada(z, x.x0, x.x1)]
            if not sig:
                break
            prev = min(sig, key=lambda z: z.top)
            bloque.append(prev)
        for z in bloque:
            otro = next((n for n, _, _ in _nifs_en(z.texto) if nifmod.es_valido(n) and n != emisor["nif"]), None)
            if otro:
                return f"con el NIF {otro}"
        cabeza = bloque[0]
        sobre = self._renglon_encima(cabeza.top, cabeza.pagina, cabeza.x0, cabeza.x1)
        encabezado = [cabeza] + ([sobre] if sobre is not None else [])
        if any(RE_CLIENTE.match(z.texto.strip()) for z in encabezado):
            return "rotulado como cliente"
        # razón social del emisor segura: la de junto a su NIF o la de la cabecera no puesta en duda
        nombre_emisor = emisor["nombre"] if not emisor.get("duda_nombre_cabecera") else None
        # el destinatario tiene su propio bloque completo (razón social y domicilio junto a su NIF, no deducidos)
        dest_completo = bool(destinatario and destinatario["nombre"] and destinatario["dom"]
                             and not destinatario.get("duda_nombre"))
        propio = any(RE_EMISOR.match(z.texto.strip()) for z in encabezado)
        for z in itertools.takewhile(lambda z: z is not x, bloque):  # los renglones de encima de la dirección
            t = self._sin_rotulo(z.texto)
            if not self._parece_nombre(t) or self.RE_ROTULO_DOCUMENTO.search(t):
                continue
            if nombre_emisor and _mismo_nombre(t, nombre_emisor):
                propio = True
                continue
            if emisor["nombre"] and _mismo_nombre(t, emisor["nombre"]):  # (la de la cabecera puesta en duda)
                return f"«{t}», que puede ser la razón social del destinatario"
            # (un nombre cualquiera si se conoce la razón social del emisor y el destinatario no tiene su propio bloque
            # completo; si no, solo con forma jurídica o de entidad: un nombre comercial o un lema en el membrete no es otra
            # parte)
            if (destinatario and _mismo_nombre(t, destinatario["nombre"])) or RE_FORMA_JURIDICA.search(t) \
                    or RE_ENTIDAD.search(t) or (emisor["nombre"] and not dest_completo):
                return f"«{t}»"
        if destinatario and not destinatario["nombre"] and not destinatario["dom"] and not propio:
            return "el NIF del destinatario figura sin razón social ni domicilio y este bloque puede ser el suyo"
        return None

    def _bloque_sin_nif(self, tope: float, partes_: list[dict]) -> str | None:
        """Razón social de un bloque de la cabecera (página 1, encima de `tope`) con su dirección y sin NIF, sin rótulo y que
        no es de ninguna de las partes ni el membrete que abre la página: puede ser el del cliente, con el NIF omitido."""
        return next((c["nombre"] for c in self._bloques_sin_nif(tope, partes_)), None)

    def _bloques_sin_nif(self, tope: float, partes_: list[dict], emisor: dict | None = None):
        """Bloques de la cabecera (página 1, encima de `tope`) con razón social y dirección y sin NIF, sin rótulo, que no son
        de ninguna de las partes: {nombre, linea, dirs}. Sin `emisor`, tampoco el que abre la página o su columna (suele ser
        el membrete del emisor). Con `emisor` (con su razón social leída en otro sitio, p. ej. el pie), el que abre la página
        también vale si va en la mitad derecha y su razón social no es la del emisor: el membrete es una imagen y el primer
        texto es el recuadro del cliente."""
        cab = sorted([x for x in self.lineas if x.pagina == 1 and x.top < tope], key=lambda x: x.top)
        nombres = [b["nombre"] for b in partes_ if b["nombre"]]
        zonas = []  # extensión vertical y ventana de los bloques de las partes en la página 1
        for b in partes_:
            l, ln = b["linea"], b["nombre_linea"]
            if l.pagina == 1:
                arriba = min(l.top, ln.top) if ln is not None and ln.pagina == 1 else l.top
                zonas.append((arriba - 1, l.bottom + 1, b["x0"], b["x1"]))
        examinados = 0
        for x in cab:
            t = self._sin_rotulo(x.texto)
            if not (RE_FORMA_JURIDICA.search(t) or RE_ENTIDAD.match(t)) or RE_CLIENTE.match(x.texto.strip()) \
                    or not self._parece_nombre(t) or any(_mismo_nombre(t, n) for n in nombres) or next(_nifs_en(x.texto), None):
                continue
            # abre la página o su columna: el membrete (salvo, con `emisor`, en la mitad derecha y con otra razón social)
            ajeno = emisor is not None and bool(emisor.get("nombre")) and (x.x0 + x.x1) / 2 > 0.5 * self.ancho.get(1, 0)
            if x.top <= cab[0].top + 15 and not ajeno:
                continue
            examinados += 1
            if examinados > MAX_BLOQUES_SIN_NIF:
                break
            if any(a <= x.top <= z and min(x.x1, x1) - max(x.x0, x0) > 0 for a, z, x0, x1 in zonas):
                continue
            if not ajeno and not any(y.bottom <= x.top and min(y.x1, x.x1) - max(y.x0, x.x0) > 0 for y in cab):
                continue  # abre su columna: el membrete
            encima = self._renglon_encima(x.top, 1, x.x0, x.x1)
            if encima is not None and (RE_ROTULO.match(encima.texto) or next(_nifs_en(encima.texto), None)):
                continue  # bloque rotulado (cliente, entrega, otro papel) o con NIF
            bloque, prev = [], x
            for _ in range(4):
                gap = max(14.0, 1.6 * (prev.bottom - prev.top))
                sig = [self.lineas[k] for k in self._entre_tops(1, prev.top + 1, prev.bottom + gap)]
                sig = [y for y in sig if y.top > prev.top and self._alineada(y, x.x0, x.x1)]
                if not sig:
                    break
                prev = min(sig, key=lambda y: y.top)
                bloque.append(prev)
            if any(next(_nifs_en(y.texto), None) or RE_NIF_ETIQUETA.search(y.texto) for y in bloque):
                continue
            if any(self._parece_direccion(y.texto) and not RE_CONTACTO.search(y.texto) for y in bloque):
                yield {"nombre": t, "linea": x, "dirs": [y for y in bloque if self._parece_direccion(y.texto)
                                                         and not RE_CONTACTO.search(y.texto) and not RE_ATENCION.match(y.texto.strip())]}

    def partes(self):
        validos, sospechosos = self._apariciones_nif()
        # Líneas con cada NIF: el bloque de un NIF no sube más allá de la línea de otro NIF de la misma columna
        nifs_linea: dict[int, set] = {}
        for n, l, _ in validos:
            nifs_linea.setdefault(id(l), set()).add(n)

        def barreras(n):
            return {i for i, ns in nifs_linea.items() if ns - {n}}
        bloques = []
        # Las apariciones repetidas de un mismo NIF solo suman indicios: se examinan las primeras de cada NIF y un número
        # total acotado (un documento con miles de NIF no debe costar minutos)
        cuenta: dict[str, int] = {}
        examinar = []
        for v in validos:
            cuenta[v[0]] = cuenta.get(v[0], 0) + 1
            if cuenta[v[0]] <= MAX_APARICIONES_POR_NIF and len(examinar) < MAX_APARICIONES_NIF:
                examinar.append(v)
        for n, l, pals in examinar:
            b = self._bloque_de(n, l, pals, barreras(n))
            alto = self.alto[l.pagina]
            em, de, motivos = 0.0, 0.0, []
            if l.top > 0.75 * alto:
                em += 3; motivos.append("pie de página")
            vecinas = [x for x in self.lineas if x.pagina == l.pagina and abs(x.top - l.top) < 40]
            if any(RE_REGISTRO_MERCANTIL.search(x.texto) for x in vecinas):
                em += 2; motivos.append("junto a datos registrales")
            sup = [x for x in self.lineas if x.pagina == l.pagina and 0 <= l.top - x.top <= 110
                   and min(x.x1, b["x1"]) - max(x.x0, b["x0"]) > 0]
            if self._bajo_rotulo_cliente(sup, b["x0"], b["x1"]):
                de += 4; motivos.append("bloque de cliente")
            # el rótulo «Cliente» encabeza el tramo contiguo de renglones que llega hasta el NIF (sin huecos)
            b["rotulo_contiguo"] = self._bajo_rotulo_cliente([x for x in sup if x.top >= b["techo_contiguo"] - 1], b["x0"], b["x1"])
            if any(RE_EMISOR.match(x.texto) for x in sup):
                em += 3; motivos.append("bloque de emisor")
            em_propio = em  # indicios del propio documento, sin contar el NIF configurado
            if self.nif_empresa and n == self.nif_empresa:
                em += 2.5; motivos.append("NIF de la empresa configurada")
            if l.top < 0.4 * alto and (b["bajo_otro_nif"] or b["bajo_otra_cabecera"]) and "bloque de cliente" not in motivos:
                # segundo bloque de la columna, justo debajo del de otro NIF o de otra cabecera con razón social y dirección
                # (cabecera del emisor y, debajo, el cliente)
                de += 0.6; motivos.append("debajo del bloque de otro NIF" if b["bajo_otro_nif"] else "debajo de otra cabecera")
            elif l.top < 0.3 * alto and "bloque de cliente" not in motivos:
                ancho, centro = self.ancho[l.pagina], (b["x0"] + b["x1"]) / 2
                if abs(centro - ancho / 2) <= 0.05 * ancho and b["x1"] - b["x0"] >= 0.4 * ancho:
                    em += 0.6; em_propio += 0.6; motivos.append("membrete centrado")
                elif centro < ancho * 0.45:
                    em += 0.6; em_propio += 0.6; motivos.append("cabecera a la izquierda")
                else:
                    de += 0.6; motivos.append("cabecera a la derecha")
            b.update(em=em, de=de, em_propio=em_propio, motivos=motivos)
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
                ant["em_propio"] += b["em_propio"] * 0.5
                ant["motivos"] = sorted(set(ant["motivos"] + b["motivos"]))
                if not ant["nombre"] and b["nombre"]:
                    ant.update(nombre=b["nombre"], nombre_pals=b["nombre_pals"], nombre_linea=b["nombre_linea"],
                               duda_monograma=b.get("duda_monograma"))
                if not ant["dom"] and b["dom"]:
                    ant.update(dom=b["dom"], dom_pals=b["dom_pals"], duda_dom_pie=b.get("duda_dom_pie"),
                               duda_dom_debajo=b.get("duda_dom_debajo"))
                ant["rotulo_contiguo"] = ant["rotulo_contiguo"] or b["rotulo_contiguo"]
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
        # NIF con forma válida y control erróneo que no es una mala lectura de un NIF detectado (o del configurado):
        # puede ser el del destinatario, y el destinatario elegido, un tercero citado en el documento
        conocidos = sorted(set(por_nif) | ({self.nif_empresa} if self.nif_empresa else set()))
        ilegibles = [n for n, _ in sospechosos if not any(nifmod.parecidos(n, v) for v in conocidos)]
        # NIF ilegible en el bloque rotulado del cliente y destinatario elegido sin ese indicio: el destinatario es el
        # bloque del cliente con el NIF vacío (se pide escribirlo), no un tercero citado en otra parte del documento
        if not (destinatario and "bloque de cliente" in destinatario["motivos"]):
            for n, l in sospechosos:
                if n not in ilegibles:
                    continue
                pals = [p for p in l.palabras if n[-4:] in p.texto.upper()] or l.palabras
                b = self._bloque_de(n, l, pals, barreras(n))
                sup = [x for x in self.lineas if x.pagina == l.pagina and 0 <= l.top - x.top <= 110
                       and min(x.x1, b["x1"]) - max(x.x0, b["x0"]) > 0]
                if self._bajo_rotulo_cliente(sup, b["x0"], b["x1"]):
                    b.update(em=0.0, de=4.0, em_propio=0.0, motivos=["bloque de cliente"], ilegible=n)
                    destinatario = b
                    break
        # Destinatario con el nombre tomado por encima del hueco que separa su bloque de lo que tiene encima, sin rótulo que
        # lo ligue a su NIF: puede ser la cabecera del emisor (nombre comercial, NIF del emisor solo en el pie). Vale el
        # nombre del bloque de su NIF; si no lo hay, el domicilio se toma de ese bloque y el nombre leído se deja vacío
        # (otra cabecera completa encima y un texto propio que no se reconoce como nombre) o se pide confirmarlo
        if destinatario and destinatario.get("nombre_lejano"):
            nuevo = self._bloque_de(destinatario["nif"], destinatario["linea"], destinatario["pals"],
                                    barreras(destinatario["nif"]), cercano=True)
            lejano, cerca_ = destinatario["nombre"], nuevo["nombre"]
            if cerca_ and destinatario.get("lejano_solo") and not _mismo_nombre(lejano, cerca_):
                # El de encima es un nombre suelto (sin dirección propia) en otro cuerpo de letra o abriendo la página: puede
                # ser el membrete del emisor o el título del bloque del NIF. Se pide confirmar; vale el de encima si tiene
                # forma jurídica y el NIF es de una sociedad, y el del bloque no parece una razón social (persona de contacto,
                # delegación)
                if not (RE_FORMA_JURIDICA.search(lejano) and nifmod.es_persona_juridica(destinatario["nif"])
                        and not (RE_FORMA_JURIDICA.search(cerca_) or RE_NEGOCIO.search(cerca_) or RE_ENTIDAD.search(cerca_))):
                    destinatario.update({k: nuevo.get(k) for k in ("nombre", "nombre_pals", "nombre_linea", "nombre_lejano",
                                                                   "dom", "dom_pals", "x0", "x1", "duda_monograma", "duda_bloque",
                                                                   "duda_dom_pie", "duda_dom_debajo")})
                destinatario["duda_nombre"] = (f"Sobre el NIF del destinatario figuran «{cerca_}» y, encima, separado por un "
                                               f"hueco, «{lejano}»: confirme cuál es su razón social")
            elif cerca_:
                destinatario.update({k: nuevo.get(k) for k in ("nombre", "nombre_pals", "nombre_linea", "nombre_lejano", "dom",
                                                               "dom_pals", "x0", "x1", "duda_monograma", "duda_bloque",
                                                               "duda_dom_pie", "duda_dom_debajo")})
            else:
                duda = ("Leído por encima del bloque del NIF del destinatario, separado de él: puede ser de otra parte (p. ej. "
                        "el emisor). Confírmelo")
                if nuevo["dom"]:
                    destinatario.update(dom=nuevo["dom"], dom_pals=nuevo["dom_pals"], duda_bloque=nuevo.get("duda_bloque"),
                                        duda_dom_pie=nuevo.get("duda_dom_pie"), duda_dom_debajo=nuevo.get("duda_dom_debajo"))
                else:
                    destinatario["duda_dom"] = duda
                propio = nuevo.get("texto_sin_nombre")
                if destinatario.get("bajo_otra_cabecera") and propio and ":" not in propio and len(propio.split()) <= 8 \
                        and all(w[:1].isupper() or w[:1].isdigit() or w.lower() in ("de", "del", "la", "las", "los", "y", "e")
                                for w in propio.split()):
                    # el bloque empieza por su propio nombre, aunque no se reconozca como tal («MERCADONIA», «TALLERES 2000»),
                    # y encima del hueco hay otra cabecera completa (razón social y dirección): el nombre es de esa otra parte
                    destinatario.update(nombre=None, nombre_pals=[], nombre_linea=None, aviso_sin_nombre=(
                        f"El bloque del NIF del destinatario empieza por «{propio}», que no se reconoce como razón social; "
                        "la de encima es de otra cabecera. Escriba la razón social del destinatario"))
                else:
                    destinatario["duda_nombre"] = duda
        # Una misma razón social no puede ser de las dos partes. Si ambas la toman del mismo renglón y este cae en la columna
        # de una sola de ellas, es de esa; si no, de la parte cuyo NIF está más cerca. La otra se recalcula sin subir hasta
        # esa línea y, si no aparece otro nombre, se deja vacía
        if emisor and destinatario and emisor["nombre"] and destinatario["nombre"] \
                and _clave(emisor["nombre"]) == _clave(destinatario["nombre"]):
            def distancia(b):
                ln = b["nombre_linea"]
                return b["linea"].top - ln.top if ln is not None and ln.pagina == b["linea"].pagina else float("inf")

            def en_columna(b):
                ln, l = b["nombre_linea"], b["linea"]
                return ln is not None and ln.pagina == l.pagina and min(ln.x1, l.x1) - max(ln.x0, l.x0) > 0
            if emisor["nombre_linea"] is not None and emisor["nombre_linea"] is destinatario["nombre_linea"] \
                    and en_columna(emisor) != en_columna(destinatario):
                perdedor = destinatario if en_columna(emisor) else emisor
            else:
                perdedor = destinatario if distancia(destinatario) >= distancia(emisor) else emisor
            ln = perdedor["nombre_linea"]
            nuevo = self._bloque_de(perdedor["nif"], perdedor["linea"], perdedor["pals"],
                                    barreras(perdedor["nif"]) | ({id(ln)} if ln is not None else set()))
            if nuevo["nombre"] and _clave(nuevo["nombre"]) == _clave(perdedor["nombre"]):
                nuevo.update(nombre=None, nombre_pals=[], nombre_linea=None)
            perdedor.update({k: nuevo.get(k) for k in ("nombre", "nombre_pals", "nombre_linea", "dom", "dom_pals",
                                                       "duda_monograma", "duda_bloque", "duda_dom_pie", "duda_dom_debajo")})
        # Cabecera del documento: página 1, encima de la tabla de conceptos (y del 40 % superior de la página)
        self.tabla()
        tope = min([f.top for f in self.filas if f.pagina == 1 and id(f) in self._ids_filas_tabla]
                   + [0.4 * self.alto.get(1, 0)])
        # NIF del destinatario sin nombre ni domicilio en su bloque (p. ej. suelto bajo el título de la factura) y, en la
        # cabecera, un único bloque sin rótulo ni NIF con razón social y dirección que no es el del emisor (con su razón
        # social leída junto a su NIF, p. ej. en el pie): es el del destinatario, pero el vínculo es una deducción y se pide
        # confirmarlo
        if destinatario and emisor and emisor["nombre"] and not destinatario["nombre"] and not destinatario["dom"] \
                and not destinatario.get("ilegible"):
            sin_nif = list(itertools.islice(self._bloques_sin_nif(tope, [emisor, destinatario], emisor=emisor), 2))
            if len(sin_nif) == 1:
                c = sin_nif[0]
                duda = (f"Razón social y domicilio tomados del bloque de la cabecera «{c['nombre']}», sin NIF: el NIF del "
                        "destinatario figura aparte y se ha supuesto que es de ese bloque. Confírmelo")
                destinatario.update(nombre=c["nombre"], nombre_pals=self._pals_de(c["nombre"], c["linea"].palabras),
                                    nombre_linea=c["linea"], duda_nombre=duda, duda_dom=duda,
                                    x0=min(destinatario["x0"], c["linea"].x0 - 10, *(y.x0 - 10 for y in c["dirs"])),
                                    x1=max(destinatario["x1"], c["linea"].x1 + 10, *(y.x1 + 10 for y in c["dirs"])))
                if c["dirs"]:
                    destinatario.update(dom=", ".join(y.texto.strip().rstrip(",") for y in c["dirs"]),
                                        dom_pals=[p for y in c["dirs"] for p in y.palabras])
        # Razón social del emisor si no está junto a su NIF (NIF solo en la columna de los datos de la factura o en el pie,
        # sin nombre): la primera de la cabecera, con forma jurídica o de entidad, fuera del bloque del destinatario
        if emisor and not emisor["nombre"] and not emisor.get("ilegible"):
            desde = None
            if destinatario:
                ln = destinatario["nombre_linea"]
                desde = min(destinatario["linea"].top, ln.top if ln is not None and ln.pagina == destinatario["linea"].pagina
                            else destinatario["linea"].top)
            for x in sorted([x for x in self.lineas if x.pagina == 1 and x.top < 0.35 * self.alto[1]], key=lambda x: x.top):
                t = x.texto.strip()
                if RE_CLIENTE.match(t) or RE_ATENCION.match(t):
                    continue
                txt = self._sin_rotulo(t)
                if not (RE_FORMA_JURIDICA.search(txt) or RE_ENTIDAD.match(txt)) or not self._parece_nombre(txt):
                    continue
                if destinatario and (_mismo_nombre(txt, destinatario["nombre"]) or (
                        destinatario["linea"].pagina == 1 and desde - 1 <= x.top <= destinatario["linea"].bottom
                        and min(x.x1, destinatario["x1"]) - max(x.x0, destinatario["x0"]) > 0)):
                    continue  # del bloque del destinatario
                emisor.update(nombre=txt, nombre_pals=self._pals_de(txt, x.palabras), nombre_linea=None,
                              aviso_nombre="Razón social tomada de la cabecera del documento: no figura junto al NIF del emisor")
                if destinatario and not destinatario["nombre"]:
                    # el NIF del destinatario tampoco lleva razón social: el bloque de la cabecera puede ser el suyo (su
                    # recuadro de dirección) tanto como el membrete del emisor
                    emisor["duda_nombre_cabecera"] = (
                        f"Ni el NIF del emisor ni el del destinatario figuran junto a una razón social: «{txt}», tomada de la "
                        "cabecera, puede ser la del destinatario. Confirme la razón social del emisor")
                break
        # Domicilio del emisor si no está junto a su NIF: líneas de dirección de la cabecera fuera del bloque del cliente.
        # Si salen de un bloque que parece de otra parte (con otro NIF, rotulado como cliente o encabezado por otra razón
        # social), no se dan por seguras: el emisor no imprime su dirección junto a su NIF y la de la cabecera puede ser la
        # del recuadro del cliente
        if emisor and not emisor["dom"]:
            cab = [x for x in self.lineas if x.pagina == 1 and x.top < 0.35 * self.alto[1]]
            partes, pals, ajenos = [], [], []
            de_dest = {id(p) for p in (destinatario["dom_pals"] + destinatario["nombre_pals"] if destinatario else [])}
            for x in cab:
                if destinatario and (min(x.x1, destinatario["x1"]) - max(x.x0, destinatario["x0"]) > 0.3 * (x.x1 - x.x0)
                                     or any(id(p) in de_dest for p in x.palabras)):
                    continue  # pertenece al bloque del destinatario
                txt = x.texto.strip()
                etiqueta = re.match(r"^([^\d:]{2,30}?)(?:\.\s*[-–]|:)\s", txt)
                if etiqueta and not re.search(r"domicilio|direcci[oó]n|sede|oficinas?", etiqueta.group(1), re.I):
                    continue
                if txt and (RE_VIA.search(txt) or RE_CP_CIUDAD.search(txt)) and not RE_CONTACTO.search(txt) and not RE_NIF_ETIQUETA.search(txt) \
                        and not RE_FORMA_JURIDICA.search(txt) and not _fecha_iso(txt):
                    partes.append(txt.rstrip(","))
                    pals += x.palabras
                    ajeno = self._bloque_de_otra_parte(x, emisor, destinatario)
                    if ajeno:
                        ajenos.append(ajeno)
            if partes:
                emisor["dom"], emisor["dom_pals"] = ", ".join(partes), pals
                if ajenos:
                    emisor["duda_dom_cabecera"] = (
                        f"El domicilio del emisor no figura junto a su NIF y el de la cabecera sale de un bloque que parece de "
                        f"otra parte ({ajenos[0]}): compruebe que es el del emisor y no el del destinatario")
        if emisor and emisor["nombre"] and any(p.metodo == "ocr" for p in emisor["nombre_pals"]):
            self._contrastar_nombre_ocr(emisor)
        def extension(b):  # (página, arriba, abajo) del bloque: su NIF, su nombre y su domicilio
            l = b["linea"]
            ps = list(l.palabras) + [p for p in b["dom_pals"] if abs(p.top - l.top) <= 120]
            if b["nombre_linea"] is not None and b["nombre_linea"].pagina == l.pagina:
                ps += b["nombre_pals"]
            return l.pagina, min(p.top for p in ps), max(p.bottom for p in ps)
        # La empresa configurada como emisor, pero la maquetación favorece al otro NIF: p. ej. factura de proveedor con el
        # proveedor en la cabecera y la empresa en un bloque sin rótulo «Cliente». Tres indicios, cada uno a favor de una
        # parte o neutro: (1) la posición (cabecera a la izquierda o centrada frente a la derecha, debajo del otro bloque);
        # (2) el bloque que va entero por encima del otro (lado a lado no cuenta); (3) el cuerpo de letra: sin rótulos, la
        # razón social del emisor suele ir destacada sobre la del cliente (texto del PDF, no OCR). Hay contradicción si, en
        # conjunto, favorecen al otro NIF y la empresa no tiene indicios propios fuertes (rótulo, pie, datos registrales)
        def alto_nombre(b):
            ln = b["nombre_linea"]
            ps = [p for p in b["nombre_pals"] if p.metodo != "ocr"] if b["nombre"] and ln is not None else []
            if not ps or ln.pagina != 1 or ln.top > 0.4 * self.alto.get(1, 0):
                return None
            return sorted(p.bottom - p.top for p in ps)[len(ps) // 2]
        destaca_otro = destaca_propio = False
        if emisor and destinatario and alto_nombre(emisor) and alto_nombre(destinatario):
            destaca_otro = alto_nombre(destinatario) >= 1.15 * alto_nombre(emisor)
            destaca_propio = alto_nombre(emisor) >= 1.15 * alto_nombre(destinatario)
        contradice = False
        if emisor and destinatario and self.nif_empresa and emisor["nif"] == self.nif_empresa \
                and "bloque de cliente" not in destinatario["motivos"]:
            dif = (destinatario["em_propio"] - destinatario["de"]) - (emisor["em_propio"] - emisor["de"])
            (pe, ae, be), (po, ao, bo) = extension(emisor), extension(destinatario)
            encima = 1 if (po, bo) <= (pe, ae + 2) else -1 if (pe, be) <= (po, ao + 2) else 0
            contradice = emisor["em_propio"] - emisor["de"] < 1 and (dif > 0) - (dif < 0) + encima + destaca_otro - destaca_propio >= 1
        # El NIF elegido puede ser de un tercero citado (cesionario de un factoring, promotor, mediador) y el del cliente,
        # faltar: (a) un rótulo de otro papel delante de su razón social o en el renglón de encima de su bloque
        # («Promotor:», «Cesionario:»), o junto al rótulo de su NIF («CIF cesionario:»); (b) el documento rotula como cliente
        # («Cliente: X S.A.») a otro, que no encabeza el bloque de ese NIF; (c) su razón social va dentro de una frase
        # («Crédito cedido a BANCO X S.A.»); (d) sin rótulo de cliente, la cabecera tiene otro bloque con razón social y
        # dirección y sin NIF, que puede ser el del cliente
        tercero = None
        if destinatario and not destinatario.get("ilegible") and not (self.nif_empresa and destinatario["nif"] == self.nif_empresa):
            rot = self._rotulo_de_otro_papel(destinatario)
            if rot:
                tercero = (f"El bloque del destinatario va rotulado «{rot}»: parece un tercero citado en el documento "
                           "(cesionario, promotor…), no el cliente. Confirme el destinatario")
            if not tercero and not destinatario.get("rotulo_contiguo"):
                rotulados = [r for r in self._clientes_rotulados() if not (emisor and _mismo_nombre(r, emisor["nombre"]))]
                if rotulados and not any(_mismo_nombre(r, destinatario["nombre"]) for r in rotulados):
                    tercero = (f"El documento rotula como cliente a «{rotulados[0]}», que no figura junto a este NIF: "
                               "confirme el destinatario")
            # (la expresión es cúbica en la longitud: una razón social real no pasa de MAX_LARGO_NOMBRE caracteres)
            nom = destinatario["nombre"] or ""
            m = RE_MENCION_TERCERO.match(nom) if len(nom) <= MAX_LARGO_NOMBRE else None
            if not tercero and m and RE_FORMA_JURIDICA.search(m["nombre"]) and _intro_de_frase(m["intro"]):
                tercero = (f"La razón social del destinatario va precedida de «{m['intro']}»: parece un tercero citado en el "
                           "documento (cesionario, promotor…), no el cliente. Confirme el destinatario")
            if not tercero and "bloque de cliente" not in destinatario["motivos"] and not destinatario.get("rotulo_contiguo"):
                otro = self._bloque_sin_nif(tope, [b for b in (emisor, destinatario) if b])
                if otro:
                    tercero = (f"La cabecera tiene otro bloque con razón social y dirección y sin NIF («{otro}»): puede ser el "
                               "del cliente y este NIF, el de un tercero. Confirme el destinatario")
            if tercero:
                destinatario["duda_nombre"] = destinatario["duda_dom"] = tercero
        out = {}
        for rol, b in (("emisor", emisor), ("destinatario", destinatario)):
            if not b:
                out[rol] = {"nombre": _vacio(f"No se identifica el {rol}"), "nif": _vacio(f"NIF del {rol} no encontrado"),
                            "domicilio": _vacio()}
                continue
            l = b["linea"]
            nombre_dom = {
                "nombre": _campo(b["nombre"], texto=b["nombre"], palabras=b["nombre_pals"], pagina=l.pagina) if b["nombre"]
                else _vacio(b.get("aviso_sin_nombre") or "Razón social no encontrada"),
                "domicilio": _campo(b["dom"], texto=b["dom"], palabras=b["dom_pals"], pagina=l.pagina) if b["dom"]
                else _vacio("Domicilio no encontrado"),
            }
            if b.get("aviso_nombre") and nombre_dom["nombre"].get("valor"):
                nombre_dom["nombre"]["avisos"].append(b["aviso_nombre"])
            if b.get("duda_nombre_cabecera") and nombre_dom["nombre"].get("valor"):
                nombre_dom["nombre"]["confianza"] = min(nombre_dom["nombre"]["confianza"], 0.6)
                nombre_dom["nombre"]["avisos"].append(b["duda_nombre_cabecera"])
            for k, duda in (("nombre", b.get("duda_nombre") or b.get("duda_monograma") or b.get("duda_bloque")),
                            ("domicilio", b.get("duda_dom") or b.get("duda_bloque"))):
                if rol == "destinatario" and duda and nombre_dom[k].get("valor"):
                    nombre_dom[k]["confianza"] = min(nombre_dom[k]["confianza"], 0.75)
                    nombre_dom[k]["avisos"].append(duda)
            if b.get("duda_dom_pie") and b["dom"] and nombre_dom["domicilio"].get("valor"):
                nombre_dom["domicilio"]["confianza"] = min(nombre_dom["domicilio"]["confianza"], 0.75)
                nombre_dom["domicilio"]["avisos"].append(b["duda_dom_pie"])
            if b.get("duda_dom_debajo") and b["dom"] and nombre_dom["domicilio"].get("valor"):
                nombre_dom["domicilio"]["confianza"] = min(nombre_dom["domicilio"]["confianza"], 0.75)
                nombre_dom["domicilio"]["avisos"].append(b["duda_dom_debajo"])
            if b.get("duda_dom_cabecera") and nombre_dom["domicilio"].get("valor"):
                nombre_dom["domicilio"]["confianza"] = min(nombre_dom["domicilio"]["confianza"], 0.6)
                nombre_dom["domicilio"]["avisos"].append(b["duda_dom_cabecera"])
            if b.get("ilegible"):
                out[rol] = {**nombre_dom, "nif": _vacio(f"El NIF del bloque del cliente («{b['ilegible']}») no tiene un dígito "
                                                         "de control válido: escriba el NIF correcto")}
                continue
            cn = _campo(b["nif"], texto=l.texto, palabras=b["pals"], pagina=l.pagina)
            cn["avisos"].append("Rol deducido: " + ", ".join(b["motivos"] or ["por descarte (sin indicios)"]))
            if b["nif"] in getattr(self, "_nifs_corregidos", {}):
                cn["confianza"] = min(cn["confianza"], 0.6)
                leido = self._nifs_corregidos[b["nif"]]
                cambios = [] if leido is None else \
                    [(a, c) for a, c in zip(re.sub(r"[.\-–—/]", "", leido), b["nif"]) if a.upper() != c]
                if all(a.upper() == "O" and c == "0" for a, c in cambios):
                    cn["avisos"].append("El OCR leyó la letra O en lugar del dígito 0: NIF corregido (su dígito de control es "
                                        "válido); compruébelo en el original")
                else:
                    cn["avisos"].append(f"El OCR leyó «{leido}»: NIF corregido cambiando "
                                        + ", ".join(f"«{a}» por «{c}»" for a, c in cambios)
                                        + " (letras y cifras que el OCR confunde); es la única lectura con dígito de control "
                                        "válido: compruébelo en el original")
            if rol == "emisor":
                seguro = self.nif_empresa == b["nif"] and "bloque de cliente" not in b["motivos"] and not contradice
            else:
                # Con la empresa como emisor y dos NIF, el otro es el destinatario solo si tiene indicios propios de
                # destinatario: si no, puede ser un tercero (cesionario de un factoring, promotor de la obra…)
                en_cabecera = l.pagina == 1 and l.top < tope
                seguro = bool(emisor is not None and self.nif_empresa and emisor["nif"] == self.nif_empresa
                              and len(unicos) == 2 and (b["de"] - b["em"] > 0 or en_cabecera) and not ilegibles)
            if contradice:
                cn["confianza"] = min(cn["confianza"], 0.75)
                cn["papel_dudoso"] = True
                cn["avisos"].append("La posición de los NIF sugiere los papeles contrarios (el otro NIF ocupa el lugar del "
                                    "emisor): confirme emisor y destinatario")
            elif not seguro and (b["em"] - b["de"]) * (1 if rol == "emisor" else -1) < 1:
                cn["confianza"] = min(cn["confianza"], 0.75)
                cn["papel_dudoso"] = True
                cn["avisos"].append("Papel deducido con pocos indicios: confirme emisor y destinatario")
            if rol == "destinatario" and tercero:
                cn["confianza"] = min(cn["confianza"], 0.75)
                cn["papel_dudoso"] = True
                cn["avisos"].append(tercero)
            if rol == "destinatario" and ilegibles and "bloque de cliente" not in b["motivos"]:
                cn["confianza"] = min(cn["confianza"], 0.75)
                cn["avisos"].append(f"El documento contiene otro NIF con dígito de control no válido ({', '.join(ilegibles)}), "
                                    "que puede ser el del destinatario: confirme el destinatario")
            out[rol] = {"nombre": nombre_dom["nombre"], "nif": cn, "domicilio": nombre_dom["domicilio"]}
        out["nifs_detectados"] = sorted(por_nif)
        out["nifs_no_validos"] = [{"texto": n, "pagina": l.pagina, "bbox": l.bbox(), "linea": l.texto} for n, l in sospechosos]
        return out

    # --- tabla de conceptos ------------------------------------------------------------------------------------------
    def _clase(self, palabra: str) -> str | None:
        t = palabra.strip(" .:|¡![]").lower()  # el OCR pega a veces el borde de la tabla, leído como «|», «¡», «!» o «[»
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
                if len(clases) < 3 and any(_es_importe(p.texto) for p in f.palabras):
                    continue  # «Importe de los servicios …… 1.200,00 €»: un renglón con su importe no es una cabecera
                if len(f.palabras) <= 14:
                    cab = f
                    break
        self._ids_filas_tabla = set()
        aviso_tabla = None
        if not cab:
            inferida = self._columnas_inferidas()
            if not inferida:
                formulas = self._lineas_formula()
                if formulas:
                    self._tabla = (formulas, "Conceptos sin tabla: líneas leídas de las fórmulas «cantidad × precio = importe» "
                                             "de cada concepto")
                    return self._tabla
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

    _IMP_F = r"[-−]?(?:\d{1,3}(?:\.\d{3})+|\d+)[.,]\d{2}"
    RE_FORMULA = re.compile(r"(?:^|\s)([-−]?\d{1,6}(?:[.,]\d{1,3})?)\s*(?:[a-záéíóúñ.]{1,6}\s*)?[x×*]\s*(" + _IMP_F +
                            r")\s*(?:€|eur(?:os)?)?(?:\s*/\s*[a-záéíóúñ.]{1,6})?\s*=\s*(" + _IMP_F + r")\s*(?:€|eur(?:os)?)?\s*$", re.I)

    RE_APARTADO_CONCEPTO = re.compile(r"\s*(?:conceptos?|descripci[oó]n(?:\s+de(?:l|\s+los)?\s+(?:servicios?|trabajos?))?|detalle|"
                                      r"servicios?\s+prestados?|trabajos?\s+realizados?)\s*:?\s*", re.I)

    def _linea_unica_texto(self, imp: dict) -> dict | None:
        """Factura sin tabla con el servicio descrito en texto corrido bajo un apartado «Concepto» y un único importe: una
        sola línea con el primer párrafo como concepto y la base imponible como importe. Solo si todas las cifras impresas
        desde el apartado son importes del resumen (base, cuotas, total, retención, a pagar): con cualquier otra cifra
        puede haber varios conceptos y no se deduce nada."""
        base = (imp.get("base_imponible") or {}).get("valor")
        if base is None or not imp.get("desglose"):
            return None
        rotulo = next((l for l in self.lineas if self.RE_APARTADO_CONCEPTO.fullmatch(l.texto)), None)
        if rotulo is None:
            return None
        permitidos = {q2(Decimal(v)) for v in [base] + [(imp.get(k) or {}).get("valor") for k in
                      ("cuota_iva", "total_factura", "total_a_pagar")] if v is not None}
        permitidos |= {q2(Decimal(d[k]["valor"])) for d in imp["desglose"] for k in ("base", "cuota") if d[k].get("valor")}
        if imp.get("retencion") and imp["retencion"]["importe"].get("valor"):
            permitidos.add(q2(Decimal(imp["retencion"]["importe"]["valor"])))
        siguientes = [l for l in self.lineas if l.pagina == rotulo.pagina and l.top > rotulo.bottom - 1]
        for l in siguientes:
            for p in l.palabras:
                v = _num(p.texto) if _es_importe(p.texto) else None
                if v is not None and abs(q2(v)) not in permitidos:
                    return None
        parrafo, ref = [], rotulo
        for l in siguientes:
            # hasta el primer salto de párrafo (más espacio que el interlineado normal)
            if l.top - ref.bottom > (2.5 if not parrafo else 0.6) * max(6.0, ref.bottom - ref.top):
                break
            if any(_es_importe(p.texto) for p in l.palabras) or self._etiquetas_en(l.palabras[:3]):
                break
            parrafo.append(l)
            ref = l
        texto = " ".join(l.texto for l in parrafo)
        if len(texto.split()) < 5:
            return None  # sin texto corrido no hay concepto que tomar
        aviso = ("Factura sin tabla de conceptos: una sola línea con el texto del apartado «Concepto» y la base imponible como "
                 "importe; revísela")
        c_imp = dict(imp["base_imponible"], avisos=[aviso])
        c_imp["confianza"] = min(c_imp.get("confianza") or 0.0, 0.7)
        return {"concepto": _campo(texto, texto=texto, lineas_=parrafo, confianza=0.7, aviso=aviso), "cantidad": _vacio(""),
                "unidad": _vacio(""), "precio": _vacio(""), "importe": c_imp, "dto": _vacio(""), "iva": _vacio("")}

    @staticmethod
    def _cifras_del_resumen(imp: dict) -> set:
        """Importes del resumen (base, cuotas, totales, retención): las únicas cifras que admite una factura de un solo
        concepto descrito en texto corrido."""
        vals = [(imp.get(k) or {}).get("valor") for k in ("base_imponible", "cuota_iva", "total_factura", "total_a_pagar")]
        vals += [d[k].get("valor") for d in imp.get("desglose") or [] for k in ("base", "cuota")]
        if imp.get("retencion") and imp["retencion"]["importe"].get("valor"):
            vals.append(imp["retencion"]["importe"]["valor"])
        out = set()
        for v in vals:
            try:
                if v is not None:
                    out.add(q2(abs(Decimal(v))))
            except ArithmeticError:
                continue
        return out

    def _linea_unica_carta(self, imp: dict) -> dict | None:
        """Factura en forma de carta, sin tabla ni apartado «Concepto»: el párrafo justo encima del cuadro de totales («Arrendamiento
        del local … correspondiente al mes de …», «Servicios de asesoría prestados durante …») describe la
        operación. Una sola línea con ese párrafo como concepto y la base imponible como importe, para revisar. Solo si todas
        las cifras impresas desde el párrafo hacia abajo son importes del resumen (con otra cifra puede haber varios
        conceptos) y el párrafo es texto corrido alineado a la izquierda del cuadro, sin cifras, rótulos ni NIF."""
        base_c = imp.get("base_imponible") or {}
        if base_c.get("valor") is None:
            return None
        cuota = _num(str((imp.get("cuota_iva") or {}).get("valor") or ""))
        if not imp.get("desglose") and cuota != 0:
            return None   # sin desglose, solo la factura sin IVA (cuota cero leída como tal)
        campos_ = [base_c, imp.get("cuota_iva"), imp.get("total_factura"), imp.get("total_a_pagar")]
        campos_ += [d[k] for d in imp.get("desglose") or [] for k in ("base", "cuota")]
        if imp.get("retencion"):
            campos_.append(imp["retencion"]["importe"])
        cajas = [(c["pagina"], c["bbox"]) for c in campos_ if isinstance(c, dict) and c.get("bbox") and c.get("pagina")
                 and c.get("valor") is not None]
        if not cajas:
            return None
        pagina = base_c.get("pagina") if base_c.get("bbox") else min(cajas, key=lambda x: (x[0], x[1][1]))[0]
        cajas = [b for pg, b in cajas if pg == pagina]
        tope, izq = min(b[1] for b in cajas), min(b[0] for b in cajas)
        encima = sorted((l for l in self.lineas if l.pagina == pagina and l.bottom <= tope + 1), key=lambda l: -l.bottom)
        if not encima:
            return None
        ult = encima[0]
        h = max(6.0, ult.bottom - ult.top)
        if tope - ult.bottom > 6 * h or ult.x0 > izq + 20:
            return None   # el párrafo acaba como mucho unos renglones en blanco por encima del cuadro, a su izquierda

        def de_parrafo(l: Linea) -> bool:
            # texto corrido: ni cifras, ni rótulos de importes o de pago y notas, ni contacto, NIF, fecha de la carta o el
            # título con el número de la factura
            t = l.texto
            return (bool(self.RE_PALABRA.search(t)) and not any(_es_importe(p.texto) for p in l.palabras)
                    and not self._etiquetas_en(l.palabras[:3]) and not RE_FIN_TABLA.match(t) and not RE_CONTACTO.search(t)
                    and not re.search(r"\biban\b|^\s*(?:factura|fra\.)\s+(?:" + NUM_ABR + r"|n[úu]m|n[úu]mero)", t, re.I)
                    and not self._fecha_de_carta(t) and not any(nifmod.es_valido(n) for n, _, _ in _nifs_en(t)))
        if not de_parrafo(ult):
            return None
        parrafo, ref = [ult], ult
        for l in encima[1:]:
            if min(l.x1, ult.x1) - max(l.x0, ult.x0) <= 0 or l.bottom > ref.top + 1:
                continue  # otra columna, o a la altura del renglón de debajo
            alto = max(6.0, ref.bottom - ref.top)
            if ref.top - l.bottom > 1.2 * alto or abs(l.x0 - ref.x0) > max(8.0, alto) or not de_parrafo(l):
                break  # salto de párrafo, renglón no alineado (la fecha de la carta) o un dato
            parrafo.insert(0, l)
            ref = l
            if len(parrafo) > 12:
                return None
        texto = re.sub(r"\s*(?:[.…·•_]{2,}|…)\s*$", "", " ".join(l.texto for l in parrafo)).strip()
        if len(texto.split()) < 5:
            return None
        permitidos = self._cifras_del_resumen(imp)
        for l in self.lineas:
            if l.pagina != pagina or l.top < parrafo[0].top - 1:
                continue
            for p in l.palabras:
                v = _num(p.texto) if _es_importe(p.texto) else None
                if v is not None and q2(abs(v)) not in permitidos:
                    return None
        aviso = ("Factura sin tabla de conceptos: una sola línea con el párrafo de encima del cuadro de totales como concepto y "
                 "la base imponible como importe; revísela")
        c_imp = dict(base_c, avisos=list(base_c.get("avisos") or []) + [aviso])
        c_imp["confianza"] = min(c_imp.get("confianza") or 0.0, 0.7)
        return {"concepto": _campo(texto, texto=texto, lineas_=parrafo, confianza=0.7, aviso=aviso), "cantidad": _vacio(""),
                "unidad": _vacio(""), "precio": _vacio(""), "importe": c_imp, "dto": _vacio(""), "iva": _vacio("")}

    def _lineas_formula(self) -> list[dict]:
        """Conceptos en «tarjetas» sin tabla: cada uno con su descripción y una fórmula «2 x 35,00 € = 70,00 €» (en el mismo
        renglón, tras la descripción, o en el renglón de debajo). Solo cuentan las fórmulas que cuadran (cantidad × precio =
        importe); la descripción es el texto del propio renglón o el bloque contiguo de encima."""
        out, usadas = [], set()
        for k, l in enumerate(self.lineas):
            m = self.RE_FORMULA.search(l.texto)
            if not m:
                continue
            cant, precio, imp = _num(m.group(1)), _num(m.group(2)), _num(m.group(3))
            if None in (cant, precio, imp) or abs(q2(cant * precio) - imp) > Decimal("0.01"):
                continue
            antes = l.texto[:m.start()].strip(" :·-–")
            if antes and self._etiquetas_en(self._palabras_rango(l, 0, m.start())):
                continue  # «IVA 21 % x …»: cálculo de un importe del resumen, no una línea
            desc: list[Linea] = []
            if self.RE_PALABRA.search(antes):
                desc, texto = [l], antes
            else:
                ref = l
                for x in reversed(self.lineas[max(0, k - 8):k]):
                    if x.pagina != l.pagina or id(x) in usadas or x.bottom > ref.top + 1 \
                            or min(x.x1, l.x1 + 200) - max(x.x0, l.x0 - 10) <= 0:
                        continue  # otra columna o el mismo renglón
                    if ref.top - x.bottom > max(6.0, 0.9 * (ref.bottom - ref.top)):
                        break
                    # la tarjeta empieza bajo un rótulo, un apartado («Conceptos») o la fórmula de la tarjeta anterior
                    if self.RE_FORMULA.search(x.texto) or not self.RE_PALABRA.search(x.texto) or re.search(r":\s*$", x.texto) \
                            or self.RE_APARTADO_CONCEPTO.fullmatch(x.texto) or self._rotulo_importe(x):
                        break
                    desc.insert(0, x)
                    ref = x
                texto = " ".join(x.texto for x in desc)
            if not desc or not texto:
                continue
            usadas.update(id(x) for x in desc + [l])
            pals_f = self._palabras_desde(l, m.start())
            cifras = {k_c: _campo(str(v), texto=m.group(0).strip(), palabras=pals_f, pagina=l.pagina)
                      for k_c, v in (("cantidad", cant), ("precio", precio), ("importe", imp))}
            out.append({"concepto": _campo(texto, texto=texto, lineas_=desc), **cifras, "unidad": _vacio(""), "dto": _vacio(""),
                        "iva": _vacio("")})
        if out:
            self._ids_filas_tabla |= {id(f) for f in self.filas if any(id(b) in usadas for b in f.bloques)}
        return out

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
            ids_nums = {id(p) for p in nums}
            textos = [p for p in pals if id(p) not in ids_nums and self.RE_PALABRA.search(p.texto)]
            if not textos or max(p.x1 for p in textos) > nums[-1].x0 or _dato_tras_dos_puntos(f.texto):
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
        # columnas por el borde derecho: la primera columna (por orden de creación) a 12 pt o menos. Los bordes de las
        # columnas distan más de 12 pt entre sí, así que en la ventana caben pocas: búsqueda binaria, coste n·log n
        bordes: list[tuple[float, int]] = []
        for _, f, nums, _ in g:
            for p in nums:
                k = bisect.bisect_left(bordes, (p.x1 - 12, -1))
                cerca = [j for x, j in bordes[k:k + 4] if abs(x - p.x1) <= 12]
                if cerca:
                    cl = clusters[min(cerca)]
                    cl["n"] += 1
                    cl["x0"], cl["x1r"] = min(cl["x0"], p.x0), max(cl["x1r"], p.x1)
                else:
                    clusters.append({"x0": p.x0, "x1": p.x1, "x1r": p.x1, "n": 1})
                    bisect.insort(bordes, (p.x1, len(clusters) - 1))
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
        # Sin columnas a la izquierda de la descripción, esta empieza donde se alinean sus renglones, aunque el rótulo vaya
        # centrado sobre una columna ancha («DESCRIPCIÓN» en el centro y los conceptos desde el margen izquierdo)
        margen_desc = None
        if concepto_col and not izq_cols:
            margen_desc = self._margen_descripcion(cab, fondo, concepto_col, numericas)
        lim_desc = concepto_col["x0"] - 60 if concepto_col else 0
        if margen_desc is not None:
            lim_desc = min(lim_desc, margen_desc - 2)
        tab_x0 = (min(c["x0"] for c in izq_cols) - 20) if izq_cols else lim_desc
        tab_x1 = max(c["x1"] for c in cols) + 40
        lineas_out, paginas_tabla = [], {cab.pagina}
        filas_cuerpo, filas_cuerpo_orig = [], []
        saltando = 0
        for f in self.filas:
            # fila de la cabecera o por encima; una fila que solo roza el fondo de la cabecera (cajas del OCR más altas que
            # el texto) ya es del cuerpo
            if (f.pagina, (f.top + f.bottom) / 2) <= (cab.pagina, fondo):
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
            if self._fila_de_totales(f) or (filas_cuerpo and self._solo_cifras(f)
                                            and not self._cifras_del_primer_concepto(f, filas_cuerpo, numericas)):
                break
            if f.pagina != cab.pagina and len({self._clase(p.texto) for p in f.palabras} - {None}) >= 2:
                continue  # cabecera repetida en una página nueva
            if RE_REGISTRO_MERCANTIL.search(txt) or (filas_cuerpo and f.top - filas_cuerpo[-1].bottom > 140 and f.pagina == filas_cuerpo[-1].pagina):
                break
            filas_cuerpo.append(f)
            filas_cuerpo_orig.append(f_orig)
        self._ids_filas_tabla |= {id(f) for f in filas_cuerpo_orig}
        precio_deducido = self._precio_sin_rotulo(filas_cuerpo, cols)
        if precio_deducido:
            cols = sorted(cols + [precio_deducido], key=lambda c: c["x0"])
            numericas = [c for c in cols if c["clase"] in ("cantidad", "precio", "dto", "iva", "importe")]
            aviso_tabla = aviso_tabla or ("Columna de precio sin rótulo legible: deducida por la alineación de las cifras entre "
                                          "la cantidad y el importe; revise las líneas")
        previas: list[tuple] = []   # renglones de texto antes de las cifras del primer concepto
        con_prefijo = False   # el primer concepto empieza por encima del renglón de sus cifras
        # Renglones más de la cabecera sin ninguna palabra de rótulo conocida («PRECIO» / «(€/millar)»): pegados a la banda de
        # cabecera (sin salto de párrafo), antes de la primera cifra, con texto que no es una cifra bajo una columna numérica.
        # Son cabecera: no son el comienzo de un concepto y su texto cuenta para el rótulo de su columna
        cab_extra = self._renglones_mas_de_cabecera(cab, fondo, filas_cuerpo, numericas, concepto_col)
        ids_cab_extra = {id(g) for g in cab_extra}
        # rótulo de la columna del precio en toda la cabecera («PRECIO / €/MILLAR»): unidad del precio
        col_precio = next((c for c in cols if c["clase"] == "precio"), None)
        rot_precio = " ".join(p.texto for g in list(banda) + cab_extra for p in g.palabras if col_precio is not None
                              and col_precio["x0"] - 4 <= (p.x0 + p.x1) / 2 <= col_precio["x1"] + 4)
        for f in filas_cuerpo:
            if id(f) in ids_cab_extra:
                continue
            pals = f.palabras
            nums, textos = [], []
            for i, p in enumerate(pals):
                if p.texto in ("€", "EUR", "eur") or re.fullmatch(r"[-_=·.*]{3,}", p.texto):
                    continue
                if _es_importe(p.texto) or re.fullmatch(r"[-−]?\d{1,6}|\d{1,3}(?:\.\d{3})+", p.texto.strip()) or RE_PORCENTAJE.fullmatch(p.texto.strip()):
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
            cercanias = []
            for k_p, p in enumerate(nums):
                cx = (p.x0 + p.x1) / 2
                mejor, dist = None, 10 ** 9
                for c in numericas:
                    d = 0 if c["x0"] - 6 <= cx <= c["x1"] + 6 else min(abs(cx - c["x0"]), abs(cx - c["x1"]), abs(p.x1 - c["x1"]))
                    if d < dist:
                        mejor, dist = c, d
                cercanias.append((dist, k_p, p, mejor))
            # cada columna se queda con la cifra más cercana (la que cae dentro de ella antes que una vecina desplazada), no
            # con la primera por la izquierda
            for dist, _, p, mejor in sorted(cercanias, key=lambda t: (t[0], t[1])):
                cx = (p.x0 + p.x1) / 2
                ancho_ref = 70
                if mejor and dist <= ancho_ref and mejor["clase"] not in asign:
                    # números de la descripción (p. ej. «132 viviendas») no se asignan si caen en la zona del concepto
                    if concepto_col and concepto_col["x0"] - 5 <= cx and (not numericas or cx < min((c["x0"] for c in numericas
                                                                                                      if c["x0"] > concepto_col["x1"] - 5), default=10 ** 6) - 10):
                        textos.append(p)
                        continue
                    asign[mejor["clase"]] = p
                else:
                    textos.append(p)
            izq = [c["x1"] for c in cols if concepto_col and c is not concepto_col and c["x1"] < concepto_col["x0"] + 5]
            lim_izq = (max(izq) + 2) if izq else lim_desc
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
                    ult = lineas_out[-1]
                    if not ult["_seguidos"]:   # la descripción sigue en el renglón de justo debajo de las cifras
                        ult["_sigue_debajo"] = f.top - ult["_bottom"] <= 0.8 * max(1.0, f.bottom - f.top)
                    ult["concepto"]["valor"] = (ult["concepto"]["valor"] + "\n" + concepto).strip()
                    ult["concepto"]["bbox"][3] = round(f.bottom, 1)
                    ult["_bottom"] = f.bottom
                    ult["_seguidos"] += 1
                    ult["_pag_ult"] = f.pagina
                elif not lineas_out and concepto:
                    previas.append((f, concepto, concepto_pals))
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
                "_bottom": f.bottom, "_seguidos": 0, "_prefijo": [], "_sigue_debajo": False, "_pag_ult": f.pagina,
                "_propio": concepto,
            }
            if not lineas_out and previas:
                # Renglones de texto contiguos (sin salto de párrafo) encima de las cifras del primer concepto: pueden ser el
                # comienzo de su descripción (cifras en un renglón intermedio) o un encabezado de grupo («Albarán nº …»); se
                # decide al final, según la descripción siga o no bajo las cifras
                ref = f
                for pf, ptxt, ppals in reversed(previas):
                    if pf.pagina != ref.pagina or ref.top - pf.bottom > 0.8 * max(1.0, pf.bottom - pf.top):
                        break
                    linea["_prefijo"].insert(0, (pf, ptxt, ppals))
                    ref = pf
            if linea["importe"]["valor"] is None and linea["precio"]["valor"] is not None and linea["cantidad"]["valor"] is None:
                linea["importe"], linea["precio"] = linea["precio"], _vacio("")
            # Precio por millar o por centenar: la unidad, en el rótulo del precio, junto a las cifras de la fila o en la columna
            # de la unidad («millar», «mil», «1000»: se lee como «/millar», «/mil», «/1000»)
            self._precio_por_lote(linea, rot_precio, " ".join(
                [p.texto for p in textos if (p.x0 + p.x1) / 2 > lim_der and p not in concepto_pals] + ["/" + unidad if unidad else ""]),
                cf_conf)
            if linea["cantidad"]["valor"] is not None and metodo == "ocr" and not any(c["clase"] == "dto" for c in cols) \
                    and linea["precio"]["valor"] and linea["importe"]["valor"] and Decimal(linea["precio"]["valor"]) != 0 \
                    and q2(Decimal(linea["cantidad"]["valor"]) * Decimal(linea["precio"]["valor"])) != q2(Decimal(linea["importe"]["valor"])):
                q = Decimal(linea["importe"]["valor"]) / Decimal(linea["precio"]["valor"])
                if q > 0 and q == q.quantize(Decimal("0.01")):
                    leida = linea["cantidad"]["texto"]
                    linea["cantidad"] = _campo(str(q2(q)), metodo="calculado", confianza=0.6,
                                               aviso=f"La cantidad leída por OCR («{leida}») no cuadra con importe ÷ precio: se propone esta")
            if linea["cantidad"]["valor"] is None and any(c["clase"] == "cantidad" for c in cols) and metodo == "ocr" \
                    and linea["precio"]["valor"] and linea["importe"]["valor"] and re.fullmatch(r"\d{3,6}", linea["precio"]["texto"] or "") \
                    and re.search(r"[.,]\d{2}\b", linea["importe"]["texto"] or ""):
                # «1250» por «12,50»: el OCR perdió la coma decimal del precio. Solo si con la coma la cantidad sale exacta
                # (importe ÷ precio con dos decimales como mucho) y sin ella no; propuesta calculada que se pide confirmar
                p_ent, imp_l = Decimal(linea["precio"]["valor"]), Decimal(linea["importe"]["valor"])
                q_ent, p_dec = imp_l / p_ent, p_ent / 100
                q = imp_l / p_dec
                if q_ent != q_ent.quantize(Decimal("0.01")) and 0 < q == q.quantize(Decimal("0.01")) and q2(q * p_dec) == q2(imp_l):
                    leido = linea["precio"]["texto"]
                    linea["precio"] = _campo(str(q2(p_dec)), metodo="calculado", confianza=0.6,
                                             aviso=f"El OCR no leyó la coma decimal del precio («{leido}»): se propone este, que "
                                                   "cuadra con el importe")
            if linea["cantidad"]["valor"] is None and any(c["clase"] == "cantidad" for c in cols) and metodo == "ocr" \
                    and linea["precio"]["valor"] and linea["importe"]["valor"] and Decimal(linea["precio"]["valor"]) != 0:
                q = Decimal(linea["importe"]["valor"]) / Decimal(linea["precio"]["valor"])
                if q > 0 and q == q.quantize(Decimal("0.01")) and q2(q * Decimal(linea["precio"]["valor"])) == q2(Decimal(linea["importe"]["valor"])):
                    linea["cantidad"] = _campo(str(q2(q)), metodo="calculado", confianza=0.6,
                                               aviso="Cantidad no legible: deducida de importe ÷ precio")
            lineas_out.append(linea)
        prim = lineas_out[0] if lineas_out else None
        prefijo = self._prefijo_del_primer_concepto(prim) if prim and prim["_prefijo"] else None
        if prefijo:
            # Descripción de varios renglones con las cifras en uno intermedio o en el último: los renglones de encima son el
            # comienzo del primer concepto
            prefijo, dudoso = prefijo
            con_prefijo = True
            c_l = prim["concepto"]
            c_l["valor"] = "\n".join([t for _, t, _ in prefijo] + [c_l["valor"]]).strip()
            xs = [p for _, _, ps in prefijo for p in ps]
            c_l["bbox"] = [round(min([c_l["bbox"][0]] + [p.x0 for p in xs]), 1), round(prefijo[0][0].top, 1),
                           round(max([c_l["bbox"][2]] + [p.x1 for p in xs]), 1), c_l["bbox"][3]]
            if dudoso:
                c_l["confianza"] = min(c_l["confianza"], 0.7)
                c_l["avisos"].append("Concepto con las cifras en un renglón intermedio: los renglones de encima de las cifras "
                                     "se han tomado como el comienzo de su descripción; compruebe que no sean un encabezado o "
                                     "una referencia")
        if con_prefijo and len(lineas_out) > 1:
            # Con las cifras en un renglón intermedio de la descripción, los renglones de texto entre las cifras de dos líneas
            # pueden ser el final de la primera o el comienzo de la siguiente: se asignan a la anterior y se pide confirmarlo.
            # No hay duda si la línea siguiente empieza en otra página (tras la cabecera repetida) que esos renglones
            aviso = ("Conceptos de varios renglones con las cifras en uno intermedio: compruebe qué renglones de la descripción "
                     "son de cada línea")
            for k in range(len(lineas_out) - 1):
                if lineas_out[k]["_seguidos"] and lineas_out[k]["_pag_ult"] == lineas_out[k + 1]["concepto"]["pagina"]:
                    for l in (lineas_out[k], lineas_out[k + 1]):
                        l["concepto"]["confianza"] = min(l["concepto"]["confianza"], 0.7)
                        if aviso not in l["concepto"]["avisos"]:
                            l["concepto"]["avisos"].append(aviso)
        for l in lineas_out:
            l.pop("_bottom", None)
            for k in ("_seguidos", "_prefijo", "_sigue_debajo", "_pag_ult", "_propio"):
                l.pop(k, None)
            l["concepto"]["valor"] = re.sub(r"[ \t]+", " ", l["concepto"]["valor"]).strip()
        self._tabla = (lineas_out, (aviso_tabla if lineas_out else "Tabla sin líneas reconocibles"))
        return self._tabla

    # Renglón que encabeza un grupo de líneas y no es parte de un concepto: una referencia a otro documento («Albarán nº 45 de
    # …», «Rectifica la factura 2599», «Según presupuesto nº 26/014»)
    RE_REF_DOCUMENTO = re.compile(r"\b(?:pedido|albar[aá]n|presupuesto|factura|oferta|contrato|expediente|certificaci[oó]n|"
                                  r"orden\s+de\s+(?:trabajo|compra)|nota\s+de\s+entrega|parte\s+de\s+trabajo)\s*"
                                  r"(?:n(?:\.?\s*[º°o]|ro|[uú]m)?\.?|n[uú]mero|#|:)?\s*(?-i:[A-Z]{0,4})[-/]?\d", re.I)
    # final de renglón que pide continuación: coma, guion de partición, paréntesis abierto o una preposición o conjunción
    RE_SIGUE_RENGLON = re.compile(r"(?:[,(]|[^\W\d_]-|\b(?:de|del|y|e|o|u|con|en|para|por|a|al|la|el|los|las|sin|sobre|"
                                  r"seg[uú]n|que|entre|hasta|desde))\s*$")

    @staticmethod
    def _empieza_en_minuscula(t: str) -> bool:
        return bool(re.match(r"\s*[a-záéíóúüñ]", t))

    def _parece_encabezado(self, t: str, siguiente: str) -> bool:
        """Renglón con forma de encabezado de grupo: termina en «:» («TRABAJOS DE IMPRESIÓN:»), es una referencia a otro
        documento («Pedido nº 00/12 de …», «Rectifica la factura nº 2599») o va todo en mayúsculas cuando el renglón
        siguiente no."""
        t = t.strip()
        if t.endswith(":") or self.RE_REF_DOCUMENTO.search(t):
            return True
        letras = re.findall(r"[^\W\d_]", t)
        return len(letras) >= 4 and not any(c.islower() for c in letras) and any(c.islower() for c in siguiente)

    def _encabezado_de_grupo(self, t: str, siguiente: str) -> bool:
        """Renglón de texto encima de la primera línea que encabeza el grupo en lugar de empezar su descripción (forma de
        encabezado, _parece_encabezado). Un renglón siguiente en minúscula continúa la frase: entonces no es encabezado."""
        return not self._empieza_en_minuscula(siguiente) and self._parece_encabezado(t, siguiente)

    def _prefijo_del_primer_concepto(self, prim: dict) -> tuple[list, bool] | None:
        """Renglones de texto contiguos encima de las cifras de la primera línea (prim["_prefijo"]) que son el comienzo de su
        descripción, y si hay que pedir confirmarlo. Los encabezados de grupo (y lo que hay encima) no cuentan. Con texto
        propio en el renglón de las cifras, el renglón de encima es del concepto si la frase sigue (el renglón de las cifras
        empieza en minúscula o el de encima acaba en coma, preposición…); se pide confirmar si alguno de esos renglones tiene
        forma de encabezado. Sin indicio de que la frase siga, si la descripción sigue también debajo de las cifras se
        antepone pero se pide confirmar (un encabezado sin «:» ni referencia, «Material de oficina», sería igual); si no
        sigue debajo es un encabezado (no se antepone)."""
        previas, propio = prim["_prefijo"], prim["_propio"].strip()
        # lo que sigue al último renglón de encima: el texto del renglón de las cifras o, sin él, el primero de debajo
        siguiente = propio or prim["concepto"]["valor"].strip().split("\n")[0]
        textos = [t for _, t, _ in previas] + [siguiente]
        corte = 0
        for i in range(len(previas)):
            if self._encabezado_de_grupo(textos[i], textos[i + 1]):
                corte = i + 1
        pref = previas[corte:]
        sospecha = any(self._parece_encabezado(textos[i], textos[i + 1]) for i in range(corte, len(previas)))
        if not propio:
            # renglón de solo cifras: la descripción está encima (y quizá debajo)
            if pref:
                return pref, sospecha
            # solo un encabezado encima y nada debajo: mejor que un concepto vacío, pero se pide confirmar
            return (previas, True) if not prim["_seguidos"] else None
        if not pref:
            return None
        if self._empieza_en_minuscula(propio) or self.RE_SIGUE_RENGLON.search(pref[-1][1]):
            return pref, sospecha
        return (pref, True) if prim["_sigue_debajo"] else None

    @staticmethod
    def _renglones_mas_de_cabecera(cab: Fila, fondo: float, filas_cuerpo: list[Fila], numericas: list[dict],
                                   concepto_col: dict | None) -> list[Fila]:
        """Renglones de la cabecera que no tienen ninguna palabra de rótulo conocida («(€/1.000 uds)», «(por mil)», «‰» bajo
        «PRECIO»): los primeros del cuerpo, encadenados a la cabecera sin salto de párrafo, sin cifras, y con algún tramo de
        texto que empieza en la zona de las columnas numéricas y cae bajo una de ellas."""
        if not numericas:
            return []
        lim_num = min((c["x0"] for c in numericas if concepto_col is None or c["x0"] > concepto_col["x1"] - 5), default=10 ** 6)
        out, ref_bottom = [], fondo
        for g in filas_cuerpo:
            if g.pagina != cab.pagina or g.top - ref_bottom > 0.8 * max(1.0, g.bottom - g.top):
                break
            if any(_es_importe(p.texto) or re.fullmatch(r"[-−]?\d+(?:[.,]\d+)*", p.texto.strip()) for p in g.palabras):
                break   # una cifra: ya es el cuerpo de la tabla
            bajo_num = [b for b in g.bloques if b.x0 >= lim_num - 15
                        and any(c["x0"] - 4 <= (p.x0 + p.x1) / 2 <= c["x1"] + 4 for p in b.palabras for c in numericas)]
            if not bajo_num:
                break
            out.append(g)
            ref_bottom = g.bottom
        return out

    def _margen_descripcion(self, cab: Fila, fondo: float, concepto_col: dict, numericas: list[dict]) -> float | None:
        """Borde izquierdo de los renglones de la descripción bajo la cabecera (hasta los totales): el x0 más repetido de los
        bloques con texto que se solapan con la columna del concepto y empiezan antes de la primera columna numérica de su
        derecha. Si ninguno se solapa con el rótulo (conceptos cortos alineados a la izquierda de un rótulo centrado), el x0
        más repetido del último tramo de texto antes de las cifras en las filas con cifras (lo que haya más a la izquierda,
        un número de orden o una columna lateral, no cuenta). None si ningún x0 se repite (salvo que solo haya uno)."""
        lim_num = min((c["x0"] for c in numericas if c["x0"] > concepto_col["x1"] - 5), default=10 ** 6)
        xs, xs_cifras, ant = [], [], None
        for f in self.filas:
            if f.pagina != cab.pagina or (f.top + f.bottom) / 2 <= fondo:
                continue
            if RE_FIN_TABLA.match(f.texto.strip()) or (ant is not None and f.top - ant.bottom > 140) or len(xs) + len(xs_cifras) >= 400:
                break
            ant = f
            con_texto = [b for b in f.bloques if b.x0 < lim_num and self.RE_PALABRA.search(b.texto)]
            xs += [b.x0 for b in con_texto if min(b.x1, concepto_col["x1"]) - max(b.x0, concepto_col["x0"]) > 0]
            if con_texto and any(_es_importe(p.texto) and (p.x0 + p.x1) / 2 >= lim_num - 10 for p in f.palabras):
                xs_cifras.append(max(b.x0 for b in con_texto))
        m = self._x0_mas_repetido(xs) if xs else None
        return m if m is not None or not xs_cifras else self._x0_mas_repetido(xs_cifras)

    @staticmethod
    def _x0_mas_repetido(xs: list[float]) -> float | None:
        """El x0 más repetido (grupos de 2 pt; con empate, el de más a la izquierda); None si ninguno se repite, salvo que
        solo haya uno."""
        xs = sorted(xs)
        grupos: list[list[float]] = []
        for x in xs:
            if grupos and x - grupos[-1][0] <= 2:
                grupos[-1].append(x)
            else:
                grupos.append([x])
        g = max(grupos, key=len)   # con empate, el de más a la izquierda
        return g[0] if len(g) >= 2 or len(xs) == 1 else None

    def _cifras_del_primer_concepto(self, f: Fila, filas_cuerpo: list[Fila], numericas: list[dict]) -> bool:
        """Fila de solo cifras justo bajo renglones de texto sin ninguna cifra (las primeras de la tabla): son la cantidad, el
        precio y el importe del primer concepto, con la descripción repartida por encima y por debajo, no los valores de un
        cuadro de totales. Cada cifra cae en una columna numérica distinta de la cabecera, una de ellas la del importe."""
        if any(_es_importe(p.texto) for g in filas_cuerpo for p in g.palabras):
            return False
        ant = filas_cuerpo[-1]
        if ant.pagina != f.pagina or f.top - ant.bottom > 0.8 * max(1.0, ant.bottom - ant.top):
            return False
        cols = []
        for p in f.palabras:
            cx = (p.x0 + p.x1) / 2
            c = next((c for c in numericas if c["x0"] - 6 <= cx <= c["x1"] + 6), None)
            if c is None or any(c is x for x in cols):
                return False
            cols.append(c)
        return any(c["clase"] == "importe" for c in cols)

    RE_POR_MIL = re.compile(r"‰|/\s*(?:1\.?000(?![\d.,]\d)|mil(?:lar(?:es)?)?\b)|\bpor\s+(?:cada\s+)?mil(?:lar)?\b|\bmillar(?:es)?\b", re.I)
    RE_POR_CIEN = re.compile(r"/\s*(?:100(?![\d.,]\d)|cien\b|centenar(?:es)?\b)|\bpor\s+(?:cada\s+)?centenar\b|\bcentenar(?:es)?\b", re.I)

    def _precio_por_lote(self, linea: dict, rotulo: str, texto_fila: str, conf: float) -> None:
        """Precio por millar o por centenar («PRECIO €/MILLAR», «€/100 uds», «por mil», «‰»), en el rótulo de la columna del
        precio o junto a las cifras de la fila: si importe = precio × cantidad ÷ 1000 (o ÷ 100), la cantidad se expresa en
        millares (o centenares), como el precio, para que precio × cantidad = importe; la unidad es «millar» («centenar»).
        Con la cantidad ya en esa unidad (precio × cantidad = importe) solo se anota la unidad si la marca va en la propia
        fila: con la marca solo en el rótulo, la línea puede ser un importe fijo (portes, diseño) de la misma tabla."""
        en_fila = True
        for rx, factor, nombre in ((self.RE_POR_MIL, 1000, "millar"), (self.RE_POR_CIEN, 100, "centenar")):
            if rx.search(texto_fila):
                break
        else:
            en_fila = False
            for rx, factor, nombre in ((self.RE_POR_MIL, 1000, "millar"), (self.RE_POR_CIEN, 100, "centenar")):
                if rx.search(rotulo):
                    break
            else:
                return
        try:
            c, p, i = (Decimal(linea[k]["valor"]) for k in ("cantidad", "precio", "importe"))
        except (TypeError, ValueError, ArithmeticError):
            return
        if c <= 0 or p == 0:
            return
        if q2(c * p) == q2(i):
            if not en_fila:
                return
        else:
            q = (c / factor).normalize()
            dif = abs(q2(q * p) - q2(i))
            if dif > Decimal("0.01") or q.as_tuple().exponent < -6:
                return
            leida = linea["cantidad"]["texto"]
            campo = dict(linea["cantidad"], valor=format(q, "f"), avisos=list(linea["cantidad"].get("avisos") or []))
            campo["avisos"].append(f"Precio por {nombre}: la cantidad impresa ({leida}) se expresa en {nombre}es "
                                   f"({campo['valor']}) para que cantidad × precio = importe")
            if dif:
                campo["confianza"] = min(campo.get("confianza") or 0.0, 0.7)
                campo["avisos"].append("Con la cantidad en esa unidad, cantidad × precio difiere del importe en un céntimo: "
                                       "confirme")
            linea["cantidad"] = campo
        if linea["unidad"].get("valor") is None:
            linea["unidad"] = _campo(nombre, texto=nombre, palabras=[], confianza=conf)

    @staticmethod
    def _precio_sin_rotulo(filas: list[Fila], cols: list[dict]) -> dict | None:
        """Cabecera con «Cantidad» e «Importe» pero sin rótulo de precio legible (p. ej. ilegible en un escaneo): si las filas
        tienen una columna de cifras alineadas por la derecha entre ambas, fuera de toda columna rotulada, es el precio."""
        clases = {c["clase"] for c in cols}
        if "precio" in clases or not {"cantidad", "importe"} <= clases or len(filas) < 2:
            return None
        cant = next(c for c in cols if c["clase"] == "cantidad")
        imp = next(c for c in cols if c["clase"] == "importe")
        if cant["x1"] >= imp["x0"]:
            return None
        grupos: list[dict] = []
        for f in filas:
            for p in f.palabras:
                cx = (p.x0 + p.x1) / 2
                if not (cant["x1"] + 6 < cx < imp["x0"] - 6) or any(c["x0"] - 6 <= cx <= c["x1"] + 6 for c in cols):
                    continue
                if not (_es_importe(p.texto) or re.fullmatch(r"\d{1,6}", p.texto.strip())):
                    continue
                g = next((g for g in grupos if abs(g["x1"] - p.x1) <= 6), None)
                if g is None:
                    grupos.append({"x0": p.x0, "x1": p.x1, "x1max": p.x1, "filas": {id(f)}})
                else:
                    g["x0"], g["x1max"] = min(g["x0"], p.x0), max(g["x1max"], p.x1)
                    g["filas"].add(id(f))
        g = max(grupos, key=lambda g: len(g["filas"]), default=None)
        if g is None or len(g["filas"]) < max(2, (len(filas) + 1) // 2):
            return None

        def en(c, f):  # cifra de la fila en la columna c
            v = [_num(p.texto) for p in f.palabras if c["x0"] - 6 <= (p.x0 + p.x1) / 2 <= c["x1"] + 6
                 and (_es_importe(p.texto) or re.fullmatch(r"[-−]?\d{1,6}", p.texto.strip()))]
            return v[0] if len(v) == 1 else None
        # Comprobación aritmética: en la mitad de las filas, cantidad × cifra = importe o, sin cantidad legible, importe ÷ cifra
        # es una cantidad exacta (una columna de tipos de IVA o de descuentos no lo cumple)
        col = {"x0": g["x0"], "x1": g["x1max"]}
        cuadran = 0
        for f in filas:
            pr, im, q = en(col, f), en(imp, f), en(cant, f)
            if pr in (None, 0) or im is None:
                continue
            if q is not None:
                cuadran += q2(q * pr) == q2(im)
            else:
                r = im / pr
                cuadran += r > 0 and r == r.quantize(Decimal("0.01"))
        if cuadran < max(2, (len(g["filas"]) + 1) // 2):
            return None
        return {"clase": "precio", "x0": g["x0"], "x1": g["x1max"], "texto": ""}

    def _fila_de_totales(self, f: Fila) -> bool:
        """Fila que inicia el bloque de totales: «Base imponible …», varias etiquetas de importe, o etiqueta + cifra sin concepto
        (en la fila entera o en uno de sus bloques)."""
        if self._totales_en(f):
            return True
        bloques = sorted(f.bloques, key=lambda b: b.x0)
        # Solo los primeros bloques con etiqueta: una fila real tiene pocos y así el coste no crece con el cubo del
        # número de bloques en filas desmesuradas
        con_etiqueta = (k for k in range(1, len(bloques)) if self._etiquetas_en(bloques[k].palabras))
        return len(bloques) > 1 and any(self._totales_en(Fila(f.pagina, bloques[k:]))
                                         for k in itertools.islice(con_etiqueta, MAX_SUFIJOS_TOTALES))

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
        por_x = xs = None
        for e in out:
            ant = res[-1] if res else None
            if ant is not None and por_x is None:
                por_x = sorted(palabras, key=lambda p: p.x0)
                xs = [p.x0 for p in por_x]
            # palabras entre las dos etiquetas (búsqueda binaria por x0: lineal aunque la fila tenga muchas etiquetas)
            entre = por_x[bisect.bisect_left(xs, ant["x1"]):bisect.bisect_right(xs, e["x0"])] if ant is not None else []
            if ant is not None and any(p.texto.strip() == "/" for p in entre) and \
                    not any(_es_importe(p.texto) for p in entre):
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
        inicios = [ini for ini, _ in pos]
        fines = [ini + len(p.texto) for ini, p in pos]
        usados, out = set(), []
        for clase, pat in ETIQUETAS_IMPORTE:
            for m in re.finditer(pat, texto, re.I):
                rango = set(range(m.start(), m.end()))
                if rango & usados:
                    continue
                # palabras que tocan la coincidencia (inicios y fines crecen: búsqueda binaria)
                pw = [p for _, p in pos[bisect.bisect_right(fines, m.start()):bisect.bisect_left(inicios, m.end())]]
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

    @staticmethod
    def _signo_del_rotulo_iva(p: Palabra, prev: Palabra | None, sig: Palabra | None, en_et: dict) -> bool:
        """«IVA %»: un «%» suelto a un espacio tras el rótulo del IVA es parte de ese rótulo (la columna del tipo), salvo que
        esté igual de cerca o más del rótulo siguiente («% IVA»)."""
        if p.texto.strip() != "%" or prev is None or id(prev) not in en_et or en_et[id(prev)]["clase"] != "iva":
            return False
        hueco = p.x0 - prev.x1
        if not 0 <= hueco < 0.6 * max(1.0, p.bottom - p.top):
            return False
        return not (sig is not None and id(sig) in en_et and sig.x0 - p.x1 <= hueco)

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
            if self._signo_del_rotulo_iva(p, prev, sig, en_et):
                # «IVA %»: el signo tras el rótulo del IVA marca su columna como la del tipo
                e_prev = en_et[id(prev)]
                e_prev["x1"] = max(e_prev["x1"], p.x1)
                e_prev["tipo"] = True
                if segs and segs[-1].get("et") is e_prev:
                    segs[-1]["x1"] = e_prev["x1"]
                continue
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
        ivas = [sg for sg in segs if sg["clase"] == "iva"]
        # dos columnas «IVA»: si una ya es la del tipo («% IVA», «Tipo IVA»), la otra es la cuota («% IVA | IVA»), no otro tipo
        hay_cuota = any(sg["clase"] in ("cuota",) for sg in segs) or (len(ivas) >= 2 and not any(sg["tipo"] for sg in ivas))
        for sg in segs:
            # «IVA» como columna junto a «Cuota IVA»: es el tipo
            if sg["clase"] == "iva" and hay_cuota and not sg["tipo"]:
                sg["tipo"] = True
        return segs

    def importes(self):
        hechos = []  # (clase, valor Decimal, palabras, pagina, texto, pct, estrategia)
        # lecturas OCR de formato dudoso («-1 23 50», «550.80» en un documento con coma decimal): solo cuentan si cuadran
        # con el resto de importes (_confirmar_dudosos)
        dudosos = []
        self._marcas_hecho = {}   # id(hecho) → (confianza máxima, aviso) de los importes leídos de forma indirecta
        filas = self.filas
        consumidas: set[int] = set()  # palabras ya usadas como valor de una columna o como tipo de una fila del cuadro
        self.tabla()
        de_tabla = self._ids_filas_tabla
        coma_decimal = self._coma_decimal()
        for i, f in enumerate(filas):
            if id(f) in de_tabla:
                continue  # cabecera y líneas de la tabla de conceptos («Base de hormigón…», columna «IVA %»)
            pals = f.palabras   # ordenadas por x0
            ets = [e for e in self._etiquetas_en(pals) if not any(id(p) in consumidas for p in e["pals"])]
            if not ets:
                continue
            if len(ets) > MAX_ETIQUETAS_FILA:
                continue  # fila desmesurada (renglones encadenados de un documento hostil): no es un cuadro de importes
            xs = [p.x0 for p in pals]
            pendientes = []
            for k, e in enumerate(ets):
                lim = ets[k + 1]["x0"] if k + 1 < len(ets) else 10 ** 6
                validos = []
                pequenas = []   # cifras pequeñas y poco legibles sobre la línea base: relleno, salvo que no siga otra cifra
                resto = []   # palabras que siguen a la etiqueta (sin el relleno de puntos)
                traduccion = cortada = False
                ref_h, ref_medio = self._altura_rotulo(e["pals"])
                # solo las palabras entre la etiqueta y la siguiente (búsqueda binaria por x0)
                for p in pals[bisect.bisect_left(xs, e["x1"] - 1):bisect.bisect_left(xs, lim)]:
                    if p.x0 < e["x1"] - 1 or p.x0 >= lim or p in e["pals"] or id(p) in consumidas:
                        continue
                    if p.texto.strip() == "/" and not validos:
                        traduccion = True  # «Total a pagar / Amount due»: rótulo bilingüe
                        continue
                    relleno = self._es_relleno(p, ref_h, ref_medio)
                    if _es_importe(p.texto) and not (relleno and p.conf < 0.5):
                        validos.append(p)
                    elif relleno:
                        if _es_importe(p.texto):
                            pequenas.append(p)
                        continue  # puntos guía entre el rótulo y la cifra («IVA 21% ……… 315,00 €»), también leídos por OCR
                    elif self.RE_PALABRA.search(p.texto) and not self.RE_NEUTRA.fullmatch(p.texto.strip()) and not traduccion:
                        cortada = True
                        break  # otro texto entre la etiqueta y la cifra: la cifra no es suya
                    resto.append(p)
                if validos:
                    p = validos[-1] if k + 1 == len(ets) else validos[0]
                    v = _num(p.texto)
                    if v is not None:
                        h = (e["clase"], v, [*e["pals"], p], f.pagina, f.texto, e["pct"], "linea")
                        consumidas.add(id(p))
                        if coma_decimal and p.metodo == "ocr" and RE_PUNTO_DECIMAL.fullmatch(_limpiar_cifra(p.texto)):
                            dudosos.append((h, p.texto, "punto"))   # «550.80» con coma decimal: coma leída como punto
                        else:
                            hechos.append(h)
                        continue
                elif pequenas and not cortada:
                    # la única cifra tras el rótulo es pequeña y poco legible (un total en letra grande con la cifra en
                    # letra normal): es su cifra, pero dudosa (_confirmar_dudosos)
                    p = pequenas[-1]
                    v = _num(p.texto)
                    if v is not None:
                        consumidas.add(id(p))
                        dudosos.append(((e["clase"], v, [*e["pals"], p], f.pagina, f.texto, e["pct"], "linea"), p.texto,
                                        "pequeña"))
                        continue
                elif not cortada:
                    # cifra con espacios en lugar de separadores en un escaneo («-1 23 50 €»)
                    esp = self._cifra_espaciada(resto)
                    if esp:
                        v, ps = esp
                        dudosos.append(((e["clase"], v, [*e["pals"], *ps], f.pagina, f.texto, e["pct"], "linea"),
                                        " ".join(q.texto for q in ps), "espaciada"))
                        consumidas.update(id(q) for q in ps)
                        continue
                pendientes.append(e)
            # Un rótulo dentro de una frase («Factura sujeta a retención a cuenta del IRPF del 15 % por …») no encabeza un
            # cuadro de importes: las cifras de debajo no son sus valores
            if pendientes:
                en_frase = self._rotulos_en_frase(ets, pals)
                pendientes = [e for e in pendientes if not en_frase(e)]
            if not pendientes:
                continue
            # Resumen traspuesto: un tipo de IVA por columna y una fila por concepto (base, cuota)
            tr = self._resumen_traspuesto(i, f, pendientes, consumidas)
            if tr:
                hechos += tr[0]
                pendientes = [e for e in pendientes if not any(e is u for u in tr[1])]
                if not pendientes:
                    continue
            if any(e["clase"] in ("base", "cuota") for e in pendientes):
                en_et = {id(p) for e in ets for p in e["pals"]}
                por_x = sorted(pals, key=lambda p: p.x0)
                tipos = []
                for k_p, p in enumerate(por_x):
                    if id(p) in en_et or not re.fullmatch(r"tipo|tipos|%|tipo\s*%|%\s*iva", p.texto.strip(), re.I):
                        continue
                    prev = por_x[k_p - 1] if k_p > 0 else None
                    if p.texto.strip() == "%" and prev is not None and self._es_valor(prev.texto) and p.x0 - prev.x1 < 8:
                        continue  # «2 %»: el porcentaje de otro rótulo («Dto. pronto pago 2 %»), no la columna del tipo
                    if self._signo_del_rotulo_iva(p, prev, por_x[k_p + 1] if k_p + 1 < len(por_x) else None,
                                                  {id(q): e for e in ets for q in e["pals"]}):
                        continue  # «IVA %»: el signo es del rótulo de su izquierda (lo trata _segmentos_cabecera)
                    sig = [e for e in ets if 0 <= e["x0"] - p.x1 < 15]
                    if sig:
                        continue  # «Tipo IVA», «% IVA»: ya lo trata el segmento de esa etiqueta
                    e_tipo = {"clase": "iva", "x0": p.x0, "x1": p.x1, "pals": [p], "pct": None, "texto": p.texto, "fin": 0, "tipo": True}
                    tipos.append(e_tipo)
                    pendientes.append(e_tipo)
                if tipos:
                    ets = sorted(ets + tipos, key=lambda e: e["x0"])
                if len(ets) > MAX_ETIQUETAS_FILA:
                    continue
            # Etiquetas sin cifra en su fila: cuadro con los valores en las filas siguientes, alineados por columnas
            segs = self._segmentos_cabecera(f, ets)
            ids_pend = {id(e) for e in pendientes}
            seg_pend = [sg for sg in segs if sg.get("et") is not None and id(sg["et"]) in ids_pend]
            if not seg_pend:
                continue
            x_min = min(sg["x0"] for sg in seg_pend) - 40
            x_max = max(sg["x1"] for sg in seg_pend) + 60
            lim_texto = max(sg["x1"] for sg in seg_pend) + 10
            # sin invadir la columna de una etiqueta ajena (otro cuadro en la misma fila)
            ids_seg_pend = {id(sg) for sg in seg_pend}
            fin_pend = max(x["x1"] for x in seg_pend)
            ajenas = [sg["x0"] for sg in segs if sg.get("et") is not None and id(sg) not in ids_seg_pend and sg["x0"] > fin_pend]
            if ajenas:
                x_max = min(x_max, min(ajenas) - 2)
            ant = f
            for f2 in filas[i + 1:i + 10]:
                if f2.pagina != f.pagina or f2.top - ant.bottom > 45:
                    break
                # los bloques de otro cuadro a la misma altura (p. ej. totales apilados a la derecha) no cuentan: los que
                # caen fuera del cuadro y los textos que empiezan a la derecha de su última columna rotulada
                zona = [p for b in f2.bloques if x_min <= (b.x0 + b.x1) / 2 <= x_max
                        and not (b.x0 > lim_texto and self.RE_PALABRA.search(b.texto)) for p in b.palabras
                        if x_min <= (p.x0 + p.x1) / 2 <= x_max and id(p) not in consumidas]
                # en la fila de justo debajo de los rótulos, un tramo de texto que empieza en el cuadro y se sale de él
                # («1.234,56 EURO (€)» bajo «TOTAL | MONEDA», leído junto): las cifras con que empieza, alineadas bajo una
                # columna rotulada, son de esa columna (se reparte por posición); el texto que las sigue no cuenta
                for b in (f2.bloques if ant is f else []):
                    if x_min <= (b.x0 + b.x1) / 2 <= x_max:
                        continue
                    for p in b.palabras:
                        if not self._es_valor(p.texto) or not any(sg["x0"] - 4 <= (p.x0 + p.x1) / 2 <= sg["x1"] + 4 for sg in seg_pend):
                            break
                        if id(p) not in consumidas:
                            zona.append(p)
                if not zona:
                    if ant is f:
                        break  # el cuadro empieza justo bajo sus rótulos
                    continue  # fila de otro cuadro intercalada entre las del cuadro, que sigue debajo
                ets2 = self._etiquetas_en(zona)
                vals = [p for p in zona if self._es_valor(p.texto) and p.texto.strip() != "%"]
                if not vals or len(vals) * len(segs) > MAX_CRUCES_CUADRO:
                    break  # sin cifras, o un cuadro desmesurado (documento hostil): coste acotado
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
                pct = pct_ret = None
                for p, sg in asign:
                    if sg["tipo"] and sg["clase"] in ("iva", "cuota"):
                        pct = _num(p.texto.replace("%", ""))
                    elif sg["tipo"] and sg["clase"] == "retencion" and id(sg.get("et")) in ids_pend:
                        pct_ret = _num(p.texto.replace("%", ""))  # columna «% IRPF» junto a la del importe retenido
                usados = False
                for p, sg in asign:
                    e = sg.get("et")
                    if e is None or id(e) not in ids_pend:
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
                    pct_h = pct if clase in ("base", "cuota") and pct is not None else e["pct"]
                    if clase == "retencion" and pct_h is None:
                        pct_h = pct_ret
                    hechos.append((clase, v, [*e["pals"], p], f.pagina, f.texto + " / " + f2.texto, pct_h, "columna"))
                for e in ets2:  # «IVA 10 %» en la columna del tipo de cada fila
                    for p in e["pals"]:
                        consumidas.add(id(p))
                if not usados:
                    break
                ant = f2
        # Cuadro de totales en renglones con puntos guía y sin rótulo de base («Importe alquiler ……… 650,00 €» encima de
        # «IVA 21% ……… 136,50 €»)
        hechos += self._bases_sin_rotulo(hechos, dudosos, consumidas, de_tabla)
        res = self._consolidar(hechos)
        if dudosos:
            res = self._confirmar_dudosos(res, hechos, dudosos)
        return self._cifras_sin_clasificar(res, hechos)

    def _rotulos_en_frase(self, ets: list[dict], pals: list[Palabra]):
        """Función que dice si una etiqueta va dentro de un texto corrido: su celda tiene muchas otras palabras que no son
        rótulos. Celdas y recuentos se calculan una vez por fila (coste lineal aunque la fila tenga muchas etiquetas)."""
        en_et = {id(p) for x in ets for p in x["pals"]}
        celda_de, otras = {}, []
        for k, c in enumerate(self._segmentos(pals)):
            otras.append(sum(1 for p in c if id(p) not in en_et and self.RE_PALABRA.search(p.texto)))
            for p in c:
                celda_de[id(p)] = k

        def en_frase(e: dict) -> bool:
            ks = [celda_de[id(p)] for p in e["pals"] if id(p) in celda_de]
            return bool(ks) and otras[min(ks)] >= 6
        return en_frase

    # --- renglones con puntos guía y lecturas OCR dudosas ------------------------------------------------------------
    def _coma_decimal(self) -> bool:
        """El documento escribe los importes con coma decimal («1.234,56»): mayoría de cifras con coma frente a las de punto
        decimal sin separador de miles («550.80»)."""
        coma = punto = 0
        for pg in self.paginas:
            for p in pg.palabras:
                t = _limpiar_cifra(p.texto)
                if RE_COMA_DECIMAL.fullmatch(t):
                    coma += 1
                elif RE_PUNTO_DECIMAL.fullmatch(t):
                    punto += 1
        return coma >= 2 and coma > punto

    @staticmethod
    def _altura_rotulo(pals: list[Palabra]) -> tuple[float, float]:
        """Altura y centro vertical (medianas) de las palabras de un rótulo: la referencia para reconocer el relleno."""
        if not pals:
            return 0.0, 0.0
        hs = sorted(p.bottom - p.top for p in pals)
        ms = sorted((p.top + p.bottom) / 2 for p in pals)
        return hs[len(hs) // 2], ms[len(ms) // 2]

    def _es_relleno(self, p: Palabra, alto_ref: float, medio_ref: float) -> bool:
        """Puntos guía entre un rótulo y su cifra. En un escaneo el OCR los lee como basura («oO” s. ..... ..«<.o»,
        «8888. 11.1 0£.0.»): tramos mucho más bajos que el rótulo y en su mitad inferior (la fila de puntos va sobre la línea
        base), con confianza baja si tienen letras, o tramos de confianza baja hechos sobre todo de puntos."""
        t = p.texto.strip()
        if not t:
            return False
        if RE_RELLENO.fullmatch(t):
            return True
        if p.metodo != "ocr":
            return False
        alto = p.bottom - p.top
        abajo = alto_ref > 0 and (p.top + p.bottom) / 2 > medio_ref   # en la mitad inferior del rótulo (sobre la línea base)
        bajo = abajo and alto < 0.6 * alto_ref
        if bajo and (p.conf < 0.6 or not self.RE_PALABRA.search(t)):
            return True
        puntos = t.count(".") + t.count("…") + t.count("·")
        if p.conf < 0.5 and puntos >= max(3, 0.4 * len(t)):
            return True
        # puntos leídos como letras pequeñas («...oo.ooposor.oooopo», «econo...»), sea cual sea la resolución del escaneo: confianza
        # casi nula, sobre la línea base, no más alto que el rótulo, con puntos y hecho de signos sin trazos altos
        return (abajo and p.conf < 0.3 and alto <= 1.05 * alto_ref and puntos >= 2 and len(t) >= 4
                and sum(c in CARACTERES_BAJOS for c in t) >= 0.75 * len(t))

    @staticmethod
    def _cifra_espaciada(pals: list[Palabra]) -> tuple[Decimal, list[Palabra]] | None:
        """Importe leído por OCR con espacios en lugar de separadores al final del tramo de un rótulo («-1 23 50 €»): grupos
        de cifras contiguos, el último de dos (los decimales). Es una lectura dudosa: solo se usa si cuadra con el resto."""
        j = len(pals)
        while j > 0 and RE_MONEDA.fullmatch(pals[j - 1].texto.strip()):
            j -= 1
        grupo: list[Palabra] = []
        while j > 0 and len(grupo) < 6:
            q = pals[j - 1]
            t = q.texto.strip().replace("−", "-")
            if not re.fullmatch(r"-?\d{1,3}|-", t) or (t.startswith("-") and not grupo):
                break
            if grupo and (not (0 <= grupo[0].x0 - q.x1 <= 1.0 * max(1.0, q.bottom - q.top)) or not _misma_linea(q, grupo[0])):
                break
            grupo.insert(0, q)
            j -= 1
            if t.startswith("-"):
                break  # el signo abre la cifra
        if len(grupo) < 2 or any(q.metodo != "ocr" for q in grupo):
            return None
        textos = [q.texto.strip().replace("−", "-") for q in grupo]
        signo = "-" if textos[0].startswith("-") else ""
        cifras = [x.lstrip("-") for x in textos if x.lstrip("-")]
        if len(cifras) < 2 or not re.fullmatch(r"\d{2}", cifras[-1]) or any(not re.fullmatch(r"\d{1,3}", x) for x in cifras[:-1]):
            return None
        try:
            v = Decimal(f"{signo}{''.join(cifras[:-1])}.{cifras[-1]}")
        except ArithmeticError:
            return None
        return (v, grupo) if abs(v) < IMPORTE_MAXIMO else None

    def _renglon_sin_rotulo(self, f: Fila, col: tuple[float, float], alto: float, consumidas: set[int]):
        """Renglón del cuadro de totales con un texto que no es un rótulo de importe conocido y una sola cifra al final,
        alineada con la columna de cifras del cuadro y separada del texto por puntos guía o por un hueco ancho («Importe
        alquiler ……… 745,20 €», «Mes octubre ……… 1.500,00 €»). Devuelve (cifra, palabras del texto, palabra de la cifra)."""
        pals = f.palabras
        if len(pals) > 40:
            return None
        letras = [p for p in pals if self.RE_PALABRA.search(p.texto) and (p.metodo != "ocr" or p.conf >= 0.5)]
        alto_ref, medio = self._altura_rotulo(letras)
        j = len(pals)
        while j > 0 and RE_MONEDA.fullmatch(pals[j - 1].texto.strip()):
            j -= 1
        if j == 0:
            return None
        a = pals[j - 1]
        if id(a) in consumidas or "%" in a.texto or not _es_importe(a.texto):
            return None
        v = _num(a.texto)
        if v is None or v == 0:
            return None
        if not (abs(a.x1 - col[1]) <= max(10.0, 1.5 * alto) or min(a.x1, col[1]) - max(a.x0, col[0]) > 0):
            return None
        antes = pals[:j - 1]
        texto = [p for p in antes if not self._es_relleno(p, alto_ref, medio)]
        relleno = len(texto) < len(antes)
        if not texto:
            return None
        # el texto de la celda más próxima a la cifra: lo de más a la izquierda es otra columna («Forma de pago: …»)
        rotulo = self._segmentos(texto)[-1]
        if len(rotulo) > 6 or not any(self.RE_PALABRA.search(p.texto) and (p.metodo != "ocr" or p.conf >= 0.5) for p in rotulo):
            return None   # sin un texto legible (solo basura del OCR) no es un renglón con rótulo
        if any(_es_importe(p.texto) for p in rotulo) or self._etiquetas_en(rotulo):
            return None   # otra cifra en el renglón, o un rótulo de importe conocido (lo trata importes)
        if _rotulo_de_impuesto(" ".join(p.texto for p in texto)):
            return None   # «Impuesto 21%», «Tax 21%», «lVA 21%» (OCR): un impuesto que no se reconoce, nunca la base
        if not relleno and a.x0 - max(p.x1 for p in rotulo) < 4 * alto:
            return None   # texto corrido que acaba en una cifra, no un renglón del cuadro
        return v, rotulo, a

    def _bases_sin_rotulo(self, hechos: list, dudosos: list, consumidas: set[int], de_tabla: set[int]) -> list:
        """Cuadro de totales en renglones con puntos guía sin rótulo de base (cartas de alquiler o de servicios): encima del
        primer importe con rótulo leído en su propio renglón (IVA, retención, total), los renglones contiguos con un texto
        que no es un rótulo conocido y una cifra alineada en la columna del cuadro son la base imponible (su suma). Solo si
        cuadra: base × tipo = cuota, o base + cuota (− retención) = total impreso; sin IVA en el cuadro, base = total (factura
        sin IVA repercutido, que la validación bloquea). Se pide confirmar salvo que cuadren a la vez el tipo y el total
        con cifras leídas con seguridad."""
        self._sin_iva_hecho = None
        if any(h[0] == "base" for h in hechos):
            return []
        anclas = [h for h in hechos if h[6] == "linea" and h[0] in ("iva", "cuota", "retencion", "total", "pagar")]
        anclas += [d[0] for d in dudosos if d[0][0] in ("iva", "cuota", "retencion", "total", "pagar")]
        if not anclas:
            return []
        fila_de = {}
        for k, f in enumerate(self.filas):
            for b in f.bloques:
                for p in b.palabras:
                    fila_de[id(p)] = k
        en_filas = sorted(((fila_de[id(h[2][-1])], h) for h in anclas if id(h[2][-1]) in fila_de), key=lambda x: x[0])
        if not en_filas:
            return []
        # importes rotulados apilados (cuadro de totales): el del IVA si lo hay; si no, el último cuadro de la página (un
        # «Importe total» en la cabecera no es el cuadro de totales)
        cuadros = [[en_filas[0]]]
        for x in en_filas[1:]:
            ant = self.filas[cuadros[-1][-1][0]]
            f = self.filas[x[0]]
            if f.pagina == ant.pagina and f.top - ant.bottom <= 6 * max(4.0, ant.bottom - ant.top):
                cuadros[-1].append(x)
            else:
                cuadros.append([x])
        cuadro = next((c for c in cuadros if any(h[0] in ("iva", "cuota") for _, h in c)), cuadros[-1])
        en_filas = cuadro
        k0, h0 = cuadro[0]
        f0, v0 = self.filas[k0], h0[2][-1]
        alto = max(4.0, v0.bottom - v0.top)
        col = (v0.x0, v0.x1)
        hueco = self._hueco_del_cuadro(cuadro, alto)
        renglones, ref, ini, parada = [], f0, k0, None
        for k in range(k0 - 1, -1, -1):
            f = self.filas[k]
            if f.pagina != f0.pagina or id(f) in de_tabla or len(renglones) >= MAX_RENGLONES_BASE:
                break
            if f.bottom > ref.top + 0.5 * alto:
                continue   # a la misma altura que el renglón de debajo (otra columna)
            if ref.top - f.bottom > hueco:
                break
            r = self._renglon_sin_rotulo(f, col, alto, consumidas)
            if r is None:
                parada = k   # el renglón que cierra el cuadro por arriba (un rótulo de impuesto, el párrafo…)
                break
            renglones.insert(0, (f, *r))
            ini, ref = k, f
        if not renglones:
            return []
        suma = sum((r[1] for r in renglones), Decimal(0))
        ivas = [h for h in hechos if h[0] in ("iva", "cuota")]
        pcts = [h for h in hechos if h[0] == "pct"]
        totales = [h[1] for h in hechos if h[0] in ("total", "pagar")]
        rets = [abs(h[1]) for h in hechos if h[0] == "retencion"]
        if len(ivas) > 1:
            return []   # varios tipos: la base de cada uno no se deduce de unos renglones sin rótulo
        if ivas:
            hc = ivas[0]
            t = hc[5] if hc[5] is not None else (pcts[0][1] if len(pcts) == 1 else None)
            por_tipo = t is not None and q2(suma * t / 100) == q2(hc[1])
            bruto = q2(suma + hc[1])
            con_total = [h for h in hechos if h[0] in ("total", "pagar")
                         and (q2(h[1]) == bruto or any(q2(h[1]) == q2(bruto - r) for r in rets))]
            por_total = bool(con_total)
            if not (por_tipo or por_total):
                return []
            # doble comprobación (tipo × base = cuota y base + cuota = total) con cifras leídas con seguridad: no hace falta
            # confirmar; con una sola, o con cifras OCR dudosas, se pide confirmar
            seguras = all(min(p.conf for p in h[2][-1:]) >= UMBRAL_SEGURO for h in [hc, *con_total])
            conf = 0.85 if por_tipo and por_total and seguras else 0.8
            motivo = ("cuadra con el tipo y la cuota de IVA y con el total impreso" if por_tipo and por_total else
                      "cuadra con el tipo y la cuota de IVA" if por_tipo else "cuadra con la cuota y el total impreso")
        else:
            # sin IVA: ningún rótulo de IVA en el cuadro, ni siquiera sin cifra legible, y un total impreso igual a la base
            # (o a la base menos la retención)
            fin = max(k for k, _ in en_filas)
            if pcts or any(e["clase"] in ("iva", "cuota") for f in self.filas[ini:fin + 1] for e in self._etiquetas_en(f.palabras)):
                return []
            if self._impuesto_en_el_cuadro(parada if parada is not None else ini, fin, hueco):
                return []   # un renglón de impuesto sin rótulo conocido («Impuesto 21%», «Tax»), o el total lleva el IVA incluido
            if not any(q2(x) == q2(suma) or any(q2(x) == q2(suma - r) for r in rets) for x in totales):
                return []
            conf, motivo = 0.8, "es igual al total impreso y el documento no imprime IVA"
        rotulos = "», «".join(" ".join(p.texto for p in r[2]) for r in renglones)
        h = ("base", suma, [p for r in renglones for p in [*r[2], r[3]]], f0.pagina, " / ".join(r[0].texto for r in renglones),
             None, "sin_rotulo")
        self._marcas_hecho[id(h)] = (conf, f"Base imponible leída {'de los renglones' if len(renglones) > 1 else 'del renglón'} "
                                           f"sin rótulo de base «{rotulos}» del cuadro de totales: {motivo}"
                                           + ("" if conf >= 0.85 else "; confírmela"))
        if not ivas:
            self._sin_iva_hecho = h
        return [h]

    def _hueco_del_cuadro(self, cuadro: list, alto: float) -> float:
        """Separación máxima entre dos renglones del cuadro de totales: un renglón en blanco. Con el paso entre los renglones
        con rótulo del cuadro si tiene varios; si no, seis alturas de la cifra (en un escaneo la caja del OCR solo cubre la
        altura de las cifras, no la del cuerpo de letra; con un cuerpo de 9 puntos o un interlineado amplio, un renglón en
        blanco ocupa más de cuatro alturas)."""
        lim = 6 * alto
        tops = [self.filas[k].top for k, _ in cuadro]
        pasos = [b - a for a, b in zip(tops, tops[1:]) if b - a > alto]
        if pasos:
            lim = max(lim, min(2.2 * min(pasos) - alto, 10 * alto))
        return lim

    def _impuesto_en_el_cuadro(self, k_ini: int, k_fin: int, hueco: float) -> bool:
        """El cuadro de totales (renglones k_ini..k_fin y los pegados por debajo) imprime un impuesto que no se reconoce como
        rótulo de IVA («Impuesto 21%», «Tax 21 %», «lVA 21%» leído por OCR, «I.V.A. al 21 por 100»), o la página dice que
        el total incluye el IVA. Una exención declarada («exenta de IVA») no cuenta: es compatible con la factura sin IVA."""
        filas = list(self.filas[k_ini:k_fin + 1])
        ult = self.filas[k_fin]
        for f in self.filas[k_fin + 1:k_fin + 4]:
            if f.pagina != ult.pagina or f.top - ult.bottom > hueco:
                break
            filas.append(f)
            ult = f
        for f in filas:
            t = " ".join(p.texto for p in f.palabras)
            for e in self._etiquetas_en(f.palabras):
                if e["clase"] == "recargo":
                    return True   # el recargo de equivalencia va con el IVA
                if e["clase"] in ("retencion", "garantia"):
                    # el porcentaje de la retención («Retención 19%») no es un impuesto repercutido
                    t = re.sub(re.escape(e["texto"]) + r"(?:[\s(]*(?:al|del|a|de)?\s*\d{1,2}(?:[.,]\d{1,2})?\s*%\)?)?", " ", t,
                               count=1, flags=re.I)
            if _rotulo_de_impuesto(t) and not RE_SIN_IVA_DECLARADO.search(t):
                return True
        pagina = self.filas[k_fin].pagina
        return any(RE_IVA_INCLUIDO.search(l.texto) for l in self.lineas if l.pagina == pagina)

    def _cuadra_dudoso(self, h: tuple, res: dict) -> bool:
        """Una lectura OCR dudosa cuadra con los importes ya leídos: retención = base × % (o, sin porcentaje legible, base +
        cuota − retención = el importe a pagar impreso), cuota = base × tipo, total = base + cuota (− retención), base × tipo
        = cuota."""
        def val(campo):
            v = (campo or {}).get("valor") if isinstance(campo, dict) else None
            try:
                return Decimal(v) if v is not None else None
            except ArithmeticError:
                return None
        base, cuota = val(res.get("base_imponible")), val(res.get("cuota_iva"))
        ret = val((res.get("retencion") or {}).get("importe"))
        rec = val(res.get("recargo_equivalencia")) or Decimal(0)
        tipos = [val(d["tipo_iva"]) for d in res.get("desglose") or []]
        clase, v, pct = h[0], h[1], h[5]
        if clase == "retencion":
            if base is not None and pct is not None and q2(base * pct / 100) == q2(abs(v)):
                return True
            # sin porcentaje («Retención IRPF -150.00»): el líquido impreso (no calculado) es base + cuota − retención
            impresos = [val(c) for c in (res.get("total_a_pagar"), res.get("total_factura"))
                        if isinstance(c, dict) and c.get("metodo") != "calculado"]
            return (base is not None and cuota is not None
                    and any(x is not None and q2(x) == q2(base + cuota + rec - abs(v)) for x in impresos))
        if clase in ("iva", "cuota"):
            t = pct if pct is not None else (tipos[0] if len(tipos) == 1 else None)
            return base is not None and t is not None and q2(base * t / 100) == q2(v)
        if clase in ("total", "pagar"):
            if base is None or cuota is None:
                return False
            bruto = q2(base + cuota + rec)
            return q2(v) == bruto or (ret is not None and q2(v) == q2(bruto - abs(ret)))
        if clase == "base":
            return cuota is not None and len(tipos) == 1 and tipos[0] is not None and q2(v * tipos[0] / 100) == q2(cuota)
        return False

    @staticmethod
    def _lectura_dudosa(leido: str, tipo: str) -> str:
        if tipo == "pequeña":
            return f"Cifra leída por OCR con poca seguridad («{leido}», más pequeña que su rótulo)"
        return f"Cifra leída por OCR con un formato dudoso («{leido}»)"

    def _confirmar_dudosos(self, res: dict, hechos: list, dudosos: list) -> dict:
        """Añade las lecturas OCR dudosas que cuadran con los demás importes (con confianza baja y un aviso) y vuelve a
        consolidar. Las de punto decimal y las cifras pequeñas poco legibles que no cuadran se conservan con confianza más
        baja y un aviso de que no cuadran (la cifra impresa puede ser esa y la factura estar descuadrada: la validación lo
        señala; no se sustituye por un cálculo). Las cifras con espacios que no cuadran se descartan (quedan como cifras sin
        interpretar): agrupar los dígitos así solo es una lectura si la confirma la aritmética."""
        pendientes = list(dudosos)
        for _ in range(3):   # una lectura confirmada puede confirmar otra (la retención, y con ella el total líquido)
            aceptados = [d for d in pendientes if self._cuadra_dudoso(d[0], res)]
            if not aceptados:
                break
            for h, leido, tipo in aceptados:
                hechos.append(h)
                v = q2(abs(h[1]) if h[0] == "retencion" else h[1])
                self._marcas_hecho[id(h)] = (0.75, f"{self._lectura_dudosa(leido, tipo)}: se toma como {v} porque cuadra con los "
                                                   "demás importes; confírmela en el original")
            ids = {id(d) for d in aceptados}
            pendientes = [d for d in pendientes if id(d) not in ids]
            res = self._consolidar(hechos)
        sueltos = [d for d in pendientes if d[2] in ("punto", "pequeña")]
        if sueltos:
            for h, leido, tipo in sueltos:
                hechos.append(h)
                v = q2(abs(h[1]) if h[0] == "retencion" else h[1])
                self._marcas_hecho[id(h)] = (0.6, f"{self._lectura_dudosa(leido, tipo)}: se toma como {v}, pero no cuadra con "
                                                  "los demás importes; compruébela en el original")
            res = self._consolidar(hechos)
        return res

    def _resumen_traspuesto(self, i: int, f: Fila, pendientes: list[dict], consumidas: set[int]):
        """Resumen de IVA traspuesto: una columna por tipo («IVA 4 % | IVA 10 % | IVA 21 % | Totales») y una fila por concepto
        («Base imponible», «Cuota IVA») con su rótulo a la izquierda y las cifras bajo cada tipo.
        Devuelve (hechos, etiquetas de la cabecera usadas) o None si la fila no es la cabecera de un resumen así."""
        tasas = sorted((e for e in pendientes if e["clase"] == "iva" and e["pct"] is not None), key=lambda e: e["x0"])
        if len(tasas) < 2 or len({e["pct"] for e in tasas}) < len(tasas):
            return None
        pals = sorted(f.palabras, key=lambda p: p.x0)
        cols = []
        for e in tasas:
            x1 = e["x1"]
            for p in pals:  # el tipo impreso tras el rótulo («4 %», «10,00%»)
                if p.x0 >= x1 - 1 and p.x0 - x1 < 12 and (self._es_valor(p.texto) or p.texto.strip() == "%"):
                    x1 = p.x1
            cols.append({"x0": e["x0"], "x1": x1, "pct": e["pct"], "et": e})
        # columna de totales del resumen: una etiqueta de total justo a la derecha del último tipo, a la distancia de una columna
        paso = max(b["x0"] - a["x1"] for a, b in zip(cols, cols[1:]))
        tot = next((e for e in sorted(pendientes, key=lambda e: e["x0"]) if e["x0"] > cols[-1]["x1"]), None)
        if tot is not None and tot["clase"] == "total" and tot["x0"] - cols[-1]["x1"] <= 1.5 * paso + 10:
            cols.append({"x0": tot["x0"], "x1": tot["x1"], "pct": None, "et": tot})
        clases = {"base": "base", "cuota": "cuota", "iva": "cuota"}
        hechos, ant = [], f
        for f2 in self.filas[i + 1:i + 8]:
            if f2.pagina != f.pagina or f2.top - ant.bottom > 45:
                break
            rot = [e for e in self._etiquetas_en(f2.palabras) if e["x1"] <= cols[0]["x0"] + 5
                   and not any(id(p) in consumidas for p in e["pals"])]
            if not rot or rot[0]["clase"] not in clases:
                if any(cols[0]["x0"] - 60 <= (p.x0 + p.x1) / 2 <= cols[-1]["x1"] + 10 for p in f2.palabras):
                    break  # fin del resumen
                continue  # fila de otro cuadro a la misma altura
            fila = []
            for c in cols:
                al = [p for p in f2.palabras if _es_importe(p.texto) and id(p) not in consumidas and "%" not in p.texto
                      and (c["x0"] - 10 <= (p.x0 + p.x1) / 2 <= c["x1"] + 10 or abs(p.x1 - c["x1"]) <= 6)]
                if len(al) == 1 and _num(al[0].texto) is not None:
                    fila.append((c, al[0]))
            if sum(1 for c, _ in fila if c["pct"] is not None) < 2:
                break
            clase = clases[rot[0]["clase"]]
            for c, p in fila:
                hechos.append((clase, _num(p.texto), [*rot[0]["pals"], *c["et"]["pals"], p], f.pagina, f.texto + " / " + f2.texto,
                               c["pct"], "traspuesto"))
                consumidas.add(id(p))
            consumidas.update(id(p) for p in rot[0]["pals"])
            ant = f2
        if not hechos:
            return None
        return hechos, [c["et"] for c in cols]

    def _cifras_sin_clasificar(self, res, hechos):
        """Con el total sin leer, anota las cifras del cuadro de totales que no se han asignado a ninguna etiqueta (un total
        con un rótulo no reconocido, una tasa, unos portes…): el total no puede deducirse entonces como base + cuota.
        Con el total leído, anota las cifras sin asignar impresas bajo él (_cifras_bajo_total)."""
        if not hechos:
            return res
        if (res.get("total_factura") or {}).get("valor") is not None:
            return self._cifras_bajo_total(res, hechos)
        usadas = {id(p) for h in hechos for p in h[2]}
        idx = [i for i, f in enumerate(self.filas) if any(id(p) in usadas for p in f.palabras)]
        if not idx:
            return res
        fin = idx[-1]
        while fin + 1 < len(self.filas) and self.filas[fin + 1].pagina == self.filas[fin].pagina \
                and self.filas[fin + 1].top - self.filas[fin].bottom <= 25 \
                and any(_es_importe(p.texto) for p in self.filas[fin + 1].palabras):
            fin += 1  # filas contiguas con cifras bajo la última etiqueta reconocida
        de_tabla = getattr(self, "_ids_filas_tabla", set())
        cifras = []
        for f in self.filas[idx[0]:fin + 1]:
            if id(f) in de_tabla:
                continue
            for p in f.palabras:
                if id(p) in usadas or not _es_importe(p.texto) or "%" in p.texto:
                    continue
                v = _num(p.texto)
                if v is not None and Decimal(0) < v < Decimal("1e12"):
                    cifras.append(str(q2(v)))
        if cifras:
            res["total_factura"]["sin_clasificar"] = cifras
            res["total_factura"]["avisos"].append("Cifras sin interpretar en el cuadro de totales: " + "; ".join(cifras))
        return res

    def _cifras_bajo_total(self, res, hechos):
        """Cifras sin asignar a ninguna etiqueta en las filas contiguas bajo el total leído, en la zona del cuadro de totales
        y distintas de los importes ya reconocidos: p. ej. «TOTAL 1.210,00 / Tasa 30,00 / IMPORTE 1.240,00». El total leído
        puede ser un subtotal y el importe de la factura otro con un rótulo no reconocido: la validación pide confirmarlo.
        Se anotan solo si alguna es mayor que el total."""
        try:
            tv = q2(Decimal(res["total_factura"]["valor"]))
        except (TypeError, ValueError, ArithmeticError):
            return res
        hs = [h for h in hechos if h[0] in ("total", "pagar") and h[1] is not None and q2(h[1]) == tv]
        if not hs:
            return res  # total calculado o deducido: no hay fila de total impresa de la que partir
        ids_valor = {id(h[2][-1]) for h in hs}
        filas_tot = [i for i, f in enumerate(self.filas) if any(id(p) in ids_valor for p in f.palabras)]
        if not filas_tot:
            return res
        usadas = {id(p) for h in hechos for p in h[2]}
        conocidas = {q2(abs(h[1])) for h in hechos if h[1] is not None}
        for k in ("base_imponible", "cuota_iva", "total_a_pagar"):
            v = _num(str((res.get(k) or {}).get("valor") or ""))
            if v is not None:
                conocidas.add(q2(abs(v)))
        v_ret = _num(str(((res.get("retencion") or {}).get("importe") or {}).get("valor") or ""))
        if v_ret is not None:
            conocidas |= {q2(abs(v_ret)), q2(tv - abs(v_ret))}
        x_min = min(p.x0 for h in hs for p in h[2]) - 40  # columna del cuadro: desde el rótulo del total
        de_tabla = getattr(self, "_ids_filas_tabla", set())
        cifras, i = [], filas_tot[-1]
        while i + 1 < len(self.filas) and len(cifras) < 20:
            f, ant = self.filas[i + 1], self.filas[i]
            if f.pagina != ant.pagina or f.top - ant.bottom > 25 or id(f) in de_tabla \
                    or not any(_es_importe(p.texto) for p in f.palabras):
                break  # fin del cuadro: hueco, otra página, la tabla o una fila sin cifras
            for p in f.palabras:
                if id(p) in usadas or p.x0 < x_min or "%" in p.texto or not _es_importe(p.texto):
                    continue
                v = _num(p.texto)
                if v is not None and Decimal(0) < v < Decimal("1e12") and q2(v) not in conocidas:
                    cifras.append(str(q2(v)))
            i += 1
        # solo cuenta si alguna supera el total (un anticipo o un pendiente de pago menores no lo cuestionan)
        if any(Decimal(x) > tv for x in cifras):
            res["total_factura"]["bajo_total"] = cifras
            res["total_factura"]["avisos"].append("Bajo el total se imprimen cifras sin interpretar, alguna mayor que él: "
                                                  + "; ".join(cifras))
            return res
        # En cualquier otro sitio del documento (cuadro horizontal, fila de texto intermedia, columna izquierda, cabecera…):
        # una cifra sin asignar que es el total leído más otra (o dos) cifras sin asignar («TOTAL 1.210 · TASA 30 ·
        # IMPORTE 1.240»): el total leído es un subtotal y el importe de la factura lleva un rótulo no reconocido
        sueltas = []
        for f in self.filas:
            if id(f) in de_tabla or len(sueltas) >= 200:
                continue
            for p in f.palabras:
                if id(p) in usadas or "%" in p.texto or not _es_importe(p.texto):
                    continue
                v = _num(p.texto)
                if v is not None and Decimal(0) < v < Decimal("1e12") and q2(v) not in conocidas:
                    sueltas.append(q2(v))
        cuenta = {}
        for v in sueltas:
            cuenta[v] = cuenta.get(v, 0) + 1
        for v in sorted(cuenta, reverse=True):
            if v <= tv:
                break
            d = v - tv
            sumandos = [d] if d in cuenta else next(([a, d - a] for a in cuenta if Decimal(0) < a < d and d - a in cuenta
                                                     and (d - a != a or cuenta[a] > 1)), None)
            if sumandos:
                res["total_factura"]["suma_otra"] = {"cifra": str(v), "sumandos": [str(a) for a in sumandos]}
                res["total_factura"]["avisos"].append(
                    f"El documento imprime {v}, que es el total leído más {' + '.join(str(a) for a in sumandos)} (cifras sin "
                    "interpretar)")
                break
        return res

    def _consolidar(self, hechos):
        res = {"desglose": [], "base_imponible": None, "cuota_iva": None, "tipo_iva": None, "total_factura": None,
               "total_a_pagar": None, "retencion": None, "recargo_equivalencia": None}

        marcas = getattr(self, "_marcas_hecho", None) or {}

        def c(h, valor=None, aviso=None, conf=None):
            campo = _campo(str(q2(valor if valor is not None else h[1])), texto=h[4], palabras=h[2], pagina=h[3], aviso=aviso,
                           confianza=conf)
            marca = marcas.get(id(h))
            if marca:   # leído de forma indirecta (renglón sin rótulo, cifra OCR dudosa): se pide confirmar
                campo["confianza"] = min(campo["confianza"], marca[0])
                campo["avisos"].append(marca[1])
            return campo
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
        # Cuota calculada línea a línea (difiere en céntimos de base × tipo): se empareja con la única base libre que cuadra
        # con esa tolerancia y queda marcada como descuadre, para que la validación la confirme
        for d, hi in zip(desglose, tipos_iva):
            if not d["_ok"]:
                cerca = [hb for hb in bases if id(hb) not in usados and abs(q2(hb[1] * hi[5] / 100) - q2(hi[1])) <= Decimal("0.05")]
                if len(cerca) == 1:
                    usados.add(id(cerca[0]))
                    d["base"] = c(cerca[0])
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
                if len({d["tipo_iva"]["valor"] for d in desglose}) > 1 and (id(bases[0]) in usados or bases[0][5] is not None):
                    # varios tipos y una sola base impresa, la de uno de ellos (con su %, o base × tipo = su cuota): las de los
                    # otros tipos no se han leído (rótulo no reconocido) y la de la factura no es esta
                    res["base_imponible"]["confianza"] = min(res["base_imponible"]["confianza"], 0.7)
                    res["base_imponible"]["avisos"].append("Varios tipos de IVA y una sola base impresa, la de uno de ellos: "
                                                           "confirme la base imponible")
            else:
                # una base «total» que coincide con la suma de las del desglose (completo, y con varios tipos distinta de las
                # de cada tipo), o la última impresa
                completo = bool(desglose) and all(d["base"]["valor"] for d in desglose)
                suma = sum((Decimal(d["base"]["valor"]) for d in desglose if d["base"]["valor"]), Decimal(0))
                igual = [b for b in bases if completo and q2(b[1]) == q2(suma) and (len(desglose) == 1 or id(b) not in usados)]
                if not igual and not no_usadas and len(desglose) > 1 and completo:
                    # varios tipos sin «total base» impreso: todas las bases son las del desglose; la base es su suma
                    res["base_imponible"] = _campo(str(q2(suma)), metodo="calculado", confianza=0.9, aviso="Suma de las bases del desglose")
                else:
                    elegida = igual[-1] if igual else (no_usadas[-1] if no_usadas else bases[-1])
                    res["base_imponible"] = c(elegida)
                    if len(desglose) > 1 and not igual:
                        # varios tipos y ninguna base impresa es la suma del desglose: la elegida puede ser la de un tipo
                        res["base_imponible"]["confianza"] = min(res["base_imponible"]["confianza"], 0.7)
                        res["base_imponible"]["avisos"].append("Varios tipos de IVA y ninguna base impresa coincide con la suma "
                                                               "del desglose: confirme la base imponible")
        if desglose and len(desglose) == 1 and desglose[0]["base"]["valor"] is None and res["base_imponible"]:
            desglose[0]["base"] = dict(res["base_imponible"])
            desglose[0]["_ok"] = q2(Decimal(desglose[0]["base"]["valor"]) * Decimal(desglose[0]["tipo_iva"]["valor"]) / 100) == \
                q2(Decimal(desglose[0]["cuota"]["valor"]))
        if not res["base_imponible"] and desglose and all(d["base"]["valor"] for d in desglose):
            res["base_imponible"] = _campo(str(q2(sum(Decimal(d["base"]["valor"]) for d in desglose))), metodo="calculado",
                                           confianza=0.85, aviso="Suma de las bases del desglose")
        oks = [d.pop("_ok", True) for d in desglose]
        for d, ok in zip(desglose, oks):
            if not ok:
                d["cuota"]["avisos"].append("La cuota no cuadra con base × tipo")
        res["desglose"] = desglose
        if desglose:
            res["tipo_iva"] = desglose[0]["tipo_iva"]
            cuotas = [Decimal(d["cuota"]["valor"]) for d in desglose]
            res["cuota_iva"] = desglose[0]["cuota"] if len(cuotas) == 1 else _campo(
                str(q2(sum(cuotas))), metodo="calculado", confianza=0.9, aviso="Suma de cuotas por tipo")
        sin_iva = getattr(self, "_sin_iva_hecho", None)
        sin_iva = sin_iva is not None and not ivas and not pcts and any(h is sin_iva for h in bases)
        if sin_iva:
            # el cuadro de totales no imprime IVA y el total es la base: cuota cero, sin desglose (la validación lo bloquea:
            # las operaciones exentas o no sujetas no están soportadas)
            res["cuota_iva"] = _campo("0.00", metodo="calculado", confianza=0.8,
                                      aviso="El documento no imprime IVA: el total es igual a la base imponible (sin IVA "
                                            "repercutido); confírmelo")
            res["tipo_iva"] = _vacio("El documento no imprime IVA")
        totales = por.get("total", [])
        if totales:
            # el total que cuadra con base + cuota; si no, el mayor impreso
            esperado = None
            if res["base_imponible"] and res["cuota_iva"]:
                esperado = q2(Decimal(res["base_imponible"]["valor"]) + Decimal(res["cuota_iva"]["valor"]) +
                              sum((h[1] for h in por.get("recargo", [])[-1:]), Decimal(0)))
            cuadra = [h for h in totales if esperado is not None and q2(h[1]) == esperado]
            # «TOTAL» = base + cuota − retención (plantillas de profesionales): es el líquido a pagar, no el total de la factura
            liquido = [h for h in totales if not cuadra and esperado is not None and por.get("retencion")
                       and q2(h[1]) == q2(esperado - abs(por["retencion"][-1][1]))]
            if liquido:
                res["total_factura"] = _campo(str(esperado), metodo="calculado", confianza=0.85,
                                              aviso=f"Calculado como base + cuota: el total impreso ({q2(liquido[-1][1])}) descuenta la "
                                                    f"retención y es el importe a pagar")
                # la cifra impresa reinterpretada: borradores.validar la muestra en la confirmación de la retención
                res["total_factura"]["liquido_impreso"] = str(q2(liquido[-1][1]))
                if not por.get("pagar"):
                    res["total_a_pagar"] = c(liquido[-1], aviso="El total impreso descuenta la retención: es el importe a pagar")
            else:
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
        if not self._coherente(res) and not sin_iva:
            etiquetados = {k: {q2(h[1]) for h in por.get(k, [])} for k in ("base", "total")}
            etiquetados["cuota"] = {q2(h[1]) for h in ivas}
            alt = self._subconjunto_coherente(desglose, oks, por, c, res) or self._resolver_aritmetica(etiquetados)
            if alt:
                res.update(alt)
        if ret and not totales and not pagar and res["base_imponible"] and res["cuota_iva"] \
                and res["base_imponible"].get("valor") is not None and res["cuota_iva"].get("valor") is not None:
            # con retención y sin total legible (un escaneo), el importe a pagar es base + cuota − retención: se calcula
            # para que no se tome el total de la factura (base + cuota) como importe a pagar; se pide confirmar
            neto = q2(Decimal(res["base_imponible"]["valor"]) + Decimal(res["cuota_iva"]["valor"]) +
                      sum((x[1] for x in rec[-1:]), Decimal(0)) - abs(ret[-1][1]))
            res["total_a_pagar"] = _campo(str(neto), metodo="calculado", confianza=0.8,
                                          aviso="No se ha leído el total impreso: importe a pagar calculado como base + "
                                                "cuota − retención; compruébelo en el original")
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

    def _subconjunto_coherente(self, desglose, oks, por, c, res):
        """Los importes con etiqueta no cuadran todos juntos, pero sí una parte: filas del desglose con base × tipo = cuota
        cuya base + cuota es un total impreso (p. ej. una factura de anticipo que reproduce el presupuesto aceptado encima
        de sus propios totales). Es más fiable que buscar cifras sueltas; se marca para confirmar."""
        filas = [d for d, ok in zip(desglose, oks) if ok and d["base"]["valor"]]
        totales = por.get("total", [])
        if not filas or not totales or len(filas) > 6:
            return None
        rec = sum((h[1] for h in por.get("recargo", [])[-1:]), Decimal(0))
        sols = []
        for n in range(len(filas), 0, -1):
            for sub in itertools.combinations(filas, n):
                b = q2(sum(Decimal(d["base"]["valor"]) for d in sub))
                cu = q2(sum(Decimal(d["cuota"]["valor"]) for d in sub))
                sols += [(sub, h, b, cu) for h in totales if q2(h[1]) == q2(b + cu + rec)]
            if sols:
                break  # la combinación con más filas
        if len({(s[2], s[3], q2(s[1][1])) for s in sols}) > 1:
            # varias combinaciones: solo si una sola tiene el rótulo explícito del total de la factura
            sols = [s for s in sols if re.search(r"total\s+(?:de\s+(?:la\s+)?)?factura|importe\s+total|total\s+documento|invoice\s+total",
                                                 " ".join(p.texto for p in s[1][2][:-1]), re.I)]
            if len({(s[2], s[3], q2(s[1][1])) for s in sols}) != 1:
                return None
        if not sols:
            return None
        sub, ht, b, cu = sols[-1]
        actual = tuple(res[k]["valor"] if res[k] else None for k in ("base_imponible", "cuota_iva", "total_factura"))
        if len(sub) == len(desglose) and actual == (str(b), str(cu), str(q2(ht[1]))):
            return None  # es lo ya elegido (p. ej. con recargo de equivalencia, el total incluye el recargo)
        propios = {b, cu, q2(ht[1])} | {q2(Decimal(d[k]["valor"])) for d in sub for k in ("base", "cuota")}
        otros = sorted({q2(h[1]) for k in ("base", "iva", "cuota", "total") for h in por.get(k, [])} - propios)
        aviso = (f"Hay otros importes con etiqueta que no cuadran con esta combinación ({', '.join(str(x) for x in otros[:4])}): "
                 f"confirme cuáles son los de esta factura" if otros else
                 "Se descartan filas del desglose repetidas o incompletas que no cuadran con el total: confirme")

        def marcar(campo):
            campo = dict(campo, avisos=list(campo.get("avisos") or []) + [aviso])
            campo["confianza"] = min(campo["confianza"], 0.8)
            return campo
        sub = list(sub)
        base = sub[0]["base"] if len(sub) == 1 else _campo(str(b), metodo="calculado", confianza=0.9, aviso="Suma de las bases del desglose")
        cuota = sub[0]["cuota"] if len(sub) == 1 else _campo(str(cu), metodo="calculado", confianza=0.9, aviso="Suma de cuotas por tipo")
        return {"desglose": sub, "tipo_iva": sub[0]["tipo_iva"], "base_imponible": marcar(base), "cuota_iva": marcar(cuota),
                "total_factura": marcar(c(ht))}

    def _resolver_aritmetica(self, etiquetados: dict | None = None):
        """Último recurso: busca en la mitad inferior una terna impresa (base, cuota, total) con un tipo vigente. Entre varias,
        prefiere la que coincide con importes que tienen etiqueta (total, base, cuota) y, después, la mayor."""
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
        et = etiquetados or {}
        ternas = []
        for t in (Decimal("21"), Decimal("10"), Decimal("4"), Decimal("5"), Decimal("2")):
            for b in valores:
                cu = q2(b * t / 100)
                if cu in valores and q2(b + cu) in valores and cu > 0:
                    tot = q2(b + cu)
                    rotulos = (tot in et.get("total", ()), b in et.get("base", ()), cu in et.get("cuota", ()))
                    ternas.append(((rotulos[0], sum(rotulos), tot), (tot, b, cu, t)))
        if not ternas:
            return None
        ternas.sort(key=lambda x: x[0], reverse=True)
        tot, b, cu, t = ternas[0][1]
        aviso = "Deducido por coherencia aritmética (base × tipo = cuota; base + cuota = total): confirme"
        alternativas = sorted({x[1][0] for x in ternas[1:]} - {tot}, reverse=True)
        if alternativas:
            aviso += f". Hay otras combinaciones coherentes impresas (total {', '.join(str(x) for x in alternativas[:3])})"

        def cc(v):
            p, l = valores[q2(v)]
            return _campo(str(q2(v)), texto=l.texto, palabras=[p], pagina=l.pagina, confianza=0.7, aviso=aviso)
        return {"base_imponible": cc(b), "cuota_iva": cc(cu), "total_factura": cc(tot),
                "tipo_iva": _campo(f"{t:.2f}", metodo="calculado", confianza=0.7, aviso="Tipo deducido por coherencia aritmética"),
                "desglose": [{"tipo_iva": _campo(f"{t:.2f}", metodo="calculado", confianza=0.7, aviso="Tipo deducido"),
                              "base": cc(b), "cuota": cc(cu)}]}

    # --- referencias y pago ----------------------------------------------------------------------------------------------
    def referencias(self):
        defs = {
            "obra": (r"^obra\b|\bobra\s*:|\bproyecto\s+de\s+obra", self._v_texto),
            "certificacion": (r"certificaci[oó]n\s*(?:n(?:\.?[º°]|[o*])\.?|n[úu]m\.?|n[úu]mero)?", self._v_entero),
            "pedido": (r"(?:n(?:\.?[º°]|[úu*])\.?\s*(?:de\s+)?|n[úu]mero\s+de\s+)?pedido(?:\s+" + NUM_ABR + r")?|orden\s+de\s+compra|purchase\s+order|\bp\.?o\.?\s*n", self._v_codigo),
            "partida_presupuestaria": (r"partida(?:\s+presupuestaria)?", self._v_codigo),
            "periodo": (r"per[ií]odo(?:\s+(?:facturado|de\s+facturaci[oó]n))?", self._v_texto),
            "contrato": (r"(?:" + NUM_ABR + r"\s*(?:de\s+)?)?contrato(?:\s+" + NUM_ABR + r")?", self._v_codigo),
            "proyecto": (r"^(?:proyecto|project)\b(?!\s+de\s+obra)", self._v_texto),
            "expediente": (r"(?:" + NUM_ABR + r"\s*(?:de\s+)?)?expediente(?:\s+" + NUM_ABR + r")?", self._v_codigo),
            "albaran": (r"albar[aá]n(?:\s+" + NUM_ABR + r")?", self._v_codigo),
            "responsable": (r"^responsable\b", self._v_texto),
        }
        out = {}
        for k, (et, val) in defs.items():
            cands = self.candidatos(et, val, abajo=k in ("certificacion", "pedido", "contrato", "partida_presupuestaria", "expediente",
                                                                  "proyecto", "periodo", "albaran"))
            c = self._campo_cerca_del_rotulo(cands)
            if c is not None:
                out[k] = c
        # Referencia comercial del documento («REFERENCIA : Campaña de otoño», «Ref.: PC-00123»): el rótulo solo, al comienzo
        # del bloque y con dos puntos. No la cita de otra factura («Referencia factura nº …», «Su referencia factura nº …»: el
        # rótulo no acaba en dos puntos; «Ref.: Factura rectificada F-030», «Abono nº 3» o el encabezado «Factura
        # rectificada» encima: el valor o el encabezado nombra otro documento, como en RE_REF_OTRA para el número)
        propias = []
        for cand in self.candidatos(r"^\s*(?:referencia|ref\.?)(?:\s*" + NUM_ABR + r")?\s*:", self._v_texto, abajo=False):
            _, prev = self._renglon_previo(cand)
            if re.search(r"\b(?:factura|fra|invoice|abono|rectificativa)\b", cand[0], re.I) or self.RE_REF_OTRA.search(cand[0]) \
                    or (prev is not None and (self.RE_REF_OTRA.search(prev.texto) or re.search(r"\babono\b", prev.texto, re.I))):
                continue
            propias.append(cand)
        c = self._campo_cerca_del_rotulo(propias)
        if c is not None:
            out["referencia"] = c
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

    # Valor tomado del bloque de la derecha de un rótulo, a más de HUECO_DERECHA_OTRO_BLOQUE renglones: si es un renglón de
    # otro bloque de texto (el recuadro del cliente frente a un «Referencia:» u «Obra:» vacíos), no es su valor; si está en
    # la columna de valores de una rejilla de rótulos (en un renglón contiguo, un rótulo suelto en la columna del rótulo
    # tiene su valor alineado con este), sí; si está solo y a más de HUECO_DERECHA_CONFIRMAR renglones, se toma a confirmar
    HUECO_DERECHA_OTRO_BLOQUE = 5.0
    HUECO_DERECHA_CONFIRMAR = 10.0

    def _valor_de_la_derecha(self, cand: tuple):
        """(hueco en renglones, bloque del rótulo, bloque del valor, alto) de un candidato tomado del bloque de la derecha de
        su rótulo; None si no lo es."""
        v, pals, pag, texto, estr = cand
        if estr != "derecha" or not pals:
            return None
        p0 = pals[0]
        h = max(1.0, max(p.bottom - p.top for p in pals))
        linea = next((self.lineas[k] for k in self._entre_tops(pag, p0.top - 3 * h, p0.top + 1)
                      if any(p is p0 for p in self.lineas[k].palabras)), None)
        if linea is None:
            return None
        izq = [b for b in self._fila_de(linea).bloques if b is not linea and b.x1 <= linea.x0 + 1]
        rot = max((b for b in izq if texto.startswith(b.texto)), key=lambda b: b.x1, default=None) \
            or max(izq, key=lambda b: b.x1, default=None)
        if rot is None:
            return None
        return max(0.0, linea.x0 - rot.x1) / h, rot, linea, h

    def _columna_de_valores(self, rot: Linea, val: Linea, h: float) -> bool | None:
        """True si `val` está en la columna de valores de una rejilla de rótulos (un renglón contiguo alineado con él es el
        valor de un rótulo suelto, sin cifras, en la columna de `rot`: alineado con él a la izquierda o a la derecha); False
        si los renglones contiguos alineados con él son de otro bloque de texto; None si no tiene renglones contiguos
        alineados."""
        tol = max(2.0, 0.3 * h)
        vecinos = [self.lineas[k] for k in self._entre_tops(val.pagina, val.top - 2.2 * h, val.top + 2.2 * h)]
        alineados = [a for a in vecinos if a is not val and 0 < abs(a.top - val.top) <= 2.2 * h
                     and (abs(a.x0 - val.x0) <= tol or abs(a.x1 - val.x1) <= tol)]
        if not alineados:
            return None
        for a in alineados:
            fila = self._fila_de(a).bloques
            for b in fila:
                if b is a or (abs(b.x0 - rot.x0) > tol and abs(b.x1 - rot.x1) > tol) or b.x1 > a.x0 + 1:
                    continue
                entre = [x for x in fila if x is not a and x is not b and x.x0 >= b.x1 - 1 and x.x1 <= a.x0 + 1]
                if not entre and not re.search(r"\d", b.texto) and len(b.texto.strip(" :")) <= 30:
                    return True
        return False

    def _campo_cerca_del_rotulo(self, cands: list) -> dict | None:
        """Campo del primer candidato cuyo valor no es otro bloque lejano del rótulo (ver HUECO_DERECHA_OTRO_BLOQUE)."""
        for cand in cands:
            d = self._valor_de_la_derecha(cand)
            if d is not None and d[0] > self.HUECO_DERECHA_OTRO_BLOQUE:
                col = self._columna_de_valores(d[1], d[2], d[3])
                if col is False:
                    continue
                if col is None and d[0] > self.HUECO_DERECHA_CONFIRMAR:
                    return self._campo_de(cand, conf=0.6, aviso="El valor está lejos de su rótulo: confirme")
            return self._campo_de(cand)
        return None

    MAX_RENGLONES_CONTINUACION = 2
    # Renglón que no continúa un valor aunque esté en su columna: rótulos sin dos puntos de datos bancarios, notas y
    # totales («BIC FICTESMM», «Titular Talleres…», «OBSERVACIONES», «Portes», «Vto. 15/11»)
    RE_NO_CONTINUA = re.compile(r"^\W*(?:bic|swift|titular|beneficiario|observaci[oó]n|observaciones|notas?|nota\s+importante|"
                                r"concepto|vtos?|venc\w*|portes|firma|sello|recargo|descuento|dto|retenci[oó]n|irpf|base|"
                                r"total|subtotal|importe|iva|igic|ipsi|entrega|env[ií]o|transporte|atentamente|gracias|"
                                r"condiciones\s+generales|protecci[oó]n\s+de\s+datos|registro|inscrita|p[aá]gina)\b", re.I)
    # Vocabulario de una forma de pago: si algún renglón unido no lo tiene, la unión se marca para confirmar
    RE_TERMINO_PAGO = re.compile(r"transfer|banc|bank|caja|caixa|kutxa|recibo|domicilia|giro|pagar[eé]|cheque|tal[oó]n|"
                                 r"efectivo|contado|tarjeta|confirming|letra|remesa|d[ií]as|\bf\.?\s?f\b|vista|\bpago|"
                                 r"cobro|ingreso|cuenta|entidad|sepa|bizum|reembolso|anticipad|aplazad|plazos?\b|mensual|"
                                 r"trimestral|\bvencim", re.I)

    def _continuacion_valor(self, cand: tuple) -> list[Linea]:
        """Renglones que continúan por debajo el valor de un rótulo, en su misma columna («FORMA DE PAGO:  60 días f.f.» ⏎
        «                Transferencia bancaria»): empiezan alineados con el valor, con su mismo cuerpo de letra, a
        interlineado normal, y a su izquierda la columna del rótulo está vacía (no son el valor de otro rótulo). Se para:
        tras un valor que acaba en punto de final de frase (no de abreviatura: «f.f.», «S.L.»); en el primer renglón con
        rótulo propio («IBAN:», «C.C.C.:», «Vencimiento», o sin dos puntos: «BIC …», «Titular …», «OBSERVACIONES»,
        «Portes»: RE_NO_CONTINUA), con cifras largas (cuentas, teléfonos), fechas, importes, NIF o datos de contacto, con un
        importe o el rótulo de un importe a su derecha en la misma fila, o de la tabla de conceptos; como mucho
        MAX_RENGLONES_CONTINUACION. Solo para valores en el renglón del rótulo (los de debajo de un rótulo pueden ser otra
        fila de una rejilla)."""
        v, pals, pag, texto, estr = cand
        if estr not in ("mismo_bloque", "derecha") or not pals:
            return []
        # el valor empieza tras el rótulo: en el mismo bloque, la palabra del final del rótulo («PAGO:») va delante
        ini = 0
        while ini < len(pals) - 1 and (pals[ini].texto.endswith(":") or not re.search(r"\w", pals[ini].texto)):
            ini += 1
        p0 = pals[ini]
        h = max(p.bottom - p.top for p in pals)
        linea = next((self.lineas[k] for k in self._entre_tops(pag, p0.top - 3 * h, p0.top + 1)
                      if any(p is p0 for p in self.lineas[k].palabras)), None)
        if linea is None:
            return []
        x_rot = linea.x0
        if estr == "derecha":   # el rótulo es el bloque de su izquierda en la fila
            izq = [b for b in self._fila_de(linea).bloques if b is not linea and b.x1 <= linea.x0 + 1]
            x_rot = max(izq, key=lambda b: b.x1).x0 if izq else linea.x0
        self.tabla()
        en_tabla = {id(b) for f in self.filas if id(f) in self._ids_filas_tabla for b in f.bloques}
        out, ultimo, previo = [], linea, " ".join(p.texto for p in pals[ini:])   # el valor tal cual (con su punto final)
        while len(out) < self.MAX_RENGLONES_CONTINUACION:
            if previo.rstrip().endswith(".") and not self.RE_ABREVIATURA_FINAL.search(previo.strip()):
                break   # el valor acaba una frase: lo de debajo es otra («Rogamos indiquen…»)
            # el primer renglón de debajo que solapa la columna del valor (no otra columna de la fila, como los totales)
            bajo = [l for l in self._lineas_bajo(ultimo, 0.8 * h) if l.x1 > p0.x0 and l.x0 < max(linea.x1, ultimo.x1)]
            l = min(bajo, key=lambda l: l.top, default=None)
            if l is None or abs(l.x0 - p0.x0) > max(2.0, 0.3 * h) or abs((l.bottom - l.top) - h) > 0.3 * h \
                    or id(l) in en_tabla:
                break
            fila = self._fila_de(l).bloques
            if any(b is not l and b.x0 < l.x0 and b.x1 > x_rot for b in fila):
                break   # algo en la columna del rótulo: es el valor de otro rótulo («Banco:  Banco Ficticio»)
            if any(b is not l and b.x0 >= l.x1 - 1 and (RE_IMPORTE.search(b.texto) or self._rotulo_importe(b)) for b in fila):
                break   # un importe a su derecha: es un rótulo de los totales («Portes   15,00»)
            t = l.texto
            if re.match(r"^[^\d:]{1,30}:", t) or self._es_etiqueta(t) or self.RE_NO_CONTINUA.search(t) \
                    or re.search(r"\d{4}|\d[\s.-]\d{3}|\b\d{1,2}\s*[/.-]\s*\d{1,2}\b", t) or _fecha_iso(t) \
                    or RE_IMPORTE.search(t) or RE_CONTACTO.search(t) or RE_NIF_ETIQUETA.search(t) or any(True for _ in _nifs_en(t)) \
                    or self._rotulo_importe(l) or self._v_texto(t) is None:
                break
            out.append(l)
            ultimo, previo = l, t
        return out

    def pago(self):
        out = {}
        c = self.candidatos(r"forma\s+de\s+pago|condiciones\s+de\s+pago|modo\s+de\s+pago|medio\s+de\s+pago", self._v_texto)
        if c:
            out["forma_pago"] = self._campo_de(c[0])
            sig = self._continuacion_valor(c[0])
            if sig:
                v, pals, pag, texto, estr = c[0]
                cont = [self._v_texto(x.texto) for x in sig]
                unido = (" ".join([v] + cont), list(pals) + [p for x in sig for p in x.palabras], pag,
                         " / ".join([texto] + [x.texto for x in sig]), estr)
                if all(self.RE_TERMINO_PAGO.search(t) for t in cont):
                    out["forma_pago"] = self._campo_de(unido)
                else:   # algún renglón unido no habla del pago: puede ser otra cosa escrita en la misma columna
                    out["forma_pago"] = self._campo_de(unido, conf=0.6, aviso="Forma de pago completada con los renglones de "
                                                                           "debajo: confirme que todos son parte de ella")
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
            # retención de garantía o fianza (obra): también en una nota en redacción libre («el 2 % retenido… garantía»,
            # «se retiene… en concepto de fianza»), sin cruzar una frase ni una mención del IRPF
            "retencion_garantia": r"retenci[oó]n\s+(?:(?:por|de|en|como)\s+(?:concepto\s+de\s+)?)?(?:garant[ií]a|fianza)"
                                  r"|fondo\s+de\s+garant[ií]a|garant[ií]a\s+del?\s+\d+\s*%"
                                  r"|\bret(?:en|ien)\w*(?:(?!i\s?r\s?p\s?f)[^.;\n]){0,60}?\b(?:garant[ií]a|fianza)"
                                  r"|\b(?:garant[ií]a|fianza)s?\b(?:(?!i\s?r\s?p\s?f)[^.;\n]){0,30}?\bret(?:en|ien)\w*",
            "verifactu_existente": r"VERI\*?FACTU|QR\s+tributario|Factura\s+verificable",
        }
        out = {}
        # «Abono» en una línea de la tabla de conceptos es un producto o un concepto («Abono complejo NPK», «Abono envases
        # devueltos»), no el título de una factura de abono; «rectificativa» sí cuenta en cualquier sitio
        try:
            self.tabla()
            ids_tabla = self._ids_filas_tabla
        except Exception:  # noqa: BLE001  (los indicios no pueden fallar: sin tabla, «abono» cuenta en todo el documento)
            ids_tabla = set()
        en_tabla = {id(b) for f in self.filas if id(f) in ids_tabla for b in f.bloques}
        for k, p in pats.items():
            for l in self.lineas:
                m = re.search(p, l.texto, re.I)
                if m and k == "rectificativa" and id(l) in en_tabla and not re.search(r"rectificativ", l.texto, re.I):
                    continue
                if m:
                    out[k] = {"texto": l.texto, "pagina": l.pagina, "bbox": l.bbox()}
                    break
        if "retencion_garantia" not in out:
            par = self._nota_garantia()
            if par:
                out["retencion_garantia"] = par
        return out

    # retención y garantía/fianza en la misma frase de una nota, con incisos («Se retiene el 2 % del importe de la presente
    # certificación… en concepto de garantía», «s/ B.I.», «según art. 14 del contrato»): sin cruzar un fin de frase (punto
    # seguido de mayúscula) ni una mención del IRPF
    RE_NOTA_GARANTIA = re.compile(
        r"\bret(?:en|ien)\w*(?:(?!i\.?\s?r\.?\s?p\.?\s?f)(?!\.\s+(?-i:[A-ZÁÉÍÓÚÑ]))[^;\n]){0,160}?\b(?:garant[ií]a|fianza)"
        r"|\b(?:garant[ií]a|fianza)s?\b(?:(?!i\.?\s?r\.?\s?p\.?\s?f)(?!\.\s+(?-i:[A-ZÁÉÍÓÚÑ]))[^;\n]){0,120}?\bret(?:en|ien)\w*", re.I)

    MAX_NOTAS_GARANTIA = 200

    def _nota_garantia(self):
        """Indicio de retención de garantía en una nota: la frase puede ocupar varios renglones (cada renglón continúa el
        anterior justo debajo y alineado a la izquierda). Coste acotado: párrafos de hasta seis renglones, cada continuación
        buscada entre los doce renglones siguientes, texto limitado y, como mucho, MAX_NOTAS_GARANTIA párrafos con las dos
        palabras evaluados con la expresión (un PDF hostil con «retención … garantía» en cada renglón no la dispara miles
        de veces)."""
        ls = self.lineas
        evaluados = 0
        for i, l in enumerate(ls):
            if evaluados >= self.MAX_NOTAS_GARANTIA:
                break
            par, j = [l], i
            while len(par) < 6:
                ult = ls[j]
                h = max(ult.bottom - ult.top, 1.0)
                k = next((k for k in range(j + 1, min(j + 13, len(ls))) if ls[k].pagina == ult.pagina
                          and -1.0 <= ls[k].top - ult.bottom <= 1.2 * h and abs(ls[k].x0 - ult.x0) <= 20), None)
                if k is None:
                    break
                par.append(ls[k])
                j = k
            texto = " ".join(x.texto for x in par)[:1200]
            bajo = texto.lower().replace("\u0307", "").replace("ı", "i")   # «İ» y «ı» también casan con re.I
            if not (("garant" in bajo or "fianza" in bajo) and ("reten" in bajo or "retien" in bajo)):
                continue
            evaluados += 1
            if self.RE_NOTA_GARANTIA.search(texto):
                return {"texto": texto[:300], "pagina": l.pagina,
                        "bbox": [round(min(x.x0 for x in par), 1), round(min(x.top for x in par), 1),
                                 round(max(x.x1 for x in par), 1), round(max(x.bottom for x in par), 1)]}
        return None

    def _seccion(self, nombre: str, fn, defecto, campo: str = "documento"):
        """Un fallo inesperado al leer una sección la deja vacía con un aviso; no impide importar el documento ni leer el
        resto. El fallo se anota en «extraccion_parcial» con el campo del borrador afectado: la validación pide revisarlo,
        porque una sección opcional vacía (fecha de operación, referencias, pago) no se distingue de una que el documento
        no tiene. El tipo de documento y los indicios no se protegen: sin ellos no se puede clasificar ni bloquear."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            aviso = f"Extracción parcial: no se pudo leer {nombre} ({type(e).__name__}); revíselo a mano"
            self._parciales.append({"seccion": nombre, "campo": campo, "aviso": aviso})
            return defecto(aviso)

    def _tabla_vacia(self, aviso: str):
        self._ids_filas_tabla = set()
        self._tabla = ([], aviso)
        return self._tabla

    def _revisar_visibilidad(self, ext: dict) -> dict:
        """Los valores leídos de texto del PDF que puede no verse en la página (blanco sin fondo, tapado por una imagen o
        un recuadro, bajo una anotación) no son seguros: pasan a dudosos y deben confirmarse con la imagen."""
        dudosas: dict[int, list[Palabra]] = {}
        for pg in self.paginas:
            ds = [p for p in pg.palabras if p.metodo == "texto_pdf" and p.conf < 1.0]
            if ds:
                dudosas[pg.numero] = ds
        aviso = "El texto del PDF en esta zona puede no verse en la página: compruébelo en la imagen"

        def visitar(c):
            if isinstance(c, dict):
                if c.get("valor") is not None and "confianza" in c and c.get("metodo") != "ocr" and c.get("bbox") \
                        and c.get("pagina") in dudosas:
                    x0, t0, x1, b0 = c["bbox"]
                    if any(x0 - 1 <= (p.x0 + p.x1) / 2 <= x1 + 1 and t0 - 1 <= (p.top + p.bottom) / 2 <= b0 + 1
                           for p in dudosas[c["pagina"]]):
                        c["confianza"] = min(c["confianza"], CONF_TEXTO_DUDOSO)
                        if aviso not in c.setdefault("avisos", []):
                            c["avisos"].append(aviso)
                for k, v in c.items():
                    if k != "paginas":
                        visitar(v)
            elif isinstance(c, list):
                for v in c:
                    visitar(v)
        if dudosas:
            visitar(ext)
        for d in ext.get("paginas", []):
            if self._texto_oculto.get(d["numero"]):
                d["texto_oculto"] = self._texto_oculto[d["numero"]]
        return ext

    def extraer(self) -> dict:
        return self._revisar_visibilidad(self._extraer())

    def _extraer(self) -> dict:
        self._parciales = []
        lineas_det, aviso_lineas = self._seccion("la tabla de conceptos", self.lineas_detalle, self._tabla_vacia, "lineas")
        imp = self._seccion("los importes", self.importes, lambda a: {
            "desglose": [], "base_imponible": _vacio(a), "cuota_iva": _vacio(a), "tipo_iva": _vacio(a), "total_factura": _vacio(a),
            "total_a_pagar": _vacio(a), "retencion": None, "recargo_equivalencia": None}, "base_imponible")
        if not lineas_det and not self._ids_filas_tabla:
            unica = self._seccion("la línea única", lambda: self._linea_unica_texto(imp) or self._linea_unica_carta(imp),
                                  lambda a: None, "lineas")
            if unica:
                lineas_det, aviso_lineas = [unica], "Factura sin tabla de conceptos: línea única deducida del texto y de la base imponible"
        partes = self._seccion("emisor y destinatario", self.partes, lambda a: {
            **{rol: {k: _vacio(a) for k in ("nombre", "nif", "domicilio")} for rol in ("emisor", "destinatario")},
            "nifs_detectados": [], "nifs_no_validos": []}, "destinatario.nif")
        return {
            "version_extractor": VERSION_EXTRACTOR,
            "tipo_documento": self.tipo_documento(),
            "numero": self._seccion("el número", self.numero, _vacio, "num_serie"),
            "fecha": self._seccion("la fecha", self.fecha, _vacio, "fecha_documento"),
            "fecha_operacion": self._seccion("la fecha de operación", self.fecha_operacion, _vacio, "fecha_operacion")
            or _vacio("No consta fecha de operación distinta"),
            "emisor": partes["emisor"],
            "destinatario": partes["destinatario"],
            "nifs_detectados": partes["nifs_detectados"],
            "nifs_no_validos": partes["nifs_no_validos"],
            "lineas": lineas_det,
            "aviso_lineas": aviso_lineas,
            **imp,
            "referencias": self._seccion("las referencias", self.referencias, lambda a: {}),
            **self._seccion("los datos de pago", self.pago, lambda a: {}, "forma_pago"),
            "indicios": self.indicios(),
            "extraccion_parcial": self._parciales,
            "paginas": [{"numero": p.numero, "metodo": p.metodo, "conf_media": p.conf_media,
                         "ancho": p.ancho, "alto": p.alto} for p in self.paginas],
        }
