# Guía de uso

**Recorrido:** subir PDF → revisar datos → confirmar la emisión → descargar el PDF definitivo y consultar su estado.

## Cuentas, empresas e invitaciones

La aplicación es **multiusuario**: cada persona tiene su cuenta y solo ve las empresas de las que es miembro.

- **Crear una cuenta**: con el registro abierto, desde «Cree una cuenta» en la pantalla de inicio de sesión; con el
  registro cerrado (lo habitual), solo desde un **enlace de invitación**. Cada uno elige su propia contraseña.
- **Dar de alta su empresa**: «Nueva empresa» (arriba a la derecha; es también la primera pantalla de quien aún no tiene
  ninguna). Quien la da de alta queda como su administrador. Hay tres formas:
  - **«Empiece con el certificado de su empresa»** (recomendada): elija el archivo `.p12`/`.pfx` y escriba su contraseña.
    La aplicación lee el certificado y muestra en una frase la empresa que identifica («Empresa: … · NIF …; válido
    hasta …»), sin guardar nada en claro: el archivo y la contraseña quedan cifrados en el servidor solo unos minutos. Al
    pulsar **«Crear la empresa con estos datos»** se crea la empresa con el NIF y la razón social del certificado (el NIF
    de la entidad en los sellos y certificados de representante; el del titular en los de persona física), su relación
    con la empresa se deduce del tipo de certificado y el certificado queda como el de la empresa **para pruebas y
    producción**. Si el certificado caduca en 30 días o menos, se avisa; si es de un representante y su nombre no es el
    de su cuenta, también. La aplicación no comprueba quién emitió el certificado (lo hace la AEAT al conectar): el NIF
    queda «sin acreditar» hasta que el operador lo verifique o la AEAT acepte un envío hecho con él.
  - **«Probar sin certificado (no se envía a Hacienda)»** (modo práctica): suba una factura o proforma suya. La aplicación
    propone el NIF, la razón social y el domicilio a partir del emisor leído (sin guardar la factura); compruébelos o
    corríjalos y pulse «Crear la empresa de práctica». La factura queda en «Documentos». La empresa solo trabaja en el
    simulador; el certificado se le pide cuando quiera pasar a pruebas o a producción (paso «Certificado»).
  - **«Prefiero escribir los datos de la empresa»**: NIF, razón social y domicilio a mano.
  Si el NIF ya está dado de alta, la aplicación no la duplica (con cualquiera de las tres formas responde lo mismo y no
  revela nada de la empresa existente): pida a uno de sus administradores que le invite.
- **Primeros pasos**: tras el alta, una sola pantalla con lo que falta: 1) subir una factura o proforma reciente (de
  ella se propone el domicilio fiscal y la numeración), 2) confirmar el domicilio («Usar este domicilio»), 3) las
  **tres preguntas sobre su empresa** con la declaración de autorización y 4) revisar y emitir la primera factura.
- **Invitar a otra persona** (administradores): Configuración → **Usuarios y empresas** → correo, rol (administrador,
  emisor o lector) y relación → «Invitar». Se muestra **una sola vez** un enlace de un solo uso que caduca a los 7 días:
  envíelo por un canal de confianza. Con **ese único enlace** la persona crea su cuenta (eligiendo su contraseña) y queda
  en la empresa; si ya tiene cuenta, entra con ella desde el enlace y pulsa «Aceptar la invitación». En los dos casos
  puede marcar a la vez «Declaro estar autorizado a actuar en nombre de …» (si no, lo hará después en Configuración).
  Si ya es miembro, solo se cambian su rol y su relación. Si quien la acepta no tenía ninguna empresa y
  tiene la aplicación abierta en otra ventana, allí la empresa queda elegida en cuanto abre otra pantalla (se le avisa
  «Ya tiene acceso a …»). Las invitaciones pendientes se pueden anular.
  El registro abierto a cualquiera sigue cerrado: solo crea cuentas el enlace de una invitación, de un solo uso, con
  caducidad, dirigido a ese correo y con límites por invitación, por quien invita y por día. El operador de la
  plataforma puede desactivar que las invitaciones de las empresas creen cuentas; entonces su invitación solo la acepta
  quien ya tenga cuenta (y necesita además una invitación del operador).
- **Quitar el acceso** (administradores): en la misma pantalla, «Quitar» junto a la persona. Pierde el acceso al
  momento (si la tenía abierta, la aplicación le avisa «Ya no tiene acceso a esa empresa» y le lleva al inicio) y se
  anulan sus invitaciones pendientes; su cuenta y lo que expidió no se borran. Si degrada a un
  administrador (p. ej. a lector), también se anulan sus invitaciones pendientes. La empresa debe conservar siempre un
  administrador con la cuenta activa.
