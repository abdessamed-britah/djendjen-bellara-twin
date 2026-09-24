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

ASSUMPTIONS = load_yaml("config/assumptions.yaml")
PETITE_GRILLE = {
    "scenarios": ("baseline",),
    "dispersions": (0.0, 1.0),
    "storage_levels": 2,
    "reps": 1,
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
        attendu = (point.dri_target_t - point.dri_production_mean_t) / point.dri_target_t * 100
        assert point.dri_loss_pct == pytest.approx(attendu)


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
    assert irregulier.dri_loss_pct > regulier.dri_loss_pct


# --- sorties ---------------------------------------------------------------


def test_la_table_expose_tous_les_champs(points):
    table = sx.to_table(points)
    assert table.column_names == [f.name for f in dataclasses.fields(sx.GridPoint)]
    assert table.num_rows == len(points)


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
    assert matrice[0][-1] == pytest.approx(point.dri_loss_pct)


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
