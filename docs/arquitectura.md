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
                         └─ trabajador de remisión ──mTLS SOAP──> AEAT (pruebas / producción)
                                                   └─ o simulador local (app/simulador.py)
```

## Modelo de datos (resumen; detalle en `app/migraciones/0001_inicial.sql`)

- `usuarios`, `sesiones`, `miembros` (rol admin/emisor/lector por empresa y declaración de autorización).
- `empresas` (NIF, razón social y **ámbito del emisor**: contribuyente, territorio, SII, exclusiones, origen de los PDF).
- `productor` (identidad del productor del SIF y número de instalación), `instalaciones`.
- `series` + `series_contadores` (patrón confirmado, correlatividad por año).
- `documentos` (original intacto + SHA-256, inspección, clasificación confirmada, bloqueo de emisión) y `extracciones` (valor, página, posición, método, confianza por campo).
- `borradores` (preparación) y `cambios_borrador` (historial de correcciones con motivo).
- `facturas` (versión inmutable de los datos aprobados, SHA-256, URL del QR, PDF con SHA-256).
- `cadenas` (último registro por obligado/entorno/instalación) y `registros` (XML, huella, cadena exacta de la huella, **estado de la comunicación**).
- `envios`, `envio_lineas`, `control_flujo` (TiempoEsperaEnvio, incidencia, bloqueo del remitente).
- `certificados` (PKCS#12 cifrado AES-256-GCM, titular, representado, legitimación), `legitimaciones`, `autorizaciones_produccion`, `declaraciones`, `actividad`.
- **Disparadores** impiden borrar facturas o registros y modificar sus campos fiscales.

Migraciones: ficheros `app/migraciones/NNNN_*.sql`, aplicados en orden al arrancar y anotados en la tabla `migraciones`.

## Estados

- **Factura:** borrador / requiere revisión (en `borradores`) → emitida → rectificada / anulada.
- **Comunicación (por registro):** pendiente → enviando → aceptado | aceptado con errores | rechazado; o sin confirmar (sin respuesta, se reenvía el mismo XML) o bloqueado (rechazo del envío completo, p. ej. certificado no autorizado). Se conservan código y descripción oficiales y el CSV.

## Seguridad

- Sesión por cookie `HttpOnly`, `SameSite=Strict` (`Secure` con `VERIFACTU_COOKIE_SEGURA=1`); CSRF por cabecera; contraseñas con scrypt; bloqueo temporal tras 5 intentos.
- Autorización en **cada** operación: el recurso se resuelve a su empresa y se comprueba el rol; un recurso ajeno responde 404.
- **Privilegio de instalación** (usuario que instala la aplicación): solo él puede identificar al productor, dar de alta empresas y cambiar el modo del simulador. Ser administrador de una empresa no basta.
- Inicio de sesión con bloqueo tras 5 fallos, con incremento atómico (no se elude con peticiones en paralelo).
- Transiciones de borradores condicionales y atómicas; la expedición vuelve a comprobar dentro de la transacción que el borrador no ha cambiado.
- PDF: validación de tipo y tamaño (15 MB, 30 páginas, 10 páginas de OCR, páginas de hasta 5.000 pt de lado, rasterizado de hasta 4.000 px por lado, flujos descomprimidos de hasta 200 MB —«bombas» de compresión rechazadas—), contraste del número y tamaño de páginas entre pypdf y el motor que rasteriza (poppler), rechazo de imágenes incrustadas de más de 50 Mpx, límites de memoria (1,5 GB), CPU y tamaño de fichero para qpdf, pdftoppm, tesseract y zbarimg, extracción de texto nativo en un proceso hijo con los mismos límites (máx. 50.000 caracteres y 60.000 de texto por página), reglas de extracción de campos en otro proceso hijo (120 s de CPU y tiempo real, mismos límites de memoria; si no termina, el documento se rechaza y nunca se usa una extracción parcial), nada se guarda hasta que el procesamiento termina bien, imágenes de página para la interfaz de hasta 1.600 px y 50 MB por documento, cuerpo de las peticiones limitado antes de leerlo (413), vistas previas con concurrencia limitada, superposición solo si fuera del hueco la página queda idéntica píxel a píxel, procesamiento fuera del bucle de eventos y con concurrencia limitada, detección de JavaScript, acciones automáticas, adjuntos, XFA y firmas; **el navegador nunca abre el PDF original**: se muestran imágenes rasterizadas en el servidor. El texto del documento se trata como datos (expresiones regulares), nunca como instrucciones.
- XML recibido con parser sin entidades externas ni red.
- Certificados: PKCS#12 y contraseña cifrados con AES-256-GCM; clave maestra en fichero `0600` **fuera** de la base de datos y de sus copias; durante el envío la clave privada se entrega al cliente TLS mediante ficheros anónimos en memoria (`memfd`), sin escribirse en disco. La vigencia se comprueba en cada uso. Nunca se devuelven a la interfaz ni se escriben en registros.
- Cabeceras: CSP estricta, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`.
- Registro de actividad de solo anexado, sin secretos.
- Interruptores de servidor `VERIFACTU_PERMITIR_PRUEBAS_AEAT` y `VERIFACTU_PERMITIR_PRODUCCION` (por defecto desactivados): las pruebas de desarrollo no pueden comunicar con la AEAT por accidente.

