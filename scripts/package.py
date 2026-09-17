#!/usr/bin/env python
"""Build the installable plugin ZIP.

    python scripts/package.py [--output dist] [--name territorial_suite]

The archive contains a single top-level folder (the plugin id), as required by the QGIS
plugin installer and by the official repository.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "territorial_suite"

EXCLUDED_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "tests", "dist"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".qgz~"}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}


def metadata_value(key: str) -> str:
    """Read a value from metadata.txt."""
    text = (PLUGIN_DIR / "metadata.txt").read_text(encoding="utf-8")
    match = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def files_to_pack() -> List[Path]:
    """Every file that belongs in the archive."""
    result: List[Path] = []
    for path in sorted(PLUGIN_DIR.rglob("*")):
        if path.is_dir():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES:
            continue
        result.append(path)
    return result


def validate() -> List[str]:
    """Basic checks before packaging."""
    problems: List[str] = []
    required = ["name", "qgisMinimumVersion", "description", "version", "author", "email"]
    for key in required:
        if not metadata_value(key):
            problems.append(f"metadata.txt: manca '{key}'")
    version = metadata_value("version")
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("ts_constants", PLUGIN_DIR / "core" / "constants.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.PLUGIN_VERSION != version:
        problems.append(f"versione non allineata: metadata.txt={version}, "
                        f"constants.py={module.PLUGIN_VERSION}")
    icon = metadata_value("icon")
    if icon and not (PLUGIN_DIR / icon).exists():
        problems.append(f"icona mancante: {icon}")
    if not (PLUGIN_DIR / "__init__.py").exists():
        problems.append("manca __init__.py")
    return problems


def main() -> int:
    """Create the ZIP and print its path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist", help="cartella di destinazione")
    parser.add_argument("--name", default="territorial_suite", help="nome del plugin")
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()

    if not args.skip_validation:
        problems = validate()
        if problems:
            print("Packaging interrotto:")
            for problem in problems:
                print(f"  - {problem}")
            return 1

    version = metadata_value("version")
    output_dir = (REPO_ROOT / args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{args.name}-{version}.zip"
    files = files_to_pack()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for path in files:
            zip_file.write(path, Path(args.name) / path.relative_to(PLUGIN_DIR))
    size = archive.stat().st_size / 1024
    print(f"{archive}  ({len(files)} file, {size:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
