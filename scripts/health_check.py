"""Bilan de santé de la collecte : à lancer à la main, sans aucun outil externe.

Lit `data/raw/port_status/_manifest.csv` et `data/clean/observations.csv`, puis affiche :
le nombre de collectes et leur répartition par statut, le taux d'erreur, l'ancienneté de la
dernière collecte réussie, le nombre d'escales reconstruites, terminées et exploitables
(`usable_escales`), et le nombre d'escales portant chaque code d'anomalie.

Code de sortie : 0 si tout va bien, 1 si aucune collecte réussie depuis plus de 24 h (ou si
le manifeste est vide), pour qu'un cron ou un hook puisse s'en servir.

Usage : python scripts/health_check.py [--max-age-hours 24]
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from corridor.transform.build_escales import build_escales, usable_escales
from corridor.transform.observations_csv import read_observations

MANIFEST = Path("data/raw/port_status/_manifest.csv")
OBSERVATIONS = Path("data/clean/observations.csv")
FORMAT_HEURE = "%Y-%m-%dT%H:%M:%SZ"


def read_manifest(path=MANIFEST):
    """Lignes du manifeste, telles quelles ; liste vide si le fichier manque."""
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_time(text):
    return datetime.strptime(text, FORMAT_HEURE).replace(tzinfo=UTC)


def collection_summary(rows, now, max_age=timedelta(hours=24)):
    """Chiffres du manifeste : nombres par statut, taux d'erreur, ancienneté, alerte.

    L'ancienneté se mesure depuis la dernière collecte **réussie** (statut autre que
    `error`) : une tentative en échec ne prouve pas que la source ait été lue.
    """
    par_statut = Counter(r["status"] for r in rows)
    reussies = [parse_time(r["fetched_at_utc"]) for r in rows if r["status"] != "error"]
    derniere = max(reussies) if reussies else None
    age = now - derniere if derniere else None
    return {
        "total": len(rows),
        "par_statut": dict(par_statut),
        "taux_erreur": par_statut.get("error", 0) / len(rows) if rows else None,
        "premiere": min(reussies) if reussies else None,
        "derniere_reussie": derniere,
        "age": age,
        "en_retard": age is None or age > max_age,
    }


def escale_summary(observations):
    """Escales reconstruites, terminées, exploitables, et escales par code d'anomalie."""
    escales = build_escales(observations)
    codes = Counter(code for e in escales for code in e.anomaly.split(",") if code)
    return {
        "escales": len(escales),
        "terminees": sum(e.finished for e in escales),
        "exploitables": len(usable_escales(escales)),
        "anomalies": dict(codes),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    args = parser.parse_args(argv)
    now = datetime.now(UTC)

    rows = read_manifest()
    c = collection_summary(rows, now, timedelta(hours=args.max_age_hours))
    print("== Collecte ==")
    if not rows:
        print(f"ALERTE : manifeste absent ou vide ({MANIFEST})")
        return 1
    print(f"collectes         : {c['total']}  {c['par_statut']}")
    print(f"taux d'erreur     : {c['taux_erreur']:.1%}")
    print(f"première réussie  : {c['premiere']:%Y-%m-%d %H:%M} UTC")
    print(f"dernière réussie  : {c['derniere_reussie']:%Y-%m-%d %H:%M} UTC "
          f"(il y a {c['age'].total_seconds() / 3600:.1f} h)")
    if c["en_retard"]:
        print(f"ALERTE : aucune collecte réussie depuis plus de {args.max_age_hours:g} h")

    print("\n== Escales ==")
    if OBSERVATIONS.exists():
        observations = read_observations(OBSERVATIONS)
        e = escale_summary(observations)
        print(f"observations      : {len(observations)}")
        print(f"escales           : {e['escales']}")
        print(f"terminées         : {e['terminees']}  (départ encadré)")
        print(f"usable_escales    : {e['exploitables']}  (terminées, hors longs séjours)")
        for code, n in sorted(e["anomalies"].items(), key=lambda kv: -kv[1]):
            print(f"  anomalie {code:24} {n}")
    else:
        print(f"observations absentes ({OBSERVATIONS}) : "
              "python -m corridor.transform.observations_csv --rebuild")
    return 1 if c["en_retard"] else 0


if __name__ == "__main__":
    sys.exit(main())
