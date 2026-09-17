"""Raster/background services: WMS, WMTS, XYZ and remote rasters.

These sources are never downloaded feature by feature: the plugin builds a QGIS raster
layer URI and lets the provider stream tiles, which is both faster and compliant with the
terms of use of the services (no scraping, no bulk copy).
"""

from __future__ import annotations

from typing import Optional
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
