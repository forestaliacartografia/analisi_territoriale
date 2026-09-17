"""What the plugin is allowed to say about the vincolo idrogeologico.

The constraint of R.D.L. 3267/1923 is not a hazard and not a risk: it is a legal regime
whose perimeter is drawn by the competent authority. The plugin reads a map of it. Those
are different things, and the vocabulary here exists to keep them apart.

The shape of this module is forced by what the sources actually publish. Where the
official layer is served as an image only - which is the case for Tuscany, verified - no
surface and no percentage can be computed from it. What remains possible is to ask the
service what it holds at a point, and to say so. A sampled answer is an observation about
the sampled points, never a measurement of the area, and the wording below never lets the
two be confused.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ...core.gaps import DataGap
from ...core.models import Provenance


class Presence(str, Enum):
    """What was observed about the constraint over the area."""

    #: Every sampled point falls inside the official perimeter.
    PRESENTE = "PRESENTE"
    #: The service answered and no sampled point falls inside the perimeter.
    #: This is a statement about the sample, not a certificate that the area is free.
    ASSENTE = "ASSENTE"
    #: Some sampled points fall inside and others do not.
    PARZIALE = "PARZIALE"
    #: No usable answer: no source, no coverage, or the service did not respond.
    NON_VERIFICABILE = "NON_VERIFICABILE"

    @property
    def label_it(self) -> str:
        """Wording for the dossier. Deliberately descriptive, never conclusive."""
        return {
            Presence.PRESENTE:
                "i punti campionati ricadono nella perimetrazione cartografica",
            Presence.ASSENTE:
                "nessun punto campionato ricade nella perimetrazione cartografica",
            Presence.PARZIALE:
                "l'area e' interessata solo in parte dalla perimetrazione cartografica",
            Presence.NON_VERIFICABILE:
                "non verificabile con le fonti disponibili",
        }[self]

    @property
    def is_conclusive(self) -> bool:
        """Whether the observation says anything at all about the territory."""
        return self is not Presence.NON_VERIFICABILE


@dataclass
class SamplePoint:
    """One point asked of the official layer, and what came back."""

    x: float = 0.0
    y: float = 0.0
    crs: str = ""
    inside: bool = False
    #: Attributes the service returned, verbatim.
    attributes: Dict[str, str] = field(default_factory=dict)
    answered: bool = True

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)


@dataclass
class HydrogeologicalOutcome:
    """The result of looking for the constraint over one area."""

    presence: Presence = Presence.NON_VERIFICABILE
    source_id: str = ""
    source_name: str = ""
    region: str = ""
    #: ``analysable`` when the perimeter can be downloaded and measured, ``view_only``
    #: when it can only be looked at and asked about point by point.
    capability: str = "view_only"
    samples: List[SamplePoint] = field(default_factory=list)
    #: Legal act references the layer itself carries, when it carries any.
    act_references: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    provenance: Optional[Provenance] = None
    #: Surface and percentage, **only** when the source could be measured. They stay
    #: ``None`` for a view-only source rather than being estimated from the sample.
    area_m2: Optional[float] = None
    percentage: Optional[float] = None

    @property
    def sampled(self) -> int:
        """How many points were asked."""
        return len(self.samples)

    @property
    def inside(self) -> int:
        """How many sampled points fall inside the perimeter."""
        return sum(1 for s in self.samples if s.inside)

    @property
    def measurable(self) -> bool:
        """Whether a surface was really measured rather than inferred."""
        return self.area_m2 is not None

    @property
    def requires_regional_source(self) -> bool:
        """Whether the answer is missing because no regional source covers the area."""
        return DataGap.REGIONAL_SOURCE_REQUIRED.value in self.gaps

    def statement(self) -> str:
        """The sentence the dossier prints.

        It describes the cartographic observation and stops there. Turning a perimeter
        into "the area is subject to the constraint" is an administrative assessment, and
        the plugin does not make it.
        """
        if self.presence is Presence.NON_VERIFICABILE:
            if self.requires_regional_source:
                return ("Vincolo idrogeologico (R.D.L. 3267/1923): non verificabile. "
                        "Il vincolo e' di competenza regionale e per quest'area non "
                        "risulta configurata una fonte cartografica ufficiale. "
                        "L'assenza di dato non equivale all'assenza del vincolo.")
            return ("Vincolo idrogeologico (R.D.L. 3267/1923): non verificabile con le "
                    "fonti disponibili. L'assenza di dato non equivale all'assenza del "
                    "vincolo.")
        if self.measurable:
            head = (f"Vincolo idrogeologico (R.D.L. 3267/1923): {self.presence.label_it}. "
                    f"Superficie interessata {self.area_m2 / 10_000:.2f} ha "
                    f"({self.percentage:.1f}% dell'area).")
        else:
            head = (f"Vincolo idrogeologico (R.D.L. 3267/1923): {self.presence.label_it} "
                    f"({self.inside} punti su {self.sampled}). La fonte e' consultabile "
                    f"ma non scaricabile: non e' possibile calcolare la superficie "
                    f"interessata.")
        return (head + " Il dato e' cartografico e ricognitivo: la verifica del vincolo "
                       "compete all'ente competente per territorio.")

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary for the dossier."""
        return {
            "presence": self.presence.value,
            "presence_label": self.presence.label_it,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "region": self.region,
            "capability": self.capability,
            "measurable": self.measurable,
            "area_m2": self.area_m2,
            "percentage": self.percentage,
            "sampled": self.sampled,
            "inside": self.inside,
            "samples": [s.as_dict() for s in self.samples],
            "act_references": list(self.act_references),
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
            "statement": self.statement(),
            "legal_reference": ("R.D.L. 30 dicembre 1923, n. 3267; "
                                "R.D. 16 maggio 1926, n. 1126"),
            "provenance": self.provenance.as_dict() if self.provenance else None,
        }
