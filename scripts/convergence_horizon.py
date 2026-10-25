"""Le taux de service converge-t-il avec l'horizon ? Diagnostic de la marche sans dérive.

**L'hypothèse à éprouver.** Dans le modèle actuel, les arrivées sont exogènes et leur débit
moyen est calé exactement sur la demande (`navires_par_an = besoin_annuel / taille_navire`).
L'offre cumulée moins la demande cumulée est donc une marche aléatoire **sans dérive**. Le
stock est borné en bas par zéro — le four s'arrête — et pas en haut, puisqu'un navire qui ne
peut pas décharger garde sa cargaison (limite 0 de CLAUDE.md). La production perdue est alors
le terme régulateur d'une marche réfléchie en zéro, qui croît comme √T. Conséquence : le taux
de perte décroît en 1/√T, β tend vers 1, et **il n'existe aucun taux de service stationnaire**
inférieur à 1. Tout β publié serait la valeur d'un transitoire, lue à l'horizon choisi.

**Comment le script peut échouer.** Il ajuste le déficit cumulé en loi de puissance,
`déficit ≈ a·T^p`, et regarde l'intervalle de confiance de `p` :

- `p` ≈ 1/2 : marche sans dérive. Le diagnostic est confirmé ;
- `p` ≈ 1 : déficit structurel — le corridor manque sa cible d'une fraction fixe à tout
  horizon. Le diagnostic serait **faux**, et le problème serait un défaut de capacité ;
- `p` ≈ 0 : déficit borné — une dérive positive le rattrape. β converge vers 1 en 1/T.

Les trois issues sont écrites avant le calcul pour que le verdict ne puisse pas être choisi
après coup.

**Le stock initial.** Il fausse les horizons courts dans un sens connu : c'est un cadeau de
`S0` tonnes, qui relève β d'au plus `S0 / cible(T)`. Le script le mesure directement, par
différence appariée avec un stock initial nul, et le compare à cette borne.

**Appariement.** Chaque réplication est jouée à tous les horizons avec la même graine. Le
modèle étant causal et ses flux aléatoires séparés, la simulation de T ans est exactement le
préfixe de celle de 50 ans : les points d'une même réplication décrivent une **seule**
trajectoire lue à plusieurs instants, et le déficit en fonction de T est une vraie courbe.

Usage : python scripts/convergence_horizon.py [--reps 20] [--scenario baseline]
"""

import argparse
import dataclasses
import math
import random
import sys
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.model import simulate
from corridor.sim.stats import estimate

HORIZONS = (1, 2, 4, 8, 16, 32, 50)
DEFAULT_SCENARIO = "baseline"
DEFAULT_REPS = 20
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/convergence_horizon.png")
#: Régime d'arrivée du diagnostic : Poisson, là où la perte est franche.
DISPERSION = 1.0
TIRAGES_BOOTSTRAP = 2000

#: Les trois issues, écrites avant le calcul. Une borne d'IC de `p` qui tombe dans une
#: zone l'en exclut ; l'issue retenue est celle dont la valeur est dans l'IC et les deux
#: autres dehors. Sinon : indécidable, et le script le dit.
ISSUES = {
    "marche sans dérive": 0.5,
    "déficit structurel": 1.0,
    "déficit borné": 0.0,
}


def configuration(scenario: str, initial: bool = True) -> SimConfig:
    """Le scénario à arrivées de Poisson, avec ou sans stock initial."""
    config = dataclasses.replace(
        SimConfig.from_scenario(load_scenario(scenario)), dispersion_arrivees=DISPERSION
    )
    if not initial:
        config = dataclasses.replace(
            config, stock_initial_stockyard_t=0.0, stock_initial_usine_t=0.0
        )
    return config


def cible_t(config: SimConfig, annees: float) -> float:
    """Production visée sur l'horizon : débit × heures ouvrées, comme dans tout le projet."""
    return config.debit_dri_t_par_h * config.heures_ouvrees_par_an * annees


def derive_relative(config: SimConfig) -> float:
    """(offre moyenne − demande moyenne) / demande, en pellets. Nulle par construction.

    C'est le chiffre qui fait de l'écart cumulé une marche sans dérive : calculé ici plutôt
    qu'affirmé, pour qu'une révision de la configuration qui le rendrait non nul se voie.
    """
    offre = config.navires_par_an * config.taille_navire_moyenne_t
    demande = config.debit_pellets_t_par_h * config.heures_ouvrees_par_an
    return (offre - demande) / demande


