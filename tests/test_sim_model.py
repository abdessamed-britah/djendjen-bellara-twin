"""Validations du modèle SimPy du corridor.

Les cinq validations obligatoires du cahier des charges (reproductibilité, conservation
de la masse, production DRI de référence, attente nulle à arrivées régulières, une année
en moins de dix secondes) sont ici, plus les comportements dont elles dépendent :
arrêts planifiés comptés à part, contre-pression quand le stockyard est plein, et effet
de l'irrégularité des arrivées.

Les valeurs attendues sont recalculées depuis la configuration, jamais recopiées.
"""

import dataclasses
import math
import statistics
import time
from itertools import pairwise
from statistics import fmean

import pytest

from corridor.sim import model as md
from corridor.sim import stats as md_stats
from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.samplers import Samplers

GRAINE = 4242


@pytest.fixture(scope="module")
def baseline() -> SimConfig:
    return SimConfig.from_scenario(load_scenario("baseline"))


@pytest.fixture(scope="module")
def run_baseline(baseline) -> md.SimResult:
    return md.simulate(baseline, seed=GRAINE, years=1.0, scenario="baseline")


# --- helper pur : le percentile ---------------------------------------------


@pytest.mark.parametrize(
    ("valeurs", "q", "attendu"),
    [
        ([], 0.9, 0.0),
        ([5.0], 0.9, 5.0),
        ([0.0, 10.0], 0.5, 5.0),
        ([0.0, 1.0, 2.0, 3.0, 4.0], 0.0, 0.0),
        ([0.0, 1.0, 2.0, 3.0, 4.0], 1.0, 4.0),
        ([0.0, 1.0, 2.0, 3.0, 4.0], 0.5, 2.0),
    ],
)
def test_percentile(valeurs, q, attendu):
    assert md.percentile(valeurs, q) == pytest.approx(attendu)


def test_percentile_p90_encadre_la_moyenne(run_baseline, baseline):
    resultat = md.simulate(
        dataclasses.replace(baseline, dispersion_arrivees=1.0), seed=1, years=1.0
    )
    assert resultat.wait_p90_h >= resultat.wait_mean_h


# --- helper pur : l'intégrale de temps -------------------------------------


def test_time_integral_compte_un_etat_termine():
    integrale = md.TimeIntegral()
    integrale.change(10.0, +1)
    integrale.change(25.0, -1)
    assert integrale.read(100.0) == pytest.approx(15.0)


def test_time_integral_compte_la_queue_encore_ouverte():
    """Le cas qui compte : un navire encore à quai à la fin de la simulation."""
    integrale = md.TimeIntegral()
    integrale.change(10.0, +1)
    assert integrale.read(50.0) == pytest.approx(40.0)


def test_time_integral_additionne_les_occupants_simultanes():
    integrale = md.TimeIntegral()
    integrale.change(0.0, +1)
    integrale.change(10.0, +1)  # deux postes occupés de 10 à 20
    integrale.change(20.0, -1)
    assert integrale.read(30.0) == pytest.approx(10.0 + 2 * 10.0 + 10.0)


# --- validation 1 : reproductibilité ---------------------------------------


def test_meme_graine_memes_resultats(baseline):
    premier = md.simulate(baseline, seed=GRAINE, years=1.0)
    second = md.simulate(baseline, seed=GRAINE, years=1.0)
    assert dataclasses.asdict(premier) == dataclasses.asdict(second)


def test_graines_differentes_resultats_differents(baseline):
    premier = md.simulate(baseline, seed=1, years=1.0)
    second = md.simulate(baseline, seed=2, years=1.0)
    assert premier.dri_production_t != second.dri_production_t or (
        premier.pellets_delivered_t != second.pellets_delivered_t
    )


# --- validation 2 : conservation de la masse -------------------------------


def _verifie_la_masse(resultat: md.SimResult) -> None:
    """Rien ne se crée, rien ne disparaît : les pellets sont quelque part.

    Le terme en transit est indispensable : une rame en route détient jusqu'à
    charge_par_train tonnes qui ne sont ni au port ni à l'usine.
    """
    entre = resultat.initial_stock_t + resultat.pellets_delivered_t
    sorti = (
        resultat.stockyard_final_t
        + resultat.plant_stock_final_t
        + resultat.pellets_in_transit_t
        + resultat.pellets_consumed_t
    )
    assert entre == pytest.approx(sorti, rel=1e-9)


def test_conservation_de_la_masse_baseline(run_baseline):
    _verifie_la_masse(run_baseline)


@pytest.mark.parametrize("dispersion", [0.0, 1.0, 1.5])
def test_conservation_de_la_masse_quelle_que_soit_la_dispersion(baseline, dispersion):
    config = dataclasses.replace(baseline, dispersion_arrivees=dispersion)
    _verifie_la_masse(md.simulate(config, seed=GRAINE, years=1.0))


def test_conservation_de_la_masse_sous_contre_pression(baseline):
    # stockyard minuscule et aucune rame : tout sature, la masse doit tenir quand même
    config = dataclasses.replace(
        baseline,
        capacite_stockyard_t=10_000.0,
        stock_initial_stockyard_t=0.0,
        nombre_de_rames=0,
    )
    _verifie_la_masse(md.simulate(config, seed=GRAINE, years=0.1))


def test_pellets_achemines_ne_depassent_pas_les_pellets_dechargés(run_baseline):
    disponible = run_baseline.initial_stock_t + run_baseline.pellets_delivered_t
    assert run_baseline.pellets_railed_t <= disponible


# --- validation 3 : production DRI de référence ----------------------------


def test_production_dri_baseline_a_dix_pourcent_de_la_cible(run_baseline, baseline):
    cible = baseline.debit_dri_t_par_h * baseline.heures_ouvrees_par_an
    assert run_baseline.dri_production_t == pytest.approx(cible, rel=0.10)


def test_consommation_de_pellets_suit_la_production(run_baseline, baseline):
    attendu = run_baseline.dri_production_t * baseline.pellets_par_t_dri
    assert run_baseline.pellets_consumed_t == pytest.approx(attendu, rel=1e-6)


def test_arrets_planifies_comptes_a_part(run_baseline, baseline):
    attendu = baseline.arrets_planifies_j * 24
    assert run_baseline.dri_planned_stop_hours == pytest.approx(attendu, abs=baseline.pas_h)
    # en baseline, l'approvisionnement suffit : aucun arrêt faute de pellets
    assert run_baseline.dri_stop_hours == 0.0


# --- validation 4 : attente nulle à arrivées régulières --------------------


def test_arrivees_regulieres_attente_quasi_nulle(run_baseline, baseline):
    assert baseline.dispersion_arrivees == 0.0  # le baseline est bien le cas régulier
    assert run_baseline.wait_mean_h == pytest.approx(0.0, abs=1e-6)
    assert run_baseline.wait_p90_h == pytest.approx(0.0, abs=1e-6)


