"""Ellipsoidal measurements and human formatting.

All areas and lengths reported by the plugin are **ellipsoidal** (computed with
``QgsDistanceArea`` on the configured ellipsoid) and expressed in metres / square metres.
The ellipsoid actually used is recorded in the report so the number is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from qgis.core import Qgis, QgsDistanceArea, QgsGeometry

from . import settings
from .crs import CrsLike, crs_from, transform_context

SQUARE_METRE = "m2"
HECTARE = "ha"
SQUARE_KILOMETRE = "km2"


def distance_area(crs: CrsLike) -> QgsDistanceArea:
    """Return a :class:`QgsDistanceArea` configured for ``crs`` and the settings ellipsoid."""
    calculator = QgsDistanceArea()
    calculator.setSourceCrs(crs_from(crs), transform_context())
    ellipsoid = settings.get("general.ellipsoid", "EPSG:7030") or "EPSG:7030"
    calculator.setEllipsoid(ellipsoid)
    return calculator


def ellipsoid_name(crs: CrsLike) -> str:
    """Return the ellipsoid actually in use (for provenance and reports)."""
    return distance_area(crs).ellipsoid()


def area_m2(geometry: QgsGeometry, crs: CrsLike) -> float:
    """Return the ellipsoidal area of ``geometry`` in square metres."""
    if geometry is None or geometry.isEmpty():
        return 0.0
    calculator = distance_area(crs)
    raw = calculator.measureArea(geometry)
    return float(calculator.convertAreaMeasurement(raw, Qgis.AreaUnit.SquareMeters))


def length_m(geometry: QgsGeometry, crs: CrsLike) -> float:
    """Return the ellipsoidal length (or perimeter) of ``geometry`` in metres."""
    if geometry is None or geometry.isEmpty():
        return 0.0
    calculator = distance_area(crs)
    raw = calculator.measurePerimeter(geometry) if geometry.type() == Qgis.GeometryType.Polygon \
        else calculator.measureLength(geometry)
    return float(calculator.convertLengthMeasurement(raw, Qgis.DistanceUnit.Meters))


def distance_m(geometry_a: QgsGeometry, geometry_b: QgsGeometry, crs: CrsLike) -> float:
    """Return the shortest ellipsoidal distance between two geometries, in metres.

    The planar shortest line is computed first (fast, in the source CRS) and then measured
    ellipsoidally, which keeps the result correct even for geographic CRS.
    """
    if geometry_a is None or geometry_b is None or geometry_a.isEmpty() or geometry_b.isEmpty():
        return float("inf")
    if geometry_a.intersects(geometry_b):
        return 0.0
    line = geometry_a.shortestLine(geometry_b)
    if line is None or line.isEmpty():
        return float("inf")
    return length_m(line, crs)


@dataclass(frozen=True)
class AreaBreakdown:
    """Same area expressed in the three units used across the UI and the report."""

    m2: float
    ha: float
    km2: float

    def as_dict(self) -> Dict[str, float]:
        """Return a plain dictionary (JSON friendly)."""
        return {SQUARE_METRE: self.m2, HECTARE: self.ha, SQUARE_KILOMETRE: self.km2}


def breakdown(square_metres: float) -> AreaBreakdown:
    """Split an area in square metres into m2/ha/km2."""
    value = float(square_metres or 0.0)
    return AreaBreakdown(m2=value, ha=value / 10_000.0, km2=value / 1_000_000.0)


def format_area(square_metres: float, *, decimals: int = 2, unit: str = "auto") -> str:
    """Format an area for the UI, choosing a readable unit by default."""
    value = float(square_metres or 0.0)
    chosen = unit
    if unit == "auto":
        if value >= 1_000_000.0:
            chosen = SQUARE_KILOMETRE
        elif value >= 10_000.0:
            chosen = HECTARE
        else:
            chosen = SQUARE_METRE
    if chosen == SQUARE_KILOMETRE:
        return f"{value / 1_000_000.0:,.{decimals}f} km²"
    if chosen == HECTARE:
        return f"{value / 10_000.0:,.{decimals}f} ha"
    return f"{value:,.{max(decimals - 2, 0)}f} m²"


def format_length(metres: float, *, decimals: int = 1) -> str:
    """Format a length for the UI, switching to kilometres above 1 km."""
    value = float(metres or 0.0)
    if value >= 1000.0:
        return f"{value / 1000.0:,.{decimals + 1}f} km"
    return f"{value:,.{decimals}f} m"


def format_distance(metres: float) -> str:
    """Format a distance, handling the "no feature found" infinite case."""
    if metres == float("inf"):
        return "-"
    return format_length(metres)


def percentage(part: float, whole: float) -> float:
    """Return ``part/whole`` as a percentage, guarding against division by zero."""
    if not whole:
        return 0.0
    return max(0.0, min(100.0, 100.0 * float(part) / float(whole)))
