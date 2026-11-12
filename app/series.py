"""Series y numeración. El identificador completo (p. ej. «166/26») se conserva íntegro como
NumSerieFactura; el patrón solo sirve para controlar la correlatividad y debe estar confirmado.

Marcadores del patrón:
- `{n}`: número correlativo sin ceros de relleno (se leen también con ceros: «0166» es el 166).
- `{n:3}` (o `{nnn}`): número con ceros de relleno hasta 3 cifras («013»). Se lee solo con exactamente ese
  ancho o, cuando el contador lo supera, con más cifras sin ceros a la izquierda («1000»).
- `{aa}` / `{aaaa}`: año con 2 o 4 cifras.
"""
from __future__ import annotations

import re

from . import db

RE_MARCADOR = re.compile(r"\{(n(?::(\d{1,2}))?|n{2,12}|aa|aaaa)\}")
MAX_CIFRAS = 12
MAX_NUMERO = 10 ** MAX_CIFRAS - 1
ANIO_MIN, ANIO_MAX = 2000, 2100
MAX_LISTA_SALTADOS = 20


def _marcadores(patron: str) -> list[tuple[re.Match, str, int]]:
    """(coincidencia, clave, ancho) de cada marcador; clave «n», «aa» o «aaaa»; ancho 0 = sin relleno."""
    out = []
    for m in RE_MARCADOR.finditer(patron):
        g = m.group(1)
        if g.startswith("n"):
            ancho = int(m.group(2)) if m.group(2) else (len(g) if len(g) > 1 else 0)
            if m.group(2) is not None and not 1 <= ancho <= MAX_CIFRAS:
                raise ValueError(f"El ancho del número debe estar entre 1 y {MAX_CIFRAS} cifras")
            out.append((m, "n", ancho))
        else:
            out.append((m, g, 0))
    return out


def validar_patron(patron: str) -> None:
    """Exactamente un marcador de número y como mucho un marcador de año; sin llaves sueltas."""
    if not isinstance(patron, str) or not patron.strip():
        raise ValueError("El patrón debe contener {n}")
    marcas = _marcadores(patron)
    claves = [c for _, c, _ in marcas]
    if claves.count("n") != 1:
        raise ValueError("El patrón debe contener {n} (o {n:3} para ceros de relleno) exactamente una vez")
    if claves.count("aa") + claves.count("aaaa") > 1:
        raise ValueError("El patrón solo puede contener un marcador de año ({aa} o {aaaa})")
    if "{" in RE_MARCADOR.sub("", patron) or "}" in RE_MARCADOR.sub("", patron):
        raise ValueError("Marcador no reconocido: use {n}, {n:3}, {aa} o {aaaa}")


def ancho(patron: str) -> int:
    """Ancho de relleno del número (0 si es «{n}»)."""
    return next((a for _, c, a in _marcadores(patron) if c == "n"), 0)


def patron_a_regex(patron: str, estricto: bool = True) -> re.Pattern:
    """Expresión del patrón. `estricto=False` lee el número con cualquier ancho (solo para reconocer el mismo número
    escrito de otra forma, p. ej. al buscar duplicados; nunca para validar el número que se expide)."""
    validar_patron(patron)
    partes, pos = [], 0
    for m, clave, w in _marcadores(patron):
        partes.append(re.escape(patron[pos:m.start()]))
        if clave == "n":
            if w and estricto:
                # exactamente w cifras (con ceros) o más cifras sin cero inicial cuando el contador supera el ancho
                expr = rf"(?P<n>\d{{{w}}}|[1-9]\d{{{w},{MAX_CIFRAS - 1}}})" if w < MAX_CIFRAS else rf"(?P<n>\d{{{w}}})"
            else:
                expr = rf"(?P<n>\d{{1,{MAX_CIFRAS}}})"
        else:
            expr = {"aa": r"(?P<aa>\d{2})", "aaaa": r"(?P<aaaa>\d{4})"}[clave]
        partes.append(expr)
        pos = m.end()
    partes.append(re.escape(patron[pos:]))
    return re.compile("^" + "".join(partes) + "$")


