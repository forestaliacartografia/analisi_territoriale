"""Results panel: summary, alerts, constraints, cadastre, terrain and sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core import measure, settings
from ..core.constants import PLUGIN_NAME
from ..core.models import AnalysisReport
from ..engines.analysis import AnalysisOrchestrator
from ..engines.report import ReportEngine
from ..engines.terrain import summarise as summarise_terrain


def _table(headers: Sequence[str]) -> QTableWidget:
    """Return a read-only table widget with the given headers."""
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSortingEnabled(True)
    return table


def _fill(table: QTableWidget, rows: Sequence[Sequence[Any]]) -> None:
    """Fill a table with plain values."""
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setToolTip(item.text())
            table.setItem(row_index, column_index, item)
    table.resizeColumnsToContents()
    table.setSortingEnabled(True)


class ResultsDock(QgsDockWidget):
    """Dock showing the outcome of the last analysis."""

    create_area_from_parcels = pyqtSignal()
    refresh_sources = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(f"{PLUGIN_NAME} - Risultati", parent)
        self.setObjectName("TerritorialSuiteResults")
        self.report: Optional[AnalysisReport] = None

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget(container)
        layout.addWidget(self.tabs)
        self.setWidget(container)

        self.summary_view = QTextBrowser()
        self.summary_view.setOpenExternalLinks(True)
        self.tabs.addTab(self.summary_view, "Riepilogo")

        self.alerts_table = _table(["Livello", "Segnalazione", "Dettaglio",
                                    "Natura del dato", "Riferimento"])
        self.tabs.addTab(self.alerts_table, "Segnalazioni")

        self.constraints_table = _table(["Fonte", "Categoria", "Stato", "Presente",
                                         "Superficie interessata", "% area",
                                         "Distanza minima", "Natura del dato"])
        self.tabs.addTab(self.constraints_table, "Vincoli e dati")

        self.tabs.addTab(self._build_cadastre_tab(), "Catasto")

        self.terrain_view = QTextBrowser()
        self.tabs.addTab(self.terrain_view, "Terreno")

        self.sources_table = _table(["Fonte", "Ente", "Stato", "Elementi", "Licenza",
                                     "Errore"])
        self.tabs.addTab(self._wrap_sources_tab(), "Fonti")

    # ------------------------------------------------------------------ tabs

    def _build_cadastre_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.cadastre_summary = QLabel("Nessuna analisi catastale eseguita.")
        self.cadastre_summary.setWordWrap(True)
        layout.addWidget(self.cadastre_summary)
        self.cadastre_table = _table(["Comune", "Foglio", "Particella",
                                      "Superficie grafica", "Superficie interessata",
                                      "% particella"])
        layout.addWidget(self.cadastre_table)
        buttons = QHBoxLayout()
        self.export_csv_button = QPushButton("Esporta CSV")
        self.export_csv_button.clicked.connect(lambda: self._export_table("csv"))
        self.export_xlsx_button = QPushButton("Esporta XLSX")
        self.export_xlsx_button.clicked.connect(lambda: self._export_table("xlsx"))
        self.copy_button = QPushButton("Copia tabella")
        self.copy_button.clicked.connect(self._copy_cadastre)
        self.area_from_parcels_button = QPushButton("Crea area dalle particelle selezionate")
        self.area_from_parcels_button.clicked.connect(self.create_area_from_parcels.emit)
        for button in (self.export_csv_button, self.export_xlsx_button, self.copy_button,
                       self.area_from_parcels_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return widget

    def _wrap_sources_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.sources_table)
        row = QHBoxLayout()
        refresh = QPushButton("Verifica disponibilita' fonti")
        refresh.clicked.connect(self.refresh_sources.emit)
        row.addWidget(refresh)
        row.addStretch(1)
        layout.addLayout(row)
        return widget

    # ------------------------------------------------------------------ data

    def set_report(self, report: Optional[AnalysisReport]) -> None:
        """Display an analysis report."""
        self.report = report
        if report is None:
            self.summary_view.setHtml("<p>Nessuna analisi disponibile.</p>")
            for table in (self.alerts_table, self.constraints_table, self.cadastre_table,
                          self.sources_table):
                table.setRowCount(0)
            self.terrain_view.setHtml("")
            return
        self._fill_summary(report)
        self._fill_alerts(report)
        self._fill_constraints(report)
        self._fill_cadastre(report)
        self._fill_terrain(report)
        self._fill_sources(report)

    def _fill_summary(self, report: AnalysisReport) -> None:
        text = AnalysisOrchestrator.summary_text(report)
        warnings = ""
        if report.warnings:
            items = "".join(f"<li>{item}</li>" for item in report.warnings)
            warnings = f"<h4>Avvisi</h4><ul>{items}</ul>"
        self.summary_view.setHtml(
            f"<pre style='font-family:Consolas,monospace'>{text}</pre>{warnings}"
            "<p style='color:#555'><i>Le informazioni riportate hanno natura ricognitiva: "
            "la verifica dei vincoli va effettuata presso gli enti competenti.</i></p>")

    def _fill_alerts(self, report: AnalysisReport) -> None:
        rows = [[alert.level.label_it, alert.title, alert.detail,
                 alert.evidence_level.label_it, alert.legal_reference]
                for alert in report.sorted_alerts()]
        _fill(self.alerts_table, rows)
        for index, alert in enumerate(report.sorted_alerts()):
            item = self.alerts_table.item(index, 0)
            if item is not None:
                font = QFont()
                font.setBold(alert.level.rank >= 2)
                item.setFont(font)

    def _fill_constraints(self, report: AnalysisReport) -> None:
        rows = []
        for result in report.results:
            rows.append([
                result.source_name,
                result.category,
                result.status.value,
                "si" if result.present else "no",
                measure.format_area(result.intersect_area_m2)
                if result.intersect_area_m2 else "-",
                f"{result.intersect_pct:.1f}" if result.intersect_pct else "-",
                measure.format_distance(result.min_distance_m)
                if result.min_distance_m is not None else "-",
                result.evidence_level.label_it,
            ])
        _fill(self.constraints_table, rows)

    def _fill_cadastre(self, report: AnalysisReport) -> None:
        rows = [[row.municipality, row.sheet, row.parcel,
                 measure.format_area(row.area_cadastral_m2),
                 measure.format_area(row.area_intersect_m2),
                 f"{row.intersect_pct:.1f}"] for row in report.cadastre]
        _fill(self.cadastre_table, rows)
        if not report.cadastre:
            self.cadastre_summary.setText(
                "Nessuna particella catastale disponibile per quest'area.")
            return
        municipalities = {row.municipality for row in report.cadastre}
        sheets = {(row.municipality, row.sheet) for row in report.cadastre}
        total = sum(row.area_intersect_m2 for row in report.cadastre)
        self.cadastre_summary.setText(
            f"{len(report.cadastre)} particelle in {len(municipalities)} Comune/i e "
            f"{len(sheets)} fogli - superficie interessata {measure.format_area(total)}. "
            f"Le superfici sono grafiche, derivate dalla mappa catastale.")

    def _fill_terrain(self, report: AnalysisReport) -> None:
        terrain = report.terrain
        if terrain is None or not terrain.available:
            self.terrain_view.setHtml("<p>Dati altimetrici non disponibili.</p>")
            return
        rows = "".join(
            f"<tr><td>{item.label}</td><td align='right'>"
            f"{measure.format_area(item.area_m2)}</td>"
            f"<td align='right'>{item.area_pct:.1f} %</td></tr>"
            for item in terrain.slope_classes)
        aspect = "".join(f"<tr><td>{name}</td><td align='right'>{value:.1f} %</td></tr>"
                         for name, value in terrain.aspect_histogram.items())
        self.terrain_view.setHtml(
            f"<pre style='font-family:Consolas,monospace'>{summarise_terrain(terrain)}</pre>"
            f"<h4>Classi di pendenza</h4><table width='100%'>{rows}</table>"
            f"<h4>Esposizione</h4><table width='60%'>{aspect}</table>")

    def _fill_sources(self, report: AnalysisReport) -> None:
        rows = []
        for result in report.all_results:
            provenance = result.provenance
            rows.append([result.source_name,
                         provenance.authority if provenance else "",
                         result.status.value, result.feature_count,
                         provenance.license if provenance else "",
                         result.error])
        _fill(self.sources_table, rows)

    # ------------------------------------------------------------------ actions

    def _export_table(self, fmt: str) -> None:
        if self.report is None or not self.report.cadastre:
            QMessageBox.information(self, PLUGIN_NAME, "Nessuna tabella catastale da esportare.")
            return
        folder = settings.get("paths.last_output_dir", "") or str(Path.home())
        suffix = "CSV (*.csv)" if fmt == "csv" else "Excel (*.xlsx)"
        path, _ = QFileDialog.getSaveFileName(self, "Esporta tabella catastale",
                                              str(Path(folder) / f"catasto.{fmt}"), suffix)
        if not path:
            return
        engine = ReportEngine(self.report)
        if fmt == "csv":
            engine.write_csv(Path(path), engine.cadastral_rows())
        else:
            engine.write_xlsx(Path(path))
        settings.set_value("paths.last_output_dir", str(Path(path).parent))
        QMessageBox.information(self, PLUGIN_NAME, f"Tabella esportata in\n{path}")

    def _copy_cadastre(self) -> None:
        if self.report is None or not self.report.cadastre:
            return
        engine = ReportEngine(self.report)
        rows = engine.cadastral_rows()
        text = "\n".join("\t".join(str(cell) for cell in row) for row in rows)
        from qgis.PyQt.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
