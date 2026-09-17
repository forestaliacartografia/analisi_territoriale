# Patrimonio culturale e paesaggistico (modulo MiC)

Il modulo trasforma i dati del Ministero della Cultura in un quadro conoscitivo
strutturato: acquisizione, normalizzazione, classificazione, deduplicazione,
individuazione della Soprintendenza competente, valutazione della qualita' del dato e
sezione dedicata nel dossier.

---

## 1. La regola che governa tutto il modulo

Il plugin distingue **quattro cose diverse** che un'implementazione ingenua confonderebbe:

| Classificazione | Significato |
|---|---|
| `DATA_PRESENT` | esiste un dato cartografico sul tema e la fonte ha risposto |
| `AREA_INTERSECTS_DATA` | l'area di progetto interseca geometricamente quel dato |
| `OFFICIAL_INFORMATION` | il dato e' pubblicato dall'ente competente per quel tema |
| `LEGAL_REFERENCE_PRESENT` | l'elemento riporta gli estremi di un atto amministrativo |

Solo la quarta autorizza a parlare di un provvedimento, e anche in quel caso il plugin
**trascrive il riferimento dichiarato dalla fonte**, non una conclusione propria.

Le frasi prodotte sono quindi della forma:

> L'area di progetto interseca il dato cartografico "TERRITORIO DELLE COLLINE A SUD DI
> FIRENZE..." della fonte Beni paesaggistici dichiarati - art. 136 (SITAP) (12,4%
> dell'area). La fonte riporta il riferimento: L 1497/39 - 1951-10-26 - ente: MPI
> (da verificare presso l'ente competente).

e **mai** «l'area e' vincolata». Un test della suite verifica che nessuna regola fornita
con il plugin contenga formule di questo tipo.

---

## 2. Architettura

```text
engines/cultural_heritage/
├── model.py          record normalizzati, Finding, DataGap, ActReference, Superintendency
├── normalizer.py     da FeatureHit a CulturalAsset, guidato dai campi del descrittore
├── classifier.py     tassonomia + lettura dei nomi dei layer regionali SITAP
├── deduplicator.py   fusione conservativa che non perde la provenienza
├── quality.py        perche' un tema e' vuoto: sei esiti distinti
├── report.py         sezione del dossier + righe per CSV/XLSX
└── engine.py         orchestratore
```

**Acquisizione e misura spaziale non sono reimplementate qui.** Le feature sono scaricate
da `services.fetcher` e misurate da `engines.spatial`, esattamente come per ogni altra
categoria: una fonte culturale eredita cache, paginazione, throttling, annullamento,
provenance e politica di errore del resto del catalogo. Il modulo aggiunge solo cio' che e'
specifico del patrimonio.

---

## 3. Tassonomia

Le sotto-categorie vivono sotto `landscape_cultural` e non aggiungono sezioni al dossier:

| Categoria | Contenuto |
|---|---|
| `cultural_heritage_assets` | beni culturali tutelati (art. 10), giardini storici, anagrafica VIR |
| `archaeological_heritage` | aree e beni archeologici |
| `landscape_assets_declared` | beni paesaggistici dichiarati (art. 136, ex L. 1497/1939) |
| `landscape_areas_by_law` | aree tutelate per legge (art. 142 c. 1 lett. a–m) |
| `unesco_heritage` | siti UNESCO e componenti |
| `heritage_administration` | ambiti di competenza delle Soprintendenze |

Una regola scritta per `landscape_cultural` scatta anche sulle sotto-categorie; una regola
con `"match": "exact"` scatta solo sulla categoria indicata, e serve appunto perche' la
regola generica non si sommi a quelle specifiche.

---

## 4. Normalizzazione

I campi sono mappati dal blocco `fields` del descrittore: aggiungere una fonte e' una
modifica JSON. Chiavi riconosciute:

```text
label  unnamed_label  code  type  class  municipality  province  region
act_law  act_document  act_date  act_authority  holder  place  period  ownership
website  pec  office_type
```

Regole applicate:

* **niente invenzioni**: un valore che la fonte non pubblica resta `unknown`;
* le **entita' HTML** dei testi SITAP (`piazza de&#39; Mozzi`) vengono risolte;
* gli oggetti **`QDate`/`QDateTime`** restituiti dal provider OGR diventano `YYYY-MM-DD`;
* un valore che **non e' un nome** ma un codice (`tipobos = 1`, `inside = 100.0`) non
  viene stampato come denominazione del bene: si usa `fields.unnamed_label`, cioe' il tema
  dichiarato dal descrittore, e il codice resta negli attributi;
* il campo `ente` di `tab_benitutelati2` e' il **soggetto detentore** del bene
  (`Comune di Firenze`, `Inail`), non la Soprintendenza: non viene mai usato come tale.

---

## 5. Soprintendenze

```text
Project Area -> feature culturale -> fonte -> ente -> Soprintendenza
```

La Soprintendenza competente e' determinata **solo** dal layer ufficiale degli ambiti
(`sitap_ws:wms_soprintendenze_attuali`). Se il layer non risponde o non copre l'area, il
valore resta `unknown` e il dossier lo dice: la competenza territoriale degli uffici
periferici non coincide con i confini amministrativi, quindi dedurla dalla regione
sarebbe una deduzione arbitraria.

Un'area a cavallo di un confine puo' ricadere in **piu'** ambiti: vengono riportati tutti.

---

## 6. Deduplicazione

Due passaggi, entrambi conservativi.

### 6.1 Righe ripetute dalla stessa fonte

Alcune viste SITAP pubblicano lo stesso poligono **due volte**, con `gml_id` diversi ma
geometria e attributi identici (verificato il 2026-09-17 su `vw_vasvia_aree_di_rispetto` e
`vw_vasvia_boschi`). I doppioni esatti vengono scartati e il dossier riporta:

> N record duplicati esattamente dalla stessa fonte sono stati scartati (...): il servizio
> pubblica piu' volte lo stesso elemento. I conteggi riportati sono quelli depurati.

La **superficie** interessata non era comunque gonfiata, perche' `engines.spatial` la
calcola sull'unione delle intersezioni.

### 6.2 Stesso bene su fonti diverse

Due record vengono uniti solo con evidenza inequivocabile:

* stesso **codice di atto** non vuoto, oppure
* **nome normalizzato identico** *e* stesso comune.

Il record che sopravvive e' quello con il valore probatorio piu' alto
(`verified_act` > `declaratory` > `cartographic`); l'altro lascia il proprio id in
`merged_with`, contribuisce i campi che al vincitore mancano e incrementa `source_count`.
Due record della **stessa** fonte con lo stesso nome non vengono mai uniti: sono due beni
distinti, o due provvedimenti sullo stesso bene.

---

## 7. Qualita' del dato

Sei esiti distinti, che una implementazione ingenua stamperebbe tutti come «nessun bene
rilevato»:

| Esito | Significato | Vuol dire «non c'e' nulla»? |
|---|---|---|
| `NO_FEATURE_FOUND` | la fonte ha risposto, l'area e' libera | **si'** |
| `NO_DATA` | nessuna fonte configurata per il tema | no |
| `SOURCE_UNAVAILABLE` | il servizio non ha risposto | no |
| `QUERY_FAILED` | ha risposto qualcosa di inutilizzabile | no |
| `SOURCE_OUTSIDE_COVERAGE` | l'area e' fuori dalla copertura dichiarata | no |
| `SOURCE_NOT_VERIFIED` | il dato esiste ma non e' interrogabile dal plugin | no |

`SOURCE_OUTSIDE_COVERAGE` scatta solo su una voce **esplicita** di `coverage.missing` nel
descrittore: il plugin non deduce mai da solo che un silenzio significhi «qui non e'
mappato».

---

## 8. Sezione del dossier

La sezione 14 del quadro conoscitivo e' articolata in dodici sottosezioni: sintesi, beni
culturali, patrimonio paesaggistico, fonti MiC/SITAP, Soprintendenze, analisi spaziale,
prossimita', qualita' del dato, fonti interrogate, fonti non disponibili, copertura delle
fonti, note e limitazioni. Si attiva quando il modulo ha prodotto un esito, e si disattiva
da *Impostazioni > Report*.

Le righe per tema sono limitate (`report.max_heritage_rows`, 40 di default) e il troncamento
e' sempre dichiarato; l'elenco completo finisce nel CSV/XLSX allegato.

---

## 9. Fonti

Le fonti operative, con l'evidenza della verifica, sono nella
[matrice delle fonti](SOURCE_MATRIX.md). In sintesi: 13 layer SITAP operativi
(art. 136, art. 142 lett. g/h/i/l, beni tutelati, giardini storici, Vincoli in Rete, aree
archeologiche, UNESCO, Soprintendenze, aree di rispetto) e 5 dataset censiti ma non
interrogabili, che restano nel catalogo per documentare il gap.

I 144 layer regionali del namespace `sitap_ws_clone` non sono descritti a mano: il
classificatore legge il nome del feature type (`CAMPANIA_art_142_g_boschi` →
regione Campania, art. 142 lett. g, categoria `landscape_areas_by_law`) e scarta i nomi
che non riconosce, invece di archiviarli in una categoria plausibile.

---

## 10. Uso

**Interfaccia** — il modulo gira dentro l'analisi one-click; la sezione compare nel dossier
e le tabelle nel pacchetto esportato.

**Processing** — `territorial_suite:cultural_heritage` accetta l'area come sorgente
poligonale e produce CSV e JSON:

```bash
qgis_process run territorial_suite:cultural_heritage --AREA=area.gpkg --OUTPUT_CSV=beni.csv
```

**API**

```python
from territorial_suite.engines.cultural_heritage import CulturalHeritageEngine

outcome = CulturalHeritageEngine().run(area)
for asset in outcome.intersecting:
    print(asset.statement_it())
```
