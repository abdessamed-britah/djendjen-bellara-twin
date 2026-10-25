"""Frontière de service estimée directement, par dichotomie, au lieu d'être lue sur une grille.

**Le problème de la méthode par grille.** `frontiere_service.py` simule 63 points par
configuration, puis lit τ*(c_a², β) en interpolant entre deux niveaux de stockage distants de
5 à 10 jours. Résultat : 28 % des paires consécutives de la frontière reculent là où τ* devrait
croître, d'un recul moyen de 6,8 jours (voir `metamodele_frontiere.py`). Ce bruit est assez
grand pour empêcher de juger un métamodèle. Il vient de trois sources cumulées — β moyenné sur
un nombre fixe de réplications, interpolation linéaire entre deux τ éloignés, et des tirages
aléatoires indépendants d'un point de grille à l'autre.

**Ce que fait ce script, et pourquoi chaque élément y est.**

1. **Dichotomie sur τ** plutôt que grille : l'effort de calcul va là où la réponse se joue,
   au lieu d'être réparti uniformément sur un domaine dont la moitié est loin de la frontière.
   Plus d'interpolation : τ* est encadré directement.
2. **Nombres aléatoires communs** : la réplication *i* utilise la même graine pour **tous** les
   τ et **tous** les c_a², et depuis les flux séparés de `corridor.sim.samplers.Streams`, le
   navire *n* a la même taille et la même cadence d'une configuration à l'autre. La différence
   entre deux évaluations voisines est alors **× 7 moins bruitée** que chacune des deux prise
   isolément (mesuré par `diagnostic_pente`).
   **Mais cela ne sert pas la dichotomie**, et c'est le piège à connaître : elle compare β à
   une *constante* (le niveau visé), et la variance de `β − constante` est celle de β. Aucun
   appariement ne la réduit. Le gain porte sur les comparaisons entre **deux configurations**
   — c'est-à-dire sur tout le reste du projet, pas sur ce script.
3. **Réplications adaptatives** : à chaque pas, le côté est décidé par l'intervalle de
   confiance de β − β cible. Tant qu'il contient zéro, on ne tranche pas et on ajoute des
   réplications — jusqu'à un plafond, au-delà duquel on déclare la résolution atteinte. Le
   calcul se concentre ainsi sur les pas difficiles.
4. **Régression isotone après coup** : τ* doit croître avec c_a². Les violations restantes
   sont du bruit, et l'algorithme PAVA les corrige en projetant sur la suite croissante la
   plus proche. Le **nombre de corrections nécessaires est la mesure du bruit résiduel**, et
   c'est elle qu'on compare aux 28 % de la grille.

**Le résultat, annoncé d'avance pour qu'on ne lise pas le script en espérant autre chose.**
La dichotomie coûte moins de la moitié du calcul de la grille et ne rend pas une frontière plus
propre : **plus efficace, pas plus précise**. Elle produit moins de paires reculantes mais des
reculs plus gros, et le verdict du métamodèle ne bouge pas d'un cran. Les chiffres exacts
viennent de l'exécution, pas de ce texte — ils changent à chaque révision du modèle.
Le diagnostic de pente dit pourquoi, et sa réponse vaut pour les deux méthodes.

**Piège de lecture.** La frontière écrite en Parquet est celle d'*après* la régression isotone :
la relire telle quelle donne 0 % de reculs et un bruit nul par construction. La colonne
`tau_brut` conserve les valeurs d'avant correction, et c'est **elle** qui mesure le bruit et qui
juge le métamodèle. Lisser une contradiction ne la fait pas disparaître de l'incertitude.

**Ce que ce script ne corrige pas.** Les β restent des moyennes de réplications d'un an :
l'aléa annuel est atténué, pas supprimé. Et la frontière reste celle du modèle, dont les deux
capacités de stockage et la cadence sont des hypothèses.

Usage : python scripts/frontiere_directe.py [--reps-min 12] [--reps-max 60] [--tol 1.0]
"""

import argparse
import math
import sys
from itertools import pairwise
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.model import simulate
from corridor.sim.sensitivity import storage_shares
from corridor.sim.stats import Estimate, estimate

sys.path.insert(0, str(Path(__file__).parent))
from frontiere_service import (
    C_A2_VALUES,
    POSTES,
    TAU_DAYS,
    consommation_journaliere_t,
    point_config,
)

