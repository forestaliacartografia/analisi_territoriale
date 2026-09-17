"""Data source catalogue: parsing, validation, overrides and scope resolution."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from territorial_suite.core.errors import ConfigError
from territorial_suite.core.models import AdminUnit, AdminUnits, EvidenceLevel, SourceType
from territorial_suite.core.registry import DataSource, DataSourceRegistry, SourceScope, normalise
from territorial_suite.core.taxonomy import Taxonomy

VALID = {
    "id": "test.wfs.landscape",
    "name": "Vincoli paesaggistici",
    "authority": "Ente di prova",
    "type": "WFS",
    "url": "https://example.org/wfs",
    "layer": "vincoli",
    "category": "landscape_cultural",
    "scope": {"level": "regional", "codes": ["Toscana"]},
    "crs": ["EPSG:4326"],
    "query": {"bbox_crs": "EPSG:4326", "page_size": 500},
    "fields": {"label": "denominazione", "code": "cod"},
    "evidence_level": "declaratory",
    "legal_reference": "D.Lgs. 42/2004",
    "license": "CC-BY 4.0",
    "official": True,
    "priority": 10,
}


def tuscany() -> AdminUnits:
    """Administrative framing of an area in Tuscany."""
    return AdminUnits(
        municipalities=[AdminUnit(name="Firenze", istat_code="048017",
                                  cadastral_code="D612", province_code="FI",
                                  region_name="Toscana")],
        provinces=[AdminUnit(name="Firenze", level="province", province_code="FI")],
        regions=[AdminUnit(name="Toscana", level="region", istat_code="09")],
        resolved=True,
    )


class TestDataSourceParsing(unittest.TestCase):
    def test_parses_valid_descriptor(self):
        source = DataSource.from_dict(VALID)
        self.assertEqual(source.id, "test.wfs.landscape")
        self.assertEqual(source.type, SourceType.WFS)
        self.assertEqual(source.evidence_level, EvidenceLevel.DECLARATORY)
        self.assertEqual(source.page_size, 500)
        self.assertEqual(source.bbox_crs, "EPSG:4326")
        self.assertTrue(source.official)

    def test_rejects_missing_id(self):
        payload = dict(VALID)
        payload.pop("id")
        with self.assertRaises(ConfigError):
            DataSource.from_dict(payload)

    def test_rejects_unknown_type(self):
        with self.assertRaises(ConfigError):
            DataSource.from_dict({**VALID, "type": "SOAP"})

    def test_rejects_missing_url_for_service(self):
        payload = dict(VALID)
        payload.pop("url")
        with self.assertRaises(ConfigError):
            DataSource.from_dict(payload)

    def test_rejects_non_http_url(self):
        with self.assertRaises(ConfigError):
            DataSource.from_dict({**VALID, "url": "ftp://example.org/data"})

    def test_unknown_evidence_level_falls_back(self):
        source = DataSource.from_dict({**VALID, "evidence_level": "certain"})
        self.assertEqual(source.evidence_level, EvidenceLevel.CARTOGRAPHIC)

    def test_label_for_uses_field_mapping_then_fallbacks(self):
        source = DataSource.from_dict(VALID)
        self.assertEqual(source.label_for({"denominazione": "Bosco"}), "Bosco")
        self.assertEqual(source.label_for({"nome": "Fiume"}), "Fiume")
        self.assertEqual(source.label_for({"cod": "X1"}), "X1")
        self.assertEqual(source.label_for({}), "")

    def test_provenance_inherits_descriptor_metadata(self):
        source = DataSource.from_dict(VALID)
        provenance = source.provenance(operation="wfs getfeature", crs="EPSG:32632")
        self.assertEqual(provenance.source_id, source.id)
        self.assertEqual(provenance.authority, "Ente di prova")
        self.assertEqual(provenance.evidence_level, EvidenceLevel.DECLARATORY)
        self.assertEqual(provenance.crs, "EPSG:32632")
        self.assertIn("retrieved_at", provenance.as_dict())

    def test_merged_with_overlay(self):
        source = DataSource.from_dict(VALID)
        merged = source.merged_with({"enabled": False, "query": {"page_size": 50}},
                                    origin="user")
        self.assertFalse(merged.enabled)
        self.assertEqual(merged.page_size, 50)
        self.assertEqual(merged.bbox_crs, "EPSG:4326")   # untouched key survives
        self.assertEqual(merged.origin, "user")


class TestScope(unittest.TestCase):
    def test_national_scope_always_matches(self):
        scope = SourceScope(level="national")
        self.assertTrue(scope.matches(AdminUnits()))
        self.assertTrue(scope.matches(tuscany()))

    def test_regional_scope_matches_by_name(self):
        self.assertTrue(SourceScope(level="regional", codes=["toscana"]).matches(tuscany()))
        self.assertFalse(SourceScope(level="regional", codes=["Liguria"]).matches(tuscany()))

    def test_regional_scope_matches_by_istat_code(self):
        self.assertTrue(SourceScope(level="regional", codes=["09"]).matches(tuscany()))

    def test_municipal_scope_matches_cadastral_code(self):
        self.assertTrue(SourceScope(level="municipal", codes=["D612"]).matches(tuscany()))
        self.assertFalse(SourceScope(level="municipal", codes=["A001"]).matches(tuscany()))

    def test_unknown_admin_units_only_keep_universal_sources(self):
        empty = AdminUnits()
        self.assertTrue(SourceScope(level="european").matches(empty))
        self.assertFalse(SourceScope(level="regional", codes=["Toscana"]).matches(empty))

    def test_normalise_strips_accents_and_case(self):
        self.assertEqual(normalise("Emilia-Romagna"), "emiliaromagna")
        self.assertEqual(normalise("Forlì"), "forli")


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.registry = DataSourceRegistry()

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, payload) -> None:
        (self.folder / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_loads_builtin_catalogue_without_errors(self):
        registry = DataSourceRegistry().load()
        self.assertGreater(len(registry), 0, "the shipped catalogue must not be empty")
        self.assertEqual(registry.errors, [], f"invalid descriptors: {registry.errors}")

    def test_loads_extra_dir_and_reports_errors(self):
        self.write("ok.json", VALID)
        self.write("broken.json", {"name": "no id"})
        self.registry.load(extra_dirs=[self.folder])
        self.assertIn("test.wfs.landscape", self.registry)
        self.assertTrue(any("broken.json" in err for err in self.registry.errors))

    def test_list_descriptor_supported(self):
        self.write("many.json", [VALID, {**VALID, "id": "test.second"}])
        self.registry.load(extra_dirs=[self.folder])
        self.assertIsNotNone(self.registry.get("test.second"))

    def test_query_filters(self):
        self.write("ok.json", [VALID,
                               {**VALID, "id": "test.wms", "type": "WMS",
                                "category": "imagery", "scope": {"level": "national"}}])
        self.registry.load(extra_dirs=[self.folder])
        landscape = self.registry.query(categories=["landscape_cultural"])
        # The shipped catalogue is loaded too, and files its MiC sources under the
        # sub-categories of landscape_cultural: check the rolled-up macro-category.
        taxonomy_root = Taxonomy.instance().root_of
        self.assertTrue(all(taxonomy_root(s.category) == "landscape_cultural"
                            for s in landscape))
        self.assertIn("test.wfs.landscape", {s.id for s in landscape})
        wms = self.registry.query(types=[SourceType.WMS])
        self.assertTrue(all(s.type == SourceType.WMS for s in wms))

    def test_resolve_for_filters_by_scope(self):
        self.write("ok.json", [
            VALID,
            {**VALID, "id": "test.liguria", "scope": {"level": "regional", "codes": ["Liguria"]}},
        ])
        self.registry.load(extra_dirs=[self.folder])
        plan = self.registry.resolve_for(tuscany(), categories=["landscape_cultural"])
        ids = {source.id for source in plan}
        self.assertIn("test.wfs.landscape", ids)
        self.assertNotIn("test.liguria", ids)

    def test_disabled_sources_are_excluded_by_default(self):
        self.write("ok.json", {**VALID, "enabled": False})
        self.registry.load(extra_dirs=[self.folder])
        enabled = {s.id for s in self.registry.query(categories=["landscape_cultural"])}
        self.assertNotIn("test.wfs.landscape", enabled)
        everything = {s.id for s in self.registry.query(categories=["landscape_cultural"],
                                                        enabled_only=False)}
        self.assertIn("test.wfs.landscape", everything)

    def test_require_raises_for_unknown(self):
        with self.assertRaises(ConfigError):
            self.registry.require("does.not.exist")


if __name__ == "__main__":
    unittest.main()
