"""Collecte ponctuelle des statistiques de trafic publiées par le port de Djen Djen.

Rien à voir avec `port_status`, qui tourne trois fois par jour sur la situation portuaire
instantanée. Ici on archive une fois pour toutes les **bilans de trafic** que le port
publie par période : « Trafic Marchandises Débarquées par catégorie de produit », en page
web et en PDF joint.

**Pourquoi ces pages.** Elles sont la seule source publique qui donne un tonnage de
pellets par période, donc la seule façon d'estimer à quel rythme les minéraliers arrivent
réellement — le paramètre `simulation.dispersion_arrivees`, de statut `estimé` depuis le
début du projet et qui gouverne tous les résultats.

**Comment lire les périodes.** Chaque page compare deux périodes **cumulées depuis le
1er janvier** : « 1er trimestre », « 1er semestre », « fin septembre », « année ». Un
trimestre isolé s'obtient donc par différence de deux cumuls (voir
`corridor.transform.parse_port_stats`). Chaque page portant deux années, chaque cumul est
publié **deux fois**, sur deux pages différentes : c'est une vérification croisée gratuite,
et le script d'analyse s'en sert.

**Ce qui est archivé.** La page HTML (qui porte toujours le tableau) et le PDF joint quand
il existe. Le PDF fait foi, mais deux périodes de 2025 n'en ont pas, et une page de 2025
pointe par erreur vers le PDF de 2024 : le HTML est donc la source de lecture, le PDF la
pièce justificative. Les deux sont empreintés dans `_manifest.csv`.

Usage : python -m corridor.ingest.port_stats [--root data/raw/port_stats] [--delay 1.0]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

BASE_URL = "https://djendjen-port.dz"
DEFAULT_ROOT = Path("data/raw/port_stats")
MANIFEST_NAME = "_manifest.csv"
USER_AGENT = (
    "corridor-twin/0.1 (etude independante de portfolio ; collecte ponctuelle "
    "de statistiques publiques ; contact via le depot GitHub)"
)
#: Délai entre deux requêtes. La collecte est ponctuelle, rien ne presse.
DEFAULT_DELAY_S = 1.5
TIMEOUT_S = 60

#: Trimestre de fin de chaque période cumulée, pour trier et différencier les cumuls.
CUMULS = {"T1": 1, "S1": 2, "9M": 3, "AN": 4}


@dataclass(frozen=True, slots=True)
class StatsPage:
    """Une page de bilan : son adresse, et les deux périodes cumulées qu'elle compare.

    `annee_gauche` et `annee_droite` sont les deux années mises en regard ; `cumul` est
    la période cumulée commune aux deux colonnes (`T1`, `S1`, `9M` ou `AN`).
    """

    slug: str
    cumul: str
    annee_gauche: int
    annee_droite: int

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.slug}/"


#: Toutes les pages de débarquement publiées, de l'exercice 2022 à l'exercice 2025.
#: Chaque cumul apparaît deux fois (comme colonne droite d'une page, comme colonne gauche
#: de la page de l'année suivante), sauf aux deux extrémités de la série.
PAGES = (
    StatsPage("trafic-portuaire-debarquement", "AN", 2021, 2022),
    StatsPage("trafic-portuaire-debarquement-1ertrim2023", "T1", 2022, 2023),
    StatsPage("trafic-portuaire-debarquement-1-er-sem-2023", "S1", 2022, 2023),
    StatsPage("trafic-portuaire-debarquement-fin-sept-2023", "9M", 2022, 2023),
    StatsPage("trafic-portuaire-debarquement-2023", "AN", 2022, 2023),
    StatsPage("debarquement-marchandises", "T1", 2023, 2024),
    StatsPage("debarquement-marchandises-2", "S1", 2023, 2024),
    StatsPage("debarquement-fin-septembre-2024", "9M", 2023, 2024),
    StatsPage("trafic-portuaire-debarquement-2024", "AN", 2023, 2024),
    StatsPage("trafic-portuaire-debarquement-1er-trim-2025", "T1", 2024, 2025),
    StatsPage("trafic-portuaire-debarquement-1-er-sem-2025", "S1", 2024, 2025),
    StatsPage("trafic-portuaire-debarquement-fin-septembre-2025", "9M", 2024, 2025),
    StatsPage("trafic-portuaire-debarquement-2025", "AN", 2024, 2025),
)

MANIFEST_FIELDS = (
    "fetched_at_utc", "slug", "cumul", "annee_gauche", "annee_droite",
    "kind", "url", "path", "sha256", "bytes", "status",
)

#: Un PDF de statistiques joint à une page ; on écarte les tarifs et la revue du port.
_PDF_LINK = re.compile(r'https://djendjen-port\.dz/wp-content/uploads/[^"\'\s<>]+?\.pdf')
_PDF_EXCLUS = ("tarif", "djendjen-info")


def attached_pdfs(html: str) -> list[str]:
    """URLs des PDF de statistiques joints à une page, sans les tarifs ni la revue."""
    trouves = sorted(set(_PDF_LINK.findall(html)))
    return [u for u in trouves if not any(x in u.lower() for x in _PDF_EXCLUS)]


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fetch(url: str, session: requests.Session | None = None, timeout_s: int = TIMEOUT_S) -> bytes:
    """Télécharge une URL et renvoie ses octets ; lève si le serveur refuse."""
    client = session or requests.Session()
    response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout_s)
    response.raise_for_status()
    return response.content


def read_manifest(root: Path = DEFAULT_ROOT) -> list[dict]:
    """Lignes du manifeste, telles quelles ; liste vide s'il n'existe pas encore."""
    path = Path(root) / MANIFEST_NAME
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_manifest(root: Path, rows: list[dict]) -> Path:
    """Ajoute des lignes au manifeste, en le créant avec son en-tête si besoin."""
    path = Path(root) / MANIFEST_NAME
    nouveau = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        if nouveau:
            writer.writeheader()
        writer.writerows(rows)
    return path


def _ecrire(root: Path, nom: str, content: bytes) -> Path:
    """Écrit un fichier brut, sans jamais écraser : un brut archivé ne se réécrit pas."""
    chemin = Path(root) / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if chemin.exists():
        return chemin
    chemin.write_bytes(content)
    return chemin


def collect(
    root: Path = DEFAULT_ROOT,
    pages: tuple[StatsPage, ...] = PAGES,
    delay_s: float = DEFAULT_DELAY_S,
    session: requests.Session | None = None,
) -> list[dict]:
    """Archive chaque page et ses PDF joints, et renvoie les lignes de manifeste écrites.

    Les erreurs n'interrompent pas la collecte : elles sont consignées avec le statut
    `error`, pour qu'une page retirée du site laisse une trace au lieu d'un silence.
    """
    client = session or requests.Session()
    maintenant = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lignes: list[dict] = []

    def ligne(page: StatsPage, kind: str, url: str, **extra) -> dict:
        return {
            "fetched_at_utc": maintenant, "slug": page.slug, "cumul": page.cumul,
            "annee_gauche": page.annee_gauche, "annee_droite": page.annee_droite,
            "kind": kind, "url": url, "path": "", "sha256": "", "bytes": "",
            "status": "ok", **extra,
        }

    for page in pages:
        try:
            html = fetch(page.url, client, TIMEOUT_S)
        except Exception as erreur:  # noqa: BLE001 - une page absente ne doit rien arrêter
            lignes.append(ligne(page, "html", page.url, status=f"error: {erreur}"))
            continue
        nom = f"{page.annee_droite}_{page.cumul}_{page.slug}.html"
        _ecrire(root, nom, html)
        lignes.append(ligne(page, "html", page.url, path=nom,
                            sha256=sha256(html), bytes=len(html)))
        time.sleep(delay_s)

        for index, pdf_url in enumerate(attached_pdfs(html.decode("utf-8", "replace"))):
            try:
                pdf = fetch(pdf_url, client, TIMEOUT_S)
            except Exception as erreur:  # noqa: BLE001
                lignes.append(ligne(page, "pdf", pdf_url, status=f"error: {erreur}"))
                continue
            suffixe = "" if index == 0 else f"_{index}"
            nom_pdf = f"{page.annee_droite}_{page.cumul}_{page.slug}{suffixe}.pdf"
            _ecrire(root, nom_pdf, pdf)
            lignes.append(ligne(page, "pdf", pdf_url, path=nom_pdf,
                                sha256=sha256(pdf), bytes=len(pdf)))
            time.sleep(delay_s)

    append_manifest(root, lignes)
    return lignes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    args = parser.parse_args(argv)

    lignes = collect(args.root, delay_s=args.delay)
    erreurs = [x for x in lignes if x["status"] != "ok"]
    print(f"{len(lignes)} fichiers traités dans {args.root} "
          f"({sum(1 for x in lignes if x['kind'] == 'html')} pages, "
          f"{sum(1 for x in lignes if x['kind'] == 'pdf')} PDF)")
    for x in erreurs:
        print(f"  ERREUR {x['url']} : {x['status']}")
    return 1 if erreurs else 0


if __name__ == "__main__":
    sys.exit(main())
