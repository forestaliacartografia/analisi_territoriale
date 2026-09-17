"""Spatial measurement and rule evaluation."""

from __future__ import annotations

import unittest

from qgis.core import QgsFeature, QgsGeometry, QgsRectangle, QgsVectorLayer

from territorial_suite.core import qt_compat
from territorial_suite.core.models import (
    Alert,
    AlertLevel,
    AnalysisReport,
    EvidenceLevel,
    Provenance,
    SlopeClass,
    SourceResult,
    SourceStatus,
    TerrainStats,
)
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.core.registry import DataSource
from territorial_suite.engines import spatial
from territorial_suite.engines.rules import Rule, RuleEngine

# 1 km square in EPSG:32632 -> 1 000 000 m2
AREA_RECT = QgsRectangle(680000, 4848000, 681000, 4849000)

SOURCE = DataSource.from_dict({
    "id": "test.source", "name": "Fonte di prova", "type": "WFS",
    "url": "https://example.org/wfs", "layer": "x", "category": "landscape_cultural",
    "fields": {"label": "nome"}, "evidence_level": "declaratory",
})


def polygon_layer(rects, crs: str = "EPSG:32632") -> QgsVectorLayer:
    """Build a polygon layer with one feature per rectangle."""
    layer = QgsVectorLayer(f"Polygon?crs={crs}", "test", "memory")
    layer.dataProvider().addAttributes([qt_compat.field("nome", qt_compat.STRING)])
    layer.updateFields()
    features = []
    for index, rect in enumerate(rects):
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromRect(rect))
        feature.setAttributes([f"elemento {index + 1}"])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def project_area() -> ProjectArea:
    """The 1 km test area."""
    return ProjectArea.from_rectangle(AREA_RECT, "EPSG:32632", name="Test")


class TestAnalyseLayer(unittest.TestCase):
    def test_half_overlap_gives_fifty_percent(self):
        area = project_area()
        layer = polygon_layer([QgsRectangle(680500, 4848000, 681500, 4849000)])
        result = spatial.analyse_layer(area, layer, source=SOURCE)
        self.assertTrue(result.present)
        self.assertEqual(result.feature_count, 1)
        self.assertAlmostEqual(result.intersect_pct, 50.0, delta=1.0)
        self.assertAlmostEqual(result.intersect_area_m2, 500_000, delta=10_000)
        self.assertEqual(result.min_distance_m, 0.0)
        self.assertEqual(result.hits[0].label, "elemento 1")

    def test_overlapping_features_do_not_exceed_one_hundred_percent(self):
        area = project_area()
        layer = polygon_layer([AREA_RECT, AREA_RECT, AREA_RECT])
        result = spatial.analyse_layer(area, layer, source=SOURCE)
        self.assertEqual(result.feature_count, 3)
        self.assertAlmostEqual(result.intersect_pct, 100.0, delta=1.0)

    def test_distance_is_measured_for_nearby_features(self):
        area = project_area()
        layer = polygon_layer([QgsRectangle(681200, 4848000, 681300, 4848100)])
        result = spatial.analyse_layer(area, layer, source=SOURCE, max_distance_m=1000)
        self.assertFalse(result.present)
        self.assertEqual(result.feature_count, 0)
        self.assertAlmostEqual(result.min_distance_m, 200.0, delta=2.0)
        self.assertEqual(len(result.hits), 1)

    def test_features_beyond_the_radius_are_ignored(self):
        area = project_area()
        layer = polygon_layer([QgsRectangle(690000, 4848000, 690100, 4848100)])
        result = spatial.analyse_layer(area, layer, source=SOURCE, max_distance_m=1000)
        self.assertFalse(result.present)
        self.assertEqual(result.hits, [])
        self.assertIsNone(result.min_distance_m)

    def test_layer_in_another_crs_is_reprojected(self):
        area = project_area()
        wgs = QgsRectangle(11.0, 43.77, 11.5, 43.80)
        layer = polygon_layer([wgs], crs="EPSG:4326")
        result = spatial.analyse_layer(area, layer, source=SOURCE)
        self.assertTrue(result.present)

    def test_result_wrapper_marks_empty_sources(self):
        empty = spatial.result_from_intersection(SOURCE, spatial.Intersection())
        self.assertEqual(empty.status, SourceStatus.EMPTY)
        self.assertFalse(empty.present)
        self.assertEqual(empty.evidence_level, EvidenceLevel.DECLARATORY)

    def test_failed_result_keeps_the_error(self):
        failed = spatial.failed_result(SOURCE, "timeout")
        self.assertFalse(failed.ok)
        self.assertEqual(failed.error, "timeout")
        self.assertEqual(failed.status, SourceStatus.OFFLINE)


class TestSearchRadius(unittest.TestCase):
    """The distance radius can never exceed the data actually downloaded."""

    def tearDown(self):
        from territorial_suite.core import settings

        settings.reset("analysis.max_distance_m")
        settings.reset("analysis.context_buffer_m")

    def test_radius_is_clamped_to_the_context_buffer(self):
        from territorial_suite.core import settings
        from territorial_suite.engines.constraints import ConstraintEngine

        settings.set_value("analysis.context_buffer_m", 800.0)
        settings.set_value("analysis.max_distance_m", 5000.0)
        self.assertEqual(ConstraintEngine.search_radius(), 800.0)

    def test_radius_follows_the_smaller_setting(self):
        from territorial_suite.core import settings
        from territorial_suite.engines.constraints import ConstraintEngine

        settings.set_value("analysis.context_buffer_m", 2000.0)
        settings.set_value("analysis.max_distance_m", 300.0)
        self.assertEqual(ConstraintEngine.search_radius(), 300.0)


