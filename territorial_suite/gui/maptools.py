"""Map tools to draw a project area on the canvas."""

from __future__ import annotations

from typing import List, Optional

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle, QgsWkbTypes
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

MODE_POLYGON = "polygon"
MODE_RECTANGLE = "rectangle"
MODE_CIRCLE = "circle"
MODE_FREEHAND = "freehand"


class AreaDrawTool(QgsMapTool):
    """Draw a polygon, a rectangle, a circle or a freehand shape on the canvas.

    Left click adds a vertex (or starts the shape), right click closes it, ``Esc`` aborts.
    The geometry is emitted in the canvas CRS.
    """

    finished = pyqtSignal(object)      # QgsGeometry in canvas CRS
    aborted = pyqtSignal()

    def __init__(self, canvas: QgsMapCanvas, mode: str = MODE_POLYGON) -> None:
        super().__init__(canvas)
        self.canvas = canvas
        self.mode = mode
        self.points: List[QgsPointXY] = []
        self.drawing = False
        self.band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.band.setColor(QColor(212, 0, 0, 60))
        self.band.setStrokeColor(QColor(212, 0, 0))
        self.band.setWidth(2)

    # ------------------------------------------------------------------ helpers

    def reset(self) -> None:
        """Clear the rubber band and the vertex list."""
        self.points = []
        self.drawing = False
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def deactivate(self) -> None:  # pragma: no cover - GUI callback
        self.reset()
        super().deactivate()

    def _update_band(self, current: Optional[QgsPointXY] = None) -> None:
        points = list(self.points)
        if current is not None:
            points = self._shape_points(current)
        self.band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        if len(points) < 2:
            return
        self.band.setToGeometry(QgsGeometry.fromPolygonXY([points + [points[0]]]), None)

    def _shape_points(self, current: QgsPointXY) -> List[QgsPointXY]:
        if self.mode == MODE_RECTANGLE and self.points:
            start = self.points[0]
            rect = QgsRectangle(start, current)
            return [QgsPointXY(rect.xMinimum(), rect.yMinimum()),
                    QgsPointXY(rect.xMaximum(), rect.yMinimum()),
                    QgsPointXY(rect.xMaximum(), rect.yMaximum()),
                    QgsPointXY(rect.xMinimum(), rect.yMaximum())]
        if self.mode == MODE_CIRCLE and self.points:
            centre = self.points[0]
            radius = centre.distance(current)
            geometry = QgsGeometry.fromPointXY(centre).buffer(radius, 48)
            polygon = geometry.asPolygon()
            return [QgsPointXY(p) for p in polygon[0]] if polygon else [centre, current]
        return self.points + [current]

    # ------------------------------------------------------------------ events

    def canvasPressEvent(self, event) -> None:  # pragma: no cover - GUI callback
        point = self.toMapCoordinates(event.pos())
        if event.button() == Qt.MouseButton.RightButton:
            self._finish()
            return
        if self.mode in (MODE_RECTANGLE, MODE_CIRCLE):
            if not self.points:
                self.points = [point]
                self.drawing = True
            else:
                self.points = self._shape_points(point)
                self._finish()
            return
        self.points.append(point)
        self.drawing = True
        self._update_band()

    def canvasMoveEvent(self, event) -> None:  # pragma: no cover - GUI callback
        if not self.drawing:
            return
        point = self.toMapCoordinates(event.pos())
        if self.mode == MODE_FREEHAND:
            self.points.append(point)
            self._update_band()
            return
        self._update_band(point)

    def canvasReleaseEvent(self, event) -> None:  # pragma: no cover - GUI callback
        if self.mode == MODE_FREEHAND and self.drawing and \
                event.button() == Qt.MouseButton.LeftButton and len(self.points) > 2:
            self._finish()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI callback
        if event.key() == Qt.Key.Key_Escape:
            self.reset()
            self.aborted.emit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
        elif event.key() == Qt.Key.Key_Backspace and self.points:
            self.points.pop()
            self._update_band()

    def _finish(self) -> None:
        """Close the shape and emit it."""
        if len(self.points) < 3:
            self.reset()
            self.aborted.emit()
            return
        geometry = QgsGeometry.fromPolygonXY([self.points + [self.points[0]]])
        self.reset()
        if geometry is None or geometry.isEmpty():
            self.aborted.emit()
            return
        self.finished.emit(geometry)
