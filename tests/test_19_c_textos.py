"""Carril C — lenguaje llano y formatos españoles en la SPA (sin navegador): menú principal, entornos y estados del envío
renombrados, fechas «12/11/2026» e importes «1.028,50 €», importes de los campos editables con coma y acciones de la
actividad en palabras. Las funciones se evalúan con Node (si está instalado) sobre el propio app.js."""
import json
import re
import shutil
import subprocess

import pytest
from conftest import RAIZ

APP_JS = RAIZ / "app" / "static" / "app.js"
JS = APP_JS.read_text(encoding="utf-8")


def _bloque(inicio: str, fin: str = "\n};\n") -> str:
    i = JS.index(inicio)
    return JS[i:JS.index(fin, i)]


def test_menu_principal_y_mas():
    nav = _bloque("const NAVEGACION = [", "\n];\n")
    assert re.findall(r"\['(/[a-z-]+)', '([^']+)'\]", nav) == [("/importar", "Documentos"), ("/facturas", "Facturas"),
                                                               ("/configuracion", "Configuración")]
    mas = _bloque("const NAVEGACION_MAS = [", "\n];\n")
    for ruta in ("/remision", "/consulta-aeat", "/actividad", "/mis-certificados", "/declaracion"):
        assert f"'{ruta}'" in mas, ruta                                      # nada se quita: se mueve a «Más»
    assert "'/consulta-aeat', 'Consulta a Hacienda', { avanzado: true, rol: 'emisor' }" in mas
    assert "'Más'" in JS and "Declaración responsable') : null" in JS       # también en el pie


def test_textos_renombrados_y_sin_codigos():
    ent = _bloque("const ENTORNOS = {")
    assert "Simulador local" not in ent and "'Modo práctica: no se envía a Hacienda'" in ent
    assert "'Registro aceptado'" not in JS and "'Aceptada en la simulación'" in JS and "'Aceptada por Hacienda'" in JS
    assert "Empezar a enviar a Hacienda" in JS and "Corregir esta factura" in JS and "'Pendiente de revisar'" in JS
    assert "Uso el certificado de mi gestoría o asesor" in JS and "Legitimación de un tercero (representante)" not in JS
    assert "dateStyle: 'short'" not in JS                                    # «12/11/26» no: año con cuatro cifras
    assert "h('code', null, e.accion" not in JS                             # la acción de la actividad, en palabras
    # «Abrir el cotejo en la sede de la AEAT» nunca en el modo práctica
    i = JS.index("'Abrir el cotejo en la sede de la AEAT'")
    assert "!simulada && " in JS[i - 200:i]
    assert ".innerHTML" not in JS and "insertAdjacentHTML" not in JS and "style=" not in JS and " onclick=\"" not in JS


@pytest.mark.skipif(shutil.which("node") is None, reason="sin Node.js")
def test_formatos_espanoles(tmp_path):
    arnes = tmp_path / "formatos.js"
    arnes.write_text("""const fs = require('fs'); const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const stub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : stub()), apply: () => stub(), construct: () => stub() });
const ctx = { console, document: stub(), window: stub(), location: stub(), navigator: stub(), localStorage: stub(), sessionStorage: stub(),
  fetch: () => new Promise(() => {}), setTimeout: () => 0, clearTimeout: () => 0, setInterval: () => 0, clearInterval: () => 0,
  addEventListener: () => {}, history: stub(), Intl, URL, URLSearchParams, matchMedia: stub() };
vm.createContext(ctx);
vm.runInContext(src + '\\n;globalThis.__f = { fmtFechaHora, fmtFecha, fmtImporte, valorControl, textoEstadoEnvio, accionActividad, textoTipoFactura };', ctx);
const f = ctx.__f;
console.log(JSON.stringify([
  f.fmtFechaHora('2026-11-12T09:05:00Z'), f.fmtFecha('2026-11-12'), f.fmtImporte('1028.50'), f.fmtImporte('-1028.5'),
  f.valorControl('importe', '1028.50'), f.valorControl('decimal', '21.00'), f.valorControl('importe', '-1234567.00'),
  f.valorControl('texto', '1028.50'), f.valorControl('importe', ''),
  f.textoEstadoEnvio('aceptado', 'simulador'), f.textoEstadoEnvio('aceptado', 'produccion'), f.textoEstadoEnvio('bloqueado', 'pruebas'),
  f.accionActividad('factura.expedida'), f.accionActividad('algo_nuevo.de_prueba'), f.textoTipoFactura('R4'), f.textoTipoFactura('F1'),
]));
""", encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(arnes), str(APP_JS)], capture_output=True, text=True, timeout=60,
                                    check=True).stdout)
    assert out == ["12/11/2026, 10:05", "12/11/2026", "1.028,50 €", "−1.028,50 €",
                   "1.028,50", "21,00", "-1.234.567,00", "1028.50", "",
                   "Aceptada en la simulación", "Aceptada por Hacienda", "Bloqueada: requiere su acción",
                   "Factura emitida", "Algo nuevo de prueba", "Factura rectificativa (corrige otra)", "Factura completa"], out
