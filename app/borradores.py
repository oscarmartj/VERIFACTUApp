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
    "Retención de IRPF: no forma parte del ImporteTotal del registro ni del QR (bases + cuotas); el PDF muestra "
    "por separado «Total factura» y «Total a pagar» (FAQ AEAT para desarrolladores, nº 20).",
    "Factura rectificativa por diferencias (R1 o R4, TipoRectificativa I) de una factura expedida en la aplicación.",
    "Anulación del registro de una factura expedida en la aplicación; subsanación de registros.",
]
CASOS_BLOQUEADOS = {
    "exencion": "Operaciones exentas (E1–E6)",
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
            return {"sugerida": "emitida_propia", "confianza": 0.8, "motivos": motivos + [
                f"El PDF está firmado electrónicamente ({f0.get('nombre') or 'firmante no indicado'}, {f0.get('fecha') or 'sin fecha'}): "
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


def datos_desde_extraccion(ext: dict, nombre_archivo: str = "") -> dict:
    lineas = []
    for l in ext.get("lineas", []):
        lineas.append({k: _v(l.get(k, {})) for k in ("concepto", "cantidad", "unidad", "precio", "importe")})
    desglose = []
    for d in ext.get("desglose", []):
        desglose.append({"tipo_iva": _v(d["tipo_iva"]), "base": _v(d["base"]), "cuota": _v(d["cuota"]),
                         "calificacion": "S1", "clave_regimen": "01", "impuesto": "01"})
    ret = ext.get("retencion")
    refs = {k: _v(v) for k, v in (ext.get("referencias") or {}).items()}
    primera = (lineas[0]["concepto"] if lineas and lineas[0].get("concepto") else "") or ""
    descripcion = re.sub(r"\s+", " ", primera).strip()[:500]
    total = _v(ext.get("total_factura", {}))
    pagar = _v(ext.get("total_a_pagar", {}))
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
        "base_imponible": _v(ext.get("base_imponible", {})),
        "cuota_total": _v(ext.get("cuota_iva", {})),
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


# Rutas de los campos editables del borrador
CAMPOS_EDITABLES = re.compile(
    r"^(num_serie|fecha_documento|fecha_operacion|descripcion_operacion|base_imponible|cuota_total|importe_total|"
    r"total_a_pagar|forma_pago|iban|vencimiento|observaciones|"
    r"(emisor|destinatario)\.(nombre|nif|domicilio)|referencias\.[a-z_]{1,30}|retencion\.(porcentaje|importe)|"
    r"lineas\.\d{1,3}\.(concepto|cantidad|unidad|precio|importe)|desglose\.\d{1,2}\.(tipo_iva|base|cuota)|"
    r"lineas|desglose|retencion)$")


CAMPOS_NUMERICOS = re.compile(r"^(base_imponible|cuota_total|importe_total|total_a_pagar|retencion\.(porcentaje|importe)|"
                              r"lineas\.\d+\.(cantidad|precio|importe)|desglose\.\d+\.(tipo_iva|base|cuota))$")
RE_NUMERO = re.compile(r"^[+-]?\d{1,12}(?:\.\d{1,6})?$")
CLAVES_LISTA = {"lineas": ("concepto", "cantidad", "unidad", "precio", "importe"),
                "desglose": ("tipo_iva", "base", "cuota", "calificacion", "clave_regimen", "impuesto")}
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
                desc = f"«{o['nombre_archivo']}» (importado el {str(o['subido_en'])[:10]}"
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
            f"El documento está fechado el {fd}; la factura se expedirá con fecha de hoy ({hoy}). "
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
    if not desglose:
        add("bloqueo", "desglose", "DESGLOSE_FALTA", "No se ha identificado el desglose de bases y cuotas por tipo de IVA.")
    suma_base, suma_cuota = Decimal("0"), Decimal("0")
    for i, d in enumerate(desglose):
        t, b, c = _dec(d.get("tipo_iva")), _dec(d.get("base")), _dec(d.get("cuota"))
        if t is None or b is None or c is None:
            add("bloqueo", f"desglose.{i}", "DESGLOSE_INCOMPLETO", "Desglose incompleto (tipo, base y cuota).")
            continue
        if t not in TIPOS_IVA_SOPORTADOS:
            add("bloqueo", f"desglose.{i}.tipo_iva", "TIPO_NO_SOPORTADO", f"Tipo de IVA {t} % no soportado.")
        if (d.get("calificacion") or "S1") != "S1" or (d.get("clave_regimen") or "01") != "01":
            add("bloqueo", f"desglose.{i}", "CALIFICACION_NO_SOPORTADA", "Solo se admite régimen general S1/01.")
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
        add("bloqueo", "cuota_total", "CUOTA_FALTA", "Falta la cuota de IVA.")
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
        add("confirmar", "importe_total", "TOTAL_NO_LEIDO",
            f"No se ha leído el total de la factura en el documento: se ha calculado como base + cuota ({q2(total)}). "
            + (f"El cuadro de totales imprime además cifras sin interpretar ({'; '.join(sin_clasificar)}). " if sin_clasificar else "")
            + "Compruebe en el original que el total impreso es ese y que no incluye otros conceptos (tasas, suplidos, "
            "portes…); si es distinto, corríjalo.")
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
                add("bloqueo", "retencion.importe", "RET_DESCUADRE",
                    f"Retención {q2(ri)} ≠ base × {rp} % = {q2(base * rp / 100)}.")
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
        motivos.append("Supuesto implementado: territorio común, sin SII, sin exclusiones indicadas.")
    return {"puede_emitir": puede, "motivos": motivos}
