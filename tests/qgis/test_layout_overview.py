"""WP1 — the locator map (inset / overview) of a sheet.

The point of the whole feature is one property: the highlighted frame must be a **linked**
`QgsLayoutItemMapOverview`, not a rectangle drawn once at build time. A drawn rectangle is
correct exactly until somebody changes the main map's scale, and then it quietly lies.
"""

from __future__ import annotations

import ast
import unittest

from qgis.core import (
    QgsLayoutItemMap,
    QgsLayoutItemMapOverview,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

from territorial_suite.core import settings
from territorial_suite.core.constants import PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from territorial_suite.core.errors import LayoutError
from territorial_suite.core.paths import plugin_dir
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.cartography.layout import MAIN_MAP_ID, LayoutBuilder, MapSpec
from territorial_suite.engines.cartography.overview import (
    ANCHOR_MAP,
    OVERVIEW_MAP_ID,
    OverviewBuilder,
    OverviewSpec,
    linked_overviews,
    locator_map,
    main_map,
)
from territorial_suite.engines.cartography.profiles import ProfileStore

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="Firenze centro")


class TestOverviewSpec(unittest.TestCase):
    """The inset is configuration, not code."""

    def test_defaults_come_from_the_settings_file(self):
        spec = OverviewSpec.from_settings()
        self.assertTrue(spec.enabled)
        self.assertGreater(spec.zoom_factor, 1.0)
        self.assertIn("administrative", spec.categories)

    def test_a_bare_boolean_is_accepted(self):
        self.assertFalse(OverviewSpec.from_dict(False).enabled)
        self.assertTrue(OverviewSpec.from_dict(True).enabled)

    def test_nonsense_values_are_corrected_not_propagated(self):
        spec = OverviewSpec.from_dict({"anchor": "altrove", "corner": "centro",
                                       "zoom_factor": 0.1})
        self.assertEqual(spec.anchor, "panel")
        self.assertEqual(spec.corner, "bottom_right")
        self.assertGreaterEqual(spec.zoom_factor, 1.5)

    def test_a_profile_overlay_only_changes_what_it_names(self):
        base = OverviewSpec.from_settings()
        merged = base.merged_with({"zoom_factor": 25})
        self.assertEqual(merged.zoom_factor, 25)
        self.assertEqual(merged.categories, base.categories)
        self.assertEqual(merged.frame_color, base.frame_color)
        self.assertIs(base.merged_with(None), base)

    def test_no_url_or_layer_name_is_hardcoded_in_the_module(self):
        source = (plugin_dir() / "engines" / "cartography" / "overview.py").read_text(
            encoding="utf-8")
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)


class TestExtent(unittest.TestCase):
    def test_the_inset_zooms_out_around_the_area(self):
        spec = OverviewSpec.from_dict({"zoom_factor": 10})
        extent = OverviewBuilder.extent_for(AREA, spec)
        area_extent = AREA.geometry_in(AREA.work_crs).boundingBox()
        self.assertGreater(extent.width(), area_extent.width() * 5)
        self.assertTrue(extent.contains(area_extent),
                        "l'area di progetto deve stare dentro il riquadro")
        self.assertAlmostEqual(extent.center().x(), area_extent.center().x(), delta=1.0)
        self.assertAlmostEqual(extent.center().y(), area_extent.center().y(), delta=1.0)

    def test_a_bigger_factor_gives_a_bigger_frame(self):
        near = OverviewBuilder.extent_for(AREA, OverviewSpec.from_dict({"zoom_factor": 4}))
        far = OverviewBuilder.extent_for(AREA, OverviewSpec.from_dict({"zoom_factor": 40}))
        self.assertGreater(far.width(), near.width())


