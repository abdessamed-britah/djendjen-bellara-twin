"""Modèle SimPy du corridor port de Djen Djen → rail → aciérie de Bellara (étape 4).

Cinq processus, une unité de temps (l'heure), une unité de masse (la tonne) :

1. l'origine des navires, au choix (`simulation.politique_approvisionnement`) :
   - `arrivals` — **exogène** : un minéralier arrive après chaque intervalle tiré ;
   - `approvisionnement` — **point de commande** : un minéralier est affrété dès que la
     position de stock passe sous le point de commande, puis `navire_en_mer` le fait
     arriver en rade après un délai tiré ;
2. `vessel`    — attente en rade, poste de déchargement, déchargement par tranches ;
3. `rame`      — navette port → usine → port, une par rame, en boucle ;
4. `dri`       — consommation horaire, arrêts planifiés, arrêts faute de stock, et en mode
   discret (`simulation.mode_arrets_dri`) des arrêts non planifiés : le four tourne alors à
   pleine cadence ou pas du tout, à disponibilité moyenne inchangée ;
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

**Régime stationnaire : l'échauffement.** `simulate(..., warmup_years=W)` simule `years`
années mais ne publie que la fenêtre [W, years]. Les compteurs ne sont pas remis à zéro à W :
on y prend un **instantané des cumuls**, et chaque indicateur publié est une différence
entre l'horizon et cet instantané. Une remise à zéro casserait les bilans de masse : à
l'instant W, une tranche peut être déjà versée au stockyard sans être encore comptée comme
déchargée, puisque le conteneur et le compteur ne bougent pas au même événement. Les
différences de cumuls, elles, gardent les deux bilans exacts — le stock « initial » publié
devient ce que les compteurs disent présent à W. À W = 0, aucun instantané n'est pris et le
calcul est identique, au bit près, à celui d'avant l'option.

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
    # étape 2 : régime stationnaire, coûts, options de la politique
    warmup_years: float  # années d'échauffement écartées ; la fenêtre publiée est le reste
    pellets_afloat_initial_t: float  # à flot au début de la fenêtre : bilan amont
    anchorage_ship_hours: float  # navire-heures en rade, intégrées : base des surestaries
    order_deferral_h: float  # heures pendant lesquelles une commande a été reportée
    dri_breakdown_hours: float  # arrêts non planifiés (mode discret) ; 0 en mode continu

    @property
    def annees_mesurees(self) -> float:
        """Longueur de la fenêtre publiée, en années : c'est elle qui fixe la cible."""
        return self.years - self.warmup_years


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
    anchorage_ships: TimeIntegral = field(default_factory=TimeIntegral)
    order_deferral_h: float = 0.0
    dri_breakdown_hours: float = 0.0
    pellets_ordered_t: float = 0.0
    pellets_afloat_t: float = 0.0
    orders_placed: int = 0
    lead_times_h: list[float] = field(default_factory=list)
    waits_h: list[float] = field(default_factory=list)
    stockyard_samples: list[float] = field(default_factory=list)
    plant_samples: list[float] = field(default_factory=list)
    inventory_samples: list[float] = field(default_factory=list)
    arrival_times_h: list[float] = field(default_factory=list)
    # date d'arrivée du navire de chaque attente de `waits_h`, dans le même ordre : une
    # attente est enregistrée à l'accostage, pas à l'arrivée
    waits_arrival_h: list[float] = field(default_factory=list)


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
        # Consommation fictive retranchée de la position vue par la politique de commande
        # pendant un arrêt du four (`politique_arrets`) ; nulle hors arrêt, et toujours nulle
        # en politique « aveugle ».
        self.consommation_fictive_t = 0.0
        # Dépassements interdits : date d'arrivée du dernier navire commandé, que le
        # suivant ne peut pas devancer.
        self._derniere_arrivee = 0.0
        # Instantané des cumuls à la fin de l'échauffement ; None sans échauffement.
        self.instantane: dict | None = None
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
        reporter = self.config.reporter_si_navire_en_rade
        while True:
            while self.position_de_stock() - self.consommation_fictive_t < seuil:
                if reporter and self.metrics.anchorage_ships.level > 0:
                    # un navire attend déjà : on reporte, et on le compte. Le réveil suivant
                    # vient de la consommation ou de l'accostage de ce navire.
                    self.metrics.order_deferral_h += self.config.pas_h
                    break
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
        if not self.config.depassements_autorises:
            # navette sur une seule route : ce navire n'arrive pas avant le précédent
            arrivee = max(self.env.now + delai_h, self._derniere_arrivee)
            self._derniere_arrivee = arrivee
            delai_h = arrivee - self.env.now
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
        self.metrics.arrival_times_h.append(arrived_at)
        # le navire attend en rade avec sa cargaison : stock flottant, pas encore au port
        self.metrics.anchorage_tonnage.change(arrived_at, +size_t)
        self.metrics.anchorage_ships.change(arrived_at, +1)
        with self.berths.request() as berth:
            yield berth
            self.metrics.anchorage_tonnage.change(self.env.now, -size_t)
            self.metrics.anchorage_ships.change(self.env.now, -1)
            if self.config.reporter_si_navire_en_rade:
                # une commande a pu être reportée à cause de ce navire : réévaluer
                self._reveiller()
            self.metrics.waits_h.append(self.env.now - arrived_at)
            self.metrics.waits_arrival_h.append(arrived_at)
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
        """Consommation horaire du DRI, arrêts planifiés, arrêts faute de pellets.

        **Mode continu** : le four tourne en permanence à cadence réduite, capacité × taux
        d'utilisation. **Mode discret** : il tourne à pleine cadence, et alterne marche et
        arrêt non planifié. La durée d'un arrêt suit sa loi (`usine.duree_arret_non_planifie
        _j`), la durée de marche une exponentielle de moyenne telle que la disponibilité
        égale le taux d'utilisation : la production moyenne est la même dans les deux modes,
        seule change sa régularité.

        Deux choix du mode discret, à connaître : l'horloge des pannes n'avance **que pendant
        les heures ouvrées** — un arrêt planifié ne consomme pas de marche ni d'arrêt, sinon
        les deux se recouvriraient et la disponibilité serait faussée ; et elle avance aussi
        pendant une rupture de pellets, parce qu'une panne est une affaire de temps, pas de
        tonnage. Les tirages viennent d'un flux dédié, `arrets_dri`.
        """
        step_h = self.config.pas_h
        discret = self.config.mode_arrets_dri == "discret"
        if discret:
            besoin_t = self.config.debit_pellets_plein_t_par_h * step_h
            production_t = self.config.debit_dri_plein_t_par_h * step_h
            pannes = self.streams.stream("arrets_dri")
            en_panne = False
            reste_h = self.samplers.uptime_h.sample(pannes)
        else:
            besoin_t = self.config.debit_pellets_t_par_h * step_h
            production_t = self.config.debit_dri_t_par_h * step_h
        while True:
            fin_planifiee = self._planned_stop_end(self.env.now)
            if fin_planifiee is not None:
                self.metrics.dri_planned_stop_hours += step_h
                self._pendant_un_arret(fin_planifiee - self.env.now)
                yield self.env.timeout(step_h)
                continue
            if discret:
                # L'heure prend l'état du processus à son premier instant, puis elle est
                # décomptée de cet état-là : c'est un échantillonnage régulier de l'alternance,
                # sans biais (disponibilité 0,8523 ± 0,0019 pour 0,85 visés, 20 graines × 20
                # ans à 12 h d'arrêt). Dans l'ordre inverse, l'heure de bascule irait au nouvel
                # état sans être prise sur sa durée : chaque période gagnerait une heure, soit
                # 69/82 = 0,84 à 12 h d'arrêt. Le `while` traverse un intervalle plus court
                # qu'une heure au lieu de lui en accorder une entière.
                while reste_h <= 0:
                    en_panne = not en_panne
                    loi = self.samplers.breakdown_h if en_panne else self.samplers.uptime_h
                    reste_h += loi.sample(pannes)
                reste_h -= step_h
                if en_panne:
                    self.metrics.dri_breakdown_hours += step_h
                    self._pendant_un_arret(reste_h + step_h)
                    yield self.env.timeout(step_h)
                    continue
            self.consommation_fictive_t = 0.0
            if self.plant.level >= besoin_t:
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

    def _pendant_un_arret(self, reste_h: float) -> None:
        """Une heure d'arrêt du four, vue par la politique de commande.

        `reste_h` est la durée restante de l'arrêt, heure en cours comprise. « aveugle » ne
        fait rien : la position se fige. « rythme_moyen » retranche une heure de consommation
        moyenne. « date_de_reprise » remplace la consommation attendue sur le délai moyen L
        (rythme moyen × L, celle pour laquelle s est réglé) par celle qu'on sait devoir
        venir : pleine cadence × (L − R)⁺ en mode discret, cadence de marche sinon.
        """
        politique = self.config.politique_arrets
        if politique == "aveugle":
            return
        if politique == "rythme_moyen":
            self.consommation_fictive_t += self.config.debit_pellets_t_par_h * self.config.pas_h
        else:
            delai_h = self.config.delai_approvisionnement_h
            cadence = (self.config.debit_pellets_plein_t_par_h
                       if self.config.mode_arrets_dri == "discret"
                       else self.config.debit_pellets_t_par_h)
            attendue = cadence * max(delai_h - reste_h, 0.0)
            self.consommation_fictive_t = attendue - self.config.debit_pellets_t_par_h * delai_h
        self._reveiller()

    def _planned_stop(self, now: float) -> bool:
        return self._planned_stop_end(now) is not None

    def _planned_stop_end(self, now: float) -> float | None:
        """Fin de l'arrêt planifié en cours, ou None hors arrêt planifié."""
        for start, end in self.stops:
            if start <= now < end:
                return end
        return None

    # --- restitution ------------------------------------------------------

    #: Compteurs cumulés et intégrales relevés dans l'instantané d'échauffement.
    _CUMULS = (
        "pellets_delivered_t", "pellets_railed_t", "pellets_consumed_t", "dri_production_t",
        "dri_stop_hours", "dri_planned_stop_hours", "vessels_arrived", "vessels_served",
        "pellets_ordered_t", "orders_placed", "order_deferral_h", "dri_breakdown_hours",
    )
    _INTEGRALES = (
        "berth", "berth_blocked", "rame_rolling", "rame_blocked", "anchorage_tonnage",
        "at_sea_tonnage", "anchorage_ships",
    )
    _LISTES = ("waits_h", "stockyard_samples", "plant_samples", "inventory_samples",
               "lead_times_h", "arrival_times_h", "waits_arrival_h")

    def prendre_instantane(self) -> None:
        """Fin de l'échauffement : relève les cumuls, sans rien remettre à zéro."""
        m, now = self.metrics, self.env.now
        self.instantane = {
            **{nom: getattr(m, nom) for nom in self._CUMULS},
            **{nom: getattr(m, nom).read(now) for nom in self._INTEGRALES},
            **{nom: len(getattr(m, nom)) for nom in self._LISTES},
        }

    def result(self, scenario: str, rep: int, seed: int, years: float,
               warmup_years: float = 0.0) -> SimResult:
        """Agrège les compteurs en une ligne de résultat, sur la fenêtre publiée."""
        m = self.metrics
        config = self.config
        now = self.env.now
        w = self.instantane
        horizon_h = (years - warmup_years) * config.heures_par_an
        berth_hours = horizon_h * config.postes_minerai
        rame_hours = horizon_h * config.nombre_de_rames

        def cumul(nom):
            return getattr(m, nom) - w[nom] if w else getattr(m, nom)

        def aire(nom):
            return getattr(m, nom).read(now) - w[nom] if w else getattr(m, nom).read(now)

        def liste(nom):
            return getattr(m, nom)[w[nom]:] if w else getattr(m, nom)

        waits = liste("waits_h")
        stockyard_samples = liste("stockyard_samples")
        plant_samples = liste("plant_samples")
        inventory_samples = liste("inventory_samples")
        lead_times = liste("lead_times_h")
        initial_t = config.stock_initial_stockyard_t + config.stock_initial_usine_t
        if w:
            # ce que les compteurs disent présent à W, stocks et rail compris
            initial_t += w["pellets_delivered_t"] - w["pellets_consumed_t"]
        afloat_initial = w["pellets_ordered_t"] - w["pellets_delivered_t"] if w else 0.0
        return SimResult(
            scenario=scenario,
            rep=rep,
            seed=seed,
            years=years,
            horizon_h=horizon_h,
            dispersion_arrivees=config.dispersion_arrivees,
            initial_stock_t=initial_t,
            pellets_delivered_t=cumul("pellets_delivered_t"),
            pellets_railed_t=cumul("pellets_railed_t"),
            pellets_consumed_t=cumul("pellets_consumed_t"),
            pellets_in_transit_t=m.pellets_in_transit_t,
            dri_production_t=cumul("dri_production_t"),
            dri_stop_hours=cumul("dri_stop_hours"),
            dri_planned_stop_hours=cumul("dri_planned_stop_hours"),
            vessels_arrived=cumul("vessels_arrived"),
            vessels_served=cumul("vessels_served"),
            wait_mean_h=fmean(waits) if waits else 0.0,
            wait_p90_h=percentile(waits, 0.90),
            berth_occupancy=aire("berth") / berth_hours if berth_hours else 0.0,
            berth_blocked_h=aire("berth_blocked"),
            stockyard_min_t=min(stockyard_samples) if stockyard_samples else 0.0,
            stockyard_mean_t=fmean(stockyard_samples) if stockyard_samples else 0.0,
            stockyard_final_t=self.stockyard.level,
            plant_stock_min_t=min(plant_samples) if plant_samples else 0.0,
            plant_stock_mean_t=fmean(plant_samples) if plant_samples else 0.0,
            plant_stock_final_t=self.plant.level,
            rame_utilisation=aire("rame_rolling") / rame_hours if rame_hours else 0.0,
            rame_blocked_h=aire("rame_blocked"),
            waiting_tonnage_mean_t=aire("anchorage_tonnage") / horizon_h if horizon_h else 0.0,
            pellets_ordered_t=cumul("pellets_ordered_t"),
            pellets_afloat_t=m.pellets_afloat_t,
            pellets_at_sea_mean_t=aire("at_sea_tonnage") / horizon_h if horizon_h else 0.0,
            orders_placed=cumul("orders_placed"),
            lead_time_mean_h=fmean(lead_times) if lead_times else 0.0,
            inventory_position_min_t=min(inventory_samples) if inventory_samples else 0.0,
            inventory_position_mean_t=fmean(inventory_samples) if inventory_samples else 0.0,
            warmup_years=warmup_years,
            pellets_afloat_initial_t=afloat_initial,
            anchorage_ship_hours=aire("anchorage_ships"),
            order_deferral_h=cumul("order_deferral_h"),
            dri_breakdown_hours=cumul("dri_breakdown_hours"),
        )


