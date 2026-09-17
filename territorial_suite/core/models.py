"""Shared domain model.

Every object here is a plain, JSON-serialisable dataclass: results travel from worker
threads to the GUI, to Processing outputs, to the report engine and to disk
(``analysis.json``) without any QGIS object in between. Geometries are carried as WKT.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- enums


class EvidenceLevel(str, Enum):
    """How strong the evidence carried by a dataset is.

    This is the guardrail that stops the plugin from turning a map layer into a legal
    qualification: it is displayed next to every finding.
    """

    #: Knowledge-level cartography: it shows *where to look*, it does not establish a right.
    CARTOGRAPHIC = "cartographic"
    #: Declaratory dataset published by the competent authority (still to be verified on deed).
    DECLARATORY = "declaratory"
    #: Constraint ascertained by an administrative act referenced in the dataset.
    VERIFIED_ACT = "verified_act"

    @property
    def label_it(self) -> str:
        """Italian wording used in the UI and in the dossier."""
        return {
            EvidenceLevel.CARTOGRAPHIC: "dato cartografico conoscitivo",
            EvidenceLevel.DECLARATORY: "dato dichiarativo dell'ente competente",
            EvidenceLevel.VERIFIED_ACT: "vincolo riferito ad atto amministrativo",
        }[self]


class AlertLevel(str, Enum):
    """Semantic classification of an alert - never a legal verdict."""

    INFO = "INFO"
    ATTENTION = "ATTENTION"
    CHECK_REQUIRED = "CHECK_REQUIRED"
    CARTOGRAPHIC_ISSUE = "CARTOGRAPHIC_ISSUE"

    @property
    def label_it(self) -> str:
        """Italian wording used in the UI and in the dossier."""
        return {
            AlertLevel.INFO: "INFORMAZIONE",
            AlertLevel.ATTENTION: "ATTENZIONE",
            AlertLevel.CHECK_REQUIRED: "VERIFICA",
            AlertLevel.CARTOGRAPHIC_ISSUE: "CRITICITA CARTOGRAFICA",
        }[self]

    @property
    def rank(self) -> int:
        """Sort order for the alert list (higher first)."""
        return {
            AlertLevel.CHECK_REQUIRED: 3,
            AlertLevel.ATTENTION: 2,
            AlertLevel.CARTOGRAPHIC_ISSUE: 1,
            AlertLevel.INFO: 0,
        }[self]


class DataNature(str, Enum):
    """What a dataset actually *is*, independently of how strong its evidence is.

    :class:`EvidenceLevel` answers "how much does this prove?"; this enum answers "what am
    I looking at?". Two layers can both be ``declaratory`` and yet mean very different
    things: the perimeter of a designated area is not the same object as the index card of
    a single listed asset, and neither is a setback band computed by the plugin itself.
    """

    #: Perimeter of an area (park, designated landscape, site).
    PERIMETER = "perimeter"
    #: Inventory of individual assets (points or small polygons), one record per asset.
    INVENTORY = "inventory"
    #: Table of administrative acts, with or without a geometry attached.
    ACT_INDEX = "act_index"
    #: Reference cartography used to locate things (cadastre, administrative units).
    REFERENCE_BASE = "reference_base"
    #: Layer computed by the plugin (buffers, setbacks, intersections).
    DERIVED = "derived"
    #: Aerial or satellite imagery.
    IMAGERY = "imagery"
    #: Elevation model and its derivatives.
    ELEVATION = "elevation"
    #: Any other thematic mapping.
    THEMATIC = "thematic"

    @property
    def label_it(self) -> str:
        """Italian wording used in the UI and in the dossier."""
        return {
            DataNature.PERIMETER: "perimetrazione di un ambito",
            DataNature.INVENTORY: "anagrafica di beni puntuali",
            DataNature.ACT_INDEX: "elenco di atti amministrativi",
            DataNature.REFERENCE_BASE: "cartografia di riferimento",
            DataNature.DERIVED: "elaborazione prodotta dal plugin",
            DataNature.IMAGERY: "immagine aerea o satellitare",
            DataNature.ELEVATION: "modello altimetrico",
            DataNature.THEMATIC: "cartografia tematica",
        }[self]

    @classmethod
    def parse(cls, value: Any, default: "DataNature" = None) -> "DataNature":
        """Parse a nature string, falling back to ``default`` when unknown."""
        fallback = default or cls.THEMATIC
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return fallback


class VerificationStatus(str, Enum):
    """How far the plugin can vouch for a source descriptor.

    The rule that governs the whole catalogue: **only a source the plugin can actually
    talk to is offered to the user as operational**. Everything else stays in the
    catalogue as documentation of what exists, so that the gap is visible instead of
    being silently filled with an invented endpoint.
    """

    #: Endpoint queried for real, with the evidence recorded in ``verification_note``.
    VERIFIED = "verified"
    #: Documented and reachable, but no recorded live query for this exact layer.
    DECLARED = "declared"
    #: Known to exist as a dataset, no technical endpoint yet: catalogued only.
    PLANNED = "planned"
    #: Endpoint known but its answer could not be validated (schema, CRS, timeouts).
    UNVERIFIED = "unverified"
    #: Only discoverable at runtime (GetCapabilities on a portal that changes).
    DISCOVERY_REQUIRED = "discovery_required"
    #: Verified as broken or withdrawn; kept so that it is not proposed again.
    UNAVAILABLE = "unavailable"

    @property
    def is_operational(self) -> bool:
        """Whether a source with this status may be queried during an analysis."""
        return self in (VerificationStatus.VERIFIED, VerificationStatus.DECLARED)

    @property
    def label_it(self) -> str:
        """Italian wording used in the UI and in the dossier."""
        return {
            VerificationStatus.VERIFIED: "verificata sul servizio",
            VerificationStatus.DECLARED: "dichiarata dall'ente",
            VerificationStatus.PLANNED: "censita, non ancora operativa",
            VerificationStatus.UNVERIFIED: "non verificabile tecnicamente",
            VerificationStatus.DISCOVERY_REQUIRED: "richiede discovery del servizio",
            VerificationStatus.UNAVAILABLE: "non disponibile",
        }[self]

    @classmethod
    def parse(cls, value: Any, default: "VerificationStatus" = None) -> "VerificationStatus":
        """Parse a status string, falling back to ``default`` when unknown."""
        fallback = default or cls.DECLARED
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return fallback


class SourceStatus(str, Enum):
    """Runtime status of a data source."""

    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    EMPTY = "EMPTY"
    UNKNOWN = "UNKNOWN"
    DISABLED = "DISABLED"
    CACHED = "CACHED"
    SKIPPED = "SKIPPED"


class SourceType(str, Enum):
    """Access protocol of a data source."""

    WFS = "WFS"
    WMS = "WMS"
    WMTS = "WMTS"
    XYZ = "XYZ"
    OGCAPI = "OGCAPI"
    GEOJSON = "GEOJSON"
    GPKG = "GPKG"
    FILE = "FILE"
    REST = "REST"
    STAC = "STAC"
    OVERPASS = "OVERPASS"
    RASTER = "RASTER"
    ARCGIS_FEATURE = "ARCGIS_FEATURE"
    ARCGIS_MAP = "ARCGIS_MAP"
    #: Tiled GeoTIFF elevation pyramid (Mapzen scheme), consumed by the terrain engine.
    TERRAIN_TILES = "TERRAIN_TILES"

    @classmethod
    def parse(cls, value: str) -> "SourceType":
        """Parse a type string, raising ``ValueError`` when unknown."""
        return cls(str(value).strip().upper())

    @property
    def is_vector_query(self) -> bool:
        """Whether features can be downloaded and measured from this source."""
        return self in (SourceType.WFS, SourceType.OGCAPI, SourceType.GEOJSON,
                        SourceType.GPKG, SourceType.FILE, SourceType.OVERPASS,
                        SourceType.ARCGIS_FEATURE)

    @property
    def is_basemap(self) -> bool:
        """Whether the source is a background/raster service."""
        return self in (SourceType.WMS, SourceType.WMTS, SourceType.XYZ,
                        SourceType.RASTER, SourceType.ARCGIS_MAP)


def utc_now() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _clean(payload: Any) -> Any:
    """Recursively convert enums and non-serialisable values for JSON export."""
    if isinstance(payload, Enum):
        return payload.value
    if isinstance(payload, dict):
        return {key: _clean(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_clean(item) for item in payload]
    if isinstance(payload, float) and payload == float("inf"):
        return None
    return payload


# --------------------------------------------------------------------------- provenance


@dataclass
class Provenance:
    """Where a piece of data comes from and how it was processed."""

    source_id: str = ""
    source_name: str = ""
    authority: str = ""
    url: str = ""
    layer: str = ""
    retrieved_at: str = field(default_factory=utc_now)
    crs: str = ""
    operation: str = ""
    inputs: List[str] = field(default_factory=list)
    license: str = ""
    attribution: str = ""
    metadata_url: str = ""
    scale: str = ""
    accuracy_m: Optional[float] = None
    update_frequency: str = ""
    last_verified: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.CARTOGRAPHIC
    data_nature: DataNature = DataNature.THEMATIC
    verification_status: VerificationStatus = VerificationStatus.DECLARED
    coverage_note: str = ""
    official: bool = False
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Provenance":
        """Rebuild from :meth:`as_dict` output."""
        data = dict(payload or {})
        level = data.get("evidence_level", EvidenceLevel.CARTOGRAPHIC)
        data["evidence_level"] = EvidenceLevel(level) if not isinstance(level, EvidenceLevel) else level
        data["data_nature"] = DataNature.parse(data.get("data_nature"))
        data["verification_status"] = VerificationStatus.parse(data.get("verification_status"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_json(self) -> str:
        """Serialise to a compact JSON string (used in layer custom properties)."""
        return json.dumps(self.as_dict(), ensure_ascii=False)


# --------------------------------------------------------------------------- admin


@dataclass
class AdminUnit:
    """An administrative unit intersecting the project area."""

    name: str
    level: str = "municipality"     # municipality | province | region | country
    istat_code: str = ""
    cadastral_code: str = ""        # Italian "codice catastale" (e.g. D612)
    province_code: str = ""
    region_name: str = ""
    area_share_pct: float = 0.0     # share of the project area falling in this unit
    source_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AdminUnit":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class AdminUnits:
    """Administrative framing of a project area."""

    municipalities: List[AdminUnit] = field(default_factory=list)
    provinces: List[AdminUnit] = field(default_factory=list)
    regions: List[AdminUnit] = field(default_factory=list)
    resolved: bool = False
    note: str = ""

    @property
    def is_multi_municipality(self) -> bool:
        """Whether the area spans more than one municipality."""
        return len(self.municipalities) > 1

    def summary(self) -> str:
        """Return a one-line human summary."""
        if not self.municipalities:
            return "unita amministrative non determinate"
        towns = ", ".join(unit.name for unit in self.municipalities)
        regions = ", ".join(sorted({unit.region_name or "" for unit in self.municipalities} - {""}))
        return f"{towns}{' (' + regions + ')' if regions else ''}"

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "municipalities": [u.as_dict() for u in self.municipalities],
            "provinces": [u.as_dict() for u in self.provinces],
            "regions": [u.as_dict() for u in self.regions],
            "resolved": self.resolved,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AdminUnits":
        """Rebuild from :meth:`as_dict` output."""
        data = payload or {}
        return cls(
            municipalities=[AdminUnit.from_dict(u) for u in data.get("municipalities", [])],
            provinces=[AdminUnit.from_dict(u) for u in data.get("provinces", [])],
            regions=[AdminUnit.from_dict(u) for u in data.get("regions", [])],
            resolved=bool(data.get("resolved", False)),
            note=data.get("note", ""),
        )


# --------------------------------------------------------------------------- results


@dataclass
class FeatureHit:
    """A single feature of a source that interacts with the project area."""

    fid: str = ""
    label: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    intersect_area_m2: float = 0.0
    intersect_pct: float = 0.0
    distance_m: float = 0.0
    geometry_wkt: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FeatureHit":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class SourceResult:
    """Outcome of querying one data source for one project area."""

    source_id: str
    source_name: str = ""
    category: str = ""
    status: SourceStatus = SourceStatus.UNKNOWN
    present: bool = False
    feature_count: int = 0
    intersect_area_m2: float = 0.0
    intersect_pct: float = 0.0
    min_distance_m: Optional[float] = None
    hits: List[FeatureHit] = field(default_factory=list)
    layer_uri: str = ""
    layer_name: str = ""
    provenance: Optional[Provenance] = None
    error: str = ""
    elapsed_ms: int = 0
    from_cache: bool = False

    @property
    def evidence_level(self) -> EvidenceLevel:
        """Evidence level inherited from the source descriptor."""
        return self.provenance.evidence_level if self.provenance else EvidenceLevel.CARTOGRAPHIC

    @property
    def ok(self) -> bool:
        """Whether the query succeeded (with or without features)."""
        return self.status in (SourceStatus.ONLINE, SourceStatus.CACHED,
                               SourceStatus.EMPTY, SourceStatus.DEGRADED)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        payload = asdict(self)
        payload["provenance"] = self.provenance.as_dict() if self.provenance else None
        return _clean(payload)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SourceResult":
        """Rebuild from :meth:`as_dict` output."""
        data = dict(payload or {})
        data["status"] = SourceStatus(data.get("status", SourceStatus.UNKNOWN))
        data["hits"] = [FeatureHit.from_dict(h) for h in data.get("hits", [])]
        prov = data.get("provenance")
        data["provenance"] = Provenance.from_dict(prov) if prov else None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Alert:
    """A classified observation produced by the rule engine."""

    level: AlertLevel
    code: str
    title: str
    detail: str = ""
    category: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.CARTOGRAPHIC
    source_ids: List[str] = field(default_factory=list)
    values: Dict[str, Any] = field(default_factory=dict)
    legal_reference: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Alert":
        """Rebuild from :meth:`as_dict` output."""
        data = dict(payload or {})
        data["level"] = AlertLevel(data.get("level", AlertLevel.INFO))
        data["evidence_level"] = EvidenceLevel(data.get("evidence_level", EvidenceLevel.CARTOGRAPHIC))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class SlopeClass:
    """One slope class and its share of the project area."""

    lower: float
    upper: Optional[float]
    pixel_count: int = 0
    area_m2: float = 0.0
    area_pct: float = 0.0

    @property
    def label(self) -> str:
        """Human label such as ``10-20 %`` or ``>50 %``."""
        if self.upper is None:
            return f">{self.lower:g} %"
        return f"{self.lower:g}-{self.upper:g} %"

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        payload = _clean(asdict(self))
        payload["label"] = self.label
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SlopeClass":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class TerrainStats:
    """Elevation and slope statistics over the project area."""

    source_id: str = ""
    cell_size_m: float = 0.0
    elevation_min: Optional[float] = None
    elevation_max: Optional[float] = None
    elevation_mean: Optional[float] = None
    elevation_median: Optional[float] = None
    elevation_range: Optional[float] = None
    elevation_std: Optional[float] = None
    slope_mean_pct: Optional[float] = None
    slope_max_pct: Optional[float] = None
    slope_classes: List[SlopeClass] = field(default_factory=list)
    aspect_histogram: Dict[str, float] = field(default_factory=dict)
    elevation_histogram: List[Dict[str, float]] = field(default_factory=list)
    dem_path: str = ""
    slope_path: str = ""
    aspect_path: str = ""
    hillshade_path: str = ""
    #: Flattened "colour ramp x hillshade" GeoTIFFs, keyed by theme (dem/slope/aspect).
    #: Layouts and packages use these because print and PDF export drop blend modes.
    shaded_paths: Dict[str, str] = field(default_factory=dict)
    contours_path: str = ""
    provenance: Optional[Provenance] = None
    note: str = ""

    @property
    def available(self) -> bool:
        """Whether any elevation statistic could be computed."""
        return self.elevation_min is not None

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        payload = asdict(self)
        payload["slope_classes"] = [c.as_dict() for c in self.slope_classes]
        payload["provenance"] = self.provenance.as_dict() if self.provenance else None
        return _clean(payload)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TerrainStats":
        """Rebuild from :meth:`as_dict` output."""
        data = dict(payload or {})
        data["slope_classes"] = [SlopeClass.from_dict(c) for c in data.get("slope_classes", [])]
        prov = data.get("provenance")
        data["provenance"] = Provenance.from_dict(prov) if prov else None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class CadastralRow:
    """One cadastral parcel intersecting the project area."""

    municipality: str = ""
    istat_code: str = ""
    cadastral_code: str = ""
    sheet: str = ""
    parcel: str = ""
    national_ref: str = ""
    area_cadastral_m2: float = 0.0
    area_intersect_m2: float = 0.0
    intersect_pct: float = 0.0
    geometry_wkt: str = ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CadastralRow":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class LayerRef:
    """A layer produced by the plugin (kept out of QGIS objects for serialisation)."""

    name: str
    uri: str = ""
    provider: str = "ogr"
    category: str = ""
    group: str = ""
    style: str = ""
    source_id: str = ""
    is_raster: bool = False

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "LayerRef":
        """Rebuild from :meth:`as_dict` output."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (payload or {}).items() if k in known})