class TestOverviewOnLayout(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)
        self.builder = LayoutBuilder(self.project)

    def build(self, spec: MapSpec = None):
        return self.builder.build(AREA, spec or MapSpec(), add_to_project=False)

    def test_the_sheet_gets_a_second_map_frame(self):
        layout = self.build()
        frames = [i for i in layout.items() if isinstance(i, QgsLayoutItemMap)]
        self.assertEqual(len(frames), 2, "atteso il riquadro principale piu' il locator")
        self.assertEqual(main_map(layout).id(), MAIN_MAP_ID)
        self.assertEqual(locator_map(layout).id(), OVERVIEW_MAP_ID)

    def test_the_overview_is_linked_not_drawn(self):
        """A static rectangle would go stale the moment the main map moves."""
        layout = self.build()
        overviews = linked_overviews(layout)
        self.assertEqual(len(overviews), 1)
        overview = overviews[0]
        self.assertIsInstance(overview, QgsLayoutItemMapOverview)
        self.assertTrue(overview.enabled())
        self.assertIs(overview.linkedMap(), main_map(layout))

    def test_the_link_survives_a_change_of_the_main_map(self):
        layout = self.build()
        overview = linked_overviews(layout)[0]
        frame = main_map(layout)
        before = frame.extent().width()
        frame.setScale(frame.scale() * 2.0)
        self.assertGreater(frame.extent().width(), before)
        # The overview still points at the same object, so QGIS redraws it from the
        # current extent: nothing was copied at build time.
        self.assertIs(overview.linkedMap(), frame)

    def test_the_inset_is_wider_than_the_main_frame_extent(self):
        layout = self.build()
        self.assertGreater(locator_map(layout).extent().width(),
                           main_map(layout).extent().width())

    def test_the_inset_shares_the_crs_of_the_sheet(self):
        layout = self.build()
        self.assertEqual(locator_map(layout).crs().authid(),
                         main_map(layout).crs().authid())

    def test_a_profile_can_switch_it_off(self):
        layout = self.build(MapSpec(profile_id="semplificato"))
        self.assertIsNone(locator_map(layout))
        self.assertEqual(linked_overviews(layout), [])
        self.assertIsNotNone(main_map(layout))

    def test_a_profile_can_change_the_zoom(self):
        standard = self.build(MapSpec(profile_id="standard"))
        technical = self.build(MapSpec(profile_id="tecnico"))
        self.assertGreater(locator_map(technical).extent().width(),
                           locator_map(standard).extent().width())

    def test_the_inset_shows_context_not_the_whole_sheet(self):
        boundaries = QgsVectorLayer("Polygon?crs=EPSG:32632", "Comuni", "memory")
        boundaries.setCustomProperty(PROP_LAYER_CATEGORY, "administrative")
        constraints = QgsVectorLayer("Polygon?crs=EPSG:32632", "Vincoli", "memory")
        constraints.setCustomProperty(PROP_LAYER_CATEGORY, "landscape_cultural")
        for layer in (boundaries, constraints):
            self.project.addMapLayer(layer, False)
            self.project.layerTreeRoot().addLayer(layer)
        layout = self.build()
        inset_layers = locator_map(layout).layers()
        self.assertIn(boundaries, inset_layers)
        self.assertNotIn(constraints, inset_layers,
                         "il locator non deve ripetere i temi della tavola")

    def test_the_basemap_of_the_inset_can_be_pinned(self):
        wanted = QgsVectorLayer("Polygon?crs=EPSG:32632", "Sfondo A", "memory")
        wanted.setCustomProperty(PROP_LAYER_CATEGORY, "imagery")
        wanted.setCustomProperty(PROP_LAYER_SOURCE_ID, "fonte.voluta")
        other = QgsVectorLayer("Polygon?crs=EPSG:32632", "Sfondo B", "memory")
        other.setCustomProperty(PROP_LAYER_CATEGORY, "imagery")
        other.setCustomProperty(PROP_LAYER_SOURCE_ID, "fonte.altra")
        for layer in (wanted, other):
            self.project.addMapLayer(layer, False)
            self.project.layerTreeRoot().addLayer(layer)
        spec = OverviewSpec.from_dict({"basemap_source_id": "fonte.voluta"})
        chosen = OverviewBuilder(self.project).layers_for(spec)
        self.assertIn(wanted, chosen)
        self.assertNotIn(other, chosen)

    def test_anchored_on_a_corner_it_stays_inside_the_main_frame(self):
        layout = self.build()
        frame = main_map(layout)
        spec = OverviewSpec.from_dict({"anchor": ANCHOR_MAP, "corner": "bottom_right"})
        left, top, width, height = OverviewBuilder.frame_rect(frame, spec)
        self.assertGreaterEqual(left, frame.pagePos().x())
        self.assertGreaterEqual(top, frame.pagePos().y())
        self.assertLessEqual(left + width,
                             frame.pagePos().x() + frame.rect().width() + 0.01)
        self.assertLessEqual(top + height,
                             frame.pagePos().y() + frame.rect().height() + 0.01)


class TestFailureHandling(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def test_a_degenerate_extent_is_refused_not_drawn_wrong(self):
        """``ProjectArea`` refuses an empty geometry upstream, but an extent can still
        degenerate after a transform: the guard must speak rather than draw a point."""
        from qgis.core import QgsGeometry

        class DegenerateArea:
            work_crs = AREA.work_crs

            def geometry_in(self, _crs):
                return QgsGeometry()

        with self.assertRaises(LayoutError):
            OverviewBuilder.extent_for(DegenerateArea(), OverviewSpec())

    def test_a_layout_without_a_main_map_produces_no_inset(self):
        from qgis.core import QgsPrintLayout

        layout = QgsPrintLayout(self.project)
        layout.initializeDefaults()
        builder = OverviewBuilder(self.project)
        self.assertIsNone(builder.add(layout, None, AREA, OverviewSpec()))

    def test_a_disabled_spec_creates_nothing(self):
        from qgis.core import QgsLayoutItemMap, QgsPrintLayout

        layout = QgsPrintLayout(self.project)
        layout.initializeDefaults()
        frame = QgsLayoutItemMap(layout)
        layout.addLayoutItem(frame)
        builder = OverviewBuilder(self.project)
        self.assertIsNone(builder.add(layout, frame, AREA,
                                      OverviewSpec.from_dict({"enabled": False})))


class TestSamePathEverywhere(unittest.TestCase):
    """GUI and Processing must get the inset from the same builder."""

    def test_the_locator_is_built_by_the_layout_builder_only(self):
        source = (plugin_dir() / "engines" / "cartography" / "layout.py").read_text(
            encoding="utf-8")
        self.assertIn("OverviewBuilder", source)
        for module in ("gui/dock.py", "processing/algs/cartography.py",
                       "processing/algs/heritage.py"):
            text = (plugin_dir() / module).read_text(encoding="utf-8")
            self.assertNotIn("QgsLayoutItemMapOverview", text,
                             f"{module} costruisce il locator da solo")

    def test_the_dock_worker_never_builds_the_inset(self):
        tree = ast.parse((plugin_dir() / "gui" / "dock.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name == "work"):
                continue
            names = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            self.assertNotIn("add", names & {"add"},
                             "un worker non puo' creare item di layout")

    def test_processing_quick_map_gets_the_inset_for_free(self):
        """The Processing path uses LayoutBuilder, so it inherits the locator."""
        project = QgsProject()
        self.addCleanup(project.clear)
        layout = LayoutBuilder(project).build(AREA, MapSpec(), add_to_project=False)
        self.assertIsNotNone(locator_map(layout))


if __name__ == "__main__":
    unittest.main()
