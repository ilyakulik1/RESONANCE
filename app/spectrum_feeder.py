"""Side-channel spectrum analyzer: FFT from file around current playhead."""

from __future__ import annotations

import os
import threading
import time

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from app.eq_curve import log_freq_axis

_FFT_SIZE = 2048
_TARGET_SR = 44100
_POLL_S = 1.0 / 30.0
# Re-seek only when playhead drifted this far from last decode point.
_SEEK_SLACK_MS = 180
# Temporal smoothing: higher = smoother / slower.
_SMOOTH_ATTACK = 0.45
_SMOOTH_RELEASE = 0.12
# Slow peak tracker for display normalization (avoids whole-curve jumps).
_PEAK_ATTACK = 0.35
_PEAK_RELEASE = 0.02


def _frame_to_mono(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        if arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
            mono = arr.astype(np.float32).mean(axis=0)
        else:
            mono = arr.astype(np.float32).mean(axis=1)
    else:
        mono = arr.astype(np.float32).reshape(-1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 1.5:
        mono = mono / 32768.0
    return mono


class _PersistentTap:
    """Keep one PyAV container open and seek only when necessary."""

    def __init__(self) -> None:
        self._path: str | None = None
        self._container = None
        self._sample_rate = _TARGET_SR
        self._last_pos_ms = -10_000

    def close(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._path = None
        self._last_pos_ms = -10_000

    def _ensure(self, path: str) -> bool:
        if self._path == path and self._container is not None:
            return True
        self.close()
        try:
            import av

            self._container = av.open(path)
            if not self._container.streams.audio:
                self.close()
                return False
            stream = self._container.streams.audio[0]
            self._sample_rate = int(getattr(stream, "rate", None) or _TARGET_SR)
            self._path = path
            return True
        except Exception:
            self.close()
            return False

    def read(self, path: str, position_ms: int, n_samples: int = _FFT_SIZE) -> tuple[np.ndarray, int] | None:
        if not path or not os.path.isfile(path):
            self.close()
            return None
        if not self._ensure(path):
            return None

        pos = max(0, int(position_ms))
        need_seek = abs(pos - self._last_pos_ms) > _SEEK_SLACK_MS or self._last_pos_ms < 0
        if need_seek:
            try:
                # PyAV seek expects microseconds for default time base.
                self._container.seek(pos * 1000)
            except Exception:
                try:
                    self._container.seek(int(pos / 1000.0 / (1.0 / self._sample_rate)))
                except Exception:
                    pass
            self._last_pos_ms = pos

        chunks: list[np.ndarray] = []
        got = 0
        try:
            for frame in self._container.decode(audio=0):
                mono = _frame_to_mono(frame.to_ndarray())
                chunks.append(mono)
                got += mono.size
                if got >= n_samples:
                    break
        except Exception:
            # Container may be broken after failed seek — reopen next time.
            self.close()
            return None

        if not chunks:
            return None

        # Advance estimated decode head so sequential polls can skip reseek.
        advance_ms = int(1000.0 * min(got, n_samples) / max(self._sample_rate, 1))
        self._last_pos_ms = pos + max(10, advance_ms // 2)

        samples = np.concatenate(chunks)[:n_samples]
        if samples.size < n_samples:
            samples = np.pad(samples, (0, n_samples - samples.size))
        return samples, self._sample_rate


def samples_to_spectrum_db(
    samples: np.ndarray,
    sample_rate: int,
    out_freqs: np.ndarray,
) -> np.ndarray:
    if samples.size == 0:
        return np.full(out_freqs.shape, -90.0, dtype=np.float64)
    window = np.hanning(samples.size).astype(np.float32)
    spectrum = np.fft.rfft(samples * window)
    mags = np.abs(spectrum)
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / max(sample_rate, 1))
    # Absolute-ish dBFS relative to full-scale FFT bin.
    ref = float(samples.size) * 0.5
    db = 20.0 * np.log10(np.maximum(mags / max(ref, 1.0), 1e-12))
    db = np.interp(out_freqs, freqs, db, left=-90.0, right=-90.0)
    return np.clip(db, -90.0, 0.0)


def _smooth_spectrum(prev: np.ndarray | None, new: np.ndarray) -> np.ndarray:
    if prev is None or prev.shape != new.shape:
        return new.copy()
    rising = new > prev
    alpha = np.where(rising, _SMOOTH_ATTACK, _SMOOTH_RELEASE)
    return prev + alpha * (new - prev)


class _SpectrumWorker(QThread):
    spectrumReady = pyqtSignal(object, object)  # freqs, db

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._path: str | None = None
        self._position_ms = 0
        self._active = False
        self._stop = False
        self._freqs = log_freq_axis(96)
        self._tap = _PersistentTap()
        self._smooth: np.ndarray | None = None
        self._peak_ref = -20.0

    def configure(self, path: str | None, position_ms: int, active: bool) -> None:
        with self._lock:
            if path != self._path:
                self._smooth = None
                self._peak_ref = -20.0
            self._path = path
            self._position_ms = max(0, int(position_ms))
            self._active = bool(active) and bool(path)

    def snapshot(self) -> tuple[str | None, int, bool]:
        with self._lock:
            return self._path, self._position_ms, self._active

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        last_idle_emit = False
        while not self._stop:
            t0 = time.perf_counter()
            path, pos, active = self.snapshot()
            if active and path:
                last_idle_emit = False
                result = self._tap.read(path, pos)
                if result is not None:
                    samples, sr = result
                    raw = samples_to_spectrum_db(samples, sr, self._freqs)
                    # Slow peak tracker → stable visual headroom
                    frame_peak = float(np.percentile(raw, 95))
                    if frame_peak > self._peak_ref:
                        self._peak_ref += _PEAK_ATTACK * (frame_peak - self._peak_ref)
                    else:
                        self._peak_ref += _PEAK_RELEASE * (frame_peak - self._peak_ref)
                    display = np.clip(raw - self._peak_ref, -90.0, 0.0)
                    self._smooth = _smooth_spectrum(self._smooth, display)
                    self.spectrumReady.emit(self._freqs.copy(), self._smooth.copy())
                elif self._smooth is not None:
                    # Hold last frame with gentle decay instead of blanking.
                    self._smooth = self._smooth * 0.92 - 1.0
                    self._smooth = np.clip(self._smooth, -90.0, 0.0)
                    self.spectrumReady.emit(self._freqs.copy(), self._smooth.copy())
            else:
                self._tap.close()
                self._smooth = None
                if not last_idle_emit:
                    self.spectrumReady.emit(
                        self._freqs.copy(),
                        np.full(self._freqs.shape, -90.0, dtype=np.float64),
                    )
                    last_idle_emit = True

            elapsed = time.perf_counter() - t0
            poll = 0.25 if not (active and path) else max(0.0, _POLL_S - elapsed)
            slept = 0.0
            while slept < poll and not self._stop:
                time.sleep(0.01)
                slept += 0.01

        self._tap.close()


class SpectrumFeeder(QObject):
    """Feeds FFT magnitude frames for the parametric EQ view."""

    spectrumReady = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = _SpectrumWorker(self)
        self._worker.spectrumReady.connect(self.spectrumReady.emit)
        self._worker.start()

    def set_source(self, path: str | None) -> None:
        _, pos, active = self._worker.snapshot()
        self._worker.configure(path, pos, active)

    def set_position_ms(self, position_ms: int) -> None:
        path, _, active = self._worker.snapshot()
        self._worker.configure(path, position_ms, active)

    def set_active(self, active: bool) -> None:
        path, pos, _ = self._worker.snapshot()
        self._worker.configure(path, pos, active)

    def update(self, path: str | None, position_ms: int, playing: bool) -> None:
        self._worker.configure(path, position_ms, playing and bool(path))

    def shutdown(self) -> None:
        self._worker.request_stop()
        self._worker.wait(1500)


def read_audio_file_info(path: str | None) -> dict[str, str]:
    """Return display strings for file properties panel."""
    empty = {
        "name": "—",
        "format": "—",
        "duration": "—",
        "sample_rate": "—",
        "bitrate": "—",
        "channels": "—",
        "bpm": "—",
    }
    if not path or not os.path.isfile(path):
        return empty

    info = dict(empty)
    info["name"] = os.path.basename(path)
    ext = os.path.splitext(path)[1].lstrip(".").upper() or "—"
    info["format"] = ext

    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            meta = audio.info
            length = getattr(meta, "length", None)
            if length and length > 0:
                total = int(length)
                m, s = divmod(total, 60)
                h, m = divmod(m, 60)
                info["duration"] = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            sr = getattr(meta, "sample_rate", None) or getattr(meta, "samplerate", None)
            if sr:
                info["sample_rate"] = f"{int(sr)} Hz"
            br = getattr(meta, "bitrate", None)
            if br:
                info["bitrate"] = f"{int(br / 1000)} kbps" if br > 1000 else f"{int(br)} kbps"
            ch = getattr(meta, "channels", None)
            if ch:
                info["channels"] = str(int(ch))
    except Exception:
        pass

    return info
