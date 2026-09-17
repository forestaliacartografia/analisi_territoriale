"""Why a result is missing. These reasons are **not** interchangeable.

The single most damaging thing a territorial plugin can do is let "we found nothing" be
read as "there is nothing". A source that timed out, a source that does not cover the
area, a source that can only be looked at, and an area that is genuinely clear are four
different statements, and only the last one is good news.

The enum started inside the cultural heritage engine, which is where the distinction was
first needed. It lives here because every engine needs it, and because two of the values
below describe situations that engine never met: a service that can be displayed but not
analysed, and a theme whose authority is regional so a national answer cannot exist.
"""

from __future__ import annotations

from enum import Enum


class DataGap(str, Enum):
    """Why a theme produced no record."""

    #: The source answered and there is genuinely nothing in the area.
    NO_FEATURE_FOUND = "NO_FEATURE_FOUND"
    #: No source at all is configured for this theme.
    NO_DATA = "NO_DATA"
    #: The source is configured but did not answer.
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    #: The area falls outside the declared coverage of the source.
    SOURCE_OUTSIDE_COVERAGE = "SOURCE_OUTSIDE_COVERAGE"
    #: The request failed (schema, CRS, unreadable geometries).
    QUERY_FAILED = "QUERY_FAILED"
    #: The source exists in the catalogue but is not operational.
    SOURCE_NOT_VERIFIED = "SOURCE_NOT_VERIFIED"
    #: The service publishes an image, or geometry without the attributes the analysis
    #: needs: it can be shown on a sheet, it cannot support a measurement.
    VIEW_ONLY = "VIEW_ONLY"
    #: The theme is a regional competence and no national dataset can answer for it.
    #: Never to be reported as "not subject to the constraint".
    REGIONAL_SOURCE_REQUIRED = "REGIONAL_SOURCE_REQUIRED"
    #: Data was obtained, but not for the whole area: a figure computed on it would
    #: describe a smaller area than the one the user asked about.
    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    #: The result was produced at a coarser detail than the one requested.
    REDUCED_RESOLUTION = "REDUCED_RESOLUTION"

    @property
    def label_it(self) -> str:
        """Italian wording used in the dossier."""
        return {
            DataGap.NO_FEATURE_FOUND: "nessun elemento nell'area",
            DataGap.NO_DATA: "nessuna fonte configurata per il tema",
            DataGap.SOURCE_UNAVAILABLE: "fonte non disponibile",
            DataGap.SOURCE_OUTSIDE_COVERAGE: "area fuori dalla copertura della fonte",
            DataGap.QUERY_FAILED: "interrogazione non riuscita",
            DataGap.SOURCE_NOT_VERIFIED: "fonte censita ma non operativa",
            DataGap.VIEW_ONLY: "strato consultabile, non analizzabile geometricamente",
            DataGap.REGIONAL_SOURCE_REQUIRED:
                "competenza regionale: serve la fonte della regione interessata",
            DataGap.PARTIAL_COVERAGE: "copertura parziale dell'area",
            DataGap.REDUCED_RESOLUTION: "dettaglio inferiore a quello richiesto",
        }[self]

    @property
    def means_absence(self) -> bool:
        """Whether this gap may be read as "the theme is not present in the area".

        Exactly one value may: the one where a working source answered and returned
        nothing. Every other value is a statement about the *data*, not the territory.
        """
        return self is DataGap.NO_FEATURE_FOUND
