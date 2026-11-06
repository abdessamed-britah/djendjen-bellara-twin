"""Test FIFO sur les escales observées : l'ordre d'accostage suit-il l'ordre d'arrivée en rade ?

Sans simulation. Sur les escales reconstruites (`build_escales`), on garde les **vraquiers**
(type de navire majoritaire de leurs observations) passés par la rade, et l'on compare, pour
chaque paire de navires qui ont attendu **en même temps** — le second est arrivé en rade avant
que le premier ait accosté —, l'ordre d'arrivée et l'ordre d'accostage. Un navire encore en
rade à la fin de la série compte : si un navire arrivé après lui a accosté, c'est un
dépassement observé.

**Deux ordres, deux hypothèses du modèle, à ne pas confondre.**

- **La file au quai** : le modèle la traite en premier arrivé, premier servi (une ressource
  SimPy), dans les deux variantes. C'est ce que mesure le test principal.
- **Les dépassements en mer** (`simulation.depassements_autorises`, « permis » ou
  « interdits ») portent sur l'ordre **commande → arrivée en rade** : un navire commandé
  après un autre arrive-t-il avant lui ? La date de commande n'est publiée nulle part. Le seul
  indice public est l'ordre de **première annonce** dans « Expected Arrivals » comparé à
  l'ordre d'arrivée : un indicateur indirect, sur un horizon d'annonce d'un jour et demi en
  médiane (étape 7), qui mesure surtout l'erreur d'ETA.

Usage : python scripts/ordre_accostage.py [--observations data/clean/observations.csv]
"""

import argparse
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

from corridor.transform.build_escales import build_escales
from corridor.transform.observations_csv import read_observations

OBSERVATIONS = Path("data/clean/observations.csv")
TYPE_VRAQUIER = "VRAQUIER"
#: Quantile de la loi normale pour un intervalle de Wilson à 95 %.
_Z95 = 1.959964


@dataclass(frozen=True)
class Attente:
    vessel: str
    cargo: str
    rade: datetime
    quai: datetime | None  # None : encore en rade à la fin de la série
    poste: str | None


def _cle(nom: str) -> str:
    return " ".join(nom.upper().split())


def types_de_navire(observations) -> dict[str, str]:
    """Le type de navire majoritaire de chaque nom."""
    compte: dict[str, Counter] = {}
    for o in observations:
        compte.setdefault(_cle(o.vessel), Counter())[o.shiptype.upper()] += 1
    return {nom: c.most_common(1)[0][0] for nom, c in compte.items()}


def attentes_vraquiers(escales, types: dict[str, str]) -> list[Attente]:
    return sorted((Attente(e.vessel, e.cargo_category, e.anchorage_time, e.berth_time, e.dock)
                   for e in escales
                   if e.anchorage_time is not None
                   and types.get(_cle(e.vessel), "").startswith(TYPE_VRAQUIER)),
                  key=lambda a: a.rade)


def paires_concurrentes(attentes: list[Attente], fin_serie: datetime) -> list[dict]:
    """Les paires (premier arrivé, second arrivé) qui ont attendu en même temps."""
    paires = []
    for a, b in combinations(attentes, 2):
        premier, second = (a, b) if a.rade <= b.rade else (b, a)
        if second.rade == premier.rade:
            continue
        fin_attente = premier.quai or fin_serie
        if second.rade >= fin_attente:
            continue
        if second.quai is None:
            depasse = None if premier.quai is None else False
        elif premier.quai is None:
            depasse = True
        else:
            depasse = second.quai < premier.quai
        if depasse is None:
            continue  # les deux encore en rade : l'ordre d'accostage n'est pas observé
        paires.append({
            "premier": premier.vessel, "second": second.vessel, "depasse": depasse,
            "meme_poste": (premier.poste == second.poste
                           if premier.poste and second.poste and premier.quai and second.quai
                           else None),
            "meme_cargaison": premier.cargo == second.cargo,
        })
    return paires


