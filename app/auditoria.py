"""Verificación independiente de la integridad de la cadena de registros y de la coherencia factura↔registro↔QR."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from lxml import etree

from . import db
from .verifactu import huella as hmod
from .verifactu.esquemas_xsd import NS_SF


def verificar_cadena(con, empresa_id: int, entorno: str) -> dict:
    regs = db.filas(con, "SELECT * FROM registros WHERE empresa_id=? AND entorno=? ORDER BY instalacion_id, secuencia", (empresa_id, entorno))
    errores = []
    previo = None
    for r in regs:
        if hmod.sha256_mayus(r["cadena_huella"]) != r["huella"]:
            errores.append(f"Registro {r['id']}: la huella no corresponde a su cadena")
        x = etree.fromstring(r["xml"].encode())
        ns = {"sf": NS_SF}
        if x.findtext("sf:Huella", namespaces=ns) != r["huella"]:
            errores.append(f"Registro {r['id']}: la huella del XML difiere")
        if x.findtext("sf:FechaHoraHusoGenRegistro", namespaces=ns) != r["fecha_hora_huso"]:
            errores.append(f"Registro {r['id']}: marca temporal del XML difiere")
        if previo is None:
            if r["secuencia"] != 1 or r["huella_anterior"] or x.find("sf:Encadenamiento/sf:PrimerRegistro", ns) is None:
                errores.append(f"Registro {r['id']}: debería ser el primero de la cadena")
        else:
            if r["secuencia"] != previo["secuencia"] + 1:
                errores.append(f"Registro {r['id']}: salto de secuencia")
            if r["huella_anterior"] != previo["huella"]:
                errores.append(f"Registro {r['id']}: no encadena con el anterior")
            if x.findtext("sf:Encadenamiento/sf:RegistroAnterior/sf:Huella", namespaces=ns) != previo["huella"]:
                errores.append(f"Registro {r['id']}: el XML no encadena con el anterior")
        previo = r
    return {"ok": not errores, "registros": len(regs), "errores": errores}


def coherencia_factura(con, factura_id: int) -> dict:
    """Comprueba que datos aprobados, XML del registro de alta y URL del QR contienen los mismos valores."""
    f = db.fila(con, "SELECT * FROM facturas WHERE id=?", (factura_id,))
    d = json.loads(f["datos"])
    alta = db.fila(con, "SELECT * FROM registros WHERE factura_id=? AND tipo='alta' ORDER BY id LIMIT 1", (factura_id,))
    x = etree.fromstring(alta["xml"].encode())
    ns = {"sf": NS_SF}
    q = {k: v[0] for k, v in parse_qs(urlparse(f["url_qr"]).query).items()}
    fecha = hmod.fecha_aeat(d["fecha_expedicion"])
    c = {
        "nif": d["emisor"]["nif"] == x.findtext("sf:IDFactura/sf:IDEmisorFactura", namespaces=ns) == q["nif"],
        "num_serie": d["num_serie"] == x.findtext("sf:IDFactura/sf:NumSerieFactura", namespaces=ns) == q["numserie"],
        "fecha": fecha == x.findtext("sf:IDFactura/sf:FechaExpedicionFactura", namespaces=ns) == q["fecha"],
        "importe_total": d["importe_total"] == x.findtext("sf:ImporteTotal", namespaces=ns) == q["importe"],
        "cuota_total": d["cuota_total"] == x.findtext("sf:CuotaTotal", namespaces=ns),
        "destinatario": d["destinatario"]["nif"] == x.findtext("sf:Destinatarios/sf:IDDestinatario/sf:NIF", namespaces=ns),
        "desglose": [(z["tipo_iva"], z["base"], z["cuota"]) for z in d["desglose"]] == [
            (e.findtext("sf:TipoImpositivo", namespaces=ns), e.findtext("sf:BaseImponibleOimporteNoSujeto", namespaces=ns),
             e.findtext("sf:CuotaRepercutida", namespaces=ns)) for e in x.findall("sf:Desglose/sf:DetalleDesglose", ns)],
    }
    return {"ok": all(c.values()), "comprobaciones": c}
