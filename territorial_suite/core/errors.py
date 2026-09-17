"""Exception hierarchy.

Design rule (see ``docs/DESIGN.md`` section L): a failure bound to a single data source
must never abort a whole analysis. Engines catch :class:`SourceError` per source, record
it in the result and carry on.
"""

from __future__ import annotations


class TerritorialSuiteError(Exception):
    """Base class for every error raised by the plugin."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.message} ({self.detail})" if self.detail else self.message


class ConfigError(TerritorialSuiteError):
    """An on-disk configuration file is missing, unreadable or invalid."""


class SourceError(TerritorialSuiteError):
    """Base class for data-source failures. Always carries the source id."""

    def __init__(self, message: str, *, source_id: str = "", detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.source_id = source_id


class SourceUnavailableError(SourceError):
    """Network failure, timeout, HTTP 4xx/5xx or empty capabilities."""


class SourceSchemaError(SourceError):
    """The service answered but the payload does not match the declared schema."""


class GeometryError(TerritorialSuiteError):
    """Invalid or unusable geometry that could not be repaired."""


class CrsError(TerritorialSuiteError):
    """A coordinate reference system is unknown or a transform is impossible."""


class CacheError(TerritorialSuiteError):
    """The on-disk cache could not be read or written."""


class EngineError(TerritorialSuiteError):
    """A processing/analysis step failed in a way that is not source specific."""


class LayoutError(EngineError):
    """A print layout could not be built, or would be misleading if printed.

    A sheet that silently loses its background is worse than no sheet at all: the reader
    cannot tell an empty territory from a missing service.
    """


class UserCancelled(TerritorialSuiteError):
    """The user cancelled a task; partial outputs must be discarded."""

    def __init__(self, message: str = "Operation cancelled by the user") -> None:
        super().__init__(message)


class ResolutionNotApproved(EngineError):
    """The requested detail does not fit the tile budget and nobody approved a coarser one.

    Raised instead of quietly returning a lower-resolution DEM. The message carries the
    alternative resolution and how to accept it, so the caller can ask the user rather
    than guess on their behalf.
    """


def describe(error: BaseException) -> str:
    """Render any exception as a sentence somebody can act on.

    Python's own wording is written for whoever is debugging, and some of it is actively
    misleading out of context: ``KeyError('hazard_risk')`` prints as ``'hazard_risk'``,
    which reaches a message bar as a quoted word with no verb. The plugin's own errors
    already carry a usable message and are passed through; everything else is given the
    kind of it was and enough context to tell a broken configuration from a broken
    network.

    The full traceback is not here on purpose: it belongs in the log, which
    :mod:`territorial_suite.tasks.runner` already writes.
    """
    if isinstance(error, TerritorialSuiteError):
        return str(error)
    if isinstance(error, KeyError):
        key = error.args[0] if error.args else "?"
        return (f"voce di configurazione mancante: «{key}». "
                f"E' un difetto del plugin, non un problema dei dati.")
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return f"componente non caricabile: {error}"
    if isinstance(error, (AttributeError, TypeError, NameError)):
        return f"errore interno del plugin ({type(error).__name__}): {error}"
    if isinstance(error, (OSError, IOError)):
        return f"errore di accesso a file o rete: {error}"
    text = str(error).strip()
    return f"{type(error).__name__}: {text}" if text else type(error).__name__
