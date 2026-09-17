# Audit e piano di evoluzione V0.2

> Deliverable 1–6 richiesti prima di modificare il codice.
> Tutte le fonti citate come *operative* sono state **interrogate realmente** il 2026-09-16
> con richieste registrate in questo documento; quelle non verificabili restano `planned`.

---

## 1. ARCHITECTURE AUDIT

### Stato (baseline 0.1.0, 223 test verdi su 3.40 LTR e 4.0)

| Layer | File | Righe | Ruolo | Estendibile senza toccare il codice? |
|---|---:|---:|---|---|
| `core` | 19 | 3.552 | modello, registry, cache, provenance, CRS, misure, tassonomia, settings | ✅ tassonomia e default via JSON |
| `services` | 12 | 1.943 | http, wfs, ogcapi, arcgis, overpass, raster, fetcher, health, vector_io | ⚠️ nuovo protocollo = nuovo client + 1 riga in `fetcher.client_for` |
| `engines` | 19 | 4.888 | admin, catasto, vincoli, terreno, prossimità, regole, download, analisi, report, package, cartografia | ✅ nuove fonti/regole/stili/template via JSON |
| `gui` | 16 | 2.621 | dock, risultati, dialoghi, palette, onboarding | — |
| `processing` | 9 | 1.083 | provider + 12 algoritmi | — |
| `tasks` | 2 | 133 | QgsTask wrapper | — |

### Punti di estensione già disponibili (da riusare, non duplicare)

1. **`DataSourceRegistry`** — carica qualunque JSON in `config/sources/**` + profilo utente + progetto.
2. **`fetcher.fetch_features(source, bbox, crs, …)`** — unico punto d'ingresso per tutti i protocolli.
3. **`Taxonomy`** — categorie da `config/taxonomy.json`, estendibile.
4. **`RuleEngine`** — regole dichiarative.
5. **`LayoutBuilder` + `templates.json`** — template cartografici a blocchi.
6. **`spatial.analyse_layer`** — misure e provenienza già uniformi per ogni sorgente.

### Duplicazioni / debiti individuati

| # | Problema | Effetto | Azione |
|---|---|---|---|
| A1 | `SourceType` non copre STAC/servizi tabellari | non si possono descrivere alcune fonti | estendere enum + adapter |
| A2 | Il registry non ha `verification_status`, `data_nature`, `fallback_sources`, `quality` | non si può distinguere fonte verificata da fonte "dichiarata" | Registry 2.0 |
| A3 | Categorie MiC/aree protette assenti dalla tassonomia | il quadro conoscitivo non le può articolare | nuove categorie gerarchiche |
| A4 | Print engine: 1 sola mappa per layout, niente atlas/batch/QA | produzione professionale limitata | Print Engine 2.0 |
| A5 | Path temporanei costruiti da `source.id` grezzo | crash su id con `:` (typename WFS reali) | **corretto durante l'audit** |
| A6 | `analysis.max_distance_m` poteva superare il buffer scaricato | regole di prossimità silenziosamente inefficaci | **corretto** (clamp + test) |

---

## 2. DATA SOURCE GAP ANALYSIS

| Categoria | 0.1.0 | Necessario | Gap |
|---|---|---|---|
| Catasto | AdE INSPIRE ✅ | — | nessuno |
| Natura 2000 | EEA SCI/SPA ✅ | ZSC/ZPS/SIC distinti | etichettatura per tipo sito |
| Aree protette nazionali | ❌ | parchi nazionali, riserve statali, parchi/riserve regionali, aree marine, altre | **tutto** |
| Beni culturali (art.10) | ❌ | beni tutelati, aree archeologiche, monumenti | **tutto** |
| Beni paesaggistici (art.136/142) | ❌ | vincoli 1497/136, art.142 a–m | **tutto** |
| Soprintendenze | ❌ | ambiti di competenza | fonte pesante, opzionale |
| UNESCO | ❌ | siti e buffer | disponibile |
| Usi civici | ❌ | art.142 h | disponibile |
| Idrografia/viabilità | OSM ✅ | fonti ufficiali regionali | configurabile |
| Terreno | DEM globale ✅ | DTM ufficiali | configurabile |

---

## 3. MiC SOURCE MATRIX — verificata

