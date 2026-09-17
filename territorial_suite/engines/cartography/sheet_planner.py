"""Which sheets an analysis has actually earned.

A sheet titled "Rischio da frana" with nothing on it is not an empty sheet: it is a
statement that somebody looked and found nothing, printed with the authority of a map.
Producing the whole catalogue every time and letting the reader work out which ones mean
something inverts the burden of proof.

So the plan is derived from the analysis, not from the catalogue. For each contract in
``config/layouts/sheets.json`` the planner asks what the run actually established about
that theme, and keeps four answers apart:

* **data present** - the sheet is generated;
* **the source answered and the area is clear** - no sheet, and that is a result worth
  writing in the report rather than drawing;
* **the source could not be reached** - no sheet, and emphatically not a clear area;
* **nothing was ever asked** - no sheet, and no claim at all.

The three refusals look identical on paper and mean entirely different things, which is
why each decision carries its reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ...core import log
from ...core.gaps import DataGap
from ...core.models import AnalysisReport, SourceStatus
from .sheet_spec import MapSheetSpecification, specification_for, specifications

#: Why a sheet was or was not produced. These are not interchangeable.
DATA_PRESENT = "DATA_PRESENT"
AVAILABLE_BUT_NO_DATA = "AVAILABLE_BUT_NO_DATA"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
NOT_DETERMINABLE = "NOT_DETERMINABLE"
NOT_REQUESTED = "NOT_REQUESTED"
PARTIAL_COVERAGE = "PARTIAL_COVERAGE"

#: Sheets that stand on their own: an overview is worth printing even when a given
#: thematic layer is absent, provided it still carries something.
GENERAL_PURPOSE = ("territorial_overview", "technical_map")

_LABELS = {
    DATA_PRESENT: "dati presenti nell'area",
    AVAILABLE_BUT_NO_DATA: "fonte interrogata, nessun dato nell'area",
    SOURCE_UNAVAILABLE: "fonte non raggiungibile",
    NOT_DETERMINABLE: "non determinabile dalle fonti disponibili",
    NOT_REQUESTED: "tema non interrogato in questa analisi",
    PARTIAL_COVERAGE: "dati presenti ma copertura parziale",
}


def label_for(status: str) -> str:
    """Italian wording of a planning outcome."""
    return _LABELS.get(status, status)


@dataclass
class SheetDecision:
    """Whether one sheet is produced, and on what evidence."""

    template: str
    title: str = ""
    theme: str = ""
    generate: bool = False
    status: str = NOT_REQUESTED
    detail: str = ""
    feature_count: int = 0
    sources: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Wording for the dock and the report."""
        return label_for(self.status)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        payload = asdict(self)
        payload["label"] = self.label
        return payload


