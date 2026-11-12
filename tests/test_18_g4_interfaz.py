"""Interfaz (carril G4): textos y comportamiento de la SPA — hallazgos UI-2, UI-3, UI-4, UI-5 y UI-7 (y G4-V1 a G4-V3 de
la verificación de la primera reparación).

Todo es LOCAL y SIMULADO: datos ficticios (NIF de provincia 00, correos @ejemplo.test) y solo el simulador local de la
AEAT. Las pruebas de navegador arrancan la aplicación en 127.0.0.1 (puerto libre aleatorio, datos temporales, sin hilo
de remisión: los envíos al simulador los hace la prueba) y la recorren con Playwright (Chromium, zona horaria UTC para
comprobar que las fechas se muestran en hora peninsular). Se omiten si no hay Node.js con Playwright.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests
from conftest import Cliente, borrador_listo, configurar, pdf, preparar_empresa, subir
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import config, db, multiusuario, remision
from app.nif import completar_cif

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
import generar_pdfs_prueba as gen  # noqa: E402

CLAVE = "clave-segura-123"
CLAVE_CERT = "Clave-Del-Certificado-Prueba-9"
NIF_A = completar_cif("B", "0011223")
NIF_B = completar_cif("B", "0044556")
EMPRESA_A = {"nif": NIF_A, "razon_social": "ALQUILERES FICTICIOS DEL NORTE S.L.", "domicilio": "C/ Inventada, 3 - 48000 Villaficticia"}
EMPRESA_B = {"nif": NIF_B, "razon_social": "TALLERES FICTICIOS DEL SUR S.L.", "domicilio": "C/ Imaginaria, 7 - 41000 Villaficticia"}
INCIDENCIA = "2026-07-01T10:05:00+00:00"          # 12:05 en hora peninsular (horario de verano)


def _reiniciar_limitadores():
    f = getattr(multiusuario, "reiniciar_limitadores", None)
    if f:
        f()


def _registrar(cli: Cliente, email: str, invitacion: str | None = None) -> Cliente:
    body = {"email": email, "nombre": email.split("@")[0].capitalize(), "clave": CLAVE}
    if invitacion:
        body["invitacion"] = invitacion
    cli.csrf = cli.ok(cli.post("/api/registro", body))["csrf"]
    return cli


# --- Servidor: fechas en hora peninsular, operador de la plataforma, declaración ------------------------------------

def test_ui5_fechas_del_servidor_en_hora_peninsular(app_cliente):
    """UI-5: el aviso de incidencia (visible en todas las pantallas) y otros textos del servidor no muestran marcas ISO ni
    «(UTC)»: la fecha va en hora peninsular, como en el resto de la interfaz."""
    c = app_cliente
    eid = preparar_empresa(c)
    _, bid, b = borrador_listo(c, eid)
    # El documento de la réplica no es de hoy: el aviso da las dos fechas como dd/mm/aaaa (antes, «2026-…» en ISO)
    fe = [i["mensaje"] for i in b["incidencias"] if i["codigo"] == "FECHA_EXPEDICION"]
    assert fe and re.search(r"fechado el \d{2}/\d{2}/\d{4}; la factura se expedirá con fecha de hoy \(\d{2}/\d{2}/\d{4}\)", fe[0]), fe
    c.ok(c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "g4-1", "confirmo": True}))
    con = db.conectar()
    with db.transaccion(con):
        con.execute("INSERT OR IGNORE INTO control_flujo (empresa_id, entorno) VALUES (?, 'simulador')", (eid,))
        con.execute("UPDATE control_flujo SET incidencia_desde=? WHERE empresa_id=? AND entorno='simulador'", (INCIDENCIA, eid))
    con.close()
    aviso = c.ok(c.get(f"/api/empresas/{eid}"))["avisos"][0]
    assert "desde el 01/07/2026 a las 12:05" in aviso and "UTC" not in aviso and "2026-07-01T" not in aviso, aviso
    assert "pendiente(s) de remitir o de confirmar" in aviso
    assert c.ok(c.get(f"/api/empresas/{eid}/remision?entorno=simulador"))["aviso_incidencia"] == aviso
    # Reimportar el mismo PDF: la fecha de la primera importación, legible
    dup = subir(c, eid, "replica_166-26.pdf")
    assert dup["duplicado"] and re.search(r"ya se importó el \d{2}/\d{2}/\d{4} a las \d{2}:\d{2} como", dup["mensaje"]), dup["mensaje"]
    # Formato común de las fechas que compone el servidor
    assert db.legible("2026-07-01T10:05:00+00:00") == "01/07/2026 a las 12:05"        # verano: UTC+2
    assert db.legible("2026-01-15T10:05:00+00:00") == "15/01/2026 a las 11:05"        # invierno: UTC+1
    assert db.legible("2026-12-31T23:30:00+00:00", hora=False) == "01/01/2027"        # el día es el peninsular
    assert db.legible("2026-11-11T16:52:07") == "11/11/2026 a las 16:52"              # sin desfase: hora peninsular
    assert db.legible(None) == "—" and db.legible("no es una fecha") == "no es una fecha"


def test_ui4_operador_de_la_plataforma_y_declaracion(app_cliente):
    """UI-4: el productor del sistema lo identifica el operador de la plataforma (no un administrador de empresa), y el
    borrador de la declaración responsable describe lo que hace hoy la aplicación (sin formalizar)."""
    op = app_cliente
    preparar_empresa(op)
    op.ok(op.put("/api/plataforma/ajustes", {"registro_abierto": True}))
    _reiniciar_limitadores()
    ana = _registrar(Cliente(TestClient(op.tc.app)), "ana@ejemplo.test")
    eb = ana.ok(ana.post("/api/empresas", EMPRESA_B))["id"]
    r = ana.put("/api/productor", {"nombre": "OTRA", "nif": NIF_B, "domicilio": "x"})
    assert r.status_code == 403 and "operador de la plataforma" in r.json()["detail"], r.text
    req = {x["clave"]: x for x in ana.ok(ana.get(f"/api/empresas/{eb}/produccion"))["requisitos"]}
    assert not req["productor"]["cumplido"] and "operador de la plataforma" in req["productor"]["detalle"]
    # Borrador de la declaración: apartado d) al día y sigue sin formalizar
    texto = (RAIZ / "docs" / "declaracion-responsable-borrador.md").read_text(encoding="utf-8")
    assert texto.startswith("# BORRADOR — NO FORMALIZADO") and "[PENDIENTE" in texto
    d = texto.split("\nd) ", 1)[1].split("\ne) ", 1)[0]
    for frase in ("exentas", "E1", "E2", "E3", "E6", "ClaveRegimen 02", "consulta", "concilia", "varias personas",
                  "operador de la plataforma", "R1", "R4"):
        assert frase.lower() in d.lower(), frase
    assert "servidor del usuario" not in d and "en régimen general de IVA, generando" not in d
    assert ana.ok(ana.get("/api/declaracion"))["contenido"] == texto


# --- Navegador ------------------------------------------------------------------------------------------------------

COMUN_JS = r"""
const { chromium, request } = require('playwright');
const [BASE, DIR, DATOS] = process.argv.slice(2);
const D = JSON.parse(DATOS || '{}');
const obs = {};
const problemas = [];
const paginas = [];
async function abrir(browser, viewport = { width: 1280, height: 900 }) {
  const ctx = await browser.newContext({ viewport, timezoneId: 'UTC', locale: 'es-ES' });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => problemas.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (/Content Security Policy|Refused to/.test(m.text())) problemas.push(`csp: ${m.text()}`); });
  await page.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  paginas.push(page);
  return page;
}
async function entrar(page, email, clave) {
  await page.goto(`${BASE}/#/login`);
  await page.getByLabel('Correo electrónico').fill(email);
  await page.getByLabel('Contraseña', { exact: true }).fill(clave);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.locator('#cabecera:not([hidden]) #sel-empresa').waitFor();
}
// Navegación como la de un enlace del menú: cambia el «hash», sin recargar la página
async function ir(page, ruta, listo) {
  await page.evaluate((r) => { window.location.hash = r; }, ruta);
  if (listo) await page.locator(listo).first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(400);
}
const limpio = (t) => String(t || '').replace(/\s+/g, ' ').trim();
async function texto(page, sel) { return limpio(await page.locator(sel).first().innerText()); }
async function banda(page) {
  const l = page.locator('#avisos-empresa');
  return (await l.isVisible()) ? limpio(await l.innerText()) : '';
}
async function esperar(fn, ms = 6000) {
  const fin = Date.now() + ms;
  for (;;) { const v = await fn(); if (v || Date.now() > fin) return v; await new Promise((r) => setTimeout(r, 200)); }
}
async function foco(page) {
  return page.evaluate(() => {
    const a = document.activeElement;
    return a ? `${a.tagName} ${limpio(a.textContent)}`.trim() : '';
    function limpio(t) { return String(t || '').replace(/\s+/g, ' ').trim(); }
  });
}
async function abiertos(page) { return page.locator('dialog[open]').count(); }
async function sesionApi(email, clave) {
  const r = await request.newContext({ baseURL: BASE });
  const s = await (await r.post('/api/sesion', { data: { email, clave } })).json();
  return { r, cab: { 'X-CSRF-Token': s.csrf } };
}
async function terminar(browser) {
  for (const p of paginas) {
    const v = await p.evaluate(() => window.__csp || []).catch(() => []);
    v.forEach((x) => problemas.push(`csp: ${x}`));
  }
  await browser.close();
  console.log('RESULTADO ' + JSON.stringify({ obs, problemas }));
}
"""


class Sitio:
    def __init__(self, base: Path, url: str):
        self.dir = base
        self.url = url
        self.datos: dict = {}


class _Http:
    """Cliente HTTP real contra el servidor local, con la interfaz que usa conftest.Cliente (rutas relativas)."""

    def __init__(self, url: str):
        self.url = url
        self.s = requests.Session()
        self.s.trust_env = False            # 127.0.0.1: nunca a través de un proxy

    def _pedir(self, metodo, ruta, **kw):
        return self.s.request(metodo, self.url + ruta, timeout=180, **kw)

    def get(self, ruta, **kw):
        return self._pedir("GET", ruta, **kw)

    def post(self, ruta, **kw):
        return self._pedir("POST", ruta, **kw)

    def put(self, ruta, **kw):
        return self._pedir("PUT", ruta, **kw)

    def patch(self, ruta, **kw):
        return self._pedir("PATCH", ruta, **kw)

    def delete(self, ruta, **kw):
        return self._pedir("DELETE", ruta, **kw)


def _cliente_http(url: str) -> Cliente:
    return Cliente(_Http(url))


def _p12(cn: str, empresa: dict = EMPRESA_B) -> bytes:
    """Certificado de sello ficticio (autofirmado) de la empresa: titular la entidad (persona jurídica)."""
    k = ec.generate_private_key(ec.SECP256R1())
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, empresa["razon_social"]),
                        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.97"), f"VATES-{empresa['nif']}")])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - dt.timedelta(days=30))
            .not_valid_after(ahora + dt.timedelta(days=365)).sign(k, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"x", k, cert, None, BestAvailableEncryption(CLAVE_CERT.encode()))


def _emitir_a(c: Cliente, eid: int, numero: str) -> int:
    datos = gen.dibujar(gen.demo(numero=numero, fecha=db.ahora().strftime("%d/%m/%Y"),
                                 emisor=(EMPRESA_A["razon_social"], NIF_A)))
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (f"{numero.replace('/', '-')}.pdf", datos, "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    did = c.ok(r)[0]["documento_id"]
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "motivo": "prueba"}))
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    b = c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]}))
    assert b["puede_emitir"], b["incidencias"]
    return c.ok(c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": uuid.uuid4().hex, "confirmo": True}))["factura_id"]


def _factura_a(s) -> int:
    """Una factura expedida de la empresa A (la de la primera prueba o, si se ejecuta sola, la siguiente de la serie)."""
    if not s.datos.get("factura_a"):
        con = db.conectar()
        n = db.fila(con, "SELECT COUNT(*) AS n FROM facturas WHERE empresa_id=?", (s.ea,))["n"] + 1
        con.close()
        s.datos["factura_a"] = _emitir_a(s.op, s.ea, f"{n:03d}/A{db.ahora():%y}")
    return s.datos["factura_a"]


VARIABLES = ("VERIFACTU_DATA_DIR", "VERIFACTU_DIARIO_DIR", "VERIFACTU_CLAVE_MAESTRA_FICHERO")


@pytest.fixture(scope="module")
def sitio(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("Node.js no está instalado: se omiten las pruebas de navegador")
    if subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, timeout=60).returncode:
        pytest.skip("Playwright para Node.js no está instalado: se omiten las pruebas de navegador")
    import uvicorn

    from app.main import crear_app
    raiz = tmp_path_factory.mktemp("g4_interfaz")
    antes = {k: os.environ.get(k) for k in VARIABLES}
    os.environ.update({"VERIFACTU_DATA_DIR": str(raiz / "data"), "VERIFACTU_DIARIO_DIR": str(raiz / "diario"),
                       "VERIFACTU_CLAVE_MAESTRA_FICHERO": str(raiz / "secretos" / "clave.bin")})
    config.configurar(config.Ajustes())
    _reiniciar_limitadores()
    srv = uvicorn.Server(uvicorn.Config(crear_app(), host="127.0.0.1", port=0, log_level="warning"))
    hilo = threading.Thread(target=srv.run, daemon=True)
    hilo.start()
    try:
        limite = time.time() + 60
        while not srv.started and hilo.is_alive() and time.time() < limite:
            time.sleep(0.05)
        assert srv.started, "el servidor local no arrancó"
        url = f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"
        s = Sitio(raiz, url)
        # Operador de la plataforma (instala con la empresa A) y Ana, usuaria que da de alta la empresa B (administradora)
        op = _cliente_http(url)
        d = op.ok(op.post("/api/instalar", {"email": "operador@ejemplo.test", "nombre": "Operadora Ficticia", "clave": CLAVE,
                                            "empresa": EMPRESA_A, "registro_abierto": True}))
        op.csrf, ea = d["csrf"], d["empresas"][0]["id"]
        configurar(op, ea, patron="{n:3}/A{aa}", siguiente=1)
        ana = _registrar(_cliente_http(url), "ana@ejemplo.test")
        eb = ana.ok(ana.post("/api/empresas", EMPRESA_B))["id"]
        configurar(ana, eb, patron="{n:3}/B{aa}", siguiente=1)
        s.op, s.ana, s.ea, s.eb = op, ana, ea, eb
        yield s
    finally:
        srv.should_exit = True
        hilo.join(20)
        for k, v in antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.configurar(config.Ajustes())


def recorrido(s: Sitio, cuerpo: str, datos: dict | None = None, timeout: int = 300) -> dict:
    js = s.dir / f"recorrido_{uuid.uuid4().hex[:8]}.js"
    js.write_text(COMUN_JS + "\n(async () => {\nconst browser = await chromium.launch();\n" + cuerpo
                  + "\nawait terminar(browser);\n})().catch((e) => { console.log('FALLO ' + (e && e.stack || e)); process.exit(1); });\n",
                  encoding="utf-8")
    p = subprocess.run(["node", str(js), s.url, str(s.dir), json.dumps(datos or {})], capture_output=True, text=True, timeout=timeout)
    lineas = [x for x in p.stdout.splitlines() if x.startswith("RESULTADO ")]
    assert p.returncode == 0 and lineas, f"recorrido fallido:\n{p.stdout[-4000:]}\n{p.stderr[-2000:]}"
    res = json.loads(lineas[-1][len("RESULTADO "):])
    assert not res["problemas"], res["problemas"]   # sin errores de JavaScript ni violaciones de la CSP
    return res["obs"]


def test_ui2_ui5_banda_de_incidencia_al_dia_y_en_hora_peninsular(sitio):
    """UI-2: tras conciliar, y en cada recarga de «Remisión», la banda de la cabecera refleja el estado real de la cola
    (antes seguía diciendo «N pendiente(s)» con la cola vacía hasta 30 s después de navegar). UI-5: las fechas de la banda y
    de «Remisión» van en hora peninsular aunque el navegador esté en UTC."""
    s = sitio
    fids = [_emitir_a(s.op, s.ea, f"{n:03d}/A{db.ahora():%y}") for n in (1, 2)]
    con = db.conectar()
    assert remision.procesar(con, s.ea, "simulador", forzar_flujo=True)["aceptado"] == 2
    # Enviados y sin respuesta (la AEAT los tiene con la misma huella): incidencia de comunicación abierta
    rids = [r["id"] for r in db.filas(con, "SELECT id FROM registros WHERE empresa_id=? ORDER BY id", (s.ea,))]
    with db.transaccion(con):
        con.execute("UPDATE registros SET estado_envio='sin_confirmar' WHERE empresa_id=?", (s.ea,))
        con.execute("UPDATE control_flujo SET incidencia_desde=? WHERE empresa_id=? AND entorno='simulador'", (INCIDENCIA, s.ea))
    s.datos["factura_a"] = fids[0]
    obs = recorrido(s, r"""
