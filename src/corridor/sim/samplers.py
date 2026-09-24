"""Tirages aléatoires du modèle : une interface, plusieurs lois (étape 4).

Chaque aléa du corridor — intervalle entre arrivées, taille du navire, cadence de
déchargement, durées ferroviaires — passe par la même interface :

    sample(rng: random.Random) -> float

Le modèle ne connaît que cette méthode. Remplacer la version paramétrique par une
version empirique tirée des escales réelles (`corridor.transform.build_escales`) ne
demandera donc aucune modification du modèle : il suffira de construire un autre jeu
de `Samplers`, par exemple

    dataclasses.replace(jeu, vessel_size_t=TirageEmpirique(tonnages_observes))

En v1, seules les lois paramétriques sont fournies, comme prévu.

**Dispersion.** Toutes les lois non constantes sont des gammas paramétrées par
(moyenne, coefficient de variation). Ce choix donne un seul bouton de réglage continu :
cv = 0 → régulier, cv = 1 → processus de Poisson (la gamma dégénère en exponentielle),
cv > 1 → arrivées groupées. C'est ce bouton que `simulation.dispersion_arrivees`
actionne pour comparer un affrètement discipliné à un affrètement erratique.

Le générateur est passé en argument à chaque tirage, jamais stocké : la reproductibilité
dépend de la seule graine de la réplication.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from corridor.sim.config import SimConfig


@runtime_checkable
class Sampler(Protocol):
    """Tout ce que le modèle exige d'un aléa : savoir se tirer."""

    def sample(self, rng: random.Random) -> float:
        """Renvoie un tirage, en heures ou en tonnes selon la grandeur."""
        ...


@dataclass(frozen=True, slots=True)
class ConstantSampler:
    """Grandeur déterministe : cadence fixe, arrivées parfaitement régulières."""

    value: float

    def sample(self, rng: random.Random) -> float:
        return self.value


@dataclass(frozen=True, slots=True)
class GammaSampler:
    """Loi gamma de moyenne et de coefficient de variation donnés, toujours positive.

    Paramétrage : `alpha = 1/cv²` (forme) et `beta = mean × cv²` (échelle). À cv = 1,
    alpha vaut 1 et la loi est exponentielle de moyenne `mean`.
    """

    mean: float
    cv: float

    def sample(self, rng: random.Random) -> float:
        shape = 1.0 / (self.cv * self.cv)
        return rng.gammavariate(shape, self.mean / shape)


def from_mean_cv(mean: float, cv: float) -> Sampler:
    """Choisit la loi : constante si la dispersion est nulle, gamma sinon."""
    if mean <= 0:
        raise ValueError(f"moyenne attendue strictement positive, reçu {mean}")
    if cv <= 0:
        return ConstantSampler(mean)
    return GammaSampler(mean, cv)


def interval_sampler(mean_h: float, dispersion: float) -> Sampler:
    """Intervalle entre deux arrivées de minéraliers.

    `dispersion` est le coefficient de variation des intervalles : 0 pour des arrivées
    régulières, 1 pour un processus de Poisson, au-delà pour des arrivées groupées.
    """
    return from_mean_cv(mean_h, dispersion)


@dataclass(frozen=True, slots=True)
class Samplers:
    """Le jeu complet des aléas passés au modèle : le seul point à remplacer."""

    interval_h: Sampler
    vessel_size_t: Sampler
    discharge_rate_t_par_h: Sampler
    load_h: Sampler
    travel_h: Sampler
    unload_h: Sampler

    @classmethod
    def from_config(cls, config: SimConfig) -> Samplers:
        """Version paramétrique : chaque loi tient sa moyenne et sa dispersion des hypothèses."""
        return cls(
            interval_h=interval_sampler(
                config.intervalle_moyen_arrivees_h, config.dispersion_arrivees
            ),
            vessel_size_t=from_mean_cv(config.taille_navire_moyenne_t, config.cv_taille_navire),
            discharge_rate_t_par_h=from_mean_cv(
                config.cadence_dechargement_t_par_h, config.cv_cadence
            ),
            load_h=from_mean_cv(config.duree_chargement_h, config.cv_durees_rail),
            travel_h=from_mean_cv(config.duree_trajet_h, config.cv_durees_rail),
            unload_h=from_mean_cv(config.duree_dechargement_h, config.cv_durees_rail),
        )
