# Source Matrix — patrimonio culturale e paesaggistico

> Ogni riga marcata `verified` è stata **interrogata davvero** il **2026-09-17** con il
> client WFS del plugin (`territorial_suite/services/ogc/wfs.py`), su aree di test reali.
> Conteggi e tempi riportati sono quelli osservati. Nessun endpoint è stato dedotto o
> ipotizzato: quanto non verificabile resta catalogato come non operativo.

---

## 1. Servizio

| Voce | Valore verificato |
|---|---|
| Sistema | **SITAP** — Sistema Informativo Territoriale Ambientale Paesaggistico |
| Ente | Ministero della Cultura |
| Endpoint WFS | `https://sitap.cultura.gov.it/geoserver/wfs` |
| Versione | WFS **2.0.0** (GeoServer) |
| Feature type pubblicati | **260** (GetCapabilities 245.686 byte in 562 ms) |
| Namespace | `sitap_ws` (76), `sitap_ws_clone` (144), `sitap_wms_bounce` (25), `sitap_public` (8), `sitap_wfs_ws` (6) |
| Documentazione | `https://sitap.cultura.gov.it/` |
| Licenza | **non dichiarata dal servizio** (pagina verificata il 2026-09-17: nessuna clausola di licenza, riuso o attribuzione) |
| Attribuzione usata | `Ministero della Cultura - SITAP` |
| Avvertenza del produttore | i dati di fonte regionale sono «suscettibili di riallineamento periodico»; il portale rinvia ai portali cartografici regionali per il dato originario aggiornato |

### Aree di test (bbox WGS84)

| Chiave | Bbox | Contesto |
|---|---|---|
| firenze | 11.24, 43.76 → 11.27, 43.78 | centro urbano storico |
| casentino | 11.75, 43.82 → 11.82, 43.88 | montagna, parco nazionale |
| napoli | 14.38, 40.81 → 14.45, 40.86 | area vulcanica |
| roma | 12.46, 41.88 → 12.50, 41.91 | centro storico UNESCO |
| milano / palermo / bolzano / bari | box urbani | controllo di copertura |

---

## 2. Matrice — fonti operative (`verified`)

| ID plugin | Layer (typename) | Categoria | CRS | Natura | Evidenza raccolta | Campi normalizzati |
|---|---|---|---|---|---|---|
| `it.mic.sitap.art136.poligoni` | `sitap_ws:wms_sitap_v1497_pol_136` | `landscape_assets_declared` | 32632 | perimetro | FI **7**/733 ms · MI **5**/1078 ms · BZ **76**/9937 ms · BA **13**/1047 ms · RM **18**/625 ms · PA 0/203 ms | `codvin`, `oggetto`, `data_decreto`, `ente`, `legge` |
| `it.mic.sitap.art136.poligoni.storico` | `sitap_ws:v1497pol_wgs84` | `landscape_assets_declared` | 32632 | perimetro | FI **5**/1218 ms | `codvr`, `uso`, `area__mq_` |
| `it.mic.sitap.beni.tutelati` | `sitap_ws:tab_benitutelati2` | `cultural_heritage_assets` | 32632 | anagrafica (punti) | FI **75**/655 ms · PA **9**/641 ms · MI/BZ/BA/RM 0 | `bene`, `ente`, `toponimo`, `nome_comun`, `cod_istat` |
| `it.mic.sitap.giardini` | `sitap_ws:tab_giardini_2` | `cultural_heritage_assets` | 32632 | anagrafica (punti) | FI **48**/641 ms · MI **62**/641 ms · RM **6**/717 ms · BA **1**/812 ms | `nome_giard`, `epoca`, `legge`, `documento`, `proprieta` |
| `it.mic.sitap.vir.beni` | `sitap_ws:tab_vir_v_geo_anagrafica_beni` | `cultural_heritage_assets` | 4326 | anagrafica (punti) | FI/MI/PA/BA/RM **400** (cap) in 1,3–1,6 s · BZ **97**/703 ms | `denominazi`, `tipo_bene`, `classe`, `comune`, `provincia` |
| `it.mic.sitap.vir.aree_archeologiche` | `sitap_ws:tab_vir_geo_aree_archeol_vincolate` | `archaeological_heritage` | 4326 | perimetro | RM **45**/703 ms · MI **8**/781 ms · FI/PA/BZ/BA 0 | `nome`, `tipo` |
| `it.mic.sitap.unesco.punti` | `sitap_ws:Unesco_point` | `unesco_heritage` | 32632 | anagrafica (punti) | RM **9**/687 ms | `cod_unesco`, `sito`, `componente`, `tipo_area` |
| `it.mic.sitap.unesco.punti.2026` | `sitap_wms_bounce:unesco_official_2026_point` | `unesco_heritage` | 3857 | anagrafica (punti) | RM **1**/875 ms | `dossier`, `property_s` |
| `it.mic.sitap.soprintendenze` | `sitap_ws:wms_soprintendenze_attuali` | `heritage_administration` | 32632 | ambito di competenza | FI **2**/967 ms · MI **2**/859 ms · BA **1**/875 ms · RM **1**/859 ms · PA/BZ 0 | `codice`, `denominazione`, `sito`, `pec`, `tipo_ufficio` |
| `it.mic.sitap.art142g.boschi` | `sitap_ws:vw_vasvia_boschi` | `landscape_areas_by_law` | 23032 | perimetro | FI **2**/4484 ms | `tipobos` |
| `it.mic.sitap.art142h.usi_civici` | `sitap_ws:tab_usi_civici` | `landscape_areas_by_law` | 32632 | perimetro | verificato in fase 1 (Casentino **1**/0,6 s); 0 nel box 2026-09-17 | `codice`, `gdi` |
| `it.mic.sitap.art142l.vulcani` | `sitap_public:vulcani` | `landscape_areas_by_law` | 32632 | perimetro | NA **2**/921 ms | `nome`, `codice` |
| `it.mic.sitap.art142i.zone_umide` | `sitap_ws:vw_vasvia_zone_umide` | `wetlands_ramsar` | 23032 | perimetro | servizio OK, 0 feature nei box di test (203 ms) | — |
| `it.mic.sitap.aree_rispetto` | `sitap_ws:vw_vasvia_aree_di_rispetto` | `landscape_areas_by_law` | 23032 | perimetro | FI **10**/952 ms | `inside` |