const page = await abrir(browser);
await entrar(page, 'operador@ejemplo.test', 'clave-segura-123');
await ir(page, '/facturas', 'h1');
obs.banda_inicial = await esperar(() => banda(page));
await ir(page, '/remision', 'h3');
obs.remision_desde = await texto(page, '.alerta:has-text("Remisión en incidencia")');
await ir(page, '/consulta-aeat', 'h1');
await page.getByRole('button', { name: 'Consultar' }).click();
await page.getByRole('checkbox', { name: /Conciliar la factura/ }).first().check();
await page.getByRole('button', { name: /Conciliar los seleccionados/ }).click();
await page.locator('dialog[open]').waitFor();
obs.foco_conciliar = await foco(page);
await page.locator('dialog[open]').getByRole('button', { name: 'Conciliar', exact: true }).click();
await page.locator('.toast', { hasText: /conciliado/ }).first().waitFor();
obs.banda_tras_conciliar = await esperar(async () => { const b = await banda(page); return /: 1 registro/.test(b) ? b : ''; }) || await banda(page);
await ir(page, '/remision', 'h3');
// Otra sesión concilia el registro que queda mientras se está en «Remisión»; «Actualizar» hace lo mismo que la recarga periódica
const a = await sesionApi('operador@ejemplo.test', 'clave-segura-123');
const r = await a.r.post(`/api/empresas/${D.ea}/consulta/conciliar`, { headers: a.cab, data: { entorno: 'simulador', registro_ids: D.rids, confirmo: true } });
obs.conciliar_api = (await r.json()).incidencia_cerrada;
await page.getByRole('button', { name: 'Actualizar' }).click();
obs.banda_tras_actualizar = (await esperar(async () => (await banda(page)) === '' ? 'oculta' : '')) || await banda(page);
obs.cola = await texto(page, '.stats');
await page.screenshot({ path: `${DIR}/ui2_remision.png`, fullPage: true });
""", {"ea": s.ea, "rids": rids})
    assert "2 registro(s)" in obs["banda_inicial"], obs
    assert "1 registro(s)" in obs["banda_tras_conciliar"], obs   # la banda se actualiza al conciliar
    assert obs["conciliar_api"] is True
    assert obs["banda_tras_actualizar"] == "oculta", obs         # y en cada recarga de «Remisión»
    assert "Pendientes 0" in obs["cola"] and "Sin confirmar 0" in obs["cola"]
    assert obs["foco_conciliar"] == "BUTTON Conciliar"           # una confirmación normal empieza en su botón principal
    banda = obs["banda_inicial"]
    assert "desde el 01/07/2026 a las 12:05" in banda and "UTC" not in banda and "2026-07-01T" not in banda, banda
    assert "12:05" in obs["remision_desde"] and "10:05" not in obs["remision_desde"], obs["remision_desde"]   # navegador en UTC


def test_ui3_quien_pierde_el_acceso_vuelve_al_inicio(sitio):
    """UI-3: a quien le quitan el acceso con la sesión abierta no se le deja en un «No encontrado» con un «Reintentar» que
    siempre falla: se relee la sesión, se le avisa y se le lleva al inicio con la cabecera al día."""
    s = sitio
    for email in ("beto@ejemplo.test", "eva@ejemplo.test"):
        tok = s.ana.ok(s.ana.post(f"/api/empresas/{s.eb}/invitaciones", {"email": email, "rol": "emisor"}))["token"]
        _reiniciar_limitadores()
        _registrar(_cliente_http(s.url), email, invitacion=tok)
    con = db.conectar()
    beto, eva = (db.fila(con, "SELECT id FROM usuarios WHERE email=?", (e,))["id"] for e in ("beto@ejemplo.test", "eva@ejemplo.test"))
    obs = recorrido(s, r"""
