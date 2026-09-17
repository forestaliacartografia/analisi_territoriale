"""Process-wide safety switches applied as early as possible at plugin load.

These are not preferences: they prevent hard crashes of the QGIS process.

``OPENPYXL_LXML``
    openpyxl prefers ``lxml`` when it is installed. lxml ships its own libxml2, which
    conflicts with the libxml2 already used by GDAL to parse GML/XML in the same process:
    the symptom is a Windows access violation inside openpyxl, far away from the real
    cause (reproduced on QGIS 3.40, GDAL 3.x, lxml 5.3). Forcing openpyxl onto the
    standard-library XML backend removes the conflict; the export is slightly slower and
    otherwise identical.

Call :func:`apply` once, before anything imports openpyxl.
"""

from __future__ import annotations

import os
import sys

from . import log

#: Environment switches applied at load time.
GUARDS = {
    "OPENPYXL_LXML": "False",
}


def apply() -> None:
    """Apply the guards, warning when it is already too late to matter."""
    for name, value in GUARDS.items():
        os.environ.setdefault(name, value)
    if "openpyxl" in sys.modules:
        try:
            import openpyxl

            if getattr(openpyxl, "LXML", False):
                log.warning(
                    "openpyxl e' gia' stato importato con il backend lxml da un altro "
                    "componente: l'export XLSX usera' il fallback CSV per evitare "
                    "instabilita' con GDAL.")
        except Exception as exc:  # pragma: no cover - defensive
            log.debug(f"apply: operazione non riuscita ({type(exc).__name__}: {exc})")


def openpyxl_is_safe() -> bool:
    """Return whether openpyxl can be used without the lxml/GDAL conflict."""
    apply()
    try:
        import openpyxl
    except ImportError:
        return False
    return not getattr(openpyxl, "LXML", False)
