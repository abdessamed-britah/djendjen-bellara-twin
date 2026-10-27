"""Politique de commande en régime stationnaire : report, point de commande, attente en rade.

Tout est mesuré sur les scénarios endogènes, les seuls à avoir un régime stationnaire, selon
le protocole de l'étape 1 (`corridor.sim.run.replicate_stationnaire` : 4 ans d'échauffement
écartés, 16 ans mesurés). Et **chaque comparaison est publiée sous les deux hypothèses de
dépassement** — délais indépendants (les navires se doublent) ou navette sur une seule route
(ils arrivent dans l'ordre de commande) — parce que cette hypothèse pèse jusqu'à 1,7 point de
taux de service (limite 20 de CLAUDE.md).

**1. L'option de report.** `reporter_si_navire_en_rade` interdit de commander tant qu'un
navire attend en rade. Elle fait en partie double emploi avec la politique, dont la position
de stock compte déjà ce navire. Ce qu'elle ajoute est mesuré en différences appariées
(option − base) sur β, l'attente en rade et le coût total. **Si rien n'est significatif,
c'est le résultat** : l'option est redondante.

**2. Le point de commande.** Balayage de s de 15 à 60 jours. Pour chaque s : β, ruptures,
attente moyenne en rade, stock moyen, et les trois termes du coût (`economic_summary.
cout_total`). Un optimum intérieur est **cherché, pas exigé** : il n'existe que si le total
en s* est significativement plus bas, en données appariées, qu'aux deux bords du domaine.

**3. Où l'optimum se déplace.** Les trois termes sont linéaires en leurs prix unitaires,
qui sont tous sans source. Seuls les **rapports** comptent : en prenant le portage pour
numéraire, la position de s* dépend de deux nombres, arrêt/portage et surestaries/portage.
Le script balaye les deux sur deux ordres de grandeur sans resimuler.

**4. L'attente simulée face à l'observée.** La distribution complète des attentes en rade,
placée à côté de la seule attente minerai mesurable de la série de situations portuaires
(lue dans `data/clean/observations.csv`, pas recopiée). C'est un **recoupement**, pas une
validation : une observation isolée, censurée à gauche, ne valide rien ; elle dit seulement
si le modèle rend plausible ce qui a été vu.

Usage : python scripts/politique_commande.py [--reps 16] [--seed 20260924]
"""

import argparse
import importlib.util
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.model import attentes_en_rade
from corridor.sim.run import (
    ECHAUFFEMENT_STATIONNAIRE_ANS,
    replicate_stationnaire,
)
from corridor.sim.stats import estimate, paired_difference, significant


def _charger(nom: str):
    """Charge un script voisin : `scripts/` n'est pas un paquet."""
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eco = _charger("economic_summary")

#: Le domaine du point de commande, en jours de consommation : la fourchette de l'hypothèse.
POINTS_DE_COMMANDE_J = (15, 20, 25, 30, 35, 40, 45, 50, 55, 60)
DEFAULT_REPS = 16
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/politique_commande.png")
#: Multiplicateurs des prix unitaires, portage pris pour numéraire. Deux ordres de grandeur
#: de part et d'autre : à peu près l'incertitude des trois prix, tous sans source.
FACTEURS = (0.1, 0.3, 1.0, 3.0, 10.0)
#: Longueur de la trajectoire unique dont on tire la distribution des attentes.
ANNEES_ATTENTES = 60.0


# --- mesures ------------------------------------------------------------------


def mesurer(config, reps: int, seed: int, unitaires, scenario: str = "") -> dict:
    """Une configuration en régime stationnaire : indicateurs et coûts, réplication par
    réplication (pour les différences appariées) et en `Estimate` (pour la publication)."""
    resultats = replicate_stationnaire(config, reps, seed, scenario)
    lignes = []
    for r in resultats:
        annees = r.annees_mesurees
        cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an * annees
        couts = eco.cout_total(r, config, unitaires)
        lignes.append({
            "beta": r.dri_production_t / cible,
            # le taux de service propre au corridor : 1 − production perdue faute de pellets.
            # Égal à β en mode continu ; en mode discret, β y ajoute l'aléa des pannes.
            "beta_appro": 1.0 - eco.perte_faute_de_pellets_t(r, config) / cible,
            "rupture_h_an": r.dri_stop_hours / annees,
            "attente_h": r.wait_mean_h,
            "stock_t": r.stockyard_mean_t + r.plant_stock_mean_t,
            "report_h_an": r.order_deferral_h / annees,
            **couts,
        })
    cles = lignes[0].keys()
    return {"lignes": lignes, **{c: estimate([ligne[c] for ligne in lignes]) for c in cles}}


def serie(mesure: dict, cle: str) -> list[float]:
    return [ligne[cle] for ligne in mesure["lignes"]]


