"""The dossier section on cultural and landscape heritage.

The section is built here, next to the module that produced the data, and handed to the
report engine as HTML: the report engine keeps owning the document (its stylesheet, its
tables, its PDF writer), this module keeps owning what heritage data may be said to mean.

Its structure follows the deliverable: summary, cultural assets, landscape heritage,
MiC/SITAP, superintendencies, spatial analysis, proximity, data quality, sources queried,
sources unavailable, areas outside coverage, notes and limitations.
"""

from __future__ import annotations

import html
from typing import Any, Callable, List, Optional, Sequence

from ...core.registry import DataSourceRegistry
from .classifier import ROOT_CATEGORY
from .model import UNKNOWN, CulturalAsset, CulturalHeritageOutcome, DataGap, ThemeOutcome
from .quality import summarise

#: Themes reported as "beni culturali" and as "patrimonio paesaggistico".
CULTURAL_THEMES = ("cultural_heritage_assets", "archaeological_heritage", "unesco_heritage")
LANDSCAPE_THEMES = ("landscape_assets_declared", "landscape_areas_by_law",
                    "wetlands_ramsar")

#: The disclaimer printed once, at the head of the section.
DISCLAIMER = (
    "Questa sezione riporta <b>dati cartografici</b> pubblicati dal Ministero della "
    "Cultura e dagli enti competenti. L'intersezione fra l'area di progetto e un dato "
    "cartografico <b>non equivale</b> all'accertamento di un vincolo: costituisce un "
    "elemento conoscitivo che indica dove effettuare la verifica. Gli estremi dei "
    "provvedimenti eventualmente riportati sono quelli <b>dichiarati dalla fonte</b> e "
    "vanno riscontrati presso l'ente competente.")


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


