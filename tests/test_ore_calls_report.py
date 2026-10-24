"""Tests de scripts/ore_calls_report.py : cadences, arrivées, intervalles, hors ligne."""

import importlib.util
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from corridor.transform.build_escales import Escale

_SPEC = importlib.util.spec_from_file_location(
    "ore_calls_report", Path(__file__).parent.parent / "scripts" / "ore_calls_report.py"
)
ocr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ocr)


def _t(jour, heure=0):
    return datetime(2026, 9, jour, heure, tzinfo=UTC)


def _escale(**ecarts):
    """Escale minerai terminée : 24 000 t déchargées en 24 h, fenêtre de départ nulle."""
    base = Escale(
        call_id="x",
        vessel="NAVIRE",
        flag="FR",
        cargo_category="iron_ore",
        cargo_label="IRON ORE",
        tonnage_t=24000.0,
        dock="QW/7",
        eta=None,
        anchorage_time=None,
        berth_time=_t(10),
        departure_min=_t(11),
        departure_max=_t(11),
        wait_hours=None,
        berth_hours=24.0,
        n_snapshots=3,
        long_stay=False,
        anomaly="",
    )
    return replace(base, **ecarts)


def test_cadence_sans_incertitude_de_depart():
    basse, haute = ocr.cadence_bounds(_escale())
    assert basse == haute == 24000.0


def test_la_fenetre_de_depart_abaisse_la_borne_basse():
    """24 h de séjour observé + 24 h de fenêtre : la cadence peut valoir deux fois moins."""
    basse, haute = ocr.cadence_bounds(_escale(departure_max=_t(12)))
    assert haute == 24000.0
    assert basse == 12000.0
    assert basse < haute


def test_escale_en_cours_sans_cadence():
    assert ocr.cadence_bounds(_escale(departure_max=None)) == (None, None)


def test_escale_sans_tonnage_ni_sejour_sans_cadence():
    assert ocr.cadence_bounds(_escale(tonnage_t=None)) == (None, None)
    assert ocr.cadence_bounds(_escale(berth_hours=None)) == (None, None)
    assert ocr.cadence_bounds(_escale(berth_hours=0.0)) == (None, None)


def test_arrivee_prefere_la_rade_au_quai():
    escale = _escale(anchorage_time=_t(8), berth_time=_t(10))
    assert ocr.arrival_time(escale) == _t(8)
    assert ocr.arrival_source(escale) == "rade"


def test_arrivee_se_rabat_sur_le_quai():
    assert ocr.arrival_time(_escale()) == _t(10)
    assert ocr.arrival_source(_escale()) == "quai"


def test_arrivee_absente_pour_un_navire_seulement_annonce():
    annonce = _escale(berth_time=None, eta=_t(20))
    assert ocr.arrival_time(annonce) is None
    assert ocr.arrival_source(annonce) == "aucune"


def test_selection_filtre_la_categorie_et_trie_par_arrivee():
    tard = _escale(vessel="TARD", berth_time=_t(20), departure_min=_t(21), departure_max=_t(21))
    tot = _escale(vessel="TOT", berth_time=_t(10))
    autre = _escale(vessel="BLE", cargo_category="grain")
    retenues = ocr.select_escales([tard, autre, tot])
    assert [e.vessel for e in retenues] == ["TOT", "TARD"]


def test_une_escale_sans_arrivee_passe_en_dernier():
    annonce = _escale(vessel="ANNONCE", berth_time=None, eta=_t(1))
    quai = _escale(vessel="QUAI")
    assert [e.vessel for e in ocr.select_escales([annonce, quai])] == ["QUAI", "ANNONCE"]


def test_intervalles_entre_arrivees_successives():
    lignes = ocr.report_rows(
        ocr.select_escales([
            _escale(vessel="A", berth_time=_t(10)),
            _escale(vessel="B", berth_time=_t(12), departure_min=_t(13), departure_max=_t(13)),
        ])
    )
    assert lignes[0]["intervalle_h"] is None
    assert lignes[1]["intervalle_h"] == 48.0


