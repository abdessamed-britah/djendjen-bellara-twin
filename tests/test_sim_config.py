"""Tests du chargement des hypothèses et des grandeurs dérivées.

Aucun chiffre de référence n'est recopié à la main : les valeurs attendues sont
recalculées ici depuis `config/assumptions.yaml`, de sorte qu'une modification des
hypothèses fasse évoluer les tests avec le modèle et non contre lui.
"""

from itertools import pairwise

import pytest

from corridor.sim import config as cfg

ASSUMPTIONS = cfg.load_yaml(cfg.ASSUMPTIONS_PATH)


def _value(dotted: str) -> float:
    section, key = dotted.split(".")
    return ASSUMPTIONS[section][key]["value"]


@pytest.fixture(scope="module")
def baseline():
    return cfg.SimConfig.from_scenario(cfg.load_scenario("baseline"))


# --- chargement et aplatissement ------------------------------------------


def test_assumption_values_aplatit_en_cles_pointees():
    values = cfg.assumption_values(ASSUMPTIONS)
    assert values["usine.capacite_dri"] == _value("usine.capacite_dri")
    assert values["rail.nombre_de_rames"] == _value("rail.nombre_de_rames")
    # une hypothèse sans valeur reste présente, à None : son absence doit se voir
    assert values["couts.cout_journee_arret_dri"] is None


def test_value_of_leve_sur_une_cle_inconnue():
    values = cfg.assumption_values(ASSUMPTIONS)
    with pytest.raises(KeyError, match="usine.capacite_inexistante"):
        cfg.value_of(values, "usine.capacite_inexistante")


def test_value_of_leve_sur_une_hypothese_sans_valeur():
    values = cfg.assumption_values(ASSUMPTIONS)
    with pytest.raises(ValueError, match="sans valeur"):
        cfg.value_of(values, "couts.cout_journee_arret_dri")


def test_toutes_les_hypotheses_portent_un_statut_connu():
    for section, entries in ASSUMPTIONS.items():
        for key, entry in entries.items():
            assert entry["status"] in cfg.STATUSES, f"{section}.{key}: {entry.get('status')}"


# --- fusion des scénarios --------------------------------------------------


def test_un_ecart_litteral_remplace_la_valeur():
    values = {"rail.nombre_de_rames": 3.0}
    fusionne = cfg.apply_overrides(values, {"rail.nombre_de_rames": {"value": 6}})
    assert fusionne["rail.nombre_de_rames"] == 6


def test_un_ecart_multiplicatif_reference_une_autre_hypothese():
    values = {"usine.capacite_dri": 2.5, "phase_2.facteur_dri": 2.0}
    fusionne = cfg.apply_overrides(
        values, {"usine.capacite_dri": {"multiply_by": "phase_2.facteur_dri"}}
    )
    assert fusionne["usine.capacite_dri"] == pytest.approx(5.0)
    assert values["usine.capacite_dri"] == 2.5  # l'original n'est pas modifié


def test_un_ecart_sur_une_cle_inconnue_est_refuse():
    with pytest.raises(KeyError, match="rail.nombre_de_locomotives"):
        cfg.apply_overrides({"rail.nombre_de_rames": 3.0},
                            {"rail.nombre_de_locomotives": {"value": 2}})


def test_une_forme_d_ecart_inconnue_est_refusee():
    with pytest.raises(ValueError, match="value.*multiply_by"):
        cfg.apply_overrides({"rail.nombre_de_rames": 3.0},
                            {"rail.nombre_de_rames": {"plus": 1}})


def test_les_trois_scenarios_se_chargent():
    for name in ("baseline", "phase2", "phase2_plus_stockage"):
        scenario = cfg.load_scenario(name)
        assert scenario.name == name
        assert scenario.description


def test_scenario_inconnu_leve_une_erreur():
    with pytest.raises(FileNotFoundError, match="phase3"):
        cfg.load_scenario("phase3")


def test_baseline_ne_devie_pas_des_hypotheses():
    baseline = cfg.load_scenario("baseline")
    reference = cfg.assumption_values(ASSUMPTIONS)
    ecarts = {k: v for k, v in baseline.values.items() if reference[k] != v}
    assert ecarts == {}


def test_phase2_double_la_capacite_dri_via_le_facteur():
    phase2 = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2"))
    attendu = _value("usine.capacite_dri") * _value("phase_2.facteur_dri")
    assert phase2.capacite_dri_t_par_an == pytest.approx(attendu * 1e6)


def test_phase2_plus_stockage_augmente_les_deux_stocks():
    phase2 = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2"))
    plus = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2_plus_stockage"))
    assert plus.capacite_dri_t_par_an == pytest.approx(phase2.capacite_dri_t_par_an)
    assert plus.capacite_stockyard_t > phase2.capacite_stockyard_t
    assert plus.capacite_stock_usine_t > phase2.capacite_stock_usine_t
    # les deux capacités restent dans leur fourchette publiée
    assert plus.capacite_stockyard_t <= ASSUMPTIONS["stockage_port"][
        "capacite_stockyard_pellets"
    ]["range"][1]
    assert plus.capacite_stock_usine_t <= ASSUMPTIONS["usine"][
        "capacite_stock_pellets_usine"
    ]["range"][1]


