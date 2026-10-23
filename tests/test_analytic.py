"""Tests de `corridor.sim.analytic` : les formules, puis la validation du moteur de file.

Deux niveaux. D'abord les formules seules, confrontées à des cas dont la réponse est
connue à la main (M/M/1, M/D/1). Ensuite **la validation du modèle** : l'attente moyenne
simulée sur le scénario `baseline_sans_blocage` doit contenir la valeur exacte de
Pollaczek-Khinchine dans son intervalle de confiance. C'est le test de référence de
l'étape 4 : s'il tombe, c'est le moteur de file qu'il faut regarder, pas la tolérance.
"""

import math
import random

import pytest

from corridor.sim import analytic as an
from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.run import replicate
from corridor.sim.samplers import Samplers, from_mean_cv
from corridor.sim.stats import estimate

# --- paramètres de la validation --------------------------------------------
#
# 200 réplications comme demandé. L'horizon de 8 ans, lui, n'est pas cosmétique : à 1 an,
# la moyenne simulée est biaisée d'environ **−9 % sous** la valeur théorique, pour deux
# raisons qui ne sont pas des défauts du modèle — le système démarre à vide (les premiers
# navires attendent moins qu'en régime stationnaire) et les navires encore en rade à
# l'horizon sont exclus de `waits_h`, alors que ce sont justement les attentes longues.
# Avec ~21 navires par an, ce transitoire pèse trop lourd. Pollaczek-Khinchine prédit un
# régime **stationnaire** : il faut donc le comparer à une mesure stationnaire.
#
# Biais mesuré, moyenne sur 20 graines maîtresses × 200 réplications :
#
#     horizon    1 an     2 ans    4 ans    8 ans    16 ans
#     biais     −9,41 %  −4,21 %  −1,84 %  −1,41 %  −0,95 %
#     graines    10/20    13/20    19/20    19/20    17/20   contenant P-K
#
# 8 ans est le meilleur compromis : le biais résiduel (−1,4 %) est largement contenu dans
# la demi-largeur de l'IC (±1,4 h, soit ±3,6 %), et 19 graines sur 20 passent. Au-delà,
# l'intervalle se resserre plus vite que le biais ne décroît et le test redevient fragile
# sans rien valider de plus — c'est pourquoi 16 ans fait moins bien que 8.
VALIDATION_REPS = 200
VALIDATION_YEARS = 8.0

#: Tirages pour estimer E[S] et E[S²]. 25 000 suffisent très largement : l'erreur de
#: Monte-Carlo sur la valeur de Pollaczek-Khinchine est alors de ±0,09 h d'écart-type
#: (mesuré sur 12 graines contre une référence à 2 millions de tirages), soit 0,2 % —
#: deux ordres de grandeur sous la demi-largeur de l'IC des réplications (±1,4 h). En
#: prendre 200 000 coûterait 4 secondes de suite de tests pour rien.
MOMENT_DRAWS = 25_000


def _samplers_constants(taille_t: float, cadence_t_par_h: float) -> Samplers:
    """Jeu d'aléas entièrement déterministe : sert à vérifier les cas dégénérés."""
    constante = from_mean_cv(1.0, 0.0)
    return Samplers(
        interval_h=constante,
        vessel_size_t=from_mean_cv(taille_t, 0.0),
        discharge_rate_t_par_h=from_mean_cv(cadence_t_par_h, 0.0),
        load_h=constante,
        travel_h=constante,
        unload_h=constante,
    )


# --- utilisation et variabilité ---------------------------------------------


def test_utilisation_est_le_produit_du_taux_et_du_service():
    assert an.utilisation(0.5, 1.0) == pytest.approx(0.5)
    assert an.utilisation(1.0 / 429.2, 142.7) == pytest.approx(142.7 / 429.2)


@pytest.mark.parametrize(("lam", "es"), [(0.0, 1.0), (-1.0, 1.0), (1.0, 0.0), (1.0, -2.0)])
def test_utilisation_refuse_des_grandeurs_non_positives(lam, es):
    with pytest.raises(ValueError):
        an.utilisation(lam, es)


