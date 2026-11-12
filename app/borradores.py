"""Borradores (ámbito de preparación): clasificación, datos editables y validación determinista.

Nada de este módulo tiene efectos fiscales. La emisión solo es posible cuando la validación
no devuelve bloqueos y el usuario ha confirmado los puntos que requieren confirmación.
"""
from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation

from . import db
from . import nif as nifmod
from .importes import q2

UMBRAL_CONFIANZA = 0.85
TIPOS_IVA_SOPORTADOS = {Decimal("21"), Decimal("10"), Decimal("4")}
# Tipos de retención de IRPF habituales en factura (otro porcentaje suele ser garantía u otro concepto)
TIPOS_IRPF_HABITUALES = {Decimal("1"), Decimal("2"), Decimal("7"), Decimal("15"), Decimal("19")}

# Casos tributarios implementados y verificados (todo lo demás se bloquea)
CASOS_SOPORTADOS = [
    "Factura completa (F1) de la empresa configurada a un destinatario con NIF español.",
    "Impuesto: IVA (01); régimen general (ClaveRegimen 01); operación sujeta y no exenta sin inversión "
    "del sujeto pasivo (S1).",
    "Uno o varios tipos impositivos generales vigentes: 21 %, 10 %, 4 %.",
    "Operaciones exentas de IVA (OperacionExenta E1 art. 20, E2 art. 21, E3 art. 22, E4 arts. 23 y 24, E6 otras), solas "
    "o en la misma factura que operaciones sujetas: base exenta sin tipo ni cuota, causa confirmada por el usuario y "
    "mención legal en la factura (art. 6.1.j RD 1619/2012). E2 y E3 se declaran con ClaveRegimen 02 (exportación); "
    "E5 (entregas intracomunitarias) exige un destinatario extranjero y no está soportada.",
    "Retención de IRPF: no forma parte del ImporteTotal del registro ni del QR (bases + cuotas); el PDF muestra "
    "por separado «Total factura» y «Total a pagar» (FAQ AEAT para desarrolladores, nº 20).",
    "Factura rectificativa por diferencias (R1 o R4, TipoRectificativa I) de una factura expedida en la aplicación.",
    "Anulación del registro de una factura expedida en la aplicación; subsanación de registros.",
]

# --- Operaciones exentas (OperacionExenta del registro, lista L10 de la Orden HAC/1177/2024) ------------------------------
# Cada fila del desglose es sujeta (CalificacionOperacion S1, con tipo y cuota) o exenta (OperacionExenta, solo con la base);
# las dos son excluyentes (validaciones AEAT 1195, 1196 y 1238). La causa no se puede deducir con seguridad del documento:
# el usuario la elige o confirma, y la factura debe llevar la mención de la exención (art. 6.1.j RD 1619/2012)
CAUSAS_EXENCION = {
    "E1": {"nombre": "Exenta por el artículo 20 (operaciones interiores)", "precepto": "art. 20 Ley 37/1992",
           "mencion": "Operación exenta de IVA (art. 20 Ley 37/1992)", "clave_regimen": "01"},
    "E2": {"nombre": "Exenta por el artículo 21 (exportaciones)", "precepto": "art. 21 Ley 37/1992",
           "mencion": "Operación exenta de IVA (art. 21 Ley 37/1992)", "clave_regimen": "02"},
    "E3": {"nombre": "Exenta por el artículo 22 (operaciones asimiladas a las exportaciones)", "precepto": "art. 22 Ley 37/1992",
           "mencion": "Operación exenta de IVA (art. 22 Ley 37/1992)", "clave_regimen": "02"},
    "E4": {"nombre": "Exenta por los artículos 23 y 24 (zonas francas, depósitos y regímenes aduaneros o fiscales)",
           "precepto": "arts. 23 y 24 Ley 37/1992", "mencion": "Operación exenta de IVA (arts. 23 y 24 Ley 37/1992)",
           "clave_regimen": "01"},
    "E5": {"nombre": "Exenta por el artículo 25 (entregas intracomunitarias)", "precepto": "art. 25 Ley 37/1992",
           "mencion": "Operación exenta de IVA (art. 25 Ley 37/1992)", "clave_regimen": "01"},
    "E6": {"nombre": "Exenta por otros motivos", "precepto": "otros preceptos",
           "mencion": "Operación exenta de IVA", "clave_regimen": "01"},
}
# E5 exige identificar al destinatario con IDOtro (NIF-IVA de otro Estado miembro, validación AEAT 1289): la aplicación solo
# admite destinatarios con NIF español
CAUSAS_NO_SOPORTADAS = {"E5"}
MAX_MENCION_EXENCION = 500


def clase_fila(fila: dict) -> str:
    """Clase de una fila del desglose: «exenta» (lleva causa E1–E6), la calificación indicada (S1, S2, N1, N2),
    «sin_calificar» (sin causa de exención, sin calificación, sin tipo y sin cuota: una base sin IVA cuyo tratamiento debe
    elegir el usuario) o «S1» por defecto (filas de versiones anteriores sin calificación pero con tipo o cuota)."""
    if not isinstance(fila, dict):
        return "S1"
    if fila.get("operacion_exenta"):
        return "exenta"
    if fila.get("calificacion"):
        return str(fila["calificacion"])
    if fila.get("tipo_iva") in (None, "") and fila.get("cuota") in (None, ""):
        return "sin_calificar"
    return "S1"


def es_exenta(fila: dict) -> bool:
    return clase_fila(fila) == "exenta"


def clave_regimen_exenta(causa: str | None) -> str:
    """ClaveRegimen de una fila exenta: 02 (exportación) para E2 y E3, que con la clave 01 se rechazan (validación AEAT
    1199); 01 (régimen general) para las demás."""
    return (CAUSAS_EXENCION.get(causa or "") or {}).get("clave_regimen", "01")


def mencion_exencion(causa: str | None) -> str | None:
    """Mención legal propuesta para una causa (el usuario puede escribir otra más precisa: «art. 20.Uno.23º …»)."""
    return (CAUSAS_EXENCION.get(causa or "") or {}).get("mencion")


def normalizar_mencion(t) -> str:
    """Mención de la exención con los saltos de línea de cualquier sistema (CRLF, CR) como LF y los tabuladores como
    espacios, sin espacios al principio ni al final: así se guarda, se valida y se imprime."""
    if not isinstance(t, str):
        return ""
    return t.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ").strip()


def mencion_vacia(t: str) -> bool:
    """Mención sin ninguna letra ni cifra («.», «-», «…»): no cita ningún precepto ni dice que la operación esté exenta."""
    return not any(ch.isalnum() for ch in t)


def texto_imprimible(t: str) -> bool:
    """El texto se puede imprimir con las fuentes estándar del PDF (WinAnsi) y comprobarse después en el PDF generado."""
    try:
        t.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return not re.search(r"[\x00-\x1f\x7f]", t.replace("\n", " "))


CASOS_BLOQUEADOS = {
    "inversion_sujeto_pasivo": "Inversión del sujeto pasivo (S2)",
    "no_sujeta": "Operaciones no sujetas (N1/N2)",
    "recargo_equivalencia": "Recargo de equivalencia",
    "suplidos": "Suplidos (deben excluirse de la base y requieren tratamiento específico)",
    "intracomunitaria": "Operaciones intracomunitarias o con destinatarios extranjeros",
    "criterio_caja": "Régimen especial del criterio de caja",
    "regimen_especial": "Regímenes especiales de IVA",
    "rectificativa": "Rectificativas importadas desde PDF (use el procedimiento de rectificación de la aplicación)",
    "verifactu_existente": "El documento ya contiene marcas de un sistema VERI*FACTU (factura ya expedida)",
}


def _v(c):
    return c.get("valor") if isinstance(c, dict) else None


def _clave(t) -> str:
    """Texto comparable: sin tildes, mayúsculas, espacios ni puntuación."""
    import unicodedata
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().casefold())


# Fecha de un PDF (ISO 32000, 7.9.4): D:AAAA[MM[DD[HH[mm[SS]]]]][Z|+HH'mm'|-HH'mm']
_FECHA_PDF = re.compile(r"(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:(Z)|([+-])(\d{2})(?:'?(\d{2}))?'?)?\s*$")


def _fecha_firma_legible(v) -> str | None:
    """La fecha de la firma (/M) como la ve el usuario, en hora peninsular («01/07/2026 a las 12:05»); None si no se lee."""
    from datetime import datetime, timedelta, timezone
    m = _FECHA_PDF.fullmatch(str(v or "").strip())
    if not m:
        return None
    a, mes, dia, hh, mi, ss, z, signo, oh, om = m.groups()
    if not dia:
        return None                                     # solo el año o el mes: no es un día que se pueda mostrar
    try:
        if z:
            tz = timezone.utc
        elif signo:
            tz = timezone((1 if signo == "+" else -1) * timedelta(hours=int(oh), minutes=int(om or 0)))
        else:
            tz = None                                   # sin desfase: se toma como hora peninsular
        d = datetime(int(a), int(mes), int(dia), int(hh or 0), int(mi or 0), int(ss or 0), tzinfo=tz)
    except ValueError:
        return None
    return db.legible(d.isoformat(), hora=hh is not None)


def clasificar(ext: dict, inspeccion: dict, empresa: dict, duplicados: list[dict] | None = None) -> dict:
    """Propone la situación del documento. El usuario debe confirmarla antes de procesarlo fiscalmente.
    `duplicados`: otros PDF o facturas con la misma identidad fiscal (importacion.duplicados_fiscales)."""
    nif_emp = nifmod.normalizar(empresa.get("nif"))
    emisor = nifmod.normalizar(_v(ext.get("emisor", {}).get("nif", {})) or "")
    dest = nifmod.normalizar(_v(ext.get("destinatario", {}).get("nif", {})) or "")
    tipo = _v(ext.get("tipo_documento", {})) or ""
    qrs = [q for q in inspeccion.get("qr", []) if q.get("tributario")]
    motivos = []
    if qrs:
        q = qrs[0]
        motivos.append(f"Contiene un QR tributario ({q.get('modalidad')}, entorno {q.get('entorno')}) que remite al "
                       f"servicio de cotejo de la AEAT para la factura {q.get('numserie')}.")
        if nifmod.normalizar(q.get("nif")) == nif_emp:
            return {"sugerida": "emitida_propia", "confianza": 0.95, "motivos": motivos + [
                "La factura ya fue expedida por un sistema de facturación: solo procede archivarla o consultar su registro."]}
        return {"sugerida": "recibida", "confianza": 0.9, "motivos": motivos}
    if "verifactu_existente" in ext.get("indicios", {}):
        motivos.append("El texto menciona VERI*FACTU o el QR tributario.")
    # Otro PDF de la misma factura ya archivado como expedido (clasificación confirmada por el usuario, no una mera
    # propuesta), o factura ya expedida en la aplicación
    previas = [d for d in duplicados or [] if d.get("tipo") == "factura"
               or (d.get("clasificacion") == "emitida_propia" and d.get("confirmado"))]
    if dest and dest == nif_emp and emisor != nif_emp:
        return {"sugerida": "recibida", "confianza": 0.9,
                "motivos": motivos + ["El NIF de la empresa figura como destinatario: es una factura de proveedor."]}
    if emisor and emisor == nif_emp:
        # antes que la copia de una factura expedida: si el papel de emisor es dudoso, la coincidencia de número no prueba nada
        if (ext.get("emisor", {}).get("nif") or {}).get("papel_dudoso"):
            return {"sugerida": "desconocido", "confianza": 0.3, "motivos": motivos + [
                "El NIF de la empresa se ha tomado como emisor con pocos indicios o en contra de la maquetación (p. ej. el "
                "otro NIF ocupa la cabecera del emisor y no hay rótulo «Cliente»): puede ser una factura recibida. "
                "Revise emisor y destinatario e indique la situación del documento."]}
        if previas:
            p = previas[0]
            donde = (f"la factura {p.get('num_serie')} ya expedida con esta aplicación" if p.get("tipo") == "factura"
                     else f"el documento «{p.get('nombre')}», archivado como factura ya expedida")
            return {"sugerida": "emitida_propia", "confianza": 0.85, "motivos": motivos + [
                f"El mismo emisor y número figuran en {donde}: es otra copia de una factura expedida, que no puede volver a "
                "expedirse. Solo procede archivarla."]}
        propuestas = [d for d in duplicados or [] if d.get("tipo") == "documento" and d.get("clasificacion") == "emitida_propia"]
        if propuestas:
            motivos.append(f"El mismo emisor y número figuran en «{propuestas[0].get('nombre')}», propuesto (sin confirmar) "
                           "como factura ya expedida: si lo es, este documento es otra copia y solo procede archivarlo.")
        if tipo in ("proforma", "presupuesto", "borrador"):
            dudoso = (ext.get("tipo_documento") or {}).get("confianza", 1.0) < UMBRAL_CONFIANZA
            return {"sugerida": "preparatorio", "confianza": 0.6 if dudoso else 0.9,
                    "motivos": motivos + [f"Documento titulado como {tipo}" + (
                        " (lectura dudosa del título: compruébelo en el original)." if dudoso else ".")]}
        firmas = [f for f in inspeccion.get("firmas", []) if f.get("firmado")]
        if firmas:
            f0 = firmas[0]
            cuando = _fecha_firma_legible(f0.get("fecha"))
            return {"sugerida": "emitida_propia", "confianza": 0.8, "motivos": motivos + [
                f"El PDF está firmado electrónicamente ({f0.get('nombre') or 'firmante no indicado'}, "
                f"{f'el {cuando}' if cuando else 'sin fecha legible'}): "
                "indica que la factura ya se expidió y se entregó. Una factura expedida no se vuelve a expedir ni se le añade un "
                "QR después; solo procede archivarla. Si en realidad es un borrador que aún no se ha entregado, confírmelo como "
                "documento preparatorio (el PDF definitivo no conservará la firma)."]}
        motivos.append("La empresa es el emisor y el documento se titula «factura».")
        motivos.append("Indique si es un documento preparado para expedirse con esta aplicación (borrador) o una "
                       "factura ya entregada al cliente (en ese caso solo se archiva).")
        return {"sugerida": "pendiente_confirmar", "confianza": 0.5, "motivos": motivos}
    if not emisor and not dest:
        return {"sugerida": "desconocido", "confianza": 0.0,
                "motivos": motivos + ["No se identifican el emisor ni el destinatario con NIF válido."]}
    return {"sugerida": "desconocido", "confianza": 0.2,
            "motivos": motivos + ["El NIF de la empresa no figura como emisor ni como destinatario."]}


