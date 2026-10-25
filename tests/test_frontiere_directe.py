"""Tests de scripts/frontiere_directe.py : la frontière cherchée par dichotomie.

Ce script décide, et une décision statistique fausse ne se voit pas dans sa sortie : une
dichotomie qui garde le mauvais demi-intervalle rend quand même un nombre crédible. Ce qui est
testé ici est donc la mécanique de décision — de quel côté de la cible on se range, quand on
refuse de trancher, où la dichotomie converge — sur un β analytique dont la réponse se calcule
à la main, sans simuler.

La régression isotone est testée à part : c'est elle qui fabrique la frontière publiée, et une
erreur d'orientation y transformerait le bruit en tendance.
"""

import importlib.util
import math
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "frontiere_directe", Path(__file__).parent.parent / "scripts" / "frontiere_directe.py"
)
fd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fd)


class EvaluateurFactice:
    """β(τ) déterministe et croissant, plus un bruit optionnel reproductible.

    Sans bruit, la dichotomie doit retrouver exactement le τ qui atteint la cible : c'est le
    seul cas où l'on connaît la bonne réponse. Le bruit sert aux tests de refus de trancher.
    """

    def __init__(self, pente=0.01, origine=0.80, bruit=0.0):
        self.pente, self.origine, self.bruit = pente, origine, bruit
        self.appels = 0
        self.annees_simulees = 0.0

    def beta_vrai(self, tau):
        return min(1.0, self.origine + self.pente * tau)

    def betas(self, postes, c_a2, tau, reps):
        self.appels += 1
        self.annees_simulees += reps
        centre = self.beta_vrai(tau)
        if not self.bruit:
            return [centre] * reps
        # bruit déterministe et de moyenne nulle : alterné, donc l'IC se resserre avec reps
        return [centre + self.bruit * (1 if i % 2 else -1) for i in range(reps)]


# --- de quel côté de la cible sommes-nous ? ---------------------------------


def test_un_beta_nettement_au_dessus_est_declare_au_dessus():
    verdict, _ = fd.cote([0.96, 0.96, 0.96, 0.96], 0.95)
    assert verdict == 1


def test_un_beta_nettement_en_dessous_est_declare_en_dessous():
    verdict, _ = fd.cote([0.90, 0.90, 0.90, 0.90], 0.95)
    assert verdict == -1


def test_un_beta_dont_l_intervalle_contient_la_cible_est_indecidable():
    """C'est le cœur de la méthode : ne pas trancher est une réponse, pas un échec."""
    verdict, e = fd.cote([0.90, 1.00, 0.92, 0.98, 0.94, 0.96], 0.95)
    assert verdict == 0
    assert e.low <= 0.95 <= e.high


def test_le_cote_renvoie_l_estimation_qui_l_a_decide():
    _, e = fd.cote([0.90, 0.92, 0.94], 0.95)
    assert e.mean == pytest.approx(0.92)


# --- réplications adaptatives ------------------------------------------------


def test_un_cas_facile_se_tranche_au_minimum_de_replications():
    ev = EvaluateurFactice()
    verdict, _, reps, decide = fd.trancher(ev, 1, 0.5, 30.0, 0.95, 8, 64)
    assert verdict == 1 and decide
    assert reps == 8


def test_un_cas_ambigu_consomme_des_replications_jusqu_au_plafond():
    """β est exactement sur la cible : aucun intervalle ne l'exclura jamais."""
    ev = EvaluateurFactice(bruit=0.05)
    cible = ev.beta_vrai(30.0)
    _, _, reps, decide = fd.trancher(ev, 1, 0.5, 30.0, cible, 8, 32)
    assert not decide
    assert reps == 32


