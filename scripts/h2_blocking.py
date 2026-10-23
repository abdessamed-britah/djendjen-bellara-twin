"""Hypothèse H2 : le blocage du poste explique-t-il l'écart à la théorie des files ?

**L'hypothèse.** L'approximation de Kingman prédit l'attente d'une file G/G/1 à partir de
l'utilisation du serveur. Appliquée au poste de déchargement avec ρ = λ·E[S], où E[S] est
la durée de déchargement d'un navire, elle devrait sous-estimer l'attente observée dès que
le stockyard sature : le navire tient alors le quai sans décharger, donc le serveur est
plus occupé que ρ ne le dit. H2 affirme deux choses :

1. l'écart entre attente simulée et Kingman(ρ) est **grand quand le stockage est faible** ;
2. remplacer ρ par ρ_eff = λ·E[S_eff], avec E[S_eff] = E[S] + B/N, **le réduit fortement**.

B est `berth_blocked_h` (heures de quai occupé sans décharger) et N le nombre de navires
servis. Autrement dit : le vrai serveur n'est pas « le déchargement », c'est « le quai »,
et sa durée de service inclut le temps d'attente de place au stockyard.

**Ce que ce script ne peut pas montrer.** Un meilleur accord ne démontre pas le mécanisme :
ρ_eff est calculé *à partir* de la simulation qu'il prédit, donc il lui emprunte de
l'information. C'est un test de cohérence interne du modèle, pas une validation
indépendante. La seule validation indépendante du projet est celle de
`tests/test_analytic.py`, où Pollaczek-Khinchine ne doit rien à la simulation.

Deux réserves de calcul, à garder en tête en lisant le tableau :

- E[S_eff] remplace E[S] **dans les deux facteurs** de Kingman (l'utilisation ρ/(1−ρ) et
  le temps E[S]), parce qu'un serveur dont le service dure plus longtemps a les deux à la
  fois. En revanche c_s² reste celui du service **sans blocage** : la variabilité du
  service effectif n'est pas mesurée, et elle est certainement plus grande. Kingman avec
  ρ_eff est donc encore un minorant de ce que l'approximation donnerait avec le bon c_s² ;
- λ est le taux d'arrivée **offert** (1/intervalle moyen), pas le taux servi. Dans les
  cases les plus engorgées, des navires n'ont jamais accosté : le système n'y est pas en
  régime stationnaire et aucune formule de file ne s'y applique vraiment.

Usage : python scripts/h2_blocking.py [--reps 30] [--years 1] [--figure docs/h2_blocage.png]
"""

import argparse
import math
import random
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # aucune fenêtre : on écrit un fichier
import matplotlib.pyplot as plt

from corridor.sim.analytic import (
    effective_service,
    kingman,
    service_moments,
    utilisation,
)
from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.samplers import Samplers
from corridor.sim.sensitivity import DISPERSIONS, SCENARIOS, run_grid

DEFAULT_FIGURE = Path("docs/h2_blocage.png")
MOMENT_DRAWS = 200_000
MOMENT_SEED = 0


def scenario_service(scenario: str, draws: int = MOMENT_DRAWS) -> dict:
    """Taux d'arrivée et moments du service d'un scénario : indépendants du point de grille.

    Ni la taille des navires ni la cadence de déchargement ne dépendent de la dispersion
    ou de la capacité de stockage : ces grandeurs se calculent donc une fois par scénario.
    """
    config = SimConfig.from_scenario(load_scenario(scenario))
    moments = service_moments(
        Samplers.from_config(config), draws=draws, rng=random.Random(MOMENT_SEED)
    )
    lam = 1.0 / config.intervalle_moyen_arrivees_h
    return {
        "config": config,
        "lam": lam,
        "es": moments.es,
        "es2": moments.es2,
        "cs2": moments.scv,
        "rho": utilisation(lam, moments.es),
    }


def _kingman_or_none(rho: float, ca2: float, cs2: float, es: float) -> float | None:
    """Kingman, ou None si la file est saturée (ρ ≥ 1) : l'attente n'y est pas finie."""
    if rho >= 1.0:
        return None
    return kingman(rho, ca2, cs2, es)


