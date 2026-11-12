"""Validación de NIF españoles (DNI, NIE y NIF de personas jurídicas)."""
from __future__ import annotations

import re

LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETRA_CONTROL = "JABCDEFGHI"

# Separador dentro de un NIF: un espacio, un punto o un guion («B-46.871.232», «21.987.654-E»)
SEP_SIMPLE = r"[\s.\-]?"
# … y, solo detrás del rótulo del NIF (ver forma_admitida), la raya «–», «—» o el guion separados por espacios
# («CIF: A – 00543215», «N.I.F.: 44444444 – A»)
SEP = r"(?:\s?[\-–—]\s?|[\s.])?"
# Separadores que solo valen detrás del rótulo: la barra tras el código de provincia, la raya y el guion con espacios
RE_SEP_TRAS_ROTULO = re.compile(r"[/–—]|\s-|-\s")
# Rótulo del NIF, también como lo lee el OCR («N.LF.», «C.l.F.», «N.1.F.»: la I leída como L, l, 1 o |)
ROTULO_NIF = (r"c\.?\s?i\.?\s?f\.?|n\.?\s?i\.?\s?f\.?|n\.?\s?i\.?\s?e\.?|d\.?\s?n\.?\s?i\.?|[cn]\.\s?[l1|]\.?\s?f\.?|vat|tax\s?id|"
              r"nif-iva|nif/cif|cif/nif")
# Rótulo del NIF justo delante de una posición (solo separadores, la abreviatura de «número» y el prefijo «ES» entre ambos:
# «CIF: », «C.I.F. nº », «NIF núm.: ES»)
RE_ROTULO_DELANTE = re.compile(r"(?<![^\W\d_])(?:" + ROTULO_NIF + r")[\s:.\-–—]*(?:(?:n\.?[º°]|n[uú]m(?:ero)?)\.?[\s:.\-–—]*)?"
                               r"(?:ES[\s-]?)?$", re.I)


def _nif_regex(sep: str, barra: bool) -> re.Pattern:
    return re.compile(
        r"(?<![A-Z0-9])(?:ES[\s-]?)?([ABCDEFGHJKLMNPQRSUVW]" + sep + r"\d{2}" + (r"(?:[\s.]?|/)" if barra else r"[\s.]?")
        + r"\d{3}[\s.]?\d{2}" + sep + r"[0-9A-J]"
        r"|[XYZ]" + sep + r"\d{7}" + sep + r"[A-Z]"
        r"|\d{8}" + sep + r"[A-Z])(?![A-Z0-9])"
    )


NIF_REGEX = _nif_regex(SEP, barra=True)
NIF_REGEX_SIMPLE = _nif_regex(SEP_SIMPLE, barra=False)  # (sin rótulo delante: solo espacio, punto o guion)


def forma_admitida(texto: str, ini: int, candidato: str) -> bool:
    """Un NIF con barra tras el código de provincia («B00/123455») o con la raya o el guion separados por espacios
    («A – 00543215», «44444444 – A») solo cuenta detrás de su rótulo («CIF: …»): sin él, esas formas son las de un número
    de factura («F26/000123», «A – 26000125») o de una referencia («Pedido 20261105 – Z»)."""
    return not RE_SEP_TRAS_ROTULO.search(candidato) or bool(RE_ROTULO_DELANTE.search(texto, max(0, ini - 40), ini))


def candidatos(regex: re.Pattern, regex_simple: re.Pattern, texto: str):
    """Coincidencias de `regex` admitidas por forma_admitida; donde una no lo es, las de `regex_simple` (sin la barra ni
    la raya) en su tramo: «Pedido B00123455 – C/ Mayor» da «B00123455»."""
    for m in regex.finditer(texto):
        if forma_admitida(texto, m.start(1), m.group(1)):
            yield m
        else:
            yield from regex_simple.finditer(texto, m.start(), m.end())


def normalizar(nif: str | None) -> str:
    """Mayúsculas y sin separadores. Un NIF solo tiene caracteres ASCII: si quedan otros (cifras de ancho completo, de
    otros alfabetos o matemáticas, letras que en mayúsculas se convierten en ASCII…), se devuelve sin convertir y no es
    válido (nunca se «arregla» en silencio: «B８２…» no es el mismo obligado que «B82…» ni otro distinto)."""
    if not nif or not isinstance(nif, str):
        return ""
    n = re.sub(r"[\s.\-–—/]", "", nif)
    if not n.isascii():
        return n
    n = n.upper()
    if n.startswith("ES") and len(n) == 11:
        n = n[2:]
    return n


def tipo(nif: str) -> str | None:
    n = normalizar(nif)
    if re.fullmatch(r"[0-9]{8}[A-Z]", n):
        return "DNI"
    if re.fullmatch(r"[XYZ][0-9]{7}[A-Z]", n):
        return "NIE"
    if re.fullmatch(r"[KLM][0-9]{7}[A-Z]", n):
        return "NIF_ESPECIAL"
    if re.fullmatch(r"[ABCDEFGHJNPQRSUVW][0-9]{7}[0-9A-J]", n):
        return "PERSONA_JURIDICA"
    return None


def _control_cif(cuerpo7: str) -> tuple[str, str]:
    pares = sum(int(cuerpo7[i]) for i in (1, 3, 5))
    impares = 0
    for i in (0, 2, 4, 6):
        d = int(cuerpo7[i]) * 2
        impares += d // 10 + d % 10
    c = (10 - (pares + impares) % 10) % 10
    return str(c), _CIF_LETRA_CONTROL[c]


def es_valido(nif: str | None) -> bool:
    n = normalizar(nif)
    t = tipo(n)
    if t == "DNI":
        return LETRAS_DNI[int(n[:8]) % 23] == n[8]
    if t == "NIE":
        num = {"X": "0", "Y": "1", "Z": "2"}[n[0]] + n[1:8]
        return LETRAS_DNI[int(num) % 23] == n[8]
    if t == "NIF_ESPECIAL":
        digito, letra = _control_cif(n[1:8])
        return n[8] == letra
    if t == "PERSONA_JURIDICA":
        digito, letra = _control_cif(n[1:8])
        primera = n[0]
        if primera in "PQRSNW":  # control con letra
            return n[8] == letra
        if primera in "ABEH":  # control con dígito
            return n[8] == digito
        return n[8] in (digito, letra)
    return False


def es_persona_juridica(nif: str) -> bool:
    return tipo(nif) == "PERSONA_JURIDICA"


def parecidos(a: str | None, b: str | None, max_dif: int = 2) -> bool:
    """Misma forma y como mucho `max_dif` caracteres distintos: un NIF mal leído (OCR, error de tecleo) del otro."""
    a, b = normalizar(a), normalizar(b)
    return bool(a) and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= max_dif


def buscar_nifs(texto: str) -> list[tuple[str, int, int]]:
    """Devuelve (nif_normalizado, inicio, fin) de los NIF con dígito de control válido."""
    out = []
    t = texto.upper()
    for m in candidatos(NIF_REGEX, NIF_REGEX_SIMPLE, t):
        n = normalizar(m.group(1))
        if es_valido(n):
            out.append((n, m.start(1), m.end(1)))
    return out


def completar_cif(letra: str, cuerpo7: str) -> str:
    """Utilidad para generar NIF sintéticos válidos en pruebas."""
    digito, letra_c = _control_cif(cuerpo7)
    return letra + cuerpo7 + (letra_c if letra in "PQRSNW" else digito)
