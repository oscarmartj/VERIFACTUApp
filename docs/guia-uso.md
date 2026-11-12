# Guía de uso

**Recorrido:** subir PDF → revisar datos → confirmar la emisión → descargar el PDF definitivo y consultar su estado.

## Cuentas, empresas e invitaciones

La aplicación es **multiusuario**: cada persona tiene su cuenta y solo ve las empresas de las que es miembro.

- **Crear una cuenta**: con el registro abierto, desde «Cree una cuenta» en la pantalla de inicio de sesión; con el
  registro cerrado (lo habitual), solo desde un **enlace de invitación**. Cada uno elige su propia contraseña.
- **Dar de alta una empresa**: «Nueva empresa» (arriba a la derecha). Quien la da de alta queda como su administrador.
  Si el NIF ya está dado de alta, la aplicación no la duplica: pida a uno de sus administradores que le invite.
- **Invitar a otra persona** (administradores): Configuración → **Usuarios y empresas** → correo, rol (administrador,
  emisor o lector) y relación → «Invitar». Se muestra **una sola vez** un enlace de un solo uso que caduca a los 7 días:
  envíelo por un canal de confianza. Si la persona ya tiene cuenta, entra con ella desde el enlace y pulsa «Aceptar la
  invitación»; si ya es miembro, solo se cambian su rol y su relación. Si quien la acepta no tenía ninguna empresa y
  tiene la aplicación abierta en otra ventana, allí la empresa queda elegida en cuanto abre otra pantalla (se le avisa
  «Ya tiene acceso a …»). Las invitaciones pendientes se pueden anular.
  Con el registro cerrado, su invitación solo la puede aceptar quien ya tenga cuenta (si no, necesita además una
  invitación del operador de la plataforma), salvo que el operador lo haya permitido.
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
  cerrado, decidir si las invitaciones de los administradores de empresas pueden crear cuentas nuevas (por defecto no).
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

## 0. Antes de empezar (una vez, rol administrador)

1. **Configuración → Empresa**: razón social, domicilio y ámbito: tipo de contribuyente, territorio, si lleva el SII, si opera en Canarias/Ceuta/Melilla, otras exclusiones y **cómo se generan los PDF** (plantilla o programa). Pulse **Confirmar ámbito**: la aplicación solo emite en los supuestos implementados y explica por qué cuando no.
2. **Declaración de autorización**: indique su relación con la empresa y declare que está autorizado a actuar por ella.
3. **Series**: cree la serie del entorno en el que va a trabajar con su patrón (p. ej. `{n}/{aa}` para «166/26») y el **siguiente número** que toca. Pruébela con un número y **confírmela**. Las rectificativas necesitan una serie propia (p. ej. `R{n}/{aa}`). Si sus números llevan **ceros de relleno** («042/A26», «A057/26»), use `{n:3}` (o `{nnn}`) para el ancho: `{n:3}/A{aa}` → 042/A26, `A{n:3}/{aa}` → A057/26; al superar el ancho se escriben más cifras (1000/A26). Con ese patrón «42/A26» no corresponde a la serie (la aplicación le indica cómo escribirlo). Si un borrador trae un número que no encaja con ninguna serie, se le **propone el patrón** y un enlace para crearla (el formulario «Probar un número» llega relleno con ese patrón y ese número). Si hay números de una serie ya usados fuera de esta instalación (por ejemplo, tras perder la base de datos y el diario, la consulta a la AEAT muestra 004 a 009 y aquí el contador va por el 004), un administrador puede pulsar **Avanzar contador…** en la serie: indica el nuevo siguiente número, revisa la lista de números que se saltan y lo confirma. El contador nunca retrocede ni puede quedar por debajo de un número expedido aquí; el cambio queda en el registro de actividad y en el diario, y no se puede deshacer. En la tabla, **Siguiente nº** es el número que se expedirá a continuación (el contador de este año, escrito con el patrón) y **Número inicial** el que se indicó al crear la serie: en las series con año es el primer número del año de confirmación (los demás años empiezan en 1).
4. **Certificados** (solo para Pruebas AEAT y PRODUCCIÓN; el simulador no los necesita): ver el apartado 7.
5. El **entorno activo** se ve siempre arriba: *Simulador local* (pruebas técnicas sin red), *Pruebas AEAT* o **PRODUCCIÓN** (en rojo). Los dos últimos están desactivados hasta completar **Configuración → Paso a producción**.
6. El paso **Productor del sistema** no es una tarea de su empresa: lo identifica y confirma el operador de la plataforma (afecta a todas las empresas). Mientras no lo haga, la producción sigue bloqueada.

