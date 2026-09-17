"""Normalised records of the Cultural Heritage module.

These dataclasses are plain JSON, like everything in :mod:`territorial_suite.core.models`:
they travel from the worker thread to the GUI, the dossier and ``analysis.json`` through
:attr:`AnalysisReport.modules`.

The vocabulary deliberately keeps four things apart, because conflating them is how a
mapping layer silently turns into a legal opinion:

``DATA_PRESENT``
    a dataset covering this theme was queried and answered.
``AREA_INTERSECTS_DATA``
    the project area geometrically intersects a feature of that dataset.
``OFFICIAL_INFORMATION``
    the dataset is published by the authority competent for the theme.
``LEGAL_REFERENCE_PRESENT``
    the feature itself carries the reference of an administrative act.

Only the fourth licenses a sentence about a *provvedimento*, and even then the plugin
reports the reference declared by the source, never a conclusion of its own.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

UNKNOWN = "unknown"


class Finding(str, Enum):
    """What the plugin is allowed to say about one record."""

    DATA_PRESENT = "DATA_PRESENT"
    AREA_INTERSECTS_DATA = "AREA_INTERSECTS_DATA"
    OFFICIAL_INFORMATION = "OFFICIAL_INFORMATION"
    LEGAL_REFERENCE_PRESENT = "LEGAL_REFERENCE_PRESENT"

    @property
    def label_it(self) -> str:
        """Italian wording used in the dossier."""
        return {
            Finding.DATA_PRESENT: "dato cartografico presente",
            Finding.AREA_INTERSECTS_DATA: "l'area interseca il dato cartografico",
            Finding.OFFICIAL_INFORMATION: "informazione da fonte ufficiale",
            Finding.LEGAL_REFERENCE_PRESENT:
                "il dato riporta il riferimento di un atto",
        }[self]


class DataGap(str, Enum):
    """Why a theme produced no record. These are **not** interchangeable.

    "No cultural asset was found" never means "there is no cultural asset".
    """

    #: The source answered and there is genuinely nothing in the area.
    NO_FEATURE_FOUND = "NO_FEATURE_FOUND"
    #: No source at all is configured for this theme.
    NO_DATA = "NO_DATA"
    #: The source is configured but did not answer.
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    #: The area falls outside the declared coverage of the source.
    SOURCE_OUTSIDE_COVERAGE = "SOURCE_OUTSIDE_COVERAGE"
    #: The request failed (schema, CRS, unreadable geometries).
    QUERY_FAILED = "QUERY_FAILED"
    #: The source exists in the catalogue but is not operational.
    SOURCE_NOT_VERIFIED = "SOURCE_NOT_VERIFIED"

    @property
    def label_it(self) -> str:
        """Italian wording used in the dossier."""
        return {
            DataGap.NO_FEATURE_FOUND: "nessun elemento nell'area",
            DataGap.NO_DATA: "nessuna fonte configurata per il tema",
            DataGap.SOURCE_UNAVAILABLE: "fonte non disponibile",
            DataGap.SOURCE_OUTSIDE_COVERAGE: "area fuori dalla copertura della fonte",
            DataGap.QUERY_FAILED: "interrogazione non riuscita",
            DataGap.SOURCE_NOT_VERIFIED: "fonte censita ma non operativa",
        }[self]

    @property
    def means_absence(self) -> bool:
        """Whether this gap may be read as "there is nothing here"."""
        return self is DataGap.NO_FEATURE_FOUND


@dataclass
class ActReference:
    """The administrative act a feature declares, as the source declares it."""

    law: str = ""
    document: str = ""
    date: str = ""
    authority: str = ""
    code: str = ""

    @property
    def present(self) -> bool:
        """Whether the source carries any usable reference at all."""
        return any((self.law, self.document, self.date, self.code))

    def label_it(self) -> str:
        """One line for the dossier, built only from what the source really says."""
        parts = [part for part in (self.law, self.document, self.date) if part]
        if self.authority:
            parts.append(f"ente: {self.authority}")
        if self.code:
            parts.append(f"codice: {self.code}")
        return " - ".join(parts)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ActReference":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class Superintendency:
    """A competent office of the Ministry, as published by the Ministry itself."""

    code: str = ""
    name: str = UNKNOWN
    office_type: str = ""
    website: str = ""
    pec: str = ""
    #: How the office was determined; only ``official_layer`` is a real determination.
    determined_by: str = UNKNOWN

    @property
    def known(self) -> bool:
        """Whether an actual office was determined."""
        return bool(self.code or (self.name and self.name != UNKNOWN))

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Superintendency":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class CulturalAsset:
    """One normalised cultural or landscape record.

    Every field is filled **only** from what a source actually published. Missing values
    stay ``unknown`` rather than being guessed: a plausible invention is worse than a gap,
    because the reader cannot tell the two apart.
    """

    global_id: str = ""
    name: str = UNKNOWN
    asset_type: str = UNKNOWN
    category: str = ""
    subcategory: str = ""
    municipality: str = UNKNOWN
    province: str = UNKNOWN
    region: str = UNKNOWN
    source_id: str = ""
    source_name: str = ""
    source_feature_id: str = ""
    source_url: str = ""
    authority: str = ""
    superintendency: Superintendency = field(default_factory=Superintendency)
    act: ActReference = field(default_factory=ActReference)
    legal_reference: str = ""
    evidence_level: str = "cartographic"
    data_nature: str = "thematic"
    verification_status: str = "declared"
    findings: List[str] = field(default_factory=list)
    intersects: bool = False
    intersect_area_m2: float = 0.0
    intersect_pct: float = 0.0
    distance_m: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    #: Ids of the records merged into this one by the deduplicator.
    merged_with: List[str] = field(default_factory=list)
    #: How many distinct sources describe this same asset.
    source_count: int = 1

    @property
    def has_act(self) -> bool:
        """Whether the record declares an administrative act."""
        return self.act.present

    def statement_it(self) -> str:
        """The sentence the dossier is allowed to print about this record.

        It always says what was observed - an intersection with a dataset, or a distance -
        and names the source. It never says that the area *is* subject to a constraint.
        """
        if self.intersects:
            head = (f"L'area di progetto interseca il dato cartografico "
                    f"\"{self.name}\" della fonte {self.source_name}")
            if self.intersect_pct:
                head += f" ({self.intersect_pct:.1f}% dell'area)"
        elif self.distance_m is not None:
            head = (f"Il dato cartografico \"{self.name}\" della fonte "
                    f"{self.source_name} si trova a {self.distance_m:.0f} m dall'area")
        else:
            head = (f"Il dato cartografico \"{self.name}\" e' presente nel contesto "
                    f"dell'area (fonte {self.source_name})")
        if self.has_act:
            head += (f". La fonte riporta il riferimento: {self.act.label_it()}"
                     f" (da verificare presso l'ente competente)")
        return head + "."

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        payload = asdict(self)
        payload["superintendency"] = self.superintendency.as_dict()
        payload["act"] = self.act.as_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CulturalAsset":
        """Rebuild from :meth:`as_dict` output."""
        data = dict(payload or {})
        data["superintendency"] = Superintendency.from_dict(data.get("superintendency"))
        data["act"] = ActReference.from_dict(data.get("act"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ThemeOutcome:
    """What happened for one taxonomy sub-category."""

    category: str
    label: str = ""
    assets: List[CulturalAsset] = field(default_factory=list)
    sources_queried: List[str] = field(default_factory=list)
    sources_failed: List[str] = field(default_factory=list)
    sources_catalogued: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    coverage_notes: List[str] = field(default_factory=list)

    @property
    def intersecting(self) -> List[CulturalAsset]:
        """Records the project area actually intersects."""
        return [asset for asset in self.assets if asset.intersects]

    @property
    def nearby(self) -> List[CulturalAsset]:
        """Records that do not intersect but were found within the search radius."""
        return [asset for asset in self.assets if not asset.intersects]

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "category": self.category,
            "label": self.label,
            "assets": [asset.as_dict() for asset in self.assets],
            "sources_queried": list(self.sources_queried),
            "sources_failed": list(self.sources_failed),
            "sources_catalogued": list(self.sources_catalogued),
            "gaps": list(self.gaps),
            "coverage_notes": list(self.coverage_notes),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ThemeOutcome":
        """Rebuild from :meth:`as_dict` output."""
        data = payload or {}
        return cls(
            category=data.get("category", ""),
            label=data.get("label", ""),
            assets=[CulturalAsset.from_dict(a) for a in data.get("assets", [])],
            sources_queried=list(data.get("sources_queried", [])),
            sources_failed=list(data.get("sources_failed", [])),
            sources_catalogued=list(data.get("sources_catalogued", [])),
            gaps=list(data.get("gaps", [])),
            coverage_notes=list(data.get("coverage_notes", [])),
        )


@dataclass
class CulturalHeritageOutcome:
    """Everything the module produced for one project area."""

    themes: List[ThemeOutcome] = field(default_factory=list)
    superintendencies: List[Superintendency] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: ``{gap: [category, ...]}`` - what could not be assessed and why.
    gap_summary: Dict[str, List[str]] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def assets(self) -> List[CulturalAsset]:
        """Every normalised record, across themes."""
        return [asset for theme in self.themes for asset in theme.assets]

    @property
    def intersecting(self) -> List[CulturalAsset]:
        """Every record the area intersects."""
        return [asset for asset in self.assets if asset.intersects]

    @property
    def with_act(self) -> List[CulturalAsset]:
        """Records that declare an administrative act."""
        return [asset for asset in self.assets if asset.has_act]

    def theme(self, category: str) -> Optional[ThemeOutcome]:
        """Return the outcome of one sub-category."""
        for theme in self.themes:
            if theme.category == category:
                return theme
        return None

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "themes": [theme.as_dict() for theme in self.themes],
            "superintendencies": [s.as_dict() for s in self.superintendencies],
            "warnings": list(self.warnings),
            "gap_summary": {k: list(v) for k, v in self.gap_summary.items()},
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CulturalHeritageOutcome":
        """Rebuild from :meth:`as_dict` output."""
        data = payload or {}
        return cls(
            themes=[ThemeOutcome.from_dict(t) for t in data.get("themes", [])],
            superintendencies=[Superintendency.from_dict(s)
                               for s in data.get("superintendencies", [])],
            warnings=list(data.get("warnings", [])),
            gap_summary={k: list(v) for k, v in (data.get("gap_summary") or {}).items()},
            elapsed_ms=int(data.get("elapsed_ms", 0) or 0),
        )