def test_scv_vaut_zero_pour_un_service_deterministe():
    assert an.scv(10.0, 100.0) == pytest.approx(0.0)


def test_scv_vaut_un_pour_un_service_exponentiel():
    """Pour une exponentielle de moyenne m, E[S²] = 2 m² donc c² = 1."""
    assert an.scv(10.0, 200.0) == pytest.approx(1.0)


def test_scv_refuse_des_moments_impossibles():
    """E[S²] < E[S]² violerait Cauchy-Schwarz : c'est une erreur d'entrée, pas un cas limite."""
    with pytest.raises(ValueError, match="Cauchy-Schwarz"):
        an.scv(10.0, 99.0)


# --- Pollaczek-Khinchine ----------------------------------------------------


def test_pk_retrouve_la_formule_exacte_de_m_m_1():
    """M/M/1 : Wq = ρ/(μ − λ). Avec λ = 0,5 et E[S] = 1 (donc μ = 1), Wq = 1 h."""
    wq = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=2.0)
    assert wq == pytest.approx(0.5 / (1.0 - 0.5))


def test_pk_donne_la_moitie_pour_un_service_deterministe():
    """M/D/1 : E[S²] = E[S]², donc Wq vaut exactement la moitié du cas exponentiel."""
    exponentiel = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=2.0)
    deterministe = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=1.0)
    assert deterministe == pytest.approx(exponentiel / 2.0)


def test_pk_croit_avec_l_utilisation():
    attentes = [an.pollaczek_khinchine(lam, 1.0, 2.0) for lam in (0.1, 0.5, 0.9, 0.99)]
    assert attentes == sorted(attentes)
    assert attentes[-1] > 50 * attentes[0]  # explosion en 1/(1−ρ)


def test_pk_croit_avec_la_variabilite_du_service():
    """À E[S] et ρ identiques, seule la variance du service change l'attente."""
    peu = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=1.0)
    beaucoup = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=5.0)
    assert beaucoup > peu


def test_pk_leve_sur_une_file_saturee():
    with pytest.raises(ValueError, match="saturée"):
        an.pollaczek_khinchine(lam=1.0, es=1.0, es2=2.0)
    with pytest.raises(ValueError, match="saturée"):
        an.pollaczek_khinchine(lam=2.0, es=1.0, es2=2.0)


def test_pk_refuse_des_moments_impossibles():
    with pytest.raises(ValueError, match="Cauchy-Schwarz"):
        an.pollaczek_khinchine(lam=0.5, es=1.0, es2=0.5)


# --- Kingman ----------------------------------------------------------------


def test_kingman_coincide_avec_m_m_1():
    """À c_a² = c_s² = 1, l'approximation devient la formule exacte du M/M/1."""
    kingman = an.kingman(rho=0.5, ca2=1.0, cs2=1.0, es=1.0)
    pk = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=2.0)
    assert kingman == pytest.approx(pk)


def test_kingman_sous_estime_le_m_d_1_a_faible_utilisation():
    """Cas connu : à c_a²=1 et c_s²=0, Kingman donne la moitié de M/D/1... soit la valeur exacte.

    Avec (c_a² + c_s²)/2 = 0,5, Kingman reproduit ici exactement Pollaczek-Khinchine pour
    un service déterministe. L'approximation n'est donc pas systématiquement fausse ; ce
    qui la rend approchée, c'est le cas général où les deux variabilités ne se composent
    pas additivement.
    """
    kingman = an.kingman(rho=0.5, ca2=1.0, cs2=0.0, es=1.0)
    pk = an.pollaczek_khinchine(lam=0.5, es=1.0, es2=1.0)
    assert kingman == pytest.approx(pk)


def test_kingman_est_nul_a_utilisation_nulle():
    assert an.kingman(rho=0.0, ca2=1.0, cs2=1.0, es=10.0) == 0.0


