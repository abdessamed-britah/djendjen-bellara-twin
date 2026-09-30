# CLAUDE.md

Contexte permanent du projet, écrit pour une **reprise après plusieurs semaines sans aucun
contexte**. Dernière mise à jour : 2026-09-30. Lis-le en entier avant toute tâche : état réel de
chaque étape, décisions prises et leurs raisons, limites connues, commandes, puis les trois
prochaines tâches avec des critères de réussite vérifiables.

**Premier réflexe à la reprise** : `python scripts/health_check.py`. Il dit si la collecte a
continué de tourner pendant la pause, son taux d'erreur, et combien d'escales exploitables la
série contient désormais (voir « Reprise »).

## Le projet

Jumeau numérique du corridor **port de Djen Djen → rail → aciérie de Bellara (AQS) → export**,
construit uniquement sur des données publiques. Question centrale : le corridor peut-il absorber
un doublement de la production, et sinon, quel est le premier goulot ?

Étude indépendante à but de portfolio. Non affiliée à AQS ni à l'Entreprise Portuaire de Djen Djen.
Toute hypothèse doit être explicite et sourcée dans `config/assumptions.yaml`.

**Réponse provisoire** : à arrivées régulières, le corridor absorbe le doublement (production
DRI à 99,9 % de la cible, 4,011 Mt). Ce qui le limite n'est ni le rail, ni (de façon démontrée)
le poste de déchargement, mais l'**irrégularité des arrivées combinée à la capacité de
stockage** — les deux paramètres les moins sourcés du modèle. À arrivées de Poisson, la phase 2
perd de l'ordre de 12 % de sa production ; en argent, quelques millions à quelques dizaines de
millions d'USD par an, sur une hypothèse de marge étrangère au site (voir README).

## État des étapes

| Étape | État | Où |
| --- | --- | --- |
| 1. Collecte du PDF de situation portuaire, ~3×/jour | ✅ tourne seule | `corridor.ingest.port_status`, `.github/workflows/scrape.yml` |
| 2. Parsing PDF → observations de navires | ✅ | `corridor.transform.parse_status` |
| 3. Observations → une ligne par escale | ✅ code ; **validation partielle sur du réel** | `corridor.transform.build_escales` |
| 4. Simulation SimPy + sensibilité + coûts | ✅ | `corridor.sim.*`, `scripts/economic_summary.py` |
| 5. ML, optimisation des stocks, tableau de bord | ⬜ non commencée | — |

`ruff check .` et `pytest -q` passent : **310 tests**, ~15 à 30 s selon la machine (la limite
visée est ~30 s : voir tâche 1). Les cinq validations obligatoires de la simulation sont des
tests : reproductibilité, conservation de la masse, production de référence, attente nulle à
arrivées régulières, une année simulée en moins de dix secondes (~65 ms mesuré).

### Étape 1 — collecte

`scrape.yml` s'exécute ~3×/jour (cron 05:00, 13:00, 21:00 UTC, en pratique décalé de 2 à 6 h) :

1. `corridor.ingest.port_status` télécharge le PDF, calcule `content_sha256` (empreinte des
   tableaux, **sans** l'heure d'en-tête) et consigne la collecte dans `_manifest.csv` ;
2. `corridor.ingest.releases publish` envoie le PDF dans la release GitHub du mois
   (`raw-AAAA-MM`, créée si absente) et renseigne `release_asset` ;
3. `corridor.transform.observations_csv` parse le PDF et ajoute ses lignes à
   `data/clean/observations.csv` ;
4. seuls `_manifest.csv` et `observations.csv` sont commités par le workflow. **Les PDF bruts ne
   sont pas dans git** (`data/raw/**/*.pdf` est ignoré) : ils sont dans les releases, et
   `scripts/fetch_raw.py` les retélécharge pour tout reparser.

