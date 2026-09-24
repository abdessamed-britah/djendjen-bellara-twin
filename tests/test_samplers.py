"""Tests des tirages aléatoires.

On vérifie deux choses : que les lois ont bien la moyenne et la dispersion demandées,
et que l'interface `Sampler` est réellement substituable — c'est elle qui permettra de
remplacer la version paramétrique par une version empirique alimentée par
`build_escales` sans toucher au modèle.
"""

import dataclasses
import random
import statistics

import pytest

from corridor.sim import samplers as sp
from corridor.sim.config import SimConfig, load_scenario

N = 4000


def _draws(sampler, seed: int = 1, n: int = N) -> list[float]:
    rng = random.Random(seed)
    return [sampler.sample(rng) for _ in range(n)]


@pytest.fixture(scope="module")
def baseline():
    return SimConfig.from_scenario(load_scenario("baseline"))


# --- lois paramétriques ----------------------------------------------------


def test_constant_sampler_ne_varie_pas():
    tirages = _draws(sp.ConstantSampler(12.5))
    assert set(tirages) == {12.5}


def test_gamma_sampler_respecte_moyenne_et_dispersion():
    tirages = _draws(sp.GammaSampler(mean=100.0, cv=0.25))
    assert statistics.fmean(tirages) == pytest.approx(100.0, rel=0.05)
    cv = statistics.stdev(tirages) / statistics.fmean(tirages)
    assert cv == pytest.approx(0.25, rel=0.10)
    assert all(t > 0 for t in tirages)  # une durée ou une masse reste positive


def test_gamma_sampler_a_cv_1_est_exponentiel():
    tirages = _draws(sp.GammaSampler(mean=50.0, cv=1.0))
    cv = statistics.stdev(tirages) / statistics.fmean(tirages)
    assert cv == pytest.approx(1.0, rel=0.10)


@pytest.mark.parametrize("cv", [0.0, -1.0])
def test_from_mean_cv_sans_dispersion_donne_une_constante(cv):
    sampler = sp.from_mean_cv(10.0, cv)
    assert isinstance(sampler, sp.ConstantSampler)
    assert sampler.sample(random.Random(0)) == 10.0


def test_from_mean_cv_refuse_une_moyenne_negative():
    with pytest.raises(ValueError, match="moyenne"):
        sp.from_mean_cv(-1.0, 0.2)


# --- le paramètre de dispersion des arrivées -------------------------------


def test_arrivees_regulieres_quand_la_dispersion_est_nulle():
    sampler = sp.interval_sampler(mean_h=429.0, dispersion=0.0)
    assert set(_draws(sampler)) == {429.0}


def test_arrivees_poisson_quand_la_dispersion_vaut_un():
    tirages = _draws(sp.interval_sampler(mean_h=429.0, dispersion=1.0))
    assert statistics.fmean(tirages) == pytest.approx(429.0, rel=0.05)
    cv = statistics.stdev(tirages) / statistics.fmean(tirages)
    assert cv == pytest.approx(1.0, rel=0.10)


def test_la_dispersion_ordonne_bien_l_irregularite():
    # à moyenne égale, plus la dispersion est grande, plus les intervalles varient
    ecarts = [
        statistics.stdev(_draws(sp.interval_sampler(429.0, d)))
        for d in (0.25, 0.75, 1.5)
    ]
    assert ecarts == sorted(ecarts)


# --- reproductibilité et substituabilité -----------------------------------


def test_meme_graine_memes_tirages():
    sampler = sp.GammaSampler(mean=100.0, cv=0.3)
    assert _draws(sampler, seed=7, n=50) == _draws(sampler, seed=7, n=50)
    assert _draws(sampler, seed=7, n=50) != _draws(sampler, seed=8, n=50)


def test_le_jeu_complet_se_construit_depuis_la_config(baseline):
    jeu = sp.Samplers.from_config(baseline)
    rng = random.Random(0)
    assert jeu.interval_h.sample(rng) == pytest.approx(baseline.intervalle_moyen_arrivees_h)
    assert jeu.vessel_size_t.sample(rng) == pytest.approx(
        baseline.taille_navire_moyenne_t, rel=0.2
    )
    assert jeu.discharge_rate_t_par_h.sample(rng) == pytest.approx(
        baseline.cadence_dechargement_t_par_h, rel=0.6
    )
    for sampler in (jeu.load_h, jeu.travel_h, jeu.unload_h):
        assert sampler.sample(rng) > 0


def test_un_sampler_maison_respecte_l_interface(baseline):
    """Une implémentation extérieure (ici un tirage empirique) est substituable."""

    class TirageEmpirique:
        """Ce que sera la version empirique : un tirage avec remise dans des observations."""

        def __init__(self, observations):
            self.observations = list(observations)

        def sample(self, rng: random.Random) -> float:
            return rng.choice(self.observations)

    empirique = TirageEmpirique([140571.0, 142533.0, 146414.0])
    assert isinstance(empirique, sp.Sampler)  # Protocol vérifiable à l'exécution
    jeu = sp.Samplers.from_config(baseline)
    remplace = dataclasses.replace(jeu, vessel_size_t=empirique)
    assert remplace.vessel_size_t.sample(random.Random(0)) in empirique.observations
    # les autres tirages ne sont pas touchés
    assert remplace.interval_h is jeu.interval_h
