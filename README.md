# FacturaPDF VF — importar PDF y expedir facturas VERI*FACTU

Aplicación web en español para: **subir un PDF → revisar los datos extraídos → confirmar la emisión → descargar la factura definitiva en PDF (con QR tributario) y consultar su estado en la AEAT.**

Funciona con **cualquier diseño de factura** (sin plantillas por emisor; PDF con texto o escaneado, con OCR local): la
extracción se mide con un corpus sintético de diseños variados y conjuntos reservados a ciegas (`docs/pruebas.md`). Lo
que se procesa son los documentos **preparatorios** (el PDF se sube antes de entregarlo al cliente); una factura ya
emitida se archiva, no se «convierte»: el QR y el registro solo se generan al expedir
(`docs/tratamiento-documentos.md`).

> **Estado (versión 0.1.0, 09/11/2026):** recorrido completo implementado y probado contra un **simulador local** de la AEAT.
> **No** se ha probado todavía contra los servicios reales de la AEAT (ni pruebas ni producción) y la producción está
> bloqueada por diseño hasta completar `docs/pendientes-produccion.md`.

## Operaciones exentas

Las facturas pueden llevar bases **exentas de IVA** (causas E1 art. 20, E2 art. 21, E3 art. 22, E4 arts. 23 y 24 y E6
otras), solas o junto a bases sujetas: en el borrador, cada fila del desglose es *sujeta* (tipo y cuota) o *exenta* (causa y
mención legal, que se imprime en la factura). Una factura sin IVA (p. ej. un alquiler de vivienda) se propone como base
exenta para que el usuario elija la causa; si el documento cita el artículo de la Ley del IVA, la causa se propone a partir
de él (una mención sin artículo o un «exento» de otro contexto no proponen ninguna). La causa se confirma siempre. No sujetas, inversión del sujeto pasivo, recargo de equivalencia y entregas intracomunitarias (E5) siguen
bloqueadas. Detalle en `docs/arquitectura.md` («Operaciones exentas») y `docs/guia-uso.md`.

## Puesta en marcha

Requisitos del sistema: Python 3.13, `poppler-utils`, `tesseract-ocr` + `tesseract-ocr-spa`, `qpdf`, `zbar-tools`.

```bash
pip install -r requirements.txt
```

```bash
python scripts/generar_pdfs_prueba.py
```

```bash
VERIFACTU_DATA_DIR=./data-demo python scripts/sembrar_demo.py
```

```bash
VERIFACTU_DATA_DIR=./data-demo uvicorn app.main:app --port 8000
```

Abra `http://localhost:8000` (usuario de demostración `demo@ejemplo.test` / `demo-clave-segura`; datos sintéticos).
Con Docker: `docker compose up --build` (ver `docker-compose.yml`).

### Variables de entorno

