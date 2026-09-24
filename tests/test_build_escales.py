"""Tests de la construction des escales (étape 3).

Deux sources, clairement séparées :

- la fixture PDF réelle, qui ne contient qu'**un seul** instantané : elle ne peut donc
  tester que l'escale non terminée et la censure à gauche. `test_fixtures_reelles_*`
  balaie `tests/fixtures/*.pdf` et se renforcera tout seul dès que plusieurs
  instantanés successifs y seront déposés ;
- des séquences **synthétiques** construites ici en Python, seule façon de tester une
  transition ou un trou de collecte tant que la série réelle manque. Les navires,
  pavillons et cargaisons sont ceux de la fixture ; seules les dates sont fabriquées.

Aucun accès réseau, aucun PDF écrit.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from corridor.transform import build_escales as bx
from corridor.transform.parse_status import (
    VesselObservation,
    categorize_cargo,
    parse_tonnage,
)

FIXTURES = Path(__file__).parent / "fixtures"
REAL_PDFS = sorted(FIXTURES.glob("port_status_*.pdf"))

# Cargaisons réelles, reprises telles quelles de la fixture.
ORE = "140571 MT IRON ORE"
ORE_AUTRE = "112000 MT IRON ORE"  # même libellé, tonnage franchement différent
DIVERS = "36524.53 MT DIVERS"

# Série synthétique : trois collectes par jour, soit un instantané toutes les 8 h.
T0 = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)


def T(index: int, hours: float = 0) -> datetime:
    """Heure du port pour l'instantané n° `index`, décalée de `hours` heures."""
    return T0 + timedelta(hours=8 * index + hours)


def _obs(
    snapshot: datetime,
    status: str,
    vessel: str,
    flag: str,
    *,
    dock: str | None = None,
    event_time: datetime | None = None,
    cargo: str = DIVERS,
    situation: str | None = None,
    long_stay: bool = False,
) -> VesselObservation:
    """Fabrique une observation comme le ferait `parse_pdf` sur un vrai PDF."""
    tonnage_t, cargo_label = parse_tonnage(cargo)
    return VesselObservation(
        source_time_utc=snapshot,
        fetched_at_utc=snapshot + timedelta(seconds=25),
        status=status,
        dock=dock,
        vessel=vessel,
        flag=flag,
        shiptype="VRAQUIER",
        cargo_raw=cargo,
        cargo_label=cargo_label,
        cargo_category=categorize_cargo(cargo),
        tonnage_t=tonnage_t,
        last_port="CASEI",
        agent="GEMA",
        event_time=event_time if event_time is not None else snapshot,
        situation=situation,
        long_stay=long_stay,
    )


def _witness(snapshot: datetime) -> VesselObservation:
    """Navire témoin, présent à chaque instantané.

    Un instantané n'existe que s'il contient au moins un navire : sans témoin, un
    instantané où le navire étudié est absent serait invisible et son départ ne
    pourrait pas être borné. Le port de Djen Djen n'est jamais vide.
    """
    return _obs(snapshot, "berthed", "CATHY OCEAN", "BAHAMAS", dock="QM", event_time=T(0, -18))


def _with_witness(n_snapshots: int, *observations: VesselObservation) -> list[VesselObservation]:
    return [_witness(T(i)) for i in range(n_snapshots)] + list(observations)


def _one(escales, vessel: str):
    found = [e for e in escales if e.vessel == vessel]
    assert len(found) == 1, f"{len(found)} escales pour {vessel}, une seule attendue"
    return found[0]


def _all(escales, vessel: str):
    return [e for e in escales if e.vessel == vessel]


# --- helpers purs ----------------------------------------------------------


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("BERGE NIMBA", "BERGE NIMBA"),
        ("berge  nimba", "BERGE NIMBA"),
        ("  Berge\tNimba ", "BERGE NIMBA"),
        ("", ""),
    ],
)
def test_normalize_name(brut, attendu):
    assert bx.normalize_name(brut) == attendu


def test_vessel_key_insensible_a_la_casse_et_aux_espaces():
    assert bx.vessel_key("berge  nimba", " Liberia ") == ("BERGE NIMBA", "LIBERIA")


def test_hours_between():
    assert bx.hours_between(T(0), T(0, 20)) == pytest.approx(20.0)
    assert bx.hours_between(T(0, 20), T(0)) == pytest.approx(-20.0)
    assert bx.hours_between(None, T(0)) is None
    assert bx.hours_between(T(0), None) is None


def test_group_snapshots_ordonne_et_regroupe():
    desordre = [_witness(T(2)), _witness(T(0)), _witness(T(1))]
    groupes = bx.group_snapshots(desordre)
    assert [t for t, _ in groupes] == [T(0), T(1), T(2)]
    assert all(list(vessels) == [("CATHY OCEAN", "BAHAMAS")] for _, vessels in groupes)


