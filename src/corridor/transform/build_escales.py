"""Construction de la table des escales à partir des instantanés (étape 3).

Une suite d'observations horodatées devient une ligne par escale. Le cœur du module
(`build_escales`) est une fonction pure : elle ne lit rien sur disque, seul
`build_from_pdfs` ouvre des fichiers.

**L'heure de départ n'est jamais connue.** Le port publie qui est présent, jamais qui
vient de partir. Tout ce qu'on sait d'un départ, c'est qu'il a eu lieu entre le dernier
instantané où le navire était présent (`departure_min`) et le premier où il ne l'est
plus (`departure_max`). C'est une censure par intervalle, et sa largeur est celle du
trou de collecte : trois collectes par jour donnent ±8 h, une panne de 20 h donne ±20 h.
`departure_uncertainty_hours` expose cette largeur ; `departure_max is None` marque une
escale encore en cours, à conserver mais à exclure des statistiques (`usable_escales`).

Deux horloges, jamais mélangées :

- les **durées** (`wait_hours`, `berth_hours`) viennent des `event_time` publiés par le
  port (colonnes Anchorage, Berthed, E.T.A) : ce sont des heures de mouvement réelles ;
- les **bornes de départ** viennent de `source_time_utc`, l'heure de mise à jour que le
  port affiche. C'est le port qui atteste la présence, pas notre heure de téléchargement.

Rien n'est corrigé en silence : toute transition inattendue (retour à la rade,
apparition directe à quai, durée négative, révision d'ETA, ripage de poste…) est
conservée telle quelle et signalée dans `anomaly`.

Usage : `from corridor.transform.build_escales import build_from_pdfs`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import TypeVar

from corridor.transform.parse_status import (
    VesselObservation,
    parse_pdf,
    strip_accents,
)

#: Ordre attendu du cycle de vie : expected → anchorage → berthed → absent.
STATUS_ORDER: dict[str, int] = {"expected": 0, "anchorage": 1, "berthed": 2}

#: Hypothèse : au-delà de cette absence, une réapparition du même nom est une nouvelle
#: escale, même à cargaison identique. Avec trois collectes par jour, un trou de
#: publication dépasse rarement 16 h ; un vrai retour se compte en jours.
MAX_ABSENCE_HOURS = 24.0

#: Hypothèse : écart relatif de tonnage toléré entre deux présences séparées par une
#: absence. Au-delà, ce n'est pas la même cargaison, donc pas la même escale.
TONNAGE_REL_TOL = 0.05

#: Codes d'anomalie possibles. `anomaly` les concatène, triés, séparés par une virgule.
ANOMALY_CODES = (
    "left_censored",          # escale commencée au premier instantané : arrivée non observée
    "direct_berth",           # apparaît à quai en cours de série, sans rade ni annonce
    "back_to_anchorage",      # repasse de berthed à anchorage
    "back_to_expected",       # repasse à expected
    "reappeared",             # réapparu après une absence, rattaché à la même escale
    "presence_gap",           # l'escale a un trou dans la série des instantanés
    "departed_without_berth", # parti sans jamais accoster
    "eta_revised",            # ETA modifiée en cours d'escale
    "dock_changed",           # ripage de poste
    "cargo_changed",          # cargaison ou tonnage modifié en cours d'escale
    "event_time_changed",     # le port a corrigé une heure de mouvement
    "negative_duration",      # attente ou séjour négatif : incohérence de la source
    "duplicate_in_snapshot",  # deux lignes pour le même navire dans un même instantané
)

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Escale:
    """Une escale reconstruite : un navire, du premier au dernier instantané où il est vu.

    `departure_min` / `departure_max` encadrent un départ jamais observé directement.
    `berth_hours` est la durée à quai **observée** (calculée sur `departure_min`), donc
    une borne basse : la durée réelle vaut au plus
    `berth_hours + departure_uncertainty_hours`.
    """

    call_id: str
    vessel: str
    flag: str
    cargo_category: str
    cargo_label: str
    tonnage_t: float | None
    dock: str | None
    eta: datetime | None
    anchorage_time: datetime | None
    berth_time: datetime | None
    departure_min: datetime | None
    departure_max: datetime | None
    wait_hours: float | None
    berth_hours: float | None
    n_snapshots: int
    long_stay: bool
    anomaly: str

    @property
    def finished(self) -> bool:
        """Vrai si le départ a été encadré ; faux si le navire était encore là."""
        return self.departure_max is not None

    @property
    def departure_uncertainty_hours(self) -> float | None:
        """Largeur de la censure sur le départ, en heures ; None si l'escale est en cours."""
        return hours_between(self.departure_min, self.departure_max)