def graines(reps: int, seed: int) -> list[int]:
    """Une graine par réplication, partagée par tous les horizons de cette réplication."""
    maitre = random.Random(seed)
    return [maitre.randrange(2**31) for _ in range(reps)]


def deficits_par_replication(config, horizons, seeds) -> dict[int, list[float]]:
    """{horizon: [déficit cumulé en tonnes de DRI, une valeur par réplication]}."""
    return {
        h: [cible_t(config, h) - simulate(config, seed=g, years=float(h)).dri_production_t
            for g in seeds]
        for h in horizons
    }


def resume(config, deficits: dict[int, list[float]]) -> list[dict]:
    """β et déficit par horizon, en `Estimate`. Le déficit est aussi donné en années de cible.

    « Déficit normalisé » = déficit ÷ cible d'**un** an : il se lit en années de production
    perdues, et c'est lui dont on regarde la croissance avec T.
    """
    annuelle = cible_t(config, 1.0)
    lignes = []
    for h, valeurs in sorted(deficits.items()):
        cible = cible_t(config, h)
        lignes.append({
            "annees": h,
            "beta": estimate([1.0 - d / cible for d in valeurs]),
            "deficit_t": estimate(valeurs),
            "deficit_normalise": estimate([d / annuelle for d in valeurs]),
        })
    return lignes


def ajuste_loi_puissance(horizons, moyennes) -> dict:
    """Moindres carrés en log-log : log D = log a + p·log T. Horizons à déficit ≤ 0 écartés.

    Écartés et non tronqués : un déficit moyen négatif (production au-dessus de la cible,
    possible aux horizons courts grâce au stock initial) n'a pas de logarithme, et le
    remplacer par une petite valeur positive fabriquerait une pente.
    """
    points = [(math.log(h), math.log(m)) for h, m in zip(horizons, moyennes, strict=True)
              if m > 0]
    if len(points) < 2:
        return {"p": math.nan, "a": math.nan, "r2": math.nan, "points": len(points)}
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    sxy = sum((x - mx) * (y - my) for x, y in points)
    syy = sum((y - my) ** 2 for _, y in points)
    p = sxy / sxx if sxx else math.nan
    a = math.exp(my - p * mx)
    r2 = (sxy * sxy) / (sxx * syy) if sxx and syy else math.nan
    return {"p": p, "a": a, "r2": r2, "points": n}


def exposant(deficits: dict[int, list[float]], tirages=TIRAGES_BOOTSTRAP, seed=0) -> dict:
    """L'exposant `p` et son IC à 95 % par bootstrap **sur les réplications**.

    On rééchantillonne des réplications entières, pas des points : une réplication est une
    trajectoire, et ses déficits aux différents horizons sont corrélés. Rééchantillonner les
    points séparément casserait cette corrélation et donnerait un IC trop étroit.
    """
    horizons = sorted(deficits)
    n = len(deficits[horizons[0]])
    moyennes = [sum(deficits[h]) / n for h in horizons]
    central = ajuste_loi_puissance(horizons, moyennes)
    rng = random.Random(seed)
    pentes = []
    for _ in range(tirages):
        tirage = [rng.randrange(n) for _ in range(n)]
        moy = [sum(deficits[h][i] for i in tirage) / n for h in horizons]
        p = ajuste_loi_puissance(horizons, moy)["p"]
        if math.isfinite(p):
            pentes.append(p)
    pentes.sort()
    if not pentes:
        return {**central, "p_bas": math.nan, "p_haut": math.nan}
    bas = pentes[int(0.025 * (len(pentes) - 1))]
    haut = pentes[int(0.975 * (len(pentes) - 1))]
    return {**central, "p_bas": bas, "p_haut": haut}


def part_du_stock_initial(avec, sans, config_avec) -> list[dict]:
    """Δβ apparié entre stock initial nominal et stock initial nul, face à sa borne S0/cible.

    La borne suppose que chaque tonne du stock de départ finit consommée : c'est le plus que
    le cadeau puisse rapporter. Un Δβ mesuré près de la borne dit que le stock initial est
    entièrement converti en production ; loin en dessous, qu'une partie dort jusqu'à la fin.
    """
    s0 = config_avec.stock_initial_stockyard_t + config_avec.stock_initial_usine_t
    lignes = []
    for h in sorted(avec):
        cible = cible_t(config_avec, h)
        # déficit_sans − déficit_avec = production_avec − production_sans
        ecarts = [(ds - da) / cible for da, ds in zip(avec[h], sans[h], strict=True)]
        lignes.append({"annees": h, "delta_beta": estimate(ecarts), "borne": s0 / cible})
    return lignes