def _cifras_extraccion(ext: dict) -> tuple[list[dict], str | None, str | None, str | None]:
    """Desglose sujeto, base imponible, cuota y total del borrador a partir de la extracción (antes de proponer exentas)."""
    desglose = []
    for d in ext.get("desglose", []):
        desglose.append({"tipo_iva": _v(d["tipo_iva"]), "base": _v(d["base"]), "cuota": _v(d["cuota"]),
                         "calificacion": "S1", "clave_regimen": "01", "impuesto": "01"})
    total = _v(ext.get("total_factura", {}))
    if total is None and desglose:
        try:
            total = str(q2(sum(Decimal(d["base"]) + Decimal(d["cuota"]) for d in desglose)))
        except (TypeError, InvalidOperation):
            total = None
        # Total no leído: se deduce como base + cuota (validar() pide confirmarlo), salvo que el cuadro de totales imprima
        # una cifra mayor sin interpretar (otro total, tasas…): entonces se deja vacío para que el usuario lo indique
        if total is not None and any(_dec(x) is not None and _dec(x) > Decimal(total)
                                     for x in (ext.get("total_factura") or {}).get("sin_clasificar") or []):
            total = None
    return desglose, _v(ext.get("base_imponible", {})), _v(ext.get("cuota_iva", {})), total


def datos_desde_extraccion(ext: dict, nombre_archivo: str = "") -> dict:
    lineas = []
    for l in ext.get("lineas", []):
        lineas.append({k: _v(l.get(k, {})) for k in ("concepto", "cantidad", "unidad", "precio", "importe")})
    ret = ext.get("retencion")
    refs = {k: _v(v) for k, v in (ext.get("referencias") or {}).items()}
    primera = (lineas[0]["concepto"] if lineas and lineas[0].get("concepto") else "") or ""
    descripcion = re.sub(r"\s+", " ", primera).strip()[:500]
    pagar = _v(ext.get("total_a_pagar", {}))
    desglose, base_imponible, cuota_total, total = _cifras_extraccion(ext)
    desglose, cuota_total, base_imponible = _proponer_exencion(ext, desglose, base_imponible, cuota_total, total)
    return {
        "tipo_documento_origen": _v(ext.get("tipo_documento", {})),
        "num_serie": _v(ext.get("numero", {})),
        "fecha_documento": _v(ext.get("fecha", {})),
        "fecha_operacion": _v(ext.get("fecha_operacion", {})),
        "tipo_factura": "F1",
        "emisor": {k: _v(ext["emisor"][k]) for k in ("nombre", "nif", "domicilio")},
        "destinatario": {k: _v(ext["destinatario"][k]) for k in ("nombre", "nif", "domicilio")},
        "descripcion_operacion": descripcion,
        "lineas": lineas,
        "desglose": desglose,
        "base_imponible": base_imponible,
        "cuota_total": cuota_total,
        "importe_total": total,
        "retencion": {"porcentaje": _v(ret["porcentaje"]), "importe": _v(ret["importe"])} if ret else None,
        "total_a_pagar": pagar or total,
        "referencias": refs,
        "forma_pago": _v(ext.get("forma_pago", {})) if ext.get("forma_pago") else None,
        "iban": _v(ext.get("iban", {})) if ext.get("iban") else None,
        "vencimiento": _v(ext.get("vencimiento", {})) if ext.get("vencimiento") else None,
        "observaciones": "",
        "indicios": sorted((ext.get("indicios") or {}).keys()),
        "confirmaciones": [],
    }


def fila_exenta(base: str | None, causa: str | None = None, motivo: str | None = None) -> dict:
    """Fila exenta del desglose (sin tipo ni cuota). Sin causa es una base sin IVA cuyo tratamiento debe elegir el usuario."""
    return {"tipo_iva": None, "base": base, "cuota": None, "calificacion": None, "clave_regimen": clave_regimen_exenta(causa),
            "impuesto": "01", "operacion_exenta": causa, "motivo_exencion": (motivo or mencion_exencion(causa)) if causa else None}


def _proponer_exencion(ext: dict, desglose: list[dict], base_txt, cuota_txt,
                       total_txt, origen: dict | None = None) -> tuple[list[dict], str | None, str | None]:
    """Propuesta de bases exentas a partir de la extracción (siempre para confirmar: validar() pide confirmar la causa).
    Devuelve (desglose, cuota total, base imponible). Si separa una base exenta con causa de una factura mixta, anota en
    `origen` (si se pasa) de dónde sale el importe (ver origen_base_exenta).

    - Sin IVA en todo el documento (desglose vacío o solo filas al 0 % sin cuota, base = total, cuota cero o vacía): una base
      exenta con la causa leída en el documento (solo si cita el artículo de la Ley del IVA: «exenta… art. 20…»: E1;
      art. 21: E2…; ver Extractor.exencion) o, si no, sin causa, para que el usuario la elija (o corrija el IVA): una
      mención sin artículo («exenta de IVA») o un «exento» de otro contexto («exento de responsabilidad») no da causa, y
      la base sin calificar sigue bloqueada (SIN_IVA).
    - Factura mixta: filas sujetas con cuota y una exención del IVA mencionada en el documento, con base > suma de las bases
      sujetas, cuota = suma de sus cuotas y total = base + cuota: la diferencia es una base sin IVA. Sin el artículo citado
      queda sin causa (bloqueada); con él, la causa solo se propone si el documento respalda la diferencia
      (_respaldo_exenta: una base exenta impresa igual a ella o las líneas de la operación exenta) y no hay importes
      ajenos a la base (INDICIOS_SIN_MIXTA); si no, el desglose no cambia (la base leída puede ser un SUBTOTAL o una
      SUMA de las líneas, que solo dice que la diferencia no lleva IVA). Si la extracción no separa así las bases, ver
      _mixta_impresa (solo con causa).
    En cualquier otro caso el desglose no cambia (los descuadres los señala la validación)."""
    base, cuota, total = _dec(base_txt), _dec(cuota_txt), _dec(total_txt)
    if total is None:
        return desglose, cuota_txt, base_txt

    def cero(d):  # fila al 0 % (o sin tipo) sin cuota: el «IVA 0,00» o «Exento» de una factura sin IVA
        t, b, c = _dec(d.get("tipo_iva")), _dec(d.get("base")), _dec(d.get("cuota"))
        return c is not None and c == 0 and (t is None or t == 0) and b is not None and b != 0
    sujetas = [d for d in desglose if not cero(d)]
    ceros = [d for d in desglose if cero(d)]
    # La causa solo se toma si viene de un artículo citado («articulo»): una extracción de una versión anterior sin
    # «exencion» (aunque tenga el indicio) o con una causa sin artículo no propone ninguna
    ex = ext.get("exencion") if isinstance(ext.get("exencion"), dict) else None
    causa = ex.get("causa") if ex and ex.get("causa") in CAUSAS_EXENCION and ex.get("articulo") else None
    motivo = ex.get("motivo") if ex and isinstance(ex.get("motivo"), str) and ex["motivo"].strip() else None
    if base is None or base <= 0:
        # base imponible no leída («SUMA 750,00 / IVA 21 % s/ 100,00 21,00 / TOTAL 771,00»): solo la factura mixta impresa
        mixta = _mixta_impresa(ext, sujetas, None, cuota, causa, motivo, origen) \
            if base is None and sujetas and not ceros else None
        return mixta or (desglose, cuota_txt, base_txt)
    if not sujetas:
        if q2(base) != q2(total) or (cuota is not None and q2(cuota) != 0):
            return desglose, cuota_txt, base_txt
        if ceros and q2(sum(_dec(d["base"]) for d in ceros)) != q2(base):
            return desglose, cuota_txt, base_txt
        return [fila_exenta(str(q2(base)), causa, motivo)], "0.00", base_txt
    if not ex or not (causa or ex.get("iva")) or cuota is None:   # «exento» de otro contexto: no es una base exenta
        return desglose, cuota_txt, base_txt
    try:
        b_suj = sum(Decimal(d["base"]) for d in sujetas)
        c_suj = sum(Decimal(d["cuota"]) for d in sujetas)
    except (TypeError, InvalidOperation):
        b_suj = c_suj = None
    resto = q2(base - b_suj) if b_suj is not None else None
    if resto is None or resto <= 0 or q2(c_suj) != q2(cuota) or q2(base + cuota) != q2(total) \
            or (ceros and q2(sum(_dec(d["base"]) for d in ceros)) != resto):
        mixta = None if ceros else _mixta_impresa(ext, sujetas, base, cuota, causa, motivo, origen)
        return mixta or (desglose, cuota_txt, base_txt)
    if causa is not None:
        # la base imponible leída puede ser un SUBTOTAL o una SUMA de las líneas: la diferencia solo es exenta por la causa
        # citada si el propio documento la respalda (una base exenta impresa igual a ella —«Base exenta 650,00» o una fila
        # al 0 % sin cuota— o las líneas de la operación exenta) y no hay importes ajenos a la base
        indicios = ext.get("indicios") if isinstance(ext.get("indicios"), dict) else {}
        imp = ex.get("importes") if isinstance(ex.get("importes"), dict) else {}
        exentas = {q2(x) for x in (_dec(v) for v in imp.get("exentas") or []) if x is not None}
        if any(k in indicios for k in INDICIOS_SIN_MIXTA) or (exentas and exentas != {resto}):
            return desglose, cuota_txt, base_txt
        respaldo = _respaldo_exenta(ext, resto, q2(b_suj), impresa=bool(ceros) or exentas == {resto})
        if respaldo is None:
            return desglose, cuota_txt, base_txt
        if origen is not None:
            origen.update(respaldo, base=str(resto),
                          calculo=f"base imponible leída {q2(base)} − bases sujetas {q2(b_suj)}")
    return sujetas + [fila_exenta(str(resto), causa, motivo)], cuota_txt, base_txt