def h2_rows(points, services) -> list[dict]:
    """Une ligne par point de grille : ρ, ρ_eff, les deux prédictions, les deux rapports.

    `ca2` est le carré du coefficient de variation des intervalles entre arrivées. Dans le
    modèle, ce coefficient **est** `dispersion_arrivees` (voir `interval_sampler`), donc
    ca² = dispersion².
    """
    lignes = []
    for point in points:
        service = services[point.scenario]
        lam, es, cs2 = service["lam"], service["es"], service["cs2"]
        ca2 = point.dispersion**2
        rho = utilisation(lam, es)

        bloque = point.berth_blocked_h.mean
        servis = point.vessels_served.mean
        es_eff = effective_service(es, bloque, servis) if servis > 0 else math.nan
        rho_eff = utilisation(lam, es_eff) if servis > 0 else math.nan

        simule = point.wait_mean_h.mean
        pred_rho = _kingman_or_none(rho, ca2, cs2, es)
        pred_eff = (
            _kingman_or_none(rho_eff, ca2, cs2, es_eff)
            if servis > 0 and math.isfinite(rho_eff)
            else None
        )
        lignes.append({
            "scenario": point.scenario,
            "dispersion": point.dispersion,
            "storage_kt": point.storage_total_t / 1e3,
            "wait_sim_h": simule,
            "wait_sim_hw": point.wait_mean_h.half_width,
            "blocked_h": bloque,
            "vessels_served": servis,
            "occupancy": point.berth_occupancy.mean,
            "rho": rho,
            "rho_eff": rho_eff,
            "pred_rho_h": pred_rho,
            "pred_eff_h": pred_eff,
            "ratio_rho": _ratio(simule, pred_rho),
            "ratio_eff": _ratio(simule, pred_eff),
        })
    return lignes


def _ratio(simule: float, predit: float | None) -> float | None:
    """Rapport attente simulée / attente prédite ; None si la prédiction n'existe pas."""
    if predit is None or predit <= 0:
        return None
    return simule / predit


def ecart_log(ratio: float | None) -> float | None:
    """Écart absolu en log du rapport : 0 si l'accord est parfait, symétrique au-dessus
    et au-dessous de 1.

    Mesurer l'écart en log évite de récompenser une prédiction qui divise par 10 plutôt
    que de multiplier par 10 : les deux sont également fausses.
    """
    if ratio is None or ratio <= 0 or not math.isfinite(ratio):
        return None
    return abs(math.log(ratio))


def _fini(valeurs):
    return [v for v in valeurs if v is not None and math.isfinite(v)]


def verdict(lignes) -> dict:
    """Chiffres sur lesquels H2 se juge, en ne gardant que les cases avec attente mesurable.

    Les cases à dispersion nulle sont écartées : l'attente simulée y est nulle, le rapport
    simulé/prédit n'a donc pas de sens. H2 porte sur les régimes où il y a une file.
    """
    utiles = [x for x in lignes if x["dispersion"] > 0 and x["wait_sim_h"] > 0]
    stockages = sorted({x["storage_kt"] for x in utiles})
    bas, haut = stockages[0], stockages[-1]

    def mediane(sous_ensemble, cle):
        valeurs = _fini(ecart_log(x[cle]) for x in sous_ensemble)
        return statistics.median(valeurs) if valeurs else math.nan

    return {
        "n_cases": len(utiles),
        "stockage_bas_kt": bas,
        "stockage_haut_kt": haut,
        "ecart_rho_global": mediane(utiles, "ratio_rho"),
        "ecart_eff_global": mediane(utiles, "ratio_eff"),
        "ecart_rho_bas": mediane([x for x in utiles if x["storage_kt"] == bas], "ratio_rho"),
        "ecart_rho_haut": mediane([x for x in utiles if x["storage_kt"] == haut], "ratio_rho"),
        "ecart_eff_bas": mediane([x for x in utiles if x["storage_kt"] == bas], "ratio_eff"),
        "ecart_eff_haut": mediane([x for x in utiles if x["storage_kt"] == haut], "ratio_eff"),
        "ratios_rho": _fini(x["ratio_rho"] for x in utiles),
        "ratios_eff": _fini(x["ratio_eff"] for x in utiles),
        "satures": sum(1 for x in utiles if x["pred_eff_h"] is None),
    }


def _defini(valeur) -> bool:
    """Vrai si la grandeur existe et est finie : sinon elle s'affiche en tiret cadratin."""
    return valeur is not None and math.isfinite(valeur)


def _n(valeur, digits=1, largeur=0):
    """Nombre aligné à droite pour le tableau détaillé."""
    texte = f"{valeur:.{digits}f}" if _defini(valeur) else "—"
    return texte.rjust(largeur)


def _fr(valeur, digits=2):
    """Nombre à la française (virgule décimale), pour les tableaux du README."""
    return f"{valeur:.{digits}f}".replace(".", ",") if _defini(valeur) else "—"


