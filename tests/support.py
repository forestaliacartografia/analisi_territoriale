"""Shared helpers for the test suite."""

from __future__ import annotations

import os
import unittest
from typing import Optional

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle

#: A small square near Firenze (WGS84), used as the default test area.
FIRENZE_BBOX = QgsRectangle(11.240, 43.770, 11.250, 43.780)
#: A square straddling two municipalities in the Apennines (WGS84).
MOUNTAIN_BBOX = QgsRectangle(11.000, 44.050, 11.030, 44.080)


def network_enabled() -> bool:
    """Whether tests that hit real services are allowed to run."""
    return os.environ.get("TERRITORIAL_SUITE_NETWORK_TESTS", "") == "1"


def requires_network(test):
    """Decorator skipping a test unless network tests are explicitly enabled."""
    return unittest.skipUnless(network_enabled(),
                               "network tests disabled (use --network)")(test)


def rect_geometry(rect: Optional[QgsRectangle] = None) -> QgsGeometry:
    """Return a polygon geometry from a rectangle (default: the Firenze test bbox)."""
    return QgsGeometry.fromRect(rect or FIRENZE_BBOX)


def point(x: float, y: float) -> QgsPointXY:
    """Convenience constructor for a map point."""
    return QgsPointXY(x, y)