def test_au_plafond_le_cote_est_parie_sur_l_estimation_ponctuelle():
    """Rendre 0 arrêterait la dichotomie sur un encadrement large : on parie, sans biais."""
    ev = EvaluateurFactice(bruit=0.05)
    cible = ev.beta_vrai(30.0)
    verdict_haut, _, _, _ = fd.trancher(ev, 1, 0.5, 30.0, cible - 1e-9, 8, 16)
    verdict_bas, _, _, _ = fd.trancher(ev, 1, 0.5, 30.0, cible + 1e-9, 8, 16)
    assert verdict_haut == 1
    assert verdict_bas == -1


def test_les_replications_doublent_au_lieu_de_monter_une_par_une():
    ev = EvaluateurFactice(bruit=0.05)
    cible = ev.beta_vrai(30.0)
    fd.trancher(ev, 1, 0.5, 30.0, cible, 8, 64)
    # 8 → 16 → 32 → 64 : quatre évaluations, pas cinquante-sept
    assert ev.appels == 4


# --- dichotomie sur τ --------------------------------------------------------


def test_la_dichotomie_encadre_le_tau_qui_atteint_la_cible():
    ev = EvaluateurFactice(pente=0.005, origine=0.80)
    cible = ev.beta_vrai(25.0)  # 0,925, atteint pile à 25 jours
    resultat = fd.tau_etoile(ev, 1, 0.5, cible, 8, 32, tol=0.5)
    assert resultat["censure"] is None
    assert abs(resultat["tau"] - 25.0) <= 0.5
    assert resultat["largeur"] <= 0.5


def test_une_cible_hors_d_atteinte_au_plafond_de_stockage_est_signalee():
    ev = EvaluateurFactice(pente=0.001, origine=0.80)
    resultat = fd.tau_etoile(ev, 1, 0.5, 0.99, 8, 32, tol=1.0)
    assert resultat["tau"] is None
    assert resultat["censure"] == "inatteignable"


def test_une_cible_deja_atteinte_au_plancher_est_marquee_censuree():
    """Rendre τ_min comme s'il était mesuré ferait croire à une exigence qu'on n'a pas mesurée."""
    ev = EvaluateurFactice(pente=0.001, origine=0.99)
    resultat = fd.tau_etoile(ev, 1, 0.0, 0.95, 8, 32, tol=1.0)
    assert resultat["tau"] == fd.TAU_MIN
    assert resultat["censure"] == "plancher"


def test_une_tolerance_plus_serree_coute_plus_de_pas():
    large = fd.tau_etoile(EvaluateurFactice(pente=0.005), 1, 0.5, 0.925, 8, 32, tol=8.0)
    serre = fd.tau_etoile(EvaluateurFactice(pente=0.005), 1, 0.5, 0.925, 8, 32, tol=0.25)
    assert serre["pas"] > large["pas"]


def test_la_dichotomie_ne_boucle_pas_quand_elle_n_arrive_pas_a_trancher():
    """Indécis à chaque pas, elle doit tout de même s'arrêter sur la tolérance."""
    ev = EvaluateurFactice(pente=0.005, bruit=0.5)
    resultat = fd.tau_etoile(ev, 1, 0.5, 0.925, 4, 8, tol=2.0)
    assert resultat["largeur"] <= 2.0
    assert resultat["indecis"] > 0


# --- régression isotone ------------------------------------------------------


def test_une_suite_deja_croissante_est_inchangee():
    assert fd.isotone([1.0, 2.0, 5.0]) == pytest.approx([1.0, 2.0, 5.0])


def test_un_recul_est_remplace_par_la_moyenne_du_bloc():
    """PAVA fusionne les violations : [3, 1] devient [2, 2], pas [1, 1] ni [3, 3]."""
    assert fd.isotone([3.0, 1.0]) == pytest.approx([2.0, 2.0])


def test_la_moyenne_est_conservee_par_la_projection():
    valeurs = [5.0, 1.0, 4.0, 2.0, 9.0]
    corrige = fd.isotone(valeurs)
    assert sum(corrige) == pytest.approx(sum(valeurs))
    assert corrige == sorted(corrige)


