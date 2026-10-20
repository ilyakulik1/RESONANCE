"""Background worker that splits a track into vocal / instrumental stems."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from app.stems import StemPaths, load_cached_stems, separate_vocals_instrumental


class _StemWorker(QThread):
    finished = pyqtSignal(str, object, object)  # path, StemPaths|None, error|None
    progress = pyqtSignal(str, float)  # path, 0..1

    def __init__(self, file_path: str, *, force: bool = False, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.force = bool(force)

    def run(self) -> None:
        path = self.file_path
        try:
            if self.isInterruptionRequested():
                self.finished.emit(path, None, "cancelled")
                return
            if not self.force:
                cached = load_cached_stems(path)
                if cached is not None:
                    self.progress.emit(path, 1.0)
                    self.finished.emit(path, cached, None)
                    return

            def on_progress(value: float) -> None:
                if not self.isInterruptionRequested():
                    self.progress.emit(path, float(value))

            result = separate_vocals_instrumental(
                path,
                progress_callback=on_progress,
                force=self.force,
            )
            if self.isInterruptionRequested():
                self.finished.emit(path, None, "cancelled")
                return
            self.finished.emit(path, result, None)
        except Exception as exc:  # noqa: BLE001 — surface to UI
            self.finished.emit(path, None, str(exc))


class StemSeparator(QObject):
    """Queue stem-separation jobs (one at a time)."""

    finished = pyqtSignal(object, str, object, object)  # item, path, StemPaths|None, error
    progress = pyqtSignal(str, float)
    busyChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[tuple[list, str, bool]] = []
        self._worker: _StemWorker | None = None
        self._current_items: list = []
        self._current_path: str | None = None
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def separate_item(self, item, file_path: str, *, force: bool = False) -> None:
        path = str(file_path)
        if not force:
            cached = load_cached_stems(path)
            if cached is not None:
                self.finished.emit(item, path, cached, None)
                return
        if self._current_path == path and self._worker is not None:
            if item not in self._current_items:
                self._current_items.append(item)
            return
        for index, (items, queued_path, queued_force) in enumerate(self._queue):
            if queued_path == path:
                if item not in items:
                    items.append(item)
                if force and not queued_force:
                    self._queue[index] = (items, queued_path, True)
                return
        self._queue.append(([item], path, bool(force)))
        self._pump()

    def cancel(self) -> None:
        self._queue.clear()
        if self._worker is not None:
            self._worker.requestInterruption()

    def _set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        self.busyChanged.emit(busy)

    def _pump(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._queue:
            self._set_busy(False)
            self.statusChanged.emit("")
            return
        items, path, force = self._queue.pop(0)
        self._current_items = list(items)
        self._current_path = path
        self._set_busy(True)
        self.statusChanged.emit(f"Separating stems (MDX HQ5): {path}")
        worker = _StemWorker(path, force=force, parent=self)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_progress(self, path: str, value: float) -> None:
        self.progress.emit(path, value)

    def _on_finished(self, path: str, result: object, error: object) -> None:
        items = list(self._current_items)
        self._current_items = []
        self._current_path = None
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        paths = result if isinstance(result, StemPaths) else None
        err = str(error) if error else None
        if not items:
            items = [None]
        for item in items:
            self.finished.emit(item, path, paths, err)
        self._pump()