def verdict(ajustement: dict, lignes: list[dict]) -> dict:
    """L'issue retenue parmi les trois, ou « indécidable » — jamais choisie après coup."""
    bas, haut = ajustement["p_bas"], ajustement["p_haut"]
    dedans = [nom for nom, v in ISSUES.items() if bas <= v <= haut]
    issue = dedans[0] if len(dedans) == 1 else "indécidable"
    betas = [ligne["beta"].mean for ligne in lignes]
    croissant = all(b2 >= b1 for b1, b2 in pairwise(betas))
    return {"issue": issue, "issues_compatibles": dedans, "beta_croissant": croissant}


def donnees_figure(lignes) -> dict:
    """Les séries des deux panneaux, sans tracer. La référence √T passe par le premier point."""
    horizons = [ligne["annees"] for ligne in lignes]
    deficits = [ligne["deficit_normalise"].mean for ligne in lignes]
    premier = next((d for d in deficits if d > 0), None)
    reference = [premier * math.sqrt(h / horizons[0]) for h in horizons] if premier else []
    return {
        "horizons": horizons,
        "beta": [ligne["beta"].mean for ligne in lignes],
        "beta_bas": [ligne["beta"].low for ligne in lignes],
        "beta_haut": [ligne["beta"].high for ligne in lignes],
        "deficit": deficits,
        "deficit_bas": [ligne["deficit_normalise"].low for ligne in lignes],
        "deficit_haut": [ligne["deficit_normalise"].high for ligne in lignes],
        "reference_racine": reference,
    }


