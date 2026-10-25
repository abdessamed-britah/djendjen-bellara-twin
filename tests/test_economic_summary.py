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


# --- chiffre appliqué : le manque à produire en plage -----------------------


def test_le_manque_a_produire_se_deduit_du_taux_de_service():
    """β = 0,95 sur une cible de 4 Mt laisse 0,2 Mt sur la table."""
    assert eco.perte_depuis_beta([0.95], 4_000_000.0) == pytest.approx([200_000.0])


def test_un_taux_de_service_au_dessus_de_un_ne_rapporte_rien():
    """Produire au-delà de la cible n'est pas un gain monnayable, c'est du bruit."""
    assert eco.perte_depuis_beta([1.05], 4_000_000.0) == [0.0]


def test_le_manque_a_produire_suit_l_ordre_des_taux_de_service():
    pertes = eco.perte_depuis_beta([0.99, 0.95, 0.90], 4_000_000.0)
    assert pertes == sorted(pertes)


def test_la_plage_de_cout_croise_les_deux_incertitudes():
    """Borne basse = petit manque × tonne bon marché, borne haute = l'inverse."""
    quantiles = {0.05: 100_000.0, 0.50: 300_000.0, 0.95: 900_000.0}
    plage = eco.plage_de_cout([0.0] * 7, quantiles, marge_bas=10.0, marge_centre=22.0,
                              marge_haut=46.0)
    assert plage["cout_bas"] == pytest.approx(1_000_000.0)
    assert plage["cout_median"] == pytest.approx(6_600_000.0)
    assert plage["cout_haut"] == pytest.approx(41_400_000.0)
    assert plage["tirages"] == 7


def test_la_plage_de_cout_est_ordonnee():
    quantiles = {0.05: 100_000.0, 0.50: 300_000.0, 0.95: 900_000.0}
    plage = eco.plage_de_cout([], quantiles, 10.0, 22.0, 46.0)
    assert plage["cout_bas"] <= plage["cout_median"] <= plage["cout_haut"]


def test_un_manque_negatif_au_premier_quantile_ne_donne_pas_un_cout_negatif():
    """À β > 1 sur les meilleurs tirages, la borne basse doit valoir zéro, pas un crédit."""
    quantiles = {0.05: -50_000.0, 0.50: 0.0, 0.95: 200_000.0}
    plage = eco.plage_de_cout([], quantiles, 10.0, 22.0, 46.0)
    assert plage["cout_bas"] == 0.0


def test_le_scenario_du_chiffre_applique_est_celui_de_l_irregularite_mesuree():
    """Le prendre sur `phase2` figerait c_a² à 0 et le chiffre ne vaudrait plus pour le site."""
    assert eco.SCENARIO_MESURE == "phase2_mesure"