def test_arrivees_irregulieres_creent_de_l_attente(baseline):
    irregulier = md.simulate(
        dataclasses.replace(baseline, dispersion_arrivees=1.0), seed=GRAINE, years=1.0
    )
    regulier = md.simulate(baseline, seed=GRAINE, years=1.0)
    assert irregulier.wait_mean_h > regulier.wait_mean_h


def test_nombre_de_navires_conforme_au_besoin(run_baseline, baseline):
    attendu = math.floor(baseline.heures_par_an / baseline.intervalle_moyen_arrivees_h)
    assert run_baseline.vessels_arrived == attendu
    assert run_baseline.vessels_served == attendu  # tous déchargés avant la fin de l'année


# --- validation 5 : une année en moins de dix secondes ---------------------


def test_une_annee_simulee_en_moins_de_dix_secondes(baseline):
    debut = time.perf_counter()
    md.simulate(baseline, seed=GRAINE, years=1.0)
    assert time.perf_counter() - debut < 10.0


# --- comportements du modèle ----------------------------------------------


def test_stockyard_plein_met_le_dechargement_en_pause(baseline):
    # cadence déterministe pour que la tranche déchargée soit calculable
    config = dataclasses.replace(
        baseline,
        capacite_stockyard_t=10_000.0,
        stock_initial_stockyard_t=0.0,
        nombre_de_rames=0,
        cv_cadence=0.0,
    )
    resultat = md.simulate(config, seed=GRAINE, years=0.1)
    tranche = config.cadence_dechargement_t_par_h * config.pas_h
    tranches = math.floor(config.capacite_stockyard_t / tranche)
    # le navire remplit le stockyard par tranches entières puis reste bloqué à quai
    assert resultat.pellets_delivered_t == pytest.approx(tranches * tranche)
    assert resultat.stockyard_final_t == pytest.approx(resultat.pellets_delivered_t)
    assert resultat.vessels_arrived >= 1
    assert resultat.vessels_served == 0  # jamais reparti : le poste reste occupé
    assert resultat.berth_occupancy > 0.0


def test_stock_usine_vide_arrete_le_dri_et_compte_les_heures(baseline):
    # aucune rame : l'usine n'est jamais réapprovisionnée, le DRI s'arrête
    config = dataclasses.replace(baseline, nombre_de_rames=0, stock_initial_usine_t=0.0)
    resultat = md.simulate(config, seed=GRAINE, years=0.1)
    assert resultat.dri_production_t == 0.0
    assert resultat.dri_stop_hours > 0.0
    heures = 0.1 * config.heures_par_an
    assert resultat.dri_stop_hours + resultat.dri_planned_stop_hours == pytest.approx(
        heures, abs=config.pas_h
    )


# --- politique d'expédition des rames --------------------------------------


def test_baseline_est_en_pull(baseline):
    assert baseline.politique_expedition == "pull"


def test_en_pull_aucune_rame_n_est_jamais_bloquee(run_baseline):
    """La promesse de la politique pull : une rame ne part que si l'usine peut la vider."""
    assert run_baseline.rame_blocked_h == 0.0


@pytest.mark.parametrize("dispersion", [0.0, 1.0])
def test_en_pull_aucun_blocage_meme_sous_arret_prolonge(baseline, dispersion):
    # DRI arrêté toute l'année et usine pleine : en pull, les rames restent au port
    config = dataclasses.replace(
        baseline,
        dispersion_arrivees=dispersion,
        stock_initial_usine_t=baseline.capacite_stock_usine_t,
        arrets_planifies_j=baseline.jours_par_an,
        arrets_planifies_nombre=1,
    )
    resultat = md.simulate(config, seed=GRAINE, years=0.2)
    assert resultat.rame_blocked_h == 0.0
    assert resultat.pellets_railed_t == 0.0  # rien n'a pu partir, et rien n'est immobilisé
    assert resultat.pellets_in_transit_t == 0.0


def test_en_push_les_rames_se_bloquent_devant_une_usine_pleine(baseline):
    config = dataclasses.replace(
        baseline,
        politique_expedition="push",
        stock_initial_usine_t=baseline.capacite_stock_usine_t,
        arrets_planifies_j=baseline.jours_par_an,
        arrets_planifies_nombre=1,
    )
    resultat = md.simulate(config, seed=GRAINE, years=0.2)
    assert resultat.rame_blocked_h > 0.0
    assert resultat.pellets_in_transit_t > 0.0  # charges immobilisées sur les rames


def test_pull_garde_le_tampon_au_port_push_le_pousse_a_l_usine(baseline):
    pull = md.simulate(baseline, seed=GRAINE, years=1.0)
    push = md.simulate(
        dataclasses.replace(baseline, politique_expedition="push"), seed=GRAINE, years=1.0
    )
    assert pull.stockyard_mean_t > push.stockyard_mean_t
    assert pull.plant_stock_mean_t < push.plant_stock_mean_t


@pytest.mark.parametrize("politique", ["pull", "push"])
def test_conservation_de_la_masse_selon_la_politique(baseline, politique):
    config = dataclasses.replace(baseline, politique_expedition=politique)
    _verifie_la_masse(md.simulate(config, seed=GRAINE, years=1.0))


def test_pull_impossible_si_une_charge_ne_tient_pas_a_l_usine():
    """Refus explicite plutôt qu'une rame en attente éternelle d'une place inexistante."""
    from corridor.sim.config import Scenario, assumption_values, load_yaml

    values = assumption_values(load_yaml("config/assumptions.yaml"))
    values["usine.capacite_stock_pellets_usine"] = 1000.0  # < charge_par_train
    with pytest.raises(ValueError, match="pull impossible"):
        SimConfig.from_scenario(Scenario("test", "", values))


def test_taux_d_occupation_du_poste_coherent(run_baseline, baseline):
    attendu = (
        run_baseline.vessels_served
        * baseline.taille_navire_moyenne_t
        / baseline.cadence_dechargement_t_par_h
        / baseline.heures_par_an
        / baseline.postes_minerai
    )
    assert 0.0 < run_baseline.berth_occupancy < 1.0
    assert run_baseline.berth_occupancy == pytest.approx(attendu, rel=0.15)


def test_utilisation_des_rames_coherente(run_baseline, baseline):
    # l'utilisation ne compte que le temps roulant : elle se compare donc directement
    # au rapport entre le besoin du DRI et la capacité ferroviaire théorique
    attendu = baseline.debit_pellets_t_par_h / baseline.capacite_rail_t_par_h
    assert 0.0 < run_baseline.rame_utilisation < 1.0
    assert run_baseline.rame_utilisation == pytest.approx(attendu, rel=0.30)


