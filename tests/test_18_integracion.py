"""Regresiones de la revisión de la integración de los carriles G1–G4 (lo que cada carril superaba por separado y falló al
juntarlos). Todo con el simulador local y datos ficticios."""
import json
import re
import shutil
from pathlib import Path

import pytest
import test_16_s_series_consulta as S
import test_18_g2_cadena as G2
from conftest import Cliente
from test_16_u_multiusuario import NIF_D, abrir_registro, instalar, nueva_empresa, otro, registrar  # noqa: F401

from app import copias, db, diario, simulador
from app.config import ajustes

APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"


def _desastre_sin_diario(c: Cliente, eid: int, aa: str, almacen_roto: bool) -> Path:
    """001 expedida y aceptada; copia; 002 y 003 expedidas y aceptadas; se pierden la base y el diario (y, si se pide, el
    almacén queda sustituido por un fichero para que la restauración termine en RestauracionIncompleta)."""
    G2._expedir(c, eid, f"001/A{aa}")
    assert S._enviar(eid)["aceptado"] == 1
    con = db.conectar()
    copia = copias.crear_copia(con)
    con.close()
    G2._expedir(c, eid, f"002/A{aa}")
    G2._expedir(c, eid, f"003/A{aa}")
    assert S._enviar(eid)["aceptado"] == 2
    a = ajustes()
    for suf in ("", "-wal", "-shm"):
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    shutil.rmtree(diario.directorio())
    if almacen_roto:
        shutil.rmtree(a.almacen_dir)
        a.almacen_dir.write_bytes(b"no es una carpeta")
    return copia


def _avisos_en_actividad(empresa_id=None) -> list[dict]:
    con = db.conectar()
    try:
        cond = "empresa_id IS NULL" if empresa_id is None else "empresa_id=?"
        return db.filas(con, f"SELECT detalle FROM actividad WHERE accion='copia.restaurada_sin_diario' AND {cond}",
                        () if empresa_id is None else (empresa_id,))
    finally:
        con.close()


def test_restauracion_sin_diario_incompleta_y_repetida_mantiene_el_aviso(app_cliente):
    """G2 (aviso «sin diario») × G3 (RestauracionIncompleta: «repita la restauración con la misma copia»). Antes, el
    primer intento no lo decía y el segundo devolvía avisos=[]: la conciliación del principio había rellenado el diario con
    la base ya restaurada, y nada impedía expedir de nuevo 002 encadenado a 001 (cadena bifurcada respecto a la AEAT)."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    copia = _desastre_sin_diario(c, eid, aa, almacen_roto=True)
    with pytest.raises(copias.RestauracionIncompleta) as e:
        copias.restaurar(copia)
    assert copias.AVISO_SIN_DIARIO in str(e.value)
    ajustes().almacen_dir.unlink()
    r = copias.restaurar(copia)
    assert diario.leer(), "la repetición concilia la base restaurada con el diario"
    assert r["avisos"] == [copias.AVISO_SIN_DIARIO]
    # Queda a la vista en la actividad de la plataforma y en la de la empresa
    assert _avisos_en_actividad() and _avisos_en_actividad(eid)
    assert json.loads(_avisos_en_actividad(eid)[-1]["detalle"])["aviso"] == copias.AVISO_SIN_DIARIO


def test_el_aviso_sin_diario_se_mantiene_hasta_que_se_consulta_o_se_expide(app_cliente):
    """Una restauración posterior (ya con el diario rellenado) sigue avisando mientras la empresa no haya consultado ni
    expedido nada desde la restauración sin diario; después de la consulta (el procedimiento del aviso) deja de arrastrarse
    (también `test_18_g2_cadena.py::test_fis1_…`, que restaura tras consultar y reanudar sin expedir)."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    copia = _desastre_sin_diario(c, eid, aa, almacen_roto=False)
    assert copias.restaurar(copia)["avisos"] == [copias.AVISO_SIN_DIARIO]
    assert copias.restaurar(copia)["avisos"] == [copias.AVISO_SIN_DIARIO]
    # Se consulta, se reanuda la cadena, se avanza el contador (el procedimiento del aviso) y se expide: ya no se arrastra
    from fastapi.testclient import TestClient
    from app.main import crear_app
    with TestClient(crear_app()) as tc:
        n = Cliente(tc)
        G2._entrar(n)
        ca = S._consultar(n, eid)["cadena_atrasada"]
        assert ca and ca["num_serie"] == f"003/A{aa}"
        assert eid not in diario.pendientes_sin_diario(diario.leer())   # ya ha consultado: el aviso no se arrastra
        n.ok(n.post(f"/api/empresas/{eid}/consulta/reanudar-cadena", {"entorno": "simulador", "huella": ca["huella"], "confirmo": True}))
        sid = n.ok(n.get(f"/api/empresas/{eid}/series"))[0]["id"]
        anio = db.ahora().year
        prev = n.ok(n.get(f"/api/series/{sid}/avance?anio={anio}&siguiente=4"))
        n.ok(n.post(f"/api/series/{sid}/avanzar", {"anio": anio, "actual": prev["actual"], "siguiente": 4, "confirmo": True}))
        G2._expedir(n, eid, f"004/A{aa}")
    con = db.conectar()
    copia2 = copias.crear_copia(con)
    con.close()
    assert copias.restaurar(copia2)["avisos"] == []


