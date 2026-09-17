"""Cultural Heritage module: normalisation, classification, dedup and data quality.

The tests that matter most here are the ones about what the plugin is allowed to *say*:
an intersection with a dataset is not a constraint, an empty theme is not an empty
territory, and a record the source did not name must not be given an invented one.
"""

from __future__ import annotations

import unittest

from territorial_suite.core.models import (
    AdminUnit,
    AdminUnits,
    FeatureHit,
    Provenance,
    SourceResult,
    SourceStatus,
)
from territorial_suite.core.registry import DataSource
from territorial_suite.engines.cultural_heritage.classifier import (
    CulturalHeritageClassifier,
    classify_layer,
)
from territorial_suite.engines.cultural_heritage.deduplicator import (
    CulturalHeritageDeduplicator,
    normalise_name,
)
from territorial_suite.engines.cultural_heritage.model import (
    UNKNOWN,
    ActReference,
    CulturalAsset,
    CulturalHeritageOutcome,
    DataGap,
    Finding,
    Superintendency,
    ThemeOutcome,
)
from territorial_suite.engines.cultural_heritage.normalizer import (
    CulturalHeritageNormalizer,
    clean_text,
    looks_like_a_name,
    superintendency_fields,
)
from territorial_suite.engines.cultural_heritage.quality import (
    CulturalHeritageQuality,
    outside_coverage,
    summarise,
)

ART136 = DataSource.from_dict({
    "id": "test.mic.art136", "name": "Art. 136 di prova", "type": "WFS",
    "url": "https://example.org/wfs", "layer": "art136",
    "category": "landscape_assets_declared", "official": True,
    "evidence_level": "verified_act", "data_nature": "perimeter",
    "verification_status": "verified", "verification_note": "prova",
    "last_verified": "2026-09-17",
    "legal_reference": "D.Lgs. 42/2004 art. 136",
    "fields": {"label": "oggetto", "code": "codvin", "act_date": "data_decreto",
               "act_authority": "ente", "act_law": "legge"},
    "coverage": {"completeness": "partial", "missing": ["Sicilia"]},
})

BOSCHI = DataSource.from_dict({
    "id": "test.mic.boschi", "name": "Boschi di prova", "type": "WFS",
    "url": "https://example.org/wfs", "layer": "boschi",
    "category": "landscape_areas_by_law", "official": True,
    "evidence_level": "cartographic",
    "fields": {"label": "tipobos", "unnamed_label": "Superficie boscata"},
})

SOPRINTENDENZE = DataSource.from_dict({
    "id": "test.mic.soprintendenze", "name": "Soprintendenze di prova", "type": "WFS",
    "url": "https://example.org/wfs", "layer": "sopr",
    "category": "heritage_administration",
    "fields": {"label": "denominazione", "code": "codice", "website": "sito",
               "pec": "pec", "office_type": "tipo_ufficio"},
})


def hit(**kwargs) -> FeatureHit:
    payload = dict(fid="1", label="", attributes={}, intersect_area_m2=0.0,
                   intersect_pct=0.0, distance_m=0.0)
    payload.update(kwargs)
    return FeatureHit(**payload)


def result(source_id="test.mic.art136", *, hits=(), status=SourceStatus.ONLINE,
           error="") -> SourceResult:
    return SourceResult(source_id=source_id, source_name=source_id, status=status,
                        present=bool(hits), hits=list(hits), error=error,
                        provenance=Provenance(source_id=source_id))


def tuscany() -> AdminUnits:
    return AdminUnits(
        municipalities=[AdminUnit(name="Firenze", region_name="Toscana")],
        regions=[AdminUnit(name="Toscana", level="region")], resolved=True)


def sicily() -> AdminUnits:
    return AdminUnits(
        municipalities=[AdminUnit(name="Palermo", region_name="Sicilia")],
        regions=[AdminUnit(name="Sicilia", level="region")], resolved=True)


# --------------------------------------------------------------------------- normalizer


