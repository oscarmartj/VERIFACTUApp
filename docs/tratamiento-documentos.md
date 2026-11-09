# Proceso correcto para cada PDF

## 1. Clasificación previa (antes de cualquier efecto fiscal)

La aplicación propone una clasificación con sus motivos y **el usuario la confirma** (queda registrada con su motivo):

| Situación | Cómo se detecta (propuesta) | Qué hace la aplicación |
|---|---|---|
| **Documento preparatorio** (borrador, proforma, presupuesto, o «factura» generada por la plantilla y aún no entregada) | Título «proforma/presupuesto/borrador»; emisor = empresa sin QR tributario | Permite crear un borrador, revisarlo y, si procede, **expedir** la factura definitiva con QR y registro. El original se conserva vinculado a la factura |
| **Factura propia ya emitida** | QR tributario de la empresa, mención VERI*FACTU, o confirmación del usuario | **Solo archivo/consulta.** No se añade QR, no se genera registro, no se reenvía. Si ya está registrada en otro SIF, su registro se consulta allí |
| **Factura recibida** | La empresa figura como destinataria | Solo se conserva. **Nunca** se comunica como venta propia |
| **Desconocido** | No aparece el NIF de la empresa | Bloqueado hasta aclararlo |

Reglas:

- La ausencia de QR en un documento antiguo **no** desencadena una nueva factura, una rectificativa ni una comunicación.
- **Una factura ya emitida no se «convierte»** a VERI\*FACTU añadiéndole un QR: el QR y el registro solo pueden generarse
  en el momento de la expedición, por el sistema que expide (el registro de alta, «de forma simultánea o inmediatamente anterior a la expedición»: art. 9 RRSIF; el QR forma parte de la factura que se expide: arts. 6.5.a y 7.5 ROF y art. 20 de la Orden HAC/1177/2024). Por eso, un PDF **firmado
  digitalmente** cuyo emisor es la empresa se propone como «factura propia ya emitida» (la firma indica que es
  el documento definitivo) y solo se archiva. Ejemplo: la factura real de referencia (firmada antes de que la empresa esté obligada) se
  archiva tal cual.
- Lo que sí se procesa son los documentos **preparatorios**: el PDF que genera la plantilla o el programa de origen se
  sube **antes de entregarlo al cliente**; la aplicación extrae sus datos, los valida y expide la factura definitiva con
  QR y registro. Sirve para cualquier diseño de factura (ver `docs/arquitectura.md`, «Extracción»).
- Un documento con QR tributario no puede clasificarse como preparatorio (lo impide la API).
- La réplica sintética de la factura de referencia («166/26», usada en las pruebas) se trata como **documento de pruebas**: queda marcada como no emitible fuera del simulador local; nunca se remiten sus datos a la AEAT.
- Para corregir una factura ya expedida (con esta aplicación) se usan los procedimientos específicos: subsanación del registro, rectificativa o anulación (ver abajo); nunca se edita ni se borra lo expedido.

## 2. Programa de origen de los PDF

En la configuración se indica cómo se generan (plantilla ofimática, programa de facturación u otro).

- **Plantilla ofimática** (Word/Excel/LibreOffice): el documento es **preparatorio**. Controles: no debe entregarse al cliente; la numeración la controla la serie confirmada en la aplicación (correlatividad exigida); la fecha de expedición es la del día en que se expide en la aplicación; el PDF definitivo es el que genera la aplicación, con QR.
- **Programa de facturación** (ERP o similar): ese programa puede ser **por sí mismo un sistema informático de facturación** sujeto al RD 1007/2023. Importar después sus PDF **no** lo adapta. La aplicación lo advierte en el ámbito; solo es coherente si ese programa se usa como generador de borradores y no expide ni registra facturas.
- Si el PDF de origen está **firmado digitalmente**, el original se conserva intacto. El PDF definitivo es un documento distinto y **no conserva esa firma** (se avisa y exige confirmación).

## 3. Del borrador al PDF definitivo

1. Extracción con procedencia (página, posición, método y confianza); los campos dudosos o ausentes se señalan; nada se inventa.
2. Validación determinista: NIF, contenido obligatorio, fechas, numeración, sumas y redondeos con aritmética decimal; casos no soportados bloqueados.
3. Correcciones con motivo y registro histórico.
4. Vista previa con marca «BORRADOR – SIN VALOR FISCAL» y espacio reservado para el QR (sin QR).
5. Confirmación expresa de la expedición (entorno, empresa, número, cliente, total).
6. En una única transacción: número consumido, **versión inmutable** de los datos aprobados, registro de alta encadenado y encolado. PDF, XML, QR y huella derivan de esa versión.
7. PDF definitivo:
   - **Superposición** si todo lo impreso en el original coincide con los datos aprobados (título «FACTURA», número, fecha de expedición = fecha impresa, partes, importes, sin retenciones) y hay hueco libre **cerca del margen superior**: se añade «QR tributario:» + QR + «VERI*FACTU» sin tapar contenido; el diseño original se conserva íntegro.
   - **Reconstrucción** en otro caso (lo habitual cuando el documento se preparó otro día): factura nueva con los datos aprobados, el logotipo recortado del original, referencias de obra, líneas, totales y condiciones de pago.
   - Verificación automática: el texto del PDF contiene número, NIF, fecha y total; el QR se decodifica desde el propio PDF y coincide con la URL registrada; aparece una sola vez y en la primera página. Si algo falla, no se expide.

## 4. Correcciones después de expedir

| Operación | Cuándo | Efecto |
|---|---|---|
| Corrección de borrador | Antes de expedir | Cambia el borrador; queda en el historial; sin efectos fiscales |
| **Subsanación** | El registro tenía errores (rechazado o aceptado con errores) y la factura no requiere rectificativa | Nuevo registro de alta encadenado con `Subsanacion=S` (y `RechazoPrevio=X` si el alta fue rechazada). La factura no cambia |
| **Rectificativa** | La factura tenía errores en sus datos (art. 15 ROF) | Nueva factura R1/R4 por diferencias, en serie específica, que identifica la rectificada; registro de alta propio |
| **Anulación** | La factura no debió expedirse (expedida por error) | Registro de anulación encadenado (`SinRegistroPrevio=S` si el alta no existe en la AEAT); la factura queda «anulada» y su número no se reutiliza |
| Recuperación / descarga | Siempre | Se descarga el PDF expedido (verificando su SHA-256) y los XML |
