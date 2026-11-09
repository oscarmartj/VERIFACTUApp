-- Posible entrega previa (para interpretar un duplicado 3000) y detalle del duplicado devuelto por la AEAT.
-- Las marcas temporales de planificación (siguiente_intento_en, proximo_envio_en, bloqueo_hasta) pasan a UTC.
ALTER TABLE registros ADD COLUMN posible_entrega INTEGER NOT NULL DEFAULT 0;
ALTER TABLE registros ADD COLUMN duplicado TEXT;
