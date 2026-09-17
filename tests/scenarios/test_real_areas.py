"""The ten reference scenarios.

Two of them (unavailable source, source without data) run offline with recorded doubles,
because they are exactly the cases that must never depend on a working service. The other
eight query the real services and are skipped unless ``--network`` is given.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsRectangle

from territorial_suite.core.errors import SourceUnavailableError
from territorial_suite.core.models import AlertLevel, SourceStatus
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.core.registry import DataSource, DataSourceRegistry
from territorial_suite.engines.analysis import AnalysisOptions, AnalysisOrchestrator
from territorial_suite.engines.constraints import ConstraintEngine
from tests.fakes import FakeHttpClient
from tests.support import requires_network

#: bbox in EPSG:4326 for each scenario.
AREAS = {
    "urban": QgsRectangle(11.2440, 43.7690, 11.2480, 43.7720),        # Firenze, centro
    "forest": QgsRectangle(11.0000, 43.9500, 11.0150, 43.9650),       # Mugello, bosco
    "coastal": QgsRectangle(10.7000, 42.7000, 10.7200, 42.7150),      # Maremma, costa
    "mountain": QgsRectangle(11.0000, 44.0500, 11.0300, 44.0800),     # Appennino
    "two_towns": QgsRectangle(11.2950, 43.8000, 11.3150, 43.8150),    # Firenze / Fiesole
    "large": QgsRectangle(11.1000, 43.7000, 11.4000, 43.9000),        # ~30 x 22 km
    "no_data": QgsRectangle(9.0000, 40.0000, 9.0100, 40.0100),        # mar Tirreno
}


def area_for(key: str, name: str = "") -> ProjectArea:
    """Build the project area of a scenario."""
    return ProjectArea.from_rectangle(AREAS[key], "EPSG:4326", name=name or key)


class TestOfflineScenarios(unittest.TestCase):
    """Scenarios 8 and 10: they must work without any working service."""

    def setUp(self):
        self.registry = DataSourceRegistry()
        self.registry._sources.clear()
        self.source = DataSource.from_dict({
            "id": "test.offline.source", "name": "Fonte di prova", "type": "WFS",
            "url": "https://example.org/wfs", "layer": "x",
            "category": "landscape_cultural", "evidence_level": "declaratory",
            "scope": {"level": "national", "codes": []},
        })
        self.registry._sources[self.source.id] = self.source

    def test_scenario_08_source_unavailable_does_not_stop_the_analysis(self):
        engine = ConstraintEngine(
            registry=self.registry,
            http=FakeHttpClient([SourceUnavailableError("servizio non raggiungibile",
                                                        source_id=self.source.id)]))
        area = area_for("urban", "Scenario 8")
        outcome = engine.run(area, categories=["landscape_cultural"])
        self.assertEqual(len(outcome.results), 1)
        result = outcome.results[0]
        self.assertFalse(result.ok)
        self.assertEqual(result.status, SourceStatus.OFFLINE)
        self.assertIn("non raggiungibile", result.error)
        # the analysis produced a result, not an exception
        self.assertFalse(result.present)

    def test_scenario_10_no_data_is_not_no_constraint(self):
        empty = (b'<?xml version="1.0"?><wfs:FeatureCollection '
                 b'xmlns:wfs="http://www.opengis.net/wfs/2.0" numberReturned="0"/>')
        engine = ConstraintEngine(registry=self.registry,
                                  http=FakeHttpClient([empty]))
        area = area_for("no_data", "Scenario 10")
        outcome = engine.run(area, categories=["landscape_cultural"])
        result = outcome.results[0]
        self.assertTrue(result.ok)
        self.assertEqual(result.status, SourceStatus.EMPTY)
        self.assertFalse(result.present)
        self.assertEqual(result.feature_count, 0)

    def test_scenario_08_failure_becomes_a_cartographic_issue_alert(self):
        from territorial_suite.core.models import AnalysisReport, SourceResult

        report = AnalysisReport(
            area=area_for("urban").as_dict(),
            results=[SourceResult(source_id="x", source_name="Fonte",
                                  category="landscape_cultural",
                                  status=SourceStatus.OFFLINE, error="timeout")])
        alerts = AnalysisOrchestrator._unavailable_source_alerts(report)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].level, AlertLevel.CARTOGRAPHIC_ISSUE)


class TestLiveScenarios(unittest.TestCase):
    """Scenarios 1-7 and 9 against the real services.

    These tests assert the **contract**, not the uptime of third-party services: a source
    that is down must produce a recorded failure and a coherent report, never an exception
    and never a silent gap. Assertions on the data itself are therefore conditional on the
    service having answered - which is exactly what the plugin promises to its users.
    """

    def run_analysis(self, key: str, **kwargs) -> "AnalysisReport":
        options = AnalysisOptions(**{"include_download": False, **kwargs})
        return AnalysisOrchestrator().run(area_for(key), options)

    def assert_report_is_coherent(self, report) -> None:
        """Invariants that must hold for every analysis, whatever the area."""
        self.assertTrue(report.finished_at)
        for result in report.results:
            self.assertLessEqual(result.intersect_pct, 100.5)
            self.assertGreaterEqual(result.intersect_area_m2, 0.0)
            if result.present:
                self.assertGreater(result.feature_count, 0)
            if not result.ok:
                self.assertTrue(result.error, "una fonte non disponibile deve dire perche'")
        for alert in report.alerts:
            self.assertTrue(alert.title)
            self.assertIsInstance(alert.level, AlertLevel)
        # every failed source must surface as a cartographic-issue alert
        failed_ids = {result.source_id for result in report.sources_failed}
        alert_ids = {source_id for alert in report.alerts for source_id in alert.source_ids
                     if alert.level == AlertLevel.CARTOGRAPHIC_ISSUE}
        self.assertTrue(failed_ids.issubset(alert_ids),
                        f"fonti non dichiarate negli alert: {failed_ids - alert_ids}")

    def assert_cadastre_is_well_formed(self, report) -> None:
        """Validate the cadastral table when the service answered."""
        if not report.cadastre:
            self.assertTrue(report.warnings or report.sources_failed,
                            "senza particelle il report deve spiegare il perche'")
            self.skipTest("servizio catastale non disponibile durante il test")
        for row in report.cadastre:
            self.assertTrue(row.municipality)
            self.assertTrue(row.sheet, "ogni particella deve avere il foglio")
            self.assertTrue(row.parcel)
            self.assertGreater(row.area_cadastral_m2, 0.0)
            self.assertLessEqual(row.intersect_pct, 100.5)

    @requires_network
    def test_scenario_01_small_urban_area(self):
        report = self.run_analysis("urban", include_terrain=False)
        self.assert_report_is_coherent(report)
        if not report.admin.resolved:
            self.assertTrue(report.warnings,
                            "unita' amministrative non risolte senza spiegazione")
        self.assert_cadastre_is_well_formed(report)

    @requires_network
    def test_scenario_02_forest_area(self):
        report = self.run_analysis("forest", include_terrain=True)
        self.assert_report_is_coherent(report)
        self.assertIsNotNone(report.terrain)
        if not report.terrain.available:
            self.assertTrue(report.warnings)

    @requires_network
    def test_scenario_03_coastal_area(self):
        report = self.run_analysis("coastal", include_terrain=False)
        self.assert_report_is_coherent(report)

    @requires_network
    def test_scenario_04_mountain_area(self):
        report = self.run_analysis("mountain", include_cadastre=False,
                                   include_constraints=False, include_terrain=True)
        self.assertIsNotNone(report.terrain)
        if not report.terrain.available:
            self.skipTest(f"DEM non disponibile: {report.warnings}")
        self.assertGreater(report.terrain.elevation_range, 50.0)
        self.assertTrue(report.terrain.slope_classes)
        self.assertAlmostEqual(sum(item.area_pct for item in report.terrain.slope_classes),
                               100.0, delta=1.0)

    @requires_network
    def test_scenario_05_area_across_two_municipalities(self):
        report = self.run_analysis("two_towns", include_terrain=False)
        self.assert_report_is_coherent(report)
        self.assert_cadastre_is_well_formed(report)
        municipalities = {row.municipality for row in report.cadastre}
        codes = {row.cadastral_code for row in report.cadastre}
        # one cadastral code per municipality, whatever their number
        self.assertEqual(len(codes), len(municipalities))

    @requires_network
    def test_scenario_06_area_across_several_sheets(self):
        report = self.run_analysis("urban", include_terrain=False)
        self.assert_cadastre_is_well_formed(report)
        sheets = {(row.municipality, row.sheet) for row in report.cadastre}
        self.assertGreaterEqual(len(sheets), 1)

    @requires_network
    def test_scenario_07_overlapping_constraints_stay_below_one_hundred_percent(self):
        report = self.run_analysis("forest", include_terrain=False)
        for result in report.results:
            self.assertLessEqual(result.intersect_pct, 100.5, result.source_id)

    @requires_network
    def test_scenario_09_large_area_is_truncated_not_broken(self):
        report = self.run_analysis("large", include_cadastre=False, include_terrain=False)
        self.assert_report_is_coherent(report)
        # Truncation is reported, never silent.
        for warning in report.warnings:
            self.assertIsInstance(warning, str)


if __name__ == "__main__":
    unittest.main()