def test_une_escale_sans_arrivee_ne_coupe_pas_la_chaine():
    """Le navire seulement annoncé n'a pas d'intervalle, et n'en vole pas un aux autres."""
    annonce = _escale(vessel="ANNONCE", berth_time=None, eta=_t(1))
    lignes = ocr.report_rows([
        _escale(vessel="A", berth_time=_t(10)),
        annonce,
        _escale(vessel="B", berth_time=_t(12), departure_min=_t(13), departure_max=_t(13)),
    ])
    assert [x["intervalle_h"] for x in lignes] == [None, None, 48.0]


def test_synthese_moyenne_les_centres_et_enveloppe_les_bornes():
    lignes = ocr.report_rows([
        _escale(vessel="A"),                                 # 24 000 exactement
        _escale(vessel="B", departure_max=_t(12)),           # 12 000 à 24 000
    ])
    synthese = ocr.cadence_summary(lignes)
    assert synthese["n"] == 2
    assert synthese["centres"] == [24000.0, 18000.0]
    assert synthese["moyenne"] == 21000.0
    assert synthese["minimum"] == 12000.0
    assert synthese["maximum"] == 24000.0


def test_synthese_sans_escale_mesurable():
    synthese = ocr.cadence_summary(ocr.report_rows([_escale(departure_max=None)]))
    assert synthese == {"n": 0, "moyenne": None, "minimum": None, "maximum": None, "centres": []}


def test_affichage_ne_leve_pas_sur_une_escale_vide_de_mesures(capsys):
    ocr.afficher(ocr.report_rows([_escale(berth_time=None, departure_max=None, eta=_t(20))]))
    sortie = capsys.readouterr().out
    assert "EN COURS" in sortie
    assert "aucune escale terminée exploitable" in sortie


def test_main_sans_observations(tmp_path, capsys):
    assert ocr.main(["--observations", str(tmp_path / "absent.csv")]) == 1
    assert "observations absentes" in capsys.readouterr().out


# --- signal observé de H2 : attente à poste libre ---------------------------


def _amarre(vessel, arrivee_jour, depart_jour, depart_max_jour=None, dock="QW/7"):
    """Escale à quai, du jour d'accostage au jour de départ (bornes en jours de septembre)."""
    return _escale(
        vessel=vessel, dock=dock, anchorage_time=None, berth_time=_t(arrivee_jour),
        departure_min=_t(depart_jour),
        departure_max=_t(depart_max_jour if depart_max_jour is not None else depart_jour),
    )


def _attend(vessel, rade_jour, accostage_jour, dock="QW/7"):
    """Escale qui attend en rade puis accoste."""
    return _escale(
        vessel=vessel, dock=dock, anchorage_time=_t(rade_jour),
        berth_time=_t(accostage_jour), departure_min=_t(accostage_jour + 2),
        departure_max=_t(accostage_jour + 2),
    )


def test_sans_attente_mesurable_il_n_y_a_pas_de_signal():
    """Il faut une entrée en rade ET un accostage : sinon rien n'est décomposable."""
    assert ocr.free_berth_wait(_escale(), [_escale()]) is None


def test_une_attente_a_poste_occupe_ne_donne_aucun_signal():
    """Le poste est pris tout le temps de l'attente : rien à expliquer, H2 ne joue pas."""
    occupant = _amarre("OCCUPANT", 8, 12)
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [occupant, attend])
    assert mesure["berth_busy_h"] == pytest.approx(96.0)
    assert mesure["berth_free_h"] == pytest.approx(0.0)


def test_une_attente_a_poste_vide_est_le_signal_cherche():
    """Personne au quai pendant l'attente : chaque heure est une heure que H2 explique."""
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [attend], debut_serie=_t(1))
    assert mesure["berth_busy_h"] == 0.0
    assert mesure["free_certain_h"] == pytest.approx(96.0)


