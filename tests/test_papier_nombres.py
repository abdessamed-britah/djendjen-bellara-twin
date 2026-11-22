"""Traçabilité du papier : tout nombre de `paper/main.tex` figure dans `paper/NOMBRES.md`.

Le test ne juge pas que la correspondance est juste — c'est `NOMBRES.md` qui la donne, ligne
par ligne — mais qu'aucun nombre n'est entré dans le papier sans y être rattaché. Les
nombres de notation (exposants, indices des formules) sont dans les environnements
mathématiques et ne sont pas lus.
"""

import re
from pathlib import Path

PAPIER = Path(__file__).parent.parent / "paper"


def nombres_du_texte(source: str) -> list[str]:
    """Les nombres du corps du papier, hors commentaires, mathématiques et commandes."""
    t = re.sub(r"(?m)(?<!\\)%.*$", "", source)
    t = t[t.index(r"\begin{document}"):]
    t = re.sub(r"\\begin\{equation\}.*?\\end\{equation\}", "", t, flags=re.DOTALL)
    t = re.sub(r"\$[^$]*\$", lambda m: " ".join(re.findall(r"\d+\.\d+", m.group())), t)
    t = re.sub(r"\\begin\{tabular\}[^\n]*", "", t)
    t = re.sub(r"\\(label|ref|cite|url|input|includegraphics)(\[[^\]]*\])?\{[^}]*\}", "", t)
    t = re.sub(r"\\setlength\{[^}]*\}\{[^}]*\}", "", t)
    return re.findall(r"\d+(?:\.\d+)?", t)


def test_l_extraction_ignore_la_notation_et_garde_les_valeurs():
    source = (r"\begin{document} rises from 0.858 % commentaire 99" "\n"
              r"$T^{1/2}$ and $p = 0.492$ \cite{TODO-x} 20 times")
    assert nombres_du_texte(source) == ["0.858", "0.492", "20"]


def test_tout_nombre_du_papier_est_rattache_dans_nombres_md():
    texte = (PAPIER / "main.tex").read_text(encoding="utf-8")
    correspondances = (PAPIER / "NOMBRES.md").read_text(encoding="utf-8")
    connus = set(re.findall(r"\d+(?:[.,]\d+)?", correspondances))
    connus |= {n.replace(",", ".") for n in connus}
    absents = sorted(set(nombres_du_texte(texte)) - connus)
    assert absents == [], f"nombres du papier absents de NOMBRES.md : {absents}"


def test_nombres_md_ne_signale_aucun_chiffre_sans_correspondance():
    correspondances = (PAPIER / "NOMBRES.md").read_text(encoding="utf-8")
    assert "⚠️" not in correspondances