const page = await abrir(browser);
await entrar(page, 'beto@ejemplo.test', 'clave-segura-123');
await ir(page, '/facturas', 'h1');
obs.cabecera_antes = await texto(page, '#sel-empresa');
const a = await sesionApi('ana@ejemplo.test', 'clave-segura-123');
obs.quitar = (await a.r.delete(`/api/empresas/${D.eb}/miembros/${D.beto}`, { headers: a.cab })).status();
await page.getByRole('link', { name: 'Documentos' }).click();
obs.aviso = await esperar(async () => {
  const t = page.locator('.toast', { hasText: /Ya no tiene acceso/ });
  return (await t.count()) ? limpio(await t.first().innerText()) : '';
});
await page.waitForTimeout(800);
obs.url = page.url().split('#')[1] || '';
obs.cabecera_despues = await texto(page, '#sel-empresa');
obs.contenido = await texto(page, '#contenido');
obs.reintentar = await page.getByRole('button', { name: 'Reintentar' }).count();
await page.screenshot({ path: `${DIR}/ui3_sin_acceso.png`, fullPage: true });
// Eva también pierde el acceso y abre «Mi cuenta»: la cabecera muestra las mismas empresas que la pantalla
const pe = await abrir(browser);
await entrar(pe, 'eva@ejemplo.test', 'clave-segura-123');
await ir(pe, '/facturas', 'h1');
obs.quitar_eva = (await a.r.delete(`/api/empresas/${D.eb}/miembros/${D.eva}`, { headers: a.cab })).status();
await pe.getByRole('link', { name: 'Mi cuenta' }).click();
obs.aviso_eva = await esperar(async () => {
  const t = pe.locator('.toast', { hasText: /Ya no tiene acceso/ });
  return (await t.count()) ? limpio(await t.first().innerText()) : '';
});
await pe.locator('h1:has-text("Mi cuenta")').waitFor();
await pe.waitForTimeout(500);
obs.url_eva = pe.url().split('#')[1] || '';
obs.cabecera_eva = await texto(pe, '#sel-empresa');
obs.cuenta_eva = await texto(pe, '#contenido');
// G4-V1: los dos vuelven a tener acceso porque aceptan una invitación en OTRA ventana (otra sesión). En la primera:
async function aceptarEnOtraVentana(email) {
  const inv = await (await a.r.post(`/api/empresas/${D.eb}/invitaciones`, { headers: a.cab, data: { email, rol: 'emisor' } })).json();
  const otra = await sesionApi(email, 'clave-segura-123');
  return (await otra.r.post('/api/invitaciones/aceptar', { headers: otra.cab, data: { token: inv.token } })).status();
}
async function toast(p, re) {
  return esperar(async () => { const t = p.locator('.toast', { hasText: re }); return (await t.count()) ? limpio(await t.first().innerText()) : ''; });
}
// - Beto, en «Nueva empresa», pulsa «Documentos»: se relee la sesión y se elige la empresa (antes: «Todavía no tiene ninguna empresa»)
obs.acepta_beto = await aceptarEnOtraVentana('beto@ejemplo.test');
await page.getByRole('link', { name: 'Documentos' }).click();
obs.aviso_beto_vuelve = await toast(page, /Ya tiene acceso/);
await page.locator('h1:has-text("Documentos")').waitFor();
obs.cabecera_beto_vuelve = limpio(await page.locator('#sel-empresa option:checked').innerText());
obs.titulo_beto_vuelve = await texto(page, '#contenido h1');
// - Eva pasa por «Nueva empresa», abre «Mi cuenta» y después «Documentos»: la cabecera y las pantallas dicen lo mismo
await ir(pe, '/empresa-nueva', 'h1');
obs.acepta_eva = await aceptarEnOtraVentana('eva@ejemplo.test');
await pe.getByRole('link', { name: 'Mi cuenta' }).click();
obs.aviso_eva_vuelve = await toast(pe, /Ya tiene acceso/);
await pe.waitForTimeout(500);
obs.cabecera_eva_cuenta = limpio(await pe.locator('#sel-empresa option:checked').innerText());
await pe.getByRole('link', { name: 'Documentos' }).click();
await pe.waitForTimeout(800);
obs.titulo_eva_documentos = await texto(pe, '#contenido h1');
await pe.getByRole('link', { name: 'Facturas' }).click();
await pe.waitForTimeout(800);
obs.titulo_eva_facturas = await texto(pe, '#contenido h1');
await pe.screenshot({ path: `${DIR}/ui3_vuelve_a_tener_acceso.png`, fullPage: true });
""", {"eb": s.eb, "beto": beto, "eva": eva})
    assert EMPRESA_B["razon_social"] in obs["cabecera_antes"] and obs["quitar"] == 200
    assert "Ya no tiene acceso a esa empresa" in obs["aviso"], obs
    assert obs["cabecera_despues"] == "Sin empresas" and obs["url"] == "/empresa-nueva", obs
    assert "No encontrado" not in obs["contenido"] and obs["reintentar"] == 0, obs
    assert obs["quitar_eva"] == 200 and "Ya no tiene acceso a esa empresa" in obs["aviso_eva"], obs
    assert obs["url_eva"] == "/cuenta" and obs["cabecera_eva"] == "Sin empresas", obs
    assert "Todavía no tiene acceso a ninguna empresa" in obs["cuenta_eva"], obs
    # G4-V1: con acceso de nuevo (aceptado en otra ventana), la cabecera y las pantallas no se contradicen
    assert obs["acepta_beto"] == 200 and obs["acepta_eva"] == 200, obs
    assert EMPRESA_B["razon_social"] in obs["aviso_beto_vuelve"], obs
    assert EMPRESA_B["razon_social"] in obs["cabecera_beto_vuelve"] and obs["titulo_beto_vuelve"] == "Documentos", obs
    assert EMPRESA_B["razon_social"] in obs["aviso_eva_vuelve"] and EMPRESA_B["razon_social"] in obs["cabecera_eva_cuenta"], obs
    assert obs["titulo_eva_documentos"] == "Documentos" and obs["titulo_eva_facturas"].startswith("Facturas"), obs


def test_ui4_pantallas_del_productor_y_del_ambito(sitio):
    """UI-4: un administrador de empresa que no es el operador ve que el productor lo identifica el operador de la
    plataforma (no un paso pendiente de su empresa, sin formulario deshabilitado), y la ayuda del ámbito ya no dice que
    las exenciones se bloquean. El operador sigue viendo el paso pendiente y el formulario."""
    s = sitio
    obs = recorrido(s, r"""