@dataclass(frozen=True, slots=True)
class _Run:
    """Une suite d'instantanés consécutifs où le navire est présent.

    `closed_at` est l'heure du premier instantané où il ne l'est plus, ou None si la
    présence court jusqu'au dernier instantané de la série.
    """

    observations: tuple[VesselObservation, ...]
    closed_at: datetime | None


def normalize_name(text: str) -> str:
    """Majuscules, espaces multiples réduites : la forme retenue pour l'identité."""
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def vessel_key(vessel: str, flag: str) -> tuple[str, str]:
    """Clé d'identité d'un navire : (nom, pavillon) normalisés."""
    return normalize_name(vessel), normalize_name(flag)


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    """Durée en heures, négative si les dates sont inversées ; None si l'une manque."""
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600.0


def _normalized_label(label: str | None) -> str:
    """Libellé comparable : la source écrit tantôt « DIVERS », tantôt « Divers »."""
    return strip_accents(normalize_name(label or ""))


def _same_tonnage(left: float | None, right: float | None, rel_tol: float) -> bool:
    """Deux tonnages sont-ils compatibles ? Un tonnage absent d'un seul côté : non."""
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(left - right) <= rel_tol * max(abs(left), abs(right))


def is_same_call(
    last_seen: VesselObservation,
    seen_again: VesselObservation,
    max_absence_hours: float = MAX_ABSENCE_HOURS,
    tonnage_rel_tol: float = TONNAGE_REL_TOL,
) -> bool:
    """Un navire réapparu après une absence poursuit-il la même escale ?

    Trois signaux secondaires, tous nécessaires : l'absence est courte, le cycle de vie
    n'a pas reculé (un navire de nouveau « expected » a bien fait une nouvelle escale),
    et la cargaison n'a pas changé (libellé identique, tonnage dans la tolérance).
    """
    gap = hours_between(last_seen.source_time_utc, seen_again.source_time_utc)
    if gap is None or gap > max_absence_hours:
        return False
    if STATUS_ORDER.get(seen_again.status, -1) < STATUS_ORDER.get(last_seen.status, -1):
        return False
    if _normalized_label(last_seen.cargo_label) != _normalized_label(seen_again.cargo_label):
        return False
    return _same_tonnage(last_seen.tonnage_t, seen_again.tonnage_t, tonnage_rel_tol)


def group_snapshots(
    observations: Iterable[VesselObservation],
) -> list[tuple[datetime, dict[tuple[str, str], VesselObservation]]]:
    """Regroupe les observations par instantané, du plus ancien au plus récent.

    L'instantané est identifié par `source_time_utc`, l'heure du port. Un instantané
    n'existe que s'il contient au moins un navire : une situation portuaire vide serait
    invisible ici, ce que Djen Djen ne produit pas. Si un navire y figure deux fois,
    la première ligne est retenue (voir `duplicate_in_snapshot`).
    """
    grouped: dict[datetime, dict[tuple[str, str], VesselObservation]] = {}
    for obs in sorted(observations, key=lambda o: (o.source_time_utc, o.fetched_at_utc)):
        vessels = grouped.setdefault(obs.source_time_utc, {})
        vessels.setdefault(vessel_key(obs.vessel, obs.flag), obs)
    return sorted(grouped.items())


