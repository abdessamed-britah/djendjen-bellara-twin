"""Tests de scripts/validation_arrets.py : la nouvelle validation, avec arrêts discrets.

Ce qui est vérifié : que les règles écrites avant le calcul sont bien celles appliquées, que
la statistique est exactement celle de l'étape 4, que les capacités balayées sont les bornes
publiées, et que la règle de verdict tranche comme elle est écrite. Sans simuler, sauf un
contrôle court du découpage.
"""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "validation_arrets", Path(__file__).parent.parent / "scripts" / "validation_arrets.py"
)
va = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(va)

RAW = load_yaml(ASSUMPTIONS_PATH)


# --- règles ----------------------------------------------------------------------


def test_la_periode_de_reference_et_la_periode_complete_sont_publiees():
    assert va.PERIODE_DE_REFERENCE == "2023-2025"
    assert va.PERIODES == {"2023-2025": (2023, 2025), "2022-2025": (2022, 2025)}


def test_le_choix_de_la_periode_est_ecrit_comme_posterieur_a_l_etape_4():
    texte = " ".join(va.__doc__.split())
    assert "après l'étape 4" in texte


def test_le_verdict_porte_sur_les_quantiles_5_et_95():
    assert va.QUANTILES_DU_VERDICT == (0.05, 0.95)


def test_les_durees_balayees_couvrent_la_fourchette_de_l_hypothese():
    bas, haut = RAW["usine"]["duree_arret_non_planifie_j"]["range"]
    assert min(va.DUREES_ARRET_J) == bas
    assert max(va.DUREES_ARRET_J) == haut
    assert RAW["usine"]["duree_arret_non_planifie_j"]["value"] in va.DUREES_ARRET_J


def test_la_statistique_est_celle_de_l_etape_4():
    tonnages = [500e3, 700e3, 900e3, 600e3] * 3
    assert va.cv2_intra(tonnages) == pytest.approx(
        va.dd.indices(tonnages * 2, 142_700)["cv2_intra"], rel=1e-12)


def test_l_observe_2022_2025_retrouve_celui_de_l_etape_4():
    """Même statistique, mêmes données : 0,174 publié à l'étape 4."""
    assert va.observe((2022, 2025)) == pytest.approx(0.1744, abs=5e-4)


def test_une_periode_incomplete_est_refusee():
    with pytest.raises(ValueError, match="incomplète"):
        va.observe((2019, 2025))


# --- capacités et configurations ---------------------------------------------------


@pytest.mark.parametrize("niveau,indice", [("bas", 0), ("haut", 1)])
def test_les_capacites_sont_les_bornes_publiees(niveau, indice):
    port, usine = va.capacites(RAW, niveau)
    assert port == RAW["stockage_port"]["capacite_stockyard_pellets"]["range"][indice]
    assert usine == RAW["usine"]["capacite_stock_pellets_usine"]["range"][indice]


def test_le_centre_est_la_valeur_d_hypothese():
    port, usine = va.capacites(RAW, "centre")
    assert port == RAW["stockage_port"]["capacite_stockyard_pellets"]["value"]
    assert usine == RAW["usine"]["capacite_stock_pellets_usine"]["value"]


def test_une_case_de_la_carte_garde_la_disponibilite_et_le_point_de_commande():
    config = va.configuration(RAW, duree_j=20, stockage="haut")
    assert config.mode_arrets_dri == "discret"
    assert config.duree_arret_h == 20 * 24
    marche, arret = config.duree_marche_moyenne_h, config.duree_arret_h
    assert marche / (marche + arret) == pytest.approx(config.taux_utilisation_dri)
    assert config.point_de_commande_j == va.val.POINT_DE_COMMANDE_J[(va.SCENARIO, True)]
    assert config.stock_initial_usine_t == pytest.approx(
        config.capacite_stock_usine_t * config.fraction_stock_initial)


def test_cv_delai_n_est_touche_que_s_il_est_demande():
    assert va.configuration(RAW).cv_delai_approvisionnement == va.val.CV_DELAI_PREENREGISTRE
    assert va.configuration(RAW, cv_delai=0.6).cv_delai_approvisionnement == 0.6


