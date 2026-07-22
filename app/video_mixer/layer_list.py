"""Layer list with playlist-style drag-and-drop reorder."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QWidget,
)

from app.ui.tokens import get_token

LAYER_MIME = "application/x-vm-layer"


class _DropLineOverlay(QWidget):
    MARGIN = 8

    def __init__(self, list_widget: "LayerListWidget"):
        super().__init__(list_widget.viewport())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def show_at(self, y: int) -> None:
        viewport = self.parentWidget()
        if viewport is None:
            return
        self.setGeometry(0, y - 1, viewport.width(), 3)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(get_token("colors.accent", "#3897fd"))
        center_y = self.height() // 2
        painter.setPen(QPen(color, 2))
        painter.drawLine(self.MARGIN, center_y, self.width() - self.MARGIN, center_y)
        dot = 3
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.MARGIN - dot, center_y - dot, dot * 2, dot * 2)
        painter.drawEllipse(self.width() - self.MARGIN - dot, center_y - dot, dot * 2, dot * 2)
        painter.end()


class LayerItemDelegate(QStyledItemDelegate):
    ROW_H = 24
    EYE_W = 22
    INDEX_W = 18
    MUTE_W = 22
    DEL_W = 22

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        rect = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        bg = QColor(get_token("colors.bg_card", "#2a2a2a"))
        painter.fillRect(rect, bg)
        if selected:
            painter.setPen(QPen(QColor(get_token("colors.text_primary", "#ffffff")), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

        visible = bool(index.data(Qt.ItemDataRole.UserRole + 1))
        is_muted = bool(index.data(Qt.ItemDataRole.UserRole + 2))
        is_video = bool(index.data(Qt.ItemDataRole.UserRole + 3))
        file_missing = bool(index.data(Qt.ItemDataRole.UserRole + 4))
        name = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        row_num = index.row() + 1

        text_color = QColor(get_token("colors.text_primary", "#e0e0e0"))
        muted = QColor(get_token("colors.text_muted", "#888888"))
        secondary = QColor(get_token("colors.text_secondary", "#b0b0b0"))
        accent = QColor(get_token("colors.accent", "#3897fd"))
        missing_color = QColor(get_token("colors.accent_red", "#e45656"))

        eye_rect = QRect(rect.left() + 2, rect.top(), self.EYE_W, rect.height())
        painter.setPen(secondary)
        painter.drawText(eye_rect, Qt.AlignmentFlag.AlignCenter, "●" if visible else "○")

        idx_rect = QRect(eye_rect.right(), rect.top(), self.INDEX_W, rect.height())
        painter.setPen(muted)
        painter.drawText(idx_rect, Qt.AlignmentFlag.AlignCenter, str(row_num))

        del_rect = QRect(rect.right() - self.DEL_W, rect.top(), self.DEL_W, rect.height())
        painter.setPen(muted)
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(del_rect, Qt.AlignmentFlag.AlignCenter, "×")

        mute_rect = QRect(del_rect.left() - self.MUTE_W, rect.top(), self.MUTE_W, rect.height())
        if is_video:
            painter.setFont(option.font)
            painter.setPen(muted if is_muted else accent)
            painter.drawText(mute_rect, Qt.AlignmentFlag.AlignCenter, "♪")

        name_rect = QRect(
            idx_rect.right() + 4,
            rect.top(),
            max(0, mute_rect.left() - idx_rect.right() - 8),
            rect.height(),
        )
        painter.setPen(missing_color if file_missing else text_color)
        painter.setFont(option.font)
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, name_rect.width()),
        )
        painter.restore()


class LayerListWidget(QListWidget):
    """Playlist-style layer list: drop line, ghost pixmap, hide-source reorder."""

    layerSelected = pyqtSignal(str)
    visibilityToggled = pyqtSignal(str, bool)
    muteToggled = pyqtSignal(str, bool)
    deleteRequested = pyqtSignal(str)
    reordered = pyqtSignal(list)  # layer ids top→bottom (UI order)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("layerList")
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setItemDelegate(LayerItemDelegate(self))
        self._drop_indicator_row: int | None = None
        self._drop_indicator_y_px: int | None = None
        self._drop_overlay = _DropLineOverlay(self)
        self._suppress_select = False
        self._dragging = False
        self.viewport().installEventFilter(self)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def is_dragging(self) -> bool:
        return self._dragging

    def eventFilter(self, obj, event):
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            if self._drop_indicator_y_px is not None:
                self._drop_overlay.show_at(self._drop_indicator_y_px)
        return super().eventFilter(obj, event)

    def _cursor_viewport_pos(self) -> QPoint:
        return self.viewport().mapFromGlobal(QCursor.pos())

    def _clear_drop_indicator(self) -> None:
        if self._drop_indicator_row is not None or self._drop_indicator_y_px is not None:
            self._drop_indicator_row = None
            self._drop_indicator_y_px = None
            self._drop_overlay.hide()

    def _drop_y_for_row(self, row: int) -> int:
        if self.count() == 0:
            return 4
        if row >= self.count():
            last = self.item(self.count() - 1)
            if last is None:
                return 4
            return self.visualItemRect(last).bottom()
        item = self.item(row)
        if item is None:
            return 4
        return self.visualItemRect(item).top()

    def _drop_row_at_viewport(self, drop_point: QPoint) -> int:
        target = self.itemAt(drop_point)
        if target is None:
            return self.count()
        row = self.row(target)
        if drop_point.y() > self.visualItemRect(target).center().y():
            row += 1
        return max(0, min(row, self.count()))

    def _update_drop_indicator(self) -> None:
        vp = self._cursor_viewport_pos()
        row = self._drop_row_at_viewport(vp)
        y = self._drop_y_for_row(row)
        self._drop_indicator_row = row
        self._drop_indicator_y_px = y
        self._drop_overlay.show_at(y)

    def drop_insert_row(self) -> int:
        if self._drop_indicator_row is not None:
            return self._drop_indicator_row
        return self._drop_row_at_viewport(self._cursor_viewport_pos())

    def layer_ids_top_to_bottom(self) -> list[str]:
        ids: list[str] = []
        for i in range(self.count()):
            item = self.item(i)
            if item is None or item.isHidden():
                continue
            lid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(lid, str):
                ids.append(lid)
        return ids

    def set_layers(
        self,
        layers: list[tuple[str, str, bool, bool, bool, bool]],
        selected_id: str | None,
    ) -> None:
        """layers: (id, name, visible, muted, is_video, file_missing) top→bottom."""
        self._suppress_select = True
        self.clear()
        select_row = -1
        for i, (layer_id, name, visible, muted, is_video, file_missing) in enumerate(layers):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, layer_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, visible)
            item.setData(Qt.ItemDataRole.UserRole + 2, muted)
            item.setData(Qt.ItemDataRole.UserRole + 3, is_video)
            item.setData(Qt.ItemDataRole.UserRole + 4, file_missing)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
            self.addItem(item)
            if layer_id == selected_id:
                select_row = i
        if select_row >= 0:
            self.setCurrentRow(select_row)
        self._suppress_select = False

    def set_selected_layer(self, layer_id: str | None) -> None:
        self._suppress_select = True
        if not layer_id:
            self.clearSelection()
            self._suppress_select = False
            return
        for i in range(self.count()):
            item = self.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == layer_id:
                self.setCurrentRow(i)
                break
        self._suppress_select = False

    def _on_selection_changed(self) -> None:
        if self._suppress_select:
            return
        item = self.currentItem()
        if item is None:
            return
        lid = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(lid, str):
            self.layerSelected.emit(lid)

    def _hit_zone(self, pos: QPoint) -> tuple[str | None, str | None]:
        """Return (zone, layer_id) where zone is eye|mute|delete|row."""
        item = self.itemAt(pos)
        if item is None:
            return None, None
        lid = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(lid, str):
            return None, None
        rect = self.visualItemRect(item)
        x = pos.x() - rect.left()
        if x <= LayerItemDelegate.EYE_W + 4:
            return "eye", lid
        if x >= rect.width() - LayerItemDelegate.DEL_W - 2:
            return "delete", lid
        mute_left = rect.width() - LayerItemDelegate.DEL_W - LayerItemDelegate.MUTE_W - 2
        if x >= mute_left and bool(item.data(Qt.ItemDataRole.UserRole + 3)):
            return "mute", lid
        return "row", lid

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            zone, lid = self._hit_zone(event.position().toPoint())
            if zone == "eye" and lid:
                item = self.itemAt(event.position().toPoint())
                if item is not None:
                    visible = not bool(item.data(Qt.ItemDataRole.UserRole + 1))
                    item.setData(Qt.ItemDataRole.UserRole + 1, visible)
                    self.visibilityToggled.emit(lid, visible)
                    self.viewport().update()
                event.accept()
                return
            if zone == "mute" and lid:
                item = self.itemAt(event.position().toPoint())
                if item is not None:
                    muted = not bool(item.data(Qt.ItemDataRole.UserRole + 2))
                    item.setData(Qt.ItemDataRole.UserRole + 2, muted)
                    self.muteToggled.emit(lid, muted)
                    self.viewport().update()
                event.accept()
                return
            if zone == "delete" and lid:
                self.deleteRequested.emit(lid)
                event.accept()
                return
        super().mousePressEvent(event)

    def _make_drag_pixmap(self, row: int) -> tuple[QPixmap, QRect]:
        item = self.item(row)
        rect = self.visualItemRect(item) if item else QRect()
        size = rect.size()
        if size.isEmpty():
            size = QSize(self.viewport().width(), LayerItemDelegate.ROW_H)

        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.initFrom(self)
        option.rect = QRect(0, 0, size.width(), size.height())
        option.state |= QStyle.StateFlag.State_Enabled
        if self.currentRow() == row:
            option.state |= QStyle.StateFlag.State_Selected
        self.itemDelegate().paint(painter, option, self.model().index(row, 0))
        painter.end()
        return pixmap, rect

    def startDrag(self, supportedActions) -> None:
        indexes = self.selectedIndexes()
        if not indexes:
            return
        row = indexes[0].row()
        item = self.item(row)
        if item is None:
            return
        lid = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(lid, str):
            return

        mime = QMimeData()
        mime.setData(LAYER_MIME, lid.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)

        pixmap, rect = self._make_drag_pixmap(row)
        if not pixmap.isNull() and not rect.isEmpty():
            cursor_vp = self._cursor_viewport_pos()
            hot = cursor_vp - rect.topLeft()
            hot.setX(max(0, min(hot.x(), rect.width() - 1)))
            hot.setY(max(0, min(hot.y(), rect.height() - 1)))
            drag.setPixmap(pixmap)
            drag.setHotSpot(hot)

        self._dragging = True
        try:
            item.setHidden(True)
            self.viewport().update()
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._dragging = False
            # Item may have been takeItem'd / list rebuilt during drop → never touch stale ref
            self._unhide_by_id(lid)
            self.viewport().update()
            self._clear_drop_indicator()

    def _unhide_by_id(self, layer_id: str) -> None:
        for i in range(self.count()):
            it = self.item(i)
            if it is None:
                continue
            try:
                if it.data(Qt.ItemDataRole.UserRole) == layer_id:
                    it.setHidden(False)
            except RuntimeError:
                continue

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(LAYER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not event.mimeData().hasFormat(LAYER_MIME):
            event.ignore()
            return
        self._update_drop_indicator()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._clear_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        target_row = self.drop_insert_row()
        self._clear_drop_indicator()
        if not event.mimeData().hasFormat(LAYER_MIME):
            event.ignore()
            return

        source_id = bytes(event.mimeData().data(LAYER_MIME)).decode("utf-8")
        source_row = -1
        for i in range(self.count()):
            it = self.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == source_id:
                source_row = i
                break
        if source_row < 0:
            event.ignore()
            return
        if target_row in (source_row, source_row + 1):
            event.acceptProposedAction()
            return

        item = self.takeItem(source_row)
        if item is None:
            return
        if source_row < target_row:
            target_row -= 1
        target_row = max(0, min(target_row, self.count()))
        self.insertItem(target_row, item)
        item.setHidden(False)
        self.setCurrentItem(item)
        self.reordered.emit(self.layer_ids_top_to_bottom())
        event.acceptProposedAction()
