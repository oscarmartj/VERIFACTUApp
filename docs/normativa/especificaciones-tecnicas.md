# Especificaciones técnicas VERI*FACTU (para implementar)

Consulta: 2026-11-09. Las referencias [B…], [A…] y [D…] remiten a `fuentes.md`.
Grado de verificación de cada bloque:

- **VERIFICADO**: leído en el BOE o en la sede AEAT.
- **COPIA**: leído en una copia del documento oficial alojada por terceros, porque el dominio oficial no era accesible. **NO VERIFICADO contra el original**, aunque se ha contrastado con fuentes oficiales.
- **NO VERIFICADO**: solo hay fuentes secundarias.

---

## 4a. Huella («hash») de los registros

Fuentes: art. 13 OM [B6] (VERIFICADO); FAQ «Huella o hash» [A6] (VERIFICADO: «El algoritmo de cálculo de huella o «hash» a emplear es, por el momento, SHA-256 en todos los casos»); documento técnico **«Detalle de las especificaciones técnicas para generación de la huella o hash de los registros de facturación», v0.1.2 de 27/08/2024** [D1] (COPIA). Los tres ejemplos oficiales los he **recalculado con SHA-256 y coinciden carácter a carácter**.

### Campos, en este orden (D1 §3, literal)

**RegistroAlta** (8 campos):

1. `IDEmisorFactura` (RegistroAlta/IDFactura/IDEmisorFactura)
2. `NumSerieFactura` (RegistroAlta/IDFactura/NumSerieFactura)
3. `FechaExpedicionFactura` (RegistroAlta/IDFactura/FechaExpedicionFactura)
4. `TipoFactura` (RegistroAlta/TipoFactura)
5. `CuotaTotal` (RegistroAlta/CuotaTotal)
6. `ImporteTotal` (RegistroAlta/ImporteTotal)
7. `Huella` (RegistroAlta/Encadenamiento/RegistroAnterior/Huella), es decir, la huella del registro **anterior**
8. `FechaHoraHusoGenRegistro` (RegistroAlta/FechaHoraHusoGenRegistro)

**RegistroAnulacion** (5 campos):

1. `IDEmisorFacturaAnulada` (RegistroAnulacion/IDFactura/IDEmisorFacturaAnulada)
2. `NumSerieFacturaAnulada` (RegistroAnulacion/IDFactura/NumSerieFacturaAnulada)
3. `FechaExpedicionFacturaAnulada` (RegistroAnulacion/IDFactura/FechaExpedicionFacturaAnulada)
4. `Huella` (RegistroAnulacion/Encadenamiento/RegistroAnterior/Huella)
5. `FechaHoraHusoGenRegistro` (RegistroAnulacion/FechaHoraHusoGenRegistro)

**RegistroEvento** (solo NO VERI*FACTU, 9 campos): `NIF` (del productor, en SistemaInformatico), `ID` (IDOtro del productor), `IdSistemaInformatico`, `Version`, `NumeroInstalacion`, `NIF` (ObligadoEmision), `TipoEvento`, `HuellaEvento` (del evento anterior), `FechaHoraHusoGenEvento`.

### Reglas de formato (D1 §3 y §5, literal)

- Formato de la cadena: `nombreCampo1=valorCampo1&nombreCampo2=valorCampo2&nombreCampoN=valorCampoN`
- «El nombre del campo será un valor constante tal y como se describe en el XML del diseño de registro.»
- «Los valores de los campos deberán tener la misma información contenida en el campo correspondiente del fichero XML, pero **eliminando los espacios al inicio y al final de cada valor** y en los campos numéricos se tratarán indistintamente los valores con una o dos posiciones en los decimales, sin tener relevancia los ceros a la derecha, considerándose todos igualmente válidos para la generación de la huella o hash.» Ejemplos del documento: `<NumSerieFactura> 12345678 / G33 </NumSerieFactura>` da `12345678 / G33`; `123.1` equivale a `123.10`.
- «Si el campo no aparece en el registro (o aparece, pero sin valor), en la cadena de caracteres solo se deberá poner el nombre del campo y el carácter "=" (sin valor a continuación)». Ejemplo del primer registro: `…ImporteTotal=123.45&Huella=&FechaHoraHusoGenRegistro=…`
- «Dicha cadena de caracteres será codificada en un array de bytes en formato **UTF-8**».
- Algoritmo: lista L12 de la OM, `01` = SHA-256 (campo `TipoHuella` = `01`).
- Salida: «En sistema **hexadecimal**. En **mayúsculas**. El tamaño será de **64 caracteres** alfanuméricos.» Se guarda en `RegistroAlta/Huella` o `RegistroAnulacion/Huella`.
- **Primer registro de la cadena**: `Encadenamiento/PrimerRegistro = "S"` y no se informa `RegistroAnterior`. **Aun así hay que calcular e informar la huella propia** del registro, usando `Huella=` vacío en la cadena (D1 §5).
- Encadenamiento: `RegistroAnterior/Huella` = «Primeros 64 caracteres de la huella o «hash» del registro de facturación anterior» (OM, anexo 2.3 y 2.4 [B7]). Con SHA-256 en hexadecimal son los 64 caracteres completos.
- Validación AEAT: si la huella no coincide con la que calcula la AEAT, el registro queda **«Aceptado con errores»** (D1 §7; error 2000 [D7]). Si la huella anterior no tiene formato de 64 caracteres hexadecimales en mayúsculas, se devuelve un aviso sin rechazo (D3 §3.1.3.18).

**Recomendación de implementación**: construir la cadena con **los mismos literales que se serializan en el XML** (mismo número de decimales). Lo más sencillo es fijar dos decimales, sin ceros a la izquierda y con punto decimal (SWeb §6.8 [A8]).

### Ejemplos oficiales (D1 §6), recalculados

