"""Logging helpers.

Everything is routed to the QGIS message log channel named after the plugin so that the
user has one place to look. Outside QGIS (pure unit tests) messages fall back to stderr.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Iterator

from .constants import LOG_CHANNEL

try:  # pragma: no cover - depends on runtime
    from qgis.core import Qgis, QgsMessageLog

    _QGIS = True
except Exception:  # pragma: no cover - outside QGIS
    _QGIS = False

_DEBUG_ENABLED = False


def set_debug(enabled: bool) -> None:
    """Enable or disable debug-level messages globally."""
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = bool(enabled)


def debug_enabled() -> bool:
    """Return whether debug logging is currently enabled."""
    return _DEBUG_ENABLED


def _emit(message: str, level_name: str) -> None:
    if _QGIS:
        level = {
            "INFO": Qgis.MessageLevel.Info,
            "WARNING": Qgis.MessageLevel.Warning,
            "ERROR": Qgis.MessageLevel.Critical,
            "SUCCESS": Qgis.MessageLevel.Success,
        }.get(level_name, Qgis.MessageLevel.Info)
        QgsMessageLog.logMessage(message, LOG_CHANNEL, level)
    else:  # pragma: no cover - test convenience
        print(f"[{LOG_CHANNEL}][{level_name}] {message}", file=sys.stderr)


def info(message: str) -> None:
    """Log an informational message."""
    _emit(message, "INFO")


def success(message: str) -> None:
    """Log a success message."""
    _emit(message, "SUCCESS")


def warning(message: str) -> None:
    """Log a warning."""
    _emit(message, "WARNING")


def error(message: str) -> None:
    """Log an error."""
    _emit(message, "ERROR")


def debug(message: str) -> None:
    """Log a debug message (no-op unless debug logging is enabled)."""
    if _DEBUG_ENABLED:
        _emit(message, "INFO")


def exception(message: str, exc: BaseException) -> None:
    """Log an exception with its type and message."""
    error(f"{message}: {type(exc).__name__}: {exc}")


@contextmanager
def timed(label: str) -> Iterator[None]:
    """Context manager logging the elapsed time of a block at debug level."""
    started = time.monotonic()
    try:
        yield
    finally:
        debug(f"{label} took {(time.monotonic() - started) * 1000:.0f} ms")
