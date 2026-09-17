"""Constraint & sensitivity engine.

For every source relevant to the project area it answers, with numbers and provenance:
is the dataset present? does it intersect? how much surface and what share of the area?
how far is the nearest feature? which features exactly?

Failure policy (``docs/DESIGN.md`` section L): a source that errors out becomes a
``SourceResult`` with ``status = OFFLINE`` and the error text. The analysis continues.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from ..core import log, settings
from ..core.cache import CacheManager
from ..core.errors import SourceError, UserCancelled
from ..core.feedback import ChildFeedback, Feedback, NullFeedback
from ..core.models import AdminUnits, LayerRef, SourceResult, SourceStatus
from ..core.paths import area_dir
from ..core.project_area import ProjectArea
from ..core.registry import DataSource, DataSourceRegistry
from ..core.taxonomy import KNOWLEDGE, Taxonomy
from ..services import fetcher, vector_io
from ..services.http import HttpClient
from . import spatial

CONSTRAINTS_GPKG = "constraints.gpkg"


@dataclass
class ConstraintOutcome:
    """Everything the constraint engine produced for one project area."""

    results: List[SourceResult] = field(default_factory=list)
    layers: List[LayerRef] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def present(self) -> List[SourceResult]:
        """Results whose dataset interacts with the area."""
        return [result for result in self.results if result.present]

    @property
    def failed(self) -> List[SourceResult]:
        """Results of sources that could not be queried."""
        return [result for result in self.results if not result.ok]


class ConstraintEngine:
    """Queries every relevant source and measures its relationship with the area."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http
        self.cache = cache

    # ------------------------------------------------------------------ planning

    def plan(self, admin: AdminUnits, categories: Optional[Sequence[str]] = None,
             *, include_support: bool = False,
             exclude_categories: Optional[Sequence[str]] = None,
             exclude_source_ids: Optional[Sequence[str]] = None) -> List[DataSource]:
        """Return the sources to query for this administrative framing.

        ``exclude_categories`` lets the orchestrator skip what another step already did
        (typically the cadastre), so the same service is never queried twice in one run.
        """
        if categories is None:
            categories = list(settings.get("analysis.categories", []))
            if not categories:
                taxonomy = Taxonomy.instance()
                categories = [c.id for c in taxonomy.all()
                              if include_support or c.kind == KNOWLEDGE]
        skip_categories = {c for c in (exclude_categories or []) if c}
        skip_ids = {s for s in (exclude_source_ids or []) if s}
        categories = [c for c in categories if c not in skip_categories]
        sources = self.registry.resolve_for(admin, categories=categories)
        return [source for source in sources
                if fetcher.supports_features(source) and source.id not in skip_ids]

    # ------------------------------------------------------------------ run

    def run(self, area: ProjectArea, *, categories: Optional[Sequence[str]] = None,
            feedback: Optional[Feedback] = None, refresh: bool = False,
            store_layers: bool = True,
            exclude_categories: Optional[Sequence[str]] = None,
            exclude_source_ids: Optional[Sequence[str]] = None,
            sources: Optional[Sequence[DataSource]] = None) -> ConstraintOutcome:
        """Analyse every relevant source against ``area``."""
        feedback = feedback or NullFeedback()
        outcome = ConstraintOutcome()
        plan = list(sources) if sources is not None else self.plan(
            area.admin, categories, exclude_categories=exclude_categories,
            exclude_source_ids=exclude_source_ids)
        if not plan:
            outcome.warnings.append(
                "Nessuna sorgente disponibile per le categorie richieste in quest'area.")
            return outcome

        gpkg = Path(area_dir(area.id)) / CONSTRAINTS_GPKG
        total = len(plan)
        for index, source in enumerate(plan):
            if feedback.is_canceled():
                raise UserCancelled()
            start = 100.0 * index / total
            end = 100.0 * (index + 1) / total
            child = ChildFeedback(feedback, start, end, prefix=f"[{source.name}] ")
            feedback.set_step(f"{source.name} ({index + 1}/{total})")
            outcome.results.append(
                self._analyse_source(area, source, gpkg, outcome, child, refresh=refresh,
                                     store_layers=store_layers))
            feedback.set_progress(end)
        return outcome

    # ------------------------------------------------------------------ one source

    @staticmethod
    def search_radius() -> float:
        """Radius within which distances are measured.

        It can never exceed the context buffer: that is how much data is actually
        downloaded around the area, so a larger "maximum distance" would silently measure
        nothing. Raise ``analysis.context_buffer_m`` to look further.
        """
        buffer_m = float(settings.get("analysis.context_buffer_m", 1000))
        requested = float(settings.get("analysis.max_distance_m", 1000))
        if requested > buffer_m:
            log.debug(f"max_distance_m ({requested:g}) limitato al buffer di contesto "
                      f"({buffer_m:g})")
        return min(requested, buffer_m)

    def _analyse_source(self, area: ProjectArea, source: DataSource, gpkg: Path,
                        outcome: ConstraintOutcome, feedback: Feedback, *,
                        refresh: bool, store_layers: bool) -> SourceResult:
        started = time.monotonic()
        try:
            fetched = fetcher.fetch_features(
                source, area.context_bbox(area.crs), area.crs, target_crs=area.work_crs,
                area_id=area.id, refresh=refresh, feedback=feedback,
                http=self.http, cache=self.cache)
        except UserCancelled:
            raise
        except SourceError as exc:
            feedback.push_warning(f"{source.name}: {exc}")
            return spatial.failed_result(
                source, str(exc), elapsed_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception(f"Unexpected failure querying {source.id}", exc)
            return spatial.failed_result(
                source, f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - started) * 1000))

        if fetched.truncated:
            outcome.warnings.append(
                f"{source.name}: risultato troncato al limite di feature configurato.")

        layer = fetched.layer(source.name)
        if layer is None or fetched.feature_count == 0:
            return spatial.result_from_intersection(
                source, spatial.Intersection(), status=SourceStatus.EMPTY,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                from_cache=fetched.from_cache, crs=area.work_crs.authid())

        intersection = spatial.analyse_layer(
            area, layer, source=source, feedback=feedback,
            max_distance_m=self.search_radius(),
            max_hits=int(settings.get("analysis.max_hits_per_source", 500)))

        layer_uri = ""
        layer_name = source.name
        if store_layers and (intersection.present or intersection.hits):
            layer_uri = self._store_layer(source, layer, gpkg, outcome, area)
        return spatial.result_from_intersection(
            source, intersection,
            status=SourceStatus.CACHED if fetched.from_cache else SourceStatus.ONLINE,
            layer_uri=layer_uri, layer_name=layer_name,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            from_cache=fetched.from_cache, crs=area.work_crs.authid())

    def _store_layer(self, source: DataSource, layer, gpkg: Path,
                     outcome: ConstraintOutcome, area: ProjectArea) -> str:
        """Copy the downloaded dataset into the project-area GeoPackage."""
        layer_name = vector_io.safe_layer_name(source.id)
        try:
            materialised = vector_io.write_layer(layer, gpkg, layer_name,
                                                 target_crs=area.work_crs, append=True)
        except Exception as exc:  # pragma: no cover - disk/permission issues
            log.exception(f"Cannot store layer for {source.id}", exc)
            outcome.warnings.append(f"{source.name}: layer non salvato ({exc})")
            return ""
        outcome.layers.append(LayerRef(
            name=source.name, uri=materialised.uri, category=source.category,
            group=source.group or "Constraints", style=source.style, source_id=source.id))
        return materialised.uri
