# Relazione e pacchetto

## Quadro conoscitivo territoriale

*Report → Relazione* produce il dossier in PDF o HTML. Struttura:

| # | Sezione | Contenuto |
|---|---|---|
| 1 | Area di progetto | denominazione, id, superficie, perimetro, CRS, ellissoide, bbox, origine, data |
| 2 | Localizzazione amministrativa | Comuni, ISTAT, codice catastale, Provincia, Regione, quota di area |
| 3 | Segnalazioni | alert classificati con dettaglio, natura del dato e riferimento dichiarato dalla fonte |
| 4 | Catasto | tabelle per Comune, con l'avvertenza sulle superfici grafiche |
| 5-16 | Categorie del quadro conoscitivo | una sezione per categoria: natura, paesaggio, idraulica, boschi, acque, infrastrutture, rischi, pianificazione, pressioni, valutazioni |
| | Dati altimetrici | quote, pendenza, classi, esposizione, fonte del DEM |
| | Fonti | elenco con ente, stato, natura del dato, licenza, data di acquisizione, attribuzioni |
| | Metadati | versione plugin e QGIS, inizio/fine analisi, fonti interrogate e non disponibili, layer prodotti, avvisi |

Per ogni categoria senza dati la relazione **lo dice esplicitamente**, distinguendo tre casi:

- nessuna sorgente configurata o pertinente;
- sorgenti interrogate senza elementi nell'area;
- sorgenti non disponibili al momento dell'analisi (quadro incompleto).

Chiude sempre l'avvertenza: il documento e' ricognitivo, non costituisce accertamento dei
vincoli ne' certificazione urbanistica, catastale o ambientale.

## Tabelle

Insieme al PDF vengono prodotti:

- `catasto.csv` — una riga per particella, separatore `;`, UTF-8 con BOM (si apre
  correttamente in Excel italiano);
- `fonti.csv` — fonte, ente, categoria, stato, elementi, natura del dato, licenza, URL, data;
- `segnalazioni.csv` — livello, titolo, dettaglio, categoria, natura, riferimento, fonti;
- `tabelle.xlsx` — le tre tabelle in un unico foglio di lavoro (se openpyxl e' utilizzabile,
  vedi [troubleshooting](troubleshooting.md)).

## Pacchetto di consegna

*Report → Esporta pacchetto*: vedi [downloads.md](downloads.md#pacchetto-completo-scarica-tutto).
Include `analysis.json`, che consente di rigenerare relazione e tavole in un secondo momento,
anche da Processing.

## Impostazioni

| Impostazione | Effetto |
|---|---|
| `report.author`, `report.organisation` | intestazione del documento e delle tavole |
| `report.include_cadastre` / `include_terrain` / `include_sources` | sezioni facoltative |
| `report.language` | lingua delle etichette (attualmente `it`) |

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | consegnare un documento leggibile, con numeri e fonti, senza riscriverlo ogni volta |
| Per quale utente | professionisti e uffici che devono allegare un quadro conoscitivo |
| Quale passaggio manuale elimina | copiare numeri dalle tabelle di QGIS in un documento, annotare le fonti, costruire le tabelle catastali |
| Quale fonte utilizza | il risultato dell'analisi (`analysis.json`) |
| Assenza della fonte | ogni sezione dichiara se il dato manca e perche' |
| Errori | il PDF fallito non blocca l'HTML ne' il pacchetto; l'errore viene riportato negli avvisi |
| Test | `tests/unit/test_report_package.py` |
| Documentazione | questo file |
| Riuso da Processing | *Genera relazione territoriale*, *Esporta il pacchetto dell'area* |
| Compatibilita' | `engines.report`, `engines.package`; PDF via Qt, XLSX opzionale |
