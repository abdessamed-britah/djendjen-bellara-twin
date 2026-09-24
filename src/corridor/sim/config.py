"""Hypothèses, scénarios et grandeurs dérivées de la simulation (étape 4).

Règle du projet : **aucune valeur numérique du domaine n'est écrite dans le code**.
Tout vient de `config/assumptions.yaml`, et les grandeurs dérivées (débit du DRI,
besoin annuel en pellets, nombre de navires par an, intervalle entre arrivées, durée
de trajet) sont calculées ici, en un seul endroit, depuis ces hypothèses.

Un scénario de `config/scenarios/` ne contient que ses **écarts** aux hypothèses. Un
écart prend deux formes : une valeur littérale (`value`) ou le produit d'une autre
hypothèse (`multiply_by`). La seconde forme évite de recopier un chiffre : la capacité
DRI de la phase 2 pointe sur `phase_2.facteur_dri` et suit donc l'hypothèse si elle
change.

Convention sur le taux d'utilisation du DRI : il s'applique à l'année entière, pas
aux seuls jours ouvrés. Le débit horaire vaut donc
`capacite_dri × taux_utilisation / heures_par_an`, et les arrêts planifiés retirent
leurs heures de la production annuelle. La cible annuelle est ainsi
`capacite_dri × taux_utilisation × jours_ouvres / jours_par_an`.

Ce module est pur : il lit des fichiers de configuration, rien d'autre.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ASSUMPTIONS_PATH = Path("config/assumptions.yaml")
SCENARIOS_DIR = Path("config/scenarios")

#: Une hypothèse est un nombre, un choix textuel, ou absente (valeur à renseigner).
AssumptionValues = dict[str, float | str | None]

#: Politiques d'expédition des rames admises (voir rail.politique_expedition).
EXPEDITION_POLICIES = ("pull", "push")

#: Statuts épistémiques admis dans assumptions.yaml (l'en-tête du fichier les décrit).
STATUSES = ("connu", "observé", "annoncé", "estimé", "inconnu", "technique")

#: Millions de tonnes → tonnes. Les capacités sont publiées en Mt/an.
_MT = 1e6
_HOURS_PER_DAY = 24


@dataclass(frozen=True, slots=True)
class Scenario:
    """Un scénario : les hypothèses du projet, éventuellement amendées."""

    name: str
    description: str
    values: AssumptionValues


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Charge un YAML en dictionnaire ; lève FileNotFoundError si le fichier manque."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"fichier de configuration introuvable : {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def assumption_values(raw: dict[str, Any]) -> AssumptionValues:
    """Aplatit les hypothèses en clés pointées : `usine.capacite_dri` → 2.5.

    Les métadonnées (unité, statut, source, fourchette) ne sont pas perdues : elles
    restent lisibles dans `raw`. Une hypothèse sans valeur est conservée à None pour
    que son absence soit détectable plutôt que silencieuse. Toutes les hypothèses ne
    sont pas numériques : `rail.politique_expedition` est un choix, pas une quantité.
    """
    values: AssumptionValues = {}
    for section, entries in raw.items():
        for key, entry in entries.items():
            values[f"{section}.{key}"] = entry.get("value")
    return values


def _present(values: AssumptionValues, dotted_key: str) -> float | str:
    if dotted_key not in values:
        raise KeyError(f"hypothèse absente de assumptions.yaml : {dotted_key}")
    value = values[dotted_key]
    if value is None:
        raise ValueError(f"hypothèse {dotted_key} sans valeur : à renseigner avant simulation")
    return value


def value_of(values: AssumptionValues, dotted_key: str) -> float:
    """Valeur numérique d'une hypothèse, avec une erreur explicite si elle manque."""
    value = _present(values, dotted_key)
    if isinstance(value, str):
        raise TypeError(f"hypothèse {dotted_key} non numérique : {value!r}")
    return float(value)


def text_of(values: AssumptionValues, dotted_key: str, allowed: tuple[str, ...]) -> str:
    """Valeur textuelle d'une hypothèse, restreinte à un choix fermé."""
    value = str(_present(values, dotted_key))
    if value not in allowed:
        raise ValueError(f"hypothèse {dotted_key} : attendu l'un de {allowed}, reçu {value!r}")
    return value


