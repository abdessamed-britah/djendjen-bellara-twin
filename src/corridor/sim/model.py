"""Modèle SimPy du corridor port de Djen Djen → rail → aciérie de Bellara (étape 4).

Cinq processus, une unité de temps (l'heure), une unité de masse (la tonne) :

1. `arrivals`  — engendre les minéraliers selon l'intervalle tiré ;
2. `vessel`    — attente en rade, poste de déchargement, déchargement par tranches ;
3. `rame`      — navette port → usine → port, une par rame, en boucle ;
4. `dri`       — consommation horaire continue, arrêts planifiés, arrêts faute de stock ;
5. `monitor`   — échantillonne les niveaux des deux stocks.

Les deux stocks sont des `simpy.Container` à capacité finie, et c'est ce qui fait
circuler la contre-pression dans tout le corridor : stockyard plein → le déchargement
s'arrête, navire immobilisé à quai ; stock usine plein → la rame attend, chargée. À
l'inverse, stock usine vide → le DRI s'arrête et chaque heure d'arrêt est comptée.

**Conservation de la masse.** À tout instant :

    stock_initial + déchargé = stockyard + stock_usine + en_transit + consommé

Le terme « en transit » n'est pas optionnel : une rame en route détient jusqu'à
`charge_par_train` tonnes qui n'appartiennent plus au port et pas encore à l'usine.

**Mesure des temps d'occupation.** Un navire encore à quai ou une rame encore bloquée
à la fin de l'horizon compte pour le temps déjà passé dans cet état. Les durées sont
donc intégrées par `TimeIntegral`, qui inclut la queue ouverte : accumuler au moment
où l'état se termine perdrait précisément les immobilisations les plus longues, celles
qui n'ont pas fini avant la fin de la simulation.

Les exports sont hors périmètre v1, comme la houle (`seuil_houle_arret` reste inutilisé)
et les temps morts à quai (amarrage, ouverture des cales) : l'occupation du poste est
donc une borne basse.
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass, field
from statistics import fmean

import simpy

from corridor.sim.config import SimConfig
from corridor.sim.samplers import Samplers

#: Tolérance de masse sous laquelle une tranche résiduelle est considérée déchargée.
_EPSILON_T = 1e-9


def percentile(values: list[float], q: float) -> float:
    """Percentile par interpolation linéaire ; 0.0 sur une série vide.

    Écrit à la main plutôt que pris dans `statistics` : il faut un résultat défini
    pour 0 et 1 observation, ce qui arrive dès qu'une réplication ne voit aucune attente.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


@dataclass(slots=True)
class TimeIntegral:
    """Intègre un effectif dans le temps : l'aire sous la courbe du nombre d'occupants.

    `change(now, +1)` à l'entrée dans l'état, `change(now, -1)` à la sortie, `read(now)`
    à tout moment — y compris quand des occupants sont encore là, dont le temps déjà
    écoulé est compté.
    """

    count: int = 0
    area: float = 0.0
    last_change: float = 0.0

    def change(self, now: float, delta: int) -> None:
        self.area += self.count * (now - self.last_change)
        self.count += delta
        self.last_change = now

    def read(self, now: float) -> float:
        return self.area + self.count * (now - self.last_change)


@dataclass(frozen=True, slots=True)
class SimResult:
    """Résultat d'une réplication : une ligne de la table de sortie."""

    scenario: str
    rep: int
    seed: int
    years: float
    horizon_h: float
    dispersion_arrivees: float
    # flux de matière
    initial_stock_t: float
    pellets_delivered_t: float
    pellets_railed_t: float
    pellets_consumed_t: float
    pellets_in_transit_t: float
    # production
    dri_production_t: float
    dri_stop_hours: float
    dri_planned_stop_hours: float
    # navires
    vessels_arrived: int
    vessels_served: int
    wait_mean_h: float
    wait_p90_h: float
    berth_occupancy: float
    # stocks
    stockyard_min_t: float
    stockyard_mean_t: float
    stockyard_final_t: float
    plant_stock_min_t: float
    plant_stock_mean_t: float
    plant_stock_final_t: float
    # rail
    rame_utilisation: float
    rame_blocked_h: float


@dataclass(slots=True)
class _Metrics:
    """Compteurs alimentés par les processus pendant la simulation."""

    pellets_delivered_t: float = 0.0
    pellets_railed_t: float = 0.0
    pellets_consumed_t: float = 0.0
    pellets_in_transit_t: float = 0.0
    dri_production_t: float = 0.0
    dri_stop_hours: float = 0.0
    dri_planned_stop_hours: float = 0.0
    vessels_arrived: int = 0
    vessels_served: int = 0
    berth: TimeIntegral = field(default_factory=TimeIntegral)
    rame_rolling: TimeIntegral = field(default_factory=TimeIntegral)
    rame_blocked: TimeIntegral = field(default_factory=TimeIntegral)
    waits_h: list[float] = field(default_factory=list)
    stockyard_samples: list[float] = field(default_factory=list)
    plant_samples: list[float] = field(default_factory=list)