DEFAULT_TABLE = Path("data/sim/frontiere_directe.parquet")
DEFAULT_SCENARIO = "phase2"
#: Bornes de la dichotomie, en jours de couverture. Mêmes que le domaine de la grille, pour
#: que la comparaison des deux méthodes porte sur la méthode et non sur le domaine.
TAU_MIN, TAU_MAX = 5.0, 60.0
#: Niveaux de service visés. Quatre suffisent à ajuster l'exposant du métamodèle, et chaque
#: niveau supplémentaire coûte une dichotomie complète par (c_a², k).
BETAS = (0.90, 0.93, 0.95, 0.97)
DEFAULT_REPS_MIN = 12
DEFAULT_REPS_MAX = 60
#: Largeur d'encadrement sous laquelle on arrête de couper, en jours.
DEFAULT_TOL = 1.0
DEFAULT_SEED = 20260924
#: Réplications par case de la grille de `frontiere_service.py` telle qu'elle a été publiée.
#: Sert uniquement à chiffrer le coût de la méthode concurrente, pas à simuler.
GRILLE_REPS = 60
#: Bruit de la frontière lue sur cette grille, mesuré par `metamodele_frontiere.py` : part de
#: paires qui reculent là où τ* doit croître, recul moyen, et erreur de transposition du
#: métamodèle structurel (colonne de c_a² retirée).
GRILLE_PART_VIOLEE = 0.28
GRILLE_RECUL_MOYEN = 6.8
GRILLE_ERREUR_METAMODELE = 10.3


class Evaluateur:
    """Évalue β(c_a², τ, k) en réutilisant les réplications déjà jouées.

    Deux mécanismes, et ils sont le cœur de la méthode :

    - **nombres aléatoires communs** : la réplication *i* a la graine `seed + i`, quelle que
      soit la configuration évaluée. Deux points voisins partagent donc leurs aléas ;
    - **cache incrémental** : une configuration déjà évaluée à 12 réplications et redemandée
      à 24 ne rejoue que les 12 manquantes. La dichotomie revient souvent au même endroit.
    """

    def __init__(self, base: SimConfig, raw, shares, seed=DEFAULT_SEED, annees=1.0):
        self.base = base
        self.raw = raw
        self.shares = shares
        self.seed = seed
        self.annees = annees
        self._cache: dict[tuple[int, float, float], list[float]] = {}
        self.annees_simulees = 0.0

    def _cle(self, postes, c_a2, tau):
        return (postes, round(c_a2, 6), round(tau, 4))

    def betas(self, postes: int, c_a2: float, tau: float, reps: int) -> list[float]:
        """Les β des `reps` premières réplications, en n'en simulant que ce qui manque."""
        cle = self._cle(postes, c_a2, tau)
        deja = self._cache.setdefault(cle, [])
        if len(deja) >= reps:
            return deja[:reps]
        config = point_config(self.base, c_a2, tau, postes, self.shares)
        cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an * self.annees
        for i in range(len(deja), reps):
            resultat = simulate(config, seed=self.seed + i, years=self.annees)
            deja.append(resultat.dri_production_t / cible)
            self.annees_simulees += self.annees
        return deja[:reps]


def cote(betas, cible: float) -> tuple[int, Estimate]:
    """De quel côté de la cible se trouve β ? +1 au-dessus, −1 en dessous, 0 indécidable.

    Le verdict vient de l'intervalle de confiance à 95 % : tant qu'il contient la cible, on
    ne tranche pas. C'est ce qui évite de couper du mauvais côté sur un écart qui n'est que
    du bruit — l'erreur qui, dans la méthode par grille, produit les reculs de τ*.
    """
    e = estimate(betas)
    if not e.defined:
        return 0, e
    if e.low > cible:
        return 1, e
    if e.high < cible:
        return -1, e
    return 0, e


def trancher(evaluateur, postes, c_a2, tau, cible, reps_min, reps_max):
    """Décide le côté en ajoutant des réplications tant que l'intervalle contient la cible.

    Renvoie (côté, estimation, réplications, décidé). `décidé` est faux quand le plafond de
    réplications a été atteint sans que l'intervalle exclue la cible : le côté renvoyé est
    alors le signe de l'estimation ponctuelle, un pari et non une décision.

    Rendre ce pari plutôt que d'abandonner est volontaire. Près de la frontière, β est plat :
    l'indécision y est la règle, pas l'exception, et s'arrêter au premier pas indécis
    laisserait un encadrement de plusieurs dizaines de jours. Un pari non biaisé se corrige
    aux pas suivants, et le nombre de paris est compté et publié.
    """
    reps = reps_min
    while True:
        verdict, e = cote(evaluateur.betas(postes, c_a2, tau, reps), cible)
        if verdict != 0:
            return verdict, e, reps, True
        if reps >= reps_max:
            pari = 1 if e.mean >= cible else -1
            return pari, e, reps, False
        reps = min(reps * 2, reps_max)


