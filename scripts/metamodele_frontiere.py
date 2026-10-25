"""Métamodèle de la frontière de service : une formule à la place d'une carte.

La frontière du README répond à « quelle couverture de stock pour tenir β ? » en lisant une
carte calculée en 25 minutes de simulation. Pour qu'un autre site s'en serve sans rejouer le
modèle, il faudrait une **formule**. Ce script cherche si une forme simple tient, et conclut
honnêtement si aucune ne tient.

**La forme candidate, et pourquoi elle.** La théorie des files donne l'attente d'un G/G/1
comme un produit d'un facteur d'utilisation ρ/(1−ρ), d'un facteur de variabilité
(c_a² + c_s²)/2 et d'un temps de service. Le stock nécessaire pour absorber cette attente
devrait croître avec la variabilité et exploser quand on s'approche du service parfait, d'où

    τ*(c_a², β) ≈ a + b · c_a² / (1 − β)^c

`a` est le stock de base d'un corridor parfaitement régulier, `b · c_a²` la part due à
l'irrégularité, et `(1 − β)^-c` l'explosion quand β tend vers 1 — exactement le rôle que
joue 1/(1−ρ) dans la file d'attente. **La forme est guidée par la file, elle n'en découle
pas** : τ* n'est pas une attente, c'est un stock, et le corridor contient un rail et un four
que Kingman ignore.

**Le modèle souple de comparaison** est une régression linéaire sur une base de fonctions
plus riche — termes croisés et quadratiques en c_a² et en u = 1/(1−β). Il n'a aucune
interprétation ; il sert de référence : si la forme structurelle fait presque aussi bien, sa
simplicité est gagnée gratuitement, sinon elle est fausse.

**Validation.** Les deux modèles sont ajustés sur une partie des points et mesurés sur
l'autre, de deux façons :

- un **partage aléatoire** 70/30, qui mesure l'interpolation ;
- un **retrait d'une colonne entière de c_a²**, bien plus sévère : le modèle doit prédire
  une irrégularité qu'il n'a jamais vue. C'est le seul test qui dise si la formule se
  transpose, et c'est précisément ce qu'on lui demande.

**Trois limites qui s'appliquent avant même de lire les erreurs.**

- Les τ* ne sont pas des mesures indépendantes : ils sont **lus sur la même carte**, par
  interpolation entre des β moyennés sur 60 réplications. Ils héritent donc de son bruit,
  et deux τ* voisins sont corrélés.
- Les points au **plancher de la grille** (τ = 5 jours, atteint à c_a² = 0) sont censurés :
  la vraie exigence est peut-être plus basse. Ils sont écartés, pas ajustés.
- Le domaine est petit : 7 valeurs de c_a², quelques niveaux de β. Une formule qui tient
  ici n'est pas validée au-delà.

Usage : python scripts/metamodele_frontiere.py [--table data/sim/frontiere_service.parquet]
"""

import argparse
import math
import random
import sys
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # aucune fenêtre : on écrit un fichier
import matplotlib.pyplot as plt
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from frontiere_service import TAU_DAYS, couverture_requise, from_table

DEFAULT_TABLE = Path("data/sim/frontiere_service.parquet")
#: Frontière estimée directement par dichotomie, alternative à la lecture sur grille.
TABLE_DIRECTE = Path("data/sim/frontiere_directe.parquet")
DEFAULT_FIGURE = Path("docs/metamodele_frontiere.png")
#: Figure de la variante ajustée sur la frontière par dichotomie, pour ne pas écraser l'autre.
FIGURE_DIRECTE = Path("docs/metamodele_frontiere_directe.png")
DEFAULT_SEED = 20260924
#: Niveaux de service pour lesquels on inverse la carte. Au-delà de 0,98, presque aucun
#: point n'est atteignable et le jeu de données se viderait.
BETAS = (0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97)
#: Exposants balayés pour `c` dans la forme structurelle.
EXPOSANTS = tuple(round(0.05 * k, 2) for k in range(1, 81))
COULEUR_STRUCTUREL = "#1f77b4"
COULEUR_SOUPLE = "#e8710a"


