"""Source manager: catalogue, availability and per-project overrides."""

from __future__ import annotations

from typing import Dict, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core import log
from ...core.constants import PLUGIN_NAME
from ...core.models import SourceStatus
from ...core.registry import DataSourceRegistry
from ...services.health import HealthChecker
from ...tasks import run_in_background

STATUS_SYMBOL = {
    SourceStatus.ONLINE: "● ONLINE",
    SourceStatus.DEGRADED: "● DEGRADATA",
    SourceStatus.OFFLINE: "○ OFFLINE",
    SourceStatus.DISABLED: "○ DISATTIVA",
    SourceStatus.UNKNOWN: "○ ?",
}


class SourcesDialog(QDialog):
    """Lists the catalogue, checks availability and toggles sources per project."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Sorgenti dati")
        self.resize(900, 560)
        self.registry = DataSourceRegistry.instance()
        self.task = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtra per nome, ente, categoria...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter_edit, 1)
        self.check_button = QPushButton("Verifica disponibilita'")
        self.check_button.clicked.connect(self.check_health)
        top.addWidget(self.check_button)
        self.reload_button = QPushButton("Ricarica catalogo")
        self.reload_button.clicked.connect(self.reload_catalogue)
        top.addWidget(self.reload_button)
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(
            ["Attiva", "Nome", "Ente", "Categoria", "Tipo", "Ambito", "Natura del dato",
             "Verifica", "Copertura", "Stato"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.currentCellChanged.connect(lambda *_: self._show_details())
        layout.addWidget(self.table, 1)

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setMaximumHeight(160)
        layout.addWidget(self.details)

        self.errors_label = QLabel("")
        self.errors_label.setWordWrap(True)
        layout.addWidget(self.errors_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._populate()

    # ------------------------------------------------------------------ table

    def _populate(self) -> None:
        sources = self.registry.all(enabled_only=False)
        self.table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            check = QCheckBox()
            check.setChecked(source.enabled)
            check.stateChanged.connect(
                lambda state, source_id=source.id: self._toggle(source_id, state))
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(6, 0, 0, 0)
            holder_layout.addWidget(check)
            holder_layout.addStretch(1)
            self.table.setCellWidget(row, 0, holder)
            values = [source.name, source.authority, source.category, source.type.value,
                      f"{source.scope.level} {', '.join(source.scope.codes)}".strip(),
                      source.nature_label_it,
                      source.verification_status.label_it,
                      source.coverage.label_it(), ""]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, source.id)
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
            if not source.is_operational:
                # Catalogued but not queryable: say so instead of letting the user believe
                # the plugin will interrogate it.
                item = self.table.item(row, 7)
                if item is not None:
                    item.setForeground(QBrush(QColor("#a05000")))
                check.setEnabled(False)
                check.setToolTip("Sorgente censita ma non operativa: non viene interrogata.")
        self.table.resizeColumnsToContents()
        errors = self.registry.errors
        catalogued = len([s for s in sources if not s.is_operational])
        summary = f"{len(sources)} sorgenti nel catalogo"
        if catalogued:
            summary += (f", di cui {catalogued} censite ma non operative "
                        f"(documentano un dato esistente che il plugin non puo' "
                        f"interrogare).")
        else:
            summary += "."
        self.errors_label.setText(
            f"<span style='color:#a00'>Descrittori non validi: {'; '.join(errors)}</span>"
            if errors else summary)
        if sources:
            self.table.selectRow(0)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            haystack = " ".join(
                self.table.item(row, column).text().lower()
                for column in range(1, self.table.columnCount())
                if self.table.item(row, column) is not None)
            self.table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _current_source_id(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 1)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def _show_details(self) -> None:
        source = self.registry.get(self._current_source_id())
        if source is None:
            self.details.setHtml("")
            return
        self.details.setHtml(
            f"<b>{source.name}</b> ({source.id})<br>"
            f"Ente: {source.authority}<br>"
            f"Servizio: <a href='{source.url}'>{source.url}</a><br>"
            f"Layer: {source.layer}<br>"
            f"Natura del dato: {source.nature_label_it}<br>"
            f"Stato di verifica: {source.verification_status.label_it}"
            f"{' - ' + source.verification_note if source.verification_note else ''}<br>"
            f"Copertura: {source.coverage.label_it()}"
            f"{' - ' + source.coverage.notes if source.coverage.notes else ''}<br>"
            f"Riferimento normativo dichiarato: {source.legal_reference or '-'}<br>"
            f"Licenza: {source.license or '-'} | Attribuzione: {source.attribution or '-'}<br>"
            f"Aggiornamento: {source.update_frequency or '-'} | "
            f"Ultima verifica: {source.last_verified or '-'}<br>"
            f"Scala: {source.scale or '-'} | Accuratezza: "
            f"{source.accuracy_m if source.accuracy_m else '-'}<br>"
            f"Alternative dichiarate: "
            f"{', '.join(source.fallback_sources) if source.fallback_sources else '-'}<br>"
            f"Origine descrittore: {source.origin} ({source.descriptor_path})<br>"
            f"<i>{source.notes}</i>")

    def _toggle(self, source_id: str, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        self.registry.set_project_override(source_id, {"enabled": enabled})
        log.info(f"Sorgente {source_id} {'attivata' if enabled else 'disattivata'} "
                 f"per questo progetto")

    # ------------------------------------------------------------------ actions

    def reload_catalogue(self) -> None:
        """Reload the descriptors from disk."""
        DataSourceRegistry.reset_instance()
        self.registry = DataSourceRegistry.instance()
        self._populate()

    def check_health(self) -> None:
        """Probe every enabled source in the background."""
        if self.task is not None:
            return
        # Only sources that claim to be reachable are probed: the catalogued ones have no
        # endpoint to talk to and would just time out.
        sources = [s for s in self.registry.all(enabled_only=True) if s.is_operational]
        self.check_button.setEnabled(False)
        self.check_button.setText("Verifica in corso...")

        def work(feedback):
            return HealthChecker().check_many(sources, refresh=True, feedback=feedback)

        def done(reports):
            self.task = None
            self.check_button.setEnabled(True)
            self.check_button.setText("Verifica disponibilita'")
            mapping: Dict[str, str] = {}
            for report in reports:
                mapping[report.source_id] = STATUS_SYMBOL.get(
                    report.status, report.status.value)
                if report.message:
                    mapping[report.source_id] += f" - {report.message[:60]}"
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 1)
                if item is None:
                    continue
                source_id = str(item.data(Qt.ItemDataRole.UserRole))
                self.table.setItem(row, 9, QTableWidgetItem(mapping.get(source_id, "")))
            self.table.resizeColumnToContents(9)

        def failed(error):
            self.task = None
            self.check_button.setEnabled(True)
            self.check_button.setText("Verifica disponibilita'")
            self.errors_label.setText(f"Verifica non riuscita: {error}")

        self.task = run_in_background(f"{PLUGIN_NAME}: verifica sorgenti", work,
                                      on_success=done, on_error=failed)
