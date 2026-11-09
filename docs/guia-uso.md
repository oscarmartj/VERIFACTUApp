# Guía de uso

**Recorrido:** subir PDF → revisar datos → confirmar la emisión → descargar el PDF definitivo y consultar su estado.

## 0. Antes de empezar (una vez, rol administrador)

1. **Configuración → Empresa**: razón social, domicilio y ámbito: tipo de contribuyente, territorio, si lleva el SII, si opera en Canarias/Ceuta/Melilla, otras exclusiones y **cómo se generan los PDF** (plantilla o programa). Pulse **Confirmar ámbito**: la aplicación solo emite en los supuestos implementados y explica por qué cuando no.
2. **Declaración de autorización**: indique su relación con la empresa y declare que está autorizado a actuar por ella.
3. **Series**: cree la serie del entorno en el que va a trabajar con su patrón (p. ej. `{n}/{aa}` para «166/26») y el **siguiente número** que toca. Pruébela con un número y **confírmela**. Las rectificativas necesitan una serie propia (p. ej. `R{n}/{aa}`).
4. El **entorno activo** se ve siempre arriba: *Simulador local* (pruebas técnicas sin red), *Pruebas AEAT* o **PRODUCCIÓN** (en rojo). Los dos últimos están desactivados hasta completar la pantalla **Producción**.

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

## 4. Vista previa y emisión

**Vista previa** muestra la factura con la marca «BORRADOR – SIN VALOR FISCAL» y el hueco del QR. **Emitir** abre una confirmación con el entorno, la empresa, el número, el cliente y el total. Al confirmar se expide la factura, se genera el registro y se envía en cuanto el control de flujo lo permite. Un doble clic no duplica nada.

## 5. Descargar y consultar el estado

En **Facturas** puede buscar por número, cliente, fechas, estado y entorno, y descargar en PDF una a una o por lotes (ZIP con PDF y XML). Cada factura muestra dos estados distintos:
- **Estado de la factura**: emitida, rectificada, anulada.
- **Estado de la comunicación**: envío pendiente, resultado sin confirmar, registro aceptado, aceptado con errores, rechazado o bloqueado, con el código y el mensaje oficiales y el CSV.

> Que la AEAT acepte el registro no significa que haya validado el tratamiento fiscal de la operación.

Si hay una incidencia de comunicación, se muestra un aviso con los registros pendientes; puede seguir facturando y la aplicación reintenta sola (al menos cada hora).

## 6. Corregir lo ya expedido

Desde la ficha de la factura: **Subsanar registro** (errores del registro), **Rectificativa** (errores de la factura, R1/R4 por diferencias) o **Anular registro** (factura que no debió expedirse). Ninguna operación edita ni borra lo expedido. Ver `docs/tratamiento-documentos.md`.
