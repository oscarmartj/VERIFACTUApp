-- 0010: preferencias de cada usuario.
--
-- «Modo avanzado» (paquete 5 de la simplificación): muestra en la interfaz las herramientas técnicas (editor del patrón de
-- la numeración, contadores por año, detalle de la remisión, consulta a la AEAT, actividad, huellas, CSV y XML de los
-- registros, códigos de incidencia, «Mis certificados» y la legitimación completa). Es una preferencia de la persona, no
-- de la empresa; desactivado por defecto. No cambia ningún permiso: el servidor sigue comprobando el rol en cada petición.
ALTER TABLE usuarios ADD COLUMN modo_avanzado INTEGER NOT NULL DEFAULT 0 CHECK (modo_avanzado IN (0, 1));
