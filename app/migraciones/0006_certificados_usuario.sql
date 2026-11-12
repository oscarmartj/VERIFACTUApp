-- «Mis certificados»: certificados electrónicos de un usuario, cargados una vez y asignados a las empresas que
-- administra; legitimación de terceros por titular del certificado (Cabecera/Representante).
-- Las filas llevan una versión monótona: cada cambio se anota en el diario (sin el archivo cifrado) y la restauración
-- aplica la versión más reciente, de modo que una baja, retirada o revocación nunca se deshace al restaurar.

CREATE TABLE certificados_usuario (
  id INTEGER PRIMARY KEY,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),   -- propietario: el único que puede asignarlo, retirarlo o borrarlo
  alias TEXT,
  sujeto TEXT NOT NULL,
  emisor TEXT NOT NULL,
  numero_serie TEXT NOT NULL,
  valido_desde TEXT NOT NULL,
  valido_hasta TEXT NOT NULL,
  titular_nif TEXT,
  representado_nif TEXT,
  titular_nombre TEXT,              -- nombre de quien presenta (entidad o persona), para Cabecera/Representante
  tipo TEXT NOT NULL,
  huella_cert TEXT NOT NULL,
  blob_cifrado BLOB,                -- PKCS#12 + contraseña (AES-256-GCM); NULL si se eliminó o debe volver a cargarse
  subido_en TEXT NOT NULL,
  eliminado_en TEXT,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX certificados_usuario_unico ON certificados_usuario(usuario_id, huella_cert) WHERE eliminado_en IS NULL;

CREATE TABLE certificado_asignaciones (
  id INTEGER PRIMARY KEY,
  certificado_usuario_id INTEGER NOT NULL REFERENCES certificados_usuario(id),
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  -- 'simulador' solo sirve para probar en local la representación (Cabecera/Representante); no hay TLS
  entorno TEXT NOT NULL CHECK (entorno IN ('simulador','pruebas','produccion')),
  activo INTEGER NOT NULL DEFAULT 1,
  asignado_por INTEGER NOT NULL REFERENCES usuarios(id),
  asignado_en TEXT NOT NULL,
  retirado_en TEXT,
  retirado_por INTEGER REFERENCES usuarios(id),
  motivo_retirada TEXT,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX certificado_asignacion_activa ON certificado_asignaciones(empresa_id, entorno) WHERE activo = 1;
CREATE INDEX certificado_asignaciones_cert ON certificado_asignaciones(certificado_usuario_id);

-- Nombre del titular en los certificados cargados por empresa
ALTER TABLE certificados ADD COLUMN titular_nombre TEXT;
-- Versión de cada certificado cargado por empresa: su estado «en uso» (activo) se anota en el diario (nunca el archivo)
-- para que una activación o desactivación posterior a la copia no se deshaga al restaurar
ALTER TABLE certificados ADD COLUMN version INTEGER NOT NULL DEFAULT 0;

-- Legitimación de un tercero: para qué titular del certificado (NIF y nombre) y si se ha revocado
ALTER TABLE legitimaciones ADD COLUMN titular_nif TEXT;
ALTER TABLE legitimaciones ADD COLUMN titular_nombre TEXT;
ALTER TABLE legitimaciones ADD COLUMN revocada_en TEXT;
ALTER TABLE legitimaciones ADD COLUMN revocada_por INTEGER REFERENCES usuarios(id);
ALTER TABLE legitimaciones ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
