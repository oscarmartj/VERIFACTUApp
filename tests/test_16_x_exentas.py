"""Operaciones exentas de IVA (carril «exentas»): OperacionExenta E1–E6 en lugar del bloqueo SIN_IVA.

- Modelo: cada fila del desglose es sujeta (S1, tipo, base y cuota) o exenta (causa E1–E6 y base, sin tipo ni cuota);
  facturas mixtas admitidas. Una base sin IVA y sin causa pide elegirla (o corregir el IVA). La causa siempre se confirma
  y la factura lleva la mención legal (art. 6.1.j RD 1619/2012). E5 (entrega intracomunitaria), no sujetas, inversión del
  sujeto pasivo y recargo de equivalencia siguen bloqueados.
- XML: DetalleDesglose con OperacionExenta y BaseImponibleOimporteNoSujeto, sin CalificacionOperacion, TipoImpositivo ni
  CuotaRepercutida; E2/E3 con ClaveRegimen 02; validado contra el XSD oficial. El simulador aplica las reglas 1199, 1207,
  1208, 1238, 1286 y 1289 del documento de validaciones.
- Extracción: la mención «exenta… art. 20…» de la Ley del IVA propone E1 (21 → E2, 22 → E3, 23/24 → E4, 25 → E5) con el
  texto citado; una factura sin IVA sin esa cita (sin mención, «exenta de IVA» sin artículo, un «exento» de otro contexto o
  el artículo de otra norma) propone una base sin causa, que sigue bloqueada (SIN_IVA) hasta que el usuario la elige.
- Flujo completo SIMULADO (nunca la AEAT): PDF sintético de alquiler de vivienda → borrador → E1 → expedición → simulador
  aceptado → coherencia PDF/XML/QR; factura mixta; rectificativa; copia y restauración con el diario.

Todos los datos son ficticios (NIF con código de provincia 00).
"""
import io
import json
import sqlite3

import pdfplumber
import pytest
from conftest import Cliente, configurar  # noqa: F401  (Cliente: fixture app_cliente)
from lxml import etree
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app import auditoria, borradores, copias, db, emision, remision, simulador
from app.extraccion import campos, texto
from app.nif import completar_cif
from app.pdf_final import _no_girado, leer_qrs
from app.verifactu import esquemas_xsd, huella as hmod, xmlgen
from app.verifactu.esquemas_xsd import NS_SF

EMI = completar_cif("B", "0012345")   # emisor ficticio (la empresa usuaria)
CLI = completar_cif("B", "0098765")   # cliente ficticio
EMPRESA = {"nif": EMI, "razon_social": "INMUEBLES FICTICIOS, S.L.", "domicilio": "C/ Inventada, 3 - 28000 Villaficticia"}
NS = {"sf": NS_SF}
MENCION_23 = "Operación exenta de IVA (art. 20.Uno.23º Ley 37/1992)"


