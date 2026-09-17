# Terreno

## Cosa fa

1. sceglie la sorgente DEM (locale o remota) e la zoom/risoluzione adeguata;
2. scarica solo le tile che coprono l'area (piu' un margine) e le mosaica;
3. riproietta nel CRS di lavoro e **ritaglia sul perimetro** dell'area;
4. calcola statistiche e derivati.

## Risultati

| Output | Dettaglio |
|---|---|
| quote | minima, massima, media, mediana, deviazione standard, dislivello |
| istogramma quote | classi configurabili (`terrain.histogram_bins`) |
| pendenza | media e massima, raster `slope.tif` (percentuale o gradi) |
| classi di pendenza | superficie e % di area per classe, soglie configurabili |
| esposizione | raster `aspect.tif` + istogramma a 8 settori (N, NE, E, SE, S, SO, O, NO) |
| ombreggiatura | `hillshade.tif`, usata come sfondo nelle tavole |
| curve di livello | `contours.gpkg` con l'equidistanza configurata (opzionale) |
| profilo altimetrico | campionamento lungo una linea (CSV da Processing) |

Il blocco di sintesi compare nel pannello Risultati e nella relazione:

```
ALTIMETRIA
----------
Min:   597 m
Max:   1169 m
Media: 862 m
Range: 573 m
Pendenza media: 43.6 %
Pendenza max:   111.8 %
```

## Sorgenti DEM

| Tipo | Descrizione |
|---|---|
| `TERRAIN_TILES` | piramide di tile GeoTIFF (schema XYZ). La sorgente inclusa e' *Terrain Tiles* su AWS Open Data: copertura globale, ~30 m in Italia |
| `RASTER` | un file locale o remoto: usa questo per un **DTM ufficiale** regionale o nazionale |

Per usare un DTM locale, apri `config/sources/terrain/terrain_tiles.json` (o copia il
descrittore nel profilo utente), compila `query.path`, imposta `enabled: true` e scegli la
sorgente nell'algoritmo o in `terrain.source_id`.

> **Quota di riferimento.** I DEM globali usano un datum verticale diverso da quello delle
> quote ortometriche italiane: per misure di progetto usa un DTM ufficiale.

## Classi di pendenza

Default `5, 10, 20, 30, 50` (%), configurabili in `terrain.slope_classes_percent`:

```
0–5 %   5–10 %   10–20 %   20–30 %   30–50 %   >50 %
```

Per ogni classe il plugin riporta pixel, superficie e percentuale dell'area.

## Parametri

| Impostazione | Default |
|---|---|
| `terrain.source_id` | vuoto (prima sorgente disponibile) |
| `terrain.cell_size_m` | 10 |
| `terrain.slope_unit` | `percent` |
| `terrain.slope_classes_percent` | `[5, 10, 20, 30, 50]` |
| `terrain.contour_interval_m` | 25 |
| `terrain.profile_samples` | 500 |

## Nota tecnica (per chi sviluppa)

Tutti gli oggetti GDAL hanno un ciclo di vita esplicito: la *band* viene rilasciata prima
del *dataset*, e i dataset restituiti da `BuildVRT`, `Warp`, `DEMProcessing` e
`ContourGenerate` vengono svuotati e chiusi subito. Tenere viva una band dopo il suo dataset
corrompe la memoria del processo e fa crashare QGIS piu' tardi, in un punto qualsiasi.

## Scheda della funzione

| Domanda | Risposta |
|---|---|
| Quale problema risolve | avere quote, pendenze ed esposizione dell'area senza cercare, scaricare, mosaicare e ritagliare un DEM a mano |
| Per quale utente | forestali, agronomi, progettisti, geologi |
| Quale passaggio manuale elimina | trovare il DEM, scaricare le tile, mosaicare, riproiettare, ritagliare, calcolare slope/aspect/hillshade, classificare |
| Quale fonte utilizza | sorgenti di categoria `terrain` del catalogo |
| Assenza della fonte | avviso nei warning, analisi che prosegue senza dati altimetrici |
| Errori | ogni derivato e' opzionale: se GDAL fallisce su uno, gli altri restano |
| Test | `tests/unit/test_models.py` (statistiche), verifiche live documentate nel changelog |
| Documentazione | questo file |
| Riuso da Processing | *Statistiche del terreno*, *Profilo altimetrico* |
| Compatibilita' | `engines.terrain`, solo GDAL + numpy |

## Rilievo ombreggiato

Quote, pendenza ed esposizione sono sempre accompagnate dall'ombreggiatura: il colore
porta il valore, l'ombra porta la forma. Senza ombra una carta delle pendenze e' una
macchia colorata.

**Sul canvas** l'ombreggiatura viene posta sopra il tema colorato in modalita'
*moltiplica*, con opacita' `terrain.hillshade_opacity` (0,55 di default).

**In stampa** QGIS perde i blend mode in diversi percorsi di export: una tavola stampata
risulterebbe piatta. Per questo il motore cuoce un GeoTIFF gia' composto — `dem_shaded.tif`,
`slope_shaded.tif`, `aspect_shaded.tif` — con la stessa matematica del canvas
(`colore x ombra`), e **le tavole usano quello**. I compositi vengono caricati nel progetto
ma lasciati spenti: sul canvas servono i layer vivi, non una copia appiattita.

Le rampe vivono in `config/styles/styles.json` e sono **discrete a valori assoluti**:

* **esposizione** — 8 settori cardinali (N, NE, E, SE, S, SO, O, NO) con
  l'avvolgimento corretto fra 337,5 e 22,5 gradi;
* **pendenza** — classi in percentuale (0-5, 5-10, 10-20, 20-30, 30-50, oltre 50).

Valori assoluti e non normalizzati sul minimo e massimo locali: cosi' una pendenza del
30 % ha lo stesso colore su qualunque tavola, e due aree diverse si possono confrontare.

Parametri dell'ombreggiatura (`config/defaults.json`): `terrain.hillshade_azimuth` (315),
`terrain.hillshade_altitude` (45), `terrain.hillshade_z_factor` (1.0),
`terrain.hillshade_opacity` (0.55), `terrain.shaded_relief` (portare a `false` per non
cuocere i compositi).

La legenda usa sempre la rampa cromatica del tema, mai il grigio dell'ombreggiatura.
