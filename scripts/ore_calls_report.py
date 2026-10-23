"""Rapport sur les escales minerai de la série réelle : durées, cadences, intervalles.

Reconstruit les escales depuis `data/clean/observations.csv`, ne garde que celles dont la
cargaison est catégorisée `iron_ore`, et affiche par navire le tonnage, l'accostage, les
bornes du départ, le séjour à quai, la cadence de déchargement, l'attente en rade et
l'intervalle depuis l'arrivée précédente.

Trois précautions de lecture, parce que ces chiffres servent à calibrer
`navires_minerai.cadence_dechargement` :

- la **cadence** est une cadence apparente sur l'occupation du poste, pas un débit de
  grue : elle inclut l'amarrage, les temps morts et l'attente de rames. C'est bien ce que
  le modèle appelle `cadence_dechargement`, donc l'usage est cohérent, mais ce n'est pas
  une performance d'engin ;
- `berth_hours` étant une borne **basse** (calculée sur `departure_min`), la cadence qui
  s'en déduit est une borne **haute**. La borne basse se calcule sur
  `berth_hours + departure_uncertainty_hours` ;
- le tonnage est celui que le port publie pour l'escale. Rien ne dit qu'il a été déchargé
  en entier à Djen Djen : si le navire repart avec du reste, la cadence est surestimée.

L'**arrivée** est l'entrée en rade (`anchorage_time`) quand elle est observée, sinon
l'accostage (`berth_time`) pour les navires apparus directement à quai ou dont l'arrivée
précède la série (`left_censored`). Les intervalles entre arrivées en héritent.

Usage : python scripts/ore_calls_report.py [--observations data/clean/observations.csv]
        python scripts/ore_calls_report.py --categorie grain
"""

import argparse
import sys
from pathlib import Path

from corridor.transform.build_escales import build_escales
from corridor.transform.observations_csv import read_observations

OBSERVATIONS = Path("data/clean/observations.csv")
CATEGORIE_MINERAI = "iron_ore"

#: conversion d'unité, pas une hypothèse de domaine
_HEURES_PAR_JOUR = 24.0


def arrival_time(escale):
    """Arrivée au port : entrée en rade si observée, sinon accostage ; None si ni l'un ni l'autre.

    Une ETA ne compte pas : c'est une annonce, pas une arrivée.
    """
    return escale.anchorage_time or escale.berth_time


def arrival_source(escale):
    """D'où vient l'arrivée retenue : `rade`, `quai`, ou `aucune` (escale annoncée seulement)."""
    if escale.anchorage_time:
        return "rade"
    return "quai" if escale.berth_time else "aucune"


def select_escales(escales, categorie=CATEGORIE_MINERAI):
    """Escales de la catégorie demandée, triées par arrivée ; celles sans arrivée en dernier."""
    retenues = [e for e in escales if e.cargo_category == categorie]
    return sorted(
        retenues,
        key=lambda e: (arrival_time(e) is None, arrival_time(e) or e.eta or e.departure_min),
    )


def cadence_bounds(escale):
    """Cadences apparentes (basse, haute) en t/jour, ou (None, None) si non calculable.

    La borne haute suppose le départ le plus tôt possible (`departure_min`, donc le séjour
    le plus court), la borne basse le départ le plus tard (`departure_max`). Une escale en
    cours, sans tonnage ou sans séjour mesurable ne donne rien.
    """
    tonnage, heures = escale.tonnage_t, escale.berth_hours
    if not escale.finished or not tonnage or not heures or heures <= 0:
        return None, None
    incertitude = escale.departure_uncertainty_hours or 0.0
    return (
        tonnage / ((heures + incertitude) / _HEURES_PAR_JOUR),
        tonnage / (heures / _HEURES_PAR_JOUR),
    )


def report_rows(escales):
    """Une ligne par escale, dans l'ordre reçu, avec l'intervalle depuis l'arrivée précédente.

    L'intervalle se mesure d'une arrivée datée à la suivante : une escale sans arrivée
    n'interrompt pas la chaîne, elle n'y figure simplement pas.
    """
    lignes = []
    precedente = None
    for escale in escales:
        arrivee = arrival_time(escale)
        basse, haute = cadence_bounds(escale)
        intervalle = None
        if arrivee is not None:
            if precedente is not None:
                intervalle = (arrivee - precedente).total_seconds() / 3600.0
            precedente = arrivee
        lignes.append(
            {
                "vessel": escale.vessel,
                "cargo_label": escale.cargo_label,
                "dock": escale.dock,
                "tonnage_t": escale.tonnage_t,
                "arrivee": arrivee,
                "arrivee_source": arrival_source(escale),
                "eta": escale.eta,
                "berth_time": escale.berth_time,
                "departure_min": escale.departure_min,
                "departure_max": escale.departure_max,
                "incertitude_h": escale.departure_uncertainty_hours,
                "berth_hours": escale.berth_hours,
                "wait_hours": escale.wait_hours,
                "cadence_basse": basse,
                "cadence_haute": haute,
                "intervalle_h": intervalle,
                "finished": escale.finished,
                "long_stay": escale.long_stay,
                "anomaly": escale.anomaly,
                "n_snapshots": escale.n_snapshots,
            }
        )
    return lignes


