"""OpenStreetMap access through the Overpass API.

OSM is a *community* source: it is never presented as an official dataset. Every layer
downloaded here carries ``evidence_level = cartographic``, the OSM attribution and the ODbL
licence, and the report lists it separately from institutional sources.

Implementation notes:

* Overpass rejects requests without a User-Agent (HTTP 406); :class:`HttpClient` always
  sends one.
* The answer is OSM XML, read through the GDAL ``OSM`` driver. That driver reports
  ``featureCount() == -1``, so features are counted while materialising them.
* Overpass bounding boxes are ``(south, west, north, east)``.
"""

from __future__ import annotations

from typing import List, Optional

from qgis.core import QgsRectangle, QgsVectorLayer

from ..core import cache as cache_module
from ..core import crs as crs_utils
from ..core import settings
from ..core.cache import CacheManager, make_key, quantise_bbox
from ..core.crs import CrsLike
from ..core.errors import SourceSchemaError, SourceUnavailableError, UserCancelled
from ..core.feedback import Feedback, NullFeedback
from ..core.paths import temp_dir
from ..core.registry import DataSource
from . import vector_io
from .http import HttpClient
from .ogc.wfs import FetchResult

#: OSM sub-layers exposed by the GDAL driver.
OSM_LAYERS = ("points", "lines", "multilinestrings", "multipolygons", "other_relations")

OSM_ATTRIBUTION = "(c) OpenStreetMap contributors, ODbL"


