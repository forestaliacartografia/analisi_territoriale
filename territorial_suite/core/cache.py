"""On-disk cache for service responses and derived datasets.

Two ideas make the cache useful rather than dangerous:

* the key quantises the bounding box on a grid, so panning a few metres reuses the entry;
* every entry has a kind-dependent TTL and the whole store is bounded by a quota with LRU
  eviction, so the cache can never grow without limit.

The index is a small SQLite database; payloads are ordinary files (GPKG/TIFF/XML/JSON) so
that they can be opened directly by QGIS and inspected by the user.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from . import log, settings
from .errors import CacheError
from .paths import cache_dir, safe_filename

KIND_VECTOR = "vector"
KIND_RASTER = "raster"
KIND_SERVICE = "services"
KIND_METADATA = "metadata"

_LOCK = threading.RLock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    source_id   TEXT NOT NULL DEFAULT '',
    area_id     TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    last_used   REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source_id);
CREATE INDEX IF NOT EXISTS idx_entries_area ON entries(area_id);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
"""


@dataclass(frozen=True)
class CacheEntry:
    """A cached payload."""

    key: str
    kind: str
    path: Path
    source_id: str
    area_id: str
    size_bytes: int
    created_at: float
    expires_at: float
    meta: Dict[str, Any]

    @property
    def expired(self) -> bool:
        """Whether the entry is past its TTL."""
        return self.expires_at > 0 and time.time() > self.expires_at


def quantise_bbox(min_x: float, min_y: float, max_x: float, max_y: float,
                  grid: float) -> str:
    """Snap a bounding box to a grid so that near-identical requests share a key."""
    if grid <= 0:
        return f"{min_x:.6f},{min_y:.6f},{max_x:.6f},{max_y:.6f}"
    snap_down = lambda v: math.floor(v / grid) * grid  # noqa: E731 - local helper
    snap_up = lambda v: math.ceil(v / grid) * grid      # noqa: E731 - local helper
    return (f"{snap_down(min_x):.3f},{snap_down(min_y):.3f},"
            f"{snap_up(max_x):.3f},{snap_up(max_y):.3f}")


def make_key(source_id: str, **parts: Any) -> str:
    """Build a deterministic cache key from a source id and query parameters."""
    payload = json.dumps({"source": source_id, **parts}, sort_keys=True, default=str)
    # Not a security primitive: this only has to turn a query into a short, stable file
    # name. ``usedforsecurity=False`` says so, and keeps the plugin installable on builds
    # that run Python in FIPS mode, where the plain constructor would raise.
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


#: Filter clause shared by the statements below. Each pair of parameters is
#: ``(value, value)``: when the value is empty the first test succeeds and the column is
#: not compared at all, so one constant statement serves every combination of filters.
_FILTER = ("WHERE (? = '' OR key = ?)"
           "  AND (? = '' OR source_id = ?)"
           "  AND (? = '' OR area_id = ?)"
           "  AND (? = '' OR kind = ?)")
_SELECT_FILTERED = f"SELECT * FROM entries {_FILTER}"      # nosec B608 - no caller data
_DELETE_FILTERED = f"DELETE FROM entries {_FILTER}"        # nosec B608 - no caller data
_SELECT_BY_KIND = "SELECT * FROM entries WHERE (? = '' OR kind = ?)"