class TestNormalizer(unittest.TestCase):
    def test_html_entities_and_qt_dates_are_cleaned(self):
        """Real SITAP values: 'piazza de&#39; Mozzi' must not reach the dossier."""
        self.assertEqual(clean_text("piazza de&#39; Mozzi"), "piazza de' Mozzi")
        self.assertEqual(clean_text("  due   spazi "), "due spazi")
        self.assertEqual(clean_text(None), "")

        from qgis.PyQt.QtCore import QDate
        self.assertEqual(clean_text(QDate(1951, 10, 26)), "1951-10-26")

    def test_act_reference_is_transcribed_not_invented(self):
        normalizer = CulturalHeritageNormalizer(ART136)
        act = normalizer.act_of({"codvin": "90064", "legge": "L 1497/39",
                                 "ente": "MPI", "data_decreto": "1951-10-26"})
        self.assertTrue(act.present)
        self.assertIn("L 1497/39", act.label_it())
        self.assertIn("MPI", act.label_it())
        empty = normalizer.act_of({"altro": "x"})
        self.assertFalse(empty.present)
        self.assertEqual(empty.label_it(), "")

    def test_a_code_is_not_a_name(self):
        """``tipobos = 1`` is a classification code: the theme label is used instead."""
        self.assertFalse(looks_like_a_name("1"))
        self.assertFalse(looks_like_a_name("100.0"))
        self.assertFalse(looks_like_a_name(""))
        self.assertTrue(looks_like_a_name("Villa La Mattonaia"))
        normalizer = CulturalHeritageNormalizer(BOSCHI)
        asset = normalizer.asset_from_hit(hit(attributes={"tipobos": 1}),
                                          category="landscape_areas_by_law")
        self.assertEqual(asset.name, "Superficie boscata")
        self.assertEqual(asset.attributes["tipobos"], 1)

    def test_missing_values_stay_unknown(self):
        normalizer = CulturalHeritageNormalizer(ART136)
        asset = normalizer.asset_from_hit(hit(attributes={}),
                                          category="landscape_assets_declared")
        self.assertEqual(asset.municipality, UNKNOWN)
        self.assertEqual(asset.province, UNKNOWN)
        self.assertEqual(asset.asset_type, UNKNOWN)

    def test_findings_classify_what_may_be_said(self):
        normalizer = CulturalHeritageNormalizer(ART136)
        asset = normalizer.asset_from_hit(
            hit(attributes={"oggetto": "Zona panoramica", "codvin": "90064",
                            "legge": "L 1497/39"},
                intersect_area_m2=1000.0, intersect_pct=12.0, distance_m=0.0),
            category="landscape_assets_declared")
        self.assertIn(Finding.DATA_PRESENT.value, asset.findings)
        self.assertIn(Finding.AREA_INTERSECTS_DATA.value, asset.findings)
        self.assertIn(Finding.OFFICIAL_INFORMATION.value, asset.findings)
        self.assertIn(Finding.LEGAL_REFERENCE_PRESENT.value, asset.findings)

    def test_distance_only_for_non_intersecting_records(self):
        normalizer = CulturalHeritageNormalizer(ART136)
        near = normalizer.asset_from_hit(hit(distance_m=250.0),
                                         category="landscape_assets_declared")
        self.assertFalse(near.intersects)
        self.assertEqual(near.distance_m, 250.0)
        inside = normalizer.asset_from_hit(hit(distance_m=0.0),
                                           category="landscape_assets_declared")
        self.assertTrue(inside.intersects)
        self.assertIsNone(inside.distance_m)

    def test_superintendency_is_read_from_the_official_layer(self):
        fields = superintendency_fields(SOPRINTENDENZE, {
            "codice": "sabap-fi", "denominazione": "Soprintendenza di Firenze",
            "sito": "example.org", "pec": "x@pec.it", "tipo_ufficio": "sabap"})
        self.assertEqual(fields["code"], "sabap-fi")
        self.assertEqual(fields["office_type"], "sabap")
        self.assertIsNone(superintendency_fields(SOPRINTENDENZE, {"altro": "x"}))


class TestStatements(unittest.TestCase):
    """The sentences printed in the dossier must never become a legal conclusion."""

    def test_intersection_is_described_as_such(self):
        asset = CulturalAsset(name="Zona panoramica", source_name="SITAP",
                              intersects=True, intersect_pct=12.5)
        text = asset.statement_it()
        self.assertIn("interseca il dato cartografico", text)
        self.assertIn("SITAP", text)
        self.assertNotIn("e' vincolata", text)
        self.assertNotIn("e' sottoposta a vincolo", text)

    def test_an_act_is_quoted_as_declared_by_the_source(self):
        asset = CulturalAsset(name="Villa", source_name="SITAP", intersects=True,
                              act=ActReference(law="L 1497/39", date="1951-10-26"))
        text = asset.statement_it()
        self.assertIn("La fonte riporta il riferimento", text)
        self.assertIn("da verificare presso l'ente competente", text)

    def test_distance_is_reported_for_nearby_records(self):
        asset = CulturalAsset(name="Bosco", source_name="SITAP", distance_m=966.0)
        self.assertIn("966 m dall'area", asset.statement_it())


