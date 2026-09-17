"""Shared plumbing for the Processing algorithms.

Every algorithm takes the project area as a polygon feature source, so it composes with the
rest of Processing (models, batch runs, ``qgis_process``) instead of depending on the GUI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterString,
)

from ...core.constants import PLUGIN_NAME
from ...core.errors import TerritorialSuiteError
from ...core.feedback import ProcessingFeedback
from ...core.project_area import ProjectArea

AREA_INPUT = "AREA"
AREA_NAME = "AREA_NAME"


def _no_threading_value():
    """Resolve the "must run in the main thread" flag once.

    QGIS 3.40 and 4.0 spell it differently, so both names are tried. The value is resolved
    at import time and combined inside each ``flags()`` override with ``super().flags()``:
    calling the base method through the class object instead would re-enter the Python
    override and blow the stack.
    """
    for owner, name in ((Qgis, "ProcessingAlgorithmFlag"),
                        (QgsProcessingAlgorithm, "Flag")):
        enum = getattr(owner, name, None)
        if enum is None:
            continue
        for candidate in ("NoThreading", "FlagNoThreading"):
            value = getattr(enum, candidate, None)
            if value is not None:
                return value
    return None  # pragma: no cover - very old builds


#: Flag meaning "this algorithm must run in the main thread" (layouts, layer tree).
NO_THREADING = _no_threading_value()


class TerritorialAlgorithm(QgsProcessingAlgorithm):
    """Base class adding the project-area parameter and the feedback adapter."""

    def group(self) -> str:
        return PLUGIN_NAME

    def groupId(self) -> str:  # noqa: N802 - QGIS API
        return "territorial_suite"

    def createInstance(self):  # noqa: N802 - QGIS API
        return type(self)()

    def tr(self, text: str) -> str:
        """Translation hook (kept simple: the UI ships in Italian and English)."""
        return text

    # ------------------------------------------------------------------ helpers

    def add_area_parameter(self, *, optional: bool = False) -> None:
        """Add the standard project-area parameters."""
        self.addParameter(QgsProcessingParameterFeatureSource(
            AREA_INPUT, self.tr("Area di progetto (poligono)"),
            [QgsProcessing.SourceType.TypeVectorPolygon], optional=optional))
        self.addParameter(QgsProcessingParameterString(
            AREA_NAME, self.tr("Nome dell'area"), defaultValue="", optional=True))

    def project_area(self, parameters: Dict[str, Any], context,
                     feedback) -> ProjectArea:
        """Build a :class:`ProjectArea` from the algorithm parameters."""
        source = self.parameterAsSource(parameters, AREA_INPUT, context)
        if source is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, AREA_INPUT))
        features: List[QgsFeature] = [feature for feature in source.getFeatures()
                                      if feature.hasGeometry()]
        if not features:
            raise QgsProcessingException("L'area di progetto non contiene geometrie.")
        name = self.parameterAsString(parameters, AREA_NAME, context) or "Area di progetto"
        try:
            return ProjectArea.from_features(features, source.sourceCrs(), name=name,
                                             source_kind="processing")
        except TerritorialSuiteError as exc:
            raise QgsProcessingException(str(exc)) from exc

    @staticmethod
    def feedback_adapter(feedback) -> ProcessingFeedback:
        """Wrap a ``QgsProcessingFeedback`` into the engine feedback interface."""
        return ProcessingFeedback(feedback)