Servizio: **GeoServer SITAP del Ministero della Cultura** — `https://sitap.cultura.gov.it/geoserver/wfs`
(WFS 2.0, 260 feature type, scoperto dal visualizzatore ufficiale `sitap.cultura.gov.it`).

| Layer (typename) | Contenuto | CRS | Verifica (area, n feature, tempo) | Campi utili | Natura del dato |
|---|---|---|---|---|---|
| `sitap_ws:v1497pol_wgs84` | vincoli ex L.1497/1939 → art. 136 | 32632 | Firenze, **16**, 1,1 s | `codvr`, `uso`, `area__mq_` | dichiarativo |
| `sitap_ws:tab_benitutelati2` | beni tutelati (art. 10) | 32632 | Firenze, **77**, 0,6 s | `bene`, `ente`, `toponimo`, `nome_comun`, `cod_istat` | dichiarativo |
| `sitap_ws:tab_vir_v_geo_anagrafica_beni` | **Vincoli in Rete** – anagrafica beni | 4326 | Firenze, **200+**, 0,7 s | `denominazi`, `tipo_bene`, `comune`, `provincia`, `class` | dichiarativo |
| `sitap_ws:tab_vir_geo_aree_archeol_vincolate` | aree archeologiche vincolate (VIR) | 4326 | Firenze, **12**, 0,6 s | `nome`, `tipo` | dichiarativo |
| `sitap_ws:vw_vasvia_boschi` | boschi art. 142 lett. g | 23032 | Firenze, **48**, 3,4 s | `tipobos` | cartografico |
| `sitap_ws:tab_usi_civici` | usi civici art. 142 lett. h | 32632 | Casentino, **1**, 0,6 s | `codice`, `gdi` | dichiarativo |
| `sitap_public:vulcani` | vulcani art. 142 lett. l | 32632 | Napoli, **4** (Vesuvio), 0,8 s | `nome`, `codice` | cartografico |
| `sitap_ws:vw_vasvia_aree_di_rispetto` | aree di rispetto | 23032 | Firenze, **24**, 1,0 s | `inside` | cartografico |
| `sitap_ws:vw_vasvia_zone_umide` | zone umide (Ramsar art. 142 i) | 23032 | 0 nelle aree di test, servizio OK | — | cartografico |
| `sitap_ws_clone:toscana_art136` | art. 136 Toscana | 3003 | Firenze, **12**, 0,8 s | `ID_BENE`, `VIN_COD`, `DATA_REV` | dichiarativo |
| `sitap_ws_clone:toscana_art142_rispetto_fiumi` | art. 142 c) Toscana | 3003 | Firenze, **4**, 0,8 s | `ID`, `LAYER_ID` | cartografico |
| `sitap_ws_clone:toscana_art142_montagne` | art. 142 d) Toscana | 3003 | Casentino, **12**, 0,9 s | `AREA`, `BENE_ID` | cartografico |
| `sitap_ws:vw_sitap_soprintendenze_paesaggio` | ambiti di competenza Soprintendenze | 32632 | risposta > 30 s | — | informativo |
| `sitap_ws:vw_unesco_sito` / `vw_unesco_buffer` | siti UNESCO | 32632 | servizio OK, **GML non parsabile** da GDAL | `cod_unesco`, `sito` | informativo |
| `sitap_wms_bounce:unesco_official_2026_polygon_core` | siti UNESCO 2026 | 3857 | Firenze, **2**, 0,2 s | `element_na`, `dossier` | informativo |

Catalogo regionale: SITAP espone layer per **Basilicata, Campania, Veneto, Sardegna, Calabria,
Emilia-Romagna, Piemonte, Lombardia, FVG, Lazio, Puglia, Toscana, Liguria, Umbria, Abruzzo**
con la stessa struttura `REGIONE_art_1xx_*`. Vengono resi disponibili tramite **discovery**
dal GetCapabilities, non scrivendo 200 descrittori a mano.

**Esclusi** (non verificabili come servizio): portale Vincoli in Rete (solo web),
Carta del Rischio (solo web), `pcn.minambiente.it` (HTTP 500 al momento della verifica).

---

## 4. PROTECTED AREAS SOURCE MATRIX — verificata

### 4.1 EEA — Nationally designated areas (CDDA v23)

