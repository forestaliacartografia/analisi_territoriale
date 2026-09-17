"""Export centre: layouts to PDF/PNG/JPEG/SVG/GeoPDF and layers to DXF.

File names are generated automatically (``01_Inquadramento.pdf``, ``02_Catasto.pdf``, ...)
so that a whole map series lands in a folder already ordered and readable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from qgis.core import QgsLayoutExporter, QgsPrintLayout

from ...core import log, settings
from ...core.errors import EngineError
from ...core.paths import safe_filename

PDF = "pdf"
PNG = "png"
JPEG = "jpg"
SVG = "svg"
GEOPDF = "geopdf"
DXF = "dxf"

FORMATS = (PDF, PNG, JPEG, SVG, GEOPDF, DXF)
EXTENSIONS = {PDF: ".pdf", PNG: ".png", JPEG: ".jpg", SVG: ".svg", GEOPDF: ".pdf",
              DXF: ".dxf"}


@dataclass
class ExportResult:
    """Outcome of one export."""

    path: Path
    format: str
    ok: bool = True
    message: str = ""


def slugify(text: str, *, max_length: int = 60) -> str:
    """Turn a map title into a file-name friendly token."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")
    return safe_filename(cleaned or "mappa", max_length=max_length)


def file_name(title: str, fmt: str, *, index: Optional[int] = None) -> str:
    """Build the output file name of a map."""
    prefix = f"{index:02d}_" if index is not None else ""
    return f"{prefix}{slugify(title)}{EXTENSIONS.get(fmt, '.pdf')}"


class ExportCenter:
    """Exports layouts produced by the cartography engine."""

    @staticmethod
    def export(layout: QgsPrintLayout, out_path: Path, fmt: str = PDF, *,
               dpi: Optional[int] = None) -> ExportResult:
        """Export one layout. Raises :class:`EngineError` on an unsupported format."""
        fmt = (fmt or PDF).lower()
        if fmt not in FORMATS:
            raise EngineError(f"Unsupported export format: {fmt}")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        exporter = QgsLayoutExporter(layout)
        resolution = int(dpi or settings.get("cartography.dpi", 300))

        if fmt in (PDF, GEOPDF):
            pdf_settings = QgsLayoutExporter.PdfExportSettings()
            pdf_settings.dpi = resolution
            pdf_settings.rasterizeWholeImage = False
            if fmt == GEOPDF:
                if not hasattr(pdf_settings, "writeGeoPdf"):
                    return ExportResult(out_path, fmt, False,
                                        "GeoPDF non supportato da questa versione di QGIS")
                pdf_settings.writeGeoPdf = True
            result = exporter.exportToPdf(str(out_path), pdf_settings)
        elif fmt in (PNG, JPEG):
            image_settings = QgsLayoutExporter.ImageExportSettings()
            image_settings.dpi = resolution
            result = exporter.exportToImage(str(out_path), image_settings)
        elif fmt == SVG:
            svg_settings = QgsLayoutExporter.SvgExportSettings()
            svg_settings.dpi = resolution
            result = exporter.exportToSvg(str(out_path), svg_settings)
        else:  # DXF is handled by export_layers_to_dxf
            raise EngineError("DXF export works on layers, use export_layers_to_dxf")

        if result != QgsLayoutExporter.ExportResult.Success:
            message = f"export result {result}"
            log.warning(f"Export failed for {out_path.name}: {message}")
            return ExportResult(out_path, fmt, False, message)
        return ExportResult(out_path, fmt, True)

    @staticmethod
    def export_series(layouts: Sequence[QgsPrintLayout], folder: Path, fmt: str = PDF, *,
                      dpi: Optional[int] = None,
                      names: Optional[Dict[str, str]] = None) -> List[ExportResult]:
        """Export several layouts into a folder with numbered file names."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        results: List[ExportResult] = []
        for index, layout in enumerate(layouts, start=1):
            title = (names or {}).get(layout.name(), layout.name())
            target = folder / file_name(title, fmt, index=index)
            try:
                results.append(ExportCenter.export(layout, target, fmt, dpi=dpi))
            except EngineError as exc:  # pragma: no cover - configuration error
                results.append(ExportResult(target, fmt, False, str(exc)))
        return results


def export_layers_to_dxf(layers: Sequence, out_path: Path, crs, *,
                         scale: float = 1000.0) -> ExportResult:
    """Export vector layers to DXF (for CAD hand-off)."""
    from qgis.core import QgsDxfExport, QgsVectorLayer

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vector_layers = [layer for layer in layers if isinstance(layer, QgsVectorLayer)
                     and layer.isValid()]
    if not vector_layers:
        return ExportResult(out_path, DXF, False, "nessun layer vettoriale da esportare")
    from qgis.PyQt.QtCore import QFile, QIODevice

    dxf = QgsDxfExport()
    device = QFile(str(out_path))
    try:
        entries = [QgsDxfExport.DxfLayer(layer) for layer in vector_layers]
        dxf.addLayers(entries)
        dxf.setSymbologyScale(scale)
        dxf.setDestinationCrs(crs)
        # QgsDxfExport writes to a QIODevice, not to a Python file object.
        if not device.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Text):
            return ExportResult(out_path, DXF, False, "impossibile aprire il file DXF")
        code = dxf.writeToFile(device, "UTF-8")
    except Exception as exc:  # pragma: no cover - API differences
        return ExportResult(out_path, DXF, False, str(exc))
    finally:
        if device.isOpen():
            device.close()
    ok = code in (0, QgsDxfExport.ExportResult.Success) if hasattr(QgsDxfExport, "ExportResult") \
        else code == 0
    return ExportResult(out_path, DXF, bool(ok), "" if ok else f"codice {code}")
