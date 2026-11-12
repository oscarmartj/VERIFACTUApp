-- Cadena de registros y AEAT tras un desastre (perdidos la base de datos y el diario, restaurada una copia antigua).
-- La consulta a la AEAT detecta registros de esta instalación posteriores al último registro local («cadena atrasada»):
-- mientras tanto no se generan registros en ese entorno. Un administrador reanuda la cadena desde el último registro que
-- tiene la AEAT («anclada»): el siguiente registro lo usa como RegistroAnterior y la fila desaparece al encadenarse.
-- Cada cambio se anota en el diario (`cadena_aeat`) y la restauración lo reproduce.
CREATE TABLE IF NOT EXISTS cadenas_aeat (
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL CHECK (entorno IN ('simulador', 'pruebas', 'produccion')),
  estado TEXT NOT NULL CHECK (estado IN ('atrasada', 'anclada')),
  -- Último registro de la AEAT posterior a la cabeza local (datos tal como los devuelve la consulta)
  id_emisor TEXT NOT NULL,
  num_serie TEXT NOT NULL,
  fecha_expedicion TEXT NOT NULL,       -- dd-mm-aaaa
  fecha_hora_huso TEXT NOT NULL,        -- FechaHoraHusoGenRegistro
  huella TEXT NOT NULL,
  tipo TEXT,                            -- alta | anulacion (según el estado que devuelve la AEAT)
  registros_posteriores INTEGER NOT NULL DEFAULT 1,
  cabeza_local TEXT,                    -- FechaHoraHusoGenRegistro del último registro local al detectarlo
  detectada_en TEXT NOT NULL,
  evidencia TEXT,                       -- carpeta(s) del almacén con la petición y la respuesta
  anclada_en TEXT,
  anclada_por INTEGER REFERENCES usuarios(id),
  PRIMARY KEY (empresa_id, entorno)
);

-- Registro que se encadenó a un registro de la AEAT (ancla externa), en JSON: {id_emisor, num_serie, fecha_expedicion,
-- fecha_hora_huso, huella}. Lo usa la verificación de la cadena local; se fija al crear el registro y no cambia.
ALTER TABLE registros ADD COLUMN ancla_aeat TEXT;

CREATE TRIGGER registros_ancla_inmutable BEFORE UPDATE OF ancla_aeat ON registros
WHEN OLD.ancla_aeat IS NOT NEW.ancla_aeat
BEGIN SELECT RAISE(ABORT, 'Los registros de facturación son inmutables'); END;
