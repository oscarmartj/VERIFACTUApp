"""Regresiones de la prueba con cinco facturas reales más (carril E: importes en renglones con puntos guía). Las facturas
no se publican: estas pruebas reproducen cada causa con documentos sintéticos (nombres, NIF, direcciones e importes
ficticios).

- E1: cuadro de totales en renglones con puntos guía («Importe alquiler ……… 650,00 €», «IVA 21% ……… 136,50 €», «TOTAL
  FACTURA ……… 786,50 €»), sin rótulo de base. La base se perdía (o solo se deducía por coherencia aritmética) y, en los
  escaneos, la basura que el OCR lee en la fila de puntos separaba el rótulo de su cifra. Los renglones sin rótulo conocido
  encima del primer importe con rótulo son la base (su suma) si cuadra: base × tipo = cuota o base + cuota (− retención) =
  total.
- E2: «TOTAL FACTURA» impreso neto de la retención (base + cuota − retención): total de la factura = base + cuota (calculado)
  e importe a pagar = el impreso; sin total legible, el importe a pagar se calcula (para confirmar). La validación no da
  descuadres y un céntimo de diferencia en la retención (redondeo del emisor) se confirma, no bloquea.
- E3: factura sin IVA (alquiler de vivienda): base = total, cuota cero, sin desglose; la validación lo bloquea con un
  mensaje claro (SIN_IVA).
- E4: carta sin tabla de conceptos: el párrafo de encima del cuadro de totales es el concepto de una sola línea con la base
  como importe (para revisar).
- E5: cifras OCR con espacios en lugar de separadores («-1 23 50 €») o con punto decimal en un documento con coma decimal
  («550.80 €»): se aceptan si cuadran con el resto de importes, con confianza baja y un aviso; las de punto decimal que no
  cuadran se conservan con confianza más baja y un aviso (la validación señala el descuadre).

Revisión adversarial del carril:
- H1: un renglón de impuesto con un rótulo no reconocido («Impuesto 21%», «Tax 21%», «lVA 21%» del OCR) no es de la base ni
  hace de la factura una factura sin IVA.
- M1: la fila de puntos guía leída por el OCR como letras pequeñas de confianza nula («...oo.ooposor.oooopo») es relleno a
  cualquier resolución (300 ppp), aunque no sea mucho más baja que el rótulo.
- M2: un renglón en blanco entre la base y el total cabe con cuerpo de 9 puntos, interlineado amplio o cajas OCR (que solo
  cubren la altura de las cifras).
- M3: una lectura OCR dudosa que no cuadra no se pierde (total junto a una tasa); la retención sin porcentaje se comprueba con
  el líquido impreso.
"""
import io
import sqlite3
import tempfile
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, inspeccion, texto
from app.extraccion.texto import Pagina, Palabra
from app.nif import completar_cif

EMI = completar_cif("B", "0012345")   # emisor (la empresa usuaria)
DNI = "44444444A"                     # cliente particular
PUNTOS = "…………………………."


