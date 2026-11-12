# Contrato de la API (v1)

Base: `/api`. JSON UTF-8. Autenticación por cookie de sesión `sid` (HttpOnly, SameSite=Strict).
Toda petición que no sea GET debe llevar la cabecera `X-CSRF-Token` con el valor `csrf` devuelto por
`POST /api/sesion` o `GET /api/yo`. Errores: `{"detail": "mensaje"}` con 400/401/403/404/409/422. Un campo JSON de tipo
erróneo (número, lista u objeto donde se espera un texto, etc.; también en `POST /api/sesion` y `POST /api/instalar`), un
identificador del cuerpo escrito con otras «cifras» («²», «①», «３») o un entero fuera del rango admitido (también un
identificador en la ruta, p. ej. `2**70`) responden 422, nunca 500. Un fallo inesperado responde 500 con un mensaje que
solo remite a lo que quien lo recibe puede consultar: a la actividad de la empresa si la ruta es de una empresa de la que
es miembro (allí queda anotado), a la de la plataforma si es el operador, o a avisar al operador en los demás casos. Se
anota sin datos (plantilla de la ruta y tipo de error), una fila por ruta, tipo y minuto con el número de veces
(`error.interno` `{ruta, tipo, veces, minuto}`). La fila de un minuto aparece cuando ese minuto ha terminado (consultar la
actividad nunca escribe el minuto en curso, así que alternar errores y consultas no multiplica las filas).

Importes siempre como **cadena decimal con punto** (`"1028.50"`); fechas ISO `aaaa-mm-dd`.

## Sesión e instalación
- `GET /api/estado` → `{instalada: bool, version, entornos_habilitados: {simulador, pruebas, produccion}, registro_abierto: bool}` (sin sesión)
- `POST /api/instalar` `{email, nombre, clave, registro_abierto?: bool, empresa: {nif, razon_social, domicilio}}` → solo si no hay usuarios. Crea el operador de la plataforma (`superadmin`) + su empresa. `registro_abierto` (por defecto `false`) abre el registro de cuentas.
- `POST /api/sesion` `{email, clave}` → `{usuario: {id, email, nombre, superadmin, cambiar_clave, alta_origen, clave_restablecida_en}, csrf, empresas: [{id, nif, razon_social, rol}]}`. 422 si `email` o `clave` no son textos; 401 si no son correctos
- `GET /api/yo` → igual que arriba. `DELETE /api/sesion`.
- `alta_origen`: `instalacion | registro | invitacion | alta_operador | version_anterior`. Las cuentas que existían al actualizar desde la versión anterior (salvo las del operador) llevan `version_anterior` y `cambiar_clave = 1`: su contraseña la fijó otra persona y deben elegir una nueva al entrar.
- Con `cambiar_clave = 1` (contraseña temporal o cuenta de la versión anterior) la sesión solo admite `GET /api/yo`, `PUT /api/yo/clave` y `DELETE /api/sesion`; el resto responde 403 «Debe cambiar su contraseña antes de continuar».

## Empresa y configuración (rol admin para modificar)
- `GET /api/empresas/{eid}` → empresa con `ambito` = `{puede_emitir, motivos[]}`, `ambito_confirmado_en`, `relacion_usuario`, `declaracion_autorizacion_en`
- `PUT /api/empresas/{eid}` `{razon_social, domicilio, tipo_contribuyente, territorio, sii (0/1), canarias_ceuta_melilla (0/1), otras_exclusiones, origen_pdf, origen_pdf_detalle}` → empresa (re-evalúa ámbito, invalida confirmación)
  - `tipo_contribuyente`: `sociedad_IS | persona_fisica_IRPF | entidad_renta | otro`; `territorio`: `comun | pais_vasco | navarra`; `origen_pdf`: `plantilla_ofimatica | programa_facturacion | otro`
- `POST /api/empresas/{eid}/ambito/confirmar` → confirma el ámbito evaluado
- `POST /api/empresas/{eid}/autorizacion-usuario` `{relacion: administrador|representante|empleado_autorizado|asesor, declaro: true}` → el usuario declara estar autorizado a actuar por la empresa
- `GET /api/empresas/{eid}/series` → `[{id, entorno, codigo, patron, descripcion, tipo, siguiente_numero, confirmada_en, con_anio, anio_actual,
  anio_confirmacion, siguiente_actual, siguiente_texto, contadores: [{anio, siguiente, siguiente_texto}]}]`. `siguiente_actual`/`siguiente_texto`: el
  siguiente número real (contador del año en curso en las series con `{aa}`/`{aaaa}`, o el único de una serie sin año), escrito con el patrón.
  `siguiente_numero` es solo el número inicial: el del año de confirmación (en los demás años la numeración empieza en 1) o el primero de una serie
  sin año; no cambia al expedir ni al avanzar el contador
