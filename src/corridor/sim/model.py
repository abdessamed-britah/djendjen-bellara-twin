"""Modèle SimPy du corridor port de Djen Djen → rail → aciérie de Bellara (étape 4).

Cinq processus, une unité de temps (l'heure), une unité de masse (la tonne) :

1. l'origine des navires, au choix (`simulation.politique_approvisionnement`) :
   - `arrivals` — **exogène** : un minéralier arrive après chaque intervalle tiré ;
   - `approvisionnement` — **point de commande** : un minéralier est affrété dès que la
     position de stock passe sous le point de commande, puis `navire_en_mer` le fait
     arriver en rade après un délai tiré ;
2. `vessel`    — attente en rade, poste de déchargement, déchargement par tranches ;
3. `rame`      — navette port → usine → port, une par rame, en boucle ;
4. `dri`       — consommation horaire continue, arrêts planifiés, arrêts faute de stock ;
5. `monitor`   — échantillonne les niveaux des deux stocks et la position de stock.

**Pourquoi deux origines.** Le modèle exogène cale le débit moyen d'arrivée exactement sur
la demande et n'a aucune rétroaction : l'écart offre − demande y est une marche aléatoire
sans dérive, et le taux de service n'y a pas de régime stationnaire
(`scripts/convergence_horizon.py`). Il est gardé parce que la validation contre
Pollaczek-Khinchine exige des arrivées de Poisson exogènes. Le point de commande rend au
système une dérive de rappel : on commande quand le stock baisse, pas à date fixe.

Les deux stocks sont des `simpy.Container` à capacité finie, et c'est ce qui fait
circuler la contre-pression dans tout le corridor : stockyard plein → le déchargement
s'arrête, navire immobilisé à quai ; stock usine plein → la rame attend, chargée. À
l'inverse, stock usine vide → le DRI s'arrête et chaque heure d'arrêt est comptée.

**Conservation de la masse, en deux bilans séparés.** À tout instant :

    aval  :  stock_initial + déchargé = stockyard + stock_usine + en_transit + consommé
    amont :  engagé = déchargé + à_flot

Le terme « en transit » n'est pas optionnel : une rame en route détient jusqu'à
`charge_par_train` tonnes qui n'appartiennent plus au port et pas encore à l'usine.
« À flot » est tout ce qui a été engagé sans être encore déchargé : en mer, en rade, et la
cargaison encore à bord d'un navire à quai. Les deux bilans restent séparés pour qu'une
fuite de masse dise *de quel côté du quai* elle se produit. En politique exogène, un navire
est « engagé » à son arrivée en rade, et le bilan amont vaut quand même.

**Mesure des temps d'occupation.** Un navire encore à quai ou une rame encore bloquée
à la fin de l'horizon compte pour le temps déjà passé dans cet état. Les durées sont
donc intégrées par `TimeIntegral`, qui inclut la queue ouverte : accumuler au moment
où l'état se termine perdrait précisément les immobilisations les plus longues, celles
qui n'ont pas fini avant la fin de la simulation.

**Stock flottant.** `waiting_tonnage_mean_t` intègre de la même façon le tonnage présent
en rade — chaque navire y pèse sa cargaison, de son arrivée à son accostage — puis le
ramène à une moyenne sur l'horizon. C'est du minerai déjà là, payé et à quelques heures
du quai : un tampon que le corridor peut rappeler, et non une simple file d'attente.

**Blocage du poste.** `berth_blocked_h` compte les heures pendant lesquelles un navire
occupe le quai sans décharger, parce que le stockyard est plein. Ces heures sont comprises
dans `berth_occupancy` — c'est bien le quai qui est pris — mais ce n'est pas du service :
c'est la contre-pression de l'aval qui remonte jusqu'au port. Le temps de service que la
file voit vaut donc E[S] + B/N et non E[S] (voir
`corridor.sim.analytic.effective_service`). À stockage infini l'indicateur est nul par
construction, et c'est exactement la condition d'application de Pollaczek-Khinchine.
Comme les autres temps d'occupation, il est intégré : un navire encore bloqué à la fin de
l'horizon compte pour le temps déjà passé à l'être. L'unité est le **poste-heure**, sommée sur
les postes : avec deux postes, deux navires peuvent être bloqués en même temps et l'indicateur
avance alors de deux heures par heure écoulée. Comparer deux configurations qui n'ont pas le
même nombre de postes demande donc de diviser par `postes_minerai`.

Les exports sont hors périmètre v1, comme la houle (`seuil_houle_arret` reste inutilisé)
et les temps morts à quai (amarrage, ouverture des cales) : l'occupation du poste est
donc une borne basse.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from itertools import pairwise
from statistics import fmean

import simpy

from corridor.sim.config import SimConfig
from corridor.sim.samplers import Samplers, Streams

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
    """Intègre une grandeur dans le temps : l'aire sous sa courbe, en unité × heure.

    `change(now, +x)` quand la grandeur augmente, `change(now, -x)` quand elle diminue,
    `read(now)` à tout moment — y compris quand elle est encore non nulle, dont le temps
    déjà écoulé est compté. La grandeur est un effectif (un poste occupé vaut +1) ou une
    masse (un navire en rade vaut +son tonnage).

    `mean(now, horizon)` ramène l'aire à une valeur moyenne sur l'horizon.
    """

    level: float = 0.0
    area: float = 0.0
    last_change: float = 0.0

    def change(self, now: float, delta: float) -> None:
        self.area += self.level * (now - self.last_change)
        self.level += delta
        self.last_change = now

    def read(self, now: float) -> float:
        return self.area + self.level * (now - self.last_change)

    def mean(self, now: float, horizon: float) -> float:
        """Valeur moyenne sur `horizon` heures ; 0 si l'horizon est nul."""
        return self.read(now) / horizon if horizon else 0.0


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
    berth_blocked_h: float
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
    # stock flottant : tonnage en attente en rade, moyenné sur l'horizon
    waiting_tonnage_mean_t: float
    # approvisionnement : bilan amont et politique de commande
    pellets_ordered_t: float  # engagé (commandé, ou arrivé en politique exogène)
    pellets_afloat_t: float  # à flot à la fin : en mer, en rade, encore à bord à quai
    pellets_at_sea_mean_t: float  # en mer, moyenné sur l'horizon ; nul en exogène
    orders_placed: int  # commandes passées ; égal aux arrivées en exogène
    lead_time_mean_h: float  # délai moyen tiré ; nul en exogène
    inventory_position_min_t: float
    inventory_position_mean_t: float


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
    berth_blocked: TimeIntegral = field(default_factory=TimeIntegral)
    rame_rolling: TimeIntegral = field(default_factory=TimeIntegral)
    rame_blocked: TimeIntegral = field(default_factory=TimeIntegral)
    anchorage_tonnage: TimeIntegral = field(default_factory=TimeIntegral)
    at_sea_tonnage: TimeIntegral = field(default_factory=TimeIntegral)
    pellets_ordered_t: float = 0.0
    pellets_afloat_t: float = 0.0
    orders_placed: int = 0
    lead_times_h: list[float] = field(default_factory=list)
    waits_h: list[float] = field(default_factory=list)
    stockyard_samples: list[float] = field(default_factory=list)
    plant_samples: list[float] = field(default_factory=list)
    inventory_samples: list[float] = field(default_factory=list)


