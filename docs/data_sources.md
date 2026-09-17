# Sorgenti dati

Le sorgenti sono **dati, non codice**: file JSON che il plugin carica all'avvio.

```
territorial_suite/config/sources/       sorgenti incluse nel plugin
<profilo QGIS>/territorial_suite/sources/   sorgenti dell'utente (sopravvivono agli aggiornamenti)
progetto QGIS                                override per singolo progetto (attiva/disattiva)
```

L'ordine di merge e' *incluse → utente → progetto*: un file utente con lo stesso `id`
sovrascrive i singoli campi della sorgente inclusa.

## Anatomia di un descrittore

```jsonc
{
  "id": "it.<regione>.<ente>.<dataset>",   // univoco, stabile
  "name": "Nome leggibile",
  "authority": "Ente titolare del dato",
  "country": "IT",
  "scope": {"level": "regional", "codes": ["Toscana", "09"]},
  "category": "landscape_cultural",        // vedi config/taxonomy.json
  "subcategory": "art142",
  "type": "WFS",                            // protocollo
  "url": "https://.../ows",
  "layer": "workspace:layer",
  "crs": ["EPSG:25832"],
  "geometry": "MultiPolygon",
  "query": {                                // parametri del protocollo
    "version": "2.0.0",
    "bbox_crs": "EPSG:25832",
    "bbox_axis_order": "auto",             // auto | xy | yx
    "bbox_precision": 6,                   // decimali della BBOX (default: 6 geografici, 2 metrici)
    "crs_format": "auto",                  // auto | urn | epsg
    "page_size": 1000,
    "max_features": 20000,
    "min_interval_s": 0.0,                 // rispetto delle policy del servizio
    "timeout_s": 30,                       // timeout HTTP per questa sorgente
    "retries": 3,                          // tentativi per questa sorgente
    "backoff_s": 1.5
  },
  "fields": {"label": "denominazione", "code": "codice", "date": "aggiornamento"},
  "evidence_level": "cartographic",         // cartographic | declaratory | verified_act
  "legal_reference": "D.Lgs. 42/2004 art.142",
  "update_frequency": "annual",
  "last_verified": "2026-09-16",
  "license": "CC-BY 4.0",
  "attribution": "Regione ...",
  "official": true,
  "metadata_url": "https://...",
  "scale": "1:10000",
  "accuracy_m": 5,
  "enabled": true,
  "priority": 30,
  "style": "landscape_constraint",
  "group": "Constraints",
  "tags": ["paesaggio"],
  "notes": "Limiti del dato, differenza fra rappresentazione e accertamento."
}
```

Un file puo' contenere un singolo oggetto, una lista, oppure `{"sources": [...]}`.
I file che iniziano con `_` sono ignorati (utile per i template).

## Protocolli supportati

| `type` | Uso | Note |
|---|---|---|
| `WFS` | download vettoriale | paging via `next`/`startindex`, ordine assi gestito |
| `OGCAPI` | OGC API - Features | paginazione via link `next`, GeoJSON |
| `ARCGIS_FEATURE` | ArcGIS REST `/query` | GeoJSON, paging con `resultOffset` |
| `OVERPASS` | OpenStreetMap | filtri o query QL completa, sotto-layer GDAL (`points`, `lines`, `multipolygons`) |
| `GEOJSON` / `GPKG` / `FILE` | dataset scaricabile intero | scaricato una volta, poi filtrato localmente |
| `WMS` / `WMTS` / `XYZ` / `RASTER` | sfondi e immagini | consumati come servizio, mai copiati |
| `TERRAIN_TILES` | piramidi DEM | usate dal motore del terreno |

## Ambito territoriale (`scope`)

| `level` | Quando si applica |
|---|---|
| `global`, `european`, `national` | sempre |
| `regional` | se `codes` contiene il nome o il codice ISTAT della Regione dell'area |
| `provincial` | nome o sigla della Provincia |
| `municipal` | nome, codice ISTAT o codice catastale del Comune |

Il confronto ignora maiuscole, accenti e punteggiatura (`Forlì` = `forli`).

## Natura del dato (`evidence_level`)

| Valore | Significato nella relazione |
|---|---|
| `cartographic` | dato cartografico conoscitivo |
| `declaratory` | dato dichiarativo dell'ente competente |
| `verified_act` | vincolo riferito ad atto amministrativo |

E' il campo che tiene separata la rappresentazione dalla qualificazione giuridica: comparira'
accanto a ogni risultato, segnalazione e voce della relazione.

## Sorgenti incluse in 0.1.0

| Id | Ente | Tipo | Categoria |
|---|---|---|---|
| `it.agenziaentrate.inspire.cp.parcel` / `.zoning` | Agenzia delle Entrate | WFS | catasto |
| `eu.eea.natura2000.sci` / `.spa` | European Environment Agency | ArcGIS REST | natura |
| `osm.*` (10 sorgenti) | OpenStreetMap | Overpass | viabilita', idrografia, sentieri, edifici, localita', infrastrutture, confini |
| `osm.basemap.standard`, `esri.basemap.world_imagery` | OSMF / Esri | XYZ | sfondi |
| `aws.terrain_tiles.geotiff` | AWS Open Data | Terrain tiles | DEM |
| `local.dem.file` | — | Raster | modello da compilare |

**Google** non e' incluso di proposito: le sue condizioni d'uso non consentono il consumo
delle tile fuori dalle Google Maps API. Ogni sorgente inclusa riporta licenza e attribuzione,
che il plugin mostra in mappa e nella relazione.

## Aggiungere una sorgente regionale

1. Copia `config/sources/regional/_TEMPLATE.json` in
   `<profilo QGIS>/territorial_suite/sources/<nome>.json`.
2. Compila `id`, `url`, `layer`, `scope.codes`, `category`, `fields`, licenza e attribuzione.
3. Metti `enabled: true` e la data odierna in `last_verified`.
4. In QGIS: *Dati → Sorgenti → Ricarica catalogo*, poi *Verifica disponibilita'*.

Da riga di comando:

```bash
"C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat" scripts\validate_sources.py --network
```

## Precisione della BBOX (dettaglio che conta)

Le coordinate del filtro spaziale vengono scritte con **6 decimali** per i CRS geografici
(~11 cm) e **2** per quelli metrici, senza zeri finali. Non e' un dettaglio estetico: il
servizio catastale nazionale rifiuta con *"Richiesta non valida"* una BBOX scritta come
`43.76900000` e accetta la stessa scritta `43.769000`. Se una sorgente ha esigenze diverse,
imposta `query.bbox_precision`.

## Manutenzione

`last_verified` dice quando la sorgente e' stata controllata l'ultima volta; le sorgenti
incluse nel plugin vengono rilasciate solo se verificate, quelle non verificate restano
disattivate.
