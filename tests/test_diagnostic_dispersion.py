"""Tests de scripts/diagnostic_dispersion.py : les indices corrigés et la règle écrite.

Le diagnostic n'a de valeur que si les deux côtés — données et simulation — passent par les
mêmes calculs, et si la règle de verdict est appliquée telle qu'elle a été écrite. C'est ce
qui est vérifié ici, sur des séries fabriquées et sur les bilans archivés, sans simuler.
"""

import importlib.util
import math
import statistics
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "diagnostic_dispersion",
    Path(__file__).parent.parent / "scripts" / "diagnostic_dispersion.py",
)
dd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dd)

Q = 142_700.0


# --- la règle écrite --------------------------------------------------------------


def test_la_regle_porte_sur_l_indice_choisi_avant_le_calcul():
    assert dd.INDICE_DU_VERDICT == "cv2_intra"
    assert dd.QUANTILES_DU_VERDICT == (0.05, 0.95)
    assert dd.SCENARIO_DU_VERDICT == "baseline_endogene"
    assert dd.INDICE_DU_VERDICT in dd.LIBELLES


def _positions(dedans_permis, dedans_interdits):
    def pos(dedans):
        return {dd.INDICE_DU_VERDICT: {"dedans": dedans}}
    return {(dd.SCENARIO_DU_VERDICT, True): pos(dedans_permis),
            (dd.SCENARIO_DU_VERDICT, False): pos(dedans_interdits)}


def test_dedans_sous_les_deux_hypotheses_n_est_pas_refute():
    assert dd.verdict(_positions(True, True))["texte"] == "non réfuté par les données"


def test_dehors_sous_les_deux_hypotheses_l_ecart_demeure():
    assert dd.verdict(_positions(False, False))["texte"] == "l'écart demeure"


def test_un_verdict_partage_est_dit_comme_tel():
    assert dd.verdict(_positions(True, False))["texte"] == "dépend de l'hypothèse de dépassement"


def test_la_position_est_bornee_par_les_quantiles_5_et_95():
    simules = [float(i) for i in range(101)]
    assert dd.position(50.0, simules)["dedans"]
    assert not dd.position(99.0, simules)["dedans"]
    assert not dd.position(1.0, simules)["dedans"]
    assert dd.position(50.0, simules)["rang"] == pytest.approx(51 / 101)


# --- les indices --------------------------------------------------------------------


def test_une_tendance_pure_n_a_aucune_variance_intra_annuelle():
    """C'est l'objet de la tâche 1 : une pente d'une année à l'autre n'est pas de l'irrégularité."""
    serie = [100.0] * 4 + [200.0] * 4 + [300.0] * 4 + [400.0] * 4
    assert dd.variance_intra_annuelle(serie) == 0.0
    assert dd.cv2(serie) > 0


def test_la_variance_intra_annuelle_a_n_moins_k_degres_de_liberte():
    serie = [1.0, 3.0, 1.0, 3.0] * 4
    # chaque année : moyenne 2, écarts ±1 → somme 16, divisée par 16 − 4
    assert dd.variance_intra_annuelle(serie) == pytest.approx(16 / 12)


def test_le_cv2_ne_depend_pas_de_la_taille_de_navire():
    tonnages = [500e3, 700e3, 900e3, 600e3] * 4
    assert dd.indices(tonnages, 70_000)["cv2_intra"] == dd.indices(tonnages, 160_000)["cv2_intra"]


def test_i_varie_exactement_comme_un_sur_q():
    tonnages = [500e3, 700e3, 900e3, 600e3] * 4
    courbe = dict(dd.i_selon_q(tonnages, (70_000, 140_000)))
    assert courbe[70_000] == pytest.approx(2 * courbe[140_000])


def test_la_taille_qui_atteint_une_cible_inverse_la_loi_en_un_sur_q():
    assert dd.taille_pour_atteindre(0.8, 140_000, 0.4) == pytest.approx(280_000)
    assert math.isinf(dd.taille_pour_atteindre(0.8, 140_000, 0.0))


def test_les_sous_ensembles_suivent_les_positions_des_donnees():
    """La première année joue 2022 et son troisième trimestre joue 2022 T3, des deux côtés."""
    tonnages = [Q * k for k in (5, 6, 0.01, 9, 5, 6, 5, 6, 5, 6, 5, 6, 5, 6, 5, 6)]
    r = dd.indices(tonnages, Q)
    navires = [t / Q for t in tonnages]
    assert r["i_premiere_annee"] == pytest.approx(dd.disp.index_of_dispersion(navires[:4]))
    assert r["i_trois_dernieres"] == pytest.approx(dd.disp.index_of_dispersion(navires[4:]))
    sans = navires[:2] + navires[3:]
    assert r["i_sans_t3"] == pytest.approx(dd.disp.index_of_dispersion(sans))
    assert r["i_brut"] == pytest.approx(dd.disp.index_of_dispersion(navires))