| Variable | Por defecto | Uso |
|---|---|---|
| `VERIFACTU_DATA_DIR` | `./data` | Base de datos y almacén privado |
| `VERIFACTU_DIARIO_DIR` | `../diario` | Diario de recuperación (en otro disco) |
| `VERIFACTU_CLAVE_MAESTRA_FICHERO` | `../secretos/clave_maestra.bin` | Clave AES-256 para certificados (se crea con permisos 0600) |
| `VERIFACTU_PERMITIR_PRUEBAS_AEAT` | `0` | Interruptor del entorno de pruebas de la AEAT |
| `VERIFACTU_PERMITIR_PRODUCCION` | `0` | Interruptor de producción |
| `VERIFACTU_COOKIE_SEGURA` | `0` | `1` detrás de HTTPS |
| `VERIFACTU_MAX_MB_PDF` / `VERIFACTU_MAX_PAGINAS` / `VERIFACTU_MAX_PAGINAS_OCR` | 15 / 30 / 10 | Límites de procesamiento |
| `VERIFACTU_TRABAJADOR` | `1` | Hilo de remisión |
| `VERIFACTU_REGISTRO_MAX_IP` / `VERIFACTU_REGISTRO_MAX_GLOBAL` | 5 / 30 | Intentos de registro sin invitación por dirección IP y hora / cuentas nuevas por hora en toda la plataforma (y 10 veces esa cifra de intentos sin invitación) |
| `VERIFACTU_REGISTRO_MAX_IP_INVITACION` / `VERIFACTU_REGISTRO_MAX_POR_INVITADOR` / `VERIFACTU_ALTAS_MAX_POR_INVITADOR` | 50 / 50 / 10 | Con una invitación vigente, el registro no gasta los límites anteriores sino estos (por hora): intentos por dirección IP (techo más alto: una oficina se da de alta desde la misma IP), intentos con las invitaciones de un mismo usuario o empresa, y cuentas creadas con sus invitaciones. Además, 10 intentos por invitación, y las cuentas creadas con invitaciones de usuarios (no del operador) desde una misma IP o de un mismo usuario nunca pasan de la mitad del límite global |
| `VERIFACTU_INVITACIONES_PENDIENTES_MAX` / `VERIFACTU_INVITACIONES_DIA_MAX` / `VERIFACTU_EMPRESAS_DIA_MAX` | 50 / 100 / 20 | Cupos de los usuarios (no del operador): invitaciones pendientes por empresa, invitaciones por persona y día, altas de empresas por persona y día |

## Usuarios, empresas y papeles (plataforma multiusuario)

La aplicación la pueden usar muchas personas a la vez, cada una con sus empresas y sin ver las de las demás:

| Papel | Quién | Qué puede hacer |
|---|---|---|
| **Operador de la plataforma** | Quien instala la aplicación (privilegio `superadmin`). Es el **productor del sistema**: suscribe la declaración responsable y se identifica en cada registro (bloque `SistemaInformatico`) | Panel «Plataforma»: abrir o cerrar el registro de cuentas, invitaciones de la plataforma, cuentas (activar, desactivar, contraseña temporal), datos del productor y modo de fallo del simulador de cualquier empresa, indicando su NIF (los administradores de cada empresa lo fijan para la suya y el panel no los lista; nunca afecta a otra). Por ser operador **no** ve documentos ni facturas de ninguna empresa (solo las de las que sea miembro). Ojo: una **contraseña temporal** le permitiría entrar como esa persona; por eso se cierra su sesión, queda anotada en la actividad de cada una de sus empresas y se le muestra en «Mi cuenta» |
| **Usuario** | Cualquier persona con cuenta (registro abierto o por invitación) | Dar de alta sus empresas (queda como administrador), aceptar invitaciones, cambiar su contraseña |
| **Administrador de una empresa** | El usuario que la dio de alta y a quien él invite como tal | Configuración, series, certificados, invitar personas, cambiar su rol o quitarles el acceso, anular y subsanar, modo de fallo del simulador local para su empresa |
| **Emisor / lector de una empresa** | Personas invitadas | Importar y expedir / solo consultar |

- El **registro** está **cerrado** por defecto: solo entra quien recibe una invitación (de un solo uso, con caducidad). El
  operador puede abrirlo en la instalación o en el panel; entonces se aplican límites por dirección y por hora. Con el
  registro cerrado, las invitaciones de los administradores de empresas solo las acepta quien ya tiene cuenta (las del
  operador sí crean cuentas), salvo que el operador lo permita en el panel.
- Una invitación solo vale mientras quien la creó siga siendo administrador activo de la empresa: degradar, quitar o
  desactivar a alguien anula sus invitaciones pendientes. Los administradores pueden **quitar** el acceso a una persona y
  cualquiera puede **salir** de una empresa; la empresa conserva siempre un administrador activo.
- Un mismo **NIF** solo puede darse de alta una vez (una cadena VERI*FACTU por obligado y sistema), también en su forma
  equivalente (las entidades C, D, F, G, J, U y V admiten como control el dígito o la letra): si ya está, hay que pedir
  acceso a uno de sus administradores.
