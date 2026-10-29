"""Tests de scripts/politique_commande.py : report, point de commande, attentes en rade.

Le script tranche trois questions — l'option de report ajoute-t-elle quelque chose, existe-t-il
un optimum intérieur en s, où se place l'attente observée — et une erreur dans la règle qui
tranche ne se verrait pas dans sa sortie. C'est donc cette règle qui est testée, sur des
mesures fabriquées dont la réponse se calcule à la main, sans simuler.
"""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.stats import estimate

_SPEC = importlib.util.spec_from_file_location(
    "politique_commande", Path(__file__).parent.parent / "scripts" / "politique_commande.py"
)
pc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pc)


def _mesure(**series):
    """Une mesure fabriquée : des séries par réplication, et leurs `Estimate`."""
    n = len(next(iter(series.values())))
    lignes = [{cle: valeurs[i] for cle, valeurs in series.items()} for i in range(n)]
    return {"lignes": lignes, **{cle: estimate(v) for cle, v in series.items()}}


def _mesure_couts(arrets, surestaries, portage, bruit=(0.0, 0.1, -0.1, 0.05, -0.05)):
    """Trois termes et leur total, avec un petit bruit commun à toutes les réplications."""
    a = [arrets + b for b in bruit]
    s = [surestaries + b for b in bruit]
    p = [portage + b for b in bruit]
    return _mesure(arrets_dri=a, surestaries=s, portage=p,
                   total=[x + y + z for x, y, z in zip(a, s, p, strict=True)])


# --- l'option de report -----------------------------------------------------


def test_une_option_sans_effet_n_est_significative_nulle_part():
    base = _mesure(beta=[0.98, 0.99, 0.97, 0.985], attente_h=[10.0, 12.0, 8.0, 11.0],
                   total=[5.0, 5.2, 4.9, 5.1])
    effets = pc.effet_du_report(base, base)
    assert not any(e["significative"] for e in effets.values())


def test_un_effet_franc_et_regulier_est_significatif():
    base = _mesure(beta=[0.98, 0.99, 0.97, 0.985], attente_h=[10.0, 12.0, 8.0, 11.0],
                   total=[5.0, 5.2, 4.9, 5.1])
    option = _mesure(beta=[0.97, 0.98, 0.96, 0.975], attente_h=[5.0, 7.0, 3.0, 6.0],
                     total=[5.0, 5.2, 4.9, 5.1])
    effets = pc.effet_du_report(base, option)
    assert effets["beta"]["significative"] and effets["beta"]["difference"].mean < 0
    assert effets["attente_h"]["significative"]
    assert not effets["total"]["significative"]


def test_l_effet_est_option_moins_base():
    base = _mesure(beta=[0.9] * 3, attente_h=[10.0, 11.0, 12.0], total=[1.0] * 3)
    option = _mesure(beta=[0.9] * 3, attente_h=[4.0, 5.0, 6.0], total=[1.0] * 3)
    assert pc.effet_du_report(base, option)["attente_h"]["difference"].mean == pytest.approx(-6.0)


# --- l'optimum en s -----------------------------------------------------------


def _balayage(totaux):
    """Un balayage dont le coût total vaut `totaux[k]` en s = 15, 20, …"""
    return [{"s": 15 + 5 * k, "mesure": _mesure_couts(t, 0.0, 0.0)}
            for k, t in enumerate(totaux)]


def test_un_creux_net_au_milieu_est_un_optimum_interieur():
    opt = pc.optimum(_balayage([10.0, 6.0, 3.0, 5.0, 9.0]))
    assert opt["s"] == 25
    assert opt["interieur"] and not opt["au_bord"]


def test_un_minimum_au_bord_n_est_pas_interieur():
    opt = pc.optimum(_balayage([10.0, 8.0, 6.0, 4.0, 2.0]))
    assert opt["s"] == 35 and opt["au_bord"] and not opt["interieur"]


def test_un_creux_noye_dans_le_bruit_n_est_pas_un_optimum():
    """Un minimum de moyennes à un cheveu d'un bord n'est pas significatif.

    Le bruit doit **changer d'un s à l'autre** : un bruit identique à tous les s s'annule en
    données appariées — c'est tout l'intérêt des nombres aléatoires communs — et le plus
    petit écart deviendrait significatif.
    """
    motifs = [(0, 2, -2, 1, -1), (2, -2, 1, -1, 0), (-2, 1, -1, 0, 2),
              (1, -1, 0, 2, -2), (-1, 0, 2, -2, 1)]
    balayage = [{"s": 15 + 5 * k, "mesure": _mesure_couts(t, 0, 0, bruit=motifs[k])}
                for k, t in enumerate([3.05, 3.02, 3.0, 4.0, 6.0])]
    opt = pc.optimum(balayage)
    assert opt["s"] == 25
    assert not opt["interieur"]


# --- la position de l'optimum selon les prix ---------------------------------


def _balayage_en_u():
    """Arrêts décroissants, surestaries et portage croissants : un U classique."""
    lignes = []
    for k, s in enumerate((15, 30, 45, 60)):
        lignes.append({"s": s, "mesure": _mesure_couts(arrets=[30.0, 1.0, 0.2, 0.0][k],
                                                       surestaries=[0.5, 2.0, 8.0, 16.0][k],
                                                       portage=[0.5, 2.0, 4.0, 6.0][k])})
    return lignes


def test_un_arret_plus_cher_pousse_vers_plus_de_stock():
    balayage = _balayage_en_u()
    assert pc.s_optimal(balayage, {}, 100.0, 1.0) >= pc.s_optimal(balayage, {}, 1.0, 1.0)


