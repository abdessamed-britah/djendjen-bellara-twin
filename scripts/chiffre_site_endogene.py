"""Le chiffre appliqué du site, refait sur le modèle corrigé : phase 2, approvisionnement endogène.

`beta_site.py` publiait la distribution de β sur le modèle exogène, en tirant c_a² dans son
bootstrap. Avec l'approvisionnement endogène, c_a² n'est plus un paramètre : l'irrégularité
des arrivées naît de la politique de commande et de l'aléa des délais. Les paramètres
inconnus qui restent sont ceux qu'on tire ici, **chacun uniformément dans sa fourchette
publiée** de `assumptions.yaml` :

- les deux capacités de stockage, port et usine **indépendamment** (tirer le total ferait
  descendre la part du port sous son minimum publié, limite 7) ;
- la durée moyenne d'un arrêt non planifié du four (seul le mode discret s'en sert : en
  continu, elle est tirée mais sans effet, ce qui garde les tirages appariés entre variantes) ;
- `cv_delai`, la dispersion du délai d'approvisionnement.

Le point de commande reste à son optimum de coût dans chaque variante (étapes 2 et 5) : c'est
une décision de l'exploitant, pas une inconnue du modèle. Les **mêmes 500 tirages** servent
aux quatre variantes (continu / discret × dépassements permis / interdits).

**Deux horizons, deux questions.** Par tirage, une trajectoire de 16 ans mesurés en régime
stationnaire (le **long terme** : ce que le projet ignore des paramètres, pour dimensionner)
et une trajectoire d'**une année isolée** après le même échauffement (ce qu'un exploitant
affronte une année donnée, pour budgéter). Les deux ne se substituent pas.

**Décomposition de la variance.** À paramètres figés à leurs valeurs centrales, des
réplications répétées donnent la variance due au seul aléa ; rapportée à la variance totale
des tirages, elle dit quelle part de la dispersion vient de l'aléa d'une année et quelle part
de l'incertitude des paramètres. Approximation : la variance de l'aléa est mesurée au centre,
elle varie d'un tirage à l'autre. L'importance de chaque paramètre est donnée par le carré
de sa corrélation de rang avec le résultat de long terme.

Usage : python scripts/chiffre_site_endogene.py [--tirages 500] [--processus 10]
"""

import argparse
import csv
import dataclasses
import importlib.util
import os
import random
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.labels import LANGUES, chemin_langue, libelle
from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_yaml
from corridor.sim.run import (
    ECHAUFFEMENT_STATIONNAIRE_ANS,
    FENETRE_STATIONNAIRE_ANS,
    simuler_en_parallele,
)


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fe = _charger("factoriel_endogene")
eco = fe.eco

SCENARIO = fe.SCENARIO
VARIANTES = fe.VARIANTES
#: Les paramètres tirés : nom, chemin dans assumptions.yaml.
PARAMETRES = (
    ("stockyard_t", ("stockage_port", "capacite_stockyard_pellets")),
    ("usine_t", ("usine", "capacite_stock_pellets_usine")),
    ("duree_arret_j", ("usine", "duree_arret_non_planifie_j")),
    ("cv_delai", ("approvisionnement", "cv_delai")),
)
HORIZONS = (("long_terme", FENETRE_STATIONNAIRE_ANS), ("une_annee", 1.0))
INDICATEURS = ("beta_appro", "total")
SEUILS_BETA = (0.95, 0.99)
QUANTILES = (0.05, 0.50, 0.95)
#: Au-delà, l'étape 3 a jugé `cv_delai` peu plausible pour un délai de transport (écart-type
#: ~9 j sur 22 à 0,40). Le chiffre est publié sur toute la fourchette, puis restreint.
CV_DELAI_PLAUSIBLE = 0.40
DEFAULT_TIRAGES = 500
DEFAULT_REPS_ALEA = 100
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/chiffre_site_endogene.png")
DEFAULT_CSV = Path("data/sim/chiffre_site_endogene.csv")


# --- tirages -------------------------------------------------------------------------


def fourchettes(raw) -> dict[str, tuple[float, float]]:
    return {nom: tuple(float(x) for x in raw[a][b]["range"]) for nom, (a, b) in PARAMETRES}


def centres(raw) -> dict[str, float]:
    return {nom: float(raw[a][b]["value"]) for nom, (a, b) in PARAMETRES}


