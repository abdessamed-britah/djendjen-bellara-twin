# CLAUDE.md

Contexte permanent du projet, écrit pour une **reprise après plusieurs semaines sans aucun
contexte**. Dernière mise à jour : 2026-10-25. Lis-le en entier avant toute tâche : état réel de
chaque étape, décisions prises et leurs raisons, limites connues, commandes, puis les trois
prochaines tâches avec des critères de réussite vérifiables.

## Objectif permanent : un article dans *Computers & Industrial Engineering*

Ce projet vise une publication dans **CIE**, en **application paper**. Ce n'est pas un vernis
ajouté à la fin : c'est le critère qui arbitre ce qu'on construit, ce qu'on garde et ce qu'on
jette. Un *application paper* de CIE est jugé sur trois choses, et il doit les tenir **toutes
les trois** :

| Critère | Ce que la revue attend | Où le projet en est |
| --- | --- | --- |
| **Originalité** | Un apport qui n'existe pas déjà dans la littérature : donnée nouvelle, méthode nouvelle, ou combinaison nouvelle. Refaire une simulation de terminal portuaire n'est pas original. | Le jumeau est alimenté par une **collecte publique automatisée** (situation portuaire 3×/jour) et une **estimation de l'irrégularité des arrivées tirée de bilans de trafic publics**. C'est là qu'est l'apport : un modèle calibré sans accès interne au site. |
| **Généralité** | Un résultat transposable, pas une étude de cas qui ne vaut que pour un site. | C'est la raison d'être des **axes sans dimension** (c_a², τ = couverture en jours) et de la **frontière de service**. Tout résultat doit pouvoir s'énoncer pour « un corridor » avant de s'énoncer pour Djen Djen. |
| **Importance** | Une question qui compte pour quelqu'un : décision d'investissement, dimensionnement, politique publique. | L'arbitrage **lisser l'affrètement contre construire du stockage** (H4), et le fait que le poste de déchargement — l'investissement intuitif — ne soit pas le levier. |

**La règle, à appliquer sans exception.** Chaque résultat publié dans le README doit dire
**lequel des trois critères il sert**, explicitement. Un résultat qui n'en sert aucun est du
travail honnête mais hors sujet pour l'article : il reste dans le dépôt, il ne monte pas dans
le papier. Un résultat qui prétend servir les trois doit le justifier trois fois.

Trois conséquences pratiques :

- **l'honnêteté est un argument de publication, pas un handicap.** Un résultat non concluant
  clairement établi (le second poste) vaut mieux qu'un résultat positif fragile. Les réserves
  écrites partout dans ce dépôt sont ce qui rend l'article défendable en relecture ;
- **un chiffre appliqué doit être une plage**, assortie de sa méthode de propagation
  d'incertitude. Un point sans intervalle ne passe pas la relecture d'une revue d'ingénierie ;
- **tout doit être reproductible par un tiers sans accès privilégié** : données publiques,
  code versionné, figures régénérables par une commande.

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
stockage**. À arrivées de Poisson, la phase 2 perd de l'ordre de 10 % de sa production ; en
argent, quelques millions à quelques dizaines de millions d'USD par an, sur une hypothèse de
marge étrangère au site (voir README). L'irrégularité est désormais **estimée** (c_a² = 0,83
sur 16 trimestres de bilans publics), les deux capacités de stockage restent `inconnu`.

## État des étapes

| Étape | État | Où |
| --- | --- | --- |
| 1. Collecte du PDF de situation portuaire, ~3×/jour | ✅ tourne seule | `corridor.ingest.port_status`, `.github/workflows/scrape.yml` |
| 2. Parsing PDF → observations de navires | ✅ | `corridor.transform.parse_status` |
| 3. Observations → une ligne par escale | ✅ code ; **validation partielle sur du réel** | `corridor.transform.build_escales` |
| 4. Simulation SimPy + sensibilité + coûts | ✅ | `corridor.sim.*`, `scripts/economic_summary.py` |
| 5. ML, optimisation des stocks, tableau de bord | ⬜ non commencée | — |

`ruff check .` et `pytest -q` passent : **676 tests, ~35 s mesurés**. S'y ajoutent **17 tests
marqués `slow`**, exclus de `pytest -q` par `addopts` dans `pyproject.toml` et lancés par
`pytest -m slow -q` (~100 s) : la validation analytique du moteur de file, deux validations de
comportement de `beta_site.py`, les deux mesures de la limite 0 (gain des nombres communs,
inertie de la capacité) et **tous les rendus de figures**. Obligatoires avant tout
commit qui touche à `corridor.sim` ; la CI lance les deux suites, dans deux étapes séparées.
Les deux mesures triplent sur une machine chargée : la limite de 45 s se vérifie au repos,
pas pendant qu'une simulation tourne à côté.

**La limite de la boucle courte est de 45 s.** Elle remplace les ~30 s fixés quand la suite
comptait 310 tests. **Règle de révision : elle se renégocie quand le nombre de tests double**
(donc vers 1 200 tests), pas avant — autrement elle dérive à chaque ajout et ne contraint
plus rien.

