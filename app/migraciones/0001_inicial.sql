-- Esquema inicial. Las tablas fiscales (facturas, registros) no admiten borrado ni
-- modificación de los campos que forman la huella: lo impiden los disparadores al final.

CREATE TABLE usuarios (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  nombre TEXT NOT NULL,
  hash_clave TEXT NOT NULL,
  activo INTEGER NOT NULL DEFAULT 1,
  intentos_fallidos INTEGER NOT NULL DEFAULT 0,
  bloqueado_hasta TEXT,
  creado_en TEXT NOT NULL
);

CREATE TABLE sesiones (
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  csrf TEXT NOT NULL,
  creada_en TEXT NOT NULL,
  expira_en TEXT NOT NULL
);

CREATE TABLE empresas (
  id INTEGER PRIMARY KEY,
  nif TEXT NOT NULL UNIQUE,
  razon_social TEXT NOT NULL,
  domicilio TEXT,
  -- Ámbito del emisor (determinado en la configuración inicial)
  tipo_contribuyente TEXT,          -- sociedad_IS | persona_fisica_IRPF | entidad_renta | otro
  territorio TEXT,                  -- comun | pais_vasco | navarra
  canarias_ceuta_melilla INTEGER NOT NULL DEFAULT 0,
  sii INTEGER,                      -- 1 si lleva libros por el SII
  otras_exclusiones TEXT,
  origen_pdf TEXT,                  -- plantilla_ofimatica | programa_facturacion | otro
  origen_pdf_detalle TEXT,
  ambito_resultado TEXT,            -- JSON con la evaluación
  ambito_confirmado_en TEXT,
  ambito_confirmado_por INTEGER REFERENCES usuarios(id),
  creado_en TEXT NOT NULL
);

CREATE TABLE miembros (
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  rol TEXT NOT NULL CHECK (rol IN ('admin','emisor','lector')),
  relacion TEXT NOT NULL,           -- administrador | representante | empleado_autorizado | asesor
  declaracion_autorizacion_en TEXT, -- el usuario declara estar autorizado a actuar por la empresa
  PRIMARY KEY (usuario_id, empresa_id)
);

-- Productor del sistema informático de facturación e identificación de esta instalación
CREATE TABLE productor (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  nombre TEXT,
  nif TEXT,
  domicilio TEXT,
  numero_instalacion TEXT NOT NULL,
  confirmado_por INTEGER REFERENCES usuarios(id),
  confirmado_en TEXT
);

-- Instalación del SIF: identidad que viaja en cada registro (bloque SistemaInformatico)
CREATE TABLE instalaciones (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  numero_instalacion TEXT NOT NULL,
  creada_en TEXT NOT NULL,
  UNIQUE (empresa_id, numero_instalacion)
);

CREATE TABLE series (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL CHECK (entorno IN ('simulador','pruebas','produccion')),
  codigo TEXT NOT NULL,             -- identificador interno de la serie (p. ej. "26")
  patron TEXT NOT NULL,             -- p. ej. "{n}/{aa}" -> 166/26 ; "PRB-{n}" -> PRB-1
  descripcion TEXT,
  tipo TEXT NOT NULL DEFAULT 'ordinaria' CHECK (tipo IN ('ordinaria','rectificativa')),
  siguiente_numero INTEGER NOT NULL DEFAULT 1,
  confirmada_por INTEGER REFERENCES usuarios(id),
  confirmada_en TEXT,
  activa INTEGER NOT NULL DEFAULT 1,
  UNIQUE (empresa_id, entorno, codigo)
);

-- Contador por serie y año (si el patrón incluye el año, la numeración se reinicia cada año)
CREATE TABLE series_contadores (
  serie_id INTEGER NOT NULL REFERENCES series(id),
  anio INTEGER NOT NULL,
  siguiente INTEGER NOT NULL,
  PRIMARY KEY (serie_id, anio)
);

