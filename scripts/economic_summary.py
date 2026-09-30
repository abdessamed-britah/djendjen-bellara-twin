"""Convertit la perte de production de DRI de chaque scénario en coût annuel estimé.

Pour chaque scénario et chacune de deux régularités d'arrivée (régulières, Poisson), rejoue
la simulation puis traduit la perte (cible − production, en t/an) en dollars.

**Conversion.** `couts.cout_journee_arret_dri` (USD/jour) est une valeur du site actuel : la
journée de production de la capacité *baseline*. On en tire une marge implicite par tonne,
`cout_journee / débit journalier de la baseline` (~22 USD/t au centre), puis
`coût = perte en tonnes × marge par tonne`. Pour la baseline, c'est exactement
`perte ÷ débit journalier × coût de la journée`, soit un nombre de jours d'arrêt équivalents
multiplié par le coût du jour. Diviser la perte par 365 au lieu du débit journalier mélangerait
des t/jour et des USD/jour ; et appliquer 130 000 USD/jour tel quel à la phase 2 sous-évaluerait
d'un facteur deux une journée qui produit deux fois plus.

**Fourchette.** L'enveloppe va de (borne basse de la perte × borne basse du coût) à
(borne haute de la perte × borne haute du coût). C'est volontairement large : elle cumule
l'IC 95 % de la simulation et la fourchette de l'hypothèse, sans supposer d'indépendance
particulière. Les montants sont arrondis au million de dollars : aller plus fin prétendrait à
une précision que ni le coût unitaire (hypothèse `estimé`, marges chinoises) ni les capacités de
stockage (`inconnu`) ne permettent.

Usage : python scripts/economic_summary.py [--reps 30] [--years 1] [--seed 20260924]
"""

import argparse
import dataclasses
import sys

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.run import replicate
from corridor.sim.stats import estimate

SCENARIOS = ("baseline", "phase2", "phase2_plus_stockage", "phase2_plus_poste")
ARRIVEES = ((0.0, "régulières"), (1.0, "Poisson"))
HEURES_PAR_JOUR = 24.0
MILLION = 1e6


def cout_journee(raw):
    """(bas, centre, haut) de `couts.cout_journee_arret_dri`, en USD/jour."""
    entree = raw["couts"]["cout_journee_arret_dri"]
    if entree.get("value") is None or "range" not in entree:
        raise ValueError("couts.cout_journee_arret_dri doit avoir une valeur et une fourchette")
    bas, haut = entree["range"]
    return float(bas), float(entree["value"]), float(haut)


def marge_par_tonne(cout_jour, debit_baseline_t_par_jour):
    """Marge implicite (USD/t) d'une journée de production de la baseline."""
    return cout_jour / debit_baseline_t_par_jour


def cout_annuel(perte_t, marge_usd_t):
    """Coût annuel (USD) d'une perte de production, à une marge par tonne donnée."""
    return perte_t * marge_usd_t


def enveloppe(perte_bas_t, perte_haut_t, marge_bas, marge_haut):
    """Bornes (USD) cumulant l'IC de la perte et la fourchette de la marge.

    Une perte négative (production au-dessus de la cible) est ramenée à zéro : ce n'est pas
    un gain économique, seulement du bruit autour d'une perte nulle.
    """
    return cout_annuel(max(perte_bas_t, 0.0), marge_bas), cout_annuel(
        max(perte_haut_t, 0.0), marge_haut
    )


def arrondi_millions(usd):
    """Arrondi au million de dollars, en texte ; « < 1 » sous le demi-million."""
    m = usd / MILLION
    return "< 1" if m < 0.5 else f"{m:,.0f}".replace(",", " ")


def perte_par_replication(scenario, dispersion, reps, years, seed):
    """Pertes annuelles (t/an) de chaque réplication, politique `pull`."""
    config = dataclasses.replace(
        SimConfig.from_scenario(load_scenario(scenario)),
        dispersion_arrivees=dispersion,
        politique_expedition="pull",
    )
    cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an * years
    resultats = replicate(config, years, reps, seed, scenario)
    return [(cible - r.dri_production_t) / years for r in resultats]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=30)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260924)
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    jour_bas, jour_centre, jour_haut = cout_journee(raw)
    baseline = SimConfig.from_scenario(load_scenario("baseline"))
    debit_jour = baseline.debit_dri_t_par_h * HEURES_PAR_JOUR
    marge_bas, marge_centre, marge_haut = (
        marge_par_tonne(c, debit_jour) for c in (jour_bas, jour_centre, jour_haut)
    )

    print("Coût annuel estimé de la perte de production DRI (politique pull)")
    print(f"Coût d'une journée d'arrêt : {jour_centre:,.0f} USD/jour "
          f"[{jour_bas:,.0f} ; {jour_haut:,.0f}], statut estimé (marges EAF chinoises).")
    print(f"Marge implicite : ~{marge_centre:.0f} USD/t [{marge_bas:.0f} ; {marge_haut:.0f}] "
          f"sur un débit de {debit_jour:,.0f} t/jour (baseline).")
    print(f"{args.reps} réplications par ligne ; montants arrondis au million d'USD.\n")
    print(f"{'scénario':22} {'arrivées':11} {'perte (Mt/an)':>18} "
          f"{'coût central':>14} {'fourchette (M USD/an)':>24}")

    for scenario in SCENARIOS:
        for dispersion, nom in ARRIVEES:
            pertes = perte_par_replication(scenario, dispersion, args.reps, args.years,
                                           args.seed)
            e = estimate(pertes)
            bas, haut = enveloppe(e.low, e.high, marge_bas, marge_haut)
            centre = cout_annuel(max(e.mean, 0.0), marge_centre)
            print(f"{scenario:22} {nom:11} {e.mean / MILLION:>10.2f} ± {e.half_width / MILLION:<5.2f} "
                  f"{arrondi_millions(centre):>14} "
                  f"{arrondi_millions(bas) + ' à ' + arrondi_millions(haut):>24}")

    print("\nÀ lire avec : marge non spécifique à l'Algérie, dispersion des arrivées non mesurée,")
    print("capacités de stockage inconnues. Ce sont des ordres de grandeur, pas des prévisions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
