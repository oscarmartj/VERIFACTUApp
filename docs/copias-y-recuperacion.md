# Copias de seguridad y recuperación

## Qué se protege

| Elemento | Ubicación | Copia |
|---|---|---|
| Base de datos | `VERIFACTU_DATA_DIR/verifactu.sqlite3` | `crear_copia` (API de *backup* de SQLite, consistente en caliente) |
| Almacén (originales, PDF definitivos, peticiones/respuestas) | `VERIFACTU_DATA_DIR/almacen` | Incluido en la copia (`almacen.tar.gz`) |
| **Diario** de facturas, registros, miembros, estados, avances manuales del contador de series, estado de la cadena respecto a la AEAT (cadena atrasada o reanudada), cuentas y ajustes de la plataforma, usos de invitaciones y salidas (cada línea con su SHA-256) | `VERIFACTU_DIARIO_DIR/diario.jsonl` | Debe estar en **otro disco/volumen** y replicarse de forma continua |
| **Espejo** de binarios no regenerables (PDF definitivos y documentos originales) | `VERIFACTU_DIARIO_DIR/almacen/` | En el mismo volumen que el diario |
| Clave maestra (descifra certificados) | `VERIFACTU_CLAVE_MAESTRA_FICHERO` | **Aparte** y custodiada (gestor de secretos o soporte cifrado). Nunca junto a las copias de la base de datos |

## Hacer una copia

```bash
python -c "from app import db, copias; print(copias.crear_copia(db.conectar(crear=False)))"
```

Genera `copias/copia_AAAAMMDDTHHMMSS/` con `verifactu.sqlite3`, `almacen.tar.gz` y `manifiesto.json` (SHA-256 de cada fichero y estado de las cadenas). Programe la copia diaria y súbala a un almacenamiento externo con versionado.

**Permisos.** Las copias contienen los resúmenes scrypt de las contraseñas, los resúmenes de las sesiones e invitaciones,
los certificados cifrados y los PDF y documentos originales. Por eso la carpeta de copias y cada `copia_…` se crean `0700`
y sus ficheros `0600` (solo el usuario del servicio), como el almacén y el diario; una carpeta de destino que ya existía
(p. ej. un disco externo montado) no se modifica. Lo mismo vale para la carpeta de datos (si la crea la aplicación), la base
de datos y su WAL, la base del simulador local y las copias apartadas al restaurar (`verifactu.antes_de_restaurar_…`). Al
arrancar, la aplicación ajusta a `0700`/`0600` la carpeta de datos, sus bases de datos y las copias creadas por versiones
anteriores con permisos más abiertos (`copias.proteger_ficheros`; si no puede, p. ej. en un volumen de otro usuario, arranca
igualmente). Al copiar las copias a otro sitio, conserve esos permisos o cífrelas.

## Restaurar (aplicación detenida)

```bash
python -c "from pathlib import Path; from app import copias; print(copias.restaurar(Path('copias/copia_…')))"
```

0. **Espacio libre.** Antes de empezar calcula el espacio que necesita (`copias.espacio_necesario`) y, si no lo hay, se
   detiene sin cambiar nada (`EspacioInsuficiente`, con los MB necesarios y los libres). Estimación, con V = base actual
   (con su WAL), C = base de la copia, J = diario y A = ficheros del almacén que faltan (de la copia y del espejo del
   diario): la base temporal ocupa T = C + J; reproducir el diario y compactarla (`VACUUM`, que en modo WAL vuelve a
   escribir toda la base) llega a 2·T; apartar la base actual, T + V; y, tras colocar la restaurada, T + A. Se exige el
   máximo de esas cifras más 16 MB de holgura en el disco de la carpeta de datos (si el almacén está en otro disco, A en
   el suyo). En la práctica, unas 2–3 veces el tamaño de la base de datos más lo que falte del almacén. Para calcular A
   se lee una vez el `almacen.tar.gz` de la copia.
