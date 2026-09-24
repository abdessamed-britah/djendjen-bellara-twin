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

from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.model import SimResult, percentile, simulate

DEFAULT_OUT_DIR = Path("data/sim")

#: Indicateurs repris dans la synthèse affichée en fin d'exécution.
_SUMMARY_FIELDS = (
    "pellets_delivered_t",
    "pellets_railed_t",
    "dri_production_t",
    "dri_stop_hours",
    "dri_planned_stop_hours",
    "vessels_arrived",
    "vessels_served",
    "wait_mean_h",
    "berth_occupancy",
    "stockyard_min_t",
    "stockyard_mean_t",
    "plant_stock_min_t",
    "plant_stock_mean_t",
    "rame_utilisation",
    "rame_blocked_h",
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


def summarize(results: Sequence[SimResult]) -> dict[str, float]:
    """Moyenne les indicateurs sur les réplications.

    Deux lectures du p90 d'attente, à ne pas confondre : `wait_p90_h` en moyenne sur les
    réplications (l'année typique) et `wait_p90_max_h` sur la pire d'entre elles (la
    mauvaise année, celle qui coûte des surestaries). Une moyenne d'attentes peut
    dépasser un p90 quand un seul navire sur quarante attend : le p90 d'une réplication
    reste alors à zéro.
    """
    if not results:
        raise ValueError("aucune réplication à résumer")
    synthese: dict[str, float] = {"reps": float(len(results))}
    for field in _SUMMARY_FIELDS:
        synthese[field] = sum(getattr(r, field) for r in results) / len(results)
    synthese["wait_p90_h"] = sum(r.wait_p90_h for r in results) / len(results)
    synthese["wait_p90_max_h"] = max(r.wait_p90_h for r in results)
    synthese["dri_production_p10_t"] = percentile([r.dri_production_t for r in results], 0.10)
    return synthese


def _print_summary(scenario: str, config: SimConfig, synthese: dict[str, float]) -> None:
    cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an
    print(f"\nScénario {scenario} — {int(synthese['reps'])} réplications")
    print(f"  navires arrivés / servis   {synthese['vessels_arrived']:.1f}"
          f" / {synthese['vessels_served']:.1f}")
    print(f"  pellets déchargés          {synthese['pellets_delivered_t'] / 1e6:.3f} Mt")
    print(f"  production DRI             {synthese['dri_production_t'] / 1e6:.3f} Mt"
          f"  (cible {cible / 1e6:.3f} Mt)")
    print(f"  arrêts DRI faute de stock  {synthese['dri_stop_hours']:.0f} h"
          f"  (planifiés {synthese['dri_planned_stop_hours']:.0f} h)")
    print(f"  attente en rade            moyenne {synthese['wait_mean_h']:.1f} h,"
          f" p90 {synthese['wait_p90_h']:.1f} h,"
          f" pire p90 {synthese['wait_p90_max_h']:.1f} h")
    print(f"  production DRI, p10        {synthese['dri_production_p10_t'] / 1e6:.3f} Mt")
    print(f"  occupation du poste        {synthese['berth_occupancy'] * 100:.1f} %")
    print(f"  stockyard min / moyen      {synthese['stockyard_min_t'] / 1e3:.0f}"
          f" / {synthese['stockyard_mean_t'] / 1e3:.0f} kt")
    print(f"  stock usine min / moyen    {synthese['plant_stock_min_t'] / 1e3:.0f}"
          f" / {synthese['plant_stock_mean_t'] / 1e3:.0f} kt")
    print(f"  utilisation des rames      {synthese['rame_utilisation'] * 100:.1f} %"
          f"  (bloquées {synthese['rame_blocked_h']:.0f} h)")


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
    args = parser.parse_args(argv)

    config = SimConfig.from_scenario(load_scenario(args.scenario))
    if args.dispersion is not None:
        config = dataclasses.replace(config, dispersion_arrivees=args.dispersion)
    seed = args.seed if args.seed is not None else config.graine_par_defaut

    results = replicate(config, args.years, args.reps, seed, args.scenario)
    path = write_parquet(results, args.out, args.scenario)
    _print_summary(args.scenario, config, summarize(results))
    print(f"\n{len(results)} réplications écrites dans {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
