"""WFS client against recorded answers of the Agenzia delle Entrate INSPIRE service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgis.core import QgsRectangle, QgsVectorLayer

from territorial_suite.core.cache import CacheManager
from territorial_suite.core.errors import SourceSchemaError
from territorial_suite.core.registry import DataSource
from territorial_suite.services import vector_io
from territorial_suite.services.ogc.wfs import WfsClient, parse_collection_info
from tests.fakes import FakeHttpClient, fixture

PARCEL_SOURCE = {
    "id": "test.cadastre.parcel",
    "name": "Particelle di prova",
    "type": "WFS",
    "url": "https://example.org/wfs",
    "layer": "CP:CadastralParcel",
    "category": "cadastral",
    "crs": ["EPSG:6706"],
    "query": {"version": "2.0.0", "bbox_crs": "EPSG:6706", "bbox_axis_order": "yx",
              "crs_format": "urn", "page_size": 1000},
    "fields": {"label": "LABEL", "code": "NATIONALCADASTRALREFERENCE"},
}

PROJECTED_SOURCE = {**PARCEL_SOURCE, "id": "test.projected",
                    "crs": ["EPSG:32632"],
                    "query": {"version": "2.0.0", "bbox_crs": "EPSG:32632",
                              "bbox_axis_order": "auto", "crs_format": "epsg",
                              "page_size": 100}}

BBOX = QgsRectangle(11.2455, 43.7695, 11.2470, 43.7710)

#: The real service always advertises a "next" page, so the client asks for it and stops
#: on the first empty answer: every fixture-driven fetch queues this terminator.
EMPTY_PAGE = (b'<?xml version="1.0"?><wfs:FeatureCollection '
              b'xmlns:wfs="http://www.opengis.net/wfs/2.0" numberReturned="0"/>')


class TestRequestBuilding(unittest.TestCase):
    def client(self, payload=None) -> WfsClient:
        source = DataSource.from_dict(payload or PARCEL_SOURCE)
        return WfsClient(source, http=FakeHttpClient())

    def test_bbox_is_latitude_first_for_geographic_urn(self):
        value = self.client().bbox_param(BBOX, "EPSG:6706")
        parts = value.split(",")
        self.assertAlmostEqual(float(parts[0]), 43.7695, places=4)
        self.assertAlmostEqual(float(parts[1]), 11.2455, places=4)
        self.assertEqual(parts[4], "urn:ogc:def:crs:EPSG::6706")

    def test_bbox_is_x_first_for_projected_crs(self):
        client = self.client(PROJECTED_SOURCE)
        rect = QgsRectangle(680000, 4848000, 681000, 4849000)
        parts = client.bbox_param(rect, "EPSG:32632").split(",")
        self.assertAlmostEqual(float(parts[0]), 680000.0, places=1)
        self.assertEqual(parts[4], "EPSG:32632")

    def test_bbox_precision_avoids_trailing_zeros(self):
        """The cadastral service rejects '43.76900000' and accepts '43.769000'."""
        rect = QgsRectangle(11.2440, 43.7690, 11.2480, 43.7720)
        value = self.client().bbox_param(rect, "EPSG:6706")
        for part in value.split(",")[:4]:
            self.assertLessEqual(len(part.split(".")[-1]), 6, part)
            self.assertFalse(part.endswith("0"), f"zeri finali in {part}")
        self.assertIn("43.769", value)

    def test_bbox_precision_is_coarser_for_metric_crs(self):
        client = self.client(PROJECTED_SOURCE)
        rect = QgsRectangle(680000.123456, 4848000.123456, 681000.5, 4849000.5)
        parts = client.bbox_param(rect, "EPSG:32632").split(",")[:4]
        for part in parts:
            decimals = part.split(".")[-1] if "." in part else ""
            self.assertLessEqual(len(decimals), 2, part)

    def test_bbox_precision_can_be_overridden(self):
        payload = {**PARCEL_SOURCE, "query": {**PARCEL_SOURCE["query"],
                                              "bbox_precision": 3}}
        parts = self.client(payload).bbox_param(
            QgsRectangle(11.244123456, 43.769123456, 11.248, 43.772), "EPSG:6706")
        self.assertIn("43.769", parts)
        self.assertNotIn("43.769123", parts)

    def test_parameter_names_follow_the_version(self):
        client = self.client()
        params = client.build_params(BBOX, "EPSG:6706", start=10, count=50)
        self.assertEqual(params["typenames"], "CP:CadastralParcel")
        self.assertEqual(params["count"], 50)
        self.assertEqual(params["startindex"], 10)

        legacy = dict(PARCEL_SOURCE)
        legacy["query"] = {**PARCEL_SOURCE["query"], "version": "1.1.0"}
        params = self.client(legacy).build_params(BBOX, "EPSG:6706", count=5)
        self.assertEqual(params["typename"], "CP:CadastralParcel")
        self.assertEqual(params["maxfeatures"], 5)


class TestCollectionInfo(unittest.TestCase):
    def test_reads_counts_and_next_link(self):
        info = parse_collection_info(fixture("ade_parcels_gml32.xml"))
        self.assertEqual(info["returned"], 3)
        self.assertIsNone(info["matched"])          # the service reports "unknown"
        self.assertTrue(info["next"].startswith("https://"))

    def test_handles_documents_without_paging(self):
        info = parse_collection_info(b"<wfs:FeatureCollection/>")
        self.assertIsNone(info["returned"])
        self.assertEqual(info["next"], "")


class TestFetch(unittest.TestCase):
    def setUp(self):
        # ignore_cleanup_errors: QGIS pools OGR connections, so a GeoPackage opened by a
        # test can still be locked when the temporary folder is removed on Windows.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cache = CacheManager(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, responses, payload=None) -> WfsClient:
        source = DataSource.from_dict(payload or PARCEL_SOURCE)
        return WfsClient(source, http=FakeHttpClient(responses), cache=self.cache)

    def test_gml_fields_are_aligned(self):
        """The gml:boundedBy strip must keep attributes aligned with their fields."""
        client = self.build([fixture("ade_parcels_gml32.xml"), EMPTY_PAGE])
        result = client.fetch(BBOX, "EPSG:4326", target_crs="EPSG:32632")
        self.assertTrue(result.has_data)
        self.assertEqual(result.feature_count, 3)
        layer = result.layer()
        self.assertIsNotNone(layer)
        self.assertEqual(layer.crs().authid(), "EPSG:32632")
        feature = next(layer.getFeatures())
        names = [field.name() for field in layer.fields()]
        self.assertIn("NATIONALCADASTRALREFERENCE", names)
        reference = feature["NATIONALCADASTRALREFERENCE"]
        self.assertTrue(str(reference).startswith("D612_"),
                        f"campo disallineato: {reference}")
        self.assertEqual(str(feature["ADMINISTRATIVEUNIT"]), "D612")

    def test_second_call_hits_the_cache(self):
        client = self.build([fixture("ade_parcels_gml32.xml"), EMPTY_PAGE])
        first = client.fetch(BBOX, "EPSG:4326", target_crs="EPSG:32632")
        self.assertFalse(first.from_cache)
        again = self.build([]).fetch(BBOX, "EPSG:4326", target_crs="EPSG:32632")
        self.assertTrue(again.from_cache)
        self.assertEqual(again.feature_count, first.feature_count)

    def test_service_exception_is_reported(self):
        # The client retries once with a simplified request before giving up, so the
        # service has to refuse twice.
        http = FakeHttpClient([fixture("ade_service_exception.xml"),
                               fixture("ade_service_exception.xml")])
        client = WfsClient(DataSource.from_dict(PARCEL_SOURCE), http=http, cache=self.cache)
        with self.assertRaises(SourceSchemaError):
            client.fetch(BBOX, "EPSG:4326")
        self.assertEqual(len(http.requests), 2)
        self.assertIn("srsname", http.requests[0]["params"])
        self.assertNotIn("srsname", http.requests[1]["params"])

    def test_simplified_retry_recovers_the_source(self):
        http = FakeHttpClient([fixture("ade_service_exception.xml"),
                               fixture("ade_parcels_gml32.xml"), EMPTY_PAGE])
        client = WfsClient(DataSource.from_dict(PARCEL_SOURCE), http=http, cache=self.cache)
        result = client.fetch(BBOX, "EPSG:4326", target_crs="EPSG:32632")
        self.assertTrue(result.has_data)
        self.assertEqual(result.feature_count, 3)

    def test_empty_answer_returns_no_data(self):
        empty = b'<?xml version="1.0"?><wfs:FeatureCollection ' \
                b'xmlns:wfs="http://www.opengis.net/wfs/2.0" numberReturned="0"/>'
        client = self.build([empty])
        result = client.fetch(BBOX, "EPSG:4326")
        self.assertFalse(result.has_data)
        self.assertEqual(result.feature_count, 0)

    def test_zoning_answer_is_parsed(self):
        payload = {**PARCEL_SOURCE, "id": "test.cadastre.zoning",
                   "layer": "CP:CadastralZoning"}
        client = self.build([fixture("ade_zoning_gml32.xml"), EMPTY_PAGE], payload)
        result = client.fetch(BBOX, "EPSG:4326", target_crs="EPSG:32632")
        self.assertTrue(result.has_data)
        layer = result.layer()
        names = [field.name() for field in layer.fields()]
        self.assertIn("NATIONALCADASTRALZONINGREFERENCE", names)
        feature = next(layer.getFeatures())
        self.assertTrue(str(feature["NATIONALCADASTRALZONINGREFERENCE"]).startswith("D612_"))

    def test_request_carries_the_expected_parameters(self):
        http = FakeHttpClient([fixture("ade_parcels_gml32.xml"), EMPTY_PAGE])
        source = DataSource.from_dict(PARCEL_SOURCE)
        WfsClient(source, http=http, cache=self.cache).fetch(BBOX, "EPSG:4326")
        # requests[0] is the GetFeature; requests[1] follows the "next" link, which the
        # server builds itself and therefore carries no parameters of ours.
        params = http.requests[0]["params"]
        self.assertEqual(params["service"], "WFS")
        self.assertEqual(params["request"], "GetFeature")
        self.assertEqual(params["version"], "2.0.0")
        self.assertIn("bbox", params)


class TestGmlSanitising(unittest.TestCase):
    def test_bounded_by_is_removed(self):
        payload = fixture("ade_parcels_gml32.xml")
        self.assertIn(b"boundedBy", payload)
        cleaned = vector_io.sanitise_gml(payload)
        self.assertNotIn(b"boundedBy", cleaned)
        self.assertIn(b"CadastralParcel", cleaned)

    def test_extension_detection(self):
        self.assertEqual(vector_io.guess_extension("application/gml+xml", b"<?xml"), ".gml")
        self.assertEqual(vector_io.guess_extension("application/json", b"{}"), ".geojson")
        self.assertEqual(vector_io.guess_extension("", b'{"type":"FeatureCollection"}'),
                         ".geojson")


if __name__ == "__main__":
    unittest.main()
