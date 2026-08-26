from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QListWidgetItem

from app.audio_loader import load_mono_samples

ANALYSIS_MAX_SECONDS = 90
TARGET_SAMPLE_RATE = 44100
HOP_LENGTH = 512
MIN_BPM = 60
MAX_BPM = 200
SEGMENT_SECONDS = 30
REFINE_WINDOW = 4
REFINE_STEP = 0.05

try:
    import librosa

    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False

try:
    from mutagen import File as MutagenFile

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False


def _normalize_bpm(bpm: float) -> float:
    value = bpm
    while value < 70:
        value *= 2
    while value > 180:
        value /= 2
    return value


def _read_bpm_from_metadata(file_path: str) -> int | None:
    if not MUTAGEN_AVAILABLE:
        return None

    try:
        audio = MutagenFile(file_path)
        if audio is None or not getattr(audio, "tags", None):
            return None

        tags = audio.tags
        for key in ("TBPM", "BPM", "bpm", "tmpo"):
            if key not in tags:
                continue
            raw = tags[key]
            value = raw[0] if isinstance(raw, (list, tuple)) else raw
            if hasattr(value, "text"):
                value = value.text[0]
            bpm = float(str(value).strip())
            if MIN_BPM <= bpm <= MAX_BPM:
                return round(bpm)
    except Exception:
        return None

    return None


def _bpm_from_beat_times(beat_times) -> float | None:
    import numpy as np

    beats = np.asarray(beat_times, dtype=np.float64)
    if beats.size < 4:
        return None

    intervals = np.diff(beats)
    q1, q3 = np.percentile(intervals, [25, 75])
    iqr = q3 - q1
    if iqr > 0:
        intervals = intervals[(intervals >= q1 - 1.5 * iqr) & (intervals <= q3 + 1.5 * iqr)]
    if intervals.size == 0:
        return None

    return 60.0 / float(np.median(intervals))


def _score_tempo(onset_env, sr: int, bpm: float) -> float:
    import numpy as np

    frames_per_beat = 60.0 * sr / (bpm * HOP_LENGTH)
    if frames_per_beat < 1:
        return 0.0

    phases = np.arange(0, len(onset_env), frames_per_beat)
    indices = np.clip(np.round(phases).astype(int), 0, len(onset_env) - 1)
    return float(np.mean(onset_env[indices]))


def _refine_bpm_with_comb(onset_env, sr: int, coarse_bpm: float) -> int:
    import numpy as np

    center = round(_normalize_bpm(coarse_bpm))
    best_bpm = center
    best_score = -1.0

    low = max(MIN_BPM, center - REFINE_WINDOW)
    high = min(MAX_BPM, center + REFINE_WINDOW)
    for bpm in np.arange(low, high + REFINE_STEP, REFINE_STEP):
        for candidate in (bpm, bpm * 2):
            normalized = _normalize_bpm(candidate)
            if not (MIN_BPM <= normalized <= MAX_BPM):
                continue
            score = _score_tempo(onset_env, sr, normalized)
            if score > best_score:
                best_score = score
                best_bpm = round(normalized)

    return best_bpm


def _collect_segment_estimates(samples, sr: int) -> list[int]:
    import numpy as np

    min_samples = sr * 3
    if len(samples) < min_samples:
        return []

    trimmed, _ = librosa.effects.trim(samples, top_db=30)
    if len(trimmed) < min_samples:
        trimmed = samples

    onset_env = librosa.onset.onset_strength(
        y=trimmed,
        sr=sr,
        hop_length=HOP_LENGTH,
        aggregate=np.median,
    )
    if onset_env.size == 0:
        return []

    estimates: list[int] = []

    coarse_tempo = float(
        librosa.feature.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=HOP_LENGTH,
            aggregate=np.median,
            std_bpm=0.5,
            max_tempo=MAX_BPM,
            ac_size=8.0,
        )[0]
    )
    estimates.append(_refine_bpm_with_comb(onset_env, sr, coarse_tempo))

    beat_tempo, beat_times = librosa.beat.beat_track(
        y=trimmed,
        sr=sr,
        hop_length=HOP_LENGTH,
        tightness=100,
        units="time",
    )
    estimates.append(_refine_bpm_with_comb(onset_env, sr, float(np.atleast_1d(beat_tempo)[0])))

    ibi_bpm = _bpm_from_beat_times(beat_times)
    if ibi_bpm is not None:
        estimates.append(_refine_bpm_with_comb(onset_env, sr, ibi_bpm))

    return estimates


