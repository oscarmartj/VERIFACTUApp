# Pruebas de aceptación

Ejecución: `python -m pytest -q` (Python 3.13, Ubuntu 24.04, Tesseract 5.3.4). Resultado del 09/11/2026: **89 pruebas superadas** (≈ 2 min 40 s).

Además de las pruebas propias, el núcleo se sometió a **tres rondas de verificación adversarial** con agentes independientes (revisión de código, búsqueda de fallos por perspectivas —concurrencia, conformidad con la especificación, seguridad— y escépticos que intentaron refutar cada hallazgo). Cada defecto confirmado se corrigió y tiene su prueba de regresión (`test_05` a `test_08`). Un *fuzz* aleatorio (20 semillas × 28 operaciones mezclando emisión, rectificación, anulación, subsanación, reintentos y todos los fallos del simulador) no encontró incoherencias entre estados de factura y registros ni en la cadena.

**Tipo:** LOCAL = lógica pura sin servicios; SIMULADA = recorrido completo con el **simulador local** de la AEAT (`app/simulador.py`), que valida XSD, recalcula huella, detecta duplicados y responde con los códigos del catálogo oficial, pero **no es la AEAT**; REAL = contra servicios de la AEAT.

| # | Caso | Prueba | Tipo | Resultado |
|---|---|---|---|---|
| 1 | Extraer la factura de referencia: 850,00 / 178,50 / 1.028,50 | `test_02_extraccion.py::test_caso1_importes_referencia` | LOCAL (réplica sintética: el PDF real no se recibió) | Superada |
| 2 | Emisor en el pie distinto del destinatario | `test_caso2_emisor_en_pie_y_destinatario` | LOCAL (réplica) | Superada |
| 3 | Referencias de obra, pedido y certificación conservadas en el PDF resultante | `test_caso3_referencias_obra`; `test_03_flujo.py::test_caso6_14_coherencia_y_qr` (texto del PDF definitivo) | LOCAL + SIMULADA | Superada |
| 4 | Datos ausentes, OCR dudoso y descuadres bloquean la emisión | `test_caso4_ocr_dudoso_y_ausentes`, `test_caso4_descuadre_bloquea`, `test_caso4_escaneado_requiere_revision` | LOCAL + SIMULADA | Superada |
| 5 | Casos tributarios soportados (21/10/4 %, varios tipos, retención IRPF) y bloqueo de los no soportados | `test_caso5_casos_tributarios`, `test_ambito_emisor` | SIMULADA | Superada |
| 6 | PDF, datos definitivos, XML y QR coinciden | `test_caso6_14_coherencia_y_qr` (`auditoria.coherencia_factura`) | SIMULADA | Superada |
| 7 | XML válido y huellas contra ejemplos oficiales | `test_01_nucleo.py::test_huella_ejemplos_oficiales` (3 vectores AEAT), `test_xml_alta_y_anulacion_validan_xsd`, `test_xml_invalido_detectado` | LOCAL | Superada (XSD de réplica pendiente de contraste oficial) |
| 8 | Sin duplicados por reimportación, doble clic y reintentos | `test_caso8_duplicados` | SIMULADA | Superada |
| 9 | Numeración y cadena ante emisiones concurrentes | `test_caso9_concurrencia` (6 hilos + mismo número en paralelo) | SIMULADA | Superada |
| 10 | Reinicios, cortes, rechazos, respuestas parciales y sin confirmar | `test_caso10_recuperacion` (caído, sin respuesta → duplicado confirma, HTTP 500, reinicio a mitad, fault 4112 → bloqueado, control de flujo, respuesta parcial, Incidencia=S) | SIMULADA | Superada |
| 11 | Subsanación, rectificación y anulación | `test_caso11_procedimientos` | SIMULADA | Superada |
| 12 | Restaurar copia sin perder continuidad ni reenviar | `test_caso12_copia_restauracion` | SIMULADA | Superada |
| 13 | Otro usuario o empresa no accede a documentos ajenos | `test_caso13_acceso_ajeno` (404 en 12 rutas, 401 sin sesión, 403 sin CSRF, lector sin permisos de escritura) | LOCAL (API) | Superada |
| 14 | Revisión visual del PDF y QR decodificado desde el documento | `test_caso6_14_coherencia_y_qr`, `test_04_pdf.py` (zbar sobre el PDF renderizado); imágenes en `docs/evidencias/` | SIMULADA + revisión visual | Superada |
| — | Entornos de pruebas/producción bloqueados sin requisitos; factura de referencia solo simulable | `test_entornos_bloqueados_sin_requisitos`, `test_documento_referencia_solo_simulable` | SIMULADA | Superada |
| — | Contenido activo (JavaScript) detectado en PDF | `test_inspeccion_contenido_activo` | LOCAL | Superada |
| — | Regresiones de la revisión independiente del núcleo: restauración con datos posteriores a la copia; duplicado tras caída durante el envío; duplicado sin envío previo → revisión; orden estricto y control de flujo tras fallo; falta de servidor reintentada; RechazoPrevio X/S/ausente y SinRegistroPrevio según la tabla oficial; fecha de operación futura bloqueada; rectificativa sobre factura anulada rechazada; reintento manual sin pisar estados definitivos; serie anual que empieza en 1 | `tests/test_05_revision.py` (11 pruebas) | SIMULADA | Superada |
| — | Regresiones de la verificación adversarial (segunda ronda): restauración que nunca deja la aplicación sin base de datos; arranque bloqueado si falta la base y hay diario; empresa y miembros posteriores a la copia; envíos sin colisión de ficheros tras restaurar; conciliación de estados perdidos; línea cortada del diario; fault de servidor confirmado por duplicado; anulación sin respuesta confirmada; anulación rechazada → reanulación con RechazoPrevio=S; simulador exige rechazo previo real (1275); varias rectificativas y anulación de rectificativas; entorno de la rectificativa; expedición en otro entorno; borrador modificado durante la expedición; transiciones de borrador; instalación por obligado; privilegios de instalación; bloqueo de inicio de sesión; descargas con nombres no latinos; certificado caducado; número largo sin invadir el QR; superposición descartada con QR previo; «bomba» de compresión; fechas no válidas | `tests/test_06_verificacion.py` (22 pruebas) | SIMULADA / LOCAL | Superada |
| — | Regresiones de la segunda verificación adversarial: arranque rechazado con base vacía, dañada o atrasada; restauración tras perder el disco de datos (PDF y originales desde el espejo); PDF dañado reparado; orden de recálculo de estados y registros «enviando» al restaurar; línea alterada del diario no reproducida; cambio de rol posterior a la copia; fallo del diario sin pisar la respuesta; duplicado de anulación con alta vigente; incidencia en rechazos del envío completo; caracteres de control; instalación concurrente; superposición con rotación o cajas distintas; imagen desproporcionada; razón social larga sin logotipo | `tests/test_07_ronda2.py` (14 pruebas) | SIMULADA / LOCAL | Superada |
| — | Regresiones de la tercera verificación adversarial: restaurar concilia antes la base viva; último estado sin inflar indicadores; factura dañada en el diario → se detiene y reserva número; miembros versionados; empresa posterior sin facturas; subida excesiva rechazada antes de leerla (413); importación rechazada sin ficheros huérfanos; página con demasiado texto; verificación que no da por bueno un fallo; superposición con estado gráfico desequilibrado o anotaciones; fallo al preparar el envío | `tests/test_08_ronda3.py` (11 pruebas) | SIMULADA / LOCAL | Superada |
| — | Interfaz: recorrido completo con Playwright (importar → clasificar → borrador → confirmar → emitir → estado aceptado), sin errores de JavaScript ni violaciones de CSP, sin desplazamiento horizontal a 390 px | `docs/evidencias/capturas/*.png` | SIMULADA (navegador real) | Superada |

