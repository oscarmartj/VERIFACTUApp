"""Copias de seguridad y restauración con reproducción del diario.

copia:     instantánea consistente de SQLite (API de backup) + almacén de documentos + manifiesto SHA-256.
restaurar: (1) verifica la copia; (2) reproduce el diario sobre una COPIA TEMPORAL de la base de datos
           restaurada (claves foráneas desactivadas durante la reproducción, referencias ausentes completadas);
           (3) solo si todo lo anterior termina bien, aparta la base de datos actual (no la borra) y coloca la
           restaurada; (4) fusiona el almacén de la copia con el actual sin borrar ni sobrescribir ficheros.
           Resultado: no se pierde la continuidad de la cadena ni se reenvían registros ya aceptados.
La clave maestra NO se incluye en la copia: se custodia aparte.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import almacen, db, diario
from .config import ajustes


def crear_copia(con, destino: Path | None = None) -> Path:
    a = ajustes()
    marca = db.ahora().strftime("%Y%m%dT%H%M%S")
    d = (destino or a.copias_dir) / f"copia_{marca}"
    d.mkdir(parents=True, exist_ok=False)
    dst = sqlite3.connect(d / "verifactu.sqlite3")
    con.backup(dst)
    dst.close()
    with tarfile.open(d / "almacen.tar.gz", "w:gz") as t:
        if a.almacen_dir.exists():
            t.add(a.almacen_dir, arcname="almacen")
    cadenas = db.filas(con, "SELECT * FROM cadenas")
    manifiesto = {"creada_en": db.ahora_iso(), "cadenas": cadenas,
                  "lineas_diario": len(diario.leer()),
                  "ficheros": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir()}}
    (d / "manifiesto.json").write_text(json.dumps(manifiesto, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def verificar_copia(d: Path) -> dict:
    m = json.loads((d / "manifiesto.json").read_text(encoding="utf-8"))
    errores = [n for n, h in m["ficheros"].items() if hashlib.sha256((d / n).read_bytes()).hexdigest() != h]
    return {"ok": not errores, "errores": errores, "manifiesto": m}


def _fusionar(origen: Path) -> int:
    """Añade al almacén los ficheros de `origen` que falten. Nunca borra ni sobrescribe."""
    a = ajustes()
    a.almacen_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    if origen.exists():
        for p in origen.rglob("*"):
            if p.is_file() and not p.name.endswith(".tmp"):
                dst = a.almacen_dir / p.relative_to(origen)
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dst)
                    n += 1
    return n


def _reparar_pdfs(con, fuentes: list[Path], marca: str) -> dict:
    """Comprueba el PDF de cada factura contra su SHA-256 registrado. Si falta o está dañado, lo toma de una copia
    válida (copia de seguridad o espejo del diario) o lo regenera; lo que no se puede recuperar se informa."""
    reparados, regenerados, irrecuperables = [], [], []
    for f in db.filas(con, "SELECT id, pdf_ruta, pdf_sha256 FROM facturas WHERE pdf_ruta IS NOT NULL"):
        if almacen.existe(f["pdf_ruta"]) and almacen.sha256(almacen.leer(f["pdf_ruta"])) == f["pdf_sha256"]:
            continue
        candidato = next((b / f["pdf_ruta"] for b in fuentes if (b / f["pdf_ruta"]).is_file()
                          and hashlib.sha256((b / f["pdf_ruta"]).read_bytes()).hexdigest() == f["pdf_sha256"]), None)
        destino = almacen.ruta_absoluta(f["pdf_ruta"])
        if destino.exists():
            os.replace(destino, destino.with_name(destino.name + f".danado-{marca}"))
        if candidato:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidato, destino)
            reparados.append(f["id"])
        else:
            try:
                ok = regenerar_pdf(con, f["id"])["identico"]
            except Exception:  # noqa: BLE001
                ok = False
            (regenerados if ok else irrecuperables).append(f["id"])
    return {"pdf_reparados": reparados, "pdf_regenerados": regenerados, "pdf_irrecuperables": irrecuperables}


def restaurar(d: Path, forzar: bool = False) -> dict:
    """Debe ejecutarse con la aplicación y el trabajador detenidos.

    forzar=True permite completar una restauración con entradas del diario descartadas por integridad, tras revisión
    manual (los números y posiciones afectados quedan reservados para no reutilizarse)."""
    v = verificar_copia(d)
    if not v["ok"]:
        raise RuntimeError(f"Copia dañada: {v['errores']}")
    a = ajustes()
    marca = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    a.db_path.parent.mkdir(parents=True, exist_ok=True)
    # 1) Si la base viva se puede leer, se concilia antes con el diario: nada confirmado en ella puede perderse
    vivas_f, vivas_r = set(), set()
    if a.db_path.exists():
        try:
            viva = db.conectar(a.db_path, crear=False)
            try:
                reconciliar_diario(viva)
                vivas_f = {x["id"] for x in db.filas(viva, "SELECT id FROM facturas")}
                vivas_r = {x["huella"] for x in db.filas(viva, "SELECT huella FROM registros")}
            finally:
                viva.close()
        except sqlite3.DatabaseError:
            pass  # base dañada: se restaura solo con la copia y el diario
    tmp = a.db_path.with_name(f"restauracion_{marca}.sqlite3")
    shutil.copy2(d / "verifactu.sqlite3", tmp)
    try:
        con = db.conectar(tmp)
        db.migrar(con)
        con.execute("PRAGMA foreign_keys=OFF")
        r = reproducir_diario(con)
        r["usuarios_provisionales"] = _completar_usuarios(con)
        r["referencias_pendientes"] = [tuple(x) for x in con.execute("PRAGMA foreign_key_check").fetchall()]
        huerfanos = db.filas(con, "SELECT id, num_serie FROM registros WHERE factura_id NOT IN (SELECT id FROM facturas)")
        if (r["entradas_descartadas"] or r["lineas_diario_ilegibles"] or huerfanos) and not forzar:
            raise RuntimeError("La restauración se detiene: el diario contiene entradas dañadas o registros sin su factura "
                               f"({len(r['entradas_descartadas'])} descartadas, {len(r['lineas_diario_ilegibles'])} líneas "
                               f"ilegibles, {len(huerfanos)} registros huérfanos). "
                               "Revíselo y, si procede, repita con forzar=True.")
        if huerfanos:
            _reservar_huerfanos(con, huerfanos)
        faltan_f = vivas_f - {x["id"] for x in db.filas(con, "SELECT id FROM facturas")}
        faltan_r = vivas_r - {x["huella"] for x in db.filas(con, "SELECT huella FROM registros")}
        if (faltan_f or faltan_r) and not forzar:
            raise RuntimeError(f"La restauración perdería {len(faltan_f)} facturas y {len(faltan_r)} registros de la base actual; "
                               "se detiene sin cambiar nada.")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except BaseException:
        for suf in ("", "-wal", "-shm"):
            Path(str(tmp) + suf).unlink(missing_ok=True)
        raise
    # Solo ahora se sustituye la base de datos. La actual se conserva como copia consistente (incluido su WAL)
    # y el cambio final es un único os.replace atómico: en ningún momento falta la base de datos.
    apartada = None
    for suf in ("-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    if a.db_path.exists():
        apartada = a.db_path.with_name(f"verifactu.antes_de_restaurar_{marca}.sqlite3")
        try:
            viva = sqlite3.connect(a.db_path)
            viva.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst = sqlite3.connect(apartada)
            viva.backup(dst)
            dst.close()
            viva.close()
        except sqlite3.DatabaseError:
            # Base dañada: se conserva tal cual, con su WAL emparejado por nombre
            shutil.copy2(a.db_path, apartada)
            for suf in ("-wal", "-shm"):
                if Path(str(a.db_path) + suf).exists():
                    shutil.copy2(Path(str(a.db_path) + suf), Path(str(apartada) + suf))
    for suf in ("-wal", "-shm"):  # ya volcados a la copia apartada; no deben aplicarse a la base restaurada
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    os.replace(tmp, a.db_path)
    with tempfile.TemporaryDirectory(dir=a.data_dir) as td:
        with tarfile.open(d / "almacen.tar.gz") as t:
            t.extractall(td, filter="data")
        de_copia = Path(td) / "almacen"
        espejo = diario.directorio() / "almacen"
        r["ficheros_almacen_recuperados"] = _fusionar(de_copia) + _fusionar(espejo)
        con = db.conectar()
        r.update(_reparar_pdfs(con, [espejo, de_copia], marca))
        con.close()
    return {"restaurada_de": str(d), "base_anterior_apartada": str(apartada) if apartada else None, **r}


def _reservar_huerfanos(con, huerfanos: list[dict]) -> None:
    """Registros cuya factura no pudo recuperarse: se reserva una factura marcadora con su id y se avanza el contador
    de la serie para que ni el número ni el identificador vuelvan a usarse."""
    from . import series as ser
    for r in huerfanos:
        reg = db.fila(con, "SELECT * FROM registros WHERE id=?", (r["id"],))
        if not db.fila(con, "SELECT id FROM facturas WHERE id=?", (reg["factura_id"],)):
            d, m, a_ = reg["fecha_expedicion"].split("-")
            con.execute("""INSERT INTO borradores (empresa_id, datos, estado, creado_en, actualizado_en)
                           VALUES (?, '{}', 'emitido', ?, ?)""", (reg["empresa_id"], db.ahora_iso(), db.ahora_iso()))
            bid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            marcador = json.dumps({"irrecuperable": True, "registro": reg["huella"]})
            con.execute("""INSERT INTO facturas (id, empresa_id, entorno, borrador_id, clave_idempotencia, num_serie, fecha_expedicion,
                           tipo_factura, emisor_nif, cuota_total, importe_total, datos, datos_sha256, url_qr, estado, creada_por, creada_en)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'emitida', (SELECT MIN(id) FROM usuarios), ?)""",
                        (reg["factura_id"], reg["empresa_id"], reg["entorno"], bid, f"irrecuperable-{reg['huella']}", reg["num_serie"],
                         f"{a_}-{m}-{d}", reg["tipo_factura"] or "F1", reg["id_emisor"], reg["cuota_total"] or "0", reg["importe_total"] or "0",
                         marcador, hashlib.sha256(marcador.encode()).hexdigest(), "", db.ahora_iso()))
            s = ser.identificar(con, reg["empresa_id"], reg["entorno"], reg["num_serie"], int(a_))
            if s:
                con.execute("""INSERT INTO series_contadores (serie_id, anio, siguiente) VALUES (?,?,?)
                               ON CONFLICT(serie_id, anio) DO UPDATE SET siguiente=MAX(siguiente, excluded.siguiente)""",
                            (s["id"], s["anio_contador"], s["numero_detectado"] + 1))


def _insertar_si_falta(con, tabla: str, fila: dict | None) -> bool:
    if not fila or db.fila(con, f"SELECT 1 FROM {tabla} WHERE id=?", (fila["id"],)):
        return False
    cols = [c[1] for c in con.execute(f"PRAGMA table_info({tabla})") if c[1] in fila]
    con.execute(f"INSERT OR IGNORE INTO {tabla} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [fila[c] for c in cols])
    return True


def _completar_usuarios(con) -> list[int]:
    """Usuarios referenciados que no existen en la copia: se crean inactivos y sin contraseña utilizable."""
    faltan = set()
    for (tabla,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for fk in con.execute(f"PRAGMA foreign_key_list({tabla})").fetchall():
            if fk[2] == "usuarios":
                col = fk[3]
                for (uid,) in con.execute(f"SELECT DISTINCT {col} FROM {tabla} WHERE {col} IS NOT NULL AND {col} NOT IN (SELECT id FROM usuarios)"):
                    faltan.add(uid)
    for uid in sorted(faltan):
        con.execute("INSERT INTO usuarios (id, email, nombre, hash_clave, activo, creado_en) VALUES (?,?,?,?,0,?)",
                    (uid, f"restaurado-{uid}@invalido.local", f"Usuario restaurado {uid}", "!", db.ahora_iso()))
    return sorted(faltan)


def _ts(x: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(x) if x else None
    except ValueError:
        return None


def entrada_integra(e: dict) -> bool:
    """Las facturas y registros anotados deben coincidir con sus propias sumas (datos, XML y huella)."""
    from .verifactu import huella as hmod
    d = e.get("datos") or {}
    try:
        if e["tipo"] == "registro":
            return (hashlib.sha256(d["xml"].encode("utf-8")).hexdigest() == d["xml_sha256"]
                    and hmod.sha256_mayus(d["cadena_huella"]) == d["huella"])
        if e["tipo"] == "factura":
            return hashlib.sha256(d["datos"].encode("utf-8")).hexdigest() == d["datos_sha256"]
    except (KeyError, AttributeError, TypeError):
        return False
    return True


def reproducir_diario(con) -> dict:
    """Reinserta facturas y registros posteriores a la copia y aplica el último estado conocido de cada registro."""
    entradas = diario.leer()
    fact_cols = [c[1] for c in con.execute("PRAGMA table_info(facturas)")]
    reg_cols = [c[1] for c in con.execute("PRAGMA table_info(registros)")]
    insertadas_f, insertados_r, estados, contexto = 0, 0, 0, 0
    descartadas = []
    miembros_ult: dict[int, dict] = {}
    miembros_ctx: dict[int, list] = {}
    ultimo_estado: dict[str, dict] = {}
    ahora = datetime.now(timezone.utc)
    with db.transaccion(con):
        for e in entradas:
            t, d = e["tipo"], e["datos"]
            # Integridad: nunca se reproduce un registro o factura cuyo contenido no coincida con sus sumas
            if not entrada_integra(e):
                descartadas.append({"tipo": t, "id": d.get("id"), "motivo": "sumas no coinciden"})
                continue
            if t == "factura":
                ctx_m = (d.get("_contexto") or {}).get("miembros")
                if ctx_m:
                    miembros_ctx[d["empresa_id"]] = ctx_m
            elif t == "miembros":
                if d.get("version", 0) >= miembros_ult.get(d["empresa_id"], {}).get("version", -1):
                    miembros_ult[d["empresa_id"]] = d
            elif t == "estado_registro":
                ultimo_estado[d["huella"]] = d  # el diario es cronológico: vale la última anotación de cada registro
                continue
            if t == "factura" and not db.fila(con, "SELECT id FROM facturas WHERE id=?", (d["id"],)):
                ctx = d.get("_contexto") or {}
                for tabla, clave in (("empresas", "empresa"), ("series", "serie"), ("instalaciones", "instalacion"),
                                     ("documentos", "documento"), ("extracciones", "extraccion"), ("borradores", "borrador")):
                    contexto += _insertar_si_falta(con, tabla, ctx.get(clave))

                if not db.fila(con, "SELECT id FROM borradores WHERE id=?", (d["borrador_id"],)):
                    con.execute("""INSERT INTO borradores (id, empresa_id, documento_id, datos, estado, creado_en, actualizado_en)
                                   VALUES (?,?,?,?, 'emitido', ?, ?)""",
                                (d["borrador_id"], d["empresa_id"], d.get("documento_id"), d["datos"], d["creada_en"], d["creada_en"]))
                cols = [c for c in fact_cols if c in d]
                con.execute(f"INSERT INTO facturas ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [d[c] for c in cols])
                con.execute("UPDATE borradores SET factura_id=?, estado='emitido' WHERE id=?", (d["id"], d["borrador_id"]))
                num = json.loads(d["datos"]).get("numeracion") or {}
                if num.get("serie_id"):
                    con.execute("""INSERT INTO series_contadores (serie_id, anio, siguiente) VALUES (?,?,?)
                                   ON CONFLICT(serie_id, anio) DO UPDATE SET siguiente=MAX(siguiente, excluded.siguiente)""",
                                (num["serie_id"], num.get("anio") or 0, num["numero"] + 1))
                insertadas_f += 1
            elif t == "estado_factura":
                con.execute("UPDATE facturas SET estado=? WHERE id=?", (d["estado"], d["id"]))
            elif t == "registro" and not db.fila(con, "SELECT id FROM registros WHERE huella=?", (d["huella"],)):
                cols = [c for c in reg_cols if c in d]
                con.execute(f"INSERT INTO registros ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [d[c] for c in cols])
                cad = db.fila(con, "SELECT * FROM cadenas WHERE empresa_id=? AND entorno=? AND instalacion_id=?",
                              (d["empresa_id"], d["entorno"], d["instalacion_id"]))
                if not cad:
                    con.execute("INSERT INTO cadenas (empresa_id, entorno, instalacion_id, secuencia) VALUES (?,?,?,0)",
                                (d["empresa_id"], d["entorno"], d["instalacion_id"]))
                    cad = {"secuencia": 0}
                if d["secuencia"] > cad["secuencia"]:
                    con.execute("""UPDATE cadenas SET ultimo_registro_id=?, secuencia=?, ultima_fecha_hora=?
                                   WHERE empresa_id=? AND entorno=? AND instalacion_id=?""",
                                (d["id"], d["secuencia"], d["fecha_hora_huso"], d["empresa_id"], d["entorno"], d["instalacion_id"]))
                insertados_r += 1
        # Estado de comunicación: solo la última anotación de cada registro, y solo si no es anterior a la copia
        for huella_, d in ultimo_estado.items():
            actual = db.fila(con, "SELECT actualizado_en FROM registros WHERE huella=?", (huella_,))
            ta, td = _ts(actual and actual["actualizado_en"]), _ts(d.get("actualizado_en"))
            if not actual or (ta is not None and td is not None and ta > td):
                continue
            enviando = d["estado_envio"] == "enviando"
            est = "sin_confirmar" if enviando else d["estado_envio"]
            sig = d.get("siguiente_intento_en")
            if enviando:
                # Igual que la recuperación en caliente: posible entrega, incidencia, espera y control de flujo
                from .remision import _backoff
                sig = (ahora + timedelta(seconds=_backoff(d.get("intentos") or 1))).replace(microsecond=0).isoformat()
                reg = db.fila(con, "SELECT empresa_id, entorno FROM registros WHERE huella=?", (huella_,))
                con.execute("""INSERT INTO control_flujo (empresa_id, entorno, tiempo_espera, incidencia_desde, proximo_envio_en)
                               VALUES (?,?,60,?,?) ON CONFLICT(empresa_id, entorno) DO UPDATE SET
                               incidencia_desde=COALESCE(incidencia_desde, excluded.incidencia_desde),
                               proximo_envio_en=excluded.proximo_envio_en""",
                            (reg["empresa_id"], reg["entorno"], ahora.replace(microsecond=0).isoformat(),
                             (ahora + timedelta(seconds=60)).replace(microsecond=0).isoformat()))
            con.execute("""UPDATE registros SET estado_envio=?, codigo_error=?, descripcion_error=?, csv=?, intentos=?,
                           posible_entrega=?, incidencia=?, duplicado=?, siguiente_intento_en=?, actualizado_en=? WHERE huella=?""",
                        (est, d["codigo_error"], d["descripcion_error"], d["csv"], d["intentos"],
                         1 if enviando else (d.get("posible_entrega") or 0), 1 if enviando else (d.get("incidencia") or 0),
                         d.get("duplicado"), sig, d["actualizado_en"], huella_))
            estados += 1
        # Miembros: la instantánea de mayor versión de cada empresa, solo si es posterior a la de la copia
        for eid, snap in miembros_ult.items():
            if _insertar_si_falta(con, "empresas", snap.get("empresa")):
                contexto += 1
                con.execute("UPDATE empresas SET version_miembros=0 WHERE id=?", (eid,))  # recién recuperada: sin miembros aún
            emp = db.fila(con, "SELECT version_miembros FROM empresas WHERE id=?", (eid,))
            if not emp or snap.get("version", 0) <= emp["version_miembros"]:
                continue
            con.execute("DELETE FROM miembros WHERE empresa_id=?", (eid,))
            for m in snap["miembros"]:
                con.execute("""INSERT INTO miembros (usuario_id, empresa_id, rol, relacion, declaracion_autorizacion_en)
                               VALUES (?,?,?,?,?)""", (m["usuario_id"], m["empresa_id"], m["rol"], m["relacion"],
                                                       m.get("declaracion_autorizacion_en")))
                contexto += 1
            con.execute("UPDATE empresas SET version_miembros=? WHERE id=?", (snap["version"], eid))
        # Las instantáneas de contexto de las facturas solo rellenan empresas que hayan quedado sin ningún miembro
        for eid, ms in miembros_ctx.items():
            if db.fila(con, "SELECT 1 FROM empresas WHERE id=?", (eid,)) and not db.fila(con, "SELECT 1 FROM miembros WHERE empresa_id=?", (eid,)):
                for m in ms:
                    con.execute("""INSERT OR IGNORE INTO miembros (usuario_id, empresa_id, rol, relacion, declaracion_autorizacion_en)
                                   VALUES (?,?,?,?,?)""", (m["usuario_id"], m["empresa_id"], m["rol"], m["relacion"],
                                                           m.get("declaracion_autorizacion_en")))
                    contexto += 1
        # El estado de cada factura se deriva de sus registros y rectificativas (cubre anotaciones perdidas).
        # De la más reciente a la más antigua (una rectificativa siempre es posterior) y hasta estabilizar.
        from .emision import recalcular_estado_factura
        for _ in range(5):
            if not any([recalcular_estado_factura(con, f["id"]) for f in db.filas(con, "SELECT id FROM facturas ORDER BY id DESC")]):
                break
        db.actividad(con, "copia.diario_reproducido", detalle={"facturas": insertadas_f, "registros": insertados_r,
                                                              "estados": estados, "filas_contexto": contexto})
    return {"facturas_recuperadas": insertadas_f, "registros_recuperados": insertados_r, "estados_aplicados": estados,
            "filas_contexto_recuperadas": contexto, "entradas_descartadas": descartadas,
            "lineas_diario_ilegibles": list(diario.LINEAS_INVALIDAS)}


def reconciliar_diario(con) -> int:
    """Al arrancar: añade al diario las facturas, registros y ESTADOS (de factura y de comunicación) que falten o
    difieran de la última anotación (p. ej. caída entre la confirmación en la base de datos y la anotación)."""
    entradas = diario.leer()
    reg_huellas, fact_ids = set(), set()
    ult_estado_reg, ult_estado_fact = {}, {}
    for e in entradas:
        d = e["datos"]
        if e["tipo"] == "registro":
            reg_huellas.add(d["huella"])
            ult_estado_reg[d["huella"]] = (d.get("estado_envio"), d.get("posible_entrega", 0))
        elif e["tipo"] == "estado_registro":
            ult_estado_reg[d["huella"]] = (d.get("estado_envio"), d.get("posible_entrega", 0))
        elif e["tipo"] == "factura":
            fact_ids.add(d["id"])
            ult_estado_fact[d["id"]] = d.get("estado")
        elif e["tipo"] == "estado_factura":
            ult_estado_fact[d["id"]] = d.get("estado")
    ult_version_m = {}
    for e in entradas:
        if e["tipo"] == "miembros":
            ult_version_m[e["datos"]["empresa_id"]] = max(ult_version_m.get(e["datos"]["empresa_id"], -1), e["datos"].get("version", 0))
    n = 0
    cols = {c[1] for c in con.execute("PRAGMA table_info(empresas)")}
    if "version_miembros" in cols:
        for emp in db.filas(con, "SELECT * FROM empresas"):
            if ult_version_m.get(emp["id"], -1) < emp["version_miembros"]:
                diario.anotar("miembros", {"empresa_id": emp["id"], "version": emp["version_miembros"], "empresa": emp,
                                           "miembros": db.filas(con, "SELECT * FROM miembros WHERE empresa_id=?", (emp["id"],))})
                n += 1
    for f in db.filas(con, "SELECT id, estado FROM facturas ORDER BY id"):
        if f["id"] not in fact_ids:
            diario.anotar_factura(con, f["id"])
            n += 1
        elif ult_estado_fact.get(f["id"]) != f["estado"]:
            diario.anotar("estado_factura", {"id": f["id"], "estado": f["estado"]})
            n += 1
    for r in db.filas(con, "SELECT id, huella, estado_envio, posible_entrega FROM registros ORDER BY id"):
        if r["huella"] not in reg_huellas:
            diario.anotar_registro(con, r["id"])
            n += 1
        if ult_estado_reg.get(r["huella"]) != (r["estado_envio"], r["posible_entrega"]):
            diario.anotar_estado(con, r["id"])
            n += 1
    return n


def comprobar_arranque() -> None:
    """Impide arrancar con una base de datos inexistente, vacía, dañada o atrasada respecto al diario: crear o usar
    una base así reiniciaría la numeración o bifurcaría la cadena. El remedio es copias.restaurar."""
    a = ajustes()
    entradas = [e for e in diario.leer() if entrada_integra(e)]
    restos = list(a.data_dir.glob("restauracion_*.sqlite3")) if a.data_dir.exists() else []
    remedio = " No se arranca para no reiniciar la numeración ni bifurcar la cadena: restaure una copia (copias.restaurar)."
    if not a.db_path.exists():
        if entradas or restos:
            raise RuntimeError(f"No existe la base de datos {a.db_path}, pero el diario tiene {len(entradas)} anotaciones" + remedio)
        return
    if not entradas:
        return
    try:
        c = sqlite3.connect(f"file:{a.db_path}?mode=ro", uri=True)
        try:
            tablas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"facturas", "registros"} <= tablas:
                raise RuntimeError(f"La base de datos {a.db_path} está vacía o incompleta y el diario tiene anotaciones." + remedio)
            ids = {r[0] for r in c.execute("SELECT id FROM facturas")}
            huellas = {r[0] for r in c.execute("SELECT huella FROM registros")}
            huerfanos = c.execute("SELECT COUNT(*) FROM registros WHERE factura_id NOT IN (SELECT id FROM facturas)").fetchone()[0]
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"La base de datos {a.db_path} no se puede leer ({e})." + remedio) from e
    if huerfanos:
        raise RuntimeError(f"La base de datos tiene {huerfanos} registros sin su factura." + remedio)
    faltan_f = {e["datos"]["id"] for e in entradas if e["tipo"] == "factura"} - ids
    faltan_r = {e["datos"]["huella"] for e in entradas if e["tipo"] == "registro"} - huellas
    if faltan_f or faltan_r:
        raise RuntimeError(f"La base de datos está atrasada respecto al diario ({len(faltan_f)} facturas y {len(faltan_r)} "
                           "registros anotados no están en ella)." + remedio)


def regenerar_pdf(con, factura_id: int) -> dict:
    """Regenera el PDF de una factura desde su versión inmutable y comprueba que es idéntico (SHA-256)."""
    from . import emision, pdf_final
    f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (factura_id,))
    datos = json.loads(f["datos"])
    doc = emision.cargar_documento(con, f.get("documento_id"))
    original = almacen.leer(doc["ruta"]) if doc and almacen.existe(doc["ruta"]) else None
    b = db.fila(con, "SELECT * FROM borradores WHERE id=?", (f["borrador_id"],))
    ext = emision.extraccion_de(con, b) if b else None
    pdf, _ = pdf_final.generar(datos, modo="definitivo", entorno=f["entorno"], url_qr=f["url_qr"], original=original,
                               extraccion=ext, fecha_expedicion=f["fecha_expedicion"],
                               forzar_estrategia=(datos.get("pdf") or {}).get("estrategia"))
    identico = almacen.sha256(pdf) == f["pdf_sha256"]
    if identico:
        almacen.guardar(f["pdf_ruta"], pdf, sobrescribir=True)
    return {"factura_id": factura_id, "identico": identico}