def _pdf(ops) -> bytes:
    """ops: (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _, alto = A4
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 10)
        (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, alto - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos: bytes):
    ext = campos.Extractor(texto.leer(datos), "f.pdf", EMI).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "f.pdf")


def _validar(ext, datos):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    empresa = {"id": 1, "nif": EMI, "razon_social": "INMUEBLES FICTICIOS, S.L.",
               "domicilio": "C/ Inventada, 3, 28000 Villaficticia", "ambito_confirmado_en": "x", "ambito_dict": {"puede_emitir": True}}
    documento = {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}
    serie = {"siguiente_numero": 1, "codigo": "X", "numero_detectado": None}
    return borradores.validar(con, empresa, documento, datos, extraccion=ext, serie=serie)


def _codigos(inc, nivel=None):
    return {i["codigo"] for i in inc if nivel is None or i["nivel"] == nivel}


def _v(campo):
    return campo["valor"] if isinstance(campo, dict) else None


def _sin_rotulo(ext) -> bool:
    return any("sin rótulo de base" in a for a in (ext["base_imponible"].get("avisos") or []))


PARRAFO = ["Arrendamiento del local comercial de la Calle Imaginaria,", "nº 7, bajo, de Villaficticia según contrato del",
           "1 de marzo de 2020 y correspondiente al mes de", "octubre de 2026."]


def _carta(totales, parrafo=PARRAFO, cliente=("Dña. Lucía Ejemplo Ficticia", f"N.I.F.: {DNI}"), tam=10, paso=25):
    """Factura en forma de carta: membrete del emisor arriba a la izquierda, destinatario a la derecha, número, fecha, un
    párrafo con el concepto y los totales en renglones con puntos guía (rótulo · puntos · cifra · €), con cuerpo de letra
    tam y paso paso entre renglones."""
    ops = [(85, 80, "INMUEBLES FICTICIOS, S.L.", 16, "Helvetica-Bold"), (85, 112, f"CIF: {EMI}"),
           (85, 128, "C/ Inventada, 3 – Local"), (85, 144, "28000 – Villaficticia"), (85, 160, "Telf: 900 000 000"),
           (260, 200, cliente[0]), (260, 216, cliente[1]), (260, 232, "Avda. Ficticia, 9"), (260, 248, "28000 Villaficticia"),
           (85, 300, "FACTURA Nº. Z-07/26", 10, "Helvetica-Bold"),
           (510, 325, "Villaficticia, a 5 de octubre de 2026", 10, "Helvetica", "r")]
    ops += [(85, 370 + 17 * k, t) for k, t in enumerate(parrafo)]
    y = 370 + 17 * len(parrafo) + 30
    for rotulo, cifra, *estilo in totales:
        if rotulo is None:
            y += paso   # renglón en blanco
            continue
        fuente = estilo[0] if estilo else "Helvetica"
        ops += [(190, y, rotulo, tam, fuente), (305, y, PUNTOS, tam, fuente), (478, y, cifra, tam, fuente, "r"),
                (482, y, "€", tam, fuente)]
        y += paso
    ops += [(85, y + 40, "Nota.- Cobro por domiciliación en la cuenta indicada por el cliente."),
            (85, y + 120, "Sus datos se tratan solo para gestionar la facturación del servicio contratado y se conservan", 7),
            (85, y + 129, "durante los plazos legales. Puede ejercer sus derechos ante Gestiones Imaginarias, S.A. en la", 7),
            (85, y + 138, "dirección indicada en el membrete.", 7)]
    return _pdf(ops)


# --- E1 + E4: base en un renglón sin rótulo de base y concepto en el párrafo de la carta --------------------------------
@pytest.mark.parametrize("rotulo", ["Importe alquiler", "Mes octubre", "Importe mes octubre"])
def test_base_en_renglon_con_puntos_guia_y_concepto_del_parrafo(rotulo):
    ext, d = _extraer(_carta([(rotulo, "1.500,00"), ("IVA 21%", "315,00"), (None, None),
                              ("TOTAL FACTURA", "1.815,00", "Helvetica-Bold")]))
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("1500.00", "315.00", "1815.00")
    assert [(x["tipo_iva"], x["base"], x["cuota"]) for x in d["desglose"]] == [("21.00", "1500.00", "315.00")]
    # base × tipo = cuota y base + cuota = total impreso: comprobada dos veces, no se pide confirmar, pero se explica
    assert ext["base_imponible"]["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert any(rotulo in a for a in ext["base_imponible"]["avisos"])
    # una sola línea: el párrafo como concepto y la base como importe, para revisar
    assert len(d["lineas"]) == 1 and d["lineas"][0]["importe"] == "1500.00"
    concepto = d["lineas"][0]["concepto"]
    assert concepto.startswith("Arrendamiento del local comercial") and concepto.endswith("octubre de 2026.")
    assert "Villaficticia, a 5" not in concepto and "FACTURA" not in concepto
    assert ext["lineas"][0]["importe"]["confianza"] < borradores.UMBRAL_CONFIANZA
    inc = _validar(ext, d)
    assert "TABLA_DEDUCIDA" in _codigos(inc, "confirmar")
    assert not _codigos(inc, "bloqueo") & {"BASE_FALTA", "BASE_DESCUADRE", "LINEAS_DESCUADRE", "TOTAL_DESCUADRE", "DESGLOSE_FALTA"}


def test_base_solo_con_la_cuota_se_pide_confirmar():
    """Sin total legible la base sin rótulo solo se comprueba con la cuota: se pide confirmar."""
    ext, d = _extraer(_carta([("Importe alquiler", "1.000,00"), ("IVA 21%", "210,00")]))
    assert d["base_imponible"] == "1000.00" and ext["base_imponible"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert "DUDOSO" in _codigos(_validar(ext, d), "confirmar")


def test_varios_renglones_sin_rotulo_suman_la_base_sin_linea_unica():
    ext, d = _extraer(_carta([("Mes septiembre", "700,00"), ("Mes octubre", "800,00"), ("IVA 21%", "315,00"),
                              ("TOTAL FACTURA", "1.815,00")]))
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("1500.00", "315.00", "1815.00")
    # otras cifras que no son del resumen: puede haber varios conceptos, no se deduce una línea única
    assert d["lineas"] == []


def test_renglon_sin_rotulo_solo_es_la_base_si_cuadra():
    ext, d = _extraer(_carta([("Importe alquiler", "1.500,00"), ("IVA 21%", "315,00"), ("TOTAL FACTURA", "1.815,00")]))
    assert d["base_imponible"] == "1500.00" and _sin_rotulo(ext)
    # la cifra no cuadra con la cuota ni con el total: no es la base (no se inventa)
    ext, d = _extraer(_carta([("Importe alquiler", "999,00"), ("IVA 21%", "315,00"), ("TOTAL FACTURA", "1.815,00")]))
    assert not _sin_rotulo(ext) and d["base_imponible"] != "999.00"


def test_varios_tipos_sin_base_rotulada_no_se_deducen_de_un_renglon():
    ext, d = _extraer(_carta([("Importe alquiler", "1.500,00"), ("IVA 21%", "315,00"), ("TOTAL FACTURA", "1.815,00")]))
    assert _sin_rotulo(ext)
    # varios tipos de IVA: la base de cada uno no se deduce de un renglón sin rótulo
    ext, d = _extraer(_carta([("Importe alquiler", "1.500,00"), ("IVA 21%", "315,00"), ("IVA 10%", "150,00"),
                              ("TOTAL FACTURA", "1.965,00")]))
    assert not _sin_rotulo(ext)


def test_texto_corrido_que_acaba_en_cifra_no_es_un_renglon_del_cuadro():
    """Un renglón de texto con una cifra al final, sin puntos guía ni hueco ancho, no es un renglón del cuadro."""
    tot = [("IVA 21%", "315,00"), ("TOTAL FACTURA", "1.815,00")]
    ext, d = _extraer(_carta([("Importe alquiler", "1.500,00")] + tot))
    assert _sin_rotulo(ext)
    ext, d = _extraer(_carta(tot, parrafo=PARRAFO[:3] + ["octubre de 2026 por un importe de 1.500,00"]))
    assert not _sin_rotulo(ext)


# --- E2: retención con el total impreso neto ------------------------------------------------------------------------------
def test_retencion_con_total_neto_impreso():
    ext, d = _extraer(_carta([("Importe mes octubre", "650,00"), ("IVA 21%", "136,50"), ("Retención 19%", "-123,50"),
                              (None, None), ("TOTAL FACTURA", "663,00", "Helvetica-Bold")]))
    assert (d["base_imponible"], d["cuota_total"]) == ("650.00", "136.50")
    assert d["importe_total"] == "786.50" and ext["total_factura"]["metodo"] == "calculado"
    assert d["total_a_pagar"] == "663.00"
    assert d["retencion"] == {"porcentaje": "19", "importe": "123.50"}
    inc = _validar(ext, d)
    assert not _codigos(inc, "bloqueo") & {"TOTAL_DESCUADRE", "PAGAR_DESCUADRE", "RET_DESCUADRE", "PAGAR_DISTINTO"}
    assert not _codigos(inc) & {"TOTAL_OTRA_CIFRA", "TOTAL_CIFRAS_DEBAJO", "RET_TIPO_INUSUAL"}
    assert "RETENCION" in _codigos(inc, "confirmar")


def test_retencion_con_un_centimo_de_diferencia_se_confirma():
    """Retención impresa truncada (720,50 × 19 % = 136,895 → 136,90; impresa 136,89): un céntimo de diferencia se confirma."""
    ext, d = _extraer(_carta([("Importe mes", "720,50"), ("IVA 21%", "151,31"), ("Retención 19%", "-136,89"),
                              ("TOTAL FACTURA", "734,92")]))
    assert (d["base_imponible"], d["importe_total"], d["total_a_pagar"]) == ("720.50", "871.81", "734.92")
    inc = _validar(ext, d)
    assert "RET_DESCUADRE" in _codigos(inc, "confirmar") and "RET_DESCUADRE" not in _codigos(inc, "bloqueo")
    assert not _codigos(inc, "bloqueo") & {"TOTAL_DESCUADRE", "PAGAR_DESCUADRE"}
    # más de un céntimo sí bloquea
    d2 = dict(d, retencion={"porcentaje": "19", "importe": "130.00"}, total_a_pagar="741.81")
    assert "RET_DESCUADRE" in _codigos(_validar(ext, d2), "bloqueo")


# --- E3: factura sin IVA ---------------------------------------------------------------------------------------------------
def test_factura_sin_iva_base_igual_al_total_y_bloqueo_claro():
    ext, d = _extraer(_carta([("Importe alquiler", "745,20"), (None, None), ("TOTAL FACTURA", "745,20", "Helvetica-Bold")],
                             parrafo=["Arrendamiento de la vivienda de la Calle Imaginaria, nº 7, de",
                                      "Villaficticia, correspondiente al mes de octubre de 2026."]))
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("745.20", "0.00", "745.20")
    # sin mención de exención: se propone una base sin IVA y sin causa, para que el usuario la elija (o corrija el IVA)
    assert d["desglose"] == [borradores.fila_exenta("745.20")] and ext["cuota_iva"]["metodo"] == "calculado"
    assert borradores.clase_fila(d["desglose"][0]) == "sin_calificar"
    assert ext["cuota_iva"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert len(d["lineas"]) == 1 and d["lineas"][0]["importe"] == "745.20"
    assert d["lineas"][0]["concepto"].startswith("Arrendamiento de la vivienda")
    inc = _validar(ext, d)
    sin_iva = [i for i in inc if i["codigo"] == "SIN_IVA"]
    assert sin_iva and sin_iva[0]["nivel"] == "bloqueo" and "no repercute IVA" in sin_iva[0]["mensaje"]
    assert "DESGLOSE_FALTA" not in _codigos(inc)
    assert not borradores.puede_emitir(inc)


def test_sin_iva_con_el_total_repetido_en_la_cabecera():
    """Un «Total factura» también en la cabecera no es el cuadro de totales: la base se busca encima del último cuadro."""
    datos = _carta([("Importe alquiler", "745,20"), (None, None), ("TOTAL FACTURA", "745,20", "Helvetica-Bold")],
                   cliente=("Dña. Lucía Ejemplo Ficticia", f"N.I.F.: {DNI}      Total factura: 745,20 €"))
    ext, d = _extraer(datos)
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("745.20", "0.00", "745.20")
    assert _sin_rotulo(ext) and "SIN_IVA" in _codigos(_validar(ext, d), "bloqueo")


def test_sin_iva_no_se_deduce_con_un_rotulo_de_iva_sin_cifra():
    """Con un rótulo de IVA en el cuadro (aunque su cifra no se lea), la base igual al total no es una factura sin IVA."""
    ext, _ = _extraer(_carta([("Importe alquiler", "745,20"), ("TOTAL FACTURA", "745,20")]))
    assert ext["cuota_iva"]["valor"] == "0.00"
    ext, _ = _extraer(_carta([("Importe alquiler", "745,20"), ("IVA 21%", "—"), ("TOTAL FACTURA", "745,20")]))
    assert ext["cuota_iva"]["valor"] != "0.00" and not _sin_rotulo(ext)


# --- E1 + E5 en escaneos: relleno de puntos leído por OCR y cifras de formato dudoso ---------------------------------------
def _pal(t, x, top, alto=9.0, conf=0.96, ancho=None):
    return Palabra(t, x, top, x + (ancho if ancho is not None else 5.2 * len(t)), top + alto, conf, "ocr")


def _renglon_ocr(rotulo, cifras, top, basura=True):
    """Renglón de un escaneo: rótulo a la izquierda, basura OCR baja y de confianza baja en la fila de puntos (sobre la línea
    base) y las cifras a la derecha (cada una, una palabra; «-1 23 50» son tres)."""
    pals, x = [], 160.0
    for w in rotulo.split():
        pals.append(_pal(w, x, top))
        x = pals[-1].x1 + 3.5
    if basura:
        pals += [_pal("oO”", 327, top + 6.5, alto=1.5, conf=0.25, ancho=13), _pal("s.", 343, top + 4.5, alto=4.5, conf=0.15, ancho=3),
                 _pal("...«o.o.", 370, top + 4.5, alto=4.5, conf=0.3, ancho=26),
                 _pal("PEOS", 398, top + 4.5, alto=4.5, conf=0.2, ancho=10),
                 _pal("Oaoaosaaoso", 410, top + 6, alto=3, conf=0.0, ancho=20)]
    x = 488.0
    for w in reversed(cifras.split()):
        x0 = x - 5.2 * len(w)
        pals.append(Palabra(w, x0, top, x, top + 9.5, 0.95, "ocr"))
        x = x0 - 1.5
    pals.append(_pal("€", 490, top))
    return pals


def _escaneo(renglones):
    pals = [_pal("INMUEBLES", 90, 80, alto=14), _pal("FICTICIOS,", 170, 80, alto=14), _pal("S.L.", 250, 80, alto=14),
            _pal("CIF:", 90, 112), _pal(EMI, 115, 112), _pal("FACTURA", 90, 300), _pal("Nº.", 140, 300), _pal("Z-07/26", 160, 300)]
    for k, w in enumerate(["Arrendamiento", "del", "local", "comercial", "de", "la", "Calle", "Imaginaria"]):
        pals.append(_pal(w, 90 + 48 * k, 360))
    for k, w in enumerate(["correspondiente", "al", "mes", "de", "octubre", "de", "2026."]):
        pals.append(_pal(w, 90 + 45 * k, 376))
    top = 410
    for r in renglones:
        if r is not None:
            pals += _renglon_ocr(*r, top=top) if isinstance(r, tuple) else r(top)
        top += 25
    return campos.Extractor([Pagina(1, 595, 842, pals, "ocr", 0.9)], "f.pdf", EMI).extraer()


def test_escaneo_relleno_ocr_entre_rotulo_y_cifra():
    ext = _escaneo([("Importe mes octubre", "650,00"), ("IVA 21%", "136,50"), ("Retención 19%", "-123,50"),
                    ("TOTAL FACTURA", "663,00")])
    assert _v(ext["base_imponible"]) == "650.00" and _v(ext["cuota_iva"]) == "136.50"
    assert _v(ext["retencion"]["importe"]) == "123.50" and _v(ext["retencion"]["porcentaje"]) == "19"
    assert _v(ext["total_factura"]) == "786.50" and _v(ext["total_a_pagar"]) == "663.00"


def test_escaneo_cifra_con_espacios_y_total_ilegible():
    """«-1 23 50 €» (espacios en lugar de separadores) cuadra con base × 19 %: se acepta para confirmar. Sin total legible,
    el importe a pagar se calcula como base + cuota − retención (para confirmar)."""
    ext = _escaneo([("Importe mes octubre", "650,00"), ("IVA 21%", "136,50"), ("Retención 19%", "-1 23 50"),
                    lambda top: [_pal("TOTAL", 160, top), _pal("FACTURA", 195, top), _pal("nu:", 451, top - 1, alto=22, conf=0.4)]])
    ret = ext["retencion"]["importe"]
    assert _v(ret) == "123.50" and ret["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("-1 23 50" in a for a in ret["avisos"])
    assert _v(ext["total_factura"]) is None   # no se ha leído: datos_desde_extraccion lo calcula y validar pide confirmarlo
    pagar = ext["total_a_pagar"]
    assert _v(pagar) == "663.00" and pagar["metodo"] == "calculado" and pagar["confianza"] < borradores.UMBRAL_CONFIANZA
    d = borradores.datos_desde_extraccion(ext, "f.pdf")
    assert (d["importe_total"], d["total_a_pagar"]) == ("786.50", "663.00")
    inc = _validar(ext, d)
    no_leido = [i for i in inc if i["codigo"] == "TOTAL_NO_LEIDO"]
    assert no_leido and "retención" in no_leido[0]["mensaje"] and "663.00" in no_leido[0]["mensaje"]
    assert not _codigos(inc, "bloqueo") & {"TOTAL_DESCUADRE", "PAGAR_DESCUADRE", "RET_DESCUADRE"}


def test_escaneo_cifra_con_espacios_que_no_cuadra_se_descarta():
    ext = _escaneo([("Importe mes octubre", "650,00"), ("IVA 21%", "136,50"), ("Retención 19%", "-1 33 50"),
                    ("TOTAL FACTURA", "786,50")])
    assert _v(ext["base_imponible"]) == "650.00" and _sin_rotulo(ext) and _v(ext["total_factura"]) == "786.50"
    assert ext["retencion"] is None   # 133,50 ≠ 650 × 19 %: no se inventa una retención


def test_escaneo_punto_decimal_en_documento_con_coma_decimal():
    """«550.80» en un escaneo con coma decimal: la coma leída como punto. Cuadra con base + cuota − retención: se acepta
    como importe a pagar, para confirmar."""
    ext = _escaneo([("Importe mes", "540,00"), ("IVA 21%", "113,40"), ("Retención 19%", "-102,60"), ("TOTAL FACTURA", "550.80")])
    assert _v(ext["base_imponible"]) == "540.00"
    assert _v(ext["total_factura"]) == "653.40" and ext["total_factura"]["metodo"] == "calculado"
    pagar = ext["total_a_pagar"]
    assert _v(pagar) == "550.80" and pagar["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("550.80" in a and "OCR" in a for a in pagar["avisos"])


def test_escaneo_punto_decimal_que_no_cuadra_se_conserva_y_se_senala():
    """«559.80» no cuadra con base + cuota − retención: no se sustituye por un importe calculado ni se pierde; se conserva
    con confianza baja y un aviso, y la validación señala el descuadre."""
    ext = _escaneo([("Importe mes", "540,00"), ("IVA 21%", "113,40"), ("Retención 19%", "-102,60"), ("TOTAL FACTURA", "559.80")])
    assert _v(ext["base_imponible"]) == "540.00"
    total = ext["total_factura"]
    assert _v(total) == "559.80" and total["confianza"] < 0.75
    assert any("559.80" in a and "no cuadra" in a for a in total["avisos"])
    assert ext["total_a_pagar"].get("metodo") != "calculado"
    d = borradores.datos_desde_extraccion(ext, "f.pdf")
    assert _codigos(_validar(ext, d), "bloqueo") & {"TOTAL_DESCUADRE", "PAGAR_DESCUADRE"}


def test_cifra_pequena_de_confianza_alta_no_es_relleno():
    """Tras un rótulo grande, la basura de la fila de puntos (baja, sobre la línea base, de confianza baja) es relleno; una
    cifra en letra más pequeña que el rótulo pero legible sigue siendo la cifra del rótulo."""
    pals = [_pal("TOTAL", 300, 500, alto=20), _pal("FACTURA", 360, 500, alto=20),
            _pal("PEOS", 420, 514, alto=5, conf=0.2), Palabra("1.210,00", 460, 510, 500, 519, 0.96, "ocr"),
            _pal("Base", 300, 450), _pal("imponible", 330, 450), Palabra("1.000,00", 460, 450, 500, 459, 0.96, "ocr"),
            _pal("IVA", 300, 470), _pal("21%", 325, 470), Palabra("210,00", 470, 470, 500, 479, 0.96, "ocr")]
    ext = campos.Extractor([Pagina(1, 595, 842, pals, "ocr", 0.9)], "f.pdf", EMI).extraer()
    assert _v(ext["total_factura"]) == "1210.00" and ext["total_factura"]["metodo"] == "ocr"
    # leído de su rótulo, no deducido por coherencia aritmética
    assert ext["total_factura"]["confianza"] >= borradores.UMBRAL_CONFIANZA and not ext["total_factura"]["avisos"]


# --- Escaneo de una carta sintética: los puntos guía leídos por Tesseract -------------------------------------------------
def _escanear(datos: bytes, dpi: int) -> bytes:
    """El PDF rasterizado a dpi y vuelto a envolver como un PDF de solo imagen (un escaneo limpio)."""
    with tempfile.TemporaryDirectory() as td:
        png = inspeccion.rasterizar(datos, Path(td), dpi=dpi)[0]
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.drawImage(ImageReader(str(png)), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
        c.save()
    return buf.getvalue()


@pytest.mark.parametrize("dpi", [200, 300])
def test_carta_escaneada_con_puntos_guia(dpi):
    """A 300 ppp el OCR lee la fila de puntos como letras pequeñas de confianza nula, no mucho más bajas que el rótulo."""
    datos = _carta([("Importe mes octubre", "650,00"), ("IVA 21%", "136,50"), ("Retención 19%", "-123,50"),
                    (None, None), ("TOTAL FACTURA", "663,00", "Helvetica-Bold")])
    ext, d = _extraer(_escanear(datos, dpi))
    assert ext["paginas"][0]["metodo"] == "ocr"
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"], d["total_a_pagar"]) == \
        ("650.00", "136.50", "786.50", "663.00")
    assert d["retencion"]["importe"] == "123.50"
    assert len(d["lineas"]) == 1 and d["lineas"][0]["importe"] == "650.00"


# --- Revisión adversarial ---------------------------------------------------------------------------------------------------
P_VIV = ["Arrendamiento de la vivienda de la Calle Imaginaria, nº 7, de",
         "Villaficticia, correspondiente al mes de octubre de 2026."]


@pytest.mark.parametrize("rotulo", ["Impuesto 21%", "Tax 21%", "lVA 21%", "I.V.A. al 21 por 100"])
def test_renglon_de_impuesto_no_reconocido_no_es_base_ni_factura_sin_iva(rotulo):
    """H1: un renglón de impuesto con un rótulo que no se reconoce no se suma a la base ni convierte la factura en una sin
    IVA: la base y la cuota siguen saliendo de la coherencia aritmética, para confirmar."""
    ext, d = _extraer(_carta([("Importe alquiler", "1.000,00"), (rotulo, "210,00"), (None, None),
                              ("TOTAL FACTURA", "1.210,00", "Helvetica-Bold")]))
    assert not _sin_rotulo(ext)
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("1000.00", "210.00", "1210.00")
    assert ext["base_imponible"]["confianza"] < borradores.UMBRAL_CONFIANZA
    assert [(x["base"], x["cuota"]) for x in d["desglose"]] == [("1000.00", "210.00")]
    assert "SIN_IVA" not in _codigos(_validar(ext, d))


def test_escaneo_renglon_de_impuesto_mal_leido_no_es_factura_sin_iva():
    """H1 en un escaneo: «IVA» leído «lVA» (I por l) no es un rótulo de IVA, pero tampoco un renglón de la base."""
    ext = _escaneo([("Importe alquiler", "1.000,00"), ("lVA 21%", "210,00"), ("TOTAL FACTURA", "1.210,00")])
    assert not _sin_rotulo(ext)
    assert _v(ext["base_imponible"]) != "1210.00" and _v(ext["cuota_iva"]) != "0.00"


def test_sin_iva_no_se_concluye_si_el_total_lleva_el_iva_incluido():
    ext, d = _extraer(_carta([("Importe servicio", "1.210,00"), (None, None), ("TOTAL FACTURA", "1.210,00", "Helvetica-Bold")],
                             parrafo=["Por la prestación de servicios de limpieza durante octubre de 2026,",
                                      "precio con el IVA del 21 % incluido."]))
    assert not _sin_rotulo(ext) and _v(ext["cuota_iva"]) != "0.00"
    assert "SIN_IVA" not in _codigos(_validar(ext, d))


def test_sin_iva_con_retencion_el_porcentaje_de_la_retencion_no_es_un_impuesto():
    ext, d = _extraer(_carta([("Importe alquiler", "1.000,00"), ("Retención 19%", "-190,00"),
                              ("TOTAL FACTURA", "810,00", "Helvetica-Bold")], parrafo=P_VIV,
                             cliente=("COMERCIAL INVENTADA, S.L.", f"C.I.F.: {completar_cif('B', '0098765')}")))
    assert (d["base_imponible"], d["cuota_total"], d["total_a_pagar"]) == ("1000.00", "0.00", "810.00")
    assert "SIN_IVA" in _codigos(_validar(ext, d), "bloqueo")


def _basura_300(top, textos=("...oo.ooposor.oooopo", "o9S.o.e.oooo.p.oo")):
    """Fila de puntos guía de un escaneo a 300 ppp leída por Tesseract: letras pequeñas y puntos de confianza nula sobre la
    línea base, de 4,5 puntos de alto frente a los 7,4 del rótulo (no mucho más bajas que él)."""
    return [_pal(t, 306 + 58 * k, top + 3.9, alto=4.5, conf=0.0, ancho=50) for k, t in enumerate(textos)]


def _renglon_300(rotulo, cifra, basura=_basura_300):
    def hacer(top):
        pals, x = [], 190.0
        for w in rotulo.split():
            pals.append(_pal(w, x, top, alto=7.4, conf=0.96))
            x = pals[-1].x1 + 3.5
        x0 = 477.4 - 5.2 * len(cifra)
        return pals + basura(top) + [Palabra(cifra, x0, top, 477.4, top + 8.3, 0.95, "ocr"), _pal("€", 482, top, alto=7.2)]
    return hacer


@pytest.mark.parametrize("basura", [_basura_300, lambda top: _basura_300(top, ("econo...", "o.oooo.ee"))])
def test_escaneo_relleno_de_letras_pequenas_de_confianza_nula(basura):
    """M1: la basura de la fila de puntos no es mucho más baja que el rótulo (6/10 de su alto) ni está hecha sobre todo de
    puntos, pero tiene confianza nula, va sobre la línea base y solo tiene signos sin trazos altos: es relleno, y la cuota
    del IVA no se separa de su rótulo."""
    ext = _escaneo([_renglon_300("Importe mes octubre", "650,00", basura), _renglon_300("IVA 21%", "136,50", basura),
                    _renglon_300("Retención 19%", "-123,50", basura), None, _renglon_300("TOTAL FACTURA", "663,00", basura)])
    assert (_v(ext["base_imponible"]), _v(ext["cuota_iva"])) == ("650.00", "136.50")
    assert (_v(ext["total_factura"]), _v(ext["total_a_pagar"])) == ("786.50", "663.00")


@pytest.mark.parametrize("tam, paso", [(9, 25), (10, 30)])
def test_sin_iva_con_renglon_en_blanco_y_otro_cuerpo_o_interlineado(tam, paso):
    """M2: el renglón en blanco entre la base y el total no corta el cuadro con cuerpo de 9 puntos o con un paso de 30."""
    ext, d = _extraer(_carta([("Importe alquiler", "745,20"), (None, None), ("TOTAL FACTURA", "745,20", "Helvetica-Bold")],
                             parrafo=P_VIV, tam=tam, paso=paso))
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("745.20", "0.00", "745.20")
    assert _sin_rotulo(ext) and "SIN_IVA" in _codigos(_validar(ext, d), "bloqueo")


def test_escaneo_sin_iva_con_renglon_en_blanco():
    """M2 con cajas OCR (solo la altura de las cifras): un renglón en blanco son más de cuatro alturas de la cifra."""
    ext = _escaneo([("Importe alquiler", "745,20"), None, ("TOTAL FACTURA", "745,20")])
    assert _v(ext["base_imponible"]) == "745.20" and _v(ext["cuota_iva"]) == "0.00" and _sin_rotulo(ext)
    ext = _escaneo([("Mes septiembre", "500,00"), ("Mes octubre", "500,00"), None, ("TOTAL FACTURA", "1.000,00")])
    assert _v(ext["base_imponible"]) == "1000.00" and _v(ext["cuota_iva"]) == "0.00"


def test_carta_sin_iva_escaneada_con_renglon_en_blanco():
    datos = _carta([("Importe alquiler", "745,20"), (None, None), ("TOTAL FACTURA", "745,20", "Helvetica-Bold")], parrafo=P_VIV)
    ext, d = _extraer(_escanear(datos, 200))
    assert ext["paginas"][0]["metodo"] == "ocr"
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("745.20", "0.00", "745.20")
    assert "SIN_IVA" in _codigos(_validar(ext, d), "bloqueo")


def test_escaneo_punto_decimal_junto_a_una_tasa_se_conserva():
    """M3: un total de punto decimal que no cuadra porque hay otra cifra (una tasa) en el cuadro no se pierde: se conserva
    para confirmar y la validación señala el descuadre, en lugar de faltar el total."""
    ext = _escaneo([("Base imponible", "1.000,00"), ("IVA 21%", "210,00"), ("Tasa residuos", "30,00"),
                    ("TOTAL FACTURA", "1240.00")])
    total = ext["total_factura"]
    assert _v(total) == "1240.00" and total["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("no cuadra" in a for a in total["avisos"])
    d = borradores.datos_desde_extraccion(ext, "f.pdf")
    inc = _validar(ext, d)
    assert "TOTAL_DESCUADRE" in _codigos(inc, "bloqueo") and "TOTAL_FALTA" not in _codigos(inc)


def test_escaneo_retencion_sin_porcentaje_cuadra_con_el_liquido_impreso():
    """M3: «Retención IRPF -150.00» sin porcentaje: base + cuota − retención = el líquido impreso, se acepta (para
    confirmar) y el importe a pagar no queda sin explicar."""
    ext = _escaneo([("Base imponible", "1.000,00"), ("IVA 21%", "210,00"), ("Retención IRPF", "-150.00"),
                    ("Total a pagar", "1.060,00")])
    ret = ext["retencion"]["importe"]
    assert _v(ret) == "150.00" and ret["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("-150.00" in a for a in ret["avisos"])
    d = borradores.datos_desde_extraccion(ext, "f.pdf")
    assert (d["importe_total"], d["total_a_pagar"]) == ("1210.00", "1060.00")
    assert "PAGAR_DISTINTO" not in _codigos(_validar(ext, d))


@pytest.mark.parametrize("con_base", [True, False])
def test_cifra_pequena_y_poco_legible_tras_un_rotulo_grande_es_su_cifra_dudosa(con_base):
    """La única cifra tras un «TOTAL» en letra grande es pequeña, va sobre la línea base y se lee con confianza baja: no es
    relleno (no hay otra cifra detrás), es el total, para confirmar."""
    pals = [_pal("TOTAL", 300, 500, alto=20), Palabra("1.210,00", 460, 511, 500, 520, 0.45, "ocr"),
            _pal("IVA", 300, 470), _pal("21%", 325, 470), Palabra("210,00", 470, 470, 500, 479, 0.96, "ocr")]
    if con_base:
        pals += [_pal("Base", 300, 450), _pal("imponible", 330, 450), Palabra("1.000,00", 460, 450, 500, 459, 0.96, "ocr")]
    ext = campos.Extractor([Pagina(1, 595, 842, pals, "ocr", 0.9)], "f.pdf", EMI).extraer()
    total = ext["total_factura"]
    assert _v(total) == "1210.00" and total["confianza"] < borradores.UMBRAL_CONFIANZA
    assert any("poca seguridad" in a for a in total["avisos"])
