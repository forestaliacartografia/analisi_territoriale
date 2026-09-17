"""Download Area dialog: choose the categories to fetch for the project area."""

from __future__ import annotations

from typing import List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import settings
from ...core.constants import PLUGIN_NAME
from ...core.project_area import ProjectArea
from ...core.taxonomy import Taxonomy
from ...engines.download import DownloadManager


class DownloadDialog(QDialog):
    """Shows which categories have sources available for this area."""

    def __init__(self, area: ProjectArea, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Scarica dati d'area")
        self.resize(420, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Categorie disponibili per quest'area (il numero indica le sorgenti "
            "configurate e applicabili):"))

        available = DownloadManager().available(area.admin)
        taxonomy = Taxonomy.instance()
        preselected = set(settings.get("download.categories", []))
        self.list = QListWidget()
        for category_id, sources in sorted(available.items()):
            category = taxonomy.get(category_id)
            label = category.label("it") if category else category_id
            item = QListWidgetItem(f"{label}  ({len(sources)})")
            item.setData(Qt.ItemDataRole.UserRole, category_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if category_id in preselected
                               else Qt.CheckState.Unchecked)
            item.setToolTip("\n".join(source.name for source in sources))
            self.list.addItem(item)
        if not available:
            self.list.addItem(QListWidgetItem("Nessuna sorgente disponibile per quest'area"))
        layout.addWidget(self.list, 1)

        layout.addWidget(QLabel(
            "<i>I dati vengono ritagliati sull'area (piu' un buffer) e salvati in un "
            "GeoPackage organizzato per categoria.</i>"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        settings.set_value("download.categories", self.categories())
        self.accept()

    def categories(self) -> List[str]:
        """Return the selected category ids."""
        result = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data and item.checkState() == Qt.CheckState.Checked:
                result.append(str(data))
        return result
