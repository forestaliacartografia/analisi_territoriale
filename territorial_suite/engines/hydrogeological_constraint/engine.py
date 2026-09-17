"""The vincolo idrogeologico of R.D.L. 3267/1923, as its own engine.

Deliberately not folded into the hazard engine. A PAI perimeter says an event is likely
somewhere; the 3267 perimeter says a legal regime applies there. Mixing them would let a
sheet titled "rischio" print a constraint, which is the error this codebase has already
made once.

The engine works in two modes, and which one applies is a property of the source, not a
setting:

* **measurable** - the perimeter can be downloaded, so the intersection with the area is
  computed and a surface and a percentage exist;
* **view-only** - the perimeter is published as an image. Points across the area are put
  to the service one by one and the answer is a count of points, never a surface. Tuscany
  is in this second case, verified: the R.D. layer is served by WMS and GetFeatureInfo
  but is absent from the WFS.

Outside the coverage of any configured source the answer is ``NON_VERIFICABILE`` with
``REGIONAL_SOURCE_REQUIRED``. It is never "not subject to the constraint": the competence
is regional, and a missing dataset says nothing about the territory.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from qgis.core import QgsPointXY, QgsVectorLayer

from ...core import crs as crs_utils
from ...core import geometry as geom_utils
from ...core import log, measure
from ...core.feedback import Feedback, NullFeedback
from ...core.gaps import DataGap
from ...core.project_area import ProjectArea
from ...core.registry import DataSource, DataSourceRegistry
from ...core.errors import SourceError
from .model import HydrogeologicalOutcome, Presence, SamplePoint

#: Taxonomy category the constraint belongs to.
CATEGORY = "hydro_geomorphological"

#: Tag a descriptor must carry to be recognised as a 3267 perimeter. Keeping it a tag
#: rather than an id lets a region be added by configuration alone.
TAG = "vincolo_idrogeologico"

#: Points sampled across the area when the source can only be looked at. A 3x3 grid plus
#: the centroid: enough to tell "all", "none" and "partly" apart without turning a view
#: service into a download.
DEFAULT_GRID = 3


class HydrogeologicalConstraintEngine:
    """Find the vincolo idrogeologico over a project area."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http=None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http

    # ------------------------------------------------------------------ selection

    def sources_for(self, area: ProjectArea) -> List[DataSource]:
        """Descriptors of the constraint that cover this area, best first."""
        found = [s for s in self.registry.query(operational_only=True)
                 if TAG in (s.tags or [])]
        return sorted(found, key=lambda s: s.priority)

    # ------------------------------------------------------------------ entry point

    def run(self, area: ProjectArea, *, feedback: Optional[Feedback] = None,
            grid: int = DEFAULT_GRID) -> HydrogeologicalOutcome:
        """Look for the constraint and report what was observed.

        Safe in a worker thread: it performs HTTP and geometry, and builds no QGIS layer
        of its own unless a measurable source hands one over.
        """
        feedback = feedback or NullFeedback()
        sources = self.sources_for(area)
        if not sources:
            return self._unverifiable(
                DataGap.REGIONAL_SOURCE_REQUIRED,
                "Nessuna fonte cartografica del vincolo idrogeologico e' configurata "
                "per quest'area. Il vincolo e' di competenza regionale.")

        source = sources[0]
        outcome = HydrogeologicalOutcome(
            source_id=source.id, source_name=source.name,
            region=", ".join(source.scope.codes) if source.scope else "",
            capability="analysable" if self._is_measurable(source) else "view_only",
            provenance=source.provenance(operation="Vincolo idrogeologico 3267/1923",
                                         crs=area.work_crs.authid()))
        try:
            if outcome.capability == "analysable":
                self._measure(area, source, outcome, feedback)
            else:
                self._sample(area, source, outcome, feedback, grid)
        except SourceError as exc:
            outcome.presence = Presence.NON_VERIFICABILE
            outcome.gaps.append(DataGap.SOURCE_UNAVAILABLE.value)
            outcome.warnings.append(f"La fonte non ha risposto: {exc}")
        return outcome

    # ------------------------------------------------------------------ measurable

    @staticmethod
    def _is_measurable(source: DataSource) -> bool:
        """Whether the perimeter can be downloaded rather than only looked at."""
        return source.type.value in ("WFS", "OGCAPI", "ARCGIS_FEATURE", "GEOJSON", "GPKG")

    def _measure(self, area: ProjectArea, source: DataSource,
                 outcome: HydrogeologicalOutcome, feedback: Feedback) -> None:
        """Download the perimeter and intersect it with the area."""
        from ...services.ogc.wfs import WfsClient

        work_crs = area.work_crs
        area_geom = area.geometry_in(work_crs)
        total = measure.area_m2(area_geom, work_crs)
        rect = area.context_bbox(source.crs[0] if source.crs else crs_utils.WGS84)
        result = WfsClient(source, http=self.http).fetch(
            rect, source.crs[0] if source.crs else crs_utils.WGS84,
            area_id=area.id, feedback=feedback)
        layer = result.layer(source.name)
        outcome.area_m2 = 0.0
        outcome.percentage = 0.0
        if layer is None or not layer.isValid():
            outcome.presence = Presence.NON_VERIFICABILE
            outcome.gaps.append(DataGap.QUERY_FAILED.value)
            outcome.area_m2 = outcome.percentage = None
            return
        self._accumulate(layer, area_geom, work_crs, source, outcome)
        outcome.percentage = measure.percentage(outcome.area_m2, total)
        if outcome.area_m2 <= 0:
            outcome.presence = Presence.ASSENTE
            outcome.gaps.append(DataGap.NO_FEATURE_FOUND.value)
        elif outcome.percentage >= 99.5:
            outcome.presence = Presence.PRESENTE
        else:
            outcome.presence = Presence.PARZIALE

    @staticmethod
    def _accumulate(layer: QgsVectorLayer, area_geom, work_crs,
                    source: DataSource, outcome: HydrogeologicalOutcome) -> None:
        """Sum the surface of the perimeter really inside the area."""
        act_field = (source.fields or {}).get("act", "atto")
        acts = set()
        total = 0.0
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if layer.crs() != work_crs:
                try:
                    geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
                except Exception as exc:
                    log.debug(f"_accumulate: feature saltata "
                              f"({type(exc).__name__}: {exc})")
                    continue
            clipped = geom_utils.intersection(geometry, area_geom)
            if clipped is None or clipped.isEmpty():
                continue
            total += measure.area_m2(clipped, work_crs)
            if act_field and layer.fields().indexOf(act_field) >= 0:
                value = str(feature[act_field] or "").strip()
                if value:
                    acts.add(value)
        outcome.area_m2 = total
        outcome.act_references = sorted(acts)

    # ------------------------------------------------------------------ view only

    def _sample(self, area: ProjectArea, source: DataSource,
                outcome: HydrogeologicalOutcome, feedback: Feedback,
                grid: int) -> None:
        """Ask the service what it holds at points spread across the area."""
        from ...services.ogc import raster

        work_crs = area.work_crs
        points = self.sample_points(area, grid)
        answered = 0
        act_field = (source.fields or {}).get("act", "atto")
        acts = set()
        for point in points:
            if feedback.is_canceled():
                break
            try:
                records = raster.feature_info(source, point, work_crs, http=self.http,
                                              feedback=feedback)
                answered += 1
            except SourceError as exc:
                outcome.warnings.append(f"Interrogazione non riuscita in un punto: {exc}")
                outcome.samples.append(SamplePoint(point.x(), point.y(),
                                                   work_crs.authid(), False, {}, False))
                continue
            inside = bool(records)
            outcome.samples.append(SamplePoint(
                point.x(), point.y(), work_crs.authid(), inside,
                records[0] if records else {}, True))
            for record in records:
                value = str(record.get(act_field, "") or "").strip()
                if value:
                    acts.add(value)

        outcome.act_references = sorted(acts)
        outcome.gaps.append(DataGap.VIEW_ONLY.value)
        outcome.warnings.append(
            "La fonte pubblica il perimetro come immagine interrogabile per punto: "
            "la superficie e la percentuale di area vincolata non sono calcolabili.")
        if not answered:
            outcome.presence = Presence.NON_VERIFICABILE
            outcome.gaps.append(DataGap.SOURCE_UNAVAILABLE.value)
            return
        hits = outcome.inside
        if hits == 0:
            outcome.presence = Presence.ASSENTE
        elif hits == answered:
            outcome.presence = Presence.PRESENTE
        else:
            outcome.presence = Presence.PARZIALE

    @staticmethod
    def sample_points(area: ProjectArea, grid: int = DEFAULT_GRID) -> List[QgsPointXY]:
        """Points spread over the area: a grid of interior points plus a safe centre.

        Only points that really fall inside the area are kept, so a concave or a
        multipart area is not sampled in the empty space around it.
        """
        grid = max(1, int(grid))
        work_crs = area.work_crs
        geometry = area.geometry_in(work_crs)
        rect = geometry.boundingBox()
        points: List[QgsPointXY] = []
        for row in range(grid):
            for column in range(grid):
                x = rect.xMinimum() + rect.width() * (column + 0.5) / grid
                y = rect.yMinimum() + rect.height() * (row + 0.5) / grid
                candidate = QgsPointXY(x, y)
                from qgis.core import QgsGeometry

                if geometry.contains(QgsGeometry.fromPointXY(candidate)):
                    points.append(candidate)
        inner = geom_utils.representative_point(geometry)
        if inner is not None and not any(p.compare(inner, 1e-9) for p in points):
            points.append(inner)
        return points

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _unverifiable(gap: DataGap, message: str) -> HydrogeologicalOutcome:
        """The honest answer when nothing can be said."""
        outcome = HydrogeologicalOutcome(presence=Presence.NON_VERIFICABILE)
        outcome.gaps.append(gap.value)
        outcome.warnings.append(message)
        return outcome


def run(area: ProjectArea, *, registry: Optional[DataSourceRegistry] = None,
        feedback: Optional[Feedback] = None) -> HydrogeologicalOutcome:
    """Convenience entry point used by the GUI and by Processing."""
    return HydrogeologicalConstraintEngine(registry=registry).run(area, feedback=feedback)
