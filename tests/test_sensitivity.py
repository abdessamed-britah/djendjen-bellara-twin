"""Tests de l'analyse de sensibilité.

La grille complète prend plusieurs minutes : ces tests la réduisent au strict
nécessaire (deux points, une réplication, quelques jours simulés) et vérifient la
mécanique — bornes de la fourchette, répartition du stockage, forme de la matrice,
écriture des sorties — pas la physique, déjà couverte par test_sim_model.py.
"""

import dataclasses

import pyarrow.parquet as pq
import pytest

from corridor.sim import sensitivity as sx
from corridor.sim.config import SimConfig, load_scenario, load_yaml
from corridor.sim.stats import estimate

ASSUMPTIONS = load_yaml("config/assumptions.yaml")
PETITE_GRILLE = {
    "scenarios": ("baseline",),
    "dispersions": (0.0, 1.0),
    "storage_levels": 2,
    "reps": 2,  # au moins deux réplications : sans dispersion, pas d'intervalle
    "years": 0.05,
    "seed": 11,
}


@pytest.fixture(scope="module")
def baseline() -> SimConfig:
    return SimConfig.from_scenario(load_scenario("baseline"))


@pytest.fixture(scope="module")
def points() -> list[sx.GridPoint]:
    return sx.run_grid(**PETITE_GRILLE)


# --- fourchette et répartition du stockage ---------------------------------


def test_les_bornes_somment_les_deux_fourchettes():
    bas, haut = sx.storage_bounds(ASSUMPTIONS)
    port = ASSUMPTIONS["stockage_port"]["capacite_stockyard_pellets"]["range"]
    usine = ASSUMPTIONS["usine"]["capacite_stock_pellets_usine"]["range"]
    assert bas == pytest.approx(port[0] + usine[0])
    assert haut == pytest.approx(port[1] + usine[1])


def test_les_proportions_viennent_des_valeurs_centrales():
    part_port, part_usine = sx.storage_shares(ASSUMPTIONS)
    port = ASSUMPTIONS["stockage_port"]["capacite_stockyard_pellets"]["value"]
    usine = ASSUMPTIONS["usine"]["capacite_stock_pellets_usine"]["value"]
    assert part_port == pytest.approx(port / (port + usine))
    assert part_port + part_usine == pytest.approx(1.0)


def test_les_niveaux_couvrent_la_fourchette():
    bas, haut = sx.storage_bounds(ASSUMPTIONS)
    niveaux = sx.storage_levels(ASSUMPTIONS, 5)
    assert len(niveaux) == 5
    assert niveaux[0] == pytest.approx(bas)
    assert niveaux[-1] == pytest.approx(haut)
    assert niveaux == sorted(niveaux)


def test_un_seul_niveau_donne_la_borne_basse():
    bas, _haut = sx.storage_bounds(ASSUMPTIONS)
    assert sx.storage_levels(ASSUMPTIONS, 1) == [pytest.approx(bas)]


def test_with_storage_repartit_et_recalcule_les_stocks_initiaux(baseline):
    parts = sx.storage_shares(ASSUMPTIONS)
    config = sx.with_storage(baseline, total_t=600_000.0, shares=parts)
    assert config.capacite_stockyard_t + config.capacite_stock_usine_t == pytest.approx(600_000.0)
    assert config.capacite_stockyard_t == pytest.approx(600_000.0 * parts[0])
    # les stocks initiaux suivent la fraction d'hypothèse, pas l'ancienne capacité
    assert config.stock_initial_stockyard_t == pytest.approx(
        config.capacite_stockyard_t * config.fraction_stock_initial
    )
    assert config.stock_initial_usine_t == pytest.approx(
        config.capacite_stock_usine_t * config.fraction_stock_initial
    )
    # le reste de la configuration est intact
    assert config.capacite_dri_t_par_an == baseline.capacite_dri_t_par_an


def test_with_storage_refuse_un_stock_usine_trop_petit_en_pull(baseline):
    with pytest.raises(ValueError, match="pull impossible"):
        sx.with_storage(baseline, total_t=3_000.0, shares=(0.9, 0.1))


def test_with_storage_accepte_un_stock_minuscule_en_push(baseline):
    push = dataclasses.replace(baseline, politique_expedition="push")
    config = sx.with_storage(push, total_t=3_000.0, shares=(0.9, 0.1))
    assert config.capacite_stock_usine_t == pytest.approx(300.0)


# --- parcours de la grille -------------------------------------------------


