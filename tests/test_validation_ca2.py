"""Tests de scripts/validation_ca2.py : les règles pré-enregistrées et leur application.

Ce script prononce un verdict de validation. Ce qui compte ici, c'est qu'il ne puisse pas
tricher : refuser de tourner si `cv_delai` a bougé, calculer I exactement comme sur les
données du port, juger contre des fenêtres de 16 trimestres, appliquer la règle de bande telle
qu'elle est écrite. Tout est testé sur des séries fabriquées, sans simuler.
"""

import importlib.util
import math
import statistics
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "validation_ca2", Path(__file__).parent.parent / "scripts" / "validation_ca2.py"
)
va = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(va)

RAW = load_yaml(ASSUMPTIONS_PATH)


# --- règles pré-enregistrées ---------------------------------------------------


def test_les_bandes_sont_celles_qui_ont_ete_fixees():
    assert va.BANDE_LARGE == (0.21, 1.73)
    assert va.BANDE_ETROITE == (0.45, 0.83)
    assert va.TRIMESTRES_PAR_FENETRE == 16


def test_le_cv_pre_enregistre_est_celui_des_hypotheses():
    """S'ils divergent un jour, le script refuse : ce test le signale avant."""
    assert va.CV_DELAI_PREENREGISTRE == RAW["approvisionnement"]["cv_delai"]["value"]


def test_le_script_refuse_si_cv_delai_a_change_dans_les_hypotheses():
    modifie = {**RAW, "approvisionnement": {**RAW["approvisionnement"],
                                            "cv_delai": {**RAW["approvisionnement"]["cv_delai"],
                                                         "value": 0.45}}}
    with pytest.raises(va.ReglesViolees, match="cv_delai"):
        va.verifier_preenregistrement(modifie)


def test_le_script_refuse_si_un_scenario_modifie_cv_delai():
    config = va.configuration("baseline_endogene", True, cv_delai=0.5)
    with pytest.raises(va.ReglesViolees, match="scénario"):
        va.verifier_preenregistrement(RAW, [config])


def test_les_configurations_centrales_passent_les_garde_fous():
    configs = [va.configuration(sc, h) for sc in va.SCENARIOS for h, _ in va.HYPOTHESES]
    va.verifier_preenregistrement(RAW, configs)


def test_seul_s_est_fixe_par_l_etape_2_et_il_est_dans_sa_fourchette():
    bas, haut = RAW["approvisionnement"]["point_de_commande_j"]["range"]
    assert all(bas <= s <= haut for s in va.POINT_DE_COMMANDE_J.values())
    assert set(va.POINT_DE_COMMANDE_J) == {(sc, h) for sc in va.SCENARIOS
                                           for h, _ in va.HYPOTHESES}


def test_la_configuration_porte_s_en_jours_et_en_tonnes():
    config = va.configuration("phase2_endogene", False)
    assert config.point_de_commande_j == 35
    assert config.point_de_commande_t == pytest.approx(
        35 * config.debit_pellets_t_par_h * 24)
    assert config.depassements_autorises is False


def test_le_balayage_couvre_toute_la_fourchette_de_cv():
    bas, haut = RAW["approvisionnement"]["cv_delai"]["range"]
    assert min(va.CV_DELAI_BALAYAGE) == bas
    assert max(va.CV_DELAI_BALAYAGE) == haut
    assert va.CV_DELAI_PREENREGISTRE in va.CV_DELAI_BALAYAGE


# --- la statistique, comme sur les données du port -----------------------------


def test_les_comptages_sont_le_tonnage_decharge_divise_par_la_taille():
    periodes = [{"debut_h": 0.0, "debarque_t": 300.0}, {"debut_h": 100.0, "debarque_t": 450.0},
                {"debut_h": 200.0, "debarque_t": 150.0}]
    assert va.comptages_trimestriels(periodes, 150.0, echauffement_h=100.0) == [3.0, 1.0]


def test_l_indice_est_celui_du_script_des_donnees_reelles():
    """Même fonction, donc même variance d'échantillon : c'est la règle 1."""
    valeurs = [5.0, 6.0, 4.0, 7.0, 5.0]
    assert va.disp.index_of_dispersion(valeurs) == pytest.approx(
        statistics.variance(valeurs) / (sum(valeurs) / len(valeurs)))


def test_un_comptage_constant_a_un_indice_nul():
    assert va.indices_par_fenetre([5.0] * 20) == [0.0] * 5


