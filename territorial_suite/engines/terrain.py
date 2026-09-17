"""Terrain engine: DEM acquisition, elevation statistics, slope, aspect, hillshade, contours.

Only GDAL is used (shipped with QGIS): no extra dependency, and the same code path works
headless, in Processing and in the GUI.

Supported DEM sources (declared in ``config/sources/terrain/``):

``RASTER``          a local or remote GeoTIFF (a DTM the user already owns);
``TERRAIN_TILES``   a tiled GeoTIFF pyramid in the XYZ scheme (verified against the AWS
                    "Terrain Tiles" open dataset: 512x512 Int16 tiles in EPSG:3857).

Everything is clipped to the project area and reprojected to the metric work CRS, so slope
is computed on real metres and the statistics refer exactly to the area, not to its bbox.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from qgis.core import QgsGeometry

from ..core import cache as cache_module
from ..core import crs as crs_utils
from ..core import log, settings
from ..core.cache import CacheManager, make_key
from ..core.gaps import DataGap
from ..core.errors import EngineError, SourceError, SourceUnavailableError, UserCancelled
from ..core.feedback import ChildFeedback, Feedback, NullFeedback
from ..core.models import LayerRef, SlopeClass, SourceType, TerrainStats
from ..core.paths import area_dir, temp_dir
from ..core.project_area import ProjectArea
from ..core.registry import DataSource, DataSourceRegistry
from ..services.http import HttpClient

EARTH_CIRCUMFERENCE = 40_075_016.686


@dataclass
class TerrainOutputs:
    """Result of the terrain analysis."""

    stats: TerrainStats = field(default_factory=TerrainStats)
    layers: List[LayerRef] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        """Whether elevation statistics could be computed."""
        return self.stats.available


@dataclass
class TileCoverage:
    """What the tile acquisition actually managed, as opposed to what was asked.

    Two silent degradations used to be possible here and both are recorded now: a zoom
    lowered to respect the tile budget, and tiles that never arrived. Either one makes a
    later statistic describe something other than the area the user asked about.
    """

    requested_cell_size_m: float = 0.0
    effective_cell_size_m: float = 0.0
    zoom_requested: int = 0
    zoom_used: int = 0
    tiles_expected: int = 0
    tiles_missing: int = 0

    @property
    def complete(self) -> bool:
        """Whether every tile the area needed was obtained."""
        return self.tiles_missing == 0

    @property
    def reduced(self) -> bool:
        """Whether the detail had to be lowered to fit the tile budget."""
        return self.zoom_used < self.zoom_requested

    def gaps(self) -> List[str]:
        """The gap values this acquisition has to declare."""
        found: List[str] = []
        if not self.complete:
            found.append(DataGap.PARTIAL_COVERAGE.value)
        if self.reduced:
            found.append(DataGap.REDUCED_RESOLUTION.value)
        return found

    def note(self) -> str:
        """One sentence for the dossier, empty when nothing degraded."""
        parts: List[str] = []
        if not self.complete:
            parts.append(f"{self.tiles_missing} tile su {self.tiles_expected} non "
                         f"scaricate: il mosaico non copre l'intera area")
        if self.reduced:
            parts.append(f"dettaglio ridotto a {self.effective_cell_size_m:.1f} m "
                         f"(richiesti {self.requested_cell_size_m:.1f} m) per rispettare "
                         f"il limite di tile")
        return "; ".join(parts)


def _gdal():
    """Import GDAL lazily and enable exceptions once."""
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def _dem_processing(destination: Path, source: Path, algorithm: str, **options) -> None:
    """Run ``gdal.DEMProcessing`` and release the returned dataset deterministically.

    The dataset returned by GDAL's Python helpers must be flushed and dropped: keeping it
    alive (or letting the garbage collector decide when to free it) leaves the output file
    incomplete and, worse, mixes GDAL's lifetime with Python's.
    """
    gdal = _gdal()
    result = None
    try:
        result = gdal.DEMProcessing(str(destination), str(source), algorithm, **options)
        if result is None:
            raise EngineError(f"GDAL {algorithm} non ha prodotto un risultato")
    finally:
        if result is not None:
            result.FlushCache()
        result = None


def tile_for(lon: float, lat: float, zoom: int) -> Tuple[int, int]:
    """Return the XYZ tile containing a geographic coordinate."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def zoom_for_resolution(target_m: float, latitude: float, tile_size: int,
                        *, zmin: int = 0, zmax: int = 15) -> int:
    """Pick the smallest zoom whose ground resolution is finer than ``target_m``.

    Web Mercator resolution is inflated by ``1/cos(latitude)``: the ground resolution of a
    tile at latitude ``lat`` is ``res_mercator * cos(lat)``.
    """
    cos_lat = max(math.cos(math.radians(latitude)), 1e-6)
    for zoom in range(zmin, zmax + 1):
        ground = (EARTH_CIRCUMFERENCE / (2 ** zoom * tile_size)) * cos_lat
        if ground <= target_m:
            return zoom
    return zmax


