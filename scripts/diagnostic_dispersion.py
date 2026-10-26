"""Diagnostic de la mesure de dispersion des arrivées : tendance, taille de navire, production.

L'étape 3 a jugé le modèle endogène trop régulier : I = 0,83 mesuré sur les bilans du port,
contre 0,07 simulé. Avant de choisir un mécanisme qui rendrait le modèle plus irrégulier, il
faut savoir **ce que mesure vraiment l'observé**. Trois suspects, tous dans la statistique
elle-même et non dans le modèle :

1. **une tendance** — si les importations de pellets croissent ou décroissent d'une année à
   l'autre (montée en cadence de l'aciérie), la variance autour de la moyenne générale
   compte cette pente comme de l'irrégularité ;
2. **la taille des navires** — N = tonnage ÷ q, et I varie comme 1/q : la valeur publiée
   suppose q = 142 700 t pour tous les navires ;
3. **la production** — si la consommation de l'aciérie varie, les importations la suivent,
   et la dispersion mesurée est celle de la production, pas de l'affrètement.

**Aucune modification du modèle.** Les comptages simulés sont ceux de l'étape 3 (scénarios
endogènes, cv_delai central, point de commande de l'étape 2), et chaque statistique leur est
appliquée **exactement** comme aux données : mêmes fonctions, mêmes découpages. Pour que les
découpages par année aient un sens côté simulation, les fenêtres de 16 trimestres simulés
commencent toutes à une frontière d'année.

**LA RÈGLE, ÉCRITE AVANT LE CALCUL ET FIGÉE DANS LES CONSTANTES CI-DESSOUS.**

- *L'indice corrigé soumis au verdict* est le **CV² intra-annuel des tonnages trimestriels** :
  la variance des tonnages autour de la moyenne de **leur** année (variance intra-groupe
  poolée, 16 − 4 = 12 degrés de liberté), divisée par le carré de la moyenne générale. C'est
  le seul indice qui retire à la fois la tendance (tâche 1) et la dépendance à q (tâche 2) :
  il ne contient que des tonnages mesurés. Choisi avant d'avoir calculé aucune de ses
  variantes.
- *Verdict* : si l'indice corrigé observé tombe entre les quantiles 5 % et 95 % de sa
  distribution simulée (fenêtres de 16 trimestres), le modèle endogène **n'est pas réfuté**
  par les données. Sinon, **l'écart demeure** et le mécanisme est à choisir.
- Le verdict se lit sur `baseline_endogene`, le corridor que décrivent les données de
  2022-2025, sous les deux hypothèses de dépassement ; s'il diffère entre elles, on le dit.
- Toutes les autres variantes (I intra-annuel, I sur 2022 seul, sur 2023-2025, sans 2022 T3,
  I en fonction de q, CV² brut) sont **publiées avec leur position** face à leur propre
  distribution simulée, mais **ne pèsent pas** dans le verdict. Si elles le contredisent,
  le rapport le signale.

**Production indépendante (tâche 3).** Les bilans d'embarquement du port donnent les
tonnages exportés de CDRI, billettes, rond à béton et fil machine. Leur corrélation avec les
pellets débarqués, au même trimestre et avec un trimestre de décalage, dit si les
importations suivent la production. **Limite à ne jamais perdre de vue : les ventes locales
de l'aciérie partent par la route et n'apparaissent dans aucun bilan portuaire**, et une
exportation est une cargaison entière : les embarquements sont aussi irréguliers que les
navires qui les emportent.

Usage : python scripts/diagnostic_dispersion.py [--graines 8] [--annees 100]
"""

import argparse
import importlib.util
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from corridor.ingest.port_stats import PAGES_EMBARQUEMENT
from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml
from corridor.sim.model import chronique
from corridor.sim.run import ECHAUFFEMENT_STATIONNAIRE_ANS
from corridor.transform.parse_port_stats import parse_rows, read_page

# --- règle pré-enregistrée ------------------------------------------------------
#: L'indice corrigé soumis au verdict.
INDICE_DU_VERDICT = "cv2_intra"
#: Quantiles de la distribution simulée qui délimitent « non réfuté ».
QUANTILES_DU_VERDICT = (0.05, 0.95)
#: Le scénario qui décrit le corridor des données 2022-2025.
SCENARIO_DU_VERDICT = "baseline_endogene"

