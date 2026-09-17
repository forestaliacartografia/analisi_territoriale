"""The Territorial Suite dock: one panel, five workflows.

Complexity inside, simplicity outside: the dock only collects choices, starts background
tasks and applies their results. Every button here maps to an engine call that is also
available from the Processing toolbox.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from qgis.core import QgsProject
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import log, measure, settings
from ..core.errors import describe as describe_error
from ..core.constants import PLUGIN_NAME
from ..core.errors import TerritorialSuiteError
from ..core.models import AnalysisReport
from ..core.project_area import ProjectArea, ProjectAreaStore
from ..engines.analysis import AnalysisOptions, AnalysisOrchestrator
from ..engines.cadastre_engine import CadastreEngine
from ..engines.cartography.layout import LayoutBuilder, MapSpec
from ..engines.cartography.series import MapSeriesEngine
from ..engines.download import DownloadManager
from ..engines.package import PackageBuilder, default_destination
from ..engines.project_layers import LayerApplier
from ..engines.report import ReportEngine, ReportOptions
from ..engines.terrain import TerrainEngine
from ..tasks import run_in_background
from .maptools import MODE_CIRCLE, MODE_FREEHAND, MODE_POLYGON, MODE_RECTANGLE, AreaDrawTool
from .widgets.section import Section, caption, primary_button, separator

QUICK = "quick"
PROFESSIONAL = "professional"


class TerritorialSuiteDock(QgsDockWidget):
    """Main dock widget."""

    area_changed = pyqtSignal(object)
    report_changed = pyqtSignal(object)

    def __init__(self, iface, parent: Optional[QWidget] = None) -> None:
        super().__init__(PLUGIN_NAME, parent)
        self.setObjectName("TerritorialSuiteDock")
        self.iface = iface
        self.area: Optional[ProjectArea] = None
        self.report: Optional[AnalysisReport] = None
        self.task = None
        self._draw_tool: Optional[AreaDrawTool] = None
        self._previous_tool = None

        container = QWidget(self)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        outer.addLayout(self._build_header())
        scroll = QScrollArea(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(2)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._build_area_section()
        self._build_analyze_section()
        self._build_data_section()
        self._build_cartography_section()
        self._build_report_section()
        self.body_layout.addStretch(1)

        outer.addWidget(separator(container))
        outer.addLayout(self._build_footer())
        self.setWidget(container)

        self.restore_area()
        self._update_state()

    # ------------------------------------------------------------------ construction

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel(f"<b>{PLUGIN_NAME}</b>")
        row.addWidget(title)
        row.addStretch(1)
        self.mode_button = QToolButton()
        self.mode_button.setCheckable(True)
        self.mode_button.setChecked(settings.get("general.mode", QUICK) == PROFESSIONAL)
        self.mode_button.setText("Pro" if self.mode_button.isChecked() else "Quick")
        self.mode_button.setToolTip("Modalita' Quick (default rapidi) / Professional "
                                    "(controllo completo)")
        self.mode_button.toggled.connect(self._toggle_mode)
        row.addWidget(self.mode_button)
        for text, tooltip, slot in (("⚙", "Impostazioni", self.open_settings),
                                    ("?", "Guida", self.open_help)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.clicked.connect(slot)
            row.addWidget(button)
        return row

    def _build_area_section(self) -> None:
        self.area_section = Section("Area di progetto")
        self.area_label = caption("Nessuna area definita.", bold=True)
        self.area_section.add(self.area_label)
        self.area_details = caption("")
        self.area_section.add(self.area_details)

        row = QHBoxLayout()
        self.create_button = QToolButton()
        self.create_button.setText("Crea")
        self.create_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.create_button)
        menu.addAction("Disegna poligono", lambda: self.start_draw(MODE_POLYGON))
        menu.addAction("Rettangolo", lambda: self.start_draw(MODE_RECTANGLE))
        menu.addAction("Cerchio", lambda: self.start_draw(MODE_CIRCLE))
        menu.addAction("Mano libera", lambda: self.start_draw(MODE_FREEHAND))
        menu.addSeparator()
        menu.addAction("Dal layer attivo", lambda: self.area_from_layer(False))
        menu.addAction("Dalla selezione", lambda: self.area_from_layer(True))
        menu.addAction("Da file...", self.area_from_file)
        menu.addAction("Da coordinate / BBOX...", self.area_from_coordinates)
        menu.addSeparator()
        menu.addAction("Dalle particelle selezionate", self.area_from_parcels)
        self.create_button.setMenu(menu)
        row.addWidget(self.create_button)
        self.load_button = QPushButton("Carica")
        self.load_button.clicked.connect(self.load_area)
        row.addWidget(self.load_button)
        self.save_button = QPushButton("Salva")
        self.save_button.clicked.connect(self.save_area)
        row.addWidget(self.save_button)
        self.area_section.add_layout(row)
        self.body_layout.addWidget(self.area_section)

    def _build_analyze_section(self) -> None:
        self.analyze_section = Section("Analisi")
        self.one_click_button = primary_button("Analisi territoriale (one-click)",
                                               self.run_one_click)
        self.analyze_section.add(self.one_click_button)
        self.analyze_section.add(caption(
            "Localizzazione, catasto, vincoli, terreno e segnalazioni in un solo passaggio."))
        self.analyze_buttons = self.analyze_section.add_buttons([
            ("Vincoli", "Analizza solo vincoli e sensibilita'", self.run_constraints),
            ("Catasto", "Particelle catastali interessate", self.run_cadastre),
            ("Terreno", "DEM, quote, pendenza, esposizione", self.run_terrain),
            ("Prossimita'", "Genera le fasce di rispetto attorno all'area",
             self.run_proximity),
            ("Risultati", "Mostra il pannello dei risultati", self.show_results),
        ])
        self.body_layout.addWidget(self.analyze_section)

    def _build_data_section(self) -> None:
        self.data_section = Section("Dati", collapsed=True)
        self.data_buttons = self.data_section.add_buttons([
            ("Scarica vettoriali", "Download dei dataset per categoria", self.run_download),
            ("Sfondo / ortofoto", "Aggiungi una basemap", self.add_basemap),
            ("DEM / DTM", "Scarica il modello del terreno", self.run_terrain),
            ("Sorgenti", "Gestione e stato delle sorgenti", self.open_sources),
        ])
        self.body_layout.addWidget(self.data_section)

    def _build_cartography_section(self) -> None:
        self.map_section = Section("Cartografia")
        row = QHBoxLayout()
        self.template_combo = QComboBox()
        for template in MapSeriesEngine().available_templates():
            self.template_combo.addItem(template.get("name", template.get("id", "")),
                                        template.get("id"))
        row.addWidget(self.template_combo, 1)
        self.quick_map_button = QPushButton("Crea mappa")
        self.quick_map_button.clicked.connect(self.run_quick_map)
        row.addWidget(self.quick_map_button)
        self.map_section.add_layout(row)
        self.map_buttons = self.map_section.add_buttons([
            ("Tavola ortofoto", "Crea la tavola su base ortofotografica",
             self.run_orthophoto),
            ("Serie di tavole", "Genera l'intera serie cartografica", self.run_map_series),
            ("Esporta tavole", "Esporta i layout in PDF", self.export_layouts),
            ("Profili di layout", "Configura loghi, testi ed elementi delle tavole",
             self.open_layout_settings),
        ])
        self.body_layout.addWidget(self.map_section)

    def _build_report_section(self) -> None:
        self.report_section = Section("Report")
        self.report_buttons = self.report_section.add_buttons([
            ("Relazione", "Genera il quadro conoscitivo (PDF/HTML)", self.run_report),
            ("Esporta pacchetto", "Crea la cartella con tutti i dati", self.run_package),
            ("Impostazioni relazione", "Contenuti, loghi e testi della relazione",
             self.open_report_settings),
        ])
        self.body_layout.addWidget(self.report_section)

    def _build_footer(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        self.status_label = caption("Pronto.")
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("Annulla")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_task)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        return layout

    # ------------------------------------------------------------------ state

    @property
    def project(self) -> QgsProject:
        """The current QGIS project."""
        return QgsProject.instance()

    def restore_area(self) -> None:
        """Reload the project area stored in the QGIS project, if any."""
        try:
            area = ProjectAreaStore(self.project).current()
        except Exception as exc:  # pragma: no cover - corrupted project
            log.warning(f"Cannot restore the project area: {exc}")
            area = None
        if area is not None:
            self.set_area(area, store=False)

    def set_area(self, area: ProjectArea, *, store: bool = True,
                 add_layer: bool = True) -> None:
        """Make ``area`` the current project area."""
        self.area = area
        if store:
            ProjectAreaStore(self.project).save(area)
        if add_layer:
            try:
                LayerApplier(self.project).add_area_layer(area)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning(f"Cannot add the area layer: {exc}")
        self.report = None
        self.report_changed.emit(None)
        self.area_changed.emit(area)
        self._update_state()

    def _update_state(self) -> None:
        has_area = self.area is not None
        if has_area:
            self.area_label.setText(f"<b>{self.area.name}</b>")
            admin = self.area.admin.summary() if self.area.admin.municipalities else \
                "unita' amministrative da determinare"
            self.area_details.setText(
                f"{measure.format_area(self.area.area_m2)} &middot; "
                f"{measure.format_length(self.area.perimeter_m)} &middot; "
                f"{self.area.crs.authid()}<br>{admin}")
        else:
            self.area_label.setText("Nessuna area definita.")
            self.area_details.setText("Usa <i>Crea</i> per disegnare o importare un'area.")
        for button in ([self.one_click_button, self.save_button]
                       + self.analyze_buttons + self.data_buttons + self.map_buttons
                       + self.report_buttons + [self.quick_map_button]):
            button.setEnabled(has_area or button is self.data_buttons[-1])
        self.data_buttons[-1].setEnabled(True)   # the source manager needs no area

    def set_report(self, report: Optional[AnalysisReport]) -> None:
        """Store the last analysis result and notify the results panel."""
        self.report = report
        self.report_changed.emit(report)

    # ------------------------------------------------------------------ area creation

    def start_draw(self, mode: str) -> None:
        """Activate the canvas tool that draws the project area."""
        canvas = self.iface.mapCanvas()
        self._previous_tool = canvas.mapTool()
        self._draw_tool = AreaDrawTool(canvas, mode)
        self._draw_tool.finished.connect(self._on_drawn)
        self._draw_tool.aborted.connect(self._restore_tool)
        canvas.setMapTool(self._draw_tool)
        self.status_label.setText("Disegna l'area: click per i vertici, tasto destro per "
                                  "chiudere, Esc per annullare.")

    def _restore_tool(self) -> None:
        canvas = self.iface.mapCanvas()
        if self._previous_tool is not None:
            canvas.setMapTool(self._previous_tool)
        self._draw_tool = None
        self.status_label.setText("Pronto.")

    def _on_drawn(self, geometry) -> None:
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        self._restore_tool()
        try:
            area = ProjectArea.from_geometry(geometry, crs, name=self._next_area_name(),
                                             source_kind="draw")
        except TerritorialSuiteError as exc:
            self._error(str(exc))
            return
        self.set_area(area)

    def area_from_layer(self, selected_only: bool) -> None:
        """Build the area from the active layer (optionally from its selection)."""
        layer = self.iface.activeLayer()
        try:
            area = ProjectArea.from_layer(layer, selected_only=selected_only)
        except TerritorialSuiteError as exc:
            self._error(str(exc))
            return
        except AttributeError:
            self._error("Seleziona prima un layer vettoriale poligonale.")
            return
        self.set_area(area)

    def area_from_file(self) -> None:
        """Build the area from a vector file."""
        folder = settings.get("paths.last_area_dir", "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Apri un'area di progetto", folder,
            "Vettoriali (*.gpkg *.shp *.geojson *.json *.kml *.kmz *.tsa.json);;Tutti (*.*)")
        if not path:
            return
        settings.set_value("paths.last_area_dir", str(Path(path).parent))
        try:
            if path.endswith(".json") and ProjectArea.load_from_file(path):
                area = ProjectArea.load_from_file(path)
            else:
                area = ProjectArea.from_file(path)
        except TerritorialSuiteError as exc:
            self._error(str(exc))
            return
        self.set_area(area)

    def area_from_coordinates(self) -> None:
        """Build the area from a bounding box typed by the user."""
        from .dialogs.area_dialog import CoordinateAreaDialog

        dialog = CoordinateAreaDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        area = dialog.area()
        if area is not None:
            self.set_area(area)

    def area_from_parcels(self) -> None:
        """Build the area from the parcels currently selected in the cadastral layer."""
        layer = self.iface.activeLayer()
        if layer is None or not hasattr(layer, "selectedFeatureCount") or \
                layer.selectedFeatureCount() == 0:
            self._error("Seleziona una o piu' particelle nel layer catastale.")
            return
        try:
            area = ProjectArea.from_layer(layer, selected_only=True,
                                          name=f"Particelle ({layer.selectedFeatureCount()})")
            area.source_kind = "cadastral_parcels"
            area.parcels = self._parcel_references(layer)
        except TerritorialSuiteError as exc:
            self._error(str(exc))
            return
        self.set_area(area)

    @staticmethod
    def _parcel_references(layer) -> List[dict]:
        """Collect the cadastral identifiers of the selected features."""
        names = {field.name().lower(): field.name() for field in layer.fields()}
        keys = {key: names.get(key) for key in ("municipality", "sheet", "parcel",
                                                "national_ref")}
        references = []
        for feature in layer.selectedFeatures():
            entry = {key: str(feature[name]) for key, name in keys.items() if name}
            if entry:
                references.append(entry)
        return references

    def _next_area_name(self) -> str:
        count = len(ProjectAreaStore(self.project).list_areas()) + 1
        return f"Area di progetto {count}"

    def load_area(self) -> None:
        """Choose one of the areas stored in the project."""
        from .dialogs.area_dialog import AreaPickerDialog

        store = ProjectAreaStore(self.project)
        areas = store.list_areas()
        if not areas:
            self._error("Nessuna area salvata nel progetto. Usa <i>Crea</i> oppure "
                        "<i>Carica</i> da file.")
            return
        dialog = AreaPickerDialog(areas, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        payload = dialog.selected()
        if payload is None:
            return
        try:
            area = ProjectArea.from_dict(payload)
        except TerritorialSuiteError as exc:
            self._error(str(exc))
            return
        store.set_current(area.id)
        self.set_area(area, store=False)

    def save_area(self) -> None:
        """Save the current area to a portable file."""
        if self.area is None:
            return
        folder = settings.get("paths.last_area_dir", "") or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "Salva l'area di progetto",
            str(Path(folder) / f"{self.area.name}.tsa.json"), f"{PLUGIN_NAME} (*.json)")
        if not path:
            return
        self.area.save_to_file(path)
        settings.set_value("paths.last_area_dir", str(Path(path).parent))
        ProjectAreaStore(self.project).save(self.area)
        self.status_label.setText(f"Area salvata in {path}")

    # ------------------------------------------------------------------ analysis

    def run_one_click(self) -> None:
        """Run the full territorial analysis in the background."""
        if not self._require_area():
            return
        options = AnalysisOptions()
        if self.mode_button.isChecked():
            from .dialogs.analysis_dialog import AnalysisOptionsDialog

            dialog = AnalysisOptionsDialog(self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            options = dialog.options()
        area = self.area

        def work(feedback):
            return AnalysisOrchestrator().run(area, options, feedback=feedback)

        self._start_task("Analisi territoriale", work, self._on_analysis_done)

    def run_constraints(self) -> None:
        """Run only the constraint analysis."""
        if not self._require_area():
            return
        area = self.area
        options = AnalysisOptions(include_cadastre=False, include_terrain=False)

        def work(feedback):
            return AnalysisOrchestrator().run(area, options, feedback=feedback)

        self._start_task("Analisi dei vincoli", work, self._on_analysis_done)

    def run_cadastre(self) -> None:
        """Query the cadastral service for the current area."""
        if not self._require_area():
            return
        area = self.area

        def work(feedback):
            return CadastreEngine().run(area, feedback=feedback)

        def done(result):
            applier = LayerApplier(self.project)
            for ref in result.layers:
                applier.add_layer_ref(ref, area_id=area.id)
            report = self.report or AnalysisReport(area=area.as_dict())
            report.cadastre = result.rows
            report.results.extend(result.source_results)
            report.layers.extend(result.layers)
            self.set_report(report)
            self.show_results()
            self.status_label.setText(
                f"Catasto: {len(result.rows)} particelle" +
                (f" - {'; '.join(result.warnings)}" if result.warnings else ""))

        self._start_task("Analisi catastale", work, done)

    def run_terrain(self) -> None:
        """Download the DEM and compute the terrain statistics."""
        if not self._require_area():
            return
        area = self.area

        def work(feedback):
            return TerrainEngine().run(area, feedback=feedback,
                                       compute_contours=self.mode_button.isChecked())

        def done(outputs):
            applier = LayerApplier(self.project)
            for ref in outputs.layers:
                applier.add_layer_ref(ref, area_id=area.id)
            report = self.report or AnalysisReport(area=area.as_dict())
            report.terrain = outputs.stats
            report.layers.extend(outputs.layers)
            self.set_report(report)
            self.show_results()
            stats = outputs.stats
            self.status_label.setText(
                f"Terreno: {stats.elevation_min:.0f}-{stats.elevation_max:.0f} m"
                if stats.available else "Terreno: dati non disponibili")

        self._start_task("Analisi del terreno", work, done)

    def run_proximity(self) -> None:
        """Build the setback bands around the area and add them to the project."""
        if not self._require_area():
            return
        from qgis.PyQt.QtWidgets import QInputDialog

        from ..core import qt_compat
        from ..engines.proximity import ProximityEngine
        from ..services import vector_io

        default = ", ".join(str(value) for value in
                            settings.get("analysis.setback_bands_m", [10, 30, 150]))
        text, ok = QInputDialog.getText(self, PLUGIN_NAME,
                                        "Distanze delle fasce (metri, separate da virgola):",
                                        text=default)
        if not ok or not text.strip():
            return
        distances = []
        for token in text.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                distances.append(float(token))
            except ValueError:
                self._error(f"Distanza non valida: {token}")
                return
        settings.set_value("analysis.setback_bands_m", distances)

        from qgis.core import QgsFeature

        bands = ProximityEngine(self.area).bands(distances)
        fields = qt_compat.fields_from([
            ("distance_m", qt_compat.DOUBLE, "Distanza (m)"),
            ("area_m2", qt_compat.DOUBLE, "Superficie (m2)"),
            ("area_ha", qt_compat.DOUBLE, "Superficie (ha)")])
        layer = vector_io.memory_layer("MultiPolygon", self.area.crs,
                                       f"Fasce di rispetto - {self.area.name}", fields)
        features = []
        for band in bands:
            feature = QgsFeature(fields)
            feature.setGeometry(band.geometry)
            feature.setAttributes([band.distance_m, round(band.area_m2, 2),
                                   round(band.area_m2 / 10_000.0, 4)])
            features.append(feature)
        layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        layer.setCustomProperty("territorial_suite/category", "legal_administrative")
        layer.setCustomProperty("territorial_suite/area_id", self.area.id)
        self.project.addMapLayer(layer, False)
        LayerApplier(self.project).group("Analisi").addLayer(layer)
        self.status_label.setText(
            "Fasce create: " + ", ".join(band.label for band in bands))

    def _on_analysis_done(self, report: AnalysisReport) -> None:
        """Apply an analysis result to the project."""
        self.set_report(report)
        applier = LayerApplier(self.project)
        try:
            applier.apply_report(self.area, report)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Cannot apply the analysis layers", exc)
        self.show_results()
        self.status_label.setText(
            f"Analisi completata: {len(report.present_results)} dati presenti, "
            f"{len(report.alerts)} segnalazioni, {len(report.sources_failed)} fonti non "
            f"disponibili.")
        self.iface.messageBar().pushSuccess(PLUGIN_NAME, "Analisi territoriale completata.")

    # ------------------------------------------------------------------ data

    def run_download(self) -> None:
        """Download the vector datasets of the chosen categories."""
        if not self._require_area():
            return
        from .dialogs.download_dialog import DownloadDialog

        dialog = DownloadDialog(self.area, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        categories = dialog.categories()
        if not categories:
            return
        area = self.area

        def work(feedback):
            return DownloadManager().download(area, categories, feedback=feedback)

        def done(outcome):
            applier = LayerApplier(self.project)
            for ref in outcome.layers:
                applier.add_layer_ref(ref, area_id=area.id)
            report = self.report or AnalysisReport(area=area.as_dict())
            report.downloads.extend(outcome.results)
            report.layers.extend(outcome.layers)
            self.set_report(report)
            self.status_label.setText(
                f"Scaricati {outcome.feature_total} elementi in {len(outcome.layers)} layer.")

        self._start_task("Download dati d'area", work, done)

    def add_basemap(self) -> None:
        """Add a background service to the project."""
        from .dialogs.basemap_dialog import BasemapDialog

        dialog = BasemapDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        source_id = dialog.source_id()
        if not source_id:
            return
        layer = LayerApplier(self.project).add_basemap(source_id)
        if layer is None:
            self._error("Servizio di sfondo non disponibile.")
        else:
            self.status_label.setText(f"Sfondo aggiunto: {layer.name()}")

    def open_sources(self) -> None:
        """Open the source manager."""
        from .dialogs.sources_dialog import SourcesDialog

        SourcesDialog(self).exec()

    # ------------------------------------------------------------------ cartography

    def run_quick_map(self) -> None:
        """Build one layout from the selected template."""
        if not self._require_area():
            return
        template_id = self.template_combo.currentData() or "territorial_overview"
        spec = MapSpec(template=template_id)
        if self.mode_button.isChecked():
            from .dialogs.map_dialog import MapDialog

            dialog = MapDialog(template_id, self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            spec = dialog.spec()
        try:
            layout = LayoutBuilder(self.project).build(self.area, spec, report=self.report)
        except Exception as exc:
            self._error(f"Impossibile creare il layout: {exc}")
            return
        self.status_label.setText(f"Layout creato: {layout.name()}")
        self.iface.openLayoutDesigner(layout)

    def run_orthophoto(self) -> None:
        """One-click orthophoto sheet, with the provider configured by the user.

        The two halves run where they are allowed to: the worker only talks to the
        network and returns a plain plan, and every QGIS object — the raster layer, the
        layer tree entry, the layout — is created here, in the main thread.
        """
        if not self._require_area():
            return
        from ..core.errors import LayoutError
        from ..engines.cartography.orthophoto import OrthophotoEngine

        area, report, project = self.area, self.report, self.project
        engine = OrthophotoEngine(project)

        def work(feedback):
            # Worker thread: network only. Creating a QgsRasterLayer or a
            # QgsPrintLayout here would crash QGIS.
            return engine.prepare(area, feedback=feedback)

        def done(plan):
            if not plan.ok:
                self._error(f"Ortofoto non disponibile: {plan.reason}")
                return
            try:
                sheet = engine.compose(area, plan, report=report)
            except LayoutError as exc:
                self._error(str(exc))
                return
            self.status_label.setText(
                f"Tavola ortofoto creata ({sheet.choice.name})."
                + (" Sorgente di ripiego: vedi le avvertenze sulla tavola."
                   if sheet.choice.fallback_used else ""))
            self.iface.openLayoutDesigner(sheet.layout)

        self._start_task("Tavola ortofoto", work, done)

    def open_layout_settings(self) -> None:
        """Open the settings dialog on the layout profiles."""
        self._open_settings_tab("Layout e relazioni")

    def open_report_settings(self) -> None:
        """Open the settings dialog on the report options."""
        self._open_settings_tab("Report")

    def _open_settings_tab(self, title: str) -> None:
        from .dialogs.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self)
        for index in range(dialog.tabs.count()):
            if dialog.tabs.tabText(index) == title:
                dialog.tabs.setCurrentIndex(index)
                break
        dialog.exec()

    def run_map_series(self) -> None:
        """Generate the whole map series for the area."""
        if not self._require_area():
            return
        area, report = self.area, self.report
        project = self.project

        def work(feedback):
            return MapSeriesEngine(project).generate(area, report=report, feedback=feedback)

        def done(result):
            self.status_label.setText(
                f"Serie generata: {result.count} tavole"
                + (f" (saltate: {', '.join(result.skipped)})" if result.skipped else ""))
            if result.layouts:
                self.iface.openLayoutDesigner(result.layouts[0])

        self._start_task("Serie cartografica", work, done)

    def export_layouts(self) -> None:
        """Export every layout of the project to a folder."""
        from ..engines.cartography.export import ExportCenter

        layouts = list(self.project.layoutManager().printLayouts())
        if not layouts:
            self._error("Nessun layout da esportare: crea prima una mappa.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Cartella di esportazione",
                                                  str(default_destination()))
        if not folder:
            return
        results = ExportCenter.export_series(layouts, Path(folder), "pdf")
        ok = sum(1 for item in results if item.ok)
        settings.set_value("paths.last_output_dir", folder)
        self.status_label.setText(f"Esportate {ok}/{len(results)} tavole in {folder}")

    # ------------------------------------------------------------------ report

    def run_report(self) -> None:
        """Generate the territorial dossier."""
        if not self._require_area() or not self._require_report():
            return
        folder = settings.get("paths.last_output_dir", "") or str(default_destination())
        path, _ = QFileDialog.getSaveFileName(
            self, "Salva la relazione",
            str(Path(folder) / f"{self.area.name}_quadro_conoscitivo.pdf"),
            "PDF (*.pdf);;HTML (*.html)")
        if not path:
            return
        settings.set_value("paths.last_output_dir", str(Path(path).parent))
        engine = ReportEngine(self.report, ReportOptions.from_settings())
        try:
            if path.lower().endswith(".html"):
                engine.write_html(Path(path))
            else:
                engine.write_pdf(Path(path))
                engine.write_xlsx(Path(path).with_suffix(".xlsx"))
        except Exception as exc:
            self._error(f"Relazione non generata: {exc}")
            return
        self.status_label.setText(f"Relazione salvata in {path}")

    def run_package(self) -> None:
        """Export the whole data package."""
        if not self._require_area() or not self._require_report():
            return
        folder = QFileDialog.getExistingDirectory(self, "Dove creare il pacchetto",
                                                  str(default_destination()))
        if not folder:
            return
        settings.set_value("paths.last_output_dir", folder)
        area, report = self.area, self.report
        layouts = list(self.project.layoutManager().printLayouts())

        def work(feedback):
            return PackageBuilder(area, report).build(Path(folder), layouts=layouts,
                                                      feedback=feedback)

        def done(result):
            self.status_label.setText(
                f"Pacchetto creato: {len(result.files)} file in {result.root}")
            QMessageBox.information(self, PLUGIN_NAME,
                                    f"Pacchetto creato in\n{result.root}")

        self._start_task("Esportazione pacchetto", work, done)

    def show_results(self) -> None:
        """Ask the plugin to show the results panel."""
        self.report_changed.emit(self.report)

    # ------------------------------------------------------------------ tasks

    def _start_task(self, description: str, work, on_success) -> None:
        """Start a background task, wiring progress, messages and errors."""
        if self.task is not None:
            self._error("Un'operazione e' gia' in corso.")
            return
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.status_label.setText(f"{description} in corso...")

        def success(result):
            self._finish_task()
            on_success(result)

        def failure(error):
            self._finish_task()
            # The message bar already shows the plugin name as the title, so repeating
            # the description when it *is* the plugin name gave «Analisi territoriale:
            # Analisi territoriale: ...». And a raw exception is not a sentence.
            reason = describe_error(error)
            self._error(reason if description == PLUGIN_NAME
                        else f"{description}: {reason}")

        def cancelled():
            self._finish_task()
            self.status_label.setText(f"{description}: operazione annullata.")

        self.task = run_in_background(
            f"{PLUGIN_NAME}: {description}", work,
            on_success=success, on_error=failure, on_cancel=cancelled,
            on_message=lambda text, level: self._on_message(text, level),
            on_step=lambda text: self.status_label.setText(text),
            on_progress=lambda value: self.progress.setValue(int(value)))

    def _finish_task(self) -> None:
        self.task = None
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)

    def cancel_task(self) -> None:
        """Cancel the running task."""
        if self.task is not None:
            self.task.cancel()
            self.status_label.setText("Annullamento in corso...")

    def _on_message(self, text: str, level: str) -> None:
        if level in ("WARNING", "ERROR"):
            log.warning(text)
        if level == "ERROR":
            self.iface.messageBar().pushWarning(PLUGIN_NAME, text[:200])

    # ------------------------------------------------------------------ helpers

    def _require_area(self) -> bool:
        if self.area is None:
            self._error("Definisci prima un'area di progetto.")
            return False
        return True

    def _require_report(self) -> bool:
        if self.report is None:
            self._error("Esegui prima un'analisi territoriale.")
            return False
        return True

    def _error(self, message: str) -> None:
        self.status_label.setText(message)
        self.iface.messageBar().pushWarning(PLUGIN_NAME, message.replace("<i>", "")
                                            .replace("</i>", ""))

    def _toggle_mode(self, professional: bool) -> None:
        self.mode_button.setText("Pro" if professional else "Quick")
        settings.set_value("general.mode", PROFESSIONAL if professional else QUICK)

    def open_settings(self) -> None:
        """Open the settings dialog."""
        from .dialogs.settings_dialog import SettingsDialog

        SettingsDialog(self).exec()

    def open_help(self) -> None:
        """Open the plugin documentation."""
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        from ..core.paths import plugin_dir

        docs = plugin_dir().parent / "docs" / "getting_started.md"
        if docs.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(docs)))
        else:  # pragma: no cover - packaged install
            QMessageBox.information(self, PLUGIN_NAME,
                                    "La documentazione si trova nella cartella docs/ "
                                    "del plugin.")
