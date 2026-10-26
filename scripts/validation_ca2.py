"""Validation du modèle endogène par la dispersion des arrivées, face aux bilans du port.

**Pourquoi c'est une validation et pas un réglage.** Dans le modèle endogène, l'irrégularité
des arrivées n'est plus une entrée : elle résulte de la politique de commande et de la
variabilité du délai d'approvisionnement. Si la dispersion qu'il produit tombe là où les
bilans de trafic du port la placent, sans qu'aucun paramètre ait été réglé pour cela, c'est
un argument indépendant en faveur du modèle. Si elle n'y tombe pas, c'est un défaut à décrire
— pas à corriger ici.

**Les règles, écrites avant le premier calcul et figées dans les constantes ci-dessous.**

1. *Statistique jugée.* L'indice de dispersion des comptages **trimestriels** de
   minéraliers, I = Var(N_j)/E[N_j], calculé exactement comme sur les données du port :
   N_j = tonnage **déchargé** dans le trimestre ÷ taille moyenne d'un minéralier, et
   `arrival_dispersion.index_of_dispersion` (variance d'échantillon) pour I. Le CV² des
   intervalles entre arrivées est publié à côté et ne sert pas au verdict.
2. *Comparer à 16 trimestres, pas à l'infini.* L'observé est calculé sur 16 trimestres ; il
   est jugé contre la distribution de I sur des **fenêtres de 16 trimestres simulés**, pas
   contre une moyenne de long terme. Les fenêtres glissent d'un trimestre : elles sont
   corrélées entre elles, mais estiment sans biais la distribution ; le nombre de fenêtres
   **disjointes** est publié pour dire combien d'information indépendante elle contient.
3. *Bandes.* `BANDE_LARGE` est l'IC bootstrap à 95 % de l'observé ; `BANDE_ETROITE` va de la
   variante sans le trimestre anormal 2022 T3 (0,45) à celle qui le garde (0,83).
4. *Verdict d'une bande.* Le modèle est **compatible** avec une bande (b, h) si au moins
   `SEUIL_PLAUSIBLE` (5 %) des fenêtres simulées donnent I ≥ b **et** au moins 5 % donnent
   I ≤ h — autrement dit, si l'intervalle à 90 % de I simulé rencontre la bande. Sinon il est
   **trop régulier** (presque toutes les fenêtres sous la bande) ou **trop irrégulier**.
   La bande large est donc un test indulgent, la bande étroite un test exigeant.
5. *Probabilités de dépassement.* P(I ≥ 0,83) et P(I ≥ 0,45) sur les fenêtres de 16
   trimestres : la probabilité qu'un corridor conforme au modèle produise, sur la même durée
   d'observation, une dispersion au moins aussi forte que celle mesurée.
6. *Ce qui peut être calibré : s seul*, et sur un critère indépendant de la dispersion — le
   point de commande optimal en coût de l'étape 2 (`POINT_DE_COMMANDE_J`). `cv_delai` reste à
   sa valeur d'hypothèse : le script **refuse de tourner** si elle a changé dans
   `assumptions.yaml` depuis l'écriture de ces règles, ou si un scénario la modifie.
7. *Balayage de cv_delai.* La fourchette entière (0,10 à 0,60) est parcourue, mais **comme
   une carte de sensibilité, pas comme un réglage** : le verdict principal reste celui de la
   valeur centrale, et aucune valeur du balayage n'est reportée dans le modèle.

**Contrôles de regroupement**, sans valeur de verdict : la part des intervalles entre arrivées
de moins de 2 jours, et la part des attentes en rade de plus de 150 h, chacune avec la
probabilité d'observer ce que la série a montré. Une seule observation ne tranche rien.

Usage : python scripts/validation_ca2.py [--graines 8] [--annees 100]
"""

import argparse
import dataclasses
import importlib.util
import math
import random
import statistics
import sys
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.model import chronique
from corridor.sim.run import ECHAUFFEMENT_STATIONNAIRE_ANS

