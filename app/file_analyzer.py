"""Background file analysis: BPM + waveform + loudness, with disk cache."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QListWidgetItem

from app.analysis_cache import (
    CANONICAL_WAVEFORM_BARS,
    has_loudness_cache,
    has_waveform_cache,
    load_analysis,
    save_analysis,
)
from app.audio_utils import generate_waveform_data
from app.bpm_detector import detect_bpm
from app.loudness import measure_file_lufs


@dataclass
class _AnalyzeJob:
    file_path: str
    item: QListWidgetItem | None = None
    force: bool = False


class _AnalyzeWorker(QThread):
    finished = pyqtSignal(str, object, object, object, bool)  # path, bpm, peaks, lufs, force

    def __init__(self, file_path: str, *, force: bool = False):
        super().__init__()
        self._file_path = file_path
        self._force = force

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        path = self._file_path
        bpm: float | None = None
        peaks: list[float] | None = None
        lufs: float | None = None

        from app.time_utils import allows_bpm_loudness_analysis

        allow_bpm_lufs = allows_bpm_loudness_analysis(path)

        if not self._force:
            cached = load_analysis(path)
            if cached is not None and cached.peaks:
                bpm = cached.bpm
                peaks = list(cached.peaks)
                lufs = cached.lufs
                dirty = False
                if allow_bpm_lufs and bpm is None:
                    bpm = detect_bpm(path)
                    if isinstance(bpm, (int, float)):
                        bpm = float(bpm)
                        dirty = True
                if allow_bpm_lufs and lufs is None and not self.isInterruptionRequested():
                    lufs = measure_file_lufs(path)
                    if lufs is not None:
                        dirty = True
                if dirty:
                    save_analysis(path, bpm=bpm, peaks=peaks, lufs=lufs)
                if not self.isInterruptionRequested():
                    self.finished.emit(path, bpm, peaks, lufs, False)
                return

        if self.isInterruptionRequested():
            return
        if allow_bpm_lufs:
            bpm_raw = detect_bpm(path)
            if isinstance(bpm_raw, (int, float)):
                bpm = float(bpm_raw)

        if self.isInterruptionRequested():
            return
        peaks = generate_waveform_data(
            path,
            CANONICAL_WAVEFORM_BARS,
            start_ms=0,
            end_ms=None,
        )

        if self.isInterruptionRequested():
            return
        if allow_bpm_lufs:
            lufs = measure_file_lufs(path)

        if peaks:
            save_analysis(path, bpm=bpm, peaks=peaks, num_bars=len(peaks), lufs=lufs)

        if not self.isInterruptionRequested():
            self.finished.emit(path, bpm, peaks or [], lufs, True)


class FileAnalyzer(QObject):
    """Queue analysis jobs that produce BPM + waveform peaks + loudness."""

    item_analyzed = pyqtSignal(
        object, str, object, object, object, bool
    )  # item, path, bpm, peaks, lufs, force
    file_analyzed = pyqtSignal(str, object, object, object, bool)  # path, bpm, peaks, lufs, force
    progress = pyqtSignal(int, int)  # done, total
    busyChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: list[_AnalyzeJob] = []
        self._active: _AnalyzeJob | None = None
        self._worker: _AnalyzeWorker | None = None
        self._total = 0
        self._done = 0
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def analyze_playlist(self, playlist, *, only_missing: bool = True) -> int:
        from app.playlist_io import get_item_bpm, get_item_duration
        from app.time_utils import allows_bpm_loudness_analysis

        jobs: list[_AnalyzeJob] = []
        for row in range(playlist.count()):
            item = playlist.item(row)
            if item is None:
                continue
            file_path = playlist.file_path_at(row) if hasattr(playlist, "file_path_at") else None
            if not file_path or not os.path.isfile(file_path):
                continue
            if only_missing:
                long_track = not allows_bpm_loudness_analysis(
                    file_path, get_item_duration(item)
                )
                has_bpm = get_item_bpm(item) is not None or long_track
                has_wave = has_waveform_cache(file_path)
                has_lufs = has_loudness_cache(file_path) or long_track
                if has_bpm and has_wave and has_lufs:
                    continue
            jobs.append(_AnalyzeJob(os.path.abspath(file_path), item=item, force=False))

        if not jobs:
            return 0

        self._queue.extend(jobs)
        self._total = self._done + len(self._queue) + (1 if self._active else 0)
        self._set_busy(True)
        self.progress.emit(self._done, self._total)
        self.statusChanged.emit(f"Queued {len(jobs)} track(s) for analysis…")
        self._start_next()
        return len(jobs)

    def analyze_item(
        self,
        item: QListWidgetItem,
        file_path: str,
        *,
        force: bool = True,
    ) -> None:
        if not file_path:
            return
        self._enqueue(
            _AnalyzeJob(os.path.abspath(file_path), item=item, force=force),
            front=True,
        )

    def _enqueue(self, job: _AnalyzeJob, *, front: bool = False) -> None:
        if front:
            self._cancel_worker()
            self._queue.insert(0, job)
            self._done = 0
            self._total = 1 + len(self._queue)
        else:
            self._queue.append(job)
            self._total = self._done + len(self._queue) + (1 if self._active else 0)
        self._set_busy(True)
        self.progress.emit(self._done, max(1, self._total))
        self._start_next()

    def _set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        self.busyChanged.emit(busy)

    def _start_next(self) -> None:
        if self._worker is not None or not self._queue:
            return
        self._active = self._queue.pop(0)
        name = Path(self._active.file_path).name
        current = self._done + 1
        total = max(self._total, current + len(self._queue))
        self._total = total
        self.statusChanged.emit(f"Analyzing {name}… ({current}/{total})")
        self.progress.emit(self._done, total)
        self._worker = _AnalyzeWorker(self._active.file_path, force=self._active.force)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _cancel_worker(self) -> None:
        if self._worker is None:
            return
        if self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        self._worker = None
        self._active = None

    def _on_worker_finished(
        self,
        file_path: str,
        bpm: object,
        peaks: object,
        lufs: object,
        force: bool = False,
    ) -> None:
        job = self._active
        worker = self._worker
        self._worker = None
        self._active = None
        if worker is not None:
            worker.deleteLater()

        self._done += 1
        total = max(self._total, self._done)
        self.progress.emit(self._done, total)

        peak_list = peaks if isinstance(peaks, list) else []
        force_flag = bool(force)
        if job is not None and job.item is not None:
            self.item_analyzed.emit(
                job.item, file_path, bpm, peak_list, lufs, force_flag
            )
        else:
            self.file_analyzed.emit(file_path, bpm, peak_list, lufs, force_flag)

        if self._queue:
            self._start_next()
            return

        self.statusChanged.emit(f"Analysis complete ({self._done}/{total})")
        self._total = 0
        self._done = 0
        self._set_busy(False)
