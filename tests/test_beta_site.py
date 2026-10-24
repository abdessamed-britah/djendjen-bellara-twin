"""Tests de scripts/beta_site.py : propagation d'incertitude vers le taux de service.

C'est le chiffre appliqué de l'étude, donc le script où une erreur silencieuse coûterait
le plus cher : un c_a² non élevé au carré, un stock initial resté celui de la configuration
de base, un quantile mal interpolé. Tout cela produirait un intervalle crédible et faux.
"""

import importlib.util
import math
import random
import statistics
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "beta_site", Path(__file__).parent.parent / "scripts" / "beta_site.py"
)
bs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bs)

RAW = load_yaml(ASSUMPTIONS_PATH)


@pytest.fixture(scope="module")
def base() -> SimConfig:
    return SimConfig.from_scenario(load_scenario("phase2_mesure"))


# --- quantiles --------------------------------------------------------------


def test_les_quantiles_encadrent_la_mediane():
    valeurs = [0.80, 0.85, 0.90, 0.95, 1.00]
    q = bs.quantiles(valeurs)
    assert q[0.50] == pytest.approx(0.90)
    assert q[0.05] <= q[0.50] <= q[0.95]


def test_le_quantile_interpole_entre_deux_points():
    """Sur deux valeurs, la médiane est leur milieu : sinon l'interpolation est fausse."""
    assert bs.quantiles([0.0, 1.0])[0.50] == pytest.approx(0.5)


def test_les_quantiles_extremes_sont_les_bornes():
    valeurs = [0.2, 0.4, 0.6, 0.8]
    q = bs.quantiles(valeurs, niveaux=(0.0, 1.0))
    assert q[0.0] == pytest.approx(0.2)
    assert q[1.0] == pytest.approx(0.8)


def test_les_quantiles_ne_dependent_pas_de_l_ordre():
    melange = [0.9, 0.1, 0.5, 0.7, 0.3]
    assert bs.quantiles(melange) == bs.quantiles(sorted(melange))


def test_les_quantiles_d_une_serie_vide_sont_indefinis():
    assert all(math.isnan(v) for v in bs.quantiles([]).values())


# --- tirage des paramètres --------------------------------------------------


def test_le_c_a2_est_eleve_a_la_racine_pour_le_modele(base):
    """Le modèle attend un coefficient de variation, c_a² en est le carré.

    L'erreur la plus facile à commettre et la plus difficile à voir : elle donnerait un
    corridor beaucoup trop régulier sans rien casser.
    """
    config = bs.tirer_parametres(base, 0.64, RAW, random.Random(1))
    assert config.dispersion_arrivees == pytest.approx(0.8)


def test_un_c_a2_negatif_ne_fait_pas_exploser_la_racine(base):
    """Un tirage bootstrap peut théoriquement sortir à zéro ; il ne doit pas lever."""
    config = bs.tirer_parametres(base, -0.01, RAW, random.Random(1))
    assert config.dispersion_arrivees == 0.0


def test_les_capacites_sont_tirees_dans_leurs_fourchettes_publiees(base):
    port = RAW["stockage_port"]["capacite_stockyard_pellets"]["range"]
    usine = RAW["usine"]["capacite_stock_pellets_usine"]["range"]
    for graine in range(40):
        config = bs.tirer_parametres(base, 0.8, RAW, random.Random(graine))
        assert port[0] <= config.capacite_stockyard_t <= port[1]
        assert usine[0] <= config.capacite_stock_usine_t <= usine[1]


def test_les_deux_capacites_sont_tirees_independamment(base):
    """Tirer le total ferait descendre la part du port sous son propre minimum publié."""
    couples = {
        (round(c.capacite_stockyard_t), round(c.capacite_stock_usine_t))
        for c in (bs.tirer_parametres(base, 0.8, RAW, random.Random(g)) for g in range(30))
    }
    ports = {p for p, _ in couples}
    usines = {u for _, u in couples}
    assert len(ports) > 20 and len(usines) > 20
    # aucune proportion fixe entre les deux : le rapport varie d'un tirage à l'autre
    rapports = {round(p / u, 3) for p, u in couples}
    assert len(rapports) > 20


def test_les_stocks_initiaux_suivent_les_capacites_tirees(base):
    """Sans cela, le tirage mesurerait un corridor démarré avec le stock d'un autre."""
    config = bs.tirer_parametres(base, 0.8, RAW, random.Random(3))
    assert config.stock_initial_stockyard_t == pytest.approx(
        config.capacite_stockyard_t * config.fraction_stock_initial
    )
    assert config.stock_initial_usine_t == pytest.approx(
        config.capacite_stock_usine_t * config.fraction_stock_initial
    )


def test_le_tirage_est_reproductible_a_graine_egale(base):
    a = bs.tirer_parametres(base, 0.8, RAW, random.Random(7))
    b = bs.tirer_parametres(base, 0.8, RAW, random.Random(7))
    assert a.capacite_stockyard_t == b.capacite_stockyard_t
    assert a.capacite_stock_usine_t == b.capacite_stock_usine_t


def test_le_tirage_ne_touche_pas_a_la_cible(base):
    """β se compare entre tirages : la cible doit rester la même partout."""
    cibles = {
        round(bs.cible_t(bs.tirer_parametres(base, 0.8, RAW, random.Random(g)), 1.0), 3)
        for g in range(10)
    }
    assert len(cibles) == 1