- **Salir de una empresa**: Mi cuenta → «Salir» junto a la empresa.
- **Mi cuenta**: datos, empresas a las que tiene acceso y **cambio de contraseña** (cierra sus demás sesiones).
- **Contraseña temporal**: si el operador de la plataforma le fija una, al entrar se le pedirá cambiarla antes de nada.
  «Mi cuenta» muestra la fecha del último restablecimiento y queda anotado en la actividad de cada una de sus empresas:
  si no lo pidió, cambie su contraseña y avise a los administradores.
- **Cuentas de la versión anterior**: al actualizar la aplicación, quien ya tenía cuenta (salvo el operador) debe elegir
  una contraseña nueva la primera vez que entra. En la versión anterior la contraseña la fijaba el operador o el
  administrador de su empresa al darle de alta y no podía cambiarse, así que otra persona puede conocerla. La pantalla
  de cambio y «Mi cuenta» lo explican; el cambio queda anotado en la actividad de cada una de sus empresas.
- **NIF con caracteres especiales**: el NIF se escribe con cifras y letras normales (también con espacios, puntos o
  guiones, que se quitan). Las cifras de ancho completo («８») o de otros alfabetos no se aceptan.

### Operador de la plataforma

Quien instala la aplicación es el **operador de la plataforma**: el **productor del sistema** (informático de
facturación), que suscribe la declaración responsable. En **Plataforma** (arriba a la derecha) puede:

- **Abrir o cerrar el registro** de cuentas (cerrado por defecto; también se elige al instalar) y, con el registro
  cerrado, decidir si las invitaciones de los administradores de empresas pueden crear cuentas nuevas (por defecto sí:
  un solo enlace por persona; antes de esta versión, no).
- Crear **invitaciones de la plataforma** (permiten crear una cuenta aunque el registro esté cerrado, sin acceso a
  ninguna empresa) y anularlas.
- Ver las **cuentas** (correo, nombre, alta, nº de empresas, estado), **desactivarlas** (se cierran sus sesiones) o
  fijarles una **contraseña temporal** (deberán cambiarla al entrar).
- Identificar al **productor** del sistema y fijar el **modo de fallo del simulador** local para una empresa (indicando
  su NIF; el panel lista los modos pendientes que usted ha fijado desde él, no los que cada empresa fija para la suya,
  para no mostrarle el NIF de empresas de las que no es miembro). El modo es de esa empresa: nunca afecta a otra.
- **Verificar el NIF de una empresa** (tarjeta «Verificación del NIF de las empresas»): busque la empresa por su NIF y,
  después de comprobar con documentación que quien la gestiona actúa por ella, pulse «Marcar como verificado» (con una
  nota opcional de lo comprobado). Sin esa marca, una empresa que dio de alta un usuario no puede hacer su **primer
  envío a la AEAT** (pruebas o producción) con el certificado de un tercero, por ejemplo una gestoría; con su propio
  certificado no hace falta (la AEAT comprueba su titularidad), y tampoco para las empresas que da de alta usted. La
  marca y su retirada quedan anotadas en la actividad de la plataforma y en la de la empresa. El simulador no la exige.
- Consultar la actividad de la plataforma: ajustes, invitaciones, contraseñas temporales, bajas, verificaciones de NIF y
  avisos; en una tabla aparte, las entradas, cambios de contraseña y altas de las cuentas; y en otra, de los errores
  técnicos solo su tipo y la ruta sin identificadores, agrupados por minuto con el número de veces (la fila de un minuto
  aparece cuando ese minuto ha terminado). Así, ni los errores que provoque cualquiera, también sin sesión, ni cientos
  de inicios de sesión de una cuenta ocultan la actividad anterior. Si restaura una copia, la verificación del NIF de
  cada empresa queda como la dejó usted por última vez (también si la retiró después de la copia).

El operador **no** ve los documentos ni las facturas de ninguna empresa por ser operador: solo los de las empresas de
las que sea miembro (por ejemplo, la que dio de alta al instalar). Una contraseña temporal le permitiría entrar como esa
persona: úsela solo a petición suya; queda anotada en la actividad de cada empresa de la cuenta.

## Menú, papeles y modo avanzado

El menú tiene cuatro entradas: **Documentos** (subir y revisar), **Facturas** (las emitidas), **Configuración** y **Más**.
En **Más** están *Envíos a Hacienda* (estado de los envíos, lo pendiente y lo bloqueado), *Dar de alta otra empresa*,
*Declaración responsable* (también en el pie de cada pantalla) y el interruptor del **modo avanzado**.

Cada persona ve solo lo que su papel le permite usar:
- **Lector**: consulta documentos, facturas y la configuración; no ve la subida de documentos, «Emitir», «Corregir esta
  factura…», «Anular registro…» ni ningún formulario de la configuración.
- **Emisor**: sube, revisa, emite y corrige facturas; ve la configuración como resumen de solo lectura y no ve «Anular
  registro…» ni «Subsanar registro…».
