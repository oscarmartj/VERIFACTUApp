"""Carril C — reparación tras la verificación independiente (datos SINTÉTICOS; solo el simulador local).

- SEG-1: «Emitir» y «He revisado…» quedan ligados a la versión del borrador que se mostró («version»/«version_vista»): si otra
  persona (u otra pestaña) cambia el borrador con el diálogo abierto, el servidor responde 409 «borrador_cambiado» y no se
  expide nada; la interfaz cierra el diálogo, vuelve a pintar la revisión y lo explica.
- VN-1/VN-2: ningún «null» suelto en la pantalla (bajo «Emitir», en el pie de las páginas públicas, en el alta con certificado):
  el append nativo convierte null en el texto «null»; se usa anadir().
- VN-3: los mensajes de la revisión escriben los importes en formato español («1.028,50 €»), en palabras (sin «≠» ni
  «base + cuota») y sin códigos internos («E1 —», «(E1–E6)»).
- VN-4: la configuración de solo lectura juzga las respuestas de la empresa, no la declaración de quien mira.
- VN-5: la tabla del IVA de la revisión cabe en su tarjeta a 1280 y a 390 px («Quitar» y el selector de la exención, enteros).
- VN-6: a un lector no se le explica cómo usar botones que no tiene.
- VN-7: el paso «Certificado» en el modo práctica dice «guardado…» si hay uno, o «no hace falta en el modo práctica».
"""
import re
from pathlib import Path

from conftest import EMPRESA_REPLICA, Cliente, borrador_listo, configurar
from fastapi.testclient import TestClient
from test_16_u_multiusuario import NIF_B, instalar, registrar
from test_16_x_exentas import datos_base, sujeta, validar
from test_18_g4_interfaz import (CLAVE_CERT, EMPRESA_A, NIF_A, _factura_a, _p12, gen, recorrido, sitio)  # noqa: F401
from test_19_c_interfaz import _invitar

from app import borradores, db

RAIZ = Path(__file__).resolve().parent.parent


# --- SEG-1 ------------------------------------------------------------------------------------------------------------

def _emisor(op, eid, email="emi@ejemplo.test"):
    tok = op.ok(op.post(f"/api/empresas/{eid}/invitaciones", {"email": email, "rol": "emisor"}))["token"]
    b = Cliente(TestClient(op.tc.app))
    assert registrar(b, email, invitacion=tok).status_code == 200
    return b


def _n_facturas():
    con = db.conectar()
    try:
        return db.fila(con, "SELECT COUNT(*) n FROM facturas")["n"]
    finally:
        con.close()


def test_emitir_y_confirmar_solo_con_la_version_que_se_vio(app_cliente):
    op = app_cliente
    eid = instalar(op, empresa=EMPRESA_REPLICA)
    configurar(op, eid)
    otro = _emisor(op, eid)
    _, bid, visto = borrador_listo(op, eid, "replica_166-26.pdf")
    assert visto["puede_emitir"] and re.fullmatch(r"[0-9a-f]{64}", visto["version"]), visto.get("version")
    cliente_visto = visto["datos"]["destinatario"]["nombre"]
    # Otra persona cambia el cliente con el diálogo de A abierto y vuelve a confirmar lo que quedó pendiente
    r = otro.ok(otro.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "destinatario.nombre", "valor": "OTRO CLIENTE DISTINTO S.L."},
        {"campo": "destinatario.nif", "valor": NIF_B}]}))
    assert r["version"] != visto["version"]
    claves = [i["clave"] for i in r["incidencias"] if i["nivel"] == "confirmar"]
    if claves:
        r = otro.ok(otro.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves, "version_vista": r["version"]}))
    assert r["puede_emitir"], r["incidencias"]
    # A pulsa «Emitir» con lo que vio: 409 y nada expedido
    antes = _n_facturas()
    e = op.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "k-a", "confirmo": True,
                                                  "version_vista": visto["version"]})
    assert e.status_code == 409 and e.json()["codigo"] == "borrador_cambiado", e.text
    assert "ha cambiado" in e.json()["detail"] and _n_facturas() == antes
    # «He revisado…» con la versión antigua tampoco confirma valores que no se han visto
    c = op.post(f"/api/borradores/{bid}/confirmaciones", {"claves": ["DUDOSO:destinatario.nombre"], "version_vista": visto["version"]})
    assert c.status_code == 409 and c.json()["codigo"] == "borrador_cambiado", c.text
    assert op.ok(op.get(f"/api/borradores/{bid}"))["version"] == r["version"]   # nada cambió
    # Tras volver a leerlo (ahora sí ve el cliente nuevo), emite con la versión nueva
    nuevo = op.ok(op.get(f"/api/borradores/{bid}"))
    assert nuevo["datos"]["destinatario"]["nombre"] == "OTRO CLIENTE DISTINTO S.L." != cliente_visto
    # Una versión que no es texto se rechaza igual (no se ignora)
    r = op.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "k-c", "confirmo": True,
                                                  "version_vista": 123})
    assert r.status_code == 409 and r.json()["codigo"] == "borrador_cambiado" and _n_facturas() == antes
    fid = op.ok(op.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "k-b", "confirmo": True,
                                                          "version_vista": nuevo["version"]}))["factura_id"]
    f = op.ok(op.get(f"/api/facturas/{fid}"))
    assert (f.get("factura") or f)["destinatario_nombre"] == "OTRO CLIENTE DISTINTO S.L."
    # La misma petición repetida (idempotencia) devuelve la misma factura aunque el borrador ya esté emitido
    assert op.ok(op.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": "k-b", "confirmo": True,
                                                           "version_vista": nuevo["version"]}))["factura_id"] == fid