1. Verifica el manifiesto (si un fichero está dañado, se aborta sin tocar nada). Si la base de datos actual aún se puede leer, **primero la concilia con el diario** (nada confirmado en ella puede perderse) y, al final, comprueba que la base restaurada contiene todas sus facturas y registros; si no, se detiene sin cambiar nada.
   - Si el diario tiene líneas ilegibles, entradas cuyo contenido no cuadra con sus sumas o registros sin su factura, la restauración **se detiene** y exige revisión y `restaurar(copia, forzar=True)`; en ese caso los números e identificadores afectados quedan **reservados** (factura marcadora y contador de serie avanzado) para no reutilizarse.
2. Copia la base de datos de la copia a un **fichero temporal** y reproduce allí el **diario** (claves foráneas desactivadas durante la reproducción): reinserta las facturas y registros posteriores a la copia junto con su **contexto** (empresa, serie, instalación, documento, extracción y borrador anotados con cada factura), aplica los cambios de estado de las facturas (anulada, rectificada) y el último estado de comunicación de cada registro (incluida la marca de posible entrega), y actualiza contadores de serie (también los avances manuales del contador, `serie_contador`, que nunca lo hacen retroceder) y cabeza de cada cadena. Los usuarios referenciados que no existían en la copia se crean **inactivos y sin contraseña utilizable**. Un registro que estaba «enviando» pasa a «sin confirmar» con posible entrega: se reenviará el mismo XML y la AEAT responderá «duplicado» si ya lo tenía.
   La reproducción se hace con `secure_delete` y la base restaurada se compacta (`VACUUM`) antes de colocarla: lo que se
   borra o sustituye al reproducir (p. ej. el archivo cifrado de un certificado eliminado después de la copia) no queda en
   páginas libres del fichero. La copia de seguridad sí lo conserva (es la foto del momento en que se hizo).
   El estado de cada factura se vuelve a derivar de sus registros y rectificativas solo para las facturas que la
   reproducción toca (insertadas, con un cambio de estado o con registros nuevos o con otro estado de comunicación) y las
   que estas rectifican; el resto viene de la copia, que es coherente. Con los índices de la migración 0008
   (`registros(factura_id, secuencia)` y `facturas(rectifica_factura_id)`) el tiempo crece en línea con el tamaño: en una
   prueba local con facturas clonadas (+10 % posteriores a la copia), restaurar 1.100, 2.200 y 4.400 facturas tardaba 3,6,
   10,7 y 39 s (34 s en reproducir el diario) y ahora 1,7, 3,0 y 6,6 s (1,1 s); 8.800 facturas, 12,4 s.
   Si algo falla en este paso (también un disco lleno o una base de datos dañada), se borra la base temporal y no cambia
   nada.
3. **Solo si todo lo anterior termina bien**, guarda una copia consistente de la base actual (`verifactu.antes_de_restaurar_<marca>.sqlite3`, incluido su WAL) y coloca la restaurada con un único `os.replace` atómico: en ningún instante falta la base de datos. Si no puede apartarla (p. ej. el disco se llena), se detiene **sin cambiar nada** y borra la base temporal y la copia apartada a medias. Un disco lleno no se confunde con una base dañada.
4. **Fusiona** en el almacén los ficheros que falten de la copia y del espejo del diario (nunca borra ni sobrescribe). Los de la copia se leen del `almacen.tar.gz` **uno a uno**, sin descomprimirlo entero en la carpeta de datos (el almacén no se necesita dos veces en disco), y cada fichero se escribe en un temporal que solo toma su nombre definitivo cuando está completo.
5. **Repara los PDF**: cada PDF definitivo se contrasta con su SHA-256 registrado; si falta o está dañado se toma de una copia válida (espejo o copia de seguridad) o se regenera desde la versión inmutable, y solo se acepta si la suma coincide. El resultado informa de `pdf_reparados`, `pdf_regenerados` y `pdf_irrecuperables`.
   Si los pasos 4 o 5 fallan (p. ej. disco lleno), la base de datos **ya está restaurada** y no se deshace: la restauración
   termina con un error explícito (`RestauracionIncompleta`) que lo dice, indica dónde está la base anterior y pide
   **repetir la restauración con la misma copia** cuando se haya corregido la causa. Repetirla es seguro: la base
   restaurada se concilia, se aparta y se vuelve a restaurar con la copia y el diario, y se completan el almacén y los PDF.
   Hasta entonces pueden faltar documentos o PDF (la aplicación arranca, pero no los encontrará).