def test_niveaux_de_stock_dans_les_capacites(run_baseline, baseline):
    assert 0.0 <= run_baseline.stockyard_min_t <= run_baseline.stockyard_mean_t
    assert run_baseline.stockyard_mean_t <= baseline.capacite_stockyard_t
    assert 0.0 <= run_baseline.plant_stock_min_t <= run_baseline.plant_stock_mean_t
    assert run_baseline.plant_stock_mean_t <= baseline.capacite_stock_usine_t


def test_les_samplers_sont_substituables_sans_toucher_au_modele(baseline):
    """Le modèle accepte un jeu de tirages quelconque : condition de la version empirique."""
    jeu = Samplers.from_config(baseline)
    taille_fixe = 100_000.0

    class TailleFixe:
        def sample(self, rng) -> float:
            return taille_fixe

    resultat = md.simulate(
        baseline,
        seed=GRAINE,
        years=1.0,
        samplers=dataclasses.replace(jeu, vessel_size_t=TailleFixe()),
    )
    assert resultat.pellets_delivered_t == pytest.approx(
        resultat.vessels_served * taille_fixe, rel=0.02
    )


def test_la_provenance_est_dans_le_resultat(run_baseline, baseline):
    assert run_baseline.scenario == "baseline"
    assert run_baseline.seed == GRAINE
    assert run_baseline.years == 1.0
    assert run_baseline.dispersion_arrivees == baseline.dispersion_arrivees
    assert run_baseline.horizon_h == pytest.approx(baseline.heures_par_an)


def test_resultat_immuable(run_baseline):
    with pytest.raises((AttributeError, TypeError)):
        run_baseline.dri_production_t = 0.0


# --- stock flottant en rade -------------------------------------------------


def test_time_integral_integre_une_masse_et_sa_moyenne():
    integrale = md.TimeIntegral()
    integrale.change(0.0, +140_000.0)   # un navire en rade
    integrale.change(10.0, -140_000.0)  # accosté au bout de 10 h
    assert integrale.read(100.0) == pytest.approx(1_400_000.0)  # t × h
    assert integrale.mean(100.0, 100.0) == pytest.approx(14_000.0)


def test_sans_attente_le_stock_flottant_est_nul(run_baseline):
    assert run_baseline.wait_mean_h == pytest.approx(0.0, abs=1e-6)
    assert run_baseline.waiting_tonnage_mean_t == pytest.approx(0.0, abs=1.0)


def test_le_stock_flottant_suit_l_attente(baseline):
    """Le stock flottant vaut la formule de Little, mais **au-dessus** et pas à ±35 %.

    `waiting_tonnage_mean_t` intègre le tonnage en rade sur tout l'horizon, alors que
    `wait_mean_h` ne moyenne que les navires qui ont fini par accoster (`waits_h` n'est
    alimenté qu'après l'obtention du poste). Un navire encore en rade à la fin pèse donc
    dans l'intégrale sans peser dans l'attente moyenne, et l'écart est d'autant plus grand
    que la file est longue à l'horizon. Mesuré sur 60 graines et sur toute la fourchette de
    `cadence_dechargement` : ratio médian 1,00, minimum 0,92, maximum 1,96. L'encadrement
    ci-dessous est volontairement asymétrique pour cette raison ; une tolérance symétrique
    serrée ne tenait que par chance sur une graine.
    """
    irregulier = md.simulate(
        dataclasses.replace(baseline, dispersion_arrivees=1.0), seed=GRAINE, years=1.0
    )
    assert irregulier.waiting_tonnage_mean_t > 0.0
    # ordre de grandeur : tonnage moyen ≈ navires/an × taille × attente moyenne / heures
    attendu = (
        irregulier.vessels_arrived
        * baseline.taille_navire_moyenne_t
        * irregulier.wait_mean_h
        / baseline.heures_par_an
    )
    assert 0.8 * attendu <= irregulier.waiting_tonnage_mean_t <= 2.5 * attendu


def test_un_navire_encore_en_rade_a_la_fin_compte(baseline):
    """Comme pour les autres intégrales : ce qui n'a pas fini d'attendre compte quand même."""
    engorge = dataclasses.replace(
        baseline,
        capacite_stockyard_t=10_000.0,
        stock_initial_stockyard_t=0.0,
        nombre_de_rames=0,
        dispersion_arrivees=0.0,
    )
    resultat = md.simulate(engorge, seed=GRAINE, years=0.5)
    # le premier navire bloque le poste, les suivants s'accumulent en rade sans jamais accoster
    assert resultat.vessels_served == 0
    assert resultat.waiting_tonnage_mean_t > 0.0


# --- blocage du poste de déchargement (hypothèse H2) ------------------------


def test_sans_contre_pression_le_poste_n_est_jamais_bloque(baseline):
    """Stockage immense : le déchargement n'est jamais mis en pause, donc B = 0 exactement.

    C'est la condition d'application de Pollaczek-Khinchine (`corridor.sim.analytic`) :
    un service dont la durée ne dépend pas de l'état du système.
    """
    large = dataclasses.replace(
        baseline,
        capacite_stockyard_t=1e9,
        capacite_stock_usine_t=1e9,
        stock_initial_stockyard_t=5e8,
        stock_initial_usine_t=5e8,
        dispersion_arrivees=1.0,
    )
    resultat = md.simulate(large, seed=GRAINE, years=1.0)
    assert resultat.berth_blocked_h == 0.0
    assert resultat.vessels_served > 0


def test_un_stockyard_etroit_bloque_le_poste(baseline):
    config = dataclasses.replace(
        baseline, capacite_stockyard_t=50_000.0, stock_initial_stockyard_t=25_000.0,
        dispersion_arrivees=1.0,
    )
    resultat = md.simulate(config, seed=GRAINE, years=1.0)
    assert resultat.berth_blocked_h > 0.0


def test_le_blocage_est_compris_dans_l_occupation_du_poste(baseline):
    """B ne s'ajoute pas à l'occupation : il en fait partie. Le quai est pris, sans servir."""
    config = dataclasses.replace(
        baseline, capacite_stockyard_t=50_000.0, stock_initial_stockyard_t=25_000.0,
        dispersion_arrivees=1.0,
    )
    resultat = md.simulate(config, seed=GRAINE, years=1.0)
    heures_poste = resultat.horizon_h * config.postes_minerai
    heures_occupees = resultat.berth_occupancy * heures_poste
    assert resultat.berth_blocked_h <= heures_occupees + config.pas_h


