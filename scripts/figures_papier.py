"""Les figures du papier de conférence, en anglais et en PDF vectoriel, sans simuler.

Tout vient des résultats déjà calculés et sauvegardés par leurs scripts :

- `paper/figures/thesis.pdf` : β selon l'horizon, modèle exogène contre modèle endogène, lu
  dans `data/sim/convergence_horizon_baseline.json` et
  `data/sim/convergence_horizon_baseline_endogene.json` (`scripts/convergence_horizon.py`) ;
- `paper/figures/factorial.pdf` : le plan factoriel poste × stockage sur le modèle corrigé,
  lu dans `data/sim/factoriel_endogene.json` (`scripts/factoriel_endogene.py`) ;
- `paper/figures/validation.pdf` : la validation des arrêts discrets à cv_delai central,
  lue dans `data/sim/validation_arrets.json` (`scripts/validation_arrets.py`), avec la
  valeur observée recalculée depuis les bilans de trafic archivés.

Les données de chaque figure passent par la fonction `donnees_figure` du script qui les a
produites : la figure du papier ne peut pas montrer autre chose que celle du README. Format
IEEE : pleine largeur 7,16 pouces, une colonne 3,5 pouces, polices TrueType incorporées
(les polices Type 3 sont refusées par les contrôles de soumission). La figure de structure
du modèle n'est pas ici : elle est en TikZ, dans `paper/figures/structure.tex`.

Usage : python scripts/figures_papier.py [--sortie paper/figures]
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

from corridor.labels import libelle
from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


fe = _charger("factoriel_endogene")
va = _charger("validation_arrets")
ch = _charger("convergence_horizon")

#: Les deux scénarios de la figure de la thèse : le même corridor, sans puis avec boucle.
SCENARIO_EXOGENE = "baseline"
SCENARIO_ENDOGENE = "baseline_endogene"

LANGUE = "en"
DEFAULT_SORTIE = Path("paper/figures")
LARGEUR_PAGE_POUCES = 7.16
LARGEUR_COLONNE_POUCES = 3.5
STYLE = {"font.size": 7.5, "axes.titlesize": 8, "axes.labelsize": 7.5,
         "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 6.5,
         "pdf.fonttype": 42, "ps.fonttype": 42}


def cv_central(raw=None) -> float:
    """cv_delai de la figure de validation : la valeur centrale de `assumptions.yaml`."""
    raw = raw or load_yaml(ASSUMPTIONS_PATH)
    return float(raw["approvisionnement"]["cv_delai"]["value"])


def donnees_validation(carte: dict, observes: dict, cv: float | None = None) -> dict:
    """Les bandes de la seule valeur `cv`, dépassements permis, et la valeur observée."""
    cv = cv_central() if cv is None else cv
    donnees = va.donnees_figure(carte, observes)
    series = {stockage: s for (c, dep, stockage), s in donnees["series"].items()
              if dep and abs(c - cv) < 1e-9}
    if not series:
        raise ValueError(f"cv_delai = {cv} absent de la carte")
    return {"series": series, "observe": donnees["observe"]}


def donnees_these(brut_exogene: dict, brut_endogene: dict) -> dict:
    """β(T) avec IC 95 % des deux modèles, et β∞ par moyennes par lots de l'endogène."""
    sortie = {}
    for cle, brut in (("exogene", brut_exogene), ("endogene", brut_endogene)):
        lignes = ch.analyser(brut)["lignes"]
        sortie[cle] = {"horizons": [ligne["annees"] for ligne in lignes],
                       "beta": [ligne["beta"].mean for ligne in lignes],
                       "bas": [ligne["beta"].low for ligne in lignes],
                       "haut": [ligne["beta"].high for ligne in lignes]}
    if sortie["exogene"]["horizons"] != sortie["endogene"]["horizons"]:
        raise ValueError("les deux modèles doivent être lus aux mêmes horizons")
    lots = brut_endogene.get("lots")
    if lots:
        limite = ch.estimate(lots["betas"])
        sortie["limite"] = (limite.mean, limite.low, limite.high)
    return sortie


def figure_these(brut_exogene: dict, brut_endogene: dict, chemin: Path) -> Path:
    donnees = donnees_these(brut_exogene, brut_endogene)
    styles = {"exogene": ("#d62728", "o"), "endogene": ("#1f77b4", "s")}
    with plt.rc_context(STYLE):
        figure, ax = plt.subplots(figsize=(LARGEUR_COLONNE_POUCES, 2.45),
                                  constrained_layout=True)
        if "limite" in donnees:
            moyenne, bas, haut = donnees["limite"]
            ax.axhspan(bas, haut, color="#1f77b4", alpha=0.15, linewidth=0)
            ax.axhline(moyenne, color="#1f77b4", linewidth=0.6, linestyle=":",
                       label=libelle("beta_lots", LANGUE))
        for cle in ("exogene", "endogene"):
            d = donnees[cle]
            couleur, marque = styles[cle]
            ax.fill_between(d["horizons"], d["bas"], d["haut"], color=couleur, alpha=0.18,
                            linewidth=0)
            ax.plot(d["horizons"], d["beta"], color=couleur, marker=marque, markersize=3,
                    linewidth=1.2, label=libelle(f"modele_{cle}", LANGUE))
        ax.set_xscale("log")
        ax.set_xticks(donnees["exogene"]["horizons"],
                      [str(h) for h in donnees["exogene"]["horizons"]])
        ax.minorticks_off()
        ax.set_xlabel(libelle("horizon_log", LANGUE))
        ax.set_ylabel(libelle("beta_service", LANGUE))
        ax.grid(True, color="0.9", linewidth=0.5)
        ax.legend(frameon=False, loc="lower right")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, metadata={"CreationDate": None})
        plt.close(figure)
    return chemin


