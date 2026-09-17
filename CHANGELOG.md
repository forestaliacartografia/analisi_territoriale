# Changelog

All notable changes to Analisi territoriale (package `territorial_suite`) are
documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses
[semantic versioning](https://semver.org/).

## [0.1.1] - 2026-09-17

Rilascio di sicurezza e confezionamento. **Nessuna modifica al comportamento**: non
cambia un solo risultato prodotto dal plugin.
Suite: **395 test verdi** su QGIS 3.40.15 LTR e QGIS 4.0.0, 0 falliti, 16 saltati.
Bandit: 0 rilievi a ogni severita'. Flake8: 0 E741.

### Corretto

- **`LICENSE` viaggia dentro il pacchetto.** Era solo nella radice del repository, ma
  l'archivio contiene la sola cartella `territorial_suite/`, che e' tutto cio' che
  l'utente installa. Il caricamento veniva rifiutato con «Cannot find LICENSE in the
  plugin package». `scripts/package.py` ora si rifiuta di costruire l'archivio se manca.
- **Link dei metadati.** `repository`, `tracker` e `homepage` puntavano a un segnaposto
  (`github.com/example/...`) irraggiungibile; `repository` e `tracker` sono obbligatori.
- **19 gestori di eccezione silenziosi** (`except Exception: pass` / `continue`).
  Violavano la regola C4 del progetto: ciascuno ora registra cosa e' stato saltato e
  perche'.

### Sicurezza

- **XML non fidato.** Le capabilities arrivano dall'host indicato da un descrittore.
  Misurato su Python 3.12: `xml.etree` rifiuta le entita' esterne ma espande quelle
  interne, sufficiente a costruire una bomba di espansione. I documenti OGC sono basati
  su XSD e non portano mai una DTD: una dichiarazione `DOCTYPE` o `ENTITY` viene ora
  rifiutata prima del parsing. `defusedxml` e' usato se la distribuzione lo fornisce.
- **SQL della cache.** Le tre segnalazioni di «injection» erano falsi positivi, ma le
  istruzioni sono ora stringhe costanti con parametri che neutralizzano i filtri vuoti:
  non resta SQL dinamico da verificare.
- **SHA-1 della chiave di cache** dichiarato `usedforsecurity=False`: non e' una
  primitiva di sicurezza, e cosi' il plugin resta installabile in modalita' FIPS.

### Aggiunto

- `tests/unit/test_capabilities_xml.py` — 6 test sulla difesa XML, bomba di espansione
  ed entita' esterna incluse.
- `tests/unit/test_no_silent_failures.py` — fa fallire la suite se un gestore ampio
  torna muto.

### Limitazioni note

- 12 gestori ristretti a eccezioni specifiche scartano comunque l'errore, e 19 gestori
  ampi non legano l'eccezione con `as`. Bandit non li segnala; non sono stati toccati per
  non allargare la modifica a ridosso del rilascio.

## [Non rilasciato] - Phase 0 + P0

Audit di riferimento e correzione dei difetti bloccanti. Lavoro poi rilasciato in 0.1.1.
Suite: **365 test verdi** su QGIS 3.40.15 LTR e QGIS 4.0.0 (baseline Phase 0: 327, +38),
0 falliti su entrambe.

### Cambiato

- **Nome visibile: «Analisi territoriale».** Cambia `PLUGIN_NAME`, e con esso menu, dock,
  titoli delle finestre, gruppo Processing, canale di log, gruppo nell'albero dei layer,
  pie' di pagina delle tavole e della relazione, e `metadata.txt`.
  **L'identita' tecnica non si muove**: `PLUGIN_ID`, il package Python, le chiavi
  `QgsSettings`, le proprieta' `PROP_*` dei layer e gli id degli algoritmi Processing
  restano `territorial_suite`, perche' spostarli romperebbe i progetti QGIS esistenti,
  le impostazioni utente e i modelli gia' salvati.
- **Icona**: l'icona fornita dall'utente (`Icon_plugin_vincoli.png`, 1254x1254) e'
  installata come `resources/icons/analisi_territoriale.png` a 128x128 e collegata a
  metadata, barra degli strumenti, menu, provider Processing e pannello.

### Corretto

- **La tavola ortofoto non conteneva alcuna ortofoto.** Causa primaria: il pannello
  eseguiva l'intero `OrthophotoEngine.run()` dentro un `QgsTask`, e quella chiamata
  creava `QgsRasterLayer` e `QgsPrintLayout` fuori dal thread principale — operazione
  illegale in QGIS. Il motore e' ora diviso in due: `prepare()` fa **solo rete** e
  restituisce un piano serializzabile (worker), `compose()` crea gli oggetti QGIS (thread
  principale). Un test strutturale legge la closure del worker e rifiuta ogni chiamata che
  vi costruirebbe un oggetto QGIS.
- Il layer dell'ortofoto raggiungeva il progetto **senza** `PROP_LAYER_CATEGORY` e
  `PROP_LAYER_SOURCE_ID`, quindi `layers_for()` non lo riconosceva e il riquadro di mappa
  restava senza sfondo. Ora e' marcato, timbrato con la provenance e collocato in fondo,
  con l'area di progetto sopra.
- Una tavola senza raster e' ora un **errore** (`LayoutError`), non un ritorno silenzioso:
  un foglio il cui sfondo e' fallito non si distingue da un territorio vuoto.
- **Pendenza ed esposizione erano piatte.** La rampa dell'esposizione era grigia a due
  stop: impossibile distinguere nord da sud. Ora sono rampe **discrete a valori assoluti**
  — 8 settori cardinali per l'esposizione (con l'avvolgimento a 360 gradi) e classi di
  pendenza in percentuale — cosi' che tavole di aree diverse restino confrontabili.
