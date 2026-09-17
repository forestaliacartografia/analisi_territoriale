"""Land cover over the project area, measured rather than displayed.

CORINE Land Cover is usually treated as a picture to put under other layers. It is a
classification, and a classification can answer questions: how much of this area is
forest, how much is built, how much is farmed, and under which official code.

Two rules shape everything here:

* **the surface is measured on the geometry**, never taken from the source's own area
  field. A CLC polygon that touches the area contributes only the part inside it, and a
  percentage computed on the whole polygon would be wrong by however much of it sticks
  out;
* **the official code is never lost**. Aggregating to level 1 or 2 is a view, not a
  replacement: ``111`` stays ``111`` underneath "Superfici artificiali", because the
  reader must be able to go back to the classification the data actually carries.

The nomenclature itself is reference data in ``config/nomenclature/``: the service
returns codes, and the labels belong to the published CORINE nomenclature, not to this
module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qgis.core import QgsGeometry, QgsVectorLayer

from ..core import crs as crs_utils
from ..core import geometry as geom_utils
from ..core import log, measure
from ..core.gaps import DataGap
from ..core.models import Provenance
from ..core.paths import config_dir, user_dir
from ..core.project_area import ProjectArea
from ..core.registry import DataSource

#: How many digits each CORINE level uses.
LEVEL_DIGITS = {1: 1, 2: 2, 3: 3}

_NOMENCLATURE: Optional[Dict[str, Any]] = None


def nomenclature() -> Dict[str, Any]:
    """Load the CORINE nomenclature (built-in, overridable by the user)."""
    global _NOMENCLATURE
    if _NOMENCLATURE is None:
        path = config_dir() / "nomenclature" / "corine_land_cover.json"
        try:
            _NOMENCLATURE = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
            log.warning(f"Cannot read the CORINE nomenclature: {exc}")
            _NOMENCLATURE = {"classes": [], "levels": {}}
        user_file = user_dir() / "nomenclature" / "corine_land_cover.json"
        if user_file.exists():
            try:
                overlay = json.loads(user_file.read_text(encoding="utf-8"))
                known = {c["code"]: c for c in _NOMENCLATURE.get("classes", [])}
                for item in overlay.get("classes", []):
                    if item.get("code"):
                        known[item["code"]] = {**known.get(item["code"], {}), **item}
                _NOMENCLATURE["classes"] = list(known.values())
            except (OSError, ValueError) as exc:  # pragma: no cover - user error path
                log.warning(f"Ignoring an invalid CORINE nomenclature: {exc}")
    return _NOMENCLATURE


def reload_nomenclature() -> None:
    """Forget the cached nomenclature (tests, settings dialog)."""
    global _NOMENCLATURE
    _NOMENCLATURE = None


def label_for(code: str, level: int = 3, language: str = "it") -> str:
    """Official label of a CORINE code at the requested level.

    An unknown code is returned as itself: inventing a name for a class the nomenclature
    does not list would be worse than admitting the code is unrecognised.
    """
    code = str(code or "").strip()
    if not code:
        return ""
    key = f"label_{language}"
    digits = LEVEL_DIGITS.get(level, 3)
    wanted = code[:digits]
    data = nomenclature()
    if digits == 3:
        for item in data.get("classes", []):
            if item.get("code") == wanted:
                return item.get(key) or item.get("label_en") or wanted
    else:
        for item in data.get("levels", {}).get(str(digits), []):
            if item.get("code") == wanted:
                return item.get(key) or item.get("label_en") or wanted
    return wanted


def known_code(code: str) -> bool:
    """Whether the nomenclature lists this level-3 code."""
    return any(item.get("code") == str(code).strip()
               for item in nomenclature().get("classes", []))


@dataclass
class LandCoverClass:
    """One land cover class over the area, with the surface really inside it."""

    code: str
    label: str
    level: int = 3
    area_m2: float = 0.0
    percentage: float = 0.0
    #: Level-3 codes that were folded into this row when aggregating.
    official_codes: List[str] = field(default_factory=list)
    recognised: bool = True

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {"code": self.code, "label": self.label, "level": self.level,
                "area_m2": round(self.area_m2, 2),
                "area_ha": round(self.area_m2 / 10_000.0, 4),
                "percentage": round(self.percentage, 2),
                "official_codes": list(self.official_codes),
                "recognised": self.recognised}


@dataclass
class LandCoverOutcome:
    """What the land cover analysis found over one area."""

    source_id: str = ""
    level: int = 3
    area_m2: float = 0.0
    classes: List[LandCoverClass] = field(default_factory=list)
    covered_m2: float = 0.0
    gaps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    provenance: Optional[Provenance] = None

    @property
    def ok(self) -> bool:
        """Whether at least one class was measured."""
        return bool(self.classes)

    @property
    def coverage_pct(self) -> float:
        """Share of the area that the classification actually covers."""
        return measure.percentage(self.covered_m2, self.area_m2)

    @property
    def dominant(self) -> Optional[LandCoverClass]:
        """The class occupying the largest share of the area."""
        return max(self.classes, key=lambda c: c.area_m2) if self.classes else None

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary for the dossier."""
        return {
            "source_id": self.source_id,
            "level": self.level,
            "area_m2": round(self.area_m2, 2),
            "covered_m2": round(self.covered_m2, 2),
            "coverage_pct": round(self.coverage_pct, 2),
            "dominant": self.dominant.as_dict() if self.dominant else None,
            "classes": [c.as_dict() for c in self.classes],
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
            "provenance": self.provenance.as_dict() if self.provenance else None,
        }


