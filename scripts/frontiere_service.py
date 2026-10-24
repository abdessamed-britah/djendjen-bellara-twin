"""Frontière de service : quelle couverture de stock faut-il pour tenir un taux de service ?

C'est la synthèse de l'étude, sous la forme la plus transposable qu'on puisse lui donner.
Les trois résultats du projet — l'irrégularité des arrivées et le stockage gouvernent tout,
le rail ne limite pas, le poste de déchargement non plus — tiennent dans une seule carte à
deux axes **sans dimension** :

- **c_a²**, carré du coefficient de variation des intervalles entre arrivées. 0 = navires
  parfaitement réguliers, 1 = processus de Poisson, au-delà = arrivées groupées. C'est la
  régularité de l'affrètement, indépendamment de sa cadence. Dans le modèle, le paramètre
  `dispersion_arrivees` **est** ce coefficient de variation, donc c_a² = dispersion².
- **τ = (K_P + K_U) / D**, la capacité de stockage totale (port + usine) rapportée à la
  consommation journalière de pellets de l'aciérie. C'est un **nombre de jours de
  couverture** : combien de temps le four tiendrait si plus rien n'arrivait. Un stock de
  450 kt ne veut rien dire hors contexte ; 27 jours de couverture, si.

La grandeur lue est le **taux de service β = production / cible**, où la cible est la
production que l'aciérie atteindrait si le minerai arrivait toujours à temps. β = 0,95
signifie « 5 % de la production annuelle perdue faute d'approvisionnement ».

**Pourquoi des axes sans dimension.** Un autre site — autre tonnage de navire, autre cadence
de déchargement, autre capacité de four — se place sur la même carte dès lors qu'il calcule
son c_a² et son τ. Ce qui ne se transpose pas, et qu'il faut lire avec la figure, c'est la
**charge du poste** ρ = λ·E[S]/c : elle dépend du site, et c'est elle qui distingue les deux
courbes tracées ici (1 poste ou 2, à demande égale).

**Ce que la carte ne dit pas.** β est une moyenne sur des réplications d'un an, assortie
d'un intervalle de confiance qui n'est pas nul : les contours sont tracés sur la moyenne, à
quelques points de pourcentage près. Les deux capacités de stockage du site restent de
statut `inconnu` — c'est bien pourquoi la figure donne une frontière et non un verdict.

Usage : python scripts/frontiere_service.py [--reps 30] [--scenario phase2]
        python scripts/frontiere_service.py --reps 5        # version rapide, contours grossiers
"""

import argparse
import dataclasses
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # aucune fenêtre : on écrit un fichier
import matplotlib.pyplot as plt
import pyarrow as pa
import pyarrow.parquet as pq

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.run import replicate
from corridor.sim.sensitivity import storage_shares, with_storage
from corridor.sim.stats import Estimate, estimate

DEFAULT_FIGURE = Path("docs/frontiere_service.png")
#: Table de la carte : git-ignorée comme les autres sorties de simulation, recalculable.
DEFAULT_TABLE = Path("data/sim/frontiere_service.parquet")
DEFAULT_SCENARIO = "phase2"
DEFAULT_REPS = 30
SEED = 20260924

#: Axe horizontal : carré du coefficient de variation des intervalles entre arrivées.
C_A2_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)
#: Axe vertical : couverture de stock, en jours de consommation de l'aciérie.
TAU_DAYS = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0)
#: Les deux configurations de poste comparées, à demande identique.
POSTES = (1, 2)
#: Les deux taux de service dont on trace la frontière.
LEVELS = (0.95, 0.99)

#: Palette catégorielle validée (ΔE CVD 22,6 en protanopie) : une teinte par nombre de postes.
COULEURS = {1: "#1f77b4", 2: "#e8710a"}
#: Le niveau de service est codé par le style de trait, jamais par la teinte.
STYLES = {0.95: "--", 0.99: "-"}

HEURES_PAR_JOUR = 24.0


def consommation_journaliere_t(config: SimConfig) -> float:
    """D : pellets consommés par jour par l'aciérie, four en marche.

    C'est le débit horaire de pellets porté à la journée. Le four ne tourne pas tous les
    jours de l'année (arrêts planifiés), donc τ se lit « jours de marche couverts », ce qui
    est bien la grandeur utile : c'est le temps que le stock peut absorber avant l'arrêt.
    """
    return config.debit_pellets_t_par_h * HEURES_PAR_JOUR


def cible_t(config: SimConfig, years: float) -> float:
    """Production que l'aciérie atteindrait si le minerai arrivait toujours à temps."""
    return config.debit_dri_t_par_h * config.heures_ouvrees_par_an * years


