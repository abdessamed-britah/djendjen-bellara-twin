"""Frontière de service : quelle couverture de stock faut-il pour tenir un taux de service ?

C'est la synthèse de l'étude, sous la forme la plus transposable qu'on puisse lui donner.
Les trois résultats du projet — l'irrégularité des arrivées et le stockage gouvernent tout,
le rail ne limite pas, le poste de déchargement non plus — tiennent dans une seule carte à
deux axes **sans dimension** :

- **c_a²**, carré du coefficient de variation des intervalles entre arrivées. 0 = navires
  parfaitement réguliers, 1 = processus de Poisson, au-delà = arrivées groupées. C'est la
  régularité de l'affrètement, indépendamment de sa cadence. Dans le modèle, le paramètre
  `dispersion_arrivees` **est** ce coefficient de variation, donc c_a² = dispersion².
- **τ = (K_P + K_U) / D**, la capacité de stockage totale (port + usine) rapportée à la
  consommation journalière de pellets de l'aciérie. C'est un **nombre de jours de
  couverture** : combien de temps le four tiendrait si plus rien n'arrivait. Un stock de
  450 kt ne veut rien dire hors contexte ; 27 jours de couverture, si.

La grandeur lue est le **taux de service β = production / cible**, où la cible est la
production que l'aciérie atteindrait si le minerai arrivait toujours à temps. β = 0,95
signifie « 5 % de la production annuelle perdue faute d'approvisionnement ».

**Pourquoi des axes sans dimension.** Un autre site — autre tonnage de navire, autre cadence
de déchargement, autre capacité de four — se place sur la même carte dès lors qu'il calcule
son c_a² et son τ. Ce qui ne se transpose pas, et qu'il faut lire avec la figure, c'est la
**charge du poste** ρ = λ·E[S]/c : elle dépend du site, et c'est elle qui distingue les deux
courbes tracées ici (1 poste ou 2, à demande égale).

**Ce que la carte ne dit pas.** β est une moyenne sur des réplications d'un an, assortie
d'un intervalle de confiance qui n'est pas nul : les contours sont tracés sur la moyenne, à
quelques points de pourcentage près. Les deux capacités de stockage du site restent de
statut `inconnu` — c'est bien pourquoi la figure donne une frontière et non un verdict.

Usage : python scripts/frontiere_service.py [--reps 30] [--scenario phase2]
        python scripts/frontiere_service.py --reps 5        # version rapide, contours grossiers
"""

import argparse
import dataclasses
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # aucune fenêtre : on écrit un fichier
import matplotlib.pyplot as plt
import pyarrow as pa
import pyarrow.parquet as pq

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.run import replicate
from corridor.sim.sensitivity import storage_shares, with_storage
from corridor.sim.stats import Estimate, estimate

DEFAULT_FIGURE = Path("docs/frontiere_service.png")
#: Table de la carte : git-ignorée comme les autres sorties de simulation, recalculable.
DEFAULT_TABLE = Path("data/sim/frontiere_service.parquet")
DEFAULT_SCENARIO = "phase2"
DEFAULT_REPS = 30
SEED = 20260924

#: Axe horizontal : carré du coefficient de variation des intervalles entre arrivées.
C_A2_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)
#: Axe vertical : couverture de stock, en jours de consommation de l'aciérie.
TAU_DAYS = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0)
#: Les deux configurations de poste comparées, à demande identique.
POSTES = (1, 2)
#: Les deux taux de service dont on trace la frontière.
LEVELS = (0.95, 0.99)

#: Palette catégorielle validée (ΔE CVD 22,6 en protanopie) : une teinte par nombre de postes.
COULEURS = {1: "#1f77b4", 2: "#e8710a"}
#: Le niveau de service est codé par le style de trait, jamais par la teinte.
STYLES = {0.95: "--", 0.99: "-"}

HEURES_PAR_JOUR = 24.0