class _Corridor:
    """Les ressources du corridor et les processus qui les font vivre."""

    def __init__(
        self,
        env: simpy.Environment,
        config: SimConfig,
        samplers: Samplers,
        rng: random.Random,
        years: float,
    ) -> None:
        self.env = env
        self.config = config
        self.samplers = samplers
        self.rng = rng
        self.metrics = _Metrics()
        self.pull = config.politique_expedition == "pull"
        self.stops = config.arrets_planifies_fenetres(years)
        self.berths = simpy.Resource(env, capacity=config.postes_minerai)
        self.stockyard = simpy.Container(
            env, capacity=config.capacite_stockyard_t, init=config.stock_initial_stockyard_t
        )
        self.plant = simpy.Container(
            env, capacity=config.capacite_stock_usine_t, init=config.stock_initial_usine_t
        )
        # Place libre à l'usine, en tonnes. En politique « pull », une rame en réserve
        # une charge complète *avant* d'aller charger : la réservation est atomique, donc
        # deux rames ne peuvent pas se croire admises pour la même place. C'est ce qui
        # garantit qu'aucune rame ne reste immobilisée chargée devant une usine pleine.
        self.plant_space = simpy.Container(
            env,
            capacity=config.capacite_stock_usine_t,
            init=config.capacite_stock_usine_t - config.stock_initial_usine_t,
        )

    # --- processus ---------------------------------------------------------

    def arrivals(self) -> Generator:
        """Engendre les minéraliers : intervalle tiré, puis taille tirée."""
        while True:
            yield self.env.timeout(self.samplers.interval_h.sample(self.rng))
            size_t = self.samplers.vessel_size_t.sample(self.rng)
            self.env.process(self.vessel(size_t))

    def vessel(self, size_t: float) -> Generator:
        """Un navire : attente en rade, puis poste, puis déchargement par tranches.

        Le poste reste occupé pendant toute la durée du déchargement, y compris les
        pauses dues à un stockyard plein : c'est le navire qui bloque le quai, et ces
        heures-là comptent dans le taux d'occupation.
        """
        arrived_at = self.env.now
        self.metrics.vessels_arrived += 1
        with self.berths.request() as berth:
            yield berth
            self.metrics.waits_h.append(self.env.now - arrived_at)
            self.metrics.berth.change(self.env.now, +1)
            rate = self.samplers.discharge_rate_t_par_h.sample(self.rng)
            remaining = size_t
            while remaining > _EPSILON_T:
                step_h = min(self.config.pas_h, remaining / rate)
                yield self.env.timeout(step_h)
                chunk = min(remaining, rate * step_h)
                yield self.stockyard.put(chunk)  # bloque si plein : déchargement en pause
                self.metrics.pellets_delivered_t += chunk
                remaining -= chunk
            self.metrics.berth.change(self.env.now, -1)
            self.metrics.vessels_served += 1

    def rame(self) -> Generator:
        """Une rame : attend d'avoir de quoi charger, fait l'aller-retour, recommence.

        Trois temps distincts, qu'il ne faut pas confondre pour lire un goulot :

        - *roulant* : chargement, trajet, déchargement, retour à vide. C'est l'utilisation
          de la rame, celle qui se compare à la capacité ferroviaire théorique ;
        - *bloqué* : la rame est chargée mais le stock usine est plein, elle attend. Ce
          n'est pas de l'utilisation, c'est un symptôme (typiquement un arrêt du DRI) ;
        - *inactif* : elle attend des pellets au port, ou une place à l'usine en politique
          « pull ». Elle n'est simplement pas employée.

        En « pull », la place à l'usine est réservée avant le chargement, donc le temps
        bloqué est nul par construction. En « push », la rame part dès qu'il y a de quoi
        charger et peut se retrouver immobilisée, chargée, devant une usine pleine.
        """
        charge = self.config.charge_par_train_t
        while True:
            if self.pull:
                yield self.plant_space.get(charge)  # inactif tant que l'usine est pleine
            yield self.stockyard.get(charge)  # inactif tant qu'il n'y a rien à charger
            self.metrics.pellets_in_transit_t += charge
            yield from self._roll(self.samplers.load_h.sample(self.rng))
            yield from self._roll(self.samplers.travel_h.sample(self.rng))
            self.metrics.rame_blocked.change(self.env.now, +1)
            yield self.plant.put(charge)  # bloque si le stock usine est plein
            self.metrics.rame_blocked.change(self.env.now, -1)
            self.metrics.pellets_in_transit_t -= charge
            self.metrics.pellets_railed_t += charge
            yield from self._roll(self.samplers.unload_h.sample(self.rng))
            yield from self._roll(self.samplers.travel_h.sample(self.rng))  # retour à vide

    def _roll(self, duration_h: float) -> Generator:
        """Une étape roulante de la rame, comptée dans son utilisation."""
        self.metrics.rame_rolling.change(self.env.now, +1)
        yield self.env.timeout(duration_h)
        self.metrics.rame_rolling.change(self.env.now, -1)

    def dri(self) -> Generator:
        """Consommation horaire du DRI, arrêts planifiés, arrêts faute de pellets."""
        step_h = self.config.pas_h
        besoin_t = self.config.debit_pellets_t_par_h * step_h
        production_t = self.config.debit_dri_t_par_h * step_h
        while True:
            if self._planned_stop(self.env.now):
                self.metrics.dri_planned_stop_hours += step_h
            elif self.plant.level >= besoin_t:
                yield self.plant.get(besoin_t)
                if self.pull:
                    # la place libérée redevient réservable par une rame
                    yield self.plant_space.put(besoin_t)
                self.metrics.pellets_consumed_t += besoin_t
                self.metrics.dri_production_t += production_t
            else:
                # stock usine épuisé : le four s'arrête, l'heure est comptée
                self.metrics.dri_stop_hours += step_h
            yield self.env.timeout(step_h)

    def monitor(self) -> Generator:
        """Échantillonne les deux niveaux de stock à intervalle fixe."""
        while True:
            self.metrics.stockyard_samples.append(self.stockyard.level)
            self.metrics.plant_samples.append(self.plant.level)
            yield self.env.timeout(self.config.pas_echantillonnage_h)

    def _planned_stop(self, now: float) -> bool:
        return any(start <= now < end for start, end in self.stops)

    # --- restitution ------------------------------------------------------

    def result(self, scenario: str, rep: int, seed: int, years: float) -> SimResult:
        """Agrège les compteurs en une ligne de résultat."""
        m = self.metrics
        config = self.config
        now = self.env.now
        horizon_h = years * config.heures_par_an
        berth_hours = horizon_h * config.postes_minerai
        rame_hours = horizon_h * config.nombre_de_rames
        return SimResult(
            scenario=scenario,
            rep=rep,
            seed=seed,
            years=years,
            horizon_h=horizon_h,
            dispersion_arrivees=config.dispersion_arrivees,
            initial_stock_t=config.stock_initial_stockyard_t + config.stock_initial_usine_t,
            pellets_delivered_t=m.pellets_delivered_t,
            pellets_railed_t=m.pellets_railed_t,
            pellets_consumed_t=m.pellets_consumed_t,
            pellets_in_transit_t=m.pellets_in_transit_t,
            dri_production_t=m.dri_production_t,
            dri_stop_hours=m.dri_stop_hours,
            dri_planned_stop_hours=m.dri_planned_stop_hours,
            vessels_arrived=m.vessels_arrived,
            vessels_served=m.vessels_served,
            wait_mean_h=fmean(m.waits_h) if m.waits_h else 0.0,
            wait_p90_h=percentile(m.waits_h, 0.90),
            berth_occupancy=m.berth.read(now) / berth_hours if berth_hours else 0.0,
            stockyard_min_t=min(m.stockyard_samples) if m.stockyard_samples else 0.0,
            stockyard_mean_t=fmean(m.stockyard_samples) if m.stockyard_samples else 0.0,
            stockyard_final_t=self.stockyard.level,
            plant_stock_min_t=min(m.plant_samples) if m.plant_samples else 0.0,
            plant_stock_mean_t=fmean(m.plant_samples) if m.plant_samples else 0.0,
            plant_stock_final_t=self.plant.level,
            rame_utilisation=m.rame_rolling.read(now) / rame_hours if rame_hours else 0.0,
            rame_blocked_h=m.rame_blocked.read(now),
        )


def simulate(
    config: SimConfig,
    seed: int,
    years: float,
    samplers: Samplers | None = None,
    scenario: str = "",
    rep: int = 0,
) -> SimResult:
    """Simule `years` années de corridor et renvoie les indicateurs de la réplication.

    Le générateur aléatoire est créé ici et nulle part ailleurs : à graine égale, le
    résultat est identique. Passer un jeu de `samplers` permet de substituer des lois
    empiriques sans rien changer au modèle.
    """
    env = simpy.Environment()
    corridor = _Corridor(
        env=env,
        config=config,
        samplers=samplers or Samplers.from_config(config),
        rng=random.Random(seed),
        years=years,
    )
    env.process(corridor.arrivals())
    env.process(corridor.dri())
    env.process(corridor.monitor())
    for _ in range(config.nombre_de_rames):
        env.process(corridor.rame())
    env.run(until=years * config.heures_par_an)
    return corridor.result(scenario=scenario, rep=rep, seed=seed, years=years)
