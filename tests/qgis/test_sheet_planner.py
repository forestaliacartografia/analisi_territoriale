"""No data, no map - and three different reasons for saying no.

A sheet titled "Rischio da frana" with nothing drawn on it is not blank: it is a claim
that somebody checked and the area is clear, printed with the authority of a map. When
the truth is that the service was down, that sheet is a lie in cartographic form.

So the planner keeps apart what the catalogue cannot:

* the source answered and the area really is clear;
* the source could not be reached;
* the theme was never asked about.

All three produce no sheet. Only the first one is news about the territory.
"""

from __future__ import annotations

import unittest

from territorial_suite.core.models import (
    AnalysisReport,
    SourceResult,
    SourceStatus,
    TerrainStats,
)
from territorial_suite.engines.cartography import sheet_planner
from territorial_suite.engines.cartography.sheet_planner import (
    AVAILABLE_BUT_NO_DATA,
    DATA_PRESENT,
    NOT_DETERMINABLE,
    NOT_REQUESTED,
    PARTIAL_COVERAGE,
    SOURCE_UNAVAILABLE,
    SheetPlanner,
    plan_for,
)


def result(category, *, present=True, count=5, status=SourceStatus.ONLINE,
           source_id="fonte.x"):
    return SourceResult(source_id=source_id, source_name=source_id, category=category,
                        status=status, present=present, feature_count=count)


def hazard_module(theme, *, determinable=True, classes=None, gaps=None,
                  source_id="pcn.pai.x"):
    return {"themes": [{
        "theme": theme, "kind": "hazard", "determinable": determinable,
        "source_id": source_id, "classes": classes or [], "gaps": gaps or [],
        "statement": f"stato di {theme}",
    }]}


class TestDataPresenceDecidesTheSheet(unittest.TestCase):
    def test_a_theme_with_features_earns_its_sheet(self):
        report = AnalysisReport(results=[result("natura2000", count=3)])
        decision = next(d for d in plan_for(report) if d.template == "natura2000_map")
        self.assertTrue(decision.generate)
        self.assertEqual(decision.status, DATA_PRESENT)
        self.assertEqual(decision.feature_count, 3)

    def test_a_theme_the_source_answered_about_but_found_nothing_gets_no_sheet(self):
        report = AnalysisReport(results=[result("natura2000", present=False, count=0)])
        decision = next(d for d in plan_for(report) if d.template == "natura2000_map")
        self.assertFalse(decision.generate)
        self.assertEqual(decision.status, AVAILABLE_BUT_NO_DATA)

    def test_an_unreachable_source_is_not_an_empty_area(self):
        """The distinction the whole module exists for."""
        report = AnalysisReport(results=[result("natura2000", present=False, count=0,
                                                status=SourceStatus.OFFLINE)])
        decision = next(d for d in plan_for(report) if d.template == "natura2000_map")
        self.assertFalse(decision.generate)
        self.assertEqual(decision.status, SOURCE_UNAVAILABLE)
        self.assertNotEqual(decision.status, AVAILABLE_BUT_NO_DATA)

    def test_a_theme_nobody_asked_about_makes_no_claim(self):
        decision = next(d for d in plan_for(AnalysisReport())
                        if d.template == "natura2000_map")
        self.assertFalse(decision.generate)
        self.assertEqual(decision.status, NOT_REQUESTED)

    def test_the_three_refusals_read_differently(self):
        wordings = {sheet_planner.label_for(s) for s in
                    (AVAILABLE_BUT_NO_DATA, SOURCE_UNAVAILABLE, NOT_REQUESTED)}
        self.assertEqual(len(wordings), 3)


