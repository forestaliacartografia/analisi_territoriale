"""Layout & report settings: the sheet's graphic identity, edited without code.

The widget is intentionally thin. It reads and writes
:class:`~territorial_suite.engines.cartography.profiles.LayoutProfile` objects and the
report options; everything it decides is stored as configuration, so a profile built here
behaves exactly like one shipped with the plugin or written by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import credentials, log, settings
from ...core.constants import PLUGIN_NAME
from ...engines.cartography.imagery import AUTOMATIC, PREFERENCE_SETTING
from ...engines.cartography.overview import OverviewSpec
from ...engines.cartography.profiles import (
    DEFAULT_PROFILE_SETTING,
    SUPPORTED_IMAGES,
    ImageSpec,
    LayoutProfile,
    ProfileStore,
)

#: Fixed texts offered in the editor, with their Italian labels.
TEXT_FIELDS = (
    ("client", "Committente"),
    ("project", "Progetto"),
    ("locality", "Localita'"),
    ("sheet_code", "Codice elaborato"),
    ("revision", "Revisione"),
    ("header", "Intestazione"),
    ("footer", "Pie' di pagina"),
    ("method_notes", "Note metodologiche"),
    ("legal_notes", "Note legali"),
)

LEGEND_MODES = (("auto", "Legenda automatica"), ("thematic", "Legenda tematica"),
                ("custom", "Legenda personalizzata"), ("none", "Nessuna legenda"))


class LayoutSettingsWidget(QWidget):
    """Editor of layout profiles plus the report and imagery options."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = ProfileStore.instance()
        self.profile: Optional[LayoutProfile] = None
        self._loading = False

        layout = QVBoxLayout(self)
        chooser = QHBoxLayout()
        chooser.addWidget(QLabel("Profilo"))
        self.profile_box = QComboBox()
        self.profile_box.currentIndexChanged.connect(self._profile_changed)
        chooser.addWidget(self.profile_box, 1)
        for text, slot in (("Duplica", self.duplicate_profile),
                           ("Elimina", self.delete_profile),
                           ("Esporta", self.export_profile),
                           ("Importa", self.import_profile)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            chooser.addWidget(button)
        layout.addLayout(chooser)

        self.builtin_note = QLabel("")
        self.builtin_note.setWordWrap(True)
        layout.addWidget(self.builtin_note)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._page_tab(), "Pagina")
        self.tabs.addTab(self._elements_tab(), "Elementi")
        self.tabs.addTab(self._images_tab(), "Loghi e immagini")
        self.tabs.addTab(self._texts_tab(), "Testi")
        self.tabs.addTab(self._imagery_tab(), "Ortofoto")
        layout.addWidget(self.tabs, 1)

        self._reload_profiles()

    # ------------------------------------------------------------------ tabs

    def _page_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.page_size = QComboBox()
        self.page_size.addItems(["A4", "A3", "A2", "A1", "A0"])
        self.page_size.setEditable(True)
        form.addRow("Formato", self.page_size)
        self.orientation = QComboBox()
        self.orientation.addItems(["landscape", "portrait"])
        form.addRow("Orientamento", self.orientation)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0.0, 60.0)
        self.margin.setSuffix(" mm")
        form.addRow("Margine", self.margin)
        self.title_size = QDoubleSpinBox()
        self.title_size.setRange(5.0, 40.0)
        form.addRow("Corpo del titolo", self.title_size)
        self.body_size = QDoubleSpinBox()
        self.body_size.setRange(4.0, 20.0)
        form.addRow("Corpo del testo", self.body_size)
        self.style_preset = QLineEdit()
        form.addRow("Preset di stile", self.style_preset)
        return widget

    def _elements_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.legend_mode = QComboBox()
        for value, label in LEGEND_MODES:
            self.legend_mode.addItem(label, value)
        form.addRow("Legenda", self.legend_mode)
        self.legend_title = QLineEdit()
        form.addRow("Titolo legenda", self.legend_title)
        self.legend_columns = QSpinBox()
        self.legend_columns.setRange(1, 4)
        form.addRow("Colonne legenda", self.legend_columns)
        self.legend_max = QSpinBox()
        self.legend_max.setRange(0, 80)
        self.legend_max.setSpecialValueText("nessun limite")
        form.addRow("Voci massime", self.legend_max)
        self.legend_exclude = QLineEdit()
        self.legend_exclude.setPlaceholderText("nomi da escludere, separati da virgola")
        form.addRow("Escludi dalla legenda", self.legend_exclude)

        arrow = QGroupBox("Rosa dei venti")
        arrow_form = QFormLayout(arrow)
        self.arrow_enabled = QCheckBox("Inserisci la freccia del nord")
        arrow_form.addRow("", self.arrow_enabled)
        self.arrow_model = QLineEdit()
        self.arrow_model.setPlaceholderText("NorthArrow_02")
        arrow_form.addRow("Modello", self.arrow_model)
        self.arrow_size = QDoubleSpinBox()
        self.arrow_size.setRange(5.0, 80.0)
        self.arrow_size.setSuffix(" mm")
        arrow_form.addRow("Dimensione", self.arrow_size)
        self.arrow_rotation = QDoubleSpinBox()
        self.arrow_rotation.setRange(-360.0, 360.0)
        self.arrow_rotation.setSuffix(" gradi")
        arrow_form.addRow("Rotazione", self.arrow_rotation)
        self.arrow_follow = QCheckBox("Segui la rotazione della mappa")
        arrow_form.addRow("", self.arrow_follow)
        form.addRow(arrow)

        self.scale_bar = QCheckBox("Barra di scala")
        form.addRow("", self.scale_bar)
        self.numeric_scale = QCheckBox("Scala numerica")
        form.addRow("", self.numeric_scale)
        self.grid = QCheckBox("Reticolo")
        form.addRow("", self.grid)
        self.coordinate_frame = QCheckBox("Coordinate ai margini")
        form.addRow("", self.coordinate_frame)
        self.attribution = QCheckBox("Attribuzione delle fonti immagine")
        form.addRow("", self.attribution)

        locator = QGroupBox("Riquadro di localizzazione")
        locator_form = QFormLayout(locator)
        self.overview_enabled = QCheckBox("Inserisci il riquadro di localizzazione")
        locator_form.addRow("", self.overview_enabled)
        self.overview_zoom = QDoubleSpinBox()
        self.overview_zoom.setRange(1.5, 200.0)
        self.overview_zoom.setSuffix(" x")
        self.overview_zoom.setToolTip(
            "Quanto il riquadro si allarga attorno all'area di progetto.")
        locator_form.addRow("Fattore di zoom", self.overview_zoom)
        self.overview_anchor = QComboBox()
        self.overview_anchor.addItem("Nel riquadro informativo", "panel")
        self.overview_anchor.addItem("Su un angolo della mappa", "map")
        locator_form.addRow("Posizione", self.overview_anchor)
        self.overview_corner = QComboBox()
        for value, label in (("top_left", "in alto a sinistra"),
                             ("top_right", "in alto a destra"),
                             ("bottom_left", "in basso a sinistra"),
                             ("bottom_right", "in basso a destra")):
            self.overview_corner.addItem(label, value)
        locator_form.addRow("Angolo", self.overview_corner)
        self.overview_frame_color = QLineEdit()
        self.overview_frame_color.setPlaceholderText("#d32f2f")
        locator_form.addRow("Colore della cornice", self.overview_frame_color)
        form.addRow(locator)
        return widget

    def _images_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Loghi (stampati in riga nel blocco 'logos')"))
        self.logo_list = QListWidget()
        layout.addWidget(self.logo_list, 1)
        layout.addLayout(self._image_buttons(self.logo_list, "logos"))
        layout.addWidget(QLabel("Immagini aggiuntive (blocco 'images', con didascalia)"))
        self.image_list = QListWidget()
        layout.addWidget(self.image_list, 1)
        layout.addLayout(self._image_buttons(self.image_list, "images"))
        self.image_note = QLabel(
            f"Formati supportati: {', '.join(SUPPORTED_IMAGES)}. "
            f"Un'immagine mancante viene segnalata sulla tavola, non ignorata.")
        self.image_note.setWordWrap(True)
        layout.addWidget(self.image_note)
        return widget

    def _image_buttons(self, target: QListWidget, kind: str) -> QHBoxLayout:
        row = QHBoxLayout()
        add = QPushButton("Aggiungi...")
        add.clicked.connect(lambda: self._add_image(target, kind))
        remove = QPushButton("Rimuovi")
        remove.clicked.connect(lambda: self._remove_image(target, kind))
        caption = QPushButton("Didascalia...")
        caption.clicked.connect(lambda: self._edit_caption(target, kind))
        for button in (add, remove, caption):
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _texts_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.text_edits = {}
        for key, label in TEXT_FIELDS:
            if key in ("legal_notes", "method_notes", "header", "footer"):
                edit = QPlainTextEdit()
                edit.setMaximumHeight(60)
            else:
                edit = QLineEdit()
            self.text_edits[key] = edit
            form.addRow(label, edit)
        hint = QLabel("Nei testi si possono usare i segnaposto {client}, {project}, "
                      "{municipality}, {province}, {region}, {date}, {sheet_number}, "
                      "{revision}, {scale}, {crs}.")
        hint.setWordWrap(True)
        form.addRow(hint)
        return widget

    def _imagery_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.imagery_provider = QComboBox()
        self.imagery_provider.addItem("Automatica (migliore disponibile)", AUTOMATIC)
        self.imagery_provider.addItem("Esri World Imagery", "esri")
        self.imagery_provider.addItem("Google (Map Tiles API)", "google")
        current = str(settings.get(PREFERENCE_SETTING, AUTOMATIC))
        index = self.imagery_provider.findData(current)
        self.imagery_provider.setCurrentIndex(max(index, 0))
        form.addRow("Ortofoto preferita", self.imagery_provider)

        self.probe_imagery = QCheckBox(
            "Verifica una tile prima di stampare (evita tavole bianche)")
        self.probe_imagery.setChecked(
            bool(settings.get("cartography.probe_imagery", True)))
        form.addRow("", self.probe_imagery)

        self.google_status = QLabel("")
        self.google_status.setWordWrap(True)
        form.addRow("Chiave Google", self.google_status)
        buttons = QHBoxLayout()
        set_key = QPushButton("Imposta chiave...")
        set_key.clicked.connect(self._set_google_key)
        clear_key = QPushButton("Rimuovi chiave")
        clear_key.clicked.connect(self._clear_google_key)
        buttons.addWidget(set_key)
        buttons.addWidget(clear_key)
        buttons.addStretch(1)
        form.addRow("", buttons)

        note = QLabel(
            "Google Satellite viene usato solo attraverso la Map Tiles API ufficiale, "
            "con la chiave dell'utente, e mai prelevando tile dagli indirizzi interni "
            "del sito Google Maps. La chiave e' conservata nel database di "
            "autenticazione cifrato di QGIS, mai in chiaro nelle impostazioni. "
            "Le tile non vengono mai stampate senza la stringa di attribuzione "
            "restituita da Google: se manca, il plugin ripiega su un'altra ortofoto e "
            "lo scrive sulla tavola.")
        note.setWordWrap(True)
        form.addRow(note)
        self._refresh_google_status()
        return widget

    # ------------------------------------------------------------------ profile I/O

    def _reload_profiles(self, select: str = "") -> None:
        self._loading = True
        self.profile_box.clear()
        for profile in self.store.all():
            suffix = "" if profile.builtin else " (personale)"
            self.profile_box.addItem(f"{profile.name}{suffix}", profile.id)
        wanted = select or str(settings.get(DEFAULT_PROFILE_SETTING, "standard"))
        index = self.profile_box.findData(wanted)
        self.profile_box.setCurrentIndex(max(index, 0))
        self._loading = False
        self._profile_changed()

    def _profile_changed(self) -> None:
        if self._loading:
            return
        profile_id = self.profile_box.currentData()
        if not profile_id:
            return
        self.profile = self.store.resolve(str(profile_id))
        self.builtin_note.setText(
            f"{self.profile.description}"
            + ("  Questo profilo e' fornito con il plugin: salvando le modifiche viene "
               "creata una copia personale con lo stesso nome."
               if self.profile.builtin else ""))
        self._load_profile(self.profile)

    def _load_profile(self, profile: LayoutProfile) -> None:
        self.page_size.setCurrentText(profile.page_size)
        self.orientation.setCurrentText(profile.orientation)
        self.margin.setValue(profile.margin_mm)
        self.title_size.setValue(profile.title_size)
        self.body_size.setValue(profile.body_size)
        self.style_preset.setText(profile.style_preset)

        index = self.legend_mode.findData(profile.legend.mode)
        self.legend_mode.setCurrentIndex(max(index, 0))
        self.legend_title.setText(profile.legend.title)
        self.legend_columns.setValue(profile.legend.columns)
        self.legend_max.setValue(profile.legend.max_entries)
        self.legend_exclude.setText(", ".join(profile.legend.exclude))

        self.arrow_enabled.setChecked(profile.north_arrow.enabled)
        self.arrow_model.setText(profile.north_arrow.model)
        self.arrow_size.setValue(profile.north_arrow.size_mm)
        self.arrow_rotation.setValue(profile.north_arrow.rotation)
        self.arrow_follow.setChecked(profile.north_arrow.follow_map)

        self.scale_bar.setChecked(profile.scale_bar)
        self.numeric_scale.setChecked(profile.numeric_scale)
        self.grid.setChecked(profile.grid)
        self.coordinate_frame.setChecked(profile.coordinate_frame)
        self.attribution.setChecked(profile.attribution)

        overview = OverviewSpec.from_settings().merged_with(profile.overview)
        self.overview_enabled.setChecked(overview.enabled)
        self.overview_zoom.setValue(overview.zoom_factor)
        self.overview_anchor.setCurrentIndex(
            max(self.overview_anchor.findData(overview.anchor), 0))
        self.overview_corner.setCurrentIndex(
            max(self.overview_corner.findData(overview.corner), 0))
        self.overview_frame_color.setText(overview.frame_color)

        self._fill_images(self.logo_list, profile.logos)
        self._fill_images(self.image_list, profile.images)

        for key, edit in self.text_edits.items():
            value = profile.text(key, "")
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(value)
            else:
                edit.setText(value)

    @staticmethod
    def _fill_images(widget: QListWidget, images) -> None:
        widget.clear()
        for spec in images:
            label = spec.path or "(vuoto)"
            if spec.caption:
                label = f"{label}  -  {spec.caption}"
            if not spec.usable:
                label = f"{label}   [{spec.why_unusable()}]"
            item = QListWidgetItem(label)
            item.setData(0x0100, spec.as_dict())      # Qt.UserRole
            widget.addItem(item)

    def collect(self) -> LayoutProfile:
        """Return the profile as currently edited."""
        profile = LayoutProfile.from_dict((self.profile or LayoutProfile()).as_dict(),
                                          builtin=False)
        # ``as_dict`` carries ``builtin`` and it wins over the parameter: an edited
        # profile is always a personal one, whatever it was copied from.
        profile.builtin = False
        profile.page_size = self.page_size.currentText().strip() or "A3"
        profile.orientation = self.orientation.currentText().strip() or "landscape"
        profile.margin_mm = float(self.margin.value())
        profile.title_size = float(self.title_size.value())
        profile.body_size = float(self.body_size.value())
        profile.style_preset = self.style_preset.text().strip() or "professional"

        profile.legend.mode = str(self.legend_mode.currentData() or "auto")
        profile.legend.title = self.legend_title.text().strip() or "Legenda"
        profile.legend.columns = int(self.legend_columns.value())
        profile.legend.max_entries = int(self.legend_max.value())
        profile.legend.exclude = [part.strip() for part
                                  in self.legend_exclude.text().split(",") if part.strip()]

        profile.north_arrow.enabled = self.arrow_enabled.isChecked()
        profile.north_arrow.model = self.arrow_model.text().strip() or "NorthArrow_02"
        profile.north_arrow.size_mm = float(self.arrow_size.value())
        profile.north_arrow.rotation = float(self.arrow_rotation.value())
        profile.north_arrow.follow_map = self.arrow_follow.isChecked()

        profile.scale_bar = self.scale_bar.isChecked()
        profile.numeric_scale = self.numeric_scale.isChecked()
        profile.grid = self.grid.isChecked()
        profile.coordinate_frame = self.coordinate_frame.isChecked()
        profile.attribution = self.attribution.isChecked()
        profile.overview = {
            "enabled": self.overview_enabled.isChecked(),
            "zoom_factor": float(self.overview_zoom.value()),
            "anchor": str(self.overview_anchor.currentData() or "panel"),
            "corner": str(self.overview_corner.currentData() or "bottom_right"),
            "frame_color": self.overview_frame_color.text().strip() or "#d32f2f",
        }

        profile.logos = self._images_of(self.logo_list)
        profile.images = self._images_of(self.image_list)

        texts = {}
        for key, edit in self.text_edits.items():
            value = (edit.toPlainText() if isinstance(edit, QPlainTextEdit)
                     else edit.text()).strip()
            if value:
                texts[key] = value
        profile.texts = texts
        return profile

    @staticmethod
    def _images_of(widget: QListWidget) -> list:
        specs = []
        for row in range(widget.count()):
            payload = widget.item(row).data(0x0100)
            if payload:
                specs.append(ImageSpec.from_dict(payload))
        return specs

    def save(self) -> None:
        """Persist the edited profile and the imagery options."""
        profile = self.collect()
        self.store.save(profile)
        settings.set_value(DEFAULT_PROFILE_SETTING, profile.id)
        settings.set_value(PREFERENCE_SETTING,
                           str(self.imagery_provider.currentData() or AUTOMATIC))
        settings.set_value("cartography.probe_imagery", self.probe_imagery.isChecked())
        ProfileStore.reload()
        self.store = ProfileStore.instance()
        log.info(f"Profilo di layout '{profile.id}' salvato")

    # ------------------------------------------------------------------ actions

    def duplicate_profile(self) -> None:
        """Create an editable copy of the current profile."""
        if self.profile is None:
            return
        name, ok = QInputDialog.getText(self, PLUGIN_NAME, "Nome del nuovo profilo:",
                                        text=f"{self.profile.name} (copia)")
        if not ok or not name.strip():
            return
        new_id = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
        copy = self.collect().duplicate(new_id, name.strip())
        self.store.save(copy)
        ProfileStore.reload()
        self.store = ProfileStore.instance()
        self._reload_profiles(select=new_id)

    def delete_profile(self) -> None:
        """Delete a personal profile (shipped ones cannot be removed)."""
        if self.profile is None:
            return
        if self.profile.builtin and not self.store.user_path().exists():
            QMessageBox.information(self, PLUGIN_NAME,
                                    "I profili forniti con il plugin non si possono "
                                    "eliminare.")
            return
        answer = QMessageBox.question(self, PLUGIN_NAME,
                                      f"Eliminare il profilo '{self.profile.name}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.store.delete(self.profile.id):
            QMessageBox.information(self, PLUGIN_NAME,
                                    "Il profilo fornito con il plugin e' stato "
                                    "ripristinato allo stato originale.")
        ProfileStore.reload()
        self.store = ProfileStore.instance()
        self._reload_profiles()

    def export_profile(self) -> None:
        """Write the current profile to a shareable file."""
        if self.profile is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta profilo",
                                              f"{self.profile.id}.json", "JSON (*.json)")
        if not path:
            return
        self.store.export(self.profile.id, Path(path))
        QMessageBox.information(self, PLUGIN_NAME, f"Profilo esportato in {path}")

    def import_profile(self) -> None:
        """Load profiles from a file."""
        path, _ = QFileDialog.getOpenFileName(self, "Importa profilo", "",
                                              "JSON (*.json)")
        if not path:
            return
        try:
            added = self.store.import_file(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, PLUGIN_NAME, f"Import non riuscito: {exc}")
            return
        ProfileStore.reload()
        self.store = ProfileStore.instance()
        self._reload_profiles(select=added[0] if added else "")
        QMessageBox.information(self, PLUGIN_NAME,
                                f"Profili importati: {', '.join(added) or 'nessuno'}")

    def _add_image(self, target: QListWidget, kind: str) -> None:
        filters = "Immagini (" + " ".join(f"*{ext}" for ext in SUPPORTED_IMAGES) + ")"
        path, _ = QFileDialog.getOpenFileName(self, "Scegli un'immagine", "", filters)
        if not path:
            return
        spec = ImageSpec(path=path, w_pct=20.0 if kind == "logos" else 45.0)
        item = QListWidgetItem(path)
        item.setData(0x0100, spec.as_dict())
        target.addItem(item)

    @staticmethod
    def _remove_image(target: QListWidget, kind: str) -> None:
        for item in target.selectedItems():
            target.takeItem(target.row(item))

    def _edit_caption(self, target: QListWidget, kind: str) -> None:
        item = target.currentItem()
        if item is None:
            return
        spec = ImageSpec.from_dict(item.data(0x0100))
        caption, ok = QInputDialog.getText(self, PLUGIN_NAME, "Didascalia:",
                                           text=spec.caption)
        if not ok:
            return
        spec.caption = caption.strip()
        item.setData(0x0100, spec.as_dict())
        item.setText(f"{spec.path}  -  {spec.caption}" if spec.caption else spec.path)

    # ------------------------------------------------------------------ credentials

    def _refresh_google_status(self) -> None:
        origin = credentials.describe(credentials.GOOGLE_MAPS)
        self.google_status.setText(
            f"Configurata ({origin})" if credentials.available(credentials.GOOGLE_MAPS)
            else f"Non configurata. In alternativa si puo' usare la variabile "
                 f"d'ambiente {credentials.env_variable(credentials.GOOGLE_MAPS)}.")

    def _set_google_key(self) -> None:
        key, ok = QInputDialog.getText(self, PLUGIN_NAME,
                                       "Chiave API Google Maps Platform:")
        if not ok or not key.strip():
            return
        stored = credentials.store(credentials.GOOGLE_MAPS, key.strip(),
                                   label=f"{PLUGIN_NAME} - Google Map Tiles")
        if not stored:
            QMessageBox.warning(
                self, PLUGIN_NAME,
                "La chiave non e' stata salvata: il database di autenticazione di QGIS "
                "non e' disponibile (spesso perche' la password principale non e' stata "
                "inserita). La chiave non viene scritta in chiaro: in alternativa usare "
                f"la variabile d'ambiente "
                f"{credentials.env_variable(credentials.GOOGLE_MAPS)}.")
        self._refresh_google_status()

    def _clear_google_key(self) -> None:
        credentials.forget(credentials.GOOGLE_MAPS)
        self._refresh_google_status()
