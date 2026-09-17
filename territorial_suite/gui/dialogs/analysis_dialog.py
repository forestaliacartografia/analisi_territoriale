"""Professional-mode options for the territorial analysis."""

from __future__ import annotations

from typing import List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import settings
from ...core.constants import PLUGIN_NAME
from ...core.taxonomy import Taxonomy
from ...engines.analysis import AnalysisOptions


class AnalysisOptionsDialog(QDialog):
    """Lets the user pick the steps, the categories and the thresholds of the analysis."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Opzioni di analisi")
        self.resize(460, 560)
        layout = QVBoxLayout(self)

        steps = QGroupBox("Fasi")
        steps_layout = QVBoxLayout(steps)
        self.admin_check = QCheckBox("Individua Comune, Provincia e Regione")
        self.admin_check.setChecked(True)
        self.cadastre_check = QCheckBox("Catasto (particelle e fogli)")
        self.cadastre_check.setChecked(True)
        self.constraints_check = QCheckBox("Vincoli e sensibilita'")
        self.constraints_check.setChecked(True)
        self.terrain_check = QCheckBox("Terreno (DEM, pendenza, esposizione)")
        self.terrain_check.setChecked(True)
        self.contours_check = QCheckBox("Curve di livello")
        self.download_check = QCheckBox("Scarica anche i dati vettoriali d'area")
        for widget in (self.admin_check, self.cadastre_check, self.constraints_check,
                       self.terrain_check, self.contours_check, self.download_check):
            steps_layout.addWidget(widget)
        layout.addWidget(steps)

        categories = QGroupBox("Categorie del quadro conoscitivo")
        categories_layout = QVBoxLayout(categories)
        self.category_list = QListWidget()
        self.category_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        enabled = set(settings.get("analysis.categories", []))
        for category in Taxonomy.instance().all():
            item = QListWidgetItem(category.label("it"))
            item.setData(Qt.ItemDataRole.UserRole, category.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if category.id in enabled
                               else Qt.CheckState.Unchecked)
            self.category_list.addItem(item)
        categories_layout.addWidget(self.category_list)
        layout.addWidget(categories, 1)

        parameters = QGroupBox("Parametri")
        form = QFormLayout(parameters)
        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setRange(0.0, 20000.0)
        self.buffer_spin.setSuffix(" m")
        self.buffer_spin.setValue(float(settings.get("analysis.context_buffer_m", 500)))
        form.addRow("Buffer di contesto", self.buffer_spin)
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(0.0, 50000.0)
        self.distance_spin.setSuffix(" m")
        self.distance_spin.setValue(float(settings.get("analysis.max_distance_m", 5000)))
        form.addRow("Raggio massimo per le distanze", self.distance_spin)
        self.cell_spin = QDoubleSpinBox()
        self.cell_spin.setRange(1.0, 200.0)
        self.cell_spin.setSuffix(" m")
        self.cell_spin.setValue(float(settings.get("terrain.cell_size_m", 10)))
        form.addRow("Risoluzione DEM", self.cell_spin)
        self.refresh_check = QCheckBox("Ignora la cache (riscarica tutto)")
        form.addRow("", self.refresh_check)
        layout.addWidget(parameters)

        layout.addWidget(QLabel(
            "<i>Le soglie e le categorie sono salvate come impostazioni del plugin.</i>"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_categories(self) -> List[str]:
        result = []
        for index in range(self.category_list.count()):
            item = self.category_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return result

    def _accept(self) -> None:
        settings.set_value("analysis.categories", self._selected_categories())
        settings.set_value("analysis.context_buffer_m", self.buffer_spin.value())
        settings.set_value("analysis.max_distance_m", self.distance_spin.value())
        settings.set_value("terrain.cell_size_m", self.cell_spin.value())
        self.accept()

    def options(self) -> AnalysisOptions:
        """Return the analysis options chosen by the user."""
        return AnalysisOptions(
            categories=self._selected_categories() or None,
            resolve_admin=self.admin_check.isChecked(),
            include_cadastre=self.cadastre_check.isChecked(),
            include_constraints=self.constraints_check.isChecked(),
            include_terrain=self.terrain_check.isChecked(),
            include_download=self.download_check.isChecked(),
            terrain_cell_size_m=self.cell_spin.value(),
            terrain_contours=self.contours_check.isChecked(),
            refresh=self.refresh_check.isChecked(),
        )
