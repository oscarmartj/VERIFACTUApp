"""Control de acceso a los entornos de remisión (pruebas AEAT y producción).

La producción solo se habilita cuando se cumplen todos los requisitos y existe una autorización
expresa del primer envío real. El interruptor de servidor impide comunicaciones reales accidentales
durante el desarrollo y las pruebas automáticas.
"""
from __future__ import annotations

import json

from . import certificados, db
from .config import APP_VERSION, ESQUEMAS_DIR, ajustes


class EntornoNoHabilitado(Exception):
    pass


def productor(con) -> dict | None:
    return db.fila(con, "SELECT * FROM productor WHERE id=1")


def esquemas_contrastados() -> bool:
    return (ESQUEMAS_DIR / "CONTRASTADO_AEAT").exists()


def requisitos(con, empresa: dict, usuario_id: int, entorno: str = "produccion") -> list[dict]:
    a = ajustes()
    req = []

    def r(clave, texto, cumplido, detalle=""):
        req.append({"clave": clave, "texto": texto, "cumplido": bool(cumplido), "detalle": detalle})
    amb = json.loads(empresa.get("ambito_resultado") or "{}")
    r("ambito", "Ámbito del emisor confirmado y soportado", empresa.get("ambito_confirmado_en") and amb.get("puede_emitir"),
      "; ".join(amb.get("motivos", [])))
    m = db.fila(con, "SELECT * FROM miembros WHERE usuario_id=? AND empresa_id=?", (usuario_id, empresa["id"]))
    r("autorizacion_usuario", "El usuario ha declarado su autorización para actuar por la empresa",
      m and m.get("declaracion_autorizacion_en"), (m or {}).get("relacion", ""))
    p = productor(con)
    r("productor", "Productor del sistema identificado y confirmado", p and p.get("nif") and p.get("confirmado_en"),
      (p or {}).get("nombre") or "Sin designar: lo identifica y confirma el operador de la plataforma")
    serie = db.fila(con, "SELECT COUNT(*) n FROM series WHERE empresa_id=? AND entorno=? AND confirmada_en IS NOT NULL",
                    (empresa["id"], entorno))
    r("serie", f"Serie de numeración confirmada para {entorno}", serie["n"] > 0)
    # Qué certificado se usará (de la empresa o de «Mis certificados») y si actúa un representante (tercero legitimado)
    cert = certificados.activo(con, empresa["id"], entorno)
    leg = (cert or {}).get("legitimacion_dict") or {}
    r("certificado", f"Certificado de {entorno} vigente y utilizable por la empresa", cert and leg.get("utilizable"),
      certificados.describir(cert, entorno))
    req[-1]["certificado"] = certificados.resumen(cert, usuario_id)
    if cert and leg.get("requiere_legitimacion"):
        t = leg.get("titular") or {}
        r("representacion", f"Legitimación del titular del certificado ({t.get('nombre') or '—'} · NIF {t.get('nif') or '—'}) "
          "para presentar en nombre de la empresa (apoderamiento o colaboración social); los envíos llevarán "
          "Cabecera/Representante", leg.get("legitimacion_ok"), leg.get("detalle", ""))
    acr = certificados.nif_acreditado(con, empresa)
    propio = bool(cert and leg.get("propio"))
    r("nif_verificado", "NIF de la empresa acreditado: certificado de la propia empresa o verificación del operador de la "
      "plataforma", acr["ok"] or propio, _detalle_nif(acr, propio))
    if entorno == "pruebas":
        r("interruptor", "Remisión al entorno de pruebas de la AEAT habilitada en el servidor (VERIFACTU_PERMITIR_PRUEBAS_AEAT=1)",
          a.permitir_pruebas_aeat)
        return req
    decl = db.fila(con, "SELECT * FROM declaraciones WHERE version_sif=? AND estado='formalizada'", (APP_VERSION,))
    r("declaracion", f"Declaración responsable formalizada por el productor para la versión {APP_VERSION}", decl)
    r("esquemas", "Esquemas XSD contrastados con la publicación oficial de la AEAT", esquemas_contrastados(),
      "Ejecute scripts/verificar_esquemas.py desde una red con acceso a la AEAT")
    ok_pruebas = db.fila(con, """SELECT COUNT(*) n FROM registros WHERE empresa_id=? AND entorno='pruebas'
                                 AND estado_envio IN ('aceptado','aceptado_con_errores')""", (empresa["id"],))
    r("prueba_aeat", "Al menos un registro aceptado en el entorno de pruebas de la AEAT", ok_pruebas["n"] > 0)
    r("interruptor", "Producción habilitada en el servidor (VERIFACTU_PERMITIR_PRODUCCION=1)", a.permitir_produccion)
    aut = db.fila(con, "SELECT * FROM autorizaciones_produccion WHERE empresa_id=? ORDER BY id DESC LIMIT 1", (empresa["id"],))
    r("autorizacion_primer_envio", "Autorización expresa del primer envío real", aut,
      f"Autorizado el {db.legible(aut['en'])}" if aut else "Pendiente")
    return req


_MOTIVOS_NIF = {
    "operador": "Verificado por el operador de la plataforma",
    "alta_por_operador": "La dio de alta el operador de la plataforma",
    "version_anterior": "Empresa dada de alta por el operador en la versión anterior de la aplicación",
    "envio_certificado_propio": "La AEAT ya aceptó un envío de la empresa hecho con su propio certificado",
}


def _detalle_nif(acr: dict, propio: bool) -> str:
    if acr["ok"]:
        return f"{_MOTIVOS_NIF.get(acr['motivo'], 'Acreditado')} ({db.legible(acr.get('en'), hora=False)})."
    if propio:
        return "Se usará un certificado de la propia empresa: la AEAT comprueba su titularidad en cada envío."
    return ("Para el primer envío a la AEAT con el certificado de un tercero, pida al operador de la plataforma que verifique el "
            "NIF de la empresa (aportando la documentación que acredite que actúa por ella), o use un certificado de la propia "
            "empresa.")


def comprobar(con, empresa: dict, usuario_id: int, entorno: str) -> None:
    if entorno == "simulador":
        return
    if entorno not in ("pruebas", "produccion"):
        raise EntornoNoHabilitado("Entorno desconocido")
    faltan = [x["texto"] for x in requisitos(con, empresa, usuario_id, entorno) if not x["cumplido"]]
    if faltan:
        raise EntornoNoHabilitado(f"El entorno «{entorno}» no está habilitado. Falta: " + "; ".join(faltan))


def resumen_primer_envio(con, empresa: dict) -> dict:
    pend = db.filas(con, """SELECT f.num_serie, f.fecha_expedicion, f.destinatario_nombre, f.destinatario_nif, f.importe_total
                            FROM facturas f WHERE f.empresa_id=? AND f.entorno='produccion' ORDER BY f.id LIMIT 20""",
                    (empresa["id"],))
    return {
        "empresa": {"nif": empresa["nif"], "razon_social": empresa["razon_social"]},
        "destino": "Servicio VERI*FACTU de la AEAT (producción)",
        "datos_remitidos": ["NIF y razón social del emisor", "Número y fecha de expedición", "Tipo de factura",
                            "Descripción de la operación", "Destinatario (NIF y razón social)",
                            "Desglose de bases, tipos y cuotas", "Cuota total e importe total",
                            "Encadenamiento y huella", "Identificación del sistema informático"],
        "no_se_remite": ["El PDF", "Líneas de detalle", "Referencias comerciales (obra, pedido, certificación)",
                         "Condiciones de pago e IBAN"],
        "facturas_pendientes": pend,
    }
