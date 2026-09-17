# Catasto

## Il servizio

Analisi territoriale interroga il servizio WFS INSPIRE dell'**Agenzia delle Entrate**
(`CP:CadastralParcel` e `CP:CadastralZoning`). Comportamenti verificati sul campo e gestiti
dal plugin:

| Comportamento del servizio | Come lo gestisce il plugin |
|---|---|
| BBOX con **latitudine per prima** (EPSG:6706, forma `urn`) | `bbox_axis_order: "yx"` nel descrittore |
| **rifiuta le coordinate con 8 decimali terminanti in zeri** (`43.76900000` → *"Richiesta non valida"*, mentre `43.769000` funziona) | le coordinate della BBOX sono scritte con 6 decimali per i CRS geografici e 2 per quelli metrici, senza zeri finali (`bbox_precision` nel descrittore) |
| solo GML 3.2 in uscita | nessun `outputformat` forzato |
| paging con `startindex`/`count` e link `next` | il client segue il link `next` finche' arrivano elementi |
| errori *"Richiesta non valida"* con HTTP 200 | rilevati come eccezione OGC, **ritentati** con backoff esponenziale e, come ultima risorsa, ripetuti in forma semplificata (senza `srsname`, con meta' `count`) |
| `numberMatched="unknown"` | non usato come condizione di fine paginazione |
| nessuna superficie censita negli attributi | la superficie e' calcolata dalla geometria ed etichettata come **grafica** |
| Province autonome di Trento e Bolzano non coperte | risultato vuoto spiegato con un avviso esplicito |

## Cosa produce

Tabella per Comune, ordinata per foglio e particella:

```
COMUNE | FOGLIO | PARTICELLA | SUPERFICIE GRAFICA | SUPERFICIE INTERESSATA | % PARTICELLA
```

più:

- layer **Particelle interessate** e **Fogli catastali** in `cadastre.gpkg` (CRS di lavoro),
  stilizzati ed etichettati;
- elenco dei Comuni interessati con la quota di area;
- export CSV/XLSX e copia della tabella negli appunti;
- comando **Crea area dalle particelle selezionate**.

## Aree su piu' Comuni e piu' fogli

L'identificativo nazionale (`NATIONALCADASTRALREFERENCE`, es. `D612_016900.10`) viene
scomposto in codice comune, foglio, eventuale allegato e sviluppo, particella. Il codice
catastale (`D612`) e' tradotto in Comune, ISTAT, Provincia e Regione tramite la tabella
ISTAT inclusa nel plugin, **senza chiamate di rete aggiuntive**.

Quando l'area attraversa piu' Comuni la tabella e' raggruppata per Comune e la relazione
riporta un blocco per ciascuno; la stessa suddivisione compare nel CSV/XLSX.

## Superfici: grafiche, non censite

> La cartografia catastale non contiene la superficie censita in banca dati.

Il plugin calcola la superficie dalla geometria della mappa (`superficie grafica`) e lo
dichiara in ogni tabella, nella relazione e nell'help dell'algoritmo di Processing. Per la
superficie censita occorre la visura.

## Parametri

| Impostazione | Default | Effetto |
|---|---|---|
| `cadastre.source_id` | `it.agenziaentrate.inspire.cp` | prefisso delle sorgenti catastali da usare |
| `cadastre.sheet_from_zoning` | true | ricava l'etichetta del foglio dal layer dei fogli |
| `cadastre.max_parcels` | 5000 | limite di particelle elaborate |

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | ottenere l'elenco delle particelle interessate e la superficie coinvolta senza uscire da QGIS |
| Per quale utente | geometri, tecnici, PA, professionisti delle pratiche |
| Quale passaggio manuale elimina | configurare il WFS catastale, capire l'ordine degli assi, paginare, intersecare, riconoscere il Comune dal codice, compilare la tabella |
| Quale fonte utilizza | WFS INSPIRE Agenzia delle Entrate + tabella ISTAT inclusa |
| Assenza della fonte | avviso esplicito (servizio non disponibile oppure territorio di Trento/Bolzano) e analisi che prosegue |
| Errori | ritentati (errori intermittenti del servizio), poi registrati come fonte non disponibile |
| Test | `tests/integration/test_cadastre_engine.py` (fixture reali del servizio) |
| Documentazione | questo file |
| Riuso da Processing | *Interroga il catasto* |
| Compatibilita' | `engines.cadastre_engine`, indipendente dalla GUI |