6. Las entradas del diario cuya suma o cuyo contenido no cuadra (XML frente a `xml_sha256`, cadena frente a huella, datos frente a `datos_sha256`) **no se reproducen** y se informan (`entradas_descartadas`, `lineas_diario_ilegibles`).

Verificado en `tests/test_03_flujo.py::test_caso12_copia_restauracion` y `tests/test_05_revision.py::test_h1_restauracion_con_datos_posteriores_a_la_copia` (documento, usuario, factura y anulación posteriores a la copia): tras restaurar no se reenvía nada ya aceptado, la factura anulada sigue anulada y la siguiente factura toma el número siguiente y encadena con el último registro real.

## Reconciliación y arranque seguro

- Al arrancar, `copias.reconciliar_diario` añade al diario las facturas, registros y **estados** (de la factura y de la comunicación) que falten o difieran de la última anotación (p. ej. caída entre la confirmación en base de datos y la anotación). También vuelve a anotar, una sola vez y con la instantánea de la serie, cada **avance manual del contador** que el diario ya no tiene (contador por delante del último avance anotado y del mayor número expedido + 1), y el **estado de la cadena** respecto a la AEAT (`cadena_aeat`) que difiera de su última anotación: si el diario se perdió o se sustituyó y se rehízo desde la base, una restauración posterior de una copia anterior no deshace el avance (`tests/test_18_g2_cadena.py::test_res3_el_arranque_vuelve_a_anotar_los_avances_del_contador`). La copia espejo de los PDF y originales no se rehace. Antes de cada envío se anota también la marca «enviando», de modo que una pérdida de la base durante un envío se restaura como «sin confirmar / posible entrega».
- Las líneas ilegibles del diario (p. ej. la última, cortada por una caída) se omiten y se registran en la actividad, sin impedir el arranque ni la restauración.
- `copias.comprobar_arranque` **impide arrancar** si falta la base de datos, si está vacía, dañada o **atrasada** respecto al diario (facturas o registros anotados que no contiene): usar una base así reiniciaría la numeración o bifurcaría la cadena. El remedio es `copias.restaurar`. La herramienta de copia abre la base con `crear=False` para no crear nunca una vacía.
- Los miembros de cada empresa se anotan con una **versión monótona**: al restaurar se aplica la instantánea de mayor versión y nunca una anterior a la copia (no se recuperan permisos ya retirados: una persona quitada de una empresa o degradada después de la copia sigue fuera o degradada).
- Se reproduce solo el **último** estado de comunicación anotado de cada registro (los indicadores de posible entrega e incidencia no se acumulan artificialmente).
- **Cuentas y ajustes de la plataforma** (plataforma multiusuario): cada cambio de una cuenta (alta, contraseña, contraseña temporal, activación o desactivación) o de un ajuste (`registro_abierto`, `invitaciones_empresa_crean_cuentas`) incrementa su versión y se anota en el diario (`usuario`, con el resumen scrypt de la contraseña —nunca la contraseña—, y `ajuste_plataforma`); al arrancar se anotan las versiones que falten. Al restaurar se aplica la última versión de cada uno si es posterior a la copia: una cuenta creada después vuelve con su contraseña, una contraseña cambiada o una cuenta desactivada no vuelven a su estado anterior y un registro cerrado no se reabre. La obligación de elegir una contraseña nueva que la actualización desde la versión anterior pone a las cuentas existentes (`alta_origen = version_anterior`) también incrementa su versión y se anota al arrancar: una restauración no la quita (solo el cambio de contraseña de la propia persona). Las sesiones de esas cuentas se cierran. Una entrada del diario nunca concede el privilegio de la plataforma. El diario contiene así los resúmenes de las contraseñas: protéjalo como las copias de la base de datos.
- **Invitaciones**: intencionadamente **no** se restauran. Al restaurar se anulan todas las pendientes de la copia (una invitación usada después de la copia figura en ella como pendiente y no debe volver a valer); las personas que aún no la hubieran usado necesitan una nueva. El **uso** de cada invitación sí se anota en el diario (`invitacion_usada`, sin el token ni su resumen; al arrancar se anotan los que falten), de modo que una invitación usada después de la copia figura como **usada** (por quién y cuándo) y no como anulada por la restauración (`invitaciones_usadas` en el resultado).
- **Sesiones**: no se anotan en el diario, y las de la copia pueden incluir sesiones cerradas después (salida, contraseña cambiada, cuenta desactivada). Una sesión cerrada después de la copia **no vuelve a valer** al restaurar (`sesiones_cerradas` en el resultado): las contraseñas, las contraseñas temporales y las bajas cierran las sesiones de esas cuentas (versión de la cuenta); cada **salida** se anota en el diario (`sesion_cerrada`, solo el resumen del token, que ya no vale, y el usuario); y, si la base que se sustituye se puede leer y no está atrasada respecto al diario, solo siguen abiertas las sesiones que también lo están en ella (mismo identificador, resumen del token y usuario). Las sesiones abiertas después de la copia no existen en ella: esas personas vuelven a iniciar sesión.
- **«Mis certificados», asignaciones y legitimaciones** se anotan en el diario (`certificados_fila`) con una versión monótona por fila y **sin el archivo ni la contraseña** (ni siquiera cifrados; solo si la fila tiene archivo). Al restaurar se aplica la versión más reciente de cada fila: una baja, retirada o revocación posterior a la copia **no se deshace**; una asignación posterior prevalece sobre el certificado de la empresa activo en la copia; un certificado cargado después de la copia vuelve **sin archivo** («debe volver a cargarse»: al subir el mismo archivo conserva sus asignaciones). Los anteriores a la copia se descifran con la clave maestra, que sigue fuera de la base y de las copias.
- **Certificado de la empresa en uso.** De los certificados cargados por empresa se anota su estado «en uso» (`activo`, con versión; nunca el archivo): activar, sustituir o dejar sin uso un certificado de la empresa después de la copia no se deshace al restaurar. Un certificado cargado por empresa **después** de la copia no puede recrearse (la fila exige el archivo cifrado): si era el que estaba en uso, el resultado lo lista en `certificados.certificados_empresa_por_recargar` y la empresa queda sin certificado en ese entorno hasta volver a cargarlo o reactivar expresamente otro (nunca se vuelve a usar en silencio el que sustituyó).
- **Evidencia de las legitimaciones.** Cada anotación de una legitimación lleva la fila de su documento de evidencia (el PDF ya tiene su copia espejo junto al diario), así que una legitimación posterior a la copia vuelve con su documento (`certificados.documentos_evidencia_recuperados`). Si el documento no puede recuperarse (p. ej., anotaciones antiguas), la legitimación pierde la referencia y deja de contar (`certificados.legitimaciones_sin_evidencia`, actividad `legitimacion.evidencia_perdida`): debe volver a declararse. Así ningún identificador de documento queda colgando y no puede acabar apuntando al documento de otra empresa.
- Tras restaurar, los datos de configuración posteriores a la copia que no acompañan a ninguna factura (archivos de certificados cargados por empresa, autorizaciones de producción, series confirmadas sin uso) deben volver a introducirse.
- **Verificación del NIF de las empresas** (panel «Plataforma»): cada verificación o retirada se anota en el diario (`verificacion_nif`) con una versión monótona, y al restaurar se aplica la más reciente: una retirada posterior a la copia **no se deshace** (la empresa sigue sin poder enviar a la AEAT con el certificado de un tercero) y una verificación posterior se conserva. El resultado lista las empresas cuyo estado cambia respecto a la copia (`verificaciones_nif_cambiadas`: empresa, NIF y si queda verificada) y cada una queda en la actividad de la plataforma y en la de la empresa (`empresa.nif_verificacion_restaurada`).