# --------------------------------------------------------------------------- classifier


class TestClassifier(unittest.TestCase):
    def test_reads_a_regional_layer_name(self):
        classification = classify_layer("sitap_ws_clone:CAMPANIA_art_142_g_boschi")
        self.assertEqual(classification.region, "Campania")
        self.assertEqual(classification.article, "142")
        self.assertEqual(classification.letter, "g")
        self.assertEqual(classification.category, "landscape_areas_by_law")
        self.assertIn("lett. g", classification.label)
        self.assertEqual(classification.legal_reference,
                         "D.Lgs. 42/2004 art. 142 c. 1 lett. g")

    def test_reads_article_136_and_10(self):
        self.assertEqual(classify_layer("sitap_ws_clone:LAZIO_art_136_c_d").category,
                         "landscape_assets_declared")
        self.assertEqual(
            classify_layer("sitap_ws_clone:BASILICATA_art_10_beni_monumentali").category,
            "cultural_heritage_assets")

    def test_an_unrecognised_name_is_not_filed_somewhere_plausible(self):
        classification = classify_layer("sitap_ws:vw_tab_feropc_shape_gc_point")
        self.assertFalse(classification.usable)
        self.assertEqual(classification.category, "")

    def test_letter_variants_of_the_real_catalogue(self):
        for layer, letter in (
                ("sitap_ws_clone:FVG_art_142_c_1_h_usi_civici", "h"),
                ("sitap_ws_clone:SARDEGNA_art_142_c_1_d_montagne", "d"),
                ("sitap_ws_clone:CAMPANIA_art_142_i_zone_umide_ramsar", "i")):
            self.assertEqual(classify_layer(layer).letter, letter, layer)

    def test_categories_are_real_taxonomy_entries(self):
        classifier = CulturalHeritageClassifier()
        for category in classifier.categories():
            self.assertIsNotNone(classifier.taxonomy.get(category), category)
            self.assertEqual(classifier.taxonomy.root_of(category), "landscape_cultural")


# --------------------------------------------------------------------------- dedup


class TestDeduplicator(unittest.TestCase):
    def test_normalise_name_drops_filler_words(self):
        self.assertEqual(normalise_name("IMMOBILE DENOMINATO Villa La Mattonaia"),
                         normalise_name("villa la mattonaia"))
        self.assertEqual(normalise_name(UNKNOWN), "")

    def test_exact_repeats_from_one_service_are_dropped(self):
        """SITAP publishes some rows twice, with different gml ids."""
        twin = dict(name="Area di rispetto", source_id="test.mic.boschi",
                    attributes={"inside": 100.0}, intersect_area_m2=500.0,
                    intersect_pct=7.1, intersects=True,
                    category="landscape_areas_by_law")
        assets = [CulturalAsset(global_id="a#1", **twin),
                  CulturalAsset(global_id="a#2", **twin)]
        dedup = CulturalHeritageDeduplicator()
        kept = dedup.run(assets)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dedup.repeated_count, 1)
        self.assertEqual(dedup.repeating_sources, ["test.mic.boschi"])

    def test_two_different_features_of_one_service_are_both_kept(self):
        assets = [
            CulturalAsset(global_id="a#1", name="Villa Rossa", source_id="s",
                          category="c", attributes={"id": 1}),
            CulturalAsset(global_id="a#2", name="Villa Rossa", source_id="s",
                          category="c", attributes={"id": 2}),
        ]
        dedup = CulturalHeritageDeduplicator()
        self.assertEqual(len(dedup.run(assets)), 2)
        self.assertEqual(dedup.repeated_count, 0)

    def test_same_act_code_from_two_services_is_merged_keeping_provenance(self):
        strong = CulturalAsset(global_id="strong#1", name="Zona panoramica",
                               source_id="mic.art136", category="landscape_assets_declared",
                               evidence_level="verified_act", intersects=True,
                               act=ActReference(code="90064", law="L 1497/39"))
        weak = CulturalAsset(global_id="weak#1", name="Altro nome",
                             source_id="mic.storico", category="landscape_assets_declared",
                             evidence_level="declaratory",
                             municipality="Firenze",
                             act=ActReference(code="90064"))
        dedup = CulturalHeritageDeduplicator()
        kept = dedup.run([weak, strong])
        self.assertEqual(len(kept), 1)
        merged = kept[0]
        self.assertEqual(merged.evidence_level, "verified_act")
        self.assertIn("weak#1", merged.merged_with)
        self.assertEqual(merged.source_count, 2)
        # the weaker record still contributed what the stronger one lacked
        self.assertEqual(merged.municipality, "Firenze")

    def test_records_without_a_usable_identity_are_never_merged(self):
        assets = [CulturalAsset(global_id=f"x#{i}", name=UNKNOWN, source_id=f"s{i}",
                                category="c") for i in range(3)]
        self.assertEqual(len(CulturalHeritageDeduplicator().run(assets)), 3)

    def test_dedup_can_be_switched_off(self):
        twin = dict(name="A", source_id="s", category="c", attributes={})
        assets = [CulturalAsset(global_id="a#1", **twin),
                  CulturalAsset(global_id="a#2", **twin)]
        self.assertEqual(len(CulturalHeritageDeduplicator(enabled=False).run(assets)), 2)


