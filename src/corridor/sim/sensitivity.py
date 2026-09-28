"""Analyse de sensibilité : irrégularité des arrivées × capacité de stockage (étape 4).

Les deux paramètres les plus déterminants du modèle sont aussi les moins sourcés :

- `simulation.dispersion_arrivees` (statut estimé) : la régularité de l'affrètement ;
- les deux capacités de stockage (statut **inconnu**), qui décident du tampon disponible
  pour absorber une rafale d'arrivées.

Ce module balaie la grille des deux, pour `baseline` et `phase2`, et mesure la perte de
production de DRI par rapport à la cible de chaque scénario
(`capacite_dri × taux_utilisation × jours_ouvrés`). Exprimer la perte en pourcentage rend
les deux scénarios comparables sur une même échelle de couleur, alors que leurs cibles
diffèrent d'un facteur deux.

Le stockage balaie la **somme** des deux fourchettes, répartie entre port et usine selon
les proportions des valeurs centrales de `assumptions.yaml`. Conséquence à garder en
tête : aux totaux les plus bas, la part du port peut descendre sous son propre minimum
publié — la fourchette du total est plus large que ce que chaque borne autorise
séparément.

Usage :

    python -m corridor.sim.sensitivity            # grille complète, ~2 à 3 minutes
    python -m corridor.sim.sensitivity --reps 5   # version rapide
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # aucune fenêtre : on écrit un fichier
import matplotlib.pyplot as plt
import pyarrow as pa
import pyarrow.parquet as pq

from corridor.sim.config import (
    ASSUMPTIONS_PATH,
    SimConfig,
    load_scenario,
    load_yaml,
)
from corridor.sim.run import replicate
from corridor.sim.stats import (
    Estimate,
    estimate,
    paired_difference,
    pearson,
    significant,
    spearman,
)

#: Grille demandée : de l'affrètement parfaitement régulier au processus de Poisson.
DISPERSIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
SCENARIOS = ("baseline", "phase2")
STORAGE_LEVELS = 5
DEFAULT_REPS = 30
DEFAULT_OUT_DIR = Path("data/sim")
DEFAULT_FIGURE = Path("docs/sensibilite_dri.png")
#: Opacité d'une case qui ne se distingue pas d'une voisine : lisible, mais en retrait.
PALE_ALPHA = 0.35

_STOCKYARD_KEY = ("stockage_port", "capacite_stockyard_pellets")
_PLANT_KEY = ("usine", "capacite_stock_pellets_usine")


#: Indicateurs agrégés par point de grille, chacun en moyenne, écart-type et IC 95 %.
GRID_INDICATORS = (
    "dri_production_t",
    "dri_loss_pct",
    "dri_stop_hours",
    "wait_mean_h",
    "wait_p90_h",
    "berth_occupancy",
    "rame_utilisation",
    "waiting_tonnage_mean_t",
)


@dataclass(frozen=True, slots=True)
class GridPoint:
    """Un point de la grille : un scénario, une dispersion, une capacité de stockage.

    Chaque indicateur est une `Estimate` (moyenne, écart-type, IC 95 %). Les pertes
    réplication par réplication sont conservées : c'est ce qui permet de comparer deux
    cases voisines en données appariées, puisqu'elles partagent leurs graines.
    """

    scenario: str
    dispersion: float
    storage_total_t: float
    stockyard_t: float
    plant_stock_t: float
    reps: int
    years: float
    dri_target_t: float
    dri_production_t: Estimate
    dri_loss_pct: Estimate
    dri_stop_hours: Estimate
    wait_mean_h: Estimate
    wait_p90_h: Estimate
    berth_occupancy: Estimate
    rame_utilisation: Estimate
    waiting_tonnage_mean_t: Estimate
    loss_by_rep: tuple[float, ...]


def _entry(raw: dict[str, Any], key: tuple[str, str]) -> dict[str, Any]:
    section, name = key
    return raw[section][name]


def storage_bounds(raw: dict[str, Any]) -> tuple[float, float]:
    """Fourchette de la capacité **totale** de stockage : somme des deux fourchettes."""
    port = _entry(raw, _STOCKYARD_KEY)["range"]
    usine = _entry(raw, _PLANT_KEY)["range"]
    return float(port[0] + usine[0]), float(port[1] + usine[1])


def storage_shares(raw: dict[str, Any]) -> tuple[float, float]:
    """Proportions port / usine, déduites des valeurs centrales des hypothèses."""
    port = float(_entry(raw, _STOCKYARD_KEY)["value"])
    usine = float(_entry(raw, _PLANT_KEY)["value"])
    total = port + usine
    return port / total, usine / total


def _levels_over_range(raw: dict[str, Any], levels: int) -> list[float]:
    if levels < 1:
        raise ValueError(f"au moins un niveau de stockage attendu, reçu {levels}")
    bas, haut = storage_bounds(raw)
    if levels == 1:
        return [bas]
    pas = (haut - bas) / (levels - 1)
    return [bas + i * pas for i in range(levels)]


def storage_levels(raw: dict[str, Any], levels: int) -> list[float]:
    """`levels` capacités totales régulièrement espacées sur la fourchette."""
    return _levels_over_range(raw, levels)


def with_storage(
    config: SimConfig,
    total_t: float,
    shares: tuple[float, float],
) -> SimConfig:
    """Rejoue une configuration avec une autre capacité totale de stockage.

    Les stocks initiaux sont recalculés depuis les nouvelles capacités : `replace` seul
    laisserait les stocks de l'ancienne configuration, et le point de grille mesurerait
    alors autre chose que ce qu'il annonce.
    """
    part_port, part_usine = shares
    stockyard = total_t * part_port
    usine = total_t * part_usine
    if config.politique_expedition == "pull" and config.charge_par_train_t > usine:
        raise ValueError(
            f"politique pull impossible : une charge de {config.charge_par_train_t:.0f} t ne "
            f"tient pas dans un stock usine de {usine:.0f} t"
        )
    return dataclasses.replace(
        config,
        capacite_stockyard_t=stockyard,
        capacite_stock_usine_t=usine,
        stock_initial_stockyard_t=stockyard * config.fraction_stock_initial,
        stock_initial_usine_t=usine * config.fraction_stock_initial,
    )


def evaluate(
    config: SimConfig,
    scenario: str,
    dispersion: float,
    total_t: float,
    shares: tuple[float, float],
    reps: int,
    years: float,
    seed: int,
) -> GridPoint:
    """Évalue un point de grille : `reps` réplications, chaque indicateur avec son IC 95 %."""
    point_config = with_storage(
        dataclasses.replace(config, dispersion_arrivees=dispersion), total_t, shares
    )
    results = replicate(point_config, years=years, reps=reps, seed=seed, scenario=scenario)
    cible = point_config.debit_dri_t_par_h * point_config.heures_ouvrees_par_an * years
    pertes = tuple((cible - r.dri_production_t) / cible * 100.0 for r in results)

    def serie(field: str) -> Estimate:
        return estimate([getattr(r, field) for r in results])

    return GridPoint(
        scenario=scenario,
        dispersion=dispersion,
        storage_total_t=total_t,
        stockyard_t=point_config.capacite_stockyard_t,
        plant_stock_t=point_config.capacite_stock_usine_t,
        reps=reps,
        years=years,
        dri_target_t=cible,
        dri_production_t=serie("dri_production_t"),
        dri_loss_pct=estimate(pertes),
        dri_stop_hours=serie("dri_stop_hours"),
        wait_mean_h=serie("wait_mean_h"),
        wait_p90_h=serie("wait_p90_h"),
        berth_occupancy=serie("berth_occupancy"),
        rame_utilisation=serie("rame_utilisation"),
        waiting_tonnage_mean_t=serie("waiting_tonnage_mean_t"),
        loss_by_rep=pertes,
    )


def run_grid(
    scenarios: Sequence[str] = SCENARIOS,
    dispersions: Sequence[float] = DISPERSIONS,
    storage_levels: int = STORAGE_LEVELS,
    reps: int = DEFAULT_REPS,
    years: float = 1.0,
    seed: int | None = None,
    assumptions_path: str | Path = ASSUMPTIONS_PATH,
    progress: bool = False,
) -> list[GridPoint]:
    """Parcourt la grille complète et renvoie un point par combinaison.

    La graine est commune à tous les points : les scénarios se comparent alors sur les
    mêmes séquences d'arrivées, et l'écart observé vient de la grille, pas du hasard.
    """
    raw = load_yaml(assumptions_path)
    shares = storage_shares(raw)
    totals = _levels_over_range(raw, storage_levels)
    points: list[GridPoint] = []
    for scenario in scenarios:
        config = SimConfig.from_scenario(load_scenario(scenario, assumptions_path))
        graine = seed if seed is not None else config.graine_par_defaut
        for total in totals:
            for dispersion in dispersions:
                point = evaluate(
                    config, scenario, dispersion, total, shares, reps, years, graine
                )
                points.append(point)
                if progress:
                    print(
                        f"  {scenario:20} stockage {total / 1e3:6.0f} kt  "
                        f"dispersion {dispersion:4.2f}  perte {point.dri_loss_pct.mean:5.1f}"
                        f" ± {point.dri_loss_pct.half_width:.1f} %"
                    )
    return points


def to_table(points: Sequence[GridPoint]) -> pa.Table:
    """Table Arrow à plat : chaque indicateur donne quatre colonnes, pour DuckDB.

    `x` devient `x_mean`, `x_std`, `x_ci95_low`, `x_ci95_high` ; les pertes par
    réplication sont gardées dans une colonne liste, `loss_by_rep`.
    """
    if not points:
        raise ValueError("aucun point de grille à convertir")
    colonnes: dict[str, list] = {}
    for point in points:
        for f in dataclasses.fields(GridPoint):
            valeur = getattr(point, f.name)
            if isinstance(valeur, Estimate):
                for suffixe, nombre in (
                    ("mean", valeur.mean),
                    ("std", valeur.std),
                    ("ci95_low", valeur.low),
                    ("ci95_high", valeur.high),
                ):
                    colonnes.setdefault(f"{f.name}_{suffixe}", []).append(nombre)
            else:
                colonnes.setdefault(f.name, []).append(
                    list(valeur) if isinstance(valeur, tuple) else valeur
                )
    return pa.table(colonnes)


def write_parquet(points: Sequence[GridPoint], out_dir: str | Path = DEFAULT_OUT_DIR) -> Path:
    """Écrit la grille dans `out_dir`, horodatée en UTC."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"sensibilite_{stamp}.parquet"
    pq.write_table(to_table(points), path)
    return path


