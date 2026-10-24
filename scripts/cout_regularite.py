"""H4 : le long de la frontière de service, vaut-il mieux lisser l'affrètement ou stocker ?

La frontière β = 0,95 est une courbe d'**indifférence technique** : tous ses points tiennent
le même taux de service. Ils ne coûtent pas la même chose. Deux leviers s'y échangent :

- **lisser l'affrètement** — obtenir des fenêtres d'arrivée contractuelles, donc faire
  baisser c_a². Coûte `couts.prime_lissage_affretement` par navire et par point de c_a²
  gagné sur l'irrégularité actuelle du site ;
- **stocker** — tenir la même production avec des arrivées plus erratiques, donc monter en
  couverture τ. Coûte `couts.cout_stockage_annualise` par kt de capacité et par an.

Descendre en c_a² réduit le stock nécessaire ; rester irrégulier l'augmente. Il existe donc
un couple (c_a², τ) de coût minimal sur la frontière, et c'est lui que ce script cherche.

**L'avertissement vaut plus que le résultat.** Les deux coûts sont de statut `estimé`, sans
source, avec des fourchettes couvrant deux ordres de grandeur. Le montant du minimum ne veut
rien dire. Ce qui en a, c'est **sa position**, et surtout comment elle se déplace avec le
rapport des deux coûts — c'est l'objet du second panneau de la figure et de la dernière
section de la sortie. Un lecteur qui connaîtrait ses propres coûts y lirait sa réponse
plutôt que la nôtre.

Deux hypothèses de forme, qui comptent autant que les chiffres :

- le coût du lissage est pris **linéaire** en c_a². En pratique les derniers points de
  régularité coûtent sans doute bien plus cher que les premiers, ce qui déplacerait
  l'optimum vers la droite (moins de lissage, plus de stock) ;
- le coût du stockage est pris **proportionnel** à la capacité, ce qui ignore les économies
  d'échelle et la contrainte d'emprise foncière.

Usage : python scripts/cout_regularite.py [--beta 0.95] [--postes 1]
"""

import argparse
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # aucune fenêtre : on écrit un fichier
import matplotlib.pyplot as plt
import pyarrow.parquet as pq

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml

sys.path.insert(0, str(Path(__file__).parent))
from frontiere_service import (
    bande_c_a2,
    consommation_journaliere_t,
    couverture_requise,
    from_table,
)

DEFAULT_FIGURE = Path("docs/cout_regularite.png")
DEFAULT_TABLE = Path("data/sim/frontiere_service.parquet")
DEFAULT_BETA = 0.95
DEFAULT_POSTES = 1
#: Rapports (stockage ÷ lissage) balayés pour montrer la sensibilité de l'optimum.
#: Échelonnés géométriquement sur cinq ordres de grandeur, et resserrés là où la bascule
#: se produit : un balayage trop grossier donnerait l'illusion d'un optimum insensible.
RATIOS = tuple(round(10 ** (-3 + k / 6), 4) for k in range(31))
#: Couleurs du même jeu validé que la frontière de service.
COULEUR_LISSAGE = "#1f77b4"
COULEUR_STOCKAGE = "#e8710a"
COULEUR_TOTAL = "#2f2f2f"


def cout_lissage(c_a2: float, c_a2_actuel: float, navires_par_an: float,
                 prime: float) -> float:
    """Coût annuel de ramener l'irrégularité de `c_a2_actuel` à `c_a2`.

    Nul si la cible est plus irrégulière que l'état actuel : on ne fait pas payer un
    lissage qu'on ne fait pas, et on ne s'accorde pas non plus un gain imaginaire en
    laissant l'affrètement se dégrader — la frontière en facturerait le stock de toute
    façon.
    """
    gagne = max(c_a2_actuel - c_a2, 0.0)
    return gagne * navires_par_an * prime


def cout_stockage(tau_jours: float, consommation_t_par_jour: float,
                  cout_par_kt_an: float) -> float:
    """Coût annuel d'une couverture de τ jours, via la capacité qu'elle représente."""
    capacite_kt = tau_jours * consommation_t_par_jour / 1e3
    return capacite_kt * cout_par_kt_an


def points_frontiere(grille, beta: float) -> list[tuple[float, float]]:
    """Les couples (c_a², τ) de la frontière, dans l'ordre des c_a² croissants.

    Les c_a² où le niveau n'est jamais atteint sont écartés : il n'y a pas de couple à
    chiffrer là où la cible est hors d'atteinte à tout prix.
    """
    requis = couverture_requise(grille, beta)
    return sorted((c, t) for c, t in requis.items() if t is not None)


