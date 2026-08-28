from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget

from app.video_mixer.compositor import layer_aabb, layer_corners

if TYPE_CHECKING:
    from app.video_mixer.media_store import MediaStore
    from app.video_mixer.model import MixerModel

_HANDLE = 8.0


def frame_to_qimage(frame: np.ndarray) -> QImage | None:
    if frame is None or frame.ndim != 3:
        return None
    h, w, c = frame.shape
    if w <= 0 or h <= 0:
        return None
    arr = np.ascontiguousarray(frame)
    if c >= 4:
        img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    else:
        img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888)
    if img.isNull():
        return None
    return img.copy()


def view_params(model: MixerModel, vw: int, vh: int) -> tuple[float, float, float]:
    cw = float(max(1, model.canvas_width))
    ch = float(max(1, model.canvas_height))
    scale = min(vw / cw, vh / ch)
    offset_x = (vw - cw * scale) / 2.0
    offset_y = (vh - ch * scale) / 2.0
    return scale, offset_x, offset_y


class MixerCanvasWidget(QWidget):
    """Software compositor: paints scene layers with QPainter (reliable on all platforms)."""

    def __init__(
        self,
        model: MixerModel,
        media_store: MediaStore,
        parent=None,
        *,
        scene_role: str = "preview",
        show_adorner: bool = False,
    ):
        super().__init__(parent)
        self.model = model
        self.media_store = media_store
        self.scene_role = scene_role
        self.show_adorner = show_adorner
        self.setMinimumHeight(80)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def _scene_id(self) -> str | None:
        if self.scene_role == "main":
            return self.model.main_scene_id
        return self.model.preview_scene_id

    def _paint_scene(self):
        if self.scene_role == "main":
            return self.model.program_scene()
        return self.model.preview_scene()

    def canvas_to_widget(self, canvas_x: float, canvas_y: float) -> tuple[float, float]:
        scale, ox, oy = view_params(self.model, self.width(), self.height())
        return ox + canvas_x * scale, oy + canvas_y * scale

    def widget_to_canvas(self, widget_x: float, widget_y: float) -> tuple[float, float]:
        scale, ox, oy = view_params(self.model, self.width(), self.height())
        if scale <= 0:
            return 0.0, 0.0
        return (widget_x - ox) / scale, (widget_y - oy) / scale

    def _paint_canvas(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#000000"))

        scale, ox, oy = view_params(self.model, self.width(), self.height())
        cw = self.model.canvas_width
        ch = self.model.canvas_height

        # Letterbox canvas bounds
        canvas_rect = QRectF(ox, oy, cw * scale, ch * scale)
        painter.fillRect(canvas_rect, QColor("#101010"))
        painter.setClipRect(canvas_rect)

        scene = self._paint_scene()
        if scene is not None:
            painter.save()
            painter.translate(ox, oy)
            painter.scale(scale, scale)
            for layer in scene.layers:
                if not layer.visible or layer.file_missing:
                    continue
                media = self.media_store.get(layer.id)
                if media is None or media.width <= 0:
                    continue
                t = layer.transform
                painter.save()
                painter.translate(t.x, t.y)
                painter.rotate(t.rotation)
                painter.scale(t.scale_x, t.scale_y)
                painter.translate(-t.anchor_x * media.width, -t.anchor_y * media.height)
                if media.pixmap is not None and not media.pixmap.isNull():
                    painter.drawPixmap(0, 0, media.pixmap)
                elif media.frame is not None:
                    image = frame_to_qimage(media.frame)
                    if image is not None and not image.isNull():
                        painter.drawImage(0, 0, image)
                painter.restore()
            painter.restore()

        painter.setClipping(False)
        # Canvas border
        painter.setPen(QPen(QColor("#404040")))
        painter.drawRect(canvas_rect)

        if self.show_adorner:
            self._draw_adorner(painter)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        self._paint_canvas(painter)
        painter.end()

    def _draw_adorner(self, painter: QPainter) -> None:
        layer = self.model.selected_layer()
        if layer is None or not layer.visible or layer.file_missing:
            return
        media = self.media_store.get(layer.id)
        if media is None or media.width <= 0 or media.height <= 0:
            return
        corners = layer_corners(layer.transform, media.width, media.height)
        widget_pts = [QPointF(*self.canvas_to_widget(x, y)) for x, y in corners]
        pen = QPen(QColor("#ffffff"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        for i in range(4):
            painter.drawLine(widget_pts[i], widget_pts[(i + 1) % 4])
        handles = _handle_points(widget_pts)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#000000")))
        for pt in handles.values():
            painter.drawRect(QRectF(pt.x() - _HANDLE / 2, pt.y() - _HANDLE / 2, _HANDLE, _HANDLE))

    def release(self) -> None:
        pass


def _handle_points(widget_pts: list[QPointF]) -> dict[str, QPointF]:
    tl, tr, br, bl = widget_pts
    return {
        "tl": tl,
        "tr": tr,
        "br": br,
        "bl": bl,
        "tm": QPointF((tl.x() + tr.x()) / 2, (tl.y() + tr.y()) / 2),
        "bm": QPointF((bl.x() + br.x()) / 2, (bl.y() + br.y()) / 2),
        "lm": QPointF((tl.x() + bl.x()) / 2, (tl.y() + bl.y()) / 2),
        "rm": QPointF((tr.x() + br.x()) / 2, (tr.y() + br.y()) / 2),
    }


class MainGLWidget(MixerCanvasWidget):
    """MAIN SCREEN view with optional crossfade between scenes."""

    def __init__(self, model: MixerModel, media_store: MediaStore, parent=None):
        super().__init__(model, media_store, parent, scene_role="main", show_adorner=False)
        self._fade_from: QPixmap | None = None
        self._fade_t: float = 1.0
        self._fade_duration_ms: int = 0
        self._fade_elapsed_ms: int = 0
        self._hold_outgoing: bool = False
        self._fade_incoming: QPixmap | None = None

    def capture_current(self) -> QPixmap:
        """Opaque widget-sized snapshot of the current program look."""
        return self._render_plate()

    def begin_transition_hold(self, outgoing: QPixmap) -> None:
        """Deprecated hold path — prefer live main during preload."""
        self._fade_from = outgoing
        self._fade_t = 0.0
        self._fade_elapsed_ms = 0
        self._fade_duration_ms = 0
        self._hold_outgoing = True
        self.update()

    def start_crossfade(self, duration_ms: int, outgoing: QPixmap | None = None) -> None:
        if outgoing is not None and not outgoing.isNull():
            self._fade_from = outgoing
        self._hold_outgoing = False
        self._fade_duration_ms = max(0, int(duration_ms))
        self._fade_elapsed_ms = 0
        self._fade_t = 0.0 if self._fade_duration_ms > 0 and self._fade_from is not None else 1.0
        if self._fade_t >= 1.0:
            self._fade_from = None
            self._fade_incoming = None
        self.update()

    def tick_transition(self, dt_ms: int) -> bool:
        """Advance crossfade; return True if still animating."""
        if self._hold_outgoing:
            self.update()
            return True
        if self._fade_from is None or self._fade_t >= 1.0:
            return False
        if self._fade_duration_ms <= 0:
            self._fade_t = 1.0
            self._fade_from = None
            self._fade_incoming = None
            self.update()
            return False
        self._fade_elapsed_ms += max(0, int(dt_ms))
        self._fade_t = min(1.0, self._fade_elapsed_ms / float(self._fade_duration_ms))
        if self._fade_t >= 1.0:
            self._fade_from = None
            self._fade_incoming = None
        self.update()
        return self._fade_from is not None

    def _render_plate(self, target: QPixmap | None = None) -> QPixmap:
        """Paint the current look at native DPI so the dissolve stays sharp."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        width = max(1, round(self.width() * dpr))
        height = max(1, round(self.height() * dpr))
        plate = target
        if plate is None or plate.isNull() or plate.width() != width or plate.height() != height:
            plate = QPixmap(width, height)
        plate.setDevicePixelRatio(1.0)
        plate.fill(QColor("#000000"))
        painter = QPainter(plate)
        painter.scale(dpr, dpr)
        self._paint_canvas(painter)
        painter.end()
        plate.setDevicePixelRatio(dpr)
        return plate

    def _incoming_plate(self) -> QPixmap:
        plate = self._render_plate(self._fade_incoming)
        self._fade_incoming = plate
        return plate

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._fade_from is None or (self._fade_t >= 1.0 and not self._hold_outgoing):
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.fillRect(self.rect(), QColor("#000000"))

        # Dissolve: keep the outgoing plate fully opaque and fade the new look
        # on top. Dual-opacity over black dipped to grey on similar scenes.
        if not self._fade_from.isNull():
            painter.setOpacity(1.0)
            painter.drawPixmap(self.rect(), self._fade_from)

        if not self._hold_outgoing and self._fade_t > 0.0:
            incoming = self._incoming_plate()
            if not incoming.isNull():
                painter.setOpacity(self._fade_t)
                painter.drawPixmap(self.rect(), incoming)

        painter.setOpacity(1.0)
        painter.end()


class OutputGLWidget(MainGLWidget):
    """Physical output view with the same Main crossfade."""


class PreviewGLWidget(MixerCanvasWidget):
    """Interactive PREVIEW with selection handles."""

    transformEdited = pyqtSignal()

    def __init__(self, model: MixerModel, media_store: MediaStore, parent=None):
        super().__init__(model, media_store, parent, scene_role="preview", show_adorner=True)
        self.setMouseTracking(True)
        self._drag_mode: str | None = None
        self._drag_start_canvas: QPointF | None = None
        self._drag_start_transform = None

    def _hit_handle(self, pos: QPointF) -> str | None:
        layer = self.model.selected_layer()
        if layer is None:
            return None
        media = self.media_store.get(layer.id)
        if media is None or media.width <= 0:
            return None
        corners = layer_corners(layer.transform, media.width, media.height)
        widget_pts = [QPointF(*self.canvas_to_widget(x, y)) for x, y in corners]
        for name, pt in _handle_points(widget_pts).items():
            if abs(pos.x() - pt.x()) <= _HANDLE and abs(pos.y() - pt.y()) <= _HANDLE:
                return name
        return None

    def _hit_layer(self, canvas_x: float, canvas_y: float) -> str | None:
        scene = self.model.preview_scene()
        if scene is None:
            return None
        for layer in reversed(scene.layers):
            if not layer.visible or layer.file_missing:
                continue
            media = self.media_store.get(layer.id)
            if media is None or media.width <= 0:
                continue
            x0, y0, x1, y1 = layer_aabb(layer.transform, media.width, media.height)
            if x0 <= canvas_x <= x1 and y0 <= canvas_y <= y1:
                return layer.id
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        handle = self._hit_handle(pos)
        cx, cy = self.widget_to_canvas(pos.x(), pos.y())

        if handle:
            layer = self.model.selected_layer()
            if layer is None:
                return
            self._drag_mode = f"scale_{handle}"
            self._drag_start_canvas = QPointF(cx, cy)
            t = layer.transform
            self._drag_start_transform = (t.x, t.y, t.scale_x, t.scale_y)
            return

        hit = self._hit_layer(cx, cy)
        if hit:
            self.model.select_layer(hit)
            layer = self.model.selected_layer()
            if layer is None:
                return
            self._drag_mode = "move"
            self._drag_start_canvas = QPointF(cx, cy)
            self._drag_start_transform = (
                layer.transform.x,
                layer.transform.y,
                layer.transform.scale_x,
                layer.transform.scale_y,
            )
            self.update()
        else:
            self.model.select_layer(None)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        cx, cy = self.widget_to_canvas(pos.x(), pos.y())

        if self._drag_mode is None:
            if self._hit_handle(pos) or self._hit_layer(cx, cy):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        layer = self.model.selected_layer()
        if layer is None or self._drag_start_canvas is None or self._drag_start_transform is None:
            return

        dx = cx - self._drag_start_canvas.x()
        dy = cy - self._drag_start_canvas.y()
        ox, oy, osx, osy = self._drag_start_transform

        if self._drag_mode == "move":
            self.model.update_transform(layer.id, x=ox + dx, y=oy + dy)
        elif self._drag_mode.startswith("scale_"):
            media = self.media_store.get(layer.id)
            if media is None or media.width <= 0:
                return
            ref = max(media.width, media.height) * 0.5
            factor = max(0.05, 1.0 + (dx + dy) / max(ref, 1.0))
            self.model.update_transform(layer.id, scale_x=osx * factor, scale_y=osy * factor)

        self.transformEdited.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = None
            self._drag_start_canvas = None
            self._drag_start_transform = None