def print_table(lignes) -> None:
    """Tableau détaillé, un bloc par scénario."""
    for scenario in sorted({x["scenario"] for x in lignes}):
        print(f"\n{'=' * 118}\n{scenario}\n{'=' * 118}")
        print(f"{'stock':>7} {'disp':>5} {'attente sim':>14} {'bloqué':>9} {'occup':>6} "
              f"{'ρ':>6} {'ρ_eff':>6} {'King(ρ)':>9} {'rapport':>8} "
              f"{'King(ρ_eff)':>12} {'rapport':>8}")
        print(f"{'(kt)':>7} {'':>5} {'(h)':>14} {'(h)':>9} {'':>6} "
              f"{'':>6} {'':>6} {'(h)':>9} {'sim/préd':>8} {'(h)':>12} {'sim/préd':>8}")
        sous = [x for x in lignes if x["scenario"] == scenario]
        for x in sorted(sous, key=lambda r: (r["storage_kt"], r["dispersion"])):
            print(
                f"{x['storage_kt']:>7.0f} {x['dispersion']:>5.2f} "
                f"{x['wait_sim_h']:>8.1f} ±{x['wait_sim_hw']:>5.1f} "
                f"{x['blocked_h']:>9.0f} {x['occupancy']:>6.3f} "
                f"{x['rho']:>6.3f} {_n(x['rho_eff'], 3, 6)} "
                f"{_n(x['pred_rho_h'], 1, 9)} {_n(x['ratio_rho'], 2, 8)} "
                f"{_n(x['pred_eff_h'], 1, 12)} {_n(x['ratio_eff'], 2, 8)}"
            )


