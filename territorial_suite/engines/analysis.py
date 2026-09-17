"""One-Click Territorial Analysis: the orchestrator.

    LOCATION -> ADMINISTRATIVE UNITS -> CADASTRE -> CONSTRAINTS -> TERRAIN -> DATA
             -> RULES -> SUMMARY

Each step is optional, reports progress on its own slice of the progress bar, and *cannot*
abort the run: a failing step is recorded as a warning plus a ``CARTOGRAPHIC_ISSUE`` alert
and the pipeline continues. The output is a single :class:`AnalysisReport`, which is what
the report engine, the cartography engine, the package builder and the GUI all consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from qgis.core import Qgis

from ..core import log, settings
from ..core.cache import CacheManager
from ..core.constants import PLUGIN_VERSION
from ..core.errors import UserCancelled
from ..core.feedback import ChildFeedback, Feedback, NullFeedback
from ..core.models import (
    Alert,
    AlertLevel,
    AnalysisReport,
    EvidenceLevel,
    LayerRef,
    utc_now,
)
from ..core.project_area import ProjectArea
from ..core.registry import DataSourceRegistry
from ..services.http import HttpClient
from .admin import AdminResolver
from .cadastre_engine import CadastreEngine
from .constraints import ConstraintEngine
from .cultural_heritage.engine import CulturalHeritageEngine
from .download import DownloadManager
from .rules import RuleEngine
from .terrain import TerrainEngine


@dataclass
class AnalysisOptions:
    """What the user asked the analysis to do."""

    categories: Optional[Sequence[str]] = None
    resolve_admin: bool = True
    include_cadastre: bool = True
    include_constraints: bool = True
    include_cultural_heritage: bool = True
    include_terrain: bool = True
    include_download: bool = False
    download_categories: Optional[Sequence[str]] = None
    terrain_source_id: str = ""
    terrain_cell_size_m: Optional[float] = None
    terrain_contours: bool = False
    refresh: bool = False
    store_layers: bool = True

    @classmethod
    def quick(cls) -> "AnalysisOptions":
        """Defaults of the Quick mode (analysis only, no bulk download)."""
        return cls()

    @classmethod
    def full(cls) -> "AnalysisOptions":
        """Everything, including the vector download - used by "Download everything"."""
        return cls(include_download=True, terrain_contours=True)


#: Relative weight of each step in the global progress bar.
_WEIGHTS = {"admin": 8, "cadastre": 17, "constraints": 38, "cultural": 4, "terrain": 20,
            "download": 10, "rules": 5}


class AnalysisOrchestrator:
    """Runs the full territorial analysis of a project area."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http
        self.cache = cache

    def run(self, area: ProjectArea, options: Optional[AnalysisOptions] = None, *,
            feedback: Optional[Feedback] = None) -> AnalysisReport:
        """Execute the pipeline and return the complete report."""
        options = options or AnalysisOptions()
        feedback = feedback or NullFeedback()
        report = AnalysisReport(
            area=area.as_dict(),
            started_at=utc_now(),
            plugin_version=PLUGIN_VERSION,
            qgis_version=Qgis.QGIS_VERSION,
            settings_snapshot=settings.snapshot(),
        )
        steps = self._enabled_steps(options)
        total_weight = sum(_WEIGHTS[name] for name in steps) or 1
        done = 0.0

        for name in steps:
            if feedback.is_canceled():
                raise UserCancelled()
            start = 100.0 * done / total_weight
            done += _WEIGHTS[name]
            end = 100.0 * done / total_weight
            child = ChildFeedback(feedback, start, end)
            handler = getattr(self, f"_step_{name}")
            try:
                handler(area, options, report, child)
            except UserCancelled:
                raise
            except Exception as exc:  # pragma: no cover - a step must never abort the run
                log.exception(f"Analysis step '{name}' failed", exc)
                report.warnings.append(f"Fase '{name}' non completata: {exc}")
                report.alerts.append(Alert(
                    level=AlertLevel.CARTOGRAPHIC_ISSUE, code=f"step.{name}.failed",
                    title=f"Fase di analisi non completata: {name}",
                    detail=str(exc), evidence_level=EvidenceLevel.CARTOGRAPHIC))
            feedback.set_progress(end)

        report.finished_at = utc_now()
        feedback.set_progress(100)
        feedback.push_info(self.summary_text(report))
        return report

    # ------------------------------------------------------------------ steps

    @staticmethod
    def _enabled_steps(options: AnalysisOptions) -> List[str]:
        steps = []
        if options.resolve_admin:
            steps.append("admin")
        if options.include_cadastre:
            steps.append("cadastre")
        if options.include_constraints:
            steps.append("constraints")
        if options.include_cultural_heritage:
            steps.append("cultural")
        if options.include_terrain:
            steps.append("terrain")
        if options.include_download:
            steps.append("download")
        steps.append("rules")
        return steps

    def _step_admin(self, area: ProjectArea, options: AnalysisOptions,
                    report: AnalysisReport, feedback: Feedback) -> None:
        resolver = AdminResolver(registry=self.registry, http=self.http, cache=self.cache)
        admin = resolver.resolve(area, feedback=feedback)
        area.admin = admin
        report.admin = admin
        report.area = area.as_dict()
        if not admin.resolved:
            report.warnings.append(
                "Unita amministrative non determinate: interrogate solo sorgenti "
                "nazionali ed europee.")

    def _step_cadastre(self, area: ProjectArea, options: AnalysisOptions,
                       report: AnalysisReport, feedback: Feedback) -> None:
        engine = CadastreEngine(registry=self.registry, http=self.http, cache=self.cache)
        outcome = engine.run(area, feedback=feedback, refresh=options.refresh)
        report.cadastre = outcome.rows
        report.results.extend(outcome.source_results)
        report.layers.extend(outcome.layers)
        report.warnings.extend(outcome.warnings)
        if outcome.municipalities and not report.admin.resolved:
            report.admin.municipalities = outcome.municipalities
            report.admin.resolved = True
            area.admin = report.admin
            report.area = area.as_dict()

    def _step_constraints(self, area: ProjectArea, options: AnalysisOptions,
                          report: AnalysisReport, feedback: Feedback) -> None:
        engine = ConstraintEngine(registry=self.registry, http=self.http, cache=self.cache)
        # The cadastre step already queried the cadastral service: never ask twice.
        exclude = ["cadastral"] if options.include_cadastre else []
        outcome = engine.run(area, categories=options.categories, feedback=feedback,
                             refresh=options.refresh, store_layers=options.store_layers,
                             exclude_categories=exclude)
        report.results.extend(outcome.results)
        report.layers.extend(outcome.layers)
        report.warnings.extend(outcome.warnings)

    def _step_cultural(self, area: ProjectArea, options: AnalysisOptions,
                       report: AnalysisReport, feedback: Feedback) -> None:
        """Structure the cultural and landscape heritage already downloaded.

        The constraint step has normally queried these sources already, so the results
        are handed over instead of being fetched again; only the sources it skipped (when
        the user narrowed the categories) are downloaded here.
        """
        engine = CulturalHeritageEngine(registry=self.registry, http=self.http,
                                        cache=self.cache)
        outcome = engine.run(area, feedback=feedback, refresh=options.refresh,
                             store_layers=options.store_layers, results=report.results)
        engine.attach(report, outcome)
        report.layers.extend(engine.layers)
        report.warnings.extend(outcome.warnings)

    def _step_terrain(self, area: ProjectArea, options: AnalysisOptions,
                      report: AnalysisReport, feedback: Feedback) -> None:
        engine = TerrainEngine(registry=self.registry, http=self.http, cache=self.cache)
        outputs = engine.run(area, feedback=feedback, source_id=options.terrain_source_id,
                             cell_size_m=options.terrain_cell_size_m,
                             compute_contours=options.terrain_contours,
                             refresh=options.refresh)
        report.terrain = outputs.stats
        report.layers.extend(outputs.layers)
        report.warnings.extend(outputs.warnings)

    def _step_download(self, area: ProjectArea, options: AnalysisOptions,
                       report: AnalysisReport, feedback: Feedback) -> None:
        manager = DownloadManager(registry=self.registry, http=self.http, cache=self.cache)
        outcome = manager.download(area, options.download_categories, feedback=feedback,
                                   refresh=options.refresh)
        report.downloads.extend(outcome.results)
        report.layers.extend(outcome.layers)
        report.warnings.extend(outcome.warnings)

    def _step_rules(self, area: ProjectArea, options: AnalysisOptions,
                    report: AnalysisReport, feedback: Feedback) -> None:
        feedback.set_step("Valutazione regole e alert")
        report.alerts.extend(RuleEngine.load().evaluate(report))
        report.alerts.extend(self._unavailable_source_alerts(report))
        feedback.set_progress(100)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _unavailable_source_alerts(report: AnalysisReport) -> List[Alert]:
        """One alert per source that did not answer - the dossier must say what is missing."""
        alerts: List[Alert] = []
        for result in report.sources_failed:
            alerts.append(Alert(
                level=AlertLevel.CARTOGRAPHIC_ISSUE,
                code="source.unavailable",
                title=f"Fonte non disponibile: {result.source_name}",
                detail=(f"{result.error}. L'analisi e' proseguita senza questa fonte: "
                        f"il quadro conoscitivo della categoria e' incompleto."),
                category=result.category,
                evidence_level=result.evidence_level,
                source_ids=[result.source_id],
            ))
        return alerts

    @staticmethod
    def summary_text(report: AnalysisReport) -> str:
        """Return the "TERRITORIAL ANALYSIS COMPLETE" summary block."""
        alerts = report.sorted_alerts()
        counters = {level: 0 for level in AlertLevel}
        for alert in alerts:
            counters[alert.level] += 1
        lines = [
            "ANALISI TERRITORIALE COMPLETATA",
            "-------------------------------",
            f"Area: {report.area.get('name', '')} - "
            f"{float(report.area.get('area_ha', 0.0)):.2f} ha",
            f"Comuni: {report.admin.summary()}",
            f"Fonti interrogate: {len(report.all_results)} "
            f"(disponibili {len(report.sources_ok)}, non disponibili "
            f"{len(report.sources_failed)})",
            f"Dati presenti nell'area: {len(report.present_results)}",
            f"Particelle catastali: {len(report.cadastre)}",
        ]
        if report.terrain is not None and report.terrain.available:
            lines.append(f"Quote: {report.terrain.elevation_min:.0f}-"
                         f"{report.terrain.elevation_max:.0f} m")
        lines.append("Segnalazioni: " + ", ".join(
            f"{level.label_it} {counters[level]}" for level in AlertLevel if counters[level])
            or "Segnalazioni: nessuna")
        lines.append(f"Layer prodotti: {len(report.layers)}")
        return "\n".join(lines)


def layers_of(report: AnalysisReport) -> List[LayerRef]:
    """Return the layers produced by an analysis, de-duplicated by URI."""
    seen = set()
    unique: List[LayerRef] = []
    for layer in report.layers:
        if layer.uri in seen:
            continue
        seen.add(layer.uri)
        unique.append(layer)
    return unique
