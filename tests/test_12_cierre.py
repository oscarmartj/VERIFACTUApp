"""Regresiones del cierre de la cuarta verificación adversarial: costes acotados ante PDF o borradores hostiles, cifras de
otros sistemas de escritura, números duplicados que solo difieren en mayúsculas y facturas demasiado largas para componer.

Datos ficticios (NIF con dígito de control válido, empresas inventadas).
"""
import pytest

from app import borradores, pdf_final
from app.extraccion import campos, texto
from app.extraccion.texto import Pagina, Palabra
from app.importacion import _probar_serie

from tests.test_10_revision_a import CLI, EMP, _cuerpo, _extraer, _pdf, _validar


# --- Cifras de otro sistema de escritura: dudosas (su /ToUnicode puede no decir lo que dibuja la fuente) -------------
@pytest.mark.parametrize("palabra", ["９00,00", "٩0,00", "𝟗90,00", "９９０,００"])
def test_cifras_no_ascii_son_dudosas(palabra):
    assert texto._cifras_no_ascii(palabra)


@pytest.mark.parametrize("palabra", ["200,00", "m²", "1.º", "nº", "21%", "F-2026/045"])
def test_cifras_ascii_y_superindices_no_son_dudosos(palabra):
    assert not texto._cifras_no_ascii(palabra)


# --- Nota de garantía: la expresión se evalúa en un número acotado de párrafos -----------------------------------------
def test_nota_garantia_con_coste_acotado(monkeypatch):
    # 2.000 renglones separados (no forman párrafos) con las dos palabras y sin frase que las una
    pal = [Palabra("Garantía;", 50, 40 + 30 * (i % 25), 90, 48 + 30 * (i % 25)) for i in range(2000)]
    pal += [Palabra("retención", 95, 40 + 30 * (i % 25), 140, 48 + 30 * (i % 25)) for i in range(2000)]
    paginas = [Pagina(n + 1, 595, 842, [p for k, p in enumerate(pal) if (k % 2000) // 25 == n]) for n in range(80)]
    ext = campos.Extractor(paginas, "f.pdf", EMP)
    llamadas = []

    class Contador:
        def search(self, s):
            llamadas.append(s)
            return None
    monkeypatch.setattr(campos.Extractor, "RE_NOTA_GARANTIA", Contador())
    assert ext._nota_garantia() is None
    assert 0 < len(llamadas) <= campos.Extractor.MAX_NOTAS_GARANTIA


def test_nota_garantia_sigue_detectando_la_retencion_de_garantia():
    ops = [(50, 50, "EMPRESA DEMO S.L.", 12, "Helvetica-Bold"), (50, 64, f"NIF: {EMP}"), (380, 50, "FACTURA", 18),
           (380, 72, "Nº factura: F-2026/045"), (380, 84, "Fecha: 15/10/2026"), (330, 120, "Cliente:", 9, "Helvetica-Bold"),
           (330, 134, "DISTRIBUCIONES NORTE S.A."), (330, 146, f"CIF: {CLI}"), (330, 158, "Av. Libertad 5, 46001 Valencia")]
    ops += _cuerpo(200) + [(50, 400, "Se retiene el 5 % del importe en concepto de garantía de la obra.")]
    ext, _ = _extraer(_pdf(ops))
    assert "retencion_garantia" in ext["indicios"]


# --- Duplicados: el mismo número de la serie con otras mayúsculas -------------------------------------------------------
@pytest.mark.parametrize("numero", ["FRA2026166", "fra20260166", "Fra 2026 166", "FRA20260166"])
def test_numero_de_serie_sin_distinguir_mayusculas(numero):
    r = _probar_serie("FRA{aaaa}{n}", numero)
    assert r["coincide"] and (r["numero"], r["anio"]) == (166, 2026)


# --- Facturas demasiado largas para componer: se bloquean antes de maquetarlas ----------------------------------------
def _datos_largos(renglones_por_concepto: int, filas: int) -> dict:
    return {"emisor": {"nombre": "EMPRESA DEMO S.L.", "nif": EMP}, "destinatario": {"nombre": "CLIENTE", "nif": CLI},
            "lineas": [{"concepto": "x\n" * renglones_por_concepto, "cantidad": "1", "precio": "1.00", "importe": "1.00"}
                       for _ in range(filas)]}


def test_renglones_estimados():
    assert borradores.renglones_estimados(_datos_largos(1, 999)) < borradores.MAX_RENGLONES_PDF
    assert borradores.renglones_estimados(_datos_largos(2499, 3)) > borradores.MAX_RENGLONES_PDF
    assert borradores.renglones_estimados({"lineas": [{"concepto": "a" * 450}]}) >= 10


def test_renglones_por_columna_y_solo_lo_que_se_imprime():
    # la columna de unidad es estrecha: un texto largo ocupa muchos renglones
    assert borradores.renglones_estimados({"lineas": [{"concepto": "x", "unidad": "u" * 400}]}) >= 80
    # lo que no se imprime (confirmaciones, numeración, origen…) no cuenta
    assert borradores.renglones_estimados({"confirmaciones": ["x" * 4000] * 50, "numeracion": {"a": "y" * 4000},
                                           "origen": {"sha256": "z" * 64}}) == 0


def test_borrador_demasiado_largo_se_bloquea():
    inc = _validar({}, _datos_largos(2499, 3))
    assert any(i["codigo"] == "DOCUMENTO_DEMASIADO_LARGO" and i["nivel"] == "bloqueo" for i in inc)
    inc = _validar({}, {"lineas": [{"concepto": "x", "unidad": "u" * 4000, "importe": "1.00"} for _ in range(30)]})
    assert any(i["codigo"] == "DOCUMENTO_DEMASIADO_LARGO" for i in inc)


def test_factura_ya_expedida_larga_se_puede_volver_a_componer():
    """El límite es de la expedición y la vista previa del borrador: una factura ya expedida siempre se recompone."""
    datos = _datos_largos(1, 4)
    datos["observaciones"] = "Nota\n" * 3200   # más que el límite, pero rápido de componer
    assert borradores.renglones_estimados(datos) > borradores.MAX_RENGLONES_PDF
    pdf, info = pdf_final.reconstruir(datos, modo="definitivo", entorno="simulador", url_qr=None, logo_png=None,
                                      fecha_expedicion="2026-11-10")
    assert pdf.startswith(b"%PDF") and info["paginas"] > 1


def test_borrador_normal_no_se_bloquea_por_largo():
    datos = _datos_largos(1, 40)
    assert not any(i["codigo"] == "DOCUMENTO_DEMASIADO_LARGO" for i in _validar({}, datos))
