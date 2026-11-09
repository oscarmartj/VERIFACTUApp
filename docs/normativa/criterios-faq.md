# Criterios interpretativos (FAQ AEAT) y contenido normativo clave

Consulta: 2026-11-09. Referencias en `fuentes.md`:

- [A6] FAQ de la sede AEAT, «actualizadas a 21 de octubre de 2026». El nombre entre paréntesis es la sección.
- [A9] «Aclaraciones a dudas de los desarrolladores», v1.3 de 4/12/2025.
- [A5] Cuestiones generales de la sede.

Las FAQ no tienen valor normativo, pero son el criterio publicado de la AEAT. Cuando algo es una inferencia mía y no un criterio publicado, lo indico como **INFERENCIA (NO VERIFICADO)**.

---

## PARTE I — Criterios de la AEAT (punto 3)

### 1. Borradores, proformas, presupuestos, albaranes y facturas de prueba

- **Están permitidos y no llevan QR**: «tanto los borradores de factura como las facturas proforma no llevan ningún código «QR» tributario. La emisión de facturas –o facturas simplificadas– proforma o sin validez fiscal, está permitida siempre y cuando se sustituyan finalmente por la factura o factura simplificada oficial expedida y esta se entregue al cliente» [A6, conceptos y definiciones].
- **No son registros de facturación**: el registro existe solo cuando la factura se valida y se expide. «Solo en ese momento podremos afirmar que existe el RF porque de hecho se ha expedido una factura. […] hasta ese momento, cualquier alteración que se produzca en ese registro, previo al RF, sería perfectamente lícita» [A9 FAQ 6].
- **Pero deben conservarse y estar vinculados al SIF**: «el sistema de generación de facturas pro-forma o sin validez fiscal, debe estar vinculado indefectiblemente al sistema de emisión de facturas formando una unidad. Conviene, a efectos de control interno, que se conserve registro de las prefacturas o facturas proforma elaboradas. […] no sería legal y sería susceptible de sanción, el uso de sistemas que generen documentos preparatorios […] sin que el sistema informático mismo disponga de elementos de control para la conservación de tales documentos preparatorios» [A6, conceptos y definiciones; A6, integridad].
- Según el art. 29.2.j LGT, cuando se expidan albaranes, proformas o prefacturas, «sus registros deberán conservarse de forma inalterable (salvo que la alteración se produzca por medio de un registro posterior, que también deberá quedar anotado en el sistema)». La AEAT trata los **presupuestos** como «facturas presupuestarias», análogas a las proformas [A9 FAQ 11].
- **Albaranes**: no llevan QR tributario [A6, QR].
- **Facturas de prueba o de formación** en un SIF en producción: son facturas reales (con QR y registro) y deben **anularse** después con un registro de anulación. Se recomienda usar una serie especial (por ejemplo «PRU 25 XXXX») y un texto visible «de pruebas» [A9 FAQ 11]. **No se puede reutilizar la numeración** de una factura de prueba: provoca el error de registro duplicado [A9 FAQ 6].
- **Para la app**: los estados borrador, proforma y presupuesto no tienen QR ni registro de facturación, pero deben guardarse con trazabilidad (historial inalterable) y vinculados a la factura final. La numeración fiscal se asigna solo al expedir.

### 2. Facturas expedidas antes de la fecha de obligación

- Las obligaciones de los usuarios se aplican desde las fechas de la DF 4.ª (01/01/2027 para IS y 01/07/2027 para el resto) [A6, ámbitos]. Hasta entonces es un «periodo de pruebas» en el que se pueden usar otros SIF, incluidos los no adaptados [A2]. Un SIF adaptado puede usarse antes con plena validez [A9 FAQ 2].
- La AEAT no admite registros con `FechaExpedicionFactura` anterior al 28/10/2024 [D3 §3.1.3.1].
- **No hay ningún criterio publicado que obligue a añadir QR o a generar registros de forma retroactiva** para facturas ya expedidas. **INFERENCIA (NO VERIFICADO)**: las facturas anteriores a la adopción del SIF no se registran. Una **rectificativa** que se expida después es una factura nueva: lleva QR y registro, y se puede identificar la original en `FacturasRectificadas` (opcional según el tipo). Si un registro existe en el SIF pero nunca se remitió (por ejemplo, porque el SIF pasó de NO VERI*FACTU a VERI*FACTU), las operativas «sin registro previo» permiten subsanarlo o anularlo [D3 anexo 5].
- Una sociedad que pase al nuevo programa en 2027 no necesita borrar el programa antiguo **si ya no puede expedir facturas con él**. Si sigue pudiendo facturar con él después de la fecha, la mera tenencia es infracción (art. 201 bis.2 LGT, 50.000 €) [A6, objeto].

