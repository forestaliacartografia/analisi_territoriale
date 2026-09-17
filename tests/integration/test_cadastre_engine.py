"""Cadastral engine driven by recorded service answers (no network)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from qgis.core import QgsRectangle

from territorial_suite.core.cache import CacheManager
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.core.registry import DataSourceRegistry
from territorial_suite.engines.admin import AdminResolver
from territorial_suite.engines.cadastre_engine import (
    CadastreEngine,
    parse_reference,
    sheet_label,
)
from tests.fakes import FakeHttpClient, fixture

EMPTY_PAGE = (b'<?xml version="1.0"?><wfs:FeatureCollection '
              b'xmlns:wfs="http://www.opengis.net/wfs/2.0" numberReturned="0"/>')

SOURCES = [
    {
        "id": "test.ade.parcel", "name": "Particelle (test)", "type": "WFS",
        "url": "https://example.org/wfs", "layer": "CP:CadastralParcel",
        "category": "cadastral", "subcategory": "parcel", "crs": ["EPSG:6706"],
        "authority": "Agenzia delle Entrate", "evidence_level": "declaratory",
        "query": {"version": "2.0.0", "bbox_crs": "EPSG:6706", "bbox_axis_order": "yx",
                  "crs_format": "urn", "page_size": 1000},
        "fields": {"label": "LABEL", "code": "NATIONALCADASTRALREFERENCE",
                   "municipality": "ADMINISTRATIVEUNIT"},
    },
    {
        "id": "test.ade.zoning", "name": "Fogli (test)", "type": "WFS",
        "url": "https://example.org/wfs", "layer": "CP:CadastralZoning",
        "category": "cadastral", "subcategory": "zoning", "crs": ["EPSG:6706"],
        "authority": "Agenzia delle Entrate", "evidence_level": "declaratory",
        "query": {"version": "2.0.0", "bbox_crs": "EPSG:6706", "bbox_axis_order": "yx",
                  "crs_format": "urn", "page_size": 500},
        "fields": {"label": "LABEL", "code": "NATIONALCADASTRALZONINGREFERENCE",
                   "municipality": "ADMINISTRATIVEUNIT"},
    },
]

AREA = QgsRectangle(11.2440, 43.7690, 11.2480, 43.7720)


class TestReferenceParsing(unittest.TestCase):
    def test_parses_the_national_reference(self):
        parts = parse_reference("D612_016900.10")
        self.assertEqual(parts["municipality_code"], "D612")
        self.assertEqual(parts["zoning"], "016900")
        self.assertEqual(parts["sheet"], "169")
        self.assertEqual(parts["parcel"], "10")
        self.assertEqual(sheet_label(parts), "169")

    def test_handles_allegato_and_sviluppo(self):
        parts = parse_reference("A001_0123A1.5")
        self.assertEqual(parts["sheet"], "123")
        self.assertEqual(parts["allegato"], "A")
        self.assertEqual(parts["sviluppo"], "1")
        self.assertEqual(sheet_label(parts), "123/A_1")

    def test_unknown_format_is_ignored(self):
        self.assertEqual(parse_reference("qualcosa"), {})
        self.assertEqual(sheet_label({}), "")


class TestCadastreEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        folder = Path(self._tmp.name)
        (folder / "sources").mkdir()
        (folder / "sources" / "cadastre.json").write_text(json.dumps(SOURCES),
                                                          encoding="utf-8")
        self._home = os.environ.get("TERRITORIAL_SUITE_HOME")
        os.environ["TERRITORIAL_SUITE_HOME"] = str(folder / "home")
        self.registry = DataSourceRegistry()
        self.registry._sources.clear()
        for payload in SOURCES:
            from territorial_suite.core.registry import DataSource

            source = DataSource.from_dict(payload)
            self.registry._sources[source.id] = source
        self.cache = CacheManager(folder / "cache")
        self.area = ProjectArea.from_rectangle(AREA, "EPSG:4326", name="Area di test")

    def tearDown(self):
        if self._home is not None:
            os.environ["TERRITORIAL_SUITE_HOME"] = self._home
        self._tmp.cleanup()

    def engine(self, responses) -> CadastreEngine:
        return CadastreEngine(registry=self.registry,
                              http=FakeHttpClient(responses), cache=self.cache)

    def test_builds_the_parcel_table(self):
        engine = self.engine([fixture("ade_zoning_gml32.xml"), EMPTY_PAGE,
                              fixture("ade_parcels_gml32.xml"), EMPTY_PAGE])
        result = engine.run(self.area)
        self.assertTrue(result.available)
        self.assertEqual(len(result.rows), 3)
        row = result.rows[0]
        self.assertEqual(row.municipality, "Firenze")
        self.assertEqual(row.cadastral_code, "D612")
        self.assertEqual(row.istat_code, "048017")
        self.assertTrue(row.sheet)
        self.assertGreater(row.area_cadastral_m2, 0.0)
        self.assertGreater(row.area_intersect_m2, 0.0)
        self.assertLessEqual(row.intersect_pct, 100.5)
        self.assertTrue(result.gpkg_path.endswith("cadastre.gpkg"))

    def test_totals_and_grouping(self):
        engine = self.engine([fixture("ade_zoning_gml32.xml"), EMPTY_PAGE,
                              fixture("ade_parcels_gml32.xml"), EMPTY_PAGE])
        result = engine.run(self.area)
        totals = result.totals()
        self.assertEqual(int(totals["municipalities"]), 1)
        self.assertIn("Firenze", result.by_municipality())
        self.assertEqual(len(result.municipalities), 1)
        self.assertEqual(result.municipalities[0].name, "Firenze")

    def test_service_failure_is_reported_without_raising(self):
        from territorial_suite.core.errors import SourceUnavailableError

        engine = self.engine([SourceUnavailableError("servizio non raggiungibile"),
                              SourceUnavailableError("servizio non raggiungibile")])
        result = engine.run(self.area)
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)
        self.assertTrue(any(not item.ok for item in result.source_results))

    def test_empty_answer_explains_trento_bolzano(self):
        engine = self.engine([EMPTY_PAGE, EMPTY_PAGE])
        result = engine.run(self.area)
        self.assertFalse(result.available)
        self.assertTrue(any("Trento" in warning for warning in result.warnings))

    def test_admin_resolver_uses_the_cadastral_zoning(self):
        resolver = AdminResolver(registry=self.registry,
                                 http=FakeHttpClient([fixture("ade_zoning_gml32.xml"),
                                                      EMPTY_PAGE]),
                                 cache=self.cache)
        admin = resolver.resolve(self.area)
        self.assertTrue(admin.resolved)
        self.assertEqual(admin.municipalities[0].name, "Firenze")
        self.assertEqual(admin.municipalities[0].region_name, "Toscana")
        self.assertEqual(admin.provinces[0].name, "Firenze")


if __name__ == "__main__":
    unittest.main()
