"""Politique de commande « consciente des arrêts » : trois politiques, variantes discrètes.

En mode discret, le four s'arrête par intermittence. La politique actuelle ne voit la
position de stock baisser qu'aux consommations : pendant un arrêt, elle se fige et l'on cesse
de commander. À la reprise, le four consomme à pleine cadence. L'étape 5 a mesuré ce que cela
coûte (ruptures doublées ou triplées par rapport au mode continu). On compare ici, en données
appariées (`simulation.politique_arrets`) :

- (a) **aveugle** — l'actuelle ;
- (b) **rythme moyen** — pendant un arrêt, une consommation fictive au rythme moyen fait
  baisser la position vue par la politique, qui continue de commander ; effacée à la reprise ;
- (c) **date de reprise** — la durée restante de l'arrêt est connue : la politique compare la
  position à la consommation qui viendra vraiment dans le délai moyen, pleine cadence ×
  (délai − reste)⁺. Borne haute de l'information.

Les arrêts concernés sont **tous** ceux du four, non planifiés (le mode discret) et planifiés
(maintenance annuelle) : la politique ne distingue pas leur origine.

**Mesures.** β approvisionnement, heures de rupture, surestaries, coût total et ses termes,
stock physique moyen, en phase 2 endogène, régime stationnaire, aux deux variantes discrètes,
au point de commande de l'étape 6 (35 j), 200 réplications appariées. La **part des ruptures
dues aux arrêts** qu'une politique récupère vaut (R_a − R_x) / (R_a − R_continu), où R_continu
est le même corridor en consommation continue au même point de commande : ce que la
politique aveugle perd *du fait* des arrêts. IC par bootstrap sur les réplications.

**L'hypothèse à tester, et la règle écrite avant le calcul.** « (b) récupère l'essentiel de
la perte à coût quasi nul. » Elle est **confirmée** dans une variante si (1) la part des
ruptures récupérée par (b) est d'au moins 50 %, borne basse de son IC comprise, **et** (2) le
coût total de (b) n'est pas significativement plus élevé que celui de (a). Elle est
**infirmée** si l'une des deux conditions échoue, et le script dit laquelle. Le point de
commande n'est pas réoptimisé par politique : on compare des politiques au même s.

Usage : python scripts/politique_arrets.py [--reps 200] [--processus 12]
"""

import argparse
import dataclasses
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.labels import LANGUES, chemin_langue, libelle
from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
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

VARIANTES = (("discret", True), ("discret", False))
POLITIQUES = ("aveugle", "rythme_moyen", "date_de_reprise")
#: Grandeurs publiées : clé, échelle d'affichage, unité.
INDICATEURS = (
    ("beta_appro", 100.0, "points de %"),
    ("rupture_h_an", 1.0, "h/an"),
    ("attente_h", 1.0, "h"),
    ("stock_t", 1e-3, "kt"),
    ("arrets_dri", 1e-6, "M USD/an"),
    ("surestaries", 1e-6, "M USD/an"),
    ("portage", 1e-6, "M USD/an"),
    ("total", 1e-6, "M USD/an"),
)
#: La règle de l'hypothèse, écrite avant le calcul.
SEUIL_ESSENTIEL = 0.50
DEFAULT_REPS = 200
DEFAULT_BOOTSTRAP = 2000
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/politique_arrets.png")
DEFAULT_JSON = Path("data/sim/politique_arrets.json")


def configuration(raw, mode: str, depassements: bool, politique: str, s_j: int):
    base = fe.configuration(raw, mode, depassements, postes=1, stockage="base")
    return dataclasses.replace(base, politique_arrets=politique, point_de_commande_j=float(s_j),
                               point_de_commande_t=s_j * base.debit_pellets_t_par_h * 24)


def part_recuperee(aveugle: list[float], politique: list[float], continu: list[float]) -> float:
    """(R_a − R_x) / (R_a − R_continu), sur les moyennes ; nan si les arrêts ne coûtent rien."""
    perte = statistics.fmean(aveugle) - statistics.fmean(continu)
    if perte <= 0:
        return float("nan")
    return (statistics.fmean(aveugle) - statistics.fmean(politique)) / perte


def bootstrap_part(aveugle, politique, continu, tirages: int, seed: int) -> tuple[float, float]:
    """IC à 95 % de la part récupérée, en rééchantillonnant les réplications appariées."""
    rng = random.Random(seed)
    n = len(aveugle)
    parts = []
    for _ in range(tirages):
        k = [rng.randrange(n) for _ in range(n)]
        parts.append(part_recuperee([aveugle[i] for i in k], [politique[i] for i in k],
                                    [continu[i] for i in k]))
    parts = sorted(p for p in parts if not math.isnan(p))
    return parts[int(0.025 * len(parts))], parts[int(0.975 * len(parts)) - 1]