class OverpassClient:
    """Client for one Overpass-based source descriptor."""

    def __init__(self, source: DataSource, *, http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.source = source
        self.http = HttpClient.for_source(source, http)
        self.cache = cache or CacheManager.instance()

    # ------------------------------------------------------------------ query building

    @property
    def osm_layer(self) -> str:
        """Which GDAL OSM sub-layer holds the requested features."""
        name = str(self.source.query.get("osm_layer", "lines")).lower()
        return name if name in OSM_LAYERS else "lines"

    def build_query(self, rect_wgs84: QgsRectangle) -> str:
        """Build the Overpass QL statement for a bounding box."""
        timeout = int(self.source.query.get("timeout", 60))
        bbox = (f"{rect_wgs84.yMinimum():.7f},{rect_wgs84.xMinimum():.7f},"
                f"{rect_wgs84.yMaximum():.7f},{rect_wgs84.xMaximum():.7f}")
        custom = self.source.query.get("ql")
        if custom:
            return str(custom).replace("{{bbox}}", bbox).replace("{{timeout}}", str(timeout))
        filters: List[str] = [str(item) for item in self.source.query.get("filters", [])]
        if not filters:
            raise SourceSchemaError(f"Overpass source {self.source.id} declares no filters",
                                    source_id=self.source.id)
        body = "".join(f"{item}({bbox});" for item in filters)
        recurse = "(._;>;);" if self.osm_layer != "points" else ""
        return f"[out:xml][timeout:{timeout}];({body});{recurse}out body;"

    # ------------------------------------------------------------------ answer checks

    @staticmethod
    def _remark(payload: bytes) -> str:
        """Return the ``<remark>`` Overpass uses to report errors, or an empty string."""
        start = payload.find(b"<remark>")
        if start < 0:
            return ""
        end = payload.find(b"</remark>", start)
        fragment = payload[start + len(b"<remark>"):end if end > 0 else start + 300]
        return " ".join(fragment.decode("utf-8", "replace").split())[:200]

    @staticmethod
    def _looks_empty(payload: bytes) -> bool:
        """Whether the answer is a valid OSM document with no elements."""
        head = payload[:400].lstrip()
        if not head.startswith(b"<?xml") and b"<osm" not in head:
            return False
        for marker in (b"<node", b"<way", b"<relation"):
            if marker in payload:
                return False
        return True

    # ------------------------------------------------------------------ fetching

    def fetch(self, rect: QgsRectangle, rect_crs: CrsLike, *,
              target_crs: Optional[CrsLike] = None, area_id: str = "",
              max_features: Optional[int] = None, refresh: bool = False,
              feedback: Optional[Feedback] = None) -> FetchResult:
        """Run the Overpass query for ``rect`` and materialise the requested sub-layer."""
        feedback = feedback or NullFeedback()
        rect_wgs84 = crs_utils.transform_bbox(rect, rect_crs, crs_utils.WGS84)
        destination = crs_utils.crs_from(target_crs) if target_crs is not None \
            else crs_utils.crs_from(crs_utils.WGS84)
        cap = int(max_features or self.source.max_features
                  or settings.get("network.max_features_per_source", 20000))

        grid = float(settings.get("cache.bbox_grid_m", 100)) / 111_320.0
        key = make_key(self.source.id, op="overpass", osm_layer=self.osm_layer,
                       filters=self.source.query.get("filters", []),
                       ql=self.source.query.get("ql", ""),
                       bbox=quantise_bbox(rect_wgs84.xMinimum(), rect_wgs84.yMinimum(),
                                          rect_wgs84.xMaximum(), rect_wgs84.yMaximum(), grid),
                       target_crs=destination.authid())
        out_path = self.cache.path_for(cache_module.KIND_VECTOR, self.source.id, key, ".gpkg")
        layer_name = "data"
        if not refresh:
            entry = self.cache.get(key)
            if entry is not None:
                cached = QgsVectorLayer(f"{entry.path}|layername={layer_name}", layer_name, "ogr")
                if cached.isValid():
                    return FetchResult(source_id=self.source.id,
                                       materialised=vector_io.MaterialisedLayer(
                                           path=entry.path, layer_name=layer_name,
                                           feature_count=cached.featureCount(),
                                           crs=cached.crs().authid()),
                                       feature_count=cached.featureCount(), from_cache=True)

        if feedback.is_canceled():
            raise UserCancelled()
        statement = self.build_query(rect_wgs84)
        feedback.push_debug(f"{self.source.id}: overpass query {statement[:120]}")
        response = self.http.get(self.source.url, {"data": statement},
                                 source_id=self.source.id, feedback=feedback,
                                 min_interval_s=self.source.query.get("min_interval_s"))
        remark = self._remark(response.content)
        if remark:
            # Overpass reports server-side failures inside the document, with HTTP 200.
            raise SourceUnavailableError(f"Overpass: {remark}", source_id=self.source.id)

        folder = vector_io.work_folder(temp_dir() / "osm", self.source.id)
        path = vector_io.payload_to_file(response.content, folder, content_type="text/xml",
                                         stem="overpass")
        osm_path = path.with_suffix(".osm")
        if path != osm_path:
            path.replace(osm_path)
        layer = QgsVectorLayer(f"{osm_path}|layername={self.osm_layer}", self.osm_layer, "ogr")
        if not layer.isValid():
            if self._looks_empty(response.content):
                # A valid but empty OSM document: no data here, not a failure.
                return FetchResult(source_id=self.source.id, feature_count=0,
                                   urls=[response.url], elapsed_ms=response.elapsed_ms)
            raise SourceSchemaError("Cannot read the Overpass answer (GDAL OSM driver)",
                                    source_id=self.source.id)
        features = []
        for index, feature in enumerate(layer.getFeatures()):
            if index >= cap:
                break
            features.append(feature)
        if not features:
            return FetchResult(source_id=self.source.id, feature_count=0,
                               urls=[response.url], elapsed_ms=response.elapsed_ms)
        materialised = vector_io.write_features(
            features, layer.fields(), vector_io.geometry_type_name(layer),
            layer.crs().authid() or crs_utils.WGS84, out_path, layer_name,
            target_crs=destination)
        self.cache.put(key, out_path, kind=cache_module.KIND_VECTOR, source_id=self.source.id,
                       area_id=area_id)
        return FetchResult(source_id=self.source.id, materialised=materialised,
                           feature_count=materialised.feature_count,
                           truncated=len(features) >= cap, urls=[response.url],
                           elapsed_ms=response.elapsed_ms)
