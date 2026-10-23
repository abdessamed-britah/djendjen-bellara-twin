"""Plan factoriel 2 × 2 : second poste de déchargement × stockage haut, en arrivées de Poisson.

**La question.** Un second poste de déchargement n'améliore pas la production de façon
significative (`phase2_plus_poste` : +0,129 ± 0,157 Mt à 30 réplications). Pourquoi ?

**La réponse que propose H2.** Le quai n'est pas saturé par sa propre cadence mais par
l'aval : le navire y reste immobilisé quand le stockyard est plein (`berth_blocked_h`).
Si c'est le mécanisme, doubler le poste sans toucher au stockage ne fait que doubler le
nombre de quais bloqués. **Prédiction : le second poste n'apporte un gain significatif que
si le stockage est augmenté en même temps.**

**Le plan.** Quatre configurations, qui forment un 2 × 2 complet :

                        stockage de base        stockage haut
    1 poste             phase2                  phase2_plus_stockage
    2 postes            phase2_plus_poste       phase2_plus_poste_plus_stockage

Toutes rejouées avec la **même graine maîtresse**, donc la réplication i des quatre
configurations voit la même séquence aléatoire. Trois quantités se lisent alors sur les
différences appariées, et c'est leur intervalle de confiance qui tranche :

- *effet simple du poste à stockage de base*  : (2 postes, bas) − (1 poste, bas) ;
- *effet simple du poste à stockage haut*     : (2 postes, haut) − (1 poste, haut) ;
- *interaction*                               : la différence de ces deux effets, soit
  (haut, 2) − (haut, 1) − (bas, 2) + (bas, 1). Elle vaut exactement l'écart entre le gain
  combiné et la somme des deux gains séparés. Positive, elle signifie que les deux leviers
  se renforcent : chacun débloque ce que l'autre limitait.

H2 prédit : effet simple à stockage de base **non** significatif, effet simple à stockage
haut significatif, interaction significative et positive.

**Ce que ce test ne dit pas.** Les deux capacités de stockage sont de statut `inconnu` ;
« stockage haut » est le haut de leurs fourchettes, pas une situation observée, et
`postes_minerai = 1` repose sur une seule observation. Le résultat porte sur le modèle,
sous ces hypothèses.

Usage : python scripts/poste_stockage_factoriel.py [--reps 200] [--years 1] [--dispersion 1.0]
"""

import argparse
import dataclasses
import random
import sys

from corridor.sim.analytic import effective_service, service_moments, utilisation
from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.run import replicate
from corridor.sim.samplers import Samplers
from corridor.sim.stats import estimate, paired_difference, significant

#: Les quatre cases du plan, indexées par (deux_postes, stockage_haut).
CELLULES = {
    (False, False): ("phase2", "1 poste, stockage de base"),
    (True, False): ("phase2_plus_poste", "2 postes, stockage de base"),
    (False, True): ("phase2_plus_stockage", "1 poste, stockage haut"),
    (True, True): ("phase2_plus_poste_plus_stockage", "2 postes, stockage haut"),
}

MOMENT_DRAWS = 50_000
MOMENT_SEED = 0


def cible_mt(config: SimConfig, years: float) -> float:
    """Cible de production sur l'horizon : capacité × utilisation × jours ouvrés / 365."""
    return config.debit_dri_t_par_h * config.heures_ouvrees_par_an * years / 1e6


def perte_pct(config: SimConfig, resultat, years: float) -> float:
    """Perte de production en % de la cible du scénario, pour une réplication."""
    cible = cible_mt(config, years)
    return (cible - resultat.dri_production_t / 1e6) / cible * 100.0


def _rng():
    return random.Random(MOMENT_SEED)


def jouer(scenario: str, reps: int, years: float, seed: int, dispersion: float):
    """Rejoue une case du plan et renvoie sa configuration et ses réplications."""
    config = dataclasses.replace(
        SimConfig.from_scenario(load_scenario(scenario)),
        dispersion_arrivees=dispersion,
        politique_expedition="pull",
    )
    return config, replicate(config, years, reps, seed, scenario)