### 3. Facturas recibidas de proveedores

- El RRSIF solo regula las **facturas expedidas**: el SIF registra «cierta información solo de las facturas expedidas» [A6, ámbitos — diferencia con el SII]; arts. 1.2, 9 y 10 RRSIF.
- La recepción se rige por el ROF: el destinatario las conserva (art. 19 ROF) [A6, VERI*FACTU].
- El receptor puede cotejar el QR [art. 17 RRSIF] y la AEAT ofrecerá la descarga de los registros que sus proveedores hayan remitido en VERI*FACTU [DA 3.ª RD 1007/2023; A6, VERI*FACTU; A9 FAQ 13].
- La falta de QR en una factura recibida no impide deducir el IVA [A6, conceptos y definiciones].
- Los sistemas que contabilizan facturas importadas no están sujetos al RRSIF, pero sí al mandato genérico del art. 29.2.j LGT (integridad, inalterabilidad…) [A6, VERI*FACTU].
- **Para la app**: las facturas recibidas no generan registro de alta ni van en la cadena VERI*FACTU.

### 4. Programa externo que genera PDFs que luego se importan en el SIF

- Un SIF es el que **expide** la factura. Entre sus funciones, «Admitir la entrada de información de facturación por cualquier método», incluido importar o recibir datos capturados en otros sistemas [A6, conceptos y definiciones; art. 1.2 RRSIF].
- Si un programa **expide** la factura (genera el PDF definitivo que se entrega), **ese programa es el SIF** y es él quien debe generar el registro y el QR. Importar después esas facturas en otro sistema no lo arregla. Si las facturas vienen de **otro SIF adaptado** (por ejemplo, por delegación), «no se tienen ni que volver a imprimir con QR, ni mucho menos volver a enviarlas a la Sede Electrónica». Lo mejor es importarlas **junto con sus registros de facturación**, que identifican el SIF emisor. La responsabilidad del fabricante del SIF receptor no alcanza a esos registros ajenos [A9 FAQ 10].
- Si el SIF adaptado recibe los datos de otro sistema, debe estar «**directa e indefectiblemente conectado**» con él [A9 FAQ 14.6]. Un sistema de pre-facturación debe formar una unidad con el SIF [A6, conceptos y definiciones].
- Word o Excel usados solo para redactar, imprimir y conservar no son SIF. Pasan a serlo si procesan la información, por ejemplo con macros que generan el libro registro [A6, ámbitos].
- **No puede convivir** facturar parte con un SIF y parte a mano. La única excepción es MiFacturae/FACe para quien no usa ningún SIF [A6, ámbitos].
- No se admite un volcado diferido al final del día desde un sistema desconectado: «ese volcado ulterior no es en sentido propio una facturación» [A6, capacidad de remisión].

### 5. Facturas rectificativas

- Tipos (L2): **R1** (error fundado de derecho y art. 80.Uno, Dos y Seis LIVA), **R2** (concurso, art. 80.Tres), **R3** (créditos incobrables, art. 80.Cuatro), **R4** (resto, incluidos errores en datos no monetarios), **R5** (rectificativa de simplificadas, por cualquier motivo) [A6, procedimientos].
- `TipoRectificativa`: **S** (sustitución: importes finales + `ImporteRectificacion` con `BaseRectificada` y `CuotaRectificada`) o **I** (diferencias: solo el importe de la rectificación, sin `ImporteRectificacion`). La AEAT publica ejemplos numéricos de ambos (por ejemplo, base 1.000 → 800: con S, `ImporteTotal` = 968; con I, `ImporteTotal` = −242) [A6, procedimientos].
- La identificación de las facturas rectificadas es opcional en el registro [A6, procedimientos; D3].
- `FechaOperacion` = la de la factura original. Si se rectifican varias, la más reciente [A6, procedimientos].
- Serie específica obligatoria para rectificativas (art. 6.1.a.2.º y 7.1.a.4.º ROF).
- **Rappels**: se documentan en rectificativas y basta con indicar el periodo, sin identificar las facturas (art. 15.4 ROF) [A9 FAQ 19].
- Una factura **F3** sustituye a simplificadas e identifica las sustituidas en `FacturasSustituidas`, que es opcional. Si la simplificada se abonó con un F2 negativo, la nueva factura se registra como F1 [A6, procedimientos].

