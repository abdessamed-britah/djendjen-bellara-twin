"""Tests de scripts/regle_service.py : s_β, coût interpolé et sensibilité aux prix."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "regle_service", Path(__file__).parent.parent / "scripts" / "regle_service.py"
)
rs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rs)

GRILLE = list(range(15, 61, 5))


def test_les_cibles_et_les_facteurs():
    assert rs.CIBLES == (0.98, 0.99)
    assert rs.FACTEURS == (0.5, 1.0, 1.5)


def test_la_recherche_monte_jusqu_a_atteindre_la_cible():
    assert rs.points_service({30: 0.95, 35: 0.97}, GRILLE, 0.98) == [40]
    assert rs.points_service({30: 0.95, 35: 0.985}, GRILLE, 0.98) == []


def test_la_recherche_descend_pour_encadrer_par_le_bas():
    assert rs.points_service({30: 0.985, 35: 0.99}, GRILLE, 0.98) == [25]
    assert rs.points_service({15: 0.985}, GRILLE, 0.98) == []


def test_la_recherche_s_arrete_au_bout_du_domaine():
    assert rs.points_service({55: 0.95, 60: 0.97}, GRILLE, 0.98) == []


def test_s_beta_interpole_entre_les_deux_points_qui_encadrent():
    r = rs.s_beta([30, 35, 40], [0.96, 0.97, 0.99], 0.98, 5)
    assert r["statut"] == "encadre"
    assert r["s"] == pytest.approx(37.5)


def test_s_beta_aux_bords():
    assert rs.s_beta([15, 20], [0.99, 0.995], 0.98, 5)["statut"] == "sous_le_domaine"
    assert rs.s_beta([55, 60], [0.95, 0.97], 0.98, 5) == {"s": None, "statut": "hors_d_atteinte"}


def test_la_valeur_interpolee():
    assert rs.valeur_interpolee([30, 35], [10.0, 20.0], 32.5) == pytest.approx(15.0)


def test_le_total_recombine_multiplie_portage_et_surestaries():
    valeurs = {"arrets_dri": [1.0], "surestaries": [2.0], "portage": [3.0]}
    assert rs.total_recombine(valeurs, 0.5, 1.5) == [1.0 + 3.0 + 1.5]


def _evalues(centre):
    return {s: {"arrets_dri": [max(0.0, (centre - s)) ** 2] * 3,
                "surestaries": [0.0] * 3, "portage": [float(s)] * 3,
                "beta_appro": [min(1.0, 0.9 + s / 500)] * 3, "total": [0.0] * 3}
            for s in range(20, 61, 5)}


def test_s_etoile_monte_quand_le_portage_baisse():
    etoiles = rs.s_etoiles(_evalues(40))
    assert etoiles[(0.5, 1.0)] > etoiles[(1.0, 1.0)] > etoiles[(1.5, 1.0)]
    assert etoiles[(1.0, 0.5)] == etoiles[(1.0, 1.5)]


def test_les_donnees_de_figure():
    analyse = {v: [{"cv": 0.1, "s_etoile": 30.0, "sensibilite": {(1.0, 1.0): 30.0, (0.5, 1.0): 33.0},
                    "service": {0.98: {"s": 25.0}, 0.99: {"s": None}}}] for v in rs.VARIANTES}
    (ligne,) = rs.donnees_figure(analyse)[rs.VARIANTES[0]]
    assert ligne == (0.1, 30.0, 30.0, 33.0, 25.0, None)
