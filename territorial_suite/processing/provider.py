"""Processing provider registering every Territorial Suite algorithm."""

from __future__ import annotations

from typing import List

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from ..core import log
from ..core.constants import PLUGIN_ID, PLUGIN_NAME, PLUGIN_VERSION
from ..core.paths import resources_dir


class TerritorialSuiteProvider(QgsProcessingProvider):
    """Exposes the engines as Processing algorithms."""

    def id(self) -> str:
        return PLUGIN_ID

    def name(self) -> str:
        return PLUGIN_NAME

    def longName(self) -> str:  # noqa: N802 - QGIS API
        return f"{PLUGIN_NAME} {PLUGIN_VERSION}"

    def icon(self) -> QIcon:
        path = resources_dir() / "icons" / "territorial_suite.svg"
        return QIcon(str(path)) if path.exists() else super().icon()

    def loadAlgorithms(self) -> None:  # noqa: N802 - QGIS API
        """Instantiate and register the algorithms, skipping the ones that fail to load."""
        for algorithm in self._algorithms():
            try:
                self.addAlgorithm(algorithm)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning(f"Algoritmo non registrato ({type(algorithm).__name__}): {exc}")

    @staticmethod
    def _algorithms() -> List:
        from .algs.area import (
            AnalyzeAreaAlgorithm,
            AreaStatisticsAlgorithm,
            GenerateBuffersAlgorithm,
        )
        from .algs.cadastre import QueryCadastreAlgorithm
        from .algs.cartography import (
            ExportPackageAlgorithm,
            GenerateMapSeriesAlgorithm,
            GenerateQuickMapAlgorithm,
            GenerateReportAlgorithm,
    ValidateSheetAlgorithm,
)
        from .algs.data import AnalyzeConstraintsAlgorithm, DownloadAreaAlgorithm
        from .algs.heritage import (
            CulturalHeritageAlgorithm,
            ImageryProvidersAlgorithm,
            OrthophotoSheetAlgorithm,
        )
        from .algs.terrain import ElevationProfileAlgorithm, TerrainStatisticsAlgorithm

        return [
            AnalyzeAreaAlgorithm(),
            AreaStatisticsAlgorithm(),
            GenerateBuffersAlgorithm(),
            AnalyzeConstraintsAlgorithm(),
            CulturalHeritageAlgorithm(),
            QueryCadastreAlgorithm(),
            TerrainStatisticsAlgorithm(),
            ElevationProfileAlgorithm(),
            DownloadAreaAlgorithm(),
            GenerateQuickMapAlgorithm(),
            OrthophotoSheetAlgorithm(),
            ImageryProvidersAlgorithm(),
            GenerateMapSeriesAlgorithm(),
            ValidateSheetAlgorithm(),
            GenerateReportAlgorithm(),
            ExportPackageAlgorithm(),
        ]
