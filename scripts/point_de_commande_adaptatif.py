"""Point de commande adaptatif au risque de délai : la règle s*(cv_delai).

L'étape 6 a montré qu'une seule inconnue pilote le chiffre du site : la dispersion du délai
d'approvisionnement, `cv_delai`. Elle tirait pourtant ce paramètre dans toute sa fourchette
en gardant le point de commande à l'optimum calculé pour la valeur centrale : aux grandes
dispersions, la perte publiée était celle d'un exploitant qui ne réajuste pas son stock de
sécurité. Ce script donne la politique d'un exploitant qui réajuste.

**Méthode.** Pour chaque variante du modèle (continu / discret × dépassements permis /
interdits) et chaque `cv_delai` d'une grille couvrant sa fourchette publiée, on cherche le
point de commande s qui minimise le coût total moyen (arrêts + surestaries + portage), en
régime stationnaire, sur la grille de s de l'hypothèse (pas de 5 jours).

- **Recherche locale, pas balayage complet.** On part de l'optimum de la valeur de cv
  précédente et l'on étend la grille tant que le minimum n'a pas deux points évalués de
  chaque côté (ou un bord du domaine). Le s de l'étape 6 est toujours évalué aussi : c'est
  la référence « sans adaptation ».
- **40 réplications appariées par point**, pas 200 : un balayage complet à 200 coûterait de
  l'ordre d'un million d'années simulées. Les différences entre deux s voisins sont
  appariées (mêmes graines, mêmes flux), ce qui suffit à situer le minimum ; l'incertitude
  qui reste est publiée.
- **s* est le sommet de la parabole** passant par le minimum de la grille et ses deux
  voisins, borné à l'intervalle qu'ils encadrent : la grille a un pas de 5 jours, la règle
  ne doit pas en hériter les marches. **IC à 95 % par bootstrap** sur les réplications
  (tirage avec remise des indices de réplication, les mêmes pour tous les s : l'appariement
  est conservé), 1 000 rééchantillons.
- **La règle** est publiée point par point (c'est elle que relit le chiffre du site, par
  interpolation linéaire) et résumée par une droite des moindres carrés.

Usage : python scripts/point_de_commande_adaptatif.py [--reps 40] [--processus 12]
        python scripts/point_de_commande_adaptatif.py --depuis-json   (rapport et figure)
"""

import argparse
import dataclasses
import importlib.util
import json
import os
import random
import statistics
import sys
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.labels import LANGUES, chemin_langue, libelle
from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_yaml
from corridor.sim.stats import estimate, paired_difference, significant


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fe = _charger("factoriel_endogene")
pc = fe.pc
eco = fe.eco

VARIANTES = fe.VARIANTES
#: Nombre de points de la grille de cv_delai, bornes de la fourchette comprises.
POINTS_CV = 6
PAS_S_J = 5
#: Les grandeurs gardées réplication par réplication.
CLES = ("beta_appro", "rupture_h_an", "attente_h", "stock_t", "arrets_dri", "surestaries",
        "portage", "total")
DEFAULT_REPS = 40
DEFAULT_BOOTSTRAP = 1000
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/point_de_commande_adaptatif.png")
DEFAULT_JSON = Path("data/sim/point_de_commande_adaptatif.json")


# --- grilles et recherche --------------------------------------------------------------


def grille_cv(raw, points: int = POINTS_CV) -> list[float]:
    bas, haut = (float(x) for x in raw["approvisionnement"]["cv_delai"]["range"])
    return [round(bas + k * (haut - bas) / (points - 1), 6) for k in range(points)]


def grille_s(raw, pas: int = PAS_S_J) -> list[int]:
    bas, haut = (int(x) for x in raw["approvisionnement"]["point_de_commande_j"]["range"])
    return list(range(bas, haut + 1, pas))


def points_a_ajouter(moyennes: dict[int, float], grille: list[int]) -> list[int]:
    """Les s manquants pour que le minimum ait deux points évalués de chaque côté.

    Vide quand la recherche est finie. Un bord du domaine compte comme un côté fermé.
    """
    s_min = min(moyennes, key=moyennes.get)
    i = grille.index(s_min)
    voisins = grille[max(0, i - 2):i] + grille[i + 1:i + 3]
    return [s for s in voisins if s not in moyennes]