def effet_du_report(base: dict, option: dict) -> dict:
    """Option − base, apparié réplication par réplication, sur les trois grandeurs demandées."""
    effets = {}
    for cle in ("beta", "attente_h", "total"):
        d = paired_difference(serie(option, cle), serie(base, cle))
        effets[cle] = {"difference": d, "significative": significant(d)}
    return effets


def optimum(balayage: list[dict], cle: str = "total") -> dict:
    """Le s qui minimise le coût moyen, et s'il est intérieur **de façon significative**.

    Intérieur veut dire : pas sur un bord du domaine, et le coût y est plus bas qu'aux deux
    bords en données appariées. Un minimum de moyennes posé sur une pente plate, à une
    demi-largeur d'IC près, n'est pas un optimum : c'est du bruit.
    """
    meilleur = min(balayage, key=lambda ligne: ligne["mesure"][cle].mean)
    bords = (balayage[0], balayage[-1])
    au_bord = meilleur["s"] in (bords[0]["s"], bords[-1]["s"])
    ecarts = [paired_difference(serie(b["mesure"], cle), serie(meilleur["mesure"], cle))
              for b in bords]
    interieur = not au_bord and all(significant(e) and e.mean > 0 for e in ecarts)
    return {"s": meilleur["s"], "cout": meilleur["mesure"][cle], "au_bord": au_bord,
            "ecarts_aux_bords": ecarts, "interieur": interieur}


def s_optimal(balayage, unitaires, facteur_arret: float, facteur_surestaries: float) -> float:
    """s* quand les prix d'arrêt et de surestaries sont multipliés, le portage fixé.

    Les trois termes étant linéaires en leurs prix, il suffit de recombiner les moyennes
    déjà mesurées : aucune simulation de plus.
    """
    def total(ligne):
        m = ligne["mesure"]
        return (facteur_arret * m["arrets_dri"].mean
                + facteur_surestaries * m["surestaries"].mean + m["portage"].mean)

    return min(balayage, key=total)["s"]


def table_des_optima(balayage, unitaires, facteurs=FACTEURS) -> list[list[float]]:
    """s*[i][j] pour un prix d'arrêt × facteurs[i] et des surestaries × facteurs[j]."""
    return [[s_optimal(balayage, unitaires, fa, fs) for fs in facteurs] for fa in facteurs]


# --- attente observée ---------------------------------------------------------


def attentes_observees(observations=None) -> list[tuple[str, float]]:
    """Les attentes en rade **mesurables** des minéraliers de la série, lues dans les données.

    Lues et non recopiées : la collecte continue, et la liste s'allongera d'elle-même.
    """
    ocr = _charger("ore_calls_report")
    chemin = observations or ocr.OBSERVATIONS
    if not Path(chemin).exists():
        return []
    escales = ocr.select_escales(ocr.build_escales(ocr.read_observations(chemin)))
    return [(e.vessel, e.wait_hours) for e in escales if e.wait_hours is not None]


def resume_attentes(attentes: list[float], observee: float | None) -> dict:
    """Moyenne, quantiles, et la place de l'attente observée dans la distribution."""
    ordonnees = sorted(attentes)
    n = len(ordonnees)
    if not n:
        return {"n": 0}

    def quantile(q):
        return ordonnees[min(int(q * (n - 1) + 0.5), n - 1)]

    resume = {
        "n": n,
        "moyenne": statistics.fmean(ordonnees),
        "mediane": quantile(0.5),
        "p90": quantile(0.9),
        "p99": quantile(0.99),
        "maximum": ordonnees[-1],
        "part_nulle": sum(a <= 1e-9 for a in ordonnees) / n,
    }
    if observee is not None:
        resume["part_au_moins_observee"] = sum(a >= observee for a in ordonnees) / n
    return resume


# --- figure -------------------------------------------------------------------


COULEURS = {"arrets_dri": "#d62728", "surestaries": "#1f77b4", "portage": "#2ca02c",
            "total": "0.15"}
LIBELLES = {"arrets_dri": "arrêts du DRI", "surestaries": "surestaries",
            "portage": "portage du stock", "total": "total"}


def donnees_figure(balayages: dict) -> dict:
    """{(scénario, dépassements): {terme: (s, moyennes M USD, bas, haut)}} et les optima."""
    sortie = {}
    for cle, balayage in balayages.items():
        s = [ligne["s"] for ligne in balayage]
        sortie[cle] = {
            terme: (s,
                    [ligne["mesure"][terme].mean / 1e6 for ligne in balayage],
                    [ligne["mesure"][terme].low / 1e6 for ligne in balayage],
                    [ligne["mesure"][terme].high / 1e6 for ligne in balayage])
            for terme in (*eco.TERMES, "total")
        }
        sortie[cle]["optimum"] = optimum(balayage)
    return sortie


