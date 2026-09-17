"""Area data package: the "download everything" deliverable.

Produces a self-contained folder the user can hand over, archive or open next month:

    PROJECT_PACKAGE/
    |-- vector/         downloaded datasets (GeoPackage)
    |-- cadastral/      parcels and sheets + tables
    |-- constraints/    constraint datasets
    |-- terrain/        dem/slope/aspect/hillshade/contours
    |-- imagery/        SERVIZI.txt (background services are streamed, not copied)
    |-- reports/        dossier PDF/HTML, analysis.json, CSV/XLSX tables
    |-- maps/           exported layouts
    |-- project/        project.qgz pointing at the package files
    `-- MANIFEST.json   what is inside, where it came from, when
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from qgis.core import QgsProject

from ..core import log, provenance as provenance_module
from ..core.constants import ANALYSIS_FILE, PLUGIN_NAME, PLUGIN_VERSION
from ..core.errors import EngineError, UserCancelled
from ..core.feedback import Feedback, NullFeedback
from ..core.models import AnalysisReport, LayerRef
from ..core.paths import area_dir, safe_filename
from ..core.project_area import ProjectArea
from .project_layers import LayerApplier
from .report import ReportEngine, ReportOptions

FOLDERS = ("vector", "cadastral", "constraints", "terrain", "imagery", "reports", "maps",
           "project")

#: Which package folder receives the output of each analysis category.
CATEGORY_FOLDER = {
    "cadastral": "cadastral",
    "terrain": "terrain",
    "imagery": "imagery",
    "roads": "vector",
    "trails": "vector",
    "hydrography": "vector",
    "buildings": "vector",
    "places": "vector",
    "administrative": "vector",
}


@dataclass
class PackageResult:
    """What the builder produced."""

    root: Path
    files: List[Path] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    manifest: Dict[str, object] = field(default_factory=dict)

    @property
    def size_bytes(self) -> int:
        """Total size of the package."""
        return sum(path.stat().st_size for path in self.files if path.exists())


class PackageBuilder:
    """Assembles the deliverable folder for one project area."""

    def __init__(self, area: ProjectArea, report: AnalysisReport, *,
                 project: Optional[QgsProject] = None) -> None:
        self.area = area
        self.report = report
        self.project = project

    # ------------------------------------------------------------------ build

    def build(self, destination: Path, *, name: str = "", include_project: bool = True,
              include_report: bool = True, layouts: Optional[Sequence] = None,
              export_format: str = "pdf",
              feedback: Optional[Feedback] = None) -> PackageResult:
        """Create the package under ``destination``."""
        feedback = feedback or NullFeedback()
        folder_name = safe_filename(name or f"{self.area.name}_{self.area.id}")
        root = Path(destination) / folder_name
        root.mkdir(parents=True, exist_ok=True)
        for folder in FOLDERS:
            (root / folder).mkdir(exist_ok=True)
        result = PackageResult(root=root)

        feedback.set_step("Copia dei dati")
        self._copy_outputs(root, result, feedback)
        feedback.set_progress(40)

        feedback.set_step("Scrittura di analisi e tabelle")
        self._write_analysis(root, result)
        if include_report:
            self._write_report(root, result)
        feedback.set_progress(65)

        if layouts:
            feedback.set_step("Esportazione tavole")
            self._export_layouts(root, layouts, export_format, result)
        feedback.set_progress(80)

        if include_project:
            feedback.set_step("Creazione del progetto QGIS")
            self._write_project(root, result)
        feedback.set_progress(95)

        self._write_imagery_note(root, result)
        result.manifest = self._manifest(result)
        manifest_path = root / "MANIFEST.json"
        manifest_path.write_text(json.dumps(result.manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        result.files.append(manifest_path)
        feedback.set_progress(100)
        feedback.push_info(f"Pacchetto creato in {root}")
        return result

    # ------------------------------------------------------------------ steps

    def _copy_outputs(self, root: Path, result: PackageResult, feedback: Feedback) -> None:
        """Copy the GeoPackages and rasters produced for this area."""
        source_dir = Path(area_dir(self.area.id))
        if not source_dir.exists():
            result.warnings.append("Nessun output da copiare per quest'area")
            return
        mapping = {
            "cadastre.gpkg": "cadastral",
            "constraints.gpkg": "constraints",
            "data.gpkg": "vector",
            "dem.tif": "terrain",
            "slope.tif": "terrain",
            "aspect.tif": "terrain",
            "hillshade.tif": "terrain",
            "contours.gpkg": "terrain",
        }
        for path in sorted(source_dir.iterdir()):
            if feedback.is_canceled():
                raise UserCancelled()
            if not path.is_file():
                continue
            folder = mapping.get(path.name, "vector")
            target = root / folder / path.name
            try:
                shutil.copy2(path, target)
                result.files.append(target)
            except OSError as exc:  # pragma: no cover - disk issues
                result.warnings.append(f"{path.name}: copia non riuscita ({exc})")

    def _write_analysis(self, root: Path, result: PackageResult) -> None:
        """Write analysis.json and the CSV tables."""
        engine = ReportEngine(self.report)
        analysis_path = root / "reports" / ANALYSIS_FILE
        analysis_path.write_text(self.report.to_json(), encoding="utf-8")
        result.files.append(analysis_path)
        tables = (("catasto.csv", engine.cadastral_rows()),
                  ("fonti.csv", engine.sources_rows()),
                  ("segnalazioni.csv", engine.alerts_rows()))
        for filename, rows in tables:
            if len(rows) <= 1:
                continue
            result.files.append(engine.write_csv(root / "reports" / filename, rows))
        xlsx = engine.write_xlsx(root / "reports" / "tabelle.xlsx")
        if xlsx is not None:
            result.files.append(xlsx)

    def _write_report(self, root: Path, result: PackageResult) -> None:
        """Write the dossier in HTML and PDF."""
        engine = ReportEngine(self.report, ReportOptions.from_settings())
        html_path = engine.write_html(root / "reports" / "quadro_conoscitivo.html")
        result.files.append(html_path)
        try:
            pdf_path = engine.write_pdf(root / "reports" / "quadro_conoscitivo.pdf")
            result.files.append(pdf_path)
        except (EngineError, Exception) as exc:  # pragma: no cover - Qt printing issues
            log.exception("Cannot render the report PDF", exc)
            result.warnings.append(f"Report PDF non generato: {exc}")

    def _export_layouts(self, root: Path, layouts: Sequence, export_format: str,
                        result: PackageResult) -> None:
        """Export the given layouts into the maps folder."""
        from .cartography.export import ExportCenter

        exports = ExportCenter.export_series(layouts, root / "maps", export_format)
        for item in exports:
            if item.ok:
                result.files.append(Path(item.path))
            else:
                result.warnings.append(f"{Path(item.path).name}: {item.message}")

    def _write_project(self, root: Path, result: PackageResult) -> None:
        """Build a QGIS project pointing at the files inside the package.

        The standalone :class:`QgsProject` is emptied explicitly before it goes out of
        scope: letting Python garbage-collect a project that still owns layers crashes
        the QGIS core at an unpredictable later moment.
        """
        project = QgsProject()
        written = False
        try:
            project.setCrs(self.area.work_crs)
            project.setTitle(f"{self.area.name} - {PLUGIN_NAME}")
            relocated = AnalysisReport.from_dict(self.report.as_dict())
            relocated.layers = [ref for ref in
                                (self._relocate(item, root) for item in self.report.layers)
                                if ref is not None]
            try:
                LayerApplier(project).apply_report(self.area, relocated)
            except Exception as exc:  # pragma: no cover - defensive
                result.warnings.append(f"Progetto QGIS incompleto: {exc}")
            path = root / "project" / "project.qgz"
            written = bool(project.write(str(path)))
            if written:
                result.files.append(path)
            else:
                result.warnings.append("Progetto QGIS non salvato")
        finally:
            project.removeAllMapLayers()
            project.clear()

    @staticmethod
    def _relocate(ref: LayerRef, root: Path) -> Optional[LayerRef]:
        """Rewrite a layer URI so that it points inside the package."""
        if not ref.uri:
            return None
        uri = ref.uri
        source, _, suffix = uri.partition("|")
        filename = Path(source).name
        folder = CATEGORY_FOLDER.get(ref.category, "constraints")
        if filename.startswith("cadastre"):
            folder = "cadastral"
        elif filename.startswith("constraints"):
            folder = "constraints"
        elif filename.startswith("data"):
            folder = "vector"
        elif filename.endswith((".tif", ".tiff")) or filename.startswith("contours"):
            folder = "terrain"
        target = root / folder / filename
        if not target.exists():
            return None
        new_uri = f"{target}|{suffix}" if suffix else str(target)
        return LayerRef(name=ref.name, uri=new_uri, provider=ref.provider,
                        category=ref.category, group=ref.group, style=ref.style,
                        source_id=ref.source_id, is_raster=ref.is_raster)

    def _write_imagery_note(self, root: Path, result: PackageResult) -> None:
        """Explain why background services are referenced, not copied."""
        lines = [
            "SERVIZI DI SFONDO",
            "=================",
            "",
            "Le basemap e le ortofoto sono servizi a tile: il pacchetto ne riporta il",
            "riferimento, non una copia delle immagini, nel rispetto delle condizioni",
            "d'uso dei fornitori.",
            "",
        ]
        for result_item in self.report.all_results:
            provenance = result_item.provenance
            if provenance is None or result_item.category != "imagery":
                continue
            lines.extend([
                f"- {provenance.source_name}",
                f"  ente: {provenance.authority}",
                f"  url: {provenance.url}",
                f"  licenza: {provenance.license}",
                f"  attribuzione: {provenance.attribution}",
                "",
            ])
        path = root / "imagery" / "SERVIZI.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        result.files.append(path)

    def _manifest(self, result: PackageResult) -> Dict[str, object]:
        """Describe the package content and its provenance."""
        sources = []
        for item in self.report.all_results:
            if item.provenance is None:
                continue
            sources.append(provenance_module.as_report_entry(item.provenance))
        return {
            "generator": f"{PLUGIN_NAME} {PLUGIN_VERSION}",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "area": self.report.area,
            "files": [str(path.relative_to(result.root)) for path in result.files
                      if path.exists()],
            "sources": sources,
            "warnings": result.warnings + list(self.report.warnings),
            "disclaimer": ("Dati a fini conoscitivi: la verifica dei vincoli va effettuata "
                           "presso gli enti competenti."),
        }


def default_destination() -> Path:
    """Return the folder proposed by the GUI for a new package."""
    from ..core import settings

    last = settings.get("paths.last_output_dir", "")
    if last and Path(last).exists():
        return Path(last)
    return Path.home() / "Documents"