def simulate(
    config: SimConfig,
    seed: int,
    years: float,
    samplers: Samplers | None = None,
    scenario: str = "",
    rep: int = 0,
    warmup_years: float = 0.0,
) -> SimResult:
    """Simule `years` années de corridor et renvoie les indicateurs de la réplication.

    Les générateurs sont créés ici et nulle part ailleurs : à graine égale, le résultat
    est identique. Il y en a **un par source d'aléa** (`Streams`), de sorte que deux
    configurations ne différant que par une capacité voient la même séquence dans chaque
    flux — le navire n a la même taille et la même cadence dans les deux. C'est la
    condition pour que les nombres aléatoires communs servent à quelque chose.

    Passer un jeu de `samplers` permet de substituer des lois empiriques sans rien
    changer au modèle.

    `warmup_years` écarte le début de la trajectoire des indicateurs publiés : c'est la
    mesure en régime stationnaire. Voir la docstring du module pour la méthode.
    """
    env, corridor = _construire(config, seed, years, samplers, warmup_years)
    env.run(until=years * config.heures_par_an)
    return corridor.result(scenario=scenario, rep=rep, seed=seed, years=years,
                           warmup_years=warmup_years)


def attentes_en_rade(config: SimConfig, seed: int, years: float,
                     warmup_years: float = 0.0) -> list[float]:
    """L'attente en rade, en heures, de chaque navire accosté dans la fenêtre publiée.

    La distribution complète, et non sa moyenne : c'est elle qu'on place face à une
    attente observée isolée, pour dire où celle-ci tombe.
    """
    env, corridor = _construire(config, seed, years, None, warmup_years)
    env.run(until=years * config.heures_par_an)
    debut = corridor.instantane["waits_h"] if corridor.instantane else 0
    return list(corridor.metrics.waits_h[debut:])