def write_figure(balayages: dict, reps: int, path=DEFAULT_FIGURE) -> Path:
    """Coût annuel en fonction de s, une case par scénario et par hypothèse de dépassement."""
    donnees = donnees_figure(balayages)
    scenarios = list(eco.SCENARIOS_STATIONNAIRES)
    hypotheses = list(eco.HYPOTHESES_DEPASSEMENT)
    figure, axes = plt.subplots(len(scenarios), len(hypotheses), figsize=(13, 9.5),
                                sharex=True, constrained_layout=True)
    for i, scenario in enumerate(scenarios):
        for j, (depassements, nom) in enumerate(hypotheses):
            ax = axes[i][j]
            serie_ = donnees[(scenario, depassements)]
            for terme in (*eco.TERMES, "total"):
                s, moy, bas, haut = serie_[terme]
                largeur = 2.6 if terme == "total" else 1.6
                ax.plot(s, moy, color=COULEURS[terme], linewidth=largeur, marker="o",
                        markersize=3.5, label=LIBELLES[terme])
                ax.fill_between(s, bas, haut, color=COULEURS[terme], alpha=0.12, linewidth=0)
            opt = serie_["optimum"]
            ax.axvline(opt["s"], color="0.4", linestyle=":", linewidth=1.2)
            etat = "intérieur" if opt["interieur"] else (
                "au bord" if opt["au_bord"] else "non significatif")
            ax.set_title(f"{scenario} — {nom}\nminimum à s = {opt['s']} j ({etat})",
                         fontsize=10, fontweight="bold")
            ax.set_ylim(bottom=0)
            ax.grid(True, color="0.9")
            if i == len(scenarios) - 1:
                ax.set_xlabel("point de commande s (jours de consommation)")
            if j == 0:
                ax.set_ylabel("coût annuel (M USD/an)")
    axes[0][0].legend(fontsize=9, loc="upper center")
    figure.suptitle(
        "Coût annuel en régime stationnaire selon le point de commande — trois termes et total\n"
        f"{reps} réplications appariées, {ECHAUFFEMENT_STATIONNAIRE_ANS:.0f} ans d'échauffement "
        "écartés, bandes : IC 95 %. Prix unitaires centraux, tous de statut estimé : "
        "seule la forme se lit, pas les montants.", fontsize=10.5)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


# --- rapport ------------------------------------------------------------------


def _m(e, echelle=1e6, chiffres=2) -> str:
    return f"{e.mean / echelle:.{chiffres}f} ± {e.half_width / echelle:.{chiffres}f}"


def print_report(effets: dict) -> None:
    print(f"\n{'=' * 100}\n1. L'option de report : ce qu'elle ajoute à la politique de base "
          f"(s = 30 j)\n{'=' * 100}")
    print("  Différences appariées option − base. β en points, attente en heures, coût en "
          "M USD/an.")
    for (scenario, nom), e in effets.items():
        b, a, c = e["beta"], e["attente_h"], e["total"]
        print(f"  {scenario:18} {nom:24} β {b['difference'].mean * 100:+.3f} ± "
              f"{b['difference'].half_width * 100:.3f} {'*' if b['significative'] else ' '}  "
              f"attente {a['difference'].mean:+.1f} ± {a['difference'].half_width:.1f} h "
              f"{'*' if a['significative'] else ' '}  coût "
              f"{c['difference'].mean / 1e6:+.3f} ± {c['difference'].half_width / 1e6:.3f}"
              f" {'*' if c['significative'] else ' '}")
    print("  * = significatif à 95 %.")


def print_balayage(cle, balayage) -> None:
    scenario, depassements = cle
    nom = dict(eco.HYPOTHESES_DEPASSEMENT)[depassements]
    print(f"\n  --- {scenario}, {nom}")
    print(f"  {'s (j)':>5} {'β':>16} {'rupture h/an':>14} {'attente h':>13} {'stock kt':>10}"
          f" {'arrêts':>13} {'surest.':>13} {'portage':>13} {'total':>13}  (M USD/an)")
    for ligne in balayage:
        m = ligne["mesure"]
        print(f"  {ligne['s']:>5} {_m(m['beta'], 1, 4):>16} {_m(m['rupture_h_an'], 1, 0):>14}"
              f" {_m(m['attente_h'], 1, 1):>13} {m['stock_t'].mean / 1e3:>10.0f}"
              f" {_m(m['arrets_dri']):>13} {_m(m['surestaries']):>13} {_m(m['portage']):>13}"
              f" {_m(m['total']):>13}")
    opt = optimum(balayage)
    bas, haut = opt["ecarts_aux_bords"]
    print(f"  minimum à s = {opt['s']} j ; écart aux bords : s = {balayage[0]['s']} → "
          f"{_m(bas)} ; s = {balayage[-1]['s']} → {_m(haut)} M USD/an ; "
          f"{'OPTIMUM INTÉRIEUR' if opt['interieur'] else 'pas d’optimum intérieur établi'}")