def consommation_journaliere_t(config: SimConfig) -> float:
    """D : pellets consommés par jour par l'aciérie, four en marche.

    C'est le débit horaire de pellets porté à la journée. Le four ne tourne pas tous les
    jours de l'année (arrêts planifiés), donc τ se lit « jours de marche couverts », ce qui
    est bien la grandeur utile : c'est le temps que le stock peut absorber avant l'arrêt.
    """
    return config.debit_pellets_t_par_h * HEURES_PAR_JOUR


def cible_t(config: SimConfig, years: float) -> float:
    """Production que l'aciérie atteindrait si le minerai arrivait toujours à temps."""
    return config.debit_dri_t_par_h * config.heures_ouvrees_par_an * years


def couverture_jours(config: SimConfig) -> float:
    """τ de la configuration : stockage total rapporté à la consommation journalière."""
    total = config.capacite_stockyard_t + config.capacite_stock_usine_t
    return total / consommation_journaliere_t(config)


def point_config(base: SimConfig, c_a2: float, tau: float, postes: int, shares) -> SimConfig:
    """Configuration d'un point de la carte : (c_a², τ, nombre de postes).

    `dispersion_arrivees` est le coefficient de variation lui-même, d'où la racine carrée.
    """
    total_t = tau * consommation_journaliere_t(base)
    config = with_storage(base, total_t, shares)
    return dataclasses.replace(
        config,
        dispersion_arrivees=math.sqrt(c_a2),
        postes_minerai=postes,
        politique_expedition="pull",
    )


def beta_grid(base: SimConfig, shares, reps: int, years: float, progress: bool = False):
    """Taux de service moyen sur toute la carte, pour chaque nombre de postes.

    Renvoie {postes: {(c_a², τ): Estimate de β}}. Toutes les cases partagent la même graine
    maîtresse : deux cases se comparent donc en données appariées si besoin, et la carte
    est reproductible à l'identique.
    """
    cible = cible_t(base, years)
    grilles = {}
    for postes in POSTES:
        cases = {}
        for c_a2 in C_A2_VALUES:
            for tau in TAU_DAYS:
                config = point_config(base, c_a2, tau, postes, shares)
                resultats = replicate(config, years, reps, SEED, f"frontiere_{postes}p")
                beta = estimate([r.dri_production_t / cible for r in resultats])
                cases[(c_a2, tau)] = beta
                if progress:
                    print(f"  {postes} poste(s)  c_a²={c_a2:.2f}  τ={tau:4.0f} j  "
                          f"β = {beta.mean:.3f} ± {beta.half_width:.3f}")
        grilles[postes] = cases
    return grilles


def to_table(grilles) -> pa.Table:
    """La carte en table Parquet : une ligne par point, pour retracer sans recalculer.

    Les quelques minutes de simulation que coûte la carte n'ont pas à être repayées pour
    changer une couleur ou un libellé — d'où `--from-parquet`.
    """
    lignes = {"postes": [], "c_a2": [], "tau_jours": [], "beta": [], "beta_half_width": [],
              "beta_std": [], "reps": []}
    for postes, cases in grilles.items():
        for (c_a2, tau), beta in cases.items():
            lignes["postes"].append(postes)
            lignes["c_a2"].append(c_a2)
            lignes["tau_jours"].append(tau)
            lignes["beta"].append(beta.mean)
            lignes["beta_half_width"].append(beta.half_width)
            lignes["beta_std"].append(beta.std)
            lignes["reps"].append(beta.n)
    return pa.table(lignes)


def from_table(table: pa.Table):
    """Reconstruit {postes: {(c_a², τ): Estimate}} depuis une table écrite par `to_table`."""
    grilles: dict[int, dict[tuple[float, float], Estimate]] = {}
    colonnes = table.to_pydict()
    for i in range(table.num_rows):
        postes = int(colonnes["postes"][i])
        cle = (float(colonnes["c_a2"][i]), float(colonnes["tau_jours"][i]))
        grilles.setdefault(postes, {})[cle] = Estimate(
            mean=float(colonnes["beta"][i]),
            std=float(colonnes["beta_std"][i]),
            n=int(colonnes["reps"][i]),
        )
    return grilles


