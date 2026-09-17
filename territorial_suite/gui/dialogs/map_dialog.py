"""One-click cartography dialog: map type, format, scale, style."""

from __future__ import annotations

from typing import Optional

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...core import settings
from ...core.constants import PLUGIN_NAME
from ...engines.cartography import scale as scale_engine
from ...engines.cartography.layout import MapSpec, template_list

AUTOMATIC = "Automatica"


class MapDialog(QDialog):
    """Collects the options of a single map."""

    def __init__(self, template_id: str = "territorial_overview",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Crea mappa")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.template_combo = QComboBox()
        for template in template_list():
            self.template_combo.addItem(template.get("name", ""), template.get("id"))
        index = self.template_combo.findData(template_id)
        if index >= 0:
            self.template_combo.setCurrentIndex(index)
        form.addRow("Tipo di tavola", self.template_combo)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Titolo (vuoto = titolo del template)")
        form.addRow("Titolo", self.title_edit)
        self.subtitle_edit = QLineEdit()
        self.subtitle_edit.setPlaceholderText("Sottotitolo (vuoto = Comune)")
        form.addRow("Sottotitolo", self.subtitle_edit)

        self.page_combo = QComboBox()
        self.page_combo.addItems(["A4", "A3", "A2", "A1", "A0"])
        self.page_combo.setCurrentText(settings.get("cartography.page_size", "A3"))
        form.addRow("Formato", self.page_combo)

        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["landscape", "portrait"])
        self.orientation_combo.setCurrentText(settings.get("cartography.orientation",
                                                           "landscape"))
        form.addRow("Orientamento", self.orientation_combo)

        self.scale_combo = QComboBox()
        self.scale_combo.addItem(AUTOMATIC, None)
        for value in scale_engine.configured_scales():
            self.scale_combo.addItem(scale_engine.format_scale(value), value)
        form.addRow("Scala", self.scale_combo)

        self.sheet_edit = QLineEdit()
        self.sheet_edit.setPlaceholderText("es. 01")
        form.addRow("Numero tavola", self.sheet_edit)

        self.author_edit = QLineEdit(settings.get("report.author", ""))
        form.addRow("Redatto da", self.author_edit)

        layout.addLayout(form)
        self.grid_check = QCheckBox("Griglia di coordinate")
        self.grid_check.setChecked(bool(settings.get("cartography.grid", True)))
        self.legend_check = QCheckBox("Legenda")
        self.legend_check.setChecked(True)
        self.north_check = QCheckBox("Freccia del nord")
        self.north_check.setChecked(True)
        self.scalebar_check = QCheckBox("Barra di scala")
        self.scalebar_check.setChecked(True)
        for widget in (self.grid_check, self.legend_check, self.north_check,
                       self.scalebar_check):
            layout.addWidget(widget)

        layout.addWidget(QLabel(
            "<i>La scala automatica sceglie il primo valore tondo in cui l'area entra "
            "nella cornice.</i>"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def spec(self) -> MapSpec:
        """Return the map specification chosen by the user."""
        return MapSpec(
            template=str(self.template_combo.currentData() or "territorial_overview"),
            title=self.title_edit.text().strip(),
            subtitle=self.subtitle_edit.text().strip(),
            page_size=self.page_combo.currentText(),
            orientation=self.orientation_combo.currentText(),
            scale=self.scale_combo.currentData(),
            sheet_number=self.sheet_edit.text().strip(),
            author=self.author_edit.text().strip(),
            show_grid=self.grid_check.isChecked(),
            show_legend=self.legend_check.isChecked(),
            show_north_arrow=self.north_check.isChecked(),
            show_scale_bar=self.scalebar_check.isChecked(),
        )