class _Corridor:
    """Les ressources du corridor et les processus qui les font vivre."""

    def __init__(
        self,
        env: simpy.Environment,
        config: SimConfig,
        samplers: Samplers,
        streams: Streams,
        years: float,
    ) -> None:
        self.env = env
        self.config = config
        self.samplers = samplers
        self.streams = streams
        self.metrics = _Metrics()
        self.pull = config.politique_expedition == "pull"
        # Réveil de la politique de commande. La position de stock ne baisse qu'aux
        # consommations du four : décharger, expédier ou faire arriver un navire déplace du
        # minerai sans en retirer. Le four réveille donc la politique, et elle seule ; une
        # revue à pas fixe ajouterait un retard qui se confondrait avec le délai.
        self.reveil: simpy.Event | None = None
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
        """Engendre les minéraliers : intervalle, taille et cadence, un flux chacun.

        Les trois grandeurs du navire sont tirées **ici**, à sa création, et non là où le
        modèle s'en sert. La cadence de déchargement ne servira qu'une fois le poste obtenu,
        mais la tirer à ce moment-là la ferait dépendre de l'ordre d'accostage, donc de la
        congestion, donc du stockage. Tirée à l'arrivée, elle ne dépend que du rang du
        navire : c'est ce qui rend deux configurations comparables tirage à tirage.
        """
        intervalles = self.streams.stream("intervalle")
        tailles = self.streams.stream("taille_navire")
        cadences = self.streams.stream("cadence_dechargement")
        while True:
            yield self.env.timeout(self.samplers.interval_h.sample(intervalles))
            size_t = self.samplers.vessel_size_t.sample(tailles)
            rate = self.samplers.discharge_rate_t_par_h.sample(cadences)
            self._engager(size_t)
            self.env.process(self.vessel(size_t, rate))

    def approvisionnement(self) -> Generator:
        """Politique (s, Q) en revue continue : un navire dès que la position passe sous s.

        Q vaut un navire, dont la taille est tirée à la commande. Si la position est très
        en dessous de s — au démarrage, ou après une longue panne —, la boucle intérieure
        commande autant de navires qu'il faut pour repasser au-dessus : c'est la forme
        classique de la politique, qui commande par multiples de Q.

        Taille, cadence et délai sont tirés **à la commande**, chacun dans son flux : la
        n-ième commande a les mêmes trois valeurs d'une configuration à l'autre, même si
        elle part à une autre date.
        """
        tailles = self.streams.stream("taille_navire")
        cadences = self.streams.stream("cadence_dechargement")
        delais = self.streams.stream("delai_approvisionnement")
        seuil = self.config.point_de_commande_t
        while True:
            while self.position_de_stock() < seuil:
                size_t = self.samplers.vessel_size_t.sample(tailles)
                rate = self.samplers.discharge_rate_t_par_h.sample(cadences)
                delai_h = self.samplers.lead_time_h.sample(delais)
                self._engager(size_t)
                self.metrics.lead_times_h.append(delai_h)
                self.env.process(self.navire_en_mer(size_t, rate, delai_h))
            self.reveil = self.env.event()
            yield self.reveil

    def navire_en_mer(self, size_t: float, rate: float, delai_h: float) -> Generator:
        """Le délai d'approvisionnement, puis le même parcours qu'un navire exogène.

        Le minerai compte dans la position de stock dès la commande : c'est ce qui empêche
        la politique de commander un navire par heure jusqu'à l'arrivée du premier.
        """
        self.metrics.at_sea_tonnage.change(self.env.now, +size_t)
        yield self.env.timeout(delai_h)
        self.metrics.at_sea_tonnage.change(self.env.now, -size_t)
        yield from self.vessel(size_t, rate)

    def position_de_stock(self) -> float:
        """Stockyard + usine + rail en transit + à flot. Tout le minerai déjà acquis.

        « À flot » couvre les navires en mer, en rade **et** la cargaison encore à bord d'un
        navire à quai : sans ce dernier terme, la position chuterait d'un navire entier à
        l'accostage puis remonterait pendant le déchargement, et la politique commanderait
        pour rien. Chaque tonne est ainsi comptée une fois et une seule, où qu'elle soit.
        """
        return (self.stockyard.level + self.plant.level
                + self.metrics.pellets_in_transit_t + self.metrics.pellets_afloat_t)

    def _engager(self, size_t: float) -> None:
        """Le navire entre au bilan amont : commandé, ou arrivé en politique exogène."""
        self.metrics.orders_placed += 1
        self.metrics.pellets_ordered_t += size_t
        self.metrics.pellets_afloat_t += size_t

    def _reveiller(self) -> None:
        """Réveille la politique de commande, si elle attend. Sans effet en exogène."""
        if self.reveil is not None and not self.reveil.triggered:
            self.reveil.succeed()

    def vessel(self, size_t: float, rate: float) -> Generator:
        """Un navire : attente en rade, puis poste, puis déchargement par tranches.

        Le poste reste occupé pendant toute la durée du déchargement, y compris les
        pauses dues à un stockyard plein : c'est le navire qui bloque le quai, et ces
        heures-là comptent dans le taux d'occupation.
        """
        arrived_at = self.env.now
        self.metrics.vessels_arrived += 1
        # le navire attend en rade avec sa cargaison : stock flottant, pas encore au port
        self.metrics.anchorage_tonnage.change(arrived_at, +size_t)
        with self.berths.request() as berth:
            yield berth
            self.metrics.anchorage_tonnage.change(self.env.now, -size_t)
            self.metrics.waits_h.append(self.env.now - arrived_at)
            self.metrics.berth.change(self.env.now, +1)
            remaining = size_t
            while remaining > _EPSILON_T:
                step_h = min(self.config.pas_h, remaining / rate)
                yield self.env.timeout(step_h)
                chunk = min(remaining, rate * step_h)
                # le navire tient le quai sans décharger tant que le stockyard est plein :
                # ces heures-là sont du blocage, pas du service (voir berth_blocked_h)
                self.metrics.berth_blocked.change(self.env.now, +1)
                yield self.stockyard.put(chunk)  # bloque si plein : déchargement en pause
                self.metrics.berth_blocked.change(self.env.now, -1)
                self.metrics.pellets_delivered_t += chunk
                self.metrics.pellets_afloat_t -= chunk
                remaining -= chunk
            self.metrics.berth.change(self.env.now, -1)
            self.metrics.vessels_served += 1

    def rame(self, index: int = 0) -> Generator:
        """Une rame : attend d'avoir de quoi charger, fait l'aller-retour, recommence.

        Chaque rame a **son** flux (`index`), pour que son j-ième cycle tire les mêmes
        durées d'une configuration à l'autre. Un flux partagé les distribuerait dans
        l'ordre où les rames se présentent, qui dépend de l'état des stocks.

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
        durees = self.streams.stream("rail", index)
        while True:
            if self.pull:
                yield self.plant_space.get(charge)  # inactif tant que l'usine est pleine
            yield self.stockyard.get(charge)  # inactif tant qu'il n'y a rien à charger
            self.metrics.pellets_in_transit_t += charge
            yield from self._roll(self.samplers.load_h.sample(durees))
            yield from self._roll(self.samplers.travel_h.sample(durees))
            self.metrics.rame_blocked.change(self.env.now, +1)
            yield self.plant.put(charge)  # bloque si le stock usine est plein
            self.metrics.rame_blocked.change(self.env.now, -1)
            self.metrics.pellets_in_transit_t -= charge
            self.metrics.pellets_railed_t += charge
            yield from self._roll(self.samplers.unload_h.sample(durees))
            yield from self._roll(self.samplers.travel_h.sample(durees))  # retour à vide

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
                self._reveiller()
            else:
                # stock usine épuisé : le four s'arrête, l'heure est comptée
                self.metrics.dri_stop_hours += step_h
            yield self.env.timeout(step_h)

    def monitor(self) -> Generator:
        """Échantillonne les deux niveaux de stock à intervalle fixe."""
        while True:
            self.metrics.stockyard_samples.append(self.stockyard.level)
            self.metrics.plant_samples.append(self.plant.level)
            self.metrics.inventory_samples.append(self.position_de_stock())
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
            berth_blocked_h=m.berth_blocked.read(now),
            stockyard_min_t=min(m.stockyard_samples) if m.stockyard_samples else 0.0,
            stockyard_mean_t=fmean(m.stockyard_samples) if m.stockyard_samples else 0.0,
            stockyard_final_t=self.stockyard.level,
            plant_stock_min_t=min(m.plant_samples) if m.plant_samples else 0.0,
            plant_stock_mean_t=fmean(m.plant_samples) if m.plant_samples else 0.0,
            plant_stock_final_t=self.plant.level,
            rame_utilisation=m.rame_rolling.read(now) / rame_hours if rame_hours else 0.0,
            rame_blocked_h=m.rame_blocked.read(now),
            waiting_tonnage_mean_t=m.anchorage_tonnage.mean(now, horizon_h),
            pellets_ordered_t=m.pellets_ordered_t,
            pellets_afloat_t=m.pellets_afloat_t,
            pellets_at_sea_mean_t=m.at_sea_tonnage.mean(now, horizon_h),
            orders_placed=m.orders_placed,
            lead_time_mean_h=fmean(m.lead_times_h) if m.lead_times_h else 0.0,
            inventory_position_min_t=min(m.inventory_samples) if m.inventory_samples else 0.0,
            inventory_position_mean_t=(
                fmean(m.inventory_samples) if m.inventory_samples else 0.0
            ),
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

    Les générateurs sont créés ici et nulle part ailleurs : à graine égale, le résultat
    est identique. Il y en a **un par source d'aléa** (`Streams`), de sorte que deux
    configurations ne différant que par une capacité voient la même séquence dans chaque
    flux — le navire n a la même taille et la même cadence dans les deux. C'est la
    condition pour que les nombres aléatoires communs servent à quelque chose.

    Passer un jeu de `samplers` permet de substituer des lois empiriques sans rien
    changer au modèle.
    """
    env, corridor = _construire(config, seed, years, samplers)
    env.run(until=years * config.heures_par_an)
    return corridor.result(scenario=scenario, rep=rep, seed=seed, years=years)


