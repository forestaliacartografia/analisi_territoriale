"""Command palette: one keystroke to reach any command, source or template."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import PLUGIN_NAME
from ..core.registry import normalise


@dataclass
class Command:
    """One entry of the palette."""

    label: str
    callback: Callable[[], None]
    keywords: List[str] = field(default_factory=list)
    hint: str = ""

    def score(self, query: str) -> int:
        """Return a match score (higher is better, 0 = no match)."""
        if not query:
            return 1
        haystack = normalise(" ".join([self.label, self.hint] + self.keywords))
        needle = normalise(query)
        if not needle:
            return 1
        if haystack.startswith(needle):
            return 100
        if needle in haystack:
            return 60
        # subsequence match, so "crmap" finds "crea mappa"
        position = 0
        for char in needle:
            position = haystack.find(char, position)
            if position < 0:
                return 0
            position += 1
        return 20


class CommandPalette(QDialog):
    """Modal, type-to-filter list of commands."""

    def __init__(self, commands: List[Command], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Comandi")
        self.setModal(True)
        self.resize(520, 420)
        self.commands = commands

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Digita un comando: analizza, catasto, mappa, DEM...")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._run_current)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemActivated.connect(lambda item: self._run_current())
        self.list.itemDoubleClicked.connect(lambda item: self._run_current())
        layout.addWidget(self.list, 1)

        self.hint = QLabel("Invio per eseguire, Esc per chiudere.")
        layout.addWidget(self.hint)
        self._refresh("")
        self.search.setFocus()

    def _refresh(self, text: str) -> None:
        scored = [(command.score(text), command) for command in self.commands]
        matches = sorted([item for item in scored if item[0] > 0],
                         key=lambda item: (-item[0], item[1].label))
        self.list.clear()
        for _, command in matches:
            item = QListWidgetItem(command.label if not command.hint
                                   else f"{command.label}   -   {command.hint}")
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        command = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if command is not None and command.callback is not None:
            command.callback()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI callback
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.list.setFocus()
        super().keyPressEvent(event)


def build_commands(dock, iface) -> List[Command]:
    """Build the command list from the dock actions."""
    return [
        Command("Analisi territoriale (one-click)", dock.run_one_click,
                ["analizza", "analisi", "quadro", "conoscitivo"], "esegue l'intera analisi"),
        Command("Analizza i vincoli", dock.run_constraints,
                ["vincoli", "constraints", "sensibilita"]),
        Command("Interroga il catasto", dock.run_cadastre,
                ["catasto", "particelle", "foglio", "mappale"]),
        Command("Calcola il terreno (DEM, pendenza)", dock.run_terrain,
                ["dem", "dtm", "pendenza", "slope", "quote", "altimetria"]),
        Command("Genera fasce di rispetto", dock.run_proximity,
                ["buffer", "fascia", "distanza", "prossimita", "rispetto"]),
        Command("Scarica i dati d'area", dock.run_download,
                ["download", "scarica", "strade", "idrografia", "edifici"]),
        Command("Aggiungi sfondo cartografico", dock.add_basemap,
                ["basemap", "ortofoto", "satellite", "osm"]),
        Command("Crea mappa", dock.run_quick_map,
                ["mappa", "layout", "tavola", "carta"]),
        Command("Genera serie di tavole", dock.run_map_series,
                ["serie", "tavole", "map series"]),
        Command("Esporta le tavole in PDF", dock.export_layouts,
                ["export", "pdf", "stampa"]),
        Command("Genera la relazione territoriale", dock.run_report,
                ["report", "relazione", "dossier", "pdf"]),
        Command("Esporta il pacchetto completo", dock.run_package,
                ["pacchetto", "package", "scarica tutto", "zip"]),
        Command("Gestione sorgenti dati", dock.open_sources,
                ["sorgenti", "fonti", "servizi", "wfs", "wms", "stato"]),
        Command("Impostazioni", dock.open_settings, ["settings", "opzioni", "cache"]),
        Command("Mostra i risultati", dock.show_results, ["risultati", "pannello"]),
        Command("Disegna una nuova area", lambda: dock.start_draw("polygon"),
                ["area", "disegna", "poligono"]),
        Command("Area da coordinate o BBOX", dock.area_from_coordinates,
                ["coordinate", "bbox", "riquadro"]),
        Command("Area dalla selezione", lambda: dock.area_from_layer(True),
                ["selezione", "feature"]),
    ]
