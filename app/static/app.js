// FacturaPDF VF — interfaz web para la expedición de facturas VERI*FACTU.
// Módulo ES sin dependencias ni paso de compilación.
//
// Seguridad: todo texto que procede del servidor o de los documentos importados se inserta
// con textContent / createElement (nunca innerHTML). No hay estilos en línea en HTML: solo se
// usan propiedades CSSOM desde JS (compatible con la CSP `style-src 'self'`).

const APP_NOMBRE = 'FacturaPDF VF';
const UMBRAL_CONFIANZA = 0.85;
const LS_EMPRESA = 'vf.empresa';
const LS_ENTORNO = 'vf.entorno';
const MAX_PAGINAS_SONDEO = 30;

/* ======================================================================================
 * Textos y etiquetas
 * ====================================================================================== */

const ENTORNOS = {
  simulador: {
    corto: 'Simulador local', tono: 'simulador',
    franja: 'Simulador local: las facturas quedan solo en esta instalación y nada se envía a la AEAT.',
    destino: 'al simulador local (no es la AEAT; no tiene efectos fiscales)',
  },
  pruebas: {
    corto: 'Pruebas AEAT', tono: 'pruebas',
    franja: 'Entorno de PRUEBAS de la AEAT: los registros se envían al servicio de preproducción, sin efectos fiscales.',
    destino: 'al entorno de pruebas de la AEAT (sin efectos fiscales)',
  },
  produccion: {
    corto: 'PRODUCCIÓN', tono: 'produccion',
    franja: 'PRODUCCIÓN: las facturas que expida son reales, tienen efectos fiscales y se remiten a la AEAT.',
    destino: 'a la AEAT (PRODUCCIÓN, con plenos efectos fiscales)',
  },
};

const ESTADO_FACTURA = {
  borrador: ['Borrador', 'neutral'],
  requiere_revision: ['Requiere revisión', 'warn'],
  emitida: ['Emitida', 'ok'],
  anulada: ['Anulada', 'danger'],
  rectificada: ['Rectificada', 'info'],
};

const ESTADO_ENVIO = {
  pendiente: ['Envío pendiente', 'neutral'],
  enviando: ['Enviando', 'info'],
  sin_confirmar: ['Resultado sin confirmar', 'warn'],
  aceptado: ['Registro aceptado', 'ok'],
  aceptado_con_errores: ['Aceptado con errores', 'warn'],
  rechazado: ['Rechazado', 'danger'],
  bloqueado: ['Bloqueado (requiere acción)', 'danger'],
};

const ESTADO_BORRADOR = {
  requiere_revision: ['Requiere revisión', 'warn'],
  listo: ['Listo para emitir', 'ok'],
  emitido: ['Emitido', 'info'],
  descartado: ['Descartado', 'neutral'],
};

const ESTADO_DOCUMENTO = {
  pendiente: ['Pendiente de procesar', 'neutral'],
  procesado: ['Procesado', 'ok'],
  error: ['Error', 'danger'],
};

const RESULTADO_ENVIO = {
  Correcto: ['Correcto', 'ok'],
  ParcialmenteCorrecto: ['Parcialmente correcto', 'warn'],
  Incorrecto: ['Incorrecto', 'danger'],
  fault: ['Error SOAP (Fault)', 'danger'],
  sin_respuesta: ['Sin respuesta', 'warn'],
  error_red: ['Error de red', 'danger'],
};

const CLASIFICACIONES = {
  preparatorio: {
    etiqueta: 'Documento preparatorio', tono: 'info',
    explicacion: 'Borrador, proforma o presupuesto preparado para expedirse con esta aplicación: se revisarán sus datos y, '
      + 'al emitir, se generará la factura definitiva con su registro VERI*FACTU.',
  },
  emitida_propia: {
    etiqueta: 'Factura ya expedida (propia)', tono: 'neutral',
    explicacion: 'Factura ya expedida: se archiva; no se añade QR ni se comunica de nuevo',
  },
  recibida: {
    etiqueta: 'Factura recibida (de proveedor)', tono: 'neutral',
    explicacion: 'Factura de proveedor: se conserva; no se comunica como venta propia',
  },
  desconocido: {
    etiqueta: 'Desconocido / no procesar', tono: 'neutral',
    explicacion: 'No se procesa fiscalmente: el documento queda archivado sin más efectos.',
  },
};

const METODOS = { texto_pdf: 'Texto PDF', ocr: 'OCR', calculado: 'Calculado', no_encontrado: 'No encontrado' };

const TIPOS_CONTRIBUYENTE = [
  ['sociedad_IS', 'Sociedad (Impuesto sobre Sociedades)'],
  ['persona_fisica_IRPF', 'Persona física con actividad económica (IRPF)'],
  ['entidad_renta', 'Entidad en régimen de atribución de rentas'],
  ['otro', 'Otro'],
];
const TERRITORIOS = [
  ['comun', 'Territorio común'],
  ['pais_vasco', 'País Vasco (territorio foral)'],
  ['navarra', 'Navarra (territorio foral)'],
];
const ORIGENES_PDF = [
  ['plantilla_ofimatica', 'Plantilla ofimática (Word, Excel, LibreOffice…)'],
  ['programa_facturacion', 'Programa de facturación'],
  ['otro', 'Otro origen'],
];
const RELACIONES = [
  ['administrador', 'Administrador de la empresa'],
  ['representante', 'Representante (legal o voluntario)'],
  ['empleado_autorizado', 'Empleado autorizado'],
  ['asesor', 'Asesor o gestor externo'],
];
const TIPOS_CERTIFICADO = {
  persona_juridica: 'Certificado de persona jurídica',
  representante_pj: 'Representante de persona jurídica',
  persona_fisica: 'Persona física',
  sello: 'Sello de entidad',
  desconocido: 'Tipo no reconocido',
};
const MODOS_SIMULADOR = [
  ['normal', 'Normal', 'Responde como el servicio: acepta o rechaza cada registro según sus validaciones.'],
  ['sin_respuesta', 'Sin respuesta', 'Procesa la petición pero no responde (tiempo agotado): el resultado queda sin confirmar.'],
  ['caido', 'Servicio caído', 'No acepta la conexión: la petición no llega a procesarse.'],
  ['error_500', 'Error 500', 'Responde con un error interno del servidor (HTTP 500): el resultado queda sin confirmar.'],
  ['fault_servidor', 'Fallo del servicio (SOAP Server)', 'Responde con un SOAP Fault de servidor: se trata como sin respuesta y se reintenta.'],
  ['rechazo', 'Rechazo del envío completo', 'Rechaza la petición entera (p. ej. certificado no autorizado, 4112): los registros quedan bloqueados.'],
  ['rechazo_registro', 'Rechazo de registros', 'Rechaza cada registro con un error de validación (1100): permite probar la subsanación y la reanulación.'],
  ['no_apoderado', 'Representante no apoderado', 'Rechaza (4112) los envíos que llevan Cabecera/Representante: simula que el titular del certificado no está apoderado ni es colaborador social.'],
  ['representante_nif_no_censado', 'NIF del representante no censado', 'Rechaza (4123) los envíos que llevan Cabecera/Representante: simula que la AEAT no encuentra en su censo el NIF del titular del certificado.'],
  ['representante_nombre_no_censado', 'Nombre del representante no censado', 'Rechaza (4124) los envíos que llevan Cabecera/Representante: simula que el nombre enviado no es el que la AEAT tiene en su censo para ese NIF.'],
];
const REFERENCIAS = [
  ['obra', 'Obra'],
  ['certificacion', 'Certificación nº'],
  ['pedido', 'Pedido'],
  ['partida_presupuestaria', 'Partida presupuestaria'],
  ['periodo', 'Periodo facturado'],
  ['contrato', 'Contrato'],
  ['proyecto', 'Proyecto'],
  ['expediente', 'Expediente'],
];
const COLUMNAS_LINEA = { concepto: 'Concepto', cantidad: 'Cantidad', unidad: 'Unidad', precio: 'Precio', importe: 'Importe' };
const COLUMNAS_DESGLOSE = { tipo_iva: 'Tipo IVA %', base: 'Base', cuota: 'Cuota' };
const ETIQUETAS_CAMPO = {
  num_serie: 'Número de factura',
  fecha_documento: 'Fecha del documento',
  fecha_operacion: 'Fecha de operación',
  descripcion_operacion: 'Descripción de la operación',
  'emisor.nombre': 'Emisor: razón social', 'emisor.nif': 'Emisor: NIF', 'emisor.domicilio': 'Emisor: domicilio',
  'destinatario.nombre': 'Destinatario: razón social', 'destinatario.nif': 'Destinatario: NIF',
  'destinatario.domicilio': 'Destinatario: domicilio',
  base_imponible: 'Base imponible', cuota_total: 'Cuota de IVA', importe_total: 'Importe total',
  total_a_pagar: 'Total a pagar', retencion: 'Retención', 'retencion.porcentaje': 'Retención: porcentaje',
  'retencion.importe': 'Retención: importe', forma_pago: 'Forma de pago', iban: 'IBAN', vencimiento: 'Vencimiento',
  observaciones: 'Observaciones', tipo_documento: 'Tipo de documento', tipo_iva: 'Tipo de IVA',
  lineas: 'Líneas de detalle', desglose: 'Desglose de IVA', documento: 'Documento de origen', empresa: 'Empresa',
  indicios: 'Particularidades del documento', referencias: 'Referencias',
};
const INDICIOS = {
  exencion: 'Operación exenta', inversion_sujeto_pasivo: 'Inversión del sujeto pasivo', no_sujeta: 'Operación no sujeta',
  recargo_equivalencia: 'Recargo de equivalencia', suplidos: 'Suplidos', descuento: 'Descuento',
  rectificativa: 'Rectificativa / abono', intracomunitaria: 'Operación intracomunitaria', criterio_caja: 'Criterio de caja',
  regimen_especial: 'Régimen especial', verifactu_existente: 'Marcas de VERI*FACTU ya presentes',
  importes_ajenos: 'Tasas, fianzas, indemnizaciones o gastos repercutidos',
};
const NOTA_ACEPTACION = 'La aceptación del registro por la AEAT no supone una validación del tratamiento fiscal de la operación.';

/* ======================================================================================
 * Utilidades
 * ====================================================================================== */

/** Crea un elemento. Los hijos de texto se insertan como nodos de texto (nunca como HTML). */
function h(etiqueta, props, ...hijos) {
  const el = document.createElement(etiqueta);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : v;
      else if (k === 'text') el.textContent = String(v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (['value', 'checked', 'disabled', 'selected', 'multiple', 'required', 'readOnly', 'hidden', 'open', 'indeterminate'].includes(k)) el[k] = v;
      else if (k === 'style') throw new Error('No se permiten estilos en línea');
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
  }
  return anadir(el, hijos);
}

function anadir(el, hijos) {
  for (const c of [hijos].flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === '') continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const vaciar = (el) => { el.replaceChildren(); return el; };
let contadorId = 0;
const uid = (p = 'c') => `${p}-${++contadorId}`;
const enc = (v) => encodeURIComponent(String(v));
const reducirMovimiento = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const esEscritorio = () => window.matchMedia('(min-width: 1024px)').matches;

function leerLS(clave) {
  try { return window.localStorage.getItem(clave); } catch { return null; }
}
function guardarLS(clave, valor) {
  try { window.localStorage.setItem(clave, valor); } catch { /* almacenamiento no disponible */ }
}

function nuevoUUID() {
  try {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
  } catch { /* contexto no seguro: se usa el método alternativo */ }
  const b = window.crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const x = Array.from(b, (n) => n.toString(16).padStart(2, '0')).join('');
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}

function parseJSON(v) {
  if (v && typeof v === 'object') return v;
  if (typeof v !== 'string' || !v.trim()) return null;
  try { return JSON.parse(v); } catch { return null; }
}

function obtener(obj, ruta) {
  let o = obj;
  for (const p of String(ruta).split('.')) {
    if (o === null || o === undefined) return undefined;
    o = o[/^\d+$/.test(p) ? Number(p) : p];
  }
  return o;
}

function comoLista(r, ...claves) {
  if (Array.isArray(r)) return r;
  if (r && typeof r === 'object') {
    for (const k of [...claves, 'items', 'resultados', 'datos']) if (Array.isArray(r[k])) return r[k];
  }
  return [];
}

function fmtImporte(v, moneda = true) {
  if (v === null || v === undefined || v === '') return '—';
  const s = String(v).trim();
  const m = /^([-−]?)(\d+)(?:\.(\d+))?$/.exec(s);
  if (!m) return s;
  const entero = m[2].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const dec = (m[3] || '').padEnd(2, '0');
  return `${m[1] ? '−' : ''}${entero},${dec}${moneda ? ' €' : ''}`;
}

function fmtFecha(v) {
  if (!v) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v));
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  const m2 = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(v));
  if (m2) return `${m2[1]}/${m2[2]}/${m2[3]}`;
  return fmtFechaHora(v);
}

/** Las fechas y horas se muestran en hora peninsular (la de la aplicación), sea cual sea la zona del navegador. */
const ZONA_HORARIA = 'Europe/Madrid';

function fmtFechaHora(v) {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short', timeZone: ZONA_HORARIA });
}

/** Solo el día (dd/mm/aaaa) de una fecha o de una marca de tiempo, en hora peninsular. */
function fmtDia(v) {
  if (!v || /^\d{4}-\d{2}-\d{2}$/.test(String(v))) return fmtFecha(v);
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: ZONA_HORARIA });
}

const fmtPct = (c) => `${Math.round(Number(c) * 100)} %`;

function hoyISO() {
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: ZONA_HORARIA }).format(new Date());
}

/** Año y mes de hoy en hora peninsular (no los del reloj del navegador, que puede estar en otra zona horaria). */
function anioMesHoy() {
  const [anio, mes] = hoyISO().split('-').map(Number);
  return { anio, mes };
}

function textoValor(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Sí' : 'No';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function humanizar(clave) {
  const s = String(clave).replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Nombres de las comprobaciones del PDF definitivo (pdf_final.verificar) en «Verificación de integridad». */
const ETIQUETAS_VERIFICACION = {
  num_serie: 'Número de factura', importe_total: 'Importe total', nif_emisor: 'NIF del emisor',
  nif_destinatario: 'NIF del destinatario', fecha_expedicion: 'Fecha de expedición', leyenda_qr: 'Leyenda del QR',
  mencion_exencion: 'Mención de la exención', lectura_qr: 'Lectura del QR', qr_primera_pagina: 'QR en la primera página',
  qr_una_sola_vez: 'QR una sola vez', sin_qr: 'Sin código QR',
};

/** Nombres de los datos del detalle del registro de actividad (empresa y plataforma). */
const ETIQUETAS_DETALLE = {
  email: 'Correo', rol: 'Rol', dias: 'Días', usuario: 'Usuario', invitacion: 'Invitación', propia: 'Por decisión propia',
  entorno: 'Entorno', motivo: 'Motivo', clave: 'Ajuste', valor: 'Valor', nif: 'NIF', nombre: 'Nombre', tipo: 'Tipo',
  titular_nif: 'NIF del titular', titular_nombre: 'Nombre del titular', representante_nif: 'NIF del representante',
  representado_nif: 'NIF representado', valido_hasta: 'Válido hasta', asignacion_id: 'Asignación',
  asignaciones_retiradas: 'Asignaciones retiradas', propietario_id: 'Propietario', propio: 'Certificado propio',
  operacion: 'Operación', envio_id: 'Envío', tls: 'Conexión TLS', num_serie: 'Número de factura', registro_id: 'Registro',
  factura: 'Factura', factura_id: 'Factura', relacion: 'Relación', clasificacion: 'Clasificación',
  clasificacion_sugerida: 'Clasificación propuesta', paginas: 'Páginas', ocr: 'OCR', sha256: 'Huella SHA-256',
  claves: 'Confirmaciones', corrige: 'Corrige el registro', afectados: 'Registros afectados',
  siguiente_intento_en: 'Siguiente intento', error: 'Error', ruta: 'Ruta', evidencia: 'Evidencia',
  estado_aeat: 'Estado en la AEAT', estado_anterior: 'Estado anterior', estado_nuevo: 'Estado nuevo', huella: 'Huella',
  id_peticion: 'Petición', ejercicio: 'Ejercicio', periodo: 'Periodo', resumen: 'Resumen', metodo: 'Método',
  anio: 'Año', anterior: 'Contador anterior', siguiente: 'Siguiente número', saltados: 'Números saltados',
  codigo: 'Código', patron: 'Patrón', cuenta: 'Cuenta', por: 'Por', pdf: 'PDF', verificacion_pdf: 'Verificación del PDF',
  modo: 'Modo', veces: 'Veces', servicio: 'Servicio', razon_social: 'Razón social', domicilio: 'Domicilio',
  tipo_contribuyente: 'Tipo de contribuyente', territorio: 'Territorio', sii: 'SII',
  canarias_ceuta_melilla: 'Canarias, Ceuta o Melilla', otras_exclusiones: 'Otras exclusiones',
  origen_pdf: 'Origen de los PDF', origen_pdf_detalle: 'Detalle del origen', puede_emitir: 'Puede emitir',
  motivos: 'Motivos', siguiente_numero: 'Siguiente número', facturas: 'Facturas', registros: 'Registros',
  estados: 'Estados', contadores_avanzados: 'Contadores avanzados', filas_contexto: 'Filas de contexto',
  anotacion: 'Anotación', id: 'Identificador', lineas: 'Líneas',
  ultimo_registro_aeat: 'Último registro en la AEAT', registros_posteriores: 'Registros posteriores',
  cabeza_local: 'Último registro local', ancla: 'Encadenado a', fecha_expedicion: 'Fecha de expedición',
  fecha_hora_huso: 'Fecha, hora y huso', verificado: 'Verificado', filas: 'Filas', aviso: 'Aviso',
  cadenas_aeat: 'Estados de la cadena recuperados', copia: 'Copia', empresas_pendientes: 'Empresas pendientes de consultar',
};

/** Por qué puede usarse un certificado en la empresa: propio (de la empresa, de su representante o del titular) o de un tercero legitimado. */
const METODO_LEGITIMACION_TEXTO = {
  apoderamiento_aeat: 'Apoderamiento en la AEAT', colaborador_social: 'Colaboración social',
  certificado_empresa: 'Certificado de la empresa', representante_pj: 'Certificado de representante de la empresa',
  titular: 'Titular del certificado (el obligado tributario)',
};

/** Tipos de objeto del registro de actividad. */
const OBJETOS_ACTIVIDAD = {
  factura: 'Factura', borrador: 'Borrador', documento: 'Documento', invitacion: 'Invitación', certificado: 'Certificado',
  certificado_usuario: 'Certificado de «Mis certificados»', legitimacion: 'Legitimación', usuario: 'Usuario',
  registro: 'Registro', serie: 'Serie',
};

/** Abrevia una huella: primeros 10 y últimos 6 caracteres. */
function abreviar(t, ini = 10, fin = 6) {
  const s = String(t || '');
  return s.length > ini + fin + 1 ? `${s.slice(0, ini)}…${s.slice(-fin)}` : s;
}

/**
 * Normaliza un número introducido por el usuario. Acepta «1.028,50», «1028,50» o «1028.50».
 * Devuelve {ok, valor} con punto decimal; con `dec2` fuerza 2 decimales (importes).
 */
function normalizarNumero(texto, { dec2 = false, maxDec = 6 } = {}) {
  let t = String(texto ?? '').trim().replace(/\s|€/g, '').replace('−', '-');
  if (t === '') return { ok: true, valor: '' };
  if (t.includes(',') && t.includes('.')) {
    t = t.lastIndexOf(',') > t.lastIndexOf('.') ? t.replace(/\./g, '').replace(',', '.') : t.replace(/,/g, '');
  } else if (t.includes(',')) {
    t = t.replace(',', '.');
  }
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(t);
  if (!m) return { ok: false, error: 'Introduzca un número (use coma o punto para los decimales).' };
  const decimales = m[3] || '';
  if (dec2) {
    if (decimales.length > 2) return { ok: false, error: 'Los importes admiten como máximo 2 decimales.' };
    return { ok: true, valor: `${m[1]}${m[2].replace(/^0+(?=\d)/, '')}.${decimales.padEnd(2, '0')}` };
  }
  if (decimales.length > maxDec) return { ok: false, error: `Como máximo ${maxDec} decimales.` };
  return { ok: true, valor: `${m[1]}${m[2].replace(/^0+(?=\d)/, '')}${decimales ? `.${decimales}` : ''}` };
}

/* ======================================================================================
 * Estado de la aplicación y acceso a la API
 * ====================================================================================== */

const app = {
  estado: null,       // GET /api/estado
  usuario: null,
  csrf: null,         // solo en memoria
  empresas: [],
  empresaId: null,
  entorno: 'simulador',
  volverA: null,
  alCambiarEntorno: null,
  avisos: [],         // incidencias de comunicación de la empresa (GET /api/empresas/{eid} → avisos)
  avisosEn: 0,
};

class ApiError extends Error {
  constructor(estado, mensaje, datos) {
    super(mensaje);
    this.estado = estado;
    this.datos = datos;
  }
}

function mensajeDeError(datos, estado) {
  const d = datos && typeof datos === 'object' ? datos.detail : null;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map((e) => {
      if (typeof e === 'string') return e;
      const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== 'body').join('.') : '';
      return (loc ? `${loc}: ` : '') + (e.msg || e.mensaje || JSON.stringify(e));
    }).join(' · ');
  }
  if (d && typeof d === 'object') {
    const txt = [d.codigo, d.mensaje || d.message || d.descripcion].filter(Boolean).join(': ');
    return txt || JSON.stringify(d);
  }
  if (typeof datos === 'string' && datos.trim() && datos.length < 400 && !/^\s*</.test(datos)) return datos.trim();
  const genericos = {
    400: 'Petición no válida', 401: 'Sesión no iniciada o caducada', 403: 'No tiene permiso para esta acción',
    404: 'No encontrado', 409: 'Conflicto con el estado actual', 413: 'El archivo es demasiado grande',
    422: 'Datos no válidos', 429: 'Demasiadas peticiones; espere un momento', 500: 'Error interno del servidor',
    502: 'Servidor no disponible', 503: 'Servicio no disponible',
  };
  return `${genericos[estado] || 'Error'} (HTTP ${estado})`;
}

/** Datos adicionales de un error (p. ej. incidencias devueltas al emitir). */
function detalleExtra(err) {
  const d = err && err.datos && typeof err.datos === 'object' ? err.datos.detail : null;
  if (d && typeof d === 'object' && !Array.isArray(d) && Array.isArray(d.incidencias)) return d.incidencias;
  if (err && err.datos && Array.isArray(err.datos.incidencias)) return err.datos.incidencias;
  return null;
}

/**
 * Llamada a la API: envía la cookie de sesión, JSON y la cabecera CSRF en peticiones que no son GET.
 * Ante 401 lleva a la pantalla de inicio de sesión (salvo `sinRedireccion`).
 */
async function api(ruta, { metodo = 'GET', json, form, sinRedireccion = false } = {}) {
  const cabeceras = { Accept: 'application/json' };
  let cuerpo;
  if (json !== undefined) {
    cabeceras['Content-Type'] = 'application/json';
    cuerpo = JSON.stringify(json);
  } else if (form) {
    cuerpo = form;
  }
  if (metodo !== 'GET' && metodo !== 'HEAD' && app.csrf) cabeceras['X-CSRF-Token'] = app.csrf;
  let r;
  try {
    r = await fetch(`/api${ruta}`, { method: metodo, headers: cabeceras, body: cuerpo, credentials: 'same-origin', cache: 'no-store' });
  } catch {
    throw new ApiError(0, 'No se puede conectar con el servidor. Compruebe la conexión e inténtelo de nuevo.');
  }
  let datos = null;
  if (r.status !== 204) {
    const tipo = r.headers.get('content-type') || '';
    try { datos = tipo.includes('json') ? await r.json() : await r.text(); } catch { datos = null; }
  }
  if (r.status === 401 && !sinRedireccion) {
    sesionPerdida();
    throw new ApiError(401, 'La sesión ha caducado. Inicie sesión de nuevo.', datos);
  }
  if (!r.ok) {
    const err = new ApiError(r.status, mensajeDeError(datos, r.status), datos);
    // Un 404 de la empresa elegida puede deberse a que otro administrador le ha quitado el acceso
    if (r.status === 404 && app.empresaId && new RegExp(`^/empresas/${enc(app.empresaId)}(?:[/?]|$)`).test(ruta)) {
      err.accesoComprobado = true;
      err.sinAcceso = await comprobarAccesoEmpresa();
    }
    throw err;
  }
  return datos;
}

let comprobacionAcceso = null;

/**
 * Vuelve a leer la sesión (GET /api/yo) cuando la empresa elegida responde 404. Si ya no es miembro, aplica la sesión,
 * lo avisa y lleva a la pantalla de inicio (sin dejar un «Reintentar» que siempre falla). Devuelve true en ese caso.
 */
function comprobarAccesoEmpresa() {
  if (!comprobacionAcceso) {
    comprobacionAcceso = (async () => {
      if (!app.usuario || !app.empresaId) return false;
      let yo;
      try { yo = await api('/yo'); } catch { return false; }
      if (!sincronizarEmpresas(yo)) return false;
      // En una pantalla de la empresa, al inicio; en una que no depende de ella (Mi cuenta, Mis certificados…), se repinta
      const { ruta } = analizarHash();
      const def = RUTAS.find(([re]) => re.test(ruta));
      if (def && def[2] && (def[2].sinEmpresa || def[2].publica)) enrutar({ forzar: true });
      else irA(rutaInicioMU());
      return true;
    })().finally(() => { comprobacionAcceso = null; });
  }
  return comprobacionAcceso;
}

/**
 * Aplica la lista de empresas de GET /api/yo. Si la empresa elegida ya no está (le han quitado el acceso o ha salido
 * desde otra ventana), vuelve a aplicar la sesión y lo avisa: devuelve true. Si no tenía ninguna elegida y ahora tiene
 * alguna (p. ej. aceptó una invitación en otra ventana), la elige como al entrar y lo avisa. Si no, solo actualiza la
 * lista y los roles.
 */
function sincronizarEmpresas(yo) {
  const lista = comoLista(yo && yo.empresas);
  if (app.empresaId && !lista.some((e) => String(e.id) === String(app.empresaId))) {
    const antes = empresaActual();
    guardiaSalida = null;
    aplicarSesion(yo);
    notificar(`Ya no tiene acceso a esa empresa${antes && antes.razon_social ? ` (${antes.razon_social})` : ''}.`, 'warn');
    return true;
  }
  if (!app.empresaId && lista.length) {
    aplicarSesion(yo);
    const nueva = empresaActual();
    notificar(`Ya tiene acceso a ${nueva ? nueva.razon_social : 'una empresa'}: la tiene elegida arriba, en «Empresa».`, 'info');
    return false;
  }
  app.empresas = lista;
  construirCabecera();
  return false;
}

/** Privilegio de instalación (productor, alta de empresas, simulador): no basta con administrar una empresa. */
const esSuperadmin = () => !!(app.usuario && app.usuario.superadmin);
const empresaActual = () => app.empresas.find((e) => String(e.id) === String(app.empresaId)) || null;
const rolActual = () => (empresaActual() || {}).rol || 'lector';
const esAdmin = () => rolActual() === 'admin';
const puedeEmitirRol = () => ['admin', 'emisor'].includes(rolActual());

function entornosHabilitados() {
  const e = app.estado && app.estado.entornos_habilitados;
  let lista;
  if (Array.isArray(e)) lista = e.filter((k) => ENTORNOS[k]);
  else if (e && typeof e === 'object') lista = Object.keys(ENTORNOS).filter((k) => e[k]);
  else lista = ['simulador'];
  return lista.length ? lista : ['simulador'];
}

/* ======================================================================================
 * Componentes de interfaz
 * ====================================================================================== */

function pill(texto, tono = 'neutral', titulo) {
  return h('span', { class: `pill pill-${tono}`, title: titulo }, texto);
}

function pillDe(mapa, clave, titulo) {
  const v = mapa[clave];
  return v ? pill(v[0], v[1], titulo) : pill(clave ? String(clave) : '—', 'neutral', titulo);
}

function badgeEntorno(entorno, grande = false) {
  const e = ENTORNOS[entorno];
  return h('span', { class: ['badge-entorno', `ent-${e ? e.tono : 'desconocido'}`, grande && 'badge-grande'] },
    grande ? h('span', { class: 'badge-pre' }, 'Entorno: ') : null, e ? e.corto : String(entorno || '—'));
}

function boton(texto, onclick, { tipo = 'secundario', pequeno = false, ...props } = {}) {
  return h('button', { type: 'button', class: ['btn', `btn-${tipo}`, pequeno && 'btn-pequeno'], onclick, ...props }, texto);
}

function enlace(texto, href, { tipo, pequeno = false, descarga = false, ...props } = {}) {
  return h('a', {
    href, class: [tipo && 'btn', tipo && `btn-${tipo}`, pequeno && 'btn-pequeno'], download: descarga ? '' : null, ...props,
  }, texto);
}

function tarjeta(titulo, ...contenido) {
  return h('section', { class: 'card' }, titulo ? h('h2', { class: 'card-titulo' }, titulo) : null, contenido);
}

function alerta(tono, ...contenido) {
  return h('div', { class: `alerta alerta-${tono}`, role: tono === 'danger' ? 'alert' : null }, contenido);
}

function kv(pares) {
  const dl = h('dl', { class: 'kv' });
  for (const [k, v] of pares) {
    if (v === undefined) continue;
    dl.append(h('dt', null, k), h('dd', null, v instanceof Node ? v : textoValor(v)));
  }
  return dl;
}

/** Tabla con contenedor desplazable (evita el desplazamiento horizontal de la página). */
function tabla(columnas, filas, { vacio = 'No hay elementos.', clase, leyenda } = {}) {
  if (!filas.length) return h('p', { class: 'vacio' }, vacio);
  const t = h('table', { class: ['tabla', clase] },
    leyenda ? h('caption', { class: 'sr-only' }, leyenda) : null,
    h('thead', null, h('tr', null, columnas.map((c) => h('th', { scope: 'col', class: c.clase }, c.titulo)))),
    h('tbody', null, filas.map((f, i) => h('tr', null, columnas.map((c) => {
      const v = c.celda(f, i);
      return h('td', { class: c.clase, 'data-col': typeof c.titulo === 'string' ? c.titulo : null }, v instanceof Node || Array.isArray(v) ? v : textoValor(v));
    })))));
  return h('div', { class: 'tabla-wrap' }, t);
}

function cargando(texto = 'Cargando…') {
  return h('div', { class: 'cargando', role: 'status' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), texto);
}

function cajaError(err, reintentar) {
  return alerta('danger',
    h('p', null, h('strong', null, 'No se pudo completar la operación. '), err && err.message ? err.message : String(err)),
    reintentar ? boton('Reintentar', reintentar, { pequeno: true }) : null);
}

function notificar(mensaje, tono = 'info') {
  const zona = document.getElementById('notificaciones');
  if (!zona) return;
  const el = h('div', { class: `toast toast-${tono}`, role: tono === 'danger' ? 'alert' : 'status' },
    h('span', { class: 'toast-texto' }, mensaje),
    h('button', { type: 'button', class: 'toast-cerrar', 'aria-label': 'Cerrar aviso', onclick: () => el.remove() }, '×'));
  zona.append(el);
  while (zona.children.length > 4) zona.firstElementChild.remove();
  window.setTimeout(() => el.remove(), tono === 'danger' ? 15000 : 6000);
}

const nombreApp = () => (app.estado && app.estado.nombre) || APP_NOMBRE;

function titulo(t) {
  document.title = `${t} · ${nombreApp()}`;
}

function cabeceraPagina(textoTitulo, { meta = [], acciones = [], volver } = {}) {
  return h('div', { class: 'pagina-cabecera' },
    volver ? h('a', { class: 'volver', href: volver[0] }, `← ${volver[1]}`) : null,
    h('div', { class: 'pagina-cabecera-fila' },
      h('div', { class: 'pagina-cabecera-texto' }, h('h1', null, textoTitulo), meta.length ? h('div', { class: 'meta' }, meta) : null),
      acciones.length ? h('div', { class: 'acciones' }, acciones) : null));
}

/* ---------- Formularios ---------- */

function campo(etiqueta, control, { ayuda, clase } = {}) {
  if (!control.id) control.id = uid('f');
  let ayudaEl = null;
  if (ayuda) {
    ayudaEl = h('p', { class: 'ayuda', id: `${control.id}-ayuda` }, ayuda);
    control.setAttribute('aria-describedby', ayudaEl.id);
  }
  return h('div', { class: ['campo', clase] }, h('label', { for: control.id }, etiqueta), control, ayudaEl);
}

function entrada(props = {}) {
  return h('input', { type: 'text', ...props });
}

function selector(opciones, valor, props = {}) {
  return h('select', props, opciones.map(([v, t, extra = {}]) => h('option', { value: v, selected: String(v) === String(valor ?? ''), ...extra }, t)));
}

/** Grupo de opciones (radio) con descripción opcional por opción. */
function grupoRadios(nombre, leyenda, opciones, valor, { onchange, ayuda } = {}) {
  const fs = h('fieldset', { class: 'radios' }, h('legend', null, leyenda));
  if (ayuda) fs.append(h('p', { class: 'ayuda' }, ayuda));
  for (const [v, t, desc] of opciones) {
    const id = uid('r');
    const input = h('input', { type: 'radio', name: nombre, id, value: v, checked: String(v) === String(valor ?? ''), onchange });
    const descEl = desc ? h('span', { class: 'radio-desc', id: `${id}-d` }, desc) : null;
    if (descEl) input.setAttribute('aria-describedby', descEl.id);
    fs.append(h('div', { class: 'radio-opcion' }, input, h('label', { for: id }, h('span', { class: 'radio-titulo' }, t), descEl)));
  }
  return fs;
}

function valorRadio(form, nombre) {
  const r = form.querySelector(`input[type="radio"][name="${nombre}"]:checked`);
  return r ? r.value : null;
}

/** Desactiva el botón mientras dura la operación (protección frente a doble clic). */
async function conEspera(btn, fn, textoEspera) {
  if (!btn || btn.dataset.ocupado === '1') return undefined;
  btn.dataset.ocupado = '1';
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  const original = btn.textContent;
  if (textoEspera) btn.textContent = textoEspera;
  try {
    return await fn();
  } finally {
    btn.dataset.ocupado = '';
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    if (textoEspera) btn.textContent = original;
  }
}

/** Representación legible de un valor JSON arbitrario (objetos, listas, booleanos). */
function renderGenerico(valor, nivel = 0) {
  if (valor === null || valor === undefined || valor === '') return h('span', { class: 'muted' }, '—');
  if (typeof valor === 'boolean') return valor ? pill('Sí', 'ok') : pill('No', 'danger');
  if (Array.isArray(valor)) {
    if (!valor.length) return h('span', { class: 'muted' }, 'Ninguno');
    if (nivel > 3) return h('span', null, JSON.stringify(valor));
    return h('ul', { class: 'lista-simple' }, valor.map((v) => h('li', null, renderGenerico(v, nivel + 1))));
  }
  if (typeof valor === 'object') {
    if (nivel > 3) return h('span', null, JSON.stringify(valor));
    return kv(Object.entries(valor).map(([k, v]) => [humanizar(k), renderGenerico(v, nivel + 1)]));
  }
  return h('span', { class: 'valor-largo' }, String(valor));
}

/* ---------- Diálogos ---------- */

function dialogo({ titulo: t, contenido, acciones = [], clase, alCerrar }) {
  const idTitulo = uid('dlg');
  const previo = document.activeElement;
  const dlg = h('dialog', { class: ['dialogo', clase], 'aria-labelledby': idTitulo },
    h('div', { class: 'dialogo-cabecera' }, h('h2', { id: idTitulo }, t)),
    h('div', { class: 'dialogo-cuerpo' }, contenido),
    acciones.length ? h('div', { class: 'dialogo-acciones' }, acciones) : null);
  dlg.addEventListener('close', () => {
    dlg.remove();
    if (alCerrar) alCerrar();
    if (previo && typeof previo.focus === 'function' && document.contains(previo)) previo.focus();
  });
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

function confirmar({ titulo: t, texto, textoBoton = 'Confirmar', peligro = false, contenido }) {
  return new Promise((resolver) => {
    let resultado = false;
    const ok = boton(textoBoton, () => { resultado = true; dlg.close(); }, { tipo: peligro ? 'peligro' : 'primario' });
    const cancelar = boton('Cancelar', () => dlg.close());
    const dlg = dialogo({
      titulo: t,
      contenido: [texto ? h('p', null, texto) : null, contenido],
      acciones: [cancelar, ok],
      alCerrar: () => resolver(resultado),
    });
    // En una acción irreversible el foco empieza en «Cancelar»: un Intro de más (o una tecla mantenida) no la ejecuta
    (peligro ? cancelar : ok).focus();
  });
}

/** Pide un texto (p. ej. el motivo de una acción). Devuelve null si se cancela. */
function pedirTexto({ titulo: t, explicacion, etiqueta, textoBoton = 'Aceptar', multilinea = true, peligro = false, valor = '' }) {
  return new Promise((resolver) => {
    let resultado = null;
    const control = multilinea ? h('textarea', { rows: 3, required: true, maxlength: 500 }) : entrada({ required: true, maxlength: 500 });
    control.value = valor;
    const error = h('p', { class: 'error-campo', role: 'alert' });
    const aceptar = () => {
      const v = control.value.trim();
      if (!v) { error.textContent = 'Este dato es obligatorio.'; control.focus(); return; }
      resultado = v;
      dlg.close();
    };
    const form = h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); aceptar(); } },
      explicacion ? (explicacion instanceof Node ? explicacion : h('p', null, explicacion)) : null,
      campo(etiqueta, control), error);
    const dlg = dialogo({
      titulo: t, contenido: form,
      acciones: [boton('Cancelar', () => dlg.close()), boton(textoBoton, aceptar, { tipo: peligro ? 'peligro' : 'primario' })],
      alCerrar: () => resolver(resultado),
    });
    control.focus();
  });
}

function descargar(url) {
  const a = h('a', { href: url, download: '', class: 'oculto' });
  document.body.append(a);
  a.click();
  a.remove();
}

/* ======================================================================================
 * Estructura: cabecera, navegación y entorno
 * ====================================================================================== */

const NAVEGACION = [
  ['/importar', 'Documentos'],
  ['/facturas', 'Facturas'],
  ['/remision', 'Remisión'],
  ['/consulta-aeat', 'Consulta AEAT'],
  ['/configuracion', 'Configuración'],
  ['/mis-certificados', 'Mis certificados'],
  ['/actividad', 'Actividad'],
  ['/declaracion', 'Declaración responsable'],
];

function construirCabecera() {
  const cab = document.getElementById('cabecera');
  vaciar(cab);
  document.body.dataset.entorno = app.usuario ? app.entorno : '';
  if (!app.usuario || app.usuario.cambiar_clave) { cab.hidden = true; return; }
  cab.hidden = false;
  const habilitados = entornosHabilitados();
  const selEmpresa = h('select', { id: 'sel-empresa', onchange: (e) => cambiarEmpresa(e.target.value, e.target) },
    app.empresas.length
      ? app.empresas.map((e) => h('option', { value: e.id, selected: String(e.id) === String(app.empresaId) }, `${e.razon_social} · ${e.nif}`))
      : h('option', { value: '' }, 'Sin empresas'));
  const selEntorno = h('select', { id: 'sel-entorno', onchange: (e) => cambiarEntorno(e.target.value, e.target) },
    Object.keys(ENTORNOS).map((k) => h('option', { value: k, selected: k === app.entorno, disabled: !habilitados.includes(k) },
      ENTORNOS[k].corto + (habilitados.includes(k) ? '' : ' (no habilitado)'))));
  const barra = h('div', { class: 'barra' },
    h('a', { class: 'marca', href: '#/importar' }, h('span', { class: 'marca-nombre' }, nombreApp()), h('span', { class: 'marca-sub' }, 'VERI*FACTU')),
    h('div', { class: 'barra-controles' },
      h('div', { class: 'control-compacto' }, h('label', { for: 'sel-empresa' }, 'Empresa'), selEmpresa),
      h('div', { class: 'control-compacto' }, h('label', { for: 'sel-entorno' }, 'Entorno de emisión'), selEntorno),
      h('div', { class: 'badge-contenedor', 'aria-live': 'polite' }, badgeEntorno(app.entorno, true))),
    h('div', { class: 'barra-usuario' },
      h('span', { class: 'usuario-nombre', title: app.usuario.email }, app.usuario.nombre || app.usuario.email),
      enlacesCuentaMU(),
      boton('Cerrar sesión', cerrarSesion, { tipo: 'enlace', pequeno: true })));
  const nav = h('nav', { class: 'nav-principal', 'aria-label': 'Secciones' },
    h('ul', null, NAVEGACION.map(([ruta, texto]) => h('li', null, h('a', { href: `#${ruta}`, dataset: { ruta } }, texto)))));
  const e = ENTORNOS[app.entorno];
  const franja = h('div', { class: `franja franja-${e.tono}` }, e.franja);
  const avisos = h('div', { id: 'avisos-empresa', class: 'avisos-empresa', role: 'region', 'aria-label': 'Avisos de comunicación', hidden: true });
  cab.append(barra, nav, franja, avisos);
  marcarNavegacion();
  pintarAvisos();
}

/** Banda persistente bajo la barra superior con las incidencias de comunicación de la empresa. */
function pintarAvisos() {
  const cont = document.getElementById('avisos-empresa');
  if (!cont) return;
  vaciar(cont);
  const lista = Array.isArray(app.avisos) ? app.avisos.filter(Boolean) : [];
  cont.hidden = lista.length === 0;
  if (!lista.length) return;
  cont.append(h('div', { class: 'avisos-interior' },
    h('span', { class: 'avisos-icono', 'aria-hidden': 'true' }, '⚠'),
    h('div', { class: 'avisos-texto' },
      h('strong', null, lista.length === 1 ? 'Incidencia de comunicación' : `Incidencias de comunicación (${lista.length})`),
      h('ul', null, lista.map((a) => h('li', null, String(a))))),
    h('a', { href: '#/remision', class: 'avisos-enlace' }, 'Ver remisión')));
}

async function refrescarAvisos(forzar = false) {
  if (!app.usuario || !app.empresaId || app.usuario.cambiar_clave) { app.avisos = []; pintarAvisos(); return; }
  if (!forzar && Date.now() - app.avisosEn < 30000) return;
  app.avisosEn = Date.now();
  const eid = app.empresaId;
  try {
    const e = await api(`/empresas/${enc(eid)}`);
    if (String(eid) !== String(app.empresaId)) return;
    actualizarAvisos(e);
  } catch { /* los avisos no impiden trabajar */ }
}

function actualizarAvisos(empresa) {
  app.avisos = empresa && Array.isArray(empresa.avisos) ? empresa.avisos : [];
  app.avisosEn = Date.now();
  pintarAvisos();
}

function marcarNavegacion() {
  const { ruta } = analizarHash();
  const mapa = { '/documento': '/importar', '/borrador': '/importar', '/factura': '/facturas' };
  let activa = ruta;
  for (const [pref, destino] of Object.entries(mapa)) if (ruta.startsWith(`${pref}/`)) activa = destino;
  if (activa.startsWith('/configuracion')) activa = '/configuracion';
  document.querySelectorAll('.nav-principal a').forEach((a) => {
    if (a.dataset.ruta === activa) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
}

function construirPie() {
  const pie = document.getElementById('pie');
  vaciar(pie);
  pie.append(
    h('span', null, `${nombreApp()}${app.estado && app.estado.version ? ` · versión ${app.estado.version}` : ''}`),
    h('span', null, 'Los documentos se muestran como imágenes generadas en el servidor; el navegador nunca abre los PDF originales.'));
}

async function cambiarEntorno(nuevo, sel) {
  if (nuevo === app.entorno) return;
  if (nuevo === 'produccion') {
    const ok = await confirmar({
      titulo: 'Cambiar al entorno de PRODUCCIÓN',
      texto: 'En PRODUCCIÓN las facturas que expida son reales, tienen efectos fiscales y sus registros se remiten a la AEAT. ¿Desea cambiar?',
      textoBoton: 'Cambiar a PRODUCCIÓN', peligro: true,
    });
    if (!ok) { sel.value = app.entorno; return; }
  }
  app.entorno = nuevo;
  guardarLS(LS_ENTORNO, nuevo);
  construirCabecera();
  notificar(`Entorno de emisión: ${ENTORNOS[nuevo].corto}`, nuevo === 'produccion' ? 'danger' : 'info');
  if (typeof app.alCambiarEntorno === 'function') app.alCambiarEntorno(nuevo);
  else enrutar({ forzar: true });
}

function cambiarEmpresa(id, sel) {
  if (guardiaSalida && !guardiaSalida()) { sel.value = String(app.empresaId); return; }
  guardiaSalida = null;
  app.empresaId = id;
  guardarLS(LS_EMPRESA, String(id));
  app.avisos = [];
  construirCabecera();
  refrescarAvisos(true);
  const { ruta } = analizarHash();
  if (/^\/(documento|borrador|factura)\//.test(ruta)) irA('/importar');
  else enrutar({ forzar: true });
}

function aplicarSesion(r) {
  app.usuario = r.usuario || null;
  app.csrf = r.csrf || null;
  app.empresas = Array.isArray(r.empresas) ? r.empresas : [];
  const guardada = leerLS(LS_EMPRESA);
  const existe = app.empresas.find((e) => String(e.id) === String(guardada));
  app.empresaId = existe ? existe.id : (app.empresas[0] ? app.empresas[0].id : null);
  const ent = leerLS(LS_ENTORNO);
  app.entorno = ent && ENTORNOS[ent] && entornosHabilitados().includes(ent) ? ent : 'simulador';
  app.avisos = [];
  construirCabecera();
  refrescarAvisos(true);
}

function sesionPerdida() {
  const { ruta } = analizarHash();
  app.usuario = null;
  app.csrf = null;
  guardiaSalida = null;
  construirCabecera();
  if (ruta !== '/login' && ruta !== '/instalar') {
    app.volverA = ruta;
    irA('/login');
  }
}

async function cerrarSesion() {
  if (guardiaSalida && !guardiaSalida()) return;
  guardiaSalida = null;
  try { await api('/sesion', { metodo: 'DELETE', sinRedireccion: true }); } catch { /* la sesión se cierra igualmente */ }
  app.usuario = null;
  app.csrf = null;
  app.empresas = [];
  app.volverA = null;
  construirCabecera();
  irA('/login');
}

/* ======================================================================================
 * Enrutado por hash
 * ====================================================================================== */

let tokenNavegacion = 0;
let limpiezas = [];
let guardiaSalida = null;   // función que devuelve true si se puede abandonar la página
let hashAnterior = '';
let ignorarSiguienteHash = false;

function analizarHash() {
  const bruto = window.location.hash.replace(/^#/, '') || '/';
  const i = bruto.indexOf('?');
  const ruta = i >= 0 ? bruto.slice(0, i) : bruto;
  return { ruta: ruta || '/', query: new URLSearchParams(i >= 0 ? bruto.slice(i + 1) : '') };
}

function irA(ruta) {
  const destino = `#${ruta}`;
  if (window.location.hash === destino) enrutar({ forzar: true });
  else window.location.hash = destino;
}

/** Cambia los parámetros de la ruta actual sin volver a cargar la página. */
function fijarQuery(params) {
  const { ruta } = analizarHash();
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') qs.set(k, v);
  const s = qs.toString();
  window.history.replaceState(null, '', `#${ruta}${s ? `?${s}` : ''}`);
  hashAnterior = window.location.hash;
}

const RUTAS = [
  [/^\/instalar$/, paginaInstalar, { publica: true }],
  [/^\/login$/, paginaLogin, { publica: true }],
  [/^\/registro$/, paginaRegistro, { publica: true }],
  [/^\/cambiar-clave$/, paginaCambiarClave, { publica: true }],
  [/^\/cuenta$/, paginaCuenta, { sinEmpresa: true }],
  [/^\/empresa-nueva$/, paginaEmpresaNueva, { sinEmpresa: true }],
  [/^\/plataforma$/, paginaPlataforma, { sinEmpresa: true }],
  [/^\/configuracion(?:\/([a-z_]+))?$/, paginaConfiguracion],
  [/^\/importar$/, paginaImportar],
  [/^\/documento\/(\d+)$/, paginaDocumento],
  [/^\/borrador\/(\d+)$/, paginaBorrador],
  [/^\/facturas$/, paginaFacturas],
  [/^\/factura\/(\d+)$/, paginaFactura],
  [/^\/remision$/, paginaRemision],
  [/^\/consulta-aeat$/, paginaConsultaAeat],
  [/^\/actividad$/, paginaActividad],
  [/^\/mis-certificados$/, paginaMisCertificados, { sinEmpresa: true }],
  [/^\/declaracion$/, paginaDeclaracion],
];

async function enrutar({ forzar = false } = {}) {
  if (ignorarSiguienteHash) { ignorarSiguienteHash = false; return; }
  if (!forzar && guardiaSalida && window.location.hash !== hashAnterior && !guardiaSalida()) {
    ignorarSiguienteHash = true;
    window.location.hash = hashAnterior;
    return;
  }
  hashAnterior = window.location.hash;
  guardiaSalida = null;
  app.alCambiarEntorno = null;
  limpiezas.splice(0).forEach((f) => { try { f(); } catch { /* sin efecto */ } });
  document.querySelectorAll('dialog.dialogo').forEach((d) => d.close());
  const token = ++tokenNavegacion;
  if (!app.estado) return;
  const { ruta, query } = analizarHash();
  if (!app.estado.instalada) {
    if (ruta !== '/instalar') { irA('/instalar'); return; }
  } else if (!app.usuario) {
    if (ruta !== '/login' && ruta !== '/registro') { app.volverA = ruta === '/' ? null : ruta; irA('/login'); return; }
  } else if (app.usuario.cambiar_clave) {
    if (ruta !== '/cambiar-clave') { irA('/cambiar-clave'); return; }
  } else if (ruta === '/' || ruta === '/login' || ruta === '/instalar' || ruta === '/cambiar-clave') {
    irA(rutaInicioMU());
    return;
  }
  const main = document.getElementById('contenido');
  document.body.classList.toggle('vista-publica', ['/login', '/instalar', '/registro', '/cambiar-clave'].includes(ruta));
  marcarNavegacion();
  vaciar(main);
  window.scrollTo(0, 0);
  const def = RUTAS.find(([re]) => re.test(ruta));
  if (!def) {
    titulo('Página no encontrada');
    main.append(cabeceraPagina('Página no encontrada'), h('p', null, h('a', { href: '#/importar' }, 'Volver a Documentos')));
    return;
  }
  const [re, fn, opciones] = def;
  const ctx = {
    params: ruta.match(re).slice(1),
    query,
    main,
    vigente: () => token === tokenNavegacion,
    alSalir: (f) => limpiezas.push(f),
  };
  if (!(opciones && (opciones.publica || opciones.sinEmpresa)) && !app.empresaId) {
    // ¿Le han dado acceso a una empresa desde otra ventana (invitación aceptada, alta)? Se relee la sesión antes de decir que no tiene
    try {
      const yo = await api('/yo');
      if (token !== tokenNavegacion) return;
      sincronizarEmpresas(yo);
    } catch { /* sin conexión: se muestra la vista sin empresa */ }
    if (token !== tokenNavegacion) return;
  }
  if (!(opciones && (opciones.publica || opciones.sinEmpresa)) && !app.empresaId) {
    titulo('Sin empresa');
    anadir(main, vistaSinEmpresaMU());
    return;
  }
  main.append(cargando());
  main.focus({ preventScroll: true });
  if (!(opciones && opciones.publica)) refrescarAvisos();
  try {
    await fn(ctx);
  } catch (err) {
    if (!ctx.vigente() || (err instanceof ApiError && err.estado === 401)) return;
    if (err instanceof ApiError && err.estado === 404 && !(opciones && (opciones.publica || opciones.sinEmpresa))) {
      // ¿Sigue siendo miembro de la empresa? (p. ej. /documentos/… de una empresa de la que le han quitado)
      if (err.sinAcceso || (!err.accesoComprobado && await comprobarAccesoEmpresa())) return;
      if (!ctx.vigente()) return;
    }
    vaciar(main);
    main.append(cajaError(err, () => enrutar({ forzar: true })));
  }
}

/** Sustituye el contenido principal si la navegación sigue vigente. */
function montar(ctx, ...nodos) {
  if (!ctx.vigente()) return false;
  vaciar(ctx.main);
  anadir(ctx.main, nodos);
  return true;
}

/* ======================================================================================
 * Instalación e inicio de sesión
 * ====================================================================================== */

function marcoPublico(...contenido) {
  return h('div', { class: 'publica' },
    h('div', { class: 'publica-marca' }, h('span', { class: 'marca-nombre' }, nombreApp()), h('span', { class: 'marca-sub' }, 'VERI*FACTU')),
    h('div', { class: 'card publica-card' }, contenido));
}

async function paginaInstalar(ctx) {
  titulo('Instalación');
  const nombre = entrada({ required: true, autocomplete: 'name', maxlength: 120 });
  const email = entrada({ type: 'email', required: true, autocomplete: 'username', maxlength: 200 });
  const clave = entrada({ type: 'password', required: true, minlength: 10, autocomplete: 'new-password' });
  const clave2 = entrada({ type: 'password', required: true, minlength: 10, autocomplete: 'new-password' });
  const nif = entrada({ required: true, maxlength: 20, autocomplete: 'off', spellcheck: 'false', class: 'mayus' });
  const razon = entrada({ required: true, maxlength: 120, autocomplete: 'organization' });
  const domicilio = h('textarea', { required: true, rows: 2, maxlength: 300 });
  const registroMU = campoRegistroAbiertoMU();
  const error = h('div', { 'aria-live': 'assertive' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-primario' }, 'Instalar y entrar');
  const form = h('form', { class: 'form', novalidate: false },
    h('fieldset', null, h('legend', null, 'Usuario administrador'),
      campo('Nombre y apellidos', nombre),
      campo('Correo electrónico', email),
      campo('Contraseña', clave, { ayuda: 'Al menos 10 caracteres.' }),
      campo('Repita la contraseña', clave2)),
    h('fieldset', null, h('legend', null, 'Empresa (obligado tributario)'),
      campo('NIF', nif),
      campo('Razón social', razon),
      campo('Domicilio fiscal', domicilio)),
    registroMU.nodo,
    error,
    h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(error);
    if (clave.value !== clave2.value) { error.append(alerta('danger', 'Las contraseñas no coinciden.')); clave2.focus(); return; }
    if (clave.value.length < 10) { error.append(alerta('danger', 'La contraseña debe tener al menos 10 caracteres.')); clave.focus(); return; }
    await conEspera(enviar, async () => {
      try {
        const r = await api('/instalar', {
          metodo: 'POST', sinRedireccion: true,
          json: {
            email: email.value.trim(), nombre: nombre.value.trim(), clave: clave.value, registro_abierto: registroMU.valor(),
            empresa: { nif: nif.value.trim().toUpperCase(), razon_social: razon.value.trim(), domicilio: domicilio.value.trim() },
          },
        });
        app.estado.instalada = true;
        const sesion = r && r.csrf ? r : await api('/sesion', { metodo: 'POST', sinRedireccion: true, json: { email: email.value.trim(), clave: clave.value } });
        clave.value = '';
        clave2.value = '';
        aplicarSesion(sesion);
        notificar('Instalación completada. Revise la configuración de la empresa.', 'ok');
        irA('/configuracion');
      } catch (err) {
        error.append(alerta('danger', err.message));
      }
    }, 'Instalando…');
  });
  montar(ctx, marcoPublico(
    h('h1', null, 'Instalación inicial'),
    h('p', null, 'Cree el usuario administrador y la empresa que expedirá las facturas. Podrá completar la configuración fiscal a continuación.'),
    form));
  nombre.focus();
}

async function paginaLogin(ctx) {
  titulo('Iniciar sesión');
  const email = entrada({ type: 'email', required: true, autocomplete: 'username' });
  const clave = entrada({ type: 'password', required: true, autocomplete: 'current-password' });
  const error = h('div', { 'aria-live': 'assertive' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-primario' }, 'Entrar');
  const form = h('form', { class: 'form' }, campo('Correo electrónico', email), campo('Contraseña', clave), error, h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(error);
    await conEspera(enviar, async () => {
      try {
        const r = await api('/sesion', { metodo: 'POST', sinRedireccion: true, json: { email: email.value.trim(), clave: clave.value } });
        clave.value = '';
        aplicarSesion(r);
        const destino = app.volverA;
        app.volverA = null;
        irA(destino && destino !== '/login' ? destino : rutaInicioMU());
      } catch (err) {
        clave.value = '';
        error.append(alerta('danger', err.estado === 401 ? 'Correo o contraseña incorrectos.' : err.message));
        clave.focus();
      }
    }, 'Entrando…');
  });
  montar(ctx, marcoPublico(h('h1', null, 'Iniciar sesión'), form, pieLoginMU()));
  email.focus();
}

/* ======================================================================================
 * Configuración (asistente por pasos)
 * ====================================================================================== */

const PASOS_CONFIG = [
  ['empresa', 'Datos de la empresa'],
  ['ambito', 'Ámbito del emisor'],
  ['autorizacion', 'Autorización del usuario'],
  ['series', 'Series de numeración'],
  ['certificados', 'Certificados'],
  ['productor', 'Productor del sistema'],
  ['produccion', 'Paso a producción'],
  ['usuarios', 'Usuarios y empresas'],
];

function cuerpoEmpresa(emp, cambios = {}) {
  const base = {
    razon_social: emp.razon_social ?? '', domicilio: emp.domicilio ?? '',
    tipo_contribuyente: emp.tipo_contribuyente ?? null, territorio: emp.territorio ?? null,
    sii: emp.sii === null || emp.sii === undefined ? null : Number(emp.sii),
    canarias_ceuta_melilla: Number(emp.canarias_ceuta_melilla || 0),
    otras_exclusiones: emp.otras_exclusiones ?? '', origen_pdf: emp.origen_pdf ?? null,
    origen_pdf_detalle: emp.origen_pdf_detalle ?? '',
  };
  return { ...base, ...cambios };
}

async function paginaConfiguracion(ctx) {
  const paso = PASOS_CONFIG.some(([k]) => k === ctx.params[0]) ? ctx.params[0] : 'empresa';
  titulo(`Configuración · ${PASOS_CONFIG.find(([k]) => k === paso)[1]}`);
  const eid = app.empresaId;
  const [rEmp, rSeries, rCerts, rProd] = await Promise.allSettled([
    api(`/empresas/${enc(eid)}`),
    api(`/empresas/${enc(eid)}/series`),
    api(`/empresas/${enc(eid)}/certificados`),
    api(`/empresas/${enc(eid)}/produccion`),
  ]);
  if (rEmp.status === 'rejected') throw rEmp.reason;
  const emp = rEmp.value;
  const series = rSeries.status === 'fulfilled' ? comoLista(rSeries.value, 'series') : null;
  const certs = rCerts.status === 'fulfilled' ? comoLista(rCerts.value, 'certificados') : null;
  const prod = rProd.status === 'fulfilled' ? rProd.value : null;
  const amb = parseJSON(emp.ambito) || parseJSON(emp.ambito_resultado) || {};
  actualizarAvisos(emp);

  const estadoPaso = {
    empresa: emp.razon_social && emp.domicilio ? 'ok' : 'pendiente',
    ambito: emp.ambito_confirmado_en ? (amb.puede_emitir ? 'ok' : 'error') : 'pendiente',
    autorizacion: emp.declaracion_autorizacion_en ? 'ok' : 'pendiente',
    series: series && series.some((s) => s.entorno === app.entorno && s.confirmada_en) ? 'ok' : 'pendiente',
    certificados: app.entorno === 'simulador' ? 'na' : (certs && certs.some((c) => c.entorno === app.entorno && c.en_uso) ? 'ok' : 'pendiente'),
    // El productor lo identifica el operador de la plataforma: no es una tarea pendiente de la empresa
    productor: emp.productor && emp.productor.nif && emp.productor.confirmado_en ? 'ok' : (esSuperadmin() ? 'pendiente' : 'operador'),
    produccion: prod && prod.habilitada ? 'ok' : 'pendiente',
    usuarios: 'opcional',
  };
  const textoEstado = {
    ok: 'completado', pendiente: 'pendiente', error: 'no soportado', na: 'no necesario en el simulador', opcional: 'opcional',
    operador: 'lo completa el operador de la plataforma',
  };
  const icono = { ok: '✓', pendiente: '○', error: '✗', na: '–', opcional: '+', operador: '–' };
  const pasos = h('nav', { class: 'stepper', 'aria-label': 'Pasos de la configuración' },
    h('ol', null, PASOS_CONFIG.map(([k, t], i) => h('li', { class: `paso paso-${estadoPaso[k]}` },
      h('a', { href: `#/configuracion/${k}`, 'aria-current': k === paso ? 'step' : null },
        h('span', { class: 'paso-icono', 'aria-hidden': 'true' }, icono[estadoPaso[k]]),
        h('span', { class: 'paso-texto' }, h('span', { class: 'paso-num' }, `${i + 1}. `), t,
          h('span', { class: 'paso-estado' }, textoEstado[estadoPaso[k]])))))));

  const recargar = () => enrutar({ forzar: true });
  let contenido;
  switch (paso) {
    case 'empresa': contenido = pasoEmpresa(emp, recargar); break;
    case 'ambito': contenido = pasoAmbito(emp, amb, recargar); break;
    case 'autorizacion': contenido = pasoAutorizacion(emp, recargar); break;
    case 'series': contenido = pasoSeries(series, rSeries.reason, recargar); break;
    case 'certificados': contenido = pasoCertificados(certs, rCerts.reason, recargar); break;
    case 'productor': contenido = pasoProductor(emp, recargar); break;
    case 'usuarios': contenido = pasoUsuariosMU(emp, recargar); break;
    default: contenido = pasoProduccion(prod, rProd.reason, emp, recargar);
  }
  const idx = PASOS_CONFIG.findIndex(([k]) => k === paso);
  const navPasos = h('div', { class: 'acciones acciones-entre' },
    idx > 0 ? enlace(`← ${PASOS_CONFIG[idx - 1][1]}`, `#/configuracion/${PASOS_CONFIG[idx - 1][0]}`, { tipo: 'secundario' }) : h('span'),
    idx < PASOS_CONFIG.length - 1 ? enlace(`${PASOS_CONFIG[idx + 1][1]} →`, `#/configuracion/${PASOS_CONFIG[idx + 1][0]}`, { tipo: 'secundario' }) : h('span'));
  montar(ctx,
    cabeceraPagina('Configuración', { meta: [h('span', null, `${emp.razon_social || ''} · NIF ${emp.nif || ''}`)] }),
    esAdmin() ? null : alerta('info', 'Solo un administrador de la empresa puede modificar la configuración. Puede consultarla.'),
    h('div', { class: 'config' }, pasos, h('div', { class: 'config-contenido' }, contenido, navPasos)));
}

function pasoEmpresa(emp, recargar) {
  const razon = entrada({ value: emp.razon_social || '', required: true, maxlength: 120, disabled: !esAdmin() });
  const domicilio = h('textarea', { rows: 3, required: true, maxlength: 300, disabled: !esAdmin() });
  domicilio.value = emp.domicilio || '';
  const nif = entrada({ value: emp.nif || '', readOnly: true, class: 'mono' });
  const guardar = h('button', { type: 'submit', class: 'btn btn-primario', disabled: !esAdmin() }, 'Guardar datos');
  const form = h('form', { class: 'form' },
    campo('NIF', nif, { ayuda: 'El NIF identifica al obligado tributario y no se puede modificar.' }),
    campo('Razón social', razon, { ayuda: 'Debe coincidir con la que consta en la AEAT: se usará en los registros de facturación.' }),
    campo('Domicilio fiscal', domicilio),
    alerta('info', 'Al guardar se vuelve a evaluar el ámbito del emisor y deberá confirmarlo de nuevo.'),
    h('div', { class: 'acciones' }, guardar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await conEspera(guardar, async () => {
      try {
        await api(`/empresas/${enc(emp.id)}`, { metodo: 'PUT', json: cuerpoEmpresa(emp, { razon_social: razon.value.trim(), domicilio: domicilio.value.trim() }) });
        const e2 = app.empresas.find((x) => String(x.id) === String(emp.id));
        if (e2) { e2.razon_social = razon.value.trim(); construirCabecera(); }
        notificar('Datos de la empresa guardados.', 'ok');
        recargar();
      } catch (err) { notificar(err.message, 'danger'); }
    }, 'Guardando…');
  });
  return tarjeta('Datos fiscales de la empresa', form);
}

function pasoAmbito(emp, amb, recargar) {
  const dis = !esAdmin();
  let sucio = false;
  const otras = h('textarea', { rows: 3, maxlength: 500, disabled: dis, name: 'otras' });
  otras.value = emp.otras_exclusiones || '';
  const detalle = entrada({ value: emp.origen_pdf_detalle || '', maxlength: 200, disabled: dis });
  const guardar = h('button', { type: 'submit', class: 'btn btn-primario', disabled: dis }, 'Guardar y evaluar');
  const siNo = [['0', 'No'], ['1', 'Sí']];
  const valSii = emp.sii === null || emp.sii === undefined ? '' : String(Number(emp.sii));
  const form = h('form', { class: 'form' },
    grupoRadios('tipo_contribuyente', '¿Qué tipo de contribuyente es la empresa?', TIPOS_CONTRIBUYENTE, emp.tipo_contribuyente),
    grupoRadios('territorio', '¿En qué territorio tributa?', TERRITORIOS, emp.territorio,
      { ayuda: 'Los obligados de los territorios forales del País Vasco y Navarra tienen sus propios sistemas (TicketBAI, Navarra).' }),
    grupoRadios('sii', '¿Lleva los libros registro del IVA mediante el SII (Suministro Inmediato de Información)?', siNo, valSii,
      { ayuda: 'Las empresas acogidas al SII están excluidas de VERI*FACTU.' }),
    grupoRadios('canarias_ceuta_melilla', '¿Realiza operaciones en Canarias, Ceuta o Melilla (IGIC / IPSI)?', siNo, String(Number(emp.canarias_ceuta_melilla || 0))),
    campo('Otras exclusiones o particularidades (opcional)', otras,
      { ayuda: 'Por ejemplo: regímenes especiales, inversión del sujeto pasivo, operaciones no sujetas… (las operaciones exentas E1–E4 y E6 sí están soportadas: se indican en el desglose de cada factura). Déjelo vacío si no hay ninguna.' }),
    grupoRadios('origen_pdf', '¿Cómo se generan los PDF que va a importar?', ORIGENES_PDF, emp.origen_pdf,
      { ayuda: 'Si proceden de un programa de facturación, ese programa podría ser por sí mismo un sistema de facturación sujeto al reglamento.' }),
    campo('Detalle del origen (programa, plantilla…)', detalle),
    h('div', { class: 'acciones' }, guardar));
  form.addEventListener('input', () => { sucio = true; actualizarConfirmacion(); });
  form.addEventListener('change', () => { sucio = true; actualizarConfirmacion(); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const sii = valorRadio(form, 'sii');
    const cambios = {
      tipo_contribuyente: valorRadio(form, 'tipo_contribuyente'),
      territorio: valorRadio(form, 'territorio'),
      sii: sii === null ? null : Number(sii),
      canarias_ceuta_melilla: Number(valorRadio(form, 'canarias_ceuta_melilla') || 0),
      otras_exclusiones: otras.value.trim(),
      origen_pdf: valorRadio(form, 'origen_pdf'),
      origen_pdf_detalle: detalle.value.trim(),
    };
    await conEspera(guardar, async () => {
      try {
        await api(`/empresas/${enc(emp.id)}`, { metodo: 'PUT', json: cuerpoEmpresa(emp, cambios) });
        notificar('Respuestas guardadas. Revise la evaluación del ámbito.', 'ok');
        recargar();
      } catch (err) { notificar(err.message, 'danger'); }
    }, 'Guardando…');
  });

  const motivos = Array.isArray(amb.motivos) ? amb.motivos : [];
  const confirmarBtn = boton('Confirmar ámbito', async () => {
    await conEspera(confirmarBtn, async () => {
      try {
        await api(`/empresas/${enc(emp.id)}/ambito/confirmar`, { metodo: 'POST', json: {} });
        notificar('Ámbito confirmado.', 'ok');
        recargar();
      } catch (err) { notificar(err.message, 'danger'); }
    });
  }, { tipo: 'primario', disabled: dis });
  const avisoSucio = h('p', { class: 'ayuda', 'aria-live': 'polite' });
  function actualizarConfirmacion() {
    confirmarBtn.disabled = dis || sucio || !emp.ambito;
    avisoSucio.textContent = sucio ? 'Hay respuestas sin guardar: guárdelas antes de confirmar el ámbito.' : '';
  }
  actualizarConfirmacion();
  const evaluado = emp.ambito || emp.ambito_resultado;
  const resultado = tarjeta('Evaluación del ámbito',
    !evaluado ? h('p', { class: 'vacio' }, 'Responda las preguntas y guarde para evaluar el ámbito.')
      : [
        amb.puede_emitir
          ? alerta('ok', h('strong', null, 'Supuesto soportado: '), 'la aplicación puede expedir facturas para este emisor.')
          : alerta('danger', h('strong', null, 'Supuesto no soportado: '), 'la aplicación no expedirá facturas para este emisor mientras se mantengan estas circunstancias.'),
        motivos.length ? h('ul', { class: 'lista-motivos' }, motivos.map((m) => h('li', { class: /^AVISO/i.test(m) ? 'motivo-aviso' : null }, m))) : null,
        emp.ambito_confirmado_en
          ? alerta('ok', `Ámbito confirmado el ${fmtFechaHora(emp.ambito_confirmado_en)}.`)
          : h('div', null,
            h('p', null, 'Al confirmar declara que las respuestas son correctas. Si cambia alguna, deberá confirmarlo de nuevo.'),
            h('div', { class: 'acciones' }, confirmarBtn), avisoSucio),
      ]);
  const casos = Array.isArray(emp.casos_soportados) ? emp.casos_soportados : [];
  const tarjetaCasos = casos.length ? tarjeta('Casos que la aplicación puede expedir',
    h('ul', { class: 'lista-motivos' }, casos.map((c) => h('li', null, String(c)))),
    h('p', { class: 'ayuda' }, 'Cualquier otro supuesto (operaciones no sujetas, inversión del sujeto pasivo, recargo de equivalencia, suplidos, '
      + 'regímenes especiales, entregas intracomunitarias exentas —E5—…) se bloquea al revisar el borrador.')) : null;
  return [tarjeta('Preguntas sobre el ámbito del emisor', form), resultado, tarjetaCasos];
}

function pasoAutorizacion(emp) {
  const rel = selector(RELACIONES, emp.relacion_usuario || 'administrador', { required: true });
  const chk = h('input', { type: 'checkbox', id: uid('chk'), required: true });
  const guardar = h('button', { type: 'submit', class: 'btn btn-primario', disabled: true }, 'Registrar declaración');
  chk.addEventListener('change', () => { guardar.disabled = !chk.checked; });
  const form = h('form', { class: 'form' },
    campo('Su relación con la empresa', rel),
    h('div', { class: 'casilla casilla-destacada' }, chk, h('label', { for: chk.id }, 'Declaro estar autorizado a actuar en nombre de esta empresa')),
    h('div', { class: 'acciones' }, guardar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!chk.checked) return;
    await conEspera(guardar, async () => {
      try {
        await api(`/empresas/${enc(emp.id)}/autorizacion-usuario`, { metodo: 'POST', json: { relacion: rel.value, declaro: true } });
        notificar('Declaración registrada.', 'ok');
        enrutar({ forzar: true });
      } catch (err) { notificar(err.message, 'danger'); }
    }, 'Registrando…');
  });
  const relTexto = (RELACIONES.find(([k]) => k === emp.relacion_usuario) || [])[1] || emp.relacion_usuario;
  return tarjeta('Autorización del usuario',
    h('p', null, 'La aplicación expide facturas y remite registros de facturación en nombre de la empresa. Solo deben usarla personas autorizadas por ella. '
      + 'Su declaración queda registrada en el registro de actividad.'),
    emp.declaracion_autorizacion_en
      ? alerta('ok', `Declaración registrada el ${fmtFechaHora(emp.declaracion_autorizacion_en)}${relTexto ? ` como «${relTexto}»` : ''}.`)
      : alerta('warn', 'Aún no ha declarado estar autorizado para actuar en nombre de esta empresa.'),
    form);
}

function ejemploPatron(patron, numero) {
  return formatearPatron(patron, numero || 1, anioMesHoy().anio);
}

function pasoSeries(series, error, recargar) {
  const dis = !esAdmin();
  const listado = error ? cajaError(error)
    : tabla([
      { titulo: 'Entorno', celda: (s) => badgeEntorno(s.entorno) },
      { titulo: 'Código', celda: (s) => h('span', { class: 'mono' }, s.codigo) },
      { titulo: 'Patrón', celda: (s) => h('code', null, s.patron) },
      { titulo: 'Descripción', celda: (s) => s.descripcion || '—' },
      { titulo: 'Tipo', celda: (s) => (s.tipo === 'rectificativa' ? 'Rectificativa' : 'Ordinaria') },
      {
        // El siguiente número real: el contador del año en curso (o el único de una serie sin año), escrito con el patrón
        titulo: 'Siguiente nº',
        celda: (s) => h('code', { title: s.con_anio ? `Contador de ${s.anio_actual}` : 'Contador único de la serie' },
          s.siguiente_texto || formatearPatron(s.patron, s.siguiente_actual || s.siguiente_numero, s.anio_actual || anioMesHoy().anio)),
      },
      {
        // El número guardado al crear la serie solo es el inicial: el del año de confirmación (series con año) o el
        // primero de una serie sin año
        titulo: 'Número inicial',
        celda: (s) => (s.con_anio && Number(s.siguiente_numero) !== 1
          ? h('span', { class: 'ayuda' }, 'Primer número de cada año: ', h('code', null, String(s.siguiente_numero)),
            s.anio_confirmacion ? ` en ${s.anio_confirmacion} (año de confirmación); 1 en los demás` : ' (año de confirmación); 1 en los demás')
          : (s.con_anio ? h('span', { class: 'ayuda' }, 'Primer número de cada año: ', h('code', null, '1'))
            : h('code', null, String(s.siguiente_numero)))),
      },
      {
        titulo: 'Contadores por año',
        celda: (s) => (Array.isArray(s.contadores) && s.contadores.length
          ? s.contadores.map((c) => h('span', { class: 'bloque' }, `${c.anio ? c.anio : 'Único'}: siguiente `,
            h('code', null, c.siguiente_texto || formatearPatron(s.patron, c.siguiente, c.anio || anioMesHoy().anio))))
          : h('span', { class: 'muted' }, '—')),
      },
      {
        titulo: 'Estado',
        celda: (s) => (s.confirmada_en
          ? [pill('Confirmada', 'ok'), h('span', { class: 'muted bloque' }, fmtFechaHora(s.confirmada_en)),
            dis ? null : boton('Avanzar contador…', () => dialogoAvanceContador(s, {}, recargar),
              { pequeno: true, title: 'Saltar números ya usados fuera de esta instalación (p. ej. tras un desastre)' })]
          : [pill('Sin confirmar', 'warn'), ' ', boton('Confirmar', (ev) => confirmarSerie(s, ev.currentTarget), { pequeno: true, disabled: dis })]),
      },
    ], series || [], { vacio: 'No hay series creadas.', leyenda: 'Series de numeración' });

  async function confirmarSerie(s, btn) {
    const ok = await confirmar({
      titulo: `Confirmar la serie ${s.codigo}`,
      contenido: [
        kv([['Entorno', badgeEntorno(s.entorno)], ['Patrón', h('code', null, s.patron)], ['Siguiente número', String(s.siguiente_numero)],
          ['Ejemplo', h('code', null, ejemploPatron(s.patron, s.siguiente_numero))]]),
        h('p', null, 'A partir de la confirmación la aplicación exigirá numeración correlativa desde el siguiente número indicado. '
          + 'Compruebe que no hay facturas expedidas con números posteriores fuera de esta aplicación.'),
      ],
      textoBoton: 'Confirmar serie',
    });
    if (!ok) return;
    await conEspera(btn, async () => {
      try {
        await api(`/series/${enc(s.id)}/confirmar`, { metodo: 'POST', json: {} });
        notificar('Serie confirmada.', 'ok');
        recargar();
      } catch (err) { notificar(err.message, 'danger'); }
    });
  }

  // Alta de serie
  const entorno = selector(Object.keys(ENTORNOS).map((k) => [k, ENTORNOS[k].corto]), app.entorno, { disabled: dis });
  const codigo = entrada({ required: true, maxlength: 20, placeholder: 'p. ej. 26', disabled: dis });
  const propuesta = parametrosAltaSerie();
  const patron = entrada({ required: true, maxlength: 60, value: propuesta.patron || '{n}/{aa}', class: 'mono', disabled: dis });
  const descripcion = entrada({ maxlength: 120, disabled: dis });
  const tipo = selector([['ordinaria', 'Ordinaria'], ['rectificativa', 'Rectificativa']], 'ordinaria', { disabled: dis });
  const siguiente = entrada({ type: 'number', min: 1, step: 1, value: propuesta.siguiente || '1', required: true, disabled: dis });
  const ejemplo = h('p', { class: 'ayuda', 'aria-live': 'polite' });
  const actualizarEjemplo = () => { ejemplo.textContent = `Ejemplo del siguiente número: ${ejemploPatron(patron.value, siguiente.value)}`; };
  patron.addEventListener('input', () => { actualizarEjemplo(); patronPrueba.value = patron.value; });
  siguiente.addEventListener('input', actualizarEjemplo);
  actualizarEjemplo();
  const crear = h('button', { type: 'submit', class: 'btn btn-primario', disabled: dis }, 'Crear serie');
  const formAlta = h('form', { class: 'form' },
    h('div', { class: 'fila-campos' }, campo('Entorno', entorno), campo('Código interno', codigo), campo('Tipo', tipo)),
    campo('Patrón del número', patron, { ayuda: AYUDA_PATRON }),
    ejemplo,
    h('div', { class: 'fila-campos' }, campo('Descripción (opcional)', descripcion), campo('Siguiente número', siguiente)),
    h('div', { class: 'acciones' }, crear));
  formAlta.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!patronValido(patron.value)) { notificar('El patrón debe contener {n} (o {n:3} con ceros de relleno) una sola vez.', 'danger'); patron.focus(); return; }
    await conEspera(crear, async () => {
      try {
        await api(`/empresas/${enc(app.empresaId)}/series`, {
          metodo: 'POST',
          json: {
            entorno: entorno.value, codigo: codigo.value.trim(), patron: patron.value.trim(), descripcion: descripcion.value.trim(),
            tipo: tipo.value, siguiente_numero: Number(siguiente.value),
          },
        });
        notificar('Serie creada. Confírmela cuando sea correcta.', 'ok');
        recargar();
      } catch (err) { notificar(err.message, 'danger'); }
    }, 'Creando…');
  });

  // Prueba de un número con un patrón (con el patrón propuesto desde un borrador, se rellena con ese número)
  const patronPrueba = entrada({ required: true, value: propuesta.patron || '{n}/{aa}', class: 'mono' });
  const numPrueba = entrada({
    required: true, placeholder: 'p. ej. 166/26', class: 'mono',
    value: propuesta.patron ? formatearPatron(propuesta.patron, Number(propuesta.siguiente) || 1, Number(propuesta.anio) || anioMesHoy().anio) : '',
  });
  const resultadoPrueba = h('div', { 'aria-live': 'polite' });
  const probar = h('button', { type: 'submit', class: 'btn btn-secundario' }, 'Probar');
  const formPrueba = h('form', { class: 'form' },
    h('div', { class: 'fila-campos' }, campo('Patrón', patronPrueba), campo('Número de factura', numPrueba)),
    (series && series.length) ? h('p', { class: 'ayuda' }, 'Patrones existentes: ',
      series.map((s) => boton(s.patron, () => { patronPrueba.value = s.patron; numPrueba.focus(); }, { tipo: 'enlace', pequeno: true }))) : null,
    h('div', { class: 'acciones' }, probar), resultadoPrueba);
  formPrueba.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultadoPrueba);
    await conEspera(probar, async () => {
      try {
        const r = await api('/series/probar', { metodo: 'POST', json: { patron: patronPrueba.value.trim(), num_serie: numPrueba.value.trim() } });
        resultadoPrueba.append(r && r.coincide
          ? alerta('ok', `Coincide con el patrón: número ${r.numero ?? '—'}${r.anio ? `, año ${r.anio}` : ''}.`)
          : alerta('warn', 'El número no coincide con el patrón.'));
      } catch (err) { resultadoPrueba.append(alerta('danger', err.message)); }
    });
  });
  return [
    tarjeta('Series de numeración', h('p', null, 'Cada entorno tiene sus propias series. El número de cada factura debe corresponder a una serie confirmada y ser el siguiente correlativo.'), listado),
    tarjeta('Nueva serie', formAlta),
    tarjeta('Probar un número', formPrueba),
  ];
}

function resumenLegitimacion(leg) {
  const l = parseJSON(leg);
  if (!l) return h('span', { class: 'muted' }, '—');
  if (typeof l !== 'object') return h('span', null, String(l));
  return h('div', { class: 'legitimacion' },
    'ok' in l ? (l.ok ? pill('Legitimación comprobada', 'ok') : pill('Legitimación no acreditada', 'danger')) : null,
    'vigente' in l ? [' ', l.vigente ? pill('Vigente', 'ok') : pill('No vigente', 'danger')] : null,
    l.metodo ? h('span', { class: 'muted bloque' }, `Método: ${METODO_LEGITIMACION_TEXTO[l.metodo] || humanizar(l.metodo)}`) : null,
    l.detalle ? h('span', { class: 'bloque' }, l.detalle) : null);
}

function pasoCertificados(certs, error, recargar) {
  const dis = !esAdmin();
  const listado = error ? cajaError(error)
    : tabla([
      { titulo: 'Entorno', celda: (c) => badgeEntorno(c.entorno) },
      { titulo: 'Origen', celda: origenCertC },
      { titulo: 'Sujeto', celda: (c) => h('span', { class: 'valor-largo' }, c.sujeto || '—') },
      { titulo: 'Titular', celda: (c) => h('span', { class: 'mono' }, c.titular_nif || '—') },
      { titulo: 'Representado', celda: (c) => h('span', { class: 'mono' }, c.representado_nif || '—') },
      { titulo: 'Tipo', celda: (c) => TIPOS_CERTIFICADO[c.tipo] || c.tipo || '—' },
      { titulo: 'Válido hasta', celda: (c) => fmtFechaHora(c.valido_hasta) },
      { titulo: 'Legitimación', celda: (c) => resumenLegitimacion(c.legitimacion) },
      { titulo: 'Activo', celda: (c) => (c.activo === 0 || c.activo === false ? pill('Sustituido', 'neutral') : pill('Activo', 'ok')) },
    ], certs || [], { vacio: 'No hay certificados cargados.', leyenda: 'Certificados' });
  const archivo = h('input', { type: 'file', accept: '.p12,.pfx,application/x-pkcs12', required: true, disabled: dis });
  const clave = entrada({ type: 'password', required: true, autocomplete: 'off', disabled: dis });
  const entorno = selector([['pruebas', ENTORNOS.pruebas.corto], ['produccion', ENTORNOS.produccion.corto]], app.entorno === 'produccion' ? 'produccion' : 'pruebas', { disabled: dis });
  const resultado = h('div', { 'aria-live': 'polite' });
  const subir = h('button', { type: 'submit', class: 'btn btn-primario', disabled: dis }, 'Cargar certificado');
  const form = h('form', { class: 'form' },
    campo('Archivo del certificado (.p12 o .pfx)', archivo),
    h('div', { class: 'fila-campos' },
      campo('Contraseña del certificado', clave, { ayuda: 'Se usa para abrir el archivo. Se guarda cifrada en el servidor y nunca se vuelve a mostrar.' }),
      campo('Entorno', entorno)),
    h('div', { class: 'acciones' }, subir), resultado);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultado);
    if (!archivo.files || !archivo.files[0]) { notificar('Seleccione el archivo del certificado.', 'danger'); return; }
    const fd = new FormData();
    fd.append('archivo', archivo.files[0], archivo.files[0].name);
    fd.append('clave', clave.value);
    fd.append('entorno', entorno.value);
    clave.value = '';
    await conEspera(subir, async () => {
      try {
        const r = await api(`/empresas/${enc(app.empresaId)}/certificados`, { metodo: 'POST', form: fd });
        archivo.value = '';
        resultado.append(alerta('ok', h('strong', null, 'Certificado cargado. '), 'Compruebe que los datos corresponden a la empresa:'),
          kv([
            ['Sujeto', h('span', { class: 'valor-largo' }, r.sujeto || '—')],
            ['NIF del titular', r.titular_nif || '—'],
            ['NIF del representado', r.representado_nif || '—'],
            ['Tipo', TIPOS_CERTIFICADO[r.tipo] || r.tipo || '—'],
            ['Válido hasta', fmtFechaHora(r.valido_hasta)],
            ['Legitimación', resumenLegitimacion(r.legitimacion)],
          ]),
          boton('Actualizar listado', recargar, { pequeno: true }));
      } catch (err) { resultado.append(alerta('danger', err.message)); }
    }, 'Cargando…');
  });
  return [
    tarjetaCertificadoEnUsoC(certs, recargar),
    tarjeta('Certificados electrónicos',
      h('p', null, 'Para remitir registros a la AEAT (pruebas o producción) se necesita un certificado de la empresa, de un representante o de alguien apoderado. El simulador local no necesita certificado.'),
      listado),
    tarjeta('Cargar certificado', form),
    tarjetaMisCertificadosEmpresaC(recargar),
    tarjetaLegitimacionC(recargar),
  ];
}

function pasoProductor(emp, recargar) {
  const p = emp.productor && typeof emp.productor === 'object' ? emp.productor : {};
  const confirmado = !!(p.nif && p.confirmado_en);
  const dis = !esSuperadmin();
  const nombre = entrada({ value: p.nombre || '', required: true, maxlength: 120, disabled: dis });
  const nif = entrada({ value: p.nif || '', required: true, maxlength: 20, class: 'mono', spellcheck: 'false', disabled: dis });
  const domicilio = h('textarea', { rows: 2, maxlength: 300, disabled: dis });
  domicilio.value = p.domicilio || '';
  const guardar = h('button', { type: 'submit', class: 'btn btn-primario', disabled: dis }, confirmado ? 'Actualizar y confirmar' : 'Confirmar productor');
  const form = h('form', { class: 'form' },
    campo('Nombre o razón social del productor', nombre),
    campo('NIF del productor', nif),
    campo('Dirección postal de contacto', domicilio),
    h('div', { class: 'acciones' }, guardar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await conEspera(guardar, async () => {
      try {
        await api('/productor', { metodo: 'PUT', json: { nombre: nombre.value.trim(), nif: nif.value.trim().toUpperCase(), domicilio: domicilio.value.trim() } });
        notificar('Productor del sistema confirmado.', 'ok');
        recargar();
      } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
    }, 'Guardando…');
  });
  let aviso;
  if (confirmado) aviso = alerta('ok', h('strong', null, 'Productor confirmado '), `el ${fmtFechaHora(p.confirmado_en)}.`);
  else if (dis) {
    aviso = alerta('info', h('strong', null, 'Productor sin identificar. '), 'Lo identifica y confirma el operador de la plataforma; '
      + 'no es una tarea de su empresa ni tiene que hacer nada en este paso. Hasta entonces, la emisión en producción permanece bloqueada.');
  } else aviso = alerta('warn', h('strong', null, 'Productor sin identificar. '), 'La emisión en producción permanece bloqueada hasta que lo identifique y confirme.');
  return tarjeta('Productor del sistema',
    h('p', null, 'El productor del sistema es la persona o entidad que asume el papel de productor de este sistema informático de facturación '
      + '(art. 13 del RD 1007/2023): quien lo produce o lo pone a disposición de los usuarios y suscribe su declaración responsable. '
      + 'En esta plataforma lo es su operador. Su NIF y nombre viajan en cada registro de facturación (bloque «SistemaInformatico»).'),
    aviso,
    kv([
      ['Nombre', p.nombre || '—'],
      ['NIF', p.nif || '—'],
      ['Domicilio', p.domicilio || '—'],
      ['Número de instalación', p.numero_instalacion ? h('span', { class: 'mono' }, p.numero_instalacion) : '—'],
    ]),
    h('p', { class: 'ayuda' }, dis
      ? 'Afecta a toda la instalación (todas las empresas). Solo el operador de la plataforma puede identificarlo o modificarlo.'
      : 'Afecta a toda la instalación (todas las empresas): como operador de la plataforma, usted es el productor del sistema.'),
    dis ? null : form);
}

const ROLES = [
  ['emisor', 'Emisor', 'Importa documentos, revisa borradores y emite facturas.'],
  ['lector', 'Lector', 'Solo consulta.'],
  ['admin', 'Administrador', 'Además configura la empresa, series y certificados, y puede anular y subsanar registros.'],
];

function listaRequisitos(requisitos) {
  return h('ul', { class: 'requisitos' }, requisitos.map((r) => h('li', { class: r.cumplido ? 'req-ok' : 'req-no' },
    h('span', { class: 'req-icono', 'aria-hidden': 'true' }, r.cumplido ? '✓' : '✗'),
    h('div', null,
      h('span', { class: 'req-texto' }, r.texto),
      ' ', r.cumplido ? pill('Cumplido', 'ok') : pill('Pendiente', 'danger'),
      r.detalle ? h('span', { class: 'muted bloque' }, String(r.detalle)) : null))));
}

function pasoProduccion(prod, error, emp) {
  if (error) return tarjeta('Paso a producción', cajaError(error, () => enrutar({ forzar: true })));
  const requisitos = comoLista(prod && prod.requisitos);
  const lista = listaRequisitos(requisitos);
  const reqPruebas = comoLista(prod && prod.requisitos_pruebas);
  const resumen = prod && prod.resumen_primer_envio;
  const otrosPendientes = requisitos.filter((r) => !r.cumplido && r.clave !== 'autorizacion_primer_envio');
  const autorizado = requisitos.some((r) => r.clave === 'autorizacion_primer_envio' && r.cumplido);
  const tarjetas = [tarjeta('Requisitos para emitir en producción',
    prod && prod.habilitada
      ? alerta('ok', h('strong', null, 'Producción habilitada. '), 'Las facturas expedidas en PRODUCCIÓN son reales y se remiten a la AEAT.')
      : alerta('warn', 'La producción no está habilitada: deben cumplirse todos los requisitos.'),
    requisitos.length ? lista : h('p', { class: 'vacio' }, 'Sin información de requisitos.'))];
  if (reqPruebas.length) {
    tarjetas.unshift(tarjeta('Requisitos para el entorno de pruebas de la AEAT',
      prod.pruebas_habilitadas
        ? alerta('ok', 'El entorno de pruebas de la AEAT está habilitado para esta empresa.')
        : alerta('info', 'Antes de producción debe superarse al menos un envío aceptado en el entorno de pruebas de la AEAT.'),
      listaRequisitos(reqPruebas)));
  }
  if (resumen) {
    const frase = (typeof resumen === 'object' && (resumen.texto_confirmacion_requerido || resumen.texto_requerido))
      || `AUTORIZO EL ENVÍO REAL A LA AEAT DE ${emp.nif || ''}`;
    const texto = entrada({ autocomplete: 'off', spellcheck: 'false', disabled: !esAdmin() });
    const chk = h('input', { type: 'checkbox', id: uid('chk'), disabled: !esAdmin() });
    const autorizar = h('button', { type: 'submit', class: 'btn btn-peligro', disabled: true }, 'Autorizar el primer envío real');
    const actualizar = () => { autorizar.disabled = !esAdmin() || !chk.checked || texto.value.trim().toUpperCase() !== frase.toUpperCase(); };
    texto.addEventListener('input', actualizar);
    chk.addEventListener('change', actualizar);
    const form = h('form', { class: 'form' },
      campo(`Para autorizar, escriba exactamente: ${frase}`, texto),
      h('div', { class: 'casilla casilla-destacada' }, chk, h('label', { for: chk.id },
        `He revisado el resumen y autorizo el primer envío real de registros a la AEAT en nombre de ${emp.razon_social || 'la empresa'} (${emp.nif || ''}).`)),
      otrosPendientes.length ? alerta('warn', 'Aún hay requisitos pendientes. Puede registrar la autorización, pero la producción no se habilitará hasta que se cumplan todos.') : null,
      h('div', { class: 'acciones' }, autorizar));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      actualizar();
      if (autorizar.disabled) return;
      await conEspera(autorizar, async () => {
        try {
          await api(`/empresas/${enc(emp.id)}/produccion/autorizacion`, { metodo: 'POST', json: { texto_confirmacion: texto.value.trim() } });
          notificar('Autorización del primer envío real registrada.', 'ok');
          enrutar({ forzar: true });
        } catch (err) { notificar(err.message, 'danger'); }
      }, 'Registrando…');
    });
    tarjetas.push(tarjeta('Autorización del primer envío real',
      h('p', null, 'Antes de la primera comunicación real a la AEAT debe revisar qué se va a enviar y autorizarlo expresamente.'),
      renderResumenPrimerEnvio(resumen),
      autorizado ? alerta('ok', 'Ya consta una autorización del primer envío real. Puede registrar otra si lo necesita.') : null,
      form));
  }
  return tarjetas;
}

function renderResumenPrimerEnvio(r) {
  if (!r || typeof r !== 'object') return renderGenerico(r);
  const conocidas = ['empresa', 'destino', 'datos_remitidos', 'no_se_remite', 'facturas_pendientes', 'texto_confirmacion_requerido', 'texto_requerido'];
  const resto = Object.fromEntries(Object.entries(r).filter(([k]) => !conocidas.includes(k)));
  const facturas = comoLista(r.facturas_pendientes);
  return h('div', { class: 'resumen-envio' },
    kv([
      ['Empresa', r.empresa ? `${r.empresa.razon_social || ''} · NIF ${r.empresa.nif || ''}` : undefined],
      ['Destino', r.destino],
    ]),
    Array.isArray(r.datos_remitidos) ? [h('h3', null, 'Datos que se comunican a la AEAT'), h('ul', { class: 'lista-simple' }, r.datos_remitidos.map((x) => h('li', null, String(x))))] : null,
    Array.isArray(r.no_se_remite) ? [h('h3', null, 'No se comunica'), h('ul', { class: 'lista-simple' }, r.no_se_remite.map((x) => h('li', null, String(x))))] : null,
    'facturas_pendientes' in r ? [h('h3', null, 'Facturas de producción que se remitirán'),
      tabla([
        { titulo: 'Número', celda: (f) => f.num_serie },
        { titulo: 'Fecha', celda: (f) => fmtFecha(f.fecha_expedicion) },
        { titulo: 'Destinatario', celda: (f) => `${f.destinatario_nombre || ''} ${f.destinatario_nif ? `(${f.destinatario_nif})` : ''}` },
        { titulo: 'Importe', clase: 'num', celda: (f) => fmtImporte(f.importe_total) },
      ], facturas, { vacio: 'Todavía no hay facturas expedidas en producción.' })] : null,
    Object.keys(resto).length ? renderGenerico(resto) : null);
}

/* ======================================================================================
 * Campos extraídos: recorrido, etiquetas y procedencia
 * ====================================================================================== */

const NO_CAMPOS = new Set(['paginas', 'indicios', 'nifs_no_validos', 'nifs_detectados', 'version_extractor', 'aviso_lineas']);
const RENOMBRAR_CAMPO = { numero: 'num_serie', fecha: 'fecha_documento', cuota_iva: 'cuota_total', total_factura: 'importe_total' };

function esCampoExtraido(n) {
  return !!n && typeof n === 'object' && !Array.isArray(n) && 'valor' in n && ('metodo' in n || 'confianza' in n || 'bbox' in n);
}

/** Acepta la extracción tal cual, envuelta en {datos: …} o como texto JSON. */
function normalizarExtraccion(x) {
  let e = parseJSON(x);
  if (e && !e.paginas && e.datos && typeof e.datos !== 'undefined' && !esCampoExtraido(e)) {
    const d = parseJSON(e.datos);
    if (d && typeof d === 'object') e = d;
  }
  return e && typeof e === 'object' ? e : null;
}

/**
 * Recorre la extracción (anidada) o un mapa plano campo → {valor, pagina, bbox, metodo, confianza, avisos}
 * y devuelve un Map con la ruta del borrador como clave.
 */
function camposExtraidos(raiz) {
  const out = new Map();
  (function visitar(nodo, ruta) {
    if (esCampoExtraido(nodo)) { out.set(RENOMBRAR_CAMPO[ruta] || ruta, { ...nodo, rutaOrigen: ruta }); return; }
    if (Array.isArray(nodo)) nodo.forEach((v, i) => visitar(v, ruta ? `${ruta}.${i}` : String(i)));
    else if (nodo && typeof nodo === 'object') {
      for (const [k, v] of Object.entries(nodo)) {
        if (!ruta && NO_CAMPOS.has(k)) continue;
        visitar(v, ruta ? `${ruta}.${k}` : k);
      }
    }
  })(raiz || {}, '');
  return out;
}

function etiquetaCampo(ruta) {
  if (ETIQUETAS_CAMPO[ruta]) return ETIQUETAS_CAMPO[ruta];
  let m = /^lineas\.(\d+)(?:\.(\w+))?$/.exec(ruta);
  if (m) return `Línea ${Number(m[1]) + 1}${m[2] ? `: ${(COLUMNAS_LINEA[m[2]] || m[2]).toLowerCase()}` : ''}`;
  m = /^desglose\.(\d+)(?:\.(\w+))?$/.exec(ruta);
  if (m) return `Desglose ${Number(m[1]) + 1}${m[2] ? `: ${COLUMNAS_DESGLOSE[m[2]] || COLUMNAS_DESGLOSE_EXENTA[m[2]] || m[2]}` : ''}`;
  m = /^referencias\.(\w+)$/.exec(ruta);
  if (m) return `Referencia: ${(REFERENCIAS.find(([k]) => k === m[1]) || [null, humanizar(m[1])])[1]}`;
  return humanizar(ruta);
}

function tonoConfianza(info) {
  if (!info || info.metodo === 'no_encontrado' || ((info.valor === null || info.valor === undefined) && !info.bbox)) return 'no';
  if (typeof info.confianza === 'number' && info.confianza < UMBRAL_CONFIANZA) return 'dudosa';
  return 'ok';
}

function textoProcedencia(info) {
  if (!info) return '';
  const partes = [];
  if (info.texto) partes.push(`Texto leído: «${info.texto}»`);
  for (const a of info.avisos || []) if (a) partes.push(String(a));
  return partes.join('\n');
}

/** Pequeña etiqueta con la procedencia: página, método y confianza. */
function chipProcedencia(info) {
  if (!info) return null;
  const tono = tonoConfianza(info);
  if (tono === 'no') return h('span', { class: 'chip chip-no', title: textoProcedencia(info) || null }, 'No encontrado');
  const partes = [];
  if (info.pagina) partes.push(`p. ${info.pagina}`);
  partes.push(METODOS[info.metodo] || info.metodo || 'Origen desconocido');
  if (typeof info.confianza === 'number') partes.push(fmtPct(info.confianza));
  return h('span', { class: `chip chip-${tono}`, title: textoProcedencia(info) || null },
    h('span', { class: 'sr-only' }, 'Procedencia: '), partes.join(' · '));
}

/* ======================================================================================
 * Visor de páginas con capa de recuadros
 * ====================================================================================== */

function crearVisor({ numPaginas = 0, url, tamanos = [], alt = 'Documento', sondear = false, alClicCaja }) {
  const cont = h('div', { class: 'visor' });
  const paginas = [];
  let cajas = [];
  let activo = null;

  function tamano(p) {
    const t = tamanos[p.n - 1];
    if (t && Number(t.ancho) > 0 && Number(t.alto) > 0) return { ancho: Number(t.ancho), alto: Number(t.alto) };
    // Sin tamaño en puntos: se estima con la resolución de rasterizado del servidor (110 ppp).
    if (p.img.naturalWidth) return { ancho: (p.img.naturalWidth * 72) / 110, alto: (p.img.naturalHeight * 72) / 110 };
    return null;
  }

  function dibujar(p) {
    vaciar(p.capa);
    const t = tamano(p);
    if (!t) return;
    const pct = (v) => `${Math.max(0, Math.min(100, v * 100))}%`;
    for (const c of cajas) {
      if (Number(c.pagina) !== p.n || !Array.isArray(c.bbox) || c.bbox.length !== 4) continue;
      const [x0, y0, x1, y1] = c.bbox.map(Number);
      if (![x0, y0, x1, y1].every(Number.isFinite)) continue;
      const m = 1.5; // margen en puntos para que el recuadro no tape el texto
      const d = h('div', {
        class: ['caja', `caja-${c.tono}`, c.incidencia && `caja-inc-${c.incidencia}`, activo && c.campo === activo && 'activa'],
        title: c.titulo || null, dataset: { campo: c.campo },
      });
      d.style.left = pct((x0 - m) / t.ancho);
      d.style.top = pct((y0 - m) / t.alto);
      d.style.width = pct((x1 - x0 + 2 * m) / t.ancho);
      d.style.height = pct((y1 - y0 + 2 * m) / t.alto);
      p.capa.append(d);
    }
  }

  function agregar(n) {
    const img = h('img', { alt: `${alt}, página ${n}`, decoding: 'async', loading: sondear || n <= 2 ? 'eager' : 'lazy' });
    const capa = h('div', { class: 'capa-cajas', 'aria-hidden': 'true' });
    const lienzo = h('div', { class: 'pagina-lienzo' }, img, capa);
    const fig = h('figure', { class: 'pagina' }, lienzo, h('figcaption', null, `Página ${n}`));
    const p = { n, img, capa, fig, lienzo };
    paginas.push(p);
    cont.append(fig);
    img.addEventListener('load', () => {
      dibujar(p);
      if (sondear && n < MAX_PAGINAS_SONDEO) agregar(n + 1);
    });
    img.addEventListener('error', () => {
      if (sondear && n > 1) { fig.remove(); paginas.splice(paginas.indexOf(p), 1); return; }
      fig.classList.add('pagina-error');
      lienzo.append(h('p', { class: 'pagina-fallo' }, 'No se pudo cargar la imagen de esta página.'));
    });
    img.src = url(n);
    if (tamanos[n - 1]) dibujar(p);
  }

  if (sondear) agregar(1);
  else for (let n = 1; n <= Math.max(1, numPaginas); n += 1) agregar(n);

  cont.addEventListener('click', (e) => {
    const c = e.target.closest && e.target.closest('.caja');
    if (c && alClicCaja) alClicCaja(c.dataset.campo);
  });

  function desplazarHacia(el) {
    const panel = el.closest('.col-visor');
    if (!panel || panel.scrollHeight <= panel.clientHeight + 4) return;
    const pr = panel.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    if (er.top >= pr.top + 48 && er.bottom <= pr.bottom - 24) return;
    panel.scrollBy({ top: er.top - pr.top - pr.height / 3, behavior: reducirMovimiento() ? 'auto' : 'smooth' });
  }

  return {
    el: cont,
    setCajas(lista) { cajas = lista || []; paginas.forEach(dibujar); },
    resaltar(campo, desplazar = false) {
      activo = campo || null;
      let primera = null;
      cont.querySelectorAll('.caja').forEach((d) => {
        const on = !!activo && d.dataset.campo === activo;
        d.classList.toggle('activa', on);
        if (on && !primera) primera = d;
      });
      if (desplazar && primera && esEscritorio()) desplazarHacia(primera);
    },
  };
}

/** Enlaza el foco y el paso del ratón sobre los campos de un panel con los recuadros del visor. */
function enlazarResaltado(panel, obtenerVisor) {
  const campoDe = (el) => (el && el.closest ? (el.closest('[data-campo]') || {}).dataset : null);
  const actual = () => {
    const d = campoDe(document.activeElement);
    return d && panel.contains(document.activeElement) ? d.campo : null;
  };
  panel.addEventListener('focusin', (e) => {
    const d = campoDe(e.target);
    const v = obtenerVisor();
    if (v) v.resaltar(d ? d.campo : null, true);
  });
  panel.addEventListener('focusout', () => {
    window.setTimeout(() => { const v = obtenerVisor(); if (v) v.resaltar(actual()); }, 0);
  });
  panel.addEventListener('mouseover', (e) => {
    const d = campoDe(e.target);
    const v = obtenerVisor();
    if (v && d) v.resaltar(d.campo);
  });
  panel.addEventListener('mouseleave', () => { const v = obtenerVisor(); if (v) v.resaltar(actual()); });
}

function leyendaCajas(noEncontrados = 0) {
  const item = (clase, texto) => h('span', { class: 'leyenda-item' }, h('span', { class: `muestra ${clase}`, 'aria-hidden': 'true' }), texto);
  return h('div', { class: 'leyenda' },
    item('caja-ok', `Lectura fiable (≥ ${Math.round(UMBRAL_CONFIANZA * 100)} %)`),
    item('caja-dudosa', `Lectura dudosa (< ${Math.round(UMBRAL_CONFIANZA * 100)} %)`),
    item('caja-inc-bloqueo', 'Campo con incidencia'),
    item('caja-no', noEncontrados ? `No encontrado: ${noEncontrados} campo(s) sin posición en el documento` : 'No encontrado (sin posición)'));
}

function pestanas(defs) {
  const lista = h('div', { role: 'tablist', class: 'pestanas', 'aria-label': 'Vistas' });
  const paneles = [];
  const botones = defs.map((d, i) => {
    const b = h('button', {
      type: 'button', role: 'tab', id: `tab-${d.id}`, 'aria-controls': `panel-${d.id}`,
      'aria-selected': i === 0 ? 'true' : 'false', tabindex: i === 0 ? '0' : '-1',
    }, d.titulo);
    b.addEventListener('click', () => mostrar(d.id));
    b.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const j = (i + (e.key === 'ArrowRight' ? 1 : defs.length - 1)) % defs.length;
      mostrar(defs[j].id);
      botones[j].focus();
    });
    paneles.push(h('div', { role: 'tabpanel', id: `panel-${d.id}`, 'aria-labelledby': `tab-${d.id}`, hidden: i !== 0, class: 'panel-pestana' }, d.panel));
    return b;
  });
  lista.append(...botones);
  function mostrar(id) {
    defs.forEach((d, i) => {
      const on = d.id === id;
      botones[i].setAttribute('aria-selected', on ? 'true' : 'false');
      botones[i].tabIndex = on ? 0 : -1;
      paneles[i].hidden = !on;
      if (on && d.alMostrar) d.alMostrar();
    });
  }
  return { el: h('div', { class: 'con-pestanas' }, defs.length > 1 ? lista : null, paneles), mostrar };
}

/* ======================================================================================
 * Importar y lista de documentos
 * ====================================================================================== */

function etiquetaClasificacion(doc) {
  const c = CLASIFICACIONES[doc.clasificacion];
  const ref = doc.bloqueo_emision ? h('span', { class: 'bloque' }, pill('Referencia: no emitible fuera del simulador', 'danger', doc.bloqueo_emision)) : null;
  if (!doc.clasificacion) return h('span', null, pill('Sin clasificar', 'warn'), ref);
  return h('span', null, pill(c ? c.etiqueta : doc.clasificacion, c ? c.tono : 'neutral'),
    doc.clasificacion_confirmada_en ? null : h('span', { class: 'muted bloque' }, 'Sugerida, sin confirmar'), ref);
}

async function paginaImportar(ctx) {
  titulo('Documentos');
  const eid = app.empresaId;
  const resultados = h('div', { class: 'resultados-subida', 'aria-live': 'polite' });
  const listado = h('div', null, cargando());
  const input = h('input', { type: 'file', accept: 'application/pdf,.pdf', multiple: true, class: 'oculto', id: uid('pdf'), tabindex: '-1' });
  const elegir = boton('Seleccionar archivos PDF', () => input.click(), { tipo: 'primario', disabled: !puedeEmitirRol() });
  const zona = h('div', { class: 'dropzone' },
    h('p', { class: 'dropzone-titulo' }, 'Arrastre aquí uno o varios PDF'),
    h('p', { class: 'muted' }, 'o'),
    elegir, input,
    h('p', { class: 'ayuda' }, 'Borradores, proformas o presupuestos de su empresa, facturas ya expedidas para archivar o facturas recibidas. '
      + 'El original se conserva intacto: se analiza como datos y nunca se ejecuta su contenido.'));
  let subiendo = false;

  async function subir(archivos) {
    if (subiendo) return;
    const todos = Array.from(archivos || []);
    if (!todos.length) return;
    const pdfs = todos.filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
    const rechazados = todos.filter((f) => !pdfs.includes(f));
    vaciar(resultados);
    if (rechazados.length) {
      resultados.append(alerta('warn', `Se ignoran ${rechazados.length} archivo(s) que no son PDF: `, rechazados.map((f) => f.name).join(', ')));
    }
    if (!pdfs.length) return;
    const fd = new FormData();
    pdfs.forEach((f) => fd.append('archivos', f, f.name));
    subiendo = true;
    zona.classList.add('ocupada');
    elegir.disabled = true;
    const aviso = cargando(`Subiendo y analizando ${pdfs.length} archivo(s)… Puede tardar si requieren reconocimiento óptico (OCR).`);
    resultados.append(aviso);
    try {
      const r = comoLista(await api(`/empresas/${enc(eid)}/documentos`, { metodo: 'POST', form: fd }), 'documentos', 'resultados');
      aviso.remove();
      resultados.append(tarjeta('Resultado de la importación', h('ul', { class: 'lista-resultados' }, r.map((x) => {
        const id = x.documento_id;
        let estado;
        if (x.error) estado = alerta('danger', h('strong', null, 'Error: '), x.error);
        else if (x.duplicado) {
          const previo = x.duplicado_de && typeof x.duplicado_de === 'object' ? x.duplicado_de.id : x.duplicado_de;
          estado = alerta('warn', h('strong', null, 'Duplicado: '),
            x.mensaje || 'este mismo archivo ya se había importado; no se ha creado un documento nuevo.', ' ',
            enlace('Ver el documento existente', `#/documento/${enc(id || previo)}`));
        } else {
          const dupF = comoLista(x.duplicados_fiscales);
          estado = h('div', null, pill('Importado', 'ok'), ' ', x.estado ? pillDe(ESTADO_DOCUMENTO, x.estado) : null, ' ',
            id ? enlace('Abrir y clasificar', `#/documento/${enc(id)}`) : null,
            dupF.length ? alerta('warn', h('strong', null, 'Posible duplicado fiscal: '), 'el mismo emisor y número figuran en ',
              dupF.map((d, i) => [i ? ', ' : '', d.tipo === 'factura'
                ? enlace(`la factura ${d.num_serie || d.id}`, `#/factura/${enc(d.id)}`)
                : enlace(`el documento «${d.nombre || d.id}»`, `#/documento/${enc(d.id)}`)]), '.') : null);
        }
        return h('li', null, h('span', { class: 'nombre-archivo' }, x.nombre || '—'), estado);
      }))));
      cargarLista();
    } catch (err) {
      aviso.remove();
      if (err.estado !== 401) resultados.append(cajaError(err));
    } finally {
      subiendo = false;
      zona.classList.remove('ocupada');
      elegir.disabled = !puedeEmitirRol();
      input.value = '';
    }
  }

  input.addEventListener('change', () => subir(input.files));
  let profundidad = 0;
  zona.addEventListener('dragenter', (e) => { e.preventDefault(); profundidad += 1; zona.classList.add('arrastrando'); });
  zona.addEventListener('dragover', (e) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; });
  zona.addEventListener('dragleave', () => { profundidad = Math.max(0, profundidad - 1); if (!profundidad) zona.classList.remove('arrastrando'); });
  zona.addEventListener('drop', (e) => {
    e.preventDefault();
    profundidad = 0;
    zona.classList.remove('arrastrando');
    if (!puedeEmitirRol()) { notificar('Su rol no permite importar documentos.', 'warn'); return; }
    if (e.dataTransfer) subir(e.dataTransfer.files);
  });
  // Evita que el navegador abra un PDF soltado fuera de la zona.
  const evitar = (e) => { if (!zona.contains(e.target)) e.preventDefault(); };
  window.addEventListener('dragover', evitar);
  window.addEventListener('drop', evitar);
  ctx.alSalir(() => { window.removeEventListener('dragover', evitar); window.removeEventListener('drop', evitar); });

  // Filtros
  const q = entrada({ type: 'search', placeholder: 'Nombre, número, cliente…', value: ctx.query.get('q') || '' });
  const clas = selector([['', 'Todas'], ...Object.entries(CLASIFICACIONES).map(([k, c]) => [k, c.etiqueta])], ctx.query.get('clasificacion') || '');
  let temporizador = null;
  q.addEventListener('input', () => { window.clearTimeout(temporizador); temporizador = window.setTimeout(cargarLista, 300); });
  clas.addEventListener('change', cargarLista);
  ctx.alSalir(() => window.clearTimeout(temporizador));

  async function cargarLista() {
    const params = new URLSearchParams();
    if (q.value.trim()) params.set('q', q.value.trim());
    if (clas.value) params.set('clasificacion', clas.value);
    fijarQuery({ q: q.value.trim(), clasificacion: clas.value });
    vaciar(listado).append(cargando());
    try {
      const docs = comoLista(await api(`/empresas/${enc(eid)}/documentos?${params}`), 'documentos');
      if (!ctx.vigente()) return;
      vaciar(listado).append(h('p', { class: 'muted' }, `${docs.length} documento(s)`), tabla([
        {
          titulo: 'Documento',
          celda: (d) => [h('a', { href: `#/documento/${enc(d.id)}`, class: 'nombre-archivo' }, d.nombre_archivo || `Documento ${d.id}`),
            h('span', { class: 'muted bloque' }, `${fmtFechaHora(d.subido_en)}${d.paginas ? ` · ${d.paginas} pág.` : ''}`)],
        },
        { titulo: 'Clasificación', celda: (d) => etiquetaClasificacion(d) },
        {
          titulo: 'Datos leídos',
          celda: (d) => [d.num_serie ? h('span', { class: 'mono bloque' }, d.num_serie) : null,
            d.destinatario ? h('span', { class: 'bloque' }, typeof d.destinatario === 'object' ? (d.destinatario.nombre || d.destinatario.nif || '') : d.destinatario) : null,
            d.importe_total ? h('span', { class: 'bloque' }, fmtImporte(d.importe_total)) : null],
        },
        { titulo: 'Estado', celda: (d) => pillDe(ESTADO_DOCUMENTO, d.estado) },
        {
          titulo: 'Siguiente paso',
          celda: (d) => {
            if (d.factura_id) return enlace('Ver factura', `#/factura/${enc(d.factura_id)}`);
            if (d.borrador_id) return enlace('Revisar borrador', `#/borrador/${enc(d.borrador_id)}`);
            if (d.clasificacion === 'preparatorio' && d.clasificacion_confirmada_en) return enlace('Crear borrador', `#/documento/${enc(d.id)}`);
            if (!d.clasificacion_confirmada_en) return enlace('Clasificar', `#/documento/${enc(d.id)}`);
            return enlace('Ver', `#/documento/${enc(d.id)}`);
          },
        },
      ], docs, { vacio: 'No hay documentos que coincidan.', leyenda: 'Documentos importados' }));
    } catch (err) {
      if (ctx.vigente() && err.estado !== 401) vaciar(listado).append(cajaError(err, cargarLista));
    }
  }

  montar(ctx,
    cabeceraPagina('Documentos', { meta: [h('span', null, 'Suba PDF → revise los datos → confirme la emisión → descargue el PDF definitivo y consulte el estado en la AEAT.')] }),
    tarjeta('Importar PDF', zona, resultados),
    tarjeta('Documentos importados',
      h('form', { class: 'filtros', role: 'search', onsubmit: (e) => { e.preventDefault(); cargarLista(); } },
        campo('Buscar', q), campo('Clasificación', clas)),
      listado));
  cargarLista();
}

/* ======================================================================================
 * Documento: clasificación e inspección
 * ====================================================================================== */

const CONTENIDO_ACTIVO = {
  javascript: 'JavaScript', accion_apertura: 'acción al abrir', acciones_automaticas: 'acciones automáticas',
  lanzar_aplicacion: 'lanzamiento de aplicaciones', adjuntos: 'archivos adjuntos', multimedia: 'contenido multimedia',
  formulario_xfa: 'formulario XFA', envio_formulario: 'envío de formularios',
};

function tamanosDe(ext, insp) {
  if (ext && Array.isArray(ext.paginas) && ext.paginas.length) return ext.paginas.map((p) => ({ ancho: p.ancho, alto: p.alto }));
  if (insp && Array.isArray(insp.tamanos_pagina)) return insp.tamanos_pagina.map((t) => ({ ancho: t[0], alto: t[1] }));
  return [];
}

function avisosInspeccion(doc, insp, ext) {
  const out = [];
  if (doc.estado === 'error') out.push(alerta('danger', h('strong', null, 'Error al procesar: '), doc.error || 'El documento no se pudo procesar.'));
  const qrs = (Array.isArray(insp.qr) ? insp.qr : []).filter((q) => q && q.tributario);
  for (const q of qrs) {
    out.push(alerta('warn', h('strong', null, 'QR tributario detectado. '),
      `El documento ya contiene un código QR de la AEAT (${q.modalidad === 'no_verifactu' ? 'No VERI*FACTU' : 'VERI*FACTU'}, entorno ${q.entorno || '—'}) `
      + `para la factura ${q.numserie || '—'} del NIF ${q.nif || '—'}${q.importe ? ` por ${fmtImporte(q.importe)}` : ''}. `
      + 'Es una factura ya expedida por un sistema de facturación: no debe volver a expedirse.'));
  }
  if (insp.firmado) {
    out.push(alerta('warn', h('strong', null, 'Documento firmado. '),
      'El original está firmado; se conserva intacto. El PDF definitivo será un documento distinto y no conserva la firma'));
  }
  const activos = Array.isArray(insp.contenido_activo) ? insp.contenido_activo : [];
  if (activos.length) {
    out.push(alerta('warn', h('strong', null, 'Contenido activo. '),
      `El PDF contiene ${activos.map((a) => CONTENIDO_ACTIVO[a] || a).join(', ')}. No se ejecuta: la aplicación lo analiza como datos `
      + 'y lo muestra como imágenes generadas en el servidor. Descargue el original solo si confía en su procedencia.'));
  }
  if (insp.cifrado) out.push(alerta('info', 'El PDF estaba protegido con cifrado sin contraseña de apertura.'));
  if (ext && ext.aviso_lineas) out.push(alerta('info', ext.aviso_lineas));
  return out;
}

async function paginaDocumento(ctx) {
  const did = ctx.params[0];
  const r = await api(`/documentos/${enc(did)}`);
  if (!ctx.vigente()) return;
  const doc = r.documento || {};
  const insp = parseJSON(r.inspeccion) || parseJSON(doc.inspeccion) || {};
  const sug = r.clasificacion_sugerida || {};
  const ext = normalizarExtraccion(r.extraccion) || {};
  const borradorId = r.borrador_id || doc.borrador_id;
  titulo(doc.nombre_archivo || `Documento ${did}`);

  const campos = camposExtraidos(ext);
  const indicios = ext.indicios && typeof ext.indicios === 'object' ? ext.indicios : {};
  const nifsMalos = Array.isArray(ext.nifs_no_validos) ? ext.nifs_no_validos : [];
  const cajas = [];
  for (const [ruta, info] of campos) {
    if (info.bbox) cajas.push({ campo: ruta, pagina: info.pagina, bbox: info.bbox, tono: tonoConfianza(info), titulo: `${etiquetaCampo(ruta)}: ${textoValor(info.valor)}` });
  }
  for (const [k, v] of Object.entries(indicios)) {
    if (v && v.bbox) cajas.push({ campo: `indicio.${k}`, pagina: v.pagina, bbox: v.bbox, tono: 'indicio', titulo: INDICIOS[k] || k });
  }
  nifsMalos.forEach((n, i) => { if (n.bbox) cajas.push({ campo: `nif_no_valido.${i}`, pagina: n.pagina, bbox: n.bbox, tono: 'dudosa', titulo: `Posible NIF ilegible: ${n.texto}` }); });
  const noEncontrados = [...campos.values()].filter((c) => tonoConfianza(c) === 'no').length;

  const visor = crearVisor({
    numPaginas: Number(doc.paginas) || Number(insp.paginas) || (ext.paginas || []).length || 1,
    url: (n) => `/api/documentos/${enc(did)}/paginas/${n}.png`,
    tamanos: tamanosDe(ext, insp),
    alt: `Documento original ${doc.nombre_archivo || ''}`,
    alClicCaja: (campo) => {
      const el = [...panel.querySelectorAll('[data-campo]')].find((x) => x.dataset.campo === campo);
      if (el) { el.scrollIntoView({ block: 'nearest' }); el.focus(); }
    },
  });
  visor.setCajas(cajas);

  // --- Clasificación
  const confirmada = !!doc.clasificacion_confirmada_en;
  // clasificacion_motivo guarda la sugerencia (JSON) y, tras confirmar, la confirmación con su motivo.
  const motivoConfirmado = (sug.confirmacion && sug.confirmacion.motivo)
    || (typeof doc.clasificacion_motivo === 'string' && !parseJSON(doc.clasificacion_motivo) ? doc.clasificacion_motivo : '');
  const bloqueadaReclasificacion = !!(borradorId || doc.factura_id);
  const sugerida = sug.sugerida;
  const etiquetaSug = CLASIFICACIONES[sugerida] ? CLASIFICACIONES[sugerida].etiqueta : (sugerida === 'pendiente_confirmar' ? 'Requiere su decisión' : (sugerida || '—'));
  const preseleccion = confirmada ? doc.clasificacion : (CLASIFICACIONES[sugerida] ? sugerida : null);
  const radios = grupoRadios('clasificacion', '¿Qué es este documento?',
    Object.entries(CLASIFICACIONES).map(([k, c]) => [k, c.etiqueta, c.explicacion]), preseleccion);
  const motivo = h('textarea', { rows: 2, maxlength: 500, required: true, placeholder: 'Por qué lo clasifica así (queda registrado)' });
  const confirmarBtn = h('button', { type: 'submit', class: 'btn btn-primario' }, confirmada ? 'Cambiar clasificación' : 'Confirmar clasificación');
  const formClas = h('form', { class: 'form' }, radios, campo('Motivo', motivo), h('div', { class: 'acciones' }, confirmarBtn));
  if (bloqueadaReclasificacion || !puedeEmitirRol()) formClas.querySelectorAll('input,textarea,button').forEach((x) => { x.disabled = true; });
  formClas.addEventListener('submit', async (e) => {
    e.preventDefault();
    const valor = valorRadio(formClas, 'clasificacion');
    if (!valor) { notificar('Elija una clasificación.', 'warn'); return; }
    if (!motivo.value.trim()) { notificar('Indique el motivo de la clasificación.', 'warn'); motivo.focus(); return; }
    await conEspera(confirmarBtn, async () => {
      try {
        await api(`/documentos/${enc(did)}/clasificacion`, { metodo: 'POST', json: { clasificacion: valor, motivo: motivo.value.trim() } });
        notificar('Clasificación confirmada.', 'ok');
        enrutar({ forzar: true });
      } catch (err) { notificar(err.message, 'danger'); }
    }, 'Guardando…');
  });

  let accionBorrador = null;
  if (borradorId) {
    accionBorrador = h('div', { class: 'acciones' }, enlace('Abrir el borrador', `#/borrador/${enc(borradorId)}`, { tipo: 'primario' }));
  } else if (confirmada && doc.clasificacion === 'preparatorio') {
    const crear = boton('Crear borrador', async () => {
      await conEspera(crear, async () => {
        try {
          const rb = await api(`/documentos/${enc(did)}/borrador`, { metodo: 'POST', json: {} });
          irA(`/borrador/${enc(rb.borrador_id)}`);
        } catch (err) { notificar(err.message, 'danger'); }
      }, 'Creando…');
    }, { tipo: 'primario', disabled: !puedeEmitirRol() });
    accionBorrador = h('div', null,
      h('p', null, 'Se creará un borrador editable con los datos leídos. Nada tiene efectos fiscales hasta que lo emita.'),
      h('div', { class: 'acciones' }, crear));
  }

  const cardClas = tarjeta('Clasificación',
    h('div', { class: 'sugerencia' },
      h('p', null, h('strong', null, 'Sugerencia automática: '), etiquetaSug,
        typeof sug.confianza === 'number' ? h('span', { class: 'muted' }, ` (confianza ${fmtPct(sug.confianza)})`) : null),
      Array.isArray(sug.motivos) && sug.motivos.length ? h('ul', { class: 'lista-motivos' }, sug.motivos.map((m) => h('li', null, m))) : null),
    confirmada
      ? alerta('ok', h('strong', null, 'Clasificación confirmada: '), (CLASIFICACIONES[doc.clasificacion] || {}).etiqueta || doc.clasificacion,
        ` — ${fmtFechaHora(doc.clasificacion_confirmada_en)}`, motivoConfirmado ? h('span', { class: 'bloque' }, `Motivo: ${motivoConfirmado}`) : null)
      : alerta('warn', 'La clasificación no está confirmada. Debe confirmarla antes de procesar el documento.'),
    confirmada && CLASIFICACIONES[doc.clasificacion] ? h('p', { class: 'explicacion' }, CLASIFICACIONES[doc.clasificacion].explicacion) : null,
    bloqueadaReclasificacion ? h('p', { class: 'muted' }, 'Ya existe un borrador o una factura a partir de este documento: la clasificación no se puede cambiar.') : formClas,
    accionBorrador);

  const duplicados = comoLista(r.duplicados_fiscales);
  const cardDup = duplicados.length ? tarjeta('Posibles duplicados fiscales',
    alerta('warn', 'Existen facturas con datos fiscales coincidentes (número o importe y destinatario). Compruebe que no va a expedir la misma factura dos veces.'),
    h('ul', { class: 'lista-simple' }, duplicados.map((d) => {
      if (d && d.tipo === 'factura') {
        return h('li', null, enlace(`Factura ${d.num_serie || d.id}`, `#/factura/${enc(d.id)}`), ` expedida el ${fmtFecha(d.fecha_expedicion)} `,
          d.entorno ? badgeEntorno(d.entorno) : null);
      }
      if (d && d.tipo === 'documento') {
        return h('li', null, 'Documento ', enlace(d.nombre || `#${d.id}`, `#/documento/${enc(d.id)}`), ` importado el ${fmtFechaHora(d.subido_en)}`);
      }
      return h('li', null, renderGenerico(d));
    }))) : null;

  let cardBloqueo = null;
  if (esAdmin() || doc.bloqueo_emision) {
    const motivoBloq = entrada({ maxlength: 300, placeholder: 'p. ej. Factura de referencia ya expedida por otro sistema', disabled: !esAdmin() });
    const accion = async (btn, motivo) => {
      await conEspera(btn, async () => {
        try {
          await api(`/documentos/${enc(did)}/bloqueo`, { metodo: 'POST', json: { motivo } });
          notificar(motivo ? 'Documento marcado como referencia.' : 'Marca de referencia retirada.', 'ok');
          enrutar({ forzar: true });
        } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
      });
    };
    const marcar = boton('Marcar como referencia', (e) => {
      const m = motivoBloq.value.trim();
      if (!m) { notificar('Indique el motivo de la marca.', 'warn'); motivoBloq.focus(); return; }
      accion(e.currentTarget, m);
    }, { tipo: 'peligro', pequeno: true, disabled: !esAdmin() });
    const quitar = boton('Quitar la marca', async (e) => {
      const btn = e.currentTarget;
      const ok = await confirmar({ titulo: 'Quitar la marca de referencia', texto: 'El documento volverá a poder usarse para expedir facturas en cualquier entorno habilitado.', textoBoton: 'Quitar la marca' });
      if (ok) accion(btn, '');
    }, { pequeno: true, disabled: !esAdmin() });
    cardBloqueo = tarjeta('Referencia – no emitible fuera del simulador',
      doc.bloqueo_emision
        ? [alerta('danger', h('strong', null, 'Marcado como referencia: '), doc.bloqueo_emision),
          h('p', { class: 'ayuda' }, 'Solo puede usarse para pruebas en el simulador local.'), esAdmin() ? h('div', { class: 'acciones' }, quitar) : null]
        : [h('p', { class: 'ayuda' }, 'Marque los documentos de referencia (por ejemplo, facturas reales ya expedidas usadas como modelo) para que nunca puedan expedirse '
          + 'en el entorno de pruebas de la AEAT ni en producción. Solo administradores.'),
        campo('Motivo', motivoBloq), h('div', { class: 'acciones' }, marcar)]);
  }

  const avisos = avisosInspeccion(doc, insp, ext);
  const listaIndicios = Object.entries(indicios);
  const cardInsp = tarjeta('Inspección del archivo',
    avisos.length ? avisos : alerta('ok', 'Sin contenido activo, firmas ni QR tributarios detectados.'),
    listaIndicios.length ? [h('h3', null, 'Particularidades detectadas en el texto'),
      h('ul', { class: 'lista-datos' }, listaIndicios.map(([k, v]) => h('li', { class: 'dato', tabindex: '0', dataset: { campo: `indicio.${k}` } },
        h('span', { class: 'dato-etiqueta' }, INDICIOS[k] || humanizar(k)),
        h('span', { class: 'dato-valor' }, v && v.texto ? `«${v.texto}»` : '—'),
        v && v.pagina ? h('span', { class: 'chip chip-neutral' }, `p. ${v.pagina}`) : null)))] : null,
    nifsMalos.length ? [h('h3', null, 'Posibles NIF ilegibles'),
      h('ul', { class: 'lista-datos' }, nifsMalos.map((n, i) => h('li', { class: 'dato', tabindex: '0', dataset: { campo: `nif_no_valido.${i}` } },
        h('span', { class: 'dato-etiqueta mono' }, n.texto || '—'), h('span', { class: 'dato-valor' }, n.linea ? `«${n.linea}»` : '')))) ] : null);

  const cardDatos = h('details', { class: 'card desplegable', open: true },
    h('summary', null, h('h2', { class: 'card-titulo' }, 'Datos leídos del documento')),
    campos.size
      ? h('ul', { class: 'lista-datos' }, [...campos].map(([ruta, info]) => h('li', { class: 'dato', tabindex: info.bbox ? '0' : null, dataset: { campo: ruta } },
        h('span', { class: 'dato-etiqueta' }, etiquetaCampo(ruta)),
        h('span', { class: 'dato-valor' }, textoValor(info.valor)),
        chipProcedencia(info))))
      : h('p', { class: 'vacio' }, doc.estado === 'pendiente' ? 'El documento aún se está procesando.' : 'No se han extraído datos.'));

  const panel = h('div', { class: 'col-panel' }, cardInsp, cardClas, cardDup, cardDatos, cardBloqueo);
  enlazarResaltado(panel, () => visor);

  montar(ctx,
    cabeceraPagina(doc.nombre_archivo || `Documento ${did}`, {
      volver: ['#/importar', 'Documentos'],
      meta: [pillDe(ESTADO_DOCUMENTO, doc.estado), h('span', null, `Subido ${fmtFechaHora(doc.subido_en)}`),
        doc.paginas ? h('span', null, `${doc.paginas} página(s)`) : null],
      acciones: [enlace('Descargar original (PDF)', `/api/documentos/${enc(did)}/original`, { tipo: 'secundario', descarga: true })],
    }),
    h('div', { class: 'split' },
      h('div', { class: 'col-visor' }, h('h2', { class: 'sr-only' }, 'Páginas del documento original'), leyendaCajas(noEncontrados), visor.el),
      panel));

  if (doc.estado === 'pendiente') {
    const t = window.setTimeout(() => { if (ctx.vigente()) enrutar({ forzar: true }); }, 4000);
    ctx.alSalir(() => window.clearTimeout(t));
  }
}

/* ======================================================================================
 * Borrador: revisión, edición con motivo, incidencias y emisión
 * ====================================================================================== */

const NIVEL_TEXTO = { bloqueo: 'Bloqueo', confirmar: 'Requiere confirmación', aviso: 'Aviso' };

const SECCIONES_BORRADOR = [
  {
    id: 'documento', titulo: 'Documento', campos: [
      { ruta: 'num_serie', etiqueta: 'Número de factura (serie y número)', tipo: 'texto', max: 60, mono: true },
      { ruta: 'fecha_documento', etiqueta: 'Fecha del documento', tipo: 'fecha' },
      { ruta: 'fecha_operacion', etiqueta: 'Fecha de operación (si es distinta)', tipo: 'fecha' },
      { ruta: 'descripcion_operacion', etiqueta: 'Descripción de la operación', tipo: 'area', max: 500 },
    ],
  },
  {
    id: 'emisor', titulo: 'Emisor', campos: [
      { ruta: 'emisor.nombre', etiqueta: 'Razón social', tipo: 'texto', max: 120 },
      { ruta: 'emisor.nif', etiqueta: 'NIF', tipo: 'nif', max: 20 },
      { ruta: 'emisor.domicilio', etiqueta: 'Domicilio', tipo: 'area', max: 300, filas: 2 },
    ],
  },
  {
    id: 'destinatario', titulo: 'Destinatario', campos: [
      { ruta: 'destinatario.nombre', etiqueta: 'Razón social', tipo: 'texto', max: 120 },
      { ruta: 'destinatario.nif', etiqueta: 'NIF', tipo: 'nif', max: 20 },
      { ruta: 'destinatario.domicilio', etiqueta: 'Domicilio', tipo: 'area', max: 300, filas: 2 },
    ],
  },
  { id: 'lineas', titulo: 'Líneas de detalle', tabla: 'lineas' },
  { id: 'desglose', titulo: 'Desglose de IVA', tabla: 'desglose' },
  {
    id: 'totales', titulo: 'Totales', campos: [
      { ruta: 'base_imponible', etiqueta: 'Base imponible', tipo: 'importe' },
      { ruta: 'cuota_total', etiqueta: 'Cuota de IVA', tipo: 'importe' },
      { ruta: 'importe_total', etiqueta: 'Importe total (bases + cuotas)', tipo: 'importe' },
      { ruta: 'retencion.porcentaje', etiqueta: 'Retención IRPF (%)', tipo: 'decimal' },
      { ruta: 'retencion.importe', etiqueta: 'Retención IRPF (importe)', tipo: 'importe' },
      { ruta: 'total_a_pagar', etiqueta: 'Total a pagar', tipo: 'importe' },
    ],
  },
  { id: 'referencias', titulo: 'Referencias', referencias: true },
  {
    id: 'pago', titulo: 'Pago', campos: [
      { ruta: 'forma_pago', etiqueta: 'Forma de pago', tipo: 'texto', max: 200 },
      { ruta: 'iban', etiqueta: 'IBAN', tipo: 'iban', max: 34 },
      { ruta: 'vencimiento', etiqueta: 'Vencimiento', tipo: 'fecha' },
    ],
  },
  {
    id: 'observaciones', titulo: 'Observaciones', campos: [
      { ruta: 'observaciones', etiqueta: 'Observaciones (aparecen en el PDF)', tipo: 'area', max: 1000 },
    ],
  },
];

const TIPOS_COL_LINEA = { concepto: 'area', cantidad: 'decimal', unidad: 'texto', precio: 'decimal', importe: 'importe' };
const TIPOS_COL_DESGLOSE = { tipo_iva: 'decimal', base: 'importe', cuota: 'importe' };

function seccionDe(ruta) {
  if (/^(num_serie|fecha_documento|fecha_operacion|descripcion_operacion)$/.test(ruta)) return 'documento';
  if (/^emisor(\.|$)/.test(ruta)) return 'emisor';
  if (/^destinatario(\.|$)/.test(ruta)) return 'destinatario';
  if (/^lineas(\.|$)/.test(ruta)) return 'lineas';
  if (/^desglose(\.|$)/.test(ruta)) return 'desglose';
  if (/^(base_imponible|cuota_total|importe_total|total_a_pagar)$/.test(ruta) || /^retencion(\.|$)/.test(ruta)) return 'totales';
  if (/^referencias(\.|$)/.test(ruta)) return 'referencias';
  if (/^(forma_pago|iban|vencimiento)$/.test(ruta)) return 'pago';
  if (ruta === 'observaciones') return 'observaciones';
  return null;
}

function aTexto(v) {
  if (v === null || v === undefined) return '';
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

function normalizarValor(tipo, crudo) {
  const t = String(crudo ?? '');
  switch (tipo) {
    case 'importe': return normalizarNumero(t, { dec2: true });
    case 'decimal': return normalizarNumero(t, { maxDec: 6 });
    case 'nif': return { ok: true, valor: t.toUpperCase().replace(/[\s.-]/g, '') };
    case 'iban': return { ok: true, valor: t.toUpperCase().replace(/\s/g, '') };
    case 'fecha':
      if (t === '' || /^\d{4}-\d{2}-\d{2}$/.test(t)) return { ok: true, valor: t };
      return { ok: false, error: 'Fecha no válida.' };
    default: return { ok: true, valor: t.trim() };
  }
}

/** Los valores del historial se guardan como JSON («"B123"», «null», listas…). */
function valorHistorial(v) {
  if (typeof v !== 'string') return v;
  try { return JSON.parse(v); } catch { return v; }
}

function textoCorto(v, max = 80) {
  const s = v === null || v === undefined || v === '' ? '(vacío)' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

async function paginaBorrador(ctx) {
  const bid = ctx.params[0];
  // Las incidencias dependen del entorno (series, certificados, requisitos): se valida con el entorno activo.
  const leerBorrador = () => api(`/borradores/${enc(bid)}?entorno=${enc(app.entorno)}`);
  let b = await leerBorrador();
  let docInfo = null;
  if (b.documento_id) {
    try { docInfo = await api(`/documentos/${enc(b.documento_id)}`); } catch (err) { if (err.estado === 401) throw err; }
  }
  if (!ctx.vigente()) return;

  const pendientes = new Map();     // ruta → {crudo, valor, error, etiqueta, anterior, seccion}
  const motivos = new Map();        // sección → motivo escrito
  const refsExtra = new Set();
  const barras = new Map();
  let versionVista = Date.now();
  let visorVista = null;
  let mostrandoVista = false;
  const procExt = normalizarExtraccion(b.procedencia);
  const proc = camposExtraidos(procExt || {});
  const docExt = docInfo ? normalizarExtraccion(docInfo.extraccion) : null;
  const docInsp = docInfo ? (parseJSON(docInfo.inspeccion) || parseJSON((docInfo.documento || {}).inspeccion) || {}) : {};
  const doc = docInfo ? (docInfo.documento || {}) : null;

  const datos = () => b.datos || {};
  const editable = () => !['emitido', 'descartado'].includes(b.estado) && !b.factura_id && puedeEmitirRol();
  const incidencias = () => (Array.isArray(b.incidencias) ? b.incidencias : []);
  const incidenciasDe = (ruta) => incidencias().filter((i) => i.campo === ruta
    || (i.campo && /\.\d+$/.test(i.campo) && ruta.startsWith(`${i.campo}.`))
    || (i.campo === 'retencion' && ruta.startsWith('retencion.')));
  const nivelVisible = (lista) => {
    if (lista.some((i) => i.nivel === 'bloqueo')) return 'bloqueo';
    if (lista.some((i) => i.nivel === 'confirmar' && !i.confirmado)) return 'confirmar';
    if (lista.some((i) => i.nivel === 'aviso')) return 'aviso';
    return null;
  };
  const ultimoCambio = () => {
    const m = new Map();
    for (const c of Array.isArray(b.cambios) ? b.cambios : []) m.set(c.campo, c);
    return m;
  };

  titulo(`Borrador ${datos().num_serie || `#${b.id}`}`);

  // --- Columna izquierda: original con recuadros / vista previa
  const visorOriginal = b.documento_id ? crearVisor({
    numPaginas: Number(doc && doc.paginas) || Number(docInsp.paginas) || ((procExt || docExt || {}).paginas || []).length || 1,
    url: (n) => `/api/documentos/${enc(b.documento_id)}/paginas/${n}.png`,
    tamanos: tamanosDe(procExt && procExt.paginas ? procExt : docExt, docInsp),
    alt: 'Documento original',
    alClicCaja: (c) => irAlCampo(c),
  }) : null;
  const contVista = h('div', { class: 'vista-previa' });
  function cargarVista() {
    vaciar(contVista);
    visorVista = crearVisor({ sondear: true, url: (n) => `/api/borradores/${enc(bid)}/vista-previa/${n}.png?v=${versionVista}`, alt: 'Vista previa del PDF' });
    contVista.append(
      alerta('info', 'Vista previa con la marca «BORRADOR – SIN VALOR FISCAL» y sin QR. Refleja los datos guardados. ',
        enlace('Abrir la vista previa en PDF', `/api/borradores/${enc(bid)}/vista-previa.pdf?v=${versionVista}`, { target: '_blank', rel: 'noopener noreferrer' })),
      visorVista.el);
  }
  const defsPestanas = [];
  if (visorOriginal) {
    defsPestanas.push({ id: `orig-${bid}`, titulo: 'Documento original', panel: [h('div', { class: 'leyenda-cont' }), visorOriginal.el], alMostrar: () => { mostrandoVista = false; } });
  }
  defsPestanas.push({ id: `vista-${bid}`, titulo: 'Vista previa', panel: contVista, alMostrar: () => { mostrandoVista = true; if (!visorVista) cargarVista(); } });
  const tabs = pestanas(defsPestanas);
  if (!visorOriginal) { mostrandoVista = true; cargarVista(); }

  function actualizarCajas() {
    if (!visorOriginal) return;
    const cajas = [];
    let noEncontrados = 0;
    for (const [ruta, info] of proc) {
      if (tonoConfianza(info) === 'no') noEncontrados += 1;
      if (!info.bbox) continue;
      cajas.push({ campo: ruta, pagina: info.pagina, bbox: info.bbox, tono: tonoConfianza(info), incidencia: nivelVisible(incidenciasDe(ruta)), titulo: `${etiquetaCampo(ruta)}: ${textoValor(info.valor)}` });
    }
    visorOriginal.setCajas(cajas);
    const cont = tabs.el.querySelector('.leyenda-cont');
    if (cont) vaciar(cont).append(leyendaCajas(noEncontrados));
  }

  // --- Columna derecha
  const panel = h('div', { class: 'col-panel' });
  const contAcciones = h('div');
  enlazarResaltado(panel, () => visorOriginal);

  function irAlCampo(ruta) {
    const todos = [...panel.querySelectorAll('[data-campo]')];
    const objetivo = todos.find((el) => el.dataset.campo === ruta)
      || todos.find((el) => el.dataset.campo.startsWith(`${ruta}.`))
      || [...panel.querySelectorAll('[data-seccion]')].find((el) => el.dataset.seccion === seccionDe(ruta));
    if (!objetivo) return;
    const control = objetivo.matches('input,textarea,select') ? objetivo : objetivo.querySelector('input,textarea,select,h2');
    objetivo.scrollIntoView({ block: 'center', behavior: reducirMovimiento() ? 'auto' : 'smooth' });
    (control || objetivo).focus({ preventScroll: true });
  }

  function mensajesIncidencia(lista) {
    return lista.map((i) => h('p', { class: `msg-inc msg-${i.nivel}${i.confirmado ? ' msg-confirmado' : ''}`, id: uid('inc') },
      h('span', { class: 'msg-nivel' }, `${i.confirmado ? 'Confirmado' : NIVEL_TEXTO[i.nivel] || i.nivel}: `), i.mensaje || '', ' ',
      i.codigo ? h('code', null, i.codigo) : null));
  }

  function chipEditado(c) {
    if (!c) return null;
    return h('span', { class: 'chip chip-editado', title: `Editado el ${fmtFechaHora(c.en)}${c.usuario ? ` por ${textoValor(c.usuario)}` : ''}${c.motivo ? `. Motivo: ${c.motivo}` : ''}` }, 'Editado');
  }

  function registrarCambio(ruta, tipo, etiqueta, control, original, errorEl, contenedor) {
    const n = normalizarValor(tipo, control.value);
    errorEl.textContent = n.ok ? '' : n.error;
    control.setAttribute('aria-invalid', n.ok ? 'false' : 'true');
    const o = normalizarValor(tipo, aTexto(original));
    const sinCambio = n.ok && o.ok ? n.valor === o.valor : control.value === aTexto(original);
    if (sinCambio) pendientes.delete(ruta);
    else pendientes.set(ruta, { crudo: control.value, valor: n.ok ? n.valor : null, error: n.ok ? null : n.error, etiqueta, anterior: original, seccion: seccionDe(ruta) });
    contenedor.classList.toggle('modificado', !sinCambio);
    actualizarBarra(seccionDe(ruta));
    renderAcciones();
  }

  function crearControl(tipo, { max, filas, mono } = {}) {
    if (tipo === 'area') return h('textarea', { rows: filas || 3, maxlength: max || null });
    return h('input', {
      type: tipo === 'fecha' ? 'date' : 'text',
      maxlength: max || null,
      inputmode: tipo === 'importe' || tipo === 'decimal' ? 'decimal' : null,
      autocomplete: 'off',
      spellcheck: ['nif', 'iban', 'importe', 'decimal'].includes(tipo) || mono ? 'false' : null,
      class: ['nif', 'iban'].includes(tipo) || mono ? 'mono' : null,
    });
  }

  function filaCampo(def, cambios) {
    const original = obtener(datos(), def.ruta);
    const p = pendientes.get(def.ruta);
    const control = crearControl(def.tipo, def);
    control.id = uid('bf');
    control.value = p ? p.crudo : aTexto(original);
    control.disabled = !editable();
    const incs = incidenciasDe(def.ruta);
    const nivel = nivelVisible(incs);
    const info = proc.get(def.ruta);
    const msgs = mensajesIncidencia(incs);
    const error = h('p', { class: 'error-campo', id: `${control.id}-err`, 'aria-live': 'polite' });
    const avisos = info && Array.isArray(info.avisos) && info.avisos.length && tonoConfianza(info) !== 'ok'
      ? h('p', { class: 'ayuda', id: `${control.id}-av` }, info.avisos.join(' · ')) : null;
    control.setAttribute('aria-describedby', [...msgs.map((m) => m.id), avisos && avisos.id, error.id].filter(Boolean).join(' '));
    if (nivel === 'bloqueo') control.setAttribute('aria-invalid', 'true');
    const cont = h('div', { class: ['campo-b', nivel && `inc-${nivel}`, p && 'modificado'], dataset: { campo: def.ruta } },
      h('div', { class: 'campo-b-cab' }, h('label', { for: control.id }, def.etiqueta),
        h('span', { class: 'chips' }, chipProcedencia(info), chipEditado(cambios.get(def.ruta)))),
      control, avisos, msgs, error);
    control.addEventListener('input', () => registrarCambio(def.ruta, def.tipo, def.etiqueta, control, original, error, cont));
    if (p && p.error) { error.textContent = p.error; control.setAttribute('aria-invalid', 'true'); }
    return cont;
  }

  function celdaEditable(ruta, tipo, etiqueta, cambios) {
    const original = obtener(datos(), ruta);
    const p = pendientes.get(ruta);
    const control = crearControl(tipo, { filas: 2 });
    control.id = uid('bc');
    control.value = p ? p.crudo : aTexto(original);
    control.disabled = !editable();
    control.setAttribute('aria-label', etiqueta);
    const info = proc.get(ruta);
    const incs = incidenciasDe(ruta);
    const nivel = nivelVisible(incs);
    const titulo = [info ? `Procedencia: ${[info.pagina ? `p. ${info.pagina}` : null, METODOS[info.metodo] || info.metodo, typeof info.confianza === 'number' ? fmtPct(info.confianza) : null].filter(Boolean).join(' · ')}` : null,
      textoProcedencia(info), cambios.get(ruta) ? `Editado${cambios.get(ruta).motivo ? `: ${cambios.get(ruta).motivo}` : ''}` : null,
      ...incs.map((i) => `${NIVEL_TEXTO[i.nivel]}: ${i.mensaje}`)].filter(Boolean).join('\n');
    if (titulo) control.title = titulo;
    if (nivel === 'bloqueo') control.setAttribute('aria-invalid', 'true');
    const error = h('span', { class: 'error-campo', 'aria-live': 'polite' });
    const td = h('td', { class: ['celda-b', `conf-${info ? tonoConfianza(info) : 'manual'}`, nivel && `inc-${nivel}`, p && 'modificado'], dataset: { campo: ruta } }, control, error);
    control.addEventListener('input', () => registrarCambio(ruta, tipo, etiqueta, control, original, error, td));
    if (p && p.error) error.textContent = p.error;
    return td;
  }

  function barraPendientes(secId) {
    const lista = h('ul', { class: 'pend-lista' });
    const titulo = h('p', { class: 'pend-titulo' });
    const motivoId = uid('mot');
    const motivo = h('input', { type: 'text', id: motivoId, maxlength: 300, placeholder: 'p. ej. «Error de lectura OCR: el NIF correcto es…»' });
    motivo.value = motivos.get(secId) || '';
    motivo.addEventListener('input', () => motivos.set(secId, motivo.value));
    const err = h('p', { class: 'error-campo', role: 'alert' });
    const guardar = boton('Guardar cambios', () => guardarSeccion(secId), { tipo: 'primario', pequeno: true });
    motivo.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); guardar.click(); } });
    const cont = h('div', { class: 'barra-pendientes', hidden: true },
      titulo, lista,
      h('div', { class: 'pend-motivo' }, h('label', { for: motivoId }, 'Motivo del cambio (obligatorio)'), motivo),
      err,
      h('div', { class: 'acciones' }, guardar, boton('Descartar cambios', () => descartarSeccion(secId), { pequeno: true })));
    barras.set(secId, { cont, lista, titulo, motivo, err, guardar });
    return cont;
  }

  function actualizarBarra(secId) {
    const bar = barras.get(secId);
    if (!bar) return;
    const items = [...pendientes].filter(([, p]) => p.seccion === secId);
    bar.cont.hidden = items.length === 0;
    bar.titulo.textContent = items.length === 1 ? '1 cambio sin guardar' : `${items.length} cambios sin guardar`;
    vaciar(bar.lista).append(...items.map(([, p]) => h('li', null,
      h('span', { class: 'pend-campo' }, `${p.etiqueta}: `), h('span', { class: 'pend-ant' }, textoCorto(p.anterior)), ' → ',
      h('span', { class: p.error ? 'pend-nuevo pend-error' : 'pend-nuevo' }, p.error ? `${textoCorto(p.crudo)} (no válido)` : textoCorto(p.valor)))));
  }

  function descartarSeccion(secId) {
    for (const [r, p] of [...pendientes]) if (p.seccion === secId) pendientes.delete(r);
    motivos.delete(secId);
    renderPanel();
  }

  async function aplicarBorrador(r) {
    // PATCH y confirmaciones devuelven el borrador validado para el simulador: si el entorno activo es otro, se vuelve a leer.
    const valido = r && r.datos && (!r.entorno_validado || r.entorno_validado === app.entorno);
    b = valido ? r : await leerBorrador();
    versionVista = Date.now();
    renderPanel();
    if (visorVista) { if (mostrandoVista) cargarVista(); else { visorVista = null; vaciar(contVista); } }
  }

  function enfocarSeccion(secId) {
    const s = [...panel.querySelectorAll('[data-seccion]')].find((x) => x.dataset.seccion === secId);
    const h2 = s && s.querySelector('h2');
    if (h2) h2.focus({ preventScroll: true });
  }

  async function guardarSeccion(secId) {
    const bar = barras.get(secId);
    const items = [...pendientes].filter(([, p]) => p.seccion === secId);
    if (!items.length || !bar) return;
    bar.err.textContent = '';
    const malos = items.filter(([, p]) => p.error);
    if (malos.length) { bar.err.textContent = `Corrija los valores no válidos: ${malos.map(([, p]) => p.etiqueta).join(', ')}.`; return; }
    const motivo = bar.motivo.value.trim();
    if (!motivo) { bar.err.textContent = 'Indique el motivo del cambio: queda registrado en el historial.'; bar.motivo.focus(); return; }
    await conEspera(bar.guardar, async () => {
      try {
        const r = await api(`/borradores/${enc(bid)}`, { metodo: 'PATCH', json: { cambios: items.map(([campo, p]) => ({ campo, valor: p.valor, motivo })) } });
        items.forEach(([campo]) => pendientes.delete(campo));
        motivos.delete(secId);
        await aplicarBorrador(r);
        notificar('Cambios guardados.', 'ok');
        enfocarSeccion(secId);
      } catch (err) {
        if (err.estado !== 401) bar.err.textContent = err.message;
      }
    }, 'Guardando…');
  }

  async function cambioEstructural(secId, ruta, nuevaLista, textoTitulo) {
    if ([...pendientes.values()].some((p) => p.seccion === secId)) {
      notificar('Guarde o descarte primero los cambios pendientes de esta sección.', 'warn');
      return;
    }
    const motivo = await pedirTexto({ titulo: textoTitulo, etiqueta: 'Motivo del cambio (obligatorio)', textoBoton: 'Guardar', multilinea: false });
    if (!motivo) return;
    try {
      const r = await api(`/borradores/${enc(bid)}`, { metodo: 'PATCH', json: { cambios: [{ campo: ruta, valor: nuevaLista, motivo }] } });
      await aplicarBorrador(r);
      notificar('Cambio guardado.', 'ok');
      enfocarSeccion(secId);
    } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
  }

  function copiaLista(ruta) {
    const l = datos()[ruta];
    return Array.isArray(l) ? JSON.parse(JSON.stringify(l)) : [];
  }

  function seccionTabla(sec, cambios) {
    const esLineas = sec.tabla === 'lineas';
    const filas = Array.isArray(datos()[sec.tabla]) ? datos()[sec.tabla] : [];
    const cols = esLineas ? Object.keys(COLUMNAS_LINEA) : Object.keys(COLUMNAS_DESGLOSE);
    const nombres = esLineas ? COLUMNAS_LINEA : COLUMNAS_DESGLOSE;
    const tipos = esLineas ? TIPOS_COL_LINEA : TIPOS_COL_DESGLOSE;
    const nombreFila = esLineas ? 'Línea' : 'Tipo';
    const cabecera = h('tr', null, h('th', { scope: 'col', class: 'col-num' }, '#'),
      cols.map((c) => h('th', { scope: 'col', class: tipos[c] === 'area' ? 'col-ancha' : (tipos[c] === 'texto' ? null : 'num') }, nombres[c])),
      esLineas ? null : h('th', { scope: 'col' }, 'Calificación'),
      editable() ? h('th', { scope: 'col' }, h('span', { class: 'sr-only' }, 'Acciones')) : null);
    const cuerpo = filas.map((f, i) => h('tr', null,
      h('td', { class: 'col-num' }, String(i + 1), (() => {
        const info = proc.get(`${sec.tabla}.${i}.${esLineas ? 'importe' : 'cuota'}`) || proc.get(`${sec.tabla}.${i}.${esLineas ? 'concepto' : 'tipo_iva'}`);
        return info && info.pagina ? h('span', { class: 'muted bloque' }, `p. ${info.pagina}`) : null;
      })()),
      cols.map((c) => (esLineas ? null : celdaSinIvaDesglose(f, c)) || celdaEditable(`${sec.tabla}.${i}.${c}`, tipos[c], `${nombreFila} ${i + 1}: ${nombres[c]}`, cambios)),
      esLineas ? null : celdaCalificacionDesglose(f, i, {
        editable: editable(), cambios, celda: celdaEditable, lista: () => copiaLista(sec.tabla),
        cambiar: (lista, t) => cambioEstructural(sec.id, sec.tabla, lista, t),
      }),
      editable() ? h('td', null, boton('Quitar', () => {
        const lista = copiaLista(sec.tabla);
        lista.splice(i, 1);
        cambioEstructural(sec.id, sec.tabla, lista, `Quitar ${nombreFila.toLowerCase()} ${i + 1}`);
      }, { pequeno: true, tipo: 'enlace', 'aria-label': `Quitar ${nombreFila.toLowerCase()} ${i + 1}` })) : null));
    const anadirBtn = editable() ? boton(esLineas ? 'Añadir línea' : 'Añadir tipo de IVA', () => {
      const lista = copiaLista(sec.tabla);
      lista.push(esLineas ? { concepto: null, cantidad: null, unidad: null, precio: null, importe: null }
        : { tipo_iva: null, base: null, cuota: null, calificacion: 'S1', clave_regimen: '01', impuesto: '01' });
      cambioEstructural(sec.id, sec.tabla, lista, esLineas ? 'Añadir línea' : 'Añadir tipo de IVA');
    }, { pequeno: true }) : null;
    return [
      filas.length
        ? h('div', { class: 'tabla-wrap' }, h('table', { class: 'tabla tabla-editable' }, h('caption', { class: 'sr-only' }, sec.titulo), h('thead', null, cabecera), h('tbody', null, cuerpo)))
        : h('p', { class: 'vacio' }, esLineas ? 'No se han identificado líneas de detalle.' : 'No se ha identificado el desglose por tipo de IVA.'),
      esLineas ? null : h('p', { class: 'ayuda' }, AYUDA_DESGLOSE_EXENTAS),
      anadirBtn ? h('div', { class: 'acciones' }, anadirBtn) : null,
    ];
  }

  function clavesReferencias() {
    const refs = datos().referencias && typeof datos().referencias === 'object' ? datos().referencias : {};
    return [...new Set([...REFERENCIAS.map(([k]) => k), ...Object.keys(refs), ...refsExtra])];
  }

  function seccionReferencias(cambios) {
    const claves = clavesReferencias();
    const campos = claves.map((k) => filaCampo({ ruta: `referencias.${k}`, etiqueta: (REFERENCIAS.find(([x]) => x === k) || [null, humanizar(k)])[1], tipo: 'texto', max: 200 }, cambios));
    let alta = null;
    if (editable()) {
      const clave = entrada({ maxlength: 30, placeholder: 'p. ej. albaran', pattern: '[a-z_]{1,30}', class: 'mono' });
      const err = h('p', { class: 'error-campo', 'aria-live': 'polite' });
      const anadirRef = () => {
        const k = clave.value.trim().toLowerCase();
        if (!/^[a-z_]{1,30}$/.test(k)) { err.textContent = 'Use solo letras minúsculas sin tildes y guiones bajos (máx. 30).'; return; }
        refsExtra.add(k);
        renderPanel();
        irAlCampo(`referencias.${k}`);
      };
      clave.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); anadirRef(); } });
      alta = h('div', { class: 'alta-referencia' }, campo('Añadir otra referencia (nombre)', clave), boton('Añadir', anadirRef, { pequeno: true }), err);
    }
    return [h('p', { class: 'ayuda' }, 'Las referencias aparecen en el PDF; no se comunican a la AEAT.'), h('div', { class: 'rejilla-campos' }, campos), alta];
  }

  function seccionBorrador(sec, cambios) {
    let cuerpo;
    let rutasCampos = [];
    if (sec.tabla) cuerpo = seccionTabla(sec, cambios);
    else if (sec.referencias) {
      rutasCampos = clavesReferencias().map((k) => `referencias.${k}`);
      cuerpo = seccionReferencias(cambios);
    }
    else {
      rutasCampos = sec.campos.map((c) => c.ruta);
      cuerpo = h('div', { class: sec.campos.length > 2 ? 'rejilla-campos' : null }, sec.campos.map((c) => filaCampo(c, cambios)));
    }
    if (sec.id === 'documento') {
      const sd = b.serie_detectada;
      const d = datos();
      cuerpo = [cuerpo,
        h('div', { class: 'info-serie' },
          h('p', null, h('strong', null, 'Tipo de factura: '), d.tipo_factura === 'F1' || !d.tipo_factura ? 'F1 — factura completa' : `${d.tipo_factura} — factura rectificativa`),
          sd && typeof sd === 'object'
            ? h('p', null, h('strong', null, 'Serie detectada: '),
              [sd.codigo ? `código ${sd.codigo}` : null, sd.patron ? `patrón ${sd.patron}` : null,
                (sd.numero_detectado ?? sd.numero) !== undefined && (sd.numero_detectado ?? sd.numero) !== null ? `número ${sd.numero_detectado ?? sd.numero}` : null,
                sd.siguiente_numero ? `siguiente esperado ${sd.siguiente_numero}` : null, sd.entorno ? `entorno ${(ENTORNOS[sd.entorno] || {}).corto || sd.entorno}` : null]
                .filter(Boolean).join(' · '))
            : avisoSinSerie(b.serie_propuesta),
          h('p', { class: 'ayuda' }, `La factura se expedirá con fecha de hoy (${fmtFecha(hoyISO())}). Si la operación se realizó en otra fecha, indíquela como fecha de operación.`))];
    }
    // Incidencias de la sección que no corresponden a un campo concreto mostrado
    const incSec = incidencias().filter((i) => seccionDe(i.campo || '') === sec.id
      && (sec.tabla || !rutasCampos.some((r) => incidenciasDe(r).includes(i))));
    const nivel = nivelVisible(incSec);
    const idTitulo = uid('sec');
    return h('section', { class: ['card', 'seccion-b', nivel && `sec-inc-${nivel}`], dataset: { seccion: sec.id }, 'aria-labelledby': idTitulo },
      h('h2', { class: 'card-titulo', id: idTitulo, tabindex: '-1' }, sec.titulo),
      incSec.length ? h('div', { class: 'inc-seccion' }, mensajesIncidencia(incSec)) : null,
      cuerpo,
      barraPendientes(sec.id));
  }

  function cardIncidencias() {
    const incs = incidencias();
    const bloq = incs.filter((i) => i.nivel === 'bloqueo');
    const conf = incs.filter((i) => i.nivel === 'confirmar');
    const avis = incs.filter((i) => i.nivel === 'aviso');
    const irBtn = (i) => (i.campo && seccionDe(i.campo) ? boton('Ir al campo', () => irAlCampo(i.campo), { tipo: 'enlace', pequeno: true }) : null);
    const item = (i) => h('li', { class: `inc-item inc-item-${i.nivel}` },
      h('div', { class: 'inc-texto' }, h('span', null, i.mensaje || ''),
        h('span', { class: 'inc-meta' }, i.codigo ? h('code', null, i.codigo) : null, i.campo ? ` · ${etiquetaCampo(i.campo)}` : null)),
      irBtn(i));
    const partes = [];
    if (!incs.length) partes.push(alerta('ok', 'Sin incidencias: el borrador supera todas las validaciones.'));
    if (bloq.length) {
      partes.push(h('div', { class: 'grupo-inc grupo-bloqueo' },
        h('h3', null, pill(`Bloqueos (${bloq.length})`, 'danger'), ' Impiden emitir hasta que se corrijan'),
        h('ul', { class: 'lista-inc' }, bloq.map(item))));
    }
    if (conf.length) {
      const sinConfirmar = conf.filter((i) => !i.confirmado);
      const registrar = boton('Registrar confirmaciones', async () => {
        const claves = [...formConf.querySelectorAll('input[type="checkbox"]:checked:not(:disabled)')].map((x) => x.value);
        if (!claves.length) { notificar('Marque los puntos que confirma.', 'warn'); return; }
        await conEspera(registrar, async () => {
          try {
            const r = await api(`/borradores/${enc(bid)}/confirmaciones`, { metodo: 'POST', json: { claves } });
            await aplicarBorrador(r);
            notificar('Confirmaciones registradas.', 'ok');
          } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
        }, 'Registrando…');
      }, { tipo: 'primario', pequeno: true, disabled: true });
      const formConf = h('div', { class: 'lista-confirmar' }, conf.map((i) => {
        const id = uid('cf');
        const chk = h('input', { type: 'checkbox', id, value: i.clave || `${i.codigo}:${i.campo}`, checked: !!i.confirmado, disabled: !!i.confirmado || !editable() });
        chk.addEventListener('change', () => { registrar.disabled = !formConf.querySelector('input[type="checkbox"]:checked:not(:disabled)'); });
        return h('div', { class: `inc-item inc-item-confirmar${i.confirmado ? ' confirmado' : ''}` },
          h('div', { class: 'casilla' }, chk, h('label', { for: id }, i.mensaje || '',
            h('span', { class: 'inc-meta' }, i.codigo ? h('code', null, i.codigo) : null, i.campo ? ` · ${etiquetaCampo(i.campo)}` : null,
              i.confirmado ? ' · Confirmado' : null))),
          irBtn(i));
      }));
      partes.push(h('div', { class: 'grupo-inc grupo-confirmar' },
        h('h3', null, pill(`Requieren confirmación (${sinConfirmar.length} de ${conf.length} pendientes)`, 'warn')),
        h('p', { class: 'ayuda' }, 'Revise cada punto en el documento. Si es correcto, márquelo y registre la confirmación; si no, corrija el dato.'),
        formConf, editable() && sinConfirmar.length ? h('div', { class: 'acciones' }, registrar) : null));
    }
    if (avis.length) {
      partes.push(h('div', { class: 'grupo-inc grupo-aviso' },
        h('h3', null, pill(`Avisos (${avis.length})`, 'info'), ' No impiden emitir'),
        h('ul', { class: 'lista-inc' }, avis.map(item))));
    }
    return tarjeta('Revisión', partes);
  }

  function motivosNoEmision() {
    const incs = incidencias();
    const nb = incs.filter((i) => i.nivel === 'bloqueo').length;
    const nc = incs.filter((i) => i.nivel === 'confirmar' && !i.confirmado).length;
    const disponibles = Array.isArray(b.entornos_disponibles) ? b.entornos_disponibles : null;
    const out = [];
    if (b.factura_id || b.estado === 'emitido') out.push('El borrador ya se ha emitido.');
    if (b.estado === 'descartado') out.push('El borrador está descartado.');
    if (!puedeEmitirRol()) out.push('Su rol en la empresa no permite emitir facturas.');
    if (nb) out.push(`Hay ${nb} bloqueo(s) que deben corregirse.`);
    if (nc) out.push(`Faltan ${nc} confirmación(es).`);
    if (pendientes.size) out.push('Hay cambios sin guardar.');
    if (b.entorno_validado && b.entorno_validado !== app.entorno) out.push('La validación mostrada corresponde a otro entorno; espere a que se actualice.');
    if (disponibles && !disponibles.includes(app.entorno)) {
      out.push(`El entorno seleccionado (${ENTORNOS[app.entorno].corto}) no está disponible para este borrador. Disponibles: ${disponibles.map((e) => (ENTORNOS[e] || {}).corto || e).join(', ') || 'ninguno'}.`);
    }
    if (!out.length && !b.puede_emitir) out.push('El servidor indica que el borrador todavía no se puede emitir.');
    return out;
  }

  function renderAcciones() {
    const razones = motivosNoEmision();
    const vista = boton('Vista previa', () => {
      tabs.mostrar(`vista-${bid}`);
      const col = tabs.el.closest('.col-visor');
      if (col && !esEscritorio()) col.scrollIntoView({ block: 'start' });
    });
    const emitir = boton('Emitir factura…', abrirEmitir, { tipo: app.entorno === 'produccion' ? 'peligro' : 'primario', disabled: razones.length > 0 });
    if (razones.length) emitir.setAttribute('aria-describedby', 'razones-no-emision');
    const descartar = editable() ? boton('Descartar borrador', descartarBorrador, { tipo: 'enlace' }) : null;
    vaciar(contAcciones).append(
      h('div', { class: 'acciones' }, vista, emitir, descartar),
      h('p', { class: 'muted' }, 'Se emitirá en: ', badgeEntorno(app.entorno), ' (cámbielo en la barra superior).',
        b.entorno_validado && b.entorno_validado !== app.entorno ? h('span', { class: 'bloque' }, 'Actualizando la validación para este entorno…') : null),
      razones.length ? h('ul', { class: 'razones', id: 'razones-no-emision' }, razones.map((r) => h('li', null, r))) : alerta('ok', 'Listo para emitir.'));
  }

  function cardResumen() {
    const d = datos();
    const factura = b.factura_id ? alerta('info', 'Este borrador ya se ha emitido. ', enlace('Ver la factura', `#/factura/${enc(b.factura_id)}`)) : null;
    renderAcciones();
    return tarjeta('Resumen',
      factura,
      kv([
        ['Número', d.num_serie || '—'],
        ['Destinatario', d.destinatario ? `${d.destinatario.nombre || '—'}${d.destinatario.nif ? ` · ${d.destinatario.nif}` : ''}` : '—'],
        ['Importe total', fmtImporte(d.importe_total)],
        d.retencion && d.retencion.importe ? ['Total a pagar', fmtImporte(d.total_a_pagar)] : ['Total a pagar', undefined],
      ]),
      contAcciones);
  }

  function cardRectificativa() {
    const rect = datos().rectificativa;
    if (!rect || typeof rect !== 'object') return null;
    const orig = rect.importes_originales || {};
    return tarjeta('Factura rectificativa',
      kv([
        ['Tipo', `${datos().tipo_factura || '—'} · ${rect.tipo_rectificativa === 'I' ? 'por diferencias' : rect.tipo_rectificativa || '—'}`],
        ['Factura rectificada', `${rect.num_serie_rectificada || '—'} (${fmtFecha(rect.fecha_rectificada)})`],
        ['Motivo', rect.motivo || '—'],
        ['Importes originales', `Base ${fmtImporte(orig.base)} · Cuota ${fmtImporte(orig.cuota)} · Total ${fmtImporte(orig.total)}`],
      ]),
      h('p', { class: 'ayuda' }, 'Indique en el desglose las diferencias (positivas o negativas) respecto a la factura rectificada.'));
  }

  function cardHistorial() {
    const cambios = Array.isArray(b.cambios) ? b.cambios : [];
    return tarjeta('Historial de cambios', tabla([
      { titulo: 'Fecha', celda: (c) => fmtFechaHora(c.en) },
      { titulo: 'Usuario', celda: (c) => (c.usuario && typeof c.usuario === 'object' ? c.usuario.nombre || c.usuario.email : textoValor(c.usuario)) },
      { titulo: 'Campo', celda: (c) => etiquetaCampo(c.campo || '') },
      { titulo: 'Antes', celda: (c) => h('span', { class: 'valor-largo' }, textoCorto(valorHistorial(c.valor_anterior), 160)) },
      { titulo: 'Después', celda: (c) => h('span', { class: 'valor-largo' }, textoCorto(valorHistorial(c.valor_nuevo), 160)) },
      { titulo: 'Motivo', celda: (c) => c.motivo || '—' },
    ], [...cambios].reverse(), { vacio: 'Sin cambios: los datos son los leídos del documento.', leyenda: 'Historial de cambios' }));
  }

  function renderPanel() {
    barras.clear();
    const cambios = ultimoCambio();
    anadir(vaciar(panel), [
      cardResumen(),
      cardIncidencias(),
      cardRectificativa(),
      SECCIONES_BORRADOR.map((s) => seccionBorrador(s, cambios)),
      cardHistorial()]);
    for (const s of SECCIONES_BORRADOR) actualizarBarra(s.id);
    actualizarCajas();
    titulo(`Borrador ${datos().num_serie || `#${b.id}`}`);
  }

  async function descartarBorrador() {
    const motivo = await pedirTexto({
      titulo: 'Descartar el borrador',
      explicacion: 'El borrador quedará descartado y no se podrá emitir. El documento original se conserva.',
      etiqueta: 'Motivo (obligatorio)', textoBoton: 'Descartar borrador', peligro: true,
    });
    if (!motivo) return;
    try {
      await api(`/borradores/${enc(bid)}/descartar`, { metodo: 'POST', json: { motivo } });
      pendientes.clear();
      notificar('Borrador descartado.', 'ok');
      await aplicarBorrador(null);
    } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
  }

  function abrirEmitir() {
    if (pendientes.size) { notificar('Guarde o descarte los cambios pendientes antes de emitir.', 'warn'); return; }
    if (motivosNoEmision().length) { renderAcciones(); return; }
    const entorno = app.entorno;
    const ent = ENTORNOS[entorno];
    const emp = empresaActual() || {};
    const d = datos();
    const num = String(d.num_serie || '').trim();
    const clave = nuevoUUID();   // una sola clave por diálogo: reintentar no duplica la factura
    let enCurso = false;
    const chk = h('input', { type: 'checkbox', id: uid('chk') });
    const confirmaNum = entrada({ autocomplete: 'off', spellcheck: 'false', class: 'mono' });
    const error = h('div', { 'aria-live': 'assertive' });
    const emitirBtn = boton(entorno === 'produccion' ? 'Emitir factura REAL' : 'Emitir factura', enviar, { tipo: entorno === 'produccion' ? 'peligro' : 'primario', disabled: true });
    const cancelar = boton('Cancelar', () => { if (!enCurso) dlg.close(); });
    const actualizar = () => { emitirBtn.disabled = enCurso || !chk.checked || confirmaNum.value.trim() !== num; };
    chk.addEventListener('change', actualizar);
    confirmaNum.addEventListener('input', actualizar);
    confirmaNum.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); if (!emitirBtn.disabled) enviar(); } });

    async function enviar() {
      if (enCurso || emitirBtn.disabled) return;
      enCurso = true;
      emitirBtn.disabled = true;
      cancelar.disabled = true;
      emitirBtn.textContent = 'Emitiendo…';
      emitirBtn.setAttribute('aria-busy', 'true');
      vaciar(error);
      try {
        const r = await api(`/borradores/${enc(bid)}/emitir`, { metodo: 'POST', json: { entorno, clave_idempotencia: clave, confirmo: true } });
        pendientes.clear();
        enCurso = false;
        dlg.close();
        notificar(`Factura ${num} emitida en ${ent.corto}.`, 'ok');
        irA(`/factura/${enc(r.factura_id)}`);
      } catch (err) {
        enCurso = false;
        cancelar.disabled = false;
        emitirBtn.textContent = entorno === 'produccion' ? 'Emitir factura REAL' : 'Emitir factura';
        emitirBtn.removeAttribute('aria-busy');
        actualizar();
        if (err.estado === 401) { dlg.close(); return; }
        const extra = detalleExtra(err);
        error.append(alerta('danger', h('strong', null, 'No se ha emitido. '), err.message,
          extra ? h('ul', { class: 'lista-simple' }, extra.map((i) => h('li', null, `${i.codigo ? `${i.codigo}: ` : ''}${i.mensaje || ''}`))) : null,
          err.estado === 0 ? h('p', null, 'Si se perdió la conexión, puede reintentar con seguridad: la misma solicitud nunca genera dos facturas.') : null));
      }
    }

    const ret = d.retencion && d.retencion.importe;
    const dlg = dialogo({
      titulo: 'Confirmar la emisión de la factura',
      clase: `dialogo-emitir dialogo-${ent.tono}`,
      contenido: [
        h('div', { class: 'emitir-entorno' }, badgeEntorno(entorno, true)),
        entorno === 'produccion'
          ? alerta('danger', h('strong', null, 'Facturación REAL. '), 'La factura tendrá plenos efectos fiscales y su registro se remitirá a la AEAT.')
          : entorno === 'pruebas'
            ? alerta('warn', h('strong', null, 'Entorno de pruebas de la AEAT. '), 'Sin efectos fiscales; el registro se envía al servicio de preproducción.')
            : alerta('info', h('strong', null, 'Simulador local. '), 'No se envía nada a la AEAT; la factura no tiene efectos fiscales.'),
        kv([
          ['Entorno', ent.corto],
          ['Empresa emisora', `${emp.razon_social || (d.emisor || {}).nombre || '—'} · NIF ${emp.nif || (d.emisor || {}).nif || '—'}`],
          ['Número de factura', h('strong', { class: 'mono' }, num || '—')],
          ['Fecha de expedición', `${fmtFecha(hoyISO())} (hoy)`],
          ['Cliente', `${(d.destinatario || {}).nombre || '—'} · NIF ${(d.destinatario || {}).nif || '—'}`],
          ['Base imponible', fmtImporte(d.base_imponible)],
          ['Cuota de IVA', fmtImporte(d.cuota_total)],
          ['Importe total', h('strong', null, fmtImporte(d.importe_total))],
          ret ? ['Retención', fmtImporte(d.retencion.importe)] : ['Retención', undefined],
          ret ? ['Total a pagar', fmtImporte(d.total_a_pagar)] : ['Total a pagar', undefined],
        ]),
        h('p', null, 'Al confirmar se expedirá la factura y se generará su ', h('strong', null, 'registro de facturación (alta)'),
          ', con huella encadenada al registro anterior, que se remitirá ', h('strong', null, ent.destino), '. ',
          'Se generará el PDF definitivo con el código QR. La factura expedida no se puede modificar ni borrar: '
          + 'cualquier corrección posterior requerirá una factura rectificativa o, si no debió expedirse, la anulación de su registro.'),
        h('div', { class: 'casilla casilla-destacada' }, chk, h('label', { for: chk.id },
          `He revisado los datos y confirmo la emisión de la factura ${num} en el entorno ${ent.corto}.`)),
        campo(`Para confirmar, escriba el número de la factura: ${num}`, confirmaNum),
        error,
      ],
      acciones: [cancelar, emitirBtn],
    });
    dlg.addEventListener('cancel', (e) => { if (enCurso) e.preventDefault(); });
    chk.focus();
  }

  // --- Montaje
  const meta = [pillDe(ESTADO_BORRADOR, b.estado)];
  if (b.documento_id) meta.push(enlace('Documento de origen', `#/documento/${enc(b.documento_id)}`));
  if (b.factura_id) meta.push(enlace('Factura emitida', `#/factura/${enc(b.factura_id)}`));
  const ok = montar(ctx,
    cabeceraPagina(`Borrador ${datos().num_serie || `#${b.id}`}`, {
      volver: ['#/importar', 'Documentos'], meta,
      acciones: b.documento_id ? [enlace('Descargar original (PDF)', `/api/documentos/${enc(b.documento_id)}/original`, { tipo: 'secundario', descarga: true })] : [],
    }),
    h('div', { class: 'split' }, h('div', { class: 'col-visor' }, tabs.el), panel));
  if (!ok) return;
  renderPanel();

  guardiaSalida = () => pendientes.size === 0 || window.confirm('Hay cambios sin guardar en el borrador. ¿Desea salir y descartarlos?');
  const antesDeSalir = (e) => { if (pendientes.size) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', antesDeSalir);
  ctx.alSalir(() => window.removeEventListener('beforeunload', antesDeSalir));
  app.alCambiarEntorno = async () => {
    renderAcciones();
    try { await aplicarBorrador(null); } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
  };
}

/* ======================================================================================
 * Facturas expedidas (historial)
 * ====================================================================================== */

function pillEnvio(estado, texto) {
  const p = pillDe(ESTADO_ENVIO, estado);
  if (texto && ESTADO_ENVIO[estado] && texto !== ESTADO_ENVIO[estado][0]) return h('span', null, p, h('span', { class: 'muted bloque' }, texto));
  if (!ESTADO_ENVIO[estado] && texto) return pill(texto, 'neutral');
  return p;
}

async function paginaFacturas(ctx) {
  titulo('Facturas');
  const eid = app.empresaId;
  const q = ctx.query;
  const filtros = {
    q: entrada({ type: 'search', value: q.get('q') || '', placeholder: 'p. ej. 166/26' }),
    cliente: entrada({ type: 'search', value: q.get('cliente') || '', placeholder: 'Nombre o NIF' }),
    desde: entrada({ type: 'date', value: q.get('desde') || '' }),
    hasta: entrada({ type: 'date', value: q.get('hasta') || '' }),
    estado: selector([['', 'Todos'], ...['emitida', 'anulada', 'rectificada'].map((k) => [k, ESTADO_FACTURA[k][0]])], q.get('estado') || ''),
    envio: selector([['', 'Todos'], ...Object.entries(ESTADO_ENVIO).map(([k, v]) => [k, v[0]])], q.get('envio') || ''),
    entorno: selector([['todos', 'Todos'], ...Object.keys(ENTORNOS).map((k) => [k, ENTORNOS[k].corto])], q.get('entorno') || app.entorno),
  };
  const seleccion = new Set();
  const listado = h('div', null, cargando());
  const lote = boton('Descargar lote (ZIP)', () => {
    if (!seleccion.size) return;
    descargar(`/api/empresas/${enc(eid)}/facturas-lote.zip?ids=${[...seleccion].map(enc).join(',')}`);
  }, { disabled: true });
  const contador = h('span', { class: 'muted', 'aria-live': 'polite' });
  const actualizarLote = () => {
    lote.disabled = seleccion.size === 0;
    lote.textContent = seleccion.size ? `Descargar lote (ZIP) · ${seleccion.size}` : 'Descargar lote (ZIP)';
    contador.textContent = seleccion.size ? `${seleccion.size} seleccionada(s)` : '';
  };

  async function cargar() {
    const valores = Object.fromEntries(Object.entries(filtros).map(([k, el]) => [k, el.value.trim()]));
    fijarQuery({ ...valores, entorno: valores.entorno });
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(valores)) if (v && !(k === 'entorno' && v === 'todos')) params.set(k, v);
    seleccion.clear();
    actualizarLote();
    vaciar(listado).append(cargando());
    try {
      const facturas = comoLista(await api(`/empresas/${enc(eid)}/facturas?${params}`), 'facturas');
      if (!ctx.vigente()) return;
      const checks = [];
      const todas = h('input', { type: 'checkbox', 'aria-label': 'Seleccionar todas las facturas listadas' });
      todas.addEventListener('change', () => {
        checks.forEach(([chk, id]) => { chk.checked = todas.checked; if (todas.checked) seleccion.add(id); else seleccion.delete(id); });
        actualizarLote();
      });
      const cols = [
        {
          titulo: todas, clase: 'col-chk',
          celda: (f) => {
            const chk = h('input', { type: 'checkbox', 'aria-label': `Seleccionar la factura ${f.num_serie}` });
            chk.addEventListener('change', () => {
              if (chk.checked) seleccion.add(f.id); else seleccion.delete(f.id);
              todas.checked = checks.every(([c]) => c.checked);
              todas.indeterminate = !todas.checked && checks.some(([c]) => c.checked);
              actualizarLote();
            });
            checks.push([chk, f.id]);
            return chk;
          },
        },
        { titulo: 'Número', celda: (f) => h('a', { href: `#/factura/${enc(f.id)}`, class: 'mono' }, f.num_serie || `#${f.id}`) },
        { titulo: 'Fecha', celda: (f) => fmtFecha(f.fecha_expedicion) },
        { titulo: 'Cliente', celda: (f) => [h('span', { class: 'bloque' }, f.destinatario_nombre || '—'), h('span', { class: 'muted mono bloque' }, f.destinatario_nif || '')] },
        { titulo: 'Importe', clase: 'num', celda: (f) => fmtImporte(f.importe_total) },
        { titulo: 'Estado de la factura', celda: (f) => pillDe(ESTADO_FACTURA, f.estado) },
        { titulo: 'Estado de la comunicación', celda: (f) => pillEnvio(f.estado_envio, f.estado_envio_texto) },
        { titulo: 'Entorno', celda: (f) => badgeEntorno(f.entorno) },
        { titulo: 'PDF', celda: (f) => enlace('PDF', `/api/facturas/${enc(f.id)}/pdf`, { descarga: true, 'aria-label': `Descargar el PDF de la factura ${f.num_serie}` }) },
      ];
      vaciar(listado).append(h('p', { class: 'muted' }, `${facturas.length} factura(s)`),
        tabla(cols, facturas, { vacio: 'No hay facturas que coincidan con la búsqueda.', leyenda: 'Facturas expedidas' }));
    } catch (err) {
      if (ctx.vigente() && err.estado !== 401) vaciar(listado).append(cajaError(err, cargar));
    }
  }

  const form = h('form', { class: 'filtros filtros-facturas', role: 'search' },
    campo('Número', filtros.q), campo('Cliente', filtros.cliente), campo('Desde', filtros.desde), campo('Hasta', filtros.hasta),
    campo('Estado de la factura', filtros.estado), campo('Estado de la comunicación', filtros.envio), campo('Entorno', filtros.entorno),
    h('div', { class: 'filtros-acciones' }, h('button', { type: 'submit', class: 'btn btn-primario' }, 'Buscar'),
      boton('Limpiar', () => {
        ['q', 'cliente', 'desde', 'hasta', 'estado', 'envio'].forEach((k) => { filtros[k].value = ''; });
        filtros.entorno.value = app.entorno;
        cargar();
      })));
  form.addEventListener('submit', (e) => { e.preventDefault(); cargar(); });
  [filtros.estado, filtros.envio, filtros.entorno, filtros.desde, filtros.hasta].forEach((el) => el.addEventListener('change', cargar));

  montar(ctx,
    cabeceraPagina('Facturas expedidas', { acciones: [contador, lote] }),
    tarjeta(null, form),
    tarjeta(null, listado));
  cargar();
}

/* ======================================================================================
 * Factura: PDF, registros, envíos y acciones
 * ====================================================================================== */

function tipoAccion(s) {
  const t = String(s || '');
  if (/rectific/i.test(t)) return 'rectificativa';
  if (/anul/i.test(t)) return 'anular';
  if (/subsan/i.test(t)) return 'subsanar';
  if (/reintent|reenv/i.test(t)) return 'reintentar';
  return null;
}

function normalizarAcciones(lista) {
  return comoLista(lista).map((a) => {
    if (typeof a === 'string') return { accion: a, tipo: tipoAccion(a) };
    if (a && typeof a === 'object') {
      const nombre = a.accion || a.tipo || a.nombre || '';
      return { accion: nombre, tipo: tipoAccion(nombre), registro_id: a.registro_id ?? a.registro ?? null };
    }
    return null;
  }).filter((a) => a && a.tipo);
}

async function paginaFactura(ctx) {
  const fid = ctx.params[0];
  const r = await api(`/facturas/${enc(fid)}`);
  if (!ctx.vigente()) return;
  const f = r.factura || {};
  const d = parseJSON(r.datos) || parseJSON(f.datos) || {};
  const registros = comoLista(r.registros).slice().sort((a, b) => (Number(a.secuencia) || 0) - (Number(b.secuencia) || 0));
  const envios = comoLista(r.envios);
  const urlQr = r.url_qr || f.url_qr || '';
  const ultimo = registros[registros.length - 1] || null;
  const num = f.num_serie || d.num_serie || `#${fid}`;
  titulo(`Factura ${num}`);

  const paginasPdf = Number(r.paginas_pdf || f.pdf_paginas || (d.pdf && d.pdf.paginas)) || 0;
  const visor = crearVisor({
    numPaginas: paginasPdf, sondear: !paginasPdf,
    url: (n) => `/api/facturas/${enc(fid)}/pdf/${n}.png`,
    alt: `PDF definitivo de la factura ${num}`,
  });

  const recargar = () => enrutar({ forzar: true });
  const acciones = normalizarAcciones(r.acciones_permitidas);
  const registroPara = (tipo, a) => {
    if (a && a.registro_id) return registros.find((x) => String(x.id) === String(a.registro_id)) || { id: a.registro_id };
    const altas = registros.filter((x) => x.tipo === 'alta');
    if (tipo === 'subsanar') return altas[altas.length - 1] || ultimo;
    return ultimo;
  };
  const vistos = new Set();
  const botones = [];
  for (const a of acciones) {
    const reg = a.tipo === 'subsanar' || a.tipo === 'reintentar' ? registroPara(a.tipo, a) : null;
    const clave = `${a.tipo}:${reg ? reg.id : ''}`;
    if (vistos.has(clave)) continue;
    vistos.add(clave);
    const sufijo = reg && registros.length > 1 ? ` (registro ${reg.secuencia ?? reg.id})` : '';
    if (a.tipo === 'rectificativa') botones.push(boton('Emitir rectificativa…', () => dialogoRectificativa(f, num)));
    if (a.tipo === 'anular') botones.push(boton('Anular registro…', () => dialogoAnular(f, num, recargar), { tipo: 'peligro' }));
    if (a.tipo === 'subsanar' && reg) botones.push(boton(`Subsanar registro…${sufijo}`, () => dialogoSubsanar(reg, num, recargar)));
    if (a.tipo === 'reintentar' && reg) {
      const btn = boton(`Reintentar envío${sufijo}`, async () => {
        const ok = await confirmar({
          titulo: 'Reintentar el envío',
          texto: `Se volverá a poner en cola el registro ${reg.secuencia ?? reg.id} de la factura ${num} para remitirlo ${ENTORNOS[f.entorno] ? ENTORNOS[f.entorno].destino : ''}, respetando el control de flujo.`,
          textoBoton: 'Reintentar',
        });
        if (!ok) return;
        await conEspera(btn, async () => {
          try {
            await api(`/registros/${enc(reg.id)}/reintentar`, { metodo: 'POST', json: {} });
            notificar('Registro puesto en cola para reenvío.', 'ok');
            refrescarAvisos(true);
            recargar();
          } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
        });
      });
      botones.push(btn);
    }
  }

  const xmlUltimo = ultimo ? enlace('XML del registro', `/api/registros/${enc(ultimo.id)}/xml`, { tipo: 'secundario', descarga: true }) : null;
  const cabecera = cabeceraPagina(`Factura ${num}`, {
    volver: ['#/facturas', 'Facturas'],
    meta: [pillDe(ESTADO_FACTURA, f.estado), badgeEntorno(f.entorno), ultimo ? pillEnvio(ultimo.estado_envio) : null],
    acciones: [enlace('Descargar PDF', `/api/facturas/${enc(fid)}/pdf`, { tipo: 'primario', descarga: true }), xmlUltimo],
  });

  const dest = d.destinatario || {};
  const emi = d.emisor || {};
  const rect = d.rectificativa && typeof d.rectificativa === 'object' ? d.rectificativa : null;
  const cardResumen = tarjeta('Datos de la factura', kv([
    ['Número', h('span', { class: 'mono' }, num)],
    ['Tipo', f.tipo_factura || d.tipo_factura || '—'],
    ['Fecha de expedición', fmtFecha(f.fecha_expedicion || d.fecha_expedicion)],
    d.fecha_operacion ? ['Fecha de operación', fmtFecha(d.fecha_operacion)] : ['Fecha de operación', undefined],
    ['Emisor', `${emi.nombre || '—'} · NIF ${emi.nif || f.emisor_nif || '—'}`],
    ['Cliente', `${dest.nombre || f.destinatario_nombre || '—'} · NIF ${dest.nif || f.destinatario_nif || '—'}`],
    ['Base imponible', fmtImporte(d.base_imponible)],
    ['Cuota de IVA', fmtImporte(d.cuota_total || f.cuota_total)],
    ['Importe total', h('strong', null, fmtImporte(f.importe_total || d.importe_total))],
    d.retencion && d.retencion.importe ? ['Total a pagar', fmtImporte(d.total_a_pagar)] : ['Total a pagar', undefined],
    rect ? ['Rectifica a', f.rectifica_factura_id ? enlace(rect.num_serie_rectificada || `Factura ${f.rectifica_factura_id}`, `#/factura/${enc(f.rectifica_factura_id)}`) : (rect.num_serie_rectificada || '—')] : ['Rectifica a', undefined],
    ['Entorno', badgeEntorno(f.entorno)],
    ['Expedida el', fmtFechaHora(f.creada_en)],
    f.borrador_id ? ['Borrador de origen', enlace(`Borrador #${f.borrador_id}`, `#/borrador/${enc(f.borrador_id)}`)] : ['Borrador de origen', undefined],
  ]));

  const copiar = boton('Copiar', async () => {
    try { await navigator.clipboard.writeText(urlQr); notificar('URL copiada.', 'ok'); } catch { notificar('No se pudo copiar; seleccione el texto manualmente.', 'warn'); }
  }, { pequeno: true });
  const cardEstado = tarjeta('Estado',
    h('div', { class: 'estados-doble' },
      h('div', null, h('p', { class: 'etiqueta-estado' }, 'Estado de la factura'), pillDe(ESTADO_FACTURA, f.estado)),
      h('div', null, h('p', { class: 'etiqueta-estado' }, 'Estado de la comunicación'),
        ultimo ? pillEnvio(ultimo.estado_envio) : pill('Sin registros', 'neutral'),
        ultimo && ultimo.codigo_error ? h('p', { class: 'error-oficial' }, h('code', null, ultimo.codigo_error), ' ', ultimo.descripcion_error || '') : null)),
    urlQr ? h('div', { class: 'qr-url' },
      h('p', { class: 'etiqueta-estado' }, 'URL del código QR (servicio de cotejo de la AEAT)'),
      h('code', { class: 'valor-largo' }, urlQr), ' ', copiar,
      /^https:\/\//i.test(urlQr) ? h('p', null, h('a', { href: urlQr, target: '_blank', rel: 'noopener noreferrer' }, 'Abrir el cotejo en la sede de la AEAT')) : null) : null,
    alerta('info', r.nota || NOTA_ACEPTACION));

  const cardAcciones = tarjeta('Acciones',
    botones.length ? h('div', { class: 'acciones' }, botones) : h('p', { class: 'muted' }, 'No hay acciones disponibles para esta factura en su estado actual.'),
    h('p', { class: 'ayuda' }, 'La factura expedida es inmutable. Las correcciones se hacen con una factura rectificativa; la anulación del registro solo procede si la factura no debió expedirse.'));

  const cardRegistros = tarjeta('Registros de facturación', tabla([
    {
      titulo: 'Tipo',
      celda: (x) => [x.tipo === 'anulacion' ? 'Anulación' : 'Alta',
        x.subsanacion === 'S' ? h('span', { class: 'muted bloque' }, 'Subsanación') : null,
        x.rechazo_previo ? h('span', { class: 'muted bloque' }, `Rechazo previo: ${x.rechazo_previo}`) : null],
    },
    { titulo: 'Secuencia', clase: 'num', celda: (x) => x.secuencia },
    { titulo: 'Huella', celda: (x) => h('code', { class: 'huella', title: x.huella || '' }, abreviar(x.huella)) },
    { titulo: 'Fecha, hora y huso', celda: (x) => h('span', { class: 'mono' }, x.fecha_hora_huso || '—') },
    {
      titulo: 'Estado del envío',
      celda: (x) => [pillEnvio(x.estado_envio),
        x.codigo_error || x.descripcion_error ? h('p', { class: 'error-oficial' }, x.codigo_error ? h('code', null, x.codigo_error) : null, ' ', x.descripcion_error || '') : null],
    },
    { titulo: 'CSV', celda: (x) => (x.csv ? h('span', { class: 'mono valor-largo' }, x.csv) : '—') },
    { titulo: 'Intentos', clase: 'num', celda: (x) => x.intentos ?? 0 },
    { titulo: 'XML', celda: (x) => enlace('XML', `/api/registros/${enc(x.id)}/xml`, { descarga: true, 'aria-label': `Descargar el XML del registro ${x.secuencia ?? x.id}` }) },
  ], registros, { vacio: 'No hay registros.', leyenda: 'Registros de facturación' }),
  registros.some((x) => x.huella_anterior) ? h('details', { class: 'detalle-huellas' }, h('summary', null, 'Ver huellas completas y encadenamiento'),
    h('ul', { class: 'lista-simple' }, registros.map((x) => h('li', null, h('strong', null, `Registro ${x.secuencia ?? x.id}: `),
      h('span', { class: 'bloque' }, 'Huella: ', h('code', { class: 'valor-largo' }, x.huella || '—')),
      h('span', { class: 'bloque' }, 'Huella anterior: ', h('code', { class: 'valor-largo' }, x.huella_anterior || '(primer registro de la cadena)')))))) : null);

  const secuenciaDe = (rid) => { const x = registros.find((y) => String(y.id) === String(rid)); return x ? (x.secuencia ?? x.id) : rid; };
  const cardEnvios = tarjeta('Envíos', tabla([
    { titulo: 'Inicio', celda: (e) => fmtFechaHora(e.iniciado_en) },
    { titulo: 'Registro', clase: 'num', celda: (e) => (e.registro_id ? secuenciaDe(e.registro_id) : '—') },
    { titulo: 'Fin', celda: (e) => fmtFechaHora(e.finalizado_en) },
    { titulo: 'Resultado', celda: (e) => (e.resultado ? pillDe(RESULTADO_ENVIO, e.resultado) : '—') },
    {
      titulo: 'Estado del registro',
      celda: (e) => [e.estado_registro ? h('span', { class: 'bloque' }, e.estado_registro) : null,
        e.codigo_error || e.descripcion_error ? h('p', { class: 'error-oficial' }, e.codigo_error ? h('code', null, e.codigo_error) : null, ' ', e.descripcion_error || '') : null],
    },
    { titulo: 'HTTP', clase: 'num', celda: (e) => e.http_estado ?? '—' },
    { titulo: 'CSV', celda: (e) => (e.csv ? h('span', { class: 'mono valor-largo' }, e.csv) : '—') },
    { titulo: 'Destino', celda: (e) => h('span', { class: 'valor-largo muted' }, e.destino || '—') },
    { titulo: 'Error', celda: (e) => (e.error ? h('span', { class: 'valor-largo' }, e.error) : '—') },
  ], envios, { vacio: 'Todavía no se ha realizado ningún envío de esta factura.', leyenda: 'Envíos' }));

  const cardVerif = r.verificacion && (typeof r.verificacion !== 'object' || Object.keys(r.verificacion).length)
    ? tarjeta('Verificación de integridad',
      h('p', { class: 'ayuda' }, 'Comprobaciones del PDF definitivo al expedirlo: refleja los datos aprobados y el QR lleva a la URL registrada.'),
      r.verificacion && typeof r.verificacion === 'object' && !Array.isArray(r.verificacion)
        ? kv(Object.entries(r.verificacion).map(([k, v]) => [ETIQUETAS_VERIFICACION[k] || humanizar(k), renderGenerico(v)]))
        : renderGenerico(r.verificacion)) : null;

  montar(ctx, cabecera,
    h('div', { class: 'split' },
      h('div', { class: 'col-visor' }, h('h2', { class: 'sr-only' }, 'Páginas del PDF definitivo'), visor.el),
      h('div', { class: 'col-panel' }, cardEstado, cardAcciones, cardResumen, cardRegistros, cardEnvios, cardVerif)));
}

function dialogoRectificativa(f, num) {
  const motivo = h('textarea', { rows: 3, maxlength: 400, required: true });
  const error = h('div', { 'aria-live': 'assertive' });
  const form = h('form', { class: 'form' },
    h('p', null, `Se creará un borrador de factura rectificativa por diferencias de la factura ${num}. Deberá revisarlo y emitirlo como cualquier otra factura.`),
    grupoRadios('tipo_rect', 'Tipo de rectificativa', [
      ['R1', 'R1 — Error fundado en derecho y causas del art. 80.Uno, Dos y Seis de la Ley del IVA', 'Por ejemplo: devolución de envases, descuentos posteriores, resolución de la operación, impago en concurso.'],
      ['R4', 'R4 — Resto de causas', 'Por ejemplo: errores en los datos o en los importes de la factura.'],
    ], 'R4'),
    campo('Motivo de la rectificación (obligatorio)', motivo),
    error);
  const crear = boton('Crear borrador de rectificativa', async () => {
    vaciar(error);
    const tipo = valorRadio(form, 'tipo_rect');
    if (!motivo.value.trim()) { error.append(alerta('danger', 'Indique el motivo.')); motivo.focus(); return; }
    await conEspera(crear, async () => {
      try {
        const r = await api(`/facturas/${enc(f.id)}/rectificativa`, { metodo: 'POST', json: { tipo, motivo: motivo.value.trim() } });
        dlg.close();
        notificar('Borrador de rectificativa creado.', 'ok');
        irA(`/borrador/${enc(r.borrador_id)}`);
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Creando…');
  }, { tipo: 'primario' });
  const dlg = dialogo({ titulo: `Rectificar la factura ${num}`, contenido: form, acciones: [boton('Cancelar', () => dlg.close()), crear] });
  motivo.focus();
}

function dialogoAnular(f, num, recargar) {
  const motivo = h('textarea', { rows: 3, maxlength: 400, required: true });
  const chk = h('input', { type: 'checkbox', id: uid('chk') });
  const error = h('div', { 'aria-live': 'assertive' });
  const ent = ENTORNOS[f.entorno] || ENTORNOS.simulador;
  const anular = boton('Anular el registro', async () => {
    vaciar(error);
    if (!motivo.value.trim()) { error.append(alerta('danger', 'Indique el motivo.')); motivo.focus(); return; }
    if (!chk.checked) { error.append(alerta('danger', 'Debe marcar la confirmación.')); chk.focus(); return; }
    await conEspera(anular, async () => {
      try {
        await api(`/facturas/${enc(f.id)}/anular`, { metodo: 'POST', json: { motivo: motivo.value.trim(), confirmo: true } });
        dlg.close();
        notificar('Registro de anulación generado.', 'ok');
        refrescarAvisos(true);
        recargar();
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Anulando…');
  }, { tipo: 'peligro', disabled: true });
  chk.addEventListener('change', () => { anular.disabled = !chk.checked; });
  const dlg = dialogo({
    titulo: `Anular el registro de la factura ${num}`,
    clase: `dialogo-${ent.tono}`,
    contenido: [
      badgeEntorno(f.entorno, true),
      alerta('warn', h('strong', null, 'Solo para facturas que no debieron expedirse. '),
        'La anulación procede, por ejemplo, si la factura se expidió por error o por duplicado. Si la operación existió y hay que corregir datos o importes, '
        + 'emita una factura rectificativa.'),
      h('p', null, `Se generará un registro de anulación encadenado, que se remitirá ${ent.destino}. La factura y su registro de alta se conservan; la factura pasará a estado «Anulada».`),
      campo('Motivo de la anulación (obligatorio)', motivo),
      h('div', { class: 'casilla casilla-destacada' }, chk, h('label', { for: chk.id }, `Confirmo que la factura ${num} no debió expedirse y solicito anular su registro.`)),
      error,
    ],
    acciones: [boton('Cancelar', () => dlg.close()), anular],
  });
  motivo.focus();
}

function dialogoSubsanar(reg, num, recargar) {
  const motivo = h('textarea', { rows: 3, maxlength: 400, required: true });
  const chk = h('input', { type: 'checkbox', id: uid('chk') });
  const error = h('div', { 'aria-live': 'assertive' });
  const enviar = boton('Generar registro de subsanación', async () => {
    vaciar(error);
    if (!motivo.value.trim()) { error.append(alerta('danger', 'Indique el motivo.')); motivo.focus(); return; }
    await conEspera(enviar, async () => {
      try {
        await api(`/registros/${enc(reg.id)}/subsanar`, { metodo: 'POST', json: { motivo: motivo.value.trim(), confirmo: true } });
        dlg.close();
        notificar('Registro de subsanación generado.', 'ok');
        refrescarAvisos(true);
        recargar();
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Generando…');
  }, { tipo: 'primario', disabled: true });
  chk.addEventListener('change', () => { enviar.disabled = !chk.checked; });
  const dlg = dialogo({
    titulo: `Subsanar el registro de la factura ${num}`,
    contenido: [
      h('p', null, 'La subsanación genera un nuevo registro de alta (Subsanación = S) con los mismos datos aprobados de la factura, para corregir un registro rechazado '
        + 'o aceptado con errores. No modifica la factura expedida.'),
      reg.codigo_error ? alerta('warn', h('strong', null, 'Error comunicado: '), h('code', null, reg.codigo_error), ' ', reg.descripcion_error || '') : null,
      campo('Motivo (obligatorio)', motivo),
      h('div', { class: 'casilla casilla-destacada' }, chk, h('label', { for: chk.id }, 'Confirmo que quiero generar y remitir un registro de subsanación.')),
      error,
    ],
    acciones: [boton('Cancelar', () => dlg.close()), enviar],
  });
  motivo.focus();
}

/* ======================================================================================
 * Remisión: cola y control de flujo
 * ====================================================================================== */

function cuantos(v) {
  if (Array.isArray(v)) return v.length;
  if (v === null || v === undefined) return 0;
  return Number(v) || 0;
}

async function paginaRemision(ctx) {
  titulo('Remisión');
  const eid = app.empresaId;
  let entorno = ENTORNOS[ctx.query.get('entorno')] ? ctx.query.get('entorno') : app.entorno;
  const selEnt = selector(Object.keys(ENTORNOS).map((k) => [k, ENTORNOS[k].corto]), entorno);
  const estado = h('div', { 'aria-live': 'polite' }, cargando());
  const simulador = h('div');
  const cuentaAtras = h('span', { class: 'cuenta-atras' });
  let proximo = null;
  let ultimaCarga = null;

  function tic() {
    if (!proximo) { cuentaAtras.textContent = ''; return; }
    const s = Math.round((new Date(proximo).getTime() - Date.now()) / 1000);
    cuentaAtras.textContent = Number.isNaN(s) ? '' : (s > 0 ? `dentro de ${s} s` : 'permitido ahora');
  }

  async function cargar() {
    try {
      const r = await api(`/empresas/${enc(eid)}/remision?entorno=${enc(entorno)}`);
      if (!ctx.vigente()) return;
      ultimaCarga = new Date();
      refrescarAvisos(true);   // la banda de la cabecera no contradice nunca a esta pantalla (todos los entornos)
      const cf = r.control_flujo || {};
      proximo = cf.proximo_envio_en || null;
      tic();
      const porEstado = r.por_estado && typeof r.por_estado === 'object' ? Object.entries(r.por_estado) : [];
      const stat = (etq, valor, extra) => h('div', { class: 'stat' }, h('span', { class: 'stat-etq' }, etq), h('span', { class: 'stat-valor' }, valor), extra || null);
      const listas = [];
      for (const [k, etq] of [['pendientes', 'Registros pendientes'], ['sin_confirmar', 'Registros sin confirmar'], ['bloqueados', 'Registros bloqueados']]) {
        if (Array.isArray(r[k]) && r[k].length) {
          listas.push(h('h3', null, etq), tabla([
            { titulo: 'Factura', celda: (x) => (x.factura_id ? enlace(x.num_serie || `#${x.factura_id}`, `#/factura/${enc(x.factura_id)}`) : (x.num_serie || '—')) },
            { titulo: 'Secuencia', clase: 'num', celda: (x) => x.secuencia ?? '—' },
            { titulo: 'Estado', celda: (x) => pillEnvio(x.estado_envio) },
            { titulo: 'Intentos', clase: 'num', celda: (x) => x.intentos ?? 0 },
            { titulo: 'Siguiente intento', celda: (x) => fmtFechaHora(x.siguiente_intento_en) },
          ], r[k]));
        }
      }
      anadir(vaciar(estado), [
        h('div', { class: 'stats' },
          stat('Próximo envío permitido', proximo ? fmtFechaHora(proximo) : 'Sin restricción', cuentaAtras),
          stat('Tiempo de espera entre envíos', cf.tiempo_espera !== undefined && cf.tiempo_espera !== null ? `${cf.tiempo_espera} s` : '—',
            h('span', { class: 'muted' }, 'Indicado por la AEAT en cada respuesta')),
          stat('Pendientes', String(cuantos(r.pendientes))),
          stat('Sin confirmar', String(cuantos(r.sin_confirmar))),
          stat('Bloqueados', String(cuantos(r.bloqueados)), cuantos(r.bloqueados) ? h('span', { class: 'muted' }, 'Requieren acción') : null)),
        r.aviso_incidencia ? alerta('warn', h('strong', null, 'Incidencia de comunicación. '), String(r.aviso_incidencia)) : null,
        cf.incidencia_desde ? alerta('warn', h('strong', null, 'Remisión en incidencia '), `desde ${fmtFechaHora(cf.incidencia_desde)}. Los registros se conservan y se reintentarán automáticamente.`) : null,
        cf.bloqueo_hasta && new Date(cf.bloqueo_hasta) > new Date() ? alerta('info', `Envío en curso (${cf.bloqueado_por || 'remitente'}) hasta ${fmtFechaHora(cf.bloqueo_hasta)}.`) : null,
        cuantos(r.sin_confirmar) ? alerta('warn', 'Hay registros cuyo resultado no se ha podido confirmar (sin respuesta). Se consultará o reenviará su estado; no se generan duplicados.') : null,
        porEstado.length ? h('div', { class: 'por-estado' }, h('span', { class: 'muted' }, 'Registros por estado: '),
          porEstado.map(([k, n]) => h('span', { class: 'por-estado-item' }, pillDe(ESTADO_ENVIO, k), ` ${n}`))) : null,
        listas,
        h('h3', null, 'Últimos envíos'),
        tabla([
          { titulo: 'Inicio', celda: (e) => fmtFechaHora(e.iniciado_en) },
          { titulo: 'Fin', celda: (e) => fmtFechaHora(e.finalizado_en) },
          { titulo: 'Registros', clase: 'num', celda: (e) => e.num_registros ?? '—' },
          { titulo: 'Resultado', celda: (e) => (e.resultado ? pillDe(RESULTADO_ENVIO, e.resultado) : pill('En curso', 'info')) },
          { titulo: 'HTTP', clase: 'num', celda: (e) => e.http_estado ?? '—' },
          { titulo: 'CSV', celda: (e) => (e.csv ? h('span', { class: 'mono valor-largo' }, e.csv) : '—') },
          { titulo: 'Espera (s)', clase: 'num', celda: (e) => e.tiempo_espera ?? '—' },
          { titulo: 'Destino', celda: (e) => h('span', { class: 'valor-largo muted' }, e.destino || '—') },
          { titulo: 'Error', celda: (e) => (e.error ? h('span', { class: 'valor-largo' }, e.error) : '—') },
        ], comoLista(r.ultimos_envios), { vacio: 'Todavía no se ha realizado ningún envío en este entorno.', leyenda: 'Últimos envíos' }),
        h('p', { class: 'muted' }, `Actualizado: ${ultimaCarga.toLocaleTimeString('es-ES', { timeZone: ZONA_HORARIA })}. Se actualiza cada 15 s.`)]);
      renderSimulador(r);
    } catch (err) {
      if (ctx.vigente() && err.estado !== 401) vaciar(estado).append(cajaError(err, cargar));
    }
  }

  let simuladorPintado = false;
  function renderSimulador(r) {
    if (entorno !== 'simulador') { vaciar(simulador); simuladorPintado = false; return; }
    const modoActual = (r.simulador && r.simulador.modo) || (r.modo_simulador && (r.modo_simulador.modo || r.modo_simulador)) || null;
    const veces = r.simulador && Number(r.simulador.veces);
    const textoModo = modoActual
      ? `Modo actual: ${(MODOS_SIMULADOR.find(([k]) => k === modoActual) || [null, modoActual])[1]}${modoActual !== 'normal' && veces ? ` (quedan ${veces} envío(s))` : ''}`
      : '';
    if (simuladorPintado) {
      const t = simulador.querySelector('.modo-actual');
      if (t) t.textContent = textoModo;
      return;
    }
    simuladorPintado = true;
    const puedeCambiar = esAdmin();   // el operador, si no es administrador de la empresa, lo fija desde su panel
    const vecesInput = entrada({ type: 'number', min: 1, max: 20, step: 1, value: '1' });
    const form = h('form', { class: 'form' },
      grupoRadios('modo_sim', 'Comportamiento del simulador en los próximos envíos de esta empresa', MODOS_SIMULADOR.map(([k, t, desc]) => [k, t, desc]), modoActual || 'normal'),
      h('div', { class: 'fila-entorno' }, campo('Número de envíos afectados', vecesInput)),
      h('div', { class: 'acciones' }, h('button', { type: 'submit', class: 'btn btn-secundario', disabled: !puedeCambiar }, 'Aplicar modo')));
    if (!puedeCambiar) form.querySelectorAll('input').forEach((x) => { x.disabled = true; });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('button[type="submit"]');
      const modo = valorRadio(form, 'modo_sim');
      await conEspera(btn, async () => {
        try {
          await api(`/empresas/${enc(eid)}/simulador/modo`, { metodo: 'POST', json: { modo, veces: Math.max(1, Math.min(20, Number(vecesInput.value) || 1)) } });
          notificar(`Simulador: modo «${(MODOS_SIMULADOR.find(([k]) => k === modo) || [null, modo])[1]}» para esta empresa.`, 'ok');
          cargar();
        } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
      });
    });
    vaciar(simulador).append(h('section', { class: 'card card-simulador' },
      h('h2', { class: 'card-titulo' }, 'Simulación local — no es la AEAT'),
      h('p', null, 'Permite probar cómo responde la aplicación ante fallos del servicio. Solo afecta a los envíos de esta empresa al simulador local; '
        + 'nunca a la AEAT ni a otras empresas.'),
      h('p', { class: 'modo-actual muted' }, textoModo),
      h('p', { class: 'ayuda' }, 'Los modos de fallo se aplican al número de envíos indicado y después el simulador vuelve automáticamente a «Normal». '
        + 'Los modos de representación solo cuentan los envíos que llevan representante (certificado de un tercero).'),
      puedeCambiar ? null : alerta('info', 'Solo un administrador de la empresa puede cambiar el modo del simulador (el operador de la plataforma, desde su panel, con el NIF de la empresa).'),
      form));
  }

  selEnt.addEventListener('change', () => {
    entorno = selEnt.value;
    fijarQuery({ entorno });
    vaciar(estado).append(cargando());
    simuladorPintado = false;
    vaciar(simulador);
    vaciar(resultadoAuditoria);
    badge.replaceChildren(badgeEntorno(entorno, true));
    cargar();
  });
  const badge = h('span', null, badgeEntorno(entorno, true));
  const resultadoAuditoria = h('div', { 'aria-live': 'polite' });
  const auditar = boton('Verificar la cadena de registros', async () => {
    vaciar(resultadoAuditoria);
    await conEspera(auditar, async () => {
      try {
        const a = await api(`/empresas/${enc(eid)}/auditoria?entorno=${enc(entorno)}`);
        const errs = comoLista(a.errores);
        resultadoAuditoria.append(a.ok
          ? alerta('ok', h('strong', null, 'Cadena íntegra. '), `${a.registros ?? 0} registro(s) verificados en ${ENTORNOS[entorno].corto}: huellas, encadenamiento, secuencia y XML coherentes.`)
          : alerta('danger', h('strong', null, 'Se han detectado incoherencias en la cadena. '), `${a.registros ?? 0} registro(s) verificados.`,
            h('ul', { class: 'lista-simple' }, errs.map((x) => h('li', null, String(x))))));
      } catch (err) { if (err.estado !== 401) resultadoAuditoria.append(alerta('danger', err.message)); }
    }, 'Verificando…');
  });
  const intervalo = window.setInterval(() => { if (!document.hidden) cargar(); }, 15000);
  const reloj = window.setInterval(tic, 1000);
  ctx.alSalir(() => { window.clearInterval(intervalo); window.clearInterval(reloj); });

  montar(ctx,
    cabeceraPagina('Remisión de registros', { acciones: [boton('Actualizar', () => cargar())] }),
    tarjeta(null, h('div', { class: 'fila-entorno' }, campo('Entorno', selEnt), badge),
      h('p', { class: 'ayuda' }, 'Los registros se remiten automáticamente respetando el control de flujo de la AEAT (tiempo de espera entre envíos y máximo de registros por envío).')),
    tarjeta('Estado de la cola', estado),
    tarjeta('Verificación independiente de la cadena',
      h('p', null, 'Recalcula las huellas de todos los registros del entorno y comprueba su encadenamiento, la secuencia y la coherencia con el XML.'),
      h('div', { class: 'acciones' }, auditar), resultadoAuditoria),
    simulador);
  cargar();
}

/* ======================================================================================
 * Actividad y declaración responsable
 * ====================================================================================== */

/** Textos de los códigos internos que aparecen como valor en el detalle de actividad (los mismos de los formularios). */
const VALORES_DETALLE = {
  tipo_contribuyente: Object.fromEntries(TIPOS_CONTRIBUYENTE),
  territorio: Object.fromEntries(TERRITORIOS),
  origen_pdf: Object.fromEntries(ORIGENES_PDF),
  relacion: Object.fromEntries(RELACIONES),
  clasificacion: { ...Object.fromEntries(Object.entries(CLASIFICACIONES).map(([c, d]) => [c, d.etiqueta])), pendiente_confirmar: 'Requiere su decisión' },
  modo: Object.fromEntries(MODOS_SIMULADOR.map(([m, t]) => [m, t])),
  pdf: { reconstruccion: 'Reconstruido con los datos aprobados', superposicion: 'QR añadido sobre el PDF original' },
  operacion: { remision: 'Envío de registros', consulta: 'Consulta a la AEAT' },
  servicio: { remision: 'Envío de registros', consulta: 'Consulta' },
  estado: Object.fromEntries(Object.entries(ESTADO_ENVIO).map(([e, [t]]) => [e, t])),
  tipo: TIPOS_CERTIFICADO,
  metodo: METODO_LEGITIMACION_TEXTO,
  motivo: {
    conciliacion_aeat: 'Conciliación con la consulta a la AEAT', operador: 'Verificado por el operador de la plataforma',
    alta_por_operador: 'La dio de alta el operador de la plataforma',
    version_anterior: 'Dada de alta por el operador en la versión anterior',
    envio_certificado_propio: 'Envío aceptado con su propio certificado',
    sin_registros_posteriores: 'La AEAT ya no muestra registros posteriores al último local',
  },
  clave: { registro_abierto: 'Registro de cuentas abierto', invitaciones_empresa_crean_cuentas: 'Las invitaciones de las empresas crean cuentas' },
};
const GRUPO_VALOR = { clasificacion_sugerida: 'clasificacion', estado_anterior: 'estado', estado_nuevo: 'estado' };
/** Datos que se guardan como 0/1. */
const DETALLE_SI_NO = new Set(['sii', 'canarias_ceuta_melilla', 'ocr', 'propio', 'propia', 'tls', 'puede_emitir']);
const RESUMEN_CONSULTA = {
  coinciden: 'coinciden', solo_aeat: 'solo en la AEAT', solo_local: 'solo en la aplicación', distintos: 'distintos',
  conciliables: 'conciliables', revisar: 'a revisar',
};

/**
 * Valor de un dato del detalle de actividad, legible: Sí/No, rol, entorno, códigos con su texto, resúmenes en palabras y
 * fechas en hora peninsular. `o` es el detalle completo (p. ej. el valor de un ajuste depende de cuál sea).
 */
function valorDetalle(k, x, o = {}) {
  if (typeof x === 'boolean') return x ? 'Sí' : 'No';
  if ((DETALLE_SI_NO.has(k) || (k === 'valor' && VALORES_DETALLE.clave[o.clave])) && (x === 0 || x === 1 || x === '0' || x === '1')) {
    return Number(x) ? 'Sí' : 'No';
  }
  if (k === 'claves' && Array.isArray(x)) {
    // «CÓDIGO:campo» de cada punto confirmado: se muestra el dato al que se refiere
    return [...new Set(x.map((c) => etiquetaCampo(String(c).includes(':') ? String(c).split(':').slice(1).join(':') : String(c))))].join(', ');
  }
  if (Array.isArray(x)) return x.map((y) => valorDetalle(k, y, o)).join(', ');
  if (k === 'verificacion_pdf' && typeof x === 'object') {
    const mal = Object.entries(x).filter(([, ok]) => ok === false).map(([c]) => ETIQUETAS_VERIFICACION[c] || humanizar(c));
    return mal.length ? `no supera: ${mal.join(', ')}` : 'todas las comprobaciones correctas';
  }
  if (k === 'resumen' && typeof x === 'object') {
    return Object.entries(x).map(([c, n]) => `${c === 'solo_aeat' ? `solo en ${servicioDetalle(o).donde}` : (RESUMEN_CONSULTA[c] || humanizar(c).toLowerCase())} ${n}`).join(', ');
  }
  if (typeof x === 'object') {
    return Object.entries(x).filter(([, y]) => y !== null && y !== undefined && y !== '')
      .map(([c, y]) => `${etiquetaDetalle(c, o)}: ${valorDetalle(c, y, o)}`).join(', ');
  }
  if (k === 'rol' && ROLES_TEXTO[x]) return ROLES_TEXTO[x];
  if (k === 'motivo' && x === 'conciliacion_aeat') return `Conciliación con la consulta ${o.entorno === 'simulador' ? 'al simulador local' : 'a la AEAT'}`;
  if (k === 'motivo' && x === 'sin_registros_posteriores') return `${servicioDetalle(o).Donde} ya no muestra registros posteriores al último local`;
  if (k === 'modo' && o.servicio === 'consulta' && typeof x === 'string') {
    const m = MODOS_CONSULTA_SIMULADOR.find(([c]) => c === x);
    if (m) return m[1];
  }
  if (k === 'copia' && typeof x === 'string') {
    // Carpeta de una copia: «copia_AAAAMMDDTHHMMSS» (hora peninsular, la de la instalación)
    const m = /^copia_(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(x);
    if (m) return `copia del ${fmtHoraPeninsular(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}`)}`;
  }
  if (/^fecha/.test(k) && typeof x === 'string' && /^\d{2}-\d{2}-\d{4}$/.test(x)) return x.replace(/-/g, '/');   // fecha en el formato de la AEAT (solo en datos de fecha: un número de factura puede tener esa forma)
  if (k === 'entorno' && ENTORNOS[x]) return ENTORNOS[x].corto;
  const mapa = VALORES_DETALLE[GRUPO_VALOR[k] || k];
  if (mapa && typeof x === 'string' && Object.prototype.hasOwnProperty.call(mapa, x)) return mapa[x];
  if (k === 'evidencia' && typeof x === 'string') {
    // Carpeta de la respuesta de la AEAT guardada en el servidor: «…/consultas/AAAAMMDDTHHMMSS-xxxx» (hora UTC)
    const m = /consultas\/(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})-([0-9a-f]+)$/.exec(x);
    if (m) return `respuesta de la consulta guardada el ${fmtFechaHora(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`)} (ref. ${m[7]})`;
  }
  if (typeof x === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(x)) return fmtHoraPeninsular(x);   // sin huso
  if (typeof x === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(x)) return fmtFechaHora(x);
  return String(x);
}

/** Una fecha y hora sin huso que el servidor guarda ya en hora peninsular (p. ej. el minuto de un error): se muestra tal
 * cual, sin convertirla desde la zona del navegador. */
function fmtHoraPeninsular(v) {
  const d = new Date(`${String(v).slice(0, 16)}:00Z`);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short', timeZone: 'UTC' });
}

/** Servicio al que se refiere un detalle de actividad (el simulador local o la AEAT), por su entorno o su servicio. */
function servicioDetalle(o = {}) {
  if (o.entorno) return nombresServicio(o.entorno);
  return nombresServicio(typeof o.servicio === 'string' && o.servicio.includes('simulador') ? 'simulador' : 'produccion');
}

/** Etiqueta de un dato del detalle de actividad; las que nombran la AEAT dicen el servicio consultado de verdad. */
function etiquetaDetalle(k, o = {}) {
  if (k === 'estado_aeat') return `Estado en ${servicioDetalle(o).donde}`;
  if (k === 'ultimo_registro_aeat') return `Último registro en ${servicioDetalle(o).donde}`;
  return ETIQUETAS_DETALLE[k] || humanizar(k);
}

function detalleActividad(v) {
  const o = parseJSON(v);
  if (o && typeof o === 'object' && !Array.isArray(o)) {
    return Object.entries(o).filter(([, x]) => x !== null && x !== undefined && x !== '' && !(Array.isArray(x) && !x.length))
      .map(([k, x]) => `${etiquetaDetalle(k, o)}: ${valorDetalle(k, x, o)}`).join(' · ');
  }
  return v === null || v === undefined ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
}

const objetoActividad = (o) => OBJETOS_ACTIVIDAD[o] || humanizar(o);

async function paginaActividad(ctx) {
  titulo('Actividad');
  const r = await api(`/empresas/${enc(app.empresaId)}/actividad`);
  if (!ctx.vigente()) return;
  const eventos = comoLista(r, 'actividad', 'eventos');
  const filtro = entrada({ type: 'search', placeholder: 'Acción, usuario, detalle…' });
  const cont = h('div');
  const rutaObjeto = { factura: '/factura/', borrador: '/borrador/', documento: '/documento/' };
  const usuarioDe = (e) => e.usuario_nombre || e.usuario_email || (e.usuario && typeof e.usuario === 'object' ? e.usuario.nombre || e.usuario.email : e.usuario) || (e.usuario_id ? `Usuario ${e.usuario_id}` : 'Sistema');
  function pintar() {
    const t = filtro.value.trim().toLowerCase();
    const lista = t ? eventos.filter((e) => [e.accion, usuarioDe(e), e.objeto && objetoActividad(e.objeto), detalleActividad(e.detalle)].join(' ').toLowerCase().includes(t)) : eventos;
    vaciar(cont).append(h('p', { class: 'muted', 'aria-live': 'polite' }, `${lista.length} de ${eventos.length} evento(s)`), tabla([
      { titulo: 'Fecha', celda: (e) => fmtFechaHora(e.en) },
      { titulo: 'Usuario', celda: (e) => usuarioDe(e) },
      { titulo: 'Acción', celda: (e) => h('code', null, e.accion || '—') },
      {
        titulo: 'Objeto',
        celda: (e) => (e.objeto && e.objeto_id && rutaObjeto[e.objeto]
          ? enlace(`${objetoActividad(e.objeto)} #${e.objeto_id}`, `#${rutaObjeto[e.objeto]}${enc(e.objeto_id)}`)
          : [e.objeto ? objetoActividad(e.objeto) : '—', e.objeto_id ? ` #${e.objeto_id}` : '']),
      },
      { titulo: 'Detalle', celda: (e) => h('span', { class: 'valor-largo' }, detalleActividad(e.detalle) || '—') },
    ], lista.slice(0, 1000), { vacio: 'No hay actividad registrada.', leyenda: 'Registro de actividad' }));
  }
  filtro.addEventListener('input', pintar);
  pintar();
  montar(ctx,
    cabeceraPagina('Registro de actividad', { meta: [h('span', null, 'Registro de solo adición: las entradas no se modifican ni se borran.')] }),
    tarjeta(null, h('div', { class: 'filtros' }, campo('Filtrar', filtro)), cont));
}

/** Marcas en línea (**negrita**, `código`) convertidas a elementos; el resto es texto. */
function enLinea(texto) {
  return String(texto).split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter((t) => t !== '').map((t) => {
    if (/^\*\*[^*]+\*\*$/.test(t)) return h('strong', null, t.slice(2, -2));
    if (/^`[^`]+`$/.test(t)) return h('code', null, t.slice(1, -1));
    return t;
  });
}

/** Markdown mínimo (#, -, >, ---, **, `) convertido a elementos con textContent: nunca se interpreta HTML. */
function textoEstructurado(texto) {
  const out = [];
  let lista = null;
  let cita = null;
  let parrafo = [];
  const cerrarParrafo = () => {
    if (!parrafo.length) return;
    const p = h('p', null, enLinea(parrafo.join(' ')));
    (cita || { append: (x) => out.push(x) }).append(p);
    parrafo = [];
  };
  for (const bruta of String(texto).split(/\r?\n/)) {
    let linea = bruta.trim();
    if (!linea) { cerrarParrafo(); lista = null; cita = null; continue; }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(linea)) { cerrarParrafo(); lista = null; cita = null; out.push(h('hr')); continue; }
    if (linea.startsWith('>')) {
      linea = linea.replace(/^>\s?/, '');
      if (!cita) { cerrarParrafo(); lista = null; cita = h('blockquote', { class: 'cita' }); out.push(cita); }
      if (!linea) { cerrarParrafo(); continue; }
    } else if (cita) { cerrarParrafo(); cita = null; }
    const m = /^(#{1,4})\s+(.*)$/.exec(linea);
    if (m) { cerrarParrafo(); lista = null; out.push(h(`h${Math.min(4, m[1].length + 1)}`, null, enLinea(m[2]))); continue; }
    const li = /^[-*•]\s+(.*)$/.exec(linea) || /^(\d+[.)]\s+.*)$/.exec(linea);
    if (li) {
      cerrarParrafo();
      if (!lista) { lista = h('ul', { class: 'lista-simple' }); (cita || { append: (x) => out.push(x) }).append(lista); }
      lista.append(h('li', null, enLinea(li[1])));
      continue;
    }
    lista = null;
    parrafo.push(linea);
  }
  cerrarParrafo();
  return out;
}

async function paginaDeclaracion(ctx) {
  titulo('Declaración responsable');
  const r = await api('/declaracion');
  if (!ctx.vigente()) return;
  const decl = (r && (r.declaracion || (Array.isArray(r) ? r[0] : r))) || {};
  const contenido = decl.contenido ?? decl.texto ?? (typeof decl.borrador === 'string' ? decl.borrador : null);
  const formalizada = decl.estado === 'formalizada';
  montar(ctx,
    cabeceraPagina('Declaración responsable del sistema informático de facturación', {
      meta: [formalizada ? pill('Formalizada', 'ok') : pill(decl.estado === 'borrador' || !decl.estado ? 'Borrador — pendiente de formalizar' : String(decl.estado), formalizada ? 'ok' : 'warn')],
    }),
    tarjeta('Estado',
      kv([
        ['Versión del sistema', decl.version_sif || decl.version || (app.estado && app.estado.version) || '—'],
        ['Estado', formalizada ? 'Formalizada' : 'Borrador'],
        ['Formalizada por', decl.formalizada_por || '—'],
        ['Formalizada el', decl.formalizada_en ? fmtFechaHora(decl.formalizada_en) : '—'],
      ]),
      formalizada ? null : alerta('warn', 'La declaración responsable debe formalizarla el productor del sistema (el operador de la plataforma) antes de usarlo en producción. '
        + 'Este texto es un borrador sin validez hasta su formalización.')),
    tarjeta('Texto de la declaración',
      contenido === null || contenido === undefined ? h('p', { class: 'vacio' }, 'No hay texto disponible.')
        : h('div', { class: 'declaracion' }, typeof contenido === 'string' ? textoEstructurado(contenido) : renderGenerico(contenido))));
}

/* ======================================================================================
 * Arranque
 * ====================================================================================== */

async function iniciar() {
  const main = document.getElementById('contenido');
  const saltar = document.getElementById('saltar');
  if (saltar) saltar.addEventListener('click', () => { main.focus(); main.scrollIntoView(); });
  const cab = document.getElementById('cabecera');
  const fijarAlto = () => document.documentElement.style.setProperty('--alto-cabecera', `${cab.offsetHeight}px`);
  if ('ResizeObserver' in window) new ResizeObserver(fijarAlto).observe(cab);
  window.addEventListener('resize', fijarAlto);
  window.addEventListener('hashchange', () => enrutar());
  try {
    app.estado = await api('/estado', { sinRedireccion: true });
  } catch (err) {
    vaciar(main).append(cabeceraPagina('No se puede conectar con el servidor'), cajaError(err, () => window.location.reload()));
    return;
  }
  construirPie();
  if (app.estado.instalada) {
    try {
      aplicarSesion(await api('/yo', { sinRedireccion: true }));
    } catch (err) {
      if (err.estado !== 401) {
        vaciar(main).append(cabeceraPagina('No se pudo cargar la sesión'), cajaError(err, () => window.location.reload()));
        return;
      }
    }
  }
  enrutar({ forzar: true });
}

iniciar();

// --- multiusuario ---
/* ======================================================================================
 * Plataforma multiusuario: registro, invitaciones, cuenta propia, alta de empresas y
 * panel del operador de la plataforma (productor del sistema). Ningún secreto (contraseñas,
 * tokens) se guarda en el navegador: el enlace de una invitación se muestra una sola vez.
 * ====================================================================================== */

const ROLES_TEXTO = { admin: 'Administrador', emisor: 'Emisor', lector: 'Lector' };
const ESTADO_INVITACION = {
  pendiente: ['Pendiente', 'info'], usada: ['Usada', 'ok'], caducada: ['Caducada', 'neutral'], revocada: ['Anulada', 'neutral'],
};
const ORIGEN_ALTA = {
  instalacion: 'Instalación', registro: 'Registro', invitacion: 'Invitación', alta_operador: 'Alta por el operador',
  version_anterior: 'Versión anterior',
};

/** Primera pantalla tras entrar: la empresa elegida o, sin empresas, el alta de una (o el panel, para el operador). */
function rutaInicioMU() {
  if (app.empresaId) return '/importar';
  return esSuperadmin() ? '/plataforma' : '/empresa-nueva';
}

function enlacesCuentaMU() {
  return [
    esSuperadmin() ? enlace('Plataforma', '#/plataforma', { tipo: 'enlace', pequeno: true }) : null,
    enlace('Nueva empresa', '#/empresa-nueva', { tipo: 'enlace', pequeno: true }),
    enlace('Mi cuenta', '#/cuenta', { tipo: 'enlace', pequeno: true }),
  ];
}

function vistaSinEmpresaMU() {
  return [
    cabeceraPagina('Todavía no tiene ninguna empresa'),
    tarjeta(null,
      h('p', null, 'Para empezar, dé de alta la empresa (obligado tributario) cuyas facturas va a expedir. Quedará como su administrador.'),
      h('p', null, 'Si su empresa ya usa la aplicación, pida a uno de sus administradores que le invite.'),
      h('div', { class: 'acciones' },
        enlace('Dar de alta una empresa', '#/empresa-nueva', { tipo: 'primario' }),
        esSuperadmin() ? enlace('Panel de la plataforma', '#/plataforma', { tipo: 'secundario' }) : null)),
  ];
}

function pieLoginMU() {
  if (app.estado && app.estado.registro_abierto) {
    return h('p', { class: 'ayuda' }, '¿No tiene cuenta? ', h('a', { href: '#/registro' }, 'Cree una cuenta'), '.');
  }
  return h('p', { class: 'ayuda' }, '¿No tiene cuenta? Pida una invitación al operador de la plataforma o al administrador de su empresa.');
}

function campoRegistroAbiertoMU() {
  const chk = h('input', { type: 'checkbox', id: uid('chk') });
  const nodo = h('fieldset', null, h('legend', null, 'Plataforma'),
    h('div', { class: 'radio-opcion' }, chk,
      h('label', { for: chk.id },
        h('span', { class: 'radio-titulo' }, 'Permitir que otras personas creen su propia cuenta'),
        h('span', { class: 'radio-desc' }, 'Cada persona gestionará sus propias empresas, sin acceso a las de los demás. '
          + 'Si no lo marca, solo podrá entrar quien reciba una invitación. Puede cambiarlo después en el panel de la plataforma.'))));
  return { nodo, valor: () => chk.checked };
}

/** Formulario de cambio de la contraseña propia (también para sustituir una contraseña temporal). */
function formClaveMU({ temporal = false, alCambiar }) {
  const actual = entrada({ type: 'password', required: true, autocomplete: 'current-password' });
  const nueva = entrada({ type: 'password', required: true, minlength: 10, maxlength: 256, autocomplete: 'new-password' });
  const nueva2 = entrada({ type: 'password', required: true, minlength: 10, maxlength: 256, autocomplete: 'new-password' });
  const error = h('div', { 'aria-live': 'assertive' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-primario' }, 'Cambiar la contraseña');
  const form = h('form', { class: 'form' },
    campo(temporal ? 'Contraseña temporal' : 'Contraseña actual', actual),
    campo('Nueva contraseña', nueva, { ayuda: 'Al menos 10 caracteres. Al cambiarla se cierran sus demás sesiones abiertas.' }),
    campo('Repita la nueva contraseña', nueva2),
    error,
    h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(error);
    if (nueva.value !== nueva2.value) { error.append(alerta('danger', 'Las contraseñas nuevas no coinciden.')); nueva2.focus(); return; }
    if (nueva.value.length < 10) { error.append(alerta('danger', 'La contraseña debe tener al menos 10 caracteres.')); nueva.focus(); return; }
    await conEspera(enviar, async () => {
      try {
        await api('/yo/clave', { metodo: 'PUT', json: { clave_actual: actual.value, clave_nueva: nueva.value } });
        actual.value = ''; nueva.value = ''; nueva2.value = '';
        await alCambiar();
      } catch (err) {
        actual.value = '';
        if (err.estado !== 401) error.append(alerta('danger', err.message));
      }
    }, 'Guardando…');
  });
  return form;
}

const TEXTO_CUENTA_VERSION_ANTERIOR = 'Su cuenta procede de la versión anterior de la aplicación, en la que la contraseña '
  + 'la fijaba el operador o el administrador de su empresa al darle de alta y no podía cambiarse.';

async function paginaCambiarClave(ctx) {
  titulo('Cambiar la contraseña');
  // Cuenta de la versión anterior que aún conserva la contraseña de entonces (no una temporal fijada después por el operador)
  const heredada = Boolean(app.usuario && app.usuario.alta_origen === 'version_anterior' && !app.usuario.clave_restablecida_en);
  const form = formClaveMU({
    temporal: !heredada,
    alCambiar: async () => {
      aplicarSesion(await api('/yo'));
      notificar('Contraseña cambiada.', 'ok');
      irA(rutaInicioMU());
    },
  });
  montar(ctx, marcoPublico(
    h('h1', null, 'Elija una contraseña nueva'),
    heredada
      ? alerta('warn', TEXTO_CUENTA_VERSION_ANTERIOR + ' Otra persona puede conocerla: para continuar, elija una nueva que '
        + 'solo conozca usted.')
      : alerta('warn', 'Su contraseña actual es temporal (la fijó el operador de la plataforma o se creó junto con su cuenta). '
        + 'Para continuar, sustitúyala por una que solo conozca usted.'),
    form,
    h('div', { class: 'acciones' }, boton('Cerrar sesión', cerrarSesion, { tipo: 'enlace', pequeno: true }))));
}

async function paginaCuenta(ctx) {
  titulo('Mi cuenta');
  const yo = await api('/yo');
  if (!ctx.vigente()) return;
  sincronizarEmpresas(yo);   // la cabecera muestra las mismas empresas que esta pantalla
  const u = yo.usuario || {};
  const empresas = comoLista(yo.empresas);
  montar(ctx,
    cabeceraPagina('Mi cuenta'),
    tarjeta('Datos de la cuenta', kv([
      ['Nombre', u.nombre || '—'],
      ['Correo electrónico', u.email || '—'],
      ['Operador de la plataforma', u.superadmin ? 'Sí' : 'No'],
      ['Último restablecimiento de la contraseña por el operador', u.clave_restablecida_en ? fmtFechaHora(u.clave_restablecida_en) : 'Nunca'],
    ]), u.clave_restablecida_en ? h('p', { class: 'ayuda' }, 'Si no lo pidió usted, cambie su contraseña y avise a los administradores '
      + 'de sus empresas: queda anotado también en la actividad de cada una.') : null,
    u.alta_origen === 'version_anterior' ? h('p', { class: 'ayuda' }, TEXTO_CUENTA_VERSION_ANTERIOR + ' Por eso, tras la '
      + 'actualización, se le pidió elegir una contraseña nueva que solo conozca usted.') : null),
    tarjeta('Empresas a las que tiene acceso',
      tabla([
        { titulo: 'Razón social', celda: (e) => e.razon_social },
        { titulo: 'NIF', celda: (e) => h('span', { class: 'mono' }, e.nif) },
        { titulo: 'Rol', celda: (e) => ROLES_TEXTO[e.rol] || e.rol },
        { titulo: 'Acciones', celda: (e) => botonQuitarMiembroMU(e, { id: u.id, email: u.email }, () => enrutar({ forzar: true })) },
      ], empresas, { vacio: 'Todavía no tiene acceso a ninguna empresa.', leyenda: 'Empresas' }),
      h('div', { class: 'acciones' }, enlace('Dar de alta una empresa', '#/empresa-nueva', { tipo: 'secundario' }))),
    tarjeta('Cambiar la contraseña', formClaveMU({
      alCambiar: async () => notificar('Contraseña cambiada. Sus demás sesiones se han cerrado.', 'ok'),
    })));
}

async function paginaEmpresaNueva(ctx) {
  titulo('Nueva empresa');
  const nif = entrada({ required: true, maxlength: 20, class: 'mono mayus', spellcheck: 'false', autocomplete: 'off' });
  const razon = entrada({ required: true, maxlength: 120, autocomplete: 'organization' });
  const dom = h('textarea', { rows: 2, maxlength: 300, required: true });
  const error = h('div', { 'aria-live': 'assertive' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-primario' }, 'Dar de alta la empresa');
  const form = h('form', { class: 'form' },
    h('div', { class: 'fila-campos' }, campo('NIF', nif), campo('Razón social', razon)),
    campo('Domicilio fiscal', dom),
    error,
    h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(error);
    await conEspera(enviar, async () => {
      try {
        const nueva = await api('/empresas', {
          metodo: 'POST', json: { nif: nif.value.trim().toUpperCase(), razon_social: razon.value.trim(), domicilio: dom.value.trim() },
        });
        if (nueva && nueva.id) guardarLS(LS_EMPRESA, String(nueva.id));
        aplicarSesion(await api('/yo'));
        notificar('Empresa dada de alta. Complete su configuración.', 'ok');
        irA('/configuracion/empresa');
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Creando…');
  });
  montar(ctx,
    cabeceraPagina('Dar de alta una empresa'),
    tarjeta(null,
      h('p', null, 'Usted quedará como administrador de la empresa y podrá invitar a otras personas. Nadie más verá sus documentos ni sus facturas.'),
      h('p', { class: 'ayuda' }, 'Cada empresa (NIF) solo puede darse de alta una vez en la aplicación: si ya lo está, pida a uno de sus administradores que le invite.'),
      form));
  nif.focus();
}

async function paginaRegistro(ctx) {
  titulo('Crear una cuenta');
  const token = ctx.query.get('invitacion') || '';
  let inv = null;
  if (token) {
    try {
      inv = await api('/invitaciones/consulta', { metodo: 'POST', json: { token }, sinRedireccion: true });
    } catch (err) {
      inv = { valida: false, error: err.message };
    }
    if (!ctx.vigente()) return;
  }
  const textoInv = inv && inv.valida
    ? (inv.empresa ? `Le han invitado a «${inv.empresa}» con el rol de ${(ROLES_TEXTO[inv.rol] || inv.rol || '').toLowerCase()}.`
      : 'Ha recibido una invitación para crear su cuenta en la plataforma.')
    : null;
  const irLogin = enlace('Iniciar sesión', '#/login', {
    tipo: 'secundario',
    onclick: () => { app.volverA = token ? `/registro?invitacion=${enc(token)}` : null; },
  });
  if (token && (!inv || !inv.valida)) {
    montar(ctx, marcoPublico(h('h1', null, 'Invitación no válida'),
      alerta('warn', inv && inv.error ? inv.error : 'El enlace de invitación no es válido: puede haber caducado, haberse usado ya o haberse anulado. Pida uno nuevo.'),
      h('div', { class: 'acciones' }, app.usuario ? enlace('Ir a la aplicación', `#${rutaInicioMU()}`, { tipo: 'secundario' }) : irLogin)));
    return;
  }
  // Con la sesión iniciada: la invitación a una empresa se acepta con esta cuenta
  if (app.usuario) {
    if (!inv || !inv.empresa) {
      montar(ctx, marcoPublico(h('h1', null, 'Ya tiene una cuenta'),
        h('p', null, `Ha iniciado sesión como ${app.usuario.email}.`),
        h('div', { class: 'acciones' }, enlace('Ir a la aplicación', `#${rutaInicioMU()}`, { tipo: 'primario' }))));
      return;
    }
    const aceptar = boton('Aceptar la invitación', async () => {
      await conEspera(aceptar, async () => {
        try {
          const r = await api('/invitaciones/aceptar', { metodo: 'POST', json: { token } });
          if (r && r.empresa_id) guardarLS(LS_EMPRESA, String(r.empresa_id));
          aplicarSesion(await api('/yo'));
          notificar(`Ya tiene acceso a ${inv.empresa}.`, 'ok');
          irA('/importar');
        } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
      }, 'Aceptando…');
    }, { tipo: 'primario' });
    montar(ctx, marcoPublico(h('h1', null, 'Invitación'),
      alerta('info', textoInv),
      h('p', null, `Ha iniciado sesión como ${app.usuario.email}. Solo puede aceptarla la cuenta del correo al que se dirigió`
        + `${inv.email ? ` (${inv.email})` : ''}.`),
      h('div', { class: 'acciones' }, aceptar, enlace('Ahora no', `#${rutaInicioMU()}`, { tipo: 'secundario' }))));
    return;
  }
  if (inv && inv.valida && inv.crea_cuenta === false) {
    montar(ctx, marcoPublico(h('h1', null, 'Invitación para una cuenta existente'),
      alerta('info', textoInv),
      alerta('warn', 'El registro de nuevas cuentas está cerrado: esta invitación solo puede aceptarla quien ya tenga una cuenta. '
        + 'Inicie sesión y acéptela. Si aún no tiene cuenta, pida al operador de la plataforma una invitación para crearla.'),
      h('div', { class: 'acciones' }, irLogin)));
    return;
  }
  if (!token && !(app.estado && app.estado.registro_abierto)) {
    montar(ctx, marcoPublico(h('h1', null, 'Registro cerrado'),
      alerta('info', 'En esta plataforma solo pueden crear su cuenta las personas invitadas. Pida una invitación al operador de la '
        + 'plataforma o al administrador de su empresa.'),
      h('div', { class: 'acciones' }, irLogin)));
    return;
  }
  const nombre = entrada({ required: true, maxlength: 120, autocomplete: 'name' });
  const email = entrada({ type: 'email', required: true, maxlength: 200, autocomplete: 'username', value: (inv && inv.email) || '', readOnly: !!(inv && inv.email) });
  const clave = entrada({ type: 'password', required: true, minlength: 10, maxlength: 256, autocomplete: 'new-password' });
  const clave2 = entrada({ type: 'password', required: true, minlength: 10, maxlength: 256, autocomplete: 'new-password' });
  const error = h('div', { 'aria-live': 'assertive' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-primario' }, 'Crear la cuenta');
  const form = h('form', { class: 'form' },
    campo('Nombre y apellidos', nombre),
    campo('Correo electrónico', email, { ayuda: inv && inv.email ? 'La invitación es para este correo.' : 'Será su usuario para entrar.' }),
    campo('Contraseña', clave, { ayuda: 'Al menos 10 caracteres.' }),
    campo('Repita la contraseña', clave2),
    error,
    h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(error);
    if (clave.value !== clave2.value) { error.append(alerta('danger', 'Las contraseñas no coinciden.')); clave2.focus(); return; }
    if (clave.value.length < 10) { error.append(alerta('danger', 'La contraseña debe tener al menos 10 caracteres.')); clave.focus(); return; }
    await conEspera(enviar, async () => {
      try {
        const r = await api('/registro', {
          metodo: 'POST', sinRedireccion: true,
          json: { email: email.value.trim(), nombre: nombre.value.trim(), clave: clave.value, invitacion: token || undefined },
        });
        clave.value = ''; clave2.value = '';
        aplicarSesion(r);
        notificar('Cuenta creada.', 'ok');
        irA(rutaInicioMU());
      } catch (err) {
        clave.value = ''; clave2.value = '';
        error.append(alerta('danger', err.message));
      }
    }, 'Creando…');
  });
  montar(ctx, marcoPublico(
    h('h1', null, 'Crear una cuenta'),
    textoInv ? alerta('info', textoInv) : h('p', null, 'Con su cuenta podrá dar de alta sus empresas y expedir sus facturas. Nadie más verá sus datos salvo las personas que usted invite.'),
    form,
    h('p', { class: 'ayuda' }, '¿Ya tiene una cuenta? ', irLogin)));
  nombre.focus();
}

/** Enlace de una invitación recién creada: se muestra una sola vez (el servidor solo guarda su resumen). */
function cajaEnlaceMU(inv) {
  const url = `${window.location.origin}${inv.enlace}`;
  const campoUrl = entrada({ value: url, readOnly: true, class: 'mono', 'aria-label': 'Enlace de la invitación', spellcheck: 'false' });
  const copiar = boton('Copiar el enlace', async () => {
    try {
      await navigator.clipboard.writeText(url);
      notificar('Enlace copiado.', 'ok');
    } catch {
      campoUrl.select();
      notificar('Enlace seleccionado: cópielo con Ctrl+C.', 'info');
    }
  }, { pequeno: true });
  return alerta('ok',
    h('p', null, h('strong', null, 'Invitación creada. '),
      `Envíe este enlace a ${inv.email || 'la persona invitada'} por un canal de confianza. Es de un solo uso, caduca el `
      + `${fmtFechaHora(inv.expira_en)} y no volverá a mostrarse.`),
    campoUrl,
    inv.crea_cuenta === false ? h('p', { class: 'ayuda' }, 'El registro está cerrado: este enlace solo sirve a quien ya tenga '
      + 'cuenta en la plataforma. Si la persona aún no la tiene, necesitará además una invitación del operador de la plataforma.') : null,
    h('div', { class: 'acciones' }, copiar));
}

function tablaInvitacionesMU(invs, recargar) {
  return tabla([
    { titulo: 'Correo', celda: (i) => i.email || h('span', { class: 'muted' }, 'cualquiera') },
    { titulo: 'Rol', celda: (i) => (i.rol ? ROLES_TEXTO[i.rol] || i.rol : '—') },
    { titulo: 'Creada', celda: (i) => fmtFechaHora(i.creada_en) },
    { titulo: 'Caduca', celda: (i) => fmtFechaHora(i.expira_en) },
    { titulo: 'Estado', celda: (i) => pillDe(ESTADO_INVITACION, i.estado) },
    {
      titulo: 'Acciones',
      celda: (i) => (i.estado === 'pendiente' ? boton('Anular', async (ev) => {
        const btn = ev.currentTarget;
        const ok = await confirmar({ titulo: 'Anular la invitación', texto: 'El enlace dejará de valer. ¿Desea anularla?', textoBoton: 'Anular', peligro: true });
        if (!ok) return;
        await conEspera(btn, async () => {
          try {
            await api(`/invitaciones/${enc(i.id)}`, { metodo: 'DELETE' });
            notificar('Invitación anulada.', 'ok');
            recargar();
          } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
        });
      }, { pequeno: true }) : '—'),
    },
  ], invs, { vacio: 'No hay invitaciones.', leyenda: 'Invitaciones' });
}

/** Paso «Usuarios y empresas» de la configuración de una empresa. */
function pasoUsuariosMU(emp, recargar) {
  const dis = !esAdmin();
  const listas = h('div', null, dis ? alerta('info', 'Solo un administrador de la empresa puede ver y gestionar quién tiene acceso.') : cargando());
  const cargarListas = async () => {
    if (dis) return;
    const [rM, rI] = await Promise.allSettled([api(`/empresas/${enc(emp.id)}/miembros`), api(`/empresas/${enc(emp.id)}/invitaciones`)]);
    vaciar(listas);
    if (rM.status === 'fulfilled') {
      listas.append(h('h3', null, 'Personas con acceso'), tabla([
        { titulo: 'Nombre', celda: (m) => m.nombre || '—' },
        { titulo: 'Correo', celda: (m) => m.email },
        { titulo: 'Rol', celda: (m) => ROLES_TEXTO[m.rol] || m.rol },
        { titulo: 'Relación', celda: (m) => (RELACIONES.find(([k]) => k === m.relacion) || [null, m.relacion || '—'])[1] },
        { titulo: 'Declaración de autorización', celda: (m) => (m.declaracion_autorizacion_en ? fmtFechaHora(m.declaracion_autorizacion_en) : pill('Pendiente', 'warn')) },
        { titulo: 'Cuenta', celda: (m) => (m.activo ? pill('Activa', 'ok') : pill('Desactivada', 'danger')) },
        { titulo: 'Acciones', celda: (m) => botonQuitarMiembroMU(emp, m, cargarListas) },
      ], comoLista(rM.value), { vacio: 'No hay miembros.', leyenda: 'Miembros' }));
    } else listas.append(cajaError(rM.reason));
    if (rI.status === 'fulfilled') {
      listas.append(h('h3', null, 'Invitaciones'), tablaInvitacionesMU(comoLista(rI.value), cargarListas));
    }
  };
  cargarListas();

  const email = entrada({ type: 'email', required: true, autocomplete: 'off', maxlength: 200, disabled: dis });
  const relacion = selector(RELACIONES, 'empleado_autorizado', { disabled: dis });
  const resultado = h('div', { 'aria-live': 'polite' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-primario', disabled: dis }, 'Invitar');
  const form = h('form', { class: 'form' },
    campo('Correo electrónico', email, { ayuda: 'Si ya es miembro, se actualizan su rol y su relación (un administrador que deja de serlo pierde sus invitaciones pendientes). Si no, se crea una invitación de un solo uso.' }),
    grupoRadios('rol_invitado', 'Rol en la empresa', ROLES, 'emisor'),
    campo('Relación con la empresa', relacion),
    resultado,
    h('div', { class: 'acciones' }, enviar));
  if (dis) form.querySelectorAll('input').forEach((x) => { x.disabled = true; });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultado);
    await conEspera(enviar, async () => {
      try {
        const r = await api(`/empresas/${enc(emp.id)}/miembros`, {
          metodo: 'POST', json: { email: email.value.trim(), rol: valorRadio(form, 'rol_invitado'), relacion: relacion.value },
        });
        if (r && r.accion === 'invitacion' && r.invitacion) resultado.append(cajaEnlaceMU(r.invitacion));
        else notificar(`Rol de ${email.value.trim()} actualizado.`, 'ok');
        email.value = '';
        cargarListas();
      } catch (err) { if (err.estado !== 401) resultado.append(alerta('danger', err.message)); }
    }, 'Guardando…');
  });
  return [
    tarjeta(`Quién tiene acceso a ${emp.razon_social || 'esta empresa'}`, listas),
    tarjeta('Invitar a una persona',
      h('p', null, 'La persona invitada crea su cuenta (o entra con la que ya tiene) desde el enlace y elige su propia contraseña: '
        + 'nadie más la conoce. Hasta que acepte, no tiene acceso a la empresa.'),
      dis ? alerta('info', 'Solo un administrador puede invitar.') : null,
      form),
    tarjeta('Otras empresas',
      h('p', null, 'Puede gestionar la facturación de varias empresas: quedará como administrador de cada empresa que dé de alta.'),
      h('div', { class: 'acciones' }, enlace('Dar de alta otra empresa', '#/empresa-nueva', { tipo: 'secundario' }))),
  ];
}

/* ---------- Panel del operador de la plataforma ---------- */

function dialogoClaveTemporalMU(u, alTerminar) {
  const c1 = entrada({ type: 'password', required: true, minlength: 10, maxlength: 256, autocomplete: 'new-password' });
  const c2 = entrada({ type: 'password', required: true, minlength: 10, maxlength: 256, autocomplete: 'new-password' });
  const error = h('p', { class: 'error-campo', role: 'alert' });
  const guardar = async () => {
    error.textContent = '';
    if (c1.value.length < 10) { error.textContent = 'Al menos 10 caracteres.'; c1.focus(); return; }
    if (c1.value !== c2.value) { error.textContent = 'Las contraseñas no coinciden.'; c2.focus(); return; }
    try {
      await api(`/plataforma/usuarios/${enc(u.id)}/clave-temporal`, { metodo: 'POST', json: { clave: c1.value } });
      c1.value = ''; c2.value = '';
      dlg.close();
      notificar(`Contraseña temporal fijada para ${u.email}. Deberá cambiarla al entrar.`, 'ok');
      alTerminar();
    } catch (err) { if (err.estado !== 401) error.textContent = err.message; }
  };
  const form = h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); guardar(); } },
    h('p', null, `Se cerrarán las sesiones abiertas de ${u.email} y, al entrar con esta contraseña, tendrá que elegir una nueva. `
      + 'Comuníquesela por un canal de confianza; la aplicación no la muestra ni la guarda en claro.'),
    alerta('warn', 'Quien conozca esta contraseña podrá entrar como esa persona y en sus empresas. Úsela solo a petición suya: '
      + 'el restablecimiento queda anotado en la actividad de cada una de sus empresas y se le muestra en «Mi cuenta».'),
    campo('Contraseña temporal', c1), campo('Repítala', c2), error);
  const dlg = dialogo({
    titulo: 'Fijar una contraseña temporal', contenido: form,
    acciones: [boton('Cancelar', () => dlg.close()), boton('Fijar contraseña', guardar, { tipo: 'peligro' })],
  });
  c1.focus();
}

function tarjetaUsuariosMU(usuarios, recargar) {
  const filtro = entrada({ type: 'search', placeholder: 'Nombre o correo…' });
  const cont = h('div');
  const yoId = app.usuario && app.usuario.id;
  const estadoDe = (u) => [
    u.activo ? pill('Activa', 'ok') : pill('Desactivada', 'danger'),
    u.superadmin ? pill('Operador', 'info') : null,
    u.bloqueado ? pill('Bloqueada temporalmente', 'warn') : null,
    u.cambiar_clave ? pill('Debe cambiar la contraseña', 'warn') : null,
  ];
  function pintar() {
    const t = filtro.value.trim().toLowerCase();
    const lista = t ? usuarios.filter((u) => `${u.nombre} ${u.email}`.toLowerCase().includes(t)) : usuarios;
    vaciar(cont).append(tabla([
      { titulo: 'Nombre', celda: (u) => u.nombre || '—' },
      { titulo: 'Correo', celda: (u) => u.email },
      { titulo: 'Alta', celda: (u) => [fmtDia(u.creado_en), h('span', { class: 'muted bloque' }, ORIGEN_ALTA[u.alta_origen] || '')] },
      { titulo: 'Empresas', clase: 'num', celda: (u) => String(u.empresas ?? 0) },
      { titulo: 'Estado', celda: estadoDe },
      {
        titulo: 'Acciones',
        celda: (u) => (u.id === yoId ? h('span', { class: 'muted' }, 'Su cuenta') : h('div', { class: 'acciones' },
          boton(u.activo ? 'Desactivar' : 'Activar', async (ev) => {
            const btn = ev.currentTarget;
            if (u.activo) {
              const ok = await confirmar({
                titulo: 'Desactivar la cuenta', texto: `${u.email} no podrá entrar y se cerrarán sus sesiones. Sus empresas y facturas no se modifican.`,
                textoBoton: 'Desactivar', peligro: true,
              });
              if (!ok) return;
            }
            await conEspera(btn, async () => {
              try {
                await api(`/plataforma/usuarios/${enc(u.id)}`, { metodo: 'PUT', json: { activo: !u.activo } });
                notificar(u.activo ? 'Cuenta desactivada.' : 'Cuenta activada.', 'ok');
                recargar();
              } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
            });
          }, { pequeno: true, tipo: u.activo ? 'peligro' : 'secundario' }),
          boton('Contraseña temporal', () => dialogoClaveTemporalMU(u, recargar), { pequeno: true }))),
      },
    ], lista, { vacio: 'No hay usuarios que coincidan.', leyenda: 'Usuarios de la plataforma' }));
  }
  filtro.addEventListener('input', pintar);
  pintar();
  return tarjeta('Usuarios',
    h('p', { class: 'ayuda' }, 'Se muestra cuántas empresas tiene cada persona, no cuáles ni su contenido.'),
    h('div', { class: 'filtros' }, campo('Buscar', filtro)), cont);
}

function tarjetaRegistroMU(p, recargar) {
  const abierto = !!(p.ajustes && p.ajustes.registro_abierto);
  const btn = boton(abierto ? 'Cerrar el registro' : 'Abrir el registro', async () => {
    const ok = await confirmar({
      titulo: abierto ? 'Cerrar el registro' : 'Abrir el registro',
      texto: abierto ? 'Solo podrán crear su cuenta las personas invitadas. Las cuentas existentes no se ven afectadas.'
        : 'Cualquier persona que llegue a la aplicación podrá crear su cuenta y dar de alta sus empresas. ¿Desea abrirlo?',
      textoBoton: abierto ? 'Cerrar el registro' : 'Abrir el registro', peligro: !abierto,
    });
    if (!ok) return;
    await conEspera(btn, async () => {
      try {
        await api('/plataforma/ajustes', { metodo: 'PUT', json: { registro_abierto: !abierto } });
        app.estado.registro_abierto = !abierto;
        notificar(abierto ? 'Registro cerrado.' : 'Registro abierto.', 'ok');
        recargar();
      } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
    });
  }, { tipo: abierto ? 'secundario' : 'primario' });
  return tarjeta('Registro de cuentas',
    abierto ? alerta('warn', h('strong', null, 'Registro abierto: '), 'cualquiera puede crear una cuenta (con límites por dirección y por hora).')
      : alerta('ok', h('strong', null, 'Registro cerrado: '), 'solo pueden crear su cuenta las personas invitadas.'),
    h('div', { class: 'acciones' }, btn),
    abierto ? null : ajusteInvitacionesEmpresaMU(p, recargar));
}

// --- multiusuario (revisión) ---
/** Con el registro cerrado: ¿pueden las invitaciones de los administradores de empresas crear cuentas nuevas? */
function ajusteInvitacionesEmpresaMU(p, recargar) {
  const activo = !!(p.ajustes && p.ajustes.invitaciones_empresa_crean_cuentas);
  const btn = boton(activo ? 'No permitirlo' : 'Permitirlo', async () => {
    const ok = await confirmar({
      titulo: activo ? 'Solo cuentas existentes' : 'Permitir cuentas nuevas por invitación de empresa',
      texto: activo ? 'Las invitaciones de los administradores de empresas solo podrá aceptarlas quien ya tenga cuenta.'
        : 'Cualquier usuario que dé de alta una empresa podrá crear cuentas nuevas invitando a otros correos (con cupos diarios). ¿Desea permitirlo?',
      textoBoton: activo ? 'No permitirlo' : 'Permitirlo', peligro: !activo,
    });
    if (!ok) return;
    await conEspera(btn, async () => {
      try {
        await api('/plataforma/ajustes', { metodo: 'PUT', json: { invitaciones_empresa_crean_cuentas: !activo } });
        notificar('Ajuste guardado.', 'ok');
        recargar();
      } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
    });
  }, { tipo: 'secundario', pequeno: true });
  const c = p.cupos || {};
  return [
    h('h3', null, 'Invitaciones de las empresas'),
    h('p', null, activo
      ? 'Permitido: las invitaciones de los administradores de empresas también crean cuentas nuevas.'
      : 'Solo cuentas existentes: las invitaciones de los administradores de empresas no crean cuentas nuevas (las suyas, como operador, sí).'),
    h('p', { class: 'ayuda' }, `Cupos de los usuarios: ${c.invitaciones_pendientes_empresa ?? '—'} invitaciones pendientes por empresa, `
      + `${c.invitaciones_dia_usuario ?? '—'} invitaciones y ${c.empresas_dia_usuario ?? '—'} altas de empresas por persona y día.`),
    h('div', { class: 'acciones' }, btn),
  ];
}

/** Quitar a una persona de la empresa (administradores) o salir de ella (la propia persona). */
function botonQuitarMiembroMU(emp, m, recargar) {
  const propio = app.usuario && String(m.id) === String(app.usuario.id);
  if (!propio && !(String(emp.id) === String(app.empresaId) ? esAdmin() : emp.rol === 'admin')) return '—';
  const nombreEmp = emp.razon_social || 'esta empresa';
  return boton(propio ? 'Salir' : 'Quitar', async (ev) => {
    const btn = ev.currentTarget;
    const ok = await confirmar({
      titulo: propio ? 'Salir de la empresa' : 'Quitar el acceso',
      texto: propio ? `Dejará de tener acceso a ${nombreEmp}. Para volver, un administrador tendrá que invitarle.`
        : `${m.email} dejará de tener acceso a ${nombreEmp} y se anularán sus invitaciones pendientes. Su cuenta y lo que expidió no se borran.`,
      textoBoton: propio ? 'Salir' : 'Quitar', peligro: true,
    });
    if (!ok) return;
    await conEspera(btn, async () => {
      try {
        await api(`/empresas/${enc(emp.id)}/miembros/${enc(m.id)}`, { metodo: 'DELETE' });
        notificar(propio ? `Ha salido de ${nombreEmp}.` : 'Acceso quitado.', 'ok');
        if (propio) { aplicarSesion(await api('/yo')); irA(rutaInicioMU()); return; }
        recargar();
      } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
    });
  }, { pequeno: true, tipo: 'peligro' });
}

function tarjetaInvitacionesPlataformaMU(invs, recargar) {
  const email = entrada({ type: 'email', maxlength: 200, autocomplete: 'off' });
  const dias = entrada({ type: 'number', min: 1, max: 30, step: 1, value: '7' });
  const resultado = h('div', { 'aria-live': 'polite' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-secundario' }, 'Crear invitación');
  const form = h('form', { class: 'form' },
    h('div', { class: 'fila-campos' },
      campo('Correo (opcional)', email, { ayuda: 'Si lo indica, solo ese correo podrá usarla.' }),
      campo('Validez (días)', dias)),
    resultado,
    h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultado);
    await conEspera(enviar, async () => {
      try {
        const inv = await api('/plataforma/invitaciones', { metodo: 'POST', json: { email: email.value.trim() || null, dias: Number(dias.value) || 7 } });
        email.value = '';
        vaciar(resultado).append(cajaEnlaceMU(inv));
      } catch (err) { if (err.estado !== 401) resultado.append(alerta('danger', err.message)); }
    }, 'Creando…');
  });
  return tarjeta('Invitaciones de la plataforma',
    h('p', null, 'Permiten crear una cuenta aunque el registro esté cerrado. No dan acceso a ninguna empresa: '
      + 'las invitaciones a una empresa las crean sus administradores.'),
    form,
    h('h3', null, 'Últimas invitaciones'),
    tablaInvitacionesMU(invs, recargar));
}

function tarjetaSimuladorMU(sim, recargar) {
  const nombreModo = (lista, k) => (lista.find(([x]) => x === k) || [null, k])[1];
  const nif = entrada({ maxlength: 20, class: 'mono', required: true, autocomplete: 'off', placeholder: 'NIF de la empresa' });
  const veces = entrada({ type: 'number', min: 1, max: 20, step: 1, value: '1' });
  const enviar = h('button', { type: 'submit', class: 'btn btn-secundario' }, 'Aplicar modo');
  const form = h('form', { class: 'form' },
    campo('Empresa (NIF)', nif),
    grupoRadios('modo_sim_plataforma', 'Comportamiento del simulador en los próximos envíos de esa empresa', MODOS_SIMULADOR, 'normal'),
    h('div', { class: 'fila-entorno' }, campo('Número de envíos afectados', veces)),
    h('div', { class: 'acciones' }, enviar));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    await conEspera(enviar, async () => {
      try {
        const m = valorRadio(form, 'modo_sim_plataforma');
        await api('/simulador/modo', { metodo: 'POST', json: { nif: nif.value.trim(), modo: m, veces: Math.max(1, Math.min(20, Number(veces.value) || 1)) } });
        notificar(`Simulador: modo «${nombreModo(MODOS_SIMULADOR, m)}» para la empresa ${nif.value.trim()}.`, 'ok');
        recargar();
      } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
    });
  });
  const pendientes = comoLista(sim && sim.empresas);
  return tarjeta('Simulador local (no es la AEAT)',
    h('p', { class: 'ayuda' }, 'El modo de fallo es de cada empresa: solo lo consumen los envíos (o las consultas) de la empresa indicada, '
      + 'y los modos de representación, solo los que llevan representante. Nunca afecta a otra empresa ni a la AEAT. '
      + 'Los administradores de cada empresa también pueden fijarlo para la suya en la pantalla Remisión; esos modos no se listan aquí.'),
    tabla([
      { titulo: 'Empresa (NIF)', celda: (x) => h('span', { class: 'mono' }, x.nif) },
      { titulo: 'Servicio', celda: (x) => (x.servicio === 'consulta' ? 'Consulta' : 'Remisión') },
      { titulo: 'Modo', celda: (x) => nombreModo(x.servicio === 'consulta' ? MODOS_CONSULTA_SIMULADOR : MODOS_SIMULADOR, x.modo) },
      { titulo: 'Quedan', clase: 'num', celda: (x) => String(x.veces ?? '—') },
    ], pendientes, { vacio: 'No hay ningún modo de fallo pendiente fijado desde este panel.', leyenda: 'Modos de fallo pendientes fijados desde este panel' }),
    form);
}

function tarjetaVerificacionNifMU() {
  const nif = entrada({ required: true, maxlength: 20, class: 'mono mayus', spellcheck: 'false', autocomplete: 'off' });
  const nota = entrada({ maxlength: 200, autocomplete: 'off' });
  const resultado = h('div', { 'aria-live': 'polite' });
  const motivos = {
    operador: 'verificado por el operador', alta_por_operador: 'la dio de alta el operador',
    version_anterior: 'empresa de la versión anterior, dada de alta por el operador',
  };
  const mostrar = (r) => vaciar(resultado).append(kv([
    ['Empresa', `${r.razon_social} · NIF ${r.nif}`],
    ['NIF verificado', r.verificado
      ? `Sí: ${motivos[r.motivo] || 'verificado'} (${fmtFechaHora(r.verificado_en)})` : 'No'],
  ]));
  const pedir = (btn, metodo, verificado) => conEspera(btn, async () => {
    vaciar(resultado);
    const valor = nif.value.trim().toUpperCase();
    if (!valor) { resultado.append(alerta('danger', 'Indique el NIF de la empresa.')); nif.focus(); return; }
    try {
      const r = metodo === 'GET'
        ? await api(`/plataforma/empresas/verificacion-nif?nif=${enc(valor)}`)
        : await api('/plataforma/empresas/verificacion-nif', {
          metodo: 'POST', json: { nif: valor, verificado, nota: nota.value.trim() || null },
        });
      mostrar(r);
      if (metodo !== 'GET') notificar(verificado ? 'NIF marcado como verificado.' : 'Verificación retirada.', 'ok');
    } catch (err) { if (err.estado !== 401) resultado.append(alerta('danger', err.message)); }
  });
  const consultar = boton('Consultar', () => pedir(consultar, 'GET'));
  const marcar = boton('Marcar como verificado', () => pedir(marcar, 'POST', true), { tipo: 'primario' });
  const retirar = boton('Retirar la verificación', () => pedir(retirar, 'POST', false), { tipo: 'peligro' });
  return tarjeta('Verificación del NIF de las empresas',
    h('p', null, 'Cualquier usuario puede dar de alta una empresa con un NIF libre y declarar que un tercero (por ejemplo, '
      + 'una gestoría) presenta en su nombre; la aplicación no puede comprobarlo. Por eso, el primer envío a la AEAT (pruebas o '
      + 'producción) con el certificado de un tercero exige que usted verifique el NIF de la empresa. No hace falta si la empresa '
      + 'usa su propio certificado, ni para las empresas que da de alta usted.'),
    alerta('warn', 'Márquelo solo después de comprobar, con documentación, que quien lo pide actúa por esa empresa. Queda anotado en '
      + 'la actividad de la plataforma y en la de la empresa.'),
    h('div', { class: 'form' },
      h('div', { class: 'fila-campos' }, campo('NIF de la empresa', nif),
        campo('Nota (opcional)', nota, { ayuda: 'Por ejemplo, qué documentación ha comprobado.' })),
      resultado,
      h('div', { class: 'acciones' }, consultar, marcar, retirar)));
}

const ACCIONES_ERROR_MU = new Set(['error.interno', 'remision.error', 'diario.error']);
// Lo que cualquier cuenta anota por sí misma (entrar, cambiar su contraseña, registrarse): tabla aparte, para que no
// desplace la auditoría del operador (contraseñas temporales, bajas, ajustes, verificaciones de NIF…)
const ACCIONES_CUENTAS_MU = new Set(['sesion.inicio', 'usuario.clave_cambiada', 'usuario.registro']);

function tablaActividadPlataformaMU(eventos, leyenda, vacio) {
  return tabla([
    { titulo: 'Fecha', celda: (e) => fmtFechaHora(e.en) },
    { titulo: 'Usuario', celda: (e) => e.usuario || 'Sistema' },
    { titulo: 'Acción', celda: (e) => h('code', null, e.accion || '—') },
    { titulo: 'Detalle', celda: (e) => h('span', { class: 'valor-largo' }, detalleActividad(e.detalle) || '—') },
  ], eventos.slice(0, 100), { vacio, leyenda });
}

function tarjetaActividadPlataformaMU(eventos) {
  const lista = comoLista(eventos);
  return tarjeta('Actividad de la plataforma',
    h('p', { class: 'ayuda' }, 'Ajustes, invitaciones, contraseñas temporales, bajas, verificaciones de NIF y avisos de la plataforma; de los errores técnicos, solo su tipo. La actividad de cada empresa solo la ven sus miembros.'),
    tablaActividadPlataformaMU(lista.filter((e) => !ACCIONES_ERROR_MU.has(e.accion) && !ACCIONES_CUENTAS_MU.has(e.accion)),
      'Actividad de la plataforma', 'No hay actividad registrada.'),
    h('h3', null, 'Entradas y altas de las cuentas'),
    h('p', { class: 'ayuda' }, 'Inicios de sesión, cambios de la propia contraseña y altas de cuentas. Se muestran aparte: '
      + 'los que haga cualquier cuenta no desplazan la actividad anterior.'),
    tablaActividadPlataformaMU(lista.filter((e) => ACCIONES_CUENTAS_MU.has(e.accion)), 'Entradas y altas de las cuentas',
      'No hay entradas registradas.'),
    h('h3', null, 'Errores técnicos'),
    h('p', { class: 'ayuda' }, 'Solo la ruta y el tipo de error, agrupados por minuto («veces»; cada minuto aparece cuando ha '
      + 'terminado). Se muestran aparte: los que '
      + 'provoque cualquiera, también sin sesión, no desplazan la actividad anterior.'),
    tablaActividadPlataformaMU(lista.filter((e) => ACCIONES_ERROR_MU.has(e.accion)), 'Errores técnicos',
      'No hay errores técnicos registrados.'));
}

async function paginaPlataforma(ctx) {
  titulo('Plataforma');
  if (!esSuperadmin()) {
    montar(ctx, cabeceraPagina('Plataforma'), alerta('warn', 'Solo el operador de la plataforma puede ver esta página.'));
    return;
  }
  const [p, usuarios, invs, act] = await Promise.all([
    api('/plataforma'), api('/plataforma/usuarios'), api('/plataforma/invitaciones'), api('/plataforma/actividad'),
  ]);
  if (!ctx.vigente()) return;
  const recargar = () => enrutar({ forzar: true });
  const t = p.totales || {};
  montar(ctx,
    cabeceraPagina('Panel de la plataforma', {
      meta: [h('span', null, `${t.usuarios ?? 0} usuarios (${t.usuarios_activos ?? 0} activos) · ${t.empresas ?? 0} empresas`)],
    }),
    alerta('info', 'Como operador de la plataforma (productor del sistema) gestiona las cuentas, el registro, las invitaciones, '
      + 'el productor y el simulador. Por ser operador no tiene acceso a los documentos ni a las facturas de ninguna empresa: solo a '
      + 'las de las que sea miembro. Una contraseña temporal permite entrar como esa persona; por eso queda anotada en la actividad '
      + 'de cada una de sus empresas.'),
    tarjetaRegistroMU(p, recargar),
    tarjetaInvitacionesPlataformaMU(comoLista(invs), recargar),
    tarjetaUsuariosMU(comoLista(usuarios), recargar),
    pasoProductor({ productor: p.productor }, recargar),
    tarjetaVerificacionNifMU(),
    tarjetaSimuladorMU(p.simulador, recargar),
    tarjetaActividadPlataformaMU(act));
}

// --- certificados-representacion ---
/* ======================================================================================
 * Certificados: «Mis certificados» (carga única por usuario y asignación a empresas que
 * administra), certificado en uso por empresa y entorno, representante y legitimación.
 * Ni el archivo ni la contraseña vuelven nunca del servidor.
 * ====================================================================================== */

const ENTORNOS_CERT_C = ['pruebas', 'produccion', 'simulador'];
const METODOS_LEGITIMACION_C = [
  ['apoderamiento_aeat', 'Apoderamiento inscrito en el Registro de apoderamientos de la AEAT'],
  ['colaborador_social', 'Colaborador social (acuerdo de colaboración social con la AEAT)'],
];

function titularCertC(c) {
  const t = (c && (c.titular || (c.legitimacion && c.legitimacion.titular))) || {};
  return h('span', null, t.nombre || '—', t.nif ? h('span', { class: 'mono muted bloque' }, `NIF ${t.nif}`) : null);
}

function origenCertC(c) {
  if (!c || c.origen !== 'mis_certificados') return pill('De la empresa', 'neutral');
  return h('span', null, pill('De «Mis certificados»', 'info'),
    c.propietario ? h('span', { class: 'muted bloque' }, c.es_mio ? 'Suyo' : `De ${c.propietario.nombre || 'otro usuario'}`) : null);
}

function papelCertC(leg) {
  if (!leg) return h('span', { class: 'muted' }, '—');
  if (leg.propio) return pill('Propio de la empresa', 'ok', 'El certificado identifica a la empresa: los envíos no llevan representante.');
  if (leg.representante) {
    return h('span', null, pill('De representante', 'info', 'Certificado de un tercero legitimado: los envíos llevan Cabecera/Representante.'),
      h('span', { class: 'muted bloque' }, `Representante: ${leg.representante.nombre} (NIF ${leg.representante.nif})`));
  }
  return pill('De un tercero sin legitimación', 'danger');
}

function vigenciaC(c) {
  const ahora = new Date().toISOString();
  const vig = c.vigente !== undefined ? c.vigente : (c.valido_desde <= ahora && ahora <= c.valido_hasta);
  return h('span', null, vig ? pill('Vigente', 'ok') : pill('No vigente', 'danger'), h('span', { class: 'muted bloque' }, `Hasta ${fmtDia(c.valido_hasta)}`));
}

/** Página «Mis certificados»: certificados del usuario, empresas donde se usan, asignación, usos y eliminación. */
async function paginaMisCertificados(ctx) {
  titulo('Mis certificados');
  const lista = comoLista(await api('/yo/certificados'), 'certificados');
  if (!ctx.vigente()) return;
  const recargar = () => enrutar({ forzar: true });
  const archivo = h('input', { type: 'file', accept: '.p12,.pfx,application/x-pkcs12', required: true });
  const clave = entrada({ type: 'password', required: true, autocomplete: 'off' });
  const alias = entrada({ maxlength: 80, placeholder: 'p. ej. Sello de la gestoría' });
  const subirBtn = h('button', { type: 'submit', class: 'btn btn-primario' }, 'Cargar en «Mis certificados»');
  const resultado = h('div', { 'aria-live': 'polite' });
  const form = h('form', { class: 'form' },
    campo('Archivo del certificado (.p12 o .pfx)', archivo),
    h('div', { class: 'fila-campos' },
      campo('Contraseña del certificado', clave, { ayuda: 'Se usa para abrir el archivo. Se guarda cifrada en el servidor y nunca se vuelve a mostrar.' }),
      campo('Nombre para reconocerlo (opcional)', alias)),
    h('div', { class: 'acciones' }, subirBtn), resultado);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultado);
    if (!archivo.files || !archivo.files[0]) { notificar('Seleccione el archivo del certificado.', 'danger'); return; }
    const fd = new FormData();
    fd.append('archivo', archivo.files[0], archivo.files[0].name);
    fd.append('clave', clave.value);
    fd.append('alias', alias.value.trim());
    clave.value = '';
    await conEspera(subirBtn, async () => {
      try {
        const r = await api('/yo/certificados', { metodo: 'POST', form: fd });
        archivo.value = '';
        notificar(`Certificado de ${(r.titular && r.titular.nombre) || 'titular sin nombre'} cargado en «Mis certificados».`, 'ok');
        recargar();
      } catch (err) { if (err.estado !== 401) resultado.append(alerta('danger', err.message)); }
    }, 'Cargando…');
  });
  const administradas = app.empresas.filter((x) => x.rol === 'admin');
  const listado = tabla([
    { titulo: 'Certificado', celda: (c) => [h('strong', null, c.alias || (c.titular && c.titular.nombre) || 'Certificado'), h('span', { class: 'muted bloque' }, TIPOS_CERTIFICADO[c.tipo] || c.tipo || '—')] },
    { titulo: 'Titular', celda: titularCertC },
    { titulo: 'Validez', celda: vigenciaC },
    {
      titulo: 'Empresas donde se usa',
      celda: (c) => (c.requiere_recarga
        ? alerta('warn', 'Vuelva a cargar este mismo archivo: se cargó después de la copia de seguridad restaurada y el servidor no lo conserva. Sus asignaciones se mantienen.')
        : (c.asignaciones.length ? h('ul', { class: 'lista-simple' }, c.asignaciones.map((a) => h('li', null,
          `${a.empresa.razon_social} · ${a.empresa.nif} `, badgeEntorno(a.entorno), ' ',
          a.ok ? (a.propio ? pill('Propio', 'ok') : pill('Representante', 'info')) : pill('No utilizable', 'danger', a.detalle),
          a.ok ? null : h('span', { class: 'muted bloque' }, a.detalle),
          boton('Retirar', () => retirarAsignacionC(c, a, recargar), { tipo: 'enlace', pequeno: true }))))
          : h('span', { class: 'muted' }, 'Sin asignar'))),
    },
    {
      titulo: 'Acciones',
      celda: (c) => h('div', { class: 'acciones' },
        boton('Asignar a una empresa', () => asignarDialogoC(c, administradas, recargar), { pequeno: true, disabled: c.requiere_recarga || !c.vigente }),
        boton('Ver usos', () => usosDialogoC(c), { pequeno: true }),
        boton('Eliminar', () => eliminarCertificadoC(c, recargar), { tipo: 'peligro', pequeno: true })),
    },
  ], lista, { vacio: 'Todavía no ha cargado ningún certificado.', leyenda: 'Mis certificados' });
  montar(ctx,
    cabeceraPagina('Mis certificados', { meta: [h('span', null, 'Cárguelos una vez y úselos en las empresas que administra.')] }),
    tarjeta('Cómo funciona',
      h('ul', { class: 'lista-simple' },
        h('li', null, 'Solo usted ve, asigna, retira y elimina sus certificados. Los administradores de cada empresa ven cuál se usa, pero no pueden descargarlo ni usarlo en otra empresa.'),
        h('li', null, 'Si el certificado es de la propia empresa (sello, persona jurídica o su representante legal) es «propio»: los envíos no llevan representante.'),
        h('li', null, 'Si es de un tercero (por ejemplo, de su gestoría), la empresa debe declarar en Configuración → Certificados el apoderamiento en la AEAT o la colaboración social de ese titular. Entonces cada envío lleva Cabecera/Representante y la AEAT comprueba el apoderamiento al recibirlo (error 4112 si no consta).'),
        h('li', null, 'Si deja de ser administrador de una empresa, su certificado deja de usarse en ella. Cada uso queda registrado («Ver usos»).'))),
    tarjeta('Cargar un certificado', form),
    tarjeta('Mis certificados', listado));
}

function asignarDialogoC(cert, administradas, recargar) {
  if (!administradas.length) {
    notificar('Solo puede asignar sus certificados a empresas que administra, y no administra ninguna.', 'warn');
    return;
  }
  const selEmp = selector(administradas.map((x) => [x.id, `${x.razon_social} · ${x.nif}`]), app.empresaId);
  const selEnt = selector(ENTORNOS_CERT_C.map((k) => [k, ENTORNOS[k].corto]), app.entorno === 'produccion' ? 'produccion' : 'pruebas');
  const error = h('p', { class: 'error-campo', role: 'alert' });
  const ok = boton('Asignar', async () => {
    await conEspera(ok, async () => {
      try {
        const r = await api(`/empresas/${enc(selEmp.value)}/certificados/asignar`, { metodo: 'POST', json: { certificado_id: cert.id, entorno: selEnt.value } });
        dlg.close();
        const leg = (r && r.legitimacion) || {};
        notificar(leg.ok ? 'Certificado asignado y listo para usarse.' : `Certificado asignado. ${leg.detalle || ''}`, leg.ok ? 'ok' : 'warn');
        recargar();
      } catch (err) { error.textContent = err.message; }
    }, 'Asignando…');
  }, { tipo: 'primario' });
  const dlg = dialogo({
    titulo: 'Asignar a una empresa',
    contenido: [
      h('p', null, `Certificado de ${(cert.titular && cert.titular.nombre) || '—'} (NIF ${(cert.titular && cert.titular.nif) || '—'}). Pasará a ser el que usa la empresa en ese entorno.`),
      campo('Empresa (de las que administra)', selEmp),
      campo('Entorno', selEnt, { ayuda: 'En el simulador local no hay conexión con la AEAT: solo sirve para probar la representación (Cabecera/Representante).' }),
      error],
    acciones: [boton('Cancelar', () => dlg.close()), ok],
  });
}

async function retirarAsignacionC(cert, a, recargar) {
  const ok = await confirmar({
    titulo: 'Retirar el certificado de la empresa',
    texto: `${a.empresa.razon_social} dejará de usar este certificado en ${ENTORNOS[a.entorno] ? ENTORNOS[a.entorno].corto : a.entorno}. Los registros pendientes no se enviarán hasta que la empresa elija otro.`,
    textoBoton: 'Retirar',
  });
  if (!ok) return;
  try {
    await api(`/yo/certificados/${enc(cert.id)}/asignaciones/${enc(a.asignacion_id)}`, { metodo: 'DELETE' });
    notificar('Asignación retirada.', 'ok');
    recargar();
  } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
}

async function eliminarCertificadoC(cert, recargar) {
  const n = (cert.asignaciones || []).length;
  const ok = await confirmar({
    titulo: 'Eliminar el certificado',
    texto: `Se borrará del servidor el archivo cifrado${n ? ` y dejará de usarse en ${n} asignación(es)` : ''}. Esta acción no se puede deshacer: para volver a usarlo tendrá que cargarlo de nuevo.`,
    textoBoton: 'Eliminar', peligro: true,
  });
  if (!ok) return;
  try {
    await api(`/yo/certificados/${enc(cert.id)}`, { metodo: 'DELETE' });
    notificar('Certificado eliminado.', 'ok');
    recargar();
  } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
}

async function usosDialogoC(cert) {
  const cont = h('div', null, cargando());
  const dlg = dialogo({ titulo: 'Usos del certificado', contenido: cont, acciones: [boton('Cerrar', () => dlg.close())] });
  try {
    const usos = comoLista(await api(`/yo/certificados/${enc(cert.id)}/usos`));
    const texto = {
      'certificado.uso': 'Envío', 'certificado.asignado': 'Asignado', 'certificado.asignacion_retirada': 'Asignación retirada',
      'certificado_usuario.cargado': 'Cargado', 'certificado_usuario.recargado': 'Cargado de nuevo',
    };
    const que = (u) => {
      if (u.accion === 'certificado.uso') {
        try { if (JSON.parse(u.detalle || '{}').operacion === 'consulta') return 'Consulta a la AEAT'; } catch (e) { /* detalle no JSON */ }
      }
      return texto[u.accion] || humanizar(u.accion);
    };
    vaciar(cont).append(tabla([
      { titulo: 'Fecha', celda: (u) => fmtFechaHora(u.en) },
      { titulo: 'Empresa', celda: (u) => (u.empresa_razon ? `${u.empresa_razon} · ${u.empresa_nif}` : '—') },
      { titulo: 'Qué', celda: que },
      { titulo: 'Detalle', celda: (u) => h('span', { class: 'valor-largo' }, detalleActividad(u.detalle) || '—') },
    ], usos, { vacio: 'Todavía no se ha usado.', leyenda: 'Usos del certificado' }));
  } catch (err) { vaciar(cont).append(cajaError(err)); }
}

/** Configuración → Certificados: qué certificado se usará en cada entorno, de dónde viene y en calidad de qué. */
function tarjetaCertificadoEnUsoC(certs, recargar) {
  const cont = h('div', null, cargando());
  const eid = app.empresaId;
  api(`/empresas/${enc(eid)}/certificados/seleccion`).then((sel) => {
    if (String(eid) !== String(app.empresaId)) return;
    vaciar(cont);
    for (const ent of ENTORNOS_CERT_C) {
      const c = sel && sel[ent];
      const inactivos = (certs || []).filter((x) => x.origen === 'empresa' && x.entorno === ent && !x.en_uso && x.legitimacion && x.legitimacion.vigente);
      if (!c && ent === 'simulador') continue;
      const noUtilizable = c && c.legitimacion && c.legitimacion.utilizable === false;
      cont.append(h('div', { class: 'card-seccion' },
        h('h3', null, badgeEntorno(ent)),
        c ? kv([
          ['Origen', origenCertC(c)],
          ['Titular', titularCertC(c)],
          ['Validez', vigenciaC(c)],
          ['Actúa como', papelCertC(c.legitimacion)],
          ['Estado', resumenLegitimacion(c.legitimacion)],
        ]) : h('p', { class: 'muted' }, ent === 'simulador' ? '' : 'Sin certificado: cargue uno para la empresa o asigne uno de «Mis certificados».'),
        ent === 'simulador' && noUtilizable ? alerta('info', 'Mientras no pueda usarse, el simulador envía sin certificado (como la propia empresa, sin representante).') : null,
        c && c.origen === 'mis_certificados' && esAdmin() ? h('div', { class: 'acciones' },
          boton('Dejar de usarlo en esta empresa', () => dejarDeUsarC(eid, c, ent, recargar), { pequeno: true, tipo: noUtilizable ? 'primario' : undefined })) : null,
        inactivos.length && esAdmin() ? h('div', { class: 'acciones' }, h('span', { class: 'muted' }, 'Volver a usar uno cargado para la empresa:'),
          inactivos.map((x) => boton(`${(x.titular && x.titular.nombre) || x.titular_nif || 'Certificado'} (hasta ${fmtDia(x.valido_hasta)})`, async (ev) => {
            await conEspera(ev.currentTarget, async () => {
              try {
                await api(`/empresas/${enc(eid)}/certificados/${enc(x.id)}/activar`, { metodo: 'POST', json: {} });
                notificar('Certificado de la empresa de nuevo en uso.', 'ok');
                recargar();
              } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
            });
          }, { pequeno: true }))) : null));
    }
  }).catch((err) => { vaciar(cont).append(cajaError(err)); });
  return tarjeta('Certificado que se usará',
    h('p', null, 'Cada entorno usa un solo certificado: el cargado para la empresa o uno de «Mis certificados» de un administrador. '
      + 'Si es de un tercero, los envíos llevan Cabecera/Representante y hace falta una legitimación declarada para su titular.'),
    cont);
}

/** Un administrador de la empresa retira la asignación de «Mis certificados» en uso (también si es de otro usuario). */
async function dejarDeUsarC(eid, c, ent, recargar) {
  const de = c.es_mio ? 'su certificado' : `el certificado de ${(c.propietario && c.propietario.nombre) || 'otro usuario'}`;
  const ok = await confirmar({
    titulo: 'Dejar de usar el certificado',
    texto: `La empresa dejará de usar ${de} en ${ENTORNOS[ent] ? ENTORNOS[ent].corto : ent}. El certificado sigue siendo de su propietario (lo verá en «Ver usos»). `
      + (ent === 'simulador' ? 'El simulador enviará sin certificado.' : 'Los registros pendientes no se enviarán hasta elegir otro certificado.'),
    textoBoton: 'Dejar de usar',
  });
  if (!ok) return;
  try {
    await api(`/empresas/${enc(eid)}/certificados/asignaciones/${enc(c.asignacion_id)}`, { metodo: 'DELETE' });
    notificar('La empresa ya no usa ese certificado.', 'ok');
    recargar();
  } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
}

/** Configuración → Certificados: usar uno de «Mis certificados» en esta empresa. */
function tarjetaMisCertificadosEmpresaC(recargar) {
  const dis = !esAdmin();
  const selCert = selector([['', 'Cargando…']], '', { disabled: true });
  const selEnt = selector(ENTORNOS_CERT_C.map((k) => [k, ENTORNOS[k].corto]), app.entorno === 'produccion' ? 'produccion' : 'pruebas', { disabled: dis });
  const enviar = h('button', { type: 'submit', class: 'btn btn-secundario', disabled: true }, 'Usar en esta empresa');
  const resultado = h('div', { 'aria-live': 'polite' });
  api('/yo/certificados').then((r) => {
    const mios = comoLista(r, 'certificados').filter((c) => c.vigente && !c.requiere_recarga);
    vaciar(selCert).append(...(mios.length
      ? mios.map((c) => h('option', { value: c.id }, `${c.alias || (c.titular && c.titular.nombre) || 'Certificado'} · NIF ${(c.titular && c.titular.nif) || '—'}`))
      : [h('option', { value: '' }, 'No tiene certificados vigentes en «Mis certificados»')]));
    selCert.disabled = dis || !mios.length;
    enviar.disabled = dis || !mios.length;
  }).catch(() => { vaciar(selCert).append(h('option', { value: '' }, '(no se pudieron cargar)')); });
  const form = h('form', { class: 'form' },
    h('div', { class: 'fila-campos' }, campo('Certificado', selCert), campo('Entorno', selEnt)),
    h('div', { class: 'acciones' }, enviar, enlace('Gestionar «Mis certificados»', '#/mis-certificados', { tipo: 'enlace' })), resultado);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultado);
    if (!selCert.value) return;
    await conEspera(enviar, async () => {
      try {
        const r = await api(`/empresas/${enc(app.empresaId)}/certificados/asignar`, { metodo: 'POST', json: { certificado_id: Number(selCert.value), entorno: selEnt.value } });
        notificar('Certificado asignado.', 'ok');
        resultado.append(alerta(r.legitimacion && r.legitimacion.ok ? 'ok' : 'warn', papelCertC(r.legitimacion), ' ', (r.legitimacion && r.legitimacion.detalle) || ''));
        window.setTimeout(recargar, 1200);
      } catch (err) { if (err.estado !== 401) resultado.append(alerta('danger', err.message)); }
    }, 'Asignando…');
  });
  return tarjeta('Usar uno de «Mis certificados»',
    h('p', null, 'Si ya cargó su certificado (o el de su gestoría) en «Mis certificados», puede usarlo aquí sin volver a subirlo. Solo usted podrá retirarlo.'),
    dis ? alerta('info', 'Solo un administrador de la empresa puede elegir el certificado.') : null, form);
}

/** Configuración → Certificados: legitimación de un tercero, por titular del certificado, con evidencia. */
function tarjetaLegitimacionC(recargar) {
  const dis = !esAdmin();
  const eid = app.empresaId;
  const metodo = selector(METODOS_LEGITIMACION_C, 'apoderamiento_aeat', { disabled: dis });
  const nif = entrada({ maxlength: 20, class: 'mono', spellcheck: 'false', required: true, disabled: dis });
  const nombre = entrada({ maxlength: 120, required: true, disabled: dis });
  const evidencia = selector([['', 'Cargando documentos…']], '', { disabled: true });
  const notas = h('textarea', { rows: 2, maxlength: 500, disabled: dis });
  const enviar = h('button', { type: 'submit', class: 'btn btn-secundario', disabled: dis }, 'Declarar legitimación');
  const resultado = h('div', { 'aria-live': 'polite' });
  const listado = h('div', null, cargando());
  api(`/empresas/${enc(eid)}/documentos`).then((docs) => {
    const lista = comoLista(docs, 'documentos');
    vaciar(evidencia).append(h('option', { value: '' }, lista.length ? '(elija el documento)' : '(no hay documentos importados)'),
      ...lista.map((d) => h('option', { value: d.id }, `${d.nombre_archivo || `Documento ${d.id}`} · ${fmtFechaHora(d.subido_en)}`)));
    evidencia.disabled = dis;
  }).catch(() => { vaciar(evidencia).append(h('option', { value: '' }, '(no se pudieron cargar los documentos)')); });
  // Propone el titular del certificado de un tercero que esté en uso
  api(`/empresas/${enc(eid)}/certificados/seleccion`).then((sel) => {
    const tercero = ENTORNOS_CERT_C.map((k) => sel && sel[k]).find((c) => c && c.legitimacion && c.legitimacion.requiere_legitimacion);
    if (tercero && !nif.value) {
      nif.value = (tercero.legitimacion.titular && tercero.legitimacion.titular.nif) || '';
      nombre.value = (tercero.legitimacion.titular && tercero.legitimacion.titular.nombre) || '';
    }
  }).catch(() => { /* sin propuesta */ });
  const pintarLista = () => api(`/empresas/${enc(eid)}/legitimaciones`).then((r) => {
    vaciar(listado).append(tabla([
      { titulo: 'Titular', celda: (l) => [l.titular_nombre || '—', h('span', { class: 'mono muted bloque' }, l.titular_nif ? `NIF ${l.titular_nif}` : 'sin titular (no cuenta)')] },
      { titulo: 'Método', celda: (l) => ({ apoderamiento_aeat: 'Apoderamiento en la AEAT', colaborador_social: 'Colaboración social' })[l.metodo] || humanizar(l.metodo) },
      { titulo: 'Evidencia', celda: (l) => l.evidencia || h('span', { class: 'muted' }, 'sin documento (no cuenta)') },
      { titulo: 'Declarada', celda: (l) => `${fmtFechaHora(l.declarado_en)}${l.declarado_por ? ` · ${l.declarado_por}` : ''}` },
      {
        titulo: 'Estado',
        celda: (l) => (l.revocada_en ? pill(`Revocada ${fmtDia(l.revocada_en)}`, 'neutral')
          : [pill('Vigente', 'ok'), dis ? null : boton('Revocar', async (ev) => {
            const ok = await confirmar({ titulo: 'Revocar la legitimación', texto: `El certificado de ${l.titular_nombre || l.titular_nif} dejará de poder usarse para esta empresa.`, textoBoton: 'Revocar', peligro: true });
            if (!ok) return;
            try {
              await api(`/empresas/${enc(eid)}/legitimaciones/${enc(l.id)}`, { metodo: 'DELETE' });
              notificar('Legitimación revocada.', 'ok');
              recargar();
            } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
          }, { tipo: 'enlace', pequeno: true })]),
      },
    ], comoLista(r), { vacio: 'No hay legitimaciones declaradas.', leyenda: 'Legitimaciones' }));
  }).catch((err) => { vaciar(listado).append(cajaError(err)); });
  pintarLista();
  const form = h('form', { class: 'form' },
    campo('Método', metodo),
    h('div', { class: 'fila-campos' },
      campo('NIF del titular del certificado', nif, { ayuda: 'Quien presenta: la entidad en los sellos y certificados de representante de persona jurídica; la persona en los de persona física.' }),
      campo('Nombre o razón social del titular', nombre, { ayuda: 'Irá en Cabecera/Representante de cada envío.' })),
    campo('Documento que lo acredita', evidencia, { ayuda: 'Importe antes el PDF del apoderamiento o del acuerdo en «Documentos» (clasifíquelo como «Desconocido / no procesar»).' }),
    campo('Notas (opcional)', notas),
    h('div', { class: 'acciones' }, enviar), resultado);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    vaciar(resultado);
    await conEspera(enviar, async () => {
      try {
        await api(`/empresas/${enc(eid)}/legitimacion`, {
          metodo: 'POST',
          json: {
            metodo: metodo.value, titular_nif: nif.value.trim(), titular_nombre: nombre.value.trim(),
            evidencia_documento_id: evidencia.value ? Number(evidencia.value) : null, notas: notas.value.trim(),
          },
        });
        notificar('Legitimación declarada. La AEAT la comprobará en cada envío.', 'ok');
        recargar();
      } catch (err) { if (err.estado !== 401) resultado.append(alerta('danger', err.message)); }
    }, 'Guardando…');
  });
  return tarjeta('Legitimación de un tercero (representante)',
    h('p', null, 'Si el certificado no es de la empresa ni de su representante legal (por ejemplo, el de su gestoría), solo puede usarse si su titular '
      + 'está apoderado ante la AEAT o es colaborador social. Declárelo para ese titular aportando la evidencia: los envíos llevarán Cabecera/Representante.'),
    form, h('h3', null, 'Legitimaciones declaradas'), listado);
}

// --- exentas ---
/* ======================================================================================
 * Operaciones exentas (E1–E6) en el desglose del borrador
 * Cada fila del desglose es sujeta a IVA (S1, con tipo y cuota) o exenta (causa E1–E6, solo la base). La validación del
 * servidor (borradores.validar) pide confirmar la causa y exige la mención legal que se imprime en la factura.
 * ====================================================================================== */

const CAUSAS_EXENCION_UI = {
  E1: { nombre: 'art. 20 Ley 37/1992 (operaciones interiores: alquiler de vivienda, enseñanza, sanidad, seguros, servicios financieros…)', clave: '01', mencion: 'Operación exenta de IVA (art. 20 Ley 37/1992)' },
  E2: { nombre: 'art. 21 (exportaciones)', clave: '02', mencion: 'Operación exenta de IVA (art. 21 Ley 37/1992)' },
  E3: { nombre: 'art. 22 (operaciones asimiladas a las exportaciones)', clave: '02', mencion: 'Operación exenta de IVA (art. 22 Ley 37/1992)' },
  E4: { nombre: 'arts. 23 y 24 (zonas francas, depósitos y regímenes aduaneros o fiscales)', clave: '01', mencion: 'Operación exenta de IVA (arts. 23 y 24 Ley 37/1992)' },
  E5: { nombre: 'art. 25 (entregas intracomunitarias): no soportada, exige un destinatario extranjero', clave: '01', mencion: 'Operación exenta de IVA (art. 25 Ley 37/1992)' },
  E6: { nombre: 'otras causas', clave: '01', mencion: 'Operación exenta de IVA' },
};
const COLUMNAS_DESGLOSE_EXENTA = { calificacion: 'calificación', operacion_exenta: 'causa de exención', motivo_exencion: 'mención de la exención' };
const AYUDA_DESGLOSE_EXENTAS = 'Cada fila es sujeta a IVA (S1, régimen general, tipos del 21 %, 10 % o 4 %) o exenta (causa E1–E6: solo la base, sin tipo ni cuota). '
  + 'La mención de las exentas se imprime en la factura (art. 6.1.j RD 1619/2012): cite el precepto (p. ej. «art. 20.Uno.23º Ley 37/1992») o indique que la operación está exenta. '
  + 'La inversión del sujeto pasivo, las operaciones no sujetas y el recargo de equivalencia no están soportados.';

function claseFilaDesglose(f) {
  if (!f || typeof f !== 'object') return 'S1';
  if (f.operacion_exenta) return 'exenta';
  if (f.calificacion) return String(f.calificacion);
  const vacio = (v) => v === null || v === undefined || v === '';
  return vacio(f.tipo_iva) && vacio(f.cuota) ? 'sin_calificar' : 'S1';
}

/** Tipo y cuota de una base exenta: no se editan (la fila no lleva IVA). Devuelve null para las demás celdas. */
function celdaSinIvaDesglose(f, col) {
  if (!['tipo_iva', 'cuota'].includes(col) || claseFilaDesglose(f) !== 'exenta') return null;
  return h('td', { class: 'num muted', title: 'Base exenta: sin tipo ni cuota de IVA' }, 'Exenta');
}

/** Calificación de la fila: sujeta (S1) o exenta con su causa; en las exentas, además, la mención legal (editable). */
function celdaCalificacionDesglose(f, i, ctx) {
  const clase = claseFilaDesglose(f);
  const valor = clase === 'exenta' ? `E:${f.operacion_exenta}` : (clase === 'sin_calificar' ? '' : clase);
  const opciones = [['', '— Elija: sujeta a IVA o exenta —'], ['S1', 'Sujeta a IVA (S1, régimen general)']];
  for (const [k, v] of Object.entries(CAUSAS_EXENCION_UI)) opciones.push([`E:${k}`, `Exenta — ${k}: ${v.nombre}`]);
  if (!opciones.some(([v]) => v === valor)) opciones.push([valor, `${valor.replace(/^E:/, '')} (no soportada)`]);
  const sel = selector(opciones, valor, { 'aria-label': `Fila ${i + 1} del desglose: sujeta o exenta`, disabled: !ctx.editable, class: 'sel-calificacion' });
  sel.addEventListener('change', () => {
    const elegido = sel.value;
    sel.value = valor;   // el cambio se aplica al guardarlo (se pide el motivo); si se cancela, queda como estaba
    const lista = ctx.lista();
    const fila = { ...(lista[i] && typeof lista[i] === 'object' ? lista[i] : {}) };
    let texto;
    if (elegido === 'S1') {
      Object.assign(fila, { calificacion: 'S1', operacion_exenta: null, motivo_exencion: null, clave_regimen: '01', impuesto: '01' });
      texto = `Fila ${i + 1} del desglose: sujeta a IVA`;
    } else if (elegido.startsWith('E:') && CAUSAS_EXENCION_UI[elegido.slice(2)]) {
      const causa = elegido.slice(2);
      const info = CAUSAS_EXENCION_UI[causa];
      Object.assign(fila, { calificacion: null, operacion_exenta: causa, tipo_iva: null, cuota: null, clave_regimen: info.clave,
        impuesto: '01', motivo_exencion: info.mencion });
      texto = `Fila ${i + 1} del desglose: exenta (${causa})`;
    } else return;
    lista[i] = fila;
    ctx.cambiar(lista, texto);
  });
  const info = clase === 'exenta' ? CAUSAS_EXENCION_UI[f.operacion_exenta] : null;
  let nota;
  if (clase === 'exenta') {
    const clave = f.clave_regimen || (info ? info.clave : '01');
    nota = h('span', { class: 'muted bloque' }, `Exenta · clave de régimen ${clave}${clave === '02' ? ' (exportación)' : ''}`);
  } else if (clase === 'sin_calificar') {
    nota = h('span', { class: 'muted bloque' }, 'Sin IVA: elija la causa de exención o, si está sujeta, indique el tipo y la cuota.');
  } else {
    nota = h('span', { class: 'muted bloque' }, `${clase} · ${f.clave_regimen || '01'}${clase === 'S1' ? ' · sujeta, régimen general' : ''}`);
  }
  if (clase !== 'exenta') return h('td', { class: 'celda-calificacion' }, sel, nota);
  // La mención se edita como las demás celdas: cambio pendiente con motivo y queda en el historial
  const td = ctx.celda(`desglose.${i}.motivo_exencion`, 'area', `Fila ${i + 1}: mención de la exención`, ctx.cambios);
  const area = td.querySelector('textarea');
  if (area) area.maxLength = 500;
  td.classList.add('celda-calificacion');
  td.prepend(sel, nota, h('span', { class: 'muted bloque' }, 'Mención en la factura:'));
  return td;
}

// --- series-consulta ---
/* ======================================================================================
 * Series con ceros de relleno ({n:3}) y consulta a la AEAT (ConsultaFactuSistemaFacturacion)
 * ====================================================================================== */

const AYUDA_PATRON = '{n} = número correlativo; {n:3} = número con ceros de relleno hasta 3 cifras (también {nnn}); '
  + '{aa} = año con 2 cifras; {aaaa} = año con 4 cifras. Ejemplos: {n}/{aa} → 166/26; {n:3}/A{aa} → 042/A26; '
  + 'A{n:3}/{aa} → A057/26; PRB-{n} → PRB-1.';
const RE_MARCADOR_PATRON = /\{(n(?::(\d{1,2}))?|n{2,12}|aa|aaaa)\}/g;
const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
const ESTADO_AEAT = {
  Correcto: ['Correcto', 'ok'],
  AceptadoConErrores: ['Aceptado con errores', 'warn'],
  Anulado: ['Anulado', 'danger'],
};
const MODOS_CONSULTA_SIMULADOR = [
  ['normal', 'Normal', 'Devuelve los registros que tiene el simulador, paginados.'],
  ['sin_respuesta', 'Sin respuesta', 'Procesa la consulta pero no responde (tiempo agotado).'],
  ['caido', 'Servicio caído', 'No acepta la conexión.'],
  ['error_500', 'Error 500', 'Responde con un error interno del servidor (HTTP 500).'],
  ['fault_servidor', 'Fallo del servicio (SOAP Server)', 'Responde con un SOAP Fault de servidor.'],
  ['rechazo', 'Rechazo de la consulta', 'Responde con un SOAP Fault de cliente (4140: sin apoderamiento para consultar).'],
  ['respuesta_invalida', 'Respuesta fuera de esquema', 'Responde sin IndicadorPaginacion: se muestra con un aviso y no permite conciliar.'],
  ['solo_como_representante', 'Tercero sin apoderamiento',
    'Rechaza con 4140 la consulta de un tercero sin IndicadorRepresentante; la aplicación la repite con el indicador y el resultado es parcial.'],
];

/** Número que corresponde a un patrón ({n}, {n:3}, {nnn}, {aa}, {aaaa}); mismo criterio que series.formatear. */
function formatearPatron(patron, numero, anio) {
  return String(patron || '').replace(RE_MARCADOR_PATRON, (m, g, w) => {
    if (g === 'aaaa') return String(anio).padStart(4, '0');
    if (g === 'aa') return String(anio % 100).padStart(2, '0');
    const ancho = w ? Number(w) : (g.length > 1 ? g.length : 0);
    return ancho ? String(numero).padStart(ancho, '0') : String(numero);
  });
}

/** Exactamente un marcador de número. */
function patronValido(patron) {
  const marcas = String(patron || '').match(/\{(n(?::\d{1,2})?|n{2,12})\}/g);
  return !!marcas && marcas.length === 1;
}

/** Patrón, siguiente número y año propuestos (enlace desde un borrador: #/configuracion/series?patron=…&siguiente=…&anio=…). */
function parametrosAltaSerie() {
  const { query } = analizarHash();
  const patron = query.get('patron');
  const siguiente = query.get('siguiente');
  const anio = query.get('anio');
  return {
    patron: patron && patronValido(patron) ? patron.slice(0, 60) : null,
    siguiente: siguiente && /^\d{1,12}$/.test(siguiente) && Number(siguiente) >= 1 ? siguiente : null,
    anio: anio && /^\d{4}$/.test(anio) ? anio : null,
  };
}

/** En el borrador: sin serie confirmada, se muestra el patrón propuesto a partir del número detectado. */
function avisoSinSerie(prop) {
  const texto = 'No se ha identificado una serie confirmada para este número. ';
  if (!prop || !prop.patron) return h('p', { class: 'muted' }, texto, h('a', { href: '#/configuracion/series' }, 'Configurar series'));
  const qs = new URLSearchParams({ patron: prop.patron, siguiente: String(prop.numero || 1) });
  if (prop.anio) qs.set('anio', String(prop.anio));
  return h('p', { class: 'muted' }, texto, 'Patrón propuesto: ', h('code', null, prop.patron),
    prop.ancho ? ` (número con ${prop.ancho} cifras y ceros de relleno)` : '', '. ',
    h('a', { href: `#/configuracion/series?${qs}` }, 'Crear la serie con este patrón'));
}

/**
 * Reanudar la cadena desde el último registro que tiene la AEAT (tras perder la base de datos y el diario y restaurar una
 * copia antigua). El servidor vuelve a consultar y solo acepta si el último registro sigue siendo el revisado (huella).
 */
function dialogoReanudarCadena(r, alTerminar) {
  const c = r.cadena_atrasada;
  const sv = nombresServicio(r.entorno);
  const serv = sv.donde;
  const chk = h('input', { type: 'checkbox', id: uid('chk') });
  const error = h('div', { 'aria-live': 'assertive' });
  const hacer = boton('Reanudar la cadena', reanudar, { tipo: 'peligro', disabled: true });
  chk.addEventListener('change', () => { hacer.disabled = !chk.checked; });
  async function reanudar() {
    vaciar(error);
    await conEspera(hacer, async () => {
      try {
        const res = await api(`/empresas/${enc(app.empresaId)}/consulta/reanudar-cadena`,
          { metodo: 'POST', json: { entorno: r.entorno, huella: c.huella, confirmo: true } });
        dlg.close();
        notificar(res.reanudada
          ? `Cadena reanudada: el próximo registro se encadenará a ${res.ancla ? res.ancla.num_serie : c.num_serie}.`
          : (res.mensaje || 'No había nada que reanudar.'), 'ok');
        if (alTerminar) alTerminar(res);
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Reanudando…');
  }
  const dlg = dialogo({
    titulo: `Reanudar la cadena desde el último registro ${sv.de}`,
    contenido: [
      h('p', null, `Se volverá a consultar ${serv} (todos los meses desde el del último registro local hasta el actual y los de las `
        + 'facturas anteriores, por si se anuló o subsanó alguna) y, si su último registro de esta instalación sigue siendo este, el próximo '
        + 'registro que se genere (expedición, anulación o subsanación) lo usará como registro anterior.'),
      kv([
        ['Factura', h('span', { class: 'mono' }, `${c.num_serie} · ${fmtFecha(c.fecha_expedicion)}`)],
        ['Generado', h('span', { class: 'mono' }, c.fecha_hora_huso)],
        ['Huella', h('span', { class: 'mono valor-largo' }, c.huella)],
      ]),
      alerta('warn', `Avance también el contador de las series cuyos números ya constan en ${serv}. Si estos registros no son de esta `
        + 'instalación (p. ej. de otro programa de facturación de la empresa), no reanude la cadena: consulte con el responsable del sistema.'),
      h('div', { class: 'casilla casilla-destacada' }, chk,
        h('label', { for: chk.id }, `He revisado el último registro ${sv.de} y confirmo que la cadena de esta instalación debe continuar desde él.`)),
      error,
    ],
    acciones: [boton('Cancelar', () => dlg.close()), hacer],
  });
}

/** Números que se saltan (lista completa o, si son muchos, los primeros y los últimos). */
function listaSaltados(sal) {
  const lista = comoLista(sal.lista);
  const mitad = Math.ceil(lista.length / 2);
  const items = sal.completa ? lista : [...lista.slice(0, mitad), null, ...lista.slice(mitad)];
  const cab = sal.cantidad === 1 ? h('p', null, 'Se salta 1 número:')
    : h('p', null, `Se saltan ${sal.cantidad} números, del `, h('code', null, sal.primero), ' al ', h('code', null, sal.ultimo), ':');
  return [cab, h('ul', { class: 'lista-simple' }, items.map((t) => h('li', null, t === null ? '…' : h('code', null, t))))];
}

/**
 * Avanzar el contador de una serie más allá de números ya usados fuera de esta instalación (p. ej. tras perder la base de
 * datos y el diario, la consulta a la AEAT los muestra «solo en la AEAT»). Antes se revisa qué números se saltan; el
 * contador nunca retrocede. Solo administradores; queda en la actividad de la empresa y en el diario de recuperación.
 */
function dialogoAvanceContador(serie, { anio, siguiente, meses, servicio } = {}, alTerminar) {
  const conAnio = /\{(aa|aaaa)\}/.test(String(serie.patron || ''));
  const anioIn = entrada({ type: 'number', min: 2000, max: 2100, step: 1, required: true, value: String(anio || anioMesHoy().anio) });
  const sigIn = entrada({ type: 'number', min: 1, max: 999999999999, step: 1, required: true, value: siguiente ? String(siguiente) : '' });
  const motivo = entrada({ maxlength: 200, placeholder: 'p. ej. números usados en la AEAT tras perder la base de datos' });
  const vista = h('div', { 'aria-live': 'polite' });
  const error = h('div', { 'aria-live': 'assertive' });
  const chk = h('input', { type: 'checkbox', id: uid('chk'), disabled: true });
  const chkTexto = h('label', { for: chk.id }, 'Confirmo que los números que se saltan ya están usados fuera de esta aplicación y que no se expedirán con ella.');
  let previa = null;
  const revisar = h('button', { type: 'submit', class: 'btn btn-secundario btn-pequeno' }, 'Revisar los números que se saltan');
  const avanzar = boton('Avanzar el contador', hacer, { tipo: 'peligro', disabled: true });
  const invalidar = () => { previa = null; chk.checked = false; chk.disabled = true; avanzar.disabled = true; vaciar(vista); };
  anioIn.addEventListener('input', invalidar);
  sigIn.addEventListener('input', invalidar);
  chk.addEventListener('change', () => { avanzar.disabled = !(chk.checked && previa); });

  async function ver() {
    invalidar();
    vaciar(error);
    const a = anioIn.value.trim();
    const n = sigIn.value.trim();
    if (conAnio && !/^\d{4}$/.test(a)) { error.append(alerta('danger', 'Indique el año del contador con 4 cifras.')); anioIn.focus(); return; }
    if (n && !/^\d{1,12}$/.test(n)) { error.append(alerta('danger', 'Indique el nuevo siguiente número (solo cifras).')); sigIn.focus(); return; }
    const q = new URLSearchParams();
    if (conAnio) q.set('anio', a);
    if (n) q.set('siguiente', n);
    await conEspera(revisar, async () => {
      try {
        const r = await api(`/series/${enc(serie.id)}/avance?${q}`);
        if (r.aviso_cadena) vista.append(alerta('warn', h('strong', null, 'Avanzar el contador no reanuda la cadena. '), r.aviso_cadena));
        const filas = [
          ['Contador actual', h('code', null, r.actual_texto)],
          ['Último número expedido aquí', r.ultimo_expedido_texto ? h('code', null, r.ultimo_expedido_texto) : 'Ninguno'],
        ];
        if (r.saltados) filas.push(['Siguiente número tras avanzar', h('code', null, r.siguiente_texto)]);
        vista.append(kv(filas));
        if (!r.saltados) return;
        vista.append(...listaSaltados(r.saltados));
        previa = r;
        chk.disabled = false;
        chkTexto.textContent = r.saltados.cantidad === 1
          ? `Confirmo que el número ${r.saltados.primero} ya está usado fuera de esta aplicación (p. ej. consta en la AEAT) y que no se expedirá con ella.`
          : `Confirmo que los números ${r.saltados.primero} a ${r.saltados.ultimo} ya están usados fuera de esta aplicación (p. ej. constan en la AEAT) y que no se expedirán con ella.`;
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Revisando…');
  }

  async function hacer() {
    vaciar(error);
    if (!previa || !chk.checked) { error.append(alerta('danger', 'Revise los números que se saltan y marque la confirmación.')); return; }
    await conEspera(avanzar, async () => {
      try {
        const cuerpo = { actual: previa.actual, siguiente: previa.siguiente, confirmo: true };
        if (conAnio) cuerpo.anio = previa.anio;
        if (motivo.value.trim()) cuerpo.motivo = motivo.value.trim();
        const r = await api(`/series/${enc(serie.id)}/avanzar`, { metodo: 'POST', json: cuerpo });
        dlg.close();
        notificar(`Contador de la serie ${serie.codigo} avanzado: el siguiente número es ${r.siguiente_texto}.`, 'ok');
        if (alTerminar) alTerminar(r);
      } catch (err) { if (err.estado !== 401) error.append(alerta('danger', err.message)); }
    }, 'Avanzando…');
  }

  const datos = [['Patrón', h('code', null, serie.patron)]];
  if (serie.entorno) datos.push(['Entorno', badgeEntorno(serie.entorno)]);
  const lista = comoLista(meses);
  // Desde la consulta: la propuesta solo cubre los meses comprobados (del consultado al actual), no todo lo que tiene la AEAT
  const avisoMeses = lista.length
    ? alerta('info', h('strong', null, `Meses comprobados: ${lista.join(', ')}. `),
      `Número propuesto: el mayor número de esta serie que ${servicio || 'la AEAT'} tiene en ${lista.length === 1 ? 'ese mes' : 'esos meses'} + 1. `
      + 'Los meses anteriores al consultado no se han mirado.')
    : null;
  const form = h('form', { class: 'form', onsubmit: (e) => { e.preventDefault(); ver(); } },
    h('div', { class: 'fila-campos' },
      conAnio ? campo('Año del contador', anioIn) : null,
      campo('Nuevo siguiente número', sigIn, { ayuda: 'El primer número que se expedirá después: el mayor número de esta serie y año ya '
        + 'usado fuera de esta aplicación + 1. Compruébelo en la consulta a la AEAT del mes completo, que mira también los meses siguientes '
        + 'hasta el actual.' })),
    campo('Motivo (opcional)', motivo),
    h('div', { class: 'acciones' }, revisar));
  const dlg = dialogo({
    titulo: `Avanzar el contador de la serie ${serie.codigo}`,
    contenido: [
      h('p', null, 'Úselo solo si hay números de esta serie ya usados fuera de esta instalación: por ejemplo, tras perder la base de datos '
        + 'y el diario, la consulta a la AEAT muestra números que aquí no constan.'),
      alerta('warn', h('strong', null, 'No se puede deshacer. '), 'El contador solo avanza: los números que se saltan no podrán expedirse '
        + 'con esta aplicación. El cambio queda en el registro de actividad y en el diario de recuperación.'),
      kv(datos),
      avisoMeses,
      form,
      vista,
      h('div', { class: 'casilla casilla-destacada' }, chk, chkTexto),
      error,
    ],
    acciones: [boton('Cancelar', () => dlg.close()), avanzar],
  });
  ver();
}

function filaFactura(x) {
  return x.factura_id ? enlace(x.num_serie || `#${x.factura_id}`, `#/factura/${enc(x.factura_id)}`, { class: 'mono' })
    : h('span', { class: 'mono' }, x.num_serie || '—');
}

function huellas(x, corto = 'AEAT') {
  const loc = x.huella_local ? abreviar(x.huella_local) : '—';
  const aeat = x.huella_aeat ? abreviar(x.huella_aeat) : '—';
  if (x.huella_local && x.huella_aeat && x.huella_local === x.huella_aeat) return h('span', { class: 'mono', title: x.huella_local }, `${loc} (iguales)`);
  return [h('span', { class: 'mono bloque', title: x.huella_local || '' }, `Local: ${loc}`),
    h('span', { class: 'mono bloque', title: x.huella_aeat || '' }, `${corto}: ${aeat}`)];
}

/** Nombres del servicio consultado en la pantalla de consulta: el simulador local nunca se presenta como la AEAT. */
function nombresServicio(entorno) {
  const sim = entorno === 'simulador';
  return {
    donde: sim ? 'el simulador local' : 'la AEAT', de: sim ? 'del simulador local' : 'de la AEAT',
    Donde: sim ? 'El simulador local' : 'La AEAT', corto: sim ? 'Simulador' : 'AEAT', estado: sim ? 'Estado en el simulador' : 'Estado AEAT',
  };
}

function notaConsulta(x) {
  return [x.revisar ? pill('Revisar', 'warn') : null, x.revisar ? ' ' : null,
    h('span', { class: 'valor-largo' }, x.motivo || x.nota || '—')];
}

async function paginaConsultaAeat(ctx) {
  titulo('Consulta a la AEAT');
  const eid = app.empresaId;
  const q = ctx.query;
  const hoy = anioMesHoy();
  const habilitados = entornosHabilitados();
  let entorno = habilitados.includes(q.get('entorno')) ? q.get('entorno') : (habilitados.includes(app.entorno) ? app.entorno : 'simulador');
  const selEnt = selector(habilitados.map((k) => [k, ENTORNOS[k].corto]), entorno);
  const ejercicio = entrada({ type: 'number', min: 2000, max: 2100, step: 1, required: true,
    value: /^\d{4}$/.test(q.get('ejercicio') || '') ? q.get('ejercicio') : String(hoy.anio) });
  const mesIni = /^(0[1-9]|1[0-2])$/.test(q.get('periodo') || '') ? q.get('periodo') : String(hoy.mes).padStart(2, '0');
  const periodo = selector(MESES.map((m, i) => [String(i + 1).padStart(2, '0'), `${String(i + 1).padStart(2, '0')} · ${m}`]), mesIni);
  const numSerie = entrada({ maxlength: 60, class: 'mono', placeholder: 'Opcional: una sola factura', value: q.get('num_serie') || '' });
  const badge = h('span', null, badgeEntorno(entorno, true));
  const resultado = h('div', { 'aria-live': 'polite' });
  const simulador = h('div');
  const puede = puedeEmitirRol();
  const btn = h('button', { type: 'submit', class: 'btn btn-primario', disabled: !puede }, 'Consultar');
  const form = h('form', { class: 'form' },
    h('div', { class: 'fila-campos' },
      campo('Entorno', selEnt), campo('Ejercicio', ejercicio), campo('Periodo (mes de expedición)', periodo),
      campo('Número de factura', numSerie)),
    h('div', { class: 'acciones' }, btn),
    puede ? null : alerta('info', 'Solo un usuario emisor o administrador puede consultar a la AEAT.'));

  async function consultar() {
    vaciar(resultado).append(cargando('Consultando…'));
    const cuerpo = { entorno, ejercicio: ejercicio.value.trim(), periodo: periodo.value };
    if (numSerie.value.trim()) cuerpo.num_serie = numSerie.value.trim();
    fijarQuery({ entorno, ejercicio: cuerpo.ejercicio, periodo: cuerpo.periodo, num_serie: cuerpo.num_serie || '' });
    try {
      const r = await api(`/empresas/${enc(eid)}/consulta`, { metodo: 'POST', json: cuerpo });
      if (!ctx.vigente()) return;
      pintar(r);
    } catch (err) {
      if (ctx.vigente() && err.estado !== 401) vaciar(resultado).append(cajaError(err));
    }
  }

  function pintar(r) {
    const res = r.resumen || {};
    const stat = (etq, valor, extra) => h('div', { class: 'stat' }, h('span', { class: 'stat-etq' }, etq), h('span', { class: 'stat-valor' }, String(valor ?? 0)), extra || null);
    const esSim = r.entorno === 'simulador';
    const sv = nombresServicio(r.entorno);
    const huellasSv = (x) => huellas(x, sv.corto);
    const nodos = [
      tarjeta('Resultado',
        h('div', { class: 'stats' },
          stat('Coinciden', res.coinciden),
          stat('Estado o huella distintos', res.distintos, res.conciliables ? h('span', { class: 'muted' }, `${res.conciliables} conciliable(s)`) : null),
          stat(`Solo en ${sv.donde}`, res.solo_aeat),
          stat('Solo en local', res.solo_local)),
        kv([
          ['Consultado', `${esSim ? 'Simulador local (no es la AEAT)' : ENTORNOS[r.entorno].corto} · ${fmtFechaHora(r.consultado_en)}`
            + (r.parcial ? ' · resultado parcial (IndicadorRepresentante)' : '')],
          ['Periodo', `${r.periodo}/${r.ejercicio}${r.num_serie ? ` · factura ${r.num_serie}` : ''}`],
          ['Registros devueltos', `${r.registros_aeat ?? 0} en ${r.paginas ?? 1} página(s)`],
          ['Evidencia', h('span', { class: 'mono valor-largo' }, r.evidencia || '—')],
        ]),
        comoLista(r.avisos).map((a) => alerta('warn', String(a))),
        res.revisar ? alerta('warn', `${res.revisar} elemento(s) requieren revisión manual. La consulta no cambia nada por sí misma.`)
          : alerta('ok', 'No hay nada que requiera revisión manual.')),
      tarjetaCadena(r),
      tarjetaDistintos(r),
      tarjeta(`Solo en ${sv.donde}`, h('p', { class: 'ayuda' }, `Registros que ${sv.donde} tiene y no constan en esta instalación (p. ej. expedidos con otro sistema o perdidos en un desastre). `
        + 'Sus números ya están usados: si son de una serie de esta instalación cuyo contador no ha llegado a ellos, un administrador puede avanzar el contador para no volver a expedirlos '
        + '(solo desde una consulta del mes completo: el número propuesto tiene en cuenta también los meses siguientes, hasta el actual).'),
        tabla([
          { titulo: 'Número', celda: (x) => h('span', { class: 'mono' }, x.num_serie) },
          { titulo: 'Fecha', celda: (x) => fmtFecha(x.fecha_expedicion) },
          { titulo: sv.estado, celda: (x) => pillDe(ESTADO_AEAT, x.estado_aeat) },
          { titulo: 'Importe', clase: 'num', celda: (x) => fmtImporte(x.importe_total_aeat) },
          { titulo: 'Huella', celda: (x) => h('span', { class: 'mono', title: x.huella_aeat || '' }, abreviar(x.huella_aeat) || '—') },
          { titulo: 'Presentado', celda: (x) => fmtFechaHora(x.timestamp_presentacion) },
          {
            titulo: 'Nota',
            // Solo con una consulta del mes completo (sin «Número de factura») y no parcial: el servidor comprueba además los
            // meses siguientes del ejercicio hasta el actual y dice cuáles
            celda: (x) => [...notaConsulta(x), x.siguiente_propuesto && x.serie_id && esAdmin() && !r.num_serie && !r.parcial
              ? h('span', { class: 'bloque' }, boton(`Avanzar el contador de la serie ${x.serie_codigo}…`, () => dialogoAvanceContador(
                { id: x.serie_id, codigo: x.serie_codigo, patron: x.serie_patron, entorno: r.entorno },
                { anio: x.anio_contador, siguiente: x.siguiente_propuesto, meses: x.meses_comprobados, servicio: r.servicio },
                () => consultar()), { pequeno: true }))
              : null],
          },
        ], comoLista(r.solo_aeat), { vacio: 'Ninguno.', leyenda: `Solo en ${sv.donde}` })),
      tarjeta('Solo en local', h('p', { class: 'ayuda' }, r.parcial
        ? `Registros de esta instalación que la respuesta parcial ${sv.de} no devuelve (puede que consten igualmente).`
        : `Registros de esta instalación que ${sv.donde} no devuelve.`),
        tabla([
          { titulo: 'Factura', celda: filaFactura },
          { titulo: 'Fecha', celda: (x) => fmtFecha(x.fecha_expedicion) },
          { titulo: 'Estado local', celda: (x) => pillDe(ESTADO_ENVIO, x.estado_local) },
          { titulo: 'Nota', celda: notaConsulta },
        ], comoLista(r.solo_local), { vacio: 'Ninguno.', leyenda: 'Solo en local' })),
      tarjeta('Coinciden',
        tabla([
          { titulo: 'Factura', celda: filaFactura },
          { titulo: 'Fecha', celda: (x) => fmtFecha(x.fecha_expedicion) },
          { titulo: 'Estado local', celda: (x) => pillDe(ESTADO_ENVIO, x.estado_local) },
          { titulo: sv.estado, celda: (x) => pillDe(ESTADO_AEAT, x.estado_aeat) },
          { titulo: 'Huella', celda: huellasSv },
          { titulo: 'Nota', celda: (x) => (x.nota ? h('span', { class: 'valor-largo muted' }, x.nota) : '—') },
        ], comoLista(r.coinciden), { vacio: 'Ninguno.', leyenda: 'Coinciden' })),
    ];
    anadir(vaciar(resultado), nodos);
  }

  /** Cadena atrasada respecto a la AEAT (tras restaurar una copia sin el diario) y cadena ya reanudada. */
  function tarjetaCadena(r) {
    const c = r.cadena_atrasada;
    const est = r.estado_cadena;
    if (!c && !(est && est.estado === 'anclada')) return null;
    if (!c) {
      return tarjeta('Cadena de registros', alerta('ok', 'La cadena se ha reanudado desde el registro ', h('code', null, est.num_serie),
        ` del ${fmtFecha(est.fecha_expedicion)} ${nombresServicio(r.entorno).de}: el próximo registro se encadenará a él.`));
    }
    const sv = nombresServicio(r.entorno);
    // Sin un último registro seguro (cadena_ambigua) o con una anulación que la consulta no deja ver, el servidor no la reanuda
    const sinReanudar = c.ambigua || comoLista(r.codigos_aviso).includes('anulacion_sin_huella');
    return tarjeta('Cadena de registros atrasada',
      alerta('danger', h('strong', null, 'No se pueden expedir, anular ni subsanar facturas en este entorno. '),
        `${sv.Donde} tiene ${c.registros_posteriores} registro(s) de esta instalación posteriores al último `
        + 'registro local: probablemente se generaron después de la copia restaurada y se perdieron con la base de datos y el diario. '
        + `El siguiente registro debe encadenarse al último que tiene ${sv.donde}, no al de la copia.`),
      kv([
        [c.ambigua ? `Registro más reciente en ${sv.donde}` : `Último registro en ${sv.donde}`, h('span', { class: 'mono' }, `${c.num_serie} · ${fmtFecha(c.fecha_expedicion)}`)],
        ['Generado', h('span', { class: 'mono' }, c.fecha_hora_huso)],
        ['Huella', h('span', { class: 'mono valor-largo' }, c.huella)],
        ['Último registro local', c.cabeza_local ? h('span', { class: 'mono' }, `${c.cabeza_local.num_serie} · ${c.cabeza_local.fecha_hora_huso}`) : 'Ninguno'],
        c.ambigua ? ['Candidatos a último', h('ul', { class: 'lista-motivos' }, comoLista(c.candidatos).map((x) => h('li', { class: 'mono' },
          `${x.num_serie} · ${fmtFecha(x.fecha_expedicion)} · ${x.fecha_hora_huso || 'sin fecha y hora'}`)))] : null,
      ].filter(Boolean)),
      sinReanudar
        ? alerta('warn', `La cadena no puede reanudarse desde la consulta: ${c.ambigua
          ? `la respuesta ${sv.de} no permite saber cuál de los registros candidatos es el último`
          : `${sv.donde} muestra una factura anulada con la huella de su registro de alta`} (vea los avisos). No expida, anule ni `
          + 'subsane facturas en este entorno y aclárelo con el operador de la plataforma.')
        : esAdmin()
          ? h('div', { class: 'acciones' }, boton(`Reanudar la cadena desde el último registro ${sv.de}…`, () => dialogoReanudarCadena(r, consultar), { tipo: 'peligro' }))
          : alerta('info', 'Solo un administrador puede reanudar la cadena.'));
  }

  function tarjetaDistintos(r) {
    const filas = comoLista(r.distintos);
    const sel = new Set();
    const admin = esAdmin();
    const conciliables = filas.filter((x) => x.conciliable);
    const sv = nombresServicio(r.entorno);
    const btnConc = boton('Conciliar los seleccionados', () => conciliar(btnConc), { tipo: 'primario', disabled: true });
    const actualizar = () => { btnConc.disabled = !sel.size; btnConc.textContent = sel.size ? `Conciliar los seleccionados (${sel.size})` : 'Conciliar los seleccionados'; };
    const cols = [];
    if (admin && conciliables.length) {
      cols.push({
        titulo: h('span', { class: 'sr-only' }, 'Seleccionar'), clase: 'col-chk',
        celda: (x) => {
          if (!x.conciliable) return '';
          const chk = h('input', { type: 'checkbox', 'aria-label': `Conciliar la factura ${x.num_serie}` });
          chk.addEventListener('change', () => { if (chk.checked) sel.add(x.registro_id); else sel.delete(x.registro_id); actualizar(); });
          return chk;
        },
      });
    }
    cols.push(
      { titulo: 'Factura', celda: filaFactura },
      { titulo: 'Fecha', celda: (x) => fmtFecha(x.fecha_expedicion) },
      { titulo: 'Estado local', celda: (x) => pillDe(ESTADO_ENVIO, x.estado_local) },
      { titulo: sv.estado, celda: (x) => pillDe(ESTADO_AEAT, x.estado_aeat) },
      { titulo: 'Huella', celda: (x) => huellas(x, sv.corto) },
      { titulo: 'Motivo', celda: (x) => [x.conciliable ? pill(`Conciliable → ${x.estado_propuesto_texto || 'aceptado'}`, 'info') : null, x.conciliable ? ' ' : null, ...notaConsulta(x)] },
    );

    async function conciliar(b) {
      const elegidas = filas.filter((x) => sel.has(x.registro_id));
      const ok = await confirmar({
        titulo: r.entorno === 'simulador' ? 'Conciliar con el simulador local' : 'Conciliar con la AEAT',
        contenido: [
          h('p', null, `Se marcarán como aceptados estos registros, que ${sv.donde} ya tiene con la misma huella. Antes de cambiar nada `
            + `se vuelve a consultar ${r.entorno === 'simulador' ? 'al simulador local' : 'a la AEAT'} y se comprueba cada uno; los que no cumplan se dejan como están.`),
          h('ul', { class: 'lista-simple' }, elegidas.map((x) => h('li', null, h('span', { class: 'mono' }, x.num_serie), ` · ${x.estado_local_texto || x.estado_local} → ${x.estado_propuesto_texto || 'Registro aceptado'}`))),
          h('p', { class: 'ayuda' }, 'El cambio queda en el registro de actividad y en el diario de recuperación. No se reenvía nada.'),
        ],
        textoBoton: 'Conciliar',
      });
      if (!ok) return;
      await conEspera(b, async () => {
        try {
          const res = await api(`/empresas/${enc(eid)}/consulta/conciliar`, { metodo: 'POST', json: { entorno: r.entorno, registro_ids: [...sel], confirmo: true } });
          const nc = comoLista(res.no_conciliados);
          notificar(`${comoLista(res.conciliados).length} registro(s) conciliado(s)${nc.length ? `; ${nc.length} sin conciliar` : ''}.`, nc.length ? 'warn' : 'ok');
          refrescarAvisos(true);   // la conciliación puede cerrar la incidencia de comunicación
          if (nc.length) {
            dialogo({
              titulo: 'Registros no conciliados',
              contenido: h('ul', { class: 'lista-simple' }, nc.map((x) => h('li', null, h('span', { class: 'mono' }, x.num_serie || `#${x.registro_id}`), `: ${x.motivo}`))),
              acciones: [boton('Cerrar', (ev) => ev.currentTarget.closest('dialog').close())],
            });
          }
          await consultar();
        } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
      }, 'Conciliando…');
    }

    return tarjeta('Estado o huella distintos',
      h('p', { class: 'ayuda' }, 'Facturas que están en los dos lados con diferencias. Solo se ofrece corregir automáticamente el caso seguro: '
        + `un registro local sin respuesta definitiva (o rechazado por duplicado, 3000) que ${sv.donde} ya tiene con la misma huella. `
        + 'El resto requiere revisión manual.'),
      tabla(cols, filas, { vacio: 'Ninguna.', leyenda: 'Estado o huella distintos' }),
      conciliables.length ? (admin ? h('div', { class: 'acciones' }, btnConc)
        : alerta('info', 'Solo un administrador puede conciliar los registros.')) : null);
  }

  function pintarSimulador() {
    vaciar(simulador);
    if (entorno !== 'simulador' || !esAdmin()) return;
    const veces = entrada({ type: 'number', min: 1, max: 20, step: 1, value: '1' });
    const formSim = h('form', { class: 'form' },
      grupoRadios('modo_consulta', 'Comportamiento del simulador en las próximas consultas', MODOS_CONSULTA_SIMULADOR, 'normal'),
      h('div', { class: 'fila-entorno' }, campo('Número de consultas afectadas', veces)),
      h('div', { class: 'acciones' }, h('button', { type: 'submit', class: 'btn btn-secundario' }, 'Aplicar modo')));
    formSim.addEventListener('submit', async (e) => {
      e.preventDefault();
      const modo = valorRadio(formSim, 'modo_consulta');
      await conEspera(formSim.querySelector('button[type="submit"]'), async () => {
        try {
          await api(`/empresas/${enc(eid)}/simulador/modo`, { metodo: 'POST', json: { modo, servicio: 'consulta', veces: Math.max(1, Math.min(20, Number(veces.value) || 1)) } });
          notificar(`Simulador (consulta): modo «${(MODOS_CONSULTA_SIMULADOR.find(([k]) => k === modo) || [null, modo])[1]}» para esta empresa.`, 'ok');
        } catch (err) { if (err.estado !== 401) notificar(err.message, 'danger'); }
      });
    });
    simulador.append(h('section', { class: 'card card-simulador' },
      h('h2', { class: 'card-titulo' }, 'Simulación local de la consulta — no es la AEAT'),
      h('p', null, 'Permite probar cómo responde la aplicación ante fallos del servicio de consulta. Solo afecta a las consultas de esta empresa al simulador local.'),
      formSim));
  }

  selEnt.addEventListener('change', () => {
    entorno = selEnt.value;
    badge.replaceChildren(badgeEntorno(entorno, true));
    vaciar(resultado);
    pintarSimulador();
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!/^\d{4}$/.test(ejercicio.value.trim())) { notificar('Indique el ejercicio con 4 cifras.', 'danger'); ejercicio.focus(); return; }
    await conEspera(btn, consultar, 'Consultando…');
  });
  pintarSimulador();
  montar(ctx,
    cabeceraPagina('Consulta a la AEAT', { meta: [badge] }),
    tarjeta(null,
      h('p', null, 'Consulta los registros de facturación que la AEAT tiene de esta empresa en un mes y los compara con los de esta '
        + 'instalación: coinciden, solo en la AEAT, solo en local, o con estado o huella distintos.'),
      h('p', { class: 'ayuda' }, 'La consulta usa el mismo certificado y las mismas comprobaciones que la remisión. No modifica nada: '
        + 'solo un administrador puede conciliar, con confirmación, los registros sin respuesta (o rechazados por duplicado) que la AEAT '
        + 'ya tiene con la misma huella. Útil para confirmar envíos sin respuesta y, tras restaurar una copia, para comprobar qué consta en la AEAT.'),
      form),
    resultado,
    simulador);
  // Nunca se consulta al cargar la página (ni al recargarla): solo con el botón «Consultar».
}