## Consultar a la AEAT después de restaurar

Tras una restauración, use **Consulta AEAT** (`POST /api/empresas/{eid}/consulta`) para cada mes afectado (desde la fecha de
la copia) y cada entorno con envíos reales, **antes de expedir**. Si el diario estaba vacío (se perdió con la base de datos), el
resultado de `copias.restaurar` lo recuerda en `avisos`: lo expedido después de la copia no se ha recuperado y la consulta es
la única forma de saber qué números y qué registros de la cadena ya constan en la AEAT
(`tests/test_18_g2_cadena.py::test_fis1_la_cadena_continua_desde_la_aeat_tras_un_desastre`). El aviso también queda en la
actividad de la plataforma y en la de **cada empresa** de la base restaurada (`copia.restaurada_sin_diario`), también la de
las que en la copia aún no tenían registros, que pueden ser justo las que expidieron después. Las empresas pendientes se
anotan en el diario (`restauracion_sin_diario`) antes de colocar la base, así que el aviso se mantiene si la restauración
termina incompleta (`RestauracionIncompleta` lo incluye en su mensaje) y se repite, o si se vuelve a restaurar (aunque se
haya vuelto a perder la base de datos y la conciliación haya rellenado el diario con la base restaurada) mientras alguna
de ellas no ha consultado ni generado un registro nuevo desde entonces. Cada empresa deja de estar pendiente cuando ha
consultado, de forma completa y fiable (un mes entero, sin «Número de factura», ni parcial, ni ajena, ni fuera de esquema),
cada mes desde el de la copia hasta el de la restauración en cada entorno en el que tenía registros (cada una de esas
consultas se anota en el diario: `consulta_aeat`), o con su primer registro nuevo (`diario.pendientes_sin_diario`; la
consulta solo lee el diario desde la última de esas anotaciones; `tests/test_18_integracion.py`). Una empresa dada de alta
después de aquella restauración no hereda el aviso: todo lo suyo está en el diario:
- **Estado o huella distintos — conciliable**: registros que la AEAT ya tiene **con la misma huella** y que en local figuran:
  - «sin confirmar» (o pendientes, enviando o bloqueados): un administrador puede conciliarlos y se marcan como aceptados sin
    reenviarlos. Si no se concilian, la cola los reenvía: para los «sin confirmar» (posible entrega) la respuesta «duplicado»
    (3000) de la AEAT los confirma igualmente.
  - «Rechazado» por duplicado (3000): es lo que ocurre con un registro recuperado como **«pendiente»** de una copia sin el
    diario que ya se había enviado después de la copia. La cola arranca con la aplicación y normalmente lo reenvía antes de que
    se pueda consultar; como no consta un envío previo sin respuesta de ese registro, el «duplicado» no lo confirma (la
    respuesta de duplicado no trae la huella) y queda rechazado «con revisión». La consulta sí devuelve la huella: si la AEAT
    tiene ese mismo registro, un administrador puede conciliarlo y queda aceptado
    (`tests/test_16_s_series_consulta.py::test_rechazo_3000_tras_restaurar_sin_diario_se_concilia`). No hace falta detener
    la cola antes de consultar.

  El cambio se anota en el diario (`estado_registro`), de modo que también sobrevive a una restauración posterior
  (`tests/test_16_s_series_consulta.py::test_conciliacion_sobrevive_a_una_restauracion`). Si tras conciliar no queda ningún
  registro sin respuesta definitiva, se cierra la incidencia de comunicación, como tras un envío con respuesta; la
  restauración hace lo mismo al terminar (la incidencia que tuviera la copia no se arrastra), de modo que las facturas nuevas
  no se remiten con `Incidencia=S` sin haber incidencia
  (`tests/test_16_s_series_consulta.py::test_conciliar_el_ultimo_pendiente_cierra_la_incidencia`).
