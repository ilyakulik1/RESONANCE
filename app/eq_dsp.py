"""Realtime parametric EQ: cascaded RBJ biquads (float32, interleaved)."""

from __future__ import annotations

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
            state = EqState()
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