const page = await abrir(browser);
await entrar(page, 'ana@ejemplo.test', 'clave-segura-123');
await ir(page, '/configuracion/productor', '.config-contenido .card');
obs.productor = await texto(page, '.config-contenido');
obs.paso = await texto(page, '.stepper li:has(a[href="#/configuracion/productor"])');
obs.paso_clase = await page.locator('.stepper li:has(a[href="#/configuracion/productor"])').getAttribute('class');
obs.campos = await page.locator('.config-contenido input').count();
await page.screenshot({ path: `${DIR}/ui4_productor_ana.png`, fullPage: true });
await ir(page, '/configuracion/ambito', '.config-contenido .card');
obs.casos = await texto(page, '.card:has-text("Casos que la aplicación puede expedir")');
const op = await abrir(browser);
await entrar(op, 'operador@ejemplo.test', 'clave-segura-123');
await ir(op, '/configuracion/productor', '.config-contenido .card');
obs.op_paso = await texto(op, '.stepper li:has(a[href="#/configuracion/productor"])');
obs.op_boton = await op.getByRole('button', { name: 'Confirmar productor' }).isEnabled();
""")
    assert "operador de la plataforma" in obs["productor"], obs["productor"]
    assert "Solo un administrador puede modificarlo" not in obs["productor"] and obs["campos"] == 0
    assert "pendiente" not in obs["paso"] and "paso-pendiente" not in obs["paso_clase"], obs
    assert "lo completa el operador de la plataforma" in obs["paso"]
    assert "Operaciones exentas de IVA" in obs["casos"]
    assert "(exenciones" not in obs["casos"] and "exenciones," not in obs["casos"], obs["casos"]
    assert obs["op_paso"].endswith("pendiente") and obs["op_boton"] is True


def test_ui5_etiquetas_legibles_en_actividad_y_verificacion(sitio):
    """UI-5: sin valores crudos: «Rol: null», «Dias», «Invitacion», «Num serie», «Nif emisor»… pasan a etiquetas en
    español y los datos vacíos no se muestran."""
    s = sitio
    s.op.ok(s.op.post("/api/plataforma/invitaciones", {"email": "nadie@ejemplo.test", "dias": 7}))
    s.op.ok(s.op.post(f"/api/empresas/{s.ea}/invitaciones", {"email": "dora@ejemplo.test", "rol": "lector"}))
    fid = _factura_a(s)
    obs = recorrido(s, r"""
