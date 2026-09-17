"""Data source catalogue.

Sources are *data*, never code: each one is a JSON descriptor under ``config/sources/``
(shipped) or ``<profile>/territorial_suite/sources/`` (user). The registry loads, validates,
merges and indexes them, and answers the only question the engines ask:

    "given this project area, which sources should I query for these categories?"
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import log
from .constants import PROP_SOURCE_OVERRIDES
from .errors import ConfigError
from .models import (
    AdminUnits,
    DataNature,
    EvidenceLevel,
    Provenance,
    SourceType,
    VerificationStatus,
    utc_now,
)
from .paths import config_dir, user_sources_dir
from .taxonomy import Taxonomy

LEVEL_EUROPEAN = "european"
LEVEL_NATIONAL = "national"
LEVEL_REGIONAL = "regional"
LEVEL_PROVINCIAL = "provincial"
LEVEL_MUNICIPAL = "municipal"
LEVEL_GLOBAL = "global"

#: Levels that always apply, whatever the administrative framing of the area.
UNIVERSAL_LEVELS = (LEVEL_GLOBAL, LEVEL_EUROPEAN, LEVEL_NATIONAL)


def normalise(text: str) -> str:
    """Lowercase, strip accents and punctuation - used to match place names."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in ascii_text.lower() if ch.isalnum())


def _default_nature(source_type: SourceType) -> DataNature:
    """Guess the nature of a dataset from its protocol, for descriptors written before
    ``data_nature`` existed: an imagery service is imagery whatever the JSON says."""
    if source_type in (SourceType.TERRAIN_TILES, SourceType.RASTER):
        return DataNature.ELEVATION
    if source_type.is_basemap:
        return DataNature.IMAGERY
    return DataNature.THEMATIC


@dataclass
class SourceScope:
    """Territorial validity of a source."""

    level: str = LEVEL_NATIONAL
    codes: List[str] = field(default_factory=list)

    def matches(self, admin: AdminUnits) -> bool:
        """Return ``True`` when the source applies to the given administrative framing."""
        level = (self.level or LEVEL_NATIONAL).lower()
        if level in UNIVERSAL_LEVELS:
            return True
        if not self.codes:
            return True
        wanted = {normalise(code) for code in self.codes if code}
        if not wanted:
            return True
        candidates: set = set()
        groups = {
            LEVEL_REGIONAL: list(admin.regions) + [u for u in admin.municipalities],
            LEVEL_PROVINCIAL: list(admin.provinces) + [u for u in admin.municipalities],
            LEVEL_MUNICIPAL: list(admin.municipalities),
        }
        for unit in groups.get(level, []):
            candidates.update({
                normalise(unit.name),
                normalise(unit.istat_code),
                normalise(unit.cadastral_code),
                normalise(unit.province_code),
                normalise(unit.region_name),
            })
        candidates.discard("")
        return bool(candidates & wanted)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {"level": self.level, "codes": list(self.codes)}


@dataclass
class SourceQuality:
    """Declared quality of a dataset, used to rank fallbacks and to warn the user.

    ``score`` is a coarse 0-100 hint written in the descriptor, not a measurement: it only
    orders alternatives that map the same thing. The textual fields are what actually ends
    up in the dossier.
    """

    score: int = 50
    completeness: str = ""
    currency: str = ""
    positional_accuracy_m: Optional[float] = None
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: Any) -> "SourceQuality":
        """Build from a JSON fragment, tolerating a bare number or a missing block."""
        if isinstance(payload, (int, float)):
            return cls(score=int(payload))
        data = payload if isinstance(payload, dict) else {}
        accuracy = data.get("positional_accuracy_m")
        return cls(
            score=max(0, min(100, int(data.get("score", 50)))),
            completeness=str(data.get("completeness", "")),
            currency=str(data.get("currency", "")),
            positional_accuracy_m=float(accuracy) if accuracy not in (None, "") else None,
            notes=str(data.get("notes", "")),
        )

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "score": self.score,
            "completeness": self.completeness,
            "currency": self.currency,
            "positional_accuracy_m": self.positional_accuracy_m,
            "notes": self.notes,
        }


