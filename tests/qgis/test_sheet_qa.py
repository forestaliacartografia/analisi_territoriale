"""The sheet must keep the promise its title makes.

The requirement this file guards is blunt: a sheet titled "Carta del rischio idraulico"
is not valid if flood risk is not actually on the map and in the legend. Every check here
inspects a **built layout**, not the builder's intentions, because what reaches the paper
is the only thing a reader can see.

The checks run in both directions. Title to legend catches the sheet that promises a theme
it does not show. Legend to title catches the general map wearing a specific name.
"""

from __future__ import annotations

from pathlib import Path

import unittest

from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemLegend,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

from territorial_suite.core.constants import PROP_LAYER_CATEGORY
from territorial_suite.core.paths import plugin_dir
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.cartography import sheet_spec
from territorial_suite.engines.cartography.layout import LayoutBuilder, MapSpec
from territorial_suite.engines.cartography.sheet_spec import (
    ERROR,
    OK,
    WARNING,
    MapSheetSpecification,
    SheetValidator,
    specification_for,
    validate_layout,
)

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="Firenze centro")


def themed_layer(project, name: str, category: str, *, empty: bool = False,
                 inside: bool = True):
    """A memory layer stamped like a real one, optionally empty or far away."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    layer.setCustomProperty(PROP_LAYER_CATEGORY, category)
    if not empty:
        rect = (QgsRectangle(11.250, 43.766, 11.262, 43.774) if inside
                else QgsRectangle(7.0, 45.0, 7.01, 45.01))
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(rect))
        layer.dataProvider().addFeatures([feature])
        layer.updateExtents()
    project.addMapLayer(layer, False)
    project.layerTreeRoot().addLayer(layer)
    return layer


class SheetCase(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)
        sheet_spec.reload_specifications()
        self.addCleanup(sheet_spec.reload_specifications)

    def build(self, template: str):
        return LayoutBuilder(self.project).build(
            AREA, MapSpec(template=template), add_to_project=False)


class TestContractsAreConfiguration(SheetCase):
    """C3: the contracts are data, and the vocabulary comes from the taxonomy."""

    def test_the_contract_is_loaded_from_the_configuration(self):
        spec = specification_for("flood_hazard_map")
        self.assertEqual(spec.theme, "flood_hazard")
        self.assertIn("flood_hazard", spec.required_categories)

    def test_an_unknown_template_has_no_theme_instead_of_failing(self):
        spec = specification_for("una_tavola_che_non_esiste")
        self.assertTrue(spec.general_purpose)

    def test_the_words_of_a_title_come_from_the_taxonomy(self):
        """Nothing in the module hardcodes what "pericolosita idraulica" means."""
        terms = MapSheetSpecification(theme="flood_hazard").title_terms()
        self.assertTrue(any("pericolosita idraulica" in t for t in terms),
                        f"la tassonomia non fornisce le parole del tema: {terms}")

    def test_no_theme_vocabulary_is_hardcoded_in_the_module(self):
        source = (plugin_dir() / "engines" / "cartography" / "sheet_spec.py").read_text(
            encoding="utf-8")
        for word in ("flood_hazard", "landslide", "vincolo idrogeologico"):
            self.assertNotIn(word, source,
                             f"«{word}» non deve vivere nel codice ma in configurazione")


class TestTitleToLegend(SheetCase):
    """The declared theme must really be on the sheet."""

    def test_a_sheet_that_shows_its_theme_passes(self):
        themed_layer(self.project, "Aree allagabili", "flood_hazard")
        report = validate_layout(self.build("flood_hazard_map"), "flood_hazard_map")
        self.assertEqual(report.level, OK, [f.message for f in report.findings])
        self.assertFalse(report.blocking)

    def test_a_missing_theme_layer_is_an_error(self):
        themed_layer(self.project, "Strade", "roads")
        report = validate_layout(self.build("flood_hazard_map"), "flood_hazard_map")
        self.assertEqual(report.level, ERROR)
        self.assertIn("theme_layer_missing", [f.code for f in report.errors])

    def test_a_theme_layer_with_no_features_is_an_error(self):
        """A layer that is present but empty draws nothing: the sheet still lies."""
        themed_layer(self.project, "Aree allagabili", "flood_hazard", empty=True)
        report = validate_layout(self.build("flood_hazard_map"), "flood_hazard_map")
        self.assertEqual(report.level, ERROR)
        self.assertIn("theme_layer_empty", [f.code for f in report.errors])

    def test_a_theme_drawn_but_absent_from_the_legend_is_an_error(self):
        layout = self.build("flood_hazard_map")
        themed_layer(self.project, "Aree allagabili", "flood_hazard")
        layout = self.build("flood_hazard_map")
        for item in layout.items():
            if isinstance(item, QgsLayoutItemLegend):
                layout.removeLayoutItem(item)
                break
        report = validate_layout(layout, "flood_hazard_map")
        self.assertEqual(report.level, ERROR)
        self.assertIn("theme_missing_from_legend", [f.code for f in report.errors])

    def test_a_layout_without_a_map_frame_is_an_error(self):
        from qgis.core import QgsPrintLayout

        layout = QgsPrintLayout(self.project)
        layout.initializeDefaults()
        report = validate_layout(layout, "flood_hazard_map")
        self.assertEqual(report.level, ERROR)
        self.assertIn("no_map_frame", [f.code for f in report.errors])


class TestExclusions(SheetCase):
    """A vincolo is not a risk, and a sheet must not say otherwise."""

    def test_a_constraint_may_not_ride_on_the_risk_sheet(self):
        spec = specification_for("risk_map")
        self.assertIn("hydro_geomorphological", spec.excluded_categories,
                      "il contratto della carta del rischio deve escludere i vincoli")

    def test_an_excluded_category_on_the_sheet_is_an_error(self):
        themed_layer(self.project, "Pericolosita", "flood_hazard")
        vincolo = themed_layer(self.project, "Vincolo idrogeologico",
                               "hydro_geomorphological")
        layout = self.build("risk_map")
        from territorial_suite.engines.cartography.overview import main_map

        # Il template non lo raccoglie piu'; qui lo si forza nel riquadro per
        # verificare che il controllo lo intercetti comunque.
        map_item = main_map(layout)
        map_item.setLayers(list(map_item.layers()) + [vincolo])
        report = SheetValidator(specification_for("risk_map")).validate(layout)
        self.assertEqual(report.level, ERROR)
        self.assertIn("excluded_category_present", [f.code for f in report.errors])

    def test_the_risk_template_no_longer_collects_constraints(self):
        """The defect this fixes: «Carta del rischio» used to print vincoli."""
        from territorial_suite.engines.cartography.layout import get_template

        categories = get_template("risk_map").get("layers", {}).get("categories", [])
        self.assertNotIn("hydro_geomorphological", categories)
        self.assertIn("flood_hazard", categories)


class TestLegendToTitle(SheetCase):
    """The reverse direction: a legend full of other themes is suspicious."""

    def test_a_legend_dominated_by_other_themes_warns(self):
        themed_layer(self.project, "Aree allagabili", "flood_hazard")
        spec = specification_for("flood_hazard_map")
        report = sheet_spec.QaReport(theme=spec.theme)
        legend = [themed_layer(self.project, "Beni culturali", "landscape_cultural"),
                  themed_layer(self.project, "Natura 2000", "natura2000"),
                  themed_layer(self.project, "Cave", "anthropic_pressure")]
        SheetValidator(spec)._check_legend_is_focused(report, legend)
        self.assertEqual(report.level, WARNING)
        self.assertIn("legend_not_focused", [f.code for f in report.warnings])

    def test_context_layers_alone_do_not_warn(self):
        """Boundaries and an orthophoto are legitimate context, not a different subject."""
        spec = specification_for("flood_hazard_map")
        report = sheet_spec.QaReport(theme=spec.theme)
        legend = [themed_layer(self.project, "Comuni", "administrative"),
                  themed_layer(self.project, "Ortofoto", "imagery")]
        SheetValidator(spec)._check_legend_is_focused(report, legend)
        self.assertEqual(report.level, OK)


class TestTitles(SheetCase):
    def test_a_title_that_does_not_name_the_theme_warns(self):
        themed_layer(self.project, "Aree allagabili", "flood_hazard")
        layout = self.build("flood_hazard_map")
        report = SheetValidator(specification_for("flood_hazard_map")).validate(
            layout, title="Tavola 3")
        self.assertIn("title_does_not_name_theme", [f.code for f in report.warnings])

    def test_a_sheet_without_a_declared_theme_skips_the_theme_checks(self):
        themed_layer(self.project, "Comuni", "administrative")
        report = validate_layout(self.build("territorial_overview"),
                                 "territorial_overview")
        self.assertFalse(report.blocking)

    def test_an_empty_title_is_an_error(self):
        spec = MapSheetSpecification(theme="flood_hazard")
        report = sheet_spec.QaReport()
        SheetValidator(spec)._check_title(report, "")
        self.assertEqual(report.level, ERROR)


class TestReportShape(SheetCase):
    def test_the_report_serialises_for_the_dossier(self):
        themed_layer(self.project, "Aree allagabili", "flood_hazard")
        report = validate_layout(self.build("flood_hazard_map"), "flood_hazard_map")
        payload = report.as_dict()
        for key in ("template", "title", "theme", "level", "summary", "findings"):
            self.assertIn(key, payload)
        import json

        json.dumps(payload)

    def test_the_worst_level_wins(self):
        self.assertEqual(sheet_spec.worst([OK, WARNING, ERROR]), ERROR)
        self.assertEqual(sheet_spec.worst([OK, WARNING]), WARNING)
        self.assertEqual(sheet_spec.worst([]), OK)


class TestExportRefusesABrokenSheet(SheetCase):
    """A wrong map that looks finished is worse than no map: it must not be written."""

    def out(self, name="tavola.png"):
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name) / name

    def test_the_sheet_remembers_which_contract_it_was_built_under(self):
        from territorial_suite.core.constants import PROP_LAYOUT_TEMPLATE

        layout = self.build("flood_hazard_map")
        self.assertEqual(layout.customProperty(PROP_LAYOUT_TEMPLATE, ""),
                         "flood_hazard_map")

    def test_a_sheet_that_breaks_its_contract_is_not_written(self):
        from territorial_suite.engines.cartography.export import PNG, ExportCenter

        themed_layer(self.project, "Strade", "roads")       # nessun tema dichiarato
        layout = self.build("flood_hazard_map")
        target = self.out()
        result = ExportCenter.export(layout, target, PNG)
        self.assertFalse(result.ok)
        self.assertFalse(target.exists(), "il file non deve essere scritto")
        self.assertIsNotNone(result.qa)
        self.assertTrue(result.qa.blocking)
        self.assertIn("contratto", result.message.lower())

    def test_a_draft_can_be_exported_on_purpose(self):
        from territorial_suite.engines.cartography.export import PNG, ExportCenter

        themed_layer(self.project, "Strade", "roads")
        layout = self.build("flood_hazard_map")
        target = self.out("bozza.png")
        result = ExportCenter.export(layout, target, PNG, validate=False)
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(result.qa)

    def test_a_valid_sheet_is_written_and_carries_its_report(self):
        from territorial_suite.engines.cartography.export import PNG, ExportCenter

        themed_layer(self.project, "Aree allagabili", "flood_hazard")
        layout = self.build("flood_hazard_map")
        target = self.out("valida.png")
        result = ExportCenter.export(layout, target, PNG)
        self.assertTrue(result.ok, result.message)
        self.assertIsNotNone(result.qa)
        self.assertFalse(result.qa.blocking)


if __name__ == "__main__":
    unittest.main()
