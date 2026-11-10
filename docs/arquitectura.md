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
   en el cuadro y base = total, la cuota es cero y no hay desglose (factura sin IVA, que la validación bloquea con SIN_IVA);
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
   así se rechazan con 422), facturas sin IVA repercutido (total = base, sin cuota: exentas o no sujetas, no soportadas) y
   facturas demasiado largas para componer. Una retención que difiere en un céntimo de base × % (redondeo del emisor) se
   pide confirmar, como la cuota.

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
