"""Layout profiles: what a sheet looks like, decided by the user and not by the code.

A **profile** is the graphic identity applied to every sheet: page and margins, fonts,
logos, which marginal elements appear, the fixed texts (client, project, sheet code,
disclaimer) and the extra images. A **template** stays what it always was: which data a
sheet shows. Profile and template are orthogonal - the same *Carta dei vincoli* can be
printed with the plain profile or with the institutional one without touching either.

Profiles ship as JSON under ``config/layouts/profiles.json``, and the user's own live in
``<profile>/territorial_suite/layouts/profiles.json``, which is what the settings dialog
writes. Nothing here is hardcoded: adding a profile is adding an object to a file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core import log, settings
from ...core.errors import ConfigError
from ...core.paths import config_dir, user_dir

#: File holding the profiles, in the shipped config and in the user profile.
PROFILES_FILE = Path("layouts") / "profiles.json"

#: Setting holding the id of the profile used when the caller does not name one.
DEFAULT_PROFILE_SETTING = "cartography.layout_profile"

#: Image formats QGIS can place on a layout.
SUPPORTED_IMAGES = (".png", ".jpg", ".jpeg", ".svg")

#: Text placeholders a profile may define. They are filled by the layout builder from the
#: project area and the analysis, so a profile can reference them in any text.
TEXT_KEYS = (
    "author", "client", "project", "locality", "municipality", "province", "region",
    "date", "sheet_code", "sheet_number", "revision", "scale", "crs", "notes",
    "method_notes", "legal_notes", "disclaimer", "header", "footer",
)


@dataclass
class ImageSpec:
    """One picture placed on a sheet: a logo, a photograph, a diagram."""

    path: str = ""
    x_pct: float = 0.0
    y_pct: float = 0.0
    w_pct: float = 20.0
    h_pct: float = 0.0
    keep_aspect: bool = True
    frame: bool = False
    caption: str = ""
    source: str = ""
    anchor: str = "panel"          # panel | page | map

    @property
    def usable(self) -> bool:
        """Whether the file exists and QGIS can place it."""
        if not self.path:
            return False
        candidate = Path(self.path)
        return candidate.is_file() and candidate.suffix.lower() in SUPPORTED_IMAGES

    def why_unusable(self) -> str:
        """Message for the user when the picture cannot be placed."""
        if not self.path:
            return "percorso non impostato"
        candidate = Path(self.path)
        if not candidate.exists():
            return f"file non trovato: {self.path}"
        if not candidate.is_file():
            return f"non e' un file: {self.path}"
        if candidate.suffix.lower() not in SUPPORTED_IMAGES:
            return (f"formato non supportato ({candidate.suffix}); "
                    f"usare {', '.join(SUPPORTED_IMAGES)}")
        return ""

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "ImageSpec":
        """Build from a JSON fragment, tolerating a bare path string."""
        if isinstance(payload, str):
            return cls(path=payload)
        data = payload if isinstance(payload, dict) else {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class LegendSpec:
    """How the legend behaves on a sheet."""

    mode: str = "auto"             # auto | thematic | custom | none
    title: str = "Legenda"
    columns: int = 1
    font_size: float = 7.5
    max_entries: int = 0           # 0 = no limit
    group_by_category: bool = False
    #: ``{layer name: label}`` renames applied before printing.
    rename: Dict[str, str] = field(default_factory=dict)
    #: Layer names to keep (empty = all) and to drop, applied in this order.
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    order: List[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        """Whether a legend should be drawn at all."""
        return self.mode != "none"

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "LegendSpec":
        """Build from a JSON fragment, tolerating a bare mode string."""
        if isinstance(payload, str):
            return cls(mode=payload.strip().lower() or "auto")
        data = payload if isinstance(payload, dict) else {}
        known = {f for f in cls.__dataclass_fields__}
        spec = cls(**{k: v for k, v in data.items() if k in known})
        if spec.mode not in ("auto", "thematic", "custom", "none"):
            spec.mode = "auto"
        spec.columns = max(1, int(spec.columns))
        return spec


@dataclass
class NorthArrowSpec:
    """Which north arrow to draw, how big, and turned how far."""

    enabled: bool = True
    model: str = "NorthArrow_02"
    size_mm: float = 18.0
    rotation: float = 0.0
    #: Follow the map rotation instead of the fixed ``rotation`` value.
    follow_map: bool = True

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "NorthArrowSpec":
        """Build from a JSON fragment, tolerating a bare boolean."""
        if isinstance(payload, bool):
            return cls(enabled=payload)
        data = payload if isinstance(payload, dict) else {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _overview_overlay(value: Any) -> Optional[Dict[str, Any]]:
    """Normalise the locator-map override written in a profile."""
    if value is None:
        return None
    if isinstance(value, bool):
        return {"enabled": value}
    return dict(value) if isinstance(value, dict) else None


@dataclass
class LayoutProfile:
    """The graphic identity of a set of sheets."""

    id: str = "standard"
    name: str = "Standard"
    description: str = ""
    builtin: bool = True
    page_size: str = "A3"
    orientation: str = "landscape"
    margin_mm: float = 10.0
    font_family: str = ""
    title_size: float = 13.0
    body_size: float = 7.0
    style_preset: str = "professional"
    logos: List[ImageSpec] = field(default_factory=list)
    images: List[ImageSpec] = field(default_factory=list)
    legend: LegendSpec = field(default_factory=LegendSpec)
    north_arrow: NorthArrowSpec = field(default_factory=NorthArrowSpec)
    scale_bar: bool = True
    numeric_scale: bool = True
    grid: bool = True
    coordinate_frame: bool = True
    attribution: bool = True
    #: Locator map override; ``None`` means "use the defaults from settings".
    overview: Optional[Dict[str, Any]] = None
    texts: Dict[str, str] = field(default_factory=dict)
    #: Marginal blocks, in order; an empty list means "use the template defaults".
    blocks: List[Dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ parsing

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], *, builtin: bool = True) -> "LayoutProfile":
        """Build a profile from its JSON description."""
        data = dict(payload or {})
        profile_id = str(data.get("id", "")).strip()
        if not profile_id:
            raise ConfigError("Profilo di layout senza id")
        return cls(
            id=profile_id,
            name=str(data.get("name", profile_id)),
            description=str(data.get("description", "")),
            builtin=bool(data.get("builtin", builtin)),
            page_size=str(data.get("page_size", "A3")),
            orientation=str(data.get("orientation", "landscape")),
            margin_mm=float(data.get("margin_mm", 10.0)),
            font_family=str(data.get("font_family", "")),
            title_size=float(data.get("title_size", 13.0)),
            body_size=float(data.get("body_size", 7.0)),
            style_preset=str(data.get("style_preset", "professional")),
            logos=[ImageSpec.from_dict(item) for item in data.get("logos", [])],
            images=[ImageSpec.from_dict(item) for item in data.get("images", [])],
            legend=LegendSpec.from_dict(data.get("legend")),
            north_arrow=NorthArrowSpec.from_dict(data.get("north_arrow")),
            scale_bar=bool(data.get("scale_bar", True)),
            numeric_scale=bool(data.get("numeric_scale", True)),
            grid=bool(data.get("grid", True)),
            coordinate_frame=bool(data.get("coordinate_frame", True)),
            attribution=bool(data.get("attribution", True)),
            overview=_overview_overlay(data.get("overview")),
            texts={str(k): str(v) for k, v in (data.get("texts") or {}).items()},
            blocks=[dict(block) for block in data.get("blocks", [])],
        )

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly dictionary."""
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "builtin": self.builtin, "page_size": self.page_size,
            "orientation": self.orientation, "margin_mm": self.margin_mm,
            "font_family": self.font_family, "title_size": self.title_size,
            "body_size": self.body_size, "style_preset": self.style_preset,
            "logos": [item.as_dict() for item in self.logos],
            "images": [item.as_dict() for item in self.images],
            "legend": self.legend.as_dict(),
            "north_arrow": self.north_arrow.as_dict(),
            "scale_bar": self.scale_bar, "numeric_scale": self.numeric_scale,
            "grid": self.grid, "coordinate_frame": self.coordinate_frame,
            "attribution": self.attribution,
            "overview": dict(self.overview) if self.overview is not None else None,
            "texts": dict(self.texts),
            "blocks": [dict(block) for block in self.blocks],
        }

    # ------------------------------------------------------------------ helpers

    def duplicate(self, new_id: str, name: str = "") -> "LayoutProfile":
        """Return an editable copy of this profile."""
        copy = LayoutProfile.from_dict(self.as_dict(), builtin=False)
        copy.id = new_id
        copy.name = name or f"{self.name} (copia)"
        copy.builtin = False
        return copy

    def pictures(self) -> List[ImageSpec]:
        """Logos and extra images together, in drawing order."""
        return list(self.logos) + list(self.images)

    def unusable_pictures(self) -> List[str]:
        """Messages for every picture that cannot be placed.

        A missing logo must be reported, never silently skipped: a sheet without the
        client's logo is a sheet that goes back for reprint.
        """
        return [f"{item.path or '(vuoto)'}: {item.why_unusable()}"
                for item in self.pictures() if not item.usable]

    def text(self, key: str, fallback: str = "") -> str:
        """Return a fixed text of the profile."""
        return self.texts.get(key, fallback)


