"""Realtime parametric EQ: cascaded RBJ biquads (float32, interleaved)."""

from __future__ import annotations

import math
import threading
from typing import Iterable

import numpy as np
from scipy.signal import lfilter, lfilter_zi

from app.eq_curve import band_coeffs
from app.eq_state import EqBand, EqState


class _Biquad:
    __slots__ = ("b", "a", "zi")

    def __init__(self, b: np.ndarray, a: np.ndarray):
        self.b = np.asarray(b, dtype=np.float64)
        self.a = np.asarray(a, dtype=np.float64)
        self.zi = lfilter_zi(self.b, self.a) * 0.0

    def reset(self) -> None:
        self.zi = lfilter_zi(self.b, self.a) * 0.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        y, self.zi = lfilter(self.b, self.a, samples, zi=self.zi)
        return y.astype(np.float32, copy=False)


def _band_is_identity(band: EqBand) -> bool:
    if not band.enabled:
        return True
    if band.type in ("bell", "low_shelf", "high_shelf"):
        return abs(band.gain_db) < 0.05
    return False


def _build_filters(bands: Iterable[EqBand], sample_rate: float) -> list[tuple[np.ndarray, np.ndarray]]:
    coeffs: list[tuple[np.ndarray, np.ndarray]] = []
    for band in bands:
        if _band_is_identity(band):
            continue
        pair = band_coeffs(band, sample_rate)
        if pair is None:
            continue
        coeffs.append(pair)
    return coeffs


class EqProcessor:
    """Per-channel biquad cascade. Thread-safe live coefficient swap."""

    def __init__(self, *, channels: int = 2, sample_rate: float = 44100.0):
        self._channels = max(1, int(channels))
        self._sample_rate = float(sample_rate)
        self._lock = threading.Lock()
        self._bypass = False
        self._filters: list[list[_Biquad]] = [[] for _ in range(self._channels)]

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    def set_sample_rate(self, sample_rate: float) -> None:
        with self._lock:
            if abs(self._sample_rate - sample_rate) < 0.5:
                return
            self._sample_rate = float(sample_rate)
            self._filters = [[] for _ in range(self._channels)]

    def set_state(self, state: EqState | None) -> None:
        """Swap coefficients immediately. Does not restart playback.

        When the band count matches, coeffs are updated in place so filter
        memory (zi) is preserved — smoother live dragging.
        """
        if state is None:
            return
        with self._lock:
            self._bypass = bool(state.bypass)
            bands = list(state.enabled_bands()) if not state.bypass else []
            pairs = _build_filters(bands, self._sample_rate)
            if (
                pairs
                and self._filters
                and len(self._filters[0]) == len(pairs)
            ):
                for c in range(self._channels):
                    for i, (b, a) in enumerate(pairs):
                        filt = self._filters[c][i]
                        filt.b = np.asarray(b, dtype=np.float64)
                        filt.a = np.asarray(a, dtype=np.float64)
                return
            self._filters = [
                [_Biquad(b, a) for b, a in pairs] for _ in range(self._channels)
            ]

    def reset(self) -> None:
        with self._lock:
            for ch_filters in self._filters:
                for f in ch_filters:
                    f.reset()

    def process_interleaved(self, interleaved: np.ndarray) -> np.ndarray:
        """Process float32 interleaved PCM; returns float32 interleaved."""
        if interleaved.size == 0:
            return interleaved
        x = np.asarray(interleaved, dtype=np.float32).reshape(-1)
        ch = self._channels
        if x.size % ch != 0:
            x = x[: x.size - (x.size % ch)]
        with self._lock:
            if self._bypass or not any(self._filters):
                return x
            frames = x.reshape(-1, ch)
            out = np.empty_like(frames)
            for c in range(ch):
                channel = frames[:, c].astype(np.float64, copy=False)
                for filt in self._filters[c]:
                    channel = filt.process(channel)
                out[:, c] = channel.astype(np.float32, copy=False)
            return out.reshape(-1)


# High-cut kill: start open (~20 kHz) and sweep cutoff down to near-DC.
_HIGH_CUT_START_HZ = 20000.0
_HIGH_CUT_END_HZ = 40.0
HIGH_CUT_Q_DEFAULT = 0.707
HIGH_CUT_Q_MIN = 0.5
HIGH_CUT_Q_MAX = 20.0
# Volume fadeout starts at this fraction of the sweep (avoids residual rumble click).
_HIGH_CUT_FADEOUT_START = 0.65


