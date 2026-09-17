# Layout e relazioni: configurare le tavole senza toccare il codice

Due concetti ortogonali:

* un **template** decide *quali dati* mostra una tavola (`config/layouts/templates.json`);
* un **profilo** decide *che aspetto ha* (`config/layouts/profiles.json`).

La stessa *Carta dei vincoli* si stampa con il profilo essenziale o con quello
istituzionale senza toccare ne' l'uno ne' l'altro.

---

## 1. Profili forniti

| Profilo | Uso | Caratteristiche |
|---|---|---|
| `standard` | tavola tecnica essenziale | A3, legenda automatica, scala, nord, fonti |
| `istituzionale` | atti e allegati di enti | A3, legenda tematica raggruppata, note legali estese |
| `tecnico` | elaborati di dettaglio | A2, massima superficie di mappa, legenda su 2 colonne |
| `professionale` | elaborati di progetto | loghi, committente, codice elaborato, revisione |
| `semplificato` | stampe di lavoro | A4, solo mappa, titolo e scala |
| `ortofoto` | tavole su base ortofotografica | attribuzione in evidenza e avvertenze |

I profili forniti non vengono mai modificati sul posto: salvando le modifiche si crea una
copia personale con lo stesso id in
`<profilo QGIS>/territorial_suite/layouts/profiles.json`, che ha la precedenza e che si
puo' eliminare per tornare all'originale.

---

## 2. Cosa si configura

### Identita' grafica

* **loghi** multipli, stampati in riga nel blocco `logos`;
* **immagini aggiuntive** (foto dell'area, del bene, estratti, schemi) nel blocco `images`,
  ciascuna con didascalia e fonte;
* formati: `.png`, `.jpg`, `.jpeg`, `.svg`.

Un'immagine configurata che **non si puo' inserire** (file mancante, formato non
supportato) non viene ignorata in silenzio: il motivo finisce nel blocco `warnings` e
quindi **stampato sulla tavola**. Una tavola senza lo stemma del committente e' una tavola
che torna indietro.

### Elementi cartografici

Legenda (`auto` / `thematic` / `custom` / `none`, con titolo, colonne, voci massime,
rinomine, inclusioni, esclusioni e ordinamento), barra di scala, scala numerica, reticolo,
coordinate ai margini, rosa dei venti (modello, dimensione, rotazione, «segui la mappa»),
attribuzione delle immagini.

### Testi

`author`, `client`, `project`, `locality`, `municipality`, `province`, `region`, `date`,
`sheet_code`, `sheet_number`, `revision`, `scale`, `crs`, `notes`, `method_notes`,
`legal_notes`, `disclaimer`, `header`, `footer`.

Si possono usare come segnaposto in qualunque testo del profilo:

```json
{"type": "text", "h_mm": 10, "value": "{client} - {project} - tav. {sheet_number} rev. {revision}"}
```

I valori territoriali (`municipality`, `province`, `region`) sono compilati dall'analisi.
Un segnaposto inesistente non fa perdere il testo: viene stampato come scritto.

---

## 3. Blocchi disponibili

Un profilo puo' ridefinire l'elenco e l'ordine dei blocchi del riquadro informativo:

```text
title  subtitle  logos  legend  scalebar  northarrow  info  images
sources  attribution  warnings  disclaimer  footer  text  logo
```

`flexible: true` su un blocco gli assegna lo spazio residuo (tipicamente la legenda).

---

## 4. Ordine di precedenza

```text
richiesta esplicita dell'utente  >  profilo  >  template  >  impostazioni globali
```

Scegliere "A4" per una singola tavola non cambia il profilo per tutte le altre.

---

## 5. Ortofoto

La tavola ortofoto sceglie il fornitore in questo ordine:

1. quello richiesto (`auto`, `esri`, `google`, o l'id di una sorgente del catalogo);
2. in mancanza, il migliore disponibile per punteggio di qualita'.

**Google** e' usato esclusivamente attraverso la **Map Tiles API** ufficiale, con la chiave
dell'utente:

1. `POST .../createSession` restituisce un token di sessione;
2. `GET .../viewport` restituisce la stringa di attribuzione **obbligatoria**;
3. `GET .../2dtiles/{z}/{x}/{y}?session=...&key=...` fornisce le tile.

Il plugin **non** preleva tile dagli indirizzi interni del sito Google Maps: sarebbe
scraping, si romperebbe al primo cambiamento e non e' quanto consentito dai termini d'uso.
Senza chiave la sorgente e' disattivata e la tavola usa un'altra ortofoto **dichiarandolo
sulla tavola stessa**, non solo nel log. Se l'attribuzione non e' ottenibile, le tile non
vengono usate affatto.

La chiave e' conservata nel **database di autenticazione cifrato di QGIS**; nelle
impostazioni resta solo l'identificativo opaco della configurazione. In alternativa si puo'
usare la variabile d'ambiente `TERRITORIAL_SUITE_GOOGLE_MAPS_KEY`.

### Perche' una tile viene scaricata prima di stampare

Un `QgsRasterLayer` XYZ e' **valido** anche quando l'host non esiste: QGIS se ne accorge
solo quando prova a disegnare. Senza verifica si otterrebbe una tavola bianca con
un'attribuzione perfetta sotto — un errore che sembra un risultato. Il motore scarica
quindi una tile sull'area prima di dichiarare utilizzabile il fornitore
(`cartography.probe_imagery`, disattivabile).

La provenienza dell'ortofoto viene registrata con `imagery_provider`, `imagery_source`,
`source_url`, `attribution`, `license`, `date`, `requested`, `fallback_used` e l'elenco
dei tentativi.

---

## 6. Dove si configura

*Impostazioni > Layout e relazioni*: schede Pagina, Elementi, Loghi e immagini, Testi,
Ortofoto. I pulsanti **Duplica / Elimina / Esporta / Importa** gestiscono i profili come
file condivisibili.

Dal pannello: *Cartografia > Profili di layout* e *Report > Impostazioni relazione*.

---

## 7. Processing

| Algoritmo | Cosa fa |
|---|---|
| `territorial_suite:orthophoto_sheet` | tavola ortofoto, con scelta del fornitore e del profilo |
| `territorial_suite:imagery_providers` | elenca le ortofoto del catalogo e, per quelle non utilizzabili, il motivo |
| `territorial_suite:cultural_heritage` | analisi del patrimonio culturale, CSV e JSON |

---

## 8. API

```python
from territorial_suite.engines.cartography.orthophoto import OrthophotoEngine
from territorial_suite.engines.cartography.profiles import ProfileStore

sheet = OrthophotoEngine().run(area, preference="esri", profile_id="professionale")
print(sheet.credit_line, sheet.choice.fallback_used, sheet.warnings)

profile = ProfileStore.instance().resolve("standard").duplicate("mio", "Il mio profilo")
profile.texts["client"] = "Comune di Prova"
ProfileStore.instance().save(profile)
```
