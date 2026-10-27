"""Plan factoriel poste × stockage refait sur le modèle corrigé, en phase 2.

Le premier plan factoriel (`poste_stockage_factoriel.py`) a été joué sur le modèle exogène,
et deux de ses conclusions sont tombées depuis : le gain du stockage venait du stock initial
(limite 0), et le modèle n'avait pas de régime stationnaire (étape 0). On le refait ici sur ce
qui en tient lieu aujourd'hui :

- **approvisionnement endogène**, point de commande à son **optimum de coût** (étapes 2 et 5) ;
- **régime stationnaire**, 4 ans d'échauffement écartés : le stock initial ne pèse plus rien,
  et c'est le premier plan factoriel du projet qui n'en dépende pas ;
- **quatre variantes**, consommation continue ou arrêts discrets × dépassements permis ou
  interdits. Le continu et le discret encadrent la réalité (le discret est une borne haute de
  l'irrégularité de la consommation) : **une conclusion ne vaut que si elle tient dans les
  quatre**, et le script le dit pour chaque effet.

**Les cases.** Un ou deux postes × stockage de base (valeurs d'hypothèse) ou haut (les deux
capacités en haut de leurs fourchettes publiées, comme `phase2_plus_stockage`). Le point de
commande ne change pas d'une case à l'autre : on compare des leviers, pas des politiques.

**Les effets**, en différences appariées « avec le levier − sans » sur 200 réplications : le
second poste à chaque niveau de stockage, le stockage à chaque nombre de postes, les deux
ensemble, et l'interaction. Chaque effet est donné sur la **production** (β approvisionnement,
ruptures, coût des arrêts) et sur la **file** (attente, blocage à quai, surestaries) : l'étape 2
suggérait que le stockage agit désormais surtout sur les surestaries.

Usage : python scripts/factoriel_endogene.py [--reps 200] [--processus 10]
"""

import argparse
import dataclasses
import importlib.util
import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.labels import LANGUES, chemin_langue, libelle
from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.stats import Estimate, paired_difference, significant


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _charger("politique_commande")
eco = pc.eco

SCENARIO = "phase2_endogene"
#: Les quatre variantes du modèle, et le point de commande optimal de chacune (étapes 2 et 5).
VARIANTES = (("continu", True), ("continu", False), ("discret", True), ("discret", False))
S_OPTIMAL_J = {("continu", True): 30, ("continu", False): 35,
               ("discret", True): 35, ("discret", False): 35}
#: Les quatre cases : (nombre de postes, niveau de stockage).
CASES = ((1, "base"), (2, "base"), (1, "haut"), (2, "haut"))
#: Les effets : (clé, case avec le levier, case sans), et l'interaction à part.
EFFETS = (
    ("poste_base", (2, "base"), (1, "base")),
    ("poste_haut", (2, "haut"), (1, "haut")),
    ("stockage_1", (1, "haut"), (1, "base")),
    ("stockage_2", (2, "haut"), (2, "base")),
    ("les_deux", (2, "haut"), (1, "base")),
)
#: Indicateurs : clé, échelle d'affichage, sens favorable (+1 : plus est mieux).
INDICATEURS = (
    ("beta_appro", 100.0, +1),
    ("rupture_h_an", 1.0, -1),
    ("attente_h", 1.0, -1),
    ("blocage_h_an", 1.0, -1),
    ("arrets_dri", 1e-6, -1),
    ("surestaries", 1e-6, -1),
    ("portage", 1e-6, -1),
    ("total", 1e-6, -1),
)
DEFAULT_REPS = 200
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/factoriel_endogene.png")
DEFAULT_JSON = Path("data/sim/factoriel_endogene.json")


def capacites(raw, stockage: str) -> tuple[float, float]:
    """Stockyard et stock usine : valeurs d'hypothèse, ou haut des deux fourchettes."""
    port = raw["stockage_port"]["capacite_stockyard_pellets"]
    usine = raw["usine"]["capacite_stock_pellets_usine"]
    if stockage == "base":
        return float(port["value"]), float(usine["value"])
    return float(port["range"][1]), float(usine["range"][1])


