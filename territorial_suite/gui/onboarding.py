"""First-run onboarding: four steps, no more."""

from __future__ import annotations

from typing import Optional

from qgis.core import QgsProject
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import PLUGIN_NAME, PROP_ONBOARDING_DONE
from ..core import settings

TEXT = f"""
<h2>Benvenuto in {PLUGIN_NAME}</h2>
<p>Quattro passi per il primo quadro conoscitivo:</p>
<ol>
<li><b>Crea l'area di progetto</b> - disegnala sulla mappa, importala da un file o
    costruiscila dalle particelle catastali selezionate.</li>
<li><b>Controlla le sorgenti</b> - in <i>Dati &gt; Sorgenti</i> vedi il catalogo, lo stato
    dei servizi e puoi aggiungere le fonti regionali che ti servono.</li>
<li><b>Esegui l'analisi</b> - il pulsante <i>Analisi territoriale (one-click)</i> raccoglie
    localizzazione, catasto, vincoli, terreno e segnalazioni.</li>
<li><b>Produci la cartografia</b> - <i>Crea mappa</i> per una tavola, <i>Serie di tavole</i>
    per l'intero set, <i>Relazione</i> e <i>Pacchetto</i> per la consegna.</li>
</ol>
<p style="color:#555"><i>Il plugin riporta dati cartografici e la loro provenienza: non
sostituisce la verifica dei vincoli presso gli enti competenti.</i></p>
"""


class OnboardingDialog(QDialog):
    """Shown once per profile (and on demand from the help menu)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{PLUGIN_NAME} - Primi passi")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        label = QLabel(TEXT)
        label.setWordWrap(True)
        label.setTextFormat(label.textFormat().RichText if hasattr(label.textFormat(), "RichText")
                            else label.textFormat())
        layout.addWidget(label, 1)
        self.dont_show = QCheckBox("Non mostrare piu' questo messaggio")
        self.dont_show.setChecked(True)
        layout.addWidget(self.dont_show)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def accept(self) -> None:
        """Remember the user's choice and close."""
        if self.dont_show.isChecked():
            settings.set_value("general.onboarding_done", True)
        super().accept()


def should_show() -> bool:
    """Whether the onboarding must be shown at start-up."""
    if settings.get("general.onboarding_done", False):
        return False
    try:
        scope, _, key = PROP_ONBOARDING_DONE.partition("/")
        done, ok = QgsProject.instance().readBoolEntry(scope, key, False)
        return not (ok and done)
    except Exception:  # pragma: no cover - defensive
        return True
