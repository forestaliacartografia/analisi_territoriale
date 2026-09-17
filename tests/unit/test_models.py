"""Domain model: serialisation round-trips and semantic guarantees."""

from __future__ import annotations

import json
import unittest

from territorial_suite.core.models import (
    AdminUnit,
    AdminUnits,
    Alert,
    AlertLevel,
    AnalysisReport,
    CadastralRow,
    EvidenceLevel,
    FeatureHit,
    LayerRef,
    Provenance,
    SlopeClass,
    SourceResult,
    SourceStatus,
    SourceType,
    TerrainStats,
)


def sample_result(**kwargs) -> SourceResult:
    """Build a representative source result."""
    payload = dict(
        source_id="it.test.source",
        source_name="Test source",
        category="landscape_cultural",
        status=SourceStatus.ONLINE,
        present=True,
        feature_count=2,
        intersect_area_m2=1234.5,
        intersect_pct=12.3,
        min_distance_m=0.0,
        hits=[FeatureHit(fid="1", label="Bosco", intersect_area_m2=1234.5, intersect_pct=12.3)],
        provenance=Provenance(source_id="it.test.source", authority="Ente",
                              evidence_level=EvidenceLevel.DECLARATORY),
    )
    payload.update(kwargs)
    return SourceResult(**payload)


class TestEnums(unittest.TestCase):
    def test_evidence_levels_have_italian_labels(self):
        for level in EvidenceLevel:
            self.assertTrue(level.label_it)
        self.assertIn("cartografico", EvidenceLevel.CARTOGRAPHIC.label_it)

    def test_alert_levels_are_semantic_not_legal(self):
        labels = {level.label_it for level in AlertLevel}
        self.assertEqual(labels, {"INFORMAZIONE", "ATTENZIONE", "VERIFICA", "CRITICITA CARTOGRAFICA"})

    def test_alert_rank_orders_severity(self):
        self.assertGreater(AlertLevel.CHECK_REQUIRED.rank, AlertLevel.ATTENTION.rank)
        self.assertGreater(AlertLevel.ATTENTION.rank, AlertLevel.INFO.rank)

    def test_source_type_capabilities(self):
        self.assertTrue(SourceType.WFS.is_vector_query)
        self.assertFalse(SourceType.WMS.is_vector_query)
        self.assertTrue(SourceType.XYZ.is_basemap)
        self.assertEqual(SourceType.parse("wfs"), SourceType.WFS)
        with self.assertRaises(ValueError):
            SourceType.parse("carrier-pigeon")


class TestSerialisation(unittest.TestCase):
    def test_provenance_round_trip(self):
        original = Provenance(source_id="a", authority="Ente", url="x",
                              evidence_level=EvidenceLevel.VERIFIED_ACT)
        restored = Provenance.from_dict(json.loads(original.to_json()))
        self.assertEqual(restored.evidence_level, EvidenceLevel.VERIFIED_ACT)
        self.assertEqual(restored.authority, "Ente")

    def test_source_result_round_trip(self):
        original = sample_result()
        restored = SourceResult.from_dict(json.loads(json.dumps(original.as_dict())))
        self.assertEqual(restored.status, SourceStatus.ONLINE)
        self.assertEqual(restored.hits[0].label, "Bosco")
        self.assertEqual(restored.evidence_level, EvidenceLevel.DECLARATORY)

    def test_infinite_distance_is_json_safe(self):
        result = sample_result(min_distance_m=float("inf"))
        payload = json.dumps(result.as_dict())
        self.assertNotIn("Infinity", payload)

    def test_admin_units_round_trip_and_summary(self):
        admin = AdminUnits(
            municipalities=[AdminUnit(name="Firenze", region_name="Toscana"),
                            AdminUnit(name="Fiesole", region_name="Toscana")],
            resolved=True,
        )
        self.assertTrue(admin.is_multi_municipality)
        self.assertIn("Firenze", admin.summary())
        self.assertIn("Toscana", admin.summary())
        restored = AdminUnits.from_dict(admin.as_dict())
        self.assertEqual(len(restored.municipalities), 2)

    def test_slope_class_labels(self):
        self.assertEqual(SlopeClass(lower=10, upper=20).label, "10-20 %")
        self.assertEqual(SlopeClass(lower=50, upper=None).label, ">50 %")

    def test_terrain_stats_round_trip(self):
        stats = TerrainStats(source_id="dem", elevation_min=24.0, elevation_max=187.0,
                             slope_classes=[SlopeClass(lower=0, upper=5, area_pct=40.0)])
        self.assertTrue(stats.available)
        restored = TerrainStats.from_dict(json.loads(json.dumps(stats.as_dict())))
        self.assertEqual(restored.elevation_max, 187.0)
        self.assertEqual(restored.slope_classes[0].area_pct, 40.0)

    def test_analysis_report_round_trip(self):
        report = AnalysisReport(
            area={"id": "abc", "name": "Test"},
            results=[sample_result(),
                     sample_result(source_id="it.down", status=SourceStatus.OFFLINE,
                                   present=False, error="timeout")],
            alerts=[Alert(level=AlertLevel.CHECK_REQUIRED, code="x", title="Verifica"),
                    Alert(level=AlertLevel.INFO, code="y", title="Info")],
            cadastre=[CadastralRow(municipality="Firenze", sheet="12", parcel="345",
                                   area_cadastral_m2=1000.0, area_intersect_m2=500.0,
                                   intersect_pct=50.0)],
            terrain=TerrainStats(elevation_min=10.0),
            layers=[LayerRef(name="Parcels", uri="path.gpkg|layername=parcels")],
        )
        restored = AnalysisReport.from_json(report.to_json())
        self.assertEqual(len(restored.results), 2)
        self.assertEqual(len(restored.sources_ok), 1)
        self.assertEqual(len(restored.sources_failed), 1)
        self.assertEqual(len(restored.present_results), 1)
        self.assertEqual(restored.sorted_alerts()[0].level, AlertLevel.CHECK_REQUIRED)
        self.assertIn("landscape_cultural", restored.by_category())
        self.assertEqual(restored.cadastre[0].parcel, "345")
        self.assertEqual(restored.layers[0].name, "Parcels")


if __name__ == "__main__":
    unittest.main()