def test_un_navire_encore_bloque_a_la_fin_compte(baseline):
    """Même convention que les autres intégrales : l'immobilisation en cours est comptée.

    Le premier navire remplit le stockyard puis reste bloqué jusqu'à la fin de l'horizon,
    sans jamais repartir. Accumuler à la sortie de l'état ne compterait rien du tout.
    """
    engorge = dataclasses.replace(
        baseline,
        capacite_stockyard_t=10_000.0,
        stock_initial_stockyard_t=0.0,
        nombre_de_rames=0,
        dispersion_arrivees=0.0,
    )
    resultat = md.simulate(engorge, seed=GRAINE, years=0.5)
    assert resultat.vessels_served == 0
    assert resultat.berth_blocked_h > 0.0


def test_le_blocage_croit_quand_le_stockage_se_resserre(baseline):
    """Monotonie attendue : moins de tampon au port, plus d'heures de quai perdues."""
    blocages = []
    for capacite in (100_000.0, 300_000.0, 900_000.0):
        config = dataclasses.replace(
            baseline, capacite_stockyard_t=capacite,
            stock_initial_stockyard_t=capacite / 2, dispersion_arrivees=1.0,
        )
        blocages.append(md.simulate(config, seed=GRAINE, years=1.0).berth_blocked_h)
    assert blocages == sorted(blocages, reverse=True)


# --- flux aléatoires synchronisés -------------------------------------------
#
# Chaque source d'aléa a son générateur, dérivé de la graine de réplication. La
# conséquence observable, et c'est elle qui est testée ici : deux configurations qui ne
# diffèrent que par une capacité voient **exactement la même séquence d'arrivées**. Avant
# cette séparation, un unique générateur partagé faisait glisser toute la suite dès qu'une
# configuration tirait une valeur de plus que l'autre — le navire n n'était plus le même
# navire d'un scénario à l'autre, et apparier les réplications ne servait presque à rien.


def _par_stockage(base: SimConfig, capacite_t: float, seed: int) -> md.SimResult:
    return md.simulate(dataclasses.replace(base, capacite_stockyard_t=capacite_t),
                       seed=seed, years=1.0)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_les_arrivees_ne_dependent_pas_du_stockage(baseline, seed):
    """Le nombre de navires n'est produit que par le flux des intervalles."""
    petit = _par_stockage(baseline, 200_000.0, seed)
    grand = _par_stockage(baseline, 900_000.0, seed)
    assert petit.vessels_arrived == grand.vessels_arrived


def test_le_stockage_change_bien_autre_chose_que_les_arrivees(baseline):
    """Garde-fou du test précédent : s'il passait parce que rien ne change, il ne dirait rien.

    On vérifie donc que les deux simulations ont bel et bien divergé — le quai ne bloque pas
    autant et la rade ne porte pas le même tonnage — alors que la séquence d'arrivées, elle,
    est restée identique. C'est exactement la propriété recherchée : même aléa, trajectoires
    différentes.
    """
    erratique = dataclasses.replace(baseline, dispersion_arrivees=1.0)
    petit = _par_stockage(erratique, 200_000.0, seed=GRAINE)
    grand = _par_stockage(erratique, 900_000.0, seed=GRAINE)
    assert petit.vessels_arrived == grand.vessels_arrived
    assert petit.berth_blocked_h != grand.berth_blocked_h
    assert petit.waiting_tonnage_mean_t != grand.waiting_tonnage_mean_t


def test_le_nombre_de_postes_ne_change_pas_non_plus_les_arrivees(baseline):
    """Même exigence pour l'autre levier comparé en données appariées."""
    erratique = dataclasses.replace(baseline, dispersion_arrivees=1.0)
    un = md.simulate(erratique, seed=GRAINE, years=1.0)
    deux = md.simulate(dataclasses.replace(erratique, postes_minerai=2),
                       seed=GRAINE, years=1.0)
    assert un.vessels_arrived == deux.vessels_arrived


def test_la_cadence_de_dechargement_est_tiree_a_l_arrivee_et_non_a_l_accostage(baseline):
    """Tirée à l'accostage, elle dépendrait de l'ordre d'accostage, donc de la congestion.

    Le signe observable : à arrivées erratiques, le tonnage débarqué du cas très stocké est
    celui d'un corridor qui n'a jamais bloqué, donc égal au tonnage arrivé — ce qui n'est
    vrai que si chaque navire garde sa cadence quelle que soit la file devant lui.
    """
    erratique = dataclasses.replace(baseline, dispersion_arrivees=1.0,
                                    capacite_stockyard_t=1e9, capacite_stock_usine_t=1e9)
    a = md.simulate(erratique, seed=GRAINE, years=1.0)
    b = md.simulate(dataclasses.replace(erratique, postes_minerai=2),
                    seed=GRAINE, years=1.0)
    assert a.vessels_arrived == b.vessels_arrived
    assert a.berth_blocked_h == pytest.approx(0.0)
    assert b.berth_blocked_h == pytest.approx(0.0)


@pytest.mark.slow
def test_les_flux_separes_rendent_les_differences_appariees_bien_moins_bruitees(baseline):
    """La mesure qui justifie toute la séparation des flux : le gain réel de l'appariement.

    On compare deux stockages sur les mêmes graines et on regarde la demi-largeur de l'IC de
    la différence appariée, face à celle qu'on aurait avec des tirages indépendants. Avec un
    générateur unique partagé, le gain mesuré valait × 1,1 : changer une capacité décalait
    toute la suite des tirages et les deux exécutions se décorrélaient. Avec un générateur
    par source, il dépasse × 3 ici.

    Marqué `slow` : il simule 2 × 24 années.
    """
    def _erratique(scenario: str) -> SimConfig:
        return dataclasses.replace(SimConfig.from_scenario(load_scenario(scenario)),
                                   dispersion_arrivees=1.0)

    # les deux scénarios du plan factoriel, et non une seule capacité retouchée : agrandir le
    # seul stockyard du port ne change rien à la production, c'est le stock de l'usine qui
    # tamponne le four
    bas, haut = _erratique("phase2"), _erratique("phase2_plus_stockage")
    graines = range(1000, 1024)
    petit = [md.simulate(bas, seed=g, years=1.0).dri_production_t for g in graines]
    grand = [md.simulate(haut, seed=g, years=1.0).dri_production_t for g in graines]
    appariee = md_stats.estimate([b - a for a, b in zip(petit, grand, strict=True)])
    independante = math.sqrt(
        md_stats.estimate(petit).half_width ** 2 + md_stats.estimate(grand).half_width ** 2
    )
    assert appariee.half_width > 0.0
    assert independante / appariee.half_width > 3.0


