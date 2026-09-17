"""Import smoke test: every module of the plugin must import cleanly.

This is the cheapest guard against syntax errors, bad imports and API drift between QGIS
3.40 and 4.0 - it runs on both.
"""

from __future__ import annotations

import importlib
import pkgutil
import unittest
from pathlib import Path

import territorial_suite

PACKAGE_ROOT = Path(territorial_suite.__file__).resolve().parent

#: Modules that must not be imported outside a real QGIS GUI session.
SKIP = set()


def module_names() -> list:
    """Return every importable module of the plugin package."""
    names = []
    for info in pkgutil.walk_packages([str(PACKAGE_ROOT)],
                                      prefix="territorial_suite."):
        if info.name in SKIP:
            continue
        names.append(info.name)
    return sorted(names)


class TestImports(unittest.TestCase):
    def test_every_module_imports(self):
        failures = []
        for name in module_names():
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 - we want the full list
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "moduli non importabili:\n" + "\n".join(failures))

    def test_plugin_entry_point_exists(self):
        from territorial_suite import classFactory

        self.assertTrue(callable(classFactory))

    def test_processing_provider_declares_algorithms(self):
        from territorial_suite.processing.provider import TerritorialSuiteProvider

        algorithms = TerritorialSuiteProvider._algorithms()
        self.assertGreaterEqual(len(algorithms), 10)
        names = {algorithm.name() for algorithm in algorithms}
        for expected in ("analyze_area", "query_cadastre", "terrain_statistics",
                         "download_area_dataset", "generate_quick_map"):
            self.assertIn(expected, names)

    def test_metadata_is_consistent(self):
        from territorial_suite.core.constants import PLUGIN_VERSION

        metadata = (PACKAGE_ROOT / "metadata.txt").read_text(encoding="utf-8")
        self.assertIn(f"version={PLUGIN_VERSION}", metadata)
        self.assertIn("qgisMinimumVersion=3.40", metadata)
        self.assertIn("hasProcessingProvider=yes", metadata)
        self.assertIn("supportsQt6=True", metadata)


if __name__ == "__main__":
    unittest.main()