@dataclass
class SourceCoverage:
    """Real territorial coverage, as opposed to the nominal :class:`SourceScope`.

    A national service is not automatically a complete one: SITAP publishes some themes for
    a handful of regions only. ``scope`` decides *whether the source is asked*, ``coverage``
    explains *what the silence means* when it answers nothing.
    """

    completeness: str = "unknown"      # complete | partial | unknown
    covered: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: Any) -> "SourceCoverage":
        """Build from a JSON fragment, tolerating a bare string or a missing block."""
        data = {"completeness": payload} if isinstance(payload, str) else payload
        data = data if isinstance(data, dict) else {}
        completeness = str(data.get("completeness", "unknown")).strip().lower()
        if completeness not in ("complete", "partial", "unknown"):
            completeness = "unknown"
        return cls(
            completeness=completeness,
            covered=[str(item) for item in data.get("covered", []) if item],
            missing=[str(item) for item in data.get("missing", []) if item],
            notes=str(data.get("notes", "")),
        )

    @property
    def is_partial(self) -> bool:
        """Whether an empty answer may mean "not mapped here" rather than "nothing here"."""
        return self.completeness != "complete"

    def label_it(self) -> str:
        """Italian one-liner for the dossier."""
        base = {
            "complete": "copertura completa sull'ambito dichiarato",
            "partial": "copertura parziale",
            "unknown": "copertura non dichiarata",
        }[self.completeness]
        if self.completeness == "partial" and self.covered:
            base = f"{base} ({', '.join(self.covered)})"
        return base

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "completeness": self.completeness,
            "covered": list(self.covered),
            "missing": list(self.missing),
            "notes": self.notes,
        }


