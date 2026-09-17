"""Processing provider: registration and execution of the network-free algorithms."""

from __future__ import annotations

import unittest

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsRectangle,
    QgsVectorLayer,
)

try:  # pragma: no cover - depends on the QGIS installation layout
    import processing
    from processing.core.Processing import Processing

    PROCESSING_AVAILABLE = True
except Exception:  # pragma: no cover - Processing not importable
    PROCESSING_AVAILABLE = False

from territorial_suite.processing.provider import TerritorialSuiteProvider

requires_processing = unittest.skipUnless(PROCESSING_AVAILABLE,
                                          "framework Processing non disponibile")

AREA_RECT = QgsRectangle(680000, 4848000, 681000, 4849000)


def area_layer() -> QgsVectorLayer:
    """A one-feature polygon layer used as the project-area parameter."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:32632", "area", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromRect(AREA_RECT))
    layer.dataProvider().addFeature(feature)
    layer.updateExtents()
    return layer


@requires_processing
class TestProvider(unittest.TestCase):
    provider = None

    @classmethod
    def setUpClass(cls):
        Processing.initialize()
        cls.provider = TerritorialSuiteProvider()
        QgsApplication.processingRegistry().addProvider(cls.provider)

    @classmethod
    def tearDownClass(cls):
        if cls.provider is not None:
            QgsApplication.processingRegistry().removeProvider(cls.provider)

    def test_every_algorithm_is_registered(self):
        registry = QgsApplication.processingRegistry()
        names = {alg.id() for alg in registry.algorithms()
                 if alg.id().startswith("territorial_suite:")}
        for expected in ("area_statistics", "generate_buffers", "analyze_area",
                         "analyze_constraints", "query_cadastre", "terrain_statistics",
                         "elevation_profile", "download_area_dataset",
                         "generate_quick_map", "generate_map_series",
                         "generate_territorial_report", "export_area_package",
                         "cultural_heritage", "orthophoto_sheet", "imagery_providers"):
            self.assertIn(f"territorial_suite:{expected}", names)

    def test_imagery_providers_lists_the_catalogue(self):
        result = processing.run("territorial_suite:imagery_providers", {"OUTPUT": None})
        self.assertGreaterEqual(result["COUNT"], 2)

    def test_orthophoto_sheet_runs_and_reports_the_provider(self):
        from territorial_suite.core import settings

        previous = settings.get("cartography.probe_imagery", True)
        settings.set_value("cartography.probe_imagery", False)
        try:
            result = processing.run("territorial_suite:orthophoto_sheet",
                                    {"AREA": area_layer(), "AREA_NAME": "Test",
                                     "PROVIDER": 2, "TITLE": "Tavola di prova"})
        finally:
            settings.set_value("cartography.probe_imagery", previous)
        # Google was asked for and is not configured: the fallback must be declared.
        self.assertTrue(result["ATTRIBUTION"])
        self.assertTrue(result["FALLBACK"])
        self.assertNotEqual(result["PROVIDER"], "google")

    def test_every_algorithm_documents_itself(self):
        registry = QgsApplication.processingRegistry()
        for alg in registry.algorithms():
            if not alg.id().startswith("territorial_suite:"):
                continue
            self.assertTrue(alg.displayName(), alg.id())
            self.assertGreater(len(alg.shortHelpString()), 40, alg.id())

    def test_area_statistics_runs(self):
        result = processing.run("territorial_suite:area_statistics",
                                {"AREA": area_layer(), "AREA_NAME": "Test",
                                 "OUTPUT": "memory:"})
        self.assertIn("OUTPUT", result)
        layer = result["OUTPUT"]
        self.assertEqual(layer.featureCount(), 1)
        feature = next(layer.getFeatures())
        self.assertAlmostEqual(feature["area_m2"], 1_000_000, delta=3000)
        self.assertAlmostEqual(feature["perimeter_m"], 4000, delta=20)
        self.assertEqual(feature["crs"], "EPSG:32632")

    def test_generate_buffers_runs(self):
        result = processing.run("territorial_suite:generate_buffers",
                                {"AREA": area_layer(), "DISTANCES": "10, 30, 150",
                                 "OUTPUT": "memory:"})
        layer = result["OUTPUT"]
        self.assertEqual(layer.featureCount(), 3)
        distances = sorted(feature["distance_m"] for feature in layer.getFeatures())
        self.assertEqual(distances, [10.0, 30.0, 150.0])
        areas = {feature["distance_m"]: feature["area_m2"] for feature in layer.getFeatures()}
        # rings grow with the distance
        self.assertLess(areas[10.0], areas[30.0])
        self.assertLess(areas[30.0], areas[150.0])

    def test_buffers_reject_invalid_distances(self):
        from qgis.core import QgsProcessingException

        with self.assertRaises(QgsProcessingException):
            processing.run("territorial_suite:generate_buffers",
                           {"AREA": area_layer(), "DISTANCES": "abc", "OUTPUT": "memory:"})

    def test_algorithm_requires_a_polygon_area(self):
        from qgis.core import QgsProcessingException

        line = QgsVectorLayer("LineString?crs=EPSG:32632", "line", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(0 0, 1 1)"))
        line.dataProvider().addFeature(feature)
        with self.assertRaises(QgsProcessingException):
            processing.run("territorial_suite:area_statistics",
                           {"AREA": line, "OUTPUT": "memory:"})


if __name__ == "__main__":
    unittest.main()