def print_markdown(lignes) -> None:
    """Tableau condensé, prêt à coller dans le README : les cases à dispersion 1 (Poisson)."""
    print("\n\n### Tableau pour le README — arrivées de Poisson (dispersion 1,0)\n")
    print("| Scénario | Stockage | Attente simulée | Heures de quai bloquées | ρ | ρ_eff "
          "| Kingman(ρ) | Kingman(ρ_eff) |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for x in sorted(lignes, key=lambda r: (r["scenario"], r["storage_kt"])):
        if x["dispersion"] != 1.0:
            continue
        print(
            f"| {x['scenario']} | {x['storage_kt']:.0f} kt "
            f"| {_fr(x['wait_sim_h'], 0)} ± {_fr(x['wait_sim_hw'], 0)} h "
            f"| {_fr(x['blocked_h'], 0)} h | {_fr(x['rho'])} | {_fr(x['rho_eff'])} "
            f"| {_fr(x['pred_rho_h'], 0)} h (×{_fr(x['ratio_rho'])}) "
            f"| {_fr(x['pred_eff_h'], 0)} h (×{_fr(x['ratio_eff'])}) |"
        )


def print_verdict(v) -> None:
    """Conclusion chiffrée sur H2, avec ses deux volets jugés séparément."""
    print(f"\n\n{'=' * 118}\nVerdict sur H2\n{'=' * 118}")
    print(f"{v['n_cases']} cases retenues (dispersion > 0 et attente simulée > 0)")
    print("\nÉcart médian à la prédiction, mesuré en |log(simulé/prédit)| "
          "(0 = accord parfait) :")
    print(f"  avec ρ      : {v['ecart_rho_global']:.3f}   "
          f"soit un facteur {math.exp(v['ecart_rho_global']):.1f}× d'erreur typique")
    print(f"  avec ρ_eff  : {v['ecart_eff_global']:.3f}   "
          f"soit un facteur {math.exp(v['ecart_eff_global']):.1f}×")
    print("\nVolet 1 — l'écart avec ρ est-il plus grand à stockage faible ?")
    print(f"  stockage {v['stockage_bas_kt']:.0f} kt : écart médian {v['ecart_rho_bas']:.3f} "
          f"(facteur {math.exp(v['ecart_rho_bas']):.1f}×)")
    print(f"  stockage {v['stockage_haut_kt']:.0f} kt : écart médian {v['ecart_rho_haut']:.3f} "
          f"(facteur {math.exp(v['ecart_rho_haut']):.1f}×)")
    print("\nVolet 2 — ρ_eff réduit-il fortement l'écart ?")
    print(f"  stockage {v['stockage_bas_kt']:.0f} kt : {v['ecart_rho_bas']:.3f} → "
          f"{v['ecart_eff_bas']:.3f}")
    print(f"  stockage {v['stockage_haut_kt']:.0f} kt : {v['ecart_rho_haut']:.3f} → "
          f"{v['ecart_eff_haut']:.3f}")
    if v["satures"]:
        print(f"\n  {v['satures']} case(s) où ρ_eff ≥ 1 : Kingman n'y est pas défini "
              "(file saturée, pas de régime stationnaire).")
    print("\nRappel : ρ_eff est calculé à partir de la simulation qu'il prédit. Un meilleur "
          "accord\nest une cohérence interne, pas une validation indépendante du mécanisme.")


def write_figure(lignes, path=DEFAULT_FIGURE) -> Path:
    """Quatre panneaux : rapport simulé/prédit contre stockage, par scénario et par variante.

    Un rapport de 1 (ligne horizontale) signifie que la formule tombe juste. Au-dessus,
    elle sous-estime l'attente. L'échelle est logarithmique parce que les rapports
    couvrent plusieurs ordres de grandeur, et le logarithme traite symétriquement une
    sous-estimation et une surestimation du même facteur.

    Les cases à dispersion nulle sont absentes : l'attente simulée y est nulle, donc le
    rapport n'est pas défini. La dispersion est codée par une **rampe d'une seule teinte**
    (clair = régulier, foncé = Poisson) parce que c'est une grandeur ordonnée.
    """
    scenarios = sorted({x["scenario"] for x in lignes})
    dispersions = sorted({x["dispersion"] for x in lignes if x["dispersion"] > 0})
    rampe = plt.get_cmap("Blues")
    couleurs = {
        d: rampe(0.35 + 0.6 * i / max(len(dispersions) - 1, 1))
        for i, d in enumerate(dispersions)
    }
    variantes = (
        ("ratio_rho", "Kingman avec ρ = λ·E[S]\n(service = déchargement seul)"),
        ("ratio_eff", "Kingman avec ρ_eff = λ·(E[S] + B/N)\n(service = occupation du quai)"),
    )

    figure, axes = plt.subplots(
        len(variantes), len(scenarios), figsize=(6.6 * len(scenarios), 9.0),
        squeeze=False, sharex=True, sharey=True, constrained_layout=True,
    )
    for i, (cle, titre_variante) in enumerate(variantes):
        for j, scenario in enumerate(scenarios):
            ax = axes[i][j]
            ax.axhline(1.0, color="0.35", linestyle="--", linewidth=1.2, zorder=1)
            for d in dispersions:
                points = sorted(
                    (x for x in lignes
                     if x["scenario"] == scenario and x["dispersion"] == d
                     and x[cle] is not None and math.isfinite(x[cle]) and x[cle] > 0),
                    key=lambda r: r["storage_kt"],
                )
                if not points:
                    continue
                ax.plot(
                    [p["storage_kt"] for p in points], [p[cle] for p in points],
                    marker="o", markersize=5.5, linewidth=2.0,
                    color=couleurs[d], label=f"dispersion {d:.2f}", zorder=3,
                )
            ax.set_yscale("log")
            ax.grid(True, which="major", color="0.88", linewidth=0.8, zorder=0)
            ax.set_axisbelow(True)
            if i == len(variantes) - 1:
                ax.set_xlabel("capacité totale de stockage (kt), port + usine")
            if j == 0:
                ax.set_ylabel(f"{titre_variante}\n\nattente simulée / prédite (×)")
            ax.set_title(scenario if i == 0 else "", fontweight="bold")
    axes[0][0].legend(title="régularité des arrivées", fontsize=8, title_fontsize=8,
                      loc="upper right", framealpha=0.9)
    figure.suptitle(
        "Hypothèse H2 : le blocage du poste explique-t-il l'écart à la théorie des files ?\n"
        "Ligne pointillée = accord parfait ; au-dessus, la formule sous-estime l'attente. "
        "Échelle logarithmique.\n"
        "En haut : service = déchargement seul. En bas : service = occupation du quai, "
        "blocage compris (E[S] + B/N).\n"
        "ρ_eff est calculé à partir de la simulation qu'il prédit : un meilleur accord est "
        "une cohérence interne, pas une preuve du mécanisme.",
        fontsize=10.5,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=30)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    parser.add_argument("--no-figure", action="store_true")
    args = parser.parse_args(argv)

    print(f"grille {len(args.scenarios)} scénario(s) × {len(DISPERSIONS)} dispersions × "
          f"5 capacités, {args.reps} réplications de {args.years:g} an")
    points = run_grid(
        scenarios=args.scenarios, reps=args.reps, years=args.years, progress=True
    )
    services = {s: scenario_service(s) for s in args.scenarios}
    for nom, s in services.items():
        print(f"\n{nom} : λ = {s['lam']:.6f} navire/h "
              f"(1 tous les {1 / s['lam']:.0f} h), E[S] = {s['es']:.1f} h, "
              f"c_s² = {s['cs2']:.4f}, ρ = {s['rho']:.4f}")

    lignes = h2_rows(points, services)
    print_table(lignes)
    print_markdown(lignes)
    print_verdict(verdict(lignes))
    if not args.no_figure:
        chemin = write_figure(lignes, args.figure)
        print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
