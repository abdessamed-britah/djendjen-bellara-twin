"""Le taux de service converge-t-il avec l'horizon ? Et vers quoi ?

**Deux régimes à distinguer, et ils ne se lisent pas de la même façon.**

*Modèle exogène* (`baseline`, `phase2`). Le débit moyen d'arrivée égale exactement la demande
(`navires_par_an = besoin_annuel / taille_navire`), sans rétroaction : l'offre cumulée moins la
demande cumulée est une marche aléatoire **sans dérive**, bornée en bas par zéro (le four
s'arrête) et pas en haut (le navire garde sa cargaison). Le déficit cumulé croît comme √T, le
taux de perte décroît en 1/√T et β tend vers 1 sans jamais se stabiliser : **pas de régime
stationnaire**.

*Modèle endogène* (`baseline_endogene`, `phase2_endogene`). Un navire est affrété quand la
position de stock passe sous le point de commande. La rétroaction rend au système une dérive de
rappel, donc un régime stationnaire. **Un régime stationnaire avec ruptures perd une fraction
constante de sa production** : le déficit cumulé croît alors comme T et β converge vers une
limite β∞ < 1. C'est le résultat attendu, et non un échec.

**Ce que dit l'exposant du déficit**, D ≈ a·T^p, et seulement cela :

- p ≈ 1/2 : marche sans dérive — aucun régime stationnaire ;
- p ≈ 1 : régime stationnaire avec pertes — β converge vers une limite inférieure à 1. Cela
  ne dit pas *pourquoi* il y a des pertes : un défaut de capacité et des ruptures aléatoires
  d'un système stationnaire donnent la même pente. La première version de ce script lisait
  p ≈ 1 comme un « déficit structurel » ; c'était confondre les deux ;
- p ≈ 0 : déficit borné — les ruptures disparaissent et β tend vers 1 en 1/T.

Les issues sont écrites avant le calcul pour que le verdict ne puisse pas être choisi après coup.

**Ce qui juge la convergence, c'est β, pas p.** Trois critères :

1. au-delà de `SEUIL_CONVERGENCE_ANS`, les différences **appariées** de β entre horizons
   successifs ne sont plus significatives ;
2. le taux de déficit se stabilise. Exprimé en années de cible par année simulée, il vaut
   exactement 1 − β : ce critère est le premier relu en points de production perdue ;
3. la **méthode des moyennes par lots** sur une seule longue réplication donne la même limite
   que les réplications indépendantes. Deux estimateurs construits différemment qui
   s'accordent : c'est ce qui distingue une limite d'un artefact de plan d'expérience.

Le premier critère dépend du nombre de réplications : la queue en 1/T du stock initial et du
démarrage finit toujours par devenir significative avec assez de réplications. Le script
publie donc la taille des différences, pas seulement leur verdict.

**Le stock initial.** Il fausse les horizons courts dans un sens connu : c'est un cadeau de
`S0` tonnes, qui relève β d'au plus `S0 / cible(T)`. Le script le mesure directement, par
différence appariée avec un stock initial nul, et le compare à cette borne.

**Appariement.** Chaque réplication est jouée à tous les horizons avec la même graine. Le
modèle étant causal et ses flux aléatoires séparés, la simulation de T ans est exactement le
préfixe de celle de 50 ans : les points d'une même réplication décrivent une **seule**
trajectoire lue à plusieurs instants, et le déficit en fonction de T est une vraie courbe.

Usage : python scripts/convergence_horizon.py [--reps 20] [--scenario baseline]
        python scripts/convergence_horizon.py --scenario phase2_endogene --lots-annees 500
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
from corridor.sim.model import production_par_periode, simulate
from corridor.sim.stats import estimate, paired_difference, significant

HORIZONS = (1, 2, 4, 8, 16, 32, 50)
DEFAULT_SCENARIO = "baseline"
DEFAULT_REPS = 20
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/convergence_horizon.png")
#: Régime d'arrivée du diagnostic exogène : Poisson, là où la perte est franche. Sans
#: effet en politique endogène, où l'irrégularité des arrivées est une sortie du modèle.
DISPERSION = 1.0
TIRAGES_BOOTSTRAP = 2000
#: Au-delà de cet horizon, β doit ne plus bouger de façon significative d'un horizon au
#: suivant (critère 1).
SEUIL_CONVERGENCE_ANS = 8
#: Moyennes par lots : longueur de la réplication unique, échauffement écarté, taille d'un
#: lot. Des lots de plusieurs années, parce que le stock d'une année se reporte sur la
#: suivante : des lots d'un an seraient autocorrélés, et leur IC trop étroit.
LOTS_ANNEES = 400
LOTS_ECHAUFFEMENT_ANS = 4
LOTS_TAILLE_ANS = 4

#: Les trois issues, écrites avant le calcul. Une borne d'IC de `p` qui tombe dans une
#: zone l'en exclut ; l'issue retenue est celle dont la valeur est dans l'IC et les deux
#: autres dehors. Sinon : indécidable, et le script le dit.
ISSUES = {
    "marche sans dérive": 0.5,
    "régime stationnaire avec pertes": 1.0,
    "déficit borné": 0.0,
}


def configuration(scenario: str, initial: bool = True) -> SimConfig:
    """Le scénario, avec ou sans stock initial ; à arrivées de Poisson s'il est exogène.

    En politique endogène, la dispersion des arrivées est sans effet : on ne la touche pas,
    pour que la configuration affichée soit celle qui tourne.
    """
    config = SimConfig.from_scenario(load_scenario(scenario))
    if config.politique_approvisionnement == "exogene":
        config = dataclasses.replace(config, dispersion_arrivees=DISPERSION)
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


def resultats_par_horizon(config, horizons, seeds) -> dict[int, list]:
    """{horizon: [SimResult, un par réplication]}, dans l'ordre des graines."""
    return {h: [simulate(config, seed=g, years=float(h)) for g in seeds] for h in horizons}