def test_politique_d_expedition_est_un_choix_ferme():
    values = cfg.assumption_values(ASSUMPTIONS)
    assert cfg.text_of(values, "rail.politique_expedition", cfg.EXPEDITION_POLICIES) == "pull"
    with pytest.raises(ValueError, match="attendu l'un de"):
        cfg.text_of({"x.y": "glisser"}, "x.y", cfg.EXPEDITION_POLICIES)


def test_value_of_refuse_une_hypothese_textuelle():
    values = cfg.assumption_values(ASSUMPTIONS)
    with pytest.raises(TypeError, match="non numérique"):
        cfg.value_of(values, "rail.politique_expedition")


# --- grandeurs dérivées ----------------------------------------------------


def test_debit_dri_reparti_sur_l_annee_entiere(baseline):
    heures = _value("simulation.jours_par_an") * 24
    attendu = _value("usine.capacite_dri") * 1e6 * _value("usine.taux_utilisation_dri") / heures
    assert baseline.debit_dri_t_par_h == pytest.approx(attendu)


def test_debit_pellets_suit_le_ratio(baseline):
    attendu = baseline.debit_dri_t_par_h * _value("usine.pellets_par_t_dri")
    assert baseline.debit_pellets_t_par_h == pytest.approx(attendu)


def test_besoin_annuel_ne_compte_que_les_jours_ouvres(baseline):
    jours_ouvres = _value("simulation.jours_par_an") - _value("usine.arrets_planifies_dri")
    attendu = baseline.debit_pellets_t_par_h * jours_ouvres * 24
    assert baseline.besoin_annuel_pellets_t == pytest.approx(attendu)
    assert baseline.heures_ouvrees_par_an == pytest.approx(jours_ouvres * 24)


def test_nombre_de_navires_et_intervalle_moyen(baseline):
    attendu = baseline.besoin_annuel_pellets_t / _value("navires_minerai.taille_moyenne")
    assert baseline.navires_par_an == pytest.approx(attendu)
    assert baseline.intervalle_moyen_arrivees_h == pytest.approx(
        _value("simulation.jours_par_an") * 24 / attendu
    )


def test_duree_trajet_derivee_de_la_distance(baseline):
    attendu = _value("rail.distance_port_usine") / _value("rail.vitesse_moyenne")
    assert baseline.duree_trajet_h == pytest.approx(attendu)


def test_cadence_horaire_derivee_de_la_cadence_journaliere(baseline):
    assert baseline.cadence_dechargement_t_par_h == pytest.approx(
        _value("navires_minerai.cadence_dechargement") / 24
    )


def test_arrets_planifies_repartis_en_fenetres(baseline):
    nombre = int(_value("simulation.arrets_planifies_nombre"))
    fenetres = baseline.arrets_planifies_fenetres(years=1.0)
    assert len(fenetres) == nombre
    duree_totale = sum(fin - debut for debut, fin in fenetres)
    assert duree_totale == pytest.approx(_value("usine.arrets_planifies_dri") * 24)
    # les fenêtres sont disjointes, ordonnées, et tiennent dans l'année
    for (_d1, f1), (d2, _f2) in pairwise(fenetres):
        assert f1 < d2
    assert fenetres[-1][1] <= _value("simulation.jours_par_an") * 24


def test_arrets_planifies_sur_deux_ans_se_repetent(baseline):
    nombre = int(_value("simulation.arrets_planifies_nombre"))
    fenetres = baseline.arrets_planifies_fenetres(years=2.0)
    assert len(fenetres) == 2 * nombre
    duree_totale = sum(fin - debut for debut, fin in fenetres)
    assert duree_totale == pytest.approx(2 * _value("usine.arrets_planifies_dri") * 24)


def test_stocks_initiaux_a_la_fraction_prevue(baseline):
    fraction = _value("simulation.fraction_stock_initial")
    assert baseline.stock_initial_stockyard_t == pytest.approx(
        _value("stockage_port.capacite_stockyard_pellets") * fraction
    )
    assert baseline.stock_initial_usine_t == pytest.approx(
        _value("usine.capacite_stock_pellets_usine") * fraction
    )


def test_capacite_ferroviaire_et_besoin_sont_coherents(baseline):
    # la capacité du rail doit dépasser le besoin du DRI, sinon le corridor
    # est bloqué avant même d'avoir un navire
    assert baseline.capacite_rail_t_par_h > baseline.debit_pellets_t_par_h


def test_config_immuable(baseline):
    with pytest.raises((AttributeError, TypeError)):
        baseline.nombre_de_rames = 99