**La boucle courte ne rend aucune vraie figure.** Un rendu matplotlib coûte une à deux
secondes et ne vérifie qu'une chose : que la bibliothèque sait tracer. Ce qui mérite un test
rapide, ce sont les **données** qui alimentent la figure — d'où une fonction `donnees_figure`
séparée du tracé dans `frontiere_service`, `h2_blocking` et `metamodele_frontiere`. Le rendu
lui-même est joué en `slow`, où il sert de test de non-régression du tracé. Les cinq
validations obligatoires de la simulation sont des tests : reproductibilité, conservation de la
masse, production de référence, attente nulle à arrivées régulières, une année simulée en moins
de dix secondes (~65 ms mesuré). S'y ajoute une **sixième, analytique** : l'attente simulée doit
contenir la valeur exacte de Pollaczek-Khinchine (`tests/test_analytic.py`).

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

Mesure au 2026-10-22 : 82 collectes réussies (24/09 → 22/10, ~3 par jour), 0 erreur, 1 138 lignes
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

**Ce qui a été mesuré sur la vraie série (2026-10-22, 1 138 observations)** : 49 escales, 32
terminées, 31 exploitables. Anomalies par escale : `left_censored` 14, `eta_revised` 8,
`dock_changed` 7, `cargo_changed` 4, les neuf autres codes 0.

Dont **4 escales minerai** (`cargo_category == iron_ore`), 3 terminées, toutes au poste QW/7,
qui ont servi à calibrer `navires_minerai.cadence_dechargement` — voir
`scripts/ore_calls_report.py` et la section « Premiers résultats observés » du README. La seule
attente en rade mesurable de la série minerai est celle de BERGE NIMBA : 183 h, et son accostage
tombe dans la fenêtre de départ de GH NIGHTINGALE au même poste. La relève est observée d'un
instantané au suivant (24/09 15:58 → 23:03) ; les heures d'entrée en rade et d'accostage
antérieures sont lues dans le PDF, pas observées par nous (les deux escales sont
`left_censored`). Ce n'est pas une file FIFO : NIMBA attendait depuis le 17/09 quand NIGHTINGALE
a accosté le 18/09.

**Ce que cela ne prouve pas** :
`build_escales` tourne sans erreur et ses invariants tiennent, mais les sorties n'ont **pas été
confrontées une à une** à ce que le port publie, et `tests/fixtures/` ne contient toujours qu'un
seul PDF. Les transitions restent testées sur des séquences synthétiques.

### Étape 4 — simulation

Modèle SimPy à cinq processus (`arrivals`, `vessel`, `rame`, `dri`, `monitor`), six
scénarios, réplications en Parquet, analyse de sensibilité à deux dimensions (dispersion des
arrivées × capacité de stockage) dont la figure est celle du README, et conversion en coût.

Scénarios (`config/scenarios/`, écarts seulement) : `baseline` ; `phase2` (capacité DRI ×
`phase_2.facteur_dri`) ; `phase2_plus_stockage` (les deux stockages en haut de fourchette) ;
`phase2_plus_poste` (`postes_minerai` = 2).

Résultats principaux (détails, tableaux et mises en garde dans le README) :

- rail : doubler le parc de rames ne change rien à arrivées régulières et ne récupère que
  1,4 point de perte en Poisson (scénario abandonné) ;
- cadence de déchargement : recalibrée à 24 000 t/jour (statut **observé**) sur les 3 escales
  minerai terminées de la série. Aucune conclusion ne change — l'écart apparié de production
  avec l'ancienne valeur estimée (25 000) n'est significatif nulle part ; seule l'occupation du
  poste monte de 1 à 3 points à arrivées régulières. Pertes en Poisson, 50 réplications :
  baseline 7,6 %, phase 2 9,7 %, phase 2 + stockage 5,1 % ;
- stockage : de 200 kt à 900 kt de capacité totale, la perte baseline en Poisson passe de
  14,1 % à 5,8 % — **mais c'est le stock initial qui le produit, pas la capacité : voir
  limite 0. Résultat à refaire** ;
- politique `pull` contre `push` : un seul cas significatif sur six (baseline, Poisson,
  +0,059 ± 0,040 Mt) ;
- blocage du poste (H2) : jusqu'à 3 087 h de quai immobilisé par an (baseline, Poisson,
  200 kt), pour une occupation réelle de 0,75 là où le déchargement seul en demande 0,34 ;
- second poste : **tranché à 200 réplications dans un plan factoriel 2×2** (poste × stockage,
  Poisson). Le poste ne récupère de production dans aucune configuration de stockage —
  +0,90 ± 1,33 point à stockage de base, −0,84 ± 1,32 à stockage haut, aucune interaction
  (−1,75 ± 1,88) — refait avec les flux séparés : +0,002 ± 0,003 et +0,000 ± 0,002, donc un
  zéro mesuré au centième de point. Le gain attribué au stockage (+4,98 ± 1,50, puis
  +2,57 ± 0,23) **vient du stock initial et non de la capacité : voir limite 0**. Le poste
  déplace en revanche
  la file de la rade vers le quai, de façon significative (voir décisions) ;
