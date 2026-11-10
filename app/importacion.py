"""Importación de PDF: validación, deduplicación, conservación del original, inspección y extracción."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from . import almacen, borradores, db
from . import series as seriesmod
from . import nif as nifmod
from .extraccion import campos, extraccion_proceso, inspeccion, texto


def _nombre_seguro(nombre: str) -> str:
    base = Path(nombre or "documento.pdf").name
    return "".join(ch for ch in base if ch.isprintable() and ch not in '\\/:*?"<>|')[:150] or "documento.pdf"


def importar(con, *, empresa: dict, usuario_id: int, nombre: str, datos: bytes) -> dict:
    nombre = _nombre_seguro(nombre)
    try:
        inspeccion.validar_basico(datos, nombre)
    except inspeccion.PDFNoValido as e:
        return {"nombre": nombre, "estado": "error", "error": str(e)}
    sha = almacen.sha256(datos)
    previo = db.fila(con, "SELECT id, nombre_archivo, subido_en FROM documentos WHERE empresa_id=? AND sha256=?", (empresa["id"], sha))
    if previo:
        return {"nombre": nombre, "documento_id": previo["id"], "duplicado": True, "estado": "duplicado",
                "duplicado_de": previo, "mensaje": f"Este archivo ya se importó el {previo['subido_en']} como «{previo['nombre_archivo']}»."}
    # Todo el procesamiento se hace ANTES de guardar nada: un PDF rechazado o un fallo no deja ficheros huérfanos
    try:
        insp = inspeccion.inspeccionar(datos)
        with tempfile.TemporaryDirectory() as td:
            # Imágenes de página para la interfaz (el navegador nunca abre el PDF original), con presupuesto de tamaño
            pngs = [p.read_bytes() for p in inspeccion.rasterizar(datos, Path(td) / "ui", dpi=110, max_px=1600,
                                                                    max_bytes=50 * 1024 * 1024)]
            alta = inspeccion.rasterizar(datos, Path(td) / "qr", dpi=200, primera=1, ultima=1, max_px=2400)
            insp["qr"] = inspeccion.detectar_qr(alta[0]) if alta else []
        paginas = texto.leer(datos)
        # Extracción de campos (y líneas de cabecera para el recorte del logotipo) en un proceso hijo limitado
        ext = extraccion_proceso.extraer(paginas, nombre, empresa["nif"])
    except inspeccion.PDFNoValido as e:
        return {"nombre": nombre, "estado": "error", "error": str(e)}
    sug = borradores.clasificar(ext, insp, empresa, duplicados_de(con, empresa["id"], ext))
    nativo = all(p.metodo == "texto_pdf" for p in paginas)
    from . import diario
    ruta = f"empresa_{empresa['id']}/originales/{sha}.pdf"
    guardados = []
    try:
        almacen.guardar(ruta, datos)
        guardados.append(almacen.ruta_absoluta(ruta))
        diario.guardar_binario(ruta, datos)  # el original no es regenerable: copia espejo en el volumen del diario
        guardados.append(diario.ruta_espejo(ruta))
        for n, png in enumerate(pngs, start=1):
            rp = f"empresa_{empresa['id']}/paginas/{sha}/{n}.png"
            almacen.guardar(rp, png, sobrescribir=True)
            guardados.append(almacen.ruta_absoluta(rp))
        with db.transaccion(con):
            cur = con.execute("""INSERT INTO documentos (empresa_id, sha256, nombre_archivo, tamano, paginas, ruta, subido_por, subido_en,
                inspeccion, texto_nativo, ocr_usado, clasificacion, clasificacion_motivo, estado)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'procesado')""",
                              (empresa["id"], sha, nombre, len(datos), insp["paginas"], ruta, usuario_id, db.ahora_iso(),
                               json.dumps(insp, ensure_ascii=False), int(nativo), int(not nativo),
                               sug["sugerida"], json.dumps(sug, ensure_ascii=False)))
            did = cur.lastrowid
            con.execute("INSERT INTO extracciones (documento_id, version_extractor, datos, creada_en) VALUES (?,?,?,?)",
                        (did, campos.VERSION_EXTRACTOR, json.dumps(ext, ensure_ascii=False), db.ahora_iso()))
            db.actividad(con, "documento.importado", usuario_id=usuario_id, empresa_id=empresa["id"], objeto="documento", objeto_id=did,
                         detalle={"sha256": sha, "paginas": insp["paginas"], "ocr": not nativo, "clasificacion_sugerida": sug["sugerida"]})
    except BaseException:
        # Si el documento no llegó a registrarse (y ningún otro lo usa), se retiran los ficheros recién guardados
        if not db.fila(con, "SELECT 1 FROM documentos WHERE sha256=?", (sha,)):
            for g in guardados:
                try:
                    g.unlink()
                except OSError:
                    pass
        raise
    return {"nombre": nombre, "documento_id": did, "duplicado": False, "estado": "procesado",
            "duplicados_fiscales": duplicados_fiscales(con, empresa["id"], did)}


def extraccion(con, documento_id: int) -> dict | None:
    e = db.fila(con, "SELECT * FROM extracciones WHERE documento_id=? ORDER BY id DESC LIMIT 1", (documento_id,))
    if not e:
        return None
    d = json.loads(e["datos"])
    d["_id"] = e["id"]
    return d


def clave_numero(numero) -> str:
    """Número de factura comparable: sin espacios, en mayúsculas y sin ceros a la izquierda en cada grupo de cifras.
    «0166/26», «166/26» y «166 / 26» son el mismo número de la serie (series.probar los lee igual)."""
    t = re.sub(r"\s+", "", str(numero or "")).upper()
    return re.sub(r"\d+", lambda m: m.group().lstrip("0") or "0", t)


def _filtro_numero(numero) -> tuple[str, str]:
    """Clave del número y un fragmento que cualquier número equivalente contiene sin espacios (su grupo de cifras más
    largo sin ceros a la izquierda), para acotar la consulta antes de comparar las claves."""
    clave = clave_numero(numero)
    return clave, max(re.findall(r"\d+", clave) or [""], key=len) or clave


def _lecturas_serie(con, empresa_id: int, numero: str, patrones: list[str] | None = None) -> list[tuple[str, int, int]]:
    """(patrón, número, año) con que cada serie de la empresa (o cada uno de `patrones`) lee el número. Con un patrón sin
    separador entre el año y el número («{aaaa}{n}», «F{aa}{n}»), «20260166» y «2026166» son el mismo número 166 de 2026
    (series.probar), que clave_numero no iguala: los ceros de relleno del número quedan dentro de un grupo de cifras."""
    if patrones is None:
        patrones = [s["patron"] for s in db.filas(con, "SELECT DISTINCT patron FROM series WHERE empresa_id=?", (empresa_id,))]
    out = []
    for patron in patrones:
        try:
            r = _probar_serie(patron, numero)
        except (ValueError, re.error, TypeError):
            continue
        if r["coincide"]:
            out.append((patron, r["numero"], r["anio"]))
    return out


def _probar_serie(patron: str, numero) -> dict:
    """series.probar sin distinguir espacios ni mayúsculas («fra20260166» impreso es el «FRA{aaaa}{n}» de la serie): solo
    para reconocer el mismo número, nunca para validar el que se expide."""
    texto = str(numero or "")
    r = {"coincide": False}
    for variante in dict.fromkeys([texto, re.sub(r"\s+", "", texto), texto.upper(), re.sub(r"\s+", "", texto).upper()]):
        r = seriesmod.probar(patron, variante)
        if r["coincide"]:
            break
    return r


def _equivalentes(con, empresa_id: int, numero: str, patrones: list[str] | None = None):
    """Función que dice si otro número es el mismo que `numero` (misma clave_numero, o el mismo número y año leídos por el
    patrón de una serie de la empresa) y los fragmentos que cualquier número equivalente contiene sin espacios (para acotar
    la consulta)."""
    clave, trozo = _filtro_numero(numero)
    lecturas = _lecturas_serie(con, empresa_id, numero, patrones)

    def igual(otro) -> bool:
        if not otro:
            return False
        if clave_numero(otro) == clave:
            return True
        for patron, n, anio in lecturas:
            try:
                r = _probar_serie(patron, otro)
            except (ValueError, re.error, TypeError):
                continue
            if r["coincide"] and (r["numero"], r["anio"]) == (n, anio):
                return True
        return False
    trozos = list(dict.fromkeys([trozo] + [str(n) for _, n, _ in lecturas]))
    return clave, igual, trozos


def _sql_trozos(expr: str, trozos: list[str]) -> str:
    return "(" + " OR ".join(f"instr(REPLACE(UPPER({expr}), ' ', ''), ?) > 0" for _ in trozos) + ")"


def documentos_mismo_numero(con, empresa_id: int, numero: str, excluir_id: int | None = None,
                            patrones: list[str] | None = None) -> list[dict]:
    """Otros documentos de la empresa cuya última extracción tiene el mismo número (clave_numero: sin distinguir espacios,
    mayúsculas ni ceros a la izquierda; o el mismo número y año según el patrón de una serie de la empresa), con su
    clasificación, el NIF del emisor leído y si ya dieron lugar a una factura. `patrones`: los de las series que se
    comparan (por omisión, todas las de la empresa)."""
    if not numero:
        return []
    clave, igual, trozos = _equivalentes(con, empresa_id, numero, patrones)
    if not clave:
        return []
    filas = db.filas(con, f"""
        SELECT d.id, d.nombre_archivo, d.subido_en, d.clasificacion, d.clasificacion_confirmada_en IS NOT NULL AS confirmado,
               json_extract(e.datos, '$.emisor.nif.valor') AS emisor_nif,
               EXISTS (SELECT 1 FROM facturas f WHERE f.documento_id = d.id) AS tiene_factura,
               json_extract(e.datos, '$.numero.valor') AS numero_leido
        FROM documentos d JOIN extracciones e ON e.id = (SELECT MAX(id) FROM extracciones WHERE documento_id = d.id)
        WHERE d.empresa_id = ? AND d.id <> ?
          AND {_sql_trozos("json_extract(e.datos, '$.numero.valor')", trozos)}
        ORDER BY d.id""", (empresa_id, excluir_id or 0, *trozos))
    return [{k: v for k, v in f.items() if k != "numero_leido"} for f in filas if igual(f["numero_leido"])]


def facturas_mismo_numero(con, empresa_id: int, emisor_nif: str, numero: str, con_fecha: bool = False,
                          patrones: list[str] | None = None) -> list[dict]:
    """Facturas expedidas por el emisor con el mismo número (clave_numero o patrón de la serie), en cualquier entorno."""
    if not numero:
        return []
    clave, igual, trozos = _equivalentes(con, empresa_id, numero, patrones)
    filas = db.filas(con, f"""SELECT id, entorno, num_serie{', fecha_expedicion' if con_fecha else ''} FROM facturas
                             WHERE empresa_id=? AND emisor_nif=? AND {_sql_trozos("num_serie", trozos)}
                             ORDER BY id""", (empresa_id, emisor_nif, *trozos))
    return [f for f in filas if igual(f["num_serie"])]


def duplicados_de(con, empresa_id: int, ext: dict, excluir_id: int | None = None) -> list[dict]:
    """Misma identidad fiscal (emisor + número) que la extracción `ext` en otros PDF o en facturas ya expedidas."""
    emisor = nifmod.normalizar((ext.get("emisor", {}).get("nif") or {}).get("valor") or "")
    numero = (ext.get("numero") or {}).get("valor")
    if not numero:
        return []
    out = []
    for d in documentos_mismo_numero(con, empresa_id, numero, excluir_id):
        if nifmod.normalizar(d["emisor_nif"] or "") == emisor:
            out.append({"tipo": "documento", "id": d["id"], "nombre": d["nombre_archivo"], "subido_en": d["subido_en"],
                        "clasificacion": d["clasificacion"], "confirmado": bool(d["confirmado"])})
    for f in facturas_mismo_numero(con, empresa_id, emisor, numero, con_fecha=True):
        out.append({"tipo": "factura", **f})
    return out


def duplicados_fiscales(con, empresa_id: int, documento_id: int) -> list[dict]:
    """Misma identidad fiscal (emisor + número) en otros PDF distintos o en facturas ya expedidas."""
    ext = extraccion(con, documento_id)
    if not ext:
        return []
    return duplicados_de(con, empresa_id, ext, documento_id)


def crear_borrador(con, *, documento_id: int, usuario_id: int) -> int:
    doc = db.fila(con, "SELECT * FROM documentos WHERE id=?", (documento_id,))
    if doc["clasificacion"] != "preparatorio" or not doc["clasificacion_confirmada_en"]:
        raise ValueError("Solo se crean borradores a partir de documentos preparatorios confirmados")
    ext = extraccion(con, documento_id)
    datos = borradores.datos_desde_extraccion(ext, doc["nombre_archivo"])
    with db.transaccion(con):
        existente = db.fila(con, """SELECT id FROM borradores WHERE documento_id=? AND (estado<>'descartado' OR factura_id IS NOT NULL)
                                   ORDER BY id DESC LIMIT 1""", (documento_id,))
        if existente:
            return existente["id"]
        if db.fila(con, "SELECT id FROM facturas WHERE documento_id=?", (documento_id,)):
            raise ValueError("El documento ya dio lugar a una factura expedida")
        ahora = db.ahora_iso()
        cur = con.execute("""INSERT INTO borradores (empresa_id, documento_id, extraccion_id, datos, estado, creado_en, actualizado_en)
                             VALUES (?,?,?,?, 'requiere_revision', ?, ?)""",
                          (doc["empresa_id"], documento_id, ext["_id"], json.dumps(datos, ensure_ascii=False), ahora, ahora))
        db.actividad(con, "borrador.creado", usuario_id=usuario_id, empresa_id=doc["empresa_id"], objeto="borrador", objeto_id=cur.lastrowid)
        return cur.lastrowid
