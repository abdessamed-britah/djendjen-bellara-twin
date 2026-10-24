"""Le chiffre appliqué de l'étude : la distribution du taux de service du site.

**Pourquoi une distribution et pas un nombre.** Le taux de service β = production ÷ cible
du corridor en phase 2 dépend de deux grandeurs que le projet ne connaît pas précisément :

- **l'irrégularité des arrivées** c_a², estimée à 0,83 sur 16 trimestres de bilans de
  trafic publics, avec un intervalle bootstrap large — et une sensibilité forte à un seul
  trimestre anormal (2022 T3, 606 t de pellets) ;
- **les deux capacités de stockage**, de statut `inconnu`, chacune avec une fourchette
  publiée dans les hypothèses.

Annoncer un β ponctuel reviendrait à cacher cette incertitude derrière une décimale. Ce
script la propage : il tire 500 jeux de paramètres dans ces distributions, simule une année
par tirage, et renvoie la **distribution de β** — médiane et intervalle à 90 %.

**Comment les tirages sont faits.**

- c_a² est tiré **directement dans l'échantillon bootstrap** de l'indice de dispersion,
  recalculé ici depuis les 16 trimestres. Pas de loi paramétrique imposée : la forme de
  l'incertitude est celle que les données donnent. Le modèle attend un coefficient de
  variation, donc la racine carrée est prise au moment de simuler ;
- chaque capacité de stockage est tirée **uniformément dans sa propre fourchette**, port et
  usine indépendamment. Tirer le total uniformément serait plus simple mais ferait
  descendre la part du port sous son propre minimum publié (limite 7 du CLAUDE.md) ;
- une réplication par tirage. β est alors la production d'**une année plausible** sous un
  jeu de paramètres plausible : c'est bien ce qu'un exploitant affronte. La part de cette
  dispersion qui vient des paramètres plutôt que de l'aléa d'une année est estimée à part,
  et imprimée.

**Deux variantes, parce que le choix n'est pas neutre.** L'indice de dispersion vaut 0,83
en gardant le trimestre 2022 T3, 0,45 en l'écartant. Les deux sont calculées et affichées :
l'écart entre les deux distributions de β **est** une mesure de la fragilité du chiffre, et
le lecteur doit le voir plutôt que de recevoir celle que l'auteur préfère.

Usage : python scripts/beta_site.py [--tirages 500] [--annees 1] [--seed 20260924]
"""

import argparse
import math
import random
import statistics
import sys
from dataclasses import replace
from pathlib import Path

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.model import simulate
from corridor.sim.run import replicate

sys.path.insert(0, str(Path(__file__).parent))
from arrival_dispersion import bootstrap_ci, index_of_dispersion, lire_cumuls, trimestres

DEFAULT_SCENARIO = "phase2_mesure"
DEFAULT_TIRAGES = 500
#: Distribution de long terme : moins de tirages, mais plusieurs années moyennées par
#: tirage, ce qui coûte `tirages_long x reps_long` années de simulation.
DEFAULT_TIRAGES_LONG = 200
DEFAULT_REPS_LONG = 6
DEFAULT_SEED = 20260924
#: Réplications à paramètres figés, pour séparer l'aléa d'une année de l'incertitude des
#: paramètres. 120 suffisent : on cherche un écart-type, pas une moyenne précise.
REPS_BRUIT = 120
#: Quantiles publiés. 5 % et 95 % encadrent 90 % des tirages.
QUANTILES = (0.05, 0.50, 0.95)
#: Un trimestre sous ce tonnage est le trimestre anormal qu'on écarte dans la variante.
SEUIL_TRIMESTRE_ANORMAL_T = 10_000.0


def navires_par_trimestre(root=None, taille_t=None) -> list[float]:
    """Navires estimés par trimestre, depuis les bilans de trafic archivés."""
    raw = load_yaml(ASSUMPTIONS_PATH)
    taille = taille_t or float(raw["navires_minerai"]["taille_moyenne"]["value"])
    cumules, _ = lire_cumuls(root) if root else lire_cumuls()
    return [tonnes / taille for _, _, tonnes in trimestres(cumules)]


