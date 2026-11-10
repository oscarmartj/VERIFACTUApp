"""Regresiones (carril A, «partes»): cliente en un recuadro arriba a la derecha y emisor solo en el pie.

Facturas sintéticas generadas con reportlab (razones sociales, calles y NIF ficticios; NIF con código de provincia 00).
Reproducen la maquetación de un emisor cuyo membrete es una imagen: el primer texto de la página es el recuadro de la
dirección del cliente y los datos del emisor (razón social, NIF y dirección) solo figuran en una línea del pie.
"""
import io
import re
import sqlite3

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import borradores
from app.extraccion import campos, texto
from app.nif import completar_cif

EMP = completar_cif("B", "0012345")     # empresa configurada (emisor)
CLI = completar_cif("A", "0054321")     # cliente
OTRO = completar_cif("B", "0098765")    # otro cliente (segunda factura)

EMISOR = "EMPRESA FICTICIA DEMO, S.L."
CLIENTE = "ÓPTICAS FICTICIAS, S.A.U."
CLIENTE2 = "GESTIONES IMAGINARIAS, S.L."


def _pdf(ops) -> bytes:
    """ops: (x, y_desde_arriba, texto[, tamaño[, fuente[, alineación]]]) en puntos."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _, alto = A4
    for op in ops:
        x, y, s = op[0], op[1], op[2]
        c.setFont(op[4] if len(op) > 4 else "Helvetica", op[3] if len(op) > 3 else 9)
        (c.drawRightString if len(op) > 5 and op[5] == "r" else c.drawString)(x, alto - y, s)
    c.showPage()
    c.save()
    return buf.getvalue()


def _extraer(datos: bytes, nif_empresa=EMP):
    ext = campos.Extractor(texto.leer(datos), "f.pdf", nif_empresa).extraer()
    return ext, borradores.datos_desde_extraccion(ext, "f.pdf")


def _validar(ext, datos):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE facturas (id INTEGER, entorno TEXT, empresa_id INTEGER, emisor_nif TEXT, num_serie TEXT)")
    empresa = {"id": 1, "nif": EMP, "razon_social": EMISOR, "domicilio": None, "ambito_confirmado_en": "x",
               "ambito_dict": {"puede_emitir": True}}
    documento = {"clasificacion": "preparatorio", "clasificacion_confirmada_en": "x"}
    serie = {"siguiente_numero": 1, "codigo": "X", "numero_detectado": None}
    return borradores.validar(con, empresa, documento, datos, extraccion=ext, serie=serie)


def _cuerpo(y0=420):
    return [(170, y0, "DESCRIPCIÓN", 9, "Helvetica-Bold"), (340, y0, "PRECIO", 9, "Helvetica-Bold"),
            (400, y0, "CANTIDAD", 9, "Helvetica-Bold"), (505, y0, "IMPORTE", 9, "Helvetica-Bold", "r"),
            (85, y0 + 28, "Servicio de mantenimiento ficticio"), (373, y0 + 28, "180,00", 9, "Helvetica", "r"),
            (420, y0 + 28, "1", 9, "Helvetica", "r"), (505, y0 + 28, "180,00", 9, "Helvetica", "r"),
            (380, y0 + 80, "BASE IMPONIBLE"), (505, y0 + 80, "180,00", 9, "Helvetica", "r"),
            (380, y0 + 94, "IVA 21%"), (505, y0 + 94, "37,80", 9, "Helvetica", "r"),
            (380, y0 + 108, "TOTAL FACTURA"), (505, y0 + 108, "217,80", 9, "Helvetica", "r")]


def _nif(n):
    return f"C.I.F.: {n[0]}-{n[1:]}"


PIE_ESPACIOS = f"{EMISOR} {_nif(EMP)} Imaginaria, 58 28002 Madrid facturas@ejemplo.es"
PIE_PUNTOS = f"{EMISOR} · {_nif(EMP)} · Imaginaria, 58 · 28002 Madrid · facturas@ejemplo.es"


def _recuadro_nif_al_pie(pie=PIE_ESPACIOS, cliente=CLIENTE, y_nif=252):
    """Recuadro del cliente arriba a la derecha (nombre en negrita, calle, CP y población, alineados a la izquierda) y su
    NIF solo, alineado a la derecha del recuadro unos 25 puntos más abajo, solapando apenas el nombre."""
    ops = [(314, 202, cliente, 10, "Helvetica-Bold"), (314, 215, "C/ Inventada, 14"), (314, 228, "28001 Madrid"),
           (510, y_nif, _nif(CLI), 8, "Helvetica", "r"),
           (510, 302, "Madrid, 3 de octubre de 2026", 10, "Helvetica", "r"),
           (206, 338, "FACTURA NÚMERO: 7001", 18, "Helvetica-Bold"), (130, 382, "REFERENCIA : SERVICIOS DE PRUEBA", 12)]
    return _pdf(ops + _cuerpo() + [(106, 792, pie, 7)])


# --- A1: NIF solo al pie del recuadro, alineado a la derecha ---------------------------------------------------------
@pytest.mark.parametrize("y_nif", [252, 262], ids=["un_renglon", "renglon_y_medio"])
def test_nif_alineado_a_la_derecha_al_pie_del_recuadro_toma_su_nombre_y_domicilio(y_nif):
    ext, d = _extraer(_recuadro_nif_al_pie(y_nif=y_nif))
    assert d["destinatario"]["nif"] == CLI
    assert d["destinatario"]["nombre"] == CLIENTE
    assert "Inventada, 14" in d["destinatario"]["domicilio"] and "28001" in d["destinatario"]["domicilio"]
    assert ext["destinatario"]["nombre"]["confianza"] >= borradores.UMBRAL_CONFIANZA


# --- A3: domicilio del emisor en la línea del pie, sin tipo de vía -----------------------------------------------------
@pytest.mark.parametrize("pie", [PIE_ESPACIOS, PIE_PUNTOS], ids=["espacios", "puntos"])
def test_domicilio_del_emisor_en_el_pie_sin_tipo_de_via(pie):
    ext, d = _extraer(_recuadro_nif_al_pie(pie))
    assert d["emisor"]["nif"] == EMP and d["emisor"]["nombre"] == EMISOR
    dom = d["emisor"]["domicilio"] or ""
    assert "Imaginaria, 58" in dom and "28002 Madrid" in dom
    assert "Inventada" not in dom and "28001" not in dom and "@" not in dom
    assert d["destinatario"]["nombre"] == CLIENTE and "28001" in d["destinatario"]["domicilio"]


def test_pie_con_rotulo_de_contacto_dentro_de_una_palabra():
    """«móvil» dentro de «Automóvil» no es un contacto: no descarta la calle del pie; el «Tel.» que sigue, sí corta."""
    pie = f"{EMISOR} · {_nif(EMP)} · Avda. del Automóvil Ficticio 3 · 28002 Madrid · Tel. 910 000 000"
    ext, d = _extraer(_recuadro_nif_al_pie(pie))
    dom = d["emisor"]["domicilio"] or ""
    assert "Automóvil Ficticio 3" in dom and "28002 Madrid" in dom
    assert "Tel" not in dom and "Inventada" not in dom


def test_domicilio_de_la_cabecera_de_otro_bloque_no_es_seguro():
    """Emisor sin dirección junto a su NIF y, en la cabecera, dos recuadros de otras partes sin NIF (el NIF del cliente va
    suelto bajo el título): la dirección de la cabecera no es la del emisor con seguridad, se pide confirmarla."""
    pie = f"{EMISOR} · {_nif(EMP)} · facturas@ejemplo.es"
    ops = [(310, 198, CLIENTE2, 10, "Helvetica-Bold"), (310, 211, "C/ Ficticia, 12"), (310, 224, "28010 Madrid"),
           (60, 198, "TRANSPORTES IMAGINARIOS, S.L.", 10, "Helvetica-Bold"), (60, 211, "Calle Inventada 3"),
           (60, 224, "28003 Madrid"),
           (510, 288, "Madrid, 3 de octubre de 2026", 10, "Helvetica", "r"),
           (192, 325, "FACTURA NÚMERO: 7002", 18, "Helvetica-Bold"), (510, 337, _nif(OTRO), 7, "Helvetica", "r")]
    ext, d = _extraer(_pdf(ops + _cuerpo() + [(92, 802, pie, 7)]))
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == OTRO
    c = ext["emisor"]["domicilio"]
    assert "Ficticia" not in (c["valor"] or "")  # el recuadro asignado al destinatario no
    assert c["valor"] is None or (c["confianza"] < borradores.UMBRAL_CONFIANZA and any("otra parte" in a for a in c["avisos"]))
    cod = {(i["nivel"], i["codigo"], i["campo"]) for i in _validar(ext, d)}
    assert ("confirmar", "DUDOSO", "emisor.domicilio") in cod or ("bloqueo", "EMISOR_DOMICILIO", "emisor.domicilio") in cod


# --- A2: NIF del destinatario suelto lejos del recuadro sin NIF -------------------------------------------------------
def _recuadro_sin_nif(otro_bloque=False):
    pie = f"{EMISOR} · {_nif(EMP)} · C/ Imaginaria, 58 · 28002 Madrid · facturas@ejemplo.es"
    ops = [(310, 198, CLIENTE2, 10, "Helvetica-Bold"), (310, 211, "C/ Ficticia, 12 2º B"), (310, 224, "28010 MADRID"),
           (311, 238, "A/A Dpto. Contabilidad"),
           (510, 288, "Madrid, 3 de octubre de 2026", 10, "Helvetica", "r"),
           (192, 325, "FACTURA NÚMERO: 7002", 18, "Helvetica-Bold"), (510, 337, _nif(OTRO), 7, "Helvetica", "r")]
    if otro_bloque:  # otro bloque sin NIF a la izquierda, bajo un lema (no abre la página: no es el membrete)
        ops += [(60, 60, "Soluciones ficticias para empresas desde 1990", 12), (60, 198, "TRANSPORTES IMAGINARIOS, S.L.", 10,
                                                                           "Helvetica-Bold"),
                (60, 211, "Calle Inventada 3"), (60, 224, "28003 Madrid")]
    return _pdf(ops + _cuerpo() + [(92, 802, pie, 7)])


def test_nif_suelto_toma_el_unico_recuadro_sin_nif_y_pide_confirmarlo():
    ext, d = _extraer(_recuadro_sin_nif())
    assert d["destinatario"]["nif"] == OTRO
    assert d["destinatario"]["nombre"] == CLIENTE2
    dom = d["destinatario"]["domicilio"] or ""
    assert "Ficticia, 12" in dom and "28010" in dom and "Contabilidad" not in dom
    for k in ("nombre", "domicilio"):
        c = ext["destinatario"][k]
        assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("Confírmelo" in a for a in c["avisos"])
    # el emisor conserva su dirección del pie
    assert "Imaginaria, 58" in d["emisor"]["domicilio"] and "Ficticia" not in d["emisor"]["domicilio"]
    cod = {(i["nivel"], i["codigo"], i["campo"]) for i in _validar(ext, d)}
    assert ("confirmar", "DUDOSO", "destinatario.nombre") in cod
    assert ("confirmar", "DUDOSO", "destinatario.domicilio") in cod


def test_nif_suelto_con_dos_recuadros_sin_nif_no_elige_ninguno():
    ext, d = _extraer(_recuadro_sin_nif(otro_bloque=True))
    assert d["destinatario"]["nif"] == OTRO
    assert d["destinatario"]["nombre"] is None and d["destinatario"]["domicilio"] is None


def test_recuadro_sin_nif_y_emisor_sin_direccion_en_el_pie():
    """El recuadro asignado al destinatario no se usa además como domicilio del emisor."""
    pie = f"{EMISOR} · {_nif(EMP)} · facturas@ejemplo.es"
    ops = [(310, 198, CLIENTE2, 10, "Helvetica-Bold"), (310, 211, "C/ Ficticia, 12"), (310, 224, "28010 MADRID"),
           (510, 288, "Madrid, 3 de octubre de 2026", 10, "Helvetica", "r"),
           (192, 325, "FACTURA NÚMERO: 7002", 18, "Helvetica-Bold"), (510, 337, _nif(OTRO), 7, "Helvetica", "r")]
    ext, d = _extraer(_pdf(ops + _cuerpo() + [(92, 802, pie, 7)]))
    assert d["destinatario"]["nombre"] == CLIENTE2
    c = ext["emisor"]["domicilio"]
    assert c["valor"] is None or c["confianza"] < borradores.UMBRAL_CONFIANZA


def test_membrete_del_emisor_no_se_toma_como_destinatario():
    """El bloque que abre la página con la razón social del emisor (la del pie) no es el del destinatario."""
    pie = f"{EMISOR} · {_nif(EMP)} · C/ Imaginaria, 58 · 28002 Madrid"
    ops = [(310, 60, EMISOR, 12, "Helvetica-Bold"), (310, 74, "C/ Imaginaria, 58"), (310, 87, "28002 Madrid"),
           (192, 325, "FACTURA NÚMERO: 7002", 18, "Helvetica-Bold"), (510, 337, _nif(OTRO), 7, "Helvetica", "r")]
    ext, d = _extraer(_pdf(ops + _cuerpo() + [(92, 802, pie, 7)]))
    assert d["destinatario"]["nif"] == OTRO
    assert d["destinatario"]["nombre"] != EMISOR


# --- A3: validación del borrador ---------------------------------------------------------------------------------------
def test_validar_pide_confirmar_domicilio_del_emisor_igual_al_del_destinatario():
    ext, d = _extraer(_recuadro_nif_al_pie())
    d["emisor"]["domicilio"] = d["destinatario"]["domicilio"]
    cod = {(i["nivel"], i["codigo"], i["campo"]) for i in _validar(ext, d)}
    assert ("confirmar", "EMISOR_DOMICILIO_DESTINATARIO", "emisor.domicilio") in cod
    # solo «CP ciudad» compartidos (misma población) no basta
    d["emisor"]["domicilio"], d["destinatario"]["domicilio"] = "28001 Madrid", "28001 Madrid"
    cod = {i["codigo"] for i in _validar(ext, d)}
    assert "EMISOR_DOMICILIO_DESTINATARIO" not in cod


# --- Revisión: palabra de contacto dentro del nombre de la calle y frases de contacto del pie -------------------------
def _cliente_con_nif():
    """Recuadro del cliente con su razón social, su dirección y su NIF (el destinatario tiene su propio bloque)."""
    return [(314, 202, CLIENTE, 10, "Helvetica-Bold"), (314, 215, "C/ Inventada, 14"), (314, 228, "28001 Madrid"),
            (510, 252, _nif(CLI), 8, "Helvetica", "r"),
            (206, 338, "FACTURA NÚMERO: 7001", 18, "Helvetica-Bold")]


@pytest.mark.parametrize("pie, esperado", [
    (f"{EMISOR} · {_nif(EMP)} · Calle Ficticia del Correo, 4 · 28002 Madrid · info@ejemplo.es", ["Calle Ficticia del Correo, 4", "28002 Madrid"]),
    (f"{EMISOR} {_nif(EMP)} Plaza Imaginaria de Correos 2 28002 Madrid", ["Plaza Imaginaria de Correos 2", "28002 Madrid"]),
    (f"{EMISOR} {_nif(EMP)} Avda. Inventada de Correos 12, 28002 Madrid", ["Avda. Inventada de Correos 12", "28002 Madrid"]),
    (f"{EMISOR} · {_nif(EMP)} · 28002 Madrid · Calle Ficticia del Correo, 4", ["Calle Ficticia del Correo, 4", "28002 Madrid"]),
    (f"{EMISOR} · {_nif(EMP)} · Ronda de Telefonía Ficticia 3 · 28002 Madrid", ["Telefonía Ficticia 3", "28002 Madrid"]),
    (f"{EMISOR} · {_nif(EMP)} · Correo de la Ficticia, 7 · 28002 Madrid · Tel. 910 000 000", ["Correo de la Ficticia, 7", "28002 Madrid"]),
], ids=["calle_del_correo", "plaza_de_correos_sin_separador", "avenida_de_correos", "cp_delante", "telefonia",
        "sin_tipo_de_via"])
def test_palabra_de_contacto_en_el_nombre_de_la_calle_no_corta_el_domicilio(pie, esperado):
    """«Correo», «Correos», «Telefonía» que forman parte del nombre de la vía no son un rótulo de contacto: no hay «:»,
    teléfono, correo ni web detrás. El domicilio no se corta en «Calle Ficticia del» / «Plaza Imaginaria de»."""
    ext, d = _extraer(_pdf(_cliente_con_nif() + _cuerpo() + [(60, 792, pie, 7)]))
    dom = d["emisor"]["domicilio"] or ""
    for s in esperado:
        assert s in dom
    assert "@" not in dom and "Tel" not in dom.replace("Telefonía", "") and "Inventada, 14" not in dom
    assert ext["emisor"]["domicilio"]["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_palabra_de_contacto_en_la_calle_junto_al_nif_de_la_cabecera():
    ops = [(60, 50, EMISOR, 14, "Helvetica-Bold"), (60, 68, f"CIF: {EMP} · Calle Ficticia del Correo, 4 · 28002 Madrid")]
    ext, d = _extraer(_pdf(ops + _cliente_con_nif() + _cuerpo()))
    dom = d["emisor"]["domicilio"] or ""
    assert "Calle Ficticia del Correo, 4" in dom and "28002 Madrid" in dom


def test_calle_cortada_en_la_palabra_de_contacto_no_es_un_domicilio():
    """Si lo que precede a un contacto no tiene número ni código postal («Calle Ficticia del» antes de «Correo: …»), no
    es un domicilio."""
    pie = f"{EMISOR} · {_nif(EMP)} · Calle Ficticia del Correo: info@ejemplo.es"
    ext, d = _extraer(_pdf(_cliente_con_nif() + _cuerpo() + [(60, 792, pie, 7)]))
    assert not (d["emisor"]["domicilio"] or "").rstrip().endswith(("del", "Ficticia"))


@pytest.mark.parametrize("cola", [
    "Síguenos en www.ejemplo.es", "Visítenos en https://ejemplo.es", "Contacto: info@ejemplo.es",
    "Pedidos: pedidos@ejemplo.es", "Atención al cliente Tel. 900 000 000", "Teléfono y fax 910 000 000",
    "Correo electrónico: info@ejemplo.es",
])
@pytest.mark.parametrize("sep", [" · ", " "], ids=["puntos", "espacios"])
def test_frase_de_contacto_del_pie_no_se_anade_al_domicilio(cola, sep):
    """La frase que acompaña al contacto no es parte del domicilio. Con separadores es un trozo aparte y se descarta; sin
    separador, el rótulo («Contacto:», «Teléfono y fax») se quita, y una frase («Síguenos en») se corta tras la población
    pidiendo confirmarlo (dónde acaba la población es una deducción)."""
    pie = sep.join([EMISOR, _nif(EMP), "C/ Imaginaria, 3", "28002 Madrid", cola])
    ext, d = _extraer(_pdf(_cliente_con_nif() + _cuerpo() + [(60, 792, pie, 7)]))
    c = ext["emisor"]["domicilio"]
    assert "Imaginaria, 3" in (c["valor"] or "") and c["valor"].rstrip().endswith("28002 Madrid")
    frase = sep == " " and not re.match(r"\w+(?: \w+)?:|Tel|Correo", cola)
    if frase:
        assert c["confianza"] < borradores.UMBRAL_CONFIANZA and any("sin separador" in a for a in c["avisos"])
    else:
        assert c["confianza"] >= borradores.UMBRAL_CONFIANZA


@pytest.mark.parametrize("poblacion", ["Villa Ficticia de Abajo", "San Imaginario de los Montes", "Puebla Inventada (Madrid)",
                                       "Platja d'Imaginari"])
def test_poblacion_de_varias_palabras_antes_del_contacto_sin_separador(poblacion):
    pie = f"{EMISOR} {_nif(EMP)} C/ Imaginaria, 3 28800 {poblacion} info@ejemplo.es Tel. 910 000 000"
    ext, d = _extraer(_pdf(_cliente_con_nif() + _cuerpo() + [(60, 792, pie, 7)]))
    c = ext["emisor"]["domicilio"]
    assert (c["valor"] or "").endswith(f"28800 {poblacion}")
    assert c["confianza"] >= borradores.UMBRAL_CONFIANZA


def test_calle_sin_tipo_de_via_con_el_codigo_postal_en_la_linea_siguiente_del_pie():
    ops = _cliente_con_nif() + _cuerpo() + [(60, 792, f"{EMISOR} · {_nif(EMP)} · Imaginaria, 58", 7),
                                            (60, 801, "28002 Madrid · Tel. 910 000 000", 7)]
    ext, d = _extraer(_pdf(ops))
    dom = d["emisor"]["domicilio"] or ""
    assert "Imaginaria, 58" in dom and "28002 Madrid" in dom and "Tel" not in dom


# --- Revisión: salvaguarda del domicilio de la cabecera ----------------------------------------------------------------
def _recuadro_derecha_sin_nif(cabecera, pie):
    """Recuadro arriba a la derecha sin NIF; el NIF del cliente, suelto bajo el título; el emisor, solo en el pie."""
    ops = ([(320, 190, cabecera, 10, "Helvetica-Bold")] if cabecera else []) + [
        (320, 203, "C/ Ficticia, 5"), (320, 216, "46001 Valencia"),
        (200, 330, "FACTURA NÚMERO: 7003", 16, "Helvetica-Bold"), (540, 345, _nif(CLI), 7, "Helvetica", "r")]
    return _pdf(ops + _cuerpo() + [(60, 800, pie, 7)])


def _dudoso(ext, d, campo):
    c = ext[campo.split(".")[0]][campo.split(".")[1]]
    cod = {(i["nivel"], i["codigo"], i["campo"]) for i in _validar(ext, d)}
    return c["valor"] is None or (c["confianza"] < borradores.UMBRAL_CONFIANZA and c["avisos"]
                                  and ("confirmar", "DUDOSO", campo) in cod)


def test_razon_social_del_emisor_leida_del_recuadro_del_cliente_se_pide_confirmar():
    """El pie solo trae el NIF del emisor (nombre y logotipo en una imagen) y el NIF del cliente va suelto: la única razón
    social de la cabecera puede ser la del cliente, igual que su dirección. Ni una ni otra salen como seguras."""
    ext, d = _extraer(_recuadro_derecha_sin_nif(CLIENTE2, f"{_nif(EMP)} · facturas@ejemplo.es"))
    assert d["emisor"]["nif"] == EMP and d["destinatario"]["nif"] == CLI
    assert _dudoso(ext, d, "emisor.nombre")
    assert _dudoso(ext, d, "emisor.domicilio")


@pytest.mark.parametrize("cabecera", ["AUTOCARES 33", None, "Bar La Ficticia"], ids=["con_digitos", "sin_cabecera", "rotulo"])
def test_domicilio_de_la_cabecera_con_el_nif_del_destinatario_sin_bloque_se_pide_confirmar(cabecera):
    """El NIF del destinatario figura sin razón social ni domicilio: el bloque de dirección de la cabecera puede ser el
    suyo, lo encabece lo que lo encabece (o nada)."""
    ext, d = _extraer(_recuadro_derecha_sin_nif(cabecera, f"{EMISOR} · {_nif(EMP)} · facturas@ejemplo.es"))
    assert d["emisor"]["nombre"] == EMISOR
    assert _dudoso(ext, d, "emisor.domicilio")


@pytest.mark.parametrize("membrete", [
    [(60, 50, "Ficticia Eventos", 16, "Helvetica-Bold"), (60, 68, "C/ Imaginaria, 9"), (60, 81, "28002 Madrid")],
    [(60, 50, "FICTICIA EVENTOS", 16, "Helvetica-Bold"), (60, 68, "C/ Imaginaria, 9"), (60, 81, "28002 Madrid")],
    [(60, 40, "Soluciones Ficticias Para Su Negocio", 9, "Helvetica-Oblique"), (60, 56, "C/ Imaginaria, 9"),
     (60, 69, "28002 Madrid")],
    [(60, 50, EMISOR, 14, "Helvetica-Bold"), (60, 68, "C/ Imaginaria, 9"), (60, 81, "28002 Madrid")],
], ids=["nombre_comercial", "nombre_comercial_mayusculas", "lema", "razon_social"])
def test_membrete_con_nombre_comercial_y_cliente_con_su_bloque_es_seguro(membrete):
    """Membrete con un nombre comercial (sin forma jurídica) o un lema y la dirección; la razón social y el NIF del emisor,
    en el pie; el cliente, con su propio bloque completo: la dirección del membrete es la del emisor, sin dudas."""
    ext, d = _extraer(_pdf(membrete + _cliente_con_nif() + _cuerpo() + [(60, 792, f"{EMISOR} · {_nif(EMP)}", 7)]))
    assert d["destinatario"]["nombre"] == CLIENTE
    c = ext["emisor"]["domicilio"]
    assert "Imaginaria, 9" in (c["valor"] or "") and "28002" in c["valor"] and "Inventada" not in c["valor"]
    assert c["confianza"] >= borradores.UMBRAL_CONFIANZA
    assert not any(i["campo"] == "emisor.domicilio" and i["nivel"] != "aviso" for i in _validar(ext, d))


def test_membrete_de_otra_sociedad_con_el_cliente_completo_sigue_en_duda():
    """Con el cliente completo, un bloque de la cabecera encabezado por otra razón social con forma jurídica sigue sin
    darse por seguro como domicilio del emisor."""
    otro = [(60, 50, "TRANSPORTES IMAGINARIOS, S.L.", 12, "Helvetica-Bold"), (60, 64, "C/ Imaginaria, 9"),
            (60, 77, "28002 Madrid")]
    ext, d = _extraer(_pdf(otro + _cliente_con_nif() + _cuerpo() + [(60, 792, f"{EMISOR} · {_nif(EMP)}", 7)]))
    assert _dudoso(ext, d, "emisor.domicilio")