### 6. Subsanación

- Sirve para corregir **datos del registro** cuando el ROF **no** exige rectificativa. Se genera un **nuevo registro de alta con `Subsanacion = S`** y la misma clave (NIF + número + fecha); el original queda intacto [SWeb §9.1.2; D3 §4.3.1; A6, alta].
- Casos: errores admisibles (aceptado con errores), errores no admisibles (rechazado) y errores detectados después por el obligado [D3 §4.3.1].
- Si el alta original fue rechazada: `Subsanacion = S` y `RechazoPrevio = X` [A9 FAQ 17].
- Si se cambian datos de la factura que **no** están en el registro, no hay que generar nada [A6, integridad; A9 FAQ 17.2.c].
- No hay plazo máximo: se hace «en cuanto sea posible» [A9 FAQ 17].
- Las subsanaciones y anulaciones «deben ser casos muy excepcionales» [A9 FAQ 17].
- El error 2004 (hora de generación posterior a la de la AEAT) no necesita subsanación [D3 §4.3.1].
- **No** se subsana en la remisión por requerimiento [SWeb §3; D3 §4.3.2].

### 7. Anulación

- Se usa «cuando se haya emitido erróneamente una factura» (art. 11.1 RRSIF), por ejemplo cuando la operación no existe o en facturas de prueba. **No** sirve para corregir operaciones reales: «todas las facturas emitidas, en la medida en que respondan a operaciones realmente efectuadas […] no pueden anularse», y para eso está la rectificativa [A9 FAQ 17.2.d].
- La factura anulada **mantiene su número** en la serie. El registro de alta y el de anulación se conservan y aparecen en los listados [A6, anulación].
- Si procede una factura correcta nueva, se expide con **otro número o con otra fecha** [A6, procedimientos].
- Influye que la factura no se haya llegado a entregar al cliente: refuerza la idea de expedición «fallida» [A9 FAQ 17].
- Una factura anulada responde «Factura no encontrada» en el cotejo de QR [D2 v0.4.6].
- `GeneradoPor` y `Generador` indican quién generó materialmente la anulación (E, D o T) [OM anexo 2.4].

### 8. Remisión inmediata (VERI*FACTU)

- Remisión «continuada, segura, correcta, íntegra, automática, consecutiva, instantánea y fehaciente» de **todos** los registros (art. 16.1 RRSIF).
- La inmediatez se aplica siempre. No se puede enviar al cerrar la tienda; solo se respetan los tiempos del control de flujo [A6, VERI*FACTU].
- Un SIF no puede intercalar modalidades. Un mismo obligado puede tener un SIF VERI*FACTU y otro NO VERI*FACTU, aunque no es lo preferible [A6, VERI*FACTU; A6, capacidad de remisión].
- El cambio de modalidad no puede ser dinámico (por sesión o por factura): el usuario elige al instalar [A9 FAQ 3].

### 9. Incidencias (AEAT no disponible, sin conexión, sin respuesta)

- No se interrumpe la facturación. Los registros se envían después, con la marca `Incidencia = S`, reintentando periódicamente. Para estos casos «no hay establecido un plazo máximo fijo» [A6, VERI*FACTU].
- La OM exige reintentar al menos cada hora, respetar el orden y avisar mostrando cuántos registros faltan (art. 16.4 OM). Las incidencias pueden tener que justificarse si la AEAT lo requiere.
- Sin respuesta: «se deben remitir de nuevo los registros de facturación hasta obtenerla» [A6, VERI*FACTU].
- Si hay pérdida de datos por un incidente, el SIF debe estar preparado para evitarla o recuperarse [A6, capacidad de remisión e integridad].

