# Probe log — PCN / MASE (Geoportale Nazionale)

Evidenza raccolta **eseguendo le richieste**, non leggendo la documentazione.
Sessione: 2026-09-17. Host: `http://wms.pcn.minambiente.it/ogc?map=...` (HTTP; HTTPS
risponde 301).

Gli endpoint elencati nel prompt sono stati trattati come **candidati**. Nessuno e' stato
dichiarato operativo prima di avere una feature vera in mano.

## 1. Raggiungibilita' e protocollo

I 22 endpoint indicati sono stati provati **sia come WMS sia come WFS**, perche' l'URL non
dice il protocollo.

| Dataset | mapfile | WMS | WFS | Esito |
|---|---|:--:|:--:|---|
| Catalogo Frane | `WMS_v1.3/Vettoriali/Catalogo_Frane.map` | si | si | operativo |
| PAI pericolosita | `WMS_v1.3/Vettoriali/PAI_pericolosita.map` | si | si | operativo |
| PAI rischio | `WMS_v1.3/Vettoriali/PAI_rischio.map` | si | si | operativo |
| ZPE | `WMS_v1.3/Vettoriali/ZPE.map` | si | si | operativo |
| EUAP | `WMS_v1.3/Vettoriali/EUAP.map` | si | si | operativo |
| Natura 2000 | `WMS_v1.3/Vettoriali/SIC_ZSC_ZPS.map` | si | si | operativo |
| Ramsar | `WMS_v1.3/Vettoriali/RAMSAR.map` | si | si | operativo |
| Antincendi PNZ | `WMS_v1.3/Vettoriali/Progetto_Antincendi_Boschivi_PNZ.map` | si | si | operativo |
| Bacini idrografici | `wfs/Bacini_idrografici.map` | no | si | operativo |
| Aste fluviali | `wfs/Aste_fluviali.map` | no | si | operativo |
| Alluvioni estensione | `wfs/Alluvioni_Estensione.map` | no | si | operativo |
| Edifici | `wfs/Edifici.map` | no | si | operativo |
| IUTI | `wfs/IUTI.map` | no | si | operativo |
| Rete ferroviaria | `wfs/Rete_ferroviaria.map` | no | si | operativo |
| **Frane** | `wfs/Frane.map` | no | no | **HTTP 500** |
| **Rete idrografica** | `wfs/Rete_idrografica.map` | no | no | **HTTP 500** |
| **Limiti amministrativi** | `wfs/Limiti_Amministrativi.map` | no | no | **HTTP 500** |
| **Scuole** | `wfs/scuole.map` | no | no | **HTTP 500** |

I quattro 500 sono stati riprovati: costanti, non intermittenti.

Nota richiesta dal prompt (§22): `wfs/Frane.map` e `Catalogo_Frane.map` **non** sono
equivalenti. Il primo e' morto, il secondo funziona. L'avvertenza era fondata.

Le mapfile sotto `WMS_v1.3/Vettoriali/` rispondono **anche** a WFS. Il contrario non vale:
le mapfile sotto `wfs/` restituiscono 500 se interrogate come WMS.

## 2. Versione WFS — la GetCapabilities inganna

`GetCapabilities&version=2.0.0` restituisce HTTP 200 e un documento `WFS_Capabilities`.
Sembra un WFS 2.0. **Non lo e'.** La `GetFeature` con la stessa versione risponde:

> `msWFSDispatch(): WFS server error. WFS Server does not support VERSION 2.0.0.`

MapServer negozia al ribasso sulle capabilities e rifiuta sulla richiesta vera. Il
dialetto operativo e' **WFS 1.1.0**, con `typename` al singolare e `maxfeatures`.

Conseguenza per il plugin: `services/ogc/wfs.py` parla WFS 2.0 con paging a link `next`.
Per PCN serve il dialetto 1.1.0. **Classificare queste fonti come operative sulla base
della sola GetCapabilities sarebbe stato un errore**, ed e' esattamente il caso che il
prompt chiede di non commettere.

Ulteriore trappola: in WFS 1.1.0 con `EPSG:4326` l'ordine degli assi e' **latitudine,
longitudine** (verificato: `lowerCorner 40.785336 14.180588` su un sito campano). Una
BBOX costruita come lon/lat interroga il posto sbagliato.

