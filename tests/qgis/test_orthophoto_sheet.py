"""P0.3 — the orthophoto sheet must actually carry an orthophoto.

The bug this file guards against had two faces and both are checked here:

* **threading** — the sheet was built inside a ``QgsTask``, where creating a
  ``QgsRasterLayer`` or a ``QgsPrintLayout`` is illegal and crashes QGIS. The test reads
  the dock's worker closure and refuses any call that would build a QGIS object there;
* **a silently empty sheet** — the layer reached the project without the custom properties
  the layout uses to recognise it, so the map frame came out with no background. The test
  asserts the raster is really inside the map item, and that its absence is an *error*.

**What these tests cannot prove.** In this headless harness an XYZ layer never paints:
the network works and the provider returns a valid block, but the block is transparent
because ``QgsApplication.tileDownloadManager`` is not running outside a real QGIS session.
The guarantees here are therefore structural — the raster is present, valid, stamped,
ordered and credited — and the visual result must be confirmed in QGIS itself.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from qgis.core import (
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)

from territorial_suite.core import settings
from territorial_suite.core.constants import PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from territorial_suite.core.errors import LayoutError
from territorial_suite.core.models import AnalysisReport
from territorial_suite.core.paths import plugin_dir
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.cartography.orthophoto import OrthophotoEngine

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="Firenze centro")

#: Anything that builds or touches a QGIS object; never allowed in a worker thread.
FORBIDDEN_IN_WORKER = {
    "compose", "run", "build_layer", "build", "addMapLayer", "insertLayer",
    "QgsRasterLayer", "QgsVectorLayer", "QgsPrintLayout", "openLayoutDesigner",
    "setLayers", "layerTreeRoot",
}


def map_items(layout):
    """The main map frames of a sheet, excluding the locator inset.

    The inset deliberately shows a different, zoomed-out set of layers, so asserting on
    it instead of the main frame would test the wrong thing.
    """
    from territorial_suite.engines.cartography.overview import OVERVIEW_MAP_ID

    return [item for item in layout.items()
            if isinstance(item, QgsLayoutItemMap) and item.id() != OVERVIEW_MAP_ID]


def label_texts(layout) -> str:
    return "\n".join(item.text() for item in layout.items()
                     if isinstance(item, QgsLayoutItemLabel))


class TestWorkerThreadSafety(unittest.TestCase):
    """C6 / anti-pattern 8 and 22: nothing QGIS-shaped may be built off the main thread."""

    @staticmethod
    def worker_closure() -> ast.FunctionDef:
        """Return the ``work`` function nested inside ``dock.run_orthophoto``."""
        tree = ast.parse((plugin_dir() / "gui" / "dock.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_orthophoto":
                for inner in node.body:
                    if isinstance(inner, ast.FunctionDef) and inner.name == "work":
                        return inner
        raise AssertionError("run_orthophoto non definisce piu' una funzione 'work'")

    def test_the_dock_worker_only_prepares(self):
        work = self.worker_closure()
        called = set()
        for node in ast.walk(work):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    called.add(func.attr)
                elif isinstance(func, ast.Name):
                    called.add(func.id)
        self.assertIn("prepare", called,
                      "il worker deve limitarsi a preparare il piano")
        offending = called & FORBIDDEN_IN_WORKER
        self.assertEqual(offending, set(),
                         f"chiamate vietate nel thread worker: {sorted(offending)}")

    def test_the_plan_carries_no_qgis_object(self):
        engine = OrthophotoEngine(QgsProject())
        plan = engine.prepare(AREA)
        self.assertIsNone(plan.choice.layer,
                          "il piano non deve trasportare un QgsRasterLayer")
        # A plan must survive the trip between threads as plain data.
        json.dumps(plan.choice.as_dict())
        self.assertIsInstance(plan.warnings, list)

    def test_the_processing_algorithm_declares_no_threading(self):
        from territorial_suite.processing.algs.base import NO_THREADING
        from territorial_suite.processing.algs.heritage import OrthophotoSheetAlgorithm

        if NO_THREADING is None:
            self.skipTest("questa build di QGIS non espone il flag NoThreading")
        self.assertTrue(OrthophotoSheetAlgorithm().flags() & NO_THREADING)


class TestSheetHasImagery(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self._probe = settings.get("cartography.probe_imagery", True)
        settings.set_value("cartography.probe_imagery", False)
        self.addCleanup(lambda: settings.set_value("cartography.probe_imagery",
                                                   self._probe))
        self.addCleanup(self.project.clear)
        self.engine = OrthophotoEngine(self.project)

    def test_the_map_frame_really_contains_a_raster(self):
        sheet = self.engine.run(AREA, add_to_project=False)
        self.assertTrue(sheet.ok)
        frames = map_items(sheet.layout)
        self.assertEqual(len(frames), 1)
        rasters = [layer for layer in frames[0].layers()
                   if isinstance(layer, QgsRasterLayer)]
        self.assertTrue(rasters, "il riquadro di mappa non contiene alcun raster")
        self.assertTrue(rasters[0].isValid())

    def test_the_imagery_layer_is_recognisable_by_the_layout(self):
        """Without these properties layers_for() drops the orthophoto."""
        sheet = self.engine.run(AREA, add_to_project=False)
        self.assertEqual(sheet.layer.customProperty(PROP_LAYER_CATEGORY, ""), "imagery")
        self.assertEqual(sheet.layer.customProperty(PROP_LAYER_SOURCE_ID, ""),
                         sheet.choice.source_id)

    def test_the_orthophoto_sits_under_everything_else(self):
        area_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "area", "memory")
        area_layer.setCustomProperty(PROP_LAYER_CATEGORY, "project_area")
        self.project.addMapLayer(area_layer, False)
        self.project.layerTreeRoot().addLayer(area_layer)
        sheet = self.engine.run(AREA, add_to_project=False)
        layers = map_items(sheet.layout)[0].layers()
        self.assertIsInstance(layers[-1], QgsRasterLayer)
        self.assertIn(area_layer, layers)
        self.assertLess(layers.index(area_layer), layers.index(sheet.layer))

    def test_the_credit_line_is_printed(self):
        sheet = self.engine.run(AREA, add_to_project=False)
        self.assertTrue(sheet.credit_line.strip())
        self.assertIn(sheet.choice.attribution.split(",")[0], label_texts(sheet.layout))

    def test_esri_works_without_any_key(self):
        sheet = self.engine.run(AREA, preference="esri", add_to_project=False)
        self.assertTrue(sheet.ok)
        self.assertEqual(sheet.choice.provider, "esri")
        self.assertFalse(sheet.choice.fallback_used)

    def test_google_without_a_key_falls_back_and_the_sheet_says_so(self):
        sheet = self.engine.run(AREA, preference="google", add_to_project=False)
        self.assertTrue(sheet.ok, "il ripiego deve comunque produrre una tavola")
        self.assertNotEqual(sheet.choice.provider, "google")
        self.assertTrue(sheet.choice.fallback_used)
        text = label_texts(sheet.layout)
        self.assertIn("Avvertenze", text)
        self.assertIn("google", text.lower())

    def test_the_layer_reaches_the_project_when_asked(self):
        sheet = self.engine.run(AREA, add_to_project=True)
        self.assertIn(sheet.layer.id(), self.project.mapLayers())
        tree_layers = [node.layer() for node in self.project.layerTreeRoot().findLayers()]
        self.assertIn(sheet.layer, tree_layers)

    def test_provenance_of_the_imagery_is_attached_to_the_report(self):
        report = AnalysisReport(area=AREA.as_dict())
        self.engine.run(AREA, report=report, add_to_project=False)
        payload = report.module("imagery")
        for key in ("imagery_provider", "imagery_source", "source_url", "attribution",
                    "license", "date", "fallback_used"):
            self.assertIn(key, payload)


class TestSheetWithoutImageryIsAnError(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def test_no_provider_raises_instead_of_printing_a_blank_sheet(self):
        engine = OrthophotoEngine(self.project)
        engine.imagery.providers = lambda: []
        plan = engine.prepare(AREA)
        self.assertFalse(plan.ok)
        with self.assertRaises(LayoutError) as caught:
            engine.compose(AREA, plan, add_to_project=False)
        self.assertIn("ortofoto", str(caught.exception).lower())

    def test_a_provider_that_cannot_build_its_layer_raises(self):
        from territorial_suite.core.errors import SourceUnavailableError

        engine = OrthophotoEngine(self.project)
        plan = engine.prepare(AREA)
        if not plan.ok:
            self.skipTest("nessuna ortofoto configurata in questo ambiente")

        def refuse(_choice):
            raise SourceUnavailableError("provider rotto", source_id="x")

        engine.imagery.build_layer = refuse
        with self.assertRaises(LayoutError):
            engine.compose(AREA, plan, add_to_project=False)


if __name__ == "__main__":
    unittest.main()
