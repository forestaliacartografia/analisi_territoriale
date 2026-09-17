"""Typed settings with file-based defaults.

Every tunable value has a default in ``config/defaults.json`` and can be overridden by the
user through ``QgsSettings`` under ``plugins/territorial_suite/``. Keys are dotted paths
(``"network.timeout_s"``). Nothing numeric or URL-like should be hardcoded in the code:
if it is configurable, it lives in the JSON defaults.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from . import log
from .constants import SETTINGS_ROOT
from .errors import ConfigError
from .paths import config_dir, user_dir

_DEFAULTS: Optional[Dict[str, Any]] = None


def _load_defaults() -> Dict[str, Any]:
    global _DEFAULTS
    if _DEFAULTS is None:
        path = config_dir() / "defaults.json"
        try:
            with open(path, "r", encoding="utf-8") as handle:
                _DEFAULTS = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ConfigError("Cannot read defaults.json", detail=str(exc)) from exc
        user_file = user_dir() / "defaults.json"
        if user_file.exists():
            try:
                with open(user_file, "r", encoding="utf-8") as handle:
                    _deep_merge(_DEFAULTS, json.load(handle))
            except (OSError, ValueError) as exc:  # pragma: no cover - user error path
                log.warning(f"Ignoring invalid user defaults.json: {exc}")
    return _DEFAULTS


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _dig(tree: Dict[str, Any], dotted: str) -> Any:
    node: Any = tree
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def default(key: str, fallback: Any = None) -> Any:
    """Return the built-in default for ``key`` (dotted path)."""
    try:
        return _dig(_load_defaults(), key)
    except KeyError:
        return fallback


def _coerce(value: Any, template: Any) -> Any:
    """Coerce a QgsSettings value (often a string) to the type of the default."""
    if template is None or value is None:
        return value
    if isinstance(template, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(template, int) and not isinstance(template, bool):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return template
    if isinstance(template, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return template
    if isinstance(template, (list, dict)):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return template
        return value
    return str(value)


def get(key: str, fallback: Any = None) -> Any:
    """Return the effective value of ``key``: user override, else built-in default."""
    template = default(key, fallback)
    try:
        from qgis.core import QgsSettings

        raw = QgsSettings().value(f"{SETTINGS_ROOT}/{key}", None)
    except Exception:  # pragma: no cover - outside QGIS
        raw = None
    if raw is None:
        return template
    return _coerce(raw, template)


def set_value(key: str, value: Any) -> None:
    """Persist a user override for ``key``."""
    try:
        from qgis.core import QgsSettings

        stored = json.dumps(value) if isinstance(value, (list, dict)) else value
        QgsSettings().setValue(f"{SETTINGS_ROOT}/{key}", stored)
    except Exception as exc:  # pragma: no cover - outside QGIS
        log.warning(f"Cannot persist setting {key}: {exc}")


def reset(key: Optional[str] = None) -> None:
    """Remove a user override (or the whole plugin settings tree when ``key`` is None)."""
    try:
        from qgis.core import QgsSettings

        settings = QgsSettings()
        settings.remove(f"{SETTINGS_ROOT}/{key}" if key else SETTINGS_ROOT)
    except Exception as exc:  # pragma: no cover - outside QGIS
        log.warning(f"Cannot reset settings: {exc}")


def section(name: str) -> Dict[str, Any]:
    """Return a whole settings section with user overrides applied."""
    base = default(name, {}) or {}
    if not isinstance(base, dict):
        return {}
    return {key: get(f"{name}.{key}") for key in base if not key.startswith("_")}


def snapshot() -> Dict[str, Any]:
    """Return every effective setting, for reports and bug files."""
    result: Dict[str, Any] = {}
    for name, value in _load_defaults().items():
        if name.startswith("_") or not isinstance(value, dict):
            continue
        result[name] = section(name)
    return result
