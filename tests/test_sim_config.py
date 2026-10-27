"""Tests du chargement des hypothèses et des grandeurs dérivées.

Aucun chiffre de référence n'est recopié à la main : les valeurs attendues sont
recalculées ici depuis `config/assumptions.yaml`, de sorte qu'une modification des
hypothèses fasse évoluer les tests avec le modèle et non contre lui.
"""

import dataclasses
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
    # (aucune n'est vide dans assumptions.yaml : jeu synthétique)
    assert cfg.assumption_values({"couts": {"x": {"value": None}}})["couts.x"] is None


def test_value_of_leve_sur_une_cle_inconnue():
    values = cfg.assumption_values(ASSUMPTIONS)
    with pytest.raises(KeyError, match="usine.capacite_inexistante"):
        cfg.value_of(values, "usine.capacite_inexistante")


def test_value_of_leve_sur_une_hypothese_sans_valeur():
    values = cfg.assumption_values({"couts": {"x": {"value": None}}})
    with pytest.raises(ValueError, match="sans valeur"):
        cfg.value_of(values, "couts.x")


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


def test_les_scenarios_se_chargent():
    for name in ("baseline", "phase2", "phase2_plus_stockage", "phase2_plus_poste",
                 "phase2_plus_poste_plus_stockage", "phase2_mesure",
                 "baseline_sans_blocage"):
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


def test_phase2_plus_poste_double_le_poste_seulement():
    phase2 = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2"))
    plus = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2_plus_poste"))
    assert plus.postes_minerai == 2 * phase2.postes_minerai
    assert plus.postes_minerai <= ASSUMPTIONS["navires_minerai"]["postes_minerai"]["range"][1]
    assert plus.capacite_dri_t_par_an == pytest.approx(phase2.capacite_dri_t_par_an)
    assert plus.capacite_stockyard_t == phase2.capacite_stockyard_t
    assert plus.capacite_stock_usine_t == phase2.capacite_stock_usine_t


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


# --- écart de scénario par copie d'une autre hypothèse -----------------------


def test_copy_from_prend_la_valeur_d_une_autre_hypothese():
    """Permet qu'une hypothèse mesurée alimente un paramètre sans recopier son chiffre."""
    valeurs = {"a.mesure": 0.91, "b.parametre": 0.0}
    obtenu = cfg.apply_overrides(valeurs, {"b.parametre": {"copy_from": "a.mesure"}})
    assert obtenu["b.parametre"] == 0.91
    assert obtenu["a.mesure"] == 0.91  # la source n'est pas modifiée


def test_copy_from_suit_la_source_si_elle_change():
    """Tout l'intérêt : réviser l'estimation suffit, le scénario n'a pas à être touché."""
    for mesure in (0.5, 0.91, 1.4):
        obtenu = cfg.apply_overrides(
            {"a.mesure": mesure, "b.parametre": 0.0},
            {"b.parametre": {"copy_from": "a.mesure"}},
        )
        assert obtenu["b.parametre"] == mesure


def test_copy_from_sur_une_source_inconnue_leve():
    with pytest.raises(KeyError):
        cfg.apply_overrides({"b.parametre": 0.0},
                            {"b.parametre": {"copy_from": "a.absente"}})


def test_un_ecart_sans_forme_connue_leve():
    with pytest.raises(ValueError, match="copy_from"):
        cfg.apply_overrides({"a": 1.0}, {"a": {"plus": 1}})


def test_phase2_mesure_pointe_l_estimation_et_ne_la_recopie_pas():
    """Garde-fou : si quelqu'un remplace `copy_from` par un littéral, ce test le voit.

    Lu dans le fichier du scénario, pas dans l'objet chargé : c'est la forme de l'écart
    qu'on veut figer, pas sa valeur résolue.
    """
    spec = cfg.load_yaml(cfg.SCENARIOS_DIR / "phase2_mesure.yaml")
    ecart = spec["overrides"]["simulation.dispersion_arrivees"]
    assert ecart == {"copy_from": "simulation.dispersion_arrivees_observee"}


