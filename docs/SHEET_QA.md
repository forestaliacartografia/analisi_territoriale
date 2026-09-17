# Contratto e controllo qualita' delle tavole

> Una tavola intitolata «Carta del rischio idraulico» non e' valida se il rischio
> idraulico non e' davvero rappresentato nella mappa e nella legenda.

Questo documento descrive come il plugin verifica quell'affermazione invece di darla per
scontata.

## Perche' esiste

Una carta il cui titolo promette un tema e la cui legenda mostra strade, comuni e
un'ortofoto non e' una scelta di stile: e' un'affermazione falsa, e chi la legge non ha
modo di accorgersene. Il problema non e' estetico ma di correttezza analitica, quindi il
controllo e' parte della pipeline e non un'opzione.

Il difetto concreto da cui questo lavoro e' partito era gia' nel repository: il template
`risk_map`, intitolato «Carta del rischio», raccoglieva la categoria
`hydro_geomorphological`. Il vincolo idrogeologico e' un **vincolo**, non un rischio:
finiva stampato su una tavola che annunciava tutt'altro istituto giuridico.

## Il contratto

Ogni tavola dichiara in `config/layouts/sheets.json` che cosa promette:

```json
"flood_hazard_map": {
  "title": "Carta della pericolosita idraulica",
  "theme": "flood_hazard",
  "required_categories": ["flood_hazard"],
  "optional_categories": ["hydrography", "water"],
  "excluded_categories": ["flood_risk"],
  "title_keywords": ["pericolosita idraulica", "aree allagabili", "pgra"],
  "primary_source": "pcn.alluvioni"
}
```

| Campo | Significato |
|---|---|
| `theme` | id di **categoria della tassonomia**, non una parola libera. Vuoto = tavola senza tema dominante |
| `required_categories` | devono essere presenti, non vuote, nel riquadro e in legenda |
| `optional_categories` | contesto legittimo, non fanno scattare il controllo inverso |
| `excluded_categories` | vietate su questa tavola |
| `required_legend_items` | voci che devono comparire in legenda per nome |
| `title_keywords` | parole aggiuntive che identificano il tema, oltre a quelle della tassonomia |

**Il vocabolario non e' inventato nel codice.** Le parole con cui un titolo viene
confrontato sono le etichette della categoria nella tassonomia: il significato di
«pericolosita idraulica» resta definito in un posto solo. Un test lo impone, fallendo se
un nome di tema compare nel modulo Python.

## I due controlli

### Titolo → legenda

Il tema dichiarato deve davvero essere sulla tavola. Non basta che lo strato sia nel
progetto: deve essere **nel riquadro di mappa**, **non vuoto**, **dentro l'estensione
stampata** e **nominato in legenda**. Uno strato presente ma spento, o disegnato ma
assente dalla legenda, non prova nulla a chi legge la carta.

### Legenda → titolo

Il controllo inverso, richiesto perche' intercetta una bugia diversa: una tavola la cui
legenda e' dominata da temi che il titolo non nomina puo' essere una carta generale con un
nome specifico. E' un'**avvertenza**, non un errore, perche' i livelli di contesto sono
legittimi e solo lo sbilanciamento e' sospetto. Confini, ortofoto, terreno, viabilita',
idrografia e toponimi contano come contesto e non fanno scattare l'avvertenza.

## Esiti

| Livello | Significato | Effetto sull'export |
|---|---|---|
| `PASS` | controlli superati | esporta |
| `WARNING` | da leggere, non bloccante | esporta |
| `ERROR` | la tavola contraddice il proprio titolo | **non esporta** |

Errori attualmente rilevati:

- `no_map_frame` — la tavola non ha un riquadro di mappa
- `no_title` — nessun titolo
- `theme_layer_missing` — il tema dichiarato non e' nel riquadro
- `theme_layer_empty` — lo strato del tema non contiene alcun elemento
- `theme_layer_out_of_extent` — il tema e' interamente fuori dall'estensione stampata
- `theme_missing_from_legend` — disegnato ma non riconoscibile
- `legend_item_missing` — manca una voce di legenda richiesta
- `excluded_category_present` — la tavola contiene un tema che il contratto vieta

Avvertenze: `title_does_not_name_theme`, `generic_title`, `legend_not_focused`.

## Blocco dell'export

`ExportCenter.export()` verifica **prima di scrivere**. Se l'esito e' `ERROR` il file non
viene creato e il risultato porta il report, cosi' chi chiama puo' mostrare il motivo e
non solo il fallimento:

```python
result = ExportCenter.export(layout, path, PDF)
if not result.ok and result.qa is not None:
    for finding in result.qa.errors:
        print(finding.message)
```

Una tavola sbagliata che sembra finita e' peggio di una tavola mancante, perche' nulla a
valle puo' accorgersene. Per una bozza deliberata si passa `validate=False`;
l'impostazione `cartography.validate_sheets` disattiva il controllo per tutto il plugin,
ma il difetto resta.

La tavola ricorda il proprio contratto: `LayoutBuilder` marca il layout con la proprieta'
`territorial_suite/layout_template`, altrimenti l'export potrebbe solo indovinare che cosa
il titolo prometteva.

## Limiti dichiarati

- Il conteggio «strato vuoto» risponde solo per i layer **vettoriali**. Un raster non
  viene mai dichiarato vuoto: contarne i pixel e' una domanda diversa e molto piu' cara.
- Il controllo sull'estensione confronta i *bounding box*, non le geometrie: uno strato il
  cui bbox interseca la tavola ma le cui geometrie cadono appena fuori passa il controllo.
- La verifica e' **strutturale**: accerta che il tema sia presente, non vuoto, ordinato e
  in legenda. Non giudica se la simbologia sia leggibile, ne' se la scala sia adatta.
