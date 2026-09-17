"""GUI smoke tests: the dock, the results panel and the dialogs must build and react.

They run on the offscreen Qt platform with a stub ``iface``: no window is shown, but every
widget is really constructed, which is what catches enum and API mistakes between Qt5 and
Qt6.
"""

from __future__ import annotations

import unittest

from qgis.core import QgsProject, QgsRectangle
from qgis.PyQt.QtWidgets import QApplication, QMainWindow, QWidget

from territorial_suite.core.constants import PLUGIN_NAME
from territorial_suite.core.project_area import ProjectArea
from tests.unit.test_report_package import sample_report


def gui_available() -> bool:
    """Whether a QApplication with widgets is usable in this process."""
    return QApplication.instance() is not None and hasattr(QApplication.instance(), "topLevelWidgets")


requires_gui = unittest.skipUnless(gui_available(), "QApplication non disponibile")


class StubMessageBar:
    """Collects the messages the GUI would show."""

    def __init__(self) -> None:
        self.messages = []

    def pushWarning(self, title, text):  # noqa: N802 - Qt-like API
        self.messages.append(("warning", title, text))

    def pushSuccess(self, title, text):  # noqa: N802 - Qt-like API
        self.messages.append(("success", title, text))

    def pushInfo(self, title, text):  # noqa: N802 - Qt-like API
        self.messages.append(("info", title, text))


class StubIface:
    """The slice of QgisInterface the plugin actually uses."""

    def __init__(self, window: QMainWindow) -> None:
        self._window = window
        self._bar = StubMessageBar()
        self._canvas = None
        self.layouts_opened = []
        self.docks = []

    def mainWindow(self):  # noqa: N802 - Qt-like API
        return self._window

    def messageBar(self):  # noqa: N802 - Qt-like API
        return self._bar

    def mapCanvas(self):  # noqa: N802 - Qt-like API
        if self._canvas is None:
            from qgis.gui import QgsMapCanvas

            self._canvas = QgsMapCanvas(self._window)
        return self._canvas

    def activeLayer(self):  # noqa: N802 - Qt-like API
        return None

    def addDockWidget(self, area, dock):  # noqa: N802 - Qt-like API
        self.docks.append(dock)

    def removeDockWidget(self, dock):  # noqa: N802 - Qt-like API
        if dock in self.docks:
            self.docks.remove(dock)

    def addPluginToMenu(self, menu, action):  # noqa: N802 - Qt-like API
        pass

    def removePluginMenu(self, menu, action):  # noqa: N802 - Qt-like API
        pass

    def addToolBarIcon(self, action):  # noqa: N802 - Qt-like API
        pass

    def removeToolBarIcon(self, action):  # noqa: N802 - Qt-like API
        pass

    def openLayoutDesigner(self, layout):  # noqa: N802 - Qt-like API
        self.layouts_opened.append(layout)


@requires_gui
class TestDock(unittest.TestCase):
    def setUp(self):
        from territorial_suite.gui.dock import TerritorialSuiteDock

        self.window = QMainWindow()
        self.iface = StubIface(self.window)
        self.dock = TerritorialSuiteDock(self.iface, self.window)

    def tearDown(self):
        self.dock.deleteLater()
        self.window.deleteLater()
        QgsProject.instance().clear()

    def test_dock_starts_without_an_area(self):
        self.assertIsNone(self.dock.area)
        self.assertIn("Nessuna area", self.dock.area_label.text())
        self.assertFalse(self.dock.one_click_button.isEnabled())

    def test_setting_an_area_enables_the_workflows(self):
        area = ProjectArea.from_rectangle(QgsRectangle(11.24, 43.76, 11.25, 43.77),
                                          "EPSG:4326", name="Area GUI")
        self.dock.set_area(area)
        self.assertIsNotNone(self.dock.area)
        self.assertIn("Area GUI", self.dock.area_label.text())
        self.assertTrue(self.dock.one_click_button.isEnabled())
        self.assertTrue(self.dock.quick_map_button.isEnabled())
        # the area layer landed in the project
        names = [layer.name() for layer in QgsProject.instance().mapLayers().values()]
        self.assertTrue(any("Area di progetto" in name for name in names))

    def test_area_is_restored_from_the_project(self):
        from territorial_suite.gui.dock import TerritorialSuiteDock

        area = ProjectArea.from_rectangle(QgsRectangle(11.24, 43.76, 11.25, 43.77),
                                          "EPSG:4326", name="Persistente")
        self.dock.set_area(area)
        other = TerritorialSuiteDock(self.iface, self.window)
        try:
            self.assertIsNotNone(other.area)
            self.assertEqual(other.area.name, "Persistente")
        finally:
            other.deleteLater()

    def test_actions_without_an_area_warn_instead_of_crashing(self):
        self.dock.run_one_click()
        self.dock.run_cadastre()
        self.dock.run_report()
        self.assertTrue(self.iface.messageBar().messages)

    def test_template_list_is_populated(self):
        self.assertGreaterEqual(self.dock.template_combo.count(), 10)

    def test_mode_toggle_updates_the_label(self):
        self.dock.mode_button.setChecked(True)
        self.assertEqual(self.dock.mode_button.text(), "Pro")
        self.dock.mode_button.setChecked(False)
        self.assertEqual(self.dock.mode_button.text(), "Quick")