def couts_sur_frontiere(points, c_a2_actuel, navires_par_an, consommation,
                        prime, cout_kt) -> list[dict]:
    """Décompose le coût annuel de chaque point de la frontière."""
    lignes = []
    for c_a2, tau in points:
        lissage = cout_lissage(c_a2, c_a2_actuel, navires_par_an, prime)
        stockage = cout_stockage(tau, consommation, cout_kt)
        lignes.append({
            "c_a2": c_a2, "tau": tau, "lissage": lissage,
            "stockage": stockage, "total": lissage + stockage,
        })
    return lignes


def minimum(lignes) -> dict:
    """Le point de coût total minimal. La frontière n'ayant que quelques points, on énumère."""
    return min(lignes, key=lambda x: x["total"])


def optimum_selon_ratio(points, c_a2_actuel, navires_par_an, consommation, ratios):
    """Position de l'optimum quand le rapport (coût stockage ÷ coût lissage) varie.

    Le rapport est pris sans dimension en normalisant chaque levier par son unité
    naturelle : `prime` est fixée à 1 et le coût du stockage à `ratio`. Seul compte le
    rapport, puisque multiplier les deux coûts par la même constante ne déplace pas le
    minimum — c'est précisément ce que ce balayage montre.
    """
    resultats = []
    for ratio in ratios:
        lignes = couts_sur_frontiere(points, c_a2_actuel, navires_par_an, consommation,
                                     prime=1.0, cout_kt=ratio)
        resultats.append((ratio, minimum(lignes)["c_a2"]))
    return resultats


def _musd(valeur: float) -> str:
    return f"{valeur / 1e6:.2f}".replace(".", ",")


def _espace(valeur: float) -> str:
    """Nombre avec espace comme séparateur de milliers, sans toucher au reste du texte."""
    return f"{valeur:,.0f}".replace(",", " ")


def print_table(lignes, actuel, minimal) -> None:
    print(f"\n{'=' * 86}\nCoût annuel le long de la frontière\n{'=' * 86}")
    print(f"{'c_a²':>6} {'τ requis':>10} {'lissage':>16} {'stockage':>16} {'total':>16}")
    print(f"{'':>6} {'(jours)':>10} {'(M USD/an)':>16} {'(M USD/an)':>16} {'(M USD/an)':>16}")
    for x in lignes:
        marque = "  <-- minimum" if x is minimal else ""
        actuelle = "  (état estimé du site)" if abs(x["c_a2"] - actuel) < 1e-9 else ""
        print(f"{x['c_a2']:>6.2f} {x['tau']:>10.1f} {_musd(x['lissage']):>16} "
              f"{_musd(x['stockage']):>16} {_musd(x['total']):>16}{marque}{actuelle}")


def print_ratios(resultats, ratio_central) -> None:
    print(f"\n{'=' * 86}\nOù se déplace l'optimum quand le rapport des coûts change\n{'=' * 86}")
    print("Rapport = (coût d'un kt de capacité pendant un an) ÷ (prime de lissage d'un "
          "navire\npour un point de c_a²). Seul ce rapport compte : multiplier les deux "
          "coûts par la\nmême constante ne déplace pas le minimum.\n")
    print(f"{'rapport':>12} {'c_a² optimal':>14}")
    precedent = None
    for ratio, c_a2 in resultats:
        bascule = precedent is not None and c_a2 != precedent
        marque = "  <-- rapport des hypothèses centrales" if abs(
            ratio - ratio_central) < 1e-9 else ("  <-- bascule" if bascule else "")
        if bascule or marque or precedent is None or ratio == resultats[-1][0]:
            print(f"{ratio:>12.4f} {c_a2:>14.2f}{marque}")
        precedent = c_a2


