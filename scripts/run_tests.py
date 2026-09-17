#!/usr/bin/env python
"""Headless test runner for Territorial Suite.

QGIS does not ship pytest, so the suite is plain ``unittest``. Run it with the Python
interpreter of the QGIS version you want to test:

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" scripts\\run_tests.py
    "C:\\Program Files\\QGIS 4.0.0\\bin\\python-qgis.bat" scripts\\run_tests.py

Options::

    scripts/run_tests.py [pattern] [--network] [--verbose]

``pattern`` filters test files (default ``test_*.py``). Network-dependent tests are
skipped unless ``--network`` (or ``TERRITORIAL_SUITE_NETWORK_TESTS=1``) is given.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def bootstrap() -> "object":
    """Initialise a headless QGIS application and an isolated plugin home."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sandbox = Path(tempfile.mkdtemp(prefix="territorial_suite_tests_"))
    os.environ["TERRITORIAL_SUITE_HOME"] = str(sandbox / "home")
    os.environ["TERRITORIAL_SUITE_CACHE"] = str(sandbox / "cache")
    # Keep the QGIS profile (settings, symbology-style.db, ...) inside the sandbox instead
    # of the working directory.
    os.environ.setdefault("QGIS_CUSTOM_CONFIG_PATH", str(sandbox / "profile"))
    (sandbox / "home").mkdir(parents=True, exist_ok=True)
    (sandbox / "cache").mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(REPO_ROOT))

    from qgis.core import QgsApplication

    # GUI enabled (on the offscreen platform) so that the widget smoke tests can build
    # the dock and the dialogs; everything else works exactly the same.
    app = QgsApplication([], True)
    app.initQgis()

    # Make the QGIS-bundled plugins importable so that the Processing framework (and
    # therefore the provider tests) can be initialised.
    plugins = Path(QgsApplication.prefixPath()) / "python" / "plugins"
    if plugins.is_dir():
        sys.path.append(str(plugins))
    return app


def main() -> int:
    """Discover and run the test suite. Returns the process exit code."""
    args = [a for a in sys.argv[1:]]
    verbose = "--verbose" in args or "-v" in args
    if "--network" in args:
        os.environ["TERRITORIAL_SUITE_NETWORK_TESTS"] = "1"
    patterns = [a for a in args if not a.startswith("-")]
    pattern = patterns[0] if patterns else "test_*.py"

    app = bootstrap()

    from qgis.core import Qgis

    print(f"Running tests on QGIS {Qgis.QGIS_VERSION} (python {sys.version.split()[0]})")
    loader = unittest.TestLoader()
    suite = loader.discover(str(REPO_ROOT / "tests"), pattern=pattern, top_level_dir=str(REPO_ROOT))
    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1, buffer=False)
    result = runner.run(suite)

    try:
        app.exitQgis()
    except Exception:  # pragma: no cover - shutdown noise
        pass
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
