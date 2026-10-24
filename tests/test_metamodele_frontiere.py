"""Tests de scripts/metamodele_frontiere.py : ajustement, validation, diagnostic de bruit.

Un métamodèle qui se trompe ne plante pas : il produit une formule plausible et fausse.
Les tests portent donc sur l'arithmétique vérifiable à la main — un ajustement doit
retrouver exactement les coefficients d'une relation sans bruit, une validation ne doit
jamais voir ses points de test, et le diagnostic d'incohérence doit compter ce qu'il dit.
"""

import importlib.util
import math
import random
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "metamodele_frontiere",
    Path(__file__).parent.parent / "scripts" / "metamodele_frontiere.py",
)
mm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mm)


def _point(c_a2, beta, tau, k=1):
    return {"k": k, "c_a2": c_a2, "beta": beta, "tau": tau}


def _serie_exacte(a=4.0, b=0.5, c=1.5):
    """Points qui suivent exactement la forme structurelle : l'ajustement doit la retrouver."""
    points = []
    for c_a2 in (0.25, 0.5, 0.75, 1.0, 1.25):
        for beta in (0.90, 0.93, 0.95, 0.97):
            tau = a + b * c_a2 / (1.0 - beta) ** c
            points.append(_point(c_a2, beta, tau))
    return points


# --- solveur linéaire -------------------------------------------------------