`https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/NatDAv23_Dyna_WM/MapServer`
ArcGIS REST, GeoJSON, filtro spaziale, layer 4 (large scale) — verifica: Foreste Casentinesi,
**5 feature in 0,44 s**, campi `siteName`, `cddaId`, `designationTypeCode`, `iucnCategory`,
`siteArea`, `legalFoundationDate`.

Codifica italiana presa dal **vocabolario ufficiale Eionet**
(`https://dd.eionet.europa.eu/vocabulary/cdda/designationTypeCodeValue/csv`, 28 codici IT):

| Codice | Etichetta ufficiale | Categoria nel plugin |
|---|---|---|
| IT01 | National Park | parchi nazionali |
| IT02 | State Nature Reserve | riserve statali |
| IT03 | Interregional Nature Park | parchi interregionali |
| IT04 | Regional/Provincial Nature Park | parchi regionali |
| IT05 | Regional/Provincial Nature Reserve | riserve regionali |
| IT06 | Natural Monument | monumenti naturali |
| IT07 | Fauna Protection Area | aree protezione fauna |
| IT30 | Other Protected Natural Regional Areas | altre aree protette |
| IT31 | Biogenetic forest reserve | riserve biogenetiche |
| IT37 / IT90 / IT94 | aree marine protette e riserve marine | aree marine protette |
| IT41 / IT42 | SIC / ZSC | Natura 2000 |
| IT11, IT13, IT14, … | vincoli paesaggistici e idrogeologici | altre tutele |

### 4.2 Fonti nazionali MiC (SITAP) con **codice EUAP**

| Layer | Verifica | Campi |
|---|---|---|
| `sitap_public:parchi_nazionali` | Casentino, **1**, 0,9 s | `denominazione`, `tipo`, `euap` |
| `sitap_ws:vw_vasvia_parchi_regionali` | Napoli, **1**, 0,8 s | `denominazione`, `euap` (es. EUAP0958) |
| `sitap_public:parchi_regionali` | servizio OK | idem |

### 4.3 Natura 2000 (già presente)

EEA `Natura2000Sites` layer 0 (Habitats) e 1 (Birds) — invariati, arricchiti con
`SITETYPE` per distinguere SIC/ZSC/ZPS.

### 4.4 Ramsar

Nessun servizio geografico ufficiale verificato: le zone umide Ramsar sono raggiungibili
tramite i layer art. 142 lett. i) di SITAP (nazionale `vw_vasvia_zone_umide` e regionali).
Stato nel catalogo: **operativa via SITAP**, `discovery_required` per una fonte dedicata.

---

## 5. PRINT ENGINE GAP ANALYSIS

| Capacità | 0.1.0 | Obiettivo 0.2 | Gap |
|---|---|---|---|
| Template | 12, a blocchi, JSON | + libreria per famiglie, import/export | medio |
| Formati pagina | A4–A0, portrait/landscape | idem, da configurazione | ok |
| Scala | automatica su lista configurata | AUTO / MANUAL / **LOCKED** | piccolo |
| Map frame | uno per layout | **multi-map**: principale + inquadramento + dettaglio | **grande** |
| Legenda | filtrata, max N, nomi accorciati | + raggruppamento semantico per categoria, colonne | medio |
| Griglia | reticolo + annotazioni | + scelta automatica formato coordinate | piccolo |
| Nord / barra scala | SVG + barra automatica | ok | ok |
| Serie di tavole | una tavola per tema | + **atlante a griglia** con indice e numerazione | **grande** |
| Batch | assente | **coda di produzione** con stati e log | **grande** |
| QA | assente | **validazione layout** prima dell'export | **grande** |
| Export | PDF/PNG/JPEG/SVG (+DXF layer) | + TIFF, preset qualità, PDF multipagina | medio |
| Metadati output | fonti, data, CRS nel pannello | + metadati incorporati nel PDF | piccolo |
| Anteprima | apertura designer QGIS | anteprima immagine nel dialogo | medio |

---

## 6. IMPLEMENTATION PLAN