| Caso | Cadena de entrada (exacta) | Huella SHA-256 |
|---|---|---|
| 1. Primer registro (alta) | `IDEmisorFactura=89890001K&NumSerieFactura=12345678/G33&FechaExpedicionFactura=01-01-2024&TipoFactura=F1&CuotaTotal=12.35&ImporteTotal=123.45&Huella=&FechaHoraHusoGenRegistro=2024-01-01T19:20:30+01:00` | `3C464DAF61ACB827C65FDA19F352A4E3BDC2C640E9E9FC4CC058073F38F12F60` |
| 2. Segundo registro (alta) | `IDEmisorFactura=89890001K&NumSerieFactura=12345679/G34&FechaExpedicionFactura=01-01-2024&TipoFactura=F1&CuotaTotal=12.35&ImporteTotal=123.45&Huella=3C464DAF61ACB827C65FDA19F352A4E3BDC2C640E9E9FC4CC058073F38F12F60&FechaHoraHusoGenRegistro=2024-01-01T19:20:35+01:00` | `F7B94CFD8924EDFF273501B01EE5153E4CE8F259766F88CF6ACB8935802A2B97` |
| 3. Anulación encadenada | `IDEmisorFacturaAnulada=89890001K&NumSerieFacturaAnulada=12345679/G34&FechaExpedicionFacturaAnulada=01-01-2024&Huella=F7B94CFD8924EDFF273501B01EE5153E4CE8F259766F88CF6ACB8935802A2B97&FechaHoraHusoGenRegistro=2024-01-01T19:20:40+01:00` | `177547C0D57AC74748561D054A9CEC14B4C4EA23D1BEFD6F2E69E3A388F90C68` |

Úsalos como tests unitarios. Comprobación realizada:
`hashlib.sha256(cadena.encode('utf-8')).hexdigest().upper()` reproduce exactamente las tres huellas.

- La huella **no** forma parte del QR (FAQ hash [A6]).
- Los SIF VERI*FACTU **no** están obligados a comprobar huellas, aunque sí a calcularlas y encadenarlas bien (FAQ hash [A6]).

---

## 4b. Código QR y servicio de cotejo

Fuentes: arts. 20 y 21 OM [B6] (VERIFICADO); FAQ «Posibilidad de remisión… Código QR» [A6] (VERIFICADO); **«Detalle de las especificaciones técnicas del código QR…», v0.4.7 de 17/10/2024** [D2] (COPIA).

### Características físicas

| Requisito | Valor | Fuente |
|---|---|---|
| Norma | ISO/IEC 18004 (D2 cita «ISO/IEC 18004:2015») | Art. 21.1 OM; D2 §2 |
| Corrección de errores | **Nivel M** (medio) | Art. 21.1 OM |
| Tamaño | **entre 30x30 y 40x40 mm** («no puede ser menor de 30x30 mm / no puede ser mayor de 40x40 mm») | Art. 21.1 OM; FAQ QR [A6] |
| Zona de silencio | «como mínimo **2 milímetros** de espacio vacío (en blanco) alrededor de los cuatro lados del código «QR», recomendándose que sean **6 milímetros**» | D2 §3 |
| Contraste | «suficientemente alto para asegurar la legibilidad» | D2 §3 |
| Legibilidad | «deberán ser legibles y estar impresos con una resolución apropiada» | Art. 20.1 OM |

### Ubicación y textos (D2 §3, literal o casi literal; coincide con la FAQ [A6])

- Al **principio de la factura**, antes del contenido generado por el SIF, y siempre como **primer QR** de la factura. En formato vertical: «arriba de esta, próximo al margen superior, preferiblemente centrado […] (o, si no, hacia el margen izquierdo-superior)». En formato apaisado: «a la izquierda de esta, preferiblemente cercana al margen superior-izquierdo».
- **Varias páginas**: «el código «QR» aparecería una única vez, en la primera página».
- Texto **encima** del QR, siempre: **«QR tributario:»** («preferiblemente centrado con respecto a este»).
- **Solo en VERI*FACTU**, «justo debajo del código «QR»»: la frase **«Factura verificable en la sede electrónica de la AEAT»** o **«VERI*FACTU»**, preferiblemente centrada. Puede ocupar varias líneas.
- Tipo y tamaño de letra de ambos textos: legibles, «siempre iguales o superiores a los del resto de datos de la factura» (D2). El art. 20.1.b OM exige un tamaño «bien visible, similar al del resto de datos».
- Se admite una ubicación distinta si hay obstáculos, siempre que el QR quede claramente visible y diferenciado. Un preimpreso (logotipo) no generado por el SIF puede mantenerse (FAQ [A6]). En A4 se pueden usar los espacios laterales para los datos del emisor o del destinatario (D2 anexo, ejemplos h-k).
- **Factura electrónica estructurada**: en lugar de la imagen, se incluye la URL del QR como **campo independiente** (art. 20.2 OM; art. 6.5.a ROF). Un PDF u otra imagen digital lleva el QR igual que el papel (FAQ [A6]).
- No se pone en albaranes, proformas ni borradores (FAQ [A6]).

### URL (D2 §5)

| Modalidad | Entorno | URL base |
|---|---|---|
| VERI*FACTU | Pruebas | `https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?` |
| VERI*FACTU | Producción | `https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR?` |
| NO VERI*FACTU | Pruebas | `https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu?` |
| NO VERI*FACTU | Producción | `https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQRNoVerifactu?` |

Plantilla: `…/ValidarQR?nif=XXXXXXXXY&numserie=YYYY...YYYY&fecha=DD-MM-AAAA&importe=NNNNNNNNN.DD`

### Parámetros (D2 §6): solo estos 4 y en este orden

| Parámetro | Formato | Longitud | Contenido |
|---|---|---|---|
| `nif` | Formato NIF | 9 | NIF del obligado a expedir |
| `numserie` | Texto; puede contener caracteres especiales (ASCII 32-126) | máx. 60 | Nº serie + nº factura |
| `fecha` | DD-MM-AAAA (guiones) | 10 | Fecha de expedición |
| `importe` | Numérico con «.» como separador decimal | máx. 12 dígitos enteros y 2 decimales | Importe total de la factura (= `ImporteTotal` del registro) |

- **Codificación**: «URL encoding» con **UTF-8** de los valores de los parámetros. Ejemplo oficial: `numserie=12345678&G33` se codifica `numserie=12345678%26G33`:
  `https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?nif=89890001K&numserie=12345678%26G33&fecha=01-01-2024&importe=241.4`
- «las cadenas de texto solo pueden contener caracteres ASCII con códigos del 32 al 126».
- Ejemplos oficiales de URL válidas (D2 §8): `https://www2.agenciatributaria.gob.es/wlpl/TIKE-CONT/ValidarQR?nif=89890001K&numserie=12345678-G33&fecha=01-09-2024&importe=241.4` (y el equivalente con `prewww2.aeat.es` y `ValidarQRNoVerifactu`).
- Importe con coma (`importe=7,2`): error 2005.
- 5.º parámetro opcional `formato=json`: **nunca** va en la URL del QR. Sirve para integraciones; la respuesta JSON trae `status`, `mensaje` y `respuesta.resultado`: `00` factura encontrada, `01` no encontrada (o anulada), `02` factura no verificable.
- Códigos de error del cotejo (D2 §10): 1001-1004 (falta nif, numserie, fecha o importe), 2001 NIF erróneo, 2002 numserie demasiado largo, 2003 numserie con caracteres no permitidos, 2004 fecha inválida, 2005 importe con formato incorrecto, 2006 importe demasiado largo, 3001 error técnico, 3002 máximo de intentos diarios superado.