# --- règles pré-enregistrées ----------------------------------------------------
#: IC bootstrap à 95 % de I observé sur 16 trimestres (scripts/arrival_dispersion.py).
BANDE_LARGE = (0.21, 1.73)
#: I observé sans le trimestre anormal 2022 T3, puis avec : les deux variantes publiées.
BANDE_ETROITE = (0.45, 0.83)
TRIMESTRES_PAR_FENETRE = 16
TRIMESTRES_PAR_AN = 4
#: Part minimale des fenêtres simulées de chaque côté de la bande pour la dire compatible.
SEUIL_PLAUSIBLE = 0.05
#: Valeur de `approvisionnement.cv_delai` au moment où ces règles ont été écrites. Si
#: l'hypothèse change, la validation n'est plus pré-enregistrée : il faut relire les règles
#: et mettre cette constante à jour sciemment, pas laisser le script suivre en silence.
CV_DELAI_PREENREGISTRE = 0.25
#: Point de commande retenu, en jours : l'optimum en coût de l'étape 2
#: (scripts/politique_commande.py, 2026-10-26), critère indépendant de la dispersion.
POINT_DE_COMMANDE_J = {
    ("baseline_endogene", True): 30,
    ("baseline_endogene", False): 30,
    ("phase2_endogene", True): 30,
    ("phase2_endogene", False): 35,
}
SCENARIOS = ("baseline_endogene", "phase2_endogene")
HYPOTHESES = ((True, "dépassements permis"), (False, "dépassements interdits"))
#: Carte de sensibilité : toute la fourchette de l'hypothèse.
CV_DELAI_BALAYAGE = (0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60)
#: Robustesse au seul paramètre calibré : s autour de son optimum, à cv_delai central.
ECARTS_S_J = (-5, 0, 5)
#: Contrôles de regroupement.
INTERVALLE_COURT_H = 48.0
ATTENTE_LONGUE_H = 150.0
DEFAULT_GRAINES = 8
DEFAULT_GRAINES_BALAYAGE = 4
DEFAULT_ANNEES = 100.0
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/validation_ca2.png")


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


disp = _charger("arrival_dispersion")


# --- garde-fous ---------------------------------------------------------------


class ReglesViolees(RuntimeError):
    """Les conditions pré-enregistrées de la validation ne sont plus réunies."""


def verifier_preenregistrement(raw, configs=()) -> None:
    """Refuse de valider si `cv_delai` a bougé depuis l'écriture des règles."""
    cv_yaml = float(raw["approvisionnement"]["cv_delai"]["value"])
    if not math.isclose(cv_yaml, CV_DELAI_PREENREGISTRE):
        raise ReglesViolees(
            f"cv_delai vaut {cv_yaml} dans assumptions.yaml, les règles ont été écrites pour "
            f"{CV_DELAI_PREENREGISTRE} : relire les règles avant de mettre la constante à jour")
    for config in configs:
        if not math.isclose(config.cv_delai_approvisionnement, cv_yaml):
            raise ReglesViolees("un scénario modifie cv_delai : la validation l'interdit")
    bas, haut = raw["approvisionnement"]["point_de_commande_j"]["range"]
    for cle, s_j in POINT_DE_COMMANDE_J.items():
        if not bas <= s_j <= haut:
            raise ReglesViolees(f"point de commande {s_j} j hors de sa fourchette pour {cle}")


# --- la statistique, calculée comme sur les données du port -------------------


def comptages_trimestriels(periodes, taille_t: float, echauffement_h: float) -> list[float]:
    """N_j = tonnage déchargé dans le trimestre ÷ taille moyenne, après l'échauffement."""
    return [p["debarque_t"] / taille_t for p in periodes if p["debut_h"] >= echauffement_h]


