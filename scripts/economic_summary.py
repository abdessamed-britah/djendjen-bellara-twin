"""Convertit la perte de production de DRI de chaque scénario en coût annuel estimé.

Pour chaque scénario et chacune de deux régularités d'arrivée (régulières, Poisson), rejoue
la simulation puis traduit la perte (cible − production, en t/an) en dollars.

**Conversion.** `couts.cout_journee_arret_dri` (USD/jour) est une valeur du site actuel : la
journée de production de la capacité *baseline*. On en tire une marge implicite par tonne,
`cout_journee / débit journalier de la baseline` (~22 USD/t au centre), puis
`coût = perte en tonnes × marge par tonne`. Pour la baseline, c'est exactement
`perte ÷ débit journalier × coût de la journée`, soit un nombre de jours d'arrêt équivalents
multiplié par le coût du jour. Diviser la perte par 365 au lieu du débit journalier mélangerait
des t/jour et des USD/jour ; et appliquer 130 000 USD/jour tel quel à la phase 2 sous-évaluerait
d'un facteur deux une journée qui produit deux fois plus.

**Fourchette.** L'enveloppe va de (borne basse de la perte × borne basse du coût) à
(borne haute de la perte × borne haute du coût). C'est volontairement large : elle cumule
l'IC 95 % de la simulation et la fourchette de l'hypothèse, sans supposer d'indépendance
particulière. Les montants sont arrondis au million de dollars : aller plus fin prétendrait à
une précision que ni le coût unitaire (hypothèse `estimé`, marges chinoises) ni les capacités de
stockage (`inconnu`) ne permettent.

Usage : python scripts/economic_summary.py [--reps 30] [--years 1] [--seed 20260924]
"""

import argparse
import dataclasses
import importlib.util
import random
import sys
from pathlib import Path

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.run import (
    ECHAUFFEMENT_STATIONNAIRE_ANS,
    FENETRE_STATIONNAIRE_ANS,
    replicate,
    replicate_stationnaire,
)
from corridor.sim.stats import estimate