- `POST /api/empresas/{eid}/series` `{entorno, codigo, patron, descripcion, tipo: ordinaria|rectificativa, siguiente_numero}` → 409 «Ya existe una serie con el código «…» en ese entorno» si el código ya está en uso en ese entorno; 422 si `codigo`, `patron` o `descripcion` no son textos o `siguiente_numero` no es un entero de 1 a 999999999999 (o texto de cifras ASCII); patrón con `{n}` (número), `{n:3}` o `{nnn}` (número con ceros de relleno hasta 3 cifras; se lee con exactamente ese ancho o con más cifras sin cero inicial), `{aa}`/`{aaaa}` (año), p. ej. `{n}/{aa}` → `166/26`, `{n:3}/A{aa}` → `042/A26`. Un solo marcador de número y como mucho uno de año (422 si no)
- `POST /api/series/{sid}/confirmar`
- `POST /api/series/probar` `{patron, num_serie}` → `{coincide, numero, anio}`
- `POST /api/series/proponer` `{num_serie}` → `{patron, numero, anio, ancho}` (p. ej. «042/A26» → `{n:3}/A{aa}`) o `{patron: null}`. No crea nada
- Avance del contador (números ya usados fuera de esta instalación, p. ej. los que la consulta a la AEAT muestra «solo en la AEAT» tras perder la base de datos y el diario). Solo el administrador de la empresa de la serie (otra empresa, otro rol o serie inexistente → 404); la serie debe estar confirmada (409):
  - `GET /api/series/{sid}/avance?anio=aaaa&siguiente=N` → `{serie_id, codigo, patron, entorno, con_anio, anio, actual, actual_texto, ultimo_expedido, ultimo_expedido_texto, aviso_cadena}` (`aviso_cadena`: texto si la cadena de la empresa en ese entorno está atrasada respecto a la AEAT; avanzar el contador no la reanuda) y, con `siguiente`, `{siguiente, siguiente_texto, saltados: {desde, hasta, cantidad, primero, ultimo, lista, completa}}` (`lista`: todos los números que se saltan, o los 10 primeros y los 10 últimos si son más de 20). No cambia nada. `anio` solo en series con `{aa}`/`{aaaa}` (2000–2100, obligatorio en ellas; 422 en las demás)
  - `POST /api/series/{sid}/avanzar` `{anio?, actual, siguiente, confirmo: true, motivo?}` → lo mismo con `anterior`. `actual` es el contador que se revisó: si ha cambiado desde entonces → 409. Nunca retrocede ni se queda igual (`siguiente` ≤ contador actual o ≤ último número expedido aquí → 409). Enteros estrictos (no texto, decimales ni booleanos; `siguiente` de 1 a 999999999999) → 422; `motivo` hasta 200 caracteres. Queda en la actividad de la empresa (`serie.contador_avanzado`, con los números saltados y el motivo) y en el diario (`serie_contador`; si no puede anotarse → 503 y no cambia nada), de modo que una restauración lo conserva; si el diario se pierde, la conciliación del arranque lo vuelve a anotar desde la base de datos (una sola vez)
- `POST /api/empresas` `{nif, razon_social, domicilio}` → nueva empresa; **cualquier usuario** (queda como admin). Un NIF solo admite caracteres ASCII (se quitan espacios, puntos, guiones y barras): cifras de ancho completo, de otros alfabetos o matemáticas → 422 «NIF no válido» (lo mismo para el destinatario, el titular de una legitimación y el productor). El NIF es único en la plataforma, también en su forma equivalente (C, D, F, G, J, U y V con control dígito o letra): si ya existe, 409 «Esa empresa ya está dada de alta en la aplicación: pida acceso a uno de sus administradores» (sin datos de la existente). 422 si algún campo no es texto; 429 si el usuario (no el operador) supera las altas de empresas por día (`VERIFACTU_EMPRESAS_DIA_MAX`, 20)
- `POST /api/empresas/{eid}/miembros` `{email, rol: admin|emisor|lector, relacion}` (admin) → si ya es miembro, actualiza rol y relación: `{usuario_id, accion: "actualizado"}`; si no, crea una invitación de un solo uso a ese correo: `{accion: "invitacion", invitacion: {…, token, enlace}}` (misma respuesta exista o no una cuenta con ese correo). Compatibilidad: con `clave`, solo el operador de la plataforma crea directamente una cuenta **nueva** (`accion: "alta"`), con contraseña temporal; si el correo ya tiene cuenta, se crea igualmente una invitación (`accion: "invitacion"`, la misma respuesta que sin `clave`): nadie entra en una empresa sin aceptarlo y su contraseña no cambia. Degradar a un administrador anula sus invitaciones pendientes de la empresa; 409 si la empresa se quedaría sin administrador activo. `GET` → lista de miembros (admin)
- `DELETE /api/empresas/{eid}/miembros/{uid}` → quita el acceso de esa persona (admin) o el propio («salir», cualquier miembro) → `{ok, empresas}` (las del usuario de la sesión). Anula sus invitaciones pendientes de la empresa; no borra su cuenta ni lo que expidió; 409 si es el último administrador activo; 404 si no es miembro o quien llama no puede
- `PUT /api/productor` `{nombre, nif, domicilio}` → identifica y confirma al productor del sistema (solo el operador de la plataforma; 403 «Solo el operador de la plataforma puede realizar esta acción» para los demás). `GET /api/empresas/{eid}` y `GET /api/plataforma` lo devuelven en `productor`
- `GET /api/empresas/{eid}` incluye además `avisos: [texto]` (incidencias de comunicación de los tres entornos, con la fecha de inicio en hora peninsular —«desde el 01/07/2026 a las 12:05»— y el nº de registros pendientes de remitir o de confirmar) y `casos_soportados: [texto]`

