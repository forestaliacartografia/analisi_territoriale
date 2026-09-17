# Configurazione

Nessun URL, soglia, scala o classe e' scritto nel codice: tutto vive in
`territorial_suite/config/` e puo' essere sovrascritto nel profilo QGIS.

```
territorial_suite/config/
├── defaults.json          soglie, timeout, scale, classi, opzioni
├── taxonomy.json          categorie del quadro conoscitivo
├── sources/               descrittori delle sorgenti (vedi data_sources.md)
├── rules/                 regole delle segnalazioni
├── styles/styles.json     stili cartografici (+ eventuali .qml)
└── layouts/templates.json template di tavola e serie
```

Override utente (sopravvivono agli aggiornamenti del plugin):

```
<profilo QGIS>/territorial_suite/
├── defaults.json     (merge profondo sui default)
├── taxonomy.json     (aggiunge o sostituisce categorie)
├── sources/*.json
├── rules/*.json
├── styles/styles.json, styles/*.qml
└── layouts/templates.json
```

Il percorso del profilo e' mostrato in *Impostazioni → Cache*.

## Le impostazioni principali

### general

| Chiave | Default | Significato |
|---|---|---|
| `mode` | `quick` | modalita' dell'interfaccia (`quick` / `professional`) |
| `work_crs_mode` | `auto_utm` | scelta del CRS metrico di lavoro (`auto_utm`, `project`, `explicit`) |
| `work_crs` | — | CRS da usare con `explicit` |
| `ellipsoid` | `EPSG:7030` | ellissoide delle misure |
| `debug_logging` | false | messaggi di debug nel log |

### network

| Chiave | Default | Significato |
|---|---|---|
| `timeout_s` | 30 | timeout per richiesta |
| `retries` | 3 | tentativi (anche per le eccezioni OGC restituite con HTTP 200) |
| `backoff_s` | 1.5 | attesa crescente fra i tentativi |
| `min_interval_s` | 0 | intervallo minimo fra richieste allo stesso host |
| `max_features_per_source` | 20000 | tetto di elementi per sorgente |
| `offline` | false | usa solo la cache |
| `auth_config_id` | — | configurazione di autenticazione QGIS (nessuna password nel plugin) |

### cache

| Chiave | Default | Significato |
|---|---|---|
| `enabled` | true | attiva la cache su disco |
| `quota_mb` | 2048 | dimensione massima, con eviction LRU |
| `bbox_grid_m` | 100 | quantizzazione della bbox nella chiave di cache |
| `ttl_hours.capabilities / vector / raster / metadata` | 168 / 24 / 720 / 168 | validita' per tipo |
| `ttl_minutes.health` | 10 | validita' dello stato dei servizi |

### analysis

`context_buffer_m` (1000), `max_distance_m` (1000, limitato al buffer di contesto),
`max_hits_per_source` (500), `keep_intersection_geometry` (true), `setback_bands_m`
(`[10, 30, 150]`), `admin_strategies` (`["cadastre", "osm"]`), `categories` (le 12 categorie
+ idrografia).

> Le due soglie vanno lette insieme: il plugin scarica un anello di `context_buffer_m`
> attorno all'area e misura le distanze al massimo entro quel raggio. Per verificare
> prossimita' piu' ampie, alza **entrambi** i valori (il download cresce di conseguenza).

### terrain, cartography, report

Vedi [terrain.md](terrain.md), [cartography.md](cartography.md), [reports.md](reports.md).

## Dove finiscono i file

| Cosa | Dove |
|---|---|
| output dell'area (gpkg, tif, report) | `<profilo>/territorial_suite/areas/<id area>/` |
| cache | `<profilo>/cache/territorial_suite/` |
| file temporanei | `<profilo>/territorial_suite/tmp/` |

Le variabili d'ambiente `TERRITORIAL_SUITE_HOME` e `TERRITORIAL_SUITE_CACHE` spostano
rispettivamente la cartella di lavoro e la cache (usate dai test).

## Modificare le regole

Copia `config/rules/default_rules.json` nel profilo, cambia soglie e testi, oppure
disattiva una regola con `"enabled": false`. Le regole con lo stesso `id` sovrascrivono
quelle incluse. Sintassi e operazioni: [constraints.md](constraints.md#segnalazioni-alert).