| Fase | Contenuto | Esito atteso |
|---|---|---|
| **1** ✅ | Audit + verifica fonti (questo documento) | matrici verificate |
| **2** ✅ | Registry 2.0 (`data_nature`, `verification_status`, `fallback_sources`, `quality`, `coverage`) + tassonomia gerarchica cultura/aree protette | descrittori più espressivi, retrocompatibili |
| **3** ✅ | Cultural Heritage Engine + motore ortofoto + profili di layout (vedi `docs/CULTURAL_HERITAGE.md`, `docs/LAYOUT_ENGINE.md`, `docs/SOURCE_MATRIX.md`) | 13 fonti MiC operative, sezione 14 del dossier, tavola ortofoto one-click, 321 test verdi |
| **4** | Protected Areas Engine: CDDA + codelist ufficiale + parchi SITAP, con classificazione per categoria | aree protette articolate, non un mega-layer |
| **5** | Print Engine 2.0: auto-designer, multi-map, legenda semantica, scale/grid engine, QA | tavole professionali |
| **6** | Atlas + map series a griglia + batch + coda | atlanti e produzione massiva |
| **7** | Export presets, TIFF, metadati PDF | output professionale |
| **8** | Workflow end-to-end + UX Quick/Pro | "one area → dossier" |
| **9** | Regressione completa 3.40 + 4.0, stress test | nessuna regressione |
| **10** | Documentazione + release 0.2.0 | pacchetto |

Vincolo trasversale: **nessun URL negli engine**, tutto nei descrittori; ogni fase si chiude
con la suite verde su entrambe le versioni di QGIS.

---

## 7. STATO AL TERMINE DELLA FASE 3 (2026-09-17)

| Voce | Valore |
|---|---|
| Test | **321 verdi** su QGIS 3.40.15 LTR e QGIS 4.0.0 (baseline fase 1: 223, fase 2: 243) |
| Sorgenti nel catalogo | 36, di cui 5 censite come non operative |
| Fonti MiC operative | 13 layer SITAP, tutti verificati con richieste reali il 2026-09-17 |
| Moduli nuovi | `engines/cultural_heritage` (7 file), `engines/cartography/{imagery,orthophoto,profiles}`, `services/google_tiles`, `core/credentials` |
| Algoritmi Processing | 15 (3 nuovi) |
| Documenti | `SOURCE_MATRIX.md`, `CULTURAL_HERITAGE.md`, `LAYOUT_ENGINE.md` |

### Debiti chiusi in fase 3

| # | Problema | Esito |
|---|---|---|
| A1 | `SourceType` non copre STAC/servizi tabellari | STAC gia' presente nell'enum; nessun adapter richiesto dalle fonti verificate |
| A3 | Categorie MiC/aree protette assenti dalla tassonomia | **chiuso** in fase 2 e usato in fase 3 |

### Difetti trovati e corretti interrogando i servizi reali

| Difetto | Effetto | Correzione |
|---|---|---|
| Feature annunciate e non leggibili dal GML | «0 feature» indistinguibile da «area libera» | confronto `numberReturned` / feature lette; errore di schema se si perde tutto |
| Viste SITAP che pubblicano lo stesso poligono due volte | conteggi raddoppiati nel dossier | scarto dei doppioni esatti, dichiarato nel report |
| `validate_sources.py --network` | segmentation fault a ogni esecuzione | riferimento a `QgsApplication` mantenuto per la durata dello script |
| `TERRAIN_TILES` senza sonda di health | sempre `UNKNOWN`, contato come fallimento | sonda sulla tile 0/0/0; solo `OFFLINE` e' fallimento |
| Layer XYZ «valido» con host inesistente | tavola bianca con attribuzione corretta | una tile reale viene scaricata prima di dichiarare utilizzabile il fornitore |

### Aperto per le fasi successive

* **Fase 4** — Protected Areas Engine (CDDA + codelist EUAP + parchi SITAP).
* **Fase 5–7** — Print Engine 2.0: multi-map, atlante, batch, coda di stampa, QA
  cartografico, preset di export, TIFF, metadati PDF.
* Perimetri UNESCO: inutilizzabili finche' il MiC non corregge il GML
  (`Invalid surfaceMember`); restano censiti come `unverified`.
* Soprintendenze in Sicilia, Trentino-Alto Adige e Valle d'Aosta: competenza regionale,
  fonti nazionali mute. Dichiarato nei descrittori e nel dossier.