## Plataforma multiusuario (cuentas, invitaciones y operador)
- `POST /api/registro` `{email, nombre, clave, invitacion?}` (sin sesión) → crea una cuenta normal e inicia sesión (misma respuesta que `POST /api/sesion`). Con el registro cerrado solo con una invitación vigente; una invitación de empresa da acceso a esa empresa con su rol. Con el registro cerrado, una invitación de empresa creada por un usuario normal (no el operador) no crea cuentas salvo que el operador lo permita (`invitaciones_empresa_crean_cuentas`): 403 «… solo puede aceptarla quien ya tenga una cuenta» (misma respuesta exista o no el correo). Errores: 422 datos no válidos; 403 registro cerrado; **409 único** para correo ya registrado, invitación inexistente, caducada, usada, anulada o de otro correo; 429 límite por dirección, por invitación, por quien invita o global por hora. Sin invitación vigente (o con un enlace no válido), cada intento cuenta en el límite por dirección (`VERIFACTU_REGISTRO_MAX_IP`, 5) y en el de intentos de toda la plataforma (10 × `VERIFACTU_REGISTRO_MAX_GLOBAL`). Con una invitación **vigente** no cuenta en ninguno de los dos (el personal de una oficina se da de alta desde la misma IP y nadie agota con sus propias invitaciones un límite común): cuenta en el de esa invitación (10 intentos por hora), en un techo por dirección más alto (`VERIFACTU_REGISTRO_MAX_IP_INVITACION`, 50) y en el de quien la creó, por usuario y por empresa (`VERIFACTU_REGISTRO_MAX_POR_INVITADOR`, 50; no el operador). El límite global de cuentas nuevas por hora (`VERIFACTU_REGISTRO_MAX_GLOBAL`, 30) se aplica siempre, pero las creadas con invitaciones de usuarios (no del operador) de un mismo usuario o empresa (`VERIFACTU_ALTAS_MAX_POR_INVITADOR`, 10) o desde una misma dirección nunca pasan de la mitad
- `POST /api/invitaciones/consulta` `{token}` (sin sesión, limitado por dirección salvo con una invitación vigente, que cuenta en el límite de esa invitación y en el techo por dirección del tráfico con invitación) → `{valida: false}` o `{valida: true, email, empresa, rol, expira_en, crea_cuenta}` (`crea_cuenta`: si sirve para crear una cuenta o solo para aceptarla con una existente). Una invitación deja de ser válida si quien la creó ya no es administrador activo de la empresa (u operador activo, si es de la plataforma)
- `POST /api/invitaciones/aceptar` `{token}` → la cuenta con sesión acepta una invitación de empresa dirigida a su correo → `{empresa_id, empresas}`; 409 si no vale para esa cuenta
- `DELETE /api/invitaciones/{iid}` → anula una invitación pendiente (las de la plataforma, el operador; las de una empresa, sus administradores; 404 en otro caso)
- `POST /api/empresas/{eid}/invitaciones` `{email, rol, relacion?, dias?: 1-30}` (admin) → `{id, empresa_id, email, rol, expira_en, estado, token, enlace, crea_cuenta}`; el token solo se devuelve aquí (se guarda su SHA-256). 429 si el usuario (no el operador) supera las invitaciones pendientes por empresa (`VERIFACTU_INVITACIONES_PENDIENTES_MAX`, 50) o las creadas por día (`VERIFACTU_INVITACIONES_DIA_MAX`, 100). `GET` → lista sin tokens, con `estado`: `pendiente | usada | caducada | revocada`
- `PUT /api/yo/clave` `{clave_actual, clave_nueva}` → cambia la contraseña propia y cierra las demás sesiones del usuario; 403 si la actual no es correcta (cuenta para el bloqueo de 5 fallos), 422 si la nueva no cumple la política o es igual
- Solo el operador de la plataforma (`superadmin`; 403 para los demás):
  - `GET /api/plataforma` → `{ajustes: {registro_abierto, invitaciones_empresa_crean_cuentas}, productor, simulador: {empresas: [{nif, servicio, modo, veces, fijado_en}]} (modos de fallo pendientes que fijó el operador desde la plataforma, con el NIF que escribió; los que fija cada empresa para la suya no se listan, para no mostrar el NIF de empresas de las que no es miembro), totales: {usuarios, usuarios_activos, empresas}, cupos}`
  - `PUT /api/plataforma/ajustes` `{registro_abierto?: bool, invitaciones_empresa_crean_cuentas?: bool}` (al menos uno) → `{registro_abierto, invitaciones_empresa_crean_cuentas}`
  - `GET /api/plataforma/usuarios?q=` → `[{id, email, nombre, activo, superadmin, cambiar_clave, creado_en, alta_origen, empresas (nº), ultima_sesion, bloqueado}]`
  - `PUT /api/plataforma/usuarios/{uid}` `{activo: bool}` → activa o desactiva (cierra sus sesiones y anula sus invitaciones pendientes); 409 sobre la propia cuenta
  - `POST /api/plataforma/usuarios/{uid}/clave-temporal` `{clave}` → contraseña temporal (cambio obligatorio al entrar; cierra sus sesiones); nunca se devuelve. Quien la conoce puede entrar como esa persona: se anota (`usuario.clave_temporal`) en la actividad de la plataforma y en la de **cada empresa** de la cuenta, y su sustitución (`usuario.clave_temporal_sustituida`) también; `GET /api/yo` devuelve `usuario.clave_restablecida_en`
  - `GET|POST /api/plataforma/invitaciones` `{email?, dias?}` → invitaciones de la plataforma (crean una cuenta sin acceso a ninguna empresa)
  - `GET /api/plataforma/actividad` → actividad sin empresa (altas, sesiones, ajustes, invitaciones de la plataforma) en tres listas, cada una con su límite, para que ninguna desplace a las otras: las 500 anotaciones más recientes de auditoría (operador y sistema: contraseñas temporales, bajas, ajustes, verificaciones de NIF, avisos…), las 500 más recientes que cualquier cuenta produce por sí misma (`sesion.inicio`, `usuario.clave_cambiada`, `usuario.registro`) y los 100 `error.interno` más recientes (agregados por minuto), que puede provocar cualquiera, también sin sesión. De los errores técnicos (`error.interno`, `remision.error`, `diario.error`) solo muestra el tipo, la ruta sin identificadores (`/api/empresas/{eid}/…`), `veces` y `minuto`, nunca el mensaje ni el objeto; la actividad de «Mis certificados» de cada usuario no se muestra (la ve su propietario en «Ver usos»)
  - `GET /api/plataforma/empresas/verificacion-nif?nif=` → `{nif, razon_social, verificado, verificado_en, motivo}` de la empresa con ese NIF (o su forma equivalente); 404 si no existe, 422 si el NIF no es válido. No da acceso a nada más de la empresa
  - `POST /api/plataforma/empresas/verificacion-nif` `{nif, verificado: bool, nota?}` → marca el NIF de la empresa como verificado (o retira la marca) tras comprobar el operador que quien la gestiona actúa por ella; misma respuesta. Se anota (`empresa.nif_verificado` / `empresa.nif_verificacion_retirada`, con la nota) en la actividad de la plataforma y en la de la empresa. `motivo`: `operador`, `alta_por_operador` (empresas que da de alta el operador, verificadas al crearlas) o `version_anterior` (empresas que existían al actualizar, que solo podía dar de alta el operador) Cada cambio se anota en el diario con su versión: una restauración aplica el más reciente (no deshace una retirada ni una verificación posteriores a la copia) y lista las empresas cuyo estado cambia (`verificaciones_nif_cambiadas`, actividad `empresa.nif_verificacion_restaurada`)