def _consensus_integer_bpm(estimates: list[int]) -> int | None:
    if not estimates:
        return None

    normalized = [round(_normalize_bpm(float(bpm))) for bpm in estimates]
    best_bpm, _best_count = Counter(normalized).most_common(1)[0]

    close = [bpm for bpm in normalized if abs(bpm - best_bpm) <= 1]
    if len(close) >= max(2, len(normalized) // 2):
        return round(sum(close) / len(close))

    return best_bpm


def _detect_bpm_librosa(samples, sr: int = TARGET_SAMPLE_RATE) -> int | None:
    segment_len = sr * SEGMENT_SECONDS
    if len(samples) <= segment_len:
        segments = [samples]
    else:
        last_start = len(samples) - segment_len
        mid_start = last_start // 2
        segments = [
            samples[0:segment_len],
            samples[mid_start:mid_start + segment_len],
            samples[last_start:last_start + segment_len],
        ]

    estimates: list[int] = []
    for segment in segments:
        estimates.extend(_collect_segment_estimates(segment, sr))

    return _consensus_integer_bpm(estimates)


def _detect_bpm_fallback(samples, sr: int = TARGET_SAMPLE_RATE) -> int | None:
    import numpy as np

    frame_size = 2048
    hop_size = HOP_LENGTH
    if len(samples) < frame_size + hop_size:
        return None

    n_frames = 1 + (len(samples) - frame_size) // hop_size
    strides = (hop_size * samples.strides[0], samples.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        samples,
        shape=(n_frames, frame_size),
        strides=strides,
    )
    onset = np.maximum(np.diff(np.sum(frames * frames, axis=1)), 0.0)
    if len(onset) < 100:
        return None

    peak = onset.max()
    if peak > 0:
        onset /= peak

    autocorr = np.correlate(onset, onset, mode="full")
    autocorr = autocorr[len(autocorr) // 2 :]

    min_lag = int(60 * sr / (MAX_BPM * hop_size))
    max_lag = int(60 * sr / (MIN_BPM * hop_size))
    max_lag = min(max_lag, len(autocorr) - 1)
    if min_lag >= max_lag:
        return None

    peak_lag = min_lag + int(np.argmax(autocorr[min_lag : max_lag + 1]))
    if peak_lag <= 0:
        return None

    coarse = 60.0 * sr / (peak_lag * hop_size)
    onset_env = onset.astype(np.float64)
    return _refine_bpm_with_comb(onset_env, sr, coarse)


def detect_bpm(file_path: str) -> int | None:
    if not file_path or not os.path.exists(file_path):
        return None

    try:
        tagged_bpm = _read_bpm_from_metadata(file_path)
        if tagged_bpm is not None:
            return tagged_bpm

        from app.time_utils import allows_bpm_loudness_analysis

        if not allows_bpm_loudness_analysis(file_path):
            return None

        samples = load_mono_samples(
            file_path,
            sample_rate=TARGET_SAMPLE_RATE,
            max_seconds=ANALYSIS_MAX_SECONDS,
        )
        if samples is None or len(samples) < TARGET_SAMPLE_RATE * 3:
            return None

        if LIBROSA_AVAILABLE:
            bpm = _detect_bpm_librosa(samples, TARGET_SAMPLE_RATE)
            if bpm is not None:
                return bpm

        return _detect_bpm_fallback(samples, TARGET_SAMPLE_RATE)
    except Exception as exc:
        print(f"BPM detection error for {file_path}: {exc}")
        return None


@dataclass
class _BpmJob:
    file_path: str
    item: QListWidgetItem | None = None
    for_playback: bool = False


class _BpmWorker(QThread):
    finished = pyqtSignal(str, object)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        bpm = detect_bpm(self._file_path)
        if not self.isInterruptionRequested():
            self.finished.emit(self._file_path, bpm)


class BpmDetector(QObject):
    """Detects BPM in a background thread queue."""

    bpm_detected = pyqtSignal(str, object)
    item_bpm_updated = pyqtSignal(object, str, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: list[_BpmJob] = []
        self._active_job: _BpmJob | None = None
        self._worker: _BpmWorker | None = None

    def request_bpm(self, file_path: str) -> None:
        abs_path = os.path.abspath(file_path)
        self._enqueue(_BpmJob(abs_path, for_playback=True), front=True)

    def request_item_bpm(
        self,
        item: QListWidgetItem,
        file_path: str,
        *,
        force: bool = False,
    ) -> None:
        from app.playlist_io import get_item_bpm

        if not force:
            stored_bpm = get_item_bpm(item)
            if stored_bpm is not None:
                self.item_bpm_updated.emit(item, os.path.abspath(file_path), stored_bpm)
                return
        self._enqueue(_BpmJob(os.path.abspath(file_path), item=item))

    def analyze_playlist(self, playlist, *, only_missing: bool = True) -> int:
        from app.playlist_io import get_item_bpm

        queued = 0
        for row in range(playlist.count()):
            item = playlist.item(row)
            if item is None:
                continue
            file_path = playlist.file_path_at(row) if hasattr(playlist, "file_path_at") else None
            if not file_path:
                continue
            if only_missing and get_item_bpm(item) is not None:
                continue
            self._enqueue(_BpmJob(os.path.abspath(file_path), item=item))
            queued += 1
        return queued

    def _enqueue(self, job: _BpmJob, *, front: bool = False) -> None:
        if front:
            self._cancel_worker()
            self._queue.insert(0, job)
        else:
            self._queue.append(job)
        self._start_next()

    def _start_next(self) -> None:
        if self._worker is not None or not self._queue:
            return
        self._active_job = self._queue.pop(0)
        self._worker = _BpmWorker(self._active_job.file_path)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _cancel_worker(self) -> None:
        if self._worker is None:
            return
        if self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        self._worker.deleteLater()
        self._worker = None
        self._active_job = None

    def _on_worker_finished(self, file_path: str, bpm: object) -> None:
        job = self._active_job
        if job is not None and job.file_path == file_path:
            if job.for_playback:
                self.bpm_detected.emit(file_path, bpm)
            if job.item is not None:
                self.item_bpm_updated.emit(job.item, file_path, bpm)

        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._active_job = None
        self._start_next()

    def shutdown(self) -> None:
        self._queue.clear()
        self._cancel_worker()
