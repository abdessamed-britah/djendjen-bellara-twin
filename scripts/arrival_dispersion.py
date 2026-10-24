"""Première estimation de c_a², l'irrégularité des arrivées, sur les bilans publics du port.

`simulation.dispersion_arrivees` est de statut `estimé` depuis le début du projet, et c'est
l'un des deux paramètres qui gouvernent tous les résultats (voir la frontière de service du
README). La série de situations portuaires ne contient que 4 arrivées de minéraliers : trop
peu. Les bilans de trafic du port, eux, remontent à 2022 et publient un tonnage de pellets
par période cumulée — de quoi compter des navires sur 16 trimestres.

**La chaîne de déduction, et ce qu'elle coûte en hypothèses.**

1. les pages publient des **cumuls depuis le 1er janvier** ; un trimestre s'obtient par
   différence de deux cumuls consécutifs. Chaque cumul étant publié sur deux pages (comme
   colonne de droite, puis comme colonne de gauche l'année suivante), la lecture est
   vérifiée par recoupement — ce script refuse de continuer si deux publications d'un même
   cumul diffèrent ;
2. le nombre de navires d'un trimestre est estimé par **tonnage ÷ taille moyenne d'un
   minéralier** (`navires_minerai.taille_moyenne`, 142 700 t, statut `observé` sur 5
   navires). Ce n'est pas un comptage : c'est une division ;
3. l'**indice de dispersion** I = Var(N)/E[N] vaut 1 pour un processus de Poisson, 0 pour
   des arrivées parfaitement régulières, et plus que 1 pour des arrivées groupées. Pour un
   processus de renouvellement, l'indice de dispersion des comptages tend vers c_a², le
   carré du coefficient de variation des intervalles — c'est ce qui autorise à lire I comme
   une estimation de c_a², **asymptotiquement**.

**Les quatre raisons de ne pas le prendre pour une mesure**, toutes écrites dans la sortie :

- *la consommation varie aussi*. Un trimestre à faible tonnage peut signifier des arrivées
  irrégulières **ou** une aciérie à l'arrêt. Rien ici ne les distingue, et I attribue aux
  arrivées toute la variance, d'où une **surestimation** probable de c_a² ;
- *16 trimestres font une petite série*, et l'intervalle de confiance par bootstrap le dit ;
- *la fenêtre trimestrielle est large*. Avec ~5 navires par trimestre, le régime asymptotique
  qui fonde I ≈ c_a² n'est pas atteint. Un trimestre lisse aussi les rafales de quelques
  jours, que la simulation, elle, voit : I sous-estime cette irrégularité-là ;
- *le tonnage n'est pas un comptage*. Un navire partiellement déchargé, ou un gabarit
  différent, se traduit en fraction de navire.

Usage : python scripts/arrival_dispersion.py [--bootstrap 10000] [--seed 20260924]
"""

import argparse
import math
import random
import statistics
import sys
from pathlib import Path

from corridor.ingest.port_stats import CUMULS, DEFAULT_ROOT, PAGES
from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.transform.parse_port_stats import pellets_row, read_page

DEFAULT_BOOTSTRAP = 10_000
DEFAULT_SEED = 20260924
#: Un trimestre dont le tonnage tombe sous cette part de la moyenne est signalé à part.
SEUIL_ANOMALIE = 0.1


def lire_cumuls(root: Path = DEFAULT_ROOT, pages=PAGES) -> tuple[dict, list[str]]:
    """Tonnage de pellets cumulé par (année, cumul), et les libellés rencontrés.

    Chaque page donne deux cumuls : l'année de gauche et celle de droite. Un cumul publié
    deux fois avec deux valeurs différentes fait lever — c'est le seul recoupement dont on
    dispose, autant qu'il serve.
    """
    cumules: dict[tuple[int, str], float] = {}
    libelles: list[str] = []
    for page in pages:
        chemin = Path(root) / f"{page.annee_droite}_{page.cumul}_{page.slug}.html"
        if not chemin.exists():
            raise FileNotFoundError(
                f"page absente : {chemin}. Lancer `python -m corridor.ingest.port_stats`."
            )
        ligne = pellets_row(read_page(chemin))
        if ligne is None:
            raise ValueError(f"aucune ligne « pellets » dans {chemin}")
        libelles.append(ligne.label)
        for annee, valeur in ((page.annee_gauche, ligne.gauche),
                              (page.annee_droite, ligne.droite)):
            if valeur is None:
                continue
            cle = (annee, page.cumul)
            if cle in cumules and cumules[cle] != valeur:
                raise ValueError(
                    f"deux publications du cumul {cle} se contredisent : "
                    f"{cumules[cle]:.0f} puis {valeur:.0f} ({chemin.name})"
                )
            cumules[cle] = valeur
    return cumules, libelles


def trimestres(cumules: dict) -> list[tuple[int, int, float]]:
    """Tonnage par trimestre isolé : (année, trimestre, tonnes), trié.

    Le cumul T1 **est** le premier trimestre ; les suivants sont des différences. Une année
    n'est retenue que si ses quatre cumuls sont publiés, sinon ses trimestres seraient faux.
    """
    annees = sorted({annee for annee, _ in cumules})
    series = []
    for annee in annees:
        if not all((annee, c) in cumules for c in CUMULS):
            continue
        ordre = sorted(CUMULS, key=lambda c: CUMULS[c])
        precedent = 0.0
        for trimestre, cumul in enumerate(ordre, start=1):
            valeur = cumules[(annee, cumul)]
            series.append((annee, trimestre, valeur - precedent))
            precedent = valeur
    return series


