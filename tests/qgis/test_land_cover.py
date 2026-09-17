"""Land cover is measured, not displayed.

Two properties matter more than any other here:

* the surface of a class is the part **inside the project area**, not the extent of the
  polygons that happen to touch it. A CLC polygon is often far larger than the area under
  study, and using its own area field would overstate every figure;
* aggregating to level 1 or 2 must not lose the official code. "Superfici artificiali" is
  a reading of ``111`` and ``112``, not a replacement for them.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsRectangle, QgsVectorLayer
from qgis.PyQt.QtCore import QMetaType

from territorial_suite.core.gaps import DataGap
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines import land_cover
from territorial_suite.engines.land_cover import LandCoverEngine, label_for, known_code

# Un'area di 1 km circa, in gradi, su Firenze.
AREA = ProjectArea.from_rectangle(QgsRectangle(11.240, 43.760, 11.260, 43.775),
                                  "EPSG:4326", name="Firenze")


def clc_layer(rows):
    """A CLC-shaped layer: one polygon per (code, rectangle)."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "CLC", "memory")
    layer.dataProvider().addAttributes(
        [QgsField("Code_18", QMetaType.Type.QString)])
    layer.updateFields()
    features = []
    for code, rect in rows:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromRect(rect))
        feature["Code_18"] = code
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


class TestNomenclature(unittest.TestCase):
    def setUp(self):
        land_cover.reload_nomenclature()
        self.addCleanup(land_cover.reload_nomenclature)

    def test_the_official_labels_come_from_configuration(self):
        self.assertEqual(label_for("311", 3, "en"), "Broad-leaved forest")
        self.assertIn("latifoglie", label_for("311", 3, "it").lower())

    def test_a_code_resolves_at_every_level(self):
        self.assertIn("boscat", label_for("311", 1, "it").lower())
        self.assertIn("bosc", label_for("311", 2, "it").lower())

    def test_an_unknown_code_is_returned_as_itself(self):
        """Inventing a name for an unlisted class would be worse than admitting it."""
        self.assertEqual(label_for("999", 3, "it"), "999")
        self.assertFalse(known_code("999"))
        self.assertTrue(known_code("311"))

    def test_the_nomenclature_covers_the_three_levels(self):
        data = land_cover.nomenclature()
        self.assertEqual(len(data["classes"]), 44)
        self.assertEqual(len(data["levels"]["1"]), 5)
        self.assertEqual(len(data["levels"]["2"]), 15)


class TestMeasurement(unittest.TestCase):
    def setUp(self):
        land_cover.reload_nomenclature()
        self.addCleanup(land_cover.reload_nomenclature)
        self.engine = LandCoverEngine(code_field="Code_18")

    def test_only_the_part_inside_the_area_is_counted(self):
        """A polygon ten times the area must not contribute ten times the surface."""
        huge = QgsRectangle(11.0, 43.6, 11.5, 44.0)          # molto piu' grande dell'area
        layer = clc_layer([("311", huge)])
        outcome = self.engine.measure_layer(AREA, layer)
        self.assertTrue(outcome.ok)
        only = outcome.classes[0]
        self.assertAlmostEqual(only.area_m2, outcome.area_m2, delta=outcome.area_m2 * 0.02)
        self.assertAlmostEqual(only.percentage, 100.0, delta=2.0)

    def test_two_classes_split_the_area(self):
        west = QgsRectangle(11.240, 43.760, 11.250, 43.775)
        east = QgsRectangle(11.250, 43.760, 11.260, 43.775)
        outcome = self.engine.measure_layer(AREA, clc_layer([("311", west),
                                                             ("111", east)]))
        self.assertEqual(len(outcome.classes), 2)
        for row in outcome.classes:
            self.assertAlmostEqual(row.percentage, 50.0, delta=3.0)
        self.assertAlmostEqual(sum(c.percentage for c in outcome.classes), 100.0, delta=3.0)

    def test_the_dominant_class_is_the_largest(self):
        big = QgsRectangle(11.240, 43.760, 11.257, 43.775)
        small = QgsRectangle(11.257, 43.760, 11.260, 43.775)
        outcome = self.engine.measure_layer(AREA, clc_layer([("311", big), ("111", small)]))
        self.assertEqual(outcome.dominant.code, "311")

    def test_a_polygon_outside_the_area_contributes_nothing(self):
        far = QgsRectangle(7.0, 45.0, 7.1, 45.1)
        outcome = self.engine.measure_layer(AREA, clc_layer([("311", far)]))
        self.assertFalse(outcome.ok)
        self.assertIn(DataGap.NO_FEATURE_FOUND.value, outcome.gaps)


