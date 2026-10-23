"""Tests de scripts/poste_stockage_factoriel.py : effets, interaction, signes. Hors ligne.

Le plan factoriel ne vaut que par l'arithmétique de ses différences : un signe inversé ou
une interaction mal appariée donnerait une conclusion fausse sans rien casser. C'est donc
elle qui est testée ici, sur des séries fabriquées dont la réponse est connue à la main.
"""

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

from corridor.sim.config import SimConfig, load_scenario

_SPEC = importlib.util.spec_from_file_location(
    "poste_stockage_factoriel",
    Path(__file__).parent.parent / "scripts" / "poste_stockage_factoriel.py",
)
fx = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fx)

BAS_1, BAS_2 = (False, False), (True, False)
HAUT_1, HAUT_2 = (False, True), (True, True)


@dataclass
class _Resultat:
    """Le strict nécessaire pour `series` : un faux résultat de réplication."""

    dri_production_t: float = 3.6e6
    berth_blocked_h: float = 800.0
    vessels_served: int = 40
    wait_mean_h: float = 200.0


def _cases(valeurs, libelle="perte DRI (%)"):
    """Quatre cases du plan, chacune avec la série donnée pour l'indicateur nommé."""
    return {cle: {"series": {libelle: list(serie)}} for cle, serie in valeurs.items()}


# --- arithmétique des effets ------------------------------------------------


def test_le_gain_est_positif_quand_le_levier_fait_baisser_la_grandeur():
    """Convention « gain » : (sans) − (avec). La perte baisse de 10 à 8 → gain +2."""
    cases = _cases({BAS_1: [10.0, 10.0], BAS_2: [8.0, 8.0]})
    assert fx._gain(cases, BAS_1, BAS_2, "perte DRI (%)").mean == pytest.approx(2.0)


def test_la_variation_est_de_signe_oppose_au_gain():
    """Variation brute : (avec) − (sans). La même baisse s'y lit −2."""
    cases = _cases({BAS_1: [10.0, 10.0], BAS_2: [8.0, 8.0]})
    assert fx._variation(cases, BAS_1, BAS_2, "perte DRI (%)").mean == pytest.approx(-2.0)


def test_l_interaction_est_la_difference_des_deux_effets_simples():
    """Gain du poste : 1 point à stockage bas, 3 à stockage haut → interaction +2."""
    cases = _cases({
        BAS_1: [10.0, 10.0], BAS_2: [9.0, 9.0],
        HAUT_1: [6.0, 6.0], HAUT_2: [3.0, 3.0],
    })
    assert fx._interaction(cases, "perte DRI (%)").mean == pytest.approx(2.0)


def test_l_interaction_est_nulle_quand_les_leviers_s_additionnent():
    """Deux leviers parfaitement additifs : aucune interaction, par construction."""
    cases = _cases({
        BAS_1: [10.0, 10.0], BAS_2: [8.0, 8.0],
        HAUT_1: [6.0, 6.0], HAUT_2: [4.0, 4.0],
    })
    assert fx._interaction(cases, "perte DRI (%)").mean == pytest.approx(0.0)


def test_l_interaction_vaut_le_gain_combine_moins_la_somme_des_gains():
    """L'identité qui fonde la lecture « le combiné dépasse-t-il la somme ? »."""
    cases = _cases({
        BAS_1: [12.0, 11.0], BAS_2: [9.0, 9.5],
        HAUT_1: [7.0, 6.0], HAUT_2: [2.0, 1.5],
    })
    libelle = "perte DRI (%)"
    combine = fx._gain(cases, BAS_1, HAUT_2, libelle).mean
    poste = fx._gain(cases, BAS_1, BAS_2, libelle).mean
    stockage = fx._gain(cases, BAS_1, HAUT_1, libelle).mean
    assert fx._interaction(cases, libelle).mean == pytest.approx(combine - poste - stockage)


def test_l_interaction_reste_appariee():
    """Appariée, donc calculée réplication par réplication — pas sur les moyennes.

    Les moyennes des quatre cases sont ici identiques deux à deux, mais les réplications
    sont permutées : une interaction calculée sur les moyennes vaudrait zéro, l'appariée
    non, et c'est l'appariée qui est juste.
    """
    cases = _cases({
        BAS_1: [10.0, 20.0], BAS_2: [10.0, 20.0],
        HAUT_1: [10.0, 20.0], HAUT_2: [20.0, 10.0],
    })
    interaction = fx._interaction(cases, "perte DRI (%)")
    assert interaction.mean == pytest.approx(0.0)
    assert interaction.std > 0.0  # la dispersion, elle, voit la permutation


def test_les_series_doivent_etre_appariables():
    cases = _cases({
        BAS_1: [10.0, 10.0], BAS_2: [8.0],
        HAUT_1: [6.0, 6.0], HAUT_2: [4.0, 4.0],
    })
    with pytest.raises(ValueError):
        fx._interaction(cases, "perte DRI (%)")