def _construire(config, seed, years, samplers=None,
                warmup_years: float = 0.0) -> tuple[simpy.Environment, _Corridor]:
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
    if warmup_years > 0:
        if warmup_years >= years:
            raise ValueError(f"échauffement de {warmup_years} an(s) pour un horizon de "
                             f"{years} : il ne resterait rien à mesurer")

        def echauffement() -> Generator:
            yield env.timeout(warmup_years * config.heures_par_an)
            corridor.prendre_instantane()

        env.process(echauffement())
    return env, corridor


def chronique(
    config: SimConfig,
    seed: int,
    years: float,
    periode_h: float | None = None,
    samplers: Samplers | None = None,
) -> dict[str, list]:
    """La trajectoire d'**une** réplication, lue en une seule simulation.

    - `periodes` : pour chaque période successive (un an par défaut), la production, les
      heures de rupture et le **tonnage déchargé**. Ce dernier est ce que publient les
      bilans de trafic du port (« marchandises débarquées ») : c'est sur lui que se calcule
      l'indice de dispersion trimestriel, de la même façon que sur les données réelles ;
    - `arrivees_h` : la date d'arrivée en rade de chaque navire ;
    - `attentes_h` : l'attente en rade de chaque navire accosté, et `attentes_arrivee_h` la
      date d'arrivée de ce navire, pour écarter proprement l'échauffement.

    Les grandeurs d'une période sont des différences de cumuls relevés pendant la
    simulation, et la dernière période est fermée sur l'état final : leurs sommes valent
    exactement les totaux de `simulate` à la même graine et au même horizon.
    """
    periode = periode_h or config.heures_par_an
    horizon = years * config.heures_par_an
    env, corridor = _construire(config, seed, years, samplers)
    m = corridor.metrics
    jalons: list[tuple[float, ...]] = [(0.0, 0.0, 0.0, 0.0)]

    def releve() -> Generator:
        while True:
            yield env.timeout(periode)
            if env.now < horizon:
                jalons.append((env.now, m.dri_production_t, m.dri_stop_hours,
                               m.pellets_delivered_t))

    env.process(releve())
    env.run(until=horizon)
    jalons.append((horizon, m.dri_production_t, m.dri_stop_hours, m.pellets_delivered_t))
    periodes = [
        {"debut_h": a[0], "fin_h": b[0], "production_t": b[1] - a[1],
         "rupture_h": b[2] - a[2], "debarque_t": b[3] - a[3]}
        for a, b in pairwise(jalons)
    ]
    return {"periodes": periodes, "arrivees_h": list(m.arrival_times_h),
            "attentes_h": list(m.waits_h), "attentes_arrivee_h": list(m.waits_arrival_h)}


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
    vaut un an. Vue de `chronique`, dont elle garde les clés historiques.
    """
    return [
        {cle: p[cle] for cle in ("debut_h", "fin_h", "production_t", "rupture_h")}
        for p in chronique(config, seed, years, periode_h, samplers)["periodes"]
    ]