def index_of_dispersion(valeurs) -> float:
    """I = Var(N)/E[N] : 0 = régulier, 1 = Poisson, > 1 = groupé. nan si la moyenne est nulle."""
    valeurs = list(valeurs)
    if len(valeurs) < 2:
        return math.nan
    moyenne = statistics.fmean(valeurs)
    if moyenne <= 0:
        return math.nan
    return statistics.variance(valeurs) / moyenne


def bootstrap_ci(valeurs, tirages: int, rng: random.Random, niveau: float = 0.95):
    """Intervalle de confiance de I par bootstrap percentile, et les tirages eux-mêmes.

    Rééchantillonne les trimestres avec remise. Avec 16 points, l'intervalle est large :
    c'est précisément ce qu'il faut montrer.
    """
    valeurs = list(valeurs)
    if len(valeurs) < 2:
        return math.nan, math.nan, []
    echantillons = []
    for _ in range(tirages):
        tirage = [rng.choice(valeurs) for _ in valeurs]
        indice = index_of_dispersion(tirage)
        if not math.isnan(indice):
            echantillons.append(indice)
    echantillons.sort()
    if not echantillons:
        return math.nan, math.nan, []
    marge = (1.0 - niveau) / 2.0
    bas = echantillons[max(int(marge * len(echantillons)) - 1, 0)]
    haut = echantillons[min(int((1 - marge) * len(echantillons)), len(echantillons) - 1)]
    return bas, haut, echantillons


def anomalies(series) -> list[tuple[int, int, float]]:
    """Trimestres dont le tonnage est dérisoire devant la moyenne : à signaler, pas à retirer."""
    tonnages = [t for _, _, t in series]
    if not tonnages:
        return []
    moyenne = statistics.fmean(tonnages)
    return [(a, q, t) for a, q, t in series if t < SEUIL_ANOMALIE * moyenne]


def _fr(valeur, digits=0):
    return f"{valeur:,.{digits}f}".replace(",", " ")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    taille = float(raw["navires_minerai"]["taille_moyenne"]["value"])

    cumules, libelles = lire_cumuls(args.root)
    series = trimestres(cumules)
    print(f"{len(series)} trimestres reconstruits depuis {len(PAGES)} bilans publiés, "
          f"tous recoupés sans contradiction.")
    etiquettes = sorted(set(libelles))
    print(f"Libellés du poste rencontrés dans la source : {', '.join(etiquettes)}")
    print(f"Taille moyenne de minéralier retenue : {_fr(taille)} t "
          f"({raw['navires_minerai']['taille_moyenne']['status']}).\n")

    print(f"{'période':>10} {'pellets (t)':>14} {'navires estimés':>17}")
    navires = []
    for annee, trimestre, tonnes in series:
        n = tonnes / taille
        navires.append(n)
        print(f"{annee} T{trimestre:<4} {_fr(tonnes):>14} {n:>17.2f}")

    moyenne = statistics.fmean(navires)
    ecart = statistics.stdev(navires)
    indice = index_of_dispersion(navires)
    rng = random.Random(args.seed)
    bas, haut, _ = bootstrap_ci(navires, args.bootstrap, rng)

    print(f"\n{'=' * 78}\nIndice de dispersion\n{'=' * 78}")
    print(f"  navires par trimestre : moyenne {moyenne:.2f}, écart-type {ecart:.2f}")
    print(f"  I = Var(N)/E[N]       : {indice:.2f}")
    print(f"  IC 95 % (bootstrap, {args.bootstrap} tirages) : [{bas:.2f} ; {haut:.2f}]")
    print("  lecture : 0 = arrivées régulières, 1 = Poisson, > 1 = arrivées groupées")

    suspects = anomalies(series)
    if suspects:
        print(f"\n{'=' * 78}\nTrimestre(s) à signaler\n{'=' * 78}")
        for annee, trimestre, tonnes in suspects:
            print(f"  {annee} T{trimestre} : {_fr(tonnes)} t, soit {tonnes / taille:.2f} navire. "
                  "Un trimestre quasi nul.")
        sans = [n for (a, q, t), n in zip(series, navires, strict=True)
                if (a, q, t) not in suspects]
        indice_sans = index_of_dispersion(sans)
        bas2, haut2, _ = bootstrap_ci(sans, args.bootstrap, random.Random(args.seed))
        print(f"\n  Sans ce(s) trimestre(s) : I = {indice_sans:.2f} "
              f"[{bas2:.2f} ; {haut2:.2f}] sur {len(sans)} trimestres.")
        print("  Ces trimestres ne sont **pas** retirés du résultat publié : rien ne dit "
              "qu'ils\n  soient des erreurs de la source plutôt que des arrêts réels de "
              "l'aciérie, et les\n  retirer reviendrait à choisir la réponse. L'écart entre "
              "les deux valeurs mesure\n  à lui seul la fragilité de l'estimation.")

    print(f"\n{'=' * 78}\nCe que ce chiffre ne vaut pas\n{'=' * 78}")
    print("  1. La consommation de l'aciérie varie aussi. Un trimestre creux peut venir "
          "d'arrivées\n     irrégulières ou d'un four à l'arrêt : I met toute la variance "
          "sur les arrivées,\n     donc il surestime probablement c_a².")
    print("  2. 16 trimestres, et l'IC du bootstrap le dit : l'intervalle couvre "
          f"{haut - bas:.1f} point(s).")
    print("  3. Une fenêtre trimestrielle lisse les rafales de quelques jours, que la "
          "simulation\n     voit : de ce côté-là, I sous-estime l'irrégularité.")
    print("  4. Le nombre de navires est un tonnage divisé par une taille moyenne, pas un "
          "comptage.")
    print("\n  Statut proposé : `estimé`. Ce n'est pas une mesure de c_a², c'est le premier "
          "ordre\n  de grandeur chiffré dont le projet dispose, et il remplace une valeur "
          "sans source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