def verdict(part: float, ic_bas: float, difference_cout) -> tuple[bool, str]:
    """La règle écrite avant le calcul : (confirmée ?, raison)."""
    raisons = []
    if not (part >= SEUIL_ESSENTIEL and ic_bas >= SEUIL_ESSENTIEL):
        raisons.append(f"part récupérée {part:.0%} (IC bas {ic_bas:.0%}) sous "
                       f"{SEUIL_ESSENTIEL:.0%}")
    if significant(difference_cout) and difference_cout.mean > 0:
        raisons.append(f"coût total plus élevé de {difference_cout.mean / 1e6:+.2f} "
                       f"± {difference_cout.half_width / 1e6:.2f} M USD/an")
    return (not raisons, "; ".join(raisons) or "les deux conditions tiennent")


def analyser(mesures: dict, bootstrap: int, seed: int) -> dict:
    """{variante: {politique: {estimations, différences à (a), part récupérée, IC}}}."""
    sortie = {}
    for variante, par_politique in mesures.items():
        continu = par_politique["continu"]
        aveugle = par_politique["aveugle"]
        sortie[variante] = {"continu": {c: estimate(continu[c]) for c, _, _ in INDICATEURS}}
        for politique in POLITIQUES:
            serie = par_politique[politique]
            ligne = {"estimations": {c: estimate(serie[c]) for c, _, _ in INDICATEURS},
                     "difference": {c: paired_difference(serie[c], aveugle[c])
                                    for c, _, _ in INDICATEURS}}
            if politique != "aveugle":
                ligne["part"] = part_recuperee(aveugle["rupture_h_an"], serie["rupture_h_an"],
                                               continu["rupture_h_an"])
                ligne["ic_part"] = bootstrap_part(aveugle["rupture_h_an"], serie["rupture_h_an"],
                                                  continu["rupture_h_an"], bootstrap, seed)
            sortie[variante][politique] = ligne
        b = sortie[variante]["rythme_moyen"]
        sortie[variante]["verdict"] = verdict(b["part"], b["ic_part"][0],
                                              b["difference"]["total"])
    return sortie


# --- sauvegarde ------------------------------------------------------------------------


def vers_json(mesures: dict, reps: int) -> dict:
    return {"reps": reps, "mesures": [
        {"mode": v[0], "depassements": v[1], "politique": p, **series}
        for v, par_politique in mesures.items() for p, series in par_politique.items()]}


def depuis_json(donnees: dict) -> dict:
    mesures: dict = {}
    for ligne in donnees["mesures"]:
        variante = (ligne["mode"], ligne["depassements"])
        mesures.setdefault(variante, {})[ligne["politique"]] = {
            c: ligne[c] for c, _, _ in INDICATEURS}
    return mesures


# --- figure ----------------------------------------------------------------------------


def donnees_figure(analyse: dict) -> dict:
    """{variante: [(politique, part, ic_bas, ic_haut, Δstock kt, ±, Δcoût M USD, ±)]}."""
    sortie = {}
    for variante, a in analyse.items():
        sortie[variante] = []
        for politique in POLITIQUES[1:]:
            ligne = a[politique]
            ds, dc = ligne["difference"]["stock_t"], ligne["difference"]["total"]
            sortie[variante].append((politique, ligne["part"] * 100, ligne["ic_part"][0] * 100,
                                     ligne["ic_part"][1] * 100, ds.mean / 1e3,
                                     ds.half_width / 1e3, dc.mean / 1e6, dc.half_width / 1e6))
    return sortie