def test_des_surestaries_plus_cheres_poussent_vers_moins_de_stock():
    balayage = _balayage_en_u()
    assert pc.s_optimal(balayage, {}, 1.0, 100.0) <= pc.s_optimal(balayage, {}, 1.0, 1.0)


def test_la_table_des_optima_a_la_forme_des_facteurs():
    table = pc.table_des_optima(_balayage_en_u(), {})
    assert len(table) == len(pc.FACTEURS)
    assert all(len(ligne) == len(pc.FACTEURS) for ligne in table)
    # s* croît, à surestaries fixées, quand l'arrêt renchérit
    for j in range(len(pc.FACTEURS)):
        colonne = [table[i][j] for i in range(len(pc.FACTEURS))]
        assert colonne == sorted(colonne)


def test_les_facteurs_couvrent_deux_ordres_de_grandeur():
    assert max(pc.FACTEURS) / min(pc.FACTEURS) >= 100
    assert 1.0 in pc.FACTEURS


def test_le_domaine_de_s_est_la_fourchette_de_l_hypothese():
    from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

    bas, haut = load_yaml(ASSUMPTIONS_PATH)["approvisionnement"]["point_de_commande_j"]["range"]
    assert min(pc.POINTS_DE_COMMANDE_J) == bas
    assert max(pc.POINTS_DE_COMMANDE_J) == haut


# --- les attentes en rade -----------------------------------------------------


def test_le_resume_place_l_attente_observee_dans_la_distribution():
    attentes = [0.0] * 6 + [50.0, 100.0, 200.0, 300.0]
    resume = pc.resume_attentes(attentes, observee=183.0)
    assert resume["n"] == 10
    assert resume["part_nulle"] == pytest.approx(0.6)
    assert resume["part_au_moins_observee"] == pytest.approx(0.2)
    assert resume["maximum"] == 300.0
    assert resume["moyenne"] == pytest.approx(65.0)


def test_une_observee_au_dela_de_toute_la_distribution_tombe_a_zero():
    """Le signal qu'on cherche : le modèle ne rend pas plausible ce qui a été vu."""
    resume = pc.resume_attentes([0.0, 10.0, 160.0], observee=183.0)
    assert resume["part_au_moins_observee"] == 0.0


def test_une_distribution_vide_ne_divise_pas_par_zero():
    assert pc.resume_attentes([], observee=183.0) == {"n": 0}


def test_les_attentes_observees_sont_lues_dans_les_donnees():
    """Lues dans data/clean, pas recopiées : la série s'allonge et la liste suivra."""
    observees = pc.attentes_observees()
    assert observees, "la série doit contenir au moins BERGE NIMBA"
    assert all(h > 0 for _, h in observees)
    assert "BERGE NIMBA" in [navire for navire, _ in observees]


def test_sans_fichier_d_observations_il_n_y_a_rien_a_comparer(tmp_path):
    assert pc.attentes_observees(tmp_path / "absent.csv") == []


# --- données de la figure -----------------------------------------------------


def test_les_donnees_de_figure_portent_les_quatre_series_et_l_optimum():
    balayages = {("baseline_endogene", True): _balayage_en_u()}
    donnees = pc.donnees_figure(balayages)
    serie = donnees[("baseline_endogene", True)]
    for terme in ("arrets_dri", "surestaries", "portage", "total"):
        s, moy, bas, haut = serie[terme]
        assert s == [15, 30, 45, 60]
        assert all(b <= m <= h for b, m, h in zip(bas, moy, haut, strict=True))
    assert serie["optimum"]["s"] == 30


def test_les_montants_de_la_figure_sont_en_millions():
    balayages = {("x", True): [{"s": 15, "mesure": _mesure_couts(2e6, 0, 0)},
                               {"s": 30, "mesure": _mesure_couts(1e6, 0, 0)}]}
    _, moy, _, _ = pc.donnees_figure(balayages)[("x", True)]["arrets_dri"]
    assert moy == pytest.approx([2.0, 1.0], abs=1e-6)


@pytest.mark.slow
def test_la_figure_est_un_png_non_vide(tmp_path):
    balayages = {(sc, h): _balayage_en_u()
                 for sc in pc.eco.SCENARIOS_STATIONNAIRES
                 for h, _ in pc.eco.HYPOTHESES_DEPASSEMENT}
    chemin = pc.write_figure(balayages, 5, tmp_path / "p.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"



@pytest.mark.slow
def test_mesurer_plusieurs_rend_exactement_des_appels_separes(monkeypatch):
    """Un seul lot de processus, mêmes graines que `replicate` : rien ne doit changer.

    Horizon raccourci (le protocole stationnaire coûterait 20 ans par réplication)."""
    import dataclasses

    from corridor.sim.run import replicate
    monkeypatch.setattr(pc, "ECHAUFFEMENT_STATIONNAIRE_ANS", 0.25)
    monkeypatch.setattr(pc, "FENETRE_STATIONNAIRE_ANS", 0.25)
    raw = pc.load_yaml(pc.ASSUMPTIONS_PATH)
    unitaires = pc.eco.couts_unitaires(raw)
    base = pc.eco.configuration_stationnaire("baseline_endogene", True)
    autre = dataclasses.replace(base, point_de_commande_t=base.point_de_commande_t * 0.5)
    ensemble = pc.mesurer_plusieurs([base, autre], 2, 5, unitaires, processus=1)
    for mesure, config in zip(ensemble, (base, autre), strict=True):
        attendu = pc._mesure(replicate(config, 0.5, 2, 5, "", warmup_years=0.25), config,
                             unitaires)
        assert mesure["lignes"] == attendu["lignes"]
    assert ensemble[0]["lignes"] != ensemble[1]["lignes"]
