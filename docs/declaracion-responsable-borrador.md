# BORRADOR — NO FORMALIZADO

> Este texto es un **borrador** preparado para que lo revise y, en su caso, suscriba **el productor del sistema**.
> No ha sido firmado ni asumido por nadie y no constituye una declaración. No existe homologación ni aval de la AEAT:
> la declaración responsable es una autodeclaración del productor (art. 13 RD 1007/2023; art. 15 Orden HAC/1177/2024).
> Los campos marcados [PENDIENTE] deben completarse con datos reales. Antes de formalizarla deben resolverse los puntos de
> `docs/pendientes-produccion.md`.

---

**DECLARACIÓN RESPONSABLE DEL SISTEMA INFORMÁTICO DE FACTURACIÓN**

a) Nombre del sistema informático de facturación: **FacturaPDF VF**

b) Código identificador del sistema informático de facturación: **FV**

c) Identificador completo de la versión del sistema informático de facturación: **0.1.0**

d) Componentes, hardware y software, del sistema informático de facturación, y breve descripción de lo que hace y de sus principales funcionalidades:
Aplicación web (servidor Python/FastAPI con base de datos SQLite y almacenamiento privado de documentos; navegador web como cliente) que se ejecuta en un servidor del usuario [PENDIENTE: describir el equipo o servicio de alojamiento]. Importa documentos PDF preparatorios, extrae y valida sus datos, permite revisarlos y corregirlos con historial, y expide facturas completas (F1) y rectificativas por diferencias (R1, R4) en régimen general de IVA, generando en el momento de la expedición el registro de facturación de alta (o de anulación), encadenado mediante huella SHA-256, que remite de forma automática a la AEAT por el servicio VERI*FACTU con control de flujo, gestión de incidencias y reintentos. Genera el PDF de la factura con el código QR tributario y la leyenda «VERI*FACTU». Componentes de terceros: ver `docs/productor-y-componentes.md`.

e) Indicación de si el sistema informático de facturación, para cumplir con el Reglamento, se ha producido de modo que solo pueda funcionar exclusivamente como «VERI*FACTU»: **Sí**.

f) Tipos de uso posibles: el sistema permite ser usado por varios obligados tributarios o por un mismo usuario para dar soporte a la facturación de varios obligados tributarios: **Sí**.

g) Tipos de firma utilizados para firmar los registros de facturación y de evento en caso de que el sistema no se utilice como «VERI*FACTU»: **No aplica** (el sistema funciona exclusivamente como «VERI*FACTU»).

h) Nombre o razón social del productor: [PENDIENTE]

i) NIF del productor: [PENDIENTE]

j) Dirección postal completa de contacto del productor: [PENDIENTE]

k) El productor del sistema informático de facturación declara que dicho sistema, en la versión indicada, cumple con lo dispuesto en el artículo 29.2.j) de la Ley 58/2003, de 17 de diciembre, General Tributaria, en el Reglamento que establece los requisitos que deben adoptar los sistemas y programas informáticos o electrónicos que soporten los procesos de facturación de empresarios y profesionales, y la estandarización de formatos de los registros de facturación, aprobado por el Real Decreto 1007/2023, de 5 de diciembre, en la Orden HAC/1177/2024, de 17 de octubre, y en la sede electrónica de la Agencia Estatal de Administración Tributaria para todo aquello que complete las especificaciones de dicha Orden. [PENDIENTE: solo puede afirmarse cuando se hayan superado las pruebas reales con la AEAT y resuelto los puntos de `docs/pendientes-produccion.md`]

l) Fecha y lugar en que el productor suscribe esta declaración responsable: [PENDIENTE: día, mes y año; localidad y país]

---

**Anexo (art. 15.2 de la Orden)**: cómo se cumple cada especificación: ver `docs/matriz-requisitos.md`; limitaciones de uso: solo los casos de `CASOS_SOPORTADOS` (`app/borradores.py`); el resto se bloquea.