def test_la_grille_a_un_point_par_combinaison(points):
    attendu = (
        len(PETITE_GRILLE["scenarios"])
        * len(PETITE_GRILLE["dispersions"])
        * PETITE_GRILLE["storage_levels"]
    )
    assert len(points) == attendu
    assert {p.scenario for p in points} == set(PETITE_GRILLE["scenarios"])
    assert {p.dispersion for p in points} == set(PETITE_GRILLE["dispersions"])


def test_chaque_point_porte_sa_perte_et_ses_hypotheses(points):
    for point in points:
        assert point.reps == PETITE_GRILLE["reps"]
        assert point.dri_target_t > 0
        assert point.stockyard_t + point.plant_stock_t == pytest.approx(point.storage_total_t)
        attendu = (
            (point.dri_target_t - point.dri_production_t.mean) / point.dri_target_t * 100
        )
        assert point.dri_loss_pct.mean == pytest.approx(attendu)
        assert len(point.loss_by_rep) == point.reps
        assert point.dri_loss_pct.n == point.reps


def test_la_grille_est_reproductible():
    premier = sx.run_grid(**PETITE_GRILLE)
    second = sx.run_grid(**PETITE_GRILLE)
    assert [dataclasses.asdict(p) for p in premier] == [dataclasses.asdict(p) for p in second]


def test_la_perte_augmente_avec_l_irregularite(baseline):
    """Le résultat que la figure doit montrer : à stockage égal, l'irrégularité coûte."""
    petite = sx.run_grid(
        scenarios=("baseline",), dispersions=(0.0, 1.0), storage_levels=1, reps=4, years=1.0,
        seed=7,
    )
    regulier = next(p for p in petite if p.dispersion == 0.0)
    irregulier = next(p for p in petite if p.dispersion == 1.0)
    assert irregulier.dri_loss_pct.mean > regulier.dri_loss_pct.mean


# --- cases voisines indiscernables -----------------------------------------


def _case(dispersion, total, pertes, stock=0.0):
    """Point de grille fabriqué à la main : seules comptent ses pertes par réplication."""
    e = estimate(pertes)
    return sx.GridPoint(
        scenario="s", dispersion=dispersion, storage_total_t=total, stockyard_t=0.0,
        plant_stock_t=0.0, reps=len(pertes), years=1.0, dri_target_t=1.0,
        dri_production_t=e, dri_loss_pct=e, dri_stop_hours=e, wait_mean_h=e,
        wait_p90_h=e, berth_occupancy=e, rame_utilisation=e,
        waiting_tonnage_mean_t=estimate([stock] * len(pertes)), loss_by_rep=tuple(pertes),
    )


def test_une_case_nettement_differente_de_toutes_ses_voisines_est_distincte():
    grille = [
        _case(0.0, 1.0, [0.0, 0.1, 0.0, 0.1]),
        _case(1.0, 1.0, [10.0, 10.2, 9.9, 10.1]),
    ]
    assert sx.distinct_from_neighbours(grille, "s") == [[True, True]]


def test_une_case_indiscernable_d_une_voisine_est_signalee():
    grille = [
        _case(0.0, 1.0, [5.0, 6.0, 4.0, 5.5]),
        _case(1.0, 1.0, [5.2, 5.8, 4.1, 5.4]),  # même perte, au bruit près
        _case(0.0, 2.0, [20.0, 20.1, 19.9, 20.2]),
        _case(1.0, 2.0, [40.0, 40.3, 39.8, 40.1]),
    ]
    matrice = sx.distinct_from_neighbours(grille, "s")
    # ligne 0 (stockage 1) : les deux cases se confondent entre elles
    assert matrice[0] == [False, False]
    # ligne 1 (stockage 2) : chacune se distingue de toutes ses voisines
    assert matrice[1] == [True, True]


def test_l_appariement_distingue_des_cases_aux_intervalles_chevauchants():
    """Même bruit d'une réplication à l'autre, écart constant : distinct malgré le chevauchement."""
    bruit = [0.0, 8.0, -6.0, 4.0, -3.0, 7.0]
    a = _case(0.0, 1.0, [10.0 + b for b in bruit])
    b = _case(1.0, 1.0, [11.0 + b for b in bruit])
    assert a.dri_loss_pct.overlaps(b.dri_loss_pct)
    assert sx.distinct_from_neighbours([a, b], "s") == [[True, True]]


def test_sans_dispersion_mesuree_aucune_case_n_est_declaree_distincte():
    grille = [_case(0.0, 1.0, [1.0]), _case(1.0, 1.0, [50.0])]
    assert sx.distinct_from_neighbours(grille, "s") == [[False, False]]


# --- sorties ---------------------------------------------------------------


