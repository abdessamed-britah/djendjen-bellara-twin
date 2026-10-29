"""Règle sous contrainte de service s_β(cv_delai), et sensibilité de s* aux prix non sourcés.

La règle de l'étape 7, s*(cv_delai), minimise un coût total dont deux prix sur trois sont sans
source : le portage du stock et le taux de surestaries. Une règle qui en dépend hérite de leur
ignorance. On lui adjoint ici une règle **qui n'en dépend pas** : le plus petit point de
commande qui tient un taux de service cible, β = 98 % et β = 99 %. Puis on mesure ce que les
prix déplacent dans s* quand chacun varie de ± 50 %.

**s_β.** Pour chaque variante et chaque cv de la grille de l'étape 7, on parcourt la grille de
s au pas de 5 jours jusqu'à encadrer la cible : s_bas, où β moyen est sous la cible, et
s_haut = s_bas + 5, où il l'atteint. s_β est l'interpolation linéaire entre les deux ; IC à
95 % par bootstrap sur les réplications. Si β n'atteint pas la cible au bout du domaine
publié (60 jours), la cible est déclarée **hors d'atteinte** dans le domaine ; s'il l'atteint
dès le bas du domaine (15 jours), s_β est publié comme « ≤ 15 ». β est le β
approvisionnement moyen de long terme : c'est un taux de service *espéré*, pas une garantie
d'une année sur l'autre. Le coût associé est interpolé de la même façon.

**Sensibilité.** Les trois termes du coût étant linéaires en leurs prix, s* se recalcule sans
simuler pour chaque combinaison (portage × 0,5 / 1 / 1,5) × (surestaries × 0,5 / 1 / 1,5) :
neuf s* par case. Quand le minimum recombiné sort de la fenêtre évaluée, la fenêtre est
étendue par simulation.

**Données.** Les mesures de l'étape 7 (`data/sim/point_de_commande_adaptatif.json`) sont
reprises telles quelles : même configuration, mêmes 40 réplications, mêmes graines, donc
identiques à ce qu'on recalculerait. Seuls les points manquants sont simulés.

Usage : python scripts/regle_service.py [--processus 12]
        python scripts/regle_service.py --depuis-json   (rapport et figure)
"""

import argparse
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
from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pca = _charger("point_de_commande_adaptatif")
fe, pc, eco = pca.fe, pca.pc, pca.eco

VARIANTES = pca.VARIANTES
CIBLES = (0.98, 0.99)
FACTEURS = (0.5, 1.0, 1.5)
TERMES = ("arrets_dri", "surestaries", "portage")
DEFAULT_BOOTSTRAP = 1000
DEFAULT_SEED = pca.DEFAULT_SEED
DEFAULT_SOURCE = pca.DEFAULT_JSON
DEFAULT_JSON = Path("data/sim/regle_service.json")
DEFAULT_FIGURE = Path("docs/regle_service.png")


# --- s_β ------------------------------------------------------------------------------


def points_service(moyennes: dict[int, float], grille: list[int], cible: float) -> list[int]:
    """Le prochain s à évaluer pour encadrer la cible ; vide quand c'est décidé.

    Décidé : deux points consécutifs de la grille encadrent la cible, ou le bas du domaine
    l'atteint déjà, ou le haut ne l'atteint pas.
    """
    evalues = sorted(moyennes)
    atteint = [s for s in evalues if moyennes[s] >= cible]
    if not atteint:
        plus_haut = evalues[-1]
        return [] if plus_haut == grille[-1] else [grille[grille.index(plus_haut) + 1]]
    premier = atteint[0]
    i = grille.index(premier)
    if i == 0:
        return []
    precedent = grille[i - 1]
    if precedent not in moyennes:
        return [precedent]
    return []


def s_beta(s_valeurs: list[int], moyennes: list[float], cible: float, pas: int) -> dict:
    """{"s": valeur ou None, "statut": encadre | sous_le_domaine | hors_d_atteinte}."""
    paires = sorted(zip(s_valeurs, moyennes, strict=True))
    for k, (s, b) in enumerate(paires):
        if b >= cible:
            if k == 0:
                return {"s": float(s), "statut": "sous_le_domaine"}
            s0, b0 = paires[k - 1]
            if s - s0 != pas:
                return {"s": float(s), "statut": "non_encadre"}
            return {"s": s0 + (cible - b0) / (b - b0) * (s - s0), "statut": "encadre"}
    return {"s": None, "statut": "hors_d_atteinte"}


def valeur_interpolee(s_valeurs: list[int], moyennes: list[float], s: float) -> float:
    """Valeur d'une grandeur en s, par interpolation linéaire entre les points évalués."""
    paires = sorted(zip(s_valeurs, moyennes, strict=True))
    for (s0, y0), (s1, y1) in pairwise(paires):
        if s0 <= s <= s1:
            return y0 + (s - s0) / (s1 - s0) * (y1 - y0)
    return dict(paires)[min(dict(paires), key=lambda x: abs(x - s))]