const page = await abrir(browser);
await entrar(page, 'operador@ejemplo.test', 'clave-segura-123');
await ir(page, '/plataforma', 'h1');
obs.plataforma = await texto(page, 'section.card:has(h2.card-titulo:text-is("Actividad de la plataforma"))');
await ir(page, `/factura/${D.fid}`, '.card-titulo:has-text("Verificación de integridad")');
obs.verificacion = await texto(page, '.card:has-text("Verificación de integridad")');
await ir(page, '/actividad', 'table');
obs.actividad = await texto(page, '#contenido');
await page.screenshot({ path: `${DIR}/ui5_actividad.png`, fullPage: true });
""", {"fid": fid})
    p = obs["plataforma"]
    assert "Rol: null" not in p and not re.search(r": null( ·|$)", p) and "Correo: nadie@ejemplo.test" in p and "Días: 7" in p, p
    v = obs["verificacion"]
    for etiqueta in ("Número de factura", "NIF del emisor", "NIF del destinatario", "Fecha de expedición", "Leyenda del QR",
                     "QR en la primera página"):
        assert etiqueta in v, (etiqueta, v)
    assert "Num serie" not in v and "Nif emisor" not in v and "Fecha expedicion" not in v
    a = obs["actividad"]
    assert "Invitación #" in a and "Invitacion" not in a and "Rol: Lector" in a and not re.search(r": null( ·|$)", a), a


CRUDO = re.compile(r"\b[a-z]+_[a-z_]+\b|\{\"|\bnull\b|\bundefined\b|\[object|\b[A-Z]{3,}_?[A-Z_]*:[a-z]|\d{4}-\d{2}-\d{2}T\d{2}")


def test_ui5_valores_legibles_en_actividad_y_certificados(sitio):
    """G4-V2: además de las etiquetas, los VALORES del detalle de actividad se muestran en palabras: el resumen de la consulta
    (antes un JSON), la evidencia, la estrategia del PDF, la clasificación propuesta, los puntos confirmados («DUDOSO:num_serie»),
    el ámbito («comun», «SII: 0», «plantilla_ofimatica») y los ajustes de la plataforma (0/1); y el método por el que se usa el
    certificado de la propia empresa («Certificado empresa»)."""
    s = sitio
    fid = _factura_a(s)
    hoy = db.ahora()
    s.op.ok(s.op.post(f"/api/empresas/{s.ea}/consulta", {"entorno": "simulador", "ejercicio": hoy.year, "periodo": f"{hoy.month:02d}"}))
    r = s.op.tc.post("/api/yo/certificados", files={"archivo": ("a.p12", _p12("SELLO EMPRESA A", EMPRESA_A), "application/x-pkcs12")},
                     data={"clave": CLAVE_CERT, "alias": "Sello de la empresa A"}, headers={"X-CSRF-Token": s.op.csrf})
    cid = s.op.ok(r)["id"]
    s.op.ok(s.op.post(f"/api/empresas/{s.ea}/certificados/asignar", {"certificado_id": cid, "entorno": "simulador"}))
    for v in (True, False):
        s.op.ok(s.op.put("/api/plataforma/ajustes", {"invitaciones_empresa_crean_cuentas": v}))
    obs = recorrido(s, r"""
