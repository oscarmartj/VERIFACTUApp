-- 0009: alta guiada por el certificado y modo práctica.
--
-- 1) Un certificado cargado para la empresa puede servir para los dos entornos de la AEAT (entorno 'todos'): se carga
--    una sola vez y se usa en pruebas y en producción. Las filas existentes ('pruebas' / 'produccion') no cambian.
--    SQLite no permite modificar un CHECK: se reconstruye la tabla con las mismas columnas y datos (ninguna tabla la
--    referencia). El archivo sigue cifrado (AES-256-GCM) con el contexto de su entorno.
CREATE TABLE certificados_0009 (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL CHECK (entorno IN ('pruebas','produccion','todos')),
  alias TEXT,
  sujeto TEXT NOT NULL,
  emisor TEXT NOT NULL,
  numero_serie TEXT NOT NULL,
  valido_desde TEXT NOT NULL,
  valido_hasta TEXT NOT NULL,
  titular_nif TEXT,
  representado_nif TEXT,
  tipo TEXT NOT NULL,
  huella_cert TEXT NOT NULL,
  blob_cifrado BLOB NOT NULL,
  legitimacion TEXT,
  activo INTEGER NOT NULL DEFAULT 1,
  subido_por INTEGER NOT NULL REFERENCES usuarios(id),
  subido_en TEXT NOT NULL,
  titular_nombre TEXT,
  version INTEGER NOT NULL DEFAULT 0
);
INSERT INTO certificados_0009 (id, empresa_id, entorno, alias, sujeto, emisor, numero_serie, valido_desde, valido_hasta,
  titular_nif, representado_nif, tipo, huella_cert, blob_cifrado, legitimacion, activo, subido_por, subido_en, titular_nombre,
  version)
  SELECT id, empresa_id, entorno, alias, sujeto, emisor, numero_serie, valido_desde, valido_hasta, titular_nif,
  representado_nif, tipo, huella_cert, blob_cifrado, legitimacion, activo, subido_por, subido_en, titular_nombre, version
  FROM certificados;
DROP TABLE certificados;
ALTER TABLE certificados_0009 RENAME TO certificados;

-- 2) Vista previa del certificado antes de crear la empresa: el archivo y su contraseña se guardan CIFRADOS (con la clave
--    maestra, ligados a la fila y al usuario) durante unos minutos; al confirmar pasan a ser el certificado de la empresa y
--    la fila se borra. Nunca se anotan en el diario ni se devuelven al navegador; las caducadas se borran al arrancar y en
--    cada uso. Del identificador que recibe el navegador solo se guarda su SHA-256.
CREATE TABLE certificados_previos (
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  blob_cifrado BLOB NOT NULL,
  creado_en TEXT NOT NULL,
  expira_en TEXT NOT NULL
);
CREATE INDEX certificados_previos_usuario ON certificados_previos(usuario_id);

-- 3) Cómo se dio de alta la empresa: 'certificado' (alta guiada por el certificado), 'practica' (modo práctica: datos
--    leídos de una factura, solo simulador hasta cargar el certificado) o 'manual'. NULL en las anteriores a esta versión.
ALTER TABLE empresas ADD COLUMN alta_origen TEXT;
