# Arquitectura, seguridad y costes

## Decisiones

| Decisión | Motivo | Efecto en coste, mantenimiento y fiabilidad |
|---|---|---|
| **Python 3.13 + FastAPI** en un único servicio | Ecosistema maduro de PDF/XML (lxml, pdfplumber, pypdf, reportlab) y validación XSD nativa | Un solo proceso que mantener; sin dependencias de pago |
| **SQLite en modo WAL** con `BEGIN IMMEDIATE` | El volumen de una pyme es bajo; la escritura serializada da numeración y encadenamiento sin carreras; copia consistente con la API de *backup* | Coste nulo. Límite: una sola instancia de escritura. Si crece, migrar a PostgreSQL con `SELECT … FOR UPDATE` sobre `cadenas` (el código ya concentra la sección crítica en `emision.emitir`) |
| **Cola persistente = tabla de registros** | El estado de la comunicación sobrevive a reinicios sin un broker adicional | Sin Redis/colas externas. Recuperación tras reinicio en `remision.recuperar_interrumpidos` |
| **Trabajador en hilo** dentro del servicio, con bloqueo con caducidad por empresa/entorno | Un remitente por cadena; si hubiera dos procesos, el bloqueo en `control_flujo` evita envíos simultáneos | Despliegue simple; no se necesita otro contenedor |
| **OCR local (Tesseract) y extracción por reglas geométricas genéricas** (sin plantillas por emisor) | Los documentos no salen del servidor; resultados deterministas y auditables; funciona con diseños nuevos sin configurarlos | Coste nulo. Medido con un corpus de diseños variados (`docs/pruebas.md`): ~100 % en PDF con texto, ~89 % de campos en escaneos simulados (lo dudoso se marca y exige revisión) |
| **Sin IA externa** | Las reglas fiscales deben ser comprobables y deterministas. Ningún dato se envía a terceros | Si en el futuro se añade IA para extracción, solo podrá proponer valores que el usuario confirme (la validación sigue siendo determinista) |
| **Frontend estático sin compilación** (HTML + JS) servido por el mismo servicio | CSP estricta (`script-src 'self'`), sin CDN | Menos superficie de ataque; sin cadena de *build* |
| **No se usa Vercel/serverless** | Se necesita disco persistente, proceso de remisión continuo y certificado cliente TLS (mTLS) | Despliegue recomendado: un VPS en la UE con Docker y HTTPS |

## Componentes

```
navegador ──HTTPS──> FastAPI (app/api.py) ──> SQLite (data/verifactu.sqlite3)
                         │                     almacén privado (data/almacen/…)
                         │                     diario JSONL (VERIFACTU_DIARIO_DIR, otro disco)
                         ├─ importación: inspección (qpdf, pypdf), rasterizado (poppler), OCR (tesseract), QR (zbar)
                         ├─ borradores: clasificación, extracción con procedencia, validación determinista
                         ├─ emisión: transacción única → factura inmutable + registro encadenado + PDF verificado
                         ├─ trabajador de remisión ──mTLS SOAP──> AEAT (pruebas / producción)
                         │                         └─ o simulador local (app/simulador.py)
                         └─ consulta a la AEAT (app/consulta.py): mismo transporte y salvaguardas, solo lectura
```

## Modelo de datos (resumen; detalle en `app/migraciones/0001_inicial.sql`)

