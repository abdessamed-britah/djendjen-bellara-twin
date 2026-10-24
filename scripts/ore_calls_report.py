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


def berth_busy_intervals(escales, dock, exclure=None):
    """Intervalles où le poste `dock` est occupé, **tous navires confondus**.

    Pas seulement les minéraliers : QW/7 reçoit aussi d'autres cargaisons, et un navire
    de marchandises diverses à quai bloque la file tout autant. L'intervalle va de
    l'accostage publié à `departure_min`, qui est une **borne basse** du départ : la durée
    d'occupation est donc sous-estimée, et le temps « poste libre » qui s'en déduit est
    une **borne haute**. C'est le sens prudent pour ce qu'on veut en faire.
    """
    for escale in escales:
        if escale is exclure or escale.dock != dock or escale.berth_time is None:
            continue
        fin = escale.departure_min
        if fin is None or fin <= escale.berth_time:
            continue
        yield (escale.berth_time, fin, escale)


def _recouvrement_h(debut_a, fin_a, debut_b, fin_b):
    """Heures communes à deux intervalles ; 0 s'ils ne se touchent pas."""
    debut = max(debut_a, debut_b)
    fin = min(fin_a, fin_b)
    return max((fin - debut).total_seconds() / 3600.0, 0.0)


def free_berth_wait(escale, escales, debut_serie=None):
    """Attente en rade décomposée en « poste occupé » et « poste libre ».

    **Le signal observé que cherche l'hypothèse H2.** Si un minéralier attend en rade
    alors que son poste est vide, ce n'est pas le poste qui le retient : c'est l'aval —
    un stockyard plein, et donc un quai qu'on ne veut pas occuper. Chaque heure d'attente
    à poste libre est une heure que l'explication « le poste est le goulot » ne couvre pas.

    **Le piège à ne pas tomber dedans.** Avant le premier instantané de la série, on ne
    sait pas qui occupait le poste : un navire a pu y être sans qu'on le voie jamais. Les
    heures « libres » antérieures à `debut_serie` sont donc **non observées**, pas libres,
    et elles sont comptées à part (`free_unobserved_h`). Seules les heures libres
    postérieures au premier instantané valent comme signal.

    Renvoie None si l'escale n'a pas d'attente mesurable (pas d'entrée en rade observée,
    ou accostage direct). Les intervalles d'occupation sont fusionnés avant d'être
    retranchés, pour qu'un poste occupé par deux navires successifs ne compte pas double.
    """
    if escale.anchorage_time is None or escale.berth_time is None:
        return None
    debut, fin = escale.anchorage_time, escale.berth_time
    total = (fin - debut).total_seconds() / 3600.0
    if total <= 0:
        return None

    occupants = []
    morceaux = []
    for debut_b, fin_b, autre in berth_busy_intervals(escales, escale.dock, exclure=escale):
        if _recouvrement_h(debut, fin, debut_b, fin_b) <= 0:
            continue
        morceaux.append((max(debut, debut_b), min(fin, fin_b)))
        occupants.append(autre)

    occupe = _union_heures(morceaux)
    libre = max(total - occupe, 0.0)
    # part du « libre » qui tombe dans la fenêtre de départ d'un occupant : le navire
    # précédent y était peut-être encore, le poste n'y est donc pas démontré vide
    fenetres = []
    for autre in escales:
        if autre is escale or autre.dock != escale.dock:
            continue
        if autre.departure_min is None or autre.departure_max is None:
            continue
        fenetres.append((max(debut, autre.departure_min), min(fin, autre.departure_max)))
    douteux = _union_heures([(a, b) for a, b in fenetres if b > a])
    douteux = min(douteux, libre)
    # part de l'attente antérieure au premier instantané : le poste y est inconnu, pas libre
    if debut_serie is not None and debut_serie > debut:
        aveugle = _recouvrement_h(debut, fin, debut, min(debut_serie, fin))
        non_observe = max(aveugle - _union_heures(
            [(a, b) for a, b in morceaux if a < debut_serie]
        ), 0.0)
        non_observe = min(non_observe, libre)
    else:
        non_observe = 0.0
    observe = max(libre - non_observe, 0.0)
    douteux = min(douteux, observe)
    return {
        "wait_total_h": total,
        "berth_busy_h": occupe,
        "berth_free_h": libre,
        "free_unobserved_h": non_observe,
        "free_observed_h": observe,
        # heures « libres » qui tombent dans la fenêtre de départ du navire précédent :
        # elles ne démontrent rien, le poste y était peut-être encore occupé
        "free_in_departure_window_h": douteux,
        "free_certain_h": max(observe - douteux, 0.0),
        "occupants": occupants,
    }


