"""Wrapping layout: items flow left-to-right and move to the next line when needed."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QFrame, QLayout, QLayoutItem, QSizePolicy, QStyle, QWidget


class FlowFrame(QFrame):
    """Frame that grows vertically when a height-for-width layout wraps."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth():
            return layout.heightForWidth(width)
        return super().heightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802
        base = super().sizeHint()
        width = self.width() if self.width() > 0 else base.width()
        return QSize(base.width(), max(base.height(), self.heightForWidth(width)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        base = super().minimumSizeHint()
        width = self.width() if self.width() > 0 else base.width()
        return QSize(base.width(), self.heightForWidth(width))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


class FlowLayout(QLayout):
    """Pack widgets into rows, wrapping when the available width is too small.

    Expanding spacers (min width 0) absorb leftover space on their row so a
    trailing widget can stay right-aligned until it has to wrap.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = -1):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._hspace = spacing
        self._vspace = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._row_size(preferred=True)

    def minimumSize(self) -> QSize:  # noqa: N802
        return self._row_size(preferred=False)

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        self._hspace = spacing
        self._vspace = spacing
        self.invalidate()

    def setHorizontalSpacing(self, spacing: int) -> None:  # noqa: N802
        self._hspace = spacing
        self.invalidate()

    def setVerticalSpacing(self, spacing: int) -> None:  # noqa: N802
        self._vspace = spacing
        self.invalidate()

    def horizontalSpacing(self) -> int:  # noqa: N802
        if self._hspace >= 0:
            return self._hspace
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutHorizontalSpacing)

    def verticalSpacing(self) -> int:  # noqa: N802
        if self._vspace >= 0:
            return self._vspace
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutVerticalSpacing)

    def _smart_spacing(self, metric: QStyle.PixelMetric) -> int:
        parent = self.parent()
        if parent is None:
            return 0
        if parent.isWidgetType():
            return parent.style().pixelMetric(metric, None, parent)
        return parent.spacing()

    @staticmethod
    def _is_flex(item: QLayoutItem) -> bool:
        widget = item.widget()
        if widget is None:
            return False
        policy = widget.sizePolicy().horizontalPolicy()
        return policy in (
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )

    def _row_size(self, *, preferred: bool) -> QSize:
        space_x = max(0, self.horizontalSpacing())
        width = 0
        height = 0
        seen = 0
        for item in self._items:
            if self._is_flex(item):
                continue
            chunk = item.sizeHint() if preferred else item.minimumSize()
            if preferred:
                if seen:
                    width += space_x
                width += chunk.width()
                height = max(height, chunk.height())
                seen += 1
            else:
                # Shrink to one (widest) item; height-for-width grows when wrapped.
                width = max(width, chunk.width())
                height = max(height, chunk.height())
        left, top, right, bottom = self.getContentsMargins()
        return QSize(width + left + right, height + top + bottom)

    def _item_width(self, item: QLayoutItem, avail: int) -> int:
        if self._is_flex(item):
            return 0
        hint = item.sizeHint().width()
        mini = item.minimumSize().width()
        return max(mini, min(hint, max(0, avail)))

    def _item_height(self, item: QLayoutItem, width: int) -> int:
        widget = item.widget()
        if widget is not None and widget.hasHeightForWidth() and width > 0:
            return widget.heightForWidth(width)
        return max(item.sizeHint().height(), item.minimumSize().height())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        avail = max(0, effective.width())
        space_x = max(0, self.horizontalSpacing())
        space_y = max(0, self.verticalSpacing())

        rows: list[list[tuple[QLayoutItem, int]]] = []
        row: list[tuple[QLayoutItem, int]] = []
        row_fixed = 0

        for item in self._items:
            if self._is_flex(item):
                row.append((item, 0))
                continue
            width = self._item_width(item, avail)
            extra = space_x if row else 0
            if row and row_fixed + extra + width > avail:
                rows.append(row)
                row = []
                row_fixed = 0
                extra = 0
            row_fixed += extra + width
            row.append((item, width))
        if row:
            rows.append(row)

        y = effective.y()
        for row in rows:
            nonflex = [(item, width) for item, width in row if not self._is_flex(item)]
            spacings = space_x * max(0, len(nonflex) - 1)
            leftover = max(0, avail - sum(width for _item, width in nonflex) - spacings)
            flex_n = sum(1 for item, _w in row if self._is_flex(item))
            flex_w = 0
            if flex_n and leftover > 0:
                extra_gaps = space_x * flex_n
                flex_w = max(0, leftover - extra_gaps) // flex_n

            placed_items: list[tuple[QLayoutItem, int]] = []
            line_h = 0
            for item, width in row:
                placed = flex_w if self._is_flex(item) else width
                if self._is_flex(item) and placed <= 0:
                    continue
                placed_items.append((item, placed))
                if not self._is_flex(item):
                    line_h = max(line_h, self._item_height(item, placed))

            if not test_only:
                x = effective.x()
                for index, (item, placed) in enumerate(placed_items):
                    if index:
                        x += space_x
                    height = line_h if self._is_flex(item) else self._item_height(item, placed)
                    item.setGeometry(QRect(QPoint(x, y), QSize(max(0, placed), max(0, height))))
                    x += placed
            y += line_h + space_y

        total = y - space_y - effective.y() if rows else 0
        return total + top + bottom