def courbes_disponibles(grilles, levels=LEVELS) -> set[tuple[int, float]]:
    """Les couples (postes, niveau) pour lesquels une frontière existe dans le domaine.

    Déduit des données — un niveau est traçable dès qu'au moins un c_a² l'atteint — et non
    du résultat de `contour`. Deux raisons : la note « courbe absente » de la figure doit
    pouvoir se vérifier sans rendre d'image, et une information tirée des données vaut
    mieux qu'une information tirée de la bibliothèque de tracé.
    """
    disponibles = set()
    for postes, grille in grilles.items():
        for level in levels:
            if any(tau is not None for tau in couverture_requise(grille, level).values()):
                disponibles.add((postes, level))
    return disponibles


def donnees_figure(grilles, raw, base: SimConfig, levels=LEVELS) -> dict:
    """Tout ce que la figure trace, sans tracer : matrices, bandes, courbes manquantes.

    Sépare le calcul du rendu. La boucle courte des tests vérifie ce dictionnaire ; le
    rendu lui-même, lent et sans logique propre, est joué à part (`pytest -m slow`).
    """
    disponibles = courbes_disponibles(grilles, levels)
    return {
        "matrices": {postes: matrice(cases) for postes, cases in grilles.items()},
        "bande_tau": bande_du_site(base, raw),
        "bande_c_a2": bande_c_a2(raw),
        "courbes_disponibles": disponibles,
        "courbes_manquantes": sorted(
            (postes, level) for postes in grilles for level in levels
            if (postes, level) not in disponibles
        ),
        "bandes": {(postes, level): bande_de_frontiere(cases, level)
                   for postes, cases in grilles.items() for level in levels},
        "rendements": {(postes, level): rendement_marginal(cases, level)
                       for postes, cases in grilles.items() for level in levels},
    }


def matrice(cases) -> list[list[float]]:
    """β en matrice [τ][c_a²], dans l'ordre des deux axes : ce qu'attend `contour`."""
    return [[cases[(c_a2, tau)].mean for c_a2 in C_A2_VALUES] for tau in TAU_DAYS]


def couverture_requise(cases, level: float, bord: float = 0.0) -> dict[float, float | None]:
    """Pour chaque c_a², la couverture τ à partir de laquelle β atteint `level`.

    Interpolation linéaire entre les deux τ qui encadrent le franchissement, en balayant
    du bas vers le haut. None si le niveau n'est jamais atteint dans le domaine exploré :
    c'est une information en soi, pas un trou à masquer.

    `bord` déplace β de ce nombre de demi-largeurs d'IC avant de chercher le franchissement.
    C'est ce qui transforme la frontière en bande : à `bord = +1` on lit la frontière du
    corridor le plus favorable que l'IC autorise, donc **moins** de stockage requis ; à
    `bord = −1`, le plus défavorable, donc davantage. La frontière n'est pas une ligne, et
    la tracer comme une ligne laisse croire à une précision qu'un β à ± 0,01 n'a pas.
    """
    requis = {}
    for c_a2 in C_A2_VALUES:
        betas = [cases[(c_a2, tau)].mean + bord * cases[(c_a2, tau)].half_width
                 for tau in TAU_DAYS]
        trouve = None
        for i in range(len(TAU_DAYS) - 1):
            bas, haut = betas[i], betas[i + 1]
            if bas < level <= haut:
                part = (level - bas) / (haut - bas) if haut != bas else 0.0
                trouve = TAU_DAYS[i] + part * (TAU_DAYS[i + 1] - TAU_DAYS[i])
                break
        if trouve is None and betas[0] >= level:
            trouve = TAU_DAYS[0]  # déjà atteint au plus bas stockage exploré
        requis[c_a2] = trouve
    return requis


