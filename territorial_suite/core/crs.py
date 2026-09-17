"""Coordinate reference system helpers.

Golden rule of the plugin: **no metric computation in degrees**. Every measurement runs
either on an ellipsoid (:mod:`core.measure`) or in a projected metric CRS chosen here.
"""

from __future__ import annotations

import math
from typing import Optional, Union

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

from . import log
from .errors import CrsError

WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"
#: ETRF2000 geographic - the CRS used by the Italian INSPIRE cadastral service.
ETRF2000_GEO = "EPSG:6706"
#: ETRS89 / UTM zone 32N and 33N cover mainland Italy.
UTM32N_ETRS89 = "EPSG:25832"
UTM33N_ETRS89 = "EPSG:25833"

CrsLike = Union[str, QgsCoordinateReferenceSystem]


def crs_from(value: CrsLike) -> QgsCoordinateReferenceSystem:
    """Return a valid :class:`QgsCoordinateReferenceSystem` or raise :class:`CrsError`."""
    if isinstance(value, QgsCoordinateReferenceSystem):
        crs = value
    else:
        crs = QgsCoordinateReferenceSystem(str(value))
    if not crs.isValid():
        raise CrsError(f"Unknown CRS: {value}")
    return crs


def transform_context() -> QgsCoordinateTransformContext:
    """Return the project transform context (datum shifts), or an empty one."""
    try:
        return QgsProject.instance().transformContext()
    except Exception:  # pragma: no cover - outside QGIS app
        return QgsCoordinateTransformContext()


def is_metric(crs: CrsLike) -> bool:
    """Return ``True`` when the CRS uses linear (metre-like) map units."""
    from qgis.core import Qgis

    resolved = crs_from(crs)
    return not resolved.isGeographic() and resolved.mapUnits() in (
        Qgis.DistanceUnit.Meters,
        Qgis.DistanceUnit.Kilometers,
        Qgis.DistanceUnit.Feet,
    )


