# Probe log — Regione Toscana / GEOscopio

Evidenza raccolta **eseguendo le richieste**, non leggendo la documentazione.
Nessun endpoint di questo file e' stato dedotto: l'indice dei servizi e' stato letto da
<https://www.regione.toscana.it/-/geoscopio-wms>, che pubblica una scheda `.htm` per ciascuno
dei 43 temi.

Data della sessione di probe: 2026-09-17.

## 1. Vincolo idrogeologico — R.D.L. 3267/1923

**Scheda ufficiale:** `http://www502.regione.toscana.it/geoscopio/servizi/wms/VINCOLO_IDROGEOLOGICO.htm`
**Endpoint (dalla voce «Risorsa online» della scheda):**
`https://www502.regione.toscana.it/ows_idrogeologico/com.rt.wms.RTmap/wms?map=owsidrogeologico`

| Operazione | Esito | Evidenza |
|---|---|---|
| WMS GetCapabilities 1.3.0 | **OK** | HTTP 200, `text/xml`, 22.002 byte, `<WMS_Capabilities version="1.3.0">` |
| WMS GetMap (`idrd32671923`) | **OK** | HTTP 200, `image/png`, 3.418 byte, 22.279 px dipinti su 160.000 |
| WMS GetFeatureInfo (`idrd32671923`) | **OK** | HTTP 200, attributi reali: `id_idr`, `idrt`, `atto`, `pk_uid` |
| WFS GetCapabilities 2.0.0 | **OK** | HTTP 200, 26.180 byte, sullo **stesso** endpoint con `service=WFS` |
| WFS GetFeature (`idrd32671923`) | **FALLITO** | HTTP 400 + `ows:ExceptionText`: «TYPENAME 'rt_idrogeol.idrd32671923.rt.poly' doesn't exist in this server» |
| WFS GetFeature (`areeboscate.2016`) | **OK** | HTTP 200, `numberReturned="1"`, geometria GML reale, comune AULLA |

### Conseguenza di progetto — la piu' importante di questo log

Il WFS di GEOscopio espone **solo** i quattro strati `areeboscate.*`. **Non** espone lo strato
delle perimetrazioni del R.D. 3267/1923.

Quindi, per la Toscana, il vincolo idrogeologico e' **`VIEW_ONLY`**: consultabile come immagine e
interrogabile per punto, **non analizzabile geometricamente**. Non e' possibile calcolare
`area_vincolata_m2` ne' `percentuale_vincolata` da questa fonte.

Le due trappole da cui questo log protegge:

1. scaricare `areeboscate` via WFS, calcolarci sopra una percentuale e chiamarla «vincolo
   idrogeologico». Sono dataset **diversi**: le aree boscate hanno, per dichiarazione della
   fonte, «valore meramente ricognitivo»;
2. dedurre da un WFS che non restituisce nulla che l'area non sia vincolata.

### Avvertenza della fonte, da citare nel dossier

La scheda ufficiale dichiara che il vincolo «ha valore **meramente ricognitivo**» e che la
valutazione della presenza del bosco e del relativo vincolo «va fatta **in situ con rilievo a
terra**» (L.R. 39/2000 art. 3 e regolamento forestale).

La ricognizione e' stata operata **dalle singole province** nel 2011-2012 su base CTR 1:10.000,
in sostituzione della copertura unica del 1983 a scala 1:25.000.

### Strati pubblicati

| Strato | WMS | WFS |
|---|---|---|
| `rt_idrogeol.idrd32671923.rt.poly` (R.D. 3267/1923) | si | **no** |
| `rt_idrogeol.areeboscate.2007.rt.poly` | si | si |
| `rt_idrogeol.areeboscate.2010.rt.poly` | si | si |
| `rt_idrogeol.areeboscate.2013.rt.poly` | si | si |
| `rt_idrogeol.areeboscate.2016.rt.poly` | si | si |

### CRS — verificato per rendering, non per dichiarazione

Le capabilities annunciano 12 CRS. Provati sullo stesso punto (Aulla, 3003: 1572327, 4890895):

| CRS | Esito |
|---|---|
| EPSG:3003 (nativo) | disegna (7.111 byte) |
| EPSG:3857 | disegna (6.722 byte) |
| EPSG:32632 | disegna (7.046 byte) |
| EPSG:25832 | disegna (7.015 byte) |
| EPSG:4326 | **vuoto** (127 byte) |

EPSG:4326 fallisce per l'ordine degli assi di WMS 1.3.0 (latitudine prima). Il descrittore
deve quindi preferire **EPSG:3003** e la sonda di salute deve trattare
«HTTP 200 + `image/png` + immagine vuota» come **fallimento**, non come successo.

Una richiesta sull'intera Toscana (600x500 px, ~1:600.000) restituisce immagine vuota pur
essendo dichiarata visibilita' 1:1 - 1:5.000.000: esiste una soglia di scala non documentata.

### Copertura — campionata, non dichiarata

Finestre di 5 km per lato, conteggio dei pixel dipinti:

| Sito | px dipinti | `id_idr` |
|---|---:|---|
| Aulla (MS) | 17.203 | prefisso 045 |
| Abetone (PT) | 6.055 | — |
| Vallombrosa (FI) | 392 | 0480000001 |
| Amiata (SI/GR) | 451 | 0530000014 |
| Casentino (AR) | 19.476 | 0510000001 |
| Alpi Apuane (LU) | **0** | — |
| Colline Pisane (PI) | 9.615 | 0500000030 |
| Elba (LI) | 12.780 | — |

I prefissi di `id_idr` sono codici ISTAT provinciali, coerenti con la ricognizione per provincia.

