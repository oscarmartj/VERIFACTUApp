"""Genera docs/matriz-requisitos.md a partir de la matriz base normativa y del estado del proyecto.

Estados: IMPL+PRUEBA (implementado y con prueba automática superada, en simulador local salvo indicación),
IMPL (implementado sin prueba automática específica), PEND-AEAT (implementado; falta prueba real contra la AEAT),
PEND (pendiente), BLOQ (caso no soportado: la aplicación lo bloquea), N/A (no aplica a este sistema, con motivo).
"""
import re
from pathlib import Path

R = Path(__file__).resolve().parents[1]
T = "tests/test_03_flujo.py"
M = {
    "R01": ("Fechas de obligación documentadas como configuración (docs/normativa/calendario-y-alcance.md); la app no fija plazos en código", "Revisión documental", "IMPL"),
    "R02": ("Versión 0.1.0 no distribuida a terceros; declaración responsable en borrador", "docs/declaracion-responsable-borrador.md", "PEND"),
    "R03": ("Pregunta SII en el ámbito; si SII=sí, emisión bloqueada (borradores.evaluar_ambito)", "tests/test_01_nucleo.py::test_ambito_emisor", "IMPL+PRUEBA"),
    "R04": ("Territorio foral (País Vasco/Navarra) bloquea la emisión", "tests/test_01_nucleo.py::test_ambito_emisor", "IMPL+PRUEBA"),
    "R05": ("IGIC/IPSI no implementados: Canarias/Ceuta/Melilla bloqueados", "borradores.evaluar_ambito", "BLOQ"),
    "R06": ("Operaciones excluidas no tratadas: casos no soportados bloqueados; solo F1 régimen general", "test_caso5_casos_tributarios", "BLOQ"),
    "R07": ("Aviso en el ámbito cuando los PDF proceden de un programa de facturación; las facturas solo se expiden desde la app", "borradores.evaluar_ambito (origen_pdf)", "IMPL"),
    "R08": ("emision.emitir: una transacción BEGIN IMMEDIATE numera, crea factura inmutable, registro encadenado y lo encola; PDF verificado antes", f"{T}::test_caso6_14_coherencia_y_qr, test_caso9_concurrencia", "IMPL+PRUEBA"),
    "R09": ("xmlgen.registro_alta con los campos del anexo para F1/R1/R4 soportados", "tests/test_01_nucleo.py::test_xml_alta_y_anulacion_validan_xsd", "IMPL+PRUEBA"),
    "R10": ("XML UTF-8 validado contra los XSD vendorizados antes de guardar y antes de remitir", "test_xml_alta_y_anulacion_validan_xsd; test_xml_invalido_detectado", "IMPL+PRUEBA"),
    "R11": ("Solo euros; otras divisas no soportadas (no se detectan automáticamente: revisión humana)", "-", "PEND"),
    "R12": ("Acción «Anular registro» (rol admin) con motivo, confirmación y explicación de su uso excepcional", f"{T}::test_caso11_procedimientos", "IMPL+PRUEBA"),
    "R13": ("Numeración consumida en la transacción; UNIQUE(empresa, entorno, emisor, número); anulada conserva número", "test_caso8_duplicados; test_caso11_procedimientos", "IMPL+PRUEBA"),
    "R14": ("verifactu/huella.py: cadenas oficiales, SHA-256, hex mayúsculas", "tests/test_01_nucleo.py::test_huella_ejemplos_oficiales (3 vectores oficiales)", "IMPL+PRUEBA"),
    "R15": ("PrimerRegistro=S y Huella= vacía en el primer registro de cada cadena", "auditoria.verificar_cadena en test_caso9/10/11", "IMPL+PRUEBA"),
    "R16": ("Encadenamiento con el último registro de la cadena dentro de la transacción serializada", "test_caso9_concurrencia (6 emisiones simultáneas)", "IMPL+PRUEBA"),
    "R17": ("Cadena por (obligado, entorno, instalación) para todas las series y años; altas y anulaciones en la misma cadena", "test_caso11_procedimientos (serie ordinaria + rectificativa + anulación en una cadena)", "IMPL+PRUEBA"),
    "R18": ("FechaHoraHusoGenRegistro en Europe/Madrid con desfase; control de reloj no decreciente. Canarias no soportado", "emision._marca_temporal", "IMPL"),
    "R19": ("Fecha de expedición = fecha del día; el borrador fechado otro día exige confirmación; fechas futuras bloqueadas", "Incidencia FECHA_EXPEDICION en test_caso6", "IMPL+PRUEBA"),
    "R20": ("Disparadores SQLite impiden UPDATE/DELETE de facturas y registros; correcciones con registros nuevos", "test_caso11_procedimientos (IntegrityError)", "IMPL+PRUEBA"),
    "R21": ("Sin funciones de borrado/edición de lo expedido; registro de actividad de solo anexado; diario JSONL", "Disparadores; test_caso11", "IMPL+PRUEBA"),
    "R22": ("Cliente SOAP mTLS (remision.transportar), carga cifrada de certificados, interpretación de respuestas", "Simulado; sin prueba real por falta de certificado y acceso de red", "PEND-AEAT"),
    "R23": ("Trabajador en segundo plano que remite en cuanto el control de flujo lo permite; despertado al expedir", "test_caso10_recuperacion", "IMPL+PRUEBA"),
    "R24": ("Endpoints del WSDL (normal y sello) por entorno; producción tras interruptor de servidor y requisitos", "config.ENDPOINTS_SOAP; test_entornos_bloqueados_sin_requisitos", "PEND-AEAT"),
    "R25": ("Máx. 1000 registros por envío y un único obligado por mensaje", "remision.procesar (LIMIT max_registros_envio); xmlgen.mensaje_remision", "IMPL"),
    "R26": ("control_flujo.proximo_envio_en = envío (con posible llegada a la AEAT) + TiempoEsperaEnvio; espera inicial 60 s; excepción con 1000 acumulados", "test_caso10_recuperacion (f); test_h3_h4", "IMPL+PRUEBA"),
    "R27": ("respuesta.interpretar: EstadoEnvio, EstadoRegistro, códigos y RegistroDuplicado; estados separados de la factura", "test_caso10, test_caso11", "IMPL+PRUEBA"),
    "R28": ("CSV guardado en envios y registros; petición y respuesta conservadas en el almacén", "test_caso10 (peticion.xml)", "IMPL+PRUEBA"),
    "R29": ("Incidencia=S en envíos posteriores a una incidencia; reintento con espera exponencial ≤ 1 h; orden de generación estricto; aviso con nº de registros pendientes; se puede seguir facturando", "test_caso10_recuperacion; test_05_revision::test_h3_h4_orden_y_control_de_flujo_tras_fallo", "IMPL+PRUEBA"),
    "R30": ("Sin respuesta/5xx/error inesperado → «sin confirmar» con posible entrega y reenvío del mismo XML; fault de servidor → reintento; fault de cliente → «bloqueado» hasta corregir", "test_caso10_recuperacion; test_h2_duplicado_tras_caida_durante_envio; test_h5_fault_de_servidor_se_reintenta", "IMPL+PRUEBA"),
    "R31": ("3000 con estado Correcta/AceptadaConErrores tras un intento de posible entrega de ESE registro → confirmado; en otro caso → rechazado con revisión", "test_caso10; test_h2_duplicado_sin_envio_previo_requiere_revision", "IMPL+PRUEBA"),
    "R32": ("emision.subsanar según la tabla oficial: X si no existe en la AEAT, S si se corrige una subsanación rechazada, ausente si se subsana un registro aceptado", "test_caso11_procedimientos; test_h6_h7_indicadores_de_subsanacion_y_anulacion", "IMPL+PRUEBA"),
    "R33": ("Anulación: SinRegistroPrevio=S si no consta alta vigente en la AEAT; RechazoPrevio=S si la anulación previa fue rechazada (combinables)", "test_h6_h7_indicadores_de_subsanacion_y_anulacion; test_h7_anulacion_sin_registro_previo", "IMPL+PRUEBA"),
    "R34": ("Acción «Subsanar» ofrecida para aceptado con errores y rechazados", "api.factura_get (acciones_permitidas)", "IMPL"),
    "R35": ("Validación previa: tipos 21/10/4, S1/01, cuota = base × tipo (tolerancia 0,01 con confirmación), totales", "test_caso4_descuadre_bloquea; test_caso5", "IMPL+PRUEBA"),
    "R36": ("NumSerieFactura 1–60 ASCII 32–126 sin \" ' < > =", "borradores.validar (NUM_FORMATO)", "IMPL"),
    "R37": ("F1 exige destinatario con NIF español válido; IDOtro (extranjero) bloqueado", "test_caso4_escaneado_requiere_revision", "IMPL+PRUEBA"),
    "R38": ("F1, R1 y R4 por diferencias (I); sustitución, F2, F3, R2, R3, R5 no soportados", "test_caso11_procedimientos", "IMPL+PRUEBA"),
    "R39": ("Bloque SistemaInformatico: productor configurable, nombre «FacturaPDF VF», Id «FV», versión, nº instalación, SoloVerifactu=S, MultiOT=S", "XML en docs/evidencias/registro_alta_166-26.xml", "IMPL+PRUEBA"),
    "R40": ("Multiempresa: datos y cadenas separados por empresa; empresa activa visible; IndicadorMultiplesOT según nº de empresas", "test_caso13_acceso_ajeno", "IMPL+PRUEBA"),
    "R41": ("Sistema solo VERI*FACTU (sin renuncia ni FechaFinVeriFactu)", "-", "N/A"),
    "R42": ("Modalidad fija VERI*FACTU", "-", "IMPL"),
    "R43": ("No aplica: sistema exclusivamente VERI*FACTU (TipoUsoPosibleSoloVerifactu=S)", "-", "N/A"),
    "R44": ("No aplica: sistema exclusivamente VERI*FACTU", "-", "N/A"),
    "R45": ("Roles admin/emisor/lector por empresa; sin acceso a otras empresas", "test_caso13_acceso_ajeno", "IMPL+PRUEBA"),
    "R46": ("Certificado de la empresa, de representante o de tercero con apoderamiento/colaboración declarados; campo Representante no implementado", "certificados.evaluar_legitimacion", "PEND-AEAT"),
    "R47": ("Borrador de declaración con los apartados del art. 15.1 OM; datos del productor pendientes", "docs/declaracion-responsable-borrador.md", "PEND"),
    "R48": ("Página «Declaración responsable» en la app y documento en docs/", "api /declaracion", "IMPL"),
    "R49": ("Tabla declaraciones por versión; conservación en docs/ por versión", "-", "IMPL"),
    "R50": ("Componentes de terceros identificados (bibliotecas de código abierto, sin servicios externos de envío)", "docs/productor-y-componentes.md", "IMPL"),
    "R51": ("QR en todas las facturas expedidas (solo F1/R1/R4 soportadas)", "test_caso6_14_coherencia_y_qr", "IMPL+PRUEBA"),
    "R52": ("QR ISO/IEC 18004 (segno), nivel M forzado, 32×32 mm (30 mm en superposición si falta hueco), 6 mm de blanco (2,5 mín.)", "tests/test_04_pdf.py; evidencia PNG", "IMPL+PRUEBA"),
    "R53": ("URL con nif, numserie, fecha, importe codificados; prewww2 para simulador/pruebas, www2 en producción", "test_url_qr; QR decodificado del PDF", "IMPL+PRUEBA"),
    "R54": ("QR al principio, centrado arriba, una sola vez en la 1.ª página; superposición solo en el 30 % superior", "test_caso6_14 (qr_una_sola_vez); test_superposicion_cabecera", "IMPL+PRUEBA"),
    "R55": ("«QR tributario:» encima y «VERI*FACTU» debajo, 10 pt negrita (≥ resto de datos)", "Evidencia factura_simulada_166-26_p1.png", "IMPL+PRUEBA"),
    "R56": ("No se generan facturas electrónicas estructuradas (Facturae/UBL)", "-", "N/A"),
    "R57": ("Borradores y proformas sin QR (hueco reservado) ni registro; conservados con su original e historial", "test_caso6_14 (vista previa sin QR)", "IMPL+PRUEBA"),
    "R58": ("Pruebas en simulador local o entorno de pruebas AEAT con series y QR de pruebas separados; en producción no hay modo de prueba", "test_entornos_bloqueados_sin_requisitos", "IMPL+PRUEBA"),
    "R59": ("Series con patrón confirmado, contador por año, correlatividad exigida; serie específica para rectificativas", "test_caso9; test_caso11", "IMPL+PRUEBA"),
    "R60": ("Validación de contenido obligatorio (número, fecha, partes con NIF y domicilio, descripción, base, tipo, cuota, total)", "borradores.validar", "IMPL+PRUEBA"),
    "R61": ("Factura simplificada (F2) no soportada", "-", "BLOQ"),
    "R62": ("Aviso si se supera el día 16 del mes siguiente a la operación", "borradores.validar (PLAZO_EXPEDICION)", "IMPL"),
    "R63": ("Rectificativa identifica la factura rectificada y motivo; importes por diferencias", "test_caso11_procedimientos", "IMPL+PRUEBA"),
    "R64": ("Duplicados art. 14 ROF no implementados (la descarga reproduce el original expedido)", "-", "PEND"),
    "R65": ("Conservación: original, PDF definitivo (SHA-256 verificado en cada descarga), XML, peticiones/respuestas; copias, diario y restauración segura", "test_caso12_copia_restauracion; test_h1_restauracion_con_datos_posteriores_a_la_copia", "IMPL+PRUEBA"),
    "R66": ("El sistema remite (VERI*FACTU)", "-", "IMPL"),
    "R67": ("Sin funciones de alteración/ocultación; inmutabilidad por disparadores", "test_caso11", "IMPL+PRUEBA"),
}

