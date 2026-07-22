import os

from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint, QRect, QEvent
from PyQt6.QtGui import (
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QFocusEvent,
    QPainter,
    QPen,
    QColor,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QStyle,
    QStyleOptionViewItem,
    QWidget,
)

from app.audio_utils import is_audio_file
from app.playlist_io import (
    get_item_file_path,
    get_item_duration,
    set_item_duration,
    set_item_file_missing,
    set_item_file_path,
)
from app.time_utils import get_audio_duration_ms
from app.ui.tokens import get_token
from app.widgets.playlist_item_delegate import PlaylistItemDelegate

TRACK_MIME = "application/x-simpleaudioplayer-track"


class _DropLineOverlay(QWidget):
    MARGIN = 8

    def __init__(self, playlist: "PlaylistWidget"):
        super().__init__(playlist.viewport())
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

    def paintEvent(self, event):
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


class PlaylistWidget(QListWidget):
    """Виджет плейлиста с поддержкой drag & drop и сортировки"""
    itemDropped = pyqtSignal(int, int, int, int)  # from_playlist, from_row, to_playlist, to_row
    focused = pyqtSignal(int)
    selectionNavigated = pyqtSignal(object)
    tracksChanged = pyqtSignal()

    def __init__(self, name, playlist_num, parent=None):
        super().__init__(parent)
        self.playlist_num = playlist_num
        self._item_paths: dict[int, str] = {}
        self._playlists: list["PlaylistWidget"] | None = None
        self._drop_indicator_row: int | None = None
        self._drop_indicator_y_px: int | None = None
        self.duration_prober = None
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setObjectName("playlistList")
        self.setFont(QFont(get_token("typography.font_family_ui", "Arial"), 10))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(False)
        self.setUniformItemSizes(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setItemDelegate(PlaylistItemDelegate(self))
        self._drop_overlay = _DropLineOverlay(self)
        self.viewport().installEventFilter(self)
        self._reclaim_focus = False

    def set_reclaim_focus(self, enabled: bool) -> None:
        """When True, immediately take keyboard focus back if something steals it."""
        self._reclaim_focus = bool(enabled)
        focused = QApplication.focusWidget()
        if self._reclaim_focus and focused != self:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def focusOutEvent(self, event: QFocusEvent):
        super().focusOutEvent(event)
        if self._reclaim_focus:
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(0, self._maybe_reclaim_focus)

    def _maybe_reclaim_focus(self) -> None:
        if not self._reclaim_focus:
            return
        focused = QApplication.focusWidget()
        if focused == self:
            return
        if focused is not None and self.isAncestorOf(focused):
            return
        # Leave focus on real text/menu editors; everything else is stolen back.
        from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QMenu

        if isinstance(focused, (QLineEdit, QComboBox, QMenu, QAbstractSpinBox)):
            return
        if focused is not None and focused.window() != self.window():
            return
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _cursor_viewport_pos(self) -> QPoint:
        return self.viewport().mapFromGlobal(QCursor.pos())

    def eventFilter(self, obj, event):
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            if self._drop_indicator_y_px is not None:
                self._drop_overlay.show_at(self._drop_indicator_y_px)
        return super().eventFilter(obj, event)

    def set_playlist_registry(self, playlists: list["PlaylistWidget"]) -> None:
        self._playlists = playlists

    def _resolve_playlist(self, playlist_num: int) -> "PlaylistWidget | None":
        if self._playlists and 1 <= playlist_num <= len(self._playlists):
            return self._playlists[playlist_num - 1]
        return None

    def _clear_drop_indicator(self) -> None:
        if self._drop_indicator_row is not None or self._drop_indicator_y_px is not None:
            self._drop_indicator_row = None
            self._drop_indicator_y_px = None
            self._drop_overlay.hide()

    def _drop_y_for_row(self, row: int) -> int:
        if self.count() == 0:
            return 4
        if row >= self.count():
            last_item = self.item(self.count() - 1)
            if last_item is None:
                return 4
            return self.visualItemRect(last_item).bottom()
        item = self.item(row)
        if item is None:
            return 4
        return self.visualItemRect(item).top()

    def _update_drop_indicator(self, _pos) -> None:
        vp_pos = self._cursor_viewport_pos()
        row = self._drop_row_at_viewport(vp_pos)
        y = self._drop_y_for_row(row)
        self._drop_indicator_row = row
        self._drop_indicator_y_px = y
        self._drop_overlay.show_at(y)

    def _drop_row_at_viewport(self, drop_point: QPoint) -> int:
        target_item = self.itemAt(drop_point)
        if target_item is None:
            return self.count()
        target_row = self.row(target_item)
        if drop_point.y() > self.visualItemRect(target_item).center().y():
            target_row += 1
        return max(0, min(target_row, self.count()))

    def _drop_row_at(self, pos) -> int:
        return self._drop_row_at_viewport(self._cursor_viewport_pos())

    def _make_drag_pixmap(self, row: int) -> tuple[QPixmap, QRect]:
        rect = self.visualItemRect(self.item(row))
        size = rect.size()
        if size.isEmpty():
            option = QStyleOptionViewItem()
            option.initFrom(self)
            index = self.model().index(row, 0)
            size = self.itemDelegate().sizeHint(option, index)

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

    def drop_insert_row(self) -> int:
        if self._drop_indicator_row is not None:
            return self._drop_indicator_row
        return self._drop_row_at_viewport(self._cursor_viewport_pos())

    def _remember_item_path(self, item: QListWidgetItem, file_path: str) -> None:
        path = os.path.abspath(file_path)
        set_item_file_path(item, path)
        self._item_paths[id(item)] = path
        set_item_file_missing(item, not os.path.isfile(path))

    def update_item_path(self, item: QListWidgetItem, file_path: str) -> None:
        """Retarget an existing playlist item to a new file path."""
        self._remember_item_path(item, file_path)
        self.viewport().update()

    def _adopt_item(self, item: QListWidgetItem) -> None:
        path = get_item_file_path(item)
        if path:
            self._item_paths[id(item)] = path

    def _apply_item_duration(self, item: QListWidgetItem, file_path: str) -> None:
        duration_ms = get_audio_duration_ms(file_path)
        if duration_ms is not None:
            set_item_duration(item, duration_ms)

    def set_playlist_font(self, font: QFont) -> None:
        self.setFont(font)
        for row in range(self.count()):
            item = self.item(row)
            if item:
                item.setFont(font)
        self.viewport().update()

    def _request_duration_probe(self, item: QListWidgetItem, file_path: str) -> None:
        if get_item_duration(item) is not None:
            self.viewport().update()
            return
        if not os.path.isfile(file_path):
            self.viewport().update()
            return
        if self.duration_prober is not None:
            self.duration_prober.probe_item(item, file_path)
        self.viewport().update()

    def add_item_with_path(
        self,
        file_path: str,
        display_name: str | None = None,
        *,
        load_duration: bool = True,
        file_exists: bool | None = None,
        row: int | None = None,
    ) -> QListWidgetItem:
        name = display_name or os.path.basename(file_path)
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setFont(self.font())
        self._remember_item_path(item, file_path)
        if file_exists is not None:
            set_item_file_missing(item, not file_exists)
        if row is None:
            self.addItem(item)
        else:
            insert_at = max(0, min(row, self.count()))
            self.insertItem(insert_at, item)
        if load_duration:
            self._apply_item_duration(item, file_path)
        self._request_duration_probe(item, file_path)
        return item

    def file_path_at(self, row: int) -> str | None:
        item = self.item(row)
        if item is None:
            return None

        path = self._item_paths.get(id(item))
        if path:
            return path

        path = get_item_file_path(item)
        if path:
            self._item_paths[id(item)] = path
        return path

    def all_file_paths(self) -> list[str]:
        paths = []
        for i in range(self.count()):
            path = self.file_path_at(i)
            if path:
                paths.append(path)
        return paths

    def clear(self):
        self._item_paths.clear()
        super().clear()

    def takeItem(self, row: int):
        item = super().takeItem(row)
        if item is not None:
            self._item_paths.pop(id(item), None)
        return item

    def focusInEvent(self, event: QFocusEvent):
        super().focusInEvent(event)
        self.focused.emit(self.playlist_num)

    def _accepts_drag(self, mime) -> bool:
        return mime.hasUrls() or mime.hasFormat(TRACK_MIME)

    def _clear_other_drop_indicators(self) -> None:
        if not self._playlists:
            return
        for playlist in self._playlists:
            if playlist is not self:
                playlist._clear_drop_indicator()

    def startDrag(self, supportedActions):
        indexes = self.selectedIndexes()
        if not indexes:
            return
        row = indexes[0].row()
        item = self.item(row)
        if item is None:
            return

        mime = QMimeData()
        mime.setData(TRACK_MIME, f"{self.playlist_num}:{row}".encode())

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

        item.setHidden(True)
        self.viewport().update()
        drag.exec(Qt.DropAction.MoveAction)
        item.setHidden(False)
        self.viewport().update()
        self._clear_drop_indicator()
        if self._playlists:
            for playlist in self._playlists:
                playlist._clear_drop_indicator()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._accepts_drag(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if not self._accepts_drag(event.mimeData()):
            event.ignore()
            return

        self._update_drop_indicator(event.position())
        self._clear_other_drop_indicators()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent):
        self._clear_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        target_row = self.drop_insert_row()
        self._clear_drop_indicator()

        if event.mimeData().hasUrls():
            insert_row = target_row
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if is_audio_file(file_path):
                    self.add_file(file_path, row=insert_row)
                    insert_row += 1
            event.acceptProposedAction()
            self.tracksChanged.emit()
            return

        if event.mimeData().hasFormat(TRACK_MIME):
            payload = bytes(event.mimeData().data(TRACK_MIME)).decode()
            try:
                source_playlist_num, source_row = map(int, payload.split(":", 1))
            except ValueError:
                return

            source_playlist = self._resolve_playlist(source_playlist_num)
            if source_playlist is None:
                return
            if source_row < 0 or source_row >= source_playlist.count():
                return

            if source_playlist is self and target_row in (source_row, source_row + 1):
                event.acceptProposedAction()
                return

            item = source_playlist.takeItem(source_row)
            if item is None:
                return

            if source_playlist is self and source_row < target_row:
                target_row -= 1
            target_row = max(0, min(target_row, self.count()))

            self.insertItem(target_row, item)
            self._adopt_item(item)
            self.setCurrentItem(item)
            self.itemDropped.emit(
                source_playlist_num,
                source_row,
                self.playlist_num,
                target_row,
            )
            event.acceptProposedAction()
            return

        super().dropEvent(event)

    def add_file(self, file_path, row: int | None = None):
        if file_path:
            self.add_item_with_path(file_path, row=row)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            item = self.currentItem()
            if item:
                self.editItem(item)
                return
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row_before = self.currentRow()
            super().keyPressEvent(event)
            if self.currentRow() != row_before:
                item = self.currentItem()
                if item is not None:
                    self.selectionNavigated.emit(item)
            return
        super().keyPressEvent(event)

    def move_item_up(self):
        """Переместить выбранный элемент вверх"""
        current_row = self.currentRow()
        if current_row > 0:
            item = self.takeItem(current_row)
            self.insertItem(current_row - 1, item)
            self._adopt_item(item)
            self.setCurrentRow(current_row - 1)

    def move_item_down(self):
        """Переместить выбранный элемент вниз"""
        current_row = self.currentRow()
        if current_row < self.count() - 1:
            item = self.takeItem(current_row)
            self.insertItem(current_row + 1, item)
            self._adopt_item(item)
            self.setCurrentRow(current_row + 1)

    def move_item_top(self):
        """Переместить выбранный элемент в начало"""
        current_row = self.currentRow()
        if current_row > 0:
            item = self.takeItem(current_row)
            self.insertItem(0, item)
            self._adopt_item(item)
            self.setCurrentRow(0)

    def move_item_bottom(self):
        """Переместить выбранный элемент в конец"""
        current_row = self.currentRow()
        if current_row < self.count() - 1:
            item = self.takeItem(current_row)
            self.insertItem(self.count(), item)
            self._adopt_item(item)
            self.setCurrentRow(self.count() - 1)

    def get_stats(self) -> tuple[int, int]:
        """Return track count and total duration in ms."""
        total_ms = 0
        count = self.count()
        for row in range(count):
            item = self.item(row)
            if item is None:
                continue
            duration = get_item_duration(item)
            if duration is not None:
                total_ms += duration
        return count, total_ms
