"""Regresiones de RES-4, RES-5 y RES-7 (revisión integrada final): copias de seguridad y restauración.

- RES-4: la restauración crece en línea con lo reproducido: índices registros(factura_id, secuencia) y
  facturas(rectifica_factura_id) (migración 0008) y recálculo del estado solo de las facturas que toca el diario y de las
  que estas rectifican.
- RES-5: antes de restaurar se comprueba el espacio libre; un fallo al apartar la base actual (disco lleno) no deja
  temporales ni una copia apartada a medias; un fallo después de colocar la base restaurada lo dice con un error explícito
  y basta con repetir; el almacén de la copia se fusiona leyendo el tar fichero a fichero (sin descomprimirlo entero).
- RES-7: carpeta de datos, base de datos, copias y copias apartadas solo para el usuario del servicio (0700/0600), y al
  arrancar se ajustan los ficheros creados por versiones anteriores.

Todos los datos son ficticios. Solo se usa el simulador local.
"""
import errno
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tarfile
import types

import pytest
from conftest import preparar_empresa
from test_03_flujo import emitir, enviar, preparar_desde_bytes, variante_pdf

from app import config, copias, db, diario, emision


def _modo(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def _huella(p) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _restos(data_dir):
    return sorted(p.name for p in data_dir.iterdir() if p.name.startswith(("restauracion_", "verifactu.antes_de_restaurar_")))


@pytest.fixture()
def umask_022():
    antes = os.umask(0o022)
    yield
    os.umask(antes)


@pytest.fixture()
def con_copia(app_cliente):
    """Empresa con una factura aceptada en el simulador y una copia; después de la copia, otra factura."""
    c = app_cliente
    eid = preparar_empresa(c)
    con = db.conectar()
    c.ok(emitir(c, preparar_desde_bytes(c, eid, "f166.pdf", variante_pdf("166/26")), "r-1"))
    enviar(eid)
    copia = copias.crear_copia(con)
    f2 = c.ok(emitir(c, preparar_desde_bytes(c, eid, "f167.pdf", variante_pdf("167/26")), "r-2"))["factura_id"]
    enviar(eid)
    con.close()
    return c, eid, copia, f2


# --- RES-4 ---------------------------------------------------------------------------------------------------------------
def test_res4_indices_para_las_consultas_de_la_restauracion(app_cliente):
    con = db.conectar()
    nombres = {r[1] for r in con.execute("PRAGMA index_list(registros)")} | {r[1] for r in con.execute("PRAGMA index_list(facturas)")}
    assert {"registros_factura", "facturas_rectifica"} <= nombres
    planes = [" ".join(str(x[3]) for x in con.execute("EXPLAIN QUERY PLAN " + q, (1,))) for q in (
        "SELECT tipo, estado_envio FROM registros WHERE factura_id=? ORDER BY secuencia DESC LIMIT 1",
        "SELECT 1 FROM facturas WHERE rectifica_factura_id=? AND estado<>'anulada'")]
    assert all("USING INDEX" in p or "USING COVERING INDEX" in p for p in planes), planes


def _rectificar(c, eid, fid):
    rb = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R1", "motivo": "Error en la base imponible"}))["borrador_id"]
    anio = db.ahora().strftime("%y")
    cambios = [{"campo": "num_serie", "valor": f"R1/{anio}", "motivo": "n"},
               {"campo": "desglose.0.base", "valor": "-100.00", "motivo": "d"},
               {"campo": "desglose.0.cuota", "valor": "-21.00", "motivo": "d"},
               {"campo": "base_imponible", "valor": "-100.00", "motivo": "d"}, {"campo": "cuota_total", "valor": "-21.00", "motivo": "d"},
               {"campo": "importe_total", "valor": "-121.00", "motivo": "d"},
               {"campo": "total_a_pagar", "valor": "-121.00", "motivo": "d"}]
    assert c.ok(c.patch(f"/api/borradores/{rb}", {"cambios": cambios}))["puede_emitir"]
    return c.ok(emitir(c, rb, "rect-1"))["factura_id"]


def test_res4_solo_se_recalculan_las_facturas_que_toca_el_diario(app_cliente, monkeypatch):
    """Tras la copia se anula una factura y se rectifica otra (y se pierde la anotación del nuevo estado de la rectificada):
    solo se recalculan la anulada, la rectificativa y la que esta rectifica; el resto viene de la copia, que es coherente."""
    c = app_cliente
    eid = preparar_empresa(c)
    s = c.ok(c.post(f"/api/empresas/{eid}/series", {"entorno": "simulador", "codigo": "R", "patron": "R{n}/{aa}",
                                                    "tipo": "rectificativa", "siguiente_numero": 1}))
    c.ok(c.post(f"/api/series/{s['id']}/confirmar"))
    con = db.conectar()
    fids = [c.ok(emitir(c, preparar_desde_bytes(c, eid, f"f{n}.pdf", variante_pdf(f"{n}/26")), f"k-{n}"))["factura_id"]
            for n in range(166, 170)]
    enviar(eid)
    copia = copias.crear_copia(con)
    c.ok(c.post(f"/api/facturas/{fids[1]}/anular", {"motivo": "Expedida por error", "confirmo": True}))
    rf = _rectificar(c, eid, fids[2])
    enviar(eid)
    con.close()
    # anotación perdida: el nuevo estado («rectificada») de la factura rectificada no llegó al diario
    ruta = diario.ruta()
    lineas = [x for x in ruta.read_text(encoding="utf-8").splitlines()
              if not (json.loads(x)["tipo"] == "estado_factura" and json.loads(x)["datos"]["id"] == fids[2])]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    vistas = []
    original = emision.recalcular_estado_factura
    monkeypatch.setattr(emision, "recalcular_estado_factura", lambda con, fid: vistas.append(fid) or original(con, fid))
    r = copias.restaurar(copia)
    assert r["facturas_recuperadas"] == 1
    con = db.conectar()
    assert {x["id"]: x["estado"] for x in db.filas(con, "SELECT id, estado FROM facturas")} == {
        fids[0]: "emitida", fids[1]: "anulada", fids[2]: "rectificada", fids[3]: "emitida", rf: "emitida"}
    assert set(vistas) == {fids[1], fids[2], rf}, vistas


# --- RES-5 ---------------------------------------------------------------------------------------------------------------
def test_res5_sin_espacio_no_empieza(con_copia, monkeypatch):
    c, eid, copia, _ = con_copia
    a = config.ajustes()
    antes = (os.stat(a.db_path).st_ino, _huella(a.db_path))
    falso = shutil._ntuple_diskusage(10 ** 9, 10 ** 9 - 1024, 1024)
    monkeypatch.setattr(copias.shutil, "disk_usage", lambda p: falso)
    with pytest.raises(RuntimeError) as e:
        copias.restaurar(copia)
    assert "espacio libre suficiente" in str(e.value) and "no se ha cambiado nada" in str(e.value)
    assert (os.stat(a.db_path).st_ino, _huella(a.db_path)) == antes and _restos(a.data_dir) == []
    monkeypatch.undo()
    necesario = copias.espacio_necesario(copia)
    assert necesario and all(x["necesario"] > os.path.getsize(copia / "verifactu.sqlite3") for x in necesario)


def _shim(modulo, **cambios):
    s = types.ModuleType(modulo.__name__ + "_prueba")
    s.__dict__.update({k: getattr(modulo, k) for k in dir(modulo) if not k.startswith("__")})
    s.__dict__.update(cambios)
    return s


def _lleno(msg="database or disk is full", codigo=sqlite3.SQLITE_FULL, clase=sqlite3.OperationalError):
    e = clase(msg)
    e.sqlite_errorcode = codigo
    return e


def _apartado_falla(monkeypatch, a, error_backup, copia_falla):
    """La copia de la base actual (apartarla) falla: `error_backup` al hacer el backup y, si se intenta copiar el fichero
    tal cual, escribe una parte y se queda sin espacio."""
    real = sqlite3.connect

    class Viva:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, k):
            return getattr(self._c, k)

        def backup(self, *a_, **k_):
            raise error_backup

    def connect(ruta, *a_, **k_):
        c = real(ruta, *a_, **k_)
        return Viva(c) if str(ruta) == str(a.db_path) else c

    def sin_espacio_copy2(src, dst, *a_, **k_):
        if "antes_de_restaurar" in str(dst) and copia_falla:
            with open(dst, "wb") as f:
                f.write(b"x" * 4096)
            raise OSError(errno.ENOSPC, "No space left on device")
        return shutil.copy2(src, dst, *a_, **k_)
    escritos = {"n": 0}

    def sin_espacio_copyfileobj(fsrc, fdst, *a_, **k_):
        if copia_falla and escritos["apartando"]:
            fdst.write(b"x" * 4096)
            raise OSError(errno.ENOSPC, "No space left on device")
        return shutil.copyfileobj(fsrc, fdst, *a_, **k_)
    escritos["apartando"] = False
    real_fichero = getattr(copias, "_fichero_privado", None)
    if real_fichero:
        def marcar(p):
            escritos["apartando"] = "antes_de_restaurar" in str(p)
            return real_fichero(p)
        monkeypatch.setattr(copias, "_fichero_privado", marcar)
    monkeypatch.setattr(copias, "sqlite3", _shim(sqlite3, connect=connect))
    monkeypatch.setattr(copias, "shutil", _shim(shutil, copy2=sin_espacio_copy2, copyfileobj=sin_espacio_copyfileobj))


