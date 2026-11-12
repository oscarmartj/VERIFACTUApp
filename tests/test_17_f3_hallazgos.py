"""Regresiones de los hallazgos X-R2, X-R3, X-R4 y X-R5 de la revisión adversarial de las operaciones exentas.

- X-R2: la mención propuesta a partir del documento es solo la frase de la exención; no arrastra la frase siguiente del
  mismo renglón aunque cite otra norma («… (art. 20.Uno.23º LIVA). Gastos de comunidad incluidos (art. 20 LAU).»).
- X-R3: factura mixta (sujeta y exenta) con los diseños habituales del cuadro de totales («BASE IMPONIBLE 750 / BASE 21 %
  100 / IVA 21 % 21», «BASE IMPONIBLE 750 / I.V.A. 21 % s/ 100,00 21,00», «BASE EXENTA 650 / BASE 4 % 100 / IVA 4 % 4»):
  el borrador separa la base exenta solo si el documento cita el artículo de la Ley del IVA y todo cuadra al céntimo
  (total = bases sujetas + cuotas + base exenta). Unas tasas, suplidos u honorarios «exentos» sin ese artículo nunca se
  convierten en base exenta.
- X-R4: la mención con saltos de línea CRLF o CR o con tabuladores se guarda y valida como con LF; una mención sin letras
  ni cifras («.») no vale.
- X-R5: el evaluador del corpus cuenta un fallo del desglose si el borrador propone filas exentas o sin calificar que el
  desglose esperado no tiene (y al revés), sin añadir campos nuevos a la puntuación.

Todos los datos son ficticios (NIF con código de provincia 00). Solo se usa el simulador local.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import Cliente  # noqa: F401  (fixture app_cliente)

from app import borradores, db
from test_16_x_exentas import (LINEA_ALQUILER, MENCION_23, TOTALES_SIN_IVA, _borrador, _confirmar_y_emitir, _instalar,
                               codigos, datos_base, extraer, factura_pdf, sujeta, validar)

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import evaluar_extraccion as ev  # noqa: E402


# --- X-R2: la mención citada es solo la frase de la exención ------------------------------------------------------------------
@pytest.mark.parametrize("notas, mencion", [
    # la frase siguiente cita otra ley (la de Arrendamientos Urbanos)
    (["Arrendamiento de vivienda exento de IVA (art. 20.Uno.23º LIVA). Gastos de comunidad incluidos (art. 20 LAU)."],
     "Arrendamiento de vivienda exento de IVA (art. 20.Uno.23º LIVA)."),
    # la frase continúa en el renglón de debajo y, tras ella, empieza otra
    (["Arrendamiento de vivienda exento de IVA según el artículo",
      "20.Uno.23º de la Ley 37/1992. Gastos de comunidad según art. 20 LAU."],
     "Arrendamiento de vivienda exento de IVA según el artículo 20.Uno.23º de la Ley 37/1992."),
    # una frase anterior en el mismo renglón
    (["Gracias por su confianza. Operación exenta de IVA (art. 20.Uno.23º Ley 37/1992)."],
     "Operación exenta de IVA (art. 20.Uno.23º Ley 37/1992)."),
    # una cláusula del contrato tras un punto y coma o tras el punto
    (["Exenta de IVA (art. 20.Uno.23º LIVA); renta según art. 22 del contrato."], "Exenta de IVA (art. 20.Uno.23º LIVA)"),
    (["Exenta de IVA (art. 20.Uno.23º LIVA). Renta actualizada según el art. 22 del contrato."],
     "Exenta de IVA (art. 20.Uno.23º LIVA)."),
    # el artículo citado sin «exenta»: su frase, sin la siguiente
    (["Operación acogida al art. 20.Uno.23º LIVA. Pago por domiciliación."], "Operación acogida al art. 20.Uno.23º LIVA."),
    # se conservan: la frase de la exención seguida de la del artículo, y el apartado escrito con punto («20. Uno. 23º»)
    (["Operación exenta de IVA. Art. 20.Uno.23º Ley 37/1992."], "Operación exenta de IVA. Art. 20.Uno.23º Ley 37/1992."),
    (["Exenta de IVA según el art. 20. Uno. 23º de la Ley 37/1992."],
     "Exenta de IVA según el art. 20. Uno. 23º de la Ley 37/1992."),
])
def test_x_r2_mencion_citada_es_solo_la_frase_de_la_exencion(notas, mencion):
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", LINEA_ALQUILER, TOTALES_SIN_IVA, notas=notas))
    assert ext["exencion"]["causa"] == "E1" and ext["exencion"]["motivo"] == mencion
    assert d["desglose"] == [borradores.fila_exenta("650.00", "E1", mencion)]
    for ajeno in ("LAU", "contrato", "Gracias", "domiciliación"):
        assert ajeno not in d["desglose"][0]["motivo_exencion"]
    # la confirmación sigue citando el texto leído del documento
    conf = next(i for i in validar(d, ext) if i["codigo"] == "EXENTA_CAUSA")
    assert f"Mención en la factura: «{mencion}»" in conf["mensaje"]
    assert "Propuesta a partir del texto del documento" in conf["mensaje"]


# --- X-R3: factura mixta ------------------------------------------------------------------------------------------------------
LINEAS_MIXTA = [("Alquiler de la vivienda, noviembre 2026", "1,00", "mes", "650,00", "650,00"),
                ("Plaza de garaje, noviembre 2026", "1,00", "mes", "100,00", "100,00")]
NOTA_ALQUILER = ["Alquiler de vivienda exento de IVA (art. 20.Uno.23º Ley 37/1992)."]
LINEAS_ACADEMIA = [("Curso de inglés noviembre", "1,00", "mes", "650,00", "650,00"),
                   ("Libro de texto", "1,00", "ud", "100,00", "100,00")]
NOTA_ACADEMIA = ["Enseñanza exenta de IVA (art. 20.Uno.9º Ley 37/1992)."]
HONORARIOS = [("Honorarios gestión transferencia vehículo", "1,00", "ud", "100,00", "100,00"),
              ("Tasa DGT transferencia (exenta)", "1,00", "ud", "55,70", "55,70")]


@pytest.mark.parametrize("lineas, totales, notas, tipo, cuota, total", [
    # base imponible total y base del tipo
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "771,00")],
     NOTA_ALQUILER, "21.00", "21.00", "771.00"),
    # base exenta rotulada y total de bases
    (LINEAS_MIXTA, [("BASE EXENTA", "650,00"), ("BASE IMPONIBLE 21%", "100,00"), ("CUOTA IVA 21%", "21,00"),
                    ("TOTAL BASE IMPONIBLE", "750,00"), ("TOTAL FACTURA", "771,00")], NOTA_ALQUILER, "21.00", "21.00", "771.00"),
    # cuota calculada «s/» una base distinta de la total (el diseño de tests/test_16_x_exentas.py)
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("I.V.A. 21% s/ 100,00", "21,00"), ("TOTAL FACTURA", "771,00")],
     NOTA_ALQUILER, "21.00", "21.00", "771.00"),
    # academia: enseñanza exenta y libro al 4 %
    (LINEAS_ACADEMIA, [("BASE IMPONIBLE", "750,00"), ("IVA 4% s/ 100,00", "4,00"), ("TOTAL FACTURA", "754,00")],
     NOTA_ACADEMIA, "4.00", "4.00", "754.00"),
    (LINEAS_ACADEMIA, [("BASE EXENTA", "650,00"), ("BASE 4%", "100,00"), ("IVA 4%", "4,00"), ("TOTAL FACTURA", "754,00")],
     NOTA_ACADEMIA, "4.00", "4.00", "754.00"),
])
def test_x_r3_factura_mixta_se_separa_si_cuadra_al_centimo(lineas, totales, notas, tipo, cuota, total):
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", lineas, totales, notas=notas))
    mencion = ext["exencion"]["motivo"]
    assert d["desglose"] == [sujeta(tipo, "100.00", cuota), borradores.fila_exenta("650.00", "E1", mencion)]
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("750.00", cuota, total)
    # total impreso = base sujeta + cuota + base exenta, al céntimo
    assert Decimal(d["importe_total"]) == sum(Decimal(x["base"]) for x in d["desglose"]) + Decimal(d["cuota_total"])
    inc = validar(d, ext)
    assert not codigos(inc, "bloqueo"), [i for i in inc if i["nivel"] == "bloqueo"]
    assert "EXENTA_CAUSA" in codigos(inc, "confirmar") and "EXENCION_MENCIONADA" not in codigos(inc)
    assert not borradores.puede_emitir(inc)   # la causa se confirma siempre


@pytest.mark.parametrize("lineas, totales, notas", [
    # tasas, suplidos u honorarios «exentos» sin el artículo de la Ley del IVA: nunca una base exenta
    (HONORARIOS, [("BASE IMPONIBLE", "155,70"), ("IVA 21% s/ 100,00", "21,00"), ("TOTAL FACTURA", "176,70")],
     ["Tasas exentas de IVA."]),
    (HONORARIOS, [("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("EXENTO", "55,70"), ("BASE IMPONIBLE", "155,70"),
                  ("TOTAL FACTURA", "176,70")], []),
    (HONORARIOS, [("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("BASE IMPONIBLE", "155,70"), ("TOTAL FACTURA", "176,70")],
     ["La gestoría queda exenta de responsabilidad por retrasos de la DGT."]),
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "771,00")],
     ["Alquiler de vivienda exento de IVA."]),
    # suplidos en el cuadro, aunque se cite el artículo
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("IVA 21% s/ 100,00", "21,00"), ("SUPLIDOS", "50,00"),
                    ("TOTAL FACTURA", "821,00")], NOTA_ALQUILER + ["Suplidos: tasas de registro pagadas por cuenta del cliente."]),
    # cifras que no cuadran al céntimo: la base exenta impresa no es la diferencia, total o cuota distintos
    (LINEAS_MIXTA, [("BASE EXENTA", "600,00"), ("BASE IMPONIBLE", "750,00"), ("BASE 21%", "100,00"), ("IVA 21%", "21,00"),
                    ("TOTAL FACTURA", "771,00")], NOTA_ALQUILER),
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("IVA 21% s/ 100,00", "21,00"), ("TOTAL FACTURA", "780,00")], NOTA_ALQUILER),
    (LINEAS_MIXTA, [("BASE IMPONIBLE", "750,00"), ("IVA 21% s/ 100,00", "22,00"), ("TOTAL FACTURA", "772,00")], NOTA_ALQUILER),
    # una diferencia sin base exenta ni base total impresas (portes) no se toma como exenta
    (LINEAS_MIXTA + [("Portes", "1,00", "ud", "30,00", "30,00")],
     [("BASE 21%", "100,00"), ("IVA 21%", "21,00"), ("PORTES", "30,00"), ("TOTAL FACTURA", "151,00")], NOTA_ALQUILER),
    # «SUMA» leída como una línea de conceptos: no se separa (limitación documentada)
    (LINEAS_MIXTA, [("SUMA", "750,00"), ("IVA 21% s/ 100,00", "21,00"), ("TOTAL FACTURA", "771,00")], NOTA_ALQUILER),
])
def test_x_r3_sin_cita_o_sin_cuadre_no_se_inventa_la_base_exenta(lineas, totales, notas):
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", lineas, totales, notas=notas))
    assert not [x for x in d["desglose"] if borradores.clase_fila(x) in ("exenta", "sin_calificar")], d["desglose"]
    assert not borradores.puede_emitir(validar(d, ext))
    assert codigos(validar(d, ext), "bloqueo")


def test_x_r3_factura_sujeta_con_mencion_no_cambia():
    ext, d = extraer(factura_pdf("ALQ-7", "10/11/2026", [LINEAS_MIXTA[1]],
                                 [("BASE IMPONIBLE", "100,00"), ("IVA 21%", "21,00"), ("TOTAL FACTURA", "121,00")],
                                 notas=NOTA_ALQUILER))
    assert d["desglose"] == [sujeta("21.00", "100.00", "21.00")]
    assert (d["base_imponible"], d["cuota_total"], d["importe_total"]) == ("100.00", "21.00", "121.00")


# --- X-R4: mención con CRLF, CR o tabuladores; mención sin contenido ----------------------------------------------------------
@pytest.mark.parametrize("mencion", [".", " - ", "…", "«»", ". ; ,", "\t.\r\n"])
def test_x_r4_mencion_sin_letras_ni_cifras_bloquea(mencion):
    inc = validar(datos_base([borradores.fila_exenta("650.00", "E1", mencion)], "650.00", "0.00", "650.00"))
    falta = [i for i in inc if i["codigo"] == "EXENTA_MENCION_FALTA"]
    assert falta and falta[0]["nivel"] == "bloqueo" and falta[0]["campo"] == "desglose.0.motivo_exencion"
    assert "art. 6.1.j RD 1619/2012" in falta[0]["mensaje"]
    assert not borradores.puede_emitir(inc)


def test_x_r4_mencion_con_signos_y_texto_sigue_valiendo():
    for mencion in ("Exenta.", "Art. 20 LIVA", "E1"):
        inc = validar(datos_base([borradores.fila_exenta("650.00", "E1", mencion)], "650.00", "0.00", "650.00"))
        assert not codigos(inc, "bloqueo"), mencion


@pytest.mark.parametrize("mencion", ["Exenta de IVA\r\nart. 20 LIVA", "Exenta de IVA\rart. 20 LIVA", "Exenta\tde IVA (art. 20 LIVA)",
                                     "Exenta de IVA\nart. 20 LIVA"])
def test_x_r4_saltos_de_linea_y_tabuladores_como_lf(mencion):
    inc = validar(datos_base([borradores.fila_exenta("650.00", "E1", mencion)], "650.00", "0.00", "650.00"))
    assert not codigos(inc, "bloqueo"), [i for i in inc if i["nivel"] == "bloqueo"]
    # la edición la guarda con LF y espacios, como la envía la interfaz
    d = datos_base([borradores.fila_exenta("650.00", "E1")], "650.00", "0.00", "650.00")
    _, d = borradores.aplicar_cambio(d, "desglose.0.motivo_exencion", mencion)
    guardada = d["desglose"][0]["motivo_exencion"]
    assert "\r" not in guardada and "\t" not in guardada
    assert guardada == mencion.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    _, d = borradores.aplicar_cambio(d, "desglose", [borradores.fila_exenta("650.00", "E1", mencion)])
    assert d["desglose"][0]["motivo_exencion"] == guardada


def test_x_r4_flujo_mencion_crlf_se_expide_con_lf(app_cliente):
    """Por la API (cliente que envía CRLF) y con un borrador guardado antes del cambio (CRLF en la base de datos): la
    factura se expide en el SIMULADOR con la mención en forma LF, impresa y comprobada en el PDF."""
    c = app_cliente
    eid = _instalar(c)
    hoy = db.ahora().strftime("%d/%m/%Y")
    bid, _ = _borrador(c, eid, "alquiler.pdf", factura_pdf("ALQ-7", hoy, LINEA_ALQUILER, TOTALES_SIN_IVA))
    b = c.ok(c.patch(f"/api/borradores/{bid}", {"cambios": [
        {"campo": "desglose", "valor": [borradores.fila_exenta("650.00", "E1")], "motivo": "Alquiler de vivienda"},
        {"campo": "desglose.0.motivo_exencion", "valor": "Operación exenta de IVA\r\n(art. 20.Uno.23º Ley 37/1992)",
         "motivo": "Precepto"}]}))
    assert b["datos"]["desglose"][0]["motivo_exencion"] == "Operación exenta de IVA\n(art. 20.Uno.23º Ley 37/1992)"
    assert not [i for i in b["incidencias"] if i["nivel"] == "bloqueo"], b["incidencias"]
    # borrador guardado por una versión anterior con CRLF y un tabulador: se valida y se expide como LF
    con = db.conectar()
    try:
        fila = db.fila(con, "SELECT datos FROM borradores WHERE id=?", (bid,))
        datos = json.loads(fila["datos"])
        datos["desglose"][0]["motivo_exencion"] = "Operación\texenta de IVA\r\n(art. 20.Uno.23º Ley 37/1992)"
        con.execute("UPDATE borradores SET datos=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), bid))
        con.commit()
    finally:
        con.close()
    b = c.ok(c.get(f"/api/borradores/{bid}"))
    assert "EXENTA_MENCION_CARACTERES" not in {i["codigo"] for i in b["incidencias"]}
    fid = _confirmar_y_emitir(c, bid, "crlf-1")
    f = c.ok(c.get(f"/api/facturas/{fid}"))
    assert f["datos"]["desglose"][0]["motivo_exencion"] == "Operación exenta de IVA\n(art. 20.Uno.23º Ley 37/1992)"
    assert f["verificacion"]["mencion_exencion"] and all(f["verificacion"].values())


# --- X-R5: el evaluador cuenta las filas exentas o sin calificar ---------------------------------------------------------------
def _pdf(tmp_path, nombre, *args, **kw) -> Path:
    p = tmp_path / nombre
    p.write_bytes(factura_pdf("ALQ-7", "10/11/2026", *args, **kw))
    return p


SUJETA_ESPERADA = {"tipo_iva": "21.00", "base": "650.00", "cuota": "136.50"}


def test_x_r5_fila_exenta_de_mas_en_un_documento_sujeto_falla(tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, "sujeta.pdf", LINEA_ALQUILER,
               [("BASE IMPONIBLE", "650,00"), ("IVA 21%", "136,50"), ("TOTAL FACTURA", "786,50")])
    esp = {"desglose": [SUJETA_ESPERADA]}
    assert ev.evaluar_documento(pdf, esp)["campos"]["desglose"] is True
    # una regresión que añade una fila exenta (o sin calificar) a una factura sujeta baja la puntuación
    original = borradores.datos_desde_extraccion
    for fila in (borradores.fila_exenta("50.00", "E1", MENCION_23), borradores.fila_exenta("50.00")):
        def con_fila_de_mas(ext, nombre="", fila=fila):
            d = original(ext, nombre)
            d["desglose"] = d["desglose"] + [fila]
            return d
        monkeypatch.setattr(ev.borradores, "datos_desde_extraccion", con_fila_de_mas)
        r = ev.evaluar_documento(pdf, esp)
        assert r["campos"]["desglose"] is False and "desglose" in r["fallos"]
    monkeypatch.setattr(ev.borradores, "datos_desde_extraccion", original)
    # al revés: el desglose esperado tiene una fila exenta que el borrador no propone
    esp_ex = {"desglose": [SUJETA_ESPERADA, {"operacion_exenta": "E1", "base": "50.00"}]}
    assert ev.evaluar_documento(pdf, esp_ex)["campos"]["desglose"] is False


def test_x_r5_filas_exentas_esperadas_y_puntuacion_sin_campos_nuevos(tmp_path):
    # factura mixta: el desglose esperado declara la fila exenta (causa y base)
    pdf = _pdf(tmp_path, "mixta.pdf", LINEAS_ACADEMIA,
               [("BASE IMPONIBLE", "750,00"), ("IVA 4% s/ 100,00", "4,00"), ("TOTAL FACTURA", "754,00")], notas=NOTA_ACADEMIA)
    sujeta4 = {"tipo_iva": "4.00", "base": "100.00", "cuota": "4.00"}
    assert ev.evaluar_documento(pdf, {"desglose": [sujeta4, {"operacion_exenta": "E1", "base": "650.00"}]})["campos"]["desglose"]
    assert not ev.evaluar_documento(pdf, {"desglose": [sujeta4]})["campos"]["desglose"]
    assert not ev.evaluar_documento(pdf, {"desglose": [sujeta4, {"operacion_exenta": "E2", "base": "650.00"}]})["campos"]["desglose"]
    # factura sin IVA con desglose esperado vacío: como antes, el desglose no se compara (no cambia la puntuación)
    pdf = _pdf(tmp_path, "sin_iva.pdf", LINEA_ALQUILER, TOTALES_SIN_IVA)
    r = ev.evaluar_documento(pdf, {"desglose": [], "base_imponible": "650.00"})
    assert "desglose" not in r["campos"] and r["campos"] == {"base_imponible": True}
    # base sin calificar esperada: operacion_exenta null
    assert ev.evaluar_documento(pdf, {"desglose": [{"operacion_exenta": None, "base": "650.00"}]})["campos"]["desglose"]
