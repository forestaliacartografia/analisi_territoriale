# Primi passi

> Documentazione utente in italiano; il codice, le API e il README sono in inglese.

## Installazione

1. Genera il pacchetto: `python scripts/package.py` (produce `dist/territorial_suite-<versione>.zip`).
2. In QGIS: *Plugins → Gestisci e installa plugin → Installa da ZIP*.
3. Abilita **Analisi territoriale**: comparira' il pannello a destra e la voce di menu
   *Plugins → Analisi territoriale*.

Requisiti: QGIS 3.40 LTR o successivo (testato anche su QGIS 4.0). Nessuna dipendenza
esterna obbligatoria.

## I quattro passi del primo utilizzo

### 1. Crea l'area di progetto

Dal pannello: **Crea ▾**

| Modalita' | Quando usarla |
|---|---|
| Disegna poligono / Mano libera | perimetro tracciato a mano sulla mappa |
| Rettangolo / Cerchio | inquadramenti rapidi, raggi di indagine |
| Dal layer attivo / Dalla selezione | il perimetro esiste gia' in un layer |
| Da file | shapefile, GeoPackage, GeoJSON, KML/KMZ, `.tsa.json` |
| Da coordinate / BBOX | perimetro noto per coordinate, oppure centro + raggio |
| Dalle particelle selezionate | seleziona le particelle nel layer catastale e costruisci l'area |

L'area viene salvata **nel progetto QGIS** (si ritrova riaprendo il progetto) e puo' essere
esportata in un file `.tsa.json` portabile con **Salva**.

### 2. Controlla le sorgenti

**Dati → Sorgenti**: elenco del catalogo, ambito territoriale, natura del dato, licenza e
stato del servizio (*Verifica disponibilita'*). Qui si disattivano le sorgenti non
pertinenti e si vedono gli eventuali descrittori non validi.

Per aggiungere le fonti della tua regione: [data_sources.md](data_sources.md).

### 3. Esegui l'analisi

**Analisi territoriale (one-click)** esegue in background:

```
unita' amministrative → catasto → vincoli e sensibilita' → terreno → regole e segnalazioni
```

In modalita' **Pro** (interruttore in alto a destra) la stessa azione apre prima la finestra
delle opzioni: fasi, categorie, buffer di contesto, raggio delle distanze, risoluzione DEM.

Al termine si apre il pannello **Risultati** con sei schede: Riepilogo, Segnalazioni,
Vincoli e dati, Catasto, Terreno, Fonti.

> **Quanto dura.** Le sorgenti istituzionali rispondono in frazioni di secondo (il catasto
> ~0,5 s); le sorgenti OpenStreetMap passano dall'API Overpass, che mette in coda le
> richieste e puo' impiegare decine di secondi ciascuna. La prima analisi di un'area puo'
> quindi richiedere qualche minuto; le successive sulla stessa area sono immediate grazie
> alla cache. Se non ti servono, disattiva le sorgenti OSM in *Dati → Sorgenti*.

### 4. Produci la cartografia

| Comando | Risultato |
|---|---|
| **Crea mappa** | un layout completo (mappa, griglia, legenda, nord, scala, inquadramento, fonti, data) |
| **Serie di tavole** | un layout per ogni tipo di carta configurato |
| **Esporta tavole** | PDF numerati in una cartella |
| **Relazione** | quadro conoscitivo in PDF/HTML + tabelle |
| **Esporta pacchetto** | cartella di consegna con dati, mappe, progetto QGIS e manifest delle fonti |

## Command palette

`Ctrl+Shift+T` apre la ricerca comandi: digita *analizza*, *catasto*, *pendenza*, *mappa*,
*pacchetto*... e premi Invio.

## Cosa aspettarsi (e cosa no)

Il plugin costruisce un **quadro conoscitivo**: dice dove guardare, con quali numeri e da
quale fonte. Non sostituisce la verifica dei vincoli presso gli enti competenti: ogni dato
riporta la sua natura (cartografica, dichiarativa o riferita ad atto amministrativo) e ogni
fonte non disponibile viene dichiarata, perche' *"nessun dato"* e *"nessun vincolo"* sono
risposte diverse.
