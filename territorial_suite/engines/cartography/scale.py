"""Automatic scale and extent computation.

Given the project area and the size of the map frame on the page, pick the first *round*
scale (from the configured list) at which the area fits with a margin, and build the
corresponding extent. This is the step that usually costs the user several attempts.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from qgis.core import QgsPointXY, QgsRectangle

from ...core import settings

#: ISO page sizes in millimetres (portrait).
PAGE_SIZES: Dict[str, Tuple[float, float]] = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
}

LANDSCAPE = "landscape"
PORTRAIT = "portrait"


def page_dimensions(page_size: str, orientation: str = PORTRAIT) -> Tuple[float, float]:
    """Return ``(width_mm, height_mm)`` for a page size and orientation."""
    width, height = PAGE_SIZES.get(str(page_size).upper(), PAGE_SIZES["A4"])
    if str(orientation).lower() == LANDSCAPE:
        return height, width
    return width, height


def configured_scales() -> List[int]:
    """Return the list of round scales the engine may choose from."""
    values = settings.get("cartography.scales", [500, 1000, 2000, 5000, 10000, 25000,
                                                 50000, 100000])
    return sorted(int(value) for value in values if int(value) > 0)


def choose_scale(extent: QgsRectangle, map_width_mm: float, map_height_mm: float, *,
                 margin_factor: float = 1.08,
                 scales: Optional[List[int]] = None) -> int:
    """Return the smallest configured scale at which ``extent`` fits in the map frame.

    ``extent`` must be expressed in a metric CRS. ``margin_factor`` leaves a little air
    around the area so that it never touches the frame.
    """
    candidates = scales or configured_scales()
    width_m = max(extent.width(), 1.0) * margin_factor
    height_m = max(extent.height(), 1.0) * margin_factor
    needed = max(width_m / (map_width_mm / 1000.0), height_m / (map_height_mm / 1000.0))
    for scale in candidates:
        if scale >= needed:
            return scale
    # Larger than every configured scale: round up to a clean value.
    magnitude = 10 ** int(math.floor(math.log10(max(needed, 1.0))))
    return int(math.ceil(needed / magnitude) * magnitude)


def extent_for_scale(centre: QgsPointXY, scale: float, map_width_mm: float,
                     map_height_mm: float) -> QgsRectangle:
    """Return the map extent (metric CRS) that renders at exactly ``scale``."""
    half_width = (map_width_mm / 1000.0) * scale / 2.0
    half_height = (map_height_mm / 1000.0) * scale / 2.0
    return QgsRectangle(centre.x() - half_width, centre.y() - half_height,
                        centre.x() + half_width, centre.y() + half_height)


def format_scale(scale: float) -> str:
    """Format a scale the Italian way: ``1:10.000``."""
    return "1:" + f"{int(round(scale)):,}".replace(",", ".")


def grid_interval(scale: float) -> float:
    """Return a sensible coordinate-grid interval (metres) for a scale."""
    target = scale * 0.05          # about 5 cm on the page
    steps = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000,
             20000, 25000, 50000, 100000]
    for step in steps:
        if step >= target:
            return float(step)
    return float(steps[-1])


def contour_interval(scale: float) -> float:
    """Return a readable contour interval (metres) for a scale."""
    table = [(1000, 1.0), (2000, 2.0), (5000, 5.0), (10000, 10.0), (25000, 25.0),
             (50000, 50.0), (100000, 100.0)]
    for limit, interval in table:
        if scale <= limit:
            return interval
    return 200.0