def _union_heures(intervalles):
    """Durée totale de l'union d'intervalles, en heures : les chevauchements ne comptent qu'une fois."""
    fusionnes = []
    for debut, fin in sorted(intervalles):
        if fusionnes and debut <= fusionnes[-1][1]:
            fusionnes[-1] = (fusionnes[-1][0], max(fusionnes[-1][1], fin))
        else:
            fusionnes.append((debut, fin))
    return sum((fin - debut).total_seconds() / 3600.0 for debut, fin in fusionnes)


def report_rows(escales, toutes=None, debut_serie=None):
    """Une ligne par escale, dans l'ordre reçu, avec l'intervalle depuis l'arrivée précédente.

    L'intervalle se mesure d'une arrivée datée à la suivante : une escale sans arrivée
    n'interrompt pas la chaîne, elle n'y figure simplement pas.

    `toutes` est la liste complète des escales, toutes cargaisons confondues : elle sert
    à savoir si le poste était libre pendant l'attente (voir `free_berth_wait`). Sans
    elle, un navire de marchandises diverses à quai passerait pour un poste vide.
    """
    reference = list(toutes) if toutes is not None else list(escales)
    lignes = []
    precedente = None
    for escale in escales:
        arrivee = arrival_time(escale)
        basse, haute = cadence_bounds(escale)
        libre = free_berth_wait(escale, reference, debut_serie)
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
                "wait_total_h": libre["wait_total_h"] if libre else None,
                "berth_busy_h": libre["berth_busy_h"] if libre else None,
                "berth_free_h": libre["berth_free_h"] if libre else None,
                "free_unobserved_h": libre["free_unobserved_h"] if libre else None,
                "free_observed_h": libre["free_observed_h"] if libre else None,
                "free_in_departure_window_h": (
                    libre["free_in_departure_window_h"] if libre else None),
                "free_certain_h": libre["free_certain_h"] if libre else None,
                "occupants": [x.vessel for x in libre["occupants"]] if libre else [],
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


def afficher(lignes, categorie=CATEGORIE_MINERAI, debut_serie=None):
    """Imprime le détail par escale, les intervalles, le signal H2, puis les cadences."""
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
        if x["berth_free_h"] is not None:
            occupants = ", ".join(sorted(set(x["occupants"]))) or "personne"
            print(f"    dont poste occupé {_nombre(x['berth_busy_h'])} h "
                  f"(par : {occupants})")
            print(f"    dont poste LIBRE  {_nombre(x['free_observed_h'])} h "
                  f"({_nombre(_jours(x['free_observed_h']), 2)} j) — signal H2")
            if x["free_unobserved_h"]:
                print(f"    dont poste inconnu {_nombre(x['free_unobserved_h'])} h "
                      "(avant le premier instantané : on ne sait pas qui l'occupait)")
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

    afficher_signal_h2(lignes, debut_serie)

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


