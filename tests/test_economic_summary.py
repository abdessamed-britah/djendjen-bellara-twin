"""Tests de scripts/economic_summary.py : conversion perte → dollars, sans simulation."""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "economic_summary", Path(__file__).parent.parent / "scripts" / "economic_summary.py"
)
eco = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(eco)


def test_cout_journee_est_estime_avec_fourchette():
    raw = load_yaml(ASSUMPTIONS_PATH)
    assert raw["couts"]["cout_journee_arret_dri"]["status"] == "estimé"
    bas, centre, haut = eco.cout_journee(raw)
    assert bas < centre < haut


def test_une_journee_de_production_perdue_coute_une_journee():
    marge = eco.marge_par_tonne(130_000, 5_000)
    assert eco.cout_annuel(5_000, marge) == pytest.approx(130_000)


def test_enveloppe_ignore_une_perte_negative():
    bas, haut = eco.enveloppe(-1_000, 2_000, 10, 40)
    assert bas == 0.0
    assert haut == pytest.approx(80_000)


def test_arrondi_large():
    assert eco.arrondi_millions(200_000) == "< 1"
    assert eco.arrondi_millions(11_400_000) == "11"