- **Administrador**: además configura la empresa y puede anular y subsanar registros.
Si el papel cambia con la pantalla abierta y se pide algo que ya no está permitido, la aplicación lo explica («Su papel en
esta empresa («Lector») no permite esta acción…») en lugar de un «No encontrado».

El **modo avanzado** es una preferencia de cada persona (desactivado al empezar; *Más → Activar el modo avanzado* o *Mi
cuenta → Preferencias*). Muestra las herramientas técnicas: el editor del patrón de la numeración, *Probar un número*,
*Contadores por año* y *Avanzar contador…*; el detalle de los envíos y la *Verificación independiente de la cadena*; la
*Consulta a Hacienda* (conciliación y *Reanudar la cadena…*); la *Actividad*; la huella, el CSV y el XML de cada registro
y los códigos de incidencia; «Mis certificados» y la legitimación completa. Nada se ha quitado: esas pantallas siguen
funcionando también por su dirección. Los avisos que exigen actuar (incidencia de comunicación, cadena atrasada, facturas
bloqueadas) se ven siempre, con o sin modo avanzado. En el *Modo práctica* la factura no ofrece «Abrir el cotejo en la sede
de la AEAT» (su QR es de prueba).

## 0. Antes de empezar (una vez, rol administrador)

Lo más rápido es seguir **Primeros pasos** (se abre tras dar de alta la empresa y desde Configuración mientras falte algo).

1. **Configuración → Datos de la empresa**: razón social y domicilio (si falta, se propone el leído en su primera factura:
   «Usar este domicilio»). El **tipo de contribuyente** se deduce de la letra del NIF (no cambia lo que la aplicación
   puede expedir).
2. **Configuración → Tres preguntas sobre su empresa**, en un solo paso: ¿tributa en el País Vasco o en Navarra
   (territorio foral)?, ¿lleva los libros del IVA por el SII?, ¿factura operaciones en Canarias, Ceuta o Melilla? Cada
   una se responde con sí o no y **ninguna está marcada de antemano**; marque «Declaro estar autorizado a actuar en
   nombre de …» y pulse **Guardar respuestas**: se evalúan, se confirman y queda registrada su declaración. Con
   cualquier «sí», la aplicación no expide facturas para la empresa y explica por qué. En «Más opciones» puede dejar una
   **nota** sobre casos especiales (no impide emitir: los casos no soportados se detectan en cada factura; antes, escribir
   cualquier cosa, incluso «Ninguna», bloqueaba toda la emisión) y cambiar su relación con la empresa. Si el código postal
   del domicilio (o del emisor de una factura) es de Álava, Gipuzkoa, Bizkaia o Navarra (01, 20, 48, 31) y respondió
   territorio común, se le avisa para que compruebe la respuesta; si guarda un domicilio así después de responder, se
   le vuelven a hacer las preguntas. Cambiar el domicilio o la razón social por lo demás no borra las respuestas. Si sus PDF parecen generados por un programa (según el
   productor y el creador del PDF), también se avisa: ese programa podría ser por sí mismo un sistema de facturación.
3. **Numeración de sus facturas**: no hace falta crearla aquí. Al revisar la primera factura, la aplicación propone la numeración a partir de su número («Esta será la numeración: F-2026/0240, F-2026/0241… ¿Es correcta?») y usted la confirma con un clic; esta pantalla muestra después los siguientes números. Lo que sigue (editor del patrón, *Probar un número*, *Contadores por año*, *Avanzar contador…*) está en el **modo avanzado**. Con el modo avanzado: cree la serie del entorno en el que va a trabajar con su patrón (p. ej. `{n}/{aa}` para «166/26») y el **siguiente número** que toca. Pruébela con un número y **confírmela**. Las rectificativas necesitan una serie propia (p. ej. `R{n}/{aa}`). Si sus números llevan **ceros de relleno** («042/A26», «A057/26»), use `{n:3}` (o `{nnn}`) para el ancho: `{n:3}/A{aa}` → 042/A26, `A{n:3}/{aa}` → A057/26; al superar el ancho se escriben más cifras (1000/A26). Con ese patrón «42/A26» no corresponde a la serie (la aplicación le indica cómo escribirlo). Si un borrador trae un número que no encaja con ninguna serie, el propio borrador pregunta «Esta será la numeración: …, ¿es correcta?» y un administrador la crea y confirma con un clic (o la configura aquí, con el formulario relleno con ese patrón y ese número). No se pregunta si ese número ya está expedido o se confunde con el de otra serie (por ejemplo, «F-2026/240» cuando la serie escribe «F-2026/0240»): entonces el borrador indica cómo escribirlo. Si hay números de una serie ya usados fuera de esta instalación (por ejemplo, tras perder la base de datos y el diario, la consulta a la AEAT muestra 004 a 009 y aquí el contador va por el 004), un administrador puede pulsar **Avanzar contador…** en la serie: indica el nuevo siguiente número, revisa la lista de números que se saltan y lo confirma. El contador nunca retrocede ni puede quedar por debajo de un número expedido aquí; el cambio queda en el registro de actividad y en el diario, y no se puede deshacer. En la tabla, **Siguiente nº** es el número que se expedirá a continuación (el contador de este año, escrito con el patrón) y **Número inicial** el que se indicó al crear la serie: en las series con año es el primer número del año de confirmación (los demás años empiezan en 1).
4. **Certificado** (solo para Pruebas de Hacienda y PRODUCCIÓN; el simulador no lo necesita): se carga una vez y sirve para los
   dos entornos; ver el apartado 7.
