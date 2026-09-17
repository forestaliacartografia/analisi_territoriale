"""Cartography engine: scale choice, layout building, legend pruning and export."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

from territorial_suite.core import qt_compat
from territorial_suite.core.constants import PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from territorial_suite.core.models import AnalysisReport, LayerRef, Provenance, SourceResult
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.cartography import export as export_module
from territorial_suite.engines.cartography import scale as scale_engine
from territorial_suite.engines.cartography import styling
from territorial_suite.engines.cartography.layout import (
    LayoutBuilder,
    MapSpec,
    get_template,
    template_list,
)
from territorial_suite.engines.cartography.series import MapSeriesEngine

AREA_RECT = QgsRectangle(680000, 4848000, 681000, 4849000)


def test_area() -> ProjectArea:
    """A 1 km square in UTM 32N."""
    return ProjectArea.from_rectangle(AREA_RECT, "EPSG:32632", name="Area di test")


class TestScaleEngine(unittest.TestCase):
    def test_page_dimensions(self):
        self.assertEqual(scale_engine.page_dimensions("A4", "portrait"), (210.0, 297.0))
        self.assertEqual(scale_engine.page_dimensions("A3", "landscape"), (420.0, 297.0))
        self.assertEqual(scale_engine.page_dimensions("unknown", "portrait"), (210.0, 297.0))

    def test_scale_choice_is_the_first_round_value_that_fits(self):
        # 1 km area + 8 % margin = 1080 m. In a 200 mm frame that needs 1:5400, so the
        # first configured round scale that fits is 1:10.000; in a 250 mm frame 1:5.000.
        self.assertEqual(scale_engine.choose_scale(AREA_RECT, 200.0, 200.0), 10000)
        self.assertEqual(scale_engine.choose_scale(AREA_RECT, 250.0, 250.0), 5000)

    def test_small_area_gets_a_large_scale(self):
        rect = QgsRectangle(680000, 4848000, 680050, 4848050)
        self.assertLessEqual(scale_engine.choose_scale(rect, 200.0, 200.0), 1000)

    def test_extent_for_scale_round_trip(self):
        extent = scale_engine.extent_for_scale(QgsPointXY(680500, 4848500), 5000, 200, 100)
        self.assertAlmostEqual(extent.width(), 1000.0, places=3)
        self.assertAlmostEqual(extent.height(), 500.0, places=3)

    def test_formatting_and_intervals(self):
        self.assertEqual(scale_engine.format_scale(10000), "1:10.000")
        self.assertGreater(scale_engine.grid_interval(10000), 0)
        self.assertEqual(scale_engine.contour_interval(5000), 5.0)


class TestStyling(unittest.TestCase):
    def test_colour_parsing_uses_rgba_order(self):
        colour = styling._color("#7fbf7f55")
        self.assertEqual((colour.red(), colour.green(), colour.blue(), colour.alpha()),
                         (127, 191, 127, 85))

    def test_category_defaults_exist(self):
        self.assertTrue(styling.style_for_category("cadastral"))
        self.assertTrue(styling.style_for_category("nature_conservation"))
        self.assertEqual(styling.style_for_category("unknown_category"), "")

    def test_apply_style_to_vector_layer(self):
        layer = QgsVectorLayer("Polygon?crs=EPSG:32632", "p", "memory")
        applied = styling.apply_style(layer, "cadastral_parcel")
        self.assertEqual(applied, "cadastral_parcel")
        self.assertIsNotNone(layer.renderer())

    def test_available_styles_cover_the_catalogue(self):
        names = styling.available_styles()
        for expected in ("cadastral_parcel", "roads", "hydrography", "slope"):
            self.assertIn(expected, names)


class TestLayoutBuilder(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.area = test_area()
        self.builder = LayoutBuilder(self.project)

    def tearDown(self):
        self.project.removeAllMapLayers()
        self.project.clear()

    def add_layer(self, name: str, category: str) -> QgsVectorLayer:
        layer = QgsVectorLayer("Polygon?crs=EPSG:32632", name, "memory")
        layer.dataProvider().addAttributes([qt_compat.field("n", qt_compat.STRING)])
        layer.updateFields()
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromRect(AREA_RECT))
        feature.setAttributes(["x"])
        layer.dataProvider().addFeature(feature)
        layer.updateExtents()
        layer.setCustomProperty(PROP_LAYER_CATEGORY, category)
        layer.setCustomProperty(PROP_LAYER_SOURCE_ID, f"src.{category}")
        self.project.addMapLayer(layer)
        return layer

    def test_templates_are_loaded(self):
        templates = template_list()
        self.assertGreaterEqual(len(templates), 10)
        ids = {template["id"] for template in templates}
        for expected in ("territorial_overview", "cadastral_map", "constraints_map"):
            self.assertIn(expected, ids)

    def test_template_defaults_are_merged(self):
        template = get_template("cadastral_map")
        self.assertIn("blocks", template)
        self.assertTrue(template["blocks"])
        self.assertEqual(template["page"]["orientation"], "portrait")

    def test_unknown_template_falls_back(self):
        template = get_template("does_not_exist")
        self.assertTrue(template.get("id"))

    def test_layout_has_every_expected_item(self):
        self.add_layer("Vincoli", "landscape_cultural")
        layout = self.builder.build(self.area, MapSpec(template="territorial_overview",
                                                       sheet_number="01"))
        kinds = {type(item).__name__ for item in layout.items()}
        for expected in ("QgsLayoutItemMap", "QgsLayoutItemLegend", "QgsLayoutItemScaleBar",
                         "QgsLayoutItemLabel", "QgsLayoutItemShape"):
            self.assertIn(expected, kinds)

    def test_map_uses_the_work_crs_and_a_round_scale(self):
        """A sheet holds more than one map frame: the scale belongs to the main one."""
        from territorial_suite.engines.cartography.overview import main_map

        layout = self.builder.build(self.area, MapSpec())
        frame = main_map(layout)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.crs().authid(), "EPSG:32632")
        self.assertIn(int(round(frame.scale())), scale_engine.configured_scales())

    def test_explicit_scale_is_respected(self):
        from territorial_suite.engines.cartography.overview import main_map

        layout = self.builder.build(self.area, MapSpec(scale=25000))
        self.assertAlmostEqual(main_map(layout).scale(), 25000, delta=1)

    def test_template_filters_layers_by_category(self):
        self.add_layer("Catasto", "cadastral")
        self.add_layer("Vincoli", "landscape_cultural")
        template = get_template("cadastral_map")
        spec = MapSpec(template="cadastral_map").merged_with_template(template)
        layers = self.builder.layers_for(template, spec)
        names = {layer.name() for layer in layers}
        self.assertIn("Catasto", names)
        self.assertNotIn("Vincoli", names)

    def test_export_to_pdf_and_png(self):
        layout = self.builder.build(self.area, MapSpec())
        with tempfile.TemporaryDirectory() as folder:
            pdf = export_module.ExportCenter.export(layout, Path(folder) / "m.pdf", "pdf")
            png = export_module.ExportCenter.export(layout, Path(folder) / "m.png", "png",
                                                    dpi=72)
            self.assertTrue(pdf.ok, pdf.message)
            self.assertTrue(png.ok, png.message)
            self.assertGreater((Path(folder) / "m.pdf").stat().st_size, 1000)

    def test_series_generates_independent_layouts(self):
        self.add_layer("Catasto", "cadastral")
        self.add_layer("Vincoli", "landscape_cultural")
        engine = MapSeriesEngine(self.project)
        result = engine.generate(self.area, series="minimal")
        self.assertGreaterEqual(result.count, 2)
        names = {layout.name() for layout in result.layouts}
        self.assertEqual(len(names), result.count)

    def test_dxf_export_of_vector_layers(self):
        from qgis.core import QgsCoordinateReferenceSystem

        layer = self.add_layer("Catasto", "cadastral")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cad.dxf"
            result = export_module.export_layers_to_dxf(
                [layer], path, QgsCoordinateReferenceSystem("EPSG:32632"))
            self.assertTrue(result.ok, result.message)
            self.assertGreater(path.stat().st_size, 1000)

    def test_dxf_export_without_vector_layers(self):
        from qgis.core import QgsCoordinateReferenceSystem

        with tempfile.TemporaryDirectory() as folder:
            result = export_module.export_layers_to_dxf(
                [], Path(folder) / "empty.dxf", QgsCoordinateReferenceSystem("EPSG:32632"))
            self.assertFalse(result.ok)

    def test_series_skips_templates_without_layers(self):
        engine = MapSeriesEngine(self.project)
        result = engine.generate(self.area, template_ids=["risk_map"])
        self.assertEqual(result.count, 0)
        self.assertIn("risk_map", result.skipped)


class TestExportNaming(unittest.TestCase):
    def test_file_names_are_numbered_and_clean(self):
        name = export_module.file_name("Carta dei vincoli e delle sensibilita'", "pdf",
                                       index=3)
        self.assertTrue(name.startswith("03_"))
        self.assertTrue(name.endswith(".pdf"))
        self.assertNotIn(" ", name)
        self.assertNotIn("'", name)

    def test_slugify_strips_accents(self):
        self.assertEqual(export_module.slugify("Carta altimetrica à è"), "Carta_altimetrica_a_e")


if __name__ == "__main__":
    unittest.main()
