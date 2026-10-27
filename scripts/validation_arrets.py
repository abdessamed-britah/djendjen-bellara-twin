"""Nouvelle validation, avec des arrêts discrets du four : la consommation variable suffit-elle ?

L'étape 4 a établi que l'écart de dispersion entre le modèle endogène et les bilans du port
n'est pas un artefact de mesure, et le bilan de masse désignait la variabilité de la
consommation comme piste la plus directe. Le mode `discret` du four la rend possible sans
toucher à la production moyenne : le four tourne à pleine cadence ou s'arrête, à
disponibilité inchangée. Ce script dit si cela suffit à rejoindre l'observé, et pour quelles
durées d'arrêt, quelles capacités de stockage et quelle variabilité du délai.

**LES RÈGLES, ÉCRITES AVANT LE CALCUL ET FIGÉES DANS LES CONSTANTES CI-DESSOUS.**

1. *Période de référence : 2023-2025.* 2022 contient une interruption (T3, 0,6 kt) suivie
   d'un rattrapage (T4, 1 356 kt), signature d'un régime de montée en cadence que le modèle
   ne représente pas. **Ce choix est fait après l'étape 4, qui a montré le poids de 2022** :
   il est donc écrit comme tel, et le verdict sur 2022-2025 est publié à côté.
2. *Statistique :* le CV² intra-annuel des tonnages trimestriels, comme à l'étape 4, calculé
   par les mêmes fonctions (`diagnostic_dispersion`) sur les données et sur la simulation.
3. *Verdict :* l'observé est **compatible** s'il tombe entre les quantiles 5 % et 95 % de la
   distribution simulée, sur des fenêtres de même longueur, alignées sur les années :
   12 trimestres pour 2023-2025, 16 pour 2022-2025.
4. *Aucun paramètre n'est réglé pour atteindre l'observé.* La durée d'arrêt, la capacité de
   stockage et `cv_delai` sont **balayés sur leurs fourchettes d'hypothèse**, et la carte dit
   où l'observé devient compatible ; rien n'est reporté dans le modèle. Le point de commande
   reste celui de l'étape 2 (30 j), choisi sur un critère de coût.

**Plan du balayage.** `baseline_endogene` (le corridor que décrivent les données), mode
d'arrêts discret ; durée d'arrêt sur sa fourchette ; capacité de stockage aux deux bornes et
au centre des fourchettes publiées (port et usine ensemble) ; `cv_delai` central sous les
deux hypothèses de dépassement, puis à ses bornes sous l'hypothèse des dépassements permis
seulement, pour tenir le temps de calcul — l'étape 3 a montré que cette hypothèse pèse peu en
baseline. Une ligne de référence en mode continu rejoue l'étape 4.

Usage : python scripts/validation_arrets.py [--graines 4] [--annees 60]
"""

import argparse
import dataclasses
import importlib.util
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.model import chronique
from corridor.sim.run import ECHAUFFEMENT_STATIONNAIRE_ANS

# --- règles pré-enregistrées ----------------------------------------------------
#: Période de référence (règle 1, choisie après l'étape 4) et période complète.
PERIODES = {"2023-2025": (2023, 2025), "2022-2025": (2022, 2025)}
PERIODE_DE_REFERENCE = "2023-2025"
QUANTILES_DU_VERDICT = (0.05, 0.95)
SCENARIO = "baseline_endogene"
#: Durées d'arrêt balayées, en jours : la fourchette de l'hypothèse, de bout en bout.
DUREES_ARRET_J = (2, 5, 7, 10, 15, 20, 30)
#: Capacités de stockage : port et usine ensemble aux bornes et au centre de leurs fourchettes.
NIVEAUX_STOCKAGE = ("bas", "centre", "haut")
TRIMESTRES_PAR_AN = 4
DEFAULT_GRAINES = 4
DEFAULT_ANNEES = 60.0
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/validation_arrets.png")


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dd = _charger("diagnostic_dispersion")
val = _charger("validation_ca2")
disp = dd.disp


def cv2_intra(tonnages) -> float:
    """La statistique du verdict, par la fonction de l'étape 4 : rien n'est recalculé à part."""
    moyenne = sum(tonnages) / len(tonnages)
    return dd.variance_intra_annuelle(tonnages) / moyenne ** 2