def dataset(grilles, betas=BETAS) -> list[dict]:
    """Points (k, c_a², β) → τ*, lus en inversant la carte pour chaque niveau de service.

    Les points au plancher de la grille sont **censurés** : la carte ne descend pas plus
    bas, donc « τ* = 5 jours » veut dire « au plus 5 jours ». Les garder reviendrait à
    ajuster le modèle sur une valeur qu'on sait fausse, et dans un coin qui pèse lourd
    (c'est là que la formule est le plus contrainte).
    """
    plancher = min(TAU_DAYS)
    points = []
    for postes, grille in grilles.items():
        for beta in betas:
            for c_a2, tau in couverture_requise(grille, beta).items():
                if tau is None or tau <= plancher:
                    continue
                points.append({"k": postes, "c_a2": c_a2, "beta": beta, "tau": tau})
    return points


def points_directs(table) -> list[dict]:
    """Points de frontière estimés par dichotomie, dans le format attendu par l'ajustement.

    Même structure que `dataset`, mais sans inversion ni censure : la dichotomie a déjà
    rendu des τ* encadrés, et les cas inatteignables ou au plancher en ont été retirés.
    """
    donnees = table.to_pydict()
    return [
        {"k": int(donnees["k"][i]), "c_a2": float(donnees["c_a2"][i]),
         "beta": float(donnees["beta"][i]), "tau": float(donnees["tau"][i]),
         "tau_brut": float(donnees["tau_brut"][i])}
        for i in range(table.num_rows)
    ]