def couverture_jours(config: SimConfig) -> float:
    """τ de la configuration : stockage total rapporté à la consommation journalière."""
    total = config.capacite_stockyard_t + config.capacite_stock_usine_t
    return total / consommation_journaliere_t(config)


def point_config(base: SimConfig, c_a2: float, tau: float, postes: int, shares) -> SimConfig:
    """Configuration d'un point de la carte : (c_a², τ, nombre de postes).

    `dispersion_arrivees` est le coefficient de variation lui-même, d'où la racine carrée.
    """
    total_t = tau * consommation_journaliere_t(base)
    config = with_storage(base, total_t, shares)
    return dataclasses.replace(
        config,
        dispersion_arrivees=math.sqrt(c_a2),
        postes_minerai=postes,
        politique_expedition="pull",
    )


def beta_grid(base: SimConfig, shares, reps: int, years: float, progress: bool = False):
    """Taux de service moyen sur toute la carte, pour chaque nombre de postes.

    Renvoie {postes: {(c_a², τ): Estimate de β}}. Toutes les cases partagent la même graine
    maîtresse : deux cases se comparent donc en données appariées si besoin, et la carte
    est reproductible à l'identique.
    """
    cible = cible_t(base, years)
    grilles = {}
    for postes in POSTES:
        cases = {}
        for c_a2 in C_A2_VALUES:
            for tau in TAU_DAYS:
                config = point_config(base, c_a2, tau, postes, shares)
                resultats = replicate(config, years, reps, SEED, f"frontiere_{postes}p")
                beta = estimate([r.dri_production_t / cible for r in resultats])
                cases[(c_a2, tau)] = beta
                if progress:
                    print(f"  {postes} poste(s)  c_a²={c_a2:.2f}  τ={tau:4.0f} j  "
                          f"β = {beta.mean:.3f} ± {beta.half_width:.3f}")
        grilles[postes] = cases
    return grilles


def to_table(grilles) -> pa.Table:
    """La carte en table Parquet : une ligne par point, pour retracer sans recalculer.

    Les quelques minutes de simulation que coûte la carte n'ont pas à être repayées pour
    changer une couleur ou un libellé — d'où `--from-parquet`.
    """
    lignes = {"postes": [], "c_a2": [], "tau_jours": [], "beta": [], "beta_half_width": [],
              "beta_std": [], "reps": []}
    for postes, cases in grilles.items():
        for (c_a2, tau), beta in cases.items():
            lignes["postes"].append(postes)
            lignes["c_a2"].append(c_a2)
            lignes["tau_jours"].append(tau)
            lignes["beta"].append(beta.mean)
            lignes["beta_half_width"].append(beta.half_width)
            lignes["beta_std"].append(beta.std)
            lignes["reps"].append(beta.n)
    return pa.table(lignes)


def from_table(table: pa.Table):
    """Reconstruit {postes: {(c_a², τ): Estimate}} depuis une table écrite par `to_table`."""
    grilles: dict[int, dict[tuple[float, float], Estimate]] = {}
    colonnes = table.to_pydict()
    for i in range(table.num_rows):
        postes = int(colonnes["postes"][i])
        cle = (float(colonnes["c_a2"][i]), float(colonnes["tau_jours"][i]))
        grilles.setdefault(postes, {})[cle] = Estimate(
            mean=float(colonnes["beta"][i]),
            std=float(colonnes["beta_std"][i]),
            n=int(colonnes["reps"][i]),
        )
    return grilles


def matrice(cases) -> list[list[float]]:
    """β en matrice [τ][c_a²], dans l'ordre des deux axes : ce qu'attend `contour`."""
    return [[cases[(c_a2, tau)].mean for c_a2 in C_A2_VALUES] for tau in TAU_DAYS]


def couverture_requise(cases, level: float) -> dict[float, float | None]:
    """Pour chaque c_a², la couverture τ à partir de laquelle β atteint `level`.

    Interpolation linéaire entre les deux τ qui encadrent le franchissement, en balayant
    du bas vers le haut. None si le niveau n'est jamais atteint dans le domaine exploré :
    c'est une information en soi, pas un trou à masquer.
    """
    requis = {}
    for c_a2 in C_A2_VALUES:
        betas = [cases[(c_a2, tau)].mean for tau in TAU_DAYS]
        trouve = None
        for i in range(len(TAU_DAYS) - 1):
            bas, haut = betas[i], betas[i + 1]
            if bas < level <= haut:
                part = (level - bas) / (haut - bas) if haut != bas else 0.0
                trouve = TAU_DAYS[i] + part * (TAU_DAYS[i + 1] - TAU_DAYS[i])
                break
        if trouve is None and betas[0] >= level:
            trouve = TAU_DAYS[0]  # déjà atteint au plus bas stockage exploré
        requis[c_a2] = trouve
    return requis