def indices_par_fenetre(comptages, taille=TRIMESTRES_PAR_FENETRE, pas=1) -> list[float]:
    """I sur chaque fenêtre de `taille` trimestres consécutifs, glissant de `pas`."""
    return [disp.index_of_dispersion(comptages[i:i + taille])
            for i in range(0, len(comptages) - taille + 1, pas)]


def quantile(valeurs, q: float) -> float:
    ordonnees = sorted(valeurs)
    if not ordonnees:
        return math.nan
    position = q * (len(ordonnees) - 1)
    bas = math.floor(position)
    haut = min(bas + 1, len(ordonnees) - 1)
    return ordonnees[bas] + (ordonnees[haut] - ordonnees[bas]) * (position - bas)


def part_au_moins(valeurs, seuil: float) -> float:
    return sum(v >= seuil for v in valeurs) / len(valeurs) if valeurs else math.nan


def part_au_plus(valeurs, seuil: float) -> float:
    return sum(v <= seuil for v in valeurs) / len(valeurs) if valeurs else math.nan


def part_sous(valeurs, seuil: float) -> float:
    """Part strictement sous le seuil."""
    return sum(v < seuil for v in valeurs) / len(valeurs) if valeurs else math.nan


def verdict_bande(indices, bande, seuil=SEUIL_PLAUSIBLE) -> str:
    """Règle 4 : compatible, trop régulier ou trop irrégulier."""
    bas, haut = bande
    if part_au_moins(indices, bas) < seuil:
        return "trop régulier"
    if part_au_plus(indices, haut) < seuil:
        return "trop irrégulier"
    return "compatible"


def resume_indices(indices, fenetres_disjointes: int) -> dict:
    return {
        "fenetres": len(indices),
        "fenetres_disjointes": fenetres_disjointes,
        "q05": quantile(indices, 0.05),
        "mediane": quantile(indices, 0.50),
        "q95": quantile(indices, 0.95),
        "p_083": part_au_moins(indices, BANDE_ETROITE[1]),
        "p_045": part_au_moins(indices, BANDE_ETROITE[0]),
        "verdict_large": verdict_bande(indices, BANDE_LARGE),
        "verdict_etroite": verdict_bande(indices, BANDE_ETROITE),
    }


# --- contrôles de regroupement ------------------------------------------------


def intervalles(arrivees_h) -> list[float]:
    ordonnees = sorted(arrivees_h)
    return [b - a for a, b in pairwise(ordonnees)]


def cv2(valeurs) -> float:
    """Carré du coefficient de variation : c_a² quand on l'applique aux intervalles."""
    if len(valeurs) < 2:
        return math.nan
    moyenne = statistics.fmean(valeurs)
    return statistics.variance(valeurs) / (moyenne * moyenne) if moyenne else math.nan