class SheetPlanner:
    """Decide which sheets the analysis supports."""

    def __init__(self, report: AnalysisReport) -> None:
        self.report = report
        self._by_category = self._index_results()
        self._themes = self._index_hazard_themes()

    # ------------------------------------------------------------------ evidence

    def _index_results(self) -> Dict[str, List[Any]]:
        """Group the source results by the category they belong to."""
        grouped: Dict[str, List[Any]] = {}
        for result in self.report.all_results:
            if result.category:
                grouped.setdefault(result.category, []).append(result)
        return grouped

    def _index_hazard_themes(self) -> Dict[str, Dict[str, Any]]:
        """The hazard/risk module's themes, keyed by theme name."""
        module = self.report.module("hazard_risk")
        return {t.get("theme", ""): t for t in module.get("themes", []) if t.get("theme")}

    def _terrain_present(self) -> bool:
        """Whether the terrain step produced a usable DEM."""
        stats = self.report.terrain
        return stats is not None and stats.available

    # ------------------------------------------------------------------ one theme

    def assess(self, categories: List[str], theme: str = "") -> SheetDecision:
        """What the run established about a set of categories.

        The hazard module is consulted first when it owns the theme, because it knows
        the difference between "measured zero" and "could not measure" - a distinction
        the generic source results cannot always express.
        """
        decision = SheetDecision(template="", theme=theme)

        outcome = self._themes.get(theme)
        if outcome is not None:
            decision.sources = [outcome.get("source_id", "")] if outcome.get("source_id") else []
            if not outcome.get("determinable", True):
                gaps = outcome.get("gaps", [])
                decision.status = (SOURCE_UNAVAILABLE
                                   if DataGap.SOURCE_UNAVAILABLE.value in gaps
                                   else NOT_DETERMINABLE)
                decision.detail = outcome.get("statement", "")
                return decision
            classes = [c for c in outcome.get("classes", []) if c.get("area_m2", 0) > 0]
            decision.feature_count = sum(c.get("feature_count", 0) for c in classes)
            if classes:
                decision.status = (PARTIAL_COVERAGE
                                   if DataGap.PARTIAL_COVERAGE.value in outcome.get("gaps", [])
                                   else DATA_PRESENT)
                decision.generate = True
                decision.detail = outcome.get("statement", "")
            else:
                decision.status = AVAILABLE_BUT_NO_DATA
                decision.detail = outcome.get("statement", "")
            return decision

        results = [r for category in categories for r in self._by_category.get(category, [])]
        if not results:
            decision.status = NOT_REQUESTED
            decision.detail = "Nessuna fonte di questo tema e' stata interrogata."
            return decision

        decision.sources = [r.source_id for r in results]
        with_data = [r for r in results if r.present and r.feature_count > 0]
        reachable = [r for r in results
                     if r.status in (SourceStatus.ONLINE, SourceStatus.CACHED)]
        if with_data:
            decision.generate = True
            decision.status = DATA_PRESENT
            decision.feature_count = sum(r.feature_count for r in with_data)
            decision.detail = (f"{decision.feature_count} elementi da "
                               f"{len(with_data)} fonti.")
        elif reachable:
            decision.status = AVAILABLE_BUT_NO_DATA
            decision.detail = ("Le fonti hanno risposto e non riportano elementi "
                               "nell'area.")
        else:
            decision.status = SOURCE_UNAVAILABLE
            decision.detail = "Nessuna fonte del tema ha risposto."
        return decision

    # ------------------------------------------------------------------ the plan

    def plan(self, templates: Optional[List[str]] = None) -> List[SheetDecision]:
        """Decide every configured sheet, in catalogue order."""
        contracts = specifications().get("sheets") or {}
        names = templates if templates is not None else list(contracts)
        decisions: List[SheetDecision] = []
        for name in names:
            spec = specification_for(name)
            decision = self._decide(name, spec)
            decisions.append(decision)
        return decisions

    def _decide(self, template: str, spec: MapSheetSpecification) -> SheetDecision:
        """One sheet."""
        if template in GENERAL_PURPOSE or spec.general_purpose:
            # An overview earns its place from the area itself, not from one theme.
            decision = SheetDecision(template=template, title=spec.title,
                                     theme=spec.theme, generate=True,
                                     status=DATA_PRESENT,
                                     detail="Tavola di inquadramento.")
            return decision
        if spec.theme == "terrain":
            present = self._terrain_present()
            return SheetDecision(
                template=template, title=spec.title, theme=spec.theme,
                generate=present,
                status=DATA_PRESENT if present else NOT_DETERMINABLE,
                detail=("Modello altimetrico disponibile." if present
                        else "Nessun modello altimetrico prodotto per l'area."))
        decision = self.assess(spec.theme_categories(), spec.theme)
        decision.template = template
        decision.title = spec.title
        return decision

    # ------------------------------------------------------------------ summary

    @staticmethod
    def summary(decisions: List[SheetDecision]) -> Dict[str, Any]:
        """What the dock and the report print about the plan."""
        generated = [d for d in decisions if d.generate]
        skipped = [d for d in decisions if not d.generate]
        return {
            "generated": [d.as_dict() for d in generated],
            "skipped": [d.as_dict() for d in skipped],
            "counts": {
                "generated": len(generated),
                "skipped": len(skipped),
                AVAILABLE_BUT_NO_DATA: sum(1 for d in skipped
                                           if d.status == AVAILABLE_BUT_NO_DATA),
                SOURCE_UNAVAILABLE: sum(1 for d in skipped
                                        if d.status == SOURCE_UNAVAILABLE),
                NOT_DETERMINABLE: sum(1 for d in skipped
                                      if d.status == NOT_DETERMINABLE),
                NOT_REQUESTED: sum(1 for d in skipped if d.status == NOT_REQUESTED),
            },
        }


def plan_for(report: AnalysisReport,
             templates: Optional[List[str]] = None) -> List[SheetDecision]:
    """Decide which sheets an analysis supports."""
    try:
        return SheetPlanner(report).plan(templates)
    except Exception as exc:  # pragma: no cover - a broken plan must not lose the run
        log.warning(f"Pianificazione delle tavole non riuscita: {exc}")
        return []
