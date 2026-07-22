from __future__ import annotations

import os

from PyQt6.QtCore import QObject, QThread, pyqtSignal


def _safe_is_running(thread: QThread | None) -> bool:
    if thread is None:
        return False
    try:
        return thread.isRunning()
    except RuntimeError:
        return False


class _WaveformWorker(QThread):
    data_ready = pyqtSignal(str, list, int, int, int)

    def __init__(
        self,
        file_path: str,
        num_bars: int,
        request_id: int,
        start_ms: int,
        end_ms: int,
        parent=None,
    ):
        super().__init__(parent)
        self._file_path = file_path
        self._num_bars = num_bars
        self._request_id = request_id
        self._start_ms = start_ms
        self._end_ms = end_ms

    def run(self) -> None:
        from app.audio_utils import generate_waveform_data

        data: list = []
        if not self.isInterruptionRequested():
            end_ms = self._end_ms if self._end_ms > 0 else None
            data = generate_waveform_data(
                self._file_path,
                num_bars=self._num_bars,
                start_ms=self._start_ms,
                end_ms=end_ms,
            )
        if not self.isInterruptionRequested():
            self.data_ready.emit(
                self._file_path,
                data,
                self._request_id,
                self._start_ms,
                self._end_ms,
            )


class WaveformLoader(QObject):
    """Generates waveform peaks in a background thread (one worker, queued requests)."""

    waveform_ready = pyqtSignal(str, list, int, int, int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._worker: _WaveformWorker | None = None
        self._active_path: str | None = None
        self._request_id = 0
        self._shutting_down = False
        self._queued_path: str | None = None
        self._queued_bars = 0
        self._queued_start_ms = 0
        self._queued_end_ms = 0
        self._queued_request_id = 0

    def request(
        self,
        file_path: str,
        num_bars: int,
        *,
        start_ms: int = 0,
        end_ms: int = 0,
    ) -> int:
        abs_path = os.path.abspath(file_path)
        self._request_id += 1
        request_id = self._request_id
        if self._shutting_down:
            return request_id
        self._active_path = abs_path
        self._queued_path = abs_path
        self._queued_bars = num_bars
        self._queued_start_ms = max(0, start_ms)
        self._queued_end_ms = max(0, end_ms)
        self._queued_request_id = request_id

        if _safe_is_running(self._worker):
            self._worker.requestInterruption()
            return request_id

        self._start_queued_worker()
        return request_id

    def shutdown(self) -> None:
        self._shutting_down = True
        self._active_path = None
        self._queued_path = None
        self._request_id += 1
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                worker.data_ready.disconnect()
            except (TypeError, RuntimeError):
                pass
            try:
                worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
        if not _safe_is_running(worker):
            if worker is not None:
                worker.deleteLater()
            return
        worker.requestInterruption()
        worker.wait(3000)
        worker.deleteLater()

    def _start_queued_worker(self) -> None:
        if self._shutting_down or self._queued_path is None:
            return
        if _safe_is_running(self._worker):
            return

        file_path = self._queued_path
        num_bars = self._queued_bars
        start_ms = self._queued_start_ms
        end_ms = self._queued_end_ms
        request_id = self._queued_request_id
        self._queued_path = None

        worker = _WaveformWorker(file_path, num_bars, request_id, start_ms, end_ms)
        worker.data_ready.connect(self._on_worker_data_ready)
        worker.finished.connect(self._on_worker_thread_finished)
        self._worker = worker
        worker.start()

    def _on_worker_data_ready(
        self,
        file_path: str,
        data: list,
        request_id: int,
        start_ms: int,
        end_ms: int,
    ) -> None:
        if self._shutting_down:
            return
        if request_id != self._request_id:
            return
        if self._active_path != file_path:
            return
        if not data:
            return
        self.waveform_ready.emit(file_path, data, request_id, start_ms, end_ms)

    def _on_worker_thread_finished(self) -> None:
        if self._shutting_down:
            return
        worker = self.sender()
        if not isinstance(worker, _WaveformWorker):
            return
        if worker is self._worker:
            self._worker = None
        worker.deleteLater()
        if self._queued_path is not None and not self._shutting_down:
            self._start_queued_worker()
