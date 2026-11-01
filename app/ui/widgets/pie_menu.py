"""Marking menu: hold the key, aim with the mouse, release to choose."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QKeyEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from app.ui.tokens import get_token

# Disc stays put. Extra margin around it holds the submenu list.
_WIDGET = 720
_OUTER = 200.0
_INNER = 54.0
_LABEL_R = 132.0
_LIST_W = 148.0
_ROW_H = 32.0


@dataclass
class PieItem:
    label: str
    detail: str = ""
    enabled: bool = True
    current: bool = False
    submenu: str | None = None
    on_click: Callable[[], None] | None = None


@dataclass
class PiePage:
    center: str = ""
    items: list[PieItem] = field(default_factory=list)
    back: bool = False


def pie_hit(
    dx: float,
    dy: float,
    count: int,
    inner: float = _INNER,
    outer: float = _OUTER,
    *,
    unbounded: bool = False,
) -> tuple[str, int]:
    """Classify a point relative to the menu center.

    Returns ``("center", -1)``, ``("slice", index)``, or ``("outside", -1)``.
    Slice 0 is at the top; indexes continue clockwise. With ``unbounded``,
    direction still selects a slice past the disc, the way a marking menu does.
    """
    dist = math.hypot(dx, dy)
    if dist < inner:
        return "center", -1
    if count <= 0 or (not unbounded and dist > outer):
        return "outside", -1
    clockwise = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
    step = (2 * math.pi) / count
    return "slice", int(clockwise / step + 0.5) % count


def _qcolor(token: str, fallback: str) -> QColor:
    return QColor(get_token(token, fallback))


class PieMenu(QWidget):
    """Hold-to-aim radial menu. Release the key on a slice to run it."""

    closed = pyqtSignal()

    def __init__(self, provider: Callable[[], dict[str, PiePage]], parent: QWidget | None = None):
        super().__init__(parent)
        self._provider = provider
        self._pages: dict[str, PiePage] = {}
        self._path: list[str] = ["root"]
        self._hover = -1
        self._center_hover = False
        self._open_submenu = -1
        self._list_hover = -1
        self._shortcut_key = int(Qt.Key.Key_QuoteLeft)
        self._emitting_close = False
        self._closing = False
        self._track = QTimer(self)
        self._track.setInterval(16)
        self._track.timeout.connect(self._track_cursor)

        self.setFixedSize(_WIDGET, _WIDGET)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )

    def set_shortcut_key(self, key: int) -> None:
        self._shortcut_key = int(key)

    def open_at(self, global_pos: QPoint) -> None:
        self._closing = False
        self._path = ["root"]
        self._hover = -1
        self._center_hover = False
        self._open_submenu = -1
        self._list_hover = -1
        self._reload()
        self._move_centered(global_pos)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._track.start()

    def confirm(self) -> None:
        """Run the aimed leaf, then close. Center and submenus do not run."""
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._track.stop()
        try:
            chosen = self._aimed_leaf()
            if chosen is not None and chosen.enabled and chosen.on_click is not None:
                chosen.on_click()
        finally:
            self._hover = -1
            self._open_submenu = -1
            self._list_hover = -1
            self.hide()
            self._closing = False

    def cancel(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._track.stop()
        self._hover = -1
        self._open_submenu = -1
        self._list_hover = -1
        self.hide()
        self._closing = False

    def _move_centered(self, global_pos: QPoint) -> None:
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        x = int(global_pos.x() - self.width() / 2)
        y = int(global_pos.y() - self.height() / 2)
        x = max(geo.left(), min(x, geo.right() - self.width() + 1))
        y = max(geo.top(), min(y, geo.bottom() - self.height() + 1))
        self.move(x, y)

    def _reload(self) -> None:
        self._pages = self._provider()
        if "root" not in self._pages:
            self._path = ["root"]
        else:
            while len(self._path) > 1 and self._path[-1] not in self._pages:
                self._path.pop()
            if not self._path:
                self._path = ["root"]
        self.update()

    def _page(self) -> PiePage | None:
        if not self._path:
            return None
        return self._pages.get(self._path[-1])

    def _hit_at(self, pos: QPointF) -> tuple[str, int]:
        page = self._page()
        count = len(page.items) if page is not None else 0
        return pie_hit(
            pos.x() - self.width() / 2,
            pos.y() - self.height() / 2,
            count,
            unbounded=True,
        )

    def _track_cursor(self) -> None:
        if self.isVisible():
            self.update_from_global(QCursor.pos())

    def _aimed_leaf(self) -> PieItem | None:
        page = self._page()
        if page is None:
            return None
        if self._open_submenu >= 0 and self._list_hover >= 0:
            parent = page.items[self._open_submenu]
            children = self._submenu_items(parent)
            if 0 <= self._list_hover < len(children):
                return children[self._list_hover]
            return None
        if 0 <= self._hover < len(page.items):
            item = page.items[self._hover]
            if item.submenu:
                return None
            return item
        return None

    def _submenu_items(self, item: PieItem) -> list[PieItem]:
        if not item.submenu:
            return []
        page = self._pages.get(item.submenu)
        if page is None:
            return []
        return page.items

    def _list_rect(self, index: int, count: int, rows: int) -> QRectF:
        center = self.width() / 2
        theta = -math.pi / 2 + index * (2 * math.pi / count)
        anchor_x = center + (_OUTER + 10) * math.cos(theta)
        anchor_y = center + (_OUTER + 10) * math.sin(theta)
        height = max(1, rows) * _ROW_H
        cos_t = math.cos(theta)
        if abs(cos_t) < 0.4:
            x = anchor_x - _LIST_W / 2
        elif cos_t > 0:
            x = anchor_x
        else:
            x = anchor_x - _LIST_W
        y = anchor_y - height / 2
        rect = QRectF(x, y, _LIST_W, height)
        margin = 8.0
        limit = self.width() - margin
        if rect.left() < margin:
            rect.moveLeft(margin)
        if rect.right() > limit:
            rect.moveRight(limit)
        if rect.top() < margin:
            rect.moveTop(margin)
        if rect.bottom() > limit:
            rect.moveBottom(limit)
        return rect

    def _list_row_at(self, local: QPointF) -> int | None:
        page = self._page()
        if page is None or self._open_submenu < 0 or self._open_submenu >= len(page.items):
            return None
        children = self._submenu_items(page.items[self._open_submenu])
        if not children:
            return None
        rect = self._list_rect(self._open_submenu, len(page.items), len(children))
        if not rect.adjusted(-6, -6, 6, 6).contains(local):
            return None
        if not rect.contains(local):
            return -1
        row = int((local.y() - rect.top()) / _ROW_H)
        if 0 <= row < len(children):
            return row
        return -1

    def update_from_global(self, global_pos: QPoint) -> None:
        if self._closing or not self.isVisible():
            return
        local = QPointF(self.mapFromGlobal(global_pos))
        list_row = self._list_row_at(local)
        if list_row is not None:
            hover = self._open_submenu
            changed = (
                self._list_hover != list_row
                or self._hover != hover
                or self._center_hover
            )
            self._list_hover = list_row
            self._hover = hover
            self._center_hover = False
            if changed:
                self.update()
            return

        zone, index = self._hit_at(local)
        page = self._page()
        open_submenu = -1
        hover = -1
        if zone == "slice" and page is not None and 0 <= index < len(page.items):
            hover = index
            item = page.items[index]
            if item.enabled and item.submenu and item.submenu in self._pages:
                open_submenu = index
        center = zone == "center"
        changed = (
            hover != self._hover
            or open_submenu != self._open_submenu
            or self._list_hover != -1
            or center != self._center_hover
        )
        self._hover = hover
        self._open_submenu = open_submenu
        self._list_hover = -1
        self._center_hover = center
        if changed:
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.update_from_global(event.globalPosition().toPoint())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        if int(event.key()) == self._shortcut_key:
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.isAutoRepeat():
            event.accept()
            return
        if int(event.key()) == self._shortcut_key:
            self.confirm()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._track.stop()
        super().hideEvent(event)
        if self._emitting_close:
            return
        self._emitting_close = True
        try:
            self.closed.emit()
        finally:
            self._emitting_close = False

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        page = self._page()
        items = page.items if page is not None else []

        disc = QColor(18, 19, 22, 238)
        border = QPen(_qcolor("colors.border", "#3a3c42"), 1.5)
        painter.setPen(border)
        painter.setBrush(disc)
        painter.drawEllipse(QRectF(
            center.x() - _OUTER, center.y() - _OUTER, _OUTER * 2, _OUTER * 2
        ))

        count = len(items)
        if count and 0 <= self._hover < count and items[self._hover].enabled:
            self._paint_slice(painter, center, self._hover, count)
            painter.setPen(border)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(
                center.x() - _OUTER, center.y() - _OUTER, _OUTER * 2, _OUTER * 2
            ))

        self._paint_center(painter, center, page)
        if count:
            self._paint_labels(painter, center, items)
        self._paint_submenu(painter, page)

    def _paint_slice(self, painter: QPainter, center: QPointF, index: int, count: int) -> None:
        span = int(round(360 * 16 / count))
        center_angle = int(90 * 16 - index * span)
        start = center_angle + span // 2
        color = _qcolor("colors.accent", "#3ec4d0")
        color.setAlpha(56)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPie(QRectF(
            center.x() - _OUTER, center.y() - _OUTER, _OUTER * 2, _OUTER * 2
        ), start, -span)
        # Cover the hole the pie would paint across the center button.
        painter.setBrush(QColor(18, 19, 22, 238))
        painter.drawEllipse(QRectF(
            center.x() - _INNER, center.y() - _INNER, _INNER * 2, _INNER * 2
        ))

    def _paint_center(self, painter: QPainter, center: QPointF, page: PiePage | None) -> None:
        fill = _qcolor("colors.bg_card", "#26282e")
        if self._center_hover and page is not None and page.back:
            fill = _qcolor("colors.bg_control_hover", "#3a3c44")
        painter.setPen(QPen(_qcolor("colors.border", "#3a3c42"), 1))
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(
            center.x() - _INNER + 6,
            center.y() - _INNER + 6,
            (_INNER - 6) * 2,
            (_INNER - 6) * 2,
        ))
        if page is None:
            return
        title = page.center.strip()
        if not title:
            return
        painter.setPen(_qcolor("colors.accent", "#3ec4d0"))
        font = QFont("Roboto")
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(center.x() - 46, center.y() - 12, 92, 24),
            int(Qt.AlignmentFlag.AlignCenter),
            title,
        )

    def _paint_labels(self, painter: QPainter, center: QPointF, items: list[PieItem]) -> None:
        count = len(items)
        label_font = QFont("Roboto")
        label_font.setPixelSize(12)
        label_font.setBold(True)
        detail_font = QFont("Roboto")
        detail_font.setPixelSize(11)
        primary = _qcolor("colors.text_primary", "#e2e2e4")
        muted = _qcolor("colors.text_muted", "#8c8c92")
        accent = _qcolor("colors.accent", "#3ec4d0")
        for index, item in enumerate(items):
            theta = -math.pi / 2 + index * (2 * math.pi / count)
            x = center.x() + _LABEL_R * math.cos(theta)
            y = center.y() + _LABEL_R * math.sin(theta)
            rect = QRectF(x - 62, y - 18, 124, 36)
            if not item.enabled:
                label_color = muted
                detail_color = muted
            elif item.current:
                label_color = accent
                detail_color = accent
            else:
                label_color = primary
                detail_color = accent if item.detail else primary
            if item.detail:
                painter.setFont(label_font)
                painter.setPen(label_color)
                painter.drawText(
                    QRectF(rect.x(), rect.y(), rect.width(), 16),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                    item.label,
                )
                painter.setFont(detail_font)
                painter.setPen(detail_color)
                painter.drawText(
                    QRectF(rect.x(), rect.y() + 16, rect.width(), 16),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                    item.detail,
                )
            else:
                painter.setFont(label_font)
                painter.setPen(label_color)
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), item.label)

    def _paint_submenu(self, painter: QPainter, page: PiePage | None) -> None:
        if page is None or self._open_submenu < 0 or self._open_submenu >= len(page.items):
            return
        parent = page.items[self._open_submenu]
        children = self._submenu_items(parent)
        if not children:
            return
        rect = self._list_rect(self._open_submenu, len(page.items), len(children))
        painter.setPen(QPen(_qcolor("colors.border", "#3a3c42"), 1))
        painter.setBrush(QColor(18, 19, 22, 242))
        painter.drawRoundedRect(rect, 6, 6)
        font = QFont("Roboto")
        font.setPixelSize(18)
        font.setBold(True)
        painter.setFont(font)
        primary = _qcolor("colors.text_primary", "#e2e2e4")
        muted = _qcolor("colors.text_muted", "#8c8c92")
        accent = _qcolor("colors.accent", "#3ec4d0")
        for row, child in enumerate(children):
            row_rect = QRectF(rect.left(), rect.top() + row * _ROW_H, rect.width(), _ROW_H)
            if row == self._list_hover and child.enabled:
                fill = QColor(accent)
                fill.setAlpha(56)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRect(row_rect.adjusted(3, 1, -3, -1))
            if not child.enabled:
                color = muted
            elif child.current or row == self._list_hover:
                color = accent
            else:
                color = primary
            painter.setPen(color)
            painter.drawText(
                row_rect.adjusted(10, 0, -8, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                child.label,
            )