def sommet(s_valeurs: list[int], moyennes: list[float]) -> float:
    """s* : sommet de la parabole par le minimum et ses deux voisins, borné entre eux.

    Au bord du domaine, ou si les trois points ne dessinent pas une cuvette, le minimum de la
    grille lui-même.
    """
    i = min(range(len(moyennes)), key=moyennes.__getitem__)
    if i == 0 or i == len(moyennes) - 1:
        return float(s_valeurs[i])
    (x0, x1, x2), (y0, y1, y2) = s_valeurs[i - 1:i + 2], moyennes[i - 1:i + 2]
    denominateur = (x0 - x1) * (x0 - x2) * (x1 - x2)
    a = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / denominateur
    b = (x2 ** 2 * (y0 - y1) + x1 ** 2 * (y2 - y0) + x0 ** 2 * (y1 - y2)) / denominateur
    if a <= 0:
        return float(x1)
    return min(max(-b / (2 * a), x0), x2)


def bootstrap_sommet(series: dict[int, list[float]], tirages: int, seed: int) -> list[float]:
    """s* recalculé sur des rééchantillons des réplications, appariement conservé."""
    s_valeurs = sorted(series)
    n = len(series[s_valeurs[0]])
    rng = random.Random(seed)
    sortie = []
    for _ in range(tirages):
        indices = [rng.randrange(n) for _ in range(n)]
        moyennes = [statistics.fmean(series[s][k] for k in indices) for s in s_valeurs]
        sortie.append(sommet(s_valeurs, moyennes))
    return sortie


def regle_lineaire(cvs: list[float], s_etoiles: list[float]) -> tuple[float, float]:
    """(ordonnée, pente) de la droite des moindres carrés s* = a + b·cv."""
    pente, ordonnee = statistics.linear_regression(cvs, s_etoiles)
    return ordonnee, pente


def interpoler(points: list[tuple[float, float]], cv: float) -> float:
    """s*(cv) par interpolation linéaire entre les points de la règle, constant au-delà."""
    points = sorted(points)
    if cv <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if cv <= x1:
            return y0 + (cv - x0) / (x1 - x0) * (y1 - y0)
    return points[-1][1]


def configuration(raw, mode: str, depassements: bool, cv: float, s_j: float) -> SimConfig:
    base = fe.configuration(raw, mode, depassements, postes=1, stockage="base")
    return dataclasses.replace(base, cv_delai_approvisionnement=cv, point_de_commande_j=float(s_j),
                               point_de_commande_t=s_j * base.debit_pellets_t_par_h * 24)


def chercher(raw, variante, cv: float, depart: int, reps: int, seed: int, unitaires,
             processus: int) -> dict[int, dict[str, list[float]]]:
    """{s: {grandeur: valeurs par réplication}} sur les points évalués pour ce cv."""
    grille = grille_s(raw)
    s_fixe = fe.S_OPTIMAL_J[variante]
    evalues: dict[int, dict[str, list[float]]] = {}
    a_faire = sorted({s for s in (depart - PAS_S_J, depart, depart + PAS_S_J, s_fixe)
                      if s in grille})
    while a_faire:
        mesures = pc.mesurer_plusieurs([configuration(raw, *variante, cv, s) for s in a_faire],
                                       reps, seed, unitaires, fe.SCENARIO, processus=processus)
        for s, mesure in zip(a_faire, mesures, strict=True):
            evalues[s] = {c: pc.serie(mesure, c) for c in CLES}
        a_faire = points_a_ajouter({s: statistics.fmean(v["total"]) for s, v in evalues.items()},
                                   grille)
    return dict(sorted(evalues.items()))


# --- analyse ---------------------------------------------------------------------------