def test_phase2_mesure_a_bien_la_dispersion_estimee():
    mesure = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2_mesure"))
    brut = cfg.load_yaml(cfg.ASSUMPTIONS_PATH)
    attendu = brut["simulation"]["dispersion_arrivees_observee"]["value"]
    assert mesure.dispersion_arrivees == pytest.approx(attendu)
    assert mesure.dispersion_arrivees > 0.0  # contrairement à baseline et phase2


def test_phase2_mesure_ne_differe_de_phase2_que_par_la_dispersion():
    """Même cible, même stockage, même poste : seul l'affrètement change."""
    mesure = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2_mesure"))
    phase2 = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2"))
    assert mesure.capacite_dri_t_par_an == phase2.capacite_dri_t_par_an
    assert mesure.capacite_stockyard_t == phase2.capacite_stockyard_t
    assert mesure.capacite_stock_usine_t == phase2.capacite_stock_usine_t
    assert mesure.postes_minerai == phase2.postes_minerai
    assert mesure.dispersion_arrivees != phase2.dispersion_arrivees


# --- approvisionnement endogène ---------------------------------------------


def test_les_scenarios_historiques_restent_exogenes(baseline):
    """La validation Pollaczek-Khinchine exige des arrivées de Poisson exogènes."""
    assert baseline.politique_approvisionnement == "exogene"
    for nom in ("phase2", "baseline_sans_blocage", "phase2_mesure"):
        config = cfg.SimConfig.from_scenario(cfg.load_scenario(nom))
        assert config.politique_approvisionnement == "exogene", nom


@pytest.mark.parametrize("nom", ["baseline_endogene", "phase2_endogene"])
def test_les_scenarios_endogenes_commandent_au_point_de_commande(nom):
    config = cfg.SimConfig.from_scenario(cfg.load_scenario(nom))
    assert config.politique_approvisionnement == "point_de_commande"


def test_phase2_endogene_ne_differe_de_phase2_que_par_l_approvisionnement():
    phase2 = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2"))
    endogene = cfg.SimConfig.from_scenario(cfg.load_scenario("phase2_endogene"))
    assert dataclasses.replace(endogene, politique_approvisionnement="exogene") == phase2


def test_le_point_de_commande_est_converti_en_jours_de_consommation(baseline):
    """s est publié en jours, comme τ ; le modèle ne voit que des tonnes."""
    attendu = _value("approvisionnement.point_de_commande_j") * baseline.debit_pellets_t_par_h * 24
    assert baseline.point_de_commande_t == pytest.approx(attendu)


def test_le_delai_d_approvisionnement_est_en_heures(baseline):
    assert baseline.delai_approvisionnement_h == pytest.approx(
        _value("approvisionnement.delai_moyen_j") * 24
    )
    assert baseline.cv_delai_approvisionnement == _value("approvisionnement.cv_delai")


def test_les_trois_hypotheses_d_approvisionnement_sont_estimees_avec_fourchette():
    for cle, entree in ASSUMPTIONS["approvisionnement"].items():
        assert entree["status"] == "estimé", cle
        bas, haut = entree["range"]
        assert bas < entree["value"] < haut, cle


def test_le_point_de_commande_central_couvre_le_delai_central():
    """Sinon la rupture serait programmée à chaque cycle, par construction."""
    appro = ASSUMPTIONS["approvisionnement"]
    assert appro["point_de_commande_j"]["value"] > appro["delai_moyen_j"]["value"]


def test_la_politique_d_approvisionnement_est_un_choix_ferme():
    valeurs = cfg.assumption_values(ASSUMPTIONS)
    valeurs["simulation.politique_approvisionnement"] = "juste_a_temps"
    scenario = cfg.Scenario(name="x", description="", values=valeurs)
    with pytest.raises(ValueError, match="politique_approvisionnement"):
        cfg.SimConfig.from_scenario(scenario)


def test_un_point_de_commande_nul_est_refuse_en_politique_endogene():
    valeurs = cfg.assumption_values(ASSUMPTIONS)
    valeurs["simulation.politique_approvisionnement"] = "point_de_commande"
    valeurs["approvisionnement.point_de_commande_j"] = 0.0
    scenario = cfg.Scenario(name="x", description="", values=valeurs)
    with pytest.raises(ValueError, match="point de commande"):
        cfg.SimConfig.from_scenario(scenario)


