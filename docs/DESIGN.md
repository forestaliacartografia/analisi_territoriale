# Analisi territoriale — Design Document

> Documento di progettazione (output A–Q del master prompt).
> Lingua: **italiano** per questo documento, per la documentazione utente (`docs/`) e per
> l'interfaccia; **inglese** per codice, API, nomi file, commenti e README. La cartella
> `i18n/` e' predisposta per una futura traduzione inglese dell'interfaccia.

Target: **QGIS 3.40 LTR (Qt5) → QGIS 4.0 (Qt6)**, Python 3.12, nessuna dipendenza esterna obbligatoria.

---

## A. PRODUCT VISION

**Nome visibile:** Analisi territoriale · **id plugin (invariato):** `territorial_suite`

**Pitch:** lo strumento che apro quando devo *capire rapidamente un territorio* e
*produrre cartografia professionale*.

**Problema.** Il quadro conoscitivo di un'area (vincoli, catasto, natura, acque,
infrastrutture, terreno) richiede oggi decine/centinaia di operazioni manuali ripetitive in
QGIS: cercare il servizio, aggiungere il WMS/WFS, impostare il CRS, filtrare per BBOX,
scaricare, ritagliare, rinominare, raggruppare, stilizzare, creare il layout, inserire
legenda/nord/scala/fonti, esportare PDF. Per ogni area. Ogni volta.

**Soluzione.** Una piattaforma modulare imperniata sulla **Project Area**: l'utente definisce
un'area; il plugin interroga in background un catalogo di sorgenti ufficiali, produce un
quadro conoscitivo strutturato, misura intersezioni e distanze, valuta regole, genera layer
organizzati, tavole cartografiche e un dossier territoriale con piena tracciabilità delle fonti.

**Valore centrale:** `ANALIZZARE → COMPRENDERE → DOCUMENTARE → CARTOGRAFARE`

**Utenti target**

| Persona | Bisogno | Cosa ottiene |
|---|---|---|
| Tecnico/progettista (geometra, ingegnere, architetto) | quadro vincolistico e catastale rapido per una pratica | dossier + tavole in minuti |
| Forestale/agronomo | piani di taglio, vincoli, pendenze, viabilità | pacchetto dati + carta forestale/altimetrica |
| PA / ufficio tecnico | istruttoria con fonti tracciate | report con provenienza e data del dato |
| Consulente ambientale (VIA/VincA/AUA) | screening preliminare sensibilità | alert classificati + fonti |
| Cartografo | tavole coerenti e ripetibili | map series + template |

**Non-obiettivi (espliciti)**

1. Il plugin **non** emette giudizi giuridici. Distingue sempre *dato cartografico conoscitivo*
   da *vincolo giuridicamente accertato* (campo `evidence_level`, vedi §G-2).
2. Niente scraping fragile o aggiramento di termini d'uso di servizi/basemap.
3. Niente dipendenze pesanti che compromettano l'installazione (no pandas/geopandas obbligatori).
4. Niente "mega plugin" a livello di UX: *complexity inside, simplicity outside*.

**Principio di prodotto — ZERO MANUAL REPETITION.** Ogni feature deve eliminare
un passaggio manuale identificabile e documentarlo nella propria scheda (§Q).

---

## B. ARCHITECTURE

### B.1 Stratificazione (dipendenze unidirezionali)

```text
┌──────────────────────────────────────────────────────────────┐
│ GUI (dock, wizard, command palette)   PROCESSING (provider)  │  ← adapter
├──────────────────────────────────────────────────────────────┤
│ TASKS (QgsTask wrapper, progress, cancel, error aggregation)  │  ← orchestrazione async
├──────────────────────────────────────────────────────────────┤
│ ENGINES  constraints · cadastre · terrain · proximity · rules │  ← logica di dominio
│          download · package · cartography · report            │
├──────────────────────────────────────────────────────────────┤
│ SERVICES  http · ogc(wfs/wms/wmts/xyz/api) · overpass · stac  │  ← I/O esterno
├──────────────────────────────────────────────────────────────┤
│ CORE  project_area · registry · models · cache · provenance   │  ← modello + utilità pure
│       crs · measure · geometry · settings · errors · qt_compat│
├──────────────────────────────────────────────────────────────┤
│ CONFIG (JSON)  sources · rules · thresholds · styles · layouts│  ← dato, non codice
└──────────────────────────────────────────────────────────────┘
```

