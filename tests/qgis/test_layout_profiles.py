"""Layout profiles and the one-click orthophoto sheet.

A profile is the graphic identity of a sheet. The tests check that it is configuration
and not code, that it never silently drops something the user asked for (a missing logo
is reported on the sheet), and that an orthophoto sheet always carries its credit line.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qgis.core import QgsProject, QgsRectangle

from territorial_suite.core import settings
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.cartography.layout import LayoutBuilder, MapSpec
from territorial_suite.engines.cartography.orthophoto import OrthophotoEngine
from territorial_suite.engines.cartography.profiles import (
    ImageSpec,
    LayoutProfile,
    LegendSpec,
    NorthArrowSpec,
    ProfileStore,
)

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="Firenze centro")


def label_texts(layout) -> str:
    from qgis.core import QgsLayoutItemLabel

    return "\n".join(item.text() for item in layout.items()
                     if isinstance(item, QgsLayoutItemLabel))


class TestShippedProfiles(unittest.TestCase):
    def setUp(self):
        self.store = ProfileStore.instance()

    def test_the_documented_profiles_exist(self):
        ids = {profile.id for profile in self.store.all()}
        for expected in ("standard", "istituzionale", "tecnico", "professionale",
                         "semplificato", "ortofoto"):
            self.assertIn(expected, ids)
        self.assertEqual(self.store.errors, [])

    def test_every_profile_parses_into_real_objects(self):
        for profile in self.store.all():
            self.assertIsInstance(profile.legend, LegendSpec)
            self.assertIsInstance(profile.north_arrow, NorthArrowSpec)
            self.assertIn(profile.legend.mode, ("auto", "thematic", "custom", "none"))
            self.assertGreater(profile.margin_mm, 0)

    def test_an_unknown_profile_falls_back_to_the_default(self):
        profile = self.store.resolve("non_esiste")
        self.assertTrue(profile.id)
        self.assertIsNotNone(self.store.get(profile.id))

    def test_round_trip_through_json(self):
        for profile in self.store.all():
            again = LayoutProfile.from_dict(json.loads(json.dumps(profile.as_dict())))
            self.assertEqual(again.id, profile.id)
            self.assertEqual(again.legend.mode, profile.legend.mode)
            self.assertEqual(len(again.logos), len(profile.logos))


class TestImageSpec(unittest.TestCase):
    def test_a_missing_logo_is_reported_not_ignored(self):
        profile = LayoutProfile(logos=[ImageSpec(path="C:/non/esiste/logo.png")])
        problems = profile.unusable_pictures()
        self.assertEqual(len(problems), 1)
        self.assertIn("file non trovato", problems[0])

    def test_an_unsupported_format_is_explained(self):
        with tempfile.TemporaryDirectory() as folder:
            bogus = Path(folder) / "logo.tiff"
            bogus.write_bytes(b"x")
            spec = ImageSpec(path=str(bogus))
            self.assertFalse(spec.usable)
            self.assertIn("formato non supportato", spec.why_unusable())

    def test_a_real_png_is_usable(self):
        with tempfile.TemporaryDirectory() as folder:
            logo = Path(folder) / "logo.png"
            logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
            self.assertTrue(ImageSpec(path=str(logo)).usable)
            self.assertEqual(ImageSpec(path=str(logo)).why_unusable(), "")

    def test_a_bare_path_string_is_accepted(self):
        self.assertEqual(ImageSpec.from_dict("logo.png").path, "logo.png")


class TestProfileStorage(unittest.TestCase):
    def setUp(self):
        self.store = ProfileStore()
        self.store.load()
        self.created = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for profile_id in self.created:
            self.store.delete(profile_id)
        ProfileStore.reload()

    def test_duplicate_save_and_delete(self):
        base = self.store.resolve("standard")
        copy = base.duplicate("test_profilo_copia", "Copia di prova")
        self.assertFalse(copy.builtin)
        self.store.save(copy)
        self.created.append(copy.id)
        reloaded = ProfileStore().load()
        self.assertIsNotNone(reloaded.get("test_profilo_copia"))
        self.assertTrue(self.store.delete("test_profilo_copia"))
        self.created.remove(copy.id)
        self.assertIsNone(ProfileStore().load().get("test_profilo_copia"))

    def test_a_shipped_profile_cannot_be_deleted(self):
        self.assertFalse(self.store.delete("standard"))
        self.assertIsNotNone(ProfileStore().load().get("standard"))

    def test_export_and_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profilo.json"
            self.store.export("tecnico", path)
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["profiles"][0]["id"], "tecnico")
            payload["profiles"][0]["id"] = "test_profilo_importato"
            path.write_text(json.dumps(payload), encoding="utf-8")
            added = self.store.import_file(path)
            self.created.extend(added)
            self.assertEqual(added, ["test_profilo_importato"])
            self.assertIsNotNone(ProfileStore().load().get("test_profilo_importato"))


class TestProfileOnLayout(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def build(self, spec: MapSpec):
        return LayoutBuilder(self.project).build(AREA, spec, add_to_project=False)

    def test_the_profile_decides_the_page(self):
        layout = self.build(MapSpec(profile_id="semplificato"))
        page = layout.pageCollection().page(0)
        # A4 landscape is 297 x 210 mm
        self.assertAlmostEqual(page.pageSize().width(), 297.0, delta=1.0)
        self.assertAlmostEqual(page.pageSize().height(), 210.0, delta=1.0)

    def test_an_explicit_request_wins_over_the_profile(self):
        layout = self.build(MapSpec(profile_id="semplificato", page_size="A3"))
        page = layout.pageCollection().page(0)
        self.assertAlmostEqual(page.pageSize().width(), 420.0, delta=1.0)

    def test_a_profile_without_a_legend_draws_none(self):
        from qgis.core import QgsLayoutItemLegend

        layout = self.build(MapSpec(profile_id="semplificato"))
        legends = [i for i in layout.items() if isinstance(i, QgsLayoutItemLegend)]
        self.assertEqual(legends, [])

    def test_a_missing_logo_ends_up_on_the_sheet(self):
        store = ProfileStore.instance()
        profile = store.resolve("standard").duplicate("test_profilo_logo", "Con logo")
        profile.logos = [ImageSpec(path="C:/non/esiste/stemma.png")]
        profile.blocks = [{"type": "title", "h_mm": 12},
                          {"type": "logos", "h_mm": 16},
                          {"type": "warnings", "h_mm": 14, "font_size": 6}]
        store.save(profile)
        self.addCleanup(lambda: (store.delete("test_profilo_logo"),
                                 ProfileStore.reload()))
        layout = self.build(MapSpec(profile_id="test_profilo_logo"))
        self.assertIn("Avvertenze", label_texts(layout))
        self.assertIn("stemma.png", label_texts(layout))

    def test_profile_texts_are_expanded_in_a_text_block(self):
        store = ProfileStore.instance()
        profile = store.resolve("standard").duplicate("test_profilo_testi", "Testi")
        profile.texts = {"client": "Comune di Prova", "project": "PUC"}
        profile.blocks = [{"type": "text", "h_mm": 12,
                           "value": "{client} - {project} - {date}"}]
        store.save(profile)
        self.addCleanup(lambda: (store.delete("test_profilo_testi"),
                                 ProfileStore.reload()))
        layout = self.build(MapSpec(profile_id="test_profilo_testi"))
        text = label_texts(layout)
        self.assertIn("Comune di Prova", text)
        self.assertIn("PUC", text)
        self.assertNotIn("{client}", text)

    def test_an_unknown_placeholder_does_not_lose_the_text(self):
        store = ProfileStore.instance()
        profile = store.resolve("standard").duplicate("test_profilo_ph", "Placeholder")
        profile.blocks = [{"type": "text", "h_mm": 12, "value": "Prova {non_esiste}"}]
        store.save(profile)
        self.addCleanup(lambda: (store.delete("test_profilo_ph"), ProfileStore.reload()))
        layout = self.build(MapSpec(profile_id="test_profilo_ph"))
        self.assertIn("Prova", label_texts(layout))


class TestOrthophotoSheet(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self._probe = settings.get("cartography.probe_imagery", True)
        settings.set_value("cartography.probe_imagery", False)
        self.addCleanup(lambda: settings.set_value("cartography.probe_imagery",
                                                   self._probe))
        self.addCleanup(self.project.clear)

    def test_the_sheet_carries_the_credit_line(self):
        sheet = OrthophotoEngine(self.project).run(AREA, add_to_project=False)
        self.assertTrue(sheet.ok)
        self.assertTrue(sheet.credit_line)
        self.assertIn("Esri", label_texts(sheet.layout))

    def test_a_fallback_is_written_on_the_sheet_not_only_in_the_log(self):
        sheet = OrthophotoEngine(self.project).run(AREA, preference="google",
                                                   add_to_project=False)
        self.assertTrue(sheet.ok)
        self.assertTrue(sheet.warnings)
        text = label_texts(sheet.layout)
        self.assertIn("Avvertenze", text)
        self.assertIn("google", text.lower())

    def test_the_imagery_provenance_is_attached_to_the_report(self):
        from territorial_suite.core.models import AnalysisReport

        report = AnalysisReport(area=AREA.as_dict())
        OrthophotoEngine(self.project).run(AREA, report=report, add_to_project=False)
        payload = report.module("imagery")
        self.assertEqual(payload["imagery_provider"], "esri")
        self.assertTrue(payload["attribution"])
        self.assertIn("source_url", payload)

    def test_no_imagery_is_an_error_not_a_blank_sheet(self):
        """A sheet whose background silently failed would be worse than no sheet."""
        from territorial_suite.core.errors import LayoutError

        engine = OrthophotoEngine(self.project)
        engine.imagery.providers = lambda: []
        plan = engine.prepare(AREA)
        self.assertFalse(plan.ok)
        self.assertIn("nessuna sorgente", plan.reason.lower())
        with self.assertRaises(LayoutError):
            engine.compose(AREA, plan, add_to_project=False)


if __name__ == "__main__":
    unittest.main()