---

## 4c. Servicio web de remisión

Fuentes: **SWeb «Sistemas Informáticos de Facturación — Remisión voluntaria / bajo requerimiento», v1.0.3 de 28/07/2025** [A8] (VERIFICADO, sede); art. 16 OM [B6] (VERIFICADO); WSDL y XSD [D5][D6] (COPIA); `errores.properties` [D7] (COPIA).

### WSDL y XSD (URLs literales de SWeb §7 y §8: VERIFICADO)

- Pruebas: `https://prewww2.aeat.es/static_files/common/internet/dep/aplicaciones/es/aeat/tikeV1.0/cont/ws/SistemaFacturacion.wsdl`
- Producción: `https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tikeV1.0/cont/ws/SistemaFacturacion.wsdl`
- XSD en el mismo directorio: `SuministroInformacion.xsd` (tipos comunes), `SuministroLR.xsd` (alta/anulación), `ConsultaLR.xsd`, `RespuestaSuministro.xsd`, `RespuestaConsultaLR.xsd`.

### Namespaces (COPIA del WSDL/XSD; coinciden con los ejemplos XML de SWeb §9, que son VERIFICADO)

Ojo: el *namespace* usa `…/aeat/tike/cont/ws/…` (sin `V1.0`), aunque los ficheros se descargan de `…/tikeV1.0/…`.

| Prefijo en SWeb | Namespace |
|---|---|
| `sum` / `sfLR` | `https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/SuministroLR.xsd` |
| `sum1` / `sf` | `https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/SuministroInformacion.xsd` |
| `sfR` | `https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/RespuestaSuministro.xsd` |
| `sfLRC` / `sfLRRC` | `…/ConsultaLR.xsd` / `…/RespuestaConsultaLR.xsd` |
| `xd` | `http://www.w3.org/2000/09/xmldsig#` |

La v0.4.2 de SWeb cambió el namespace de `<Cabecera>`, `<RegistroAlta>` y `<RegistroAnulacion>`. En el ejemplo oficial, `Cabecera` y `RegistroFactura` van con `sum:` y su contenido (`ObligadoEmision`, `RegistroAlta`…) con `sum1:`.

### Endpoints SOAP: COPIA del WSDL, **NO VERIFICADO contra el original**

Hay dos copias independientes del WSDL y son idénticas byte a byte (npm y GitHub invoiceninja). Otros proyectos citan las mismas URLs (Microsoft BCApps, fiskaltrust, invopop).

| Servicio / puerto WSDL | Entorno | Certificado | URL |
|---|---|---|---|
| `sfVerifactu` / `SistemaVerifactu` | Producción | normal | `https://www1.agenciatributaria.gob.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP` |
| `sfVerifactu` / `SistemaVerifactuSello` | Producción | **sello** | `https://www10.agenciatributaria.gob.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP` |
| `sfVerifactu` / `SistemaVerifactuPruebas` | Pruebas | normal | `https://prewww1.aeat.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP` |
| `sfVerifactu` / `SistemaVerifactuSelloPruebas` | Pruebas | **sello** | `https://prewww10.aeat.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP` |
| `sfRequerimiento` / `SistemaRequerimiento*` (NO VERI*FACTU, bajo requerimiento) | mismas 4 combinaciones | | `…/wlpl/TIKE-CONT/ws/SistemaFacturacion/RequerimientoSOAP` en `www1`, `www10`, `prewww1` y `prewww10` |

### Operaciones (WSDL, COPIA; los nombres coinciden con los elementos de los ejemplos SWeb)

- `RegFactuSistemaFacturacion`: entrada `sfLR:RegFactuSistemaFacturacion` (Cabecera + RegistroFactura 1..1000); salida `sfR:RespuestaRegFactuSistemaFacturacion`. Disponible en `sfVerifactu` y en `sfRequerimiento`.
- `ConsultaFactuSistemaFacturacion`: consulta de registros presentados, **solo en VERI*FACTU**. Máximo 10.000 registros por respuesta; si hay más, consulta paginada (SWeb §6.4).
- SOAP 1.1, `style="document"`, `use="literal"`, `soapAction=""`. Transporte HTTPS. UTF-8 (SWeb §4).

### Autenticación (SWeb §4.1 y §4.3; art. 5 OM: VERIFICADO)

- Autenticación **TLS cliente** con un «certificado electrónico cualificado reconocido».
- Puede remitir el propio obligado, un apoderado o un colaborador social (Resolución de 18/12/2024 [B11]; Convenio de colaboración social 017 para empresas de software [A6, A9 FAQ 16]). Se admite el certificado de un tercero que sea representante, apoderado o colaborador social (FAQ VERI*FACTU [A6]).
- Error 4112 (COPIA): «El titular del certificado debe ser Obligado Emisión, Colaborador Social, Apoderado o Sucesor.»
- `Cabecera/Representante`: para la colaboración social «transitiva» (obligado → asesor → plataforma) (SWeb §2.4).

### Tamaño de envío y composición

- «El número máximo de registros por envío es de **1.000**» (SWeb §3). `RegistroFactura` va de 1 a 1.000 (anexo 2.2 OM).
- Se pueden mezclar altas y anulaciones, pero cada una en **su propia** ocurrencia de `RegistroFactura` (D3 §3.1.2).
- «Todos los registros de facturación que se envíen en un mismo lote deben corresponder al mismo obligado tributario» (FAQ VERI*FACTU [A6]).
- Clave única de un registro: `NIF + NumSerieFactura + FechaExpedicionFactura` (SWeb §3).

### Control de flujo (art. 16.2 OM, literal; repetido en SWeb §6.4.4.1)

> «Los sistemas informáticos «VERI*FACTU» deberán implementar un mecanismo de control de flujo basado en el tiempo de espera entre envíos, el cual tomará inicialmente el valor de **60 segundos**, y en el número máximo de registros admitidos en cada envío. […] c) Para poder realizar el siguiente envío, el sistema informático deberá esperar a que transcurran «t» segundos desde el anterior envío **o** deberá esperar a tener acumulados un número de registros de facturación igual al límite establecido en el diseño de registro para cada envío, **la circunstancia que ocurra primero**. d) […] En la respuesta puede recibir una nueva actualización del valor del parámetro «t».»