### 10. Duplicados

- Clave de un registro: `IDEmisorFactura` + `NumSerieFactura` + `FechaExpedicionFactura` [SWeb §3]. Reutilizar un número ya remitido, aunque esté anulado, da el error **3000 «Registro de facturación duplicado»** y la respuesta incluye `RegistroDuplicado` con el estado previo (Correcta, AceptadaConErrores o Anulada) [A9 FAQ 6; SWeb §6.4.4; D7].
- Un reenvío técnico de un registro ya aceptado también devuelve 3000. **INFERENCIA (NO VERIFICADO)**: tratar el 3000 con `EstadoRegistroDuplicado` ∈ {Correcta, AceptadaConErrores} como «ya presentado» si los datos coinciden con los del registro local.
- Los **duplicados de factura del art. 14 ROF** (copia por pérdida del original o por varios destinatarios, con la mención «duplicado») son otra figura distinta. **No hay criterio publicado** sobre si generan registro: **NO VERIFICADO**. **INFERENCIA**: no se genera un nuevo registro de alta (es la misma factura); se reimprime el mismo QR.

### 11. Obligación de incluir el QR

- «todas las facturas producidas con un SIF, cualquiera que sea la modalidad empleada, deberán llevar obligatoriamente el código «QR» impreso» [A6, QR]. La base legal son los arts. 6.5 y 7.5 ROF.
- Quien no está obligado pero usa voluntariamente un SIF adaptado (por ejemplo, un arrendador sin actividad económica) debe incluir el QR, porque el SIF no puede dejar de cumplir [A6, QR].
- Los sujetos al SII no están afectados [A6, QR].
- No es obligatorio imprimir en papel. Toda impresión o imagen (PDF) lleva QR. La factura estructurada lleva la URL [A6, QR].
- El QR va al principio de la factura, solo en la primera página y con un tamaño de 30-40 mm [A6, QR; D2].
- El usuario puede elegir dónde colocar el QR dentro de la parte superior, pero el SIF no debe permitirle suprimirlo [A9 FAQ 9].

### 12. Certificado utilizado para remitir (propio, representante, colaborador social, apoderamiento)

- El certificado debe ser válido en la sede y cualificado (art. 5 OM; SWeb §4.3: «certificado electrónico cualificado reconocido»).
- Remite el propio obligado o un tercero en representación (arts. 79-81 RD 1065/2007; Orden HAC/1398/2003) (art. 5 OM).
- «se puede utilizar cualquiera de las vías actuales de identificación admitidas por la AEAT, incluyendo la representación, el apoderamiento y la colaboración social, haciendo uso de un certificado cualificado por parte del tercero» [A6, VERI*FACTU].
- **Apoderamiento**: debe estar inscrito en el Registro de apoderamientos, con poder general o específico para VERI*FACTU. Se otorga por comparecencia, por documento público o privado con firma legitimada, o por internet (vía preferente) [A6, cumplimiento].
- **Colaboración social**: con los modelos de la **Resolución de 18/12/2024** [B11]:
  - Anexo I: del obligado a la empresa de software.
  - Anexo II: del obligado al profesional tributario.
  - Anexo III: del profesional a la empresa de software.

  El modelo queda en poder del colaborador y no se aporta salvo requerimiento [A6, cumplimiento].
- Las empresas de software firman el **Convenio 017**, por correo a `comunicacion.sepri@correo.aeat.es` [A6, colaboración; A9 FAQ 16].
- Se admite otorgar la representación por medios electrónicos (formulario web o pop-up) siempre que exijan cumplimentar y firmar el otorgamiento. No sirven «modalidades de aceptación de condiciones del servicio» [A9 FAQ 16.4; A6, colaboración].
- El campo `Cabecera/Representante` sirve para la colaboración social «transitiva» [SWeb §2.4].
- Existen endpoints separados para **certificado de sello** (`www10` y `prewww10`) [D5, COPIA].
- Las aplicaciones deben gestionar certificados y no pueden funcionar sin uno instalado [art. 4.b OM; A9 FAQ 3 y 11].