def tau_etoile(evaluateur, postes, c_a2, cible, reps_min, reps_max, tol) -> dict:
    """τ* par dichotomie sur τ, avec décision statistique à chaque pas.

    Trois issues possibles, et les trois sont des informations :

    - `None` quand la cible n'est pas atteinte même au plafond de stockage ;
    - `TAU_MIN` quand elle l'est déjà au plancher — la vraie exigence est peut-être plus
      basse, c'est une censure, signalée par `censure` ;
    - une valeur encadrée à `tol` près sinon.
    """
    haut_cote, _, _, _ = trancher(evaluateur, postes, c_a2, TAU_MAX, cible,
                                  reps_min, reps_max)
    if haut_cote < 0:
        return {"tau": None, "censure": "inatteignable", "pas": 0, "indecis": 0,
                "largeur": math.nan}
    bas_cote, _, _, _ = trancher(evaluateur, postes, c_a2, TAU_MIN, cible,
                                 reps_min, reps_max)
    if bas_cote > 0:
        return {"tau": TAU_MIN, "censure": "plancher", "pas": 0, "indecis": 0,
                "largeur": math.nan}

    bas, haut, pas, indecis = TAU_MIN, TAU_MAX, 0, 0
    while haut - bas > tol:
        milieu = (bas + haut) / 2
        verdict, _, _, decide = trancher(evaluateur, postes, c_a2, milieu, cible,
                                         reps_min, reps_max)
        pas += 1
        indecis += not decide
        if verdict > 0:
            haut = milieu
        else:
            bas = milieu
    return {"tau": (bas + haut) / 2, "censure": None, "pas": pas, "indecis": indecis,
            "largeur": haut - bas}


def diagnostic_pente(evaluateur, postes, c_a2, bas, haut, reps) -> dict:
    """Pente locale dβ/dτ, gain réel des nombres aléatoires communs, et budget qui en découle.

    C'est le diagnostic qui dit **pourquoi** la frontière est difficile à situer, quelle que
    soit la méthode. Trois chiffres :

    - la **pente** : de combien β monte par jour de couverture supplémentaire. Si elle est
      faible, une grande incertitude sur τ* correspond à une petite incertitude sur β, et
      aucune astuce d'échantillonnage n'y changera rien ;
    - le **gain des nombres aléatoires communs** : le rapport entre la demi-largeur qu'on
      aurait avec des tirages indépendants et celle de la différence appariée. Mesuré, pas
      supposé, parce qu'il dépend entièrement de la façon dont le modèle consomme l'aléa :
      avec un seul générateur partagé il valait × 1,1, avec un générateur par source
      (`Streams`) il vaut × 7. **Ce gain ne profite pourtant pas à la dichotomie**, qui
      compare β à une constante et non à une autre configuration ;
    - le **budget** : les réplications qu'il faudrait par évaluation pour situer τ* à un jour
      près, déduit de la pente et de la variance observée.
    """
    serie_bas = evaluateur.betas(postes, c_a2, bas, reps)
    serie_haut = evaluateur.betas(postes, c_a2, haut, reps)
    e_bas, e_haut = estimate(serie_bas), estimate(serie_haut)
    appariee = estimate([h - b for h, b in zip(serie_haut, serie_bas, strict=True)])
    independante = math.sqrt(e_bas.half_width**2 + e_haut.half_width**2)
    pente = appariee.mean / (haut - bas) if haut > bas else math.nan
    budget = (reps * (e_haut.half_width / pente) ** 2
              if pente and math.isfinite(pente) and pente > 0 else math.nan)
    return {
        "pente": pente,
        "demi_largeur_appariee": appariee.half_width,
        "demi_largeur_independante": independante,
        "gain_crn": independante / appariee.half_width if appariee.half_width else math.nan,
        "reps_pour_un_jour": budget,
        "reps": reps,
    }


