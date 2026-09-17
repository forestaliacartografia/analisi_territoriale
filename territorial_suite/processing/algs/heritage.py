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
    QgsProcessingParameterNumber,
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


class HydrogeologicalConstraintAlgorithm(TerritorialAlgorithm):
    """Look for the vincolo idrogeologico of R.D.L. 3267/1923 over the area."""

    GRID = "GRID"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "analyze_hydrogeological_constraint"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Analizza vincolo idrogeologico (R.D.L. 3267/1923)"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Cerca il vincolo idrogeologico sull'area di progetto usando le fonti "
                "cartografiche ufficiali configurate.\n\n"
                "Quando la fonte e' scaricabile calcola la superficie interessata e la "
                "percentuale. Quando la fonte pubblica il perimetro come sola immagine "
                "interroga punti distribuiti sull'area e riporta quanti ricadono nella "
                "perimetrazione: in quel caso la superficie NON e' calcolabile e non "
                "viene stimata.\n\n"
                "Se nessuna fonte copre l'area l'esito e' NON_VERIFICABILE: il vincolo "
                "e' di competenza regionale e l'assenza di dato non equivale "
                "all'assenza del vincolo. L'esito e' un'osservazione cartografica, non "
                "un accertamento: la verifica compete all'ente competente.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        self.addParameter(QgsProcessingParameterNumber(
            self.GRID, "Densita' del campionamento (solo fonti non scaricabili)",
            QgsProcessingParameterNumber.Type.Integer, defaultValue=3,
            minValue=1, maxValue=8))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Esito (JSON)", fileFilter="JSON (*.json)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        from ...engines.hydrogeological_constraint import HydrogeologicalConstraintEngine

        area = self.project_area(parameters, context, feedback)
        grid = self.parameterAsInt(parameters, self.GRID, context)
        outcome = HydrogeologicalConstraintEngine().run(
            area, feedback=self.feedback_adapter(feedback), grid=grid)

        feedback.pushInfo(outcome.statement())
        if outcome.source_name:
            feedback.pushInfo(f"Fonte: {outcome.source_name} ({outcome.capability})")
        for act in outcome.act_references:
            feedback.pushInfo(f"Atto riportato dal dato: {act}")
        for warning in outcome.warnings:
            feedback.pushWarning(warning)
        for gap in outcome.gaps:
            feedback.pushInfo(f"Lacuna dichiarata: {gap}")

        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if out_path:
            Path(out_path).write_text(
                json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        return {self.OUTPUT: out_path or "", "ESITO": outcome.presence.value,
                "MISURABILE": outcome.measurable}


class HazardRiskAlgorithm(TerritorialAlgorithm):
    """Measure hazard and risk themes over the area, without conflating them."""

    THEMES = "THEMES"
    OUTPUT = "OUTPUT"

    def __init__(self) -> None:
        super().__init__()
        from ...engines.hazard_risk import rules as hazard_rules

        self._themes = list((hazard_rules().get("themes") or {}).items())

    def name(self) -> str:
        return "analyze_hazard_risk"

    def displayName(self) -> str:  # noqa: N802 - QGIS API
        return "Analizza pericolosita e rischio"

    def shortHelpString(self) -> str:  # noqa: N802 - QGIS API
        return ("Misura i temi di pericolosita e rischio sull'area di progetto tenendoli "
                "distinti: inventario, suscettibilita, pericolosita e rischio sono "
                "affermazioni diverse e non vengono mai convertite l'una nell'altra.\n\n"
                "Le superfici sono calcolate sulla parte di poligono realmente interna "
                "all'area. Il codice ufficiale della classe e' sempre conservato accanto "
                "al livello normalizzato.\n\n"
                "Se un tema non ha una fonte che possa rispondere, il risultato dichiara "
                "che non e' determinabile: non viene mai ricavato dal tema vicino. Un "
                "rischio dedotto da una pericolosita e' un numero che nessuno ha "
                "calcolato, presentato come se qualcuno l'avesse fatto.")

    def initAlgorithm(self, config=None) -> None:  # noqa: N802 - QGIS API
        self.add_area_parameter()
        labels = [f"{name} ({conf.get('kind', '')})" for name, conf in self._themes]
        self.addParameter(QgsProcessingParameterEnum(
            self.THEMES, "Temi da misurare", labels or ["(nessun tema configurato)"],
            allowMultiple=True,
            defaultValue=list(range(len(self._themes))) or [0]))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, "Esito (JSON)", fileFilter="JSON (*.json)",
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 - QGIS API
        from ...engines.hazard_risk import HazardRiskEngine

        if not self._themes:
            raise QgsProcessingException("Nessun tema di pericolosita configurato.")
        area = self.project_area(parameters, context, feedback)
        chosen = self.parameterAsEnums(parameters, self.THEMES, context)
        names = [self._themes[i][0] for i in chosen] or [n for n, _ in self._themes]

        outcome = HazardRiskEngine().run(area, themes=names,
                                         feedback=self.feedback_adapter(feedback))
        for theme in outcome.themes:
            feedback.pushInfo(theme.statement())
            for row in theme.classes:
                origin = "" if row.derived_from == "attribute" else "  [classe dallo strato]"
                feedback.pushInfo(
                    f"    {row.official_label[:46]:46s} {row.area_m2 / 10_000:9.2f} ha "
                    f"{row.percentage:5.1f}%  [{row.normalised}]{origin}")
            for limitation in theme.limitations:
                feedback.pushWarning(f"    {limitation}")
            for gap in theme.gaps:
                feedback.pushInfo(f"    lacuna: {gap}")

        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if out_path:
            Path(out_path).write_text(
                json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8")
        determinable = sum(1 for t in outcome.themes if t.determinable)
        return {self.OUTPUT: out_path or "", "TEMI": len(outcome.themes),
                "DETERMINABILI": determinable}