## Extracción con cualquier diseño de factura (corpus)

La extracción se mide campo a campo (número, fecha, tipo, NIF/nombre/domicilio de emisor y destinatario, base, cuota,
total, total a pagar, retención, desglose por tipos, líneas —importes, conceptos, cantidad y precio—, referencias, IBAN,
forma de pago, indicios fiscales y situación propuesta) con `python scripts/evaluar_extraccion.py <carpetas> [--detalle]`.
Todos los documentos del corpus son **sintéticos** (datos ficticios, NIF con dígito de control válido, generados por
`scripts/corpus/*.py`); la factura real solo se usa como prueba privada local.

| Conjunto | Documentos | Uso | Resultado |
|---|---|---|---|
| Desarrollo (`tests/corpus/`): profesionales con IRPF, certificaciones de obra, ERP con rejillas y 2 páginas, recargo de equivalencia, proforma apaisada, rectificativa, carta con totales en frases, minimalista a dos columnas, exenta, presupuesto Courier, escaneada | 22 | Ajuste de las reglas | 100 % de campos (516/516) |
| Reservado 1 (`tests/corpus_holdout/`): telecomunicaciones de 2 páginas, hostal con IVA 10/21, bilingüe ES/EN, emisor a la derecha, alquiler con totales arriba y retención 19 %, autocares con totales en una frase | 6 | **Medición ciega** antes de mirarlo | **83,0 %** de campos a ciegas (NIF, número, fecha, tipo y situación al 100 %; importes 67-83 %). Tras corregir las causas generales que reveló: 100 % (ya no es ciego) |
| Reservado 2 (`tests/corpus_holdout2/`), generado por un agente sin acceso al extractor: bilingüe catalán/castellano, miles con espacio y fechas abreviadas, IVA por línea con descuento negativo, SaaS con «Facturar a / Enviar a», fotógrafa con «Total a percibir», suministro eléctrico recibido de 2 páginas, apaisada de 8 columnas, talonario mecanografiado escaneado | 8 | **Medición ciega** | **84,1 %** de campos a ciegas (NIF, domicilio del emisor, forma de pago, retención, indicios y situación al 100 %). Tras corregir las causas generales: 7 de 8 perfectos; el escaneo mecanografiado conserva errores de OCR |
| Factura real de referencia (`tests/privado/`, fuera del repositorio) | 1 | Prueba privada | 24/24 campos |
| Escaneos simulados (los 22 de desarrollo rasterizados a 200 ppp, girados ±1,2° y desenfocados) | 21 | Robustez del OCR | 90,6 % de campos (importes, desglose, líneas, fechas: 95-100 %; nombres y números: 81-90 % por errores de lectura de caracteres, que se marcan para revisión) |

