# CLAUDE.md

Contexte permanent du projet. Lis-le avant toute tâche. Il est écrit pour une reprise
sans contexte : état réel, décisions déjà prises et leurs raisons, limites connues,
commandes, et les trois tâches suivantes.

## Le projet

Jumeau numérique du corridor **port de Djen Djen → rail → aciérie de Bellara (AQS) → export**,
construit uniquement sur des données publiques. Question centrale : le corridor peut-il absorber
un doublement de la production, et sinon, quel est le premier goulot ?

Étude indépendante à but de portfolio. Non affiliée à AQS ni à l'Entreprise Portuaire de Djen Djen.
Toute hypothèse doit être explicite et sourcée dans `config/assumptions.yaml`.

**Réponse provisoire de l'étape 4** : à arrivées régulières, le corridor absorbe le doublement
(production DRI à 99,9 % de la cible). Ce qui le limite n'est ni le poste de déchargement ni le
rail, mais l'**irrégularité des arrivées combinée à la capacité de stockage** — les deux
paramètres les moins sourcés du modèle. Voir « Limites connues ».

## État des étapes

| Étape | État | Où |
| --- | --- | --- |
| 1. Collecte du PDF de situation portuaire, 3×/jour | ✅ | `corridor.ingest.port_status` |
| 2. Parsing PDF → observations de navires | ✅ | `corridor.transform.parse_status` |
| 3. Observations → une ligne par escale | ✅ **non validé sur du réel** | `corridor.transform.build_escales` |
| 4. Simulation SimPy v1 + sensibilité | ✅ | `corridor.sim.*` |
| 5. ML, optimisation des stocks, tableau de bord | ⬜ | — |

`ruff check .` et `pytest -q` passent : **214 tests**, dont les cinq validations obligatoires de
la simulation (reproductibilité, conservation de la masse, production de référence, attente nulle
à arrivées régulières, une année simulée en moins de dix secondes — mesuré à ~65 ms).

### Étape 1 — collecte (terminée)

Archive le PDF horodaté en UTC dans `data/raw/port_status/`, sans jamais dupliquer un PDF
identique au précédent, et consigne chaque tentative dans `_manifest.csv` (`new`, `unchanged`,
`error`). Le manifeste est lui-même une donnée : il révèle la fréquence réelle de mise à jour
du port.

### Étape 2 — parsing (terminée)

`parse_pdf(path) -> list[VesselObservation]`. Une observation par navire et par instantané, avec
`source_time_utc` (heure du port), `fetched_at_utc` (nom du fichier), statut, cargaison
catégorisée, tonnage, et `long_stay` pour les navires à quai depuis plus de 60 jours.

### Étape 3 — escales (terminée, mais non validée sur données réelles)

`build_escales(observations) -> list[Escale]` et `build_from_pdfs(paths)`. Le départ n'est jamais
observé : il est encadré par `departure_min` / `departure_max`, et `departure_max is None` marque
une escale en cours. **Les transitions ne sont testées que sur des séquences synthétiques** : il
n'existe qu'un seul instantané réel (voir « Limites connues »).

### Étape 4 — simulation (terminée)

Modèle SimPy à cinq processus (`arrivals`, `vessel`, `rame`, `dri`, `monitor`), trois scénarios,
réplications en Parquet, et une analyse de sensibilité à deux dimensions dont la figure est celle
du README.

## Commandes

```bash
pip install -e ".[dev]"                       # environnement conda « corridor », Python 3.12
ruff check . && pytest -q                     # doivent passer avant tout commit

python -m corridor.ingest.port_status         # une collecte
python scripts/inspect_latest.py              # voir le contenu du dernier PDF

python -m corridor.sim.run --scenario baseline --years 1 --reps 50
python -m corridor.sim.run --scenario phase2 --years 1 --reps 50 --dispersion 1.0
python -m corridor.sim.sensitivity            # grille 5×5×2, 30 réplications : ~3 min 30
python -m corridor.sim.sensitivity --reps 5   # version rapide
```

