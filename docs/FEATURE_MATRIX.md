# Feature matrix — Analisi territoriale 0.1.0

> Deliverable di Phase 0. Stato **misurato** il 2026-09-17 leggendo il codice ed eseguendo
> la suite, non dichiarato.
>
> Legenda: **E** presente e da preservare · **X** presente, da estendere · **B** da
> costruire · **W** wrapper su algoritmo QGIS nativo · **BUG** difetto confermato

---

## Baseline misurata in sessione

| Runner | Eseguiti | Passati | Falliti | Saltati | Durata |
|---|---:|---:|---:|---:|---:|
| QGIS 3.40.15 LTR (Python 3.12.12) | 327 | 311 | **0** | 16 | 30,0 s |
| QGIS 4.0.0 (Python 3.12.13) | 327 | 311 | **0** | 16 | 24,3 s |

I 16 saltati sono i test di rete (`--network`), esclusi per scelta dal percorso predefinito.
Eseguiti separatamente in sessione: 6 scenari culturali + 2 sonde ortofoto, tutti verdi su
entrambe le versioni.

**Questo e' il gate.** Ogni fase successiva deve chiudersi con un numero ≥ 327 e 0 falliti.

---

## 1. Core

| Capacita' | Stato | Dove | Note |
|---|:--:|---|---|
| Project Area (disegno, rettangolo, cerchio, layer, file, coordinate, particelle) | X | `core/project_area.py` | persistenza `.tsa.json` e nel progetto |
| Territorial Snapshot | X | `core/models.py::AnalysisReport` | **e' gia' la snapshot**: esteso con `modules` |
| Registry delle fonti | X | `core/registry.py` | 37 fonti, qualita', copertura, stato di verifica, ripieghi |
| Provenance | X | `core/provenance.py`, `core/models.py` | scritta nei metadati e nelle proprieta' dei layer |
| Data quality / gap | X | `engines/cultural_heritage/model.py` | `DataGap` a 6 valori — **da promuovere in `core/`** |
| Tassonomia gerarchica | E | `core/taxonomy.py` | 32 categorie, `expand`/`ancestry`/`root_of` |
| Cache su disco | X | `core/cache.py` | TTL, quantizzazione bbox, quota, LRU |
| CRS e misure ellissoidiche | E | `core/crs.py`, `core/measure.py` | CRS metrico automatico |
| Credenziali | E | `core/credentials.py` | auth DB QGIS o variabile d'ambiente |
| Gerarchia errori | E | `core/errors.py` | 10 classi; mancano `AuthenticationError`, `CoverageError`, `DataQualityError`, `ExportError`, `LayoutError`, `ValidationError` |
| Discovery 2.0 | B | — | oggi il discovery e' manuale (script di probe) |

## 2. Services

| Capacita' | Stato | Dove |
|---|:--:|---|
| HTTP su stack QGIS (retry, backoff, throttling, eccezioni OGC in HTTP 200) | E | `services/http.py` |
| WFS KVP con paging `next`, ordine assi, perdita feature rilevata | E | `services/ogc/wfs.py` |
| OGC API Features · ArcGIS REST · Overpass · raster WMS/WMTS/XYZ | E | `services/ogc/*`, `services/arcgis.py`, `services/overpass.py` |
| Google Map Tiles fail-closed | E | `services/google_tiles.py` |
| Health check per protocollo | X | `services/health.py` |
| Rivalidazione periodica delle fonti | B | — |

## 3. Engines di dominio

| Dominio | Stato | Note |
|---|:--:|---|
| Amministrativo | X | catasto prima, OSM come ripiego |
| Catasto | X | particelle, fogli, superfici **grafiche** |
| Vincoli / sensibilita' | X | presenza, superficie, %, distanza minima, dettaglio per feature |
| Patrimonio culturale (MiC) | **E** | modulo di riferimento: normalizer, classifier, dedup, quality, report |
| Terreno | X | DEM, quote, pendenza, esposizione, ombreggiatura, curve, profilo |
| Prossimita' | X | fasce di rispetto e distanze |
| Aree protette · Idrologia · Foreste · Geologia · Infrastrutture · Trasporti · Uso del suolo · Urbanistica | B | nessuno presente |

