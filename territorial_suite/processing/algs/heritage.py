"""Processing algorithms for the Cultural Heritage module and the orthophoto sheet."""

from __future__ import annotations

import json
from pathlib import Path

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
    QgsProject,
)

from ...core.errors import LayoutError
from ...core.models import AnalysisReport
from ...engines.cartography import export as export_module
from ...engines.cartography.imagery import AUTOMATIC, ImageryEngine
from ...engines.cartography.layout import MapSpec
from ...engines.cartography.orthophoto import OrthophotoEngine
from ...engines.cartography.profiles import ProfileStore
from ...engines.cultural_heritage.engine import MODULE_KEY, CulturalHeritageEngine
from ...engines.cultural_heritage.report import EXPORT_HEADER, rows_for_export
from .base import NO_THREADING, TerritorialAlgorithm


class CulturalHeritageAlgorithm(TerritorialAlgorithm):
    """Query, normalise and classify the cultural and landscape heritage of an area."""

    DEDUPLICATE = "DEDUPLICATE"
    REFRESH = "REFRESH"
    OUTPUT_CSV = "OUTPUT_CSV"
    OUTPUT_JSON = "OUTPUT_JSON"

    def name(self) -> str:
        return "cultural_heritage"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Patrimonio culturale e paesaggistico"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return (
            "Interroga le fonti del Ministero della Cultura configurate nel catalogo "
            "(SITAP), normalizza i record, li classifica per tema, individua la "
            "Soprintendenza competente dal layer ufficiale e valuta la qualita' del "
            "dato.\n\n"
            "Il risultato descrive **intersezioni con dati cartografici**: la presenza "
            "di una geometria non equivale all'accertamento di un vincolo. Dove la "
            "fonte riporta gli estremi di un atto, questi sono trascritti come "
            "dichiarati dalla fonte e restano da verificare presso l'ente competente.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterBoolean(
            self.DEDUPLICATE, "Unisci i record che descrivono lo stesso bene", True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REFRESH, "Ignora la cache e riscarica", False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_CSV, "Tabella dei beni (CSV)", fileFilter="CSV (*.csv)",
            optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_JSON, "Esito completo (JSON)", fileFilter="JSON (*.json)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        from ...core import settings

        area = self.project_area(parameters, context, feedback)
        settings.set_value("cultural_heritage.deduplicate",
                           self.parameterAsBool(parameters, self.DEDUPLICATE, context))
        engine = CulturalHeritageEngine()
        outcome = engine.run(
            area, feedback=self.feedback_adapter(feedback),
            refresh=self.parameterAsBool(parameters, self.REFRESH, context))

        for theme in outcome.themes:
            feedback.pushInfo(
                f"{theme.label}: {len(theme.assets)} record "
                f"({len(theme.intersecting)} intersecano l'area)"
                + (f" - {', '.join(theme.gaps)}" if theme.gaps else ""))
        for office in outcome.superintendencies:
            feedback.pushInfo(f"Soprintendenza competente: {office.name}")
        for warning in outcome.warnings:
            feedback.pushWarning(warning)

        results = {"ASSETS": len(outcome.assets),
                   "INTERSECTING": len(outcome.intersecting),
                   "WITH_ACT": len(outcome.with_act)}

        csv_path = self.parameterAsFileOutput(parameters, self.OUTPUT_CSV, context)
        if csv_path:
            self._write_csv(Path(csv_path), outcome)
            results[self.OUTPUT_CSV] = csv_path
        json_path = self.parameterAsFileOutput(parameters, self.OUTPUT_JSON, context)
        if json_path:
            Path(json_path).write_text(
                json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
            results[self.OUTPUT_JSON] = json_path
        return results

    @staticmethod
    def _write_csv(path: Path, outcome) -> None:
        import csv

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(EXPORT_HEADER)
            writer.writerows(rows_for_export(outcome))


class OrthophotoSheetAlgorithm(TerritorialAlgorithm):
    """Build the orthophoto sheet for an area, with the configured imagery provider."""

    PROVIDER = "PROVIDER"
    PROFILE = "PROFILE"
    TITLE = "TITLE"
    ANALYSIS = "ANALYSIS"
    OUTPUT = "OUTPUT"

    def __init__(self) -> None:
        super().__init__()
        self._providers = [AUTOMATIC, "esri", "google"]
        self._profiles = [profile.id for profile in ProfileStore.instance().all()]

    def flags(self):
        """Layout creation must run in the main thread."""
        flags = super().flags()
        return flags | NO_THREADING if NO_THREADING is not None else flags

    def name(self) -> str:
        return "orthophoto_sheet"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Tavola ortofoto"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return (
            "Crea la tavola su base ortofotografica per l'area di progetto.\n\n"
            "La sorgente e' scelta fra quelle configurate nel catalogo. Google viene "
            "usato esclusivamente tramite la Map Tiles API ufficiale con la chiave "
            "dell'utente: senza chiave la tavola usa un'altra ortofoto e lo dichiara. "
            "L'attribuzione richiesta dal fornitore e' sempre stampata sulla tavola.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterEnum(
            self.PROVIDER, "Ortofoto", ["Automatica", "Esri World Imagery",
                                        "Google (Map Tiles API)"], defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.PROFILE, "Profilo di layout", self._profiles or ["standard"],
            defaultValue=max(self._profiles.index("ortofoto"), 0)
            if "ortofoto" in self._profiles else 0))
        self.addParameter(QgsProcessingParameterString(
            self.TITLE, "Titolo", defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterFile(
            self.ANALYSIS, "File analysis.json (opzionale)", optional=True,
            extension="json"))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Esporta in PDF (opzionale)", fileFilter="PDF (*.pdf)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        provider = self._providers[self.parameterAsEnum(parameters, self.PROVIDER,
                                                        context)]
        profiles = self._profiles or ["standard"]
        profile_id = profiles[min(self.parameterAsEnum(parameters, self.PROFILE, context),
                                  len(profiles) - 1)]
        analysis_path = self.parameterAsFile(parameters, self.ANALYSIS, context)
        report = None
        if analysis_path:
            try:
                report = AnalysisReport.from_json(
                    Path(analysis_path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise QgsProcessingException(
                    f"File di analisi non leggibile: {exc}") from exc

        spec = MapSpec(title=self.parameterAsString(parameters, self.TITLE, context))
        # The algorithm declares NoThreading, so this already runs in the main thread and
        # may create QGIS objects; it goes through the same engine as the dock.
        try:
            sheet = OrthophotoEngine(QgsProject.instance()).run(
                area, preference=provider, profile_id=profile_id, spec=spec,
                report=report, feedback=self.feedback_adapter(feedback))
        except LayoutError as exc:
            raise QgsProcessingException(str(exc)) from exc
        for warning in sheet.warnings:
            feedback.pushWarning(warning)
        feedback.pushInfo(f"Ortofoto utilizzata: {sheet.choice.name}")
        feedback.pushInfo(f"Attribuzione: {sheet.credit_line}")

        results = {"LAYOUT": sheet.layout.name(),
                   "PROVIDER": sheet.choice.provider,
                   "ATTRIBUTION": sheet.credit_line,
                   "FALLBACK": sheet.choice.fallback_used}
        path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if path:
            result = export_module.ExportCenter.export(sheet.layout, Path(path), "pdf")
            if not result.ok:
                feedback.pushWarning(f"Export non riuscito: {result.message}")
            results[self.OUTPUT] = path
        return results


class ImageryProvidersAlgorithm(TerritorialAlgorithm):
    """List the imagery providers and say why an unavailable one cannot be used."""

    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "imagery_providers"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Ortofoto disponibili"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Elenca le sorgenti di ortofoto del catalogo, il loro stato e, per "
                "quelle non utilizzabili, il motivo.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Elenco (JSON)", fileFilter="JSON (*.json)", optional=True,
            createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        engine = ImageryEngine()
        rows = []
        for source in engine.registry.all(enabled_only=False):
            if source.category != "imagery":
                continue
            key = engine.provider_key(source)
            rows.append({
                "id": source.id, "provider": key, "name": source.name,
                "enabled": source.enabled,
                "operational": source.is_operational,
                "status": source.verification_status.value,
                "attribution": source.attribution,
                "reason": engine.why_unavailable(key),
            })
            feedback.pushInfo(
                f"{source.id}: {'disponibile' if not rows[-1]['reason'] else rows[-1]['reason']}")
        path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if path:
            Path(path).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return {self.OUTPUT: path, "COUNT": len(rows)}


#: Module key of the heritage payload, re-exported for the algorithms' callers.
HERITAGE_MODULE = MODULE_KEY