5. El **entorno activo** se ve siempre arriba: *Modo práctica: no se envía a Hacienda* (el simulador local, sin red), *Pruebas de Hacienda (sin efectos fiscales)* o **PRODUCCIÓN: facturas reales** (en rojo). Los dos últimos están desactivados hasta completar **Configuración → Empezar a enviar a Hacienda**; mientras solo haya un entorno habilitado no se muestra el selector, pero la etiqueta sí.
6. **Empezar a enviar a Hacienda** muestra los requisitos de su empresa. Lo que depende de la plataforma (productor del
   sistema, declaración responsable, esquemas, interruptores del servidor) se resume en una línea: «La plataforma aún no
   está habilitada para producción (lo gestiona el operador)». El paso **Productor del sistema** solo lo ve el operador.
   La autorización del primer envío real queda plegada mientras falte algo más.

Las fechas y horas se muestran siempre en hora peninsular (también el año y el mes que la aplicación le propone, aunque su
equipo esté en otra zona horaria), con el año completo («12/11/2026, 10:05»), y los importes con coma decimal («1.028,50 €»,
también en los campos que se pueden editar). En **Actividad** (modo avanzado) cada evento se describe en palabras
(«Factura emitida»; el código interno queda en la información emergente). Las confirmaciones de acciones que no se pueden deshacer (quitar a una persona, salir de una empresa, eliminar un certificado…) se abren con «Cancelar» seleccionado: para confirmarlas, pulse el botón rojo.

## 1. Importar

Arrastre uno o varios PDF (máx. 15 MB y 30 páginas cada uno). Si un archivo ya se había importado, se indica y no se duplica. Si otro PDF tiene el **mismo número de factura**, se avisa.

Se admite **cualquier diseño** de factura (no hace falta configurar plantillas): con texto nativo o escaneada (se aplica
OCR local). Recomendación: suba el PDF **antes de enviarlo al cliente**. Una factura ya enviada (o firmada) se archiva,
no se convierte: el QR solo puede añadirse al expedirla. En los escaneos, revise especialmente los campos en ámbar.

## 2. ¿Qué es el documento?

Si el documento es claramente una **proforma, un presupuesto o un borrador** de su empresa (título leído con seguridad, sin
QR de Hacienda, sin firma electrónica y sin otro PDF ni factura con el mismo número), se abre **directamente su borrador**
con la línea «Tratado como proforma o presupuesto · Cambiar». Si no lo es, pulse **Cambiar**: si ya la entregó a su
cliente se archiva (y el borrador se descarta), y lo mismo para una factura de un proveedor u otro documento.

En los demás casos la aplicación hace **una sola pregunta**:
- Su empresa emite un documento titulado «factura» (o está firmado): **«¿Ya entregó esta factura a su cliente?»**. «No,
  todavía no: prepararla para emitir» abre el borrador; «Sí, ya la entregué: solo archivarla» la archiva (una factura
  entregada no se vuelve a emitir ni se le añade un QR).
- Con el QR de Hacienda: solo puede archivarse (o guardarse como factura de un proveedor).
- Factura de un proveedor o documento ajeno: se propone guardarlo como tal.

«Otra situación…» muestra las cuatro opciones. La nota es opcional: quién clasificó el documento y cuándo queda registrado.
Si el original está firmado digitalmente, se conserva intacto y se le avisará de que el PDF definitivo no conservará esa firma.

## 3. Revisar y emitir (una sola pantalla)

