"""QGIS plugin entry point: menus, toolbar, docks and the Processing provider."""

from __future__ import annotations

from typing import List, Optional

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import QAction, QShortcut

from .core import compat_guards, log, settings
from .core.constants import PLUGIN_NAME
from .core.paths import resources_dir

MENU = f"&{PLUGIN_NAME}"


class TerritorialSuitePlugin:
    """Wires the plugin into the QGIS user interface."""

    def __init__(self, iface) -> None:
        self.iface = iface
        self.actions: List[QAction] = []
        self.dock = None
        self.results_dock = None
        self.provider = None
        self.shortcut: Optional[QShortcut] = None
        compat_guards.apply()
        log.set_debug(bool(settings.get("general.debug_logging", False)))

    # ------------------------------------------------------------------ lifecycle

    def initGui(self) -> None:  # noqa: N802 - QGIS API
        """Create the docks, the toolbar and register the Processing provider."""
        from .gui.dock import TerritorialSuiteDock
        from .gui.results_dock import ResultsDock

        self.dock = TerritorialSuiteDock(self.iface, self.iface.mainWindow())
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

        self.results_dock = ResultsDock(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.results_dock)
        self.results_dock.hide()
        self.dock.report_changed.connect(self._on_report_changed)
        self.results_dock.create_area_from_parcels.connect(self.dock.area_from_parcels)
        self.results_dock.refresh_sources.connect(self.dock.open_sources)

        self._add_action(PLUGIN_NAME, self._toggle_dock,
                         icon="analisi_territoriale.png",
                         toolbar=True, checkable=True, checked=True)
        self._add_action("Analisi territoriale (one-click)", self.dock.run_one_click,
                         toolbar=True)
        self._add_action("Crea mappa", self.dock.run_quick_map, toolbar=True)
        self._add_action("Comandi...", self.open_palette, toolbar=True)
        self._add_action("Risultati", self._toggle_results)
        self._add_action("Sorgenti dati...", self.dock.open_sources)
        self._add_action("Impostazioni...", self.dock.open_settings)
        self._add_action("Primi passi...", self.show_onboarding)

        self.shortcut = QShortcut(QKeySequence("Ctrl+Shift+T"), self.iface.mainWindow())
        self.shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut.activated.connect(self.open_palette)

        self._register_provider()
        self._maybe_show_onboarding()
        log.info(f"{PLUGIN_NAME} caricato")

    def unload(self) -> None:
        """Remove every UI element and the Processing provider."""
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []
        if self.shortcut is not None:
            self.shortcut.setEnabled(False)
            self.shortcut = None
        for dock in (self.results_dock, self.dock):
            if dock is not None:
                self.iface.removeDockWidget(dock)
                dock.deleteLater()
        self.dock = None
        self.results_dock = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        log.info(f"{PLUGIN_NAME} rimosso")

    # ------------------------------------------------------------------ helpers

    def _add_action(self, text: str, callback, *, icon: str = "", toolbar: bool = False,
                    checkable: bool = False, checked: bool = False) -> QAction:
        path = resources_dir() / "icons" / icon if icon else None
        action = QAction(QIcon(str(path)) if path and path.exists() else QIcon(), text,
                         self.iface.mainWindow())
        action.triggered.connect(callback)
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)
        self.iface.addPluginToMenu(MENU, action)
        if toolbar:
            self.iface.addToolBarIcon(action)
        self.actions.append(action)
        return action

    def _register_provider(self) -> None:
        """Register the Processing provider (algorithms mirror the GUI workflows)."""
        try:
            from .processing.provider import TerritorialSuiteProvider

            self.provider = TerritorialSuiteProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)
        except Exception as exc:  # pragma: no cover - Processing unavailable
            log.warning(f"Provider Processing non registrato: {exc}")

    def _toggle_dock(self, checked: bool) -> None:
        if self.dock is not None:
            self.dock.setVisible(checked)

    def _toggle_results(self) -> None:
        if self.results_dock is not None:
            self.results_dock.setVisible(not self.results_dock.isVisible())

    def _on_report_changed(self, report) -> None:
        if self.results_dock is None:
            return
        self.results_dock.set_report(report)
        if report is not None:
            self.results_dock.show()
            self.results_dock.raise_()

    def open_palette(self) -> None:
        """Open the command palette."""
        if self.dock is None:
            return
        from .gui.command_palette import CommandPalette, build_commands

        CommandPalette(build_commands(self.dock, self.iface),
                       self.iface.mainWindow()).exec()

    def show_onboarding(self) -> None:
        """Show the first-run guide on demand."""
        from .gui.onboarding import OnboardingDialog

        OnboardingDialog(self.iface.mainWindow()).exec()

    def _maybe_show_onboarding(self) -> None:
        from .gui.onboarding import should_show

        if should_show():
            self.show_onboarding()
