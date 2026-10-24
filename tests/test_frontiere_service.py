"""Tests de scripts/frontiere_service.py : adimensionnement, couverture requise, figure.

L'enjeu de ce livrable est la conversion entre grandeurs physiques et grandeurs sans
dimension : une erreur sur τ ou sur c_a² ne ferait rien tomber, elle déplacerait seulement
la frontière. C'est donc cette conversion qui est vérifiée ici, en plus de la lecture des
contours.
"""

import importlib.util
import math
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, SimConfig, load_scenario, load_yaml
from corridor.sim.sensitivity import storage_shares
from corridor.sim.stats import estimate

_SPEC = importlib.util.spec_from_file_location(
    "frontiere_service", Path(__file__).parent.parent / "scripts" / "frontiere_service.py"
)
fs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fs)

RAW = load_yaml(ASSUMPTIONS_PATH)
SHARES = storage_shares(RAW)


@pytest.fixture(scope="module")
def base() -> SimConfig:
    return SimConfig.from_scenario(load_scenario("phase2"))


def _cases(valeurs):
    """Carte fabriquée : {(c_a², τ): β constant}, pour tester la lecture des contours."""
    return {cle: estimate([v, v]) for cle, v in valeurs.items()}


def _carte_monotone(seuil_par_c_a2):
    """β croissant avec τ, franchissant 0,95 au τ indiqué pour chaque c_a²."""
    cases = {}
    for c_a2 in fs.C_A2_VALUES:
        seuil = seuil_par_c_a2[c_a2]
        for tau in fs.TAU_DAYS:
            cases[(c_a2, tau)] = estimate([0.90 + 0.10 * min(tau / seuil, 1.0)] * 2)
    return cases


# --- adimensionnement -------------------------------------------------------


def test_la_consommation_journaliere_est_le_debit_horaire_sur_24h(base):
    attendu = base.debit_pellets_t_par_h * 24.0
    assert fs.consommation_journaliere_t(base) == pytest.approx(attendu)


def test_la_couverture_est_le_stock_divise_par_la_consommation(base):
    total = base.capacite_stockyard_t + base.capacite_stock_usine_t
    attendu = total / fs.consommation_journaliere_t(base)
    assert fs.couverture_jours(base) == pytest.approx(attendu)


def test_un_point_de_la_carte_a_bien_la_couverture_demandee(base):
    """Le cœur de l'adimensionnement : τ demandé = τ obtenu, quel que soit le scénario."""
    for tau in (5.0, 27.5, 60.0):
        config = fs.point_config(base, c_a2=1.0, tau=tau, postes=1, shares=SHARES)
        assert fs.couverture_jours(config) == pytest.approx(tau)


def test_la_dispersion_est_la_racine_du_c_a2(base):
    """`dispersion_arrivees` est un coefficient de variation, c_a² est son carré."""
    config = fs.point_config(base, c_a2=1.0, tau=20.0, postes=1, shares=SHARES)
    assert config.dispersion_arrivees == pytest.approx(1.0)
    config = fs.point_config(base, c_a2=0.25, tau=20.0, postes=1, shares=SHARES)
    assert config.dispersion_arrivees == pytest.approx(0.5)
    config = fs.point_config(base, c_a2=1.5, tau=20.0, postes=1, shares=SHARES)
    assert config.dispersion_arrivees == pytest.approx(math.sqrt(1.5))


def test_un_point_de_la_carte_pose_le_nombre_de_postes(base):
    for postes in (1, 2):
        config = fs.point_config(base, c_a2=1.0, tau=20.0, postes=postes, shares=SHARES)
        assert config.postes_minerai == postes


def test_les_stocks_initiaux_suivent_les_capacites(base):
    """`with_storage` doit recalculer les stocks initiaux, sinon le point mesure autre chose."""
    config = fs.point_config(base, c_a2=1.0, tau=10.0, postes=1, shares=SHARES)
    assert config.stock_initial_stockyard_t == pytest.approx(
        config.capacite_stockyard_t * config.fraction_stock_initial
    )
    assert config.stock_initial_usine_t == pytest.approx(
        config.capacite_stock_usine_t * config.fraction_stock_initial
    )


def test_toute_la_grille_est_constructible(base):
    """Aucun point du domaine ne doit être refusé, notamment aux plus faibles couvertures."""
    for c_a2 in fs.C_A2_VALUES:
        for tau in fs.TAU_DAYS:
            for postes in fs.POSTES:
                fs.point_config(base, c_a2, tau, postes, SHARES)


def test_le_nombre_de_postes_ne_change_pas_la_cible(base):
    """β se compare entre 1 et 2 postes : la cible doit donc être la même."""
    cibles = {
        fs.cible_t(fs.point_config(base, 1.0, 20.0, postes, SHARES), 1.0)
        for postes in fs.POSTES
    }
    assert len(cibles) == 1


# --- lecture des contours ---------------------------------------------------


def test_la_couverture_requise_interpole_entre_deux_points():
    """β passe de 0,94 à 0,96 entre 10 et 15 jours : le seuil 0,95 tombe au milieu."""
    cases = _cases({(c, t): 0.5 for c in fs.C_A2_VALUES for t in fs.TAU_DAYS})
    cases[(0.0, 10.0)] = estimate([0.94, 0.94])
    cases[(0.0, 15.0)] = estimate([0.96, 0.96])
    cases[(0.0, 5.0)] = estimate([0.90, 0.90])
    assert fs.couverture_requise(cases, 0.95)[0.0] == pytest.approx(12.5)