def proba_au_moins_k_sur_n(p: float, k: int, n: int) -> float:
    """P(au moins k succès sur n) pour une probabilité élémentaire p : la chance de voir
    dans un échantillon de la taille de l'observé au moins ce qui y a été vu."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def observe(observations=None) -> dict:
    """Intervalles entre arrivées et attentes des minéraliers, lus dans les données."""
    ocr = _charger("ore_calls_report")
    chemin = observations or ocr.OBSERVATIONS
    if not Path(chemin).exists():
        return {"intervalles_h": [], "attentes_h": []}
    toutes = ocr.build_escales(ocr.read_observations(chemin))
    lignes = ocr.report_rows(ocr.select_escales(toutes), toutes=toutes)
    return {
        "intervalles_h": [x["intervalle_h"] for x in lignes if x["intervalle_h"] is not None],
        "attentes_h": [x["wait_hours"] for x in lignes if x["wait_hours"] is not None],
    }


def i_observe_courant(raw) -> tuple[float, float]:
    """I recalculé sur les bilans archivés, avec et sans le trimestre anormal.

    Sert à vérifier que les bandes pré-enregistrées décrivent toujours les données : si la
    série est révisée, le verdict ne doit pas juger contre des chiffres périmés.
    """
    beta_site = _charger("beta_site")
    navires = beta_site.navires_par_trimestre()
    return (disp.index_of_dispersion(navires),
            disp.index_of_dispersion(beta_site.sans_trimestre_anormal(navires, raw)))


# --- simulation -----------------------------------------------------------------


def configuration(scenario, depassements, cv_delai=None, decalage_s_j=0) -> SimConfig:
    base = SimConfig.from_scenario(load_scenario(scenario))
    s_j = POINT_DE_COMMANDE_J[(scenario, depassements)] + decalage_s_j
    ecarts = {"depassements_autorises": depassements, "point_de_commande_j": float(s_j),
              "point_de_commande_t": s_j * base.debit_pellets_t_par_h * 24}
    if cv_delai is not None:
        ecarts["cv_delai_approvisionnement"] = cv_delai
    return dataclasses.replace(base, **ecarts)


def graines(n: int, seed: int) -> list[int]:
    maitre = random.Random(seed)
    return [maitre.randrange(2**31) for _ in range(n)]


def simuler(config, seeds, annees: float, taille_t: float) -> dict:
    """Les fenêtres de I, les intervalles et les attentes, poolés sur plusieurs graines."""
    echauffement_h = ECHAUFFEMENT_STATIONNAIRE_ANS * config.heures_par_an
    total = ECHAUFFEMENT_STATIONNAIRE_ANS + annees
    trimestre_h = config.heures_par_an / TRIMESTRES_PAR_AN
    indices, ecarts, attentes, disjointes = [], [], [], 0
    for g in seeds:
        c = chronique(config, seed=g, years=total, periode_h=trimestre_h)
        comptages = comptages_trimestriels(c["periodes"], taille_t, echauffement_h)
        indices += indices_par_fenetre(comptages)
        disjointes += len(comptages) // TRIMESTRES_PAR_FENETRE
        arrivees = [a for a in c["arrivees_h"] if a >= echauffement_h]
        ecarts += intervalles(arrivees)
        attentes += [w for w, a in zip(c["attentes_h"], c["attentes_arrivee_h"], strict=True)
                     if a >= echauffement_h]
    return {"indices": indices, "disjointes": disjointes, "intervalles_h": ecarts,
            "attentes_h": attentes}


def regroupement(sim: dict, obs: dict) -> dict:
    """Contrôles 3 : probabilité élémentaire, et chance de revoir l'observé."""
    p_court = part_sous(sim["intervalles_h"], INTERVALLE_COURT_H)
    p_long = part_au_moins(sim["attentes_h"], ATTENTE_LONGUE_H)
    k_court = sum(x < INTERVALLE_COURT_H for x in obs["intervalles_h"])
    k_long = sum(x > ATTENTE_LONGUE_H for x in obs["attentes_h"])
    return {
        "cv2": cv2(sim["intervalles_h"]),
        "p_intervalle_court": p_court,
        "p_attente_longue": p_long,
        "observe_court": (k_court, len(obs["intervalles_h"])),
        "observe_long": (k_long, len(obs["attentes_h"])),
        "chance_court": proba_au_moins_k_sur_n(p_court, k_court, len(obs["intervalles_h"])),
        "chance_long": proba_au_moins_k_sur_n(p_long, k_long, len(obs["attentes_h"])),
    }


# --- figure -------------------------------------------------------------------


def donnees_figure(balayage: dict) -> dict:
    """{(scénario, dépassements): (cv, q05, médiane, q95)}, prêtes à tracer."""
    sortie = {}
    for (scenario, depassements), lignes in balayage.items():
        cvs = sorted(lignes)
        sortie[(scenario, depassements)] = (
            cvs,
            [lignes[c]["q05"] for c in cvs],
            [lignes[c]["mediane"] for c in cvs],
            [lignes[c]["q95"] for c in cvs],
        )
    return sortie


