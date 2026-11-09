"""Importación de PDF: validación, deduplicación, conservación del original, inspección y extracción."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import almacen, borradores, db
from . import nif as nifmod
from .extraccion import campos, inspeccion, texto


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
    except inspeccion.PDFNoValido as e:
        return {"nombre": nombre, "estado": "error", "error": str(e)}
    ext = campos.Extractor(paginas, nombre, empresa["nif"]).extraer()
    # Líneas de cabecera para el recorte del logotipo
    lin = texto.lineas(paginas)
    ext["_lineas_cabecera"] = [{"texto": l.texto, "bbox": l.bbox()} for l in lin if l.pagina == 1 and l.top < 0.2 * paginas[0].alto]
    sug = borradores.clasificar(ext, insp, empresa)
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


def duplicados_fiscales(con, empresa_id: int, documento_id: int) -> list[dict]:
    """Misma identidad fiscal (emisor + número) en otros PDF distintos o en facturas ya expedidas."""
    ext = extraccion(con, documento_id)
    if not ext:
        return []
    emisor = nifmod.normalizar((ext.get("emisor", {}).get("nif") or {}).get("valor") or "")
    numero = (ext.get("numero") or {}).get("valor")
    if not numero:
        return []
    out = []
    for d in db.filas(con, "SELECT id, nombre_archivo, subido_en FROM documentos WHERE empresa_id=? AND id<>?", (empresa_id, documento_id)):
        e2 = extraccion(con, d["id"])
        if not e2:
            continue
        if ((e2.get("numero") or {}).get("valor") == numero
                and nifmod.normalizar((e2.get("emisor", {}).get("nif") or {}).get("valor") or "") == emisor):
            out.append({"tipo": "documento", "id": d["id"], "nombre": d["nombre_archivo"], "subido_en": d["subido_en"]})
    for f in db.filas(con, "SELECT id, entorno, num_serie, fecha_expedicion FROM facturas WHERE empresa_id=? AND emisor_nif=? AND num_serie=?",
                      (empresa_id, emisor, numero)):
        out.append({"tipo": "factura", **f})
    return out


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
