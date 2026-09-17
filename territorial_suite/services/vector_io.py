"""Turning service payloads into QGIS layers, and layers into GeoPackages.

Two hard-won details live here:

* **GML sanitising.** The INSPIRE GML 3.2 served by several Italian services carries a
  ``gml:boundedBy`` envelope inside every feature. GDAL's schema guesser turns those two
  corners into attributes, which shifts every subsequent field by two positions (verified
  against the Agenzia delle Entrate cadastral service). Stripping ``gml:boundedBy`` before
  handing the file to OGR produces a correct, aligned schema.
* **Deterministic materialisation.** Every downloaded dataset is written to a GeoPackage in
  a known CRS, so caching, provenance and repeated analysis all work on a stable file.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFields,
    QgsGeometry,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import crs as crs_utils
from ..core import log
from ..core.crs import CrsLike
from ..core.errors import SourceSchemaError
from ..core.paths import safe_filename

_BOUNDED_BY = re.compile(rb"<(\w+:)?boundedBy>.*?</(\w+:)?boundedBy>", re.DOTALL)

GML_CONTENT_TYPES = ("application/gml+xml", "text/xml", "application/xml")
JSON_CONTENT_TYPES = ("application/json", "application/geo+json", "application/geojson")


@dataclass
class MaterialisedLayer:
    """A dataset written to disk and ready to be opened by QGIS."""

    path: Path
    layer_name: str
    feature_count: int
    crs: str

    @property
    def uri(self) -> str:
        """OGR URI usable with ``QgsVectorLayer``."""
        return f"{self.path}|layername={self.layer_name}"


def sanitise_gml(payload: bytes) -> bytes:
    """Remove per-feature ``gml:boundedBy`` envelopes so OGR infers the right schema."""
    return _BOUNDED_BY.sub(b"", payload)


def guess_extension(content_type: str, payload: bytes) -> str:
    """Pick the file extension OGR needs to choose the right driver."""
    lowered = (content_type or "").lower()
    head = payload[:512].lstrip()
    if any(token in lowered for token in JSON_CONTENT_TYPES) or head.startswith((b"{", b"[")):
        return ".geojson"
    if b"<?xml" in head or head.startswith(b"<"):
        if b"kml" in head.lower():
            return ".kml"
        return ".gml"
    if head.startswith(b"PK"):
        return ".zip"
    if head.startswith(b"SQLite format 3"):
        return ".gpkg"
    return ".dat"


def work_folder(root: Path, source_id: str) -> Path:
    """Return a scratch folder for a source, safe as a path component.

    Source ids may legitimately contain characters that Windows forbids in paths
    (``sitap_ws:v1497pol_wgs84`` is a real WFS type name): never build a directory from a
    raw id.
    """
    return Path(root) / safe_filename(source_id, fallback="source")


def payload_to_file(payload: bytes, folder: Path, *, content_type: str = "",
                    stem: str = "") -> Path:
    """Write a service payload to a temporary file OGR can open."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    extension = guess_extension(content_type, payload)
    data = sanitise_gml(payload) if extension == ".gml" else payload
    path = folder / f"{safe_filename(stem, fallback=uuid.uuid4().hex[:10])}{extension}"
    path.write_bytes(data)
    for sidecar in (".gfs", ".xsd"):
        stale = path.with_suffix(sidecar)
        if stale.exists():
            stale.unlink()
    return path


#: Axis orders a source may declare. ``xy`` is longitude/easting first, ``yx`` is
#: latitude/northing first. ``auto`` leaves the reader to its own defaults.
AXIS_XY = "xy"
AXIS_YX = "yx"
AXIS_AUTO = "auto"