- El operador **no** accede a los datos de una empresa por serlo: todas las rutas de empresa exigen ser miembro (404 si no). La única vía indirecta es la contraseña temporal (ver arriba), que queda a la vista de los miembros de las empresas afectadas.

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
- `GET /api/borradores/{bid}` → `{id, estado, documento_id, datos, incidencias: [{nivel: bloqueo|confirmar|aviso, campo, codigo, mensaje, clave?, confirmado?}], puede_emitir, cambios: [{en, usuario, campo, valor_anterior, valor_nuevo, motivo}], procedencia: extraccion (campo → {valor, pagina, bbox, metodo, confianza, avisos}), serie_detectada, serie_propuesta (si no hay serie: `{patron, numero, anio, ancho}`), entornos_disponibles: [..]}`
- `PATCH /api/borradores/{bid}?entorno=` `{cambios: [{campo: "destinatario.nif", valor: "...", motivo: "..."}]}` → borrador. Rutas de campo: ver `CAMPOS_EDITABLES` en `app/borradores.py`.
- `POST /api/borradores/{bid}/confirmaciones?entorno=` `{claves: ["FECHA_EXPEDICION:fecha_documento", ...]}`
- `GET /api/borradores/{bid}/vista-previa.pdf` y `GET /api/borradores/{bid}/vista-previa/{n}.png` → con marca "BORRADOR – SIN VALOR FISCAL" y sin QR
- `POST /api/borradores/{bid}/emitir` `{entorno: simulador|pruebas|produccion, clave_idempotencia: uuid, confirmo: true}` → `{factura_id}` (idempotente: misma clave → misma factura; la clave es de cada empresa: la misma clave en otra empresa no choca ni revela nada; en la misma empresa, con otro borrador → 409)
- `POST /api/borradores/{bid}/descartar` `{motivo}`

### Desglose: filas sujetas y exentas
Cada fila de `datos.desglose` es una de estas (claves admitidas en una fila: `tipo_iva`, `base`, `cuota`, `calificacion`,
`clave_regimen`, `impuesto`, `operacion_exenta`, `motivo_exencion`; cualquier otra se rechaza con 422):
- **Sujeta**: `{calificacion: "S1", tipo_iva, base, cuota, clave_regimen: "01", impuesto: "01"}` (tipos 21, 10 o 4 %).
- **Exenta**: `{operacion_exenta: "E1".."E6", base, motivo_exencion, calificacion: null, tipo_iva: null, cuota: null,
  clave_regimen, impuesto: "01"}`. `motivo_exencion` es la mención legal que se imprime en la factura (art. 6.1.j RD
  1619/2012; máx. 500 caracteres imprimibles, con alguna letra o cifra). Los saltos de línea CRLF o CR se guardan como LF
  y los tabuladores como espacios (al editar, al validar y al expedir). Al expedir, `clave_regimen` se fija a `02` (exportación) para E2 y E3 y a
  `01` para las demás. E5 se bloquea (exige destinatario extranjero, regla AEAT 1289).
- **Sin calificar** (`operacion_exenta`, `calificacion`, `tipo_iva` y `cuota` vacíos): base sin IVA que propone la
  extracción cuando el documento no repercute IVA; bloquea (`SIN_IVA`) hasta elegir la causa o indicar el IVA.