def write_figure(analyse: dict, reps: int, path=DEFAULT_FIGURE) -> list[Path]:
    donnees = donnees_figure(analyse)
    couleurs = {"rythme_moyen": "#2ca02c", "date_de_reprise": "#9467bd"}
    chemins = []
    for langue in LANGUES:
        figure, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
        variantes = list(donnees)
        for k, politique in enumerate(POLITIQUES[1:]):
            x = [i + (k - 0.5) * 0.35 for i in range(len(variantes))]
            lignes = [donnees[v][k] for v in variantes]
            nom = libelle(politique, langue)
            axes[0].bar(x, [li[1] for li in lignes], width=0.33, color=couleurs[politique],
                        yerr=[[li[1] - li[2] for li in lignes], [li[3] - li[1] for li in lignes]],
                        capsize=4, label=nom)
            axes[1].bar(x, [li[4] for li in lignes], width=0.33, color=couleurs[politique],
                        yerr=[li[5] for li in lignes], capsize=4)
            axes[2].bar(x, [li[6] for li in lignes], width=0.33, color=couleurs[politique],
                        yerr=[li[7] for li in lignes], capsize=4)
        etiquettes = [libelle("permis" if v[1] else "interdits", langue) for v in variantes]
        for ax, cle in zip(axes, ("part_recuperee", "delta_stock", "delta_cout"), strict=True):
            ax.set_xticks(range(len(variantes)), etiquettes)
            ax.axhline(0, color="0.4", linewidth=1)
            ax.set_title(libelle(cle, langue), fontweight="bold")
            ax.grid(True, axis="y", color="0.9")
        axes[0].axhline(SEUIL_ESSENTIEL * 100, color="0.3", linestyle="--", linewidth=1)
        poignees, noms = axes[0].get_legend_handles_labels()
        figure.legend(poignees, noms, loc="outside lower center", ncol=2, fontsize=9)
        figure.suptitle(f"{libelle('titre_politique_arrets', langue)} — {reps} reps",
                        fontsize=11)
        chemin = chemin_langue(path, langue)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, dpi=150)
        plt.close(figure)
        chemins.append(chemin)
    return chemins


# --- rapport ---------------------------------------------------------------------------


def print_report(analyse: dict) -> None:
    for variante, a in analyse.items():
        print(f"\n{'=' * 118}\n{libelle(variante[0])}, "
              f"{libelle('permis' if variante[1] else 'interdits')}, s = "
              f"{fe.S_OPTIMAL_J[variante]} j\n{'=' * 118}")
        print(f"  {'politique':22}" + "".join(f"{c[:15]:>18}" for c, _, _ in INDICATEURS))
        print(f"  {'(continu, référence)':22}" + "".join(
            f"{a['continu'][c].mean * e:>18.3f}" for c, e, _ in INDICATEURS))
        for politique in POLITIQUES:
            print(f"  {politique:22}" + "".join(
                f"{a[politique]['estimations'][c].mean * e:>18.3f}" for c, e, _ in INDICATEURS))
        print("\n  écart à (a), apparié — β en points de pourcentage, stock en kt")
        for politique in POLITIQUES[1:]:
            d = a[politique]["difference"]
            print(f"  {politique:22}" + "".join(
                f"{d[c].mean * e:+.3f}±{d[c].half_width * e:.3f}"
                f"{'*' if significant(d[c]) else ' '}".rjust(18) for c, e, _ in INDICATEURS))
            bas, haut = a[politique]["ic_part"]
            print(f"  {'':22} part des ruptures dues aux arrêts récupérée : "
                  f"{a[politique]['part']:.0%} [{bas:.0%} ; {haut:.0%}]")
        confirme, raison = a["verdict"]
        print(f"\n  hypothèse « (b) récupère l'essentiel à coût quasi nul » : "
              f"{'CONFIRMÉE' if confirme else 'INFIRMÉE'} — {raison}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--processus", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--depuis-json", action="store_true")
    args = parser.parse_args(argv)

    if args.depuis_json:
        donnees = json.loads(args.json.read_text(encoding="utf-8"))
        mesures, reps = depuis_json(donnees), donnees["reps"]
    else:
        raw = load_yaml(ASSUMPTIONS_PATH)
        unitaires = eco.couts_unitaires(raw)
        mesures, reps = {}, args.reps
        for variante in VARIANTES:
            s_j = fe.S_OPTIMAL_J[variante]
            noms = ("continu", *POLITIQUES)
            configs = [configuration(raw, "continu", variante[1], "aveugle", s_j)] + [
                configuration(raw, *variante, p, s_j) for p in POLITIQUES]
            resultats = pc.mesurer_plusieurs(configs, args.reps, args.seed, unitaires,
                                             fe.SCENARIO, processus=args.processus)
            mesures[variante] = {nom: {c: pc.serie(m, c) for c, _, _ in INDICATEURS}
                                 for nom, m in zip(noms, resultats, strict=True)}
            print(f"  {variante} : mesuré", flush=True)
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(vers_json(mesures, reps)), encoding="utf-8")
        print(f"mesures écrites : {args.json}")

    analyse = analyser(mesures, args.bootstrap, args.seed)
    print_report(analyse)
    for chemin in write_figure(analyse, reps, args.figure):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