def apply_overrides(
    values: AssumptionValues,
    overrides: dict[str, dict[str, Any]] | None,
) -> AssumptionValues:
    """Applique les écarts d'un scénario et renvoie un nouveau dictionnaire.

    Un écart porte sur une clé qui existe déjà : un scénario amende les hypothèses,
    il n'en invente pas. Deux formes admises, `{value: x}` et
    `{multiply_by: autre.cle}`. Une valeur non numérique est reprise telle quelle.
    """
    merged = dict(values)
    for key, spec in (overrides or {}).items():
        if key not in merged:
            raise KeyError(f"écart de scénario sur une hypothèse inconnue : {key}")
        if not isinstance(spec, dict):
            raise TypeError(f"écart {key} : attendu un objet, reçu {spec!r}")
        if "value" in spec:
            raw_value = spec["value"]
            merged[key] = (
                float(raw_value) if isinstance(raw_value, int | float) else str(raw_value)
            )
        elif "multiply_by" in spec:
            facteur = value_of(merged, str(spec["multiply_by"]))
            merged[key] = value_of(merged, key) * facteur
        else:
            raise ValueError(f"écart {key} : il faut 'value' ou 'multiply_by', reçu {spec!r}")
    return merged


def load_scenario(
    name: str,
    assumptions_path: str | Path = ASSUMPTIONS_PATH,
    scenarios_dir: str | Path = SCENARIOS_DIR,
) -> Scenario:
    """Charge les hypothèses, y applique les écarts du scénario nommé."""
    raw = load_yaml(assumptions_path)
    scenario_file = Path(scenarios_dir) / f"{name}.yaml"
    spec = load_yaml(scenario_file)
    return Scenario(
        name=spec.get("nom", name),
        description=spec.get("description", ""),
        values=apply_overrides(assumption_values(raw), spec.get("overrides")),
    )