class LandCoverEngine:
    """Measure land cover over a project area from a classified vector source."""

    def __init__(self, *, code_field: str = "") -> None:
        self.code_field = code_field

    # ------------------------------------------------------------------ measurement

    def measure_layer(self, area: ProjectArea, layer: QgsVectorLayer, *,
                      source: Optional[DataSource] = None,
                      level: int = 3, language: str = "it") -> LandCoverOutcome:
        """Compute the surface of each class really inside the area.

        Safe to call from a worker thread only if ``layer`` was built there; the engine
        itself creates no QGIS object.
        """
        level = level if level in LEVEL_DIGITS else 3
        work_crs = area.work_crs
        area_geom = area.geometry_in(work_crs)
        total = measure.area_m2(area_geom, work_crs)
        outcome = LandCoverOutcome(source_id=(source.id if source else ""),
                                   level=level, area_m2=total)
        if source is not None:
            outcome.provenance = source.provenance(
                operation=f"Uso del suolo, livello {level}", crs=work_crs.authid())

        field_name = self.code_field or (source.fields.get("code") if source else "") or ""
        if layer is None or not layer.isValid():
            outcome.gaps.append(DataGap.SOURCE_UNAVAILABLE.value)
            return outcome
        if field_name and layer.fields().indexOf(field_name) < 0:
            outcome.gaps.append(DataGap.QUERY_FAILED.value)
            outcome.warnings.append(
                f"Il campo del codice «{field_name}» non esiste nello strato: "
                f"la classificazione non e' interpretabile.")
            return outcome

        by_code: Dict[str, float] = {}
        unreadable = 0
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if layer.crs() != work_crs:
                try:
                    geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
                except Exception as exc:
                    log.debug(f"measure_layer: feature saltata "
                              f"({type(exc).__name__}: {exc})")
                    unreadable += 1
                    continue
            clipped = geom_utils.intersection(geometry, area_geom)
            if clipped is None or clipped.isEmpty():
                continue
            # The surface is the part inside the area, not the polygon's own extent.
            surface = measure.area_m2(clipped, work_crs)
            if surface <= 0:
                continue
            code = str(feature[field_name]).strip() if field_name else ""
            by_code[code] = by_code.get(code, 0.0) + surface

        if unreadable:
            outcome.warnings.append(
                f"{unreadable} geometrie non trasformabili nel CRS di lavoro: "
                f"escluse dal calcolo.")
            outcome.gaps.append(DataGap.PARTIAL_COVERAGE.value)
        if not by_code:
            outcome.gaps.append(DataGap.NO_FEATURE_FOUND.value)
            return outcome

        outcome.classes = self._aggregate(by_code, total, level, language)
        outcome.covered_m2 = sum(c.area_m2 for c in outcome.classes)
        # CORINE does not map anything smaller than 25 ha, so a small area is often only
        # partly classified. Saying "70% forest" without saying "of the 80% classified"
        # would overstate what the data knows.
        if measure.percentage(outcome.covered_m2, total) < 99.0:
            outcome.gaps.append(DataGap.PARTIAL_COVERAGE.value)
            outcome.warnings.append(
                f"La classificazione copre il {outcome.coverage_pct:.1f}% dell'area: "
                f"le percentuali si riferiscono alla sola parte classificata.")
        return outcome

    @staticmethod
    def _aggregate(by_code: Dict[str, float], total: float, level: int,
                   language: str) -> List[LandCoverClass]:
        """Fold level-3 codes into the requested level, keeping the originals."""
        digits = LEVEL_DIGITS.get(level, 3)
        grouped: Dict[str, Dict[str, Any]] = {}
        for code, surface in by_code.items():
            key = (code[:digits] if code else "")
            entry = grouped.setdefault(key, {"area": 0.0, "codes": set()})
            entry["area"] += surface
            if code:
                entry["codes"].add(code)
        classes = []
        for key, entry in grouped.items():
            classes.append(LandCoverClass(
                code=key or "-",
                label=label_for(key, level, language) if key else "classe non indicata",
                level=level,
                area_m2=entry["area"],
                percentage=measure.percentage(entry["area"], total),
                official_codes=sorted(entry["codes"]),
                recognised=bool(key) and all(known_code(c) for c in entry["codes"]),
            ))
        return sorted(classes, key=lambda c: c.area_m2, reverse=True)