def echantillon_bootstrap(navires, tirages: int, rng: random.Random) -> list[float]:
    """Échantillon bootstrap de l'indice de dispersion : la distribution de c_a² elle-même.

    `bootstrap_ci` renvoie déjà les tirages ; on les réutilise plutôt que d'en refaire,
    pour que l'intervalle publié et la distribution tirée soient exactement les mêmes.
    """
    _, _, echantillons = bootstrap_ci(navires, tirages, rng)
    return [x for x in echantillons if x > 0]


def tirer_parametres(base: SimConfig, c_a2: float, raw, rng: random.Random) -> SimConfig:
    """Une configuration tirée : c_a² imposé, capacités de stockage tirées dans leur fourchette.

    `dispersion_arrivees` est un coefficient de variation et c_a² son carré, d'où la racine.
    Les stocks initiaux suivent les capacités tirées, sinon le tirage mesurerait autre chose.
    """
    port = raw["stockage_port"]["capacite_stockyard_pellets"]["range"]
    usine = raw["usine"]["capacite_stock_pellets_usine"]["range"]
    stockyard = rng.uniform(float(port[0]), float(port[1]))
    plant = rng.uniform(float(usine[0]), float(usine[1]))
    return replace(
        base,
        dispersion_arrivees=math.sqrt(max(c_a2, 0.0)),
        capacite_stockyard_t=stockyard,
        capacite_stock_usine_t=plant,
        stock_initial_stockyard_t=stockyard * base.fraction_stock_initial,
        stock_initial_usine_t=plant * base.fraction_stock_initial,
    )


def cible_t(config: SimConfig, annees: float) -> float:
    return config.debit_dri_t_par_h * config.heures_ouvrees_par_an * annees


def propager(base, raw, c_a2_echantillon, tirages, annees, rng, reps=1) -> dict:
    """Tire `tirages` jeux de paramètres et renvoie les β obtenus.

    `reps = 1` donne la **distribution prédictive d'une année** : chaque β est la
    production d'une année plausible sous des paramètres plausibles. C'est ce qu'un
    exploitant affronte, et sa dispersion mélange les deux sources d'incertitude.

    `reps > 1` moyenne plusieurs années par tirage : l'aléa annuel s'estompe en 1/√reps
    et il reste la **distribution de long terme**, celle qui répond à « de quoi dépend le
    taux de service du corridor », pas à « que vaudra l'année prochaine ». C'est le chiffre
    de dimensionnement.

    Garde aussi les paramètres tirés : sans eux on ne peut pas dire *lequel* des deux
    pilote la dispersion du résultat, et c'est une question qu'un relecteur posera.
    """
    betas, c_a2_tires, tau_tires = [], [], []
    for _ in range(tirages):
        c_a2 = rng.choice(c_a2_echantillon)
        config = tirer_parametres(base, c_a2, raw, rng)
        cible = cible_t(config, annees)
        annees_simulees = [
            simulate(config, seed=rng.randrange(2**31), years=annees).dri_production_t / cible
            for _ in range(reps)
        ]
        betas.append(statistics.fmean(annees_simulees))
        c_a2_tires.append(c_a2)
        total = config.capacite_stockyard_t + config.capacite_stock_usine_t
        tau_tires.append(total / (config.debit_pellets_t_par_h * 24.0))
    return {"beta": betas, "c_a2": c_a2_tires, "tau": tau_tires, "reps": reps}


def quantiles(valeurs, niveaux=QUANTILES) -> dict[float, float]:
    """Quantiles empiriques par interpolation linéaire, sur une copie triée."""
    ordonnees = sorted(valeurs)
    if not ordonnees:
        return dict.fromkeys(niveaux, math.nan)
    resultats = {}
    for niveau in niveaux:
        position = niveau * (len(ordonnees) - 1)
        bas = math.floor(position)
        haut = min(bas + 1, len(ordonnees) - 1)
        part = position - bas
        resultats[niveau] = ordonnees[bas] * (1 - part) + ordonnees[haut] * part
    return resultats


