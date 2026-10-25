"""Tests de scripts/convergence_horizon.py : le diagnostic de la marche sans dérive.

Le script prononce un verdict entre trois issues écrites d'avance. Ce qui est testé ici est
donc la mécanique qui y mène — l'ajustement en loi de puissance, son intervalle par
bootstrap sur les réplications, la règle qui choisit l'issue — sur des déficits fabriqués
dont l'exposant est connu, sans simuler.
"""

import importlib.util
import math
import random
from pathlib import Path

import pytest

from corridor.sim.config import SimConfig, load_scenario

_SPEC = importlib.util.spec_from_file_location(
    "convergence_horizon", Path(__file__).parent.parent / "scripts" / "convergence_horizon.py"
)
ch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ch)

HORIZONS = (1, 2, 4, 8, 16, 32)


def _deficits(p: float, bruit: float = 0.0, reps: int = 20, seed: int = 1):
    """Déficits fabriqués D = 1000·T^p, avec un bruit multiplicatif optionnel."""
    rng = random.Random(seed)
    return {
        h: [1000.0 * h ** p * (1.0 + bruit * rng.uniform(-1, 1)) for _ in range(reps)]
        for h in HORIZONS
    }


# --- la dérive est nulle par construction ----------------------------------


@pytest.mark.parametrize("scenario", ["baseline", "phase2"])
def test_l_offre_moyenne_egale_exactement_la_demande(scenario):
    """C'est la prémisse du diagnostic : si elle tombe, l'étape 0 est à relire."""
    config = SimConfig.from_scenario(load_scenario(scenario))
    assert ch.derive_relative(config) == pytest.approx(0.0, abs=1e-12)


def test_le_scenario_du_diagnostic_est_a_arrivees_de_poisson():
    config = ch.configuration("baseline")
    assert config.dispersion_arrivees == pytest.approx(1.0)


def test_sans_stock_initial_les_deux_stocks_partent_de_zero():
    config = ch.configuration("baseline", initial=False)
    assert config.stock_initial_stockyard_t == 0.0
    assert config.stock_initial_usine_t == 0.0


def test_une_graine_par_replication_et_reproductible():
    assert ch.graines(5, 7) == ch.graines(5, 7)
    assert len(set(ch.graines(5, 7))) == 5


# --- loi de puissance ------------------------------------------------------


@pytest.mark.parametrize("p", [0.0, 0.5, 1.0])
def test_l_ajustement_retrouve_un_exposant_exact(p):
    moyennes = [1000.0 * h ** p for h in HORIZONS]
    ajuste = ch.ajuste_loi_puissance(HORIZONS, moyennes)
    assert ajuste["p"] == pytest.approx(p, abs=1e-9)
    assert ajuste["a"] == pytest.approx(1000.0, rel=1e-9)


def test_un_deficit_negatif_est_ecarte_et_non_tronque():
    """Le remplacer par une petite valeur positive fabriquerait une pente."""
    moyennes = [-50.0] + [1000.0 * h ** 0.5 for h in HORIZONS[1:]]
    ajuste = ch.ajuste_loi_puissance(HORIZONS, moyennes)
    assert ajuste["points"] == len(HORIZONS) - 1
    assert ajuste["p"] == pytest.approx(0.5, abs=1e-9)


def test_moins_de_deux_points_ne_donne_pas_d_exposant():
    ajuste = ch.ajuste_loi_puissance((1, 2), (-1.0, -2.0))
    assert math.isnan(ajuste["p"])


# --- intervalle de l'exposant ----------------------------------------------


def test_l_intervalle_encadre_l_exposant_vrai():
    resultat = ch.exposant(_deficits(0.5, bruit=0.3), tirages=400)
    assert resultat["p_bas"] <= 0.5 <= resultat["p_haut"]


def test_un_bruit_plus_fort_elargit_l_intervalle():
    etroit = ch.exposant(_deficits(0.5, bruit=0.05), tirages=400)
    large = ch.exposant(_deficits(0.5, bruit=0.6), tirages=400)
    assert (large["p_haut"] - large["p_bas"]) > (etroit["p_haut"] - etroit["p_bas"])


def test_le_bootstrap_est_reproductible():
    a = ch.exposant(_deficits(0.5, bruit=0.3), tirages=200, seed=3)
    b = ch.exposant(_deficits(0.5, bruit=0.3), tirages=200, seed=3)
    assert (a["p_bas"], a["p_haut"]) == (b["p_bas"], b["p_haut"])