class CulturalHeritageReport:
    """Renders the heritage section of the dossier."""

    def __init__(self, outcome: CulturalHeritageOutcome, *, number: int = 14,
                 max_rows: int = 40,
                 registry: Optional[DataSourceRegistry] = None) -> None:
        self.outcome = outcome
        self.number = number
        self.max_rows = max(1, int(max_rows))
        self.registry = registry or DataSourceRegistry.instance()

    # ------------------------------------------------------------------ entry point

    def to_html(self, table: Callable[[Sequence[str], Sequence[Sequence[Any]]], str]) -> str:
        """Return the whole section.

        ``table`` is the report engine's own table renderer, passed in so the section
        inherits the document's styling instead of inventing its own.
        """
        self._table = table
        blocks = [f"<h2>{self.number}. Patrimonio culturale e paesaggistico</h2>",
                  f"<p class='small'>{DISCLAIMER}</p>"]
        step = iter(range(1, 20))
        for builder in (self._summary, self._cultural_assets, self._landscape,
                        self._mic_sitap, self._superintendencies, self._spatial,
                        self._proximity, self._data_quality, self._sources_queried,
                        self._sources_unavailable, self._outside_coverage, self._notes):
            index = next(step)
            block = builder(f"{self.number}.{index}")
            if block:
                blocks.append(block)
        return "\n".join(blocks)

    # ------------------------------------------------------------------ subsections

    def _summary(self, number: str) -> str:
        assets = self.outcome.assets
        intersecting = self.outcome.intersecting
        with_act = self.outcome.with_act
        offices = [o for o in self.outcome.superintendencies if o.known]
        rows = [
            ["Elementi rilevati nel contesto dell'area", len(assets)],
            ["Elementi che intersecano l'area", len(intersecting)],
            ["Elementi con riferimento a un atto dichiarato dalla fonte", len(with_act)],
            ["Temi valutati", len([t for t in self.outcome.themes if not t.gaps])],
            ["Temi non valutabili", len([t for t in self.outcome.themes if t.gaps])],
            ["Soprintendenze competenti individuate",
             ", ".join(o.name for o in offices) if offices else UNKNOWN],
        ]
        head = f"<h3>{number} Sintesi</h3>"
        if not assets and not offices:
            return (head + "<p class='missing'>Nessun elemento del patrimonio culturale "
                           "o paesaggistico e' stato rilevato dalle fonti interrogate. "
                           "Vedere la sottosezione sulla qualita' del dato per sapere "
                           "se cio' significa assenza di beni o assenza di dato.</p>")
        return head + self._table_two_columns(rows)

    def _cultural_assets(self, number: str) -> str:
        return self._themes_block(f"{number} Beni culturali", CULTURAL_THEMES)

    def _landscape(self, number: str) -> str:
        return self._themes_block(f"{number} Patrimonio paesaggistico", LANDSCAPE_THEMES)

    def _mic_sitap(self, number: str) -> str:
        """Which MiC datasets answered, with the nature and the evidence of each."""
        rows = []
        for theme in self.outcome.themes:
            for source_id in theme.sources_queried:
                source = self.registry.get(source_id)
                if source is None or "cultura" not in (source.authority or "").lower():
                    continue
                rows.append([source.name, source.layer,
                             source.data_nature.label_it,
                             source.evidence_level.label_it,
                             source.verification_status.label_it,
                             source.coverage.label_it()])
        if not rows:
            return ""
        unique = {tuple(row): row for row in rows}
        return (f"<h3>{number} Fonti del Ministero della Cultura (SITAP)</h3>" +
                self._table(["Fonte", "Layer", "Natura del dato", "Valore probatorio",
                             "Stato di verifica", "Copertura"], list(unique.values())))

    def _superintendencies(self, number: str) -> str:
        offices = [o for o in self.outcome.superintendencies if o.known]
        head = f"<h3>{number} Soprintendenze</h3>"
        if not offices:
            return (head + "<p class='missing'>La Soprintendenza competente non e' stata "
                           "determinata dalle fonti disponibili. Il dato non viene "
                           "dedotto dalla posizione geografica: la competenza "
                           "territoriale degli uffici non coincide con i confini "
                           "amministrativi.</p>")
        rows = [[o.name, o.code, o.office_type, o.website, o.pec] for o in offices]
        note = ("<p class='small'>Ufficio individuato dal layer ufficiale degli ambiti "
                "di competenza. Un'area a cavallo di un confine puo' ricadere in piu' "
                "ambiti: sono elencati tutti.</p>")
        return head + self._table(["Ufficio", "Codice", "Tipo", "Sito", "PEC"],
                                  rows) + note

    def _spatial(self, number: str) -> str:
        intersecting = self.outcome.intersecting
        head = f"<h3>{number} Analisi spaziale</h3>"
        if not intersecting:
            return head + ("<p class='missing'>Nessun elemento interseca l'area di "
                           "progetto fra quelli restituiti dalle fonti interrogate.</p>")
        rows = [[a.name, a.asset_type, a.source_name,
                 f"{a.intersect_area_m2:,.0f}".replace(",", " ") if a.intersect_area_m2
                 else "-",
                 f"{a.intersect_pct:.2f}" if a.intersect_pct else "-",
                 a.act.label_it() or "-"]
                for a in self._limit(intersecting)]
        table = self._table(["Elemento", "Tipo", "Fonte", "Superficie interessata (m2)",
                             "% area", "Riferimento dichiarato dalla fonte"], rows)
        return head + table + self._truncation_note(intersecting)

    def _proximity(self, number: str) -> str:
        nearby = sorted((a for a in self.outcome.assets if not a.intersects),
                        key=lambda a: a.distance_m if a.distance_m is not None else 1e12)
        head = f"<h3>{number} Prossimita'</h3>"
        if not nearby:
            return ""
        rows = [[a.name, a.asset_type, a.source_name,
                 f"{a.distance_m:.0f}" if a.distance_m is not None else "-"]
                for a in self._limit(nearby)]
        return (head + self._table(["Elemento", "Tipo", "Fonte", "Distanza (m)"], rows) +
                self._truncation_note(nearby))

    def _data_quality(self, number: str) -> str:
        head = f"<h3>{number} Qualita' del dato</h3>"
        lines = summarise(self.outcome.gap_summary)
        blocks = [head]
        if lines:
            blocks.append("<ul>" + "".join(f"<li>{_escape(line)}</li>"
                                           for line in lines) + "</ul>")
        else:
            blocks.append("<p>Tutti i temi sono stati valutati con le fonti "
                          "disponibili.</p>")
        if self.outcome.warnings:
            blocks.append("<ul>" + "".join(f"<li>{_escape(w)}</li>"
                                           for w in self.outcome.warnings) + "</ul>")
        blocks.append("<p class='small'>L'assenza di elementi restituiti da una fonte "
                      "non equivale all'assenza di beni o di tutele sul territorio.</p>")
        return "".join(blocks)

    def _sources_queried(self, number: str) -> str:
        rows = []
        for theme in self.outcome.themes:
            for source_id in theme.sources_queried:
                source = self.registry.get(source_id)
                state = "non ha risposto" if source_id in theme.sources_failed \
                    else "ha risposto"
                found = len([a for a in theme.assets if a.source_id == source_id])
                rows.append([source.name if source else source_id,
                             theme.label, state, found])
        if not rows:
            return ""
        return (f"<h3>{number} Fonti interrogate</h3>" +
                self._table(["Fonte", "Tema", "Esito", "Elementi"], rows))

    def _sources_unavailable(self, number: str) -> str:
        rows = []
        for theme in self.outcome.themes:
            for source_id in theme.sources_failed:
                source = self.registry.get(source_id)
                rows.append([source.name if source else source_id, theme.label,
                             "non disponibile al momento dell'analisi"])
            for source_id in theme.sources_catalogued:
                source = self.registry.get(source_id)
                rows.append([source.name if source else source_id, theme.label,
                             source.verification_status.label_it if source
                             else DataGap.SOURCE_NOT_VERIFIED.label_it])
        if not rows:
            return ""
        note = ("<p class='small'>Le fonti censite ma non operative documentano dati che "
                "esistono e che il plugin non e' in grado di interrogare: non sono state "
                "usate nell'analisi e i temi che coprono restano da verificare "
                "direttamente presso l'ente.</p>")
        return (f"<h3>{number} Fonti non disponibili o non operative</h3>" +
                self._table(["Fonte", "Tema", "Stato"], rows) + note)

    def _outside_coverage(self, number: str) -> str:
        notes: List[str] = []
        for theme in self.outcome.themes:
            for note in theme.coverage_notes:
                if note not in notes:
                    notes.append(note)
        if not notes:
            return ""
        return (f"<h3>{number} Copertura delle fonti</h3><ul>" +
                "".join(f"<li>{_escape(note)}</li>" for note in notes) + "</ul>")

    def _notes(self, number: str) -> str:
        items = [
            "I dati cartografici hanno valore ricognitivo: non sostituiscono la "
            "consultazione degli atti presso gli enti competenti.",
            "Le tutele previste dall'art. 142 del D.Lgs. 42/2004 operano per legge: la "
            "cartografia le rappresenta, non le costituisce.",
            "L'iscrizione di un sito nella lista UNESCO non e' di per se' un vincolo: le "
            "tutele applicabili discendono dagli strumenti nazionali e locali.",
            "Le Regioni e le Province autonome a statuto speciale gestiscono in proprio "
            "parte del patrimonio: in quei territori le fonti nazionali possono essere "
            "mute pur in presenza di tutele.",
        ]
        return (f"<h3>{number} Note e limitazioni</h3><ul>" +
                "".join(f"<li>{_escape(item)}</li>" for item in items) + "</ul>")

    # ------------------------------------------------------------------ helpers

    def _themes_block(self, title: str, categories: Sequence[str]) -> str:
        blocks = [f"<h3>{title}</h3>"]
        wrote = False
        for theme in self.outcome.themes:
            if theme.category not in categories:
                continue
            wrote = True
            blocks.append(f"<h4>{_escape(theme.label)}</h4>")
            blocks.append(self._theme_body(theme))
        if not wrote:
            return ""
        return "".join(blocks)

    def _theme_body(self, theme: ThemeOutcome) -> str:
        if not theme.assets:
            gaps = [DataGap(g).label_it for g in theme.gaps if _is_gap(g)]
            reason = "; ".join(gaps) if gaps else "nessun elemento restituito"
            return f"<p class='missing'>{_escape(reason)}.</p>"
        rows = [[a.name, a.asset_type,
                 "si'" if a.intersects else "no",
                 f"{a.distance_m:.0f}" if a.distance_m is not None else "0",
                 a.source_name,
                 a.act.label_it() or "-",
                 f"{a.source_count} fonti" if a.source_count > 1 else ""]
                for a in self._limit(theme.assets)]
        table = self._table(["Elemento", "Tipo", "Interseca", "Distanza (m)", "Fonte",
                             "Riferimento dichiarato", "Provenienza"], rows)
        return table + self._truncation_note(theme.assets)

    def _limit(self, assets: Sequence[CulturalAsset]) -> List[CulturalAsset]:
        ordered = sorted(assets, key=lambda a: (not a.intersects,
                                                -(a.intersect_pct or 0.0),
                                                a.distance_m if a.distance_m else 0.0))
        return ordered[:self.max_rows]

    def _truncation_note(self, assets: Sequence[CulturalAsset]) -> str:
        if len(assets) <= self.max_rows:
            return ""
        return (f"<p class='small'>Elenco troncato alle prime {self.max_rows} righe su "
                f"{len(assets)}: l'elenco completo e' nei file allegati.</p>")

    def _table_two_columns(self, rows: Sequence[Sequence[Any]]) -> str:
        return self._table(["Voce", "Valore"], rows)


