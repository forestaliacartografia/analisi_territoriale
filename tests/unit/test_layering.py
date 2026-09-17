"""Architecture rules, enforced automatically.

The layering of ``docs/DESIGN.md`` section B.1 is only real if a test checks it:

* ``core`` knows nothing about services, engines, tasks, gui or processing;
* ``services`` only depends on ``core``;
* ``engines`` depend on ``core`` and ``services``;
* only ``gui`` imports QtWidgets;
* no service URL is hardcoded outside ``config/``.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Dict, List, Set

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent / "territorial_suite"
PACKAGE_NAME = "territorial_suite"

#: layer -> layers it is allowed to import from (besides itself).
ALLOWED: Dict[str, Set[str]] = {
    "core": set(),
    "services": {"core"},
    "engines": {"core", "services"},
    "tasks": {"core", "services", "engines"},
    "processing": {"core", "services", "engines", "tasks"},
    "gui": {"core", "services", "engines", "tasks", "processing"},
}

#: literals that look like a URL but are only scheme checks.
URL_PATTERN = re.compile(r"^(https?)://[^\s]+\.[^\s]+")

#: Hosts allowed in code: standard OGC/W3C identifiers, not data services.
URL_ALLOWLIST = ("www.opengis.net", "www.w3.org", "schemas.opengis.net",
                 "inspire.ec.europa.eu")


def python_files() -> List[Path]:
    """Every python module shipped inside the plugin package."""
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def layer_of(path: Path) -> str:
    """Return the layer a module belongs to (``""`` for top-level modules)."""
    relative = path.relative_to(PACKAGE_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else ""


def imported_modules(tree: ast.AST) -> Set[str]:
    """Return the plugin-internal modules imported by a parsed module."""
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE_NAME + "."):
                    found.add(alias.name[len(PACKAGE_NAME) + 1:])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import: resolved by the caller, which knows the module path
                found.add(f"__relative__{node.level}:{node.module or ''}")
            elif node.module and node.module.startswith(PACKAGE_NAME + "."):
                found.add(node.module[len(PACKAGE_NAME) + 1:])
    return found


def resolve_relative(path: Path, marker: str) -> str:
    """Resolve a ``__relative__`` marker produced by :func:`imported_modules`."""
    level_text, _, module = marker[len("__relative__"):].partition(":")
    level = int(level_text)
    package_parts = list(path.relative_to(PACKAGE_ROOT).parts[:-1])
    if level > 1:
        package_parts = package_parts[: len(package_parts) - (level - 1)]
    target = package_parts + ([module] if module else [])
    return ".".join(target)


def docstring_nodes(tree: ast.AST) -> Set[int]:
    """Return the ids of constant nodes that are docstrings."""
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


class TestLayering(unittest.TestCase):
    def test_package_has_modules(self):
        self.assertGreater(len(python_files()), 5)

    def test_layer_dependencies(self):
        violations = []
        for path in python_files():
            layer = layer_of(path)
            if layer not in ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module in imported_modules(tree):
                resolved = resolve_relative(path, module) if module.startswith("__relative__") else module
                target_layer = resolved.split(".")[0] if resolved else ""
                if not target_layer or target_layer == layer:
                    continue
                if target_layer not in ALLOWED and "." not in resolved:
                    continue  # top-level module such as "plugin"
                if target_layer in ALLOWED and target_layer not in ALLOWED[layer]:
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)} imports {resolved}")
        self.assertEqual(violations, [], "layer violations: " + "; ".join(violations))

    def test_only_gui_imports_widgets(self):
        # plugin.py is the QGIS entry point: it wires menus, toolbars and docks, so it is
        # allowed to touch QtWidgets and iface. Everything else must stay headless.
        allowed = {"gui"}
        offenders = []
        for path in python_files():
            if layer_of(path) in allowed or path.name == "plugin.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "QtWidgets" in text or "from qgis.utils import iface" in text:
                offenders.append(str(path.relative_to(PACKAGE_ROOT)))
        self.assertEqual(offenders, [],
                         "only the gui layer may use QtWidgets or iface: " + ", ".join(offenders))

    def test_no_hardcoded_service_urls_outside_config(self):
        offenders = []
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            skip = docstring_nodes(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and id(node) not in skip and URL_PATTERN.match(node.value.strip()) \
                        and not any(host in node.value for host in URL_ALLOWLIST):
                    offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno} -> {node.value[:60]}")
        self.assertEqual(offenders, [],
                         "service URLs belong to config/sources: " + "; ".join(offenders))

    def test_qt_is_imported_through_the_shim(self):
        offenders = []
        for path in python_files():
            text = path.read_text(encoding="utf-8")
            for forbidden in ("from PyQt5", "import PyQt5", "from PyQt6", "import PyQt6"):
                if forbidden in text:
                    offenders.append(f"{path.relative_to(PACKAGE_ROOT)} uses {forbidden}")
        self.assertEqual(offenders, [],
                         "always import Qt through qgis.PyQt: " + "; ".join(offenders))

    def test_no_unscoped_qvariant_field_types(self):
        offenders = []
        for path in python_files():
            text = path.read_text(encoding="utf-8")
            if re.search(r"QVariant\.(String|Int|Double|Bool|Date|DateTime|LongLong)", text):
                offenders.append(str(path.relative_to(PACKAGE_ROOT)))
        self.assertEqual(offenders, [],
                         "use QMetaType.Type via core.qt_compat: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
