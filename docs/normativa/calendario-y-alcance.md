# Calendario, alcance y encaje de VERI*FACTU

Consulta: 2026-11-09. Las referencias entre corchetes (B1, A6…) remiten a `fuentes.md`.
Abreviaturas: **RRSIF** = Reglamento aprobado por el RD 1007/2023; **OM** = Orden HAC/1177/2024; **ROF** = Reglamento de facturación (RD 1619/2012); **SIF** = sistema informático de facturación; **OT/OEF** = obligado tributario / obligado a expedir facturas.

---

## 1. Calendario

### 1.1 Normas publicadas en el BOE (derecho vigente)

| Norma | Contribuyentes del Impuesto sobre Sociedades (art. 3.1.a RRSIF) | Resto de obligados del art. 3.1 (IRPF con actividad, IRNR con EP, entidades en atribución de rentas) | Productores y comercializadores (art. 3.2) | Servicio de recepción de la AEAT |
|---|---|---|---|---|
| **RD 1007/2023**, texto original (BOE 06/12/2023) [B2] | antes del 01/07/2025 | antes del 01/07/2025 | 9 meses desde la entrada en vigor de la OM. Contratos de mantenimiento plurianual: adaptados antes del 01/07/2025 | 9 meses desde la entrada en vigor de la OM |
| **RD 254/2025**, de 1 de abril (BOE 02/04/2025, BOE-A-2025-6600, vigor 03/04/2025) [B3] | antes del **01/01/2026** | antes del **01/07/2026** | 9 meses desde la OM. Contratos plurianuales: antes de las fechas anteriores | igual |
| **RDL 15/2025**, de 2 de diciembre, DF 1.ª (BOE núm. 290, 03/12/2025, **BOE-A-2025-24446**, vigor 04/12/2025) [B4] — **VIGENTE** | antes del **01/01/2027** | antes del **01/07/2027** | «en el plazo máximo de nueve meses desde la entrada en vigor de la orden ministerial», sin perjuicio de adaptar los contratos plurianuales antes de las fechas anteriores | igual |

Texto vigente de la DF 4.ª del RD 1007/2023, en la redacción del RDL 15/2025 (copia literal del consolidado [B1]):

> «No obstante, los obligados tributarios a que se refiere el artículo 3.1.a) deberán tener adaptados los sistemas informáticos a las características y requisitos establecidos en este reglamento y en su normativa de desarrollo antes del 1 de enero de 2027. El resto de obligados tributarios mencionados en el artículo 3.1 deberán tener operativos los citados sistemas informáticos antes del 1 de julio de 2027.»

Datos que lo confirman:

- **Convalidación**: el Congreso convalidó el RDL 15/2025 el 11/12/2025 (Resolución publicada en el BOE núm. 301, de 16/12/2025, **BOE-A-2025-25695**) [B5]. La DF 2.ª del RDL salvaguarda el rango reglamentario del RD 1007/2023. Nota: el preámbulo del RDL menciona un «artículo 3», pero la modificación está en la **disposición final primera**.
- **No hay cambios posteriores en el BOE**: el consolidado del RD 1007/2023 muestra «Última actualización publicada el 03/12/2025» a fecha 09/11/2026 [B1]. En el BOE solo aparece un recurso de inconstitucionalidad (BOE-A-2026-7634) contra la **DF 3.ª** del RDL 15/2025, que trata del régimen local de Cataluña y no de VERI*FACTU [B4].
- **Lo confirma la AEAT**: la nota informativa (actualizada el 13/10/2026) dice que las entidades del IS deben estar adaptadas antes del 01/01/2027 y el resto antes del 01/07/2027 [A2][A3]. La FAQ (21/10/2026) dice lo mismo [A6].
- **Productores de SIF**: la OM entró en vigor el 29/10/2024 (BOE 28/10/2024) [B6], así que el plazo de 9 meses venció el **29/07/2025**. La FAQ AEAT lo dice así: «nueve meses desde la entrada en vigor de la orden ministerial (29 de julio de 2025)» [A6, ámbitos]. Desde esa fecha los productores solo pueden ofrecer SIF adaptados, salvo los contratos de mantenimiento ya vigentes [A9 §1].
- **Servicios de la AEAT**: «Dichos servicios han entrado en producción el pasado 23 de abril de 2025» (remisión, cotejo de QR, consulta) [A9 §1 y §2].
- **Periodo previo («de pruebas»)**: «El periodo previo a las respectivas fechas es un periodo de pruebas, durante el cual se podrá dejar de remitir registros de prueba con un SIF modalidad VERI*FACTU y utilizar para facturar otros SIF» [A2]. La FAQ añade que, si se opta por VERI*FACTU dentro de ese periodo, no hay obligación de permanecer hasta el 31/12. A partir del 01/01/2027 o del 01/07/2027 sí rige la permanencia hasta el fin del año natural (art. 16.5 RRSIF) [A6, objeto].
- **Uso anticipado válido**: un SIF adaptado puede usarse «desde ya mismo» con plena validez fiscal [A9 §2].