Rutas editables por campo: `desglose.N.(tipo_iva|base|cuota|calificacion|operacion_exenta|motivo_exencion)`
(`operacion_exenta` solo admite E1–E6 y `calificacion` S1, S2, N1 o N2; si no, 422). Para cambiar una fila de sujeta a
exenta o al revés, la interfaz sustituye la lista completa (`campo: "desglose"`).

Incidencias propias: `SIN_IVA` (bloqueo: base sin IVA y sin causa), `EXENTA_CAUSA` (confirmar: la causa siempre se
confirma), `EXENCION_MENCIONADA` (confirmar: el documento menciona una exención y ninguna base es exenta) y los bloqueos
`EXENTA_MENCION_FALTA` (también si la mención solo tiene signos de puntuación o espacios), `EXENTA_MENCION_LARGA`, `EXENTA_MENCION_CARACTERES`, `EXENTA_CON_CUOTA`, `EXENTA_Y_SUJETA`,
`EXENTA_CAUSA_NO_VALIDA`, `EXENTA_NO_SOPORTADA` (E5), `EXENTA_BASE_CERO`. La extracción (`procedencia.exencion`) incluye la
propuesta `{causa, articulo, motivo, texto, iva, pagina, bbox}` (y `importes`, ver abajo) o `null` (sin mención de
exención). `causa` y `motivo` solo tienen valor si el documento cita un artículo 20–25 de la Ley del IVA; si no (exención
sin artículo, «exento» de otro contexto, artículo de otra norma) son `null`, `iva` indica si la frase nombra el IVA y la
base queda sin calificar (`SIN_IVA`). `texto` es el renglón leído (con el de debajo si la frase sigue) y `motivo`, la
mención propuesta: solo la frase del artículo (y la contigua que dice «exenta», si la del artículo no lo dice), sin las
demás frases del renglón. Con causa, `importes` (si los hay) son las cifras de los renglones del cuadro de totales que
permiten separar una factura mixta: `{exentas: ["650.00"], bases: ["750.00"], sobre: [{tipo, base, cuota}]}` (base
exenta rotulada, base imponible total sin tipo y cuota «IVA 21 % s/ 100,00»); el borrador solo los usa si todo cuadra al
céntimo y el documento respalda la diferencia (ver `docs/arquitectura.md`, «Operaciones exentas»). El indicio
`importes_ajenos` (tasas, IBI, basuras, fianzas, depósitos de garantía, indemnizaciones, provisiones de fondos, gastos por
cuenta del cliente o repercutidos; solo si el documento menciona una exención o cita un artículo 20–25, con las mismas
expresiones que `exencion`: «exentos», «Art 20», «artículos 20 y siguientes») impide separar una base exenta, y en una
factura sin IVA la confirmación `EXENTA_CAUSA` cita su renglón para que se compruebe que la base no lo incluye.

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
- `POST /api/empresas/{eid}/certificados` multipart `archivo` (.p12/.pfx), `clave`, `entorno` (`pruebas|produccion`) → `{id, sujeto, titular_nif, representado_nif, titular, tipo, valido_hasta, legitimacion, origen: "empresa", en_uso}` (admin). Queda en uso y retira la asignación de «Mis certificados» de ese entorno.
- `GET /api/empresas/{eid}/certificados` → certificados de la empresa y asignaciones de «Mis certificados» (`origen: empresa|mis_certificados`, `titular {nif, nombre}`, `propietario`, `es_mio`, `activo`, `en_uso`, `legitimacion` evaluada ahora). Nunca el archivo ni la contraseña.
- `GET /api/empresas/{eid}/certificados/seleccion` → `{simulador, pruebas, produccion}`: certificado que se usará en cada entorno (o `null`) con `legitimacion = {vigente, utilizable, propio, titular, requiere_legitimacion, legitimacion_ok, metodo, representante: {nif, nombre} | null, aviso_nombre, ok, detalle}`. El nombre del representante es el del certificado (la entidad —O— en sellos y certificados de representante de persona jurídica; «APELLIDOS NOMBRE» en los de persona física); el declarado en la legitimación solo se usa si el certificado no trae ninguno. Si difieren (sin contar tildes, puntuación ni orden de las palabras), `aviso_nombre` lo explica (también en `detalle`)
- `POST /api/empresas/{eid}/certificados/asignar` `{certificado_id, entorno: simulador|pruebas|produccion}` → asigna uno de «Mis certificados» (solo su propietario y solo si administra la empresa; si no, 404). En `simulador` solo determina la representación (sin TLS).
- `POST /api/empresas/{eid}/certificados/{cid}/activar` → vuelve a usar un certificado cargado para la empresa (admin)
- `DELETE /api/empresas/{eid}/certificados/asignaciones/{aid}` → `{ok, entorno, en_uso}`: un administrador de la empresa deja de usar en ella un certificado de «Mis certificados» asignado (suyo o de otro usuario, p. ej. de un propietario que ya no la administra). Solo retira esa asignación (404 si no es de esta empresa o ya está retirada); queda en la actividad, en «Ver usos» del propietario y en el diario. En `simulador` una asignación que ya no puede usarse (propietario sin administración, eliminada, sin archivo o caducada) no bloquea: se envía sin certificado.
- «Mis certificados» (solo el propietario; lo ajeno responde 404): `GET /api/yo/certificados` → `[{id, alias, titular, tipo, valido_desde, valido_hasta, vigente, requiere_recarga, asignaciones: [{asignacion_id, empresa, entorno, propio, utilizable, ok, representante, detalle}]}]`; `POST /api/yo/certificados` multipart `archivo`, `clave`, `alias` (409 si ya lo tiene; 422 si no abre o no está vigente; si figuraba «sin archivo» tras una restauración, lo recarga); `DELETE /api/yo/certificados/{cid}` (borra el archivo cifrado y retira sus asignaciones); `DELETE /api/yo/certificados/{cid}/asignaciones/{aid}` (retirar, aunque ya no administre la empresa); `GET /api/yo/certificados/{cid}/usos` (asignaciones, retiradas y cada uso: `certificado.uso` con `detalle.operacion` `remision` o `consulta`)
- `GET /api/empresas/{eid}/produccion` → `{requisitos: [{clave, texto, cumplido, detalle}], habilitada}`
- `POST /api/empresas/{eid}/produccion/autorizacion` `{texto_confirmacion}` → registra la autorización del primer envío real tras mostrar el resumen (`GET .../produccion` incluye `resumen_primer_envio` y en él `texto_confirmacion_requerido`)
- `POST /api/empresas/{eid}/legitimacion` `{metodo: apoderamiento_aeat|colaborador_social, titular_nif, titular_nombre, evidencia_documento_id, notas}` → `{ok, id}` (admin). Legitima al **titular** de un certificado de un tercero (NIF y nombre que irán en `Cabecera/Representante`); la evidencia es un documento de la empresa y es obligatoria. `GET /api/empresas/{eid}/legitimaciones` (`evidencia` y `evidencia_documento_id` solo si el documento existe y es de esta empresa; una legitimación sin evidencia no cuenta); `DELETE /api/empresas/{eid}/legitimaciones/{lid}` (revocar, admin)
- `GET /api/empresas/{eid}/produccion`: el requisito `certificado` dice qué certificado se usará (y lo incluye en `certificado`); si es de un tercero aparece además `representacion` (legitimación de su titular). El requisito `nif_verificado` (pruebas y producción; nunca en el simulador) se cumple si el certificado es propio de la empresa, si el operador verificó su NIF (o la dio de alta él) o si la AEAT ya aceptó un envío de la empresa hecho con su propio certificado. Sin él, la expedición fuera del simulador no se permite y, si el certificado se cambia después de expedir, el envío con el certificado de un tercero no se hace: los registros siguen pendientes con la explicación (lo mismo la consulta a la AEAT)
- `GET /api/empresas/{eid}/auditoria?entorno=` → `{ok, registros, errores[]}` verificación independiente de la cadena
- `GET /api/empresas/{eid}/remision` incluye `aviso_incidencia` y `bloqueados`
- `GET /api/empresas/{eid}/actividad` → las 500 anotaciones más recientes de la empresa y, aparte, sus 100 `error.interno` más recientes (agregados por ruta, tipo y minuto): los errores no desplazan la auditoría de la empresa
- `GET /api/declaracion` → borrador/estado de la declaración responsable
- `POST /api/empresas/{eid}/simulador/modo` `{modo: normal|sin_respuesta|caido|error_500|rechazo|fault_servidor|rechazo_registro|no_apoderado|representante_nif_no_censado|representante_nombre_no_censado, veces: 1–20, servicio?: remision|consulta}` → `{modo, veces, consulta: {modo, veces}, empresa_id, nif}`: modo de fallo del **simulador local** para los próximos envíos (o consultas) de **esa empresa** (un administrador de la empresa; si no, 404, como todas las rutas de una empresa: el operador usa la ruta siguiente). El modo se guarda con el obligado (NIF de la empresa) y solo lo consumen los envíos cuyo `Cabecera/ObligadoEmision` es ese NIF; nunca afecta a otra empresa ni a la AEAT. `normal` lo quita. `no_apoderado` responde 4112; `representante_nif_no_censado` y `representante_nombre_no_censado`, 4123 y 4124 (NIF o nombre del representante no identificados en el censo; el simulador no tiene censo, solo reproduce la respuesta): estos tres solo se aplican, y solo se descuentan, en los envíos que llevan `Cabecera/Representante`. Estos rechazos dejan los registros bloqueados con una explicación en español. Con `servicio: consulta` los modos son `normal|sin_respuesta|caido|error_500|rechazo|fault_servidor|respuesta_invalida|solo_como_representante` y solo afectan a las consultas de esa empresa. `veces` debe ser un entero de 1 a 20 y el modo, uno de la lista (si no, 422). Queda en la actividad de la empresa (`simulador.modo`). `GET .../remision?entorno=simulador` devuelve `simulador: {modo, veces, consulta: {modo, veces}}` con el modo pendiente de esa empresa
- `POST /api/simulador/modo` `{nif, modo, veces, servicio?}` → lo mismo para la empresa de ese NIF (las dos formas del carácter de control valen, como en la verificación del NIF), **solo el operador de la plataforma** (panel «Plataforma»), para cualquier empresa aunque no sea miembro (404 si no existe; queda en la actividad de esa empresa). Los demás reciben 403: un administrador usa la ruta de su empresa (la anterior), de modo que ninguna ruta recibe en el cuerpo la empresa sobre la que actúa quien no es el operador. No acepta `empresa_id` (422) y la respuesta no lo incluye: recorriendo identificadores, el operador podría averiguar el NIF de empresas de las que no es miembro. Sin `nif` → 422 (ya no hay un modo común a todas las empresas). Cada modo guarda quién lo fijó: `GET /api/plataforma` devuelve en `simulador.empresas` solo los pendientes que fijó el operador (con esta ruta), no los que un administrador fija para su empresa, y la respuesta al operador tampoco los muestra (salen como `normal`)