def test_un_point_de_commande_nul_est_tolere_en_politique_exogene():
    """En exogène il est ignoré : le refuser bloquerait un scénario qui ne s'en sert pas."""
    valeurs = cfg.assumption_values(ASSUMPTIONS)
    valeurs["approvisionnement.point_de_commande_j"] = 0.0
    scenario = cfg.Scenario(name="x", description="", values=valeurs)
    assert cfg.SimConfig.from_scenario(scenario).point_de_commande_t == 0.0


# --- étape 2 : coûts et options -----------------------------------------------


def test_les_surestaries_ont_une_seule_cle_en_usd_par_heure():
    """Deux clés pour un même fait finiraient par diverger : l'ancienne a été déplacée."""
    assert "taux_surestaries" not in ASSUMPTIONS["navires_minerai"]
    entree = ASSUMPTIONS["couts"]["surestaries_usd_par_heure"]
    assert entree["status"] == "estimé"
    bas, haut = entree["range"]
    assert bas < entree["value"] < haut
    assert haut / bas >= 5, "fourchette large demandée"


def test_le_portage_a_ses_deux_facteurs_dans_les_couts():
    for cle in ("prix_pellets", "taux_de_portage"):
        assert ASSUMPTIONS["couts"][cle]["status"] == "estimé"


def test_les_options_par_defaut_sont_celles_du_modele_de_l_etape_1(baseline):
    """Dépassements permis, pas de report : sinon l'étape 1 ne serait plus reproductible."""
    assert baseline.depassements_autorises is True
    assert baseline.reporter_si_navire_en_rade is False


def test_une_option_booleenne_se_change_par_scenario():
    valeurs = cfg.apply_overrides(cfg.assumption_values(ASSUMPTIONS),
                                  {"simulation.depassements_autorises": {"value": False}})
    scenario = cfg.Scenario(name="x", description="", values=valeurs)
    assert cfg.SimConfig.from_scenario(scenario).depassements_autorises is False


# --- étape 5 : arrêts discrets du four -------------------------------------------


def test_le_mode_continu_reste_celui_par_defaut(baseline):
    """Le mode historique : basculer en silence changerait tous les résultats publiés."""
    assert baseline.mode_arrets_dri == "continu"


def test_la_duree_de_marche_rend_la_disponibilite_egale_au_taux_d_utilisation(baseline):
    marche, arret = baseline.duree_marche_moyenne_h, baseline.duree_arret_h
    assert marche / (marche + arret) == pytest.approx(baseline.taux_utilisation_dri)


def test_la_pleine_cadence_est_la_capacite_sans_taux_d_utilisation(baseline):
    assert baseline.debit_dri_plein_t_par_h * baseline.taux_utilisation_dri == pytest.approx(
        baseline.debit_dri_t_par_h)
    assert baseline.debit_pellets_plein_t_par_h == pytest.approx(
        baseline.debit_dri_plein_t_par_h * baseline.pellets_par_t_dri)


def test_les_hypotheses_d_arret_sont_estimees_avec_une_fourchette_large():
    for cle in ("duree_arret_non_planifie_j", "cv_duree_arret"):
        entree = ASSUMPTIONS["usine"][cle]
        assert entree["status"] == "estimé"
        bas, haut = entree["range"]
        assert bas < entree["value"] < haut
    bas, haut = ASSUMPTIONS["usine"]["duree_arret_non_planifie_j"]["range"]
    assert haut / bas >= 10


def test_le_mode_d_arret_est_un_choix_ferme():
    valeurs = cfg.assumption_values(ASSUMPTIONS)
    valeurs["simulation.mode_arrets_dri"] = "intermittent"
    with pytest.raises(ValueError, match="mode_arrets_dri"):
        cfg.SimConfig.from_scenario(cfg.Scenario(name="x", description="", values=valeurs))


def test_le_mode_discret_refuse_un_four_toujours_disponible():
    valeurs = cfg.assumption_values(ASSUMPTIONS)
    valeurs["simulation.mode_arrets_dri"] = "discret"
    valeurs["usine.taux_utilisation_dri"] = 1.0
    with pytest.raises(ValueError, match="mode discret"):
        cfg.SimConfig.from_scenario(cfg.Scenario(name="x", description="", values=valeurs))
