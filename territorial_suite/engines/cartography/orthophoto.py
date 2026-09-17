"""One-click orthophoto sheet.

From a project area to a printable sheet: choose the imagery provider, put the area on top
of it, build the layout with the orthophoto profile, and carry the credit line and the
provenance of the imagery that was *actually* used — including a visible notice when the
requested provider could not be used and another one took its place.

**Threading.** The work splits in two, and the split is not cosmetic: creating a
``QgsRasterLayer`` or a ``QgsPrintLayout`` outside the main thread crashes QGIS.

``prepare()``
    Network only: pick a provider, negotiate a session if needed, prove a tile answers.
    Returns a plain, serialisable :class:`OrthophotoPlan`. **Safe in a QgsTask.**
``compose()``
    Pure QGIS object construction: build the raster layer, stamp it, add it to the
    project, build the layout. **Main thread only.**

``run()`` is the two in sequence, for callers that already are on the main thread
(Processing with ``NoThreading``, the Python API, tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from qgis.core import QgsMapLayer, QgsPrintLayout, QgsProject

from ...core import log, provenance as provenance_module
from ...core.constants import PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from ...core.errors import LayoutError, SourceError
from ...core.feedback import Feedback, NullFeedback
from ...core.models import AnalysisReport
from ...core.project_area import ProjectArea
from ...core.registry import DataSourceRegistry
from ...services.http import HttpClient
from . import provenance_module_key
from .imagery import AUTOMATIC, ImageryChoice, ImageryEngine
from .layout import LayoutBuilder, MapSpec

#: Template and profile used when the caller does not ask for others.
TEMPLATE_ID = "orthophoto_map"
PROFILE_ID = "ortofoto"

#: Category stamped on the imagery layer so the layout can find it again.
IMAGERY_CATEGORY = "imagery"


@dataclass
class OrthophotoPlan:
    """What the worker thread decided. Contains no QGIS object, by construction."""

    choice: ImageryChoice = field(default_factory=ImageryChoice)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether a usable provider was found."""
        return self.choice.available

    @property
    def reason(self) -> str:
        """Why no provider could be used."""
        return "; ".join(self.choice.attempts) or "nessuna sorgente di ortofoto"


