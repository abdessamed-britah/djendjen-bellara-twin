"""Tests de scripts/health_check.py : bilan du manifeste, hors ligne et sans PDF."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "health_check", Path(__file__).parent.parent / "scripts" / "health_check.py"
)
hc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hc)

MAINTENANT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _ligne(heure, statut="new"):
    return {"fetched_at_utc": heure, "status": statut}


def test_taux_d_erreur_et_statuts():
    rows = [_ligne("2026-09-30T05:00:00Z"), _ligne("2026-09-30T06:00:00Z", "error"),
            _ligne("2026-09-30T07:00:00Z", "unchanged"), _ligne("2026-09-30T08:00:00Z")]
    c = hc.collection_summary(rows, MAINTENANT)
    assert c["total"] == 4
    assert c["taux_erreur"] == 0.25
    assert c["par_statut"] == {"new": 2, "error": 1, "unchanged": 1}
    assert not c["en_retard"]


def test_alerte_au_dela_de_24h():
    c = hc.collection_summary([_ligne("2026-09-29T11:00:00Z")], MAINTENANT)
    assert c["age"] == timedelta(hours=25)
    assert c["en_retard"]


def test_une_erreur_recente_ne_compte_pas_comme_collecte():
    rows = [_ligne("2026-09-28T05:00:00Z"), _ligne("2026-09-30T11:00:00Z", "error")]
    assert hc.collection_summary(rows, MAINTENANT)["en_retard"]


def test_manifeste_vide_est_en_retard():
    c = hc.collection_summary([], MAINTENANT)
    assert c["en_retard"]
    assert c["taux_erreur"] is None


def test_manifeste_absent(tmp_path):
    assert hc.read_manifest(tmp_path / "absent.csv") == []


def test_escales_sur_serie_vide():
    assert hc.escale_summary([]) == {
        "escales": 0, "terminees": 0, "exploitables": 0, "anomalies": {},
    }