@pytest.mark.slow
def test_la_capacite_de_stockage_ne_change_pas_la_production():
    """Le résultat le plus inconfortable du modèle, verrouillé pour qu'il ne se reperde pas.

    À stock initial **figé**, faire varier les deux capacités de stockage d'un facteur 12 ne
    déplace pas la production d'un cent-millième. Ce n'est pas un bug : quand le stockyard est
    plein, le navire garde sa cargaison à quai ou en rade, et cette flotte-tampon n'a pas de
    plafond dans le modèle (ni surestaries, ni déroutement — limite 9). Les silos déplacent le
    minerai, ils ne nourrissent pas le four.

    Le gain que le projet attribuait au stockage venait du **stock initial**, que les scénarios
    dérivent de la capacité : c'est ce que le second assert isole.

    Si ce test tombe un jour, c'est que le modèle a appris à plafonner le tampon flottant — et
    il faudra alors refaire toute la frontière de service, dont l'axe τ est une capacité.
    """
    base = dataclasses.replace(SimConfig.from_scenario(load_scenario("phase2")),
                               dispersion_arrivees=1.0)
    graines = range(4000, 4012)

    def production(facteur, initial=None):
        sy, us = initial or (base.stock_initial_stockyard_t, base.stock_initial_usine_t)
        config = dataclasses.replace(
            base,
            capacite_stockyard_t=base.capacite_stockyard_t * facteur,
            capacite_stock_usine_t=base.capacite_stock_usine_t * facteur,
            stock_initial_stockyard_t=sy, stock_initial_usine_t=us,
        )
        return [md.simulate(config, seed=g, years=1.0).dri_production_t for g in graines]

    reference = production(1.0)
    moyenne = fmean(reference)
    for facteur in (0.5, 2.0, 6.0):
        autre = production(facteur)
        ecarts = [b - a for a, b in zip(reference, autre, strict=True)]
        # en moyenne : moins de 0,01 %, soit quelques dizaines de tonnes sur 3,7 Mt, quand le
        # « gain du stockage » publié valait 2,57 points, c'est-à-dire ~95 000 t
        assert abs(fmean(ecarts)) / moyenne < 1e-4, f"facteur {facteur} : {fmean(ecarts):.1f} t"
        # réplication par réplication : moins de 0,1 %, donc jamais de quoi déplacer une
        # conclusion, même sur l'année la plus sensible
        assert max(abs(e) for e in ecarts) / moyenne < 1e-3, f"facteur {facteur}"

    # ... alors que le stock initial, lui, change tout
    double = production(1.0, initial=(base.stock_initial_stockyard_t * 2,
                                      base.stock_initial_usine_t * 2))
    assert fmean(double) > moyenne * 1.01


# --- approvisionnement endogène ---------------------------------------------
#
# Un navire est affrété quand la position de stock passe sous le point de commande. Ce qui
# est vérifié ici : les deux bilans de masse, la règle de commande elle-même, et le fait
# que le modèle exogène — dont dépend la validation analytique — n'a pas bougé.


@pytest.fixture(scope="module")
def endogene() -> SimConfig:
    return SimConfig.from_scenario(load_scenario("baseline_endogene"))


def _verifie_l_amont(resultat: md.SimResult) -> None:
    """Tout ce qui a été engagé est soit déchargé, soit encore à flot."""
    assert resultat.pellets_ordered_t == pytest.approx(
        resultat.pellets_delivered_t + resultat.pellets_afloat_t, rel=1e-9
    )


@pytest.mark.parametrize("scenario", ["baseline_endogene", "phase2_endogene"])
def test_les_deux_bilans_de_masse_tiennent_en_endogene(scenario):
    config = SimConfig.from_scenario(load_scenario(scenario))
    resultat = md.simulate(config, seed=GRAINE, years=1.0)
    _verifie_la_masse(resultat)
    _verifie_l_amont(resultat)


@pytest.mark.parametrize("dispersion", [0.0, 1.0])
def test_le_bilan_amont_tient_aussi_en_exogene(baseline, dispersion):
    """En exogène, un navire est « engagé » à son arrivée : le bilan doit valoir quand même."""
    config = dataclasses.replace(baseline, dispersion_arrivees=dispersion)
    _verifie_l_amont(md.simulate(config, seed=GRAINE, years=1.0))


def test_les_bilans_tiennent_en_endogene_sous_contre_pression(endogene):
    """Stockyard minuscule et aucune rame : tout sature, rien ne doit se perdre."""
    config = dataclasses.replace(
        endogene, capacite_stockyard_t=10_000.0, stock_initial_stockyard_t=0.0,
        nombre_de_rames=0,
    )
    resultat = md.simulate(config, seed=GRAINE, years=0.3)
    _verifie_la_masse(resultat)
    _verifie_l_amont(resultat)


def test_la_position_de_stock_ne_descend_pas_sous_le_point_de_commande(endogene):
    """La revue est continue : au plus une consommation horaire sépare le franchissement
    de la commande, d'où la tolérance d'un pas de consommation et pas davantage."""
    resultat = md.simulate(endogene, seed=GRAINE, years=2.0)
    pas = endogene.debit_pellets_t_par_h * endogene.pas_h
    assert resultat.inventory_position_min_t >= endogene.point_de_commande_t - pas


def test_la_position_moyenne_reste_entre_s_et_s_plus_un_navire(endogene):
    """Politique (s, Q) à Q = un navire : la position oscille entre s et s + Q."""
    resultat = md.simulate(endogene, seed=GRAINE, years=2.0)
    assert endogene.point_de_commande_t < resultat.inventory_position_mean_t
    assert resultat.inventory_position_mean_t < (
        endogene.point_de_commande_t + 1.5 * endogene.taille_navire_moyenne_t
    )


def test_les_commandes_suivent_la_consommation(endogene):
    """Sur un horizon long, on commande ce qu'on consomme : c'est la dérive de rappel."""
    # sur 3 ans, l'écart tient à la position finale moins l'initiale, bornée par s + Q :
    # ~2 % de la consommation, d'où la marge de 3 %
    resultat = md.simulate(endogene, seed=GRAINE, years=3.0)
    assert resultat.pellets_ordered_t / resultat.pellets_consumed_t == pytest.approx(1.0, abs=0.03)


def test_le_delai_tire_suit_l_hypothese(endogene):
    # ~60 commandes sur 3 ans : écart-type de la moyenne ~3,2 % pour cv = 0,25, d'où 10 %
    resultat = md.simulate(endogene, seed=GRAINE, years=3.0)
    assert resultat.lead_time_mean_h == pytest.approx(
        endogene.delai_approvisionnement_h, rel=0.10
    )
    assert resultat.pellets_at_sea_mean_t > 0.0


def test_la_dispersion_des_arrivees_est_sans_effet_en_endogene(endogene):
    """L'irrégularité n'est plus une entrée : elle sort de la politique et du délai."""
    regulier = md.simulate(dataclasses.replace(endogene, dispersion_arrivees=0.0),
                           seed=GRAINE, years=1.0)
    erratique = md.simulate(dataclasses.replace(endogene, dispersion_arrivees=1.5),
                            seed=GRAINE, years=1.0)
    assert dataclasses.asdict(regulier) | {"dispersion_arrivees": 0} == (
        dataclasses.asdict(erratique) | {"dispersion_arrivees": 0}
    )


