"""Le point de commande compté au rythme de marche du four : l'écart continu–discret se réduit-il ?

En mode discret, le four tourne à pleine cadence ou s'arrête ; le point de commande s est
pourtant exprimé en jours de consommation **moyenne** (disponibilité comprise). Une marche
consomme donc s jours de stock en s × 0,85 jours environ. On teste ici la variante qui
compte s en jours de consommation **au rythme de marche** : s_t = s_j × débit plein × 24, soit
à s = 35 j environ 41 jours de consommation moyenne.

**Ce qu'on compare**, à s = 35 j, 200 réplications appariées, politique (a), les deux
hypothèses de dépassement : les épisodes de rupture par an (journal des ruptures), les heures
de rupture, β approvisionnement, le stock physique moyen et le coût total, pour

- C : consommation continue, s en jours moyens (référence) ;
- D : arrêts discrets, s en jours moyens (l'actuel) ;
- D′ : arrêts discrets, s en jours au rythme de marche.

L'écart continu–discret vaut D − C ; la part réduite, 1 − (D′ − C) / (D − C), avec un IC par
bootstrap sur les réplications. **C et D ne sont pas recalculés** : ce sont les mêmes
configurations, graines et réplications qu'aux étapes 7 (`data/sim/politique_arrets.json`)
et 8 (`data/sim/attribution_ruptures.json`). Seul D′ est simulé ; le script refuse de
tourner si ces deux fichiers manquent, plutôt que de mélanger des tirages différents.

Usage : python scripts/rythme_de_marche.py [--reps 200] [--processus 12]
"""

import argparse
import dataclasses
import importlib.util
import json
import os
import random
import statistics
import sys
from pathlib import Path

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.run import FENETRE_STATIONNAIRE_ANS
from corridor.sim.stats import estimate, paired_difference, significant


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    # Enregistré sous son nom : les processus fils retrouvent ses fonctions par ce nom.
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


ar = _charger("attribution_ruptures")
pa = _charger("politique_arrets")
fe, pc, eco = ar.fe, pa.pc, pa.eco

VARIANTES = (("discret", True), ("discret", False))
CLES = ("beta_appro", "rupture_h_an", "stock_t", "total")
DEFAULT_REPS = 200
DEFAULT_BOOTSTRAP = 2000
DEFAULT_SEED = 20260924
DEFAULT_JSON = Path("data/sim/rythme_de_marche.json")


def configuration_marche(raw, depassements: bool):
    """D′ : arrêts discrets, s = 35 jours de consommation au rythme de marche."""
    base = ar.configuration(raw, "discret", depassements)
    return dataclasses.replace(
        base, point_de_commande_t=ar.S_J * base.debit_pellets_plein_t_par_h * 24)


def episodes_par_an(reps: list[dict]) -> list[float]:
    return [len(r["episodes"]) / FENETRE_STATIONNAIRE_ANS for r in reps]


def part_reduite(continu, discret, marche) -> float:
    """1 − (D′ − C) / (D − C), sur les moyennes."""
    ecart = statistics.fmean(discret) - statistics.fmean(continu)
    if ecart <= 0:
        return float("nan")
    return 1 - (statistics.fmean(marche) - statistics.fmean(continu)) / ecart


