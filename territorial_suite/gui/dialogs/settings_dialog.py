"""Settings dialog: network, cache, analysis, terrain, cartography, report."""

from __future__ import annotations

from typing import Optional

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import settings
from ...core.cache import CacheManager
from ...core.constants import PLUGIN_NAME
from ...core.paths import cache_dir, user_dir
from .layout_settings import LayoutSettingsWidget


class SettingsDialog(QDialog):
    """Edits the plugin settings stored in QgsSettings."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Impostazioni")
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._network_tab(), "Rete")
        self.tabs.addTab(self._cache_tab(), "Cache")
        self.tabs.addTab(self._analysis_tab(), "Analisi")
        self.tabs.addTab(self._cartography_tab(), "Cartografia")
        self.layout_settings = LayoutSettingsWidget(self)
        self.tabs.addTab(self.layout_settings, "Layout e relazioni")
        self.tabs.addTab(self._report_tab(), "Report")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ tabs

    def _network_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 600)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(int(settings.get("network.timeout_s", 30)))
        form.addRow("Timeout richieste", self.timeout)
        self.retries = QSpinBox()
        self.retries.setRange(1, 10)
        self.retries.setValue(int(settings.get("network.retries", 3)))
        form.addRow("Tentativi", self.retries)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.0, 30.0)
        self.interval.setSuffix(" s")
        self.interval.setValue(float(settings.get("network.min_interval_s", 0.0)))
        form.addRow("Intervallo minimo per host", self.interval)
        self.max_features = QSpinBox()
        self.max_features.setRange(100, 1_000_000)
        self.max_features.setSingleStep(1000)
        self.max_features.setValue(int(settings.get("network.max_features_per_source",
                                                    20000)))
        form.addRow("Limite elementi per sorgente", self.max_features)
        self.offline = QCheckBox("Modalita' offline (usa solo la cache)")
        self.offline.setChecked(bool(settings.get("network.offline", False)))
        form.addRow("", self.offline)
        self.auth_id = QLineEdit(settings.get("network.auth_config_id", ""))
        self.auth_id.setPlaceholderText("id configurazione di autenticazione QGIS")
        form.addRow("Autenticazione", self.auth_id)
        form.addRow(QLabel("<i>Le credenziali sono gestite dal sistema di autenticazione "
                           "di QGIS: il plugin non memorizza password.</i>"))
        return widget

    def _cache_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        self.cache_enabled = QCheckBox("Abilita la cache")
        self.cache_enabled.setChecked(bool(settings.get("cache.enabled", True)))
        form.addRow("", self.cache_enabled)
        self.quota = QDoubleSpinBox()
        self.quota.setRange(16.0, 100000.0)
        self.quota.setSuffix(" MB")
        self.quota.setValue(float(settings.get("cache.quota_mb", 2048)))
        form.addRow("Dimensione massima", self.quota)
        self.ttl_vector = QSpinBox()
        self.ttl_vector.setRange(1, 8760)
        self.ttl_vector.setSuffix(" h")
        self.ttl_vector.setValue(int(settings.get("cache.ttl_hours.vector", 24)))
        form.addRow("Validita' dati vettoriali", self.ttl_vector)
        self.ttl_raster = QSpinBox()
        self.ttl_raster.setRange(1, 8760)
        self.ttl_raster.setSuffix(" h")
        self.ttl_raster.setValue(int(settings.get("cache.ttl_hours.raster", 720)))
        form.addRow("Validita' raster", self.ttl_raster)
        layout.addLayout(form)

        cache = CacheManager.instance()
        stats = cache.stats()
        self.cache_label = QLabel(
            f"Cache in uso: {stats['total_bytes'] / 1024 / 1024:.1f} MB<br>"
            f"Percorso: {cache_dir()}<br>Profilo plugin: {user_dir()}")
        self.cache_label.setWordWrap(True)
        layout.addWidget(self.cache_label)
        row = QHBoxLayout()
        purge = QPushButton("Rimuovi elementi scaduti")
        purge.clicked.connect(self._purge_cache)
        row.addWidget(purge)
        clear = QPushButton("Svuota la cache")
        clear.clicked.connect(self._clear_cache)
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return widget

    def _analysis_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.buffer = QDoubleSpinBox()
        self.buffer.setRange(0.0, 20000.0)
        self.buffer.setSuffix(" m")
        self.buffer.setValue(float(settings.get("analysis.context_buffer_m", 500)))
        form.addRow("Buffer di contesto", self.buffer)
        self.max_distance = QDoubleSpinBox()
        self.max_distance.setRange(0.0, 50000.0)
        self.max_distance.setSuffix(" m")
        self.max_distance.setValue(float(settings.get("analysis.max_distance_m", 5000)))
        form.addRow("Raggio per le distanze", self.max_distance)
        self.cell_size = QDoubleSpinBox()
        self.cell_size.setRange(1.0, 200.0)
        self.cell_size.setSuffix(" m")
        self.cell_size.setValue(float(settings.get("terrain.cell_size_m", 10)))
        form.addRow("Risoluzione DEM", self.cell_size)
        self.slope_classes = QLineEdit(
            ", ".join(str(value) for value in
                      settings.get("terrain.slope_classes_percent", [5, 10, 20, 30, 50])))
        form.addRow("Classi di pendenza (%)", self.slope_classes)
        self.contour = QDoubleSpinBox()
        self.contour.setRange(0.5, 500.0)
        self.contour.setSuffix(" m")
        self.contour.setValue(float(settings.get("terrain.contour_interval_m", 25)))
        form.addRow("Equidistanza curve di livello", self.contour)
        self.work_crs_mode = QLineEdit(settings.get("general.work_crs_mode", "auto_utm"))
        form.addRow("CRS di lavoro (auto_utm/project/explicit)", self.work_crs_mode)
        self.work_crs = QLineEdit(settings.get("general.work_crs", ""))
        self.work_crs.setPlaceholderText("es. EPSG:32632 (solo per 'explicit')")
        form.addRow("CRS esplicito", self.work_crs)
        return widget

    def _cartography_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.page_size = QLineEdit(settings.get("cartography.page_size", "A3"))
        form.addRow("Formato predefinito", self.page_size)
        self.orientation = QLineEdit(settings.get("cartography.orientation", "landscape"))
        form.addRow("Orientamento", self.orientation)
        self.scales = QLineEdit(", ".join(
            str(value) for value in settings.get("cartography.scales", [])))
        form.addRow("Scale disponibili", self.scales)
        self.legend_max = QSpinBox()
        self.legend_max.setRange(1, 60)
        self.legend_max.setValue(int(settings.get("cartography.legend_max_layers", 12)))
        form.addRow("Massimo layer in legenda", self.legend_max)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 1200)
        self.dpi.setValue(int(settings.get("cartography.dpi", 300)))
        form.addRow("Risoluzione export", self.dpi)
        self.logo = QLineEdit(settings.get("cartography.logo_path", ""))
        self.logo.setPlaceholderText("percorso del logo (SVG/PNG)")
        form.addRow("Logo", self.logo)
        return widget

    def _report_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.author = QLineEdit(settings.get("report.author", ""))
        form.addRow("Autore", self.author)
        self.organisation = QLineEdit(settings.get("report.organisation", ""))
        form.addRow("Organizzazione", self.organisation)
        self.include_cadastre = QCheckBox("Includi la sezione catastale")
        self.include_cadastre.setChecked(bool(settings.get("report.include_cadastre", True)))
        form.addRow("", self.include_cadastre)
        self.include_terrain = QCheckBox("Includi i dati altimetrici")
        self.include_terrain.setChecked(bool(settings.get("report.include_terrain", True)))
        form.addRow("", self.include_terrain)
        self.include_sources = QCheckBox("Includi l'elenco delle fonti")
        self.include_sources.setChecked(bool(settings.get("report.include_sources", True)))
        form.addRow("", self.include_sources)
        self.include_heritage = QCheckBox(
            "Includi la sezione sul patrimonio culturale e paesaggistico")
        self.include_heritage.setChecked(
            bool(settings.get("report.include_cultural_heritage", True)))
        form.addRow("", self.include_heritage)
        self.max_heritage_rows = QSpinBox()
        self.max_heritage_rows.setRange(5, 500)
        self.max_heritage_rows.setValue(int(settings.get("report.max_heritage_rows", 40)))
        form.addRow("Righe per tema nel dossier", self.max_heritage_rows)
        return widget

    # ------------------------------------------------------------------ actions

    def _purge_cache(self) -> None:
        removed = CacheManager.instance().purge_expired()
        QMessageBox.information(self, PLUGIN_NAME, f"Rimossi {removed} elementi scaduti.")

    def _clear_cache(self) -> None:
        answer = QMessageBox.question(
            self, PLUGIN_NAME,
            "Svuotare completamente la cache? I dati saranno riscaricati alla prossima "
            "analisi.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = CacheManager.instance().clear()
        QMessageBox.information(self, PLUGIN_NAME, f"Rimossi {removed} elementi.")

    @staticmethod
    def _numbers(text: str) -> list:
        values = []
        for token in text.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                values.append(float(token) if "." in token else int(token))
            except ValueError:
                continue
        return values

    def _save(self) -> None:
        settings.set_value("network.timeout_s", self.timeout.value())
        settings.set_value("network.retries", self.retries.value())
        settings.set_value("network.min_interval_s", self.interval.value())
        settings.set_value("network.max_features_per_source", self.max_features.value())
        settings.set_value("network.offline", self.offline.isChecked())
        settings.set_value("network.auth_config_id", self.auth_id.text().strip())

        settings.set_value("cache.enabled", self.cache_enabled.isChecked())
        settings.set_value("cache.quota_mb", self.quota.value())
        settings.set_value("cache.ttl_hours.vector", self.ttl_vector.value())
        settings.set_value("cache.ttl_hours.raster", self.ttl_raster.value())

        settings.set_value("analysis.context_buffer_m", self.buffer.value())
        settings.set_value("analysis.max_distance_m", self.max_distance.value())
        settings.set_value("terrain.cell_size_m", self.cell_size.value())
        settings.set_value("terrain.slope_classes_percent",
                           self._numbers(self.slope_classes.text()))
        settings.set_value("terrain.contour_interval_m", self.contour.value())
        settings.set_value("general.work_crs_mode", self.work_crs_mode.text().strip())
        settings.set_value("general.work_crs", self.work_crs.text().strip())

        settings.set_value("cartography.page_size", self.page_size.text().strip())
        settings.set_value("cartography.orientation", self.orientation.text().strip())
        settings.set_value("cartography.scales", self._numbers(self.scales.text()))
        settings.set_value("cartography.legend_max_layers", self.legend_max.value())
        settings.set_value("cartography.dpi", self.dpi.value())
        settings.set_value("cartography.logo_path", self.logo.text().strip())

        settings.set_value("report.author", self.author.text().strip())
        settings.set_value("report.organisation", self.organisation.text().strip())
        settings.set_value("report.include_cadastre", self.include_cadastre.isChecked())
        settings.set_value("report.include_terrain", self.include_terrain.isChecked())
        settings.set_value("report.include_sources", self.include_sources.isChecked())
        settings.set_value("report.include_cultural_heritage",
                           self.include_heritage.isChecked())
        settings.set_value("report.max_heritage_rows", self.max_heritage_rows.value())
        self.layout_settings.save()
        self.accept()

    def _restore_defaults(self) -> None:
        answer = QMessageBox.question(self, PLUGIN_NAME,
                                      "Ripristinare tutte le impostazioni predefinite?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        settings.reset()
        QMessageBox.information(self, PLUGIN_NAME,
                                "Impostazioni ripristinate. Riapri la finestra per vederle.")
        self.accept()
