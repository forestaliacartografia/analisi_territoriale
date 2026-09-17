# Cartografia

## Mappa rapida

*Cartografia → Crea mappa*: scegli il tipo di tavola e il plugin costruisce un layout di
stampa gia' completo:

- cornice mappa con **scala scelta automaticamente** fra quelle configurate;
- griglia di coordinate con annotazioni nel CRS di lavoro;
- legenda filtrata sui layer effettivamente in mappa, con nomi accorciati e numero massimo
  configurabile;
- freccia del nord, barra di scala con segmenti tondi, scala numerica;
- pannello con Comune/Provincia/Regione, superficie, perimetro, CRS, scala, data, autore;
- elenco delle fonti dei dati e formula sulla natura conoscitiva dell'elaborazione;
- numero di tavola e piè di pagina.

In modalita' **Pro** la finestra permette di impostare titolo, sottotitolo, formato
(A4→A0), orientamento, scala fissa, numero tavola, autore e quali elementi mostrare.

## Serie di tavole

*Cartografia → Serie di tavole* genera un layout indipendente per ogni tipo configurato.
Serie incluse:

| Serie | Tavole |
|---|---|
| `default` | inquadramento, catasto, vincoli, ambiente, idrografia, infrastrutture, rischio, ortofoto, altimetria, pendenza |
| `minimal` | inquadramento, catasto, vincoli |
| `forestry` | inquadramento, forestale, idrografia, altimetria, pendenza |

Le tavole i cui layer non esistono vengono saltate (e dichiarate): una "carta del rischio"
vuota non serve a nessuno.

## Template

12 template in `config/layouts/templates.json`:

```
01 Inquadramento territoriale   07 Carta delle infrastrutture
02 Carta catastale              08 Carta del rischio
03 Carta dei vincoli            09 Ortofoto
04 Carta ambientale             10 Carta altimetrica
05 Carta forestale              11 Carta della pendenza
06 Carta idrografica            12 Carta tecnica di progetto
```

Ogni template dichiara la pagina, la geometria di mappa e pannello, i blocchi del pannello
(titolo, sottotitolo, legenda, barra di scala, nord, informazioni, fonti, avvertenza, piè di
pagina) e **quali categorie di layer mostrare**. Aggiungere un template significa aggiungere
un oggetto JSON: nessuna modifica al codice.

## Motore di scala

Sceglie la prima scala *tonda* fra quelle configurate in cui l'area entra nella cornice con
un margine dell'8 %:

```
1:500  1:1.000  1:2.000  1:5.000  1:10.000  1:25.000  1:50.000  1:100.000
```

Se l'area e' piu' grande di tutte, arrotonda per eccesso a un valore pulito. L'intervallo
della griglia segue la scala (circa 5 cm sulla carta).

## Stili

`config/styles/styles.json` centralizza colori, spessori, tratteggi e campi di etichetta per
particelle, fogli, Natura 2000, vincoli, boschi, idrografia, viabilita', sentieri,
elettrodotti, ferrovie, curve di livello, DEM, pendenza, esposizione, ombreggiatura.

Un file `.qml` con lo stesso nome dello stile (nel plugin o nel profilo utente) ha la
precedenza: esporta lo stile da QGIS, salvalo come `<nome>.qml` e il plugin lo usera'.

> Nota sui colori: i valori sono in formato `#RRGGBBAA`. Qt interpreta le stringhe a nove
> caratteri come `#AARRGGBB`, quindi il plugin fa il parsing dell'alfa per conto proprio.

## Esportazione

*Esporta tavole* oppure il pulsante nella finestra della serie. Formati dei layout: PDF,
PNG, JPEG, SVG (il GeoPDF e' previsto per la 2.0). L'export **DXF** lavora sui layer, non
sul layout, ed e' disponibile dall'API
(`engines.cartography.export.export_layers_to_dxf`): sara' collegato al pannello nella
prossima versione. I nomi dei file sono generati automaticamente:

```
01_Inquadramento_territoriale.pdf
02_Estratto_di_mappa_catastale.pdf
03_Carta_dei_vincoli_e_delle_sensibilita.pdf
```

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | produrre cartografia professionale coerente in pochi secondi |
| Per quale utente | tecnici e cartografi che devono consegnare tavole |
| Quale passaggio manuale elimina | creare il layout, dimensionare la mappa, scegliere la scala, inserire legenda/nord/scala/griglia, scrivere fonti e data, esportare con nomi ordinati |
| Quale fonte utilizza | i layer prodotti dall'analisi, piu' gli sfondi configurati |
| Assenza della fonte | la tavola viene saltata se non ha layer; la legenda mostra solo cio' che c'e' |
| Errori | isolati per tavola: un template che fallisce non blocca la serie |
| Test | `tests/qgis/test_cartography.py` (layout, scala, export, serie) |
| Documentazione | questo file |
| Riuso da Processing | *Genera mappa rapida*, *Genera serie di tavole* |
| Compatibilita' | `engines.cartography.*`, usa solo le API layout di QGIS |
