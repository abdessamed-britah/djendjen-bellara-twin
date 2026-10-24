"""Tests de scripts/arrival_dispersion.py : différenciation des cumuls, indice, bootstrap.

Le risque de ce script n'est pas de planter, c'est de produire un chiffre faux et
plausible : un cumul pris pour un trimestre, un recoupement qui ne recoupe rien, un indice
de dispersion mal normalisé. Ce sont ces trois choses qui sont testées ici.
"""

import importlib.util
import math
import random
import statistics
from pathlib import Path

import pytest

from corridor.ingest.port_stats import StatsPage

_SPEC = importlib.util.spec_from_file_location(
    "arrival_dispersion", Path(__file__).parent.parent / "scripts" / "arrival_dispersion.py"
)
ad = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ad)


def _page_html(label, gauche, droite):
    return (
        "<table><tr><td>Bobines en Acier</td><td>1</td><td>2</td><td>+1 %</td></tr>"
        f"<tr><td>{label}</td><td>{gauche}</td><td>{droite}</td><td>+1 %</td></tr>"
        "<tr><td>Total</td><td>9</td><td>9</td><td>+1 %</td></tr></table>"
    )


def _archive(tmp_path, valeurs):
    """Écrit une archive factice ; `valeurs` associe une page à ses deux tonnages."""
    pages = []
    for (cumul, g_annee, d_annee), (gauche, droite) in valeurs.items():
        page = StatsPage(f"bilan-{cumul}-{d_annee}", cumul, g_annee, d_annee)
        pages.append(page)
        chemin = tmp_path / f"{page.annee_droite}_{page.cumul}_{page.slug}.html"
        chemin.write_text(_page_html("Pellets", gauche, droite), encoding="utf-8")
    return tuple(pages)


# --- lecture et recoupement des cumuls --------------------------------------


def test_les_deux_colonnes_d_une_page_alimentent_deux_annees(tmp_path):
    pages = _archive(tmp_path, {("T1", 2024, 2025): (750067, 567848)})
    cumules, labels = ad.lire_cumuls(tmp_path, pages)
    assert cumules[(2024, "T1")] == 750067
    assert cumules[(2025, "T1")] == 567848
    assert labels == ["Pellets"]


def test_deux_publications_concordantes_passent(tmp_path):
    """Le même cumul publié deux fois avec la même valeur : c'est le cas normal."""
    pages = _archive(tmp_path, {
        ("T1", 2023, 2024): (582664, 750067),
        ("T1", 2024, 2025): (750067, 567848),
    })
    cumules, _ = ad.lire_cumuls(tmp_path, pages)
    assert cumules[(2024, "T1")] == 750067


def test_deux_publications_contradictoires_font_lever(tmp_path):
    """Le seul recoupement disponible : il doit arrêter le calcul, pas le laisser filer."""
    pages = _archive(tmp_path, {
        ("T1", 2023, 2024): (582664, 750067),
        ("T1", 2024, 2025): (999999, 567848),
    })
    with pytest.raises(ValueError, match="se contredisent"):
        ad.lire_cumuls(tmp_path, pages)


def test_une_page_absente_fait_lever(tmp_path):
    pages = (StatsPage("absente", "T1", 2024, 2025),)
    with pytest.raises(FileNotFoundError, match="port_stats"):
        ad.lire_cumuls(tmp_path, pages)


def test_une_page_sans_ligne_pellets_fait_lever(tmp_path):
    page = StatsPage("sans-pellets", "T1", 2024, 2025)
    (tmp_path / "2025_T1_sans-pellets.html").write_text(
        _page_html("Bobines", 1, 2), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="pellets"):
        ad.lire_cumuls(tmp_path, (page,))


# --- des cumuls aux trimestres ----------------------------------------------


def test_le_premier_cumul_est_le_premier_trimestre():
    cumules = {(2024, "T1"): 100.0, (2024, "S1"): 250.0,
               (2024, "9M"): 400.0, (2024, "AN"): 500.0}
    assert ad.trimestres(cumules) == [
        (2024, 1, 100.0), (2024, 2, 150.0), (2024, 3, 150.0), (2024, 4, 100.0)
    ]


def test_les_trimestres_somment_au_cumul_annuel():
    cumules = {(2022, "T1"): 437400.0, (2022, "S1"): 1182809.0,
               (2022, "9M"): 1183415.0, (2022, "AN"): 2539171.0}
    total = sum(t for _, _, t in ad.trimestres(cumules))
    assert total == pytest.approx(cumules[(2022, "AN")])


