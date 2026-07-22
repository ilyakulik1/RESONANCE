from PyQt6.QtCore import QObject, QUrl, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QListWidgetItem

import os

from app.playlist_io import get_item_duration, set_item_duration


class DurationProber(QObject):
    PROBE_TIMEOUT_MS = 5000
    itemProbed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._queue: list[tuple[str, QListWidgetItem]] = []
        self._current_item: QListWidgetItem | None = None
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._player.durationChanged.connect(self._on_duration_changed)

    def probe_item(self, item: QListWidgetItem, file_path: str) -> None:
        if get_item_duration(item) is not None:
            return
        if not file_path or not os.path.isfile(file_path):
            return
        self._queue.append((file_path, item))
        if self._current_item is None:
            self._start_next()

    def probe_playlist(self, playlist) -> None:
        for row in range(playlist.count()):
            item = playlist.item(row)
            file_path = playlist.file_path_at(row)
            if item and file_path:
                self.probe_item(item, file_path)

    def _start_next(self) -> None:
        self._timeout_timer.stop()
        while self._queue:
            file_path, item = self._queue.pop(0)
            if get_item_duration(item) is not None:
                continue
            if not file_path or not os.path.isfile(file_path):
                continue
            self._current_item = item
            self._player.setSource(QUrl.fromLocalFile(file_path))
            self._timeout_timer.start(self.PROBE_TIMEOUT_MS)
            return
        self._current_item = None

    def _on_duration_changed(self, duration_ms: int) -> None:
        if duration_ms <= 0 or self._current_item is None:
            return

        self._timeout_timer.stop()
        item = self._current_item
        set_item_duration(item, duration_ms)
        widget = item.listWidget()
        if widget:
            widget.viewport().update()
        self.itemProbed.emit(item)
        self._current_item = None
        QTimer.singleShot(0, self._start_next)

    def _on_timeout(self) -> None:
        self._current_item = None
        self._player.stop()
        self._start_next()