TRIMESTRES_PAR_AN = 4
ANNEES_PAR_FENETRE = 4
#: Position, dans une fenêtre de 16 trimestres, du trimestre qui correspond à 2022 T3.
POSITION_T3_PREMIERE_ANNEE = 2
#: Tailles de navire balayées pour la tâche 2, en tonnes.
TAILLES_Q = tuple(range(70_000, 160_001, 10_000))
#: Produits exportés suivis (tâche 3) : motif du libellé, tel qu'il varie d'une page à l'autre.
PRODUITS = {
    "CDRI": re.compile(r"^\s*c?\s*-?\s*dri\s*$", re.IGNORECASE),
    "billettes": re.compile(r"billette", re.IGNORECASE),
    "rond à béton": re.compile(r"rond", re.IGNORECASE),
    "fil machine": re.compile(r"fil\s+machine", re.IGNORECASE),
}
TIRAGES_BOOTSTRAP = 4000
DEFAULT_GRAINES = 8
DEFAULT_ANNEES = 100.0
DEFAULT_SEED = 20260924
DEFAULT_FIGURE = Path("docs/diagnostic_dispersion.png")
DEFAULT_ROOT = Path("data/raw/port_stats")


def _charger(nom: str):
    chemin = Path(__file__).parent / f"{nom}.py"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


disp = _charger("arrival_dispersion")
val = _charger("validation_ca2")


# --- les indices, appliqués à l'identique aux deux côtés --------------------------


def variance_intra_annuelle(valeurs, par_an=TRIMESTRES_PAR_AN) -> float:
    """Variance autour de la moyenne de chaque année, poolée : Σ (x − x̄_année)² / (n − k)."""
    annees = [valeurs[i:i + par_an] for i in range(0, len(valeurs), par_an)]
    annees = [a for a in annees if len(a) == par_an]
    n, k = sum(len(a) for a in annees), len(annees)
    if n - k <= 0:
        return math.nan
    somme = sum((x - statistics.fmean(a)) ** 2 for a in annees for x in a)
    return somme / (n - k)


def cv2(valeurs) -> float:
    """Var / Moyenne² : ne dépend d'aucune taille de navire supposée."""
    if len(valeurs) < 2:
        return math.nan
    moyenne = statistics.fmean(valeurs)
    return statistics.variance(valeurs) / moyenne ** 2 if moyenne else math.nan


def indices(tonnages, q: float) -> dict[str, float]:
    """Toutes les variantes, sur une série de 16 tonnages trimestriels rangés par année.

    Les mêmes positions servent aux données et à la simulation : la première année de la
    fenêtre joue 2022, son troisième trimestre joue 2022 T3.
    """
    navires = [t / q for t in tonnages]
    moyenne_t = statistics.fmean(tonnages)
    sans_t3 = [x for i, x in enumerate(navires) if i != POSITION_T3_PREMIERE_ANNEE]
    return {
        "i_brut": disp.index_of_dispersion(navires),
        "i_intra": variance_intra_annuelle(navires) / statistics.fmean(navires),
        "i_premiere_annee": disp.index_of_dispersion(navires[:TRIMESTRES_PAR_AN]),
        "i_trois_dernieres": disp.index_of_dispersion(navires[TRIMESTRES_PAR_AN:]),
        "i_sans_t3": disp.index_of_dispersion(sans_t3),
        "cv2_brut": cv2(tonnages),
        "cv2_intra": variance_intra_annuelle(tonnages) / moyenne_t ** 2,
    }


LIBELLES = {
    "cv2_intra": "CV² intra-annuel des tonnages  (VERDICT)",
    "cv2_brut": "CV² des tonnages",
    "i_brut": "I, 16 trimestres (étape 3)",
    "i_intra": "I intra-annuel",
    "i_premiere_annee": "I, 2022 seul",
    "i_trois_dernieres": "I, 2023-2025",
    "i_sans_t3": "I, sans 2022 T3",
}


def i_selon_q(tonnages, tailles=TAILLES_Q) -> list[tuple[int, float]]:
    """I observé selon la taille de navire supposée : il varie exactement comme 1/q."""
    return [(q, disp.index_of_dispersion([t / q for t in tonnages])) for q in tailles]


def taille_pour_atteindre(i_observe: float, q_reference: float, i_cible: float) -> float:
    """La taille q à laquelle l'observé tomberait sur `i_cible` : I(q) = I(q₀)·q₀/q."""
    return i_observe * q_reference / i_cible if i_cible > 0 else math.inf


