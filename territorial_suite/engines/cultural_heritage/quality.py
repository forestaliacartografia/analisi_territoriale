"""Why a theme is empty: the difference between "nothing here" and "we do not know".

This is the part of the module that stops a clean-looking dossier from being wrong. Six
outcomes that a naive implementation would all print as *nessun bene culturale rilevato*:

=========================== ==========================================================
``NO_FEATURE_FOUND``        the source answered and the area really is clear
``NO_DATA``                 nothing is configured for this theme at all
``SOURCE_UNAVAILABLE``      the service did not answer
``QUERY_FAILED``            it answered something unusable
``SOURCE_OUTSIDE_COVERAGE`` the area is outside what the source actually maps
``SOURCE_NOT_VERIFIED``     a dataset is known to exist but cannot be queried
=========================== ==========================================================

Only the first licenses the sentence "no cultural asset intersects the area".
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from ...core.models import AdminUnits, SourceResult, SourceStatus
from ...core.registry import DataSource
from .model import DataGap
from .normalizer import clean_text


def _names(admin: AdminUnits) -> List[str]:
    """Every administrative name that could match a coverage entry."""
    values: List[str] = []
    for unit in list(admin.municipalities) + list(admin.provinces) + list(admin.regions):
        for value in (unit.name, unit.region_name, unit.province_code):
            if value:
                values.append(str(value).strip().lower())
    return values


def outside_coverage(source: DataSource, admin: AdminUnits) -> bool:
    """Whether the area sits in a territory the descriptor declares as not covered.

    Only an explicit ``coverage.missing`` entry counts. The plugin never guesses that a
    silence means "not covered here".
    """
    missing = [str(item).strip().lower() for item in source.coverage.missing if item]
    if not missing or not admin.resolved:
        return False
    names = _names(admin)
    return any(entry in name or name in entry for entry in missing for name in names
               if entry and name)


class CulturalHeritageQuality:
    """Classifies the outcome of every source of a theme."""

    def __init__(self, admin: AdminUnits) -> None:
        self.admin = admin

    def gap_for(self, source: DataSource, result: Optional[SourceResult]) -> DataGap:
        """Return the gap that explains an empty or failed result."""
        if not source.is_operational:
            return DataGap.SOURCE_NOT_VERIFIED
        if result is None:
            return DataGap.NO_DATA
        if result.status is SourceStatus.OFFLINE and not result.error:
            return DataGap.SOURCE_UNAVAILABLE
        if not result.ok:
            # A schema error means the service answered with something unusable, which is
            # very different from a service that is down.
            text = (result.error or "").lower()
            unusable = any(token in text for token in
                           ("schema", "geometry", "crs", "parse", "could not be read",
                            "invalid"))
            return DataGap.QUERY_FAILED if unusable else DataGap.SOURCE_UNAVAILABLE
        if outside_coverage(source, self.admin):
            return DataGap.SOURCE_OUTSIDE_COVERAGE
        return DataGap.NO_FEATURE_FOUND

    def assess(self, sources: Sequence[DataSource],
               results: Dict[str, SourceResult]) -> Dict[str, DataGap]:
        """Return ``{source id: gap}`` for the sources that produced no record."""
        gaps: Dict[str, DataGap] = {}
        for source in sources:
            result = results.get(source.id)
            if result is not None and result.ok and result.hits:
                continue
            gaps[source.id] = self.gap_for(source, result)
        return gaps

    def coverage_notes(self, sources: Sequence[DataSource]) -> List[str]:
        """Caveats worth printing next to an empty theme."""
        notes: List[str] = []
        for source in sources:
            note = clean_text(source.coverage.notes)
            if note and note not in notes:
                notes.append(f"{source.name}: {note}")
        return notes


def summarise(gaps: Dict[str, List[str]]) -> List[str]:
    """Human sentences for the dossier, one per gap kind that actually occurred."""
    lines: List[str] = []
    for key, categories in gaps.items():
        try:
            gap = DataGap(key)
        except ValueError:  # pragma: no cover - forward compatibility
            continue
        if not categories:
            continue
        subject = ", ".join(sorted(set(categories)))
        if gap.means_absence:
            lines.append(f"{gap.label_it}: {subject} (le fonti hanno risposto).")
        else:
            lines.append(f"{gap.label_it}: {subject}. Il tema non e' stato valutato e "
                         f"l'assenza di elementi non puo' essere interpretata come "
                         f"assenza di beni o di tutele.")
    return lines
