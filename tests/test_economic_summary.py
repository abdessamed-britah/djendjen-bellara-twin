"""Tests de scripts/economic_summary.py : conversion perte → dollars, sans simulation."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml

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


# --- étape 2 : coût total en régime stationnaire ------------------------------



RAW = load_yaml(ASSUMPTIONS_PATH)


def _resultat(production_t, heures_rade=0.0, stockyard=0.0, usine=0.0, annees=2.0):
    """Le strict nécessaire de SimResult pour cout_total, sans simuler."""
    return SimpleNamespace(annees_mesurees=annees, dri_production_t=production_t,
                           anchorage_ship_hours=heures_rade, stockyard_mean_t=stockyard,
                           plant_stock_mean_t=usine)


@pytest.fixture(scope="module")
def endogene():
    return SimConfig.from_scenario(load_scenario("baseline_endogene"))


def _cible(config, annees):
    return config.debit_dri_t_par_h * config.heures_ouvrees_par_an * annees


def test_les_prix_unitaires_sont_lus_dans_les_hypotheses():
    u = eco.couts_unitaires(RAW)
    couts = RAW["couts"]
    assert u["surestaries_usd_h"] == couts["surestaries_usd_par_heure"]["value"]
    assert u["portage_usd_t_an"] == pytest.approx(
        couts["prix_pellets"]["value"] * couts["taux_de_portage"]["value"])


def test_les_bornes_des_prix_encadrent_la_valeur_centrale():
    bas, centre, haut = (eco.couts_unitaires(RAW, n) for n in ("bas", "value", "haut"))
    for cle in centre:
        assert bas[cle] < centre[cle] < haut[cle], cle


def test_sans_perte_ni_attente_ni_stock_le_cout_est_nul(endogene):
    couts = eco.cout_total(_resultat(_cible(endogene, 2.0)), endogene, eco.couts_unitaires(RAW))
    assert couts == {"arrets_dri": 0.0, "surestaries": 0.0, "portage": 0.0, "total": 0.0}


def test_le_cout_d_arret_est_la_production_manquante_par_an_fois_la_marge(endogene):
    u = eco.couts_unitaires(RAW)
    manque_t = 10_000.0
    couts = eco.cout_total(_resultat(_cible(endogene, 2.0) - manque_t), endogene, u)
    assert couts["arrets_dri"] == pytest.approx(manque_t / 2.0 * u["marge_usd_t"])


def test_produire_au_dela_de_la_cible_ne_rapporte_rien(endogene):
    couts = eco.cout_total(_resultat(_cible(endogene, 2.0) * 1.01), endogene,
                           eco.couts_unitaires(RAW))
    assert couts["arrets_dri"] == 0.0


def test_les_surestaries_sont_des_navire_heures_par_an(endogene):
    u = eco.couts_unitaires(RAW)
    couts = eco.cout_total(_resultat(_cible(endogene, 4.0), heures_rade=800.0, annees=4.0),
                           endogene, u)
    assert couts["surestaries"] == pytest.approx(200.0 * u["surestaries_usd_h"])


def test_le_portage_porte_sur_le_stock_physique_moyen(endogene):
    u = eco.couts_unitaires(RAW)
    couts = eco.cout_total(_resultat(_cible(endogene, 2.0), stockyard=100_000.0,
                                     usine=50_000.0), endogene, u)
    assert couts["portage"] == pytest.approx(150_000.0 * u["portage_usd_t_an"])


def test_le_total_est_la_somme_des_trois_termes(endogene):
    couts = eco.cout_total(_resultat(_cible(endogene, 2.0) - 5_000.0, 300.0, 80_000.0,
                                     40_000.0), endogene, eco.couts_unitaires(RAW))
    assert couts["total"] == pytest.approx(sum(couts[t] for t in eco.TERMES))


def test_l_estimation_du_total_est_la_somme_des_estimations_des_termes(endogene):
    """Moyenne linéaire : la somme des moyennes est la moyenne des sommes."""
    resultats = [_resultat(_cible(endogene, 2.0) - k * 1000, 10.0 * k, 1e5, 5e4)
                 for k in range(1, 6)]
    couts = eco.estimer_couts(resultats, endogene, eco.couts_unitaires(RAW))
    assert couts["total"].mean == pytest.approx(sum(couts[t].mean for t in eco.TERMES))


def test_les_scenarios_stationnaires_sont_les_endogenes():
    for nom in eco.SCENARIOS_STATIONNAIRES:
        config = SimConfig.from_scenario(load_scenario(nom))
        assert config.politique_approvisionnement == "point_de_commande", nom


def test_les_deux_hypotheses_de_depassement_sont_publiees():
    assert {h for h, _ in eco.HYPOTHESES_DEPASSEMENT} == {True, False}


def test_la_configuration_stationnaire_porte_l_hypothese_et_les_ecarts():
    config = eco.configuration_stationnaire("phase2_endogene", False,
                                            reporter_si_navire_en_rade=True)
    assert config.depassements_autorises is False
    assert config.reporter_si_navire_en_rade is True