def test_le_modele_endogene_est_reproductible(endogene):
    premier = md.simulate(endogene, seed=GRAINE, years=1.0)
    second = md.simulate(endogene, seed=GRAINE, years=1.0)
    assert dataclasses.asdict(premier) == dataclasses.asdict(second)


def test_un_point_de_commande_sous_la_demande_du_delai_programme_la_rupture(endogene):
    """s = 5 jours face à un délai de ~22 jours : chaque cycle finit à sec."""
    juste = md.simulate(endogene, seed=GRAINE, years=1.0)
    court = md.simulate(dataclasses.replace(
        endogene, point_de_commande_t=5 * endogene.debit_pellets_t_par_h * 24),
        seed=GRAINE, years=1.0)
    assert court.dri_stop_hours > 5 * max(juste.dri_stop_hours, 1.0)


def test_un_point_de_commande_genereux_supprime_presque_les_ruptures(endogene):
    large = md.simulate(dataclasses.replace(
        endogene, point_de_commande_t=60 * endogene.debit_pellets_t_par_h * 24),
        seed=GRAINE, years=2.0)
    assert large.dri_stop_hours < 0.002 * 2 * endogene.heures_ouvrees_par_an


@pytest.mark.slow
def test_un_delai_plus_variable_coute_des_ruptures(endogene):
    """Même point de commande, délai plus incertain : la marge de sécurité ne suffit plus.

    Marqué `slow` : une validation de comportement sur 36 années simulées.
    """
    def ruptures(cv):
        config = dataclasses.replace(endogene, cv_delai_approvisionnement=cv)
        return sum(md.simulate(config, seed=g, years=3.0).dri_stop_hours for g in range(6))
    assert ruptures(0.6) > ruptures(0.1)


def test_le_quai_n_est_plus_un_tampon_sans_plafond(endogene):
    """Ce que l'exogène ne savait pas faire : la politique plafonne le minerai à flot.

    En exogène à arrivées de Poisson, le quai se bloque des centaines d'heures par an, la
    flotte stockant ce que les silos ne peuvent pas prendre. En endogène, on ne commande
    pas ce qu'on n'a pas la place de recevoir.
    """
    resultat = md.simulate(endogene, seed=GRAINE, years=2.0)
    assert resultat.berth_blocked_h < 50.0


def test_en_exogene_une_commande_est_une_arrivee(baseline):
    resultat = md.simulate(baseline, seed=GRAINE, years=1.0)
    assert resultat.orders_placed == resultat.vessels_arrived
    assert resultat.lead_time_mean_h == 0.0
    assert resultat.pellets_at_sea_mean_t == 0.0


# --- production par période : les lots des moyennes par lots ----------------


@pytest.mark.parametrize("scenario", ["baseline", "baseline_endogene"])
def test_les_periodes_somment_exactement_a_la_production_totale(scenario):
    config = SimConfig.from_scenario(load_scenario(scenario))
    periodes = md.production_par_periode(config, seed=GRAINE, years=2.0)
    total = md.simulate(config, seed=GRAINE, years=2.0)
    assert len(periodes) == 2
    assert sum(p["production_t"] for p in periodes) == pytest.approx(total.dri_production_t)
    assert sum(p["rupture_h"] for p in periodes) == pytest.approx(total.dri_stop_hours)


def test_les_periodes_se_suivent_sans_trou_ni_chevauchement(endogene):
    periodes = md.production_par_periode(endogene, seed=GRAINE, years=1.5)
    assert periodes[0]["debut_h"] == 0.0
    for avant, apres in pairwise(periodes):
        assert apres["debut_h"] == avant["fin_h"]
    assert periodes[-1]["fin_h"] == pytest.approx(1.5 * endogene.heures_par_an)


def test_une_periode_ne_produit_pas_plus_que_sa_capacite(endogene):
    periodes = md.production_par_periode(endogene, seed=GRAINE, years=2.0)
    plafond = endogene.debit_dri_t_par_h * endogene.heures_par_an
    assert all(p["production_t"] <= plafond * (1 + 1e-9) for p in periodes)


# --- étape 2 : régime stationnaire, navires en rade, options de la politique --


def _verifie_l_amont_fenetre(resultat: md.SimResult) -> None:
    """Bilan amont sur une fenêtre : ce qui était à flot au début compte comme engagé."""
    assert resultat.pellets_afloat_initial_t + resultat.pellets_ordered_t == pytest.approx(
        resultat.pellets_delivered_t + resultat.pellets_afloat_t, rel=1e-9
    )


@pytest.mark.parametrize("scenario", ["baseline", "phase2_endogene"])
def test_les_deux_bilans_tiennent_exactement_apres_echauffement(scenario):
    """C'est la raison de l'instantané plutôt que d'une remise à zéro des compteurs."""
    config = SimConfig.from_scenario(load_scenario(scenario))
    resultat = md.simulate(config, seed=GRAINE, years=1.5, warmup_years=0.5)
    _verifie_la_masse(resultat)
    _verifie_l_amont_fenetre(resultat)


@pytest.mark.parametrize("scenario", ["baseline", "baseline_endogene"])
def test_la_fenetre_est_exactement_la_difference_de_deux_horizons(scenario):
    """Le modèle étant causal, la fenêtre [W, T] est le cumul à T moins le cumul à W."""
    config = SimConfig.from_scenario(load_scenario(scenario))
    fenetre = md.simulate(config, seed=GRAINE, years=1.0, warmup_years=0.5)
    long = md.simulate(config, seed=GRAINE, years=1.0)
    court = md.simulate(config, seed=GRAINE, years=0.5)
    assert fenetre.dri_production_t == pytest.approx(
        long.dri_production_t - court.dri_production_t)
    assert fenetre.dri_stop_hours == pytest.approx(long.dri_stop_hours - court.dri_stop_hours)
    assert fenetre.anchorage_ship_hours == pytest.approx(
        long.anchorage_ship_hours - court.anchorage_ship_hours)
    assert fenetre.annees_mesurees == pytest.approx(0.5)


def test_sans_echauffement_l_amont_part_de_zero(endogene):
    resultat = md.simulate(endogene, seed=GRAINE, years=1.0)
    assert resultat.pellets_afloat_initial_t == 0.0
    assert resultat.warmup_years == 0.0
    assert resultat.annees_mesurees == 1.0


def test_un_echauffement_qui_couvre_tout_l_horizon_est_refuse(endogene):
    with pytest.raises(ValueError, match="rien à mesurer"):
        md.simulate(endogene, seed=GRAINE, years=2.0, warmup_years=2.0)