def normalise_axes(path: Path, response_axis_order: str = AXIS_AUTO) -> Path:
    """Rewrite a GML whose coordinates are latitude-first into a canonical file.

    Some WFS 1.1.0 services label their answer ``EPSG:4326`` in the short form while
    writing latitude first, which is what the standard prescribes for that CRS and the
    opposite of what the short form tells GDAL. The reader then places every geometry
    with its ordinates exchanged: valid, plausible, and somewhere else. An intersection
    with the project area returns zero and nothing anywhere says why.

    The order is **declared by the source**, never guessed from the numbers. Over Italy
    both ordinates sit below 90, so any rule of the form "if x looks like a latitude"
    would be a coin toss dressed as logic, and would flip correct data as readily as
    wrong data.

    The rewrite happens while ``GML_CONSIDER_EPSG_AS_URN`` is set, which makes GDAL read
    the short form with the axis order the standard assigns it. The result is written to
    a GeoPackage rather than handed back as a lazily-opened layer: the provider reads
    when it pleases, and by then the configuration option would be gone.

    :returns: the canonical file, or ``path`` unchanged when no rewrite was asked for.
    """
    if response_axis_order != AXIS_YX or path.suffix.lower() != ".gml":
        return path
    try:
        from osgeo import gdal
    except ImportError:  # pragma: no cover - GDAL always ships with QGIS
        return path

    # GeoPackage, verified against the live service. GeoJSON was tried and silently
    # handed back the original ordering, which is exactly the failure this function
    # exists to prevent - so the format is part of the fix, not an implementation detail.
    target = path.with_suffix(".canonical.gpkg")
    previous = gdal.GetConfigOption("GML_CONSIDER_EPSG_AS_URN", None)
    try:
        gdal.SetConfigOption("GML_CONSIDER_EPSG_AS_URN", "YES")
        for sidecar in (".gfs", ".xsd"):
            stale = path.with_suffix(sidecar)
            if stale.exists():
                stale.unlink()
        if target.exists():
            target.unlink()
        source = gdal.OpenEx(str(path), gdal.OF_VECTOR)
        if source is None:
            return path
        # The returned dataset owns the file: it has to be closed before anything
        # reads it, or the GeoPackage is opened empty and the geometries look lost.
        written = gdal.VectorTranslate(str(target), source, format="GPKG")
        count = 0
        if written is not None:
            layer = written.GetLayer(0)
            count = layer.GetFeatureCount() if layer is not None else 0
            layer = None
        written = None
        source = None
        if count <= 0:
            log.warning(f"{path.name}: la normalizzazione dell'ordine degli assi non ha "
                        f"prodotto feature, si usa il file originale")
            return path
    except Exception as exc:  # pragma: no cover - driver differences
        log.warning(f"{path.name}: normalizzazione dell'ordine degli assi non riuscita "
                    f"({type(exc).__name__}: {exc})")
        return path
    finally:
        gdal.SetConfigOption("GML_CONSIDER_EPSG_AS_URN", previous)
    if not target.exists():
        return path
    log.debug(f"{path.name}: coordinate latitudine-prima normalizzate in {target.name}")
    return target


def open_vector(path: Path, *, name: str = "", crs_hint: str = "",
                response_axis_order: str = AXIS_AUTO) -> QgsVectorLayer:
    """Open a vector file with the OGR provider, applying a CRS hint when it lacks one.

    ``response_axis_order`` is what the *source descriptor* says the service writes, not
    a guess: see :func:`normalise_axes`.
    """
    path = normalise_axes(path, response_axis_order)
    layer = QgsVectorLayer(str(path), name or path.stem, "ogr")
    if not layer.isValid():
        raise SourceSchemaError(f"Cannot read downloaded dataset {path.name}")
    if crs_hint and not layer.crs().isValid():
        layer.setCrs(crs_utils.crs_from(crs_hint))
    return layer


def layer_from_payload(payload: bytes, folder: Path, *, name: str = "",
                       content_type: str = "", crs_hint: str = "",
                       response_axis_order: str = AXIS_AUTO) -> QgsVectorLayer:
    """Materialise a service payload and open it as a vector layer."""
    path = payload_to_file(payload, folder, content_type=content_type, stem=name)
    return open_vector(path, name=name, crs_hint=crs_hint,
                       response_axis_order=response_axis_order)


def _writer_options(layer_name: str, *, exists: bool, append: bool,
                    transform: Optional[QgsCoordinateTransform],
                    driver: str = "GPKG") -> QgsVectorFileWriter.SaveVectorOptions:
    """Build writer options.

    ``CreateOrOverwriteLayer`` opens the container in *update* mode, which only works on a
    file that already exists - asking for it on a missing file fails with "Opening of data
    source in update mode failed". So the action depends on whether the file is there.
    """
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    if transform is not None:
        options.ct = transform
    if exists:
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer if append
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile)
    return options


