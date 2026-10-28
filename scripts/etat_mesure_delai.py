"""État des lieux : peut-on mesurer `cv_delai` à partir de la collecte ? Sans simulation.

`cv_delai` est la dispersion du délai d'approvisionnement — de la décision d'affréter à
l'arrivée en rade —, et l'étape 6 a montré qu'il pilote à lui seul le chiffre du site. La
question posée ici est modeste : dans les situations portuaires déjà collectées, trouve-t-on
pour chaque navire **une arrivée annoncée et une arrivée observée** ? Avec quel effectif, sur
quelle période, et que vaut un premier chiffre ?

**Ce qu'on apparie.** Pour chaque navire, les instantanés où il figure dans « Expected
Arrivals » donnent une ETA publiée (première et dernière) et la date de la première annonce ;
le premier instantané où il apparaît en rade ou à quai donne son **heure d'arrivée publiée**
(entrée en rade, ou accostage s'il n'est jamais passé par la rade). L'écart arrivée − ETA est
l'**erreur d'annonce**.

**Ce que cela ne mesure pas.** Le délai du modèle commence à la décision d'affréter, que
rien de public ne date. L'erreur d'annonce n'en voit que la **fin** : l'aléa qui reste entre
la publication de l'ETA et l'arrivée, sur un horizon d'annonce de quelques jours. Le cv
provisoire publié ici — écart-type de l'erreur rapporté au délai moyen de l'hypothèse —
est donc une **borne basse** de `cv_delai`, et il emprunte son dénominateur à une hypothèse
sans source.

Usage : python scripts/etat_mesure_delai.py [--observations data/clean/observations.csv]
"""

import argparse
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.transform.observations_csv import read_observations

OBSERVATIONS = Path("data/clean/observations.csv")
CATEGORIE_MINERAI = "iron_ore"
STATUTS_ARRIVES = ("anchorage", "berthed")
#: conversion d'unité, pas une hypothèse de domaine
_SECONDES_PAR_HEURE = 3600.0


@dataclass(frozen=True)
class Appariement:
    """Un navire annoncé puis arrivé, avec ce que la série en dit."""

    vessel: str
    cargo_category: str
    premiere_annonce: datetime  # premier instantané où il est annoncé
    eta_premiere: datetime
    eta_derniere: datetime
    revisions: int  # nombre de changements d'ETA publiés
    arrivee: datetime  # entrée en rade publiée, ou accostage
    statut_arrivee: str

    @property
    def erreur_premiere_h(self) -> float:
        """Arrivée − première ETA, en heures ; positif = en retard sur l'annonce."""
        return _heures(self.arrivee - self.eta_premiere)

    @property
    def erreur_derniere_h(self) -> float:
        return _heures(self.arrivee - self.eta_derniere)

    @property
    def horizon_annonce_h(self) -> float:
        """Combien de temps avant l'ETA le navire a été annoncé pour la première fois."""
        return _heures(self.eta_premiere - self.premiere_annonce)


def _heures(delta) -> float:
    return delta.total_seconds() / _SECONDES_PAR_HEURE


def _cle(obs) -> str:
    return " ".join(obs.vessel.upper().split())


def apparier(observations) -> tuple[list[Appariement], dict[str, int]]:
    """Les navires annoncés puis arrivés, et le décompte de ceux qui ne le sont pas.

    Un navire peut faire plusieurs escales : une annonce se ferme à la première
    observation en rade ou à quai qui la suit, et une nouvelle annonce peut recommencer
    ensuite. Les annonces sans ETA publiée ne comptent pas.
    """
    par_navire: dict[str, list] = {}
    for obs in sorted(observations, key=lambda o: o.source_time_utc):
        par_navire.setdefault(_cle(obs), []).append(obs)
    paires, compte = [], {"annonces_sans_arrivee": 0, "arrivees_sans_annonce": 0}
    for liste in par_navire.values():
        annonces: list = []
        deja_arrive = False
        for obs in liste:
            if obs.status == "expected":
                if obs.event_time is not None:
                    annonces.append(obs)
                deja_arrive = False
                continue
            if obs.status in STATUTS_ARRIVES and not deja_arrive:
                deja_arrive = True
                if not annonces:
                    compte["arrivees_sans_annonce"] += 1
                    continue
                if obs.event_time is None:
                    annonces = []
                    continue
                etas = [a.event_time for a in annonces]
                paires.append(Appariement(
                    vessel=_cle(obs), cargo_category=annonces[0].cargo_category,
                    premiere_annonce=annonces[0].source_time_utc, eta_premiere=etas[0],
                    eta_derniere=etas[-1],
                    revisions=sum(a != b for a, b in pairwise(etas)),
                    arrivee=obs.event_time, statut_arrivee=obs.status))
                annonces = []
        if annonces:
            compte["annonces_sans_arrivee"] += 1
    return paires, compte


