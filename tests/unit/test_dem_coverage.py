"""A DEM is not "available" just because a file came back.

Two degradations used to happen silently in the tile acquisition, and both change what a
later statistic means:

* the zoom is lowered when the area needs more tiles than the budget allows, so the DEM
  is coarser than the caller asked for;
* individual tiles can fail to download, and the mosaic is built from what arrived, so it
  has holes.

Neither is wrong as a trade-off. Both are wrong as a secret: a slope computed on a DEM
with holes describes a smaller area than the one the user asked about, and nothing in the
output would say so.
"""

from __future__ import annotations

import unittest

from territorial_suite.core.gaps import DataGap
from territorial_suite.core.models import TerrainStats
from territorial_suite.engines.terrain import TileCoverage


class TestDataGapMeanings(unittest.TestCase):
    """The whole point of the enum is that the values are not interchangeable."""

    def test_only_an_answered_query_may_be_read_as_absence(self):
        absent = [gap for gap in DataGap if gap.means_absence]
        self.assertEqual(absent, [DataGap.NO_FEATURE_FOUND],
                         "solo una fonte che ha risposto puo' significare «non c'e'»")

    def test_every_gap_can_be_written_in_the_dossier(self):
        for gap in DataGap:
            self.assertTrue(gap.label_it.strip(), gap.name)

    def test_the_new_values_exist_and_are_distinct(self):
        for name in ("VIEW_ONLY", "REGIONAL_SOURCE_REQUIRED", "PARTIAL_COVERAGE",
                     "REDUCED_RESOLUTION"):
            self.assertIn(name, DataGap.__members__)
        self.assertNotEqual(DataGap.VIEW_ONLY, DataGap.NO_DATA)
        self.assertFalse(DataGap.REGIONAL_SOURCE_REQUIRED.means_absence,
                         "«serve la fonte regionale» non significa «non vincolato»")

    def test_the_enum_is_shared_not_copied(self):
        """The cultural heritage engine must use the same enum, not a twin of it."""
        from territorial_suite.engines.cultural_heritage.model import DataGap as Heritage

        self.assertIs(Heritage, DataGap)


class TestTileCoverage(unittest.TestCase):
    def test_a_complete_acquisition_declares_nothing(self):
        coverage = TileCoverage(requested_cell_size_m=10, effective_cell_size_m=10,
                                zoom_requested=13, zoom_used=13, tiles_expected=4)
        self.assertTrue(coverage.complete)
        self.assertFalse(coverage.reduced)
        self.assertEqual(coverage.gaps(), [])
        self.assertEqual(coverage.note(), "")

    def test_missing_tiles_are_a_partial_coverage_gap(self):
        coverage = TileCoverage(zoom_requested=13, zoom_used=13,
                                tiles_expected=16, tiles_missing=3)
        self.assertFalse(coverage.complete)
        self.assertIn(DataGap.PARTIAL_COVERAGE.value, coverage.gaps())
        self.assertIn("3 tile su 16", coverage.note())

    def test_a_lowered_zoom_is_a_reduced_resolution_gap(self):
        coverage = TileCoverage(requested_cell_size_m=5, effective_cell_size_m=20,
                                zoom_requested=14, zoom_used=12, tiles_expected=64)
        self.assertTrue(coverage.reduced)
        self.assertIn(DataGap.REDUCED_RESOLUTION.value, coverage.gaps())
        self.assertIn("20.0 m", coverage.note())
        self.assertIn("5.0 m", coverage.note())

    def test_both_degradations_are_reported_together(self):
        coverage = TileCoverage(requested_cell_size_m=5, effective_cell_size_m=10,
                                zoom_requested=14, zoom_used=13,
                                tiles_expected=64, tiles_missing=2)
        self.assertEqual(sorted(coverage.gaps()),
                         sorted([DataGap.PARTIAL_COVERAGE.value,
                                 DataGap.REDUCED_RESOLUTION.value]))
        self.assertIn(";", coverage.note(), "entrambi i motivi devono comparire")


class TestStatsCarryTheGaps(unittest.TestCase):
    def test_the_statistics_keep_the_requested_detail_beside_the_real_one(self):
        stats = TerrainStats(cell_size_m=20.0, requested_cell_size_m=5.0,
                             tiles_expected=64, tiles_missing=2,
                             gaps=[DataGap.PARTIAL_COVERAGE.value],
                             coverage_note="2 tile su 64 non scaricate")
        payload = stats.as_dict()
        self.assertEqual(payload["cell_size_m"], 20.0)
        self.assertEqual(payload["requested_cell_size_m"], 5.0)
        self.assertIn(DataGap.PARTIAL_COVERAGE.value, payload["gaps"])
        self.assertIn("non scaricate", payload["coverage_note"])

    def test_the_gaps_survive_a_round_trip_through_the_dossier(self):
        stats = TerrainStats(cell_size_m=10.0, gaps=[DataGap.REDUCED_RESOLUTION.value],
                             coverage_note="dettaglio ridotto")
        back = TerrainStats.from_dict(stats.as_dict())
        self.assertEqual(back.gaps, [DataGap.REDUCED_RESOLUTION.value])
        self.assertEqual(back.coverage_note, "dettaglio ridotto")

    def test_an_untouched_acquisition_declares_no_gap(self):
        self.assertEqual(TerrainStats().gaps, [])