Statut du manifeste : `new` si la situation diffère de la dernière collecte réussie, `unchanged`
si `content_sha256` est identique, `error` si le téléchargement a échoué. Le PDF est archivé même
`unchanged` : son heure de génération est nouvelle, et c'est elle qui atteste que les navires
étaient encore listés. **Les statuts antérieurs au 2026-09-28 ont été calculés sur l'empreinte du
fichier** et valent tous `new` ; ils n'ont pas été réécrits. Pour ces lignes, comparer
`content_sha256` d'une ligne à l'autre.

**Le workflow commite sur `main` pendant que tu travailles** : fais `git pull --rebase` avant
tout `git push`, sinon le push est refusé.

Mesure au 2026-09-30 : 18 collectes réussies (24/09 → 30/09, ~3 par jour), 0 erreur, 315 lignes
d'observations.

### Étape 2 — parsing

`parse_pdf(path) -> list[VesselObservation]`. Une observation par navire et par instantané, avec
`source_time_utc` (heure de **génération** du PDF, lue dans l'en-tête), `fetched_at_utc` (nom du
fichier), statut, cargaison catégorisée, tonnage, et `long_stay` pour les navires à quai depuis
plus de 60 jours. `parse_pdf_bytes` fait la même chose depuis les octets, `content_fingerprint`
donne l'empreinte de la situation décrite.

### Étape 3 — escales

`build_escales(observations) -> list[Escale]`, `build_from_pdfs(paths)` et
`usable_escales(escales)` (escales terminées, hors longs séjours : le seul sous-ensemble à
utiliser pour des statistiques de durée). Le départ n'est jamais observé : il est encadré par
`departure_min` / `departure_max`, et `departure_max is None` marque une escale en cours.

**Ce qui a été mesuré sur la vraie série (2026-09-30, 315 observations)** : 26 escales, 8
terminées, 8 exploitables. Anomalies par escale : `left_censored` 14, `dock_changed` 4,
`eta_revised` 1, `cargo_changed` 1, les neuf autres codes 0. **Une seule escale est complète de
l'annonce au départ** (FALCON KIZUNA, cargaison « other » : 1 h d'attente, ≥ 22 h à quai) ; les
sept autres escales terminées étaient déjà en cours au premier instantané (`left_censored`) ou
sont arrivées directement à quai, donc sans attente mesurable. **Ce que cela ne prouve pas** :
`build_escales` tourne sans erreur et ses invariants tiennent, mais les sorties n'ont **pas été
confrontées une à une** à ce que le port publie, et `tests/fixtures/` ne contient toujours qu'un
seul PDF. Les transitions restent testées sur des séquences synthétiques.

### Étape 4 — simulation

Modèle SimPy à cinq processus (`arrivals`, `vessel`, `rame`, `dri`, `monitor`), quatre
scénarios, réplications en Parquet, analyse de sensibilité à deux dimensions (dispersion des
arrivées × capacité de stockage) dont la figure est celle du README, et conversion en coût.

Scénarios (`config/scenarios/`, écarts seulement) : `baseline` ; `phase2` (capacité DRI ×
`phase_2.facteur_dri`) ; `phase2_plus_stockage` (les deux stockages en haut de fourchette) ;
`phase2_plus_poste` (`postes_minerai` = 2).

Résultats principaux (détails, tableaux et mises en garde dans le README) :

- rail : doubler le parc de rames ne change rien à arrivées régulières et ne récupère que
  1,4 point de perte en Poisson (scénario abandonné) ;
- stockage : de 200 kt à 900 kt de capacité totale, la perte baseline en Poisson passe de
  14,1 % à 5,8 % ;
- politique `pull` contre `push` : un seul cas significatif sur six (baseline, Poisson,
  +0,059 ± 0,040 Mt) ;
- second poste (`phase2_plus_poste`, 30 réplications) : +0,129 ± 0,157 Mt en Poisson, 0,000 ±
  0,001 Mt à arrivées régulières : **non concluant** ;
- coût de la perte, politique `pull`, Poisson (`scripts/economic_summary.py`) : baseline ~4 M
  USD/an (1 à 10), phase 2 ~11 M USD/an (2 à 27), phase 2 + stockage ~5 M (1 à 13), phase 2 +
  second poste ~8 M (2 à 20). À arrivées régulières : moins de 1 M USD/an.

