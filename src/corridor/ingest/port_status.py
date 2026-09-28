"""Collecte de la situation portuaire de Djen Djen (exigences F01 et F02).

Chaque exécution :
1. télécharge le PDF de situation portuaire (avec plusieurs tentatives) ;
2. vérifie que c'est bien un PDF ;
3. calcule l'empreinte de son **contenu** (les tableaux, pas le fichier) ;
4. l'archive localement, horodaté en UTC ;
5. ajoute une ligne au manifeste CSV, y compris en cas d'échec.

**Pourquoi l'empreinte du contenu.** L'en-tête du PDF porte l'heure de génération du
document, qui change à chaque téléchargement : deux PDF décrivant la même situation
diffèrent donc toujours octet par octet, et une déduplication par empreinte de fichier
ne se déclenche jamais. `content_sha256` ignore cette heure ; le statut `unchanged`
signifie « même situation que la collecte précédente réussie ».

Le PDF est archivé même inchangé : son heure de génération, elle, est nouvelle, et c'est
elle qui atteste que les navires étaient encore listés à ce moment-là. L'archive durable
n'est pas ce dossier, mais les releases GitHub (voir `corridor.ingest.releases`).

Le manifeste est aussi une donnée : il révèle à quelle fréquence la situation
publiée change réellement.

Usage : python -m corridor.ingest.port_status [--root DOSSIER] [--url URL]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import requests

SOURCE_URL = "https://status.djendjen-port.dz/status/"
# Remplace VOTRE_EMAIL : un user-agent identifiable est une marque de respect envers la source.
USER_AGENT = "djendjen-bellara-twin/0.1 (projet portfolio open-source; contact: VOTRE_EMAIL)"
DEFAULT_ROOT = Path("data/raw/port_status")
MANIFEST_NAME = "_manifest.csv"
MANIFEST_FIELDS = [
    "fetched_at_utc",
    "status",
    "sha256",          # empreinte du fichier : change à chaque collecte (en-tête horodaté)
    "content_sha256",  # empreinte de la situation décrite : seule base du statut
    "size_bytes",
    "path",
    "release_asset",   # « raw-AAAA-MM/fichier.pdf » une fois publié, vide avant
    "error",
]

#: Calcule l'empreinte du contenu d'un PDF ; None si le PDF est illisible.
Fingerprinter = Callable[[bytes, datetime], str | None]

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Le PDF n'a pas pu être récupéré après toutes les tentatives."""


def fetch_pdf(
    url: str = SOURCE_URL,
    retries: int = 3,
    backoff_s: float = 10.0,
    timeout_s: float = 60.0,
    session: requests.Session | None = None,
) -> bytes:
    """Télécharge le PDF ; réessaie en cas d'erreur réseau ou de réponse non-PDF."""
    session = session or requests.Session()
    last_error = "échec inconnu"
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout_s)
            if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
                return resp.content
            last_error = (
                f"HTTP {resp.status_code}, {len(resp.content)} octets, "
                f"début={resp.content[:20]!r}"
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        log.warning("Tentative %d/%d échouée : %s", attempt, retries, last_error)
        if attempt < retries:
            time.sleep(backoff_s * attempt)
    raise FetchError(last_error)


class ManifestSchemaError(RuntimeError):
    """L'en-tête du manifeste ne correspond pas aux colonnes attendues."""


def read_manifest(manifest: Path) -> list[dict[str, str]]:
    """Lit toutes les lignes du manifeste ; liste vide s'il n'existe pas."""
    if not manifest.exists():
        return []
    with manifest.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        _check_header(manifest, reader.fieldnames)
        return list(reader)


def write_manifest(manifest: Path, rows: list[dict[str, str]]) -> None:
    """Réécrit le manifeste en entier, de façon atomique (fichier temporaire puis rename).

    Sert à renseigner `release_asset` après publication. Un arrêt en pleine écriture
    laisse l'ancien manifeste intact plutôt qu'un fichier tronqué.
    """
    manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) or "" for k in MANIFEST_FIELDS})
    os.replace(tmp, manifest)