def tirer(raw, n: int, seed: int) -> list[dict]:
    """`n` jeux de paramètres, chacun uniforme dans sa fourchette, et deux graines par tirage
    (une par horizon) : le même tirage sert aux quatre variantes."""
    rng = random.Random(seed)
    bornes = fourchettes(raw)
    tirages = []
    for _ in range(n):
        tirage = {nom: rng.uniform(*bornes[nom]) for nom, _ in PARAMETRES}
        tirage.update(graine_long=rng.randrange(2**32), graine_annee=rng.randrange(2**32))
        tirages.append(tirage)
    return tirages


def configuration(raw, mode: str, depassements: bool, parametres: dict) -> SimConfig:
    """La case de base du plan factoriel, avec les quatre paramètres tirés."""
    base = fe.configuration(raw, mode, depassements, postes=1, stockage="base")
    port, usine = parametres["stockyard_t"], parametres["usine_t"]
    arret_h = parametres["duree_arret_j"] * 24
    u = base.taux_utilisation_dri
    return dataclasses.replace(
        base, capacite_stockyard_t=port, capacite_stock_usine_t=usine,
        stock_initial_stockyard_t=port * base.fraction_stock_initial,
        stock_initial_usine_t=usine * base.fraction_stock_initial,
        duree_arret_h=arret_h, duree_marche_moyenne_h=arret_h * u / (1 - u),
        cv_delai_approvisionnement=parametres["cv_delai"])


def indicateurs(resultat, config, unitaires) -> dict[str, float]:
    annees = resultat.annees_mesurees
    cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an * annees
    return {"beta_appro": 1.0 - eco.perte_faute_de_pellets_t(resultat, config) / cible,
            **eco.cout_total(resultat, config, unitaires)}


def simuler(configs_graines, annees: float, unitaires, processus: int) -> list[dict]:
    """Une trajectoire par (config, graine), mesurée sur `annees` après l'échauffement."""
    taches = [(config, graine, ECHAUFFEMENT_STATIONNAIRE_ANS + annees, SCENARIO, k,
               ECHAUFFEMENT_STATIONNAIRE_ANS)
              for k, (config, graine) in enumerate(configs_graines)]
    resultats = simuler_en_parallele(taches, processus)
    return [indicateurs(r, c, unitaires)
            for r, (c, _) in zip(resultats, configs_graines, strict=True)]


# --- statistiques --------------------------------------------------------------------


def quantile(valeurs, q: float) -> float:
    """Quantile empirique par interpolation linéaire (méthode inclusive)."""
    triees = sorted(valeurs)
    position = q * (len(triees) - 1)
    bas = int(position)
    haut = min(bas + 1, len(triees) - 1)
    return triees[bas] + (position - bas) * (triees[haut] - triees[bas])


def resume(valeurs) -> dict[str, float]:
    return {f"q{round(q * 100):02d}": quantile(valeurs, q) for q in QUANTILES}


def part_alea(valeurs_tirages, valeurs_alea) -> float:
    """Part de la variance totale qu'explique l'aléa seul, bornée à 1."""
    totale = statistics.variance(valeurs_tirages)
    if totale == 0:
        return 0.0
    return min(1.0, statistics.variance(valeurs_alea) / totale)


def importance(tirages, valeurs) -> dict[str, float]:
    """Carré de la corrélation de rang de chaque paramètre avec le résultat."""
    sortie = {}
    for nom, _ in PARAMETRES:
        x = [t[nom] for t in tirages]
        if len(set(valeurs)) < 2:
            sortie[nom] = 0.0
            continue
        sortie[nom] = statistics.correlation(x, valeurs, method="ranked") ** 2
    return sortie


def part_au_dessus(valeurs, seuil: float) -> float:
    return sum(v >= seuil for v in valeurs) / len(valeurs)


# --- figure --------------------------------------------------------------------------


def donnees_figure(resultats: dict) -> dict:
    """{(indicateur, variante, horizon): (valeurs triées, fréquences cumulées)}, mis à
    l'échelle d'affichage (β en %, coût en M USD/an)."""
    echelles = {"beta_appro": 100.0, "total": 1e-6}
    sortie = {}
    for (variante, horizon), lignes in resultats["tirages"].items():
        for cle in INDICATEURS:
            x = sorted(ligne[cle] * echelles[cle] for ligne in lignes)
            sortie[(cle, variante, horizon)] = (x, [(k + 1) / len(x) for k in range(len(x))])
    return sortie