- `usuarios` (con `superadmin`, `cambiar_clave`, `version` y `alta_origen`), `sesiones`, `miembros` (rol admin/emisor/lector por empresa y declaración de autorización), `invitaciones` (SHA-256 del token, empresa y rol, caducidad, uso y anulación) y `plataforma` (ajustes versionados, p. ej. `registro_abierto`).
- `empresas` (NIF, razón social y **ámbito del emisor**: contribuyente, territorio, SII, exclusiones, origen de los PDF).
- `productor` (identidad del productor del sistema y número de instalación), `instalaciones`.
- `series` + `series_contadores` (patrón confirmado, correlatividad por año).
- `documentos` (original intacto + SHA-256, inspección, clasificación confirmada, bloqueo de emisión) y `extracciones` (valor, página, posición, método, confianza por campo).
- `borradores` (preparación) y `cambios_borrador` (historial de correcciones con motivo).
- `facturas` (versión inmutable de los datos aprobados, SHA-256, URL del QR, PDF con SHA-256).
- `cadenas` (último registro por obligado/entorno/instalación) y `registros` (XML, huella, cadena exacta de la huella, **estado de la comunicación** y, si la cadena se reanudó desde la AEAT, `ancla_aeat`: el registro de la AEAT al que se encadenó).
- `cadenas_aeat` (migración 0007): por empresa y entorno, cadena **atrasada** respecto a la AEAT (la consulta encontró registros de esta instalación posteriores al último local; bloquea generar registros) o **anclada** a su último registro (el siguiente registro lo usa como anterior y la fila desaparece).
- `envios`, `envio_lineas`, `control_flujo` (TiempoEsperaEnvio, incidencia, bloqueo del remitente).
- `certificados` (por empresa: PKCS#12 cifrado AES-256-GCM, titular, representado, legitimación; su estado «en uso» se anota con versión en el diario), `certificados_usuario` («Mis certificados»: propietario, titular, archivo cifrado o eliminado), `certificado_asignaciones` (certificado de un usuario en uso por una empresa y entorno; una activa como máximo), `legitimaciones` (apoderamiento o colaboración social **por titular** del certificado, con evidencia y revocación), `autorizaciones_produccion`, `declaraciones`, `actividad`.
- **Disparadores** impiden borrar facturas o registros y modificar sus campos fiscales.

Migraciones: ficheros `app/migraciones/NNNN_*.sql`, aplicados en orden al arrancar y anotados en la tabla `migraciones`.
`0008_indices_restauracion.sql` añade los índices `registros(factura_id, secuencia)` y `facturas(rectifica_factura_id)`
(estado de cada factura, anulación y restauración sin recorrer las tablas enteras).

## Estados

- **Factura:** borrador / requiere revisión (en `borradores`) → emitida → rectificada / anulada.
- **Comunicación (por registro):** pendiente → enviando → aceptado | aceptado con errores | rechazado; o sin confirmar (sin respuesta, se reenvía el mismo XML) o bloqueado (rechazo del envío completo, p. ej. certificado no autorizado). Se conservan código y descripción oficiales y el CSV.

## Consulta a la AEAT (`app/consulta.py`)

- **Mensaje**: `xmlgen.mensaje_consulta` (ConsultaLR.xsd) por obligado y periodo de imputación (mes de expedición), con
  `NumSerieFactura` opcional, `ClavePaginacion`, `DatosAdicionalesRespuesta/MostrarSistemaInformatico=S` (la respuesta trae el
  bloque `SistemaInformatico` de cada registro, que identifica la instalación) y, solo en el reintento descrito abajo,
  `IndicadorRepresentante`; se valida contra el XSD antes de enviarlo.
- **Transporte y salvaguardas**: los de la remisión. Simulador local siempre; pruebas o producción solo con el interruptor
  del servidor, `produccion.comprobar` (requisitos de la empresa) y el certificado activo y vigente
  (`remision._preparar_destino`, `remision._post`). El representante sale de `consulta.certificado_para_consulta`, que usa
  `remision._preparar_destino` → (url, certificado, representante) con la misma regla que la remisión
  (`certificados.para_envio`, también en el simulador, que recibe el NIF del tercero en el propio proceso). **Representante**:
  según `SuministroInformacion.xsd`, `IndicadorRepresentante=S` «permite, a quien realiza la consulta, obtener los registros
  de facturación en los que figura como representante», es decir, **limita** la respuesta. Por eso la comparación, la
  conciliación y el listado tras un desastre consultan **sin** él aunque el certificado sea de un tercero; solo si la AEAT
  rechaza la consulta con **4140** («no está apoderado en los trámites necesarios», `errores.properties`) se repite con
  `IndicadorRepresentante=S` y el resultado se marca como **parcial** (aviso `consulta_parcial_representante`): las filas
  «solo en local» no se dan por ausentes, lo que no devuelve no se concilia y no se proponen avances de contador ni reanudar
  la cadena. Qué error da realmente la AEAT debe contrastarse en el entorno de pruebas (`docs/pendientes-produccion.md`
  punto 5). El simulador guarda quién presentó cada registro (NIF de `Cabecera/Representante` o del obligado), lo devuelve
  como `NIFPresentador`, filtra por él con el indicador y reproduce el 4140 con el modo «solo_como_representante»
  (`tests/test_18_g2_cadena.py`). Una sola consulta a la vez por empresa y entorno; como máximo 50 páginas.
  Ejercicio con 4 cifras ASCII (2000–2100).
- **Respuesta**: `verifactu/respuesta_consulta.py` (RespuestaConsultaLR.xsd, parser sin entidades ni red). Una respuesta que
  no cumple el esquema, o cuya Cabecera (NIF del obligado) o PeriodoImputacion no corresponde a lo consultado, se muestra
  con aviso, pero no permite conciliar.
- **Comparación** con los registros locales del periodo (por número y fecha): coinciden, solo en la AEAT, solo en local,
  estado o huella distintos. Todos los textos (también la `descripcion_error` que deja la conciliación) nombran el servicio
  consultado: «el simulador local» o «la AEAT» (`consulta.servicio`). Para «solo en la AEAT» se avisa si el número es de una
  serie local cuyo contador no ha llegado y, si la consulta es del mes completo (sin número de factura), no parcial y fiable,
  se consultan además los meses siguientes del ejercicio hasta el actual (en una serie sin año, también los de los años
  siguientes; como mucho 24 meses) y se propone avanzar ese contador hasta el mayor
  número de la serie en todos ellos + 1, indicando los meses comprobados (ver «Series»).
- **Cadena atrasada y reanudación** (tras perder la base de datos y el diario y restaurar una copia antigua): la comparación
  busca registros del mismo obligado, de **esta instalación** (`SistemaInformatico`: `IdSistemaInformatico` y
  `NumeroInstalacion`; si la respuesta no lo trae, cualquiera, salvo que no haya cadena local), con una huella que no consta
  aquí (tampoco las anteriores a un ancla, siguiendo hacia atrás su `Encadenamiento`) y posteriores al último registro local:
  los encadenados a él (`Encadenamiento/RegistroAnterior`, y los encadenados a ellos) y los generados en su mismo segundo o
  después (`FechaHoraHusoGenRegistro` solo tiene segundos; con un ancla pendiente, solo después de ella). El último de la
  cadena (`consulta.ultimo_de_la_cadena`) es el que ningún otro tiene como `RegistroAnterior`; como la consulta solo devuelve el
  último registro de cada factura, una subsanación o una anulación deja huecos y puede haber varios: entonces el generado más
  tarde y, si coinciden en el segundo, el que no termina el tramo que empieza en el último registro local. Si aun así no puede
  saberse (o falta su fecha y hora), el aviso lo dice (`cadena_ambigua`, con los candidatos) y no se reanuda desde ninguno.
  Tampoco si la consulta da por anulada una factura con la huella de su alta sin que aquí conste la anulación
  (`anulacion_sin_huella`): esa anulación no se ve (pendiente de contrastar en pruebas qué devuelve la AEAT,
  `docs/pendientes-produccion.md` punto 5). Si hay registros posteriores, avisa `cadena_atrasada` con el último (IDFactura, fecha,
  `FechaHoraHusoGenRegistro` y huella, tomados de la respuesta), lo guarda en `cadenas_aeat`
  (estado «atrasada», actividad `cadena.atrasada_aeat`, diario `cadena_aeat`) y la expedición, la anulación y la subsanación
  de ese entorno se bloquean (`CADENA_ATRASADA`; también en el simulador, que es donde se prueba). La acción de administrador
  `consulta.reanudar_cadena` (`POST …/consulta/reanudar-cadena`, CSRF) vuelve a consultar en el servidor desde el mes del
  último registro local hasta el actual, más los meses de las facturas locales (una anulación o subsanación posterior de una
  de ellas es un registro de la cadena con la fecha de expedición de esa factura) y el del registro detectado (`meses_cadena`,
  como mucho 120), exige que el último registro posterior sea el que el administrador
  revisó (huella) y guarda el **ancla** (estado «anclada»), auditada (`cadena.reanudada_aeat`) y anotada en el diario en la
  misma transacción. `emision._anterior` la usa como `Encadenamiento/RegistroAnterior` del siguiente registro (alta o
  anulación), que la guarda en `registros.ancla_aeat` (inmutable), y la fila desaparece; `auditoria.verificar_cadena` acepta
  ese salto solo con el ancla. La restauración aplica la última anotación `cadena_aeat` salvo que la cadena local ya la haya
  superado (un registro tiene esa huella o se encadenó a ella, o el último registro local es posterior). Es idempotente.
  La detección necesita la consulta: `copias.restaurar` avisa (`avisos`, la actividad `copia.restaurada_sin_diario` de la
  plataforma y de cada empresa, y el mensaje de `RestauracionIncompleta`) cuando restaura sin diario para que se consulte
  antes de expedir. Las empresas pendientes se anotan en el diario (`restauracion_sin_diario`), así que una restauración
  posterior lo mantiene, aunque se vuelva a perder la base, mientras alguna no haya consultado de forma completa y fiable
  cada mes desde el de la copia en sus entornos (`consulta_aeat`) ni generado un registro nuevo (`diario.pendientes_sin_diario`).
- **Conciliación segura** (administrador, con confirmación): vuelve a consultar, toma el turno del remitente (ningún envío de
  esa empresa y entorno en curso), lo renueva antes de cada página y comprueba dentro de la transacción que escribe que
  sigue siendo suyo (si caducó y otro remitente lo tomó, no cambia nada: 409). Solo marca como aceptado un registro
  pendiente/sin confirmar/bloqueado (nunca «enviando», que puede estar en manos de un remitente), o
  rechazado por duplicado (3000), que es el último de su factura y que la AEAT tiene con la misma huella y un estado
  coherente. El 3000 sin envío previo sin respuesta (p. ej. un registro restaurado como «pendiente» que ya se había enviado)
  no puede confirmarse con la respuesta de duplicado, que no trae la huella; la consulta sí la trae. Se anota en la actividad
  y en el diario (`estado_registro`), como cualquier respuesta de la AEAT. Lo demás queda para revisión manual.
- **Incidencia de comunicación**: un envío con respuesta cierra `control_flujo.incidencia_desde`; cuando no queda nada que
  enviar, la cierran la conciliación y la restauración (`remision.cerrar_incidencia_resuelta`: solo si ya no hay registros
  sin respuesta definitiva), para que los registros nuevos no se remitan con `Incidencia=S` sin haber incidencia.
- **Evidencias**: petición y respuesta de cada página en `almacen/empresa_X/<entorno>/consultas/…` (incluidas en las copias).
- **Simulador local** (`app/simulador.py`): responde a la consulta con los registros que ha recibido. Guarda la hora de
  recepción con su huso (hora peninsular) y la devuelve como `TimestampPresentacion`, también con el servidor en UTC (las
  guardadas sin huso por versiones anteriores se leen como UTC, la hora de los contenedores). Sus modos de fallo (de la
  remisión y de la consulta) son de cada obligado: se guardan con el NIF de la empresa (`sim_modos`) y solo los consume un
  sobre cuyo `ObligadoEmision` es ese NIF; los de representación (4112/4123/4124), solo si además lleva
  `Cabecera/Representante`. Los fija un administrador de la empresa o el operador; nunca afectan a otra empresa.
- La consulta solo guarda estado propio en `cadenas_aeat` (cadena atrasada o anclada, migración 0007).

## Series con ceros de relleno (`app/series.py`)

Marcadores `{n}`, `{n:W}` / `{n…n}` (ancho W, 1–12), `{aa}`, `{aaaa}`; un solo marcador de número y como mucho uno de año.
Con ancho, el número se lee con exactamente W cifras o con más cifras sin cero inicial (el contador superó el ancho);
`series.formatear` escribe los ceros. La búsqueda de duplicados lee el patrón sin exigir el ancho (`estricto=False`), de
modo que «042/A26», «42/A26» y «0042/A26» son el mismo número. `series.proponer_patron` deduce un patrón de un número
detectado («042/A26» → `{n:3}/A{aa}`) y `series.formato_esperado` indica cómo escribir un número con el ancho de la serie.

**Avance del contador** (`series.preparar_avance` / `series.avanzar`; `GET /api/series/{sid}/avance`, `POST …/avanzar`):
si hay números ya usados fuera de la instalación (p. ej. la AEAT tiene 004–009 y la base y el diario se perdieron), un
administrador puede fijar el siguiente número más allá. Solo avanza (`MAX`), nunca por debajo del último número expedido
aquí, y exige confirmar el contador revisado (si cambió entre la revisión y la confirmación → 409). Se audita
(`serie.contador_avanzado`, con los números saltados) y se anota en el diario (`serie_contador`, con la fila de la serie)
dentro de la misma transacción; la restauración lo reproduce con `MAX` (y recupera la serie si es posterior a la copia y su
empresa existe). La expedición vuelve a comprobar la correlatividad dentro de su transacción, así que un borrador con un
número saltado ya no se expide (`NUM_NO_CORRELATIVO`). Si el diario se pierde, la conciliación del arranque vuelve a anotar los
avances que la base tiene y el diario no. `GET /api/empresas/{eid}/series` devuelve el siguiente número real
(`siguiente_actual`/`siguiente_texto`); `siguiente_numero` solo es el número inicial.

## Seguridad

- Sesión por cookie `HttpOnly`, `SameSite=Strict` (`Secure` con `VERIFACTU_COOKIE_SEGURA=1`); CSRF por cabecera; contraseñas con scrypt; bloqueo temporal tras 5 intentos.
- Autorización en **cada** operación: el recurso se resuelve a su empresa y se comprueba el rol; un recurso ajeno responde 404.
- **Plataforma multiusuario** (`app/multiusuario.py`, `app/api_multiusuario.py`): papeles y garantías en la sección «Plataforma multiusuario» más abajo. El privilegio de la plataforma (`superadmin`, quien instala) solo permite identificar al productor, fijar el modo de fallo del simulador de cualquier empresa (un administrador de la empresa lo fija para la suya: el modo se guarda con el NIF del obligado y quién lo fijó, y solo lo consumen los envíos de esa empresa, los de representación solo los que llevan `Cabecera/Representante`; el panel del operador solo lista los que fijó el operador, no el NIF de las demás empresas) y gestionar cuentas, registro e invitaciones; **cualquier usuario** da de alta sus empresas. Ser administrador de una empresa no da el privilegio de la plataforma, y tener este no da acceso a ninguna empresa.
- Inicio de sesión con bloqueo tras 5 fallos, con incremento atómico (no se elude con peticiones en paralelo).
- Transiciones de borradores condicionales y atómicas; la expedición vuelve a comprobar dentro de la transacción que el borrador no ha cambiado.
- PDF: validación de tipo y tamaño (15 MB, 30 páginas, 10 páginas de OCR, páginas de hasta 5.000 pt de lado, rasterizado de hasta 4.000 px por lado, flujos descomprimidos de hasta 200 MB —«bombas» de compresión rechazadas—), contraste del número y tamaño de páginas entre pypdf y el motor que rasteriza (poppler), rechazo de imágenes incrustadas de más de 50 Mpx, límites de memoria (1,5 GB), CPU y tamaño de fichero para qpdf, pdftoppm, tesseract y zbarimg, extracción de texto nativo en un proceso hijo con los mismos límites (máx. 50.000 caracteres y 60.000 de texto por página), reglas de extracción de campos en otro proceso hijo (120 s de CPU y tiempo real, mismos límites de memoria; si no termina, el documento se rechaza y nunca se usa una extracción parcial), nada se guarda hasta que el procesamiento termina bien, imágenes de página para la interfaz de hasta 1.600 px y 50 MB por documento, cuerpo de las peticiones limitado antes de leerlo (413), vistas previas con concurrencia limitada, superposición solo si fuera del hueco la página queda idéntica píxel a píxel, procesamiento fuera del bucle de eventos y con concurrencia limitada, detección de JavaScript, acciones automáticas, adjuntos, XFA y firmas; **el navegador nunca abre el PDF original**: se muestran imágenes rasterizadas en el servidor. El texto del documento se trata como datos (expresiones regulares), nunca como instrucciones.
- XML recibido con parser sin entidades externas ni red.
- Ficheros: la carpeta de datos (si la crea la aplicación), la base de datos y su WAL, la base del simulador, las copias de
  seguridad (carpeta `0700`, ficheros `0600`) y las copias apartadas al restaurar solo las puede leer el usuario del
  servicio, como el almacén, el diario y la clave maestra; al arrancar se ajustan las creadas antes con permisos más
  abiertos (`copias.proteger_ficheros`, `db.crear_privado`).
- Certificados: PKCS#12 y contraseña cifrados con AES-256-GCM; clave maestra en fichero `0600` **fuera** de la base de datos y de sus copias; durante el envío la clave privada se entrega al cliente TLS mediante ficheros anónimos en memoria (`memfd`), sin escribirse en disco. La vigencia se comprueba en cada uso. Nunca se devuelven a la interfaz ni se escriben en registros (ni en la actividad ni en el diario).
- **Certificados por usuario («Mis certificados», `app/certificados.py`, `app/api_certificados.py`).** Un usuario carga su certificado una vez (mismas comprobaciones de archivo, contraseña y vigencia; cifrado con un contexto AEAD ligado a la fila y al propietario) y lo asigna a empresas que **administra**. Solo el propietario lo ve en su lista, lo asigna, lo retira o lo elimina (a cualquier otro usuario le responde 404); los administradores de la empresa ven cuál se usa (titular, vigencia, origen), pueden sustituirlo por otro, pero no descargarlo ni usarlo en otra empresa (no existe ninguna ruta de descarga). Si el propietario deja de ser administrador de la empresa o se desactiva su usuario, la asignación deja de poder usarse: se comprueba **en cada uso**. Eliminar sobrescribe el archivo cifrado (`secure_delete`) y retira sus asignaciones. Cualquier administrador de la empresa puede dejar de usar en ella una asignación (aunque sea de otro usuario), sin acceso al certificado. Cada uso (envío o consulta), cada asignación y cada retirada quedan en la actividad de la empresa y en «Ver usos» del propietario.
- **Titularidad y representante.** Cada empresa y entorno usa un único certificado (de la empresa o asignado). Es **propio** si identifica a la empresa (sello o persona jurídica con su NIF, o representante de esa entidad) o si su titular persona física es el obligado; si no, es de un **tercero** y solo se usa si la empresa ha declarado una legitimación (apoderamiento en la AEAT o colaboración social) **para ese titular** (NIF y nombre) con evidencia y sin revocar. Con un tercero, cada envío lleva `Cabecera/Representante` (nombre y NIF del titular: la entidad en sellos y certificados de representante de persona jurídica). El nombre es el del **certificado** (O de la entidad; «APELLIDOS NOMBRE», como en el censo, en los de persona física), no el escrito al declarar la legitimación, que solo se usa si el certificado no trae ninguno; si difieren, la evaluación lo avisa (`aviso_nombre`) porque la AEAT contrasta el nombre con su censo (4124). La regla está en una sola función, `certificados.para_envio(con, empresa, entorno) -> (certificado, representante | None)`, que usa la remisión (`remision._enviar` obtiene cabecera y certificado TLS de la misma llamada) y también las consultas (`ConsultaFactuSistemaFacturacion`), que usan el mismo certificado pero van **sin** `Cabecera/IndicadorRepresentante` aunque haya representante: solo lo llevan al repetirse tras un 4140, y entonces el resultado es parcial (ver «Consulta a la AEAT»). Para otros servicios, `remision._preparar_destino(con, entorno, eid)` aplica los interruptores del servidor y esa misma regla y devuelve `(url, certificado, representante)`; el uso se audita solo (`certificado.uso`, `operacion: consulta`) al descifrar el certificado en `certificados.ficheros_pem`, justo antes de cada conexión TLS. En el simulador, una asignación que ya no puede usarse (propietario sin administración, eliminada, sin archivo o caducada) no bloquea: se envía sin certificado; un tercero utilizable sin legitimación sí se rechaza. La evidencia de una legitimación debe existir y ser un documento de la misma empresa. Un 4112 de la AEAT (titular no autorizado), un 4123 (NIF del representante no censado) o un 4124 (nombre del representante no censado) bloquean los registros con una explicación en español (`certificados.con_ayuda`); el simulador los reproduce con los modos «no_apoderado», «representante_nif_no_censado» y «representante_nombre_no_censado». La AEAT es quien verifica el apoderamiento, solo al remitir.
- Cabeceras: CSP estricta, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`.
- Confirmaciones irreversibles (quitar a una persona, salir de una empresa, eliminar un certificado, revocar una
  legitimación, desactivar una cuenta, cambiar a PRODUCCIÓN…): el diálogo se abre con el foco en «Cancelar», de modo que
  un Intro de más o una tecla mantenida no las ejecuta; Escape cierra sin hacer nada. Descartar un borrador, anular un
  registro y avanzar el contador de una serie no empiezan en el botón (sino en el motivo, en los datos o en el cuerpo del
  diálogo) y exigen además un motivo o una casilla de confirmación.
- Fechas para el usuario: la interfaz muestra las fechas y horas en hora peninsular (`Europe/Madrid`), sea cual sea la
  zona del navegador; también calcula así la fecha de hoy y el año y el mes que propone (ejemplo del patrón de una serie,
  año del avance de contador, ejercicio y periodo de la consulta a la AEAT). Los textos que compone el servidor (aviso de
  incidencia, documento ya importado, fecha del documento, autorización del primer envío, vigencia del certificado, fecha
  de la firma de un PDF firmado al clasificarlo) usan `db.legible` («01/07/2026 a las 12:05» o solo el día); una fecha de
  firma que no se puede leer se indica como «sin fecha legible», nunca con su valor interno («D:2026…»). La única
  excepción es la columna «Fecha, hora y huso» de los registros de una factura: muestra `FechaHoraHusoGenRegistro` tal
  como se registró y remitió (ISO 8601 con el desfase), porque forma parte de la huella.
- Registro de actividad de solo anexado, sin secretos.
- Interruptores de servidor `VERIFACTU_PERMITIR_PRUEBAS_AEAT` y `VERIFACTU_PERMITIR_PRODUCCION` (por defecto desactivados): las pruebas de desarrollo no pueden comunicar con la AEAT por accidente.

## Plataforma multiusuario

| Papel | Alcance |
|---|---|
| Operador de la plataforma (`superadmin`) | Productor del sistema (suscribe la declaración responsable). Ajustes de la plataforma, cuentas, invitaciones de la plataforma, productor y modo del simulador de cualquier empresa. Sin acceso a datos de empresas salvo como miembro |
| Usuario | Sus empresas (queda como administrador al darlas de alta) y las que le inviten |
| Miembro de una empresa | `admin` / `emisor` / `lector` en esa empresa |

- **Aislamiento**: toda ruta con un recurso (empresa, documento, borrador, factura, registro, serie, certificado,
  invitación) lo resuelve a su empresa y exige ser miembro con el rol mínimo; si no, 404 (no se revela que exista).
  `tests/test_16_u_multiusuario.py` recorre automáticamente **todas** las rutas `/api` con identificadores, con un
  usuario ajeno y con el operador sin membresía (las del panel de la plataforma, solo con el usuario ajeno: 403), cada
  parámetro con un identificador real del tipo que corresponde (de «Mis certificados» para `{cid}`, certificado de la
  empresa para `{cert_id}`, asignación `{aid}`, legitimación `{lid}`, serie `{sid}`, incluidos el avance y el avance del
  contador). Cada ruta con identificadores debe responder **exactamente 404** a quien no es miembro (403 las del panel
  de la plataforma a quien no es operador; las excepciones, con su motivo, irían en `EXCEPCIONES_404`, hoy vacía): una
  ruta que valida el cuerpo antes de comprobar el acceso (422) también falla. Además, nada cambia en la base de datos
  (todas las tablas salvo las sesiones). La prueba **falla** si una ruta con identificadores queda sin probar (p. ej.,
  una ruta nueva con un parámetro que el barrido no conoce). `tests/test_18_g1_cuentas.py` quita, de una en una, la
  comprobación del rol de **cada** función que llama a `rol()` (las encuentra en el código y comprueba que el barrido
  pasa por todas), la del propietario de «Mis certificados» y la del operador, y comprueba que el barrido detecta cada
  una.
- **Registro**: cerrado por defecto (ajuste `registro_abierto` en la tabla `plataforma`). Límites en memoria (como el
  bloqueo de inicio de sesión): intentos por dirección IP y hora, cuentas nuevas por hora y intentos totales. Detrás de un
  proxy inverso, uvicorn debe arrancarse con `--proxy-headers --forwarded-allow-ips=<proxy>` para ver la IP real.
- **Sin enumeración de cuentas**: correo ya registrado, invitación inexistente, caducada, usada, anulada o para otro
  correo dan la misma respuesta (409, mismo mensaje), y el resumen scrypt de la contraseña se calcula siempre antes de
  consultar nada (el tiempo no delata si el correo existe). Añadir a una empresa a quien no es miembro crea siempre una
  invitación, exista o no una cuenta con ese correo: nadie entra en una empresa sin aceptar y la respuesta no revela
  qué correos están registrados.
- **Invitaciones**: token aleatorio de 256 bits que solo se muestra al crearla; se guarda su SHA-256. Un solo uso
  (actualización condicional dentro de la misma transacción que crea la cuenta), caducidad de 1 a 30 días, anulables, y
  ligadas al correo cuando se indica (siempre en las de empresa). El enlace usa el fragmento `#` de la URL: no llega al
  servidor ni a los registros de acceso. Como no hay verificación del correo, quien tenga el enlace puede usarlo; por
  eso una invitación **solo vale mientras su creador pueda crearla** (cuenta activa y administrador de la empresa;
  operador activo, si es de la plataforma), comprobado al consultarla, al registrarse y al aceptarla, y degradar,
  quitar o desactivar a alguien anula además sus invitaciones pendientes.
- **Registro cerrado e invitaciones de empresa**: con el registro cerrado, una invitación de empresa creada por un
  usuario normal no crea cuentas (solo la acepta una cuenta existente; 403 igual exista o no el correo), porque
  cualquiera podría dar de alta una empresa propia e invitar a correos arbitrarios. Las del operador sí, y el operador
  puede permitirlo en general (`invitaciones_empresa_crean_cuentas`, versionado y anotado en el diario como
  `registro_abierto`). Cupos en la base de datos para los usuarios (no para el operador): invitaciones pendientes por
  empresa, invitaciones por persona y día y altas de empresas por persona y día.
- **Miembros**: un administrador puede cambiar el rol de otro o quitarle el acceso (`DELETE
  /empresas/{eid}/miembros/{uid}`) y cualquiera puede salir; la empresa conserva siempre un administrador con la cuenta
  activa. La baja se anota como una instantánea de los miembros en el diario (sobrevive a una restauración).
- **Altas por el administrador** (U5): la opción elegida es la **invitación**, porque la persona elige su propia
  contraseña y nadie más la conoce ni hay que transmitirla. El alta directa con contraseña (`clave` en
  `POST /empresas/{eid}/miembros`) se conserva solo para el operador de la plataforma, solo crea cuentas **nuevas** y les
  pone una **contraseña temporal**; si el correo ya tiene cuenta, se le envía una invitación como a cualquiera (no se le
  mete en la empresa sin que acepte ni se toca su contraseña).
- **Límites del registro** (en memoria): sin invitación vigente, por dirección IP (`VERIFACTU_REGISTRO_MAX_IP`) y
  intentos totales por hora en toda la plataforma (10 × `VERIFACTU_REGISTRO_MAX_GLOBAL`). Con una invitación vigente, el
  registro y la consulta del enlace **no** cuentan en esos dos límites (el personal de una oficina comparte IP, y nadie
  debe poder agotar con sus propias invitaciones un límite común a todos), sino en el de esa invitación (10 por hora),
  en un techo por IP más alto (`VERIFACTU_REGISTRO_MAX_IP_INVITACION`, 50) y, el registro, en el de quien la creó, por
  usuario y por empresa (`VERIFACTU_REGISTRO_MAX_POR_INVITADOR`, 50; no el operador). Las cuentas nuevas por hora
  (`VERIFACTU_REGISTRO_MAX_GLOBAL`) cuentan siempre; las creadas con invitaciones de usuarios (no del operador) de un
  mismo usuario o empresa (`VERIFACTU_ALTAS_MAX_POR_INVITADOR`, 10) o desde una misma IP nunca pasan de la mitad de ese
  límite. Los enlaces no
  válidos (adivinados, caducados, usados) cuentan por IP como siempre. Superar un límite no gasta los demás.
- **Actividad de la plataforma** (operador): la que no es de ninguna empresa (altas, sesiones, ajustes, invitaciones de
  la plataforma). De los errores técnicos solo se muestra el tipo y la ruta sin identificadores; la actividad de «Mis
  certificados» de cada usuario (titular y entidad representada) no se muestra. Tres listas, cada una con su límite:
  las 500 anotaciones más recientes de auditoría (operador y sistema), las 500 más recientes que cualquier cuenta
  produce por sí misma y sin límite (inicios de sesión, cambios de su contraseña, altas:
  `api_multiusuario.ACCIONES_DE_LAS_CUENTAS`) y los 100 errores internos más recientes. Así, ni una avalancha de errores
  provocados sin sesión ni cientos de inicios de sesión de una cuenta desplazan las contraseñas temporales, las bajas,
  los ajustes o las verificaciones de NIF. La actividad de cada empresa muestra igualmente sus errores aparte.
  En la interfaz (de la plataforma, de cada empresa y en «Ver usos» de «Mis certificados») los datos del detalle llevan
  etiquetas en español («Correo», «Días», «NIF del titular»…) y sus valores en palabras: rol, entorno, ámbito (tipo de
  contribuyente, territorio, origen de los PDF), clasificación, estrategia del PDF, estado del envío, modo del simulador,
  método de legitimación y tipo de certificado con el mismo texto que en los formularios; 0/1 como «Sí»/«No» (SII,
  Canarias, Ceuta o Melilla, ajustes de la plataforma); el resumen de una consulta en palabras y con el servicio consultado («coinciden
  2, solo en el simulador local 0…»; también «Estado en …» y «Último registro en …»); la evidencia guardada como «respuesta de la consulta guardada el …»; los puntos confirmados de un
  borrador por el dato al que se refieren; las fechas en hora peninsular (las que el servidor guarda sin huso, como el minuto de un error, ya lo están: se muestran tal cual) y sin los datos vacíos; el modo del simulador de una consulta con el texto de los modos de la consulta. El código de la acción
  (`factura.expedida`…) se muestra tal cual: identifica el evento y sirve para filtrar.
- **Errores internos (500)** (`app/errores.py`): los tipos erróneos en el cuerpo responden 422 (también en
  `/api/sesion` e `/api/instalar`) y una serie con un código ya usado, 409. Lo inesperado se anota sin datos (plantilla
  de la ruta y tipo de la excepción) en **una fila por ruta, tipo y minuto con el número de veces**: la actividad es de
  solo anexado, así que las repeticiones se cuentan en memoria y la fila de un minuto se escribe cuando ese minuto ha
  terminado (con el siguiente error o al consultar la actividad) o al detener la aplicación (si el proceso muere antes,
  solo queda la traza en el registro del servidor). Consultar la actividad nunca escribe el minuto en curso: si lo
  hiciera, alternar un error y una consulta escribiría una fila por error. Si la ruta es de una empresa (`{eid}` o el documento, borrador,
  factura, registro o serie de la ruta) y quien pide es miembro, se anota también en la actividad de esa empresa y el
  mensaje remite a ella; al operador se le remite a la de la plataforma y a los demás, a avisar al operador (nunca a un
  registro que no pueden ver).
- **Acceso perdido con la sesión abierta**: si la empresa elegida responde 404 (otro administrador le ha quitado o ha
  salido desde otra ventana), la interfaz relee `GET /api/yo`, aplica la sesión, avisa «Ya no tiene acceso a esa
  empresa» y lleva al inicio (otra empresa suya, el alta de una empresa o el panel del operador). Si sigue siendo
  miembro (p. ej. un documento que no existe), muestra el error de siempre. Al revés, si no tiene ninguna empresa elegida
  y la sesión releída trae alguna (aceptó una invitación en otra ventana), la elige como al entrar y avisa «Ya tiene
  acceso a …»: lo comprueba al abrir «Mi cuenta» y al abrir cualquier pantalla de empresa, de modo que la cabecera y las
  pantallas nunca se contradicen.
- **Productor del sistema**: lo identifica y confirma el operador de la plataforma (`PUT /api/productor`, 403 «Solo el
  operador de la plataforma…» para los demás); en la configuración de una empresa el paso figura como «lo completa el
  operador de la plataforma» y no como tarea pendiente de la empresa.
- **Contraseñas**: misma política (≥ 10 caracteres, máx. 256, scrypt). Cambio propio con la actual (los fallos cuentan
  para el bloqueo de 5 intentos) que cierra las demás sesiones; contraseña temporal del operador con cambio obligatorio
  (`cambiar_clave`: la sesión solo admite `/api/yo`, `/api/yo/clave` y cerrar sesión, comprobado en el servidor);
  desactivar una cuenta cierra sus sesiones. Ninguna contraseña ni token aparece en la actividad ni en la interfaz.
- **Contraseña temporal = posible suplantación**: el operador no ve datos de empresas por serlo, pero quien fija una
  contraseña temporal puede entrar como esa persona. Mientras no haya recuperación por correo
  (`docs/pendientes-produccion.md`), se mitiga haciéndolo visible: se anota en la actividad de cada empresa de la cuenta
  (y su sustitución), se muestra a la persona en «Mi cuenta» y la persona deja de poder entrar con su contraseña.
- **Cuentas de la versión anterior**: en ella, toda cuenta salvo la del operador la creaba el operador o un administrador
  de empresa con una contraseña que elegía él, y no había forma de cambiarla. La migración `0005_multiusuario.sql`, que
  se aplica una sola vez al actualizar (en una instalación nueva no hay cuentas todavía), marca esas cuentas con
  `cambiar_clave = 1` y `alta_origen = 'version_anterior'` e incrementa su versión, de modo que la conciliación del
  arranque lo anota en el diario y una restauración no lo deshace. Al entrar deben elegir una contraseña nueva (la
  pantalla de cambio y «Mi cuenta» explican el motivo) y la sustitución queda en la actividad de cada una de sus
  empresas, como con una contraseña temporal.
- **NIF único** en la plataforma (una sola cadena por obligado y sistema): si ya existe, 409 sin datos de la empresa. Se
  compara en todas sus formas equivalentes (C, D, F, G, J, U y V admiten como control el dígito o la letra), dentro de
  la transacción de alta (`crear_empresa`), así que vale para cualquier vía de alta. Un NIF solo admite caracteres
  ASCII (`nif.normalizar` no convierte nada más y `nif.tipo` exige `[0-9]`): las cifras de ancho completo, de otros
  alfabetos o matemáticas dan 422 en vez de colarse como un NIF «distinto» (empresa, destinatario, titular de una
  legitimación y productor). Al arrancar, `multiusuario.avisar_nifs_no_ascii` cuenta las filas que una versión anterior
  hubiera guardado así y lo anota en la actividad de la plataforma (solo los recuentos, de nuevo si cambian) y en la de
  cada empresa afectada.
- **NIF verificado** (`empresas.nif_verificado_*`, `certificados.nif_acreditado`): cualquier usuario da de alta una
  empresa con un NIF libre, y la legitimación de un tercero es una declaración con una evidencia que la aplicación no
  puede comprobar. Por eso el envío a la AEAT (pruebas o producción, también la consulta) con un certificado que **no**
  es propio de la empresa exige que su NIF esté acreditado: verificado por el operador en el panel
  (`POST /api/plataforma/empresas/verificacion-nif`, anotado en la actividad de la plataforma y en la de la empresa), dada
  de alta por el operador (también las de la versión anterior, que solo él podía crear) o con un envío de la empresa
  hecho con su **propio** certificado que la AEAT ya aceptó. Que se haya cargado o asignado un certificado propio no basta:
  la aplicación no valida la cadena del certificado (uno autofirmado con ese NIF «parecería» propio); la AEAT sí, al
  conectar. Con un certificado propio el envío no lo necesita. Se comprueba en los requisitos de la pantalla Producción
  (`nif_verificado`) y en `certificados.para_envio` (el certificado puede cambiarse después de expedir). El simulador
  nunca lo exige. Cada verificación o retirada sube `nif_verificado_version` y se anota en el diario
  (`verificacion_nif`): una restauración aplica la más reciente, como con las legitimaciones (una retirada posterior a
  la copia no se deshace), y anota las empresas cuyo estado cambia (`empresa.nif_verificacion_restaurada`).
- **IndicadorMultiplesOT** (bloque `SistemaInformatico`): en SaaS se calcula por usuario (FAQ desarrolladores n.º 4):
  `S` si quien genera el registro (expedición, anulación o subsanación) es miembro de más de una empresa; las empresas
  de otros usuarios de la plataforma no cuentan.
- **Copias y diario**: cada cambio de una cuenta o de un ajuste incrementa su versión en la misma transacción y se anota
  en el diario (`usuario`, con el resumen scrypt y nunca la contraseña; `ajuste_plataforma`); al arrancar se anotan las
  versiones que falten. Al restaurar se aplica la última versión de cada cuenta y ajuste (una contraseña cambiada o una
  cuenta desactivada después de la copia no vuelven atrás), se cierran las sesiones de esas cuentas, el diario nunca
  concede el privilegio de la plataforma y **las invitaciones pendientes se anulan**; las usadas después de la copia
  (su uso se anota en el diario) figuran como usadas. Una sesión cerrada después de la copia no vuelve a valer: las salidas
  se anotan en el diario (`sesion_cerrada`, solo el resumen del token) y, si la base sustituida se puede leer, solo siguen
  abiertas las sesiones que también lo están en ella (ver `docs/copias-y-recuperacion.md`).

## Privacidad y conservación

- Los documentos y datos reales permanecen en el servidor del cliente; no hay servicios externos de OCR ni IA.
- Conservación propuesta: facturas expedidas, registros, peticiones/respuestas y originales durante el plazo de prescripción tributaria (4 años, art. 66 LGT) y, si aplica, el mercantil (6 años, art. 30 CCom). Los borradores descartados y documentos no emitidos pueden purgarse tras 12 meses (pendiente de implementar como tarea programada).
- Exportación: descarga individual y por lotes (ZIP con PDF y XML); copias completas (`app/copias.py`).

## Extracción de datos de cualquier diseño de factura (`app/extraccion/campos.py`, versión «reglas-2.3»)

La extracción no depende de una plantilla ni de un emisor concreto: trabaja sobre la **geometría** de las palabras
(texto nativo del PDF o, si no lo hay, OCR local) y sobre reglas genéricas, comprobadas con un corpus sintético de
diseños muy distintos (ver `docs/pruebas.md`).

1. **Renglones y bloques**: las palabras se agrupan por altura (sin mezclar dos renglones aunque un logotipo grande los
   abarque) y se separan en bloques por huecos relativos al espaciado de cada renglón (columnas, cajas laterales).
2. **Etiqueta → valor**: en el mismo bloque («Nº factura: X», «Número de proforma. - X», «Expediente municipal: X»), en el
   bloque de la derecha, o debajo (rejillas «Número | Fecha» con los valores en la fila siguiente). Rótulos bilingües
   («Pedido / PO No.») admitidos. En las referencias, un valor de la derecha a más de 5 renglones del rótulo no se toma
   si es un renglón de otro bloque de texto (el recuadro del cliente frente a un «Referencia:» u «Obra:» vacíos); sí si
   está en la columna de valores de una rejilla de rótulos (un renglón contiguo alineado con él es el valor de otro rótulo
   suelto de la misma columna); si está solo y a más de 10 renglones, se toma a confirmar. La forma de pago
   sigue en los renglones de debajo de su valor y en su misma columna (sin otro rótulo a su izquierda) hasta una frase
   acabada en punto, un renglón con rótulo propio (también sin dos puntos: «BIC …», «Titular …», «OBSERVACIONES»), cuenta,
   IBAN, fecha o importe, o con un importe a su derecha («Portes  15,00»); si un renglón unido no tiene vocabulario de
   pago, se marca para confirmar. El punto final de una abreviatura («60 días f.f.») se conserva. «REFERENCIA : texto»
   es la referencia comercial del documento, salvo que cite otra factura («Referencia factura nº …», «Ref.: Factura
   rectificada …»). Sin fecha rotulada, la fecha de carta («Villaficticia, 14 de octubre de 2026», «En Villaficticia, a
   1 de noviembre de 2026»: el lugar y solo la fecha, en un renglón suelto que no sigue la frase del de encima, a
   cualquier altura de la página; el año también con punto de millar, «2.026») se acepta si ninguna otra fecha suelta
   la contradice y ningún rótulo «Fecha:» del documento se ha quedado sin usar (valor ilegible, o renglón que dice
   además otra cosa); si la contradicen, se marca para confirmar. Al pie hay también fechas de otros actos: la fecha de
   carta solo se acepta sin más si no la presenta un rótulo con dos puntos («Recibido y conforme:», «Mandato SEPA
   firmado por el deudor:»), si por debajo del 40 % de la página no va en letra menor que la del texto (la letra
   pequeña del pie) y si el documento no tiene otra fecha de carta en blanco o ilegible («a ___ de ______ de 2026»);
   si no, se propone a confirmar, con un aviso cuando la del documento está en blanco. Cualquier otra fecha sin rótulo
   de la parte superior se marca para confirmar, salvo la citada en una frase («s/contrato del 3 de marzo de 2024»: la
   de otro documento; no un encabezado «Factura nº 45 del 15/10/2026»), que nunca se toma. Tampoco vale como fecha
   rotulada la de una frase que fecha otra cosa («según el contrato de fecha 3 de marzo de 2024», «Albarán de fecha
   …»; sí «Le remitimos la factura nº X de fecha …») ni la de un rótulo de otro papel («Fecha contrato:»). Tras
   «FACTURA», las lecturas del OCR de «Nº» («N“.», «N”», «N.» con la º perdida) y «Nro.» rotulan el número, salvo que
   la abreviatura vaya con el nombre de otra cosa («FACTURA Nº CLIENTE»: dos rótulos de una rejilla); un número seguido
   de « / » y más cifras («2026 / 045») se marca para confirmar. El tipo de documento toma la confianza de su palabra
   clave (no la de una abreviatura mal leída del mismo renglón) y, entre renglones del mismo tipo y tamaño, prefiere el
   título («FACTURA Nº. X») a una frase o un rótulo de totales; en la cabecera, una palabra clave mencionada en un dato
   o una frase («Forma de pago: abono en cuenta», «Según presupuesto aceptado…», «Albarán de fecha …») no decide el
   tipo, y si solo hay menciones el tipo deducido se marca para confirmar.
3. **Partes**: cada NIF/CIF/NIE válido (también con puntos, guiones o espacios: «B-46.871.232», «21.987.654-E»,
   «C . I . F . : B - 00618371»; con la raya o el guion separados por espacios, «CIF: A – 00543215», «N.I.F.: 44444444 – A»,
   y con una barra tras el código de provincia, «CIF: B00/123455», solo detrás del rótulo del NIF, también con «nº» o
   «núm.» en medio: sin él esas formas son las de un número de factura o una serie, «F26/000123», «A – 26000125», o de una
   referencia, «Pedido 20261105 – Z»; un NIF sin rótulo seguido de una raya, «B00678904 – C/ …», se sigue reconociendo)
   define un bloque (misma columna por solape o alineación). El papel (emisor/destinatario)
   se puntúa por indicios: rótulo de cliente, pie con datos registrales, NIF de la empresa configurada, posición. La razón
   social prefiere la línea con forma jurídica o entidad (S.L., S.A., comunidad de propietarios…); para un NIF de persona
   física, el nombre de la persona antes que el comercial. El domicilio se toma del bloque, del pie («RAZÓN · NIF ·
   dirección · CP ciudad · Tel.», también sin tipo de vía: «Imaginaria, 58 28001 Ciudad correo@…») o de la cabecera, cortando
   teléfonos y correos: corta un correo, una web o una palabra de contacto seguida de «:», de un teléfono, de un correo o de
   una web; la misma palabra sin nada de eso detrás puede ser parte de la calle («Calle Ficticia del Correo, 4», «Plaza
   Imaginaria de Correos 2») y no corta. Lo que precede al contacto solo vale si es un domicilio con número o código postal, sin el rótulo
   («Contacto:», «Pedidos:», «Síguenos en»); si va pegado sin separador y tras la población sigue una frase, se corta tras la
   población y se pide confirmarlo. Un NIF solo en su renglón, alineado al borde de un recuadro uno o dos renglones por debajo del
   nombre y la dirección (que solo lo solapan en parte), es de ese recuadro. Si el NIF del destinatario va suelto, sin
   nombre ni dirección (p. ej. bajo el título), y la cabecera tiene un único bloque sin rótulo ni NIF con razón social y
   dirección que no es el del emisor (cuya razón social se lee junto a su NIF, p. ej. en el pie), se le asigna ese bloque
   con confianza baja para que se confirme. La dirección del emisor tomada de la cabecera (sin dirección junto a su NIF)
   se marca dudosa si sale de un bloque de otra parte (otro NIF, rótulo de cliente, otra razón social con forma jurídica o
   de entidad, o cualquier otro nombre si el destinatario no tiene su propio bloque completo: un nombre comercial o un lema
   en el membrete no lo es) o si el NIF del destinatario figura sin razón social ni domicilio y el bloque no lo encabeza la
   razón social del emisor leída junto a su NIF. Si ni el emisor ni el destinatario tienen razón social junto a su NIF, la
   del emisor tomada de la cabecera puede ser la del recuadro del destinatario: se marca dudosa y no sirve para dar por
   suyo el bloque que encabeza. La validación pide confirmar un domicilio del emisor que contiene el del destinatario
   (EMISOR_DOMICILIO_DESTINATARIO). Bajo el NIF, un domicilio sin código postal (el de un particular: «Residencial
   Ficticio, 7B» ⏎ «Urb. …» ⏎ «LOCALIDAD (Provincia)») se toma de los renglones contiguos de su columna y se pide
   confirmarlo. Una calle sin tipo de vía («Nombre, 7B», no una persona de contacto «Att. …» ni un rótulo) solo vale si la
   sigue un renglón que por sí solo es de domicilio: empieza por un tipo de vía o por el código postal y la población, o es
   «LOCALIDAD (Provincia)» (no «Pedido 12345»); y siempre se pide confirmarla. Entre 45 y 90 pt bajo el NIF solo se añaden
   esos renglones, y solo mientras el domicilio no tenga código postal ni población: tras «LOCALIDAD (Provincia)» lo que
   sigue («Ref. 10234», «Póliza 12345», «Lugar de entrega C/ …», «Entrega: …») ya no es el domicilio; un domicilio
   completado así se pide confirmar aunque lleve código postal.
4. **Tabla de conceptos**: cabecera reconocida por clases de columna (concepto, cantidad, unidad, precio, dto., IVA,
   importe, código, periodo/fecha), incluidas cabeceras en dos renglones; cada cifra se asigna a la columna más próxima y
   las de columnas no numéricas (código, periodo) no se confunden con cantidades. La tabla termina en el bloque de totales
   (también si va en una caja a la derecha en la misma fila que otro texto), en una fila solo de cifras o en un párrafo;
   continúa en la página siguiente tras «Suma y sigue» o el pie, saltando la cabecera repetida. **Sin cabecera legible**
   (habitual en escaneos), las columnas se deducen de la alineación de las cifras. Sin columnas a la izquierda de la
   descripción, esta empieza donde se alinean sus renglones (aunque el rótulo vaya centrado sobre una columna ancha; con
   conceptos cortos que no llegan bajo el rótulo, donde se alinea el último tramo de texto antes de las cifras). Renglones
   de texto contiguos encima de las cifras de la primera línea: un **encabezado de grupo** (acaba en «:», es una
   referencia a otro documento —«Pedido nº …», «Albarán …», «Rectifica la factura …»— o va todo en mayúsculas) no es parte
   del concepto; el resto es el comienzo del concepto (cifras en un renglón intermedio) si la frase sigue (el renglón de
   las cifras empieza en minúscula o el de encima acaba en coma o preposición) o si no hay texto en el renglón de las
   cifras; sin ese indicio, si la descripción sigue debajo se antepone pero se pide confirmar (podría ser un encabezado
   sin marca), y si no sigue no se antepone. Con varios conceptos así en la misma página, se pide confirmar a cuál
   pertenece cada renglón. Un segundo renglón de cabecera sin rótulos conocidos («(€/1.000 uds)», «‰» bajo «PRECIO»),
   pegado a la cabecera, sin cifras y con texto bajo una columna numérica, es cabecera: no es el comienzo de un concepto.
   **Precio por millar o centenar** («€/MILLAR», «/1000», «por mil», «‰», «€/100»), en el rótulo del precio (en
   cualquiera de sus renglones) o junto a las cifras: si importe = precio × cantidad ÷ 1000 (÷ 100), la cantidad se
   expresa en esa unidad (unidad «millar» o «centenar») para que cantidad × precio = importe.
   **Sin tabla**: con un apartado «Concepto», o en una **carta** (el párrafo de texto corrido justo encima del cuadro de
   totales, alineado a su izquierda, sin cifras, rótulos, NIF ni la fecha de la carta), una sola línea con ese texto y la
   base imponible como importe, para revisar; solo si todas las cifras desde el párrafo hacia abajo son del resumen.
5. **Importes**: etiqueta con su cifra en la misma fila sin texto ajeno en medio, o cuadros de etiquetas con los valores
   debajo, por columnas, incluido el **desglose por tipos** («Tipo IVA | Base | Cuota» con una fila por tipo) y el recargo
   de equivalencia. En «% IVA | IVA» (o «IVA % | IVA»), la columna rotulada como tipo es el tipo y la otra la cuota; una
   columna de la moneda no es un importe, y un tramo leído junto («1.234,56 EURO (€)») se reparte por posición. Se distinguen la retención de IRPF y la **retención de garantía** (que no es IRPF ni cambia la base,
   la cuota ni el total). Un comprobador aritmético busca la combinación coherente base + cuota = total si las etiquetas
   no bastan.
   **Renglones con puntos guía** («Importe alquiler ……… 650,00 €», «IVA 21% ……… 136,50 €», «TOTAL FACTURA ……… X €»):
   los puntos entre el rótulo y la cifra son relleno, también la basura que el OCR lee en esa fila (tramos mucho más bajos
   que el rótulo, sobre la línea base, de confianza baja; o, a cualquier resolución, tramos de confianza casi nula sobre la
   línea base hechos de puntos y letras sin trazos altos, «...oo.ooposor»). Sin rótulo de base, los renglones contiguos
   encima del primer importe rotulado con un texto que no es un rótulo conocido y una cifra en la columna del cuadro son la
   base (su suma), solo si cuadra (base × tipo = cuota, o base + cuota − retención = total) y para confirmar salvo que
   cuadren las dos cosas. Entre renglones cabe uno en blanco: seis alturas de la cifra, o algo más de dos veces el paso entre
   los renglones rotulados del cuadro (las cajas del OCR solo cubren la altura de las cifras). Un renglón con un porcentaje o
   una palabra de impuesto en el texto («Impuesto 21%», «Tax 21%», «lVA 21%» mal leído) nunca es de la base. Sin ningún IVA
   en el cuadro y base = total, la cuota es cero y no hay desglose (factura sin IVA: el borrador propone una base exenta, ver
   «Operaciones exentas»);
   no se concluye si el cuadro tiene un renglón de impuesto no reconocido o un recargo, o la página dice que el total lleva
   el IVA incluido (una exención declarada no lo impide). Con retención y el total impreso neto de ella, el total de la
   factura se calcula (base + cuota) y el impreso es el importe a pagar; si el total no se lee, el importe a pagar se calcula
   para confirmar. Cifras OCR dudosas («-1 23 50 €», con espacios por separadores; «550.80», con punto decimal en un
   documento con coma decimal; o la única cifra tras el rótulo, pequeña y poco legible) se aceptan con confianza baja y un
   aviso si cuadran con los demás importes (la retención sin porcentaje, si base + cuota − retención es el líquido impreso).
   Si no cuadran, las de punto decimal y las pequeñas se conservan con confianza aún más baja y un aviso de que no cuadran
   (la validación señala el descuadre; no se sustituyen por un cálculo); las de espacios se descartan.
6. **OCR** (páginas sin texto): enderezado automático (±3°), limpieza de motas, contraste, ampliación ×1,5 y borrado de
   los bordes finos de tablas y cajas (con ellos Tesseract se salta el texto de las celdas); Tesseract en modo de texto
   disperso (`--psm 11`), un hilo por proceso (varios OCR simultáneos no compiten), porque la aplicación reconstruye
   renglones y columnas por geometría; unión de decimales separados («17289, 27»); contraste de la razón social con la
   del pie. Rótulos del NIF tal como los lee el OCR («N.LF.», «C.l.F.», «N.1.F.»). Un NIF leído tras su rótulo sin
   control válido se corrige con las confusiones habituales entre letras y cifras (B/8, O/D/Q/0, I/l/|/1, S/5, Z/2, G/6,
   T/7, A/4) solo donde la forma del NIF pide la otra clase de carácter, como mucho en dos posiciones y solo si una única
   variante es válida: se marca para confirmar, con la lectura original en el aviso, y esa lectura no se señala además
   como NIF ilegible; nunca en texto nativo. Los valores leídos por OCR llevan su confianza y
   la cantidad ilegible deducida de importe ÷ precio se marca como «calculado».
7. **Texto que no se ve**: un PDF puede llevar texto que el lector no ve (blanco sin fondo, tapado por una imagen,
   recortado, fuera de la página, en modo invisible, con máscaras o con fuentes cuyo /ToUnicode no corresponde a lo que
   dibujan). `texto_proceso.py` descarta el que reconoce como invisible; si la página es mayoritariamente así, se lee por
   OCR; el resto se contrasta con la imagen representada (tinta bajo cada palabra y, para las cifras de fuentes
   incrustadas, lectura OCR de cada glifo) y lo que no coincide baja a confianza 0,5 con el aviso «puede no verse en la
   página». Un blanco de una fuente incrustada junto a una cifra que dibuja algo en la imagen también la hace dudosa (puede
   ser una cifra traducida por un espacio). Si el programa TrueType incrustado dice que el glifo del blanco no tiene
   contornos (en todas las tablas que un visor puede usar), la tinta de una línea recta larga que cruza su caja (borde de
   un recuadro, separador de columnas) no cuenta como dibujo del blanco; con un glifo que dibuja, o si no se sabe (CFF,
   Type1, Type3), cuenta toda. Las cifras de otros sistemas de escritura («９», «٩») también son dudosas. La versión de pdfminer.six está
   fijada en `requirements.txt` porque `texto_proceso.py` amplía sus clases internas.
8. **Partes con pocos indicios**: si el papel de emisor y destinatario se deduce solo de la posición, o la maqueta
   sugiere los papeles contrarios a los del NIF configurado, ambos NIF bajan de confianza, se pide confirmarlos y la
   clasificación propuesta es «desconocido», no «factura propia». Un NIF ilegible del cliente nunca se sustituye por el
   de un tercero citado en el documento.
9. **Nada se inventa**: cada valor lleva página, posición, método y confianza; lo dudoso se marca para confirmar y lo no
   encontrado queda vacío. La validación fiscal posterior es determinista e independiente de la extracción: bloquea,
   entre otros, la reexpedición de un número ya archivado como emitido (aunque cambien ceros de relleno, espacios o
   mayúsculas), un destinatario con los datos del emisor, un total que no se ha podido leer, una retención de garantía
   sin confirmar, filas sin cantidad cuyo importe no es el precio, datos guardados con tipos no válidos (las ediciones
   así se rechazan con 422), bases sin IVA sin causa de exención elegida (SIN_IVA), operaciones no sujetas y
   facturas demasiado largas para componer. Una retención que difiere en un céntimo de base × % (redondeo del emisor) se
   pide confirmar, como la cuota.

## Operaciones exentas (E1–E6)

- **Modelo**: cada fila de `desglose` (borrador y versión inmutable de la factura) es *sujeta* (`calificacion` S1, `tipo_iva`,
  `base`, `cuota`) o *exenta* (`operacion_exenta` E1–E6, `base`, `motivo_exencion`; sin tipo ni cuota). Una base sin
  calificación ni causa (la que propone la extracción para una factura sin IVA) bloquea con `SIN_IVA` hasta que el usuario
  elige la causa o indica el IVA. Son JSON dentro de `borradores.datos` y `facturas.datos`: no hay tablas ni columnas
  nuevas, y la copia, el diario y la restauración los conservan como el resto de los datos aprobados
  (`tests/test_16_x_exentas.py::test_factura_exenta_sobrevive_a_copia_y_restauracion`).
- **Registro** (`xmlgen.registro_alta`): `DetalleDesglose` con `Impuesto` 01, `ClaveRegimen`, `OperacionExenta` y
  `BaseImponibleOimporteNoSujeto`, sin `CalificacionOperacion`, `TipoImpositivo` ni `CuotaRepercutida` (choice del XSD y
  reglas AEAT 1195, 1196 y 1238). E2 y E3 van con `ClaveRegimen` 02 (con la 01 se rechazan, regla 1199; con la 02 solo se
  admiten exentas, regla 1286); el resto con 01. E5 se bloquea: exige identificar al destinatario con `IDOtro` (regla
  1289) y la aplicación solo admite NIF españoles. `CuotaTotal` = suma de las cuotas de las filas sujetas e `ImporteTotal`
  = bases (sujetas y exentas) + cuotas; la retención de IRPF sigue sin formar parte de ninguno. La huella no cambia.
- **Validación** (`borradores.validar`): la causa se confirma siempre (`EXENTA_CAUSA`), la mención es obligatoria
  (art. 6.1.j RD 1619/2012; ≤ 500 caracteres imprimibles con las fuentes del PDF, con alguna letra o cifra: «.» no vale) y
  una fila exenta no lleva tipo ni cuota. La mención se guarda, valida y expide con los saltos de línea CRLF o CR como LF y
  los tabuladores como espacios (`borradores.normalizar_mencion`).
  Un documento que menciona una exención sin ninguna base exenta pide confirmarlo (`EXENCION_MENCIONADA`); antes era un
  bloqueo. No sujetas (N1/N2), inversión del sujeto pasivo (S2), recargo de equivalencia e IGIC/IPSI siguen bloqueados.
- **Extracción** (`Extractor.exencion`): solo un artículo de la **Ley del IVA** da la causa: 20 → E1, 21 → E2, 22 → E3,
  23 y 24 → E4, 25 → E5. Vale el renglón que menciona la exención («exento/a», «exención») y cita el artículo (también en
  el renglón siguiente de la misma frase), o el que cita el artículo de la Ley 37/1992 sin decir «exenta». El artículo es
  de la Ley del IVA si la cita la nombra («art. 20.Uno.9º LIVA», «Ley 37/1992, art. 20») o, si no nombra ninguna norma, si
  la misma frase dice «exenta» y nombra el IVA («Exenta de IVA según el art. 20.Uno.9º»); el de otra norma («art. 22 del
  contrato», «art. 25 de la Ley 49/2002», un reglamento, el IRPF) no cuenta. La mención propuesta es la frase citada que
  contiene el artículo (más la contigua que dice «exenta», si la del artículo no lo dice), sin las demás frases del
  renglón: de «… exento de IVA (art. 20.Uno.23º LIVA). Gastos de comunidad incluidos (art. 20 LAU).» solo la primera.
  Sin ese artículo no se propone causa ni se inventa la mención: «Operación exenta de IVA», «IVA EXENTO» o un «exento» de
  otro contexto («exento de responsabilidad», «exento de retención») dejan la base **sin calificar**, que sigue bloqueada
  (`SIN_IVA`; si la frase nombra el IVA, el mensaje la cita) hasta que el usuario elige la causa y escribe la mención.
  `borradores.datos_desde_extraccion` convierte en base exenta (o sin calificar) una factura sin IVA (o con filas al 0 %
  sin cuota) cuyo total es la base. En una factura **mixta** separa la base exenta solo si todo cuadra al céntimo (total
  impreso = bases sujetas + cuotas + base exenta): (a) si la extracción da las filas sujetas con su base y una base
  imponible mayor, y el documento menciona una exención del IVA (sin artículo, la base exenta queda sin calificar); (b) si
  no, con las cifras de los renglones del cuadro de totales (`Extractor._importes_factura_mixta`): la base exenta rotulada
  («BASE EXENTA 650,00»), la base imponible total sin tipo («BASE IMPONIBLE 750,00» junto a «BASE 21 % 100,00»; también
  «SUBTOTAL», «SUMA» o «BASE TOTAL») o la base de la cuota («I.V.A. 21 % s/ 100,00 21,00»), y entonces solo si el
  documento cita el artículo de la Ley del IVA (unas tasas, suplidos u honorarios «exentos» sin él nunca se convierten en
  base exenta), cada fila sujeta cumple base × tipo = cuota y la base exenta impresa (si la hay) es exactamente la
  diferencia (`borradores._mixta_impresa`). En los dos casos, con la causa del artículo, la diferencia solo se propone
  como exenta si **el documento la respalda** (`borradores._respaldo_exenta`): una base exenta impresa igual a ella (o
  una fila al 0 % sin cuota), o las líneas de conceptos de la operación exenta —las que dicen «exento/a», o nombran el
  objeto de la frase de la exención o del apartado del art. 20.Uno citado (vivienda, enseñanza, sanidad, seguro…)—
  suman exactamente la diferencia y las demás líneas suman exactamente las bases sujetas. Una base total, un SUBTOTAL o
  una SUMA iguales a la suma de las líneas no respaldan nada (solo dicen que la diferencia no lleva IVA). Tampoco se
  separa si el documento tiene suplidos, recargo de equivalencia, inversión del sujeto pasivo, operaciones no sujetas o
  importes ajenos a la base (`INDICIOS_SIN_MIXTA`; el indicio `importes_ajenos` de `Extractor.indicios` señala tasas,
  IBI, basuras, fianzas, depósitos de garantía, indemnizaciones, provisiones de fondos, gastos por cuenta del cliente o
  repercutidos, y se busca si el documento menciona una exención o cita un artículo 20–25 con las mismas expresiones con
  las que `exencion()` propone la causa —«exentos», «Art 20», «artículos 20 y siguientes»—, más amplias que las del
  indicio `exencion`); además, una línea de conceptos con esas palabras nunca forma parte del respaldo aunque falte el
  indicio. En una factura sin IVA (toda la base exenta, sin diferencia que calcular) el indicio no impide la propuesta,
  pero la confirmación `EXENTA_CAUSA` cita el renglón para que se compruebe que la base no lo incluye. Estas salvaguardas dependen de que el documento use esas palabras: un
  importe ajeno con otro nombre en una línea que comparta objeto con la exención podría acabar en la diferencia; por eso la
  confirmación `EXENTA_CAUSA` de esa base dice que es una diferencia calculada (y de qué cifras) y cita las líneas que la
  forman (`borradores.origen_base_exenta`). En los demás diseños (cifras en columnas bajo sus rótulos, una «SUMA» que la
  extracción lee como línea de conceptos, varios tipos cuyas bases no lee, una diferencia sin respaldo) el desglose no
  cambia y el descuadre bloquea hasta que el usuario separa las bases en el editor.
  El evaluador del corpus compara en el campo `desglose` las filas sujetas (tipo, base, cuota) y las filas sin IVA: falla
  si el borrador propone una fila exenta o sin calificar que el desglose esperado no tiene, o al revés (una fila exenta
  esperada se escribe `{"operacion_exenta": "E1", "base": "650.00"}`, o con `null` si queda sin calificar); un desglose
  esperado vacío no se compara, como antes, así que las cifras del corpus no cambian.
- **PDF** (`pdf_final`): «Base exenta (E1 · art. 20 Ley 37/1992)» en el cuadro de totales y la mención debajo; la factura
  se compone siempre (no se superpone el QR sobre el original) y la verificación comprueba que la mención está impresa
  (sin las letras giradas de la marca de agua). El QR lleva el `ImporteTotal`.
- **Simulador**: aplica las reglas 1199, 1207, 1208, 1238, 1286 y 1289 del documento de validaciones.

## OCR e IA: qué información reciben

| Servicio | Datos que recibe | Configuración de privacidad | Coste |
|---|---|---|---|
| Tesseract (local) | Imágenes de páginas sin texto nativo | Proceso local, sin red | 0 € |
| IA externa | **Ninguno** (no se usa) | — | 0 € |

## Costes estimados (orientativos, IVA no incluido)

| Concepto | Opción | Coste |
|---|---|---|
| Servidor | VPS en la UE, 2 vCPU / 4 GB / 40 GB | ≈ 5–15 €/mes |
| Copias externas | Almacenamiento de objetos en la UE con versionado (≈ 10–50 GB) | ≈ 1–5 €/mes |
| Dominio y HTTPS | Dominio propio + Let's Encrypt | ≈ 10–15 €/año |
| Certificado electrónico | Representante de persona jurídica o sello (FNMT u otro prestador cualificado) | Según la tarifa vigente del prestador (consultar) |
| OCR / IA | Local / no se usa | 0 € |
| Mantenimiento | Actualización de esquemas, validaciones y normativa | Horas de desarrollo según cambios de la AEAT |
