from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import Qt, QPoint, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
)

from app.analysis_cache import load_peaks
from app.playlist_io import (
    BPM_ROLE,
    COLOR_ROLE,
    DURATION_ROLE,
    NOTES_ROLE,
    STEMS_ROLE,
    get_item_file_path,
    is_item_file_missing,
    is_item_placeholder,
    normalize_notes,
)
from app.time_utils import format_time_ms
from app.ui.tokens import get_token

# Callback: item -> {path, position_ms, duration_ms, channel_name} | None
AuxPlaybackProvider = Callable[[object], dict | None]

_MINI_WAVE_H = 18
_MINI_WAVE_BARS = 96
_PLAYING_ROW_EXTRA = _MINI_WAVE_H + 6


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


def _notes_from_index(index) -> list[str]:
    return normalize_notes(index.data(NOTES_ROLE))


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
    stems_rect: QRect | None
    note_rects: tuple[tuple[str, QRect], ...]
    wave_rect: QRect | None


class PlaylistItemDelegate(QStyledItemDelegate):
    ROW_PAD_H = 4
    COL_GAP = 6
    META_PAD = 6
    STEMS_BADGE_W = 40
    BADGE_RADIUS = 5
    ROW_V_PAD = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._badge_font = QFont(get_token("typography.font_family_ui", "Arial"))
        self._badge_font.setPixelSize(10)
        self._aux_playback_provider: AuxPlaybackProvider | None = None
        self._peaks_cache: dict[str, list[float]] = {}

    def set_aux_playback_provider(self, provider: AuxPlaybackProvider | None) -> None:
        self._aux_playback_provider = provider

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
        return fm.height() + 2

    def _meta_widths(self, fm, *, row_count: int) -> tuple[int, int, int]:
        """Index, time, and BPM columns sized for the playlist font."""
        pad = self.META_PAD
        index_w = fm.horizontalAdvance(str(max(int(row_count), 1))) + pad
        time_w = fm.horizontalAdvance("0:00:00") + pad
        bpm_w = fm.horizontalAdvance("000.0") + pad
        return index_w, time_w, bpm_w

    def _note_badge_width(self, text: str) -> int:
        width = QFontMetrics(self._badge_font).horizontalAdvance(text) + self.META_PAD * 2
        return max(24, width)

    def _layout_columns(
        self,
        rect: QRect,
        badge_h: int,
        *,
        has_stems: bool,
        notes: list[str],
        show_wave: bool,
        fm,
        row_count: int,
    ) -> _ColumnLayout:
        inner_left = rect.left() + self.ROW_PAD_H
        inner_right = rect.right() - self.ROW_PAD_H
        top_band_h = badge_h + self.ROW_V_PAD * 2
        if show_wave:
            top_band = QRect(rect.left(), rect.top(), rect.width(), top_band_h)
        else:
            top_band = rect

        badge_y = top_band.top() + (top_band.height() - badge_h) // 2
        index_w, time_w, bpm_w = self._meta_widths(fm, row_count=row_count)

        index_rect = QRect(inner_left, badge_y, index_w, badge_h)

        bpm_rect = QRect(
            inner_right - bpm_w,
            badge_y,
            bpm_w,
            badge_h,
        )
        time_rect = QRect(
            bpm_rect.left() - self.COL_GAP - time_w,
            badge_y,
            time_w,
            badge_h,
        )
        cursor = time_rect.left() - self.COL_GAP
        stems_rect = None
        if has_stems:
            stems_rect = QRect(
                cursor - self.STEMS_BADGE_W,
                badge_y,
                self.STEMS_BADGE_W,
                badge_h,
            )
            cursor = stems_rect.left() - self.COL_GAP

        note_widths = [self._note_badge_width(text) for text in notes]
        notes_span = sum(note_widths) + self.COL_GAP * max(0, len(note_widths) - 1)
        note_left = cursor - notes_span
        note_rects: list[tuple[str, QRect]] = []
        for text, width in zip(notes, note_widths):
            note_rects.append((text, QRect(note_left, badge_y, width, badge_h)))
            note_left += width + self.COL_GAP

        name_right = note_rects[0][1].left() - self.COL_GAP if note_rects else cursor

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
            stems_rect=stems_rect,
            note_rects=tuple(note_rects),
            wave_rect=wave_rect,
        )

    def _shift(self, color: QColor, delta: int) -> QColor:
        return QColor(
            max(0, min(255, color.red() + delta)),
            max(0, min(255, color.green() + delta)),
            max(0, min(255, color.blue() + delta)),
        )

    def _color_wash(self, mark: QColor) -> QColor:
        """Dense dark wash so a track color reads as a solid row."""
        dark = self._color("bg_window", "#1a1c20")
        return QColor(
            (dark.red() * 3 + mark.red() * 2) // 5,
            (dark.green() * 3 + mark.green() * 2) // 5,
            (dark.blue() * 3 + mark.blue() * 2) // 5,
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
        if mark is not None and mark.isValid():
            base = self._color_wash(mark)
            if hovered:
                base = self._shift(base, 12)
        elif hovered:
            base = self._color("bg_item_hover", "#243044")
        elif row % 2 == 0:
            base = self._color("bg_surface", "#1a2330")
        else:
            base = self._color("playlist_row_alt", "#161e28")
        if aux_playing:
            accent = self._color("accent", "#2ad4ea")
            base = QColor(
                (base.red() * 3 + accent.red()) // 4,
                (base.green() * 3 + accent.green()) // 4,
                (base.blue() * 3 + accent.blue()) // 4,
            )
        if selected:
            base = QColor("#008264")
        return base

    def _item_font(self, option: QStyleOptionViewItem, index) -> QFont:
        font = index.data(Qt.ItemDataRole.FontRole)
        if isinstance(font, QFont):
            return QFont(font)
        return QFont(option.font)

    def _editor_stylesheet(self, font: QFont, background: QColor) -> str:
        family = font.family().replace('"', "")
        if font.pointSize() > 0:
            size = f"{font.pointSize()}pt"
        elif font.pixelSize() > 0:
            size = f"{font.pixelSize()}px"
        else:
            size = "13pt"
        foreground = self._color("text_primary", "#e7eef6")
        return (
            "QLineEdit#playlistItemEditor {"
            f' font-size: {size};'
            f' font-family: "{family}";'
            f" background-color: {background.name()};"
            f" color: {foreground.name()};"
            " border: none;"
            " padding: 0;"
            " margin: 0;"
            "}"
        )

    def _editor_background(self, option: QStyleOptionViewItem, index) -> QColor:
        item = None
        widget = option.widget if option.widget is not None else self.parent()
        if widget is not None and hasattr(widget, "item"):
            item = widget.item(index.row())
        return self._row_background(
            index.row(),
            selected=bool(option.state & QStyle.StateFlag.State_Selected),
            hovered=bool(option.state & QStyle.StateFlag.State_MouseOver),
            mark=_color_from_index(index),
            aux_playing=self._aux_info_for_item(item) is not None,
        )

    def _is_editing_index(self, widget, index) -> bool:
        if widget is None or not hasattr(widget, "state"):
            return False
        if widget.state() != QAbstractItemView.State.EditingState:
            return False
        current = widget.currentIndex()
        return (
            current.isValid()
            and current.row() == index.row()
            and current.column() == index.column()
        )

    def _restore_item_font(self, index, font: QFont) -> None:
        widget = self.parent()
        if widget is None or not hasattr(widget, "item"):
            return
        item = widget.item(index.row())
        if item is not None:
            item.setFont(font)

    def _name_editor_rect(self, option: QStyleOptionViewItem, index) -> QRect:
        badge_h = self._badge_height(option)
        item = None
        widget = option.widget if option.widget is not None else self.parent()
        if widget is not None and hasattr(widget, "item"):
            item = widget.item(index.row())
        row_count = widget.count() if widget is not None and hasattr(widget, "count") else 1
        layout = self._layout_columns(
            option.rect,
            badge_h,
            has_stems=_has_stems_from_index(index),
            notes=_notes_from_index(index),
            show_wave=self._aux_info_for_item(item) is not None,
            fm=option.fontMetrics,
            row_count=row_count,
        )
        name_rect = QRect(layout.name_rect)
        if layout.wave_rect is not None:
            name_rect.setHeight(max(badge_h, name_rect.height() - _MINI_WAVE_H - 2))
        return name_rect

    def createEditor(self, parent, option, index):
        self.initStyleOption(option, index)
        font = self._item_font(option, index)
        editor = QLineEdit(parent)
        editor.setObjectName("playlistItemEditor")
        editor.setFont(font)
        editor.setStyleSheet(self._editor_stylesheet(font, self._editor_background(option, index)))
        editor.setAutoFillBackground(True)
        editor.setFrame(False)
        editor.setTextMargins(0, 0, 0, 0)
        editor.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        editor.setProperty("_playlistItemFontFamily", font.family())
        editor.setProperty("_playlistItemFontPt", font.pointSize())
        editor.setProperty("_playlistItemFontPx", font.pixelSize())
        return editor

    def _font_saved_on_editor(self, editor) -> QFont | None:
        family = editor.property("_playlistItemFontFamily")
        if not isinstance(family, str) or not family:
            return None
        point_size = editor.property("_playlistItemFontPt")
        pixel_size = editor.property("_playlistItemFontPx")
        font = QFont(family)
        if isinstance(point_size, int) and point_size > 0:
            font.setPointSize(point_size)
        elif isinstance(pixel_size, int) and pixel_size > 0:
            font.setPixelSize(pixel_size)
        return font

    def updateEditorGeometry(self, editor, option, index):
        self.initStyleOption(option, index)
        rect = self._name_editor_rect(option, index)
        editor.setGeometry(rect)
        text_h = option.fontMetrics.height()
        vpad = max(0, (rect.height() - text_h) // 2)
        editor.setTextMargins(0, vpad, 0, vpad)

    def setModelData(self, editor, model, index):
        font = self._font_saved_on_editor(editor)
        if font is None:
            raw = index.data(Qt.ItemDataRole.FontRole)
            font = QFont(raw) if isinstance(raw, QFont) else None
        super().setModelData(editor, model, index)
        if font is not None:
            self._restore_item_font(index, font)

    def destroyEditor(self, editor, index):
        font = self._font_saved_on_editor(editor)
        super().destroyEditor(editor, index)
        if font is not None:
            self._restore_item_font(index, font)

    def _draw_badge(
        self,
        painter: QPainter,
        rect: QRect,
        text: str,
        *,
        bg: QColor,
        fg: QColor,
        font: QFont | None = None,
    ) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, self.BADGE_RADIUS, self.BADGE_RADIUS)
        painter.setPen(fg)
        painter.setFont(font or self._badge_font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def _wave_parts(self, rect: QRect) -> tuple[QRect, QRect]:
        """Stop button on the left, scrubbable wave on the right."""
        stop = QRect(rect.left() + 2, rect.top() + 2, 14, max(8, rect.height() - 4))
        seek = QRect(
            stop.right() + 3,
            rect.top(),
            max(0, rect.right() - (stop.right() + 3)),
            rect.height(),
        )
        return stop, seek

    def _option_for_row(self, playlist, index):
        option = QStyleOptionViewItem()
        playlist.initViewItemOption(option)
        option.rect = playlist.visualRect(index)
        self.initStyleOption(option, index)
        return option

    def _layout_for_row(self, playlist, index, option):
        item = playlist.item(index.row())
        aux = self._aux_info_for_item(item)
        layout = self._layout_columns(
            option.rect,
            self._badge_height(option),
            has_stems=_has_stems_from_index(index),
            notes=_notes_from_index(index),
            show_wave=aux is not None,
            fm=option.fontMetrics,
            row_count=playlist.count() if hasattr(playlist, "count") else 1,
        )
        return layout, item, aux

    def aux_interaction(self, playlist, pos: QPoint):
        """Hit-test a playing sample row. Returns ('stop'|'seek', item, ratio)."""
        index = playlist.indexAt(pos)
        if not index.isValid():
            return None
        option = self._option_for_row(playlist, index)
        layout, item, aux = self._layout_for_row(playlist, index, option)
        if item is None or aux is None or layout.wave_rect is None:
            return None
        stop, seek = self._wave_parts(layout.wave_rect)
        if stop.contains(pos):
            return ("stop", item, 0.0)
        if seek.contains(pos) and seek.width() > 0:
            ratio = (pos.x() - seek.left()) / float(seek.width())
            return ("seek", item, max(0.0, min(1.0, ratio)))
        return None

    def aux_seek_ratio(self, playlist, item, pos: QPoint) -> float | None:
        row = playlist.row(item) if item is not None else -1
        if row < 0:
            return None
        index = playlist.model().index(row, 0)
        if not index.isValid():
            return None
        option = self._option_for_row(playlist, index)
        layout, _item, aux = self._layout_for_row(playlist, index, option)
        if aux is None or layout.wave_rect is None:
            return None
        _stop, seek = self._wave_parts(layout.wave_rect)
        if seek.width() <= 0:
            return None
        ratio = (pos.x() - seek.left()) / float(seek.width())
        return max(0.0, min(1.0, ratio))

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

        stop, seek = self._wave_parts(rect)
        track_bg = QColor(255, 255, 255, 28 if selected else 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_bg)
        painter.drawRoundedRect(rect, 3, 3)

        painter.setBrush(QColor(196, 92, 92, 210))
        painter.drawRoundedRect(stop, 2, 2)
        painter.setBrush(QColor("#1a1c20"))
        glyph = stop.adjusted(4, 4, -4, -4)
        painter.drawRect(glyph)

        if seek.width() > 0:
            mid_y = seek.center().y()
            amp = max(2, seek.height() // 2 - 2)
            n = max(1, len(peaks))
            bar_w = max(1.0, seek.width() / float(n))
            played_x = seek.left() + int(seek.width() * max(0.0, min(1.0, progress)))
            played = QColor("#e0a15a")
            rest = QColor(played)
            rest.setAlpha(90 if selected else 70)
            for i, peak in enumerate(peaks):
                h = max(1, int(abs(float(peak)) * amp))
                x = int(seek.left() + i * bar_w)
                w = max(1, int(bar_w) - 1)
                color = played if x + w <= played_x else rest
                painter.fillRect(x, mid_y - h, w, h * 2, color)
            painter.setPen(QPen(played, 1))
            painter.drawLine(played_x, seek.top() + 1, played_x, seek.bottom() - 1)

        label = str(channel_name or "CH")
        chip_w = max(28, painter.fontMetrics().horizontalAdvance(label) + 8)
        chip = QRect(rect.right() - chip_w - 2, rect.top() + 1, chip_w, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawRoundedRect(chip, 3, 3)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(self._badge_font)
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), label)

        painter.restore()

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
        row_count = widget.count() if widget is not None and hasattr(widget, "count") else row + 1
        item_font = self._item_font(option, index)
        layout = self._layout_columns(
            option.rect,
            badge_h,
            has_stems=has_stems,
            notes=_notes_from_index(index),
            show_wave=show_wave,
            fm=option.fontMetrics,
            row_count=row_count,
        )

        painter.save()
        painter.setClipRect(option.rect)
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

        clear = QColor(0, 0, 0, 0)
        plain_fg = self._color("text_secondary", "#c5d0dc")
        index_text = str(row + 1)
        duration_text = format_time_ms(_duration_from_index(index))
        bpm_value = _bpm_from_index(index)
        bpm_text = _format_bpm(bpm_value) if bpm_value is not None else "—"

        self._draw_badge(
            painter,
            layout.index_rect,
            index_text,
            bg=clear,
            fg=plain_fg,
            font=item_font,
        )
        accent_bg = self._color("accent", "#2ad4ea")
        accent_fg = self._color("text_on_accent", "#0c1218")
        for note_text, note_rect in layout.note_rects:
            self._draw_badge(
                painter,
                note_rect,
                note_text,
                bg=accent_bg,
                fg=accent_fg,
            )
        if layout.stems_rect is not None:
            self._draw_badge(
                painter,
                layout.stems_rect,
                "stems",
                bg=accent_bg,
                fg=accent_fg,
            )
        self._draw_badge(
            painter,
            layout.time_rect,
            duration_text,
            bg=clear,
            fg=plain_fg,
            font=item_font,
        )
        self._draw_badge(
            painter,
            layout.bpm_rect,
            bpm_text,
            bg=clear,
            fg=self._color("accent", "#2ad4ea"),
            font=item_font,
        )

        if layout.name_rect.width() > 0 and not self._is_editing_index(widget, index):
            painter.setFont(item_font)
            placeholder = is_item_placeholder(item)
            if placeholder:
                painter.setPen(self._color("text_muted", "#7d8b9c"))
            elif is_item_file_missing(item):
                painter.setPen(self._color("accent_red", "#c45c5c"))
            else:
                painter.setPen(self._color("text_primary", "#e7eef6"))

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

        return QSize(max(width, 0), height)
