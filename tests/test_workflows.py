"""Contrat du workflow de collecte : ce qu'il commite, ce qu'il publie.

Un workflow ne se teste pas en local ; on vérifie au moins qu'il ne réintroduit pas les
PDF bruts dans git et qu'il appelle bien chaque étape. L'exécution réelle, elle, se
valide par workflow_dispatch.
"""

import subprocess
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).parent.parent
SCRAPE = RACINE / ".github" / "workflows" / "scrape.yml"


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(SCRAPE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def etapes(workflow):
    return workflow["jobs"]["scrape"]["steps"]


def _script(etapes, nom_partiel):
    return next(e.get("run", "") for e in etapes if nom_partiel in e.get("name", ""))


def test_declenchable_a_la_main(workflow):
    # PyYAML lit la clé « on » comme le booléen True
    declencheurs = workflow.get("on", workflow.get(True))
    assert "workflow_dispatch" in declencheurs
    assert "schedule" in declencheurs


def test_droits_suffisants_pour_les_releases(workflow):
    assert workflow["permissions"]["contents"] == "write"
    assert "GITHUB_TOKEN" in workflow["jobs"]["scrape"]["env"]["GH_TOKEN"]


def test_chaque_etape_est_appelee_dans_l_ordre(etapes):
    commandes = [e.get("run", "") for e in etapes]
    indices = [
        next(i for i, c in enumerate(commandes) if module in c)
        for module in (
            "corridor.ingest.port_status",
            "corridor.ingest.releases publish",
            "corridor.transform.observations_csv",
            "git push",
        )
    ]
    assert indices == sorted(indices)


def test_seuls_le_manifeste_et_les_observations_sont_commites(etapes):
    script = _script(etapes, "Enregistrer")
    ajouts = [ligne.strip() for ligne in script.splitlines() if ligne.strip().startswith("git add")]
    assert ajouts == ["git add data/raw/port_status/_manifest.csv data/clean/observations.csv"]


def test_les_etapes_aval_tournent_meme_apres_un_echec(etapes):
    for nom in ("Publier", "Ajouter les observations", "Enregistrer"):
        etape = next(e for e in etapes if nom in e.get("name", ""))
        assert etape.get("if") == "always()", nom


def test_un_artefact_de_secours_conserve_les_pdf(etapes):
    secours = next(e for e in etapes if "artefact" in e.get("name", "").lower())
    assert secours["uses"].startswith("actions/upload-artifact@")
    assert secours["if"] == "always()"


@pytest.mark.parametrize(
    ("chemin", "ignore"),
    [
        ("data/raw/port_status/2026/09/28/port_status_20260928T113633Z.pdf", True),
        ("data/raw/port_status/_manifest.csv", False),
        ("data/clean/observations.csv", False),
        ("tests/fixtures/port_status_20260924T155825Z.pdf", False),
    ],
)
def test_gitignore_exclut_les_pdf_bruts_mais_pas_les_fixtures(chemin, ignore):
    try:
        resultat = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", chemin], cwd=RACINE, check=False
        )
    except FileNotFoundError:
        pytest.skip("git absent")
    assert (resultat.returncode == 0) is ignore
