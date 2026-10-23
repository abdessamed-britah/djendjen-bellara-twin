"""Tests de scripts/h2_blocking.py : rapports, écarts en log, verdict, figure. Hors ligne."""

import importlib.util
import math
from pathlib import Path

import pytest

from corridor.sim.sensitivity import GridPoint
from corridor.sim.stats import estimate

_SPEC = importlib.util.spec_from_file_location(
    "h2_blocking", Path(__file__).parent.parent / "scripts" / "h2_blocking.py"
)
h2 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h2)


def _case(dispersion, storage_kt, attente, bloque, servis, scenario="s"):
    """Point de grille fabriqué : seules comptent les grandeurs que H2 consomme."""
    zero = estimate([0.0, 0.0])
    return GridPoint(
        scenario=scenario, dispersion=dispersion, storage_total_t=storage_kt * 1e3,
        stockyard_t=0.0, plant_stock_t=0.0, reps=2, years=1.0, dri_target_t=1.0,
        dri_production_t=zero, dri_loss_pct=zero, dri_stop_hours=zero,
        wait_mean_h=estimate([attente, attente]), wait_p90_h=zero,
        berth_occupancy=zero, berth_blocked_h=estimate([bloque, bloque]),
        vessels_served=estimate([servis, servis]), rame_utilisation=zero,
        waiting_tonnage_mean_t=zero, loss_by_rep=(0.0, 0.0),
    )


def _service(lam=0.002, es=140.0, cs2=0.02):
    return {"lam": lam, "es": es, "cs2": cs2, "rho": lam * es, "es2": 0.0, "config": None}


# --- rapports et écarts -----------------------------------------------------


def test_rapport_simule_sur_predit():
    assert h2._ratio(100.0, 50.0) == pytest.approx(2.0)


def test_rapport_indefini_sans_prediction():
    assert h2._ratio(100.0, None) is None
    assert h2._ratio(100.0, 0.0) is None
    assert h2._ratio(100.0, -3.0) is None


def test_ecart_log_est_nul_pour_un_accord_parfait():
    assert h2.ecart_log(1.0) == pytest.approx(0.0)


def test_ecart_log_traite_symetriquement_les_deux_sens():
    """Diviser par 2 et multiplier par 2 sont deux erreurs de même taille."""
    assert h2.ecart_log(2.0) == pytest.approx(h2.ecart_log(0.5))


def test_ecart_log_indefini_sur_un_rapport_absent_ou_nul():
    assert h2.ecart_log(None) is None
    assert h2.ecart_log(0.0) is None
    assert h2.ecart_log(-1.0) is None
    assert h2.ecart_log(math.inf) is None


def test_kingman_ou_none_refuse_une_file_saturee():
    assert h2._kingman_or_none(1.0, 1.0, 1.0, 10.0) is None
    assert h2._kingman_or_none(1.4, 1.0, 1.0, 10.0) is None
    assert h2._kingman_or_none(0.5, 1.0, 1.0, 10.0) > 0.0


# --- construction des lignes ------------------------------------------------


def test_le_blocage_allonge_le_service_et_l_utilisation():
    """E[S_eff] = E[S] + B/N, et ρ_eff suit : c'est tout le mécanisme testé par H2."""
    point = _case(dispersion=1.0, storage_kt=200, attente=300.0, bloque=1400.0, servis=20.0)
    ligne = h2.h2_rows([point], {"s": _service(lam=0.002, es=140.0)})[0]
    assert ligne["rho"] == pytest.approx(0.28)
    assert ligne["rho_eff"] == pytest.approx(0.002 * (140.0 + 70.0))
    assert ligne["rho_eff"] > ligne["rho"]


def test_sans_blocage_rho_eff_vaut_rho():
    point = _case(dispersion=1.0, storage_kt=900, attente=50.0, bloque=0.0, servis=20.0)
    ligne = h2.h2_rows([point], {"s": _service()})[0]
    assert ligne["rho_eff"] == pytest.approx(ligne["rho"])
    assert ligne["pred_eff_h"] == pytest.approx(ligne["pred_rho_h"])
    assert ligne["ratio_eff"] == pytest.approx(ligne["ratio_rho"])


