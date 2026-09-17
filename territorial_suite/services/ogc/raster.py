"""Raster/background services: WMS, WMTS, XYZ and remote rasters.

These sources are never downloaded feature by feature: the plugin builds a QGIS raster
layer URI and lets the provider stream tiles, which is both faster and compliant with the
terms of use of the services (no scraping, no bulk copy).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import quote

from qgis.core import QgsRasterLayer

from ...core import crs as crs_utils
from ...core.errors import SourceUnavailableError
from ...core.registry import DataSource


def build_uri(source: DataSource, *, crs: Optional[str] = None) -> str:
    """Build the provider URI for a raster source."""
    query = source.query or {}
    target_crs = crs or (source.crs[0] if source.crs else crs_utils.WEB_MERCATOR)
    if source.type.value == "XYZ":
        parts = [f"type=xyz", f"url={quote(source.url, safe='')}"]
        parts.append(f"zmin={int(query.get('zmin', 0))}")
        parts.append(f"zmax={int(query.get('zmax', 19))}")
        if query.get("tile_size"):
            parts.append(f"tilePixelRatio={int(query.get('tile_pixel_ratio', 1))}")
        if source.attribution:
            parts.append(f"referer={quote(query.get('referer', ''), safe='')}")
        return "&".join(part for part in parts if not part.endswith("="))
    if source.type.value == "WMTS":
        params = {
            "contextualWMSLegend": "0",
            "crs": target_crs,
            "format": query.get("format", "image/png"),
            "layers": source.layer,
            "styles": query.get("style", ""),
            "tileMatrixSet": query.get("tile_matrix_set", ""),
            "url": source.url,
        }
        return "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
    if source.type.value == "WMS":
        params = {
            "contextualWMSLegend": "0",
            "crs": target_crs,
            "dpiMode": str(query.get("dpi_mode", 7)),
            "format": query.get("format", "image/png"),
            "layers": source.layer,
            "styles": query.get("style", ""),
            "url": source.url,
        }
        if query.get("version"):
            params["version"] = str(query["version"])
        return "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
    if source.type.value == "RASTER":
        return source.url
    raise SourceUnavailableError(f"Source {source.id} is not a raster service",
                                 source_id=source.id)


def provider_for(source: DataSource) -> str:
    """Return the QGIS raster provider key for a source type."""
    return {"XYZ": "wms", "WMS": "wms", "WMTS": "wms", "RASTER": "gdal"}.get(
        source.type.value, "wms")


def build_layer(source: DataSource, *, name: str = "", crs: Optional[str] = None) -> QgsRasterLayer:
    """Build a raster layer for a background/imagery source.

    :raises SourceUnavailableError: when the provider refuses the URI.
    """
    uri = build_uri(source, crs=crs)
    layer = QgsRasterLayer(uri, name or source.name, provider_for(source))
    if not layer.isValid():
        raise SourceUnavailableError(f"Cannot open raster service {source.name}",
                                     source_id=source.id, detail=uri[:200])
    return layer


#: Value of ``INFO_FORMAT`` tried first; MapServer answers this everywhere the plugin
#: has probed, and the plain-text shape is far easier to read back than the XML one.
INFO_FORMAT = "text/plain"

#: A MapServer plain-text ``GetFeatureInfo`` line: two spaces, a name, an equals sign.
_INFO_LINE = re.compile(r"^\s+(\w[\w.]*)\s*=\s*'?(.*?)'?\s*$")


def feature_info(source: DataSource, point, point_crs, *,
                 http: Optional["object"] = None, pixels: int = 101,
                 span_m: float = 60.0, feedback=None) -> List[Dict[str, str]]:
    """Ask a WMS what it has at one point.

    This is how a *view-only* service can still be used as evidence. It is deliberately
    not a substitute for a download: the answer describes one point, so it supports a
    statement like "the official layer reports a perimeter here" and never a surface or a
    percentage. Reading a measurement out of it would be exactly the error the plugin
    exists to avoid.

    :param point: a :class:`QgsPointXY` in ``point_crs``.
    :returns: one dictionary per feature the service reports, possibly empty.
    :raises SourceUnavailableError: when the service is not a WMS or refuses the request.
    """
    from ..http import HttpClient

    if source.type.value not in ("WMS", "WMTS"):
        raise SourceUnavailableError(
            f"{source.id} non e' un servizio WMS: GetFeatureInfo non e' applicabile",
            source_id=source.id)

    query = source.query or {}
    service_crs = crs_utils.crs_from(
        query.get("info_crs") or (source.crs[0] if source.crs else crs_utils.WGS84))
    located = crs_utils.transform_point(point, point_crs, service_crs)

    # A square window centred on the point: the service needs an extent and a pixel, not
    # a coordinate. The span is in the service's own units when those are degrees.
    half = span_m / 2.0
    if service_crs.isGeographic():
        half = half / 111_320.0
    bbox = [located.x() - half, located.y() - half,
            located.x() + half, located.y() + half]
    if crs_utils.axis_inverted(service_crs) and str(
            query.get("info_axis_order", "auto")).lower() != "xy":
        bbox = [bbox[1], bbox[0], bbox[3], bbox[2]]

    middle = pixels // 2
    params = {
        "service": "WMS",
        "version": str(query.get("version", "1.3.0")),
        "request": "GetFeatureInfo",
        "layers": source.layer,
        "query_layers": query.get("query_layers", source.layer),
        "crs": service_crs.authid(),
        "bbox": ",".join(f"{value:.6f}" for value in bbox),
        "width": str(pixels), "height": str(pixels),
        "i": str(middle), "j": str(middle),
        "info_format": query.get("info_format", INFO_FORMAT),
        "feature_count": str(int(query.get("feature_count", 10))),
    }
    if params["version"].startswith("1.1"):
        # WMS 1.1.1 spells three of these differently.
        params["srs"] = params.pop("crs")
        params["x"], params["y"] = params.pop("i"), params.pop("j")
    params.update(query.get("info_params", {}) or {})

    client = http or HttpClient()
    response = client.get(source.url, params, source_id=source.id, feedback=feedback)
    return parse_feature_info(response.content)


def parse_feature_info(payload: bytes) -> List[Dict[str, str]]:
    """Read a MapServer plain-text ``GetFeatureInfo`` body into a list of records."""
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in text.splitlines():
        if line.strip().startswith("Feature "):
            if current:
                records.append(current)
            current = {}
            continue
        if line.strip().startswith("Layer "):
            if current:
                records.append(current)
                current = {}
            continue
        match = _INFO_LINE.match(line)
        if match:
            current[match.group(1)] = match.group(2).strip()
    if current:
        records.append(current)
    return [r for r in records if r]
