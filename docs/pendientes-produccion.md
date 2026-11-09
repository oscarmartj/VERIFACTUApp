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
13. Escaneos de mala calidad: el OCR local acierta ~89 % de los campos en escaneos simulados; los valores dudosos se marcan para revisión. Opcional: IA de extracción solo como propuesta que el usuario confirma, con consentimiento expreso y valorando el envío de datos a terceros.
14. Limitaciones conocidas de la recuperación: el diario se lee completo en cada arranque (adecuado para el volumen de una pyme; para volúmenes altos, índice o puntos de control en el manifiesto de la copia); certificados, autorizaciones de producción y series confirmadas sin uso posteriores a la última copia deben volver a introducirse tras restaurar; una línea del diario ilegible exige revisión manual y `forzar=True` para restaurar.
15. Despliegue de varias instancias: el diseño asume un único proceso de aplicación (SQLite y un remitente por empresa y entorno). Para varias instancias, migrar a PostgreSQL y renovar el bloqueo del remitente.
