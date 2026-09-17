"""Project area creation, metrics, serialisation and storage."""

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

from territorial_suite.core.errors import GeometryError
from territorial_suite.core.project_area import (
    SOURCE_BBOX,
    SOURCE_CIRCLE,
    ProjectArea,
    ProjectAreaStore,
)
from tests.support import FIRENZE_BBOX, rect_geometry


def polygon_layer(name: str = "areas", crs: str = "EPSG:4326") -> QgsVectorLayer:
    """Build an in-memory polygon layer with two adjacent squares."""
    layer = QgsVectorLayer(f"Polygon?crs={crs}", name, "memory")
    provider = layer.dataProvider()
    features = []
    for rect in (QgsRectangle(11.24, 43.77, 11.245, 43.775),
                 QgsRectangle(11.245, 43.77, 11.25, 43.775)):
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(rect))
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


class TestProjectAreaFactories(unittest.TestCase):
    def test_from_geometry_computes_metrics(self):
        area = ProjectArea.from_geometry(rect_geometry(), "EPSG:4326", name="Test")
        self.assertGreater(area.area_m2, 0.0)
        self.assertGreater(area.perimeter_m, 0.0)
        self.assertEqual(area.crs.authid(), "EPSG:4326")
        self.assertEqual(area.work_crs.authid(), "EPSG:32632")
        self.assertEqual(len(area.id), 12)

    def test_from_geometry_rejects_non_polygon(self):
        with self.assertRaises(GeometryError):
            ProjectArea.from_geometry(QgsGeometry.fromPointXY(QgsPointXY(11.0, 43.0)),
                                      "EPSG:4326")

    def test_from_rectangle_sets_source_kind(self):
        area = ProjectArea.from_rectangle(FIRENZE_BBOX, "EPSG:4326")
        self.assertEqual(area.source_kind, SOURCE_BBOX)

    def test_from_circle_has_expected_area(self):
        area = ProjectArea.from_circle(QgsPointXY(11.25, 43.77), 200.0, "EPSG:4326")
        self.assertEqual(area.source_kind, SOURCE_CIRCLE)
        expected = 3.14159265 * 200.0 ** 2
        self.assertAlmostEqual(area.area_m2, expected, delta=expected * 0.02)

    def test_from_layer_dissolves_features(self):
        layer = polygon_layer()
        area = ProjectArea.from_layer(layer)
        # Two adjacent squares dissolve into a single part.
        self.assertEqual(area.metrics().part_count, 1)
        self.assertEqual(area.name, "areas")

    def test_from_layer_requires_selection_when_asked(self):
        layer = polygon_layer()
        with self.assertRaises(GeometryError):
            ProjectArea.from_layer(layer, selected_only=True)

    def test_from_file_round_trip(self):
        area = ProjectArea.from_geometry(rect_geometry(), "EPSG:4326", name="Saved")
        with tempfile.TemporaryDirectory() as folder:
            path = area.save_to_file(str(Path(folder) / "area"))
            self.assertTrue(path.exists())
            restored = ProjectArea.load_from_file(str(path))
        self.assertEqual(restored.name, "Saved")
        self.assertEqual(restored.id, area.id)
        self.assertAlmostEqual(restored.area_m2, area.area_m2, places=3)


class TestProjectAreaGeometry(unittest.TestCase):
    def setUp(self):
        self.area = ProjectArea.from_geometry(rect_geometry(), "EPSG:4326", name="Test")

    def test_bbox_in_other_crs(self):
        bbox = self.area.bbox("EPSG:32632")
        self.assertGreater(bbox.width(), 500.0)
        self.assertLess(bbox.width(), 2000.0)

    def test_context_bbox_is_larger_than_bbox(self):
        plain = self.area.bbox("EPSG:32632")
        context = self.area.context_bbox("EPSG:32632", buffer_m=500.0)
        self.assertGreater(context.width(), plain.width() + 900.0)

    def test_centroid_wgs84(self):
        point = self.area.centroid_wgs84()
        self.assertAlmostEqual(point.x(), 11.245, places=2)
        self.assertAlmostEqual(point.y(), 43.775, places=2)

    def test_memory_layer_carries_attributes(self):
        layer = self.area.to_memory_layer()
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.featureCount(), 1)
        feature = next(layer.getFeatures())
        self.assertEqual(feature["area_id"], self.area.id)
        self.assertAlmostEqual(feature["area_ha"], self.area.area_m2 / 10_000.0, places=3)

    def test_as_dict_is_json_serialisable(self):
        payload = self.area.as_dict()
        self.assertIn("geometry_wkt", payload)
        self.assertIn("bbox", payload)
        self.assertEqual(payload["format"], "territorial_suite.project_area/1")
        restored = ProjectArea.from_dict(payload)
        self.assertAlmostEqual(restored.area_m2, self.area.area_m2, places=3)


class TestProjectAreaStore(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.store = ProjectAreaStore(self.project)

    def test_save_and_restore_current(self):
        area = ProjectArea.from_geometry(rect_geometry(), "EPSG:4326", name="Stored")
        self.store.save(area)
        restored = self.store.current()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.id, area.id)
        self.assertEqual(restored.name, "Stored")

    def test_multiple_areas_and_switch(self):
        first = ProjectArea.from_geometry(rect_geometry(), "EPSG:4326", name="First")
        second = ProjectArea.from_rectangle(QgsRectangle(11.0, 43.0, 11.01, 43.01), "EPSG:4326",
                                            name="Second")
        self.store.save(first)
        self.store.save(second)
        self.assertEqual(len(self.store.list_areas()), 2)
        self.assertEqual(self.store.current().name, "Second")
        self.store.set_current(first.id)
        self.assertEqual(self.store.current().name, "First")

    def test_remove_and_clear(self):
        area = ProjectArea.from_geometry(rect_geometry(), "EPSG:4326")
        self.store.save(area)
        self.store.remove(area.id)
        self.assertEqual(self.store.list_areas(), [])
        self.store.clear()
        self.assertIsNone(self.store.current())

    def test_current_is_none_on_empty_project(self):
        self.assertIsNone(self.store.current())


if __name__ == "__main__":
    unittest.main()
