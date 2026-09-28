"""Table versionnée des observations de navires : `data/clean/observations.csv`.

C'est la donnée que le dépôt conserve à la place des PDF : une ligne par navire et par
collecte, les colonnes de `VesselObservation` dans l'ordre. Les PDF eux-mêmes sont dans
les releases GitHub, et cette table se reconstruit entièrement à partir d'eux
(`scripts/fetch_raw.py`, puis `--rebuild`).

Une collecte `unchanged` est ajoutée comme les autres : sa situation est la même, mais son
heure ne l'est pas, et c'est cette heure qui atteste que les navires étaient encore listés.
Sans elle, l'étape 3 bornerait les départs plus largement qu'elle ne le peut.

L'ajout est idempotent : une collecte déjà présente (même `fetched_at_utc`) n'est jamais
ajoutée deux fois, de sorte que le workflow peut être relancé sans dupliquer de lignes.

Usage :
    python -m corridor.transform.observations_csv            # ajoute les nouvelles collectes
    python -m corridor.transform.observations_csv --rebuild  # régénère tout depuis les PDF
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import logging
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from corridor.ingest.port_status import DEFAULT_ROOT, MANIFEST_NAME, read_manifest
from corridor.transform.parse_status import VesselObservation, parse_pdf

DEFAULT_PATH = Path("data/clean/observations.csv")
FIELDS = [f.name for f in dataclasses.fields(VesselObservation)]
_DATETIME_FIELDS = {"source_time_utc", "fetched_at_utc", "event_time"}
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

log = logging.getLogger(__name__)


def _to_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime(_TIME_FORMAT)
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _from_row(row: dict[str, str]) -> VesselObservation:
    values: dict[str, object] = {}
    for name in FIELDS:
        cell = row[name]
        if name in _DATETIME_FIELDS:
            values[name] = (
                datetime.strptime(cell, _TIME_FORMAT).replace(tzinfo=UTC) if cell else None
            )
        elif name == "tonnage_t":
            values[name] = float(cell) if cell else None
        elif name == "long_stay":
            values[name] = cell == "true"
        elif name in ("dock", "situation"):
            values[name] = cell or None
        else:
            values[name] = cell
    return VesselObservation(**values)


def _check_header(path: Path, fieldnames: Sequence[str] | None) -> None:
    if fieldnames is not None and list(fieldnames) != FIELDS:
        raise ValueError(f"{path} : colonnes {fieldnames}, attendu {FIELDS}")


def read_observations(path: str | Path = DEFAULT_PATH) -> list[VesselObservation]:
    """Relit la table ; directement consommable par `build_escales`."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        _check_header(path, reader.fieldnames)
        return [_from_row(row) for row in reader]


def collected_times(path: str | Path = DEFAULT_PATH) -> set[str]:
    """Heures de collecte déjà présentes dans la table, au format du manifeste."""
    path = Path(path)
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        _check_header(path, reader.fieldnames)
        return {row["fetched_at_utc"] for row in reader}


def append_observations(
    path: str | Path, observations: Iterable[VesselObservation]
) -> int:
    """Ajoute des observations en fin de table ; crée la table et son en-tête si besoin."""
    path = Path(path)
    rows = list(observations)
    if not rows:
        return 0
    is_new = not path.exists()
    if not is_new:
        with path.open(newline="", encoding="utf-8") as f:
            _check_header(path, csv.DictReader(f).fieldnames)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        if is_new:
            writer.writerow(FIELDS)
        for obs in rows:
            writer.writerow([_to_cell(getattr(obs, name)) for name in FIELDS])
    return len(rows)


def update_from_archive(
    root: str | Path = DEFAULT_ROOT,
    path: str | Path = DEFAULT_PATH,
) -> int:
    """Ajoute les collectes réussies du manifeste qui ne sont pas encore dans la table.

    Une collecte dont le PDF n'est pas sur le disque (runner éphémère, ou collecte
    ancienne non retéléchargée) est sautée avec un avertissement ; un PDF que le parseur
    ne comprend pas l'est aussi, sans bloquer les suivants.
    """
    root = Path(root)
    deja = collected_times(path)
    ajoutees = 0
    for row in read_manifest(root / MANIFEST_NAME):
        if row["status"] not in ("new", "unchanged") or not row.get("path"):
            continue
        if row["fetched_at_utc"] in deja:
            continue
        pdf = root / row["path"]
        if not pdf.exists():
            log.warning("PDF absent, collecte non ajoutée : %s", pdf)
            continue
        try:
            observations = parse_pdf(pdf)
        except Exception as exc:  # noqa: BLE001 - un PDF illisible ne bloque pas les autres
            log.warning("PDF illisible, collecte non ajoutée : %s (%s)", pdf, exc)
            continue
        ajoutees += append_observations(path, observations)
        deja.add(row["fetched_at_utc"])
    return ajoutees


def rebuild(root: str | Path = DEFAULT_ROOT, path: str | Path = DEFAULT_PATH) -> int:
    """Régénère la table depuis zéro à partir des PDF locaux listés au manifeste."""
    path = Path(path)
    if path.exists():
        path.unlink()
    return update_from_archive(root, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tient à jour data/clean/observations.csv.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--rebuild", action="store_true", help="régénère toute la table")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    count = (rebuild if args.rebuild else update_from_archive)(args.root, args.out)
    log.info("%d observations ajoutées à %s", count, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
