"""Google Map Tiles API client.

Google satellite imagery is only used through the **Map Tiles API**, the interface Google
publishes for this purpose, with the user's own API key. The plugin does not fetch tiles
from the URLs the Google Maps website uses internally: that would be scraping, it breaks
whenever Google changes anything, and it is not what the Terms of Service allow.

The flow Google documents is:

1. ``POST {createSession}?key=...`` with the map type, language and region, which returns
   a session token valid for about two weeks;
2. ``GET {viewport}?session=...&key=...&zoom=&north=&south=&east=&west=`` which returns
   the ``copyright`` string that **must** be displayed with the tiles;
3. ``GET {tiles}/{z}/{x}/{y}?session=...&key=...`` for the tiles themselves.

Every endpoint comes from the source descriptor, never from this file. If the attribution
cannot be obtained, the provider reports itself as unusable rather than displaying tiles
without the required credit.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from qgis.core import QgsRectangle

from ..core import credentials, log
from ..core.errors import SourceUnavailableError
from ..core.registry import DataSource
from .http import HttpClient

#: How long a session token is reused before asking for a new one (Google says ~2 weeks;
#: the plugin is deliberately more conservative).
SESSION_TTL_S = 6 * 3600


@dataclass
class TileSession:
    """An active Map Tiles API session."""

    token: str
    expiry: float = 0.0
    image_format: str = "jpeg"
    tile_width: int = 256
    tile_height: int = 256
    attribution: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        """Whether the token can still be used."""
        return bool(self.token) and time.time() < self.expiry


class GoogleTilesClient:
    """Session, attribution and tile URL for one Google imagery descriptor."""

    def __init__(self, source: DataSource, *, http: Optional[HttpClient] = None,
                 api_key: str = "") -> None:
        self.source = source
        self.http = HttpClient.for_source(source, http)
        self.api_key = api_key or credentials.get(credentials.GOOGLE_MAPS)
        self._session: Optional[TileSession] = None

    # ------------------------------------------------------------------ endpoints

    def _endpoint(self, key: str) -> str:
        """Read an endpoint from the descriptor; the engine never hardcodes URLs."""
        value = str((self.source.query or {}).get(key, "")).strip()
        if not value:
            raise SourceUnavailableError(
                f"Il descrittore {self.source.id} non dichiara l'endpoint '{key}'",
                source_id=self.source.id)
        return value

    @property
    def configured(self) -> bool:
        """Whether an API key is available for this provider."""
        return bool(self.api_key)

    # ------------------------------------------------------------------ session

    def session(self, *, refresh: bool = False) -> TileSession:
        """Create (or reuse) a Map Tiles API session.

        :raises SourceUnavailableError: when no API key is configured or Google refuses.
        """
        if not self.configured:
            raise SourceUnavailableError(
                "Nessuna chiave API Google configurata: impostarla in "
                f"Impostazioni, oppure nella variabile d'ambiente "
                f"{credentials.env_variable(credentials.GOOGLE_MAPS)}",
                source_id=self.source.id)
        if self._session is not None and self._session.valid and not refresh:
            return self._session
        query = self.source.query or {}
        payload = {
            "mapType": str(query.get("map_type", "satellite")),
            "language": str(query.get("language", "it-IT")),
            "region": str(query.get("region", "IT")),
        }
        for optional in ("imageFormat", "scale", "highDpi"):
            if optional in query:
                payload[optional] = query[optional]
        response = self.http.post_json(self._endpoint("session_url"), payload,
                                       params={"key": self.api_key},
                                       source_id=self.source.id)
        try:
            document = json.loads(response.text())
        except ValueError as exc:
            raise SourceUnavailableError("Risposta non valida da Google Map Tiles",
                                         source_id=self.source.id,
                                         detail=str(exc)) from exc
        token = str(document.get("session", "")).strip()
        if not token:
            raise SourceUnavailableError(
                f"Google Map Tiles non ha restituito un token di sessione: "
                f"{str(document)[:160]}", source_id=self.source.id)
        expiry = float(document.get("expiry", 0) or 0)
        self._session = TileSession(
            token=token,
            expiry=min(expiry, time.time() + SESSION_TTL_S) if expiry
            else time.time() + SESSION_TTL_S,
            image_format=str(document.get("imageFormat", "jpeg")),
            tile_width=int(document.get("tileWidth", 256) or 256),
            tile_height=int(document.get("tileHeight", 256) or 256),
            raw=document,
        )
        return self._session

    # ------------------------------------------------------------------ attribution

    def attribution(self, rect_wgs84: QgsRectangle, zoom: int = 12) -> str:
        """Return the copyright string Google requires to be displayed.

        :raises SourceUnavailableError: when it cannot be retrieved. Tiles must not be
            shown without it, so the caller is expected to fall back to another provider.
        """
        session = self.session()
        if session.attribution:
            return session.attribution
        response = self.http.get(self._endpoint("viewport_url"), {
            "session": session.token,
            "key": self.api_key,
            "zoom": int(zoom),
            "north": f"{rect_wgs84.yMaximum():.6f}",
            "south": f"{rect_wgs84.yMinimum():.6f}",
            "east": f"{rect_wgs84.xMaximum():.6f}",
            "west": f"{rect_wgs84.xMinimum():.6f}",
        }, source_id=self.source.id)
        try:
            document = json.loads(response.text())
        except ValueError as exc:
            raise SourceUnavailableError("Attribuzione Google non leggibile",
                                         source_id=self.source.id,
                                         detail=str(exc)) from exc
        copyright_text = str(document.get("copyright", "")).strip()
        if not copyright_text:
            raise SourceUnavailableError(
                "Google non ha restituito la stringa di attribuzione richiesta dai suoi "
                "termini d'uso: le tile non possono essere usate senza",
                source_id=self.source.id)
        session.attribution = copyright_text
        return copyright_text

    # ------------------------------------------------------------------ tiles

    def tile_url_template(self) -> str:
        """Return the ``{z}/{x}/{y}`` template QGIS needs, with the session parameters."""
        session = self.session()
        base = self._endpoint("tiles_url").rstrip("/")
        return f"{base}/{{z}}/{{x}}/{{y}}?session={session.token}&key={self.api_key}"

    def prepare(self, rect_wgs84: QgsRectangle, zoom: int = 12) -> Dict[str, Any]:
        """Everything the imagery engine needs: URL, attribution and session details."""
        attribution = self.attribution(rect_wgs84, zoom=zoom)
        session = self.session()
        log.debug(f"{self.source.id}: sessione Google valida fino a {session.expiry:.0f}")
        return {
            "url": self.tile_url_template(),
            "attribution": attribution,
            "image_format": session.image_format,
            "tile_size": session.tile_width,
        }