### 13. Declaración responsable (DR)

- **Quién**: el **productor** del SIF (art. 13.1 RRSIF). También quien desarrolla un SIF propio para uso interno, y quien integra código abierto. Los productores de ampliaciones o componentes de terceros aportan su propia DR (art. 15.4 OM) [A6, DR].
- El **comercializador** debe asegurarse de que el producto está certificado (art. 201 bis LGT) [A6, DR].
- **Sin certificación externa ni registro previo** («auto-certificación»). **Sin firma electrónica** obligatoria: la «fecha y lugar» del art. 13.4 se refieren a cuándo y dónde se suscribe [A6, DR].
- **Contenido y orden** (art. 15.1 OM). Empieza con el título «**DECLARACIÓN RESPONSABLE DEL SISTEMA INFORMÁTICO DE FACTURACIÓN**» y cada dato va precedido de su texto descriptivo:
  - a) nombre del SIF;
  - b) código identificador (= `IdSistemaInformatico`, 2 caracteres);
  - c) versión completa;
  - d) componentes hardware y software, y descripción de funcionalidades;
  - e) si es «solo VERI*FACTU»;
  - f) si es multi-OT;
  - g) tipos de firma en modo NO VERI*FACTU;
  - h) nombre o razón social del productor;
  - i) NIF del productor (o identificación extranjera con tipo y país);
  - j) dirección postal;
  - k) manifestación de cumplimiento del art. 29.2.j LGT, del RRSIF, de la OM y de las especificaciones de la sede;
  - l) fecha (día, mes y año) y lugar (localidad y país).

  Anexo recomendado (art. 15.2): otros contactos, URLs, cómo se cumple cada especificación y otra información.
- **Dónde** (art. 13.2 RRSIF; art. 15.3 OM; FAQ [A6, DR]):
  - **dentro del SIF** la de la versión actual, «accesible de forma rápida, fácil e intuitiva» desde cualquier terminal (se recomienda en «Ayuda», junto a «Acerca de»);
  - **fuera del SIF**, accesible al comercializador y al cliente antes y después de la compra (papel o formato electrónico gratuito y extendido, como PDF o texto).
- **Conservación**: el productor y el comercializador guardan las DR de **todas** las versiones. Se recomienda publicarlas en internet. Los usuarios conservan las de los sistemas que sustituyan o actualicen [art. 13.3 RRSIF; A6, DR].
- **Ejemplo oficial**: «Ejemplos de declaraciones responsables (V0.5.1)» [A10]. En el ejemplo 1 («solo VERI*FACTU»), el apartado 1.g dice: «no se realiza una firma electrónica expresa de los registros de facturación generados, ya que la normativa considera que quedan firmados al ser remitidos correctamente».
- **SIF «solo VERI*FACTU»**: también necesita DR [A6, DR].
- **Versiones**: cada cambio de versión requiere su DR. Un servicio de envío invocado de forma indefectible forma parte del producto: si cambia, cambia la versión de los productos que lo usan [A9 FAQ 8]. Las personalizaciones que no afectan a los requisitos no necesitan DR propia [A9 FAQ 9].
- La DR solo puede incorporarse a un producto adaptado. Certificar como cumplidor uno que no lo es «sería sancionable» [A9 FAQ 3].

### 14. Obligaciones del productor

- Ofrecer solo productos adaptados desde el **29/07/2025** (DF 4.ª RD 1007/2023; FAQ [A6, ámbitos]).
- Emitir y conservar las DR (art. 13).
- Facilitar información a la AEAT (art. 14.3 RRSIF).
- No fabricar SIF «solo NO VERI*FACTU» [A6, VERI*FACTU].
- Impedir el cambio dinámico de modalidad [A9 FAQ 3].
- Responder de que el SIF genere y guarde o remita los registros y de que ofrezca la exportación [A6, conservación].
- **Sanciones** (art. 201 bis LGT [B10]):
  - fabricar o comercializar sistemas no conformes: **150.000 €** por ejercicio y por tipo de sistema;
  - falta de certificación: **1.000 €** por sistema comercializado;
  - tenencia de sistemas no conformes por el usuario: **50.000 €** por ejercicio.