def afficher_signal_h2(lignes, debut_serie=None):
    """Synthèse du signal observé de H2 : attendre en rade alors que le poste est libre.

    H2 dit que le quai n'est pas saturé par sa propre cadence mais par l'aval. Dans le
    modèle, cela se mesure par `berth_blocked_h`. Dans la série réelle, le signal
    équivalent est son miroir : un minéralier qui attend **pendant que son poste est
    vide**. Chaque heure de ce genre est une heure que l'explication « le poste est le
    goulot » ne couvre pas.
    """
    mesurables = [x for x in lignes if x["berth_free_h"] is not None]
    print("\n== Signal observé de H2 : attente à poste libre ==")
    if not mesurables:
        print("  aucune attente en rade mesurable dans la série "
              "(il faut une entrée en rade ET un accostage observés)")
        return
    if debut_serie is not None:
        print(f"  Premier instantané de la série : {_date(debut_serie)}. Avant lui, "
              "l'occupation\n  du poste est inconnue, pas libre.")

    total = sum(x["wait_total_h"] for x in mesurables)
    occupe = sum(x["berth_busy_h"] for x in mesurables)
    certain = sum(x["free_certain_h"] for x in mesurables)
    douteux = sum(x["free_in_departure_window_h"] for x in mesurables)
    inconnu = sum(x["free_unobserved_h"] for x in mesurables)
    for x in mesurables:
        print(f"\n  {x['vessel']} (poste {x['dock'] or '—'})")
        print(f"    attente totale     {_nombre(x['wait_total_h'])} h")
        print(f"    poste occupé       {_nombre(x['berth_busy_h'])} h "
              f"(par : {', '.join(sorted(set(x['occupants']))) or 'personne'})")
        print(f"    poste LIBRE        {_nombre(x['free_certain_h'])} h  <-- signal H2")
        if x["free_in_departure_window_h"]:
            print(f"    poste douteux      {_nombre(x['free_in_departure_window_h'])} h "
                  "(dans la fenêtre de départ du navire précédent)")
        if x["free_unobserved_h"]:
            print(f"    poste inconnu      {_nombre(x['free_unobserved_h'])} h "
                  "(avant le premier instantané)")

    print(f"\n  Total sur {len(mesurables)} attente(s) mesurable(s) : {_nombre(total)} h "
          "d'attente, dont")
    print(f"  {_nombre(occupe)} h à poste démontré occupé, {_nombre(certain)} h à poste "
          f"démontré libre,")
    print(f"  {_nombre(douteux)} h dans une fenêtre de départ et {_nombre(inconnu)} h "
          "avant le début de la série.")
    part = certain / total * 100 if total else 0.0
    print(f"\n  Soit {part:.1f} % de l'attente passée en rade alors que le quai était "
          "démontré vide.")
    if certain <= 0:
        print("  **La série ne montre donc aucune attente à poste libre.** Ce qui en avait "
              "l'air")
        print("  tombe entièrement dans la fenêtre de départ du navire précédent ou avant "
              "le premier")
        print("  instantané : le poste y était peut-être encore occupé, on ne peut rien en "
              "conclure.")
        print("  C'est un résultat, pas une absence de résultat : sur cette série, rien ne "
              "contredit")
        print("  l'idée que le poste est pris dès qu'un navire attend. H2 n'y gagne ni n'y "
              "perd.")

    print("\n  Trois réserves, et elles sont lourdes :")
    print("  1. Une seule attente est mesurable sur toute la série minerai. Quel que soit "
          "le")
    print("     résultat, un cas n'est pas une statistique.")
    print("  2. L'occupation du poste est bornée par `departure_min`, une borne basse du "
          "départ :")
    print("     le temps « poste libre » qui s'en déduit est donc une borne haute.")
    print("  3. Un poste vide n'est pas un poste disponible : accostage de nuit, pilotage, "
          "marée,")
    print("     préparation du quai — ou un stockyard plein qu'on ne voit pas. Le signal "
          "est")
    print("     compatible avec H2, il ne la démontre pas.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--observations", type=Path, default=OBSERVATIONS)
    parser.add_argument("--categorie", default=CATEGORIE_MINERAI)
    args = parser.parse_args(argv)

    if not args.observations.exists():
        print(f"observations absentes ({args.observations}) : "
              "python -m corridor.transform.observations_csv --rebuild")
        return 1

    observations = read_observations(args.observations)
    debut = min((o.source_time_utc for o in observations), default=None)
    escales = build_escales(observations)
    retenues = select_escales(escales, args.categorie)
    afficher(report_rows(retenues, toutes=escales, debut_serie=debut), args.categorie, debut)
    return 0


if __name__ == "__main__":
    sys.exit(main())
