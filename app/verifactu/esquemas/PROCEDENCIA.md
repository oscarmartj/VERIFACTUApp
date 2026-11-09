# Procedencia de los esquemas AEAT (VERI*FACTU)

| Fichero | Origen oficial (ruta publicada por la AEAT) |
|---|---|
| SuministroLR.xsd, SuministroInformacion.xsd, RespuestaSuministro.xsd, ConsultaLR.xsd, RespuestaConsultaLR.xsd, SistemaFacturacion.wsdl, errores.properties | https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tikeV1.0/cont/ws/ |
| xmldsig-core-schema.xsd | https://www.w3.org/TR/xmldsig-core/xmldsig-core-schema.xsd (W3C) |

**Cómo se obtuvieron (2026-11-09):** los servidores de la AEAT no respondían desde el entorno de desarrollo
(conexión TLS cortada). Se tomaron de una réplica pública (repositorio GitHub `ramoncoroso/verifactu`,
carpeta `schemas/`, cuyo PROVENANCE.md declara descarga byte a byte desde la ruta oficial el 2026-08-02
con las mismas sumas SHA-256 que figuran en `SHA256SUMS`). Se contrastaron con otras dos réplicas independientes
(`Eseperio/verifactu-php`, `mybooking-es/verifactu-rb`): coinciden en estructura; esas réplicas contienen versiones
anteriores (faltan, p. ej., ClaveRegimen 21 y el tipo SistemaInformaticoConsultaType).

**Estado:** PENDIENTE DE CONTRASTE con la publicación oficial. Ejecutar `python scripts/verificar_esquemas.py`
desde una red con acceso a la AEAT: descarga los ficheros oficiales y compara las sumas SHA-256.
Los ficheros no se modifican: la ruta remota de xmldsig se resuelve localmente en memoria al validar.