def test_la_couverture_requise_vaut_le_minimum_si_le_niveau_est_deja_atteint():
    cases = _cases({(c, t): 0.999 for c in fs.C_A2_VALUES for t in fs.TAU_DAYS})
    assert fs.couverture_requise(cases, 0.95)[0.0] == min(fs.TAU_DAYS)


def test_la_couverture_requise_est_none_si_le_niveau_n_est_jamais_atteint():
    """Un niveau hors d'atteinte est une information, pas un trou à combler."""
    cases = _cases({(c, t): 0.80 for c in fs.C_A2_VALUES for t in fs.TAU_DAYS})
    assert fs.couverture_requise(cases, 0.95)[0.0] is None


def test_plus_les_arrivees_sont_irregulieres_plus_il_faut_de_couverture():
    """La lecture même de la figure : la frontière monte avec c_a²."""
    seuils = {c_a2: 10.0 + 20.0 * c_a2 for c_a2 in fs.C_A2_VALUES}
    requis = fs.couverture_requise(_carte_monotone(seuils), 0.95)
    valeurs = [requis[c_a2] for c_a2 in fs.C_A2_VALUES]
    assert all(v is not None for v in valeurs)
    assert valeurs == sorted(valeurs)


def test_la_matrice_suit_l_ordre_des_deux_axes():
    """`contour` attend β[τ][c_a²] : une transposition retournerait la figure."""
    cases = _cases({(c, t): c + t for c in fs.C_A2_VALUES for t in fs.TAU_DAYS})
    m = fs.matrice(cases)
    assert len(m) == len(fs.TAU_DAYS)
    assert len(m[0]) == len(fs.C_A2_VALUES)
    assert m[0][0] == pytest.approx(fs.C_A2_VALUES[0] + fs.TAU_DAYS[0])
    assert m[-1][0] == pytest.approx(fs.C_A2_VALUES[0] + fs.TAU_DAYS[-1])
    assert m[0][-1] == pytest.approx(fs.C_A2_VALUES[-1] + fs.TAU_DAYS[0])


# --- bande du site ----------------------------------------------------------


def test_la_bande_du_site_encadre_sa_valeur_centrale(base):
    bas, centre, haut = fs.bande_du_site(base, RAW)
    assert bas < centre < haut
    assert bas > 0


def test_la_bande_du_site_correspond_aux_capacites_publiees(base):
    """La bande traduit en jours la fourchette de deux hypothèses de statut inconnu."""
    bas, centre, haut = fs.bande_du_site(base, RAW)
    d = fs.consommation_journaliere_t(base)
    port = RAW["stockage_port"]["capacite_stockyard_pellets"]
    usine = RAW["usine"]["capacite_stock_pellets_usine"]
    assert centre == pytest.approx((port["value"] + usine["value"]) / d)
    assert bas == pytest.approx((port["range"][0] + usine["range"][0]) / d)
    assert haut == pytest.approx((port["range"][1] + usine["range"][1]) / d)


# --- sorties ----------------------------------------------------------------


def _grilles_factices():
    seuils = {c_a2: 8.0 + 25.0 * c_a2 for c_a2 in fs.C_A2_VALUES}
    return {postes: _carte_monotone(seuils) for postes in fs.POSTES}


def test_le_tableau_s_affiche_sans_lever(base, capsys):
    fs.print_table(_grilles_factices(), base)
    sortie = capsys.readouterr().out
    assert "Taux de service" in sortie
    assert "Couverture τ requise" in sortie


def test_la_figure_est_un_png_non_vide(base, tmp_path):
    chemin = fs.write_figure(
        _grilles_factices(), base, RAW, "phase2", 30, tmp_path / "frontiere.png"
    )
    assert chemin.exists()
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert chemin.stat().st_size > 10_000


def test_la_figure_se_trace_meme_sans_contour_atteignable(base, tmp_path):
    """Si un niveau n'est atteint nulle part, la figure doit le dire, pas échouer."""
    plate = {
        postes: _cases({(c, t): 0.80 for c in fs.C_A2_VALUES for t in fs.TAU_DAYS})
        for postes in fs.POSTES
    }
    assert fs.write_figure(plate, base, RAW, "phase2", 30, tmp_path / "f.png").exists()


def test_la_carte_fait_l_aller_retour_par_parquet():
    """Retracer la figure ne doit pas coûter une nouvelle simulation de plusieurs minutes."""
    grilles = _grilles_factices()
    relues = fs.from_table(fs.to_table(grilles))
    assert set(relues) == set(grilles)
    for postes, cases in grilles.items():
        assert set(relues[postes]) == set(cases)
        for cle, beta in cases.items():
            assert relues[postes][cle].mean == pytest.approx(beta.mean)
            assert relues[postes][cle].n == beta.n
            assert relues[postes][cle].half_width == pytest.approx(beta.half_width)


def test_la_table_porte_une_ligne_par_point():
    table = fs.to_table(_grilles_factices())
    assert table.num_rows == len(fs.C_A2_VALUES) * len(fs.TAU_DAYS) * len(fs.POSTES)
    assert set(table.column_names) >= {"postes", "c_a2", "tau_jours", "beta", "reps"}


def test_une_teinte_par_poste_et_un_style_par_niveau():
    """Jamais deux informations sur le même canal visuel : c'est la règle de la figure."""
    assert set(fs.COULEURS) == set(fs.POSTES)
    assert len(set(fs.COULEURS.values())) == len(fs.POSTES)
    assert set(fs.STYLES) == set(fs.LEVELS)
    assert len(set(fs.STYLES.values())) == len(fs.LEVELS)