def probar(patron: str, num_serie: str, estricto: bool = True) -> dict:
    m = patron_a_regex(patron, estricto).match((num_serie or "").strip())
    if not m:
        return {"coincide": False}
    g = m.groupdict()
    anio = int(g["aaaa"]) if g.get("aaaa") else (2000 + int(g["aa"]) if g.get("aa") else 0)
    return {"coincide": True, "numero": int(g["n"]), "anio": anio}


def formatear(patron: str, numero: int, anio: int) -> str:
    """Número de factura que corresponde al patrón (con los ceros de relleno de `{n:3}`)."""
    validar_patron(patron)

    def sustituir(m: re.Match) -> str:
        g = m.group(1)
        if g == "aaaa":
            return f"{anio:04d}"
        if g == "aa":
            return f"{anio % 100:02d}"
        w = int(m.group(2)) if m.group(2) else (len(g) if len(g) > 1 else 0)
        return f"{numero:0{w}d}" if w else str(numero)
    return RE_MARCADOR.sub(sustituir, patron)


def proponer_patron(num_serie: str, anio: int) -> dict | None:
    """Patrón propuesto a partir de un número de factura detectado («042/A26» → «{n:3}/A{aa}»; «A057/26» →
    «A{n:3}/{aa}»; «2026-0045» → «{aaaa}-{n:4}»; «166/26» → «{n}/{aa}»). El año se reconoce si coincide con `anio`;
    el número es el último grupo de cifras restante y, si lleva ceros a la izquierda, se propone su ancho.
    Solo es una propuesta: la serie la crea y confirma el usuario. None si no hay cifras."""
    t = (num_serie or "").strip()
    if not t or "{" in t or "}" in t or len(t) > 60:
        return None
    grupos = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\d+", t)]
    if not grupos:
        return None
    a4 = f"{anio:04d}"
    marcas: dict[int, tuple[int, int, str]] = {}   # índice de grupo -> (inicio, fin, marcador)
    anio_en = None
    if len(grupos) > 1:
        # año: un grupo igual al año de expedición (o al anterior o siguiente), 4 cifras antes que 2; si el año aparece
        # también como posible número («26/26»), se toma como año el último
        for cand in (anio, anio - 1, anio + 1):
            for objetivo, marca in ((f"{cand:04d}", "{aaaa}"), (f"{cand % 100:02d}", "{aa}")):
                idx = [i for i, (_, _, g) in enumerate(grupos) if g == objetivo]
                if idx:
                    anio_en = idx[-1]
                    marcas[anio_en] = (grupos[anio_en][0], grupos[anio_en][1], marca)
                    break
            if anio_en is not None:
                break
    restantes = [i for i in range(len(grupos)) if i not in marcas]
    if not restantes:
        return None
    i_num = restantes[-1]
    ini, fin, g = grupos[i_num]
    sub = None
    if anio_en is None and len(g) >= 6 and g.startswith(a4):
        # «20260045»: año de 4 cifras pegado al número
        sub = ("{aaaa}", g[4:])
    if sub:
        resto = sub[1]
        marca_n = "{n:%d}" % len(resto) if resto.startswith("0") else "{n}"
        marcas[i_num] = (ini, fin, sub[0] + marca_n)
        numero = int(resto)
    else:
        marca_n = "{n:%d}" % len(g) if (g.startswith("0") and len(g) > 1) else "{n}"
        marcas[i_num] = (ini, fin, marca_n)
        numero = int(g)
    out, pos = [], 0
    for i in sorted(marcas):
        a, b, marca = marcas[i]
        out.append(t[pos:a])
        out.append(marca)
        pos = b
    out.append(t[pos:])
    patron = "".join(out)
    try:
        validar_patron(patron)
        r = probar(patron, t)
    except (ValueError, re.error):
        return None
    if not r.get("coincide"):
        return None
    return {"patron": patron, "numero": numero, "anio": r["anio"], "ancho": ancho(patron)}