Las fechas y horas se muestran siempre en hora peninsular (también el año y el mes que la aplicación le propone, aunque su
equipo esté en otra zona horaria). En **Actividad** cada evento lleva su código (p. ej. `factura.expedida`) y el detalle en
palabras. Las confirmaciones de acciones que no se pueden deshacer (quitar a una persona, salir de una empresa, eliminar un certificado…) se abren con «Cancelar» seleccionado: para confirmarlas, pulse el botón rojo.

## 1. Importar

Arrastre uno o varios PDF (máx. 15 MB y 30 páginas cada uno). Si un archivo ya se había importado, se indica y no se duplica. Si otro PDF tiene el **mismo número de factura**, se avisa.

Se admite **cualquier diseño** de factura (no hace falta configurar plantillas): con texto nativo o escaneada (se aplica
OCR local). Recomendación: suba el PDF **antes de enviarlo al cliente**. Una factura ya enviada (o firmada) se archiva,
no se convierte: el QR solo puede añadirse al expedirla. En los escaneos, revise especialmente los campos en ámbar.

## 2. Clasificar

Cada documento muestra una propuesta con sus motivos. Confirme si es:
- **Preparatorio** (borrador/proforma que va a expedir con la aplicación),
- **Factura propia ya emitida** (solo se archiva; no se le añade QR ni se comunica),
- **Factura recibida** (solo se conserva),
- **Desconocido**.

Si el original está firmado digitalmente, se conserva intacto y se le avisará de que el PDF definitivo no conservará esa firma.

## 3. Revisar el borrador

A la izquierda, el original con recuadros sobre los datos leídos (verde: fiable; ámbar: dudoso; sin recuadro: no encontrado). A la derecha, el formulario con la procedencia de cada dato (página, método y confianza).
- **Bloqueos** (rojo): impiden emitir (p. ej. el total no cuadra, falta el NIF, caso fiscal no soportado).
- **Por confirmar**: marque cada casilla cuando lo haya comprobado (p. ej. lectura OCR dudosa, la factura se expedirá con la fecha de hoy).
- **Avisos**: informativos.
Cada corrección pide un motivo y queda en el historial.

### Operaciones exentas de IVA

En **Desglose de IVA**, cada fila es **sujeta a IVA** (tipo, base y cuota) o **exenta** (solo la base). Para una fila
exenta, elija en la columna *Calificación* la causa:

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
- La aplicación no puede comprobar que una operación esté exenta: **la causa siempre se confirma** (casilla en *Revisión*).
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

**Vista previa** muestra la factura con la marca «BORRADOR – SIN VALOR FISCAL» y el hueco del QR. **Emitir** abre una confirmación con el entorno, la empresa, el número, el cliente y el total. Al confirmar se expide la factura, se genera el registro y se envía en cuanto el control de flujo lo permite. Un doble clic no duplica nada.

## 5. Descargar y consultar el estado

En **Facturas** puede buscar por número, cliente, fechas, estado y entorno, y descargar en PDF una a una o por lotes (ZIP con PDF y XML). Cada factura muestra dos estados distintos:
- **Estado de la factura**: emitida, rectificada, anulada.
- **Estado de la comunicación**: envío pendiente, resultado sin confirmar, registro aceptado, aceptado con errores, rechazado o bloqueado, con el código y el mensaje oficiales y el CSV.

> Que la AEAT acepte el registro no significa que haya validado el tratamiento fiscal de la operación.

Si hay una incidencia de comunicación, se muestra arriba, en todas las pantallas, un aviso con la fecha y hora en que empezó y los registros pendientes de remitir o de confirmar; puede seguir facturando y la aplicación reintenta sola (al menos cada hora). El aviso se actualiza al conciliar, reintentar o subsanar y cada vez que se recarga la pantalla **Remisión** (cada 15 s o con «Actualizar»), y desaparece cuando ya no queda nada pendiente.

## 6. Corregir lo ya expedido

Desde la ficha de la factura: **Subsanar registro** (errores del registro), **Rectificativa** (errores de la factura, R1/R4 por diferencias) o **Anular registro** (factura que no debió expedirse). Ninguna operación edita ni borra lo expedido. Ver `docs/tratamiento-documentos.md`.

## 7. Certificados electrónicos: propios o de representante

