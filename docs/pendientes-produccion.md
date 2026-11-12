# Pendientes antes de usar la aplicación en producción

La aplicación **bloquea** la producción hasta que se cumplen los requisitos que puede comprobar (Configuración →
«Paso a producción», `GET /api/empresas/{id}/produccion`). Además, quedan estas tareas:

## Bloqueantes

1. **Modelos reales de la empresa**: se han probado 9 facturas reales de 5 emisores (solo como prueba privada,
   nunca se emiten ni se remiten: `tests/privado/`, excluido del repositorio). La primera medición dio un 69,6 % de
   campos (2 de 9 perfectas), muy por debajo de los corpus sintéticos; tras corregir las causas generales, 100 %.
   Cada modelo nuevo puede traer formatos no vistos: antes de producción conviene probar una muestra de los PDF
   **preparatorios** reales de cada empresa que vaya a usar la aplicación (sin emitirlos).
2. **Productor del sistema**: designar a la persona o entidad responsable, completar sus datos en la aplicación y en la declaración responsable, y que esta la **suscriba** (no puede hacerlo una herramienta de IA ni firmarse en su nombre).
3. **Esquemas oficiales**: contrastar los XSD/WSDL/catálogo de errores con la AEAT (`python scripts/verificar_esquemas.py`) desde una red con acceso a la sede. Se obtuvieron de una réplica pública con sumas SHA-256 declaradas (ver `app/verifactu/esquemas/PROCEDENCIA.md`).
4. **Certificado y legitimación** (gestión implementada; falta el certificado real): certificado electrónico de la empresa (sello o persona jurídica) o de su representante, o de un tercero con apoderamiento o colaboración social en la AEAT. Se carga en la aplicación (formulario seguro, nunca en el chat), por empresa o una sola vez en **«Mis certificados»** y asignado a las empresas que el usuario administra. La aplicación comprueba titularidad o representación; con el certificado de un tercero exige una legitimación declarada **para ese titular** con evidencia y envía `Cabecera/Representante`. El apoderamiento **solo lo verifica la AEAT al remitir** (error 4112: los registros quedan bloqueados hasta corregirlo y reintentar).
5. **Prueba real en el entorno de pruebas de la AEAT** (`prewww1.aeat.es`): no se ha podido ejecutar porque (a) desde el entorno de desarrollo la AEAT corta las conexiones TLS y (b) no se dispone de certificado. Pasos: `VERIFACTU_PERMITIR_PRUEBAS_AEAT=1`, certificado de pruebas, serie de pruebas (p. ej. `PRB-{n}`), datos sintéticos, y verificar: alta aceptada, duplicado (3000), subsanación, anulación, rechazo por datos (p. ej. tipo no permitido) y control de flujo con `TiempoEsperaEnvio` real. Comprobar también el cotejo del QR en `prewww2.aeat.es`. En la **consulta**
   (`ConsultaFactuSistemaFacturacion`), contrastar: (a) que con el certificado de un tercero (apoderado o colaborador social) la
   consulta **sin** `IndicadorRepresentante` devuelve todos los registros del obligado, y qué error da la AEAT si el tercero no
   puede consultarlos así (la aplicación supone 4140, «no está apoderado en los trámites necesarios», y solo entonces repite con
   `IndicadorRepresentante=S`, marcando el resultado como parcial); (b) que con `IndicadorRepresentante=S` solo devuelve los
   registros que presentó ese representante (lo dice el XSD; el simulador lo reproduce); (c) que con
   `MostrarSistemaInformatico=S` la respuesta trae el bloque `SistemaInformatico` (`IdSistemaInformatico`, `NumeroInstalacion`)
   y `FechaHoraHusoGenRegistro` de cada registro: la detección de la **cadena atrasada** y «Reanudar la cadena desde el último
   registro de la AEAT» se basan en ellos (sin `SistemaInformatico`, un registro de otro programa del mismo obligado generado
   después del último registro local bloquearía la expedición; sin `FechaHoraHusoGenRegistro`, un registro no se tiene en
   cuenta para la cadena); (d) que la AEAT acepta un registro cuyo `RegistroAnterior` es el último que ella tiene aunque la
   instalación lo haya recuperado de la consulta (cadena reanudada tras restaurar una copia sin el diario); y (e) los tiempos
   reales: según el XSD, `MostrarSistemaInformatico=S` alarga la respuesta, la propuesta de avance del contador consulta
   también los meses siguientes (hasta 24) y «Reanudar la cadena» vuelve a consultar todos los meses desde el último registro
   local y los de las facturas locales (hasta 120); y (f) qué `Huella`, `FechaHoraHusoGenRegistro` y `Encadenamiento` devuelve
   la consulta para una **factura anulada** (y para una subsanada): la aplicación toma el último registro de la cadena de esos
   datos (el que ningún otro tiene como `RegistroAnterior`; el simulador devuelve los de la anulación). Si la AEAT devolviera
   los del alta, una anulación perdida no se vería: la aplicación lo avisa (`anulacion_sin_huella`) y no reanuda la cadena
   mientras ocurra, así que «Reanudar la cadena» no sirve tras anular facturas anteriores a la copia hasta contrastarlo. El
   simulador no valida el encadenamiento (acepta cualquier `RegistroAnterior`): que la cadena no se bifurque lo comprueban las
   pruebas, no el simulador.
