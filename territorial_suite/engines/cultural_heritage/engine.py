"""The Cultural Heritage / MiC engine.

It coordinates, in this order:

    sources -> acquisition -> normalisation -> classification -> deduplication
            -> superintendency -> data quality -> outcome

**Acquisition and spatial measurement are not reimplemented here.** The features are
downloaded and measured by :class:`~territorial_suite.engines.constraints.ConstraintEngine`,
exactly like every other category, so a cultural source benefits from the same caching,
paging, throttling, cancellation, provenance and failure policy as the rest of the
catalogue. What this module adds is everything that is specific to heritage data: reading
the administrative act a feature declares, keeping the competent office separate from the
owner of the building, merging the same asset published by two services without losing
either provenance, and saying precisely why a theme came back empty.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence

from ...core import log, settings
from ...core.cache import CacheManager
from ...core.feedback import ChildFeedback, Feedback, NullFeedback
from ...core.models import AnalysisReport, SourceResult
from ...core.project_area import ProjectArea
from ...core.registry import DataSource, DataSourceRegistry
from ...core.taxonomy import Taxonomy
from ...services.http import HttpClient
from ..constraints import ConstraintEngine
from .classifier import CULTURAL_CATEGORIES, ROOT_CATEGORY, CulturalHeritageClassifier
from .deduplicator import CulturalHeritageDeduplicator
from .model import (
    CulturalAsset,
    CulturalHeritageOutcome,
    DataGap,
    Superintendency,
    ThemeOutcome,
)
from .normalizer import CulturalHeritageNormalizer, superintendency_fields
from .quality import CulturalHeritageQuality

#: Module key under which the outcome is attached to the analysis report.
MODULE_KEY = "cultural_heritage"

#: Category whose sources describe *who is competent*, not *what is protected*.
ADMINISTRATION_CATEGORY = "heritage_administration"


class CulturalHeritageEngine:
    """Turns the cultural sources of the catalogue into a structured outcome."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None,
                 taxonomy: Optional[Taxonomy] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http
        self.cache = cache
        self.taxonomy = taxonomy or Taxonomy.instance()
        self.classifier = CulturalHeritageClassifier(self.taxonomy)

    # ------------------------------------------------------------------ planning

    def categories(self) -> List[str]:
        """Sub-categories handled by this module."""
        return list(CULTURAL_CATEGORIES)

    def plan(self, area: ProjectArea) -> List[DataSource]:
        """Operational cultural sources relevant to the area, best quality first."""
        sources = self.registry.resolve_for(area.admin, categories=[ROOT_CATEGORY])
        from ...services import fetcher

        usable = [s for s in sources if fetcher.supports_features(s)]
        return sorted(usable, key=lambda s: (-s.quality.score, s.priority, s.name.lower()))

    def catalogued(self) -> List[DataSource]:
        """Cultural datasets the plugin knows of and cannot query."""
        return self.registry.catalogued_only(categories=[ROOT_CATEGORY])

    # ------------------------------------------------------------------ run

    def run(self, area: ProjectArea, *, feedback: Optional[Feedback] = None,
            refresh: bool = False, store_layers: bool = True,
            results: Optional[Sequence[SourceResult]] = None
            ) -> CulturalHeritageOutcome:
        """Analyse the cultural and landscape heritage around ``area``.

        ``results`` lets the orchestrator hand over the results the constraint step has
        already produced, so a full analysis never queries the same service twice.
        """
        feedback = feedback or NullFeedback()
        started = time.monotonic()
        outcome = CulturalHeritageOutcome()
        plan = self.plan(area)
        if not plan:
            outcome.warnings.append(
                "Nessuna fonte operativa per il patrimonio culturale e paesaggistico "
                "in quest'area.")

        by_source = {result.source_id: result for result in (results or [])}
        missing = [source for source in plan if source.id not in by_source]
        if missing:
            feedback.set_step("Patrimonio culturale: interrogazione fonti")
            engine = ConstraintEngine(registry=self.registry, http=self.http,
                                      cache=self.cache)
            child = ChildFeedback(feedback, 0.0, 70.0)
            acquired = engine.run(area, feedback=child, refresh=refresh,
                                  store_layers=store_layers, sources=missing)
            for result in acquired.results:
                by_source[result.source_id] = result
            outcome.warnings.extend(acquired.warnings)
            self._acquired_layers = list(acquired.layers)
        else:
            self._acquired_layers = []

        feedback.set_step("Patrimonio culturale: normalizzazione e classificazione")
        outcome.superintendencies = self._superintendencies(plan, by_source)
        outcome.themes = self._themes(area, plan, by_source, outcome)
        outcome.gap_summary = self._gap_summary(outcome.themes)
        outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
        feedback.set_progress(100)
        return outcome

    @property
    def layers(self) -> List:
        """Layers materialised by the last :meth:`run` (empty when results were reused)."""
        return list(getattr(self, "_acquired_layers", []))

    # ------------------------------------------------------------------ pieces

    def _superintendencies(self, plan: Sequence[DataSource],
                           results: Dict[str, SourceResult]) -> List[Superintendency]:
        """Determine the competent offices - only from the official layer.

        The area may straddle a boundary, in which case every office found is reported.
        When the layer has nothing to say, the answer stays ``unknown``: the office is
        never deduced from the region or the province, because the territorial competence
        of the Soprintendenze does not follow administrative boundaries.
        """
        offices: List[Superintendency] = []
        seen = set()
        for source in plan:
            if source.category != ADMINISTRATION_CATEGORY:
                continue
            result = results.get(source.id)
            if result is None or not result.ok:
                continue
            for hit in result.hits:
                fields = superintendency_fields(source, hit.attributes or {})
                if not fields:
                    continue
                key = fields.get("code") or fields.get("name")
                if key in seen:
                    continue
                seen.add(key)
                offices.append(Superintendency(determined_by="official_layer", **fields))
        return offices

    def _themes(self, area: ProjectArea, plan: Sequence[DataSource],
                results: Dict[str, SourceResult],
                outcome: CulturalHeritageOutcome) -> List[ThemeOutcome]:
        """Build one outcome per sub-category."""
        quality = CulturalHeritageQuality(area.admin)
        dedup = CulturalHeritageDeduplicator(
            enabled=bool(settings.get("cultural_heritage.deduplicate", True)))
        grouped = self.classifier.group_by_category(plan)
        catalogued = self.catalogued()
        themes: List[ThemeOutcome] = []
        merged = repeated = 0
        repeating: List[str] = []

        for category in self.classifier.categories():
            sources = grouped.get(category, [])
            theme = ThemeOutcome(category=category, label=self.classifier.label(category))
            theme.sources_catalogued = [s.id for s in catalogued
                                        if s.category == category]
            assets: List[CulturalAsset] = []
            for source in sources:
                result = results.get(source.id)
                theme.sources_queried.append(source.id)
                if result is None or not result.ok:
                    theme.sources_failed.append(source.id)
                    continue
                normalizer = CulturalHeritageNormalizer(source)
                assets.extend(normalizer.assets_from_result(result, category=category))
            if category != ADMINISTRATION_CATEGORY:
                theme.assets = dedup.run(assets)
                merged += dedup.merged_count
                repeated += dedup.repeated_count
                for source_id in dedup.repeating_sources:
                    if source_id not in repeating:
                        repeating.append(source_id)
            else:
                # Offices are reported on their own, not as heritage records.
                theme.assets = []
            # ``gaps`` answers one question only: *why is this theme empty?* A theme
            # that produced records has no gap, even when other datasets on the same
            # subject could not be queried - those are listed as catalogued sources and
            # reported separately, so a full theme never looks half-broken.
            if not theme.assets and category != ADMINISTRATION_CATEGORY:
                if not sources:
                    theme.gaps = [DataGap.NO_DATA.value]
                else:
                    gaps = quality.assess(sources, results)
                    theme.gaps = sorted({gap.value for gap in gaps.values()})
                if theme.sources_catalogued and                         DataGap.SOURCE_NOT_VERIFIED.value not in theme.gaps:
                    theme.gaps.append(DataGap.SOURCE_NOT_VERIFIED.value)
            theme.coverage_notes = quality.coverage_notes(sources)
            themes.append(theme)
        if merged:
            log.info(f"Cultural heritage: {merged} records merged across sources")
        if repeated:
            names = ", ".join(sorted(
                self.registry.get(s).name if self.registry.get(s) else s
                for s in repeating))
            outcome.warnings.append(
                f"{repeated} record duplicati esattamente dalla stessa fonte sono stati "
                f"scartati ({names}): il servizio pubblica piu' volte lo stesso elemento. "
                f"I conteggi riportati sono quelli depurati.")
        return themes

    @staticmethod
    def _gap_summary(themes: Sequence[ThemeOutcome]) -> Dict[str, List[str]]:
        """``{gap: [theme label, ...]}`` for the data-quality section."""
        summary: Dict[str, List[str]] = {}
        for theme in themes:
            for gap in theme.gaps:
                summary.setdefault(gap, []).append(theme.label or theme.category)
        return summary

    # ------------------------------------------------------------------ report

    def attach(self, report: AnalysisReport, outcome: CulturalHeritageOutcome) -> None:
        """Store the outcome on the analysis report."""
        report.modules[MODULE_KEY] = outcome.as_dict()


def outcome_of(report: AnalysisReport) -> CulturalHeritageOutcome:
    """Read back the module payload of a report (empty when the step did not run)."""
    return CulturalHeritageOutcome.from_dict(report.module(MODULE_KEY))