def series(config: SimConfig, resultats, years: float, draws: int = MOMENT_DRAWS
           ) -> dict[str, list[float]]:
    """Les indicateurs du test, réplication par réplication.

    `berth_blocked_h` est en **poste-heures**, sommées sur les postes : avec deux postes,
    deux navires peuvent être bloqués en même temps. La version « h/poste » la divise par le
    nombre de postes, et c'est la seule des deux qui se compare entre 1 et 2 postes.

    L'**occupation effective** est ρ_eff = λ·(E[S] + B/N)/c : la charge que la file voit
    réellement, blocage compris, ramenée au poste. C'est elle, et non `berth_occupancy`,
    qui se compare d'une configuration à l'autre quand le nombre de postes change — à
    demande égale, deux postes sont deux fois moins chargés qu'un seul.
    """
    moments = service_moments(Samplers.from_config(config), draws, _rng())
    lam = 1.0 / config.intervalle_moyen_arrivees_h
    occupations = []
    for r in resultats:
        es_eff = (
            effective_service(moments.es, r.berth_blocked_h, r.vessels_served)
            if r.vessels_served > 0
            else moments.es
        )
        occupations.append(utilisation(lam, es_eff, config.postes_minerai))
    return {
        "perte DRI (%)": [perte_pct(config, r, years) for r in resultats],
        "production DRI (Mt)": [r.dri_production_t / 1e6 for r in resultats],
        "quai bloqué (poste-h)": [r.berth_blocked_h for r in resultats],
        "quai bloqué (h/poste)": [
            r.berth_blocked_h / config.postes_minerai for r in resultats
        ],
        "occupation effective": occupations,
        "attente en rade (h)": [r.wait_mean_h for r in resultats],
    }


#: (libellé, décimales) de chaque indicateur affiché, dans l'ordre du tableau.
INDICATEURS = (
    ("perte DRI (%)", 2),
    ("production DRI (Mt)", 3),
    ("quai bloqué (poste-h)", 0),
    ("quai bloqué (h/poste)", 0),
    ("occupation effective", 3),
    ("attente en rade (h)", 1),
)

#: Indicateurs du tableau des effets : tous des **maux**, pour que la convention de signe
#: « une baisse est un gain » tienne sur toute la colonne. La production en est donc exclue.
EFFETS = tuple(x for x in INDICATEURS if x[0] != "production DRI (Mt)")


def print_cases(cases) -> None:
    """Les quatre cases du plan, chacune avec ses IC 95 %."""
    print(f"\n{'=' * 104}\nLes quatre cases du plan\n{'=' * 104}")
    print(f"{'configuration':30}", end="")
    for libelle, _ in INDICATEURS:
        print(f"{libelle:>21}", end="")
    print()
    for cle in ((False, False), (True, False), (False, True), (True, True)):
        _, titre = CELLULES[cle]
        print(f"{titre:30}", end="")
        for libelle, digits in INDICATEURS:
            e = estimate(cases[cle]["series"][libelle])
            print(f"{e.mean:>12.{digits}f} ±{e.half_width:<8.{digits}f}", end="")
        print()


def print_effets(cases) -> None:
    """Effets simples et interaction, en différences appariées.

    La convention de signe est « gain » : pour la perte de DRI et les grandeurs de
    congestion, une **baisse** est un progrès, donc l'effet affiché est (sans levier) −
    (avec levier). Un nombre positif se lit toujours « le levier améliore ». La production
    est absente de ce tableau pour cette raison même : c'est un bien, pas un mal, et la
    convention s'y inverserait. Elle porte de toute façon la même information que la perte.
    """
    print(f"\n{'=' * 104}\nEffets, en différences appariées (positif = le levier améliore)\n"
          f"{'=' * 104}")
    for libelle, digits in EFFETS:
        print(f"\n### {libelle}")
        bas = _gain(cases, (False, False), (True, False), libelle)
        haut = _gain(cases, (False, True), (True, True), libelle)
        inter = _interaction(cases, libelle)
        for nom, d in (
            ("second poste, à stockage de base", bas),
            ("second poste, à stockage haut   ", haut),
            ("interaction (haut − bas)        ", inter),
        ):
            verdict = "SIGNIFICATIF" if significant(d) else "non significatif"
            print(f"  {nom}  {d.mean:>+11.{digits}f} ± {d.half_width:<10.{digits}f} {verdict}")


def _serie(cases, cle, libelle):
    return cases[cle]["series"][libelle]


def _gain(cases, sans, avec, libelle):
    """Gain apporté par le levier : (sans) − (avec), apparié réplication par réplication.

    Convention « gain » : les indicateurs du tableau des effets sont tous des maux
    (perte, blocage, attente, charge), donc une baisse est un progrès et le gain est
    positif quand le levier améliore. La production en est exclue pour cette raison
    (voir `EFFETS`).
    """
    return paired_difference(_serie(cases, sans, libelle), _serie(cases, avec, libelle))


def _variation(cases, sans, avec, libelle):
    """Variation brute du levier : (avec) − (sans). Négatif = la grandeur baisse."""
    return paired_difference(_serie(cases, avec, libelle), _serie(cases, sans, libelle))


def _interaction(cases, libelle):
    """Interaction : gain du poste à stockage haut moins gain du poste à stockage de base.

    Calculée réplication par réplication pour rester appariée, donc sur la série
    (haut,1) − (haut,2) − (bas,1) + (bas,2).
    """
    h1 = _serie(cases, (False, True), libelle)
    h2 = _serie(cases, (True, True), libelle)
    b1 = _serie(cases, (False, False), libelle)
    b2 = _serie(cases, (True, False), libelle)
    return estimate([
        (a - b) - (c - d) for a, b, c, d in zip(h1, h2, b1, b2, strict=True)
    ])