- **Il rilievo ombreggiato non arrivava in stampa.** Sul canvas l'ombreggiatura ora
  moltiplica sopra il colore (`CompositionMode_Multiply`, opacita' configurabile), e
  poiche' QGIS perde i blend mode in diversi percorsi di export il motore del terreno
  **cuoce un GeoTIFF composito** (`dem_shaded.tif`, `slope_shaded.tif`,
  `aspect_shaded.tif`) che layout e pacchetto usano al posto del tema grezzo. La
  provenance registra DEM, azimut, altezza, rampa e algoritmo.

### Aggiunto

- `docs/FEATURE_MATRIX.md` e `docs/ARCHITECTURE.md`: stato misurato del codice, grafo
  delle dipendenze, contratti, registro dei rischi e baseline di riferimento.
- `core/errors.LayoutError`.
- `styling.apply_shaded_relief()`, `ramp_spec()`, `ramp_lookup()`, `ramp_for_style()`,
  `has_ramp()`; `terrain.bake_shaded_relief()`; `project_layers.shade_terrain()`.
- `terrain.hillshade_opacity`, `hillshade_azimuth`, `hillshade_altitude`,
  `hillshade_z_factor`, `shaded_relief` in `config/defaults.json`: i parametri
  dell'ombreggiatura non sono piu' scritti nel codice.
- `tests/unit/test_identity.py`, `tests/qgis/test_orthophoto_sheet.py`,
  `tests/qgis/test_terrain_styling.py`.

## [Non rilasciato] - Fase 3

Modulo **Cultural Heritage / MiC**, motore **ortofoto** e **profili di layout**.
Suite: **319 test verdi** su QGIS 3.40.15 LTR e QGIS 4.0.0 (baseline 243, +76).

### Aggiunto

**Patrimonio culturale**
- Modulo `engines/cultural_heritage`: normalizzazione, classificazione, deduplicazione,
  determinazione della Soprintendenza competente, valutazione della qualita' del dato e
  sezione dedicata del dossier (14.1-14.12).
- Quattro classificazioni tenute distinte - `DATA_PRESENT`, `AREA_INTERSECTS_DATA`,
  `OFFICIAL_INFORMATION`, `LEGAL_REFERENCE_PRESENT` - e sei esiti distinti per un tema
  vuoto, cosi' che «nessun bene trovato» non venga mai confuso con «non esistono beni».
- 13 fonti SITAP operative e 5 censite come non interrogabili (vedi
  `docs/SOURCE_MATRIX.md`), tutte verificate con richieste reali il 2026-09-17.
- Lettura dei 144 layer regionali SITAP dal nome del feature type, senza descrittori a
  mano; i nomi non riconosciuti vengono scartati invece che archiviati a caso.
- Sei regole specifiche per art. 136, art. 142, beni culturali, archeologia e UNESCO, con
  `"match": "exact"` per evitare che la regola generica si sommi a quelle specifiche.

**Cartografia**
- Motore ortofoto con scelta del fornitore (`auto` / `esri` / `google`), fallback
  dichiarato **sulla tavola** e provenienza completa dell'immagine usata.
- Supporto Google tramite la **Map Tiles API ufficiale** con chiave dell'utente
  (createSession + viewport + 2dtiles); nessuno scraping. Le tile non vengono mai stampate
  senza la stringa di attribuzione richiesta.
- Chiavi API conservate nel database di autenticazione cifrato di QGIS
  (`core/credentials.py`), mai in chiaro nelle impostazioni; alternativa via variabile
  d'ambiente.
- Verifica di una tile reale prima di dichiarare utilizzabile un fornitore: un layer XYZ e'
  «valido» anche se l'host non esiste.
- Profili di layout (`config/layouts/profiles.json`): pagina, margini, corpi, loghi
  multipli, immagini con didascalia, legenda (auto/tematica/personalizzata/assente, con
  rinomine, filtri, ordine e colonne), rosa dei venti configurabile, testi con segnaposto,
  blocchi ridefinibili. Sei profili forniti; duplicazione, eliminazione, esportazione e
  importazione dall'interfaccia.
- Workflow one-click `Tavola ortofoto` nel pannello e in Processing.
- Tre nuovi algoritmi Processing: `cultural_heritage`, `orthophoto_sheet`,
  `imagery_providers`.
- Scheda **Layout e relazioni** nelle impostazioni.

### Corretto

- **Feature perse in silenzio nel client WFS.** Un servizio che annuncia
  `numberReturned="3"` e le cui geometrie GML GDAL non riesce a leggere produceva «0
  feature», indistinguibile da «in quest'area non c'e' nulla». Il client confronta ora
  l'annuncio con le feature effettivamente lette: se ne perde una parte lo segnala, se le
  perde tutte solleva un errore di schema. (Osservato sui layer UNESCO poligonali SITAP.)
- **Record duplicati dalla fonte.** Alcune viste SITAP pubblicano lo stesso poligono due
  volte con `gml_id` diversi: i doppioni esatti vengono scartati e il dossier dichiara
  quanti e da quale fonte.
- **`scripts/validate_sources.py --network` andava in segmentation fault**: `bootstrap()`
  restituiva la `QgsApplication` ma il riferimento veniva scartato, il garbage collector
  distruggeva l'oggetto C++ e la prima richiesta di rete faceva crashare il processo.
- **`aws.terrain_tiles.geotiff` non aveva sonda di disponibilita'** e risultava sempre
  `UNKNOWN`: aggiunta la sonda per `TERRAIN_TILES`; solo `OFFLINE` conta come fallimento.
- Gli stili ereditano ora lungo la gerarchia della tassonomia: una sotto-categoria senza
  stile proprio usa quello della sua macro-categoria invece di restare senza.
- I filtri per categoria di download e albero dei layer riconoscono le sotto-categorie.

### Modificato

- `AnalysisReport` ha un campo `modules` per gli esiti dei motori tematici, cosi' che
  `core` non debba conoscere `engines`.
- `Provenance` porta natura del dato, stato di verifica e nota di copertura fino al dossier.
- La sezione del dossier sul patrimonio si disattiva da *Impostazioni > Report*.

## [0.1.0] - 2026-09-16

First end-to-end release (experimental): the MVP of `docs/DESIGN.md` section O is complete
and verified on QGIS 3.40 LTR and QGIS 4.0.

### Added

**Core**
- `ProjectArea` with creation from drawing, rectangle, circle, freehand, layer, selection,
  file (SHP/GPKG/GeoJSON/KML), coordinates, bounding box and cadastral parcels; persistence
  in the QGIS project and as a portable `.tsa.json`.
- Ellipsoidal measurement, automatic metric work CRS (UTM), shape metrics, robust geometry
  repair.
- Data-source registry reading JSON descriptors (built-in, per profile and per project),
  with territorial scope resolution and per-project overrides.
- Disk cache with TTL, bbox quantisation, quota and LRU eviction; data provenance written
  into layer metadata and custom properties.
- Bundled ISTAT municipality table (ISTAT code ↔ name ↔ cadastral code ↔ province ↔ region).

**Services**
- HTTP client on the QGIS network stack: retries with backoff, per-host throttling,
  detection *and retry* of OGC exceptions returned with HTTP 200.
- WFS (KVP, paging driven by the `next` link, axis-order handling), OGC API Features,
  ArcGIS REST, Overpass/OSM, whole-file sources, WMS/WMTS/XYZ layer building, health checks.
- GML sanitising: `gml:boundedBy` is stripped before handing the payload to OGR, which
  otherwise misaligns every attribute of INSPIRE feature types.

**Engines**
- Administrative resolution (cadastral zoning first, OSM boundaries as fallback).
- Cadastre: parcels and sheets, per-municipality table, graphic surfaces, GeoPackage output.
- Constraints: presence, intersected surface and percentage, minimum distance, per-feature
  detail, evidence level, provenance.
- Terrain: DEM tiles download, clipping, elevation statistics, slope with configurable
  classes, aspect, hillshade, contours, elevation profile.
- Download manager, rule engine (declarative JSON), analysis orchestrator, report engine
  (HTML/PDF/CSV/XLSX), package builder (data + maps + `project.qgz` + manifest).
- Cartography: styling engine, automatic scale, layout builder with 12 templates, smart
  legend, map series, export centre.

**Interfaces**
- Dock with five workflows, background tasks with progress and cancel, results panel
  (summary, alerts, constraints, cadastre, terrain, sources), source manager, settings,
  command palette (`Ctrl+Shift+T`), onboarding.
- Processing provider with 12 algorithms mirroring the GUI.

**Quality**
- 223 automated tests (unit, integration on recorded fixtures, QGIS-in-the-loop, GUI smoke
  tests on the offscreen platform, Processing provider execution) green on QGIS 3.40 LTR and
  4.0, including an architecture test that enforces the layering rules and forbids hardcoded
  service URLs, plus ten real-world scenarios (two of which run offline by design).
- Packaging, catalogue validation and reference-data update scripts.

### Fixed during development (worth recording)

- **BBOX precision.** The Italian cadastral WFS answers *"Richiesta non valida"* to a
  bounding box written with eight decimals ending in zeros (`43.76900000`) and accepts the
  very same box with six (`43.769000`) — measured 0/8 versus 5/5 successes. Bounding boxes
  are now formatted with 6 decimals for geographic CRS and 2 for metric ones, without
  trailing zeros; the failure rate of the cadastral step went from systematic to none, and
  the request time from ~10 s (retries) to 0.5 s.

- GDAL lifetime: raster bands are released before their dataset, and the datasets returned
  by `BuildVRT`/`Warp`/`DEMProcessing`/`ContourGenerate` are flushed and dropped explicitly.
  Keeping a band alive after its dataset corrupts the heap and crashes QGIS later, in
  unrelated code.
- openpyxl is forced onto the standard-library XML backend (`OPENPYXL_LXML=False`): lxml
  brings its own libxml2, which collides with the one GDAL uses to parse GML and produces an
  access violation. When openpyxl was already imported with lxml, the tables fall back to CSV.
- GeoPackage writing: `CreateOrOverwriteLayer` is only used when the file already exists,
  and no layer is reopened to count features (a pooled OGR connection makes the next write
  fail with "update mode failed").
- Processing provider: a wrong parameter type in one algorithm crashed the whole QGIS
  process (a Python exception raised inside `loadAlgorithms()` is not propagated), and
  calling the base `flags()` through the class object recursed until the stack blew. Both
  are now covered by tests that register the provider and run the algorithms.
- WFS paging: an empty last page is recognised from `numberReturned="0"` instead of being
  handed to OGR, which cannot open it; a page that fails to parse after some data has
  already arrived truncates the result with a warning instead of losing the whole source.
- Overpass: server-side failures reported inside the document (`<remark>`) are recognised
  as unavailability, and a valid but empty answer counts as "no data", not as an error.
- Proximity radius: the distance search radius is clamped to the context buffer actually
  downloaded around the area. With the previous defaults (500 m downloaded, 5 km searched)
  every rule with a threshold above 500 m could never fire, silently.

### Known limitations

- The shipped catalogue covers national/European/global sources plus the Italian cadastre;
  regional catalogues are added by configuration (a template is provided).
- STAC, GeoPDF and the interpolation suite are planned for 2.0 (see the roadmap). DXF export
  of vector layers works and is tested, but is not wired to the dock yet: it is available
  through `engines.cartography.export.export_layers_to_dxf`.
- Elevation data comes from a global DEM: for detailed work configure an official DTM.
