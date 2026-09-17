#!/usr/bin/env python
"""Validate the data-source catalogue (and optionally probe the services).

    "C:\\Program Files\\QGIS 3.40.15\\bin\\python-qgis-ltr.bat" scripts/validate_sources.py
    ... scripts/validate_sources.py --network      # also checks availability

Exit code 1 when a descriptor is invalid, or when --network finds an offline source that
is marked as shipped-and-enabled.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def bootstrap():
    """Initialise a headless QGIS application in a sandbox profile."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sandbox = Path(tempfile.mkdtemp(prefix="territorial_suite_validate_"))
    os.environ["TERRITORIAL_SUITE_HOME"] = str(sandbox / "home")
    os.environ["TERRITORIAL_SUITE_CACHE"] = str(sandbox / "cache")
    sys.path.insert(0, str(REPO_ROOT))
    from qgis.core import QgsApplication

    app = QgsApplication([], False)
    app.initQgis()
    return app


#: The application must outlive every QGIS call: dropping the last Python reference
#: destroys the C++ object and the next network request segfaults.
APP = None


def main() -> int:
    """Load the catalogue, report problems, optionally check availability."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true",
                        help="interroga i servizi per verificarne la disponibilita'")
    parser.add_argument("--category", default="", help="filtra per categoria")
    args = parser.parse_args()

    global APP
    APP = bootstrap()
    from territorial_suite.core.registry import DataSourceRegistry

    registry = DataSourceRegistry().load()
    sources = registry.all(enabled_only=False)
    if args.category:
        sources = [source for source in sources if source.category == args.category]

    print(f"{len(sources)} sorgenti nel catalogo")
    failures = 0
    for problem in registry.errors:
        print(f"  ERRORE descrittore: {problem}")
        failures += 1

    from territorial_suite.core.taxonomy import Taxonomy

    known_categories = set(Taxonomy.instance().ids())

    for source in sources:
        flags = []
        if not source.enabled:
            flags.append("disattivata")
        if not source.last_verified:
            flags.append("mai verificata")
        if source.official and not source.metadata_url:
            flags.append("senza metadata_url")
        if not source.license:
            flags.append("senza licenza")
        if source.category and source.category not in known_categories:
            flags.append("categoria sconosciuta '" + source.category + "'")
            failures += 1
        # The rule of the National Data Fabric: a source is offered as operational only
        # when someone actually talked to the service and wrote down what came back.
        if source.verification_status.value == "verified":
            if not source.verification_note:
                flags.append("verified senza verification_note")
                failures += 1
            if not source.last_verified:
                failures += 1
        if not source.is_operational:
            flags.append("non operativa (" + source.verification_status.value + ")")
        for fallback in source.fallback_sources:
            if registry.get(fallback) is None:
                flags.append("fallback inesistente '" + fallback + "'")
                failures += 1
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {source.id:42s} {source.type.value:15s} {source.category:26s}"
              f"{source.verification_status.value:20s}{suffix}")

    catalogued = registry.catalogued_only()
    if catalogued:
        print("")
        print(f"{len(catalogued)} sorgenti censite ma non operative "
              f"(documentano un gap, non vengono mai interrogate):")
        for source in catalogued:
            print(f"  {source.id:42s} {source.verification_status.value:20s} "
                  f"{source.notes[:60]}")

    if args.network:
        from territorial_suite.core.models import SourceStatus
        from territorial_suite.services.health import HealthChecker

        print("\nVerifica disponibilita':")
        checker = HealthChecker()
        for source in sources:
            if not source.enabled or not source.is_operational:
                continue
            report = checker.check(source, refresh=True)
            status = report.status.value
            print(f"  {source.id:42s} {status:8s} {report.elapsed_ms:5d} ms "
                  f"{report.message[:70]}")
            # A source with no probe for its protocol is not a failure; an unreachable
            # one is.
            if report.status is SourceStatus.OFFLINE:
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