@pytest.mark.parametrize("error_backup, copia_falla", [
    (_lleno(), False),                                                                  # disco lleno al hacer la copia
    (_lleno("file is not a database", sqlite3.SQLITE_NOTADB, sqlite3.DatabaseError), True),   # base dañada + disco lleno
], ids=["backup_lleno", "danada_y_copia_llena"])
def test_res5_disco_lleno_al_apartar_no_deja_restos(con_copia, monkeypatch, error_backup, copia_falla):
    c, eid, copia, f2 = con_copia
    a = config.ajustes()
    antes = (os.stat(a.db_path).st_ino, _huella(a.db_path))
    _apartado_falla(monkeypatch, a, error_backup, copia_falla)
    with pytest.raises((OSError, sqlite3.Error)):
        copias.restaurar(copia)
    monkeypatch.undo()
    assert _restos(a.data_dir) == []                       # ni la base temporal ni una copia apartada a medias
    assert (os.stat(a.db_path).st_ino, _huella(a.db_path)) == antes
    copias.comprobar_arranque()
    r = copias.restaurar(copia)                             # con espacio, se repite y termina
    assert r["facturas_recuperadas"] == 1 and r["pdf_irrecuperables"] == []


def test_res5_fallo_tras_colocar_la_base_lo_explica_y_se_repite(con_copia):
    """Desastre: el almacén se ha perdido y en su lugar hay un fichero; la base restaurada ya está colocada cuando falla
    el almacén: error explícito (qué está hecho y qué repetir). Corregida la causa, repetir la restauración lo completa."""
    c, eid, copia, f2 = con_copia
    a = config.ajustes()
    shutil.rmtree(a.almacen_dir)
    a.almacen_dir.write_bytes(b"no es una carpeta")
    with pytest.raises(RuntimeError) as e:
        copias.restaurar(copia)
    msg = str(e.value)
    assert "base de datos restaurada ya está colocada" in msg and "antes_de_restaurar" in msg
    assert "repita la restauración con la misma copia" in msg
    assert type(e.value).__name__ == "RestauracionIncompleta"
    a.almacen_dir.unlink()
    r = copias.restaurar(copia)
    assert r["ficheros_almacen_recuperados"] > 0 and r["pdf_irrecuperables"] == []
    con = db.conectar()
    assert db.fila(con, "SELECT COUNT(*) n FROM facturas")["n"] == 2