## Privacidad y conservación

- Los documentos y datos reales permanecen en el servidor del cliente; no hay servicios externos de OCR ni IA.
- Conservación propuesta: facturas expedidas, registros, peticiones/respuestas y originales durante el plazo de prescripción tributaria (4 años, art. 66 LGT) y, si aplica, el mercantil (6 años, art. 30 CCom). Los borradores descartados y documentos no emitidos pueden purgarse tras 12 meses (pendiente de implementar como tarea programada).
- Exportación: descarga individual y por lotes (ZIP con PDF y XML); copias completas (`app/copias.py`).

## Extracción de datos de cualquier diseño de factura (`app/extraccion/campos.py`, versión «reglas-2.2»)

La extracción no depende de una plantilla ni de un emisor concreto: trabaja sobre la **geometría** de las palabras
(texto nativo del PDF o, si no lo hay, OCR local) y sobre reglas genéricas, comprobadas con un corpus sintético de
diseños muy distintos (ver `docs/pruebas.md`).

1. **Renglones y bloques**: las palabras se agrupan por altura (sin mezclar dos renglones aunque un logotipo grande los
   abarque) y se separan en bloques por huecos relativos al espaciado de cada renglón (columnas, cajas laterales).
2. **Etiqueta → valor**: en el mismo bloque («Nº factura: X», «Número de proforma. - X», «Expediente municipal: X»), en el
   bloque de la derecha, o debajo (rejillas «Número | Fecha» con los valores en la fila siguiente). Rótulos bilingües
   («Pedido / PO No.») admitidos.
3. **Partes**: cada NIF/CIF/NIE válido (también con puntos, guiones o espacios: «B-46.871.232», «21.987.654-E»,
   «C . I . F . : B - 00618371») define un bloque (misma columna por solape o alineación). El papel (emisor/destinatario)
   se puntúa por indicios: rótulo de cliente, pie con datos registrales, NIF de la empresa configurada, posición. La razón
   social prefiere la línea con forma jurídica o entidad (S.L., S.A., comunidad de propietarios…); para un NIF de persona
   física, el nombre de la persona antes que el comercial. El domicilio se toma del bloque, del pie («RAZÓN · NIF ·
   dirección · CP ciudad · Tel.») o de la cabecera, cortando teléfonos y correos.
4. **Tabla de conceptos**: cabecera reconocida por clases de columna (concepto, cantidad, unidad, precio, dto., IVA,
   importe, código, periodo/fecha), incluidas cabeceras en dos renglones; cada cifra se asigna a la columna más próxima y
   las de columnas no numéricas (código, periodo) no se confunden con cantidades. La tabla termina en el bloque de totales
   (también si va en una caja a la derecha en la misma fila que otro texto), en una fila solo de cifras o en un párrafo;
   continúa en la página siguiente tras «Suma y sigue» o el pie, saltando la cabecera repetida. **Sin cabecera legible**
   (habitual en escaneos), las columnas se deducen de la alineación de las cifras.
5. **Importes**: etiqueta con su cifra en la misma fila sin texto ajeno en medio, o cuadros de etiquetas con los valores
   debajo, por columnas, incluido el **desglose por tipos** («Tipo IVA | Base | Cuota» con una fila por tipo) y el recargo
   de equivalencia. Se distinguen la retención de IRPF y la **retención de garantía** (que no es IRPF ni cambia la base,
   la cuota ni el total). Un comprobador aritmético busca la combinación coherente base + cuota = total si las etiquetas
   no bastan.
6. **OCR** (páginas sin texto): enderezado automático (±3°), limpieza de motas, contraste, ampliación ×1,5 y borrado de
   los bordes finos de tablas y cajas (con ellos Tesseract se salta el texto de las celdas); Tesseract en modo de texto
   disperso (`--psm 11`), un hilo por proceso (varios OCR simultáneos no compiten), porque la aplicación reconstruye
   renglones y columnas por geometría; unión de decimales separados («17289, 27»); contraste de la razón social con la
   del pie. Los valores leídos por OCR llevan su confianza y
   la cantidad ilegible deducida de importe ÷ precio se marca como «calculado».
7. **Texto que no se ve**: un PDF puede llevar texto que el lector no ve (blanco sin fondo, tapado por una imagen,
   recortado, fuera de la página, en modo invisible, con máscaras o con fuentes cuyo /ToUnicode no corresponde a lo que
   dibujan). `texto_proceso.py` descarta el que reconoce como invisible; si la página es mayoritariamente así, se lee por
   OCR; el resto se contrasta con la imagen representada (tinta bajo cada palabra y, para las cifras de fuentes
   incrustadas, lectura OCR de cada glifo) y lo que no coincide baja a confianza 0,5 con el aviso «puede no verse en la
   página». Las cifras de otros sistemas de escritura («９», «٩») también son dudosas. La versión de pdfminer.six está
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
   así se rechazan con 422) y facturas demasiado largas para componer.

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