def observe(annees_periode) -> float:
    """CV² intra-annuel des pellets débarqués sur la période, lu dans les bilans archivés."""
    cumuls, _ = disp.lire_cumuls()
    debut, fin = annees_periode
    tonnages = [v for a, _, v in disp.trimestres(cumuls) if debut <= a <= fin]
    attendu = (fin - debut + 1) * TRIMESTRES_PAR_AN
    if len(tonnages) != attendu:
        raise ValueError(f"période {debut}-{fin} incomplète : {len(tonnages)} trimestres")
    return cv2_intra(tonnages)


def capacites(raw, niveau: str) -> tuple[float, float]:
    """Stockyard et stock usine, ensemble à une borne ou au centre de leurs fourchettes."""
    port = raw["stockage_port"]["capacite_stockyard_pellets"]
    usine = raw["usine"]["capacite_stock_pellets_usine"]
    if niveau == "centre":
        return float(port["value"]), float(usine["value"])
    i = 0 if niveau == "bas" else 1
    return float(port["range"][i]), float(usine["range"][i])


def configuration(raw, depassements=True, cv_delai=None, duree_j=None, stockage="centre",
                  mode="discret"):
    """La configuration d'une case de la carte, à partir de celle de l'étape 3."""
    base = val.configuration(SCENARIO, depassements, cv_delai=cv_delai)
    port, usine = capacites(raw, stockage)
    ecarts = {"mode_arrets_dri": mode, "capacite_stockyard_t": port,
              "capacite_stock_usine_t": usine,
              "stock_initial_stockyard_t": port * base.fraction_stock_initial,
              "stock_initial_usine_t": usine * base.fraction_stock_initial}
    if duree_j is not None:
        arret_h = duree_j * 24.0
        a = base.taux_utilisation_dri
        ecarts |= {"duree_arret_h": arret_h, "duree_marche_moyenne_h": arret_h * a / (1 - a)}
    return dataclasses.replace(base, **ecarts)


def fenetres(tonnages, annees: int) -> list[list[float]]:
    """Fenêtres disjointes de `annees` années, alignées sur les années simulées."""
    longueur = annees * TRIMESTRES_PAR_AN
    return [tonnages[i:i + longueur] for i in range(0, len(tonnages) - longueur + 1, longueur)]


def simuler(config, seeds, annees: float) -> list[float]:
    """Tonnages déchargés par trimestre après échauffement, mis bout à bout graine par graine."""
    echauffement_h = ECHAUFFEMENT_STATIONNAIRE_ANS * config.heures_par_an
    series = []
    for g in seeds:
        c = chronique(config, seed=g, years=ECHAUFFEMENT_STATIONNAIRE_ANS + annees,
                      periode_h=config.heures_par_an / TRIMESTRES_PAR_AN)
        series.append([p["debarque_t"] for p in c["periodes"] if p["debut_h"] >= echauffement_h])
    return series


def juger(series, periode, valeur_observee) -> dict:
    """Règle 3 : la distribution simulée sur des fenêtres de même longueur, et le verdict."""
    debut, fin = PERIODES[periode]
    valeurs = [cv2_intra(f) for s in series for f in fenetres(s, fin - debut + 1)]
    bas = val.quantile(valeurs, QUANTILES_DU_VERDICT[0])
    haut = val.quantile(valeurs, QUANTILES_DU_VERDICT[1])
    return {"fenetres": len(valeurs), "q05": bas, "mediane": val.quantile(valeurs, 0.5),
            "q95": haut, "compatible": bas <= valeur_observee <= haut,
            "rang": sum(v <= valeur_observee for v in valeurs) / len(valeurs)
            if valeurs else math.nan}


# --- figure ---------------------------------------------------------------------


def donnees_figure(carte: dict, observes: dict) -> dict:
    """{(cv, dépassements, stockage): (durées, q05, médiane, q95)} sur la période de référence."""
    sortie = {}
    for (cv, dep, stockage, duree), r in carte.items():
        sortie.setdefault((cv, dep, stockage), []).append((duree, r[PERIODE_DE_REFERENCE]))
    return {
        "series": {cle: ([d for d, _ in sorted(v)], [x["q05"] for _, x in sorted(v)],
                         [x["mediane"] for _, x in sorted(v)], [x["q95"] for _, x in sorted(v)])
                   for cle, v in sortie.items()},
        "observe": observes[PERIODE_DE_REFERENCE],
    }


