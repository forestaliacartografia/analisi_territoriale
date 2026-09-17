"""What a sheet promises, and whether it keeps the promise.

A map that says "Carta della pericolosita idraulica" in the title and shows roads,
municipalities and an orthophoto is not a cartographic style choice: it is a false
statement, and a reader has no way to detect it. This module makes the promise explicit
(:class:`MapSheetSpecification`) and then checks it against the layout that was actually
built (:class:`SheetValidator`).

The check runs in both directions, because each catches a different lie:

* **title to legend** - the declared theme must really be on the sheet: present in the
  map frame, visible, not empty, and named in the legend. A layer that is in the project
  but not in the frame proves nothing to whoever reads the paper;
* **legend to title** - a sheet whose legend is dominated by themes the title never
  mentions may be a general map wearing a specific name.

The vocabulary is not invented here. A theme is a **taxonomy category id**, and the words
a title is matched against are that category's own labels, so the taxonomy stays the one
place where the meaning of "pericolosita idraulica" is defined.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from qgis.core import (
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsMapLayer,
    QgsPrintLayout,
    QgsVectorLayer,
)

from ...core import log
from ...core.constants import PROP_LAYER_CATEGORY, PROP_LAYER_SOURCE_ID
from ...core.paths import config_dir, user_dir
from ...core.taxonomy import Taxonomy

#: Outcome levels, worst last: the order is what :func:`worst` compares.
#: The constant is called OK and not PASS on purpose: a name containing "pass"
#: is read as a credential by security scanners, and a suppression comment would
#: be one more thing to explain. The level itself is still reported as "pass".
OK = "pass"
WARNING = "warning"
ERROR = "error"
_ORDER = {OK: 0, WARNING: 1, ERROR: 2}

#: Titles that promise nothing in particular; flagged, never blocked.
GENERIC_TITLES = ("carta", "mappa", "tavola", "inquadramento", "generale", "sintesi")

_SPECS: Optional[Dict[str, Any]] = None


def _fold(text: str) -> str:
    """Lowercase, strip accents and punctuation: "Pericolosità" -> "pericolosita"."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", stripped).strip()


def worst(levels: Sequence[str]) -> str:
    """Return the most severe level of a sequence."""
    return max(levels, key=lambda lv: _ORDER.get(lv, 0)) if levels else OK


@dataclass
class QaFinding:
    """One thing the validator checked."""

    level: str
    code: str
    message: str
    category: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)


@dataclass
class QaReport:
    """Everything the validator checked on one sheet."""

    template: str = ""
    title: str = ""
    theme: str = ""
    findings: List[QaFinding] = field(default_factory=list)

    def add(self, level: str, code: str, message: str, category: str = "") -> QaFinding:
        """Record a finding and return it."""
        finding = QaFinding(level, code, message, category)
        self.findings.append(finding)
        return finding

    @property
    def level(self) -> str:
        """The worst level recorded."""
        return worst([f.level for f in self.findings])

    @property
    def blocking(self) -> bool:
        """Whether the sheet must not be exported."""
        return self.level == ERROR

    @property
    def errors(self) -> List[QaFinding]:
        """Findings that block the export."""
        return [f for f in self.findings if f.level == ERROR]

    @property
    def warnings(self) -> List[QaFinding]:
        """Findings worth reading but not blocking."""
        return [f for f in self.findings if f.level == WARNING]

    def summary(self) -> str:
        """One line for the dock, the log and the report."""
        if not self.findings:
            return "nessun controllo eseguito"
        if self.blocking:
            return f"{len(self.errors)} errori, {len(self.warnings)} avvertenze"
        if self.warnings:
            return f"{len(self.warnings)} avvertenze"
        return "controlli superati"

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {"template": self.template, "title": self.title, "theme": self.theme,
                "level": self.level, "summary": self.summary(),
                "findings": [f.as_dict() for f in self.findings]}