def test_kingman_croit_avec_chacun_de_ses_trois_facteurs():
    base = an.kingman(rho=0.5, ca2=1.0, cs2=1.0, es=1.0)
    assert an.kingman(rho=0.8, ca2=1.0, cs2=1.0, es=1.0) > base
    assert an.kingman(rho=0.5, ca2=2.0, cs2=1.0, es=1.0) > base
    assert an.kingman(rho=0.5, ca2=1.0, cs2=2.0, es=1.0) > base
    assert an.kingman(rho=0.5, ca2=1.0, cs2=1.0, es=2.0) > base


@pytest.mark.parametrize(
    ("rho", "ca2", "cs2", "es"),
    [(1.0, 1.0, 1.0, 1.0), (-0.1, 1.0, 1.0, 1.0), (0.5, -1.0, 1.0, 1.0),
     (0.5, 1.0, -1.0, 1.0), (0.5, 1.0, 1.0, 0.0)],
)
def test_kingman_refuse_des_entrees_hors_domaine(rho, ca2, cs2, es):
    with pytest.raises(ValueError):
        an.kingman(rho, ca2, cs2, es)


# --- moments du temps de service --------------------------------------------


def test_moments_d_un_service_deterministe():
    """Tonnage et cadence constants : S vaut exactement leur rapport, sans dispersion."""
    moments = an.service_moments(_samplers_constants(140_000.0, 1_000.0), draws=100)
    assert moments.es == pytest.approx(140.0)
    assert moments.es2 == pytest.approx(140.0**2)
    assert moments.scv == pytest.approx(0.0)
    assert moments.draws == 100


def test_moments_reproductibles_a_graine_egale():
    config = SimConfig.from_scenario(load_scenario("baseline"))
    samplers = Samplers.from_config(config)
    a = an.service_moments(samplers, draws=5_000, rng=random.Random(7))
    b = an.service_moments(samplers, draws=5_000, rng=random.Random(7))
    assert (a.es, a.es2) == (b.es, b.es2)


def test_moments_coherents_avec_la_cadence_des_hypotheses():
    """E[S] doit valoir le tonnage moyen divisé par la cadence, à la dispersion près.

    L'écart attendu est positif : E[1/cadence] > 1/E[cadence] (inégalité de Jensen),
    donc décharger à cadence variable prend en moyenne plus longtemps qu'à cadence fixe.
    """
    config = SimConfig.from_scenario(load_scenario("baseline"))
    moments = an.service_moments(Samplers.from_config(config), draws=20_000, rng=random.Random(3))
    nominal = config.taille_navire_moyenne_t / config.cadence_dechargement_t_par_h
    assert moments.es > nominal
    assert moments.es == pytest.approx(nominal, rel=0.05)
    assert moments.scv > 0.0


def test_moments_refusent_un_effectif_trop_petit():
    with pytest.raises(ValueError):
        an.service_moments(_samplers_constants(1.0, 1.0), draws=1)


# --- service effectif -------------------------------------------------------


def test_service_effectif_ajoute_le_blocage_par_navire():
    assert an.effective_service(es=100.0, blocked_h=200.0, vessels_served=10) == pytest.approx(120.0)


def test_service_effectif_vaut_le_service_sans_blocage():
    assert an.effective_service(es=100.0, blocked_h=0.0, vessels_served=10) == 100.0


def test_service_effectif_refuse_un_denominateur_nul_ou_un_blocage_negatif():
    with pytest.raises(ValueError, match="aucun navire servi"):
        an.effective_service(es=100.0, blocked_h=10.0, vessels_served=0)
    with pytest.raises(ValueError, match="négatif"):
        an.effective_service(es=100.0, blocked_h=-1.0, vessels_served=10)


# --- VALIDATION DU MOTEUR DE FILE -------------------------------------------
#
# Ces tests sont marqués `slow` : 200 réplications de 8 ans coûtent quelques secondes, ce
# qui ferait sortir `pytest -q` de son budget d'une trentaine de secondes. Ils ne sont pas
# optionnels pour autant — la CI les lance dans une étape dédiée, et il faut les lancer
# avant tout commit qui touche à `corridor.sim` :
#
#     pytest -m slow -q
#
# L'exclusion est portée par `addopts` dans pyproject.toml.


