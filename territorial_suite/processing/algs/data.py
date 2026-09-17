"""Data algorithms: constraint analysis and area download."""

from __future__ import annotations

import json
from pathlib import Path

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsWkbTypes,
)

from ...core import qt_compat
from ...core.taxonomy import Taxonomy
from ...engines.admin import AdminResolver
from ...engines.constraints import ConstraintEngine
from ...engines.download import DownloadManager
from .base import TerritorialAlgorithm


class AnalyzeConstraintsAlgorithm(TerritorialAlgorithm):
    """Constraint and sensitivity analysis of the project area."""

    CATEGORIES = "CATEGORIES"
    REFRESH = "REFRESH"
    OUTPUT = "OUTPUT"
    OUTPUT_JSON = "OUTPUT_JSON"

    def __init__(self) -> None:
        super().__init__()
        self._categories = [category for category in Taxonomy.instance().all()]

    def name(self) -> str:
        return "analyze_constraints"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Analizza vincoli e sensibilita'"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Interroga le sorgenti configurate per le categorie selezionate e misura "
                "presenza, superficie interessata, percentuale dell'area e distanza minima "
                "per ciascuna.\n\nOgni risultato riporta la natura del dato "
                "(cartografico / dichiarativo / riferito ad atto): il plugin non "
                "trasforma un dato cartografico in un accertamento giuridico.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        labels = [category.label("it") for category in self._categories]
        knowledge = [index for index, category in enumerate(self._categories)
                     if category.kind == "knowledge"]
        self.addParameter(QgsProcessingParameterEnum(
            self.CATEGORIES, "Categorie", labels, allowMultiple=True,
            defaultValue=knowledge))
        self.addParameter(QgsProcessingParameterBoolean(self.REFRESH, "Ignora la cache",
                                                        False))
        # A table without geometry: the sink *type* is a Processing source type, not a
        # geometry type (the WKB type is passed to parameterAsSink instead).
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Sintesi per sorgente",
            type=QgsProcessing.SourceType.TypeVector))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_JSON, "Risultati (JSON)", fileFilter="JSON (*.json)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        indexes = self.parameterAsEnums(parameters, self.CATEGORIES, context)
        categories = [self._categories[index].id for index in indexes
                      if 0 <= index < len(self._categories)]
        refresh = self.parameterAsBool(parameters, self.REFRESH, context)

        adapter = self.feedback_adapter(feedback)
        admin = AdminResolver().resolve(area, feedback=adapter)
        area.admin = admin
        outcome = ConstraintEngine().run(area, categories=categories or None,
                                         feedback=adapter, refresh=refresh)
        for warning in outcome.warnings:
            feedback.pushWarning(warning)

        fields = qt_compat.fields_from([
            ("source", qt_compat.STRING, "Fonte"),
            ("source_id", qt_compat.STRING, "Id"),
            ("category", qt_compat.STRING, "Categoria"),
            ("status", qt_compat.STRING, "Stato"),
            ("present", qt_compat.BOOL, "Presente"),
            ("features", qt_compat.INT, "Elementi"),
            ("area_int_m2", qt_compat.DOUBLE, "Superficie interessata (m2)"),
            ("int_pct", qt_compat.DOUBLE, "% area"),
            ("distance_m", qt_compat.DOUBLE, "Distanza minima (m)"),
            ("evidence", qt_compat.STRING, "Natura del dato"),
            ("authority", qt_compat.STRING, "Ente"),
            ("error", qt_compat.STRING, "Errore"),
        ])
        sink, sink_id = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                             QgsWkbTypes.Type.NoGeometry)
        for result in outcome.results:
            feature = QgsFeature(fields)
            feature.setAttributes([
                result.source_name, result.source_id, result.category,
                result.status.value, result.present, result.feature_count,
                round(result.intersect_area_m2, 2), round(result.intersect_pct, 2),
                round(result.min_distance_m, 2) if result.min_distance_m is not None else None,
                result.evidence_level.label_it,
                result.provenance.authority if result.provenance else "",
                result.error])
            sink.addFeature(feature, QgsFeatureSink.Flag.FastInsert)

        path = self.parameterAsFileOutput(parameters, self.OUTPUT_JSON, context)
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(
                json.dumps([result.as_dict() for result in outcome.results],
                           ensure_ascii=False, indent=2), encoding="utf-8")
        present = sum(1 for result in outcome.results if result.present)
        feedback.pushInfo(f"{present} sorgenti con dati nell'area su "
                          f"{len(outcome.results)} interrogate.")
        return {self.OUTPUT: sink_id, self.OUTPUT_JSON: path, "PRESENT": present}


class DownloadAreaAlgorithm(TerritorialAlgorithm):
    """Download the vector datasets available for the area."""

    CATEGORIES = "CATEGORIES"
    CLIP = "CLIP"
    REFRESH = "REFRESH"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"

    def __init__(self) -> None:
        super().__init__()
        self._categories = [category for category in Taxonomy.instance().all()]

    def name(self) -> str:
        return "download_area_dataset"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Scarica i dati dell'area"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Scarica i dataset disponibili per l'area (viabilita', idrografia, "
                "sentieri, edifici, localita', infrastrutture...), li ritaglia sul "
                "perimetro e li salva in un GeoPackage organizzato per categoria.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        labels = [category.label("it") for category in self._categories]
        default = [index for index, category in enumerate(self._categories)
                   if category.id in ("roads", "hydrography", "trails", "buildings",
                                      "places")]
        self.addParameter(QgsProcessingParameterEnum(
            self.CATEGORIES, "Categorie", labels, allowMultiple=True, defaultValue=default))
        self.addParameter(QgsProcessingParameterBoolean(self.CLIP,
                                                        "Ritaglia sull'area", True))
        self.addParameter(QgsProcessingParameterBoolean(self.REFRESH, "Ignora la cache",
                                                        False))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, "Cartella di destinazione"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        indexes = self.parameterAsEnums(parameters, self.CATEGORIES, context)
        categories = [self._categories[index].id for index in indexes
                      if 0 <= index < len(self._categories)]
        folder = Path(self.parameterAsString(parameters, self.OUTPUT_FOLDER, context))
        folder.mkdir(parents=True, exist_ok=True)

        adapter = self.feedback_adapter(feedback)
        area.admin = AdminResolver().resolve(area, feedback=adapter)
        outcome = DownloadManager().download(
            area, categories or None, out_path=folder / "data.gpkg",
            clip=self.parameterAsBool(parameters, self.CLIP, context),
            refresh=self.parameterAsBool(parameters, self.REFRESH, context),
            feedback=adapter)
        for warning in outcome.warnings:
            feedback.pushWarning(warning)
        feedback.pushInfo(f"{outcome.feature_total} elementi in {len(outcome.layers)} layer "
                          f"-> {outcome.gpkg_path}")
        return {self.OUTPUT_FOLDER: str(folder), "LAYERS": len(outcome.layers),
                "FEATURES": outcome.feature_total, "GPKG": outcome.gpkg_path}
