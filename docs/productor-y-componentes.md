# Productor del sistema y componentes

## Quién es el productor

Según el art. 13 del Reglamento (RD 1007/2023) y las FAQ de la AEAT, es productor quien desarrolla el sistema, **también si lo desarrolla para su propio uso** o integra componentes de código abierto. Este sistema se ha construido por encargo del usuario con asistencia de una herramienta de IA, que **no puede asumir esa condición**.

**Pendiente de decisión del responsable:** designar a la persona o entidad que asume la condición de productor y su mantenimiento (previsiblemente la propia empresa usuaria, como desarrollo para uso interno, o la persona o entidad que lo mantenga y distribuya). Hasta entonces:

- la tabla `productor` queda sin confirmar y **producción y pruebas AEAT están bloqueadas** (requisito «Productor del sistema identificado y confirmado»);
- en el simulador local, el bloque `SistemaInformatico` usa provisionalmente la razón social y el NIF de la empresa emisora.

Datos del productor que deben aportarse: nombre o razón social, NIF (o identificación extranjera con tipo y país) y dirección postal.

## Identificación del sistema

| Dato | Valor |
|---|---|
| Nombre del sistema (`NombreSistemaInformatico`) | FacturaPDF VF |
| Código identificador (`IdSistemaInformatico`) | FV |
| Versión | 0.1.0 |
| Modalidad | Exclusivamente VERI*FACTU (`TipoUsoPosibleSoloVerifactu = S`) |
| Multiobligado | Sí (`TipoUsoPosibleMultiOT = S`); `IndicadorMultiplesOT` = S si la instalación tiene más de una empresa |
| Número de instalación | Generado al instalar (`INST-xxxxxxxx`), único por despliegue |

## Componentes de terceros (todos de código abierto y locales; ninguno remite datos a terceros)

| Componente | Función | Licencia |
|---|---|---|
| Python 3.13, FastAPI, Starlette, Uvicorn | Servidor web y API | PSF / MIT / BSD |
| SQLite | Base de datos | Dominio público |
| lxml / libxml2 | Generación y validación XML contra XSD | BSD / MIT |
| pdfplumber, pdfminer.six, pypdf | Lectura de texto y estructura de PDF | MIT / BSD |
| ReportLab | Composición del PDF definitivo | BSD |
| segno | Generación del QR (ISO/IEC 18004, nivel M) | BSD |
| cryptography | PKCS#12 y AES-256-GCM | Apache 2.0 / BSD |
| requests | Cliente HTTPS mTLS | Apache 2.0 |
| Poppler (pdftoppm), qpdf, Tesseract OCR (spa), ZBar | Rasterizado, inspección, OCR y lectura de QR | GPL / Apache 2.0 / LGPL |
| Esquemas AEAT (XSD, WSDL, errores.properties) | Estructura y validación de registros | Publicación de la AEAT |

No se utilizan servicios externos de envío, OCR ni IA: no hay declaraciones responsables de terceros que recabar por esos conceptos. Si en el futuro se incorporara un componente de remisión de terceros, su productor deberá aportar su propia declaración (art. 15.4 de la Orden).

## Documentación técnica por versión

- Código fuente y pruebas automatizadas (`tests/`), con evidencias en `docs/evidencias/`.
- Matriz de requisitos con su estado (`docs/matriz-requisitos.md`).
- Esquemas usados y su procedencia (`app/verifactu/esquemas/PROCEDENCIA.md`).
- Declaración responsable de cada versión (`docs/declaracion-responsable-borrador.md`; la formalizada se registra en la tabla `declaraciones` y se muestra en la aplicación).
