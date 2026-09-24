"""Tests des réplications, de l'écriture Parquet et de la ligne de commande."""

import dataclasses

import pyarrow.parquet as pq
import pytest

from corridor.sim import run as rn
from corridor.sim.config import SimConfig, load_scenario
from corridor.sim.model import SimResult

# horizon court : ces tests vérifient la plomberie, pas la physique du corridor
ANNEES = 0.05
REPS = 3
GRAINE = 77


@pytest.fixture(scope="module")
def baseline() -> SimConfig:
    return SimConfig.from_scenario(load_scenario("baseline"))


@pytest.fixture(scope="module")
def resultats(baseline) -> list[SimResult]:
    return rn.replicate(baseline, years=ANNEES, reps=REPS, seed=GRAINE, scenario="baseline")


# --- réplications ----------------------------------------------------------


def test_replicate_produit_une_ligne_par_replication(resultats):
    assert len(resultats) == REPS
    assert [r.rep for r in resultats] == list(range(REPS))
    assert {r.scenario for r in resultats} == {"baseline"}


def test_chaque_replication_a_sa_propre_graine(resultats):
    graines = [r.seed for r in resultats]
    assert len(set(graines)) == REPS


def test_replications_reproductibles_depuis_la_graine_maitresse(baseline):
    premier = rn.replicate(baseline, years=ANNEES, reps=REPS, seed=GRAINE, scenario="b")
    second = rn.replicate(baseline, years=ANNEES, reps=REPS, seed=GRAINE, scenario="b")
    assert [dataclasses.asdict(r) for r in premier] == [dataclasses.asdict(r) for r in second]


def test_graine_maitresse_differente_donne_d_autres_replications(baseline):
    premier = rn.replicate(baseline, years=ANNEES, reps=REPS, seed=1, scenario="b")
    second = rn.replicate(baseline, years=ANNEES, reps=REPS, seed=2, scenario="b")
    assert [r.seed for r in premier] != [r.seed for r in second]


# --- table et Parquet ------------------------------------------------------


def test_la_table_expose_tous_les_champs_du_resultat(resultats):
    table = rn.results_to_table(resultats)
    attendus = [f.name for f in dataclasses.fields(SimResult)]
    assert table.column_names == attendus
    assert table.num_rows == REPS


def test_les_sorties_demandees_sont_toutes_presentes(resultats):
    colonnes = set(rn.results_to_table(resultats).column_names)
    demandees = {
        "pellets_delivered_t",      # pellets livrés
        "dri_production_t",         # production DRI
        "dri_stop_hours",           # heures d'arrêt DRI
        "wait_mean_h",              # attente moyenne en rade
        "wait_p90_h",               # attente p90 en rade
        "berth_occupancy",          # taux d'occupation du poste
        "stockyard_min_t",          # niveaux des deux stocks
        "stockyard_mean_t",
        "plant_stock_min_t",
        "plant_stock_mean_t",
        "rame_utilisation",         # utilisation des rames
    }
    assert demandees <= colonnes


def test_ecriture_parquet_relisible(resultats, tmp_path):
    chemin = rn.write_parquet(resultats, out_dir=tmp_path, scenario="baseline")
    assert chemin.exists()
    assert chemin.suffix == ".parquet"
    assert chemin.name.startswith("baseline_")
    relu = pq.read_table(chemin)
    assert relu.num_rows == REPS
    assert relu.column("dri_production_t").to_pylist() == [
        r.dri_production_t for r in resultats
    ]


def test_deux_ecritures_ne_s_ecrasent_pas(resultats, tmp_path):
    premier = rn.write_parquet(resultats, out_dir=tmp_path, scenario="baseline")
    second = rn.write_parquet(resultats, out_dir=tmp_path, scenario="phase2")
    assert premier != second
    assert len(list(tmp_path.glob("*.parquet"))) == 2


# --- synthèse --------------------------------------------------------------


def test_summarize_moyenne_les_replications(resultats):
    synthese = rn.summarize(resultats)
    assert synthese["reps"] == REPS
    assert synthese["dri_production_t"] == pytest.approx(
        sum(r.dri_production_t for r in resultats) / REPS
    )
    # deux lectures du p90 : l'année typique et la pire année
    assert synthese["wait_p90_h"] == pytest.approx(
        sum(r.wait_p90_h for r in resultats) / REPS
    )
    assert synthese["wait_p90_max_h"] == max(r.wait_p90_h for r in resultats)
    assert synthese["wait_p90_max_h"] >= synthese["wait_p90_h"]


def test_summarize_refuse_une_liste_vide():
    with pytest.raises(ValueError, match="aucune réplication"):
        rn.summarize([])


# --- ligne de commande -----------------------------------------------------


def test_main_ecrit_un_parquet_et_rend_zero(tmp_path, capsys):
    code = rn.main(
        [
            "--scenario", "baseline",
            "--years", str(ANNEES),
            "--reps", "2",
            "--seed", str(GRAINE),
            "--out", str(tmp_path),
        ]
    )
    assert code == 0
    fichiers = list(tmp_path.glob("baseline_*.parquet"))
    assert len(fichiers) == 1
    assert pq.read_table(fichiers[0]).num_rows == 2
    sortie = capsys.readouterr().out
    assert "baseline" in sortie
    assert "production DRI" in sortie


def test_main_refuse_un_scenario_inconnu(tmp_path):
    with pytest.raises(FileNotFoundError, match="phase3"):
        rn.main(["--scenario", "phase3", "--years", "0.01", "--reps", "1",
                 "--out", str(tmp_path)])


def test_main_accepte_une_dispersion_imposee(tmp_path):
    code = rn.main(
        [
            "--scenario", "baseline",
            "--years", str(ANNEES),
            "--reps", "1",
            "--seed", str(GRAINE),
            "--out", str(tmp_path),
            "--dispersion", "1.2",
        ]
    )
    assert code == 0
    table = pq.read_table(next(iter(tmp_path.glob("*.parquet"))))
    assert table.column("dispersion_arrivees").to_pylist() == [1.2]