# --------------------------------------------------------------------------- quality


class TestDataQuality(unittest.TestCase):
    def test_the_six_outcomes_are_distinguished(self):
        quality = CulturalHeritageQuality(tuscany())
        self.assertEqual(quality.gap_for(ART136, result(hits=[])),
                         DataGap.NO_FEATURE_FOUND)
        self.assertEqual(quality.gap_for(ART136, None), DataGap.NO_DATA)
        self.assertEqual(
            quality.gap_for(ART136, result(status=SourceStatus.OFFLINE,
                                           error="connection timed out")),
            DataGap.SOURCE_UNAVAILABLE)
        self.assertEqual(
            quality.gap_for(ART136, result(status=SourceStatus.OFFLINE,
                                           error="geometry could not be read")),
            DataGap.QUERY_FAILED)
        catalogued = DataSource.from_dict({**ART136.as_dict(), "id": "x",
                                           "verification_status": "planned"})
        self.assertEqual(quality.gap_for(catalogued, result(hits=[])),
                         DataGap.SOURCE_NOT_VERIFIED)

    def test_an_area_in_a_declared_gap_is_not_reported_as_empty(self):
        self.assertTrue(outside_coverage(ART136, sicily()))
        self.assertFalse(outside_coverage(ART136, tuscany()))
        quality = CulturalHeritageQuality(sicily())
        self.assertEqual(quality.gap_for(ART136, result(hits=[])),
                         DataGap.SOURCE_OUTSIDE_COVERAGE)

    def test_only_no_feature_found_means_absence(self):
        self.assertTrue(DataGap.NO_FEATURE_FOUND.means_absence)
        for gap in (DataGap.NO_DATA, DataGap.SOURCE_UNAVAILABLE, DataGap.QUERY_FAILED,
                    DataGap.SOURCE_OUTSIDE_COVERAGE, DataGap.SOURCE_NOT_VERIFIED):
            self.assertFalse(gap.means_absence, gap)

    def test_summary_warns_when_absence_cannot_be_concluded(self):
        lines = summarise({DataGap.SOURCE_UNAVAILABLE.value: ["Beni culturali"]})
        self.assertEqual(len(lines), 1)
        self.assertIn("non puo' essere interpretata come assenza", lines[0])
        clean = summarise({DataGap.NO_FEATURE_FOUND.value: ["Archeologia"]})
        self.assertNotIn("non puo' essere interpretata", clean[0])

    def test_unresolved_admin_never_triggers_a_coverage_claim(self):
        self.assertFalse(outside_coverage(ART136, AdminUnits()))


# --------------------------------------------------------------------------- model