def figure_factorielle(par_variante: dict, chemin: Path) -> Path:
    donnees = fe.donnees_figure(par_variante)
    noms = [n for n, _, _ in fe.EFFETS] + ["interaction"]
    with plt.rc_context(STYLE):
        figure, axes = plt.subplots(1, len(fe.PANNEAUX), figsize=(LARGEUR_PAGE_POUCES, 2.5),
                                    sharey=True, constrained_layout=True)
        for ax, cle in zip(axes, fe.PANNEAUX, strict=True):
            for k, variante in enumerate(fe.VARIANTES):
                decalage = (k - 1.5) * 0.17
                for j, (_, moyenne, demi) in enumerate(donnees[cle][variante]):
                    ax.errorbar(moyenne, j + decalage, xerr=demi, fmt="o", markersize=2.5,
                                elinewidth=0.8, capsize=1.5, color=fe.COULEURS[variante],
                                label=(f"{libelle(variante[0], LANGUE)}, "
                                       f"{libelle('permis' if variante[1] else 'interdits', LANGUE)}"
                                       if j == 0 else None))
            ax.axvline(0, color="0.4", linewidth=0.8)
            ax.set_xlabel(libelle(cle, LANGUE))
            ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.grid(True, color="0.9", linewidth=0.5)
        axes[0].set_yticks(range(len(noms)), [libelle(n, LANGUE) for n in noms])
        axes[0].invert_yaxis()
        poignees, etiquettes = axes[0].get_legend_handles_labels()
        figure.legend(poignees, etiquettes, loc="outside lower center", ncol=2, frameon=False)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, metadata={"CreationDate": None})
        plt.close(figure)
    return chemin


def figure_validation(carte: dict, observes: dict, chemin: Path) -> Path:
    donnees = donnees_validation(carte, observes)
    couleurs = {"bas": "#d62728", "centre": "#1f77b4", "haut": "#2ca02c"}
    with plt.rc_context(STYLE):
        figure, ax = plt.subplots(figsize=(LARGEUR_COLONNE_POUCES, 2.6),
                                  constrained_layout=True)
        for stockage in va.NIVEAUX_STOCKAGE:
            d, bas, med, haut = donnees["series"][stockage]
            niveau = libelle(f"niveau_{stockage}", LANGUE)
            ax.fill_between(d, bas, haut, color=couleurs[stockage], alpha=0.12, linewidth=0)
            ax.plot(d, med, color=couleurs[stockage], marker="o", markersize=2.5,
                    linewidth=1.0, label=libelle("stockage_niveau", LANGUE).format(niveau=niveau))
        ax.axhline(donnees["observe"], color="0.2", linestyle="--", linewidth=1.0,
                   label=libelle("observe_periode", LANGUE).format(
                       periode=va.PERIODE_DE_REFERENCE))
        ax.set_xlabel(libelle("duree_arret_j", LANGUE))
        ax.set_ylabel(libelle("cv2_intra", LANGUE))
        ax.set_yscale("log")
        ax.grid(True, which="both", color="0.9", linewidth=0.5)
        figure.legend(frameon=False, loc="outside upper center", ncol=2)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(chemin, metadata={"CreationDate": None})
        plt.close(figure)
    return chemin


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sortie", type=Path, default=DEFAULT_SORTIE)
    parser.add_argument("--factoriel", type=Path, default=fe.DEFAULT_JSON)
    parser.add_argument("--validation", type=Path, default=va.DEFAULT_JSON)
    parser.add_argument("--exogene", type=Path, default=ch.chemin_json(SCENARIO_EXOGENE))
    parser.add_argument("--endogene", type=Path, default=ch.chemin_json(SCENARIO_ENDOGENE))
    args = parser.parse_args(argv)

    exogene = json.loads(args.exogene.read_text(encoding="utf-8"))
    endogene = json.loads(args.endogene.read_text(encoding="utf-8"))
    par_variante = fe.depuis_json(json.loads(args.factoriel.read_text(encoding="utf-8")))
    carte = va.depuis_json(json.loads(args.validation.read_text(encoding="utf-8")))
    observes = {p: va.observe(bornes) for p, bornes in va.PERIODES.items()}
    for chemin in (figure_these(exogene, endogene, args.sortie / "thesis.pdf"),
                   figure_factorielle(par_variante, args.sortie / "factorial.pdf"),
                   figure_validation(carte, observes, args.sortie / "validation.pdf")):
        print(f"figure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