def _duplicate_keys(observations: Iterable[VesselObservation]) -> set[tuple[str, str]]:
    """Navires apparaissant plus d'une fois dans un même instantané."""
    counted = Counter(
        (obs.source_time_utc, vessel_key(obs.vessel, obs.flag)) for obs in observations
    )
    return {key for (_time, key), count in counted.items() if count > 1}


def _presence_runs(
    key: tuple[str, str],
    snapshots: Sequence[tuple[datetime, dict[tuple[str, str], VesselObservation]]],
) -> list[_Run]:
    """Découpe la série en suites de présence consécutive, chacune fermée par une absence."""
    runs: list[_Run] = []
    current: list[VesselObservation] = []
    for time, vessels in snapshots:
        obs = vessels.get(key)
        if obs is not None:
            current.append(obs)
        elif current:
            runs.append(_Run(tuple(current), time))
            current = []
    if current:
        runs.append(_Run(tuple(current), None))
    return runs


def _merge_runs(
    runs: Sequence[_Run],
    max_absence_hours: float,
    tonnage_rel_tol: float,
) -> list[list[_Run]]:
    """Recolle les présences séparées par une absence quand c'est la même escale."""
    groups: list[list[_Run]] = []
    for run in runs:
        previous = groups[-1][-1].observations[-1] if groups else None
        if previous is not None and is_same_call(
            previous, run.observations[0], max_absence_hours, tonnage_rel_tol
        ):
            groups[-1].append(run)
        else:
            groups.append([run])
    return groups


def _first_and_changed(values: Iterable[_T | None]) -> tuple[_T | None, bool]:
    """Première valeur non nulle, et si une valeur ultérieure en diffère."""
    first: _T | None = None
    changed = False
    for value in values:
        if value is None or value == "":
            continue
        if first is None:
            first = value
        elif value != first:
            changed = True
    return first, changed


def _event_times(observations: Sequence[VesselObservation], status: str) -> list[datetime | None]:
    return [obs.event_time for obs in observations if obs.status == status]


def _slug(text: str) -> str:
    return _NON_ALNUM.sub("-", normalize_name(strip_accents(text))).strip("-")


def _to_escale(
    runs: Sequence[_Run],
    first_snapshot_time: datetime,
    duplicated: bool,
) -> Escale:
    """Réduit une escale (une ou plusieurs présences recollées) à une ligne."""
    observations = [obs for run in runs for obs in run.observations]
    first, last = observations[0], observations[-1]
    codes: set[str] = set()

    if len(runs) > 1:
        codes.update(("reappeared", "presence_gap"))
    if duplicated:
        codes.add("duplicate_in_snapshot")
    if first.source_time_utc == first_snapshot_time and first.status != "expected":
        # le navire était déjà là au premier instantané : son arrivée est hors champ,
        # ce n'est pas une transition anormale mais une censure à gauche
        codes.add("left_censored")
    elif first.status == "berthed":
        codes.add("direct_berth")

    for previous, current in pairwise(observations):
        if STATUS_ORDER.get(current.status, -1) < STATUS_ORDER.get(previous.status, -1):
            codes.add("back_to_expected" if current.status == "expected" else "back_to_anchorage")

    eta, eta_changed = _first_and_changed(_event_times(observations, "expected"))
    anchorage_time, anchorage_changed = _first_and_changed(_event_times(observations, "anchorage"))
    berth_time, berth_changed = _first_and_changed(_event_times(observations, "berthed"))
    dock, dock_changed = _first_and_changed(obs.dock for obs in observations)
    if eta_changed:
        codes.add("eta_revised")
    if anchorage_changed or berth_changed:
        codes.add("event_time_changed")
    if dock_changed:
        codes.add("dock_changed")

    reference = next((obs for obs in observations if obs.cargo_raw), first)
    label = _normalized_label(reference.cargo_label)
    if any(
        _normalized_label(obs.cargo_label) != label
        or not _same_tonnage(obs.tonnage_t, reference.tonnage_t, TONNAGE_REL_TOL)
        for obs in observations
        if obs.cargo_raw
    ):
        codes.add("cargo_changed")

    departure_min = last.source_time_utc
    departure_max = runs[-1].closed_at
    wait_hours = hours_between(anchorage_time, berth_time)
    berth_hours = hours_between(berth_time, departure_min)
    if (wait_hours is not None and wait_hours < 0) or (berth_hours is not None and berth_hours < 0):
        codes.add("negative_duration")
    if departure_max is not None and berth_time is None:
        codes.add("departed_without_berth")

    vessel, flag = vessel_key(first.vessel, first.flag)
    return Escale(
        call_id=f"{_slug(vessel)}-{_slug(flag)}-{first.source_time_utc:%Y%m%dT%H%MZ}",
        vessel=vessel,
        flag=flag,
        cargo_category=reference.cargo_category,
        cargo_label=reference.cargo_label,
        tonnage_t=reference.tonnage_t,
        dock=dock,
        eta=eta,
        anchorage_time=anchorage_time,
        berth_time=berth_time,
        departure_min=departure_min,
        departure_max=departure_max,
        wait_hours=wait_hours,
        berth_hours=berth_hours,
        n_snapshots=len(observations),
        long_stay=any(obs.long_stay for obs in observations),
        anomaly=",".join(sorted(codes)),
    )