@pytest.fixture(scope="module")
def validation():
    """Rejoue `baseline_sans_blocage` et calcule la prédiction analytique correspondante.

    Portée module : les 200 réplications ne sont jouées qu'une fois pour tous les tests
    de cette section.
    """
    config = SimConfig.from_scenario(load_scenario("baseline_sans_blocage"))
    moments = an.service_moments(
        Samplers.from_config(config), draws=MOMENT_DRAWS, rng=random.Random(0)
    )
    lam = 1.0 / config.intervalle_moyen_arrivees_h
    resultats = replicate(
        config, VALIDATION_YEARS, VALIDATION_REPS, config.graine_par_defaut, "validation"
    )
    return {
        "config": config,
        "moments": moments,
        "lam": lam,
        "rho": an.utilisation(lam, moments.es),
        "wq": an.pollaczek_khinchine(lam, moments.es, moments.es2),
        "attente": estimate([r.wait_mean_h for r in resultats]),
        "occupation": estimate([r.berth_occupancy for r in resultats]),
        "resultats": resultats,
    }


@pytest.mark.slow
def test_le_scenario_de_validation_exclut_tout_blocage(validation):
    """Sans cette condition, Pollaczek-Khinchine ne s'applique pas : à vérifier d'abord.

    Un seul poste, des stocks qui ne saturent jamais et qui ne se vident jamais : la durée
    d'occupation du quai ne dépend donc que du navire, pas de l'état du corridor.
    """
    config = validation["config"]
    assert config.postes_minerai == 1
    assert config.dispersion_arrivees == 1.0
    for resultat in validation["resultats"]:
        # le stockyard ne sature jamais : aucun navire ne peut rester à quai sans décharger
        assert resultat.stockyard_final_t < config.capacite_stockyard_t
        # et le stock usine ne s'épuise jamais (`dri_stop_hours` ne compte que l'imprévu)
        assert resultat.dri_stop_hours == 0.0


@pytest.mark.slow
def test_la_file_est_stable(validation):
    assert 0.0 < validation["rho"] < 1.0


@pytest.mark.slow
def test_l_occupation_du_poste_simulee_vaut_rho(validation):
    """Première vérification, indépendante de l'attente : le poste doit être occupé ρ du temps.

    C'est le contrôle le plus direct du temps de service : si l'occupation tombait à côté
    de ρ, c'est que la durée de déchargement simulée n'est pas celle qu'on croit, et toute
    comparaison d'attente serait vide de sens.
    """
    assert validation["occupation"].contains(validation["rho"])


@pytest.mark.slow
def test_l_attente_simulee_contient_pollaczek_khinchine(validation):
    """LE test de validation du moteur de file : simulation contre formule exacte.

    En cas d'échec, ne pas élargir l'intervalle : chercher la cause. Dans l'ordre, les
    suspects sont la gestion du poste (`berths.request`), l'horloge, la loi effective des
    aléas, et le biais d'horizon fini documenté en tête de ce fichier.
    """
    attente = validation["attente"]
    assert attente.defined
    assert attente.contains(validation["wq"]), (
        f"attente simulée {attente.mean:.2f} ± {attente.half_width:.2f} h "
        f"(IC 95 % [{attente.low:.2f}, {attente.high:.2f}]) ne contient pas "
        f"Pollaczek-Khinchine = {validation['wq']:.2f} h"
    )


@pytest.mark.slow
def test_l_ecart_relatif_a_la_theorie_reste_petit(validation):
    """Garde-fou indépendant de l'effectif : l'écart relatif doit rester sous 10 %.

    L'intervalle de confiance se resserre quand on ajoute des réplications ; cet écart-là
    ne bouge pas. Les deux tests ensemble disent donc : ni biais, ni dispersion anormale.
    """
    ecart = validation["attente"].mean / validation["wq"] - 1.0
    assert math.isfinite(ecart)
    assert abs(ecart) < 0.10, f"écart relatif à la théorie de {ecart:+.1%}"