- Requisitos por persona antes de emitir fuera del simulador: cada miembro declara su relación con la empresa y su
  autorización; el ámbito de la empresa debe estar confirmado (Configuración → «Paso a producción»).
- **NIF verificado**: cualquier usuario puede dar de alta una empresa con un NIF libre y declarar que un tercero (una
  gestoría) presenta en su nombre. Por eso, el primer envío a la AEAT (pruebas o producción) con el certificado de un
  tercero exige que el operador marque el NIF como verificado en el panel «Plataforma» (queda anotado); no hace falta si
  la empresa usa su propio certificado ni para las empresas que da de alta el operador. El simulador no lo exige. Al
  restaurar una copia se conserva la última decisión del operador (también una retirada posterior a la copia).
- Para un despliegue **público** faltan piezas (verificación del correo, recuperación de contraseña por correo,
  condiciones de uso y contrato de encargado del tratamiento): ver `docs/pendientes-produccion.md`.

### Actualizar desde la versión anterior

Al arrancar la nueva versión sobre los datos de la anterior, las migraciones se aplican solas. Las cuentas existentes,
salvo las del operador, deberán **elegir una contraseña nueva** al entrar: en la versión anterior su contraseña la fijaba
el operador o un administrador de empresa y no podía cambiarse, así que otra persona puede conocerla. Avise a sus
usuarios antes de actualizar. Las empresas de la versión anterior (las dio de alta el operador) quedan con el NIF
verificado.

## Certificados electrónicos y representación

Los certificados (.p12/.pfx) se cargan **dentro de la aplicación** (nunca se envían por otros medios): por empresa, o una
sola vez por usuario en **«Mis certificados»** y asignados a las empresas que ese usuario administra. Se guardan cifrados
(AES-256-GCM, clave maestra fuera de la base de datos) y nunca se devuelven. Un certificado de la propia empresa es
«propio»; uno de un tercero (p. ej. una gestoría) exige declarar su apoderamiento o colaboración social y entonces los
envíos llevan `Cabecera/Representante`. Cualquier administrador de la empresa puede dejar de usar en ella un certificado
asignado por otro usuario; cada uso (envío o consulta) queda auditado. Ver `docs/guia-uso.md` (apartado 7) y `docs/arquitectura.md` (Seguridad).

## Pruebas

```bash
python -m pytest -q
```

Casi 900 pruebas: los 14 casos de aceptación del encargo, las regresiones de cuatro rondas de verificación adversarial (tres del núcleo y una del extractor y el flujo de borradores), las de la prueba con 9 facturas reales (reproducidas con documentos sintéticos) y la extracción sobre el corpus de diseños variados (ver `docs/pruebas.md`; evaluación detallada con `python scripts/evaluar_extraccion.py tests/corpus tests/corpus_holdout* --detalle`). Todas son **simuladas o locales**; las evidencias (PDF, PNG, XML, JSON y capturas) se guardan en `docs/evidencias/`.

## Consulta a la AEAT y series con ceros de relleno

- **Consulta a la AEAT** (menú *Consulta AEAT*): pide al servicio `ConsultaFactuSistemaFacturacion` los registros de la
  empresa en un mes (todas las páginas) y los compara con los locales: *coinciden*, *solo en la AEAT*, *solo en local* y
  *estado o huella distintos*. No cambia nada por sí misma; un administrador puede **conciliar**, con confirmación, el único
  caso seguro (registro local sin respuesta definitiva, o rechazado por duplicado 3000, que la AEAT tiene con la misma
  huella; si no queda nada pendiente, se cierra también la incidencia de comunicación) y, si la AEAT tiene números de una
  serie que aquí no constan (p. ej. tras un desastre), **avanzar el contador** de la serie, con revisión de los números que
  se saltan, auditado y anotado en el diario (la propuesta mira también los meses siguientes hasta el actual). Si la AEAT
  tiene registros de esta instalación posteriores al último local (tras restaurar una copia sin el diario), avisa de la
  **cadena atrasada**, bloquea la expedición en ese entorno y un administrador puede **reanudar la cadena desde el último
  registro de la AEAT** (auditado, anotado en el diario, sin bifurcarla: el último es el que ningún otro tiene como registro
  anterior, y si no puede saberse con seguridad no se reanuda). Con el certificado de un tercero consulta sin
  `IndicadorRepresentante` (solo tras un 4140 lo repite con él y avisa de que el resultado es parcial). Usa el mismo
  transporte y las mismas salvaguardas que la remisión; el simulador local también responde a la consulta y sus textos nunca
  dicen «la AEAT». Ver `docs/api.md`, `docs/guia-uso.md` (apartado 8) y `docs/copias-y-recuperacion.md`.
