"""Registry 2.0: data nature, verification status, quality, coverage and hierarchy.

The point of these tests is the guardrail of the National Data Fabric: a source the
plugin cannot really talk to must stay catalogued and must never be queried.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from territorial_suite.core.models import DataNature, Provenance, VerificationStatus
from territorial_suite.core.registry import DataSource, DataSourceRegistry
from territorial_suite.core.taxonomy import Taxonomy

BASE = {
    "id": "test.v2.base",
    "name": "Fonte di prova",
    "type": "WFS",
    "url": "https://example.org/wfs",
    "layer": "prova",
    "category": "landscape_assets_declared",
}


class TestDescriptorV2(unittest.TestCase):
    def test_defaults_are_backward_compatible(self):
        """A 0.1.0 descriptor keeps working and stays operational."""
        source = DataSource.from_dict(BASE)
        self.assertEqual(source.verification_status, VerificationStatus.DECLARED)
        self.assertTrue(source.is_operational)
        self.assertEqual(source.data_nature, DataNature.THEMATIC)
        self.assertEqual(source.quality.score, 50)
        self.assertEqual(source.coverage.completeness, "unknown")
        self.assertEqual(source.fallback_sources, [])

    def test_imagery_and_terrain_get_a_sensible_default_nature(self):
        imagery = DataSource.from_dict({**BASE, "type": "WMTS"})
        terrain = DataSource.from_dict({**BASE, "type": "TERRAIN_TILES"})
        self.assertEqual(imagery.data_nature, DataNature.IMAGERY)
        self.assertEqual(terrain.data_nature, DataNature.ELEVATION)

    def test_parses_the_new_block(self):
        source = DataSource.from_dict({
            **BASE,
            "data_nature": "perimeter",
            "verification_status": "verified",
            "verification_note": "16 feature in 1.1 s su Firenze, 2026-09-16",
            "quality": {"score": 80, "completeness": "nazionale",
                        "positional_accuracy_m": 25, "currency": "2023"},
            "coverage": {"completeness": "partial", "covered": ["Toscana"],
                         "missing": ["Sicilia"]},
            "fallback_sources": ["test.v2.other"],
            "healthcheck": {"op": "GetCapabilities"},
            "documentation_url": "https://example.org/docs",
        })
        self.assertEqual(source.data_nature, DataNature.PERIMETER)
        self.assertEqual(source.verification_status, VerificationStatus.VERIFIED)
        self.assertEqual(source.quality.positional_accuracy_m, 25.0)
        self.assertTrue(source.coverage.is_partial)
        self.assertIn("Toscana", source.coverage.covered)
        self.assertEqual(source.healthcheck["op"], "GetCapabilities")

    def test_unknown_values_fall_back_instead_of_raising(self):
        """A typo in a descriptor must not take the catalogue down."""
        source = DataSource.from_dict({**BASE, "data_nature": "banana",
                                       "verification_status": "maybe",
                                       "coverage": "sideways"})
        self.assertEqual(source.data_nature, DataNature.THEMATIC)
        self.assertEqual(source.verification_status, VerificationStatus.DECLARED)
        self.assertEqual(source.coverage.completeness, "unknown")

    def test_round_trip_through_as_dict(self):
        payload = {**BASE, "data_nature": "inventory", "verification_status": "planned",
                   "quality": {"score": 70}, "coverage": {"completeness": "complete"},
                   "fallback_sources": ["a", "b"]}
        source = DataSource.from_dict(payload)
        again = DataSource.from_dict(source.as_dict())
        self.assertEqual(again.data_nature, DataNature.INVENTORY)
        self.assertEqual(again.verification_status, VerificationStatus.PLANNED)
        self.assertEqual(again.quality.score, 70)
        self.assertEqual(again.coverage.completeness, "complete")
        self.assertEqual(again.fallback_sources, ["a", "b"])

    def test_non_operational_statuses(self):
        for status in ("planned", "unverified", "discovery_required", "unavailable"):
            source = DataSource.from_dict({**BASE, "verification_status": status})
            self.assertFalse(source.is_operational, status)

    def test_overlay_merges_the_nested_blocks(self):
        source = DataSource.from_dict({**BASE, "quality": {"score": 40, "currency": "2020"}})
        merged = source.merged_with({"quality": {"score": 90}}, origin="user")
        self.assertEqual(merged.quality.score, 90)
        self.assertEqual(merged.quality.currency, "2020")

    def test_provenance_carries_the_nature(self):
        source = DataSource.from_dict({**BASE, "data_nature": "act_index",
                                       "verification_status": "verified",
                                       "coverage": {"completeness": "partial",
                                                    "covered": ["Toscana"]}})
        provenance = source.provenance(operation="download")
        self.assertEqual(provenance.data_nature, DataNature.ACT_INDEX)
        self.assertEqual(provenance.verification_status, VerificationStatus.VERIFIED)
        self.assertIn("parziale", provenance.coverage_note)
        again = Provenance.from_dict(json.loads(provenance.to_json()))
        self.assertEqual(again.data_nature, DataNature.ACT_INDEX)


class TestRegistryV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.registry = DataSourceRegistry()
        self.addCleanup(self.tmp.cleanup)

    def load(self, payload):
        (self.folder / "sources.json").write_text(json.dumps(payload), encoding="utf-8")
        self.registry.load(extra_dirs=[self.folder])

    def test_catalogued_sources_are_never_queried(self):
        self.load([
            {**BASE, "id": "test.v2.live", "verification_status": "verified"},
            {**BASE, "id": "test.v2.paper", "verification_status": "planned"},
        ])
        ids = {s.id for s in self.registry.query(categories=["landscape_assets_declared"])}
        self.assertIn("test.v2.live", ids)
        self.assertNotIn("test.v2.paper", ids)
        self.assertIn("test.v2.paper", {s.id for s in self.registry.catalogued_only()})
        # ... but the Source Manager still lists it
        self.assertIn("test.v2.paper", {s.id for s in self.registry.all(enabled_only=False)})

    def test_query_expands_subcategories(self):
        """Asking for the dossier macro-category returns the sources filed underneath."""
        self.load([
            {**BASE, "id": "test.v2.art136", "category": "landscape_assets_declared"},
            {**BASE, "id": "test.v2.arch", "category": "archaeological_heritage"},
            {**BASE, "id": "test.v2.nature", "category": "protected_areas_national"},
        ])
        ids = {s.id for s in self.registry.query(categories=["landscape_cultural"])}
        self.assertIn("test.v2.art136", ids)
        self.assertIn("test.v2.arch", ids)
        self.assertNotIn("test.v2.nature", ids)
        # the shipped catalogue is loaded too: Natura 2000 rolls up the same way
        ids = {s.id for s in self.registry.query(categories=["nature_conservation"])}
        self.assertIn("test.v2.nature", ids)
        self.assertNotIn("test.v2.art136", ids)

    def test_roll_up_groups_subcategories_under_the_dossier_section(self):
        self.load([{**BASE, "id": "test.v2.arch", "category": "archaeological_heritage"}])
        grouped = self.registry.by_category(roll_up=True)
        self.assertIn("test.v2.arch", {s.id for s in grouped.get("landscape_cultural", [])})

    def test_fallbacks_are_ordered_by_quality_and_skip_dead_ones(self):
        self.load([
            {**BASE, "id": "test.v2.main",
             "fallback_sources": ["test.v2.good", "test.v2.better", "test.v2.dead"]},
            {**BASE, "id": "test.v2.good", "quality": {"score": 60}},
            {**BASE, "id": "test.v2.better", "quality": {"score": 90}},
            {**BASE, "id": "test.v2.dead", "verification_status": "unavailable"},
        ])
        order = [s.id for s in self.registry.fallbacks_for("test.v2.main")]
        self.assertEqual(order, ["test.v2.better", "test.v2.good"])


class TestTaxonomyHierarchy(unittest.TestCase):
    def setUp(self):
        self.taxonomy = Taxonomy.instance()

    def test_dossier_keeps_twelve_macro_sections(self):
        self.assertEqual(len(self.taxonomy.knowledge()), 12)

    def test_new_subcategories_are_filed_correctly(self):
        for child, parent in (("landscape_assets_declared", "landscape_cultural"),
                              ("landscape_areas_by_law", "landscape_cultural"),
                              ("cultural_heritage_assets", "landscape_cultural"),
                              ("archaeological_heritage", "landscape_cultural"),
                              ("unesco_heritage", "landscape_cultural"),
                              ("heritage_administration", "landscape_cultural"),
                              ("protected_areas_national", "nature_conservation"),
                              ("protected_areas_regional", "nature_conservation"),
                              ("protected_areas_marine", "nature_conservation"),
                              ("natura2000", "nature_conservation"),
                              ("wetlands_ramsar", "nature_conservation"),
                              ("protected_areas_other", "nature_conservation")):
            category = self.taxonomy.get(child)
            self.assertIsNotNone(category, child)
            self.assertEqual(category.parent, parent, child)
            self.assertEqual(self.taxonomy.root_of(child), parent, child)

    def test_expand_is_transitive_and_stable(self):
        expanded = self.taxonomy.expand(["landscape_cultural"])
        self.assertEqual(expanded[0], "landscape_cultural")
        self.assertIn("unesco_heritage", expanded)
        self.assertNotIn("natura2000", expanded)
        self.assertEqual(len(expanded), len(set(expanded)))

    def test_expand_of_a_leaf_returns_itself(self):
        self.assertEqual(self.taxonomy.expand(["natura2000"]), ["natura2000"])

    def test_root_of_unknown_category_is_the_category_itself(self):
        self.assertEqual(self.taxonomy.root_of("not_a_category"), "not_a_category")


class TestHierarchyReachesTheEngines(unittest.TestCase):
    """The hierarchy is useless if a rule written for a macro-category stops firing."""

    def test_rule_on_a_macro_category_fires_on_a_subcategory(self):
        from territorial_suite.core.models import (
            AnalysisReport, EvidenceLevel, Provenance, SourceResult, SourceStatus)
        from territorial_suite.engines.rules import Rule, RuleEngine

        rule = Rule.from_dict({
            "id": "test.nature", "title": "Area naturalistica",
            "level": "CHECK_REQUIRED",
            "when": {"category": "nature_conservation", "operation": "intersects"},
        })
        park = SourceResult(
            source_id="test.park", source_name="Parco", category="protected_areas_national",
            status=SourceStatus.ONLINE, present=True, feature_count=1,
            provenance=Provenance(source_id="test.park",
                                  evidence_level=EvidenceLevel.DECLARATORY))
        heritage = SourceResult(
            source_id="test.art136", source_name="Vincolo", category="landscape_assets_declared",
            status=SourceStatus.ONLINE, present=True, feature_count=1,
            provenance=Provenance(source_id="test.art136",
                                  evidence_level=EvidenceLevel.DECLARATORY))
        self.assertTrue(rule.matches_source(park))
        self.assertFalse(rule.matches_source(heritage))
        alerts = RuleEngine([rule]).evaluate(
            AnalysisReport(area={"area_m2": 1000.0, "name": "T"}, results=[park, heritage]))
        self.assertEqual([alert.title for alert in alerts], ["Area naturalistica"])

    def test_an_exact_rule_does_not_double_up_with_the_specific_ones(self):
        """A generic macro-category rule must not fire next to a sub-category rule."""
        from territorial_suite.core.models import (
            EvidenceLevel, Provenance, SourceResult, SourceStatus)
        from territorial_suite.engines.rules import Rule

        generic = Rule.from_dict({
            "id": "test.generic", "title": "Generica", "level": "INFO",
            "when": {"category": "landscape_cultural", "operation": "intersects",
                     "match": "exact"}})
        specific = Rule.from_dict({
            "id": "test.specific", "title": "Specifica", "level": "INFO",
            "when": {"category": "landscape_assets_declared", "operation": "intersects"}})
        result = SourceResult(
            source_id="s", category="landscape_assets_declared",
            status=SourceStatus.ONLINE, present=True,
            provenance=Provenance(source_id="s",
                                  evidence_level=EvidenceLevel.DECLARATORY))
        self.assertFalse(generic.matches_source(result))
        self.assertTrue(specific.matches_source(result))
        root = SourceResult(**{**result.__dict__, "category": "landscape_cultural"})
        self.assertTrue(generic.matches_source(root))

    def test_shipped_rules_describe_observations_not_verdicts(self):
        """No shipped rule may assert that an area *is* subject to a constraint."""
        from territorial_suite.engines.rules import RuleEngine

        forbidden = ("e' vincolata", "e' sottoposta a vincolo", "risulta vincolat",
                     "e' soggetta a vincolo")
        for rule in RuleEngine.load().rules:
            text = f"{rule.title} {rule.detail}".lower()
            for phrase in forbidden:
                self.assertNotIn(phrase, text, f"{rule.id}: {phrase}")

    def test_report_files_subcategories_under_their_dossier_section(self):
        from territorial_suite.core.models import (
            AnalysisReport, EvidenceLevel, Provenance, SourceResult, SourceStatus)
        from territorial_suite.engines.report import ReportEngine

        result = SourceResult(
            source_id="test.park", source_name="Parco nazionale di prova",
            category="protected_areas_national", status=SourceStatus.ONLINE,
            present=True, feature_count=1, intersect_pct=12.5,
            provenance=Provenance(source_id="test.park",
                                  evidence_level=EvidenceLevel.DECLARATORY))
        report = AnalysisReport(area={"area_m2": 1000.0, "name": "Area"}, results=[result])
        sections = ReportEngine(report)._knowledge_sections()
        nature = [block for block in sections
                  if "Aree naturalistiche e conservazione" in block]
        self.assertEqual(len(nature), 1)
        self.assertIn("Parco nazionale di prova", nature[0])
        # and it did not create a thirteenth section of its own
        self.assertEqual(len(sections), 11)


if __name__ == "__main__":
    unittest.main()
