"""Formules de files d'attente : le repère analytique du modèle de simulation.

Ce module ne simule rien. Il donne l'attente moyenne que la théorie des files prédit pour
le poste de déchargement, afin de pouvoir **valider le moteur de simulation** contre un
résultat exact. C'est le seul endroit du projet où une attente est calculée sans SimPy.

**Pourquoi c'est utile.** Si l'attente simulée s'écarte de la formule exacte dans le cas
où la formule s'applique, alors le modèle a un défaut : mauvaise gestion du poste,
mauvaise horloge, ou un aléa qui n'a pas la loi annoncée. Inversement, un accord à
l'intérieur de l'intervalle de confiance ne prouve pas que tout le corridor est juste —
seulement que la file d'attente devant le poste se comporte comme une file M/G/1.

**Le cas où la formule s'applique.** Pollaczek-Khinchine exige : arrivées de Poisson,
**un** serveur, discipline sans priorité, et un service dont la durée ne dépend pas de
l'état du système. Dans le corridor, cette dernière condition est la plus fragile : dès
que le stockyard sature, le navire reste à quai sans décharger, et la durée d'occupation
du poste devient fonction de la file en amont. C'est pourquoi la validation se fait sur
le scénario `baseline_sans_blocage`, dont les capacités de stockage sont assez grandes
pour qu'aucun blocage ne soit possible. Dans tous les autres scénarios, la formule est
un **minorant** de l'attente, pas une prédiction — et l'écart se mesure (voir
`kingman` et l'indicateur `berth_blocked_h`).

**Unités.** Le temps est en heures, la masse en tonnes, comme dans tout
`corridor.sim`. Les taux sont donc des navires par heure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from corridor.sim.samplers import Samplers

#: Nombre de tirages par défaut pour estimer les moments du temps de service.
DEFAULT_MOMENT_DRAWS = 200_000


def utilisation(lam: float, es: float) -> float:
    """Taux d'occupation d'un serveur unique : ρ = λ × E[S].

    Au-delà de 1, la file est instable : il arrive plus de navires que le poste ne peut
    en servir, et l'attente moyenne n'a pas de limite finie.
    """
    if lam <= 0:
        raise ValueError(f"taux d'arrivée attendu strictement positif, reçu {lam}")
    if es <= 0:
        raise ValueError(f"durée de service attendue strictement positive, reçue {es}")
    return lam * es


def scv(es: float, es2: float) -> float:
    """Carré du coefficient de variation du service : c² = E[S²]/E[S]² − 1.

    C'est la forme sous laquelle la variabilité entre dans l'approximation de Kingman.
    """
    if es <= 0:
        raise ValueError(f"durée de service attendue strictement positive, reçue {es}")
    if es2 < es * es:
        raise ValueError(
            f"moments incohérents : E[S²] = {es2:g} est inférieur à E[S]² = {es * es:g}, "
            "ce qu'aucune loi ne permet (inégalité de Cauchy-Schwarz)"
        )
    return es2 / (es * es) - 1.0


def pollaczek_khinchine(lam: float, es: float, es2: float) -> float:
    """Attente moyenne **en file** d'un M/G/1, formule exacte de Pollaczek-Khinchine.

        Wq = λ × E[S²] / (2 × (1 − ρ))     avec ρ = λ × E[S]

    `lam` est le taux d'arrivée (navires/h), `es` et `es2` les deux premiers moments de
    la durée de service (h et h²). Le résultat est l'attente **avant** le service, hors
    durée de service : c'est ce que le modèle appelle `wait_mean_h`.

    Exact sous ses hypothèses — arrivées de Poisson, un serveur, service indépendant de
    l'état du système — et valable quelle que soit la loi du service, d'où le « G ».
    Lève si la file est saturée (ρ ≥ 1), car l'attente y est infinie et renvoyer un
    nombre fini serait trompeur.
    """
    rho = utilisation(lam, es)
    if rho >= 1.0:
        raise ValueError(
            f"file saturée : ρ = {rho:.3f} ≥ 1, l'attente moyenne n'est pas finie "
            "(Pollaczek-Khinchine ne s'applique qu'en régime stable)"
        )
    scv(es, es2)  # refuse des moments impossibles avant de calculer
    return lam * es2 / (2.0 * (1.0 - rho))


def kingman(rho: float, ca2: float, cs2: float, es: float) -> float:
    """Approximation de Kingman (dite « VUT ») de l'attente moyenne d'un G/G/1.

        Wq ≈ ρ/(1 − ρ) × (c_a² + c_s²)/2 × E[S]

    Trois facteurs : utilisation (ρ/(1−ρ)), variabilité ((c_a²+c_s²)/2) et temps (E[S]).
    `ca2` et `cs2` sont les carrés des coefficients de variation des intervalles entre
    arrivées et des durées de service.

    C'est une **approximation**, pas une identité : elle est exacte pour M/M/1, bonne à
    forte utilisation, et elle sous-estime l'attente à faible utilisation. Son intérêt
    ici est d'accepter des arrivées non poissonniennes (c_a² ≠ 1), donc de couvrir toute
    la grille de sensibilité, là où Pollaczek-Khinchine ne couvre que la colonne de
    Poisson. À c_a² = 1 et service exponentiel (c_s² = 1), les deux formules coïncident.
    """
    if not 0.0 <= rho < 1.0:
        raise ValueError(f"utilisation attendue dans [0, 1), reçue {rho}")
    if ca2 < 0 or cs2 < 0:
        raise ValueError(f"carrés de coefficients de variation négatifs : {ca2}, {cs2}")
    if es <= 0:
        raise ValueError(f"durée de service attendue strictement positive, reçue {es}")
    return rho / (1.0 - rho) * (ca2 + cs2) / 2.0 * es


@dataclass(frozen=True, slots=True)
class ServiceMoments:
    """Moments du temps de service estimés par tirage, avec l'effectif qui les fonde."""

    es: float
    es2: float
    draws: int

    @property
    def scv(self) -> float:
        """Carré du coefficient de variation du service."""
        return scv(self.es, self.es2)


