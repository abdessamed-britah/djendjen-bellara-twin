"""Tests de scripts/factoriel_endogene.py : le plan poste × stockage sur le modèle corrigé.

Ce qui est vérifié sans simuler : que les cases sont bien les bornes publiées, que chaque
variante porte son point de commande optimal, que les effets et l'interaction sont les bonnes
différences appariées, et que la règle « ne conclure que sur ce qui tient dans les quatre »
tranche comme elle est écrite.
"""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.stats import Estimate

_SPEC = importlib.util.spec_from_file_location(
    "factoriel_endogene", Path(__file__).parent.parent / "scripts" / "factoriel_endogene.py"
)
fe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fe)

RAW = load_yaml(ASSUMPTIONS_PATH)


def test_quatre_variantes_et_leur_optimum_de_l_etape_5():
    assert set(fe.VARIANTES) == {(m, d) for m in ("continu", "discret") for d in (True, False)}
    assert fe.S_OPTIMAL_J == {("continu", True): 30, ("continu", False): 35,
                              ("discret", True): 35, ("discret", False): 35}
    bas, haut = RAW["approvisionnement"]["point_de_commande_j"]["range"]
    assert all(bas <= s <= haut for s in fe.S_OPTIMAL_J.values())


def test_le_stockage_haut_est_le_haut_des_deux_fourchettes():
    port, usine = fe.capacites(RAW, "haut")
    assert port == RAW["stockage_port"]["capacite_stockyard_pellets"]["range"][1]
    assert usine == RAW["usine"]["capacite_stock_pellets_usine"]["range"][1]
    assert fe.capacites(RAW, "base") == (
        RAW["stockage_port"]["capacite_stockyard_pellets"]["value"],
        RAW["usine"]["capacite_stock_pellets_usine"]["value"])


@pytest.mark.parametrize("variante", fe.VARIANTES)
@pytest.mark.parametrize("case", fe.CASES)
def test_une_case_porte_ses_leviers_et_garde_le_reste(variante, case):
    config = fe.configuration(RAW, *variante, *case)
    assert config.politique_approvisionnement == "point_de_commande"
    assert config.mode_arrets_dri == variante[0]
    assert config.depassements_autorises == variante[1]
    assert config.postes_minerai == case[0]
    assert config.capacite_stockyard_t == fe.capacites(RAW, case[1])[0]
    assert config.stock_initial_usine_t == pytest.approx(
        config.capacite_stock_usine_t * config.fraction_stock_initial)
    s_j = fe.S_OPTIMAL_J[variante]
    assert config.point_de_commande_j == s_j
    assert config.point_de_commande_t == pytest.approx(s_j * config.debit_pellets_t_par_h * 24)


def test_le_point_de_commande_ne_change_pas_d_une_case_a_l_autre():
    configs = [fe.configuration(RAW, "discret", True, *case) for case in fe.CASES]
    assert len({c.point_de_commande_t for c in configs}) == 1


def _mesure(valeurs):
    return {"lignes": [{cle: v for cle, _, _ in fe.INDICATEURS} for v in valeurs]}


def test_les_effets_sont_des_differences_appariees_et_l_interaction_leur_ecart():
    cases = {(1, "base"): _mesure([1, 2, 3]), (2, "base"): _mesure([2, 3, 5]),
             (1, "haut"): _mesure([4, 4, 4]), (2, "haut"): _mesure([4, 6, 5])}
    e = fe.effets(cases)
    assert e[("poste_base", "total")].mean == pytest.approx((1 + 1 + 2) / 3)
    assert e[("stockage_1", "total")].mean == pytest.approx((3 + 2 + 1) / 3)
    assert e[("les_deux", "total")].mean == pytest.approx((3 + 4 + 2) / 3)
    # (0, 2, 1) − (1, 1, 2) = (−1, 1, −1)
    assert e[("interaction", "total")].mean == pytest.approx(-1 / 3)


def _e(moyenne, demi=0.1):
    return Estimate(mean=moyenne, std=demi * 10 / 1.96, n=100)


@pytest.mark.parametrize("moyennes,attendu", [
    ((1, 2, 3, 4), "hausse dans les quatre"),
    ((-1, -2, -3, -4), "baisse dans les quatre"),
    ((0, 0.01, -0.02, 0), "non significatif dans les quatre"),
    ((1, 2, 0, 0), "dépend de la variante (2/4 significatifs)"),
    ((1, -2, 3, 4), "signe opposé selon la variante (4/4 significatifs)"),
])
def test_la_regle_des_quatre_variantes(moyennes, attendu):
    par_variante = {v: {("poste_base", "total"): _e(m)}
                    for v, m in zip(fe.VARIANTES, moyennes, strict=True)}
    assert fe.robustesse(par_variante, "poste_base", "total") == attendu


def _par_variante():
    noms = [n for n, _, _ in fe.EFFETS] + ["interaction"]
    return {v: {(n, c): _e(1.0) for n in noms for c, _, _ in fe.INDICATEURS}
            for v in fe.VARIANTES}


def test_les_donnees_de_figure_sont_mises_a_l_echelle():
    donnees = fe.donnees_figure(_par_variante())
    assert set(donnees) == set(fe.PANNEAUX)
    _, moyenne, _ = donnees["beta_appro"][fe.VARIANTES[0]][0]
    assert moyenne == pytest.approx(100.0)
    _, moyenne, _ = donnees["total"][fe.VARIANTES[0]][0]
    assert moyenne == pytest.approx(1e-6)


@pytest.mark.slow
def test_une_figure_par_langue(tmp_path):
    chemins = fe.write_figure(_par_variante(), 3, tmp_path / "f.png")
    assert [c.name for c in chemins] == ["f.png", "f_en.png"]
    assert all(c.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for c in chemins)


def test_les_effets_se_relisent_a_l_identique_depuis_json():
    import json
    par_variante = _par_variante()
    relu = fe.depuis_json(json.loads(json.dumps(fe.vers_json(par_variante))))
    assert relu == par_variante