- **Solo en la AEAT**: registros que la AEAT tiene y la instalación no (perdidos junto con el diario o expedidos con otro
  sistema). La consulta muestra número, fecha, importes, huella y estado, pero no reconstruye la factura ni su PDF. Sus números
  ya están usados: se avisa si pertenecen a una serie local cuyo contador no ha llegado a ellos; no los expida de nuevo. Un
  administrador puede **avanzar el contador** de esa serie más allá del mayor número usado (botón en la propia fila o en la
  configuración de series): revisa antes los números que se saltan, el contador nunca retrocede y el avance se anota en la
  actividad y en el diario (`serie_contador`), de modo que una restauración posterior lo conserva
  (`tests/test_17_f2_hallazgos.py::test_sf7_avanzar_el_contador_tras_un_desastre`). La propuesta es el mayor número de la serie
  en el mes consultado **y en los siguientes del año hasta el actual** (en una serie sin año, también los de los años
  siguientes; la aplicación los consulta y dice cuáles) + 1; con una consulta de un solo número de factura no se propone nada.
- **Cadena atrasada**: si entre los registros «solo en la AEAT» (o con otra huella) hay registros de **esta instalación**
  posteriores al último registro local (encadenados a él, o generados en su mismo segundo o después), la cadena local se ha
  quedado atrás: el siguiente registro no puede encadenarse al de la copia (la cadena de la AEAT se bifurcaría). La consulta lo
  avisa con el último de ellos (el que ningún otro tiene como `RegistroAnterior`, aunque se generen en el mismo segundo;
  `tests/test_18_g2_cadena.py::test_g2v1_reanudar_sigue_el_encadenamiento_con_registros_del_mismo_segundo`) (número, fecha,
  `FechaHoraHusoGenRegistro` y huella, tal como los devuelve la AEAT), lo guarda y lo anota en el diario, y **bloquea** expedir,
  anular y subsanar en ese entorno. Un administrador lo revisa y usa **Reanudar la cadena desde el último registro de la AEAT**:
  la aplicación vuelve a consultar en el servidor todos los meses desde el del último registro local hasta el actual y los de
  las facturas locales (una anulación o subsanación posterior de una factura antigua es también un registro de la cadena, con
  la fecha de esa factura;
  `tests/test_18_g2_cadena.py::test_fis1_reanudar_consulta_tambien_los_meses_de_facturas_anteriores`), comprueba que el
  último registro sigue siendo el revisado (huella) y guarda un **ancla**; el siguiente registro la usa como
  `RegistroAnterior` (queda en `registros.ancla_aeat`, que la verificación de la cadena acepta) y el ancla desaparece. Se
  audita (`cadena.reanudada_aeat`), se anota en el diario (`cadena_aeat`) y es idempotente; una restauración posterior vuelve a
  aplicar el ancla si aún no se ha usado y nunca si ya se usó
  (`tests/test_18_g2_cadena.py::test_fis1_la_cadena_continua_desde_la_aeat_tras_un_desastre`, con una cadena real expedida y
  enviada al simulador, perdidos la base y el diario y restaurada una copia antigua). Procedimiento: consulte cada mes desde
  la copia, avance los contadores, reanude la cadena y solo entonces vuelva a expedir. La cadena atrasada solo se detecta con
  la consulta: si se expide antes de consultar, ese registro se encadena al último de la copia.