En la respuesta llega como `<TiempoEsperaEnvio>` (en segundos). Regla para el código: `puedeEnviar = (ahora - ultimoEnvio >= t) || (pendientes >= 1000)`. Hay que actualizar `t` con cada respuesta.

Aun así, el envío debe ser inmediato y no se puede agrupar al final del día: «el envío de las facturas simplificadas debe producirse […] de forma inmediata, respetando los tiempos técnicos exigidos por el control de flujo» (FAQ VERI*FACTU [A6]).

### Incidencias y reintentos (art. 16.4 OM: VERIFICADO)

- Remitir «en cuanto sea posible, respetando el orden temporal de generación», marcando `Cabecera/RemisionVoluntaria/Incidencia = "S"`.
- «reintentar periódicamente, **al menos una vez cada hora**».
- Mostrar un aviso con el **número de registros pendientes** mientras quede alguno.
- La facturación **no** se interrumpe (FAQ VERI*FACTU [A6]).
- Si no llega respuesta: «se deben remitir de nuevo los registros de facturación hasta obtenerla» (FAQ [A6]). Un `Fault` con `soapenv:Server` o sin respuesta → reenviar. Un `soapenv:Client` → corregir el mensaje (SWeb §5.1). Un reenvío de un registro que ya está en la AEAT devuelve el error 3000 «duplicado» con el nodo `RegistroDuplicado` (ver más abajo), así que hay que tratarlo de forma idempotente.

### Respuesta (SWeb §6.4.4: VERIFICADO; nombres XML según el XSD, COPIA)

```
RespuestaRegFactuSistemaFacturacion
├─ CSV                      Alfanumérico(16). «IMPORTANTE: El CSV debe ser almacenado por el SIF en el momento de alta, no podrá ser recuperado a través de consultas posteriores.» Solo se devuelve si al menos un registro es aceptado (art. 16.5 OM).
├─ DatosPresentacion
│   ├─ NIFPresentador
│   └─ TimestampPresentacion  ISO 8601 con huso de los servidores AEAT (p. ej. 2024-01-01T19:20:30+01:00)
├─ Cabecera                 (eco de la enviada)
├─ TiempoEsperaEnvio        segundos (numérico)
├─ EstadoEnvio              Correcto | ParcialmenteCorrecto | Incorrecto   (L18)
└─ RespuestaLinea (0..1000)
    ├─ IDFactura (IDEmisorFactura, NumSerieFactura, FechaExpedicionFactura)
    ├─ Operacion: TipoOperacion (Alta | Anulacion, L22), Subsanacion, RechazoPrevio, SinRegistroPrevio
    ├─ RefExterna
    ├─ EstadoRegistro        Correcto | AceptadoConErrores | Incorrecto   (L19)
    ├─ CodigoErrorRegistro   (entero)
    ├─ DescripcionErrorRegistro
    └─ RegistroDuplicado     (solo si se rechaza por duplicado)
        ├─ IdPeticionRegistroDuplicado  Alfanumérico(20)
        ├─ EstadoRegistroDuplicado      Correcta | AceptadaConErrores | Anulada  (L21)
        ├─ CodigoErrorRegistro
        └─ DescripcionErrorRegistro
```

- `EstadoEnvio`: `Correcto` si todos los registros son correctos; `ParcialmenteCorrecto` si algún registro es Incorrecto o AceptadoConErrores; `Incorrecto` si todos son Incorrectos (SWeb L18).
- Si falla la estructura XML o la cabecera, la respuesta es un `SOAPFault` y se rechaza **todo** el envío (p. ej. `Codigo[4104]`).

### Códigos de error más relevantes ([D7] COPIA, NO VERIFICADO; las categorías coinciden con D3 §4.4)

| Código | Texto (resumido) | Efecto |
|---|---|---|
| 4102 | El XML no cumple el esquema (falta un campo obligatorio) | Rechazo del envío completo |
| 4104 / 4107 / 4109 | NIF de ObligadoEmision no identificado / NIF no censado / formato de NIF incorrecto | Rechazo del envío completo |
| 4112 | Titular del certificado no autorizado | Rechazo del envío completo |
| 4113 / 4114 | Superado el límite de registros por bloque o de facturas | Rechazo del envío completo |
| 4119 | Caracteres no UTF-8 | Rechazo del envío completo |
| 4120 | `FechaFinVeriFactu` incorrecta (debe ser 31-12-20XX del año actual o del anterior) | Rechazo del envío completo |
| 4127 | Remisión voluntaria en un sistema no VERI*FACTU | Rechazo del envío completo |
| 1104 / 1130 | `NumSerieFactura` incorrecto / con caracteres no permitidos | Rechazo del registro |
| 1108 | El NIF de `IDEmisorFactura` no coincide con el de `ObligadoEmision` | Rechazo del registro |
| 1112 | `FechaExpedicionFactura` posterior a la fecha actual | Rechazo del registro |
| 1142 | `CuotaRepercutida` incorrecta para la base y el tipo (fuera de ±10 €) | Rechazo del registro |
| 1150 | F2 con base + cuota > 3.000 € | Rechazo del registro |
| 1152 | Fecha de expedición anterior al 28/10/2024 | Rechazo del registro |
| 1189 / 1190 | Falta `Destinatarios` (F1, F3, R1-R4) / sobra `Destinatarios` (F2, R5) | Rechazo del registro |
| 1180 / 1269 | Error en el bloque `Encadenamiento` / `RegistroAnterior` mal informado | Rechazo del registro |
| 1244 / 1268 | `FechaHoraHusoGenRegistro` con formato o longitud incorrectos | Rechazo del registro |
| 1262 / 1291 / 1292 | Longitud de la huella / hash anterior o propio no alfanumérico | Rechazo del registro |
| **3000** | **Registro de facturación duplicado** | Rechazo del registro (se devuelve `RegistroDuplicado`) |
| **3001** | El registro de facturación ya ha sido dado de baja | Rechazo del registro |
| **3002** | No existe el registro de facturación | Rechazo del registro |
| **3003** | El presentador no tiene permisos para actualizar este registro | Rechazo del registro |
| **3004** | No se puede modificar la factura: se dio de alta vía formulario | Rechazo del registro |
| **2000** | Huella mal calculada | **Aceptado con errores** (subsanar) |
| 2001 | NIF de Destinatarios no censado | Aceptado con errores |
| 2002 / 2003 | Longitud o contenido de la huella del registro anterior | Aceptado con errores |
| 2004 | `FechaHoraHusoGenRegistro` posterior a la hora de la AEAT, fuera del margen | Aceptado con errores (D3: «Se excepciona este error de la necesidad de ser subsanado») |
| 2005 | `ImporteTotal` no cuadra (±10 €) | Aceptado con errores |
| 2006 | `CuotaTotal` no cuadra (±10 €) | Aceptado con errores |
| 2007 | `PrimerRegistro = S` cuando ya existen registros para ese SIF y NIF | Aceptado con errores |
| 2008 | La huella anterior es igual a la huella actual | Aceptado con errores |