def bande_de_frontiere(cases, level: float) -> dict[float, tuple | None]:
    """La frontière comme bande : (τ optimiste, τ central, τ pessimiste) par c_a².

    Les trois valeurs viennent du même franchissement, lu à trois hauteurs de β : la
    moyenne, et les deux bornes de son IC à 95 %. Un c_a² dont même la borne optimiste
    n'atteint pas le niveau n'a pas de bande du tout et vaut None ; un c_a² dont seule la
    borne pessimiste manque garde sa bande, ouverte vers le haut (τ pessimiste à None).
    """
    optimiste = couverture_requise(cases, level, bord=+1.0)
    central = couverture_requise(cases, level, bord=0.0)
    pessimiste = couverture_requise(cases, level, bord=-1.0)
    bandes: dict[float, tuple | None] = {}
    for c_a2 in C_A2_VALUES:
        if central[c_a2] is None and optimiste[c_a2] is None:
            bandes[c_a2] = None
            continue
        bandes[c_a2] = (optimiste[c_a2], central[c_a2], pessimiste[c_a2])
    return bandes


def rendement_marginal(cases, level: float) -> dict[float, float | None]:
    """Rendement marginal du stockage au voisinage de la frontière : dβ/dτ, par jour.

    C'est la pente du segment de grille où β franchit le niveau. Elle répond à la question
    du dimensionnement, que la frontière seule n'adresse pas : **un jour de couverture de
    plus, ça rapporte combien de points de taux de service ?** Une pente faible signifie
    que la frontière est plate, donc mal située par la simulation *et* peu sensible dans
    la réalité — se tromper de cinq jours ne coûte alors presque rien. Une pente forte
    signifie l'inverse : la frontière est nette et la rater se paie.

    None là où le niveau n'est pas franchi dans le domaine (pas de segment à mesurer).
    """
    pentes: dict[float, float | None] = {}
    for c_a2 in C_A2_VALUES:
        betas = [cases[(c_a2, tau)].mean for tau in TAU_DAYS]
        pente = None
        for i in range(len(TAU_DAYS) - 1):
            bas, haut = betas[i], betas[i + 1]
            if bas < level <= haut:
                pente = (haut - bas) / (TAU_DAYS[i + 1] - TAU_DAYS[i])
                break
        if pente is None and betas[0] >= level:
            # déjà atteint au plancher : la pente utile est celle du premier segment
            pente = (betas[1] - betas[0]) / (TAU_DAYS[1] - TAU_DAYS[0])
        pentes[c_a2] = pente
    return pentes


def print_table(grilles, base: SimConfig) -> None:
    """La carte en chiffres : β case par case, puis la couverture requise par niveau."""
    for postes in POSTES:
        cases = grilles[postes]
        print(f"\n{'=' * 96}\nTaux de service β, {postes} poste(s) de déchargement\n{'=' * 96}")
        print(f"{'τ (jours)':>10}", end="")
        for c_a2 in C_A2_VALUES:
            print(f"{f'c_a²={c_a2:.2f}':>14}", end="")
        print()
        for tau in TAU_DAYS:
            print(f"{tau:>10.0f}", end="")
            for c_a2 in C_A2_VALUES:
                b = cases[(c_a2, tau)]
                print(f"  {b.mean:>6.3f}±{b.half_width:<5.3f}", end="")
            print()

    print(f"\n{'=' * 96}\nCouverture τ requise, en jours de consommation\n{'=' * 96}")
    print(f"{'niveau':>8} {'postes':>7}", end="")
    for c_a2 in C_A2_VALUES:
        print(f"{f'c_a²={c_a2:.2f}':>14}", end="")
    print()
    for level in LEVELS:
        for postes in POSTES:
            requis = couverture_requise(grilles[postes], level)
            print(f"{level:>8.2f} {postes:>7}", end="")
            for c_a2 in C_A2_VALUES:
                valeur = requis[c_a2]
                print(f"{'jamais' if valeur is None else f'{valeur:.1f} j':>14}", end="")
            print()
    print("\n« jamais » = le niveau n'est pas atteint dans le domaine exploré "
          f"(τ ≤ {max(TAU_DAYS):.0f} jours).")


