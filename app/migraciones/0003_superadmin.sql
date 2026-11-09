-- Privilegio de instalación (productor del sistema, alta de empresas, modo del simulador):
-- solo el usuario que instaló la aplicación o a quien este se lo conceda.
ALTER TABLE usuarios ADD COLUMN superadmin INTEGER NOT NULL DEFAULT 0;
UPDATE usuarios SET superadmin = 1 WHERE id = (SELECT MIN(id) FROM usuarios);
