"""Attribution des ruptures : d'où viennent-elles, et que valent les interprétations ?

L'étape 7 a montré que commander pendant les arrêts du four ne récupère presque rien, et en
a tiré une lecture non démontrée : les ruptures dues aux arrêts viendraient **de la pleine
cadence après la reprise**. On la soumet ici à un test, épisode par épisode.

**Le journal** (`corridor.sim.model.journal_des_ruptures`, sans effet sur la trajectoire).
Un épisode de rupture est une suite d'heures où le four devrait tourner et n'a pas de
pellets. Pour chacun : le temps écoulé depuis la dernière reprise du four (fin d'un arrêt
planifié ou non), la position de stock à cette reprise et au début de l'épisode, le contenu
de la chaîne en transit (à flot + rail) en jours de consommation moyenne, et **le plus grand
retard d'arrivée parmi les commandes récentes** : retard = arrivée en rade − (commande +
délai moyen), commandes récentes = celles dont l'arrivée était attendue dans le délai moyen
qui précède l'épisode. Distribution de référence : les heures de marche, par âge depuis la
dernière reprise. Sans elle, on ne saurait pas si les ruptures *se concentrent* après une
reprise ou s'y trouvent seulement parce que le four y tourne.

**Les classes**, exclusives et dans cet ordre : (i) l'épisode commence dans les N jours qui
suivent une reprise ; (ii) sinon, il est précédé d'un retard supérieur au 90e centile des
retards de la même variante ; (iii) autre. Les recouvrements sont publiés à part.

**Le choix de N, sur la distribution.** N est l'âge qui **maximise l'écart** entre la part des
épisodes et la part des heures de marche commencés avant lui (statistique de
Kolmogorov-Smirnov du côté de l'hypothèse) : c'est le N **le plus favorable** à
l'interprétation testée, de sorte qu'un rejet ne peut pas tenir à un N mal choisi. Le N
mécanique — un délai moyen, au-delà duquel une commande passée à la reprise est arrivée —
est publié à côté.

**La règle, écrite avant le calcul.** Risque relatif = part des épisodes dans les N jours ÷
part des heures de marche dans les N jours, IC à 95 % par bootstrap sur les réplications.
L'interprétation « pleine cadence après la reprise » est **confirmée** si, en discret, la
classe (i) réunit au moins la moitié des épisodes et que le risque relatif vaut au moins 2,
borne basse comprise ; **infirmée** si le risque relatif ne dépasse pas 1,25, borne haute
comprise ; **non tranchée** sinon. La politique (d) — relever s avant la reprise — n'est
testée que si elle est confirmée.

Usage : python scripts/attribution_ruptures.py [--reps 200] [--processus 12]
"""

import argparse
import bisect
import dataclasses
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.labels import LANGUES, chemin_langue, libelle
from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.model import journal_des_ruptures
from corridor.sim.run import ECHAUFFEMENT_STATIONNAIRE_ANS, FENETRE_STATIONNAIRE_ANS


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fe = _charger("factoriel_endogene")

VARIANTES = fe.VARIANTES
S_J = 35
#: Âges candidats pour N, en jours entiers.
AGES_N_J = tuple(range(1, 61))
CENTILE_RETARD = 0.90
#: Pas d'échantillonnage de la référence de la classe (ii), en heures : à quelle fréquence on
#: regarde, hors de tout épisode, le plus grand retard parmi les commandes récentes.
PAS_REFERENCE_H = 5 * 24
#: Règle écrite avant le calcul.
PART_MINIMALE_I = 0.50
RISQUE_CONFIRME = 2.0
RISQUE_INFIRME = 1.25
DEFAULT_REPS = 200
DEFAULT_BOOTSTRAP = 1000
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/attribution_ruptures.png")
DEFAULT_JSON = Path("data/sim/attribution_ruptures.json")


def configuration(raw, mode: str, depassements: bool):
    base = fe.configuration(raw, mode, depassements, postes=1, stockage="base")
    return dataclasses.replace(base, point_de_commande_j=float(S_J),
                               point_de_commande_t=S_J * base.debit_pellets_t_par_h * 24)


