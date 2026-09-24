"""Tests du parser de la situation portuaire, sur le PDF réel de tests/fixtures/.

Aucun accès réseau : tout part du PDF archivé le 2026-09-24 à 15:58:25 UTC.
Chaque piège listé dans CLAUDE.md a son test.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from corridor.transform import parse_status as psx

FIXTURE = Path(__file__).parent / "fixtures" / "port_status_20260924T155825Z.pdf"

# Heure de mise à jour du port : 2026-09-24 16:58 Africa/Algiers = 15:58 UTC.
SOURCE_TIME = datetime(2026, 9, 24, 15, 58, tzinfo=UTC)
# Heure de collecte, lue dans le nom du fichier (déjà en UTC).
FETCHED_AT = datetime(2026, 9, 24, 15, 58, 25, tzinfo=UTC)


@pytest.fixture(scope="module")
def observations():
    return psx.parse_pdf(FIXTURE)


# --- en-tête et horodatages ------------------------------------------------


def test_entete_heure_locale_convertie_en_utc():
    assert psx.parse_header("Djen-Djen : 2026-09-24 16:58\nPort Situation") == SOURCE_TIME


def test_entete_absente_leve_une_erreur():
    with pytest.raises(ValueError, match="en-tête"):
        psx.parse_header("Port Situation\nBerthed Vessels (12)")


def test_heure_de_collecte_lue_dans_le_nom_du_fichier():
    assert psx.fetched_at_from_name(FIXTURE) == FETCHED_AT


def test_nom_de_fichier_non_horodate_leve_une_erreur():
    with pytest.raises(ValueError, match="horodatage"):
        psx.fetched_at_from_name(Path("situation.pdf"))


# --- nettoyage des cellules ------------------------------------------------


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        (None, ""),
        ("MARSHALL\nISLANDS", "MARSHALL ISLANDS"),
        ("PORTE-\nCONTENEURS", "PORTE- CONTENEURS"),
        ("  SARL   I.S.M.S \n", "SARL I.S.M.S"),
        (
            "ALGERIA MARITIME\nSERVICES AGENCY\nALMARSA (LALOUI IMAD)",
            "ALGERIA MARITIME SERVICES AGENCY ALMARSA (LALOUI IMAD)",
        ),
    ],
)
def test_clean_cell(brut, attendu):
    assert psx.clean_cell(brut) == attendu


# --- tonnages --------------------------------------------------------------


@pytest.mark.parametrize(
    ("cargo", "tonnage", "libelle"),
    [
        # cas réels du PDF
        ("360,48Mt Bus", 360.48, "Bus"),                         # virgule, unité collée
        ("27229.383 MT DIVERS", 27229.383, "DIVERS"),            # point, trois décimales
        ("37170.63 MT DIVERS", 37170.63, "DIVERS"),
        ("29348,76 MT MDF\nBOARD", 29348.76, "MDF BOARD"),       # cellule multi-lignes
        ("27735.25 Mt Divers", 27735.25, "Divers"),              # unité en minuscules
        ("60236.54 Blé Dur", 60236.54, "Blé Dur"),               # aucune unité
        ("142533 MT IRON ORE IN\nBULK", 142533.0, "IRON ORE IN BULK"),  # entier
        ("244 TCS (4469.262 Mt)", 4469.262, "244 TCS"),          # tonnage entre parenthèses
        ("EMBT 9500 MT\nGRIGNON D'OLIVE", 9500.0, "EMBT GRIGNON D'OLIVE"),  # préfixe parasite
        ("140571 MT IRON ORE", 140571.0, "IRON ORE"),
        # cas synthétiques : unité avant le nombre, tonnage absent
        ("MT 12000 DIVERS", 12000.0, "DIVERS"),
        ("IRON ORE IN BULK", None, "IRON ORE IN BULK"),
        ("DIVERS", None, "DIVERS"),
        ("", None, ""),
        (None, None, ""),
    ],
)
def test_parse_tonnage(cargo, tonnage, libelle):
    valeur, texte = psx.parse_tonnage(cargo)
    assert texte == libelle
    if tonnage is None:
        assert valeur is None
    else:
        assert valeur == pytest.approx(tonnage)


# --- catégories de marchandise --------------------------------------------


@pytest.mark.parametrize(
    ("cargo", "categorie"),
    [
        ("142533 MT IRON ORE IN BULK", "iron_ore"),
        ("140571 MT IRON ORE", "iron_ore"),
        ("iron ore fines", "iron_ore"),                 # insensible à la casse
        ("PELLETS DE FER", "iron_ore"),
        ("13500 MT Pellet Feed", "iron_ore"),
        ("MINERAI DE FER", "iron_ore"),
        ("60236.54 Blé Dur", "grain"),                  # avec accent
        ("60236.54 BLE DUR", "grain"),                  # sans accent
        ("25000 MT WHEAT", "grain"),
        ("18000 MT ORGE", "grain"),
        ("244 TCS (4469.262 Mt)", "container"),
        ("120 CONTENEURS", "container"),
        ("27229.383 MT DIVERS", "general"),
        ("27735.25 Mt Divers", "general"),
        ("29348,76 MT MDF BOARD", "general"),
        ("360,48Mt Bus", "general"),
        ("EMBT 9500 MT GRIGNON D'OLIVE", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_categorize_cargo(cargo, categorie):
    assert psx.categorize_cargo(cargo) == categorie


def test_categorize_cargo_ignore_accents_et_casse():
    assert psx.categorize_cargo("blé dur") == psx.categorize_cargo("BLE DUR") == "grain"


# --- séjours anormalement longs -------------------------------------------


@pytest.mark.parametrize(
    ("status", "jours", "attendu"),
    [
        ("berthed", 213, True),     # le navire immobilisé depuis sept mois
        ("berthed", 61, True),
        ("berthed", 60, False),     # « plus de 60 jours » : 60 pile n'est pas un long séjour
        ("berthed", 2, False),
        ("anchorage", 213, False),  # l'attente en rade n'est pas un séjour à quai
        ("expected", 213, False),
    ],
)
def test_is_long_stay(status, jours, attendu):
    debut = SOURCE_TIME - timedelta(days=jours)
    assert psx.is_long_stay(status, debut, SOURCE_TIME) is attendu


def test_is_long_stay_sans_date_d_evenement():
    assert psx.is_long_stay("berthed", None, SOURCE_TIME) is False


# --- parsing du PDF réel ---------------------------------------------------


def test_trois_sections_et_nombre_de_navires(observations):
    compte = Counter(obs.status for obs in observations)
    assert compte == {"berthed": 12, "anchorage": 2}
    assert len(observations) == 14


def test_section_expected_arrivals_vide_ne_plante_pas(observations):
    # Le PDF contient « Expected Arrivals (0) » : un tableau réduit à son en-tête.
    assert [obs for obs in observations if obs.status == "expected"] == []


def test_horodatages_communs_a_toutes_les_lignes(observations):
    assert {obs.source_time_utc for obs in observations} == {SOURCE_TIME}
    assert {obs.fetched_at_utc for obs in observations} == {FETCHED_AT}


def test_premiere_ligne_a_quai_complete(observations):
    eagle = next(obs for obs in observations if obs.vessel == "EAGLE")
    assert eagle.status == "berthed"
    assert eagle.dock == "RR/1"
    assert eagle.flag == "MONGOLIE"
    assert eagle.shiptype == "CARGO"
    assert eagle.cargo_raw == "360,48Mt Bus"
    assert eagle.cargo_label == "Bus"
    assert eagle.cargo_category == "general"
    assert eagle.tonnage_t == pytest.approx(360.48)
    assert eagle.last_port == "HAIPHONG"
    # agent réparti sur trois lignes dans la cellule
    assert eagle.agent == "ALGERIA MARITIME SERVICES AGENCY ALMARSA (LALOUI IMAD)"
    assert eagle.event_time == datetime(2026, 2, 24, 21, 0, tzinfo=UTC)
    assert eagle.situation is None


def test_cellules_multi_lignes_aplaties(observations):
    anqifeng = next(obs for obs in observations if obs.vessel == "ANQIFENG")
    assert anqifeng.flag == "MARSHALL ISLANDS"
    assert anqifeng.agent == "SARL ISA SHIPPING TRADING AND LOGISTIC"

    rong_xiang = next(obs for obs in observations if obs.vessel == "RONG XIANG")
    assert rong_xiang.cargo_raw == "29348,76 MT MDF BOARD"
    assert rong_xiang.tonnage_t == pytest.approx(29348.76)

    acrux = next(obs for obs in observations if obs.vessel == "ACRUX AMELIA")
    assert acrux.last_port == "VANCOUVER (CA)"
    assert acrux.cargo_category == "grain"

    pantonio = next(obs for obs in observations if obs.vessel == "PANTONIO")
    assert pantonio.shiptype == "PORTE- CONTENEURS"
    assert pantonio.cargo_category == "container"
    assert pantonio.tonnage_t == pytest.approx(4469.262)
    # le tonnage part dans tonnage_t, le compte de conteneurs reste dans le libellé
    assert pantonio.cargo_label == "244 TCS"


def test_navires_en_rade(observations):
    rade = [obs for obs in observations if obs.status == "anchorage"]
    assert [obs.vessel for obs in rade] == ["GOLDEN BAY", "BERGE NIMBA"]
    golden_bay, berge_nimba = rade
    # la section Anchorage n'a pas de colonne Dock mais a une colonne Situation
    assert golden_bay.dock is None
    assert golden_bay.situation == "Waiting for berth"
    assert golden_bay.event_time == datetime(2026, 9, 14, 15, 55, tzinfo=UTC)
    assert golden_bay.cargo_raw == "EMBT 9500 MT GRIGNON D'OLIVE"
    assert golden_bay.cargo_category == "other"
    assert golden_bay.tonnage_t == pytest.approx(9500.0)
    assert berge_nimba.cargo_category == "iron_ore"
    assert berge_nimba.tonnage_t == pytest.approx(140571.0)
    assert berge_nimba.long_stay is False


def test_mineraliers_identifies(observations):
    mineraliers = [obs for obs in observations if obs.cargo_category == "iron_ore"]
    assert {obs.vessel for obs in mineraliers} == {"GH NIGHTINGALE", "BERGE NIMBA"}
    # ~140 000 t : ce sont ces navires qui alimentent le DRI d'AQS
    assert all(130_000 < obs.tonnage_t < 150_000 for obs in mineraliers)


def test_navire_a_quai_depuis_sept_mois_signale(observations):
    longs_sejours = [obs for obs in observations if obs.long_stay]
    assert [obs.vessel for obs in longs_sejours] == ["EAGLE"]
    # à quai depuis le 2026-02-24, soit plus de 200 jours : immobilisé, pas une escale
    assert (longs_sejours[0].source_time_utc - longs_sejours[0].event_time).days > 200
    # les treize autres navires restent exploitables pour les durées à quai
    assert sum(not obs.long_stay for obs in observations) == 13


def test_seuil_de_long_sejour_parametrable():
    court = psx.parse_pdf(FIXTURE, long_stay_days=10)
    # sept navires à quai depuis plus de dix jours (accostés avant le 2026-09-14 15:58 UTC)
    assert sum(obs.long_stay for obs in court) == 7


def test_tous_les_tonnages_du_pdf_sont_extraits(observations):
    assert all(obs.tonnage_t is not None for obs in observations)
    assert all(obs.cargo_category in psx.CARGO_CATEGORIES for obs in observations)


def test_cargo_label_est_le_libelle_sans_tonnage(observations):
    # le libellé ne doit plus contenir le tonnage, mais rester non vide
    labels = {obs.vessel: obs.cargo_label for obs in observations}
    assert labels["GH NIGHTINGALE"] == "IRON ORE IN BULK"
    assert labels["ACRUX AMELIA"] == "Blé Dur"
    assert labels["GOLDEN BAY"] == "EMBT GRIGNON D'OLIVE"
    assert all(obs.cargo_label for obs in observations)


def test_observations_immuables(observations):
    with pytest.raises((AttributeError, TypeError)):
        observations[0].vessel = "AUTRE"