- El usuario que utiliza un SIF debidamente certificado «no tendrá responsabilidad por el incorrecto funcionamiento de este», que corresponde al fabricante [A5, certificación].
- Si el cliente no actualiza, la responsabilidad es del usuario, no del fabricante [A9 FAQ 14; A6, DR].

---

## PARTE II — Contenido normativo clave (punto 5)

### II.1 RD 1619/2012, Reglamento de facturación (consolidado, «Última actualización publicada el 31/03/2026») [B9]

| Art. | Contenido relevante (resumen fiel; las citas entre comillas son literales) |
|---|---|
| **3** | Excepciones a la obligación de facturar: operaciones exentas del art. 20 LIVA (salvo las de 20.Uno.2.º-5.º, 15.º, 20.º, 22.º, 24.º, 25.º y 28.º), recargo de equivalencia, régimen simplificado y REAGP, con excepciones (inmuebles, activos fijos). |
| **4** | Factura simplificada: importe ≤ **400 €** IVA incluido o rectificativa; ≤ **3.000 €** en ventas al por menor y otros sectores de la lista (hostelería, transporte de personas, peluquería…). No se admite en entregas intracomunitarias, ventas a distancia, inversión del sujeto pasivo con autofactura, etc. |
| **6.1** | Contenido de la factura completa: a) número y, en su caso, serie (numeración **correlativa** dentro de cada serie). Series **obligatorias** para facturas expedidas por el destinatario o por terceros (una por cada uno), **rectificativas** y otros casos. b) fecha de expedición; c) nombre y apellidos o razón social del expedidor y del destinatario; d) NIF del expedidor y, en ciertos casos, del destinatario; e) domicilios; f) descripción con los datos para la base imponible, precio unitario sin impuesto y descuentos; g) tipo(s); h) cuota «que deberá consignarse por separado»; i) fecha de la operación si es distinta; j) referencia a la exención; l) «facturación por el destinatario»; m) «inversión del sujeto pasivo»; n-p) menciones de regímenes especiales (agencias de viajes, bienes usados, criterio de caja). |
| **6.2** | Base imponible desglosada por separado si hay operaciones exentas y no exentas, con inversión y sin ella, o tipos distintos. |
| **6.5** (añadido por el RD 1007/2023) | «a) La representación gráfica del contenido parcial de la factura mediante un código "QR". En el caso de que la factura sea electrónica, la representación gráfica podrá ser sustituida por el contenido que representa el código "QR". b) Estas facturas, sean electrónicas o no, incorporarán además la frase "Factura verificable en la sede electrónica de la AEAT" o "VERI*FACTU" únicamente en aquellos casos en los que el sistema informático realice la remisión de todos los registros de facturación a la AEAT». |
| **7** | Factura simplificada: número y serie correlativa (con series separadas para rectificativas y para las expedidas por destinatario o tercero); **series separadas de las completas** si en el mismo año natural se expiden de ambos tipos; fecha de expedición; fecha de la operación si es distinta; NIF y nombre del expedidor; tipo de bienes o servicios; tipo impositivo («IVA incluido» opcional) y base por tipo si hay varios; contraprestación total; en rectificativas, la referencia a la rectificada. **7.2**: si el destinatario empresario lo exige, NIF y domicilio del destinatario y cuota por separado («simplificada cualificada», que se registra como F1). **7.5**: también el QR y la frase del art. 6.5. |
| **8.4** (añadido por el RD 1007/2023) | Se presume la autenticidad y la integridad de la factura expedida con un SIF conforme al RRSIF. |
| **8 bis, 9, 10** (modificados o añadidos por el RD 238/2026, vigor 20/04/2026) | Factura electrónica obligatoria B2B en los términos de la Ley 56/2007 y el RD 238/2026, salvo simplificadas no cualificadas. Autenticidad e integridad mediante firma electrónica avanzada (certificado cualificado), EDI u otros medios validados; para la B2B obligatoria, los procedimientos del RD 238/2026. |
| **11** | Plazo de expedición: «en el momento de realizarse la operación». Si el destinatario es empresario o profesional, «antes del día 16 del mes siguiente» al devengo. Para entregas intracomunitarias, antes del día 16 del mes siguiente al inicio del transporte. En criterio de caja, regla equivalente. |
| **12** | Moneda libre, pero la cuota de IVA en euros (el registro, siempre en euros: art. 10.2 RRSIF). Lengua libre. |
| **13** | Recapitulativas: operaciones del mismo mes natural para un mismo destinatario. Se expiden el último día del mes o, si el destinatario es empresario, antes del día 16 del mes siguiente. |
| **14** | Un solo original. Duplicados solo si hay varios destinatarios o pérdida del original, con la mención «duplicado». |
| **15** | Rectificativas: si la factura no cumple los arts. 6 o 7, si la cuota es incorrecta o en los supuestos del art. 80 LIVA. Se expiden «tan pronto como el obligado […] tenga constancia», dentro de los **4 años** desde el devengo. Nueva factura que identifica la rectificada; puede rectificar varias. En descuentos por volumen basta con indicar el periodo. Los importes pueden indicarse por la diferencia o por el resultado tras la rectificación, señalando la diferencia. Las facturas en sustitución o canje de simplificadas no son rectificativas (15.6). |
| **19** | Conservación durante el plazo de la LGT de las facturas recibidas, las copias o matrices de las expedidas, los justificantes, etc. Puede hacerla un tercero; si está fuera de la UE, con comunicación previa. |