6. **Revisión del calendario**: el RDL 15/2025 fija el 01/01/2027 (contribuyentes del IS) y el 01/07/2027 (resto). La «Comunicación del Ministerio de Hacienda de 05/10/2026» anuncia una posible ampliación que **no** está publicada como norma: revisar el BOE antes de fijar fechas.

## Recomendados

7. Despliegue con HTTPS y acceso restringido (VPN o lista de IP), `VERIFACTU_COOKIE_SEGURA=1`, copias diarias externas, diario en otro volumen y clave maestra custodiada aparte.
8. Sincronización horaria (NTP) del servidor: `FechaHoraHusoGenRegistro` debe ser la hora real de generación.
9. ~~Procedimiento guiado de consulta a la AEAT~~ **Implementado** (pantalla *Consulta AEAT*, `app/consulta.py`): consulta paginada, comparación con los registros locales y conciliación segura con confirmación; probado contra el simulador (`tests/test_16_s_series_consulta.py`). Falta probarlo en el entorno de pruebas de la AEAT junto con el punto 5 (paginación real con más de 10.000 registros, códigos de error de la consulta y huella que devuelve la AEAT para una factura anulada). Con certificado de un tercero legitimado la consulta usa ese certificado pero va **sin** `IndicadorRepresentante` (según el XSD, el indicador limita la respuesta a los registros que presentó el representante); solo tras un 4140 se repite con él y el resultado se marca como parcial (`tests/test_18_g2_cadena.py`; contrastar en el punto 5). Si la consulta muestra números ya usados en la AEAT que aquí no constan, un administrador puede avanzar el contador de la serie (auditado y anotado en el diario; la propuesta cubre el mes consultado y los siguientes del año hasta el actual) y, si la AEAT tiene registros de esta instalación posteriores al último local, reanudar la cadena desde el último de ellos.
10. ~~Representante en la cabecera (`Cabecera/Representante`) cuando remita un tercero distinto del obligado.~~ **Implementado**: se añade cuando el certificado en uso es de un tercero legitimado (validado contra el XSD y probado en el simulador, también los rechazos 4112, 4123 y 4124 con los modos «no_apoderado», «representante_nif_no_censado» y «representante_nombre_no_censado»). El nombre del representante se toma del certificado (O; «APELLIDOS NOMBRE» en persona física) y se avisa si difiere del declarado. Falta contrastarlo en el entorno de pruebas de la AEAT con un apoderamiento real (punto 5), en particular cómo compara la AEAT el nombre con su censo (4124). Las consultas usan el mismo certificado y la misma regla (`certificados.para_envio`, también en el simulador), pero sin `IndicadorRepresentante` salvo tras un 4140 (punto 9).
11. Política de purga de borradores descartados (tarea programada).
12. Casos no soportados (bloqueados hoy): inversión del sujeto pasivo, no sujetas, recargo de equivalencia, suplidos, simplificadas (F2), sustitutivas (F3), rectificativas por sustitución, IGIC/IPSI, destinatarios extranjeros (y con ellos las entregas intracomunitarias exentas E5), otras divisas, duplicados del art. 14 ROF.
    - **Operaciones exentas: soportadas** (11/11/2026) con las causas E1, E2, E3, E4 y E6, solas o junto a operaciones
      sujetas (`docs/arquitectura.md`, «Operaciones exentas»). La causa la elige o confirma siempre el usuario y la mención
      legal se imprime en la factura. Pendiente: contrastar en el entorno de pruebas de la AEAT un alta con E1 y otra con
      E2/E3 y clave de régimen 02 (las reglas 1199, 1238, 1286 y 1289 solo se han probado con el simulador local).
    - La extracción separa por sí sola las bases de una factura **mixta** solo si el documento cita el artículo de la Ley
      del IVA y el cuadro de totales imprime, en renglones con su cifra, la base exenta («Base exenta 650 / Base 21 % 100 /
      IVA 21 / TOTAL 771»), la base imponible total («Base imponible 750 / Base 21 % 100») o la base de la cuota
      («I.V.A. 21 % s/ 100,00»), todo cuadra al céntimo y el documento respalda la diferencia (base exenta impresa, o
      líneas de la operación exenta que la suman exactamente). Con las cifras en columnas bajo sus rótulos, una «Suma» que
      la extracción lee como concepto, varios tipos cuyas bases no lee, una exención sin artículo o una diferencia sin
      respaldo, el borrador queda bloqueado por descuadre y el usuario separa las bases en el editor. Las salvaguardas
      contra suplidos, tasas, fianzas, depósitos de garantía, indemnizaciones o gastos repercutidos dependen de que el
      documento use esas palabras (detección por palabras clave): la confirmación de la causa cita las líneas de las que
      sale la base exenta para que el usuario lo compruebe y, en una factura sin IVA, el renglón con esas palabras.
