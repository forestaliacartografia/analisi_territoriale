"""Terrain algorithms: DEM download, statistics, slope classes, elevation profile."""

from __future__ import annotations

import json
from pathlib import Path

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessing,
)

from ...core import crs as crs_utils
from ...engines.terrain import TerrainEngine, summarise
from .base import TerritorialAlgorithm


class TerrainStatisticsAlgorithm(TerritorialAlgorithm):
    """DEM acquisition plus elevation and slope statistics."""

    SOURCE = "SOURCE"
    CELL_SIZE = "CELL_SIZE"
    CONTOURS = "CONTOURS"
    OUTPUT_DEM = "OUTPUT_DEM"
    OUTPUT_SLOPE = "OUTPUT_SLOPE"
    OUTPUT_JSON = "OUTPUT_JSON"

    def __init__(self) -> None:
        super().__init__()
        self._sources = []

    def name(self) -> str:
        return "terrain_statistics"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Statistiche del terreno"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Acquisisce il DEM per l'area, lo ritaglia sul perimetro e calcola quote "
                "(min, max, media, mediana, dislivello), pendenza media e massima, classi "
                "di pendenza ed esposizione. Puo' generare anche le curve di livello.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self._sources = TerrainEngine().available_sources()
        labels = [source.name for source in self._sources] or ["(nessuna sorgente)"]
        self.addParameter(QgsProcessingParameterEnum(self.SOURCE, "Sorgente DEM", labels,
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_SIZE, "Risoluzione (m)", QgsProcessingParameterNumber.Type.Double,
            defaultValue=10.0, minValue=1.0, maxValue=500.0))
        self.addParameter(QgsProcessingParameterBoolean(self.CONTOURS,
                                                        "Genera le curve di livello", False))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_DEM, "DEM ritagliato", optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_SLOPE, "Pendenza", optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_JSON, "Statistiche (JSON)", fileFilter="JSON (*.json)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        if not self._sources:
            raise QgsProcessingException("Nessuna sorgente DEM configurata nel catalogo.")
        index = self.parameterAsEnum(parameters, self.SOURCE, context)
        source = self._sources[min(index, len(self._sources) - 1)]
        cell_size = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        contours = self.parameterAsBool(parameters, self.CONTOURS, context)

        outputs = TerrainEngine().run(area, feedback=self.feedback_adapter(feedback),
                                      source_id=source.id, cell_size_m=cell_size,
                                      compute_contours=contours)
        for warning in outputs.warnings:
            feedback.pushWarning(warning)
        if not outputs.available:
            raise QgsProcessingException("Statistiche del terreno non disponibili: "
                                         + "; ".join(outputs.warnings))
        feedback.pushInfo(summarise(outputs.stats))

        results = {"ELEVATION_MIN": outputs.stats.elevation_min,
                   "ELEVATION_MAX": outputs.stats.elevation_max,
                   "ELEVATION_MEAN": outputs.stats.elevation_mean,
                   "SLOPE_MEAN_PCT": outputs.stats.slope_mean_pct}
        dem_target = self.parameterAsOutputLayer(parameters, self.OUTPUT_DEM, context)
        if dem_target and outputs.stats.dem_path:
            self._copy(outputs.stats.dem_path, dem_target)
            results[self.OUTPUT_DEM] = dem_target
        slope_target = self.parameterAsOutputLayer(parameters, self.OUTPUT_SLOPE, context)
        if slope_target and outputs.stats.slope_path:
            self._copy(outputs.stats.slope_path, slope_target)
            results[self.OUTPUT_SLOPE] = slope_target
        json_target = self.parameterAsFileOutput(parameters, self.OUTPUT_JSON, context)
        if json_target:
            Path(json_target).parent.mkdir(parents=True, exist_ok=True)
            Path(json_target).write_text(json.dumps(outputs.stats.as_dict(), indent=2,
                                                    ensure_ascii=False), encoding="utf-8")
            results[self.OUTPUT_JSON] = json_target
        return results

    @staticmethod
    def _copy(source: str, destination: str) -> None:
        import shutil

        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


class ElevationProfileAlgorithm(TerritorialAlgorithm):
    """Sample the DEM along a line."""

    LINE = "LINE"
    SAMPLES = "SAMPLES"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "elevation_profile"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Profilo altimetrico"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Campiona il DEM dell'area lungo una linea e produce un CSV con distanza "
                "progressiva e quota. Richiede che le statistiche del terreno siano gia' "
                "state calcolate per l'area.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE, "Linea del profilo", [QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterNumber(
            self.SAMPLES, "Numero di campioni", QgsProcessingParameterNumber.Type.Integer,
            defaultValue=200, minValue=10, maxValue=5000))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Profilo (CSV)", fileFilter="CSV (*.csv)"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        import csv

        area = self.project_area(parameters, context, feedback)
        source = self.parameterAsSource(parameters, self.LINE, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.LINE))
        geometries = [feature.geometry() for feature in source.getFeatures()
                      if feature.hasGeometry()]
        if not geometries:
            raise QgsProcessingException("La linea del profilo e' vuota.")
        line = crs_utils.transform_geometry(geometries[0], source.sourceCrs(), area.work_crs)
        samples = self.parameterAsInt(parameters, self.SAMPLES, context)
        engine = TerrainEngine()
        try:
            profile = engine.elevation_profile(area, line, samples=samples)
        except Exception as exc:
            raise QgsProcessingException(str(exc)) from exc
        path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["distanza_m", "quota_m"])
            for point in profile:
                writer.writerow([round(point["distance_m"], 2),
                                 round(point["elevation"], 2)])
        feedback.pushInfo(f"{len(profile)} campioni scritti in {path}")
        return {self.OUTPUT: path, "SAMPLES": len(profile)}
