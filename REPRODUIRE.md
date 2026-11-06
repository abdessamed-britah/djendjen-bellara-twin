# Reproduire les résultats

Ce fichier dit comment refaire, depuis un clone du dépôt et sans aucun accès privilégié, chaque
chiffre de [`docs/results_summary.md`](docs/results_summary.md). L'étiquette git
**`v0.1-resultats`** marque le commit sur lequel ces chiffres ont été figés.

## Environnement

| Composant | Version utilisée (2026-11-06) |
| --- | --- |
| Python | 3.12.14 (conda, environnement `corridor`) |
| simpy | 4.1.2 |
| numpy | 2.5.3 |
| pyarrow | 25.0.1 |
| matplotlib | 3.11.2 |
| pdfplumber | 0.11.10 |
| PyYAML | 6.0.3 |
| requests | 2.34.2 |
| pytest | 9.1.1 |
| ruff | 0.16.8 |

Machine des mesures : portable Windows 11, 14 processeurs logiques ; les calculs parallèles
utilisent 10 à 12 processus (`--processus`, par défaut le nombre de processeurs moins deux ou quatre).

```bash
conda create -n corridor python=3.12
conda activate corridor
pip install -e ".[dev]"
git config core.hooksPath scripts/hooks   # hook pre-commit : jamais contourné
```

Sous Windows, le Python du projet est `%USERPROFILE%\miniconda3\envs\corridor\python.exe` ;
`python` seul n'est pas dans le PATH de Git Bash.

## Tests

```bash
ruff check .
pytest -q          # boucle courte : 1 154 tests
pytest -m slow -q  # 35 tests lents : validation analytique, figures, parallèle = série
```

Durée de la boucle courte, mesurée une fois à l'étape 9 : voir « Durée de la boucle courte »
plus bas.

## Données

Tout part de données publiques versionnées ou archivées :

- `data/clean/observations.csv` et `data/raw/port_status/_manifest.csv` (versionnés, mis à jour
  ~3×/jour par le workflow `scrape.yml`) ; les PDF bruts sont dans les releases GitHub
  `raw-AAAA-MM`, `python scripts/fetch_raw.py` les retélécharge ;
- `data/raw/port_stats/` : pages HTML des bilans de trafic 2022-2025 (versionnées).

Les sorties de simulation (`data/sim/*.json`, `*.parquet`, `*.csv`) sont ignorées par git et
se recalculent par les commandes ci-dessous. Toutes les graines par défaut valent **20260924**.

## Commandes, dans l'ordre des dépendances

Durées : *mesurée* = chronométrée lors de la régénération de l'étape 9 (10 processus, machine
non dédiée) ; *annoncée* = estimée à l'étape qui a produit le chiffre. Comptez deux à trois
fois plus sur batterie.