def configuration(raw, mode: str, depassements: bool, postes: int, stockage: str) -> SimConfig:
    """Une case du plan, dans une variante du modèle."""
    base = SimConfig.from_scenario(load_scenario(SCENARIO))
    port, usine = capacites(raw, stockage)
    s_j = S_OPTIMAL_J[(mode, depassements)]
    return dataclasses.replace(
        base, mode_arrets_dri=mode, depassements_autorises=depassements, postes_minerai=postes,
        capacite_stockyard_t=port, capacite_stock_usine_t=usine,
        stock_initial_stockyard_t=port * base.fraction_stock_initial,
        stock_initial_usine_t=usine * base.fraction_stock_initial,
        point_de_commande_j=float(s_j), point_de_commande_t=s_j * base.debit_pellets_t_par_h * 24)


def effets(cases: dict) -> dict:
    """{(effet, indicateur): différence appariée}, interaction comprise."""
    sortie = {}
    for cle, _, _ in INDICATEURS:
        def serie(case, cle=cle):
            return pc.serie(cases[case], cle)
        for nom, avec, sans in EFFETS:
            sortie[(nom, cle)] = paired_difference(serie(avec), serie(sans))
        # interaction : (2 postes − 1 poste) à stockage haut, moins le même écart à la base
        poste_haut = [a - b for a, b in zip(serie((2, "haut")), serie((1, "haut")), strict=True)]
        poste_base = [a - b for a, b in zip(serie((2, "base")), serie((1, "base")), strict=True)]
        sortie[("interaction", cle)] = paired_difference(poste_haut, poste_base)
    return sortie


def robustesse(par_variante: dict, effet: str, cle: str) -> str:
    """La règle : une conclusion ne vaut que si elle tient dans les quatre variantes."""
    differences = [par_variante[v][(effet, cle)] for v in VARIANTES]
    signes = {d.mean > 0 for d in differences if significant(d)}
    nb_significatifs = sum(significant(d) for d in differences)
    if nb_significatifs == len(VARIANTES) and len(signes) == 1:
        return "hausse dans les quatre" if signes.pop() else "baisse dans les quatre"
    if nb_significatifs == 0:
        return "non significatif dans les quatre"
    if len(signes) > 1:
        return f"signe opposé selon la variante ({nb_significatifs}/4 significatifs)"
    return f"dépend de la variante ({nb_significatifs}/4 significatifs)"


# --- sauvegarde : retracer la figure sans resimuler -----------------------------------


def vers_json(par_variante: dict) -> list[dict]:
    return [{"mode": v[0], "depassements": v[1], "effet": e, "indicateur": c,
             "mean": d.mean, "std": d.std, "n": d.n}
            for v, effets_v in par_variante.items() for (e, c), d in effets_v.items()]


def depuis_json(lignes: list[dict]) -> dict:
    par_variante = {}
    for ligne in lignes:
        variante = (ligne["mode"], ligne["depassements"])
        par_variante.setdefault(variante, {})[(ligne["effet"], ligne["indicateur"])] = Estimate(
            mean=ligne["mean"], std=ligne["std"], n=ligne["n"])
    return par_variante


# --- figure -------------------------------------------------------------------------


PANNEAUX = ("beta_appro", "surestaries", "total")
COULEURS = {("continu", True): "#1f77b4", ("continu", False): "#9ecae1",
            ("discret", True): "#d62728", ("discret", False): "#fc9272"}


def donnees_figure(par_variante: dict) -> dict:
    """{indicateur: {variante: [(effet, moyenne, demi-largeur)]}}, mis à l'échelle."""
    echelles = {cle: e for cle, e, _ in INDICATEURS}
    noms = [n for n, _, _ in EFFETS] + ["interaction"]
    return {cle: {v: [(n, par_variante[v][(n, cle)].mean * echelles[cle],
                       par_variante[v][(n, cle)].half_width * echelles[cle]) for n in noms]
                  for v in VARIANTES} for cle in PANNEAUX}