base = (R / "docs/normativa/matriz-requisitos-base.md").read_text(encoding="utf-8")
out = ["# Matriz de requisitos con evidencias", "",
       "Consulta normativa: 2026-11-09 (ver `docs/normativa/fuentes.md`). Estado del código: versión 0.1.0.", "",
       "**Leyenda de estados:** IMPL+PRUEBA = implementado y con prueba automática superada (en el **simulador local**, "
       "salvo que se indique); IMPL = implementado sin prueba automática específica; PEND-AEAT = implementado, falta la "
       "prueba real contra el entorno de pruebas de la AEAT; PEND = pendiente; BLOQ = caso no soportado y bloqueado; "
       "N/A = no aplica a este sistema.", "",
       "> Un XML que valida localmente o un QR que se puede escanear no acreditan por sí solos el cumplimiento: "
       "ninguna prueba se ha ejecutado todavía contra los servicios de la AEAT.", "",
       "| Requisito | Fuente oficial | Funcionalidad que lo cumple | Prueba o evidencia | Estado |", "|---|---|---|---|---|"]
for linea in base.splitlines():
    m = re.match(r"^\| (R\d+) — (.*?) \| (.*?) \| .*$", linea)
    if not m:
        continue
    k = m.group(1)
    f, e, s = M.get(k, ("pendiente", "pendiente", "PEND"))
    out.append(f"| {k} — {m.group(2)} | {m.group(3)} | {f} | {e} | {s} |")
(R / "docs/matriz-requisitos.md").write_text("\n".join(out) + "\n", encoding="utf-8")
cuenta = {}
for k in M.values():
    cuenta[k[2]] = cuenta.get(k[2], 0) + 1
print("filas:", len(out) - 10, cuenta)