def _cells(points: Sequence[GridPoint], scenario: str) -> tuple[
    list[float], list[float], dict[tuple[float, float], GridPoint]
]:
    retenus = [p for p in points if p.scenario == scenario]
    if not retenus:
        raise ValueError(f"aucun point de grille pour le scénario {scenario}")
    dispersions = sorted({p.dispersion for p in retenus})
    totals = sorted({p.storage_total_t for p in retenus})
    return dispersions, totals, {(p.storage_total_t, p.dispersion): p for p in retenus}


def loss_matrix(
    points: Sequence[GridPoint],
    scenario: str,
) -> tuple[list[float], list[float], list[list[float]]]:
    """Matrice des pertes moyennes : lignes = stockage croissant, colonnes = dispersion."""
    dispersions, totals, index = _cells(points, scenario)
    return (
        dispersions,
        totals,
        [[index[(total, d)].dri_loss_pct.mean for d in dispersions] for total in totals],
    )


def distinct_from_neighbours(
    points: Sequence[GridPoint],
    scenario: str,
) -> list[list[bool]]:
    """Chaque case se distingue-t-elle significativement de toutes ses voisines ?

    Voisines : les quatre cases adjacentes (même stockage et dispersion voisine, ou même
    dispersion et stockage voisin). La comparaison est appariée — les cases partagent
    leurs graines — et une case est « non distincte » si l'IC 95 % de sa différence avec
    au moins une voisine contient zéro. Une telle case ne doit pas être lue comme un
    palier : sa perte pourrait être celle de sa voisine.
    """
    dispersions, totals, index = _cells(points, scenario)
    matrice: list[list[bool]] = []
    for i, total in enumerate(totals):
        ligne: list[bool] = []
        for j, d in enumerate(dispersions):
            case = index[(total, d)]
            voisines = [
                index[(totals[vi], dispersions[vj])]
                for vi, vj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1))
                if 0 <= vi < len(totals) and 0 <= vj < len(dispersions)
            ]
            ligne.append(
                all(
                    significant(paired_difference(case.loss_by_rep, v.loss_by_rep))
                    for v in voisines
                )
            )
        matrice.append(ligne)
    return matrice


