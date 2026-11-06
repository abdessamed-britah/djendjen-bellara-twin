# Tableau unique des résultats

Une ligne par chiffre destiné à l'article *Computers & Industrial Engineering*. Figé à
l'étiquette git **`v0.1-resultats`** (2026-11-06) ; commandes et durées dans
[`REPRODUIRE.md`](../REPRODUIRE.md). Détail, figures et discussion dans le README.

**Colonnes.** *Commit* : celui qui a produit le chiffre tel qu'il est écrit ici ;
`v0.1` = régénéré ou calculé à l'étape 9, sur le commit étiqueté. *Graine* : graine
maîtresse ; la réplication i de toutes les configurations d'un même script en dérive, d'où
des comparaisons appariées. *Rép.* : réplications par configuration (ou tirages, ou effectif
observé). *Variante* : C/D = arrêts du four continus/discrets, p/i = dépassements en mer
permis/interdits, « exo. » = approvisionnement exogène (modèle historique), « — » = sans
simulation. *Statut* : **calculé** (sortie d'un script relancé, sauvegardée par lui),
**reconstruit** (relevé dans une sortie imprimée, non recalculé depuis), **fixé par
hypothèse** (valeur de travail de `config/assumptions.yaml`, pas un résultat).
Intervalles : IC 95 % (± 1,96 σ/√n) pour les moyennes et différences appariées ; [q05 ; q95]
pour les distributions sur tirages ; Wilson 95 % pour les proportions observées ; bootstrap
percentile pour c_a² et les parts.

**Réserve générale, valable pour toutes les lignes en USD** : la marge par tonne, le taux de
surestaries, le prix des pellets et le taux de portage sont des valeurs de travail sans
source locale (lignes H6-H9) ; les surestaries sont comptées sans franchise de staries,
donc en borne haute. Les deux capacités de stockage sont `inconnu` (H4-H5).

## 1. Données observées

| ID | Chiffre | Valeur | Intervalle | Script | Commit | Graine | Rép. | Variante | Statut | Réserve |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| O1 | Irrégularité des débarquements de minerai, I (lu comme c_a²) | 0,83 | [0,21 ; 1,73] bootstrap | `arrival_dispersion.py` | 3c41062 | 20260924 | 16 trimestres, 10 000 tirages | — | calculé | 0,45 sans 2022 T3 ; ne vaut c_a² que si les arrivées sont exogènes (étape 3) |
| O2 | Cadence apparente de déchargement | 24 000 t/j | centres des 3 escales à ± 2,5 % | `ore_calls_report.py` | f7a7044 | — | 3 escales | — | calculé | 3 navires, un seul poste (QW/7), un seul gabarit |
| O3 | Attente en rade de BERGE NIMBA | 183,2 h | — | `ore_calls_report.py` | 2fb8f82 | — | 1 escale | — | calculé | censurée à gauche ; 0,0 h de poste démontré libre |
| O4 | Attente en rade de IAN M (minerai, QW/7) | 159,4 h | — | `ordre_accostage.py` | v0.1 | — | 1 escale | — | calculé | seconde attente minerai mesurée, collecte jusqu'au 2026-11-05 |
| O5 | Test FIFO au quai : paires de vraquiers ayant attendu ensemble, second accosté le premier | 6 sur 8 = 75 % | [41 ; 93] % Wilson | `ordre_accostage.py` | v0.1 | — | 13 vraquiers, 8 paires | — | calculé | toutes les paires à des postes différents, 5 sur 8 à cargaisons différentes : ne teste pas la file d'un même poste |
| O6 | Ordre en mer (indirect) : paires de vraquiers annoncés dans un ordre, arrivés dans l'autre | 2 sur 148 = 1 % | [0 ; 5] % Wilson | `ordre_accostage.py` | v0.1 | — | 18 vraquiers | — | calculé | horizon d'annonce ~1,5 j : mesure l'erreur d'ETA, pas l'ordre des commandes ; ne tranche pas p/i |

## 2. Validation du modèle

| ID | Chiffre | Valeur | Intervalle | Script | Commit | Graine | Rép. | Variante | Statut | Réserve |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V1 | Attente M/G/1 simulée contre Pollaczek-Khinchine (38,53 h) | 37,96 h | ± 1,40 h | `tests/test_analytic.py` | 959c7b3 | 20260924 | 200 × 8 ans | exo. | calculé | ne valide que la file devant le poste ; le test revérifie l'inclusion à chaque `pytest -m slow` |
| V2 | Modèle à consommation continue : I médian simulé (contre 0,83 observé) | 0,067 à 0,089 | P(I ≥ 0,45) = 0 | `validation_ca2.py` | 50fee4e | 20260924 | 8 graines × 100 ans | C p/i | calculé | réfuté : trop régulier |
| V3 | CV² intra-annuel des tonnages débarqués, observé contre simulé continu | 0,174 contre [0,006 ; 0,032] | — | `diagnostic_dispersion.py` | 576420b | 20260924 | 8 graines × 100 ans | C | calculé | l'écart n'est pas un artefact de mesure |
| V4 | Arrêts discrets, 2023-2025 : combinaisons compatibles | 118 sur 126 | — | `validation_arrets.py` | v0.1 (étape 5 : 5d741e7) | 20260924 | 4 graines × 60 ans | D p/i | calculé | **régénéré** : 0 verdict changé sur les 84 combinaisons de l'étape 5 ; faible puissance ; période choisie après avoir vu 2022 |
| V5 | Compatibilité par cv_delai, 2023-2025, dépassements permis (0,10 / 0,25 / 0,40 / 0,50 / 0,60) | 18 / 19 / 21 / 21 / 21 sur 21 | — | `validation_arrets.py` | v0.1 | 20260924 | 4 × 60 ans | D p | calculé | ne discrimine pas cv_delai ; 0,25 interdits : 18 sur 21 |
| V6 | Arrêts discrets, 2022-2025 : combinaisons compatibles | 15 sur 126, toutes à 30 j d'arrêt | — | `validation_arrets.py` | v0.1 | 20260924 | 4 × 60 ans | D p/i | calculé | montée en cadence 2022 |

## 3. Résultats du modèle

| ID | Chiffre | Valeur | Intervalle | Script | Commit | Graine | Rép. | Variante | Statut | Réserve |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | Effet de la capacité de stockage (× 12) sur la production, stock initial figé | < 0,002 % | — | `tests/test_sim_model.py` | 3d0fa1d | 20260924 | 1, 4, 12 ans | exo. | calculé | modèle exogène sans régime stationnaire (limite 0) : motive le modèle endogène |
| M2 | Exposant du déficit cumulé, approvisionnement exogène | 0,578 | [0,434 ; 0,718] | `convergence_horizon.py` | 9802faf | 20260924 | 20 | exo. | calculé | marche sans dérive : aucun β stationnaire |
| M3 | β∞ approvisionnement, baseline endogène | 0,9867 | ± 0,0012 | `convergence_horizon.py` | 92d2fe6 | 20260924 | 400 ans par lots | C p | calculé | s = 30 j fixé par hypothèse |
| M4 | β∞ approvisionnement, phase 2 endogène | 0,9912 | ± 0,0008 | `convergence_horizon.py` | 92d2fe6 | 20260924 | 400 ans par lots | C p | calculé | l'ordre phase 2 > baseline tient aux dépassements (M5) |
| M5 | β phase 2, dépassements permis → interdits | 0,9917 → 0,9749 | — | script de diagnostic | 92d2fe6 | 20260924 | 12 graines × 20 ans | C p/i | reconstruit | mesuré par substitution avant que l'option existe |
| M6 | Coût total à s = 30 j, phase 2 (arrêts + surestaries + portage) | 4,90 M USD/an (p) ; 7,03 (i) | ± 0,13 ; ± 0,26 | `politique_commande.py` | 712d0e5 | 20260924 | 16 × 16 ans | C p/i | calculé | coûts non sourcés |
| M7 | Asymétrie de la courbe de coût en s, baseline | 15 j trop bas : +10,8 M ; 30 j trop haut : +3,0 M USD/an | — | `politique_commande.py` | 712d0e5 | 20260924 | 16 | C p | calculé | coûts non sourcés |
| M8 | Effet des arrêts discrets sur β approvisionnement | −1,3 à −2,1 points | — | `effet_arrets.py` | 5d741e7 | 20260924 | 16 | C→D | calculé | discret = borne haute de l'irrégularité |
| M9 | Effet des arrêts discrets sur le coût total | +0,9 (baseline) ; +1,8 à +2,3 (phase 2) M USD/an | — | `effet_arrets.py` | 5d741e7 | 20260924 | 16 | C→D | calculé | coûts non sourcés |
| M10 | Second poste, effet sur β (stockage de base) | +0,005 / +0,007 / +0,021 / +0,058 point | ± 0,002 / 0,002 / 0,005 / 0,014 | `factoriel_endogene.py` | v0.1 (étape 6 : 181c52a) | 20260924 | 200 × 16 ans | C p / C i / D p / D i | calculé | **régénéré** : inchangé ; significatif mais négligeable |
| M11 | Second poste, effet sur les surestaries (stockage de base) | −1,96 / −2,87 / −2,77 / −3,56 M USD/an | ± 0,02 / 0,02 / 0,02 / 0,03 | `factoriel_endogene.py` | v0.1 | 20260924 | 200 | C p / C i / D p / D i | calculé | surestaries en borne haute ; C i : −2,88 publié, −2,875 → arrondi |
| M12 | Second poste, effet sur le coût total (stockage de base) | −1,61 / −2,37 / −2,35 / −3,03 M USD/an | ± 0,01 / 0,02 / 0,02 / 0,03 | `factoriel_endogene.py` | v0.1 | 20260924 | 200 | C p / C i / D p / D i | calculé | gain **brut** : ni investissement ni exploitation du quai ; poste 2 non observé |
| M13 | Stockage haut, effet sur β (1 poste) | −0,007 / −0,007 / −0,020 / −0,022 point | ± 0,011 / 0,006 / 0,013 / 0,024 | `factoriel_endogene.py` | v0.1 | 20260924 | 200 | C p / C i / D p / D i | calculé | capacités `inconnu` ; le stockage ne sert pas la production |
| M14 | Coût total, case de base (1 poste, stockage de base) | 4,96 / 6,53 / 6,46 / 8,43 M USD/an | — | `factoriel_endogene.py` | v0.1 | 20260924 | 200 | C p / C i / D p / D i | calculé | coûts non sourcés |
| M15 | Règle s\*(cv_delai) : s optimal de cv = 0,10 à 0,60 | C p 27,5→35,2 ; C i 27,5→44,4 ; D p 31,7→36,1 ; D i 31,7→44,8 j | — | `point_de_commande_adaptatif.py` | e12ad9a | 20260924 | **40** par point | 4 variantes | calculé | 40 réplications ; grille de s au pas de la grille ; coûts non sourcés |
| M16 | Politique (b) « rythme moyen » : part des ruptures dues aux arrêts récupérée | 20 % (p) ; 25 % (i) | [17 ; 23] ; [23 ; 28] | `politique_arrets.py` | e12ad9a | 20260924 | 200 | D p / D i | calculé | coûte +1,32 ± 0,07 et +1,03 ± 0,08 M USD/an : hypothèse « l'essentiel à coût nul » infirmée |
| M17 | Politique (c) « date de reprise » : part récupérée | 8 % | — | `politique_arrets.py` | e12ad9a | 20260924 | 200 | D p / D i | calculé | coût +0,05 / −0,03 M USD/an |
| M18 | Risque relatif de rupture dans les 22 j suivant une reprise du four | 0,20 (p) ; 0,30 (i) | [0,16 ; 0,26] ; [0,27 ; 0,35] | `attribution_ruptures.py` | fffc2b9 | 20260924 | 200 | D p / D i | calculé | « pleine cadence après la reprise » infirmée |
| M19 | Épisodes de rupture précédés d'un retard au-delà du 90e centile | 98 / 98 / 89 / 85 % (21-23 % des instants quelconques) | RR ≈ 3,9 à 4,4 | `attribution_ruptures.py` | fffc2b9 | 20260924 | 200 | C p / C i / D p / D i | calculé | une attribution, pas une causalité démontrée |
| M20 | Épisodes de rupture par an | 0,15 / 0,85 / 2,50 / 5,42 | — | `attribution_ruptures.py` | fffc2b9 | 20260924 | 200 | C p / C i / D p / D i | calculé | s = 35 j |
| M21 | Point de commande au rythme de marche : part de l'écart continu–discret réduite (épisodes/an) | 100 % (p) ; 92 % (i) | [98 ; 103] ; [88 ; 94] bootstrap | `rythme_de_marche.py` | v0.1 | 20260924 | 200 | D p / D i | calculé | en tonnes, identique à s = 41,2 j de consommation moyenne au lieu de 35 : coûte +89 / +82 kt de stock et +1,64 ± 0,03 / +0,74 ± 0,06 M USD/an face à D ; C et D repris des étapes 7-8, D′ seul simulé |
| M22 | Point de commande au rythme de marche : part de l'écart réduite (heures de rupture) | 109 % (p) ; 122 % (i) | [108 ; 111] ; [119 ; 124] bootstrap | `rythme_de_marche.py` | v0.1 | 20260924 | 200 | D p / D i | calculé | > 100 % : D′ porte plus de stock que la référence continue ; même réserve que M21 |
| M23 | s pour tenir β = 98 %, D i, cv = 0,6 | 58,7 j, 23,4 M USD/an | [57,7 ; 59,8] j | `regle_service.py` | fffc2b9 | 20260924 | **40** | D i | calculé | contre s\* = 44,8 j (93,3 %, 19,2 M) ; β = 99 % hors domaine (> 60 j) |
| M24 | Sensibilité de s\* aux prix ± 50 % | 1-3 j (cv 0,1) ; 10-11 j (cv 0,6, i) | — | `regle_service.py` | fffc2b9 | 20260924 | 40 | 4 variantes | calculé | autant que l'écart des cibles 98/99 % |

## 4. Chiffre appliqué au site (distribution sur 500 tirages)

Méthode de propagation : chaque tirage prend un jeu de paramètres dans les fourchettes de
`assumptions.yaml` (capacités, délai, cv_delai, prix), une graine propre, et simule 16 ans
en régime stationnaire ; la plage est [q05 ; q95] des 500 tirages, valeur = médiane.

| ID | Chiffre | Valeur | Intervalle | Script | Commit | Graine | Rép. | Variante | Statut | Réserve |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | β de long terme, s fixe | 98,4 / 97,7 / 98,9 / 94,9 % | [96,0 ; 100,0] / [89,3 ; 100,0] / [97,2 ; 100,0] / [85,8 ; 100,0] | `chiffre_site_endogene.py` | 181c52a | 20260924 | 500 tirages | C p / C i / D p / D i | calculé | capacités `inconnu`, cv_delai non mesuré ; D = borne haute des arrêts |
| S2 | Enveloppe des quatre variantes, s fixe | β 85,8-100 % ; coût 3,2-22,1 M USD/an | — | `chiffre_site_endogene.py` | 181c52a | 20260924 | 500 | 4 variantes | calculé | coûts non sourcés |
| S3 | β de long terme, s adapté à cv_delai | 99,5 / 98,7 / 99,0 / 97,4 % | [98,8 ; 99,9] / [95,2 ; 99,9] / [97,7 ; 99,8] / [92,3 ; 99,7] | `chiffre_site_endogene.py --adaptatif` | e12ad9a | 20260924 | 500 | C p / C i / D p / D i | calculé | suppose cv_delai connu de l'exploitant ; règle M15 à 40 réplications |
| S4 | Coût total, s adapté | 5,8 / 9,1 / 7,6 / 11,7 M USD/an | [2,8 ; 7,5] / [2,9 ; 16,6] / [4,5 ; 9,9] / [4,7 ; 20,2] | `chiffre_site_endogene.py --adaptatif` | e12ad9a | 20260924 | 500 | C p / C i / D p / D i | calculé | coûts non sourcés |
| S5 | Part de la dispersion d'une année due à l'aléa (horizons appariés) | un tiers à deux tiers | — | `chiffre_site_endogene.py` | e12ad9a | 20260924 | 500 | 4 variantes | calculé | dépend de la largeur des fourchettes de paramètres |
| S6 | cv_delai mesurable sur la collecte (provisoire) | 0,028 | — | `etat_mesure_delai.py` | e12ad9a | — | 34 paires, 1 minerai | — | calculé | ne mesure pas le délai de commande ; inutilisable pour la plage |

## 5. Valeurs fixées par hypothèse (pas des résultats)

| ID | Paramètre | Valeur | Fourchette | Source | Statut |
| --- | --- | --- | --- | --- | --- |
| H1 | Délai d'approvisionnement moyen | 22 j | [15 ; 30] | ordre de grandeur de traversée | fixé par hypothèse (`estimé`) |
| H2 | cv_delai | 0,25 | [0,10 ; 0,60] | aucune | fixé par hypothèse (`estimé`) |
| H3 | Point de commande s | 30 j (C p) ; 35 j (C i, D p, D i) | [15 ; 60] | optimum de M6 | fixé par hypothèse (`estimé`) |
| H4 | Stockyard à pellets du port | 300 kt | [150 ; 600] kt | aucune | fixé par hypothèse (`inconnu`) |
| H5 | Stock de pellets de l'usine | 150 kt | [50 ; 300] kt | aucune | fixé par hypothèse (`inconnu`) |
| H6 | Coût d'une journée d'arrêt du DRI | 130 000 USD | [40 000 ; 230 000] | marges de fours chinois | fixé par hypothèse (`estimé`) |
| H7 | Surestaries | 1 250 USD/h | [400 ; 3 000] | aucune locale | fixé par hypothèse (`estimé`) |
| H8 | Prix des pellets | 140 USD/t | [100 ; 180] | aucune locale | fixé par hypothèse (`estimé`) |
| H9 | Taux de portage | 0,10 /an | [0,06 ; 0,15] | aucune locale | fixé par hypothèse (`estimé`) |
| H10 | Durée moyenne d'un arrêt discret du four | 7 j | [2 ; 30] | aucune ; 2 j écarté par V4 | fixé par hypothèse (`estimé`) |

## Ce que ce tableau ne contient pas

Les résultats antérieurs à l'étape 0 qui dépendent de l'axe τ (frontière de service, H4,
métamodèle, frontière directe, gain du stockage du plan factoriel exogène) sont **invalidés**
par la limite 0 et n'entrent pas dans l'article. Ils restent dans le README, marqués comme
tels.