def test_la_sortie_est_toujours_croissante_meme_sur_une_suite_decroissante():
    corrige = fd.isotone([9.0, 7.0, 5.0, 3.0])
    assert corrige == pytest.approx([6.0] * 4)


def test_une_suite_vide_ou_unique_ne_pose_pas_de_probleme():
    assert fd.isotone([]) == []
    assert fd.isotone([4.0]) == pytest.approx([4.0])


# --- monotonie imposée sur la frontière -------------------------------------


def _points(taus, k=1, beta=0.95):
    return [{"k": k, "beta": beta, "c_a2": 0.25 * i, "tau": t} for i, t in enumerate(taus)]


def test_une_frontiere_coherente_ne_demande_aucune_correction():
    resultat = fd.corriger_monotonie(_points([10.0, 20.0, 30.0]))
    assert resultat["corrections"] == 0
    assert resultat["violations"] == 0
    assert resultat["recul_moyen"] == 0.0


def test_les_reculs_sont_comptes_avant_correction():
    """Après correction il n'y en a plus aucun : compter là donnerait toujours zéro."""
    resultat = fd.corriger_monotonie(_points([10.0, 30.0, 20.0, 40.0]))
    assert resultat["paires"] == 3
    assert resultat["violations"] == 1
    assert resultat["recul_moyen"] == pytest.approx(10.0)
    assert [p["tau"] for p in resultat["points"]] == sorted(p["tau"] for p in resultat["points"])


def test_la_valeur_d_avant_correction_est_conservee():
    resultat = fd.corriger_monotonie(_points([30.0, 10.0]))
    assert [p["tau_brut"] for p in resultat["points"]] == [30.0, 10.0]
    assert [p["tau"] for p in resultat["points"]] == pytest.approx([20.0, 20.0])


def test_la_monotonie_est_imposee_par_niveau_de_service_et_par_nombre_de_postes():
    """Mélanger deux niveaux de β fabriquerait des reculs qui n'en sont pas."""
    points = _points([10.0, 20.0], beta=0.95) + _points([40.0, 50.0], beta=0.90)
    resultat = fd.corriger_monotonie(points)
    assert resultat["corrections"] == 0
    assert resultat["paires"] == 2


def test_la_correction_maximale_est_le_plus_grand_deplacement():
    resultat = fd.corriger_monotonie(_points([30.0, 10.0]))
    assert resultat["ecart_max"] == pytest.approx(10.0)


def test_une_frontiere_vide_ne_divise_pas_par_zero():
    resultat = fd.corriger_monotonie([])
    assert resultat["points"] == []
    assert math.isnan(resultat["part_corrigee"])
    assert math.isnan(resultat["part_violee"])


# --- diagnostic : pourquoi la frontière est difficile à situer ---------------


def test_la_pente_mesuree_est_celle_du_beta_simule():
    ev = EvaluateurFactice(pente=0.004)
    diag = fd.diagnostic_pente(ev, 1, 0.5, 20.0, 40.0, reps=8)
    assert diag["pente"] == pytest.approx(0.004, abs=1e-9)


def test_une_pente_plus_faible_exige_davantage_de_replications():
    """C'est l'argument central : la platitude de β(τ), pas la méthode, fixe le coût."""
    raide = fd.diagnostic_pente(EvaluateurFactice(pente=0.02, origine=0.1, bruit=0.02),
                                1, 0.5, 20.0, 40.0, reps=16)
    plate = fd.diagnostic_pente(EvaluateurFactice(pente=0.002, origine=0.1, bruit=0.02),
                                1, 0.5, 20.0, 40.0, reps=16)
    assert plate["reps_pour_un_jour"] > raide["reps_pour_un_jour"]


