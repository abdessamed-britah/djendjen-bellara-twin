"""Tests de scripts/figures_papier.py : les figures du papier, sans simuler.

La boucle courte vérifie la sélection des données ; le rendu des deux PDF est joué en `slow`,
comme tout rendu de figure.
"""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.stats import Estimate

_SPEC = importlib.util.spec_from_file_location(
    "figures_papier", Path(__file__).parent.parent / "scripts" / "figures_papier.py"
)
fp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fp)

OBSERVES = {"2023-2025": 0.04, "2022-2025": 0.17}


def _carte():
    r = {"q05": 0.01, "mediane": 0.02, "q95": 0.03}
    return {(cv, dep, s, d): {"2023-2025": r, "2022-2025": r}
            for cv in (0.1, 0.25) for dep in (True, False)
            for s in fp.va.NIVEAUX_STOCKAGE for d in (2, 30)}


def _par_variante():
    noms = [n for n, _, _ in fp.fe.EFFETS] + ["interaction"]
    return {v: {(n, cle): Estimate(mean=0.001, std=0.001, n=200)
                for n in noms for cle, _, _ in fp.fe.INDICATEURS}
            for v in fp.fe.VARIANTES}


def test_la_figure_de_validation_ne_garde_que_le_cv_central_avec_depassements():
    donnees = fp.donnees_validation(_carte(), OBSERVES)
    assert set(donnees["series"]) == set(fp.va.NIVEAUX_STOCKAGE)
    durees, *_ = donnees["series"]["centre"]
    assert durees == [2, 30]
    assert donnees["observe"] == OBSERVES[fp.va.PERIODE_DE_REFERENCE]


def test_le_cv_de_la_figure_est_la_valeur_centrale_publiee():
    raw = {"approvisionnement": {"cv_delai": {"value": 0.1}}}
    assert fp.cv_central(raw) == 0.1
    donnees = fp.donnees_validation(_carte(), OBSERVES, cv=fp.cv_central(raw))
    assert set(donnees["series"]) == set(fp.va.NIVEAUX_STOCKAGE)


def test_un_cv_absent_de_la_carte_leve():
    with pytest.raises(ValueError):
        fp.donnees_validation(_carte(), OBSERVES, cv=0.6)


def test_les_figures_sont_en_anglais():
    assert fp.LANGUE == "en"


@pytest.mark.slow
def test_les_deux_figures_sont_des_pdf_sans_date(tmp_path):
    chemins = [fp.figure_factorielle(_par_variante(), tmp_path / "f.pdf"),
               fp.figure_validation(_carte(), OBSERVES, tmp_path / "v.pdf")]
    for chemin in chemins:
        contenu = chemin.read_bytes()
        assert contenu[:5] == b"%PDF-"
        assert b"/CreationDate" not in contenu
        assert b"/Type3" not in contenu  # polices TrueType incorporées, pas Type 3


# --- figure de la thèse : β selon l'horizon, exogène contre endogène -------------------


def _brut(scenario, deficit_par_an, lots=None):
    config = fp.ch.configuration(scenario)
    cible = fp.ch.cible_t(config, 1.0)
    horizons = [1, 8, 16]
    return {
        "scenario": scenario, "reps": 3, "seed": 1, "horizons": horizons,
        "politique": config.politique_approvisionnement, "cible_an_t": cible,
        "deficits_avec_t": {str(h): [deficit_par_an(h) * cible * k for k in (0.9, 1.0, 1.1)]
                            for h in horizons},
        "deficits_sans_t": {str(h): [deficit_par_an(h) * cible * 1.2] * 3 for h in horizons},
        "rupture_h": {str(h): [10.0, 12.0, 14.0] for h in horizons},
        "lots": lots,
    }


def test_la_these_lit_les_deux_modeles_aux_memes_horizons():
    exogene = _brut(fp.SCENARIO_EXOGENE, lambda h: 0.1 * h ** 0.5)
    endogene = _brut(fp.SCENARIO_ENDOGENE, lambda h: 0.01 * h,
                     lots={"betas": [0.98, 0.99, 0.985]})
    donnees = fp.donnees_these(exogene, endogene)
    assert donnees["exogene"]["horizons"] == [1, 8, 16]
    # déficit en √T : β monte avec l'horizon ; déficit en T : β constant
    assert donnees["exogene"]["beta"] == sorted(donnees["exogene"]["beta"])
    assert donnees["endogene"]["beta"][0] == pytest.approx(donnees["endogene"]["beta"][-1])
    moyenne, bas, haut = donnees["limite"]
    assert moyenne == pytest.approx(0.985)
    assert bas < moyenne < haut


def test_la_these_refuse_des_horizons_differents():
    exogene = _brut(fp.SCENARIO_EXOGENE, lambda h: 0.1)
    endogene = _brut(fp.SCENARIO_ENDOGENE, lambda h: 0.01)
    endogene["horizons"] = [1, 8]
    for cle in ("deficits_avec_t", "deficits_sans_t", "rupture_h"):
        del endogene[cle]["16"]
    with pytest.raises(ValueError):
        fp.donnees_these(exogene, endogene)


@pytest.mark.slow
def test_la_figure_de_la_these_est_un_pdf_sans_date(tmp_path):
    chemin = fp.figure_these(_brut(fp.SCENARIO_EXOGENE, lambda h: 0.1 * h ** 0.5),
                             _brut(fp.SCENARIO_ENDOGENE, lambda h: 0.01 * h,
                                   lots={"betas": [0.98, 0.99]}), tmp_path / "t.pdf")
    contenu = chemin.read_bytes()
    assert contenu[:5] == b"%PDF-" and b"/CreationDate" not in contenu