En el escritorio, el original a la izquierda con recuadros sobre los datos leídos (verde: fiable; ámbar: dudoso); en el
móvil, detrás del botón **Ver original**. A la derecha:
- **Antes de emitir**, en lenguaje llano y por este orden:
  - lo que **hay que corregir** (p. ej. el total no cuadra, falta el NIF del cliente, caso no soportado), con **Ir al
    campo** y, cuando hay un arreglo seguro, un botón de un clic: «Usar el siguiente número de la serie: F-2026/0242»
    (el número se escribe en el borrador y usted lo ve; el contador no cambia);
  - **preguntas**: «Esta será la numeración: F-2026/0240, F-2026/0241… ¿Es correcta?» (un administrador la confirma y la
    serie queda creada y confirmada; el código interno se pone solo; «No, configurarla de otra forma» lleva a
    Configuración), «¿La operación se realizó el 02/11/2026?» (Sí / No, es de hoy / Otra fecha; si después cambia la
    fecha del documento, se pregunta de nuevo) y, para cada base exenta,
    «¿Esta base está exenta por …?» (una por una);
  - los **campos marcados en ámbar** (lecturas dudosas del número, los importes o el cliente, posible duplicado, total
    calculado…), con **una sola acción**: «He revisado los campos marcados». Si corrige un campo, deja de estar marcado.
    Si después cambia algún dato, la revisión se repite.
  - Los avisos que no impiden emitir quedan plegados.
- **Cliente**, **Factura** (número, fechas, descripción), **IVA** e **Importes**: siempre visibles y editables.
- **Resumen** y el botón **Emitir**.
- **Más opciones**: líneas de detalle (plegadas), datos de su empresa (el NIF se comprueba con el de la empresa: si
  coincide, no hay que confirmarlo), referencias, pago (IBAN, forma de pago, vencimiento), observaciones, tipo de factura,
  datos leídos del documento con su procedencia y confianza, historial de cambios y **Descartar** (con motivos rápidos).

Las lecturas dudosas del IBAN, la forma de pago, el vencimiento y las referencias (obra, pedido, partida presupuestaria…)
se marcan en ámbar pero no hay que confirmarlas: se imprimen en la factura y no se comunican a Hacienda. El NIF y el
nombre del emisor dudosos tampoco, si coinciden exactamente con los de su empresa (salvo que la duda sea si su empresa es
el emisor o el cliente del documento). Los datos del cliente, los importes,
los tipos de IVA, las fechas y el número **nunca** se dan por buenos solos.

Cada cambio se guarda con **Guardar cambios**; la **nota es opcional**. El historial guarda quién, cuándo, el valor
anterior, el nuevo y su procedencia (corrección manual, numeración de la serie, respuesta a una pregunta…). La
procedencia la comprueba la aplicación: un valor escrito a mano figura siempre como corrección manual.

### Operaciones exentas de IVA

En **IVA**, cada fila es **con IVA** (tipo, base y cuota) o **exenta** (solo la base). Para una fila
exenta, elija en la columna *Con IVA o exenta* la causa:

| Causa | Precepto (Ley 37/1992) | Ejemplos |
|---|---|---|
| E1 | art. 20 | alquiler de vivienda (20.Uno.23º), enseñanza, sanidad, seguros, servicios financieros |
| E2 | art. 21 | exportaciones (se declara con la clave de régimen 02) |
| E3 | art. 22 | operaciones asimiladas a las exportaciones (clave 02) |
| E4 | arts. 23 y 24 | zonas francas, depósitos y regímenes aduaneros o fiscales |
| E5 | art. 25 | entregas intracomunitarias: **no soportadas** (exigen un destinatario extranjero) |
| E6 | otros | otras exenciones |

- Debajo de la causa está la **mención** que se imprime en la factura (obligatoria, art. 6.1.j RD 1619/2012). Se propone
  una genérica («Operación exenta de IVA (art. 20 Ley 37/1992)»); escriba el precepto concreto si lo conoce
  («art. 20.Uno.23º»).
- Si el documento no repercute IVA (p. ej. un alquiler de vivienda con «Importe alquiler» y «TOTAL FACTURA»), el borrador
  trae una base **sin IVA** y un bloqueo que pide elegir la causa o, si la operación está sujeta, indicar el tipo y la cuota.
  Si el documento cita la exención con el artículo de la Ley del IVA («exenta… art. 20… Ley 37/1992», «art. 20.Uno.23º
  LIVA»), la causa se propone a partir de ese texto. Si solo dice «exenta de IVA» sin artículo, o «exento» en otro
  sentido («exento de responsabilidad»), no se propone ninguna causa: elíjala usted y escriba la mención con el precepto.