def analyser(resultats: dict, bootstrap: int, seed: int) -> dict:
    """Par variante et par cv : s* et son IC, coût et β à s* et au s fixe de l'étape 6."""
    bornes = grille_s(load_yaml(ASSUMPTIONS_PATH))
    sortie = {}
    for variante, par_cv in resultats.items():
        lignes = []
        for cv, evalues in par_cv.items():
            s_valeurs = list(evalues)
            moyennes = [statistics.fmean(evalues[s]["total"]) for s in s_valeurs]
            s_etoile = sommet(s_valeurs, moyennes)
            boot = sorted(bootstrap_sommet({s: evalues[s]["total"] for s in s_valeurs},
                                           bootstrap, seed))
            s_grille = s_valeurs[min(range(len(moyennes)), key=moyennes.__getitem__)]
            s_fixe = fe.S_OPTIMAL_J[variante]
            balayage = [{"s": s, "mesure": {
                "lignes": [dict(zip(CLES, valeurs, strict=True))
                           for valeurs in zip(*(evalues[s][c] for c in CLES), strict=True)],
                **{c: estimate(evalues[s][c]) for c in CLES}}} for s in s_valeurs]
            opt = pc.optimum(balayage)
            lignes.append({
                "cv": cv, "s_etoile": s_etoile, "ic": (boot[int(0.025 * len(boot))],
                                                       boot[int(0.975 * len(boot)) - 1]),
                "s_grille": s_grille, "interieur": opt["interieur"],
                "au_bord_du_domaine": s_grille in (bornes[0], bornes[-1]),
                "s_fixe": s_fixe,
                "a_s_grille": {c: estimate(evalues[s_grille][c]) for c in CLES},
                "a_s_fixe": {c: estimate(evalues[s_fixe][c]) for c in CLES},
                "gain": {c: paired_difference(evalues[s_fixe][c], evalues[s_grille][c])
                         for c in CLES},
            })
        cvs = [ligne["cv"] for ligne in lignes]
        sortie[variante] = {"lignes": lignes,
                            "droite": regle_lineaire(cvs, [ligne["s_etoile"] for ligne in lignes])}
    return sortie


def points_de_la_regle(analyse: dict, variante) -> list[tuple[float, float]]:
    return [(ligne["cv"], ligne["s_etoile"]) for ligne in analyse[variante]["lignes"]]


# --- sauvegarde ------------------------------------------------------------------------


def vers_json(resultats: dict, reps: int) -> dict:
    return {"reps": reps, "resultats": [
        {"mode": v[0], "depassements": v[1], "cv": cv, "s": s, **valeurs}
        for v, par_cv in resultats.items() for cv, evalues in par_cv.items()
        for s, valeurs in evalues.items()]}


def depuis_json(donnees: dict) -> dict:
    resultats: dict = {}
    for ligne in donnees["resultats"]:
        variante = (ligne["mode"], ligne["depassements"])
        par_cv = resultats.setdefault(variante, {})
        par_cv.setdefault(ligne["cv"], {})[ligne["s"]] = {c: ligne[c] for c in CLES}
    for par_cv in resultats.values():
        for cv in par_cv:
            par_cv[cv] = dict(sorted(par_cv[cv].items()))
    return resultats


def lire_regle(chemin=DEFAULT_JSON, bootstrap: int = 200, seed: int = DEFAULT_SEED) -> dict:
    """{variante: points (cv, s*)} : ce que relit le chiffre du site."""
    analyse = analyser(depuis_json(json.loads(Path(chemin).read_text(encoding="utf-8"))),
                       bootstrap, seed)
    return {v: points_de_la_regle(analyse, v) for v in analyse}


# --- figure ----------------------------------------------------------------------------


def donnees_figure(analyse: dict) -> dict:
    """{variante: [(cv, s*, ic_bas, ic_haut, gain M USD/an, demi-largeur)]}."""
    return {v: [(ligne["cv"], ligne["s_etoile"], *ligne["ic"], ligne["gain"]["total"].mean / 1e6,
                 ligne["gain"]["total"].half_width / 1e6) for ligne in a["lignes"]]
            for v, a in analyse.items()}