def siguiente(con, serie_id: int, anio: int) -> int:
    c = db.fila(con, "SELECT siguiente FROM series_contadores WHERE serie_id=? AND anio=?", (serie_id, anio))
    if c:
        return c["siguiente"]
    s = db.fila(con, "SELECT siguiente_numero, confirmada_en FROM series WHERE id=?", (serie_id,))
    # Primer uso de un año: el número confirmado vale para el año de la confirmación (o para series sin año);
    # en cualquier otro año la numeración empieza en 1.
    anio_conf = int(s["confirmada_en"][:4]) if s.get("confirmada_en") else None
    if anio == 0 or anio == anio_conf:
        return s["siguiente_numero"]
    return 1


def _tiene_anio(patron: str) -> bool:
    return any(c in ("aa", "aaaa") for _, c, _ in _marcadores(patron))


def identificar(con, empresa_id: int, entorno: str, num_serie: str, anio_expedicion: int, tipo: str = "ordinaria") -> dict | None:
    """Devuelve la serie confirmada cuyo patrón encaja con el número, con el número detectado y el esperado."""
    for s in db.filas(con, """SELECT * FROM series WHERE empresa_id=? AND entorno=? AND activa=1 AND tipo=?
                             AND confirmada_en IS NOT NULL ORDER BY id""", (empresa_id, entorno, tipo)):
        try:
            r = probar(s["patron"], num_serie or "")
        except (ValueError, re.error):
            continue  # patrón antiguo no válido: no identifica ningún número
        if r["coincide"]:
            anio = r["anio"] or 0
            s["numero_detectado"] = r["numero"]
            s["anio_detectado"] = anio
            s["anio_contador"] = anio if _tiene_anio(s["patron"]) else 0
            s["siguiente_numero"] = siguiente(con, s["id"], s["anio_contador"])
            s["anio_correcto"] = (anio == 0) or (anio == anio_expedicion)
            return s
    return None


def formato_esperado(con, empresa_id: int, entorno: str, num_serie: str, anio_expedicion: int,
                     tipo: str = "ordinaria") -> dict | None:
    """Si el número no encaja con ninguna serie confirmada solo por el ancho de los ceros de relleno («42/A26» con
    «{n:3}/A{aa}»), devuelve la serie y cómo debe escribirse («042/A26»)."""
    for s in db.filas(con, """SELECT * FROM series WHERE empresa_id=? AND entorno=? AND activa=1 AND tipo=?
                             AND confirmada_en IS NOT NULL ORDER BY id""", (empresa_id, entorno, tipo)):
        try:
            if not ancho(s["patron"]) or probar(s["patron"], num_serie or "")["coincide"]:
                continue
            r = probar(s["patron"], num_serie or "", estricto=False)
        except (ValueError, re.error):
            continue
        if r["coincide"]:
            anio = r["anio"] or anio_expedicion
            return {"serie_id": s["id"], "codigo": s["codigo"], "patron": s["patron"],
                    "esperado": formatear(s["patron"], r["numero"], anio)}
    return None


def consumir(con, serie: dict) -> None:
    """Avanza el contador (dentro de la transacción de expedición)."""
    n = serie["numero_detectado"] + 1
    con.execute("""INSERT INTO series_contadores (serie_id, anio, siguiente) VALUES (?,?,?)
                   ON CONFLICT(serie_id, anio) DO UPDATE SET siguiente=excluded.siguiente""",
                (serie["id"], serie["anio_contador"], n))


# --- Avance manual del contador (números ya usados fuera de esta instalación) ---------------------------------------
class AvanceNoValido(ValueError):
    def __init__(self, mensaje: str, estado: int = 409):
        super().__init__(mensaje)
        self.estado = estado


def tiene_anio(patron: str) -> bool:
    try:
        return _tiene_anio(patron)
    except ValueError:
        return False


