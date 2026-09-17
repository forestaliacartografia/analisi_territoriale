# Risoluzione dei problemi

Il primo posto dove guardare e' il **log di QGIS**, canale *Territorial Suite*
(*Vista → Pannelli → Log dei messaggi*). Per messaggi piu' dettagliati:
*Impostazioni → Analisi* → attiva `debug_logging` (oppure `general.debug_logging` nei default).

## Una fonte risulta non disponibile

Il plugin non si ferma: registra la fonte come OFFLINE, genera una segnalazione di
*criticita' cartografica* e prosegue. Per capire il perche':

1. *Dati → Sorgenti → Verifica disponibilita'*: la colonna Stato mostra l'esito e l'errore.
2. Molti servizi pubblici falliscono a intermittenza: riprovare piu' tardi spesso basta.
3. Se il servizio richiede autenticazione, crea una configurazione nel sistema di
   autenticazione di QGIS e indicane l'id in *Impostazioni → Rete*.
4. Dietro proxy aziendale: il plugin usa le impostazioni di rete di QGIS, verificale in
   *Opzioni → Rete*.

## Il catasto non restituisce particelle

Cause tipiche, in ordine di frequenza:

- l'area ricade nelle **Province autonome di Trento o Bolzano**, dove il catasto e' gestito
  dai catasti provinciali e non dal servizio nazionale (il plugin lo dichiara);
- il servizio ha risposto *"Richiesta non valida"*: e' un errore intermittente noto, il
  plugin ritenta automaticamente, ma sotto carico puo' fallire comunque;
- l'area e' enorme: riduci l'estensione o alza `network.max_features_per_source`.

## Le superfici catastali non coincidono con la visura

E' atteso: il servizio cartografico **non contiene la superficie censita**. Il plugin
calcola la superficie dalla geometria della mappa e la etichetta come *grafica*. Per la
superficie censita serve la visura catastale.

## Overpass / OpenStreetMap va in timeout

Le istanze pubbliche di Overpass sono soggette a code e limiti. Rimedi:

- riduci l'area o il buffer di contesto;
- aumenta `network.min_interval_s` (le sorgenti OSM hanno gia' 2 s);
- disattiva temporaneamente le sorgenti OSM nel Source Manager;
- in `config/sources/osm/...` puoi indicare l'URL di un'altra istanza Overpass.

## L'export XLSX produce dei CSV

Succede quando `openpyxl` risulta gia' caricato con il backend **lxml**. lxml porta con se'
la propria libxml2, che entra in conflitto con quella usata da GDAL per leggere il GML: il
risultato e' un crash del processo QGIS. Il plugin preferisce degradare: forza
`OPENPYXL_LXML=False` al caricamento e, se e' troppo tardi, esporta in CSV e lo scrive nel
log. Se ti serve l'XLSX, evita di caricare prima altri plugin che importano openpyxl con
lxml.

## QGIS si chiude durante un'analisi

Segnala il caso con il log: sono crash di libreria, non eccezioni Python. Due cause note,
gia' risolte nel plugin, utili come riferimento se estendi il codice:

- **GDAL**: usare una *band* dopo aver rilasciato il *dataset* (o non chiudere i dataset di
  `Warp`/`BuildVRT`/`DEMProcessing`) corrompe la memoria e fa crashare il processo in un
  punto qualsiasi, anche molto dopo;
- **lxml + GDAL**: vedi il punto precedente sull'XLSX.

## "Impossibile scrivere ... update mode failed"

Il GeoPackage era aperto da un layer ancora vivo. Il plugin evita di riaprire i file appena
scritti; se succede con file tuoi, chiudi i layer che puntano a quel GeoPackage e riprova.

## La mappa esce vuota o senza sfondo

- Lo sfondo e' un servizio a tile: serve connessione al momento del rendering.
- Se hai scelto un template che filtra per categoria (es. *Carta del rischio*) e nessun
  layer di quella categoria e' presente, la tavola viene saltata: prima esegui l'analisi.
- Verifica che i layer dell'analisi siano stati caricati nel progetto (pulsante *Risultati*
  → l'analisi one-click li carica automaticamente).

## Le etichette o i numeri sembrano sbagliati

- Le superfici e i perimetri sono **ellissoidici**: possono differire di qualche decimo di
  percento dai valori planari calcolati in un CRS proiettato. L'ellissoide usato e' scritto
  nella relazione.
- Le percentuali di intersezione sono calcolate sull'unione delle intersezioni: con dataset
  sovrapposti non superano mai il 100 %.

## Reimpostare tutto

*Impostazioni → Ripristina predefiniti* azzera le preferenze. Per ripartire dai dati:
*Impostazioni → Cache → Svuota la cache*. Gli output delle aree restano in
`<profilo>/territorial_suite/areas/` e vanno eliminati a mano se non servono piu'.
