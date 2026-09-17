"""Area data download: "give me every dataset for this area, organised".

The output is one GeoPackage per project area with one layer per source, named by category,
clipped to the area (plus a configurable buffer) and reprojected to the work CRS - the
manual sequence of *add service, filter by bbox, download, clip, rename, group* that this
plugin exists to remove.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

from ..core import crs as crs_utils
from ..core import geometry as geom_utils
from ..core import log, settings
from ..core.cache import CacheManager
from ..core.errors import SourceError, UserCancelled
from ..core.feedback import ChildFeedback, Feedback, NullFeedback
from ..core.models import AdminUnits, LayerRef, SourceResult, SourceStatus
from ..core.paths import area_dir
from ..core.project_area import ProjectArea
from ..core.registry import DataSource, DataSourceRegistry
from ..core.taxonomy import Taxonomy
from ..services import fetcher, vector_io
from ..services.http import HttpClient

DATA_GPKG = "data.gpkg"


@dataclass
class DownloadOutcome:
    """Result of a Download Area run."""

    layers: List[LayerRef] = field(default_factory=list)
    results: List[SourceResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    gpkg_path: str = ""

    @property
    def feature_total(self) -> int:
        """Total number of downloaded features."""
        return sum(result.feature_count for result in self.results)


class DownloadManager:
    """Downloads the vector datasets available for an area, by category."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http
        self.cache = cache

    # ------------------------------------------------------------------ planning

    def available(self, admin: AdminUnits,
                  categories: Optional[Sequence[str]] = None) -> Dict[str, List[DataSource]]:
        """Return the downloadable sources grouped by category."""
        wanted = list(categories or settings.get("download.categories", []))
        sources = self.registry.resolve_for(admin, categories=wanted or None)
        # The registry already widened the request to the sub-categories; widen the local
        # filter the same way or the sub-categories would be dropped again here.
        allowed = set(Taxonomy.instance().expand(wanted)) if wanted else set()
        grouped: Dict[str, List[DataSource]] = {}
        for source in sources:
            if not fetcher.supports_features(source):
                continue
            if allowed and source.category not in allowed:
                continue
            grouped.setdefault(source.category, []).append(source)
        return grouped

    def category_labels(self, language: str = "it") -> Dict[str, str]:
        """Return human labels for the download categories."""
        taxonomy = Taxonomy.instance()
        return {category.id: category.label(language) for category in taxonomy.all()}

    # ------------------------------------------------------------------ run

    def download(self, area: ProjectArea, categories: Optional[Sequence[str]] = None, *,
                 out_path: Optional[Path] = None, clip: Optional[bool] = None,
                 buffer_m: Optional[float] = None, refresh: bool = False,
                 feedback: Optional[Feedback] = None) -> DownloadOutcome:
        """Download every dataset of the requested categories for ``area``."""
        feedback = feedback or NullFeedback()
        outcome = DownloadOutcome()
        plan = self.available(area.admin, categories)
        sources = [source for group in plan.values() for source in group]
        if not sources:
            outcome.warnings.append("Nessuna sorgente scaricabile per le categorie richieste.")
            return outcome

        gpkg = Path(out_path) if out_path else Path(area_dir(area.id)) / DATA_GPKG
        outcome.gpkg_path = str(gpkg)
        do_clip = bool(settings.get("download.clip_to_area", True) if clip is None else clip)
        buffer = float(settings.get("download.buffer_m", 250) if buffer_m is None else buffer_m)

        total = len(sources)
        for index, source in enumerate(sources):
            if feedback.is_canceled():
                raise UserCancelled()
            child = ChildFeedback(feedback, 100.0 * index / total, 100.0 * (index + 1) / total,
                                  prefix=f"[{source.name}] ")
            feedback.set_step(f"Download {source.name} ({index + 1}/{total})")
            outcome.results.append(
                self._download_one(area, source, gpkg, outcome, child,
                                   clip=do_clip, buffer_m=buffer, refresh=refresh))
        return outcome

    # ------------------------------------------------------------------ one source

    def _download_one(self, area: ProjectArea, source: DataSource, gpkg: Path,
                      outcome: DownloadOutcome, feedback: Feedback, *,
                      clip: bool, buffer_m: float, refresh: bool) -> SourceResult:
        started = time.monotonic()
        try:
            fetched = fetcher.fetch_features(
                source, area.context_bbox(area.crs, buffer_m=buffer_m), area.crs,
                target_crs=area.work_crs, area_id=area.id, refresh=refresh,
                feedback=feedback, http=self.http, cache=self.cache)
        except UserCancelled:
            raise
        except SourceError as exc:
            feedback.push_warning(f"{source.name}: {exc}")
            outcome.warnings.append(f"{source.name}: {exc}")
            return self._failed(source, str(exc), started)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception(f"Download failed for {source.id}", exc)
            outcome.warnings.append(f"{source.name}: {type(exc).__name__}: {exc}")
            return self._failed(source, f"{type(exc).__name__}: {exc}", started)

        layer = fetched.layer(source.name)
        if layer is None or fetched.feature_count == 0:
            return SourceResult(source_id=source.id, source_name=source.name,
                                category=source.category, status=SourceStatus.EMPTY,
                                provenance=source.provenance(operation="download"),
                                elapsed_ms=int((time.monotonic() - started) * 1000))
        if clip:
            layer = self._clip(area, layer, buffer_m)
        layer_name = f"{source.category}_{vector_io.safe_layer_name(source.id)}"[:60]
        try:
            materialised = vector_io.write_layer(layer, gpkg, layer_name,
                                                 target_crs=area.work_crs, append=True)
        except Exception as exc:  # pragma: no cover - disk issues
            outcome.warnings.append(f"{source.name}: scrittura non riuscita ({exc})")
            return self._failed(source, str(exc), started)
        outcome.layers.append(LayerRef(name=source.name, uri=materialised.uri,
                                       category=source.category,
                                       group=source.group or "Data", style=source.style,
                                       source_id=source.id))
        if fetched.truncated:
            outcome.warnings.append(f"{source.name}: risultato troncato al limite configurato.")
        return SourceResult(
            source_id=source.id, source_name=source.name, category=source.category,
            status=SourceStatus.CACHED if fetched.from_cache else SourceStatus.ONLINE,
            present=materialised.feature_count > 0, feature_count=materialised.feature_count,
            layer_uri=materialised.uri, layer_name=source.name,
            provenance=source.provenance(operation="download", crs=area.work_crs.authid()),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            from_cache=fetched.from_cache)

    @staticmethod
    def _failed(source: DataSource, error: str, started: float) -> SourceResult:
        return SourceResult(source_id=source.id, source_name=source.name,
                            category=source.category, status=SourceStatus.OFFLINE,
                            error=error, provenance=source.provenance(operation="download"),
                            elapsed_ms=int((time.monotonic() - started) * 1000))

    @staticmethod
    def _clip(area: ProjectArea, layer: QgsVectorLayer, buffer_m: float) -> QgsVectorLayer:
        """Clip a layer on the (buffered) project area, keeping its schema."""
        clip_geom = area.buffered_geometry(buffer_m) if buffer_m else area.geometry
        clip_geom = crs_utils.transform_geometry(clip_geom, area.crs, layer.crs())
        try:
            clip_geom = geom_utils.ensure_valid(clip_geom, context="clip mask")
        except Exception:  # pragma: no cover - defensive
            return layer
        clipped = vector_io.memory_layer(vector_io.geometry_type_name(layer), layer.crs(),
                                         layer.name(), layer.fields())
        provider = clipped.dataProvider()
        kept: List[QgsFeature] = []
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty() or not geometry.intersects(clip_geom):
                continue
            new_feature = QgsFeature(feature)
            piece = geom_utils.intersection(geometry, clip_geom)
            if piece.isEmpty():
                continue
            new_feature.setGeometry(QgsGeometry(piece))
            kept.append(new_feature)
        if kept:
            provider.addFeatures(kept)
        clipped.updateExtents()
        return clipped