def write_figure(balayage: dict, path=DEFAULT_FIGURE) -> Path:
    donnees = donnees_figure(balayage)
    figure, axes = plt.subplots(1, len(SCENARIOS), figsize=(13, 5.6), sharey=True,
                                constrained_layout=True)
    couleurs = {True: "#1f77b4", False: "#e8710a"}
    for ax, scenario in zip(axes, SCENARIOS, strict=True):
        ax.axhspan(*BANDE_LARGE, color="0.92", zorder=0, label="IC 95 % de l'observé")
        ax.axhspan(*BANDE_ETROITE, color="#2ca02c", alpha=0.15, zorder=1,
                   label="observé : 0,45 (sans 2022 T3) à 0,83")
        for depassements, nom in HYPOTHESES:
            cvs, bas, med, haut = donnees[(scenario, depassements)]
            ax.fill_between(cvs, bas, haut, color=couleurs[depassements], alpha=0.18, zorder=2)
            ax.plot(cvs, med, color=couleurs[depassements], marker="o", linewidth=2,
                    zorder=3, label=f"I simulé, médiane et 90 % — {nom}")
        ax.axvline(CV_DELAI_PREENREGISTRE, color="0.4", linestyle=":", linewidth=1.2)
        ax.set_title(scenario, fontweight="bold")
        ax.set_xlabel("cv du délai d'approvisionnement")
        ax.set_yscale("log")
        ax.grid(True, which="both", color="0.9")
    axes[0].set_ylabel("I sur 16 trimestres (échelle log)")
    axes[0].legend(fontsize=8.5, loc="lower right")
    figure.suptitle("Dispersion trimestrielle des arrivées : le modèle endogène face aux bilans "
                    "du port\nI = Var/Moyenne des minéraliers déchargés par trimestre, sur des "
                    "fenêtres de 16 trimestres simulés (comme l'observé). Pointillé : cv central.",
                    fontsize=10.5)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


# --- rapport ------------------------------------------------------------------


