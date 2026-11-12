-- Plataforma multiusuario: registro de cuentas, invitaciones, cambio obligatorio de contraseña y ajustes de la plataforma.
-- Las cuentas llevan una versión monótona: cada cambio (contraseña, alta o baja, contraseña temporal) la incrementa y se
-- anota en el diario, de modo que la restauración aplica siempre su último estado y nunca uno anterior a la copia.
ALTER TABLE usuarios ADD COLUMN cambiar_clave INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usuarios ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usuarios ADD COLUMN alta_origen TEXT;

-- Al actualizar desde la versión anterior, toda cuenta que no sea del operador tiene una contraseña que eligió otra
-- persona (el operador o un administrador de empresa, en el alta) y que no podía cambiarse: debe elegir una nueva al
-- entrar. La versión se incrementa para que la conciliación del arranque lo anote en el diario (una restauración no lo
-- deshace). En una instalación nueva no hay cuentas cuando se aplica esta migración.
UPDATE usuarios SET cambiar_clave = 1, alta_origen = 'version_anterior', version = version + 1 WHERE superadmin = 0;

-- NIF de la empresa acreditado ante la plataforma: lo marca el operador (o queda marcado si él mismo da de alta la empresa).
-- Hasta entonces, el primer envío real a la AEAT (pruebas o producción) solo es posible con un certificado de la propia
-- empresa. Las empresas de la versión anterior las dio de alta el operador (era el único que podía hacerlo).
ALTER TABLE empresas ADD COLUMN nif_verificado_en TEXT;
ALTER TABLE empresas ADD COLUMN nif_verificado_por INTEGER REFERENCES usuarios(id);
ALTER TABLE empresas ADD COLUMN nif_verificado_motivo TEXT;
-- Versión de la marca (sube con cada verificación o retirada): el diario guarda cada cambio y una restauración aplica el
-- más reciente, de modo que no vuelve a dar por verificado un NIF cuya verificación se retiró después de la copia.
ALTER TABLE empresas ADD COLUMN nif_verificado_version INTEGER NOT NULL DEFAULT 0;
UPDATE empresas SET nif_verificado_en = creado_en, nif_verificado_motivo = 'version_anterior';

-- Ajustes de la plataforma (p. ej. registro_abierto). Sin fila = valor por defecto (registro CERRADO).
CREATE TABLE plataforma (
  clave TEXT PRIMARY KEY,
  valor TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  actualizado_en TEXT,
  actualizado_por INTEGER REFERENCES usuarios(id)
);

-- Invitaciones de un solo uso y con caducidad. Del token solo se guarda su SHA-256.
-- empresa_id NULL: invitación de la plataforma (operador). Con empresa: alta en esa empresa con el rol indicado.
CREATE TABLE invitaciones (
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  empresa_id INTEGER REFERENCES empresas(id),
  email TEXT COLLATE NOCASE,
  rol TEXT CHECK (rol IS NULL OR rol IN ('admin','emisor','lector')),
  relacion TEXT,
  creada_por INTEGER NOT NULL REFERENCES usuarios(id),
  creada_en TEXT NOT NULL,
  expira_en TEXT NOT NULL,
  usada_en TEXT,
  usada_por INTEGER REFERENCES usuarios(id),
  revocada_en TEXT,
  revocada_motivo TEXT
);
CREATE INDEX invitaciones_empresa ON invitaciones(empresa_id);