def test_la_spa_envia_la_version_vista():
    js = (RAIZ / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "confirmo: true, version_vista: version" in js
    assert "json: { claves, version_vista: b.version }" in js
    assert "borradorCambiado(err)" in js


# --- VN-1 / VN-2: ningún «null» suelto --------------------------------------------------------------------------------

def _argumentos(s: str, i: int) -> tuple[str, int]:
    depth, j, q = 1, i, None
    while depth:
        c = s[j]
        if q:
            if c == "\\":
                j += 2
                continue
            if c == q:
                q = None
        elif c in "'\"`":
            q = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        j += 1
    return s[i:j - 1], j


def _nivel_superior(args: str) -> list[str]:
    """Argumentos de primer nivel, con lo anidado (paréntesis, corchetes, llaves, cadenas) eliminado."""
    out, cur, depth, q, i = [], "", 0, None, 0
    while i < len(args):
        c = args[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"`":
            q = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(cur)
            cur = ""
        elif depth == 0:
            cur += c
        i += 1
    return out + [cur]


def test_sin_append_nativo_con_hijos_nulos():
    """El Element.append nativo escribe «null» por un hijo null y «[object HTMLDivElement]» por una lista: con hijos
    opcionales se usa anadir() (o h()), que los salta. Antes: el pie («null» en las páginas públicas), las acciones de la
    revisión («null» bajo «Emitir») y el alta con certificado."""
    js = (RAIZ / "app" / "static" / "app.js").read_text(encoding="utf-8")
    malos = []
    for m in re.finditer(r"\.append\(", js):
        args, _ = _argumentos(js, m.end())
        for a in _nivel_superior(args):
            t = a.strip()
            nulo = re.search(r":\s*(null|undefined)\s*$|^(null|undefined)$", t) or ("&&" in t and "?" not in t)
            if (nulo or re.search(r"\.(map|filter)$", t)) and not t.startswith("..."):
                malos.append((js[:m.start()].count("\n") + 1, t[:80]))
    assert not malos, malos


# --- VN-3: importes de los mensajes en formato español ------------------------------------------------------------------

PUNTO = re.compile(r"(?<![\d.])\d+\.\d{1,2}(?![\d])")


def _mensajes(inc):
    return [i["mensaje"] for i in inc]


def test_mensajes_de_la_revision_con_importes_en_formato_espanol():
    casos = {
        "total": datos_base([sujeta("21", "850.00", "178.50")], "850.00", "178.50", "1030.50"),
        "cuota": datos_base([sujeta("21", "850.00", "170.00")], "850.00", "170.00", "1020.00"),
        "base": datos_base([sujeta("21", "800.00", "168.00")], "850.00", "168.00", "1018.00"),
        "lineas": datos_base([sujeta("21", "850.00", "178.50")], "850.00", "178.50", "1028.50",
                             lineas=[{"concepto": "Trabajo", "cantidad": "2", "precio": "400.00", "importe": "850.00"}]),
        "retencion": datos_base([sujeta("21", "850.00", "178.50")], "850.00", "178.50", "1028.50", total_a_pagar="900.00",
                                retencion={"importe": "100.00", "porcentaje": "12.5"}),
        "exenta": datos_base([borradores.fila_exenta("650.00", "E1")], "650.00", "0.00", "650.00"),
        "sin_iva": datos_base([], "650.00", "0.00", "650.00"),
    }
    vistos = {}
    for k, d in casos.items():
        msgs = _mensajes(validar(d))
        vistos[k] = msgs
        for m in msgs:
            assert not PUNTO.search(m), (k, PUNTO.search(m).group(), m)
            assert "≠" not in m and "base + cuota" not in m and "E1–E6" not in m and not re.search(r"\bE[1-6] —", m), (k, m)
    junto = {k: " ".join(v) for k, v in vistos.items()}
    assert "El total (1.030,50 €) no es la base (850,00 €) más el IVA (178,50 €), que suman 1.028,50 €." in junto["total"]
    assert "no es el 21 % de su base (850,00 €), que da 178,50 €" in junto["cuota"]
    assert "(800,00 €) no coincide con la base imponible (850,00 €)" in junto["base"]
    assert "la cantidad (2) por el precio (400,00 €) da 800,00 €, pero el importe es 850,00 €" in junto["lineas"]
    assert "La retención (100,00 €) no es el 12,5 % de la base, que da 106,25 €." in junto["retencion"]
    assert "Base exenta de la fila 1 (650,00 €): Exenta por el artículo 20" in junto["exenta"]
    assert "el total (650,00 €) es igual a la base imponible" in junto["sin_iva"]
    # Total no leído en el documento, con retención y cifras sin interpretar: también en formato español
    ext = {"total_factura": {"valor": None, "sin_clasificar": ["1240.00"]}}
    d = datos_base([sujeta("21", "850.00", "178.50")], "850.00", "178.50", "1028.50", total_a_pagar="901.00",
                   retencion={"importe": "127.50", "porcentaje": "15"})
    m = " ".join(_mensajes(validar(d, ext)))
    assert "como la base más el IVA (1.028,50 €), y el importe a pagar como ese total menos la retención (901,00 €)" in m, m
    assert "(1.240,00 €)" in m and not PUNTO.search(m), m


# --- Navegador: VN-1, VN-2, VN-4, VN-5, VN-6, VN-7 y SEG-1 en la interfaz ----------------------------------------------

def _subir(c, eid, numero, datos=None):
    datos = datos or gen.dibujar(gen.demo(numero=numero, fecha=db.ahora().strftime("%d/%m/%Y"),
                                          emisor=(EMPRESA_A["razon_social"], NIF_A)))
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (f"{numero.replace('/', '-')}.pdf", datos, "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    did = c.ok(r)[0]["documento_id"]
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "motivo": "prueba"}))
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]}))
    return bid