def test_un_navire_d_une_autre_cargaison_occupe_aussi_le_poste():
    """QW/7 reçoit aussi du non-minerai : l'ignorer gonflerait le signal."""
    divers = replace(_amarre("DIVERS", 8, 10), cargo_category="general")
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [divers, attend], debut_serie=_t(1))
    assert mesure["berth_busy_h"] == pytest.approx(48.0)
    assert mesure["free_certain_h"] == pytest.approx(48.0)


def test_un_navire_a_un_autre_poste_n_occupe_rien():
    ailleurs = _amarre("AILLEURS", 8, 12, dock="QW/3")
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [ailleurs, attend], debut_serie=_t(1))
    assert mesure["berth_busy_h"] == 0.0


def test_deux_occupants_qui_se_chevauchent_ne_comptent_qu_une_fois():
    """Sinon le temps occupé dépasserait l'attente et le signal deviendrait négatif."""
    a = _amarre("A", 8, 11)
    b = _amarre("B", 10, 12)
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [a, b, attend], debut_serie=_t(1))
    assert mesure["berth_busy_h"] == pytest.approx(96.0)
    assert mesure["berth_free_h"] == pytest.approx(0.0)


def test_l_attente_avant_le_premier_instantane_est_inconnue_pas_libre():
    """Le piège principal : un navire a pu occuper le quai sans qu'on le voie jamais."""
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [attend], debut_serie=_t(10))
    assert mesure["free_unobserved_h"] == pytest.approx(48.0)
    assert mesure["free_observed_h"] == pytest.approx(48.0)


def test_sans_debut_de_serie_rien_n_est_declare_inconnu():
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [attend])
    assert mesure["free_unobserved_h"] == 0.0


def test_le_libre_qui_tombe_dans_une_fenetre_de_depart_est_douteux():
    """Le cas réel de la série : le navire précédent y était peut-être encore."""
    occupant = _amarre("OCCUPANT", 8, 10, depart_max_jour=12)
    attend = _attend("ATTEND", 8, 12)
    mesure = ocr.free_berth_wait(attend, [occupant, attend], debut_serie=_t(1))
    assert mesure["free_observed_h"] == pytest.approx(48.0)
    assert mesure["free_in_departure_window_h"] == pytest.approx(48.0)
    assert mesure["free_certain_h"] == pytest.approx(0.0)


def test_les_quatre_parts_somment_a_l_attente_totale():
    occupant = _amarre("OCCUPANT", 8, 10, depart_max_jour=11)
    attend = _attend("ATTEND", 6, 12)
    m = ocr.free_berth_wait(attend, [occupant, attend], debut_serie=_t(7))
    somme = (m["berth_busy_h"] + m["free_unobserved_h"]
             + m["free_in_departure_window_h"] + m["free_certain_h"])
    assert somme == pytest.approx(m["wait_total_h"])


def test_l_union_des_heures_fusionne_les_chevauchements():
    assert ocr._union_heures([(_t(1), _t(3)), (_t(2), _t(4))]) == pytest.approx(72.0)
    assert ocr._union_heures([(_t(1), _t(2)), (_t(3), _t(4))]) == pytest.approx(48.0)
    assert ocr._union_heures([]) == 0.0


def test_le_signal_s_affiche_sans_lever(capsys):
    occupant = _amarre("OCCUPANT", 8, 10, depart_max_jour=11)
    attend = _attend("ATTEND", 6, 12)
    lignes = ocr.report_rows([attend], toutes=[occupant, attend], debut_serie=_t(7))
    ocr.afficher_signal_h2(lignes, _t(7))
    sortie = capsys.readouterr().out
    assert "Signal observé de H2" in sortie
    assert "poste LIBRE" in sortie


def test_le_signal_signale_son_absence(capsys):
    """Sans attente mesurable, dire qu'il n'y a rien plutôt que d'afficher des zéros."""
    ocr.afficher_signal_h2(ocr.report_rows([_escale()]))
    assert "aucune attente en rade mesurable" in capsys.readouterr().out