def test_aucune_observation_donne_aucune_escale():
    assert bx.build_escales([]) == []


# --- cycle de vie : une transition par test --------------------------------


def test_cycle_complet_expected_anchorage_berthed_absent():
    eta, anchored, berthed_at = T(3), T(0, 7), T(0, 15)
    escales = bx.build_escales(
        _with_witness(
            4,
            _obs(T(0), "expected", "BERGE NIMBA", "LIBERIA", event_time=eta, cargo=ORE),
            _obs(
                T(1),
                "anchorage",
                "BERGE NIMBA",
                "LIBERIA",
                event_time=anchored,
                cargo=ORE,
                situation="Waiting for berth",
            ),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=berthed_at, cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.anomaly == ""  # cycle nominal : rien à signaler
    assert nimba.flag == "LIBERIA"
    assert nimba.cargo_category == "iron_ore"
    assert nimba.cargo_label == "IRON ORE"
    assert nimba.tonnage_t == pytest.approx(140571.0)
    assert nimba.dock == "QW/7"
    assert nimba.eta == eta
    assert nimba.anchorage_time == anchored
    assert nimba.berth_time == berthed_at
    assert nimba.n_snapshots == 3
    # présent au 3e instantané, absent au 4e : le départ est encadré, pas daté
    assert nimba.departure_min == T(2)
    assert nimba.departure_max == T(3)
    assert nimba.finished is True
    assert nimba.departure_uncertainty_hours == pytest.approx(8.0)
    assert nimba.wait_hours == pytest.approx(8.0)  # 7 h → 15 h, heures du port
    assert nimba.berth_hours == pytest.approx(1.0)  # 15 h → 16 h (departure_min)
    assert nimba.long_stay is False


def test_retour_a_quai_vers_rade_signale_et_conserve():
    escales = bx.build_escales(
        _with_witness(
            4,
            _obs(T(1), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=T(0, 7), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(3), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=T(0, 7), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.anomaly == "back_to_anchorage"  # conservé, jamais corrigé
    assert nimba.berth_time == T(0, 15)  # le passage à quai reste dans l'escale
    assert nimba.n_snapshots == 3


def test_retour_vers_expected_signale():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=T(0, 7), cargo=ORE),
            _obs(T(2), "expected", "BERGE NIMBA", "LIBERIA", event_time=T(5), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.anomaly == "back_to_expected"
    assert nimba.eta == T(5)


def test_apparition_directe_a_quai_signalee():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 5), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.anomaly == "direct_berth"
    assert nimba.anchorage_time is None
    assert nimba.wait_hours is None  # aucune attente observable
    assert nimba.berth_time == T(0, 5)
    assert nimba.berth_hours == pytest.approx(3.0)
    assert nimba.departure_max == T(2)


def test_depart_sans_accostage_signale():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=T(0, 7), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.anomaly == "departed_without_berth"
    assert nimba.berth_time is None
    assert nimba.berth_hours is None
    assert nimba.wait_hours is None
    assert nimba.finished is True


# --- identité du navire et réapparitions -----------------------------------


def test_identite_normalisee_regroupe_les_variantes():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(0), "expected", "BERGE NIMBA", "LIBERIA", event_time=T(2), cargo=ORE),
            _obs(T(1), "anchorage", "berge  nimba", " Liberia ", event_time=T(1), cargo=ORE),
            _obs(T(2), "berthed", "Berge Nimba", "liberia", dock="QW/7",
                 event_time=T(2), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")  # une seule escale malgré trois graphies
    assert nimba.n_snapshots == 3
    assert nimba.flag == "LIBERIA"


def test_reapparition_fusionnee_si_cargaison_identique():
    # absent du seul instantané n° 3 : trou de publication, pas un départ
    escales = bx.build_escales(
        _with_witness(
            6,
            _obs(T(1), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=T(0, 7), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 10), cargo=ORE),
            _obs(T(4), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 10), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.anomaly == "presence_gap,reappeared"
    assert nimba.n_snapshots == 3  # les trois présences, le trou ne compte pas
    assert nimba.departure_min == T(4)
    assert nimba.departure_max == T(5)
    assert nimba.wait_hours == pytest.approx(3.0)  # l'attente traverse le trou


def test_reapparition_scindee_si_tonnage_different():
    escales = bx.build_escales(
        _with_witness(
            5,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(3), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(3), cargo=ORE_AUTRE),
        )
    )
    premiere, seconde = _all(escales, "BERGE NIMBA")
    assert len(_all(escales, "BERGE NIMBA")) == 2
    assert premiere.departure_min == T(1)
    assert premiere.departure_max == T(2)  # premier instantané sans le navire
    assert premiere.tonnage_t == pytest.approx(140571.0)
    assert seconde.tonnage_t == pytest.approx(112000.0)
    assert seconde.call_id != premiere.call_id


def test_reapparition_scindee_si_absence_trop_longue():
    # cargaison identique, mais 32 h entre les deux présences : nouvelle escale
    escales = bx.build_escales(
        _with_witness(
            6,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(5), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(5), cargo=ORE),
        )
    )
    assert len(_all(escales, "BERGE NIMBA")) == 2


def test_reapparition_scindee_si_le_cycle_recule():
    # à quai, absent, puis annoncé de nouveau : c'est une seconde escale
    escales = bx.build_escales(
        _with_witness(
            4,
            _obs(T(0), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, -5), cargo=ORE),
            _obs(T(2), "expected", "BERGE NIMBA", "LIBERIA", event_time=T(4), cargo=ORE),
        )
    )
    premiere, seconde = _all(escales, "BERGE NIMBA")
    assert premiere.berth_time == T(0, -5)
    assert seconde.eta == T(4)
    assert "back_to_expected" not in seconde.anomaly  # deux escales, pas une régression


def test_doublon_dans_un_instantane_signale():
    doublon = _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QDZ/1", cargo=ORE)
    escales = bx.build_escales(
        _with_witness(
            2,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7", cargo=ORE),
            doublon,
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert "duplicate_in_snapshot" in nimba.anomaly
    assert nimba.dock == "QW/7"  # la première ligne est retenue
    assert nimba.n_snapshots == 1


# --- trou de collecte ------------------------------------------------------


def test_trou_de_collecte_elargit_l_incertitude_sur_le_depart():
    # collectes à 16:00 et 00:00, puis rien pendant 20 h : reprise à 20:00
    g0, g1, g2 = T0, T0 + timedelta(hours=8), T0 + timedelta(hours=28)
    escales = bx.build_escales(
        [_witness(g) for g in (g0, g1, g2)]
        + [
            _obs(g0, "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, -2), cargo=ORE),
            _obs(g1, "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, -2), cargo=ORE),
        ]
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.departure_min == g1
    assert nimba.departure_max == g2
    # l'incertitude vaut exactement la durée du trou
    assert nimba.departure_uncertainty_hours == pytest.approx(20.0)
    assert nimba.berth_hours == pytest.approx(10.0)  # borne basse : 14:00 → 00:00


# --- durées, calculées sur les heures du port ------------------------------


def test_durees_calculees_sur_event_time_pas_sur_les_collectes():
    # les heures d'événement sont volontairement loin des heures de collecte :
    # un calcul basé sur les instantanés donnerait 8 h d'attente au lieu de 40 h
    anchored, berthed_at = T(0, -30), T(0, 10)
    escales = bx.build_escales(
        _with_witness(
            4,
            _obs(T(1), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=anchored, cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=berthed_at, cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.wait_hours == pytest.approx(40.0)
    assert nimba.berth_hours == pytest.approx(6.0)  # 10 h → 16 h (departure_min)
    assert bx.hours_between(nimba.anchorage_time, nimba.berth_time) == pytest.approx(40.0)


def test_duree_negative_conservee_et_signalee():
    # le port annonce un accostage antérieur au mouillage : incohérence gardée telle quelle
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "anchorage", "BERGE NIMBA", "LIBERIA", event_time=T(0, 20), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 10), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert "negative_duration" in nimba.anomaly
    assert nimba.wait_hours == pytest.approx(-10.0)  # non corrigé


# --- révisions en cours d'escale ------------------------------------------


def test_eta_revisee_signalee_premiere_valeur_gardee():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "expected", "BERGE NIMBA", "LIBERIA", event_time=T(3), cargo=ORE),
            _obs(T(2), "expected", "BERGE NIMBA", "LIBERIA", event_time=T(6), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert "eta_revised" in nimba.anomaly
    assert nimba.eta == T(3)  # l'annonce initiale, pas la révision


def test_ripage_de_poste_signale():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/1",
                 event_time=T(0, 15), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert "dock_changed" in nimba.anomaly
    assert nimba.dock == "QW/7"  # poste d'accostage initial


def test_cargaison_modifiee_en_cours_d_escale_signalee():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=DIVERS),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert "cargo_changed" in nimba.anomaly
    assert nimba.cargo_label == "IRON ORE"
    assert nimba.cargo_category == "iron_ore"


def test_heure_d_evenement_corrigee_signalee():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 16), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert "event_time_changed" in nimba.anomaly
    assert nimba.berth_time == T(0, 15)


# --- escales non terminées et exclusion des statistiques -------------------


def test_escale_non_terminee_conservee_mais_hors_statistiques():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
            _obs(T(2), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 15), cargo=ORE),
        )
    )
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.departure_max is None  # toujours là au dernier instantané
    assert nimba.finished is False
    assert nimba.departure_uncertainty_hours is None
    assert nimba.berth_hours == pytest.approx(1.0)  # durée observée à ce jour
    assert nimba in escales  # conservée dans la table
    assert nimba not in bx.usable_escales(escales)  # mais hors statistiques


def test_long_sejour_exclu_des_statistiques():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(0), "berthed", "EAGLE", "MONGOLIE", dock="RR/1",
                 event_time=T0 - timedelta(days=212), cargo=ORE, long_stay=True),
            _obs(T(1), "berthed", "EAGLE", "MONGOLIE", dock="RR/1",
                 event_time=T0 - timedelta(days=212), cargo=ORE, long_stay=True),
        )
    )
    eagle = _one(escales, "EAGLE")
    assert eagle.long_stay is True
    assert eagle.finished is True  # absent au 3e instantané : escale terminée
    assert eagle in escales
    assert eagle not in bx.usable_escales(escales)
    # le témoin, lui, reste dans la table mais n'est pas terminé
    assert bx.usable_escales(escales) == []


def test_usable_escales_garde_les_escales_completes():
    escales = bx.build_escales(
        _with_witness(
            3,
            _obs(T(0), "expected", "BERGE NIMBA", "LIBERIA", event_time=T(1), cargo=ORE),
            _obs(T(1), "berthed", "BERGE NIMBA", "LIBERIA", dock="QW/7",
                 event_time=T(0, 9), cargo=ORE),
        )
    )
    assert [e.vessel for e in bx.usable_escales(escales)] == ["BERGE NIMBA"]


# --- call_id ---------------------------------------------------------------


def test_call_id_lisible_et_deterministe():
    obs = _with_witness(
        2,
        _obs(T(0), "berthed", "XIN YANG CAI FU", "CHINE", dock="GC/2", cargo=DIVERS),
    )
    premier = bx.build_escales(obs)
    second = bx.build_escales(list(reversed(obs)))  # l'ordre d'entrée ne compte pas
    assert _one(premier, "XIN YANG CAI FU").call_id == "XIN-YANG-CAI-FU-CHINE-20260924T1600Z"
    assert [e.call_id for e in premier] == [e.call_id for e in second]


# --- fixtures réelles ------------------------------------------------------


def test_le_jeu_de_fixtures_reelles_est_present():
    assert REAL_PDFS, "aucun PDF dans tests/fixtures/"


def test_fixtures_reelles_invariants():
    escales = bx.build_from_pdfs(REAL_PDFS)
    assert escales
    for e in escales:
        assert e.n_snapshots >= 1
        assert set(e.anomaly.split(",")) <= set(bx.ANOMALY_CODES) | {""}
        assert e.departure_min is not None
        if e.departure_max is not None:
            assert e.departure_max > e.departure_min
            assert e.departure_uncertainty_hours > 0
        if e.berth_time is not None and e.anchorage_time is not None:
            assert e.wait_hours == pytest.approx(
                bx.hours_between(e.anchorage_time, e.berth_time)
            )
        assert (e.wait_hours is None) == (e.anchorage_time is None or e.berth_time is None)


def test_fixture_unique_ne_donne_que_des_escales_en_cours():
    if len(REAL_PDFS) > 1:
        pytest.skip("plusieurs instantanés réels disponibles : voir les tests de séquence")
    escales = bx.build_from_pdfs(REAL_PDFS)
    # 12 navires à quai + 2 en rade au premier et seul instantané
    assert len(escales) == 14
    assert all(e.n_snapshots == 1 for e in escales)
    # aucun départ observable : toutes les escales sont en cours
    assert all(e.departure_max is None for e in escales)
    assert all(not e.finished for e in escales)
    assert bx.usable_escales(escales) == []
    # arrivée jamais observée : censure à gauche, pas une transition anormale
    assert all("left_censored" in e.anomaly for e in escales)
    assert all("direct_berth" not in e.anomaly for e in escales)
    # l'attente n'est pas calculable : les navires à quai n'ont pas d'heure de mouillage
    assert all(e.wait_hours is None for e in escales)
    eagle = _one(escales, "EAGLE")
    assert eagle.long_stay is True
    assert eagle.berth_hours > 200 * 24  # sept mois à quai, à exclure des moyennes
    nimba = _one(escales, "BERGE NIMBA")
    assert nimba.cargo_category == "iron_ore"
    assert nimba.berth_time is None
    assert nimba.anchorage_time == datetime(2026, 9, 17, 6, 0, tzinfo=UTC)
