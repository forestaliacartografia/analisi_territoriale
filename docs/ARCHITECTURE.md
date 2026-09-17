# Architettura — Analisi territoriale (package `territorial_suite`)

> Documento di Phase 0. Fotografa l'architettura **misurata** il 2026-09-17, non quella
> desiderata. Ogni numero qui deriva da un'analisi statica del sorgente o da un'esecuzione
> registrata in sessione.

---

## 1. Identità

| Voce | Valore |
|---|---|
| Nome visibile | **Analisi territoriale** |
| ID tecnico / package Python | `territorial_suite` — **invariato**, e con esso chiavi `QgsSettings`, `PROP_*` dei layer e id degli algoritmi Processing |
| Versione | 0.1.0 (experimental) |
| QGIS | 3.40 LTR (Qt5) e 4.0.0 (Qt6) |
| Licenza | GPL-2.0-or-later |

Il nome visibile e il nome tecnico sono deliberatamente diversi: cambiare il secondo
romperebbe i progetti QGIS esistenti (proprieta' dei layer), le impostazioni utente e i
modelli Processing gia' salvati.

---

## 2. Metriche del codice

| Strato | File | Righe | Ruolo |
|---|---:|---:|---|
| `core` | 20 | 4.073 | modello, registry, area di progetto, provenance, cache, CRS, misure, tassonomia, credenziali, errori |
| `services` | 13 | 2.223 | HTTP, health, fetcher, vector I/O, WFS, OGC API, ArcGIS, Overpass, Google Map Tiles, raster |
| `engines` | 30 | 7.598 | analisi, amministrativo, catasto, vincoli, terreno, patrimonio culturale, cartografia, report, pacchetto |
| `tasks` | 2 | 133 | wrapper `QgsTask` |
| `processing` | 10 | 1.344 | provider + 15 algoritmi |
| `gui` | 17 | 3.253 | dock, risultati, dialoghi, palette, onboarding |
| radice | 2 | 168 | `plugin.py`, `__init__.py` |
| **Totale** | **94** | **18.792** | |

Test: **20 file, 4.206 righe, 327 test**.

---

## 3. Grafo delle dipendenze (misurato)

```text
             ┌──────────────┐
             │     core     │  ← nessuna dipendenza da altri strati
             └──────┬───────┘
         ┌──────────┼───────────┬──────────────┐
         │          │           │              │
   ┌─────▼────┐ ┌───▼────┐ ┌────▼─────┐  ┌─────▼──────┐
   │ services │ │ tasks  │ │ engines  │  │ processing │
   │  (67)    │ │  (3)   │ │  (139)   │  │    (15)    │
   └─────┬────┘ └────────┘ └────┬─────┘  └─────┬──────┘
         │  18                  │              │ 21
         └──────────────────────┤              │
                                │              │
                          ┌─────▼──────────────▼─────┐
                          │           gui            │
                          │ core 41 · engines 21 ·   │
                          │ services 2 · tasks 2     │
                          └──────────────────────────┘
```

Regola: `core ← services ← engines ← tasks ← gui/processing`. Un import verso destra e' una
violazione.

**Violazioni rilevate: nessuna.** `tests/unit/test_layering.py` la verifica a ogni
esecuzione della suite.

Nota di lettura: `gui → services` (2 import) e `gui → tasks` (2) sono legittimi — la GUI usa
`HealthChecker` e il runner dei task —, ma la GUI **non** implementa algoritmi: ogni
funzione importante passa dall'engine corrispondente.

---

## 4. Contratti centrali (da estendere, mai duplicare)

| Contratto | Dove | Ruolo |
|---|---|---|
| `AnalysisReport` | `core/models.py` | **la Territorial Snapshot**. Unico artefatto centrale: report, cartografia, pacchetto e GUI leggono questo. Estendibile via `modules: Dict[str, Any]` |
| `SourceResult` | `core/models.py` | esito dell'interrogazione di una fonte su un'area |
| `Provenance` | `core/models.py` | da dove viene un dato, con natura, valore probatorio, stato di verifica, copertura |
| `DataSource`, `SourceQuality`, `SourceCoverage`, `VerificationStatus`, `DataNature` | `core/registry.py`, `core/models.py` | catalogo come configurazione |
| `ProjectArea` | `core/project_area.py` | il territorio, con persistenza `.tsa.json` |
| `Finding`, `DataGap` | `engines/cultural_heritage/model.py` | semantica legale e semantica dei vuoti. **Da promuovere in `core/`** (Phase 1) |
| `Feedback` | `core/feedback.py` | progresso e annullamento, identico in GUI, task e Processing |

---

## 5. Thread-safety

`tasks/runner.py` esegue `work(feedback)` dentro `EngineTask.run()`, cioe' **in un thread
worker**. `finished()` viene invece consegnata nel thread principale.

Regola: il worker scarica, calcola e serializza; il thread principale crea layer, progetto,
layout e GUI. I risultati viaggiano come dataclass JSON + WKT.

**Corretto in P0.3.** `gui/dock.py::run_orthophoto` eseguiva `OrthophotoEngine.run()`
nel worker, e quella chiamata creava `QgsRasterLayer` e `QgsPrintLayout`. Ora il
worker chiama solo `OrthophotoEngine.prepare()` (rete, dato semplice) e ogni oggetto
QGIS nasce in `compose()`, nel thread principale.
`tests/qgis/test_orthophoto_sheet.py` legge la closure del worker e rifiuta qualunque
chiamata che costruirebbe un oggetto QGIS li' dentro.

---

## 6. Configurazione

Tutto cio' che puo' cambiare senza una release:

```text
territorial_suite/config/
├── defaults.json     soglie, timeout, scale, classi, DPI
├── taxonomy.json     32 categorie, gerarchiche
├── rules/            28 regole di segnalazione
├── styles/           stili vettoriali, raster e rampe di colore
├── layouts/          template delle tavole, serie e profili grafici
└── sources/          37 descrittori di fonte
```

Override utente in `<profilo QGIS>/territorial_suite/`, override di progetto nel `.qgz`.

---

## 7. Fonti nel catalogo (37)

| Ambito | Fonti | Stato |
|---|---|---|
| Catasto | AdE INSPIRE (particelle, fogli) | verified — no PA Trento/Bolzano |
| Patrimonio culturale | 13 layer MiC SITAP | verified |
| Patrimonio culturale | UNESCO poligoni, Soprintendenze paesaggio, Vincoli in Rete, Carta del Rischio | **non operative**, censite |
| Natura | EEA Natura 2000 SCI/SPA | verified |
| OSM | 10 sorgenti Overpass | verified |
| Terreno | AWS Terrain Tiles + DEM locale | verified / template |
| Basemap | OSM, Esri World Imagery | verified |
| Basemap | Google Map Tiles | declared, `enabled: false` senza chiave |
| Regionali | solo `_TEMPLATE.json` | — |

Nessuna fonte e' `verified` senza `verification_note` e `last_verified`:
`scripts/validate_sources.py` lo impone.

---

## 8. Registro dei rischi

| # | Rischio | Probabilita' | Impatto | Mitigazione |
|---|---|---|---|---|
| R1 | Oggetti QGIS creati nel thread worker | ~~certa~~ **chiusa in P0.3** | crash di QGIS | separazione worker/main thread, presidiata da un test strutturale |
| R2 | Servizi pubblici italiani instabili o lenti | alta | analisi incompleta | retry con backoff, throttling, `DataGap` esplicito, fonti di ripiego dichiarate |
| R3 | Schema di una fonte che cambia senza preavviso | media | attributi errati | mappatura via descrittore, `verification_note` datata, rivalidazione periodica (Phase 1) |
| R4 | GML non conforme (UNESCO SITAP) | certa | feature perse | confronto `numberReturned` / feature lette, errore esplicito |
| R5 | Divergenza API Qt5/Qt6 e QGIS 3/4 | media | rottura su una versione | `qgis.PyQt`, enum con scope, doppia esecuzione della suite |
| R6 | Chiave API in chiaro | bassa | fuga di credenziali | `core/credentials.py`, solo auth DB QGIS o variabile d'ambiente |
| R7 | Interpretazione giuridica implicita | media | **danno all'utente** | `Finding`/`DataGap`, test che vieta formule di verdetto nelle regole |
| R8 | Blend mode persi in stampa/PDF | ~~certa~~ **chiusa in P0.4** | tavole piatte | composito GeoTIFF cotto e usato dal layout |
| R9 | Crescita non governata del catalogo | media | manutenzione | catalogo come configurazione + validatore in CI |
| R10 | Lifecycle GDAL (dataset non chiusi) | media | crash o file bloccati | ordine esplicito di rilascio, `FlushCache` (Phase 1: audit sistematico) |
| R11 | Le tile XYZ non si disegnano nell'harness headless | **certa**, ma **di ambiente** | nessuna prova a pixel che le ortofoto si vedano | accertato in sessione: rete OK (tile da 14,6 KB via `HttpClient`), layer valido, blocco del provider valido **ma trasparente**, `QgsApplication.tileDownloadManager` assente nel binding. Le garanzie su ortofoto e locator sono quindi **strutturali** (raster presente nel riquadro, valido, marcato, ordinato, accreditato); la resa visiva va verificata in una sessione QGIS reale |

---

## 9. Percorso di esecuzione unico

```text
PROJECT AREA
   → AnalysisOrchestrator          (engines/analysis.py)
      → admin → cadastre → constraints → cultural_heritage → terrain → download → rules
   → AnalysisReport                 (core/models.py)
      → cartografia  (engines/cartography/*)
      → relazione    (engines/report.py)
      → pacchetto    (engines/package.py)
```

Dock, command palette, Processing e API Python percorrono **lo stesso codice**: la GUI e' un
punto di accesso, non un'implementazione alternativa.
