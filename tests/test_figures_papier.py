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
