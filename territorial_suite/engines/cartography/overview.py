"""Locator map: the small inset that says *where in the world* the sheet is.

Built on the QGIS-native pair, not on a hand-drawn rectangle:

* a second :class:`QgsLayoutItemMap` zoomed out around the project area;
* a :class:`QgsLayoutItemMapOverview` attached to that inset and **linked to the main
  map**, so QGIS itself keeps the highlighted frame in sync with the main map's extent.

The difference matters. A static rectangle drawn at build time lies the moment anyone
touches the main map — changes its scale, moves it, prints an atlas page. A linked
overview cannot go out of sync, because it is not a copy of the extent: it *is* the
extent.

Everything the inset looks like comes from configuration (the layout profile and
``config/defaults.json``); this module contains no URL, no colour literal that is not a
documented default, and no layer name.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from qgis.core import (
    Qgis,
    QgsFillSymbol,
    QgsLayoutItemMap,
    QgsLayoutItemMapOverview,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsMapLayer,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
)

from ...core import log, settings
from ...core.constants import PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from ...core.errors import LayoutError
from ...core.project_area import ProjectArea

#: Item id of the main map,
#: duplicated here to avoid importing the layout module (circular import).
MAIN_MAP_ID = "main_map"

#: Item id of the locator map frame.
OVERVIEW_MAP_ID = "locator_map"

#: Where the inset may be anchored.
ANCHOR_PANEL = "panel"
ANCHOR_MAP = "map"

#: Corners available when the inset is anchored over the main map.
CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")


@dataclass
class OverviewSpec:
    """What the locator map should look like. Pure configuration."""

    enabled: bool = True
    #: ``panel`` places it as a block in the side panel; ``map`` floats it on a corner
    #: of the main map.
    anchor: str = ANCHOR_PANEL
    corner: str = "bottom_right"
    #: Size as a share of the main map, used only when ``anchor`` is ``map``.
    width_pct: float = 22.0
    height_pct: float = 22.0
    margin_mm: float = 4.0
    #: How far the inset zooms out around the project area.
    zoom_factor: float = 12.0
    #: Fixed scale for the inset; when set it wins over ``zoom_factor``.
    scale: Optional[int] = None
    #: Background source id; empty means "the first operational basemap".
    basemap_source_id: str = ""
    #: Extra categories drawn in the inset (administrative boundaries, typically).
    categories: List[str] = field(default_factory=lambda: ["administrative"])
    frame: bool = True
    frame_color: str = "#d32f2f"
    frame_width: float = 0.5
    fill_color: str = "#d32f2f33"
    centered: bool = True
    inverted: bool = False

    @classmethod
    def from_dict(cls, payload: Any) -> "OverviewSpec":
        """Build from a JSON fragment, tolerating a bare boolean."""
        if isinstance(payload, bool):
            return cls(enabled=payload)
        data = payload if isinstance(payload, dict) else {}
        known = {f for f in cls.__dataclass_fields__}
        spec = cls(**{k: v for k, v in data.items() if k in known})
        if spec.anchor not in (ANCHOR_PANEL, ANCHOR_MAP):
            spec.anchor = ANCHOR_PANEL
        if spec.corner not in CORNERS:
            spec.corner = "bottom_right"
        spec.zoom_factor = max(1.5, float(spec.zoom_factor))
        return spec

    @classmethod
    def from_settings(cls) -> "OverviewSpec":
        """Defaults from ``config/defaults.json``, overridable per profile."""
        return cls.from_dict(settings.get("cartography.overview", {}) or {})

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    def merged_with(self, overlay: Any) -> "OverviewSpec":
        """Apply a profile-level override on top of these defaults."""
        if overlay is None:
            return self
        if isinstance(overlay, bool):
            return OverviewSpec.from_dict({**self.as_dict(), "enabled": overlay})
        if not isinstance(overlay, dict):
            return self
        return OverviewSpec.from_dict({**self.as_dict(), **overlay})


class OverviewBuilder:
    """Adds the locator map to a layout."""

    def __init__(self, project: Optional[QgsProject] = None) -> None:
        self.project = project or QgsProject.instance()

    # ------------------------------------------------------------------ layers

    def layers_for(self, spec: OverviewSpec) -> List[QgsMapLayer]:
        """Pick what the inset shows: context boundaries over a background.

        Deliberately sparse. A locator map crowded with the same layers as the main map
        answers no question that the main map has not already answered.
        """
        wanted = {name for name in spec.categories if name}
        context: List[QgsMapLayer] = []
        basemaps: List[QgsMapLayer] = []
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            if layer is None or not layer.isValid():
                continue
            category = layer.customProperty(PROP_LAYER_CATEGORY, "") or ""
            source_id = layer.customProperty(PROP_LAYER_SOURCE_ID, "") or ""
            if category == "imagery":
                if spec.basemap_source_id and source_id != spec.basemap_source_id:
                    continue
                basemaps.append(layer)
            elif category in wanted:
                context.append(layer)
        return context + basemaps

    # ------------------------------------------------------------------ extent

    @staticmethod
    def extent_for(area: ProjectArea, spec: OverviewSpec) -> QgsRectangle:
        """Zoom out around the area by ``zoom_factor``, keeping it centred."""
        extent = QgsRectangle(area.geometry_in(area.work_crs).boundingBox())
        if extent.isEmpty():
            raise LayoutError("L'area di progetto non ha un'estensione utilizzabile")
        extent.scale(float(spec.zoom_factor))
        return extent

    # ------------------------------------------------------------------ geometry

    @staticmethod
    def frame_rect(main_map: QgsLayoutItemMap, spec: OverviewSpec) -> tuple:
        """Position and size of an inset floating on a corner of the main map."""
        map_x = main_map.pagePos().x()
        map_y = main_map.pagePos().y()
        map_width = main_map.rect().width()
        map_height = main_map.rect().height()
        width = map_width * max(5.0, float(spec.width_pct)) / 100.0
        height = map_height * max(5.0, float(spec.height_pct)) / 100.0
        margin = float(spec.margin_mm)
        left = map_x + margin
        top = map_y + margin
        if spec.corner.endswith("right"):
            left = map_x + map_width - width - margin
        if spec.corner.startswith("bottom"):
            top = map_y + map_height - height - margin
        return left, top, width, height

    # ------------------------------------------------------------------ build

    def add(self, layout: QgsPrintLayout, main_map: QgsLayoutItemMap,
            area: ProjectArea, spec: OverviewSpec, *,
            x: Optional[float] = None, y: Optional[float] = None,
            width: Optional[float] = None, height: Optional[float] = None,
            layers: Optional[Sequence[QgsMapLayer]] = None
            ) -> Optional[QgsLayoutItemMap]:
        """Create the inset and link its overview to ``main_map``.

        **Main thread only**: it builds layout items.

        :returns: the inset map item, or ``None`` when the spec disables it.
        :raises LayoutError: when the area has no usable extent.
        """
        if not spec.enabled or main_map is None:
            return None
        if x is None or y is None or width is None or height is None:
            x, y, width, height = self.frame_rect(main_map, spec)
        if width <= 0 or height <= 0:
            return None

        inset = QgsLayoutItemMap(layout)
        layout.addLayoutItem(inset)
        inset.setId(OVERVIEW_MAP_ID)
        inset.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        inset.attemptResize(QgsLayoutSize(width, height, Qgis.LayoutUnit.Millimeters))
        inset.setCrs(main_map.crs())
        inset.setFrameEnabled(True)

        selected = list(layers) if layers is not None else self.layers_for(spec)
        if selected:
            inset.setLayers(selected)
            inset.setKeepLayerSet(True)

        inset.zoomToExtent(self.extent_for(area, spec))
        if spec.scale:
            inset.setScale(float(spec.scale))

        self._attach_overview(inset, main_map, spec)
        return inset

    def _attach_overview(self, inset: QgsLayoutItemMap, main_map: QgsLayoutItemMap,
                         spec: OverviewSpec) -> Optional[QgsLayoutItemMapOverview]:
        """Attach the linked overview that highlights the main map's extent."""
        try:
            stack = inset.overviews()
            overview = stack.overview(0) if stack.size() else None
            if overview is None:
                overview = QgsLayoutItemMapOverview("localizzazione", inset)
                stack.addOverview(overview)
            overview.setEnabled(True)
            overview.setLinkedMap(main_map)
            overview.setCentered(bool(spec.centered))
            overview.setInverted(bool(spec.inverted))
            if spec.frame:
                overview.setFrameSymbol(QgsFillSymbol.createSimple({
                    "color": spec.fill_color,
                    "outline_color": spec.frame_color,
                    "outline_width": str(spec.frame_width),
                }))
        except Exception as exc:  # pragma: no cover - layout API differences
            # An inset without its linked frame still locates the area, so the sheet is
            # usable; but a silent downgrade would be indistinguishable from a working
            # overview, so it is logged.
            log.warning(f"Riquadro di localizzazione senza cornice collegata: {exc}")
            return None
        return overview


def main_map(layout: QgsPrintLayout) -> Optional[QgsLayoutItemMap]:
    """The main map frame of a sheet, told apart from the locator by its item id."""
    fallback = None
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        if item.id() == OVERVIEW_MAP_ID:
            continue
        if item.id() == MAIN_MAP_ID:
            return item
        fallback = fallback or item
    return fallback


def locator_map(layout: QgsPrintLayout) -> Optional[QgsLayoutItemMap]:
    """The locator map of a sheet, when the profile asked for one."""
    for item in layout.items():
        if isinstance(item, QgsLayoutItemMap) and item.id() == OVERVIEW_MAP_ID:
            return item
    return None


def linked_overviews(layout: QgsPrintLayout) -> List[QgsLayoutItemMapOverview]:
    """Every overview of a layout that is enabled and really linked to a map."""
    found: List[QgsLayoutItemMapOverview] = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        stack = item.overviews()
        for index in range(stack.size()):
            overview = stack.overview(index)
            if overview is not None and overview.enabled() and \
                    overview.linkedMap() is not None:
                found.append(overview)
    return found
