"""Ce que changent les arrêts discrets du four au taux de service et au coût de l'étape 2.

La validation de l'étape 5 (`validation_arrets.py`) passe sur 2023-2025 pour des durées
d'arrêt plausibles, dont la valeur centrale de l'hypothèse (7 jours). On rejoue donc les
résultats de l'étape 2 avec le four en marche/arrêt, **à disponibilité moyenne inchangée**, et
on mesure ce qui bouge :

1. au point de commande retenu à l'étape 2, en **données appariées** (discret − continu,
   mêmes graines) : β en régime stationnaire, heures de rupture, attente en rade, stock
   physique moyen, et les trois termes du coût ;
2. le balayage du point de commande en mode discret : l'optimum en coût se déplace-t-il ?

Même protocole qu'à l'étape 2 (4 ans d'échauffement, 16 ans mesurés), mêmes scénarios
endogènes, mêmes deux hypothèses de dépassement, mêmes prix unitaires — tous de statut estimé.

Usage : python scripts/effet_arrets.py [--reps 16]
"""

import argparse
import importlib.util
import sys
from pathlib import Path

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.stats import paired_difference, significant


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _charger("politique_commande")
eco = pc.eco

#: Le point de commande de l'étape 2, par configuration (optimum en coût, mode continu).
POINT_DE_COMMANDE_ETAPE_2 = {
    ("baseline_endogene", True): 30, ("baseline_endogene", False): 30,
    ("phase2_endogene", True): 30, ("phase2_endogene", False): 35,
}
#: Les grandeurs comparées, et leur échelle d'affichage.
GRANDEURS = (
    ("beta_appro", "β approvisionnement", 100.0, "points"),
    ("beta", "β brut", 100.0, "points"),
    ("rupture_h_an", "ruptures", 1.0, "h/an"),
    ("attente_h", "attente en rade", 1.0, "h"),
    ("stock_t", "stock physique", 1e-3, "kt"),
    ("arrets_dri", "coût arrêts DRI", 1e-6, "M USD/an"),
    ("surestaries", "coût surestaries", 1e-6, "M USD/an"),
    ("portage", "coût portage", 1e-6, "M USD/an"),
    ("total", "coût total", 1e-6, "M USD/an"),
)
DEFAULT_REPS = 16
DEFAULT_SEED = 20260924


def configurations(scenario: str, depassements: bool, s_j: float):
    """Les deux modes du four au même point de commande, le reste identique."""
    base = eco.configuration_stationnaire(scenario, depassements)
    ecarts = {"point_de_commande_j": float(s_j),
              "point_de_commande_t": s_j * base.debit_pellets_t_par_h * 24}
    return (eco.configuration_stationnaire(scenario, depassements, **ecarts),
            eco.configuration_stationnaire(scenario, depassements, mode_arrets_dri="discret",
                                           **ecarts))


def comparer(continu: dict, discret: dict) -> dict:
    """Discret − continu, apparié réplication par réplication, pour chaque grandeur."""
    sortie = {}
    for cle, *_ in GRANDEURS:
        d = paired_difference(pc.serie(discret, cle), pc.serie(continu, cle))
        sortie[cle] = {"continu": continu[cle], "discret": discret[cle], "difference": d,
                       "significative": significant(d)}
    return sortie


def print_comparaison(cle, comparaison) -> None:
    scenario, depassements = cle
    print(f"\n  --- {scenario}, {dict(eco.HYPOTHESES_DEPASSEMENT)[depassements]}, "
          f"s = {POINT_DE_COMMANDE_ETAPE_2[cle]} j")
    print(f"  {'grandeur':18} {'continu':>18} {'discret':>18} {'discret − continu':>22}")
    for nom_cle, nom, echelle, unite in GRANDEURS:
        c = comparaison[nom_cle]
        d = c["difference"]
        print(f"  {nom:18} {c['continu'].mean * echelle:>10.3f} ± {c['continu'].half_width * echelle:<6.3f}"
              f" {c['discret'].mean * echelle:>10.3f} ± {c['discret'].half_width * echelle:<6.3f}"
              f" {d.mean * echelle:>+11.3f} ± {d.half_width * echelle:<6.3f}"
              f" {'*' if c['significative'] else ' '} {unite}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--points", type=int, nargs="+", default=list(pc.POINTS_DE_COMMANDE_J))
    args = parser.parse_args(argv)

    unitaires = eco.couts_unitaires(load_yaml(ASSUMPTIONS_PATH))
    print(f"{args.reps} réplications appariées, protocole stationnaire de l'étape 2 ; "
          "arrêts discrets à la valeur centrale de l'hypothèse")

    print(f"\n{'=' * 104}\n1. Au point de commande de l'étape 2 : discret − continu\n{'=' * 104}")
    comparaisons = {}
    for cle, s_j in POINT_DE_COMMANDE_ETAPE_2.items():
        continu, discret = configurations(*cle, s_j)
        comparaisons[cle] = comparer(
            pc.mesurer(continu, args.reps, args.seed, unitaires, cle[0]),
            pc.mesurer(discret, args.reps, args.seed, unitaires, cle[0]))
        print_comparaison(cle, comparaisons[cle])
    print("  * = significatif à 95 %.")

    print(f"\n{'=' * 104}\n2. Balayage du point de commande, mode discret\n{'=' * 104}")
    for cle, s_etape_2 in POINT_DE_COMMANDE_ETAPE_2.items():
        balayage = []
        for s_j in args.points:
            _, discret = configurations(*cle, s_j)
            balayage.append({"s": s_j, "mesure": pc.mesurer(discret, args.reps, args.seed,
                                                             unitaires, cle[0])})
        pc.print_balayage(cle, balayage)
        print(f"  optimum en mode continu (étape 2) : s = {s_etape_2} j")
    return 0


if __name__ == "__main__":
    sys.exit(main())