def print_verdict(cases) -> None:
    """Conclusion sur la prédiction de H2, volet par volet, sur la perte de DRI."""
    libelle = "perte DRI (%)"
    bas = _gain(cases, (False, False), (True, False), libelle)
    haut = _gain(cases, (False, True), (True, True), libelle)
    inter = _interaction(cases, libelle)
    combine = _gain(cases, (False, False), (True, True), libelle)
    stockage_seul = _gain(cases, (False, False), (False, True), libelle)
    somme = bas.mean + stockage_seul.mean

    print(f"\n{'=' * 104}\nPrédiction de H2 : le second poste ne paie que si le stockage "
          f"suit\n{'=' * 104}")
    print("Sur la perte de production de DRI, en points de pourcentage récupérés :\n")
    print(f"  second poste seul (stockage de base) : {bas.mean:+.2f} ± {bas.half_width:.2f}  "
          f"{'significatif' if significant(bas) else 'NON significatif'}")
    print(f"  stockage seul (1 poste)              : {stockage_seul.mean:+.2f} ± "
          f"{stockage_seul.half_width:.2f}  "
          f"{'significatif' if significant(stockage_seul) else 'NON significatif'}")
    print(f"  second poste, stockage déjà haut     : {haut.mean:+.2f} ± {haut.half_width:.2f}  "
          f"{'significatif' if significant(haut) else 'NON significatif'}")
    print(f"  les deux leviers ensemble            : {combine.mean:+.2f} ± "
          f"{combine.half_width:.2f}  "
          f"{'significatif' if significant(combine) else 'NON significatif'}")
    print(f"\n  somme des deux gains séparés         : {somme:+.2f}")
    print(f"  interaction (combiné − somme)        : {inter.mean:+.2f} ± "
          f"{inter.half_width:.2f}  "
          f"{'SIGNIFICATIVE' if significant(inter) else 'NON significative'}")

    volet1 = not significant(bas)
    volet2 = significant(haut)
    volet3 = significant(inter) and inter.mean > 0
    print("\nVolets de la prédiction :")
    print(f"  1. pas de gain significatif à stockage de base : {'OUI' if volet1 else 'NON'}")
    print(f"  2. gain significatif quand le stockage suit    : {'OUI' if volet2 else 'NON'}")
    print(f"  3. interaction positive et significative       : {'OUI' if volet3 else 'NON'}")
    if volet1 and volet2 and volet3:
        print("\n  → prédiction SOUTENUE sur les trois volets.")
    else:
        print("\n  → prédiction NON soutenue : il y faut les trois volets, et au moins un "
              "manque.")
        print("    Le second poste ne récupère de production dans AUCUNE des deux "
              "configurations de stockage.")

    # le poste change bel et bien quelque chose, mais pas la production : où va la file ?
    print("\nOù passe la file quand on ajoute un poste ?")
    print("  Variations appariées brutes (2 postes − 1 poste) : négatif = la grandeur baisse.")
    for nom, cles in (
        ("à stockage de base", ((False, False), (True, False))),
        ("à stockage haut   ", ((False, True), (True, True))),
    ):
        sans, avec = cles
        da = _variation(cases, sans, avec, "attente en rade (h)")
        db = _variation(cases, sans, avec, "quai bloqué (h/poste)")
        print(f"  {nom} : attente en rade {da.mean:>+8.1f} ± {da.half_width:<6.1f} h "
              f"({'sig.' if significant(da) else 'n.s.'})   "
              f"blocage par poste {db.mean:>+7.0f} ± {db.half_width:<5.0f} h "
              f"({'sig.' if significant(db) else 'n.s.'})")
    print("  Une attente en rade qui baisse pendant que le blocage par poste monte signifie "
          "que la\n  file n'a pas disparu : elle a changé d'endroit, de la rade vers le quai.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=200)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--dispersion", type=float, default=1.0)
    args = parser.parse_args(argv)

    print(f"{args.reps} réplications de {args.years:g} an, dispersion {args.dispersion:g} "
          f"(1 = Poisson), politique pull, graine maîtresse {args.seed}")
    cases = {}
    for cle, (scenario, titre) in CELLULES.items():
        config, resultats = jouer(scenario, args.reps, args.years, args.seed, args.dispersion)
        cases[cle] = {
            "config": config,
            "series": series(config, resultats, args.years),
            "scenario": scenario,
        }
        print(f"  {titre:30} {scenario:34} postes={config.postes_minerai} "
              f"stockage={(config.capacite_stockyard_t + config.capacite_stock_usine_t) / 1e3:.0f} kt "
              f"cible={cible_mt(config, args.years):.3f} Mt")

    print_cases(cases)
    print_effets(cases)
    print_verdict(cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
