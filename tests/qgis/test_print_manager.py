"""A print must survive being closed, and must be recoverable after a bad update.

What is stored is the recipe, not the drawing. That is the whole point: a stored PDF can
be reprinted but never refreshed, whereas a stored specification can be rebuilt against
new data, which is the operation the user actually wants.

The register lives in the QGIS project, so it travels with the file that gets saved and
sent to somebody else.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsProject, QgsRectangle

from territorial_suite.core.constants import PROP_PRINT_ID, PROP_PRINT_REGISTRY
from territorial_suite.core.errors import LayoutError
from territorial_suite.core.project_area import ProjectArea
from territorial_suite.engines.cartography.layout import MapSpec
from territorial_suite.engines.cartography.print_manager import (
    HISTORY_LIMIT,
    PrintManager,
    PrintRecord,
    print_id_of,
)

AREA = ProjectArea.from_rectangle(QgsRectangle(11.250, 43.766, 11.262, 43.774),
                                  "EPSG:4326", name="Firenze centro")


class ManagerCase(unittest.TestCase):
    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)
        self.manager = PrintManager(self.project)

    def create(self, title="Tavola di prova", template="territorial_overview"):
        return self.manager.create(
            AREA, MapSpec(template=template, title=title), add_to_project=False)


class TestCreateAndList(ManagerCase):
    def test_a_new_print_is_recorded(self):
        record, layout = self.create()
        self.assertTrue(record.print_id)
        self.assertEqual(record.title, "Tavola di prova")
        self.assertEqual(record.version, 1)
        self.assertEqual([r.print_id for r in self.manager.records()], [record.print_id])
        self.assertIsNotNone(layout)

    def test_the_layout_knows_which_print_it_is(self):
        record, layout = self.create()
        self.assertEqual(print_id_of(layout), record.print_id)
        self.assertEqual(layout.customProperty(PROP_PRINT_ID, ""), record.print_id)

    def test_prints_are_stored_in_the_project_not_on_disk(self):
        self.create()
        raw, ok = self.project.readEntry("territorial_suite", "prints", "")
        self.assertTrue(ok)
        self.assertIn("print_id", raw)

    def test_an_empty_project_has_no_prints(self):
        self.assertEqual(self.manager.records(), [])
        self.assertIsNone(self.manager.get("inesistente"))

    def test_the_list_shows_the_most_recently_touched_first(self):
        first, _ = self.create("Prima")
        second, _ = self.create("Seconda")
        second.updated_at = "2030-01-01T00:00:00+00:00"
        self.manager.save(second)
        self.assertEqual(self.manager.records()[0].print_id, second.print_id)
        self.assertEqual(len(self.manager.records()), 2)


class TestReopen(ManagerCase):
    def test_a_stored_print_can_be_rebuilt(self):
        record, _ = self.create("Tavola 03", template="hydrographic_map")
        reopened, layout = self.manager.open(record.print_id, AREA, add_to_project=False)
        self.assertEqual(reopened.print_id, record.print_id)
        self.assertEqual(reopened.template, "hydrographic_map")
        self.assertEqual(print_id_of(layout), record.print_id)

    def test_the_recipe_survives_the_round_trip(self):
        record, _ = self.create()
        stored = self.manager.get(record.print_id)
        spec = stored.map_spec()
        self.assertEqual(spec.template, record.template)
        self.assertEqual(spec.title, record.title)

    def test_reopening_something_that_is_not_there_says_so(self):
        with self.assertRaises(LayoutError) as caught:
            self.manager.open("non_esiste", AREA, add_to_project=False)
        self.assertIn("non esiste", str(caught.exception))


class TestUpdateAndVersioning(ManagerCase):
    def test_an_update_keeps_the_identity_and_bumps_the_version(self):
        record, _ = self.create("Tavola 03")
        updated, _ = self.manager.update(record.print_id, AREA, add_to_project=False)
        self.assertEqual(updated.print_id, record.print_id)
        self.assertEqual(updated.version, 2)
        self.assertEqual(len(self.manager.records()), 1,
                         "un aggiornamento non deve creare una seconda stampa")

    def test_the_previous_version_is_kept(self):
        record, _ = self.create("Titolo originale")
        self.manager.update(record.print_id, AREA,
                            spec=MapSpec(template=record.template, title="Titolo nuovo"),
                            add_to_project=False)
        current = self.manager.get(record.print_id)
        self.assertEqual(current.title, "Titolo nuovo")
        self.assertEqual(len(current.history), 1)
        self.assertEqual(current.history[0]["title"], "Titolo originale")

    def test_a_superseded_version_can_be_brought_back(self):
        record, _ = self.create("Titolo originale")
        self.manager.update(record.print_id, AREA,
                            spec=MapSpec(template=record.template, title="Titolo sbagliato"),
                            add_to_project=False)
        restored = self.manager.restore(record.print_id, 1)
        self.assertEqual(restored.title, "Titolo originale")
        self.assertEqual(restored.print_id, record.print_id)
        self.assertGreater(restored.version, 2, "il ripristino e' a sua volta una versione")

    def test_restoring_is_itself_undoable(self):
        record, _ = self.create("A")
        self.manager.update(record.print_id, AREA,
                            spec=MapSpec(template=record.template, title="B"),
                            add_to_project=False)
        restored = self.manager.restore(record.print_id, 1)
        titles = [h["title"] for h in restored.history]
        self.assertIn("B", titles, "la versione sostituita dal ripristino resta")

    def test_restoring_a_version_that_was_never_kept_is_refused(self):
        record, _ = self.create()
        with self.assertRaises(LayoutError):
            self.manager.restore(record.print_id, 99)

    def test_the_history_does_not_grow_without_bound(self):
        record, _ = self.create()
        for index in range(HISTORY_LIMIT + 5):
            self.manager.update(record.print_id, AREA,
                                spec=MapSpec(template=record.template,
                                             title=f"Versione {index}"),
                                add_to_project=False)
        current = self.manager.get(record.print_id)
        self.assertLessEqual(len(current.history), HISTORY_LIMIT)
        self.assertEqual(current.version, HISTORY_LIMIT + 6)

    def test_a_history_entry_does_not_nest_its_own_history(self):
        record, _ = self.create()
        self.manager.update(record.print_id, AREA, add_to_project=False)
        self.manager.update(record.print_id, AREA, add_to_project=False)
        current = self.manager.get(record.print_id)
        for entry in current.history:
            self.assertNotIn("history", entry)


class TestDuplicate(ManagerCase):
    def test_a_duplicate_is_independent(self):
        record, _ = self.create("Originale")
        copy = self.manager.duplicate(record.print_id)
        self.assertNotEqual(copy.print_id, record.print_id)
        self.assertEqual(copy.version, 1)
        self.assertEqual(copy.history, [])
        self.assertIn("copia", copy.title.lower())
        self.assertEqual(len(self.manager.records()), 2)

    def test_a_duplicate_can_be_named(self):
        record, _ = self.create()
        copy = self.manager.duplicate(record.print_id, title="Variante A3")
        self.assertEqual(copy.title, "Variante A3")

    def test_editing_a_duplicate_leaves_the_original_alone(self):
        record, _ = self.create("Originale")
        copy = self.manager.duplicate(record.print_id)
        self.manager.update(copy.print_id, AREA,
                            spec=MapSpec(template=copy.template, title="Cambiata"),
                            add_to_project=False)
        self.assertEqual(self.manager.get(record.print_id).title, "Originale")


class TestRemoval(ManagerCase):
    def test_a_print_can_be_deleted(self):
        record, _ = self.create()
        self.assertTrue(self.manager.remove(record.print_id))
        self.assertEqual(self.manager.records(), [])

    def test_deleting_something_absent_reports_it_instead_of_pretending(self):
        self.assertFalse(self.manager.remove("non_esiste"))


class TestQualityIsRecorded(ManagerCase):
    def test_the_record_carries_the_last_quality_outcome(self):
        """A list must be able to show which sheets are in trouble without rebuilding."""
        record, _ = self.manager.create(
            AREA, MapSpec(template="flood_hazard_map", title="Carta della pericolosita "
                                                             "idraulica"),
            add_to_project=False)
        self.assertEqual(record.qa_level, "error",
                         "nessuno strato del tema e' presente: deve risultare in errore")
        self.assertTrue(record.qa_summary)
        self.assertEqual(record.theme, "flood_hazard")

    def test_a_sheet_without_a_theme_records_no_alarm(self):
        record, _ = self.create(template="territorial_overview")
        self.assertNotEqual(record.qa_level, "error")


class TestCorruptedProject(ManagerCase):
    def test_an_unreadable_register_is_ignored_not_fatal(self):
        self.project.writeEntry("territorial_suite", "prints", "{non json")
        self.assertEqual(self.manager.records(), [])

    def test_a_register_that_is_not_a_list_is_ignored(self):
        self.project.writeEntry("territorial_suite", "prints", '{"a": 1}')
        self.assertEqual(self.manager.records(), [])


class TestRecordShape(unittest.TestCase):
    def test_a_record_survives_serialisation(self):
        import json

        record = PrintRecord(title="Tavola", template="risk_map", version=3)
        payload = record.as_dict()
        json.dumps(payload)
        back = PrintRecord.from_dict(payload)
        self.assertEqual(back.title, "Tavola")
        self.assertEqual(back.version, 3)

    def test_an_unnamed_print_still_has_a_label(self):
        self.assertIn("Stampa", PrintRecord().label)


if __name__ == "__main__":
    unittest.main()
