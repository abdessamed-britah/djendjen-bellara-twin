"""Archive durable des PDF bruts dans les releases GitHub, une release par mois.

Les PDF ne sont plus versionnés dans git : à 3 collectes par jour et ~580 Ko par PDF, le
dépôt aurait grossi de ~600 Mo par an. Ils vont dans une release `raw-AAAA-MM`, créée à
la première collecte du mois, et le manifeste garde la trace de chacun dans la colonne
`release_asset` (« raw-2026-09/port_status_20260928T113633Z.pdf »).

Deux sens :

- `publish_pending` (dans le workflow) : envoie chaque PDF archivé localement dont
  `release_asset` est vide, puis renseigne la colonne. Idempotent : relancé, il ne
  republie rien ; interrompu, il reprend là où il s'est arrêté, car le manifeste est
  réécrit après chaque envoi réussi.
- `fetch_missing` (en local, via `scripts/fetch_raw.py`) : retélécharge les PDF absents
  du disque et vérifie leur empreinte contre le manifeste, pour tout reparser.

Le transport (commande `gh` à l'envoi, HTTPS à la réception) est injecté : les tests
tournent hors ligne avec des doublures, comme ceux de la collecte.

Usage dans le workflow : python -m corridor.ingest.releases publish [--root DOSSIER]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests

from corridor.ingest.port_status import (
    DEFAULT_ROOT,
    MANIFEST_NAME,
    USER_AGENT,
    read_manifest,
    write_manifest,
)

RELEASE_PREFIX = "raw-"

log = logging.getLogger(__name__)


class IntegrityError(RuntimeError):
    """Un PDF retéléchargé ne correspond pas à l'empreinte consignée au manifeste."""


class Uploader(Protocol):
    """Tout ce que la publication exige d'un transport : envoyer un fichier dans une release."""

    def upload(self, tag: str, file: Path) -> None: ...


def release_tag(fetched_at_utc: str) -> str:
    """Release mensuelle d'une collecte : « 2026-09-28T11:36:33Z » → « raw-2026-09 »."""
    return f"{RELEASE_PREFIX}{fetched_at_utc[:7]}"


def asset_ref(tag: str, filename: str) -> str:
    return f"{tag}/{filename}"


def split_asset_ref(ref: str) -> tuple[str, str]:
    tag, _, filename = ref.partition("/")
    if not tag or not filename:
        raise ValueError(f"référence d'asset invalide : {ref!r} (attendu « tag/fichier »)")
    return tag, filename


def asset_url(repo: str, tag: str, filename: str) -> str:
    """URL publique de téléchargement d'un asset (dépôt public, sans jeton)."""
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def publish_pending(root: Path, uploader: Uploader) -> list[str]:
    """Publie tous les PDF locaux non encore publiés ; renvoie les références publiées.

    Une ligne sans `path` (collecte en erreur) n'a rien à publier. Une ligne dont le
    fichier a disparu du disque est laissée vide et signalée : c'est un PDF perdu, que
    seul l'artefact de secours du workflow peut encore contenir.
    """
    manifest = root / MANIFEST_NAME
    rows = read_manifest(manifest)
    published: list[str] = []
    for row in rows:
        if row.get("release_asset") or not row.get("path"):
            continue
        local = root / row["path"]
        if not local.exists():
            log.warning("PDF introuvable, non publié : %s", local)
            continue
        tag = release_tag(row["fetched_at_utc"])
        uploader.upload(tag, local)
        row["release_asset"] = asset_ref(tag, local.name)
        published.append(row["release_asset"])
        write_manifest(manifest, rows)  # après chaque envoi : une panne ne perd rien
    return published


Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class GhUploader:
    """Envoi par la commande `gh`, authentifiée par GH_TOKEN dans le workflow.

    Crée la release du mois si elle manque, ne renvoie pas un asset déjà présent, et
    réessaie l'envoi : sur le runner, un PDF non publié est un PDF perdu.
    """

    retries: int = 3
    backoff_s: float = 10.0
    run: Runner = field(default=subprocess.run)

    def upload(self, tag: str, file: Path) -> None:
        self._ensure_release(tag)
        if file.name in self._asset_names(tag):
            log.info("Déjà publié : %s/%s", tag, file.name)
            return
        for attempt in range(1, self.retries + 1):
            result = self.run(
                ["gh", "release", "upload", tag, str(file)], capture_output=True, text=True
            )
            if result.returncode == 0:
                log.info("Publié : %s/%s", tag, file.name)
                return
            log.warning("Envoi %d/%d échoué : %s", attempt, self.retries, result.stderr.strip())
            if attempt < self.retries:
                time.sleep(self.backoff_s * attempt)
        raise RuntimeError(f"échec de publication de {file.name} dans {tag}")

    def _ensure_release(self, tag: str) -> None:
        if self.run(["gh", "release", "view", tag], capture_output=True).returncode == 0:
            return
        self.run(
            [
                "gh", "release", "create", tag,
                "--title", f"PDF bruts {tag.removeprefix(RELEASE_PREFIX)}",
                "--notes", (
                    "Situation portuaire de Djen Djen, PDF bruts collectés 3×/jour. "
                    "Index : data/raw/port_status/_manifest.csv."
                ),
                "--latest=false",  # une archive de données n'est pas une version du code
            ],
            check=True,
            capture_output=True,
        )

    def _asset_names(self, tag: str) -> set[str]:
        result = self.run(
            ["gh", "release", "view", tag, "--json", "assets"],
            capture_output=True,
            text=True,
            check=True,
        )
        return {asset["name"] for asset in json.loads(result.stdout or "{}").get("assets", [])}


Downloader = Callable[[str], bytes]


def http_download(url: str, session: requests.Session | None = None) -> bytes:
    """Télécharge un asset public ; GitHub redirige vers son stockage, suivi par requests."""
    session = session or requests.Session()
    response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    return response.content


def fetch_missing(root: Path, repo: str, download: Downloader = http_download) -> list[Path]:
    """Retélécharge les PDF publiés absents du disque, en vérifiant leur empreinte.

    L'empreinte du fichier (`sha256` au manifeste) ne sert plus à dédupliquer, mais elle
    garantit ici que le PDF récupéré est exactement celui qui a été collecté.
    """
    fetched: list[Path] = []
    for row in read_manifest(root / MANIFEST_NAME):
        if not row.get("release_asset") or not row.get("path"):
            continue
        target = root / row["path"]
        if target.exists():
            continue
        tag, filename = split_asset_ref(row["release_asset"])
        content = download(asset_url(repo, tag, filename))
        digest = hashlib.sha256(content).hexdigest()
        if digest != row["sha256"]:
            raise IntegrityError(f"{filename} : empreinte {digest[:12]}…, attendu {row['sha256'][:12]}…")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        fetched.append(target)
    return fetched


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publie les PDF bruts dans les releases.")
    parser.add_argument("command", choices=["publish"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    published = publish_pending(args.root, GhUploader())
    log.info("%d PDF publiés", len(published))
    return 0


if __name__ == "__main__":
    sys.exit(main())