def test_le_solveur_resout_un_systeme_connu():
    solution = mm._resoudre([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
    assert solution[0] == pytest.approx(1.0)
    assert solution[1] == pytest.approx(3.0)


def test_le_solveur_supporte_un_pivot_nul_en_tete():
    """Sans pivot partiel, ce système ferait une division par zéro."""
    solution = mm._resoudre([[0.0, 1.0], [1.0, 0.0]], [2.0, 3.0])
    assert solution[0] == pytest.approx(3.0)
    assert solution[1] == pytest.approx(2.0)


def test_le_solveur_refuse_un_systeme_singulier():
    with pytest.raises(ValueError, match="singulier"):
        mm._resoudre([[1.0, 2.0], [2.0, 4.0]], [3.0, 6.0])


def test_les_moindres_carres_retrouvent_une_droite():
    a, b = mm._ols([1.0, 2.0, 3.0, 4.0], [3.0, 5.0, 7.0, 9.0])
    assert a == pytest.approx(1.0)
    assert b == pytest.approx(2.0)


# --- ajustement structurel --------------------------------------------------


def test_l_ajustement_structurel_retrouve_une_relation_exacte():
    """Sans bruit, les trois coefficients doivent revenir à la valeur qui a engendré."""
    modele = mm.fit_structurel(_serie_exacte(a=4.0, b=0.5, c=1.5))
    assert modele["c"] == pytest.approx(1.5, abs=0.05)
    assert modele["a"] == pytest.approx(4.0, abs=0.5)
    assert modele["b"] == pytest.approx(0.5, abs=0.05)
    assert modele["sse"] < 1e-6


def test_la_prediction_structurelle_est_exacte_sur_sa_propre_forme():
    points = _serie_exacte()
    modele = mm.fit_structurel(points)
    assert mm.rmse(points, mm.predire_structurel, modele) < 1e-3


def test_la_couverture_requise_croit_avec_l_irregularite_dans_le_modele():
    """Garde-fou de sens : un b négatif voudrait dire « plus erratique, moins de stock »."""
    modele = mm.fit_structurel(_serie_exacte())
    assert modele["b"] > 0


def test_la_couverture_requise_explose_quand_beta_tend_vers_un():
    modele = mm.fit_structurel(_serie_exacte())
    proche = mm.predire_structurel(modele, _point(1.0, 0.99, 0.0))
    loin = mm.predire_structurel(modele, _point(1.0, 0.90, 0.0))
    assert proche > loin


# --- modèle souple ----------------------------------------------------------


def test_le_modele_souple_absorbe_une_relation_exacte():
    points = _serie_exacte()
    modele = mm.fit_souple(points)
    assert mm.rmse(points, mm.predire_souple, modele) < 1.0


def test_le_modele_souple_a_bien_six_termes():
    modele = mm.fit_souple(_serie_exacte())
    assert len(modele["coefficients"]) == 6


def test_la_base_souple_contient_la_constante_et_les_deux_axes():
    phi = mm._base_souple(_point(0.5, 0.95, 0.0))
    assert phi[0] == 1.0
    assert phi[1] == pytest.approx(0.5)
    assert phi[2] == pytest.approx(20.0)  # 1/(1−0,95)


# --- erreurs ----------------------------------------------------------------


def test_les_erreurs_sont_nulles_sur_une_prediction_parfaite():
    points = [_point(0.5, 0.95, 10.0)]
    parfait = {"a": 10.0, "b": 0.0, "c": 1.0}
    assert mm.rmse(points, mm.predire_structurel, parfait) == pytest.approx(0.0)
    assert mm.mae(points, mm.predire_structurel, parfait) == pytest.approx(0.0)


def test_la_rmse_penalise_plus_les_grosses_erreurs_que_la_mae():
    points = [_point(0.5, 0.95, 10.0), _point(0.5, 0.95, 30.0)]
    modele = {"a": 10.0, "b": 0.0, "c": 1.0}
    assert mm.rmse(points, mm.predire_structurel, modele) > mm.mae(
        points, mm.predire_structurel, modele
    )


def test_les_erreurs_d_un_ensemble_vide_sont_indefinies():
    assert math.isnan(mm.rmse([], mm.predire_structurel, {"a": 0, "b": 0, "c": 1}))
    assert math.isnan(mm.mae([], mm.predire_structurel, {"a": 0, "b": 0, "c": 1}))


# --- validation -------------------------------------------------------------


def test_le_partage_aleatoire_ne_perd_ni_ne_duplique_aucun_point():
    points = _serie_exacte()
    apprentissage, test = mm.partage_aleatoire(points, seed=1)
    assert len(apprentissage) + len(test) == len(points)
    identifiants = [id(p) for p in apprentissage + test]
    assert len(set(identifiants)) == len(points)


def test_le_partage_aleatoire_est_reproductible():
    points = _serie_exacte()
    a1, t1 = mm.partage_aleatoire(points, seed=3)
    a2, t2 = mm.partage_aleatoire(points, seed=3)
    assert [p["tau"] for p in a1] == [p["tau"] for p in a2]
    assert [p["tau"] for p in t1] == [p["tau"] for p in t2]


def test_la_validation_par_colonne_ne_laisse_jamais_fuiter_le_test():
    """Le point clé : le modèle ne doit jamais avoir vu le c_a² sur lequel on le juge."""
    points = _serie_exacte()
    for c_a2, apprentissage, test in mm.validation_par_colonne(points):
        assert all(p["c_a2"] == c_a2 for p in test)
        assert all(p["c_a2"] != c_a2 for p in apprentissage)
        assert len(apprentissage) + len(test) == len(points)


def test_la_validation_par_colonne_couvre_toutes_les_colonnes():
    points = _serie_exacte()
    vues = {c for c, _, _ in mm.validation_par_colonne(points)}
    assert vues == {p["c_a2"] for p in points}


# --- jeu de données ---------------------------------------------------------


def test_les_points_censures_au_plancher_sont_ecartes(monkeypatch):
    """« τ* = plancher » veut dire « au plus », pas « exactement » : l'ajuster serait faux."""
    plancher = min(mm.TAU_DAYS)
    monkeypatch.setattr(
        mm, "couverture_requise",
        lambda grille, beta: {0.0: plancher, 0.5: 25.0, 1.0: None},
    )
    points = mm.dataset({1: {}}, betas=(0.95,))
    assert [p["c_a2"] for p in points] == [0.5]


def test_le_dataset_couvre_les_niveaux_demandes(monkeypatch):
    monkeypatch.setattr(mm, "couverture_requise", lambda grille, beta: {0.5: 20.0})
    points = mm.dataset({1: {}, 2: {}}, betas=(0.90, 0.95))
    assert len(points) == 4
    assert {p["beta"] for p in points} == {0.90, 0.95}
    assert {p["k"] for p in points} == {1, 2}


# --- diagnostic de bruit de la carte ----------------------------------------


def test_une_carte_parfaitement_monotone_n_a_aucune_violation():
    points = [_point(c, 0.95, 10.0 + 10 * c) for c in (0.0, 0.5, 1.0, 1.5)]
    bruit = mm.incoherence_de_la_carte(points)
    assert bruit["violations"] == 0
    assert bruit["part"] == 0.0
    assert bruit["recul_moyen"] == 0.0


def test_un_recul_de_tau_est_compte_comme_une_violation():
    """τ* qui baisse quand c_a² monte est physiquement impossible : c'est du bruit."""
    points = [_point(0.0, 0.95, 20.0), _point(0.5, 0.95, 12.0), _point(1.0, 0.95, 30.0)]
    bruit = mm.incoherence_de_la_carte(points)
    assert bruit["paires"] == 2
    assert bruit["violations"] == 1
    assert bruit["recul_moyen"] == pytest.approx(8.0)
    assert bruit["recul_max"] == pytest.approx(8.0)


def test_les_series_de_k_et_de_beta_sont_comparees_separement():
    """Comparer τ* d'un β à celui d'un autre inventerait des violations."""
    points = [
        _point(0.0, 0.95, 30.0), _point(0.5, 0.95, 40.0),
        _point(0.0, 0.90, 10.0), _point(0.5, 0.90, 20.0),
    ]
    assert mm.incoherence_de_la_carte(points)["violations"] == 0


def test_le_nombre_de_postes_separe_aussi_les_series():
    points = [
        _point(0.0, 0.95, 30.0, k=1), _point(0.5, 0.95, 40.0, k=1),
        _point(0.0, 0.95, 10.0, k=2), _point(0.5, 0.95, 20.0, k=2),
    ]
    assert mm.incoherence_de_la_carte(points)["violations"] == 0


# --- sorties ----------------------------------------------------------------


def test_le_verdict_dit_qu_on_ne_peut_pas_trancher_quand_le_bruit_domine(capsys):
    bruit = {"paires": 30, "violations": 9, "part": 0.3, "recul_moyen": 7.0, "recul_max": 15.0}
    mm.verdict(dispersion=16.0, moy_s=10.0, moy_f=9.0, err_s_alea=9.0, err_f_alea=5.0,
               bruit=bruit)
    sortie = capsys.readouterr().out
    assert "ne peut pas trancher" in sortie
    assert "ni validée ni réfutée" in sortie


def test_le_verdict_conclut_a_l_echec_quand_le_bruit_est_faible(capsys):
    bruit = {"paires": 30, "violations": 1, "part": 0.03, "recul_moyen": 0.2, "recul_max": 0.5}
    mm.verdict(dispersion=16.0, moy_s=12.0, moy_f=11.0, err_s_alea=11.0, err_f_alea=10.0,
               bruit=bruit)
    assert "Aucune forme simple ne tient" in capsys.readouterr().out


def test_le_verdict_conclut_au_succes_quand_l_erreur_est_petite(capsys):
    bruit = {"paires": 30, "violations": 1, "part": 0.03, "recul_moyen": 0.2, "recul_max": 0.5}
    mm.verdict(dispersion=16.0, moy_s=2.0, moy_f=2.0, err_s_alea=2.0, err_f_alea=2.0,
               bruit=bruit)
    assert "Une forme simple tient" in capsys.readouterr().out


def test_le_rapport_s_affiche_sans_lever(capsys):
    points = _serie_exacte()
    structurel = mm.fit_structurel(points)
    souple = mm.fit_souple(points)
    colonnes = [
        (c_a2, mm.rmse(test, mm.predire_structurel, mm.fit_structurel(app)),
         mm.rmse(test, mm.predire_souple, mm.fit_souple(app)), len(test))
        for c_a2, app, test in mm.validation_par_colonne(points)
    ]
    mm.print_rapport(points, structurel, souple,
                     mm.partage_aleatoire(points, seed=1), colonnes,
                     mm.apport_du_nombre_de_postes(points))
    sortie = capsys.readouterr().out
    assert "Validation 1" in sortie
    assert "Validation 2" in sortie


@pytest.mark.slow
def test_la_figure_est_un_png_non_vide(tmp_path):
    points = _serie_exacte()
    chemin = mm.write_figure(points, mm.fit_structurel(points), mm.fit_souple(points),
                             tmp_path / "meta.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert chemin.stat().st_size > 10_000


def test_l_ecart_type_sert_de_reference_aux_erreurs():
    assert mm.ecart_type([10.0, 10.0, 10.0]) == pytest.approx(0.0)
    assert mm.ecart_type([0.0, 10.0]) == pytest.approx(math.sqrt(50.0))
    assert math.isnan(mm.ecart_type([5.0]))


def test_le_terme_en_k_n_apporte_rien_sur_des_points_qui_n_en_dependent_pas():
    """Sur une série engendrée sans k, ajouter un terme en k ne doit rien gagner."""
    points = _serie_exacte() + [dict(p, k=2) for p in _serie_exacte()]
    apport = mm.apport_du_nombre_de_postes(points)
    assert apport["rmse_sans_k"] - apport["rmse_avec_k"] < 0.5
    assert abs(apport["coefficient_k"]) < 1.0


def test_le_balayage_des_exposants_couvre_une_plage_utile():
    assert min(mm.EXPOSANTS) <= 0.1
    assert max(mm.EXPOSANTS) >= 3.0
    assert len(mm.EXPOSANTS) >= 40


def test_random_est_bien_utilise_de_maniere_deterministe():
    """Le script ne doit pas dépendre de l'état global du générateur."""
    random.seed(1)
    a = mm.partage_aleatoire(_serie_exacte(), seed=9)[1]
    random.seed(999)
    b = mm.partage_aleatoire(_serie_exacte(), seed=9)[1]
    assert [p["tau"] for p in a] == [p["tau"] for p in b]


# --- données de la figure (la boucle courte ne rend aucune image) -----------


def test_le_residu_est_bien_predit_moins_observe():
    """Erreur de signe invisible sur une image : un modèle qui surestime doit donner > 0."""
    points = [_point(0.5, 0.95, 10.0)]
    surestime = {"a": 15.0, "b": 0.0, "c": 1.0}
    souple = mm.fit_souple(_serie_exacte())
    series = mm.donnees_figure(points, surestime, souple)
    assert series["structurel"]["residus"][0] == pytest.approx(5.0)


def test_les_series_de_la_figure_ont_la_meme_longueur_que_les_points():
    points = _serie_exacte()
    series = mm.donnees_figure(points, mm.fit_structurel(points), mm.fit_souple(points))
    for serie in series.values():
        for colonne in ("observes", "predits", "residus", "c_a2"):
            assert len(serie[colonne]) == len(points)


def test_les_deux_modeles_apparaissent_dans_les_series():
    points = _serie_exacte()
    series = mm.donnees_figure(points, mm.fit_structurel(points), mm.fit_souple(points))
    assert set(series) == {"structurel", "souple"}


def test_les_observes_sont_identiques_entre_les_deux_modeles():
    """Ils partagent l'axe des abscisses du panneau de gauche : une divergence serait un bug."""
    points = _serie_exacte()
    series = mm.donnees_figure(points, mm.fit_structurel(points), mm.fit_souple(points))
    assert series["structurel"]["observes"] == series["souple"]["observes"]


def test_les_residus_sont_quasi_nuls_sur_la_forme_exacte():
    """Sur des points engendrés par la formule, le modèle structurel ne doit pas dévier."""
    points = _serie_exacte()
    series = mm.donnees_figure(points, mm.fit_structurel(points), mm.fit_souple(points))
    assert max(abs(r) for r in series["structurel"]["residus"]) < 0.01
