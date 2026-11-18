"""Carril B — interfaz: alta guiada por el certificado, primeros pasos con las tres preguntas (sin respuesta marcada de
antemano), invitación de un solo enlace y configuración sin el paso del productor para quien no es el operador.

Navegador real (Playwright, Chromium) contra la aplicación en 127.0.0.1 con datos temporales y SINTÉTICOS (el mismo
arnés que test_18_g4_interfaz); solo el simulador local. Sin errores de JavaScript, sin violaciones de la CSP y sin
desplazamiento horizontal a 390 px. Se omite si no hay Node.js con Playwright."""
from test_18_g4_interfaz import (CLAVE, _cliente_http, _p12, _registrar, _reiniciar_limitadores, recorrido,  # noqa: F401
                                 sitio)

from app import db
from app.nif import completar_cif

NIF_NUEVA = completar_cif("B", "0071999")
EMPRESA_NUEVA = {"nif": NIF_NUEVA, "razon_social": "CARPINTERÍA FICTICIA DEL ESTE S.L."}

ANCHO = r"""
async function sinScrollHorizontal(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}
"""


def test_alta_con_certificado_y_tres_preguntas_a_390(sitio):
    s = sitio
    _reiniciar_limitadores()
    tok = s.op.ok(s.op.post("/api/plataforma/invitaciones", {"email": "marta@ejemplo.test"}))["token"]
    _registrar(_cliente_http(s.url), "marta@ejemplo.test", invitacion=tok)
    ruta = s.dir / "empresa_nueva.p12"
    ruta.write_bytes(_p12("SELLO EMPRESA NUEVA", EMPRESA_NUEVA))
    obs = recorrido(s, ANCHO + r"""
const page = await abrir(browser, { width: 390, height: 844 });
await page.goto(`${BASE}/#/login`);
await page.getByLabel('Correo electrónico').fill('marta@ejemplo.test');
await page.getByLabel('Contraseña', { exact: true }).fill('clave-segura-123');
await page.getByRole('button', { name: 'Entrar' }).click();
await page.locator('h1:has-text("Dar de alta su empresa")').waitFor();
obs.alta_sin_scroll = await sinScrollHorizontal(page);
obs.alta = await texto(page, '#contenido');
await page.getByLabel('Certificado de su empresa (.p12 o .pfx)').setInputFiles(D.p12);
await page.getByLabel('Contraseña del certificado').fill('Clave-Del-Certificado-Prueba-9');
await page.getByRole('button', { name: 'Ver los datos del certificado' }).click();
await page.getByRole('button', { name: 'Crear la empresa con estos datos' }).waitFor();
obs.clave_tras_enviar = await page.getByLabel('Contraseña del certificado').inputValue();
obs.previa = await texto(page, '.card:has-text("Empiece con el certificado")');
obs.previa_sin_scroll = await sinScrollHorizontal(page);
await page.screenshot({ path: `${DIR}/b_previa_390.png`, fullPage: true });
await page.getByRole('button', { name: 'Crear la empresa con estos datos' }).click();
await page.locator('h1:has-text("Primeros pasos")').waitFor();
await page.getByRole('button', { name: 'Guardar respuestas' }).waitFor();
obs.marcadas = await page.locator('.tres-preguntas input[type=radio]:checked').count();
obs.guardar_inicial = await page.getByRole('button', { name: 'Guardar respuestas' }).isEnabled();
for (const g of ['1. ', '2. ', '3. ']) {
  await page.locator('fieldset', { hasText: g }).locator('label', { hasText: /^\s*No\s*$/ }).click();
}
obs.guardar_sin_declaracion = await page.getByRole('button', { name: 'Guardar respuestas' }).isEnabled();
await page.getByLabel(/Declaro estar autorizado/).check();
await page.getByRole('button', { name: 'Guardar respuestas' }).click();
await page.getByText('Respondidas.').waitFor();
obs.pasos_sin_scroll = await sinScrollHorizontal(page);
obs.pasos = await texto(page, '#contenido');
await page.screenshot({ path: `${DIR}/b_primeros_pasos_390.png`, fullPage: true });
// Configuración: sin el paso del productor y la ruta antigua de la autorización lleva a las tres preguntas
await ir(page, '/configuracion/autorizacion', '.config-contenido .card');
obs.paso_actual = await page.locator('.stepper a[aria-current="step"]').getAttribute('href');
obs.productor_en_menu = await page.locator('.stepper a[href="#/configuracion/productor"]').count();
obs.marcadas_confirmadas = await page.locator('.tres-preguntas input[type=radio]:checked').count();
await ir(page, '/configuracion/certificados', '.config-contenido .card');
obs.certificados = await texto(page, '.config-contenido');
obs.cert_sin_scroll = await sinScrollHorizontal(page);
""", {"p12": str(ruta)})
    assert obs["alta_sin_scroll"] and obs["previa_sin_scroll"] and obs["pasos_sin_scroll"] and obs["cert_sin_scroll"], obs
    assert "Probar sin certificado (no se envía a Hacienda)" in obs["alta"]
    assert obs["clave_tras_enviar"] == ""                                   # la contraseña no se queda en la página
    assert f"Empresa: {EMPRESA_NUEVA['razon_social']} · NIF {NIF_NUEVA}; válido hasta" in obs["previa"], obs["previa"]
    assert obs["marcadas"] == 0 and obs["guardar_inicial"] is False and obs["guardar_sin_declaracion"] is False, obs
    assert "Respondidas." in obs["pasos"] and "(deducido del NIF)" in obs["pasos"]
    assert obs["paso_actual"] == "#/configuracion/ambito" and obs["productor_en_menu"] == 0, obs
    assert obs["marcadas_confirmadas"] == 3                                 # ya respondidas: se muestran sus respuestas
    assert "Pruebas y producción" in obs["certificados"] and "Opciones avanzadas" in obs["certificados"], obs["certificados"]
    con = db.conectar()
    e = db.fila(con, "SELECT * FROM empresas WHERE nif=?", (NIF_NUEVA,))
    assert e["alta_origen"] == "certificado" and e["ambito_confirmado_en"] and e["nif_verificado_en"] is None
    assert db.fila(con, "SELECT entorno FROM certificados WHERE empresa_id=?", (e["id"],))["entorno"] == "todos"


