# Vincoli e sensibilita' territoriali

## Cosa calcola

Per ogni sorgente pertinente all'area, il *Constraint & Sensitivity Engine* determina:

| Informazione | Come |
|---|---|
| presenza / assenza | almeno un elemento interseca l'area |
| intersezione | geometria dell'intersezione (conservata in WKT, disattivabile) |
| superficie interessata | **unione** delle intersezioni, misurata sull'ellissoide |
| percentuale | superficie interessata / superficie dell'area |
| distanza minima | per gli elementi che non intersecano, entro il raggio configurato |
| elementi | etichetta, attributi principali, superficie e percentuale per elemento |
| fonte | nome, ente, URL, layer, licenza, attribuzione, scala, accuratezza, aggiornamento |
| natura del dato | cartografico / dichiarativo / riferito ad atto amministrativo |
| riferimento normativo | quello **dichiarato dalla fonte**, mai un accertamento |

L'unione delle intersezioni evita il difetto classico di questi calcoli: con dataset che si
sovrappongono (frequentissimo nei vincoli), sommare le singole aree porta a percentuali
superiori al 100 %.

## Tassonomia

Le categorie sono quelle del quadro conoscitivo territoriale, definite in
`config/taxonomy.json` ed estendibili:

1. Vincoli giuridico-amministrativi
2. Aree naturalistiche e conservazione
3. Vincoli paesaggistici e culturali
4. Vincoli idraulici e geomorfologici
5. Boschi, vegetazione e patrimonio naturale
6. Acque
7. Infrastrutture e fasce di rispetto
8. Rischi
9. Usi e pianificazione del territorio
10. Attivita' antropiche e pressioni
11. Dati catastali e proprieta'
12. Dati utili alle autorizzazioni e valutazioni ambientali

Piu' le categorie operative (unita' amministrative, terreno, immagini, viabilita',
idrografia, sentieri, edifici, localita').

## Segnalazioni (alert)

Il *Rule Engine* valuta regole dichiarative (`config/rules/*.json`) e produce segnalazioni
classificate:

| Livello | Significato |
|---|---|
| **INFORMAZIONE** | elemento presente, nessuna implicazione particolare |
| **ATTENZIONE** | elemento che merita una valutazione |
| **VERIFICA** | occorre verificare presso l'ente competente |
| **CRITICITA' CARTOGRAFICA** | il quadro e' incompleto (fonte non disponibile, dati assenti) |

Una regola e' un oggetto JSON:

```json
{
  "id": "water.watercourse_150m",
  "title": "Corso d'acqua entro {threshold} m dall'area",
  "detail": "Elemento piu' vicino a {distance} (fonte: {source}). ...",
  "level": "CHECK_REQUIRED",
  "when": {"category": "hydrography", "operation": "within_or_near",
           "threshold": 150, "unit": "m"},
  "legal_reference": "D.Lgs. 42/2004 art. 142 c.1 lett. c)"
}
```

Operazioni disponibili: `intersects`, `not_present`, `area_pct_gt`, `area_m2_gt`,
`distance_lt`, `within_or_near`, `count_gt`, `unavailable`, `attribute_in`; sui soggetti
`area` (`multi_municipality`, `admin_unresolved`, `area_ha_gt`), `cadastre`
(`multi_municipality`, `multi_sheet`, `parcels_gt`, `no_parcels`) e `terrain`
(`slope_mean_gt`, `slope_max_gt`, `slope_class_pct_gt`, `elevation_range_gt`).

Segnaposto utilizzabili nei testi: `{source} {count} {pct} {area} {distance} {threshold}
{names} {value} {class} {error} {note}`.

## Il limite che il plugin non supera

> Il dato cartografico dice **dove guardare**, non **cosa e' dovuto**.

Nessuna regola produce una qualificazione giuridica: `level` e' una classificazione
semantica, `legal_reference` e' il riferimento dichiarato dalla fonte, e ogni segnalazione
riporta la natura del dato su cui si basa. Le sorgenti non disponibili generano sempre una
segnalazione di *criticita' cartografica*, perche' l'assenza di dati non e' assenza di
vincoli.

## Parametri

| Impostazione | Default | Effetto |
|---|---|---|
| `analysis.context_buffer_m` | 1000 | quanto territorio viene effettivamente scaricato attorno all'area |
| `analysis.max_distance_m` | 1000 | raggio entro cui misurare le distanze; **viene limitato al buffer di contesto**, perche' oltre non ci sono dati |
| `analysis.max_hits_per_source` | 500 | elementi dettagliati conservati per fonte |
| `analysis.keep_intersection_geometry` | true | conserva la geometria dell'intersezione |
| `analysis.categories` | 12 categorie + idrografia | cosa interrogare nell'analisi |

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | sapere cosa interessa l'area, quanto e da quale fonte, senza aggiungere decine di servizi a mano |
| Per quale utente | tecnico, PA, consulente ambientale |
| Quale passaggio manuale elimina | aggiungere WMS/WFS, filtrare per bbox, ritagliare, intersecare, calcolare aree e distanze, annotare le fonti |
| Quale fonte utilizza | tutte quelle del catalogo pertinenti all'area (`core.registry`) |
| Assenza della fonte | risultato con stato OFFLINE + segnalazione di criticita' cartografica; l'analisi prosegue |
| Errori | per sorgente, mai globali; testo dell'errore conservato nel risultato e nella relazione |
| Test | `tests/unit/test_spatial_rules.py`, `tests/integration/test_wfs_client.py` |
| Documentazione | questo file |
| Riuso da Processing | *Analizza vincoli e sensibilita'* |
| Compatibilita' | `engines.constraints` + `engines.rules`, nessuna dipendenza da GUI |
