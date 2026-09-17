"""Cartography algorithms: quick map, map series and report/package generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterString,
    QgsProject,
)

from ...core.models import AnalysisReport
from ...engines.cartography import export as export_module
from ...engines.cartography.layout import LayoutBuilder, MapSpec, template_list
from ...engines.cartography.series import MapSeriesEngine
from ...engines.package import PackageBuilder
from ...engines.project_layers import LayerApplier
from ...engines.report import ReportEngine, ReportOptions
from .base import NO_THREADING, TerritorialAlgorithm

REPORT_PARAM = "ANALYSIS"


def _load_report(path: str) -> AnalysisReport:
    """Read an ``analysis.json`` produced by the analysis algorithm."""
    try:
        return AnalysisReport.from_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QgsProcessingException(f"File di analisi non leggibile: {exc}") from exc


class GenerateQuickMapAlgorithm(TerritorialAlgorithm):
    """Build one print layout for the area."""

    TEMPLATE = "TEMPLATE"
    PAGE = "PAGE"
    ORIENTATION = "ORIENTATION"
    TITLE = "TITLE"
    LOAD_LAYERS = "LOAD_LAYERS"
    OUTPUT = "OUTPUT"

    PAGES = ["A4", "A3", "A2", "A1", "A0"]
    ORIENTATIONS = ["landscape", "portrait"]

    def __init__(self) -> None:
        super().__init__()
        self._templates = template_list()

    def flags(self):
        """Layout creation must run in the main thread."""
        flags = super().flags()
        return flags | NO_THREADING if NO_THREADING is not None else flags

    def name(self) -> str:
        return "generate_quick_map"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Genera mappa rapida"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Crea un layout di stampa completo (mappa, griglia, legenda, nord, scala, "
                "inquadramento amministrativo, fonti e data) per l'area di progetto e lo "
                "aggiunge al progetto corrente.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        labels = [template.get("name", template.get("id", "")) for template in self._templates]
        self.addParameter(QgsProcessingParameterEnum(self.TEMPLATE, "Tipo di tavola",
                                                     labels or ["(nessun template)"],
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.PAGE, "Formato", self.PAGES,
                                                     defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum(self.ORIENTATION, "Orientamento",
                                                     self.ORIENTATIONS, defaultValue=0))
        self.addParameter(QgsProcessingParameterString(self.TITLE, "Titolo",
                                                       defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterFile(
            REPORT_PARAM, "File analysis.json (opzionale)", optional=True,
            extension="json"))
        self.addParameter(QgsProcessingParameterBoolean(
            self.LOAD_LAYERS, "Carica nel progetto i layer dell'analisi", True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Esporta in PDF (opzionale)", fileFilter="PDF (*.pdf)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        if not self._templates:
            raise QgsProcessingException("Nessun template cartografico configurato.")
        index = self.parameterAsEnum(parameters, self.TEMPLATE, context)
        template_id = self._templates[min(index, len(self._templates) - 1)].get("id")
        analysis_path = self.parameterAsFile(parameters, REPORT_PARAM, context)
        report = _load_report(analysis_path) if analysis_path else None

        project = QgsProject.instance()
        if report is not None and self.parameterAsBool(parameters, self.LOAD_LAYERS, context):
            LayerApplier(project).apply_report(area, report)

        spec = MapSpec(template=str(template_id),
                       title=self.parameterAsString(parameters, self.TITLE, context),
                       page_size=self.PAGES[self.parameterAsEnum(parameters, self.PAGE,
                                                                 context)],
                       orientation=self.ORIENTATIONS[self.parameterAsEnum(
                           parameters, self.ORIENTATION, context)])
        layout = LayoutBuilder(project).build(area, spec, report=report)
        feedback.pushInfo(f"Layout creato: {layout.name()} (scala 1:{int(spec.scale or 0)})")

        path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if path:
            result = export_module.ExportCenter.export(layout, Path(path), "pdf")
            if not result.ok:
                feedback.pushWarning(f"Export non riuscito: {result.message}")
        return {self.OUTPUT: path, "LAYOUT": layout.name(), "SCALE": spec.scale}


class GenerateMapSeriesAlgorithm(TerritorialAlgorithm):
    """Build the whole map series for the area."""

    SERIES = "SERIES"
    EXPORT = "EXPORT"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"

    def __init__(self) -> None:
        super().__init__()
        self._series = list(MapSeriesEngine().available_series().keys()) or ["default"]

    def flags(self):
        """Layout creation must run in the main thread."""
        flags = super().flags()
        return flags | NO_THREADING if NO_THREADING is not None else flags

    def name(self) -> str:
        return "generate_map_series"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Genera serie di tavole"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Crea un layout indipendente per ogni tavola della serie configurata "
                "(inquadramento, catasto, vincoli, ambiente, idrografia, infrastrutture, "
                "rischio, ortofoto, altimetria, pendenza) e le esporta numerate.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterEnum(self.SERIES, "Serie", self._series,
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterFile(
            REPORT_PARAM, "File analysis.json (opzionale)", optional=True, extension="json"))
        self.addParameter(QgsProcessingParameterBoolean(self.EXPORT, "Esporta in PDF", True))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, "Cartella di esportazione", optional=True,
            createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        series_name = self._series[self.parameterAsEnum(parameters, self.SERIES, context)]
        analysis_path = self.parameterAsFile(parameters, REPORT_PARAM, context)
        report = _load_report(analysis_path) if analysis_path else None
        project = QgsProject.instance()
        if report is not None:
            LayerApplier(project).apply_report(area, report)
        engine = MapSeriesEngine(project)
        result = engine.generate(area, series=series_name, report=report,
                                 feedback=self.feedback_adapter(feedback))
        for warning in result.warnings:
            feedback.pushWarning(warning)
        feedback.pushInfo(f"{result.count} tavole generate.")
        folder = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        exported: List[str] = []
        if folder and self.parameterAsBool(parameters, self.EXPORT, context):
            for item in engine.export(result, Path(folder), "pdf"):
                if item.ok:
                    exported.append(str(item.path))
                else:
                    feedback.pushWarning(f"{Path(item.path).name}: {item.message}")
        return {self.OUTPUT_FOLDER: folder, "LAYOUTS": result.count,
                "EXPORTED": len(exported)}


class GenerateReportAlgorithm(TerritorialAlgorithm):
    """Produce the territorial dossier from an analysis file."""

    FORMAT = "FORMAT"
    OUTPUT = "OUTPUT"
    FORMATS = ["PDF", "HTML"]

    def name(self) -> str:
        return "generate_territorial_report"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Genera relazione territoriale"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Costruisce il quadro conoscitivo territoriale a partire dal file "
                "analysis.json prodotto dall'analisi: area, localizzazione, catasto, "
                "vincoli per categoria, terreno, fonti e metadati, con la natura di ogni "
                "dato e la relativa provenienza.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.addParameter(QgsProcessingParameterFile(
            REPORT_PARAM, "File analysis.json", extension="json"))
        self.addParameter(QgsProcessingParameterEnum(self.FORMAT, "Formato", self.FORMATS,
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Relazione", fileFilter="PDF (*.pdf);;HTML (*.html)"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        report = _load_report(self.parameterAsFile(parameters, REPORT_PARAM, context))
        engine = ReportEngine(report, ReportOptions.from_settings())
        path = Path(self.parameterAsFileOutput(parameters, self.OUTPUT, context))
        chosen = self.FORMATS[self.parameterAsEnum(parameters, self.FORMAT, context)]
        if chosen == "HTML":
            engine.write_html(path.with_suffix(".html"))
            path = path.with_suffix(".html")
        else:
            engine.write_pdf(path.with_suffix(".pdf"))
            path = path.with_suffix(".pdf")
            engine.write_xlsx(path.with_suffix(".xlsx"))
        feedback.pushInfo(f"Relazione scritta in {path}")
        return {self.OUTPUT: str(path)}


class ExportPackageAlgorithm(TerritorialAlgorithm):
    """Export the complete data package."""

    OUTPUT_FOLDER = "OUTPUT_FOLDER"

    def name(self) -> str:
        return "export_area_package"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Esporta il pacchetto dell'area"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Crea la cartella di consegna con dati vettoriali, catasto, vincoli, "
                "terreno, relazione, tabelle, tavole, progetto QGIS e manifest delle "
                "fonti.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterFile(
            REPORT_PARAM, "File analysis.json", extension="json"))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, "Cartella di destinazione"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        area = self.project_area(parameters, context, feedback)
        report = _load_report(self.parameterAsFile(parameters, REPORT_PARAM, context))
        folder = Path(self.parameterAsString(parameters, self.OUTPUT_FOLDER, context))
        result = PackageBuilder(area, report).build(
            folder, feedback=self.feedback_adapter(feedback))
        for warning in result.warnings:
            feedback.pushWarning(warning)
        feedback.pushInfo(f"Pacchetto creato in {result.root} "
                          f"({len(result.files)} file, "
                          f"{result.size_bytes / 1024 / 1024:.1f} MB)")
        return {self.OUTPUT_FOLDER: str(result.root), "FILES": len(result.files)}


class ValidateSheetAlgorithm(TerritorialAlgorithm):
    """Check every layout of the project against the contract of its template.

    Same engine as the GUI and the export: the rules live in one place, so a sheet that
    Processing accepts is a sheet the export accepts, and the reverse.
    """

    OUTPUT = "OUTPUT"
    STRICT = "STRICT"

    def flags(self):
        """Layouts belong to the main thread."""
        flags = super().flags()
        return flags | NO_THREADING if NO_THREADING is not None else flags

    def name(self) -> str:
        return "validate_sheet"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Valida tavola"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Verifica che ogni tavola del progetto rispetti il contratto del proprio "
                "template: il tema dichiarato dal titolo deve essere presente nel "
                "riquadro di mappa, non vuoto, dentro l'estensione stampata e in "
                "legenda. Segnala anche le legende dominate da temi che il titolo non "
                "dichiara. Le tavole senza tema dominante superano i controlli sul tema.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.addParameter(QgsProcessingParameterBoolean(
            self.STRICT, "Interrompi se una tavola contraddice il proprio titolo", False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Esito dei controlli (JSON)", fileFilter="JSON (*.json)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        from ...engines.cartography.sheet_spec import ERROR, WARNING

        project = QgsProject.instance()
        layouts = [lay for lay in project.layoutManager().layouts()
                   if hasattr(lay, "pageCollection")]
        if not layouts:
            raise QgsProcessingException("Il progetto non contiene alcuna tavola.")

        strict = self.parameterAsBool(parameters, self.STRICT, context)
        reports, blocking = [], 0
        for layout in layouts:
            qa = export_module.ExportCenter.validate(layout)
            if qa is None:
                feedback.pushInfo(f"{layout.name()}: nessun contratto dichiarato, "
                                  f"controlli sul tema non applicabili.")
                continue
            reports.append({"layout": layout.name(), **qa.as_dict()})
            feedback.pushInfo(f"{layout.name()}: {qa.level.upper()} - {qa.summary()}")
            for finding in qa.findings:
                if finding.level == ERROR:
                    feedback.reportError(f"    {finding.code}: {finding.message}")
                    blocking += 1
                elif finding.level == WARNING:
                    feedback.pushWarning(f"    {finding.code}: {finding.message}")

        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if out_path:
            Path(out_path).write_text(json.dumps(reports, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        if blocking and strict:
            raise QgsProcessingException(
                f"{blocking} controlli bloccanti: le tavole contraddicono il proprio "
                f"titolo e non vanno esportate.")
        return {self.OUTPUT: out_path or "", "ERRORI": blocking,
                "TAVOLE": len(reports)}