# --- distribution simulée --------------------------------------------------------


def fenetres_annuelles(tonnages, longueur=ANNEES_PAR_FENETRE * TRIMESTRES_PAR_AN):
    """Fenêtres de 16 trimestres qui commencent toutes à une frontière d'année, disjointes."""
    return [tonnages[i:i + longueur] for i in range(0, len(tonnages) - longueur + 1, longueur)]


def simuler(config, seeds, annees: float, q: float) -> tuple[list[dict[str, float]], float]:
    """Les indices de chaque fenêtre simulée, toutes graines confondues, et le tonnage
    trimestriel moyen simulé — à comparer à l'observé, puisque CV² en dépend."""
    echauffement_h = ECHAUFFEMENT_STATIONNAIRE_ANS * config.heures_par_an
    trimestre_h = config.heures_par_an / TRIMESTRES_PAR_AN
    sortie, moyennes = [], []
    for g in seeds:
        c = chronique(config, seed=g, years=ECHAUFFEMENT_STATIONNAIRE_ANS + annees,
                      periode_h=trimestre_h)
        tonnages = [p["debarque_t"] for p in c["periodes"] if p["debut_h"] >= echauffement_h]
        sortie += [indices(f, q) for f in fenetres_annuelles(tonnages)]
        moyennes.append(statistics.fmean(tonnages))
    return sortie, statistics.fmean(moyennes)


def position(observe: float, simules: list[float]) -> dict:
    """Où tombe l'observé : quantiles simulés, rang, et dedans ou dehors de [5 % ; 95 %]."""
    bas = val.quantile(simules, QUANTILES_DU_VERDICT[0])
    haut = val.quantile(simules, QUANTILES_DU_VERDICT[1])
    return {
        "observe": observe,
        "q05": bas,
        "mediane": val.quantile(simules, 0.5),
        "q95": haut,
        "rang": sum(s <= observe for s in simules) / len(simules) if simules else math.nan,
        "dedans": bas <= observe <= haut,
    }


def verdict(positions: dict) -> dict:
    """La règle écrite : l'indice du verdict, sur le scénario du verdict, sous chaque hypothèse."""
    issues = {h: positions[(SCENARIO_DU_VERDICT, h)][INDICE_DU_VERDICT]["dedans"]
              for h in (True, False)}
    if all(issues.values()):
        texte = "non réfuté par les données"
    elif not any(issues.values()):
        texte = "l'écart demeure"
    else:
        texte = "dépend de l'hypothèse de dépassement"
    return {"par_hypothese": issues, "texte": texte}


# --- exportations ------------------------------------------------------------------


def lire_exportations(root: Path = DEFAULT_ROOT, pages=PAGES_EMBARQUEMENT):
    """Cumuls exportés par produit, avec la règle de choix entre publications discordantes.

    Chaque cumul est publié deux fois (trois pour le 1er semestre 2023). Règle : la valeur
    **majoritaire** ; à défaut, celle de la page de l'année elle-même (colonne de droite),
    que la page suivante ne fait que recopier. Une ligne **absente** d'une page ne vaut zéro
    que faute d'autre publication : la page 2022 n'a pas de ligne DRI, alors que la page 2023
    en donne 67 205 t pour 2022. Toutes les discordances sont renvoyées, rien n'est tranché
    en silence.
    """
    publications = defaultdict(list)
    for page in pages:
        lignes = parse_rows(read_page(Path(root) / f"{page.annee_droite}_{page.cumul}_"
                                                   f"{page.slug}.html"))
        for produit, motif in PRODUITS.items():
            trouvees = [x for x in lignes if motif.search(x.label)]
            for annee, cote in ((page.annee_gauche, "gauche"), (page.annee_droite, "droite")):
                valeur = getattr(trouvees[0], cote) if trouvees else None
                publications[(produit, annee, page.cumul)].append((valeur, cote, page.slug))
    cumuls, discordances = defaultdict(dict), []
    for (produit, annee, cumul), pubs in publications.items():
        chiffres = [v for v, _, _ in pubs if v is not None]
        if not chiffres:
            cumuls[produit][(annee, cumul)] = 0.0
            continue
        comptes = Counter(chiffres).most_common()
        if len(comptes) > 1 and comptes[0][1] == comptes[1][1]:
            retenue = next((v for v, cote, _ in pubs if v is not None and cote == "droite"),
                           comptes[0][0])
        else:
            retenue = comptes[0][0]
        absent_contre_chiffre = len(chiffres) < len(pubs) and any(c != 0 for c in chiffres)
        if len(set(chiffres)) > 1 or absent_contre_chiffre:
            discordances.append({"produit": produit, "annee": annee, "cumul": cumul,
                                 "publications": pubs, "retenue": retenue})
        cumuls[produit][(annee, cumul)] = retenue
    return cumuls, discordances


