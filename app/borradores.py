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


def clasificar(ext: dict, inspeccion: dict, empresa: dict) -> dict:
    """Propone la situación del documento. El usuario debe confirmarla antes de procesarlo fiscalmente."""
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
    if dest and dest == nif_emp and emisor != nif_emp:
        return {"sugerida": "recibida", "confianza": 0.9,
                "motivos": motivos + ["El NIF de la empresa figura como destinatario: es una factura de proveedor."]}
    if emisor and emisor == nif_emp:
        if tipo in ("proforma", "presupuesto", "borrador"):
            return {"sugerida": "preparatorio", "confianza": 0.9,
                    "motivos": motivos + [f"Documento titulado como {tipo}."]}
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


def aplicar_cambio(datos: dict, ruta: str, valor) -> tuple[object, dict]:
    if not CAMPOS_EDITABLES.match(ruta):
        raise ValueError(f"Campo no editable: {ruta}")
    nuevo = copy.deepcopy(datos)
    partes = ruta.split(".")
    obj = nuevo
    for p in partes[:-1]:
        if p.isdigit():
            idx = int(p)
            while len(obj) <= idx:
                obj.append({})
            obj = obj[idx]
        else:
            obj = obj.setdefault(p, {})
    ult = partes[-1]
    anterior = obj.get(ult) if isinstance(obj, dict) else None
    if ult in ("lineas", "desglose") and not isinstance(valor, list):
        raise ValueError("Se esperaba una lista")
    if isinstance(valor, str):
        valor = valor.strip()
        if ruta.endswith(".nif"):
            valor = nifmod.normalizar(valor)
    obj[ult] = valor if valor != "" else None
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
        return Decimal(str(x))
    except InvalidOperation:
        return None


def validar(con, empresa: dict, documento: dict | None, datos: dict, *, extraccion: dict | None = None,
            serie: dict | None = None, es_rectificativa: bool = False, cambiados: set[str] | None = None) -> list[dict]:
    """Devuelve incidencias: nivel 'bloqueo' (impide emitir), 'confirmar' (requiere confirmación expresa) o 'aviso'."""
    inc: list[dict] = []
    cambiados = cambiados or set()

    def add(nivel, campo, codigo, mensaje):
        inc.append({"nivel": nivel, "campo": campo, "codigo": codigo, "mensaje": mensaje})

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
    if not de.get("domicilio"):
        add("bloqueo", "destinatario.domicilio", "DEST_DOMICILIO",
            "Falta el domicilio del destinatario (contenido obligatorio de la factura completa, art. 6 RD 1619/2012).")
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
        dup = db.fila(con, "SELECT id, entorno FROM facturas WHERE empresa_id=? AND emisor_nif=? AND num_serie=?",
                      (empresa["id"], nifmod.normalizar(empresa["nif"]), ns))
        if dup:
            add("bloqueo", "num_serie", "DUPLICADA",
                f"Ya existe la factura {ns} expedida (entorno {dup['entorno']}). No se puede volver a expedir.")

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
    if total is None:
        add("bloqueo", "importe_total", "TOTAL_FALTA", "Falta el total de la factura.")
    elif base is not None and cuota is not None and q2(base + cuota) != q2(total):
        add("bloqueo", "importe_total", "TOTAL_DESCUADRE",
            f"Total {q2(total)} ≠ base {q2(base)} + cuota {q2(cuota)} = {q2(base + cuota)}.")
    if total is not None and abs(total) >= Decimal("100000000"):
        add("bloqueo", "importe_total", "MACRODATO", "Importes ≥ 100.000.000 € (macrodato) no soportados.")
    # Líneas
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
    if con_importe and base is not None and q2(suma_lineas) != q2(base):
        add("bloqueo", "lineas", "LINEAS_DESCUADRE",
            f"La suma de las líneas ({q2(suma_lineas)}) ≠ base imponible ({q2(base)}). Revise descuentos o suplidos.")
    if not lineas:
        add("aviso", "lineas", "SIN_LINEAS", "No se han identificado líneas de detalle.")
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
            add("confirmar", "retencion", "RETENCION",
                "La factura incluye retención: el registro VERI*FACTU recoge el importe total (bases + cuotas) y el "
                "PDF muestra el importe a pagar neto de retención. Confirme este tratamiento.")
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
        for d in campos_dudosos(extraccion):
            ruta = _ruta_borrador(d["campo"])
            if ruta and ruta not in cambiados:
                add("confirmar", ruta, "DUDOSO",
                    f"Lectura dudosa ({d['metodo']}, confianza {d['confianza']:.0%}): «{d['valor']}». Corrija o confirme.")
        for n in extraccion.get("nifs_no_validos") or []:
            add("aviso", "documento", "NIF_ILEGIBLE",
                f"Se ha leído «{n['texto']}» con forma de NIF pero su dígito de control no es válido (posible error de OCR) "
                f"en la línea «{n['linea']}».")
    # Confirmaciones ya dadas
    conf = set(datos.get("confirmaciones") or [])
    for i in inc:
        if i["nivel"] == "confirmar":
            i["clave"] = f"{i['codigo']}:{i['campo']}"
            i["confirmado"] = i["clave"] in conf
    return inc


def _ruta_borrador(ruta_ext: str) -> str | None:
    mapa = {"numero": "num_serie", "fecha": "fecha_documento", "base_imponible": "base_imponible",
            "cuota_iva": "cuota_total", "total_factura": "importe_total", "total_a_pagar": "total_a_pagar",
            "tipo_iva": None, "tipo_documento": None, "forma_pago": "forma_pago", "iban": "iban"}
    if ruta_ext in mapa:
        return mapa[ruta_ext]
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
    return None


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