def deficits(config, resultats) -> dict[int, list[float]]:
    """{horizon: [déficit cumulé en tonnes de DRI, une valeur par réplication]}."""
    return {
        h: [cible_t(config, h) - r.dri_production_t for r in liste]
        for h, liste in resultats.items()
    }


def part_des_ruptures(config, resultats) -> dict[int, object]:
    """{horizon: Estimate de la part des heures ouvrées perdues faute de pellets}."""
    return {
        h: estimate([r.dri_stop_hours / (config.heures_ouvrees_par_an * h) for r in liste])
        for h, liste in resultats.items()
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


def convergence_beta(config, deficits_par_h, seuil=SEUIL_CONVERGENCE_ANS) -> list[dict]:
    """Critère 1 : différences appariées de β entre horizons successifs, au-delà du seuil.

    Appariées parce qu'une réplication est une trajectoire : β(16) et β(8) d'une même graine
    partagent huit années, et c'est l'intervalle de leur différence qui décide.
    """
    horizons = sorted(deficits_par_h)
    lignes = []
    for avant, apres in pairwise(horizons):
        if avant < seuil:
            continue
        b_avant = [1.0 - d / cible_t(config, avant) for d in deficits_par_h[avant]]
        b_apres = [1.0 - d / cible_t(config, apres) for d in deficits_par_h[apres]]
        difference = paired_difference(b_apres, b_avant)
        lignes.append({"de": avant, "a": apres, "difference": difference,
                       "significative": significant(difference)})
    return lignes


def moyennes_par_lots(config, seed, annees=LOTS_ANNEES, echauffement=LOTS_ECHAUFFEMENT_ANS,
                      taille=LOTS_TAILLE_ANS) -> dict:
    """Critère 3 : β limite estimé sur **une seule** longue réplication coupée en lots.

    Les `echauffement` premières années sont écartées (démarrage et stock initial), puis les
    années restantes sont regroupées par `taille`. Chaque lot donne un β, et la moyenne des
    lots porte l'IC. L'autocorrélation d'ordre 1 entre lots successifs est publiée : proche
    de zéro, les lots sont presque indépendants et l'IC est honnête ; nettement positive,
    il est trop étroit et il faut des lots plus longs.
    """
    periodes = production_par_periode(config, seed=seed, years=float(annees))
    utiles = periodes[echauffement:]
    cible_an = cible_t(config, 1.0)
    lots = [utiles[i:i + taille] for i in range(0, len(utiles) - taille + 1, taille)]
    betas = [sum(p["production_t"] for p in lot) / (cible_an * len(lot)) for lot in lots]
    annees_avec_rupture = sum(p["rupture_h"] > 0 for p in utiles)
    return {
        "beta": estimate(betas),
        "annees": annees,
        "lots": len(betas),
        "autocorrelation": autocorrelation(betas),
        "part_annees_avec_rupture": annees_avec_rupture / len(utiles) if utiles else math.nan,
        "part_heures_en_rupture": (sum(p["rupture_h"] for p in utiles)
                                   / (config.heures_ouvrees_par_an * len(utiles))
                                   if utiles else math.nan),
    }


def autocorrelation(valeurs) -> float:
    """Autocorrélation d'ordre 1 ; NaN si la série est trop courte ou constante."""
    n = len(valeurs)
    if n < 3:
        return math.nan
    moyenne = sum(valeurs) / n
    variance = sum((v - moyenne) ** 2 for v in valeurs)
    if variance == 0:
        return math.nan
    return sum((a - moyenne) * (b - moyenne) for a, b in pairwise(valeurs)) / variance


def accord_des_estimateurs(independantes, lots) -> dict:
    """Critère 3, la comparaison : les deux estimations de β∞ s'accordent-elles ?

    Les deux estimateurs ne partagent aucune réplication : l'IC de leur différence combine
    donc les deux demi-largeurs en quadrature.
    """
    ecart = lots.mean - independantes.mean
    demi = math.sqrt(lots.half_width ** 2 + independantes.half_width ** 2)
    return {"ecart": ecart, "demi_largeur": demi, "accord": abs(ecart) <= demi}


def issues_compatibles(ajustement: dict) -> list[str]:
    """Les issues dont la valeur de p tombe dans l'IC de l'ajustement."""
    bas, haut = ajustement["p_bas"], ajustement["p_haut"]
    return [nom for nom, v in ISSUES.items() if bas <= v <= haut]


def issue_de(ajustement: dict) -> str:
    """L'issue unique compatible, ou « indécidable » s'il y en a zéro ou plusieurs."""
    dedans = issues_compatibles(ajustement)
    return dedans[0] if len(dedans) == 1 else "indécidable"


def verdict(ajustement: dict, lignes: list[dict]) -> dict:
    """L'issue retenue parmi les trois, ou « indécidable » — jamais choisie après coup."""
    dedans = issues_compatibles(ajustement)
    issue = issue_de(ajustement)
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


def write_figure(lignes, ajustement, scenario, reps, path=DEFAULT_FIGURE,
                 politique="exogene", beta_limite=None) -> Path:
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
    if beta_limite is not None:
        gauche.axhspan(beta_limite.low, beta_limite.high, color="#e8710a", alpha=0.15,
                       linewidth=0, label="β∞ par moyennes par lots (IC 95 %)")
        gauche.axhline(beta_limite.mean, color="#e8710a", linewidth=1.2)
        gauche.legend(fontsize=9, loc="lower right")
    gauche.set_title("β selon l'horizon de simulation", fontweight="bold")

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
                    linestyle=":", linewidth=1.2, label="référence ∝ T (régime stationnaire avec pertes)")
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
        f"Convergence avec l'horizon — scénario {scenario}, "
        f"{'arrivées de Poisson exogènes' if politique == 'exogene' else 'approvisionnement par point de commande'}, "
        f"{reps} réplications appariées\n"
        "Chaque réplication est une seule trajectoire lue à plusieurs horizons. Bandes : IC à "
        "95 %.", fontsize=10.5)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def verdict_convergence(differences, accord) -> dict:
    """β a-t-il convergé ? Les critères 1 et 3 doivent tenir ensemble."""
    stable = bool(differences) and not any(d["significative"] for d in differences)
    return {"stable_au_dela_du_seuil": stable,
            "accord_des_estimateurs": accord["accord"] if accord else None,
            "converge": stable and (accord is None or accord["accord"])}