def test_invitacion_de_un_solo_enlace_en_el_navegador(sitio):
    s = sitio
    _reiniciar_limitadores()
    inv = s.ana.ok(s.ana.post(f"/api/empresas/{s.eb}/invitaciones", {"email": "nuria@ejemplo.test", "rol": "emisor"}))
    obs = recorrido(s, ANCHO + r"""
const page = await abrir(browser, { width: 390, height: 844 });
await page.goto(`${BASE}/#/registro?invitacion=${D.token}`);
await page.getByLabel('Nombre y apellidos').waitFor();
obs.titulo = await texto(page, '#contenido h1');
obs.sin_scroll = await sinScrollHorizontal(page);
await page.getByLabel('Nombre y apellidos').fill('Nuria Ficticia');
await page.getByLabel('Contraseña', { exact: true }).fill('clave-segura-123');
await page.getByLabel('Repita la contraseña').fill('clave-segura-123');
await page.getByLabel(/Declaro estar autorizado/).check();
await page.getByRole('button', { name: 'Crear la cuenta' }).click();
await page.locator('h1:has-text("Documentos")').waitFor();
obs.empresa = await texto(page, '#sel-empresa option:checked');
""", {"token": inv["token"]})
    assert obs["titulo"] == "Crear una cuenta" and obs["sin_scroll"], obs              # antes: «Invitación para una cuenta existente»
    assert "TALLERES FICTICIOS DEL SUR" in obs["empresa"], obs
    m = db.fila(db.conectar(), """SELECT m.rol, m.declaracion_autorizacion_en FROM miembros m JOIN usuarios u ON u.id=m.usuario_id
                                 WHERE u.email='nuria@ejemplo.test'""")
    assert m["rol"] == "emisor" and m["declaracion_autorizacion_en"]