- La aplicación no puede comprobar que una operación esté exenta: **la causa siempre se confirma**, base por base (pregunta en *Antes de emitir*).
- Una factura puede mezclar filas sujetas y exentas. La extracción propone la base exenta solo si el documento cita el
  artículo de la Ley del IVA, las cifras del cuadro de totales cuadran al céntimo (p. ej. «BASE IMPONIBLE 750,00 / I.V.A.
  21 % s/ 100,00 21,00 / TOTAL 771,00») y el propio documento respalda esa base: la imprime («BASE EXENTA 650,00») o las
  líneas de la operación exenta (las que dicen «exento» o nombran lo mismo que la frase de la exención: la vivienda, el
  curso…) suman exactamente la diferencia y las demás líneas, la base sujeta. Un «SUBTOTAL» o una «SUMA» no bastan:
  solo dicen que la diferencia no lleva IVA, no que esté exenta. Tampoco se separa nada si el documento menciona
  suplidos, tasas, fianzas, depósitos de garantía, indemnizaciones, provisiones de fondos, gastos por cuenta del cliente
  o repercutidos (IBI, basuras…). Si toda la factura es exenta, la base se propone igualmente, pero la confirmación de la
  causa cita el renglón con esas palabras para que compruebe que la base no lo incluye. La aplicación lo detecta por esas
  palabras, así que un importe ajeno escrito de otra forma también puede acabar en la diferencia: compruebe siempre de
  qué líneas sale la base exenta (la confirmación de la causa lo indica y dice que es una diferencia calculada). En los
  demás casos el borrador queda bloqueado por descuadre: añada la fila con *Añadir tipo de IVA*, márquela como exenta y
  corrija la base, la cuota y el total.
- La mención debe decir algo (un «.» no vale); los saltos de línea se guardan tal cual y los tabuladores como espacios.
- No están soportadas las operaciones **no sujetas**, la **inversión del sujeto pasivo** ni el **recargo de equivalencia**.

## 4. Vista previa y emisión

**Vista previa** (pestaña junto al original) muestra la factura con la marca «BORRADOR – SIN VALOR FISCAL» y el hueco del QR. **Emitir** abre el resumen (entorno, empresa, cliente, número, fechas, base, IVA y total): en el modo práctica y en Pruebas de Hacienda basta con pulsar **Emitir** (el foco empieza en «Cancelar»; no hay que volver a escribir el número). En **PRODUCCIÓN** se pide una única confirmación fuerte: escribir el número de la factura antes de «Emitir factura REAL» (y la autorización del primer envío real de la empresa sigue siendo previa). Al confirmar se expide la factura, se genera el registro y se envía en cuanto el control de flujo lo permite. Un doble clic no duplica nada, y el servidor vuelve a comprobar todo antes de expedir. Solo se expide lo que usted vio: si otra persona (u otra pestaña suya) cambia el borrador mientras el resumen está abierto, «Emitir» no expide nada, el resumen se cierra, la revisión se vuelve a mostrar con los datos nuevos y se le avisa («El borrador ha cambiado desde que lo revisó…»); lo mismo con «He revisado los campos marcados». Los importes de los avisos se escriben como en la factura («1.028,50 €»).

## 5. Descargar y consultar el estado

En **Facturas** puede buscar por número, cliente, fechas, estado y entorno, y descargar en PDF una a una o por lotes (ZIP con PDF y XML). Cada factura muestra dos estados distintos:
- **Estado de la factura**: emitida, rectificada, anulada.
- **Estado de la comunicación**: envío pendiente, resultado sin confirmar, registro aceptado, aceptado con errores, rechazado o bloqueado, con el código y el mensaje oficiales y el CSV.

> Que la AEAT acepte el registro no significa que haya validado el tratamiento fiscal de la operación.

Si hay una incidencia de comunicación, se muestra arriba, en todas las pantallas, un aviso con la fecha y hora en que empezó y los registros pendientes de remitir o de confirmar; puede seguir facturando y la aplicación reintenta sola (al menos cada hora). El aviso se actualiza al conciliar, reintentar o subsanar y cada vez que se recarga la pantalla **Envíos a Hacienda** (menú «Más»; cada 15 s o con «Actualizar»), y desaparece cuando ya no queda nada pendiente.

## 6. Corregir lo ya expedido

Desde la ficha de la factura: **Subsanar registro** (errores del registro), **Corregir esta factura** (rectificativa por diferencias; R4 por defecto, R1 solo para los casos del art. 80 de la Ley del IVA; se indica qué hay que corregir y se imprime en la factura) o **Anular registro** (factura que no debió expedirse). El número de la rectificativa sale del contador de la serie de rectificativas; si aún no hay ninguna, se propone una a partir de la de la factura (`R{n}/{aa}` para `{n}/{aa}`, `R-{aaaa}/{n:4}` para `F-{aaaa}/{n:4}`), que un administrador confirma desde el borrador. Ninguna operación edita ni borra lo expedido. Ver `docs/tratamiento-documentos.md`.

## 7. Certificados electrónicos: propios o de representante

El certificado sirve para identificarse ante la AEAT al remitir (pruebas y producción). Se carga siempre en la propia
aplicación, con su contraseña, en un formulario seguro: se guarda cifrado y **nunca** vuelve a mostrarse ni se puede
descargar. Hay dos maneras de cargarlo:

- **Para una empresa** (Configuración → Certificado → *Cargar el certificado*): queda ligado a esa empresa y sirve para
  **pruebas y producción** (es el que se carga también en el alta guiada). En *Opciones avanzadas* puede cargar uno para
  un solo entorno (prevalece en ese entorno), usar uno de «Mis certificados» o declarar la legitimación de un tercero.
  Se avisa 30 días antes de que caduque.