class TestOutcomeSerialisation(unittest.TestCase):
    def test_round_trip_through_json(self):
        outcome = CulturalHeritageOutcome(
            themes=[ThemeOutcome(category="unesco_heritage", label="Siti UNESCO",
                                 assets=[CulturalAsset(global_id="a#1", name="Sito",
                                                       intersects=True,
                                                       act=ActReference(law="L 77/2006"))],
                                 gaps=[DataGap.NO_FEATURE_FOUND.value])],
            superintendencies=[Superintendency(code="sabap-fi", name="SABAP Firenze",
                                               determined_by="official_layer")],
            warnings=["avviso"], gap_summary={"NO_FEATURE_FOUND": ["Siti UNESCO"]},
            elapsed_ms=1234)
        again = CulturalHeritageOutcome.from_dict(outcome.as_dict())
        self.assertEqual(len(again.themes), 1)
        self.assertEqual(again.themes[0].assets[0].act.law, "L 77/2006")
        self.assertTrue(again.superintendencies[0].known)
        self.assertEqual(again.elapsed_ms, 1234)
        self.assertEqual(again.theme("unesco_heritage").label, "Siti UNESCO")
        self.assertIsNone(again.theme("does_not_exist"))

    def test_an_undetermined_office_is_not_known(self):
        self.assertFalse(Superintendency().known)
        self.assertEqual(Superintendency().name, UNKNOWN)

    def test_module_payload_survives_the_report_round_trip(self):
        from territorial_suite.core.models import AnalysisReport
        report = AnalysisReport(area={"name": "T"})
        report.modules["cultural_heritage"] = CulturalHeritageOutcome(
            warnings=["x"]).as_dict()
        again = AnalysisReport.from_json(report.to_json())
        self.assertEqual(again.module("cultural_heritage")["warnings"], ["x"])
        self.assertEqual(again.module("missing"), {})


# --------------------------------------------------------------------------- report


class TestReportSection(unittest.TestCase):
    """The section must be honest even when it is empty, and never overstate."""

    def setUp(self):
        from territorial_suite.engines.report import ReportEngine
        self.table = ReportEngine._table

    def render(self, outcome):
        from territorial_suite.engines.cultural_heritage.report import (
            CulturalHeritageReport)
        return CulturalHeritageReport(outcome, number=14).to_html(self.table)

    def test_structure_and_disclaimer(self):
        outcome = CulturalHeritageOutcome(themes=[
            ThemeOutcome(category="cultural_heritage_assets", label="Beni culturali",
                         assets=[CulturalAsset(global_id="a#1", name="Palazzo X",
                                               source_name="SITAP", intersects=True,
                                               intersect_pct=4.0)],
                         sources_queried=["test.mic.art136"])])
        page = self.render(outcome)
        self.assertIn("14. Patrimonio culturale e paesaggistico", page)
        self.assertIn("14.1 Sintesi", page)
        self.assertIn("non equivale", page)
        self.assertIn("Palazzo X", page)

    def test_an_empty_section_says_why_not_just_that_it_is_empty(self):
        outcome = CulturalHeritageOutcome(
            themes=[ThemeOutcome(category="archaeological_heritage",
                                 label="Aree e beni archeologici",
                                 gaps=[DataGap.SOURCE_UNAVAILABLE.value])],
            gap_summary={DataGap.SOURCE_UNAVAILABLE.value: ["Aree e beni archeologici"]})
        page = self.render(outcome)
        self.assertIn("fonte non disponibile", page)
        self.assertIn("non puo&#x27; essere interpretata come assenza", page.replace("'", "&#x27;"))

    def test_an_undetermined_office_is_explained_not_guessed(self):
        page = self.render(CulturalHeritageOutcome(
            themes=[ThemeOutcome(category="cultural_heritage_assets", label="Beni")]))
        self.assertIn("non e' stata determinata", page)
        self.assertIn("non viene dedotto dalla posizione geografica", page)

    def test_export_rows_carry_provenance(self):
        from territorial_suite.engines.cultural_heritage.report import (
            EXPORT_HEADER, rows_for_export)
        outcome = CulturalHeritageOutcome(themes=[
            ThemeOutcome(category="unesco_heritage", label="Siti UNESCO",
                         assets=[CulturalAsset(global_id="a#1", name="Centro storico",
                                               source_id="mic.unesco",
                                               source_name="SITAP", intersects=True,
                                               act=ActReference(code="IT_174"))])])
        rows = rows_for_export(outcome)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), len(EXPORT_HEADER))
        self.assertIn("mic.unesco", rows[0])

    def test_truncation_is_declared(self):
        assets = [CulturalAsset(global_id=f"a#{i}", name=f"Bene {i}", intersects=True)
                  for i in range(60)]
        outcome = CulturalHeritageOutcome(themes=[
            ThemeOutcome(category="cultural_heritage_assets", label="Beni",
                         assets=assets)])
        page = self.render(outcome)
        self.assertIn("Elenco troncato", page)
        self.assertIn("su 60", page)


if __name__ == "__main__":
    unittest.main()
