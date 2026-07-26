from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygon, QWheelEvent
from PyQt6.QtWidgets import QWidget

from app.time_utils import format_time_ms
from app.ui.tokens import get_token, get_token_int


class DragMode(Enum):
    NONE = auto()
    RANGE_START = auto()
    RANGE_END = auto()
    FADE_IN = auto()
    FADE_OUT = auto()
    SCRUB = auto()
    PAN = auto()


@dataclass
class TimelineState:
    range_start_ms: int = 0
    range_end_ms: int = 0
    fade_in_ms: int = 0
    fade_out_ms: int = 0


class AudioWaveform(QWidget):
    """Waveform with playback range, fade zones, hover line, and scrubber."""

    positionChanged = pyqtSignal(int)
    rangeChanged = pyqtSignal(int, int)
    fadeChanged = pyqtSignal(int, int)
    widthChanged = pyqtSignal(int)
    viewChanged = pyqtSignal()

    MIN_GAP_MS = 100
    HANDLE_HIT_PX = 10
    TRIANGLE_W = 14
    TRIANGLE_H = 10
    LABEL_H = 16
    MIN_VIEW_MS = 50
    ZOOM_FACTOR = 1.15
    PAN_WHEEL_STEP = 0.08

    def __init__(
        self,
        parent=None,
        *,
        bar_area_height: int | None = None,
        simple: bool = False,
    ):
        super().__init__(parent)
        self.waveform_data: list[float] = []
        self.waveform_map_start_ms = 0
        self.waveform_map_end_ms = 0
        self.position = 0
        self.duration = 0
        self.range_start_ms = 0
        self.range_end_ms = 0
        self.fade_in_ms = 0
        self.fade_out_ms = 0
        self.hover_x: int | None = None
        self.drag_mode = DragMode.NONE
        self._deferred_render = False
        self._drag_emitted = False
        self.view_start_ms = 0
        self.view_end_ms = 0
        self._pan_last_x = 0
        # Simple mode: scrub only — no range/fade markers, no zoom/pan
        self._simple = simple

        if bar_area_height is None:
            wave_height = get_token_int("sizes.waveform_height", 118)
        else:
            wave_height = bar_area_height
        self.setMinimumHeight(wave_height)
        self.setMaximumHeight(wave_height + 20)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # Never steal ↑/↓ keyboard navigation from playlists / file browser
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if simple:
            self.setToolTip("Click or drag to scrub.")
        else:
            self.setToolTip(
                "Колёсико / трекпад вверх-вниз: масштаб. "
                "Трекпад влево-вправо, Shift+колёсико или перетаскивание: прокрутка."
            )

    @property
    def is_simple(self) -> bool:
        return self._simple

    def _color(self, token: str, fallback: str) -> QColor:
        value = get_token(f"colors.{token}", fallback)
        if value.startswith("rgba"):
            parts = value.replace("rgba(", "").replace(")", "").split(",")
            return QColor(int(parts[0]), int(parts[1]), int(parts[2]), int(float(parts[3]) * 255))
        return QColor(value)

    def set_deferred_render(self, enabled: bool):
        self._deferred_render = enabled
        self.update()

    def set_waveform(self, data, *, map_start_ms: int = 0, map_end_ms: int = 0):
        if not data:
            self.waveform_map_start_ms = 0
            self.waveform_map_end_ms = 0
            self._reset_view_state(emit=True)
        else:
            self.waveform_map_start_ms = max(0, map_start_ms)
            if map_end_ms > map_start_ms:
                self.waveform_map_end_ms = map_end_ms
            else:
                self.waveform_map_end_ms = 0
        self.waveform_data = data
        if not self._deferred_render:
            self.update()

    def set_position(self, pos: int):
        self.position = pos
        if self.drag_mode != DragMode.SCRUB and not self._deferred_render:
            self.update()

    def get_timeline_state(self) -> TimelineState:
        return TimelineState(
            range_start_ms=self.range_start_ms,
            range_end_ms=self.range_end_ms,
            fade_in_ms=self.fade_in_ms,
            fade_out_ms=self.fade_out_ms,
        )

    def set_timeline_state(self, state: TimelineState) -> None:
        self.range_start_ms = state.range_start_ms
        self.range_end_ms = state.range_end_ms or self.duration
        self.fade_in_ms = state.fade_in_ms
        self.fade_out_ms = state.fade_out_ms
        self._clamp_range()
        self._clamp_fades()
        self._clamp_view()
        if not self._deferred_render:
            self.update()

    def reset_timeline(self) -> None:
        self.range_start_ms = 0
        self.range_end_ms = self.duration
        self.fade_in_ms = 0
        self.fade_out_ms = 0
        self.update()

    def reset_view(self) -> None:
        self._reset_view_state(emit=True)
        self.update()

    def _reset_view_state(self, *, emit: bool) -> None:
        self.view_start_ms = 0
        self.view_end_ms = 0
        if emit:
            self.viewChanged.emit()

    def set_duration(self, duration: int):
        previous_duration = self.duration
        self.duration = max(0, duration)
        if self.duration > 0 and self.range_end_ms <= 0:
            self.range_end_ms = self.duration
        elif self.duration > 0 and self.range_end_ms > self.duration:
            self.range_end_ms = self.duration
        self._clamp_range()
        if previous_duration != self.duration:
            self._invalidate_view_for_duration()
        self._clamp_view()
        if not self._deferred_render:
            self.update()

    def _invalidate_view_for_duration(self) -> None:
        if self.duration <= 0:
            self._reset_view_state(emit=False)
            return
        if self.view_end_ms <= 0:
            return
        if (
            self.view_start_ms >= self.duration
            or self.view_end_ms > self.duration
            or self.view_start_ms < 0
            or self.view_end_ms <= self.view_start_ms
        ):
            self._reset_view_state(emit=True)

    @property
    def is_zoomed(self) -> bool:
        return self.duration > 0 and self.visible_duration_ms < self.duration

    @property
    def visible_end_ms(self) -> int:
        if self.duration <= 0:
            return 0
        if self.view_end_ms <= 0:
            return self.duration
        return min(self.view_end_ms, self.duration)

    @property
    def visible_duration_ms(self) -> int:
        if self.duration <= 0:
            return 0
        return max(1, self.visible_end_ms - self.view_start_ms)

    def ms_to_x(self, ms: int) -> int:
        width = self.width()
        visible_duration = self.visible_duration_ms
        if self.duration <= 0 or width <= 0 or visible_duration <= 0:
            return 0
        return int(((ms - self.view_start_ms) / visible_duration) * width)

    def x_to_ms(self, x: int) -> int:
        width = self.width()
        if width <= 0 or self.duration <= 0:
            return 0
        ratio = max(0.0, min(1.0, x / width))
        return int(self.view_start_ms + ratio * self.visible_duration_ms)

    def _clamp_view(self) -> None:
        if self.duration <= 0:
            self._reset_view_state(emit=False)
            return

        if self.view_end_ms <= 0:
            if self.view_start_ms > 0:
                self._reset_view_state(emit=True)
            return

        if self.view_end_ms > self.duration:
            self.view_end_ms = self.duration

        if self.view_start_ms >= self.view_end_ms:
            self._reset_view_state(emit=True)
            return

        min_span = min(self.MIN_VIEW_MS, self.duration)
        span = self.view_end_ms - self.view_start_ms
        if span < min_span:
            center = (self.view_start_ms + self.view_end_ms) // 2
            half = min_span // 2
            self.view_start_ms = max(0, center - half)
            self.view_end_ms = min(self.duration, self.view_start_ms + min_span)
            self.view_start_ms = max(0, self.view_end_ms - min_span)

        if self.view_start_ms < 0:
            self.view_start_ms = 0
        if self.view_end_ms > self.duration:
            self.view_end_ms = self.duration
        if self.view_end_ms <= self.view_start_ms:
            self._reset_view_state(emit=True)
            return
        if self.view_end_ms - self.view_start_ms >= self.duration:
            self._reset_view_state(emit=True)

    def _set_view_start(self, start_ms: int) -> None:
        if self.duration <= 0 or not self.is_zoomed:
            return
        span = self.visible_duration_ms
        start_ms = max(0, min(start_ms, self.duration - span))
        self.view_start_ms = start_ms
        self.view_end_ms = start_ms + span
        self._clamp_view()
        self.viewChanged.emit()
        self.update()

    def _pan_by_px(self, dx_px: int) -> None:
        if dx_px == 0 or not self.is_zoomed:
            return
        width = self.width()
        if width <= 0:
            return
        delta_ms = int(-dx_px / width * self.visible_duration_ms)
        if delta_ms == 0:
            return
        self._set_view_start(self.view_start_ms + delta_ms)

    def _pan_by_wheel_notches(self, notches: float) -> None:
        if not self.is_zoomed or notches == 0:
            return
        delta_ms = int(notches * self.visible_duration_ms * self.PAN_WHEEL_STEP)
        if delta_ms == 0:
            return
        self._set_view_start(self.view_start_ms - delta_ms)

    def _data_index_for_ms(self, ms: int, num_data: int) -> int:
        if num_data <= 0:
            return 0
        map_end = self.waveform_map_end_ms
        map_start = self.waveform_map_start_ms
        if map_end > map_start:
            span = map_end - map_start
            ratio = (ms - map_start) / span
        elif self.duration > 0:
            ratio = ms / self.duration
        else:
            return 0
        ratio = max(0.0, min(1.0, ratio))
        return min(num_data - 1, int(ratio * num_data))

    def _clamp_range(self) -> None:
        if self.duration <= 0:
            return
        self.range_start_ms = max(0, min(self.range_start_ms, self.duration - self.MIN_GAP_MS))
        self.range_end_ms = max(
            self.range_start_ms + self.MIN_GAP_MS,
            min(self.range_end_ms, self.duration),
        )

    def _clamp_fades(self) -> None:
        max_range = max(0, self.range_end_ms - self.range_start_ms - self.MIN_GAP_MS)
        self.fade_in_ms = max(0, min(self.fade_in_ms, max_range - self.fade_out_ms))
        self.fade_out_ms = max(0, min(self.fade_out_ms, max_range - self.fade_in_ms))

    def _wave_top(self) -> int:
        return self.LABEL_H + 2

    def _wave_height(self) -> int:
        return self.height() - self._wave_top()

    def _px_to_ms(self, px: int) -> int:
        width = self.width()
        if width <= 0:
            return 0
        return max(1, int(abs(px) / width * self.visible_duration_ms))

    def _ms_in_range(self, ms: int) -> bool:
        if self._simple:
            return 0 <= ms <= self.duration
        return self.range_start_ms <= ms <= self.range_end_ms

    def _hit_zone(self, x: int, y: int, *, right_button: bool = False) -> DragMode:
        if self.duration <= 0:
            return DragMode.NONE

        if self._simple:
            if right_button:
                return DragMode.NONE
            return DragMode.SCRUB

        ms = self.x_to_ms(x)
        start_x = self.ms_to_x(self.range_start_ms)
        end_x = self.ms_to_x(self.range_end_ms)

        if right_button:
            return self._hit_fade_zone(ms)

        wave_top = self._wave_top()
        in_triangle_band = y <= wave_top + self.TRIANGLE_H

        if in_triangle_band and abs(x - start_x) <= self.TRIANGLE_W:
            return DragMode.RANGE_START
        if in_triangle_band and abs(x - end_x) <= self.TRIANGLE_W:
            return DragMode.RANGE_END

        if abs(x - start_x) <= self.HANDLE_HIT_PX:
            return DragMode.RANGE_START
        if abs(x - end_x) <= self.HANDLE_HIT_PX:
            return DragMode.RANGE_END

        if self._ms_in_range(ms):
            return DragMode.SCRUB
        return DragMode.NONE

    def _hit_fade_zone(self, ms: int) -> DragMode:
        if self._simple:
            return DragMode.NONE
        start = self.range_start_ms
        end = self.range_end_ms

        if not self._ms_in_range(ms):
            return DragMode.NONE

        fade_in_edge = start + self.fade_in_ms
        fade_out_edge = end - self.fade_out_ms
        hit_ms = self._px_to_ms(self.HANDLE_HIT_PX)

        if abs(ms - fade_in_edge) <= hit_ms:
            return DragMode.FADE_IN
        if abs(ms - fade_out_edge) <= hit_ms:
            return DragMode.FADE_OUT

        mid_ms = (start + end) // 2
        return DragMode.FADE_IN if ms <= mid_ms else DragMode.FADE_OUT

    def _clamp_scrub_ms(self, ms: int) -> int:
        if self._simple:
            return max(0, min(ms, self.duration))
        return max(self.range_start_ms, min(ms, self.range_end_ms))

    @property
    def is_scrubbing(self) -> bool:
        return self.drag_mode == DragMode.SCRUB

    @property
    def is_panning(self) -> bool:
        return self.drag_mode == DragMode.PAN

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._deferred_render:
            self.widthChanged.emit(self.width())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        wave_top = self._wave_top()
        wave_height = self._wave_height()

        bg = self._color("waveform_bg", "#000000")
        painter.fillRect(0, wave_top, width, wave_height, bg)

        if self._deferred_render:
            return

        if self.duration <= 0 or not self.waveform_data:
            painter.setPen(QPen(self._color("text_muted", "#9f9f9f")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No audio loaded")
            return

        if wave_height <= 0:
            return

        bar_color = self._color("waveform_bar", "#ffffff")
        bar_dim = self._color("waveform_bar_dim", "rgba(255,255,255,0.35)")
        center_y = wave_top + wave_height / 2
        max_amplitude = wave_height / 2 - 4
        num_data = len(self.waveform_data)

        start_x = self.ms_to_x(self.range_start_ms)
        end_x = self.ms_to_x(self.range_end_ms)

        visible_duration = self.visible_duration_ms
        for x in range(width):
            ms_start = self.view_start_ms + (x / width) * visible_duration
            ms_end = self.view_start_ms + ((x + 1) / width) * visible_duration
            start_idx = self._data_index_for_ms(int(ms_start), num_data)
            end_idx = self._data_index_for_ms(int(ms_end), num_data)
            if end_idx <= start_idx:
                end_idx = min(num_data, start_idx + 1)
            else:
                end_idx = min(num_data, end_idx + 1)
            segment = self.waveform_data[start_idx:end_idx]
            if not segment:
                continue

            amplitude = max(segment)
            bar_height = amplitude * max_amplitude
            if bar_height <= 0:
                continue

            if self._simple:
                color = bar_color
            else:
                color = bar_color if start_x <= x <= end_x else bar_dim
            painter.fillRect(
                x,
                int(center_y - bar_height),
                1,
                max(1, int(bar_height * 2)),
                color,
            )

        if not self._simple:
            fade_overlay = self._color("fade_overlay", "rgba(228,86,86,0.25)")
            fade_edge = self._color("fade_edge", "#e45656")
            fade_in_x = self.ms_to_x(self.range_start_ms + self.fade_in_ms)
            fade_out_x = self.ms_to_x(self.range_end_ms - self.fade_out_ms)

            if self.fade_in_ms > 0:
                fade_width = fade_in_x - start_x
                if fade_width > 0:
                    painter.fillRect(start_x, wave_top, fade_width, wave_height, fade_overlay)
                painter.setPen(QPen(fade_edge, 2))
                painter.drawLine(fade_in_x, wave_top, fade_in_x, wave_top + wave_height)

            if self.fade_out_ms > 0:
                fade_width = end_x - fade_out_x
                if fade_width > 0:
                    painter.fillRect(fade_out_x, wave_top, fade_width, wave_height, fade_overlay)
                painter.setPen(QPen(fade_edge, 2))
                painter.drawLine(fade_out_x, wave_top, fade_out_x, wave_top + wave_height)

        if self.duration > 0 and width > 0:
            pos_x = self.ms_to_x(self.position)
            marker = self._color("marker_white", "#d9d9d9")
            painter.setPen(QPen(marker, 2))
            painter.drawLine(pos_x, wave_top, pos_x, wave_top + wave_height)

        if not self._simple:
            marker = self._color("marker_white", "#d9d9d9")
            painter.setPen(QPen(marker, 2))
            painter.drawLine(start_x, wave_top, start_x, wave_top + wave_height)
            painter.drawLine(end_x, wave_top, end_x, wave_top + wave_height)

            self._draw_marker_triangle(painter, start_x, wave_top, marker)
            self._draw_marker_triangle(painter, end_x, wave_top, marker)

            self._draw_time_label(painter, start_x, self.range_start_ms)
            self._draw_time_label(painter, end_x, self.range_end_ms)

        if self.hover_x is not None and self.drag_mode == DragMode.NONE:
            if self._simple:
                hover_x = max(0, min(self.hover_x, width - 1))
            else:
                start_x = self.ms_to_x(self.range_start_ms)
                end_x = self.ms_to_x(self.range_end_ms)
                hover_x = max(start_x, min(self.hover_x, end_x))
            cursor_color = self._color("cursor_line", "#6d6d6d")
            painter.setPen(QPen(cursor_color, 2))
            painter.drawLine(hover_x, wave_top, hover_x, wave_top + wave_height)
            hover_ms = self.x_to_ms(hover_x)
            self._draw_time_label(painter, hover_x, hover_ms, active=True)

    def _draw_marker_triangle(self, painter: QPainter, x: int, wave_top: int, color: QColor) -> None:
        half = self.TRIANGLE_W // 2
        tri = QPolygon([
            QPoint(x, wave_top),
            QPoint(x - half, wave_top + self.TRIANGLE_H),
            QPoint(x + half, wave_top + self.TRIANGLE_H),
        ])
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(tri)

    def _draw_time_label(
        self,
        painter: QPainter,
        x: int,
        ms: int,
        *,
        active: bool = False,
    ) -> None:
        text = format_time_ms(ms)
        font = QFont(get_token("typography.font_family_ui", "Arial"), 8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 8
        label_x = max(0, min(x - tw // 2, self.width() - tw))
        label_y = 0
        bg = self._color("cursor_line" if active else "marker_white", "#6d6d6d")
        text_color = self._color("text_primary" if active else "text_dark", "#ffffff")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRect(label_x, label_y, tw, self.LABEL_H)
        painter.setPen(QPen(text_color))
        painter.drawText(label_x + 4, label_y, tw - 8, self.LABEL_H, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event):
        if self.duration <= 0:
            return

        button = event.button()
        x = int(event.position().x())
        y = int(event.position().y())

        if button == Qt.MouseButton.MiddleButton and self.is_zoomed:
            if self._simple:
                return
            self.drag_mode = DragMode.PAN
            self._pan_last_x = x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if button not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return

        right_button = button == Qt.MouseButton.RightButton
        self.drag_mode = self._hit_zone(x, y, right_button=right_button)
        if self.drag_mode == DragMode.NONE:
            if (
                not self._simple
                and button == Qt.MouseButton.LeftButton
                and self.is_zoomed
                and y >= self._wave_top()
                and not self._ms_in_range(self.x_to_ms(x))
            ):
                self.drag_mode = DragMode.PAN
                self._pan_last_x = x
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                return
        elif (
            not self._simple
            and self.drag_mode == DragMode.SCRUB
            and button == Qt.MouseButton.LeftButton
            and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            and self.is_zoomed
        ):
            self.drag_mode = DragMode.PAN
            self._pan_last_x = x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if self.drag_mode == DragMode.SCRUB:
            self._update_scrub(x)
        elif self.drag_mode == DragMode.PAN:
            self._pan_last_x = x
        else:
            self._update_drag(x)

    def mouseMoveEvent(self, event):
        x = int(event.position().x())
        if self.drag_mode == DragMode.PAN:
            dx = x - self._pan_last_x
            self._pan_last_x = x
            self._pan_by_px(dx)
            return

        if self.drag_mode != DragMode.NONE:
            if self.drag_mode == DragMode.SCRUB:
                self._update_scrub(x)
            else:
                self._update_drag(x)
            return

        if self.duration > 0:
            ms = self.x_to_ms(x)
            if self._ms_in_range(ms):
                self.hover_x = x
                self.unsetCursor()
            else:
                self.hover_x = None
                if self.is_zoomed and int(event.position().y()) >= self._wave_top():
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
                else:
                    self.unsetCursor()
        else:
            self.hover_x = None
            self.unsetCursor()
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag_mode == DragMode.NONE:
            return

        button = event.button()
        if self.drag_mode == DragMode.PAN:
            if button in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.MiddleButton,
            ):
                self.drag_mode = DragMode.NONE
                self.unsetCursor()
                self.update()
            return

        if button not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return

        if self.drag_mode == DragMode.SCRUB:
            self.positionChanged.emit(self.position)
        elif self.drag_mode in (DragMode.RANGE_START, DragMode.RANGE_END):
            self.rangeChanged.emit(self.range_start_ms, self.range_end_ms)
        elif self.drag_mode in (DragMode.FADE_IN, DragMode.FADE_OUT):
            self.fadeChanged.emit(self.fade_in_ms, self.fade_out_ms)

        self.drag_mode = DragMode.NONE
        self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._simple:
            return
        if self.duration <= 0 or not self.waveform_data or self._deferred_render:
            return

        pixel = event.pixelDelta()
        angle = event.angleDelta()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # Trackpad often reports pixelDelta; mouse wheels usually only angleDelta.
        dx = pixel.x()
        dy = pixel.y()
        if dx == 0 and dy == 0:
            dx = angle.x()
            dy = angle.y()

        # Horizontal-dominant gesture (trackpad left/right) → pan when zoomed.
        if abs(dx) > abs(dy) and dx != 0:
            if not self.is_zoomed:
                return
            if pixel.x() != 0:
                self._pan_by_px(pixel.x())
            else:
                self._pan_by_wheel_notches(dx / 120.0)
            event.accept()
            return

        # Shift + vertical scroll → pan (mouse wheel / trackpad).
        if shift:
            if not self.is_zoomed:
                return
            delta = dy if dy != 0 else dx
            if delta == 0:
                return
            if pixel.y() != 0 or (pixel.x() != 0 and dy == 0):
                self._pan_by_px(pixel.y() if pixel.y() != 0 else pixel.x())
            else:
                self._pan_by_wheel_notches(delta / 120.0)
            event.accept()
            return

        # Vertical scroll → zoom around cursor.
        if dy == 0:
            return

        width = self.width()
        if width <= 0:
            return

        x = int(event.position().x())
        anchor_ms = self.x_to_ms(x)
        visible_duration = self.visible_duration_ms
        zoom_in = dy > 0
        factor = self.ZOOM_FACTOR if zoom_in else 1.0 / self.ZOOM_FACTOR
        new_duration = int(visible_duration / factor)
        new_duration = max(
            min(self.MIN_VIEW_MS, self.duration),
            min(new_duration, self.duration),
        )

        if new_duration >= self.duration:
            self._reset_view_state(emit=True)
            self.update()
        else:
            ratio = x / width
            new_start = int(anchor_ms - ratio * new_duration)
            new_start = max(0, min(new_start, self.duration - new_duration))
            self.view_start_ms = new_start
            self.view_end_ms = new_start + new_duration

        self._clamp_view()
        self.viewChanged.emit()
        self.update()
        event.accept()

    def _update_scrub(self, x: int) -> None:
        self.position = self._clamp_scrub_ms(self.x_to_ms(x))
        self.update()

    def _update_drag(self, x: int) -> None:
        ms = self.x_to_ms(x)

        if self.drag_mode == DragMode.RANGE_START:
            self.range_start_ms = max(0, min(ms, self.range_end_ms - self.MIN_GAP_MS))
        elif self.drag_mode == DragMode.RANGE_END:
            self.range_end_ms = max(self.range_start_ms + self.MIN_GAP_MS, min(ms, self.duration))
        elif self.drag_mode == DragMode.FADE_IN:
            rel = max(0, min(ms - self.range_start_ms, self.range_end_ms - self.range_start_ms - self.fade_out_ms - self.MIN_GAP_MS))
            self.fade_in_ms = rel
        elif self.drag_mode == DragMode.FADE_OUT:
            rel = max(0, min(self.range_end_ms - ms, self.range_end_ms - self.range_start_ms - self.fade_in_ms - self.MIN_GAP_MS))
            self.fade_out_ms = rel

        self._clamp_fades()
        self.update()