def series_trimestrielles(cumuls_par_produit, annees) -> dict[str, list[float]]:
    """Tonnage par trimestre isolé, produit par produit, sur les années demandées."""
    sortie = {}
    for produit, cumuls in cumuls_par_produit.items():
        trims = {(a, t): v for a, t, v in disp.trimestres(cumuls)}
        sortie[produit] = [trims.get((a, t), math.nan) for a in annees for t in range(1, 5)]
    return sortie


def correlation(x, y) -> float:
    if len(x) < 3 or statistics.pstdev(x) == 0 or statistics.pstdev(y) == 0:
        return math.nan
    return statistics.correlation(x, y)


def correlation_bootstrap(x, y, tirages=TIRAGES_BOOTSTRAP, seed=0) -> dict:
    """Corrélation de Pearson et son IC à 95 % par bootstrap sur les paires de trimestres."""
    rng = random.Random(seed)
    n = len(x)
    valeurs = []
    for _ in range(tirages):
        idx = [rng.randrange(n) for _ in range(n)]
        r = correlation([x[i] for i in idx], [y[i] for i in idx])
        if math.isfinite(r):
            valeurs.append(r)
    return {"r": correlation(x, y), "bas": val.quantile(valeurs, 0.025),
            "haut": val.quantile(valeurs, 0.975), "n": n}


def correlations(pellets, exportations) -> dict:
    """Pellets débarqués contre exportations, au même trimestre et avec un trimestre de retard.

    Le retard compare les pellets d'un trimestre aux exportations du suivant : le temps de
    réduire le minerai, de couler l'acier et de charger un navire.
    """
    sortie = {}
    for nom, serie in exportations.items():
        paires = [(p, e) for p, e in zip(pellets, serie, strict=True) if math.isfinite(e)]
        sortie[(nom, 0)] = correlation_bootstrap([p for p, _ in paires], [e for _, e in paires])
        decalees = [(p, e) for p, e in zip(pellets[:-1], serie[1:], strict=True)
                    if math.isfinite(e)]
        sortie[(nom, 1)] = correlation_bootstrap([p for p, _ in decalees],
                                                 [e for _, e in decalees])
    return sortie


# --- taille des navires observés (tâche 4) ------------------------------------------


def tailles_observees(observations=None) -> list[tuple[str, float]]:
    """Tonnage publié des minéraliers de la série de situations portuaires."""
    ocr = _charger("ore_calls_report")
    chemin = observations or ocr.OBSERVATIONS
    if not Path(chemin).exists():
        return []
    escales = ocr.select_escales(ocr.build_escales(ocr.read_observations(chemin)))
    return [(e.vessel, e.tonnage_t) for e in escales if e.tonnage_t]


# --- figure -----------------------------------------------------------------------


def donnees_figure(observe_q, positions, pellets, exportations, annees) -> dict:
    trimestres = [f"{a}\nT{t}" if t == 1 else f"T{t}" for a in annees for t in range(1, 5)]
    total = [sum(serie[i] for serie in exportations.values()
                 if math.isfinite(serie[i])) for i in range(len(pellets))]
    return {"i_selon_q": observe_q, "positions": positions, "trimestres": trimestres,
            "pellets_kt": [p / 1e3 for p in pellets],
            "exportations_kt": [t / 1e3 for t in total]}


