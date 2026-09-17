"""The vincolo idrogeologico, and the sentences the plugin is allowed to write.

The engine exists because the constraint of R.D.L. 3267/1923 is a legal regime, not a
hazard class, and folding it into the risk engine is how a sheet titled "rischio" ends up
printing a constraint. That mistake has already been made once in this codebase.

The harder requirement is semantic. Tuscany publishes the perimeter as an image that can
be asked about point by point but not downloaded, so no surface exists to report. The
tests below hold three lines that are easy to cross:

* a view-only source yields a count of points, never a surface or a percentage;
* "no source covers this area" is never written as "the area is not subject to the
  constraint";
* nothing the engine writes asserts that the constraint applies - that is the competent
  authority's assessment, not a plugin's.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsRectangle

from territorial_suite.core.gaps import DataGap
from territorial_suite.core.paths import plugin_dir
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.core.registry import DataSourceRegistry
from territorial_suite.engines.hydrogeological_constraint import (
    TAG,
    HydrogeologicalConstraintEngine,
    HydrogeologicalOutcome,
    Presence,
    SamplePoint,
)

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="Firenze centro")


class TestVocabulary(unittest.TestCase):
    """The words matter as much as the numbers here."""

    def test_the_four_outcomes_exist_and_read_as_observations(self):
        for value in ("PRESENTE", "ASSENTE", "PARZIALE", "NON_VERIFICABILE"):
            self.assertIn(value, Presence.__members__)
        for presence in Presence:
            self.assertTrue(presence.label_it.strip())
            # Nessuna etichetta deve affermare che il vincolo si applica.
            self.assertNotIn("vincolat", presence.label_it.lower())

    def test_absence_of_data_is_not_absence_of_the_constraint(self):
        self.assertFalse(Presence.NON_VERIFICABILE.is_conclusive)
        self.assertTrue(Presence.ASSENTE.is_conclusive)

    def test_the_statement_never_pronounces_on_the_law(self):
        for presence in Presence:
            outcome = HydrogeologicalOutcome(presence=presence)
            text = outcome.statement().lower()
            for forbidden in ("e' vincolata", "e' soggetta al vincolo",
                              "ai sensi del r.d.l. 3267/1923 l'area"):
                self.assertNotIn(forbidden, text)
            self.assertIn("3267", outcome.statement())

    def test_the_legal_references_are_both_cited(self):
        payload = HydrogeologicalOutcome().as_dict()
        self.assertIn("3267", payload["legal_reference"])
        self.assertIn("1126", payload["legal_reference"])


class TestNoSourceMeansNoVerdict(unittest.TestCase):
    def setUp(self):
        self.registry = DataSourceRegistry()
        self.registry.load()

    def test_an_area_with_no_configured_source_is_unverifiable(self):
        engine = HydrogeologicalConstraintEngine(registry=self.registry)
        engine.sources_for = lambda area: []
        outcome = engine.run(AREA)
        self.assertIs(outcome.presence, Presence.NON_VERIFICABILE)
        self.assertIn(DataGap.REGIONAL_SOURCE_REQUIRED.value, outcome.gaps)
        self.assertTrue(outcome.requires_regional_source)

    def test_that_case_says_so_in_words(self):
        engine = HydrogeologicalConstraintEngine(registry=self.registry)
        engine.sources_for = lambda area: []
        text = engine.run(AREA).statement()
        self.assertIn("competenza regionale", text)
        self.assertIn("non equivale all'assenza del vincolo", text)

    def test_the_gap_itself_refuses_to_mean_absence(self):
        self.assertFalse(DataGap.REGIONAL_SOURCE_REQUIRED.means_absence)
        self.assertFalse(DataGap.VIEW_ONLY.means_absence)


class TestViewOnlySourcesNeverProduceASurface(unittest.TestCase):
    """The line this engine exists to hold."""

    def outcome(self, hits, total):
        outcome = HydrogeologicalOutcome(capability="view_only",
                                         gaps=[DataGap.VIEW_ONLY.value])
        for index in range(total):
            outcome.samples.append(SamplePoint(0.0, float(index), "EPSG:3003",
                                               index < hits, {}, True))
        outcome.presence = (Presence.PRESENTE if hits == total
                            else Presence.ASSENTE if hits == 0 else Presence.PARZIALE)
        return outcome

    def test_a_view_only_result_has_no_area_and_no_percentage(self):
        outcome = self.outcome(4, 4)
        self.assertFalse(outcome.measurable)
        self.assertIsNone(outcome.area_m2)
        self.assertIsNone(outcome.percentage)
        payload = outcome.as_dict()
        self.assertIsNone(payload["area_m2"])
        self.assertIsNone(payload["percentage"])

    def test_the_statement_says_the_surface_cannot_be_computed(self):
        text = self.outcome(4, 4).statement()
        self.assertIn("non e' possibile calcolare la superficie", text)
        self.assertNotIn("%", text.split("compete")[0].replace("100%", ""))

    def test_the_sample_is_reported_as_points_not_as_extent(self):
        outcome = self.outcome(2, 5)
        self.assertIs(outcome.presence, Presence.PARZIALE)
        self.assertIn("2 punti su 5", outcome.statement())

    def test_a_measurable_result_does_report_the_surface(self):
        outcome = HydrogeologicalOutcome(presence=Presence.PARZIALE,
                                         capability="analysable",
                                         area_m2=124_300.0, percentage=34.7)
        self.assertTrue(outcome.measurable)
        self.assertIn("12.43 ha", outcome.statement())
        self.assertIn("34.7%", outcome.statement())


class TestSampling(unittest.TestCase):
    def test_every_sampled_point_falls_inside_the_area(self):
        from qgis.core import QgsGeometry

        points = HydrogeologicalConstraintEngine.sample_points(AREA, grid=3)
        self.assertTrue(points)
        geometry = AREA.geometry_in(AREA.work_crs)
        for point in points:
            self.assertTrue(geometry.contains(QgsGeometry.fromPointXY(point)),
                            "un punto campionato cade fuori dall'area")

    def test_a_denser_grid_asks_more_points(self):
        few = HydrogeologicalConstraintEngine.sample_points(AREA, grid=2)
        many = HydrogeologicalConstraintEngine.sample_points(AREA, grid=5)
        self.assertGreater(len(many), len(few))

    def test_even_a_degenerate_grid_yields_an_interior_point(self):
        self.assertTrue(HydrogeologicalConstraintEngine.sample_points(AREA, grid=0))


class TestItIsItsOwnEngine(unittest.TestCase):
    """Anti-pattern: the 1923 constraint mixed with PAI hazard."""

    @staticmethod
    def executable_source(name: str) -> str:
        """The module's code with docstrings and comments removed.

        The prose is allowed - and expected - to explain why this engine is *not* the
        PAI engine. What must stay out is the hazard vocabulary in the logic.
        """
        import ast
        import io
        import tokenize

        path = plugin_dir() / "engines" / "hydrogeological_constraint" / name
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    text = text.replace(doc, "")
        stripped = []
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type != tokenize.COMMENT:
                stripped.append(token.string)
        return " ".join(stripped)

    def test_the_engine_does_not_import_the_hazard_vocabulary(self):
        source = self.executable_source("engine.py")
        for word in ("flood_hazard", "landslide_hazard", "pericolosita"):
            self.assertNotIn(word, source,
                             f"«{word}» non appartiene al vincolo idrogeologico")

    def test_the_engine_does_not_read_the_hazard_engines(self):
        """Separation is structural, not a matter of wording."""
        source = self.executable_source("engine.py")
        for module in ("hydrogeo_risk", "flood", "landslide", "hazard"):
            self.assertNotIn(f"import {module}", source)

    def test_no_endpoint_is_hardcoded(self):
        for name in ("engine.py", "model.py"):
            source = (plugin_dir() / "engines" / "hydrogeological_constraint"
                      / name).read_text(encoding="utf-8")
            self.assertNotIn("http://", source)
            self.assertNotIn("https://", source)

    def test_sources_are_found_by_tag_so_a_region_is_configuration(self):
        registry = DataSourceRegistry()
        registry.load()
        tagged = [s for s in registry.query(operational_only=False)
                  if TAG in (s.tags or [])]
        self.assertTrue(tagged, "nessun descrittore marcato come vincolo idrogeologico")
        for source in tagged:
            self.assertEqual(source.category, "hydro_geomorphological")


class TestTheTuscanSourceIsDescribedHonestly(unittest.TestCase):
    """What the probe established has to survive in the catalogue."""

    def setUp(self):
        registry = DataSourceRegistry()
        registry.load()
        self.source = next((s for s in registry.query(operational_only=False)
                            if s.id == "rt.vincolo_idrogeologico"), None)

    def test_the_source_exists(self):
        self.assertIsNotNone(self.source)

    def test_it_is_declared_as_a_view_service_not_a_download(self):
        self.assertEqual(self.source.type.value, "WMS")

    def test_the_descriptor_records_why(self):
        note = (self.source.verification_note or "") + (self.source.notes or "")
        self.assertIn("TYPENAME", note)
        self.assertIn("VIEW_ONLY", note)

    def test_it_warns_that_the_layer_is_recognitive(self):
        self.assertIn("ricognitiv", (self.source.notes or "").lower())

    def test_the_wooded_areas_are_a_different_source(self):
        registry = DataSourceRegistry()
        registry.load()
        boscate = next((s for s in registry.query(operational_only=False)
                        if s.id == "rt.aree_boscate.2016"), None)
        self.assertIsNotNone(boscate)
        self.assertNotEqual(boscate.category, "hydro_geomorphological")
        self.assertNotIn(TAG, boscate.tags or [],
                         "le aree boscate non sono il vincolo idrogeologico")


if __name__ == "__main__":
    unittest.main()
