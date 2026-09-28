"""Tests de l'archivage des PDF bruts dans les releases GitHub.

Hors ligne : l'envoi (`gh`) et la réception (HTTPS) sont remplacés par des doublures,
comme le téléchargement dans test_port_status.py.
"""

import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest

from corridor.ingest import port_status as ps
from corridor.ingest import releases as rl

T1 = datetime(2026, 9, 28, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)  # un autre mois : une autre release


def octets(content, fetched_at):
    return hashlib.sha256(content).hexdigest()


class FakeUploader:
    def __init__(self, fail_on: set[str] | None = None):
        self.sent: list[tuple[str, str]] = []
        self.fail_on = fail_on or set()

    def upload(self, tag, file):
        if file.name in self.fail_on:
            raise RuntimeError("réseau indisponible")
        self.sent.append((tag, file.name))


def _collecte(root, content, when):
    return ps.save_snapshot(content, when, root, fingerprint=octets)


# --- nommage ---------------------------------------------------------------


def test_une_release_par_mois():
    assert rl.release_tag("2026-09-28T11:36:33Z") == "raw-2026-09"
    assert rl.release_tag("2026-10-01T00:00:00Z") == "raw-2026-10"


def test_reference_d_asset_aller_retour():
    ref = rl.asset_ref("raw-2026-09", "port_status_20260928T113633Z.pdf")
    assert ref == "raw-2026-09/port_status_20260928T113633Z.pdf"
    assert rl.split_asset_ref(ref) == ("raw-2026-09", "port_status_20260928T113633Z.pdf")


def test_reference_d_asset_invalide():
    with pytest.raises(ValueError, match="tag/fichier"):
        rl.split_asset_ref("sans-barre.pdf")


def test_url_publique_d_un_asset():
    assert rl.asset_url("proprio/depot", "raw-2026-09", "a.pdf") == (
        "https://github.com/proprio/depot/releases/download/raw-2026-09/a.pdf"
    )


# --- publication -----------------------------------------------------------


def test_publie_les_pdf_en_attente_et_renseigne_le_manifeste(tmp_path):
    _collecte(tmp_path, b"%PDF A", T1)
    _collecte(tmp_path, b"%PDF B", T2)
    envoi = FakeUploader()
    publies = rl.publish_pending(tmp_path, envoi)
    assert envoi.sent == [
        ("raw-2026-09", "port_status_20260928T050000Z.pdf"),
        ("raw-2026-10", "port_status_20261001T130000Z.pdf"),
    ]
    lignes = ps.read_manifest(tmp_path / ps.MANIFEST_NAME)
    assert [row["release_asset"] for row in lignes] == publies
    assert publies[0] == "raw-2026-09/port_status_20260928T050000Z.pdf"


def test_publication_idempotente(tmp_path):
    _collecte(tmp_path, b"%PDF A", T1)
    rl.publish_pending(tmp_path, FakeUploader())
    second = FakeUploader()
    assert rl.publish_pending(tmp_path, second) == []
    assert second.sent == []


def test_les_lignes_d_erreur_n_ont_rien_a_publier(tmp_path):
    ps.append_manifest(
        tmp_path / ps.MANIFEST_NAME,
        {"fetched_at_utc": "2026-09-28T05:00:00Z", "status": "error", "error": "HTTP 503"},
    )
    envoi = FakeUploader()
    assert rl.publish_pending(tmp_path, envoi) == []
    assert envoi.sent == []


def test_un_pdf_disparu_reste_en_attente(tmp_path):
    row = _collecte(tmp_path, b"%PDF A", T1)
    (tmp_path / row["path"]).unlink()
    assert rl.publish_pending(tmp_path, FakeUploader()) == []
    assert ps.read_manifest(tmp_path / ps.MANIFEST_NAME)[0]["release_asset"] == ""


def test_une_panne_en_cours_de_route_ne_perd_pas_ce_qui_est_publie(tmp_path):
    _collecte(tmp_path, b"%PDF A", T1)
    second = _collecte(tmp_path, b"%PDF B", T2)
    en_panne = FakeUploader(fail_on={second["path"].rsplit("/", 1)[-1]})
    with pytest.raises(RuntimeError):
        rl.publish_pending(tmp_path, en_panne)
    lignes = ps.read_manifest(tmp_path / ps.MANIFEST_NAME)
    assert lignes[0]["release_asset"]  # le premier envoi est consigné
    assert lignes[1]["release_asset"] == ""  # le second sera repris au prochain passage
    assert rl.publish_pending(tmp_path, FakeUploader())  # et il l'est


