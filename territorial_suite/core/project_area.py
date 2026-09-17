"""The Project Area: logical centre of every operation in the plugin.

A project area is a polygon plus its identity (id, name, CRS, creation date), its metrics
(area, perimeter, bbox, centroid) and its administrative framing. It is persisted both in
the QGIS project (so reopening a project restores the analysis context) and as a portable
``*.tsa.json`` file.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

from . import crs as crs_utils
from . import geometry as geom_utils
from . import log, measure, qt_compat, settings
from .constants import AREA_FILE_SUFFIX, PROP_PROJECT_AREA, PROP_PROJECT_AREA_LIST
from .errors import ConfigError, GeometryError
from .models import AdminUnits, utc_now

#: How the area was created (used for provenance and UI hints).
SOURCE_DRAW = "draw"
SOURCE_RECTANGLE = "rectangle"
SOURCE_CIRCLE = "circle"
SOURCE_LAYER = "layer"
SOURCE_SELECTION = "selection"
SOURCE_FILE = "file"
SOURCE_COORDINATES = "coordinates"
SOURCE_BBOX = "bbox"
SOURCE_CADASTRAL = "cadastral_parcels"
SOURCE_UNKNOWN = "unknown"


@dataclass(eq=False)
class ProjectArea:
    """A validated polygon with identity, metrics and administrative framing."""

    geometry: QgsGeometry
    crs: QgsCoordinateReferenceSystem
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "Project area"
    created_at: str = field(default_factory=utc_now)
    source_kind: str = SOURCE_UNKNOWN
    notes: str = ""
    admin: AdminUnits = field(default_factory=AdminUnits)
    parcels: List[Dict[str, Any]] = field(default_factory=list)
    _work_crs: Optional[QgsCoordinateReferenceSystem] = field(default=None, repr=False)
    _area_m2: Optional[float] = field(default=None, repr=False)
    _perimeter_m: Optional[float] = field(default=None, repr=False)

    # ------------------------------------------------------------------ factories

    @classmethod
    def from_geometry(cls, geometry: QgsGeometry, crs: crs_utils.CrsLike, *,
                      name: Optional[str] = None, source_kind: str = SOURCE_UNKNOWN,
                      notes: str = "") -> "ProjectArea":
        """Create an area from any polygonal geometry, repairing it if necessary."""
        polygonal = geom_utils.keep_polygons(geom_utils.ensure_valid(geometry, context="project area"))
        if polygonal.isEmpty():
            raise GeometryError("The project area must be polygonal")
        return cls(
            geometry=polygonal,
            crs=crs_utils.crs_from(crs),
            name=name or "Project area",
            source_kind=source_kind,
            notes=notes,
        )

    @classmethod
    def from_wkt(cls, wkt: str, crs: crs_utils.CrsLike, **kwargs: Any) -> "ProjectArea":
        """Create an area from a WKT polygon."""
        geometry = QgsGeometry.fromWkt(wkt)
        if geometry is None or geometry.isNull():
            raise GeometryError("Invalid WKT geometry")
        return cls.from_geometry(geometry, crs, **kwargs)

    @classmethod
    def from_rectangle(cls, rect: QgsRectangle, crs: crs_utils.CrsLike, **kwargs: Any) -> "ProjectArea":
        """Create an area from a bounding box."""
        kwargs.setdefault("source_kind", SOURCE_BBOX)
        return cls.from_geometry(QgsGeometry.fromRect(rect), crs, **kwargs)

    @classmethod
    def from_circle(cls, centre: QgsPointXY, radius_m: float, crs: crs_utils.CrsLike,
                    *, segments: int = 72, **kwargs: Any) -> "ProjectArea":
        """Create a circular area of ``radius_m`` metres around a point."""
        point = QgsGeometry.fromPointXY(centre)
        circle = geom_utils.buffer_metres(point, crs, radius_m, segments=segments)
        kwargs.setdefault("source_kind", SOURCE_CIRCLE)
        return cls.from_geometry(circle, crs, **kwargs)

    @classmethod
    def from_features(cls, features: Iterable[QgsFeature], crs: crs_utils.CrsLike,
                      *, dissolve: bool = True, **kwargs: Any) -> "ProjectArea":
        """Create an area from features, dissolving them by default."""
        geometries = [f.geometry() for f in features if f.hasGeometry()]
        if not geometries:
            raise GeometryError("No geometries available to build the project area")
        merged = geom_utils.union(geometries) if dissolve else geometries[0]
        return cls.from_geometry(merged, crs, **kwargs)

    @classmethod
    def from_layer(cls, layer: QgsVectorLayer, *, selected_only: bool = False,
                   dissolve: bool = True, **kwargs: Any) -> "ProjectArea":
        """Create an area from a vector layer (optionally from its selection only)."""
        if layer is None or not layer.isValid():
            raise ConfigError("Invalid layer")
        if layer.geometryType() != Qgis.GeometryType.Polygon:
            raise GeometryError("The source layer must be polygonal")
        features = layer.selectedFeatures() if selected_only else list(layer.getFeatures())
        if not features:
            raise GeometryError("The layer has no features to use" if not selected_only
                                else "No feature is selected")
        kwargs.setdefault("name", layer.name())
        kwargs.setdefault("source_kind", SOURCE_SELECTION if selected_only else SOURCE_LAYER)
        return cls.from_features(features, layer.crs(), dissolve=dissolve, **kwargs)

    @classmethod
    def from_file(cls, path: str, *, layer_name: str = "", **kwargs: Any) -> "ProjectArea":
        """Create an area from a vector file (SHP, GPKG, GeoJSON, KML/KMZ...)."""
        uri = f"{path}|layername={layer_name}" if layer_name else str(path)
        layer = QgsVectorLayer(uri, Path(path).stem, "ogr")
        if not layer.isValid():
            raise ConfigError(f"Cannot open {path}")
        kwargs.setdefault("name", Path(path).stem)
        kwargs.setdefault("source_kind", SOURCE_FILE)
        return cls.from_layer(layer, **kwargs)

    # ------------------------------------------------------------------ geometry access

    @property
    def work_crs(self) -> QgsCoordinateReferenceSystem:
        """Metric CRS used for buffers, clips and geometric operations."""
        if self._work_crs is None:
            self._work_crs = crs_utils.resolve_work_crs(
                self.geometry,
                self.crs,
                mode=settings.get("general.work_crs_mode", "auto_utm"),
                explicit=settings.get("general.work_crs", ""),
            )
        return self._work_crs

    def geometry_in(self, target: crs_utils.CrsLike) -> QgsGeometry:
        """Return the area geometry reprojected to ``target``."""
        return crs_utils.transform_geometry(self.geometry, self.crs, target)

    def bbox(self, target: Optional[crs_utils.CrsLike] = None) -> QgsRectangle:
        """Return the bounding box in the area CRS, or in ``target``."""
        if target is None:
            return self.geometry.boundingBox()
        return crs_utils.bbox_in(self.geometry, self.crs, target)

    def bbox_wgs84(self) -> QgsRectangle:
        """Return the bounding box in EPSG:4326 (used for most OGC queries)."""
        return self.bbox(crs_utils.WGS84)

    def centroid(self) -> Optional[QgsPointXY]:
        """Return a representative point of the area, in the area CRS."""
        return geom_utils.centroid(self.geometry)

    def centroid_wgs84(self) -> Optional[QgsPointXY]:
        """Return a representative point in EPSG:4326."""
        point = self.centroid()
        if point is None:
            return None
        return crs_utils.transform_point(point, self.crs, crs_utils.WGS84)

    def buffered_geometry(self, distance_m: float) -> QgsGeometry:
        """Return the area geometry expanded by ``distance_m`` metres (area CRS)."""
        return geom_utils.buffer_metres(self.geometry, self.crs, distance_m)

    def context_bbox(self, target: crs_utils.CrsLike, *, buffer_m: Optional[float] = None) -> QgsRectangle:
        """Return the query bounding box, enlarged by the configured context buffer.

        Queries use a buffered bbox so that nearby features (for distance checks) are
        retrieved together with the intersecting ones, in a single request.
        """
        distance = settings.get("analysis.context_buffer_m", 500) if buffer_m is None else buffer_m
        geometry = self.buffered_geometry(float(distance or 0.0))
        return crs_utils.transform_bbox(geometry.boundingBox(), self.crs, target)

    # ------------------------------------------------------------------ metrics

    @property
    def area_m2(self) -> float:
        """Ellipsoidal area in square metres."""
        if self._area_m2 is None:
            self._area_m2 = measure.area_m2(self.geometry, self.crs)
        return self._area_m2

    @property
    def perimeter_m(self) -> float:
        """Ellipsoidal perimeter in metres."""
        if self._perimeter_m is None:
            self._perimeter_m = measure.length_m(self.geometry, self.crs)
        return self._perimeter_m

    def metrics(self) -> geom_utils.ShapeMetrics:
        """Return the full set of shape descriptors."""
        return geom_utils.shape_metrics(self.geometry, self.crs)

    def summary(self) -> str:
        """Return a one-line description used in the dock and in reports."""
        return (f"{self.name} - {measure.format_area(self.area_m2)} - "
                f"{measure.format_length(self.perimeter_m)} - {self.crs.authid()}")

    # ------------------------------------------------------------------ QGIS objects

    def to_memory_layer(self, name: Optional[str] = None) -> QgsVectorLayer:
        """Return a single-feature memory layer carrying the area and its metrics."""
        layer = QgsVectorLayer(f"Polygon?crs={self.crs.authid()}", name or self.name, "memory")
        provider = layer.dataProvider()
        provider.addAttributes([
            qt_compat.field("area_id", qt_compat.STRING),
            qt_compat.field("name", qt_compat.STRING),
            qt_compat.field("created_at", qt_compat.STRING),
            qt_compat.field("source_kind", qt_compat.STRING),
            qt_compat.field("area_m2", qt_compat.DOUBLE),
            qt_compat.field("area_ha", qt_compat.DOUBLE),
            qt_compat.field("perimeter_m", qt_compat.DOUBLE),
            qt_compat.field("municipalities", qt_compat.STRING),
        ])
        layer.updateFields()
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry(self.geometry))
        feature.setAttributes([
            self.id, self.name, self.created_at, self.source_kind,
            round(self.area_m2, 2), round(self.area_m2 / 10_000.0, 4),
            round(self.perimeter_m, 2),
            ", ".join(unit.name for unit in self.admin.municipalities),
        ])
        provider.addFeature(feature)
        layer.updateExtents()
        return layer

    # ------------------------------------------------------------------ serialisation

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary describing the area."""
        bbox = self.bbox()
        centroid = self.centroid()
        centroid_wgs = self.centroid_wgs84()
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "source_kind": self.source_kind,
            "notes": self.notes,
            "crs": self.crs.authid() or self.crs.toWkt(),
            "work_crs": self.work_crs.authid(),
            "geometry_wkt": self.geometry.asWkt(),
            "area_m2": self.area_m2,
            "area_ha": self.area_m2 / 10_000.0,
            "area_km2": self.area_m2 / 1_000_000.0,
            "perimeter_m": self.perimeter_m,
            "bbox": geom_utils.bbox_dict(bbox),
            "centroid": {"x": centroid.x(), "y": centroid.y()} if centroid else None,
            "centroid_wgs84": {"lon": centroid_wgs.x(), "lat": centroid_wgs.y()} if centroid_wgs else None,
            "ellipsoid": measure.ellipsoid_name(self.crs),
            "admin": self.admin.as_dict(),
            "parcels": self.parcels,
            "format": "territorial_suite.project_area/1",
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ProjectArea":
        """Rebuild an area from :meth:`as_dict` output."""
        data = payload or {}
        wkt = data.get("geometry_wkt", "")
        geometry = QgsGeometry.fromWkt(wkt)
        if geometry is None or geometry.isNull():
            raise GeometryError("Stored project area has no usable geometry")
        area = cls(
            geometry=geometry,
            crs=crs_utils.crs_from(data.get("crs") or crs_utils.WGS84),
            id=data.get("id") or uuid.uuid4().hex[:12],
            name=data.get("name", "Project area"),
            created_at=data.get("created_at", utc_now()),
            source_kind=data.get("source_kind", SOURCE_UNKNOWN),
            notes=data.get("notes", ""),
            admin=AdminUnits.from_dict(data.get("admin", {})),
            parcels=list(data.get("parcels", [])),
        )
        return area

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise the area to JSON."""
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent)

    def save_to_file(self, path: str) -> Path:
        """Write the portable ``*.tsa.json`` representation of the area."""
        target = Path(path)
        if target.suffix != ".json":
            target = target.with_name(target.name + AREA_FILE_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        log.info(f"Project area saved to {target}")
        return target

    @classmethod
    def load_from_file(cls, path: str) -> "ProjectArea":
        """Read a ``*.tsa.json`` file written by :meth:`save_to_file`."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot read project area file {path}", detail=str(exc)) from exc
        return cls.from_dict(payload)


