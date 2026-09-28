"""Tests des statistiques sur réplications."""

import math
import statistics

import pytest

from corridor.sim import stats as st


def test_estimate_moyenne_ecart_type_et_intervalle():
    valeurs = [10.0, 12.0, 14.0, 16.0]
    e = st.estimate(valeurs)
    assert e.mean == pytest.approx(13.0)
    assert e.std == pytest.approx(statistics.stdev(valeurs))  # dénominateur n − 1
    assert e.n == 4
    attendu = 1.96 * e.std / math.sqrt(4)
    assert e.half_width == pytest.approx(attendu)
    assert (e.low, e.high) == pytest.approx((13.0 - attendu, 13.0 + attendu))


def test_une_serie_constante_a_un_intervalle_nul():
    e = st.estimate([5.0, 5.0, 5.0])
    assert e.std == 0.0
    assert e.half_width == 0.0
    assert e.contains(5.0)


def test_une_seule_replication_n_a_pas_d_intervalle():
    e = st.estimate([3.0])
    assert not e.defined
    assert math.isnan(e.half_width)
    assert not e.contains(3.0)  # on ne prétend rien sans dispersion mesurée


def test_aucune_replication_est_refusee():
    with pytest.raises(ValueError, match="aucune réplication"):
        st.estimate([])


def test_chevauchement_des_intervalles():
    a = st.Estimate(mean=10.0, std=2.0, n=4)  # IC [8,04 ; 11,96]
    b = st.Estimate(mean=11.0, std=2.0, n=4)
    c = st.Estimate(mean=20.0, std=2.0, n=4)
    assert a.overlaps(b) and b.overlaps(a)
    assert not a.overlaps(c)


def test_difference_appariee():
    a = [10.0, 11.0, 12.0, 13.0]
    b = [9.0, 10.2, 10.8, 12.0]
    d = st.paired_difference(a, b)
    assert d.mean == pytest.approx(statistics.fmean(x - y for x, y in zip(a, b, strict=True)))
    assert st.significant(d)


def test_l_appariement_detecte_ce_que_le_chevauchement_manque():
    """Le cas qui justifie l'appariement : forte variance commune, petit écart constant."""
    base = [100.0, 140.0, 80.0, 120.0, 90.0, 130.0]
    a, b = base, [x - 2.0 for x in base]
    assert st.estimate(a).overlaps(st.estimate(b))  # les intervalles se chevauchent
    assert st.significant(st.paired_difference(a, b))  # mais la différence est certaine


def test_difference_nulle_non_significative():
    a = [10.0, 12.0, 9.0, 11.0]
    b = [11.0, 11.0, 10.0, 10.0]
    assert not st.significant(st.paired_difference(a, b))


def test_series_de_longueurs_differentes_refusees():
    with pytest.raises(ValueError, match="non appariables"):
        st.paired_difference([1.0, 2.0], [1.0])


def test_pas_de_conclusion_sans_dispersion():
    assert not st.significant(st.paired_difference([5.0], [1.0]))


# --- corrélations ----------------------------------------------------------


def test_pearson_relation_parfaite():
    assert st.pearson([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]) == pytest.approx(1.0)
    assert st.pearson([1.0, 2.0, 3.0, 4.0], [8.0, 6.0, 4.0, 2.0]) == pytest.approx(-1.0)


def test_pearson_sans_relation():
    assert abs(st.pearson([1.0, 2.0, 3.0, 4.0], [3.0, 1.0, 4.0, 2.0])) < 0.6


def test_pearson_serie_constante_est_indefinie():
    assert math.isnan(st.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))


def test_pearson_series_incompatibles():
    with pytest.raises(ValueError, match="non appariables"):
        st.pearson([1.0], [1.0, 2.0])


def test_spearman_capte_une_relation_monotone_non_lineaire():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 4.0, 9.0, 16.0, 25.0]  # croissante mais courbe
    assert st.spearman(xs, ys) == pytest.approx(1.0)
    assert st.pearson(xs, ys) < 1.0


def test_spearman_gere_les_ex_aequo():
    assert st.spearman([1.0, 2.0, 2.0, 3.0], [1.0, 2.0, 2.0, 3.0]) == pytest.approx(1.0)
