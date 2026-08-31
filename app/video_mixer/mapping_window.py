"""Projection mapping editor for the main/output canvas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent,
    QKeyEvent,
    QMouseEvent,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets.float_value_stepper import FloatValueStepper
from app.video_mixer.gl_widget import (
    OutputGLWidget,
    letterbox_norm_corners,
)

if TYPE_CHECKING:
    from app.video_mixer.media_store import MediaStore
    from app.video_mixer.model import MixerModel


_HANDLE_HIT = 12.0


class MappingCanvas(OutputGLWidget):
    """Interactive output preview: drag corners / move / scale the mapped quad."""

    mappingEdited = pyqtSignal()

    def __init__(self, model: MixerModel, media_store: MediaStore, parent=None):
        super().__init__(model, media_store, parent)
        self.show_mapping_handles = True
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)
        self._drag_mode: str | None = None
        self._drag_corner: int | None = None
        self._drag_start: QPointF | None = None
        self._drag_corners: list[tuple[float, float]] | None = None

    def _ensure_editable_corners(self) -> list[tuple[float, float]]:
        mapping = self.model.output_mapping
        if mapping.is_identity():
            corners = letterbox_norm_corners(self.model, self.width(), self.height())
            mapping.tl, mapping.tr, mapping.br, mapping.bl = corners  # type: ignore[misc]
            return list(corners)
        return list(mapping.corners())

    def _widget_corners(self) -> list[QPointF]:
        corners = self._ensure_editable_corners() if self._drag_mode else (
            letterbox_norm_corners(self.model, self.width(), self.height())
            if self.model.output_mapping.is_identity()
            else list(self.model.output_mapping.corners())
        )
        vw = float(max(1, self.width()))
        vh = float(max(1, self.height()))
        return [QPointF(x * vw, y * vh) for x, y in corners]

    def _hit_corner(self, pos: QPointF) -> int | None:
        for i, pt in enumerate(self._widget_corners()):
            if abs(pos.x() - pt.x()) <= _HANDLE_HIT and abs(pos.y() - pt.y()) <= _HANDLE_HIT:
                return i
        return None

    def _hit_inside(self, pos: QPointF) -> bool:
        poly = QPolygonF(self._widget_corners())
        return poly.containsPoint(pos, Qt.FillRule.OddEvenFill)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        corner = self._hit_corner(pos)
        corners = self._ensure_editable_corners()
        if corner is not None:
            self._drag_mode = "corner"
            self._drag_corner = corner
            self._drag_start = QPointF(pos)
            self._drag_corners = list(corners)
            return
        if self._hit_inside(pos):
            self._drag_mode = "move"
            self._drag_corner = None
            self._drag_start = QPointF(pos)
            self._drag_corners = list(corners)
            return

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag_mode is None:
            if self._hit_corner(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif self._hit_inside(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._drag_start is None or self._drag_corners is None:
            return

        vw = float(max(1, self.width()))
        vh = float(max(1, self.height()))
        dx = (pos.x() - self._drag_start.x()) / vw
        dy = (pos.y() - self._drag_start.y()) / vh
        mapping = self.model.output_mapping

        if self._drag_mode == "corner" and self._drag_corner is not None:
            ox, oy = self._drag_corners[self._drag_corner]
            mapping.set_corner(self._drag_corner, ox + dx, oy + dy)
        elif self._drag_mode == "move":
            for i, (ox, oy) in enumerate(self._drag_corners):
                mapping.set_corner(i, ox + dx, oy + dy)

        self.model.outputMappingChanged.emit()
        self.mappingEdited.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = None
            self._drag_corner = None
            self._drag_start = None
            self._drag_corners = None
            self.mappingEdited.emit()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self._ensure_editable_corners()
        factor = 1.05 if delta > 0 else 1.0 / 1.05
        self.model.scale_output_mapping(factor)
        self.mappingEdited.emit()
        self.update()
        event.accept()


class MappingWindow(QWidget):
    """Separate window to transform the main screen on the physical output."""

    closed = pyqtSignal()

    def __init__(self, model: MixerModel, media_store: MediaStore, parent=None):
        super().__init__(parent)
        self.model = model
        self.media_store = media_store
        self.setWindowTitle("Projection Mapping")
        self.setObjectName("videoMixerMappingWindow")
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(960, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        title = QLabel("MAIN SCREEN MAP")
        title.setObjectName("propTitle")
        toolbar.addWidget(title)
        toolbar.addStretch(1)

        scale_label = QLabel("Scale")
        scale_label.setObjectName("propTitle")
        toolbar.addWidget(scale_label)
        self.scale_stepper = FloatValueStepper(
            self, 5.0, 400.0, 100.0, step=1.0, decimals=0, value_width=48
        )
        self.scale_stepper.setToolTip("Uniform scale of the mapped screen (%)")
        self.scale_stepper.valueChanged.connect(self._on_scale_changed)
        toolbar.addWidget(self.scale_stepper)

        width_label = QLabel("W")
        width_label.setObjectName("propTitle")
        toolbar.addWidget(width_label)
        self.width_stepper = FloatValueStepper(
            self, 5.0, 400.0, 100.0, step=1.0, decimals=0, value_width=48
        )
        self.width_stepper.setToolTip("Horizontal scale of the mapped screen (%)")
        self.width_stepper.valueChanged.connect(self._on_width_changed)
        toolbar.addWidget(self.width_stepper)

        height_label = QLabel("H")
        height_label.setObjectName("propTitle")
        toolbar.addWidget(height_label)
        self.height_stepper = FloatValueStepper(
            self, 5.0, 400.0, 100.0, step=1.0, decimals=0, value_width=48
        )
        self.height_stepper.setToolTip("Vertical scale of the mapped screen (%)")
        self.height_stepper.valueChanged.connect(self._on_height_changed)
        toolbar.addWidget(self.height_stepper)

        self.btn_reset = QToolButton()
        self.btn_reset.setObjectName("mixerToolBtn")
        self.btn_reset.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_reset.setText("Reset")
        self.btn_reset.setToolTip("Reset mapping to letterboxed full screen")
        self.btn_reset.clicked.connect(self._reset)
        toolbar.addWidget(self.btn_reset)

        self.btn_fit = QToolButton()
        self.btn_fit.setObjectName("mixerToolBtn")
        self.btn_fit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_fit.setText("Fit")
        self.btn_fit.setToolTip("Fit canvas into the window (letterbox)")
        self.btn_fit.clicked.connect(self._fit)
        toolbar.addWidget(self.btn_fit)

        hint = QLabel("Corners · move inside · wheel scale · W/H steppers · Esc close")
        hint.setObjectName("mixerTimeLabel")
        hint.setStyleSheet("color: #9f9f9f; font-size: 11px;")
        toolbar.addWidget(hint)
        root.addLayout(toolbar)

        self.canvas = MappingCanvas(model, media_store, self)
        self.canvas.mappingEdited.connect(self._on_canvas_edited)
        root.addWidget(self.canvas, 1)

        self._updating_controls = False
        self.model.outputMappingChanged.connect(self._on_mapping_changed)
        self._sync_transform_readout()

    def refresh(self) -> None:
        if self.isVisible():
            self.canvas.update()

    def _on_mapping_changed(self) -> None:
        self._sync_transform_readout()
        self.canvas.update()

    def _on_canvas_edited(self) -> None:
        self._sync_transform_readout()

    def _reference_size(self) -> tuple[float, float]:
        ref = letterbox_norm_corners(
            self.model, max(1, self.canvas.width()), max(1, self.canvas.height())
        )
        ref_w = max(abs(ref[1][0] - ref[0][0]), 1e-6)
        ref_h = max(abs(ref[3][1] - ref[0][1]), 1e-6)
        return ref_w, ref_h

    def _current_size(self) -> tuple[float, float]:
        cur = self.model.output_mapping.corners()
        cur_w = max(abs(cur[1][0] - cur[0][0]), abs(cur[2][0] - cur[3][0]), 1e-6)
        cur_h = max(abs(cur[3][1] - cur[0][1]), abs(cur[2][1] - cur[1][1]), 1e-6)
        return cur_w, cur_h

    def _ensure_editable_mapping(self) -> None:
        mapping = self.model.output_mapping
        if mapping.is_identity():
            corners = letterbox_norm_corners(
                self.model, max(1, self.canvas.width()), max(1, self.canvas.height())
            )
            mapping.tl, mapping.tr, mapping.br, mapping.bl = corners  # type: ignore[misc]

    def _sync_transform_readout(self) -> None:
        if self.model.output_mapping.is_identity():
            scale_pct = width_pct = height_pct = 100.0
        else:
            ref_w, ref_h = self._reference_size()
            cur_w, cur_h = self._current_size()
            scale_pct = max(5.0, min(400.0, (cur_w / ref_w) * 100.0))
            width_pct = scale_pct
            height_pct = max(5.0, min(400.0, (cur_h / ref_h) * 100.0))
        self._updating_controls = True
        self.scale_stepper.set_value(scale_pct)
        self.width_stepper.set_value(width_pct)
        self.height_stepper.set_value(height_pct)
        self._updating_controls = False

    def _apply_axis_scale(self, axis: str, pct: float) -> None:
        if self._updating_controls:
            return
        self._ensure_editable_mapping()
        ref_w, ref_h = self._reference_size()
        cur_w, cur_h = self._current_size()
        if axis == "uniform":
            ref = ref_w
            cur = cur_w
            scale_fn = self.model.scale_output_mapping
        elif axis == "x":
            ref = ref_w
            cur = cur_w
            scale_fn = self.model.scale_output_mapping_x
        else:
            ref = ref_h
            cur = cur_h
            scale_fn = self.model.scale_output_mapping_y
        current_pct = (cur / ref) * 100.0
        if current_pct <= 1e-3:
            return
        factor = float(pct) / current_pct
        scale_fn(factor)
        self.canvas.update()

    def _on_scale_changed(self, pct: float) -> None:
        self._apply_axis_scale("uniform", pct)

    def _on_width_changed(self, pct: float) -> None:
        self._apply_axis_scale("x", pct)

    def _on_height_changed(self, pct: float) -> None:
        self._apply_axis_scale("y", pct)

    def _reset(self) -> None:
        self.model.reset_output_mapping()
        self.canvas.update()
        self._sync_transform_readout()

    def _fit(self) -> None:
        corners = letterbox_norm_corners(
            self.model, max(1, self.canvas.width()), max(1, self.canvas.height())
        )
        mapping = self.model.output_mapping
        mapping.tl, mapping.tr, mapping.br, mapping.bl = corners  # type: ignore[misc]
        self.model.outputMappingChanged.emit()
        self.model.changed.emit()
        self.canvas.update()
        self._sync_transform_readout()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.closed.emit()
        super().closeEvent(event)

    def release(self) -> None:
        self.canvas.release()