CREATE TABLE documentos (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  sha256 TEXT NOT NULL,
  nombre_archivo TEXT NOT NULL,
  tamano INTEGER NOT NULL,
  paginas INTEGER,
  ruta TEXT NOT NULL,               -- relativa al almacén privado; el original nunca se modifica
  subido_por INTEGER NOT NULL REFERENCES usuarios(id),
  subido_en TEXT NOT NULL,
  inspeccion TEXT,                  -- JSON: firmas, JavaScript, cifrado, adjuntos, QR detectado...
  texto_nativo INTEGER,
  ocr_usado INTEGER,
  clasificacion TEXT,               -- preparatorio | emitida_propia | recibida | desconocido
  clasificacion_motivo TEXT,
  clasificacion_confirmada_por INTEGER REFERENCES usuarios(id),
  clasificacion_confirmada_en TEXT,
  bloqueo_emision TEXT,             -- motivo por el que no puede emitirse fuera del simulador local (p. ej. factura de referencia)
  estado TEXT NOT NULL DEFAULT 'pendiente', -- pendiente | procesado | error
  error TEXT,
  UNIQUE (empresa_id, sha256)
);

CREATE TABLE extracciones (
  id INTEGER PRIMARY KEY,
  documento_id INTEGER NOT NULL REFERENCES documentos(id),
  version_extractor TEXT NOT NULL,
  datos TEXT NOT NULL,              -- JSON con valor, procedencia y confianza por campo
  creada_en TEXT NOT NULL
);

CREATE TABLE borradores (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  documento_id INTEGER REFERENCES documentos(id),
  extraccion_id INTEGER REFERENCES extracciones(id),
  datos TEXT NOT NULL,              -- JSON editable (preparación)
  estado TEXT NOT NULL DEFAULT 'requiere_revision',
  -- requiere_revision | listo | emitido | descartado
  rectifica_factura_id INTEGER REFERENCES facturas(id),
  factura_id INTEGER REFERENCES facturas(id),
  creado_en TEXT NOT NULL,
  actualizado_en TEXT NOT NULL
);

CREATE TABLE cambios_borrador (
  id INTEGER PRIMARY KEY,
  borrador_id INTEGER NOT NULL REFERENCES borradores(id),
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  en TEXT NOT NULL,
  campo TEXT NOT NULL,
  valor_anterior TEXT,
  valor_nuevo TEXT,
  motivo TEXT
);

-- Factura expedida: versión inmutable de los datos aprobados
CREATE TABLE facturas (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL CHECK (entorno IN ('simulador','pruebas','produccion')),
  borrador_id INTEGER NOT NULL UNIQUE REFERENCES borradores(id),
  documento_id INTEGER REFERENCES documentos(id),
  clave_idempotencia TEXT NOT NULL UNIQUE,
  serie_id INTEGER REFERENCES series(id),
  numero INTEGER,
  num_serie TEXT NOT NULL,
  fecha_expedicion TEXT NOT NULL,   -- ISO aaaa-mm-dd
  tipo_factura TEXT NOT NULL,
  emisor_nif TEXT NOT NULL,
  destinatario_nif TEXT,
  destinatario_nombre TEXT,
  cuota_total TEXT NOT NULL,
  importe_total TEXT NOT NULL,
  datos TEXT NOT NULL,              -- JSON canónico de los datos aprobados
  datos_sha256 TEXT NOT NULL,
  url_qr TEXT NOT NULL,
  pdf_ruta TEXT,
  pdf_sha256 TEXT,
  pdf_estrategia TEXT,              -- superposicion | reconstruccion
  estado TEXT NOT NULL DEFAULT 'emitida', -- emitida | anulada | rectificada
  rectifica_factura_id INTEGER REFERENCES facturas(id),
  creada_por INTEGER NOT NULL REFERENCES usuarios(id),
  creada_en TEXT NOT NULL,
  UNIQUE (empresa_id, entorno, emisor_nif, num_serie)
);