const page = await abrir(browser);
await entrar(page, 'operador@ejemplo.test', 'clave-segura-123');
const detalles = async (sel) => (await page.locator(sel).allInnerTexts()).map(limpio);
await ir(page, '/actividad', 'table');
obs.actividad = await detalles('#contenido table tbody tr td:last-child');
await page.screenshot({ path: `${DIR}/ui5_valores_actividad.png`, fullPage: true });
await ir(page, '/plataforma', 'section.card:has(h2.card-titulo:text-is("Actividad de la plataforma")) table');
obs.plataforma = await detalles('section.card:has(h2.card-titulo:text-is("Actividad de la plataforma")) table tbody tr td:last-child');
await ir(page, '/configuracion/certificados', '.config-contenido .card');
obs.certificados = await texto(page, '.config-contenido');
await ir(page, '/mis-certificados', 'td:has-text("Sello de la empresa A")');
await page.getByRole('button', { name: 'Ver usos' }).first().click();
await page.locator('dialog[open] table').waitFor();
obs.usos = (await page.locator('dialog[open] table tbody tr').allInnerTexts()).map(limpio);
""")
    a = obs["actividad"]
    for d in a + obs["plataforma"] + obs["usos"]:
        assert not CRUDO.search(d), d                                   # ni códigos internos, ni JSON, ni marcas ISO
    t = " | ".join(a)
    assert "Resumen: coinciden " in t and "solo en el simulador local " in t and "solo en la AEAT" not in t and "Evidencia: respuesta de la consulta guardada el " in t, t
    assert "Territorio: Territorio común" in t and "SII: No" in t and "Origen de los PDF: Plantilla ofimática" in t, t
    assert "PDF: Reconstruido con los datos aprobados" in t or "PDF: QR añadido sobre el PDF original" in t, t
    assert "Clasificación propuesta: " in t and "Certificado propio: Sí" in t, t
    p = " | ".join(obs["plataforma"])
    assert "Ajuste: Las invitaciones de las empresas crean cuentas · Valor: Sí" in p, p
    assert "Ajuste: Las invitaciones de las empresas crean cuentas · Valor: No" in p, p
    assert "Método: Certificado de la empresa" in obs["certificados"] and "Método: Certificado empresa" not in obs["certificados"]
    u = " | ".join(obs["usos"])
    assert "Cargado" in u and "Asignado" in u and "usuario.cargado" not in u and "Tipo: Certificado de persona jurídica" in u, u
    assert fid


def test_ui5_fecha_de_la_firma_en_hora_peninsular():
    """G4-V3 (a): el motivo de la clasificación de un PDF firmado no copia la fecha interna de la firma («D:20260701…»): la
    muestra en hora peninsular, o dice que no se puede leer."""
    from app import borradores
    from app.extraccion import campos, texto
    datos = gen.dibujar(gen.demo(titulo="FACTURA", numero="007/A26", fecha="01/07/2026", emisor=(EMPRESA_A["razon_social"], NIF_A)))
    ext = campos.Extractor(texto.leer(datos), "firmada.pdf", NIF_A).extraer()

    def motivo(fecha):
        r = borradores.clasificar(ext, {"qr": [], "firmas": [{"firmado": True, "nombre": "SELLO FICTICIO", "fecha": fecha}]}, {"nif": NIF_A})
        assert r["sugerida"] == "emitida_propia", r
        return next(m for m in r["motivos"] if "firmado electrónicamente" in m)

    for fecha in ("D:20260701120500+02'00'", "D:20260701100500Z", "D:20260701050500-05'00'"):
        m = motivo(fecha)
        assert "(SELLO FICTICIO, el 01/07/2026 a las 12:05)" in m and "D:2026" not in m, (fecha, m)
    assert "(SELLO FICTICIO, el 01/01/2027 a las 00:30)" in motivo("D:20261231233000Z")        # el día peninsular
    assert "(SELLO FICTICIO, el 15/01/2026)" in motivo("D:20260115")
    for ilegible in ("D:2026", "D:20261341", "no es una fecha", ""):
        m = motivo(ilegible)
        assert "(SELLO FICTICIO, sin fecha legible)" in m and "D:" not in m, (ilegible, m)


def test_ui5_anio_y_mes_de_hoy_en_hora_peninsular(sitio):
    """G4-V3 (b): el año del ejemplo de una serie y el ejercicio y el mes que propone la consulta a la AEAT son los de hoy en
    hora peninsular, no los del reloj del navegador (en UTC, el 31/12 a las 23:30 UTC ya es 1 de enero en la Península)."""
    s = sitio
    obs = recorrido(s, r"""