def utm_epsg_for(longitude: float, latitude: float) -> str:
    """Return the WGS84/UTM EPSG code covering a geographic coordinate."""
    if not (-180.0 <= longitude <= 180.0) or not (-90.0 <= latitude <= 90.0):
        raise CrsError(f"Coordinates out of range: {longitude}, {latitude}")
    zone = int(math.floor((longitude + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return f"EPSG:{32600 + zone if latitude >= 0 else 32700 + zone}"


def auto_work_crs(geometry: QgsGeometry, crs: CrsLike) -> QgsCoordinateReferenceSystem:
    """Pick the metric CRS used for buffers, clips and geometric operations.

    A projected metric CRS is kept as is; a geographic CRS is replaced by the UTM zone of
    the geometry centroid. Ellipsoidal measurements are unaffected by this choice.
    """
    source = crs_from(crs)
    if is_metric(source):
        return source
    centroid = geometry.centroid().asPoint() if not geometry.isEmpty() else QgsPointXY(0.0, 0.0)
    if not source.isGeographic():  # projected but not metric: normalise through WGS84
        centroid = transform_point(centroid, source, crs_from(WGS84))
    elif source.authid() != WGS84:
        centroid = transform_point(centroid, source, crs_from(WGS84))
    return crs_from(utm_epsg_for(centroid.x(), centroid.y()))


def resolve_work_crs(geometry: QgsGeometry, crs: CrsLike, mode: str = "auto_utm",
                     explicit: str = "") -> QgsCoordinateReferenceSystem:
    """Resolve the work CRS according to the user setting.

    ``mode`` is one of ``auto_utm`` (default), ``project`` or ``explicit``.
    """
    mode = (mode or "auto_utm").lower()
    if mode == "explicit" and explicit:
        try:
            candidate = crs_from(explicit)
            if is_metric(candidate):
                return candidate
            log.warning(f"Configured work CRS {explicit} is not metric; falling back to UTM")
        except CrsError:
            log.warning(f"Configured work CRS {explicit} is invalid; falling back to UTM")
    if mode == "project":
        try:
            project_crs = QgsProject.instance().crs()
            if project_crs.isValid() and is_metric(project_crs):
                return project_crs
        except Exception as exc:  # pragma: no cover - outside QGIS app
            log.debug(f"resolve_work_crs: operazione non riuscita ({type(exc).__name__}: {exc})")
    return auto_work_crs(geometry, crs)


def make_transform(source: CrsLike, target: CrsLike) -> QgsCoordinateTransform:
    """Build a transform between two CRS, raising :class:`CrsError` when impossible."""
    src, dst = crs_from(source), crs_from(target)
    transform = QgsCoordinateTransform(src, dst, transform_context())
    if not transform.isValid():
        raise CrsError(f"No transform available from {src.authid()} to {dst.authid()}")
    return transform


def transform_geometry(geometry: QgsGeometry, source: CrsLike, target: CrsLike) -> QgsGeometry:
    """Return a copy of ``geometry`` reprojected from ``source`` to ``target``."""
    src, dst = crs_from(source), crs_from(target)
    if src == dst:
        return QgsGeometry(geometry)
    clone = QgsGeometry(geometry)
    result = clone.transform(make_transform(src, dst))
    if result != 0:
        raise CrsError(f"Failed to reproject geometry from {src.authid()} to {dst.authid()}")
    return clone


def transform_point(point: QgsPointXY, source: CrsLike, target: CrsLike) -> QgsPointXY:
    """Reproject a single point."""
    src, dst = crs_from(source), crs_from(target)
    if src == dst:
        return QgsPointXY(point)
    return make_transform(src, dst).transform(point)


def transform_bbox(rect: QgsRectangle, source: CrsLike, target: CrsLike) -> QgsRectangle:
    """Reproject a bounding box (densified, so that curved edges stay inside)."""
    src, dst = crs_from(source), crs_from(target)
    if src == dst:
        return QgsRectangle(rect)
    return make_transform(src, dst).transformBoundingBox(rect)


def bbox_in(geometry: QgsGeometry, source: CrsLike, target: CrsLike) -> QgsRectangle:
    """Return the bounding box of ``geometry`` expressed in ``target``."""
    return transform_bbox(geometry.boundingBox(), source, target)


def bbox_precision(crs: CrsLike) -> int:
    """Return how many decimals a bounding box needs in this CRS.

    Six decimals of a degree are about 11 cm, two centimetres of a metric CRS: more than
    enough for a spatial filter. Sending more is not only useless, it is actively harmful -
    the Italian cadastral WFS rejects ``43.76900000`` (eight decimals, trailing zeros)
    while it accepts ``43.769000``, verified 2026-09-16.
    """
    return 6 if crs_from(crs).isGeographic() else 2


def format_ordinate(value: float, precision: int) -> str:
    """Format a coordinate with ``precision`` decimals, without trailing zeros."""
    text = f"{float(value):.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def describe(crs: CrsLike) -> str:
    """Return a short human description such as ``EPSG:32632 - WGS 84 / UTM zone 32N``."""
    resolved = crs_from(crs)
    authid = resolved.authid() or "custom"
    return f"{authid} - {resolved.description()}" if resolved.description() else authid


def axis_inverted(crs: CrsLike) -> bool:
    """Return ``True`` when the CRS declares latitude/longitude (northing first) order.

    OGC services that use ``urn:ogc:def:crs:EPSG::4326`` expect that order in BBOX values.
    """
    resolved = crs_from(crs)
    try:
        return bool(resolved.hasAxisInverted())
    except AttributeError:  # pragma: no cover - very old builds
        return resolved.isGeographic()


def urn(crs: CrsLike) -> str:
    """Return the OGC URN form of a CRS (``urn:ogc:def:crs:EPSG::4326``)."""
    resolved = crs_from(crs)
    authid = resolved.authid()
    if authid.startswith("EPSG:"):
        return f"urn:ogc:def:crs:EPSG::{authid.split(':', 1)[1]}"
    return authid


def optional_crs(value: Optional[str]) -> Optional[QgsCoordinateReferenceSystem]:
    """Return a CRS for a possibly empty string, or ``None``."""
    if not value:
        return None
    try:
        return crs_from(value)
    except CrsError:
        return None
