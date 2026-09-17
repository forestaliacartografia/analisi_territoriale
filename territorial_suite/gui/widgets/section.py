"""Small widgets shared by the dock: collapsible sections and compact buttons."""

from __future__ import annotations

from typing import Callable, List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class Section(QWidget):
    """A titled, collapsible block of the dock."""

    def __init__(self, title: str, parent: Optional[QWidget] = None, *,
                 collapsed: bool = False) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 4)
        self._layout.setSpacing(2)

        self.header = QToolButton(self)
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(not collapsed)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.DownArrow if not collapsed
                                 else Qt.ArrowType.RightArrow)
        self.header.setAutoRaise(True)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        font = self.header.font()
        font.setBold(True)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.header.setFont(font)
        self.header.toggled.connect(self._on_toggled)
        self._layout.addWidget(self.header)

        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 2, 2, 2)
        self.body_layout.setSpacing(3)
        self._layout.addWidget(self.body)
        self.body.setVisible(not collapsed)

    def _on_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        self.header.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def add(self, widget: QWidget) -> QWidget:
        """Add a widget to the section body."""
        self.body_layout.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        """Add a nested layout to the section body."""
        self.body_layout.addLayout(layout)

    def add_buttons(self, entries: List[tuple], *, columns: int = 2) -> List[QPushButton]:
        """Add a grid of buttons from ``(label, tooltip, callback)`` tuples."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        buttons: List[QPushButton] = []
        for index, entry in enumerate(entries):
            label, tooltip, callback = entry[0], entry[1], entry[2]
            button = QPushButton(label, self.body)
            button.setToolTip(tooltip)
            if callback is not None:
                button.clicked.connect(callback)
            grid.addWidget(button, index // columns, index % columns)
            buttons.append(button)
        self.body_layout.addLayout(grid)
        return buttons


def separator(parent: Optional[QWidget] = None) -> QFrame:
    """Return a thin horizontal separator."""
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def caption(text: str, parent: Optional[QWidget] = None, *, bold: bool = False,
            wrap: bool = True) -> QLabel:
    """Return a small explanatory label."""
    label = QLabel(text, parent)
    label.setWordWrap(wrap)
    font = label.font()
    font.setPointSizeF(max(font.pointSizeF() - 1.0, 7.0))
    font.setBold(bold)
    label.setFont(font)
    return label


def primary_button(text: str, callback: Optional[Callable] = None,
                   parent: Optional[QWidget] = None) -> QPushButton:
    """Return the emphasised button of a section."""
    button = QPushButton(text, parent)
    font = button.font()
    font.setBold(True)
    button.setFont(font)
    button.setMinimumHeight(30)
    if callback is not None:
        button.clicked.connect(callback)
    return button
