"""Parsing du PDF de situation portuaire de Djen Djen (étape 2).

Le PDF est un document texte (pas un scan) : les trois tableaux sont extraits par
pdfplumber, puis chaque section est reconnue **par sa ligne d'en-tête** et non par
son rang dans la page. Les trois sections n'ont ni les mêmes colonnes ni le même
nombre de colonnes, et une section peut être vide (« Expected Arrivals (0) » :
le tableau n'a alors que son en-tête).

Toutes les fonctions sont pures sauf `parse_pdf`, qui lit le fichier.

Deux horloges cohabitent, à ne jamais confondre :

- `source_time_utc` : heure de mise à jour affichée par le port, locale
  (Africa/Algiers, UTC+1 sans changement d'heure), convertie en UTC ;
- `fetched_at_utc` : heure de notre collecte, déjà en UTC dans le nom du fichier.

Les heures des tableaux (Berthed, Anchorage, E.T.A) sont elles aussi locales et
sont converties en UTC, pour que l'étape 3 puisse soustraire deux dates sans piège.

Usage : `from corridor.transform.parse_status import parse_pdf`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber

#: Fuseau du port. L'Algérie est à UTC+1 toute l'année (pas d'heure d'été).
PORT_TZ = ZoneInfo("Africa/Algiers")

#: Un navire à quai depuis plus de ce nombre de jours est immobilisé, pas en escale.
LONG_STAY_DAYS = 60

CARGO_CATEGORIES = ("iron_ore", "grain", "container", "general", "other")

#: Mots-clés par catégorie, testés dans cet ordre sur le libellé sans accents.
#: `\b` est indispensable : sans lui « ble » attraperait « assemble »,
#: et « bus » attraperait « BUSAN ».
_CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("iron_ore", r"iron\s*ore|pellet|minerai"),
    ("grain", r"\bble\b|\bdurum\b|wheat|grain|cereale|\bmais\b|\bcorn\b|\borge\b|barley"),
    ("container", r"\btcs\b|\bteu\b|conteneur|container"),
    ("general", r"\bdivers\b|general|\bmdf\b|\bboard\b|\bbus\b|\bvehicul"),
)

#: Unités de tonnage rencontrées : `MT`, `Mt`, `T`… `\b` empêche « T » d'avaler
#: le « T » de « TCS » (nombre de conteneurs, pas un tonnage).
_UNIT = r"(?:MT|TM|TONNES|TONNE|TONS|TON|T)\b"
#: Nombre décimal à la virgule *ou* au point. Aucun séparateur de milliers n'a été
#: observé dans la source (`142533`, jamais `142,533`) : la virgule est donc toujours
#: traitée comme une décimale. Si la source change, cette règle est à revoir.
_NUMBER = r"\d+(?:[.,]\d+)?"

#: Le nombre porteur de l'unité est le tonnage, où qu'il soit dans la cellule :
#: dans « 244 TCS (4469.262 Mt) », c'est celui entre parenthèses.
_TONNAGE_AFTER = re.compile(rf"({_NUMBER})\s*{_UNIT}", re.IGNORECASE)
_TONNAGE_BEFORE = re.compile(rf"\b{_UNIT}\s*({_NUMBER})", re.IGNORECASE)
#: À défaut d'unité (« 60236.54 Blé Dur »), un nombre en tête de cellule est un tonnage.
_TONNAGE_BARE = re.compile(rf"^\s*({_NUMBER})")

_HEADER_TIME = re.compile(
    r"Djen[\s-]*Djen\s*:?\s*(\d{4}-\d{2}-\d{2}[\sT]+\d{2}:\d{2}(?::\d{2})?)",
    re.IGNORECASE,
)
_FILE_TIME = re.compile(r"(\d{8}T\d{6})Z")

#: Colonne d'horodatage propre à chaque section, et donc signature de la section.
_EVENT_COLUMN = {"berthed": "berthed", "anchorage": "anchorage", "expected": "e.t.a"}


@dataclass(frozen=True, slots=True)
class VesselObservation:
    """Un navire vu dans un instantané de la situation portuaire.

    C'est une observation, pas une escale : un même navire réapparaît à chaque
    collecte tant qu'il est présent. Les escales sont reconstruites à l'étape 3.
    """

    source_time_utc: datetime
    fetched_at_utc: datetime
    status: str  # berthed | anchorage | expected
    dock: str | None  # renseigné seulement à quai
    vessel: str
    flag: str
    shiptype: str
    cargo_raw: str
    cargo_label: str  # cargo_raw amputé du tonnage : sert de signal d'identité à l'étape 3
    cargo_category: str
    tonnage_t: float | None
    last_port: str
    agent: str
    event_time: datetime | None  # Berthed, Anchorage ou E.T.A selon la section
    situation: str | None  # renseigné seulement en rade
    long_stay: bool


def clean_cell(value: str | None) -> str:
    """Aplati une cellule : sauts de ligne en espaces, espaces multiples réduites.

    Les cellules du PDF sont fréquemment sur plusieurs lignes
    (« MARSHALL\\nISLANDS », agents sur trois lignes). Une cellule absente vaut "".
    """
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def strip_accents(text: str) -> str:
    """Retire les accents : « Blé Dur » et « BLE DUR » doivent se classer pareil."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def parse_local_datetime(text: str | None) -> datetime | None:
    """Lit une date-heure locale du port et la renvoie en UTC ; None si illisible.

    Les tableaux n'indiquent aucun fuseau : « 2026-02-24 22:00 » est une heure
    locale d'Algérie, soit 21:00 UTC.
    """
    cleaned = clean_cell(text)
    if not cleaned:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=PORT_TZ).astimezone(UTC)
        except ValueError:
            continue
    return None