def bande_du_site(base: SimConfig, raw) -> tuple[float, float, float]:
    """Couverture τ du site : valeur centrale et bornes de la fourchette publiée.

    Les deux capacités de stockage étant de statut `inconnu`, le site n'est pas un point
    sur la carte mais une bande. C'est exactement ce que la figure doit montrer.
    """
    d = consommation_journaliere_t(base)
    port = raw["stockage_port"]["capacite_stockyard_pellets"]
    usine = raw["usine"]["capacite_stock_pellets_usine"]
    centre = (float(port["value"]) + float(usine["value"])) / d
    bas = (float(port["range"][0]) + float(usine["range"][0])) / d
    haut = (float(port["range"][1]) + float(usine["range"][1])) / d
    return bas, centre, haut


def bande_c_a2(raw) -> tuple[float, float, float] | None:
    """Position du site sur l'axe horizontal : (bas, centre, haut) en c_a².

    L'hypothèse stocke un **coefficient de variation** ; l'axe de la carte est son carré.
    L'élévation au carré est donc le point à ne pas rater, et c'est pourquoi elle est
    faite ici et nulle part ailleurs. Renvoie None tant que l'estimation n'existe pas,
    pour que la figure reste traçable sur un dépôt où elle n'aurait pas été calculée.
    """
    entree = raw.get("simulation", {}).get("dispersion_arrivees_observee")
    if not entree:
        return None
    centre = float(entree["value"]) ** 2
    bas = float(entree["range"][0]) ** 2
    haut = float(entree["range"][1]) ** 2
    return bas, centre, haut


def _bande_tracable(bande, c_a2_values=C_A2_VALUES):
    """Les séries d'une bande, prêtes à remplir : bornes, centre, et où la bande est ouverte.

    Une borne pessimiste absente signifie que même le bas de l'IC de β n'atteint pas le
    niveau dans le domaine exploré. On la porte au plafond pour pouvoir tracer, mais le
    point est marqué `ouvert` : la figure l'hachure au lieu de le remplir, parce qu'une
    surface pleine jusqu'au plafond se lirait comme une borne mesurée alors qu'il n'y en a
    pas. Faute de cette distinction, l'essentiel de la bande passerait pour du connu.
    """
    xs, bas, haut, centre, ouvert = [], [], [], [], []
    plafond = max(TAU_DAYS)
    for c_a2 in c_a2_values:
        valeurs = bande.get(c_a2)
        if valeurs is None:
            continue
        opt, cen, pess = valeurs
        if opt is None:
            continue
        xs.append(c_a2)
        bas.append(opt)
        haut.append(plafond if pess is None else pess)
        centre.append(None if cen is None else cen)
        ouvert.append(pess is None)
    return {"c_a2": xs, "bas": bas, "haut": haut, "centre": centre, "ouvert": ouvert,
            "ouverte": any(ouvert)}