- **Series con ceros de relleno**: `{n:3}` (o `{nnn}`) en el patrón (`{n:3}/A{aa}` → `042/A26`); si un número no encaja con
  ninguna serie, el borrador propone el patrón.

## Documentación

| Documento | Contenido |
|---|---|
| `docs/normativa/` | Investigación normativa con fecha de consulta, fuentes, calendario, especificaciones y criterios |
| `docs/matriz-requisitos.md` | Requisito, fuente oficial, funcionalidad, prueba o evidencia y estado |
| `docs/tratamiento-documentos.md` | Proceso correcto para cada tipo de PDF y programa de origen |
| `docs/arquitectura.md` | Decisiones, modelo de datos, seguridad, privacidad y **costes** |
| `docs/copias-y-recuperacion.md` | Copia, restauración y diario |
| `docs/guia-uso.md` | Guía de uso |
| `docs/api.md` | Contrato de la API |
| `docs/productor-y-componentes.md` | Productor, identificación del sistema y componentes |
| `docs/declaracion-responsable-borrador.md` | Borrador (no formalizado) de la declaración responsable |
| `docs/pendientes-produccion.md` | Lista concreta de pendientes antes de producción |

## Estructura

```
app/            servidor (FastAPI), extracción, validación, emisión, remisión, simulador, PDF
app/verifactu/  huella, XML, QR, respuestas y esquemas oficiales (XSD/WSDL)
app/static/     interfaz web (HTML/JS/CSS sin dependencias externas)
scripts/        generación de PDF de prueba, datos demo, contraste de esquemas, matriz
tests/          pruebas de aceptación y fixtures sintéticos
```

## Tamaño del código

**43.240 líneas de código** propio (sin líneas en blanco ni comentarios), en 100 ficheros. Contando también los
comentarios (5.250) y las líneas en blanco (5.683), son 54.173 líneas.

| Parte | Ficheros | Líneas de código |
|---|---:|---:|
| Aplicación (`app/`): servidor, extracción, validación, emisión, PDF y SQL | 44 | 15.810 |
| Interfaz web (`app/static/`): JavaScript, CSS y HTML | 3 | 5.748 |
| Scripts (`scripts/`): generadores de facturas sintéticas, evaluación y utilidades | 14 | 9.970 |
| Pruebas (`tests/`) | 37 | 11.680 |
| Despliegue (`Dockerfile`, `docker-compose.yml`) | 2 | 32 |
| **Total** | **100** | **43.240** |

Por lenguaje: Python 37.042, JavaScript 5.118, CSS 607, SQL 418, HTML 23, Dockerfile 17 y YAML 15.

No se cuentan los esquemas oficiales de la AEAT (XSD, WSDL y catálogo de errores, 2.316 líneas), los datos esperados del
corpus de pruebas (JSON, 3.910), los PDF de prueba ni la documentación. Medido el 12/11/2026 con
[cloc](https://github.com/AlDanial/cloc) 1.94:

```bash
cloc --vcs=git --include-lang=Python,JavaScript,CSS,HTML,SQL,Dockerfile,YAML .
```