def write_figure(resultats: dict, path=DEFAULT_FIGURE) -> list[Path]:
    donnees = donnees_figure(resultats)
    styles = {"long_terme": "-", "une_annee": ":"}
    chemins = []
    for langue in LANGUES:
        figure, axes = plt.subplots(1, len(INDICATEURS), figsize=(13, 5.2),
                                    constrained_layout=True)
        for ax, cle in zip(axes, INDICATEURS, strict=True):
            for variante in VARIANTES:
                for horizon, _ in HORIZONS:
                    x, y = donnees[(cle, variante, horizon)]
                    ax.step(x, y, where="post", color=fe.COULEURS[variante],
                            linestyle=styles[horizon], linewidth=1.4,
                            label=(f"{libelle(variante[0], langue)}, "
                                   f"{libelle('permis' if variante[1] else 'interdits', langue)}"
                                   f" — {libelle(horizon, langue)}"))
            ax.set_xlabel(libelle(cle, langue).replace("(points)", "(%)"))
            ax.set_ylabel(libelle("frequence", langue))
            ax.grid(True, color="0.9")
        for seuil in SEUILS_BETA:
            axes[0].axvline(seuil * 100, color="0.5", linewidth=0.8, linestyle="--")
        poignees, etiquettes = axes[0].get_legend_handles_labels()
        figure.legend(poignees, etiquettes, loc="outside lower center", ncol=4, fontsize=7)
        figure.suptitle(libelle("titre_site", langue), fontsize=11)
        chemin = chemin_langue(path, langue)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, dpi=150)
        plt.close(figure)
        chemins.append(chemin)
    return chemins


# --- calcul et rapport ---------------------------------------------------------------


def calculer(raw, tirages: list[dict], reps_alea: int, seed: int, processus: int) -> dict:
    unitaires = eco.couts_unitaires(raw)
    centre = centres(raw)
    maitre = random.Random(seed + 1)
    graines_alea = [maitre.randrange(2**32) for _ in range(reps_alea)]
    sortie = {"tirages": {}, "alea": {}}
    for variante in VARIANTES:
        configs = [configuration(raw, *variante, t) for t in tirages]
        config_centre = configuration(raw, *variante, centre)
        for horizon, annees in HORIZONS:
            cle_graine = "graine_long" if horizon == "long_terme" else "graine_annee"
            sortie["tirages"][(variante, horizon)] = simuler(
                [(c, t[cle_graine]) for c, t in zip(configs, tirages, strict=True)],
                annees, unitaires, processus)
            sortie["alea"][(variante, horizon)] = simuler(
                [(config_centre, g) for g in graines_alea], annees, unitaires, processus)
    return sortie


def ecrire_csv(tirages, resultats, chemin=DEFAULT_CSV) -> Path:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    noms = [n for n, _ in PARAMETRES]
    with chemin.open("w", newline="", encoding="utf-8") as f:
        ecrivain = csv.writer(f)
        ecrivain.writerow(["mode", "depassements", "horizon", "tirage", *noms,
                           *INDICATEURS, *eco.TERMES])
        for (variante, horizon), lignes in resultats["tirages"].items():
            for k, (t, ligne) in enumerate(zip(tirages, lignes, strict=True)):
                ecrivain.writerow([variante[0], variante[1], horizon, k,
                                   *(t[n] for n in noms), *(ligne[c] for c in INDICATEURS),
                                   *(ligne[c] for c in eco.TERMES)])
    return chemin


def lire_csv(chemin=DEFAULT_CSV) -> tuple[list[dict], dict]:
    """Relit les tirages écrits par `ecrire_csv` : (tirages, résultats), sans les graines."""
    noms = [n for n, _ in PARAMETRES]
    tirages, resultats = {}, {"tirages": {}}
    with Path(chemin).open(encoding="utf-8") as f:
        for ligne in csv.DictReader(f):
            variante = (ligne["mode"], ligne["depassements"] == "True")
            k = int(ligne["tirage"])
            tirages[k] = {n: float(ligne[n]) for n in noms}
            resultats["tirages"].setdefault((variante, ligne["horizon"]), []).append(
                {c: float(ligne[c]) for c in (*INDICATEURS, *eco.TERMES)})
    return [tirages[k] for k in sorted(tirages)], resultats