## 4. Analytics

| Capacita' | Stato | Note |
|---|:--:|---|
| Overlay, statistiche zonali, analisi di rete | W | da wrappare su `native:*`, `QgsZonalStatistics`, `QgsGraphAnalyzer` |
| Multicriteria · Suitability · Rischio · Scenari · Recipes · Temporale · Change detection | B | — |

## 5. Cartografia

| Capacita' | Stato | Note |
|---|:--:|---|
| Layout a blocchi + 12 template | X | `engines/cartography/layout.py` |
| Profili grafici (loghi, testi, legenda, nord) | X | 6 profili, editabili dall'interfaccia |
| Serie di tavole | X | 3 serie configurate |
| Export PDF/PNG/JPEG/SVG | X | manca TIFF, preset, metadati PDF |
| **Tavola ortofoto** | **BUG** | **P0.3** — oggetti QGIS creati nel worker thread |
| **DEM / pendenza / esposizione** | **BUG** | **P0.4** — rampa esposizione grigia a 2 stop, nessun multiply, nessun composito di stampa |
| Atlas (`QgsLayoutAtlas`) · QA cartografica · coda di stampa · batch | B | — |

## 6. Report e pacchetto

| Capacita' | Stato |
|---|:--:|
| Relazione HTML/PDF con sezioni per categoria | X |
| Sezione patrimonio culturale (14.1–14.12) | E |
| Tabelle CSV/XLSX | X |
| Pacchetto d'area (GeoPackage, progetto, manifest) | X |
| Checksum, `LICENSES.txt`, `ATTRIBUTIONS.txt` | B |

## 7. UX e integrazione

| Capacita' | Stato | Note |
|---|:--:|---|
| Dock a 5 sezioni, Quick/Pro, one-click | E | non rifondare |
| Command palette `Ctrl+Shift+T` | X | |
| Pannello risultati, onboarding | E | |
| Processing: 15 algoritmi | X | id stabili |
| **Nome visibile** | **BUG** | **P0.1** — «Territorial Suite» invece di «Analisi territoriale» |
| **Icona** | **BUG** | **P0.2** — placeholder, non `Icon_plugin_vincoli` |
| `QgsLocator` · diagnostica · i18n inglese · API `api.py` | B | |

## 8. Difetti confermati (ingresso a P0)

| # | Difetto | Evidenza raccolta in audit | Fase |
|---|---|---|---|
| D1 | Nome visibile errato | `core/constants.py::PLUGIN_NAME = "Territorial Suite"`, propagato in 8 punti di interfaccia | P0.1 |
| D2 | Icona placeholder | `resources/icons/territorial_suite.svg` (703 byte, generata); l'allegato `Icon_plugin_vincoli.png` 1254×1254 non e' collegato | P0.2 |
| D3 | Tavola ortofoto priva di raster | `gui/dock.py::run_orthophoto` passa `OrthophotoEngine.run` a `_start_task`; `tasks/runner.py::EngineTask.run` esegue nel worker; l'engine crea `QgsRasterLayer`, chiama `addMapLayer`, `layerTreeRoot().insertLayer` e costruisce `QgsPrintLayout` | P0.3 |
| D4 | Rilievo non ombreggiato | `config/styles/styles.json`: rampa `aspect` a 2 stop grigi (`#d9d9d9`→`#525252`); nessun `CompositionMode_Multiply`; nessun composito cotto per la stampa | P0.4 |

D3 e D4 sono difetti introdotti nelle fasi precedenti di questo lavoro: sono qui
riconosciuti come tali e corretti in P0, non aggirati.

---

## 9. Cosa Phase 0 **non** ha fatto

Nessun engine nuovo, nessuna fonte nuova, nessun refactor, nessun bump di versione.
Phase 0 produce solo conoscenza: questa matrice, `docs/ARCHITECTURE.md` e la baseline.

---