# --- Utilidades ----------------------------------------------------------------------------------------------------------
def factura_pdf(numero: str, fecha: str, lineas, totales, notas=()) -> bytes:
    """Factura sintética: membrete del emisor, título, número y fecha, recuadro del cliente, tabla de conceptos, cuadro de
    totales y notas al pie de los totales."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    w, h = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(18 * mm, h - 25 * mm, EMPRESA["razon_social"])
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, h - 31 * mm, f"CIF: {EMI}")
    c.drawString(18 * mm, h - 36 * mm, "C/ Inventada, 3 - 28000 Villaficticia")
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(w - 18 * mm, h - 25 * mm, "FACTURA")
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 18 * mm, h - 33 * mm, f"Nº: {numero}")
    c.drawRightString(w - 18 * mm, h - 39 * mm, f"Fecha: {fecha}")
    y = h - 55 * mm
    c.rect(105 * mm, y - 25 * mm, 87 * mm, 30 * mm)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(108 * mm, y + 1 * mm, "CLIENTE")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(108 * mm, y - 5 * mm, "COMERCIAL INVENTADA, S.L.")
    c.setFont("Helvetica", 9)
    for i, t in enumerate(["Avda. Ficticia, 9", "28000 Villaficticia", f"CIF: {CLI}"]):
        c.drawString(108 * mm, y - (10 + 5 * i) * mm, t)
    y = h - 100 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "CONCEPTO")
    c.drawRightString(128 * mm, y, "CANTIDAD")
    c.drawString(131 * mm, y, "UD.")
    c.drawRightString(165 * mm, y, "PRECIO")
    c.drawRightString(w - 20 * mm, y, "IMPORTE")
    c.setFont("Helvetica", 9)
    y -= 9 * mm
    for concepto, cant, ud, precio, imp in lineas:
        c.drawString(20 * mm, y, concepto)
        c.drawRightString(128 * mm, y, cant)
        c.drawString(131 * mm, y, ud)
        c.drawRightString(165 * mm, y, precio)
        c.drawRightString(w - 20 * mm, y, imp)
        y -= 6 * mm
    c.line(18 * mm, y, w - 18 * mm, y)
    y -= 10 * mm
    for etq, val in totales:
        negrita = etq.startswith("TOTAL")
        c.setFont("Helvetica-Bold" if negrita else "Helvetica", 10 if negrita else 9)
        c.drawRightString(160 * mm, y, etq)
        c.drawRightString(w - 20 * mm, y, val + " €")
        y -= 6 * mm
    y -= 6 * mm
    c.setFont("Helvetica", 8.5)
    for t in list(notas) + ["Forma de pago: domiciliación bancaria."]:
        c.drawString(18 * mm, y, t)
        y -= 4.5 * mm
    c.setFont("Helvetica", 7)
    c.drawCentredString(w / 2, 20 * mm, f"{EMPRESA['razon_social']} - CIF {EMI} - C/ Inventada, 3 - 28000 Villaficticia")
    c.showPage()
    c.save()
    return buf.getvalue()


LINEA_ALQUILER = [("Alquiler de la vivienda de la Calle Imaginaria 7, noviembre 2026", "1,00", "mes", "650,00", "650,00")]
TOTALES_SIN_IVA = [("BASE IMPONIBLE", "650,00"), ("TOTAL FACTURA", "650,00")]


def extraer(pdf: bytes):
    ext = campos.Extractor(texto.leer(pdf), "f.pdf", EMI).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "f.pdf")


EMPRESA_VALIDAR = {"id": 1, "nif": EMI, "razon_social": EMPRESA["razon_social"], "domicilio": EMPRESA["domicilio"],
                   "ambito_confirmado_en": "x", "ambito_dict": {"puede_emitir": True}}


def validar(datos, ext=None, es_rectificativa=False):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    serie = {"siguiente_numero": 7, "codigo": "A", "numero_detectado": 7}
    return borradores.validar(con, EMPRESA_VALIDAR, None, datos, extraccion=ext, serie=serie, es_rectificativa=es_rectificativa)


def codigos(inc, nivel=None):
    return {i["codigo"] for i in inc if nivel is None or i["nivel"] == nivel}


def datos_base(desglose, base, cuota, total, **extra):
    d = {"num_serie": "ALQ-7", "emisor": {"nif": EMI, "nombre": EMPRESA["razon_social"], "domicilio": EMPRESA["domicilio"]},
         "destinatario": {"nif": CLI, "nombre": "COMERCIAL INVENTADA, S.L.", "domicilio": "Avda. Ficticia, 9, 28000 Villaficticia"},
         "descripcion_operacion": "Alquiler de vivienda", "desglose": desglose, "base_imponible": base, "cuota_total": cuota,
         "importe_total": total, "total_a_pagar": total, "lineas": [{"concepto": "Alquiler", "importe": base}], "indicios": []}
    d.update(extra)
    return d


def sujeta(tipo, base, cuota):
    return {"tipo_iva": tipo, "base": base, "cuota": cuota, "calificacion": "S1", "clave_regimen": "01", "impuesto": "01"}


SISTEMA = xmlgen.SistemaInformatico(nombre_razon="PRODUCTOR FICTICIO", nif=completar_cif("B", "0000017"),
                                    nombre_sistema="FacturaPDF VF", id_sistema="FV", version="0.1.0", numero_instalacion="1")


def alta(desglose, cuota_total, importe_total, *, fecha=None, nif_dest=CLI):
    fecha = fecha or db.ahora().strftime("%d-%m-%Y")
    fh = hmod.fecha_hora_huso(db.ahora())
    huella = hmod.huella_alta(id_emisor=EMI, num_serie="ALQ-7", fecha_expedicion=fecha, tipo_factura="F1",
                              cuota_total=cuota_total, importe_total=importe_total, huella_anterior="", fecha_hora_huso=fh)
    return xmlgen.registro_alta(xmlgen.DatosAlta(
        id_emisor=EMI, nombre_razon_emisor=EMPRESA["razon_social"], num_serie="ALQ-7", fecha_expedicion=fecha,
        tipo_factura="F1", descripcion_operacion="Alquiler de vivienda", desglose=desglose, cuota_total=cuota_total,
        importe_total=importe_total, fecha_hora_huso=fh, huella=huella, encadenamiento=None, sistema=SISTEMA,
        destinatarios=[{"nombre": "COMERCIAL INVENTADA, S.L.", "nif": nif_dest}]))


def xsd(el) -> list[str]:
    return esquemas_xsd.validar(xmlgen.mensaje_remision({"nombre": EMPRESA["razon_social"], "nif": EMI}, [xmlgen.a_texto(el)]))


# --- X2: XML y XSD ---------------------------------------------------------------------------------------------------------
def test_xml_exenta_valida_xsd_sin_tipo_ni_cuota():
    el = alta([{"impuesto": "01", "clave_regimen": "01", "operacion_exenta": "E1", "base": "650.00"}], "0.00", "650.00")
    assert xsd(el) == []
    det = el.find("sf:Desglose/sf:DetalleDesglose", NS)
    assert [etree.QName(x).localname for x in det] == ["Impuesto", "ClaveRegimen", "OperacionExenta", "BaseImponibleOimporteNoSujeto"]
    assert det.findtext("sf:OperacionExenta", namespaces=NS) == "E1"
    assert el.findtext("sf:CuotaTotal", namespaces=NS) == "0.00" and el.findtext("sf:ImporteTotal", namespaces=NS) == "650.00"


def test_xml_mixta_y_exportacion_validan_xsd():
    """Sujeta al 21 % + exenta E1 + exportación E2 (clave 02) en una sola factura: CuotaTotal = cuotas, ImporteTotal = bases +
    cuotas."""
    des = [{"impuesto": "01", "clave_regimen": "01", "calificacion": "S1", "tipo_impositivo": "21.00", "base": "100.00",
            "cuota": "21.00"},
           {"impuesto": "01", "clave_regimen": "01", "operacion_exenta": "E1", "base": "650.00"},
           {"impuesto": "01", "clave_regimen": "02", "operacion_exenta": "E2", "base": "50.00"}]
    el = alta(des, "21.00", "821.00")
    assert xsd(el) == []
    dets = el.findall("sf:Desglose/sf:DetalleDesglose", NS)
    assert [d.findtext("sf:CalificacionOperacion", namespaces=NS) for d in dets] == ["S1", None, None]
    assert [d.findtext("sf:OperacionExenta", namespaces=NS) for d in dets] == [None, "E1", "E2"]
    assert [d.findtext("sf:ClaveRegimen", namespaces=NS) for d in dets] == ["01", "01", "02"]
    assert [d.find("sf:TipoImpositivo", NS) is not None for d in dets] == [True, False, False]
    assert [d.find("sf:CuotaRepercutida", NS) is not None for d in dets] == [True, False, False]
    # el simulador local acepta el registro (reglas de importes, exentas y huella)
    assert simulador._validar_alta(el) == ("Correcto", None)


def _con_tipo(el):
    """Añade TipoImpositivo y CuotaRepercutida a la primera fila exenta (en el orden del XSD, que lo admite)."""
    det = el.find("sf:Desglose/sf:DetalleDesglose", NS)
    ex = det.find("sf:OperacionExenta", NS)
    t = etree.Element(f"{{{NS_SF}}}TipoImpositivo")
    t.text = "21.00"
    ex.addnext(t)
    cu = etree.Element(f"{{{NS_SF}}}CuotaRepercutida")
    cu.text = "0.00"
    det.append(cu)
    return el


@pytest.mark.parametrize("desglose, codigo, nif_dest", [
    # exenta con tipo y cuota (1238)
    (None, "1238", CLI),
    # E2/E3 con clave 01 (1199)
    ([{"impuesto": "01", "clave_regimen": "01", "operacion_exenta": "E2", "base": "650.00"}], "1199", CLI),
    ([{"impuesto": "01", "clave_regimen": "01", "operacion_exenta": "E3", "base": "650.00"}], "1199", CLI),
    # E5 con destinatario identificado con NIF español (1289)
    ([{"impuesto": "01", "clave_regimen": "01", "operacion_exenta": "E5", "base": "650.00"}], "1289", CLI),
    # clave 02 en una operación sujeta (1286)
    ([{"impuesto": "01", "clave_regimen": "02", "calificacion": "S1", "tipo_impositivo": "21.00", "base": "650.00",
       "cuota": "136.50"}], "1286", CLI),
])
def test_simulador_reglas_de_exentas(desglose, codigo, nif_dest):
    if desglose is None:
        el = _con_tipo(alta([{"impuesto": "01", "clave_regimen": "01", "operacion_exenta": "E1", "base": "650.00"}],
                            "0.00", "650.00"))
        assert xsd(el) == []   # el XSD lo admite: lo rechaza la validación de negocio
    else:
        cuota = "136.50" if desglose[0].get("cuota") else "0.00"
        el = alta(desglose, cuota, f"{650 + float(cuota):.2f}", nif_dest=nif_dest)
        assert xsd(el) == []
    assert simulador._validar_alta(el) == ("Incorrecto", codigo)


# --- X3: validación del borrador ---------------------------------------------------------------------------------------------
def test_base_sin_iva_y_sin_causa_pide_elegirla():
    d = datos_base([borradores.fila_exenta("650.00")], "650.00", "0.00", "650.00")
    inc = validar(d)
    sin = [i for i in inc if i["codigo"] == "SIN_IVA"]
    assert sin and sin[0]["nivel"] == "bloqueo" and sin[0]["campo"] == "desglose.0"
    assert "no repercute IVA" in sin[0]["mensaje"] and "causa de exención" in sin[0]["mensaje"]
    assert not borradores.puede_emitir(inc)
    # sin desglose (borradores anteriores): el mismo aviso, con la indicación de añadir la base exenta
    inc = validar(datos_base([], "650.00", "0.00", "650.00"))
    assert "SIN_IVA" in codigos(inc, "bloqueo") and "base exenta" in next(i for i in inc if i["codigo"] == "SIN_IVA")["mensaje"]


def test_exenta_e1_solo_pide_confirmar_la_causa():
    d = datos_base([borradores.fila_exenta("650.00", "E1", MENCION_23)], "650.00", "0.00", "650.00")
    inc = validar(d)
    assert not codigos(inc, "bloqueo"), [i for i in inc if i["nivel"] == "bloqueo"]
    conf = [i for i in inc if i["codigo"] == "EXENTA_CAUSA"]
    assert len(conf) == 1 and conf[0]["nivel"] == "confirmar" and not conf[0]["confirmado"]
    assert "E1" in conf[0]["mensaje"] and MENCION_23 in conf[0]["mensaje"]
    assert not borradores.puede_emitir(inc)
    d["confirmaciones"] = [i["clave"] for i in inc if i["nivel"] == "confirmar"]
    assert borradores.puede_emitir(validar(d))


def test_factura_mixta_sujeta_y_exenta():
    d = datos_base([sujeta("21", "100.00", "21.00"), borradores.fila_exenta("650.00", "E1", MENCION_23)],
                   "750.00", "21.00", "771.00", lineas=[{"concepto": "Garaje", "importe": "100.00"},
                                                        {"concepto": "Vivienda", "importe": "650.00"}])
    inc = validar(d)
    assert not codigos(inc, "bloqueo"), [i for i in inc if i["nivel"] == "bloqueo"]
    assert codigos(inc, "confirmar") == {"EXENTA_CAUSA"}
    # el total debe seguir siendo bases + cuotas
    assert "TOTAL_DESCUADRE" in codigos(validar(dict(d, importe_total="750.00", total_a_pagar="750.00")), "bloqueo")


@pytest.mark.parametrize("cambio, codigo", [
    ({"motivo_exencion": None}, "EXENTA_MENCION_FALTA"),
    ({"motivo_exencion": "x" * 501}, "EXENTA_MENCION_LARGA"),
    ({"motivo_exencion": "Exenta 免税"}, "EXENTA_MENCION_CARACTERES"),
    ({"tipo_iva": "21", "cuota": "136.50"}, "EXENTA_CON_CUOTA"),
    ({"operacion_exenta": "E5"}, "EXENTA_NO_SOPORTADA"),
    ({"operacion_exenta": "E7"}, "EXENTA_CAUSA_NO_VALIDA"),
    ({"calificacion": "S1"}, "EXENTA_Y_SUJETA"),
    ({"clave_regimen": "03"}, "CALIFICACION_NO_SOPORTADA"),
    ({"impuesto": "03"}, "CALIFICACION_NO_SOPORTADA"),
    ({"base": "0.00"}, "EXENTA_BASE_CERO"),
    ({"base": None}, "DESGLOSE_INCOMPLETO"),
])
def test_errores_de_una_fila_exenta_bloquean(cambio, codigo):
    fila = dict(borradores.fila_exenta("650.00", "E1", MENCION_23), **cambio)
    inc = validar(datos_base([fila], "650.00", "0.00", "650.00"))
    assert codigo in codigos(inc, "bloqueo"), inc
    assert not borradores.puede_emitir(inc)


def test_e2_y_e3_se_declaran_con_clave_02():
    for causa in ("E2", "E3"):
        fila = borradores.fila_exenta("650.00", causa)
        assert fila["clave_regimen"] == "02" and fila["motivo_exencion"] == borradores.mencion_exencion(causa)
        inc = validar(datos_base([fila], "650.00", "0.00", "650.00"))
        assert not codigos(inc, "bloqueo")
        assert "clave de régimen 02" in next(i for i in inc if i["codigo"] == "EXENTA_CAUSA")["mensaje"]
        # una fila guardada con la clave 01 (p. ej. editada por la API) también vale: al expedir se fija la 02
        inc = validar(datos_base([dict(fila, clave_regimen="01")], "650.00", "0.00", "650.00"))
        assert not codigos(inc, "bloqueo")


def test_siguen_bloqueados_s2_no_sujeta_recargo_e_igic():
    base = datos_base([sujeta("21", "650.00", "136.50")], "650.00", "136.50", "786.50")
    assert "CALIFICACION_NO_SOPORTADA" in codigos(validar(dict(base, desglose=[dict(sujeta("21", "650.00", "136.50"),
                                                                                     calificacion="S2")])), "bloqueo")
    for ind in ("no_sujeta", "inversion_sujeto_pasivo", "recargo_equivalencia", "intracomunitaria"):
        assert f"NO_SOPORTADO_{ind.upper()}" in codigos(validar(dict(base, indicios=[ind])), "bloqueo")
    amb = borradores.evaluar_ambito({"territorio": "comun", "sii": 0, "tipo_contribuyente": "sociedad_IS",
                                     "canarias_ceuta_melilla": 1})
    assert not amb["puede_emitir"] and any("IGIC/IPSI" in m for m in amb["motivos"])
    assert not any("exent" in v.lower() for v in borradores.CASOS_BLOQUEADOS.values())
    assert any("OperacionExenta" in c for c in borradores.CASOS_SOPORTADOS)


def test_exencion_mencionada_sin_base_exenta_pide_confirmar():
    d = datos_base([sujeta("21", "650.00", "136.50")], "650.00", "136.50", "786.50", indicios=["exencion"])
    inc = validar(d)
    assert not codigos(inc, "bloqueo") and "EXENCION_MENCIONADA" in codigos(inc, "confirmar")
    # con una base exenta en el desglose ya no se pide
    d2 = datos_base([sujeta("21", "100.00", "21.00"), borradores.fila_exenta("550.00", "E1")], "650.00", "21.00", "671.00",
                    indicios=["exencion"])
    assert "EXENCION_MENCIONADA" not in codigos(validar(d2))


def test_edicion_de_la_fila_exenta():
    d = datos_base([borradores.fila_exenta("650.00")], "650.00", "0.00", "650.00")
    _, d = borradores.aplicar_cambio(d, "desglose.0.operacion_exenta", "E1")
    _, d = borradores.aplicar_cambio(d, "desglose.0.motivo_exencion", MENCION_23)
    assert borradores.clase_fila(d["desglose"][0]) == "exenta"
    with pytest.raises(ValueError, match="causa de exención"):
        borradores.aplicar_cambio(d, "desglose.0.operacion_exenta", "E9")
    with pytest.raises(ValueError, match="calificación"):
        borradores.aplicar_cambio(d, "desglose.0.calificacion", "X1")
    # la lista completa admite las claves nuevas y comprueba sus valores
    _, d2 = borradores.aplicar_cambio(d, "desglose", [sujeta("21", "100.00", "21.00"), borradores.fila_exenta("550.00", "E4")])
    assert [borradores.clase_fila(x) for x in d2["desglose"]] == ["S1", "exenta"]
    with pytest.raises(ValueError):
        borradores.aplicar_cambio(d, "desglose", [dict(borradores.fila_exenta("550.00", "E1"), operacion_exenta="EX")])
    with pytest.raises(ValueError, match="no admitidos"):
        borradores.aplicar_cambio(d, "desglose", [dict(borradores.fila_exenta("550.00", "E1"), otro="x")])


def test_datos_aprobados_de_una_factura_exenta():
    b = {"datos": json.dumps(datos_base([sujeta("21", "100.00", "21.00"),
                                         dict(borradores.fila_exenta("650.00", "E2"), clave_regimen="01", tipo_iva="",
                                              motivo_exencion=" Exportación exenta (art. 21 Ley 37/1992) ")],
                                        "750.00", "21.00", "771.00"))}
    serie = {"id": 1, "codigo": "A", "patron": "ALQ-{n}", "numero_detectado": 7, "anio_contador": 2026}
    d = emision.datos_aprobados(b, {"nif": EMI, "razon_social": EMPRESA["razon_social"]}, "simulador", "2026-11-10", serie, None)
    assert d["desglose"][0] == {"tipo_iva": "21.00", "base": "100.00", "cuota": "21.00", "calificacion": "S1",
                                "clave_regimen": "01", "impuesto": "01"}
    assert d["desglose"][1] == {"tipo_iva": None, "base": "650.00", "cuota": None, "calificacion": None, "clave_regimen": "02",
                                "impuesto": "01", "operacion_exenta": "E2",
                                "motivo_exencion": "Exportación exenta (art. 21 Ley 37/1992)"}


# --- X4: propuestas de la extracción -----------------------------------------------------------------------------------------
def test_extraccion_corpus_formacion_exenta():
    from pathlib import Path
    pdf = Path(__file__).resolve().parent / "corpus" / "variados" / "var_04_formacion_exenta_letter.pdf"
    ext, d = extraer(pdf.read_bytes())
    assert ext["exencion"]["causa"] == "E1" and "art. 20" in ext["exencion"]["motivo"]
    assert d["desglose"] == [borradores.fila_exenta("1635.00", "E1", ext["exencion"]["motivo"])]
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("1635.00", "0.00", "1635.00")
    assert "exencion" in d["indicios"]


def test_extraccion_alquiler_sin_iva_propone_base_sin_causa():
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER, TOTALES_SIN_IVA))
    assert ext["exencion"] is None
    assert d["desglose"] == [borradores.fila_exenta("650.00")]
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("650.00", "0.00", "650.00")
    inc = validar(d, ext)
    assert "SIN_IVA" in codigos(inc, "bloqueo") and "DESGLOSE_FALTA" not in codigos(inc)


@pytest.mark.parametrize("notas, causa, mencion", [
    (["Operación exenta de IVA según el artículo 20.Uno.23º de la Ley 37/1992."], "E1", "artículo 20.Uno.23º"),
    # la frase sigue en el renglón de debajo
    (["Operación exenta del Impuesto sobre el Valor Añadido en virtud del artículo", "20.Uno.23º de la Ley 37/1992."],
     "E1", "artículo 20.Uno.23º de la Ley 37/1992"),
    (["Exportación exenta de IVA (art. 21 Ley 37/1992)."], "E2", "art. 21"),
    (["Operación exenta (art. 22 LIVA)."], "E3", "art. 22"),
    (["Exenta de IVA según los arts. 23 y 24 de la Ley 37/1992."], "E4", "arts. 23 y 24"),
    # la cita no nombra la ley, pero la frase dice «exenta» y nombra el IVA
    (["Exenta de IVA según el art. 20.Uno.9º."], "E1", "art. 20.Uno.9º"),
    # la ley se nombra antes del artículo
    (["Operación exenta conforme a la Ley 37/1992, art. 20.Uno.23º."], "E1", "Ley 37/1992, art. 20.Uno.23º"),
    # el artículo de la Ley del IVA citado sin la palabra «exenta»
    (["Operación acogida al art. 20.Uno.23º LIVA."], "E1", "art. 20.Uno.23º LIVA"),
])
def test_extraccion_causa_segun_el_articulo(notas, causa, mencion):
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER, TOTALES_SIN_IVA, notas=notas))
    assert ext["exencion"]["causa"] == causa
    fila = d["desglose"][0]
    assert fila["operacion_exenta"] == causa and fila["base"] == "650.00" and fila["tipo_iva"] is None
    assert mencion in fila["motivo_exencion"]   # el texto citado del documento, nunca una mención inventada
    inc = validar(d, ext)
    conf = next(i for i in inc if i["codigo"] == "EXENTA_CAUSA")
    assert conf["nivel"] == "confirmar" and "Propuesta a partir del texto del documento" in conf["mensaje"]


def test_extraccion_entrega_intracomunitaria_queda_bloqueada():
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER, TOTALES_SIN_IVA,
                                 notas=["Entrega exenta de IVA (art. 25 Ley 37/1992)."]))
    assert d["desglose"][0]["operacion_exenta"] == "E5"
    assert "EXENTA_NO_SOPORTADA" in codigos(validar(d, ext), "bloqueo")


def test_propuesta_mixta_y_casos_que_no_cambian():
    """Factura mixta: base impresa mayor que las bases sujetas, cuota de las sujetas y total = base + cuota: la diferencia es
    la base exenta si las líneas de la operación exenta la respaldan (G3, FIS-2/FIX-2). Sin mención de la exención, si los
    importes no cuadran o si ninguna línea la respalda, el desglose no cambia."""
    lineas = [{"concepto": {"valor": c}, "importe": {"valor": i}}
              for c, i in (("Alquiler de la vivienda, noviembre", "650.00"), ("Plaza de garaje, noviembre", "100.00"))]
    ext = {"desglose": [{"tipo_iva": {"valor": "21.00"}, "base": {"valor": "100.00"}, "cuota": {"valor": "21.00"}}],
           "base_imponible": {"valor": "750.00"}, "cuota_iva": {"valor": "21.00"}, "total_factura": {"valor": "771.00"},
           "exencion": {"causa": "E1", "articulo": "20", "motivo": "Exento de IVA (art. 20.Uno.23º Ley 37/1992)",
                        "texto": "x"},
           "emisor": {}, "destinatario": {}, "lineas": lineas}
    ext["emisor"] = ext["destinatario"] = {k: {"valor": None} for k in ("nombre", "nif", "domicilio")}
    d = borradores.datos_desde_extraccion(ext)
    assert [borradores.clase_fila(x) for x in d["desglose"]] == ["S1", "exenta"]
    # sin líneas que respalden la diferencia (la base leída puede ser un SUBTOTAL), no se propone la base exenta
    assert len(borradores.datos_desde_extraccion(dict(ext, lineas=[]))["desglose"]) == 1
    assert d["desglose"][1] == borradores.fila_exenta("650.00", "E1", "Exento de IVA (art. 20.Uno.23º Ley 37/1992)")
    assert not codigos(validar(dict(datos_base(d["desglose"], "750.00", "21.00", "771.00"), lineas=[])), "bloqueo")
    # sin mención de exención: no se inventa una base exenta (el descuadre lo señala la validación)
    assert len(borradores.datos_desde_extraccion(dict(ext, exencion=None))["desglose"]) == 1
    # total que no cuadra con base + cuota: tampoco
    assert len(borradores.datos_desde_extraccion(dict(ext, total_factura={"valor": "800.00"}))["desglose"]) == 1
    # factura con IVA que menciona una exención en otro contexto: el desglose sujeto no cambia
    ext2 = dict(ext, base_imponible={"valor": "100.00"}, total_factura={"valor": "121.00"})
    assert borradores.datos_desde_extraccion(ext2)["desglose"] == [sujeta("21.00", "100.00", "21.00")]


# --- X-R1 (revisión): un «exento» sin el artículo de la Ley del IVA no da causa ---------------------------------------------
@pytest.mark.parametrize("notas, iva", [
    # «exento» de otro contexto
    (["El arrendador queda exento de responsabilidad por daños en el inmueble."], False),
    (["El inquilino queda exento de depositar fianza adicional."], False),
    (["Exento de retención de IRPF (art. 75.3 RIRPF)."], False),
    # el artículo 20–25 es de otra norma
    (["Exento según el art. 25 de la Ley 49/2002 de mecenazgo."], False),
    (["Exento de responsabilidad según el art. 21 del contrato."], False),
    (["Exenta de IVA según el art. 20 del Reglamento del IVA."], True),
    # exención del IVA sin artículo: no se inventa la mención del art. 20
    (["Operación exenta de IVA."], True),
    (["Operación exenta de IVA. Renta actualizada según el art. 22 del contrato."], True),
])
def test_exento_sin_articulo_de_la_ley_del_iva_no_propone_causa(notas, iva):
    """Sin IVA y sin el artículo de la Ley 37/1992, la base queda sin causa y SIN_IVA sigue bloqueando (antes, cualquier
    «exento» proponía E1 con la mención «art. 20 Ley 37/1992» y el bloqueo pasaba a ser una confirmación más)."""
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER, TOTALES_SIN_IVA, notas=notas))
    assert ext["exencion"]["causa"] is None and ext["exencion"]["motivo"] is None and ext["exencion"]["iva"] is iva
    assert d["desglose"] == [borradores.fila_exenta("650.00")]
    inc = validar(d, ext)
    assert "SIN_IVA" in codigos(inc, "bloqueo") and "EXENTA_CAUSA" not in codigos(inc)
    assert not borradores.puede_emitir(inc)
    msg = next(i for i in inc if i["codigo"] == "SIN_IVA")["mensaje"]
    assert "no repercute IVA" in msg and "art. 20 Ley 37/1992" not in msg
    assert ("no cita el artículo de la Ley 37/1992" in msg and notas[0][:40] in msg) is iva
    # el usuario elige la causa y escribe la mención: la confirmación no la presenta como leída del documento
    _, d = borradores.aplicar_cambio(d, "desglose.0.operacion_exenta", "E1")
    _, d = borradores.aplicar_cambio(d, "desglose.0.motivo_exencion", MENCION_23)
    inc = validar(d, ext)
    conf = next(i for i in inc if i["codigo"] == "EXENTA_CAUSA")
    assert "SIN_IVA" not in codigos(inc) and "Propuesta a partir del texto del documento" not in conf["mensaje"]


def test_sin_cita_de_exencion_no_hay_propuesta():
    # un artículo 20–25 de otra norma sin mencionar ninguna exención: ni siquiera hay propuesta
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER, TOTALES_SIN_IVA,
                                 notas=["Renta sin IVA, actualizada según el art. 22 del contrato."]))
    assert ext["exencion"] is None and d["desglose"] == [borradores.fila_exenta("650.00")]
    assert "SIN_IVA" in codigos(validar(d, ext), "bloqueo")
    # «IVA EXENTO» en el cuadro de totales, sin artículo: base sin causa
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER,
                                 [("BASE IMPONIBLE", "650,00"), ("IVA EXENTO", "0,00"), ("TOTAL FACTURA", "650,00")]))
    assert ext["exencion"]["causa"] is None and d["desglose"] == [borradores.fila_exenta("650.00")]
    assert "SIN_IVA" in codigos(validar(d, ext), "bloqueo")
    # factura con IVA y un «exento» de otro contexto: el desglose sujeto no cambia y solo se pide confirmar la mención
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER,
                                 [("BASE IMPONIBLE", "650,00"), ("IVA 21%", "136,50"), ("TOTAL FACTURA", "786,50")],
                                 notas=["El arrendador queda exento de responsabilidad por daños en el inmueble."]))
    assert [borradores.clase_fila(x) for x in d["desglose"]] == ["S1"]
    inc = validar(d, ext)
    assert not codigos(inc, "bloqueo") and "EXENCION_MENCIONADA" in codigos(inc, "confirmar")


def test_propuesta_mixta_sin_causa_y_extraccion_anterior():
    ext = {"desglose": [{"tipo_iva": {"valor": "21.00"}, "base": {"valor": "100.00"}, "cuota": {"valor": "21.00"}}],
           "base_imponible": {"valor": "750.00"}, "cuota_iva": {"valor": "21.00"}, "total_factura": {"valor": "771.00"},
           "lineas": []}
    ext["emisor"] = ext["destinatario"] = {k: {"valor": None} for k in ("nombre", "nif", "domicilio")}
    # exención del IVA sin artículo: la base exenta se añade sin causa (bloqueada)
    d = borradores.datos_desde_extraccion(dict(ext, exencion={"causa": None, "iva": True, "texto": "Exenta de IVA"}))
    assert d["desglose"][1] == borradores.fila_exenta("650.00")
    assert "SIN_IVA" in codigos(validar(dict(datos_base(d["desglose"], "750.00", "21.00", "771.00"), lineas=[])), "bloqueo")
    # «exento» de otro contexto: no se crea ninguna base exenta (el descuadre lo señala la validación)
    d = borradores.datos_desde_extraccion(dict(ext, exencion={"causa": None, "iva": False, "texto": "exento de fianza"}))
    assert len(d["desglose"]) == 1
    # extracción de una versión anterior (solo el indicio): ninguna causa propuesta
    viejo = dict(ext, indicios={"exencion": {"texto": "Exento"}})
    assert len(borradores.datos_desde_extraccion(viejo)["desglose"]) == 1
    viejo = dict(viejo, desglose=[], base_imponible={"valor": "650.00"}, cuota_iva={"valor": "0.00"},
                 total_factura={"valor": "650.00"})
    assert borradores.datos_desde_extraccion(viejo)["desglose"] == [borradores.fila_exenta("650.00")]
    # una causa sin artículo citado (p. ej. guardada por una versión anterior del extractor) tampoco se propone
    sin_art = dict(viejo, exencion={"causa": "E1", "articulo": None, "motivo": None, "texto": "exento de fianza"})
    d = borradores.datos_desde_extraccion(sin_art)
    assert d["desglose"] == [borradores.fila_exenta("650.00")]
    _, d = borradores.aplicar_cambio(d, "desglose.0.operacion_exenta", "E1")
    _, d = borradores.aplicar_cambio(d, "desglose.0.motivo_exencion", MENCION_23)
    conf = next(i for i in validar(d, sin_art) if i["codigo"] == "EXENTA_CAUSA")
    assert "Propuesta a partir del texto del documento" not in conf["mensaje"]


# --- X5/X8: flujo completo en el SIMULADOR local ------------------------------------------------------------------------------
def _instalar(c, patron="ALQ-{n}", siguiente=7):
    r = c.post("/api/instalar", {"email": "admin@ejemplo.test", "nombre": "Administración", "clave": "clave-segura-123",
                                 "empresa": EMPRESA})
    d = c.ok(r)
    c.csrf = d["csrf"]
    eid = d["empresas"][0]["id"]
    configurar(c, eid, patron, siguiente)
    return eid


def _borrador(c, eid, nombre, pdf):
    r = c.tc.post(f"/api/empresas/{eid}/documentos", files=[("archivos", (nombre, pdf, "application/pdf"))],
                  headers={"X-CSRF-Token": c.csrf})
    did = c.ok(r)[0]["documento_id"]
    c.ok(c.post(f"/api/documentos/{did}/clasificacion", {"clasificacion": "preparatorio", "motivo": "Borrador de la plantilla"}))
    bid = c.ok(c.post(f"/api/documentos/{did}/borrador"))["borrador_id"]
    return bid, c.ok(c.get(f"/api/borradores/{bid}"))


def _confirmar_y_emitir(c, bid, clave):
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    claves = [i["clave"] for i in b["incidencias"] if i["nivel"] == "confirmar"]
    b = c.ok(c.post(f"/api/borradores/{bid}/confirmaciones", {"claves": claves}))
    assert b["puede_emitir"], [i for i in b["incidencias"] if i["nivel"] != "aviso"]
    return c.ok(c.post(f"/api/borradores/{bid}/emitir", {"entorno": "simulador", "clave_idempotencia": clave, "confirmo": True}))["factura_id"]


def _enviar(eid):
    con = db.conectar()
    try:
        return remision.procesar(con, eid, "simulador", forzar_flujo=True)
    finally:
        con.close()


def _texto_pdf(pdf: bytes) -> str:
    """Texto horizontal del PDF (sin las letras giradas de la marca de agua, que se intercalan con el texto)."""
    with pdfplumber.open(io.BytesIO(pdf)) as d:
        return "\n".join(p.filter(_no_girado).extract_text() or "" for p in d.pages)


def test_flujo_alquiler_exento_hasta_el_simulador(app_cliente):
    c = app_cliente
    eid = _instalar(c)
    hoy = db.ahora().strftime("%d/%m/%Y")
    bid, b = _borrador(c, eid, "alquiler_noviembre.pdf", factura_pdf("ALQ-7", hoy, LINEA_ALQUILER, TOTALES_SIN_IVA))
    # Sin IVA y sin mención: base sin causa, bloqueo que pide elegirla
    assert b["datos"]["desglose"] == [borradores.fila_exenta("650.00")]
    sin = [i for i in b["incidencias"] if i["codigo"] == "SIN_IVA"]
    assert sin and sin[0]["nivel"] == "bloqueo" and sin[0]["campo"] == "desglose.0" and not b["puede_emitir"]
    # La vista previa del borrador muestra la base sin IVA pendiente de causa (y sin QR)
    prev = c.get(f"/api/borradores/{bid}/vista-previa.pdf").content
    assert "Base sin IVA" in _texto_pdf(prev) and leer_qrs(prev, 1) == [[]]
    # El usuario elige E1 (como haría la interfaz: lista del desglose completa) y precisa la mención
    fila = borradores.fila_exenta("650.00", "E1")
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "desglose", "valor": [fila], "motivo": "Alquiler de vivienda: exento"},
        {"campo": "desglose.0.motivo_exencion", "valor": MENCION_23, "motivo": "Precepto concreto"}]}))
    assert b["datos"]["desglose"][0]["operacion_exenta"] == "E1"
    assert not [i for i in b["incidencias"] if i["nivel"] == "bloqueo"], b["incidencias"]
    assert "EXENTA_CAUSA" in {i["codigo"] for i in b["incidencias"] if i["nivel"] == "confirmar"}
    assert [x["campo"] for x in b["cambios"]][-2:] == ["desglose", "desglose.0.motivo_exencion"]
    fid = _confirmar_y_emitir(c, bid, "exenta-1")
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert all(f["verificacion"].values()) and f["verificacion"]["mencion_exencion"]
    assert f["datos"]["cuota_total"] == "0.00" and f["datos"]["importe_total"] == "650.00"
    assert f["url_qr"].endswith("&importe=650.00")
    # XML: OperacionExenta y solo la base; valida contra el XSD
    xml = c.get(f"/api/registros/{f['registros'][0]['id']}/xml").text
    reg = etree.fromstring(xml.encode())
    det = reg.find("sf:Desglose/sf:DetalleDesglose", NS)
    assert det.findtext("sf:OperacionExenta", namespaces=NS) == "E1" and det.find("sf:CalificacionOperacion", NS) is None
    assert det.find("sf:TipoImpositivo", NS) is None and det.find("sf:CuotaRepercutida", NS) is None
    assert (reg.findtext("sf:CuotaTotal", namespaces=NS), reg.findtext("sf:ImporteTotal", namespaces=NS)) == ("0.00", "650.00")
    assert xsd(reg) == []
    # Remisión al SIMULADOR local: aceptado
    _enviar(eid)
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert f["registros"][0]["estado_envio"] == "aceptado", f["registros"][0]
    con = db.conectar()
    coh = auditoria.coherencia_factura(con, fid)
    assert coh["ok"], coh
    assert auditoria.verificar_cadena(con, eid, "simulador")["ok"]
    # PDF: base exenta con su causa, mención legal, total y QR (una vez, con el ImporteTotal)
    pdf = c.get(f"/api/facturas/{fid}/pdf").content
    txt = _texto_pdf(pdf)
    assert "Base exenta (E1 · art. 20 Ley 37/1992)" in txt and MENCION_23 in txt and "650,00" in txt
    assert leer_qrs(pdf, 1)[0] == [f["url_qr"]]
    assert f["factura"]["pdf_estrategia"] == "reconstruccion"


def test_flujo_factura_mixta_y_rectificativa(app_cliente):
    """Factura con una base sujeta y otra exenta (el usuario separa las bases en el editor), expedida y aceptada en el
    simulador; su rectificativa conserva la fila exenta."""
    c = app_cliente
    eid = _instalar(c)
    hoy = db.ahora().strftime("%d/%m/%Y")
    lineas = [("Alquiler de la vivienda, noviembre 2026", "1,00", "mes", "650,00", "650,00"),
              ("Plaza de garaje, noviembre 2026", "1,00", "mes", "100,00", "100,00")]
    pdf = factura_pdf("ALQ-7", hoy, lineas, [("BASE IMPONIBLE", "750,00"), ("I.V.A. 21% s/ 100,00", "21,00"),
                                              ("TOTAL FACTURA", "771,00")],
                      notas=["Alquiler de vivienda exento de IVA (art. 20.Uno.23º Ley 37/1992)."])
    bid, b = _borrador(c, eid, "mixta.pdf", pdf)
    # el extractor no separa las bases: la factura no puede expedirse tal cual
    assert not b["puede_emitir"]
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "desglose", "valor": [sujeta("21.00", "100.00", "21.00"),
                                        borradores.fila_exenta("650.00", "E1", MENCION_23)], "motivo": "Garaje sujeto, vivienda exenta"},
        {"campo": "base_imponible", "valor": "750.00", "motivo": "Suma de bases"},
        {"campo": "cuota_total", "valor": "21.00", "motivo": "Cuota del garaje"},
        {"campo": "importe_total", "valor": "771.00", "motivo": "Total impreso"},
        {"campo": "total_a_pagar", "valor": "771.00", "motivo": "Total impreso"}]}))
    assert not [i for i in b["incidencias"] if i["nivel"] == "bloqueo"], b["incidencias"]
    fid = _confirmar_y_emitir(c, bid, "mixta-1")
    _enviar(eid)
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert f["registros"][0]["estado_envio"] == "aceptado" and all(f["verificacion"].values())
    con = db.conectar()
    assert auditoria.coherencia_factura(con, fid)["ok"]
    xml = etree.fromstring(c.get(f"/api/registros/{f['registros'][0]['id']}/xml").content)
    assert [d.findtext("sf:OperacionExenta", namespaces=NS) for d in xml.findall("sf:Desglose/sf:DetalleDesglose", NS)] == [None, "E1"]
    assert (xml.findtext("sf:CuotaTotal", namespaces=NS), xml.findtext("sf:ImporteTotal", namespaces=NS)) == ("21.00", "771.00")
    txt = _texto_pdf(c.get(f"/api/facturas/{fid}/pdf").content)
    for t in ("Base imponible al 21 %", "IVA 21 %", "Base exenta (E1 · art. 20 Ley 37/1992)", MENCION_23, "771,00"):
        assert t in txt, t
    # Rectificativa por diferencias: la fila exenta se conserva (base 0, causa y mención)
    rb = c.ok(c.post(f"/api/facturas/{fid}/rectificativa", {"tipo": "R4", "motivo": "Revisión de la renta"}))["borrador_id"]
    r = c.ok(c.get(f"/api/borradores/{rb}"))
    assert [borradores.clase_fila(x) for x in r["datos"]["desglose"]] == ["S1", "exenta"]
    assert r["datos"]["desglose"][1] == borradores.fila_exenta("0.00", "E1", MENCION_23)
    assert not {"EXENTA_BASE_CERO", "SIN_IVA"} & {i["codigo"] for i in r["incidencias"]}


def test_factura_exenta_sobrevive_a_copia_y_restauracion(app_cliente):
    """La factura exenta, su registro y su PDF (con la mención) se reconstruyen del diario tras restaurar una copia anterior."""
    c = app_cliente
    eid = _instalar(c)
    con = db.conectar()
    copia = copias.crear_copia(con)
    hoy = db.ahora().strftime("%d/%m/%Y")
    bid, _ = _borrador(c, eid, "alquiler.pdf", factura_pdf("ALQ-7", hoy, LINEA_ALQUILER, TOTALES_SIN_IVA,
                                                           notas=["Operación exenta de IVA según el artículo 20.Uno.23º de la Ley 37/1992."]))
    fid = _confirmar_y_emitir(c, bid, "exenta-copia")
    _enviar(eid)
    antes = db.fila(con, "SELECT datos, datos_sha256, pdf_sha256 FROM facturas WHERE id=?", (fid,))
    xml_antes = db.fila(con, "SELECT xml FROM registros WHERE factura_id=?", (fid,))["xml"]
    con.close()
    r = copias.restaurar(copia)
    assert r["facturas_recuperadas"] == 1 and r["pdf_irrecuperables"] == [], r
    con = db.conectar()
    despues = db.fila(con, "SELECT datos, datos_sha256, pdf_sha256 FROM facturas WHERE id=?", (fid,))
    assert despues == antes
    assert json.loads(despues["datos"])["desglose"][0]["operacion_exenta"] == "E1"
    assert db.fila(con, "SELECT xml, estado_envio FROM registros WHERE factura_id=?", (fid,)) == {"xml": xml_antes,
                                                                                               "estado_envio": "aceptado"}
    # el PDF definitivo se regenera idéntico desde la versión inmutable (mismo SHA-256)
    assert copias.regenerar_pdf(con, fid)["identico"]
    assert auditoria.coherencia_factura(con, fid)["ok"]
