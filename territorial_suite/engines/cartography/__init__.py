"""Cartographic production: layouts, styles, scales, series, imagery and export."""

#: Key under which the imagery provenance is attached to an analysis report.
IMAGERY_MODULE = "imagery"


def provenance_module_key() -> str:
    """Return the report module key used for the imagery provenance."""
    return IMAGERY_MODULE
