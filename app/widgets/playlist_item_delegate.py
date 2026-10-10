from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QEvent, QPointF, Qt, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from app.analysis_cache import load_peaks
from app.loudness import TRACK_GAIN_DB_MAX, TRACK_GAIN_DB_MIN, gain_db_to_volume, volume_to_gain_db
from app.playlist_io import (
    BPM_ROLE,
    COLOR_ROLE,
    DURATION_ROLE,
    GAIN_DB_ROLE,
    STEMS_ROLE,
    get_item_file_path,
    is_item_file_missing,
    is_item_placeholder,
)
from app.time_utils import format_time_ms
from app.ui.tokens import get_token

# Callback: item -> {path, position_ms, duration_ms, channel_name} | None
AuxPlaybackProvider = Callable[[object], dict | None]
# Callback: (item, volume 0..1) -> None
GainVolumeHandler = Callable[[object, float], None]

_MINI_WAVE_H = 18
_MINI_WAVE_BARS = 96
_PLAYING_ROW_EXTRA = _MINI_WAVE_H + 6
_GAIN_KNOB = 26
_GAIN_DRAG_PX = 100.0  # full −24…+12 span over this many pixels of vertical drag
_GAIN_WHEEL_DB = 0.5


def _duration_from_index(index) -> int | None:
    value = index.data(DURATION_ROLE)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bpm_from_index(index) -> float | None:
    value = index.data(BPM_ROLE)
    if value is None:
        return None
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    if bpm <= 0:
        return None
    return bpm


def _color_from_index(index) -> QColor | None:
    value = index.data(COLOR_ROLE)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    color = QColor(text)
    return color if color.isValid() else None


def _has_stems_from_index(index) -> bool:
    return bool(index.data(STEMS_ROLE))


def _volume_from_index(index) -> float:
    value = index.data(GAIN_DB_ROLE)
    if value is None:
        return 1.0
    try:
        return gain_db_to_volume(float(value))
    except (TypeError, ValueError):
        return 1.0


def _format_bpm(bpm: float) -> str:
    if bpm == int(bpm):
        return str(int(bpm))
    return f"{bpm:.1f}"


@dataclass(frozen=True)
class _ColumnLayout:
    index_rect: QRect
    name_rect: QRect
    time_rect: QRect
    bpm_rect: QRect
    gain_rect: QRect
    stems_rect: QRect | None
    wave_rect: QRect | None