class CacheManager:
    """Process-wide cache manager (thread safe)."""

    _instance: Optional["CacheManager"] = None

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = Path(root) if root else cache_dir()
        self._db_path = self._root / "index.sqlite"
        self._root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def instance(cls) -> "CacheManager":
        """Return the shared cache manager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the shared instance (tests)."""
        cls._instance = None

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection, commit and *close* it.

        Closing matters on Windows: a connection left open keeps a lock on
        ``index.sqlite`` and prevents the cache directory from being removed.
        """
        connection = sqlite3.connect(str(self._db_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_db(self) -> None:
        try:
            with _LOCK, self._connect() as connection:
                connection.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise CacheError("Cannot initialise the cache index", detail=str(exc)) from exc

    @property
    def enabled(self) -> bool:
        """Whether caching is switched on in the settings."""
        return bool(settings.get("cache.enabled", True))

    @property
    def offline(self) -> bool:
        """Whether the plugin must answer from the cache only."""
        return bool(settings.get("network.offline", False))

    @property
    def root(self) -> Path:
        """Cache root directory."""
        return self._root

    def ttl_seconds(self, kind: str) -> float:
        """Return the TTL configured for a cache kind, in seconds."""
        hours = settings.get(f"cache.ttl_hours.{kind}", None)
        if hours is None and kind == "health":
            return float(settings.get("cache.ttl_minutes.health", 10)) * 60.0
        if hours is None:
            hours = settings.get("cache.ttl_hours.vector", 24)
        return float(hours) * 3600.0

    def path_for(self, kind: str, source_id: str, key: str, suffix: str) -> Path:
        """Return the file path a payload must be written to."""
        folder = self._root / safe_filename(kind, fallback="misc") / safe_filename(source_id, fallback="unknown")
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{key}{suffix}"

    # ------------------------------------------------------------------ API

    def get(self, key: str) -> Optional[CacheEntry]:
        """Return a fresh cache entry, or ``None`` when missing/expired/stale."""
        if not self.enabled:
            return None
        try:
            with _LOCK, self._connect() as connection:
                row = connection.execute("SELECT * FROM entries WHERE key=?", (key,)).fetchone()
                if row is None:
                    return None
                entry = self._row_to_entry(row)
                if not entry.path.exists():
                    connection.execute("DELETE FROM entries WHERE key=?", (key,))
                    return None
                if entry.expired and not self.offline:
                    return None
                connection.execute(
                    "UPDATE entries SET hits=hits+1, last_used=? WHERE key=?",
                    (time.time(), key),
                )
                return entry
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning(f"Cache read failed: {exc}")
            return None

    def put(self, key: str, path: Path, *, kind: str = KIND_VECTOR, source_id: str = "",
            area_id: str = "", ttl_seconds: Optional[float] = None,
            meta: Optional[Dict[str, Any]] = None) -> Optional[CacheEntry]:
        """Register an existing file in the cache index."""
        if not self.enabled:
            return None
        target = Path(path)
        if not target.exists():
            return None
        now = time.time()
        ttl = self.ttl_seconds(kind) if ttl_seconds is None else float(ttl_seconds)
        # ttl == 0 means "never expires"; a negative ttl marks the entry already stale.
        expires_at = 0.0 if ttl == 0 else now + ttl
        size = self._payload_size(target)
        try:
            with _LOCK, self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO entries "
                    "(key, kind, source_id, area_id, path, size_bytes, created_at, expires_at, hits, last_used, meta) "
                    "VALUES (?,?,?,?,?,?,?,?,COALESCE((SELECT hits FROM entries WHERE key=?),0),?,?)",
                    (key, kind, source_id, area_id, str(target), size, now,
                     expires_at, key, now,
                     json.dumps(meta or {}, ensure_ascii=False)),
                )
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning(f"Cache write failed: {exc}")
            return None
        self.enforce_quota()
        return self.get(key)

    def invalidate(self, *, key: str = "", source_id: str = "", area_id: str = "",
                   kind: str = "") -> int:
        """Delete cache entries matching the given filters. Returns the number removed."""
        # The filters are optional, but the SQL is not assembled from them: an empty
        # filter neutralises its own clause through a parameter. The statements below are
        # therefore constant strings, with every value bound - there is no code path that
        # can put caller data into the SQL text.
        params = (key, key, source_id, source_id, area_id, area_id, kind, kind)
        removed = 0
        try:
            with _LOCK, self._connect() as connection:
                rows = connection.execute(_SELECT_FILTERED, params).fetchall()
                for row in rows:
                    self._remove_payload(Path(row["path"]))
                    removed += 1
                connection.execute(_DELETE_FILTERED, params)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning(f"Cache invalidation failed: {exc}")
        return removed

    def clear(self) -> int:
        """Empty the whole cache."""
        return self.invalidate()

    def size_bytes(self) -> int:
        """Return the total size of the cached payloads."""
        try:
            with _LOCK, self._connect() as connection:
                row = connection.execute("SELECT COALESCE(SUM(size_bytes),0) AS total FROM entries").fetchone()
                return int(row["total"] or 0)
        except sqlite3.Error:  # pragma: no cover - defensive
            return 0

    def stats(self) -> Dict[str, Any]:
        """Return counters used by the settings dialog."""
        try:
            with _LOCK, self._connect() as connection:
                rows = connection.execute(
                    "SELECT kind, COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS total "
                    "FROM entries GROUP BY kind"
                ).fetchall()
                return {
                    "total_bytes": self.size_bytes(),
                    "quota_bytes": int(float(settings.get("cache.quota_mb", 2048)) * 1024 * 1024),
                    "by_kind": {row["kind"]: {"count": row["n"], "bytes": int(row["total"])}
                                for row in rows},
                }
        except sqlite3.Error:  # pragma: no cover - defensive
            return {"total_bytes": 0, "quota_bytes": 0, "by_kind": {}}

    def purge_expired(self) -> int:
        """Remove entries whose TTL has elapsed."""
        now = time.time()
        removed = 0
        try:
            with _LOCK, self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM entries WHERE expires_at>0 AND expires_at<?", (now,)
                ).fetchall()
                for row in rows:
                    self._remove_payload(Path(row["path"]))
                    removed += 1
                connection.execute("DELETE FROM entries WHERE expires_at>0 AND expires_at<?", (now,))
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning(f"Cache purge failed: {exc}")
        return removed

    def enforce_quota(self) -> int:
        """Evict least-recently-used entries until the store fits the configured quota."""
        quota = int(float(settings.get("cache.quota_mb", 2048)) * 1024 * 1024)
        if quota <= 0:
            return 0
        total = self.size_bytes()
        if total <= quota:
            return 0
        removed = 0
        try:
            with _LOCK, self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM entries ORDER BY last_used ASC"
                ).fetchall()
                for row in rows:
                    if total <= quota:
                        break
                    self._remove_payload(Path(row["path"]))
                    connection.execute("DELETE FROM entries WHERE key=?", (row["key"],))
                    total -= int(row["size_bytes"] or 0)
                    removed += 1
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.warning(f"Cache eviction failed: {exc}")
        if removed:
            log.debug(f"Cache eviction removed {removed} entries")
        return removed

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _payload_size(path: Path) -> int:
        try:
            if path.is_dir():
                return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            return path.stat().st_size
        except OSError:  # pragma: no cover - defensive
            return 0

    @staticmethod
    def _remove_payload(path: Path) -> None:
        try:
            if path.is_dir():
                for child in sorted(path.rglob("*"), reverse=True):
                    child.unlink() if child.is_file() else child.rmdir()
                path.rmdir()
            elif path.exists():
                path.unlink()
            # GeoPackage side-car files
            for suffix in ("-wal", "-shm", ".aux.xml"):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
        except OSError as exc:  # pragma: no cover - defensive
            log.debug(f"Cannot delete cached payload {path}: {exc}")

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CacheEntry:
        try:
            meta = json.loads(row["meta"] or "{}")
        except ValueError:  # pragma: no cover - defensive
            meta = {}
        return CacheEntry(
            key=row["key"],
            kind=row["kind"],
            path=Path(row["path"]),
            source_id=row["source_id"],
            area_id=row["area_id"],
            size_bytes=int(row["size_bytes"] or 0),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            meta=meta,
        )

    def entries(self, *, kind: str = "") -> list:
        """Return the cached entries (for the settings dialog).

        A list, not a generator: the SQLite connection must be closed before the caller
        starts deleting files, otherwise Windows keeps the index locked.
        """
        try:
            with _LOCK, self._connect() as connection:
                rows = connection.execute(_SELECT_BY_KIND, (kind, kind)).fetchall()
            return [self._row_to_entry(row) for row in rows]
        except sqlite3.Error:  # pragma: no cover - defensive
            return []


def env_cache_root() -> Optional[str]:
    """Return the cache root override from the environment, if any (tests)."""
    return os.environ.get("TERRITORIAL_SUITE_CACHE")