class ProfileStore:
    """Loads, merges and saves layout profiles."""

    _instance: Optional["ProfileStore"] = None

    def __init__(self) -> None:
        self._profiles: Dict[str, LayoutProfile] = {}
        self.errors: List[str] = []

    @classmethod
    def instance(cls) -> "ProfileStore":
        """Return the shared store, loading it on first use."""
        if cls._instance is None:
            cls._instance = cls().load()
        return cls._instance

    @classmethod
    def reload(cls) -> "ProfileStore":
        """Force a reload from disk (after the settings dialog writes)."""
        cls._instance = cls().load()
        return cls._instance

    # ------------------------------------------------------------------ loading

    @staticmethod
    def user_path() -> Path:
        """Where the user's own profiles are written."""
        return user_dir() / PROFILES_FILE

    def load(self) -> "ProfileStore":
        """Load shipped profiles, then the user's (which may override by id)."""
        self._profiles.clear()
        self.errors.clear()
        for path, builtin in ((config_dir() / PROFILES_FILE, True),
                              (self.user_path(), False)):
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self.errors.append(f"{path.name}: {exc}")
                log.warning(f"Profili di layout non leggibili in {path}: {exc}")
                continue
            for raw in payload.get("profiles", []):
                try:
                    profile = LayoutProfile.from_dict(raw, builtin=builtin)
                except ConfigError as exc:
                    self.errors.append(str(exc))
                    continue
                self._profiles[profile.id] = profile
        if not self._profiles:
            self._profiles["standard"] = LayoutProfile()
        return self

    # ------------------------------------------------------------------ queries

    def all(self) -> List[LayoutProfile]:
        """Every profile, shipped ones first, then the user's, by name."""
        return sorted(self._profiles.values(),
                      key=lambda p: (not p.builtin, p.name.lower()))

    def get(self, profile_id: str) -> Optional[LayoutProfile]:
        """Return a profile by id."""
        return self._profiles.get(profile_id)

    def default(self) -> LayoutProfile:
        """The profile used when the caller does not name one."""
        wanted = str(settings.get(DEFAULT_PROFILE_SETTING, "standard") or "standard")
        return self.get(wanted) or self.get("standard") or self.all()[0]

    def resolve(self, profile_id: str = "") -> LayoutProfile:
        """Return the named profile, or the default one."""
        if profile_id:
            found = self.get(profile_id)
            if found is not None:
                return found
            log.warning(f"Profilo di layout '{profile_id}' non trovato: uso il predefinito")
        return self.default()

    # ------------------------------------------------------------------ writing

    def save(self, profile: LayoutProfile) -> Path:
        """Write (or overwrite) a user profile.

        A shipped profile is never modified in place: saving one stores a user copy with
        the same id, which takes precedence at load time and can be deleted to go back.
        """
        path = self.user_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": []}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # pragma: no cover - corrupted user file
                log.warning("File dei profili utente illeggibile: viene riscritto")
                payload = {"profiles": []}
        stored = profile.as_dict()
        stored["builtin"] = False
        existing = [p for p in payload.get("profiles", []) if p.get("id") != profile.id]
        payload["profiles"] = existing + [stored]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        self._profiles[profile.id] = LayoutProfile.from_dict(stored, builtin=False)
        return path

    def delete(self, profile_id: str) -> bool:
        """Remove a user profile. Shipped profiles cannot be deleted."""
        path = self.user_path()
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # pragma: no cover - corrupted user file
            return False
        remaining = [p for p in payload.get("profiles", []) if p.get("id") != profile_id]
        if len(remaining) == len(payload.get("profiles", [])):
            return False
        payload["profiles"] = remaining
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        self.load()
        return True

    def export(self, profile_id: str, path: Path) -> Path:
        """Write one profile to a shareable file."""
        profile = self.get(profile_id)
        if profile is None:
            raise ConfigError(f"Profilo di layout sconosciuto: {profile_id}")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"profiles": [profile.as_dict()]},
                                     ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        return target

    def import_file(self, path: Path) -> List[str]:
        """Import profiles from a file, returning the ids that were added."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError("File dei profili non leggibile", detail=str(exc)) from exc
        added: List[str] = []
        for raw in payload.get("profiles", []):
            profile = LayoutProfile.from_dict(raw, builtin=False)
            self.save(profile)
            added.append(profile.id)
        return added