El certificado sirve para identificarse ante la AEAT al remitir (pruebas y producción). Se carga siempre en la propia
aplicación, con su contraseña, en un formulario seguro: se guarda cifrado y **nunca** vuelve a mostrarse ni se puede
descargar. Hay dos maneras de cargarlo:

- **Para una empresa** (Configuración → Certificados → *Cargar certificado*): queda ligado a esa empresa y entorno.
- **En «Mis certificados»** (menú superior): lo carga **una sola vez** y lo usa en todas las empresas que **administra**
  (*Asignar a una empresa*, o en Configuración → Certificados → *Usar uno de «Mis certificados»*). Es útil para una
  gestoría o asesor que lleva varias empresas. Solo usted ve, asigna, retira o elimina sus certificados; los demás
  administradores de la empresa ven cuál se usa, pueden sustituirlo por otro, pero no descargarlo ni llevarlo a otra
  empresa. Si deja de ser administrador de una empresa, su certificado deja de usarse en ella. *Ver usos* muestra cada
  asignación, cada retirada y cada envío o consulta a la AEAT hecho con él.
- Cualquier **administrador de la empresa** puede *Dejar de usarlo en esta empresa* (tarjeta *Certificado que se
  usará*), aunque el certificado sea de otro usuario, p. ej. de alguien que ya no administra la empresa. Solo retira
  esa asignación; el propietario lo ve en *Ver usos*. En el *Simulador local*, una asignación que ya no puede usarse
  no bloquea los envíos: se hacen sin certificado, como antes de asignarlo.

Cada empresa usa **un único certificado por entorno**: la tarjeta *Certificado que se usará* indica cuál, de dónde
viene, su titular, su vigencia y en calidad de qué actúa:

- **Propio**: el certificado es de la empresa (sello o persona jurídica con su NIF), de su representante legal o, si es
  un autónomo, del propio titular. Los envíos no llevan representante.
- **De representante**: el certificado es de un tercero (p. ej. el sello de la gestoría). Solo se puede usar si la
  empresa declara en *Legitimación de un tercero* el **apoderamiento** inscrito en la AEAT o el acuerdo de
  **colaboración social** de ese titular: NIF y nombre del titular (se proponen los del certificado en uso) y el
  documento que lo acredita (impórtelo antes en «Documentos» y clasifíquelo como «Desconocido / no procesar»).
  Entonces cada envío lleva `Cabecera/Representante` con ese NIF y el nombre que figura **en el certificado** (la
  AEAT lo contrasta con su censo); si el nombre que escribió es distinto, la aplicación lo avisa. La legitimación puede
  revocarse. Una
  legitimación cuyo documento de evidencia ya no existe (p. ej., perdido al restaurar una copia) no cuenta: vuelva a
  declararla con el documento.

**Configuración → Paso a producción** muestra qué certificado se usará y, si es de un tercero, el requisito de su legitimación. La
aplicación no puede comprobar el apoderamiento: lo comprueba **la AEAT en cada envío**. Por eso, el **primer envío** a la
AEAT (pruebas o producción) de una empresa que dio de alta un usuario, hecho con el certificado de un tercero, exige
además el requisito «NIF de la empresa acreditado»: pida al operador de la plataforma que verifique el NIF aportando la
documentación que acredite que actúa por la empresa. No hace falta si se usa un certificado de la propia empresa, ni
después de que la AEAT haya aceptado un envío de la empresa hecho con su propio certificado. Si responde que el titular no
está autorizado (error 4112), o que no encuentra en su censo el NIF (4123) o el nombre (4124) del representante, los
registros quedan **bloqueados** con una explicación; corrija la causa en la AEAT (o elija otro certificado) y pulse
*Reintentar*. En el simulador local puede probar estos casos con los modos «Representante no apoderado», «NIF del
representante no censado» y «Nombre del representante no censado» (pantalla Remisión, entorno *Simulador local*) tras
asignar el certificado del tercero a ese entorno. El modo lo fija un **administrador de la empresa** (o el operador de la
plataforma) y es **solo de esa empresa**: lo consumen sus próximos envíos al simulador, nunca los de otras empresas ni la
AEAT. Los modos de representación solo cuentan los envíos que llevan representante; un envío con un certificado propio
no los gasta. Pasado el número de envíos indicado, el simulador vuelve a «Normal». La pantalla *Consulta AEAT* tiene
también sus modos de fallo para las consultas de la empresa.

## 8. Consulta a la AEAT

En **Consulta AEAT** elija el entorno, el ejercicio y el mes (y, si quiere, un número de factura) y pulse **Consultar**.
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