def write_figure(donnees, path=DEFAULT_FIGURE) -> Path:
    figure, (a, b, c) = plt.subplots(1, 3, figsize=(16, 5.4), constrained_layout=True,
                                     width_ratios=[1, 1.15, 1.5])
    ref = donnees["positions"][(SCENARIO_DU_VERDICT, True)]["i_brut"]
    qs = [q / 1e3 for q, _ in donnees["i_selon_q"]]
    a.plot(qs, [i for _, i in donnees["i_selon_q"]], color="#d62728", marker="o",
           label="I observé selon q supposé")
    a.axhspan(ref["q05"], ref["q95"], color="#1f77b4", alpha=0.18,
              label="I simulé, 5 %-95 % (baseline)")
    a.axvline(142.7, color="0.4", linestyle=":", linewidth=1.2)
    a.set_xlabel("taille de navire supposée q (kt)")
    a.set_ylabel("indice de dispersion I")
    a.set_title("I dépend de q, comme 1/q", fontweight="bold")
    a.set_yscale("log")
    a.legend(fontsize=8.5)

    cles = list(LIBELLES)
    for j, cle in enumerate(cles):
        pos = donnees["positions"][(SCENARIO_DU_VERDICT, True)][cle]
        b.plot([pos["q05"], pos["q95"]], [j, j], color="#1f77b4", linewidth=6, alpha=0.5)
        b.plot([pos["mediane"]], [j], "o", color="#1f77b4")
        b.plot([pos["observe"]], [j], "D", color="#d62728")
    b.set_yticks(range(len(cles)), [LIBELLES[c] for c in cles], fontsize=8.5)
    b.set_xscale("log")
    b.invert_yaxis()
    b.set_title("Observé (◆) face au simulé (barre 5 %-95 %)", fontweight="bold")

    x = range(len(donnees["pellets_kt"]))
    c.bar([i - 0.2 for i in x], donnees["pellets_kt"], width=0.4, color="#7f7f7f",
          label="pellets débarqués")
    c.bar([i + 0.2 for i in x], donnees["exportations_kt"], width=0.4, color="#2ca02c",
          label="CDRI + billettes + rond + fil exportés")
    c.set_xticks(list(x), donnees["trimestres"], fontsize=7.5)
    c.set_ylabel("kt par trimestre")
    c.set_title("Importations et exportations du port, 2022-2025", fontweight="bold")
    c.legend(fontsize=8.5)
    for ax in (a, b, c):
        ax.grid(True, color="0.9", which="both")
    figure.suptitle("Ce que mesure la dispersion des débarquements : tendance, taille de "
                    "navire, production\nSimulé : baseline_endogene, dépassements permis, "
                    "fenêtres de 16 trimestres alignées sur les années.", fontsize=10.5)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


