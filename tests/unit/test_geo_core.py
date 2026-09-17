"""CRS, measurement and geometry utilities."""

from __future__ import annotations

import math
import unittest

from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY, QgsRectangle

from territorial_suite.core import crs as crs_utils
from territorial_suite.core import geometry as geom_utils
from territorial_suite.core import measure
from territorial_suite.core.errors import CrsError, GeometryError
from tests.support import FIRENZE_BBOX, rect_geometry


class TestCrs(unittest.TestCase):
    def test_crs_from_accepts_string_and_object(self):
        self.assertEqual(crs_utils.crs_from("EPSG:4326").authid(), "EPSG:4326")
        crs = QgsCoordinateReferenceSystem("EPSG:32632")
        self.assertEqual(crs_utils.crs_from(crs).authid(), "EPSG:32632")

    def test_crs_from_rejects_garbage(self):
        with self.assertRaises(CrsError):
            crs_utils.crs_from("EPSG:not-a-crs")

    def test_utm_zone_detection(self):
        self.assertEqual(crs_utils.utm_epsg_for(11.25, 43.77), "EPSG:32632")   # Firenze
        self.assertEqual(crs_utils.utm_epsg_for(15.10, 37.50), "EPSG:32633")   # Sicilia
        self.assertEqual(crs_utils.utm_epsg_for(-58.4, -34.6), "EPSG:32721")   # southern hemisphere

    def test_auto_work_crs_is_metric(self):
        work = crs_utils.auto_work_crs(rect_geometry(), "EPSG:4326")
        self.assertEqual(work.authid(), "EPSG:32632")
        self.assertTrue(crs_utils.is_metric(work))

    def test_auto_work_crs_keeps_metric_source(self):
        geometry = QgsGeometry.fromRect(QgsRectangle(680000, 4848000, 680500, 4848500))
        work = crs_utils.auto_work_crs(geometry, "EPSG:32632")
        self.assertEqual(work.authid(), "EPSG:32632")

    def test_transform_geometry_round_trip(self):
        original = rect_geometry()
        projected = crs_utils.transform_geometry(original, "EPSG:4326", "EPSG:32632")
        back = crs_utils.transform_geometry(projected, "EPSG:32632", "EPSG:4326")
        self.assertAlmostEqual(original.boundingBox().xMinimum(),
                               back.boundingBox().xMinimum(), places=7)

    def test_urn_and_axis_order(self):
        self.assertEqual(crs_utils.urn("EPSG:4326"), "urn:ogc:def:crs:EPSG::4326")
        self.assertTrue(crs_utils.axis_inverted("EPSG:4326"))
        self.assertFalse(crs_utils.axis_inverted("EPSG:32632"))


class TestMeasure(unittest.TestCase):
    def test_ellipsoidal_area_matches_projected_area(self):
        geometry = rect_geometry()
        ellipsoidal = measure.area_m2(geometry, "EPSG:4326")
        projected = crs_utils.transform_geometry(geometry, "EPSG:4326", "EPSG:32632").area()
        self.assertGreater(ellipsoidal, 0.0)
        self.assertLess(abs(ellipsoidal - projected) / ellipsoidal, 0.01)

    def test_perimeter_of_known_square(self):
        # 1 km square in a metric CRS: perimeter must be 4 km.
        geometry = QgsGeometry.fromRect(QgsRectangle(680000, 4848000, 681000, 4849000))
        perimeter = measure.length_m(geometry, "EPSG:32632")
        self.assertAlmostEqual(perimeter, 4000.0, delta=5.0)

    def test_distance_between_disjoint_geometries(self):
        left = QgsGeometry.fromRect(QgsRectangle(680000, 4848000, 680100, 4848100))
        right = QgsGeometry.fromRect(QgsRectangle(680600, 4848000, 680700, 4848100))
        distance = measure.distance_m(left, right, "EPSG:32632")
        self.assertAlmostEqual(distance, 500.0, delta=1.0)

    def test_distance_is_zero_when_intersecting(self):
        left = QgsGeometry.fromRect(QgsRectangle(0, 0, 10, 10))
        right = QgsGeometry.fromRect(QgsRectangle(5, 5, 15, 15))
        self.assertEqual(measure.distance_m(left, right, "EPSG:32632"), 0.0)

    def test_formatting_switches_unit(self):
        self.assertIn("m²", measure.format_area(500))
        self.assertIn("ha", measure.format_area(50_000))
        self.assertIn("km²", measure.format_area(5_000_000))
        self.assertIn("km", measure.format_length(1500))
        self.assertIn("m", measure.format_length(150))
        self.assertEqual(measure.format_distance(float("inf")), "-")

    def test_percentage_guards_zero(self):
        self.assertEqual(measure.percentage(5, 0), 0.0)
        self.assertAlmostEqual(measure.percentage(25, 200), 12.5)