## 3. Capacita' reale — geometria si, attributi spesso no

Verificato con `GetFeature` (1 feature) **e** con `DescribeFeatureType`, che e' la
dichiarazione autorevole dello schema.

### Fonti con soli dati geometrici

| Dataset | Campi dichiarati |
|---|---|
| Natura 2000 `SP.SITIPROTETTI.SIC_ZSC_ZPS` | **solo `msGeometry`** |
| EUAP `SP.SITIPROTETTI.EUAP` | **solo `msGeometry`** |
| Ramsar, ZPE, PAI (pericolosita e rischio), Catalogo Frane | nessun attributo restituito |

Si puo' calcolare **l'intersezione e la superficie**. Non si possono ottenere
denominazione, codice, tipologia o classe.

Questo risponde a due richieste del prompt in modo negativo, e va detto:

- **§26 EUAP** chiede denominazione, codice e tipologia: da questa fonte **non sono
  disponibili**. Ottenibili: superficie e intersezione. Il resto e' `DATA_GAP`.
- **§27 Natura 2000** chiede di non aggregare SIC/ZSC/ZPS perdendo il tipo ufficiale. Da
  questa fonte **il tipo non e' pubblicato**: i tre stanno in un unico strato senza campo
  che li distingua. Non e' una scelta di progetto, e' un limite della fonte.
  Il plugin ha gia' in catalogo `config/sources/european/natura2000_eea.json` (EEA): per
  questo tema la fonte europea va preferita, e quella PCN resta per la rappresentazione.
- **§23 PAI** chiede classi ufficiali con codice e denominazione: gli attributi non ci
  sono. L'unica distinzione disponibile e' **il nome dello strato**
  (`RN.PAI.PERICOLOSITA.ALLUVIONE`, `...FRANA_01`, `...FRANA_02`).

### Fonti pienamente analizzabili

| Dataset | Attributi verificati |
|---|---|
| Bacini idrografici | `nome_bac`, `nome_corso`, `autorita` (es. «ADB ADIGE»), `ordine`, `foglio_igm`, `dgc_codice` |
| Aste fluviali | `nome`, `tipo` (FIUME), `bacino_pri`, `ordine`, `id_tratta` |
| Alluvioni estensione | `category` (`HighProbabilityHazard`), `source` (`Fluvial`), `rbdname` (distretto), `apsfrcode`, `eu_cd_hp`, `character`, `mechanism` |
| Edifici | `id_edifici`, `codice_istat`, `altezza`, `quota_suolo`, `quota_gronda`, `tipologia` |
| IUTI | `id_25ha`, `cod_90`, `fuso`, `zona` |
| Rete ferroviaria | `nome`, `ente` (Ferrovie dello Stato S.p.a.), `tipologia`, `sede` |

