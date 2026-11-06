"""Tests de scripts/rythme_de_marche.py : configuration D′, part réduite, sans simuler."""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "rythme_de_marche", Path(__file__).parent.parent / "scripts" / "rythme_de_marche.py"
)
rm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rm)

RAW = load_yaml(ASSUMPTIONS_PATH)


@pytest.mark.parametrize("depassements", [True, False])
def test_le_point_de_commande_est_compte_au_rythme_de_marche(depassements):
    config = rm.configuration_marche(RAW, depassements)
    assert config.mode_arrets_dri == "discret"
    assert config.depassements_autorises is depassements
    assert config.point_de_commande_t == pytest.approx(
        rm.ar.S_J * config.debit_pellets_plein_t_par_h * 24)
    # au rythme moyen, cela fait s / taux d'utilisation jours de consommation
    s_moyen = config.point_de_commande_t / (config.debit_pellets_t_par_h * 24)
    assert s_moyen == pytest.approx(rm.ar.S_J / config.taux_utilisation_dri, rel=1e-6)


def test_seul_le_point_de_commande_change_par_rapport_a_d():
    import dataclasses
    d = rm.ar.configuration(RAW, "discret", True)
    d_prime = rm.configuration_marche(RAW, True)
    assert dataclasses.replace(d_prime, point_de_commande_t=d.point_de_commande_t) == d


def test_la_part_reduite():
    assert rm.part_reduite([1.0], [5.0], [3.0]) == pytest.approx(0.5)
    assert rm.part_reduite([1.0], [5.0], [1.0]) == pytest.approx(1.0)
    assert rm.part_reduite([1.0], [5.0], [6.0]) == pytest.approx(-0.25)


def test_l_ic_de_la_part_reduite_l_encadre():
    continu = [1.0 + (k % 3) * 0.1 for k in range(80)]
    discret = [5.0 + (k % 5) * 0.2 for k in range(80)]
    marche = [3.0 + (k % 4) * 0.2 for k in range(80)]
    bas, haut = rm.bootstrap_part(continu, discret, marche, 400, 1)
    assert bas <= rm.part_reduite(continu, discret, marche) <= haut


def test_les_references_manquantes_font_lever(tmp_path):
    with pytest.raises(FileNotFoundError):
        rm.lire_references(tmp_path / "a.json", tmp_path / "b.json", 200)