def _beta_site():
    """Charge `scripts/beta_site.py` pour réutiliser sa propagation d'incertitude.

    Importé paresseusement et par chemin : `scripts/` n'est pas un paquet, et surtout le
    mode nominal de ce script ne doit pas dépendre de la présence des bilans de trafic que
    `beta_site` lit au démarrage.
    """
    chemin = Path(__file__).parent / "beta_site.py"
    spec = importlib.util.spec_from_file_location("beta_site", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCENARIOS = ("baseline", "phase2", "phase2_plus_stockage", "phase2_plus_poste")
#: Scénario du chiffre appliqué : irrégularité mesurée sur les bilans de trafic.
SCENARIO_MESURE = "phase2_mesure"
ARRIVEES = ((0.0, "régulières"), (1.0, "Poisson"))
#: Les seuls scénarios qui ont un régime stationnaire, donc un coût annuel qui ne dépend
#: pas de l'horizon de lecture (voir scripts/convergence_horizon.py).
SCENARIOS_STATIONNAIRES = ("baseline_endogene", "phase2_endogene")
#: Toute comparaison entre scénarios endogènes se publie sous les deux hypothèses : elles
#: pèsent jusqu'à 1,7 point de taux de service (limite 20 de CLAUDE.md).
HYPOTHESES_DEPASSEMENT = ((True, "dépassements permis"), (False, "dépassements interdits"))
#: Les trois termes du coût total, dans l'ordre où ils sont publiés.
TERMES = ("arrets_dri", "surestaries", "portage")
HEURES_PAR_JOUR = 24.0
MILLION = 1e6


def cout_journee(raw):
    """(bas, centre, haut) de `couts.cout_journee_arret_dri`, en USD/jour."""
    entree = raw["couts"]["cout_journee_arret_dri"]
    if entree.get("value") is None or "range" not in entree:
        raise ValueError("couts.cout_journee_arret_dri doit avoir une valeur et une fourchette")
    bas, haut = entree["range"]
    return float(bas), float(entree["value"]), float(haut)


def marge_par_tonne(cout_jour, debit_baseline_t_par_jour):
    """Marge implicite (USD/t) d'une journée de production de la baseline."""
    return cout_jour / debit_baseline_t_par_jour


def cout_annuel(perte_t, marge_usd_t):
    """Coût annuel (USD) d'une perte de production, à une marge par tonne donnée."""
    return perte_t * marge_usd_t


def enveloppe(perte_bas_t, perte_haut_t, marge_bas, marge_haut):
    """Bornes (USD) cumulant l'IC de la perte et la fourchette de la marge.

    Une perte négative (production au-dessus de la cible) est ramenée à zéro : ce n'est pas
    un gain économique, seulement du bruit autour d'une perte nulle.
    """
    return cout_annuel(max(perte_bas_t, 0.0), marge_bas), cout_annuel(
        max(perte_haut_t, 0.0), marge_haut
    )


def arrondi_millions(usd):
    """Arrondi au million de dollars, en texte ; « < 1 » sous le demi-million."""
    m = usd / MILLION
    return "< 1" if m < 0.5 else f"{m:,.0f}".replace(",", " ")


def perte_par_replication(scenario, dispersion, reps, years, seed):
    """Pertes annuelles (t/an) de chaque réplication, politique `pull`."""
    config = dataclasses.replace(
        SimConfig.from_scenario(load_scenario(scenario)),
        dispersion_arrivees=dispersion,
        politique_expedition="pull",
    )
    cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an * years
    resultats = replicate(config, years, reps, seed, scenario)
    return [(cible - r.dri_production_t) / years for r in resultats]


def perte_depuis_beta(betas, cible_t_par_an: float) -> list[float]:
    """Manque à produire annuel (t/an) de chaque tirage, déduit de son taux de service.

    `perte = cible × (1 − β)`, ramenée à zéro quand β dépasse 1 : produire au-dessus de la
    cible n'est pas un gain monnayable, c'est du bruit autour d'une perte nulle. La cible ne
    dépend ni de l'irrégularité ni du stockage tirés, seulement de la capacité du four, donc
    un seul nombre suffit pour tous les tirages.
    """
    return [max(cible_t_par_an * (1.0 - beta), 0.0) for beta in betas]


def plage_de_cout(pertes, quantiles_t, marge_bas: float, marge_centre: float,
                  marge_haut: float) -> dict:
    """Le coût du manque à produire sous forme de plage, et sa décomposition.

    Deux incertitudes s'empilent et il faut les garder distinctes, sous peine de ne plus
    savoir ce que la plage mesure :

    - celle du **corridor** : les quantiles 5 % / 50 % / 95 % du manque à produire sur les
      tirages de c_a² et de stockage. C'est ce que la simulation sait ;
    - celle du **prix d'une tonne manquante** : la fourchette de `cout_journee_arret_dri`,
      qui est une hypothèse `estimé` sans source locale.

    La plage publiée croise les deux extrêmes (bas × bas, haut × haut) : volontairement
    large, parce que rien n'autorise à supposer que les deux se compensent.
    """
    return {
        "perte_bas_t": quantiles_t[0.05],
        "perte_mediane_t": quantiles_t[0.50],
        "perte_haut_t": quantiles_t[0.95],
        "cout_bas": cout_annuel(max(quantiles_t[0.05], 0.0), marge_bas),
        "cout_median": cout_annuel(max(quantiles_t[0.50], 0.0), marge_centre),
        "cout_haut": cout_annuel(max(quantiles_t[0.95], 0.0), marge_haut),
        "tirages": len(pertes),
    }


def couts_unitaires(raw, niveau: str = "value") -> dict[str, float]:
    """Les trois prix unitaires du coût total, à la valeur centrale ou à une borne.

    `niveau` vaut `"value"`, `"bas"` ou `"haut"`. Chaque prix est lu dans
    `assumptions.yaml` et nulle part ailleurs :

    - **arrêt du DRI**, en USD par tonne de DRI non produite : la marge implicite de
      `cout_journee_arret_dri`, ramenée au débit journalier de la baseline ;
    - **surestaries**, en USD par heure d'attente en rade et par navire ;
    - **portage**, en USD par tonne de stock physique et par an : prix des pellets × taux
      de portage.
    """
    def lire(cle):
        entree = raw["couts"][cle]
        if niveau == "value":
            return float(entree["value"])
        return float(entree["range"][0 if niveau == "bas" else 1])

    baseline = SimConfig.from_scenario(load_scenario("baseline"))
    debit_jour = baseline.debit_dri_t_par_h * HEURES_PAR_JOUR
    return {
        "marge_usd_t": marge_par_tonne(lire("cout_journee_arret_dri"), debit_jour),
        "surestaries_usd_h": lire("surestaries_usd_par_heure"),
        "portage_usd_t_an": lire("prix_pellets") * lire("taux_de_portage"),
    }


def cout_total(resultat, config: SimConfig, unitaires: dict[str, float]) -> dict[str, float]:
    """Coût annuel d'une réplication, en USD/an : trois termes séparés et leur somme.

    - **arrêts du DRI** : production manquante sur la fenêtre mesurée × marge par tonne.
      Ramenée à zéro si négative — produire au-delà de la cible n'est pas un gain ;
    - **surestaries** : navire-heures passées en rade × taux horaire. Intégrées dans le
      temps, elles comptent aussi les navires encore en rade à la fin ;
    - **portage** : stock physique moyen (stockyard + usine) × prix × taux de portage. Le
      minerai en mer et en rade n'est pas porté ici : qui le porte dépend de l'Incoterm.

    Tout est ramené à **une année de la fenêtre mesurée** (`annees_mesurees`), pas à
    l'horizon total : l'échauffement ne compte ni dans la production ni dans les coûts.
    """
    annees = resultat.annees_mesurees
    cible = config.debit_dri_t_par_h * config.heures_ouvrees_par_an * annees
    perte_t_an = max(cible - resultat.dri_production_t, 0.0) / annees
    stock_moyen_t = resultat.stockyard_mean_t + resultat.plant_stock_mean_t
    termes = {
        "arrets_dri": perte_t_an * unitaires["marge_usd_t"],
        "surestaries": resultat.anchorage_ship_hours / annees * unitaires["surestaries_usd_h"],
        "portage": stock_moyen_t * unitaires["portage_usd_t_an"],
    }
    termes["total"] = sum(termes[t] for t in TERMES)
    return termes


def estimer_couts(resultats, config: SimConfig, unitaires) -> dict[str, object]:
    """Une `Estimate` par terme et pour le total, sur les réplications.

    Les termes sont estimés réplication par réplication puis moyennés, et non l'inverse :
    c'est ce qui donne au total un IC qui tient compte de la corrélation entre termes (une
    année à ruptures est aussi une année à stock bas, donc à portage faible).
    """
    lignes = [cout_total(r, config, unitaires) for r in resultats]
    return {cle: estimate([ligne[cle] for ligne in lignes]) for cle in (*TERMES, "total")}


def configuration_stationnaire(scenario: str, depassements: bool, **ecarts) -> SimConfig:
    """Le scénario endogène sous une hypothèse de dépassement, avec d'éventuels écarts."""
    config = SimConfig.from_scenario(load_scenario(scenario))
    return dataclasses.replace(config, depassements_autorises=depassements, **ecarts)


def print_stationnaire(unitaires, reps: int, seed: int) -> None:
    """Le coût total par scénario endogène, en régime stationnaire, sous les deux hypothèses."""
    print(f"\n{'=' * 104}")
    print("Coût annuel en régime stationnaire : arrêts du DRI + surestaries + portage")
    print(f"{'=' * 104}")
    print(f"  {reps} réplications appariées, {ECHAUFFEMENT_STATIONNAIRE_ANS:.0f} ans "
          f"d'échauffement écartés, {FENETRE_STATIONNAIRE_ANS:.0f} ans mesurés.")
    print(f"  Prix unitaires centraux : marge {unitaires['marge_usd_t']:.1f} USD/t de DRI, "
          f"surestaries {unitaires['surestaries_usd_h']:,.0f} USD/h, portage "
          f"{unitaires['portage_usd_t_an']:.1f} USD/t/an. IC à 95 % sur la simulation seule.")
    print(f"\n  {'scénario':20} {'hypothèse':24} " + " ".join(
        f"{t:>21}" for t in ("arrêts DRI", "surestaries", "portage", "total")) + "   (M USD/an)")
    for scenario in SCENARIOS_STATIONNAIRES:
        for depassements, nom in HYPOTHESES_DEPASSEMENT:
            config = configuration_stationnaire(scenario, depassements)
            couts = estimer_couts(replicate_stationnaire(config, reps, seed, scenario),
                                  config, unitaires)
            cellules = " ".join(
                f"{couts[t].mean / MILLION:>13.2f} ± {couts[t].half_width / MILLION:<5.2f}"
                for t in (*TERMES, "total"))
            print(f"  {scenario:20} {nom:24} {cellules}")
    print("\n  Les prix unitaires sont tous de statut estimé et sans source locale : les montants")
    print("  servent à comparer les termes entre eux et les configurations entre elles. L'IC ne")
    print("  porte que l'aléa de la simulation, pas l'incertitude sur les prix.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=30)
    parser.add_argument("--years", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--mesure", action="store_true",
                        help="ajoute le chiffre appliqué : scénario phase2_mesure, "
                             "irrégularité mesurée et stockage tiré dans sa fourchette")
    parser.add_argument("--tirages", type=int, default=200,
                        help="tirages de paramètres du mode --mesure")
    parser.add_argument("--stationnaire", action="store_true",
                        help="seulement le coût total en régime stationnaire, scénarios "
                             "endogènes, sous les deux hypothèses de dépassement")
    args = parser.parse_args(argv)

    raw = load_yaml(ASSUMPTIONS_PATH)
    if args.stationnaire:
        print_stationnaire(couts_unitaires(raw), args.reps, args.seed)
        return 0
    jour_bas, jour_centre, jour_haut = cout_journee(raw)
    baseline = SimConfig.from_scenario(load_scenario("baseline"))
    debit_jour = baseline.debit_dri_t_par_h * HEURES_PAR_JOUR
    marge_bas, marge_centre, marge_haut = (
        marge_par_tonne(c, debit_jour) for c in (jour_bas, jour_centre, jour_haut)
    )

    print("Coût annuel estimé de la perte de production DRI (politique pull)")
    print(f"Coût d'une journée d'arrêt : {jour_centre:,.0f} USD/jour "
          f"[{jour_bas:,.0f} ; {jour_haut:,.0f}], statut estimé (marges EAF chinoises).")
    print(f"Marge implicite : ~{marge_centre:.0f} USD/t [{marge_bas:.0f} ; {marge_haut:.0f}] "
          f"sur un débit de {debit_jour:,.0f} t/jour (baseline).")
    print(f"{args.reps} réplications par ligne ; montants arrondis au million d'USD.\n")
    print(f"{'scénario':22} {'arrivées':11} {'perte (Mt/an)':>18} "
          f"{'coût central':>14} {'fourchette (M USD/an)':>24}")

    for scenario in SCENARIOS:
        for dispersion, nom in ARRIVEES:
            pertes = perte_par_replication(scenario, dispersion, args.reps, args.years,
                                           args.seed)
            e = estimate(pertes)
            bas, haut = enveloppe(e.low, e.high, marge_bas, marge_haut)
            centre = cout_annuel(max(e.mean, 0.0), marge_centre)
            print(f"{scenario:22} {nom:11} {e.mean / MILLION:>10.2f} ± {e.half_width / MILLION:<5.2f} "
                  f"{arrondi_millions(centre):>14} "
                  f"{arrondi_millions(bas) + ' à ' + arrondi_millions(haut):>24}")

    print("\nÀ lire avec : marge non spécifique à l'Algérie, dispersion des arrivées non mesurée,")
    print("capacités de stockage inconnues. Ce sont des ordres de grandeur, pas des prévisions.")

    if args.mesure:
        print_mesure(raw, marge_bas, marge_centre, marge_haut, args)
    return 0


def print_mesure(raw, marge_bas, marge_centre, marge_haut, args) -> None:
    """Le chiffre appliqué du site : le manque à produire en plage, pas en point.

    Les lignes du tableau ci-dessus fixent c_a² à 0 ou à 1 par convention, et le stockage à
    sa valeur centrale : trois hypothèses pour un seul montant. Ici les deux coordonnées
    inconnues du site sont **tirées** — c_a² dans son échantillon bootstrap, chaque capacité
    de stockage dans sa propre fourchette — et le résultat est la distribution du manque à
    produire, dont on publie les quantiles. Deux variantes, avec et sans le trimestre
    anormal 2022 T3, parce que leur écart *est* la mesure de fragilité du résultat.
    """
    bs = _beta_site()
    base = SimConfig.from_scenario(load_scenario(SCENARIO_MESURE))
    cible = bs.cible_t(base, args.years) / args.years
    navires = bs.navires_par_trimestre()
    sans_anormal = bs.sans_trimestre_anormal(navires, raw)

    print(f"\n{'=' * 96}")
    print(f"Chiffre appliqué — scénario {SCENARIO_MESURE} : le manque à produire en plage")
    print(f"{'=' * 96}")
    print(f"  {args.tirages} tirages, une réplication d'un an chacun ; c_a² tiré dans son "
          "échantillon bootstrap,")
    print("  chaque capacité de stockage tirée dans sa propre fourchette.")
    print(f"  Cible de production : {cible / MILLION:.3f} Mt/an.")
    print(f"\n  {'variante':46} {'manque à produire (Mt/an)':>28} {'coût (M USD/an)':>22}")

    for nom, serie in (("A — tous les trimestres", navires),
                       ("B — sans le trimestre anormal 2022 T3", sans_anormal)):
        rng = random.Random(args.seed)
        echantillon = bs.echantillon_bootstrap(serie, args.tirages * 4, rng)
        resultat = bs.propager(base, raw, echantillon, args.tirages, args.years, rng)
        pertes = perte_depuis_beta(resultat["beta"], cible)
        plage = plage_de_cout(pertes, bs.quantiles(pertes), marge_bas, marge_centre,
                              marge_haut)
        intervalle = (f"{plage['perte_mediane_t'] / MILLION:.3f} "
                      f"[{plage['perte_bas_t'] / MILLION:.3f} ; "
                      f"{plage['perte_haut_t'] / MILLION:.3f}]")
        cout = (f"{arrondi_millions(plage['cout_median'])} "
                f"[{arrondi_millions(plage['cout_bas'])} ; "
                f"{arrondi_millions(plage['cout_haut'])}]")
        print(f"  {nom:46} {intervalle:>28} {cout:>22}")

    print("\n  Médiane et intervalle à 90 % des tirages, croisés avec la fourchette de la marge")
    print("  unitaire. La borne basse est une année où l'irrégularité et le stockage tombent")
    print("  tous deux du bon côté **et** où la tonne manquante vaut peu : ce n'est pas un")
    print("  scénario central, c'est un coin de la distribution. Ne jamais citer une seule")
    print("  des trois valeurs sans les deux autres.")


if __name__ == "__main__":
    sys.exit(main())
