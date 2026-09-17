# Bundled reference data

## `comuni_istat.csv`

Lookup table of Italian municipalities used to translate between the identifiers that
different services speak: ISTAT code, municipality name, **cadastral code** (the code the
Agenzia delle Entrate INSPIRE service returns in `ADMINISTRATIVEUNIT`), province and region.

| column | meaning |
|---|---|
| `istat` | ISTAT municipality code (6 chars, alphanumeric form) |
| `name` | Italian name |
| `cadastral` | cadastral code (e.g. `D612` = Firenze) |
| `province` | supra-municipal territorial unit (province / metropolitan city) |
| `province_abbr` | vehicle registration code (e.g. `FI`) |
| `region` | region name |
| `nuts3` | NUTS3 2024 code |

* Source: ISTAT — *Elenco dei codici e delle denominazioni delle unità territoriali*
  (`https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv`).
* Retrieved and converted: 2026-09-16 (7896 municipalities).
* Licence: ISTAT open data, CC BY 4.0 — attribution: "Istat".
* Conversion: original semicolon CSV (latin-1) re-encoded to UTF-8, keeping only the seven
  columns above; no value was altered.

The table contains **no geometry**: it is an identifier dictionary, not a boundary dataset.
Boundaries are obtained from the configured administrative sources
(`config/sources/**`, category `administrative`).

To refresh it, run:

```bash
python scripts/update_reference_data.py
```
