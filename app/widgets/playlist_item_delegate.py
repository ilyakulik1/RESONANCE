from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from app.playlist_io import BPM_ROLE, COLOR_ROLE, DURATION_ROLE, is_item_file_missing
from app.time_utils import format_time_ms
from app.ui.tokens import get_token


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


class PlaylistItemDelegate(QStyledItemDelegate):
    ROW_PAD_H = 4
    COL_GAP = 6
    INDEX_BADGE_W = 24
    TIME_BADGE_W = 46
    BPM_BADGE_W = 32
    BADGE_RADIUS = 5
    ROW_V_PAD = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge_font = QFont(get_token("typography.font_family_ui", "Arial"))
        self._badge_font.setPixelSize(10)

    def _color(self, token: str, fallback: str) -> QColor:
        return QColor(get_token(f"colors.{token}", fallback))

    def _badge_height(self, option: QStyleOptionViewItem) -> int:
        fm = option.fontMetrics
        return max(18, fm.height() + 2)

    def _layout_columns(self, rect: QRect, badge_h: int) -> _ColumnLayout:
        inner_left = rect.left() + self.ROW_PAD_H
        inner_right = rect.right() - self.ROW_PAD_H
        badge_y = rect.top() + (rect.height() - badge_h) // 2

        index_rect = QRect(inner_left, badge_y, self.INDEX_BADGE_W, badge_h)

        bpm_rect = QRect(
            inner_right - self.BPM_BADGE_W,
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

        name_left = index_rect.right() + self.COL_GAP
        name_right = time_rect.left() - self.COL_GAP
        name_width = max(0, name_right - name_left)
        name_rect = QRect(name_left, rect.top(), name_width, rect.height())

        return _ColumnLayout(
            index_rect=index_rect,
            name_rect=name_rect,
            time_rect=time_rect,
            bpm_rect=bpm_rect,
        )

    def _row_background(
        self,
        row: int,
        *,
        selected: bool,
        hovered: bool,
        mark: QColor | None = None,
    ) -> QColor:
        if selected:
            return self._color("accent_blue", "#3897fd")
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

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)

        row = index.row()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        badge_h = self._badge_height(option)
        layout = self._layout_columns(option.rect, badge_h)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mark = _color_from_index(index)
        painter.fillRect(
            option.rect,
            self._row_background(row, selected=selected, hovered=hovered, mark=mark),
        )
        if mark is not None and mark.isValid() and not selected:
            strip = QRect(option.rect.left(), option.rect.top(), 3, option.rect.height())
            painter.fillRect(strip, mark)

        badge_bg, badge_fg = self._badge_colors(selected=selected)
        index_text = str(row + 1)
        duration_text = format_time_ms(_duration_from_index(index))
        bpm_value = _bpm_from_index(index)
        bpm_text = _format_bpm(bpm_value) if bpm_value is not None else "—"

        self._draw_badge(
            painter,
            layout.index_rect,
            index_text,
            bg=badge_bg,
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

        item = None
        widget = option.widget
        if widget is not None and hasattr(widget, "item"):
            item = widget.item(row)

        if layout.name_rect.width() > 0:
            painter.setFont(option.font)
            if is_item_file_missing(item):
                painter.setPen(self._color("accent_red", "#e45656"))
            elif selected:
                painter.setPen(self._color("text_on_accent", "#ffffff"))
            else:
                painter.setPen(self._color("text_primary", "#ffffff"))

            name = index.data(Qt.ItemDataRole.DisplayRole) or ""
            elided = option.fontMetrics.elidedText(
                name,
                Qt.TextElideMode.ElideRight,
                layout.name_rect.width(),
            )
            painter.drawText(
                layout.name_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                elided,
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
        return QSize(max(width, 0), max(height, 26))