def print_rapport(config, lignes, ajustement, parts, issue, ajustement_sans=None,
                  ruptures=None, differences=None, lots=None, accord=None,
                  convergence=None, ajustement_tardif=None) -> None:
    print(f"  politique d'approvisionnement : {config.politique_approvisionnement}")
    if config.politique_approvisionnement == "exogene":
        print(f"  dérive relative de l'offre : {derive_relative(config):+.2e} "
              "(nulle par construction : offre calée sur la demande)")
    else:
        print(f"  point de commande {config.point_de_commande_j:.0f} j, délai moyen "
              f"{config.delai_approvisionnement_h / 24:.0f} j (cv "
              f"{config.cv_delai_approvisionnement:.2f})")

    print(f"\n{'=' * 96}\nβ, déficit et ruptures selon l'horizon\n{'=' * 96}")
    print(f"{'T (ans)':>8} {'β':>18} {'déficit (années de cible)':>28} "
          f"{'taux 1−β':>10} {'heures en rupture':>20}")
    for ligne in lignes:
        b, d = ligne["beta"], ligne["deficit_normalise"]
        r = ruptures[ligne["annees"]] if ruptures else None
        rupt = f"{r.mean:>11.2%} ± {r.half_width:<6.2%}" if r else ""
        print(f"{ligne['annees']:>8} {b.mean:>10.4f} ± {b.half_width:<6.4f} "
              f"{d.mean:>18.4f} ± {d.half_width:<7.4f} {1 - b.mean:>10.2%} {rupt}")

    print(f"\n{'=' * 96}\nLoi de puissance du déficit : D ≈ a·T^p\n{'=' * 96}")
    print(f"  p = {ajustement['p']:.3f}, IC 95 % bootstrap [{ajustement['p_bas']:.3f} ; "
          f"{ajustement['p_haut']:.3f}], R² = {ajustement['r2']:.3f}, "
          f"{ajustement['points']} horizons")
    for nom, valeur in ISSUES.items():
        dans = ajustement["p_bas"] <= valeur <= ajustement["p_haut"]
        print(f"  {nom:34s} (p = {valeur:.1f}) : {'compatible' if dans else 'exclu'}")
    if ajustement_tardif is not None:
        tardif = issue_de(ajustement_tardif)
        print(f"\n  Hors transitoire (T ≥ {SEUIL_CONVERGENCE_ANS} ans) : p = "
              f"{ajustement_tardif['p']:.3f}, IC 95 % [{ajustement_tardif['p_bas']:.3f} ; "
              f"{ajustement_tardif['p_haut']:.3f}] → {tardif}")
        print("  Un régime stationnaire avec un démarrage difficile a un déficit affine,")
        print("  D ≈ c·T + d : la loi de puissance lit l'ordonnée d comme une pente plus")
        print("  faible. Restreinte aux horizons où le transitoire est amorti, elle lit c.")
    if ajustement_sans is not None:
        print(f"\n  À stock initial nul : p = {ajustement_sans['p']:.3f}, IC 95 % "
              f"[{ajustement_sans['p_bas']:.3f} ; {ajustement_sans['p_haut']:.3f}]")
        if config.politique_approvisionnement == "exogene":
            print("  Le stock initial réduit les déficits des horizons courts et raidit donc la")
            print("  pente apparente : c'est cet exposant-ci qui décrit la marche elle-même.")
        else:
            print("  **À ne pas lire comme le régime** : sans stock de départ, la politique")
            print("  commande à t = 0 mais rien n'arrive avant le délai d'approvisionnement.")
            print("  La rupture du démarrage est alors certaine, elle concentre le déficit au")
            print("  début et aplatit la pente. En endogène, retirer le stock initial crée un")
            print("  biais au lieu d'en supprimer un : ce sont β et les lots qui jugent.")

    print(f"\n{'=' * 96}\nPart du stock initial dans β\n{'=' * 96}")
    print(f"{'T (ans)':>8} {'Δβ mesuré':>20} {'borne S0/cible':>16} {'part convertie':>16}")
    for ligne in parts:
        d = ligne["delta_beta"]
        convertie = d.mean / ligne["borne"] if ligne["borne"] else math.nan
        print(f"{ligne['annees']:>8} {d.mean:>12.4f} ± {d.half_width:<6.4f} "
              f"{ligne['borne']:>16.4f} {convertie:>16.0%}")

    if differences is not None:
        print(f"\n{'=' * 96}\nCritère 1 — β bouge-t-il encore au-delà de "
              f"{SEUIL_CONVERGENCE_ANS} ans ?\n{'=' * 96}")
        print("  Différences appariées β(T suivant) − β(T), même trajectoire :")
        for d in differences:
            e = d["difference"]
            print(f"  {d['de']:>3} → {d['a']:<3} ans : {e.mean:+.4f} ± {e.half_width:.4f}  "
                  f"{'SIGNIFICATIVE' if d['significative'] else 'non significative'}")

    if lots is not None:
        print(f"\n{'=' * 96}\nCritère 3 — moyennes par lots sur une seule réplication\n"
              f"{'=' * 96}")
        b = lots["beta"]
        print(f"  {lots['annees']} ans, {LOTS_ECHAUFFEMENT_ANS} d'échauffement écartés, "
              f"{lots['lots']} lots de {LOTS_TAILLE_ANS} ans")
        print(f"  β∞ par lots            : {b.mean:.4f} ± {b.half_width:.4f}")
        print(f"  autocorrélation lag 1  : {lots['autocorrelation']:+.3f}")
        print(f"  heures en rupture      : {lots['part_heures_en_rupture']:.2%} des heures ouvrées")
        print(f"  années avec ≥ 1 rupture : {lots['part_annees_avec_rupture']:.0%}")
        if accord is not None:
            print(f"  écart avec les réplications indépendantes à {lignes[-1]['annees']} ans : "
                  f"{accord['ecart']:+.4f} ± {accord['demi_largeur']:.4f}  "
                  f"→ {'ACCORD' if accord['accord'] else 'DÉSACCORD'}")

    print(f"\n{'=' * 96}\nVerdict\n{'=' * 96}")
    print(f"  exposant : **{issue['issue']}**  (compatibles : "
          f"{', '.join(issue['issues_compatibles']) or 'aucune'})")
    print(f"  β croissant avec l'horizon : {'oui' if issue['beta_croissant'] else 'non'}")
    if convergence is not None:
        print(f"  β stable au-delà de {SEUIL_CONVERGENCE_ANS} ans : "
              f"{'oui' if convergence['stable_au_dela_du_seuil'] else 'NON'}")
        if convergence["accord_des_estimateurs"] is not None:
            print(f"  lots et réplications s'accordent : "
                  f"{'oui' if convergence['accord_des_estimateurs'] else 'NON'}")
        print(f"  **β converge : {'OUI' if convergence['converge'] else 'NON'}**")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS))
    parser.add_argument("--lots-annees", type=int, default=0,
                        help="longueur de la réplication des moyennes par lots ; 0 = pas de lots")
    parser.add_argument("--figure", type=Path, default=None)
    args = parser.parse_args(argv)

    avec_config = configuration(args.scenario, initial=True)
    sans_config = configuration(args.scenario, initial=False)
    seeds = graines(args.reps, args.seed)
    print(f"scénario {args.scenario}, {args.reps} réplications, horizons {args.horizons} ans")

    resultats = resultats_par_horizon(avec_config, args.horizons, seeds)
    avec = deficits(avec_config, resultats)
    sans = deficits(sans_config, resultats_par_horizon(sans_config, args.horizons, seeds))
    lignes = resume(avec_config, avec)
    ajustement = exposant(avec)
    parts = part_du_stock_initial(avec, sans, avec_config)
    issue = verdict(ajustement, lignes)
    differences = convergence_beta(avec_config, avec)

    lots = accord = None
    if args.lots_annees:
        lots = moyennes_par_lots(avec_config, seed=args.seed, annees=args.lots_annees)
        accord = accord_des_estimateurs(lignes[-1]["beta"], lots["beta"])
    convergence = verdict_convergence(differences, accord)

    print_rapport(avec_config, lignes, ajustement, parts, issue,
                  ajustement_sans=exposant(sans),
                  ajustement_tardif=exposant(
                      {h: d for h, d in avec.items() if h >= SEUIL_CONVERGENCE_ANS}),
                  ruptures=part_des_ruptures(avec_config, resultats),
                  differences=differences, lots=lots, accord=accord,
                  convergence=convergence)

    figure = args.figure or (DEFAULT_FIGURE if args.scenario == DEFAULT_SCENARIO
                             else DEFAULT_FIGURE.with_name(
                                 f"convergence_horizon_{args.scenario}.png"))
    chemin = write_figure(lignes, ajustement, args.scenario, args.reps, figure,
                          politique=avec_config.politique_approvisionnement,
                          beta_limite=lots["beta"] if lots else None)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
