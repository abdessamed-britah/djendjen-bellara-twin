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
    (1.0, "régime stationnaire avec pertes"),
    (0.0, "déficit borné"),
])
def test_chaque_issue_est_reconnue(p, issue):
    resultat = ch.verdict({"p_bas": p - 0.1, "p_haut": p + 0.1}, _lignes_croissantes())
    assert resultat["issue"] == issue


def test_un_intervalle_qui_couvre_deux_issues_est_indecidable():
    """Le script ne choisit pas après coup entre deux issues compatibles."""
    resultat = ch.verdict({"p_bas": 0.3, "p_haut": 1.1}, _lignes_croissantes())
    assert resultat["issue"] == "indécidable"
    assert set(resultat["issues_compatibles"]) == {"marche sans dérive", "régime stationnaire avec pertes"}


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


# --- étape 1 : convergence de β en régime endogène --------------------------


def test_le_regime_stationnaire_est_lu_sur_p_egal_un():
    """Correction de la première version : p ≈ 1 n'est pas un « déficit structurel ».

    Un régime stationnaire avec ruptures perd une fraction constante de sa production : le
    déficit croît comme T. C'est l'issue attendue d'un modèle sain, pas un échec.
    """
    assert ch.ISSUES["régime stationnaire avec pertes"] == 1.0
    assert "déficit structurel" not in ch.ISSUES


def test_la_dispersion_n_est_forcee_qu_en_exogene():
    """En endogène, elle est sans effet : la forcer afficherait une configuration fausse."""
    assert ch.configuration("baseline").dispersion_arrivees == ch.DISPERSION
    endogene = ch.configuration("baseline_endogene")
    reference = SimConfig.from_scenario(load_scenario("baseline_endogene"))
    assert endogene.dispersion_arrivees == reference.dispersion_arrivees


def _deficits_vers_une_limite(beta_limite: float, bruit: float = 0.0, reps: int = 12):
    """Déficits d'un régime qui converge : β(T) = β∞ exactement, à un bruit près."""
    config = ch.configuration("baseline_endogene")
    rng = random.Random(5)
    return config, {
        h: [ch.cible_t(config, h) * (1 - beta_limite) * (1 + bruit * rng.uniform(-1, 1))
            for _ in range(reps)]
        for h in HORIZONS
    }


def test_un_beta_deja_a_sa_limite_ne_bouge_plus_au_dela_du_seuil():
    config, deficits = _deficits_vers_une_limite(0.98, bruit=0.2)
    differences = ch.convergence_beta(config, deficits)
    assert differences, "aucune paire au-delà du seuil : le critère ne mesurerait rien"
    assert all(d["de"] >= ch.SEUIL_CONVERGENCE_ANS for d in differences)
    assert not any(d["significative"] for d in differences)


def test_un_beta_qui_monte_encore_est_detecte():
    """La marche sans dérive : β(T) = 1 − c/√T, chaque horizon gagne quelque chose."""
    config = ch.configuration("baseline")
    rng = random.Random(2)
    deficits = {
        h: [ch.cible_t(config, h) * 0.15 / math.sqrt(h) * (1 + 0.02 * rng.uniform(-1, 1))
            for _ in range(12)]
        for h in HORIZONS
    }
    differences = ch.convergence_beta(config, deficits)
    assert any(d["significative"] for d in differences)
    assert all(d["difference"].mean > 0 for d in differences)


def test_les_paires_avant_le_seuil_sont_ignorees():
    config, deficits = _deficits_vers_une_limite(0.98)
    assert all(d["de"] >= 8 for d in ch.convergence_beta(config, deficits, seuil=8))
    assert len(ch.convergence_beta(config, deficits, seuil=1)) == len(HORIZONS) - 1


def test_l_autocorrelation_d_une_serie_alternee_est_negative():
    assert ch.autocorrelation([1.0, -1.0] * 10) < -0.9


def test_l_autocorrelation_d_une_tendance_est_positive():
    assert ch.autocorrelation([float(i) for i in range(20)]) > 0.8


def test_l_autocorrelation_d_une_serie_trop_courte_ou_constante_est_indefinie():
    assert math.isnan(ch.autocorrelation([1.0, 2.0]))
    assert math.isnan(ch.autocorrelation([3.0] * 10))


def test_deux_estimateurs_proches_s_accordent():
    a = ch.estimate([0.980, 0.982, 0.978, 0.981])
    b = ch.estimate([0.979, 0.983, 0.981, 0.980])
    assert ch.accord_des_estimateurs(a, b)["accord"]