# --- rapport ------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graines", type=int, default=DEFAULT_GRAINES)
    parser.add_argument("--annees", type=float, default=DEFAULT_ANNEES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    q0 = float(raw["navires_minerai"]["taille_moyenne"]["value"])
    cumuls_pellets, _ = disp.lire_cumuls()
    trims = disp.trimestres(cumuls_pellets)
    annees = sorted({a for a, _, _ in trims})
    pellets = [v for _, _, v in trims]
    if len(pellets) != len(annees) * TRIMESTRES_PAR_AN:
        raise ValueError("série de pellets incomplète : les découpages par année seraient faux")
    observe = indices(pellets, q0)

    print(f"Observé : {len(pellets)} trimestres {annees[0]}-{annees[-1]}, q = {q0:,.0f} t, "
          f"moyenne {statistics.fmean(pellets) / 1e3:,.0f} kt/trimestre")
    seeds = val.graines(args.graines, args.seed)
    positions = {}
    for scenario in val.SCENARIOS:
        for depassements, _ in val.HYPOTHESES:
            config = val.configuration(scenario, depassements)
            simules, moyenne_t = simuler(config, seeds, args.annees, q0)
            positions[(scenario, depassements)] = {
                cle: position(observe[cle], [s[cle] for s in simules]) for cle in LIBELLES}
            positions[(scenario, depassements)]["_fenetres"] = len(simules)
            positions[(scenario, depassements)]["_moyenne_kt"] = moyenne_t / 1e3

    print(f"\n{'=' * 112}\n1-2. Indices observés face à leur distribution simulée\n{'=' * 112}")
    for (scenario, depassements), pos in positions.items():
        print(f"\n  --- {scenario}, {dict(val.HYPOTHESES)[depassements]} "
              f"({pos['_fenetres']} fenêtres ; moyenne simulée {pos['_moyenne_kt']:,.0f} "
              f"kt/trimestre)")
        print(f"  {'indice':42} {'observé':>9} {'simulé médian [5 % ; 95 %]':>30} "
              f"{'rang':>6}  position")
        for cle, libelle in LIBELLES.items():
            p = pos[cle]
            print(f"  {libelle:42} {p['observe']:>9.3f} {p['mediane']:>11.3f} "
                  f"[{p['q05']:.3f} ; {p['q95']:.3f}] {p['rang']:>6.0%}  "
                  f"{'DEDANS' if p['dedans'] else 'dehors'}")

    print(f"\n{'=' * 112}\n2. I observé selon la taille de navire supposée\n{'=' * 112}")
    observe_q = i_selon_q(pellets)
    print("  " + "  ".join(f"q={q / 1e3:.0f}k : {i:.2f}" for q, i in observe_q))
    q95 = positions[(SCENARIO_DU_VERDICT, True)]["i_brut"]["q95"]
    print(f"  Pour tomber sous le 95e centile simulé ({q95:.3f}), l'observé exigerait "
          f"q = {taille_pour_atteindre(observe['i_brut'], q0, q95) / 1e3:,.0f} kt.")

    print(f"\n{'=' * 112}\n3. Production indépendante : exportations du port\n{'=' * 112}")
    cumuls_exp, discordances = lire_exportations()
    exportations = series_trimestrielles(cumuls_exp, annees)
    exportations_total = [sum(s[i] for s in exportations.values()) for i in range(len(pellets))]
    for d in discordances:
        pubs = ", ".join(f"{'absent' if v is None else f'{v:,.0f}'} ({cote})"
                         for v, cote, _ in d["publications"])
        print(f"  discordance {d['produit']} {d['annee']} {d['cumul']} : {pubs} "
              f"→ retenu {d['retenue']:,.0f}")
    negatifs = [(nom, i) for nom, s in exportations.items() for i, v in enumerate(s) if v < 0]
    if negatifs:
        print(f"  ATTENTION trimestres négatifs (cumul en baisse) : {negatifs}")
    print(f"\n  {'trimestre':10} {'pellets kt':>11} " + " ".join(f"{n:>13}" for n in PRODUITS)
          + f" {'total kt':>10}")
    for i, (a, t, v) in enumerate(trims):
        print(f"  {a} T{t}    {v / 1e3:>11.1f} "
              + " ".join(f"{exportations[n][i] / 1e3:>13.1f}" for n in PRODUITS)
              + f" {exportations_total[i] / 1e3:>10.1f}")
    print(f"\n  CV² des tonnages : pellets {cv2(pellets):.3f}, exportations totales "
          f"{cv2(exportations_total):.3f}, CDRI {cv2(exportations['CDRI']):.3f}")
    corr = correlations(pellets, {"total": exportations_total, "CDRI": exportations["CDRI"]})
    print(f"\n  {'série':10} {'décalage':>9} {'r':>8} {'IC 95 % bootstrap':>22} {'paires':>7}")
    for (nom, decalage), c in corr.items():
        print(f"  {nom:10} {decalage:>9} {c['r']:>8.2f} [{c['bas']:>6.2f} ; {c['haut']:>5.2f}]"
              f" {c['n']:>11}")
    print("  Rappel : les ventes locales partent par la route et n'apparaissent pas ici.")

    print(f"\n{'=' * 112}\n4. Taille des minéraliers observés depuis septembre\n{'=' * 112}")
    tailles = tailles_observees()
    for navire, t in tailles:
        print(f"  {navire:22} {t:>10,.0f} t  ({(t - q0) / q0:+.1%} face à {q0:,.0f})")

    print(f"\n{'=' * 112}\nVerdict selon la règle écrite\n{'=' * 112}")
    v = verdict(positions)
    for depassements, dedans in v["par_hypothese"].items():
        p = positions[(SCENARIO_DU_VERDICT, depassements)][INDICE_DU_VERDICT]
        print(f"  {SCENARIO_DU_VERDICT}, {dict(val.HYPOTHESES)[depassements]} : CV² intra "
              f"observé {p['observe']:.4f} contre [{p['q05']:.4f} ; {p['q95']:.4f}] → "
              f"{'dedans' if dedans else 'dehors'}")
    print(f"  **{v['texte'].upper()}**")

    chemin = write_figure(donnees_figure(observe_q, positions, pellets,
                                         {n: exportations[n] for n in PRODUITS}, annees),
                          args.figure)
    print(f"\nfigure écrite : {chemin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