def parse_header(text: str) -> datetime:
    """Extrait l'heure de mise à jour du port (« Djen-Djen : 2026-09-24 16:58 ») en UTC.

    Lève `ValueError` plutôt que de se rabattre sur la première date rencontrée :
    une date de mise à jour silencieusement fausse contaminerait toute l'étape 3.
    """
    match = _HEADER_TIME.search(text or "")
    if not match:
        first_line = clean_cell((text or "").split("\n")[0])
        raise ValueError(f"en-tête « Djen-Djen : <date> » introuvable (début : {first_line!r})")
    moment = parse_local_datetime(match.group(1))
    if moment is None:
        raise ValueError(f"en-tête illisible : {match.group(1)!r}")
    return moment


def fetched_at_from_name(path: str | Path) -> datetime:
    """Lit l'heure de collecte (UTC) dans le nom du fichier archivé."""
    name = Path(path).name
    match = _FILE_TIME.search(name)
    if not match:
        raise ValueError(f"horodatage UTC absent du nom de fichier : {name!r}")
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


def parse_tonnage(cargo_text: str | None) -> tuple[float | None, str]:
    """Sépare le tonnage (en tonnes) du libellé de marchandise.

    Gère la virgule comme le point décimal, l'unité avant ou après le nombre, collée
    ou non, et l'absence de tonnage. Quand la cellule contient plusieurs nombres,
    c'est celui porteur de l'unité qui gagne : « 244 TCS (4469.262 Mt) » donne
    4469.262 t et le libellé « 244 TCS », qui conserve le nombre de conteneurs.

    Renvoie `(None, libellé)` si aucun nombre n'est exploitable.
    """
    text = clean_cell(cargo_text)
    if not text:
        return None, ""
    for pattern in (_TONNAGE_AFTER, _TONNAGE_BEFORE, _TONNAGE_BARE):
        match = pattern.search(text)
        if match:
            tonnage = float(match.group(1).replace(",", "."))
            return tonnage, _strip_match(text, match.start(), match.end())
    return None, text


def _strip_match(text: str, start: int, end: int) -> str:
    """Retire du libellé la portion « nombre + unité » et les parenthèses vidées."""
    label = f"{text[:start]} {text[end:]}"
    label = re.sub(r"\(\s*\)", " ", label)
    return re.sub(r"\s+", " ", label).strip(" -,;:")