**Regole di dipendenza (verificate dal test `tests/unit/test_layering.py`)**

- `core` non importa `services`, `engines`, `gui`, `processing`.
- `services` importa solo `core`.
- `engines` importano `core` + `services`.
- `gui` e `processing` sono **adapter**: nessuna logica di dominio, solo composizione.
- Nessun modulo non-GUI importa `QtWidgets` o `iface`.
- Nessun URL o soglia numerica hardcodata fuori da `config/`.

### B.2 Concorrenza

- Ogni operazione di rete o calcolo pesante gira in `QgsTask` (`tasks/`).
- I task **non** toccano il layer tree né la GUI: producono *risultati* (dataclass + file su
  disco/GeoPackage); l'applicazione al progetto avviene nel main thread (`taskCompleted` →
  `ResultApplier`).
- Il fan-out sulle sorgenti usa sottotask cancellabili; il fallimento di una sorgente **non**
  annulla le altre (§L).
- HTTP dentro i task: `QgsBlockingNetworkRequest` (thread-safe, usa proxy/auth/certificati di QGIS).

### B.3 Estendibilità

- **Sorgenti**: file JSON droppabili in `config/sources/**` + cartella utente
  (`<profile>/territorial_suite/sources/`), senza toccare il codice.
- **Regole**: JSON dichiarativo interpretato dal `RuleEngine`.
- **Stili/Layout**: QML/JSON in `config/styles`, `config/layouts` + override utente.
- **Moduli futuri** (GeoAI, LiDAR, 3D…): estensione in `engines/` e registrazione nel
  registro delle capability; nessuno implementato nell'MVP.

---

## C. MODULE MAP

| Modulo | Responsabilità | Dipende da |
|---|---|---|
| `core.project_area` | `ProjectArea`: geometria, CRS, metriche, unità amministrative, serializzazione | core.* |
| `core.registry` | `DataSource` + caricamento/validazione/indicizzazione catalogo, filtro per ambito | core.models |
| `core.models` | dataclass dei risultati e enum (`EvidenceLevel`, `AlertLevel`, `SourceStatus`, `SourceType`) | — |
| `core.admin_reference` | tabella ISTAT dei Comuni (ISTAT ↔ nome ↔ codice catastale ↔ provincia ↔ regione) | core.paths |
| `core.compat_guards` | interruttori di sicurezza applicati al caricamento (es. backend XML di openpyxl) | core.log |
| `core.cache` | cache su disco con TTL, chiave = (source, bbox quantizzato, params), quota, invalidazione | core.settings |
| `core.provenance` | scrittura/lettura provenienza su metadata + custom properties dei layer | — |
| `core.crs` | CRS di lavoro, UTM automatico, trasformazioni sicure | — |
| `core.measure` | aree/lunghezze ellissoidali (`QgsDistanceArea`), formattazione m²/ha/km² | core.crs |
| `core.geometry` | shape metrics, convex hull, MBG, centroidi, clip robusto (fix geometrie) | core.measure |
| `services.http` | GET/POST con retry, timeout, backoff, user-agent, auth config QGIS | core.errors |
| `services.ogc.*` | capabilities parsing, WFS (paging, bbox, filtri), WMS/WMTS/XYZ, OGC API Features | services.http |
| `services.overpass` | query OSM via Overpass API, mapping tag→categoria | services.http |
| `services.cadastre` | client WFS INSPIRE Agenzia delle Entrate (CP:CadastralParcel/CadastralZoning) | services.ogc.wfs |
| `services.health` | health check sorgenti con cache breve e semaforo UI | services.http |
| `engines.analysis` | orchestratore One-Click: pipeline a step, aggregazione risultati/errori | tutti gli engine |
| `engines.constraints` | presenza, intersezione, %, superficie, distanza, attributi, fonte | services, core |
| `engines.cadastre_engine` | comuni → fogli → particelle → intersezione → tabella multi-comune | services.cadastre |
| `engines.terrain` | DEM, clip, statistiche quota, slope/aspect/hillshade, classi, contour, profilo | processing (GDAL) |
| `engines.proximity` | distanze minime, buffer e fasce di rispetto | core.geometry |
| `engines.rules` | valutazione regole dichiarative → `Alert` | core.models |
| `engines.download` | Download Area per categoria, scrittura GeoPackage organizzato | services, core |
| `engines.package` | "Scarica tutto": albero cartelle, `project.qgz`, manifest, report | tutti |
| `engines.cartography.*` | styling, scale, legend, layout builder, map series, export center | core, QGIS layout API |
| `engines.report` | dossier territoriale (HTML/PDF), tabelle CSV/XLSX | engines.* |
| `tasks.*` | wrapper `QgsTask` per ciascun engine + `ResultApplier` | engines |
| `gui.*` | dockwidget, pannelli, wizard area, command palette, onboarding, source manager | tasks |
| `processing.*` | provider + algoritmi (stessa logica degli engine) | engines |

