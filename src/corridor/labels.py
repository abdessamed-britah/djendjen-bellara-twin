"""Libellés des figures, en français et en anglais.

Les figures du projet servent deux publics : le dépôt, en français, et l'article visé par
*Computers & Industrial Engineering*, en anglais. Plutôt que de dupliquer les scripts de
tracé ou d'écrire les libellés en dur, chaque figure demande ses textes ici, par clé, dans la
langue voulue, et chaque script écrit une version par langue (`chemin_langue`).

Règle : **une clé absente dans une langue fait lever**, au lieu de retomber en silence sur
l'autre langue. Une figure anglaise avec un libellé resté en français passerait inaperçue
jusqu'à la relecture de l'article.

Les figures antérieures à l'étape 6 n'ont qu'une version française ; elles passeront ici au
fil des révisions.
"""

from __future__ import annotations

from pathlib import Path

LANGUES = ("fr", "en")

LIBELLES: dict[str, dict[str, str]] = {
    # indicateurs
    "beta_appro": {"fr": "β approvisionnement (points)", "en": "supply service level β (points)"},
    "rupture_h_an": {"fr": "ruptures (h/an)", "en": "stock-out hours per year"},
    "attente_h": {"fr": "attente en rade (h)", "en": "anchorage wait (h)"},
    "blocage_h_an": {"fr": "blocage à quai (h/an)", "en": "berth blocked (h/yr)"},
    "arrets_dri": {"fr": "coût des arrêts DRI (M USD/an)", "en": "DRI stoppage cost (M USD/yr)"},
    "surestaries": {"fr": "surestaries (M USD/an)", "en": "demurrage (M USD/yr)"},
    "portage": {"fr": "portage du stock (M USD/an)", "en": "inventory carrying (M USD/yr)"},
    "total": {"fr": "coût total (M USD/an)", "en": "total cost (M USD/yr)"},
    # variantes du modèle
    "continu": {"fr": "consommation continue", "en": "continuous consumption"},
    "discret": {"fr": "arrêts discrets", "en": "discrete outages"},
    "permis": {"fr": "dépassements permis", "en": "overtaking allowed"},
    "interdits": {"fr": "dépassements interdits", "en": "no overtaking"},
    # leviers du plan factoriel
    "poste_base": {"fr": "second poste, stockage de base", "en": "second berth, base storage"},
    "poste_haut": {"fr": "second poste, stockage haut", "en": "second berth, high storage"},
    "stockage_1": {"fr": "stockage haut, 1 poste", "en": "high storage, 1 berth"},
    "stockage_2": {"fr": "stockage haut, 2 postes", "en": "high storage, 2 berths"},
    "les_deux": {"fr": "les deux leviers", "en": "both levers"},
    "interaction": {"fr": "interaction", "en": "interaction"},
    # titres et axes
    "titre_factoriel": {
        "fr": "Plan factoriel poste × stockage, phase 2, approvisionnement endogène",
        "en": "Berth × storage factorial design, phase 2, endogenous supply",
    },
    "sous_titre_factoriel": {
        "fr": "Différences appariées (avec le levier − sans), IC 95 %, régime stationnaire",
        "en": "Paired differences (with lever − without), 95% CI, steady state",
    },
    "effet": {"fr": "effet du levier", "en": "lever effect"},
    "titre_site": {
        "fr": "Chiffre du site, phase 2 : distribution sur l'incertitude des paramètres",
        "en": "Site figure, phase 2: distribution over parameter uncertainty",
    },
    "long_terme": {"fr": "long terme (16 ans)", "en": "long run (16 years)"},
    "une_annee": {"fr": "une année isolée", "en": "a single year"},
    "frequence": {"fr": "part des tirages", "en": "share of draws"},
    # étape 7 : point de commande adaptatif
    "cv_delai": {"fr": "dispersion du délai d'approvisionnement (cv)",
                 "en": "supply lead-time variability (cv)"},
    "s_optimal": {"fr": "point de commande optimal s* (jours)",
                  "en": "optimal reorder point s* (days)"},
    "titre_regle": {"fr": "La règle s*(cv), IC 95 % par bootstrap",
                    "en": "The rule s*(cv), 95% bootstrap CI"},
    "gain_adaptation": {"fr": "coût à s fixe − coût au meilleur s de la grille (M USD/an)",
                        "en": "cost at fixed s − cost at best grid s (M USD/yr)"},
    "titre_gain_adaptation": {"fr": "Ce que rapporte l'adaptation",
                              "en": "What adapting is worth"},
    "titre_site_adaptatif": {
        "fr": "Chiffre du site, phase 2, point de commande adapté à chaque tirage",
        "en": "Site figure, phase 2, reorder point adapted to each draw",
    },
    # étape 7 : politique consciente des arrêts
    "aveugle": {"fr": "(a) aveugle aux arrêts", "en": "(a) outage-blind"},
    "rythme_moyen": {"fr": "(b) commande au rythme moyen", "en": "(b) order at mean rate"},
    "date_de_reprise": {"fr": "(c) date de reprise connue", "en": "(c) restart date known"},
    "part_recuperee": {"fr": "ruptures dues aux arrêts récupérées (%)",
                       "en": "outage-driven stock-outs recovered (%)"},
    "delta_stock": {"fr": "stock physique moyen, écart à (a) (kt)",
                    "en": "mean physical stock, change vs (a) (kt)"},
    "delta_cout": {"fr": "coût total, écart à (a) (M USD/an)",
                   "en": "total cost, change vs (a) (M USD/yr)"},
    "titre_politique_arrets": {
        "fr": "Commander pendant les arrêts du four : trois politiques, arrêts discrets",
        "en": "Ordering during furnace outages: three policies, discrete outages",
    },
    # étape 8 : attribution des ruptures et règle sous contrainte de service
    "ruptures": {"fr": "début des épisodes de rupture", "en": "stock-out episode starts"},
    "heures_marche": {"fr": "heures de marche du four (référence)",
                      "en": "furnace running hours (reference)"},
    "episodes": {"fr": "épisodes", "en": "episodes"},
    "age_reprise": {"fr": "jours depuis la dernière reprise du four",
                    "en": "days since last furnace restart"},
    "part_cumulee": {"fr": "part cumulée", "en": "cumulative share"},
    "titre_attribution": {
        "fr": "Les ruptures suivent-elles les reprises du four ? Phase 2, s = 35 j",
        "en": "Do stock-outs follow furnace restarts? Phase 2, s = 35 days",
    },
    "s_service": {"fr": "plus petit s tenant β (jours)",
                  "en": "smallest s meeting β (days)"},
    "titre_service": {"fr": "Règle sous contrainte de service s_β(cv)",
                      "en": "Service-constrained rule s_β(cv)"},
    "titre_sensibilite_couts": {"fr": "s* à coût minimal, prix ± 50 %",
                                "en": "Cost-optimal s*, prices ± 50%"},
    "beta_cible": {"fr": "β cible", "en": "target β"},
    "titre_regle_service": {
        "fr": "Point de commande : contrainte de service contre optimum de coût, phase 2",
        "en": "Reorder point: service constraint versus cost optimum, phase 2",
    },
    "titre_adaptatif": {
        "fr": "Point de commande adaptatif au risque de délai, phase 2, régime stationnaire",
        "en": "Reorder point adapted to lead-time risk, phase 2, steady state",
    },
}


def libelle(cle: str, langue: str = "fr") -> str:
    """Le texte d'une clé dans une langue ; lève si la clé ou la langue manque."""
    if langue not in LANGUES:
        raise ValueError(f"langue inconnue : {langue!r} (connues : {LANGUES})")
    if cle not in LIBELLES:
        raise KeyError(f"libellé inconnu : {cle!r}")
    if langue not in LIBELLES[cle]:
        raise KeyError(f"libellé {cle!r} sans version {langue!r}")
    return LIBELLES[cle][langue]


def chemin_langue(chemin: str | Path, langue: str) -> Path:
    """`docs/x.png` en français, `docs/x_en.png` en anglais : une figure par langue."""
    chemin = Path(chemin)
    if langue == "fr":
        return chemin
    return chemin.with_name(f"{chemin.stem}_{langue}{chemin.suffix}")