# --- propagation ------------------------------------------------------------


def test_la_propagation_renvoie_autant_de_beta_que_de_tirages(base):
    rng = random.Random(5)
    sortie = bs.propager(base, RAW, [0.5, 0.8, 1.2], tirages=4, annees=0.2, rng=rng)
    assert len(sortie["beta"]) == 4
    assert len(sortie["c_a2"]) == 4
    assert len(sortie["tau"]) == 4


def test_les_beta_sont_des_taux_plausibles(base):
    rng = random.Random(5)
    sortie = bs.propager(base, RAW, [0.8], tirages=3, annees=0.2, rng=rng)
    assert all(0.0 <= b <= 1.01 for b in sortie["beta"])


def test_le_tau_rapporte_est_une_couverture_en_jours(base):
    rng = random.Random(5)
    sortie = bs.propager(base, RAW, [0.8], tirages=5, annees=0.2, rng=rng)
    assert all(5.0 < tau < 90.0 for tau in sortie["tau"])


@pytest.mark.slow
def test_moyenner_plusieurs_annees_resserre_la_distribution(base):
    """C'est tout l'objet de la variante de long terme : l'aléa annuel s'estompe.

    Marqué `slow` : il simule des dizaines d'années. Le hook de pré-commit le relance dès
    que `src/corridor/sim/` bouge, ce dont il dépend.
    """
    une = bs.propager(base, RAW, [0.8], tirages=10, annees=0.25, rng=random.Random(2))
    plusieurs = bs.propager(base, RAW, [0.8], tirages=10, annees=0.25,
                            rng=random.Random(2), reps=4)
    assert statistics.stdev(plusieurs["beta"]) < statistics.stdev(une["beta"])


def test_la_propagation_est_reproductible_a_graine_egale(base):
    a = bs.propager(base, RAW, [0.5, 0.9], tirages=3, annees=0.2, rng=random.Random(11))
    b = bs.propager(base, RAW, [0.5, 0.9], tirages=3, annees=0.2, rng=random.Random(11))
    assert a["beta"] == b["beta"]


@pytest.mark.slow
def test_un_corridor_plus_irregulier_produit_moins(base):
    """Le sens du résultat : si cette monotonie tombe, le tirage ne mesure pas ce qu'il dit.

    Marqué `slow` pour la même raison que le test précédent.
    """
    regulier = bs.propager(base, RAW, [0.0], tirages=5, annees=0.4, rng=random.Random(4))
    erratique = bs.propager(base, RAW, [2.0], tirages=5, annees=0.4, rng=random.Random(4))
    assert statistics.fmean(regulier["beta"]) > statistics.fmean(erratique["beta"])


# --- corrélation ------------------------------------------------------------


def test_la_correlation_vaut_un_sur_une_relation_affine():
    assert bs.correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert bs.correlation([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)


def test_la_correlation_est_indefinie_sur_une_serie_constante():
    assert math.isnan(bs.correlation([1, 1, 1], [1, 2, 3]))
    assert math.isnan(bs.correlation([1], [2]))


# --- série des bilans de trafic ---------------------------------------------


def test_les_navires_par_trimestre_viennent_des_bilans_archives():
    navires = bs.navires_par_trimestre()
    assert len(navires) == 16
    assert all(n >= 0 for n in navires)
    assert statistics.fmean(navires) > 1.0


def test_l_echantillon_bootstrap_est_strictement_positif():
    echantillon = bs.echantillon_bootstrap([3.0, 5.0, 8.0, 2.0], 500, random.Random(1))
    assert len(echantillon) > 400
    assert all(x > 0 for x in echantillon)


def test_l_echantillon_bootstrap_entoure_l_indice_observe():
    valeurs = [3.0, 5.0, 8.0, 2.0, 6.0, 4.0]
    echantillon = bs.echantillon_bootstrap(valeurs, 2000, random.Random(1))
    observe = bs.index_of_dispersion(valeurs)
    assert min(echantillon) <= observe <= max(echantillon)


def test_le_seuil_du_trimestre_anormal_ecarte_exactement_un_trimestre():
    """Garde-fou : si la série change, ce test dit que le seuil doit être revu."""
    navires = bs.navires_par_trimestre()
    taille = float(RAW["navires_minerai"]["taille_moyenne"]["value"])
    ecartes = [n for n in navires if n * taille < bs.SEUIL_TRIMESTRE_ANORMAL_T]
    assert len(ecartes) == 1


# --- sorties ----------------------------------------------------------------


def test_la_variante_s_affiche_sans_lever(base, capsys):
    rng = random.Random(5)
    sortie = bs.propager(base, RAW, [0.8, 1.0], tirages=5, annees=0.2, rng=rng)
    bs.print_variante("Variante de test", sortie, 0.83, 0.05)
    texte = capsys.readouterr().out
    assert "taux de service" in texte
    assert "intervalle à 90 %" in texte


def test_une_decomposition_impossible_est_dite(base, capsys):
    """Quand le bruit dépasse la dispersion totale, ne pas imprimer un pourcentage absurde."""
    sortie = {"beta": [0.9, 0.9, 0.9001], "c_a2": [0.8] * 3, "tau": [20.0] * 3, "reps": 1}
    bs.print_variante("Variante de test", sortie, 0.83, bruit=0.5)
    assert "non concluante" in capsys.readouterr().out