def bootstrap_s_beta(evalues: dict, cible: float, pas: int, tirages: int, seed: int):
    """IC à 95 % de s_β ; None si la cible n'est pas encadrée dans une partie des tirages."""
    s_valeurs = sorted(evalues)
    n = len(evalues[s_valeurs[0]]["beta_appro"])
    rng = random.Random(seed)
    valeurs = []
    for _ in range(tirages):
        k = [rng.randrange(n) for _ in range(n)]
        moyennes = [statistics.fmean(evalues[s]["beta_appro"][i] for i in k) for s in s_valeurs]
        r = s_beta(s_valeurs, moyennes, cible, pas)
        if r["s"] is not None:
            valeurs.append(r["s"])
    if len(valeurs) < 0.95 * tirages:
        return None
    valeurs.sort()
    return valeurs[int(0.025 * len(valeurs))], valeurs[int(0.975 * len(valeurs)) - 1]


# --- sensibilité aux prix -------------------------------------------------------------------


def total_recombine(valeurs: dict, facteur_portage: float, facteur_surestaries: float) -> list[float]:
    """Le coût total par réplication avec le portage et les surestaries multipliés."""
    return [a + facteur_surestaries * s + facteur_portage * p for a, s, p in
            zip(valeurs["arrets_dri"], valeurs["surestaries"], valeurs["portage"], strict=True)]


def s_etoiles(evalues: dict) -> dict[tuple[float, float], float]:
    """s* (sommet de parabole) pour chaque combinaison de facteurs de prix."""
    s_valeurs = sorted(evalues)
    sortie = {}
    for fp in FACTEURS:
        for fs in FACTEURS:
            moyennes = [statistics.fmean(total_recombine(evalues[s], fp, fs)) for s in s_valeurs]
            sortie[(fp, fs)] = pca.sommet(s_valeurs, moyennes)
    return sortie


def points_sensibilite(evalues: dict, grille: list[int]) -> list[int]:
    """Les s manquants pour que chaque minimum recombiné ait ses voisins évalués."""
    manquants = set()
    for fp in FACTEURS:
        for fs in FACTEURS:
            moyennes = {s: statistics.fmean(total_recombine(v, fp, fs)) for s, v in evalues.items()}
            manquants.update(pca.points_a_ajouter(moyennes, grille))
    return sorted(manquants)


# --- recherche -------------------------------------------------------------------------


def a_evaluer(evalues: dict, grille: list[int]) -> list[int]:
    moyennes = {s: statistics.fmean(v["beta_appro"]) for s, v in evalues.items()}
    besoins = set(points_sensibilite(evalues, grille))
    for cible in CIBLES:
        besoins.update(points_service(moyennes, grille, cible))
    return sorted(besoins - set(evalues))


def completer(resultats: dict, raw, reps: int, seed: int, processus: int) -> int:
    """Simule les points manquants, case par case ; renvoie le nombre de points ajoutés."""
    unitaires = eco.couts_unitaires(raw)
    grille = pca.grille_s(raw)
    ajoutes = 0
    for variante, par_cv in resultats.items():
        for cv, evalues in par_cv.items():
            while besoins := a_evaluer(evalues, grille):
                mesures = pc.mesurer_plusieurs(
                    [pca.configuration(raw, *variante, cv, s) for s in besoins], reps, seed,
                    unitaires, fe.SCENARIO, processus=processus)
                for s, m in zip(besoins, mesures, strict=True):
                    evalues[s] = {c: pc.serie(m, c) for c in pca.CLES}
                ajoutes += len(besoins)
                print(f"  {variante} cv {cv:.2f} : + s {besoins}", flush=True)
            par_cv[cv] = dict(sorted(evalues.items()))
    return ajoutes


# --- analyse -----------------------------------------------------------------------------


def analyser(resultats: dict, pas: int, tirages: int, seed: int) -> dict:
    sortie = {}
    for variante, par_cv in resultats.items():
        lignes = []
        for cv, evalues in par_cv.items():
            s_valeurs = sorted(evalues)
            beta = [statistics.fmean(evalues[s]["beta_appro"]) for s in s_valeurs]
            total = [statistics.fmean(evalues[s]["total"]) for s in s_valeurs]
            etoiles = s_etoiles(evalues)
            s_star = etoiles[(1.0, 1.0)]
            ligne = {"cv": cv, "s_etoile": s_star,
                     "cout_s_etoile": valeur_interpolee(s_valeurs, total, s_star),
                     "beta_s_etoile": valeur_interpolee(s_valeurs, beta, s_star),
                     "sensibilite": etoiles, "service": {}}
            for cible in CIBLES:
                r = s_beta(s_valeurs, beta, cible, pas)
                if r["s"] is not None:
                    r["cout"] = valeur_interpolee(s_valeurs, total, r["s"])
                r["ic"] = (bootstrap_s_beta(evalues, cible, pas, tirages, seed)
                           if r["statut"] == "encadre" else None)
                ligne["service"][cible] = r
            lignes.append(ligne)
        sortie[variante] = lignes
    return sortie


# --- sauvegarde -------------------------------------------------------------------------


def lire(chemin) -> tuple[dict, int]:
    donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return pca.depuis_json(donnees), donnees["reps"]


