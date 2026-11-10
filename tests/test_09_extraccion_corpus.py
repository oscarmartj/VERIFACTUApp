"""Extracción con diseños de factura variados (corpus sintético) y con la factura real de referencia (privada).

El corpus de desarrollo (tests/corpus) se usa para ajustar las reglas. Los corpus reservados (tests/corpus_holdout,
corpus_holdout2 y corpus_holdout3) se puntuaron a ciegas al recibirlos, antes de mirarlos: esa primera puntuación (en
docs/pruebas.md) es la única medida de la generalidad. Después se estudiaron para corregir las causas generales de sus
fallos, así que ya no son ciegos: aquí solo protegen frente a regresiones y su precisión actual no mide la generalidad.
Medirla de nuevo exige un corpus nuevo que nadie haya visto. Los umbrales protegen frente a regresiones: un cambio de las
reglas que mejore un diseño y empeore otros hace fallar estas pruebas.
"""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import evaluar_extraccion as ev  # noqa: E402

CRITICOS = ("numero", "fecha", "emisor.nif", "destinatario.nif", "base_imponible", "cuota_iva", "total_factura", "desglose")


def _precision(res, campos):
    tot = sum(res["por_campo"][c]["total"] for c in campos if c in res["por_campo"])
    ok = sum(res["por_campo"][c]["aciertos"] for c in campos if c in res["por_campo"])
    return ok / tot if tot else 1.0


@pytest.mark.skipif(not (RAIZ / "tests/corpus").exists(), reason="sin corpus")
def test_corpus_desarrollo():
    res = ev.evaluar([RAIZ / "tests/corpus"])
    assert len(res["documentos"]) >= 10
    assert not [d for d in res["documentos"] if d.get("error")]
    assert res["global"]["precision"] >= 0.97, res["por_campo"]
    assert _precision(res, CRITICOS) >= 0.98, res["por_campo"]


# Reservados ya estudiados tras su medición ciega (ver el docstring): son corpus de regresión, no de generalidad
RESERVADOS_ESTUDIADOS = [d for d in (RAIZ / "tests/corpus_holdout", RAIZ / "tests/corpus_holdout2",
                                     RAIZ / "tests/corpus_holdout3") if d.exists()]


@pytest.mark.skipif(not RESERVADOS_ESTUDIADOS, reason="sin corpus reservado")
def test_corpus_reservado():
    """Regresión sobre los corpus reservados ya estudiados (no es una medida de generalidad)."""
    res = ev.evaluar(RESERVADOS_ESTUDIADOS)
    assert res["documentos"]
    assert res["global"]["precision"] >= 0.85, res["por_campo"]
    assert _precision(res, CRITICOS) >= 0.9, res["por_campo"]


@pytest.mark.skipif(not list((RAIZ / "tests/privado").glob("*.json")) if (RAIZ / "tests/privado").exists() else True,
                    reason="la factura real de referencia es privada y no se distribuye")
def test_factura_real_de_referencia():
    res = ev.evaluar([RAIZ / "tests/privado"])
    for d in res["documentos"]:
        assert not d.get("error") and not d["fallos"], (d["archivo"], d.get("fallos"))