def ultimo_expedido(con, serie_id: int, anio: int) -> int | None:
    """Mayor número expedido en esta instalación con la serie y el año del contador (0 = serie sin año)."""
    f = db.fila(con, """SELECT MAX(numero) n FROM facturas WHERE serie_id=? AND numero IS NOT NULL
                        AND COALESCE(CASE WHEN json_valid(datos) THEN json_extract(datos, '$.numeracion.anio') END, 0)=?""",
                (serie_id, anio))
    return f["n"] if f else None


def numeros_saltados(patron: str, anio: int, desde: int, hasta: int) -> dict:
    """Números que dejan de poder expedirse (de `desde` a `hasta`, ambos incluidos), escritos con el patrón."""
    cantidad = hasta - desde + 1
    if cantidad <= MAX_LISTA_SALTADOS:
        nums = list(range(desde, hasta + 1))
    else:
        mitad = MAX_LISTA_SALTADOS // 2
        nums = list(range(desde, desde + mitad)) + list(range(hasta - mitad + 1, hasta + 1))
    return {"desde": desde, "hasta": hasta, "cantidad": cantidad, "primero": formatear(patron, desde, anio),
            "ultimo": formatear(patron, hasta, anio), "lista": [formatear(patron, n, anio) for n in nums],
            "completa": cantidad <= MAX_LISTA_SALTADOS}


def preparar_avance(con, serie: dict, anio: int | None, siguiente_nuevo: int | None = None) -> dict:
    """Estado del contador de la serie para `anio` y, con `siguiente_nuevo`, qué números se saltarían al avanzarlo.
    Nunca permite retroceder ni quedarse en el mismo número, ni quedar por debajo de un número ya expedido aquí.
    Lanza AvanceNoValido (409 por el estado; 422 por los datos)."""
    if not serie.get("confirmada_en"):
        raise AvanceNoValido("La serie no está confirmada: confírmela (o créela con el siguiente número correcto) en lugar de "
                             "avanzar su contador.")
    con_anio = tiene_anio(serie["patron"])
    if con_anio and not anio:
        raise AvanceNoValido("Indique el año del contador: la numeración de esta serie se reinicia cada año.", 422)
    if not con_anio and anio:
        raise AvanceNoValido("Esta serie no lleva año: tiene un único contador (no indique el año).", 422)
    anio_c = anio if con_anio else 0
    actual = siguiente(con, serie["id"], anio_c)
    ult = ultimo_expedido(con, serie["id"], anio_c)
    out = {"serie_id": serie["id"], "codigo": serie["codigo"], "patron": serie["patron"], "entorno": serie["entorno"],
           "con_anio": con_anio, "anio": anio_c, "actual": actual, "actual_texto": formatear(serie["patron"], actual, anio_c),
           "ultimo_expedido": ult, "ultimo_expedido_texto": formatear(serie["patron"], ult, anio_c) if ult else None}
    if siguiente_nuevo is None:
        return out
    if siguiente_nuevo <= actual:
        raise AvanceNoValido(f"El contador ya va por el {formatear(serie['patron'], actual, anio_c)}: solo puede avanzarse "
                             "(indique un número mayor); nunca retrocede.")
    if ult is not None and siguiente_nuevo <= ult:
        raise AvanceNoValido(f"Ya se expidió aquí el {formatear(serie['patron'], ult, anio_c)}: el contador solo puede avanzar "
                             "más allá.")
    out.update(siguiente=siguiente_nuevo, siguiente_texto=formatear(serie["patron"], siguiente_nuevo, anio_c),
               saltados=numeros_saltados(serie["patron"], anio_c, actual, siguiente_nuevo - 1))
    return out


def avanzar(con, serie_id: int, anio: int, siguiente_nuevo: int) -> None:
    """Fija el contador (dentro de la transacción de quien llama, tras preparar_avance). MAX: nunca retrocede."""
    con.execute("""INSERT INTO series_contadores (serie_id, anio, siguiente) VALUES (?,?,?)
                   ON CONFLICT(serie_id, anio) DO UPDATE SET siguiente=MAX(siguiente, excluded.siguiente)""",
                (serie_id, anio, siguiente_nuevo))
