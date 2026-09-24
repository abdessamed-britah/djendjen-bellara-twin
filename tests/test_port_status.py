import csv
from datetime import UTC, datetime

import pytest
import requests

from corridor.ingest import port_status as ps

PDF_A = b"%PDF-1.4 contenu A"
PDF_B = b"%PDF-1.4 contenu B"
T1 = datetime(2026, 9, 28, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
T3 = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


def read_manifest(root):
    with (root / ps.MANIFEST_NAME).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, headers, timeout):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_premier_instantane_archive(tmp_path):
    row = ps.save_snapshot(PDF_A, T1, tmp_path)
    assert row["status"] == "new"
    assert row["path"] == "2026/09/28/port_status_20260928T050000Z.pdf"
    assert (tmp_path / row["path"]).read_bytes() == PDF_A


def test_instantane_identique_non_duplique(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path)
    row = ps.save_snapshot(PDF_A, T2, tmp_path)
    assert row["status"] == "unchanged"
    assert len(list(tmp_path.rglob("*.pdf"))) == 1
    assert [r["status"] for r in read_manifest(tmp_path)] == ["new", "unchanged"]


def test_instantane_modifie_archive(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path)
    row = ps.save_snapshot(PDF_B, T2, tmp_path)
    assert row["status"] == "new"
    assert len(list(tmp_path.rglob("*.pdf"))) == 2


def test_ligne_erreur_ignoree_pour_la_deduplication(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path)
    ps.append_manifest(
        tmp_path / ps.MANIFEST_NAME,
        {"fetched_at_utc": "2026-09-28T13:00:00Z", "status": "error", "error": "timeout"},
    )
    assert ps.save_snapshot(PDF_A, T3, tmp_path)["status"] == "unchanged"


def test_fetch_reessaie_puis_reussit(monkeypatch):
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    session = FakeSession([
        requests.ConnectionError("hors ligne"),
        FakeResponse(200, b"<html>maintenance</html>"),  # 200 mais pas un PDF
        FakeResponse(200, PDF_A),
    ])
    assert ps.fetch_pdf(session=session, retries=3) == PDF_A
    assert session.calls == 3


def test_fetch_echoue_apres_toutes_les_tentatives(monkeypatch):
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    session = FakeSession([FakeResponse(503, b""), FakeResponse(503, b"")])
    with pytest.raises(ps.FetchError, match="HTTP 503"):
        ps.fetch_pdf(session=session, retries=2)


def test_main_consigne_l_echec(tmp_path, monkeypatch):
    def echec(*args, **kwargs):
        raise ps.FetchError("HTTP 503")

    monkeypatch.setattr(ps, "fetch_pdf", echec)
    assert ps.main(["--root", str(tmp_path)]) == 1
    rows = read_manifest(tmp_path)
    assert rows[0]["status"] == "error"
    assert "503" in rows[0]["error"]