13. Escaneos de mala calidad: el OCR local acierta ~90 % de los campos en escaneos simulados; los valores dudosos se marcan para revisión. Opcional: IA de extracción solo como propuesta que el usuario confirma, con consentimiento expreso y valorando el envío de datos a terceros.
14. Limitaciones conocidas de la recuperación: el diario se lee completo en cada arranque (adecuado para el volumen de una pyme; para volúmenes altos, índice o puntos de control en el manifiesto de la copia); los certificados cargados por empresa, las autorizaciones de producción y las series confirmadas sin uso posteriores a la última copia deben volver a introducirse tras restaurar («Mis certificados», sus asignaciones, las legitimaciones con su documento de evidencia y el certificado de la empresa en uso sí se recuperan del diario, pero el archivo de un certificado posterior a la copia debe volver a cargarse: el diario nunca lo guarda; la restauración lista los certificados de empresa por recargar y las legitimaciones que quedaron sin evidencia); una línea del diario ilegible exige revisión manual y `forzar=True` para restaurar. Si se pierden **a la vez** la base de datos y el diario, lo expedido después de la última copia solo se recupera de la AEAT: la consulta lo muestra, avisa de la cadena atrasada y bloquea la expedición hasta reanudar la cadena desde el último registro de la AEAT y avanzar los contadores; las facturas, sus PDF y documentos de esos días no se recuperan (solo sus registros constan en la AEAT). Si se pierde solo el diario, el arranque lo rehace desde la base de datos (incluidos los avances manuales de contador y el estado de la cadena respecto a la AEAT), pero no la copia espejo de los PDF y originales.
15. Despliegue de varias instancias: el diseño asume un único proceso de aplicación (SQLite y un remitente por empresa y entorno). Para varias instancias, migrar a PostgreSQL y renovar el bloqueo del remitente.
16. **Límites conocidos del extractor** (cuarta verificación adversarial, 10/11/2026). Se corrigieron los 23 hallazgos
    confirmados y lo que salió en dos rondas de reparación, pero los verificadores siguieron construyendo variantes. Las
    abiertas son maquetas hechas a propósito; ninguna inventa datos ni permite expedir sin que el usuario revise y
    confirme el borrador, pero algunas proponen un valor erróneo **sin marcarlo en ámbar**. Por eso la revisión del
    borrador frente al original sigue siendo imprescindible.
    - **Partes**: nombre comercial solo en el membrete (razón social y CIF solo en el pie) tomado como destinatario; un
      tercero calificado antes del rótulo del NIF («Cesionario CIF: …», «Pago a favor de: CIF …») bajo el bloque del
      cliente; NIF del cliente con una letra O o I en lugar de una cifra junto a un tercero presentado como «Obra
      promovida por»; dirección de envío impresa tras el CIF del cliente que sustituye al domicilio fiscal; razón social
      del cliente separada dos renglones del resto de su bloque.
    - **Números e importes**: referencia a otra factura impresa por encima o a la altura del número propio cuando este
      tiene un rótulo débil; varios tipos de IVA con una sola base impresa y la cuota del tipo reducido con un rótulo no
      reconocido («IVA reducido», «Imp. 10 %»): la base y la cuota proponen solo las del tipo general (lo detecta el
      descuadre del total, que bloquea).
    - **Total**: un total impreso que suma tres o más recargos (tasas, portes, envases) o los cita sin decimales puede
      no detectarse y proponerse base + cuota como total.
    - **Retención de garantía** redactada con otras fórmulas: se pide la confirmación genérica de la retención y, si el
      usuario la confirma, el PDF la rotula como IRPF.
    - **Texto oculto**: texto invisible sobre una imagen de fondo con contenido (banda de pie, trama de seguridad)
      manda la página entera al OCR y pierde campos de una factura legítima; sigue habiendo variantes de fuentes
      cuyo /ToUnicode no dice lo que dibujan (se contrastan las cifras con la imagen, no las letras).
    - **Confirmaciones de más** en facturas correctas: nombres de cliente con minúsculas antes de la sigla y la forma
      jurídica; cabecera con razón social sin NIF; código de cliente suelto tomado por NIF ilegible; resúmenes «a
      origen / anteriores» o «saldo pendiente» (TOTAL_OTRA_CIFRA); renglones de pie sin punto unidos en una frase de
      garantía; desglose con filas a cero de otros tipos; tablas monoespaciadas con filetes verticales entre cifras;
      descuento por línea con solo «Concepto | Precio | Importe» (bloqueo LINEA_SIN_CANTIDAD: hay que indicar la
      cantidad).
    - **Coste**: un PDF hostil de 30 páginas tarda unos 35 s en extraerse (límite del proceso hijo: 120 s); una factura
      de 2.500 renglones tarda unos 12 s en expedirse y por encima de 3.000 se bloquea (DOCUMENTO_DEMASIADO_LARGO).

