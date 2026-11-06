"""Tests de scripts/ordre_accostage.py : paires concurrentes, dépassements, intervalle."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ordre_accostage", Path(__file__).parent.parent / "scripts" / "ordre_accostage.py"
)
oa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oa)

T0 = datetime(2026, 10, 1, tzinfo=UTC)
FIN = T0 + timedelta(days=30)


def _a(nom, rade_j, quai_j, poste="QW/7", cargo="iron_ore"):
    return oa.Attente(nom, cargo, T0 + timedelta(days=rade_j),
                      None if quai_j is None else T0 + timedelta(days=quai_j), poste)


def test_deux_navires_servis_dans_l_ordre_ne_sont_pas_un_depassement():
    (p,) = oa.paires_concurrentes([_a("A", 0, 3), _a("B", 1, 4)], FIN)
    assert p["depasse"] is False
    assert p["meme_poste"] is True


def test_le_second_accoste_avant_le_premier():
    (p,) = oa.paires_concurrentes([_a("A", 0, 5), _a("B", 1, 2, poste="QW/1")], FIN)
    assert p["depasse"] is True
    assert p["meme_poste"] is False


def test_des_attentes_qui_ne_se_chevauchent_pas_ne_forment_pas_de_paire():
    assert oa.paires_concurrentes([_a("A", 0, 1), _a("B", 2, 3)], FIN) == []


def test_un_navire_encore_en_rade_depasse_par_un_arrive_apres_lui():
    (p,) = oa.paires_concurrentes([_a("A", 0, None), _a("B", 1, 2)], FIN)
    assert p["depasse"] is True
    assert p["meme_poste"] is None


def test_deux_navires_encore_en_rade_ne_disent_rien():
    assert oa.paires_concurrentes([_a("A", 0, None), _a("B", 1, None)], FIN) == []


def test_l_intervalle_de_wilson():
    bas, haut = oa.wilson(6, 8)
    assert bas == pytest.approx(0.409, abs=0.005)
    assert haut == pytest.approx(0.929, abs=0.005)
    assert oa.wilson(0, 10)[0] == pytest.approx(0.0, abs=1e-12)


def test_les_inversions_en_mer_comparent_annonce_et_arrivee():
    jour = timedelta(days=1)
    couples = [("A", T0, T0 + 3 * jour), ("B", T0 + jour, T0 + 2 * jour),
               ("C", T0 + 2 * jour, T0 + 5 * jour)]
    r = oa.inversions_en_mer(couples)
    assert (r["n"], r["depassements"]) == (3, 1)
