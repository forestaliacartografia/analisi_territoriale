"""Cadastral algorithm: parcels intersecting the project area."""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsGeometry,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
    QgsWkbTypes,
)

from ...core import measure, qt_compat
from ...engines.cadastre_engine import CadastreEngine
from ...engines.report import ReportEngine
from ...core.models import AnalysisReport
from .base import TerritorialAlgorithm


class QueryCadastreAlgorithm(TerritorialAlgorithm):
    """Query the national cadastral service for the parcels of an area."""

    REFRESH = "REFRESH"
    OUTPUT = "OUTPUT"
    OUTPUT_TABLE = "OUTPUT_TABLE"

    def name(self) -> str:
        return "query_cadastre"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Interroga il catasto"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Scarica le particelle catastali che intersecano l'area e ne calcola la "
                "superficie interessata, suddivise per Comune e foglio.\n\n"
                "Le superfici sono GRAFICHE, calcolate dalla geometria della mappa "
                "catastale: non sono le superfici censite in banca dati. Il servizio non "
                "copre le Province autonome di Trento e Bolzano.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterBoolean(self.REFRESH, "Ignora la cache",
                                                        False))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT,
                                                            "Particelle interessate"))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_TABLE, "Tabella catastale (CSV)", fileFilter="CSV (*.csv)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        refresh = self.parameterAsBool(parameters, self.REFRESH, context)
        result = CadastreEngine().run(area, feedback=self.feedback_adapter(feedback),
                                      refresh=refresh)
        for warning in result.warnings:
            feedback.pushWarning(warning)

        fields = qt_compat.fields_from([
            ("municipality", qt_compat.STRING, "Comune"),
            ("istat", qt_compat.STRING, "ISTAT"),
            ("cad_code", qt_compat.STRING, "Codice catastale"),
            ("sheet", qt_compat.STRING, "Foglio"),
            ("parcel", qt_compat.STRING, "Particella"),
            ("national_ref", qt_compat.STRING, "Identificativo"),
            ("area_graph_m2", qt_compat.DOUBLE, "Superficie grafica (m2)"),
            ("area_int_m2", qt_compat.DOUBLE, "Superficie interessata (m2)"),
            ("int_pct", qt_compat.DOUBLE, "% particella"),
        ])
        sink, sink_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                             QgsWkbTypes.Type.MultiPolygon, area.work_crs)
        for row in result.rows:
            if feedback.isCanceled():
                break
            feature = QgsFeature(fields)
            if row.geometry_wkt:
                feature.setGeometry(QgsGeometry.fromWkt(row.geometry_wkt))
            feature.setAttributes([row.municipality, row.istat_code, row.cadastral_code,
                                   row.sheet, row.parcel, row.national_ref,
                                   round(row.area_cadastral_m2, 2),
                                   round(row.area_intersect_m2, 2),
                                   round(row.intersect_pct, 2)])
            sink.addFeature(feature, QgsFeatureSink.Flag.FastInsert)

        table_path = self.parameterAsFileOutput(parameters, self.OUTPUT_TABLE, context)
        if table_path:
            report = AnalysisReport(area=area.as_dict(), cadastre=result.rows)
            engine = ReportEngine(report)
            engine.write_csv(Path(table_path), engine.cadastral_rows())

        totals = result.totals()
        feedback.pushInfo(
            f"{int(totals['parcels'])} particelle in {int(totals['municipalities'])} "
            f"Comune/i e {int(totals['sheets'])} fogli - superficie interessata "
            f"{measure.format_area(totals['intersect_area_m2'])}")
        return {self.OUTPUT: sink_id, self.OUTPUT_TABLE: table_path,
                "PARCELS": len(result.rows)}
