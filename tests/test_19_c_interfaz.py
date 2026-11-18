"""Carril C — interfaz en un navegador real (Playwright, Chromium) contra la aplicación en 127.0.0.1 con datos temporales y
SINTÉTICOS (el mismo arnés que test_18_g4_interfaz); solo el simulador local.

- Menú por papel: «Documentos», «Facturas», «Configuración» y «Más»; cada papel ve solo lo que puede usar (un lector no ve
  la subida, «Emitir», «Corregir esta factura…» ni «Anular registro…»; un emisor no ve las acciones de la configuración).
- Un mensaje claro, no un «No encontrado», cuando el papel no permite una acción.
- «Modo avanzado» por persona: oculto por defecto, se activa desde «Más» y muestra las herramientas técnicas; las pantallas
  técnicas siguen funcionando por su dirección.
- La etiqueta del entorno siempre visible; sin «Abrir el cotejo en la sede de la AEAT» en el modo práctica; sin fechas ISO,
  códigos internos ni decimales con punto en las pantallas principales; sin desplazamiento horizontal a 390 px.
Se omite si no hay Node.js con Playwright."""
import re

from test_18_g4_interfaz import (CLAVE, _cliente_http, _factura_a, _registrar, _reiniciar_limitadores, recorrido,  # noqa: F401
                                 sitio)

from app import db

COMUN = r"""
const sinScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const menu = async (page) => (await page.locator('.nav-principal > ul > li > a, .nav-principal > ul > li > details > summary').allInnerTexts()).map(limpio);
async function abrirMas(page) {
  await page.locator('.nav-mas > summary').click();
  await page.locator('.nav-mas-lista').waitFor();
  return (await page.locator('.nav-mas-lista a').allInnerTexts()).map(limpio);
}
"""
CRUDO = re.compile(r"\d{4}-\d{2}-\d{2}|\b[a-z]+_[a-z_]+\b|\b\d+\.\d{2}\b(?![.,]\d)|Simulador local|Registro aceptado|null|undefined")


def _invitar(s, email, rol):
    tok = s.op.ok(s.op.post(f"/api/empresas/{s.ea}/invitaciones", {"email": email, "rol": rol}))["token"]
    _reiniciar_limitadores()
    _registrar(_cliente_http(s.url), email, invitacion=tok)