# Particularidades que cambian las cuentas del cuadro de totales o que no son base exenta por la causa citada: con ellas la
# diferencia no se propone como base exenta (tasas, fianzas, indemnizaciones, provisiones de fondos, gastos por cuenta del
# cliente o repercutidos: Extractor.indicios, «importes_ajenos»). Dependen de que el documento use esas palabras
INDICIOS_SIN_MIXTA = ("suplidos", "recargo_equivalencia", "inversion_sujeto_pasivo", "no_sujeta", "importes_ajenos")

# Objeto de las exenciones más habituales del art. 20.Uno de la Ley 37/1992 (texto sin tildes y en minúsculas): una línea de
# conceptos es de la operación exenta si nombra el mismo objeto que la frase de la exención (o el del apartado citado)
TEMAS_EXENCION = {
    "vivienda": r"\bviviendas?\b|\bpisos?\b",
    "ensenanza": r"\bensenanzas?\b|\beducaci|\beducativ|\bformaci[o]n\b|\bformativ|\bcursos?\b|\bclases?\b|\bmatricula|"
                 r"\bacademi|\bescolar|\bdocen|\bleccion|\btutoria|\bidiomas?\b|\bcolegio|\bguarderia",
    "sanitaria": r"\bsanitari|\bmedic|\bhospital|\bclinic|\bdental|\bodontolog|\bfisioterap|\bpsicolog|\benfermer|"
                 r"\bterapi|\brehabilitaci|\bpodolog",
    "seguro": r"\bseguros?\b|\bpolizas?\b|\breaseguros?\b",
    "financiera": r"\bprestamos?\b|\bcreditos?\b|\bintereses\b|\bfinancier|\bavales?\b",
    "deportiva": r"\bdeport|\bgimnasio|\bpolideportivo",
    "cultural": r"\bcultura|\bmuseos?\b|\bbibliotecas?\b|\bteatros?\b|\bconciertos?\b",
    "social": r"\basistencia social|\bresidencia de (?:mayores|ancianos)|\bdependencia\b|\bdiscapacidad|\bcentro de dia\b",
    "terrenos": r"\bterrenos?\b|\bsolar(?:es)?\b|\brustic",
}
# apartado del art. 20.Uno citado → objeto
TEMAS_APARTADO = {2: "sanitaria", 3: "sanitaria", 4: "sanitaria", 5: "sanitaria", 8: "social", 9: "ensenanza",
                  10: "ensenanza", 13: "deportiva", 14: "cultural", 16: "seguro", 18: "financiera", 20: "terrenos",
                  23: "vivienda"}
RE_APARTADO_20 = re.compile(r"\b20\s*\.?\s*(?:uno|1)\s*[.,]?\s*(\d{1,2})\s*(?:o\b|\.|º|\b)")
# palabras de la frase de la exención que no dicen su objeto
PALABRAS_GENERICAS = frozenset(
    "exento exenta exentos exentas exencion exenciones articulo impuesto valor anadido operacion operaciones servicio "
    "servicios prestacion prestaciones factura facturas segun conforme alquiler alquileres arrendamiento arrendamientos "
    "arrendamiento leyes sobre importe importes total gastos concepto conceptos mensual enero febrero marzo abril mayo "
    "junio julio agosto septiembre octubre noviembre diciembre regimen general especial directiva real decreto previsto "
    "prevista dispuesto establecido apartado numero estan dicha dicho presente objeto entrega entregas actividad "
    "actividades realizada realizadas sujeta sujetas sujeto regulado regulada recogido recogida previsto aplicable "
    "aplicacion vigente normativa tributaria tributario presente".split())
# se comparan los seis primeros caracteres: «impuestos» o «servicio» son tan genéricos como «impuesto» o «servicios»
_PREFIJOS_GENERICOS = frozenset(w[:6] for w in PALABRAS_GENERICAS)


def _normal(t) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().casefold()