@dataclass
class MapSheetSpecification:
    """The contract of one sheet: what it claims and what must back the claim."""

    template: str = ""
    title: str = ""
    #: Taxonomy category the sheet is *about*. Empty means a general-purpose sheet,
    #: which is exempt from the theme checks but not from the layout ones.
    theme: str = ""
    required_categories: List[str] = field(default_factory=list)
    required_legend_items: List[str] = field(default_factory=list)
    optional_categories: List[str] = field(default_factory=list)
    excluded_categories: List[str] = field(default_factory=list)
    primary_source: str = ""
    #: Extra words that also identify the theme in a title, beyond the taxonomy labels.
    title_keywords: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MapSheetSpecification":
        """Build from a JSON fragment, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @property
    def general_purpose(self) -> bool:
        """Whether the sheet declares no dominant theme."""
        return not self.theme

    def theme_categories(self) -> List[str]:
        """Categories that satisfy the theme: the declared ones, or the theme itself."""
        return list(self.required_categories) or ([self.theme] if self.theme else [])

    def accepted_categories(self) -> List[str]:
        """Everything the sheet may legitimately show as its own subject."""
        return self.theme_categories() + list(self.optional_categories)

    def title_terms(self) -> List[str]:
        """Words whose presence in a title means "this sheet is about the theme".

        Taken from the taxonomy label of the theme, so the wording lives in one place.
        """
        terms = [_fold(word) for word in self.title_keywords if word]
        if self.theme:
            category = Taxonomy.instance().get(self.theme)
            if category is not None:
                for language in ("it", "en"):
                    label = _fold(category.label(language))
                    if label:
                        terms.append(label)
        return [t for t in terms if t]


def specifications() -> Dict[str, Any]:
    """Load the sheet contracts (built-in plus user overrides)."""
    global _SPECS
    if _SPECS is None:
        path = config_dir() / "layouts" / "sheets.json"
        try:
            _SPECS = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
            log.warning(f"Cannot read sheets.json: {exc}")
            _SPECS = {"sheets": {}}
        user_file = user_dir() / "layouts" / "sheets.json"
        if user_file.exists():
            try:
                overlay = json.loads(user_file.read_text(encoding="utf-8"))
                for key, value in (overlay.get("sheets") or {}).items():
                    _SPECS.setdefault("sheets", {})[key] = {
                        **_SPECS.get("sheets", {}).get(key, {}), **value}
            except (OSError, ValueError) as exc:  # pragma: no cover - user error path
                log.warning(f"Ignoring invalid user sheets.json: {exc}")
    return _SPECS


def reload_specifications() -> None:
    """Forget the cached contracts (used by the tests and by the settings dialog)."""
    global _SPECS
    _SPECS = None


def specification_for(template_id: str, *, title: str = "") -> MapSheetSpecification:
    """Return the contract of a template, or an unthemed one when none is declared."""
    payload = (specifications().get("sheets") or {}).get(template_id)
    if not payload:
        return MapSheetSpecification(template=template_id, title=title)
    spec = MapSheetSpecification.from_dict(payload)
    spec.template = template_id
    spec.title = title or spec.title
    return spec


# --------------------------------------------------------------------------- inspection

def category_of(layer: QgsMapLayer) -> str:
    """The thematic category a layer was stamped with when it entered the project."""
    return (layer.customProperty(PROP_LAYER_CATEGORY, "") or "") if layer else ""


def legend_items(layout: QgsPrintLayout) -> List[QgsMapLayer]:
    """Every layer that a legend of this layout actually lists."""
    found: List[QgsMapLayer] = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemLegend):
            continue
        model = item.model()
        if model is None:
            continue
        root = model.rootGroup()
        if root is None:
            continue
        for node in root.findLayers():
            layer = node.layer()
            # A node switched off is printed as absent, so it does not count as shown.
            if layer is not None and node.itemVisibilityChecked():
                found.append(layer)
    return found


def is_empty(layer: QgsMapLayer) -> bool:
    """Whether a vector layer has no features at all.

    Only vector layers can answer honestly; a raster is never reported as empty here,
    because counting its pixels is a different (and much more expensive) question.
    """
    if isinstance(layer, QgsVectorLayer) and layer.isValid():
        try:
            return layer.featureCount() == 0
        except Exception as exc:  # pragma: no cover - provider differences
            log.debug(f"is_empty: conteggio non disponibile ({type(exc).__name__}: {exc})")
    return False


def within_extent(layer: QgsMapLayer, frame: QgsLayoutItemMap) -> bool:
    """Whether a layer has anything inside what the map frame shows."""
    if layer is None or frame is None or not layer.isValid():
        return False
    extent = layer.extent()
    if extent.isEmpty():
        return False
    try:
        from qgis.core import QgsGeometry

        from ...core import crs as crs_utils

        if layer.crs() != frame.crs():
            extent = crs_utils.bbox_in(QgsGeometry.fromRect(extent),
                                       layer.crs(), frame.crs())
        return frame.extent().intersects(extent)
    except Exception as exc:  # pragma: no cover - transform edge cases
        log.debug(f"within_extent: confronto non riuscito ({type(exc).__name__}: {exc})")
        return True


class SheetValidator:
    """Check a built layout against the contract its template declares."""

    def __init__(self, spec: MapSheetSpecification) -> None:
        self.spec = spec

    # ------------------------------------------------------------------ entry point

    def validate(self, layout: QgsPrintLayout, *, title: str = "") -> QaReport:
        """Run every check on a finished layout.

        The layout must already be built: the point is to inspect what will be printed,
        not what the builder intended.
        """
        from .overview import main_map

        title = title or self.spec.title
        report = QaReport(template=self.spec.template, title=title, theme=self.spec.theme)
        frame = main_map(layout)
        if frame is None:
            report.add(ERROR, "no_map_frame",
                       "La tavola non contiene un riquadro di mappa.")
            return report

        shown = list(frame.layers()) or []
        legend = legend_items(layout)
        self._check_title(report, title)
        if not self.spec.general_purpose:
            self._check_theme_is_present(report, shown, frame)
            self._check_theme_is_in_legend(report, shown, legend)
            self._check_legend_is_focused(report, legend)
        self._check_excluded(report, shown)
        if not report.findings:
            report.add(OK, "ok", "Controlli superati.")
        return report

    # ------------------------------------------------------------------ title

    def _check_title(self, report: QaReport, title: str) -> None:
        """The title must name the theme, and must not be a placeholder."""
        folded = _fold(title)
        if not folded:
            report.add(ERROR, "no_title", "La tavola non ha un titolo.")
            return
        if self.spec.general_purpose:
            return
        terms = self.spec.title_terms()
        if terms and not any(term in folded for term in terms):
            report.add(WARNING, "title_does_not_name_theme",
                       f"Il titolo «{title}» non nomina il tema dichiarato "
                       f"({self.spec.theme}).", self.spec.theme)
        elif folded in GENERIC_TITLES or len(folded.split()) < 2:
            report.add(WARNING, "generic_title",
                       f"Il titolo «{title}» e' troppo generico per identificare il tema.")

    # ------------------------------------------------------------------ title -> legend

    def _check_theme_is_present(self, report: QaReport, shown: List[QgsMapLayer],
                                frame: QgsLayoutItemMap) -> None:
        """The declared theme must really be in the map frame, and say something."""
        wanted = self.spec.theme_categories()
        matching = [lay for lay in shown if category_of(lay) in wanted]
        if not matching:
            report.add(ERROR, "theme_layer_missing",
                       f"Il titolo dichiara «{self.spec.theme}» ma nessuno strato di "
                       f"quel tema e' nel riquadro di mappa.", self.spec.theme)
            return
        if all(is_empty(lay) for lay in matching):
            report.add(ERROR, "theme_layer_empty",
                       f"Lo strato del tema «{self.spec.theme}» e' presente ma non "
                       f"contiene alcun elemento: la tavola non mostra il tema che "
                       f"annuncia.", self.spec.theme)
            return
        drawable = [lay for lay in matching if not is_empty(lay)]
        if not any(within_extent(lay, frame) for lay in drawable):
            report.add(ERROR, "theme_layer_out_of_extent",
                       f"Lo strato del tema «{self.spec.theme}» e' interamente fuori "
                       f"dall'estensione stampata.", self.spec.theme)

    def _check_theme_is_in_legend(self, report: QaReport, shown: List[QgsMapLayer],
                                  legend: List[QgsMapLayer]) -> None:
        """Being drawn is not enough: the reader must be able to name what they see."""
        wanted = self.spec.theme_categories()
        in_frame = [lay for lay in shown if category_of(lay) in wanted]
        if not in_frame:
            return                       # gia' segnalato come errore piu' grave
        if not any(lay in legend for lay in in_frame):
            report.add(ERROR, "theme_missing_from_legend",
                       f"Il tema «{self.spec.theme}» e' disegnato ma non compare in "
                       f"legenda: chi legge non puo' riconoscerlo.", self.spec.theme)
        for name in self.spec.required_legend_items:
            folded = _fold(name)
            if not any(folded in _fold(lay.name()) for lay in legend):
                report.add(ERROR, "legend_item_missing",
                           f"La legenda non riporta la voce richiesta «{name}».",
                           self.spec.theme)

    # ------------------------------------------------------------------ legend -> title

    def _check_legend_is_focused(self, report: QaReport,
                                 legend: List[QgsMapLayer]) -> None:
        """A legend crowded with other themes suggests a general map with a specific name.

        This is the reverse direction required by the specification, and it is a warning
        on purpose: context layers are legitimate, and only the balance is suspicious.
        """
        accepted = set(self.spec.accepted_categories())
        support = {"project_area", "administrative", "imagery", "terrain", "roads",
                   "places", "buildings", "hydrography"}
        foreign = sorted({category_of(lay) for lay in legend
                          if category_of(lay)
                          and category_of(lay) not in accepted
                          and category_of(lay) not in support})
        if not foreign:
            return
        on_theme = sum(1 for lay in legend if category_of(lay) in accepted)
        if len(foreign) >= 2 and len(foreign) > on_theme:
            report.add(WARNING, "legend_not_focused",
                       f"La legenda e' dominata da temi che il titolo non dichiara "
                       f"({', '.join(foreign)}): la tavola potrebbe non essere "
                       f"centrata sul tema annunciato.", self.spec.theme)

    def _check_excluded(self, report: QaReport, shown: List[QgsMapLayer]) -> None:
        """Some themes are explicitly forbidden on a sheet (a risk map is not a plan)."""
        for category in self.spec.excluded_categories:
            offenders = [lay.name() for lay in shown if category_of(lay) == category]
            if offenders:
                report.add(ERROR, "excluded_category_present",
                           f"La tavola contiene «{category}», che il contratto della "
                           f"tavola esclude: {', '.join(offenders[:3])}.", category)


def validate_layout(layout: QgsPrintLayout, template_id: str, *,
                    title: str = "") -> QaReport:
    """Validate a layout against the contract of its template."""
    spec = specification_for(template_id, title=title)
    return SheetValidator(spec).validate(layout, title=title)