- **irrégularité des arrivées, première estimation** : c_a² = 0,83, IC bootstrap
  [0,21 ; 1,73], sur 16 trimestres de bilans de trafic du port (2022-2025). Soit des arrivées
  proches du régime de Poisson. Voir limite 5 pour ce que ce chiffre ne vaut pas ;
- **coût appliqué du manque à produire** (`economic_summary.py --mesure`, 200 tirages) :
  médiane **0,197 Mt/an [0 ; 0,931]**, soit **4 M USD/an [< 1 ; 37]** en gardant tous les
  trimestres ; **0,111 Mt/an [0 ; 0,598]**, soit **2 M USD/an [< 1 ; 24]** sans 2022 T3. La
  borne basse est nulle dans les deux cas : une année sur dix tient la cible. Voir limite 0
  pour ce que le tirage du stockage propage réellement ;
- **chiffre appliqué du site** (`scripts/beta_site.py`, scénario `phase2_mesure`, 500
  tirages) : taux de service β de long terme **93,2 % [86,8 ; 98,3]** en gardant tous les
  trimestres, **95,4 % [90,4 ; 98,2]** sans 2022 T3. Sur une seule année, 94,9 %
  [77,2 ; 100,0] et 96,8 % [84,8 ; 100,0]. **86 % de la dispersion annuelle vient de l'aléa
  d'une année, pas de l'incertitude des paramètres** : les deux lignes répondent à des
  questions différentes, dimensionner contre budgéter ;
- **métamodèle de la frontière** (`scripts/metamodele_frontiere.py`) : la forme guidée par
  la file τ* ≈ a + b·c_a²/(1−β)^c donne 3,7 + 0,35·c_a²/(1−β)^1,65, avec une erreur de
  transposition de 10,3 j (colonne de c_a² retirée) contre 9,3 j pour une régression souple à
  six termes. **Ni validée ni réfutée** : l'erreur est du même ordre que l'incohérence interne
  de la carte (28 % des paires consécutives reculent là où τ* devrait croître, de 6,8 j en
  moyenne). Résultat annexe net : ajouter un terme en nombre de postes ne gagne que 0,003 j ;
- **frontière cherchée au lieu d'être lue** (`scripts/frontiere_directe.py`) : dichotomie sur
  τ, nombres aléatoires communs, réplications adaptatives, monotonie imposée par régression
  isotone. **43 % du calcul de la grille (3 226 années contre 7 560) pour un bruit du même
  ordre** (25 % de paires reculantes contre 28 %, écart non significatif sur 24 paires) :
  plus efficace, pas plus précise. Le métamodèle réajusté dessus donne
  τ* = 7,7 + 0,18·c_a²/(1−β)^1,80 et 7,5 j d'erreur par colonne retirée (contre 10,3 j) —
  **le verdict reste « on ne peut pas trancher »**, 7,5 j valant toujours les 5,7 j de recul
  moyen. Deux diagnostics transposables : pente dβ/dτ = 0,0014/jour, d'où ~7 700 réplications
  par évaluation pour situer τ* à ± 1 jour, et **gain des nombres aléatoires communs mesuré à
  × 1,1 seulement** ;
- **frontière de service** (`scripts/frontiere_service.py`, phase 2, 60 réplications) : la
  couverture τ requise pour tenir β = 0,95 passe de ≤ 5 jours à arrivées régulières à ~59 jours
  à régime de Poisson, et devient hors d'atteinte (> 60 j) au-delà. β = 0,99 n'est atteignable
  qu'à c_a² = 0 (20,5 j). Les courbes à 1 et 2 postes sont **superposées** ;
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
- **Le poste de déchargement n'est pas la ressource contrainte : tranché, pas supposé.**
  Depuis les flux séparés, l'effet du second poste sur la production vaut **+0,002 ± 0,003
  point** à stockage de base et **+0,000 ± 0,002** à stockage haut : non plus « non
  significatif à ±1,3 point », mais un zéro établi au centième de point. Plan
  factoriel 2×2 (poste × stockage) en Poisson, 200 réplications appariées
  (`poste_stockage_factoriel.py`). Le second poste ne récupère de production **dans aucune des
  deux configurations de stockage** : +0,90 ± 1,33 point à stockage de base, −0,84 ± 1,32 à
  stockage haut, et aucune interaction (−1,75 ± 1,88). Les deux leviers ensemble (+4,13 ± 1,50)
  ne font pas mieux que le stockage seul (+4,98 ± 1,50). L'ancien résultat « non concluant » à
  30 réplications est donc clos, et dans le sens négatif. **Refait avec les flux séparés**
  (2026-10-25) : l'effet du poste tombe à +0,002 ± 0,003 et +0,000 ± 0,002, un zéro au
  centième de point au lieu d'un « non significatif » à ±1,3. Le gain du stockage, lui, est
  un artefact de stock initial (limite 0).
