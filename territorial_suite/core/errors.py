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