def test_la_reference_continue_reste_continue():
    assert va.configuration(RAW, mode="continu").mode_arrets_dri == "continu"


# --- découpage et verdict ------------------------------------------------------------


def test_les_fenetres_ont_la_longueur_de_la_periode_et_sont_alignees():
    serie = list(range(48))
    douze = va.fenetres(serie, 3)
    assert [f[0] for f in douze] == [0, 12, 24, 36]
    assert all(len(f) == 12 for f in douze)
    assert len(va.fenetres(serie, 4)) == 3


def _series_constantes(cv2_par_fenetre):
    """Séries fabriquées dont chaque fenêtre de 3 ans a un CV² intra connu."""
    series = []
    for amplitude in cv2_par_fenetre:
        annee = [100.0 - amplitude, 100.0 + amplitude, 100.0 - amplitude, 100.0 + amplitude]
        series.append(annee * 3)
    return series


def test_un_observe_dans_la_bande_est_compatible():
    series = _series_constantes([1.0, 2.0, 3.0, 4.0, 5.0] * 4)
    milieu = va.cv2_intra([97.0, 103.0, 97.0, 103.0] * 3)
    r = va.juger(series, "2023-2025", milieu)
    assert r["compatible"]
    assert r["fenetres"] == 20


def test_un_observe_au_dessus_de_toute_la_distribution_ne_l_est_pas():
    series = _series_constantes([1.0, 2.0, 3.0] * 4)
    r = va.juger(series, "2023-2025", 1.0)
    assert not r["compatible"]
    assert r["rang"] == 1.0


def test_les_donnees_de_figure_sont_rangees_par_duree():
    r = {"q05": 0.01, "mediane": 0.02, "q95": 0.03}
    carte = {(0.25, True, "centre", d): {"2023-2025": r, "2022-2025": r} for d in (30, 2, 7)}
    donnees = va.donnees_figure(carte, {"2023-2025": 0.04, "2022-2025": 0.17})
    durees, *_ = donnees["series"][(0.25, True, "centre")]
    assert durees == [2, 7, 30]
    assert donnees["observe"] == 0.04


@pytest.mark.slow
def test_la_figure_est_un_png_non_vide(tmp_path):
    r = {"q05": 0.01, "mediane": 0.02, "q95": 0.03}
    carte = {(cv, True, s, d): {"2023-2025": r, "2022-2025": r}
             for cv in (0.1, 0.25) for s in va.NIVEAUX_STOCKAGE for d in (2, 30)}
    chemin = va.write_figure(carte, {"2023-2025": 0.04, "2022-2025": 0.17}, tmp_path / "a.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# --- étape 8 : la carte relue par paramètre ---------------------------------------


def _carte_minimale():
    def r(ok):
        return {p: {"mediane": 0.03, "q05": 0.01, "q95": 0.05, "compatible": ok}
                for p in va.PERIODES}
    return {(0.1, True, "bas", 2.0): r(False), (0.1, True, "bas", 7.0): r(True),
            (0.6, True, "bas", 2.0): r(True), (0.25, False, "haut", 7.0): r(True)}


def test_la_carte_se_relit_depuis_json():
    import json
    carte = _carte_minimale()
    assert va.depuis_json(json.loads(json.dumps(va.vers_json(carte)))) == carte


def test_la_compatibilite_se_compte_par_valeur_d_un_axe():
    carte = _carte_minimale()
    assert va.compatibilite_par(carte, "2023-2025", "cv_delai") == {
        0.1: (1, 2), 0.25: (1, 1), 0.6: (1, 1)}
    assert va.compatibilite_par(carte, "2023-2025", "duree_j") == {2.0: (1, 2), 7.0: (2, 2)}


def test_la_compatibilite_par_cv_peut_se_restreindre_a_une_hypothese_de_depassement():
    parts = va.compatibilite_par(_carte_minimale(), "2023-2025", "cv_delai", depassements=True)
    assert parts == {0.1: (1, 2), 0.6: (1, 1)}