#: Style name prefix of a baked composite: "shaded_slope", "shaded_dem", ...
SHADED_STYLE_PREFIX = "shaded_"


def bake_shaded_relief(colour_source: Path, hillshade_path: Path, destination: Path, *,
                       ramp: str, opacity: float = 0.55) -> Path:
    """Flatten "colour ramp x hillshade" into a three-band RGB GeoTIFF.

    Why this exists: on the canvas the shading is a *blend mode*, and QGIS drops layer
    blend modes in several print and PDF export paths. A sheet would then come out flat,
    which is exactly the difference between a readable relief map and a coloured blob.
    Baking the composite once makes the printed sheet look like the screen.

    The maths is the same the canvas performs: ``rgb x (grey/255)``, with the shading
    attenuated by ``opacity`` so the colours stay legible.

    :param colour_source: DEM, slope or aspect raster (single band, real values).
    :param hillshade_path: the 0-255 hillshade of the same DEM, same grid.
    :param ramp: name of the ramp in ``config/styles/styles.json``.
    :raises EngineError: when the two rasters do not share the same grid.
    """
    import numpy

    from .cartography import styling

    if not styling.has_ramp(ramp):
        raise EngineError(f"Rampa di colore sconosciuta: {ramp}")
    gdal = _gdal()
    values, transform = TerrainEngine._read_array(colour_source)
    shade, shade_transform = TerrainEngine._read_array(hillshade_path)
    if values.shape != shade.shape:
        raise EngineError(
            f"Griglie diverse fra {colour_source.name} ({values.shape}) e "
            f"{hillshade_path.name} ({shade.shape}): composito non calcolabile")

    table = styling.ramp_lookup(ramp)
    if not table:
        raise EngineError(f"Rampa di colore vuota: {ramp}")
    spec = styling.ramp_spec(ramp)
    filled = values.filled(numpy.nan)
    if spec["type"] == "normalised":
        finite = numpy.isfinite(filled)
        low = float(numpy.nanmin(filled)) if finite.any() else 0.0
        high = float(numpy.nanmax(filled)) if finite.any() else 1.0
        span = (high - low) or 1.0
        bounds = [low + bound * span for bound, _ in table]
    else:
        bounds = [bound for bound, _ in table]

    height, width = values.shape
    rgb = numpy.zeros((3, height, width), dtype="float64")
    # Discrete classification: the first bound a value does not exceed wins, and anything
    # above the last bound keeps the last colour.
    remaining = numpy.ones((height, width), dtype=bool)
    for bound, colour in zip(bounds, [entry[1] for entry in table]):
        selected = remaining & (filled <= bound)
        for channel in range(3):
            rgb[channel][selected] = colour[channel]
        remaining &= ~selected
    if remaining.any():
        last = table[-1][1]
        for channel in range(3):
            rgb[channel][remaining] = last[channel]

    grey = numpy.clip(shade.filled(255.0) / 255.0, 0.0, 1.0)
    strength = max(0.0, min(1.0, float(opacity)))
    # opacity 0 -> pure colour, opacity 1 -> full multiply.
    factor = 1.0 - strength + strength * grey
    rgb *= factor
    nodata_mask = numpy.ma.getmaskarray(values)
    alpha = numpy.where(nodata_mask, 0, 255).astype("uint8")
    rgb = numpy.clip(rgb, 0, 255).astype("uint8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(str(destination), width, height, 4, gdal.GDT_Byte,
                            options=["COMPRESS=DEFLATE", "TILED=YES", "PHOTOMETRIC=RGB",
                                     "ALPHA=YES"])
    if dataset is None:
        raise EngineError(f"Impossibile creare il composito {destination.name}")
    try:
        dataset.SetGeoTransform(transform)
        reference = gdal.Open(str(colour_source))
        if reference is not None:
            dataset.SetProjection(reference.GetProjection())
            reference = None
        for index in range(3):
            dataset.GetRasterBand(index + 1).WriteArray(rgb[index])
        dataset.GetRasterBand(4).WriteArray(alpha)
        dataset.FlushCache()
    finally:
        dataset = None
    return destination


