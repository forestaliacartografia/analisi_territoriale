"""Proximity engine: setback bands and distances to the datasets around the area.

Buffers are always built in the metric work CRS, so "30 m" means thirty metres even when
the project area lives in geographic coordinates - the single most common mistake in this
kind of analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from qgis.core import QgsGeometry

from ..core import geometry as geom_utils
from ..core import measure, settings
from ..core.models import SourceResult
from ..core.project_area import ProjectArea


@dataclass
class Band:
    """One setback band around the project area."""

    distance_m: float
    geometry: QgsGeometry
    area_m2: float = 0.0

    @property
    def label(self) -> str:
        """Human label, e.g. ``fascia 150 m``."""
        return f"fascia {self.distance_m:g} m"


@dataclass
class ProximityResult:
    """Bands plus the distance of every analysed source."""

    bands: List[Band] = field(default_factory=list)
    distances: Dict[str, float] = field(default_factory=dict)

    def nearest(self, limit: int = 5) -> List[tuple]:
        """Return the closest sources as ``(source_id, distance)`` pairs."""
        ordered = sorted(self.distances.items(), key=lambda item: item[1])
        return ordered[:limit]


class ProximityEngine:
    """Computes setback bands and collects distances from an analysis."""

    def __init__(self, area: ProjectArea) -> None:
        self.area = area

    # ------------------------------------------------------------------ bands

    def bands(self, distances: Sequence[float], *, rings: bool = True) -> List[Band]:
        """Build the bands at the given distances (metres).

        ``rings=True`` returns the ring between one distance and the previous one, which is
        what a "fascia di rispetto" usually means; ``rings=False`` returns full buffers.
        """
        result: List[Band] = []
        previous: Optional[QgsGeometry] = self.area.geometry
        for distance in sorted(float(value) for value in distances if float(value) > 0):
            buffered = self.area.buffered_geometry(distance)
            geometry = buffered
            if rings and previous is not None:
                difference = buffered.difference(previous)
                if not difference.isEmpty():
                    geometry = difference
            result.append(Band(distance_m=distance, geometry=geometry,
                               area_m2=measure.area_m2(geometry, self.area.crs)))
            previous = buffered
        return result

    def band_for(self, distance_m: float) -> Band:
        """Return a single buffer band."""
        geometry = self.area.buffered_geometry(distance_m)
        return Band(distance_m=distance_m, geometry=geometry,
                    area_m2=measure.area_m2(geometry, self.area.crs))

    # ------------------------------------------------------------------ distances

    @staticmethod
    def distances(results: Sequence[SourceResult]) -> Dict[str, float]:
        """Collect the minimum distance of every source that reported one."""
        collected: Dict[str, float] = {}
        for result in results:
            if result.present:
                collected[result.source_id] = 0.0
            elif result.min_distance_m is not None:
                collected[result.source_id] = float(result.min_distance_m)
        return collected

    def run(self, results: Sequence[SourceResult], *,
            distances: Optional[Sequence[float]] = None) -> ProximityResult:
        """Build the configured bands and collect the distances of an analysis."""
        values = distances if distances is not None else \
            settings.get("analysis.setback_bands_m", [10, 30, 150])
        return ProximityResult(bands=self.bands(values),
                               distances=self.distances(results))

    # ------------------------------------------------------------------ helpers

    def within(self, geometry: QgsGeometry, distance_m: float) -> bool:
        """Whether a geometry (in the area CRS) lies within ``distance_m`` of the area."""
        if geometry is None or geometry.isEmpty():
            return False
        if geometry.intersects(self.area.geometry):
            return True
        return measure.distance_m(geometry, self.area.geometry, self.area.crs) <= distance_m

    def clip_to_band(self, geometry: QgsGeometry, distance_m: float) -> QgsGeometry:
        """Clip a geometry to the buffer band at ``distance_m``."""
        band = self.area.buffered_geometry(distance_m)
        return geom_utils.intersection(geometry, band)
