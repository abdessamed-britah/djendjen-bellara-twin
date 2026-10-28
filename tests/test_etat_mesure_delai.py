"""Tests de scripts/etat_mesure_delai.py : l'appariement annonce → arrivée, hors ligne."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from corridor.transform.parse_status import VesselObservation

_SPEC = importlib.util.spec_from_file_location(
    "etat_mesure_delai", Path(__file__).parent.parent / "scripts" / "etat_mesure_delai.py"
)
em = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(em)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _obs(heure, statut, evenement=None, navire="NIMBA", categorie="iron_ore"):
    t = T0 + timedelta(hours=heure)
    return VesselObservation(
        source_time_utc=t, fetched_at_utc=t, status=statut, dock=None, vessel=navire,
        flag="X", shiptype="VRAQUIER", cargo_raw="", cargo_label="", cargo_category=categorie,
        tonnage_t=None, last_port="", agent="",
        event_time=None if evenement is None else T0 + timedelta(hours=evenement),
        situation=None, long_stay=False)


def test_une_annonce_suivie_d_une_arrivee_donne_l_erreur_et_l_horizon():
    paires, compte = em.apparier([_obs(0, "expected", 50), _obs(8, "expected", 60),
                                  _obs(70, "anchorage", 62), _obs(78, "anchorage", 62)])
    (p,) = paires
    assert p.erreur_premiere_h == 12.0
    assert p.erreur_derniere_h == 2.0
    assert p.horizon_annonce_h == 50.0
    assert p.revisions == 1
    assert compte == {"annonces_sans_arrivee": 0, "arrivees_sans_annonce": 0}


def test_une_arrivee_sans_annonce_et_une_annonce_sans_arrivee_sont_comptees():
    paires, compte = em.apparier([_obs(0, "berthed", -5, navire="A"),
                                  _obs(0, "expected", 30, navire="B")])
    assert paires == []
    assert compte == {"annonces_sans_arrivee": 1, "arrivees_sans_annonce": 1}


def test_deux_escales_du_meme_navire_donnent_deux_appariements():
    serie = [_obs(0, "expected", 10), _obs(12, "berthed", 11), _obs(100, "expected", 130),
             _obs(140, "anchorage", 128)]
    paires, _ = em.apparier(serie)
    assert [p.erreur_premiere_h for p in paires] == [1.0, -2.0]


def test_le_cv_provisoire_rapporte_l_ecart_type_au_delai_moyen():
    assert em.cv_provisoire([-10.0, 10.0], 100.0) == pytest.approx(14.1421356 / 100)
    assert em.cv_provisoire([3.0], 100.0) is None


def test_le_resume_d_une_serie_vide():
    assert em.resume([]) == {"n": 0}
