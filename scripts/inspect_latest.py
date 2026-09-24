"""Affiche le texte et les tableaux du dernier PDF collecté.

Sert à préparer le parser de la semaine 2 : on regarde la structure réelle
du document avant d'écrire la moindre règle d'extraction.

Usage : python scripts/inspect_latest.py [DOSSIER]
"""

import sys
from pathlib import Path

import pdfplumber

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw/port_status")
pdfs = sorted(root.rglob("port_status_*.pdf"))
if not pdfs:
    sys.exit(f"Aucun PDF dans {root}. Lance d'abord : python -m corridor.ingest.port_status")

latest = pdfs[-1]
print(f"Fichier : {latest}\n")
with pdfplumber.open(latest) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        print(f"===== Page {i} =====")
        print(page.extract_text() or "(aucun texte : PDF scanné ?)")
        for j, table in enumerate(page.extract_tables(), 1):
            print(f"--- Tableau {j} : {len(table)} lignes ---")
            for row in table[:5]:
                print(row)
