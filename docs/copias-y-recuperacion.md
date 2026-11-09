# Copias de seguridad y recuperación

## Qué se protege

| Elemento | Ubicación | Copia |
|---|---|---|
| Base de datos | `VERIFACTU_DATA_DIR/verifactu.sqlite3` | `crear_copia` (API de *backup* de SQLite, consistente en caliente) |
| Almacén (originales, PDF definitivos, peticiones/respuestas) | `VERIFACTU_DATA_DIR/almacen` | Incluido en la copia (`almacen.tar.gz`) |
| **Diario** de facturas, registros, miembros y estados (cada línea con su SHA-256) | `VERIFACTU_DIARIO_DIR/diario.jsonl` | Debe estar en **otro disco/volumen** y replicarse de forma continua |
| **Espejo** de binarios no regenerables (PDF definitivos y documentos originales) | `VERIFACTU_DIARIO_DIR/almacen/` | En el mismo volumen que el diario |
| Clave maestra (descifra certificados) | `VERIFACTU_CLAVE_MAESTRA_FICHERO` | **Aparte** y custodiada (gestor de secretos o soporte cifrado). Nunca junto a las copias de la base de datos |

## Hacer una copia

```bash
python -c "from app import db, copias; print(copias.crear_copia(db.conectar(crear=False)))"
```

Genera `copias/copia_AAAAMMDDTHHMMSS/` con `verifactu.sqlite3`, `almacen.tar.gz` y `manifiesto.json` (SHA-256 de cada fichero y estado de las cadenas). Programe la copia diaria y súbala a un almacenamiento externo con versionado.

## Restaurar (aplicación detenida)

```bash
python -c "from pathlib import Path; from app import copias; print(copias.restaurar(Path('copias/copia_…')))"
```

1. Verifica el manifiesto (si un fichero está dañado, se aborta sin tocar nada). Si la base de datos actual aún se puede leer, **primero la concilia con el diario** (nada confirmado en ella puede perderse) y, al final, comprueba que la base restaurada contiene todas sus facturas y registros; si no, se detiene sin cambiar nada.
   - Si el diario tiene líneas ilegibles, entradas cuyo contenido no cuadra con sus sumas o registros sin su factura, la restauración **se detiene** y exige revisión y `restaurar(copia, forzar=True)`; en ese caso los números e identificadores afectados quedan **reservados** (factura marcadora y contador de serie avanzado) para no reutilizarse.
2. Copia la base de datos de la copia a un **fichero temporal** y reproduce allí el **diario** (claves foráneas desactivadas durante la reproducción): reinserta las facturas y registros posteriores a la copia junto con su **contexto** (empresa, serie, instalación, documento, extracción y borrador anotados con cada factura), aplica los cambios de estado de las facturas (anulada, rectificada) y el último estado de comunicación de cada registro (incluida la marca de posible entrega), y actualiza contadores de serie y cabeza de cada cadena. Los usuarios referenciados que no existían en la copia se crean **inactivos y sin contraseña utilizable**. Un registro que estaba «enviando» pasa a «sin confirmar» con posible entrega: se reenviará el mismo XML y la AEAT responderá «duplicado» si ya lo tenía.
3. **Solo si todo lo anterior termina bien**, guarda una copia consistente de la base actual (`verifactu.antes_de_restaurar_<marca>.sqlite3`, incluido su WAL) y coloca la restaurada con un único `os.replace` atómico: en ningún instante falta la base de datos.
4. **Fusiona** en el almacén los ficheros que falten de la copia y del espejo del diario (nunca borra ni sobrescribe).
5. **Repara los PDF**: cada PDF definitivo se contrasta con su SHA-256 registrado; si falta o está dañado se toma de una copia válida (espejo o copia de seguridad) o se regenera desde la versión inmutable, y solo se acepta si la suma coincide. El resultado informa de `pdf_reparados`, `pdf_regenerados` y `pdf_irrecuperables`.
6. Las entradas del diario cuya suma o cuyo contenido no cuadra (XML frente a `xml_sha256`, cadena frente a huella, datos frente a `datos_sha256`) **no se reproducen** y se informan (`entradas_descartadas`, `lineas_diario_ilegibles`).

Verificado en `tests/test_03_flujo.py::test_caso12_copia_restauracion` y `tests/test_05_revision.py::test_h1_restauracion_con_datos_posteriores_a_la_copia` (documento, usuario, factura y anulación posteriores a la copia): tras restaurar no se reenvía nada ya aceptado, la factura anulada sigue anulada y la siguiente factura toma el número siguiente y encadena con el último registro real.

## Reconciliación y arranque seguro

- Al arrancar, `copias.reconciliar_diario` añade al diario las facturas, registros y **estados** (de la factura y de la comunicación) que falten o difieran de la última anotación (p. ej. caída entre la confirmación en base de datos y la anotación). Antes de cada envío se anota también la marca «enviando», de modo que una pérdida de la base durante un envío se restaura como «sin confirmar / posible entrega».
- Las líneas ilegibles del diario (p. ej. la última, cortada por una caída) se omiten y se registran en la actividad, sin impedir el arranque ni la restauración.
- `copias.comprobar_arranque` **impide arrancar** si falta la base de datos, si está vacía, dañada o **atrasada** respecto al diario (facturas o registros anotados que no contiene): usar una base así reiniciaría la numeración o bifurcaría la cadena. El remedio es `copias.restaurar`. La herramienta de copia abre la base con `crear=False` para no crear nunca una vacía.
- Los miembros de cada empresa se anotan con una **versión monótona**: al restaurar se aplica la instantánea de mayor versión y nunca una anterior a la copia (no se recuperan permisos ya retirados).
- Se reproduce solo el **último** estado de comunicación anotado de cada registro (los indicadores de posible entrega e incidencia no se acumulan artificialmente).
- Tras restaurar, los datos de configuración posteriores a la copia que no acompañan a ninguna factura (certificados, autorizaciones de producción, series confirmadas sin uso) deben volver a introducirse.

## Riesgo residual

Si se pierden **a la vez** la base de datos y el diario, los registros posteriores a la última copia solo podrían reconstruirse consultando a la AEAT (servicio `ConsultaFactuSistemaFacturacion`, cuyo mensaje genera `xmlgen.mensaje_consulta`, pendiente de integrar en un procedimiento guiado). Por eso el diario debe estar en un soporte distinto.
