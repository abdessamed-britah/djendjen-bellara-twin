"""Statistiques sur les réplications : moyenne, écart-type, intervalle de confiance à 95 %.

Chaque indicateur agrégé sur n réplications est rapporté comme une `Estimate` :

    moyenne ± 1,96 × écart-type / √n

avec l'écart-type d'échantillon (dénominateur n − 1). Le facteur 1,96 est celui de la loi
normale ; à n = 30, la loi de Student donnerait 2,05, soit un intervalle ~4 % plus large.
L'approximation est retenue telle que demandée, et elle est à garder en tête pour les
petites valeurs de n.

**Comparer deux configurations.** Toutes les configurations d'une même étude sont rejouées
avec la même graine maîtresse : la réplication i de A et celle de B partagent leur graine.
Les échantillons sont donc **appariés**, et la bonne question n'est pas « les deux
intervalles se chevauchent-ils ? » mais « l'intervalle de la différence réplication par
réplication contient-il zéro ? » (`paired_difference`). Le chevauchement des intervalles
individuels est un critère plus conservateur : deux intervalles peuvent se chevaucher
alors que la différence appariée est nettement non nulle.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, stdev

#: Quantile à 97,5 % de la loi normale : intervalle bilatéral à 95 %.
Z_95 = 1.96


@dataclass(frozen=True, slots=True)
class Estimate:
    """Moyenne d'un indicateur sur n réplications, avec sa dispersion et son intervalle."""

    mean: float
    std: float  # écart-type d'échantillon ; nan si n < 2
    n: int

    @property
    def defined(self) -> bool:
        """Faux si n < 2 : sans dispersion mesurée, aucun intervalle n'a de sens."""
        return self.n >= 2 and not math.isnan(self.std)

    @property
    def half_width(self) -> float:
        return Z_95 * self.std / math.sqrt(self.n) if self.defined else math.nan

    @property
    def low(self) -> float:
        return self.mean - self.half_width

    @property
    def high(self) -> float:
        return self.mean + self.half_width

    def contains(self, value: float) -> bool:
        return self.defined and self.low <= value <= self.high

    def overlaps(self, other: Estimate) -> bool:
        """Les deux intervalles ont-ils une partie commune ? Faux si l'un n'est pas défini."""
        return self.defined and other.defined and self.low <= other.high and other.low <= self.high


def estimate(values: Sequence[float]) -> Estimate:
    """Moyenne, écart-type d'échantillon et effectif d'une série de réplications."""
    values = list(values)
    if not values:
        raise ValueError("aucune réplication à agréger")
    return Estimate(
        mean=fmean(values),
        std=stdev(values) if len(values) > 1 else math.nan,
        n=len(values),
    )


def paired_difference(a: Sequence[float], b: Sequence[float]) -> Estimate:
    """Estimation de la différence moyenne A − B, réplication par réplication."""
    if len(a) != len(b):
        raise ValueError(f"séries non appariables : {len(a)} contre {len(b)} réplications")
    return estimate([x - y for x, y in zip(a, b, strict=True)])


def significant(difference: Estimate) -> bool:
    """La différence sort-elle de son intervalle, c'est-à-dire l'IC 95 % exclut-il zéro ?

    Faux quand l'intervalle n'est pas défini : on ne conclut pas sans dispersion mesurée.
    """
    return difference.defined and not difference.contains(0.0)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Corrélation linéaire de Pearson ; nan si une série est constante."""
    if len(xs) != len(ys):
        raise ValueError(f"séries non appariables : {len(xs)} contre {len(ys)}")
    if len(xs) < 2:
        return math.nan
    mx, my = fmean(xs), fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy) if vx and vy else math.nan


def _ranks(values: Sequence[float]) -> list[float]:
    """Rangs moyens, ex æquo partagés : base de la corrélation de Spearman."""
    ordre = sorted(range(len(values)), key=lambda i: values[i])
    rangs = [0.0] * len(values)
    i = 0
    while i < len(ordre):
        j = i
        while j + 1 < len(ordre) and values[ordre[j + 1]] == values[ordre[i]]:
            j += 1
        moyen = (i + j) / 2 + 1
        for k in range(i, j + 1):
            rangs[ordre[k]] = moyen
        i = j + 1
    return rangs


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Corrélation de rang : capte une relation monotone, même non linéaire."""
    return pearson(_ranks(xs), _ranks(ys))