def test_le_gain_des_nombres_communs_est_mesure_et_non_suppose():
    """Un β sans bruit est parfaitement corrélé : le gain doit alors être très grand."""
    diag = fd.diagnostic_pente(EvaluateurFactice(pente=0.004), 1, 0.5, 20.0, 40.0, reps=8)
    assert diag["demi_largeur_appariee"] == pytest.approx(0.0, abs=1e-12)


# --- coût des deux méthodes --------------------------------------------------


def test_le_cout_de_la_grille_est_le_produit_de_sa_forme_par_ses_replications():
    cout = fd.cout_de_la_grille((0.0, 1.0), (5.0, 10.0, 15.0), (1, 2), reps=10)
    assert cout["cases"] == 12
    assert cout["annees"] == 120


def test_la_grille_paie_tout_son_domaine_meme_loin_de_la_frontiere():
    """C'est le seul reproche chiffrable fait à la grille : il doit apparaître dans le coût."""
    etroite = fd.cout_de_la_grille((0.0, 1.0), (5.0, 60.0), (1,), reps=10)
    large = fd.cout_de_la_grille((0.0, 1.0), (5.0, 20.0, 40.0, 60.0), (1,), reps=10)
    assert large["annees"] == 2 * etroite["annees"]


def test_la_comparaison_rapporte_les_deux_couts_dans_la_meme_unite():
    mono = fd.corriger_monotonie(_points([10.0, 30.0, 20.0]))
    comparaison = fd.comparaison_methodes(3000.0, mono["points"], mono,
                                          grille=fd.cout_de_la_grille(reps=60))
    assert comparaison["annees_grille"] == 7560
    assert comparaison["rapport_cout"] == pytest.approx(3000.0 / 7560)


def test_la_comparaison_garde_les_chiffres_de_la_grille_pour_memoire():
    """Sans eux, la sortie dirait « 25 % » sans dire à quoi on le compare."""
    mono = fd.corriger_monotonie(_points([10.0, 20.0]))
    comparaison = fd.comparaison_methodes(100.0, mono["points"], mono)
    assert comparaison["part_violee_grille"] == fd.GRILLE_PART_VIOLEE
    assert comparaison["recul_moyen_grille"] == fd.GRILLE_RECUL_MOYEN


# --- aller-retour Parquet ----------------------------------------------------


def test_la_frontiere_survit_a_l_aller_retour_parquet(tmp_path):
    import pyarrow.parquet as pq

    mono = fd.corriger_monotonie(_points([10.0, 30.0, 20.0]))
    chemin = tmp_path / "frontiere.parquet"
    pq.write_table(fd.to_table(mono["points"]), chemin)
    relus = fd.from_table(pq.read_table(chemin))
    assert len(relus) == 3
    for avant, apres in zip(mono["points"], relus, strict=True):
        assert apres["tau"] == pytest.approx(avant["tau"])
        assert apres["tau_brut"] == pytest.approx(avant["tau_brut"])
        assert apres["k"] == avant["k"]


def test_la_table_porte_la_colonne_d_avant_correction():
    """Sans `tau_brut`, le bruit de la frontière serait nul par construction à la relecture."""
    mono = fd.corriger_monotonie(_points([30.0, 10.0]))
    assert "tau_brut" in fd.to_table(mono["points"]).column_names


# --- sorties -----------------------------------------------------------------


def test_le_tableau_de_comparaison_s_affiche_sans_lever(capsys):
    mono = fd.corriger_monotonie(_points([10.0, 30.0, 20.0]))
    fd.print_comparaison(fd.comparaison_methodes(3226.0, mono["points"], mono), mono)
    sortie = capsys.readouterr().out
    assert "dichotomie" in sortie and "grille" in sortie
    assert "43%" in sortie


def test_le_module_s_annonce_comme_plus_efficace_et_non_plus_precis():
    """Le docstring porte le résultat : s'il promettait mieux, il mentirait sur la mesure."""
    assert "plus efficace, pas plus\nprécise" in fd.__doc__
