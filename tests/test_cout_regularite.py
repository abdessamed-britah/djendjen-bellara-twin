"""Tests de scripts/cout_regularite.py : arbitrage lissage / stockage le long de la frontière.

Le danger de ce script est de produire un optimum faux et crédible : un coût de lissage
compté à l'envers, un rapport de coûts mal normalisé, un minimum cherché sur la mauvaise
colonne. C'est cette arithmétique-là qui est testée, sur des frontières fabriquées dont la
réponse se calcule à la main.
"""

import importlib.util
from pathlib import Path

import pytest

from corridor.sim.config import ASSUMPTIONS_PATH, load_yaml

_SPEC = importlib.util.spec_from_file_location(
    "cout_regularite", Path(__file__).parent.parent / "scripts" / "cout_regularite.py"
)
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

#: Frontière factice : plus les arrivées sont irrégulières, plus il faut de couverture.
FRONTIERE = [(0.0, 10.0), (0.5, 30.0), (1.0, 60.0)]


# --- coût du lissage --------------------------------------------------------


def test_le_lissage_coute_par_navire_et_par_point():
    """Ramener c_a² de 0,8 à 0,3 sur 40 navires à 1 000 USD le point : 0,5 × 40 × 1 000."""
    assert cr.cout_lissage(0.3, 0.8, 40.0, 1000.0) == pytest.approx(20_000.0)


def test_rester_a_l_etat_actuel_ne_coute_rien():
    assert cr.cout_lissage(0.8, 0.8, 40.0, 1000.0) == 0.0


def test_laisser_l_affretement_se_degrader_ne_rapporte_rien():
    """Un c_a² visé plus élevé que l'actuel ne doit pas générer un gain imaginaire."""
    assert cr.cout_lissage(1.2, 0.8, 40.0, 1000.0) == 0.0


def test_le_lissage_coute_d_autant_plus_qu_on_vise_bas():
    couts = [cr.cout_lissage(c, 1.0, 40.0, 1000.0) for c in (1.0, 0.75, 0.5, 0.0)]
    assert couts == sorted(couts)
    assert couts[0] == 0.0


# --- coût du stockage -------------------------------------------------------


def test_le_stockage_coute_par_kt_et_par_an():
    """20 jours × 10 000 t/jour = 200 kt de capacité, à 4 000 USD/kt/an."""
    assert cr.cout_stockage(20.0, 10_000.0, 4_000.0) == pytest.approx(800_000.0)


def test_une_couverture_nulle_ne_coute_rien():
    assert cr.cout_stockage(0.0, 10_000.0, 4_000.0) == 0.0


def test_le_stockage_est_proportionnel_a_la_couverture():
    simple = cr.cout_stockage(10.0, 10_000.0, 4_000.0)
    double = cr.cout_stockage(20.0, 10_000.0, 4_000.0)
    assert double == pytest.approx(2 * simple)


# --- lecture de la frontière ------------------------------------------------


def test_les_points_sans_couverture_atteignable_sont_ecartes(monkeypatch):
    """Là où β n'est atteint à aucun prix, il n'y a pas de couple à chiffrer."""
    monkeypatch.setattr(cr, "couverture_requise",
                        lambda grille, beta: {0.0: 10.0, 0.5: 30.0, 1.0: None})
    assert cr.points_frontiere({}, 0.95) == [(0.0, 10.0), (0.5, 30.0)]


def test_les_points_sont_tries_par_c_a2(monkeypatch):
    monkeypatch.setattr(cr, "couverture_requise",
                        lambda grille, beta: {1.0: 60.0, 0.0: 10.0, 0.5: 30.0})
    assert [c for c, _ in cr.points_frontiere({}, 0.95)] == [0.0, 0.5, 1.0]


# --- décomposition et minimum -----------------------------------------------


def test_la_decomposition_somme_au_total():
    lignes = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0, 1000.0, 4000.0)
    for x in lignes:
        assert x["total"] == pytest.approx(x["lissage"] + x["stockage"])


def test_le_minimum_est_bien_le_plus_petit_total():
    lignes = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0, 1000.0, 4000.0)
    assert cr.minimum(lignes)["total"] == min(x["total"] for x in lignes)