const page = await abrir(browser);
await page.clock.setFixedTime(new Date('2026-12-31T23:30:00Z'));
await entrar(page, 'operador@ejemplo.test', 'clave-segura-123');
obs.hoy_navegador = await page.evaluate(() => new Date().getUTCFullYear());
await ir(page, '/configuracion/series', '.config-contenido form');
await page.getByLabel('Patrón del número').fill('{n:3}/A{aa}');
await page.waitForTimeout(300);
obs.ejemplo = await texto(page, '.config-contenido p:has-text("Ejemplo del siguiente número")');
await ir(page, '/consulta-aeat', 'h1');
obs.ejercicio = await page.getByLabel('Ejercicio').inputValue();
obs.periodo = await page.getByLabel('Periodo (mes de expedición)').inputValue();
await page.screenshot({ path: `${DIR}/ui5_consulta_fin_de_anio.png`, fullPage: true });
""")
    assert obs["hoy_navegador"] == 2026, obs                                  # el navegador (UTC) aún está en 2026
    assert obs["ejemplo"].endswith("001/A27"), obs
    assert obs["ejercicio"] == "2027" and obs["periodo"] == "01", obs


def test_ui7_confirmaciones_irreversibles_empiezan_en_cancelar(sitio):
    """UI-7: en una confirmación irreversible el foco empieza en «Cancelar»: un Intro de más (o mantenido) no quita a una
    persona, no elimina un certificado ni saca de la empresa; Escape también cierra sin hacer nada."""
    s = sitio
    tok = s.ana.ok(s.ana.post(f"/api/empresas/{s.eb}/invitaciones", {"email": "carla@ejemplo.test", "rol": "lector"}))["token"]
    _reiniciar_limitadores()
    _registrar(_cliente_http(s.url), "carla@ejemplo.test", invitacion=tok)
    r = s.ana.tc.post("/api/yo/certificados", files={"archivo": ("c.p12", _p12("SELLO PRUEBA G4"), "application/x-pkcs12")},
                      data={"clave": CLAVE_CERT, "alias": "Sello de prueba"}, headers={"X-CSRF-Token": s.ana.csrf})
    s.ana.ok(r)
    obs = recorrido(s, r"""