def test_le_cv2_et_i_sont_lies_par_le_nombre_moyen_de_navires():
    tonnages = [500e3, 700e3, 900e3, 600e3] * 4
    r = dd.indices(tonnages, Q)
    assert r["i_brut"] == pytest.approx(r["cv2_brut"] * statistics.fmean(tonnages) / Q)


def test_les_fenetres_simulees_commencent_aux_frontieres_d_annee():
    fenetres = dd.fenetres_annuelles(list(range(40)))
    assert [f[0] for f in fenetres] == [0, 16]
    assert all(len(f) == 16 for f in fenetres)


# --- exportations ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def exportations():
    return dd.lire_exportations()


def test_les_quatre_produits_sont_lus(exportations):
    cumuls, _ = exportations
    assert set(cumuls) == set(dd.PRODUITS)


def test_une_ligne_absente_ne_masque_pas_une_valeur_publiee_ailleurs(exportations):
    """La page 2022 n'a pas de ligne DRI ; la page 2023 en donne 67 205 t pour 2022."""
    cumuls, _ = exportations
    assert cumuls["CDRI"][(2022, "AN")] == pytest.approx(67_205)


def test_la_majorite_tranche_entre_trois_publications(exportations):
    """Le 1er semestre 2023 est publié trois fois ; une seule porte une coquille."""
    cumuls, _ = exportations
    assert cumuls["billettes"][(2023, "S1")] == pytest.approx(119_757)
    assert cumuls["fil machine"][(2023, "S1")] == pytest.approx(15_222)


def test_a_egalite_la_page_de_l_annee_l_emporte(exportations):
    """Rond à béton 2022 : 45 676 sur la page 2022, 42 645 recopié par erreur sur la 2023."""
    cumuls, _ = exportations
    assert cumuls["rond à béton"][(2022, "AN")] == pytest.approx(45_676)


def test_chaque_discordance_est_rendue_et_aucune_n_est_tranchee_en_silence(exportations):
    _, discordances = exportations
    cles = {(d["produit"], d["annee"], d["cumul"]) for d in discordances}
    assert ("rond à béton", 2022, "AN") in cles
    assert ("billettes", 2023, "S1") in cles


def test_les_series_d_exportation_ont_seize_trimestres_positifs(exportations):
    cumuls, _ = exportations
    series = dd.series_trimestrielles(cumuls, [2022, 2023, 2024, 2025])
    for nom, serie in series.items():
        assert len(serie) == 16, nom
        assert all(math.isfinite(v) and v >= 0 for v in serie), nom


# --- corrélations ----------------------------------------------------------------------


def test_une_relation_lineaire_parfaite_a_une_correlation_de_un():
    x = [float(i) for i in range(10)]
    c = dd.correlation_bootstrap(x, [2 * v + 1 for v in x], tirages=200)
    assert c["r"] == pytest.approx(1.0)
    assert c["bas"] == pytest.approx(1.0)


def test_le_decalage_compare_les_pellets_aux_exportations_du_trimestre_suivant():
    pellets = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    exportations = {"x": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]}
    corr = dd.correlations(pellets, exportations)
    assert corr[("x", 1)]["n"] == 5
    assert corr[("x", 0)]["r"] == pytest.approx(1.0)


def test_une_serie_constante_n_a_pas_de_correlation():
    assert math.isnan(dd.correlation([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]))


# --- tâche 4 ----------------------------------------------------------------------------


def test_les_tailles_observees_sont_lues_dans_la_serie():
    tailles = dd.tailles_observees()
    assert tailles
    assert all(t > 0 for _, t in tailles)


def test_sans_observations_aucune_taille(tmp_path):
    assert dd.tailles_observees(tmp_path / "absent.csv") == []


# --- figure -----------------------------------------------------------------------------


def _donnees():
    pos = {cle: {"observe": 0.5, "q05": 0.1, "mediane": 0.2, "q95": 0.3} for cle in dd.LIBELLES}
    positions = {(dd.SCENARIO_DU_VERDICT, True): pos}
    return dd.donnees_figure([(70_000, 1.0), (140_000, 0.5)], positions, [1.0] * 8,
                             {"a": [0.5] * 8, "b": [0.25] * 8}, [2022, 2023])


def test_les_donnees_de_figure_somment_les_exportations():
    d = _donnees()
    assert d["exportations_kt"] == pytest.approx([0.75e-3] * 8)
    assert len(d["trimestres"]) == 8


@pytest.mark.slow
def test_la_figure_est_un_png_non_vide(tmp_path):
    chemin = dd.write_figure(_donnees(), tmp_path / "d.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
