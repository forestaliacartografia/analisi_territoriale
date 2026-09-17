# Analisi territoriale

**QGIS territorial intelligence & cartographic production suite** — define an area, get the
knowledge framework, produce the maps.

    ANALIZZARE → COMPRENDERE → DOCUMENTARE → CARTOGRAFARE

Analisi territoriale turns a geographic area into a documented answer: it queries a
configurable catalogue of official services (OGC WFS/WMS/WMTS/XYZ, OGC API Features, ArcGIS
REST, the INSPIRE cadastral service of the Agenzia delle Entrate, elevation tiles,
OpenStreetMap), measures what interacts with the area, produces organised layers, print
layouts and a territorial dossier — with the provenance of every single datum.

| | |
|---|---|
| **QGIS** | 3.40 LTR → 4.0 (Qt5 and Qt6, tested on both) |
| **Dependencies** | none beyond what QGIS ships (PyQGIS, GDAL, numpy; openpyxl optional) |
| **Licence** | GPL-3.0-or-later |
| **Status** | 0.1.0 — experimental, MVP complete end to end |

---

## What it does

| Workflow | What you get |
|---|---|
| **Project Area** | draw, rectangle, circle, freehand, from layer/selection/file/coordinates/BBOX, or from the selected cadastral parcels — stored in the QGIS project and as a portable `.tsa.json` |
| **One-click analysis** | administrative units → cadastre → constraints → terrain → rules, in background, cancellable |
| **Cadastre** | parcels and sheets intersecting the area, per municipality, with intersected surface and percentage; CSV/XLSX export |
| **Constraints** | presence, intersected surface, % of the area, minimum distance, key attributes, source, authority, date, scale, licence, **nature of the data** |
| **Terrain** | DEM download and clipping, elevation statistics, slope (with configurable classes), aspect, hillshade, contours, elevation profile |
| **Download area** | roads, hydrography, trails, buildings, places, infrastructure → one organised GeoPackage, clipped and styled |
| **Cartography** | quick map and 12 configurable layout templates, automatic scale, smart legend, north arrow, grid, sources and date; map series; PDF/PNG/JPEG/SVG export with numbered file names |
| **Report & package** | 16-section dossier (HTML/PDF), tables (CSV/XLSX), and a delivery folder with data, maps, `project.qgz` and a manifest of sources |
| **Processing** | 12 algorithms mirroring the GUI, usable in models, batch and `qgis_process` |

## The rule that shapes the whole plugin

> **A map is not a legal finding.**

Every source declares its `evidence_level` (*cartographic* / *declaratory* /
*referred to an administrative act*), every alert is a semantic classification
(INFORMAZIONE / ATTENZIONE / VERIFICA / CRITICITÀ CARTOGRAFICA) and never a verdict, and the
dossier states the difference explicitly. When a source is unavailable, the report says so:
"no data" and "no constraint" are different answers.

## Install

**From a ZIP** (until it is published on the QGIS plugin repository):

```bash
python scripts/package.py
```

then in QGIS: *Plugins → Manage and Install Plugins → Install from ZIP* and pick
`dist/territorial_suite-<version>.zip`.

**From source**: copy (or symlink) the `territorial_suite/` folder into your QGIS profile
`python/plugins/` directory and enable it in the plugin manager.

## First run

1. **Create the project area** — the `Crea` menu of the dock.
2. **Check the sources** — *Dati → Sorgenti* shows the catalogue and the service status.
3. **Run the analysis** — *Analisi territoriale (one-click)*.
4. **Produce the cartography** — *Crea mappa*, *Tavola ortofoto*, *Serie di tavole*,
   *Relazione*, *Pacchetto*.
5. **Make it yours** — *Impostazioni → Layout e relazioni*: logos, fixed texts, legend
   mode, north arrow and the orthophoto provider, all without touching the code.

`Ctrl+Shift+T` opens the command palette.

## Configuration, not code

Everything that can change without a release lives in `territorial_suite/config/` and can be
overridden per profile in `<QGIS profile>/territorial_suite/`:

```
config/
├── sources/      data-source descriptors (JSON) by scope: national, european, regional, ...
├── rules/        alert rules (JSON)
├── styles/       cartographic styles (JSON + optional .qml)
├── layouts/      layout templates, map series and sheet profiles (logos, texts, legend)
└── defaults.json thresholds, timeouts, scales, classes
```

Adding a regional WFS means dropping one JSON file — see
[`docs/data_sources.md`](docs/data_sources.md) and `config/sources/regional/_TEMPLATE.json`.

## Documentation

* [Getting started](docs/getting_started.md)
* [Project area](docs/project_area.md) · [Constraints](docs/constraints.md) ·
  [Cadastre](docs/cadastre.md) · [Terrain](docs/terrain.md)
* [Data sources](docs/data_sources.md) · [Downloads](docs/downloads.md)
* [Cartography](docs/cartography.md) · [Reports](docs/reports.md)
* [Cultural heritage / MiC](docs/CULTURAL_HERITAGE.md) ·
  [Layout engine and orthophoto](docs/LAYOUT_ENGINE.md) ·
  [Verified source matrix](docs/SOURCE_MATRIX.md)
* [Configuration](docs/configuration.md) · [Troubleshooting](docs/troubleshooting.md)
* [Developer guide](docs/developer_guide.md) · [Design document](docs/DESIGN.md)

## Tests

```bash
"C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" scripts\run_tests.py
"C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat" scripts\run_tests.py
```

223 tests, no network required (recorded fixtures), green on both QGIS versions. Add
`--network` to also run the scenarios that hit the real services.

## Credits and data

The plugin ships identifiers only (ISTAT municipality table, CC BY 4.0); every geographic
dataset stays with its own provider and is attributed in the layer metadata, in the map and
in the report. See `territorial_suite/resources/reference/README.md` and the `license` and
`attribution` fields of each source descriptor.