const page = await abrir(browser);
await entrar(page, 'ana@ejemplo.test', 'clave-segura-123');
// Quitar a una persona: foco en «Cancelar»; Intro cierra sin quitarla; Escape también
await ir(page, '/configuracion/usuarios', 'td:has-text("carla@ejemplo.test")');
const fila = page.locator('tr', { hasText: 'carla@ejemplo.test' });
await fila.getByRole('button', { name: 'Quitar' }).click();
await page.locator('dialog[open]').waitFor();
obs.quitar_foco = await foco(page);
await page.keyboard.press('Enter');
await page.waitForTimeout(800);
obs.quitar_tras_intro = await abiertos(page);
const miembros = async () => (await (await page.request.get(`${BASE}/api/empresas/${D.eb}/miembros`)).json()).map((x) => x.email);
obs.miembros_tras_intro = await miembros();
if (obs.miembros_tras_intro.includes('carla@ejemplo.test')) {   // (antes, ese Intro ya la había quitado)
  await fila.getByRole('button', { name: 'Quitar' }).click();
  await page.locator('dialog[open]').waitFor();
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
  obs.quitar_tras_escape = await abiertos(page);
}
// Eliminar un certificado con el teclado: Intro en «Eliminar» abre el diálogo y un segundo Intro no lo confirma
await ir(page, '/mis-certificados', 'td:has-text("Sello de prueba")');
await page.getByRole('button', { name: 'Eliminar' }).first().focus();
await page.keyboard.press('Enter');
await page.locator('dialog[open]').waitFor();
obs.eliminar_foco = await foco(page);
await page.keyboard.press('Enter');
await page.waitForTimeout(1200);
obs.eliminar_abiertos = await abiertos(page);
obs.certificados = (await (await page.request.get(`${BASE}/api/yo/certificados`)).json()).length;
// Salir de la empresa (Mi cuenta)
await ir(page, '/cuenta', 'td:has-text("TALLERES FICTICIOS DEL SUR")');
await page.locator('tr', { hasText: 'TALLERES FICTICIOS DEL SUR' }).getByRole('button', { name: 'Salir' }).click();
await page.locator('dialog[open]').waitFor();
obs.salir_foco = await foco(page);
await page.screenshot({ path: `${DIR}/ui7_salir.png` });
await page.keyboard.press('Escape');
await page.waitForTimeout(500);
obs.salir_tras_escape = await abiertos(page);
obs.miembros = await miembros();
""", {"eb": s.eb})
    assert "carla@ejemplo.test" in obs["miembros_tras_intro"], obs           # un Intro de más no quita a nadie
    assert obs["quitar_foco"] == "BUTTON Cancelar" and obs["quitar_tras_intro"] == 0 and obs["quitar_tras_escape"] == 0, obs
    assert obs["eliminar_foco"] == "BUTTON Cancelar" and obs["eliminar_abiertos"] == 0 and obs["certificados"] == 1, obs
    assert obs["salir_foco"] == "BUTTON Cancelar" and obs["salir_tras_escape"] == 0, obs
    assert "carla@ejemplo.test" in obs["miembros"] and "ana@ejemplo.test" in obs["miembros"], obs
