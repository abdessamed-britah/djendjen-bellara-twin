"""Dépassements entre navires permis ou interdits : l'effet sur β, mesuré par le modèle actuel.

Le chiffre publié jusqu'ici (phase 2 : 0,9917 → 0,9749, ligne M5 de
`docs/results_summary.md`) venait d'un script de diagnostic qui substituait `navire_en_mer`
avant que l'option existe, sur 12 graines × 20 ans comptés depuis le démarrage. Il est
refait ici par l'option du modèle, `simulation.depassements_autorises`, avec le protocole
stationnaire du projet (`corridor.sim.run` : 4 ans d'échauffement, 16 ans mesurés) et des
réplications **appariées** : la réplication i des deux hypothèses voit les mêmes tailles,
cadences et délais tirés, commande par commande.

Ce qui est comparé, pour `baseline_endogene` et `phase2_endogene` à leur configuration par
défaut (consommation continue, point de commande de `assumptions.yaml`) : β
approvisionnement, 1 − production perdue faute de pellets ÷ cible, sous les deux
hypothèses, et la différence appariée « interdits − permis ».

Usage : python scripts/depassements.py [--reps 200] [--processus 12]
        python scripts/depassements.py --depuis-json
"""

import argparse
import dataclasses
import importlib.util
import json
import os
import sys
from pathlib import Path

from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.stats import estimate, paired_difference, significant


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    # Enregistré sous son nom : les processus fils retrouvent ses fonctions par ce nom.
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


pc = _charger("politique_commande")
eco = pc.eco

SCENARIOS = ("baseline_endogene", "phase2_endogene")
HYPOTHESES = ((True, "permis"), (False, "interdits"))
DEFAULT_REPS = 200
DEFAULT_SEED = 20260924
DEFAULT_JSON = Path("data/sim/depassements.json")


def configuration(scenario: str, depassements: bool) -> SimConfig:
    config = SimConfig.from_scenario(load_scenario(scenario))
    return dataclasses.replace(config, depassements_autorises=depassements)


def mesurer(reps: int, seed: int, processus: int) -> dict:
    """{scénario: {"permis"|"interdits": [β approvisionnement, une valeur par réplication]}}."""
    from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
    unitaires = eco.couts_unitaires(load_yaml(ASSUMPTIONS_PATH))
    sortie = {}
    for scenario in SCENARIOS:
        configs = [configuration(scenario, dep) for dep, _ in HYPOTHESES]
        mesures = pc.mesurer_plusieurs(configs, reps, seed, unitaires, scenario,
                                       processus=processus)
        sortie[scenario] = {nom: pc.serie(m, "beta_appro")
                            for (_, nom), m in zip(HYPOTHESES, mesures, strict=True)}
        print(f"  {scenario} : mesuré", flush=True)
    return sortie


def analyser(series: dict) -> dict:
    sortie = {}
    for scenario, s in series.items():
        difference = paired_difference(s["interdits"], s["permis"])
        sortie[scenario] = {"permis": estimate(s["permis"]),
                            "interdits": estimate(s["interdits"]),
                            "difference": difference,
                            "significative": significant(difference)}
    return sortie


def print_report(analyse: dict, reps: int) -> None:
    print(f"\nβ approvisionnement, régime stationnaire, {reps} réplications appariées")
    print(f"  {'scénario':20} {'permis':>18} {'interdits':>18} {'interdits − permis':>22}")
    for scenario, a in analyse.items():
        p, i, d = a["permis"], a["interdits"], a["difference"]
        print(f"  {scenario:20} {p.mean:>9.4f} ± {p.half_width:.4f} "
              f"{i.mean:>9.4f} ± {i.half_width:.4f} "
              f"{d.mean:>+12.4f} ± {d.half_width:.4f}{' *' if a['significative'] else ''}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--depuis-json", action="store_true")
    args = parser.parse_args(argv)

    if args.depuis_json:
        brut = json.loads(args.json.read_text(encoding="utf-8"))
    else:
        brut = {"reps": args.reps, "seed": args.seed,
                "series": mesurer(args.reps, args.seed, args.processus)}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(brut), encoding="utf-8")
        print(f"mesures écrites : {args.json}")
    print_report(analyser(brut["series"]), brut["reps"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
