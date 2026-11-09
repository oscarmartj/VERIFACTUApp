"""Validación de NIF españoles (DNI, NIE y NIF de personas jurídicas)."""
from __future__ import annotations

import re

LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETRA_CONTROL = "JABCDEFGHI"

NIF_REGEX = re.compile(
    r"(?<![A-Z0-9])(?:ES[\s-]?)?([ABCDEFGHJKLMNPQRSUVW][\s.-]?\d{2}[\s.]?\d{3}[\s.]?\d{2}[\s.-]?[0-9A-J]"
    r"|[XYZ][\s.-]?\d{7}[\s.-]?[A-Z]"
    r"|\d{8}[\s.-]?[A-Z])(?![A-Z0-9])"
)


def normalizar(nif: str | None) -> str:
    if not nif:
        return ""
    n = re.sub(r"[\s.\-]", "", nif.upper())
    if n.startswith("ES") and len(n) == 11:
        n = n[2:]
    return n


def tipo(nif: str) -> str | None:
    n = normalizar(nif)
    if re.fullmatch(r"\d{8}[A-Z]", n):
        return "DNI"
    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", n):
        return "NIE"
    if re.fullmatch(r"[KLM]\d{7}[A-Z]", n):
        return "NIF_ESPECIAL"
    if re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", n):
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


def buscar_nifs(texto: str) -> list[tuple[str, int, int]]:
    """Devuelve (nif_normalizado, inicio, fin) de los NIF con dígito de control válido."""
    out = []
    for m in NIF_REGEX.finditer(texto.upper()):
        n = normalizar(m.group(1))
        if es_valido(n):
            out.append((n, m.start(1), m.end(1)))
    return out


def completar_cif(letra: str, cuerpo7: str) -> str:
    """Utilidad para generar NIF sintéticos válidos en pruebas."""
    digito, letra_c = _control_cif(cuerpo7)
    return letra + cuerpo7 + (letra_c if letra in "PQRSNW" else digito)