def test_la_table_aplatit_chaque_indicateur_en_quatre_colonnes(points):
    table = sx.to_table(points)
    for indicateur in sx.GRID_INDICATORS:
        for suffixe in ("mean", "std", "ci95_low", "ci95_high"):
            assert f"{indicateur}_{suffixe}" in table.column_names
    assert "loss_by_rep" in table.column_names
    assert table.num_rows == len(points)
    ligne = table.to_pylist()[0]
    assert ligne["dri_loss_pct_ci95_low"] <= ligne["dri_loss_pct_mean"]
    assert ligne["dri_loss_pct_mean"] <= ligne["dri_loss_pct_ci95_high"]
    assert len(ligne["loss_by_rep"]) == PETITE_GRILLE["reps"]


def test_ecriture_parquet_relisible(points, tmp_path):
    chemin = sx.write_parquet(points, out_dir=tmp_path)
    assert chemin.exists()
    assert pq.read_table(chemin).num_rows == len(points)


def test_la_matrice_est_orientee_dispersion_x_stockage(points):
    dispersions, totaux, matrice = sx.loss_matrix(points, "baseline")
    assert dispersions == sorted({p.dispersion for p in points})
    assert totaux == sorted({p.storage_total_t for p in points})
    assert len(matrice) == len(totaux)
    assert all(len(ligne) == len(dispersions) for ligne in matrice)
    # la case (stockage bas, dispersion haute) correspond bien au point homonyme
    point = next(
        p for p in points if p.storage_total_t == totaux[0] and p.dispersion == dispersions[-1]
    )
    assert matrice[0][-1] == pytest.approx(point.dri_loss_pct.mean)


def test_la_matrice_refuse_un_scenario_absent(points):
    with pytest.raises(ValueError, match="phase2"):
        sx.loss_matrix(points, "phase2")


def test_la_figure_est_un_png_non_vide(points, tmp_path):
    chemin = sx.write_heatmaps(points, path=tmp_path / "figure.png")
    assert chemin.exists()
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert chemin.stat().st_size > 10_000  # une figure, pas une image vide


def test_la_figure_exige_au_moins_un_point(tmp_path):
    with pytest.raises(ValueError, match="aucun point"):
        sx.write_heatmaps([], path=tmp_path / "vide.png")


# --- ligne de commande -----------------------------------------------------


def test_main_ecrit_la_table_et_la_figure(tmp_path, capsys):
    figure = tmp_path / "sensibilite.png"
    code = sx.main(
        [
            "--scenarios", "baseline",
            "--dispersions", "0", "1",
            "--storage-levels", "2",
            "--reps", "1",
            "--years", "0.05",
            "--out", str(tmp_path),
            "--figure", str(figure),
        ]
    )
    assert code == 0
    assert figure.exists()
    assert len(list(tmp_path.glob("sensibilite_*.parquet"))) == 1
    assert "perte" in capsys.readouterr().out


# --- stock flottant contre perte de production ------------------------------


def test_le_test_de_stock_flottant_detecte_une_relation_negative():
    grille = [
        _case(0.0, 1.0, [12.0, 12.2], stock=0.0),
        _case(0.5, 2.0, [8.0, 8.1], stock=20_000.0),
        _case(1.0, 3.0, [4.0, 4.2], stock=40_000.0),
        _case(1.0, 4.0, [1.0, 1.1], stock=60_000.0),
    ]
    test = sx.floating_stock_test(grille)
    assert test.n == 4
    assert test.pearson_r < -0.9
    assert test.spearman_r == pytest.approx(-1.0)


def test_le_test_isole_l_effet_a_dispersion_fixee():
    grille = [
        _case(0.0, 1.0, [10.0, 10.1], stock=1_000.0),
        _case(0.0, 2.0, [6.0, 6.1], stock=3_000.0),
        _case(1.0, 1.0, [20.0, 20.1], stock=50_000.0),
        _case(1.0, 2.0, [14.0, 14.1], stock=70_000.0),
    ]
    test = sx.floating_stock_test(grille)
    # globalement, la dispersion tire les deux grandeurs vers le haut : corrélation positive
    assert test.pearson_r > 0
    # à dispersion fixée, la relation attendue réapparaît, négative
    assert set(test.within_dispersion) == {0.0, 1.0}
    for effectif, r in test.within_dispersion.values():
        assert effectif == 2
        assert r < 0


def test_le_test_exige_au_moins_deux_points():
    with pytest.raises(ValueError, match="deux points"):
        sx.floating_stock_test([_case(0.0, 1.0, [1.0, 2.0])])


def test_le_stock_flottant_est_dans_la_table(points):
    table = sx.to_table(points)
    assert "waiting_tonnage_mean_t_mean" in table.column_names
    assert "waiting_tonnage_mean_t_ci95_high" in table.column_names
