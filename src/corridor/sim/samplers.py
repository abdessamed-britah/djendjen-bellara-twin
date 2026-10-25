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

#: Les sources d'aléa du modèle, une par flux. Chacune a son propre générateur, dérivé de la
#: graine de réplication, pour que deux configurations qui ne diffèrent que par une capacité
#: voient **les mêmes tirages dans chaque flux** : le navire n a la même taille et la même
#: cadence, la rame k fait son j-ième cycle avec les mêmes durées. Sans cette séparation, un
#: seul générateur partagé fait glisser toute la suite dès qu'une configuration tire une
#: valeur de plus que l'autre, et les nombres aléatoires communs ne communiquent plus rien.
#:
#: Les arrêts planifiés n'y figurent pas : ils sont déterministes (`arrets_planifies_fenetres`
#: les place à intervalles réguliers), donc déjà synchronisés sans rien consommer.
#:
#: `delai_approvisionnement` ne sert qu'en politique de point de commande : la n-ième
#: commande y tire le n-ième délai, quel que soit le moment où elle part.
SOURCES = (
    "intervalle", "taille_navire", "cadence_dechargement", "rail", "delai_approvisionnement",
)


@dataclass(frozen=True, slots=True)
class Streams:
    """Un générateur par source d'aléa, tous dérivés de la seule graine de réplication.

    `index` sépare les entités d'une même source qui tirent en parallèle — une rame par
    index, par exemple. Deux rames partageant un générateur se voleraient leurs tirages
    dans un ordre qui dépend du trafic, donc de la configuration.

    La dérivation passe par une graine textuelle (`"<graine>/<source>/<index>"`), que
    `random.Random` hache en SHA-512 : deux flux ne peuvent pas se retrouver sur la même
    séquence, et la graine de réplication reste le seul paramètre à fixer.
    """

    seed: int

    def stream(self, source: str, index: int = 0) -> random.Random:
        """Le générateur d'une source, éventuellement d'une entité de cette source."""
        if source not in SOURCES:
            raise ValueError(f"source d'aléa inconnue : {source!r} (connues : {SOURCES})")
        return random.Random(f"{self.seed}/{source}/{index}")


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
    #: Délai d'approvisionnement, de la commande à l'arrivée en rade. Inutilisé en
    #: politique exogène ; facultatif pour que les jeux construits à la main restent valides.
    lead_time_h: Sampler | None = None

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
            lead_time_h=from_mean_cv(
                config.delai_approvisionnement_h, config.cv_delai_approvisionnement
            ),
        )