---

## D. DATA SOURCE ARCHITECTURE

### D.1 Catalogo

```text
config/sources/
├── _schema.json            # schema del descrittore (validazione interna)
├── national/               # ISPRA, MiC, MASE, ...
├── european/               # Natura 2000 EEA, Copernicus
├── regional/<codice>/      # es. toscana/geoscopio_*.json
├── provincial/
├── municipal/
├── cadastral/              # agenzia_entrate_inspire.json
├── osm/                    # overpass_*.json
├── imagery/                # ortofoto, XYZ basemap
├── terrain/                # DEM/DTM
└── infrastructure/
```

**Descrittore sorgente:**

```jsonc
{
  "id": "it.mic.vincoli_paesaggistici_142",
  "name": "Aree tutelate per legge (art.142 D.Lgs.42/2004)",
  "authority": "Ministero della Cultura",
  "country": "IT",
  "scope": {"level": "national", "codes": []},
  "category": "landscape_cultural",           // tassonomia §6 del prompt
  "subcategory": "art142",
  "type": "WFS",                              // WFS|WMS|WMTS|XYZ|OGCAPI|GEOJSON|GPKG|REST|STAC|OVERPASS|RASTER
  "url": "https://.../wfs",
  "layer": "typename",
  "crs": ["EPSG:4326", "EPSG:32632"],
  "geometry": "Polygon",
  "query": {"bbox_crs": "EPSG:4326", "page_size": 1000, "filter_capable": true},
  "fields": {"label": "denominazione", "code": "codice", "date": "data_prov"},
  "evidence_level": "cartographic",           // cartographic | declaratory | verified_act
  "legal_reference": "D.Lgs. 42/2004 art.142",
  "update_frequency": "annual",
  "last_verified": "2026-09-16",
  "license": "CC-BY 4.0",
  "official": true,
  "metadata_url": "https://...",
  "attribution": "...",
  "scale": "1:10000",
  "accuracy_m": 5,
  "enabled": true,
  "priority": 10,
  "notes": "..."
}
```

`evidence_level` è il campo che impedisce di trasformare un dato cartografico in
qualificazione giuridica: GUI e report riportano sempre la dicitura corrispondente.

### D.2 Risoluzione delle sorgenti per una Project Area

```text
ProjectArea → comuni/province/regioni (ISTAT) → SourceScopeResolver
   → sorgenti nazionali + europee + regionali(regione/i) + provinciali + comunali + OSM
   → filtro per categoria richiesta, enabled, health
   → piano di esecuzione (ordinato per priority, deduplicato)
```

### D.3 Override utente

Ordine di merge: `builtin` → `profilo utente` → `progetto QGIS`. Ogni livello può aggiungere,
disabilitare (`"enabled": false`) o sovrascrivere singoli campi di una sorgente.

---

## E. USER WORKFLOWS