-- Estado de la cadena por obligado tributario e instalación del SIF (única para todas las series y ejercicios)
CREATE TABLE cadenas (
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL,
  instalacion_id INTEGER NOT NULL REFERENCES instalaciones(id),
  ultimo_registro_id INTEGER REFERENCES registros(id),
  secuencia INTEGER NOT NULL DEFAULT 0,
  ultima_fecha_hora TEXT,
  PRIMARY KEY (empresa_id, entorno, instalacion_id)
);

CREATE TABLE registros (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL,
  instalacion_id INTEGER NOT NULL REFERENCES instalaciones(id),
  secuencia INTEGER NOT NULL,
  factura_id INTEGER NOT NULL REFERENCES facturas(id),
  tipo TEXT NOT NULL CHECK (tipo IN ('alta','anulacion')),
  subsanacion TEXT,
  rechazo_previo TEXT,
  sin_registro_previo TEXT,
  registro_corregido_id INTEGER REFERENCES registros(id),
  id_emisor TEXT NOT NULL,
  num_serie TEXT NOT NULL,
  fecha_expedicion TEXT NOT NULL,   -- dd-mm-aaaa
  tipo_factura TEXT,
  cuota_total TEXT,
  importe_total TEXT,
  huella_anterior TEXT,
  fecha_hora_huso TEXT NOT NULL,
  huella TEXT NOT NULL UNIQUE,
  cadena_huella TEXT NOT NULL,      -- cadena exacta sobre la que se calculó la huella (auditoría)
  xml TEXT NOT NULL,
  xml_sha256 TEXT NOT NULL,
  -- Estado de la comunicación (independiente del estado de la factura)
  estado_envio TEXT NOT NULL DEFAULT 'pendiente',
  -- pendiente | enviando | sin_confirmar | aceptado | aceptado_con_errores | rechazado | bloqueado
  incidencia INTEGER NOT NULL DEFAULT 0,
  intentos INTEGER NOT NULL DEFAULT 0,
  siguiente_intento_en TEXT,
  ultimo_envio_id INTEGER REFERENCES envios(id),
  codigo_error TEXT,
  descripcion_error TEXT,
  csv TEXT,
  creado_en TEXT NOT NULL,
  actualizado_en TEXT NOT NULL,
  UNIQUE (empresa_id, entorno, instalacion_id, secuencia)
);
CREATE INDEX registros_cola ON registros(empresa_id, entorno, estado_envio, secuencia);

CREATE TABLE envios (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL,
  destino TEXT NOT NULL,            -- URL o 'simulador-local'
  iniciado_en TEXT NOT NULL,
  finalizado_en TEXT,
  num_registros INTEGER NOT NULL,
  peticion_ruta TEXT,
  respuesta_ruta TEXT,
  http_estado INTEGER,
  resultado TEXT,                   -- Correcto | ParcialmenteCorrecto | Incorrecto | fault | sin_respuesta | error_red
  csv TEXT,
  tiempo_espera INTEGER,
  error TEXT
);

CREATE TABLE envio_lineas (
  envio_id INTEGER NOT NULL REFERENCES envios(id),
  registro_id INTEGER NOT NULL REFERENCES registros(id),
  estado_registro TEXT,
  codigo_error TEXT,
  descripcion_error TEXT,
  duplicado TEXT,                   -- JSON
  PRIMARY KEY (envio_id, registro_id)
);

-- Control de flujo y exclusión mutua del remitente por empresa y entorno
CREATE TABLE control_flujo (
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL,
  proximo_envio_en TEXT,
  tiempo_espera INTEGER NOT NULL DEFAULT 60,
  bloqueo_hasta TEXT,
  bloqueado_por TEXT,
  incidencia_desde TEXT,
  PRIMARY KEY (empresa_id, entorno)
);