def _ligne_resume(nom, r) -> str:
    return (f"  {nom:46} {r['mediane']:>7.3f} [{r['q05']:.3f} ; {r['q95']:.3f}]"
            f" {r['p_083']:>9.1%} {r['p_045']:>9.1%}   {r['verdict_large']:<16}"
            f"{r['verdict_etroite']:<16} ({r['fenetres_disjointes']} fenêtres disjointes)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graines", type=int, default=DEFAULT_GRAINES)
    parser.add_argument("--graines-balayage", type=int, default=DEFAULT_GRAINES_BALAYAGE)
    parser.add_argument("--annees", type=float, default=DEFAULT_ANNEES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--cv", type=float, nargs="+", default=list(CV_DELAI_BALAYAGE),
                        help="valeurs de cv_delai de la carte de sensibilité")
    parser.add_argument("--sans-robustesse", action="store_true",
                        help="saute la robustesse en s (essais rapides)")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    centrales = {(sc, h): configuration(sc, h) for sc in SCENARIOS for h, _ in HYPOTHESES}
    verifier_preenregistrement(raw, centrales.values())
    i_tout, i_sans = i_observe_courant(raw)
    if not (math.isclose(i_tout, BANDE_ETROITE[1], abs_tol=0.005)
            and math.isclose(i_sans, BANDE_ETROITE[0], abs_tol=0.005)):
        raise ReglesViolees(f"les bilans donnent désormais I = {i_tout:.3f} et {i_sans:.3f} : "
                            "les bandes pré-enregistrées sont périmées")
    taille_t = float(raw["navires_minerai"]["taille_moyenne"]["value"])
    obs = observe()
    print(f"règles vérifiées : cv_delai = {CV_DELAI_PREENREGISTRE}, I observé = {i_tout:.2f} "
          f"(sans 2022 T3 : {i_sans:.2f}) ; {args.annees:.0f} ans par graine après "
          f"{ECHAUFFEMENT_STATIONNAIRE_ANS:.0f} ans d'échauffement")

    print(f"\n{'=' * 118}\n1. Verdict à cv_delai central ({CV_DELAI_PREENREGISTRE}), "
          f"{args.graines} graines\n{'=' * 118}")
    print(f"  {'configuration':46} {'I médian [5 % ; 95 %]':>27} {'P(I≥0,83)':>9} "
          f"{'P(I≥0,45)':>9}   {'bande large':<16}{'bande étroite':<16}")
    seeds = graines(args.graines, args.seed)
    centraux = {}
    for (scenario, depassements), config in centrales.items():
        sim = simuler(config, seeds, args.annees, taille_t)
        r = resume_indices(sim["indices"], sim["disjointes"])
        centraux[(scenario, depassements)] = (r, regroupement(sim, obs))
        print(_ligne_resume(f"{scenario}, {dict(HYPOTHESES)[depassements]}", r))

    print(f"\n{'=' * 118}\n2. Carte de sensibilité à cv_delai — pas un réglage\n{'=' * 118}")
    seeds_b = graines(args.graines_balayage, args.seed)
    balayage = {}
    for scenario in SCENARIOS:
        for depassements, nom in HYPOTHESES:
            lignes = {}
            for cv in args.cv:
                config = configuration(scenario, depassements, cv_delai=cv)
                sim = simuler(config, seeds_b, args.annees, taille_t)
                lignes[cv] = resume_indices(sim["indices"], sim["disjointes"])
            balayage[(scenario, depassements)] = lignes
            print(f"\n  --- {scenario}, {nom}")
            for cv in args.cv:
                print(_ligne_resume(f"cv_delai = {cv:.2f}", lignes[cv]))
            plausibles = [cv for cv in args.cv
                          if lignes[cv]["verdict_etroite"] == "compatible"]
            print(f"  bande étroite compatible pour cv_delai ∈ "
                  f"{plausibles if plausibles else 'aucune valeur de la fourchette'}")

    print(f"\n{'=' * 118}\n   Robustesse au seul paramètre calibré : s ± 5 j, cv central\n"
          f"{'=' * 118}")
    for (scenario, depassements) in ([] if args.sans_robustesse else centrales):
        for d in ECARTS_S_J:
            if d == 0:
                continue
            config = configuration(scenario, depassements, decalage_s_j=d)
            sim = simuler(config, seeds_b, args.annees, taille_t)
            r = resume_indices(sim["indices"], sim["disjointes"])
            nom = (f"{scenario}, {dict(HYPOTHESES)[depassements]}, s = "
                   f"{POINT_DE_COMMANDE_J[(scenario, depassements)] + d} j")
            print(_ligne_resume(nom, r))

    print(f"\n{'=' * 118}\n3. Regroupement, à cv central — contrôle, pas verdict\n{'=' * 118}")
    k_c, n_c = next(iter(centraux.values()))[1]["observe_court"]
    k_l, n_l = next(iter(centraux.values()))[1]["observe_long"]
    print(f"  observé : {k_c} intervalle(s) < {INTERVALLE_COURT_H:.0f} h sur {n_c} mesuré(s) ; "
          f"{k_l} attente(s) > {ATTENTE_LONGUE_H:.0f} h sur {n_l} mesurée(s).")
    print(f"  {'configuration':46} {'CV² interv.':>11} {'P(<2 j)':>9} {'chance obs.':>12} "
          f"{'P(>150 h)':>10} {'chance obs.':>12}")
    for (scenario, depassements), (_, g) in centraux.items():
        print(f"  {scenario + ', ' + dict(HYPOTHESES)[depassements]:46} {g['cv2']:>11.3f}"
              f" {g['p_intervalle_court']:>9.2%} {g['chance_court']:>12.2%}"
              f" {g['p_attente_longue']:>10.2%} {g['chance_long']:>12.2%}")
    print("  « chance obs. » : probabilité qu'un échantillon de la taille de l'observé montre au")
    print("  moins ce qui y a été vu. Une ou deux observations ne tranchent rien.")

    chemin = write_figure(balayage, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