def wilson(k: int, n: int) -> tuple[float, float]:
    """Intervalle de Wilson à 95 % d'une proportion k/n."""
    if n == 0:
        return math.nan, math.nan
    p = k / n
    centre = (p + _Z95 ** 2 / (2 * n)) / (1 + _Z95 ** 2 / n)
    demi = _Z95 * math.sqrt(p * (1 - p) / n + _Z95 ** 2 / (4 * n ** 2)) / (1 + _Z95 ** 2 / n)
    return centre - demi, centre + demi


def resume(paires: list[dict]) -> dict:
    k = sum(p["depasse"] for p in paires)
    return {"n": len(paires), "depassements": k, "ic": wilson(k, len(paires))}


def annonces_et_arrivees(observations, types: dict[str, str]) -> list[tuple[str, datetime, datetime]]:
    """(navire, première annonce, arrivée publiée) des vraquiers annoncés puis arrivés."""
    premiere, arrivee = {}, {}
    for o in sorted(observations, key=lambda o: o.source_time_utc):
        nom = _cle(o.vessel)
        if not types.get(nom, "").startswith(TYPE_VRAQUIER):
            continue
        if o.status == "expected":
            premiere.setdefault(nom, o.source_time_utc)
        elif nom in premiere and nom not in arrivee and o.event_time is not None:
            arrivee[nom] = o.event_time
    return [(n, premiere[n], arrivee[n]) for n in arrivee]


def inversions_en_mer(couples) -> dict:
    """Paires annoncées dans un ordre et arrivées dans l'autre (annonce à des instants distincts)."""
    paires = [(a, b) for a, b in combinations(couples, 2) if a[1] != b[1]]
    k = sum((a[1] < b[1]) != (a[2] < b[2]) for a, b in paires)
    return {"n": len(paires), "depassements": k, "ic": wilson(k, len(paires))}


def _ligne(nom: str, r: dict) -> str:
    if r["n"] == 0:
        return f"  {nom:52} aucune paire"
    bas, haut = r["ic"]
    return (f"  {nom:52} {r['depassements']:3} sur {r['n']:3} = {r['depassements'] / r['n']:.0%}"
            f"  [Wilson 95 % : {bas:.0%} ; {haut:.0%}]")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--observations", type=Path, default=OBSERVATIONS)
    args = parser.parse_args(argv)

    observations = read_observations(args.observations)
    fin = max(o.source_time_utc for o in observations)
    debut = min(o.source_time_utc for o in observations)
    types = types_de_navire(observations)
    attentes = attentes_vraquiers(build_escales(observations), types)
    paires = paires_concurrentes(attentes, fin)
    print(f"Série du {debut:%Y-%m-%d} au {fin:%Y-%m-%d} : {len(attentes)} vraquiers passés par "
          f"la rade ({sum(a.quai is None for a in attentes)} encore en rade à la fin), "
          f"{len(paires)} paires ayant attendu en même temps.")
    print("\nFile au quai — le second arrivé a-t-il accosté avant le premier ?")
    print(_ligne("toutes les paires", resume(paires)))
    for nom, filtre in (
        ("même poste", lambda p: p["meme_poste"] is True),
        ("postes différents", lambda p: p["meme_poste"] is False),
        ("même catégorie de cargaison", lambda p: p["meme_cargaison"]),
        ("catégories différentes", lambda p: not p["meme_cargaison"]),
    ):
        print(_ligne(nom, resume([p for p in paires if filtre(p)])))
    for p in paires:
        if p["depasse"]:
            print(f"    dépassement : {p['second']} (arrivé second) avant {p['premier']}"
                  f" — même poste : {p['meme_poste']}, même cargaison : {p['meme_cargaison']}")
    print("\nOrdre en mer (indicateur indirect) — annoncé le premier, arrivé le premier ?")
    couples = annonces_et_arrivees(observations, types)
    print(_ligne(f"vraquiers annoncés puis arrivés ({len(couples)})", inversions_en_mer(couples)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