Categorías del fichero: 4xxx/35xx = «rechazo del envío completo»; 1xxx y 3000-3004 = «rechazo de la factura (o de la petición completa si el error se produce en la cabecera)»; 2xxx = «aceptación del registro […] (posteriormente deben ser subsanados)».

### Otras reglas de formato (SWeb §6.7-6.9: VERIFICADO)

- La AEAT recorta los espacios al inicio y al final de los textos y almacena el valor recortado.
- Números: sin ceros a la izquierda (`1`, no `01`); punto decimal; los ceros a la derecha tras el punto son opcionales. En las fechas sí van ceros a la izquierda (`02-07-2014`).
- Escapar `&` como `&amp;` y `<` como `&lt;`.

### Ejemplo oficial de mensaje de alta (SWeb §9.1.1.1, VERIFICADO, abreviado)

```xml
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:sum="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/SuministroLR.xsd"
 xmlns:sum1="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tike/cont/ws/SuministroInformacion.xsd"
 xmlns:xd="http://www.w3.org/2000/09/xmldsig#">
 <soapenv:Header/>
 <soapenv:Body>
  <sum:RegFactuSistemaFacturacion>
   <sum:Cabecera>
    <sum1:ObligadoEmision><sum1:NombreRazon>XXXXX</sum1:NombreRazon><sum1:NIF>AAAA</sum1:NIF></sum1:ObligadoEmision>
   </sum:Cabecera>
   <sum:RegistroFactura>
    <sum1:RegistroAlta>
     <sum1:IDVersion>1.0</sum1:IDVersion>
     <sum1:IDFactura><sum1:IDEmisorFactura>AAAA</sum1:IDEmisorFactura><sum1:NumSerieFactura>12345</sum1:NumSerieFactura><sum1:FechaExpedicionFactura>13-09-2024</sum1:FechaExpedicionFactura></sum1:IDFactura>
     <sum1:NombreRazonEmisor>XXXXX</sum1:NombreRazonEmisor>
     <sum1:TipoFactura>F1</sum1:TipoFactura>
     <sum1:DescripcionOperacion>Descripc</sum1:DescripcionOperacion>
     <sum1:Destinatarios><sum1:IDDestinatario><sum1:NombreRazon>YYYY</sum1:NombreRazon><sum1:NIF>BBBB</sum1:NIF></sum1:IDDestinatario></sum1:Destinatarios>
     <sum1:Desglose>
      <sum1:DetalleDesglose><sum1:ClaveRegimen>01</sum1:ClaveRegimen><sum1:CalificacionOperacion>S1</sum1:CalificacionOperacion><sum1:TipoImpositivo>4</sum1:TipoImpositivo><sum1:BaseImponibleOimporteNoSujeto>10</sum1:BaseImponibleOimporteNoSujeto><sum1:CuotaRepercutida>0.4</sum1:CuotaRepercutida></sum1:DetalleDesglose>
      <sum1:DetalleDesglose><sum1:ClaveRegimen>01</sum1:ClaveRegimen><sum1:CalificacionOperacion>S1</sum1:CalificacionOperacion><sum1:TipoImpositivo>21</sum1:TipoImpositivo><sum1:BaseImponibleOimporteNoSujeto>100</sum1:BaseImponibleOimporteNoSujeto><sum1:CuotaRepercutida>21</sum1:CuotaRepercutida></sum1:DetalleDesglose>
     </sum1:Desglose>
     <sum1:CuotaTotal>21.4</sum1:CuotaTotal>
     <sum1:ImporteTotal>131.4</sum1:ImporteTotal>
     <sum1:Encadenamiento><sum1:RegistroAnterior><sum1:IDEmisorFactura>AAAA</sum1:IDEmisorFactura><sum1:NumSerieFactura>44</sum1:NumSerieFactura><sum1:FechaExpedicionFactura>13-09-2024</sum1:FechaExpedicionFactura><sum1:Huella>HuellaRegistroAnterior</sum1:Huella></sum1:RegistroAnterior></sum1:Encadenamiento>
     <sum1:SistemaInformatico><sum1:NombreRazon>SSSS</sum1:NombreRazon><sum1:NIF>NNNN</sum1:NIF><sum1:NombreSistemaInformatico>NombreSistemaInformatico</sum1:NombreSistemaInformatico><sum1:IdSistemaInformatico>77</sum1:IdSistemaInformatico><sum1:Version>1.0.03</sum1:Version><sum1:NumeroInstalacion>383</sum1:NumeroInstalacion><sum1:TipoUsoPosibleSoloVerifactu>N</sum1:TipoUsoPosibleSoloVerifactu><sum1:TipoUsoPosibleMultiOT>S</sum1:TipoUsoPosibleMultiOT><sum1:IndicadorMultiplesOT>S</sum1:IndicadorMultiplesOT></sum1:SistemaInformatico>
     <sum1:FechaHoraHusoGenRegistro>2024-09-13T19:20:30+01:00</sum1:FechaHoraHusoGenRegistro>
     <sum1:TipoHuella>01</sum1:TipoHuella>
     <sum1:Huella>Huella</sum1:Huella>
    </sum1:RegistroAlta>
   </sum:RegistroFactura>
  </sum:RegFactuSistemaFacturacion>
 </soapenv:Body>
</soapenv:Envelope>
```