def test_operador_fija_el_modo_del_simulador_solo_por_nif(app_cliente):
    """G3 × G1: el operador no puede recorrer identificadores internos para conocer el NIF de empresas de las que no es
    miembro, y busca el NIF igual que en la verificación del NIF (las dos formas del carácter de control)."""
    op = app_cliente
    instalar(op)
    abrir_registro(op)
    b = otro(op)
    registrar(b, "b@ejemplo.test")
    eb = b.ok(nueva_empresa(b, "G00765438", "ASOCIACIÓN FICTICIA", "C/ Inventada, 9 - 00000 Villaficticia"))["id"]
    for i in range(1, 6):
        r = op.post("/api/simulador/modo", {"empresa_id": i, "modo": "normal"})
        assert r.status_code == 422 and "G00765438" not in r.text
    r = op.ok(op.post("/api/simulador/modo", {"nif": "G0076543H", "modo": "caido", "veces": 1}))   # forma con letra
    assert "empresa_id" not in r and r["modo"] == "caido"
    assert simulador.modo_actual("G00765438")["modo"] == "caido"
    # quien no es el operador nunca usa esta ruta (ni para su propia empresa): la suya es la de la empresa
    assert b.post("/api/simulador/modo", {"nif": "G00765438", "modo": "normal"}).status_code == 403
    assert b.ok(b.post(f"/api/empresas/{eb}/simulador/modo", {"modo": "normal"}))["modo"] == "normal"


def test_la_interfaz_fija_el_modo_con_la_ruta_de_la_empresa():
    """Las tarjetas del simulador de Remisión y Consulta usan /empresas/{eid}/simulador/modo (aislamiento, aviso de
    acceso perdido y errores anotados en la empresa); la ruta del panel solo se usa con el NIF que escribe el operador."""
    js = APP_JS.read_text(encoding="utf-8")
    usos = [m.start() for m in re.finditer(r"api\('/simulador/modo'", js)]
    assert len(usos) == 1
    assert "nif: nif.value" in js[usos[0]:usos[0] + 200]
    assert len(re.findall(r"api\(`/empresas/\$\{enc\(eid\)\}/simulador/modo`", js)) == 2
    assert "empresa_id: Number(eid)" not in js


def test_el_aviso_sin_diario_sobrevive_a_perder_otra_vez_la_base(app_cliente):
    """Tras la restauración sin diario, la aplicación arranca (la conciliación rellena el diario con la base restaurada) y
    se vuelve a perder la base: la nueva restauración sigue avisando, porque las empresas pendientes están en el diario."""
    from fastapi.testclient import TestClient
    from app.main import crear_app
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    copia = _desastre_sin_diario(c, eid, aa, almacen_roto=False)
    assert copias.restaurar(copia)["avisos"] == [copias.AVISO_SIN_DIARIO]
    with TestClient(crear_app()):
        pass                                                        # arranque: concilia y rellena el diario
    assert len(diario.leer()) > 1 and eid in diario.pendientes_sin_diario(diario.leer())
    a = ajustes()
    for suf in ("", "-wal", "-shm"):
        Path(str(a.db_path) + suf).unlink(missing_ok=True)          # se pierde otra vez la base (no el diario)
    r = copias.restaurar(copia)
    assert r["avisos"] == [copias.AVISO_SIN_DIARIO]
    assert _avisos_en_actividad(eid)