def write_figure(carte, observes, path=DEFAULT_FIGURE) -> Path:
    donnees = donnees_figure(carte, observes)
    cvs = sorted({cle[0] for cle in donnees["series"]})
    figure, axes = plt.subplots(1, len(cvs), figsize=(5.2 * len(cvs), 5.2), sharey=True,
                                constrained_layout=True, squeeze=False)
    couleurs = {"bas": "#d62728", "centre": "#1f77b4", "haut": "#2ca02c"}
    for ax, cv in zip(axes[0], cvs, strict=True):
        for (c, dep, stockage), (d, bas, med, haut) in donnees["series"].items():
            if c != cv or not dep:
                continue
            ax.fill_between(d, bas, haut, color=couleurs[stockage], alpha=0.15)
            ax.plot(d, med, color=couleurs[stockage], marker="o",
                    label=f"stockage {stockage}")
        ax.axhline(donnees["observe"], color="0.2", linestyle="--",
                   label=f"observé {PERIODE_DE_REFERENCE}")
        ax.set_title(f"cv_delai = {cv}", fontweight="bold")
        ax.set_xlabel("durée moyenne d'un arrêt non planifié (jours)")
        ax.set_yscale("log")
        ax.grid(True, which="both", color="0.9")
    axes[0][0].set_ylabel("CV² intra-annuel des tonnages débarqués")
    axes[0][0].legend(fontsize=8.5)
    figure.suptitle(f"Arrêts discrets du four : la dispersion simulée rejoint-elle l'observé ? "
                    f"({PERIODE_DE_REFERENCE}, fenêtres de 12 trimestres)\nBandes : 5 %-95 % "
                    "simulé, baseline_endogene, dépassements permis. Compatible = l'observé "
                    "dans la bande.", fontsize=10.5)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


# --- rapport ----------------------------------------------------------------------


def _ligne(nom, r) -> str:
    return " ".join(
        f"{r[p]['mediane']:.3f} [{r[p]['q05']:.3f} ; {r[p]['q95']:.3f}] "
        f"{'COMPATIBLE' if r[p]['compatible'] else 'non':<10}" for p in PERIODES) + f"  {nom}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graines", type=int, default=DEFAULT_GRAINES)
    parser.add_argument("--annees", type=float, default=DEFAULT_ANNEES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--durees", type=float, nargs="+", default=list(DUREES_ARRET_J))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    val.verifier_preenregistrement(raw)
    observes = {p: observe(bornes) for p, bornes in PERIODES.items()}
    seeds = val.graines(args.graines, args.seed)
    cv_central = val.CV_DELAI_PREENREGISTRE
    bas_cv, haut_cv = raw["approvisionnement"]["cv_delai"]["range"]
    print("Observé, CV² intra-annuel : " + ", ".join(
        f"{p} = {v:.4f}" for p, v in observes.items())
          + f" ; référence {PERIODE_DE_REFERENCE} (choisie après l'étape 4)")
    print(f"{args.graines} graines × {args.annees:.0f} ans après {ECHAUFFEMENT_STATIONNAIRE_ANS:.0f}"
          " ans d'échauffement ; colonnes : " + " | ".join(
              f"{p} médiane [5 % ; 95 %] verdict" for p in PERIODES))

    def mesurer(config):
        series = simuler(config, seeds, args.annees)
        return {p: juger(series, p, observes[p]) for p in PERIODES}

    print("\n  référence, mode continu (étape 4) :")
    print("  " + _ligne("continu", mesurer(configuration(raw, mode="continu"))))

    carte = {}
    plans = ([(cv_central, True), (cv_central, False)]
             + [(cv, True) for cv in (bas_cv, haut_cv)])
    for cv, depassements in plans:
        print(f"\n  --- cv_delai = {cv}, {dict(val.HYPOTHESES)[depassements]}")
        for stockage in NIVEAUX_STOCKAGE:
            port, usine = capacites(raw, stockage)
            for duree in args.durees:
                r = mesurer(configuration(raw, depassements, cv_delai=cv, duree_j=duree,
                                          stockage=stockage))
                carte[(cv, depassements, stockage, duree)] = r
                nom = f"stockage {stockage} ({(port + usine) / 1e3:.0f} kt), arrêt {duree:g} j"
                print("  " + _ligne(nom, r))

    print(f"\n{'=' * 100}\nCombinaisons compatibles\n{'=' * 100}")
    for periode in PERIODES:
        ok = [cle for cle, r in carte.items() if r[periode]["compatible"]]
        print(f"  {periode} : {len(ok)} sur {len(carte)}")
        for cv, dep, stockage, duree in ok:
            print(f"    cv_delai {cv}, {dict(val.HYPOTHESES)[dep]}, stockage {stockage}, "
                  f"arrêt {duree:g} j")

    chemin = write_figure(carte, observes, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