def _palabras(t: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{5,}", t) if w[:6] not in _PREFIJOS_GENERICOS}


def _respaldo_exenta(ext: dict, resto: Decimal, b_suj: Decimal, impresa: bool = False) -> dict | None:
    """Respaldo en el propio documento de una base exenta calculada como diferencia (factura mixta): las líneas de conceptos
    de la operación exenta (las que dicen «exento/a» o nombran el objeto de la frase de la exención o del apartado del
    art. 20.Uno citado) suman exactamente la diferencia y las demás líneas suman exactamente las bases sujetas; o bien
    (impresa) el documento imprime la base exenta. Devuelve {"lineas": [[concepto, importe], …], "impresa": bool} o None.
    Un SUBTOTAL o una SUMA igual a la suma de las líneas no respalda nada: solo dice que la diferencia no lleva IVA. Una
    línea con un importe ajeno a la base (tasa, fianza, indemnización…: el indicio «importes_ajenos») tampoco: devuelve None
    aunque el indicio no figure en la extracción."""
    from .extraccion.campos import Extractor
    ex = ext.get("exencion") if isinstance(ext.get("exencion"), dict) else {}
    # la frase de la exención (la mención propuesta; el renglón leído puede llevar otras frases: «Gastos… (art. 20 LAU)»)
    frase = _normal(ex.get("motivo") or ex.get("texto") or "")
    temas = {t for t, rx in TEMAS_EXENCION.items() if re.search(rx, frase)}
    for m in RE_APARTADO_20.finditer(frase):
        if int(m.group(1)) in TEMAS_APARTADO:
            temas.add(TEMAS_APARTADO[int(m.group(1))])
    propias = _palabras(re.sub(r"\(.*?\)", " ", frase))
    lineas, suma, ligadas, suma_ligadas = [], Decimal(0), [], Decimal(0)
    for l in ext.get("lineas") or []:
        concepto = _v(l.get("concepto")) if isinstance(l, dict) else None
        imp = _dec(_v(l.get("importe"))) if isinstance(l, dict) else None
        if not isinstance(concepto, str) or imp is None:
            lineas = None
            break
        if Extractor._importe_ajeno(concepto):
            return None
        c = _normal(concepto)
        suma += imp
        if re.search(r"\bexent[oa]s?\b", c) or any(re.search(TEMAS_EXENCION[t], c) for t in temas) \
                or _palabras(c) & propias:
            ligadas.append([re.sub(r"\s+", " ", concepto).strip()[:80], str(q2(imp))])
            suma_ligadas += imp
    if lineas is not None and ligadas and q2(suma_ligadas) == q2(resto) and q2(suma - suma_ligadas) == q2(b_suj):
        return {"lineas": ligadas, "impresa": impresa}
    return {"lineas": [], "impresa": True} if impresa else None


def origen_base_exenta(ext: dict | None) -> dict | None:
    """De dónde sale la base exenta que la extracción propone en una factura mixta: {"base", "calculo", "lineas",
    "impresa"} (ver _respaldo_exenta), o None si no propone ninguna (o no es mixta). Lo usa la confirmación EXENTA_CAUSA."""
    if not isinstance(ext, dict):
        return None
    try:
        desglose, base, cuota, total = _cifras_extraccion(ext)
        origen: dict = {}
        _proponer_exencion(ext, desglose, base, cuota, total, origen)
    except Exception:  # noqa: BLE001  (una extracción guardada por otra versión: la confirmación sale sin el origen)
        return None
    return origen or None


def _mixta_impresa(ext: dict, sujetas: list[dict], base: Decimal | None, cuota: Decimal | None, causa: str | None,
                   motivo: str | None, origen: dict | None = None) -> tuple[list[dict], str, str] | None:
    """Factura mixta cuyas bases no separa la extracción («BASE IMPONIBLE 750 / BASE 21 % 100 / IVA 21 % 21 / TOTAL 771»,
    «BASE IMPONIBLE 750 / I.V.A. 21 % s/ 100,00 21,00 / TOTAL 771», «BASE EXENTA 650 / BASE 4 % 100 / IVA 4 % 4 /
    TOTAL 754»): la base exenta se propone a partir de las cifras impresas en el cuadro de totales
    (Extractor._importes_factura_mixta) solo si
    - el documento cita el artículo 20–25 de la Ley del IVA que da la causa (una tasa, un suplido o unos honorarios
      «exentos» sin ese artículo no se convierten en base exenta) y no tiene suplidos, recargo de equivalencia, inversión
      del sujeto pasivo, operaciones no sujetas ni importes ajenos a la base (INDICIOS_SIN_MIXTA);
    - cada fila sujeta cuadra al céntimo (base × tipo = cuota; la base es la impresa en «IVA 21 % s/ 100,00» si la leída
      no cuadra) y la suma de sus cuotas es la cuota total impresa;
    - la base exenta, total − bases sujetas − cuotas, es positiva y el documento la respalda: es la cifra impresa como
      base exenta (si se imprime alguna, todas deben ser esa) o, si no se imprime, las líneas de la operación exenta suman
      exactamente esa cifra y las demás, las bases sujetas (_respaldo_exenta). Una base imponible total, un SUBTOTAL o
      una SUMA impresos no bastan: suelen ser la suma de las líneas y solo dicen que la diferencia no lleva IVA.
    Devuelve (desglose, cuota total, base imponible) o None (el desglose no cambia y el descuadre sigue bloqueando)."""
    ex = ext.get("exencion") if isinstance(ext.get("exencion"), dict) else {}
    imp = ex.get("importes") if isinstance(ex.get("importes"), dict) else None
    total = _dec(_v(ext.get("total_factura") or {}))   # el total impreso, nunca uno deducido
    indicios = ext.get("indicios") if isinstance(ext.get("indicios"), dict) else {}
    if causa is None or imp is None or total is None or cuota is None or any(k in indicios for k in INDICIOS_SIN_MIXTA):
        return None
    sobre = {}
    for s in imp.get("sobre") or []:
        t, b, c = (_dec(s.get(k)) if isinstance(s, dict) else None for k in ("tipo", "base", "cuota"))
        if None not in (t, b, c):
            sobre.setdefault(q2(t), set()).add((q2(b), q2(c)))
    filas = []
    for d in sujetas:
        t, b, c = _dec(d.get("tipo_iva")), _dec(d.get("base")), _dec(d.get("cuota"))
        if t is None or t <= 0 or c is None or c <= 0:
            return None
        if b is None or q2(b * t / Decimal(100)) != q2(c):
            impresas = {bs for bs, cs in sobre.get(q2(t), set()) if cs == q2(c) and q2(bs * t / Decimal(100)) == q2(c)}
            if len(impresas) != 1:
                return None
            b = impresas.pop()
        filas.append(dict(d, base=str(q2(b))))
    b_suj = q2(sum(Decimal(d["base"]) for d in filas))
    c_suj = q2(sum(_dec(d["cuota"]) for d in filas))
    resto = q2(total - b_suj - c_suj)
    if c_suj != q2(cuota) or resto <= 0:
        return None
    exentas = {q2(x) for x in (_dec(v) for v in imp.get("exentas") or []) if x is not None}
    if exentas and exentas != {resto}:
        return None
    respaldo = _respaldo_exenta(ext, resto, b_suj, impresa=bool(exentas))
    if respaldo is None:
        return None
    if origen is not None:
        origen.update(respaldo, base=str(resto), calculo=f"total impreso {q2(total)} − bases sujetas y cuotas {b_suj + c_suj}")
    return filas + [fila_exenta(str(resto), causa, motivo)], str(c_suj), str(q2(b_suj + resto))


# Rutas de los campos editables del borrador
CAMPOS_EDITABLES = re.compile(
    r"^(num_serie|fecha_documento|fecha_operacion|descripcion_operacion|base_imponible|cuota_total|importe_total|"
    r"total_a_pagar|forma_pago|iban|vencimiento|observaciones|"
    r"(emisor|destinatario)\.(nombre|nif|domicilio)|referencias\.[a-z_]{1,30}|retencion\.(porcentaje|importe)|"
    r"lineas\.\d{1,3}\.(concepto|cantidad|unidad|precio|importe)|"
    r"desglose\.\d{1,2}\.(tipo_iva|base|cuota|calificacion|operacion_exenta|motivo_exencion)|"
    r"lineas|desglose|retencion)$")


CAMPOS_NUMERICOS = re.compile(r"^(base_imponible|cuota_total|importe_total|total_a_pagar|retencion\.(porcentaje|importe)|"
                              r"lineas\.\d+\.(cantidad|precio|importe)|desglose\.\d+\.(tipo_iva|base|cuota))$")
RE_NUMERO = re.compile(r"^[+-]?\d{1,12}(?:\.\d{1,6})?$")
CLAVES_LISTA = {"lineas": ("concepto", "cantidad", "unidad", "precio", "importe"),
                "desglose": ("tipo_iva", "base", "cuota", "calificacion", "clave_regimen", "impuesto", "operacion_exenta",
                             "motivo_exencion")}
MAX_TEXTO = 5000
# Tamaño de la factura compuesta (pdf_final): un borrador que ocuparía más renglones no se compone ni se expide. Sin este
# límite, conceptos de miles de renglones (admitidos uno a uno) harían que cada vista previa o expedición tardase minutos
MAX_RENGLONES_PDF = 3000


def _renglones(v, ancho: int) -> int:
    if not isinstance(v, str) or not v:
        return 0
    return sum(max(1, -(-len(s) // ancho)) for s in v.split("\n"))


def renglones_estimados(datos: dict) -> int:
    """Cota superior de los renglones que ocupará la factura compuesta (pdf_final.reconstruir): solo los textos que se
    imprimen, cada uno al ancho de su columna con letra ancha (≈ 0,4 caracteres por mm: concepto 38, unidad 5, partes
    36, referencias 55, notas a todo el ancho 72); cada fila de conceptos ocupa lo que su celda más alta."""
    def lista(v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    def dic(v):
        return v if isinstance(v, dict) else {}
    n = 0
    for fila in lista(datos.get("lineas")):
        n += max(1, _renglones(fila.get("concepto"), 38), _renglones(fila.get("unidad"), 5),
                 _renglones(fila.get("cantidad"), 8), _renglones(fila.get("precio"), 9), _renglones(fila.get("importe"), 10))
    for parte in ("emisor", "destinatario"):
        n += sum(_renglones(dic(datos.get(parte)).get(k), 36) for k in ("nombre", "nif", "domicilio"))
    for k, v in dic(datos.get("referencias")).items():
        n += max(_renglones(k if isinstance(k, str) else "", 16), _renglones(v, 55))
    n += 2 * len(lista(datos.get("desglose")))
    n += sum(_renglones(f.get("motivo_exencion"), 72) for f in lista(datos.get("desglose")) if f.get("operacion_exenta"))
    rect = dic(datos.get("rectificativa"))
    n += _renglones(rect.get("motivo"), 72) + _renglones(rect.get("num_serie_rectificada"), 72)
    n += sum(_renglones(datos.get(k), 72) for k in ("observaciones", "forma_pago", "iban", "vencimiento"))
    return n


def texto_codificable(t: str) -> bool:
    """Un sustituto UTF-16 suelto (JSON «\\ud800») no se puede guardar en UTF-8: el texto se rechaza antes de guardarlo."""
    return not re.search("[\ud800-\udfff]", t)


def _escalar(ruta: str, valor):
    """Valor de un campo simple: texto o vacío (los importes, además, con formato numérico «1234.56»)."""
    if valor is None:
        return None
    numerico = bool(CAMPOS_NUMERICOS.match(ruta))
    if numerico and isinstance(valor, (int, float)) and not isinstance(valor, bool):
        valor = str(valor)
    if not isinstance(valor, str):
        raise ValueError(f"{ruta}: se esperaba un texto")
    valor = valor.strip()
    if valor == "":
        return None
    if len(valor) > MAX_TEXTO:
        raise ValueError(f"{ruta}: texto demasiado largo")
    if not texto_codificable(valor):
        raise ValueError(f"{ruta}: el texto contiene caracteres no válidos (sustitutos UTF-16 sueltos)")
    if numerico and not RE_NUMERO.match(valor):
        raise ValueError(f"{ruta}: «{valor[:40]}» no es un número válido (use punto decimal, p. ej. 1028.50)")
    if ruta.endswith(".nif"):
        valor = nifmod.normalizar(valor)
    if ruta.startswith("desglose.") and ruta.endswith(".motivo_exencion"):
        valor = normalizar_mencion(valor)   # CRLF, CR y tabuladores de un cliente de la API: como los guarda la interfaz
    if ruta.startswith("desglose.") and ruta.endswith(".operacion_exenta") and valor not in CAUSAS_EXENCION:
        raise ValueError(f"{ruta}: «{valor[:10]}» no es una causa de exención (E1, E2, E3, E4, E5 o E6)")
    if ruta.startswith("desglose.") and ruta.endswith(".calificacion") and valor not in ("S1", "S2", "N1", "N2"):
        raise ValueError(f"{ruta}: «{valor[:10]}» no es una calificación de la operación (S1, S2, N1 o N2)")
    return valor


def _valor_tipado(ruta: str, valor):
    """Comprueba el tipo del valor según la ruta (las ediciones con tipos inesperados se rechazan, no se guardan)."""
    if ruta in CLAVES_LISTA:
        if not isinstance(valor, list):
            raise ValueError(f"{ruta}: se esperaba una lista")
        if len(valor) > (999 if ruta == "lineas" else 99):
            raise ValueError(f"{ruta}: demasiados elementos")
        out = []
        for i, el in enumerate(valor):
            if not isinstance(el, dict):
                raise ValueError(f"{ruta}.{i}: se esperaba un objeto con los campos de la fila")
            extra = set(el) - set(CLAVES_LISTA[ruta])
            if extra:
                raise ValueError(f"{ruta}.{i}: campos no admitidos: {', '.join(sorted(map(str, extra)))}")
            out.append({k: _escalar(f"{ruta}.{i}.{k}", v) for k, v in el.items()})
        return out
    if ruta == "retencion":
        if valor is None:
            return None
        if not isinstance(valor, dict) or set(valor) - {"porcentaje", "importe"}:
            raise ValueError("retencion: se esperaba un objeto con «porcentaje» e «importe»")
        return {k: _escalar(f"retencion.{k}", valor.get(k)) for k in ("porcentaje", "importe")}
    return _escalar(ruta, valor)


def aplicar_cambio(datos: dict, ruta: str, valor) -> tuple[object, dict]:
    if not isinstance(ruta, str) or not CAMPOS_EDITABLES.match(ruta):
        raise ValueError(f"Campo no editable: {ruta}")
    valor = _valor_tipado(ruta, valor)
    nuevo = copy.deepcopy(datos)
    partes = ruta.split(".")
    obj = nuevo
    for n, p in enumerate(partes[:-1]):
        sig_es_lista = partes[n + 1].isdigit()
        if p.isdigit():
            idx = int(p)
            while len(obj) <= idx:
                obj.append({})
            if not isinstance(obj[idx], dict):
                obj[idx] = {}  # fila con un valor no válido guardado: se rehace al editarla
            obj = obj[idx]
        else:
            if not isinstance(obj.get(p), list if sig_es_lista else dict):
                obj[p] = [] if sig_es_lista else {}  # p. ej. «retencion» vacía al editar su importe
            obj = obj[p]
    ult = partes[-1]
    anterior = obj.get(ult)
    obj[ult] = valor
    return anterior, nuevo


def campos_dudosos(ext: dict) -> list[dict]:
    """Campos extraídos con confianza baja o con avisos de lectura (OCR, sin etiqueta...)."""
    out = []

    def visitar(c, ruta):
        if isinstance(c, dict) and "valor" in c and "confianza" in c:
            if c["valor"] is not None and c["confianza"] < UMBRAL_CONFIANZA:
                out.append({"campo": ruta, "valor": c["valor"], "confianza": c["confianza"], "pagina": c.get("pagina"),
                            "bbox": c.get("bbox"), "metodo": c.get("metodo")})
        elif isinstance(c, dict):
            for k, v in c.items():
                if k not in ("paginas", "indicios", "nifs_no_validos"):
                    visitar(v, f"{ruta}.{k}" if ruta else k)
        elif isinstance(c, list):
            for i, v in enumerate(c):
                visitar(v, f"{ruta}.{i}")
    visitar(ext, "")
    return out


def _dec(x) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        return None
    # NaN, infinitos o magnitudes absurdas harían fallar la aritmética de la validación: se tratan como no válidos
    return d if d.is_finite() and abs(d) < Decimal("1e12") else None


def saneado(datos: dict) -> dict:
    """Datos del borrador con los valores de tipo inesperado vacíos (p. ej. para componer la vista previa)."""
    return _saneado(datos)[0]


def _saneado(datos: dict) -> tuple[dict, list[str]]:
    """Copia para validar en la que todo valor con un tipo inesperado (datos guardados por una versión anterior o
    corruptos) queda vacío; devuelve también sus rutas, que se notifican como bloqueo. validar() nunca debe fallar."""
    malos: list[str] = []

    def esc(ruta, v):
        if v is None or isinstance(v, str):
            return v
        malos.append(ruta)
        return None

    def filas(clave, claves):
        v = d.get(clave)
        if v is None:
            return []
        if not isinstance(v, list):
            malos.append(clave)
            return []
        out = []
        for i, el in enumerate(v):
            if not isinstance(el, dict):
                malos.append(f"{clave}.{i}")
                el = {}
            out.append({**el, **{k: esc(f"{clave}.{i}.{k}", el.get(k)) for k in claves}})
        return out

    d = dict(datos) if isinstance(datos, dict) else {}
    for k in ("num_serie", "fecha_documento", "fecha_operacion", "descripcion_operacion", "base_imponible", "cuota_total",
              "importe_total", "total_a_pagar", "forma_pago", "iban", "vencimiento", "observaciones"):
        d[k] = esc(k, d.get(k))
    for parte in ("emisor", "destinatario"):
        v = d.get(parte)
        if v is not None and not isinstance(v, dict):
            malos.append(parte)
        v = v if isinstance(v, dict) else {}
        d[parte] = {**v, **{k: esc(f"{parte}.{k}", v.get(k)) for k in ("nombre", "nif", "domicilio")}}
    d["lineas"] = filas("lineas", CLAVES_LISTA["lineas"])
    d["desglose"] = filas("desglose", CLAVES_LISTA["desglose"])
    ret = d.get("retencion")
    if ret is not None and not isinstance(ret, dict):
        malos.append("retencion")
        ret = None
    d["retencion"] = {k: esc(f"retencion.{k}", ret.get(k)) for k in ("porcentaje", "importe")} if ret else None
    refs = d.get("referencias")
    if refs is not None and not isinstance(refs, dict):
        malos.append("referencias")
    d["referencias"] = {k: esc(f"referencias.{k}", v) for k, v in refs.items()} if isinstance(refs, dict) else {}
    for k in ("indicios", "confirmaciones"):
        v = d.get(k)
        d[k] = [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    return d, malos


def validar(con, empresa: dict, documento: dict | None, datos: dict, *, extraccion: dict | None = None,
            serie: dict | None = None, es_rectificativa: bool = False, cambiados: set[str] | None = None) -> list[dict]:
    """Devuelve incidencias: nivel 'bloqueo' (impide emitir), 'confirmar' (requiere confirmación expresa) o 'aviso'."""
    inc: list[dict] = []
    cambiados = cambiados or set()

    def add(nivel, campo, codigo, mensaje, ref=None):
        # ref: lo que se confirma (p. ej. los documentos duplicados); si cambia, la confirmación dada ya no vale
        inc.append({"nivel": nivel, "campo": campo, "codigo": codigo, "mensaje": mensaje, **({"ref": ref} if ref else {})})

    datos, malos = _saneado(datos)
    for ruta in malos:
        add("bloqueo", ruta, "DATO_NO_VALIDO", f"El valor guardado en «{ruta}» no tiene un formato válido: corríjalo.")

    # --- Documento de origen
    if documento is not None:
        if documento.get("bloqueo_emision"):
            add("bloqueo", "documento", "DOC_BLOQUEADO", documento["bloqueo_emision"])
        if documento.get("clasificacion") != "preparatorio" or not documento.get("clasificacion_confirmada_en"):
            add("bloqueo", "documento", "DOC_NO_PREPARATORIO",
                "Solo puede expedirse a partir de un documento preparatorio confirmado como tal. "
                f"Clasificación actual: {documento.get('clasificacion') or 'sin confirmar'}.")
        insp = documento.get("inspeccion_dict") or {}
        if insp.get("firmado"):
            add("confirmar", "documento", "DOC_FIRMADO",
                "El original está firmado digitalmente. Se conserva intacto; el PDF definitivo será un documento "
                "distinto y NO conservará esa firma.")
    # --- Ámbito del emisor
    amb = empresa.get("ambito_dict") or {}
    if not empresa.get("ambito_confirmado_en"):
        add("bloqueo", "empresa", "AMBITO_SIN_CONFIRMAR", "Confirme el ámbito del emisor en la configuración.")
    elif not amb.get("puede_emitir"):
        add("bloqueo", "empresa", "AMBITO_NO_SOPORTADO", "; ".join(amb.get("motivos", ["Ámbito no soportado"])))

    # --- Partes
    rs = (empresa.get("razon_social") or "").strip()
    if not rs or len(rs) > 120:
        add("bloqueo", "empresa", "EMPRESA_RAZON_SOCIAL",
            "La razón social de la empresa configurada debe tener entre 1 y 120 caracteres (se usa en el registro).")
    em, de = datos.get("emisor") or {}, datos.get("destinatario") or {}
    if not em.get("nif"):
        add("bloqueo", "emisor.nif", "EMISOR_FALTA", "Falta el NIF del emisor.")
    elif nifmod.normalizar(em["nif"]) != nifmod.normalizar(empresa["nif"]):
        add("bloqueo", "emisor.nif", "EMISOR_NO_EMPRESA",
            f"El emisor ({em['nif']}) no es la empresa configurada ({empresa['nif']}).")
    if not em.get("nombre"):
        add("bloqueo", "emisor.nombre", "EMISOR_NOMBRE", "Falta la razón social del emisor.")
    elif empresa.get("razon_social") and em["nombre"].strip().upper() != empresa["razon_social"].strip().upper():
        add("aviso", "emisor.nombre", "EMISOR_NOMBRE_DISTINTO",
            f"La razón social del documento difiere de la configurada («{empresa['razon_social']}»). "
            "En el registro se usará la configurada.")
    if not de.get("nif"):
        add("bloqueo", "destinatario.nif", "DEST_FALTA", "Falta el NIF del destinatario (obligatorio en factura completa).")
    elif not nifmod.es_valido(de["nif"]):
        add("bloqueo", "destinatario.nif", "DEST_NIF", f"El NIF del destinatario ({de['nif']}) no es válido.")
    elif nifmod.normalizar(de["nif"]) == nifmod.normalizar(empresa["nif"]):
        add("bloqueo", "destinatario.nif", "DEST_ES_EMISOR", "El destinatario coincide con el emisor.")
    if not de.get("nombre"):
        add("bloqueo", "destinatario.nombre", "DEST_NOMBRE", "Falta la razón social del destinatario.")
    elif len(de["nombre"]) > 120:
        add("bloqueo", "destinatario.nombre", "DEST_NOMBRE_LARGO", "La razón social supera 120 caracteres.")
    elif _clave(de["nombre"]) in {_clave(em.get("nombre")), _clave(rs)} - {""}:
        add("bloqueo", "destinatario.nombre", "DEST_NOMBRE_ES_EMISOR",
            f"La razón social del destinatario («{de['nombre']}») es la del emisor: probablemente se ha leído del bloque "
            "del emisor. Escriba la del destinatario.")
    if not de.get("domicilio"):
        add("bloqueo", "destinatario.domicilio", "DEST_DOMICILIO",
            "Falta el domicilio del destinatario (contenido obligatorio de la factura completa, art. 6 RD 1619/2012).")
    elif any(len(k) >= 8 and not re.fullmatch(r"\d{5}[a-z]+", k) and k in _clave(de["domicilio"])  # no solo «CP ciudad»
             for k in {_clave(em.get("domicilio")), _clave(empresa.get("domicilio"))}):
        add("confirmar", "destinatario.domicilio", "DEST_DOMICILIO_EMISOR",
            "El domicilio del destinatario contiene el del emisor: compruebe que no se ha leído del bloque del emisor.")
    if not em.get("domicilio"):
        add("bloqueo", "emisor.domicilio", "EMISOR_DOMICILIO", "Falta el domicilio del emisor (art. 6 RD 1619/2012).")
    elif len(k_de := _clave(de.get("domicilio"))) >= 8 and not re.fullmatch(r"\d{5}[a-z]+", k_de) \
            and k_de in _clave(em["domicilio"]):  # (el inverso de DEST_DOMICILIO_EMISOR; no solo «CP ciudad»)
        add("confirmar", "emisor.domicilio", "EMISOR_DOMICILIO_DESTINATARIO",
            "El domicilio del emisor contiene el del destinatario: compruebe que no se ha leído del bloque del destinatario "
            "(p. ej. el recuadro de la dirección del cliente, si el emisor solo imprime la suya en el pie).")

    # --- Número y serie
    ns = (datos.get("num_serie") or "").strip()
    if not ns:
        add("bloqueo", "num_serie", "NUM_FALTA", "Falta el número de factura.")
    else:
        if len(ns) > 60 or not all(32 <= ord(ch) <= 126 for ch in ns) or any(ch in ns for ch in "\"'<>="):
            add("bloqueo", "num_serie", "NUM_FORMATO",
                "El número debe tener 1–60 caracteres ASCII imprimibles, sin \" ' < > = (validaciones AEAT).")
        if serie is None:
            add("bloqueo", "num_serie", "SERIE_NO_CONFIRMADA",
                f"«{ns}» no corresponde a ninguna serie confirmada. Configure y confirme la serie y su numeración.")
        else:
            esperado = serie["siguiente_numero"]
            num = serie.get("numero_detectado")
            if num is not None and num != esperado:
                add("bloqueo", "num_serie", "NUM_NO_CORRELATIVO",
                    f"El número {num} de la serie «{serie['codigo']}» no es el siguiente correlativo (se espera {esperado}).")
        from .importacion import documentos_mismo_numero, facturas_mismo_numero
        # mismo número aunque se imprima de otra forma equivalente («0166/26» y «166/26»; con el patrón de la serie,
        # «20260166» y «2026166» en «{aaaa}{n}»)
        patrones = [serie["patron"]] if serie and isinstance(serie.get("patron"), str) else []
        dup = (facturas_mismo_numero(con, empresa["id"], nifmod.normalizar(empresa["nif"]), ns, patrones=patrones) or [None])[0]
        if dup:
            add("bloqueo", "num_serie", "DUPLICADA",
                f"Ya existe la factura {dup['num_serie']} expedida (entorno {dup['entorno']}). No se puede volver a expedir.")
        # Otros PDF importados con el mismo emisor y número (p. ej. la copia firmada de una factura ya expedida)
        if documento is not None and documento.get("id"):
            archivados, otros, ids_otros = [], [], []
            for o in documentos_mismo_numero(con, empresa["id"], ns, excluir_id=documento["id"], patrones=patrones):
                propia = o["clasificacion"] == "emitida_propia"  # expedida por la empresa, aunque no se leyera su NIF
                if o["tiene_factura"] or not (propia or nifmod.normalizar(o["emisor_nif"] or "") == nifmod.normalizar(empresa["nif"])):
                    continue  # la factura expedida ya la cubre DUPLICADA; otro emisor no es la misma factura
                desc = f"«{o['nombre_archivo']}» (importado el {db.legible(o['subido_en'], hora=False)}"
                if propia:
                    archivados.append(desc + (", archivado)" if o["confirmado"] else ", propuesto para archivo)"))
                else:
                    # sin clasificar o clasificado como factura recibida (el NIF de la empresa leído como emisor puede ser
                    # un error de lectura de un proveedor): no consta como expedida, se pide confirmar que no es la misma
                    otros.append(desc + (", clasificado como factura recibida)" if o["clasificacion"] == "recibida" else ")"))
                    ids_otros.append(str(o["id"]))
            if archivados:
                add("bloqueo", "num_serie", "DUPLICADA_ARCHIVADA",
                    f"El número {ns} de este emisor figura en {', '.join(archivados)} como factura ya expedida: no puede "
                    "volver a expedirse. Si ese documento no es una factura expedida, revise antes su clasificación.")
            if otros:
                # la confirmación vale solo para estos documentos: otro importado después con el mismo número la pide de nuevo
                add("confirmar", "num_serie", "DUPLICADO_DOCUMENTO",
                    f"El mismo emisor y número {ns} figuran en otro documento importado: {', '.join(otros)}. Confirme que "
                    "no se trata de la misma factura.", ref=_ref_documentos(ids_otros))

    # --- Fechas (formato y calendario reales; límites de las validaciones AEAT)
    from datetime import date
    hoy_d = db.ahora().date()
    hoy = hoy_d.isoformat()
    for campo_f in ("fecha_documento", "fecha_operacion"):
        v = datos.get(campo_f)
        if v in (None, ""):
            continue
        try:
            fv = date.fromisoformat(str(v))
            if not 1900 <= fv.year <= 2999 or len(str(v)) != 10:
                raise ValueError
        except ValueError:
            add("bloqueo", campo_f, "FECHA_FORMATO", f"La fecha «{v}» no es válida (formato aaaa-mm-dd).")
            datos = dict(datos, **{campo_f: None})
            continue
        hace_20 = date(hoy_d.year - 20, hoy_d.month, min(hoy_d.day, 28))
        if campo_f == "fecha_operacion" and fv < hace_20:
            add("bloqueo", campo_f, "FECHA_OP_ANTIGUA",
                "La fecha de operación no puede ser anterior a hoy menos veinte años (validaciones AEAT).")
    fd = datos.get("fecha_documento")
    if fd and fd > hoy:
        add("bloqueo", "fecha_documento", "FECHA_FUTURA", "La fecha del documento es posterior a hoy.")
    if fd and fd != hoy:
        add("confirmar", "fecha_documento", "FECHA_EXPEDICION",
            f"El documento está fechado el {db.legible(fd, hora=False)}; la factura se expedirá con fecha de hoy "
            f"({db.legible(hoy, hora=False)}). "
            "Si la operación se realizó en otra fecha, indíquela como fecha de operación.")
    fo = datos.get("fecha_operacion")
    if fo and fo > hoy:
        add("bloqueo", "fecha_operacion", "FECHA_OP_FUTURA",
            "La fecha de operación no puede ser posterior a la de expedición (hoy) en régimen general (validaciones AEAT).")
    ref_plazo = fo or fd
    if ref_plazo and ref_plazo <= hoy:
        a, m = int(ref_plazo[:4]), int(ref_plazo[5:7])
        limite = f"{a + (m == 12):04d}-{(m % 12) + 1:02d}-15"
        if hoy > limite:
            add("aviso", "fecha_operacion", "PLAZO_EXPEDICION",
                f"Para destinatarios empresarios la factura debe expedirse antes del día 16 del mes siguiente a la operación "
                f"(art. 11 RD 1619/2012); el plazo terminó el {limite}. Indique la fecha de operación real si es distinta.")

    # --- Descripción
    desc = (datos.get("descripcion_operacion") or "").strip()
    if not desc:
        add("bloqueo", "descripcion_operacion", "DESC_FALTA", "Falta la descripción de la operación.")
    elif len(desc) > 500:
        add("bloqueo", "descripcion_operacion", "DESC_LARGA", "La descripción supera 500 caracteres.")

    # --- Importes (aritmética decimal exacta)
    desglose = datos.get("desglose") or []
    base = _dec(datos.get("base_imponible"))
    cuota = _dec(datos.get("cuota_total"))
    total = _dec(datos.get("importe_total"))
    if not desglose and base is not None and total is not None and base > 0 and q2(base) == q2(total) \
            and (cuota is None or q2(cuota) == 0):
        # factura sin IVA repercutido (p. ej. alquiler de vivienda): total = base, sin cuota ni desglose
        add("bloqueo", "desglose", "SIN_IVA",
            f"La factura no repercute IVA: el total ({q2(total)}) es igual a la base imponible y no hay cuota. Si la operación "
            "está exenta, añada en el desglose una base exenta y elija la causa de exención (E1–E6) y la mención legal; "
            "si está sujeta a IVA, indique el tipo, la base y la cuota en el desglose y corrija la cuota y el total. Las "
            "operaciones no sujetas al impuesto no están soportadas.")
    elif not desglose:
        add("bloqueo", "desglose", "DESGLOSE_FALTA", "No se ha identificado el desglose de bases y cuotas por tipo de IVA.")
    suma_base, suma_cuota = Decimal("0"), Decimal("0")
    ext_exencion = (extraccion or {}).get("exencion") if isinstance((extraccion or {}).get("exencion"), dict) else None
    origen = None   # de dónde sale la base exenta propuesta en una factura mixta (solo si hay alguna fila exenta)
    if ext_exencion and ext_exencion.get("causa") and any(clase_fila(d) == "exenta" for d in desglose):
        origen = origen_base_exenta(extraccion)
    # renglón con una tasa, fianza, indemnización… (indicio «importes_ajenos»): se cita al confirmar la causa de la exención
    ajeno = ((extraccion or {}).get("indicios") or {}).get("importes_ajenos") \
        if isinstance((extraccion or {}).get("indicios"), dict) else None
    ajeno = re.sub(r"\s+", " ", str(ajeno.get("texto") or "")).strip()[:80] if isinstance(ajeno, dict) else ""
    for i, d in enumerate(desglose):
        clase = clase_fila(d)
        if clase in ("exenta", "sin_calificar"):
            b = _dec(d.get("base"))
            if b is not None:
                suma_base += b
            _validar_fila_exenta(add, i, d, clase, b, es_rectificativa, ext_exencion, origen, ajeno)
            continue
        t, b, c = _dec(d.get("tipo_iva")), _dec(d.get("base")), _dec(d.get("cuota"))
        if t is None or b is None or c is None:
            add("bloqueo", f"desglose.{i}", "DESGLOSE_INCOMPLETO", "Desglose incompleto (tipo, base y cuota).")
            continue
        if t not in TIPOS_IVA_SOPORTADOS:
            add("bloqueo", f"desglose.{i}.tipo_iva", "TIPO_NO_SOPORTADO", f"Tipo de IVA {t} % no soportado." + (
                " Si la operación está exenta, márquela como exenta en el desglose y elija la causa (E1–E6)."
                if t == 0 and c == 0 else ""))
        if (d.get("calificacion") or "S1") != "S1" or (d.get("clave_regimen") or "01") != "01" \
                or (d.get("impuesto") or "01") != "01":
            add("bloqueo", f"desglose.{i}", "CALIFICACION_NO_SOPORTADA",
                "Solo se admiten operaciones sujetas y no exentas sin inversión del sujeto pasivo (S1) en régimen general "
                "(01) del IVA, u operaciones exentas (E1–E6). La inversión del sujeto pasivo (S2), las operaciones no "
                "sujetas (N1/N2) y otros regímenes o impuestos no están soportados.")
        esperado = q2(b * t / Decimal(100))
        if esperado != q2(c):
            nivel = "confirmar" if abs(esperado - c) <= Decimal("0.01") else "bloqueo"
            add(nivel, f"desglose.{i}.cuota", "CUOTA_DESCUADRE",
                f"Cuota {q2(c)} ≠ base {q2(b)} × {t} % = {esperado}.")
        if not es_rectificativa and (b < 0 or c < 0):
            add("bloqueo", f"desglose.{i}", "NEGATIVO", "Importes negativos: use el procedimiento de rectificación.")
        suma_base += b
        suma_cuota += c
    if base is None:
        add("bloqueo", "base_imponible", "BASE_FALTA", "Falta la base imponible.")
    elif desglose and q2(suma_base) != q2(base):
        add("bloqueo", "base_imponible", "BASE_DESCUADRE", f"La suma de bases por tipo ({q2(suma_base)}) ≠ base ({q2(base)}).")
    if cuota is None:
        add("bloqueo", "cuota_total", "CUOTA_FALTA", "Falta la cuota de IVA." + (
            " Todas las bases son exentas: indique 0.00." if desglose and all(es_exenta(x) for x in desglose) else ""))
    elif desglose and q2(suma_cuota) != q2(cuota):
        add("bloqueo", "cuota_total", "CUOTA_TOTAL_DESCUADRE", f"La suma de cuotas ({q2(suma_cuota)}) ≠ cuota total ({q2(cuota)}).")
    tf_ext = (extraccion or {}).get("total_factura")
    total_no_leido = isinstance(tf_ext, dict) and "valor" in tf_ext and tf_ext["valor"] is None
    sin_clasificar = [str(x) for x in tf_ext.get("sin_clasificar") or []] if total_no_leido else []
    if total is None:
        add("bloqueo", "importe_total", "TOTAL_FALTA", "Falta el total de la factura." + (
            f" No se ha identificado en el documento: el cuadro de totales imprime cifras que no se han podido interpretar "
            f"({'; '.join(sin_clasificar)}) y no coinciden con base + cuota. Indique el total impreso; si incluye otros "
            "conceptos (tasas, suplidos…), la factura no puede expedirse con esta aplicación." if sin_clasificar else ""))
    elif base is not None and cuota is not None and q2(base + cuota) != q2(total):
        add("bloqueo", "importe_total", "TOTAL_DESCUADRE",
            f"Total {q2(total)} ≠ base {q2(base)} + cuota {q2(cuota)} = {q2(base + cuota)}.")
    if total is not None and abs(total) >= Decimal("100000000"):
        add("bloqueo", "importe_total", "MACRODATO", "Importes ≥ 100.000.000 € (macrodato) no soportados.")
    # Total no leído en el documento: datos_desde_extraccion lo deduce como base + cuota, lo que siempre cuadra
    if total_no_leido and total is not None and "importe_total" not in cambiados:
        # con retención, el total impreso puede ser el de la factura o el importe a pagar (ya descontada la retención)
        ret_i = _dec((datos.get("retencion") or {}).get("importe"))
        ret_i = ret_i if ret_i is not None and ret_i > 0 else None
        add("confirmar", "importe_total", "TOTAL_NO_LEIDO",
            f"No se ha leído el total de la factura en el documento: se ha calculado como base + cuota ({q2(total)})"
            + (f", y el importe a pagar como ese total menos la retención ({q2(total - ret_i)})" if ret_i else "") + ". "
            + (f"El cuadro de totales imprime además cifras sin interpretar ({'; '.join(sin_clasificar)}). " if sin_clasificar else "")
            + "Compruebe en el original que el total impreso es ese"
            + (" (o, si ya descuenta la retención, el importe a pagar)" if ret_i else "")
            + " y que no incluye otros conceptos (tasas, suplidos, portes…); si es distinto, corríjalo.")
    # Total leído con cifras sin interpretar debajo, alguna mayor (p. ej. «TOTAL 1.210 / Tasa 30 / IMPORTE 1.240»): el total
    # leído puede ser un subtotal; nada más lo detecta si el importe final lleva un rótulo no reconocido
    bajo_total = [str(x) for x in tf_ext.get("bajo_total") or []] if isinstance(tf_ext, dict) and not total_no_leido else []
    if bajo_total and total is not None and "importe_total" not in cambiados:
        add("confirmar", "importe_total", "TOTAL_CIFRAS_DEBAJO",
            f"Bajo el total leído ({q2(total)}) el cuadro de totales imprime cifras que no se han podido interpretar "
            f"({'; '.join(bajo_total)}), alguna mayor que el total. Compruebe en el original que el total de la factura y el "
            "importe a pagar son esos: si la factura suma otros conceptos (tasas, portes, suplidos…), no puede expedirse con "
            "esta aplicación.")
    # Total leído que, sumado a otras cifras sin interpretar, da otra cifra impresa en el documento (cuadro horizontal,
    # importe final en otra columna o en la cabecera): el total leído puede ser un subtotal
    suma_otra = tf_ext.get("suma_otra") if isinstance(tf_ext, dict) and not total_no_leido and not bajo_total else None
    if isinstance(suma_otra, dict) and total is not None and "importe_total" not in cambiados:
        add("confirmar", "importe_total", "TOTAL_OTRA_CIFRA",
            f"El documento imprime {suma_otra.get('cifra')}, que es el total leído ({q2(total)}) más "
            f"{' + '.join(str(x) for x in suma_otra.get('sumandos') or [])}, cifras que no se han podido interpretar. "
            "Compruebe en el original que el total de la factura y el importe a pagar son esos: si la factura suma otros "
            "conceptos (tasas, portes, suplidos…), no puede expedirse con esta aplicación.")
    # Líneas
    renglones = renglones_estimados(datos)
    if renglones > MAX_RENGLONES_PDF:
        add("bloqueo", "lineas", "DOCUMENTO_DEMASIADO_LARGO",
            f"La factura ocuparía unos {renglones} renglones (máximo {MAX_RENGLONES_PDF}, unas 60 páginas): acorte los "
            "conceptos o las observaciones o divida la operación en varias facturas.")
    lineas = datos.get("lineas") or []
    suma_lineas = Decimal("0")
    con_importe = 0
    for i, l in enumerate(lineas):
        imp = _dec(l.get("importe"))
        if imp is None:
            continue
        con_importe += 1
        suma_lineas += imp
        cant, precio = _dec(l.get("cantidad")), _dec(l.get("precio"))
        if cant is not None and precio is not None and q2(cant * precio) != q2(imp):
            add("bloqueo", f"lineas.{i}.importe", "LINEA_DESCUADRE",
                f"Línea {i + 1}: {cant} × {precio} = {q2(cant * precio)} ≠ importe {q2(imp)}.")
        elif cant is None and precio is not None and q2(precio) != q2(imp):
            # sin cantidad, el precio unitario debe coincidir con el importe (cantidad 1); si no, falta la cantidad
            # o el «precio» es otra cifra (p. ej. la cantidad leída en una columna deducida). Un descuento de la línea
            # que explica la diferencia no es un error (la incidencia DESCUENTO pide confirmar que la base lo descuenta)
            dtos = _descuentos_linea(extraccion, i, l)
            if any(q2(precio * (100 - d) / 100) == q2(imp) for d in dtos):
                continue
            if Decimal(0) < imp < precio and ("descuento" in (datos.get("indicios") or [])
                                              or re.search(rf"\b{_RE_PAL_DTO}\b", str(l.get("concepto") or ""), re.I)):
                add("confirmar", f"lineas.{i}.cantidad", "LINEA_SIN_CANTIDAD",
                    f"Línea {i + 1}: el importe {q2(imp)} es menor que el precio {q2(precio)} y no consta la cantidad; el "
                    "documento menciona un descuento. Compruebe en el original la cantidad, el precio y el descuento de la línea.")
            else:
                add("bloqueo", f"lineas.{i}.cantidad", "LINEA_SIN_CANTIDAD",
                    f"Línea {i + 1}: el precio {q2(precio)} no coincide con el importe {q2(imp)} y no consta la cantidad. "
                    "Indique la cantidad o corrija el precio.")
    if con_importe and base is not None and q2(suma_lineas) != q2(base):
        add("bloqueo", "lineas", "LINEAS_DESCUADRE",
            f"La suma de las líneas ({q2(suma_lineas)}) ≠ base imponible ({q2(base)}). Revise descuentos o suplidos.")
    if not lineas:
        add("aviso", "lineas", "SIN_LINEAS", "No se han identificado líneas de detalle.")
    elif extraccion and extraccion.get("lineas") and extraccion.get("aviso_lineas"):
        # columnas deducidas sin cabecera: cantidad, precio e importe pueden estar intercambiados
        add("confirmar", "lineas", "TABLA_DEDUCIDA",
            f"{extraccion['aviso_lineas']}. Compruebe en el original el concepto, la cantidad, el precio y el importe de "
            "cada línea antes de confirmar.")
    # Retención y total a pagar
    ret = datos.get("retencion")
    pagar = _dec(datos.get("total_a_pagar"))
    if ret and (ret.get("importe") not in (None, "")):
        ri, rp = _dec(ret.get("importe")), _dec(ret.get("porcentaje"))
        if ri is None or ri < 0:
            add("bloqueo", "retencion.importe", "RET_IMPORTE", "Importe de retención no válido.")
        else:
            if rp is not None and base is not None and q2(base * rp / 100) != q2(ri):
                # un céntimo de diferencia es un redondeo distinto (o truncado) del emisor, como en la cuota: se confirma
                esperado_r = q2(base * rp / 100)
                add("confirmar" if abs(esperado_r - q2(ri)) <= Decimal("0.01") else "bloqueo", "retencion.importe",
                    "RET_DESCUADRE", f"Retención {q2(ri)} ≠ base × {rp} % = {esperado_r}."
                    + (" La diferencia de un céntimo suele ser un redondeo distinto del emisor: compruébelo en el original."
                       if abs(esperado_r - q2(ri)) <= Decimal("0.01") else ""))
            if total is not None and pagar is not None and q2(total - ri) != q2(pagar):
                add("bloqueo", "total_a_pagar", "PAGAR_DESCUADRE",
                    f"Total a pagar {q2(pagar)} ≠ total {q2(total)} − retención {q2(ri)} = {q2(total - ri)}.")
            if rp is None:
                add("bloqueo", "retencion.porcentaje", "RET_PORCENTAJE_FALTA",
                    "No consta el porcentaje de la retención: indíquelo (el PDF lo muestra y se comprueba con la base).")
            # Retención de garantía (obra) o fianza: retiene parte del pago, no es IRPF; el PDF la mostraría como IRPF
            ext_ret = (extraccion or {}).get("retencion") or {}
            rotulo = " ".join(dict.fromkeys(str(ext_ret[k].get("texto") or "") for k in ("importe", "porcentaje")
                                            if isinstance(ext_ret.get(k), dict)))
            irpf = bool(re.search(r"i\.?\s?r\.?\s?p\.?\s?f", rotulo, re.I))
            habitual = rp is not None and rp in TIPOS_IRPF_HABITUALES
            en_rotulo = bool(re.search(r"(?:garant[ií]a|fianza).{0,25}retenci|retenci[oó]n.{0,25}(?:garant[ií]a|fianza)",
                                       rotulo, re.I))  # en el propio rótulo, no en otro texto de la misma fila
            nota = ((extraccion or {}).get("indicios") or {}).get("retencion_garantia")
            nota = str(nota.get("texto") or "") if isinstance(nota, dict) else ""
            en_nota = "retencion_garantia" in datos["indicios"]
            if (en_rotulo or (en_nota and not habitual)) and not irpf:
                add("bloqueo", "retencion", "RETENCION_GARANTIA",
                    f"La retención leída parece de garantía o fianza («{(rotulo.strip() if en_rotulo else nota)[:120]}»), no "
                    "de IRPF. La retención de garantía no está soportada: el PDF definitivo la mostraría como «Retención IRPF».")
            elif en_rotulo or en_nota:
                add("confirmar", "retencion", "RETENCION_GARANTIA",
                    f"El documento menciona una retención de garantía («{(rotulo.strip() if en_rotulo else nota)[:120]}»), "
                    "que no está soportada. Confirme que la retención indicada es de IRPF (el PDF la mostrará como «Retención "
                    "IRPF») y que no se descuenta ninguna garantía del importe a pagar.")
            elif rp is not None and not habitual:
                add("confirmar", "retencion.porcentaje", "RET_TIPO_INUSUAL",
                    f"La retención del {rp.normalize():f} % no corresponde a un tipo habitual de IRPF (1, 2, 7, 15 o 19 %). "
                    "Confirme que es una retención de IRPF y no de garantía u otro concepto: el PDF la mostrará como "
                    "«Retención IRPF».")
            # «TOTAL» impreso igual a base + cuota − retención: se ha reinterpretado como importe a pagar y el total de la
            # factura se ha calculado; la confirmación lo dice expresamente
            liq = tf_ext.get("liquido_impreso") if isinstance(tf_ext, dict) else None
            reinterpretado = (f" El documento imprime como total {liq}, que ya descuenta la retención: se ha tomado como "
                              f"importe a pagar y el total de la factura se ha calculado como base + cuota ({q2(total)}). "
                              "Compruébelo en el original."
                              if liq and total is not None and not {"importe_total", "total_a_pagar"} & cambiados else "")
            add("confirmar", "retencion", "RETENCION",
                "La factura incluye retención: el registro VERI*FACTU recoge el importe total (bases + cuotas) y el "
                "PDF muestra el importe a pagar neto de retención." + reinterpretado + " Confirme este tratamiento.")
    elif pagar is not None and total is not None and q2(pagar) != q2(total):
        add("bloqueo", "total_a_pagar", "PAGAR_DISTINTO",
            "El importe a pagar difiere del total sin retención identificada (¿suplidos, anticipos?). Caso no soportado.")

    # --- Casos no soportados
    for ind in datos.get("indicios") or []:
        if ind in CASOS_BLOQUEADOS and not (es_rectificativa and ind == "rectificativa"):
            add("bloqueo", "indicios", f"NO_SOPORTADO_{ind.upper()}", f"Caso no soportado: {CASOS_BLOQUEADOS[ind]}.")
        elif ind == "exencion" and not any(clase_fila(x) in ("exenta", "sin_calificar") for x in desglose):
            # el documento menciona una exención pero ninguna base del desglose es exenta
            nota = ((extraccion or {}).get("indicios") or {}).get("exencion")
            nota = str(nota.get("texto") or "")[:120] if isinstance(nota, dict) else ""
            add("confirmar", "desglose", "EXENCION_MENCIONADA",
                "El documento menciona una exención" + (f" («{nota}»)" if nota else "") + ", pero ninguna base del desglose "
                "es exenta. Si alguna operación de la factura está exenta, márquela como exenta en el desglose y elija la "
                "causa; si todas están sujetas a IVA, confirme.")
        elif ind == "descuento":
            add("confirmar", "lineas", "DESCUENTO", "El documento menciona un descuento: confirme que la base imponible ya lo descuenta.")

    # --- Extracción dudosa
    if extraccion:
        # una sección que falló al leerse queda vacía: si es opcional (fecha de operación, referencias, pago) nada más lo
        # detecta, así que se pide revisarla (desaparece al corregir el campo afectado)
        for p in extraccion.get("extraccion_parcial") or []:
            ruta = p.get("campo") if isinstance(p, dict) and isinstance(p.get("campo"), str) else "documento"
            if ruta not in cambiados:
                add("confirmar", ruta, "EXTRACCION_PARCIAL",
                    f"No se pudo leer {p.get('seccion', 'una sección') if isinstance(p, dict) else 'una sección'} del "
                    f"documento: el borrador puede carecer de esos datos. Revíselos con el PDF y confirme.")
        dudosos_vistos = set()
        for d in campos_dudosos(extraccion):
            ruta = _ruta_borrador(d["campo"])
            if ruta and ruta not in cambiados and (ruta, str(d["valor"])) not in dudosos_vistos:
                dudosos_vistos.add((ruta, str(d["valor"])))  # tipo_iva y desglose.0.tipo_iva: la misma lectura, una vez
                add("confirmar", ruta, "DUDOSO",
                    f"Lectura dudosa{'' if CAMPOS_EDITABLES.match(ruta) else ' de «' + d['campo'] + '»'} ({d['metodo']}, "
                    f"confianza {d['confianza']:.0%}): «{d['valor']}». Corrija o confirme.")
        # Texto del PDF que no se ve (invisible, transparente, recortado, fuera de la zona visible): se ha ignorado, pero
        # puede indicar un PDF manipulado (otros importes en una capa oculta). En una página leída por OCR es la capa de
        # texto de un escaneo, lo normal
        ocultos = [(p.get("numero"), p.get("texto_oculto")) for p in extraccion.get("paginas") or []
                   if isinstance(p, dict) and isinstance(p.get("texto_oculto"), int) and p["texto_oculto"] > 0
                   and p.get("metodo") != "ocr"]
        if ocultos:
            add("aviso", "documento", "TEXTO_OCULTO",
                "El PDF contiene texto que no se ve al mostrar la página (" + "; ".join(
                    f"{n} caracteres en la página {pg}" for pg, n in ocultos) + "): no se ha usado para leer los datos. Puede "
                "ser normal (marcas internas del programa que generó el PDF), pero también indicio de un documento "
                "manipulado: compare el borrador con lo que muestra el original.")
        conocidos = list(extraccion.get("nifs_detectados") or []) + [empresa.get("nif")]
        for n in extraccion.get("nifs_no_validos") or []:
            msg = (f"Se ha leído «{n['texto']}» con forma de NIF pero su dígito de control no es válido (posible error de OCR) "
                   f"en la línea «{n['linea']}».")
            if any(nifmod.parecidos(n["texto"], v) for v in conocidos) or "destinatario.nif" in cambiados:
                add("aviso", "documento", "NIF_ILEGIBLE", msg)
            else:  # puede ser el NIF del destinatario (y el elegido, un tercero): se exige revisarlo
                add("confirmar", "destinatario.nif", "NIF_ILEGIBLE", msg + " Puede ser el del destinatario: compruébelo.")
    # Confirmaciones ya dadas
    conf = set(datos.get("confirmaciones") or [])
    for i in inc:
        if i["nivel"] == "confirmar":
            i["clave"] = f"{i['codigo']}:{i['campo']}" + (f":{i['ref']}" if i.get("ref") else "")
            i["confirmado"] = i["clave"] in conf
    return inc


def _validar_fila_exenta(add, i: int, d: dict, clase: str, b: Decimal | None, es_rectificativa: bool,
                         ext_exencion: dict | None, origen: dict | None = None, ajeno: str = "") -> None:
    """Fila sin IVA del desglose: exenta (con causa) o sin calificar (sin causa, sin tipo y sin cuota). `origen`: de dónde
    sale la base exenta que la extracción propuso en una factura mixta (origen_base_exenta). `ajeno`: renglón del documento
    con una tasa, fianza, indemnización… (indicio «importes_ajenos»), que la base exenta puede incluir sin deber."""
    campo = f"desglose.{i}"
    if clase == "sin_calificar":
        # exención del IVA mencionada sin el artículo que da la causa («Operación exenta de IVA»): se cita, sin proponer causa
        nota = (str(ext_exencion.get("texto") or "")[:120] if isinstance(ext_exencion, dict) and ext_exencion.get("iva")
                and not ext_exencion.get("causa") else "")
        add("bloqueo", campo, "SIN_IVA",
            f"La fila {i + 1} del desglose no repercute IVA (base {q2(b) if b is not None else 'sin indicar'}, sin tipo ni "
            "cuota). "
            + (f"El documento menciona una exención («{nota}») pero no cita el artículo de la Ley 37/1992 que da su causa: "
               "no se propone ninguna. " if nota else "")
            + "Si la operación está exenta, elija la causa de exención (E1–E6) y revise la mención legal que se "
            "imprimirá en la factura; si está sujeta a IVA, indique el tipo y la cuota y corrija la cuota y el total. Las "
            "operaciones no sujetas al impuesto no están soportadas.")
        return
    causa = d.get("operacion_exenta")
    info = CAUSAS_EXENCION.get(causa)
    if d.get("calificacion"):
        add("bloqueo", campo, "EXENTA_Y_SUJETA",
            f"La fila {i + 1} indica a la vez una calificación ({d['calificacion']}) y una causa de exención ({causa}): son "
            "excluyentes (validaciones AEAT 1196 y 1238). Elija «Sujeta» o «Exenta».")
    if info is None:
        add("bloqueo", f"{campo}.operacion_exenta", "EXENTA_CAUSA_NO_VALIDA",
            f"«{causa}» no es una causa de exención admitida (E1, E2, E3, E4 o E6).")
    elif causa in CAUSAS_NO_SOPORTADAS:
        add("bloqueo", f"{campo}.operacion_exenta", "EXENTA_NO_SOPORTADA",
            f"{causa} ({info['nombre'].lower()}): la AEAT exige identificar al destinatario con su NIF-IVA de otro Estado "
            "miembro (IDOtro, validación 1289) y la aplicación solo admite destinatarios con NIF español. Las entregas "
            "intracomunitarias no están soportadas.")
    t, c = _dec(d.get("tipo_iva")), _dec(d.get("cuota"))
    if (d.get("tipo_iva") not in (None, "") and t != 0) or (d.get("cuota") not in (None, "") and c != 0):
        add("bloqueo", campo, "EXENTA_CON_CUOTA",
            f"La fila {i + 1} es exenta: no lleva tipo de IVA ni cuota (validación AEAT 1238). Deje vacíos el tipo y la cuota "
            "o, si la operación está sujeta, márquela como sujeta.")
    if (d.get("impuesto") or "01") != "01" or (info and (d.get("clave_regimen") or "01") not in ("01", info["clave_regimen"])):
        add("bloqueo", campo, "CALIFICACION_NO_SOPORTADA",
            "Solo se admiten operaciones exentas del IVA (impuesto 01) en régimen general (01) o, para E2 y E3, de exportación (02).")
    if b is None:
        add("bloqueo", f"{campo}.base", "DESGLOSE_INCOMPLETO", f"Falta la base exenta de la fila {i + 1}.")
    elif not es_rectificativa and b < 0:
        add("bloqueo", campo, "NEGATIVO", "Importes negativos: use el procedimiento de rectificación.")
    elif b == 0 and not es_rectificativa:
        add("bloqueo", f"{campo}.base", "EXENTA_BASE_CERO", f"La base exenta de la fila {i + 1} es cero: indíquela o quite la fila.")
    motivo = normalizar_mencion(d.get("motivo_exencion"))
    if not motivo or mencion_vacia(motivo):
        add("bloqueo", f"{campo}.motivo_exencion", "EXENTA_MENCION_FALTA",
            (f"La mención de la exención («{motivo[:20]}») solo tiene signos de puntuación o espacios. " if motivo else "")
            + "Indique la mención de la exención que se imprimirá en la factura: la referencia al precepto de la Ley 37/1992 "
            "(o de la Directiva 2006/112/CE) o la indicación de que la operación está exenta (art. 6.1.j RD 1619/2012)"
            + (f", p. ej. «{info['mencion']}»." if info else "."))
    elif len(motivo) > MAX_MENCION_EXENCION:
        add("bloqueo", f"{campo}.motivo_exencion", "EXENTA_MENCION_LARGA",
            f"La mención de la exención supera {MAX_MENCION_EXENCION} caracteres.")
    elif not texto_imprimible(motivo):
        add("bloqueo", f"{campo}.motivo_exencion", "EXENTA_MENCION_CARACTERES",
            "La mención de la exención contiene caracteres que no se pueden imprimir en la factura: escríbala solo con "
            "letras, cifras y signos del alfabeto latino.")
    if info is None or causa in CAUSAS_NO_SOPORTADAS:
        return
    leida = (isinstance(ext_exencion, dict) and ext_exencion.get("causa") == causa and ext_exencion.get("articulo")
             and ext_exencion.get("texto"))
    add("confirmar", campo, "EXENTA_CAUSA",
        f"Base exenta de la fila {i + 1} ({q2(b) if b is not None else '—'}): {causa} — {info['nombre']}"
        + (f", con clave de régimen {info['clave_regimen']} (exportación)" if info["clave_regimen"] != "01" else "")
        + f". Mención en la factura: «{motivo[:200]}». "
        + (f"Propuesta a partir del texto del documento («{str(ext_exencion['texto'])[:120]}»). " if leida else "")
        + (_texto_origen(origen) if leida and origen and b is not None and _dec(origen.get("base")) == q2(b) else "")
        + (f"El documento incluye importes que no suelen formar parte de una base exenta (tasas, fianzas, indemnizaciones, "
           f"provisiones de fondos o gastos repercutidos o por cuenta del cliente), como «{ajeno}»: compruebe que esta base "
           "no los incluye y, si los incluye, corrija la base y el total. " if ajeno else "")
        + "La aplicación no puede comprobar que la operación esté exenta: confirme la causa con la normativa (o con su "
          "asesor) antes de expedir; si no es correcta, cámbiela o marque la operación como sujeta.")


def _texto_origen(origen: dict) -> str:
    """Frase de la confirmación EXENTA_CAUSA que explica de dónde sale una base exenta calculada como diferencia."""
    lineas = [x for x in origen.get("lineas") or [] if isinstance(x, (list, tuple)) and len(x) == 2]
    citadas = [f"«{c}» ({imp})" for c, imp in lineas[:3]] + ([f"{len(lineas) - 3} más"] if len(lineas) > 3 else [])
    out = f"Esta base exenta ({origen.get('base')}) se ha calculado como diferencia ({origen.get('calculo')})"
    if origen.get("impresa"):
        out += " y coincide con la base exenta impresa en el documento"
    if citadas:
        out += ("; corresponde a la línea " if len(lineas) == 1 else "; corresponde a las líneas ") \
            + (", ".join(citadas[:-1]) + " y " + citadas[-1] if len(citadas) > 1 else citadas[0]) + "."
    else:
        out += "; no se han identificado las líneas de conceptos que la forman: compruébelas en el original."
    return out + " Compruebe que esas operaciones son las exentas por la causa citada. "


def _ref_documentos(ids: list[str]) -> str:
    """Referencia corta y estable de un conjunto de documentos (forma parte de la clave de una confirmación)."""
    ref = "doc" + "-".join(sorted(ids, key=lambda x: (len(x), x)))
    if len(ref) <= 60:
        return ref
    import hashlib
    return "docs" + hashlib.sha256(ref.encode()).hexdigest()[:20]


def _descuentos_linea(extraccion: dict | None, i: int, linea: dict) -> list[Decimal]:
    """Porcentajes de descuento de la línea i: la columna de descuento leída en esa línea de la extracción (si es la misma
    línea: igual precio e importe) o un «dto./descuento N %» escrito en su concepto."""
    out = []
    ext_l = ((extraccion or {}).get("lineas") or [])[i:i + 1]
    ext_l = ext_l[0] if ext_l and isinstance(ext_l[0], dict) else {}
    if all(_dec(_v(ext_l.get(k))) == _dec(linea.get(k)) for k in ("precio", "importe")):
        d = _dec(_v(ext_l.get("dto")))
        if d is not None:
            out.append(d)
    # en el concepto: «dto./dcto./descuento/bonif. 10 %», «10 % dto.» o «-10 %» (la cifra solo vale si explica el importe)
    for m in re.finditer(rf"\b{_RE_PAL_DTO}\b\.?\s*(?:del?\s+)?(\d{{1,2}}(?:[.,]\d{{1,2}})?)\s*%"
                         rf"|(?<![\w.,])(\d{{1,2}}(?:[.,]\d{{1,2}})?)\s*%\s*(?:de\s+)?{_RE_PAL_DTO}\b"
                         r"|(?<![\w.,])[-−–]\s?(\d{1,2}(?:[.,]\d{1,2})?)\s*%", str(linea.get("concepto") or ""), re.I):
        out.append(Decimal(next(g for g in m.groups() if g).replace(",", ".")))
    return [d for d in out if Decimal(0) < d < Decimal(100)]


# palabras con que se rotula un descuento de línea
_RE_PAL_DTO = r"(?:dto|dcto|descto|desc|descuento|bonif|bonificaci[oó]n|rappel|rebaja)"


def _ruta_borrador(ruta_ext: str) -> str | None:
    mapa = {"numero": "num_serie", "fecha": "fecha_documento", "base_imponible": "base_imponible",
            "cuota_iva": "cuota_total", "total_factura": "importe_total", "total_a_pagar": "total_a_pagar",
            # tipo_iva es el del primer tipo del desglose (su lectura dudosa se confirma allí); tipo_documento no es un dato
            # del borrador: solo propone la clasificación del documento, que el usuario confirma siempre (clasificar)
            "tipo_iva": "desglose.0.tipo_iva", "tipo_documento": None, "forma_pago": "forma_pago", "iban": "iban",
            "fecha_operacion": "fecha_operacion", "vencimiento": "vencimiento"}
    if ruta_ext in mapa:
        return mapa[ruta_ext]
    if CAMPOS_EDITABLES.match(ruta_ext):
        return ruta_ext  # mismo nombre en la extracción y en el borrador
    m = re.match(r"^(emisor|destinatario)\.(nombre|nif|domicilio)$", ruta_ext)
    if m:
        return ruta_ext
    m = re.match(r"^lineas\.(\d+)\.(concepto|cantidad|unidad|precio|importe)$", ruta_ext)
    if m:
        return ruta_ext
    m = re.match(r"^desglose\.(\d+)\.(tipo_iva|base|cuota)$", ruta_ext)
    if m:
        return ruta_ext
    m = re.match(r"^referencias\.(\w+)$", ruta_ext)
    if m:
        return ruta_ext
    m = re.match(r"^retencion\.(porcentaje|importe)$", ruta_ext)
    if m:
        return ruta_ext
    m = re.match(r"^lineas\.(\d+)\.\w+$", ruta_ext)
    if m:
        return f"lineas.{m.group(1)}"  # otras columnas de la línea (descuento, IVA…): se confirma la línea
    return "documento"  # cualquier otra lectura dudosa se confirma en el documento, nunca se descarta


def puede_emitir(incidencias: list[dict]) -> bool:
    return not any(i["nivel"] == "bloqueo" for i in incidencias) and all(
        i.get("confirmado") for i in incidencias if i["nivel"] == "confirmar")


# --- Ámbito del emisor ---------------------------------------------------------------
def evaluar_ambito(e: dict) -> dict:
    """Determina si la aplicación puede emitir para este emisor (solo supuestos implementados)."""
    motivos, puede = [], True
    if e.get("territorio") in ("pais_vasco", "navarra"):
        puede = False
        motivos.append("Territorio foral: VERI*FACTU (RD 1007/2023) no aplica a los obligados de los territorios "
                       "forales del País Vasco y Navarra, que tienen sus propios sistemas (TicketBAI / Navarra).")
    elif e.get("territorio") != "comun":
        puede = False
        motivos.append("Indique el territorio fiscal.")
    if e.get("sii") is None:
        puede = False
        motivos.append("Indique si la empresa lleva los libros registro del IVA mediante el SII.")
    elif int(e.get("sii")):
        puede = False
        motivos.append("Las empresas que llevan los libros por el SII están excluidas del RD 1007/2023 (art. 3).")
    if e.get("tipo_contribuyente") not in ("sociedad_IS", "persona_fisica_IRPF", "entidad_renta", "otro"):
        puede = False
        motivos.append("Indique el tipo de contribuyente.")
    if int(e.get("canarias_ceuta_melilla") or 0):
        puede = False
        motivos.append("Operaciones en Canarias, Ceuta o Melilla (IGIC/IPSI) no implementadas.")
    if e.get("otras_exclusiones"):
        puede = False
        motivos.append("Se han indicado otras exclusiones o particularidades: " + str(e["otras_exclusiones"]))
    if e.get("origen_pdf") == "programa_facturacion":
        motivos.append("AVISO: los PDF proceden de un programa de facturación. Ese programa puede ser por sí mismo un "
                       "sistema informático de facturación sujeto al RD 1007/2023; esta aplicación no lo adapta. Las "
                       "facturas deben expedirse solo desde esta aplicación y el programa de origen debe limitarse a "
                       "documentos preparatorios.")
    if puede:
        motivos.append("Supuesto implementado: territorio común, sin SII, sin exclusiones indicadas. Operaciones de IVA en "
                       "régimen general sujetas a los tipos del 21, 10 o 4 % y operaciones exentas (E1–E4 y E6), con la causa "
                       "confirmada en cada factura; las no sujetas, la inversión del sujeto pasivo y el recargo de "
                       "equivalencia siguen sin soportarse.")
    return {"puede_emitir": puede, "motivos": motivos}
