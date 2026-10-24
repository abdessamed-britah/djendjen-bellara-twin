"""Tests de `corridor.ingest.port_stats` : archivage des bilans de trafic. Hors ligne.

Aucun appel réseau : la session HTTP est remplacée par une fausse qui sert des octets
décidés dans le test. Ce qui est vérifié, c'est l'archivage — ne jamais écraser un brut,
tracer chaque fichier, et ne pas s'arrêter sur une page absente.
"""


import pytest

from corridor.ingest import port_stats as ps


class _FausseReponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _FausseSession:
    """Sert un contenu par URL ; toute URL inconnue lève, comme un 404."""

    def __init__(self, reponses: dict[str, bytes]):
        self.reponses = reponses
        self.appels: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.appels.append(url)
        if url not in self.reponses:
            return _FausseReponse(b"", status=404)
        return _FausseReponse(self.reponses[url])


PAGE = ps.StatsPage("bilan-test", "T1", 2024, 2025)
PDF_URL = "https://djendjen-port.dz/wp-content/uploads/2025/05/Data-Stats-1Trim-2025.pdf"
TARIF_URL = "https://djendjen-port.dz/wp-content/uploads/2026/08/CAHIER-DES-TARIFS-2026.pdf"
REVUE_URL = "https://djendjen-port.dz/wp-content/uploads/2024/08/DJENDJEN-info-N-26.pdf"


def _html(*liens: str) -> bytes:
    corps = "".join(f'<a href="{u}">doc</a>' for u in liens)
    return f"<html><body><table><tr><td>Pellets</td><td>1</td><td>2</td></tr></table>{corps}"\
        "</body></html>".encode()


# --- repérage des PDF joints ------------------------------------------------


def test_les_pdf_de_statistiques_sont_reperes():
    assert ps.attached_pdfs(_html(PDF_URL).decode()) == [PDF_URL]


def test_les_tarifs_et_la_revue_ne_sont_pas_des_statistiques():
    """Toutes les pages du site les portent : les archiver treize fois n'aurait aucun sens."""
    trouves = ps.attached_pdfs(_html(PDF_URL, TARIF_URL, REVUE_URL).decode())
    assert trouves == [PDF_URL]


def test_une_page_sans_pdf_n_en_invente_pas():
    assert ps.attached_pdfs(_html().decode()) == []


def test_les_doublons_sont_reduits():
    assert ps.attached_pdfs(_html(PDF_URL, PDF_URL).decode()) == [PDF_URL]


# --- archivage --------------------------------------------------------------


def test_une_page_et_son_pdf_sont_archives(tmp_path):
    session = _FausseSession({PAGE.url: _html(PDF_URL), PDF_URL: b"%PDF-1.4 faux"})
    lignes = ps.collect(tmp_path, (PAGE,), delay_s=0.0, session=session)
    assert [x["kind"] for x in lignes] == ["html", "pdf"]
    assert (tmp_path / "2025_T1_bilan-test.html").exists()
    assert (tmp_path / "2025_T1_bilan-test.pdf").read_bytes() == b"%PDF-1.4 faux"
    assert all(x["status"] == "ok" for x in lignes)


def test_le_manifeste_trace_empreinte_et_taille(tmp_path):
    contenu = _html()
    session = _FausseSession({PAGE.url: contenu})
    ps.collect(tmp_path, (PAGE,), delay_s=0.0, session=session)
    lignes = ps.read_manifest(tmp_path)
    assert len(lignes) == 1
    assert lignes[0]["sha256"] == ps.sha256(contenu)
    assert int(lignes[0]["bytes"]) == len(contenu)
    assert lignes[0]["cumul"] == "T1"
    assert lignes[0]["annee_gauche"] == "2024"


def test_un_brut_deja_archive_n_est_jamais_reecrit(tmp_path):
    """Règle du projet : un fichier brut archivé ne se réécrit pas."""
    chemin = tmp_path / "2025_T1_bilan-test.html"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(b"version d'origine")
    session = _FausseSession({PAGE.url: _html()})
    ps.collect(tmp_path, (PAGE,), delay_s=0.0, session=session)
    assert chemin.read_bytes() == b"version d'origine"


def test_une_page_absente_est_consignee_sans_arreter_la_collecte(tmp_path):
    autre = ps.StatsPage("bilan-present", "AN", 2023, 2024)
    session = _FausseSession({autre.url: _html()})  # PAGE est absente
    lignes = ps.collect(tmp_path, (PAGE, autre), delay_s=0.0, session=session)
    erreurs = [x for x in lignes if x["status"] != "ok"]
    assert len(erreurs) == 1
    assert erreurs[0]["slug"] == "bilan-test"
    assert (tmp_path / "2024_AN_bilan-present.html").exists()


def test_un_pdf_absent_n_empeche_pas_d_archiver_la_page(tmp_path):
    session = _FausseSession({PAGE.url: _html(PDF_URL)})  # le PDF renvoie 404
    lignes = ps.collect(tmp_path, (PAGE,), delay_s=0.0, session=session)
    assert (tmp_path / "2025_T1_bilan-test.html").exists()
    assert [x["status"] != "ok" for x in lignes] == [False, True]


def test_le_manifeste_s_ajoute_sans_ecraser(tmp_path):
    session = _FausseSession({PAGE.url: _html()})
    ps.collect(tmp_path, (PAGE,), delay_s=0.0, session=session)
    ps.collect(tmp_path, (PAGE,), delay_s=0.0, session=session)
    assert len(ps.read_manifest(tmp_path)) == 2


def test_manifeste_absent_donne_une_liste_vide(tmp_path):
    assert ps.read_manifest(tmp_path) == []


# --- cohérence de la table des pages ----------------------------------------


def test_chaque_cumul_est_publie_deux_fois_sauf_aux_extremites():
    """C'est ce recoupement qui permet de vérifier la lecture, il doit exister."""
    publications: dict[tuple[int, str], int] = {}
    for page in ps.PAGES:
        for annee in (page.annee_gauche, page.annee_droite):
            publications[(annee, page.cumul)] = publications.get((annee, page.cumul), 0) + 1
    doubles = [cle for cle, n in publications.items() if n == 2]
    assert len(doubles) >= 8


def test_les_cumuls_declares_sont_connus():
    assert {p.cumul for p in ps.PAGES} <= set(ps.CUMULS)


def test_les_annees_d_une_page_se_suivent():
    for page in ps.PAGES:
        assert page.annee_droite == page.annee_gauche + 1


def test_l_url_se_construit_depuis_le_slug():
    assert PAGE.url == "https://djendjen-port.dz/bilan-test/"


@pytest.mark.parametrize("annee", (2022, 2023, 2024, 2025))
def test_les_quatre_cumuls_de_chaque_exercice_sont_couverts(annee):
    """Sans les quatre cumuls, les trimestres d'une année ne peuvent pas être reconstruits."""
    couverts = {p.cumul for p in ps.PAGES
                if annee in (p.annee_gauche, p.annee_droite)}
    assert couverts == set(ps.CUMULS), f"{annee} : {couverts}"
