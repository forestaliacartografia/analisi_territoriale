"""WFS client with explicit control over bbox, axis order, paging and caching.

The QGIS WFS provider is excellent for interactive browsing, but an analysis engine needs
three things it cannot easily guarantee: a deterministic spatial filter, a bounded number
of requests, and a materialised file it can cache and stamp with provenance. This client
therefore speaks KVP WFS directly and hands the payload to
:mod:`territorial_suite.services.vector_io`.

Axis order: WFS 2.0 with a ``urn:ogc:def:crs`` code expects latitude first for geographic
CRS. Descriptors can force the order with ``query.bbox_axis_order`` (``auto``/``xy``/``yx``)
for the services that get it wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qgis.core import QgsFeature, QgsRectangle, QgsVectorLayer

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

#: Parameter names differ between WFS 1.x and 2.0.
_PARAMS = {
    "2.0.0": {"typename": "typenames", "count": "count", "start": "startindex"},
    "1.1.0": {"typename": "typename", "count": "maxfeatures", "start": "startindex"},
    "1.0.0": {"typename": "typename", "count": "maxfeatures", "start": ""},
}

_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_NUMBER_MATCHED = re.compile(rb'numberMatched="(\d+)"')
_NEXT_LINK = re.compile(rb'\snext="([^"]+)"')


def parse_collection_info(payload: bytes) -> Dict[str, Any]:
    """Read ``numberReturned``/``numberMatched``/``next`` from a WFS 2.0 answer.

    Paging must follow the ``next`` link: a page shorter than the requested count does
    **not** mean the end of the result set on services that cap the response size.
    """
    head = payload[:8192]
    returned = _NUMBER_RETURNED.search(head)
    matched = _NUMBER_MATCHED.search(head)
    next_link = _NEXT_LINK.search(head)
    return {
        "returned": int(returned.group(1)) if returned else None,
        "matched": int(matched.group(1)) if matched else None,
        "next": next_link.group(1).decode("utf-8", "replace").replace("&amp;", "&")
        if next_link else "",
    }


@dataclass
class FetchResult:
    """Outcome of a WFS query for one bounding box."""

    source_id: str
    materialised: Optional[vector_io.MaterialisedLayer] = None
    feature_count: int = 0
    from_cache: bool = False
    truncated: bool = False
    #: Features the service said it was sending that could not be read back.
    #: A dataset with unparsable geometries must never look like an empty area.
    lost_features: int = 0
    urls: List[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def lossy(self) -> bool:
        """Whether part of the answer was dropped while parsing."""
        return self.lost_features > 0

    @property
    def has_data(self) -> bool:
        """Whether at least one feature was returned."""
        return self.feature_count > 0 and self.materialised is not None

    def layer(self, name: str = "") -> Optional[QgsVectorLayer]:
        """Open the materialised dataset as a QGIS layer."""
        if self.materialised is None:
            return None
        layer = QgsVectorLayer(self.materialised.uri, name or self.materialised.layer_name, "ogr")
        return layer if layer.isValid() else None


class WfsClient:
    """KVP WFS client for one data source."""

    def __init__(self, source: DataSource, *, http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.source = source
        self.http = HttpClient.for_source(source, http)
        self.cache = cache or CacheManager.instance()

    # ------------------------------------------------------------------ request building

    @property
    def version(self) -> str:
        """WFS version declared by the descriptor (default 2.0.0)."""
        return str(self.source.query.get("version", "2.0.0"))

    @property
    def response_axis_order(self) -> str:
        """Axis order the service writes its geometries in, as the descriptor declares it.

        Never inferred from the coordinates: over Italy latitude and longitude are both
        below 90, so a value-based rule would flip correct data as often as wrong data.
        """
        return str(self.source.query.get("response_axis_order", "auto")).lower()

    @property
    def params_names(self) -> Dict[str, str]:
        """Parameter names for the negotiated version."""
        return _PARAMS.get(self.version, _PARAMS["2.0.0"])

    def _crs_token(self, crs: CrsLike) -> str:
        """Format a CRS the way this service expects it in BBOX/SRSNAME."""
        style = str(self.source.query.get("crs_format", "auto")).lower()
        resolved = crs_utils.crs_from(crs)
        if style == "epsg":
            return resolved.authid()
        if style == "urn":
            return crs_utils.urn(resolved)
        return crs_utils.urn(resolved) if self.version.startswith("2.") else resolved.authid()

    def bbox_param(self, rect: QgsRectangle, crs: CrsLike) -> str:
        """Build the ``BBOX`` value with the right axis order, precision and CRS token."""
        # The order of the *request* is a separate fact from the order of the
        # *response*: a service may well take one and answer in the other, and the PCN
        # PAI services do exactly that. ``bbox_axis_order`` is kept as the older spelling.
        order = str(self.source.query.get("request_axis_order")
                    or self.source.query.get("bbox_axis_order", "auto")).lower()
        inverted = crs_utils.axis_inverted(crs) if order == "auto" else order == "yx"
        if inverted:
            values = [rect.yMinimum(), rect.xMinimum(), rect.yMaximum(), rect.xMaximum()]
        else:
            values = [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]
        precision = int(self.source.query.get("bbox_precision",
                                              crs_utils.bbox_precision(crs)))
        parts = [crs_utils.format_ordinate(value, precision) for value in values]
        if self.source.query.get("bbox_include_crs", True):
            parts.append(self._crs_token(crs))
        return ",".join(parts)

    def build_params(self, rect: QgsRectangle, crs: CrsLike, *, start: int = 0,
                     count: int = 0) -> Dict[str, Any]:
        """Build the KVP parameters of a ``GetFeature`` request."""
        names = self.params_names
        params: Dict[str, Any] = {
            "service": "WFS",
            "version": self.version,
            "request": "GetFeature",
            names["typename"]: self.source.layer,
        }
        if rect is not None:
            params["bbox"] = self.bbox_param(rect, crs)
        if self.source.query.get("srsname", True):
            params["srsname"] = self._crs_token(crs)
        if count:
            params[names["count"]] = int(count)
        if start and names["start"]:
            params[names["start"]] = int(start)
        output_format = self.source.query.get("output_format")
        if output_format:
            params["outputformat"] = output_format
        for key in ("filter", "cql_filter", "propertyname", "sortby"):
            value = self.source.query.get(key)
            if value:
                params[key] = value
        params.update(self.source.query.get("params", {}) or {})
        return params

    # ------------------------------------------------------------------ capabilities

    def capabilities_url(self) -> str:
        """URL of the ``GetCapabilities`` request."""
        return self.http.build_url(self.source.url, {
            "service": "WFS", "request": "GetCapabilities", "version": self.version})

    def capabilities(self, *, refresh: bool = False):
        """Fetch and parse the capabilities document (cached)."""
        from . import capabilities as caps_module

        key = make_key(self.source.id, op="capabilities", version=self.version)
        path = self.cache.path_for(cache_module.KIND_SERVICE, self.source.id, key, ".xml")
        if not refresh:
            entry = self.cache.get(key)
            if entry is not None:
                return caps_module.parse(entry.path.read_bytes())
        response = self.http.get(self.source.url, {
            "service": "WFS", "request": "GetCapabilities", "version": self.version},
            source_id=self.source.id)
        path.write_bytes(response.content)
        self.cache.put(key, path, kind=cache_module.KIND_SERVICE, source_id=self.source.id,
                       ttl_seconds=self.cache.ttl_seconds("capabilities"))
        return caps_module.parse(response.content)

    # ------------------------------------------------------------------ features

    def fetch(self, rect: QgsRectangle, rect_crs: CrsLike, *,
              target_crs: Optional[CrsLike] = None, area_id: str = "",
              max_features: Optional[int] = None, refresh: bool = False,
              feedback: Optional[Feedback] = None) -> FetchResult:
        """Download every feature intersecting ``rect`` and materialise them.

        The bounding box is reprojected to the CRS declared by the descriptor; paging is
        driven by ``query.page_size`` and stops at ``max_features`` (or the descriptor's
        own cap, or the global setting).
        """
        feedback = feedback or NullFeedback()
        service_crs = crs_utils.crs_from(self.source.bbox_crs)
        destination = crs_utils.crs_from(target_crs) if target_crs is not None else service_crs
        query_rect = crs_utils.transform_bbox(rect, rect_crs, service_crs)

        cap = int(max_features or self.source.max_features
                  or settings.get("network.max_features_per_source", 20000))
        page_size = max(1, int(self.source.page_size))

        key = self._cache_key(query_rect, service_crs, destination, cap)
        out_path = self.cache.path_for(cache_module.KIND_VECTOR, self.source.id, key, ".gpkg")
        layer_name = "data"
        if not refresh:
            entry = self.cache.get(key)
            if entry is not None and entry.path.exists():
                cached = QgsVectorLayer(f"{entry.path}|layername={layer_name}", layer_name, "ogr")
                if cached.isValid():
                    log.debug(f"{self.source.id}: cache hit ({cached.featureCount()} features)")
                    return FetchResult(
                        source_id=self.source.id,
                        materialised=vector_io.MaterialisedLayer(
                            path=entry.path, layer_name=layer_name,
                            feature_count=cached.featureCount(), crs=cached.crs().authid()),
                        feature_count=cached.featureCount(), from_cache=True,
                        truncated=bool(entry.meta.get("truncated", False)))

        features: List[QgsFeature] = []
        fields = None
        geometry_type = ""
        source_crs_authid = service_crs.authid()
        urls: List[str] = []
        truncated = False
        start = 0
        elapsed = 0
        page_index = 0
        lost = 0
        next_url = ""
        min_interval = self.source.query.get("min_interval_s")
        work_folder = vector_io.work_folder(temp_dir() / "wfs", self.source.id)

        while True:
            if feedback.is_canceled():
                raise UserCancelled()
            remaining = cap - len(features)
            if remaining <= 0:
                truncated = True
                break
            if next_url:
                response = self.http.get(next_url, source_id=self.source.id,
                                         min_interval_s=min_interval, feedback=feedback)
            else:
                params = self.build_params(query_rect, service_crs, start=start,
                                           count=min(page_size, remaining))
                response = self._get_with_fallback(params, min_interval, feedback)
            urls.append(response.url)
            elapsed += response.elapsed_ms
            if not response.content.strip():
                break
            info = parse_collection_info(response.content)
            if info["returned"] == 0:
                # An empty collection is a valid end-of-paging answer, and OGR cannot open
                # it: stop here instead of failing a query that already returned data.
                feedback.push_debug(f"{self.source.id}: empty page, paging finished")
                break
            try:
                # The window we asked for is handed down so that a service answering
                # with its axes the wrong way round can be noticed: the geometries would
                # otherwise be valid, plausible and in the wrong hemisphere.
                page_layer = vector_io.layer_from_payload(
                    response.content, work_folder, name=f"page_{page_index}",
                    content_type=response.content_type, crs_hint=service_crs.authid(),
                    response_axis_order=self.response_axis_order)
                page_features = list(page_layer.getFeatures())
            except SourceSchemaError:
                if features:
                    feedback.push_warning(
                        f"{self.source.id}: pagina {page_index} illeggibile, uso i "
                        f"{len(features)} elementi gia' ottenuti")
                    truncated = True
                    break
                raise
            announced = info["returned"]
            if announced is not None and announced > len(page_features):
                # GDAL silently drops features whose GML geometry it cannot parse
                # (SITAP publishes such UNESCO polygons). Reporting the remainder as if
                # the area were empty would be a lie, so count the loss and, when nothing
                # at all survived, fail the query instead.
                lost += announced - len(page_features)
                log.warning(f"{self.source.id}: {announced - len(page_features)} of "
                            f"{announced} features on page {page_index} could not be "
                            f"parsed")
            if fields is None and page_layer.fields():
                fields = page_layer.fields()
                geometry_type = vector_io.geometry_type_name(page_layer)
                if page_layer.crs().isValid():
                    source_crs_authid = page_layer.crs().authid()
            features.extend(page_features)
            feedback.push_debug(f"{self.source.id}: page {page_index} -> "
                                f"{len(page_features)} features "
                                f"(matched={info['matched']}, next={'yes' if info['next'] else 'no'})")
            page_index += 1
            start += len(page_features)
            if len(features) >= cap:
                truncated = True
                break
            if not page_features:
                break
            if info["next"] and info["next"] not in urls:
                next_url = info["next"]
                continue
            next_url = ""
            if len(page_features) < min(page_size, remaining):
                break
            if not self.params_names["start"]:
                truncated = len(page_features) >= page_size
                break

        if not features or fields is None:
            if lost:
                raise SourceSchemaError(
                    f"The service returned {lost} features whose geometry could not be "
                    f"read: the area is not empty, the dataset is unusable here",
                    source_id=self.source.id)
            log.debug(f"{self.source.id}: no features in the requested bbox")
            return FetchResult(source_id=self.source.id, feature_count=0, urls=urls,
                               elapsed_ms=elapsed)

        if geometry_type == "Unknown":
            raise SourceSchemaError("Downloaded dataset has no usable geometry",
                                    source_id=self.source.id)
        materialised = vector_io.write_features(
            features, fields, geometry_type, source_crs_authid, out_path, layer_name,
            target_crs=destination)
        self.cache.put(key, out_path, kind=cache_module.KIND_VECTOR, source_id=self.source.id,
                       area_id=area_id, meta={"truncated": truncated, "urls": urls[:5]})
        if truncated:
            log.warning(f"{self.source.id}: result truncated at {cap} features")
        if lost:
            feedback.push_warning(
                f"{self.source.id}: {lost} elementi non leggibili sono stati esclusi")
        return FetchResult(source_id=self.source.id, materialised=materialised,
                           feature_count=materialised.feature_count, truncated=truncated,
                           lost_features=lost, urls=urls, elapsed_ms=elapsed)

    def _get_with_fallback(self, params: Dict[str, Any], min_interval, feedback):
        """Perform a GetFeature, retrying once with a simplified request.

        Some services (verified on the Italian cadastral WFS) reject a perfectly valid
        request with *"Richiesta non valida"* intermittently, and accept the very same
        query a moment later or with fewer optional parameters. After the transport-level
        retries of :class:`HttpClient`, one degraded attempt - no ``srsname``, half the
        page size - recovers most of those cases instead of losing the source.
        """
        degraded = dict(params)
        degraded.pop("srsname", None)
        count_key = self.params_names["count"]
        if count_key in degraded:
            try:
                degraded[count_key] = max(100, int(degraded[count_key]) // 2)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                pass
        variants = [params] + ([degraded] if degraded != params else [])
        last_error: Optional[SourceSchemaError] = None
        for index, variant in enumerate(variants):
            try:
                return self.http.get(self.source.url, variant, source_id=self.source.id,
                                     min_interval_s=min_interval, feedback=feedback)
            except SourceSchemaError as exc:
                last_error = exc
                if index + 1 < len(variants):
                    feedback.push_debug(f"{self.source.id}: richiesta rifiutata dal "
                                        f"servizio, riprovo in forma semplificata")
        raise last_error

    def _cache_key(self, rect: QgsRectangle, service_crs, destination, cap: int) -> str:
        grid = float(settings.get("cache.bbox_grid_m", 100))
        if crs_utils.crs_from(service_crs).isGeographic():
            grid = grid / 111_320.0        # rough metre -> degree conversion for the grid
        return make_key(
            self.source.id,
            op="getfeature",
            typename=self.source.layer,
            version=self.version,
            bbox=quantise_bbox(rect.xMinimum(), rect.yMinimum(),
                               rect.xMaximum(), rect.yMaximum(), grid),
            bbox_crs=crs_utils.crs_from(service_crs).authid(),
            target_crs=crs_utils.crs_from(destination).authid(),
            cap=cap,
            filters={k: v for k, v in self.source.query.items()
                     if k in ("filter", "cql_filter", "propertyname", "params")},
        )