@requires_gui
class TestResultsDock(unittest.TestCase):
    def setUp(self):
        from territorial_suite.gui.results_dock import ResultsDock

        self.window = QMainWindow()
        self.dock = ResultsDock(self.window)

    def tearDown(self):
        self.dock.deleteLater()
        self.window.deleteLater()

    def test_empty_state(self):
        self.dock.set_report(None)
        self.assertEqual(self.dock.alerts_table.rowCount(), 0)

    def test_report_fills_every_tab(self):
        report = sample_report()
        self.dock.set_report(report)
        self.assertEqual(self.dock.alerts_table.rowCount(), len(report.alerts))
        self.assertEqual(self.dock.constraints_table.rowCount(), len(report.results))
        self.assertEqual(self.dock.cadastre_table.rowCount(), len(report.cadastre))
        self.assertEqual(self.dock.sources_table.rowCount(), len(report.all_results))
        self.assertIn("ALTIMETRIA", self.dock.terrain_view.toPlainText())
        self.assertIn("grafiche", self.dock.cadastre_summary.text())


@requires_gui
class TestDialogs(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()

    def tearDown(self):
        self.parent.deleteLater()

    def test_coordinate_dialog_builds_an_area(self):
        from territorial_suite.gui.dialogs.area_dialog import CoordinateAreaDialog

        dialog = CoordinateAreaDialog(self.parent)
        dialog.min_x.setValue(11.24)
        dialog.min_y.setValue(43.76)
        dialog.max_x.setValue(11.25)
        dialog.max_y.setValue(43.77)
        area = dialog.area()
        self.assertIsNotNone(area)
        self.assertGreater(area.area_m2, 0)
        dialog.deleteLater()

    def test_coordinate_dialog_circle_mode(self):
        from territorial_suite.gui.dialogs.area_dialog import CoordinateAreaDialog

        dialog = CoordinateAreaDialog(self.parent)
        dialog.mode_combo.setCurrentIndex(1)
        dialog.centre_x.setValue(11.25)
        dialog.centre_y.setValue(43.77)
        dialog.radius.setValue(300.0)
        area = dialog.area()
        self.assertIsNotNone(area)
        self.assertAlmostEqual(area.area_m2, 3.14159 * 300 ** 2, delta=20000)
        dialog.deleteLater()

    def test_map_dialog_returns_a_spec(self):
        from territorial_suite.gui.dialogs.map_dialog import MapDialog

        dialog = MapDialog("cadastral_map", self.parent)
        spec = dialog.spec()
        self.assertEqual(spec.template, "cadastral_map")
        self.assertIn(spec.page_size, ("A4", "A3", "A2", "A1", "A0"))
        dialog.deleteLater()

    def test_analysis_options_dialog(self):
        from territorial_suite.gui.dialogs.analysis_dialog import AnalysisOptionsDialog

        dialog = AnalysisOptionsDialog(self.parent)
        options = dialog.options()
        self.assertTrue(options.include_cadastre)
        self.assertTrue(options.categories)
        dialog.deleteLater()

    def test_download_dialog_lists_categories(self):
        from territorial_suite.core.models import AdminUnit, AdminUnits
        from territorial_suite.gui.dialogs.download_dialog import DownloadDialog

        area = ProjectArea.from_rectangle(QgsRectangle(11.24, 43.76, 11.25, 43.77),
                                          "EPSG:4326")
        area.admin = AdminUnits(municipalities=[AdminUnit(name="Firenze",
                                                          region_name="Toscana")],
                                resolved=True)
        dialog = DownloadDialog(area, self.parent)
        self.assertGreater(dialog.list.count(), 0)
        dialog.deleteLater()

    def test_basemap_dialog_lists_services(self):
        from territorial_suite.gui.dialogs.basemap_dialog import BasemapDialog

        dialog = BasemapDialog(self.parent)
        self.assertGreater(dialog.list.count(), 0)
        self.assertTrue(dialog.source_id())
        dialog.deleteLater()

    def test_sources_dialog_lists_the_catalogue(self):
        from territorial_suite.gui.dialogs.sources_dialog import SourcesDialog

        dialog = SourcesDialog(self.parent)
        self.assertGreater(dialog.table.rowCount(), 0)
        dialog.deleteLater()

    def test_sources_dialog_shows_verification_and_coverage(self):
        """The user must be able to see how far a source has been verified."""
        from territorial_suite.gui.dialogs.sources_dialog import SourcesDialog

        dialog = SourcesDialog(self.parent)
        headers = [dialog.table.horizontalHeaderItem(i).text()
                   for i in range(dialog.table.columnCount())]
        self.assertIn("Verifica", headers)
        self.assertIn("Copertura", headers)
        column = headers.index("Verifica")
        values = {dialog.table.item(row, column).text()
                  for row in range(dialog.table.rowCount())
                  if dialog.table.item(row, column) is not None}
        self.assertIn("verificata sul servizio", values)
        dialog._show_details()
        self.assertIn("Stato di verifica", dialog.details.toHtml())
        dialog.deleteLater()

    def test_settings_dialog_builds_every_tab(self):
        from territorial_suite.gui.dialogs.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.parent)
        titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        self.assertEqual(titles, ["Rete", "Cache", "Analisi", "Cartografia",
                                  "Layout e relazioni", "Report"])
        dialog.deleteLater()

    def test_layout_settings_tab_edits_profiles(self):
        """The sheet's identity is configured here, not in the code."""
        from territorial_suite.gui.dialogs.layout_settings import LayoutSettingsWidget

        widget = LayoutSettingsWidget(self.parent)
        self.assertGreater(widget.profile_box.count(), 3)
        self.assertEqual(widget.tabs.count(), 5)
        widget.profile_box.setCurrentIndex(
            widget.profile_box.findData("semplificato"))
        self.assertEqual(widget.page_size.currentText(), "A4")
        self.assertEqual(widget.legend_mode.currentData(), "none")
        collected = widget.collect()
        self.assertEqual(collected.page_size, "A4")
        self.assertFalse(collected.builtin)
        widget.deleteLater()

    def test_layout_settings_never_shows_a_secret(self):
        from territorial_suite.gui.dialogs.layout_settings import LayoutSettingsWidget

        widget = LayoutSettingsWidget(self.parent)
        self.assertIn("configurata", widget.google_status.text().lower())
        widget.deleteLater()

    def test_onboarding_dialog(self):
        from territorial_suite.gui.onboarding import OnboardingDialog

        dialog = OnboardingDialog(self.parent)
        self.assertIn(PLUGIN_NAME, dialog.windowTitle())
        dialog.deleteLater()


@requires_gui
class TestCommandPalette(unittest.TestCase):
    def test_palette_filters_commands(self):
        from territorial_suite.gui.command_palette import Command, CommandPalette

        calls = []
        commands = [
            Command("Analisi territoriale", lambda: calls.append("analisi"),
                    ["analizza"]),
            Command("Crea mappa", lambda: calls.append("mappa"), ["layout"]),
            Command("Interroga il catasto", lambda: calls.append("catasto"),
                    ["particelle"]),
        ]
        palette = CommandPalette(commands)
        try:
            palette.search.setText("catasto")
            self.assertEqual(palette.list.count(), 1)
            palette.search.setText("particelle")
            self.assertEqual(palette.list.count(), 1)
            palette.search.setText("")
            self.assertEqual(palette.list.count(), 3)
        finally:
            palette.deleteLater()

    def test_subsequence_matching(self):
        from territorial_suite.gui.command_palette import Command

        command = Command("Crea mappa", lambda: None, [])
        self.assertGreater(command.score("crmap"), 0)
        self.assertEqual(command.score("zzz"), 0)


if __name__ == "__main__":
    unittest.main()