@dataclass
class AnalysisReport:
    """Complete outcome of a territorial analysis - the plugin's central artefact."""

    area: Dict[str, Any] = field(default_factory=dict)          # ProjectArea.as_dict()
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""
    results: List[SourceResult] = field(default_factory=list)
    #: Results of the bulk download step, kept apart from the analysis results: a
    #: downloaded dataset says "these features are near the area", not "this dataset
    #: interacts with the area", and rules must not confuse the two.
    downloads: List[SourceResult] = field(default_factory=list)
    alerts: List[Alert] = field(default_factory=list)
    cadastre: List[CadastralRow] = field(default_factory=list)
    terrain: Optional[TerrainStats] = None
    admin: AdminUnits = field(default_factory=AdminUnits)
    layers: List[LayerRef] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    settings_snapshot: Dict[str, Any] = field(default_factory=dict)
    #: Output of the thematic engines that build on the shared results, keyed by module
    #: name (``cultural_heritage``, ...). Kept as plain JSON so that ``core`` never has to
    #: know about ``engines``: each module owns the shape of its own entry.
    modules: Dict[str, Any] = field(default_factory=dict)
    plugin_version: str = ""
    qgis_version: str = ""

    def module(self, name: str) -> Dict[str, Any]:
        """Return the payload a thematic engine attached, or an empty dictionary."""
        value = self.modules.get(name)
        return value if isinstance(value, dict) else {}

    @property
    def all_results(self) -> List[SourceResult]:
        """Analysis results plus download results (used by the Sources panel)."""
        return list(self.results) + list(self.downloads)

    @property
    def sources_ok(self) -> List[SourceResult]:
        """Sources that answered."""
        return [r for r in self.all_results if r.ok]

    @property
    def sources_failed(self) -> List[SourceResult]:
        """Sources that did not answer."""
        return [r for r in self.all_results if not r.ok]

    @property
    def present_results(self) -> List[SourceResult]:
        """Sources with at least one feature interacting with the area."""
        return [r for r in self.results if r.present]

    def by_category(self) -> Dict[str, List[SourceResult]]:
        """Group results by taxonomy category."""
        grouped: Dict[str, List[SourceResult]] = {}
        for result in self.results:
            grouped.setdefault(result.category or "other", []).append(result)
        return grouped

    def sorted_alerts(self) -> List[Alert]:
        """Return alerts ordered by severity rank, then title."""
        return sorted(self.alerts, key=lambda a: (-a.level.rank, a.title))

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return _clean({
            "area": self.area,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": [r.as_dict() for r in self.results],
            "downloads": [r.as_dict() for r in self.downloads],
            "alerts": [a.as_dict() for a in self.alerts],
            "cadastre": [c.as_dict() for c in self.cadastre],
            "terrain": self.terrain.as_dict() if self.terrain else None,
            "admin": self.admin.as_dict(),
            "layers": [layer.as_dict() for layer in self.layers],
            "warnings": self.warnings,
            "settings_snapshot": self.settings_snapshot,
            "modules": self.modules,
            "plugin_version": self.plugin_version,
            "qgis_version": self.qgis_version,
        })

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AnalysisReport":
        """Rebuild from :meth:`as_dict` output."""
        data = payload or {}
        terrain = data.get("terrain")
        return cls(
            area=data.get("area", {}),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            results=[SourceResult.from_dict(r) for r in data.get("results", [])],
            downloads=[SourceResult.from_dict(r) for r in data.get("downloads", [])],
            alerts=[Alert.from_dict(a) for a in data.get("alerts", [])],
            cadastre=[CadastralRow.from_dict(c) for c in data.get("cadastre", [])],
            terrain=TerrainStats.from_dict(terrain) if terrain else None,
            admin=AdminUnits.from_dict(data.get("admin", {})),
            layers=[LayerRef.from_dict(item) for item in data.get("layers", [])],
            warnings=list(data.get("warnings", [])),
            settings_snapshot=data.get("settings_snapshot", {}),
            modules=dict(data.get("modules", {}) or {}),
            plugin_version=data.get("plugin_version", ""),
            qgis_version=data.get("qgis_version", ""),
        )

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise the whole report to JSON."""
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "AnalysisReport":
        """Parse a report previously written by :meth:`to_json`."""
        return cls.from_dict(json.loads(text))