def _check_header(manifest: Path, fieldnames: list[str] | None) -> None:
    """Refuse d'écrire dans un manifeste d'un autre schéma : les colonnes se décaleraient."""
    if fieldnames is not None and list(fieldnames) != MANIFEST_FIELDS:
        raise ManifestSchemaError(
            f"{manifest} : colonnes {fieldnames}, attendu {MANIFEST_FIELDS}. "
            "Migrer le manifeste avant d'y ajouter une ligne."
        )


def last_content_sha256(manifest: Path) -> str | None:
    """Empreinte de contenu de la dernière collecte réussie (erreurs ignorées)."""
    ok_rows = [
        r for r in read_manifest(manifest)
        if r["status"] in ("new", "unchanged") and r.get("content_sha256")
    ]
    return ok_rows[-1]["content_sha256"] if ok_rows else None


def append_manifest(manifest: Path, row: dict) -> None:
    is_new_file = not manifest.exists()
    if not is_new_file:
        with manifest.open(newline="", encoding="utf-8") as f:
            _check_header(manifest, csv.DictReader(f).fieldnames)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        if is_new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDS})


def pdf_content_sha256(content: bytes, fetched_at: datetime) -> str | None:
    """Empreinte de la situation décrite par un PDF ; None s'il ne se lit pas.

    Import local : la collecte ne dépend du parseur que pour cette empreinte, et un PDF
    que le parseur ne comprend plus doit quand même être archivé.
    """
    from corridor.transform.parse_status import content_fingerprint, parse_pdf_bytes

    try:
        return content_fingerprint(parse_pdf_bytes(content, fetched_at))
    except Exception as exc:  # noqa: BLE001 - tout échec de lecture a la même conséquence
        log.warning("Contenu illisible, empreinte impossible : %s", exc)
        return None


def snapshot_path(fetched_at: datetime) -> Path:
    """Chemin relatif d'archivage d'une collecte, sous la racine locale."""
    return Path(fetched_at.strftime("%Y/%m/%d")) / f"port_status_{fetched_at:%Y%m%dT%H%M%SZ}.pdf"


def save_snapshot(
    content: bytes,
    fetched_at: datetime,
    root: Path,
    fingerprint: Fingerprinter = pdf_content_sha256,
) -> dict:
    """Archive le PDF et consigne la collecte ; le statut dépend du contenu, pas du fichier.

    - `new` : la situation diffère de la dernière collecte réussie ;
    - `unchanged` : même `content_sha256`, donc même situation publiée.

    Un PDF illisible est archivé quand même, en `new`, avec la raison dans `error` : on
    ne jette jamais une donnée brute parce que le parseur ne la comprend pas encore.
    """
    manifest = root / MANIFEST_NAME
    content_sha = fingerprint(content, fetched_at)
    rel = snapshot_path(fetched_at)
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(content)
    unchanged = content_sha is not None and content_sha == last_content_sha256(manifest)
    row = {
        "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "unchanged" if unchanged else "new",
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_sha256": content_sha or "",
        "size_bytes": len(content),
        "path": rel.as_posix(),
        "error": "" if content_sha else "contenu illisible : empreinte impossible",
    }
    append_manifest(manifest, row)
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collecte la situation portuaire de Djen Djen.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="dossier d'archivage")
    parser.add_argument("--url", default=SOURCE_URL, help="URL du PDF de situation")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fetched_at = datetime.now(UTC).replace(microsecond=0)
    try:
        content = fetch_pdf(args.url)
    except FetchError as exc:
        append_manifest(
            args.root / MANIFEST_NAME,
            {
                "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "error",
                "error": str(exc)[:300],
            },
        )
        log.error("Collecte échouée : %s", exc)
        return 1  # fait échouer le workflow → GitHub t'envoie un e-mail d'alerte

    row = save_snapshot(content, fetched_at, args.root)
    log.info("Statut=%s (contenu %s) taille=%s octets fichier=%s", row["status"],
             row["content_sha256"][:12] or "illisible", row["size_bytes"], row["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