### 1.2 Anuncios y propuestas que **no** están publicados en el BOE

| Fecha | Qué hay | Estado | Verificación |
|---|---|---|---|
| 05/10/2026 | La portada AEAT [A1] enlaza una «**Comunicación del Ministerio de Hacienda de 05/10/2026: Previsión de ampliación del plazo de adaptación de los sistemas informáticos de facturación (SIF)**» (PDF en `www.hacienda.gob.es`). | **Anuncio o previsión, no es una norma.** En el BOE no hay ninguna norma que modifique la DF 4.ª después del RDL 15/2025 (ver 1.1). | **NO VERIFICADO (fuente no accesible)**: solo he podido confirmar el **título** del enlace en la sede. Según fuentes secundarias [S1], la comunicación prevé **aplazar las obligaciones pendientes del RRSIF hasta octubre de 2028** para hacerlas coincidir con la factura electrónica obligatoria de quienes facturan **hasta 8 M€**. No he podido confirmar si el anuncio afecta también a los contribuyentes del IS (01/01/2027) ni qué instrumento normativo se usará. |

**Recomendación para el código**: guardar las fechas de obligación como **configuración**, con un valor por defecto para «IS» (2027-01-01) y otro para «resto» (2027-07-01), y nunca fijarlas en el código. Hay que revisar el BOE y el consolidado del RD 1007/2023 antes de cada release.

### 1.3 Calendario relacionado: factura electrónica B2B (Ley «Crea y Crece»)

- **Ley 18/2022** (art. 12, que modifica el art. 2 bis de la Ley 56/2007). La desarrolla el **RD 238/2026, de 25 de marzo** (BOE núm. 79, 31/03/2026, BOE-A-2026-7295, vigor 20/04/2026) [B12]. Según su DF 4.ª, la aplicación efectiva se cuenta desde la entrada en vigor de la orden ministerial de la solución pública:
  - «a) Doce meses después, para los empresarios y profesionales cuyo volumen de operaciones […] haya excedido de 8 millones de euros durante el año natural inmediato anterior.»
  - «b) Veinticuatro meses después, para el resto de los empresarios y profesionales.»
- Esa orden es la **Orden HAC/1028/2026, de 2 de octubre** (BOE núm. 247, 05/10/2026, BOE-A-2026-20587, **vigor 06/10/2026**). Su DF única dice que con su entrada en vigor se da «inicio al cómputo de los plazos» [B13]. Por tanto: más de 8 M€ → **octubre de 2027** (12 meses desde el 06/10/2026); resto → **octubre de 2028** (24 meses). El día exacto depende de cómo se compute «de fecha a fecha»; no hay calendario oficial de la AEAT que lo precise: **NO VERIFICADO**.
- Relación con VERI*FACTU: el modelo UBL de la Orden HAC/1028/2026 incluye el grupo **BG-24 «QR FISCAL»** con BT-122 = «QR», BT-123 = «VERIFACTU» o «TICKETBAI», y BT-ES-25 = «la URL correspondiente al QR VERI*FACTU o TICKETBAI» [B13]. Es decir, la factura electrónica B2B lleva la URL del QR tributario como campo independiente, en línea con el art. 20.2 OM [B6].

---

## 2. Alcance subjetivo y objetivo

### 2.1 Quién está obligado

- **Art. 3.1 RRSIF** [B1]: obligados tributarios que «utilicen sistemas informáticos de facturación, aunque solo los usen para una parte de su actividad»:
  - a) contribuyentes del **IS**, salvo las entidades **exentas del art. 9.1 LIS**. Las **parcialmente exentas** (art. 9.2-9.4 LIS) solo quedan obligadas por las operaciones que generen rentas sujetas y no exentas;
  - b) contribuyentes del **IRPF** con actividades económicas;
  - c) contribuyentes del **IRNR** con establecimiento permanente;
  - d) **entidades en atribución de rentas** con actividades económicas.
