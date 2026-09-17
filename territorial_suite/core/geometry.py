"""Geometry utilities: validity, clipping, buffering and shape metrics.

Everything here is defensive: real-world public datasets contain self-intersections,
duplicate nodes and mixed dimensionality, and a single bad polygon must never abort an
analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional

from qgis.core import Qgis, QgsGeometry, QgsPointXY, QgsRectangle

from . import log, measure
from .crs import CrsLike, auto_work_crs, crs_from, transform_geometry
from .errors import GeometryError


def ensure_valid(geometry: QgsGeometry, *, context: str = "geometry") -> QgsGeometry:
    """Return a GEOS-valid copy of ``geometry``, repairing it when needed."""
    if geometry is None or geometry.isNull():
        raise GeometryError(f"Empty {context}")
    if geometry.isGeosValid():
        return QgsGeometry(geometry)
    repaired = geometry.makeValid()
    if repaired is None or repaired.isNull() or not repaired.isGeosValid():
        repaired = QgsGeometry(geometry)
        repaired.removeDuplicateNodes()
        repaired = repaired.buffer(0.0, 8)
    if repaired is None or repaired.isNull() or not repaired.isGeosValid():
        raise GeometryError(f"Invalid {context} could not be repaired")
    log.debug(f"Repaired invalid {context}")
    return repaired


def keep_polygons(geometry: QgsGeometry) -> QgsGeometry:
    """Drop non-polygonal parts from a possibly heterogeneous geometry collection."""
    if geometry is None or geometry.isEmpty():
        return QgsGeometry()
    if geometry.type() == Qgis.GeometryType.Polygon:
        return QgsGeometry(geometry)
    parts = [QgsGeometry(part) for part in geometry.asGeometryCollection()
             if part.type() == Qgis.GeometryType.Polygon]
    if not parts:
        return QgsGeometry()
    return QgsGeometry.unaryUnion(parts)


def union(geometries: Iterable[QgsGeometry]) -> QgsGeometry:
    """Union a set of geometries, skipping invalid ones."""
    valid: List[QgsGeometry] = []
    for geom in geometries:
        if geom is None or geom.isEmpty():
            continue
        try:
            valid.append(ensure_valid(geom))
        except GeometryError:
            continue
    if not valid:
        return QgsGeometry()
    if len(valid) == 1:
        return valid[0]
    return QgsGeometry.unaryUnion(valid)


def intersection(geometry_a: QgsGeometry, geometry_b: QgsGeometry) -> QgsGeometry:
    """Return the intersection of two geometries, repairing inputs if necessary."""
    if geometry_a is None or geometry_b is None:
        return QgsGeometry()
    if not geometry_a.boundingBoxIntersects(geometry_b):
        return QgsGeometry()
    try:
        result = geometry_a.intersection(geometry_b)
    except Exception:  # pragma: no cover - GEOS failure
        result = None
    if result is None or result.isNull():
        result = ensure_valid(geometry_a).intersection(ensure_valid(geometry_b))
    return result if result is not None and not result.isNull() else QgsGeometry()


def buffer_metres(geometry: QgsGeometry, crs: CrsLike, distance_m: float,
                  *, segments: int = 12) -> QgsGeometry:
    """Buffer a geometry by a distance in metres, whatever its CRS.

    The geometry is temporarily reprojected to a metric CRS so that the distance is real.
    """
    if geometry is None or geometry.isEmpty():
        return QgsGeometry()
    if not distance_m:
        return QgsGeometry(geometry)
    source = crs_from(crs)
    work = auto_work_crs(geometry, source)
    if work == source:
        return geometry.buffer(float(distance_m), segments)
    projected = transform_geometry(geometry, source, work)
    buffered = projected.buffer(float(distance_m), segments)
    return transform_geometry(buffered, work, source)


def centroid(geometry: QgsGeometry) -> Optional[QgsPointXY]:
    """Return the centroid, or a representative inner point when the centroid falls out."""
    if geometry is None or geometry.isEmpty():
        return None
    point = geometry.centroid()
    if point is None or point.isEmpty():
        return None
    if geometry.type() == Qgis.GeometryType.Polygon and not geometry.contains(point):
        inner = geometry.pointOnSurface()
        if inner is not None and not inner.isEmpty():
            return inner.asPoint()
    return point.asPoint()


def representative_point(geometry: QgsGeometry) -> Optional[QgsPointXY]:
    """Return a point guaranteed to lie inside the geometry."""
    if geometry is None or geometry.isEmpty():
        return None
    inner = geometry.pointOnSurface()
    return inner.asPoint() if inner is not None and not inner.isEmpty() else None


def bbox_dict(rect: QgsRectangle) -> Dict[str, float]:
    """Return a bounding box as a JSON-friendly dictionary."""
    return {
        "min_x": rect.xMinimum(),
        "min_y": rect.yMinimum(),
        "max_x": rect.xMaximum(),
        "max_y": rect.yMaximum(),
        "width": rect.width(),
        "height": rect.height(),
    }


@dataclass(frozen=True)
class ShapeMetrics:
    """Geometric descriptors of a project area."""

    area_m2: float
    perimeter_m: float
    compactness: float            # Polsby-Popper: 4*pi*A / P^2 (1 = circle)
    elongation: float             # oriented bounding box: short side / long side (1 = square)
    convexity: float              # area / convex hull area (1 = convex)
    equivalent_circle_radius_m: float
    obb_width_m: float
    obb_height_m: float
    obb_angle_deg: float
    part_count: int
    vertex_count: int

    def as_dict(self) -> Dict[str, float]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)


def shape_metrics(geometry: QgsGeometry, crs: CrsLike) -> ShapeMetrics:
    """Compute shape descriptors using ellipsoidal area/perimeter and a metric CRS.

    The oriented bounding box is computed in the metric work CRS, so its width and height
    are real metres rather than degrees.
    """
    import math

    if geometry is None or geometry.isEmpty():
        raise GeometryError("Cannot compute shape metrics of an empty geometry")

    source = crs_from(crs)
    area = measure.area_m2(geometry, source)
    perimeter = measure.length_m(geometry, source)

    work = auto_work_crs(geometry, source)
    projected = transform_geometry(geometry, source, work) if work != source else QgsGeometry(geometry)

    obb_width = obb_height = obb_angle = 0.0
    try:
        result = projected.orientedMinimumBoundingBox()
        if isinstance(result, (tuple, list)) and len(result) >= 5:
            _, _, obb_angle, obb_width, obb_height = result[:5]
    except Exception:  # pragma: no cover - GEOS failure
        log.debug("Oriented minimum bounding box unavailable; falling back to bbox")
        rect = projected.boundingBox()
        obb_width, obb_height = rect.width(), rect.height()

    hull = projected.convexHull()
    hull_area = hull.area() if hull is not None and not hull.isEmpty() else 0.0
    planar_area = projected.area()

    long_side = max(obb_width, obb_height)
    short_side = min(obb_width, obb_height)

    return ShapeMetrics(
        area_m2=area,
        perimeter_m=perimeter,
        compactness=(4.0 * math.pi * area / (perimeter ** 2)) if perimeter else 0.0,
        elongation=(short_side / long_side) if long_side else 0.0,
        convexity=(planar_area / hull_area) if hull_area else 0.0,
        equivalent_circle_radius_m=math.sqrt(area / math.pi) if area > 0 else 0.0,
        obb_width_m=float(obb_width),
        obb_height_m=float(obb_height),
        obb_angle_deg=float(obb_angle),
        part_count=len(geometry.asGeometryCollection()) if geometry.isMultipart() else 1,
        vertex_count=sum(1 for _ in geometry.vertices()),
    )
