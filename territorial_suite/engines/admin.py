"""Administrative framing of a project area (municipality, province, region).

This runs **first** in every analysis, because the source catalogue is resolved against it:
regional and municipal sources only apply to the units actually touched by the area.

Two strategies, tried in the order configured in ``analysis.admin_strategies``:

``cadastre``
    query the cadastral *zoning* layer (one small request) and translate the
    ``ADMINISTRATIVEUNIT`` cadastral codes through the bundled ISTAT table. Official,
    precise, and it gives the share of the area per municipality.

``osm``
    query OSM administrative boundaries. Used where the cadastral service does not apply
    (autonomous provinces of Trento and Bolzano) or when it is unavailable.

If every strategy fails the analysis does **not** stop: the area is reported as
"administrative units not determined" and only universal sources are queried.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from qgis.core import QgsGeometry, QgsVectorLayer

from ..core import crs as crs_utils
from ..core import geometry as geom_utils
from ..core import log, measure, settings
from ..core.admin_reference import MunicipalityIndex
from ..core.cache import CacheManager
from ..core.errors import SourceError, UserCancelled
from ..core.feedback import Feedback, NullFeedback
from ..core.models import AdminUnit, AdminUnits
from ..core.project_area import ProjectArea
from ..core.registry import DataSource, DataSourceRegistry
from ..services import fetcher
from ..services.http import HttpClient

CADASTRE_ZONING_CATEGORY = "cadastral"
ADMIN_CATEGORY = "administrative"


class AdminResolver:
    """Resolves the administrative units intersecting a project area."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http
        self.cache = cache
        self.index = MunicipalityIndex.instance()

    # ------------------------------------------------------------------ public API

    def resolve(self, area: ProjectArea, *, feedback: Optional[Feedback] = None) -> AdminUnits:
        """Return the administrative units of ``area`` using the configured strategies."""
        feedback = feedback or NullFeedback()
        strategies = list(settings.get("analysis.admin_strategies", ["cadastre", "osm"]))
        errors: List[str] = []
        for strategy in strategies:
            if feedback.is_canceled():
                raise UserCancelled()
            try:
                if strategy == "cadastre":
                    units = self._from_cadastre(area, feedback)
                elif strategy == "osm":
                    units = self._from_osm(area, feedback)
                else:
                    log.warning(f"Unknown admin strategy '{strategy}'")
                    continue
            except UserCancelled:
                raise
            except SourceError as exc:
                errors.append(f"{strategy}: {exc}")
                feedback.push_warning(f"Administrative units via {strategy}: {exc}")
                continue
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{strategy}: {type(exc).__name__}: {exc}")
                log.exception(f"Admin strategy {strategy} failed", exc)
                continue
            if units.municipalities:
                units.resolved = True
                feedback.push_info(f"Unita amministrative: {units.summary()}")
                return units
        note = "; ".join(errors) if errors else "nessuna sorgente amministrativa disponibile"
        feedback.push_warning(f"Unita amministrative non determinate ({note})")
        return AdminUnits(resolved=False, note=note)

    # ------------------------------------------------------------------ strategies

    def _zoning_source(self) -> Optional[DataSource]:
        source_id = settings.get("cadastre.source_id", "")
        candidates = [source for source in
                      self.registry.query(categories=[CADASTRE_ZONING_CATEGORY])
                      if source.subcategory == "zoning"]
        if source_id:
            preferred = [s for s in candidates if s.id.startswith(source_id)]
            if preferred:
                return preferred[0]
        return candidates[0] if candidates else None

    def _from_cadastre(self, area: ProjectArea, feedback: Feedback) -> AdminUnits:
        """Derive the units from cadastral zoning (``ADMINISTRATIVEUNIT`` codes)."""
        source = self._zoning_source()
        if source is None:
            raise SourceError("no cadastral zoning source in the catalogue")
        feedback.set_step("Individuazione dei Comuni (catasto)")
        result = fetcher.fetch_features(
            source, area.bbox(), area.crs, target_crs=area.work_crs, area_id=area.id,
            feedback=feedback, http=self.http, cache=self.cache)
        layer = result.layer("zoning")
        if layer is None or result.feature_count == 0:
            raise SourceError("the cadastral service returned no sheet for this area",
                              source_id=source.id)
        field = source.fields.get("municipality", "ADMINISTRATIVEUNIT")
        shares = self._shares_by_code(area, layer, field)
        if not shares:
            raise SourceError("cadastral answer without administrative unit codes",
                              source_id=source.id)
        return self._units_from_codes(shares, source_id=source.id)

    def _from_osm(self, area: ProjectArea, feedback: Feedback) -> AdminUnits:
        """Derive the units from OSM administrative boundaries."""
        candidates = self.registry.query(categories=[ADMIN_CATEGORY])
        if not candidates:
            raise SourceError("no administrative boundary source in the catalogue")
        source = candidates[0]
        feedback.set_step("Individuazione dei Comuni (confini amministrativi)")
        result = fetcher.fetch_features(
            source, area.bbox(), area.crs, target_crs=area.work_crs, area_id=area.id,
            feedback=feedback, http=self.http, cache=self.cache)
        layer = result.layer("admin")
        if layer is None or result.feature_count == 0:
            raise SourceError("no administrative boundary found for this area",
                              source_id=source.id)
        return self._units_from_osm_layer(area, layer, source_id=source.id)

    # ------------------------------------------------------------------ helpers

    def _shares_by_code(self, area: ProjectArea, layer: QgsVectorLayer,
                        field: str) -> Dict[str, float]:
        """Return ``{cadastral_code: share of the project area in %}``."""
        names = [f.name() for f in layer.fields()]
        if field not in names:
            field = next((name for name in names if name.upper() == "ADMINISTRATIVEUNIT"), "")
        if not field:
            return {}
        work_crs = area.work_crs
        area_geom = area.geometry_in(work_crs)
        by_code: Dict[str, List[QgsGeometry]] = {}
        for feature in layer.getFeatures():
            code = str(feature[field] or "").strip().upper()
            geometry = feature.geometry()
            if not code or geometry is None or geometry.isEmpty():
                continue
            if layer.crs() != work_crs:
                try:
                    geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
                except Exception:  # pragma: no cover - broken feature
                    continue
            by_code.setdefault(code, []).append(QgsGeometry(geometry))
        shares: Dict[str, float] = {}
        for code, geometries in by_code.items():
            merged = geom_utils.union(geometries)
            clipped = geom_utils.intersection(merged, area_geom)
            if clipped.isEmpty():
                continue
            shares[code] = measure.percentage(measure.area_m2(clipped, work_crs), area.area_m2)
        if not shares:
            # the area may be smaller than one sheet: fall back to plain presence
            shares = {code: 0.0 for code in by_code}
        return shares

    def _units_from_codes(self, shares: Dict[str, float], *, source_id: str) -> AdminUnits:
        """Translate cadastral codes into municipalities, provinces and regions."""
        municipalities: List[AdminUnit] = []
        provinces: Dict[str, AdminUnit] = {}
        regions: Dict[str, AdminUnit] = {}
        for code, share in sorted(shares.items(), key=lambda item: -item[1]):
            row = self.index.by_cadastral(code)
            if row is None:
                log.warning(f"Unknown cadastral code {code}")
                municipalities.append(AdminUnit(name=f"[{code}]", cadastral_code=code,
                                                area_share_pct=share, source_id=source_id))
                continue
            municipalities.append(AdminUnit(
                name=row.name, istat_code=row.istat, cadastral_code=row.cadastral,
                province_code=row.province_abbr, region_name=row.region,
                area_share_pct=share, source_id=source_id))
            provinces.setdefault(row.province, AdminUnit(
                name=row.province, level="province", province_code=row.province_abbr,
                region_name=row.region, source_id=source_id))
            regions.setdefault(row.region, AdminUnit(
                name=row.region, level="region", region_name=row.region, source_id=source_id))
        return AdminUnits(municipalities=municipalities,
                          provinces=list(provinces.values()),
                          regions=list(regions.values()),
                          resolved=bool(municipalities))

    def _units_from_osm_layer(self, area: ProjectArea, layer: QgsVectorLayer, *,
                              source_id: str) -> AdminUnits:
        """Build the units from an OSM boundaries layer (admin_level 4/6/8)."""
        names = {f.name().lower(): f.name() for f in layer.fields()}
        level_field = names.get("admin_level", "")
        name_field = names.get("name", "")
        tags_field = names.get("other_tags", "")
        work_crs = area.work_crs
        area_geom = area.geometry_in(work_crs)
        buckets: Dict[str, List[Tuple[str, float, str]]] = {"8": [], "6": [], "4": []}
        for feature in layer.getFeatures():
            level = str(feature[level_field] or "").strip() if level_field else ""
            name = str(feature[name_field] or "").strip() if name_field else ""
            if level not in buckets or not name:
                continue
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if layer.crs() != work_crs:
                try:
                    geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
                except Exception:  # pragma: no cover - broken feature
                    continue
            clipped = geom_utils.intersection(geometry, area_geom)
            if clipped.isEmpty():
                continue
            share = measure.percentage(measure.area_m2(clipped, work_crs), area.area_m2)
            istat = ""
            if tags_field:
                tags = str(feature[tags_field] or "")
                istat = _hstore_value(tags, "ref:ISTAT")
            buckets[level].append((name, share, istat))

        municipalities = []
        for name, share, istat in sorted(buckets["8"], key=lambda item: -item[1]):
            row = self.index.by_istat(istat) if istat else None
            if row is None:
                matches = self.index.by_name(name)
                row = matches[0] if len(matches) == 1 else None
            municipalities.append(AdminUnit(
                name=row.name if row else name,
                istat_code=row.istat if row else istat,
                cadastral_code=row.cadastral if row else "",
                province_code=row.province_abbr if row else "",
                region_name=row.region if row else "",
                area_share_pct=share, source_id=source_id))
        provinces = [AdminUnit(name=name, level="province", area_share_pct=share,
                               source_id=source_id)
                     for name, share, _ in sorted(buckets["6"], key=lambda item: -item[1])]
        regions = [AdminUnit(name=name, level="region", region_name=name,
                             area_share_pct=share, source_id=source_id)
                   for name, share, _ in sorted(buckets["4"], key=lambda item: -item[1])]
        if not regions and municipalities:
            regions = [AdminUnit(name=unit.region_name, level="region",
                                 region_name=unit.region_name, source_id=source_id)
                       for unit in municipalities if unit.region_name]
        return AdminUnits(municipalities=municipalities, provinces=provinces,
                          regions=regions, resolved=bool(municipalities))


def _hstore_value(hstore: str, key: str) -> str:
    """Extract one key from GDAL's ``other_tags`` HSTORE string."""
    needle = f'"{key}"=>"'
    start = hstore.find(needle)
    if start < 0:
        return ""
    start += len(needle)
    end = hstore.find('"', start)
    return hstore[start:end] if end > start else ""
