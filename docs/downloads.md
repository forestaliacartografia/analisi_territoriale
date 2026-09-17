# Download dei dati d'area

## Scarica dati d'area

*Dati → Scarica vettoriali* (oppure Processing → *Scarica i dati dell'area*).

1. il plugin elenca le **categorie che hanno sorgenti disponibili per quell'area**
   (con il numero di sorgenti e i nomi nel tooltip);
2. scarica solo cio' che ricade nella bbox dell'area piu' un buffer;
3. ritaglia sull'area (opzionale), riproietta nel CRS di lavoro;
4. scrive tutto in un unico GeoPackage con un layer per sorgente, nominato
   `categoria_idsorgente`;
5. carica i layer nel progetto, raggruppati e stilizzati, con la provenienza registrata.

Categorie tipiche: viabilita', idrografia, sentieristica, edifici, localita' e toponimi,
infrastrutture (elettrodotti, ferrovie), confini amministrativi.

## Sfondi e ortofoto

*Dati → Sfondo / ortofoto* elenca i servizi configurati con **attribuzione e condizioni
d'uso in chiaro**. Gli sfondi sono servizi a tile: il plugin li consuma, non li copia. Nel
pacchetto di consegna trovi `imagery/SERVIZI.txt` con i riferimenti, non le immagini.

## Pacchetto completo ("scarica tutto")

*Report → Esporta pacchetto* costruisce la cartella di consegna:

```
<Area>_<id>/
├── vector/         dati scaricati (GeoPackage)
├── cadastral/      particelle e fogli
├── constraints/    dataset dei vincoli
├── terrain/        dem.tif, slope.tif, aspect.tif, hillshade.tif, contours.gpkg
├── imagery/        SERVIZI.txt (riferimenti ai servizi di sfondo)
├── reports/        quadro_conoscitivo.pdf/html, analysis.json, tabelle CSV/XLSX
├── maps/           tavole esportate in PDF
├── project/        project.qgz che punta ai file del pacchetto
└── MANIFEST.json   contenuto, fonti, avvisi, data
```

`analysis.json` contiene l'intero risultato dell'analisi: puo' essere riletto dagli
algoritmi *Genera relazione territoriale*, *Genera mappa rapida*, *Genera serie di tavole* e
*Esporta il pacchetto dell'area*, anche su un'altra macchina.

## Limiti e prestazioni

| Impostazione | Default | Effetto |
|---|---|---|
| `network.max_features_per_source` | 20000 | tetto di elementi per sorgente; oltre, il risultato e' segnalato come troncato |
| `download.buffer_m` | 250 | quanto allargare l'area prima di scaricare |
| `download.clip_to_area` | true | ritaglio sul perimetro |
| `network.min_interval_s` | 0 | intervallo minimo fra richieste allo stesso host |
| `cache.ttl_hours.vector` | 24 | validita' dei dati vettoriali in cache |

Le sorgenti OpenStreetMap hanno gia' un `min_interval_s` di 2 s nel descrittore, come
richiesto dalle policy d'uso di Overpass. Se un servizio risponde lentamente conviene ridurre
l'area o disattivare temporaneamente quella sorgente nel Source Manager.

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | avere tutti i dati vettoriali di un'area gia' ritagliati, nominati, raggruppati e stilizzati |
| Per quale utente | chiunque prepari un progetto QGIS su un'area nuova |
| Quale passaggio manuale elimina | cercare i servizi, aggiungerli, filtrare, scaricare, ritagliare, rinominare, raggruppare, stilizzare, salvare |
| Quale fonte utilizza | tutte le sorgenti vettoriali del catalogo pertinenti all'area |
| Assenza della fonte | warning per sorgente; le altre categorie vengono comunque scaricate |
| Errori | isolati per sorgente, elencati alla fine |
| Test | `tests/integration/test_wfs_client.py`, scenari `tests/scenarios` |
| Documentazione | questo file |
| Riuso da Processing | *Scarica i dati dell'area*, *Esporta il pacchetto dell'area* |
| Compatibilita' | `engines.download`, `engines.package` |