Scénarios disponibles : `baseline`, `phase2`, `phase2_plus_stockage`.
Sorties : `data/sim/*.parquet` (ignoré par git, recalculable) et `docs/sensibilite_dri.png`
(versionné, c'est la figure du README).

## Décisions structurantes, et pourquoi

**Parsing (étape 2)**

- Les trois sections du PDF sont reconnues **par leur ligne d'en-tête**, pas par leur rang dans la
  page : une section vide (`Expected Arrivals (0)`) se réduit à son en-tête et ne produit rien.
- Le tonnage retenu est **le nombre porteur de l'unité**, où qu'il soit : `244 TCS (4469.262 Mt)`
  donne 4469,262 t. À défaut d'unité, un nombre en tête de cellule est un tonnage (`60236.54 Blé Dur`).
- La virgule est **toujours** une décimale : aucun séparateur de milliers n'a été observé dans la
  source. Si le port s'y met un jour, la règle casse silencieusement.
- `parse_header` **lève** si l'en-tête manque, au lieu de se rabattre sur la première date du
  document : une heure de mise à jour fausse contaminerait toute la table des escales.

**Escales (étape 3)**

- Les bornes du départ sont en **heure du port** (`source_time_utc`), pas en heure de collecte :
  c'est le port qui atteste la présence. Les durées (`wait_hours`, `berth_hours`) viennent des
  `event_time` publiés.
- `berth_hours` est une **borne basse** (calculée sur `departure_min`). La durée réelle vaut au
  plus `berth_hours + departure_uncertainty_hours`.
- `left_censored` (escale commencée au premier instantané, arrivée hors champ) est distingué de
  `direct_berth` (apparition à quai en cours de série). Sans cette distinction, les 12 navires du
  premier instantané seraient tous signalés comme anormaux.
- Une réapparition après absence est rattachée à la même escale si l'absence est courte (24 h), si
  le cycle n'a pas reculé et si la cargaison concorde. Rien n'est corrigé en silence : 13 codes
  d'anomalie documentés dans `ANOMALY_CODES`.

**Simulation (étape 4)**

- `taux_utilisation_dri` s'applique à **l'année entière**, pas aux seuls jours ouvrés : le débit
  horaire vaut `capacite_dri × taux_utilisation / heures_par_an`, et les arrêts planifiés retirent
  ensuite leurs heures. La cible annuelle est donc `capacité × utilisation × jours_ouvrés / 365`.
- La conservation de la masse a **trois** termes de sortie, pas deux :
  `stock_initial + déchargé = stockyard + stock_usine + en_transit + consommé`. Une rame en route
  détient jusqu'à `charge_par_train` tonnes qui n'appartiennent ni au port ni à l'usine.
- Les temps d'occupation sont **intégrés** (`TimeIntegral`) pour inclure ce qui est encore en cours
  à la fin de l'horizon. Les accumuler à la fin de l'état perdrait les immobilisations les plus
  longues, justement celles qui comptent.
- L'utilisation des rames ne compte que le **temps roulant** ; le temps bloqué devant une usine
  pleine est publié à part (`rame_blocked_h`) — ce n'est pas de l'utilisation, c'est un symptôme.
- **Politique d'expédition `pull` par défaut** : une rame réserve la place à l'usine avant d'aller
  charger, donc `rame_blocked_h` est nul par construction et le tampon reste au port. `push`
  (l'ancien comportement) est conservé pour comparaison. En arrivées irrégulières, `pull` perd
  moins de production que `push` (7,9 % contre 10,8 % en baseline) mais crée plus d'attente en rade.
- `baseline` est **volontairement régulier** (`dispersion_arrivees = 0`) : c'est le cas de
  référence qui isole la capacité du corridor de l'irrégularité de l'affrètement. L'irrégularité
  s'étudie avec `--dispersion` ou par la grille de sensibilité.
- Tous les aléas passent par `Sampler.sample(rng)` et un jeu `Samplers` : une version empirique
  alimentée par `build_escales` se substitue sans toucher au modèle (c'est la tâche 3 ci-dessous).
- Un scénario n'exprime que ses **écarts**, sous forme `{value: x}` ou `{multiply_by: autre.clé}`.
  La capacité DRI de la phase 2 pointe sur `phase_2.facteur_dri` : aucun chiffre n'y est recopié.

## Limites connues

À lire avant de croire un résultat.

1. **Un seul instantané réel** (`tests/fixtures/port_status_20260924T155825Z.pdf`). L'étape 3 n'a
   donc jamais tourné sur une vraie séquence : transitions, réapparitions et trous de collecte sont
   testés sur des séquences synthétiques construites en Python. `test_fixtures_reelles_invariants`
   balaie `tests/fixtures/*.pdf` par `glob` et se renforcera seul dès que d'autres PDF y seront
   déposés. **C'est le blocage principal du projet.**
2. **Les deux capacités de stockage sont de statut `inconnu`** et pilotent tout le résultat : de
   200 kt à 900 kt, la perte de production passe de 14,1 % à 5,8 % (baseline, arrivées de Poisson).
   Aucune conclusion chiffrée ne doit être avancée sans cette fourchette.
3. **`dispersion_arrivees` n'est pas mesurée** (statut estimé). C'est le second axe de la figure, et
   il n'a aucune source : il faudra l'estimer sur les escales réelles.
4. Dans la grille de sensibilité, la répartition port/usine suit les proportions des valeurs
   centrales. Aux totaux les plus bas, la part du port descend **sous son propre minimum publié** :
   la fourchette du total est plus large que ce que chaque borne autorise séparément.
5. **Hors périmètre v1** : exports, houle (`seuil_houle_arret` est inutilisé), surestaries et coûts,
   temps morts à quai (amarrage, ouverture des cales). L'occupation du poste est donc une borne basse.
6. `postes_minerai = 1` repose sur **une seule observation** (QW/7 en minerai, un navire en rade).
7. Le DRI s'arrête entièrement dès qu'il manque une heure de pellets : pas de marche dégradée.

## Structure du dépôt

```
src/corridor/ingest/     collecte (port_status.py)
src/corridor/transform/  parse_status.py, build_escales.py
src/corridor/sim/        config.py, samplers.py, model.py, run.py, sensitivity.py
config/assumptions.yaml  toutes les valeurs numériques du domaine
config/scenarios/        baseline, phase2, phase2_plus_stockage (écarts seulement)
scripts/                 outils d'inspection ponctuels
data/raw/port_status/    PDF horodatés UTC + _manifest.csv
data/sim/                sorties Parquet (git-ignoré, recalculable)
docs/                    figures versionnées
tests/                   pytest, fixtures PDF réelles
```

## Conventions

- Python 3.12, environnement conda `corridor`, installé en éditable (`pip install -e ".[dev]"`).
- Code et docstrings en **français**. Noms de variables en anglais quand c'est l'usage du domaine.
- `ruff check .` et `pytest -q` doivent passer avant tout commit.
- Tests hors ligne : aucun appel réseau, on utilise les PDF réels de `tests/fixtures/`.
- Fichiers bruts **jamais modifiés** : tout est recalculable depuis `data/raw/`.
- **Aucune valeur numérique du domaine dans le code** : tout vient de `assumptions.yaml`, et les
  grandeurs dérivées sont calculées dans `corridor.sim.config`.
- Statuts d'hypothèse : `connu`, `observé`, `annoncé`, `estimé`, `inconnu`, `technique`.
- Pas de dépendance lourde sans raison. Stockage en Parquet, requêtes en DuckDB.
- Une fonction = une responsabilité ; privilégier les fonctions pures, testables sans disque.

## Structure réelle du PDF de situation portuaire

En-tête : `Djen-Djen : 2026-09-24 16:58` = heure de mise à jour **du port** (heure locale,
Africa/Algiers, UTC+1). Distincte de l'heure de collecte, qui est dans le nom du fichier en UTC.

Trois sections, trois schémas différents, chacune avec sa ligne d'en-tête :

| Section | Colonnes |
| --- | --- |
| `Berthed Vessels (n)` | Dock, Vessel, Flag, Shiptype, Cargo, Last Port, Agent, Berthed |
| `Vessels at Anchorage (n)` | Vessel, Flag, Shiptype, Cargo, Last Port, Agent, Anchorage, Situation |
| `Expected Arrivals (n)` | Vessel, Flag, Shiptype, Cargo, Last Port, Agent, E.T.A |

Pièges observés sur des cas réels, tous couverts par un test :

- Cellules multi-lignes : `MARSHALL\nISLANDS`, `29348,76 MT MDF\nBOARD`, agents sur 3 lignes.
- Tonnages hétérogènes : `360,48Mt`, `27229.383 MT DIVERS`, `142533 MT IRON ORE IN BULK`,
  `244 TCS (4469.262 Mt)`, `60236.54 Blé Dur`, `EMBT 9500 MT GRIGNON D'OLIVE`.
- Une section peut être vide (`Expected Arrivals (0)`) : le tableau n'a alors que son en-tête.
- Séjours anormaux : un navire à quai depuis 7 mois (immobilisé, pas une escale). À signaler,
  jamais à inclure tel quel dans les statistiques de durée à quai.
- Casse variable d'un libellé à l'autre : `DIVERS` et `Divers` désignent la même cargaison.

## Trois prochaines tâches

**1. Constituer une vraie série d'instantanés et valider l'étape 3 dessus.** *(débloque tout le reste)*

Laisser tourner la collecte 3×/jour pendant au moins trois semaines, puis déposer les PDF dans
`tests/fixtures/` et faire tourner `build_from_pdfs` sur la série réelle.
*Réussi quand* : au moins 30 instantanés réels archivés ; au moins une escale complète observée de
l'annonce au départ (`departure_max is not None`) ; `test_fixtures_reelles_invariants` vert sur la
chaîne réelle ; au moins un test de transition rejoué sur du réel et non sur du synthétique ; le
taux d'anomalies par code publié dans le README.

**2. Sourcer les deux capacités de stockage.** *(retire la principale incertitude)*

Chercher des sources publiques pour le stockyard à pellets du port et le stock de l'aciérie
(rapports d'activité de l'EPJ, presse spécialisée, imagerie satellite pour l'emprise au sol).
*Réussi quand* : les deux hypothèses passent de `inconnu` à `estimé` ou `connu` avec une source
citée ; leur fourchette se resserre ; la grille de sensibilité est relancée et la bande de perte
plausible annoncée dans le README est réduite en conséquence.

**3. Brancher les samplers empiriques sur `build_escales`.** *(dépend de la tâche 1)*

Ajouter `Samplers.from_escales(escales)` qui tire avec remise dans les intervalles entre arrivées,
les tonnages et les cadences de déchargement réellement observés, et mesurer
`dispersion_arrivees` sur la série.
*Réussi quand* : le modèle tourne avec le jeu empirique **sans une ligne modifiée** dans
`model.py` ; `dispersion_arrivees` passe de `estimé` à `observé` avec sa valeur mesurée ; la
production de DRI simulée avec les lois empiriques est comparée à celle des lois paramétriques et
l'écart est commenté.

## Ce qu'il ne faut pas faire

- Inventer des chiffres sur AQS ou le port : soit une source publique, soit une hypothèse étiquetée.
- Écrire une valeur numérique du domaine dans le code plutôt que dans `assumptions.yaml`.
- Augmenter la fréquence de collecte au-delà de 3×/jour (respect de la source).
- Supprimer ou réécrire un PDF brut déjà archivé.
- Fabriquer de faux PDF de situation portuaire pour compléter les fixtures : une séquence
  synthétique se construit en Python, dans le fichier de test, jamais sur le disque.
- Annoncer un résultat chiffré sans rappeler que la dispersion et les capacités de stockage ne sont
  pas sourcées.
