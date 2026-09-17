"""Area algorithms: geometry statistics, buffers and the one-click analysis."""

from __future__ import annotations

import json
from pathlib import Path

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsGeometry,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
)

from ...core import measure, qt_compat
from ...engines.analysis import AnalysisOptions, AnalysisOrchestrator
from .base import TerritorialAlgorithm


class AreaStatisticsAlgorithm(TerritorialAlgorithm):
    """Geometric descriptors of the project area."""

    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "area_statistics"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Statistiche geometriche dell'area"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Calcola superficie, perimetro, bounding box, centroide, compattezza, "
                "elongazione e convessita' dell'area di progetto. Le misure di superficie "
                "e perimetro sono ellissoidiche.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Area con statistiche", optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        metrics = area.metrics()
        fields = qt_compat.fields_from([
            ("name", qt_compat.STRING), ("area_m2", qt_compat.DOUBLE),
            ("area_ha", qt_compat.DOUBLE), ("area_km2", qt_compat.DOUBLE),
            ("perimeter_m", qt_compat.DOUBLE), ("compactness", qt_compat.DOUBLE),
            ("elongation", qt_compat.DOUBLE), ("convexity", qt_compat.DOUBLE),
            ("radius_eq_m", qt_compat.DOUBLE), ("parts", qt_compat.INT),
            ("vertices", qt_compat.INT), ("crs", qt_compat.STRING),
        ])
        sink, sink_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                             area.geometry.wkbType(), area.crs)
        if sink is not None:
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry(area.geometry))
            feature.setAttributes([
                area.name, metrics.area_m2, metrics.area_m2 / 10_000.0,
                metrics.area_m2 / 1_000_000.0, metrics.perimeter_m, metrics.compactness,
                metrics.elongation, metrics.convexity,
                metrics.equivalent_circle_radius_m, metrics.part_count,
                metrics.vertex_count, area.crs.authid()])
            sink.addFeature(feature, QgsFeatureSink.Flag.FastInsert)
        feedback.pushInfo(f"Superficie: {measure.format_area(metrics.area_m2)}")
        feedback.pushInfo(f"Perimetro: {measure.format_length(metrics.perimeter_m)}")
        feedback.pushInfo(f"Compattezza: {metrics.compactness:.3f} | "
                          f"Elongazione: {metrics.elongation:.3f}")
        return {self.OUTPUT: sink_id, "STATISTICS": json.dumps(metrics.as_dict())}


class GenerateBuffersAlgorithm(TerritorialAlgorithm):
    """Setback bands around the project area."""

    DISTANCES = "DISTANCES"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "generate_buffers"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Genera fasce di rispetto"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Crea una o piu' fasce attorno all'area di progetto. Le distanze sono in "
                "metri e vengono applicate in un CRS metrico, anche quando l'area e' in "
                "coordinate geografiche.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        from qgis.core import QgsProcessingParameterString

        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterString(
            self.DISTANCES, "Distanze in metri (separate da virgola)",
            defaultValue="10, 30, 150"))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Fasce di rispetto"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        raw = self.parameterAsString(parameters, self.DISTANCES, context)
        distances = []
        for token in raw.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                distances.append(float(token))
            except ValueError:
                raise QgsProcessingException(f"Distanza non valida: {token}")
        if not distances:
            raise QgsProcessingException("Nessuna distanza indicata.")

        fields = qt_compat.fields_from([
            ("distance_m", qt_compat.DOUBLE), ("area_m2", qt_compat.DOUBLE),
            ("area_ha", qt_compat.DOUBLE)])
        from qgis.core import QgsWkbTypes

        from ...engines.proximity import ProximityEngine

        sink, sink_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                             QgsWkbTypes.Type.MultiPolygon, area.crs)
        for band in ProximityEngine(area).bands(distances):
            if feedback.isCanceled():
                break
            feature = QgsFeature(fields)
            feature.setGeometry(band.geometry)
            feature.setAttributes([band.distance_m, band.area_m2, band.area_m2 / 10_000.0])
            sink.addFeature(feature, QgsFeatureSink.Flag.FastInsert)
            feedback.pushInfo(f"{band.label}: {measure.format_area(band.area_m2)}")
        return {self.OUTPUT: sink_id}


class AnalyzeAreaAlgorithm(TerritorialAlgorithm):
    """The one-click territorial analysis, as a Processing algorithm."""

    CADASTRE = "CADASTRE"
    CONSTRAINTS = "CONSTRAINTS"
    TERRAIN = "TERRAIN"
    DOWNLOAD = "DOWNLOAD"
    REFRESH = "REFRESH"
    OUTPUT_JSON = "OUTPUT_JSON"

    def name(self) -> str:
        return "analyze_area"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Analizza area (one-click)"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Esegue l'analisi territoriale completa: unita' amministrative, catasto, "
                "vincoli e sensibilita', terreno e segnalazioni. Produce il file "
                "analysis.json con tutti i risultati e la provenienza dei dati.\n\n"
                "I risultati hanno natura ricognitiva e non costituiscono accertamento "
                "dei vincoli.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterBoolean(self.CADASTRE, "Catasto", True))
        self.addParameter(QgsProcessingParameterBoolean(self.CONSTRAINTS,
                                                        "Vincoli e sensibilita'", True))
        self.addParameter(QgsProcessingParameterBoolean(self.TERRAIN, "Terreno", True))
        self.addParameter(QgsProcessingParameterBoolean(self.DOWNLOAD,
                                                        "Scarica anche i dati vettoriali",
                                                        False))
        self.addParameter(QgsProcessingParameterBoolean(self.REFRESH, "Ignora la cache",
                                                        False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_JSON, "File dei risultati (analysis.json)",
            fileFilter="JSON (*.json)", optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        options = AnalysisOptions(
            include_cadastre=self.parameterAsBool(parameters, self.CADASTRE, context),
            include_constraints=self.parameterAsBool(parameters, self.CONSTRAINTS, context),
            include_terrain=self.parameterAsBool(parameters, self.TERRAIN, context),
            include_download=self.parameterAsBool(parameters, self.DOWNLOAD, context),
            refresh=self.parameterAsBool(parameters, self.REFRESH, context),
        )
        report = AnalysisOrchestrator().run(area, options,
                                            feedback=self.feedback_adapter(feedback))
        feedback.pushInfo(AnalysisOrchestrator.summary_text(report))
        path = self.parameterAsFileOutput(parameters, self.OUTPUT_JSON, context)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(report.to_json(), encoding="utf-8")
        return {self.OUTPUT_JSON: path,
                "ALERTS": len(report.alerts),
                "SOURCES_FAILED": len(report.sources_failed),
                "PARCELS": len(report.cadastre)}
