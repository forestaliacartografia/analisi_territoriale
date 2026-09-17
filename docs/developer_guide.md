# Guida per sviluppatori

## Architettura in una pagina

```
GUI (dock, dialoghi, palette)        PROCESSING (provider + 12 algoritmi)     ← adapter
------------------------------------------------------------------------
TASKS (QgsTask: progresso, cancel, errori)                                   ← orchestrazione
------------------------------------------------------------------------
ENGINES  admin · cadastre · constraints · terrain · download · rules         ← dominio
         analysis · report · package · cartography · project_layers
------------------------------------------------------------------------
SERVICES  http · ogc(wfs/ogcapi/raster/capabilities) · arcgis · overpass     ← I/O
          fetcher · vector_io · health
------------------------------------------------------------------------
CORE  project_area · registry · models · cache · provenance · crs · measure  ← modello
      geometry · settings · taxonomy · feedback · errors · paths · qt_compat
------------------------------------------------------------------------
CONFIG (JSON)  sources · rules · styles · layouts · taxonomy · defaults      ← dati
```

Regole verificate da `tests/unit/test_layering.py`:

- `core` non importa `services`, `engines`, `tasks`, `gui`, `processing`;
- `services` importa solo `core`; `engines` importano `core` + `services`;
- solo `gui/` (e `plugin.py`) usano `QtWidgets` o `iface`;
- Qt si importa **sempre** da `qgis.PyQt` (mai `PyQt5`/`PyQt6` diretti);
- i tipi dei campi si creano con `QMetaType.Type` tramite `core.qt_compat`;
- nessun URL di servizio nel codice: stanno in `config/sources/`.

## Contratti fondamentali

**Feedback** — ogni engine riceve un `core.feedback.Feedback` (progresso, step, log,
cancellazione) e non conosce ne' la GUI ne' Processing. Implementazioni: `NullFeedback`,
`ProcessingFeedback`, `TaskFeedback`, `ChildFeedback` (sotto-intervallo di progresso).

**Risultati serializzabili** — gli engine restituiscono dataclass di `core.models`
(JSON-friendly, geometrie in WKT). `AnalysisReport` e' l'artefatto centrale: alimenta
relazione, pacchetto, cartografia e GUI, e viene salvato come `analysis.json`.

**Thread** — i task producono risultati, il main thread li applica. Un task non tocca mai il
layer tree, il progetto o i widget.

## Aggiungere una sorgente

Nessun codice: un JSON in `config/sources/**` (vedi [data_sources.md](data_sources.md)).
Se il protocollo e' nuovo: aggiungi un client in `services/`, poi una riga in
`services/fetcher.client_for()` e un valore in `core.models.SourceType`.

## Aggiungere un engine

1. `engines/<nome>.py` con una classe che riceve `registry`, `http`, `cache` opzionali e
   un metodo `run(area, *, feedback=None)` che ritorna una dataclass serializzabile.
2. Se e' una fase dell'analisi: aggiungi `_step_<nome>` e il peso in
   `engines/analysis.py::_WEIGHTS`.
3. Esponilo in Processing (`processing/algs/`) e, se serve, nel dock.
4. Test: unit con doppi (`tests/fakes.FakeHttpClient`), integrazione con fixture registrate.
5. Documentazione: un file in `docs/` con la scheda delle 10 domande.

## Test

```bash
"C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" scripts\run_tests.py
"C:\Program Files\QGIS 4.0.0\bin\python-qgis.bat" scripts\run_tests.py
scripts\run_tests.py test_wfs_client.py       # un solo file
scripts\run_tests.py --network                # abilita gli scenari di rete
```

Il runner crea un profilo isolato (`TERRITORIAL_SUITE_HOME`, `TERRITORIAL_SUITE_CACHE`) e
avvia QGIS headless: i test non sporcano il profilo dell'utente.

Struttura: `tests/unit` (senza rete), `tests/integration` (fixture registrate in
`tests/fixtures`), `tests/qgis` (layout, export, import di tutti i moduli),
`tests/scenarios` (casi reali, marcati `@requires_network`).

## Trappole note (non ripeterle)

| Trappola | Regola |
|---|---|
| GDAL: band viva dopo il dataset | rilascia sempre band → dataset, in quest'ordine; chiudi i dataset di `Warp`/`BuildVRT`/`DEMProcessing`/`ContourGenerate` |
| lxml + GDAL (libxml2 doppia) | `OPENPYXL_LXML=False` prima di importare openpyxl (`core.compat_guards`) |
| GeoPackage "update mode failed" | usa `CreateOrOverwriteLayer` solo se il file esiste; non riaprire il file per contare le feature |
| GML INSPIRE + OGR | rimuovi `gml:boundedBy` prima di passare il file a OGR, altrimenti i campi si disallineano |
| `QgsProject()` autonomo | svuotalo (`removeAllMapLayers()` + `clear()`) prima che venga distrutto |
| WFS: pagina corta ≠ fine dei dati | segui il link `next` |
| Colori `#RRGGBBAA` | Qt legge nove caratteri come `#AARRGGBB`: fai il parsing dell'alfa |
| Enum Qt | usa sempre la forma *scoped* (`Qt.AlignmentFlag.AlignLeft`): obbligatoria in Qt6 |

## Packaging e rilascio

```bash
python scripts/package.py                    # dist/territorial_suite-<versione>.zip
"…python-qgis-ltr.bat" scripts/validate_sources.py --network
python scripts/update_reference_data.py      # tabella ISTAT dei Comuni
```

Prima di un rilascio: versione allineata fra `metadata.txt` e `core/constants.py` (lo
verifica `package.py`), changelog aggiornato, suite verde su 3.40 LTR **e** 4.0.