def test_les_heures_en_rade_couvrent_au_moins_les_attentes_terminees(baseline):
    """Intégrées dans le temps, elles comptent aussi les navires encore en rade à la fin."""
    erratique = dataclasses.replace(baseline, dispersion_arrivees=1.0)
    resultat = md.simulate(erratique, seed=GRAINE, years=1.0)
    attentes = md.attentes_en_rade(erratique, seed=GRAINE, years=1.0)
    assert resultat.anchorage_ship_hours >= sum(attentes) - 1e-6
    assert resultat.anchorage_ship_hours > 0


def test_la_distribution_des_attentes_redonne_la_moyenne_publiee(endogene):
    resultat = md.simulate(endogene, seed=GRAINE, years=1.5, warmup_years=0.5)
    attentes = md.attentes_en_rade(endogene, seed=GRAINE, years=1.5, warmup_years=0.5)
    assert fmean(attentes) == pytest.approx(resultat.wait_mean_h)


def _ordres(config, seed=GRAINE, years=1.5):
    """Tailles des navires dans l'ordre de commande, puis dans l'ordre d'arrivée en rade."""
    commandes, arrivees = [], []
    en_mer, navire = md._Corridor.navire_en_mer, md._Corridor.vessel

    def espion_mer(self, size_t, rate, delai_h):
        commandes.append(size_t)
        return en_mer(self, size_t, rate, delai_h)

    def espion_navire(self, size_t, rate):
        arrivees.append(size_t)
        return navire(self, size_t, rate)

    md._Corridor.navire_en_mer, md._Corridor.vessel = espion_mer, espion_navire
    try:
        md.simulate(config, seed=seed, years=years)
    finally:
        md._Corridor.navire_en_mer, md._Corridor.vessel = en_mer, navire
    return commandes, arrivees


def test_sans_depassement_les_navires_arrivent_dans_l_ordre_de_commande():
    config = dataclasses.replace(SimConfig.from_scenario(load_scenario("phase2_endogene")),
                                 depassements_autorises=False)
    commandes, arrivees = _ordres(config)
    assert arrivees == commandes[:len(arrivees)]


def test_avec_depassements_l_ordre_d_arrivee_differe():
    """Garde-fou du test précédent : sans dépassement possible, il ne prouverait rien."""
    config = SimConfig.from_scenario(load_scenario("phase2_endogene"))
    commandes, arrivees = _ordres(config)
    assert arrivees != commandes[:len(arrivees)]


def test_les_options_endogenes_sont_sans_effet_en_exogene(baseline):
    erratique = dataclasses.replace(baseline, dispersion_arrivees=1.0)
    reference = md.simulate(erratique, seed=GRAINE, years=1.0)
    options = md.simulate(dataclasses.replace(
        erratique, depassements_autorises=False, reporter_si_navire_en_rade=True),
        seed=GRAINE, years=1.0)
    assert dataclasses.asdict(reference) == dataclasses.asdict(options)


def test_le_report_ne_s_active_que_si_l_option_est_demandee():
    config = SimConfig.from_scenario(load_scenario("phase2_endogene"))
    sans = md.simulate(config, seed=GRAINE, years=1.5)
    avec = md.simulate(dataclasses.replace(config, reporter_si_navire_en_rade=True),
                       seed=GRAINE, years=1.5)
    assert sans.order_deferral_h == 0.0
    assert avec.order_deferral_h > 0.0


def test_les_bilans_tiennent_avec_les_deux_options():
    config = dataclasses.replace(SimConfig.from_scenario(load_scenario("phase2_endogene")),
                                 depassements_autorises=False, reporter_si_navire_en_rade=True)
    resultat = md.simulate(config, seed=GRAINE, years=1.5, warmup_years=0.5)
    _verifie_la_masse(resultat)
    _verifie_l_amont_fenetre(resultat)


# --- étape 3 : la chronique, pour juger la dispersion comme sur les données ---


@pytest.mark.parametrize("scenario", ["baseline", "baseline_endogene"])
def test_le_tonnage_decharge_par_periode_somme_au_total(scenario):
    config = SimConfig.from_scenario(load_scenario(scenario))
    c = md.chronique(config, seed=GRAINE, years=1.0, periode_h=config.heures_par_an / 4)
    total = md.simulate(config, seed=GRAINE, years=1.0)
    assert len(c["periodes"]) == 4
    assert sum(p["debarque_t"] for p in c["periodes"]) == pytest.approx(
        total.pellets_delivered_t)


def test_chaque_attente_porte_la_date_d_arrivee_de_son_navire(endogene):
    c = md.chronique(endogene, seed=GRAINE, years=1.0)
    assert len(c["attentes_h"]) == len(c["attentes_arrivee_h"])
    assert set(c["attentes_arrivee_h"]) <= set(c["arrivees_h"])


def test_les_arrivees_sont_datees_dans_l_horizon_et_dans_l_ordre(endogene):
    c = md.chronique(endogene, seed=GRAINE, years=1.0)
    assert c["arrivees_h"] == sorted(c["arrivees_h"])
    assert all(0 <= a < endogene.heures_par_an for a in c["arrivees_h"])


def test_production_par_periode_reste_une_vue_de_la_chronique(endogene):
    """Le refactoring ne doit pas changer la sortie historique."""
    vue = md.production_par_periode(endogene, seed=GRAINE, years=1.0)
    brute = md.chronique(endogene, seed=GRAINE, years=1.0)["periodes"]
    assert [p["production_t"] for p in vue] == [p["production_t"] for p in brute]
    assert set(vue[0]) == {"debut_h", "fin_h", "production_t", "rupture_h"}


# --- étape 5 : arrêts discrets du four ------------------------------------------


@pytest.fixture(scope="module")
def discret(endogene) -> SimConfig:
    return dataclasses.replace(endogene, mode_arrets_dri="discret")


def _ample(config):
    """Stock généreux : on isole la disponibilité du four, sans rupture de pellets."""
    return dataclasses.replace(
        config, point_de_commande_t=90 * config.debit_pellets_t_par_h * 24,
        point_de_commande_j=90.0, capacite_stockyard_t=3e6, capacite_stock_usine_t=1.5e6)


def test_en_mode_continu_il_n_y_a_aucun_arret_non_planifie(endogene):
    assert md.simulate(endogene, seed=GRAINE, years=1.0).dri_breakdown_hours == 0.0


def test_les_deux_bilans_tiennent_en_mode_discret(discret):
    resultat = md.simulate(discret, seed=GRAINE, years=2.0, warmup_years=0.5)
    _verifie_la_masse(resultat)
    _verifie_l_amont_fenetre(resultat)