def print_conditionnel(tirages, resultats, cle: str, plafond: float) -> None:
    """Le même chiffre restreint aux tirages où un paramètre reste sous un plafond."""
    garde = [t[cle] <= plafond for t in tirages]
    print(f"\n{'=' * 100}\nRestreint aux tirages où {cle} ≤ {plafond} "
          f"({sum(garde)} sur {len(garde)})\n{'=' * 100}")
    for variante in VARIANTES:
        for horizon, _ in HORIZONS:
            lignes = [ligne for ligne, g in zip(resultats["tirages"][(variante, horizon)], garde,
                                                strict=True) if g]
            beta = [ligne["beta_appro"] for ligne in lignes]
            rb = resume(beta)
            rc = resume([ligne["total"] / 1e6 for ligne in lignes])
            print(f"  {variante[0]:8} {'permis' if variante[1] else 'interdits':9} "
                  f"{horizon:11} β appro {rb['q50']:.4f} [{rb['q05']:.4f} ; {rb['q95']:.4f}]"
                  f"  P(β≥0,95) {part_au_dessus(beta, 0.95):.2f}"
                  f"   coût {rc['q50']:.1f} [{rc['q05']:.1f} ; {rc['q95']:.1f}] M USD/an")


def print_report(tirages, resultats) -> None:
    for variante in VARIANTES:
        mode, dep = variante
        print(f"\n{'=' * 100}\n{libelle(mode)}, {libelle('permis' if dep else 'interdits')}, "
              f"s = {fe.S_OPTIMAL_J[variante]} j\n{'=' * 100}")
        for horizon, _ in HORIZONS:
            lignes = resultats["tirages"][(variante, horizon)]
            alea = resultats["alea"][(variante, horizon)]
            beta = [ligne["beta_appro"] for ligne in lignes]
            total = [ligne["total"] for ligne in lignes]
            rb, rc = resume(beta), resume(total)
            print(f"  {libelle(horizon):22} β appro  {rb['q50']:.4f} [{rb['q05']:.4f} ; "
                  f"{rb['q95']:.4f}]   P(β≥0,95) {part_au_dessus(beta, 0.95):.2f}   "
                  f"P(β≥0,99) {part_au_dessus(beta, 0.99):.2f}")
            print(f"  {'':22} coût total {rc['q50'] / 1e6:.1f} [{rc['q05'] / 1e6:.1f} ; "
                  f"{rc['q95'] / 1e6:.1f}] M USD/an   termes médians : " + ", ".join(
                      f"{t} {quantile([ligne[t] for ligne in lignes], 0.5) / 1e6:.1f}"
                      for t in eco.TERMES))
            print(f"  {'':22} part de l'aléa dans la variance : β "
                  f"{part_alea(beta, [a['beta_appro'] for a in alea]):.0%}, coût "
                  f"{part_alea(total, [a['total'] for a in alea]):.0%}")
        long_terme = resultats["tirages"][(variante, "long_terme")]
        for cle in INDICATEURS:
            imp = importance(tirages, [ligne[cle] for ligne in long_terme])
            print(f"  importance (ρ² de rang, long terme) sur {cle:10} " + "  ".join(
                f"{n} {v:.2f}" for n, v in imp.items()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tirages", type=int, default=DEFAULT_TIRAGES)
    parser.add_argument("--reps-alea", type=int, default=DEFAULT_REPS_ALEA)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 4))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--depuis-csv", action="store_true",
                        help="relit --csv au lieu de simuler : rapport restreint et figure")
    args = parser.parse_args(argv)

    if args.depuis_csv:
        tirages, resultats = lire_csv(args.csv)
        print_conditionnel(tirages, resultats, "cv_delai", CV_DELAI_PLAUSIBLE)
        for chemin in write_figure(resultats, args.figure):
            print(f"figure écrite : {chemin}")
        return 0

    raw = load_yaml(ASSUMPTIONS_PATH)
    tirages = tirer(raw, args.tirages, args.seed)
    print(f"{SCENARIO}, {args.tirages} tirages × 4 variantes, {args.reps_alea} réplications "
          f"à paramètres centraux pour l'aléa, {args.processus} processus. Fourchettes : "
          + ", ".join(f"{n} {b}" for n, b in fourchettes(raw).items()))
    resultats = calculer(raw, tirages, args.reps_alea, args.seed, args.processus)
    print_report(tirages, resultats)
    print_conditionnel(tirages, resultats, "cv_delai", CV_DELAI_PLAUSIBLE)
    print(f"\ntirages écrits : {ecrire_csv(tirages, resultats, args.csv)}")
    for chemin in write_figure(resultats, args.figure):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
