"""Copias de seguridad y restauración con reproducción del diario.

copia:     instantánea consistente de SQLite (API de backup) + almacén de documentos + manifiesto SHA-256.
restaurar: (1) verifica la copia; (2) reproduce el diario sobre una COPIA TEMPORAL de la base de datos
           restaurada (claves foráneas desactivadas durante la reproducción, referencias ausentes completadas);
           (3) solo si todo lo anterior termina bien, aparta la base de datos actual (no la borra) y coloca la
           restaurada; (4) fusiona el almacén de la copia con el actual sin borrar ni sobrescribir ficheros.
           Resultado: no se pierde la continuidad de la cadena ni se reenvían registros ya aceptados.
           Antes de empezar comprueba que hay espacio libre (espacio_necesario); un fallo antes de colocar la base no
           cambia nada ni deja ficheros a medias, y uno posterior lo explica (RestauracionIncompleta).
La clave maestra NO se incluye en la copia: se custodia aparte. Las copias, la base y sus copias apartadas solo las
puede leer el usuario del servicio (carpetas 0700, ficheros 0600).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import almacen, db, diario
from .config import ajustes


AVISO_SIN_DIARIO = ("Se ha restaurado sin diario de recuperación: lo expedido después de la copia no se ha podido recuperar. "
                    "Antes de expedir, anular o subsanar, consulte a la AEAT (página Consulta a la AEAT) cada mes desde la "
                    "fecha de la copia: avance los contadores de las series y, si la consulta avisa de que la cadena está "
                    "atrasada, reanúdela desde el último registro de la AEAT.")


class RestauracionIncompleta(RuntimeError):
    """La base restaurada ya está colocada, pero no se pudo terminar de fusionar el almacén o reparar los PDF."""


class EspacioInsuficiente(RuntimeError):
    """No hay espacio libre para restaurar: no se ha cambiado nada."""


def _privado(p: Path, carpeta: bool = False) -> None:
    """Solo el usuario del servicio: carpetas 0700 y ficheros 0600 (las copias contienen contraseñas cifradas con scrypt,
    certificados cifrados y documentos)."""
    os.chmod(p, 0o700 if carpeta else 0o600)


def _fichero_privado(p: Path) -> Path:
    """Crea vacío (0600) el fichero que SQLite va a abrir: hereda el modo, también sus -wal, -shm y -journal."""
    os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return p


def _abrir_privado(p: Path):
    """Fichero nuevo (no puede existir) para escribir en binario, creado ya con modo 0600."""
    return os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def _copiar_privado(origen: Path, destino: Path) -> None:
    """Copia el contenido (no los permisos: una copia antigua podía ser 0644) en un fichero nuevo 0600, en disco."""
    with open(origen, "rb") as src, _abrir_privado(destino) as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())


def proteger_ficheros() -> None:
    """Al arrancar: la carpeta de datos, la base de datos (y su WAL), las copias apartadas al restaurar, la base del
    simulador y las copias de seguridad quedan solo para el usuario del servicio (las versiones anteriores las creaban
    legibles por todos). Nunca falla el arranque por esto (p. ej. un volumen que no es del usuario)."""
    a = ajustes()
    candidatos = [(a.data_dir, True)]
    if a.data_dir.exists():
        candidatos += [(p, False) for pat in ("*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm", "*.sqlite3-journal")
                       for p in a.data_dir.glob(pat)]
    if a.copias_dir.exists():
        candidatos.append((a.copias_dir, True))
        for c in a.copias_dir.glob("copia_*"):
            if c.is_dir() and not c.is_symlink():
                candidatos.append((c, True))
                candidatos += [(f, False) for f in c.iterdir() if f.is_file()]
    for p, carpeta in candidatos:
        try:
            if p.exists() and not p.is_symlink() and p.stat().st_mode & 0o077:
                _privado(p, carpeta)
        except OSError:
            pass


def crear_copia(con, destino: Path | None = None) -> Path:
    """Copia completa en <destino o copias_dir>/copia_<marca>/ (carpeta 0700, ficheros 0600). La carpeta de copias se
    crea 0700; una carpeta de destino que ya existía (p. ej. un disco externo) no se modifica."""
    a = ajustes()
    marca = db.ahora().strftime("%Y%m%dT%H%M%S")
    raiz = destino or a.copias_dir
    nueva = not raiz.exists()
    raiz.mkdir(parents=True, exist_ok=True, mode=0o700)
    if nueva or raiz == a.copias_dir:
        _privado(raiz, carpeta=True)
    d = raiz / f"copia_{marca}"
    d.mkdir(exist_ok=False, mode=0o700)
    _privado(d, carpeta=True)
    dst = sqlite3.connect(_fichero_privado(d / "verifactu.sqlite3"))
    con.backup(dst)
    dst.close()
    with _abrir_privado(d / "almacen.tar.gz") as f, tarfile.open(fileobj=f, mode="w:gz") as t:
        if a.almacen_dir.exists():
            t.add(a.almacen_dir, arcname="almacen")
    cadenas = db.filas(con, "SELECT * FROM cadenas")
    manifiesto = {"creada_en": db.ahora_iso(), "cadenas": cadenas,
                  "lineas_diario": len(diario.leer()),
                  "ficheros": {p.name: _sha256_fichero(p) for p in d.iterdir()}}
    with _abrir_privado(d / "manifiesto.json") as f:
        f.write(json.dumps(manifiesto, ensure_ascii=False, indent=1).encode("utf-8"))
    for f in d.iterdir():
        _privado(f)
    return d


def _sha256_fichero(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar_copia(d: Path) -> dict:
    m = json.loads((d / "manifiesto.json").read_text(encoding="utf-8"))
    errores = [n for n, h in m["ficheros"].items() if not (d / n).is_file() or _sha256_fichero(d / n) != h]
    return {"ok": not errores, "errores": errores, "manifiesto": m}


def _fusionar(origen: Path, marca: str) -> int:
    """Añade al almacén los ficheros de `origen` (el espejo del diario) que falten. Nunca borra ni sobrescribe."""
    raiz = almacen._raiz()
    n = 0
    if origen.exists():
        for p in origen.rglob("*"):
            if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp"):
                dst = raiz / p.relative_to(origen)
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _colocar(lambda f, p=p: _volcar(p, f), dst, marca)
                    n += 1
    return n


def _volcar(origen: Path, f) -> None:
    with open(origen, "rb") as src:
        shutil.copyfileobj(src, f, 1024 * 1024)


def _colocar(escribir, dst: Path, marca: str) -> str:
    """Escribe un fichero del almacén en un temporal 0600 junto a su destino y lo coloca con os.replace (nunca queda
    uno a medias con el nombre definitivo); si falla, borra el temporal. Devuelve el SHA-256 de lo escrito."""
    tmp = dst.with_name(f"{dst.name}.restauracion-{marca}.tmp")
    h = hashlib.sha256()
    try:
        with _abrir_privado(tmp) as f:
            class _Suma:
                def write(self, b):
                    h.update(b)
                    return f.write(b)
            escribir(_Suma())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return h.hexdigest()


def _miembros_almacen(t: tarfile.TarFile):
    """Ficheros regulares de la copia del almacén, leídos en secuencia, con su ruta relativa dentro del almacén. Se ignoran
    enlaces, dispositivos, rutas absolutas o con «..» y temporales."""
    for m in t:
        if not m.isfile():
            continue
        partes = m.name.replace("\\", "/").split("/")
        if len(partes) < 2 or partes[0] != "almacen" or any(x in ("", ".", "..") for x in partes[1:]) \
                or partes[-1].endswith(".tmp"):
            continue
        yield m, "/".join(partes[1:])


def _fusionar_tar(tar: Path, marca: str) -> int:
    """Añade al almacén los ficheros de la copia (almacen.tar.gz) que falten, leyéndolos de uno en uno del propio tar
    (sin descomprimir antes toda la copia): nunca borra ni sobrescribe, y el almacén no se necesita dos veces en disco."""
    almacen._raiz()   # la carpeta del almacén, 0700
    n = 0
    with tarfile.open(tar, "r:gz") as t:
        for m, rel in _miembros_almacen(t):
            try:
                dst = almacen.ruta_absoluta(rel)
            except ValueError:   # fuera del almacén (p. ej. por un enlace): se ignora
                continue
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = t.extractfile(m)
            if src is None:
                continue
            with src:
                _colocar(lambda f: shutil.copyfileobj(src, f, 1024 * 1024), dst, marca)
            n += 1
    return n


def _bytes_por_fusionar(tar: Path, espejo: Path) -> int:
    """Bytes de los ficheros de la copia del almacén y del espejo del diario que faltan en el almacén actual (lo que la
    restauración va a escribir en él). Recorre una vez el tar de la copia."""
    total = 0
    with tarfile.open(tar, "r:gz") as t:
        for m, rel in _miembros_almacen(t):
            if not (ajustes().almacen_dir / rel).exists():
                total += m.size
    if espejo.exists():
        for p in espejo.rglob("*"):
            if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp") \
                    and not (ajustes().almacen_dir / p.relative_to(espejo)).exists():
                total += p.stat().st_size
    return total


MARGEN_ESPACIO = 16 * 1024 * 1024   # bytes de holgura: índices, diario de transacciones de SQLite, manifiestos


def _tam(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def espacio_necesario(d: Path) -> list[dict]:
    """Espacio libre que necesita restaurar la copia `d`, por disco: [{carpeta, necesario, libre}]. Estimación (ver
    docs/copias-y-recuperacion.md), con la base actual V (más su WAL), la de la copia C, el diario J y los ficheros del
    almacén A que faltan (de la copia y del espejo del diario): la base temporal T = C + J; reproducir el diario y
    compactarla (VACUUM, que en modo WAL vuelve a escribir toda la base) ocupa hasta 2·T; apartar la base actual, T + V; y
    tras colocar la restaurada (la actual ya apartada), T + A. Se pide el máximo, más MARGEN_ESPACIO, en el disco de la
    carpeta de datos (y A en el del almacén si es otro)."""
    a = ajustes()
    viva = _tam(a.db_path) + _tam(Path(str(a.db_path) + "-wal"))
    temporal = _tam(d / "verifactu.sqlite3") + _tam(diario.ruta())
    alm = _bytes_por_fusionar(d / "almacen.tar.gz", diario.directorio() / "almacen")

    def existente(p: Path) -> Path:
        p = p.resolve()
        while not p.exists() and p != p.parent:
            p = p.parent
        return p
    datos, de_almacen = existente(a.data_dir), existente(a.almacen_dir)
    if os.stat(datos).st_dev == os.stat(de_almacen).st_dev:
        pedido = [(datos, max(2 * temporal, temporal + viva, temporal + alm))]
    else:
        pedido = [(datos, max(2 * temporal, temporal + viva)), (de_almacen, alm)]
    return [{"carpeta": str(c), "necesario": n + MARGEN_ESPACIO, "libre": shutil.disk_usage(c).free} for c, n in pedido]


def _mb(n: int) -> str:
    return f"{max(n, 0) / (1024 * 1024):,.0f} MB".replace(",", ".")


def _comprobar_espacio(d: Path) -> None:
    for x in espacio_necesario(d):
        if x["libre"] < x["necesario"]:
            raise EspacioInsuficiente(
                f"No hay espacio libre suficiente para restaurar la copia: se necesitan unos {_mb(x['necesario'])} libres "
                f"en {x['carpeta']} y hay {_mb(x['libre'])}. Libere espacio (o use un disco mayor) y repita la "
                "restauración: no se ha cambiado nada.")


def _sin_espacio(e: BaseException) -> bool:
    """Error por disco lleno (o cuota agotada), que no es una base de datos dañada."""
    import errno
    if isinstance(e, sqlite3.Error):
        return (getattr(e, "sqlite_errorcode", 0) or 0) & 0xFF == sqlite3.SQLITE_FULL
    return isinstance(e, OSError) and e.errno in (errno.ENOSPC, errno.EDQUOT)


def _borrar_base(p: Path | None) -> None:
    if p is not None:
        for suf in ("", "-wal", "-shm", "-journal"):
            Path(str(p) + suf).unlink(missing_ok=True)


def _reparar_pdfs(con, espejo: Path, tar: Path | None, marca: str) -> dict:
    """Comprueba el PDF de cada factura contra su SHA-256 registrado. Si falta o está dañado, lo toma de una copia
    válida (espejo del diario o, leyéndolo del tar sin descomprimir el resto, copia de seguridad) o lo regenera; lo que no
    se puede recuperar se informa."""
    reparados, regenerados, irrecuperables = [], [], []
    malas = {}
    for f in db.filas(con, "SELECT id, pdf_ruta, pdf_sha256 FROM facturas WHERE pdf_ruta IS NOT NULL"):
        if almacen.existe(f["pdf_ruta"]) and almacen.sha256(almacen.leer(f["pdf_ruta"])) == f["pdf_sha256"]:
            continue
        malas[f["id"]] = f

    def apartar_danado(destino: Path) -> None:
        if destino.exists():
            os.replace(destino, destino.with_name(destino.name + f".danado-{marca}"))
    pendientes = {}
    for fid, f in malas.items():
        e = espejo / f["pdf_ruta"]
        destino = almacen.ruta_absoluta(f["pdf_ruta"])
        if e.is_file() and _sha256_fichero(e) == f["pdf_sha256"]:
            apartar_danado(destino)
            destino.parent.mkdir(parents=True, exist_ok=True)
            _colocar(lambda x, e=e: _volcar(e, x), destino, marca)
            reparados.append(fid)
        else:
            pendientes.setdefault(f["pdf_ruta"].replace("\\", "/"), []).append(f)
    if pendientes and tar is not None and tar.is_file():
        with tarfile.open(tar, "r:gz") as t:
            for m, rel in _miembros_almacen(t):
                if rel not in pendientes:
                    continue
                f = pendientes[rel][0]
                destino = almacen.ruta_absoluta(rel)
                src = t.extractfile(m)
                if src is None:
                    continue
                tmp = destino.with_name(f"{destino.name}.copia-{marca}.tmp")
                destino.parent.mkdir(parents=True, exist_ok=True)
                with src:
                    suma = _colocar(lambda x: shutil.copyfileobj(src, x, 1024 * 1024), tmp, marca)
                if suma != f["pdf_sha256"]:
                    tmp.unlink(missing_ok=True)
                    continue
                apartar_danado(destino)
                os.replace(tmp, destino)
                for x in pendientes.pop(rel):
                    reparados.append(x["id"])
    for lista in pendientes.values():
        for f in lista:
            destino = almacen.ruta_absoluta(f["pdf_ruta"])
            apartar_danado(destino)
            try:
                ok = regenerar_pdf(con, f["id"])["identico"]
            except Exception:  # noqa: BLE001
                ok = False
            (regenerados if ok else irrecuperables).append(f["id"])
    return {"pdf_reparados": sorted(reparados), "pdf_regenerados": regenerados, "pdf_irrecuperables": irrecuperables}


def restaurar(d: Path, forzar: bool = False) -> dict:
    """Debe ejecutarse con la aplicación y el trabajador detenidos.

    forzar=True permite completar una restauración con entradas del diario descartadas por integridad, tras revisión
    manual (los números y posiciones afectados quedan reservados para no reutilizarse).

    Antes de empezar comprueba el espacio libre (espacio_necesario; si no basta: EspacioInsuficiente, sin cambiar nada).
    Un fallo antes de colocar la base restaurada (también un disco lleno al apartar la actual) deja la base actual como
    estaba y borra los temporales. Un fallo después (al fusionar el almacén o reparar los PDF) lanza
    RestauracionIncompleta, que dice qué está hecho y que hay que repetir la restauración con la misma copia."""
    v = verificar_copia(d)
    if not v["ok"]:
        raise RuntimeError(f"Copia dañada: {v['errores']}")
    a = ajustes()
    marca = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    a.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _comprobar_espacio(d)
    # 1) Si la base viva se puede leer, se concilia antes con el diario: nada confirmado en ella puede perderse
    vivas_f, vivas_r = set(), set()
    vivas_s: set | None = None   # sesiones abiertas en la base actual; None: no se pudo leer o está atrasada
    if a.db_path.exists():
        try:
            viva = db.conectar(a.db_path, crear=False)
            try:
                reconciliar_diario(viva)
                vivas_f = {x["id"] for x in db.filas(viva, "SELECT id FROM facturas")}
                vivas_r = {x["huella"] for x in db.filas(viva, "SELECT huella FROM registros")}
                vivas_s = {(x["id"], x["token_hash"], x["usuario_id"])
                           for x in db.filas(viva, "SELECT id, token_hash, usuario_id FROM sesiones")}
                if _atrasada(vivas_f, vivas_r):
                    vivas_s = None   # una base atrasada respecto al diario no dice qué sesiones siguen abiertas
            finally:
                viva.close()
        except sqlite3.DatabaseError as e:
            if _sin_espacio(e):
                raise
            # base dañada: se restaura solo con la copia y el diario
    # Sin diario no se sabe qué se expidió después de la copia. Si una restauración anterior fue sin diario y alguna empresa
    # aún no ha consultado ni expedido desde entonces (anotación `restauracion_sin_diario` en el diario: sobrevive a que se
    # pierda otra vez la base y a que se repita una restauración incompleta), sigue sin saberse para ella: el aviso se
    # mantiene, aunque la conciliación de arriba acabe de rellenar el diario con la base actual
    entradas_previas = diario.leer()
    pendientes_previas = diario.pendientes_sin_diario(entradas_previas)
    sin_diario = not entradas_previas or bool(pendientes_previas)
    empresas_sin_diario: dict[int, dict] = {}
    tmp = a.db_path.with_name(f"restauracion_{marca}.sqlite3")
    try:
        _copiar_privado(d / "verifactu.sqlite3", tmp)   # 0600: sus -wal/-shm y la base colocada heredan el modo
        con = db.conectar(tmp)
        try:
            # Lo que la reproducción borre o sustituya (p. ej. el archivo cifrado de un certificado eliminado después
            # de la copia) se sobrescribe en el fichero, no solo se desenlaza
            con.execute("PRAGMA secure_delete=ON")
            db.migrar(con)
            con.execute("PRAGMA foreign_keys=OFF")
            r = reproducir_diario(con)
            from . import multiusuario   # cuentas y ajustes de la plataforma; las invitaciones pendientes se anulan
            r.update(multiusuario.reproducir_diario(con, diario.leer()))
            r["sesiones_cerradas"] = _cerrar_sesiones(con, vivas_s, diario.leer())
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
                raise RuntimeError(f"La restauración perdería {len(faltan_f)} facturas y {len(faltan_r)} registros de la base "
                                   "actual; se detiene sin cambiar nada.")
            if sin_diario:
                empresas_sin_diario = _anotar_restauracion_sin_diario(con, d, entradas_previas, pendientes_previas)
            con.execute("PRAGMA foreign_keys=ON")
            # Sin páginas libres heredadas de la copia: nada borrado antes o durante la reproducción queda en el fichero
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
    except BaseException:
        _borrar_base(tmp)
        raise
    # 2) Solo ahora se sustituye la base de datos. La actual se conserva como copia consistente (incluido su WAL)
    # y el cambio final es un único os.replace atómico: en ningún momento falta la base de datos. Si no se puede apartar
    # (p. ej. disco lleno), no se cambia nada y no quedan temporales ni una copia apartada a medias.
    apartada = None
    try:
        if sin_diario:
            # Antes de colocar la base: si algo falla después, la repetición (o una restauración posterior aunque se
            # pierda otra vez la base) sigue sabiendo qué empresas están pendientes de consultar y qué meses
            diario.anotar("restauracion_sin_diario", {"copia": d.name, "meses": _meses_desde_la_copia(d),
                                                      "empresas": {str(k): v for k, v in empresas_sin_diario.items()}})
        for suf in ("-wal", "-shm"):
            Path(str(tmp) + suf).unlink(missing_ok=True)
        if a.db_path.exists():
            apartada = a.db_path.with_name(f"verifactu.antes_de_restaurar_{marca}.sqlite3")
            try:
                viva = sqlite3.connect(a.db_path)
                try:
                    viva.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    dst = sqlite3.connect(_fichero_privado(apartada))
                    try:
                        viva.backup(dst)
                    finally:
                        dst.close()
                finally:
                    viva.close()
            except sqlite3.DatabaseError as e:
                if _sin_espacio(e):
                    raise
                # Base dañada: se conserva tal cual, con su WAL emparejado por nombre
                _borrar_base(apartada)
                _copiar_privado(a.db_path, apartada)
                for suf in ("-wal", "-shm"):
                    if Path(str(a.db_path) + suf).exists():
                        _copiar_privado(Path(str(a.db_path) + suf), Path(str(apartada) + suf))
    except BaseException:
        _borrar_base(tmp)
        _borrar_base(apartada)
        raise
    for suf in ("-wal", "-shm"):  # ya volcados a la copia apartada; no deben aplicarse a la base restaurada
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    os.replace(tmp, a.db_path)
    # 3) Almacén y PDF. La base ya está restaurada: un fallo aquí no se oculta (RestauracionIncompleta)
    espejo = diario.directorio() / "almacen"
    try:
        r["ficheros_almacen_recuperados"] = _fusionar_tar(d / "almacen.tar.gz", marca) + _fusionar(espejo, marca)
        con = db.conectar()
        try:
            r.update(_reparar_pdfs(con, espejo, d / "almacen.tar.gz", marca))
        finally:
            con.close()
    except BaseException as e:
        if not isinstance(e, Exception):
            raise
        raise RestauracionIncompleta(
            "Restauración incompleta: la base de datos restaurada ya está colocada"
            + (f" (la anterior se conserva en {apartada})" if apartada else "")
            + f", pero no se pudo terminar de recuperar los documentos del almacén ni comprobar los PDF de las facturas "
            f"({type(e).__name__}: {e}). Corrija la causa (p. ej. libere espacio en disco) y repita la restauración con la "
            "misma copia: es segura de repetir y completa lo que falta. Hasta entonces, algunos documentos o PDF pueden "
            "faltar." + (f" {AVISO_SIN_DIARIO}" if sin_diario else "")) from e
    avisos = [AVISO_SIN_DIARIO] if sin_diario else []
    return {"restaurada_de": str(d), "base_anterior_apartada": str(apartada) if apartada else None, **r, "avisos": avisos}


def _anotar_restauracion_sin_diario(con, d: Path, entradas_previas: list[dict], pendientes: dict[int, int]) -> dict[int, dict]:
    """Empresas que quedan pendientes de consultar tras esta restauración sin diario —{empresa_id: {ultimo, entornos}}: el
    último registro de cada una en la base restaurada y los entornos en los que tenía registros— y constancia en la
    actividad de la plataforma y de cada una de ellas (el aviso queda a la vista del operador y de sus administradores
    aunque se pierda la salida de la restauración).

    Si el diario estaba vacío, todas las empresas de la base restaurada: también las que en la copia aún no tenían
    registros, que pueden ser justo las que expidieron después. Si el diario ya tenía una restauración sin diario, las que
    siguen pendientes y las que aquella anotación no conocía, salvo las que tienen anotaciones en el diario posteriores a
    ella (p. ej. dadas de alta después: todo lo suyo está en el diario y se ha recuperado)."""
    conocidas: set[int] = set()
    k = None
    for i, e in enumerate(entradas_previas):
        if e.get("tipo") == "restauracion_sin_diario":
            conocidas |= {int(x) for x in ((e.get("datos") or {}).get("empresas") or {})}
            k = i
    con_rastro: set[int] = set()
    if k is not None:
        for e in entradas_previas[k + 1:]:
            dd = e.get("datos") or {}
            if isinstance(dd, dict) and isinstance(dd.get("empresa_id"), int):
                con_rastro.add(dd["empresa_id"])
    out: dict[int, dict] = {}
    for e in db.filas(con, "SELECT id FROM empresas ORDER BY id"):
        eid = e["id"]
        if entradas_previas and eid not in pendientes and (eid in conocidas or eid in con_rastro):
            continue
        out[eid] = {"ultimo": db.fila(con, "SELECT COALESCE(MAX(id), 0) m FROM registros WHERE empresa_id=?", (eid,))["m"],
                    "entornos": [x["entorno"] for x in db.filas(con, "SELECT DISTINCT entorno FROM registros WHERE empresa_id=? "
                                                                    "ORDER BY entorno", (eid,))]}
    db.actividad(con, "copia.restaurada_sin_diario", detalle={"copia": d.name, "empresas_pendientes": len(out),
                                                              "aviso": AVISO_SIN_DIARIO})
    for eid in out:
        db.actividad(con, "copia.restaurada_sin_diario", empresa_id=eid, detalle={"copia": d.name, "aviso": AVISO_SIN_DIARIO})
    return out


def _meses_desde_la_copia(d: Path) -> list[str]:
    """«aaaa-mm» de cada mes desde el de la copia (por el nombre de su carpeta, en hora peninsular) hasta el actual: los
    que el aviso pide consultar. Si el nombre no dice la fecha, solo el mes actual."""
    import re
    hoy = db.ahora()
    m = re.match(r"copia_(\d{4})(\d{2})\d{2}T", d.name)
    a, mes = (int(m.group(1)), int(m.group(2))) if m else (hoy.year, hoy.month)
    if (a, mes) > (hoy.year, hoy.month) or a < 2000:
        a, mes = hoy.year, hoy.month
    out = []
    while (a, mes) <= (hoy.year, hoy.month) and len(out) < 120:
        out.append(f"{a:04d}-{mes:02d}")
        a, mes = (a + 1, 1) if mes == 12 else (a, mes + 1)
    return out


def _atrasada(vivas_f: set, vivas_r: set) -> bool:
    """La base actual no contiene facturas o registros anotados en el diario (p. ej. se sustituyó por otra antigua)."""
    entradas = [e for e in diario.leer() if entrada_integra(e)]
    return bool({e["datos"]["id"] for e in entradas if e["tipo"] == "factura"} - vivas_f
                or {e["datos"]["huella"] for e in entradas if e["tipo"] == "registro"} - vivas_r)


def _cerrar_sesiones(con, vivas: set | None, entradas: list[dict]) -> int:
    """Las sesiones vienen de la copia: una cerrada después (salida, contraseña cambiada o temporal, cuenta desactivada)
    no debe volver a valer. Las contraseñas y las bajas ya cierran las sesiones de esas cuentas (versión de la cuenta en el
    diario); aquí se cierran además las salidas anotadas en el diario (`sesion_cerrada`) y, si la base que se sustituye se
    pudo leer y no está atrasada, toda sesión que no siga abierta en ella (mismo identificador, resumen del token y
    usuario)."""
    salidas = {(e.get("datos") or {}).get("token_hash") for e in entradas if e.get("tipo") == "sesion_cerrada"}
    with db.transaccion(con):
        cerrar = [x["id"] for x in db.filas(con, "SELECT id, token_hash, usuario_id FROM sesiones")
                  if x["token_hash"] in salidas or (vivas is not None and (x["id"], x["token_hash"], x["usuario_id"]) not in vivas)]
        con.executemany("DELETE FROM sesiones WHERE id=?", [(i,) for i in cerrar])
    return len(cerrar)


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
    contadores: list[dict] = []   # avances manuales del contador de una serie (series.preparar_avance)
    cadenas_aeat: dict[tuple[int, str], dict] = {}   # última anotación de la cadena respecto a la AEAT (consulta)
    tocadas: set[int] = set()     # facturas que la reproducción inserta o cambia (o cuyos registros cambia)
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
            elif t == "serie_contador":
                if not _contador_valido(d):
                    descartadas.append({"tipo": t, "id": d.get("serie_id"), "motivo": "datos no válidos"})
                else:
                    contadores.append(d)   # se aplican al final, cuando ya están las empresas y series del diario
                continue
            elif t == "cadena_aeat":
                if not _cadena_aeat_valida(d):
                    descartadas.append({"tipo": t, "id": d.get("empresa_id"), "motivo": "datos no válidos"})
                else:
                    cadenas_aeat[(d["empresa_id"], d["entorno"])] = d   # el diario es cronológico: vale la última
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
                tocadas.add(d["id"])
            elif t == "estado_factura":
                if con.execute("UPDATE facturas SET estado=? WHERE id=? AND estado IS NOT ?",
                               (d["estado"], d["id"], d["estado"])).rowcount:
                    tocadas.add(d["id"])
            elif t == "registro" and not db.fila(con, "SELECT id FROM registros WHERE huella=?", (d["huella"],)):
                cols = [c for c in reg_cols if c in d]
                con.execute(f"INSERT INTO registros ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [d[c] for c in cols])
                tocadas.add(d["factura_id"])
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
            actual = db.fila(con, "SELECT actualizado_en, factura_id, estado_envio FROM registros WHERE huella=?", (huella_,))
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
            if est != actual["estado_envio"]:   # el estado de la factura solo depende del estado de sus registros
                tocadas.add(actual["factura_id"])
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
        # Avances manuales de contador (números ya usados fuera de la instalación): nunca retroceden (MAX). Una serie
        # posterior a la copia se recupera de la instantánea anotada si su empresa existe.
        contadores_n = 0
        for d in contadores:
            if not db.fila(con, "SELECT 1 FROM series WHERE id=?", (d["serie_id"],)):
                s_ = d["serie"]
                if not db.fila(con, "SELECT 1 FROM empresas WHERE id=?", (s_["empresa_id"],)):
                    continue
                contexto += _insertar_si_falta(con, "series", s_)
            con.execute("""INSERT INTO series_contadores (serie_id, anio, siguiente) VALUES (?,?,?)
                           ON CONFLICT(serie_id, anio) DO UPDATE SET siguiente=MAX(siguiente, excluded.siguiente)""",
                        (d["serie_id"], d["anio"], d["siguiente"]))
            contadores_n += 1
        # Cadena atrasada respecto a la AEAT o anclada a su último registro (consulta / reanudar la cadena)
        cadenas_n = _reproducir_cadenas_aeat(con, cadenas_aeat)
        # «Mis certificados», asignaciones y legitimaciones: versión más reciente anotada (sin el archivo cifrado)
        from .certificados import reproducir_diario as reproducir_certificados
        certs = reproducir_certificados(con, entradas)
        # El estado de cada factura se deriva de sus registros y rectificativas (cubre anotaciones perdidas). Solo cambia el
        # de las facturas que la reproducción ha tocado y el de las que estas rectifican (y así hacia arriba): las demás
        # vienen de la copia, que es coherente. De la más reciente a la más antigua (una rectificativa siempre es
        # posterior) y hasta estabilizar. Con los índices de la migración 0008, el coste crece en línea con lo reproducido.
        from .emision import recalcular_estado_factura
        revisar = _con_rectificadas(con, tocadas)
        for _ in range(5):
            if not any([recalcular_estado_factura(con, fid) for fid in sorted(revisar, reverse=True)]):
                break
        # La incidencia de comunicación de la copia se cierra si ya no queda nada sin respuesta definitiva (p. ej. el
        # diario trae la respuesta o la conciliación posterior a la copia): las facturas nuevas no llevan Incidencia=S.
        from .remision import cerrar_incidencia_resuelta
        cerrar_incidencia_resuelta(con)
        db.actividad(con, "copia.diario_reproducido", detalle={"facturas": insertadas_f, "registros": insertados_r,
                                                              "estados": estados, "filas_contexto": contexto,
                                                              "contadores_avanzados": contadores_n,
                                                              "cadenas_aeat": cadenas_n})
    return {"facturas_recuperadas": insertadas_f, "registros_recuperados": insertados_r, "estados_aplicados": estados,
            "filas_contexto_recuperadas": contexto, "entradas_descartadas": descartadas, "certificados": certs,
            "contadores_avanzados": contadores_n, "cadenas_aeat": cadenas_n,
            "lineas_diario_ilegibles": list(diario.LINEAS_INVALIDAS)}


_COLS_CADENA_AEAT = ("empresa_id", "entorno", "estado", "id_emisor", "num_serie", "fecha_expedicion", "fecha_hora_huso",
                     "huella", "tipo", "registros_posteriores", "cabeza_local", "detectada_en", "evidencia", "anclada_en",
                     "anclada_por")


def _cadena_aeat_valida(d: dict) -> bool:
    """Anotación «cadena_aeat» bien formada (estado atrasada / anclada / resuelta, huella y fechas)."""
    import re
    try:
        if not (isinstance(d.get("empresa_id"), int) and not isinstance(d["empresa_id"], bool) and d["empresa_id"] >= 1
                and d.get("entorno") in ("simulador", "pruebas", "produccion")
                and isinstance(d.get("huella"), str) and re.fullmatch(r"[0-9A-F]{64}", d["huella"])):
            return False
        if d.get("estado") == "resuelta":
            return True
        return (d.get("estado") in ("atrasada", "anclada") and isinstance(d.get("num_serie"), str)
                and 1 <= len(d["num_serie"]) <= 60 and isinstance(d.get("id_emisor"), str)
                and isinstance(d.get("fecha_expedicion"), str) and re.fullmatch(r"\d{2}-\d{2}-\d{4}", d["fecha_expedicion"])
                and _ts(d.get("fecha_hora_huso")) is not None)
    except (TypeError, KeyError):
        return False


def _cadena_aeat_superada(con, empresa_id: int, entorno: str, huella: str, fecha_hora_huso: str | None) -> bool:
    """¿La cadena local ya ha pasado ese registro de la AEAT? (lo tiene, un registro se encadenó a él como ancla, o el
    último registro local es posterior)."""
    if db.fila(con, """SELECT 1 FROM registros WHERE empresa_id=? AND entorno=? AND (huella=? OR
                       (ancla_aeat IS NOT NULL AND json_valid(ancla_aeat) AND json_extract(ancla_aeat, '$.huella')=?))""",
               (empresa_id, entorno, huella, huella)):
        return True
    cab = db.fila(con, """SELECT MAX(c.ultima_fecha_hora) fh FROM cadenas c JOIN instalaciones i ON i.id=c.instalacion_id
                          WHERE c.empresa_id=? AND c.entorno=? AND i.empresa_id=c.empresa_id""", (empresa_id, entorno))
    t_local, t_aeat = _ts_huso(cab and cab["fh"]), _ts_huso(fecha_hora_huso)
    return t_local is not None and t_aeat is not None and t_local >= t_aeat


def _ts_huso(x: str | None) -> datetime | None:
    """Fecha y hora con huso (sin huso se entiende hora peninsular), comparable entre sí."""
    t = _ts(x)
    return t.replace(tzinfo=db.TZ) if t is not None and t.tzinfo is None else t


def _reproducir_cadenas_aeat(con, ultimas: dict[tuple[int, str], dict]) -> int:
    """Aplica la última anotación `cadena_aeat` de cada empresa y entorno salvo que la cadena local ya la haya superado (p.
    ej. el ancla se usó después de la copia) y quita las filas de la copia que ya no rigen. Idempotente."""
    n = 0
    for (eid, ent), d in ultimas.items():
        if not db.fila(con, "SELECT 1 FROM empresas WHERE id=?", (eid,)):
            continue
        if d["estado"] == "resuelta":
            n += con.execute("DELETE FROM cadenas_aeat WHERE empresa_id=? AND entorno=? AND huella=?",
                             (eid, ent, d["huella"])).rowcount
            continue
        if _cadena_aeat_superada(con, eid, ent, d["huella"], d["fecha_hora_huso"]):
            continue
        fila = {k: d.get(k) for k in _COLS_CADENA_AEAT}
        con.execute(f"""INSERT OR REPLACE INTO cadenas_aeat ({','.join(_COLS_CADENA_AEAT)})
                        VALUES ({','.join('?' * len(_COLS_CADENA_AEAT))})""", [fila[k] for k in _COLS_CADENA_AEAT])
        n += 1
    for f in db.filas(con, "SELECT empresa_id, entorno, huella, fecha_hora_huso FROM cadenas_aeat"):
        if _cadena_aeat_superada(con, f["empresa_id"], f["entorno"], f["huella"], f["fecha_hora_huso"]):
            con.execute("DELETE FROM cadenas_aeat WHERE empresa_id=? AND entorno=?", (f["empresa_id"], f["entorno"]))
    return n


def _con_rectificadas(con, ids: set[int]) -> set[int]:
    """Las facturas `ids` que existen y las que rectifican, directa o indirectamente (su estado depende de ellas)."""
    out: set[int] = set()
    vistas = {i for i in ids if isinstance(i, int)}
    pend = sorted(vistas)
    while pend:
        lote, pend = pend[:500], pend[500:]
        filas = db.filas(con, f"SELECT id, rectifica_factura_id FROM facturas WHERE id IN ({','.join('?' * len(lote))})", lote)
        out.update(f["id"] for f in filas)
        nuevas = {f["rectifica_factura_id"] for f in filas if f["rectifica_factura_id"] is not None} - vistas
        vistas |= nuevas
        pend += sorted(nuevas)
    return out


def _contador_valido(d: dict) -> bool:
    """Anotación «serie_contador» bien formada (enteros en rango y la instantánea de la misma serie)."""
    def entero(v, minimo, maximo):
        return isinstance(v, int) and not isinstance(v, bool) and minimo <= v <= maximo
    s_ = d.get("serie")
    return (entero(d.get("serie_id"), 1, 2 ** 63 - 1) and entero(d.get("anio"), 0, 9999)
            and entero(d.get("siguiente"), 1, 10 ** 12) and isinstance(s_, dict) and s_.get("id") == d["serie_id"]
            and entero(s_.get("empresa_id"), 1, 2 ** 63 - 1))


def reconciliar_diario(con) -> int:
    """Al arrancar: añade al diario las facturas, registros y ESTADOS (de factura y de comunicación) que falten o
    difieran de la última anotación (p. ej. caída entre la confirmación en la base de datos y la anotación), los avances
    manuales del contador que el diario no tiene (`_reconciliar_contadores`) y el estado de la cadena respecto a la AEAT
    (`_reconciliar_cadenas_aeat`). Idempotente: lo que ya consta no se vuelve a anotar."""
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
    n = _reconciliar_contadores(con, entradas) + _reconciliar_cadenas_aeat(con, entradas)
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
    from . import multiusuario   # cuentas y ajustes de la plataforma cuya versión no figure aún en el diario
    from .certificados import reconciliar_diario as reconciliar_certificados
    return n + multiusuario.reconciliar_diario(con, entradas) + reconciliar_certificados(con, entradas)


def _reconciliar_contadores(con, entradas: list[dict]) -> int:
    """Avances manuales del contador (`serie_contador`) que el diario no tiene (p. ej. el diario se perdió o se sustituyó y
    se ha rehecho desde la base de datos): se anota, con la instantánea de la serie, cada contador que va por delante
    tanto del último avance anotado para esa serie y año como del mayor número expedido + 1 (que la restauración ya
    recupera de las facturas). Así una restauración posterior no lo deshace. Idempotente: una vez anotado, ya no va por
    delante."""
    cubierto: dict[tuple[int, int], int] = {}
    for e in entradas:
        d = e.get("datos") or {}
        if e.get("tipo") == "serie_contador" and _contador_valido(d):
            k = (d["serie_id"], d["anio"])
            cubierto[k] = max(cubierto.get(k, 0), d["siguiente"])
        elif e.get("tipo") == "factura":
            try:
                num = json.loads(d.get("datos") or "{}").get("numeracion") or {}
            except (TypeError, ValueError):
                continue
            if isinstance(num.get("serie_id"), int) and isinstance(num.get("numero"), int):
                k = (num["serie_id"], num.get("anio") or 0)
                cubierto[k] = max(cubierto.get(k, 0), num["numero"] + 1)
    for f in db.filas(con, """SELECT serie_id, COALESCE(CASE WHEN json_valid(datos) THEN json_extract(datos, '$.numeracion.anio')
                              END, 0) anio, MAX(numero) n FROM facturas WHERE serie_id IS NOT NULL AND numero IS NOT NULL
                              GROUP BY 1, 2"""):
        k = (f["serie_id"], f["anio"] or 0)
        cubierto[k] = max(cubierto.get(k, 0), f["n"] + 1)   # se anotan como facturas en esta misma conciliación
    n = 0
    for c in db.filas(con, "SELECT serie_id, anio, siguiente FROM series_contadores ORDER BY serie_id, anio"):
        previo = cubierto.get((c["serie_id"], c["anio"]))
        if previo is not None and c["siguiente"] <= previo:
            continue
        serie = db.fila(con, "SELECT * FROM series WHERE id=?", (c["serie_id"],))
        if not serie or previo is None and c["siguiente"] <= _primer_numero(serie, c["anio"]):
            continue   # sin serie, o el contador está en su valor inicial (nada que conservar)
        diario.anotar("serie_contador", {"serie_id": c["serie_id"], "empresa_id": serie["empresa_id"], "anio": c["anio"],
                                         "anterior": previo, "siguiente": c["siguiente"], "serie": serie,
                                         "origen": "conciliacion_arranque"})
        n += 1
    return n


def _primer_numero(serie: dict, anio: int) -> int:
    """Número con el que empieza el contador de ese año sin ningún avance (series.siguiente sin fila de contador)."""
    anio_conf = int(serie["confirmada_en"][:4]) if serie.get("confirmada_en") else None
    return serie["siguiente_numero"] if (anio == 0 or anio == anio_conf) else 1


def _reconciliar_cadenas_aeat(con, entradas: list[dict]) -> int:
    """Estado de la cadena respecto a la AEAT (cadenas_aeat) que no coincide con su última anotación en el diario."""
    ultimas = {}
    for e in entradas:
        d = e.get("datos") or {}
        if e.get("tipo") == "cadena_aeat" and _cadena_aeat_valida(d):
            ultimas[(d["empresa_id"], d["entorno"])] = (d.get("estado"), d.get("huella"))
    n = 0
    if not db.fila(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cadenas_aeat'"):
        return 0   # base anterior a la migración 0007 (p. ej. la base viva al restaurar antes de arrancar la versión nueva)
    for f in db.filas(con, "SELECT * FROM cadenas_aeat"):
        if ultimas.get((f["empresa_id"], f["entorno"])) != (f["estado"], f["huella"]):
            diario.anotar("cadena_aeat", f)
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