@dataclass(frozen=True, slots=True)
class FloatingStockTest:
    """Résultat du test : la perte de DRI baisse-t-elle quand le stock flottant monte ?

    `overall` porte sur tous les points de la grille. `within_dispersion` reprend le même
    calcul **à dispersion fixée** : c'est le garde-fou, car l'irrégularité des arrivées
    fait varier les deux grandeurs à la fois et peut fabriquer à elle seule une corrélation.
    """

    n: int
    pearson_r: float
    spearman_r: float
    within_dispersion: dict[float, tuple[int, float]]  # dispersion → (effectif, Pearson)


def floating_stock_test(points: Sequence[GridPoint]) -> FloatingStockTest:
    """Corrèle le stock flottant moyen en rade et la perte de production de DRI.

    L'hypothèse à tester : un tonnage élevé en attente en rade **accompagne** une perte
    plus faible, parce que ce minerai déjà présent sert de tampon mobilisable dès que le
    stock usine baisse. Une corrélation négative est cohérente avec cette lecture ; elle
    ne la démontre pas (voir la mise en garde du README).
    """
    if len(points) < 2:
        raise ValueError("au moins deux points de grille sont nécessaires")
    stock = [p.waiting_tonnage_mean_t.mean for p in points]
    perte = [p.dri_loss_pct.mean for p in points]
    par_dispersion: dict[float, tuple[int, float]] = {}
    for d in sorted({p.dispersion for p in points}):
        groupe = [p for p in points if p.dispersion == d]
        if len(groupe) >= 2:
            par_dispersion[d] = (
                len(groupe),
                pearson(
                    [p.waiting_tonnage_mean_t.mean for p in groupe],
                    [p.dri_loss_pct.mean for p in groupe],
                ),
            )
    return FloatingStockTest(
        n=len(points),
        pearson_r=pearson(stock, perte),
        spearman_r=spearman(stock, perte),
        within_dispersion=par_dispersion,
    )