- **En «Mis certificados»** (menú «Más», con el modo avanzado): lo carga **una sola vez** y lo usa en todas las empresas que **administra**
  (*Asignar a una empresa*, o en Configuración → Certificado → *Usar uno de «Mis certificados»*). Es útil para una
  gestoría o asesor que lleva varias empresas. Solo usted ve, asigna, retira o elimina sus certificados; los demás
  administradores de la empresa ven cuál se usa, pueden sustituirlo por otro, pero no descargarlo ni llevarlo a otra
  empresa. Si deja de ser administrador de una empresa, su certificado deja de usarse en ella. *Ver usos* muestra cada
  asignación, cada retirada y cada envío o consulta a la AEAT hecho con él.
- Cualquier **administrador de la empresa** puede *Dejar de usarlo en esta empresa* (tarjeta *Certificado que se
  usará*), aunque el certificado sea de otro usuario, p. ej. de alguien que ya no administra la empresa. Solo retira
  esa asignación; el propietario lo ve en *Ver usos*. En el *Modo práctica* (simulador local), una asignación que ya no puede usarse
  no bloquea los envíos: se hacen sin certificado, como antes de asignarlo.

Cada empresa usa **un único certificado por entorno** (uno cargado para los dos entornos sirve en ambos): la tarjeta *Certificado que se usará* indica cuál, de dónde
viene, su titular, su vigencia y en calidad de qué actúa:

- **Propio**: el certificado es de la empresa (sello o persona jurídica con su NIF), de su representante legal o, si es
  un autónomo, del propio titular. Los envíos no llevan representante.
- **De representante**: el certificado es de un tercero (p. ej. el sello de la gestoría). Solo se puede usar si la
  empresa declara en *Uso el certificado de mi gestoría o asesor* (Configuración → Certificado, con el modo avanzado) el **apoderamiento** inscrito en la AEAT o el acuerdo de
  **colaboración social** de ese titular: NIF y nombre del titular (se proponen los del certificado en uso) y el
  documento que lo acredita (impórtelo antes en «Documentos» y clasifíquelo como «Desconocido / no procesar»).
  Entonces cada envío lleva `Cabecera/Representante` con ese NIF y el nombre que figura **en el certificado** (la
  AEAT lo contrasta con su censo); si el nombre que escribió es distinto, la aplicación lo avisa. La legitimación puede
  revocarse. Una
  legitimación cuyo documento de evidencia ya no existe (p. ej., perdido al restaurar una copia) no cuenta: vuelva a
  declararla con el documento.

**Configuración → Empezar a enviar a Hacienda** muestra qué certificado se usará y, si es de un tercero, el requisito de su legitimación. La
aplicación no puede comprobar el apoderamiento: lo comprueba **la AEAT en cada envío**. Por eso, el **primer envío** a la
AEAT (pruebas o producción) de una empresa que dio de alta un usuario, hecho con el certificado de un tercero, exige
además el requisito «NIF de la empresa acreditado»: pida al operador de la plataforma que verifique el NIF aportando la
documentación que acredite que actúa por la empresa. No hace falta si se usa un certificado de la propia empresa, ni
después de que la AEAT haya aceptado un envío de la empresa hecho con su propio certificado. Si responde que el titular no
está autorizado (error 4112), o que no encuentra en su censo el NIF (4123) o el nombre (4124) del representante, los
registros quedan **bloqueados** con una explicación; corrija la causa en la AEAT (o elija otro certificado) y pulse
*Reintentar*. En el simulador local puede probar estos casos con los modos «Representante no apoderado», «NIF del
representante no censado» y «Nombre del representante no censado» (pantalla *Envíos a Hacienda* del menú «Más», con el modo avanzado y el *Modo práctica*) tras
asignar el certificado del tercero a ese entorno. El modo lo fija un **administrador de la empresa** (o el operador de la
plataforma) y es **solo de esa empresa**: lo consumen sus próximos envíos al simulador, nunca los de otras empresas ni la
AEAT. Los modos de representación solo cuentan los envíos que llevan representante; un envío con un certificado propio
no los gasta. Pasado el número de envíos indicado, el simulador vuelve a «Normal». La pantalla *Consulta a Hacienda* tiene
también sus modos de fallo para las consultas de la empresa.

## 8. Consulta a la AEAT

