"""Territorial Suite - QGIS territorial intelligence and cartographic production suite.

This module only exposes the QGIS plugin entry point. Everything else lives in the
layered packages ``core``, ``services``, ``engines``, ``tasks``, ``gui`` and ``processing``.
"""

from __future__ import annotations


# noinspection PyPep8Naming
def classFactory(iface):  # pragma: no cover - QGIS entry point
    """Load the plugin class.

    :param iface: a QGIS interface instance (``qgis.gui.QgisInterface``)
    """
    from .plugin import TerritorialSuitePlugin

    return TerritorialSuitePlugin(iface)
