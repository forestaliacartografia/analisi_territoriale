"""OGC API - Features client (GeoJSON, link-driven paging)."""

from __future__ import annotations

import json
from typing import List, Optional

from qgis.core import QgsRectangle, QgsVectorLayer

from ...core import cache as cache_module
from ...core import crs as crs_utils
from ...core import log, settings
from ...core.cache import CacheManager, make_key, quantise_bbox
from ...core.crs import CrsLike
from ...core.errors import SourceSchemaError, UserCancelled
from ...core.feedback import Feedback, NullFeedback
from ...core.paths import temp_dir
from ...core.registry import DataSource
from .. import vector_io
from ..http import HttpClient
from .wfs import FetchResult


class OgcApiClient:
    """Client for an OGC API - Features collection."""

    def __init__(self, source: DataSource, *, http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.source = source
        self.http = HttpClient.for_source(source, http)
        self.cache = cache or CacheManager.instance()

    def items_url(self) -> str:
        """Return the ``/items`` endpoint of the configured collection."""
        base = self.source.url.rstrip("/")
        if base.endswith("/items"):
            return base
        if self.source.layer:
            if "/collections/" in base:
                return f"{base}/items"
            return f"{base}/collections/{self.source.layer}/items"
        return f"{base}/items"

    def fetch(self, rect: QgsRectangle, rect_crs: CrsLike, *,
              target_crs: Optional[CrsLike] = None, area_id: str = "",
              max_features: Optional[int] = None, refresh: bool = False,
              feedback: Optional[Feedback] = None) -> FetchResult:
        """Download the features intersecting ``rect`` following ``next`` links."""
        feedback = feedback or NullFeedback()
        # OGC API Features uses CRS84 (lon/lat) for the bbox parameter by default.
        bbox_crs = crs_utils.crs_from(self.source.query.get("bbox_crs", crs_utils.WGS84))
        destination = crs_utils.crs_from(target_crs) if target_crs is not None else bbox_crs
        query_rect = crs_utils.transform_bbox(rect, rect_crs, bbox_crs)
        cap = int(max_features or self.source.max_features
                  or settings.get("network.max_features_per_source", 20000))
        page_size = max(1, int(self.source.page_size))

        key = make_key(self.source.id, op="ogcapi", collection=self.source.layer,
                       bbox=quantise_bbox(query_rect.xMinimum(), query_rect.yMinimum(),
                                          query_rect.xMaximum(), query_rect.yMaximum(),
                                          float(settings.get("cache.bbox_grid_m", 100)) / 111_320.0),
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
        url = self.items_url()
        precision = crs_utils.bbox_precision(bbox_crs)
        params = {
            "bbox": ",".join(crs_utils.format_ordinate(value, precision)
                             for value in (query_rect.xMinimum(), query_rect.yMinimum(),
                                           query_rect.xMaximum(), query_rect.yMaximum())),
            "limit": min(page_size, cap),
            "f": self.source.query.get("format", "json"),
        }
        if bbox_crs.authid() not in ("EPSG:4326", "OGC:CRS84"):
            params["bbox-crs"] = f"http://www.opengis.net/def/crs/EPSG/0/{bbox_crs.postgisSrid()}"
        params.update(self.source.query.get("params", {}) or {})

        while url:
            if feedback.is_canceled():
                raise UserCancelled()
            response = self.http.get(url, params, accept="application/geo+json",
                                     source_id=self.source.id, feedback=feedback,
                                     min_interval_s=self.source.query.get("min_interval_s"))
            urls.append(response.url)
            elapsed += response.elapsed_ms
            try:
                payload = json.loads(response.text())
            except ValueError as exc:
                raise SourceSchemaError("Invalid GeoJSON payload", source_id=self.source.id,
                                        detail=str(exc)) from exc
            features = payload.get("features", [])
            collected.extend(features)
            feedback.push_debug(f"{self.source.id}: {len(features)} features "
                                f"({len(collected)} total)")
            if len(collected) >= cap:
                truncated = True
                break
            url = ""
            params = None
            for link in payload.get("links", []):
                if link.get("rel") == "next" and link.get("href"):
                    url = link["href"]
                    break
            if not features:
                break

        if not collected:
            return FetchResult(source_id=self.source.id, feature_count=0, urls=urls,
                               elapsed_ms=elapsed)

        document = json.dumps({"type": "FeatureCollection", "features": collected[:cap]})
        work_folder = vector_io.work_folder(temp_dir() / "ogcapi", self.source.id)
        layer = vector_io.layer_from_payload(document.encode("utf-8"), work_folder,
                                             name="items", content_type="application/geo+json",
                                             crs_hint=crs_utils.WGS84)
        materialised = vector_io.write_layer(layer, out_path, layer_name, target_crs=destination)
        self.cache.put(key, out_path, kind=cache_module.KIND_VECTOR, source_id=self.source.id,
                       area_id=area_id, meta={"truncated": truncated})
        log.debug(f"{self.source.id}: {materialised.feature_count} features materialised")
        return FetchResult(source_id=self.source.id, materialised=materialised,
                           feature_count=materialised.feature_count, truncated=truncated,
                           urls=urls, elapsed_ms=elapsed)