- **Solo en local** con estado aceptado o **huella distinta**: requieren revisión manual.

## Riesgo residual

Si se pierden **a la vez** la base de datos y el diario, los registros posteriores a la última copia no pueden reconstruirse
automáticamente: la consulta a la AEAT (apartado anterior) permite **identificarlos** (número, fecha, importes, huella y
estado), avanzar los contadores y **reanudar la cadena** desde el último registro de la AEAT, pero la factura completa y su
PDF no se recuperan. La detección de la cadena atrasada depende de que la AEAT devuelva `SistemaInformatico`,
`FechaHoraHusoGenRegistro` y `Encadenamiento` de cada registro y, para una factura anulada, la huella y la fecha y hora de su
**anulación** (pendiente de contrastar en el entorno de pruebas, `docs/pendientes-produccion.md` punto 5): si devolviera las
del alta, una anulación perdida no se vería, la consulta lo avisa (`anulacion_sin_huella`) y la cadena no se reanuda. Tampoco
se reanuda si la respuesta no permite saber cuál es el último registro (varios generados en el mismo segundo tras huecos de la
consulta, aviso `cadena_ambigua`). En esos casos la expedición en ese entorno sigue bloqueada (si la cadena está atrasada) o
debe detenerse, y hay que aclararlo con el operador de la plataforma. Por eso el diario debe estar en un soporte distinto.
