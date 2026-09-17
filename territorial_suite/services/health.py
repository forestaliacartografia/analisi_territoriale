"""Data source health checks.

The Source panel shows a status light per source. A check must be cheap (one small
request), cached for a few minutes, and must never raise: a source that is down is a
*result*, not an error.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from ..core import cache as cache_module
from ..core.cache import CacheManager, make_key
from ..core.errors import UserCancelled
from ..core.feedback import Feedback, NullFeedback
from ..core.models import SourceStatus, SourceType, utc_now
from ..core.registry import DataSource
from .http import HttpClient


@dataclass
class HealthReport:
    """Result of probing one source."""

    source_id: str
    status: SourceStatus = SourceStatus.UNKNOWN
    message: str = ""
    elapsed_ms: int = 0
    checked_at: str = field(default_factory=utc_now)
    from_cache: bool = False

    @property
    def online(self) -> bool:
        """Whether the source answered."""
        return self.status in (SourceStatus.ONLINE, SourceStatus.DEGRADED)

    def as_dict(self) -> Dict[str, object]:
        """Return a JSON-friendly dictionary."""
        return {
            "source_id": self.source_id,
            "status": self.status.value,
            "message": self.message,
            "elapsed_ms": self.elapsed_ms,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "HealthReport":
        """Rebuild from :meth:`as_dict` output."""
        return cls(
            source_id=str(payload.get("source_id", "")),
            status=SourceStatus(str(payload.get("status", SourceStatus.UNKNOWN.value))),
            message=str(payload.get("message", "")),
            elapsed_ms=int(payload.get("elapsed_ms", 0) or 0),
            checked_at=str(payload.get("checked_at", "")),
            from_cache=True,
        )


class HealthChecker:
    """Probes sources and caches the outcome for a short time."""

    def __init__(self, *, http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.http = http or HttpClient(retries=1, timeout_s=15)
        self.cache = cache or CacheManager.instance()

    # ------------------------------------------------------------------ probes

    @staticmethod
    def probe_request(source: DataSource) -> Optional[tuple]:
        """Return ``(url, params)`` of the cheapest request proving the source is alive."""
        query = source.query or {}
        if source.type in (SourceType.WFS,):
            return source.url, {"service": "WFS", "request": "GetCapabilities",
                                "version": str(query.get("version", "2.0.0"))}
        if source.type in (SourceType.WMS, SourceType.WMTS):
            service = "WMS" if source.type == SourceType.WMS else "WMTS"
            return source.url, {"service": service, "request": "GetCapabilities"}
        if source.type in (SourceType.XYZ, SourceType.TERRAIN_TILES):
            # Tile 0/0/0 always exists in both schemes and is the cheapest thing to ask for.
            url = source.url
            for token, value in (("{z}", "0"), ("{x}", "0"), ("{y}", "0"), ("{s}", "a"),
                                 ("{-y}", "0"), ("{q}", "0")):
                url = url.replace(token, value)
            return url, None
        if source.type == SourceType.OGCAPI:
            base = source.url.rstrip("/")
            if source.layer and "/collections/" not in base:
                base = f"{base}/collections/{source.layer}"
            return base, {"f": "json"}
        if source.type in (SourceType.ARCGIS_FEATURE, SourceType.ARCGIS_MAP):
            base = source.url.rstrip("/")
            if source.layer and not base.rsplit("/", 1)[-1].isdigit():
                base = f"{base}/{source.layer}"
            return base, {"f": "json"}
        if source.type == SourceType.OVERPASS:
            return source.url, {"data": "[out:json][timeout:10];out count;"}
        if source.type in (SourceType.GEOJSON, SourceType.GPKG, SourceType.FILE,
                           SourceType.RASTER, SourceType.STAC, SourceType.REST):
            return source.url, None
        return None

    # ------------------------------------------------------------------ API

    def check(self, source: DataSource, *, refresh: bool = False) -> HealthReport:
        """Probe one source, using the short-lived cached answer when possible."""
        if not source.enabled:
            return HealthReport(source_id=source.id, status=SourceStatus.DISABLED,
                                message="disabled in the catalogue")
        key = make_key(source.id, op="health", url=source.url, layer=source.layer)
        if not refresh:
            entry = self.cache.get(key)
            if entry is not None and entry.path.exists():
                try:
                    return HealthReport.from_dict(json.loads(entry.path.read_text("utf-8")))
                except (OSError, ValueError):  # pragma: no cover - corrupted cache
                    pass
        probe = self.probe_request(source)
        if probe is None:
            report = HealthReport(source_id=source.id, status=SourceStatus.UNKNOWN,
                                  message="no health probe for this source type")
        else:
            url, params = probe
            if not url:
                report = HealthReport(source_id=source.id, status=SourceStatus.UNKNOWN,
                                      message="no url configured")
            else:
                started = time.monotonic()
                ok, message, elapsed = self.http.head_ok(url, params, source_id=source.id)
                elapsed = elapsed or int((time.monotonic() - started) * 1000)
                report = HealthReport(
                    source_id=source.id,
                    status=SourceStatus.ONLINE if ok else SourceStatus.OFFLINE,
                    message=message, elapsed_ms=elapsed)
        path = self.cache.path_for(cache_module.KIND_METADATA, source.id, key, ".json")
        try:
            path.write_text(json.dumps(report.as_dict(), ensure_ascii=False), encoding="utf-8")
            self.cache.put(key, path, kind=cache_module.KIND_METADATA, source_id=source.id,
                           ttl_seconds=self.cache.ttl_seconds("health"))
        except OSError:  # pragma: no cover - defensive
            pass
        return report

    def check_many(self, sources: Iterable[DataSource], *, refresh: bool = False,
                   feedback: Optional[Feedback] = None) -> List[HealthReport]:
        """Probe several sources, reporting progress and honouring cancellation."""
        feedback = feedback or NullFeedback()
        items = list(sources)
        reports: List[HealthReport] = []
        for index, source in enumerate(items, start=1):
            if feedback.is_canceled():
                raise UserCancelled()
            feedback.set_step(f"Checking {source.name}")
            reports.append(self.check(source, refresh=refresh))
            feedback.set_progress(100.0 * index / max(1, len(items)))
        return reports