**W1 — One-Click Territorial Analysis (killer #1)**

```text
Definisci area ─▶ ANALYZE ─▶ [location→admin→cadastre→constraints→environment→
hydro→infra→risk→terrain→imagery] ─▶ SUMMARY (alert, fonti ok/ko, statistiche,
layer creati) ─▶ [Genera mappe] [Genera report] [Esporta pacchetto]
```

**W2 — One-Click Cartography (killer #2)**: tipo tavola + formato + scala + stile → layout pronto.

**W3 — Analisi catastale**: area → comuni → fogli/particelle → tabella multi-comune → export.

**W4 — Download Area**: area + categorie → GeoPackage organizzato + gruppi layer stilizzati.

**W5 — Terrain**: area → DEM → statistiche + slope/aspect/hillshade + classi + contour.

**W6 — Package "Scarica tutto"**: area → albero completo + `project.qgz` + report.

**W7 — Da particelle a Project Area**: seleziona particelle → "Crea area di progetto".

**Quick mode** espone W1/W2/W6 con default; **Professional mode** espone sorgenti, CRS, soglie,
stili, output.

---

## F. UX/UI

Dockwidget unico, sezioni collassabili:

```text
┌─────────────────────────────────────┐
│ TERRITORIAL SUITE        [⚡][⚙][?] │  ⚡=Quick/Pro  ⚙=settings  ?=docs
├─────────────────────────────────────┤
│ PROJECT AREA                        │
│ ▸ nome | 12,4 ha | EPSG:32632       │
│ [Create ▾] [Load] [Save]            │
├─────────────────────────────────────┤
│ ▶ ANALYZE                           │
│   ⦿ One-Click Analysis              │
│   ▸ Territorial Framework           │
│   ▸ Constraints    ▸ Cadastre       │
│   ▸ Terrain        ▸ Proximity      │
├─────────────────────────────────────┤
│ ▶ DATA                              │
│   ▸ Download Vector  ▸ Imagery      │
│   ▸ DEM / DTM        ▸ Sources ●●●○ │
├─────────────────────────────────────┤
│ ▶ CARTOGRAPHY                       │
│   ▸ Quick Map ▸ Map Series ▸ Templates│
├─────────────────────────────────────┤
│ ▶ REPORT                            │
│   ▸ Territorial Report ▸ Export Package│
├─────────────────────────────────────┤
│ [ progress bar + cancel ]  [log ▾]  │
└─────────────────────────────────────┘
```

- **Results panel** a schede: *Summary · Alerts · Constraints · Cadastre · Terrain · Sources*.
- **Command palette** (`Ctrl+Shift+T`): ricerca su comandi, sorgenti, template.
- **Onboarding** in 4 passi al primo avvio (area → sorgenti → analisi → mappa).
- Regole UI: nessun colore o icona che esprima un giudizio giuridico; gli alert usano etichette
  semantiche (INFORMAZIONE / ATTENZIONE / VERIFICA / CRITICITÀ CARTOGRAFICA) con palette neutra
  e testo esplicito sulla natura del dato.

---

## G. FUNCTIONAL SPECIFICATION (sintesi; dettaglio in `docs/`)

**G-1 Project Area** — creazione da: disegno libero, poligono, rettangolo, cerchio, selezione
feature, layer, SHP/GPKG/GeoJSON/KML-KMZ, coordinate, BBOX, particelle catastali (singole o
aggregate). Persistenza nel progetto QGIS + file `.tsa.json` portabile. Metriche: area
(m²/ha/km²), perimetro (m/km), bbox, centroide, CRS, unità amministrative, id progetto, data.

**G-2 Constraint & Sensitivity Engine** — per sorgente: presenza, intersezione, % area
interessata, superficie interessata, distanza minima (se non interseca), geometria interessata,
attributi chiave, fonte/ente/data/scala/accuratezza/licenza/URL, `evidence_level`, riferimento
normativo (testuale, mai come verdetto).

**G-3 Cadastre** — comuni interessati → codice catastale → WFS INSPIRE AdE → zoning (fogli) →
particelle → intersezione → tabella per comune con superficie catastale, superficie interessata,
%, geometria. Gestione esplicita multi-comune e multi-foglio.

**G-4 Alert/Rule Engine** — regole JSON `{dataset, operation, threshold, unit, result}`;
operazioni: `intersects`, `within`, `distance_lt`, `area_pct_gt`, `count_gt`, `contains`,
`attribute_eq/in`, composizioni `all/any/not`. Esito = `Alert(level, title, detail, evidence,
sources)`. Il campo `result` è informativo (`INFO|ATTENTION|CHECK_REQUIRED|CARTOGRAPHIC_ISSUE`).

**G-5 Terrain** — DEM locale/remoto/ufficiale/Copernicus/custom; clip sull'area; statistiche
quota; slope (media, max, classi configurabili, % area per classe); aspect; hillshade; contour;
profilo altimetrico; istogramma quote.

**G-6 Download/Imagery/Package** — vedi §E W4/W6; categorie e mapping in `config/sources`.

**G-7 Cartography** — Quick Map, Template Engine (12 template), Legend Engine, Scale Engine,
Styling Engine, Map Series, Export Center (PDF/PNG/JPEG/SVG/GeoPDF/DXF) con naming automatico.

**G-8 Report** — dossier a 16 sezioni con fonte per ogni informazione, tabelle catastali,
statistiche terreno, elenco alert, metadati e provenienza.

---

## H. DATA MODEL

```python
EvidenceLevel = Enum("cartographic", "declaratory", "verified_act")
AlertLevel    = Enum("INFO", "ATTENTION", "CHECK_REQUIRED", "CARTOGRAPHIC_ISSUE")
SourceStatus  = Enum("ONLINE", "DEGRADED", "OFFLINE", "UNKNOWN", "DISABLED")

@dataclass ProjectArea:
    id, name, geometry_wkt, crs, work_crs, created_at
    area_m2, perimeter_m, bbox, centroid
    admin: AdminUnits(municipalities[], provinces[], regions[])
    source_kind, notes, parcels: list[ParcelRef]

@dataclass DataSource            # ← config/sources/*.json (§D)
@dataclass Provenance:   source_id, url, authority, retrieved_at, crs, operation, inputs[], license
@dataclass FeatureHit:   fid, label, attributes, intersect_area_m2, intersect_pct, distance_m
@dataclass SourceResult: source, status, hits[], feature_count, layer_uri, provenance, error, elapsed_ms
@dataclass ConstraintHit: category, source, present, intersect_area_m2, intersect_pct, min_distance_m, features[]
@dataclass Alert:        level, code, title, detail, evidence_level, sources[], values{}
@dataclass TerrainStats: min, max, mean, median, range, slope_mean, slope_max, slope_classes[], aspect_hist
@dataclass CadastralRow: municipality, istat, cad_code, sheet, parcel, area_cad_m2, area_int_m2, pct
@dataclass AnalysisReport: project_area, started_at, finished_at, results[], alerts[], terrain,
                           cadastre[], sources_ok[], sources_failed[], layers_created[], warnings[]
```

Persistenza: `AnalysisReport` serializzabile in JSON (`analysis.json`) — base per report,
package, ri-esecuzione e test di regressione con fixture registrate.

---

## I. API DESIGN (interna, stabile)

```python
# core
ProjectArea.from_geometry(geom, crs, name=None) -> ProjectArea
ProjectArea.from_layer(layer, selected_only=False) -> ProjectArea
ProjectArea.to_dict() / from_dict()
ProjectAreaStore(project).save(area) / load() / list()

DataSourceRegistry.instance().load()
    .get(source_id) -> DataSource
    .query(category=None, scope=None, types=None, enabled_only=True) -> list[DataSource]
    .resolve_for(area, categories=None) -> list[DataSource]

CacheManager.get(key) / put(key, path, ttl) / invalidate(prefix) / size() / clear()
ProvenanceManager.stamp(layer, provenance) / read(layer) -> Provenance

# services
HttpClient.get(url, params, timeout, retries) -> HttpResponse
WfsClient(source).describe() / features(bbox, crs, filter=None, limit=None)
WmsBuilder(source).layer(name) -> QgsRasterLayer
CadastreClient.municipalities_for(geom) / zoning(code, bbox) / parcels(code, bbox)
HealthChecker.check(source) -> SourceStatus

# engines
ConstraintEngine(area, registry, cache).run(categories, feedback) -> list[SourceResult]
CadastreEngine(area).run(feedback) -> list[CadastralRow]
TerrainEngine(area, dem_source).run(feedback) -> TerrainStats
ProximityEngine(area).distances(results) -> dict[source_id, float]
RuleEngine(rules).evaluate(report) -> list[Alert]
DownloadManager(area).download(categories, out_gpkg, feedback) -> list[Path]
AnalysisOrchestrator(area, options).run(feedback) -> AnalysisReport
MapEngine(project).quick_map(spec: MapSpec) -> QgsPrintLayout
MapSeriesEngine(project).generate(specs) -> list[QgsPrintLayout]
ExportCenter.export(layout, fmt, path) -> Path
ReportEngine(report).to_pdf(path) / to_html(path) / tables_to_xlsx(path)
PackageBuilder(report).build(root_dir) -> Path
```

Regola: **ogni engine è utilizzabile senza GUI e senza `iface`**, riceve un `Feedback`
(progress/cancel/log) astratto implementato sia da `QgsTask` sia da `QgsProcessingFeedback`.

---

## J. PROCESSING PROVIDER

```text
Territorial Suite
├── Area
│   ├── Create project area from layer
│   ├── Analyze area (one-click)
│   └── Area geometry statistics
├── Constraints
│   ├── Analyze constraints
│   └── Generate buffers / setbacks
├── Cadastre
│   └── Query cadastral parcels
├── Terrain
│   ├── Download DEM for area
│   ├── Calculate terrain statistics
│   ├── Calculate slope classes
│   └── Elevation profile
├── Data
│   └── Download area dataset
├── Cartography
│   ├── Generate quick map
│   └── Generate map series
└── Report
    └── Generate territorial report
```

Ogni algoritmo riusa l'engine corrispondente (nessuna logica duplicata), dichiara parametri
tipizzati, supporta `Run as Batch` ed è richiamabile da modello e da `qgis_process`.

---

## K. CACHING STRATEGY

```text
<profile>/cache/territorial_suite/
├── vector/<source_id>/<bbox_hash>_<params_hash>.gpkg
├── raster/<source_id>/<bbox_hash>_<res>.tif
├── services/<host>/capabilities_<hash>.xml
├── metadata/<source_id>.json          # health, last_ok, schema
└── index.sqlite                        # chiave→file, ttl, size, hits, area_id
```

- **Chiave**: `sha1(source_id | bbox arrotondato a griglia | crs | filtri | page_size)`.
- **TTL** per tipo (config): capabilities 7g, vector 24h, raster 30g, health 10min.
- **Quota** (default 2 GB) con eviction LRU; `Clear cache` globale o per Project Area.
- Bypass esplicito (`Refresh`) e modalità *offline* (solo cache).

---

## L. ERROR HANDLING

Principio: **nessuna sorgente può interrompere l'analisi.**

| Classe | Esempio | Comportamento |
|---|---|---|
| `SourceUnavailableError` | HTTP 5xx, DNS, timeout | retry con backoff (3), poi `status=OFFLINE`, analisi prosegue, voce nel report finale |
| `SourceSchemaError` | typename assente, campo mancante | degrada a `DEGRADED`, fallback sui campi, warning |
| `GeometryError` | geometria invalida | `makeValid()` + log; se fallisce → errore localizzato alla sorgente |
| `CrsError` | trasformazione impossibile | skip sorgente + warning esplicito |
| `ConfigError` | descrittore non valido | sorgente esclusa al load, messaggio nel Source Manager |
| `UserCancelled` | cancel | rollback layer temporanei, nessun file parziale pubblicato |

Tutto passa da `core.errors` + log su `QgsMessageLog` canale *Analisi territoriale* con livello
configurabile; il pannello Sources mostra stato e ultimo errore per sorgente.

---

## M. TEST STRATEGY

- **Unit (senza rete)**: geometria, misure, CRS, registry, cache, regole, naming, scale engine,
  parser capabilities/GML su fixture salvate.
- **Integration (mock HTTP)**: `FakeHttpClient` che serve fixture → WFS paging, errori 500,
  XML malformato, dataset vuoto, timeout.
- **QGIS-in-the-loop**: test eseguiti con `python-qgis-ltr.bat` e `python-qgis.bat`
  (3.40 LTR + 4.0) su `QgsApplication` headless: layer, layout, export, processing.
- **Scenari reali (§43 del prompt)**: 10 casi (urbano, forestale, costiero, montano,
  multi-comune, multi-foglio, vincoli sovrapposti, WFS down, dataset grande, area senza dati)
  come fixture parametrizzate; quelli che richiedono rete sono marcati `@network`.
- **Regressione**: `analysis.json` di riferimento confrontato con tolleranze numeriche.
- **Layering test**: verifica automatica delle regole §B.1 e assenza di URL hardcodati.

---

## N. REPOSITORY STRUCTURE

```text
Plugin_Vincoli/
├── territorial_suite/          # ← cartella installabile del plugin
│   ├── __init__.py  metadata.txt  plugin.py
│   ├── core/  services/  engines/  tasks/  gui/  processing/
│   ├── config/{sources,styles,layouts,rules,thresholds,defaults}
│   ├── resources/{icons,report}
│   └── i18n/
├── tests/{unit,integration,qgis,fixtures,scenarios}
├── docs/
├── scripts/{package.py,run_tests.py,validate_sources.py}
├── README.md  LICENSE (GPL-3.0)  CHANGELOG.md
```

---

## O. MVP (V1.0-beta: ciò che deve funzionare end-to-end)

1. **Project Area** — disegno/rettangolo/layer/file/coordinate/BBOX + salvataggio nel progetto.
2. **Data Source Registry** — catalogo JSON + Source Manager + health check.
3. **Catasto** — WFS AdE, particelle intersecanti, tabella multi-comune, export CSV/XLSX.
4. **Constraint Analysis** — set iniziale di sorgenti nazionali/europee + 1 regione pilota,
   con intersezioni, %, distanze, evidence level.
5. **Basic Terrain** — DEM (locale o remoto configurato), statistiche quota, slope + classi.
6. **Vector Download** — viabilità, idrografia, sentieri, edifici, località → GPKG.
7. **Imagery** — basemap/ortofoto configurabili con attribution.
8. **Quick Map** — layout A4/A3 portrait/landscape con legenda, nord, scala, griglia, fonti.
9. **Report** — dossier PDF/HTML con fonti e tabelle.
10. **Processing provider** — almeno: Analyze area, Query cadastre, Terrain statistics,
    Download area dataset, Quick map.

Fuori MVP: STAC, GeoPDF/DXF, interpolazione completa, map series completa (solo 4 tavole),
moduli futuri (§54 del prompt).

---

## P. ROADMAP

| Rel. | Contenuto | Complessità | Dipendenze | Test | Criteri di accettazione |
|---|---|---|---|---|---|
| **V0.1** | Core, ProjectArea, settings, log, dock scheletro, packaging | M | — | unit geometria/CRS/serializzazione | crea/salva/ricarica area; il plugin carica su 3.40 e 4.0 |
| **V0.2** | Registry + catalogo + http/OGC + cache + health + Source Manager | A | V0.1 | mock HTTP, capabilities fixture | WFS reale interrogabile con bbox; sorgente down non blocca |
| **V0.3** | Catasto AdE + tabella + export + "area da particelle" | A | V0.2 | fixture GML AdE, multi-comune | tabella corretta su area a cavallo di 2 comuni |
| **V0.4** | Constraint engine + rule engine + alert + results panel | A | V0.2 | scenari 1,2,7,10 | % e distanze corrette; evidence level sempre mostrato |
| **V0.5** | Terrain engine (DEM, stats, slope/aspect/hillshade, contour, profilo) | A | V0.2 | raster fixture | statistiche coerenti con `gdalinfo` sulla fixture |
| **V0.6** | Download manager + OSM + imagery + package | M | V0.2 | scenari 4,9 | GPKG organizzato, nomi/gruppi/stili automatici |
| **V0.7** | Cartography: styling, scale, legend, layout, map series, export | A | V0.4–0.6 | export PDF/PNG headless | tavola A3 professionale in <10 s |
| **V0.8** | Report engine + provenance completa + XLSX | M | V0.3–0.7 | confronto snapshot | report con fonte per ogni sezione |
| **V1.0** | One-Click Analysis, command palette, onboarding, Quick/Pro mode, docs, i18n, QA | A | tutte | suite completa su 3.40 + 4.0 | 10 scenari verdi; pubblicazione su repo QGIS |
| **V2.0** | Interpolazione, STAC, GeoPDF/DXF, regole avanzate, altre regioni | — | V1.0 | — | — |
| **V3.0** | Ecosystem: moduli esterni, API GeoAI/LiDAR/3D | — | V2.0 | — | — |

---

## Q. ACCEPTANCE CRITERIA (validi per ogni feature)

Una feature è **completa** solo se:

1. risponde alle 10 domande della regola assoluta (§60 del prompt) nella sua scheda in `docs/`;
2. gestisce errori, assenza di dati e cancellazione;
3. è testata (unit + almeno uno scenario) e i test passano su **3.40 LTR e 4.0**;
4. è documentata in `docs/` e nel changelog;
5. non rompe altre funzionalità (suite verde);
6. non introduce dipendenze esterne non motivate;
7. rispetta il CRS (nessun calcolo metrico in gradi; misure ellissoidali dichiarate);
8. conserva metadata/provenance sui layer prodotti;
9. è riutilizzabile da Processing quando ha senso;
10. non esprime giudizi giuridici automatici e cita sempre la fonte.
