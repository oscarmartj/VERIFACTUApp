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

## Pruebas

```bash
python -m pytest -q
```

Más de 500 pruebas: los 14 casos de aceptación del encargo, las regresiones de cuatro rondas de verificación adversarial (tres del núcleo y una del extractor y el flujo de borradores) y la extracción sobre el corpus de diseños variados (ver `docs/pruebas.md`; evaluación detallada con `python scripts/evaluar_extraccion.py tests/corpus tests/corpus_holdout* --detalle`). Todas son **simuladas o locales**; las evidencias (PDF, PNG, XML, JSON y capturas) se guardan en `docs/evidencias/`.

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
