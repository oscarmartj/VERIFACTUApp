-- Versión monótona de los miembros de cada empresa: la restauración aplica la instantánea más reciente
-- y nunca una anterior a la copia (evita recuperar permisos ya retirados).
ALTER TABLE empresas ADD COLUMN version_miembros INTEGER NOT NULL DEFAULT 0;
