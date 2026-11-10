# Pendientes antes de usar la aplicación en producción

La aplicación **bloquea** la producción hasta que se cumplen los requisitos que puede comprobar (pantalla
«Producción», `GET /api/empresas/{id}/produccion`). Además, quedan estas tareas:

## Bloqueantes

1. **Modelos reales de la empresa**: se recibió un PDF real (factura firmada; se usa solo como prueba privada,
   nunca se emite ni se remite: `tests/privado/`, excluido del repositorio) y la extracción lo lee completo. Antes de
   producción conviene probar con una muestra de los PDF **preparatorios** reales que vaya a usar la empresa (sin
   emitirlos), por si su programa de origen genera algún diseño no cubierto.
2. **Productor del sistema**: designar a la persona o entidad responsable, completar sus datos en la aplicación y en la declaración responsable, y que esta la **suscriba** (no puede hacerlo una herramienta de IA ni firmarse en su nombre).
3. **Esquemas oficiales**: contrastar los XSD/WSDL/catálogo de errores con la AEAT (`python scripts/verificar_esquemas.py`) desde una red con acceso a la sede. Se obtuvieron de una réplica pública con sumas SHA-256 declaradas (ver `app/verifactu/esquemas/PROCEDENCIA.md`).
4. **Certificado y legitimación**: certificado electrónico de la empresa (sello o persona jurídica) o de su representante, o de un tercero con apoderamiento o colaboración social en la AEAT. Se carga en la aplicación (formulario seguro, nunca en el chat), que comprueba titularidad o representación. El apoderamiento solo lo verifica la AEAT al remitir (error 4112).
5. **Prueba real en el entorno de pruebas de la AEAT** (`prewww1.aeat.es`): no se ha podido ejecutar porque (a) desde el entorno de desarrollo la AEAT corta las conexiones TLS y (b) no se dispone de certificado. Pasos: `VERIFACTU_PERMITIR_PRUEBAS_AEAT=1`, certificado de pruebas, serie de pruebas (p. ej. `PRB-{n}`), datos sintéticos, y verificar: alta aceptada, duplicado (3000), subsanación, anulación, rechazo por datos (p. ej. tipo no permitido) y control de flujo con `TiempoEsperaEnvio` real. Comprobar también el cotejo del QR en `prewww2.aeat.es`.
6. **Revisión del calendario**: el RDL 15/2025 fija el 01/01/2027 (contribuyentes del IS) y el 01/07/2027 (resto). La «Comunicación del Ministerio de Hacienda de 05/10/2026» anuncia una posible ampliación que **no** está publicada como norma: revisar el BOE antes de fijar fechas.

## Recomendados

7. Despliegue con HTTPS y acceso restringido (VPN o lista de IP), `VERIFACTU_COOKIE_SEGURA=1`, copias diarias externas, diario en otro volumen y clave maestra custodiada aparte.
8. Sincronización horaria (NTP) del servidor: `FechaHoraHusoGenRegistro` debe ser la hora real de generación.
9. Procedimiento guiado de consulta a la AEAT (`ConsultaFactuSistemaFacturacion`) para conciliar estados y recuperar información tras un desastre (el mensaje ya se genera y valida).
10. Representante en la cabecera (`Cabecera/Representante`) cuando remita un tercero distinto del obligado.
11. Política de purga de borradores descartados (tarea programada).
12. Casos no soportados (bloqueados hoy): exentas, inversión del sujeto pasivo, no sujetas, recargo de equivalencia, suplidos, simplificadas (F2), sustitutivas (F3), rectificativas por sustitución, IGIC/IPSI, destinatarios extranjeros, otras divisas, duplicados del art. 14 ROF.
13. Escaneos de mala calidad: el OCR local acierta ~90 % de los campos en escaneos simulados; los valores dudosos se marcan para revisión. Opcional: IA de extracción solo como propuesta que el usuario confirma, con consentimiento expreso y valorando el envío de datos a terceros.
14. Limitaciones conocidas de la recuperación: el diario se lee completo en cada arranque (adecuado para el volumen de una pyme; para volúmenes altos, índice o puntos de control en el manifiesto de la copia); certificados, autorizaciones de producción y series confirmadas sin uso posteriores a la última copia deben volver a introducirse tras restaurar; una línea del diario ilegible exige revisión manual y `forzar=True` para restaurar.
15. Despliegue de varias instancias: el diseño asume un único proceso de aplicación (SQLite y un remitente por empresa y entorno). Para varias instancias, migrar a PostgreSQL y renovar el bloqueo del remitente.
16. **Límites conocidos del extractor** (cuarta verificación adversarial, 10/11/2026). Se corrigieron los 23 hallazgos
    confirmados y lo que salió en dos rondas de reparación, pero los verificadores siguieron construyendo variantes. Las
    abiertas son maquetas hechas a propósito; ninguna inventa datos ni permite expedir sin que el usuario revise y
    confirme el borrador, pero algunas proponen un valor erróneo **sin marcarlo en ámbar**. Por eso la revisión del
    borrador frente al original sigue siendo imprescindible.
    - **Partes**: nombre comercial solo en el membrete (razón social y CIF solo en el pie) tomado como destinatario; un
      tercero calificado antes del rótulo del NIF («Cesionario CIF: …», «Pago a favor de: CIF …») bajo el bloque del
      cliente; NIF del cliente con una letra O o I en lugar de una cifra junto a un tercero presentado como «Obra
      promovida por»; dirección de envío impresa tras el CIF del cliente que sustituye al domicilio fiscal; razón social
      del cliente separada dos renglones del resto de su bloque.
    - **Números e importes**: referencia a otra factura impresa por encima o a la altura del número propio cuando este
      tiene un rótulo débil; varios tipos de IVA con una sola base impresa y la cuota del tipo reducido con un rótulo no
      reconocido («IVA reducido», «Imp. 10 %»): la base y la cuota proponen solo las del tipo general (lo detecta el
      descuadre del total, que bloquea).
    - **Total**: un total impreso que suma tres o más recargos (tasas, portes, envases) o los cita sin decimales puede
      no detectarse y proponerse base + cuota como total.
    - **Retención de garantía** redactada con otras fórmulas: se pide la confirmación genérica de la retención y, si el
      usuario la confirma, el PDF la rotula como IRPF.
    - **Texto oculto**: texto invisible sobre una imagen de fondo con contenido (banda de pie, trama de seguridad)
      manda la página entera al OCR y pierde campos de una factura legítima; sigue habiendo variantes de fuentes
      cuyo /ToUnicode no dice lo que dibujan (se contrastan las cifras con la imagen, no las letras).
    - **Confirmaciones de más** en facturas correctas: nombres de cliente con minúsculas antes de la sigla y la forma
      jurídica; cabecera con razón social sin NIF; código de cliente suelto tomado por NIF ilegible; resúmenes «a
      origen / anteriores» o «saldo pendiente» (TOTAL_OTRA_CIFRA); renglones de pie sin punto unidos en una frase de
      garantía; desglose con filas a cero de otros tipos; tablas monoespaciadas con filetes verticales entre cifras;
      descuento por línea con solo «Concepto | Precio | Importe» (bloqueo LINEA_SIN_CANTIDAD: hay que indicar la
      cantidad).
    - **Coste**: un PDF hostil de 30 páginas tarda unos 35 s en extraerse (límite del proceso hijo: 120 s); una factura
      de 2.500 renglones tarda unos 12 s en expedirse y por encima de 3.000 se bloquea (DOCUMENTO_DEMASIADO_LARGO).