def test_deux_estimateurs_eloignes_se_contredisent():
    a = ch.estimate([0.950, 0.951, 0.949, 0.950])
    b = ch.estimate([0.990, 0.991, 0.989, 0.990])
    resultat = ch.accord_des_estimateurs(a, b)
    assert not resultat["accord"]
    assert resultat["ecart"] == pytest.approx(0.040, abs=1e-6)


def test_l_ic_de_l_ecart_combine_les_deux_demi_largeurs_en_quadrature():
    """Les deux estimateurs ne partagent aucune réplication : pas d'appariement possible."""
    a = ch.estimate([0.97, 0.99, 0.98, 0.98])
    b = ch.estimate([0.96, 1.00, 0.98, 0.98])
    attendu = math.sqrt(a.half_width ** 2 + b.half_width ** 2)
    assert ch.accord_des_estimateurs(a, b)["demi_largeur"] == pytest.approx(attendu)


def test_la_convergence_exige_la_stabilite_et_l_accord():
    stable = [{"significative": False}]
    instable = [{"significative": True}]
    assert ch.verdict_convergence(stable, {"accord": True})["converge"]
    assert not ch.verdict_convergence(stable, {"accord": False})["converge"]
    assert not ch.verdict_convergence(instable, {"accord": True})["converge"]


def test_sans_paire_au_dela_du_seuil_on_ne_conclut_pas_a_la_convergence():
    """Une liste vide n'est pas une preuve de stabilité : elle ne prouve rien."""
    assert not ch.verdict_convergence([], {"accord": True})["converge"]


def test_la_part_des_ruptures_se_lit_en_heures_ouvrees():
    config = ch.configuration("baseline_endogene")

    class Faux:
        def __init__(self, heures):
            self.dri_stop_hours = heures

    heures_an = config.heures_ouvrees_par_an
    parts = ch.part_des_ruptures(config, {2: [Faux(0.01 * heures_an * 2)] * 3})
    assert parts[2].mean == pytest.approx(0.01)


def test_les_moyennes_par_lots_ecartent_l_echauffement_et_comptent_les_lots():
    config = ch.configuration("baseline_endogene")
    lots = ch.moyennes_par_lots(config, seed=1, annees=5, echauffement=1, taille=2)
    assert lots["lots"] == 2  # (5 − 1) // 2
    assert lots["annees"] == 5
    assert 0.0 < lots["beta"].mean <= 1.0 + 1e-9
    assert 0.0 <= lots["part_annees_avec_rupture"] <= 1.0


def test_un_deficit_affine_fausse_l_exposant_global_mais_pas_l_exposant_tardif():
    """Le cas de phase2_endogene : régime stationnaire, démarrage en rupture.

    D = c·T + d avec d > 0 : la loi de puissance sur tous les horizons lit une pente
    inférieure à 1, restreinte aux horizons longs elle retrouve 1. C'est ce qui justifie
    l'exposant hors transitoire, ajouté après avoir vu le résultat de phase 2.
    """
    horizons = (1, 2, 4, 8, 16, 32, 50)
    deficits = {h: [0.009 * h + 0.01] * 5 for h in horizons}
    globale = ch.ajuste_loi_puissance(horizons, [d[0] for d in deficits.values()])["p"]
    tardifs = [h for h in horizons if h >= ch.SEUIL_CONVERGENCE_ANS]
    tardive = ch.ajuste_loi_puissance(tardifs, [deficits[h][0] for h in tardifs])["p"]
    assert globale < 0.85
    assert tardive == pytest.approx(1.0, abs=0.06)


def test_l_issue_unique_est_isolee_de_la_liste_des_compatibles():
    assert ch.issue_de({"p_bas": 0.9, "p_haut": 1.1}) == "régime stationnaire avec pertes"
    assert ch.issue_de({"p_bas": 0.3, "p_haut": 1.1}) == "indécidable"
    assert ch.issues_compatibles({"p_bas": 0.6, "p_haut": 0.8}) == []


@pytest.mark.slow
def test_la_figure_endogene_porte_la_limite_des_lots(tmp_path):
    lignes = _lignes_figure()
    ajustement = {"p": 1.0, "p_bas": 0.9, "p_haut": 1.1, "a": 1.0}
    chemin = ch.write_figure(lignes, ajustement, "baseline_endogene", 3, tmp_path / "e.png",
                             politique="point_de_commande",
                             beta_limite=ch.estimate([0.98, 0.99, 0.985]))
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