- **Le second poste déplace la file, il ne la supprime pas.** Les trois indicateurs de
  congestion bougent significativement quand on l'ajoute : l'attente en rade baisse
  (−59,7 ± 49,3 h à stockage de base, −120,3 ± 32,0 h à stockage haut) pendant que le blocage
  **par poste** monte (+1 277 ± 242 h et +486 ± 201 h) et que l'occupation effective baisse. Les
  navires accostent plus tôt et attendent à quai au lieu d'attendre en rade. Comme ce qui
  plafonne la production est le débit absorbable en aval, déplacer la file ne produit rien de
  plus. C'est aussi un garde-fou de lecture : **ne jamais conclure d'une baisse d'attente en
  rade qu'un goulot a été levé.**
- **Le moteur de file est validé contre une formule exacte.** `corridor.sim.analytic` donne
  Pollaczek-Khinchine (M/G/1, exacte) et Kingman (G/G/1, approchée). Le scénario
  `baseline_sans_blocage` porte les deux stockages à 10^9 t pour que la durée d'occupation du
  quai vaille exactement tonnage ÷ cadence — c'est l'hypothèse d'indépendance du service
  qu'exige la formule. Résultat : 37,96 ± 1,40 h simulées contre 38,53 h théoriques, et
  l'occupation tombe sur ρ = 0,3401. **Ne vaut que pour la file devant le poste** : rien n'est
  dit du rail, du four ni des stocks.
- **Une validation stationnaire se mesure sur un horizon long.** À 1 an le même test échoue
  (biais de −9,4 %), non par défaut du modèle mais parce que le système démarre à vide et que
  les navires encore en rade à l'horizon sont exclus de `waits_h`. Avec ~21 navires par an le
  transitoire domine. Biais sur 20 graines × 200 réplications : −9,41 % (1 an), −4,21 % (2),
  −1,84 % (4), −1,41 % (8), −0,95 % (16). 8 ans retenu : au-delà, l'IC se resserre plus vite que
  le biais ne décroît et le test redevient fragile sans rien valider de plus.
- **`berth_blocked_h` sépare le service du blocage.** Les heures où un navire tient le quai sans
  décharger (stockyard plein) sont comprises dans `berth_occupancy` mais ne sont pas du service.
  Intégrées comme les autres temps d'occupation, donc un navire encore bloqué à l'horizon compte.
- **Le contre-test observé de H2 ne trouve rien, et c'est un résultat.** Le signal
  observable de H2 serait un minéralier attendant à poste vide. Sur la seule attente
  mesurable de la série (BERGE NIMBA, 183,2 h), `ore_calls_report.py` compte **0,0 h** de
  poste démontré libre : 139,2 h de poste occupé, 5,2 h dans la fenêtre de départ du navire
  précédent et 38,8 h avant le premier instantané, où l'occupation est inconnue. Rien ne
  contredit donc l'idée que le poste est pris dès qu'un navire attend. Le calcul compte
  **tout** navire au même poste, pas seulement les minéraliers : QW/7 reçoit aussi du
  non-minerai, et l'ignorer gonflerait le signal.
- **H2 est soutenue : ce qui sature le poste, c'est le stockage plein, pas le déchargement.**
  Avec E[S_eff] = E[S] + B/N, l'écart médian de Kingman à la simulation passe d'un facteur 2,8×
  à 1,4× sur 39 cases de la grille, et la correction est d'autant plus forte que le blocage est
  grand (200 kt : 6,3× → 1,3× ; 900 kt : 2,0× → 1,7×). Cela explique que doubler le poste
  n'apporte rien : deux quais bloqués par un stockyard plein ne valent pas mieux qu'un seul.
  **Mais la prédiction que H2 en tirait est fausse** : on en déduisait que le second poste
  paierait si le stockage suivait, et le plan factoriel montre que non (voir ci-dessus). H2
  décrit correctement la charge du quai, elle ne prédit pas correctement l'effet des leviers.
  **Cohérence interne, pas validation indépendante** : ρ_eff est calculé à partir de la
  simulation qu'il prédit. Voir limite 14.
- **`cadence_dechargement` est une cadence apparente sur l'occupation du poste**, mesurée
  tonnage publié ÷ séjour à quai, donc amarrage, temps morts et attente de rames compris. Ce
  n'est pas un débit de grue, et c'est bien ce que le modèle consomme (le poste reste occupé
  pendant toute la durée du déchargement, pauses incluses). Chaque escale donne une fourchette
  et non un point, parce que le départ n'est jamais observé : borne haute sur `departure_min`,
  borne basse sur `departure_max`.
- **La synthèse se lit sur deux axes sans dimension**, pour être transposable : c_a² (carré du
  coefficient de variation des intervalles entre arrivées, que `dispersion_arrivees` **est**
  directement) et τ = (K_P + K_U)/D, la couverture de stock en jours de consommation. Un stock
  en kilotonnes ne se compare pas d'un site à l'autre, une couverture en jours si. Ce qui ne se
  transpose pas est la charge du poste ρ = λ·E[S]/c, d'où une courbe par nombre de postes.
