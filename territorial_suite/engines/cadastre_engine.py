"""Cadastral analysis: which parcels does the project area touch, and by how much.

Pipeline (see ``docs/cadastre.md``)::

    project area -> bbox -> CP:CadastralZoning (sheets) -> CP:CadastralParcel (parcels)
                 -> intersection -> per-municipality table -> GeoPackage + rows

Honesty rules baked in:

* the INSPIRE service carries **no registered surface**: the "cadastral" area reported here
  is the *graphic* surface computed from the map geometry, and it is labelled as such;
* the service does not cover the autonomous provinces of Trento and Bolzano, which is
  reported as a warning instead of an empty result;
* an area spanning several municipalities produces one group of rows per municipality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

from ..core import crs as crs_utils
from ..core import geometry as geom_utils
from ..core import log, measure, qt_compat, settings
from ..core.admin_reference import MunicipalityIndex
from ..core.cache import CacheManager
from ..core.errors import SourceError, UserCancelled
from ..core.feedback import ChildFeedback, Feedback, NullFeedback
from ..core.models import (
    AdminUnit,
    CadastralRow,
    LayerRef,
    SourceResult,
    SourceStatus,
)
from ..core.paths import area_dir
from ..core.project_area import ProjectArea
from ..core.registry import DataSource, DataSourceRegistry
from ..services import fetcher, vector_io
from ..services.http import HttpClient
from . import spatial

#: ``D612_016900.10`` -> municipality code, zoning code, parcel label.
_REFERENCE = re.compile(r"^(?P<mun>[A-Z]\d{3})_(?P<zoning>[A-Z0-9]+)\.(?P<parcel>.+)$")

CADASTRE_GPKG = "cadastre.gpkg"
PARCELS_LAYER = "parcels"
SHEETS_LAYER = "sheets"


@dataclass
class CadastreResult:
    """Outcome of the cadastral analysis."""

    rows: List[CadastralRow] = field(default_factory=list)
    municipalities: List[AdminUnit] = field(default_factory=list)
    layers: List[LayerRef] = field(default_factory=list)
    source_results: List[SourceResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    truncated: bool = False
    gpkg_path: str = ""

    @property
    def available(self) -> bool:
        """Whether at least one parcel was found."""
        return bool(self.rows)

    def by_municipality(self) -> Dict[str, List[CadastralRow]]:
        """Group rows by municipality, preserving the sheet/parcel ordering."""
        grouped: Dict[str, List[CadastralRow]] = {}
        for row in self.rows:
            grouped.setdefault(row.municipality or "-", []).append(row)
        return grouped

    def totals(self) -> Dict[str, float]:
        """Return the summary numbers shown under the table."""
        return {
            "parcels": float(len(self.rows)),
            "municipalities": float(len({row.municipality for row in self.rows})),
            "sheets": float(len({(row.municipality, row.sheet) for row in self.rows})),
            "intersect_area_m2": sum(row.area_intersect_m2 for row in self.rows),
        }


def parse_reference(reference: str) -> Dict[str, str]:
    """Split a ``NATIONALCADASTRALREFERENCE`` into its parts.

    ``D612_016900.10`` -> municipality ``D612``, sheet ``169``, allegato ``0``,
    sviluppo ``0``, parcel ``10``.
    """
    match = _REFERENCE.match((reference or "").strip().upper())
    if not match:
        return {}
    zoning = match.group("zoning")
    sheet_digits = zoning[:4]
    sheet = str(int(sheet_digits)) if sheet_digits.isdigit() else sheet_digits
    return {
        "municipality_code": match.group("mun"),
        "zoning": zoning,
        "sheet": sheet,
        "allegato": zoning[4] if len(zoning) > 4 and zoning[4] not in ("0",) else "",
        "sviluppo": zoning[5] if len(zoning) > 5 and zoning[5] not in ("0",) else "",
        "parcel": match.group("parcel"),
    }


def sheet_label(parts: Dict[str, str]) -> str:
    """Return the human sheet label (``169``, ``169/A``, ``169/A_1``)."""
    if not parts:
        return ""
    label = parts.get("sheet", "")
    if parts.get("allegato"):
        label = f"{label}/{parts['allegato']}"
    if parts.get("sviluppo"):
        label = f"{label}_{parts['sviluppo']}"
    return label


class CadastreEngine:
    """Queries the cadastral service and builds the parcel table for a project area."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http
        self.cache = cache
        self.index = MunicipalityIndex.instance()

    # ------------------------------------------------------------------ sources

    def _source(self, subcategory: str) -> Optional[DataSource]:
        preferred = settings.get("cadastre.source_id", "")
        candidates = [source for source in self.registry.query(categories=["cadastral"])
                      if source.subcategory == subcategory]
        if preferred:
            matching = [s for s in candidates if s.id.startswith(preferred)]
            if matching:
                return matching[0]
        return candidates[0] if candidates else None

    # ------------------------------------------------------------------ run

    def run(self, area: ProjectArea, *, feedback: Optional[Feedback] = None,
            refresh: bool = False, include_sheets: Optional[bool] = None) -> CadastreResult:
        """Run the cadastral analysis for ``area``."""
        feedback = feedback or NullFeedback()
        result = CadastreResult()
        parcel_source = self._source("parcel")
        if parcel_source is None:
            result.warnings.append("Nessuna sorgente catastale configurata")
            return result

        sheets_map: Dict[str, str] = {}
        if include_sheets if include_sheets is not None else settings.get(
                "cadastre.sheet_from_zoning", True):
            sheets_map = self._fetch_sheets(area, result, feedback, refresh=refresh)

        feedback.set_step("Interrogazione particelle catastali")
        try:
            fetched = fetcher.fetch_features(
                parcel_source, area.bbox(), area.crs, target_crs=area.work_crs,
                area_id=area.id, refresh=refresh,
                feedback=ChildFeedback(feedback, 30, 70), http=self.http, cache=self.cache)
        except UserCancelled:
            raise
        except SourceError as exc:
            result.source_results.append(spatial.failed_result(parcel_source, str(exc)))
            result.warnings.append(f"Servizio catastale non disponibile: {exc}")
            return result

        layer = fetched.layer(PARCELS_LAYER)
        if layer is None or fetched.feature_count == 0:
            result.source_results.append(spatial.failed_result(
                parcel_source, "nessuna particella restituita per l'area",
                status=SourceStatus.EMPTY))
            result.warnings.append(
                "Il servizio catastale non ha restituito particelle per quest'area. "
                "Nelle Province autonome di Trento e Bolzano il catasto non e' gestito "
                "dall'Agenzia delle Entrate e va consultato presso il catasto provinciale.")
            return result
        result.truncated = fetched.truncated
        if fetched.truncated:
            result.warnings.append(
                "Numero di particelle troncato al limite configurato: ridurre l'area "
                "o aumentare 'network.max_features_per_source'.")

        feedback.set_step("Intersezione con l'area di progetto")
        rows, features = self._build_rows(area, layer, parcel_source, sheets_map, feedback)
        result.rows = rows
        result.municipalities = self._municipalities(rows, area)

        if features:
            gpkg = Path(area_dir(area.id)) / CADASTRE_GPKG
            materialised = vector_io.write_features(
                features, self._parcel_fields(), "MultiPolygon", area.work_crs,
                gpkg, PARCELS_LAYER, append=True)
            result.gpkg_path = str(materialised.path)
            result.layers.append(LayerRef(name="Particelle interessate", uri=materialised.uri,
                                          category="cadastral", group="Cadastre",
                                          style="cadastral_parcel",
                                          source_id=parcel_source.id))
        result.source_results.append(SourceResult(
            source_id=parcel_source.id,
            source_name=parcel_source.name,
            category="cadastral",
            status=SourceStatus.CACHED if fetched.from_cache else SourceStatus.ONLINE,
            present=bool(rows),
            feature_count=len(rows),
            intersect_area_m2=sum(row.area_intersect_m2 for row in rows),
            intersect_pct=measure.percentage(sum(row.area_intersect_m2 for row in rows),
                                             area.area_m2),
            min_distance_m=0.0 if rows else None,
            layer_uri=result.layers[0].uri if result.layers else "",
            layer_name="Particelle interessate",
            provenance=parcel_source.provenance(operation="cadastral intersection",
                                                crs=area.work_crs.authid()),
            elapsed_ms=fetched.elapsed_ms,
            from_cache=fetched.from_cache,
        ))
        feedback.set_progress(100)
        return result

    # ------------------------------------------------------------------ steps

    def _fetch_sheets(self, area: ProjectArea, result: CadastreResult, feedback: Feedback,
                      *, refresh: bool) -> Dict[str, str]:
        """Fetch the cadastral sheets and return ``{zoning reference: label}``."""
        source = self._source("zoning")
        if source is None:
            return {}
        feedback.set_step("Individuazione fogli catastali")
        try:
            fetched = fetcher.fetch_features(
                source, area.bbox(), area.crs, target_crs=area.work_crs, area_id=area.id,
                refresh=refresh, feedback=ChildFeedback(feedback, 0, 30),
                http=self.http, cache=self.cache)
        except UserCancelled:
            raise
        except SourceError as exc:
            result.warnings.append(f"Fogli catastali non disponibili: {exc}")
            result.source_results.append(spatial.failed_result(source, str(exc)))
            return {}
        layer = fetched.layer(SHEETS_LAYER)
        if layer is None:
            return {}
        code_field = source.fields.get("code", "NATIONALCADASTRALZONINGREFERENCE")
        label_field = source.fields.get("label", "LABEL")
        names = [f.name() for f in layer.fields()]
        if code_field not in names or label_field not in names:
            return {}
        mapping: Dict[str, str] = {}
        for feature in layer.getFeatures():
            reference = str(feature[code_field] or "").strip().upper()
            label = str(feature[label_field] or "").strip()
            if reference and label:
                mapping[reference] = label
        if mapping:
            gpkg = Path(area_dir(area.id)) / CADASTRE_GPKG
            try:
                materialised = vector_io.write_layer(layer, gpkg, SHEETS_LAYER,
                                                     target_crs=area.work_crs, append=True)
                result.layers.append(LayerRef(name="Fogli catastali", uri=materialised.uri,
                                              category="cadastral", group="Cadastre",
                                              style="cadastral_zoning", source_id=source.id))
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("Cannot store the cadastral sheets layer", exc)
                result.warnings.append(f"Fogli catastali non salvati: {exc}")
        result.source_results.append(SourceResult(
            source_id=source.id, source_name=source.name, category="cadastral",
            status=SourceStatus.CACHED if fetched.from_cache else SourceStatus.ONLINE,
            present=bool(mapping), feature_count=fetched.feature_count,
            provenance=source.provenance(operation="cadastral zoning",
                                         crs=area.work_crs.authid()),
            elapsed_ms=fetched.elapsed_ms, from_cache=fetched.from_cache))
        return mapping

    @staticmethod
    def _parcel_fields():
        """Schema of the parcel layer produced by the engine."""
        return qt_compat.fields_from([
            ("municipality", qt_compat.STRING, "Comune"),
            ("istat", qt_compat.STRING, "Codice ISTAT"),
            ("cad_code", qt_compat.STRING, "Codice catastale"),
            ("sheet", qt_compat.STRING, "Foglio"),
            ("parcel", qt_compat.STRING, "Particella"),
            ("national_ref", qt_compat.STRING, "Identificativo nazionale"),
            ("area_graph_m2", qt_compat.DOUBLE, "Superficie grafica (m2)"),
            ("area_int_m2", qt_compat.DOUBLE, "Superficie interessata (m2)"),
            ("int_pct", qt_compat.DOUBLE, "Percentuale interessata"),
        ])

    def _build_rows(self, area: ProjectArea, layer: QgsVectorLayer, source: DataSource,
                    sheets_map: Dict[str, str], feedback: Feedback):
        """Intersect every parcel with the area and build the table rows."""
        work_crs = area.work_crs
        area_geom = geom_utils.ensure_valid(area.geometry_in(work_crs))
        fields = self._parcel_fields()
        reference_field = source.fields.get("code", "NATIONALCADASTRALREFERENCE")
        label_field = source.fields.get("label", "LABEL")
        municipality_field = source.fields.get("municipality", "ADMINISTRATIVEUNIT")
        names = [f.name() for f in layer.fields()]
        max_parcels = int(settings.get("cadastre.max_parcels", 5000))

        rows: List[CadastralRow] = []
        features: List[QgsFeature] = []
        for feature in layer.getFeatures():
            if feedback.is_canceled():
                raise UserCancelled()
            if len(rows) >= max_parcels:
                break
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if layer.crs() != work_crs:
                try:
                    geometry = crs_utils.transform_geometry(geometry, layer.crs(), work_crs)
                except Exception as exc:  # pragma: no cover - broken feature
                    log.debug(f"_build_rows: elemento saltato ({type(exc).__name__}: {exc})")
                    continue
            try:
                geometry = geom_utils.ensure_valid(geometry, context="parcel")
            except Exception as exc:
                log.debug(f"_build_rows: elemento saltato ({type(exc).__name__}: {exc})")
                continue
            if not geometry.intersects(area_geom):
                continue
            clipped = geom_utils.intersection(geometry, area_geom)
            if clipped.isEmpty():
                continue

            reference = str(feature[reference_field]).strip() \
                if reference_field in names else ""
            parts = parse_reference(reference)
            municipality_code = (str(feature[municipality_field]).strip().upper()
                                 if municipality_field in names
                                 else parts.get("municipality_code", ""))
            row_info = self.index.by_cadastral(municipality_code)
            sheet = sheets_map.get(parts.get("zoning", ""), "") or sheet_label(parts)
            parcel_label = str(feature[label_field]).strip() if label_field in names \
                else parts.get("parcel", "")

            graphic_area = measure.area_m2(geometry, work_crs)
            intersect_area = measure.area_m2(clipped, work_crs)
            row = CadastralRow(
                municipality=row_info.name if row_info else f"[{municipality_code}]",
                istat_code=row_info.istat if row_info else "",
                cadastral_code=municipality_code,
                sheet=sheet,
                parcel=parcel_label or parts.get("parcel", ""),
                national_ref=reference,
                area_cadastral_m2=graphic_area,
                area_intersect_m2=intersect_area,
                intersect_pct=measure.percentage(intersect_area, graphic_area),
                geometry_wkt=clipped.asWkt(4) if settings.get(
                    "analysis.keep_intersection_geometry", True) else "",
            )
            rows.append(row)

            output = QgsFeature(fields)
            output.setGeometry(QgsGeometry(geometry))
            output.setAttributes([
                row.municipality, row.istat_code, row.cadastral_code, row.sheet,
                row.parcel, row.national_ref, round(row.area_cadastral_m2, 2),
                round(row.area_intersect_m2, 2), round(row.intersect_pct, 2),
            ])
            features.append(output)

        rows.sort(key=_row_sort_key)
        features.sort(key=lambda f: _row_sort_key(CadastralRow(
            municipality=str(f["municipality"]), sheet=str(f["sheet"]),
            parcel=str(f["parcel"]))))
        return rows, features

    def _municipalities(self, rows: List[CadastralRow], area: ProjectArea) -> List[AdminUnit]:
        """Summarise the municipalities touched by the parcels."""
        totals: Dict[str, float] = {}
        for row in rows:
            totals[row.cadastral_code] = totals.get(row.cadastral_code, 0.0) + \
                row.area_intersect_m2
        units: List[AdminUnit] = []
        for code, total in sorted(totals.items(), key=lambda item: -item[1]):
            info = self.index.by_cadastral(code)
            units.append(AdminUnit(
                name=info.name if info else f"[{code}]",
                istat_code=info.istat if info else "",
                cadastral_code=code,
                province_code=info.province_abbr if info else "",
                region_name=info.region if info else "",
                area_share_pct=measure.percentage(total, area.area_m2),
                source_id="cadastre",
            ))
        return units


def _row_sort_key(row: CadastralRow):
    """Sort by municipality, then sheet and parcel numerically when possible."""
    def numeric(value: str):
        digits = re.match(r"^\d+", (value or "").strip())
        return (int(digits.group()) if digits else 10 ** 9, value or "")

    return (row.municipality or "", numeric(row.sheet), numeric(row.parcel))