Pruebas automáticas: `tests/test_09_extraccion_corpus.py` exige ≥ 97 % en desarrollo (≥ 98 % en campos críticos:
número, fecha, NIF, base, cuota, total y desglose), ≥ 85 % en los reservados y la factura real completa (se omite si no
está presente).

Limitaciones conocidas: escaneos de baja calidad (los valores dudosos se marcan en ámbar y exigen confirmación), totales
redactados solo en prosa compleja, y diseños muy distintos de los probados. Ningún valor se inventa: lo no encontrado
queda vacío y bloquea la expedición hasta completarlo.

## Pruebas REALES contra la AEAT: no ejecutadas

| Prueba | Motivo | Qué falta |
|---|---|---|
| Alta, duplicado, subsanación y anulación en `prewww1.aeat.es` | (1) La AEAT corta las conexiones TLS desde el entorno de desarrollo (proxy: «tunnel closed»); (2) no se dispone de certificado electrónico | Ejecutar desde un equipo/servidor con acceso, con certificado de pruebas y `VERIFACTU_PERMITIR_PRUEBAS_AEAT=1` (ver `docs/pendientes-produccion.md`, punto 5) |
| Cotejo del QR en `prewww2.aeat.es` | Mismo bloqueo de red | Abrir la URL del QR de una factura de pruebas registrada |
| Contraste de XSD con la publicación oficial | Mismo bloqueo de red | `python scripts/verificar_esquemas.py` |

## Evidencias generadas (`docs/evidencias/`)

- `factura_simulada_166-26.pdf` y `_p1.png`: factura definitiva (simulador) reconstruida desde la réplica, con QR centrado arriba.
- `registro_alta_166-26.xml`: registro de alta generado; `coherencia_166-26.json`: verificación PDF/XML/QR, cadena y huella.
- `factura_simulada_retencion_p1.png`, `rectificativa_simulada_p1.png`, `cadena_simulada.json`.
- `capturas/`: capturas de la interfaz.