def write_figure(par_variante: dict, reps: int, path=DEFAULT_FIGURE) -> list[Path]:
    donnees = donnees_figure(par_variante)
    noms = [n for n, _, _ in EFFETS] + ["interaction"]
    chemins = []
    for langue in LANGUES:
        figure, axes = plt.subplots(1, len(PANNEAUX), figsize=(16, 5.6), sharey=True,
                                    constrained_layout=True)
        for ax, cle in zip(axes, PANNEAUX, strict=True):
            for k, variante in enumerate(VARIANTES):
                decalage = (k - 1.5) * 0.17
                for j, (_, moyenne, demi) in enumerate(donnees[cle][variante]):
                    ax.errorbar(moyenne, j + decalage, xerr=demi, fmt="o", markersize=4,
                                color=COULEURS[variante], capsize=2,
                                label=(f"{libelle(variante[0], langue)}, "
                                       f"{libelle('permis' if variante[1] else 'interdits', langue)}"
                                       if j == 0 else None))
            ax.axvline(0, color="0.4", linewidth=1)
            ax.set_title(libelle(cle, langue), fontweight="bold")
            ax.set_xlabel(libelle("effet", langue))
            ax.grid(True, color="0.9")
        axes[0].set_yticks(range(len(noms)), [libelle(n, langue) for n in noms])
        axes[0].invert_yaxis()
        poignees, etiquettes = axes[0].get_legend_handles_labels()
        figure.legend(poignees, etiquettes, loc="outside lower center", ncol=4, fontsize=8)
        figure.suptitle(f"{libelle('titre_factoriel', langue)}\n"
                        f"{libelle('sous_titre_factoriel', langue)} — {reps} reps",
                        fontsize=11)
        chemin = chemin_langue(path, langue)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, dpi=150)
        plt.close(figure)
        chemins.append(chemin)
    return chemins


# --- rapport ------------------------------------------------------------------------


def print_variante(variante, cases, eff) -> None:
    mode, dep = variante
    print(f"\n{'=' * 110}\n{libelle(mode)}, {libelle('permis' if dep else 'interdits')}, "
          f"s = {S_OPTIMAL_J[variante]} j\n{'=' * 110}")
    print(f"  {'case':18}" + "".join(f"{libelle(c).split(' (')[0][:16]:>18}" for c, _, _ in INDICATEURS))
    for case in CASES:
        print(f"  {case[0]} poste(s), {case[1]:5}  " + "".join(
            f"{cases[case][c].mean * e:>18.3f}" for c, e, _ in INDICATEURS))
    print(f"\n  {'effet (avec − sans)':34}" + "".join(
        f"{libelle(c).split(' (')[0][:16]:>18}" for c, _, _ in INDICATEURS))
    for nom in [n for n, _, _ in EFFETS] + ["interaction"]:
        print(f"  {libelle(nom):34}" + "".join(
            f"{eff[(nom, c)].mean * e:>+10.3f}±{eff[(nom, c)].half_width * e:<6.3f}"
            f"{'*' if significant(eff[(nom, c)]) else ' '}" for c, e, _ in INDICATEURS))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--depuis-json", action="store_true",
                        help="retrace la figure depuis --json, sans simuler")
    args = parser.parse_args(argv)

    if args.depuis_json:
        par_variante = depuis_json(json.loads(args.json.read_text(encoding="utf-8")))
        n = next(iter(next(iter(par_variante.values())).values())).n
        for chemin in write_figure(par_variante, n, args.figure):
            print(f"figure écrite : {chemin}")
        return 0

    raw = load_yaml(ASSUMPTIONS_PATH)
    unitaires = eco.couts_unitaires(raw)
    print(f"{SCENARIO}, {args.reps} réplications appariées par case, régime stationnaire, "
          f"{args.processus} processus. * = significatif à 95 %.")
    par_variante = {}
    for variante in VARIANTES:
        cases = {case: pc.mesurer(configuration(raw, *variante, *case), args.reps, args.seed,
                                  unitaires, SCENARIO, processus=args.processus)
                 for case in CASES}
        par_variante[variante] = effets(cases)
        print_variante(variante, cases, par_variante[variante])

    print(f"\n{'=' * 110}\nCe qui tient dans les quatre variantes\n{'=' * 110}")
    for nom in [n for n, _, _ in EFFETS] + ["interaction"]:
        print(f"\n  {libelle(nom)}")
        for cle, _, _ in INDICATEURS:
            print(f"    {libelle(cle):38} {robustesse(par_variante, nom, cle)}")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(vers_json(par_variante), indent=1), encoding="utf-8")
    print(f"effets écrits : {args.json}")
    for chemin in write_figure(par_variante, args.reps, args.figure):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
