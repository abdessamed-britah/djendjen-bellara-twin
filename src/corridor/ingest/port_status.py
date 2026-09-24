"""Collecte de la situation portuaire de Djen Djen (exigences F01 et F02).

Chaque exécution :
1. télécharge le PDF de situation portuaire (avec plusieurs tentatives) ;
2. vérifie que c'est bien un PDF ;
3. l'archive tel quel, horodaté en UTC, sauf s'il est identique au précédent ;
4. ajoute une ligne au manifeste CSV, y compris en cas d'échec.

Le manifeste est aussi une donnée : il révèle à quelle fréquence le port
met réellement à jour sa situation.

Usage : python -m corridor.ingest.port_status [--root DOSSIER] [--url URL]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

SOURCE_URL = "https://status.djendjen-port.dz/status/"
# Remplace VOTRE_EMAIL : un user-agent identifiable est une marque de respect envers la source.
USER_AGENT = "djendjen-bellara-twin/0.1 (projet portfolio open-source; contact: VOTRE_EMAIL)"
DEFAULT_ROOT = Path("data/raw/port_status")
MANIFEST_NAME = "_manifest.csv"
MANIFEST_FIELDS = ["fetched_at_utc", "status", "sha256", "size_bytes", "path", "error"]

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


def last_sha256(manifest: Path) -> str | None:
    """Empreinte du dernier PDF récupéré avec succès (les lignes d'erreur sont ignorées)."""
    if not manifest.exists():
        return None
    with manifest.open(newline="", encoding="utf-8") as f:
        ok_rows = [r for r in csv.DictReader(f) if r["status"] in ("new", "unchanged")]
    return ok_rows[-1]["sha256"] if ok_rows else None


def append_manifest(manifest: Path, row: dict) -> None:
    is_new_file = not manifest.exists()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        if is_new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDS})


def save_snapshot(content: bytes, fetched_at: datetime, root: Path) -> dict:
    """Archive le PDF s'il a changé et consigne l'observation dans le manifeste."""
    digest = hashlib.sha256(content).hexdigest()
    manifest = root / MANIFEST_NAME
    row = {
        "fetched_at_utc": fetched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": digest,
        "size_bytes": len(content),
    }
    if digest == last_sha256(manifest):
        row["status"] = "unchanged"
    else:
        rel = Path(fetched_at.strftime("%Y/%m/%d")) / (
            f"port_status_{fetched_at:%Y%m%dT%H%M%SZ}.pdf"
        )
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(content)
        row["status"] = "new"
        row["path"] = rel.as_posix()
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
    log.info("Statut=%s taille=%s octets fichier=%s", row["status"], row["size_bytes"],
             row.get("path") or "(identique au précédent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