- **H4 ne publie pas un montant, elle publie un seuil de rapport.** Sur la frontière
  β = 0,95, lisser l'affrètement et construire du stockage s'échangent. Les deux coûts
  (`prime_lissage_affretement`, `cout_stockage_annualise`) sont des valeurs de travail sans
  source, donc le montant du minimum ne veut rien dire. Ce qui en a : seul le **rapport** des
  deux coûts déplace l'optimum (multiplier les deux par une constante ne change rien), et il
  ne prend que quatre positions sur cinq ordres de grandeur. Au-delà d'un rapport de ~0,07, le
  moins cher est de lisser ; les hypothèses centrales donnent 0,080, donc tout juste au-delà.
  **Ne jamais citer un montant de cette section.**
- Un scénario n'exprime que ses **écarts**, sous trois formes : `{value: x}`,
  `{multiply_by: autre.clé}` et `{copy_from: autre.clé}`. Aucun chiffre n'y est recopié :
  corriger `phase_2.facteur_dri` corrige `phase2`, et réviser
  `dispersion_arrivees_observee` corrige `phase2_mesure`. `copy_from` a été ajouté pour ce
  dernier cas : écrire `value: 0.91` aurait figé une estimation qui va bouger.
- **Avant de conclure qu'un modèle échoue, mesurer le bruit des données qui le jugent.**
  Le métamodèle de la frontière semblait réfuté (erreur de 10,3 j sur 16 j de dispersion)
  jusqu'à ce qu'on mesure l'incohérence interne de la carte : τ* doit croître avec c_a², et
  28 % des paires consécutives reculent, de 6,8 j en moyenne. L'erreur du modèle est donc du
  même ordre que l'imprécision de sa cible, et la bonne conclusion est « on ne peut pas
  trancher », pas « la formule est fausse ». Ce diagnostic ne coûte rien et ne suppose rien :
  il n'utilise qu'une monotonie physiquement obligatoire.
- **Quand une méthode échoue, mesurer ce qui la limite avant d'en essayer une autre.** La
  frontière par grille était floue ; la chercher par dichotomie avec nombres aléatoires
  communs et réplications adaptatives était la réponse évidente, et elle n'a presque rien
  donné. Le diagnostic de pente dit pourquoi, et il valait pour les deux méthodes depuis le
  début : près de la frontière, β ne monte que de ~0,1 point par jour de couverture, donc
  situer τ* au jour près demande de résoudre β au millième — des milliers de réplications par
  évaluation. **La difficulté est dans la platitude de β(τ), pas dans la méthode.** Corollaire
  à ne pas oublier : les nombres aléatoires communs ne gagnent ici que × 1,1, *mesuré*, parce
  que changer la capacité de stockage change la trajectoire et décorrèle les deux exécutions
  malgré la graine partagée. Partager une graine ne suffit pas, il faut que les deux systèmes
  consomment les aléas dans le même ordre.
- **Lisser une contradiction ne la supprime pas.** La frontière directe est publiée après
  régression isotone : relue telle quelle, elle a 0 % de reculs et un bruit nul *par
  construction*. La colonne `tau_brut` conserve les valeurs d'avant correction, et c'est elle
  qui mesure le bruit et juge le métamodèle. Même piège partout où l'on nettoie des données
  avant de s'en servir pour juger un modèle.
- **Un générateur par source d'aléa, dérivé de la graine de réplication.**
  `corridor.sim.samplers.Streams` en donne un par source (`intervalle`, `taille_navire`,
  `cadence_dechargement`, `rail`, une sous-série par rame). Les arrêts planifiés n'en ont pas :
  ils sont déterministes. La cadence de déchargement est tirée **à l'arrivée du navire**, pas à
  son accostage, sans quoi elle dépendrait de l'ordre d'accostage et donc de la congestion.
  Conséquence observable et testée : deux configurations ne différant que par une capacité
  voient **la même séquence d'arrivées**, le navire n ayant la même taille et la même cadence.
  **Le gain mesuré est de ×7 sur les différences appariées** (contre ×1,1 avec un générateur
  unique partagé, où changer une capacité décalait toute la suite des tirages). C'est ce qui a
  fait apparaître le zéro de la limite 0 : avec ±1,3 point de bruit, il passait pour un
  « non significatif ».
  **Ce gain ne sert pas une recherche de racine contre une cible fixe** (`frontiere_directe`) :
  la variance de `β − constante` est celle de β, et aucun appariement ne la réduit. Il sert les
  comparaisons entre deux configurations, donc tout le reste.
- **Le chiffre appliqué est une distribution, jamais un point.** `beta_site.py` tire c_a²
  dans son échantillon bootstrap et chaque capacité de stockage dans sa propre fourchette
  (indépendamment : tirer le total ferait descendre la part du port sous son minimum
  publié). Deux variantes toujours publiées côte à côte, avec et sans le trimestre anormal
  2022 T3 — leur écart *est* la mesure de fragilité du résultat.
- **Une année et le long terme ne sont pas le même chiffre.** À une réplication par tirage,
  86 % de la dispersion de β vient de l'aléa annuel et non des paramètres. L'intervalle
  d'une année mesure donc surtout la variabilité intrinsèque du corridor ; celui de long
  terme (plusieurs années moyennées par tirage) mesure ce que le projet ignore. Ne jamais
  présenter l'un pour l'autre.

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