## Consulta a la AEAT (ConsultaFactuSistemaFacturacion)
- `POST /api/empresas/{eid}/consulta` `{entorno, ejercicio: "aaaa", periodo: "01".."12", num_serie?}` (rol emisor) → consulta todas las páginas
  (IndicadorPaginacion/ClavePaginacion) y compara con los registros locales del mes de expedición:
  `{entorno, ejercicio, periodo, num_serie, consultado_en, paginas, destino, servicio, evidencia, evidencias: [carpeta], avisos: [texto],
  codigos_aviso: [cadena_atrasada|cadena_ambigua|anulacion_sin_huella|consulta_parcial_representante], respuesta_ajena: [texto], parcial, indicador_representante, cadena_atrasada,
  estado_cadena, avance?: {meses_comprobados, fiable, motivo}, registros_aeat, resumen: {coinciden, solo_aeat, solo_local, distintos, conciliables, revisar},
  coinciden: [...], solo_aeat: [...], solo_local: [...], distintos: [...]}`. `servicio`: «el simulador local» o «la AEAT» (todos los textos
  nombran el servicio realmente consultado). La consulta pide `MostrarSistemaInformatico=S` y va **sin** `IndicadorRepresentante` aunque el
  certificado sea de un tercero (con él, según el XSD, solo se devolverían los registros que presentó el representante); solo si la respuesta es
  un 4140 (sin apoderamiento para consultar) se repite con `IndicadorRepresentante=S` y el resultado es **parcial** (`parcial: true`, aviso
  `consulta_parcial_representante`): las filas «solo en local» no se dan por ausentes (`revisar: false`) y no se proponen avances de contador ni
  reanudar la cadena. `cadena_atrasada`: `{id_emisor, num_serie, fecha_expedicion, fecha_hora_huso, huella, estado_aeat, tipo,
  registros_posteriores, de_esta_instalacion, ambigua, candidatos, cabeza_local}` del último registro de la cadena que la AEAT tiene de esta
  instalación (por `SistemaInformatico`; si la respuesta no lo trae, de cualquier sistema del obligado, salvo sin cadena local) entre los que
  tienen una huella que no consta aquí y son posteriores al último registro local: los encadenados a él (`Encadenamiento/RegistroAnterior`) y los
  generados en su mismo segundo o después. El último es el que ningún otro de ellos tiene como `RegistroAnterior` (la hora solo tiene segundos);
  si hay varios (la consulta solo devuelve el último registro de cada factura, así que una subsanación o una anulación deja huecos), el generado
  más tarde, y si coinciden en el segundo, el que no termina el tramo que empieza en el último registro local. Si aun así no puede saberse,
  `ambigua: true`, `candidatos: [{num_serie, fecha_expedicion, fecha_hora_huso, huella}]` y aviso `cadena_ambigua`: la cadena queda atrasada
  igualmente y no se reanuda desde ninguno. Queda guardado (tabla `cadenas_aeat`, diario `cadena_aeat`, actividad `cadena.atrasada_aeat`)
  y bloquea expedir, anular y subsanar en ese entorno (`CADENA_ATRASADA`; 409) hasta reanudar la cadena. Esas filas llevan
  `posterior_a_la_cadena: true`. Aviso `anulacion_sin_huella`: la consulta da por anulada una factura con la huella de su registro de alta y
  aquí no consta esa anulación (la AEAT podría responder así; pendiente de contrastar en pruebas): esa anulación es un registro de la cadena que
  no se ve, así que no se reanuda la cadena mientras ocurra. `estado_cadena`: `{estado: atrasada|anclada, num_serie, fecha_expedicion, fecha_hora_huso, huella}` o null.
  Cada fila: `{num_serie, fecha_expedicion (dd-mm-aaaa), factura_id?, registro_id?, tipo_registro?, estado_local?, estado_local_texto?, huella_local?,
  estado_aeat?, huella_aeat?, codigo_error_aeat?, descripcion_error_aeat?, id_peticion?, timestamp_presentacion?, importe_total_aeat?, revisar, nota|motivo,
  conciliable?, estado_propuesto?}`; las de `solo_aeat` cuyo número es de una serie local cuyo contador no ha llegado a él llevan además
  `{serie_id, serie_codigo, serie_patron, anio_contador, numero_aeat, siguiente_local, siguiente_propuesto?, meses_comprobados?}`.
  `siguiente_propuesto` (para avanzar el contador) solo se da en una consulta del mes completo (sin `num_serie`), no parcial y fiable: el servidor
  consulta además los meses siguientes del ejercicio hasta el actual (todas las páginas; si la serie no lleva año, también los de los años
  siguientes, como mucho 24 meses en total: si ese límite no llega al mes actual, no se propone nada y `avance.motivo` lo explica) y propone el mayor número de esa serie y año del contador en todos ellos + 1; `meses_comprobados`
  (`["10/2026", "11/2026"]`) dice qué meses cubre. No es «el mayor que tiene la AEAT» en todo el año: los meses anteriores al consultado no se
  miran. Si uno de esos meses no puede consultarse de forma completa y fiable, no se propone nada (`avance.fiable: false`, `avance.motivo` y un
  aviso). Con `num_serie` la nota de la fila indica que se consulte el mes completo. `ejercicio`: 4 cifras ASCII de 2000 a 2100 (otras cifras Unicode,
  «0000» o fuera de rango → 422). Si la Cabecera (NIF del obligado) o el PeriodoImputacion de la respuesta no corresponden a lo consultado, se
  indica en `respuesta_ajena` y en `avisos`, y no se ofrece conciliar nada. No modifica registros ni facturas (solo guarda, si procede, la
  cadena atrasada). Mismas salvaguardas que la remisión (también la regla del representante en el simulador): pruebas/producción → 403 si el interruptor del servidor
  está desactivado o faltan requisitos (`produccion.comprobar`); sin certificado vigente → 403; fallo del servicio (sin conexión, sin respuesta, HTTP 5xx,
  SOAP Fault) → 502 con el motivo; otra consulta en curso de la misma empresa y entorno → 409. Petición y respuesta de cada página se guardan en
  `evidencia` (almacén). Queda en la actividad (`aeat.consulta`).