class PlaylistItemDelegate(QStyledItemDelegate):
    ROW_PAD_H = 4
    COL_GAP = 6
    INDEX_BADGE_W = 24
    TIME_BADGE_W = 46
    BPM_BADGE_W = 32
    STEMS_BADGE_W = 40
    GAIN_KNOB_W = _GAIN_KNOB
    BADGE_RADIUS = 5
    ROW_V_PAD = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge_font = QFont(get_token("typography.font_family_ui", "Arial"))
        self._badge_font.setPixelSize(10)
        self._aux_playback_provider: AuxPlaybackProvider | None = None
        self._gain_handler: GainVolumeHandler | None = None
        self._peaks_cache: dict[str, list[float]] = {}
        self._gain_drag: dict | None = None

    def set_aux_playback_provider(self, provider: AuxPlaybackProvider | None) -> None:
        self._aux_playback_provider = provider

    def set_gain_volume_handler(self, handler: GainVolumeHandler | None) -> None:
        self._gain_handler = handler

    def _aux_info_for_item(self, item) -> dict | None:
        if item is None or self._aux_playback_provider is None:
            return None
        try:
            info = self._aux_playback_provider(item)
        except Exception:
            return None
        return info if isinstance(info, dict) else None

    def _peaks_for_path(self, path: str | None) -> list[float]:
        if not path:
            return []
        cached = self._peaks_cache.get(path)
        if cached is not None:
            return cached
        peaks = load_peaks(path, _MINI_WAVE_BARS) or []
        self._peaks_cache[path] = list(peaks)
        return self._peaks_cache[path]

    def _color(self, token: str, fallback: str) -> QColor:
        return QColor(get_token(f"colors.{token}", fallback))

    def _badge_height(self, option: QStyleOptionViewItem) -> int:
        fm = option.fontMetrics
        return max(_GAIN_KNOB, fm.height() + 2)

    def _layout_columns(
        self,
        rect: QRect,
        badge_h: int,
        *,
        has_stems: bool,
        show_wave: bool,
    ) -> _ColumnLayout:
        inner_left = rect.left() + self.ROW_PAD_H
        inner_right = rect.right() - self.ROW_PAD_H
        top_band_h = badge_h + self.ROW_V_PAD * 2
        if show_wave:
            top_band = QRect(rect.left(), rect.top(), rect.width(), top_band_h)
        else:
            top_band = rect

        badge_y = top_band.top() + (top_band.height() - badge_h) // 2
        knob_y = top_band.top() + (top_band.height() - self.GAIN_KNOB_W) // 2

        index_rect = QRect(inner_left, badge_y, self.INDEX_BADGE_W, badge_h)

        gain_rect = QRect(
            inner_right - self.GAIN_KNOB_W,
            knob_y,
            self.GAIN_KNOB_W,
            self.GAIN_KNOB_W,
        )
        bpm_rect = QRect(
            gain_rect.left() - self.COL_GAP - self.BPM_BADGE_W,
            badge_y,
            self.BPM_BADGE_W,
            badge_h,
        )
        time_rect = QRect(
            bpm_rect.left() - self.COL_GAP - self.TIME_BADGE_W,
            badge_y,
            self.TIME_BADGE_W,
            badge_h,
        )
        stems_rect = None
        name_right = time_rect.left() - self.COL_GAP
        if has_stems:
            stems_rect = QRect(
                time_rect.left() - self.COL_GAP - self.STEMS_BADGE_W,
                badge_y,
                self.STEMS_BADGE_W,
                badge_h,
            )
            name_right = stems_rect.left() - self.COL_GAP

        name_left = index_rect.right() + self.COL_GAP
        name_width = max(0, name_right - name_left)
        name_rect = QRect(name_left, top_band.top(), name_width, top_band.height())

        wave_rect = None
        if show_wave:
            wave_top = top_band.bottom() - 2
            wave_rect = QRect(
                name_left,
                wave_top,
                max(0, inner_right - name_left),
                _MINI_WAVE_H,
            )

        return _ColumnLayout(
            index_rect=index_rect,
            name_rect=name_rect,
            time_rect=time_rect,
            bpm_rect=bpm_rect,
            gain_rect=gain_rect,
            stems_rect=stems_rect,
            wave_rect=wave_rect,
        )

    def _row_background(
        self,
        row: int,
        *,
        selected: bool,
        hovered: bool,
        mark: QColor | None = None,
        aux_playing: bool = False,
    ) -> QColor:
        if selected:
            return self._color("accent_blue", "#3897fd")
        if aux_playing:
            base = QColor(get_token("colors.accent", "#3897fd"))
            base.setAlpha(48)
            # Blend onto surface
            surface = self._color("bg_surface", "#262626")
            return QColor(
                (surface.red() * 2 + base.red()) // 3,
                (surface.green() * 2 + base.green()) // 3,
                (surface.blue() * 2 + base.blue()) // 3,
            )
        if hovered:
            base = self._color("bg_item_hover", "#333333")
        elif row % 2 == 0:
            base = self._color("bg_surface", "#262626")
        else:
            base = self._color("playlist_row_alt", "#2c2c2c")
        if mark is None or not mark.isValid():
            return base
        tinted = QColor(base)
        tinted.setRed(min(255, (tinted.red() * 2 + mark.red()) // 3))
        tinted.setGreen(min(255, (tinted.green() * 2 + mark.green()) // 3))
        tinted.setBlue(min(255, (tinted.blue() * 2 + mark.blue()) // 3))
        return tinted

    def _badge_colors(self, *, selected: bool) -> tuple[QColor, QColor]:
        if selected:
            bg = QColor(255, 255, 255, 38)
            fg = self._color("text_on_accent", "#ffffff")
            return bg, fg
        bg = self._color("bg_control", "#404040")
        fg = self._color("text_secondary", "#e0e0e0")
        return bg, fg

    def _draw_badge(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        *,
        bg: QColor,
        fg: QColor,
    ) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, self.BADGE_RADIUS, self.BADGE_RADIUS)
        painter.setPen(fg)
        painter.setFont(self._badge_font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def _draw_mini_wave(
        self,
        painter: QPainter,
        rect: QRect,
        *,
        peaks: list[float],
        progress: float,
        channel_name: str,
        selected: bool,
    ) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        track_bg = QColor(255, 255, 255, 28 if selected else 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_bg)
        painter.drawRoundedRect(rect, 3, 3)

        mid_y = rect.center().y()
        amp = max(2, rect.height() // 2 - 2)
        n = max(1, len(peaks))
        bar_w = max(1.0, rect.width() / float(n))
        played_x = rect.left() + int(rect.width() * max(0.0, min(1.0, progress)))

        played = QColor(get_token("colors.accent", "#3897fd"))
        if selected:
            played = QColor(255, 255, 255)
        rest = QColor(255, 255, 255, 90 if selected else 70)
        for i, peak in enumerate(peaks):
            h = max(1, int(abs(float(peak)) * amp))
            x = int(rect.left() + i * bar_w)
            w = max(1, int(bar_w) - 1)
            color = played if x + w <= played_x else rest
            painter.fillRect(x, mid_y - h, w, h * 2, color)

        # Playhead
        painter.setPen(QPen(played if not selected else QColor("#ffffff"), 1))
        painter.drawLine(played_x, rect.top() + 1, played_x, rect.bottom() - 1)

        # Channel chip
        label = str(channel_name or "CH")
        chip_w = max(28, painter.fontMetrics().horizontalAdvance(label) + 8)
        chip = QRect(rect.right() - chip_w - 2, rect.top() + 1, chip_w, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        painter.drawRoundedRect(chip, 3, 3)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(self._badge_font)
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), label)

        painter.restore()

    def _draw_gain_knob(
        self,
        painter: QPainter,
        rect: QRect,
        volume: float,
        *,
        selected: bool,
    ) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        v = max(0.0, min(1.0, float(volume)))
        cx = rect.center().x()
        cy = rect.center().y()
        radius = min(rect.width(), rect.height()) / 2.0 - 1.0

        body = QColor(get_token("colors.bg_control", "#404040"))
        if selected:
            body = QColor(255, 255, 255, 48)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.setBrush(body)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        # Clockwise from 7 o'clock (min) through top to 5 o'clock (max),
        # matching QDial / master VolumeKnob.
        # Math degrees (0=east, CCW+): 225° → −45° as value rises.
        start_deg = 225.0
        angle_deg = start_deg - 270.0 * v
        span = 270.0 * v

        accent = QColor(get_token("colors.accent", "#3897fd"))
        if selected:
            accent = QColor("#ffffff")
        pen = QPen(accent, 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arc_rect = QRect(
            int(cx - radius + 2),
            int(cy - radius + 2),
            int((radius - 2) * 2),
            int((radius - 2) * 2),
        )
        # Qt arcs: 0° at 3 o'clock, CCW positive — negative span = clockwise.
        painter.drawArc(
            arc_rect,
            int(start_deg * 16),
            int(-span * 16),
        )

        angle_rad = math.radians(angle_deg)
        inner = radius * 0.25
        outer = radius - 3.0
        x1 = cx + inner * math.cos(angle_rad)
        y1 = cy - inner * math.sin(angle_rad)
        x2 = cx + outer * math.cos(angle_rad)
        y2 = cy - outer * math.sin(angle_rad)
        painter.setPen(QPen(accent if not selected else QColor("#ffffff"), 2))
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.restore()

    def _layout_for_option(self, option, index) -> _ColumnLayout:
        badge_h = self._badge_height(option)
        item = None
        widget = option.widget
        if widget is not None and hasattr(widget, "item"):
            item = widget.item(index.row())
        aux_info = self._aux_info_for_item(item)
        return self._layout_columns(
            option.rect,
            badge_h,
            has_stems=_has_stems_from_index(index),
            show_wave=aux_info is not None,
        )

    def _emit_gain_volume(self, playlist, item, volume: float) -> None:
        if item is None or self._gain_handler is None:
            return
        vol = max(0.0, min(1.0, float(volume)))
        try:
            self._gain_handler(item, vol)
        except Exception:
            return
        playlist.viewport().update()

    def handle_viewport_event(self, playlist, event) -> bool:
        """Interactive track-gain knob (same feel as master VolumeKnob)."""
        etype = event.type()
        if etype not in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.Wheel,
        ):
            return False

        if etype == QEvent.Type.MouseMove and self._gain_drag is not None:
            if not isinstance(event, QMouseEvent):
                return False
            start_vol = float(self._gain_drag["start_vol"])
            start_y = int(self._gain_drag["start_y"])
            item = self._gain_drag.get("item")
            delta = (start_y - event.pos().y()) / _GAIN_DRAG_PX
            self._emit_gain_volume(playlist, item, start_vol + delta)
            return True

        if etype == QEvent.Type.MouseButtonRelease and self._gain_drag is not None:
            self._gain_drag = None
            return True

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = playlist.itemAt(pos)
        if item is None:
            return False

        row = playlist.row(item)
        index = playlist.model().index(row, 0)
        option = QStyleOptionViewItem()
        self.initStyleOption(option, index)
        option.rect = playlist.visualItemRect(item)
        option.widget = playlist
        layout = self._layout_for_option(option, index)
        if not layout.gain_rect.contains(pos):
            return False

        volume = _volume_from_index(index)

        if etype == QEvent.Type.MouseButtonDblClick and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                # Reset to unity (0 dB).
                self._emit_gain_volume(playlist, item, gain_db_to_volume(0.0))
                self._gain_drag = None
                return True

        if etype == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            self._gain_drag = {
                "item": item,
                "start_vol": volume,
                "start_y": pos.y(),
            }
            return True

        if etype == QEvent.Type.Wheel and isinstance(event, QWheelEvent):
            delta = event.angleDelta().y()
            if delta == 0:
                return False
            span = TRACK_GAIN_DB_MAX - TRACK_GAIN_DB_MIN
            step = (_GAIN_WHEEL_DB / span) if span > 0 else 0.05
            if delta < 0:
                step = -step
            self._emit_gain_volume(playlist, item, volume + step)
            return True

        return False

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)

        row = index.row()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        badge_h = self._badge_height(option)
        has_stems = _has_stems_from_index(index)

        item = None
        widget = option.widget
        if widget is not None and hasattr(widget, "item"):
            item = widget.item(row)

        aux_info = self._aux_info_for_item(item)
        show_wave = aux_info is not None
        layout = self._layout_columns(
            option.rect, badge_h, has_stems=has_stems, show_wave=show_wave
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mark = _color_from_index(index)
        painter.fillRect(
            option.rect,
            self._row_background(
                row,
                selected=selected,
                hovered=hovered,
                mark=mark,
                aux_playing=show_wave,
            ),
        )
        if mark is not None and mark.isValid():
            strip = QRect(option.rect.left(), option.rect.top(), 3, option.rect.height())
            painter.fillRect(strip, mark)

        badge_bg, badge_fg = self._badge_colors(selected=selected)
        index_text = str(row + 1)
        duration_text = format_time_ms(_duration_from_index(index))
        bpm_value = _bpm_from_index(index)
        bpm_text = _format_bpm(bpm_value) if bpm_value is not None else "—"
        gain_vol = _volume_from_index(index)

        self._draw_badge(
            painter,
            layout.index_rect,
            index_text,
            bg=badge_bg,
            fg=badge_fg,
        )
        if layout.stems_rect is not None:
            accent_bg = QColor(get_token("colors.accent", "#3897fd"))
            if selected:
                accent_bg = QColor(255, 255, 255, 55)
            self._draw_badge(
                painter,
                layout.stems_rect,
                "stems",
                bg=accent_bg,
                fg=badge_fg,
            )
        self._draw_badge(
            painter,
            layout.time_rect,
            duration_text,
            bg=badge_bg,
            fg=badge_fg,
        )
        self._draw_badge(
            painter,
            layout.bpm_rect,
            bpm_text,
            bg=badge_bg,
            fg=badge_fg,
        )
        self._draw_gain_knob(
            painter,
            layout.gain_rect,
            gain_vol,
            selected=selected,
        )

        if layout.name_rect.width() > 0:
            painter.setFont(option.font)
            placeholder = is_item_placeholder(item)
            if placeholder:
                painter.setPen(self._color("text_muted", "#808080"))
            elif is_item_file_missing(item):
                painter.setPen(self._color("accent_red", "#e45656"))
            elif selected:
                painter.setPen(self._color("text_on_accent", "#ffffff"))
            else:
                painter.setPen(self._color("text_primary", "#ffffff"))

            name = index.data(Qt.ItemDataRole.DisplayRole) or ""
            # Keep name in the top band only (above the mini wave).
            name_draw = QRect(layout.name_rect)
            if show_wave:
                name_draw.setHeight(max(badge_h, name_draw.height() - _MINI_WAVE_H - 2))
            elided = option.fontMetrics.elidedText(
                name,
                Qt.TextElideMode.ElideRight,
                name_draw.width(),
            )
            painter.drawText(
                name_draw,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                elided,
            )

        if show_wave and layout.wave_rect is not None and aux_info is not None:
            path = str(aux_info.get("path") or get_item_file_path(item) or "")
            peaks = self._peaks_for_path(path)
            if not peaks:
                peaks = [0.25] * _MINI_WAVE_BARS
            duration = max(1, int(aux_info.get("duration_ms") or 0))
            position = max(0, int(aux_info.get("position_ms") or 0))
            progress = min(1.0, position / float(duration))
            self._draw_mini_wave(
                painter,
                layout.wave_rect,
                peaks=peaks,
                progress=progress,
                channel_name=str(aux_info.get("channel_name") or "CH"),
                selected=selected,
            )

        painter.restore()

    def sizeHint(self, option, index):
        self.initStyleOption(option, index)
        width = option.rect.width()
        widget = option.widget
        if widget is not None and hasattr(widget, "viewport"):
            width = widget.viewport().width()
        badge_h = self._badge_height(option)
        height = badge_h + self.ROW_V_PAD * 2

        item = None
        if widget is not None and hasattr(widget, "item"):
            item = widget.item(index.row())
        if self._aux_info_for_item(item) is not None:
            height += _PLAYING_ROW_EXTRA

        return QSize(max(width, 0), max(height, _GAIN_KNOB + self.ROW_V_PAD * 2))