def test_un_stockage_tres_cher_pousse_vers_la_regularite():
    lignes = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0,
                                    prime=1.0, cout_kt=1e6)
    assert cr.minimum(lignes)["c_a2"] == 0.0


def test_un_lissage_tres_cher_pousse_vers_le_stockage():
    lignes = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0,
                                    prime=1e9, cout_kt=1.0)
    assert cr.minimum(lignes)["c_a2"] == 1.0


# --- sensibilité au rapport des coûts ---------------------------------------


def test_seul_le_rapport_des_couts_compte():
    """Multiplier les deux coûts par la même constante ne doit pas déplacer l'optimum.

    C'est la propriété qui autorise à résumer tout le problème par un seul nombre sans
    dimension, et donc tout le panneau de droite de la figure.
    """
    petit = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0, 2.0, 8.0)
    grand = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0, 2000.0, 8000.0)
    assert cr.minimum(petit)["c_a2"] == cr.minimum(grand)["c_a2"]


def test_l_optimum_se_deplace_vers_la_regularite_quand_le_rapport_monte():
    """Plus le stockage est cher devant le lissage, plus l'optimum glisse vers la gauche."""
    resultats = cr.optimum_selon_ratio(FRONTIERE, 1.0, 40.0, 10_000.0,
                                       (1e-6, 1e-3, 1.0, 1e3, 1e6))
    c_a2 = [c for _, c in resultats]
    assert c_a2 == sorted(c_a2, reverse=True)
    assert c_a2[0] == 1.0
    assert c_a2[-1] == 0.0


def test_le_balayage_couvre_plusieurs_ordres_de_grandeur():
    """Un balayage trop étroit donnerait l'illusion d'un optimum insensible aux coûts."""
    assert min(cr.RATIOS) <= 1e-3
    assert max(cr.RATIOS) >= 1e2
    assert len(cr.RATIOS) >= 20


def test_le_rapport_le_plus_proche_se_mesure_en_log():
    """Sur une échelle logarithmique, 0,03 est plus proche de 0,01 que de 0,1."""
    assert cr._ratio_le_plus_proche((0.01, 0.1, 1.0), 0.028) == 0.01
    assert cr._ratio_le_plus_proche((0.01, 0.1, 1.0), 0.04) == 0.1


# --- hypothèses de coût -----------------------------------------------------


def test_les_deux_couts_existent_et_sont_explicitement_sans_source():
    """Ils doivent rester `estimé` : les publier autrement serait les faire passer pour sourcés."""
    couts = load_yaml(ASSUMPTIONS_PATH)["couts"]
    for cle in ("prime_lissage_affretement", "cout_stockage_annualise"):
        assert couts[cle]["status"] == "estimé"
        assert "source" not in couts[cle]
        assert couts[cle]["range"][0] < couts[cle]["value"] < couts[cle]["range"][1]


def test_les_fourchettes_couvrent_au_moins_un_ordre_de_grandeur():
    couts = load_yaml(ASSUMPTIONS_PATH)["couts"]
    for cle in ("prime_lissage_affretement", "cout_stockage_annualise"):
        bas, haut = couts[cle]["range"]
        assert haut / bas >= 10, cle


# --- sorties ----------------------------------------------------------------


def test_les_tableaux_s_affichent_sans_lever(capsys):
    lignes = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0, 1000.0, 4000.0)
    cr.print_table(lignes, 1.0, cr.minimum(lignes))
    cr.print_ratios(cr.optimum_selon_ratio(FRONTIERE, 1.0, 40.0, 10_000.0, cr.RATIOS), 1.0)
    sortie = capsys.readouterr().out
    assert "Coût annuel le long de la frontière" in sortie
    assert "minimum" in sortie


def test_la_figure_est_un_png_non_vide(tmp_path):
    lignes = cr.couts_sur_frontiere(FRONTIERE, 1.0, 40.0, 10_000.0, 1000.0, 4000.0)
    resultats = cr.optimum_selon_ratio(FRONTIERE, 1.0, 40.0, 10_000.0, cr.RATIOS)
    chemin = cr.write_figure(lignes, resultats, cr.minimum(lignes), 0.83, 0.08,
                             0.95, 1, tmp_path / "cout.png")
    assert chemin.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert chemin.stat().st_size > 10_000