def _is_gap(value: str) -> bool:
    try:
        DataGap(value)
    except ValueError:
        return False
    return True


def rows_for_export(outcome: CulturalHeritageOutcome) -> List[List[Any]]:
    """Flat rows for the CSV/XLSX attachment of the dossier."""
    rows: List[List[Any]] = []
    for theme in outcome.themes:
        for asset in theme.assets:
            rows.append([
                theme.label, asset.name, asset.asset_type, asset.municipality,
                asset.province, "si'" if asset.intersects else "no",
                round(asset.intersect_area_m2, 1), round(asset.intersect_pct, 3),
                "" if asset.distance_m is None else round(asset.distance_m, 1),
                asset.source_name, asset.source_id, asset.act.label_it(),
                asset.legal_reference, asset.evidence_level, asset.data_nature,
                asset.source_count, ";".join(asset.findings),
            ])
    return rows


EXPORT_HEADER = ["Tema", "Elemento", "Tipo", "Comune", "Provincia", "Interseca",
                 "Superficie interessata (m2)", "% area", "Distanza (m)", "Fonte",
                 "Id fonte", "Riferimento dichiarato", "Riferimento normativo",
                 "Valore probatorio", "Natura del dato", "N. fonti", "Classificazione"]

#: Category the section belongs to in the taxonomy, for the report engine.
SECTION_CATEGORY = ROOT_CATEGORY