def test_ca2_est_le_carre_de_la_dispersion():
    """Dans le modèle, la dispersion **est** le coefficient de variation des intervalles."""
    lignes = h2.h2_rows(
        [_case(0.5, 500, 50.0, 0.0, 20.0), _case(1.0, 500, 50.0, 0.0, 20.0)],
        {"s": _service()},
    )
    # à ρ identique, Kingman est proportionnel à (c_a² + c_s²) : passer de 0,25 à 1 l'augmente
    assert lignes[1]["pred_rho_h"] > lignes[0]["pred_rho_h"]


def test_une_case_saturee_n_a_pas_de_prediction_effective():
    """B/N assez grand pour que ρ_eff dépasse 1 : aucune attente stationnaire n'existe."""
    point = _case(dispersion=1.0, storage_kt=200, attente=900.0, bloque=8000.0, servis=10.0)
    ligne = h2.h2_rows([point], {"s": _service(lam=0.002, es=140.0)})[0]
    assert ligne["rho_eff"] >= 1.0
    assert ligne["pred_eff_h"] is None
    assert ligne["ratio_eff"] is None


# --- verdict ----------------------------------------------------------------


def test_le_verdict_ecarte_les_cases_sans_attente():
    """Dispersion nulle ou attente nulle : le rapport simulé/prédit n'a aucun sens."""
    lignes = h2.h2_rows(
        [_case(0.0, 200, 0.0, 0.0, 20.0),
         _case(1.0, 200, 300.0, 1400.0, 20.0),
         _case(1.0, 900, 50.0, 0.0, 20.0)],
        {"s": _service()},
    )
    assert h2.verdict(lignes)["n_cases"] == 2


def test_le_verdict_compare_les_stockages_extremes():
    lignes = h2.h2_rows(
        [_case(1.0, 200, 400.0, 2000.0, 20.0), _case(1.0, 900, 60.0, 0.0, 20.0)],
        {"s": _service()},
    )
    v = h2.verdict(lignes)
    assert v["stockage_bas_kt"] == 200.0
    assert v["stockage_haut_kt"] == 900.0
    # sans blocage à 900 kt, la correction ne change rien ; à 200 kt elle change tout
    assert v["ecart_eff_haut"] == pytest.approx(v["ecart_rho_haut"])
    assert v["ecart_eff_bas"] != pytest.approx(v["ecart_rho_bas"])


def test_le_verdict_compte_les_cases_saturees():
    lignes = h2.h2_rows(
        [_case(1.0, 200, 900.0, 8000.0, 10.0), _case(1.0, 900, 60.0, 0.0, 20.0)],
        {"s": _service(lam=0.002, es=140.0)},
    )
    assert h2.verdict(lignes)["satures"] == 1


# --- sorties ----------------------------------------------------------------


def test_les_tableaux_s_affichent_sans_lever(capsys):
    lignes = h2.h2_rows(
        [_case(1.0, 200, 400.0, 2000.0, 20.0), _case(0.5, 900, 20.0, 0.0, 20.0)],
        {"s": _service()},
    )
    h2.print_table(lignes)
    h2.print_markdown(lignes)
    h2.print_verdict(h2.verdict(lignes))
    sortie = capsys.readouterr().out
    assert "Verdict sur H2" in sortie
    assert "cohérence interne" in sortie


def test_la_figure_est_un_png_non_vide(tmp_path):
    lignes = h2.h2_rows(
        [_case(d, kt, 100.0 * d, 500.0, 20.0)
         for d in (0.0, 0.5, 1.0) for kt in (200, 550, 900)],
        {"s": _service()},
    )
    chemin = h2.write_figure(lignes, tmp_path / "h2.png")
    assert chemin.exists()
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert chemin.stat().st_size > 10_000


def test_la_figure_tolere_une_case_sans_prediction(tmp_path):
    """Une case saturée n'a pas de rapport : elle doit être sautée, pas faire tomber le tracé."""
    lignes = h2.h2_rows(
        [_case(1.0, 200, 900.0, 8000.0, 10.0), _case(1.0, 900, 60.0, 100.0, 20.0)],
        {"s": _service(lam=0.002, es=140.0)},
    )
    assert any(x["ratio_eff"] is None for x in lignes)
    assert h2.write_figure(lignes, tmp_path / "h2.png").exists()


def test_service_d_un_scenario_est_coherent():
    service = h2.scenario_service("baseline", draws=5_000)
    assert service["lam"] > 0.0
    assert service["es"] > 0.0
    assert 0.0 < service["rho"] < 1.0
    assert service["cs2"] > 0.0
    assert service["rho"] == pytest.approx(service["lam"] * service["es"])