def test_el_aviso_sin_diario_llega_a_la_empresa_que_aun_no_tenia_registros(app_cliente):
    """La empresa que en la copia aún no había expedido, y expidió después, es justo la que más necesita el aviso."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    con = db.conectar()
    copia = copias.crear_copia(con)                                 # copia sin ningún registro
    con.close()
    G2._expedir(c, eid, f"001/A{aa}")
    G2._expedir(c, eid, f"002/A{aa}")
    assert S._enviar(eid)["aceptado"] == 2
    a = ajustes()
    for suf in ("", "-wal", "-shm"):
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    shutil.rmtree(diario.directorio())
    assert copias.restaurar(copia)["avisos"] == [copias.AVISO_SIN_DIARIO]
    assert _avisos_en_actividad(eid)
    assert diario.pendientes_sin_diario(diario.leer()) == {eid: 0}


@pytest.mark.skipif(shutil.which("node") is None, reason="sin Node.js")
def test_detalle_de_actividad_en_palabras_y_en_hora_peninsular(tmp_path):
    """G1 × G4: el minuto de un error (hora peninsular sin huso) no se desplaza con la zona del navegador. G3 × G4: el
    modo de una consulta del simulador con su texto. G2 × G4: «la AEAT» solo cuando lo es. Y las etiquetas de las
    anotaciones de las copias."""
    import os
    import subprocess
    arnes = tmp_path / "detalle.js"
    arnes.write_text("""const fs = require('fs'); const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const stub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : stub()), apply: () => stub(), construct: () => stub() });
const ctx = { console, document: stub(), window: stub(), location: stub(), navigator: stub(), localStorage: stub(), sessionStorage: stub(),
  fetch: () => new Promise(() => {}), setTimeout: () => 0, clearTimeout: () => 0, setInterval: () => 0, clearInterval: () => 0,
  addEventListener: () => {}, history: stub(), Intl, URL, URLSearchParams, matchMedia: stub() };
vm.createContext(ctx);
vm.runInContext(src + '\\n;globalThis.__d = detalleActividad;', ctx);
for (const d of JSON.parse(process.argv[3])) console.log(ctx.__d(JSON.stringify(d)));
""", encoding="utf-8")
    casos = [
        {"minuto": "2026-11-12T03:54", "ruta": "/api/x", "veces": 3},
        {"modo": "respuesta_invalida", "servicio": "consulta", "veces": 1},
        {"modo": "rechazo", "servicio": "consulta", "veces": 1},
        {"entorno": "simulador", "resumen": {"coinciden": 1, "solo_aeat": 2},
         "ultimo_registro_aeat": {"num_serie": "003/A26", "fecha_expedicion": "12-11-2026", "estado_aeat": "Correcto"}},
        {"entorno": "produccion", "resumen": {"solo_aeat": 1}},
        {"copia": "copia_20261112T035440", "empresas_pendientes": 2},
        {"facturas": 1, "cadenas_aeat": 1},
    ]
    for zona in ("Atlantic/Canary", "UTC", "Europe/Madrid"):
        out = subprocess.run(["node", str(arnes), str(APP_JS), json.dumps(casos)], capture_output=True, text=True,
                             timeout=60, env=dict(os.environ, TZ=zona), check=True).stdout.splitlines()
        assert out[0].startswith("Minuto: 12/11/26, 3:54"), (zona, out[0])
        assert out[1].startswith("Modo: Respuesta fuera de esquema") and out[2].startswith("Modo: Rechazo de la consulta")
        assert "solo en el simulador local 2" in out[3] and "Último registro en el simulador local" in out[3]
        assert "Fecha de expedición: 12/11/2026" in out[3] and "AEAT" not in out[3]
        assert "solo en la AEAT 1" in out[4]
        assert out[5].startswith("Copia: copia del 12/11/26, 3:54")
        assert "Estados de la cadena recuperados: 1" in out[6]
        assert not any(re.search(r"\b[a-z]+_[a-z_]+\b", x) for x in out), out


def test_solo_cuenta_la_consulta_que_puede_detectar_lo_perdido(app_cliente):
    """Una consulta de un solo número o de un mes anterior a la copia no retira el aviso (no puede ver lo que se perdió); la
    del mes de la copia, completa y fiable, sí. Cada consulta útil queda en el diario con su mes y su entorno."""
    from fastapi.testclient import TestClient
    from app.main import crear_app
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    copia = _desastre_sin_diario(c, eid, aa, almacen_roto=False)
    copias.restaurar(copia)
    marca = [e for e in diario.leer() if e["tipo"] == "restauracion_sin_diario"][-1]["datos"]
    hoy = db.ahora()
    assert marca["meses"] == [f"{hoy.year}-{hoy.month:02d}"]
    assert marca["empresas"][str(eid)]["entornos"] == ["simulador"]
    with TestClient(crear_app()) as tc:
        n = Cliente(tc)
        G2._entrar(n)
        S._consultar(n, eid, num_serie=f"001/A{aa}")                  # un solo número
        assert eid in diario.pendientes_actuales()
        anterior = f"{hoy.month - 1:02d}" if hoy.month > 1 else None
        if anterior:
            S._consultar(n, eid, periodo=anterior)                      # un mes anterior a la copia
            assert eid in diario.pendientes_actuales()
        S._consultar(n, eid)                                            # el mes de la copia, completo
        assert eid not in diario.pendientes_actuales()
    assert [e["datos"] for e in diario.leer() if e["tipo"] == "consulta_aeat"][-1] == {
        "empresa_id": eid, "entorno": "simulador", "mes": f"{hoy.year}-{hoy.month:02d}"}


def test_un_fallo_al_anotar_el_aviso_no_deja_restos(app_cliente, monkeypatch):
    """Si el diario no admite la anotación (disco lleno), la restauración no cambia nada y no deja la base temporal."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    copia = _desastre_sin_diario(c, eid, aa, almacen_roto=False)
    real = diario.anotar

    def lleno(tipo, datos):
        if tipo == "restauracion_sin_diario":
            raise OSError(28, "No space left on device")
        return real(tipo, datos)
    monkeypatch.setattr(diario, "anotar", lleno)
    with pytest.raises(OSError):
        copias.restaurar(copia)
    a = ajustes()
    assert not a.db_path.exists() and not list(a.db_path.parent.glob("restauracion_*"))
    monkeypatch.setattr(diario, "anotar", real)
    assert copias.restaurar(copia)["avisos"] == [copias.AVISO_SIN_DIARIO]