def resume(valeurs: list[float]) -> dict[str, float]:
    if not valeurs:
        return {"n": 0}
    sortie = {"n": len(valeurs), "moyenne": statistics.fmean(valeurs),
              "mediane": statistics.median(valeurs), "min": min(valeurs), "max": max(valeurs)}
    if len(valeurs) >= 2:
        sortie["ecart_type"] = statistics.stdev(valeurs)
    return sortie


def cv_provisoire(erreurs_h: list[float], delai_moyen_h: float) -> float | None:
    """Écart-type de l'erreur d'annonce ÷ délai moyen de l'hypothèse : une borne basse."""
    if len(erreurs_h) < 2:
        return None
    return statistics.stdev(erreurs_h) / delai_moyen_h


def _ligne(nom: str, r: dict) -> str:
    if r["n"] == 0:
        return f"    {nom:38} n = 0"
    texte = (f"    {nom:38} n = {r['n']:3}  médiane {r['mediane']:+8.1f}  "
             f"moyenne {r['moyenne']:+8.1f}  [{r['min']:+.1f} ; {r['max']:+.1f}]")
    if "ecart_type" in r:
        texte += f"  écart-type {r['ecart_type']:.1f}"
    return texte


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--observations", type=Path, default=OBSERVATIONS)
    args = parser.parse_args(argv)

    observations = read_observations(args.observations)
    raw = load_yaml(ASSUMPTIONS_PATH)
    delai_moyen_h = float(raw["approvisionnement"]["delai_moyen_j"]["value"]) * 24
    instants = sorted({o.source_time_utc for o in observations})
    paires, compte = apparier(observations)
    print(f"{len(observations)} observations, {len(instants)} instantanés, "
          f"du {instants[0]:%Y-%m-%d} au {instants[-1]:%Y-%m-%d}.")
    print(f"Navires annoncés puis arrivés : {len(paires)} ; annoncés sans arrivée dans la "
          f"série : {compte['annonces_sans_arrivee']} ; arrivés sans annonce préalable "
          f"(dont ceux déjà là au premier instantané) : {compte['arrivees_sans_annonce']}.")
    for nom, groupe in (("toutes cargaisons", paires),
                        ("minerai", [p for p in paires if p.cargo_category == CATEGORIE_MINERAI])):
        print(f"\n  {nom} — heures (positif = arrivée après l'ETA)")
        print(_ligne("arrivée − première ETA", resume([p.erreur_premiere_h for p in groupe])))
        print(_ligne("arrivée − dernière ETA", resume([p.erreur_derniere_h for p in groupe])))
        print(_ligne("horizon d'annonce (ETA − 1re annonce)",
                     resume([p.horizon_annonce_h for p in groupe])))
        revises = sum(p.revisions > 0 for p in groupe)
        print(f"    ETA révisée au moins une fois : {revises} sur {len(groupe)} ; "
              f"arrivée par la rade : {sum(p.statut_arrivee == 'anchorage' for p in groupe)}")
        cv = cv_provisoire([p.erreur_premiere_h for p in groupe], delai_moyen_h)
        print("    cv provisoire (écart-type de l'erreur sur la première ETA ÷ délai moyen "
              f"de l'hypothèse) : {'non calculable' if cv is None else f'{cv:.3f}'}")
    for p in sorted(paires, key=lambda p: p.arrivee):
        if p.cargo_category == CATEGORIE_MINERAI:
            print(f"    minerai : {p.vessel:24} annoncé {p.premiere_annonce:%Y-%m-%d %H:%M}, "
                  f"ETA {p.eta_premiere:%Y-%m-%d %H:%M} → arrivé {p.arrivee:%Y-%m-%d %H:%M} "
                  f"({p.erreur_premiere_h:+.1f} h, {p.revisions} révision(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
