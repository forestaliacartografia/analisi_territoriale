"""Qt5/Qt6 and QGIS 3.40/4.0 compatibility helpers.

Rules verified on QGIS 3.40 LTR (PyQt5 5.15 / Qt 5.15) and QGIS 4.0 (PyQt6 / Qt 6.8):

* always use *scoped* enum access (``Qt.AlignmentFlag.AlignLeft``) - works on both;
* always build fields with ``QMetaType.Type`` - ``QVariant.Type`` is deprecated in 3.40
  and unavailable as an unscoped enum in Qt6;
* always call ``dialog.exec()`` - ``exec_()`` is gone in PyQt6.

Import Qt only through ``qgis.PyQt`` so the shim picks the right binding.
"""

from __future__ import annotations

from typing import Any, Iterable

from qgis.PyQt.QtCore import QMetaType
from qgis.core import QgsField, QgsFields

#: Common field types, resolved once.
STRING = QMetaType.Type.QString
INT = QMetaType.Type.Int
LONG = QMetaType.Type.LongLong
DOUBLE = QMetaType.Type.Double
BOOL = QMetaType.Type.Bool
DATE = QMetaType.Type.QDate
DATETIME = QMetaType.Type.QDateTime


def field(name: str, meta_type: Any = STRING, *, length: int = 0, precision: int = 0,
          alias: str = "", comment: str = "") -> QgsField:
    """Create a :class:`QgsField` in a way that works on QGIS 3.40 and 4.0."""
    fld = QgsField(name, meta_type)
    if length:
        fld.setLength(length)
    if precision:
        fld.setPrecision(precision)
    if alias:
        fld.setAlias(alias)
    if comment:
        fld.setComment(comment)
    return fld


def fields_from(spec: Iterable[tuple]) -> QgsFields:
    """Build :class:`QgsFields` from ``(name, meta_type)`` or ``(name, meta_type, alias)``.

    :param spec: iterable of tuples describing the fields, in order.
    """
    result = QgsFields()
    for item in spec:
        name, meta_type = item[0], item[1]
        alias = item[2] if len(item) > 2 else ""
        result.append(field(name, meta_type, alias=alias))
    return result


def exec_dialog(dialog: Any) -> int:
    """Run a modal dialog on both PyQt5 and PyQt6."""
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return int(runner())
