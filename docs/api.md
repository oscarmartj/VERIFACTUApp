# Contrato de la API (v1)

Base: `/api`. JSON UTF-8. Autenticación por cookie de sesión `sid` (HttpOnly, SameSite=Strict).
Toda petición que no sea GET debe llevar la cabecera `X-CSRF-Token` con el valor `csrf` devuelto por
`POST /api/sesion` o `GET /api/yo`. Errores: `{"detail": "mensaje"}` con 400/401/403/404/409/422.

Importes siempre como **cadena decimal con punto** (`"1028.50"`); fechas ISO `aaaa-mm-dd`.

## Sesión e instalación
- `GET /api/estado` → `{instalada: bool, version, entornos_habilitados: {simulador, pruebas, produccion}}` (sin sesión)
- `POST /api/instalar` `{email, nombre, clave, empresa: {nif, razon_social, domicilio}}` → solo si no hay usuarios. Crea admin + empresa.
- `POST /api/sesion` `{email, clave}` → `{usuario: {id, email, nombre}, csrf, empresas: [{id, nif, razon_social, rol}]}`
- `GET /api/yo` → igual que arriba. `DELETE /api/sesion`.

## Empresa y configuración (rol admin para modificar)
- `GET /api/empresas/{eid}` → empresa con `ambito` = `{puede_emitir, motivos[]}`, `ambito_confirmado_en`, `relacion_usuario`, `declaracion_autorizacion_en`
- `PUT /api/empresas/{eid}` `{razon_social, domicilio, tipo_contribuyente, territorio, sii (0/1), canarias_ceuta_melilla (0/1), otras_exclusiones, origen_pdf, origen_pdf_detalle}` → empresa (re-evalúa ámbito, invalida confirmación)
  - `tipo_contribuyente`: `sociedad_IS | persona_fisica_IRPF | entidad_renta | otro`; `territorio`: `comun | pais_vasco | navarra`; `origen_pdf`: `plantilla_ofimatica | programa_facturacion | otro`
- `POST /api/empresas/{eid}/ambito/confirmar` → confirma el ámbito evaluado
- `POST /api/empresas/{eid}/autorizacion-usuario` `{relacion: administrador|representante|empleado_autorizado|asesor, declaro: true}` → el usuario declara estar autorizado a actuar por la empresa
- `GET /api/empresas/{eid}/series` → `[{id, entorno, codigo, patron, descripcion, tipo, siguiente_numero, confirmada_en}]`
- `POST /api/empresas/{eid}/series` `{entorno, codigo, patron, descripcion, tipo: ordinaria|rectificativa, siguiente_numero}`; patrón con `{n}` (número), `{aa}`/`{aaaa}` (año), p. ej. `{n}/{aa}` → `166/26`
- `POST /api/series/{sid}/confirmar`
- `POST /api/series/probar` `{patron, num_serie}` → `{coincide, numero, anio}`
- `POST /api/empresas` `{nif, razon_social, domicilio}` → nueva empresa (el usuario queda como admin)
- `POST /api/empresas/{eid}/miembros` `{email, nombre, clave, rol: admin|emisor|lector, relacion}` → alta de usuario en la empresa; `GET` → lista de miembros (admin)
- `PUT /api/productor` `{nombre, nif, domicilio}` → identifica y confirma al productor del SIF (admin). `GET /api/empresas/{eid}` lo devuelve en `productor`
- `GET /api/empresas/{eid}` incluye además `avisos: [texto]` (incidencias de comunicación con nº de registros pendientes) y `casos_soportados: [texto]`

## Documentos (importación)
- `POST /api/empresas/{eid}/documentos` multipart `archivos` (uno o varios PDF) → `[{documento_id, nombre, duplicado: bool, duplicado_de?, estado, error?}]`
- `GET /api/empresas/{eid}/documentos?q=&clasificacion=` → lista `{id, nombre_archivo, subido_en, paginas, clasificacion, clasificacion_confirmada_en, estado, borrador_id, factura_id, num_serie, destinatario, importe_total}`
- `GET /api/documentos/{did}` → `{documento, inspeccion, clasificacion_sugerida: {sugerida, confianza, motivos[]}, extraccion, borrador_id, duplicados_fiscales: [...]}`
- `GET /api/documentos/{did}/paginas/{n}.png` → imagen rasterizada (ancho/alto de página en puntos en `extraccion.paginas[n-1]`; bbox de campos en puntos, origen arriba-izquierda)
- `GET /api/documentos/{did}/original` → descarga del PDF original intacto
- `POST /api/documentos/{did}/clasificacion` `{clasificacion: preparatorio|emitida_propia|recibida|desconocido, motivo}`
- `POST /api/documentos/{did}/borrador` → `{borrador_id}` (solo `preparatorio` confirmado)
- `POST /api/documentos/{did}/bloqueo` `{motivo}` → marca el documento como no emitible fuera del simulador (admin); `motivo` vacío lo desbloquea