- `POST /api/empresas/{eid}/consulta/reanudar-cadena` `{entorno, huella, confirmo: true}` (rol admin, CSRF) → `{reanudada, ya_reanudada, ancla:
  {estado, id_emisor, num_serie, fecha_expedicion, fecha_hora_huso, huella, anclada_en}, registros_posteriores?, cabeza_local?, evidencias,
  meses_consultados, mensaje?}`.
  «Reanudar la cadena desde el último registro de la AEAT»: vuelve a consultar en el servidor (todas las páginas, sin `IndicadorRepresentante`)
  todos los meses desde el del último registro local hasta el actual (sin registros locales, desde el alta de la empresa), los meses de las
  facturas locales (una anulación o subsanación posterior de una de ellas lleva su fecha de expedición) y el del registro detectado, como mucho
  120 (los más recientes; `meses_consultados` dice cuáles), y toma el último registro de la cadena de esta instalación posterior a la cabeza local (con la regla de `cadena_atrasada`). `huella`
  (64 hexadecimales en mayúsculas; si no → 422) es la del registro que el administrador revisó: si ya no es la del último → 409 (y se actualiza la
  cadena atrasada). Si no puede saberse con seguridad cuál es el último (`ambigua`) o la consulta muestra una anulación sin su propia huella
  (`anulacion_sin_huella`) → 409 con la explicación, y la cadena sigue atrasada: hay que revisarlo con el operador de la plataforma. Nunca se usan otros datos del navegador. Guarda el ancla (`cadenas_aeat`, estado `anclada`), se audita
  (`cadena.reanudada_aeat`) y se anota en el diario (`cadena_aeat`) en la misma transacción (si no puede anotarse → 503 y no cambia nada). El
  siguiente registro (alta o anulación) usa el ancla como `Encadenamiento/RegistroAnterior`, la guarda en `registros.ancla_aeat` y la retira.
  Repetirlo con la misma huella no cambia nada (`ya_reanudada: true`); sin registros posteriores → 409 (o, si estaba atrasada, se marca resuelta:
  `reanudada: false` con `mensaje`, actividad `cadena.atrasada_resuelta`). Los textos nombran el servicio («el simulador local» o «la AEAT»).
  Una respuesta parcial, ajena o fuera de esquema → 409.
