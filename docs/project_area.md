# Area di progetto

L'area di progetto e' il centro logico del plugin: ogni analisi, download, mappa e relazione
si riferisce a essa.

## Cosa contiene

| Dato | Note |
|---|---|
| geometria | poligono o multipoligono, riparato automaticamente se non valido |
| CRS | quello di origine (mappa, layer, file) |
| CRS di lavoro | metrico, scelto automaticamente (fuso UTM del centroide) o imposto |
| superficie | **ellissoidica**, in m² / ha / km² |
| perimetro | ellissoidico, in m / km |
| bounding box, centroide | anche in WGS84 |
| unita' amministrative | Comune/i, Provincia/e, Regione/i con la quota di area interessata |
| identificativo e data | id breve stabile, data di creazione |
| origine | disegno, rettangolo, cerchio, layer, selezione, file, coordinate, bbox, particelle |
| particelle | riferimenti catastali quando l'area nasce da particelle selezionate |

## Metriche geometriche

*Processing → Territorial Suite → Statistiche geometriche dell'area* calcola anche:

- **compattezza** (Polsby-Popper, `4πA/P²`): 1 = cerchio, ~0,79 = quadrato;
- **elongazione**: lato corto / lato lungo del rettangolo orientato minimo;
- **convessita'**: area / area dell'inviluppo convesso;
- raggio del cerchio equivalente, numero di parti e di vertici.

## Persistenza

- **Nel progetto QGIS**: l'area (e le precedenti) restano nel `.qgz`; **Carica** permette di
  tornare a una qualsiasi.
- **Su file**: **Salva** produce un `.tsa.json` portabile (geometria in WKT + metadati).

## CRS di lavoro

Tutte le operazioni metriche (buffer, ritagli, pendenza, distanze) avvengono in un CRS
proiettato metrico. La scelta segue `general.work_crs_mode`:

| Valore | Comportamento |
|---|---|
| `auto_utm` (default) | fuso UTM WGS84 del centroide dell'area |
| `project` | CRS del progetto, se metrico |
| `explicit` | il CRS indicato in `general.work_crs` |

Le superfici e i perimetri restano comunque **ellissoidici**: l'ellissoide usato e' scritto
nella relazione, cosi' il numero e' riproducibile.

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | dare un riferimento unico, misurato e tracciabile a tutte le elaborazioni |
| Per quale utente | chiunque debba analizzare un'area: tecnico, PA, consulente, forestale |
| Quale passaggio manuale elimina | creare a mano un layer di perimetro, calcolarne area e perimetro, riproiettarlo, ricordarsi il CRS |
| Quale fonte utilizza | nessuna: e' un input dell'utente (eventualmente da catasto) |
| Assenza della fonte | non applicabile |
| Errori | geometrie non valide riparate, geometrie non poligonali rifiutate con messaggio |
| Test | `tests/unit/test_project_area.py`, `tests/unit/test_geo_core.py` |
| Documentazione | questo file |
| Riuso da Processing | si': parametro *Area di progetto* in tutti gli algoritmi |
| Compatibilita' | `core.project_area`, nessuna dipendenza da GUI |
