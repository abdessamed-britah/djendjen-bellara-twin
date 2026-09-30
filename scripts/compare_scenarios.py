"""Compare les scénarios deux à deux, avec intervalles de confiance et test d'appariement.

Produit les deux tableaux du README : pull contre push, et arrivées régulières contre
Poisson. Chaque configuration est rejouée avec la **même graine maîtresse**, donc la
réplication i de A et celle de B partagent leur séquence aléatoire : les différences se
testent en données appariées (`corridor.sim.stats.paired_difference`), ce qui détecte des
écarts réels que le simple chevauchement des intervalles manquerait.

Usage : python scripts/compare_scenarios.py [--reps 50] [--years 1]
"""

import argparse
import dataclasses
import sys

from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.run import replicate
from corridor.sim.stats import estimate, paired_difference, significant

SCENARIOS = ("baseline", "phase2", "phase2_plus_stockage", "phase2_plus_poste")
INDICATEURS = (
    ("production DRI (Mt)", "dri_production_t", 1e-6, 3),
    ("arrêts DRI (h)", "dri_stop_hours", 1.0, 0),
    ("attente en rade (h)", "wait_mean_h", 1.0, 1),
    ("occupation du poste (%)", "berth_occupancy", 100.0, 1),
    ("utilisation des rames (%)", "rame_utilisation", 100.0, 1),
    ("rames bloquées (h)", "rame_blocked_h", 1.0, 0),
)


def serie(results, field):
    return [getattr(r, field) for r in results]


def rejoue(scenario, reps, years, seed, **ecarts):
    config = dataclasses.replace(SimConfig.from_scenario(load_scenario(scenario)), **ecarts)
    return config, replicate(config, years, reps, seed, scenario)


def compare(titre, gauche, droite, nom_gauche, nom_droite, indicateurs=INDICATEURS):
    """Affiche A, B et leur différence appariée, avec un verdict de significativité."""
    print(f"\n### {titre}")
    print(f"{'indicateur':28} {nom_gauche:>22} {nom_droite:>22} "
          f"{'différence (IC 95 %)':>26}  verdict")
    for libelle, champ, echelle, digits in indicateurs:
        a = [v * echelle for v in serie(gauche, champ)]
        b = [v * echelle for v in serie(droite, champ)]
        ea, eb = estimate(a), estimate(b)
        d = paired_difference(a, b)
        verdict = "significatif" if significant(d) else "NON significatif"
        print(
            f"{libelle:28} {ea.mean:>12.{digits}f} ± {ea.half_width:<7.{digits}f} "
            f"{eb.mean:>12.{digits}f} ± {eb.half_width:<7.{digits}f} "
            f"{d.mean:>14.{digits}f} ± {d.half_width:<9.{digits}f}  {verdict}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=50)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260924)
    args = parser.parse_args(argv)

    for scenario in SCENARIOS:
        print(f"\n{'=' * 100}\n{scenario}\n{'=' * 100}")
        for dispersion, nom in ((0.0, "régulières"), (1.0, "Poisson")):
            _, pull = rejoue(scenario, args.reps, args.years, args.seed,
                             dispersion_arrivees=dispersion, politique_expedition="pull")
            _, push = rejoue(scenario, args.reps, args.years, args.seed,
                             dispersion_arrivees=dispersion, politique_expedition="push")
            compare(f"{scenario}, arrivées {nom} : pull contre push",
                    pull, push, "pull", "push")

        _, regulier = rejoue(scenario, args.reps, args.years, args.seed,
                             dispersion_arrivees=0.0, politique_expedition="pull")
        _, poisson = rejoue(scenario, args.reps, args.years, args.seed,
                            dispersion_arrivees=1.0, politique_expedition="pull")
        compare(f"{scenario}, politique pull : régulières contre Poisson",
                regulier, poisson, "régulières", "Poisson")

    for dispersion, nom in ((0.0, "régulières"), (1.0, "Poisson")):
        _, seul = rejoue("phase2", args.reps, args.years, args.seed,
                         dispersion_arrivees=dispersion, politique_expedition="pull")
        _, double = rejoue("phase2_plus_poste", args.reps, args.years, args.seed,
                           dispersion_arrivees=dispersion, politique_expedition="pull")
        compare(f"phase2, arrivées {nom} : 1 poste contre 2 postes",
                seul, double, "1 poste", "2 postes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