def isotone(valeurs: list[float]) -> list[float]:
    """Projection croissante la plus proche au sens des moindres carrés (algorithme PAVA).

    « Pool adjacent violators » : tant qu'un bloc est plus bas que le précédent, les deux
    fusionnent et prennent leur moyenne. Le résultat est la suite croissante qui s'écarte le
    moins des valeurs d'origine — donc la correction minimale qui rétablisse une monotonie
    que la physique impose.
    """
    blocs: list[tuple[float, int]] = []  # (moyenne, effectif)
    for valeur in valeurs:
        blocs.append((valeur, 1))
        while len(blocs) > 1 and blocs[-2][0] > blocs[-1][0]:
            (m1, n1), (m2, n2) = blocs[-2], blocs[-1]
            blocs[-2:] = [((m1 * n1 + m2 * n2) / (n1 + n2), n1 + n2)]
    return [moyenne for moyenne, effectif in blocs for _ in range(effectif)]


def corriger_monotonie(points, tolerance=1e-9) -> dict:
    """Applique la régression isotone par (k, β) et compte ce qu'il a fallu corriger.

    Le nombre de corrections est la mesure de bruit qu'on compare aux 28 % de la grille.
    """
    corriges, corrections, total = [], 0, 0
    violations, paires, reculs = 0, 0, []
    for postes in sorted({p["k"] for p in points}):
        for beta in sorted({p["beta"] for p in points}):
            serie = sorted(
                (p for p in points if p["k"] == postes and p["beta"] == beta),
                key=lambda p: p["c_a2"],
            )
            if not serie:
                continue
            avant = [p["tau"] for p in serie]
            apres = isotone(avant)
            for a, b in pairwise(avant):
                paires += 1
                if b < a:
                    violations += 1
                    reculs.append(a - b)
            for point, valeur in zip(serie, apres, strict=True):
                total += 1
                if abs(valeur - point["tau"]) > tolerance:
                    corrections += 1
                corriges.append({**point, "tau": valeur, "tau_brut": point["tau"]})
    return {
        "points": corriges,
        "corrections": corrections,
        "total": total,
        "part_corrigee": corrections / total if total else math.nan,
        "violations": violations,
        "paires": paires,
        "part_violee": violations / paires if paires else math.nan,
        "recul_moyen": sum(reculs) / len(reculs) if reculs else 0.0,
        "recul_max": max(reculs) if reculs else 0.0,
        "ecart_max": max((abs(p["tau"] - p["tau_brut"]) for p in corriges), default=0.0),
    }


def cout_de_la_grille(c_a2_values=C_A2_VALUES, tau_days=TAU_DAYS, postes_values=POSTES,
                      reps=GRILLE_REPS) -> dict:
    """Coût de la méthode concurrente, dans la même unité : une réplication d'un an.

    La grille paie son domaine en entier, y compris les cases très loin de la frontière, et
    elle le paie avant de savoir où la frontière passe. C'est la seule chose que la dichotomie
    lui reproche de façon chiffrable.
    """
    cases = len(c_a2_values) * len(tau_days) * len(postes_values)
    return {"cases": cases, "reps": reps, "annees": cases * reps}


def comparaison_methodes(annees_directe, points, monotonie, grille=None) -> dict:
    """Coût de calcul et bruit résiduel des deux méthodes, côte à côte.

    Les deux colonnes ne décrivent pas le même objet : la grille rend une carte complète dont
    la frontière est un sous-produit interpolé, la dichotomie rend la frontière seule. La
    comparaison n'est donc juste que sur ce qu'on voulait obtenir — la position de τ*.
    """
    grille = grille or cout_de_la_grille()
    return {
        "annees_directe": annees_directe,
        "annees_grille": grille["annees"],
        "rapport_cout": annees_directe / grille["annees"] if grille["annees"] else math.nan,
        "points": len(points),
        "part_violee_directe": monotonie["part_violee"],
        "part_violee_grille": GRILLE_PART_VIOLEE,
        "recul_moyen_directe": monotonie["recul_moyen"],
        "recul_moyen_grille": GRILLE_RECUL_MOYEN,
    }


def construire_frontiere(evaluateur, betas, c_a2_values, postes_values,
                         reps_min, reps_max, tol, progress=False) -> list[dict]:
    """Toute la frontière : un τ* par (k, β, c_a²), les cas inatteignables écartés."""
    points = []
    for postes in postes_values:
        for beta in betas:
            for c_a2 in c_a2_values:
                resultat = tau_etoile(evaluateur, postes, c_a2, beta,
                                      reps_min, reps_max, tol)
                if progress:
                    tau = resultat["tau"]
                    etiquette = "inatteignable" if tau is None else f"{tau:5.1f} j"
                    print(f"  k={postes} β={beta:.2f} c_a²={c_a2:.2f} → {etiquette}"
                          f"  ({resultat['pas']} pas, "
                          f"{evaluateur.annees_simulees:.0f} années cumulées)")
                if resultat["tau"] is None or resultat["censure"] == "plancher":
                    continue
                points.append({"k": postes, "beta": beta, "c_a2": c_a2,
                               "tau": resultat["tau"], "pas": resultat["pas"],
                               "indecis": resultat["indecis"]})
    return points


