"""Dialogs to create a project area from coordinates and to pick a stored one."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qgis.core import QgsPointXY, QgsRectangle
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import crs as crs_utils
from ...core import measure
from ...core.constants import PLUGIN_NAME
from ...core.errors import TerritorialSuiteError
from ...core.project_area import ProjectArea

MODE_BBOX = 0
MODE_CENTRE = 1


def _coordinate_spin(minimum: float, maximum: float, decimals: int = 6) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(0.001)
    return spin


class CoordinateAreaDialog(QDialog):
    """Create an area from a bounding box or from a centre point plus a radius."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Area da coordinate")
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit("Area da coordinate")
        form.addRow("Nome", self.name_edit)
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(crs_utils.crs_from(crs_utils.WGS84))
        form.addRow("Sistema di riferimento", self.crs_widget)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Bounding box", "Centro e raggio"])
        form.addRow("Modalita'", self.mode_combo)
        layout.addLayout(form)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        bbox_page = QWidget()
        bbox_form = QFormLayout(bbox_page)
        self.min_x = _coordinate_spin(-1e8, 1e8)
        self.min_y = _coordinate_spin(-1e8, 1e8)
        self.max_x = _coordinate_spin(-1e8, 1e8)
        self.max_y = _coordinate_spin(-1e8, 1e8)
        self.min_x.setValue(11.24)
        self.min_y.setValue(43.76)
        self.max_x.setValue(11.26)
        self.max_y.setValue(43.78)
        for label, widget in (("X min / Lon min", self.min_x), ("Y min / Lat min", self.min_y),
                              ("X max / Lon max", self.max_x), ("Y max / Lat max", self.max_y)):
            bbox_form.addRow(label, widget)
        self.stack.addWidget(bbox_page)

        centre_page = QWidget()
        centre_form = QFormLayout(centre_page)
        self.centre_x = _coordinate_spin(-1e8, 1e8)
        self.centre_y = _coordinate_spin(-1e8, 1e8)
        self.centre_x.setValue(11.25)
        self.centre_y.setValue(43.77)
        self.radius = QDoubleSpinBox()
        self.radius.setRange(1.0, 100000.0)
        self.radius.setValue(500.0)
        self.radius.setSuffix(" m")
        centre_form.addRow("X / Lon", self.centre_x)
        centre_form.addRow("Y / Lat", self.centre_y)
        centre_form.addRow("Raggio", self.radius)
        self.stack.addWidget(centre_page)

        self.mode_combo.currentIndexChanged.connect(self.stack.setCurrentIndex)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                        QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def area(self) -> Optional[ProjectArea]:
        """Build the project area from the dialog values."""
        crs = self.crs_widget.crs()
        name = self.name_edit.text().strip() or "Area da coordinate"
        try:
            if self.mode_combo.currentIndex() == MODE_BBOX:
                rect = QgsRectangle(self.min_x.value(), self.min_y.value(),
                                    self.max_x.value(), self.max_y.value())
                if rect.isEmpty():
                    raise TerritorialSuiteError("Il bounding box e' vuoto")
                return ProjectArea.from_rectangle(rect, crs, name=name)
            centre = QgsPointXY(self.centre_x.value(), self.centre_y.value())
            return ProjectArea.from_circle(centre, self.radius.value(), crs, name=name)
        except TerritorialSuiteError as exc:
            QMessageBox.warning(self, PLUGIN_NAME, str(exc))
            return None


class AreaPickerDialog(QDialog):
    """Choose one of the project areas stored in the QGIS project."""

    def __init__(self, areas: List[Dict[str, Any]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Aree salvate")
        self.areas = areas
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Seleziona l'area di progetto da caricare:"))
        self.list = QListWidget()
        for payload in areas:
            area_m2 = float(payload.get("area_m2", 0.0) or 0.0)
            item = QListWidgetItem(
                f"{payload.get('name', 'Area')} - {measure.format_area(area_m2)} "
                f"({payload.get('created_at', '')[:10]})")
            self.list.addItem(item)
        if areas:
            self.list.setCurrentRow(len(areas) - 1)
        layout.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> Optional[Dict[str, Any]]:
        """Return the selected area payload."""
        row = self.list.currentRow()
        if row < 0 or row >= len(self.areas):
            return None
        return self.areas[row]