CREATE TABLE certificados (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  entorno TEXT NOT NULL CHECK (entorno IN ('pruebas','produccion')),
  alias TEXT,
  sujeto TEXT NOT NULL,
  emisor TEXT NOT NULL,
  numero_serie TEXT NOT NULL,
  valido_desde TEXT NOT NULL,
  valido_hasta TEXT NOT NULL,
  titular_nif TEXT,
  representado_nif TEXT,
  tipo TEXT NOT NULL,               -- persona_juridica | representante_pj | persona_fisica | sello | desconocido
  huella_cert TEXT NOT NULL,
  blob_cifrado BLOB NOT NULL,       -- PKCS#12 + contraseña, cifrados con AES-256-GCM
  legitimacion TEXT,                -- JSON con el resultado de la comprobación
  activo INTEGER NOT NULL DEFAULT 1,
  subido_por INTEGER NOT NULL REFERENCES usuarios(id),
  subido_en TEXT NOT NULL
);

CREATE TABLE legitimaciones (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  metodo TEXT NOT NULL,             -- certificado_empresa | representante_pj | apoderamiento_aeat | colaborador_social
  evidencia_documento_id INTEGER REFERENCES documentos(id),
  declarado_por INTEGER NOT NULL REFERENCES usuarios(id),
  declarado_en TEXT NOT NULL,
  notas TEXT
);

CREATE TABLE autorizaciones_produccion (
  id INTEGER PRIMARY KEY,
  empresa_id INTEGER NOT NULL REFERENCES empresas(id),
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
  en TEXT NOT NULL,
  resumen TEXT NOT NULL,            -- JSON: empresa, documentos y datos que se comunicarán
  texto_confirmacion TEXT NOT NULL
);

CREATE TABLE declaraciones (
  id INTEGER PRIMARY KEY,
  version_sif TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'borrador', -- borrador | formalizada
  contenido TEXT NOT NULL,
  documento_firmado_ruta TEXT,
  formalizada_por TEXT,
  formalizada_en TEXT
);

CREATE TABLE actividad (
  id INTEGER PRIMARY KEY,
  en TEXT NOT NULL,
  usuario_id INTEGER,
  empresa_id INTEGER,
  accion TEXT NOT NULL,
  objeto TEXT,
  objeto_id INTEGER,
  detalle TEXT
);
CREATE INDEX actividad_empresa ON actividad(empresa_id, en);

-- Inmutabilidad de lo expedido -----------------------------------------------
CREATE TRIGGER facturas_no_borrar BEFORE DELETE ON facturas
BEGIN SELECT RAISE(ABORT, 'Las facturas expedidas no se pueden borrar'); END;

CREATE TRIGGER facturas_inmutables BEFORE UPDATE OF empresa_id, entorno, num_serie, fecha_expedicion,
  tipo_factura, emisor_nif, destinatario_nif, cuota_total, importe_total, datos, datos_sha256, url_qr,
  clave_idempotencia, borrador_id ON facturas
BEGIN SELECT RAISE(ABORT, 'Los datos de una factura expedida son inmutables'); END;

CREATE TRIGGER facturas_pdf_una_vez BEFORE UPDATE OF pdf_sha256 ON facturas
WHEN OLD.pdf_sha256 IS NOT NULL AND OLD.pdf_sha256 <> NEW.pdf_sha256
BEGIN SELECT RAISE(ABORT, 'El PDF definitivo no se puede sustituir'); END;

CREATE TRIGGER registros_no_borrar BEFORE DELETE ON registros
BEGIN SELECT RAISE(ABORT, 'Los registros de facturación no se pueden borrar'); END;

CREATE TRIGGER registros_inmutables BEFORE UPDATE OF empresa_id, entorno, instalacion_id, secuencia,
  factura_id, tipo, id_emisor, num_serie, fecha_expedicion, tipo_factura, cuota_total, importe_total,
  huella_anterior, fecha_hora_huso, huella, cadena_huella, xml, xml_sha256, subsanacion, rechazo_previo,
  sin_registro_previo ON registros
BEGIN SELECT RAISE(ABORT, 'Los registros de facturación son inmutables'); END;

CREATE TRIGGER actividad_solo_anadir BEFORE UPDATE ON actividad
BEGIN SELECT RAISE(ABORT, 'El registro de actividad no se modifica'); END;