def write_figure(lignes, ajustement, scenario, reps, path=DEFAULT_FIGURE) -> Path:
    """β(T) avec sa bande, et le déficit en log-log contre la référence √T."""
    donnees = donnees_figure(lignes)
    figure, (gauche, droite) = plt.subplots(1, 2, figsize=(13.0, 5.6), constrained_layout=True)
    h = donnees["horizons"]

    gauche.fill_between(h, donnees["beta_bas"], donnees["beta_haut"], color="#1f77b4",
                        alpha=0.18, linewidth=0)
    gauche.plot(h, donnees["beta"], color="#1f77b4", marker="o", linewidth=2.0)
    gauche.axhline(1.0, color="0.45", linestyle="--", linewidth=1.0)
    gauche.set_xscale("log")
    gauche.set_xlabel("horizon de simulation T (années, échelle log)")
    gauche.set_ylabel("β = production ÷ cible")
    gauche.set_title("Le taux de service dépend de l'horizon", fontweight="bold")

    positifs = [(x, d, b, t) for x, d, b, t in zip(h, donnees["deficit"], donnees["deficit_bas"],
                                                   donnees["deficit_haut"], strict=True) if d > 0]
    if positifs:
        xs = [p[0] for p in positifs]
        droite.fill_between(xs, [max(p[2], 1e-6) for p in positifs], [p[3] for p in positifs],
                            color="#e8710a", alpha=0.18, linewidth=0)
        droite.plot(xs, [p[1] for p in positifs], color="#e8710a", marker="o", linewidth=2.0,
                    label="déficit cumulé simulé")
    if donnees["reference_racine"]:
        droite.plot(h, donnees["reference_racine"], color="0.35", linestyle="--",
                    linewidth=1.2, label="référence ∝ √T (marche sans dérive)")
        droite.plot(h, [donnees["reference_racine"][0] * x / h[0] for x in h], color="0.65",
                    linestyle=":", linewidth=1.2, label="référence ∝ T (déficit structurel)")
    droite.set_xscale("log")
    droite.set_yscale("log")
    droite.set_xlabel("horizon de simulation T (années, échelle log)")
    droite.set_ylabel("déficit cumulé, en années de cible")
    droite.set_title(f"Exposant ajusté p = {ajustement['p']:.2f} "
                     f"[{ajustement['p_bas']:.2f} ; {ajustement['p_haut']:.2f}]".replace(".", ","),
                     fontweight="bold")
    droite.legend(fontsize=9, loc="upper left")

    for ax in (gauche, droite):
        ax.grid(True, which="both", color="0.90", linewidth=0.7)
        ax.set_axisbelow(True)
    figure.suptitle(
        f"Convergence avec l'horizon — scénario {scenario}, arrivées de Poisson, "
        f"{reps} réplications appariées\n"
        "Chaque réplication est une seule trajectoire lue à plusieurs horizons. Bandes : IC à "
        "95 %.", fontsize=10.5)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def print_rapport(config, lignes, ajustement, parts, issue, ajustement_sans=None) -> None:
    print(f"  dérive relative de l'offre : {derive_relative(config):+.2e} "
          "(nulle par construction : offre calée sur la demande)")
    print(f"\n{'=' * 90}\nβ et déficit cumulé selon l'horizon\n{'=' * 90}")
    print(f"{'T (ans)':>8} {'β':>18} {'déficit (années de cible)':>30} "
          f"{'déficit / √T':>16}")
    for ligne in lignes:
        b, d = ligne["beta"], ligne["deficit_normalise"]
        print(f"{ligne['annees']:>8} {b.mean:>10.4f} ± {b.half_width:<6.4f} "
              f"{d.mean:>20.4f} ± {d.half_width:<7.4f} "
              f"{d.mean / math.sqrt(ligne['annees']):>16.4f}")

    print(f"\n{'=' * 90}\nLoi de puissance du déficit : D ≈ a·T^p\n{'=' * 90}")
    print(f"  p = {ajustement['p']:.3f}, IC 95 % bootstrap [{ajustement['p_bas']:.3f} ; "
          f"{ajustement['p_haut']:.3f}], R² = {ajustement['r2']:.3f}, "
          f"{ajustement['points']} horizons")
    for nom, valeur in ISSUES.items():
        dans = ajustement["p_bas"] <= valeur <= ajustement["p_haut"]
        print(f"  {nom:22s} (p = {valeur:.1f}) : {'compatible' if dans else 'exclu'}")
    if ajustement_sans is not None:
        print(f"\n  À stock initial nul : p = {ajustement_sans['p']:.3f}, IC 95 % "
              f"[{ajustement_sans['p_bas']:.3f} ; {ajustement_sans['p_haut']:.3f}]")
        print("  Le stock initial réduit les déficits des horizons courts et raidit donc la")
        print("  pente apparente : c'est cet exposant-ci qui décrit la marche elle-même.")

    print(f"\n{'=' * 90}\nPart du stock initial dans β\n{'=' * 90}")
    print(f"{'T (ans)':>8} {'Δβ mesuré':>20} {'borne S0/cible':>16} {'part convertie':>16}")
    for ligne in parts:
        d = ligne["delta_beta"]
        convertie = d.mean / ligne["borne"] if ligne["borne"] else math.nan
        print(f"{ligne['annees']:>8} {d.mean:>12.4f} ± {d.half_width:<6.4f} "
              f"{ligne['borne']:>16.4f} {convertie:>16.0%}")

    print(f"\n{'=' * 90}\nVerdict\n{'=' * 90}")
    print(f"  issue : **{issue['issue']}**  (compatibles : "
          f"{', '.join(issue['issues_compatibles']) or 'aucune'})")
    print(f"  β croissant avec l'horizon : {'oui' if issue['beta_croissant'] else 'non'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    args = parser.parse_args(argv)

    avec_config = configuration(args.scenario, initial=True)
    sans_config = configuration(args.scenario, initial=False)
    seeds = graines(args.reps, args.seed)
    print(f"scénario {args.scenario}, arrivées de Poisson, {args.reps} réplications, "
          f"horizons {args.horizons} ans")

    avec = deficits_par_replication(avec_config, args.horizons, seeds)
    sans = deficits_par_replication(sans_config, args.horizons, seeds)
    lignes = resume(avec_config, avec)
    ajustement = exposant(avec)
    parts = part_du_stock_initial(avec, sans, avec_config)
    issue = verdict(ajustement, lignes)
    print_rapport(avec_config, lignes, ajustement, parts, issue, ajustement_sans=exposant(sans))

    chemin = write_figure(lignes, ajustement, args.scenario, args.reps, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