def _disponibilite(config, graines, annees):
    dispos = []
    for g in graines:
        r = md.simulate(config, seed=g, years=annees)
        dispos.append(1 - r.dri_breakdown_hours / (config.heures_ouvrees_par_an * annees))
    return dispos


def test_la_disponibilite_realisee_rejoint_le_taux_d_utilisation(discret):
    """Même disponibilité moyenne que le mode continu : c'est la condition de l'étape 5.

    Avec des arrêts courts (12 h), il y a des centaines de cycles par an et la disponibilité
    réalisée se resserre vite : sur 10 ans, son écart-type est de ~0,006, d'où une marge de
    0,02 (plus de trois écarts-types). Sur 3 ans, elle aurait été de 0,011, et le test aurait
    échoué par malchance une fois sur quinze.
    """
    config = dataclasses.replace(_ample(discret), duree_arret_h=12.0,
                                 duree_marche_moyenne_h=12.0 * 0.85 / 0.15)
    (dispo,) = _disponibilite(config, [GRAINE], 10.0)
    assert dispo == pytest.approx(config.taux_utilisation_dri, abs=0.02)


@pytest.mark.slow
def test_la_disponibilite_tient_aussi_avec_des_arrets_de_sept_jours(discret):
    """Valeur centrale de l'hypothèse : peu de cycles par an, donc 12 graines poolées.

    Une réplication de 30 ans a un écart-type de ~0,013 sur la disponibilité : un test sur
    une seule graine échouerait une fois sur cinquante par simple malchance.
    """
    dispos = _disponibilite(_ample(discret), range(12), 30.0)
    assert fmean(dispos) == pytest.approx(discret.taux_utilisation_dri, abs=0.012)


def test_en_marche_le_four_tourne_a_pleine_cadence(discret):
    config = _ample(discret)
    r = md.simulate(config, seed=GRAINE, years=3.0)
    heures_en_marche = config.heures_ouvrees_par_an * 3 - r.dri_breakdown_hours - r.dri_stop_hours
    assert r.dri_production_t / heures_en_marche == pytest.approx(
        config.debit_dri_plein_t_par_h, rel=1e-3)


def test_la_sequence_des_pannes_ne_depend_pas_du_stockage(discret):
    """Flux dédié et horloge des heures ouvrées : deux stockages voient les mêmes pannes."""
    petit = md.simulate(dataclasses.replace(discret, capacite_stockyard_t=150_000.0),
                        seed=GRAINE, years=2.0)
    grand = md.simulate(dataclasses.replace(discret, capacite_stockyard_t=900_000.0),
                        seed=GRAINE, years=2.0)
    assert petit.dri_breakdown_hours == grand.dri_breakdown_hours
    assert petit.dri_breakdown_hours > 0


@pytest.mark.slow
def test_des_arrets_plus_longs_rendent_la_consommation_trimestrielle_plus_irreguliere(discret):
    """Même disponibilité, arrêts plus longs et plus rares : les trimestres divergent davantage."""
    def cv2_consommation(jours):
        config = dataclasses.replace(_ample(discret), duree_arret_h=jours * 24.0,
                                     duree_marche_moyenne_h=jours * 24.0 * 0.85 / 0.15)
        c = md.chronique(config, seed=GRAINE, years=12.0, periode_h=config.heures_par_an / 4)
        prod = [p["production_t"] for p in c["periodes"][4:]]
        return statistics.variance(prod) / statistics.fmean(prod) ** 2
    assert cv2_consommation(30) > 2 * cv2_consommation(2)


# --- étape 7 : politique de commande consciente des arrêts ------------------------


def test_la_politique_des_arrets_est_aveugle_par_defaut(endogene):
    assert endogene.politique_arrets == "aveugle"


def _corridor(config):
    _, corridor = md._construire(config, GRAINE, 1.0, None, 0.0)
    return corridor


def test_aveugle_ne_touche_pas_a_la_position(discret):
    corridor = _corridor(discret)
    corridor._pendant_un_arret(100.0)
    assert corridor.consommation_fictive_t == 0.0


def test_rythme_moyen_retranche_une_heure_de_consommation_moyenne_par_heure(discret):
    config = dataclasses.replace(discret, politique_arrets="rythme_moyen")
    corridor = _corridor(config)
    corridor._pendant_un_arret(100.0)
    corridor._pendant_un_arret(99.0)
    assert corridor.consommation_fictive_t == pytest.approx(
        2 * config.debit_pellets_t_par_h * config.pas_h)


@pytest.mark.parametrize("fraction", [2.0, 1.0, 0.5, 0.0])
def test_date_de_reprise_compte_la_consommation_qui_viendra_dans_le_delai(discret, fraction):
    config = dataclasses.replace(discret, politique_arrets="date_de_reprise")
    corridor = _corridor(config)
    delai = config.delai_approvisionnement_h
    corridor._pendant_un_arret(fraction * delai)
    attendue = config.debit_pellets_plein_t_par_h * max(delai - fraction * delai, 0.0)
    assert corridor.consommation_fictive_t == pytest.approx(
        attendue - config.debit_pellets_t_par_h * delai)


def test_date_de_reprise_ne_commande_rien_de_plus_tant_que_la_reprise_est_loin(discret):
    """Arrêt plus long que le délai : la position vue est même relevée, rien à commander."""
    config = dataclasses.replace(discret, politique_arrets="date_de_reprise")
    corridor = _corridor(config)
    corridor._pendant_un_arret(3 * config.delai_approvisionnement_h)
    assert corridor.consommation_fictive_t < 0


@pytest.mark.parametrize("politique", ["rythme_moyen", "date_de_reprise"])
def test_les_deux_bilans_tiennent_avec_une_politique_consciente_des_arrets(discret, politique):
    resultat = md.simulate(dataclasses.replace(discret, politique_arrets=politique),
                           seed=GRAINE, years=1.0, warmup_years=0.25)
    _verifie_la_masse(resultat)
    _verifie_l_amont_fenetre(resultat)


def test_commander_pendant_les_arrets_releve_le_stock_physique(discret):
    """Les commandes passées pendant l'arrêt arrivent : le stock moyen monte."""
    def stock(politique):
        r = md.simulate(dataclasses.replace(discret, politique_arrets=politique),
                        seed=GRAINE, years=2.5, warmup_years=0.5)
        return r.stockyard_mean_t + r.plant_stock_mean_t
    assert stock("rythme_moyen") > stock("aveugle")


def test_les_pannes_restent_les_memes_quelle_que_soit_la_politique(discret):
    heures = {p: md.simulate(dataclasses.replace(discret, politique_arrets=p), seed=GRAINE,
                             years=1.0).dri_breakdown_hours
              for p in ("aveugle", "rythme_moyen", "date_de_reprise")}
    assert len(set(heures.values())) == 1
