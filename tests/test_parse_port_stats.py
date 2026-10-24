"""Tests de `corridor.transform.parse_port_stats` : lecture du tableau de débarquement.

Hors ligne. Les cas tordus sont construits en Python dans ce fichier, et un test balaie
en plus les pages réellement archivées quand elles sont présentes.
"""

from pathlib import Path

import pytest

from corridor.ingest.port_stats import PAGES
from corridor.transform import parse_port_stats as pps

ARCHIVE = Path(__file__).parent.parent / "data" / "raw" / "port_stats"


def _table(lignes: str) -> str:
    return f"<table><tbody>{lignes}</tbody></table>"


def _ligne(label, gauche, droite, evolution="+ 1 %"):
    return (
        f'<tr style="height: 24px;">'
        f'<td style="width: 25%;">{label}</td>'
        f'<td class="xl66" align="right">{gauche}</td>'
        f'<td class="xl66" align="right">{droite}</td>'
        f'<td><span style="color: #008000;">{evolution}</span></td>'
        f"</tr>"
    )


# --- nettoyage des cellules -------------------------------------------------


def test_les_balises_sont_retirees_et_les_entites_decodees():
    assert pps.clean_text('<span style="color:#f00">Pellets&nbsp;/ DRI</span>') == "Pellets / DRI"


def test_le_tiret_cadratin_html_est_decode():
    assert pps.clean_text("<td>&#8211; 15 %</td>") == "– 15 %"


@pytest.mark.parametrize(
    "cellule",
    ["1 670 113", "1 670 113", "1&nbsp;670&nbsp;113", "1 670 113",
     " 1 670 113 ", "<span>1 670 113</span>"],
)
def test_toutes_les_espaces_de_milliers_sont_acceptees(cellule):
    """Une seule forme d'espace oubliée et le tonnage serait tronqué sans erreur."""
    assert pps.parse_number(cellule) == 1_670_113.0


@pytest.mark.parametrize("cellule", ["– 15 %", "+ 29 %", "-", "", "&nbsp;", "n/a", "1,5"])
def test_ce_qui_n_est_pas_un_tonnage_vaut_none(cellule):
    assert pps.parse_number(cellule) is None


def test_zero_est_un_tonnage_pas_une_absence():
    assert pps.parse_number("0") == 0.0


# --- lecture des lignes -----------------------------------------------------


def test_les_lignes_sont_lues_dans_l_ordre():
    html = _table(_ligne("Blé/Orge/Soja/Mais", "339 746", "285 710")
                  + _ligne("Pellets", "750 067", "567 848"))
    lignes = pps.parse_rows(html)
    assert [x.label for x in lignes] == ["Blé/Orge/Soja/Mais", "Pellets"]
    assert lignes[1].gauche == 750_067.0
    assert lignes[1].droite == 567_848.0


def test_une_ligne_trop_courte_est_ignoree():
    """Les lignes de titre et de mise en page n'ont pas quatre cellules."""
    html = _table("<tr><td>Marchandises</td></tr>" + _ligne("Pellets", "1", "2"))
    assert [x.label for x in pps.parse_rows(html)] == ["Pellets"]


def test_une_ligne_sans_libelle_est_ignoree():
    html = _table(_ligne("", "1", "2") + _ligne("Pellets", "3", "4"))
    assert [x.label for x in pps.parse_rows(html)] == ["Pellets"]


def test_une_cellule_non_numerique_donne_none_sans_faire_tomber_la_ligne():
    html = _table(_ligne("Fil Machine", "-", "1 500"))
    ligne = pps.parse_rows(html)[0]
    assert ligne.gauche is None
    assert ligne.droite == 1_500.0


# --- la ligne des pellets ---------------------------------------------------


@pytest.mark.parametrize("label", ["Pellets", "Pellets / DRI", "PELLETS", "pellets"])
def test_la_ligne_pellets_est_reconnue_quel_que_soit_son_libelle(label):
    """Le port a renommé le poste en 2024 : la reconnaissance se fait sur un motif."""
    ligne = pps.pellets_row(_table(_ligne(label, "750 067", "567 848")))
    assert ligne is not None
    assert ligne.label == label  # le libellé d'origine est conservé, pas normalisé
    assert ligne.droite == 567_848.0


def test_aucune_ligne_pellets_vaut_none_et_pas_zero():
    """Une nomenclature changée doit se voir, pas se lire comme un tonnage nul."""
    assert pps.pellets_row(_table(_ligne("Bobines en Acier", "1", "2"))) is None


def test_la_premiere_ligne_correspondante_gagne():
    html = _table(_ligne("Pellets", "1", "2") + _ligne("Pellets / DRI", "3", "4"))
    assert pps.pellets_row(html).gauche == 1.0


def test_le_mot_pellets_doit_etre_entier():
    """Un motif trop lâche attraperait n'importe quel libellé contenant ces lettres."""
    assert pps.pellets_row(_table(_ligne("Pelletiers divers", "1", "2"))) is None


# --- sur les pages réellement archivées -------------------------------------


@pytest.mark.skipif(not ARCHIVE.exists(), reason="archive des bilans absente")
def test_toutes_les_pages_archivees_portent_une_ligne_pellets():
    """Se renforce tout seul : chaque page ajoutée à l'archive entre dans ce test."""
    pages = [ARCHIVE / f"{p.annee_droite}_{p.cumul}_{p.slug}.html" for p in PAGES]
    presentes = [p for p in pages if p.exists()]
    assert presentes, "aucune page archivée"
    for chemin in presentes:
        ligne = pps.pellets_row(pps.read_page(chemin))
        assert ligne is not None, chemin.name
        assert ligne.gauche and ligne.gauche > 0, chemin.name
        assert ligne.droite and ligne.droite > 0, chemin.name


@pytest.mark.skipif(not ARCHIVE.exists(), reason="archive des bilans absente")
def test_les_pages_archivees_ont_un_total_coherent():
    """Le total publié doit valoir la somme des grandes catégories, sans les sous-lignes.

    Contrôle grossier mais utile : il tomberait si le parseur confondait les niveaux du
    tableau, par exemple en comptant les sous-lignes dans le total.
    """
    chemins = [ARCHIVE / f"{p.annee_droite}_{p.cumul}_{p.slug}.html" for p in PAGES]
    for chemin in (c for c in chemins if c.exists()):
        lignes = pps.parse_rows(pps.read_page(chemin))
        totaux = [x for x in lignes if x.label.lower().startswith("total")]
        assert totaux, chemin.name
        for total in totaux:
            assert total.droite and total.droite > 0, chemin.name