def cadence_summary(lignes):
    """Synthèse des cadences : moyenne des valeurs centrales, et enveloppe de toutes les bornes.

    `moyenne` est la moyenne des milieux de fourchette, un par navire : c'est elle qui
    alimente la valeur de l'hypothèse. `minimum` et `maximum` enveloppent **toutes** les
    bornes mesurées, donc donnent la fourchette de l'hypothèse.
    """
    mesurees = [
        (x["cadence_basse"], x["cadence_haute"])
        for x in lignes
        if x["cadence_basse"] is not None
    ]
    if not mesurees:
        return {"n": 0, "moyenne": None, "minimum": None, "maximum": None, "centres": []}
    centres = [(basse + haute) / 2 for basse, haute in mesurees]
    return {
        "n": len(mesurees),
        "moyenne": sum(centres) / len(centres),
        "minimum": min(basse for basse, _ in mesurees),
        "maximum": max(haute for _, haute in mesurees),
        "centres": centres,
    }


def _nombre(valeur, decimales=1):
    """Nombre avec espace fine comme séparateur de milliers, ou tiret si absent."""
    if valeur is None:
        return "—"
    return f"{valeur:,.{decimales}f}".replace(",", " ")


def _date(valeur):
    return f"{valeur:%Y-%m-%d %H:%M}" if valeur is not None else "—"


def _jours(heures):
    return None if heures is None else heures / _HEURES_PAR_JOUR


def afficher(lignes, categorie=CATEGORIE_MINERAI):
    """Imprime le détail par escale, les intervalles entre arrivées, puis les cadences."""
    print(f"== Escales « {categorie} » : {len(lignes)} ==")
    for x in lignes:
        etat = "terminée" if x["finished"] else "EN COURS"
        print(f"\n{x['vessel']}  [{etat}]  {x['cargo_label']}  poste {x['dock'] or '—'}")
        print(f"  tonnage publié      {_nombre(x['tonnage_t'], 0)} t")
        print(f"  arrivée ({x['arrivee_source']:6})    {_date(x['arrivee'])}"
              f"    ETA {_date(x['eta'])}")
        print(f"  accostage           {_date(x['berth_time'])}")
        print(f"  départ encadré      {_date(x['departure_min'])} → {_date(x['departure_max'])}"
              f"  (fenêtre {_nombre(x['incertitude_h'])} h)")
        print(f"  séjour à quai       ≥ {_nombre(x['berth_hours'])} h "
              f"(≥ {_nombre(_jours(x['berth_hours']), 2)} j, borne basse)")
        print(f"  attente en rade     {_nombre(x['wait_hours'])} h "
              f"({_nombre(_jours(x['wait_hours']), 2)} j)")
        print(f"  cadence apparente   {_nombre(x['cadence_basse'], 0)} "
              f"à {_nombre(x['cadence_haute'], 0)} t/jour")
        print(f"  intervalle arrivées {_nombre(x['intervalle_h'])} h "
              f"({_nombre(_jours(x['intervalle_h']), 1)} j depuis l'arrivée précédente)")
        print(f"  instantanés {x['n_snapshots']}   anomalies {x['anomaly'] or '—'}")

    intervalles = [x["intervalle_h"] for x in lignes if x["intervalle_h"] is not None]
    print("\n== Intervalles entre arrivées ==")
    if intervalles:
        jours = ", ".join(_nombre(_jours(v), 1) for v in intervalles)
        print(f"  {len(intervalles)} intervalle(s), en jours : {jours}")
        print("  trop peu pour en tirer une dispersion : voir la note de dispersion_arrivees")
    else:
        print("  moins de deux arrivées datées")

    synthese = cadence_summary(lignes)
    print("\n== Cadence de déchargement apparente ==")
    if not synthese["n"]:
        print("  aucune escale terminée exploitable")
        return
    centres = ", ".join(_nombre(v, 0) for v in synthese["centres"])
    print(f"  {synthese['n']} escale(s) mesurée(s) ; centre par navire : {centres} t/jour")
    print(f"  moyenne des centres  : {_nombre(synthese['moyenne'], 0)} t/jour")
    print(f"  enveloppe des bornes : {_nombre(synthese['minimum'], 0)} "
          f"à {_nombre(synthese['maximum'], 0)} t/jour")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--observations", type=Path, default=OBSERVATIONS)
    parser.add_argument("--categorie", default=CATEGORIE_MINERAI)
    args = parser.parse_args(argv)

    if not args.observations.exists():
        print(f"observations absentes ({args.observations}) : "
              "python -m corridor.transform.observations_csv --rebuild")
        return 1

    escales = build_escales(read_observations(args.observations))
    afficher(report_rows(select_escales(escales, args.categorie)), args.categorie)
    return 0


if __name__ == "__main__":
    sys.exit(main())
