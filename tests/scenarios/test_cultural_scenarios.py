"""Live scenarios for the Cultural Heritage module and the orthophoto sheet.

These talk to the real services, so they run only with ``--network``. They assert
*contract invariants*, never service uptime: a source that is down is a legitimate outcome
and the test checks that the plugin says so instead of pretending the area is empty.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsProject, QgsRectangle

from territorial_suite.core.models import AnalysisReport
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.admin import AdminResolver
from territorial_suite.engines.cartography.orthophoto import OrthophotoEngine
from territorial_suite.engines.cultural_heritage import CulturalHeritageEngine
from territorial_suite.engines.cultural_heritage.engine import (
    ADMINISTRATION_CATEGORY,
)
from territorial_suite.engines.cultural_heritage.model import DataGap
from tests.support import requires_network

#: Real places with very different heritage density.
AREAS = {
    # Florence historic centre: dense, UNESCO, several art. 136 designations.
    "firenze": QgsRectangle(11.250, 43.766, 11.262, 43.774),
    # Palermo: Sicily runs its own landscape administration, so national sources are thin.
    "palermo": QgsRectangle(13.350, 38.110, 13.362, 38.118),
}


def area_for(key: str) -> ProjectArea:
    area = ProjectArea.from_rectangle(AREAS[key], "EPSG:4326", name=f"Scenario {key}")
    try:
        area.admin = AdminResolver().resolve(area)
    except Exception:            # pragma: no cover - admin service down
        pass
    return area


@requires_network
class TestCulturalHeritageScenarios(unittest.TestCase):
    def assert_outcome_is_coherent(self, outcome) -> None:
        """Invariants that must hold whatever the services answered."""
        for theme in outcome.themes:
            if theme.category == ADMINISTRATION_CATEGORY:
                # This theme never holds assets: the competent offices are reported on
                # their own, so "empty" is its normal state and not a gap.
                continue
            if theme.assets:
                self.assertEqual(theme.gaps, [],
                                 f"{theme.category}: un tema con record non ha gap")
            else:
                self.assertTrue(theme.gaps or not theme.sources_queried,
                                f"{theme.category}: tema vuoto senza spiegazione")
            for gap in theme.gaps:
                self.assertIn(gap, {item.value for item in DataGap})
            for asset in theme.assets:
                self.assertTrue(asset.source_id)
                self.assertTrue(asset.source_name)
                self.assertTrue(asset.global_id)
                self.assertIn("DATA_PRESENT", asset.findings)
                if asset.intersects:
                    self.assertIsNone(asset.distance_m)
                    self.assertIn("AREA_INTERSECTS_DATA", asset.findings)
                else:
                    self.assertIsNotNone(asset.distance_m)
                self.assertLessEqual(asset.intersect_pct, 100.5)
                if asset.has_act:
                    self.assertIn("LEGAL_REFERENCE_PRESENT", asset.findings)

    def assert_statements_are_not_verdicts(self, outcome) -> None:
        """No sentence may claim the area is subject to a constraint."""
        forbidden = ("e' vincolata", "e' sottoposta a vincolo", "risulta vincolat")
        for asset in outcome.assets:
            text = asset.statement_it().lower()
            for phrase in forbidden:
                self.assertNotIn(phrase, text, asset.global_id)

    def test_scenario_dense_historic_centre(self):
        outcome = CulturalHeritageEngine().run(area_for("firenze"), store_layers=False)
        self.assert_outcome_is_coherent(outcome)
        self.assert_statements_are_not_verdicts(outcome)
        if not outcome.assets:
            self.assertTrue(outcome.themes, "nessun tema valutato")
            self.skipTest(f"fonti MiC non disponibili: {outcome.gap_summary}")
        # The historic centre of a city like Florence must produce something.
        self.assertTrue(outcome.intersecting,
                        "nessun elemento interseca il centro storico di Firenze")
        with_act = outcome.with_act
        if with_act:
            for asset in with_act:
                self.assertTrue(asset.act.label_it())
                self.assertIn("da verificare presso l'ente competente",
                              asset.statement_it())

    def test_scenario_superintendency_comes_from_the_official_layer(self):
        outcome = CulturalHeritageEngine().run(area_for("firenze"), store_layers=False)
        if not outcome.superintendencies:
            self.skipTest("layer delle Soprintendenze non disponibile")
        for office in outcome.superintendencies:
            self.assertEqual(office.determined_by, "official_layer")
            self.assertTrue(office.known)
            self.assertTrue(office.name.strip())

    def test_scenario_region_with_its_own_administration(self):
        """In Sicily the national sources are thin: silence must be explained."""
        outcome = CulturalHeritageEngine().run(area_for("palermo"), store_layers=False)
        self.assert_outcome_is_coherent(outcome)
        empty = [theme for theme in outcome.themes
                 if not theme.assets and theme.category != ADMINISTRATION_CATEGORY]
        for theme in empty:
            if not theme.sources_queried:
                continue
            self.assertTrue(theme.gaps, f"{theme.category}: vuoto senza motivo")
            self.assertTrue(theme.coverage_notes or theme.gaps,
                            f"{theme.category}: nessuna nota di copertura")

    def test_scenario_dossier_section_is_produced(self):
        from territorial_suite.engines.cultural_heritage.engine import (
            CulturalHeritageEngine as Engine)
        from territorial_suite.engines.report import ReportEngine

        area = area_for("firenze")
        engine = Engine()
        outcome = engine.run(area, store_layers=False)
        report = AnalysisReport(area=area.as_dict())
        engine.attach(report, outcome)
        html = ReportEngine(report).to_html()
        self.assertIn("Patrimonio culturale e paesaggistico", html)
        self.assertIn("non equivale", html)
        self.assertIn("Qualita&#x27; del dato".replace("&#x27;", "'"), html)


@requires_network
class TestOrthophotoScenario(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def test_scenario_orthophoto_sheet_is_credited(self):
        sheet = OrthophotoEngine(self.project).run(area_for("firenze"),
                                                   add_to_project=False)
        if not sheet.ok:
            self.skipTest(f"nessuna ortofoto disponibile: {sheet.warnings}")
        self.assertTrue(sheet.credit_line.strip())
        from qgis.core import QgsLayoutItemLabel

        texts = "\n".join(item.text() for item in sheet.layout.items()
                          if isinstance(item, QgsLayoutItemLabel))
        self.assertIn(sheet.choice.attribution.split(",")[0], texts)

    def test_scenario_requesting_google_without_a_key_is_declared(self):
        sheet = OrthophotoEngine(self.project).run(area_for("firenze"),
                                                   preference="google",
                                                   add_to_project=False)
        if not sheet.ok:
            self.skipTest(f"nessuna ortofoto disponibile: {sheet.warnings}")
        if sheet.choice.provider == "google":
            self.assertTrue(sheet.choice.attribution,
                            "le tile Google non possono essere usate senza attribuzione")
            return
        self.assertTrue(sheet.choice.fallback_used)
        self.assertTrue(any("google" in warning.lower() for warning in sheet.warnings))


if __name__ == "__main__":
    unittest.main()