def bruit_de_replication(base, raw, c_a2_median, annees, seed) -> float:
    """Écart-type de β à **paramètres figés** : la part qui vient de l'aléa d'une année.

    Sert à répondre à « votre intervalle, c'est de l'incertitude ou du bruit ? ». Si cet
    écart-type est du même ordre que celui de la distribution complète, l'incertitude des
    paramètres n'apporte presque rien — et inversement.
    """
    config = tirer_parametres(base, c_a2_median, raw, random.Random(seed))
    resultats = replicate(config, annees, REPS_BRUIT, seed, "bruit")
    cible = cible_t(config, annees)
    return statistics.stdev([r.dri_production_t / cible for r in resultats])


def correlation(xs, ys) -> float:
    """Corrélation de Pearson, pour dire lequel des deux paramètres pilote β."""
    if len(xs) < 2:
        return math.nan
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else math.nan


def _pc(valeur: float) -> str:
    return f"{valeur * 100:.1f}".replace(".", ",")


def print_variante(nom, resultat, indice, bruit) -> None:
    """Distribution de β d'une variante, avec ce qui la pilote."""
    betas = resultat["beta"]
    q = quantiles(betas)
    print(f"\n{'=' * 84}\n{nom}\n{'=' * 84}")
    print(f"  indice de dispersion retenu : I = {indice:.2f} (c_a² médian tiré : "
          f"{statistics.median(resultat['c_a2']):.2f})")
    print(f"  taux de service β, sur {len(betas)} tirages :")
    print(f"     médiane            {_pc(q[0.50])} %")
    print(f"     intervalle à 90 %  {_pc(q[0.05])} % à {_pc(q[0.95])} %")
    print(f"     perte médiane      {_pc(1 - q[0.50])} % de la cible")
    print(f"     perte au pire 5 %  {_pc(1 - q[0.05])} % de la cible")
    ecart_total = statistics.stdev(betas)
    print(f"\n  Dispersion de β : écart-type total {_pc(ecart_total)} point(s), dont "
          f"{_pc(bruit)} point(s)")
    print(f"  d'aléa d'une année à paramètres figés ({REPS_BRUIT} réplications, stockage")
    print("  fixé à un tirage et c_a² à sa médiane).")
    part = (bruit / ecart_total) ** 2 if ecart_total else math.nan
    if not math.isfinite(part):
        print("  Décomposition impossible : la dispersion totale est nulle.")
    elif part >= 1.0:
        print("  L'aléa d'une année explique à lui seul toute la dispersion observée : "
              "l'incertitude")
        print("  des paramètres n'en ressort pas. Décomposition non concluante, à ne pas "
              "lire comme")
        print("  « les paramètres ne comptent pas » — voir les corrélations ci-dessous.")
    else:
        print(f"  Soit environ {part * 100:.0f} % de la variance venant de l'aléa annuel et "
              f"{(1 - part) * 100:.0f} % de")
        print("  l'incertitude sur les paramètres.")
    print(f"\n  Corrélation de β avec c_a² : {correlation(resultat['c_a2'], betas):+.2f}")
    print(f"  Corrélation de β avec τ    : {correlation(resultat['tau'], betas):+.2f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--tirages", type=int, default=DEFAULT_TIRAGES)
    parser.add_argument("--annees", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--tirages-long", type=int, default=DEFAULT_TIRAGES_LONG,
                        help="tirages de la distribution de long terme (plus coûteux)")
    parser.add_argument("--reps-long", type=int, default=DEFAULT_REPS_LONG,
                        help="années moyennées par tirage pour le long terme")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    base = SimConfig.from_scenario(load_scenario(args.scenario))
    navires = navires_par_trimestre()
    taille = float(raw["navires_minerai"]["taille_moyenne"]["value"])
    sans_anormal = [n for n in navires
                    if n * taille >= SEUIL_TRIMESTRE_ANORMAL_T]

    print(f"scénario {args.scenario} : cible {cible_t(base, args.annees) / 1e6:.3f} Mt de DRI "
          f"sur {args.annees:g} an(s)")
    print(f"  {len(navires)} trimestres de bilans de trafic, "
          f"{len(navires) - len(sans_anormal)} écarté(s) dans la seconde variante")
    print(f"  stockage tiré dans [{raw['stockage_port']['capacite_stockyard_pellets']['range']}]"
          f" + [{raw['usine']['capacite_stock_pellets_usine']['range']}] t")
    print(f"  {args.tirages} tirages, une réplication d'un an par tirage")

    variantes = (
        ("Variante A — tous les trimestres (c_a² = 0,83)", navires),
        ("Variante B — sans le trimestre anormal 2022 T3 (c_a² = 0,45)", sans_anormal),
    )
    resumes, resumes_long = {}, {}
    for nom, serie in variantes:
        rng = random.Random(args.seed)
        echantillon = echantillon_bootstrap(serie, args.tirages * 4, rng)
        resultat = propager(base, raw, echantillon, args.tirages, args.annees, rng)
        bruit = bruit_de_replication(
            base, raw, statistics.median(resultat["c_a2"]), args.annees, args.seed
        )
        print_variante(nom, resultat, index_of_dispersion(serie), bruit)
        resumes[nom] = quantiles(resultat["beta"])

        long_terme = propager(base, raw, echantillon, args.tirages_long, args.annees,
                              rng, reps=args.reps_long)
        resumes_long[nom] = quantiles(long_terme["beta"])
        q = resumes_long[nom]
        print(f"\n  Long terme ({args.tirages_long} tirages × {args.reps_long} années "
              f"moyennées, l'aléa annuel atténué) :")
        print(f"     médiane {_pc(q[0.50])} %, intervalle à 90 % "
              f"{_pc(q[0.05])} % à {_pc(q[0.95])} %")
        print(f"     corrélation avec c_a² {correlation(long_terme['c_a2'], long_terme['beta']):+.2f}"
              f", avec τ {correlation(long_terme['tau'], long_terme['beta']):+.2f}")

    print(f"\n{'=' * 84}\nLe chiffre appliqué de l'étude\n{'=' * 84}")
    print("  Taux de service β d'**une année** (ce qu'un exploitant affronte) :")
    for nom, q in resumes.items():
        court = nom.split("—")[1].strip() if "—" in nom else nom
        print(f"    {court:50} β = {_pc(q[0.50])} % [{_pc(q[0.05])} ; {_pc(q[0.95])}]")
    print("\n  Taux de service β **de long terme** (le chiffre de dimensionnement, "
          "l'aléa annuel atténué) :")
    for nom, q in resumes_long.items():
        court = nom.split("—")[1].strip() if "—" in nom else nom
        print(f"    {court:50} β = {_pc(q[0.50])} % [{_pc(q[0.05])} ; {_pc(q[0.95])}]")
    print("\n  Les deux répondent à des questions différentes et ne se substituent pas :")
    print("  le premier intervalle est large surtout parce qu'une année tirée au sort varie ;")
    print("  le second ne garde que ce que le projet ignore des paramètres.")
    medianes = [q[0.50] for q in resumes.values()]
    ecart = abs(medianes[0] - medianes[1])
    print(f"\n  Écart entre les deux médianes : {_pc(ecart)} point(s). Un seul trimestre de la")
    print("  source le produit. C'est la mesure la plus directe de la fragilité du chiffre,")
    print("  et c'est pourquoi les deux variantes sont publiées plutôt qu'une seule.")
    print("\n  À citer comme une plage, jamais comme un point : les deux capacités de stockage")
    print("  sont de statut `inconnu`, et c_a² est estimé, pas mesuré.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