- `POST /api/empresas/{eid}/consulta/conciliar` `{entorno, registro_ids: [id], confirmo: true}` (rol admin) → `{conciliados: [{registro_id, factura_id,
  num_serie, estado_anterior, estado_nuevo}], no_conciliados: [{registro_id, num_serie, motivo}], incidencia_cerrada: bool}`. `registro_ids`: enteros
  de 1 a 2^63−1 (otro valor → 422). Vuelve a consultar a la AEAT y, con el turno del remitente tomado (409 si hay un envío en curso), renovado antes
  de cada página y comprobado de nuevo en la transacción que escribe (si otro proceso lo tomó durante la consulta → 409 y no cambia nada), marca
  `aceptado` (o `aceptado_con_errores`) solo los registros que siguen en pendiente/sin_confirmar/bloqueado (o `rechazado` con código 3000, duplicado;
  nunca uno «enviando»), son el último de su factura y la AEAT tiene con la misma huella y un estado coherente (con una respuesta parcial, lo
  que no devuelve no se concilia). `descripcion_error` y la actividad nombran el servicio consultado («Conciliado con la consulta al simulador
  local…» / «… a la AEAT…»). Si la respuesta no
  cumple `RespuestaConsultaLR.xsd` o no corresponde a lo consultado (obligado, ejercicio o periodo), no concilia nada (502). Cada cambio se anota en la actividad (`registro.conciliado_aeat`) y en el diario. Si
  después no queda ningún registro sin respuesta definitiva en esa empresa y entorno, cierra la incidencia de comunicación (`incidencia_cerrada: true`,
  actividad `remision.incidencia_cerrada`): las facturas siguientes no se remiten con `Incidencia=S`.

## Estados (texto para la interfaz)
Estado de la factura: `borrador`, `requiere_revision`, `emitida`, `anulada`, `rectificada`.
Estado de la comunicación (por registro): `pendiente` → "Envío pendiente", `enviando` → "Enviando",
`sin_confirmar` → "Resultado sin confirmar", `aceptado` → "Registro aceptado", `aceptado_con_errores` →
"Aceptado con errores", `rechazado` → "Rechazado", `bloqueado` → "Bloqueado (requiere acción)".
Nota fija: la aceptación de un registro por la AEAT no valida el tratamiento fiscal de la operación.
