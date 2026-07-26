"""EQ frequency-response helpers (RBJ biquads) for UI curve visualization."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from app.eq_state import EqBand, FREQ_MAX_HZ, FREQ_MIN_HZ

# Visualization sample rate for transfer-function evaluation.
_CURVE_SR = 48000.0
_CURVE_POINTS = 256


def log_freq_axis(n: int = _CURVE_POINTS) -> np.ndarray:
    return np.logspace(math.log10(FREQ_MIN_HZ), math.log10(FREQ_MAX_HZ), int(n))


def _biquad_peaking(f0: float, db_gain: float, q: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    a = 10 ** (db_gain / 40.0)
    w0 = 2.0 * math.pi * f0 / sr
    alpha = math.sin(w0) / (2.0 * max(q, 1e-6))
    cos_w0 = math.cos(w0)
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _biquad_lowshelf(f0: float, db_gain: float, q: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    a = 10 ** (db_gain / 40.0)
    w0 = 2.0 * math.pi * f0 / sr
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(q, 1e-6))
    two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
    b0 = a * ((a + 1) - (a - 1) * cos_w0 + two_sqrt_a_alpha)
    b1 = 2.0 * a * ((a - 1) - (a + 1) * cos_w0)
    b2 = a * ((a + 1) - (a - 1) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a + 1) + (a - 1) * cos_w0 + two_sqrt_a_alpha
    a1 = -2.0 * ((a - 1) + (a + 1) * cos_w0)
    a2 = (a + 1) + (a - 1) * cos_w0 - two_sqrt_a_alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _biquad_highshelf(f0: float, db_gain: float, q: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    a = 10 ** (db_gain / 40.0)
    w0 = 2.0 * math.pi * f0 / sr
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(q, 1e-6))
    two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
    b0 = a * ((a + 1) + (a - 1) * cos_w0 + two_sqrt_a_alpha)
    b1 = -2.0 * a * ((a - 1) + (a + 1) * cos_w0)
    b2 = a * ((a + 1) + (a - 1) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a + 1) - (a - 1) * cos_w0 + two_sqrt_a_alpha
    a1 = 2.0 * ((a - 1) - (a + 1) * cos_w0)
    a2 = (a + 1) - (a - 1) * cos_w0 - two_sqrt_a_alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _biquad_lowpass(f0: float, q: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    w0 = 2.0 * math.pi * f0 / sr
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * max(q, 1e-6))
    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = (1.0 - cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _biquad_highpass(f0: float, q: float, sr: float) -> tuple[np.ndarray, np.ndarray]:
    w0 = 2.0 * math.pi * f0 / sr
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * max(q, 1e-6))
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def band_coeffs(band: EqBand, sr: float = _CURVE_SR) -> tuple[np.ndarray, np.ndarray] | None:
    f0 = float(np.clip(band.freq_hz, FREQ_MIN_HZ, min(FREQ_MAX_HZ, sr * 0.45)))
    q = max(band.q, 1e-6)
    if band.type == "bell":
        return _biquad_peaking(f0, band.gain_db, q, sr)
    if band.type == "low_shelf":
        return _biquad_lowshelf(f0, band.gain_db, q, sr)
    if band.type == "high_shelf":
        return _biquad_highshelf(f0, band.gain_db, q, sr)
    if band.type == "low_cut":
        return _biquad_highpass(f0, q, sr)
    if band.type == "high_cut":
        return _biquad_lowpass(f0, q, sr)
    return None


def _mag_db_for_biquad(b: np.ndarray, a: np.ndarray, freqs: np.ndarray, sr: float) -> np.ndarray:
    w = 2.0 * math.pi * freqs / sr
    zj = np.exp(-1j * w)
    zj2 = zj * zj
    num = b[0] + b[1] * zj + b[2] * zj2
    den = a[0] + a[1] * zj + a[2] * zj2
    mag = np.abs(num / np.maximum(np.abs(den), 1e-20))
    return 20.0 * np.log10(np.maximum(mag, 1e-20))


def response_db(
    bands: Iterable[EqBand],
    freqs: np.ndarray | None = None,
    *,
    bypass: bool = False,
    sr: float = _CURVE_SR,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs_hz, magnitude_db) for the cascade of enabled bands."""
    if freqs is None:
        freqs = log_freq_axis()
    freqs = np.asarray(freqs, dtype=np.float64)
    total = np.zeros_like(freqs, dtype=np.float64)
    if bypass:
        return freqs, total
    for band in bands:
        if not band.enabled:
            continue
        coeffs = band_coeffs(band, sr)
        if coeffs is None:
            continue
        b, a = coeffs
        total += _mag_db_for_biquad(b, a, freqs, sr)
    return freqs, total
