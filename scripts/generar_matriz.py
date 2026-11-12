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
    "R17": ("Cadena por (obligado, entorno, instalación) para todas las series y años; altas y anulaciones en la misma cadena; tras perder la base y el diario y restaurar una copia, la consulta detecta los registros de esta instalación posteriores al último local (cadena atrasada: se bloquea generar registros) y un administrador reanuda la cadena desde el último registro de la AEAT (el que ningún otro tiene como RegistroAnterior, aunque se generen en el mismo segundo; si no puede saberse, o la AEAT muestra una anulación sin su propia huella, no se reanuda) con un ancla externa auditada, en el diario y que sobrevive a una restauración: nunca se bifurca", "test_caso11_procedimientos (serie ordinaria + rectificativa + anulación en una cadena); tests/test_18_g2_cadena.py::test_fis1_la_cadena_continua_desde_la_aeat_tras_un_desastre, test_fis1_reanudar_consulta_tambien_los_meses_de_facturas_anteriores, test_g2v1_*, test_g2v5_* (simulador; pendiente de contrastar en pruebas que la AEAT devuelve SistemaInformatico, Encadenamiento y la huella de la anulación)", "IMPL+PRUEBA"),
    "R18": ("FechaHoraHusoGenRegistro en Europe/Madrid con desfase; control de reloj no decreciente. Canarias no soportado", "emision._marca_temporal", "IMPL"),
    "R19": ("Fecha de expedición = fecha del día; el borrador fechado otro día exige confirmación; fechas futuras bloqueadas", "Incidencia FECHA_EXPEDICION en test_caso6", "IMPL+PRUEBA"),
    "R20": ("Disparadores SQLite impiden UPDATE/DELETE de facturas y registros; correcciones con registros nuevos", "test_caso11_procedimientos (IntegrityError)", "IMPL+PRUEBA"),
    "R21": ("Sin funciones de borrado/edición de lo expedido; registro de actividad de solo anexado (los errores internos se agregan por minuto sin modificar filas, y consultar la actividad no escribe el minuto en curso; errores e inicios de sesión de las cuentas van en listas aparte, para que nadie pueda desplazar la auditoría); diario JSONL", "Disparadores; test_caso11; tests/test_18_g1_cuentas.py::test_iso3_errores_internos_agregados_sin_desplazar_la_auditoria, test_g1v2_consultar_la_actividad_no_rompe_la_agregacion_ni_desplaza_la_auditoria, test_g1v3_entradas_de_las_cuentas_no_desplazan_la_auditoria_de_la_plataforma", "IMPL+PRUEBA"),
    "R22": ("Cliente SOAP mTLS (remision.transportar), carga cifrada de certificados, interpretación de respuestas; consulta ConsultaFactuSistemaFacturacion paginada, con el mismo transporte y salvaguardas, y comparación con los registros locales (consulta.py); sin IndicadorRepresentante aunque el certificado sea de un tercero (resultado parcial solo tras un 4140); los textos nombran el servicio consultado (simulador local o AEAT)", "Simulado (tests/test_16_s_series_consulta.py; tests/test_18_g2_cadena.py::test_fis3_*, test_fis5_*, test_g2v3_*); sin prueba real por falta de certificado y acceso de red", "PEND-AEAT"),
    "R23": ("Trabajador en segundo plano que remite en cuanto el control de flujo lo permite; despertado al expedir", "test_caso10_recuperacion", "IMPL+PRUEBA"),
    "R24": ("Endpoints del WSDL (normal y sello) por entorno; producción tras interruptor de servidor y requisitos", "config.ENDPOINTS_SOAP; test_entornos_bloqueados_sin_requisitos", "PEND-AEAT"),
    "R25": ("Máx. 1000 registros por envío y un único obligado por mensaje", "remision.procesar (LIMIT max_registros_envio); xmlgen.mensaje_remision", "IMPL"),
    "R26": ("control_flujo.proximo_envio_en = envío (con posible llegada a la AEAT) + TiempoEsperaEnvio; espera inicial 60 s; excepción con 1000 acumulados", "test_caso10_recuperacion (f); test_h3_h4", "IMPL+PRUEBA"),
    "R27": ("respuesta.interpretar: EstadoEnvio, EstadoRegistro, códigos y RegistroDuplicado; estados separados de la factura", "test_caso10, test_caso11", "IMPL+PRUEBA"),
    "R28": ("CSV guardado en envios y registros; petición y respuesta conservadas en el almacén", "test_caso10 (peticion.xml)", "IMPL+PRUEBA"),
    "R29": ("Incidencia=S en envíos posteriores a una incidencia; reintento con espera exponencial ≤ 1 h; orden de generación estricto; aviso con nº de registros pendientes y fecha de inicio en hora peninsular, al día tras conciliar, reintentar o subsanar y en cada recarga de «Remisión»; se puede seguir facturando", "test_caso10_recuperacion; test_05_revision::test_h3_h4_orden_y_control_de_flujo_tras_fallo; tests/test_18_g4_interfaz.py::test_ui2_ui5_banda_de_incidencia_al_dia_y_en_hora_peninsular", "IMPL+PRUEBA"),
    "R30": ("Sin respuesta/5xx/error inesperado → «sin confirmar» con posible entrega y reenvío del mismo XML; fault de servidor → reintento; fault de cliente → «bloqueado» hasta corregir", "test_caso10_recuperacion; test_h2_duplicado_tras_caida_durante_envio; test_h5_fault_de_servidor_se_reintenta", "IMPL+PRUEBA"),
    "R31": ("3000 con estado Correcta/AceptadaConErrores tras un intento de posible entrega de ESE registro → confirmado; en otro caso → rechazado con revisión, conciliable si la consulta a la AEAT devuelve ese mismo registro (misma huella)", "test_caso10; test_h2_duplicado_sin_envio_previo_requiere_revision; tests/test_16_s_series_consulta.py::test_rechazo_3000_tras_restaurar_sin_diario_se_concilia", "IMPL+PRUEBA"),
    "R32": ("emision.subsanar según la tabla oficial: X si no existe en la AEAT, S si se corrige una subsanación rechazada, ausente si se subsana un registro aceptado", "test_caso11_procedimientos; test_h6_h7_indicadores_de_subsanacion_y_anulacion", "IMPL+PRUEBA"),
    "R33": ("Anulación: SinRegistroPrevio=S si no consta alta vigente en la AEAT; RechazoPrevio=S si la anulación previa fue rechazada (combinables)", "test_h6_h7_indicadores_de_subsanacion_y_anulacion; test_h7_anulacion_sin_registro_previo", "IMPL+PRUEBA"),
    "R34": ("Acción «Subsanar» ofrecida para aceptado con errores y rechazados", "api.factura_get (acciones_permitidas)", "IMPL"),
    "R35": ("Validación previa: tipos 21/10/4, S1/01 o exenta E1–E4/E6 (excluyentes, sin tipo ni cuota en la exenta, E2/E3 con clave 02), cuota = base × tipo (tolerancia 0,01 con confirmación), totales", "test_caso4_descuadre_bloquea; test_caso5; tests/test_16_x_exentas.py", "IMPL+PRUEBA"),
    "R36": ("NumSerieFactura 1–60 ASCII 32–126 sin \" ' < > =", "borradores.validar (NUM_FORMATO)", "IMPL"),
    "R37": ("F1 exige destinatario con NIF español válido; IDOtro (extranjero) bloqueado", "test_caso4_escaneado_requiere_revision", "IMPL+PRUEBA"),
    "R38": ("F1, R1 y R4 por diferencias (I); sustitución, F2, F3, R2, R3, R5 no soportados", "test_caso11_procedimientos", "IMPL+PRUEBA"),
    "R39": ("Bloque SistemaInformatico: productor configurable, nombre «FacturaPDF VF», Id «FV», versión, nº instalación, SoloVerifactu=S, MultiOT=S", "XML en docs/evidencias/registro_alta_166-26.xml", "IMPL+PRUEBA"),
    "R40": ("Multiempresa y multiusuario: datos y cadenas separados por empresa; un NIF solo se da de alta una vez en la plataforma (una cadena por obligado y sistema); empresa activa visible; IndicadorMultiplesOT por usuario del SaaS (empresas que gestiona quien genera el registro, no las de toda la plataforma); el NIF se compara también en su forma equivalente (control con dígito o con letra) y solo admite caracteres ASCII (cifras de otros alfabetos → 422; aviso al arrancar si hay filas guardadas así)", "test_caso13_acceso_ajeno; tests/test_16_u_multiusuario.py::test_alta_de_empresas_por_cualquier_usuario_y_nif_unico, test_r2_indicador_multiples_ot_por_usuario_del_saas, test_r3_nif_unico_tambien_con_el_control_alternativo; tests/test_18_g1_cuentas.py::test_iso2_nif_solo_con_caracteres_ascii, test_iso2_aviso_al_arrancar_de_nif_no_ascii_guardados", "IMPL+PRUEBA"),
    "R41": ("Sistema solo VERI*FACTU (sin renuncia ni FechaFinVeriFactu)", "-", "N/A"),
    "R42": ("Modalidad fija VERI*FACTU", "-", "IMPL"),
    "R43": ("No aplica: sistema exclusivamente VERI*FACTU (TipoUsoPosibleSoloVerifactu=S)", "-", "N/A"),
    "R44": ("No aplica: sistema exclusivamente VERI*FACTU", "-", "N/A"),
    "R45": ("Roles admin/emisor/lector por empresa; cada usuario solo accede a las empresas de las que es miembro; el operador de la plataforma tampoco, salvo que sea miembro; quitar miembros (con la sesión abierta, la interfaz avisa y vuelve al inicio; y al revés, una invitación aceptada en otra ventana elige la empresa al volver a abrir una pantalla); las invitaciones solo valen mientras su creador siga siendo administrador; barrido de aislamiento de todas las rutas /api con identificadores (404 estricto y sin cambios en la base; falla si alguna queda sin probar o si se quita el control de acceso de cualquier función que llama a rol()); límites del registro que nadie agota con sus propias invitaciones; las cuentas de la versión anterior eligen contraseña nueva al entrar", "test_caso13_acceso_ajeno; tests/test_16_u_multiusuario.py::test_aislamiento_entre_usuarios_y_operador_sin_membresia, test_r1_admin_degradado_o_desactivado_no_conserva_sus_invitaciones, test_r6_quitar_miembros_y_salir_de_una_empresa; tests/test_18_g1_cuentas.py (test_iso7_*, test_iso1_*, test_res1_cuentas_de_la_version_anterior_deben_cambiar_la_clave); tests/test_18_g4_interfaz.py::test_ui3_quien_pierde_el_acceso_vuelve_al_inicio; tests/test_18_g3_simulador.py::test_iso5_el_modo_de_una_empresa_no_lo_consume_otra (modo del simulador de cada empresa)", "IMPL+PRUEBA"),
    "R46": ("«Mis certificados» (carga única por usuario y asignación a empresas que administra) o certificado de la empresa; titularidad propia o de tercero; legitimación (apoderamiento/colaboración social) declarada por titular con evidencia; Cabecera/Representante en cada envío con certificado de tercero (nombre del certificado; aviso si difiere del declarado); 4112, 4123 y 4124 bloquean el envío con explicación; la AEAT verifica el apoderamiento solo al remitir; el primer envío real con el certificado de un tercero exige el NIF de la empresa verificado por el operador (salvo certificado propio); una restauración conserva la última verificación o retirada (diario)", "tests/test_16_c_certificados.py (XSD con Representante, simulador «no_apoderado» → 4112); tests/test_17_f1_hallazgos.py::test_c_f7_nombre_del_representante_del_certificado_y_ayudas (4123/4124); tests/test_18_g1_cuentas.py::test_iso6_primer_envio_real_con_certificado_de_tercero_exige_nif_verificado, test_g1v4_restaurar_una_copia_no_deshace_una_verificacion_retirada_despues", "PEND-AEAT"),
    "R47": ("Borrador de declaración con los apartados del art. 15.1 OM (apartado d al día: exentas, E2/E3 con clave 02, consulta a la AEAT y plataforma multiusuario del operador); datos del productor pendientes y sin formalizar", "docs/declaracion-responsable-borrador.md; tests/test_18_g4_interfaz.py::test_ui4_operador_de_la_plataforma_y_declaracion", "PEND"),
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
    "R59": ("Series con patrón confirmado, contador por año, correlatividad exigida; serie específica para rectificativas; números con ceros de relleno ({n:3}: «042/A26») leídos con su ancho exacto; avance del contador sobre números ya usados fuera de la instalación (solo hacia delante, confirmado, auditado y en el diario; propuesta con el mes consultado y los siguientes hasta el actual, nunca desde la consulta de un solo número ni si quedan meses sin comprobar; el arranque vuelve a anotarlo si el diario se rehízo); Configuración → Series muestra el siguiente número real", "test_caso9; test_caso11; tests/test_16_s_series_consulta.py; tests/test_17_f2_hallazgos.py::test_sf7_avanzar_el_contador_tras_un_desastre; tests/test_18_g2_cadena.py::test_ui1_*, test_ui8_*, test_res3_*", "IMPL+PRUEBA"),
    "R60": ("Validación de contenido obligatorio (número, fecha, partes con NIF y domicilio, descripción, base, tipo, cuota, total; en las exentas, la mención del art. 6.1.j impresa y comprobada en el PDF)", "borradores.validar; test_flujo_alquiler_exento_hasta_el_simulador", "IMPL+PRUEBA"),
    "R61": ("Factura simplificada (F2) no soportada", "-", "BLOQ"),
    "R62": ("Aviso si se supera el día 16 del mes siguiente a la operación", "borradores.validar (PLAZO_EXPEDICION)", "IMPL"),
    "R63": ("Rectificativa identifica la factura rectificada y motivo; importes por diferencias", "test_caso11_procedimientos", "IMPL+PRUEBA"),
    "R64": ("Duplicados art. 14 ROF no implementados (la descarga reproduce el original expedido)", "-", "PEND"),
    "R65": ("Conservación: original, PDF definitivo (SHA-256 verificado en cada descarga), XML, peticiones/respuestas; copias (0700/0600, solo el usuario del servicio), diario y restauración segura (espacio comprobado antes, sin restos si falla, error explícito si falla tras colocar la base, almacén fusionado fichero a fichero, tiempo lineal)", "test_caso12_copia_restauracion; test_h1_restauracion_con_datos_posteriores_a_la_copia; tests/test_18_g3_copias.py", "IMPL+PRUEBA"),
    "R66": ("El sistema remite (VERI*FACTU)", "-", "IMPL"),
    "R67": ("Sin funciones de alteración/ocultación; inmutabilidad por disparadores", "test_caso11", "IMPL+PRUEBA"),
    "R68": ("Filas exentas E1–E4 y E6 (solas o con sujetas) con causa confirmada por el usuario y mención impresa; E2/E3 con clave 02; E5 bloqueada (destinatarios extranjeros no soportados); propuestas de la extracción (factura mixta solo si el documento respalda la base exenta: base exenta impresa o líneas de la operación exenta, y nunca con tasas, fianzas, indemnizaciones o gastos repercutidos en el documento, con cualquier redacción de la exención)", "tests/test_16_x_exentas.py; tests/test_17_f3_hallazgos.py; tests/test_18_g3_mixtas.py", "IMPL+PRUEBA (simulador; PEND-AEAT)"),
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
