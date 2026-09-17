"""Map series generator: one project area, a whole set of independent layouts.

``GENERATE MAP SERIES`` builds one QGIS layout per map type declared in the configured
series (overview, cadastre, constraints, environment, hydrography, infrastructure, risk,
orthophoto, elevation, slope), each with its own layer selection and scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from qgis.core import QgsPrintLayout, QgsProject

from ...core import log
from ...core.errors import UserCancelled
from ...core.feedback import Feedback, NullFeedback
from ...core.models import AnalysisReport
from ...core.project_area import ProjectArea
from . import export as export_module
from .layout import LayoutBuilder, MapSpec, get_template, series_names, template_list


@dataclass
class SeriesResult:
    """Layouts produced by a series run."""

    layouts: List[QgsPrintLayout] = field(default_factory=list)
    titles: Dict[str, str] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of layouts created."""
        return len(self.layouts)


class MapSeriesEngine:
    """Creates a series of layouts for one project area."""

    def __init__(self, project: Optional[QgsProject] = None) -> None:
        self.project = project or QgsProject.instance()
        self.builder = LayoutBuilder(self.project)

    def available_series(self) -> Dict[str, List[str]]:
        """Return the configured series."""
        return series_names()

    def available_templates(self) -> List[Dict[str, object]]:
        """Return the configured templates."""
        return template_list()

    def generate(self, area: ProjectArea, *, series: str = "default",
                 template_ids: Optional[Sequence[str]] = None,
                 report: Optional[AnalysisReport] = None,
                 base_spec: Optional[MapSpec] = None,
                 skip_empty: bool = True,
                 feedback: Optional[Feedback] = None) -> SeriesResult:
        """Build every layout of a series.

        ``skip_empty`` drops a map whose template asks for categories that produced no
        layer at all - a "risk map" with nothing on it helps nobody.
        """
        feedback = feedback or NullFeedback()
        result = SeriesResult()
        ids = list(template_ids or self.available_series().get(series, []))
        if not ids:
            result.warnings.append(f"Serie '{series}' non configurata")
            return result

        # With an analysis in hand the series is decided by what the run established,
        # not by the catalogue. A sheet nobody's data supports is not printed empty: an
        # empty thematic sheet reads as "we looked and the area is clear", which is a
        # different statement from "the service was down" and from "nobody asked".
        plan = {}
        if report is not None and skip_empty:
            from .sheet_planner import plan_for

            plan = {d.template: d for d in plan_for(report, templates=ids)}
            refused = [d for d in plan.values() if not d.generate]
            for decision in refused:
                result.skipped.append(decision.template)
                result.warnings.append(
                    f"{decision.title or decision.template}: non generata - "
                    f"{decision.label}. {decision.detail}".strip())
            ids = [i for i in ids if plan.get(i) is None or plan[i].generate]
            if not ids:
                result.warnings.append(
                    "Nessun tema dell'analisi giustifica una tavola tematica.")
                return result

        for index, template_id in enumerate(ids, start=1):
            if feedback.is_canceled():
                raise UserCancelled()
            template = get_template(template_id)
            spec = MapSpec(**{**(base_spec.__dict__ if base_spec else {}),
                              "template": template_id,
                              "sheet_number": f"{index:02d}"})
            spec.title = template.get("title", template.get("name", template_id))
            feedback.set_step(f"Tavola {index}/{len(ids)}: {spec.title}")
            layers = self.builder.layers_for(template, spec)
            wanted = template.get("layers", {}).get("categories", [])
            if skip_empty and wanted and not layers:
                result.skipped.append(template_id)
                feedback.push_debug(f"{template_id}: nessun layer disponibile, tavola saltata")
                continue
            try:
                layout = self.builder.build(area, spec, report=report, layers=layers or None)
            except Exception as exc:  # pragma: no cover - a template must not break the run
                log.exception(f"Cannot build layout {template_id}", exc)
                result.warnings.append(f"{template_id}: {exc}")
                continue
            result.layouts.append(layout)
            result.titles[layout.name()] = spec.title
            feedback.set_progress(100.0 * index / len(ids))
        return result

    def export(self, result: SeriesResult, folder: Path, fmt: str = export_module.PDF, *,
               dpi: Optional[int] = None) -> List[export_module.ExportResult]:
        """Export a generated series to a folder."""
        return export_module.ExportCenter.export_series(result.layouts, folder, fmt,
                                                        dpi=dpi, names=result.titles)
