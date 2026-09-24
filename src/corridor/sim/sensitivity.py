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
from corridor.sim.run import replicate, summarize

#: Grille demandée : de l'affrètement parfaitement régulier au processus de Poisson.
DISPERSIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
SCENARIOS = ("baseline", "phase2")
STORAGE_LEVELS = 5
DEFAULT_REPS = 30
DEFAULT_OUT_DIR = Path("data/sim")
DEFAULT_FIGURE = Path("docs/sensibilite_dri.png")

_STOCKYARD_KEY = ("stockage_port", "capacite_stockyard_pellets")
_PLANT_KEY = ("usine", "capacite_stock_pellets_usine")


@dataclass(frozen=True, slots=True)
class GridPoint:
    """Un point de la grille : un scénario, une dispersion, une capacité de stockage."""

    scenario: str
    dispersion: float
    storage_total_t: float
    stockyard_t: float
    plant_stock_t: float
    reps: int
    years: float
    dri_target_t: float
    dri_production_mean_t: float
    dri_loss_pct: float
    dri_stop_hours: float
    wait_mean_h: float
    wait_p90_h: float
    berth_occupancy: float
    rame_utilisation: float


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
    """Évalue un point de grille : `reps` réplications, agrégées en une perte moyenne."""
    point_config = with_storage(
        dataclasses.replace(config, dispersion_arrivees=dispersion), total_t, shares
    )
    results = replicate(point_config, years=years, reps=reps, seed=seed, scenario=scenario)
    synthese = summarize(results)
    cible = point_config.debit_dri_t_par_h * point_config.heures_ouvrees_par_an * years
    production = synthese["dri_production_t"]
    return GridPoint(
        scenario=scenario,
        dispersion=dispersion,
        storage_total_t=total_t,
        stockyard_t=point_config.capacite_stockyard_t,
        plant_stock_t=point_config.capacite_stock_usine_t,
        reps=reps,
        years=years,
        dri_target_t=cible,
        dri_production_mean_t=production,
        dri_loss_pct=(cible - production) / cible * 100.0,
        dri_stop_hours=synthese["dri_stop_hours"],
        wait_mean_h=synthese["wait_mean_h"],
        wait_p90_h=synthese["wait_p90_h"],
        berth_occupancy=synthese["berth_occupancy"],
        rame_utilisation=synthese["rame_utilisation"],
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
                        f"dispersion {dispersion:4.2f}  perte {point.dri_loss_pct:5.1f} %"
                    )
    return points


def to_table(points: Sequence[GridPoint]) -> pa.Table:
    """Convertit la grille en table Arrow, une colonne par champ."""
    if not points:
        raise ValueError("aucun point de grille à convertir")
    rows = [dataclasses.asdict(p) for p in points]
    return pa.table({name: [row[name] for row in rows] for name in rows[0]})


def write_parquet(points: Sequence[GridPoint], out_dir: str | Path = DEFAULT_OUT_DIR) -> Path:
    """Écrit la grille dans `out_dir`, horodatée en UTC."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"sensibilite_{stamp}.parquet"
    pq.write_table(to_table(points), path)
    return path


def loss_matrix(
    points: Sequence[GridPoint],
    scenario: str,
) -> tuple[list[float], list[float], list[list[float]]]:
    """Matrice des pertes d'un scénario : lignes = stockage croissant, colonnes = dispersion."""
    retenus = [p for p in points if p.scenario == scenario]
    if not retenus:
        raise ValueError(f"aucun point de grille pour le scénario {scenario}")
    dispersions = sorted({p.dispersion for p in retenus})
    totals = sorted({p.storage_total_t for p in retenus})
    index = {(p.storage_total_t, p.dispersion): p.dri_loss_pct for p in retenus}
    return (
        dispersions,
        totals,
        [[index[(total, d)] for d in dispersions] for total in totals],
    )


def write_heatmaps(
    points: Sequence[GridPoint],
    path: str | Path = DEFAULT_FIGURE,
    scenarios: Sequence[str] | None = None,
) -> Path:
    """Écrit les cartes de chaleur de la perte de DRI, une par scénario, échelle commune.

    La figure doit se lire seule : chaque case porte sa valeur, les axes portent leurs
    unités, et le sous-titre rappelle que les deux paramètres balayés ne sont pas sourcés.
    """
    if not points:
        raise ValueError("aucun point de grille à tracer")
    noms = list(scenarios) if scenarios else sorted({p.scenario for p in points})
    matrices = {nom: loss_matrix(points, nom) for nom in noms}
    vmax = max(max(max(ligne) for ligne in m[2]) for m in matrices.values())
    vmax = max(vmax, 1.0)

    figure, axes = plt.subplots(
        1, len(noms), figsize=(6.2 * len(noms), 5.4), squeeze=False, constrained_layout=True
    )
    image = None
    for ax, nom in zip(axes[0], noms, strict=True):
        dispersions, totals, matrice = matrices[nom]
        image = ax.imshow(
            matrice, origin="lower", aspect="auto", cmap="YlOrRd", vmin=0.0, vmax=vmax
        )
        ax.set_xticks(range(len(dispersions)), [f"{d:.2f}" for d in dispersions])
        ax.set_yticks(range(len(totals)), [f"{t / 1e3:.0f}" for t in totals])
        ax.set_xlabel("dispersion des arrivées\n(0 = régulières, 1 = Poisson)")
        ax.set_ylabel("capacité totale de stockage (kt)\nport + usine")
        reps = {p.reps for p in points if p.scenario == nom}
        ax.set_title(f"{nom} — {max(reps)} réplications/point", fontweight="bold")
        for i, ligne in enumerate(matrice):
            for j, valeur in enumerate(ligne):
                ax.text(
                    j,
                    i,
                    f"{valeur:.1f}",
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="white" if valeur > 0.6 * vmax else "black",
                )
    barre = figure.colorbar(image, ax=axes[0], shrink=0.85)
    barre.set_label("perte de production DRI (% de la cible du scénario)")
    figure.suptitle(
        "Perte de production de DRI selon la régularité des arrivées et le stockage\n"
        "Cible = capacité DRI × taux d'utilisation × jours ouvrés. "
        "Dispersion et capacités de stockage ne sont pas sourcées (statuts estimé et inconnu).",
        fontsize=11,
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
    pire = max(points, key=lambda p: p.dri_loss_pct)
    print(f"\nperte maximale {pire.dri_loss_pct:.1f} % "
          f"({pire.scenario}, dispersion {pire.dispersion:.2f}, "
          f"stockage {pire.storage_total_t / 1e3:.0f} kt)")
    print(f"{len(points)} points écrits dans {table}")
    print(f"figure écrite dans {figure}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