def bootstrap_part(continu, discret, marche, tirages: int, seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(continu)
    parts = []
    for _ in range(tirages):
        k = [rng.randrange(n) for _ in range(n)]
        parts.append(part_reduite([continu[i] for i in k], [discret[i] for i in k],
                                  [marche[i] for i in k]))
    parts.sort()
    return parts[int(0.025 * tirages)], parts[int(0.975 * tirages) - 1]


def lire_references(chemin_politiques: Path, chemin_attribution: Path, reps: int) -> dict:
    """C et D des étapes 7 et 8 : {variante: {"C"|"D": {grandeur: série}}}."""
    if not chemin_politiques.exists() or not chemin_attribution.exists():
        raise FileNotFoundError("lancer d'abord politique_arrets.py et attribution_ruptures.py")
    politiques = pa.depuis_json(json.loads(chemin_politiques.read_text(encoding="utf-8")))
    attribution = json.loads(chemin_attribution.read_text(encoding="utf-8"))
    journaux = {(d["mode"], d["depassements"]): d["reps"] for d in attribution["variantes"]}
    sortie = {}
    for variante in VARIANTES:
        dep = variante[1]
        c, d = journaux[("continu", dep)], journaux[variante]
        if len(c) != reps or len(politiques[variante]["aveugle"]["total"]) != reps:
            raise ValueError("les références n'ont pas le nombre de réplications demandé")
        sortie[variante] = {
            "C": {**{k: politiques[variante]["continu"][k] for k in CLES},
                  "episodes_an": episodes_par_an(c)},
            "D": {**{k: politiques[variante]["aveugle"][k] for k in CLES},
                  "episodes_an": episodes_par_an(d)},
        }
    return sortie


def mesurer_marche(raw, reps: int, seed: int, processus: int) -> dict:
    """D′, simulé : mêmes graines que les références, pour les deux mesures."""
    unitaires = eco.couts_unitaires(raw)
    sortie = {}
    for variante in VARIANTES:
        config = configuration_marche(raw, variante[1])
        (mesure,) = pc.mesurer_plusieurs([config], reps, seed, unitaires, fe.SCENARIO,
                                         processus=processus)
        conso_jour_t = config.debit_pellets_t_par_h * 24
        journaux = [ar.caracteriser(j, config.delai_approvisionnement_h, conso_jour_t)
                    for j in ar.journaux(config, reps, seed, processus)]
        sortie[variante] = {**{k: pc.serie(mesure, k) for k in CLES},
                            "episodes_an": episodes_par_an(journaux)}
        print(f"  {variante} : D′ mesuré", flush=True)
    return sortie


def analyser(references: dict, marche: dict, tirages: int, seed: int) -> dict:
    sortie = {}
    for variante in VARIANTES:
        c, d, m = references[variante]["C"], references[variante]["D"], marche[variante]
        ligne = {"estimations": {nom: {k: estimate(x[k]) for k in (*CLES, "episodes_an")}
                                 for nom, x in (("C", c), ("D", d), ("D′", m))},
                 "D′ − D": {k: paired_difference(m[k], d[k]) for k in (*CLES, "episodes_an")}}
        for k in ("episodes_an", "rupture_h_an"):
            ligne[f"part_{k}"] = part_reduite(c[k], d[k], m[k])
            ligne[f"ic_{k}"] = bootstrap_part(c[k], d[k], m[k], tirages, seed)
        sortie[variante] = ligne
    return sortie


def print_report(analyse: dict, s_moyen_j: float) -> None:
    echelles = {"beta_appro": 100.0, "rupture_h_an": 1.0, "stock_t": 1e-3, "total": 1e-6,
                "episodes_an": 1.0}
    for variante, a in analyse.items():
        print(f"\n{'=' * 100}\nDépassements {'permis' if variante[1] else 'interdits'} — s = "
              f"{ar.S_J} j ; au rythme de marche, soit {s_moyen_j:.1f} j de consommation "
              f"moyenne\n{'=' * 100}")
        print(f"  {'':6}" + "".join(f"{k:>16}" for k in ("episodes_an", *CLES)))
        for nom, est in a["estimations"].items():
            print(f"  {nom:6}" + "".join(f"{est[k].mean * echelles[k]:>16.3f}"
                                         for k in ("episodes_an", *CLES)))
        d = a["D′ − D"]
        print(f"  {'D′ − D':6}" + "".join(
            f"{d[k].mean * echelles[k]:+.3f}±{d[k].half_width * echelles[k]:.3f}"
            f"{'*' if significant(d[k]) else ' '}".rjust(16) for k in ("episodes_an", *CLES)))
        for k in ("episodes_an", "rupture_h_an"):
            bas, haut = a[f"ic_{k}"]
            print(f"  part de l'écart continu–discret réduite ({k}) : {a[f'part_{k}']:.0%} "
                  f"[{bas:.0%} ; {haut:.0%}]")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--politiques", type=Path, default=pa.DEFAULT_JSON)
    parser.add_argument("--attribution", type=Path, default=ar.DEFAULT_JSON)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--depuis-json", action="store_true")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    references = lire_references(args.politiques, args.attribution, args.reps)
    if args.depuis_json:
        brut = json.loads(args.json.read_text(encoding="utf-8"))
        marche = {(d["mode"], d["depassements"]): d["series"] for d in brut}
    else:
        marche = mesurer_marche(raw, args.reps, args.seed, args.processus)
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([{"mode": v[0], "depassements": v[1], "series": s}
                                         for v, s in marche.items()]), encoding="utf-8")
        print(f"mesures écrites : {args.json}")
    base = configuration_marche(raw, True)
    s_moyen_j = base.point_de_commande_t / (base.debit_pellets_t_par_h * 24)
    print_report(analyser(references, marche, args.bootstrap, args.seed), s_moyen_j)
    return 0


if __name__ == "__main__":
    sys.exit(main())