def result(**kwargs) -> SourceResult:
    """Build a source result with sensible defaults."""
    payload = dict(source_id="test.source", source_name="Fonte", category="water",
                   status=SourceStatus.ONLINE, present=False,
                   provenance=Provenance(source_id="test.source",
                                         evidence_level=EvidenceLevel.CARTOGRAPHIC))
    payload.update(kwargs)
    return SourceResult(**payload)


def report_with(*results, **kwargs) -> AnalysisReport:
    """Build an analysis report around the given results."""
    return AnalysisReport(area=kwargs.pop("area", {"area_m2": 1_000_000.0, "name": "T"}),
                          results=list(results), **kwargs)


class TestRuleEngine(unittest.TestCase):
    def evaluate(self, rule_payload, report) -> list:
        engine = RuleEngine([Rule.from_dict(rule_payload)])
        return engine.evaluate(report)

    def test_intersects_rule(self):
        alerts = self.evaluate(
            {"id": "r1", "title": "Presente {source}", "level": "CHECK_REQUIRED",
             "when": {"category": "water", "operation": "intersects"}},
            report_with(result(present=True, feature_count=2, intersect_pct=12.5,
                               intersect_area_m2=125000)))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].level, AlertLevel.CHECK_REQUIRED)
        self.assertIn("Fonte", alerts[0].title)

    def test_distance_rule_only_fires_below_threshold(self):
        payload = {"id": "r2", "title": "Vicino a {distance}", "level": "ATTENTION",
                   "when": {"category": "water", "operation": "distance_lt",
                            "threshold": 150}}
        near = self.evaluate(payload, report_with(result(min_distance_m=80.0)))
        far = self.evaluate(payload, report_with(result(min_distance_m=800.0)))
        self.assertEqual(len(near), 1)
        self.assertEqual(far, [])
        self.assertIn("80", near[0].title)

    def test_within_or_near_covers_both_cases(self):
        payload = {"id": "r3", "title": "Entro {threshold}", "level": "INFO",
                   "when": {"category": "water", "operation": "within_or_near",
                            "threshold": 150}}
        self.assertEqual(len(self.evaluate(payload, report_with(result(present=True)))), 1)
        self.assertEqual(len(self.evaluate(payload,
                                           report_with(result(min_distance_m=100.0)))), 1)
        self.assertEqual(self.evaluate(payload, report_with(result(min_distance_m=900.0))), [])

    def test_area_percentage_rule(self):
        payload = {"id": "r4", "title": "Oltre il {threshold}%", "level": "ATTENTION",
                   "when": {"category": "water", "operation": "area_pct_gt",
                            "threshold": 30}}
        self.assertEqual(len(self.evaluate(payload,
                                           report_with(result(present=True,
                                                              intersect_pct=45.0)))), 1)
        self.assertEqual(self.evaluate(payload,
                                       report_with(result(present=True,
                                                          intersect_pct=10.0))), [])

    def test_source_id_selector(self):
        payload = {"id": "r5", "title": "x", "level": "INFO",
                   "when": {"source_id": "test.source", "operation": "intersects"}}
        self.assertEqual(len(self.evaluate(payload, report_with(result(present=True)))), 1)
        other = result(source_id="other.source", present=True)
        self.assertEqual(self.evaluate(payload, report_with(other)), [])

    def test_area_subject_rules(self):
        from territorial_suite.core.models import AdminUnit, AdminUnits

        report = report_with(admin=AdminUnits(
            municipalities=[AdminUnit(name="A"), AdminUnit(name="B")], resolved=True))
        alerts = self.evaluate(
            {"id": "r6", "title": "Piu' Comuni: {count}", "level": "ATTENTION",
             "when": {"subject": "area", "operation": "multi_municipality"}}, report)
        self.assertEqual(len(alerts), 1)
        self.assertIn("2", alerts[0].title)

    def test_terrain_subject_rules(self):
        report = report_with(terrain=TerrainStats(
            elevation_min=100.0, slope_mean_pct=48.0,
            slope_classes=[SlopeClass(lower=30, upper=50, area_pct=40.0)]))
        alerts = self.evaluate(
            {"id": "r7", "title": "Pendenza {value}", "level": "ATTENTION",
             "when": {"subject": "terrain", "operation": "slope_mean_gt",
                      "threshold": 35}}, report)
        self.assertEqual(len(alerts), 1)

    def test_unknown_operation_does_not_raise(self):
        alerts = self.evaluate({"id": "r8", "title": "x", "level": "INFO",
                                "when": {"category": "water", "operation": "nope"}},
                               report_with(result(present=True)))
        self.assertEqual(alerts, [])

    def test_shipped_rules_load_and_are_consistent(self):
        engine = RuleEngine.load()
        self.assertGreater(len(engine.rules), 8)
        for rule in engine.rules:
            self.assertTrue(rule.id)
            self.assertTrue(rule.title)
            self.assertIsInstance(rule.level, AlertLevel)

    def test_alerts_are_sorted_by_severity(self):
        engine = RuleEngine([
            Rule.from_dict({"id": "info", "title": "info", "level": "INFO",
                            "when": {"category": "water", "operation": "intersects"}}),
            Rule.from_dict({"id": "check", "title": "check", "level": "CHECK_REQUIRED",
                            "when": {"category": "water", "operation": "intersects"}}),
        ])
        alerts = engine.evaluate(report_with(result(present=True)))
        self.assertEqual(alerts[0].level, AlertLevel.CHECK_REQUIRED)


if __name__ == "__main__":
    unittest.main()