# --- figure ----------------------------------------------------------------------------


def donnees_figure(analyse: dict) -> dict:
    """{variante: [(cv, s*, s* min, s* max, s_98, s_99)]} ; None quand hors d'atteinte."""
    return {v: [(li["cv"], li["s_etoile"], min(li["sensibilite"].values()),
                 max(li["sensibilite"].values()),
                 *(li["service"][c]["s"] for c in CIBLES)) for li in lignes]
            for v, lignes in analyse.items()}


def write_figure(analyse: dict, reps: int, path=DEFAULT_FIGURE) -> list[Path]:
    donnees = donnees_figure(analyse)
    styles = {CIBLES[0]: "o-", CIBLES[1]: "s--"}
    chemins = []
    for langue in LANGUES:
        figure, (ax_b, ax_s) = plt.subplots(1, 2, figsize=(14, 6.2), sharey=True,
                                            constrained_layout=True)
        for k, variante in enumerate(VARIANTES):
            couleur = fe.COULEURS[variante]
            nom = (f"{libelle(variante[0], langue)}, "
                   f"{libelle('permis' if variante[1] else 'interdits', langue)}")
            points = donnees[variante]
            for j, cible in enumerate(CIBLES):
                xy = [(p[0], p[4 + j]) for p in points if p[4 + j] is not None]
                if xy:
                    ax_b.plot(*zip(*xy, strict=True), styles[cible], color=couleur,
                              label=f"{nom}, {libelle('beta_cible', langue)} {cible:.0%}")
            decalage = (k - 1.5) * 0.006
            x = [p[0] + decalage for p in points]
            ax_s.errorbar(x, [p[1] for p in points],
                          yerr=[[p[1] - p[2] for p in points], [p[3] - p[1] for p in points]],
                          fmt="o-", color=couleur, capsize=3, label=nom)
        ax_b.set_title(libelle("titre_service", langue), fontweight="bold")
        ax_b.set_ylabel(libelle("s_service", langue))
        ax_s.set_title(libelle("titre_sensibilite_couts", langue), fontweight="bold")
        for ax in (ax_b, ax_s):
            ax.set_xlabel(libelle("cv_delai", langue))
            ax.grid(True, color="0.9")
        poignees, noms = ax_b.get_legend_handles_labels()
        figure.legend(poignees, noms, loc="outside lower center", ncol=4, fontsize=7)
        figure.suptitle(f"{libelle('titre_regle_service', langue)} — {reps} reps", fontsize=11)
        chemin = chemin_langue(path, langue)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, dpi=150)
        plt.close(figure)
        chemins.append(chemin)
    return chemins


# --- rapport ---------------------------------------------------------------------------


def _s(r) -> str:
    if r["statut"] == "hors_d_atteinte":
        return "> 60 (hors domaine)"
    if r["statut"] == "sous_le_domaine":
        return "≤ 15"
    ic = f" [{r['ic'][0]:.1f} ; {r['ic'][1]:.1f}]" if r.get("ic") else " (IC instable)"
    return f"{r['s']:.1f}{ic}"


def print_report(analyse: dict) -> None:
    for variante, lignes in analyse.items():
        print(f"\n{'=' * 118}\n{libelle(variante[0])}, "
              f"{libelle('permis' if variante[1] else 'interdits')}\n{'=' * 118}")
        print(f"  {'cv':>5} {'s* (coût)':>10} {'β à s*':>8} {'coût à s*':>10}  "
              f"{'s_98 (j)':>24} {'coût':>6}  {'s_99 (j)':>24} {'coût':>6}  "
              f"{'s* prix ± 50 % : min – max':>28}  portage seul / surestaries seules")
        for li in lignes:
            sens = li["sensibilite"]
            cases = []
            for cible in CIBLES:
                r = li["service"][cible]
                cout = f"{r['cout'] / 1e6:6.2f}" if r.get("cout") is not None else "     —"
                cases.append(f"{_s(r):>24} {cout}")
            print(f"  {li['cv']:>5.2f} {li['s_etoile']:>10.1f} {li['beta_s_etoile'] * 100:>8.2f} "
                  f"{li['cout_s_etoile'] / 1e6:>10.2f}  " + "  ".join(cases)
                  + f"  {min(sens.values()):>13.1f} – {max(sens.values()):<12.1f}"
                  + f"  {sens[(0.5, 1.0)]:.1f}–{sens[(1.5, 1.0)]:.1f} / "
                  + f"{sens[(1.0, 0.5)]:.1f}–{sens[(1.0, 1.5)]:.1f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--depuis-json", action="store_true")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    if args.depuis_json:
        resultats, reps = lire(args.json)
    else:
        resultats, reps = lire(args.source)
        ajoutes = completer(resultats, raw, reps, args.seed, args.processus)
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(pca.vers_json(resultats, reps)), encoding="utf-8")
        print(f"{ajoutes} points simulés en plus de l'étape 7 ; mesures écrites : {args.json}")
    analyse = analyser(resultats, pca.PAS_S_J, args.bootstrap, args.seed)
    print_report(analyse)
    for chemin in write_figure(analyse, reps, args.figure):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
