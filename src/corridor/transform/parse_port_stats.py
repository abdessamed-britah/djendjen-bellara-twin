"""Lecture des bilans « Marchandises Débarquées » archivés par `corridor.ingest.port_stats`.

Le tableau du port est un tableau HTML régulier : une ligne par catégorie de produit,
quatre cellules — libellé, tonnage de la période de gauche, tonnage de la période de
droite, évolution en pourcentage. On en extrait les lignes, et surtout celle des
**pellets**, seul poste de la nomenclature qui corresponde à la matière première de
l'aciérie de Bellara.

**Deux pièges de la source, tous deux observés :**

- le libellé de la ligne change d'une année sur l'autre : `Pellets` jusqu'en 2023,
  `Pellets / DRI` à partir de 2024. La reconnaissance se fait donc sur un motif, pas sur
  une égalité de chaîne — et le libellé exact est conservé pour que le changement reste
  visible dans les sorties ;
- les nombres sont écrits avec des espaces de toutes sortes (espace ordinaire, insécable
  `\xa0`, `&nbsp;`, fine insécable) comme séparateurs de milliers. Une seule de ces formes
  oubliée et le tonnage est tronqué sans erreur.

**Ce que ce module ne fait pas :** aucune conversion de cumul en trimestre. Les pages
publient des cumuls depuis le 1er janvier ; les différencier est le travail de
`scripts/arrival_dispersion.py`, qui en porte aussi les mises en garde.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from pathlib import Path

#: Ligne et cellule d'un tableau HTML, en tolérant les attributs et les retours à la ligne.
_TR = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_TD = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_BALISE = re.compile(r"<[^>]+>")
#: Toutes les espaces vues en séparateur de milliers, insécables comprises.
_ESPACES = re.compile(r"[\s   ]+")
#: Un tonnage : au moins un chiffre, et rien d'autre que des chiffres une fois nettoyé.
_NOMBRE = re.compile(r"^\d+$")
#: Le poste qui porte le minerai de fer, quel que soit son libellé selon l'année.
PELLETS = re.compile(r"\bpellets?\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class StatRow:
    """Une ligne du tableau : son libellé tel que publié, et ses deux tonnages.

    `gauche` et `droite` sont les deux périodes comparées par la page, dans l'ordre où
    elle les présente (la plus ancienne d'abord). `None` quand la cellule n'est pas un
    nombre — le port laisse parfois un tiret.
    """

    label: str
    gauche: float | None
    droite: float | None


def clean_text(cellule: str) -> str:
    """Contenu lisible d'une cellule : balises retirées, entités décodées, espaces réduites."""
    sans_balises = _BALISE.sub(" ", cellule)
    decode = html_module.unescape(sans_balises)
    return _ESPACES.sub(" ", decode).strip()


def parse_number(texte: str) -> float | None:
    """Tonnage d'une cellule, ou None si ce n'en est pas un.

    Les espaces de milliers sont retirées **avant** le test : « 1 670 113 » est un nombre,
    « – 15 % » n'en est pas un, et « - » non plus.
    """
    compact = _ESPACES.sub("", html_module.unescape(_BALISE.sub("", texte)))
    return float(compact) if _NOMBRE.match(compact) else None


def parse_rows(page_html: str) -> list[StatRow]:
    """Toutes les lignes du tableau de la page, dans l'ordre de publication.

    Ne garde que les lignes à quatre cellules au moins dont la première porte un libellé :
    les lignes de titre et de mise en page en sont écartées d'elles-mêmes.
    """
    lignes = []
    for bloc in _TR.findall(page_html):
        cellules = _TD.findall(bloc)
        if len(cellules) < 3:
            continue
        label = clean_text(cellules[0])
        if not label:
            continue
        lignes.append(StatRow(
            label=label,
            gauche=parse_number(cellules[1]),
            droite=parse_number(cellules[2]),
        ))
    return lignes


def find_row(rows: list[StatRow], motif: re.Pattern[str]) -> StatRow | None:
    """Première ligne dont le libellé correspond au motif ; None si aucune."""
    for ligne in rows:
        if motif.search(ligne.label):
            return ligne
    return None


def pellets_row(page_html: str) -> StatRow | None:
    """La ligne des pellets de la page, libellé d'origine compris ; None si absente.

    Absente veut dire quelque chose : ou bien le port a changé sa nomenclature, ou bien la
    page n'est pas un bilan de débarquement. Dans les deux cas il faut le voir, pas lire
    un zéro.
    """
    return find_row(parse_rows(page_html), PELLETS)


def read_page(path: str | Path) -> str:
    """Lit une page archivée ; le site est en UTF-8, les octets douteux sont remplacés."""
    return Path(path).read_bytes().decode("utf-8", "replace")