**Cautela:** lo zero sulle Apuane deriva da **una sola** finestra campione e non autorizza
alcuna conclusione sulla provincia di Lucca. E' registrato qui come motivo per non inferire mai
l'assenza di vincolo dall'assenza di pixel.

## 2. Temi GEOscopio pertinenti a questa slice

Dall'indice ufficiale (43 temi). Endpoint e strati **non ancora sondati** se non indicato.

| Tema | Scheda | Stato |
|---|---|---|
| `VINCOLO_IDROGEOLOGICO` | si | **sondato**, vedi sopra |
| `CTR` | si | da sondare |
| `SUSCETTIBILITA_FRANE` | si | da sondare |
| `DB_GEOMORFOLOGICO` | si | da sondare |
| `IDROGRAFIA` | si | da sondare |
| `SENTIERISTICA` | si | da sondare |
| `TOPONOMASTICA` | si | da sondare |
| `EDIFICATO` | si | da sondare |
| `MORFOLOGIA` | si | da sondare |

**Assente dall'indice GEOscopio:** un tema di pericolosita'/rischio **idraulico** (PGRA / PAI).
La competenza e' dell'Autorita' di Bacino Distrettuale dell'Appennino Settentrionale, non della
Regione: la fonte va cercata li', non inventata dentro GEOscopio.

**Da verificare:** le Zone Vulnerabili ai Nitrati sono citate nella pagina indice ma non hanno
una scheda `.htm` con quel nome; il tema va localizzato prima di essere descritto.

## 3. Zone Vulnerabili ai Nitrati — sondate il 2026-09-17

**Rettifica.** Nella prima ricognizione avevo riportato le ZVN fra i temi elencati
dall'indice GEOscopio. Il testo della pagina **non le contiene**: quell'informazione
veniva da una sintesi automatica della pagina, non dal suo HTML, e non andava accettata.
La scheda esiste ma sotto un indirizzo che l'indice scaricato non riportava:
`https://www502.regione.toscana.it/geoscopio/servizi/wms/ZONE_VULNERABILI_NITRATI.htm`

**Endpoint:** `https://www502.regione.toscana.it/wmsraster/com.rt.wms.RTmap/wms?map=owszvn&language=ita`

| Operazione | Esito |
|---|---|
| WMS GetCapabilities | OK, 19 strati |
| WFS GetCapabilities 1.1.0 | OK, **sei** tipi `rt_zvn.zvn1..zvn6.rt.poly` |
| WFS GetFeature | OK, geometria reale, 217 KB per una feature |

La scheda WMS documenta **un solo** strato (`rt_zvn.zvn.rt.poly`); il WFS ne espone sei.

Attributi verificati: `zona` (denominazione ufficiale, es. «Zona costiera tra San Vincenzo
e la Fossa Calda»), `idrt` (es. `RT0030`), piu' un flag per ciascuna delibera —
`z_07_520_a`, `z_07_520_b`, `z_07_521`, `z_07_522_a`, `z_07_522_b`, `z_21_018_a`,
`z_21_018_b`, `z_21_018_c` — che dice **con quale atto** quel poligono e' stato designato.

**Classificazione: `ANALYSABLE`.**

**Avvertenza della fonte, da citare:** la perimetrazione di dettaglio e' individuata per
**fogli catastali** negli allegati alle D.G.R. 520/2007, 521/2007, 522/2007 e 18/2021; lo
strato cartografico ne e' la «trasposizione cartografica».

## 4. Carta Tecnica Regionale — sondata il 2026-09-17

**Endpoint:** `https://www502.regione.toscana.it/ows_ctr/com.rt.wms.RTmap/ows?map=owsctr`

| Operazione | Esito |
|---|---|
| WMS GetCapabilities | OK, **68** strati (`rt_ctr.10k`, `rt_ctr.2k.liv2.any`, varianti `greylight`, `impianto`) |
| WFS GetCapabilities | OK, ma **5 soli** tipi: `inq.ctr.2k/5k/10k.wfs_search`, `inq.igm.f50k.wfs_search`, `idpgeodet.raf7km.rt` |

**La banca dati topografica vettoriale non e' pubblicata qui.** Il WFS della CTR espone
soltanto gli **indici dei fogli** (quadri d'unione per la ricerca) e un layer di punti
geodetici. I 68 strati WMS sono fogli cartografici resi come immagine.

Verificato anche `BASI_TOPOGRAFICHE`: e' la carta 1:50.000, anch'essa raster.

**Conseguenza:** curve di livello, idrografia, viabilita', sentieri, toponimi ed edificato
**non** si ottengono dalla CTR. La Regione li pubblica in **temi dedicati**, ciascuno con
il proprio WFS, verificati lo stesso giorno:

| Tema | Endpoint | typeNames |
|---|---|---|
| IDROGRAFIA | `wmsraster/...?map=wmsidrogr` | 9, fra cui `corsi.rt.line`, `tronchi.rt.line`, `bacini_idrografici_amministrativi.rt.poly` |
| SENTIERISTICA | `ows_sentieristica/...?map=owssentieristica` | `rt_sent.idsentrei.rt` |
| TOPONOMASTICA | `wmsraster/...?map=wmstoponomastica` | `toponimi_ctr10k_ricomposti.poly` |
| EDIFICATO | `ows2/...?map=owsedificato` | `rt_edif.unitavolumetriche`, `rt_edif.centri_matrice.poly` |

I toponimi si chiamano `toponimi_ctr10k`: derivano dalla CTR 1:10.000. Il contenuto che si
attribuisce alla «CTR interrogabile» esiste, ma distribuito per tema, ed e' li' che va
preso.

Classificazione: CTR `DISPLAY_ONLY` come base cartografica, piu' `ANALYSABLE` per gli
indici dei fogli e per i quattro temi vettoriali dedicati.