### II.2 RD 1007/2023, RRSIF (consolidado, «Última actualización publicada el 03/12/2025») [B1]

| Art. | Contenido |
|---|---|
| 1 | Objeto. Concepto de SIF: admitir entrada, conservar y procesar información de facturación. Ámbito territorial (foral, Canarias, Ceuta y Melilla). |
| 3 | Sujetos obligados: IS, IRPF con actividad, IRNR con EP y entidades en atribución, aunque usen el SIF solo para una parte de su actividad. Productores y comercializadores. Exclusión del SII (3.3). |
| 4 | Operaciones excluidas (redacción del RD 254/2025). |
| 5 | Resolución de no aplicación. |
| 6 | Delegación en el destinatario o en un tercero, sin perder la responsabilidad (RD 254/2025). |
| 7 | Recursos: a) SIF conforme (puede servir a varios obligados si sus registros están diferenciados); b) aplicación de la AEAT. |
| **8** | Requisitos: integridad, conservación, accesibilidad, legibilidad, trazabilidad e inalterabilidad. **Capacidad de remitir** a la AEAT «de forma continuada, segura, correcta, íntegra, automática, consecutiva, instantánea y fehaciente». 8.2.a: alteración prohibida; «Cualquier necesidad de corrección o anulación […] deberá ser realizada mediante al menos un registro de facturación adicional posterior». 8.2.b: encadenamiento y fechado. 8.2.c: conservación, descarga y exportación. 8.3: registro de eventos. 8.4: disociación del acceso a la información tributaria. |
| **9** | El registro de alta se genera «de forma simultánea o inmediatamente anterior a la expedición de cada factura». |
| **10** | Contenido del registro de alta, letras a) a q): NIF y nombre del expedidor y, en su caso, del destinatario; indicación de expedición por destinatario o tercero; número y serie; fechas de expedición y de operación; tipo de factura; datos de rectificativa; facturas sustituidas; descripción; importe total; régimen; inversión del sujeto pasivo; base, tipo, cuota, recargo; exención y no sujeción; datos del registro anterior y «parte de la huella»; identificación del SIF y del productor; fecha, hora, minuto y segundo; circunstancias de generación. 10.2: importes en **euros**. |
| **11** | Registro de anulación: procede «cuando se haya emitido erróneamente una factura». Contenido, letras a) a h). |
| **12** | Huella en todos los registros y **firma electrónica**. |
| **13** | **Declaración responsable** del productor: por escrito y visible en el sistema en cada versión, disponible para el cliente y el comercializador, conservada para todas las versiones; contenido mínimo (13.4). |
| 14 | Verificación por la AEAT: personación, acceso, copia y requerimiento de registros; información a los productores. |
| **15** | Remisión voluntaria a la AEAT de todos los registros. |
| **16** | Sistema VERI*FACTU: 16.2 cumplimiento del art. 8 presumido por diseño; **16.3 exento de firma** («siendo suficiente con que calculen la huella»); 16.4 la aplicación de la AEAT es VERI*FACTU; **16.5** opción tácita al iniciar la remisión sistemática, que dura al menos hasta el final del año natural del primer envío. |
| 17 | Remisión de información por el receptor mediante el QR. |
| DA 1.ª | Autorizaciones del Departamento de Gestión: los datos dispensados no se incluyen y se indica la circunstancia. |
| DA 3.ª | La AEAT integrará los registros VERI*FACTU en el libro registro de facturas expedidas y facilitará la descarga a los destinatarios. |
| DF 1.ª | Modifica los arts. 6.5, 7.5 y 8.4 del ROF. |
| DF 4.ª | Calendario (ver `calendario-y-alcance.md`). |