def to_table(points) -> pa.Table:
    """Frontière en table Parquet, pour la réajuster sans tout recalculer."""
    colonnes = {cle: [p[cle] for p in points]
                for cle in ("k", "beta", "c_a2", "tau")}
    colonnes["tau_brut"] = [p.get("tau_brut", p["tau"]) for p in points]
    return pa.table(colonnes)


def from_table(table: pa.Table) -> list[dict]:
    donnees = table.to_pydict()
    return [
        {"k": int(donnees["k"][i]), "beta": float(donnees["beta"][i]),
         "c_a2": float(donnees["c_a2"][i]), "tau": float(donnees["tau"][i]),
         "tau_brut": float(donnees["tau_brut"][i])}
        for i in range(table.num_rows)
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--reps-min", type=int, default=DEFAULT_REPS_MIN)
    parser.add_argument("--reps-max", type=int, default=DEFAULT_REPS_MAX)
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--annees", type=float, default=1.0)
    parser.add_argument("--reps-diagnostic", type=int, default=120,
                        help="réplications du diagnostic de pente (hors dichotomie)")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    shares = storage_shares(raw)
    base = SimConfig.from_scenario(load_scenario(args.scenario))
    evaluateur = Evaluateur(base, raw, shares, seed=args.seed, annees=args.annees)

    print(f"scénario {args.scenario}, dichotomie sur τ ∈ [{TAU_MIN:.0f} ; {TAU_MAX:.0f}] j, "
          f"tolérance {args.tol:g} j")
    print(f"  niveaux visés : {', '.join(f'{b:.2f}' for b in BETAS)}")
    print(f"  réplications {args.reps_min} à {args.reps_max}, nombres aléatoires communs "
          f"(graines {args.seed}+i)")
    print(f"  consommation de référence : "
          f"{consommation_journaliere_t(base):,.0f} t/jour\n".replace(",", " "))

    points = construire_frontiere(evaluateur, BETAS, C_A2_VALUES, POSTES,
                                  args.reps_min, args.reps_max, args.tol, progress=True)
    if not points:
        print("aucun point de frontière exploitable")
        return 1

    monotonie = corriger_monotonie(points)
    args.table.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(to_table(monotonie["points"]), args.table)

    print(f"\n{'=' * 86}\nFrontière obtenue\n{'=' * 86}")
    print(f"  {len(points)} points, {evaluateur.annees_simulees:.0f} années simulées au total")
    print(f"  soit {evaluateur.annees_simulees / len(points):.0f} années par point de "
          "frontière")

    print(f"\n{'=' * 86}\nMonotonie en c_a² (le bruit résiduel)\n{'=' * 86}")
    print(f"  avant correction : {monotonie['violations']} paires reculantes sur "
          f"{monotonie['paires']} ({monotonie['part_violee']:.0%})")
    print(f"  régression isotone : {monotonie['corrections']} points corrigés sur "
          f"{monotonie['total']} ({monotonie['part_corrigee']:.0%})")
    print(f"  correction maximale : {monotonie['ecart_max']:.2f} j")
    print(f"  recul moyen avant correction : {monotonie['recul_moyen']:.1f} j "
          f"(au pire {monotonie['recul_max']:.1f} j)")

    print_comparaison(comparaison_methodes(evaluateur.annees_simulees, points, monotonie),
                      monotonie)

    print(f"\n{'=' * 86}\nPourquoi la frontière est difficile à situer\n{'=' * 86}")
    return _main_suite(evaluateur, args)


def print_comparaison(comparaison, monotonie) -> None:
    """Le tableau des deux méthodes et sa lecture, séparés du calcul pour être vérifiables."""
    print(f"\n{'=' * 86}\nCoût de calcul contre précision : dichotomie ou grille ?\n{'=' * 86}")
    grille = cout_de_la_grille()
    print(f"  {'':30s}{'dichotomie':>14s}{'grille':>14s}")
    print(f"  {'années simulées':30s}{comparaison['annees_directe']:>14.0f}"
          f"{comparaison['annees_grille']:>14d}")
    print(f"  {'cases ou dichotomies payées':30s}{comparaison['points']:>14d}"
          f"{grille['cases']:>14d}")
    print(f"  {'paires reculantes':30s}{comparaison['part_violee_directe']:>14.0%}"
          f"{comparaison['part_violee_grille']:>14.0%}")
    print(f"  {'recul moyen (j)':30s}{comparaison['recul_moyen_directe']:>14.1f}"
          f"{comparaison['recul_moyen_grille']:>14.1f}")
    moins_de_reculs = comparaison["part_violee_directe"] < comparaison["part_violee_grille"]
    reculs_plus_gros = comparaison["recul_moyen_directe"] > comparaison["recul_moyen_grille"]
    print(f"\n  La dichotomie coûte {comparaison['rapport_cout']:.0%} du calcul de la grille.")
    if moins_de_reculs and reculs_plus_gros:
        print("  Sur le bruit, elle échange un défaut contre un autre : **moins de paires")
        print("  reculantes, mais des reculs plus gros**. Il n'y a donc pas de gain de")
        print("  précision à annoncer, seulement un gain d'efficacité.")
    elif moins_de_reculs:
        print("  Elle rend aussi une frontière un peu plus propre — moins de paires reculantes")
        print("  et des reculs plus petits — mais l'écart reste petit devant son incertitude.")
    else:
        print("  Sur le bruit, elle ne fait pas mieux que la grille.")
    print("  **Elle est donc plus efficace, pas plus précise**, et cela ne change aucune")
    print("  conclusion : la frontière reste floue à une dizaine de jours près, par les deux")
    print("  chemins. Deux réserves à ne pas masquer : la grille rend une carte complète dont")
    print("  la frontière n'est qu'un sous-produit, et le bruit de la dichotomie n'est mesuré")
    print(f"  que sur {monotonie['paires']} paires — l'écart entre "
          f"{monotonie['part_violee']:.0%} et {GRILLE_PART_VIOLEE:.0%} n'est pas")
    print("  significatif à cet effectif.")
    print("\n  La précision se juge en bout de chaîne, sur ce que le métamodèle arrive à")
    print(f"  transposer : {GRILLE_ERREUR_METAMODELE:.1f} j d'erreur avec la grille. Pour le "
          "chiffre de cette\n  frontière-ci : python scripts/metamodele_frontiere.py --directe")


def _main_suite(evaluateur, args) -> int:
    """Diagnostic de pente et épilogue : la fin de `main`, isolée pour la garder lisible."""
    diag = diagnostic_pente(evaluateur, 1, 0.75, 40.0, 60.0, args.reps_diagnostic)
    print(f"  Mesuré à c_a² = 0,75, entre 40 et 60 jours, sur {diag['reps']} réplications :")
    print(f"    pente dβ/dτ                    {diag['pente']:.5f} par jour")
    print(f"    demi-largeur, tirages appariés {diag['demi_largeur_appariee']:.4f}")
    print(f"    demi-largeur, si indépendants  {diag['demi_largeur_independante']:.4f}")
    print(f"    gain des nombres communs       ×{diag['gain_crn']:.1f}")
    print(f"    réplications pour ±1 jour      ~{diag['reps_pour_un_jour']:.0f} par évaluation")
    print("\n  Lecture : près de la frontière, β ne monte que de ~0,1 point par jour de")
    print("  couverture. Situer τ* au jour près demande donc de résoudre β à cette finesse,")
    print("  ce qui coûte des milliers de réplications par évaluation. **La difficulté est")
    print("  dans la platitude de β(τ), pas dans la méthode** : ni la grille ni la dichotomie")
    print("  n'y échappent. Le gain des nombres aléatoires communs, lui, est mesuré et non")
    print("  supposé : il valait ×1,1 avec un générateur unique partagé par toutes les")
    print("  sources d'aléa, et ×7 depuis qu'il y en a un par source. **Mais il ne profite")
    print("  pas à cette dichotomie**, qui compare β à une constante — la variance de")
    print("  β − constante est celle de β, et aucun appariement ne la réduit. Le gain sert")
    print("  les comparaisons entre deux configurations, donc tout le reste du projet.")

    print(f"\n  Frontière écrite : {args.table}")
    print("  Réajuster le métamodèle dessus : "
          "python scripts/metamodele_frontiere.py --directe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
