"""ArcGIS REST FeatureServer / MapServer query client.

Many Italian regional and provincial portals publish their layers through ArcGIS REST
rather than OGC services, so supporting the ``/query`` endpoint (GeoJSON output, offset
paging) considerably widens the catalogue with no extra dependency.
"""

from __future__ import annotations

import json
from typing import List, Optional

from qgis.core import QgsRectangle, QgsVectorLayer

from ..core import cache as cache_module
from ..core import crs as crs_utils
from ..core import settings
from ..core.cache import CacheManager, make_key, quantise_bbox
from ..core.crs import CrsLike
from ..core.errors import SourceSchemaError, UserCancelled
from ..core.feedback import Feedback, NullFeedback
from ..core.paths import temp_dir
from ..core.registry import DataSource
from . import vector_io
from .http import HttpClient
from .ogc.wfs import FetchResult


class ArcGisClient:
    """Client for an ArcGIS REST feature layer."""

    def __init__(self, source: DataSource, *, http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.source = source
        self.http = HttpClient.for_source(source, http)
        self.cache = cache or CacheManager.instance()

    def query_url(self) -> str:
        """Return the ``/query`` endpoint, appending the layer index when needed."""
        base = self.source.url.rstrip("/")
        if base.endswith("/query"):
            return base
        if self.source.layer and not base.rsplit("/", 1)[-1].isdigit():
            return f"{base}/{self.source.layer}/query"
        return f"{base}/query"

    def fetch(self, rect: QgsRectangle, rect_crs: CrsLike, *,
              target_crs: Optional[CrsLike] = None, area_id: str = "",
              max_features: Optional[int] = None, refresh: bool = False,
              feedback: Optional[Feedback] = None) -> FetchResult:
        """Download the features intersecting ``rect`` using offset paging."""
        feedback = feedback or NullFeedback()
        service_crs = crs_utils.crs_from(self.source.bbox_crs or crs_utils.WGS84)
        destination = crs_utils.crs_from(target_crs) if target_crs is not None else service_crs
        query_rect = crs_utils.transform_bbox(rect, rect_crs, service_crs)
        cap = int(max_features or self.source.max_features
                  or settings.get("network.max_features_per_source", 20000))
        page_size = max(1, int(self.source.page_size))
        srid = service_crs.postgisSrid()

        grid = float(settings.get("cache.bbox_grid_m", 100))
        if service_crs.isGeographic():
            grid /= 111_320.0
        key = make_key(self.source.id, op="arcgis", layer=self.source.layer,
                       bbox=quantise_bbox(query_rect.xMinimum(), query_rect.yMinimum(),
                                          query_rect.xMaximum(), query_rect.yMaximum(), grid),
                       target_crs=destination.authid(), cap=cap)
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

        collected: List[dict] = []
        urls: List[str] = []
        elapsed = 0
        truncated = False
        offset = 0
        while True:
            if feedback.is_canceled():
                raise UserCancelled()
            params = {
                "where": self.source.query.get("where", "1=1"),
                "geometry": (f"{query_rect.xMinimum()},{query_rect.yMinimum()},"
                             f"{query_rect.xMaximum()},{query_rect.yMaximum()}"),
                "geometryType": "esriGeometryEnvelope",
                "inSR": srid,
                "outSR": srid,
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": self.source.query.get("out_fields", "*"),
                "returnGeometry": "true",
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": min(page_size, cap - len(collected)),
            }
            params.update(self.source.query.get("params", {}) or {})
            response = self.http.get(self.query_url(), params, source_id=self.source.id,
                                     feedback=feedback,
                                     min_interval_s=self.source.query.get("min_interval_s"))
            urls.append(response.url)
            elapsed += response.elapsed_ms
            try:
                payload = json.loads(response.text())
            except ValueError as exc:
                raise SourceSchemaError("Invalid GeoJSON from ArcGIS service",
                                        source_id=self.source.id, detail=str(exc)) from exc
            if isinstance(payload, dict) and payload.get("error"):
                message = payload["error"].get("message", "unknown error")
                raise SourceSchemaError(f"ArcGIS service error: {message}",
                                        source_id=self.source.id)
            features = payload.get("features", [])
            collected.extend(features)
            feedback.push_debug(f"{self.source.id}: {len(features)} features "
                                f"({len(collected)} total)")
            exceeded = bool(payload.get("properties", {}).get("exceededTransferLimit")
                            or payload.get("exceededTransferLimit"))
            if len(collected) >= cap:
                truncated = True
                break
            if not features or (len(features) < page_size and not exceeded):
                break
            offset += len(features)

        if not collected:
            return FetchResult(source_id=self.source.id, feature_count=0, urls=urls,
                               elapsed_ms=elapsed)
        document = json.dumps({"type": "FeatureCollection", "features": collected[:cap]})
        layer = vector_io.layer_from_payload(document.encode("utf-8"),
                                             vector_io.work_folder(temp_dir() / "arcgis",
                                                                   self.source.id),
                                             name="items",
                                             content_type="application/geo+json",
                                             crs_hint=service_crs.authid())
        materialised = vector_io.write_layer(layer, out_path, layer_name, target_crs=destination)
        self.cache.put(key, out_path, kind=cache_module.KIND_VECTOR, source_id=self.source.id,
                       area_id=area_id, meta={"truncated": truncated})
        return FetchResult(source_id=self.source.id, materialised=materialised,
                           feature_count=materialised.feature_count, truncated=truncated,
                           urls=urls, elapsed_ms=elapsed)
