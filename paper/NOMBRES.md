# Chaque nombre du papier, et d'où il vient

Correspondance entre les nombres de `paper/main.tex` (texte, tableau, légendes) et de
`paper/figures/structure.tex` d'une part, et les lignes de
[`docs/results_summary.md`](../docs/results_summary.md) d'autre part (étiquette
`v0.2-resultats`). Les identifiants (O1, V4, M12, P3…) sont ceux de ce tableau. Les nombres de
pure notation mathématique (exposant 1/2, indices, constantes de la démonstration) et les
coordonnées TikZ ne sont pas listés.

**État : tous les nombres ont une ligne correspondante. Aucun n'est sans correspondance.**

✅ = la valeur figure dans la ligne citée (arrondie au nombre de décimales du papier quand
c'est précisé).

## Résumé (abstract)

| Nombre dans le papier | Ligne | Statut |
| --- | --- | --- |
| exponent 0.492 [0.367; 0.599], « once the initial stock is removed » | M2b | ✅ |
| service level 0.858 at one year → 0.971 at fifty years | M2c | ✅ |
| storage × 12, < 0.002 % | M1 | ✅ |
| 0.9867 ± 0.0012 ; 0.9912 ± 0.0008 | M3, M4 | ✅ |

## 1. Introduction

Aucun nombre.

## 2. Reconstructing vessel calls

| Nombre | Ligne | Statut |
| --- | --- | --- |
| collecte « three times a day » | P1 (cadence programmée ; 2,91 par jour réalisées) | ✅ |
| 13 anomaly codes | P2 | ✅ |
| 24 000 t/day, 3 calls, one berth, one size | O2 | ✅ |
| 183.2 h | O3 | ✅ |
| 159.4 h | O4 | ✅ |
| 13 bulk carriers, 6 of 8 pairs, 75 % [41; 93] %, all 8 at different berths, 5 of 8 different cargo | O5 | ✅ |
| announced about 1.5 days ahead | O6 (réserve) | ✅ |
| mean lead time 22 days | H1 | ✅ |
| coefficient of variation 0.028 | S6 | ✅ |
| 2 of 148 pairs, 1 % [0; 5] %, 18 bulk carriers | O6 | ✅ |
| 0.83 [0.21; 1.73], 16 quarters ; 0.45 without one quarter | O1 | ✅ |

## 3. The trap

| Nombre | Ligne | Statut |
| --- | --- | --- |
| replicated 20 times | M2, M2b, M2c (Rép.) | ✅ |
| horizons from 1 to 50 years | M2c | ✅ |
| p = 0.578 [0.434; 0.718] **with** the nominal initial stock | M2 | ✅ |
| p = 0.492 [0.367; 0.599] **with zero** initial stock | M2b | ✅ |
| 0.858 at one year, 0.971 at fifty years | M2c | ✅ |
| capacities × 12, < 0.002 %, horizons 1, 4, 12 years | M1 | ✅ |
| Fig. 1 : same 20 paired replications per model, 95 % intervals, 400-year run | M2c, M3b (Rép.), M3 (400 ans par lots), en-tête (IC 95 %) | ✅ ; la figure trace les sept valeurs de M2c et de M3b et la bande de M3, lues dans `data/sim/convergence_horizon_baseline.json` et `…_baseline_endogene.json` |

## 4. The corrective model

| Nombre | Ligne | Statut |
| --- | --- | --- |
| Q = one vessel, repeated until the position is back at or above s (texte et Fig. 2) | P5 | ✅ |
| lead time 22 days [15; 30] | H1 | ✅ |
| cv 0.25 [0.10; 0.60] | H2 | ✅ |
| reorder point 30 or 35 days, [15; 60] | H3 | ✅ |
| outages 7 days [2; 30] | H10 | ✅ |
| Pollaczek–Khinchine : 200 runs × 8 years, 37.96 ± 1.40 h vs 38.53 h | V1 | ✅ |
| continuous : 8 seeds × 100 years, median 0.067–0.089 vs 0.83, P(I ≥ 0.45) = 0 | V2 | ✅ |
| within-year CV² 0.174 vs [0.006; 0.032] | V3 | ✅ |
| 2023–2025 : 118 of 126, 4 seeds × 60 years, 2-day outages rejected | V4, H10 | ✅ |
| simulated 5–95 % band (texte, tableau I, légende de la Fig. 3) | P3 | ✅ |
| 2022–2025 : 15 of 126, all at 30 days | V6 | ✅ |
| 18, 19, 21, 21, 21 of 21 at cv 0.10, 0.25, 0.40, 0.50, 0.60 | V5 | ✅ |
| warm-up 4 years, measured over 16 | P4 | ✅ |
| 95 % intervals | en-tête du tableau (« IC 95 % ») | ✅ |
| Fig. 3 : cv 0.25, three storage levels, 2023–2025 | V5, H2, V4 | ✅ |

## 5. Results

| Nombre | Ligne | Statut |
| --- | --- | --- |
| endogenous β between 0.987 and 0.991 at every horizon | M3b | ✅ (min 0,987, max 0,991) |
| no significant paired difference beyond 8 years | M3b (réserve) | ✅ |
| exponent 1.048 [0.952; 1.134], fitted on horizons **of 8 years and more** (endogène) | M3b (réserve) | ✅ |
| batch means over 400 years, 0.9867 ± 0.0012, 0.9912 ± 0.0008 | M3, M4 | ✅ |
| 0.9917 → 0.9749 | M5 | ✅ (statut **reconstruit**, dit dans les limites du papier) |
| 2 × 2 design, 200 paired replications of 16 years, four variants | M10–M14 | ✅ |
| storage : −0.007 to −0.022 points | M13 | ✅ |
| second berth : +0.005 to +0.058 points | M10 | ✅ |
| demurrage −1.96 to −3.56 M USD/year | M11 | ✅ (avec la mention coûts non sourcés, surestaries en borne haute) |
| total −1.61 to −3.03 M USD/year | M12 | ✅ (même mention) |
| base-case total 4.96 to 8.43 M USD/year | M14 | ✅ (même mention) |
| discrete outages −1.3 to −2.1 points | M8 | ✅ |
| 0.15, 0.85, 2.50, 5.42 episodes per year | M20 | ✅ |
| relative risk 0.20 and 0.30, 22 days after a restart | M18 | ✅ |
| delay beyond 90th percentile precedes 85 to 98 %, vs 21 to 23 % | M19 | ✅ |
| full-rate reorder point closes 100 % and 92 %, adds 89 and 82 kt | M21 | ✅ |
| s\* 27.5 → 44.4 (Ci), 31.7 → 44.8 (Di), 27.5 → 35.2 (Cp), 31.7 → 36.1 (Dp), cv 0.10 → 0.60 | M15 | ✅ |
| 500 draws ; 5th percentile 85.8 % to 97.2 % (fixed s) | S1 | ✅ (q05 des quatre variantes : 96.0 / 89.3 / 97.2 / 85.8) |
| 5th percentile 92.3 % to 98.8 % (adapted s) | S3 | ✅ (q05 : 98.8 / 95.2 / 97.7 / 92.3) |
| Fig. 4 : 200 replications, 16 years, 95 % intervals | M10–M13 | ✅ ; la figure trace les 6 effets × 3 indicateurs × 4 variantes de `data/sim/factoriel_endogene.json`, dont les valeurs publiées sont M10–M13 ; les effets « both levers » et « interaction » sont tracés sans être cités dans le texte |

## 6. Limitations and conclusion

| Nombre | Ligne | Statut |
| --- | --- | --- |
| unloading rate on 3 calls | O2 | ✅ |

## Déclarations

| Nombre | Statut |
| --- | --- |
| tag `v0.2-resultats` | nom d'étiquette git, pas un résultat |

## Les deux exposants du modèle exogène

Ils ne sont jamais cités sans leur condition : **0.578** [0.434; 0.718] *avec* le stock
initial nominal (M2), **0.492** [0.367; 0.599] *à stock initial nul* (M2b). Le résumé ne cite
que le second, avec « once the initial stock is removed » ; la section 3.2 cite les deux, avec
« with the nominal initial stock » et « with zero initial stock », et dit pourquoi le second
décrit la marche elle-même. L'exposant **1.048** de la section 5.1 est celui du modèle
*endogène*, sur les horizons de 8 ans et plus.
