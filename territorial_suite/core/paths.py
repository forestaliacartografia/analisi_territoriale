"""Filesystem locations used by the plugin.

Three roots exist:

``plugin_dir``   read-only, shipped with the plugin (built-in configuration);
``user_dir``     writable, inside the active QGIS profile (user overrides, outputs);
``cache_dir``    writable, inside the QGIS profile cache (purgeable at any time).

Everything that writes to disk goes through this module so that packaging, tests and
"clear cache" have a single source of truth.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

PLUGIN_PACKAGE = "territorial_suite"

_UNSAFE = re.compile(r"[^A-Za-z0-9._@ -]+")


def plugin_dir() -> Path:
    """Return the installed plugin directory (read-only at runtime)."""
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """Return the built-in configuration directory."""
    return plugin_dir() / "config"


def resources_dir() -> Path:
    """Return the built-in resources directory (icons, report assets)."""
    return plugin_dir() / "resources"


def _profile_dir() -> Path:
    """Return the active QGIS profile directory, or a temp dir outside QGIS."""
    try:
        from qgis.core import QgsApplication

        path = QgsApplication.qgisSettingsDirPath()
        if path:
            return Path(path)
    except Exception:  # pragma: no cover - QGIS not available (pure unit tests)
        pass
    return Path(tempfile.gettempdir()) / "qgis_profile_stub"


def user_dir() -> Path:
    """Return (and create) the writable user directory inside the QGIS profile."""
    path = Path(os.environ.get("TERRITORIAL_SUITE_HOME", _profile_dir() / PLUGIN_PACKAGE))
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_sources_dir() -> Path:
    """Return (and create) the directory holding user-provided source descriptors."""
    path = user_dir() / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """Return (and create) the cache root."""
    env = os.environ.get("TERRITORIAL_SUITE_CACHE")
    if env:
        path = Path(env)
    else:
        path = _profile_dir() / "cache" / PLUGIN_PACKAGE
    path.mkdir(parents=True, exist_ok=True)
    return path


def area_dir(area_id: str) -> Path:
    """Return (and create) the output directory of one project area.

    Engines write their durable outputs here (GeoPackages, rasters, reports); the package
    builder copies them wherever the user asks.
    """
    path = user_dir() / "areas" / safe_filename(area_id, fallback="area")
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir() -> Path:
    """Return (and create) a scratch directory for intermediate outputs."""
    path = user_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str, *, fallback: str = "untitled", max_length: int = 80) -> str:
    """Sanitise ``name`` so that it can be used as a single path component.

    Blocks path traversal, directory separators and characters rejected by Windows.
    """
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip(". ")
    cleaned = cleaned.replace("..", "_")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length]


def ensure_within(root: Path, candidate: Path) -> Path:
    """Return ``candidate`` resolved, raising ``ValueError`` if it escapes ``root``.

    Used for every user-supplied output path that the plugin writes into a managed
    directory (cache, package builder), to prevent path traversal.
    """
    root_resolved = root.resolve()
    target = (root_resolved / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise ValueError(f"Path {target} is outside {root_resolved}")
    return target
