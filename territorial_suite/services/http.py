"""HTTP client built on the QGIS network stack.

Why not ``requests``: ``QgsBlockingNetworkRequest`` inherits the user's proxy settings,
SSL exceptions, authentication configurations and the QGIS network cache, works inside
worker threads, and adds no dependency. It blocks, which is exactly what an engine running
inside a ``QgsTask`` wants.

The client also normalises two things every public service gets wrong sooner or later:

* an OGC ``ServiceException`` returned with HTTP 200;
* transient 5xx/429 answers, which are retried with exponential backoff.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QUrl, QUrlQuery
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import log, settings
from ..core.constants import PLUGIN_ID, PLUGIN_VERSION
from ..core.errors import SourceSchemaError, SourceUnavailableError, UserCancelled
from ..core.feedback import Feedback

#: Markers of an OGC exception document returned with a 200 status.
_EXCEPTION_MARKERS = (
    b"<ServiceExceptionReport",
    b"<ows:ExceptionReport",
    b"<ExceptionReport",
    b"<ServiceException",
)
#: HTTP status codes worth retrying.
RETRYABLE_STATUS = (408, 425, 429, 500, 502, 503, 504)

#: Per-host timestamp of the last request, used to honour service usage policies.
_LAST_REQUEST: Dict[str, float] = {}
_THROTTLE_LOCK = threading.Lock()


def _throttle(url: str, min_interval_s: float) -> None:
    """Sleep so that two requests to the same host are at least ``min_interval_s`` apart.

    Public services such as Overpass explicitly ask clients to rate-limit themselves; this
    also makes flaky services markedly more reliable.
    """
    if min_interval_s <= 0:
        return
    host = urlsplit(url).netloc.lower()
    with _THROTTLE_LOCK:
        previous = _LAST_REQUEST.get(host, 0.0)
        wait = previous + min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, min_interval_s))
        _LAST_REQUEST[host] = time.monotonic()


@dataclass
class HttpResponse:
    """Outcome of an HTTP request."""

    url: str
    status_code: int = 0
    content: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the request succeeded at the HTTP level."""
        return not self.error and (self.status_code == 0 or 200 <= self.status_code < 300)

    @property
    def content_type(self) -> str:
        """Lowercase content type without parameters."""
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    def text(self, encoding: str = "utf-8") -> str:
        """Decode the payload, never raising on malformed bytes."""
        return self.content.decode(encoding, errors="replace")

    def exception_message(self) -> str:
        """Return the message of an embedded OGC exception, or an empty string."""
        head = self.content[:4096]
        if not any(marker in head for marker in _EXCEPTION_MARKERS):
            return ""
        text = self.text()
        for opener, closer in (("<![CDATA[", "]]>"),
                               ("<ServiceException", "</ServiceException>"),
                               ("<ows:ExceptionText>", "</ows:ExceptionText>")):
            start = text.find(opener)
            if start >= 0:
                end = text.find(closer, start)
                fragment = text[start + len(opener):end if end > 0 else start + 300]
                cleaned = fragment.split(">")[-1] if opener.startswith("<S") else fragment
                cleaned = " ".join(cleaned.split())
                if cleaned:
                    return cleaned[:300]
        return "OGC service exception"


