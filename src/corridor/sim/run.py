"""Exécution des réplications et écriture des résultats en Parquet (étape 4).

Usage :

    python -m corridor.sim.run --scenario baseline --years 1 --reps 50

Chaque réplication produit une ligne ; le fichier Parquet de `data/sim/` en contient
autant que de réplications, plus les colonnes de provenance (scénario, graine,
dispersion) qui permettent de comparer plusieurs exécutions dans une même requête
DuckDB.

`--dispersion` force la dispersion des arrivées sans toucher aux fichiers de scénario :
c'est le bouton qui sert à comparer un affrètement régulier (0) à un affrètement
erratique (1 ou plus) à demande égale.
"""

from __future__ import annotations

import argparse
import dataclasses
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from corridor.sim.config import EXPEDITION_POLICIES, SimConfig, load_scenario
from corridor.sim.model import SimResult, percentile, simulate
from corridor.sim.stats import Estimate, estimate

DEFAULT_OUT_DIR = Path("data/sim")

#: Indicateurs agrégés sur les réplications (moyenne, écart-type, IC 95 %).
SUMMARY_FIELDS = (
    "pellets_delivered_t",
    "pellets_railed_t",
    "dri_production_t",
    "dri_stop_hours",
    "dri_planned_stop_hours",
    "vessels_arrived",
    "vessels_served",
    "wait_mean_h",
    "wait_p90_h",
    "berth_occupancy",
    "stockyard_min_t",
    "stockyard_mean_t",
    "plant_stock_min_t",
    "plant_stock_mean_t",
    "rame_utilisation",
    "rame_blocked_h",
    "waiting_tonnage_mean_t",
)


def replicate(
    config: SimConfig,
    years: float,
    reps: int,
    seed: int,
    scenario: str,
) -> list[SimResult]:
    """Rejoue le même scénario `reps` fois, chaque réplication avec sa propre graine.

    Les graines sont tirées d'un générateur maître : une seule graine en entrée suffit
    à rejouer l'ensemble à l'identique, et deux réplications ne partagent jamais leur
    séquence aléatoire.
    """
    master = random.Random(seed)
    graines = [master.randrange(2**32) for _ in range(reps)]
    return [
        simulate(config, seed=graine, years=years, scenario=scenario, rep=rep)
        for rep, graine in enumerate(graines)
    ]


def results_to_table(results: Sequence[SimResult]) -> pa.Table:
    """Convertit les réplications en table Arrow, une colonne par champ du résultat."""
    if not results:
        raise ValueError("aucune réplication à convertir")
    rows = [dataclasses.asdict(r) for r in results]
    return pa.table({name: [row[name] for row in rows] for name in rows[0]})


def write_parquet(
    results: Sequence[SimResult],
    out_dir: str | Path = DEFAULT_OUT_DIR,
    scenario: str = "sim",
) -> Path:
    """Écrit les réplications dans `out_dir`, horodatées en UTC pour ne rien écraser."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{scenario}_{stamp}.parquet"
    pq.write_table(results_to_table(results), path)
    return path


def summarize(results: Sequence[SimResult]) -> dict[str, Estimate]:
    """Moyenne, écart-type et intervalle à 95 % de chaque indicateur sur les réplications.

    Le p90 d'attente est lui-même une statistique par réplication : `wait_p90_h` est donc
    la moyenne des p90, avec son intervalle. Une moyenne d'attentes peut dépasser un p90
    quand un seul navire sur quarante attend : le p90 d'une réplication reste alors à zéro.
    """
    if not results:
        raise ValueError("aucune réplication à résumer")
    return {field: estimate([getattr(r, field) for r in results]) for field in SUMMARY_FIELDS}


def summary_table(summary: dict[str, Estimate], scenario: str) -> pa.Table:
    """Une ligne par indicateur : moyenne, écart-type, effectif et bornes de l'IC 95 %."""
    noms = list(summary)
    return pa.table(
        {
            "scenario": [scenario] * len(noms),
            "indicator": noms,
            "mean": [summary[n].mean for n in noms],
            "std": [summary[n].std for n in noms],
            "n": [summary[n].n for n in noms],
            "ci95_low": [summary[n].low for n in noms],
            "ci95_high": [summary[n].high for n in noms],
        }
    )


