#!/usr/bin/env python
"""Refresh the bundled municipality table from the official ISTAT list.

    python scripts/update_reference_data.py [--source URL] [--input file.csv]

Keeps only the seven columns the plugin needs and re-encodes them to UTF-8. Nothing else
is modified: the values are the official ones.
"""

from __future__ import annotations

import argparse
import csv
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "territorial_suite" / "resources" / "reference" / "comuni_istat.csv"
DEFAULT_URL = ("https://www.istat.it/storage/codici-unita-amministrative/"
               "Elenco-comuni-italiani.csv")
USER_AGENT = "TerritorialSuite/0.1 (QGIS plugin reference data updater)"

COLUMNS = {
    "istat": "Codice Comune formato alfanumerico",
    "name": "Denominazione in italiano",
    "cadastral": "Codice Catastale del comune",
    "province": "Denominazione dell'Unit",
    "province_abbr": "Sigla automobilistica",
    "region": "Denominazione Regione",
    "nuts3": "Codice NUTS3 2024",
}


def download(url: str) -> bytes:
    """Download the ISTAT CSV."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - fixed URL
        return response.read()


def convert(raw: bytes) -> list:
    """Extract the columns the plugin needs."""
    text = raw.decode("latin-1")
    rows = list(csv.reader(text.splitlines(), delimiter=";"))
    if not rows:
        raise SystemExit("CSV vuoto")
    header = [column.replace("\n", " ").strip() for column in rows[0]]

    def index_of(fragment: str) -> int:
        for index, column in enumerate(header):
            if fragment.lower() in column.lower():
                return index
        raise SystemExit(f"Colonna non trovata: {fragment}")

    indexes = {key: index_of(fragment) for key, fragment in COLUMNS.items()}
    result = []
    for row in rows[1:]:
        if len(row) <= max(indexes.values()):
            continue
        istat = row[indexes["istat"]].strip()
        if not istat or not istat[0].isdigit():
            continue
        result.append([
            istat,
            row[indexes["name"]].strip(),
            row[indexes["cadastral"]].strip().upper(),
            row[indexes["province"]].strip(),
            row[indexes["province_abbr"]].strip().upper(),
            row[indexes["region"]].strip(),
            row[indexes["nuts3"]].strip(),
        ])
    result.sort(key=lambda item: item[0])
    return result


def main() -> int:
    """Download, convert and write the reference table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_URL)
    parser.add_argument("--input", default="", help="usa un CSV gia' scaricato")
    args = parser.parse_args()

    raw = Path(args.input).read_bytes() if args.input else download(args.source)
    rows = convert(raw)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGET, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow(list(COLUMNS.keys()))
        writer.writerows(rows)
    print(f"{TARGET}: {len(rows)} comuni, {TARGET.stat().st_size / 1024:.0f} KB")
    print("Ricordati di aggiornare la data in resources/reference/README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
