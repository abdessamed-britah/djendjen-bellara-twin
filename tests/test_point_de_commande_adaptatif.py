"""Tests de scripts/point_de_commande_adaptatif.py : recherche de s* et règle s*(cv)."""

import importlib.util
import json
from itertools import pairwise
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "point_de_commande_adaptatif",
    Path(__file__).parent.parent / "scripts" / "point_de_commande_adaptatif.py",
)
pca = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pca)

RAW = load_yaml(ASSUMPTIONS_PATH)


def test_la_grille_de_cv_couvre_la_fourchette_publiee():
    grille = pca.grille_cv(RAW)
    assert grille[0] == RAW["approvisionnement"]["cv_delai"]["range"][0]
    assert grille[-1] == RAW["approvisionnement"]["cv_delai"]["range"][1]
    assert len(grille) == pca.POINTS_CV


def test_la_grille_de_s_couvre_la_fourchette_publiee_au_pas_de_5_jours():
    grille = pca.grille_s(RAW)
    assert grille[0] == RAW["approvisionnement"]["point_de_commande_j"]["range"][0]
    assert grille[-1] == RAW["approvisionnement"]["point_de_commande_j"]["range"][1]
    assert all(b - a == 5 for a, b in pairwise(grille))


GRILLE = list(range(15, 61, 5))


def test_la_recherche_s_arrete_quand_le_minimum_a_deux_voisins_de_chaque_cote():
    moyennes = {25: 3.0, 30: 2.0, 35: 1.0, 40: 2.0, 45: 3.0}
    assert pca.points_a_ajouter(moyennes, GRILLE) == []


def test_la_recherche_s_etend_du_cote_du_minimum():
    assert pca.points_a_ajouter({30: 3.0, 35: 2.0, 40: 1.0}, GRILLE) == [45, 50]
    assert pca.points_a_ajouter({30: 1.0, 35: 2.0, 40: 3.0}, GRILLE) == [20, 25]


def test_un_bord_du_domaine_ferme_la_recherche():
    assert pca.points_a_ajouter({15: 1.0, 20: 2.0, 25: 3.0}, GRILLE) == []


def test_le_sommet_de_la_parabole():
    # y = (s − 33)² : sommet exact à 33
    s = [25, 30, 35, 40, 45]
    assert pca.sommet(s, [(x - 33) ** 2 for x in s]) == pytest.approx(33.0)


def test_le_sommet_reste_entre_les_voisins_du_minimum_et_vaut_le_bord_au_bord():
    assert pca.sommet([20, 25, 30], [1.0, 2.0, 3.0]) == 20.0
    assert pca.sommet([20, 25, 30], [2.0, 1.0, 1.0]) == pytest.approx(27.5)


def test_le_bootstrap_garde_l_appariement():
    series = {s: [(s - 33) ** 2 + bruit for bruit in (0.0, 0.5, -0.5, 0.2)] for s in
              (25, 30, 35, 40)}
    boot = pca.bootstrap_sommet(series, 100, 1)
    # le bruit est commun à tous les s : l'appariement le neutralise exactement
    assert all(b == pytest.approx(33.0) for b in boot)


def test_la_regle_lineaire_et_l_interpolation():
    assert pca.regle_lineaire([0.1, 0.3, 0.5], [20.0, 30.0, 40.0]) == pytest.approx((15.0, 50.0))
    points = [(0.1, 20.0), (0.3, 30.0), (0.6, 50.0)]
    assert pca.interpoler(points, 0.2) == pytest.approx(25.0)
    assert pca.interpoler(points, 0.45) == pytest.approx(40.0)
    assert pca.interpoler(points, 0.05) == 20.0
    assert pca.interpoler(points, 0.9) == 50.0


def test_une_configuration_porte_cv_et_s():
    config = pca.configuration(RAW, "discret", False, 0.5, 47.5)
    assert config.cv_delai_approvisionnement == 0.5
    assert config.point_de_commande_j == 47.5
    assert config.point_de_commande_t == pytest.approx(47.5 * config.debit_pellets_t_par_h * 24)
    assert config.mode_arrets_dri == "discret"
    assert not config.depassements_autorises


def _resultats():
    resultats = {}
    for variante in pca.VARIANTES:
        s_fixe = pca.fe.S_OPTIMAL_J[variante]
        par_cv = {}
        for cv, centre in ((0.1, 30), (0.6, 45)):
            par_cv[cv] = {s: {c: [float((s - centre) ** 2 + k) for k in range(4)]
                              for c in pca.CLES}
                          for s in sorted({centre - 10, centre - 5, centre, centre + 5,
                                           centre + 10, s_fixe})}
        resultats[variante] = par_cv
    return resultats


def test_les_mesures_se_relisent_depuis_json():
    resultats = _resultats()
    relu = pca.depuis_json(json.loads(json.dumps(pca.vers_json(resultats, 4))))
    assert relu == resultats


def test_l_analyse_trouve_s_etoile_et_le_gain_par_rapport_au_s_fixe():
    analyse = pca.analyser(_resultats(), 20, 1)
    lignes = analyse[("discret", True)]["lignes"]
    assert [ligne["s_etoile"] for ligne in lignes] == pytest.approx([30.0, 45.0])
    # s fixe 35 : (35 − 45)² = 100 de plus que l'optimum à cv 0,6
    assert lignes[1]["gain"]["total"].mean == pytest.approx(100.0)
    assert analyse[("discret", True)]["droite"][1] == pytest.approx(30.0)


def test_les_donnees_de_figure_sont_en_millions():
    donnees = pca.donnees_figure(pca.analyser(_resultats(), 20, 1))
    cv, s, _, _, gain, _ = donnees[("discret", True)][1]
    assert (cv, s) == (0.6, pytest.approx(45.0))
    assert gain == pytest.approx(100.0 / 1e6)