def print_table(grilles, base: SimConfig) -> None:
    """La carte en chiffres : β case par case, puis la couverture requise par niveau."""
    for postes in POSTES:
        cases = grilles[postes]
        print(f"\n{'=' * 96}\nTaux de service β, {postes} poste(s) de déchargement\n{'=' * 96}")
        print(f"{'τ (jours)':>10}", end="")
        for c_a2 in C_A2_VALUES:
            print(f"{f'c_a²={c_a2:.2f}':>14}", end="")
        print()
        for tau in TAU_DAYS:
            print(f"{tau:>10.0f}", end="")
            for c_a2 in C_A2_VALUES:
                b = cases[(c_a2, tau)]
                print(f"  {b.mean:>6.3f}±{b.half_width:<5.3f}", end="")
            print()

    print(f"\n{'=' * 96}\nCouverture τ requise, en jours de consommation\n{'=' * 96}")
    print(f"{'niveau':>8} {'postes':>7}", end="")
    for c_a2 in C_A2_VALUES:
        print(f"{f'c_a²={c_a2:.2f}':>14}", end="")
    print()
    for level in LEVELS:
        for postes in POSTES:
            requis = couverture_requise(grilles[postes], level)
            print(f"{level:>8.2f} {postes:>7}", end="")
            for c_a2 in C_A2_VALUES:
                valeur = requis[c_a2]
                print(f"{'jamais' if valeur is None else f'{valeur:.1f} j':>14}", end="")
            print()
    print("\n« jamais » = le niveau n'est pas atteint dans le domaine exploré "
          f"(τ ≤ {max(TAU_DAYS):.0f} jours).")


def bande_du_site(base: SimConfig, raw) -> tuple[float, float, float]:
    """Couverture τ du site : valeur centrale et bornes de la fourchette publiée.

    Les deux capacités de stockage étant de statut `inconnu`, le site n'est pas un point
    sur la carte mais une bande. C'est exactement ce que la figure doit montrer.
    """
    d = consommation_journaliere_t(base)
    port = raw["stockage_port"]["capacite_stockyard_pellets"]
    usine = raw["usine"]["capacite_stock_pellets_usine"]
    centre = (float(port["value"]) + float(usine["value"])) / d
    bas = (float(port["range"][0]) + float(usine["range"][0])) / d
    haut = (float(port["range"][1]) + float(usine["range"][1])) / d
    return bas, centre, haut