def test_menu_por_papel_y_mensajes_claros(sitio):
    s = sitio
    s.op.ok(s.op.put("/api/yo/preferencias", {"modo_avanzado": False}))
    fid = _factura_a(s)
    _invitar(s, "lola@ejemplo.test", "lector")
    _invitar(s, "emilio@ejemplo.test", "emisor")
    obs = recorrido(s, COMUN + r"""
// --- Lectora (1280): solo consulta
const p = await abrir(browser);
await entrar(p, 'lola@ejemplo.test', 'clave-segura-123');
await p.locator('h1:has-text("Documentos")').waitFor();
obs.menu = await menu(p);
obs.etiqueta = await texto(p, '#cabecera .badge-grande');
obs.lector_subir = await p.getByRole('button', { name: 'Seleccionar archivos PDF' }).count();
obs.lector_docs = await texto(p, '#contenido');
obs.lector_mas = await abrirMas(p);
await ir(p, `/factura/${D.fid}`, '.card-titulo:has-text("Acciones")');
obs.lector_factura = await texto(p, '#contenido');
obs.lector_botones = await p.locator('#contenido button').allInnerTexts();
await ir(p, '/configuracion', 'h1');
obs.lector_config_controles = await p.locator('#contenido input, #contenido select, #contenido textarea, #contenido form').count();
obs.lector_config = await texto(p, '#contenido');
obs.pie = await texto(p, '#pie');
// --- Emisor (390): sin acciones de configuración ni «Anular registro…»
const e = await abrir(browser, { width: 390, height: 844 });
await entrar(e, 'emilio@ejemplo.test', 'clave-segura-123');
await e.locator('h1:has-text("Documentos")').waitFor();
obs.emisor_menu = await menu(e);
obs.emisor_menu_una_linea = await e.evaluate(() => { const li = [...document.querySelectorAll('.nav-principal > ul > li')]; return new Set(li.map((x) => x.offsetTop)).size === 1; });
obs.emisor_mas = await abrirMas(e);
obs.sin_scroll = { documentos: await sinScroll(e) };
await e.keyboard.press('Escape');
await ir(e, `/factura/${D.fid}`, '.card-titulo:has-text("Acciones")');
obs.emisor_botones = await e.locator('#contenido button').allInnerTexts();
obs.emisor_factura = await texto(e, '#contenido');
obs.sin_scroll.factura = await sinScroll(e);
await ir(e, '/configuracion/series', 'h1');
obs.emisor_config_controles = await e.locator('#contenido input, #contenido select, #contenido textarea, #contenido form').count();
obs.sin_scroll.configuracion = await sinScroll(e);
await ir(e, '/facturas', 'table');
obs.facturas = await texto(e, '#contenido table');
obs.sin_scroll.facturas = await sinScroll(e);
// Activa el modo avanzado desde «Más»: aparecen las herramientas técnicas
await abrirMas(e);
await e.getByRole('button', { name: 'Activar el modo avanzado' }).click();
await e.locator('.toast', { hasText: 'Modo avanzado activado' }).waitFor();
obs.emisor_mas_avanzado = await abrirMas(e);
obs.sin_scroll.mas_abierto = await sinScroll(e);
await e.keyboard.press('Escape');
await ir(e, `/factura/${D.fid}`, '.card-titulo:has-text("Registros de facturación")');
obs.emisor_avanzado_factura = await texto(e, '#contenido');
// Le cambian el papel a lector con la pantalla abierta: «Corregir esta factura» → mensaje claro (no «No encontrado»)
const a = await sesionApi('operador@ejemplo.test', 'clave-segura-123');
obs.cambio = (await a.r.post(`/api/empresas/${D.ea}/miembros`, { headers: a.cab, data: { email: 'emilio@ejemplo.test', rol: 'lector' } })).status();
await e.getByRole('button', { name: 'Corregir esta factura…' }).click();
await e.locator('dialog[open] textarea').fill('Precio de la línea 2');
await e.locator('dialog[open]').getByRole('button', { name: 'Preparar la corrección' }).click();
await e.locator('dialog[open] .alerta-danger').waitFor();
obs.mensaje_sin_permiso = await texto(e, 'dialog[open] .alerta-danger');
await e.screenshot({ path: `${DIR}/c_sin_permiso_390.png`, fullPage: true });
// --- Administrador (modo básico): numeración sencilla; las pantallas técnicas siguen por su dirección
const o = await abrir(browser);
await entrar(o, 'operador@ejemplo.test', 'clave-segura-123');
obs.op_mas = await abrirMas(o);
await o.keyboard.press('Escape');
await ir(o, '/configuracion/series', '.config-contenido .card');
obs.op_numeracion = await texto(o, '.config-contenido');
obs.op_patron = await o.getByLabel('Patrón del número').count();
await ir(o, '/consulta-aeat', 'h1');
obs.op_consulta = await texto(o, '#contenido');
await ir(o, '/remision', 'h3');
obs.op_remision = await texto(o, '#contenido');
await ir(o, `/factura/${D.fid}`, '.card-titulo:has-text("Acciones")');
obs.op_factura = await texto(o, '#contenido');
obs.op_botones = await o.locator('#contenido button').allInnerTexts();
await o.screenshot({ path: `${DIR}/c_factura_admin_1280.png`, fullPage: true });
""", {"fid": fid, "ea": s.ea})
    # Menú y etiqueta del entorno
    assert obs["menu"] == ["Documentos", "Facturas", "Configuración", "Más"], obs["menu"]
    assert obs["emisor_menu"] == obs["menu"] and obs["emisor_menu_una_linea"], obs
    assert obs["etiqueta"] == "Modo práctica: no se envía a Hacienda", obs["etiqueta"]
    assert "Declaración responsable" in obs["pie"]
    # Lectora: ni subida, ni acciones sobre la factura, ni consulta a Hacienda, ni formularios de configuración
    assert obs["lector_subir"] == 0 and "no subirlos ni emitirlos" in obs["lector_docs"], obs["lector_docs"]
    assert "Envíos a Hacienda" in obs["lector_mas"] and "Consulta a Hacienda" not in obs["lector_mas"], obs["lector_mas"]
    assert not any(re.search(r"Anular|Corregir|Subsanar|Reintentar|Emitir", b) for b in obs["lector_botones"]), obs["lector_botones"]
    assert "solo permite consultar" in obs["lector_factura"], obs["lector_factura"]
    assert obs["lector_config_controles"] == 0 and "Solo un administrador de la empresa puede cambiarla" in obs["lector_config"]
    # Emisor: corrige pero no anula; configuración de solo lectura
    assert any("Corregir esta factura" in b for b in obs["emisor_botones"]), obs["emisor_botones"]
    assert not any("Anular" in b or "Subsanar" in b for b in obs["emisor_botones"]), obs["emisor_botones"]
    assert obs["emisor_config_controles"] == 0, obs
    assert all(obs["sin_scroll"].values()), obs["sin_scroll"]
    # Modo avanzado: las herramientas técnicas aparecen en «Más» y en la factura
    assert "Consulta a Hacienda" not in obs["emisor_mas"] and "Actividad" not in obs["emisor_mas"], obs["emisor_mas"]
    for x in ("Consulta a Hacienda", "Actividad", "Mis certificados"):
        assert x in obs["emisor_mas_avanzado"], obs["emisor_mas_avanzado"]
    assert "Huella" in obs["emisor_avanzado_factura"] and "CSV" in obs["emisor_avanzado_factura"]
    assert db.fila(db.conectar(), "SELECT modo_avanzado FROM usuarios WHERE email='emilio@ejemplo.test'")["modo_avanzado"] == 1
    # Mensaje claro cuando el papel no permite la acción
    assert obs["cambio"] == 200
    m = obs["mensaje_sin_permiso"]
    assert "no permite esta acción" in m and "«Lector»" in m and "No encontrado" not in m, m
    # Administrador en modo básico
    assert "Consulta a Hacienda" not in obs["op_mas"]
    assert obs["op_patron"] == 0 and re.search(r"Facturas: \d{3}/A\d{2}, \d{3}/A\d{2}…", obs["op_numeracion"]), obs["op_numeracion"]
    assert "Mostrar las opciones avanzadas" in obs["op_numeracion"] and "{n" not in obs["op_numeracion"]
    assert "Consulta a Hacienda (AEAT)" in obs["op_consulta"] and "herramienta del modo avanzado" in obs["op_consulta"]
    assert "Anular registro…" in obs["op_botones"], obs["op_botones"]
    # Lenguaje llano en el modo práctica
    for t in (obs["op_factura"], obs["lector_factura"], obs["emisor_factura"], obs["op_remision"], obs["facturas"], obs["op_numeracion"]):
        assert "Abrir el cotejo" not in t and "Verificación independiente de la cadena" not in t
        assert not CRUDO.search(t), (CRUDO.search(t), t)
    assert "Aceptada en la simulación" in obs["op_factura"] + obs["facturas"] or "Pendiente de enviar" in obs["op_factura"]
