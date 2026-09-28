"""Tests de la table versionnée des observations (data/clean/observations.csv)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from corridor.ingest import port_status as ps
from corridor.transform import observations_csv as oc
from corridor.transform.build_escales import build_escales
from corridor.transform.parse_status import content_fingerprint
from corridor.transform.parse_status import parse_pdf as vrai_parse_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "port_status_20260924T155825Z.pdf"
FETCHED_AT = datetime(2026, 9, 24, 15, 58, 25, tzinfo=UTC)

_DEJA_LUS: dict[tuple[str, str], list] = {}


def parse_pdf(path, *args, **kwargs):
    """Le vrai parseur, mémoïsé par (nom, contenu) : ~1,2 s par lecture de PDF réel.

    Même nom et mêmes octets donnent le même résultat : le cache ne change rien à ce
    qui est testé, il évite seulement de relire dix fois la même fixture.
    """
    path = Path(path)
    cle = (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
    if cle not in _DEJA_LUS:
        _DEJA_LUS[cle] = vrai_parse_pdf(path, *args, **kwargs)
    return _DEJA_LUS[cle]


@pytest.fixture(autouse=True)
def parseur_memoise(monkeypatch):
    monkeypatch.setattr(oc, "parse_pdf", parse_pdf)


def empreinte_de_la_fixture(content, fetched_at):
    return content_fingerprint(parse_pdf(FIXTURE))


@pytest.fixture
def archive(tmp_path):
    """Une archive locale minimale : la fixture réelle, rangée et consignée au manifeste."""
    root = tmp_path / "raw"
    ps.save_snapshot(FIXTURE.read_bytes(), FETCHED_AT, root, fingerprint=empreinte_de_la_fixture)
    return root


def test_aller_retour_sans_perte(tmp_path):
    observations = parse_pdf(FIXTURE)
    chemin = tmp_path / "obs.csv"
    assert oc.append_observations(chemin, observations) == len(observations)
    assert oc.read_observations(chemin) == observations


def test_les_colonnes_suivent_vessel_observation(tmp_path):
    chemin = tmp_path / "obs.csv"
    oc.append_observations(chemin, parse_pdf(FIXTURE))
    entete = chemin.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert entete == oc.FIELDS


def test_mise_a_jour_depuis_l_archive(archive, tmp_path):
    chemin = tmp_path / "obs.csv"
    assert oc.update_from_archive(archive, chemin) == 14
    assert oc.collected_times(chemin) == {"2026-09-24T15:58:25Z"}


def test_mise_a_jour_idempotente(archive, tmp_path):
    chemin = tmp_path / "obs.csv"
    oc.update_from_archive(archive, chemin)
    assert oc.update_from_archive(archive, chemin) == 0
    assert len(oc.read_observations(chemin)) == 14


def test_une_collecte_inchangee_est_ajoutee_avec_sa_propre_heure(archive, tmp_path):
    # même contenu, collecté 8 h plus tard : même situation, nouvelle heure de présence
    plus_tard = datetime(2026, 9, 24, 23, 58, 25, tzinfo=UTC)
    row = ps.save_snapshot(
        FIXTURE.read_bytes(), plus_tard, archive, fingerprint=empreinte_de_la_fixture
    )
    assert row["status"] == "unchanged"
    chemin = tmp_path / "obs.csv"
    oc.update_from_archive(archive, chemin)
    assert oc.collected_times(chemin) == {"2026-09-24T15:58:25Z", "2026-09-24T23:58:25Z"}


def test_un_pdf_absent_ou_illisible_ne_bloque_pas_les_autres(archive, tmp_path):
    illisible = ps.save_snapshot(b"%PDF factice", datetime(2026, 9, 25, tzinfo=UTC), archive)
    assert (archive / illisible["path"]).exists()
    ps.append_manifest(
        archive / ps.MANIFEST_NAME,
        {"fetched_at_utc": "2026-09-26T00:00:00Z", "status": "new",
         "path": "2026/09/26/absent.pdf"},
    )
    chemin = tmp_path / "obs.csv"
    assert oc.update_from_archive(archive, chemin) == 14  # seule la fixture lisible compte


def test_les_lignes_d_erreur_sont_ignorees(archive, tmp_path):
    ps.append_manifest(
        archive / ps.MANIFEST_NAME,
        {"fetched_at_utc": "2026-09-25T00:00:00Z", "status": "error", "error": "HTTP 503"},
    )
    assert oc.update_from_archive(archive, tmp_path / "obs.csv") == 14


def test_rebuild_repart_de_zero(archive, tmp_path):
    chemin = tmp_path / "obs.csv"
    oc.append_observations(chemin, parse_pdf(FIXTURE))
    oc.append_observations(chemin, parse_pdf(FIXTURE))  # doublon volontaire
    assert oc.rebuild(archive, chemin) == 14
    assert len(oc.read_observations(chemin)) == 14


def test_une_table_d_un_autre_schema_est_refusee(tmp_path):
    chemin = tmp_path / "obs.csv"
    chemin.write_text("vessel,flag\n", encoding="utf-8")
    with pytest.raises(ValueError, match="colonnes"):
        oc.append_observations(chemin, parse_pdf(FIXTURE))


def test_la_table_alimente_directement_l_etape_3(archive, tmp_path):
    chemin = tmp_path / "obs.csv"
    oc.update_from_archive(archive, chemin)
    escales = build_escales(oc.read_observations(chemin))
    assert len(escales) == 14