class TestAggregationKeepsTheCode(unittest.TestCase):
    def setUp(self):
        land_cover.reload_nomenclature()
        self.addCleanup(land_cover.reload_nomenclature)
        self.engine = LandCoverEngine(code_field="Code_18")
        west = QgsRectangle(11.240, 43.760, 11.250, 43.775)
        east = QgsRectangle(11.250, 43.760, 11.260, 43.775)
        self.layer = clc_layer([("111", west), ("112", east)])

    def test_level_three_keeps_the_classes_apart(self):
        outcome = self.engine.measure_layer(AREA, self.layer, level=3)
        self.assertEqual(sorted(c.code for c in outcome.classes), ["111", "112"])

    def test_level_one_folds_them_but_records_the_originals(self):
        outcome = self.engine.measure_layer(AREA, self.layer, level=1)
        self.assertEqual(len(outcome.classes), 1)
        folded = outcome.classes[0]
        self.assertEqual(folded.code, "1")
        self.assertIn("artificial", folded.label.lower())
        self.assertEqual(folded.official_codes, ["111", "112"],
                         "l'aggregazione non deve far perdere la classe di origine")
        self.assertAlmostEqual(folded.percentage, 100.0, delta=3.0)

    def test_level_two_folds_to_the_urban_fabric(self):
        outcome = self.engine.measure_layer(AREA, self.layer, level=2)
        self.assertEqual(outcome.classes[0].code, "11")
        self.assertEqual(outcome.classes[0].official_codes, ["111", "112"])


class TestHonestyAboutWhatIsMissing(unittest.TestCase):
    def setUp(self):
        land_cover.reload_nomenclature()
        self.addCleanup(land_cover.reload_nomenclature)
        self.engine = LandCoverEngine(code_field="Code_18")

    def test_a_partly_classified_area_says_so(self):
        """CLC maps nothing under 25 ha, so a small area is often only partly covered."""
        half = QgsRectangle(11.240, 43.760, 11.250, 43.775)
        outcome = self.engine.measure_layer(AREA, clc_layer([("311", half)]))
        self.assertIn(DataGap.PARTIAL_COVERAGE.value, outcome.gaps)
        self.assertTrue(any("classificat" in w for w in outcome.warnings))
        self.assertLess(outcome.coverage_pct, 99.0)

    def test_a_fully_classified_area_declares_no_gap(self):
        whole = QgsRectangle(11.20, 43.70, 11.30, 43.80)
        outcome = self.engine.measure_layer(AREA, clc_layer([("311", whole)]))
        self.assertEqual(outcome.gaps, [])

    def test_a_missing_code_field_is_reported_not_guessed(self):
        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "senza codice", "memory")
        outcome = self.engine.measure_layer(AREA, layer)
        self.assertIn(DataGap.QUERY_FAILED.value, outcome.gaps)
        self.assertFalse(outcome.ok)

    def test_an_invalid_layer_is_a_source_gap(self):
        outcome = self.engine.measure_layer(AREA, QgsVectorLayer("", "rotto", "memory"))
        self.assertIn(DataGap.SOURCE_UNAVAILABLE.value, outcome.gaps)

    def test_an_unrecognised_code_is_flagged_but_still_measured(self):
        whole = QgsRectangle(11.20, 43.70, 11.30, 43.80)
        outcome = self.engine.measure_layer(AREA, clc_layer([("999", whole)]))
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.classes[0].recognised)


class TestSerialisation(unittest.TestCase):
    def test_the_outcome_fits_the_dossier(self):
        import json

        engine = LandCoverEngine(code_field="Code_18")
        whole = QgsRectangle(11.20, 43.70, 11.30, 43.80)
        outcome = engine.measure_layer(AREA, clc_layer([("311", whole)]))
        payload = outcome.as_dict()
        for key in ("level", "area_m2", "covered_m2", "coverage_pct", "dominant",
                    "classes", "gaps", "warnings"):
            self.assertIn(key, payload)
        json.dumps(payload)
        self.assertIn("area_ha", payload["classes"][0])


if __name__ == "__main__":
    unittest.main()