# --- transport gh ----------------------------------------------------------


class FakeGh:
    """Rejoue la commande gh : releases existantes, assets présents, échecs d'envoi."""

    def __init__(self, releases=None, upload_failures=0):
        self.releases: dict[str, list[str]] = releases or {}
        self.upload_failures = upload_failures
        self.calls: list[list[str]] = []

    def __call__(self, args, capture_output=False, text=False, check=False):
        self.calls.append(args)
        verbe, tag = args[2], args[3]
        if verbe == "view" and "--json" in args:
            assets = [{"name": n} for n in self.releases.get(tag, [])]
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"assets": assets}))
        if verbe == "view":
            return subprocess.CompletedProcess(args, 0 if tag in self.releases else 1)
        if verbe == "create":
            self.releases[tag] = []
            return subprocess.CompletedProcess(args, 0)
        if verbe == "upload":
            if self.upload_failures:
                self.upload_failures -= 1
                return subprocess.CompletedProcess(args, 1, stderr="HTTP 502")
            self.releases[tag].append(args[4].replace("\\", "/").rsplit("/", 1)[-1])
            return subprocess.CompletedProcess(args, 0)
        raise AssertionError(args)


def test_gh_cree_la_release_du_mois_si_absente(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"%PDF")
    gh = FakeGh()
    rl.GhUploader(run=gh, backoff_s=0).upload("raw-2026-09", fichier)
    creation = next(c for c in gh.calls if c[2] == "create")
    assert creation[3] == "raw-2026-09"
    assert "--latest=false" in creation  # une archive de données n'est pas une version
    assert gh.releases["raw-2026-09"] == ["a.pdf"]


def test_gh_ne_renvoie_pas_un_asset_deja_present(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"%PDF")
    gh = FakeGh(releases={"raw-2026-09": ["a.pdf"]})
    rl.GhUploader(run=gh, backoff_s=0).upload("raw-2026-09", fichier)
    assert not any(c[2] in ("create", "upload") for c in gh.calls)


def test_gh_reessaie_un_envoi_echoue(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"%PDF")
    gh = FakeGh(releases={"raw-2026-09": []}, upload_failures=2)
    rl.GhUploader(run=gh, retries=3, backoff_s=0).upload("raw-2026-09", fichier)
    assert sum(c[2] == "upload" for c in gh.calls) == 3
    assert gh.releases["raw-2026-09"] == ["a.pdf"]


def test_gh_abandonne_apres_toutes_les_tentatives(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"%PDF")
    gh = FakeGh(releases={"raw-2026-09": []}, upload_failures=5)
    with pytest.raises(RuntimeError, match="échec de publication"):
        rl.GhUploader(run=gh, retries=2, backoff_s=0).upload("raw-2026-09", fichier)


# --- retéléchargement ------------------------------------------------------


def test_retelecharge_les_pdf_absents_et_verifie_leur_empreinte(tmp_path):
    row = _collecte(tmp_path, b"%PDF A", T1)
    rl.publish_pending(tmp_path, FakeUploader())
    (tmp_path / row["path"]).unlink()  # comme sur un clone neuf
    demandes = []

    def telecharge(url):
        demandes.append(url)
        return b"%PDF A"

    recuperes = rl.fetch_missing(tmp_path, "proprio/depot", telecharge)
    assert recuperes == [tmp_path / row["path"]]
    assert (tmp_path / row["path"]).read_bytes() == b"%PDF A"
    assert demandes == [
        (
            "https://github.com/proprio/depot/releases/download/raw-2026-09/"
            "port_status_20260928T050000Z.pdf"
        )
    ]


def test_ne_retelecharge_pas_un_pdf_deja_present(tmp_path):
    _collecte(tmp_path, b"%PDF A", T1)
    rl.publish_pending(tmp_path, FakeUploader())
    assert rl.fetch_missing(tmp_path, "p/d", lambda url: pytest.fail("aucun appel attendu")) == []


def test_un_pdf_altere_est_refuse(tmp_path):
    row = _collecte(tmp_path, b"%PDF A", T1)
    rl.publish_pending(tmp_path, FakeUploader())
    (tmp_path / row["path"]).unlink()
    with pytest.raises(rl.IntegrityError, match="empreinte"):
        rl.fetch_missing(tmp_path, "p/d", lambda url: b"%PDF corrompu")
    assert not (tmp_path / row["path"]).exists()  # rien d'altéré n'est écrit