def write_layer(layer: QgsVectorLayer, out_path: Path, layer_name: str, *,
                target_crs: Optional[CrsLike] = None, append: bool = False,
                driver: str = "GPKG", force_multi: bool = True) -> MaterialisedLayer:
    """Write a vector layer to a GeoPackage (or another OGR driver), reprojecting if asked.

    ``force_multi`` promotes the declared geometry type to its multi variant: services
    routinely mix ``Polygon`` and ``MultiPolygon`` in the same feature type, which a strict
    GeoPackage would reject.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    transform = None
    destination_crs = layer.crs()
    if target_crs is not None:
        destination = crs_utils.crs_from(target_crs)
        if destination != layer.crs():
            transform = QgsCoordinateTransform(layer.crs(), destination,
                                               crs_utils.transform_context())
            destination_crs = destination
    options = _writer_options(layer_name, exists=out_path.exists(), append=append,
                              transform=transform, driver=driver)
    if force_multi and layer.wkbType() != QgsWkbTypes.Type.NoGeometry:
        options.overrideGeometryType = QgsWkbTypes.multiType(layer.wkbType())
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(out_path), crs_utils.transform_context(), options)
    code = result[0] if isinstance(result, (tuple, list)) else result
    if code != QgsVectorFileWriter.WriterError.NoError:
        message = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else ""
        raise SourceSchemaError(f"Cannot write {out_path.name}: {message or code}")
    # Never reopen the file just to count: QGIS pools OGR connections, and a pooled
    # handle on a GeoPackage makes the *next* write fail with "update mode failed".
    count = layer.featureCount()
    if count is None or count < 0:
        count = sum(1 for _ in layer.getFeatures())
    return MaterialisedLayer(path=out_path, layer_name=layer_name, feature_count=int(count),
                             crs=destination_crs.authid())


def memory_layer(geometry_type: str, crs: CrsLike, name: str, fields: QgsFields) -> QgsVectorLayer:
    """Create an in-memory layer with the given schema."""
    resolved = crs_utils.crs_from(crs)
    layer = QgsVectorLayer(f"{geometry_type}?crs={resolved.authid()}", name, "memory")
    if fields:
        layer.dataProvider().addAttributes(list(fields))
        layer.updateFields()
    return layer


def write_features(features: Sequence[QgsFeature], fields: QgsFields, geometry_type: str,
                   crs: CrsLike, out_path: Path, layer_name: str, *,
                   target_crs: Optional[CrsLike] = None, append: bool = False) -> MaterialisedLayer:
    """Write a list of features to a GeoPackage layer."""
    layer = memory_layer(geometry_type, crs, layer_name, fields)
    if features:
        layer.dataProvider().addFeatures(list(features))
        layer.updateExtents()
    return write_layer(layer, out_path, layer_name, target_crs=target_crs, append=append)


def features_in(layer: QgsVectorLayer, rect: Optional[QgsRectangle] = None,
                *, limit: int = 0) -> Iterable[QgsFeature]:
    """Iterate over features, optionally restricted to a bounding box and a count."""
    request = QgsFeatureRequest()
    if rect is not None:
        request.setFilterRect(rect)
    if limit:
        request.setLimit(int(limit))
    return layer.getFeatures(request)


def reproject_geometry_to_layer(geometry: QgsGeometry, geometry_crs: CrsLike,
                                layer: QgsVectorLayer) -> QgsGeometry:
    """Return ``geometry`` in the CRS of ``layer``."""
    return crs_utils.transform_geometry(geometry, geometry_crs, layer.crs())


def geometry_type_name(layer: QgsVectorLayer) -> str:
    """Return ``Point``/``LineString``/``Polygon`` for a layer (used to build memory layers)."""
    mapping = {
        Qgis.GeometryType.Point: "Point",
        Qgis.GeometryType.Line: "LineString",
        Qgis.GeometryType.Polygon: "Polygon",
    }
    return mapping.get(layer.geometryType(), "Unknown")


def safe_layer_name(source_id: str) -> str:
    """Turn a source id into a GeoPackage-friendly layer name."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in (source_id or "layer"))
    cleaned = cleaned.strip("_") or "layer"
    if cleaned[0].isdigit():
        cleaned = f"l_{cleaned}"
    return cleaned[:60]


def unique_layer_name(base: str, existing: Iterable[str]) -> str:
    """Return a layer name not already present in ``existing``."""
    taken = {name.lower() for name in existing}
    candidate = base
    index = 2
    while candidate.lower() in taken:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def list_gpkg_layers(path: Path) -> List[str]:
    """Return the layer names contained in a GeoPackage."""
    layer = QgsVectorLayer(str(path), "probe", "ogr")
    if not layer.isValid():
        return []
    try:
        provider = layer.dataProvider()
        return [item.split("!!::!!")[1] for item in provider.subLayers()]
    except Exception:  # pragma: no cover - defensive
        return []


def safe_field_names(fields: QgsFields) -> List[str]:
    """Return the field names of a layer, ignoring provider quirks."""
    try:
        return [field.name() for field in fields]
    except Exception:  # pragma: no cover - defensive
        log.debug("Cannot read field names")
        return []


def bbox_to_tuple(rect: QgsRectangle) -> Tuple[float, float, float, float]:
    """Return ``(xmin, ymin, xmax, ymax)``."""
    return rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()
