"""One entry point for "give me the features of this source inside this bbox".

Engines never instantiate a protocol client themselves: they call :func:`fetch_features`
and get a :class:`FetchResult` back, whatever the underlying protocol is. Adding a new
protocol therefore means adding a client and one line here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from qgis.core import QgsRectangle, QgsVectorLayer

from ..core import cache as cache_module
from ..core import crs as crs_utils
from ..core import settings
from ..core.cache import CacheManager, make_key, quantise_bbox
from ..core.crs import CrsLike
from ..core.errors import SourceSchemaError, SourceUnavailableError
from ..core.feedback import Feedback, NullFeedback
from ..core.models import SourceType
from ..core.registry import DataSource
from . import vector_io
from .arcgis import ArcGisClient
from .http import HttpClient
from .ogc.ogcapi import OgcApiClient
from .ogc.wfs import FetchResult, WfsClient
from .overpass import OverpassClient


def supports_features(source: DataSource) -> bool:
    """Whether features can be downloaded and measured from this source."""
    return source.type.is_vector_query


def client_for(source: DataSource, *, http: Optional[HttpClient] = None,
               cache: Optional[CacheManager] = None):
    """Return the protocol client handling ``source``."""
    if source.type == SourceType.WFS:
        return WfsClient(source, http=http, cache=cache)
    if source.type == SourceType.OGCAPI:
        return OgcApiClient(source, http=http, cache=cache)
    if source.type == SourceType.ARCGIS_FEATURE:
        return ArcGisClient(source, http=http, cache=cache)
    if source.type == SourceType.OVERPASS:
        return OverpassClient(source, http=http, cache=cache)
    if source.type in (SourceType.GEOJSON, SourceType.GPKG, SourceType.FILE):
        return FileClient(source, http=http, cache=cache)
    raise SourceUnavailableError(f"Source type {source.type.value} cannot be queried "
                                 f"for features", source_id=source.id)


def fetch_features(source: DataSource, rect: QgsRectangle, rect_crs: CrsLike, *,
                   target_crs: Optional[CrsLike] = None, area_id: str = "",
                   max_features: Optional[int] = None, refresh: bool = False,
                   feedback: Optional[Feedback] = None,
                   http: Optional[HttpClient] = None,
                   cache: Optional[CacheManager] = None) -> FetchResult:
    """Download the features of ``source`` intersecting ``rect``."""
    return client_for(source, http=http, cache=cache).fetch(
        rect, rect_crs, target_crs=target_crs, area_id=area_id,
        max_features=max_features, refresh=refresh, feedback=feedback)


class FileClient:
    """Client for whole-file sources (GeoJSON/GPKG/any OGR file, local or remote).

    The file is downloaded once, cached, then filtered locally by bounding box. This is the
    fallback for authorities that publish a dataset instead of a service; the size guard
    keeps the plugin from pulling a national dataset by accident.
    """

    def __init__(self, source: DataSource, *, http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.source = source
        self.http = HttpClient.for_source(source, http)
        self.cache = cache or CacheManager.instance()

    def _local_path(self, feedback: Feedback) -> Path:
        """Return the local path of the dataset, downloading it when needed."""
        if not self.source.url or self.source.url.startswith("file://"):
            path = Path(self.source.url.replace("file://", "")) if self.source.url \
                else Path(self.source.query.get("path", ""))
            if not path.exists():
                raise SourceUnavailableError(f"File not found: {path}", source_id=self.source.id)
            return path
        key = make_key(self.source.id, op="download", url=self.source.url)
        suffix = Path(self.source.url.split("?")[0]).suffix or ".dat"
        entry = self.cache.get(key)
        if entry is not None and entry.path.exists():
            return entry.path
        feedback.push_debug(f"{self.source.id}: downloading dataset")
        response = self.http.get(self.source.url, source_id=self.source.id, feedback=feedback)
        path = self.cache.path_for(cache_module.KIND_VECTOR, self.source.id, key, suffix)
        path.write_bytes(vector_io.sanitise_gml(response.content)
                         if suffix == ".gml" else response.content)
        self.cache.put(key, path, kind=cache_module.KIND_VECTOR, source_id=self.source.id,
                       ttl_seconds=self.cache.ttl_seconds("vector"))
        return path

    def fetch(self, rect: QgsRectangle, rect_crs: CrsLike, *,
              target_crs: Optional[CrsLike] = None, area_id: str = "",
              max_features: Optional[int] = None, refresh: bool = False,
              feedback: Optional[Feedback] = None) -> FetchResult:
        """Open the dataset and extract the features intersecting ``rect``."""
        feedback = feedback or NullFeedback()
        path = self._local_path(feedback)
        uri = f"{path}|layername={self.source.layer}" if self.source.layer else str(path)
        layer = QgsVectorLayer(uri, self.source.name, "ogr")
        if not layer.isValid():
            raise SourceSchemaError(f"Cannot open dataset {path.name}", source_id=self.source.id)
        source_crs = layer.crs() if layer.crs().isValid() else \
            crs_utils.crs_from(self.source.bbox_crs)
        destination = crs_utils.crs_from(target_crs) if target_crs is not None else source_crs
        query_rect = crs_utils.transform_bbox(rect, rect_crs, source_crs)
        cap = int(max_features or self.source.max_features
                  or settings.get("network.max_features_per_source", 20000))

        features = []
        for index, feature in enumerate(vector_io.features_in(layer, query_rect)):
            if index >= cap:
                break
            features.append(feature)
        if not features:
            return FetchResult(source_id=self.source.id, feature_count=0)

        grid = float(settings.get("cache.bbox_grid_m", 100))
        if source_crs.isGeographic():
            grid /= 111_320.0
        key = make_key(self.source.id, op="file_extract", layer=self.source.layer,
                       bbox=quantise_bbox(query_rect.xMinimum(), query_rect.yMinimum(),
                                          query_rect.xMaximum(), query_rect.yMaximum(), grid),
                       target_crs=destination.authid(), cap=cap)
        out_path = self.cache.path_for(cache_module.KIND_VECTOR, self.source.id, key, ".gpkg")
        materialised = vector_io.write_features(
            features, layer.fields(), vector_io.geometry_type_name(layer),
            source_crs.authid(), out_path, "data", target_crs=destination)
        self.cache.put(key, out_path, kind=cache_module.KIND_VECTOR,
                       source_id=self.source.id, area_id=area_id)
        return FetchResult(source_id=self.source.id, materialised=materialised,
                           feature_count=materialised.feature_count,
                           truncated=len(features) >= cap)