En **Consulta a Hacienda** (menú «Más», con el modo avanzado) elija el entorno, el ejercicio y el mes (y, si quiere, un número de factura) y pulse **Consultar**.
La aplicación pide a la AEAT (o al simulador local) los registros de la empresa en ese periodo, recorriendo todas las
páginas de la respuesta, y los compara con los de esta instalación:
- **Coinciden**: misma factura, misma huella y estado coherente.
- **Estado o huella distintos**: están en los dos lados con diferencias. El único caso que se puede corregir desde aquí es el
  seguro: un registro local *sin confirmar* (o pendiente o bloqueado), o *rechazado por duplicado (3000)*, que la
  AEAT ya tiene **con la misma huella**. El segundo caso aparece, por ejemplo, tras restaurar una copia sin el diario: la cola
  reenvía una factura que ya constaba y la AEAT la rechaza por duplicada. Un registro que se está *enviando* no se concilia:
  espere a que termine el envío y vuelva a consultar.
  Un administrador puede seleccionarlo y pulsar **Conciliar**: tras confirmar, la aplicación vuelve a consultar, comprueba de
  nuevo cada registro y lo marca como aceptado (sin reenviarlo); queda en el registro de actividad y en el diario. Si ya no
  queda nada pendiente de respuesta, se cierra también el aviso de incidencia de comunicación. Si mientras tanto empieza un
  envío de la cola, no se cambia nada y se le pide que lo intente de nuevo.
- **Solo en la AEAT**: la AEAT los tiene y aquí no constan (expedidos con otro sistema o perdidos en un desastre). Sus
  números ya están usados: no los vuelva a expedir. Si son de una serie de esta instalación cuyo contador aún no ha llegado
  a ellos, un administrador ve el botón **Avanzar el contador de la serie…**, que propone como siguiente número el mayor
  número de la serie que consta en la AEAT **en el mes consultado y en los siguientes del año hasta el actual** + 1 (en una
  serie sin año, también los meses de los años siguientes; la aplicación consulta esos meses y el diálogo dice cuáles ha
  comprobado; los meses anteriores no se miran). Si consultó un solo **número de factura**, no se propone nada: consulte el
  mes completo (ver el apartado de series). Tampoco se propone nada si una serie sin año obligaría a comprobar más de 24
  meses hasta el actual: consulte un mes más reciente.
- **Solo en local**: la AEAT no los tiene. Lo normal es que estén pendientes de envío (la cola los enviará) o rechazados.
- **Cadena de registros atrasada**: si la AEAT tiene registros de esta instalación generados **después** del último
  registro local (lo normal tras perder la base de datos y el diario y restaurar una copia antigua), la pantalla lo avisa con
  el último de ellos (número, fecha, hora de generación y huella) y, hasta resolverlo, **no se puede expedir, anular ni
  subsanar** en ese entorno: el siguiente registro debe encadenarse al último que tiene la AEAT, no al de la copia. Un
  administrador lo revisa y pulsa **Reanudar la cadena desde el último registro de la AEAT…**: la aplicación vuelve a
  consultar (todos los meses desde el último registro local y los de las facturas anteriores, por si se anuló o subsanó
  alguna) y, si el último registro sigue siendo el revisado, el siguiente registro se encadenará a él (queda en la
  actividad y en el diario, y se conserva aunque se vuelva a restaurar una copia). Avance también los contadores de las
  series afectadas. Si esos registros fueran de otro programa de facturación de la empresa, no reanude la cadena. Tras
  restaurar una copia, consulte **antes** de expedir: la cadena atrasada solo se detecta con la consulta. Si la pantalla
  avisa de que no puede saberse cuál es el último registro (varios generados en el mismo segundo) o de que la AEAT muestra
  una factura anulada con la huella de su alta sin que aquí conste la anulación, la cadena no se puede reanudar desde la
  consulta: no expida, anule ni subsane en ese entorno y aclárelo con el operador de la plataforma.

Si la respuesta no corresponde a lo consultado (otro obligado, otro ejercicio u otro mes), la pantalla lo avisa y no ofrece
conciliar nada: revise el certificado y los datos de la empresa.

Con el certificado de una gestoría o asesoría (representante), la consulta se hace igualmente **sin** el indicador de
representante, para recibir todos los registros de la empresa (también los que presentó la propia empresa u otra gestoría).
Si la AEAT la rechaza porque el titular no está apoderado para consultar (4140), se repite con el indicador y el resultado es
**parcial**: solo trae los registros que presentó esa gestoría. La pantalla lo avisa; lo que no aparece no se da por ausente y
no se proponen avances de contador ni reanudar la cadena. En el simulador local todos los textos, títulos y columnas dicen
«el simulador local» (por ejemplo, «Solo en el simulador local» o «Reanudar la cadena desde el último registro del simulador
local»), nunca «la AEAT».

La consulta no cambia nada por sí misma; lo que no es el caso seguro queda para su revisión. Solo los usuarios emisores
o administradores pueden consultar; el entorno de pruebas y la producción requieren lo mismo que la remisión (interruptor
del servidor, requisitos de la empresa y certificado vigente). Las peticiones y respuestas se guardan como evidencia.