def test_res5_almacen_fusionado_sin_descomprimir_la_copia(con_copia, monkeypatch):
    """Solo los ficheros que faltan, leídos uno a uno del tar: nunca se extrae la copia entera en la carpeta de datos (ni
    se sobrescribe un fichero que ya está)."""
    c, eid, copia, f2 = con_copia
    a = config.ajustes()
    pdfs = sorted(p for p in a.almacen_dir.rglob("*.pdf") if p.is_file())
    perdido, conservado = pdfs[0], pdfs[-1]
    perdido.unlink()
    ino = os.stat(conservado).st_ino

    def prohibido(*a_, **k_):
        raise AssertionError("se ha descomprimido la copia entera")
    monkeypatch.setattr(tarfile.TarFile, "extractall", prohibido)
    r = copias.restaurar(copia)
    assert perdido.is_file() and r["ficheros_almacen_recuperados"] >= 1
    assert os.stat(conservado).st_ino == ino
    assert not [p for p in a.data_dir.iterdir() if p.is_dir() and p.name.startswith("tmp")]
    assert not list(a.almacen_dir.rglob("*.tmp"))


# --- RES-7 ---------------------------------------------------------------------------------------------------------------
def test_res7_base_copias_y_apartadas_solo_para_el_servicio(umask_022, con_copia):
    c, eid, copia, f2 = con_copia
    a = config.ajustes()
    assert _modo(a.data_dir) == 0o700
    con = db.conectar()
    con.execute("SELECT COUNT(*) FROM facturas").fetchone()   # abre el WAL y su índice (-shm)
    assert {s_: _modo(str(a.db_path) + s_) for s_ in ("", "-wal", "-shm")} == {"": 0o600, "-wal": 0o600, "-shm": 0o600}
    con.close()
    assert _modo(a.data_dir / "simulador_aeat.sqlite3") == 0o600
    assert _modo(a.copias_dir) == 0o700 and _modo(copia) == 0o700
    assert {p.name: _modo(p) for p in copia.iterdir()} == {"verifactu.sqlite3": 0o600, "almacen.tar.gz": 0o600,
                                                           "manifiesto.json": 0o600}
    r = copias.restaurar(copia)
    assert _modo(r["base_anterior_apartada"]) == 0o600
    assert _modo(a.db_path) == 0o600


def test_res7_al_arrancar_se_ajustan_los_permisos_antiguos(umask_022, con_copia):
    """Ficheros de una versión anterior (0644 / 0755): al arrancar quedan 0600 / 0700."""
    c, eid, copia, f2 = con_copia
    a = config.ajustes()
    antiguos = [a.data_dir, a.db_path, a.copias_dir, copia, copia / "verifactu.sqlite3", copia / "almacen.tar.gz"]
    for p in antiguos:
        os.chmod(p, 0o755 if os.path.isdir(p) else 0o644)
    from fastapi.testclient import TestClient
    from app.main import crear_app
    with TestClient(crear_app()):
        pass
    assert {str(p): _modo(p) for p in antiguos} == {str(p): (0o700 if os.path.isdir(p) else 0o600) for p in antiguos}