def write_heatmaps(
    points: Sequence[GridPoint],
    path: str | Path = DEFAULT_FIGURE,
    scenarios: Sequence[str] | None = None,
) -> Path:
    """Écrit les cartes de chaleur de la perte de DRI, une par scénario, échelle commune.

    La figure doit se lire seule : chaque case porte sa perte moyenne et la demi-largeur
    de son IC 95 %, les axes portent leurs unités, et une case **pâle** signale une perte
    qui ne se distingue pas significativement d'au moins une case voisine
    (`distinct_from_neighbours`) — donc un écart de couleur qu'il ne faut pas lire.
    """
    if not points:
        raise ValueError("aucun point de grille à tracer")
    noms = list(scenarios) if scenarios else sorted({p.scenario for p in points})
    matrices = {nom: loss_matrix(points, nom) for nom in noms}
    distinctes = {nom: distinct_from_neighbours(points, nom) for nom in noms}
    vmax = max(max(max(ligne) for ligne in m[2]) for m in matrices.values())
    vmax = max(vmax, 1.0)

    figure, axes = plt.subplots(
        1, len(noms), figsize=(6.4 * len(noms), 6.0), squeeze=False, constrained_layout=True
    )
    image = None
    for ax, nom in zip(axes[0], noms, strict=True):
        dispersions, totals, matrice = matrices[nom]
        _, _, index = _cells(points, nom)
        opacite = [[1.0 if nette else PALE_ALPHA for nette in ligne] for ligne in distinctes[nom]]
        image = ax.imshow(
            matrice, origin="lower", aspect="auto", cmap="YlOrRd", vmin=0.0, vmax=vmax,
            alpha=opacite,
        )
        ax.set_xticks(range(len(dispersions)), [f"{d:.2f}" for d in dispersions])
        ax.set_yticks(range(len(totals)), [f"{t / 1e3:.0f}" for t in totals])
        ax.set_xlabel("dispersion des arrivées\n(0 = régulières, 1 = Poisson)")
        ax.set_ylabel("capacité totale de stockage (kt)\nport + usine")
        reps = {p.reps for p in points if p.scenario == nom}
        ax.set_title(f"{nom} — {max(reps)} réplications/point", fontweight="bold")
        for i, total in enumerate(totals):
            for j, d in enumerate(dispersions):
                perte = index[(total, d)].dri_loss_pct
                nette = distinctes[nom][i][j]
                couleur = "white" if nette and perte.mean > 0.6 * vmax else "black"
                ax.text(j, i + 0.08, f"{perte.mean:.1f}", ha="center", va="center",
                        fontsize=10, color=couleur, fontweight="bold" if nette else "normal")
                ax.text(j, i - 0.22, f"± {perte.half_width:.1f}", ha="center", va="center",
                        fontsize=7, color=couleur)
    barre = figure.colorbar(image, ax=axes[0], shrink=0.85)
    barre.set_label("perte de production DRI (% de la cible du scénario)")
    figure.suptitle(
        "Perte de production de DRI selon la régularité des arrivées et le stockage\n"
        "Chaque case : perte moyenne ± demi-largeur de l'IC 95 %. Case pâle : ne se distingue "
        "pas d'au moins une case voisine\n(IC 95 % de la différence appariée contenant 0). "
        "Dispersion et capacités de stockage ne sont pas sourcées (statuts estimé et inconnu).",
        fontsize=10.5,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyse de sensibilité du corridor : arrivées × stockage."
    )
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    parser.add_argument("--dispersions", nargs="+", type=float, default=list(DISPERSIONS))
    parser.add_argument("--storage-levels", type=int, default=STORAGE_LEVELS)
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    args = parser.parse_args(argv)

    print(
        f"Grille : {len(args.scenarios)} scénarios × {len(args.dispersions)} dispersions "
        f"× {args.storage_levels} niveaux de stockage × {args.reps} réplications"
    )
    points = run_grid(
        scenarios=args.scenarios,
        dispersions=args.dispersions,
        storage_levels=args.storage_levels,
        reps=args.reps,
        years=args.years,
        seed=args.seed,
        progress=True,
    )
    table = write_parquet(points, args.out)
    figure = write_heatmaps(points, args.figure, scenarios=args.scenarios)
    pire = max(points, key=lambda p: p.dri_loss_pct.mean)
    print(f"\nperte maximale {pire.dri_loss_pct.mean:.1f} ± {pire.dri_loss_pct.half_width:.1f} % "
          f"({pire.scenario}, dispersion {pire.dispersion:.2f}, "
          f"stockage {pire.storage_total_t / 1e3:.0f} kt)")
    print(f"{len(points)} points écrits dans {table}")
    print(f"figure écrite dans {figure}")

    test = floating_stock_test(points)
    print()
    print(f"Stock flottant en rade contre perte de DRI, sur {test.n} points :")
    print(f"  Pearson {test.pearson_r:+.2f}   Spearman {test.spearman_r:+.2f}")
    print("  à dispersion fixée (le garde-fou) :")
    for dispersion, (effectif, r) in test.within_dispersion.items():
        print(f"    dispersion {dispersion:4.2f} ({effectif} points) : Pearson {r:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
