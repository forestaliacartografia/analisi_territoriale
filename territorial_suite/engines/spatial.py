"""Shared spatial measurement used by every analysis engine.

Given a project area and a downloaded layer, produce the numbers the dossier needs:
presence, intersected surface, percentage of the area, minimum distance, and a per-feature
breakdown - all measured on the ellipsoid, in a metric work CRS.

Two details matter for correctness:

* the **total** intersected surface is computed on the *union* of the individual
  intersections, otherwise overlapping features (very common in constraint datasets) would
  inflate the percentage beyond 100 %;
* features that do not intersect are still reported, with their distance, up to the
  configured search radius - that is what makes proximity checks possible in one pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from qgis.core import Qgis, QgsFeature, QgsFeatureRequest, QgsGeometry, QgsVectorLayer

from ..core import crs as crs_utils
from ..core import geometry as geom_utils
from ..core import log, measure, settings
from ..core.errors import GeometryError
from ..core.feedback import Feedback, NullFeedback
from ..core.models import FeatureHit, SourceResult, SourceStatus
from ..core.project_area import ProjectArea
from ..core.registry import DataSource

#: Attributes never copied into a hit (provider bookkeeping).
_SKIP_FIELDS = {"fid", "gml_id", "objectid", "shape_length", "shape_area", "shape__length",
                "shape__area"}


@dataclass
class Intersection:
    """Aggregated geometric relationship between an area and a dataset."""

    present: bool = False
    feature_count: int = 0
    intersect_area_m2: float = 0.0
    intersect_pct: float = 0.0
    min_distance_m: Optional[float] = None
    hits: List[FeatureHit] = None
    geometry_wkt: str = ""

    def __post_init__(self) -> None:
        if self.hits is None:
            self.hits = []


def _feature_attributes(feature: QgsFeature, layer: QgsVectorLayer, *,
                        max_fields: int = 25) -> Dict[str, Any]:
    """Return a JSON-friendly attribute dictionary for a feature."""
    attributes: Dict[str, Any] = {}
    for field, value in zip(layer.fields(), feature.attributes()):
        name = field.name()
        if name.lower() in _SKIP_FIELDS:
            continue
        if value is None or (hasattr(value, "isNull") and value.isNull()):
            continue
        attributes[name] = value if isinstance(value, (int, float, bool)) else str(value)
        if len(attributes) >= max_fields:
            break
    return attributes


def analyse_layer(area: ProjectArea, layer: QgsVectorLayer, *,
                  source: Optional[DataSource] = None,
                  max_distance_m: Optional[float] = None,
                  keep_geometry: Optional[bool] = None,
                  max_hits: int = 500,
                  feedback: Optional[Feedback] = None) -> Intersection:
    """Measure how a downloaded layer relates to the project area.

    :param area: the project area.
    :param layer: a layer whose CRS is known (it is reprojected internally when needed).
    :param source: descriptor used to pick the human label of each feature.
    :param max_distance_m: ignore non-intersecting features farther than this.
    :param keep_geometry: store the intersected geometry as WKT in the result.
    """
    feedback = feedback or NullFeedback()
    if layer is None or not layer.isValid():
        return Intersection()

    work_crs = area.work_crs
    area_geom = area.geometry_in(work_crs)
    try:
        area_geom = geom_utils.ensure_valid(area_geom, context="project area")
    except GeometryError:
        log.warning("Project area geometry could not be repaired for analysis")
        return Intersection()
    area_m2 = area.area_m2

    radius = float(settings.get("analysis.max_distance_m", 5000)
                   if max_distance_m is None else max_distance_m)
    store_geometry = bool(settings.get("analysis.keep_intersection_geometry", True)
                          if keep_geometry is None else keep_geometry)

    needs_transform = layer.crs() != work_crs
    search_geom = area.buffered_geometry(radius) if radius > 0 else area.geometry
    search_rect = crs_utils.transform_bbox(search_geom.boundingBox(), area.crs, layer.crs())
    request = QgsFeatureRequest().setFilterRect(search_rect)

    hits: List[FeatureHit] = []
    intersections: List[QgsGeometry] = []
    min_distance: Optional[float] = None
    count = 0
    is_polygonal = layer.geometryType() == Qgis.GeometryType.Polygon

    for feature in layer.getFeatures(request):
        if feedback.is_canceled():
            break
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        if needs_transform:
            try:
                geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
            except Exception as exc:  # pragma: no cover - broken feature
                log.debug(f"analyse_layer: elemento saltato ({type(exc).__name__}: {exc})")
                continue
        try:
            geometry = geom_utils.ensure_valid(geometry, context="feature")
        except GeometryError:
            continue

        attributes = _feature_attributes(feature, layer)
        label = source.label_for(attributes) if source is not None else ""
        hit = FeatureHit(fid=str(feature.id()), label=label, attributes=attributes)

        if geometry.intersects(area_geom):
            count += 1
            hit.distance_m = 0.0
            if is_polygonal:
                clipped = geom_utils.intersection(geometry, area_geom)
                if not clipped.isEmpty():
                    intersections.append(clipped)
                    hit.intersect_area_m2 = measure.area_m2(clipped, work_crs)
                    hit.intersect_pct = measure.percentage(hit.intersect_area_m2, area_m2)
                    if store_geometry:
                        hit.geometry_wkt = clipped.asWkt(6)
            elif store_geometry:
                hit.geometry_wkt = geometry.asWkt(6)
            min_distance = 0.0
        else:
            distance = measure.distance_m(geometry, area_geom, work_crs)
            if radius and distance > radius:
                continue
            hit.distance_m = distance
            min_distance = distance if min_distance is None else min(min_distance, distance)
        if len(hits) < max_hits:
            hits.append(hit)

    total_area = 0.0
    union_wkt = ""
    if intersections:
        merged = geom_utils.union(intersections)
        total_area = measure.area_m2(merged, work_crs)
        if store_geometry:
            union_wkt = merged.asWkt(6)

    return Intersection(
        present=count > 0,
        feature_count=count,
        intersect_area_m2=total_area,
        intersect_pct=measure.percentage(total_area, area_m2),
        min_distance_m=min_distance,
        hits=sorted(hits, key=lambda h: (h.distance_m, -h.intersect_area_m2)),
        geometry_wkt=union_wkt,
    )


def result_from_intersection(source: DataSource, intersection: Intersection, *,
                             status: SourceStatus = SourceStatus.ONLINE,
                             layer_uri: str = "", layer_name: str = "",
                             elapsed_ms: int = 0, from_cache: bool = False,
                             crs: str = "") -> SourceResult:
    """Wrap an :class:`Intersection` into the serialisable result object."""
    effective_status = status
    if status == SourceStatus.ONLINE and intersection.feature_count == 0 \
            and not intersection.hits:
        effective_status = SourceStatus.EMPTY
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        category=source.category,
        status=effective_status,
        present=intersection.present,
        feature_count=intersection.feature_count,
        intersect_area_m2=intersection.intersect_area_m2,
        intersect_pct=intersection.intersect_pct,
        min_distance_m=intersection.min_distance_m,
        hits=intersection.hits,
        layer_uri=layer_uri,
        layer_name=layer_name or source.name,
        provenance=source.provenance(operation="spatial analysis", crs=crs),
        elapsed_ms=elapsed_ms,
        from_cache=from_cache,
    )


def failed_result(source: DataSource, error: str, *,
                  status: SourceStatus = SourceStatus.OFFLINE,
                  elapsed_ms: int = 0) -> SourceResult:
    """Build the result recorded when a source could not be queried."""
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        category=source.category,
        status=status,
        present=False,
        error=error,
        provenance=source.provenance(operation="failed request"),
        elapsed_ms=elapsed_ms,
    )
