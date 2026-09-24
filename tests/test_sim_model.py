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
import time

import pytest

from corridor.sim import model as md
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