# --- indicateurs ------------------------------------------------------------


def test_la_perte_est_nulle_quand_la_production_atteint_la_cible():
    config = SimConfig.from_scenario(load_scenario("phase2"))
    cible_t = fx.cible_mt(config, 1.0) * 1e6
    assert fx.perte_pct(config, _Resultat(dri_production_t=cible_t), 1.0) == pytest.approx(0.0)


def test_la_perte_vaut_dix_pour_cent_a_dix_pour_cent_sous_la_cible():
    config = SimConfig.from_scenario(load_scenario("phase2"))
    cible_t = fx.cible_mt(config, 1.0) * 1e6
    perte = fx.perte_pct(config, _Resultat(dri_production_t=0.9 * cible_t), 1.0)
    assert perte == pytest.approx(10.0)


def test_le_blocage_par_poste_divise_par_le_nombre_de_postes():
    """La seule des deux mesures de blocage qui se compare entre 1 et 2 postes."""
    deux = SimConfig.from_scenario(load_scenario("phase2_plus_poste"))
    assert deux.postes_minerai == 2
    series = fx.series(deux, [_Resultat(berth_blocked_h=1000.0)], 1.0, draws=200)
    assert series["quai bloqué (poste-h)"] == [1000.0]
    assert series["quai bloqué (h/poste)"] == [500.0]


def test_l_occupation_effective_est_plus_basse_avec_deux_postes():
    """À blocage et demande identiques, deux postes se partagent la charge."""
    un = SimConfig.from_scenario(load_scenario("phase2"))
    deux = SimConfig.from_scenario(load_scenario("phase2_plus_poste"))
    resultat = [_Resultat()]
    charge_un = fx.series(un, resultat, 1.0, draws=200)["occupation effective"][0]
    charge_deux = fx.series(deux, resultat, 1.0, draws=200)["occupation effective"][0]
    assert charge_deux == pytest.approx(charge_un / 2)


def test_l_occupation_effective_monte_avec_le_blocage():
    config = SimConfig.from_scenario(load_scenario("phase2"))
    sans = fx.series(config, [_Resultat(berth_blocked_h=0.0)], 1.0, draws=200)
    avec = fx.series(config, [_Resultat(berth_blocked_h=4000.0)], 1.0, draws=200)
    assert avec["occupation effective"][0] > sans["occupation effective"][0]


def test_la_production_est_hors_du_tableau_des_effets():
    """Elle est un bien, pas un mal : la convention de signe des effets s'y inverserait."""
    assert "production DRI (Mt)" in dict(fx.INDICATEURS)
    assert "production DRI (Mt)" not in dict(fx.EFFETS)


# --- le plan et ses scénarios ------------------------------------------------


def test_le_plan_couvre_les_quatre_cases():
    assert set(fx.CELLULES) == {BAS_1, BAS_2, HAUT_1, HAUT_2}


def test_les_quatre_scenarios_du_plan_ont_bien_les_facteurs_annonces():
    """Garde-fou sur les fichiers de scénario : le plan 2 × 2 doit être un vrai 2 × 2."""
    charges = {}
    for cle, (scenario, _) in fx.CELLULES.items():
        config = SimConfig.from_scenario(load_scenario(scenario))
        charges[cle] = (
            config.postes_minerai,
            config.capacite_stockyard_t + config.capacite_stock_usine_t,
        )
    # facteur « poste » : 1 contre 2, à stockage égal
    assert charges[BAS_1][0] == 1 and charges[BAS_2][0] == 2
    assert charges[HAUT_1][0] == 1 and charges[HAUT_2][0] == 2
    assert charges[BAS_1][1] == charges[BAS_2][1]
    assert charges[HAUT_1][1] == charges[HAUT_2][1]
    # facteur « stockage » : bas contre haut, à nombre de postes égal
    assert charges[HAUT_1][1] > charges[BAS_1][1]
    assert charges[HAUT_2][1] > charges[BAS_2][1]


def test_les_quatre_scenarios_visent_la_meme_cible():
    """Sans cible commune, comparer des pertes en pourcentage n'aurait pas de sens."""
    cibles = {
        fx.cible_mt(SimConfig.from_scenario(load_scenario(scenario)), 1.0)
        for scenario, _ in fx.CELLULES.values()
    }
    assert len(cibles) == 1


# --- sorties ----------------------------------------------------------------


def test_les_tableaux_s_affichent_sans_lever(capsys):
    cases = {
        cle: {"series": {libelle: [1.0, 2.0] for libelle, _ in fx.INDICATEURS}}
        for cle in fx.CELLULES
    }
    fx.print_cases(cases)
    fx.print_effets(cases)
    fx.print_verdict(cases)
    sortie = capsys.readouterr().out
    assert "Les quatre cases du plan" in sortie
    assert "Volets de la prédiction" in sortie
    assert "Où passe la file" in sortie