def write_figure(analyse: dict, reps: int, path=DEFAULT_FIGURE) -> list[Path]:
    donnees = donnees_figure(analyse)
    chemins = []
    for langue in LANGUES:
        figure, (ax_s, ax_g) = plt.subplots(1, 2, figsize=(13, 5.4), constrained_layout=True)
        for k, variante in enumerate(VARIANTES):
            points = donnees[variante]
            decalage = (k - 1.5) * 0.006
            x = [p[0] + decalage for p in points]
            nom = (f"{libelle(variante[0], langue)}, "
                   f"{libelle('permis' if variante[1] else 'interdits', langue)}")
            couleur = fe.COULEURS[variante]
            ax_s.errorbar(x, [p[1] for p in points],
                          yerr=[[p[1] - p[2] for p in points], [p[3] - p[1] for p in points]],
                          fmt="o-", color=couleur, capsize=3, label=nom)
            ax_s.axhline(fe.S_OPTIMAL_J[variante], color=couleur, linestyle=":", linewidth=0.8)
            ax_g.errorbar(x, [p[4] for p in points], yerr=[p[5] for p in points], fmt="o-",
                          color=couleur, capsize=3, label=nom)
        ax_s.set_xlabel(libelle("cv_delai", langue))
        ax_s.set_ylabel(libelle("s_optimal", langue))
        ax_s.set_title(libelle("titre_regle", langue), fontweight="bold")
        ax_g.axhline(0, color="0.4", linewidth=1)
        ax_g.set_xlabel(libelle("cv_delai", langue))
        ax_g.set_ylabel(libelle("gain_adaptation", langue))
        ax_g.set_title(libelle("titre_gain_adaptation", langue), fontweight="bold")
        for ax in (ax_s, ax_g):
            ax.grid(True, color="0.9")
        poignees, etiquettes = ax_s.get_legend_handles_labels()
        figure.legend(poignees, etiquettes, loc="outside lower center", ncol=4, fontsize=8)
        figure.suptitle(f"{libelle('titre_adaptatif', langue)} — {reps} reps", fontsize=11)
        chemin = chemin_langue(path, langue)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, dpi=150)
        plt.close(figure)
        chemins.append(chemin)
    return chemins


# --- rapport ---------------------------------------------------------------------------


def print_report(analyse: dict) -> None:
    for variante, a in analyse.items():
        mode, dep = variante
        ordonnee, pente = a["droite"]
        print(f"\n{'=' * 112}\n{libelle(mode)}, {libelle('permis' if dep else 'interdits')} "
              f"— s fixe de l'étape 6 : {fe.S_OPTIMAL_J[variante]} j ; droite s* = "
              f"{ordonnee:.1f} + {pente:.1f}·cv\n{'=' * 112}")
        print(f"  {'cv':>5} {'s* (j)':>7} {'IC 95 %':>15} {'grille':>7} {'intérieur':>10}"
              f"{'coût à s*':>11}{'coût à s fixe':>15}{'gain (s fixe − s grille)':>24}"
              f"{'β à s*':>9}{'β à s fixe':>12}")
        for ligne in a["lignes"]:
            g = ligne["gain"]["total"]
            print(f"  {ligne['cv']:>5.2f} {ligne['s_etoile']:>7.1f} "
                  f"[{ligne['ic'][0]:>5.1f} ; {ligne['ic'][1]:>5.1f}] {ligne['s_grille']:>7} "
                  f"{'oui' if ligne['interieur'] else 'non':>10}"
                  f"{ligne['a_s_grille']['total'].mean / 1e6:>11.2f}"
                  f"{ligne['a_s_fixe']['total'].mean / 1e6:>15.2f}"
                  f"{g.mean / 1e6:>+15.2f} ± {g.half_width / 1e6:.2f}"
                  f"{'*' if significant(g) else ' '}"
                  f"{ligne['a_s_grille']['beta_appro'].mean * 100:>9.2f}"
                  f"{ligne['a_s_fixe']['beta_appro'].mean * 100:>12.2f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--depuis-json", action="store_true",
                        help="relit --json : rapport et figure, sans simuler")
    args = parser.parse_args(argv)

    if args.depuis_json:
        donnees = json.loads(args.json.read_text(encoding="utf-8"))
        resultats, reps = depuis_json(donnees), donnees["reps"]
    else:
        raw = load_yaml(ASSUMPTIONS_PATH)
        unitaires = eco.couts_unitaires(raw)
        print(f"{fe.SCENARIO}, {args.reps} réplications appariées par point, régime "
              f"stationnaire, cv ∈ {grille_cv(raw)}, {args.processus} processus.", flush=True)
        resultats, reps = {}, args.reps
        for variante in VARIANTES:
            depart = fe.S_OPTIMAL_J[variante]
            resultats[variante] = {}
            for cv in grille_cv(raw):
                evalues = chercher(raw, variante, cv, depart, args.reps, args.seed, unitaires,
                                   args.processus)
                resultats[variante][cv] = evalues
                depart = min(evalues, key=lambda s: statistics.fmean(evalues[s]["total"]))
                print(f"  {variante} cv {cv:.2f} : s évalués {list(evalues)}, minimum à "
                      f"{depart} j", flush=True)
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(vers_json(resultats, reps)), encoding="utf-8")
        print(f"mesures écrites : {args.json}")

    analyse = analyser(resultats, args.bootstrap, args.seed)
    print_report(analyse)
    for chemin in write_figure(analyse, reps, args.figure):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