def write_figure(grilles, base: SimConfig, raw, scenario: str, reps: int,
                 path=DEFAULT_FIGURE) -> Path:
    """La frontière comme **bande**, et le rendement marginal du stockage à côté.

    Deux panneaux, parce qu'il y a deux questions distinctes et qu'une seule courbe ne
    répond pas aux deux :

    - à gauche, **où passe la frontière**, et avec quelle imprécision. Une teinte par
      nombre de postes, un style de trait par niveau de service : jamais deux informations
      sur le même canal. La frontière est une bande et non une ligne, parce que β a un IC
      de ± 0,01 à ± 0,03 selon les cases : la tracer en trait fin prêterait à la carte une
      netteté qu'elle n'a pas ;
    - à droite, **ce que rapporte un jour de stock de plus** au voisinage de la frontière.
      C'est la grandeur de dimensionnement : elle dit si se tromper de cinq jours coûte
      cinq points de taux de service ou un demi-point.

    Les deux bandes du site — couverture en ordonnée, c_a² estimé en abscisse — rappellent
    que ni l'une ni l'autre coordonnée du site n'est connue.
    """
    figure, (ax, ax_pente) = plt.subplots(
        1, 2, figsize=(15.0, 8.0), width_ratios=[1.75, 1.0], constrained_layout=True
    )
    donnees = donnees_figure(grilles, raw, base)
    bas, centre, haut = donnees["bande_tau"]

    ax.axhspan(bas, haut, color="0.93", zorder=0)
    for borne in (bas, haut):
        ax.axhline(borne, color="0.70", linewidth=0.9, zorder=1)
    ax.axhline(centre, color="0.55", linewidth=1.0, linestyle=":", zorder=1)
    ax.text(0.985, centre, f"hypothèse centrale du site : {centre:.0f} j ",
            transform=ax.get_yaxis_transform(), va="bottom", ha="right",
            fontsize=8.5, color="0.35")
    ax.text(0.985, haut, f"fourchette publiée du site : {bas:.0f} à {haut:.0f} j ",
            transform=ax.get_yaxis_transform(), va="top", ha="right",
            fontsize=8.5, color="0.35")

    # position horizontale du site, estimée sur les bilans de trafic 2022-2025
    horizontal = donnees["bande_c_a2"]
    if horizontal is not None:
        ca_bas, ca_centre, ca_haut = horizontal
        # la fourchette déborde le domaine tracé : on la borne pour ne pas écrire hors cadre,
        # et le libellé dit la valeur réelle, pas la valeur bornée
        visible_haut = min(ca_haut, max(C_A2_VALUES))
        for axe in (ax, ax_pente):
            axe.axvspan(ca_bas, visible_haut, color="#e8710a", alpha=0.07, zorder=0)
            for borne in (ca_bas, visible_haut):
                axe.axvline(borne, color="#b06a1a", linewidth=0.9, linestyle=":", zorder=2)
            axe.axvline(ca_centre, color="#b06a1a", linewidth=1.6, linestyle="-.", zorder=2)
        ax.text(ca_centre, 0.988, f" c_a² estimé du site : {ca_centre:.2f} ".replace(".", ","),
                transform=ax.get_xaxis_transform(), va="top", ha="left",
                fontsize=9.5, color="#8a4f10", fontweight="bold")
        depasse = " (bornée au cadre)" if ca_haut > max(C_A2_VALUES) else ""
        ax.text(ca_centre, 0.945,
                f" fourchette {ca_bas:.2f} à {ca_haut:.2f}{depasse} ".replace(".", ","),
                transform=ax.get_xaxis_transform(), va="top", ha="left",
                fontsize=8.5, color="#8a4f10")

    ouvertes, sans_bande = [], []
    for postes in POSTES:
        for level in LEVELS:
            serie = _bande_tracable(donnees["bandes"][(postes, level)])
            if not serie["c_a2"]:
                continue
            visibles = [(c, t) for c, t in zip(serie["c_a2"], serie["centre"], strict=True)
                        if t is not None]
            # Une bande de moins de trois points ne dessine pas une frontière, elle dessine
            # un coin : β = 0,99 n'est atteint qu'aux deux premiers c_a², et sa bande
            # remplissait tout le haut du cadre d'une surface que deux points ne portent
            # pas. On garde alors le trait central seul, et la légende le dit.
            if len(visibles) < 3:
                if visibles:
                    ax.plot([c for c, _ in visibles], [t for _, t in visibles],
                            color=COULEURS[postes], linestyle=STYLES[level], linewidth=2.2,
                            marker="o", markersize=5, zorder=4)
                    sans_bande.append((postes, level, len(visibles)))
                continue
            ferme = [not o for o in serie["ouvert"]]
            # la partie encadrée des deux côtés : une vraie surface
            ax.fill_between(serie["c_a2"], serie["bas"], serie["haut"], where=ferme,
                            color=COULEURS[postes], alpha=0.18, zorder=2, linewidth=0,
                            interpolate=True)
            # la partie ouverte vers le haut : hachurée, parce qu'elle n'a pas de borne
            ax.fill_between(serie["c_a2"], serie["bas"], serie["haut"],
                            where=serie["ouvert"], facecolor="none", hatch="///",
                            edgecolor=COULEURS[postes], alpha=0.45, zorder=2,
                            linewidth=0.0, interpolate=True)
            ax.plot(serie["c_a2"], serie["bas"], color=COULEURS[postes],
                    linewidth=0.9, alpha=0.8, zorder=3)
            ax.plot([c for c, _ in visibles], [t for _, t in visibles],
                    color=COULEURS[postes], linestyle=STYLES[level], linewidth=2.2,
                    zorder=4)
            if serie["ouverte"]:
                ouvertes.append((postes, level))

    for postes in POSTES:
        ax.plot([], [], color=COULEURS[postes], linewidth=2.2,
                label=f"{postes} poste de déchargement" if postes == 1
                else f"{postes} postes de déchargement")
    for level in LEVELS:
        ax.plot([], [], color="0.35", linewidth=2.2, linestyle=STYLES[level],
                label=f"β = {level:.2f}".replace(".", ","))
    ax.fill_between([], [], [], color="0.45", alpha=0.18,
                    label="bande = IC 95 % de β reporté sur τ")
    ax.fill_between([], [], [], facecolor="none", hatch="///", edgecolor="0.45",
                    linewidth=0.0, label="hachuré = pas de borne haute dans le domaine")
    # en bas à droite : la zone où aucun niveau n'est atteint, donc vide de courbes
    ax.legend(loc="lower right", fontsize=9, framealpha=0.95)

    # la moitié droite est vide de courbes, et c'est le résultat : au-delà de Poisson,
    # aucun des deux niveaux n'est atteint même au sommet du domaine de stockage
    hors_atteinte = max(
        (c_a2 for postes in POSTES
         for c_a2, tau in couverture_requise(grilles[postes], min(LEVELS)).items()
         if tau is not None),
        default=None,
    )
    if hors_atteinte is not None and hors_atteinte < max(C_A2_VALUES):
        milieu = (hors_atteinte + max(C_A2_VALUES)) / 2
        ax.text(milieu, (min(TAU_DAYS) + max(TAU_DAYS)) / 2,
                f"au-delà de c_a² ≈ {hors_atteinte:.2f},\nβ = {min(LEVELS):.2f}".replace(".", ",")
                + f"\nn'est atteint par aucune\ndes deux configurations,\nmême à "
                  f"{max(TAU_DAYS):.0f} jours de stock",
                ha="center", va="center", fontsize=9.5, color="0.35", style="italic")

    ax.set_xlim(min(C_A2_VALUES), max(C_A2_VALUES))
    ax.set_ylim(min(TAU_DAYS), max(TAU_DAYS))
    ax.set_xlabel("c_a² — irrégularité des arrivées (carré du coefficient de variation\n"
                  "des intervalles) : 0 = parfaitement régulier, 1 = processus de Poisson")
    ax.set_ylabel("τ — couverture de stock, en jours de consommation de l'aciérie\n"
                  "(capacité port + usine) ÷ (pellets consommés par jour)")
    ax.set_title("Où passe la frontière, et à combien de jours près", fontweight="bold")

    # --- panneau de droite : le rendement marginal du stockage ---------------
    for postes in POSTES:
        for level in LEVELS:
            pentes = donnees["rendements"][(postes, level)]
            points = [(c, p * 100.0) for c, p in pentes.items() if p is not None]
            if not points:
                continue
            ax_pente.plot([c for c, _ in points], [p for _, p in points],
                          color=COULEURS[postes], linestyle=STYLES[level], linewidth=2.2,
                          marker="o", markersize=5, zorder=3)
    ax_pente.set_xlim(min(C_A2_VALUES), max(C_A2_VALUES))
    ax_pente.set_ylim(bottom=0.0)
    ax_pente.set_xlabel("c_a² — irrégularité des arrivées")
    ax_pente.set_ylabel("dβ/dτ — points de taux de service gagnés\npar jour de couverture "
                        "supplémentaire")
    ax_pente.set_title("Ce que rapporte un jour de stock de plus", fontweight="bold")
    toutes = [p * 100.0 for pentes in donnees["rendements"].values()
              for p in pentes.values() if p is not None]
    if toutes:
        median = sorted(toutes)[len(toutes) // 2]
        ax_pente.axhline(median, color="0.55", linewidth=1.0, linestyle=":", zorder=2)
        ax_pente.text(
            0.5, 0.03,
            "Mesuré sur le segment de grille où β franchit le niveau.\n"
            f"Médiane : {median:.2f} point de β par jour de couverture.\n".replace(
                f"{median:.2f}", f"{median:.2f}".replace(".", ","))
            + f"Se tromper de 10 jours sur la frontière coûte donc ~{10 * median:.0f} points\n"
              "de taux de service : c'est l'enjeu réel de la largeur de la bande.",
            transform=ax_pente.transAxes, ha="center", va="bottom", fontsize=8.5,
            color="0.30", style="italic",
        )

    for axe in (ax, ax_pente):
        axe.grid(True, color="0.90", linewidth=0.8, zorder=0)
        axe.set_axisbelow(True)

    manquants = donnees["courbes_manquantes"]
    lignes = [
        f"Frontière de service du corridor — scénario {scenario}",
        "Au-dessus d'une bande, le taux de service est atteint : pour une irrégularité",
        "d'affrètement donnée, elle se lit en jours de stock. La bande est l'IC 95 % de β",
        "reporté sur l'axe des τ — la frontière n'est pas une ligne.",
        (f"β = production ÷ cible, moyenne sur {reps} réplications d'un an, "
         f"{len(C_A2_VALUES)} × {len(TAU_DAYS)} points par bande."),
        "Axes sans dimension : un autre site se place sur la même carte. Ce qui ne s'y",
        "transpose pas est la charge du poste, que distinguent les deux couleurs.",
        ("Bande grise : couverture du site d'après ses hypothèses (statut inconnu). "
         "Bande orange : c_a² estimé"),
        "sur les bilans de trafic 2022-2025 — une estimation large, pas une mesure.",
    ]
    if ouvertes:
        ouvert = ", ".join(
            f"{p} poste{'s' if p > 1 else ''} / β = {n:.2f}".replace(".", ",")
            for p, n in ouvertes
        )
        lignes.append(f"Bande ouverte vers le haut ({ouvert}) : la borne pessimiste de β "
                      "n'atteint le niveau nulle part dans le domaine.")
    if sans_bande:
        maigres = ", ".join(
            f"{p} poste{'s' if p > 1 else ''} / β = {n:.2f}".replace(".", ",")
            + f" : {combien} point{'s' if combien > 1 else ''}"
            for p, n, combien in sans_bande
        )
        lignes.append(f"Point ou trait sans bande ({maigres}) : trop peu de valeurs de c_a² "
                      "atteignent le niveau pour en dessiner une surface.")
    if manquants:
        absents = ", ".join(
            f"{p} poste{'s' if p > 1 else ''} / β = {n:.2f}".replace(".", ",")
            for p, n in manquants
        )
        lignes.append(f"Bande absente ({absents}) : niveau atteint nulle part dans le "
                      "domaine exploré.")
    figure.suptitle("\n".join(lignes), fontsize=10.0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--from-parquet", type=Path, default=None,
                        help="retrace la figure depuis une carte déjà calculée, sans simuler")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    shares = storage_shares(raw)
    base = SimConfig.from_scenario(load_scenario(args.scenario))
    d = consommation_journaliere_t(base)
    bas, centre, haut = bande_du_site(base, raw)

    print(f"scénario {args.scenario} : consommation {d:,.0f} t de pellets par jour"
          .replace(",", " "))
    print(f"  cible annuelle {cible_t(base, 1.0) / 1e6:.3f} Mt de DRI")
    print(f"  couverture du site : {centre:.1f} jours (fourchette {bas:.1f} à {haut:.1f})")
    print(f"  carte : {len(C_A2_VALUES)} × {len(TAU_DAYS)} points × {len(POSTES)} "
          f"configurations de poste, {args.reps} réplications par point")

    if args.from_parquet:
        grilles = from_table(pq.read_table(args.from_parquet))
        reps = max(b.n for cases in grilles.values() for b in cases.values())
        print(f"  carte relue depuis {args.from_parquet} ({reps} réplications par point)")
    else:
        grilles = beta_grid(base, shares, args.reps, args.years, progress=True)
        reps = args.reps
        args.table.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(to_table(grilles), args.table)
        print(f"\ncarte écrite : {args.table}")
    print_table(grilles, base)
    chemin = write_figure(grilles, base, raw, args.scenario, reps, args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