def test_la_empresa_creada_despues_no_hereda_el_aviso(app_cliente):
    """Una empresa dada de alta después de la restauración sin diario tiene todo en el diario: una restauración posterior
    no la marca como pendiente aunque otra empresa siga pendiente."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    copia = _desastre_sin_diario(c, eid, aa, almacen_roto=False)
    copias.restaurar(copia)
    from fastapi.testclient import TestClient
    from app.main import crear_app
    with TestClient(crear_app()) as tc:
        n = Cliente(tc)
        G2._entrar(n)
        nueva = n.ok(nueva_empresa(n, NIF_D, "EMPRESA NUEVA FICTICIA S.L.", "C/ Inventada, 7 - 00000 Villaficticia"))["id"]
    a = ajustes()
    for suf in ("", "-wal", "-shm"):
        Path(str(a.db_path) + suf).unlink(missing_ok=True)
    r = copias.restaurar(copia)
    assert r["avisos"] == [copias.AVISO_SIN_DIARIO]                    # la primera sigue pendiente
    pend = diario.pendientes_sin_diario(diario.leer())
    assert eid in pend and nueva not in pend


def test_consultar_no_lee_el_diario_entero_si_nada_esta_pendiente(app_cliente, monkeypatch):
    """Sin ninguna restauración sin diario, pendientes_actuales responde sin descodificar el diario (que puede ser grande);
    con ella, lee solo desde la anotación y da lo mismo que leerlo entero."""
    c = app_cliente
    eid = S._instalar(c)
    aa = db.ahora().strftime("%y")
    G2._expedir(c, eid, f"001/A{aa}")
    llamadas = []
    real = diario._leer_lineas
    monkeypatch.setattr(diario, "_leer_lineas", lambda ls: llamadas.append(1) or real(ls))
    assert diario.pendientes_actuales() == {} and not llamadas
    diario.anotar("restauracion_sin_diario", {"copia": "copia_x", "meses": ["2026-11"],
                                              "empresas": {str(eid): {"ultimo": 99, "entornos": ["simulador"]}}})
    G2._expedir(c, eid, f"002/A{aa}")                                   # registro con id ≤ 99: no la retira
    assert diario.pendientes_actuales() == diario.pendientes_sin_diario(diario.leer()) == {eid: 99}
    assert llamadas
