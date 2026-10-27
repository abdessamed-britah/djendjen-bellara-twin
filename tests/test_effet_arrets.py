"""Tests de scripts/effet_arrets.py : continu contre discret, au même point de commande.

La comparaison n'a de sens que si les deux configurations ne diffèrent que par le mode du
four, et si la différence est bien « discret − continu » réplication par réplication. C'est
ce qui est vérifié, sans simuler.
"""

import dataclasses
import importlib.util
from pathlib import Path

import pytest

from corridor.sim.stats import estimate

_SPEC = importlib.util.spec_from_file_location(
    "effet_arrets", Path(__file__).parent.parent / "scripts" / "effet_arrets.py"
)
ea = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ea)


def test_le_point_de_commande_est_celui_de_l_etape_2():
    """Le même que celui pré-enregistré par la validation de l'étape 3 : une seule source."""
    spec = importlib.util.spec_from_file_location(
        "validation_ca2", Path(__file__).parent.parent / "scripts" / "validation_ca2.py")
    val = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(val)
    assert ea.POINT_DE_COMMANDE_ETAPE_2 == val.POINT_DE_COMMANDE_J
    assert set(ea.POINT_DE_COMMANDE_ETAPE_2.values()) <= set(ea.pc.POINTS_DE_COMMANDE_J)


@pytest.mark.parametrize("cle", list(ea.POINT_DE_COMMANDE_ETAPE_2))
def test_les_deux_configurations_ne_different_que_par_le_mode_du_four(cle):
    continu, discret = ea.configurations(*cle, ea.POINT_DE_COMMANDE_ETAPE_2[cle])
    assert continu.mode_arrets_dri == "continu"
    assert discret.mode_arrets_dri == "discret"
    assert dataclasses.replace(discret, mode_arrets_dri="continu") == continu


def test_le_point_de_commande_est_porte_en_jours_et_en_tonnes():
    continu, discret = ea.configurations("baseline_endogene", True, 25)
    for config in (continu, discret):
        assert config.point_de_commande_j == 25
        assert config.point_de_commande_t == pytest.approx(
            25 * config.debit_pellets_t_par_h * 24)


def _mesure(**series):
    n = len(next(iter(series.values())))
    lignes = [{c: v[i] for c, v in series.items()} for i in range(n)]
    return {"lignes": lignes, **{c: estimate(v) for c, v in series.items()}}


def test_la_difference_est_discret_moins_continu():
    valeurs = {cle: [1.0, 2.0, 3.0] for cle, *_ in ea.GRANDEURS}
    continu = _mesure(**valeurs)
    discret = _mesure(**{cle: [v + 0.5 for v in vs] for cle, vs in valeurs.items()})
    comparaison = ea.comparer(continu, discret)
    for cle, *_ in ea.GRANDEURS:
        assert comparaison[cle]["difference"].mean == pytest.approx(0.5)
        assert comparaison[cle]["significative"]


def test_le_taux_de_service_d_approvisionnement_est_publie_a_cote_du_brut():
    """En mode discret, β brut y ajoute l'aléa des pannes : il faut les deux pour lire."""
    cles = [cle for cle, *_ in ea.GRANDEURS]
    assert cles[:2] == ["beta_appro", "beta"]
    assert {"arrets_dri", "surestaries", "portage", "total"} <= set(cles)