class TestGeometry(unittest.TestCase):
    def test_ensure_valid_repairs_bowtie(self):
        bowtie = QgsGeometry.fromWkt("POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))")
        self.assertFalse(bowtie.isGeosValid())
        repaired = geom_utils.ensure_valid(bowtie)
        self.assertTrue(repaired.isGeosValid())

    def test_ensure_valid_rejects_null(self):
        with self.assertRaises(GeometryError):
            geom_utils.ensure_valid(QgsGeometry())

    def test_keep_polygons_filters_collection(self):
        collection = QgsGeometry.fromWkt(
            "GEOMETRYCOLLECTION(POINT(0 0), LINESTRING(0 0, 1 1), "
            "POLYGON((0 0, 0 1, 1 1, 1 0, 0 0)))"
        )
        polygons = geom_utils.keep_polygons(collection)
        self.assertFalse(polygons.isEmpty())
        self.assertAlmostEqual(polygons.area(), 1.0, places=6)

    def test_buffer_metres_on_geographic_crs(self):
        point = QgsGeometry.fromPointXY(QgsPointXY(11.25, 43.77))
        buffered = geom_utils.buffer_metres(point, "EPSG:4326", 100.0)
        area = measure.area_m2(buffered, "EPSG:4326")
        self.assertAlmostEqual(area, math.pi * 100.0 ** 2, delta=math.pi * 100.0 ** 2 * 0.02)

    def test_union_skips_invalid(self):
        good = QgsGeometry.fromRect(QgsRectangle(0, 0, 1, 1))
        merged = geom_utils.union([good, QgsGeometry(), None])
        self.assertAlmostEqual(merged.area(), 1.0, places=6)

    def test_intersection_of_disjoint_is_empty(self):
        left = QgsGeometry.fromRect(QgsRectangle(0, 0, 1, 1))
        right = QgsGeometry.fromRect(QgsRectangle(5, 5, 6, 6))
        self.assertTrue(geom_utils.intersection(left, right).isEmpty())

    def test_shape_metrics_of_square(self):
        geometry = QgsGeometry.fromRect(QgsRectangle(680000, 4848000, 681000, 4849000))
        metrics = geom_utils.shape_metrics(geometry, "EPSG:32632")
        self.assertAlmostEqual(metrics.area_m2, 1_000_000.0, delta=2000.0)
        self.assertAlmostEqual(metrics.elongation, 1.0, delta=0.01)
        self.assertAlmostEqual(metrics.convexity, 1.0, delta=0.01)
        # Polsby-Popper compactness of a square is pi/4.
        self.assertAlmostEqual(metrics.compactness, math.pi / 4.0, delta=0.02)
        self.assertGreater(metrics.equivalent_circle_radius_m, 0.0)

    def test_centroid_is_inside_for_concave_shape(self):
        # A U-shaped polygon whose mathematical centroid falls outside the shape.
        u_shape = QgsGeometry.fromWkt(
            "POLYGON((0 0, 10 0, 10 10, 8 10, 8 2, 2 2, 2 10, 0 10, 0 0))"
        )
        centroid = geom_utils.centroid(u_shape)
        self.assertIsNotNone(centroid)
        self.assertTrue(u_shape.contains(QgsGeometry.fromPointXY(centroid)))

    def test_bbox_dict_keys(self):
        payload = geom_utils.bbox_dict(FIRENZE_BBOX)
        self.assertEqual(
            set(payload), {"min_x", "min_y", "max_x", "max_y", "width", "height"}
        )


if __name__ == "__main__":
    unittest.main()