# Delta Phase D1 — tessuto dati nazionale + cartografia (WP0)

> Audit di apertura D1, misurato il 2026-09-17. Nessun codice di prodotto in WP0.

## Baseline D1 (gate di questa sessione)

| Runner | Eseguiti | Passati | Falliti | Saltati | Durata |
|---|---:|---:|---:|---:|---:|
| QGIS 3.40.15 LTR | 365 | 349 | **0** | 16 | 62,9 s |
| QGIS 4.0.0 | 365 | 349 | **0** | 16 | 69,4 s |

Ogni WP deve chiudersi con ≥ 365 e 0 falliti su entrambe.

## Catalogo attuale — 37 descrittori

| Protocollo | N | | Stato di verifica | N |
|---|---:|---|---|---:|
| WFS | 18 | | `verified` | 30 |
| OVERPASS | 10 | | `unverified` | 2 |
| XYZ | 3 | | `discovery_required` | 2 |
| REST | 2 | | `declared` | 2 |
| ARCGIS_FEATURE | 2 | | `unavailable` | 1 |
| TERRAIN_TILES + RASTER | 2 | | | |

**Nessun WMS nel catalogo.** Il plugin sa costruire layer WMS/WMTS
(`services/ogc/raster.py`) ma non ha ancora una sola fonte WMS descritta: tutta la
famiglia *view* del Geoportale Nazionale e' assente.

## Copertura OGC

| Protocollo | Modulo | Stato |
|---|---|---|
| WFS 2.0 | `services/ogc/wfs.py` | presente, con paging `next` e perdita feature rilevata |
| OGC API Features | `services/ogc/ogcapi.py` | presente |
| WMS / WMTS / XYZ | `services/ogc/raster.py` | costruzione layer presente, **nessuna fonte descritta** |
| GetCapabilities | `services/ogc/capabilities.py` | parser WFS/WMS/WMTS presente |
| **CSW** | — | **assente** (discovery) |
| **WCS** | — | **assente** (download raster) |
| **WPS** | — | **assente** (trasformazione) |

## Gap funzionali D1

| # | Capacita' | Stato | WP |
|---|---|:--:|---|
| G1 | Riquadro di localizzazione nei layout | **assente** — `QgsLayoutItemMapOverview` disponibile e non usato; "territorial_overview" nei template e' un nome di tavola, non un inset | WP1 |
| G2 | DEM/pendenza/esposizione ramp x hillshade | **presente** (P0.4), da confermare | WP2 |
| G3 | Catalogo ortofoto | 3 fonti (OSM, Esri, Google) — nessuna nazionale PCN/AGEA | WP3 |
| G4 | Servizi OGC nazionali MASE | **assenti**: nessun WMS, nessun CSW, nessun WCS | WP4 |
| G5 | Cartografia IGM 25.000 come base | **assente** | WP4 |
| G6 | Vincoli art. 136/142/157 completi | parziale: 13 layer SITAP, mancano lettere art. 142 non coperte e art. 157 | WP4 |
| G7 | Rischio idraulico / PGRA | **assente** | WP5 |
| G8 | Frane PAI / IFFI | **assente** | WP5 |
| G9 | Vincolo idrogeologico R.D. 3267/1923 | **assente** — nessun layer nazionale esiste, competenza regionale | WP6 |
| G10 | `DataGap` in `core/` + `VIEW_ONLY`, `REGIONAL_SOURCE_REQUIRED` | `DataGap` vive in `cultural_heritage`, mancano i due valori | WP4 |

## Vincoli architetturali confermati in audit

* `AnalysisReport.modules{}` e' il punto di estensione per ogni motore tematico: nessun
  tipo parallelo va creato.
* `OrthophotoEngine.prepare()` / `compose()` separa worker e main thread: da non
  riunificare.
* `engines/cultural_heritage/` resta il contratto di riferimento da **clonare**, mai da
  modificare per accomodare un motore nuovo.
* Stratificazione `core ← services ← engines ← tasks ← gui/processing`: **0 violazioni**,
  presidiata da `tests/unit/test_layering.py`.