def write_figure(lignes, resultats, minimal, actuel, ratio_central, beta, postes,
                 path=DEFAULT_FIGURE) -> Path:
    """Deux panneaux : la décomposition du coût, puis la sensibilité de l'optimum.

    Le second panneau porte le message : avec des coûts incertains de deux ordres de
    grandeur, c'est la position de l'optimum en fonction de leur **rapport** qui est
    informative, pas le montant du minimum.
    """
    figure, (gauche, droite) = plt.subplots(1, 2, figsize=(13.5, 6.4),
                                            constrained_layout=True)

    xs = [x["c_a2"] for x in lignes]
    for serie, couleur, libelle in (
        ("lissage", COULEUR_LISSAGE, "lisser l'affrètement"),
        ("stockage", COULEUR_STOCKAGE, "construire du stockage"),
        ("total", COULEUR_TOTAL, "total"),
    ):
        largeur = 2.6 if serie == "total" else 2.0
        gauche.plot(xs, [x[serie] / 1e6 for x in lignes], marker="o", markersize=5.5,
                    linewidth=largeur, color=couleur, label=libelle, zorder=3)
    gauche.axvline(minimal["c_a2"], color=COULEUR_TOTAL, linestyle="--", linewidth=1.2,
                   zorder=2)
    gauche.annotate(
        f"minimum : c_a² = {minimal['c_a2']:.2f}".replace(".", ",")
        + f"\nτ = {minimal['tau']:.0f} j, {_musd(minimal['total'])} M USD/an",
        xy=(minimal["c_a2"], minimal["total"] / 1e6),
        xytext=(0.34, 0.58), textcoords="axes fraction", fontsize=9.5,
        arrowprops={"arrowstyle": "->", "color": COULEUR_TOTAL, "linewidth": 1.2},
    )
    gauche.axvline(actuel, color="#b06a1a", linestyle="-.", linewidth=1.4, zorder=2)
    gauche.text(actuel, 0.02, f" c_a² estimé du site : {actuel:.2f} ".replace(".", ","),
                transform=gauche.get_xaxis_transform(), fontsize=8.5, color="#8a4f10",
                va="bottom", ha="left")
    gauche.set_xlabel("c_a² — irrégularité des arrivées visée")
    gauche.set_ylabel("coût annuel (M USD/an)")
    gauche.set_title(f"Coût de tenir β = {beta:.2f}".replace(".", ",")
                     + f" avec {postes} poste", fontweight="bold")
    gauche.legend(fontsize=9)
    gauche.grid(True, color="0.90", linewidth=0.8, zorder=0)
    gauche.set_axisbelow(True)

    ratios = [r for r, _ in resultats]
    droite.step([r for r, _ in resultats], [c for _, c in resultats], where="post",
                marker="o", markersize=6, linewidth=2.4, color=COULEUR_TOTAL, zorder=3)
    droite.axvline(ratio_central, color="#b06a1a", linestyle="-.", linewidth=1.4, zorder=2)
    droite.text(ratio_central, 0.98, " rapport des hypothèses centrales ",
                transform=droite.get_xaxis_transform(), fontsize=8.5, color="#8a4f10",
                va="top", ha="left")
    droite.set_xscale("log")
    droite.set_xlim(min(ratios), max(ratios))
    droite.set_xlabel("rapport (coût du stockage) ÷ (prime de lissage), sans dimension")
    droite.set_ylabel("c_a² du couple le moins cher")
    droite.set_title("Position de l'optimum selon le rapport des coûts", fontweight="bold")
    droite.grid(True, color="0.90", linewidth=0.8, zorder=0)
    droite.set_axisbelow(True)

    figure.suptitle(
        "H4 — sur la frontière de service, lisser l'affrètement ou construire du stockage ?\n"
        "Tous les points de la courbe tiennent le même taux de service ; ils ne coûtent pas "
        "la même chose.\n"
        "Les deux coûts sont de statut ESTIMÉ, sans source, à deux ordres de grandeur "
        "près : le montant ne veut rien dire ;\n"
        "ce qui en a est la position de l'optimum, et sa sensibilité au rapport des deux "
        "coûts (panneau de droite).",
        fontsize=10.5,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--beta", type=float, default=DEFAULT_BETA)
    parser.add_argument("--postes", type=int, default=DEFAULT_POSTES)
    parser.add_argument("--scenario", default="phase2")
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    args = parser.parse_args(argv)

    if not args.table.exists():
        print(f"carte de frontière absente ({args.table}) : "
              "python scripts/frontiere_service.py --reps 60")
        return 1

    raw = load_yaml(ASSUMPTIONS_PATH)
    base = SimConfig.from_scenario(load_scenario(args.scenario))
    prime = float(raw["couts"]["prime_lissage_affretement"]["value"])
    cout_kt = float(raw["couts"]["cout_stockage_annualise"]["value"])
    bande = bande_c_a2(raw)
    if bande is None:
        print("aucune estimation de c_a² dans les hypothèses "
              "(`simulation.dispersion_arrivees_observee`)")
        return 1
    actuel = bande[1]
    consommation = consommation_journaliere_t(base)
    navires = base.navires_par_an

    grilles = from_table(pq.read_table(args.table))
    if args.postes not in grilles:
        print(f"la carte ne contient pas la configuration à {args.postes} poste(s)")
        return 1
    points = points_frontiere(grilles[args.postes], args.beta)
    if not points:
        print(f"β = {args.beta} n'est atteint nulle part dans la carte")
        return 1

    print(f"scénario {args.scenario}, {args.postes} poste(s), frontière β = {args.beta}")
    print(f"  c_a² estimé du site       : {actuel:.2f}")
    print(f"  navires de minerai par an : {navires:.1f}")
    print(f"  consommation              : {consommation:,.0f} t de pellets/jour"
          .replace(",", " "))
    print(f"  prime de lissage          : {_espace(prime)} USD/navire/point de c_a² "
          "(estimé, sans source)")
    print(f"  coût du stockage          : {_espace(cout_kt)} USD/kt/an (estimé, sans source)")

    lignes = couts_sur_frontiere(points, actuel, navires, consommation, prime, cout_kt)
    minimal = minimum(lignes)
    print_table(lignes, actuel, minimal)

    ratio_central = cout_kt / prime
    resultats = optimum_selon_ratio(points, actuel, navires, consommation, RATIOS)
    print_ratios(resultats, _ratio_le_plus_proche(RATIOS, ratio_central))

    bascules = sorted({c for _, c in resultats})
    print(f"\n  Sur les rapports balayés ({min(RATIOS):g} à {max(RATIOS):g}), l'optimum ne "
          f"prend que {len(bascules)} position(s) : "
          f"{', '.join(f'{c:.2f}'.replace('.', ',') for c in bascules)}.")
    print(f"  Rapport des hypothèses centrales : {ratio_central:.3f}.")
    print("\n  Lecture : le stockage est facturé par kt et par an, le lissage par navire et "
          "par point.\n  Un rapport élevé veut dire « le stockage est cher devant le "
          "lissage », et pousse donc\n  l'optimum vers la gauche — vers des arrivées plus "
          "régulières et moins de silo.")

    print(f"\n{'=' * 86}\nTrois choses à ne pas lire de travers\n{'=' * 86}")
    bord = min(c for c, _ in points)
    if abs(minimal["c_a2"] - bord) < 1e-9:
        tau_bord = next(t for c, t in points if abs(c - bord) < 1e-9)
        print(f"  1. **L'optimum tombe sur le bord gauche du domaine** (c_a² = {bord:.2f}). "
              "La carte ne\n     descend pas plus bas, et la couverture requise y est "
              f"bornée par le plancher de\n     la grille ({tau_bord:.0f} jours) : le vrai "
              "minimum est peut-être au-delà, et son coût\n     plus bas encore. À lire "
              "« aussi régulier que possible », pas « exactement zéro ».")
    else:
        print(f"  1. L'optimum ({minimal['c_a2']:.2f}) est à l'intérieur du domaine exploré.")
    print("  2. **La courbe de coût n'est pas monotone** entre deux points voisins de la "
          "frontière.\n     C'est du bruit de la carte, pas un creux réel : τ y est lu sur "
          "des moyennes de\n     60 réplications, à quelques jours près (voir la section "
          "frontière du README).")
    print("  3. **Les deux coûts sont des valeurs de travail sans source**, à deux ordres de "
          "grandeur\n     près. Le montant du minimum ne veut rien dire. Ce qui en a est "
          "le tableau ci-dessus :\n     il dit à partir de quel rapport de coûts la réponse "
          "bascule, et un lecteur qui\n     connaît ses propres coûts y lit sa réponse "
          "plutôt que la nôtre.")

    chemin = write_figure(lignes, resultats, minimal, actuel, ratio_central,
                          args.beta, args.postes, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


def _ratio_le_plus_proche(ratios, cible: float) -> float:
    """Le rapport balayé le plus proche du rapport réel, pour le marquer dans le tableau."""
    return min(ratios, key=lambda r: abs(math.log(r) - math.log(cible)))


if __name__ == "__main__":
    sys.exit(main())
