"""Retélécharge les PDF bruts depuis les releases GitHub, pour tout reparser en local.

Les PDF ne sont plus dans git : ils sont dans les releases mensuelles `raw-AAAA-MM`, et le
manifeste dit où trouver chacun (colonne `release_asset`). Ce script récupère ceux qui
manquent sur le disque, vérifie leur empreinte, puis on peut régénérer la table propre :

    python scripts/fetch_raw.py
    python -m corridor.transform.observations_csv --rebuild

Le dépôt est public : aucun jeton n'est nécessaire.

Usage : python scripts/fetch_raw.py [--repo PROPRIETAIRE/DEPOT] [--root DOSSIER]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from corridor.ingest.port_status import DEFAULT_ROOT
from corridor.ingest.releases import fetch_missing


def repo_from_git_remote() -> str:
    """Déduit « propriétaire/dépôt » de l'URL du remote origin."""
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True
    ).stdout.strip()
    match = re.search(r"github\.com[:/](?P<repo>[^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        sys.exit(f"Remote origin non GitHub : {url}. Passer --repo propriétaire/dépôt.")
    return match.group("repo")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=None, help="propriétaire/dépôt (défaut : remote origin)")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    repo = args.repo or repo_from_git_remote()
    fetched = fetch_missing(args.root, repo)
    print(f"{len(fetched)} PDF retéléchargés depuis {repo} dans {args.root}")
    for path in fetched:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