class HttpClient:
    """Small, retrying, cancellable HTTP client."""

    def __init__(self, *, timeout_s: Optional[float] = None, retries: Optional[int] = None,
                 backoff_s: Optional[float] = None, auth_cfg: str = "",
                 user_agent: str = "", min_interval_s: Optional[float] = None) -> None:
        self.timeout_s = float(timeout_s if timeout_s is not None
                               else settings.get("network.timeout_s", 30))
        self.retries = int(retries if retries is not None else settings.get("network.retries", 3))
        self.backoff_s = float(backoff_s if backoff_s is not None
                               else settings.get("network.backoff_s", 1.5))
        self.auth_cfg = auth_cfg or settings.get("network.auth_config_id", "")
        self.user_agent = user_agent or f"{PLUGIN_ID}/{PLUGIN_VERSION} (QGIS plugin)"
        self.min_interval_s = float(min_interval_s if min_interval_s is not None
                                    else settings.get("network.min_interval_s", 0.0))

    @classmethod
    def for_source(cls, source: Any, injected: Optional["HttpClient"] = None) -> "HttpClient":
        """Return the client to use for a source.

        An injected client (tests, or an engine sharing one) always wins; otherwise the
        descriptor may tune ``timeout_s``, ``retries``, ``backoff_s`` and ``min_interval_s``
        for services that are slower or stricter than the average.
        """
        if injected is not None:
            return injected
        query = getattr(source, "query", {}) or {}
        return cls(timeout_s=query.get("timeout_s"), retries=query.get("retries"),
                   backoff_s=query.get("backoff_s"),
                   min_interval_s=query.get("min_interval_s"))

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def build_url(url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Return ``url`` with ``params`` appended, preserving existing query items."""
        parsed = QUrl(url)
        query = QUrlQuery(parsed.query())
        for key, value in (params or {}).items():
            if value is None:
                continue
            query.removeAllQueryItems(key)
            query.addQueryItem(str(key), str(value))
        parsed.setQuery(query)
        return parsed.toString()

    def _request(self, url: str, accept: str = "") -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", self.user_agent.encode("utf-8"))
        if accept:
            request.setRawHeader(b"Accept", accept.encode("utf-8"))
        try:
            request.setTransferTimeout(int(self.timeout_s * 1000))
        except AttributeError:  # pragma: no cover - very old Qt
            pass
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        return request

    @staticmethod
    def _headers(reply: Any) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        try:
            for name, value in reply.rawHeaderPairs():
                headers[bytes(name).decode("latin-1").lower()] = bytes(value).decode("latin-1")
        except Exception:  # pragma: no cover - defensive
            pass
        return headers

    # ------------------------------------------------------------------ requests

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, *,
            accept: str = "", source_id: str = "",
            min_interval_s: Optional[float] = None,
            feedback: Optional[Feedback] = None) -> HttpResponse:
        """Perform a GET, retrying transient failures.

        Service exceptions returned with HTTP 200 are retried too: several Italian public
        services (the cadastral WFS among them) answer *"Richiesta non valida"* under load
        for requests that succeed a second later.

        :raises SourceUnavailableError: when every attempt failed.
        :raises SourceSchemaError: when the service kept answering with an OGC exception.
        :raises UserCancelled: when ``feedback`` reports cancellation.
        """
        if settings.get("network.offline", False):
            raise SourceUnavailableError("Offline mode is enabled", source_id=source_id,
                                         detail=url)
        full_url = self.build_url(url, params)
        last_error = ""
        last_exception_message = ""
        attempts = max(1, self.retries)
        interval = self.min_interval_s if min_interval_s is None else float(min_interval_s)
        for attempt in range(1, attempts + 1):
            if feedback is not None and feedback.is_canceled():
                raise UserCancelled()
            _throttle(full_url, interval)
            started = time.monotonic()
            blocking = QgsBlockingNetworkRequest()
            if self.auth_cfg:
                blocking.setAuthCfg(self.auth_cfg)
            error_code = blocking.get(self._request(full_url, accept), True)
            elapsed = int((time.monotonic() - started) * 1000)
            reply = blocking.reply()
            status = 0
            content = b""
            headers: Dict[str, str] = {}
            if reply is not None:
                try:
                    status = int(reply.attribute(
                        QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
                except Exception:  # pragma: no cover - defensive
                    status = 0
                content = bytes(reply.content())
                headers = self._headers(reply)
            if error_code == QgsBlockingNetworkRequest.ErrorCode.NoError and \
                    (status == 0 or 200 <= status < 300):
                response = HttpResponse(url=full_url, status_code=status, content=content,
                                        headers=headers, elapsed_ms=elapsed)
                message = response.exception_message()
                if not message:
                    return response
                last_exception_message = message
                last_error = f"service exception: {message}"
                log.debug(f"HTTP attempt {attempt}/{attempts} returned an exception "
                          f"({message}) for {full_url}")
                if attempt == attempts:
                    break
                # Exponential backoff: services that answer "invalid request" under load
                # recover in a few seconds, not in a few hundred milliseconds.
                time.sleep(self.backoff_s * (2 ** (attempt - 1)))
                continue
            last_error = blocking.errorMessage() or f"HTTP {status}"
            retryable = status in RETRYABLE_STATUS or status == 0
            log.debug(f"HTTP attempt {attempt}/{attempts} failed ({last_error}) for {full_url}")
            if not retryable or attempt == attempts:
                break
            time.sleep(self.backoff_s * attempt)
        if last_exception_message:
            raise SourceSchemaError(f"Service returned an exception: {last_exception_message}",
                                    source_id=source_id, detail=full_url)
        raise SourceUnavailableError(f"Request failed: {last_error}", source_id=source_id,
                                     detail=full_url)

    def post_json(self, url: str, payload: Dict[str, Any], *,
                  params: Optional[Dict[str, Any]] = None, source_id: str = "",
                  feedback: Optional[Feedback] = None) -> HttpResponse:
        """POST a JSON document and return the answer.

        Only used where a service has no GET equivalent (the Google Map Tiles session
        endpoint). It is deliberately simple: one attempt plus the configured retries,
        no streaming, no redirect juggling.
        """
        if settings.get("network.offline", False):
            raise SourceUnavailableError("Offline mode is enabled", source_id=source_id,
                                         detail=url)
        full_url = self.build_url(url, params)
        body = json.dumps(payload).encode("utf-8")
        last_error = ""
        attempts = max(1, self.retries)
        for attempt in range(1, attempts + 1):
            if feedback is not None and feedback.is_canceled():
                raise UserCancelled()
            _throttle(full_url, self.min_interval_s)
            started = time.monotonic()
            blocking = QgsBlockingNetworkRequest()
            if self.auth_cfg:
                blocking.setAuthCfg(self.auth_cfg)
            request = self._request(full_url, "application/json")
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,
                              "application/json")
            error_code = blocking.post(request, QByteArray(body), True)
            elapsed = int((time.monotonic() - started) * 1000)
            reply = blocking.reply()
            status = 0
            content = b""
            headers: Dict[str, str] = {}
            if reply is not None:
                try:
                    status = int(reply.attribute(
                        QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
                except Exception:  # pragma: no cover - defensive
                    status = 0
                content = bytes(reply.content())
                headers = self._headers(reply)
            if error_code == QgsBlockingNetworkRequest.ErrorCode.NoError and                     (status == 0 or 200 <= status < 300):
                return HttpResponse(url=full_url, status_code=status, content=content,
                                    headers=headers, elapsed_ms=elapsed)
            last_error = blocking.errorMessage() or f"HTTP {status}"
            if status not in RETRYABLE_STATUS or attempt == attempts:
                break
            time.sleep(self.backoff_s * attempt)
        raise SourceUnavailableError(f"Request failed: {last_error}", source_id=source_id,
                                     detail=full_url)

    def get_to_file(self, url: str, path: Path, params: Optional[Dict[str, Any]] = None,
                    **kwargs: Any) -> Path:
        """GET a resource and store the payload at ``path``."""
        response = self.get(url, params, **kwargs)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        return target

    def head_ok(self, url: str, params: Optional[Dict[str, Any]] = None, *,
                source_id: str = "") -> Tuple[bool, str, int]:
        """Cheap availability probe used by the health checker.

        Returns ``(ok, message, elapsed_ms)`` and never raises.
        """
        try:
            response = self.get(url, params, source_id=source_id)
            return True, "", response.elapsed_ms
        except (SourceUnavailableError, SourceSchemaError) as exc:
            return False, str(exc), 0
        except UserCancelled:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"{type(exc).__name__}: {exc}", 0


def split_urls(text: str) -> List[str]:
    """Split a whitespace/comma separated list of URLs (used by some descriptors)."""
    return [item.strip() for item in text.replace(",", " ").split() if item.strip()]