class TestDegradationIsNeverSilent(unittest.TestCase):
    """The rule: a coarser DEM may be returned, but never without being asked for."""

    def test_the_budget_and_the_cost_are_both_recorded(self):
        coverage = TileCoverage(requested_cell_size_m=5.0, effective_cell_size_m=20.0,
                                zoom_requested=14, zoom_used=12,
                                max_tiles=64, tiles_at_requested=900,
                                tiles_expected=60,
                                degradation_reason="oltre il limite")
        self.assertEqual(coverage.max_tiles, 64)
        self.assertEqual(coverage.tiles_at_requested, 900)
        self.assertTrue(coverage.reduced)

    def test_the_proposal_names_an_alternative_and_how_to_accept_it(self):
        coverage = TileCoverage(requested_cell_size_m=5.0, effective_cell_size_m=20.0,
                                zoom_requested=14, zoom_used=12,
                                max_tiles=64, tiles_at_requested=900, tiles_expected=60)
        text = coverage.proposal()
        self.assertIn("900 tile", text)
        self.assertIn("limite di 64", text)
        self.assertIn("20.0 m", text)
        self.assertIn("approve_degradation", text)

    def test_an_unapproved_reduction_says_so_in_the_note(self):
        coverage = TileCoverage(requested_cell_size_m=5.0, effective_cell_size_m=20.0,
                                zoom_requested=14, zoom_used=12, tiles_expected=60,
                                user_approved=False, degradation_reason="oltre il limite")
        self.assertIn("non approvato", coverage.note())

    def test_an_approved_reduction_says_that_instead(self):
        coverage = TileCoverage(requested_cell_size_m=5.0, effective_cell_size_m=20.0,
                                zoom_requested=14, zoom_used=12, tiles_expected=60,
                                user_approved=True, degradation_reason="oltre il limite")
        self.assertIn("approvato dall'utente", coverage.note())

    def test_the_refusal_is_its_own_error_type(self):
        from territorial_suite.core.errors import EngineError, ResolutionNotApproved

        self.assertTrue(issubclass(ResolutionNotApproved, EngineError))

    def test_the_engine_refuses_rather_than_lowering_the_zoom(self):
        """Reading the source: no loop may silently walk the zoom down any more."""
        import ast

        from territorial_suite.core.paths import plugin_dir

        source = (plugin_dir() / "engines" / "terrain.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_download_tiles":
                target = node
        self.assertIsNotNone(target)
        raises = {n.func.id for n in ast.walk(target)
                  if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
                  and isinstance(n.exc.func, ast.Name)
                  for n in [n.exc]}
        self.assertIn("ResolutionNotApproved", raises,
                      "l'acquisizione deve rifiutare, non degradare da sola")


class TestDemMetadata(unittest.TestCase):
    """What the report has to be able to say about the DEM it used."""

    def test_every_required_field_exists(self):
        stats = TerrainStats()
        for name in ("requested_cell_size_m", "available_cell_size_m", "cell_size_m",
                     "tiles_expected", "max_tiles", "tiles_at_requested",
                     "resolution_degradation", "degradation_reason", "user_approved",
                     "vertical_reference", "vertical_crs", "horizontal_crs",
                     "extent", "pixel_size_m", "nodata", "acquired_at"):
            self.assertTrue(hasattr(stats, name), name)

    def test_the_vertical_reference_defaults_to_unknown_not_to_a_guess(self):
        self.assertEqual(TerrainStats().vertical_reference, "unknown")

    def test_the_metadata_survives_the_dossier(self):
        stats = TerrainStats(cell_size_m=20.0, requested_cell_size_m=5.0,
                             available_cell_size_m=2.4, max_tiles=64,
                             tiles_at_requested=900, resolution_degradation=True,
                             degradation_reason="oltre il limite", user_approved=True,
                             horizontal_crs="EPSG:32632", pixel_size_m=20.0,
                             nodata=-9999.0, extent={"xmin": 1.0, "ymin": 2.0,
                                                     "xmax": 3.0, "ymax": 4.0})
        back = TerrainStats.from_dict(stats.as_dict())
        self.assertTrue(back.resolution_degradation)
        self.assertTrue(back.user_approved)
        self.assertEqual(back.available_cell_size_m, 2.4)
        self.assertEqual(back.horizontal_crs, "EPSG:32632")
        self.assertEqual(back.nodata, -9999.0)
        self.assertEqual(back.extent["xmax"], 3.0)


if __name__ == "__main__":
    unittest.main()
