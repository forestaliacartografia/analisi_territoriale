"""C4, made executable: no failure may be swallowed without a trace.

A handler that catches an exception and then does nothing is indistinguishable, from the
outside, from code that worked. This suite once shipped eighteen of them; the security
scanner of the official plugin repository found every one and blocked the upload. This
test stops them coming back, and it reads the source rather than trusting a convention.

The rule is not "never catch broadly" - a cosmetic layout tweak or an optional Qt API is
worth surviving. The rule is that the reason must remain **inspectable**: log it, count
it, re-raise it, or record it in a result. Just not silence.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from territorial_suite.core.paths import plugin_dir


def python_files():
    """Every module the plugin ships, tests excluded."""
    for path in sorted(plugin_dir().rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def catches_broadly(handler: ast.ExceptHandler) -> bool:
    """Whether the handler catches ``Exception`` (or everything).

    The scope is deliberate. A handler narrowed to ``OSError`` states which failure it
    expects and is a design decision; a broad one states nothing, so swallowing there
    hides failures nobody predicted. Narrow handlers that still discard their exception
    are a real weakness, tracked separately - this guard is about the class that blocked
    the plugin upload, and it must stay honest about what it does not cover.
    """
    caught = handler.type
    if caught is None:
        return True
    names = []
    if isinstance(caught, ast.Name):
        names = [caught.id]
    elif isinstance(caught, ast.Tuple):
        names = [e.id for e in caught.elts if isinstance(e, ast.Name)]
    return "Exception" in names or "BaseException" in names


class TestNoSilentFailures(unittest.TestCase):
    def test_no_handler_swallows_its_exception(self):
        offenders = []
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler) or not catches_broadly(node):
                    continue
                # A handler whose whole body is `pass`, or `continue`/`break` alone,
                # leaves nothing behind: the failure simply never happened.
                body = [n for n in node.body if not (isinstance(n, ast.Expr)
                                                     and isinstance(n.value, ast.Constant)
                                                     and isinstance(n.value.value, str))]
                if len(body) != 1:
                    continue
                only = body[0]
                if isinstance(only, ast.Pass) or isinstance(only, (ast.Continue, ast.Break)):
                    offenders.append(f"{path.relative_to(plugin_dir())}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "gestori che ingoiano l'eccezione senza lasciare traccia (C4):\n  "
            + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