@dataclass
class DataSource:
    """A queryable territorial data source, described entirely by configuration."""

    id: str
    name: str
    type: SourceType
    url: str = ""
    layer: str = ""
    authority: str = ""
    country: str = "IT"
    scope: SourceScope = field(default_factory=SourceScope)
    category: str = ""
    subcategory: str = ""
    crs: List[str] = field(default_factory=list)
    geometry: str = ""
    query: Dict[str, Any] = field(default_factory=dict)
    fields: Dict[str, str] = field(default_factory=dict)
    evidence_level: EvidenceLevel = EvidenceLevel.CARTOGRAPHIC
    data_nature: DataNature = DataNature.THEMATIC
    verification_status: VerificationStatus = VerificationStatus.DECLARED
    verification_note: str = ""
    quality: SourceQuality = field(default_factory=SourceQuality)
    coverage: SourceCoverage = field(default_factory=SourceCoverage)
    fallback_sources: List[str] = field(default_factory=list)
    healthcheck: Dict[str, Any] = field(default_factory=dict)
    documentation_url: str = ""
    legal_reference: str = ""
    update_frequency: str = ""
    last_verified: str = ""
    license: str = ""
    attribution: str = ""
    official: bool = False
    metadata_url: str = ""
    scale: str = ""
    accuracy_m: Optional[float] = None
    enabled: bool = True
    priority: int = 100
    style: str = ""
    group: str = ""
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    origin: str = "builtin"          # builtin | user | project
    descriptor_path: str = ""

    # ------------------------------------------------------------------ parsing

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], *, origin: str = "builtin",
                  path: str = "") -> "DataSource":
        """Build a source from a JSON descriptor, validating the mandatory fields."""
        data = {k: v for k, v in (payload or {}).items() if not k.startswith("_")}
        source_id = str(data.get("id", "")).strip()
        if not source_id:
            raise ConfigError(f"Source descriptor without id ({path or 'inline'})")
        name = str(data.get("name", "")).strip() or source_id
        try:
            source_type = SourceType.parse(data.get("type", ""))
        except ValueError as exc:
            raise ConfigError(f"Source {source_id}: unknown type "
                              f"'{data.get('type')}'", detail=str(exc)) from exc
        url = str(data.get("url", "")).strip()
        if not url and source_type not in (SourceType.FILE, SourceType.GPKG):
            raise ConfigError(f"Source {source_id}: missing url")
        if url and not url.startswith(("http://", "https://", "file://")):
            raise ConfigError(f"Source {source_id}: unsupported url scheme")
        evidence_raw = data.get("evidence_level", EvidenceLevel.CARTOGRAPHIC.value)
        try:
            evidence = EvidenceLevel(evidence_raw)
        except ValueError:
            log.warning(f"Source {source_id}: unknown evidence_level '{evidence_raw}', "
                        f"falling back to 'cartographic'")
            evidence = EvidenceLevel.CARTOGRAPHIC
        scope_raw = data.get("scope") or {}
        scope = SourceScope(
            level=str(scope_raw.get("level", LEVEL_NATIONAL)).lower(),
            codes=[str(code) for code in scope_raw.get("codes", []) if code],
        )
        accuracy = data.get("accuracy_m")
        return cls(
            id=source_id,
            name=name,
            type=source_type,
            url=url,
            layer=str(data.get("layer", "")),
            authority=str(data.get("authority", "")),
            country=str(data.get("country", "IT")),
            scope=scope,
            category=str(data.get("category", "")),
            subcategory=str(data.get("subcategory", "")),
            crs=[str(code) for code in data.get("crs", []) if code],
            geometry=str(data.get("geometry", "")),
            query=dict(data.get("query", {})),
            fields=dict(data.get("fields", {})),
            evidence_level=evidence,
            data_nature=DataNature.parse(data.get("data_nature"),
                                         _default_nature(source_type)),
            verification_status=VerificationStatus.parse(data.get("verification_status")),
            verification_note=str(data.get("verification_note", "")),
            quality=SourceQuality.from_dict(data.get("quality")),
            coverage=SourceCoverage.from_dict(data.get("coverage")),
            fallback_sources=[str(item) for item in data.get("fallback_sources", []) if item],
            healthcheck=dict(data.get("healthcheck", {})),
            documentation_url=str(data.get("documentation_url", "")),
            legal_reference=str(data.get("legal_reference", "")),
            update_frequency=str(data.get("update_frequency", "")),
            last_verified=str(data.get("last_verified", "")),
            license=str(data.get("license", "")),
            attribution=str(data.get("attribution", "")),
            official=bool(data.get("official", False)),
            metadata_url=str(data.get("metadata_url", "")),
            scale=str(data.get("scale", "")),
            accuracy_m=float(accuracy) if accuracy not in (None, "") else None,
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 100)),
            style=str(data.get("style", "")),
            group=str(data.get("group", "")),
            tags=[str(tag) for tag in data.get("tags", [])],
            notes=str(data.get("notes", "")),
            origin=origin,
            descriptor_path=path,
        )

    def merged_with(self, overlay: Dict[str, Any], *, origin: str) -> "DataSource":
        """Return a copy of this source with ``overlay`` fields applied."""
        base = self.as_dict()
        for key, value in (overlay or {}).items():
            if key.startswith("_"):
                continue
            if key in ("scope", "quality", "coverage") and isinstance(value, dict):
                base[key] = {**(base.get(key) or {}), **value}
            elif key in ("query", "fields", "healthcheck") and isinstance(value, dict):
                base[key] = {**base.get(key, {}), **value}
            else:
                base[key] = value
        return DataSource.from_dict(base, origin=origin, path=self.descriptor_path)

    # ------------------------------------------------------------------ helpers

    @property
    def bbox_crs(self) -> str:
        """CRS used for spatial filters against this service."""
        return str(self.query.get("bbox_crs") or (self.crs[0] if self.crs else "EPSG:4326"))

    @property
    def page_size(self) -> int:
        """Number of features requested per page."""
        return int(self.query.get("page_size", 1000))

    @property
    def max_features(self) -> int:
        """Hard cap on the number of features retrieved from this source."""
        return int(self.query.get("max_features", 0))

    @property
    def label_field(self) -> str:
        """Attribute used as human label for a feature."""
        return str(self.fields.get("label", ""))

    def label_for(self, attributes: Dict[str, Any]) -> str:
        """Return the best available human label for a feature of this source."""
        for key in (self.fields.get("label"), self.fields.get("name"), "denominazione",
                    "nome", "name", "descrizione", "label"):
            if key and attributes.get(key) not in (None, ""):
                return str(attributes[key])
        code = self.fields.get("code")
        if code and attributes.get(code) not in (None, ""):
            return str(attributes[code])
        return ""

    def applies_to(self, admin: AdminUnits) -> bool:
        """Whether the source is territorially relevant for an area."""
        return self.scope.matches(admin)

    @property
    def is_operational(self) -> bool:
        """Whether the descriptor points at a service the plugin can really talk to.

        A catalogued-but-unverified source is deliberately *not* operational: it documents
        a dataset the plugin knows about and cannot reach, which is information, not a
        service. It stays visible in the Source Manager with its status. This is orthogonal
        to :attr:`enabled`, which is the user switching a working source off.
        """
        return self.verification_status.is_operational

    @property
    def nature_label_it(self) -> str:
        """One line describing what the dataset is and how much it proves."""
        return f"{self.data_nature.label_it} - {self.evidence_level.label_it}"

    def provenance(self, *, operation: str = "", crs: str = "",
                   inputs: Optional[Sequence[str]] = None) -> Provenance:
        """Build the provenance record for data coming from this source."""
        return Provenance(
            source_id=self.id,
            source_name=self.name,
            authority=self.authority,
            url=self.url,
            layer=self.layer,
            retrieved_at=utc_now(),
            crs=crs,
            operation=operation,
            inputs=list(inputs or []),
            license=self.license,
            attribution=self.attribution,
            metadata_url=self.metadata_url,
            scale=self.scale,
            accuracy_m=self.accuracy_m,
            update_frequency=self.update_frequency,
            last_verified=self.last_verified,
            evidence_level=self.evidence_level,
            data_nature=self.data_nature,
            verification_status=self.verification_status,
            coverage_note=self.coverage.label_it(),
            official=self.official,
            notes=self.notes,
        )

    def as_dict(self) -> Dict[str, Any]:
        """Return the descriptor as a JSON-friendly dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "url": self.url,
            "layer": self.layer,
            "authority": self.authority,
            "country": self.country,
            "scope": self.scope.as_dict(),
            "category": self.category,
            "subcategory": self.subcategory,
            "crs": list(self.crs),
            "geometry": self.geometry,
            "query": dict(self.query),
            "fields": dict(self.fields),
            "evidence_level": self.evidence_level.value,
            "data_nature": self.data_nature.value,
            "verification_status": self.verification_status.value,
            "verification_note": self.verification_note,
            "quality": self.quality.as_dict(),
            "coverage": self.coverage.as_dict(),
            "fallback_sources": list(self.fallback_sources),
            "healthcheck": dict(self.healthcheck),
            "documentation_url": self.documentation_url,
            "legal_reference": self.legal_reference,
            "update_frequency": self.update_frequency,
            "last_verified": self.last_verified,
            "license": self.license,
            "attribution": self.attribution,
            "official": self.official,
            "metadata_url": self.metadata_url,
            "scale": self.scale,
            "accuracy_m": self.accuracy_m,
            "enabled": self.enabled,
            "priority": self.priority,
            "style": self.style,
            "group": self.group,
            "tags": list(self.tags),
            "notes": self.notes,
        }


class DataSourceRegistry:
    """Loads and indexes the source catalogue."""

    _instance: Optional["DataSourceRegistry"] = None

    def __init__(self) -> None:
        self._sources: Dict[str, DataSource] = {}
        self._errors: List[str] = []
        self._loaded = False

    @classmethod
    def instance(cls) -> "DataSourceRegistry":
        """Return the shared registry, loading the catalogue on first use."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the shared registry (tests, or after the user edits the catalogue)."""
        cls._instance = None

    # ------------------------------------------------------------------ loading

    def load(self, *, extra_dirs: Optional[Iterable[Path]] = None) -> "DataSourceRegistry":
        """Load built-in descriptors, then user ones, then project overrides."""
        self._sources.clear()
        self._errors.clear()
        for path in self._descriptor_files(config_dir() / "sources"):
            self._load_file(path, origin="builtin")
        for path in self._descriptor_files(user_sources_dir()):
            self._load_file(path, origin="user")
        for folder in extra_dirs or []:
            for path in self._descriptor_files(Path(folder)):
                self._load_file(path, origin="user")
        self._apply_project_overrides()
        self._loaded = True
        log.info(f"Data source catalogue: {len(self._sources)} sources, {len(self._errors)} errors")
        return self

    @staticmethod
    def _descriptor_files(root: Path) -> List[Path]:
        if not root.exists():
            return []
        return sorted(p for p in root.rglob("*.json") if not p.name.startswith("_"))

    def _load_file(self, path: Path, *, origin: str) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._errors.append(f"{path.name}: {exc}")
            log.warning(f"Invalid source descriptor {path}: {exc}")
            return
        descriptors = payload if isinstance(payload, list) else payload.get("sources", [payload])
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            try:
                source = DataSource.from_dict(descriptor, origin=origin, path=str(path))
            except ConfigError as exc:
                self._errors.append(f"{path.name}: {exc}")
                log.warning(str(exc))
                continue
            existing = self._sources.get(source.id)
            if existing is not None and origin != "builtin":
                self._sources[source.id] = existing.merged_with(descriptor, origin=origin)
            else:
                self._sources[source.id] = source

    def _apply_project_overrides(self) -> None:
        """Apply per-project source overrides stored in the QGIS project file."""
        try:
            from qgis.core import QgsProject

            scope, _, key = PROP_SOURCE_OVERRIDES.partition("/")
            raw, ok = QgsProject.instance().readEntry(scope, key, "")
        except Exception:  # pragma: no cover - outside QGIS
            return
        if not ok or not raw:
            return
        try:
            overrides = json.loads(raw)
        except ValueError:  # pragma: no cover - corrupted project
            log.warning("Project source overrides are corrupted and were ignored")
            return
        for source_id, overlay in (overrides or {}).items():
            source = self._sources.get(source_id)
            if source is None or not isinstance(overlay, dict):
                continue
            try:
                self._sources[source_id] = source.merged_with(overlay, origin="project")
            except ConfigError as exc:  # pragma: no cover - user error path
                log.warning(f"Invalid project override for {source_id}: {exc}")

    def set_project_override(self, source_id: str, overlay: Dict[str, Any]) -> None:
        """Persist a per-project override for a source and apply it immediately."""
        try:
            from qgis.core import QgsProject

            project = QgsProject.instance()
            scope, _, key = PROP_SOURCE_OVERRIDES.partition("/")
            raw, ok = project.readEntry(scope, key, "")
            overrides = json.loads(raw) if ok and raw else {}
            overrides[source_id] = {**overrides.get(source_id, {}), **overlay}
            project.writeEntry(scope, key, json.dumps(overrides, ensure_ascii=False))
            project.setDirty(True)
        except Exception as exc:  # pragma: no cover - outside QGIS
            log.warning(f"Cannot store project override: {exc}")
            return
        source = self._sources.get(source_id)
        if source is not None:
            self._sources[source_id] = source.merged_with(overlay, origin="project")

    # ------------------------------------------------------------------ queries

    @property
    def errors(self) -> List[str]:
        """Descriptor errors found while loading (shown in the Source Manager)."""
        return list(self._errors)

    def all(self, *, enabled_only: bool = False) -> List[DataSource]:
        """Return every known source, ordered by priority then name."""
        sources = [s for s in self._sources.values() if s.enabled or not enabled_only]
        return sorted(sources, key=lambda s: (s.priority, s.name.lower()))

    def get(self, source_id: str) -> Optional[DataSource]:
        """Return a source by id."""
        return self._sources.get(source_id)

    def require(self, source_id: str) -> DataSource:
        """Return a source by id or raise :class:`ConfigError`."""
        source = self.get(source_id)
        if source is None:
            raise ConfigError(f"Unknown data source: {source_id}")
        return source

    @staticmethod
    def _expand_categories(categories: Sequence[str]) -> set:
        """Widen a category filter with the sub-categories of the taxonomy."""
        try:
            return set(Taxonomy.instance().expand(categories))
        except Exception as exc:  # pragma: no cover - broken user taxonomy
            log.warning(f"Taxonomy unavailable, categories not expanded: {exc}")
            return {c for c in categories if c}

    def query(self, *, categories: Optional[Sequence[str]] = None,
              types: Optional[Sequence[SourceType]] = None,
              levels: Optional[Sequence[str]] = None,
              tags: Optional[Sequence[str]] = None,
              enabled_only: bool = True,
              operational_only: bool = True) -> List[DataSource]:
        """Filter the catalogue.

        ``operational_only`` is the guardrail of the National Data Fabric: sources that are
        merely catalogued (``planned``, ``unverified``, ``discovery_required``) never reach
        an analysis, however enabled they are. Pass ``False`` to list the catalogue itself.
        """
        wanted_categories = self._expand_categories(categories or [])
        wanted_types = {SourceType(t) if not isinstance(t, SourceType) else t for t in (types or [])}
        wanted_levels = {str(level).lower() for level in (levels or [])}
        wanted_tags = {str(tag).lower() for tag in (tags or [])}
        result = []
        for source in self.all(enabled_only=enabled_only):
            if enabled_only and not source.enabled:
                continue
            if operational_only and not source.is_operational:
                continue
            if wanted_categories and source.category not in wanted_categories:
                continue
            if wanted_types and source.type not in wanted_types:
                continue
            if wanted_levels and (source.scope.level or "").lower() not in wanted_levels:
                continue
            if wanted_tags and not wanted_tags & {t.lower() for t in source.tags}:
                continue
            result.append(source)
        return result

    def resolve_for(self, admin: AdminUnits, *, categories: Optional[Sequence[str]] = None,
                    types: Optional[Sequence[SourceType]] = None,
                    enabled_only: bool = True,
                    operational_only: bool = True) -> List[DataSource]:
        """Return the execution plan: sources relevant for this administrative framing.

        When the administrative units are unknown (offline, service down) only universal
        sources are returned, and the caller is expected to warn the user - the analysis
        still runs rather than failing.
        """
        candidates = self.query(categories=categories, types=types, enabled_only=enabled_only,
                                operational_only=operational_only)
        return [source for source in candidates if source.applies_to(admin)]

    def catalogued_only(self, *, categories: Optional[Sequence[str]] = None
                        ) -> List[DataSource]:
        """Return the sources the plugin knows of but cannot query.

        These are what the dossier reports as *gaps*: datasets that exist somewhere but
        were not technically verifiable, so no analysis was run against them.
        """
        wanted = self._expand_categories(categories or [])
        return [source for source in self.all(enabled_only=False)
                if not source.verification_status.is_operational
                and (not wanted or source.category in wanted)]

    def fallbacks_for(self, source_id: str) -> List[DataSource]:
        """Return the declared alternatives for a source, best quality first."""
        source = self.get(source_id)
        if source is None:
            return []
        alternatives = [self._sources[fid] for fid in source.fallback_sources
                        if fid in self._sources]
        return sorted((s for s in alternatives if s.is_operational and s.enabled),
                      key=lambda s: (-s.quality.score, s.priority, s.name.lower()))

    def categories(self) -> List[str]:
        """Return the distinct categories present in the catalogue."""
        return sorted({source.category for source in self._sources.values() if source.category})

    def by_category(self, *, enabled_only: bool = True,
                    roll_up: bool = False) -> Dict[str, List[DataSource]]:
        """Group the catalogue by category.

        With ``roll_up`` the sub-categories are folded into their dossier macro-category,
        which is how the report and the layer tree want to see them.
        """
        taxonomy = None
        if roll_up:
            try:
                taxonomy = Taxonomy.instance()
            except Exception as exc:  # pragma: no cover - broken user taxonomy
                log.warning(f"Taxonomy unavailable, categories not rolled up: {exc}")
        grouped: Dict[str, List[DataSource]] = {}
        for source in self.all(enabled_only=enabled_only):
            category = source.category or "other"
            if taxonomy is not None:
                category = taxonomy.root_of(category)
            grouped.setdefault(category, []).append(source)
        return grouped

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, source_id: object) -> bool:
        return str(source_id) in self._sources
