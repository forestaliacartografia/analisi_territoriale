"""Putting analysis results into the QGIS project.

Loading a layer, naming it, putting it in the right group, applying the style and stamping
its provenance is exactly the repetitive sequence the plugin exists to remove. Everything
happens here, in one place, so the GUI, Processing and the package builder behave the same.

This module touches the *project* (layer tree), never the GUI: it must run in the main
thread but needs no ``iface``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from qgis.core import (
    QgsLayerTreeGroup,
    QgsMapLayer,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from ..core import log, provenance as provenance_module
from ..core.constants import (
    GROUP_ROOT,
    PROP_LAYER_AREA_ID,
    PROP_LAYER_CATEGORY,
    PROP_LAYER_SOURCE_ID,
)
from ..core.models import AnalysisReport, LayerRef, Provenance
from ..core.project_area import ProjectArea
from ..core.registry import DataSourceRegistry
from ..core.taxonomy import Taxonomy
from .cartography import styling

AREA_CATEGORY = "project_area"
STYLE_PROPERTY = "territorial_suite/style"


@dataclass
class AppliedLayers:
    """Layers added to the project by one run."""

    layers: List[QgsMapLayer] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def ids(self) -> List[str]:
        """Ids of the layers added."""
        return [layer.id() for layer in self.layers]


class LayerApplier:
    """Loads analysis outputs into the QGIS project, styled and documented."""

    def __init__(self, project: Optional[QgsProject] = None, *,
                 registry: Optional[DataSourceRegistry] = None) -> None:
        self.project = project or QgsProject.instance()
        self.registry = registry or DataSourceRegistry.instance()

    # ------------------------------------------------------------------ groups

    def root_group(self) -> QgsLayerTreeGroup:
        """Return (creating it if needed) the plugin root group."""
        root = self.project.layerTreeRoot()
        group = root.findGroup(GROUP_ROOT)
        if group is None:
            group = root.insertGroup(0, GROUP_ROOT)
        return group

    def group(self, name: str) -> QgsLayerTreeGroup:
        """Return (creating it if needed) a sub-group of the plugin group."""
        parent = self.root_group()
        if not name:
            return parent
        group = parent.findGroup(name)
        if group is None:
            group = parent.addGroup(name)
        return group

    # ------------------------------------------------------------------ layers

    def add_area_layer(self, area: ProjectArea) -> QgsMapLayer:
        """Add (or refresh) the layer representing the project area itself."""
        name = f"Area di progetto - {area.name}"
        existing = self._find_existing(name, area.id, AREA_CATEGORY)
        if existing is not None:
            self.project.removeMapLayer(existing.id())
        layer = area.to_memory_layer(name)
        layer.setCustomProperty(PROP_LAYER_CATEGORY, AREA_CATEGORY)
        layer.setCustomProperty(PROP_LAYER_AREA_ID, area.id)
        layer.setCustomProperty(STYLE_PROPERTY, "project_area")
        styling.apply_style(layer, "project_area")
        provenance_module.stamp(layer, Provenance(
            source_id="project_area", source_name="Area di progetto",
            operation=f"created from {area.source_kind}", crs=area.crs.authid()),
            area_id=area.id, category=AREA_CATEGORY)
        self.project.addMapLayer(layer, False)
        self.group("").insertLayer(0, layer)
        return layer

    def add_layer_ref(self, ref: LayerRef, *, area_id: str = "") -> Optional[QgsMapLayer]:
        """Load one produced layer (vector or raster) into the project."""
        if not ref.uri:
            return None
        name = ref.name or Path(ref.uri).stem
        if ref.is_raster or ref.provider == "gdal":
            layer: QgsMapLayer = QgsRasterLayer(ref.uri, name, "gdal")
        else:
            layer = QgsVectorLayer(ref.uri, name, ref.provider or "ogr")
        if not layer.isValid():
            log.warning(f"Cannot load layer {name} ({ref.uri})")
            return None
        style_name = ref.style or styling.style_for_category(ref.category)
        layer.setCustomProperty(PROP_LAYER_CATEGORY, ref.category)
        layer.setCustomProperty(PROP_LAYER_SOURCE_ID, ref.source_id)
        layer.setCustomProperty(STYLE_PROPERTY, style_name)
        if area_id:
            layer.setCustomProperty(PROP_LAYER_AREA_ID, area_id)
        styling.apply_style(layer, style_name, category=ref.category)
        source = self.registry.get(ref.source_id) if ref.source_id else None
        if source is not None:
            provenance_module.stamp(layer, source.provenance(operation="analysis output"),
                                    area_id=area_id, category=ref.category)
        self.project.addMapLayer(layer, False)
        node = self.group(self._group_name(ref)).addLayer(layer)
        if style_name.startswith(styling.SHADED_PREFIX) and node is not None:
            # A baked composite exists for printing, where blend modes are lost. On the
            # canvas the live layers plus the multiplied hillshade already show the
            # relief, so the composite is loaded but left switched off: two copies of the
            # same theme drawn on top of each other would only confuse the user.
            node.setItemVisibilityChecked(False)
        return layer

    def add_basemap(self, source_id: str) -> Optional[QgsMapLayer]:
        """Add a background service (XYZ/WMS/WMTS) at the bottom of the group."""
        source = self.registry.get(source_id)
        if source is None:
            return None
        from ..services.ogc import raster as raster_service

        try:
            layer = raster_service.build_layer(source)
        except Exception as exc:
            log.warning(f"Cannot add basemap {source_id}: {exc}")
            return None
        layer.setCustomProperty(PROP_LAYER_CATEGORY, "imagery")
        layer.setCustomProperty(PROP_LAYER_SOURCE_ID, source.id)
        provenance_module.stamp(layer, source.provenance(operation="basemap"),
                                category="imagery")
        self.project.addMapLayer(layer, False)
        group = self.group("Imagery")
        group.addLayer(layer)
        return layer

    # ------------------------------------------------------------------ bulk

    def apply_report(self, area: ProjectArea, report: AnalysisReport, *,
                     basemap_source_id: str = "",
                     include_categories: Optional[Sequence[str]] = None) -> AppliedLayers:
        """Load every layer produced by an analysis, grouped and styled."""
        applied = AppliedLayers()
        area_layer = self.add_area_layer(area)
        if area_layer is not None:
            applied.layers.append(area_layer)
        wanted = set(Taxonomy.instance().expand([c for c in (include_categories or []) if c]))
        seen: set = set()
        for ref in report.layers:
            if ref.uri in seen:
                continue
            seen.add(ref.uri)
            if wanted and ref.category not in wanted:
                continue
            layer = self.add_layer_ref(ref, area_id=area.id)
            if layer is None:
                applied.skipped.append(ref.name)
            else:
                applied.layers.append(layer)
        self.shade_terrain(applied.layers)
        if basemap_source_id:
            # Added last, so the Imagery group ends up at the bottom of the tree without
            # any node juggling (cloning and re-inserting layer-tree nodes is a reliable
            # way to corrupt the tree).
            basemap = self.add_basemap(basemap_source_id)
            if basemap is not None:
                applied.layers.append(basemap)
        return applied

    def shade_terrain(self, layers: Sequence[QgsMapLayer]) -> int:
        """Multiply the hillshade over the coloured terrain layers on the canvas.

        Colour carries the value, shading carries the shape. This is the on-screen half of
        the relief: the printed half is the composite the terrain engine bakes, because
        QGIS drops layer blend modes in several export paths.

        :returns: how many layers were blended.
        """
        hillshade = None
        coloured = []
        for layer in layers:
            if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
                continue
            style = (layer.customProperty(STYLE_PROPERTY, "") or "").lower()
            if style == "hillshade":
                hillshade = layer
            elif style in ("dem", "slope", "aspect"):
                coloured.append(layer)
        if hillshade is None or not coloured:
            return 0
        blended = 0
        for layer in coloured:
            if styling.apply_shaded_relief(layer, hillshade):
                blended += 1
        # The hillshade must sit above what it shades, or the multiply has nothing to
        # multiply with.
        self._raise_above(hillshade, coloured)
        return blended

    def _raise_above(self, hillshade: QgsRasterLayer,
                     coloured: Sequence[QgsMapLayer]) -> None:
        """Move the hillshade node directly above the topmost coloured terrain layer."""
        root = self.project.layerTreeRoot()
        node = root.findLayer(hillshade.id())
        if node is None or node.parent() is None:
            return
        parent = node.parent()
        siblings = parent.children()
        try:
            positions = [siblings.index(root.findLayer(layer.id()))
                         for layer in coloured if root.findLayer(layer.id()) in siblings]
        except ValueError:  # pragma: no cover - node moved meanwhile
            return
        if not positions or siblings.index(node) < min(positions):
            return
        parent.removeChildNode(node)
        parent.insertLayer(min(positions), hillshade)

    def remove_area_layers(self, area_id: str) -> int:
        """Remove every layer belonging to a project area. Returns how many were removed."""
        removed = 0
        for layer in list(self.project.mapLayers().values()):
            if layer.customProperty(PROP_LAYER_AREA_ID, "") == area_id:
                self.project.removeMapLayer(layer.id())
                removed += 1
        return removed

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _group_name(ref: LayerRef) -> str:
        if ref.group:
            return ref.group
        taxonomy = Taxonomy.instance()
        category = taxonomy.get(ref.category)
        return category.label("it") if category else "Data"

    def _find_existing(self, name: str, area_id: str, category: str) -> Optional[QgsMapLayer]:
        for layer in self.project.mapLayers().values():
            if layer.name() == name and \
                    layer.customProperty(PROP_LAYER_AREA_ID, "") == area_id and \
                    layer.customProperty(PROP_LAYER_CATEGORY, "") == category:
                return layer
        return None


def layer_summary(layers: Sequence[QgsMapLayer]) -> Dict[str, int]:
    """Count the applied layers by category (for the dock summary)."""
    counters: Dict[str, int] = {}
    for layer in layers:
        category = layer.customProperty(PROP_LAYER_CATEGORY, "") or "other"
        counters[category] = counters.get(category, 0) + 1
    return counters