`Alluvioni_Estensione` e' la fonte piu' ricca per il rischio idraulico: porta la semantica
PGRA completa (scenario di probabilita', distretto, codice europeo).

## 4. Semantica — tre denominazioni da non inventare

Il prompt chiede di non espandere acronimi a memoria. Titoli letti dalle capabilities:

- **ZPE** = «Zone di protezione ecologica del Mediterraneo nord-occidentale, del Mar
  Ligure e del Mar Tirreno». E' un tema **marino**, al largo. Per un'area di progetto
  terrestre non intersechera' mai: inserirlo fra i vincoli territoriali sarebbe fuorviante.
- **Progetto Antincendi Boschivi PNZ** = «Progetto incendi - cartografia anti incendi
  boschivi (AIB) **nei Parchi nazionali**». **Non** e' un catasto nazionale delle aree
  percorse dal fuoco: la copertura e' limitata ai parchi nazionali. Espone 32 strati WFS,
  fra cui gli incendi anno per anno dal 2007 al 2015 e oltre.
- **Edifici** = `ED.EDIFICATO.CAPOLUOGHI.` — **solo i capoluoghi**, non l'edificato
  nazionale.

## 5. Strati pubblicati (estratto)

- Catalogo Frane: `POLIGONALI`, `DGPV`, `AREE.FRANE.DIFFUSE`, `DIREZIONI`, `LINEARI`, `PIFF`
- PAI pericolosita / rischio: `.ALLUVIONE`, `.FRANA_01`, `.FRANA_02` (ciascuno)
- Alluvioni: 21 strati, combinazione distretto (`ITE`, `ITF`, `ITG`, `ITH`) x probabilita'
  (`LPH`, `MPH`, `HPH`), annata 2018
- Bacini: `.PRINCIPALI`, `.SECONDARI`
- Aste fluviali: `FIUMI_PRINCIPALI_SECONDARI`, `FIUMI_TORRENTI`, `CORSI_ACQUA`, `ELEMENTI_IDRICI`
- Rete ferroviaria: `NODOFERROVIARIO`, `TRATTAFERROVIARIA`

## 6. Classificazione conclusiva

| Capacita' | Dataset |
|---|---|
| `ANALYSABLE` (geometria + attributi) | Bacini, Aste fluviali, Alluvioni, Edifici, IUTI, Rete ferroviaria |
| `DOWNLOADABLE` (geometria, senza attributi) | EUAP, Natura 2000, Ramsar, ZPE, PAI pericolosita, PAI rischio, Catalogo Frane, AIB |
| `SOURCE_UNAVAILABLE` | Frane, Rete idrografica, Limiti amministrativi, Scuole |

Nessuna fonte e' `verified` finche' un descrittore non porta questa evidenza con
`last_verified` e nota.

## 7. Conteggio feature su AOI reale — 2026-09-17

Sondaggio precedente: raggiungibilita' e schema. Questo: **quante feature arrivano davvero**
su un'area di progetto reale, attraverso il client del plugin (non con curl).

AOI: piana di Pisa, `11.35, 43.69 - 10.45, 43.75` (EPSG:4326), distretto ITC.

| Fonte | Feature | Campi restituiti |
|---|---:|---|
| `pcn.alluvioni.itc.hph` | 35 | `rbdname`, `uomcode`, `apsfrcode`, `category`, ... |
| `pcn.alluvioni.itc.mph` | 52 | idem |
| `pcn.alluvioni.itc.lph` | 35 | idem |
| `pcn.pai.pericolosita.alluvione` | 180 | **solo** `fid`, `gml_id` |
| `pcn.pai.pericolosita.frana_01` | 3 | **solo** `fid`, `gml_id` |
| `pcn.euap` | 1 | **solo** `fid`, `gml_id` |
| `pcn.natura2000` | 2 | **solo** `fid`, `gml_id` |
| `pcn.ramsar` | 1 | **solo** `fid`, `gml_id` |
| `pcn.bacini.principali` | 2 | `nome_bac`, `nome_corso`, `autorita`, ... |
| `pcn.reticolo.fiumi_principali_secondari` | 2 | `nome`, `tipo`, `bacino_pri`, ... |
| `pcn.edificato.capoluoghi` | **5000** | `id_edifici`, `codice_istat`, `altezza`, ... |
| `pcn.iuti` | 351 | `id_25ha`, `cod_90`, ... |
| `pcn.ferrovie.trattaferroviaria` | 43 | `nome`, `ente`, `tipologia`, ... |
| `rt.aree_boscate.2016` | 127 | `comune`, `idrt`, ... |
| `rt.zvn.1` | 0 | l'area non ricade nel gruppo 1: vanno interrogati tutti e sei |

### Due esiti da registrare

**`pcn.pai.rischio.alluvione` e `pcn.frane.poligonali` falliscono la lettura del GML**
(`SourceSchemaError: Cannot read downloaded dataset page_0.gml`). Il servizio risponde 200
e annuncia feature, ma GDAL non ne legge la geometria — lo stesso quadro gia' incontrato
con alcuni strati SITAP.

Questo e' il comportamento **voluto**: il client dichiara un errore invece di riportare
«0 feature», che sarebbe indistinguibile da un'area senza rischio. La conseguenza per il
motore e' che il tema `flood_risk` da PAI risulta `SOURCE_UNAVAILABLE`, non «rischio
assente».

**`pcn.edificato.capoluoghi` restituisce esattamente 5000 feature**, cioe' il tetto
configurato: la risposta e' troncata. Ogni percentuale calcolata su un insieme troncato
descrive le sole feature scaricate, e il motore lo dichiara con `PARTIAL_COVERAGE` invece
di presentarla come completa.
