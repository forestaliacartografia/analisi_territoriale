"""Orthophoto engine: pick an imagery provider, honour its terms, record what was used.

The user chooses ``auto``, ``esri``, ``google`` or a specific source id. The engine:

* resolves the choice against the catalogue (never against a hardcoded URL);
* for Google, opens a Map Tiles API session with the user's own key and retrieves the
  attribution string Google requires - if either fails, the provider is simply unusable;
* falls back to the next configured provider, **recording the fallback** so the map says
  which imagery it is actually showing;
* returns the attribution to be printed on the layout.

What it never does: invent a tile URL, scrape a viewer, or draw imagery without its
credit line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qgis.core import QgsRasterLayer, QgsRectangle

from ...core import log, settings
from ...core.errors import SourceError, SourceUnavailableError
from ...core.models import Provenance, utc_now
from ...core.registry import DataSource, DataSourceRegistry
from ...services.http import HttpClient
from ...services.ogc import raster as raster_service
from ..terrain import tile_for

#: Preference keywords the GUI and the Processing algorithms accept.
AUTOMATIC = "auto"
GOOGLE = "google"
ESRI = "esri"

#: Tag a descriptor must carry to be considered aerial/satellite imagery.
IMAGERY_TAG = "ortofoto"

#: Setting holding the user's default preference.
PREFERENCE_SETTING = "cartography.imagery_provider"


@dataclass
class ImageryChoice:
    """The imagery actually used for a map, and how it was reached."""

    source_id: str = ""
    provider: str = ""
    name: str = ""
    url: str = ""
    attribution: str = ""
    license: str = ""
    date: str = ""
    requested: str = ""
    fallback_used: bool = False
    #: Providers that were tried and why they were skipped, in order.
    attempts: List[str] = field(default_factory=list)
    layer: Optional[QgsRasterLayer] = None

    @property
    def available(self) -> bool:
        """Whether a provider was successfully selected and probed.

        This says nothing about a QGIS layer existing: selection runs in a worker thread,
        where creating one would be illegal. See :attr:`layer_ready`.
        """
        return bool(self.source_id and self.url)

    @property
    def layer_ready(self) -> bool:
        """Whether the raster layer has been built (main thread only)."""
        return self.layer is not None and self.layer.isValid()

    def without_layer(self) -> "ImageryChoice":
        """A copy carrying no QGIS object, safe to hand back from a worker thread."""
        return ImageryChoice(
            source_id=self.source_id, provider=self.provider, name=self.name,
            url=self.url, attribution=self.attribution, license=self.license,
            date=self.date, requested=self.requested,
            fallback_used=self.fallback_used, attempts=list(self.attempts))

    def as_dict(self) -> Dict[str, Any]:
        """Provenance block stamped on the layout and on the layer."""
        return {
            "imagery_provider": self.provider,
            "imagery_source": self.name,
            "source_id": self.source_id,
            "source_url": self.url,
            "attribution": self.attribution,
            "license": self.license,
            "date": self.date,
            "requested": self.requested,
            "fallback_used": self.fallback_used,
            "attempts": list(self.attempts),
        }

    def credit_line(self) -> str:
        """The single line that must appear on the sheet."""
        parts = [part for part in (self.attribution, self.license) if part]
        return " - ".join(parts) if parts else self.name


class ImageryEngine:
    """Resolves the orthophoto provider for a map."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http

    # ------------------------------------------------------------------ catalogue

    def providers(self) -> List[DataSource]:
        """Imagery sources in the catalogue, best quality first."""
        sources = [source for source in self.registry.all(enabled_only=True)
                   if source.category == "imagery" and source.is_operational
                   and IMAGERY_TAG in {tag.lower() for tag in source.tags}]
        return sorted(sources, key=lambda s: (-s.quality.score, s.priority, s.name))

    def provider_key(self, source: DataSource) -> str:
        """The keyword identifying a provider (``esri``, ``google``, ...)."""
        declared = str((source.query or {}).get("provider", "")).strip().lower()
        return declared or source.id.split(".", 1)[0].lower()

    def candidates(self, preference: str) -> List[DataSource]:
        """Ordered list of providers to try for a preference."""
        wanted = (preference or AUTOMATIC).strip().lower()
        available = self.providers()
        if wanted in ("", AUTOMATIC):
            return available
        exact = [s for s in available if s.id == preference]
        if exact:
            return exact + [s for s in available if s.id != preference]
        chosen = [s for s in available if self.provider_key(s) == wanted]
        others = [s for s in available if self.provider_key(s) != wanted]
        return chosen + others

    def why_unavailable(self, preference: str) -> str:
        """Explain why a requested provider is not among the candidates.

        Silently falling back would leave the user looking at Esri imagery believing it
        is Google: the reason belongs in the sheet, not in a log file.
        """
        wanted = (preference or "").strip().lower()
        if wanted in ("", AUTOMATIC):
            return ""
        if any(self.provider_key(s) == wanted or s.id == preference
               for s in self.providers()):
            return ""
        for source in self.registry.all(enabled_only=False):
            if source.category != "imagery":
                continue
            if self.provider_key(source) != wanted and source.id != preference:
                continue
            if not source.is_operational:
                return (f"{source.name}: sorgente censita ma non operativa "
                        f"({source.verification_status.label_it})")
            if not source.enabled:
                reason = "disattivata nel catalogo"
                if self.provider_key(source) == GOOGLE:
                    reason = ("richiede una chiave API Google configurata dall'utente "
                              "(Impostazioni > Cartografia)")
                return f"{source.name}: {reason}"
        return f"provider '{wanted}' non presente nel catalogo"

    # ------------------------------------------------------------------ resolution

    def resolve(self, rect_wgs84: QgsRectangle, *, preference: str = "",
                zoom: int = 12, allow_fallback: bool = True) -> ImageryChoice:
        """Select a provider **and** build its layer.

        Convenience for main-thread callers. A background task must call :meth:`select`
        instead and build the layer afterwards with :meth:`build_layer`, because creating
        a ``QgsRasterLayer`` outside the main thread crashes QGIS.
        """
        choice = self.select(rect_wgs84, preference=preference, zoom=zoom,
                            allow_fallback=allow_fallback)
        if choice.available:
            try:
                choice.layer = self.build_layer(choice)
            except SourceError as exc:
                choice.attempts.append(f"{choice.name}: {exc}")
        return choice

    def build_layer(self, choice: "ImageryChoice") -> QgsRasterLayer:
        """Create the raster layer of an already selected provider.

        **Main thread only.** No network happens here: the provider was probed during
        :meth:`select`, so this is pure QGIS object construction.

        :raises SourceUnavailableError: when the provider is unknown or refuses the URI.
        """
        source = self.registry.get(choice.source_id)
        if source is None:
            raise SourceUnavailableError(
                f"Sorgente ortofoto sconosciuta: {choice.source_id}",
                source_id=choice.source_id)
        if choice.provider == GOOGLE:
            # The session URL was already negotiated; only the layer is missing.
            return self._xyz_layer(source, choice.url)
        return raster_service.build_layer(source, name=source.name)

    def select(self, rect_wgs84: QgsRectangle, *, preference: str = "",
               zoom: int = 12, allow_fallback: bool = True) -> ImageryChoice:
        """Choose a provider and prove it answers, without creating any QGIS object.

        Safe to call from a worker thread: it performs HTTP only and returns a plain,
        serialisable record.
        """
        requested = (preference or settings.get(PREFERENCE_SETTING, AUTOMATIC)
                     or AUTOMATIC).strip().lower()
        choice = ImageryChoice(requested=requested)
        reason = self.why_unavailable(requested)
        if reason:
            choice.attempts.append(reason)
        ordered = self.candidates(requested)
        if not ordered:
            choice.attempts.append("nessuna sorgente di ortofoto abilitata nel catalogo")
            return choice
        for index, source in enumerate(ordered):
            if index and not allow_fallback:
                break
            try:
                built = self._prepare(source, rect_wgs84, zoom=zoom)
            except SourceError as exc:
                choice.attempts.append(f"{source.name}: {exc}")
                log.info(f"Ortofoto: {source.id} non utilizzabile ({exc})")
                continue
            except Exception as exc:  # pragma: no cover - provider surprises
                choice.attempts.append(f"{source.name}: {type(exc).__name__}: {exc}")
                continue
            built.requested = requested
            built.fallback_used = bool(reason) or index > 0 or (
                requested not in ("", AUTOMATIC)
                and self.provider_key(source) != requested and source.id != preference)
            built.attempts = choice.attempts + [f"{source.name}: utilizzata"]
            return built
        return choice

    def probe_tile(self, source: DataSource, url_template: str,
                   rect_wgs84: QgsRectangle, zoom: int) -> None:
        """Fetch one tile to prove the provider really answers.

        A ``QgsRasterLayer`` built on an XYZ template is *valid* even when the host does
        not exist: QGIS only discovers that when it tries to draw. Without this probe a
        sheet could be printed blank, with a perfectly good attribution underneath - an
        error that looks like a result.

        :raises SourceUnavailableError: when the tile cannot be fetched.
        """
        if not bool(settings.get("cartography.probe_imagery", True)):
            return
        centre = rect_wgs84.center()
        level = max(int((source.query or {}).get("zmin", 0)),
                    min(int(zoom), int((source.query or {}).get("zmax", 19))))
        x, y = tile_for(centre.x(), centre.y(), level)
        url = (url_template.replace("{z}", str(level)).replace("{x}", str(x))
               .replace("{y}", str(y)).replace("{-y}", str((2 ** level - 1) - y))
               .replace("{s}", "a").replace("{q}", "0"))
        client = HttpClient.for_source(source, self.http)
        response = client.get(url, source_id=source.id)
        kind = response.content_type
        if kind and not kind.startswith("image/"):
            raise SourceUnavailableError(
                f"il servizio ha risposto con '{kind}' invece di un'immagine",
                source_id=source.id, detail=url[:160])
        if len(response.content) < 128:
            raise SourceUnavailableError("il servizio ha restituito una tile vuota",
                                         source_id=source.id, detail=url[:160])

    def _prepare(self, source: DataSource, rect_wgs84: QgsRectangle, *,
                 zoom: int) -> ImageryChoice:
        """Negotiate and probe one provider. Network only - no QGIS object is created."""
        provider = self.provider_key(source)
        attribution = source.attribution
        url = source.url
        if provider == GOOGLE:
            from ...services.google_tiles import GoogleTilesClient

            client = GoogleTilesClient(source, http=self.http)
            if not client.configured:
                raise SourceUnavailableError(
                    "chiave API Google non configurata", source_id=source.id)
            prepared = client.prepare(rect_wgs84, zoom=zoom)
            url = prepared["url"]
            attribution = prepared["attribution"]
            self.probe_tile(source, url, rect_wgs84, zoom)
        elif source.type.value == "XYZ":
            self.probe_tile(source, source.url, rect_wgs84, zoom)
        return ImageryChoice(
            source_id=source.id, provider=provider, name=source.name, url=url,
            attribution=attribution, license=source.license,
            date=source.last_verified or utc_now()[:10])

    @staticmethod
    def _xyz_layer(source: DataSource, url: str) -> QgsRasterLayer:
        """Build an XYZ layer from a ready-made template (Google session URL)."""
        from urllib.parse import quote

        query = source.query or {}
        uri = (f"type=xyz&url={quote(url, safe='')}"
               f"&zmin={int(query.get('zmin', 0))}&zmax={int(query.get('zmax', 22))}")
        layer = QgsRasterLayer(uri, source.name, "wms")
        if not layer.isValid():
            raise SourceUnavailableError("il provider raster ha rifiutato la sorgente",
                                         source_id=source.id, detail=uri[:120])
        return layer

    # ------------------------------------------------------------------ provenance

    def provenance(self, choice: ImageryChoice) -> Optional[Provenance]:
        """Provenance record for the imagery actually used."""
        source = self.registry.get(choice.source_id)
        if source is None:
            return None
        record = source.provenance(operation="orthophoto")
        record.attribution = choice.attribution or record.attribution
        record.url = choice.url or record.url
        if choice.fallback_used:
            record.notes = (f"{record.notes} Provider richiesto: {choice.requested}; "
                            f"utilizzato: {choice.provider}.").strip()
        return record