@dataclass(frozen=True, slots=True)
class SimConfig:
    """Paramètres de simulation : hypothèses lues, puis grandeurs dérivées.

    Toutes les durées sont en heures, toutes les masses en tonnes : le modèle ne
    convertit plus rien.
    """

    # --- hypothèses directes
    jours_par_an: float
    postes_minerai: int
    taille_navire_moyenne_t: float
    capacite_stockyard_t: float
    capacite_stock_usine_t: float
    nombre_de_rames: int
    charge_par_train_t: float
    politique_expedition: str  # pull | push, voir rail.politique_expedition
    duree_chargement_h: float
    duree_dechargement_h: float
    capacite_dri_t_par_an: float
    taux_utilisation_dri: float
    pellets_par_t_dri: float
    arrets_planifies_j: float
    arrets_planifies_nombre: int
    dispersion_arrivees: float
    cv_taille_navire: float
    cv_cadence: float
    cv_durees_rail: float
    fraction_stock_initial: float
    pas_h: float
    pas_echantillonnage_h: float
    graine_par_defaut: int

    # --- grandeurs dérivées
    heures_par_an: float
    heures_ouvrees_par_an: float
    cadence_dechargement_t_par_h: float
    duree_trajet_h: float
    debit_dri_t_par_h: float
    debit_pellets_t_par_h: float
    besoin_annuel_pellets_t: float
    navires_par_an: float
    intervalle_moyen_arrivees_h: float
    capacite_rail_t_par_h: float
    stock_initial_stockyard_t: float
    stock_initial_usine_t: float

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> SimConfig:
        """Construit la configuration d'un scénario : seul endroit où l'on calcule."""
        v = scenario.values
        jours_par_an = value_of(v, "simulation.jours_par_an")
        heures_par_an = jours_par_an * _HOURS_PER_DAY
        arrets_planifies_j = value_of(v, "usine.arrets_planifies_dri")
        heures_ouvrees = (jours_par_an - arrets_planifies_j) * _HOURS_PER_DAY

        capacite_dri = value_of(v, "usine.capacite_dri") * _MT
        taux_utilisation = value_of(v, "usine.taux_utilisation_dri")
        pellets_par_t = value_of(v, "usine.pellets_par_t_dri")
        # le taux d'utilisation porte sur l'année entière ; les arrêts planifiés
        # retirent ensuite leurs heures de la production
        debit_dri = capacite_dri * taux_utilisation / heures_par_an
        debit_pellets = debit_dri * pellets_par_t

        taille_navire = value_of(v, "navires_minerai.taille_moyenne")
        besoin_annuel = debit_pellets * heures_ouvrees
        navires_par_an = besoin_annuel / taille_navire

        nombre_de_rames = int(value_of(v, "rail.nombre_de_rames"))
        charge_par_train = value_of(v, "rail.charge_par_train")
        duree_trajet = value_of(v, "rail.distance_port_usine") / value_of(v, "rail.vitesse_moyenne")
        duree_chargement = value_of(v, "rail.duree_chargement_train")
        duree_dechargement = value_of(v, "rail.duree_dechargement_train")
        cycle_rame = duree_chargement + duree_dechargement + 2 * duree_trajet

        capacite_stockyard = value_of(v, "stockage_port.capacite_stockyard_pellets")
        capacite_usine = value_of(v, "usine.capacite_stock_pellets_usine")
        fraction_initiale = value_of(v, "simulation.fraction_stock_initial")

        politique = text_of(v, "rail.politique_expedition", EXPEDITION_POLICIES)
        if politique == "pull" and charge_par_train > capacite_usine:
            # sinon la rame attendrait indéfiniment une place qui ne peut pas exister
            raise ValueError(
                f"politique pull impossible : une charge de {charge_par_train:.0f} t ne tient "
                f"pas dans un stock usine de {capacite_usine:.0f} t"
            )

        return cls(
            jours_par_an=jours_par_an,
            postes_minerai=int(value_of(v, "navires_minerai.postes_minerai")),
            taille_navire_moyenne_t=taille_navire,
            capacite_stockyard_t=capacite_stockyard,
            capacite_stock_usine_t=capacite_usine,
            nombre_de_rames=nombre_de_rames,
            charge_par_train_t=charge_par_train,
            politique_expedition=politique,
            duree_chargement_h=duree_chargement,
            duree_dechargement_h=duree_dechargement,
            capacite_dri_t_par_an=capacite_dri,
            taux_utilisation_dri=taux_utilisation,
            pellets_par_t_dri=pellets_par_t,
            arrets_planifies_j=arrets_planifies_j,
            arrets_planifies_nombre=int(value_of(v, "simulation.arrets_planifies_nombre")),
            dispersion_arrivees=value_of(v, "simulation.dispersion_arrivees"),
            cv_taille_navire=value_of(v, "simulation.cv_taille_navire"),
            cv_cadence=value_of(v, "simulation.cv_cadence_dechargement"),
            cv_durees_rail=value_of(v, "simulation.cv_durees_rail"),
            fraction_stock_initial=fraction_initiale,
            pas_h=value_of(v, "simulation.pas_de_temps"),
            pas_echantillonnage_h=value_of(v, "simulation.pas_echantillonnage_stocks"),
            graine_par_defaut=int(value_of(v, "simulation.graine_par_defaut")),
            heures_par_an=heures_par_an,
            heures_ouvrees_par_an=heures_ouvrees,
            cadence_dechargement_t_par_h=(
                value_of(v, "navires_minerai.cadence_dechargement") / _HOURS_PER_DAY
            ),
            duree_trajet_h=duree_trajet,
            debit_dri_t_par_h=debit_dri,
            debit_pellets_t_par_h=debit_pellets,
            besoin_annuel_pellets_t=besoin_annuel,
            navires_par_an=navires_par_an,
            intervalle_moyen_arrivees_h=heures_par_an / navires_par_an,
            capacite_rail_t_par_h=nombre_de_rames * charge_par_train / cycle_rame,
            stock_initial_stockyard_t=capacite_stockyard * fraction_initiale,
            stock_initial_usine_t=capacite_usine * fraction_initiale,
        )

    def arrets_planifies_fenetres(self, years: float) -> list[tuple[float, float]]:
        """Fenêtres d'arrêt planifié du DRI, en heures depuis le début de la simulation.

        Les `arrets_planifies_dri` jours annuels sont découpés en `arrets_planifies_nombre`
        arrêts de durée égale, espacés régulièrement et répétés chaque année simulée.
        """
        if self.arrets_planifies_nombre <= 0 or self.arrets_planifies_j <= 0:
            return []
        duree = self.arrets_planifies_j * _HOURS_PER_DAY / self.arrets_planifies_nombre
        intervalle = self.heures_par_an / self.arrets_planifies_nombre
        fenetres: list[tuple[float, float]] = []
        annee = 0
        while annee * self.heures_par_an < years * self.heures_par_an:
            base = annee * self.heures_par_an
            for i in range(self.arrets_planifies_nombre):
                # placé au milieu de son intervalle : jamais à l'instant 0
                debut = base + (i + 0.5) * intervalle - duree / 2
                fenetres.append((debut, debut + duree))
            annee += 1
        return [(d, f) for d, f in fenetres if d < years * self.heures_par_an]
