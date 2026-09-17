"""Quick cartography: turn a project area (and its analysis) into a finished layout.

One call produces a print layout with map, coordinate grid, legend, north arrow, scale bar,
administrative and CRS information, data sources, date, sheet number and the wording that
keeps the reader aware of what the map is (and is not).

Templates are configuration (``config/layouts/templates.json``): the builder stacks the
declared blocks in the side panel and never hardcodes a position.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from qgis.core import (
    Qgis,
    QgsFillSymbol,
    QgsLayerTree,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLegendStyle,
    QgsMapLayer,
    QgsPrintLayout,
    QgsProject,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

from ...core import log, measure, settings
from ...core.errors import LayoutError
from ...core.constants import PLUGIN_NAME, PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from ...core.models import AnalysisReport
from ...core.paths import config_dir, resources_dir, user_dir
from ...core.project_area import ProjectArea
from . import scale as scale_engine
from .styling import SHADED_PREFIX
from .overview import ANCHOR_MAP, OverviewBuilder, OverviewSpec
from .profiles import (
    TEXT_KEYS,
    LayoutProfile,
    LegendSpec,
    ProfileStore,
)

DISCLAIMER = ("Elaborazione cartografica a fini conoscitivi. I perimetri riportati derivano "
              "dai dati cartografici delle fonti indicate e non costituiscono accertamento "
              "dei vincoli, che va verificato presso gli enti competenti.")

_TEMPLATES: Optional[Dict[str, Any]] = None


@dataclass
class MapSpec:
    """Everything the user chooses for one map."""

    template: str = "territorial_overview"
    title: str = ""
    subtitle: str = ""
    page_size: str = ""
    orientation: str = ""
    scale: Optional[int] = None
    sheet_number: str = ""
    author: str = ""
    logo_path: str = ""
    show_grid: Optional[bool] = None
    show_legend: bool = True
    show_north_arrow: bool = True
    show_scale_bar: bool = True
    basemap_source_id: Optional[str] = None
    layer_ids: List[str] = field(default_factory=list)
    style_preset: str = ""
    notes: str = ""
    #: Graphic identity applied to the sheet; empty means the default profile.
    profile_id: str = ""
    #: Values filling the ``{placeholder}`` texts of the profile.
    texts: Dict[str, str] = field(default_factory=dict)
    #: Credit line of the imagery actually used, when the sheet has one.
    attribution: str = ""
    #: Warnings the sheet must carry (a missing logo, a provider fallback).
    warnings: List[str] = field(default_factory=list)

    def merged_with_template(self, template: Dict[str, Any]) -> "MapSpec":
        """Return a copy with the template defaults filled in."""
        page = template.get("page", {})
        return MapSpec(
            template=template.get("id", self.template),
            title=self.title or template.get("title", template.get("name", "")),
            subtitle=self.subtitle,
            page_size=self.page_size or page.get("size", settings.get("cartography.page_size", "A3")),
            orientation=self.orientation or page.get(
                "orientation", settings.get("cartography.orientation", "landscape")),
            scale=self.scale,
            sheet_number=self.sheet_number,
            author=self.author or settings.get("report.author", ""),
            logo_path=self.logo_path or settings.get("cartography.logo_path", ""),
            show_grid=self.show_grid if self.show_grid is not None
            else bool(template.get("map", {}).get("grid", settings.get("cartography.grid", True))),
            show_legend=self.show_legend,
            show_north_arrow=self.show_north_arrow,
            show_scale_bar=self.show_scale_bar,
            basemap_source_id=self.basemap_source_id,
            layer_ids=list(self.layer_ids),
            style_preset=self.style_preset or settings.get("cartography.style_preset",
                                                           "professional"),
            notes=self.notes,
            profile_id=self.profile_id,
            texts=dict(self.texts),
            attribution=self.attribution,
            warnings=list(self.warnings),
        )

    def merged_with_profile(self, profile: "LayoutProfile") -> "MapSpec":
        """Apply a layout profile, without overriding what the caller asked for.

        The order is deliberate: an explicit request from the user wins over the profile,
        and the profile wins over the template defaults. That way choosing "A4" for one
        sheet does not silently change the profile for every other sheet.
        """
        merged = MapSpec(**{key: getattr(self, key) for key in
                            self.__dataclass_fields__})
        merged.profile_id = profile.id
        merged.page_size = self.page_size or profile.page_size
        merged.orientation = self.orientation or profile.orientation
        merged.style_preset = self.style_preset or profile.style_preset
        if self.show_grid is None:
            merged.show_grid = profile.grid
        merged.show_legend = self.show_legend and profile.legend.enabled
        merged.show_north_arrow = self.show_north_arrow and profile.north_arrow.enabled
        merged.show_scale_bar = self.show_scale_bar and profile.scale_bar
        merged.texts = {**profile.texts, **self.texts}
        merged.author = self.author or profile.text("author", "")
        return merged


#: Item id of the main map frame of a sheet.
MAIN_MAP_ID = "main_map"


def templates() -> Dict[str, Any]:
    """Load the layout templates (built-in plus user overrides)."""
    global _TEMPLATES
    if _TEMPLATES is None:
        path = config_dir() / "layouts" / "templates.json"
        try:
            _TEMPLATES = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
            log.warning(f"Cannot read templates.json: {exc}")
            _TEMPLATES = {"defaults": {}, "templates": [], "series": {}}
        user_file = user_dir() / "layouts" / "templates.json"
        if user_file.exists():
            try:
                overlay = json.loads(user_file.read_text(encoding="utf-8"))
                known = {item["id"]: item for item in _TEMPLATES.get("templates", [])}
                for item in overlay.get("templates", []):
                    if item.get("id"):
                        known[item["id"]] = {**known.get(item["id"], {}), **item}
                _TEMPLATES["templates"] = sorted(known.values(),
                                                 key=lambda t: t.get("order", 999))
                _TEMPLATES.setdefault("series", {}).update(overlay.get("series", {}))
                if overlay.get("defaults"):
                    _TEMPLATES["defaults"] = {**_TEMPLATES.get("defaults", {}),
                                              **overlay["defaults"]}
            except (OSError, ValueError) as exc:  # pragma: no cover - user error path
                log.warning(f"Ignoring invalid user templates.json: {exc}")
    return _TEMPLATES


def reload_templates() -> None:
    """Drop the cached templates."""
    global _TEMPLATES
    _TEMPLATES = None


def template_list() -> List[Dict[str, Any]]:
    """Return the available templates, ordered."""
    return sorted(templates().get("templates", []), key=lambda t: t.get("order", 999))


def get_template(template_id: str) -> Dict[str, Any]:
    """Return one template merged with the defaults."""
    config = templates()
    defaults = config.get("defaults", {})
    found = next((item for item in config.get("templates", [])
                  if item.get("id") == template_id), None)
    if found is None:
        found = (config.get("templates") or [{}])[0]
        log.warning(f"Unknown layout template '{template_id}', using "
                    f"'{found.get('id', 'default')}'")
    merged = {**defaults, **found}
    merged["page"] = {**defaults.get("page", {}), **found.get("page", {})}
    merged["map"] = {**defaults.get("map", {}), **found.get("map", {})}
    merged["panel"] = {**defaults.get("panel", {}), **found.get("panel", {})}
    merged["blocks"] = found.get("blocks_override") or defaults.get("blocks", [])
    return merged


def series_names() -> Dict[str, List[str]]:
    """Return the configured map series."""
    return templates().get("series", {})


class LayoutBuilder:
    """Builds print layouts for a project area."""

    def __init__(self, project: Optional[QgsProject] = None) -> None:
        self.project = project or QgsProject.instance()
        self.profile: LayoutProfile = ProfileStore.instance().default()
        #: Locator map of the last built layout, when the profile asked for one.
        self.overview_item: Optional[QgsLayoutItemMap] = None

    # ------------------------------------------------------------------ public API

    def build(self, area: ProjectArea, spec: Optional[MapSpec] = None, *,
              report: Optional[AnalysisReport] = None,
              layers: Optional[Sequence[QgsMapLayer]] = None,
              add_to_project: bool = True) -> QgsPrintLayout:
        """Create the layout and return it (already added to the project by default)."""
        spec = (spec or MapSpec())
        profile = ProfileStore.instance().resolve(spec.profile_id)
        template = get_template(spec.template)
        spec = spec.merged_with_profile(profile).merged_with_template(template)
        # A picture the user configured and that cannot be placed is reported on the
        # sheet: a silently missing logo is a sheet that comes back for reprint.
        spec.warnings = list(spec.warnings) + profile.unusable_pictures()
        if profile.blocks:
            template = {**template, "blocks": profile.blocks}
        self.profile = profile

        page_width, page_height = scale_engine.page_dimensions(spec.page_size, spec.orientation)
        margin = float(profile.margin_mm or template.get("page", {}).get(
            "margin_mm", settings.get("cartography.margin_mm", 10)))

        layout = QgsPrintLayout(self.project)
        layout.initializeDefaults()
        layout.setName(self._layout_name(area, spec))
        layout.setUnits(Qgis.LayoutUnit.Millimeters)
        page = layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(page_width, page_height, Qgis.LayoutUnit.Millimeters))

        inner_width = page_width - 2 * margin
        inner_height = page_height - 2 * margin
        map_conf = template.get("map", {})
        panel_conf = template.get("panel", {})

        map_item = self._add_map(layout, area, spec, template,
                                 x=margin + inner_width * float(map_conf.get("x_pct", 0)) / 100.0,
                                 y=margin + inner_height * float(map_conf.get("y_pct", 0)) / 100.0,
                                 width=inner_width * float(map_conf.get("w_pct", 73)) / 100.0,
                                 height=inner_height * float(map_conf.get("h_pct", 100)) / 100.0,
                                 layers=layers)
        self._add_panel(layout, area, spec, template, map_item, report,
                        x=margin + inner_width * float(panel_conf.get("x_pct", 74)) / 100.0,
                        y=margin + inner_height * float(panel_conf.get("y_pct", 0)) / 100.0,
                        width=inner_width * float(panel_conf.get("w_pct", 26)) / 100.0,
                        height=inner_height * float(panel_conf.get("h_pct", 100)) / 100.0)

        if add_to_project:
            manager = self.project.layoutManager()
            existing = manager.layoutByName(layout.name())
            if existing is not None:
                manager.removeLayout(existing)
            manager.addLayout(layout)
        return layout

    # ------------------------------------------------------------------ map

    def _add_map(self, layout: QgsPrintLayout, area: ProjectArea, spec: MapSpec,
                 template: Dict[str, Any], *, x: float, y: float, width: float,
                 height: float,
                 layers: Optional[Sequence[QgsMapLayer]]) -> QgsLayoutItemMap:
        """Add the map frame, set its CRS/extent/scale, its layers and its grid."""
        map_item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(map_item)
        # A sheet now holds more than one map frame (the locator is one too). Naming them
        # is what lets the export, the QA checks and the tests tell the main frame from
        # the inset without guessing from their size or their order.
        map_item.setId(MAIN_MAP_ID)
        map_item.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        map_item.attemptResize(QgsLayoutSize(width, height, Qgis.LayoutUnit.Millimeters))
        map_item.setCrs(area.work_crs)
        map_item.setBackgroundColor(QColor("#ffffff"))
        map_item.setFrameEnabled(bool(template.get("map", {}).get("frame", True)))

        selected = list(layers) if layers is not None else self.layers_for(template, spec)
        if selected:
            map_item.setLayers(selected)
            map_item.setKeepLayerSet(True)

        extent = area.geometry_in(area.work_crs).boundingBox()
        map_item.zoomToExtent(extent)
        chosen_scale = spec.scale or template.get("scale_hint") or scale_engine.choose_scale(
            extent, width, height)
        map_item.setScale(float(chosen_scale))
        # round(): QGIS returns e.g. 1999.996 after fitting, and int() would print 1:1.999.
        spec.scale = int(round(map_item.scale()))

        if spec.show_grid:
            self._configure_grid(map_item, area, spec)
        return map_item

    @staticmethod
    def _configure_grid(map_item: QgsLayoutItemMap, area: ProjectArea, spec: MapSpec) -> None:
        """Enable a coordinate grid with readable annotations."""
        grid = map_item.grid()
        interval = scale_engine.grid_interval(spec.scale or map_item.scale())
        grid.setEnabled(True)
        grid.setStyle(QgsLayoutItemMapGrid.GridStyle.FrameAnnotationsOnly)
        grid.setIntervalX(interval)
        grid.setIntervalY(interval)
        grid.setCrs(area.work_crs)
        grid.setAnnotationEnabled(True)
        grid.setAnnotationPrecision(0)
        grid.setFrameStyle(QgsLayoutItemMapGrid.FrameStyle.InteriorTicks)
        grid.setFrameWidth(1.6)
        for position in (QgsLayoutItemMapGrid.BorderSide.Left,
                         QgsLayoutItemMapGrid.BorderSide.Right,
                         QgsLayoutItemMapGrid.BorderSide.Top,
                         QgsLayoutItemMapGrid.BorderSide.Bottom):
            try:
                grid.setAnnotationDisplay(
                    QgsLayoutItemMapGrid.DisplayMode.ShowAll, position)
            except Exception:  # pragma: no cover - API differences
                break
        font = QFont()
        font.setPointSizeF(6.0)
        try:
            text_format = grid.annotationTextFormat()
            text_format.setSize(6.0)
            grid.setAnnotationTextFormat(text_format)
        except Exception:  # pragma: no cover - API differences
            pass
        map_item.updateBoundingRect()

    def layers_for(self, template: Dict[str, Any], spec: MapSpec) -> List[QgsMapLayer]:
        """Pick the project layers a template wants, in drawing order (top first)."""
        wanted = {c for c in template.get("layers", {}).get("categories", []) if c}
        basemap_id = spec.basemap_source_id if spec.basemap_source_id is not None \
            else template.get("layers", {}).get("basemap", "")
        terrain_style = template.get("layers", {}).get("terrain", "")

        vectors: List[QgsMapLayer] = []
        rasters: List[QgsMapLayer] = []
        shaded: List[QgsMapLayer] = []
        basemaps: List[QgsMapLayer] = []
        area_layers: List[QgsMapLayer] = []
        tree_layers = [node.layer() for node in self.project.layerTreeRoot().findLayers()
                       if node.layer() is not None]
        for layer in tree_layers:
            category = layer.customProperty(PROP_LAYER_CATEGORY, "") or ""
            source_id = layer.customProperty(PROP_LAYER_SOURCE_ID, "") or ""
            if spec.layer_ids and layer.id() not in spec.layer_ids:
                continue
            if category == "project_area":
                area_layers.append(layer)
                continue
            if source_id and basemap_id and source_id == basemap_id:
                basemaps.append(layer)
                continue
            if category == "imagery":
                if basemap_id and source_id != basemap_id:
                    continue
                basemaps.append(layer)
                continue
            if category == "terrain":
                style = (layer.customProperty("territorial_suite/style", "") or "").lower()
                name = layer.name().lower()
                if not terrain_style:
                    continue
                # A baked "shaded_<theme>" composite is preferred over the raw theme: it
                # already carries the hillshade, which a printed blend mode would lose.
                if style == f"{SHADED_PREFIX}_{terrain_style}":
                    shaded.append(layer)
                elif terrain_style in style or terrain_style in name:
                    rasters.append(layer)
                continue
            if wanted and category not in wanted:
                continue
            vectors.append(layer)
        # When a composite exists the raw theme is dropped: showing both would print the
        # unshaded version on top of the shaded one.
        terrain_layers = shaded or rasters
        return area_layers + vectors + terrain_layers + basemaps

    # ------------------------------------------------------------------ side panel

    def _add_panel(self, layout: QgsPrintLayout, area: ProjectArea, spec: MapSpec,
                   template: Dict[str, Any], map_item: QgsLayoutItemMap,
                   report: Optional[AnalysisReport], *, x: float, y: float,
                   width: float, height: float) -> None:
        """Draw the information panel: background plus the configured blocks."""
        panel_conf = template.get("panel", {})
        background = QgsLayoutItemShape(layout)
        layout.addLayoutItem(background)
        background.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        background.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        background.attemptResize(QgsLayoutSize(width, height, Qgis.LayoutUnit.Millimeters))
        symbol = QgsFillSymbol.createSimple({
            "color": panel_conf.get("background", "#ffffff"),
            "outline_color": panel_conf.get("border", "#808080"),
            "outline_width": "0.3",
        })
        background.setSymbol(symbol)

        blocks = list(template.get("blocks", []))
        padding = 3.0
        inner_x = x + padding
        inner_width = width - 2 * padding
        fixed = sum(float(block.get("h_mm", 10)) for block in blocks
                    if not block.get("flexible"))
        flexible_blocks = [block for block in blocks if block.get("flexible")]
        spare = max(height - 2 * padding - fixed - len(blocks) * 1.5, 0.0)
        cursor = y + padding

        for block in blocks:
            kind = block.get("type", "")
            block_height = float(block.get("h_mm", 10))
            if block.get("flexible") and flexible_blocks:
                block_height = max(block_height, spare / len(flexible_blocks))
            if kind in ("legend", "scalebar", "northarrow") and not self._block_enabled(kind, spec):
                continue
            self._add_block(layout, kind, block, area, spec, template, map_item, report,
                            x=inner_x, y=cursor, width=inner_width, height=block_height)
            cursor += block_height + 1.5

    @staticmethod
    def _block_enabled(kind: str, spec: MapSpec) -> bool:
        return {"legend": spec.show_legend, "scalebar": spec.show_scale_bar,
                "northarrow": spec.show_north_arrow}.get(kind, True)

    def _add_block(self, layout: QgsPrintLayout, kind: str, block: Dict[str, Any],
                   area: ProjectArea, spec: MapSpec, template: Dict[str, Any],
                   map_item: QgsLayoutItemMap, report: Optional[AnalysisReport], *,
                   x: float, y: float, width: float, height: float) -> None:
        """Add one panel block."""
        if kind == "title":
            self._label(layout, spec.title or template.get("name", ""), x, y, width, height,
                        size=float(block.get("font_size", 13)), bold=True)
        elif kind == "subtitle":
            subtitle = spec.subtitle or self._default_subtitle(area)
            self._label(layout, subtitle, x, y, width, height,
                        size=float(block.get("font_size", 8.5)))
        elif kind == "legend":
            self._legend(layout, map_item, x, y, width, height,
                         size=float(block.get("font_size", 7.5)))
        elif kind == "scalebar":
            self._scalebar(layout, map_item, spec, x, y, width, height,
                           size=float(block.get("font_size", 7)))
        elif kind == "northarrow":
            self._north_arrow(layout, x, y, width, height)
        elif kind == "info":
            self._label(layout, self._info_text(area, spec), x, y, width, height,
                        size=float(block.get("font_size", 7)))
        elif kind == "sources":
            self._label(layout, self._sources_text(report), x, y, width, height,
                        size=float(block.get("font_size", 6)))
        elif kind == "disclaimer":
            self._label(layout, DISCLAIMER, x, y, width, height,
                        size=float(block.get("font_size", 6)), italic=True)
        elif kind == "footer":
            self._label(layout, self._footer_text(spec), x, y, width, height,
                        size=float(block.get("font_size", 6.5)))
        elif kind == "logo" and spec.logo_path:
            self._picture(layout, spec.logo_path, x, y, width, height)
        elif kind == "logos":
            self._pictures(layout, self.profile.logos, x, y, width, height)
        elif kind == "images":
            self._pictures(layout, self.profile.images, x, y, width, height,
                           captions=True, size=float(block.get("font_size", 6)))
        elif kind == "overview":
            self._overview(layout, map_item, area, x, y, width, height)
        elif kind == "attribution":
            text = self._attribution_text(spec, report)
            if text:
                self._label(layout, text, x, y, width, height,
                            size=float(block.get("font_size", 6.5)))
        elif kind == "warnings":
            if spec.warnings:
                self._label(layout, "Avvertenze: " + "; ".join(spec.warnings),
                            x, y, width, height,
                            size=float(block.get("font_size", 6)), italic=True)
        elif kind == "text":
            self._label(layout, self._expand(str(block.get("value", "")), spec, area),
                        x, y, width, height, size=float(block.get("font_size", 7)))

    # ------------------------------------------------------------------ items

    @staticmethod
    def _label(layout: QgsPrintLayout, text: str, x: float, y: float, width: float,
               height: float, *, size: float = 8.0, bold: bool = False,
               italic: bool = False) -> QgsLayoutItemLabel:
        label = QgsLayoutItemLabel(layout)
        layout.addLayoutItem(label)
        label.setText(text or "")
        font = QFont()
        font.setPointSizeF(size)
        font.setBold(bold)
        font.setItalic(italic)
        try:
            label.setFont(font)
        except Exception:  # pragma: no cover - API differences
            pass
        label.setHAlign(Qt.AlignmentFlag.AlignLeft)
        label.setVAlign(Qt.AlignmentFlag.AlignTop)
        label.setMargin(1.0)
        label.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        label.attemptResize(QgsLayoutSize(width, height, Qgis.LayoutUnit.Millimeters))
        return label

    def _legend(self, layout: QgsPrintLayout, map_item: QgsLayoutItemMap, x: float,
                y: float, width: float, height: float, *, size: float = 7.5) -> None:
        config = self.profile.legend
        legend = QgsLayoutItemLegend(layout)
        layout.addLayoutItem(legend)
        legend.setTitle(config.title or "Legenda")
        legend.setLinkedMap(map_item)
        legend.setLegendFilterByMapEnabled(True)
        legend.setAutoUpdateModel(True)
        legend.updateLegend()
        legend.setAutoUpdateModel(False)
        self._prune_legend(legend, map_item, config)
        if config.columns > 1:
            try:
                legend.setColumnCount(int(config.columns))
                legend.setSplitLayer(True)
            except Exception:  # pragma: no cover - API differences
                pass
        size = config.font_size or size
        font = QFont()
        font.setPointSizeF(size)
        for style in (QgsLegendStyle.Style.Title, QgsLegendStyle.Style.Group,
                      QgsLegendStyle.Style.Subgroup, QgsLegendStyle.Style.SymbolLabel):
            try:
                title_font = QFont(font)
                title_font.setBold(style == QgsLegendStyle.Style.Title)
                title_font.setPointSizeF(size + (1.0 if style == QgsLegendStyle.Style.Title else 0))
                legend.setStyleFont(style, title_font)
            except Exception:  # pragma: no cover - API differences
                break
        try:
            legend.setResizeToContents(False)
        except AttributeError:  # pragma: no cover - older builds
            pass
        legend.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        legend.attemptResize(QgsLayoutSize(width, height, Qgis.LayoutUnit.Millimeters))

    @staticmethod
    def _prune_legend(legend: QgsLayoutItemLegend, map_item: QgsLayoutItemMap,
                      config: Optional[LegendSpec] = None) -> None:
        """Keep the legend readable, and apply the profile's own rules.

        The profile can rename entries, keep or drop layers by name, reorder them and cap
        their number. In ``custom`` mode only the layers it lists explicitly survive, so a
        sheet can carry a hand-written legend without the map losing layers.
        """
        config = config or LegendSpec()
        maximum = int(config.max_entries or
                      settings.get("cartography.legend_max_layers", 12))
        model = legend.model()
        root = model.rootGroup()
        if root is None:
            return
        allowed = {layer.id() for layer in (map_item.layers() or [])}
        include = [name.lower() for name in config.include]
        exclude = {name.lower() for name in config.exclude}
        if config.mode == "custom" and not include:
            include = [name.lower() for name in config.order] or include
        kept = 0
        for node in list(root.children()):
            if not QgsLayerTree.isLayer(node):
                continue
            layer = node.layer()
            if layer is None or (allowed and layer.id() not in allowed):
                root.removeChildNode(node)
                continue
            lowered = layer.name().lower()
            if exclude and any(token in lowered for token in exclude):
                root.removeChildNode(node)
                continue
            if include and not any(token in lowered for token in include):
                root.removeChildNode(node)
                continue
            kept += 1
            if kept > maximum:
                root.removeChildNode(node)
                continue
            renamed = LayoutBuilder._renamed(layer.name(), config)
            if renamed != layer.name():
                node.setName(renamed)
                continue
            name = layer.name()
            if len(name) > 42:
                node.setName(name[:39] + "...")
        if config.order:
            LayoutBuilder._reorder_legend(root, config.order)
        legend.updateFilterByMap()

    @staticmethod
    def _renamed(name: str, config: LegendSpec) -> str:
        """Apply the profile's rename table (exact match first, then substring)."""
        if name in config.rename:
            return config.rename[name]
        lowered = name.lower()
        for key, value in config.rename.items():
            if key.lower() in lowered:
                return value
        return name

    @staticmethod
    def _reorder_legend(root, order) -> None:
        """Put the legend entries in the order the profile lists, others after."""
        wanted = [token.lower() for token in order]

        def rank(node) -> int:
            label = (node.name() or "").lower()
            for index, token in enumerate(wanted):
                if token in label:
                    return index
            return len(wanted)

        children = list(root.children())
        ordered = sorted(children, key=rank)
        if [id(node) for node in ordered] == [id(node) for node in children]:
            return
        try:
            clones = [node.clone() for node in ordered]
            for node in children:
                root.removeChildNode(node)
            root.insertChildNodes(0, clones)
        except Exception:  # pragma: no cover - layer-tree API differences
            # Reordering is cosmetic: never risk corrupting the tree for it.
            pass

    @staticmethod
    def _scalebar(layout: QgsPrintLayout, map_item: QgsLayoutItemMap, spec: MapSpec,
                  x: float, y: float, width: float, height: float, *,
                  size: float = 7.0) -> None:
        bar = QgsLayoutItemScaleBar(layout)
        layout.addLayoutItem(bar)
        bar.applyDefaultSettings()
        bar.setLinkedMap(map_item)
        bar.setStyle("Single Box")
        try:
            bar.setUnits(Qgis.DistanceUnit.Meters)
            bar.setUnitLabel("m")
            bar.setNumberOfSegments(2)
            bar.setNumberOfSegmentsLeft(0)
            bar.setHeight(2.2)
        except Exception:  # pragma: no cover - API differences
            pass
        # Let QGIS pick a round segment length that fits the panel, otherwise the bar
        # renders as a "0 m" stub.
        try:
            from qgis.core import QgsScaleBarSettings

            bar.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFitWidth)
            bar.setMinimumBarWidth(max(width * 0.45, 10.0))
            bar.setMaximumBarWidth(max(width * 0.92, 20.0))
        except Exception:  # pragma: no cover - API differences
            segment = scale_engine.grid_interval(spec.scale or map_item.scale()) / 2.0
            try:
                bar.setUnitsPerSegment(segment)
            except Exception:
                pass
        try:
            text_format = bar.textFormat()
            text_format.setSize(size)
            bar.setTextFormat(text_format)
        except Exception:  # pragma: no cover - API differences
            pass
        bar.update()
        bar.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        bar.attemptResize(QgsLayoutSize(width, height * 0.6, Qgis.LayoutUnit.Millimeters))
        LayoutBuilder._label(layout, scale_engine.format_scale(spec.scale or map_item.scale()),
                             x, y + height * 0.62, width, height * 0.38, size=size, bold=True)

    def _pictures(self, layout: QgsPrintLayout, images, x: float, y: float,
                  width: float, height: float, *, captions: bool = False,
                  size: float = 6.0) -> None:
        """Place a row of pictures side by side, skipping the unusable ones.

        The unusable ones are not forgotten: :meth:`LayoutProfile.unusable_pictures` has
        already put them in ``spec.warnings`` so the sheet can say what is missing.
        """
        usable = [item for item in images if item.usable]
        if not usable or width <= 0:
            return
        caption_height = size * 1.6 if captions else 0.0
        gap = 2.0
        available = width - gap * (len(usable) - 1)
        declared = sum(max(item.w_pct, 1.0) for item in usable)
        cursor = x
        for item in usable:
            item_width = available * (max(item.w_pct, 1.0) / declared)
            item_height = max(height - caption_height, 1.0)
            self._picture(layout, item.path, cursor, y, item_width, item_height,
                          keep_aspect=item.keep_aspect)
            if captions and (item.caption or item.source):
                text = " - ".join(part for part in (item.caption, item.source) if part)
                self._label(layout, text, cursor, y + item_height, item_width,
                            caption_height, size=size, italic=True)
            cursor += item_width + gap

    def _overview(self, layout: QgsPrintLayout, map_item: QgsLayoutItemMap,
                  area: ProjectArea, x: float, y: float, width: float,
                  height: float) -> None:
        """Place the locator map, linked to the main map frame."""
        spec = OverviewSpec.from_settings().merged_with(self.profile.overview)
        if not spec.enabled:
            return
        builder = OverviewBuilder(self.project)
        try:
            if spec.anchor == ANCHOR_MAP:
                # Floating on a corner of the main map: it ignores the panel slot.
                self.overview_item = builder.add(layout, map_item, area, spec)
            else:
                self.overview_item = builder.add(layout, map_item, area, spec,
                                                 x=x, y=y, width=width, height=height)
        except LayoutError as exc:
            log.warning(f"Riquadro di localizzazione non inserito: {exc}")
            self.overview_item = None

    def _attribution_text(self, spec: MapSpec, report: Optional[AnalysisReport]) -> str:
        """The credit line the imagery provider requires, plus any fallback notice."""
        if not self.profile.attribution:
            return ""
        parts = [spec.attribution] if spec.attribution else []
        payload = (report.module("imagery") if report is not None else {}) or {}
        if payload:
            credit = payload.get("attribution", "")
            if credit and credit not in parts:
                parts.append(credit)
            if payload.get("fallback_used"):
                parts.append(f"Ortofoto richiesta: {payload.get('requested', '-')}; "
                             f"utilizzata: {payload.get('imagery_source', '-')}")
        return " | ".join(part for part in parts if part)

    @staticmethod
    def _expand(text: str, spec: MapSpec, area: ProjectArea) -> str:
        """Fill the ``{placeholder}`` values of a profile text."""
        if not text or "{" not in text:
            return text or ""
        values = dict(spec.texts)
        values.setdefault("author", spec.author)
        values.setdefault("date", datetime.now().strftime("%d/%m/%Y"))
        values.setdefault("sheet_number", spec.sheet_number)
        values.setdefault("scale", scale_engine.format_scale(spec.scale) if spec.scale
                          else "")
        values.setdefault("crs", area.crs_authid if hasattr(area, "crs_authid")
                          else str(area.crs.authid() if hasattr(area.crs, "authid")
                                   else ""))
        values.setdefault("locality", area.name or "")
        admin = getattr(area, "admin", None)
        if admin is not None:
            values.setdefault("municipality",
                              ", ".join(u.name for u in admin.municipalities) or "")
            values.setdefault("province",
                              ", ".join(u.name for u in admin.provinces) or "")
            values.setdefault("region",
                              ", ".join(u.name for u in admin.regions) or "")
        for key in TEXT_KEYS:
            values.setdefault(key, "")
        try:
            return text.format(**values)
        except (KeyError, IndexError, ValueError):
            # A profile written by hand may reference a placeholder that does not exist:
            # print the text as it is rather than losing it.
            return text

    @staticmethod
    def _picture(layout: QgsPrintLayout, path: str, x: float, y: float, width: float,
                 height: float, *, keep_aspect: bool = True) -> Optional[QgsLayoutItemPicture]:
        if not path or not Path(path).exists():
            return None
        picture = QgsLayoutItemPicture(layout)
        layout.addLayoutItem(picture)
        picture.setPicturePath(str(path))
        try:
            picture.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom if keep_aspect
                                  else QgsLayoutItemPicture.ResizeMode.Stretch)
        except Exception:  # pragma: no cover - API differences
            pass
        picture.attemptMove(QgsLayoutPoint(x, y, Qgis.LayoutUnit.Millimeters))
        picture.attemptResize(QgsLayoutSize(width, height, Qgis.LayoutUnit.Millimeters))
        return picture

    def _north_arrow(self, layout: QgsPrintLayout, x: float, y: float, width: float,
                     height: float) -> None:
        arrow = resources_dir() / "icons" / "north_arrow.svg"
        picture = self._picture(layout, str(arrow), x + width / 2.0 - height / 2.0, y,
                                height, height)
        if picture is None:
            self._label(layout, "N", x, y, width, height, size=12, bold=True)

    # ------------------------------------------------------------------ texts

    @staticmethod
    def _layout_name(area: ProjectArea, spec: MapSpec) -> str:
        base = spec.title or spec.template
        return f"{base} - {area.name}"[:120]

    @staticmethod
    def _default_subtitle(area: ProjectArea) -> str:
        municipalities = ", ".join(unit.name for unit in area.admin.municipalities)
        return municipalities or area.name

    @staticmethod
    def _info_text(area: ProjectArea, spec: MapSpec) -> str:
        from datetime import date

        admin = area.admin
        provinces = ", ".join(unit.name for unit in admin.provinces)
        regions = ", ".join(unit.name for unit in admin.regions)
        lines = [
            f"Area di progetto: {area.name}",
            f"Comune/i: {', '.join(u.name for u in admin.municipalities) or 'n.d.'}",
            f"Provincia: {provinces or 'n.d.'}",
            f"Regione: {regions or 'n.d.'}",
            f"Superficie: {measure.format_area(area.area_m2)}",
            f"Perimetro: {measure.format_length(area.perimeter_m)}",
            f"Sistema di riferimento: {area.work_crs.authid()}",
            f"Scala: {scale_engine.format_scale(spec.scale or 0)}",
            f"Data: {date.today().strftime('%d/%m/%Y')}",
        ]
        if spec.author:
            lines.append(f"Redatto da: {spec.author}")
        return "\n".join(lines)

    @staticmethod
    def _sources_text(report: Optional[AnalysisReport]) -> str:
        if report is None:
            return "Fonti: vedi relazione territoriale."
        seen: List[str] = []
        for result in report.all_results:
            if not result.ok or result.provenance is None:
                continue
            authority = result.provenance.authority or result.source_name
            entry = f"{result.source_name} ({authority})" if authority else result.source_name
            if entry not in seen:
                seen.append(entry)
        if not seen:
            return "Fonti: nessuna fonte disponibile."
        return "Fonti dei dati:\n- " + "\n- ".join(seen[:10])

    @staticmethod
    def _footer_text(spec: MapSpec) -> str:
        parts = [PLUGIN_NAME]
        if spec.sheet_number:
            parts.append(f"Tavola {spec.sheet_number}")
        return " | ".join(parts)