## Décisions structurantes, et pourquoi

**Parsing (étape 2)**

- Les trois sections du PDF sont reconnues **par leur ligne d'en-tête**, pas par leur rang dans la
  page : une section vide (`Expected Arrivals (0)`) se réduit à son en-tête et ne produit rien.
- Le tonnage retenu est **le nombre porteur de l'unité**, où qu'il soit : `244 TCS (4469.262 Mt)`
  donne 4469,262 t. À défaut d'unité, un nombre en tête de cellule est un tonnage (`60236.54 Blé Dur`).
- La virgule est **toujours** une décimale : aucun séparateur de milliers n'a été observé. Si le
  port s'y met un jour, la règle casse silencieusement.
- `parse_header` **lève** si l'en-tête manque, au lieu de se rabattre sur la première date du
  document : une heure d'accostage prise pour l'heure du document fausserait tous les instantanés.
- **L'en-tête date la génération du PDF, pas les données.** Il coïncide à la minute près avec
  l'heure de téléchargement. Le changement de situation se détecte donc sur le contenu
  (`content_fingerprint` : navire, statut, poste, cargaison, tonnage, heure d'événement,
  normalisés et triés), jamais sur le fichier ni sur l'en-tête.

**Escales (étape 3)**

- Les bornes du départ sont en `source_time_utc` (heure de génération du PDF, à la minute près
  l'heure de collecte). Les durées (`wait_hours`, `berth_hours`) viennent des `event_time`
  publiés, qui sont de vraies heures de mouvement.
- `berth_hours` est une **borne basse** (calculée sur `departure_min`) : la durée réelle vaut au
  plus `berth_hours + departure_uncertainty_hours`.
- `left_censored` (escale commencée au premier instantané, arrivée hors champ) est distingué de
  `direct_berth` (apparition à quai en cours de série). Sans cette distinction, les navires du
  premier instantané seraient tous signalés comme anormaux.
- Une réapparition après absence est rattachée à la même escale si l'absence est courte (24 h), si
  le cycle n'a pas reculé et si la cargaison concorde. Rien n'est corrigé en silence : 13 codes
  d'anomalie documentés dans `ANOMALY_CODES`.

**Simulation (étape 4)**

- `taux_utilisation_dri` s'applique à **l'année entière**, pas aux seuls jours ouvrés : le débit
  horaire vaut `capacite_dri × taux_utilisation / heures_par_an`, et les arrêts planifiés retirent
  ensuite leurs heures. La cible annuelle est `capacité × utilisation × jours_ouvrés / 365`.
- La conservation de la masse a **trois** termes de sortie :
  `stock_initial + déchargé = stockyard + stock_usine + en_transit + consommé`. Une rame en route
  détient jusqu'à `charge_par_train` tonnes qui n'appartiennent ni au port ni à l'usine.
- Les temps d'occupation sont **intégrés** (`TimeIntegral`) pour inclure ce qui est encore en cours
  à la fin de l'horizon : les accumuler à la fin de l'état perdrait les immobilisations les plus
  longues, justement celles qui comptent.
- L'utilisation des rames ne compte que le **temps roulant** ; le temps bloqué devant une usine
  pleine est publié à part (`rame_blocked_h`) : ce n'est pas de l'utilisation, c'est un symptôme.
- **Politique d'expédition `pull` par défaut** : une rame réserve la place à l'usine avant d'aller
  charger, donc `rame_blocked_h` est nul par construction. `push` (ancien comportement) est
  conservé pour comparaison. Le choix ne change la production de façon significative que dans un
  cas sur six ; seul `rame_blocked_h` diffère toujours, et c'est structurel.
- `baseline` est **volontairement régulier** (`dispersion_arrivees = 0`) : cas de référence qui
  isole la capacité du corridor de l'irrégularité de l'affrètement. L'irrégularité s'étudie avec
  `--dispersion` ou par la grille de sensibilité.
- Tous les aléas passent par `Sampler.sample(rng)` et un jeu `Samplers` : une version empirique
  alimentée par `build_escales` se substitue sans toucher au modèle (tâche 3).
- **Tout indicateur agrégé est une `Estimate`** (moyenne, écart-type, IC 95 % à ±1,96 σ/√n, dans
  `corridor.sim.stats` ; à n = 30, Student donnerait un intervalle ~4 % plus large). Deux
  configurations se comparent en **données appariées** : toutes partagent la même graine maîtresse,
  donc la réplication i de A et celle de B voient la même séquence aléatoire, et c'est l'intervalle
  de la *différence* qui tranche, pas le chevauchement des deux intervalles (critère trop
  conservateur). Dans `compare_scenarios.py`, la différence affichée est **A − B**.
- `waiting_tonnage_mean_t` mesure le **stock flottant** : tonnage en rade intégré dans le temps
  puis moyenné sur l'horizon.
- **L'hypothèse du tampon flottant est réfutée par le modèle** : on attendait qu'un fort tonnage
  en rade *réduise* la perte ; la corrélation sur les 50 points de la grille est **+0,87**
  (Spearman +0,91), positive aussi à stockage fixé et à dispersion fixée. Le minerai en rade est
  coincé en amont du goulot présumé, qui affame aussi le four. Un tampon n'aide que s'il est en
  aval de la contrainte. Corrélation entre sorties d'un même modèle, pas causalité démontrée.
- **Le second poste ne prouve pas que le poste soit le goulot.** La lecture « le poste unique est
  la contrainte » reste une hypothèse de mécanisme : `phase2_plus_poste` donne +0,129 ± 0,157 Mt
  en Poisson, aucun écart significatif à 30 réplications. Résultat non concluant, pas une
  réfutation ; ~200 réplications (`compare_scenarios.py --reps 200`) permettraient de trancher.
- Un scénario n'exprime que ses **écarts**, sous forme `{value: x}` ou `{multiply_by: autre.clé}`.
  Aucun chiffre n'y est recopié : corriger `phase_2.facteur_dri` corrige `phase2`.

**Coûts (`scripts/economic_summary.py`)**

- `couts.cout_journee_arret_dri` = 130 000 USD/jour, fourchette [40 000 ; 230 000], statut
  `estimé`. Dérivé de marges de fours à arc électrique **chinois** sur le rond à béton (7 à 44
  USD/t) : non spécifique à l'Algérie, **à corriger dès qu'une donnée locale existe**.
- Ce chiffre est celui d'une journée de la **baseline** (~5 800 t/jour, soit ~22 USD/t implicite).
  Le script convertit donc la perte en tonnes via cette marge par tonne, et non « perte ÷ 365 ×
  coût du jour » (t/jour × USD/jour, dimensionnellement faux) ni 130 000 USD/jour appliqué tel
  quel à la phase 2 (qui produit deux fois plus par jour, donc serait sous-évaluée de moitié).
- La fourchette cumule l'IC 95 % de la simulation et la fourchette de l'hypothèse (enveloppe
  volontairement large). Montants **arrondis au million** : plus fin serait une fausse précision.
  Ils servent à comparer des scénarios, pas à chiffrer un préjudice.

## Limites connues

À lire avant de croire un résultat.

1. **L'étape 3 n'est validée que partiellement sur du réel.** Elle tourne sur 18 instantanés
   (26 escales, 8 exploitables, une seule complète de l'annonce au départ) et ses invariants
   tiennent, mais rien n'a été confronté à une vérité terrain, et `tests/fixtures/` ne contient
   qu'un PDF : transitions, réapparitions et trous de collecte restent testés sur du synthétique.
   `test_fixtures_reelles_invariants` balaie `tests/fixtures/*.pdf` par `glob` et se renforcera
   seul dès que d'autres PDF y seront déposés. **C'est le blocage principal du projet.**
2. **La fraîcheur des données du port est inconnue.** L'en-tête date la génération du PDF, pas la
   mise à jour de ses tableaux. « Présent à t » veut dire « listé dans un document généré à t » :
   si le port tarde à retirer un navire parti, `departure_min` est trop tardif, sans signal dans
   la source. `content_sha256` révèle seulement si la situation *publiée* a changé.
3. **La cadence réelle de collecte est irrégulière.** ~3 collectes par jour en moyenne, mais
   GitHub exécute avec 2 à 6 h de retard et en saute parfois (le créneau de 13:00 le 28/09 n'a
   jamais tourné). Les trous élargissent d'autant la censure des départs (5 à 12 h mesurées sur
   les escales terminées). Ne jamais dépasser 3×/jour (respect de la source).
4. **Les deux capacités de stockage sont de statut `inconnu`** et pilotent tout le résultat : de
   200 kt à 900 kt, la perte de production passe de 14,1 % à 5,8 % (baseline, Poisson). Aucune
   conclusion chiffrée sans cette fourchette.
5. **`dispersion_arrivees` n'est pas mesurée** (statut `estimé`) : second axe de la figure, sans
   source. Il faut l'estimer sur les escales réelles (tâche 3).
6. **Le coût de la journée d'arrêt vient de marges chinoises** (statut `estimé`) et donne des
   ordres de grandeur seulement. Les chiffres en USD héritent en plus des limites 4 et 5.
7. Dans la grille de sensibilité, la répartition port/usine suit les proportions des valeurs
   centrales. Aux totaux les plus bas, la part du port descend **sous son propre minimum publié** :
   la fourchette du total est plus large que ce que chaque borne autorise séparément.
8. **30 réplications ne suffisent pas** pour ordonner les cases à forte dispersion de la grille
   (IC de ±5 points) ni pour trancher le test du second poste. Les tableaux pull/push du README
   ont été calculés à 50 réplications, la comparaison des postes et le tableau des coûts à 30.
9. **Hors périmètre v1** : exports, houle (`seuil_houle_arret` est inutilisé), surestaries,
   temps morts à quai (amarrage, ouverture des cales). L'occupation du poste est une borne basse.
10. `postes_minerai = 1` repose sur **une seule observation** (QW/7 en minerai, un navire en rade).
    Le scénario à 2 postes est le haut de cette fourchette, pas une situation observée.
11. Le DRI s'arrête entièrement dès qu'il manque une heure de pellets : pas de marche dégradée.
12. Le premier instantané de la série (24/09) contient un navire à quai depuis février (long
    séjour) : toujours l'exclure des durées (`usable_escales` le fait).

## Reprise après une longue pause

1. `git pull --rebase` : le workflow a commité des données pendant l'absence.
2. `python scripts/health_check.py` : collectes, taux d'erreur, dernière collecte réussie
   (alerte au-delà de 24 h, code de sortie 1), escales terminées et exploitables, anomalies.
   Sans dépendance à Claude.
3. Si la collecte s'est arrêtée : regarder l'onglet Actions de GitHub (GitHub peut désactiver
   les workflows planifiés d'un dépôt inactif depuis 60 jours : les réactiver si besoin),
   puis `python -m corridor.ingest.port_status` en local pour tester la source.
4. `pip install -e ".[dev]"` puis `ruff check . && pytest -q` avant de toucher quoi que ce soit.

## Commandes

Environnement conda `corridor`, Python 3.12. Sous Windows, le Python de ce projet est
`%USERPROFILE%\miniconda3\envs\corridor\python.exe` : `python` seul n'est pas dans le PATH de
Git Bash.

```bash
pip install -e ".[dev]"
ruff check . && pytest -q                     # doivent passer avant tout commit

python scripts/health_check.py                # bilan de la collecte et des escales
python -m corridor.ingest.port_status         # une collecte (PDF local + ligne de manifeste)
python scripts/inspect_latest.py              # voir le contenu du dernier PDF
python scripts/fetch_raw.py                   # retélécharger les PDF depuis les releases
python -m corridor.transform.observations_csv --rebuild   # régénérer observations.csv

python -m corridor.sim.run --scenario baseline --years 1 --reps 50
python -m corridor.sim.run --scenario phase2 --years 1 --reps 50 --dispersion 1.0 --politique push
python -m corridor.sim.sensitivity            # grille 5×5×2, 30 réplications : ~4 min
python -m corridor.sim.sensitivity --reps 5   # version rapide
python scripts/compare_scenarios.py --reps 50 # pull/push, régulier/Poisson, 1 contre 2 postes
python scripts/economic_summary.py --reps 30  # coût annuel de la perte, avec fourchette
```

Sorties : `data/sim/*.parquet` (ignoré par git, recalculable) et `docs/sensibilite_dri.png`
(versionné, c'est la figure du README).

## Structure du dépôt

```
src/corridor/ingest/     port_status.py (collecte), releases.py (archive GitHub)
src/corridor/transform/  parse_status.py, observations_csv.py, build_escales.py
src/corridor/sim/        config.py, samplers.py, model.py, run.py, sensitivity.py, stats.py
config/assumptions.yaml  toutes les valeurs numériques du domaine
config/scenarios/        baseline, phase2, phase2_plus_stockage, phase2_plus_poste
scripts/                 health_check, economic_summary, compare_scenarios,
                         inspect_latest, fetch_raw
data/raw/port_status/    _manifest.csv versionné ; PDF locaux git-ignorés
releases raw-AAAA-MM     les PDF bruts eux-mêmes, un asset par collecte
data/clean/              observations.csv : une ligne par navire et par collecte (versionné)
data/sim/                sorties Parquet (git-ignoré, recalculable)
docs/                    figures versionnées
tests/                   pytest, fixtures PDF réelles
```

## Conventions

- Python 3.12, code et docstrings en **français**. Noms de variables en anglais quand c'est
  l'usage du domaine.
- `ruff check .` et `pytest -q` doivent passer avant tout commit.
- Tests hors ligne : aucun appel réseau, on utilise les PDF réels de `tests/fixtures/`.
- Fichiers bruts **jamais modifiés** : tout est recalculable depuis `data/raw/`.
- **Aucune valeur numérique du domaine dans le code** : tout vient de `assumptions.yaml`, et les
  grandeurs dérivées sont calculées dans `corridor.sim.config`.
- Statuts d'hypothèse : `connu`, `observé`, `annoncé`, `estimé`, `inconnu`, `technique`.
- Pas de dépendance lourde sans raison. Stockage en Parquet, requêtes en DuckDB.
- Une fonction = une responsabilité ; privilégier les fonctions pures, testables sans disque.
- Un script de `scripts/` importe le paquet `corridor` et se teste par `importlib` (voir
  `tests/test_health_check.py`).

## Structure réelle du PDF de situation portuaire

En-tête : `Djen-Djen : 2026-09-24 16:58` = heure de **génération du document** par le serveur du
port (heure locale, Africa/Algiers, UTC+1). Elle coïncide à la minute près avec l'heure de
collecte, qui est dans le nom du fichier en UTC. Ce n'est **pas** l'heure de mise à jour des
données portuaires, que le PDF ne publie nulle part.

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

## Trois prochaines tâches, par ordre de priorité

**1. Terminer la validation de l'étape 3 sur la série réelle.** *(priorité 1 : elle débloque la
tâche 3 et donne sa crédibilité à tout le reste)*

Il reste : choisir 4 à 6 instantanés couvrant un cycle complet (annonce → rade → quai → départ,
le cas FALCON KIZUNA du 24-30/09 est un candidat) et les déposer dans `tests/fixtures/` (~580 Ko
et ~1,2 s de parsing par PDF : `python scripts/fetch_raw.py` les retélécharge, le reste demeure
dans les releases) ; écrire au moins un test de transition rejoué sur ces PDF ; comparer à la
main quelques escales au contenu du PDF (le port ne publie pas de vérité terrain, donc c'est
une lecture croisée, pas une mesure) ; publier dans le README le taux d'anomalies par code.
*Réussi quand* : (a) `pytest -q` vert avec ≥ 4 PDF réels dans `tests/fixtures/` et
`test_fixtures_reelles_invariants` exécuté sur eux ; (b) au moins un test de transition dont
l'entrée est une séquence de PDF réels ; (c) le README contient un tableau anomalies par code
sur la série réelle, avec le nombre d'escales et la date de calcul ; (d) au moins une escale
`finished` avec `eta`, `anchorage_time` et `berth_time` renseignés est vérifiée à la main
(FALCON KIZUNA compte déjà) ; (e) la durée totale de `pytest -q` reste sous ~30 s
(`pytest -q --durations=10` pour repérer les tests lents ; elle est à ~15-30 s aujourd'hui).

**2. Sourcer les deux capacités de stockage.** *(retire la principale incertitude)*

Chercher des sources publiques pour le stockyard à pellets du port (`stockage_port.
capacite_stockyard_pellets`, fourchette [150 000 ; 600 000] t) et le stock de l'aciérie
(`usine.capacite_stock_pellets_usine`, [50 000 ; 300 000] t) : rapports d'activité de l'EPJ,
presse spécialisée, imagerie satellite pour l'emprise au sol. Ne rien inventer : à défaut de
source, l'hypothèse reste `inconnu`.
*Réussi quand* : chacune des deux hypothèses passe de `inconnu` à `estimé` ou `connu` avec un
champ `source` renseigné (URL ou référence) ; leur fourchette est plus étroite qu'aujourd'hui ;
`python -m corridor.sim.sensitivity` a été relancée, `docs/sensibilite_dri.png` régénérée et la
bande de perte plausible du README resserrée ; `python scripts/economic_summary.py` a été
relancé et le tableau de coûts du README mis à jour.

**3. Brancher les samplers empiriques sur `build_escales`.** *(dépend de la tâche 1, et de
suffisamment d'escales : viser ≥ 20 escales exploitables à `health_check.py`, la série continue
de s'allonger pendant la pause)*

Ajouter `Samplers.from_escales(escales)` qui tire avec remise dans les intervalles entre
arrivées, les tonnages et les cadences de déchargement réellement observés (`usable_escales`
seulement, en rappelant que `berth_hours` est une borne basse), et mesurer `dispersion_arrivees`
sur la série.
*Réussi quand* : le modèle tourne avec le jeu empirique **sans une ligne modifiée** dans
`model.py` (`git diff` le prouve) ; `dispersion_arrivees` passe de `estimé` à `observé` dans
`assumptions.yaml`, avec sa valeur mesurée et le nombre d'escales utilisées ; la production de
DRI simulée avec les lois empiriques est comparée à celle des lois paramétriques en données
appariées, et l'écart est commenté dans le README ; les nouveaux tests passent hors ligne.

## Ce qu'il ne faut pas faire

- Inventer des chiffres sur AQS ou le port : soit une source publique, soit une hypothèse étiquetée.
- Écrire une valeur numérique du domaine dans le code plutôt que dans `assumptions.yaml`.
- Augmenter la fréquence de collecte au-delà de 3×/jour (respect de la source).
- Supprimer ou réécrire un PDF brut déjà archivé (dans une release ou en local).
- Recommiter des PDF bruts dans git en dehors de `tests/fixtures/` : ils vont dans les releases
  `raw-AAAA-MM`.
- Réécrire l'historique git pour en retirer les PDF déjà commités : ils y restent, c'est voulu.
- Décider qu'une situation a changé en comparant les fichiers ou l'en-tête : seul
  `content_sha256` le dit.
- Fabriquer de faux PDF de situation portuaire pour compléter les fixtures : une séquence
  synthétique se construit en Python, dans le fichier de test, jamais sur le disque.
- Annoncer un résultat chiffré, et *a fortiori* un montant en USD, sans rappeler que la
  dispersion des arrivées, les capacités de stockage et la marge unitaire ne sont pas sourcées.
- Présenter un résultat non significatif comme une preuve, dans un sens ou dans l'autre.