# --- verdict ---------------------------------------------------------------


def _lignes_croissantes():
    return [{"beta": ch.estimate([0.90 + 0.01 * i] * 2)} for i in range(4)]


@pytest.mark.parametrize(("p", "issue"), [
    (0.5, "marche sans dérive"),
    (1.0, "déficit structurel"),
    (0.0, "déficit borné"),
])
def test_chaque_issue_est_reconnue(p, issue):
    resultat = ch.verdict({"p_bas": p - 0.1, "p_haut": p + 0.1}, _lignes_croissantes())
    assert resultat["issue"] == issue


def test_un_intervalle_qui_couvre_deux_issues_est_indecidable():
    """Le script ne choisit pas après coup entre deux issues compatibles."""
    resultat = ch.verdict({"p_bas": 0.3, "p_haut": 1.1}, _lignes_croissantes())
    assert resultat["issue"] == "indécidable"
    assert set(resultat["issues_compatibles"]) == {"marche sans dérive", "déficit structurel"}


def test_un_intervalle_qui_ne_couvre_aucune_issue_est_indecidable():
    resultat = ch.verdict({"p_bas": 0.65, "p_haut": 0.8}, _lignes_croissantes())
    assert resultat["issue"] == "indécidable"
    assert resultat["issues_compatibles"] == []


def test_la_croissance_de_beta_est_lue_sur_les_moyennes():
    decroissantes = [{"beta": ch.estimate([0.95 - 0.01 * i] * 2)} for i in range(4)]
    assert ch.verdict({"p_bas": 0.4, "p_haut": 0.6}, _lignes_croissantes())["beta_croissant"]
    assert not ch.verdict({"p_bas": 0.4, "p_haut": 0.6}, decroissantes)["beta_croissant"]


# --- stock initial ---------------------------------------------------------


def test_le_cadeau_du_stock_initial_decroit_en_un_sur_t():
    """Un même tonnage rapporté à une cible qui grandit avec T : la borne vaut S0/cible."""
    config = ch.configuration("baseline")
    avec = {1: [100.0, 100.0], 4: [400.0, 400.0]}
    sans = {1: [200.0, 200.0], 4: [500.0, 500.0]}
    parts = ch.part_du_stock_initial(avec, sans, config)
    assert parts[0]["borne"] == pytest.approx(4 * parts[1]["borne"])
    assert parts[0]["delta_beta"].mean == pytest.approx(4 * parts[1]["delta_beta"].mean)


def test_le_cadeau_mesure_est_positif_quand_le_stock_aide():
    config = ch.configuration("baseline")
    parts = ch.part_du_stock_initial({1: [100.0]}, {1: [300.0]}, config)
    assert parts[0]["delta_beta"].mean > 0


# --- données de la figure --------------------------------------------------


def _lignes_figure():
    config = ch.configuration("baseline")
    return ch.resume(config, {h: [1000.0 * h ** 0.5] * 3 for h in (1, 4, 16)})


def test_la_reference_racine_passe_par_le_premier_point():
    donnees = ch.donnees_figure(_lignes_figure())
    assert donnees["reference_racine"][0] == pytest.approx(donnees["deficit"][0])
    assert donnees["reference_racine"][1] == pytest.approx(2 * donnees["deficit"][0])


def test_les_series_ont_la_longueur_des_horizons():
    donnees = ch.donnees_figure(_lignes_figure())
    n = len(donnees["horizons"])
    for cle in ("beta", "beta_bas", "beta_haut", "deficit", "deficit_bas", "deficit_haut"):
        assert len(donnees[cle]) == n


def test_le_deficit_normalise_se_lit_en_annees_de_cible():
    config = ch.configuration("baseline")
    annuelle = ch.cible_t(config, 1.0)
    lignes = ch.resume(config, {2: [annuelle * 0.3] * 2})
    assert lignes[0]["deficit_normalise"].mean == pytest.approx(0.3)
    assert lignes[0]["beta"].mean == pytest.approx(1.0 - 0.15)


@pytest.mark.slow
def test_la_figure_est_un_png_non_vide(tmp_path):
    lignes = _lignes_figure()
    ajustement = {"p": 0.5, "p_bas": 0.4, "p_haut": 0.6, "a": 1.0}
    chemin = ch.write_figure(lignes, ajustement, "baseline", 3, tmp_path / "c.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
