"""Tests de scripts/chiffre_site_endogene.py : le chiffre du site sur le modèle corrigé.

Sans simuler : les tirages restent dans les fourchettes publiées et sont reproductibles, la
configuration d'un tirage garde la disponibilité du four, les quantiles, la part de l'aléa
et l'importance des paramètres se calculent comme écrit.
"""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "chiffre_site_endogene", Path(__file__).parent.parent / "scripts" / "chiffre_site_endogene.py"
)
cs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cs)

RAW = load_yaml(ASSUMPTIONS_PATH)


def test_les_quatre_parametres_inconnus_sont_tires_dans_leur_fourchette():
    bornes = cs.fourchettes(RAW)
    assert bornes["stockyard_t"] == (150_000.0, 600_000.0)
    assert bornes["usine_t"] == (50_000.0, 300_000.0)
    assert bornes["duree_arret_j"] == (2.0, 30.0)
    assert bornes["cv_delai"] == (0.10, 0.60)
    for t in cs.tirer(RAW, 200, 1):
        for nom, (bas, haut) in bornes.items():
            assert bas <= t[nom] <= haut


def test_les_tirages_sont_reproductibles_et_les_graines_distinctes():
    a, b = cs.tirer(RAW, 50, 7), cs.tirer(RAW, 50, 7)
    assert a == b
    graines = [t["graine_long"] for t in a] + [t["graine_annee"] for t in a]
    assert len(set(graines)) == len(graines)


def test_port_et_usine_sont_tires_independamment():
    tirages = cs.tirer(RAW, 400, 3)
    rho = cs.statistics.correlation([t["stockyard_t"] for t in tirages],
                                    [t["usine_t"] for t in tirages])
    assert abs(rho) < 0.15


@pytest.mark.parametrize("variante", cs.VARIANTES)
def test_un_tirage_garde_la_disponibilite_et_le_point_de_commande(variante):
    t = {"stockyard_t": 400e3, "usine_t": 80e3, "duree_arret_j": 20.0, "cv_delai": 0.5}
    config = cs.configuration(RAW, *variante, t)
    assert config.capacite_stockyard_t == 400e3
    assert config.stock_initial_stockyard_t == pytest.approx(400e3 * config.fraction_stock_initial)
    assert config.duree_arret_h == 480.0
    marche = config.duree_marche_moyenne_h
    assert marche / (marche + 480.0) == pytest.approx(config.taux_utilisation_dri)
    assert config.cv_delai_approvisionnement == 0.5
    assert config.point_de_commande_j == cs.fe.S_OPTIMAL_J[variante]
    assert config.postes_minerai == 1


def test_aux_valeurs_centrales_on_retrouve_la_case_de_base_du_plan():
    base = cs.fe.configuration(RAW, "discret", True, 1, "base")
    assert cs.configuration(RAW, "discret", True, cs.centres(RAW)) == base


def test_les_quantiles_interpolent():
    valeurs = list(range(101))
    assert cs.resume(valeurs) == {"q05": 5.0, "q50": 50.0, "q95": 95.0}
    assert cs.quantile([1.0, 2.0], 0.5) == 1.5


def test_la_part_de_l_alea_est_bornee():
    assert cs.part_alea([1, 2, 3, 4], [2, 2.5]) == pytest.approx(0.125 / (5 / 3))
    assert cs.part_alea([1, 1.1], [0, 10]) == 1.0
    assert cs.part_alea([1, 1], [0, 1]) == 0.0


def test_l_importance_trouve_le_parametre_qui_pilote():
    tirages = cs.tirer(RAW, 100, 5)
    valeurs = [t["cv_delai"] * 2 for t in tirages]
    imp = cs.importance(tirages, valeurs)
    assert imp["cv_delai"] == pytest.approx(1.0)
    assert max(v for n, v in imp.items() if n != "cv_delai") < 0.1


def test_part_au_dessus_du_seuil():
    assert cs.part_au_dessus([0.9, 0.95, 0.99, 1.0], 0.95) == 0.75


def _resultats():
    lignes = [{"beta_appro": 0.9 + k / 100, "total": 1e6 * k} for k in range(5)]
    return {"tirages": {(v, h): lignes for v in cs.VARIANTES for h, _ in cs.HORIZONS}}


def test_les_donnees_de_figure_sont_des_repartitions_a_l_echelle():
    donnees = cs.donnees_figure(_resultats())
    x, y = donnees[("beta_appro", cs.VARIANTES[0], "long_terme")]
    assert x == pytest.approx([90, 91, 92, 93, 94])
    assert y[-1] == 1.0
    x, _ = donnees[("total", cs.VARIANTES[0], "une_annee")]
    assert x == pytest.approx([0, 1, 2, 3, 4])


@pytest.mark.slow
def test_une_figure_par_langue(tmp_path):
    chemins = cs.write_figure(_resultats(), tmp_path / "s.png")
    assert [c.name for c in chemins] == ["s.png", "s_en.png"]
    assert all(c.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for c in chemins)


def test_les_tirages_se_relisent_depuis_le_csv(tmp_path):
    tirages = cs.tirer(RAW, 5, 2)
    lignes = [{"beta_appro": 0.9 + k / 100, "total": 1e6 * k, "arrets_dri": 1.0,
               "surestaries": 2.0, "portage": 3.0} for k in range(5)]
    resultats = {"tirages": {(v, h): lignes for v in cs.VARIANTES for h, _ in cs.HORIZONS}}
    chemin = cs.ecrire_csv(tirages, resultats, tmp_path / "t.csv")
    relus, relu = cs.lire_csv(chemin)
    assert relu == resultats
    assert relus == [{n: t[n] for n, _ in cs.PARAMETRES} for t in tirages]