def build_escales(
    observations: Iterable[VesselObservation],
    max_absence_hours: float = MAX_ABSENCE_HOURS,
    tonnage_rel_tol: float = TONNAGE_REL_TOL,
) -> list[Escale]:
    """Transforme une suite d'observations horodatées en une ligne par escale.

    L'ordre d'entrée n'a pas d'importance : les observations sont regroupées par
    instantané puis remises en ordre chronologique.
    """
    observations = list(observations)
    if not observations:
        return []
    snapshots = group_snapshots(observations)
    duplicated = _duplicate_keys(observations)
    first_snapshot_time = snapshots[0][0]

    keys: list[tuple[str, str]] = []
    for _time, vessels in snapshots:
        keys.extend(key for key in vessels if key not in keys)

    dated: list[tuple[datetime, Escale]] = []
    for key in keys:
        runs = _presence_runs(key, snapshots)
        for group in _merge_runs(runs, max_absence_hours, tonnage_rel_tol):
            escale = _to_escale(group, first_snapshot_time, key in duplicated)
            dated.append((group[0].observations[0].source_time_utc, escale))
    # ordre chronologique du premier instantané où le navire est vu ; le call_id
    # départage pour que la sortie soit reproductible à l'identique
    dated.sort(key=lambda pair: (pair[0], pair[1].call_id))
    return [escale for _first_seen, escale in dated]


def build_from_pdfs(
    paths: Iterable[str | Path],
    max_absence_hours: float = MAX_ABSENCE_HOURS,
    tonnage_rel_tol: float = TONNAGE_REL_TOL,
) -> list[Escale]:
    """Lit une série d'instantanés PDF archivés et en construit les escales."""

    def _observations() -> Iterator[VesselObservation]:
        for path in paths:
            yield from parse_pdf(path)

    return build_escales(_observations(), max_absence_hours, tonnage_rel_tol)


def usable_escales(escales: Iterable[Escale]) -> list[Escale]:
    """Sous-ensemble exploitable pour les statistiques de durée.

    On écarte les escales en cours (départ non encadré) et les longs séjours (navires
    immobilisés, jusqu'à sept mois à quai dans la source). Elles restent dans la table :
    c'est leur usage statistique qui est refusé, pas leur existence.
    """
    return [e for e in escales if e.finished and not e.long_stay]