- **Art. 3.2**: productores y comercializadores de SIF, en lo relativo a esa actividad.
- La AEAT lo resume en la «**regla de los 4 NO**» [A6, ámbitos]: el RRSIF se aplica a quien expida facturas establecido en territorio español y (a) NO facture exclusivamente a mano, (b) NO esté en el SII (de forma obligatoria o voluntaria), (c) NO tenga domicilio fiscal en el País Vasco o en Navarra y (d) NO tenga una resolución de no aplicación (art. 5 RRSIF).
- Solo afecta a las **facturas** (completas y simplificadas). No afecta a otros justificantes como albaranes o recibos REAGP [A6, conceptos; art. 1.2 RRSIF].
- **Quien no está obligado a expedir factura** (art. 3 ROF): el RRSIF no le afecta «siempre y cuando no se expidan facturas o estas se expidan TODAS a mano». Si expide alguna factura con un SIF, se le aplica [A6, ámbitos].
- **Hojas de cálculo y procesadores de texto**: no son SIF si solo se usan para introducir datos, expedir o imprimir y conservar. Sí lo son si procesan la información para generar libros registro, contabilidad, etc. Por ejemplo, una macro que genere el libro de facturas expedidas convierte la hoja en SIF [A6, ámbitos].
- **Todo o nada**: «no es posible que conviva la emisión de parte de las facturas con un SIF y parte de forma manual». La única excepción es usar MiFacturae/FACe sin ningún SIF [A6, ámbitos].
- **Operaciones con el extranjero**: incluidas (exportaciones, entregas intracomunitarias, servicios internacionales). Los no residentes quedan incluidos si tienen un EP [A6, ámbitos].
- **Tiendas online**: incluidas [A6, ámbitos].

### 2.2 Exclusiones

| Exclusión | Fuente |
|---|---|
| Contribuyentes que llevan los libros registro por **SII** (art. 62.6 RIVA), obligatorio o voluntario («El ámbito subjetivo de ambos proyectos es excluyente») | Art. 3.3 RRSIF [B1]; FAQ [A6, ámbitos] |
| Entidades exentas del art. 9.1 LIS | Art. 3.1.a RRSIF |
| Operaciones de las DA 3.ª y 6.ª del ROF (determinadas entregas de energía eléctrica; facturas expedidas por la CNE por cuenta de terceros) | Art. 4.1 RRSIF (redacción del RD 254/2025) |
| Facturas por operaciones realizadas a través de EP situados en el extranjero | Art. 4.2 RRSIF |
| Facturas expedidas materialmente por el destinatario o por un tercero (art. 5 ROF) cuando este lleva sus libros por **SII** | Art. 4.3 RRSIF (RD 254/2025); FAQ [A6, cumplimiento] |
| Resolución de no aplicación (Departamento de Inspección), que puede ser total, parcial o temporal | Art. 5 RRSIF |
| Domicilio fiscal en los **Territorios Históricos del País Vasco** o en **Navarra**: sujetos a la normativa foral (TicketBAI en Álava, Bizkaia y Gipuzkoa). El RRSIF se aplica a quien tenga domicilio fiscal en territorio común | Art. 1.3 RRSIF; FAQ [A6, ámbitos] |

### 2.3 Canarias, Ceuta y Melilla

Están **dentro** del ámbito. Las referencias al IVA se entienden hechas al IGIC y al IPSI (art. 1.3 RRSIF). En el registro se usa el campo `Impuesto` (L1: 01 IVA, 02 IPSI, 03 IGIC, 05 Otros) y las claves L8A (IVA) o L8B (IGIC) [B6 anexo; A6 ámbitos; A9 FAQ 23-26].

### 2.4 Delegación (autofacturación o facturación por terceros)

Si la expedición material se delega en el destinatario o en un tercero (art. 5 ROF), es ese destinatario o tercero quien genera y, en VERI*FACTU, remite el registro. El obligado sigue siendo responsable (art. 6 RRSIF, redacción del RD 254/2025). Para remitir en nombre del obligado hace falta un **apoderamiento inscrito** o **colaboración social** [A6, cumplimiento; B11].

---

## 3. Modalidades: VERI*FACTU frente a «NO VERI*FACTU»