@dataclass
class OrthophotoSheet:
    """The outcome of the one-click orthophoto workflow."""

    layout: Optional[QgsPrintLayout] = None
    choice: Optional[ImageryChoice] = None
    layer: Optional[QgsMapLayer] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether a sheet with real imagery was produced."""
        return (self.layout is not None and self.layer is not None
                and self.choice is not None and self.choice.layer_ready)

    @property
    def credit_line(self) -> str:
        """The attribution that must stay on the sheet."""
        return self.choice.credit_line() if self.choice else ""


class OrthophotoEngine:
    """Builds an orthophoto sheet for a project area."""

    def __init__(self, project: Optional[QgsProject] = None, *,
                 registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None) -> None:
        self.project = project or QgsProject.instance()
        self.registry = registry or DataSourceRegistry.instance()
        self.imagery = ImageryEngine(registry=self.registry, http=http)

    # ------------------------------------------------------------------ worker half

    def prepare(self, area: ProjectArea, *, preference: str = AUTOMATIC,
                zoom: int = 12, feedback: Optional[Feedback] = None) -> OrthophotoPlan:
        """Choose and probe the imagery provider. Safe to call from a worker thread."""
        feedback = feedback or NullFeedback()
        feedback.set_step("Ortofoto: selezione della sorgente")
        choice = self.imagery.select(area.bbox_wgs84(), preference=preference, zoom=zoom)
        plan = OrthophotoPlan(choice=choice.without_layer())
        if not plan.ok:
            log.warning(f"Ortofoto non disponibile per l'area {area.id}: "
                        f"{choice.attempts}")
            return plan
        if choice.fallback_used:
            plan.warnings.append(
                f"Ortofoto richiesta '{choice.requested}' non utilizzabile: e' stata "
                f"usata '{choice.name}'. " + "; ".join(choice.attempts[:-1]))
        feedback.set_progress(60)
        return plan

    # ------------------------------------------------------------------ main-thread half

    def compose(self, area: ProjectArea, plan: OrthophotoPlan, *,
                profile_id: str = PROFILE_ID, template_id: str = TEMPLATE_ID,
                spec: Optional[MapSpec] = None,
                report: Optional[AnalysisReport] = None,
                layers: Optional[List[QgsMapLayer]] = None,
                add_to_project: bool = True,
                feedback: Optional[Feedback] = None) -> OrthophotoSheet:
        """Build the layer and the layout from a prepared plan. **Main thread only.**

        :raises LayoutError: when no imagery can be placed. A sheet whose background
            silently failed is worse than no sheet: the reader cannot tell an empty
            territory from a missing service.
        """
        feedback = feedback or NullFeedback()
        sheet = OrthophotoSheet(choice=plan.choice, warnings=list(plan.warnings))
        if not plan.ok:
            raise LayoutError(f"Nessuna ortofoto disponibile per la tavola: {plan.reason}")

        try:
            layer = self.imagery.build_layer(plan.choice)
        except SourceError as exc:
            raise LayoutError(
                f"L'ortofoto '{plan.choice.name}' non ha potuto essere caricata: {exc}"
            ) from exc
        plan.choice.layer = layer
        sheet.layer = layer
        self._stamp(layer, plan.choice)

        if add_to_project:
            self.project.addMapLayer(layer, False)
            root = self.project.layerTreeRoot()
            # Bottom of the tree: the orthophoto is a background, never a foreground.
            root.insertLayer(len(root.children()), layer)

        if report is not None:
            report.modules[provenance_module_key()] = plan.choice.as_dict()

        feedback.set_step("Ortofoto: composizione della tavola")
        spec = spec or MapSpec()
        if not spec.template or spec.template == "territorial_overview":
            spec.template = template_id
        spec.profile_id = spec.profile_id or profile_id
        spec.attribution = plan.choice.credit_line()
        spec.warnings = list(spec.warnings) + sheet.warnings
        spec.basemap_source_id = spec.basemap_source_id or plan.choice.source_id

        map_layers = self._map_layers(layer, layers)
        sheet.layout = LayoutBuilder(self.project).build(
            area, spec, report=report, layers=map_layers, add_to_project=add_to_project)
        sheet.warnings = list(spec.warnings)
        if not self._layout_has_raster(sheet.layout):
            raise LayoutError(
                "La tavola e' stata composta senza la base ortofotografica: "
                "il layer raster non e' entrato nel riquadro di mappa.")
        feedback.set_progress(100)
        return sheet

    # ------------------------------------------------------------------ both halves

    def run(self, area: ProjectArea, *, preference: str = AUTOMATIC,
            profile_id: str = PROFILE_ID, template_id: str = TEMPLATE_ID,
            spec: Optional[MapSpec] = None,
            report: Optional[AnalysisReport] = None,
            layers: Optional[List[QgsMapLayer]] = None,
            add_to_project: bool = True,
            zoom: int = 12,
            feedback: Optional[Feedback] = None) -> OrthophotoSheet:
        """Prepare and compose in one call. **Main thread only.**"""
        plan = self.prepare(area, preference=preference, zoom=zoom, feedback=feedback)
        return self.compose(area, plan, profile_id=profile_id, template_id=template_id,
                            spec=spec, report=report, layers=layers,
                            add_to_project=add_to_project, feedback=feedback)

    # ------------------------------------------------------------------ helpers

    def _stamp(self, layer: QgsMapLayer, choice: ImageryChoice) -> None:
        """Mark the layer so the layout and the layer tree recognise it.

        Without these two properties ``LayoutBuilder.layers_for`` cannot tell an imagery
        layer from any other raster, and drops it from the map frame.
        """
        layer.setCustomProperty(PROP_LAYER_CATEGORY, IMAGERY_CATEGORY)
        layer.setCustomProperty(PROP_LAYER_SOURCE_ID, choice.source_id)
        provenance = self.imagery.provenance(choice)
        if provenance is not None:
            provenance_module.stamp(layer, provenance, category=IMAGERY_CATEGORY)

    def _map_layers(self, imagery: QgsMapLayer,
                    extra: Optional[List[QgsMapLayer]]) -> List[QgsMapLayer]:
        """Drawing order for the map frame: area on top, orthophoto at the bottom."""
        ordered: List[QgsMapLayer] = []
        for layer in list(extra or []):
            if layer is not None and layer is not imagery and layer not in ordered:
                ordered.append(layer)
        if not ordered:
            ordered.extend(self._area_layers())
        ordered.append(imagery)
        return ordered

    def _area_layers(self) -> List[QgsMapLayer]:
        """The project-area layers already in the tree, so the sheet shows the area."""
        found: List[QgsMapLayer] = []
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            if layer is None:
                continue
            if (layer.customProperty(PROP_LAYER_CATEGORY, "") or "") == "project_area":
                found.append(layer)
        return found

    @staticmethod
    def _layout_has_raster(layout: QgsPrintLayout) -> bool:
        """Whether at least one map frame of the layout really carries a raster."""
        from qgis.core import QgsLayoutItemMap, QgsRasterLayer

        for item in layout.items():
            if not isinstance(item, QgsLayoutItemMap):
                continue
            for layer in item.layers() or []:
                if isinstance(layer, QgsRasterLayer) and layer.isValid():
                    return True
        return False