def _journal(tache):
    config, graine = tache
    return journal_des_ruptures(config, graine, ECHAUFFEMENT_STATIONNAIRE_ANS
                                + FENETRE_STATIONNAIRE_ANS, ECHAUFFEMENT_STATIONNAIRE_ANS)


def journaux(config, reps: int, seed: int, processus: int) -> list[dict]:
    """Un journal par réplication, mêmes graines que `replicate` à graine maîtresse égale."""
    maitre = random.Random(seed)
    taches = [(config, maitre.randrange(2**32)) for _ in range(reps)]
    if processus <= 1:
        return [_journal(t) for t in taches]
    with ProcessPoolExecutor(max_workers=min(processus, reps)) as executeur:
        return list(executeur.map(_journal, taches, chunksize=max(1, reps // (4 * processus))))


# --- réduction d'un journal ------------------------------------------------------------


def retards_j(journal: dict, delai_moyen_h: float) -> list[tuple[float, float]]:
    """(arrivée attendue, retard en jours) de chaque commande arrivée."""
    return sorted(((t0 + delai_moyen_h, (arrivee - t0 - delai_moyen_h) / 24)
                   for t0, arrivee, _ in journal["commandes"]))


def retard_recent_max(attendus, cles, instant_h: float, delai_moyen_h: float) -> float | None:
    """Le plus grand retard parmi les commandes attendues dans le délai moyen avant `instant_h`."""
    i = bisect.bisect_right(cles, instant_h)
    j = bisect.bisect_left(cles, instant_h - delai_moyen_h)
    recents = [r for _, r in attendus[j:i]]
    return max(recents) if recents else None


def caracteriser(journal: dict, delai_moyen_h: float, conso_jour_t: float,
                 pas_reference_h: float = PAS_REFERENCE_H) -> dict:
    """Les grandeurs de chaque épisode, et les deux références, pour une réplication.

    Référence de la classe (i) : les heures de marche par âge depuis la reprise (journal).
    Référence de la classe (ii) : le plus grand retard récent, relevé tous les
    `pas_reference_h` sur la fenêtre mesurée, qu'il y ait rupture ou non.
    """
    attendus = retards_j(journal, delai_moyen_h)
    cles = [a for a, _ in attendus]
    reprises = journal["reprises"]
    heures_reprises = [h for h, _ in reprises]
    episodes = []
    for e in journal["episodes"]:
        debut = e["debut_h"]
        retard_max = retard_recent_max(attendus, cles, debut, delai_moyen_h)
        k = bisect.bisect_right(heures_reprises, e["derniere_reprise_h"]) - 1
        position_reprise = (reprises[k][1] / conso_jour_t
                            if k >= 0 and heures_reprises[k] == e["derniere_reprise_h"] else None)
        episodes.append({
            "age_j": (debut - e["derniere_reprise_h"]) / 24,
            "duree_h": e["duree_h"],
            "position_reprise_j": position_reprise,
            "position_j": e["position_t"] / conso_jour_t,
            "transit_j": e["transit_t"] / conso_jour_t,
            "retard_max_j": retard_max,
        })
    retards_mesures = [r for a, r in attendus if a >= journal["debut_h"]]
    instants = []
    instant = journal["debut_h"]
    while instant < journal["fin_h"]:
        instants.append(instant)
        instant += pas_reference_h
    reference = [retard_recent_max(attendus, cles, x, delai_moyen_h) for x in instants]
    return {"episodes": episodes, "retards_j": retards_mesures,
            "retards_reference_j": [None if r is None else round(r, 2) for r in reference],
            "heures_par_age_j": {int(a): h for a, h in journal["heures_marche_par_age_j"].items()}}


# --- analyse ---------------------------------------------------------------------------


def repartition(ages: list[float], n_j: int) -> float:
    return sum(a < n_j for a in ages) / len(ages) if ages else float("nan")


def repartition_heures(heures: dict[int, float], n_j: int) -> float:
    total = sum(heures.values())
    return sum(h for a, h in heures.items() if a < n_j) / total if total else float("nan")


def choisir_n(ages: list[float], heures: dict[int, float], candidats=AGES_N_J) -> int:
    """Le N le plus favorable à l'hypothèse : max de F_épisodes(N) − F_heures(N)."""
    return max(candidats, key=lambda n: repartition(ages, n) - repartition_heures(heures, n))


def centile(valeurs: list[float], q: float) -> float:
    triees = sorted(valeurs)
    return triees[min(len(triees) - 1, int(q * len(triees)))]


def classer(episode: dict, n_j: int, seuil_retard_j: float) -> str:
    if episode["age_j"] < n_j:
        return "i"
    if episode["retard_max_j"] is not None and episode["retard_max_j"] > seuil_retard_j:
        return "ii"
    return "iii"


def _depasse(retard: float | None, seuil: float) -> bool:
    return retard is not None and retard > seuil


def _agreger(reps: list[dict]) -> tuple[list[dict], dict[int, float], list[float]]:
    episodes = [e for r in reps for e in r["episodes"]]
    heures: dict[int, float] = {}
    for r in reps:
        for a, h in r["heures_par_age_j"].items():
            heures[a] = heures.get(a, 0.0) + h
    return episodes, heures, [x for r in reps for x in r["retards_j"]]


def mesures(reps: list[dict], n_j: int, seuil_retard_j: float) -> dict[str, float]:
    """Parts des classes, risque relatif et recouvrement, sur un ensemble de réplications."""
    episodes, heures, _ = _agreger(reps)
    if not episodes:
        return {"n": 0}
    classes = [classer(e, n_j, seuil_retard_j) for e in episodes]
    part_i = repartition([e["age_j"] for e in episodes], n_j)
    reference = repartition_heures(heures, n_j)
    retard = [_depasse(e["retard_max_j"], seuil_retard_j) for e in episodes]
    echantillon = [r for rep in reps for r in rep["retards_reference_j"]]
    reference_ii = sum(_depasse(r, seuil_retard_j) for r in echantillon) / len(echantillon)
    heures_rupture = sum(e["duree_h"] for e in episodes)
    sortie = {
        "n": len(episodes),
        "reference": reference,
        "risque": part_i / reference if reference > 0 else float("nan"),
        "retard_tous": sum(retard) / len(retard),
        "reference_ii": reference_ii,
        "risque_ii": (sum(retard) / len(retard)) / reference_ii if reference_ii else float("nan"),
        "i_et_retard": sum(c == "i" and r for c, r in zip(classes, retard, strict=True))
        / len(classes),
    }
    for classe in ("i", "ii", "iii"):
        sortie[classe] = classes.count(classe) / len(classes)
        # pondérée par les heures de rupture : ce sont elles qui font la perte
        sortie[f"{classe}_h"] = sum(e["duree_h"] for e, c in zip(episodes, classes, strict=True)
                                    if c == classe) / heures_rupture
    return sortie


def bootstrap(reps: list[dict], n_j: int, seuil_retard_j: float, tirages: int,
              seed: int) -> dict[str, tuple[float, float]]:
    rng = random.Random(seed)
    echantillons = [mesures([reps[rng.randrange(len(reps))] for _ in reps], n_j, seuil_retard_j)
                    for _ in range(tirages)]
    sortie = {}
    for cle in ("i", "ii", "iii", "i_h", "ii_h", "iii_h", "risque", "risque_ii"):
        valeurs = sorted(e[cle] for e in echantillons if e.get("n") and not math.isnan(e[cle]))
        if valeurs:
            sortie[cle] = (valeurs[int(0.025 * len(valeurs))],
                           valeurs[int(0.975 * len(valeurs)) - 1])
    return sortie


def verdict(m: dict, ic: dict) -> str:
    """La règle écrite avant le calcul, sur une variante discrète."""
    if m["i"] >= PART_MINIMALE_I and ic["risque"][0] >= RISQUE_CONFIRME:
        return "confirmée"
    if ic["risque"][1] <= RISQUE_INFIRME:
        return "infirmée"
    return "non tranchée"


def risque_par_age(reps: list[dict], largeur_j: int = 5, jusqu_a_j: int = 90) -> list[tuple]:
    """(âge, épisodes pour 1 000 heures de marche) par tranches : le profil du risque."""
    episodes, heures, _ = _agreger(reps)
    sortie = []
    for debut in range(0, jusqu_a_j, largeur_j):
        nb = sum(debut <= e["age_j"] < debut + largeur_j for e in episodes)
        h = sum(v for a, v in heures.items() if debut <= a < debut + largeur_j)
        sortie.append((debut, 1000 * nb / h if h else float("nan")))
    return sortie


def analyser(par_variante: dict, delai_moyen_j: float, tirages: int, seed: int) -> dict:
    sortie = {}
    for variante, reps in par_variante.items():
        episodes, heures, retards = _agreger(reps)
        seuil = centile(retards, CENTILE_RETARD)
        ages = [e["age_j"] for e in episodes]
        n_favorable = choisir_n(ages, heures)
        n_mecanique = round(delai_moyen_j)
        resultat = {"seuil_retard_j": seuil, "n_episodes": len(episodes),
                    "episodes_par_an": len(episodes) / (len(reps) * FENETRE_STATIONNAIRE_ANS),
                    "profil": risque_par_age(reps)}
        for nom, n in (("favorable", n_favorable), ("mecanique", n_mecanique)):
            m = mesures(reps, n, seuil)
            ic = bootstrap(reps, n, seuil, tirages, seed) if m.get("n") else {}
            resultat[nom] = {"N": n, "mesures": m, "ic": ic,
                             "verdict": verdict(m, ic) if variante[0] == "discret" and ic
                             else None}
        resultat["medianes"] = {
            cle: statistics.median(v) if (v := [e[cle] for e in episodes if e[cle] is not None])
            else None
            for cle in ("age_j", "duree_h", "position_reprise_j", "position_j", "transit_j",
                        "retard_max_j")}
        sortie[variante] = resultat
    return sortie


# --- figure ----------------------------------------------------------------------------


def donnees_figure(par_variante: dict) -> dict:
    """{variante: (âges, F épisodes, F heures de marche)} sur 0-90 jours."""
    sortie = {}
    for variante, reps in par_variante.items():
        episodes, heures, _ = _agreger(reps)
        ages = [e["age_j"] for e in episodes]
        grille = list(range(91))
        sortie[variante] = (grille, [repartition(ages, n) for n in grille],
                            [repartition_heures(heures, n) for n in grille])
    return sortie


def write_figure(par_variante: dict, analyse: dict, reps: int, path=DEFAULT_FIGURE) -> list[Path]:
    donnees = donnees_figure(par_variante)
    chemins = []
    for langue in LANGUES:
        figure, axes = plt.subplots(1, len(VARIANTES), figsize=(17, 4.8), sharey=True,
                                    constrained_layout=True)
        for ax, variante in zip(axes, VARIANTES, strict=True):
            grille, f_ep, f_h = donnees[variante]
            ax.plot(grille, f_ep, color=fe.COULEURS[variante], linewidth=2,
                    label=libelle("ruptures", langue))
            ax.plot(grille, f_h, color="0.4", linestyle="--", label=libelle("heures_marche", langue))
            n = analyse[variante]["favorable"]["N"]
            ax.axvline(n, color="0.2", linewidth=0.8, linestyle=":")
            ax.set_title(f"{libelle(variante[0], langue)}, "
                         f"{libelle('permis' if variante[1] else 'interdits', langue)}\n"
                         f"N = {n} j, {analyse[variante]['n_episodes']} "
                         f"{libelle('episodes', langue)}", fontsize=10)
            ax.set_xlabel(libelle("age_reprise", langue))
            ax.grid(True, color="0.9")
        axes[0].set_ylabel(libelle("part_cumulee", langue))
        axes[0].legend(fontsize=8, loc="upper left")
        figure.suptitle(f"{libelle('titre_attribution', langue)} — {reps} reps", fontsize=11)
        chemin = chemin_langue(path, langue)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, dpi=150)
        plt.close(figure)
        chemins.append(chemin)
    return chemins


# --- rapport ---------------------------------------------------------------------------


def _ic(ic, cle) -> str:
    return f"[{ic[cle][0]:.0%} ; {ic[cle][1]:.0%}]" if cle in ic else ""


def print_report(analyse: dict) -> None:
    for variante, a in analyse.items():
        print(f"\n{'=' * 104}\n{libelle(variante[0])}, "
              f"{libelle('permis' if variante[1] else 'interdits')}, s = {S_J} j — "
              f"{a['n_episodes']} épisodes ({a['episodes_par_an']:.2f} par an), seuil de retard "
              f"(90e centile) {a['seuil_retard_j']:.1f} j\n{'=' * 104}")
        med = a["medianes"]
        print("  médianes : " + ", ".join(
            f"{c} {v:.1f}" for c, v in med.items() if v is not None))
        for nom in ("favorable", "mecanique"):
            r = a[nom]
            m, ic = r["mesures"], r["ic"]
            if not m.get("n"):
                print(f"  N {nom} : aucun épisode")
                continue
            print(f"  N {nom} = {r['N']} j, par épisode : (i) {m['i']:.0%} {_ic(ic, 'i')}  "
                  f"(ii) {m['ii']:.0%} {_ic(ic, 'ii')}  (iii) {m['iii']:.0%} {_ic(ic, 'iii')}")
            print(f"  {'':16} par heure de rupture : (i) {m['i_h']:.0%} {_ic(ic, 'i_h')}  "
                  f"(ii) {m['ii_h']:.0%} {_ic(ic, 'ii_h')}  (iii) {m['iii_h']:.0%} "
                  f"{_ic(ic, 'iii_h')}")
            print(f"  {'':16} (i) : heures de marche dans les N j {m['reference']:.0%}, risque "
                  f"relatif {m['risque']:.2f} "
                  + (f"[{ic['risque'][0]:.2f} ; {ic['risque'][1]:.2f}]" if "risque" in ic else "")
                  + (f"  → {r['verdict']}" if r["verdict"] else ""))
            print(f"  {'':16} (ii) : retard > seuil avant {m['retard_tous']:.0%} des épisodes, "
                  f"contre {m['reference_ii']:.0%} des instants pris au hasard ; risque relatif "
                  f"{m['risque_ii']:.2f} "
                  + (f"[{ic['risque_ii'][0]:.2f} ; {ic['risque_ii'][1]:.2f}]"
                     if "risque_ii" in ic else ""))
        print("  risque par tranche d'âge (épisodes pour 1 000 h de marche) : " + "  ".join(
            f"{d}-{d + 5} j {v:.2f}" for d, v in a["profil"] if not math.isnan(v)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--depuis-json", action="store_true")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    base = configuration(raw, "discret", True)
    delai_moyen_h = base.delai_approvisionnement_h
    if args.depuis_json:
        donnees = json.loads(args.json.read_text(encoding="utf-8"))
        reps = donnees["reps"]
        par_variante = {(d["mode"], d["depassements"]): d["reps"] for d in donnees["variantes"]}
    else:
        reps = args.reps
        par_variante = {}
        for variante in VARIANTES:
            config = configuration(raw, *variante)
            conso_jour_t = config.debit_pellets_t_par_h * 24
            par_variante[variante] = [caracteriser(j, delai_moyen_h, conso_jour_t)
                                      for j in journaux(config, reps, args.seed, args.processus)]
            print(f"  {variante} : journal relevé", flush=True)
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"reps": reps, "variantes": [
            {"mode": v[0], "depassements": v[1], "reps": r} for v, r in par_variante.items()]}),
            encoding="utf-8")
        print(f"journaux écrits : {args.json}")
    for liste in par_variante.values():
        for r in liste:
            r["heures_par_age_j"] = {int(a): h for a, h in r["heures_par_age_j"].items()}
    analyse = analyser(par_variante, delai_moyen_h / 24, args.bootstrap, args.seed)
    print_report(analyse)
    for chemin in write_figure(par_variante, analyse, reps, args.figure):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
