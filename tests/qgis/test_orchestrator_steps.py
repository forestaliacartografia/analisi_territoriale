"""The pipeline must not fall over because a step was half-registered.

The bug this file exists for: ``hazard_risk`` was added to the list of enabled steps but
not to the progress-weight table. The weights are summed **before** the per-step
try/except, so the lookup raised ``KeyError('hazard_risk')`` and killed the whole
analysis before a single handler ran. What the user saw was
``Analisi territoriale: Analisi territoriale: 'hazard_risk'`` - the plugin name twice and
a quoted word, because a ``KeyError`` stringifies to the repr of its key.

Two classes of defect are guarded here: a step that exists in one table and not the
other, and an exception that reaches the user without becoming a sentence.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsRectangle

from territorial_suite.core.errors import ConfigError, SourceUnavailableError, describe
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines import analysis as analysis_module
from territorial_suite.engines.analysis import AnalysisOptions, AnalysisOrchestrator

# The structural tests read the pipeline declaration; the behavioural ones deliberately
# do not, so that they keep reproducing the bug regardless of how it was fixed.
_STEPS = getattr(analysis_module, "_STEPS", ())
_WEIGHTS = getattr(analysis_module, "_WEIGHTS", {})

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="AOI rettangolare")


def offline_options(**overrides) -> AnalysisOptions:
    """Options that touch no network: only the step wiring is under test."""
    base = dict(resolve_admin=False, include_cadastre=False, include_constraints=False,
                include_cultural_heritage=False, include_terrain=False,
                include_download=False)
    base.update(overrides)
    return AnalysisOptions(**base)


@unittest.skipUnless(_STEPS, "la pipeline non e' dichiarata in un solo posto")
class TestEveryDeclaredStepIsComplete(unittest.TestCase):
    """A step is three things at once, and none of them may be missing."""

    def test_every_step_has_a_weight(self):
        for name, _weight, _option in _STEPS:
            self.assertIn(name, _WEIGHTS, f"«{name}» non ha un peso di avanzamento")

    def test_every_step_has_a_handler(self):
        orchestrator = AnalysisOrchestrator()
        for name, _weight, _option in _STEPS:
            self.assertTrue(hasattr(orchestrator, f"_step_{name}"),
                            f"manca il metodo _step_{name}")

    def test_every_optional_step_names_a_real_option(self):
        options = AnalysisOptions()
        for name, _weight, option in _STEPS:
            if option:
                self.assertTrue(hasattr(options, option),
                                f"«{name}» punta all'opzione inesistente «{option}»")

    def test_hazard_risk_is_among_them(self):
        """The regression itself: the step must be declared, weighted and handled."""
        names = [name for name, _w, _o in _STEPS]
        self.assertIn("hazard_risk", names)
        self.assertIn("hazard_risk", _WEIGHTS)
        self.assertTrue(hasattr(AnalysisOrchestrator(), "_step_hazard_risk"))

    def test_the_weights_come_from_the_declaration(self):
        """Two tables drift; one cannot."""
        self.assertEqual(set(_WEIGHTS), {name for name, _w, _o in _STEPS})


class TestTheAnalysisSurvivesTheHazardStep(unittest.TestCase):
    """Reproduces the P0 exactly: build an AOI, run the analysis, reach hazard_risk.

    The hazard engine is stubbed with an empty outcome. What is under test is the
    *wiring* - that the step is declared, weighted, dispatched and lands in the report -
    and the suite has to stay offline and fast; the engine's own behaviour is covered by
    ``test_hazard_risk.py`` and by the real-data runs.
    """

    def setUp(self):
        from territorial_suite.engines import hazard_risk

        self._real_run = hazard_risk.HazardRiskEngine.run
        hazard_risk.HazardRiskEngine.run = lambda self, area, **kw:             hazard_risk.HazardRiskOutcome(area_m2=1.0)
        self.addCleanup(setattr, hazard_risk.HazardRiskEngine, "run", self._real_run)

    def test_running_with_hazard_risk_enabled_raises_no_key_error(self):
        options = offline_options(include_hazard_risk=True)
        self.assertIn("hazard_risk", AnalysisOrchestrator._enabled_steps(options))
        try:
            report = AnalysisOrchestrator().run(AREA, options)
        except KeyError as exc:  # pragma: no cover - this is the bug
            self.fail(f"KeyError({exc.args[0]!r}) dall'orchestratore: la fase e' "
                      f"dichiarata ma non registrata ovunque serve")
        self.assertIsNotNone(report)

    def test_the_step_reaches_the_report(self):
        report = AnalysisOrchestrator().run(AREA, offline_options(include_hazard_risk=True))
        self.assertIn("hazard_risk", report.modules,
                      "la fase e' stata eseguita ma non ha scritto nel report")

    def test_the_module_carries_the_theme_matrix(self):
        report = AnalysisOrchestrator().run(AREA, offline_options(include_hazard_risk=True))
        payload = report.module("hazard_risk")
        for key in ("themes", "matrix", "area_m2"):
            self.assertIn(key, payload)

    def test_a_failing_step_does_not_abort_the_others(self):
        """One source in trouble must not take the analysis down with it."""
        orchestrator = AnalysisOrchestrator()
        original = orchestrator._step_hazard_risk

        def explode(*args, **kwargs):
            raise SourceUnavailableError("fonte finta non disponibile", source_id="x")

        orchestrator._step_hazard_risk = explode
        report = orchestrator.run(AREA, offline_options(include_hazard_risk=True))
        self.assertTrue(any("hazard_risk" in w for w in report.warnings))
        self.assertIsNotNone(report.finished_at, "l'analisi deve comunque concludersi")
        orchestrator._step_hazard_risk = original

    @unittest.skipUnless(hasattr(analysis_module, "_STEPS"),
                         "la pipeline non e' dichiarata in un solo posto")
    def test_a_step_declared_without_a_handler_is_refused_by_name(self):
        """The failure mode that produced the bug, now with a sentence attached."""
        original = analysis_module._STEPS
        analysis_module._STEPS = original + (("fase_inesistente", 1, ""),)
        try:
            with self.assertRaises(ConfigError) as caught:
                AnalysisOrchestrator().run(AREA, offline_options())
            self.assertIn("fase_inesistente", str(caught.exception))
        finally:
            analysis_module._STEPS = original


class TestErrorsBecomeSentences(unittest.TestCase):
    """What reaches the message bar has to be readable by whoever reads it."""

    def test_a_key_error_no_longer_arrives_as_a_quoted_word(self):
        text = describe(KeyError("hazard_risk"))
        self.assertIn("hazard_risk", text)
        self.assertNotEqual(text.strip(), "'hazard_risk'")
        self.assertIn("configurazione", text)

    def test_a_key_error_says_it_is_a_defect_not_a_data_problem(self):
        self.assertIn("difetto del plugin", describe(KeyError("x")))

    def test_an_import_failure_keeps_the_real_reason(self):
        text = describe(ImportError("No module named 'osgeo'"))
        self.assertIn("osgeo", text)
        self.assertIn("non caricabile", text)

    def test_the_plugin_own_errors_pass_through_unchanged(self):
        error = SourceUnavailableError("La fonte non risponde", source_id="pcn.x")
        self.assertEqual(describe(error), str(error))

    def test_an_internal_error_is_named_as_such(self):
        self.assertIn("errore interno", describe(AttributeError("'NoneType' has no 'x'")))

    def test_an_exception_without_a_message_still_yields_something(self):
        self.assertTrue(describe(RuntimeError()).strip())


if __name__ == "__main__":
    unittest.main()