0. **LA CAPACITÉ DE STOCKAGE N'EST PAS UN LEVIER DANS CE MODÈLE, et tout ce qui est publié
   là-dessus est à refaire.** Mesuré le 2026-10-25, après la séparation des flux aléatoires :
   à **stock initial figé**, multiplier les deux capacités par 12 (τ de 13 à 160 jours) change
   la production de moins de **0,002 %** — quelques dizaines de tonnes sur 3,7 Mt — aux
   horizons de 1, 4 et 12 ans. Le « gain du stockage » de +2,57 points vient **entièrement du
   stock initial**, que les scénarios dérivent de la capacité via `fraction_stock_initial`
   (décomposition : capacité seule +0,000 %, stock initial seul +3,438 %, les deux +3,439 %).
   **Mécanisme** : quand le stockyard est plein, le navire garde sa cargaison à quai ou en
   rade, et ce tampon flottant n'a aucun plafond (ni surestaries, ni déroutement — limite 9).
   Le four ne s'arrête que s'il n'y a plus de minerai nulle part, ce qui dépend des arrivées.
   **Les silos déplacent la file, exactement comme le second poste** — le garde-fou était déjà
   écrit dans ce fichier, il n'avait pas été appliqué au stockage.
   **Conséquence** : sont invalidés le gain du stockage du plan factoriel, la bande de perte
   de la grille de sensibilité, et **l'axe τ de la frontière de service**, donc aussi H4, le
   métamodèle et la frontière directe. Verrouillé par
   `tests/test_sim_model.py::test_la_capacite_de_stockage_ne_change_pas_la_production` : si ce
   test tombe, c'est que le modèle plafonne enfin le tampon flottant, et il faut tout refaire.
   **C'est le premier chantier du projet, avant toute nouvelle analyse.**

1. **L'étape 3 n'est validée que partiellement sur du réel.** Elle tourne sur 82 instantanés
   (49 escales, 31 exploitables, dont 4 escales minerai) et ses invariants
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
4. **Les deux capacités de stockage sont de statut `inconnu`.** On lisait « de 200 kt à
   900 kt, la perte passe de 14,1 % à 5,8 % » ; la limite 0 montre que cet écart vient du
   stock initial couplé à la capacité, pas de la capacité. Les deux hypothèses restent non
   sourcées, mais elles ne pilotent **pas** le résultat de la façon annoncée.
5. **`dispersion_arrivees` a une première estimation, pas une mesure.**
   `dispersion_arrivees_observee` = 0,91 (c_a² = 0,83, IC bootstrap [0,21 ; 1,73]), obtenue sur
   16 trimestres de bilans de trafic du port, 2022-2025 (`scripts/arrival_dispersion.py`).
   Quatre réserves : les variations de consommation de l'aciérie s'y mêlent et poussent à la
   **surestimation** ; 16 trimestres font une petite série ; la fenêtre trimestrielle lisse les
   rafales de quelques jours et pousse à la **sous-estimation** ; le nombre de navires est un
   tonnage divisé par une taille moyenne, pas un comptage. Un seul trimestre (2022 T3, 606 t)
   fait passer I de 0,83 à 0,45 selon qu'on le garde ou non. La série de situations portuaires
   ne contient toujours que 4 arrivées minerai : elle ne permettra pas de trancher avant 2027.
   **`simulation.dispersion_arrivees` reste à 0** : c'est le cas de référence régulier du
   scénario `baseline`, et le changer transformerait en silence tous les résultats publiés.
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
13. **La cadence observée repose sur 3 navires**, tous au poste QW/7 et tous de 139 à 143 kt.
    Rien ne dit ce qu'elle vaudrait ailleurs ou sur un autre gabarit, et rien ne prouve que le
    tonnage publié ait été déchargé en entier à Djen Djen (s'il reste du fret à bord, la cadence
    est surestimée). La dispersion inter-navires (`cv_cadence_dechargement` = 0,15) reste
    `estimé` : les 3 centres mesurés sont à ± 2,5 % l'un de l'autre, bien moins dispersés, mais
    3 observations ne suffisent pas à resserrer ce coefficient.
14. **H2 est une cohérence interne, pas une preuve de mécanisme.** ρ_eff = λ·(E[S] + B/N) est
    calculé à partir de la simulation qu'il sert à prédire : il lui emprunte de l'information.
    La correction recentre l'erreur sans la supprimer — en phase 2 la formule corrigée
    *sur*estime désormais l'attente (×0,51 à 200 kt), en baseline à fort stockage elle la
    sous-estime encore (×1,62). Et aucune case de la grille n'est sans blocage : même à 900 kt,
    haut des deux fourchettes publiées, la baseline perd 1 013 h de quai par an. « Stockage
    faible » est donc relatif. Enfin c_s² reste celui du service sans blocage, faute de mesure
    de la variabilité du service effectif. La seule validation qui ne doive rien à la simulation
    est celle de `tests/test_analytic.py`.