### Note di copertura registrate nei descrittori

* **Sicilia** e **province autonome di Trento e Bolzano** hanno competenza propria in materia
  di paesaggio e beni culturali: su Palermo `wms_sitap_v1497_pol_136`, `tab_giardini_2` e
  `wms_soprintendenze_attuali` rispondono **0**. Un risultato vuoto in quelle aree
  **non significa assenza di tutele**: il descrittore lo dichiara e il report lo scrive.
* `tab_benitutelati2` ha copertura **molto discontinua** (75 su Firenze, 9 su Palermo, 0 su
  Milano, Roma, Bari, Bolzano). Catalogata come `partial` con nota esplicita.
* `tab_vir_v_geo_anagrafica_beni` risponde ovunque ed è l'unica con copertura ampia, ma è
  un'**anagrafica di beni**, non una perimetrazione di vincolo.

---

## 3. Fonti censite ma **non operative**

| Layer | Stato | Motivo verificato |
|---|---|---|
| `sitap_ws:vw_unesco_sito`, `vw_unesco_buffer` | `unverified` | GML con `Invalid surfaceMember`: GDAL non legge le geometrie. Il servizio **risponde**, il dato **non è utilizzabile**. |
| `sitap_wms_bounce:unesco_official_2026_polygon_core` / `_buffer` | `unverified` | stesso errore: 3 e 13 feature annunciate, 0 leggibili |
| `sitap_ws:vw_sitap_soprintendenze_paesaggio` | `unavailable` | risposta oltre 30 s in fase 1; sostituita da `wms_soprintendenze_attuali` (0,9 s) |
| `sitap_ws:vw_sitap_soprintendenze_no_paesaggio` | `declared` | risponde (0 feature sulle aree di test), copertura non dimostrata |
| Portale **Vincoli in Rete** | `discovery_required` | portale web, nessun endpoint GIS pubblicato; i suoi dati sono raggiungibili via i layer `tab_vir_*` di SITAP |
| **Carta del Rischio** (ICCD) | `discovery_required` | consultazione solo web, nessun servizio OGC verificato |
| `pcn.minambiente.it` | `unavailable` | HTTP 500 alla verifica |

> Nessuna di queste viene interrogata durante un'analisi: `DataSource.is_operational` è
> `False` e il Registry le esclude da `query()` e `resolve_for()`.

---

## 4. Layer regionali — **discovery**, non descrittori a mano

SITAP pubblica **144 feature type** nel namespace `sitap_ws_clone` con lo schema
`REGIONE_art_1xx_tema` (più 25 in `sitap_wms_bounce`). Regioni osservate nel
GetCapabilities: Basilicata, Campania, Emilia-Romagna, FVG, Lazio, Liguria, Lombardia,
Piemonte, Puglia, Sardegna, Toscana, Umbria, Veneto, Abruzzo, Calabria.

Campioni verificati il 2026-09-17:

| Layer | Area | Esito |
|---|---|---|
| `sitap_ws_clone:toscana_art136` | firenze | **4** feature / 858 ms |
| `sitap_ws_clone:LAZIO_art_136_c_d` | roma | **10** feature / 782 ms |
| `sitap_ws_clone:CAMPANIA_art_142_m_zone_interesse_archeo_base` | napoli | 0 feature / 280 ms (servizio OK) |

Scrivere 144 descrittori a mano sarebbe insostenibile e si romperebbe al primo
rinominamento lato MiC. Il plugin li ottiene dal **GetCapabilities**, classificandoli con
il pattern `art_136` / `art_142_<lettera>` / `art_10` presente nel nome del layer.

---

## 5. Difetto corretto durante la verifica

`sitap_wms_bounce:unesco_official_2026_polygon_core` rispondeva con
`numberReturned="3"` mentre GDAL non riusciva a leggere nessuna geometria: il client
restituiva **0 feature**, indistinguibile da «in quest'area non c'è nulla».

Il client WFS ora confronta `numberReturned` con le feature effettivamente lette e:

* se ne perde una parte → le conta in `FetchResult.lost_features` e avvisa;
* se le perde **tutte** → solleva `SourceSchemaError`, così la fonte risulta *non
  disponibile* e non *area vuota*.

---

*Ultimo aggiornamento: 2026-09-17.*
