import csv
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from corridor.ingest import port_status as ps

PDF_A = b"%PDF-1.4 contenu A"
PDF_B = b"%PDF-1.4 contenu B"
T1 = datetime(2026, 9, 28, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
T3 = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "port_status_20260924T155825Z.pdf"


def contenu_egal_octets(content, fetched_at):
    """Doublure d'empreinte : ici, deux PDF ont le même contenu s'ils ont les mêmes octets.

    Les vrais PDF diffèrent toujours par leur en-tête horodaté ; cette doublure permet de
    tester la logique de statut sans dépendre du parseur, avec des octets factices.
    """
    return hashlib.sha256(content).hexdigest()


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
    row = ps.save_snapshot(PDF_A, T1, tmp_path, fingerprint=contenu_egal_octets)
    assert row["status"] == "new"
    assert row["path"] == "2026/09/28/port_status_20260928T050000Z.pdf"
    assert (tmp_path / row["path"]).read_bytes() == PDF_A
    assert row["content_sha256"] == contenu_egal_octets(PDF_A, T1)
    assert read_manifest(tmp_path)[0]["release_asset"] == ""  # vide tant que non publié


def test_contenu_identique_statut_inchange_mais_pdf_archive(tmp_path):
    """Changement de contrat : même situation → `unchanged`, mais le PDF est gardé.

    Son heure de génération est nouvelle, et c'est elle qui atteste que les navires
    étaient encore listés : sans ce PDF, observations.csv ne serait plus recalculable.
    """
    ps.save_snapshot(PDF_A, T1, tmp_path, fingerprint=contenu_egal_octets)
    row = ps.save_snapshot(PDF_A, T2, tmp_path, fingerprint=contenu_egal_octets)
    assert row["status"] == "unchanged"
    assert len(list(tmp_path.rglob("*.pdf"))) == 2
    assert [r["status"] for r in read_manifest(tmp_path)] == ["new", "unchanged"]


def test_instantane_modifie_archive(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path, fingerprint=contenu_egal_octets)
    row = ps.save_snapshot(PDF_B, T2, tmp_path, fingerprint=contenu_egal_octets)
    assert row["status"] == "new"
    assert len(list(tmp_path.rglob("*.pdf"))) == 2


def test_ligne_erreur_ignoree_pour_la_deduplication(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path, fingerprint=contenu_egal_octets)
    ps.append_manifest(
        tmp_path / ps.MANIFEST_NAME,
        {"fetched_at_utc": "2026-09-28T13:00:00Z", "status": "error", "error": "timeout"},
    )
    row = ps.save_snapshot(PDF_A, T3, tmp_path, fingerprint=contenu_egal_octets)
    assert row["status"] == "unchanged"


def test_le_statut_depend_du_contenu_pas_du_fichier(tmp_path):
    """Deux fichiers différents octet par octet, même situation : inchangé."""
    def meme_situation(content, fetched_at):
        return "situation-identique"

    ps.save_snapshot(PDF_A, T1, tmp_path, fingerprint=meme_situation)
    row = ps.save_snapshot(PDF_B, T2, tmp_path, fingerprint=meme_situation)
    assert row["status"] == "unchanged"
    lignes = read_manifest(tmp_path)
    assert lignes[0]["sha256"] != lignes[1]["sha256"]  # les fichiers diffèrent bien


def test_empreinte_de_contenu_d_un_vrai_pdf():
    empreinte = ps.pdf_content_sha256(FIXTURE.read_bytes(), T1)
    assert empreinte is not None and len(empreinte) == 64
    # l'indépendance vis-à-vis des heures est testée dans test_parse_status.py


def test_pdf_illisible_archive_quand_meme(tmp_path):
    # empreinte par défaut : le parseur ne lit pas ces octets factices
    row = ps.save_snapshot(PDF_A, T1, tmp_path)
    assert row["status"] == "new"
    assert row["content_sha256"] == ""
    assert "illisible" in row["error"]
    assert (tmp_path / row["path"]).read_bytes() == PDF_A  # on ne jette jamais le brut


def test_un_pdf_illisible_ne_passe_jamais_pour_inchange(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path)
    assert ps.save_snapshot(PDF_A, T2, tmp_path)["status"] == "new"


# --- manifeste -------------------------------------------------------------


def test_manifeste_d_un_ancien_schema_refuse(tmp_path):
    ancien = tmp_path / ps.MANIFEST_NAME
    ancien.write_text("fetched_at_utc,status,sha256,size_bytes,path,error\n", encoding="utf-8")
    with pytest.raises(ps.ManifestSchemaError, match="Migrer"):
        ps.append_manifest(ancien, {"fetched_at_utc": "x", "status": "error"})


def test_write_manifest_reecrit_et_se_relit(tmp_path):
    ps.save_snapshot(PDF_A, T1, tmp_path, fingerprint=contenu_egal_octets)
    manifeste = tmp_path / ps.MANIFEST_NAME
    lignes = ps.read_manifest(manifeste)
    lignes[0]["release_asset"] = "raw-2026-09/port_status_20260928T050000Z.pdf"
    ps.write_manifest(manifeste, lignes)
    relu = ps.read_manifest(manifeste)
    assert relu[0]["release_asset"] == "raw-2026-09/port_status_20260928T050000Z.pdf"
    assert relu[0]["sha256"] == lignes[0]["sha256"]
    assert not manifeste.with_suffix(".csv.tmp").exists()  # rien de temporaire ne traîne


def test_le_manifeste_expose_les_nouvelles_colonnes():
    assert "content_sha256" in ps.MANIFEST_FIELDS
    assert "release_asset" in ps.MANIFEST_FIELDS


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