15. **La validation analytique ne tourne pas dans `pytest -q`.** Elle est marquée `slow` et
    exclue par défaut, parce que 200 réplications × 8 ans coûtent 6 s et faisaient sortir la
    suite de son budget. Conséquence à connaître : une régression du moteur de file **ne sera
    pas vue** par la boucle courte. Lancer `pytest -m slow -q` avant tout commit touchant à
    `corridor.sim` ; la CI le fait dans une étape dédiée, et `scripts/hooks/pre-commit` le
    fait en local dès que `src/corridor/sim/` est à l'index — à installer une fois par
    `git config core.hooksPath scripts/hooks`. **Il ne se contourne jamais** (voir « Ce qu'il
    ne faut pas faire ») : c'est le seul filet local du moteur de file.
    Ne pas baisser `VALIDATION_YEARS` pour gagner
    du temps : à 4 ans le biais d'horizon fini passe de −1,4 % à −1,8 % et le test se
    rapproche de son seuil d'échec pour rien.

16. **Aucune formule ne remplace encore la carte de frontière.** Le métamodèle n'est ni
    validé ni réfuté : son erreur de transposition (10,3 j) est du même ordre que le bruit de
    la carte qui le juge (6,8 j de recul moyen là où τ* devrait croître, sur 28 % des paires).
    **Chercher τ* directement par dichotomie a été essayé et ne suffit pas** : le verdict
    reste le même (7,5 j d'erreur contre 5,7 j de bruit), pour 43 % du calcul. Le coût de
    trancher est maintenant chiffré : ~7 700 réplications par évaluation, contre 10 à 60
    aujourd'hui. En attendant, lire la frontière demande de rejouer `frontiere_service.py`
    ou `frontiere_directe.py`.
17. **La frontière de service est tracée sur des moyennes.** β a une demi-largeur d'IC de
    ±0,01 à ±0,03 selon les cases, la grille fait 7 × 9 points interpolés linéairement, et les
    contours sont tirés des moyennes sans lissage : la position exacte d'une courbe vaut à
    quelques jours de couverture près. Ne pas lire « 27,8 jours » comme une spécification. Et
    surtout, **la position du site sur l'axe horizontal est inconnue** (limite 5) : la carte dit
    quelle mesure trancherait, elle ne tranche pas pour Djen Djen.

## Reprise après une longue pause

1. `git pull --rebase` : le workflow a commité des données pendant l'absence.
2. `python scripts/health_check.py` : collectes, taux d'erreur, dernière collecte réussie
   (alerte au-delà de 24 h, code de sortie 1), escales terminées et exploitables, anomalies.
   Sans dépendance à Claude.
3. Si la collecte s'est arrêtée : regarder l'onglet Actions de GitHub (GitHub peut désactiver
   les workflows planifiés d'un dépôt inactif depuis 60 jours : les réactiver si besoin),
   puis `python -m corridor.ingest.port_status` en local pour tester la source.
4. `pip install -e ".[dev]"` puis `ruff check . && pytest -q && pytest -m slow -q` avant de
   toucher quoi que ce soit (~35 s et ~100 s).

## Commandes

Environnement conda `corridor`, Python 3.12. Sous Windows, le Python de ce projet est
`%USERPROFILE%\miniconda3\envs\corridor\python.exe` : `python` seul n'est pas dans le PATH de
Git Bash.

```bash
pip install -e ".[dev]"
ruff check . && pytest -q                     # doivent passer avant tout commit
pytest -m slow -q                             # validation analytique (~6 s), exclue de pytest -q

python scripts/health_check.py                # bilan de la collecte et des escales
python -m corridor.ingest.port_stats          # archive les bilans de trafic 2022-2025 (ponctuel)
python scripts/arrival_dispersion.py          # c_a² estimé sur 16 trimestres
python scripts/ore_calls_report.py            # escales minerai : cadences, attentes, signal H2
git config core.hooksPath scripts/hooks       # hook pre-commit : pytest -m slow si sim/ touché
python -m corridor.ingest.port_status         # une collecte (PDF local + ligne de manifeste)
python scripts/inspect_latest.py              # voir le contenu du dernier PDF
python scripts/fetch_raw.py                   # retélécharger les PDF depuis les releases
python -m corridor.transform.observations_csv --rebuild   # régénérer observations.csv

python -m corridor.sim.run --scenario baseline --years 1 --reps 50
python -m corridor.sim.run --scenario phase2 --years 1 --reps 50 --dispersion 1.0 --politique push
python -m corridor.sim.sensitivity            # grille 5×5×2, 30 réplications : ~4 min
python scripts/h2_blocking.py --reps 30       # hypothèse H2 : blocage du poste (~5 min)
python scripts/poste_stockage_factoriel.py    # plan 2x2 poste x stockage, 200 rép. (~3 min)
python scripts/frontiere_service.py --reps 60 # frontière de service (c_a² x τ) : ~25 min
python scripts/cout_regularite.py             # H4 : lisser l'affrètement ou stocker ?
python scripts/beta_site.py --tirages 500     # chiffre appliqué : distribution de β (~8 min)
python scripts/metamodele_frontiere.py        # formule contre carte : verdict et erreurs
python scripts/frontiere_directe.py           # frontière par dichotomie (~30 min)
python scripts/metamodele_frontiere.py --directe   # la même formule, sur cette frontière
python scripts/frontiere_service.py --from-parquet data/sim/frontiere_service.parquet
                                              # retrace la figure sans resimuler
python -m corridor.sim.sensitivity --reps 5   # version rapide
python scripts/compare_scenarios.py --reps 50 # pull/push, régulier/Poisson, 1 contre 2 postes
python scripts/economic_summary.py --reps 30  # coût annuel de la perte, avec fourchette
python scripts/economic_summary.py --mesure   # chiffre appliqué : le manque à produire en plage
```

Sorties : `data/sim/*.parquet` (ignoré par git, recalculable) et les figures versionnées de
`docs/` : `sensibilite_dri.png`, `h2_blocage.png`, `frontiere_service.png`,
`cout_regularite.png`, `metamodele_frontiere.png` et `metamodele_frontiere_directe.png`.

## Structure du dépôt

```
src/corridor/ingest/     port_status.py (collecte 3x/jour), releases.py (archive GitHub),
                         port_stats.py (bilans de trafic, collecte ponctuelle)
src/corridor/transform/  parse_status.py, observations_csv.py, build_escales.py,
                         parse_port_stats.py (tableaux « Marchandises Débarquées »)
src/corridor/sim/        config.py, samplers.py, model.py, run.py, sensitivity.py, stats.py,
                         analytic.py (files d'attente : le repère hors simulation)
config/assumptions.yaml  toutes les valeurs numériques du domaine
config/scenarios/        baseline, phase2, phase2_plus_stockage, phase2_plus_poste,
                         phase2_plus_poste_plus_stockage (plan factoriel 2x2),
                         phase2_mesure (irrégularité estimée : le chiffre appliqué),
                         baseline_sans_blocage (validation analytique, pas un cas physique)
scripts/hooks/pre-commit lance `pytest -m slow -q` si src/corridor/sim/ est touché
scripts/                 health_check, ore_calls_report, arrival_dispersion,
                         economic_summary, compare_scenarios, h2_blocking,
                         poste_stockage_factoriel, frontiere_service, frontiere_directe,
                         cout_regularite,
                         beta_site, metamodele_frontiere, inspect_latest, fetch_raw
data/raw/port_status/    _manifest.csv versionné ; PDF locaux git-ignorés
data/raw/port_stats/     bilans de trafic 2022-2025 : pages HTML versionnées (ce sont elles
                         qui portent les tableaux lus), PDF joints git-ignorés (~10 Mo),
                         empreintes des deux dans _manifest.csv
releases raw-AAAA-MM     les PDF bruts eux-mêmes, un asset par collecte
data/clean/              observations.csv : une ligne par navire et par collecte (versionné)
data/sim/                sorties Parquet (git-ignoré, recalculable)
docs/                    figures versionnées
tests/                   pytest, fixtures PDF réelles
```

## Conventions

- Python 3.12, code et docstrings en **français**. Noms de variables en anglais quand c'est
  l'usage du domaine.
- `ruff check .` et `pytest -q` doivent passer avant tout commit, et `pytest -m slow -q` en
  plus dès que le commit touche à `corridor.sim`.
- **Tout résultat publié dans le README porte une étiquette** disant lequel des trois critères
  CIE il sert (originalité, généralité, importance). Voir « Objectif permanent » en tête.
- **Un test qui rend une vraie figure est marqué `slow`.** La boucle courte teste les données
  qui alimentent la figure (`donnees_figure`), jamais le tracé. Un test qui vérifie qu'une
  fonction de tracé *lève* avant de tracer peut rester rapide : il ne rend rien.
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

Au 2026-10-22, `health_check.py` annonce 31 escales exploitables, donc le seuil est franchi —
**mais toutes cargaisons confondues**. Pour les lois qui comptent ici (intervalles entre
arrivées de minéraliers, tonnages, cadences), la série n'offre que **4 escales minerai dont 3
terminées** : assez pour la cadence (déjà fait, statut `observé`), pas pour la dispersion des
arrivées. Le seuil à viser est donc une dizaine d'escales **minerai**, soit, au rythme observé
d'environ une par semaine, plutôt début 2027.

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

- **Contourner le hook pre-commit, sous quelque forme que ce soit** : ni `--no-verify`, ni
  `-c core.hooksPath=…`, ni `HUSKY=0`, ni désinstallation temporaire. **Y compris quand on a
  vérifié qu'il n'aurait rien lancé** : c'est précisément le raisonnement qui le rend inutile,
  puisqu'un garde-fou qu'on désarme après avoir jugé soi-même du risque ne garde plus rien.
  Si le hook gêne, il faut corriger le hook ou le commit, pas le sauter — et si on l'a sauté,
  le dire.
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
- Publier un résultat dans le README sans dire lequel des trois critères CIE il sert.
- Donner un chiffre appliqué au site sous forme de point : c'est une plage, avec sa méthode
  de propagation d'incertitude, ou rien.