def _construire(config, seed, years, samplers=None) -> tuple[simpy.Environment, _Corridor]:
    """L'environnement et le corridor, processus lancés : le seul endroit qui les assemble.

    L'origine des navires est choisie ici et nulle part ailleurs dans le modèle.
    """
    env = simpy.Environment()
    corridor = _Corridor(
        env=env,
        config=config,
        samplers=samplers or Samplers.from_config(config),
        streams=Streams(seed),
        years=years,
    )
    if config.politique_approvisionnement == "point_de_commande":
        env.process(corridor.approvisionnement())
    else:
        env.process(corridor.arrivals())
    env.process(corridor.dri())
    env.process(corridor.monitor())
    for index in range(config.nombre_de_rames):
        env.process(corridor.rame(index))
    return env, corridor


def production_par_periode(
    config: SimConfig,
    seed: int,
    years: float,
    periode_h: float | None = None,
    samplers: Samplers | None = None,
) -> list[dict[str, float]]:
    """Production et heures de rupture de chaque période d'**une seule** réplication.

    Ce sont les lots de la méthode des moyennes par lots : une longue trajectoire coupée en
    tranches successives, plutôt que des réplications indépendantes. Par défaut une période
    vaut un an. La somme des productions des périodes vaut exactement la production de
    `simulate` à la même graine et au même horizon : les périodes sont des différences de
    cumuls relevés pendant la simulation, et la dernière est fermée sur l'état final.
    """
    periode = periode_h or config.heures_par_an
    horizon = years * config.heures_par_an
    env, corridor = _construire(config, seed, years, samplers)
    m = corridor.metrics
    jalons: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]

    def releve() -> Generator:
        while True:
            yield env.timeout(periode)
            if env.now < horizon:
                jalons.append((env.now, m.dri_production_t, m.dri_stop_hours))

    env.process(releve())
    env.run(until=horizon)
    jalons.append((horizon, m.dri_production_t, m.dri_stop_hours))
    return [
        {"debut_h": a[0], "fin_h": b[0], "production_t": b[1] - a[1], "rupture_h": b[2] - a[2]}
        for a, b in pairwise(jalons)
    ]