def print_rapports(cle, balayage, unitaires) -> None:
    scenario, depassements = cle
    nom = dict(eco.HYPOTHESES_DEPASSEMENT)[depassements]
    table = table_des_optima(balayage, unitaires)
    print(f"\n  --- s* (jours) selon les prix, {scenario}, {nom} — portage fixé")
    entete = "arrêt × | surest. ×"
    print(f"  {entete:>22}" + "".join(f"{f:>8g}" for f in FACTEURS))
    for fa, ligne in zip(FACTEURS, table, strict=True):
        print(f"  {fa:>22g}" + "".join(f"{s:>8}" for s in ligne))


def print_attentes(resumes: dict, observees) -> None:
    print(f"\n{'=' * 100}\n4. Attente en rade simulée face à l'observée — recoupement, pas "
          f"validation\n{'=' * 100}")
    if observees:
        for navire, h in observees:
            print(f"  observée : {navire}, {h:.1f} h ({h / 24:.1f} j), censurée à gauche")
    else:
        print("  aucune attente minerai mesurable dans la série")
    print(f"\n  {'configuration':44} {'navires':>8} {'moyenne':>9} {'médiane':>9} {'p90':>8}"
          f" {'p99':>8} {'max':>8} {'part nulle':>11} {'≥ observée':>11}")
    for nom, r in resumes.items():
        if not r.get("n"):
            continue
        obs = f"{r['part_au_moins_observee']:.1%}" if "part_au_moins_observee" in r else "—"
        print(f"  {nom:44} {r['n']:>8} {r['moyenne']:>8.1f}h {r['mediane']:>8.1f}h"
              f" {r['p90']:>7.0f}h {r['p99']:>7.0f}h {r['maximum']:>7.0f}h"
              f" {r['part_nulle']:>11.0%} {obs:>11}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--points", type=int, nargs="+", default=list(POINTS_DE_COMMANDE_J))
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    unitaires = eco.couts_unitaires(raw)
    print(f"régime stationnaire : {args.reps} réplications, s ∈ {args.points} j")

    # 2. balayage — la base de l'option est la case s = valeur centrale de l'hypothèse
    balayages, effets = {}, {}
    for scenario in eco.SCENARIOS_STATIONNAIRES:
        for depassements, nom in eco.HYPOTHESES_DEPASSEMENT:
            base = eco.configuration_stationnaire(scenario, depassements)
            balayage = []
            for s_j in args.points:
                config = eco.configuration_stationnaire(
                    scenario, depassements,
                    point_de_commande_t=s_j * base.debit_pellets_t_par_h * 24,
                    point_de_commande_j=float(s_j))
                balayage.append({"s": s_j, "mesure": mesurer(config, args.reps, args.seed,
                                                              unitaires, scenario)})
            balayages[(scenario, depassements)] = balayage
            # 1. option de report, à s central
            mesure_base = next((b["mesure"] for b in balayage
                                if b["s"] == base.point_de_commande_j), None)
            if mesure_base is None:
                mesure_base = mesurer(base, args.reps, args.seed, unitaires, scenario)
            option = eco.configuration_stationnaire(scenario, depassements,
                                                    reporter_si_navire_en_rade=True)
            effets[(scenario, nom)] = effet_du_report(
                mesure_base, mesurer(option, args.reps, args.seed, unitaires, scenario))

    print_report(effets)
    print(f"\n{'=' * 100}\n2. Balayage du point de commande\n{'=' * 100}")
    for cle, balayage in balayages.items():
        print_balayage(cle, balayage)
    print(f"\n{'=' * 100}\n3. Position de l'optimum selon le rapport des prix\n{'=' * 100}")
    for cle, balayage in balayages.items():
        print_rapports(cle, balayage, unitaires)

    # 4. attentes en rade
    observees = attentes_observees()
    observee = observees[0][1] if observees else None
    resumes = {}
    for scenario in eco.SCENARIOS_STATIONNAIRES:
        for depassements, nom in eco.HYPOTHESES_DEPASSEMENT:
            config = eco.configuration_stationnaire(scenario, depassements)
            attentes = attentes_en_rade(
                config, seed=args.seed, years=ECHAUFFEMENT_STATIONNAIRE_ANS + ANNEES_ATTENTES,
                warmup_years=ECHAUFFEMENT_STATIONNAIRE_ANS)
            resumes[f"{scenario}, {nom}"] = resume_attentes(attentes, observee)
    print_attentes(resumes, observees)

    chemin = write_figure(balayages, args.reps, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