class ProjectAreaStore:
    """Persistence of project areas inside a QGIS project."""

    def __init__(self, project: Optional[QgsProject] = None) -> None:
        self._project = project or QgsProject.instance()

    # -- current area

    def save(self, area: ProjectArea, *, make_current: bool = True) -> None:
        """Store ``area`` in the project, optionally making it the current one."""
        areas = {item["id"]: item for item in self.list_areas()}
        areas[area.id] = area.as_dict()
        self._project.writeEntry(*self._entry(PROP_PROJECT_AREA_LIST),
                                 json.dumps(list(areas.values()), ensure_ascii=False))
        if make_current:
            self._project.writeEntry(*self._entry(PROP_PROJECT_AREA), area.id)
        self._project.setDirty(True)

    def current(self) -> Optional[ProjectArea]:
        """Return the current project area, or ``None``."""
        area_id, _ = self._project.readEntry(*self._entry(PROP_PROJECT_AREA), "")
        areas = self.list_areas()
        if not areas:
            return None
        payload = next((item for item in areas if item.get("id") == area_id), areas[-1])
        try:
            return ProjectArea.from_dict(payload)
        except (GeometryError, ConfigError) as exc:  # pragma: no cover - corrupted project
            log.warning(f"Stored project area could not be restored: {exc}")
            return None

    def set_current(self, area_id: str) -> None:
        """Mark an existing stored area as the current one."""
        self._project.writeEntry(*self._entry(PROP_PROJECT_AREA), area_id)
        self._project.setDirty(True)

    def list_areas(self) -> List[Dict[str, Any]]:
        """Return the raw dictionaries of every area stored in the project."""
        raw, ok = self._project.readEntry(*self._entry(PROP_PROJECT_AREA_LIST), "")
        if not ok or not raw:
            return []
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, list) else []
        except ValueError:  # pragma: no cover - corrupted project
            log.warning("Stored project areas are corrupted and were ignored")
            return []

    def remove(self, area_id: str) -> None:
        """Delete a stored area."""
        areas = [item for item in self.list_areas() if item.get("id") != area_id]
        self._project.writeEntry(*self._entry(PROP_PROJECT_AREA_LIST),
                                 json.dumps(areas, ensure_ascii=False))
        self._project.setDirty(True)

    def clear(self) -> None:
        """Remove every stored area from the project."""
        self._project.removeEntry(*self._entry(PROP_PROJECT_AREA_LIST))
        self._project.removeEntry(*self._entry(PROP_PROJECT_AREA))
        self._project.setDirty(True)

    @staticmethod
    def _entry(key: str) -> tuple:
        scope, _, name = key.partition("/")
        return scope, name