| Aspecto | VERI*FACTU | NO VERI*FACTU (emisión de facturas no verificables) | Fuente |
|---|---|---|---|
| Remisión de los registros a la AEAT | Obligatoria, automática, continua e inmediata (es lo que define la modalidad) | No se remiten. Solo se envían si hay un **requerimiento**, por un servicio distinto | Art. 16 RRSIF; arts. 16 y 18 OM; SWeb §6.6 [A8] |
| Huella (hash) y encadenamiento | **Sí** | Sí | Art. 12 y 16.3 RRSIF; art. 7 OM |
| **Firma electrónica XAdES Enveloped** de los registros | **No es obligatoria** («siendo suficiente con que calculen la huella»). La FAQ añade que tampoco es un campo obligatorio del registro | **Obligatoria** (registros de facturación y de evento) | Art. 16.3 RRSIF; art. 14 OM; FAQ «Firma» [A6]; Anexo OM: «Signature … Obligatorio para conservación y para requerimiento, pero no para remisión voluntaria» [B7] |
| **Registro de eventos** | **No** (art. 9 OM no aplica). Un SIF «solo VERI*FACTU» no está obligado a implementarlo; un SIF «dual» sí, para cuando funcione como NO VERI*FACTU | Obligatorio | Art. 3 y 9 OM; FAQ eventos y hash [A6]; A9 FAQ 15 nota 1 |
| Comprobaciones bajo demanda de huella, firma y cadena; alarmas; seguimiento adelante y atrás; comprobar el registro anterior antes de generar uno nuevo | No aplican (art. 3 OM excluye 6.b-f, 7.f, 7.h, 7.i, 7.j, 8 y 9) | Obligatorias | Art. 3, 6 y 7 OM; FAQ trazabilidad [A6]; A9 FAQ 15 |
| Exactitud del reloj (±1 minuto) responsabilidad del usuario | No aplica (7.f excluido) | Sí | Art. 7.f OM; FAQ VERI*FACTU [A6] |
| Conservación y exportación de registros por el SIF | No regulada (los conserva la AEAT). Recomendable conservarlos por funcionalidad | Obligatoria durante el plazo del art. 19.1 ROF | Art. 16.2 RRSIF; art. 8 OM; A9 FAQ 13; FAQ conservación [A6] |
| QR en la factura | Sí. URL `…/ValidarQR` | Sí. URL `…/ValidarQRNoVerifactu` | Arts. 6.5 y 7.5 ROF; art. 20-21 OM; D2 |
| Frase «Factura verificable en la sede electrónica de la AEAT» o «VERI*FACTU» | **Sí** | **No** | Art. 6.5.b ROF; art. 20.1.b OM |
| Permanencia | Al menos hasta el 31/12 del año del primer envío efectivo. Renuncia con `FechaFinVeriFactu` antes de fin de año | — | Art. 16.5 RRSIF; art. 17 OM |
| Producción de SIF «solo NO VERI*FACTU» | — | **No admitida**: todo SIF debe tener capacidad de remisión | Art. 8.1 RRSIF; FAQ VERI*FACTU [A6] |

---

## 4. VERI*FACTU frente a Facturae, factura electrónica B2B, SII y TicketBAI

| Sistema | Qué es | Relación con VERI*FACTU |
|---|---|---|
| **VERI*FACTU / RRSIF** | Requisitos de los SIF: registro de facturación encadenado por cada factura expedida, QR, declaración responsable y, en modalidad VERI*FACTU, remisión inmediata. «Los Registros de facturación […] en ningún caso son facturas electrónicas» [A5] | — |
| **Facturae / FACe** (Ley 25/2013, B2G) | Formato XML de factura electrónica y punto de entrada para facturar a las Administraciones Públicas | Independiente. Una factura Facturae emitida por un SIF **debe** cumplir el RRSIF (registro y URL del QR). La única excepción es MiFacturae usado por quien no tiene ningún SIF [A6, ámbitos]. El RD 238/2026 admite Facturae como sintaxis B2B [B12, art. 7.1.d] |
| **Factura electrónica B2B** (Ley 18/2022 + RD 238/2026 + Orden HAC/1028/2026) | Obligación de expedir facturas en formato estructurado (UBL, CII, EDIFACT o Facturae) entre empresarios, firmadas, con copia fiel en UBL a la solución pública de la AEAT y comunicación de estados y pagos | Obligación **distinta y acumulable**. La factura electrónica estructurada debe llevar la **URL del QR como campo** (art. 20.2 OM; BG-24 «QR FISCAL» en la Orden HAC/1028/2026). Calendario en §1.3 |
| **SII** (art. 62.6 RIVA) | Llevanza electrónica de los libros registro de IVA (facturas emitidas y recibidas) en un plazo determinado | **Excluyente**: quien está en el SII no aplica el RRSIF (art. 3.3). Quien no está en el SII sigue obligado a llevar los libros registro y a presentar los modelos 347 y 349; VERI*FACTU no los sustituye [A6, VERI*FACTU] |
| **TicketBAI** (Haciendas Forales vascas) | Sistema foral parecido a VERI*FACTU | Se aplica según el domicilio fiscal: quien está sujeto a normativa foral queda fuera del RRSIF y cumple TicketBAI [A6, ámbitos]. Navarra también queda fuera del RRSIF; no he verificado su sistema propio: **NO VERIFICADO** |