def _siguiente(s):
    con = db.conectar()
    n = db.fila(con, "SELECT COUNT(*) AS n FROM facturas WHERE empresa_id=?", (s.ea,))["n"] + 1
    con.close()
    return f"{n:03d}/A{db.ahora():%y}"


def test_interfaz_sin_null_tabla_del_iva_y_textos_por_papel(sitio):
    s = sitio
    s.op.ok(s.op.put("/api/yo/preferencias", {"modo_avanzado": False}))
    fid = _factura_a(s)
    _invitar(s, "lucia@ejemplo.test", "lector")
    _invitar(s, "eloy@ejemplo.test", "emisor")
    bid_listo = _subir(s.op, s.ea, _siguiente(s))
    assert s.op.ok(s.op.get(f"/api/borradores/{bid_listo}"))["puede_emitir"]
    # Borrador con una base exenta (alquiler de vivienda) para la tabla del IVA
    bid_ex = _subir(s.op, s.ea, "999/A99")
    s.op.ok(s.op.patch(f"/api/borradores/{bid_ex}", {"cambios": [
        {"campo": "desglose", "valor": [borradores.fila_exenta("850.00", "E1")]},
        {"campo": "cuota_total", "valor": "0.00"}, {"campo": "importe_total", "valor": "850.00"},
        {"campo": "total_a_pagar", "valor": "850.00"}]}))
    # Certificado de la empresa B guardado (Ana): en el modo práctica, el paso dice «guardado…»
    r = s.ana.tc.post(f"/api/empresas/{s.eb}/certificados", files={"archivo": ("b.p12", _p12("SELLO B"), "application/x-pkcs12")},
                      data={"clave": CLAVE_CERT, "entorno": "todos"}, headers={"X-CSRF-Token": s.ana.csrf})
    assert r.status_code == 200, r.text
    facturas_antes = _n_facturas()
    obs = recorrido(s, r"""
const sinNull = async (p) => !/\bnull\b|\bundefined\b|\[object /.test(await p.locator('body').innerText());
obs.null = {};
// --- Páginas públicas (sin sesión): el pie sin «null»
const pub = await abrir(browser);
await pub.goto(`${BASE}/#/login`);
await pub.getByRole('button', { name: 'Entrar' }).waitFor();
obs.pie_login = await texto(pub, '#pie');
obs.null.login = await sinNull(pub);
await pub.goto(`${BASE}/#/registro?invitacion=x`);
await pub.waitForTimeout(800);
obs.null.registro = await sinNull(pub);
// --- Administrador: revisión lista para emitir (sin «null» bajo «Emitir») y tabla del IVA a 1280
const o = await abrir(browser);
await entrar(o, 'operador@ejemplo.test', 'clave-segura-123');
await ir(o, `/borrador/${D.listo}`, '.card-emitir');
await o.getByRole('button', { name: 'Emitir', exact: true }).waitFor();
obs.resumen = await texto(o, '.card-emitir');
obs.null.revision = await sinNull(o);
await o.screenshot({ path: `${DIR}/c_rep_lista_1280.png`, fullPage: true });
const medirIva = (p) => p.evaluate(() => {
  // la tabla de la tarjeta «IVA» de la revisión
  const h2 = [...document.querySelectorAll('h2.card-titulo')].find((x) => x.textContent.trim() === 'IVA');
  const t = h2 && h2.closest('section').querySelector('table');
  if (!t) return null;
  const w = t.closest('.tabla-wrap').getBoundingClientRect();
  const dentro = (el) => { const r = el.getBoundingClientRect(); return r.left >= w.left - 1 && r.right <= w.right + 1; };
  const quitar = [...t.querySelectorAll('button')].filter((b) => /Quitar/.test(b.textContent));
  const sel = t.querySelector('select');
  return { cabe: t.scrollWidth <= t.closest('.tabla-wrap').clientWidth + 1, quitar: quitar.length > 0 && quitar.every(dentro),
           selector: !!sel && dentro(sel), texto: t.innerText };
});
await ir(o, `/borrador/${D.ex}`, 'h2.card-titulo:text-is("IVA")');
obs.iva_1280 = await medirIva(o);
obs.null.exenta = await sinNull(o);
await o.screenshot({ path: `${DIR}/c_rep_iva_1280.png`, fullPage: true });
await ir(o, '/configuracion/empresa', '.stepper');
obs.paso_cert_sin = await texto(o, '.stepper');
// SEG-1 en la interfaz: el diálogo abierto, otra persona cambia el cliente; «Emitir» no expide y se explica
await ir(o, `/borrador/${D.listo}`, '.card-emitir');
await o.getByRole('button', { name: 'Emitir', exact: true }).click();
await o.locator('dialog[open]').waitFor();
obs.dialogo = await texto(o, 'dialog[open]');
const otro = await sesionApi('eloy@ejemplo.test', 'clave-segura-123');
obs.patch = (await otro.r.patch(`/api/borradores/${D.listo}`, { headers: otro.cab,
  data: { cambios: [{ campo: 'destinatario.nombre', valor: 'OTRO CLIENTE DISTINTO S.L.' }] } })).status();
await o.locator('dialog[open]').getByRole('button', { name: 'Emitir', exact: true }).click();
await o.locator('.toast', { hasText: 'ha cambiado' }).waitFor({ timeout: 8000 }).catch(() => {});
await o.waitForTimeout(500);
obs.toast = limpio((await o.locator('.toast').allInnerTexts()).join(' | '));
obs.dialogos_abiertos = await abiertos(o);
obs.hash = await o.evaluate(() => window.location.hash);
obs.revision_nueva = await texto(o, '#contenido');
// --- Exenta a 390 px
const m = await abrir(browser, { width: 390, height: 844 });
await entrar(m, 'operador@ejemplo.test', 'clave-segura-123');
await ir(m, `/borrador/${D.ex}`, 'h2.card-titulo:text-is("IVA")');
obs.iva_390 = await medirIva(m);
obs.sin_scroll_390 = await m.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
await m.screenshot({ path: `${DIR}/c_rep_iva_390.png`, fullPage: true });
// --- Emisor: la configuración dice que las tres preguntas están respondidas
const e = await abrir(browser);
await entrar(e, 'eloy@ejemplo.test', 'clave-segura-123');
await ir(e, '/configuracion', 'h1');
obs.emisor_config = await texto(e, '#contenido');
obs.null.emisor_config = await sinNull(e);
// --- Lectora: la ayuda de «Acciones» no explica botones que no tiene
const l = await abrir(browser);
await entrar(l, 'lucia@ejemplo.test', 'clave-segura-123');
await ir(l, `/factura/${D.fid}`, '.card-titulo:has-text("Acciones")');
obs.lector_acciones = await texto(l, 'section.card:has(h2.card-titulo:text-is("Acciones"))');
obs.null.lector_factura = await sinNull(l);
// --- Ana (empresa B con su certificado guardado): paso «Certificado» en el modo práctica
const a = await abrir(browser);
await entrar(a, 'ana@ejemplo.test', 'clave-segura-123');
await ir(a, '/configuracion/empresa', '.stepper');
obs.paso_cert_con = await texto(a, '.stepper');
""", {"listo": bid_listo, "ex": bid_ex, "fid": fid})
    # VN-1 / VN-2
    assert all(obs["null"].values()), obs["null"]
    assert "null" not in obs["pie_login"] and "versión" in obs["pie_login"], obs["pie_login"]
    assert "null" not in obs["resumen"] and "Se emitirá en" in obs["resumen"], obs["resumen"]
    # VN-5: la tabla del IVA cabe en su tarjeta, con «Quitar» y el selector enteros
    for k in ("iva_1280", "iva_390"):
        assert obs[k] and obs[k]["cabe"] and obs[k]["quitar"] and obs[k]["selector"], (k, obs[k])
        assert "Con IVA o exenta" in obs[k]["texto"], obs[k]["texto"]
    assert obs["sin_scroll_390"]
    # VN-7
    assert "Certificado no hace falta en el modo práctica" in obs["paso_cert_sin"], obs["paso_cert_sin"]
    assert "Certificado guardado (se usará al enviar a Hacienda)" in obs["paso_cert_con"], obs["paso_cert_con"]
    assert "simulador" not in obs["paso_cert_sin"] + obs["paso_cert_con"]
    # VN-4
    assert "Respondidas: la aplicación puede expedir" in obs["emisor_config"], obs["emisor_config"]
    assert "Pendientes: un administrador" not in obs["emisor_config"]
    # VN-6
    assert "solo permite consultar" in obs["lector_acciones"], obs["lector_acciones"]
    assert "Corregir esta factura" not in obs["lector_acciones"] and "anulación" not in obs["lector_acciones"], obs["lector_acciones"]
    # SEG-1 en la interfaz: nada expedido, diálogo cerrado, revisión repintada con el cliente nuevo
    assert obs["patch"] == 200
    assert "ha cambiado" in obs["toast"] and obs["dialogos_abiertos"] == 0, obs
    assert obs["hash"].startswith("#/borrador/") and "OTRO CLIENTE DISTINTO S.L." in obs["revision_nueva"], obs["hash"]
    assert _n_facturas() == facturas_antes
