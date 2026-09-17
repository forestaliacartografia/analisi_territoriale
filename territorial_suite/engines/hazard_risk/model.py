"""Hazard is not risk, and neither is an inventory.

Four things get called "frane" in ordinary speech and they are not the same claim:

* an **inventory** records a phenomenon somebody observed and mapped;
* **susceptibility** says a place has the predisposition for one;
* **hazard** attaches a probability, usually through a plan that classifies it;
* **risk** combines that probability with what would be damaged.

The same four apply to water. Collapsing them is how a dossier ends up asserting a risk
that no source ever computed, and the whole shape of this module exists to keep them
apart: a theme carries its own kind, and nothing converts one kind into another.

The hard rule, from the requirement and from the data: **a risk is never derived from a
hazard**. When only hazard sources answer, the risk theme reports that it is not
determinable and says which source was consulted, rather than quietly reusing the hazard
classes under a different heading.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ...core import measure
from ...core.gaps import DataGap
from ...core.models import Provenance


class Kind(str, Enum):
    """What a theme actually asserts."""

    INVENTORY = "inventory"
    SUSCEPTIBILITY = "susceptibility"
    HAZARD = "hazard"
    RISK = "risk"

    @property
    def label_it(self) -> str:
        """Wording for the dossier."""
        return {
            Kind.INVENTORY: "inventario dei fenomeni",
            Kind.SUSCEPTIBILITY: "suscettibilita'",
            Kind.HAZARD: "pericolosita'",
            Kind.RISK: "rischio",
        }[self]

    @property
    def is_probabilistic(self) -> bool:
        """Whether the kind carries a statement about likelihood."""
        return self in (Kind.HAZARD, Kind.RISK)


@dataclass
class HazardClass:
    """One class of a hazard or risk source, over the area.

    ``official_code`` and ``official_label`` are whatever the source published, kept
    verbatim. ``normalised`` is the plugin's own comparable level and is always a
    *second* value: aggregating for a legend must never erase the classification the
    authority actually used.
    """

    official_code: str = ""
    official_label: str = ""
    normalised: str = ""
    area_m2: float = 0.0
    percentage: float = 0.0
    feature_count: int = 0
    #: Where the class came from: ``attribute`` when the source published it as data,
    #: ``layer`` when the only distinction available was which layer it was in.
    derived_from: str = "attribute"

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        payload = asdict(self)
        payload["area_ha"] = round(self.area_m2 / 10_000.0, 4)
        payload["area_m2"] = round(self.area_m2, 2)
        payload["percentage"] = round(self.percentage, 2)
        return payload


@dataclass
class ThemeOutcome:
    """One theme (flood hazard, landslide risk, ...) over the area."""

    theme: str = ""
    kind: Kind = Kind.HAZARD
    source_id: str = ""
    source_name: str = ""
    plan: str = ""
    dataset_version: str = ""
    classes: List[HazardClass] = field(default_factory=list)
    area_m2: float = 0.0
    gaps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    provenance: Optional[Provenance] = None

    @property
    def present(self) -> bool:
        """Whether any class of this theme was measured over the area."""
        return any(c.area_m2 > 0 for c in self.classes)

    @property
    def determinable(self) -> bool:
        """Whether a source able to answer this theme was available at all."""
        return not any(g in self.gaps for g in (DataGap.NO_DATA.value,
                                                DataGap.SOURCE_UNAVAILABLE.value,
                                                DataGap.VIEW_ONLY.value))

    @property
    def covered_m2(self) -> float:
        """Total surface the theme's classes occupy inside the area."""
        return sum(c.area_m2 for c in self.classes)

    @property
    def percentage(self) -> float:
        """Share of the area the theme occupies."""
        return measure.percentage(self.covered_m2, self.area_m2)

    @property
    def worst(self) -> Optional[HazardClass]:
        """The class with the largest surface, which is not the same as the highest."""
        return max(self.classes, key=lambda c: c.area_m2) if self.classes else None

    def statement(self) -> str:
        """The sentence the dossier prints for this theme."""
        label = self.kind.label_it
        if not self.determinable:
            reason = ", ".join(DataGap[g].label_it for g in self.gaps
                               if g in DataGap.__members__)
            return (f"{label.capitalize()} ({self.theme}): non determinabile dalla fonte "
                    f"disponibile ({reason or 'nessuna fonte interrogabile'}).")
        if not self.present:
            return (f"{label.capitalize()} ({self.theme}): nessuna perimetrazione "
                    f"interseca l'area secondo {self.source_name or 'la fonte consultata'}.")
        parts = [f"{c.official_label or c.official_code}: "
                 f"{c.area_m2 / 10_000:.2f} ha ({c.percentage:.1f}%)"
                 for c in self.classes if c.area_m2 > 0]
        return (f"{label.capitalize()} ({self.theme}) da {self.source_name}: "
                + "; ".join(parts) + ".")

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "theme": self.theme,
            "kind": self.kind.value,
            "kind_label": self.kind.label_it,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "plan": self.plan,
            "dataset_version": self.dataset_version,
            "present": self.present,
            "determinable": self.determinable,
            "area_m2": round(self.area_m2, 2),
            "covered_m2": round(self.covered_m2, 2),
            "percentage": round(self.percentage, 2),
            "classes": [c.as_dict() for c in self.classes],
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
            "statement": self.statement(),
            "provenance": self.provenance.as_dict() if self.provenance else None,
        }


@dataclass
class HazardRiskOutcome:
    """Every hazard and risk theme looked at over one area."""

    area_m2: float = 0.0
    themes: List[ThemeOutcome] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def by_kind(self, kind: Kind) -> List[ThemeOutcome]:
        """Themes of one kind."""
        return [t for t in self.themes if t.kind is kind]

    def theme(self, name: str) -> Optional[ThemeOutcome]:
        """One theme by name."""
        return next((t for t in self.themes if t.theme == name), None)

    @property
    def ok(self) -> bool:
        """Whether at least one theme could be determined."""
        return any(t.determinable for t in self.themes)

    def matrix(self) -> List[Dict[str, Any]]:
        """The synthetic table the dossier prints, one row per theme."""
        rows = []
        for outcome in self.themes:
            worst = outcome.worst
            rows.append({
                "theme": outcome.theme,
                "kind": outcome.kind.value,
                "present": outcome.present if outcome.determinable else None,
                "area_m2": round(outcome.covered_m2, 2),
                "percentage": round(outcome.percentage, 2),
                "dominant_class": (worst.official_label or worst.official_code)
                                  if worst else "",
                "source": outcome.source_name,
                "determinable": outcome.determinable,
            })
        return rows

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary for the dossier."""
        return {
            "area_m2": round(self.area_m2, 2),
            "themes": [t.as_dict() for t in self.themes],
            "matrix": self.matrix(),
            "warnings": list(self.warnings),
        }
