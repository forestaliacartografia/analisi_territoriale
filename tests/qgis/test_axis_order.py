"""Axis order is declared by the source, never guessed from the coordinates.

The defect this file guards produced no error of any kind. The PCN PAI service answers
WFS 1.1.0 with ``srsName="EPSG:4326"`` and writes latitude first, which is what the
standard prescribes for that CRS and the opposite of what the short form tells GDAL. The
reader placed every geometry with its ordinates exchanged: valid, plausible, and in the
wrong place. The intersection with the project area came out empty and the plugin
reported "the source answered and there is nothing here" over an area that is in fact
entirely inside a flood hazard perimeter.

Two things follow, and the tests below hold both.

**The order is configuration.** Over Italy latitude and longitude are both under 90, so
any rule of the form "if this looks like a latitude, swap" is a coin toss wearing the
clothes of logic: it would flip correct data as readily as wrong data. A heuristic of
exactly that shape was written here once and is gone.

**Request and response are separate facts.** The same service takes its bbox in one
order and answers in another, so one setting cannot describe both.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from qgis.core import QgsRectangle

from territorial_suite.core.paths import plugin_dir
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.core.registry import DataSource, DataSourceRegistry
from territorial_suite.services import vector_io
from territorial_suite.services.ogc.wfs import WfsClient

#: A real answer from the PCN PAI service, recorded rather than invented: short-form
#: ``EPSG:4326`` with latitude written first. A hand-made GML does not reproduce the
#: defect - the driver only mishandles this combination on the document structure the
#: service actually emits, which is precisely why the fixture is a recording.
#: First coordinates: 43.646844 10.642376, i.e. latitude before longitude.
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" /     "pai_pericolosita_lat_first.gml"

#: The window the recording was requested for, in the Maremma near Grosseto.
AREA = ProjectArea.from_rectangle(QgsRectangle(10.60, 43.62, 10.70, 43.68),
                                  "EPSG:4326", name="area della registrazione")
#: Once straightened the recording spans roughly 10.27-11.32 E, 43.52-43.92 N.


def read_fixture(folder: Path, order: str, name: str = "risposta"):
    """Read the recording through the real entry point.

    ``layer_from_payload`` is what the WFS client calls, and it sanitises the GML before
    writing it: the recording has to travel the same road or the test would exercise a
    path nothing uses.
    """
    return vector_io.layer_from_payload(
        FIXTURE.read_bytes(), folder, name=name,
        content_type="application/gml+xml", crs_hint="EPSG:4326",
        response_axis_order=order)


def first_ordinate(bbox: str) -> float:
    """The leading number of a BBOX parameter, whatever its formatting."""
    return float(bbox.split(",")[0])


class TestNoHeuristicSurvives(unittest.TestCase):
    """The rule that was banned, checked against the source rather than trusted."""

    def test_the_coordinate_heuristic_is_gone(self):
        source = (plugin_dir() / "services" / "vector_io.py").read_text(encoding="utf-8")
        for banned in ("looks_transposed", "_reopen_swapped"):
            self.assertNotIn(banned, source,
                             f"«{banned}» decideva lo swap dai valori delle coordinate")

    def test_nothing_decides_the_order_from_a_magnitude(self):
        source = (plugin_dir() / "services" / "vector_io.py").read_text(encoding="utf-8")
        for banned in ("> 90.0", ">= 90.0", "<= 90.0", "abs(extent."):
            self.assertNotIn(banned, source,
                             "l'ordine degli assi non si deduce dai valori")

    def test_the_orders_are_two_separate_settings(self):
        self.assertTrue(hasattr(vector_io, "AXIS_XY"))
        self.assertTrue(hasattr(vector_io, "AXIS_YX"))
        self.assertTrue(hasattr(vector_io, "AXIS_AUTO"))


class TestNormalisationIsDeclarative(unittest.TestCase):
    def setUp(self):
        import tempfile

        # Not a TemporaryDirectory: on Windows an open OGR layer keeps its file
        # locked and the cleanup would fail the test for the wrong reason.
        self.folder = Path(tempfile.mkdtemp(prefix="axis_"))

    def test_without_a_declaration_nothing_is_touched(self):
        """A file is only rewritten when the descriptor asks for it."""
        path = self.folder / "intatta.gml"
        path.write_bytes(FIXTURE.read_bytes())
        self.assertEqual(vector_io.normalise_axes(path, vector_io.AXIS_AUTO), path)
        self.assertEqual(vector_io.normalise_axes(path, vector_io.AXIS_XY), path)

    def test_the_raw_reading_puts_latitude_on_the_x_axis(self):
        """The defect itself: nothing fails, the numbers are just exchanged."""
        layer = read_fixture(self.folder, vector_io.AXIS_AUTO, "grezza")
        extent = layer.extent()
        self.assertGreater(extent.xMinimum(), 43.0, "x dovrebbe essere una latitudine")
        self.assertLess(extent.yMaximum(), 12.0, "y dovrebbe essere una longitudine")

    def test_a_latitude_first_answer_is_read_the_right_way_round(self):
        """Raw 43.646844 10.642376 must become x=10.64, y=43.64."""
        layer = read_fixture(self.folder, vector_io.AXIS_YX, "lettura")
        self.assertGreater(layer.featureCount(), 0)
        extent = layer.extent()
        self.assertTrue(10.0 < extent.xMinimum() < 12.0,
                        f"x deve essere una longitudine: {extent.toString(3)}")
        self.assertTrue(43.0 < extent.yMinimum() < 45.0,
                        f"y deve essere una latitudine: {extent.toString(3)}")

    def test_the_normalised_geometry_meets_the_area_the_raw_one_misses(self):
        """The defect in one assertion: same bytes, opposite spatial answer."""
        area_geom = AREA.geometry_in("EPSG:4326")

        raw = read_fixture(self.folder, vector_io.AXIS_AUTO, "grezza2")
        raw_hits = sum(1 for f in raw.getFeatures()
                       if f.geometry().intersects(area_geom))
        self.assertEqual(raw_hits, 0, "non normalizzata non deve intersecare")

        fixed = read_fixture(self.folder, vector_io.AXIS_YX, "normalizzata")
        fixed_hits = sum(1 for f in fixed.getFeatures()
                         if f.geometry().intersects(area_geom))
        self.assertGreater(fixed_hits, 0,
                           "normalizzata deve ricadere nell'area di progetto")

    def test_the_attributes_survive_the_rewrite(self):
        layer = read_fixture(self.folder, vector_io.AXIS_YX, "attributi")
        feature = next(layer.getFeatures())
        self.assertIn("pericolosita", [f.name() for f in layer.fields()])
        self.assertTrue(str(feature["pericolosita"]).strip(),
                        "la classe ufficiale deve sopravvivere alla riscrittura")
        self.assertIn("adb", [f.name() for f in layer.fields()])


class TestTheClientReadsTheDeclaration(unittest.TestCase):
    def source(self, **query):
        base = {"version": "1.1.0"}
        base.update(query)
        return DataSource.from_dict({
            "id": "t", "name": "t", "type": "WFS",
            "url": "http://example.invalid/ogc", "layer": "x",
            "crs": ["EPSG:4326"], "query": base})

    def test_the_response_order_comes_from_the_descriptor(self):
        client = WfsClient(self.source(response_axis_order="yx"))
        self.assertEqual(client.response_axis_order, "yx")

    def test_without_a_declaration_the_client_says_auto(self):
        self.assertEqual(WfsClient(self.source()).response_axis_order, "auto")

    def test_the_request_order_is_a_different_setting(self):
        client = WfsClient(self.source(request_axis_order="yx",
                                       response_axis_order="xy"))
        self.assertEqual(client.response_axis_order, "xy")
        rect = QgsRectangle(10.0, 43.0, 11.0, 44.0)
        bbox = client.bbox_param(rect, "EPSG:4326")
        self.assertAlmostEqual(first_ordinate(bbox), 43.0, places=3,
                               msg=f"richiesta dichiarata yx, latitudine prima: {bbox}")

    def test_the_older_spelling_still_works(self):
        client = WfsClient(self.source(bbox_axis_order="xy"))
        bbox = client.bbox_param(QgsRectangle(10.0, 43.0, 11.0, 44.0), "EPSG:4326")
        self.assertAlmostEqual(first_ordinate(bbox), 10.0, places=3)


class TestThePaiDescriptorsCarryWhatWasMeasured(unittest.TestCase):
    """The configuration is the fix: if it drifts, the geometries move again."""

    def setUp(self):
        registry = DataSourceRegistry()
        registry.load()
        self.pai = [s for s in registry.query(operational_only=False)
                    if s.id.startswith("pcn.pai.")]

    def test_every_pai_source_declares_both_orders(self):
        self.assertTrue(self.pai)
        for source in self.pai:
            self.assertEqual(source.query.get("request_axis_order"), "yx", source.id)
            self.assertEqual(source.query.get("response_axis_order"), "yx", source.id)

    def test_the_note_records_how_the_order_was_established(self):
        for source in self.pai:
            self.assertIn("assi", (source.verification_note or "").lower(), source.id)

    def test_other_services_are_not_forced_into_the_same_order(self):
        """The PAI exception is declared per source, not applied to everything."""
        registry = DataSourceRegistry()
        registry.load()
        others = [s for s in registry.query(operational_only=False)
                  if not s.id.startswith("pcn.pai.")
                  and s.query.get("response_axis_order")]
        self.assertEqual(others, [],
                         "nessun'altra fonte deve aver ereditato l'eccezione PAI")


if __name__ == "__main__":
    unittest.main()