class TestHazardThemesUseTheirOwnEvidence(unittest.TestCase):
    """The hazard module knows "measured zero" from "could not measure"."""

    def test_a_measured_class_earns_the_sheet(self):
        report = AnalysisReport()
        report.modules["hazard_risk"] = hazard_module(
            "flood_hazard", classes=[{"area_m2": 1200.0, "feature_count": 4}])
        decision = next(d for d in plan_for(report) if d.template == "flood_hazard_map")
        self.assertTrue(decision.generate)
        self.assertEqual(decision.status, DATA_PRESENT)
        self.assertEqual(decision.feature_count, 4)

    def test_a_theme_measured_as_empty_gets_no_sheet(self):
        report = AnalysisReport()
        report.modules["hazard_risk"] = hazard_module("flood_hazard", classes=[])
        decision = next(d for d in plan_for(report) if d.template == "flood_hazard_map")
        self.assertFalse(decision.generate)
        self.assertEqual(decision.status, AVAILABLE_BUT_NO_DATA)

    def test_an_undeterminable_theme_is_not_an_empty_one(self):
        report = AnalysisReport()
        report.modules["hazard_risk"] = hazard_module(
            "flood_risk", determinable=False, gaps=["SOURCE_UNAVAILABLE"])
        decision = next(d for d in plan_for(report) if d.template == "flood_risk_map")
        self.assertFalse(decision.generate)
        self.assertEqual(decision.status, SOURCE_UNAVAILABLE)

    def test_a_theme_with_no_source_at_all_says_not_determinable(self):
        report = AnalysisReport()
        report.modules["hazard_risk"] = hazard_module(
            "flood_risk", determinable=False, gaps=["NO_DATA"])
        decision = next(d for d in plan_for(report) if d.template == "flood_risk_map")
        self.assertEqual(decision.status, NOT_DETERMINABLE)

    def test_truncated_data_still_earns_a_sheet_but_is_flagged(self):
        report = AnalysisReport()
        report.modules["hazard_risk"] = hazard_module(
            "flood_hazard", classes=[{"area_m2": 900.0, "feature_count": 2}],
            gaps=["PARTIAL_COVERAGE"])
        decision = next(d for d in plan_for(report) if d.template == "flood_hazard_map")
        self.assertTrue(decision.generate)
        self.assertEqual(decision.status, PARTIAL_COVERAGE)


class TestTerrainAndOverviews(unittest.TestCase):
    def test_the_elevation_sheet_follows_the_dem(self):
        report = AnalysisReport(terrain=TerrainStats(elevation_min=10.0))
        decision = next(d for d in plan_for(report) if d.template == "elevation_map")
        self.assertTrue(decision.generate)

    def test_without_a_dem_there_is_no_elevation_sheet(self):
        decision = next(d for d in plan_for(AnalysisReport())
                        if d.template == "elevation_map")
        self.assertFalse(decision.generate)
        self.assertEqual(decision.status, NOT_DETERMINABLE)

    def test_an_overview_is_produced_even_when_a_theme_is_missing(self):
        """The exception in the rule: a framing sheet stands on the area itself."""
        decision = next(d for d in plan_for(AnalysisReport())
                        if d.template == "territorial_overview")
        self.assertTrue(decision.generate)


class TestThePlanAsAWhole(unittest.TestCase):
    def test_a_poor_area_does_not_produce_a_catalogue_of_empty_sheets(self):
        """Scenario C of the brief: few data, few sheets."""
        decisions = plan_for(AnalysisReport())
        generated = [d for d in decisions if d.generate]
        self.assertTrue(generated, "l'inquadramento deve restare")
        self.assertLessEqual(len(generated), 3,
                             f"troppe tavole per un'area senza dati: "
                             f"{[d.template for d in generated]}")

    def test_a_rich_area_produces_the_sheets_it_supports(self):
        """Scenario A: several themes with data, several sheets."""
        report = AnalysisReport(results=[
            result("natura2000", count=2, source_id="a"),
            result("protected_areas_national", count=1, source_id="b"),
            result("hydrography", count=9, source_id="c"),
            result("nitrate_vulnerable_zones", count=3, source_id="d"),
        ], terrain=TerrainStats(elevation_min=5.0))
        templates = {d.template for d in plan_for(report) if d.generate}
        for wanted in ("natura2000_map", "protected_areas_map", "hydrographic_map",
                       "nitrate_zones_map", "elevation_map", "territorial_overview"):
            self.assertIn(wanted, templates)

    def test_the_summary_counts_each_reason_separately(self):
        # Each category here owns a sheet of its own, so the three outcomes stay
        # separable: Ramsar would be folded into the protected-areas sheet and its
        # emptiness hidden by a neighbour that does have data.
        report = AnalysisReport(results=[
            result("natura2000", count=2),
            result("nitrate_vulnerable_zones", present=False, count=0),
            result("fire", present=False, count=0, status=SourceStatus.OFFLINE),
        ])
        summary = SheetPlanner.summary(plan_for(report))
        self.assertGreaterEqual(summary["counts"]["generated"], 1)
        self.assertGreaterEqual(summary["counts"][AVAILABLE_BUT_NO_DATA], 1)
        self.assertGreaterEqual(summary["counts"][SOURCE_UNAVAILABLE], 1)

    def test_every_decision_carries_a_reason(self):
        for decision in plan_for(AnalysisReport(results=[result("natura2000")])):
            self.assertTrue(decision.status)
            self.assertTrue(decision.label)

    def test_the_plan_serialises_for_the_dossier(self):
        import json

        json.dumps(SheetPlanner.summary(plan_for(AnalysisReport())))

    def test_a_broken_report_yields_no_plan_instead_of_losing_the_run(self):
        self.assertEqual(plan_for(None), [])


if __name__ == "__main__":
    unittest.main()
