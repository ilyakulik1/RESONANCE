"""Simple stereo peak limiter (after master EQ on the air mix bus)."""

from __future__ import annotations

import numpy as np


class PeakLimiter:
    """Feed-forward peak limiter with instant attack and exponential release."""

    def __init__(
        self,
        *,
        sample_rate: float = 44100.0,
        threshold: float = 0.95,
        ceiling: float = 0.99,
        release_ms: float = 80.0,
    ):
        self._sample_rate = max(1.0, float(sample_rate))
        self.threshold = float(threshold)
        self.ceiling = float(ceiling)
        self.release_ms = float(release_ms)
        self._envelope = 1.0
        self._bypass = False
        self._update_coeff()

    def _update_coeff(self) -> None:
        # Exponential release toward 1.0 per sample when below threshold.
        ms = max(1.0, float(self.release_ms))
        self._release_coeff = float(np.exp(-1.0 / (self._sample_rate * ms * 0.001)))

    def set_sample_rate(self, sample_rate: float) -> None:
        self._sample_rate = max(1.0, float(sample_rate))
        self._update_coeff()

    def set_bypass(self, bypass: bool) -> None:
        self._bypass = bool(bypass)

    def is_bypass(self) -> bool:
        return self._bypass

    def set_threshold(self, threshold: float) -> None:
        self.threshold = max(0.05, min(1.0, float(threshold)))

    def set_ceiling(self, ceiling: float) -> None:
        self.ceiling = max(0.1, min(1.0, float(ceiling)))

    def set_release_ms(self, release_ms: float) -> None:
        self.release_ms = max(1.0, min(2000.0, float(release_ms)))
        self._update_coeff()

    def reset(self) -> None:
        self._envelope = 1.0

    def process_interleaved(self, interleaved: np.ndarray) -> np.ndarray:
        if interleaved.size == 0 or self._bypass:
            return interleaved
        samples = np.asarray(interleaved, dtype=np.float32)
        if samples.size % 2 != 0:
            samples = samples[: samples.size - (samples.size % 2)]
        if samples.size == 0:
            return samples

        frames = samples.reshape(-1, 2)
        peak = np.max(np.abs(frames), axis=1)
        thr = max(1e-6, float(self.threshold))
        ceiling = max(thr, float(self.ceiling))
        # Target gain so peak maps to ceiling when above threshold.
        target = np.ones(peak.shape[0], dtype=np.float64)
        over = peak > thr
        if np.any(over):
            target[over] = (ceiling / peak[over]).astype(np.float64)

        env = float(self._envelope)
        coeff = float(self._release_coeff)
        out_gain = np.empty(peak.shape[0], dtype=np.float32)
        for i in range(peak.shape[0]):
            t = float(target[i])
            if t < env:
                env = t  # instant attack
            else:
                env = coeff * env + (1.0 - coeff) * t
            out_gain[i] = env
        self._envelope = env
        frames = frames * out_gain[:, np.newaxis]
        return frames.reshape(-1).astype(np.float32, copy=False)

    def to_dict(self) -> dict:
        return {
            "bypass": self._bypass,
            "threshold": self.threshold,
            "ceiling": self.ceiling,
            "release_ms": self.release_ms,
        }

    def load_dict(self, data: object) -> None:
        if not isinstance(data, dict):
            return
        if "bypass" in data:
            self.set_bypass(bool(data["bypass"]))
        if "threshold" in data:
            try:
                self.set_threshold(float(data["threshold"]))
            except (TypeError, ValueError):
                pass
        if "ceiling" in data:
            try:
                self.set_ceiling(float(data["ceiling"]))
            except (TypeError, ValueError):
                pass
        if "release_ms" in data:
            try:
                self.set_release_ms(float(data["release_ms"]))
            except (TypeError, ValueError):
                pass