## Despliegue público multiusuario

La aplicación ya admite muchos usuarios independientes (registro cerrado por defecto o abierto, invitaciones,
aislamiento por empresa, panel del operador). Antes de abrirla al público en Internet falta:

17. **Verificación del correo** al registrarse y **recuperación de la contraseña por correo**: no hay envío de correos.
    Hoy cualquiera puede registrar una cuenta con un correo ajeno (no da acceso a ninguna empresa, pero ocupa ese
    correo) y quien olvida su contraseña depende de que el operador le fije una temporal, lo que le permitiría entrar
    como esa persona (hoy se mitiga anotándolo en la actividad de cada empresa de la cuenta y mostrándolo en «Mi
    cuenta»; con la recuperación por correo el operador ya no necesitaría conocer ninguna contraseña). Con verificación por correo,
    además, el registro dejaría de revelar si un correo ya tiene cuenta (hoy las respuestas son uniformes y limitadas,
    pero un alta que sale bien indica que el correo estaba libre).
18. **Condiciones de uso y contrato de encargado del tratamiento** (art. 28 RGPD) con cada usuario o empresa: el
    operador de la plataforma trata por cuenta de cada empresa sus facturas, clientes y certificados. Falta la
    aceptación expresa en el registro, el registro de actividades de tratamiento, la política de privacidad, el
    procedimiento de ejercicio de derechos y la baja de cuentas y empresas con conservación de lo expedido durante el
    plazo legal.
19. **Supervisión de abusos**: los límites de registro son en memoria y por proceso (se reinician al reiniciar y no
    se comparten entre instancias); los cupos de invitaciones y de altas de empresas por usuario sí están en la base de
    datos. Faltan alertas sobre altas masivas, inicios de sesión fallidos y volumen de
    importación por usuario, límites de almacenamiento por cuenta, CAPTCHA o similar si el registro está abierto, y
    un procedimiento para **reclamar un NIF** dado de alta por quien no representa a esa empresa (impide que la empresa
    legítima se dé de alta: el operador tendría que verificar la representación, p. ej. con el certificado de la
    empresa, y transferirla). Situación actual con los certificados de terceros: quien da de alta una empresa con un NIF
    ajeno puede usar en ella el certificado de un tercero (también el suyo propio, de «Mis certificados» o cargado en la
    empresa, p. ej. el sello de su gestoría) con una legitimación que **declara él mismo**; la aplicación solo comprueba
    que la evidencia sea un documento de esa empresa, no su contenido. Ni la aplicación ni el resto de requisitos de la
    pantalla Producción (que el propio usuario cumple como administrador) lo impedirían: solo lo frena la AEAT al
    remitir (4112), y a un colaborador social no le pide una autorización por cada obligado. Como protección, el primer
    envío a la AEAT (pruebas o producción, también la consulta) con un certificado que no es propio de la empresa exige
    que el operador haya **verificado su NIF** en el panel «Plataforma» (queda anotado), salvo para las empresas que dio
    de alta él (también las de la versión anterior) o si la AEAT ya aceptó un envío de la empresa con su propio
    certificado. El operador debe comprobar la representación con documentación antes de marcarlo; ese procedimiento
    (qué documentos pide, cómo y quién lo revisa, cuándo se retira la marca) está pendiente de definir. El simulador
    local no lo exige.
20. **Segundo factor de autenticación** para el operador y para los administradores de empresas que remitan a la AEAT,
    y caducidad de sesiones por inactividad (hoy, 12 h desde el inicio).
21. Detrás de un proxy inverso, arrancar uvicorn con `--proxy-headers --forwarded-allow-ips=<IP del proxy>` para que los
    límites por dirección usen la IP real del cliente.