Orden de los elementos de `RegistroAlta` según el XSD (COPIA; coincide con la tabla del anexo 2.3 de la OM, VERIFICADO):
`IDVersion, IDFactura, RefExterna?, NombreRazonEmisor, Subsanacion?, RechazoPrevio?, TipoFactura, TipoRectificativa?, FacturasRectificadas?, FacturasSustituidas?, ImporteRectificacion?, FechaOperacion?, DescripcionOperacion, FacturaSimplificadaArt7273?, FacturaSinIdentifDestinatarioArt61d?, Macrodato?, EmitidaPorTerceroODestinatario?, Tercero?, Destinatarios?, Cupon?, Desglose, CuotaTotal, ImporteTotal, Encadenamiento, SistemaInformatico, FechaHoraHusoGenRegistro, NumRegistroAcuerdoFacturacion?, IdAcuerdoSistemaInformatico?, TipoHuella, Huella, ds:Signature?`

`RegistroAnulacion`: `IDVersion, IDFactura(IDEmisorFacturaAnulada, NumSerieFacturaAnulada, FechaExpedicionFacturaAnulada), RefExterna?, SinRegistroPrevio?, RechazoPrevio?, GeneradoPor?, Generador?, Encadenamiento, SistemaInformatico, FechaHoraHusoGenRegistro, TipoHuella, Huella, ds:Signature?`

`Cabecera`: `ObligadoEmision(NombreRazon, NIF), Representante?, RemisionVoluntaria?(FechaFinVeriFactu?, Incidencia?), RemisionRequerimiento?(RefRequerimiento, FinRequerimiento?)`

### Formatos de campo (anexo OM [B7]: VERIFICADO)

| Campo | Formato |
|---|---|
| `IDVersion` | Alfanumérico(3), L15 = `1.0` |
| NIF (cualquier campo) | FormatoNIF(9) |
| `NumSerieFactura` | Alfanumérico(60) |
| Fechas (`FechaExpedicionFactura`, `FechaOperacion`…) | `dd-mm-yyyy` |
| `FechaHoraHusoGenRegistro` | DateTime ISO 8601 `YYYY-MM-DDThh:mm:ssTZD`, p. ej. `2024-01-01T19:20:30+01:00`. «El huso horario es el que está usando el sistema informático de facturación en el momento de generación» |
| Importes (`BaseImponibleOimporteNoSujeto`, `CuotaRepercutida`, `CuotaTotal`, `ImporteTotal`…) | Decimal(12,2). XSD: `(\+|-)?\d{1,12}(\.\d{0,2})?` (COPIA) |
| `TipoImpositivo`, `TipoRecargoEquivalencia` | Decimal(3,2) |
| `NombreRazon*` | Alfanumérico(120) |
| `DescripcionOperacion` | Alfanumérico(500) |
| `RefExterna` | Alfanumérico(60) |
| `Desglose/DetalleDesglose` | de **1 a 12** líneas |
| `Destinatarios/IDDestinatario` | de 1 a 1000 |
| `Huella` | Alfanumérico(64); `TipoHuella` Alfanumérico(2) L12 = `01` |
| `RefRequerimiento` | Alfanumérico(18) |
| IDOtro: `CodigoPais` (ISO 3166-1 alfa-2), `IDType` (L7: 02 NIF-IVA, 03 Pasaporte, 04 Doc. oficial del país de residencia, 05 Certificado de residencia, 06 Otro documento probatorio, 07 No censado), `ID` Alfanumérico(20) | |

---

## 4d. Validaciones de negocio clave

Fuente: **«Validaciones — Sistemas Informáticos de Facturación y Sistemas VERI*FACTU», v1.1.5 de 02/10/2025** [D3] (COPIA; puede haber una versión posterior: NO VERIFICADO), salvo que se indique otra cosa.

### Tipos de validación (D3 §3.1)

1. **Estructurales** (XSD): rechazo completo del mensaje.
2. **Sintácticas**: en la cabecera, rechazo completo; en un registro, rechazo de ese registro.
3. **De negocio**: rechazo del registro (o aviso si el error es admisible).

### Cabecera (D3 §3.1.1)

- El NIF de `ObligadoEmision` y el de `Representante` deben estar identificados en la AEAT.
- `FechaFinVeriFactu` y `Incidencia`: solo en sistemas VERI*FACTU. `FechaFinVeriFactu` con formato `31-12-20XX`, del año actual de la AEAT o del anterior.
- `RefRequerimiento`: solo en NO VERI*FACTU, obligatorio allí, y debe existir en la AEAT.

### RegistroAlta (D3 §3.1.3)