| # | Commande | Produit | Durée |
| --- | --- | --- | --- |
| 1 | `python scripts/arrival_dispersion.py` | c_a² sur 16 trimestres, IC bootstrap | secondes |
| 2 | `python scripts/ore_calls_report.py` | cadence observée, attente BERGE NIMBA, signal H2 | secondes |
| 3 | `pytest -m slow -q tests/test_analytic.py` | validation Pollaczek-Khinchine | ~6 s |
| 4 | `python scripts/convergence_horizon.py --scenario phase2_endogene --lots-annees 400` (et `baseline_endogene`) | β∞ endogène | ~20 min chacun, annoncée |
| 5 | `python scripts/politique_commande.py` | courbe de coût en s, s optimal | ~50 min, annoncée |
| 6 | `python scripts/validation_ca2.py` | réfutation du modèle continu | ~1 h, annoncée |
| 7 | `python scripts/diagnostic_dispersion.py` | l'écart n'est pas un artefact | ~15 min, annoncée |
| 8 | `python scripts/validation_arrets.py` | carte de compatibilité, 126 combinaisons → `data/sim/validation_arrets.json`, figure FR/EN | **29 min, mesurée** |
| 9 | `python scripts/effet_arrets.py` | continu contre discret : β et coût | ~40 min, annoncée |
| 10 | `python scripts/factoriel_endogene.py` | plan poste × stockage, 4 variantes → `data/sim/factoriel_endogene.json` | **80 min, mesurée** |
| 11 | `python scripts/chiffre_site_endogene.py` | chiffre du site à s fixe, 500 tirages → CSV | ~80 min, annoncée |
| 12 | `python scripts/point_de_commande_adaptatif.py` | règle s\*(cv) → `data/sim/point_de_commande_adaptatif.json` | ~2 h 30, annoncée |
| 13 | `python scripts/chiffre_site_endogene.py --adaptatif --comparer-a data/sim/chiffre_site_endogene.csv` | chiffre du site à s adapté (après 11 et 12) | ~1 h, annoncée |
| 14 | `python scripts/politique_arrets.py` | politiques a/b/c → `data/sim/politique_arrets.json` | ~40 min, annoncée |
| 15 | `python scripts/attribution_ruptures.py` | journal des ruptures → `data/sim/attribution_ruptures.json` | ~30 min, annoncée |
| 16 | `python scripts/regle_service.py` | s_β(cv), sensibilité aux prix (après 12) | ~45 min, annoncée |
| 17 | `python scripts/rythme_de_marche.py` | s au rythme de marche (après 14 et 15) → `data/sim/rythme_de_marche.json` | **17 min, mesurée** |
| 18 | `python scripts/ordre_accostage.py` | test FIFO sur les escales observées | secondes |

Les scripts longs écrivent leurs résultats en JSON ou CSV et savent les relire sans simuler
(`--depuis-json`, `--depuis-csv`) : les figures se retracent en quelques secondes. Les figures
sont produites en français (`docs/x.png`) et en anglais (`docs/x_en.png`).

Les étapes 17 et 18 ne recalculent pas leurs références : le rythme de marche reprend C et D
des étapes 14 et 15 (mêmes graines, mêmes réplications) et refuse de tourner sans elles. Le
test FIFO dépend de la série collectée : il donne des effectifs différents à chaque nouvelle
collecte (chiffres du tableau : série du 2026-09-24 au 2026-11-05).

## Durée de la boucle courte

Mesurée une seule fois, le 2026-11-06, sur secteur, sans simulation en cours (charge du
processeur à 6 % juste après la mesure ; navigateur et éditeur ouverts) :

| Mesure | Tests | Durée |
| --- | --- | --- |
| `pytest -q`, sur secteur, au repos | 1 138 | **68 s** (67,6 s selon pytest) |
| pour mémoire : `pytest -q` sur batterie, au repos (étape 9, avant de pouvoir brancher) | 1 138 | 116 s |
| pour mémoire : 2026-10-29, sur secteur, 31 % du processeur pris ailleurs | 1 124 | 57 s |
| pour mémoire : 2026-10-27, sur batterie | 1 001 | 38 s |

**La limite de 45 s est dépassée au repos.** Les mesures successives ne sont pas cohérentes
entre elles (57 s sur une machine chargée, 68 s au repos) : la durée dépend davantage de
l'état thermique et énergétique du portable que du nombre de tests, et la réduire encore en
passant des tests en `slow` déplacerait la couverture sans rendre la mesure plus stable.

**Proposition, à valider par le porteur du projet** : porter la limite à **90 s sur secteur,
au repos**, soit la mesure du jour plus ~30 % de marge pour la dispersion d'une mesure à
l'autre, et la renégocier quand la suite atteindra ~2 300 tests (le double d'aujourd'hui),
selon la règle de révision existante. La limite n'est pas modifiée dans `pyproject.toml` ni
dans `CLAUDE.md` tant que cette proposition n'est pas acceptée.

La durée des tests en CI (GitHub Actions) n'a pas pu être relevée : l'outil `gh` n'est pas
installé sur la machine des mesures.
