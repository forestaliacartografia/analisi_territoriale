"""Cultural Heritage / MiC module.

Acquisition, normalisation, classification, deduplication, data-quality assessment and
reporting of cultural and landscape heritage data, built on top of the shared analysis
engines rather than beside them: features are downloaded by
:mod:`territorial_suite.services.fetcher` and measured by
:mod:`territorial_suite.engines.spatial`, exactly like every other category.
"""

from .engine import CulturalHeritageEngine
from .model import (
    ActReference,
    CulturalAsset,
    CulturalHeritageOutcome,
    DataGap,
    Finding,
    Superintendency,
    ThemeOutcome,
)

__all__ = [
    "ActReference",
    "CulturalAsset",
    "CulturalHeritageEngine",
    "CulturalHeritageOutcome",
    "DataGap",
    "Finding",
    "Superintendency",
    "ThemeOutcome",
]