def categorize_cargo(cargo_text: str | None) -> str:
    """Classe une marchandise : iron_ore, grain, container, general ou other.

    Insensible à la casse et aux accents. Tout ce qui contient « iron ore » ou
    « pellet » est `iron_ore` : ce sont les minéraliers (~140 000 t) qui alimentent
    le DRI de Bellara. `general` couvre le fret conventionnel (DIVERS, MDF BOARD,
    véhicules) ; `other` est le refuge des marchandises nommées mais hors
    nomenclature, comme le grignon d'olive.
    """
    text = strip_accents(clean_cell(cargo_text)).lower()
    if not text:
        return "other"
    for category, pattern in _CATEGORY_PATTERNS:
        if re.search(pattern, text):
            return category
    return "other"


def is_long_stay(
    status: str,
    event_time: datetime | None,
    source_time_utc: datetime,
    days: int = LONG_STAY_DAYS,
) -> bool:
    """Signale un navire à quai depuis plus de `days` jours.

    Un tel navire est immobilisé (un cas réel dépasse sept mois) : il faut pouvoir
    l'exclure des statistiques de durée à quai, jamais l'y inclure tel quel.
    L'attente en rade n'est pas un séjour à quai et n'est donc jamais signalée.
    """
    if status != "berthed" or event_time is None:
        return False
    return source_time_utc - event_time > timedelta(days=days)


def _section_of(header: list[str]) -> str | None:
    """Reconnaît la section d'un tableau d'après sa ligne d'en-tête.

    On se fie aux colonnes, pas à l'ordre des tableaux dans la page : la section
    « Berthed » est la seule à avoir une colonne Dock, « Anchorage » la seule à
    avoir une colonne Situation, « Expected Arrivals » la seule avec E.T.A.
    """
    labels = {clean_cell(cell).lower() for cell in header}
    if "dock" in labels and "berthed" in labels:
        return "berthed"
    if "anchorage" in labels and "situation" in labels:
        return "anchorage"
    if "e.t.a" in labels:
        return "expected"
    return None


def _row_to_observation(
    row: dict[str, str],
    status: str,
    source_time_utc: datetime,
    fetched_at_utc: datetime,
    long_stay_days: int,
) -> VesselObservation:
    cargo_raw = row.get("cargo", "")
    tonnage_t, cargo_label = parse_tonnage(cargo_raw)
    event_time = parse_local_datetime(row.get(_EVENT_COLUMN[status]))
    return VesselObservation(
        source_time_utc=source_time_utc,
        fetched_at_utc=fetched_at_utc,
        status=status,
        dock=row.get("dock") or None,
        vessel=row.get("vessel", ""),
        flag=row.get("flag", ""),
        shiptype=row.get("shiptype", ""),
        cargo_raw=cargo_raw,
        cargo_label=cargo_label,
        cargo_category=categorize_cargo(cargo_raw),
        tonnage_t=tonnage_t,
        last_port=row.get("last port", ""),
        agent=row.get("agent", ""),
        event_time=event_time,
        situation=row.get("situation") or None,
        long_stay=is_long_stay(status, event_time, source_time_utc, long_stay_days),
    )


def parse_pdf(
    path: str | Path,
    long_stay_days: int = LONG_STAY_DAYS,
) -> list[VesselObservation]:
    """Transforme un instantané PDF en une observation par navire.

    L'heure de mise à jour du port vient de l'en-tête, l'heure de collecte du nom
    du fichier. Les sections vides ne produisent aucune ligne.
    """
    path = Path(path)
    fetched_at_utc = fetched_at_from_name(path)
    observations: list[VesselObservation] = []
    with pdfplumber.open(path) as pdf:
        source_time_utc = parse_header(pdf.pages[0].extract_text() or "")
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                header = [clean_cell(cell).lower() for cell in table[0]]
                status = _section_of(table[0])
                if status is None:  # tableau inattendu : mieux vaut l'ignorer que deviner
                    continue
                for raw_row in table[1:]:
                    cells = [clean_cell(cell) for cell in raw_row]
                    if not any(cells):  # ligne de séparation vide
                        continue
                    row = dict(zip(header, cells, strict=False))
                    observations.append(
                        _row_to_observation(
                            row, status, source_time_utc, fetched_at_utc, long_stay_days
                        )
                    )
    return observations
