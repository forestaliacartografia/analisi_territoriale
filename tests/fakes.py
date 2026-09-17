"""Test doubles: an HTTP client that replays recorded fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from territorial_suite.core.errors import SourceSchemaError, SourceUnavailableError
from territorial_suite.services.http import HttpResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> bytes:
    """Read a recorded service response."""
    return (FIXTURES / name).read_bytes()


class FakeHttpClient:
    """Replays a queue of responses and records the requests it received.

    Each queued item is either raw ``bytes`` (a 200 answer), an :class:`HttpResponse`, or
    an exception instance, which is raised - that is how failing sources are simulated.
    """

    def __init__(self, responses: Optional[Sequence[Any]] = None, *,
                 content_type: str = "application/gml+xml") -> None:
        self.responses: List[Any] = list(responses or [])
        self.requests: List[Dict[str, Any]] = []
        self.content_type = content_type
        self.timeout_s = 5
        self.retries = 1
        self.backoff_s = 0.0
        self.min_interval_s = 0.0

    # -- API used by the service clients

    @staticmethod
    def build_url(url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Same contract as :meth:`HttpClient.build_url`, without Qt."""
        if not params:
            return url
        query = "&".join(f"{key}={value}" for key, value in params.items()
                         if value is not None)
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{query}" if query else url

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> HttpResponse:
        """Return the next queued response."""
        self.requests.append({"url": url, "params": dict(params or {}), "kwargs": kwargs})
        if not self.responses:
            raise SourceUnavailableError("no more fake responses",
                                         source_id=kwargs.get("source_id", ""))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, HttpResponse):
            return item
        payload = item if isinstance(item, bytes) else str(item).encode("utf-8")
        response = HttpResponse(url=self.build_url(url, params), status_code=200,
                                content=payload,
                                headers={"content-type": self.content_type},
                                elapsed_ms=1)
        message = response.exception_message()
        if message:
            raise SourceSchemaError(f"Service returned an exception: {message}",
                                    source_id=kwargs.get("source_id", ""))
        return response

    def get_to_file(self, url: str, path: Path, params: Optional[Dict[str, Any]] = None,
                    **kwargs: Any) -> Path:
        """Write the next queued response to ``path``."""
        response = self.get(url, params, **kwargs)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        return target

    def head_ok(self, url: str, params: Optional[Dict[str, Any]] = None, *,
                source_id: str = "") -> tuple:
        """Availability probe used by the health checker."""
        try:
            response = self.get(url, params, source_id=source_id)
            return True, "", response.elapsed_ms
        except (SourceUnavailableError, SourceSchemaError) as exc:
            return False, str(exc), 0

    # -- helpers for assertions

    @property
    def last_request(self) -> Dict[str, Any]:
        """The most recent request received."""
        return self.requests[-1] if self.requests else {}

    def param(self, name: str, index: int = -1) -> Any:
        """Return one parameter of a recorded request."""
        if not self.requests:
            return None
        return self.requests[index]["params"].get(name)