def _ols(xs, ys) -> tuple[float, float]:
    """Droite des moindres carrés y = a + b·x, en forme fermée."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def _resoudre(matrice, second_membre) -> list[float]:
    """Système linéaire par élimination de Gauss avec pivot partiel.

    Écrit à la main plutôt que tiré d'une bibliothèque : le système fait au plus six
    inconnues, et le projet s'interdit une dépendance de plus pour si peu.
    """
    n = len(second_membre)
    a = [list(ligne) + [second_membre[i]] for i, ligne in enumerate(matrice)]
    for colonne in range(n):
        pivot = max(range(colonne, n), key=lambda r: abs(a[r][colonne]))
        if abs(a[pivot][colonne]) < 1e-12:
            raise ValueError("système singulier : base de fonctions redondante")
        a[colonne], a[pivot] = a[pivot], a[colonne]
        for ligne in range(colonne + 1, n):
            facteur = a[ligne][colonne] / a[colonne][colonne]
            for k in range(colonne, n + 1):
                a[ligne][k] -= facteur * a[colonne][k]
    solution = [0.0] * n
    for colonne in reversed(range(n)):
        reste = a[colonne][n] - sum(a[colonne][k] * solution[k] for k in range(colonne + 1, n))
        solution[colonne] = reste / a[colonne][colonne]
    return solution


def fit_structurel(points, exposants=EXPOSANTS) -> dict:
    """Ajuste τ* = a + b·c_a²/(1−β)^c, par balayage sur `c` et moindres carrés sur (a, b).

    Deux paramètres linéaires et un seul non linéaire : un balayage suffit, et il évite
    d'embarquer un optimiseur pour trois inconnues.
    """
    meilleur = None
    for c in exposants:
        xs = [p["c_a2"] / (1.0 - p["beta"]) ** c for p in points]
        a, b = _ols(xs, [p["tau"] for p in points])
        erreurs = [a + b * x - p["tau"] for x, p in zip(xs, points, strict=True)]
        sse = sum(e * e for e in erreurs)
        if meilleur is None or sse < meilleur["sse"]:
            meilleur = {"a": a, "b": b, "c": c, "sse": sse}
    return meilleur


def predire_structurel(modele, point) -> float:
    return modele["a"] + modele["b"] * point["c_a2"] / (1.0 - point["beta"]) ** modele["c"]


def _base_souple(point) -> list[float]:
    """Base de fonctions du modèle souple : constante, c_a², u, croisé, carrés.

    u = 1/(1−β) est la grandeur qui explose près du service parfait ; lui donner un terme
    quadratique laisse au modèle la liberté de courber autrement que la forme structurelle.
    """
    c_a2 = point["c_a2"]
    u = 1.0 / (1.0 - point["beta"])
    return [1.0, c_a2, u, c_a2 * u, u * u, c_a2 * c_a2]


def fit_souple(points, base=_base_souple) -> dict:
    """Régression linéaire sur une base plus riche, par équations normales."""
    colonnes = len(base(points[0]))
    matrice = [[0.0] * colonnes for _ in range(colonnes)]
    second = [0.0] * colonnes
    for point in points:
        phi = base(point)
        for i in range(colonnes):
            second[i] += phi[i] * point["tau"]
            for j in range(colonnes):
                matrice[i][j] += phi[i] * phi[j]
    return {"coefficients": _resoudre(matrice, second), "base": base}


def predire_souple(modele, point) -> float:
    phi = modele["base"](point)
    return sum(c * x for c, x in zip(modele["coefficients"], phi, strict=True))


def rmse(points, predire, modele) -> float:
    """Erreur quadratique moyenne, en jours de couverture."""
    if not points:
        return math.nan
    carres = [(predire(modele, p) - p["tau"]) ** 2 for p in points]
    return math.sqrt(sum(carres) / len(carres))


def mae(points, predire, modele) -> float:
    if not points:
        return math.nan
    return sum(abs(predire(modele, p) - p["tau"]) for p in points) / len(points)


def ecart_type(valeurs) -> float:
    """Écart-type de la série à prédire : la référence devant laquelle juger une erreur."""
    n = len(valeurs)
    if n < 2:
        return math.nan
    moyenne = sum(valeurs) / n
    return math.sqrt(sum((v - moyenne) ** 2 for v in valeurs) / (n - 1))


def partage_aleatoire(points, part=0.7, seed=DEFAULT_SEED):
    """Partage apprentissage / test, mélangé une fois pour toutes."""
    melange = list(points)
    random.Random(seed).shuffle(melange)
    coupe = int(len(melange) * part)
    return melange[:coupe], melange[coupe:]


def validation_par_colonne(points):
    """Retire une valeur de c_a² entière, apprend sur le reste, teste dessus.

    Bien plus sévère que le partage aléatoire : le modèle doit prédire une irrégularité
    jamais vue. C'est le seul test qui réponde à « cette formule se transpose-t-elle ? ».
    """
    for c_a2 in sorted({p["c_a2"] for p in points}):
        test = [p for p in points if p["c_a2"] == c_a2]
        apprentissage = [p for p in points if p["c_a2"] != c_a2]
        if len(apprentissage) > 6 and test:
            yield c_a2, apprentissage, test


def apport_du_nombre_de_postes(points) -> dict:
    """Le métamodèle a-t-il besoin de savoir combien de postes a le terminal ?

    La frontière montre deux courbes superposées ; si c'est vrai, ajouter un terme en k ne
    doit presque rien gagner. C'est une vérification indépendante de ce résultat.
    """
    def base_avec_k(point):
        return [*_base_souple(point), float(point["k"])]

    sans = fit_souple(points)
    erreur_sans = rmse(points, predire_souple, sans)
    if len({p["k"] for p in points}) < 2:
        # une seule configuration de poste : la colonne k est constante, donc colinéaire
        # avec la constante du modèle. Rien à comparer, et le dire vaut mieux que lever.
        return {"rmse_sans_k": erreur_sans, "rmse_avec_k": erreur_sans,
                "coefficient_k": 0.0, "comparable": False}
    avec = fit_souple(points, base=base_avec_k)
    return {
        "rmse_sans_k": erreur_sans,
        "rmse_avec_k": rmse(points, predire_souple, avec),
        "coefficient_k": avec["coefficients"][-1],
        "comparable": True,
    }


def incoherence_de_la_carte(points, cle: str = "tau") -> dict:
    """Mesure le bruit de la carte, par ses propres contradictions internes.

    À nombre de postes et niveau de service fixés, τ* **doit** croître avec c_a² : des
    arrivées plus erratiques ne peuvent pas demander moins de stock. Chaque recul est donc
    du bruit, et son amplitude donne l'ordre de grandeur de l'imprécision des τ*.

    Sans cette mesure, on ne saurait pas lire l'erreur du métamodèle : une erreur de 10
    jours est un échec si les données valent le jour près, et un non-résultat si la carte
    elle-même se contredit de 7 jours.
    """
    violations, total, amplitudes = 0, 0, []
    for k in sorted({p["k"] for p in points}):
        for beta in sorted({p["beta"] for p in points}):
            serie = sorted(
                (p for p in points if p["k"] == k and p["beta"] == beta),
                key=lambda p: p["c_a2"],
            )
            for avant, apres in pairwise(serie):
                total += 1
                if apres[cle] < avant[cle]:
                    violations += 1
                    amplitudes.append(avant[cle] - apres[cle])
    return {
        "paires": total,
        "violations": violations,
        "part": violations / total if total else math.nan,
        "recul_moyen": sum(amplitudes) / len(amplitudes) if amplitudes else 0.0,
        "recul_max": max(amplitudes) if amplitudes else 0.0,
        "colonne": cle,
    }


def print_rapport(points, structurel, souple, aleatoire, colonnes, apport_k) -> None:
    taus = [p["tau"] for p in points]
    dispersion = ecart_type(taus)
    print(f"\n{'=' * 86}\nJeu de données\n{'=' * 86}")
    print(f"  {len(points)} points (k, c_a², β) → τ*, après retrait des points censurés "
          "au plancher")
    print(f"  τ* va de {min(taus):.1f} à {max(taus):.1f} jours, écart-type {dispersion:.1f}")
    niveaux = sorted({p["beta"] for p in points})
    print(f"  niveaux de β : "
          f"{', '.join(f'{b:.2f}'.replace('.', ',') for b in niveaux)}")

    print(f"\n{'=' * 86}\nAjustement sur la totalité des points\n{'=' * 86}")
    print(f"  structurel  τ* = {structurel['a']:.1f} + {structurel['b']:.2f} · c_a² / "
          f"(1−β)^{structurel['c']:.2f}")
    print(f"              RMSE {rmse(points, predire_structurel, structurel):.2f} j, "
          f"MAE {mae(points, predire_structurel, structurel):.2f} j")
    print(f"  souple      6 termes, RMSE {rmse(points, predire_souple, souple):.2f} j, "
          f"MAE {mae(points, predire_souple, souple):.2f} j")
    print("  (ajustement sur les mêmes points que la mesure : optimiste par construction, "
          "c'est\n  la validation ci-dessous qui compte)")

    print(f"\n{'=' * 86}\nValidation 1 — partage aléatoire 70/30 (interpolation)\n{'=' * 86}")
    apprentissage, test = aleatoire
    ms = fit_structurel(apprentissage)
    mf = fit_souple(apprentissage)
    err_s = rmse(test, predire_structurel, ms)
    err_f = rmse(test, predire_souple, mf)
    print(f"  {len(apprentissage)} points d'apprentissage, {len(test)} de test")
    print(f"  structurel  RMSE {err_s:.2f} j  ({err_s / dispersion:.0%} de l'écart-type)")
    print(f"  souple      RMSE {err_f:.2f} j  ({err_f / dispersion:.0%} de l'écart-type)")

    print(f"\n{'=' * 86}\nValidation 2 — une colonne de c_a² retirée (transposition)\n{'=' * 86}")
    print(f"{'c_a² retiré':>12} {'n test':>7} {'RMSE structurel':>17} {'RMSE souple':>13}")
    totaux_s, totaux_f = [], []
    for c_a2, err_s, err_f, n in colonnes:
        print(f"{c_a2:>12.2f} {n:>7} {err_s:>17.2f} {err_f:>13.2f}")
        totaux_s.append(err_s)
        totaux_f.append(err_f)
    moy_s = sum(totaux_s) / len(totaux_s)
    moy_f = sum(totaux_f) / len(totaux_f)
    print(f"{'moyenne':>12} {'':>7} {moy_s:>17.2f} {moy_f:>13.2f}")
    print(f"\n  Rapportée à l'écart-type des τ* ({dispersion:.1f} j) : "
          f"{moy_s / dispersion:.0%} pour le structurel, {moy_f / dispersion:.0%} "
          "pour le souple.")

    print(f"\n{'=' * 86}\nLe nombre de postes apporte-t-il quelque chose ?\n{'=' * 86}")
    if not apport_k.get("comparable", True):
        print("  Une seule configuration de poste dans le jeu de données : rien à comparer.")
        return
    print(f"  RMSE sans terme en k : {apport_k['rmse_sans_k']:.2f} j")
    print(f"  RMSE avec terme en k : {apport_k['rmse_avec_k']:.2f} j "
          f"(coefficient {apport_k['coefficient_k']:+.2f} j par poste)")
    gain = apport_k["rmse_sans_k"] - apport_k["rmse_avec_k"]
    if gain < 0.5:
        print(f"  Gain : {gain:.3f} j. Le métamodèle n'a pas besoin de savoir combien de "
              "postes a le\n  terminal — confirmation indépendante du plan factoriel.")
    else:
        print(f"  Gain : {gain:.3f} j, le nombre de postes compte.")


def verdict(dispersion, moy_s, moy_f, err_s_alea, err_f_alea, bruit) -> None:
    """Conclusion, y compris le cas où le jeu de données ne permet pas de conclure."""
    print(f"\n{'=' * 86}\nCe que vaut la carte elle-même\n{'=' * 86}")
    print(f"  À k et β fixés, τ* doit croître avec c_a². Sur {bruit['paires']} paires "
          f"consécutives,\n  {bruit['violations']} reculent ({bruit['part']:.0%}), d'un "
          f"recul moyen de {bruit['recul_moyen']:.1f} j (au pire {bruit['recul_max']:.1f} j).")
    print("  Ces reculs sont impossibles physiquement : c'est le bruit de la carte, mesuré "
          "par ses\n  propres contradictions.")
    if bruit.get("colonne") == "tau_brut":
        print("  Mesuré **avant** la régression isotone : la frontière publiée a été rendue")
        print("  croissante, donc la lire telle quelle donnerait 0 % de reculs et un bruit")
        print("  nul par construction. Lisser une contradiction ne la supprime pas — elle")
        print("  reste dans l'incertitude des τ*, et c'est elle qui juge la formule.")

    print(f"\n{'=' * 86}\nVerdict\n{'=' * 86}")
    seuil = 0.5 * dispersion
    tient = moy_s <= seuil
    presque_aussi_bon = moy_s <= 1.25 * moy_f
    # si l'erreur du modèle est du même ordre que le bruit des données, on ne tranche pas
    indecidable = moy_s <= 2.0 * bruit["recul_moyen"]

    if tient and presque_aussi_bon and not indecidable:
        print("  **Une forme simple tient.** La formule structurelle prédit une couverture")
        print(f"  jamais vue à {moy_s:.1f} jour(s) près en moyenne, soit "
              f"{moy_s / dispersion:.0%} de la dispersion des")
        print("  τ*, et elle ne fait pas significativement moins bien que le modèle souple")
        print("  à six termes. Sa simplicité est donc gagnée sans rien perdre.")
    elif indecidable:
        print("  **On ne peut pas trancher : la carte n'est pas assez précise pour juger "
              "une formule.**")
        print(f"  L'erreur de transposition du modèle structurel ({moy_s:.1f} j) est du même "
              "ordre que")
        print(f"  l'incohérence interne de la carte ({bruit['recul_moyen']:.1f} j de recul "
              "moyen là où τ* devrait")
        print("  croître). Le modèle souple à six termes ne fait pas mieux "
              f"({moy_f:.1f} j), ce qui confirme")
        print("  que la limite est dans les données, pas dans la forme choisie.")
        print("\n  Conclusion honnête : **la formule n'est ni validée ni réfutée**. Ce qu'il")
        print("  faudrait pour trancher n'est pas une meilleure formule, c'est une frontière")
        print("  plus précise — et cela se paie en réplications : près de la frontière, β ne")
        print("  monte que de ~0,1 point par jour de couverture, donc situer τ* au jour près")
        print("  demande des milliers de réplications par évaluation (voir frontiere_directe).")
        print("  En l'état, la carte simulée reste le seul moyen fiable de lire la frontière.")
    else:
        print("  **Aucune forme simple ne tient sur ce domaine.** L'erreur de transposition")
        print(f"  du modèle structurel ({moy_s:.1f} j) atteint {moy_s / dispersion:.0%} de "
              "la dispersion des τ*,")
        print(f"  bien au-delà du bruit de la carte ({bruit['recul_moyen']:.1f} j). Le "
              f"modèle souple non plus ({moy_f:.1f} j).")
        print("  L'exigence de stock d'un corridor ne se résume donc pas à une formule de")
        print("  file d'attente : le rail et le four y participent.")
    print(f"\n  Pour mémoire, en interpolation (partage aléatoire) : {err_s_alea:.2f} j "
          f"et {err_f_alea:.2f} j.")
    print("  L'écart entre les deux validations mesure ce que coûte l'extrapolation.")


def donnees_figure(points, structurel, souple) -> dict:
    """Les séries que la figure trace : observés, prédits et résidus, par modèle.

    Le tracé n'a plus qu'à les poser sur deux axes. Séparer les deux permet de vérifier
    dans la boucle courte que la figure dit la vérité — notamment que les résidus sont bien
    « prédit − observé » et non l'inverse, une erreur de signe qu'une image ne trahit pas.
    """
    observes = [p["tau"] for p in points]
    series = {}
    for nom, predire, modele in (
        ("structurel", predire_structurel, structurel),
        ("souple", predire_souple, souple),
    ):
        predits = [predire(modele, p) for p in points]
        series[nom] = {
            "observes": observes,
            "predits": predits,
            "residus": [pr - ob for pr, ob in zip(predits, observes, strict=True)],
            "c_a2": [p["c_a2"] for p in points],
        }
    return series


#: Comment les τ* ont été obtenus. La figure doit le dire : la mise en garde n'est pas la même
#: selon qu'ils sont interpolés sur une grille ou encadrés par dichotomie puis lissés.
ORIGINE_TAU = {
    "grille": (
        "τ* lu sur la carte simulée (jours)",
        ("Les τ* sont lus sur une carte simulée, pas mesurés : ils en héritent le bruit, "
         "et deux points voisins sont corrélés."),
    ),
    "dichotomie": (
        "τ* encadré par dichotomie (jours)",
        ("Les τ* sont encadrés par dichotomie puis rendus croissants par régression "
         "isotone : le bruit est lissé, pas supprimé."),
    ),
}


def write_figure(points, structurel, souple, path=DEFAULT_FIGURE, origine="grille") -> Path:
    """Prédit contre observé, et résidus par c_a² : où la formule se trompe, et comment."""
    abscisse, mise_en_garde = ORIGINE_TAU[origine]
    figure, (gauche, droite) = plt.subplots(1, 2, figsize=(13.0, 6.0),
                                            constrained_layout=True)
    observes = [p["tau"] for p in points]
    series = donnees_figure(points, structurel, souple)
    for nom, couleur, libelle in (
        ("structurel", COULEUR_STRUCTUREL, "structurel : a + b·c_a²/(1−β)^c"),
        ("souple", COULEUR_SOUPLE, "souple : 6 termes"),
    ):
        serie = series[nom]
        gauche.scatter(serie["observes"], serie["predits"], s=28, color=couleur,
                       alpha=0.75, label=libelle, zorder=3)
        droite.scatter(serie["c_a2"], serie["residus"], s=28, color=couleur,
                       alpha=0.75, zorder=3)
    borne = max(observes) * 1.05
    gauche.plot([0, borne], [0, borne], color="0.35", linestyle="--", linewidth=1.2, zorder=2)
    gauche.set_xlabel(abscisse)
    gauche.set_ylabel("τ* prédit par le métamodèle (jours)")
    gauche.set_title("Prédit contre observé", fontweight="bold")
    gauche.legend(fontsize=9)
    droite.axhline(0.0, color="0.35", linestyle="--", linewidth=1.2, zorder=2)
    droite.set_xlabel("c_a² — irrégularité des arrivées")
    droite.set_ylabel("résidu : prédit − observé (jours)")
    droite.set_title("Résidus : la formule se trompe-t-elle toujours au même endroit ?",
                     fontweight="bold")
    for ax in (gauche, droite):
        ax.grid(True, color="0.90", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
    figure.suptitle(
        "Métamodèle de la frontière de service — une formule à la place d'une carte\n"
        "Ajustement sur la totalité des points (optimiste) ; les erreurs qui comptent sont "
        "celles de la validation.\n" + mise_en_garde,
        fontsize=10.5,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--figure", type=Path, default=None,
                        help="par défaut, une figure distincte selon la source ajustée")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--directe", action="store_true",
                        help="ajuster sur la frontière estimée par dichotomie, pas sur la "
                             "grille")
    parser.add_argument("--table-directe", type=Path, default=TABLE_DIRECTE)
    args = parser.parse_args(argv)

    if args.directe:
        if not args.table_directe.exists():
            print(f"frontière directe absente ({args.table_directe}) : "
                  "python scripts/frontiere_directe.py")
            return 1
        points = points_directs(pq.read_table(args.table_directe))
        figure = args.figure or FIGURE_DIRECTE
        print(f"ajustement sur la frontière estimée par dichotomie "
              f"({args.table_directe})")
    else:
        if not args.table.exists():
            print(f"carte de frontière absente ({args.table}) : "
                  "python scripts/frontiere_service.py --reps 60")
            return 1
        points = dataset(from_table(pq.read_table(args.table)))
        figure = args.figure or DEFAULT_FIGURE
        print(f"ajustement sur la carte par grille ({args.table})")
    if len(points) < 20:
        print(f"trop peu de points exploitables ({len(points)}) pour ajuster un métamodèle")
        return 1

    structurel = fit_structurel(points)
    souple = fit_souple(points)
    aleatoire = partage_aleatoire(points, seed=args.seed)

    colonnes = []
    for c_a2, apprentissage, test in validation_par_colonne(points):
        ms = fit_structurel(apprentissage)
        mf = fit_souple(apprentissage)
        colonnes.append((c_a2, rmse(test, predire_structurel, ms),
                         rmse(test, predire_souple, mf), len(test)))

    apport_k = apport_du_nombre_de_postes(points)
    print_rapport(points, structurel, souple, aleatoire, colonnes, apport_k)

    dispersion = ecart_type([p["tau"] for p in points])
    moy_s = sum(e for _, e, _, _ in colonnes) / len(colonnes)
    moy_f = sum(e for _, _, e, _ in colonnes) / len(colonnes)
    apprentissage, test = aleatoire
    verdict(dispersion, moy_s, moy_f,
            rmse(test, predire_structurel, fit_structurel(apprentissage)),
            rmse(test, predire_souple, fit_souple(apprentissage)),
            incoherence_de_la_carte(points, "tau_brut" if args.directe else "tau"))

    chemin = write_figure(points, structurel, souple, figure,
                          "dichotomie" if args.directe else "grille")
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
