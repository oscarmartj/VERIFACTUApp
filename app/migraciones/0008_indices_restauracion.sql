-- 0008: índices para buscar los registros de una factura y sus rectificativas. Sin ellos, cada consulta recorre la tabla
-- entera y la restauración (copias.reproducir_diario → emision.recalcular_estado_factura) crecía con el cuadrado del
-- número de facturas. Solo añade índices: vale para una base de la versión anterior (hasta 0004) y para una nueva.
CREATE INDEX IF NOT EXISTS registros_factura ON registros(factura_id, secuencia);
CREATE INDEX IF NOT EXISTS facturas_rectifica ON facturas(rectifica_factura_id);