def write_summary_parquet(
    summary: dict[str, Estimate],
    out_dir: str | Path = DEFAULT_OUT_DIR,
    scenario: str = "sim",
) -> Path:
    """Écrit la synthèse à côté des réplications, avec le même horodatage de principe."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{scenario}_{stamp}_synthese.parquet"
    pq.write_table(summary_table(summary, scenario), path)
    return path


def _fmt(e: Estimate, scale: float = 1.0, digits: int = 1) -> str:
    """« moyenne ± demi-largeur » à l'échelle voulue (Mt, kt, %)."""
    return f"{e.mean * scale:.{digits}f} ± {e.half_width * scale:.{digits}f}"


def _print_summary(
    scenario: str,
    config: SimConfig,
    results: Sequence[SimResult],
    summary: dict[str, Estimate],
) -> None:
    s = summary
    cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an
    pire_p90 = max(r.wait_p90_h for r in results)
    p10 = percentile([r.dri_production_t for r in results], 0.10)
    print()
    print(f"Scénario {scenario} — {len(results)} réplications, IC 95 % des moyennes")
    print(f"  navires arrivés / servis   {_fmt(s['vessels_arrived'])}"
          f" / {_fmt(s['vessels_served'])}")
    print(f"  pellets déchargés          {_fmt(s['pellets_delivered_t'], 1e-6, 3)} Mt")
    print(f"  production DRI             {_fmt(s['dri_production_t'], 1e-6, 3)} Mt"
          f"  (cible {cible / 1e6:.3f} Mt, p10 {p10 / 1e6:.3f} Mt)")
    print(f"  arrêts DRI faute de stock  {_fmt(s['dri_stop_hours'], 1, 0)} h"
          f"  (planifiés {s['dri_planned_stop_hours'].mean:.0f} h)")
    print(f"  attente en rade, moyenne   {_fmt(s['wait_mean_h'])} h")
    print(f"  attente en rade, p90       {_fmt(s['wait_p90_h'])} h  (pire réplication {pire_p90:.1f} h)")
    print(f"  occupation du poste        {_fmt(s['berth_occupancy'], 100)} %")
    print(f"  stockyard min / moyen      {_fmt(s['stockyard_min_t'], 1e-3, 0)}"
          f" / {_fmt(s['stockyard_mean_t'], 1e-3, 0)} kt")
    print(f"  stock usine min / moyen    {_fmt(s['plant_stock_min_t'], 1e-3, 0)}"
          f" / {_fmt(s['plant_stock_mean_t'], 1e-3, 0)} kt")
    print(f"  utilisation des rames      {_fmt(s['rame_utilisation'], 100)} %"
          f"  (bloquées {_fmt(s['rame_blocked_h'], 1, 0)} h)")
    print(f"  stock flottant en rade     {_fmt(s['waiting_tonnage_mean_t'], 1e-3, 0)} kt")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simule le corridor Djen Djen–Bellara.")
    parser.add_argument("--scenario", default="baseline", help="nom d'un fichier de config/scenarios")
    parser.add_argument("--years", type=float, default=1.0, help="durée simulée, en années")
    parser.add_argument("--reps", type=int, default=50, help="nombre de réplications")
    parser.add_argument("--seed", type=int, default=None, help="graine maîtresse")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="dossier de sortie")
    parser.add_argument(
        "--dispersion",
        type=float,
        default=None,
        help="force la dispersion des arrivées (0 = régulières, 1 = Poisson)",
    )
    parser.add_argument(
        "--politique", choices=EXPEDITION_POLICIES, default=None,
        help="force la politique d'expédition des rames",
    )
    args = parser.parse_args(argv)

    config = SimConfig.from_scenario(load_scenario(args.scenario))
    if args.dispersion is not None:
        config = dataclasses.replace(config, dispersion_arrivees=args.dispersion)
    if args.politique is not None:
        config = dataclasses.replace(config, politique_expedition=args.politique)
    seed = args.seed if args.seed is not None else config.graine_par_defaut

    results = replicate(config, args.years, args.reps, seed, args.scenario)
    summary = summarize(results)
    path = write_parquet(results, args.out, args.scenario)
    synthese = write_summary_parquet(summary, args.out, args.scenario)
    _print_summary(args.scenario, config, results, summary)
    print()
    print(f"{len(results)} réplications écrites dans {path}")
    print(f"synthèse (moyenne, écart-type, IC 95 %) écrite dans {synthese}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