def write_figure(grilles, base: SimConfig, raw, scenario: str, reps: int,
                 path=DEFAULT_FIGURE) -> Path:
    """Trace les frontières β = 0,95 et β = 0,99 dans le plan (c_a², τ).

    Une **teinte** par nombre de postes, un **style de trait** par niveau de service :
    jamais deux informations sur le même canal. La bande grise rappelle où se situe le
    site d'après ses hypothèses, qui sont de statut `inconnu` — d'où une bande et non un
    point. Au-dessus d'une courbe, le taux de service est atteint.
    """
    figure, ax = plt.subplots(figsize=(10.5, 8.0), constrained_layout=True)
    bas, centre, haut = bande_du_site(base, raw)

    ax.axhspan(bas, haut, color="0.93", zorder=0)
    for borne in (bas, haut):
        ax.axhline(borne, color="0.70", linewidth=0.9, zorder=1)
    ax.axhline(centre, color="0.55", linewidth=1.0, linestyle=":", zorder=1)
    ax.text(0.985, centre, f"hypothèse centrale du site : {centre:.0f} j ",
            transform=ax.get_yaxis_transform(), va="bottom", ha="right",
            fontsize=8.5, color="0.35")
    ax.text(0.985, haut, f"fourchette publiée du site : {bas:.0f} à {haut:.0f} j ",
            transform=ax.get_yaxis_transform(), va="top", ha="right",
            fontsize=8.5, color="0.35")

    traces = []
    for postes in POSTES:
        valeurs = matrice(grilles[postes])
        for level in LEVELS:
            contour = ax.contour(
                C_A2_VALUES, TAU_DAYS, valeurs, levels=[level],
                colors=[COULEURS[postes]], linestyles=[STYLES[level]], linewidths=2.2,
                zorder=3,
            )
            if any(len(c) for c in contour.allsegs):
                traces.append((postes, level))

    for postes in POSTES:
        ax.plot([], [], color=COULEURS[postes], linewidth=2.2,
                label=f"{postes} poste de déchargement" if postes == 1
                else f"{postes} postes de déchargement")
    for level in LEVELS:
        ax.plot([], [], color="0.35", linewidth=2.2, linestyle=STYLES[level],
                label=f"β = {level:.2f}".replace(".", ","))
    # en bas à droite : la zone où aucun niveau n'est atteint, donc vide de courbes
    ax.legend(loc="lower right", fontsize=9, framealpha=0.95)

    # la moitié droite est vide de courbes, et c'est le résultat : au-delà de Poisson,
    # aucun des deux niveaux n'est atteint même au sommet du domaine de stockage
    hors_atteinte = max(
        (c_a2 for postes in POSTES
         for c_a2, tau in couverture_requise(grilles[postes], min(LEVELS)).items()
         if tau is not None),
        default=None,
    )
    if hors_atteinte is not None and hors_atteinte < max(C_A2_VALUES):
        milieu = (hors_atteinte + max(C_A2_VALUES)) / 2
        ax.text(milieu, (min(TAU_DAYS) + max(TAU_DAYS)) / 2,
                f"au-delà de c_a² ≈ {hors_atteinte:.2f},\nβ = {min(LEVELS):.2f}".replace(".", ",")
                + f"\nn'est atteint par aucune\ndes deux configurations,\nmême à "
                  f"{max(TAU_DAYS):.0f} jours de stock",
                ha="center", va="center", fontsize=9.5, color="0.35", style="italic")

    ax.set_xlim(min(C_A2_VALUES), max(C_A2_VALUES))
    ax.set_ylim(min(TAU_DAYS), max(TAU_DAYS))
    ax.set_xlabel("c_a² — irrégularité des arrivées (carré du coefficient de variation\n"
                  "des intervalles) : 0 = parfaitement régulier, 1 = processus de Poisson")
    ax.set_ylabel("τ — couverture de stock, en jours de consommation de l'aciérie\n"
                  "(capacité port + usine) ÷ (pellets consommés par jour)")
    ax.grid(True, color="0.90", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)

    manquants = [(p, n) for p in POSTES for n in LEVELS if (p, n) not in traces]
    lignes = [
        f"Frontière de service du corridor — scénario {scenario}",
        "Au-dessus d'une courbe, le taux de service est atteint : pour une irrégularité",
        "d'affrètement donnée, elle se lit en jours de stock.",
        (f"β = production ÷ cible, moyenne sur {reps} réplications d'un an, "
         f"{len(C_A2_VALUES)} × {len(TAU_DAYS)} points par courbe."),
        "Axes sans dimension : un autre site se place sur la même carte. Ce qui ne s'y",
        "transpose pas est la charge du poste, que distinguent les deux couleurs.",
    ]
    if manquants:
        absents = ", ".join(
            f"{p} poste{'s' if p > 1 else ''} / β = {n:.2f}".replace(".", ",")
            for p, n in manquants
        )
        lignes.append(f"Courbe absente ({absents}) : niveau atteint nulle part dans le "
                      "domaine exploré.")
    figure.suptitle("\n".join(lignes), fontsize=10.0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--from-parquet", type=Path, default=None,
                        help="retrace la figure depuis une carte déjà calculée, sans simuler")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    shares = storage_shares(raw)
    base = SimConfig.from_scenario(load_scenario(args.scenario))
    d = consommation_journaliere_t(base)
    bas, centre, haut = bande_du_site(base, raw)

    print(f"scénario {args.scenario} : consommation {d:,.0f} t de pellets par jour"
          .replace(",", " "))
    print(f"  cible annuelle {cible_t(base, 1.0) / 1e6:.3f} Mt de DRI")
    print(f"  couverture du site : {centre:.1f} jours (fourchette {bas:.1f} à {haut:.1f})")
    print(f"  carte : {len(C_A2_VALUES)} × {len(TAU_DAYS)} points × {len(POSTES)} "
          f"configurations de poste, {args.reps} réplications par point")

    if args.from_parquet:
        grilles = from_table(pq.read_table(args.from_parquet))
        reps = max(b.n for cases in grilles.values() for b in cases.values())
        print(f"  carte relue depuis {args.from_parquet} ({reps} réplications par point)")
    else:
        grilles = beta_grid(base, shares, args.reps, args.years, progress=True)
        reps = args.reps
        args.table.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(to_table(grilles), args.table)
        print(f"\ncarte écrite : {args.table}")
    print_table(grilles, base)
    chemin = write_figure(grilles, base, raw, args.scenario, reps, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