class TerrainEngine:
    """Downloads a DEM for the project area and derives terrain information."""

    def __init__(self, *, registry: Optional[DataSourceRegistry] = None,
                 http: Optional[HttpClient] = None,
                 cache: Optional[CacheManager] = None) -> None:
        self.registry = registry or DataSourceRegistry.instance()
        self.http = http or HttpClient()
        self.cache = cache or CacheManager.instance()

    # ------------------------------------------------------------------ sources

    def available_sources(self) -> List[DataSource]:
        """Return the enabled terrain sources, best first."""
        return [source for source in self.registry.query(categories=["terrain"])
                if source.type in (SourceType.RASTER, SourceType.TERRAIN_TILES)]

    def pick_source(self, source_id: str = "") -> Optional[DataSource]:
        """Return the DEM source to use (explicit id, configured default, or first)."""
        wanted = source_id or settings.get("terrain.source_id", "")
        sources = self.available_sources()
        if wanted:
            for source in sources:
                if source.id == wanted:
                    return source
            log.warning(f"Terrain source '{wanted}' not found, using the default one")
        return sources[0] if sources else None

    # ------------------------------------------------------------------ run

    def run(self, area: ProjectArea, *, feedback: Optional[Feedback] = None,
            source_id: str = "", cell_size_m: Optional[float] = None,
            compute_slope: bool = True, compute_aspect: bool = True,
            compute_hillshade: bool = True, compute_contours: bool = False,
            refresh: bool = False) -> TerrainOutputs:
        """Acquire the DEM and compute elevation/slope statistics for ``area``."""
        feedback = feedback or NullFeedback()
        outputs = TerrainOutputs()
        source = self.pick_source(source_id)
        if source is None:
            outputs.warnings.append("Nessuna sorgente DEM configurata")
            return outputs

        cell_size = float(cell_size_m or settings.get("terrain.cell_size_m", 10))
        coverage = TileCoverage(requested_cell_size_m=cell_size,
                                effective_cell_size_m=cell_size)
        try:
            feedback.set_step(f"Acquisizione DEM ({source.name})")
            dem_path = self._acquire_dem(area, source, cell_size,
                                         ChildFeedback(feedback, 0, 50),
                                         refresh=refresh, coverage=coverage)
        except UserCancelled:
            raise
        except (SourceError, EngineError) as exc:
            outputs.warnings.append(f"DEM non disponibile: {exc}")
            feedback.push_warning(f"DEM non disponibile: {exc}")
            return outputs

        provenance = source.provenance(operation=f"DEM clip {cell_size:g} m",
                                       crs=area.work_crs.authid())
        outputs.stats = TerrainStats(
            source_id=source.id, cell_size_m=coverage.effective_cell_size_m or cell_size,
            requested_cell_size_m=cell_size,
            tiles_expected=coverage.tiles_expected, tiles_missing=coverage.tiles_missing,
            gaps=coverage.gaps(), coverage_note=coverage.note(),
            dem_path=str(dem_path), provenance=provenance, note=source.notes)
        # A DEM that covers only part of the area, or that came back coarser than asked,
        # is still useful - but every figure derived from it describes something other
        # than what the user requested, so it is said out loud rather than logged.
        if outputs.stats.coverage_note:
            message = f"DEM: {outputs.stats.coverage_note}"
            outputs.warnings.append(message)
            feedback.push_warning(message)
            if provenance is not None:
                provenance.notes = f"{provenance.notes} {message}.".strip()
        outputs.layers.append(LayerRef(name="DEM", uri=str(dem_path), provider="gdal",
                                       category="terrain", group="Terrain", style="dem",
                                       source_id=source.id, is_raster=True))

        feedback.set_step("Statistiche altimetriche")
        self._elevation_stats(dem_path, outputs.stats)

        if compute_slope:
            feedback.set_step("Calcolo pendenza")
            self._slope(area, dem_path, outputs)
        if compute_aspect:
            feedback.set_step("Calcolo esposizione")
            self._aspect(area, dem_path, outputs)
        if compute_hillshade:
            feedback.set_step("Calcolo ombreggiatura")
            self._hillshade(area, dem_path, outputs)
            if bool(settings.get("terrain.shaded_relief", True)):
                feedback.set_step("Composito rilievo ombreggiato")
                self._shaded_composites(area, outputs)
        if compute_contours:
            feedback.set_step("Curve di livello")
            self._contours(area, dem_path, outputs)
        feedback.set_progress(100)
        return outputs

    # ------------------------------------------------------------------ acquisition

    def _acquire_dem(self, area: ProjectArea, source: DataSource, cell_size: float,
                     feedback: Feedback, *, refresh: bool,
                     coverage: "TileCoverage") -> Path:
        """Return a DEM clipped to the area, in the work CRS, at ``cell_size`` metres."""
        if source.type == SourceType.RASTER:
            raw = self._local_raster(source)
        elif source.type == SourceType.TERRAIN_TILES:
            raw = self._download_tiles(area, source, cell_size, feedback,
                                       refresh=refresh, coverage=coverage)
        else:
            raise EngineError(f"Unsupported terrain source type {source.type.value}")
        return self._clip_to_area(area, raw, cell_size, source)

    @staticmethod
    def _local_raster(source: DataSource) -> Path:
        path_value = source.query.get("path") or source.url.replace("file://", "")
        path = Path(path_value)
        if not path_value or not path.exists():
            raise SourceUnavailableError(f"DEM file not found: {path_value}",
                                         source_id=source.id)
        return path

    def _download_tiles(self, area: ProjectArea, source: DataSource, cell_size: float,
                        feedback: Feedback, *, refresh: bool,
                        coverage: "TileCoverage") -> Path:
        """Download the XYZ elevation tiles covering the area and build a VRT.

        ``coverage`` is filled in as the work proceeds: the caller needs to know not only
        that a DEM came back, but whether it is the DEM that was asked for.
        """
        gdal = _gdal()
        tile_size = int(source.query.get("tile_size", 512))
        zmin = int(source.query.get("zmin", 0))
        zmax = int(source.query.get("zmax", 15))
        max_tiles = int(source.query.get("max_tiles", 64))

        bbox = area.context_bbox(crs_utils.WGS84, buffer_m=max(cell_size * 4, 100.0))
        centre = bbox.center()
        zoom = zoom_for_resolution(cell_size, centre.y(), tile_size, zmin=zmin, zmax=zmax)
        coverage.requested_cell_size_m = cell_size
        coverage.zoom_requested = zoom

        x_min, y_max = tile_for(bbox.xMinimum(), bbox.yMinimum(), zoom)
        x_max, y_min = tile_for(bbox.xMaximum(), bbox.yMaximum(), zoom)
        # Lowering the zoom keeps the download bounded, but it hands back a coarser DEM
        # than the caller asked for. That is a legitimate trade-off and an illegitimate
        # secret, so the amount of the reduction is recorded and declared.
        while (x_max - x_min + 1) * (y_max - y_min + 1) > max_tiles and zoom > zmin:
            zoom -= 1
            x_min, y_max = tile_for(bbox.xMinimum(), bbox.yMinimum(), zoom)
            x_max, y_min = tile_for(bbox.xMaximum(), bbox.yMaximum(), zoom)
        tiles = [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]
        if not tiles:
            raise EngineError("No elevation tile covers the project area")
        coverage.zoom_used = zoom
        coverage.tiles_expected = len(tiles)
        coverage.effective_cell_size_m = cell_size * (2 ** (coverage.zoom_requested - zoom))
        feedback.push_debug(f"{source.id}: zoom {zoom}, {len(tiles)} tiles")

        paths: List[str] = []
        for index, (x, y) in enumerate(tiles, start=1):
            if feedback.is_canceled():
                raise UserCancelled()
            url = (source.url.replace("{z}", str(zoom)).replace("{x}", str(x))
                   .replace("{y}", str(y)))
            key = make_key(source.id, op="tile", z=zoom, x=x, y=y)
            entry = None if refresh else self.cache.get(key)
            if entry is not None and entry.path.exists():
                paths.append(str(entry.path))
            else:
                target = self.cache.path_for(cache_module.KIND_RASTER, source.id, key, ".tif")
                try:
                    self.http.get_to_file(url, target, source_id=source.id, feedback=feedback)
                except SourceError as exc:
                    feedback.push_warning(f"Tile {zoom}/{x}/{y} non scaricata: {exc}")
                    coverage.tiles_missing += 1
                    continue
                self.cache.put(key, target, kind=cache_module.KIND_RASTER,
                               source_id=source.id, area_id=area.id,
                               ttl_seconds=self.cache.ttl_seconds("raster"))
                paths.append(str(target))
            feedback.set_progress(100.0 * index / len(tiles))
        if not paths:
            raise SourceUnavailableError("No elevation tile could be downloaded",
                                         source_id=source.id)
        vrt_path = temp_dir() / "terrain" / f"{area.id}_{zoom}.vrt"
        vrt_path.parent.mkdir(parents=True, exist_ok=True)
        vrt = gdal.BuildVRT(str(vrt_path), paths)
        if vrt is None:
            raise EngineError("Impossibile costruire il mosaico delle tile DEM")
        vrt.FlushCache()          # write the .vrt before anyone opens it
        vrt = None
        return vrt_path

    def _clip_to_area(self, area: ProjectArea, raw: Path, cell_size: float,
                      source: DataSource) -> Path:
        """Reproject and clip the DEM on the project area boundary."""
        gdal = _gdal()
        cutline = self._cutline(area)
        out_path = Path(area_dir(area.id)) / "dem.tif"
        nodata = float(source.query.get("nodata", -32768))
        warped = None
        try:
            warped = gdal.Warp(
                str(out_path), str(raw),
                dstSRS=area.work_crs.authid() or area.work_crs.toWkt(),
                xRes=cell_size, yRes=cell_size,
                cutlineDSName=str(cutline), cutlineLayer="area",
                cropToCutline=True, dstNodata=nodata,
                resampleAlg="bilinear", multithread=True,
                creationOptions=["COMPRESS=DEFLATE", "TILED=YES"],
            )
        except Exception as exc:  # pragma: no cover - GDAL failure
            raise EngineError(f"Cannot clip the DEM: {exc}") from exc
        finally:
            if warped is not None:
                warped.FlushCache()
            warped = None
        if not out_path.exists():
            raise EngineError("The clipped DEM was not produced")
        return out_path

    @staticmethod
    def _cutline(area: ProjectArea) -> Path:
        """Write the area geometry as a one-feature GeoPackage usable as GDAL cutline."""
        from ..services import vector_io

        geometry = area.geometry_in(area.work_crs)
        layer = vector_io.memory_layer("Polygon", area.work_crs, "area", None)
        from qgis.core import QgsFeature

        feature = QgsFeature()
        feature.setGeometry(QgsGeometry(geometry))
        layer.dataProvider().addFeature(feature)
        layer.updateExtents()
        path = temp_dir() / "terrain" / f"cutline_{area.id}.gpkg"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        vector_io.write_layer(layer, path, "area")
        return path

    # ------------------------------------------------------------------ statistics

    @staticmethod
    def _read_array(path: Path):
        """Read a single-band raster as a masked numpy array.

        GDAL lifetime rule: a ``Band`` object points into its ``Dataset``. Releasing the
        dataset while a band object is still alive is a use-after-free that corrupts the
        heap and crashes QGIS later, in unrelated code. The band is therefore always
        released **before** the dataset.
        """
        import numpy

        gdal = _gdal()
        dataset = gdal.Open(str(path))
        if dataset is None:
            raise EngineError(f"Cannot open raster {path.name}")
        band = None
        try:
            band = dataset.GetRasterBand(1)
            array = band.ReadAsArray()
            nodata = band.GetNoDataValue()
            transform = dataset.GetGeoTransform()
        finally:
            band = None
            dataset = None
        if array is None:
            raise EngineError(f"Empty raster {path.name}")
        data = numpy.ma.masked_invalid(array.astype("float64"))
        if nodata is not None:
            data = numpy.ma.masked_equal(data, nodata)
        return data, transform

    def _elevation_stats(self, dem_path: Path, stats: TerrainStats) -> None:
        """Fill the elevation statistics and histogram."""
        import numpy

        data, _ = self._read_array(dem_path)
        if data.count() == 0:
            log.warning("The clipped DEM has no valid pixel")
            return
        values = data.compressed()
        stats.elevation_min = float(numpy.min(values))
        stats.elevation_max = float(numpy.max(values))
        stats.elevation_mean = float(numpy.mean(values))
        stats.elevation_median = float(numpy.median(values))
        stats.elevation_std = float(numpy.std(values))
        stats.elevation_range = stats.elevation_max - stats.elevation_min
        bins = int(settings.get("terrain.histogram_bins", 20))
        counts, edges = numpy.histogram(values, bins=max(2, bins))
        total = float(counts.sum()) or 1.0
        stats.elevation_histogram = [
            {"from": float(edges[i]), "to": float(edges[i + 1]),
             "count": int(counts[i]), "pct": 100.0 * float(counts[i]) / total}
            for i in range(len(counts))
        ]

    def _slope(self, area: ProjectArea, dem_path: Path, outputs: TerrainOutputs) -> None:
        """Compute the slope raster, its statistics and the class breakdown."""
        import numpy

        unit = str(settings.get("terrain.slope_unit", "percent")).lower()
        slope_path = Path(area_dir(area.id)) / "slope.tif"
        try:
            _dem_processing(slope_path, dem_path, "slope",
                            slopeFormat="percent" if unit == "percent" else "degree",
                            computeEdges=True,
                            creationOptions=["COMPRESS=DEFLATE", "TILED=YES"])
        except Exception as exc:  # pragma: no cover - GDAL failure
            outputs.warnings.append(f"Pendenza non calcolata: {exc}")
            return
        data, transform = self._read_array(slope_path)
        if data.count() == 0:
            outputs.warnings.append("Pendenza non calcolabile (nessun pixel valido)")
            return
        values = data.compressed()
        outputs.stats.slope_mean_pct = float(numpy.mean(values))
        outputs.stats.slope_max_pct = float(numpy.max(values))
        outputs.stats.slope_path = str(slope_path)
        outputs.layers.append(LayerRef(name="Pendenza", uri=str(slope_path), provider="gdal",
                                       category="terrain", group="Terrain", style="slope",
                                       source_id=outputs.stats.source_id, is_raster=True))

        breaks = [float(value) for value in settings.get("terrain.slope_classes_percent",
                                                         [5, 10, 20, 30, 50])]
        cell_area = abs(transform[1] * transform[5]) or 1.0
        bounds = [0.0] + sorted(breaks)
        classes: List[SlopeClass] = []
        total = float(values.size) or 1.0
        for index, lower in enumerate(bounds):
            upper = bounds[index + 1] if index + 1 < len(bounds) else None
            if upper is None:
                mask = values >= lower
            else:
                mask = (values >= lower) & (values < upper)
            count = int(numpy.count_nonzero(mask))
            classes.append(SlopeClass(lower=lower, upper=upper, pixel_count=count,
                                      area_m2=count * cell_area,
                                      area_pct=100.0 * count / total))
        outputs.stats.slope_classes = classes

    def _aspect(self, area: ProjectArea, dem_path: Path, outputs: TerrainOutputs) -> None:
        """Compute the aspect raster and its eight-sector histogram."""
        import numpy

        aspect_path = Path(area_dir(area.id)) / "aspect.tif"
        try:
            _dem_processing(aspect_path, dem_path, "aspect", computeEdges=True,
                            creationOptions=["COMPRESS=DEFLATE", "TILED=YES"])
        except Exception as exc:  # pragma: no cover - GDAL failure
            outputs.warnings.append(f"Esposizione non calcolata: {exc}")
            return
        outputs.stats.aspect_path = str(aspect_path)
        outputs.layers.append(LayerRef(name="Esposizione", uri=str(aspect_path),
                                       provider="gdal", category="terrain", group="Terrain",
                                       style="aspect", source_id=outputs.stats.source_id,
                                       is_raster=True))
        data, _ = self._read_array(aspect_path)
        if data.count() == 0:
            return
        values = data.compressed()
        values = values[(values >= 0) & (values <= 360)]
        if values.size == 0:
            return
        sectors = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        shifted = (values + 22.5) % 360.0
        indices = (shifted / 45.0).astype(int) % 8
        histogram: Dict[str, float] = {}
        for index, name in enumerate(sectors):
            histogram[name] = 100.0 * float(numpy.count_nonzero(indices == index)) / values.size
        outputs.stats.aspect_histogram = histogram

    def _hillshade(self, area: ProjectArea, dem_path: Path, outputs: TerrainOutputs) -> None:
        """Compute the hillshade used as a background in the maps."""
        path = Path(area_dir(area.id)) / "hillshade.tif"
        try:
            _dem_processing(path, dem_path, "hillshade", computeEdges=True,
                            azimuth=float(settings.get("terrain.hillshade_azimuth", 315)),
                            altitude=float(settings.get("terrain.hillshade_altitude", 45)),
                            zFactor=float(settings.get("terrain.hillshade_z_factor", 1.0)),
                            creationOptions=["COMPRESS=DEFLATE", "TILED=YES"])
        except Exception as exc:  # pragma: no cover - GDAL failure
            outputs.warnings.append(f"Ombreggiatura non calcolata: {exc}")
            return
        outputs.stats.hillshade_path = str(path)
        outputs.layers.append(LayerRef(name="Ombreggiatura", uri=str(path), provider="gdal",
                                       category="terrain", group="Terrain", style="hillshade",
                                       source_id=outputs.stats.source_id, is_raster=True))

    def _shaded_composites(self, area: ProjectArea, outputs: TerrainOutputs) -> None:
        """Bake DEM, slope and aspect over the hillshade, once, for print and package.

        Each composite is added as its own layer reference with ``style="shaded"``: it is
        already coloured, so no renderer is applied to it. The legend keeps using the
        colour ramp of the underlying theme, never the grey of the shading.
        """
        stats = outputs.stats
        if stats is None or not stats.hillshade_path:
            return
        hillshade = Path(stats.hillshade_path)
        opacity = float(settings.get("terrain.hillshade_opacity", 0.55))
        from .cartography import styling

        themes = (("dem", stats.dem_path, "Rilievo ombreggiato (quote)"),
                  ("slope", stats.slope_path, "Rilievo ombreggiato (pendenza)"),
                  ("aspect", stats.aspect_path, "Rilievo ombreggiato (esposizione)"))
        for key, source_path, label in themes:
            if not source_path:
                continue
            # The theme is "dem", its palette is "terrain": ask the style, do not guess.
            ramp = styling.ramp_for_style(key)
            destination = Path(area_dir(area.id)) / f"{key}_shaded.tif"
            try:
                bake_shaded_relief(Path(source_path), hillshade, destination,
                                   ramp=ramp, opacity=opacity)
            except Exception as exc:
                # GDAL and numpy raise a wide range of types here; whatever happens the
                # composite is optional, so the run continues - but never silently.
                message = f"Composito ombreggiato '{key}' non calcolato: {exc}"
                outputs.warnings.append(message)
                log.warning(message)
                continue
            stats.shaded_paths[key] = str(destination)
            outputs.layers.append(LayerRef(
                name=label, uri=str(destination), provider="gdal", category="terrain",
                group="Terrain", style=f"{SHADED_STYLE_PREFIX}{key}",
                source_id=stats.source_id, is_raster=True))
        if stats.shaded_paths and stats.provenance is not None:
            azimuth = settings.get("terrain.hillshade_azimuth", 315)
            altitude = settings.get("terrain.hillshade_altitude", 45)
            note = (f"Compositi rilievo ombreggiato: rampa cromatica per tema "
                    f"moltiplicata per l'ombreggiatura (azimut {azimuth} gradi, "
                    f"altezza {altitude} gradi, opacita' {opacity:g}), "
                    f"algoritmo GDAL hillshade sul DEM {Path(stats.dem_path).name}.")
            stats.provenance.notes = f"{stats.provenance.notes} {note}".strip()

    def _contours(self, area: ProjectArea, dem_path: Path, outputs: TerrainOutputs) -> None:
        """Generate contour lines at the configured interval."""
        gdal = _gdal()
        from osgeo import ogr, osr

        interval = float(settings.get("terrain.contour_interval_m", 25))
        path = Path(area_dir(area.id)) / "contours.gpkg"
        dataset = band = out = layer = None
        try:
            dataset = gdal.Open(str(dem_path))
            if dataset is None:
                raise EngineError("DEM non leggibile")
            band = dataset.GetRasterBand(1)
            driver = ogr.GetDriverByName("GPKG")
            if path.exists():
                path.unlink()
            out = driver.CreateDataSource(str(path))
            srs = osr.SpatialReference()
            srs.ImportFromWkt(dataset.GetProjection())
            layer = out.CreateLayer("contours", srs, ogr.wkbLineString)
            layer.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
            layer.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
            gdal.ContourGenerate(band, interval, 0, [], 1, band.GetNoDataValue() or -32768,
                                 layer, 0, 1)
        except Exception as exc:  # pragma: no cover - GDAL failure
            outputs.warnings.append(f"Curve di livello non generate: {exc}")
            return
        finally:
            # Release in dependency order: layer -> datasource, band -> dataset.
            layer = None
            if out is not None:
                out.FlushCache()
            out = None
            band = None
            dataset = None
        outputs.stats.contours_path = str(path)
        outputs.layers.append(LayerRef(name=f"Curve di livello ({interval:g} m)",
                                       uri=f"{path}|layername=contours", category="terrain",
                                       group="Terrain", style="contours",
                                       source_id=outputs.stats.source_id))

    # ------------------------------------------------------------------ profile

    def elevation_profile(self, area: ProjectArea, line: QgsGeometry, *,
                          dem_path: Optional[Path] = None,
                          samples: Optional[int] = None) -> List[Dict[str, float]]:
        """Sample the DEM along a line and return ``[{distance_m, elevation}]``."""
        import numpy

        path = Path(dem_path) if dem_path else Path(area_dir(area.id)) / "dem.tif"
        if not path.exists():
            raise EngineError("No DEM available for the profile: run the terrain analysis first")
        count = int(samples or settings.get("terrain.profile_samples", 500))
        data, transform = self._read_array(path)
        origin_x, pixel_w, _, origin_y, _, pixel_h = transform
        length = line.length()
        if length <= 0:
            return []
        profile: List[Dict[str, float]] = []
        for index in range(count + 1):
            distance = length * index / count
            point = line.interpolate(distance)
            if point is None or point.isEmpty():
                continue
            xy = point.asPoint()
            column = int((xy.x() - origin_x) / pixel_w)
            row = int((xy.y() - origin_y) / pixel_h)
            if 0 <= row < data.shape[0] and 0 <= column < data.shape[1]:
                value = data[row, column]
                if value is not numpy.ma.masked:
                    profile.append({"distance_m": float(distance), "elevation": float(value)})
        return profile


def summarise(stats: TerrainStats) -> str:
    """Return the compact ALTIMETRIA block shown in the dock and in the report."""
    if not stats.available:
        return "ALTIMETRIA\n----------\nDati non disponibili"
    lines = [
        "ALTIMETRIA",
        "----------",
        f"Min:   {stats.elevation_min:.0f} m",
        f"Max:   {stats.elevation_max:.0f} m",
        f"Media: {stats.elevation_mean:.0f} m",
        f"Range: {stats.elevation_range:.0f} m",
    ]
    if stats.slope_mean_pct is not None:
        lines.append(f"Pendenza media: {stats.slope_mean_pct:.1f} %")
        lines.append(f"Pendenza max:   {stats.slope_max_pct:.1f} %")
    return "\n".join(lines)
