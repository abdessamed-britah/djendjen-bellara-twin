"""Tests de corridor.labels : chaque libellé existe dans les deux langues, sans repli muet."""

from pathlib import Path

import pytest

from corridor import labels


@pytest.mark.parametrize("cle", sorted(labels.LIBELLES))
def test_chaque_libelle_existe_dans_chaque_langue(cle):
    for langue in labels.LANGUES:
        assert labels.libelle(cle, langue).strip(), (cle, langue)


@pytest.mark.parametrize("cle", sorted(labels.LIBELLES))
def test_les_deux_versions_ne_sont_pas_identiques_sauf_mots_communs(cle):
    """Une traduction oubliée se voit à ce que l'anglais recopie le français."""
    fr, en = labels.libelle(cle, "fr"), labels.libelle(cle, "en")
    mots_communs = {"interaction"}
    assert fr != en or fr in mots_communs, cle


def test_une_cle_inconnue_fait_lever():
    with pytest.raises(KeyError, match="inconnu"):
        labels.libelle("rien", "fr")


def test_une_langue_inconnue_fait_lever():
    with pytest.raises(ValueError, match="langue"):
        labels.libelle("total", "de")


def test_une_version_manquante_fait_lever_au_lieu_de_retomber_sur_l_autre(monkeypatch):
    monkeypatch.setitem(labels.LIBELLES, "partiel", {"fr": "seulement en français"})
    with pytest.raises(KeyError, match="sans version"):
        labels.libelle("partiel", "en")


def test_une_figure_par_langue():
    assert labels.chemin_langue("docs/x.png", "fr") == Path("docs/x.png")
    assert labels.chemin_langue("docs/x.png", "en") == Path("docs/x_en.png")
