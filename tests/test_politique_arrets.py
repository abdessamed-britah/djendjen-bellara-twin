"""Tests de scripts/politique_arrets.py : la règle écrite avant le calcul, sans simuler."""

import importlib.util
import math
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.stats import Estimate

_SPEC = importlib.util.spec_from_file_location(
    "politique_arrets", Path(__file__).parent.parent / "scripts" / "politique_arrets.py"
)
pa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pa)

RAW = load_yaml(ASSUMPTIONS_PATH)


def test_seules_les_variantes_discretes_sont_comparees():
    assert pa.VARIANTES == (("discret", True), ("discret", False))
    assert pa.POLITIQUES == ("aveugle", "rythme_moyen", "date_de_reprise")


@pytest.mark.parametrize("politique", pa.POLITIQUES)
def test_une_configuration_porte_sa_politique_et_le_s_de_l_etape_6(politique):
    config = pa.configuration(RAW, "discret", False, politique, 35)
    assert config.politique_arrets == politique
    assert config.mode_arrets_dri == "discret"
    assert config.point_de_commande_j == 35.0
    assert config.point_de_commande_t == pytest.approx(35 * config.debit_pellets_t_par_h * 24)


def test_la_part_recuperee_rapporte_le_gain_a_la_perte_due_aux_arrets():
    assert pa.part_recuperee([10.0], [7.0], [4.0]) == pytest.approx(0.5)
    assert pa.part_recuperee([10.0], [4.0], [4.0]) == pytest.approx(1.0)
    assert pa.part_recuperee([10.0], [12.0], [4.0]) == pytest.approx(-1 / 3)
    assert math.isnan(pa.part_recuperee([4.0], [3.0], [4.0]))


def test_l_ic_de_la_part_encadre_la_part():
    aveugle = [10.0 + (k % 3) for k in range(60)]
    politique = [6.0 + (k % 3) for k in range(60)]
    continu = [2.0 + (k % 3) for k in range(60)]
    bas, haut = pa.bootstrap_part(aveugle, politique, continu, 300, 1)
    assert bas <= 0.5 <= haut


def _d(moyenne, demi):
    return Estimate(mean=moyenne, std=demi * 10 / 1.96, n=100)


@pytest.mark.parametrize("part,ic_bas,cout,confirme", [
    (0.8, 0.6, _d(0.0, 1e5), True),
    (0.8, 0.6, _d(-2e5, 1e5), True),
    (0.8, 0.4, _d(0.0, 1e5), False),     # l'IC descend sous 50 %
    (0.3, 0.2, _d(0.0, 1e5), False),
    (0.8, 0.6, _d(3e5, 1e5), False),     # coût significativement plus élevé
])
def test_la_regle_de_l_hypothese(part, ic_bas, cout, confirme):
    assert pa.verdict(part, ic_bas, cout)[0] is confirme


def _mesures():
    series = {c: [1.0, 2.0, 3.0] for c, _, _ in pa.INDICATEURS}
    return {v: {p: dict(series) for p in ("continu", *pa.POLITIQUES)} for v in pa.VARIANTES}


def test_les_mesures_se_relisent_depuis_json():
    import json
    mesures = _mesures()
    assert pa.depuis_json(json.loads(json.dumps(pa.vers_json(mesures, 3)))) == mesures


def _analyse():
    mesures = _mesures()
    for v in pa.VARIANTES:
        mesures[v]["aveugle"]["rupture_h_an"] = [10.0, 11.0, 12.0]
        mesures[v]["continu"]["rupture_h_an"] = [2.0, 3.0, 4.0]
        mesures[v]["rythme_moyen"]["rupture_h_an"] = [6.0, 7.0, 8.0]
        mesures[v]["date_de_reprise"]["rupture_h_an"] = [4.0, 5.0, 6.0]
    return pa.analyser(mesures, 50, 1)


def test_l_analyse_donne_la_part_de_chaque_politique():
    a = _analyse()[("discret", True)]
    assert a["rythme_moyen"]["part"] == pytest.approx(0.5)
    assert a["date_de_reprise"]["part"] == pytest.approx(0.75)


def test_les_donnees_de_figure_sont_en_pour_cent_kt_et_millions():
    donnees = pa.donnees_figure(_analyse())
    politique, part, *_ = donnees[("discret", True)][0]
    assert politique == "rythme_moyen"
    assert part == pytest.approx(50.0)