### II.3 Orden HAC/1177/2024 (consolidada «Última actualización publicada el 28/10/2024»; corrección de errores BOE 08/11/2024) [B6][B7][B8]

| Art. | Contenido clave |
|---|---|
| 1 | Definiciones: SIF, **componente** y **componente principal** de facturación. |
| **2** | Un SIF multi-OT se comporta como sistemas independientes: registros separados, **cadenas independientes**, VERI*FACTU independiente por OT y visualización constante del OT activo, con aviso si hay más de uno. |
| **3** | En VERI*FACTU no se aplican los arts. 6.b-f, 7.f, 7.h, 7.i, 7.j, 8 y 9. |
| 4 | Capacidad de remisión: conexión a internet, gestión de certificados, envío y proceso de respuestas. |
| 5 | Identificación con un certificado válido en la sede; remisión propia o por representante o colaborador social. |
| **6** | Integridad: calcular la huella. Solo en NO VERI*FACTU: comprobar huella, firma y cadena, y alarmas con su evento. |
| **7** | Trazabilidad: a) datos del registro anterior (NIF, serie y número, fecha, primeros 64 caracteres de la huella); b) primer registro; c) **una cadena por OT y SIF**; d) altas y anulaciones en la misma cadena; e) fecha y hora exactas; f) ±1 minuto (no aplica en VERI*FACTU); g) huso horario; h) navegación adelante y atrás; i) comprobaciones previas; j) avisos. |
| 8 | Conservación y exportación por periodo (no aplica en VERI*FACTU). |
| 9 | Registro de eventos: tipos L2E y resumen cada 6 horas y al cerrar (no aplica en VERI*FACTU). |
| **10, 11** | Registros de alta y anulación en XML, UTF-8, con la estructura del anexo, apartado 3. |
| 12 | Número de registro de la autorización (`NumRegistroAcuerdoFacturacion`) y resolución de no aplicación (`IdAcuerdoSistemaInformatico`). |
| **13** | Campos de la huella y algoritmo según el documento técnico de la sede. |
| 14 | Firma **XAdES Enveloped** (ETSI EN 319 132) con certificado cualificado. |
| **15** | Declaración responsable: contenido, orden, título y ubicación. |
| **16** | Remisión: XML del anexo, apartado 4; **control de flujo** (60 s y 1.000 registros); validaciones; **incidencias** (reintento al menos cada hora, aviso con los pendientes); **CSV**. |
| **17** | Inicio en cualquier momento; permanencia hasta el 31/12; renuncia con `FechaFinVeriFactu` enviada antes del fin de año. |
| 18 | Remisión por requerimiento: mismas características, otro servicio, `RefRequerimiento`. |
| 19 | Aplicación de facturación de la AEAT: facturas en nombre propio o por apoderado, siempre con destinatario. |
| **20** | Representación gráfica: QR y, en VERI*FACTU, la frase. En la factura electrónica estructurada, la URL como campo. |
| **21** | QR de 30x30 a 40x40 mm, ISO/IEC 18004, nivel M; contenido: URL + NIF, serie y número, fecha e importe total. |
| DA 1.ª | Habilita a la AEAT para publicar los detalles técnicos (servicios, validaciones, huella, firma, QR y su URL, ubicación y textos). |
| Anexo | Bloques Cabecera, RegistroFactura (1-1000), RegistroAlta, RegistroAnulacion, RegistroEvento y SistemaInformatico; listas L1-L17 y L1E-L4E. |