class HighCutSweep:
    """One-shot low-pass cutoff sweep applied after the track EQ.

    Thread-safe: ``set_progress`` / ``set_q`` may run on the UI thread while
    ``process_interleaved`` runs on the audio callback thread.
    """

    def __init__(self, *, channels: int = 2, sample_rate: float = 44100.0):
        self._channels = max(1, int(channels))
        self._sample_rate = float(sample_rate)
        self._lock = threading.Lock()
        self._active = False
        self._filters: list[_Biquad] = []
        self._cutoff_hz = _HIGH_CUT_START_HZ
        self._q = HIGH_CUT_Q_DEFAULT
        self._gain = 1.0

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def set_sample_rate(self, sample_rate: float) -> None:
        with self._lock:
            if abs(self._sample_rate - sample_rate) < 0.5:
                return
            self._sample_rate = float(sample_rate)
            if self._active:
                self._rebuild_locked(self._cutoff_hz)

    def start(
        self,
        cutoff_hz: float = _HIGH_CUT_START_HZ,
        *,
        q: float | None = None,
    ) -> None:
        with self._lock:
            if q is not None:
                self._q = _clamp_high_cut_q(q)
            self._gain = 1.0
            self._active = True
            self._rebuild_locked(cutoff_hz)

    def clear(self) -> None:
        with self._lock:
            self._active = False
            self._filters = []
            self._cutoff_hz = _HIGH_CUT_START_HZ
            self._gain = 1.0

    def set_q(self, q: float) -> None:
        with self._lock:
            self._q = _clamp_high_cut_q(q)
            if self._active:
                self._rebuild_locked(self._cutoff_hz)

    def set_cutoff_hz(self, cutoff_hz: float) -> None:
        with self._lock:
            if not self._active:
                return
            self._rebuild_locked(cutoff_hz)

    def set_progress(self, progress: float) -> None:
        """Update cutoff and end fadeout gain from 0..1 sweep progress."""
        t = max(0.0, min(1.0, float(progress)))
        with self._lock:
            if not self._active:
                return
            self._rebuild_locked(high_cut_sweep_hz(t))
            self._gain = high_cut_fadeout_gain(t)

    def _rebuild_locked(self, cutoff_hz: float) -> None:
        sr = max(1.0, self._sample_rate)
        lo = 20.0
        hi = min(20000.0, sr * 0.45)
        f0 = float(np.clip(cutoff_hz, lo, hi))
        self._cutoff_hz = f0
        band = EqBand(freq_hz=f0, q=self._q, type="high_cut")
        pair = band_coeffs(band, sr)
        if pair is None:
            return
        b, a = pair
        if self._filters and len(self._filters) == self._channels:
            for filt in self._filters:
                filt.b = np.asarray(b, dtype=np.float64)
                filt.a = np.asarray(a, dtype=np.float64)
            return
        self._filters = [_Biquad(b, a) for _ in range(self._channels)]

    def process_interleaved(self, interleaved: np.ndarray) -> np.ndarray:
        if interleaved.size == 0:
            return interleaved
        x = np.asarray(interleaved, dtype=np.float32).reshape(-1)
        ch = self._channels
        if x.size % ch != 0:
            x = x[: x.size - (x.size % ch)]
        with self._lock:
            if not self._active or not self._filters:
                return x
            frames = x.reshape(-1, ch)
            out = np.empty_like(frames)
            gain = float(self._gain)
            for c in range(ch):
                channel = frames[:, c].astype(np.float64, copy=False)
                channel = self._filters[c].process(channel)
                if gain != 1.0:
                    channel = channel * gain
                out[:, c] = channel.astype(np.float32, copy=False)
            return out.reshape(-1)


def _clamp_high_cut_q(q: float) -> float:
    return float(max(HIGH_CUT_Q_MIN, min(HIGH_CUT_Q_MAX, float(q))))


def high_cut_sweep_hz(progress: float) -> float:
    """Map 0..1 progress to exponential cutoff (open → closed)."""
    t = max(0.0, min(1.0, float(progress)))
    start = math.log(_HIGH_CUT_START_HZ)
    end = math.log(_HIGH_CUT_END_HZ)
    return float(math.exp(start + (end - start) * t))


def high_cut_fadeout_gain(progress: float) -> float:
    """Volume envelope for the last part of the high-cut kill (1 → 0)."""
    t = max(0.0, min(1.0, float(progress)))
    start = _HIGH_CUT_FADEOUT_START
    if t <= start:
        return 1.0
    u = (t - start) / max(1e-9, 1.0 - start)
    # Smooth ease-out so the residual bass doesn't click when the deck stops.
    return float((1.0 - u) * (1.0 - u))