- **IDFactura**: el NIF de `IDEmisorFactura` debe ser el de `ObligadoEmision`. `FechaExpedicionFactura` no puede ser posterior a hoy ni anterior al **28/10/2024**. Con IVA o IGIC solo puede ser anterior a `FechaOperacion` si `ClaveRegimen` es 14 o 15.
- **NumSerieFactura**: «solo puede contener caracteres ASCII del 32 a 126 (caracteres imprimibles), no permitiéndose» `"` (34), `'` (39), `<` (60), `>` (62) ni `=` (61). Longitud máxima 60. Errores en COPIA D7: 1130 «El campo NumSerieFactura contiene caracteres no permitidos» y 1287 «El valor del campo %s contiene carácteres no validos (<, >, ", ', =)».
- **Subsanacion / RechazoPrevio**: `RechazoPrevio = "X"` solo si `Subsanacion = "S"`; `RechazoPrevio = "S"` no se admite si `Subsanacion` no está informado o vale "N". Ver la tabla de operativas al final de este apartado.
- **TipoRectificativa** (L3: `S` sustitución, `I` diferencias): obligatorio solo y siempre en R1-R5. **FacturasRectificadas** (opcional): solo en R1-R5. **FacturasSustituidas** (opcional): solo en F3. **ImporteRectificacion** (`BaseRectificada`, `CuotaRectificada`, `CuotaRecargoRectificado`): obligatorio solo si `TipoRectificativa = S`.
- **FechaOperacion**: no puede ser anterior a hoy menos 20 años ni posterior al año siguiente al actual. Solo puede ser posterior a hoy con ClaveRegimen 14 o 15 (IVA o IGIC).
- `FacturaSimplificadaArt7273 = S` solo en F1, F3 o R1-R4. `FacturaSinIdentifDestinatarioArt61d = S` solo en F2 o R5.
- `Macrodato = S` obligatorio si |ImporteTotal| ≥ 100.000.000,00.
- `EmitidaPorTerceroODestinatario`: con `T` el bloque `Tercero` es obligatorio; con `D` lo es `Destinatarios`.
- **Destinatarios** (COPIA D7: 1189/1190): obligatorio en F1, F3 y R1-R4; prohibido en F2 y R5. Si se identifica por NIF, debe estar identificado. IDOtro con `CodigoPais = ES` solo admite IDType 03 o 07. IDType 02 (NIF-IVA) solo en F1, F3 y R1-R4.
- `Cupon = S` solo en R1 o R5.
- **Desglose / DetalleDesglose**:
  - `Impuesto` (L1; vacío equivale a 01 IVA). `ClaveRegimen` obligatoria con IVA o IGIC (L8A/L8B).
  - `CalificacionOperacion` (S1, S2, N1, N2) y `OperacionExenta` (E1-E6; con IGIC también E7 y E8) son **excluyentes**, y debe haber una de las dos (COPIA D7 1195/1196).
  - **TipoImpositivo** (IVA, S1): solo **0; 2; 4; 5; 7,5; 10; 21**. El 5 solo para operaciones del 01/07/2022 al 30/09/2024; el 2 y el 7,5 solo del 01/10/2024 al 31/12/2024.
  - **TipoRecargoEquivalencia**: 0; 0,26; 0,5; 0,62; 1; 1,4; 1,75; 5,2. Con 21 → 5,2 o 1,75; con 10 → 1,4; con 4 → 0,5; con 5 → 0,5 (hasta 31/12/2022) o 0,62 (01/01/2023-30/09/2024); con 2 → 0,26; con 7,5 → 1.
  - **S2** (inversión del sujeto pasivo): TipoFactura F1, F3 o R1-R4; `TipoImpositivo = 0` y `CuotaRepercutida = 0`, que deben informarse (no pueden ir vacíos).
  - **N1/N2** (IVA): sin TipoImpositivo, CuotaRepercutida ni recargo.
  - **Exenta**: sin TipoImpositivo, CuotaRepercutida ni recargo. Con ClaveRegimen 01 no se admite E2 ni E3. E5 (IVA) solo con `IDOtro` en el destinatario.
  - Claves de régimen: 02 → solo OperacionExenta; 03 → si hay calificación, solo S1; 04 → S2 o exenta; 06 → no F2, F3 ni R5, y `BaseImponibleACoste` obligatoria; 07 → no S2, N1, N2, E2, E3, E4 ni E5; 08 → N2 obligatoria; 10 → N1, F1 y destinatarios con NIF; 11 → tipo 21; 14 → FechaOperacion obligatoria y posterior a la de expedición, destinatarios con NIF que empiece por P, Q, S o V, y TipoFactura F1 o R1-R4.
  - `BaseImponibleACoste` solo con ClaveRegimen 06 o Impuesto 02/05.
- **CuotaRepercutida** (§15.7): solo distinta de 0 con S1. Con S1 se valida, **salvo con TipoRectificativa = I o en R2 y R3**, que tenga el mismo signo que la base y que `CuotaRepercutida = Base × TipoImpositivo / 100 ± 10,00 €`. **Si no se cumple, el registro se rechaza** (error 1142 en COPIA D7, de la categoría de rechazo). La v1.0.2 de D3 unificó la tolerancia en «únicamente margen +/- 10 euros».
- **F2** (§15.8): Σ(Base + Cuota) ≤ 3.000,00 €, con un margen de +10 €. No se aplica si hay `NumRegistroAcuerdoFacturacion` o `FacturaSinIdentifDestinatarioArt61d = S`.
- **CuotaTotal** (§16): debe ser Σ(CuotaRepercutida + CuotaRecargoEquivalencia) ± 10,00 €. Si no, **aviso sin rechazo**.
- **ImporteTotal** (§17): debe ser Σ(BaseImponibleOimporteNoSujeto + CuotaRepercutida + CuotaRecargoEquivalencia) ± 10,00 €. Si no, aviso sin rechazo. No se valida con ClaveRegimen 03, 05, 06, 08 o 09. Concepto: los suplidos, retenciones de IRPF/IS y recargo financiero **no** forman parte del ImporteTotal; la FAQ de desarrolladores recomienda mostrar por separado «Importe total factura» (el del QR) y «Total a pagar» [A9 FAQ 20; A6 alta].
- **Huella anterior** (§18): 64 caracteres hexadecimales en mayúsculas; si no, aviso.
- **FechaHoraHusoGenRegistro** (§20): ≤ hora del sistema de la AEAT, con un margen cuyo valor **no se publica: NO VERIFICADO**. Si se supera, aviso sin rechazo.
- **Huella** (§23): si no cumple las especificaciones, aviso sin rechazo.

### RegistroAnulacion (D3 §3.1.4)

- El NIF de `IDEmisorFacturaAnulada` debe ser el de `ObligadoEmision`.
- `GeneradoPor` (L16: `E` Expedidor, `D` Destinatario, `T` Tercero) va siempre junto con el bloque `Generador`. Si no se informa, se entiende que el registro lo generó el emisor material de la factura anulada. Con `E` es obligatorio el NIF en `Generador`. Con `T` no se admite IDType 07. Con `D` e IDOtro con `CodigoPais = ES`, solo IDType 03 o 07. El NIF del `Generador` debe ser distinto del de `ObligadoEmision`.
- Huella anterior, `FechaHoraHusoGenRegistro` y huella: igual que en el alta.

### SistemaInformatico (D3 §3.1.5)

- `NIF` o `IDOtro` del productor, uno de los dos y nunca ambos. No se admite IDType 07. Con `CodigoPais = ES` el IDType debe ser 03.
- **`IdSistemaInformatico`: siempre 2 posiciones, cada una una letra mayúscula (excepto la Ñ) o un dígito.**
- `NombreSistemaInformatico`, `TipoUsoPosibleSoloVerifactu` y `TipoUsoPosibleMultiOT` son obligatorios y deben tener contenido.

### Operativas admisibles (D3 anexo 5; coinciden con SWeb §9-10 [A8], VERIFICADO)

**Altas**

| Operación | `Subsanacion` | `RechazoPrevio` | Modalidad | Condición en la AEAT |
|---|---|---|---|---|
| Alta inicial | N o ausente | N o ausente | VF y no VF | No existe registro → OK. Si existe alta o anulación → ERROR (3000) |
| Alta por rechazo (el alta inicial fue rechazada y no existe en la AEAT) | S | X | VF | No existe registro → OK. Si existe → ERROR |
| Alta de subsanación (el registro existe, aceptado o aceptado con errores) | S | N o ausente | VF y no VF | Si existe alta → OK: sustituye completamente. Si existe anulación → OK: reactiva el registro. Si no existe → ERROR |
| Alta por rechazo de subsanación | S | S | VF | El registro existe en la AEAT y la subsanación anterior fue rechazada |
| Alta de subsanación sin registro previo (p. ej. venía de NO VF) | S | X | VF (no VF opcional) | No existe en la AEAT |
| Alta por rechazo de subsanación sin registro previo | S | X | VF | No existe en la AEAT |
| (No admitido) | N | S o X | — | — (L17 OM) |

**Anulaciones**

| Operación | `SinRegistroPrevio` | `RechazoPrevio` | Condición en la AEAT |
|---|---|---|---|
| Anulación | N o ausente | N o ausente | Debe existir un alta (→ la anula) o una anulación (→ deja los nuevos datos). Si no existe → ERROR |
| Anulación por rechazo | N o ausente | S | El registro existe y la anulación previa fue rechazada |
| Anulación sin registro previo | S | N o ausente | No debe existir nada en la AEAT. Se crea un registro de anulación |
| Anulación por rechazo sin registro previo | S | S | No existe en la AEAT y el intento previo fue rechazado |

**Reglas comunes** (SWeb §9):

- Cada subsanación o anulación es un **registro nuevo**, con la **misma clave** (NIF + NumSerie + Fecha) que el original, y se encadena con el registro inmediatamente anterior de la cadena.
- El registro original nunca se modifica.
- Una factura cotejada por QR cuyo registro está anulado responde «Factura no encontrada» (D2 v0.4.6).

---

## 4e. Bloque `SistemaInformatico` y `FechaHoraHusoGenRegistro`

Fuente: OM, anexo 2.6 [B7] (VERIFICADO, literal resumido); FAQ desarrolladores n.º 4 [A9] (VERIFICADO).

| Campo | Formato | Significado (OM) |
|---|---|---|
| `NombreRazon` | A(120) | Nombre o razón social del **productor**. Si hay varios productores, el del **componente principal** (art. 1.2.c OM) |
| `NIF` / `IDOtro`(`CodigoPais`, `IDType`, `ID`) | NIF(9) / A(2), A(2) L7, A(20) | Identificación del productor |
| `NombreSistemaInformatico` | A(30) | «Nombre dado por la persona o entidad productora a su sistema informático de facturación». Obligatorio en alta y anulación |
| `IdSistemaInformatico` | A(2) | «Código identificativo dado por la persona o entidad productora […] Deberá distinguirlo de otros posibles SIF distintos que produzca esta misma persona o entidad productora». Valores: 2 caracteres [A-Z sin Ñ, 0-9] (D3) |
| `Version` | A(50) | «Identificación de la versión del sistema informático de facturación (SIF) que se ejecuta» |
| `NumeroInstalacion` | A(100) | «Número de instalación del SIF utilizado. Deberá distinguirlo de otros posibles SIF utilizados para realizar la facturación del obligado […] pasadas, presentes o futuras […] incluso aunque en dichas instalaciones se emplee el mismo SIF de un productor». La FAQ desarrolladores n.º 4 recomienda un timestamp de instalación o un autonumérico que nunca se repita. **Si se formatea y se reinstala, el número debe ser nuevo**. En un SIF multi-OT, cada facturación (de otro OT o de un centro independiente) lleva su propio número |
| `TipoUsoPosibleSoloVerifactu` | A(1) L4 S/N | `S` si el SIF «solo puede funcionar exclusivamente como VERI*FACTU»; `N` si puede funcionar también como NO VERI*FACTU |
| `TipoUsoPosibleMultiOT` | A(1) L4 | `S` si el SIF **permite** llevar la facturación de varios OT; `N` si solo uno |
| `IndicadorMultiplesOT` | A(1) L4 | `S` si **en el momento de generar el registro** el SIF contiene o gestiona más de un OT, sea cual sea su estado (alta, baja…). Debe calcularlo el propio sistema: no puede introducirlo ni cambiarlo el usuario. En SaaS se calcula por usuario del SaaS (FAQ desarrolladores n.º 4) |

- Un SIF se identifica de forma universal por **NIF del OEF + IdSistemaInformatico (junto con el NIF del productor) + NumeroInstalacion**. Un cambio de `Version` no crea un SIF distinto (FAQ desarrolladores n.º 4).
- `FechaHoraHusoGenRegistro`: ISO 8601 `YYYY-MM-DDThh:mm:ssTZD`, p. ej. `2024-01-01T19:20:30+01:00`, con el huso del SIF al generar (OM, anexo 2.3 y 2.4). Debe ser la fecha y hora exactas de generación según el territorio desde el que se expide (art. 7.e y 7.g OM).
- La fecha de expedición debe coincidir con la de generación del registro y, en general, con la del envío. Se admiten desfases puntuales, por ejemplo por incidencias o por el cambio de horario (FAQ VERI*FACTU [A6]).
- El art. 9 RRSIF exige generar el registro «de forma simultánea o inmediatamente anterior a la expedición de cada factura».

---

## 4f. Alcance de la cadena

- Art. 7.c OM (VERIFICADO, literal): «Para un determinado obligado tributario, cada sistema informático producirá una **única cadena** de registros de facturación, es decir, todos los registros de facturación de un mismo obligado tributario generados por un mismo sistema informático deberán formar parte de la misma cadena.»
- Art. 7.d OM: «La cadena de registros de facturación generada contendrá tanto los registros de facturación de alta como los registros de facturación de anulación.»
- Art. 7.a OM: el registro anterior es el «inmediatamente anterior **por orden cronológico de fecha de generación**».
- Art. 7.b OM: el primer registro «desde su instalación o puesta en marcha inicial» se marca como primero (`PrimerRegistro = S`).
- FAQ trazabilidad [A6] (VERIFICADO): «debe existir una **única cadena de RF por cada par de valores distintos de (SIF; OT)**». El cambio de año o de serie «no afecta en nada a la trazabilidad»: el primer registro del año nuevo se encadena con el último del anterior, «independientemente del número de factura (o de serie)». No hay cadenas separadas para altas y anulaciones.
- Art. 2.b OM y FAQ [A6]: un SIF multi-OT debe llevar **cadenas independientes por OT**.
- FAQ VERI*FACTU [A6]: los módulos de un ERP centralizado comparten una única cadena, sea cual sea la serie. Los TPV o módulos que funcionan de forma aislada son SIF distintos, cada uno con su propia cadena.
- En el registro, `RegistroAnterior/IDEmisorFactura` puede no coincidir con el actual en casos puntuales, como un cambio de NIF por fusión (OM, anexo 2.3).
