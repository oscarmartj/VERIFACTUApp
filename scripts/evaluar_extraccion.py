"""Evalúa la extracción sobre un corpus de PDF con valores esperados (JSON con el mismo nombre).

Uso: python scripts/evaluar_extraccion.py tests/corpus [tests/corpus_holdout ...] [--informe ruta.json] [--detalle]
Compara campo a campo (número, fecha, partes, importes, desglose, líneas, referencias, pago, indicios y
situación del documento) y muestra la precisión por campo y los fallos de cada documento.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app import borradores, nif as nifmod  # noqa: E402
from app.extraccion import campos, inspeccion, texto  # noqa: E402


BLOQUEANTES = {"exencion", "inversion_sujeto_pasivo", "no_sujeta", "recargo_equivalencia", "suplidos", "rectificativa",
               "intracomunitaria", "criterio_caja", "regimen_especial", "verifactu_existente"}


def norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]", "", t)


def dec(v):
    try:
        return Decimal(str(v)).quantize(Decimal("0.01")) if v not in (None, "") else None
    except InvalidOperation:
        return None


def val(c):
    return c.get("valor") if isinstance(c, dict) else None


def evaluar_documento(pdf: Path, esp: dict) -> dict:
    datos = pdf.read_bytes()
    try:
        insp = inspeccion.inspeccionar(datos)
    except inspeccion.PDFNoValido as e:
        return {"archivo": pdf.name, "error": str(e), "campos": {}}
    insp["qr"] = []
    pags = texto.leer(datos)
    ext = campos.Extractor(pags, pdf.name, esp.get("empresa_nif")).extraer()
    d = borradores.datos_desde_extraccion(ext, pdf.name)
    sug = borradores.clasificar(ext, insp, {"nif": esp.get("empresa_nif")})
    r: dict[str, bool | None] = {}

    def comp(nombre, ok):
        r[nombre] = bool(ok)
    if esp.get("numero") is not None:
        comp("numero", norm(d.get("num_serie")) == norm(esp["numero"]))
    if esp.get("fecha"):
        comp("fecha", d.get("fecha_documento") == esp["fecha"])
    if esp.get("tipo_documento"):
        comp("tipo_documento", (d.get("tipo_documento_origen") or "") == esp["tipo_documento"])
    for rol in ("emisor", "destinatario"):
        e = esp.get(rol) or {}
        x = d.get(rol) or {}
        if e.get("nif"):
            comp(f"{rol}.nif", nifmod.normalizar(x.get("nif")) == nifmod.normalizar(e["nif"]))
        if e.get("nombre"):
            a, b = norm(x.get("nombre")), norm(e["nombre"])
            comp(f"{rol}.nombre", bool(a) and (a == b or (len(b) > 6 and (b in a or a in b))))
        if e.get("domicilio_contiene"):
            comp(f"{rol}.domicilio", norm(e["domicilio_contiene"]) in norm(x.get("domicilio")))
    for k_esp, k_d in (("base_imponible", "base_imponible"), ("cuota_iva", "cuota_total"), ("total_factura", "importe_total")):
        if esp.get(k_esp) is not None:
            comp(k_esp, dec(d.get(k_d)) == dec(esp[k_esp]))
    if esp.get("total_a_pagar") is not None:
        comp("total_a_pagar", dec(d.get("total_a_pagar")) == dec(esp["total_a_pagar"]))
    if "retencion" in esp:
        if esp["retencion"]:
            ret = d.get("retencion") or {}
            comp("retencion", dec(ret.get("importe")) == dec(esp["retencion"]["importe"]))
        else:
            comp("retencion", not (d.get("retencion") or {}).get("importe"))
    if esp.get("desglose"):
        got = sorted((dec(x.get("tipo_iva")), dec(x.get("base")), dec(x.get("cuota"))) for x in d.get("desglose") or [])
        exp = sorted((dec(x["tipo_iva"]), dec(x["base"]), dec(x["cuota"])) for x in esp["desglose"])
        comp("desglose", got == exp)
    if esp.get("lineas"):
        got = d.get("lineas") or []
        imps_got = sorted(dec(x.get("importe")) for x in got if x.get("importe") is not None)
        imps_exp = sorted(dec(x["importe"]) for x in esp["lineas"])
        comp("lineas.importes", imps_got == imps_exp)
        conceptos = " ".join(norm(x.get("concepto")) for x in got)
        comp("lineas.conceptos", all(norm(x["concepto_contiene"]) in conceptos for x in esp["lineas"] if x.get("concepto_contiene")))
        cants = [(dec(x.get("cantidad")), dec(x.get("precio"))) for x in got]
        exp_c = [(dec(x.get("cantidad")), dec(x.get("precio"))) for x in esp["lineas"] if x.get("cantidad") is not None]
        if exp_c:
            comp("lineas.cantidad_precio", all(e in cants for e in exp_c))
    for k, v in (esp.get("referencias") or {}).items():
        if v:
            comp(f"ref.{k}", norm((d.get("referencias") or {}).get(k)) == norm(v) or
                 (len(norm(v)) > 8 and norm(v) in norm((d.get("referencias") or {}).get(k))))
    if esp.get("iban"):
        comp("iban", norm(d.get("iban")) == norm(esp["iban"]))
    if esp.get("forma_pago_contiene"):
        comp("forma_pago", norm(esp["forma_pago_contiene"]) in norm(d.get("forma_pago")))
    for ind in esp.get("indicios_esperados") or []:
        comp(f"indicio.{ind}", ind in (d.get("indicios") or []))
    if "indicios_esperados" in esp:
        # falsos positivos de particularidades que bloquean o cambian el tratamiento fiscal
        extra = set(d.get("indicios") or []) & BLOQUEANTES - set(esp.get("indicios_esperados") or [])
        comp("indicio.no_esperado", not extra)
        if extra:
            r["_extra"] = sorted(extra)
    sit = esp.get("situacion_esperada")
    if sit:
        s = sug["sugerida"]
        comp("situacion", s == sit or (s == "pendiente_confirmar" and sit in ("preparatorio", "emitida_propia")))
    extra = r.pop("_extra", None)
    return {"archivo": pdf.name, "estilo": esp.get("estilo"), "campos": r, "indicios_no_esperados": extra,
            "fallos": [k for k, ok in r.items() if not ok], "obtenido": {k: d.get(k) for k in ("num_serie", "fecha_documento",
                       "base_imponible", "cuota_total", "importe_total", "total_a_pagar")} | {
                       "emisor": d.get("emisor"), "destinatario": d.get("destinatario"), "desglose": d.get("desglose"),
                       "lineas": d.get("lineas"), "referencias": d.get("referencias"), "situacion": sug["sugerida"]}}


def evaluar(dirs: list[Path]) -> dict:
    docs = []
    for d in dirs:
        for js in sorted(d.rglob("*.json")):
            pdf = js.with_suffix(".pdf")
            if not pdf.exists():
                continue
            esp = json.loads(js.read_text(encoding="utf-8"))
            docs.append(evaluar_documento(pdf, esp) | {"conjunto": d.name})
    por_campo: dict[str, list[bool]] = {}
    for doc in docs:
        for k, ok in doc.get("campos", {}).items():
            base = k.split(".")[0] if k.startswith(("ref.", "indicio.")) else k
            por_campo.setdefault(base, []).append(ok)
    total = sum(len(v) for v in por_campo.values())
    bien = sum(sum(v) for v in por_campo.values())
    return {"documentos": docs, "por_campo": {k: {"aciertos": sum(v), "total": len(v), "precision": round(sum(v) / len(v), 3)}
                                               for k, v in sorted(por_campo.items())},
            "global": {"aciertos": bien, "total": total, "precision": round(bien / total, 3) if total else None},
            "documentos_perfectos": sum(1 for d in docs if d.get("campos") and not d.get("fallos"))}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    informe = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--informe"), None)
    detalle = "--detalle" in sys.argv
    res = evaluar([Path(a) for a in args])
    print(f"Documentos: {len(res['documentos'])} · perfectos: {res['documentos_perfectos']} · "
          f"precisión global por campo: {res['global']['precision']:.1%} ({res['global']['aciertos']}/{res['global']['total']})")
    for k, v in res["por_campo"].items():
        print(f"  {k:24s} {v['precision']:6.1%}  ({v['aciertos']}/{v['total']})")
    for d in res["documentos"]:
        if d.get("error") or d.get("fallos"):
            print(f"- {d['conjunto']}/{d['archivo']}: {d.get('error') or ', '.join(d['fallos'])}")
            if detalle:
                print("    ", json.dumps(d.get("obtenido"), ensure_ascii=False, default=str)[:1500])
    if informe:
        Path(informe).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