## Borradores (preparación)
- `GET /api/borradores/{bid}` → `{id, estado, documento_id, datos, incidencias: [{nivel: bloqueo|confirmar|aviso, campo, codigo, mensaje, clave?, confirmado?}], puede_emitir, cambios: [{en, usuario, campo, valor_anterior, valor_nuevo, motivo}], procedencia: extraccion (campo → {valor, pagina, bbox, metodo, confianza, avisos}), serie_detectada, entornos_disponibles: [..]}`
- `PATCH /api/borradores/{bid}?entorno=` `{cambios: [{campo: "destinatario.nif", valor: "...", motivo: "..."}]}` → borrador. Rutas de campo: ver `CAMPOS_EDITABLES` en `app/borradores.py`.
- `POST /api/borradores/{bid}/confirmaciones?entorno=` `{claves: ["FECHA_EXPEDICION:fecha_documento", ...]}`
- `GET /api/borradores/{bid}/vista-previa.pdf` y `GET /api/borradores/{bid}/vista-previa/{n}.png` → con marca "BORRADOR – SIN VALOR FISCAL" y sin QR
- `POST /api/borradores/{bid}/emitir` `{entorno: simulador|pruebas|produccion, clave_idempotencia: uuid, confirmo: true}` → `{factura_id}` (idempotente: misma clave → misma factura)
- `POST /api/borradores/{bid}/descartar` `{motivo}`

## Facturas expedidas
- `GET /api/empresas/{eid}/facturas?q=&cliente=&desde=&hasta=&estado=&envio=&entorno=` → `[{id, entorno, num_serie, fecha_expedicion, destinatario_nombre, destinatario_nif, importe_total, estado, estado_envio, estado_envio_texto, creada_en}]`
- `GET /api/facturas/{fid}` → `{factura, datos, registros: [{id, tipo, secuencia, huella, huella_anterior, fecha_hora_huso, estado_envio, codigo_error, descripcion_error, csv, intentos, subsanacion, rechazo_previo}], envios: [...], verificacion: {...}, url_qr, acciones_permitidas: [...]}`
- `GET /api/facturas/{fid}/pdf`, `GET /api/facturas/{fid}/pdf/{n}.png`, `GET /api/registros/{rid}/xml`
- `GET /api/empresas/{eid}/facturas-lote.zip?ids=1,2,3` → ZIP con PDF y XML
- `POST /api/facturas/{fid}/rectificativa` `{tipo: R1|R4, motivo}` → `{borrador_id}`
- `POST /api/facturas/{fid}/anular` `{motivo, confirmo: true}` → registro de anulación
- `POST /api/registros/{rid}/subsanar` `{motivo, confirmo: true}`
- `POST /api/registros/{rid}/reintentar`

## Remisión, certificados y producción
- `GET /api/empresas/{eid}/remision?entorno=` → `{control_flujo, pendientes, sin_confirmar, ultimos_envios: [...]}`
- `POST /api/empresas/{eid}/certificados` multipart `archivo` (.p12/.pfx), `clave`, `entorno` → `{id, sujeto, titular_nif, representado_nif, tipo, valido_hasta, legitimacion}`
- `GET /api/empresas/{eid}/certificados`
- `GET /api/empresas/{eid}/produccion` → `{requisitos: [{clave, texto, cumplido, detalle}], habilitada}`
- `POST /api/empresas/{eid}/produccion/autorizacion` `{texto_confirmacion}` → registra la autorización del primer envío real tras mostrar el resumen (`GET .../produccion` incluye `resumen_primer_envio` y en él `texto_confirmacion_requerido`)
- `POST /api/empresas/{eid}/legitimacion` `{metodo: apoderamiento_aeat|colaborador_social, evidencia_documento_id, notas}`
- `GET /api/empresas/{eid}/auditoria?entorno=` → `{ok, registros, errores[]}` verificación independiente de la cadena
- `GET /api/empresas/{eid}/remision` incluye `aviso_incidencia` y `bloqueados`
- `GET /api/empresas/{eid}/actividad`
- `GET /api/declaracion` → borrador/estado de la declaración responsable
- `POST /api/simulador/modo` `{modo: normal|sin_respuesta|caido|error_500|rechazo|fault_servidor|rechazo_registro, veces}` (solo entorno simulador; admin)

## Estados (texto para la interfaz)
Estado de la factura: `borrador`, `requiere_revision`, `emitida`, `anulada`, `rectificada`.
Estado de la comunicación (por registro): `pendiente` → "Envío pendiente", `enviando` → "Enviando",
`sin_confirmar` → "Resultado sin confirmar", `aceptado` → "Registro aceptado", `aceptado_con_errores` →
"Aceptado con errores", `rechazado` → "Rechazado", `bloqueado` → "Bloqueado (requiere acción)".
Nota fija: la aceptación de un registro por la AEAT no valida el tratamiento fiscal de la operación.
