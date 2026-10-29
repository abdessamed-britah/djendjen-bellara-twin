"""Tests de scripts/attribution_ruptures.py : caractérisation, classes et règle, sans simuler."""

import importlib.util
import math
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "attribution_ruptures", Path(__file__).parent.parent / "scripts" / "attribution_ruptures.py"
)
ar = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ar)

RAW = load_yaml(ASSUMPTIONS_PATH)
L_H = 22 * 24.0


def test_les_variantes_sont_au_point_de_commande_de_l_etape_6():
    for variante in ar.VARIANTES:
        config = ar.configuration(RAW, *variante)
        assert config.point_de_commande_j == 35.0
        assert config.politique_arrets == "aveugle"


def _journal():
    # deux commandes : l'une à l'heure, l'autre arrivée avec 10 jours de retard
    return {
        "debut_h": 1000.0, "fin_h": 1000.0 + 30 * 24,
        "episodes": [{"debut_h": 1000.0 + 25 * 24, "duree_h": 6.0, "derniere_reprise_h": 1100.0,
                      "position_t": 3.0e5, "transit_t": 2.9e5}],
        "reprises": [(1100.0, 4.0e5)],
        "commandes": [(1000.0, 1000.0 + L_H, L_H), (1050.0, 1050.0 + L_H + 240.0, L_H)],
        "heures_marche_par_age_j": {0: 24.0, 1: 24.0},
    }


def test_un_episode_est_caracterise_en_jours_de_consommation():
    r = ar.caracteriser(_journal(), L_H, 1.0e4)
    (e,) = r["episodes"]
    assert e["age_j"] == pytest.approx((1000.0 + 25 * 24 - 1100.0) / 24)
    assert e["position_reprise_j"] == pytest.approx(40.0)
    assert e["transit_j"] == pytest.approx(29.0)
    assert e["retard_max_j"] == pytest.approx(10.0)


def test_la_reference_des_retards_couvre_la_fenetre_au_pas_choisi():
    r = ar.caracteriser(_journal(), L_H, 1.0e4, pas_reference_h=5 * 24)
    assert len(r["retards_reference_j"]) == 6


def test_les_classes_suivent_leur_ordre():
    seuil = 7.0
    assert ar.classer({"age_j": 3.0, "retard_max_j": 20.0}, 10, seuil) == "i"
    assert ar.classer({"age_j": 30.0, "retard_max_j": 20.0}, 10, seuil) == "ii"
    assert ar.classer({"age_j": 30.0, "retard_max_j": 2.0}, 10, seuil) == "iii"
    assert ar.classer({"age_j": 30.0, "retard_max_j": None}, 10, seuil) == "iii"


def test_n_est_le_plus_favorable_a_l_hypothese():
    # épisodes tous dans les 3 premiers jours, heures de marche réparties sur 30 jours
    ages = [0.5, 1.5, 2.5]
    heures = {a: 1.0 for a in range(30)}
    assert ar.choisir_n(ages, heures, range(1, 31)) == 3


def _rep(ages, retards_ep, reference, heures):
    return {"episodes": [{"age_j": a, "retard_max_j": r, "duree_h": 2.0}
                         for a, r in zip(ages, retards_ep, strict=True)],
            "retards_j": [1.0] * 9 + [10.0], "retards_reference_j": reference,
            "heures_par_age_j": heures}


def test_les_mesures_donnent_les_risques_relatifs():
    rep = _rep([1.0, 40.0], [8.0, 9.0], [8.0, 1.0, 1.0, 1.0], {0: 10.0, 30: 10.0})
    m = ar.mesures([rep], 10, 7.0)
    assert m["i"] == 0.5 and m["ii"] == 0.5
    assert m["risque"] == pytest.approx(0.5 / 0.5)
    assert m["retard_tous"] == 1.0
    assert m["risque_ii"] == pytest.approx(1.0 / 0.25)
    assert m["i_h"] == pytest.approx(0.5)


@pytest.mark.parametrize("part_i,ic,attendu", [
    (0.6, (2.5, 4.0), "confirmée"),
    (0.4, (2.5, 4.0), "non tranchée"),
    (0.1, (0.2, 1.1), "infirmée"),
    (0.1, (0.5, 1.6), "non tranchée"),
])
def test_la_regle_de_l_interpretation(part_i, ic, attendu):
    assert ar.verdict({"i": part_i}, {"risque": ic}) == attendu


def test_le_profil_de_risque_par_age():
    rep = _rep([1.0, 2.0, 12.0], [None, None, None], [], {1: 100.0, 12: 1000.0})
    profil = dict(ar.risque_par_age([rep], 5, 15))
    assert profil[0] == pytest.approx(1000 * 2 / 100)
    assert profil[10] == pytest.approx(1000 * 1 / 1000)
    assert math.isnan(profil[5])