def test_une_annee_incomplete_est_ecartee():
    """Sans ses quatre cumuls, les trimestres d'une année seraient faux : on n'en fait rien."""
    cumules = {(2024, "T1"): 100.0, (2024, "S1"): 250.0,
               (2025, "T1"): 90.0, (2025, "S1"): 200.0,
               (2025, "9M"): 300.0, (2025, "AN"): 400.0}
    assert {a for a, _, _ in ad.trimestres(cumules)} == {2025}


def test_un_trimestre_quasi_nul_est_conserve_tel_quel():
    """2022 T3 vaut 606 t dans la vraie série : ce n'est pas au parseur de le corriger."""
    cumules = {(2022, "T1"): 437400.0, (2022, "S1"): 1182809.0,
               (2022, "9M"): 1183415.0, (2022, "AN"): 2539171.0}
    troisieme = next(t for _, q, t in ad.trimestres(cumules) if q == 3)
    assert troisieme == pytest.approx(606.0)


# --- indice de dispersion ---------------------------------------------------


def test_l_indice_est_nul_sur_une_serie_constante():
    """Arrivées parfaitement régulières : aucune variance, donc I = 0."""
    assert ad.index_of_dispersion([5.0] * 10) == pytest.approx(0.0)


def test_l_indice_vaut_un_sur_une_serie_de_poisson():
    """Vérification de la normalisation : pour un Poisson, Var = E, donc I ≈ 1."""
    rng = random.Random(20260924)
    tirage = [float(sum(rng.random() < 0.05 for _ in range(100))) for _ in range(4000)]
    assert ad.index_of_dispersion(tirage) == pytest.approx(1.0, rel=0.1)


def test_l_indice_est_la_variance_sur_la_moyenne():
    valeurs = [3.0, 5.0, 8.0, 2.0]
    attendu = statistics.variance(valeurs) / statistics.fmean(valeurs)
    assert ad.index_of_dispersion(valeurs) == pytest.approx(attendu)


def test_l_indice_grandit_avec_le_groupement():
    regulier = ad.index_of_dispersion([5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    groupe = ad.index_of_dispersion([0.0, 10.0, 0.0, 10.0, 0.0, 10.0])
    assert groupe > 1.0 > regulier


def test_l_indice_est_indefini_sans_dispersion_mesurable():
    assert math.isnan(ad.index_of_dispersion([5.0]))
    assert math.isnan(ad.index_of_dispersion([0.0, 0.0]))


# --- bootstrap --------------------------------------------------------------


def test_l_intervalle_encadre_l_indice_observe():
    valeurs = [3.0, 5.0, 8.0, 2.0, 6.0, 4.0, 7.0, 5.0]
    bas, haut, tirages = ad.bootstrap_ci(valeurs, 2000, random.Random(1))
    assert bas <= ad.index_of_dispersion(valeurs) <= haut
    assert len(tirages) > 1500


def test_l_intervalle_est_reproductible_a_graine_egale():
    valeurs = [3.0, 5.0, 8.0, 2.0, 6.0]
    a = ad.bootstrap_ci(valeurs, 500, random.Random(7))[:2]
    b = ad.bootstrap_ci(valeurs, 500, random.Random(7))[:2]
    assert a == b


def test_l_intervalle_se_resserre_quand_la_serie_s_allonge():
    rng = random.Random(3)
    courte = [rng.uniform(2, 8) for _ in range(8)]
    longue = courte * 12
    large = ad.bootstrap_ci(courte, 2000, random.Random(1))
    etroit = ad.bootstrap_ci(longue, 2000, random.Random(1))
    assert (etroit[1] - etroit[0]) < (large[1] - large[0])


def test_l_intervalle_est_indefini_sur_une_serie_trop_courte():
    bas, haut, tirages = ad.bootstrap_ci([4.0], 100, random.Random(1))
    assert math.isnan(bas) and math.isnan(haut)
    assert tirages == []


# --- repérage des trimestres anormaux ---------------------------------------


def test_un_trimestre_derisoire_est_signale():
    series = [(2022, 1, 437400.0), (2022, 2, 745409.0),
              (2022, 3, 606.0), (2022, 4, 1355756.0)]
    assert ad.anomalies(series) == [(2022, 3, 606.0)]


def test_une_serie_reguliere_ne_signale_rien():
    series = [(2024, q, 800000.0) for q in range(1, 5)]
    assert ad.anomalies(series) == []


def test_la_serie_vide_ne_signale_rien():
    assert ad.anomalies([]) == []