def service_moments(
    samplers: Samplers,
    draws: int = DEFAULT_MOMENT_DRAWS,
    rng: random.Random | None = None,
) -> ServiceMoments:
    """Estime E[S] et E[S²] du temps de déchargement d'un navire, par tirage de Monte-Carlo.

    Le temps de service du poste est `tonnage / cadence`, les deux tirés une fois par
    navire : c'est exactement ce que fait `_Corridor.vessel`, où le déchargement par
    tranches de `pas_de_temps` totalise `tonnage / cadence` à l'heure près tant qu'aucun
    blocage ne l'interrompt. **Si le modèle change cette composition, cette fonction doit
    changer avec lui** — c'est sa seule fragilité.

    Estimé par tirage et non par algèbre à dessein : la formule fermée dépendrait de la
    loi choisie (gamma aujourd'hui), alors que le tirage reste juste quand les lois
    deviendront empiriques (`Samplers.from_escales`, tâche 3). L'erreur de Monte-Carlo
    décroît en 1/√`draws` et reste négligeable devant l'intervalle de confiance des
    réplications au nombre de tirages par défaut.
    """
    if draws < 2:
        raise ValueError(f"au moins 2 tirages attendus, reçu {draws}")
    generator = rng or random.Random(0)
    somme = somme_carres = 0.0
    for _ in range(draws):
        size_t = samplers.vessel_size_t.sample(generator)
        rate = samplers.discharge_rate_t_par_h.sample(generator)
        service_h = size_t / rate
        somme += service_h
        somme_carres += service_h * service_h
    return ServiceMoments(es=somme / draws, es2=somme_carres / draws, draws=draws)


def effective_service(es: float, blocked_h: float, vessels_served: int) -> float:
    """Temps de service **effectif** du poste : E[S_eff] = E[S] + B/N.

    `blocked_h` (B) est le temps total pendant lequel un navire occupait le poste sans
    décharger, faute de place au stockyard (`berth_blocked_h`), et `vessels_served` (N)
    le nombre de navires servis. B/N est donc le blocage moyen par navire.

    L'idée testée est que le poste n'est pas un serveur de durée E[S] mais de durée
    E[S] + B/N : la contre-pression du stockyard allonge l'occupation du quai, et c'est
    cette occupation-là que la file voit. Attention, ce n'est pas qu'un changement
    d'unité : un service dont la durée dépend de l'état du système viole l'hypothèse
    d'indépendance de Pollaczek-Khinchine. E[S_eff] rend la formule *moins fausse*, il ne
    la rend pas exacte.
    """
    if vessels_served <= 0:
        raise ValueError(f"aucun navire servi : B/N indéfini (N = {vessels_served})")
    if blocked_h < 0:
        raise ValueError(f"temps de blocage négatif : {blocked_h}")
    return es + blocked_h / vessels_served