def test_les_fenetres_glissent_d_un_trimestre_sur_seize():
    comptages = list(range(1, 21))
    indices = va.indices_par_fenetre([float(c) for c in comptages])
    assert len(indices) == 20 - 16 + 1


def test_trop_peu_de_trimestres_ne_donne_aucune_fenetre():
    assert va.indices_par_fenetre([1.0] * 15) == []


# --- règle de bande -------------------------------------------------------------


def test_une_distribution_sous_la_bande_est_trop_reguliere():
    assert va.verdict_bande([0.05] * 99 + [0.5], (0.45, 0.83)) == "trop régulier"


def test_une_distribution_au_dessus_est_trop_irreguliere():
    assert va.verdict_bande([1.5] * 99 + [0.5], (0.45, 0.83)) == "trop irrégulier"


def test_cinq_pour_cent_de_fenetres_dans_la_bande_suffisent():
    """La règle 4 telle qu'écrite : l'intervalle à 90 % doit rencontrer la bande."""
    assert va.verdict_bande([0.1] * 95 + [0.5] * 5, (0.45, 0.83)) == "compatible"
    assert va.verdict_bande([0.1] * 96 + [0.5] * 4, (0.45, 0.83)) == "trop régulier"


def test_le_resume_porte_les_deux_probabilites_demandees():
    r = va.resume_indices([0.1] * 90 + [0.5] * 6 + [0.9] * 4, fenetres_disjointes=6)
    assert r["p_083"] == pytest.approx(0.04)
    assert r["p_045"] == pytest.approx(0.10)
    assert r["verdict_large"] == "compatible"
    assert r["verdict_etroite"] == "compatible"
    assert r["fenetres_disjointes"] == 6


# --- contrôles de regroupement --------------------------------------------------


def test_les_intervalles_sont_calcules_sur_les_dates_triees():
    assert va.intervalles([10.0, 0.0, 4.0]) == [4.0, 6.0]


def test_des_arrivees_regulieres_ont_un_cv2_nul():
    assert va.cv2([24.0] * 10) == 0.0


def test_la_part_sous_un_seuil_est_stricte():
    assert va.part_sous([47.0, 48.0, 49.0], 48.0) == pytest.approx(1 / 3)


def test_la_chance_de_revoir_l_observe_suit_la_binomiale():
    """1 intervalle court sur 2 : la chance d'en voir au moins un vaut 1 − (1 − p)²."""
    assert va.proba_au_moins_k_sur_n(0.1, 1, 2) == pytest.approx(1 - 0.9 ** 2)
    assert va.proba_au_moins_k_sur_n(0.3, 1, 1) == pytest.approx(0.3)
    assert va.proba_au_moins_k_sur_n(0.5, 0, 3) == pytest.approx(1.0)


def test_l_observe_est_lu_dans_les_donnees():
    obs = va.observe()
    assert obs["attentes_h"], "BERGE NIMBA doit y figurer"
    assert any(x < va.INTERVALLE_COURT_H for x in obs["intervalles_h"])


def test_sans_fichier_d_observations_rien_n_est_compare(tmp_path):
    assert va.observe(tmp_path / "absent.csv") == {"intervalles_h": [], "attentes_h": []}


def test_l_observe_courant_retrouve_les_bandes_pre_enregistrees():
    """Si la série de bilans est révisée, le script refusera : ce test le dit avant."""
    i_tout, i_sans = va.i_observe_courant(RAW)
    assert i_tout == pytest.approx(va.BANDE_ETROITE[1], abs=0.005)
    assert i_sans == pytest.approx(va.BANDE_ETROITE[0], abs=0.005)


# --- figure -------------------------------------------------------------------


def _balayage():
    ligne = {"q05": 0.02, "mediane": 0.05, "q95": 0.1}
    return {(sc, h): {cv: ligne for cv in (0.1, 0.25, 0.6)}
            for sc in va.SCENARIOS for h, _ in va.HYPOTHESES}


def test_les_donnees_de_figure_sont_triees_par_cv():
    cvs, bas, med, haut = va.donnees_figure(_balayage())[("baseline_endogene", True)]
    assert cvs == [0.1, 0.25, 0.6]
    assert all(b <= m <= h for b, m, h in zip(bas, med, haut, strict=True))
    assert not any(math.isnan(x) for x in med)


@pytest.mark.slow
def test_la_figure_est_un_png_non_vide(tmp_path):
    chemin = va.write_figure(_balayage(), tmp_path / "v.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
