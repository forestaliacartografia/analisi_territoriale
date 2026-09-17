"""Choose a background service (basemap / orthophoto)."""

from __future__ import annotations

from typing import Optional

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

from ...core.constants import PLUGIN_NAME
from ...core.models import SourceType
from ...core.registry import DataSourceRegistry


class BasemapDialog(QDialog):
    """Lists the configured background services with their attribution."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Sfondo cartografico")
        self.resize(460, 320)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        registry = DataSourceRegistry.instance()
        for source in registry.query(categories=["imagery"],
                                     types=[SourceType.XYZ, SourceType.WMS,
                                            SourceType.WMTS, SourceType.ARCGIS_MAP]):
            item = QListWidgetItem(f"{source.name}  [{source.type.value}]")
            item.setData(Qt.ItemDataRole.UserRole, source.id)
            item.setToolTip(f"{source.authority}\n{source.attribution}\n"
                            f"{source.notes}".strip())
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)
        self.attribution = QLabel("")
        self.attribution.setWordWrap(True)
        layout.addWidget(self.attribution)
        self.list.currentRowChanged.connect(self._update_attribution)
        self._update_attribution(self.list.currentRow())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_attribution(self, row: int) -> None:
        source_id = self.source_id()
        source = DataSourceRegistry.instance().get(source_id) if source_id else None
        if source is None:
            self.attribution.setText("")
            return
        self.attribution.setText(
            f"<b>Attribuzione:</b> {source.attribution}<br>"
            f"<b>Licenza / condizioni:</b> {source.license}<br>"
            f"<i>{source.notes}</i>")

    def source_id(self) -> str:
        """Return the selected source id."""
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""
