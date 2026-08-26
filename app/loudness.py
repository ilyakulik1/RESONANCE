"""Track loudness measurement (BS.1770-style) and gain-to-target helpers."""

from __future__ import annotations

import math
import os

import numpy as np
from scipy.signal import sosfilt

# Match tracks to this integrated loudness (LUFS).
TARGET_LUFS = -14.0
TARGET_LUFS_MIN = -30.0
TARGET_LUFS_MAX = -6.0
GAIN_DB_MIN = -24.0
GAIN_DB_MAX = 24.0

# Analysis decode settings (reuse BPM sample rate; longer window for loudness).
LOUDNESS_SAMPLE_RATE = 22050
LOUDNESS_MAX_SECONDS = 180.0


def db_to_linear(db: float) -> float:
    return float(10.0 ** (float(db) / 20.0))


def linear_to_db(linear: float) -> float:
    x = max(1e-12, float(linear))
    return float(20.0 * math.log10(x))


def clamp_target_lufs(lufs: float) -> float:
    return float(max(TARGET_LUFS_MIN, min(TARGET_LUFS_MAX, float(lufs))))


def clamp_gain_db(gain_db: float) -> float:
    return float(max(GAIN_DB_MIN, min(GAIN_DB_MAX, float(gain_db))))


def gain_db_to_target(lufs: float, *, target_lufs: float = TARGET_LUFS) -> float:
    """Gain needed so measured LUFS reaches the target."""
    return clamp_gain_db(clamp_target_lufs(target_lufs) - float(lufs))


def _k_weight_sos(sample_rate: float) -> np.ndarray:
    """Second-order sections approximating BS.1770 K-weighting."""
    sr = float(sample_rate)
    # High-shelf stage (~+4 dB above ~1.5 kHz), bilinear from analog prototype.
    f0 = 1681.974450955533
    G = 3.999843853973347
    Q = 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10.0 ** (G / 20.0)
    Vb = Vh**0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    shelf_b = np.array(
        [
            (Vh + Vb * K / Q + K * K) / a0,
            2.0 * (K * K - Vh) / a0,
            (Vh - Vb * K / Q + K * K) / a0,
        ],
        dtype=np.float64,
    )
    shelf_a = np.array(
        [1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0],
        dtype=np.float64,
    )

    # High-pass / RLB stage (~38 Hz).
    f0_hp = 38.13547087602444
    Q_hp = 0.5003270373238773
    K_hp = math.tan(math.pi * f0_hp / sr)
    a0_hp = 1.0 + K_hp / Q_hp + K_hp * K_hp
    hp_b = np.array([1.0, -2.0, 1.0], dtype=np.float64) / a0_hp
    hp_a = np.array(
        [
            1.0,
            2.0 * (K_hp * K_hp - 1.0) / a0_hp,
            (1.0 - K_hp / Q_hp + K_hp * K_hp) / a0_hp,
        ],
        dtype=np.float64,
    )

    def tf2sos(b: np.ndarray, a: np.ndarray) -> np.ndarray:
        # One biquad → one SOS row [b0,b1,b2,a0,a1,a2]
        return np.array([[b[0], b[1], b[2], a[0], a[1], a[2]]], dtype=np.float64)

    return np.vstack([tf2sos(shelf_b, shelf_a), tf2sos(hp_b, hp_a)])


def measure_integrated_lufs(
    samples: np.ndarray,
    sample_rate: float,
) -> float | None:
    """Return integrated loudness in LUFS, or None if signal is too short/quiet."""
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    sr = float(sample_rate)
    if x.size < int(sr * 0.4):
        return None

    # Trim obvious leading/trailing silence for more stable estimates.
    abs_x = np.abs(x)
    thr = max(1e-4, float(np.max(abs_x)) * 0.001)
    nonzero = np.flatnonzero(abs_x >= thr)
    if nonzero.size == 0:
        return None
    x = x[nonzero[0] : nonzero[-1] + 1]
    if x.size < int(sr * 0.4):
        return None

    sos = _k_weight_sos(sr)
    weighted = sosfilt(sos, x)

    # Block mean-square with absolute gate (−70 LUFS), simplified BS.1770.
    block = max(1, int(round(0.4 * sr)))
    hop = max(1, int(round(0.1 * sr)))
    if weighted.size < block:
        ms = float(np.mean(weighted * weighted))
        if ms <= 1e-12:
            return None
        return float(-0.691 + 10.0 * math.log10(ms))

    powers: list[float] = []
    for start in range(0, weighted.size - block + 1, hop):
        chunk = weighted[start : start + block]
        powers.append(float(np.mean(chunk * chunk)))
    if not powers:
        return None

    abs_gate = 10.0 ** ((-70.0 + 0.691) / 10.0)
    gated = [p for p in powers if p > abs_gate]
    if not gated:
        return None

    # Relative gate: 10 LU below ungated loudness of absolute-gated blocks.
    ungated_ms = float(np.mean(gated))
    ungated_lufs = -0.691 + 10.0 * math.log10(max(ungated_ms, 1e-12))
    rel_gate = 10.0 ** ((ungated_lufs - 10.0 + 0.691) / 10.0)
    final = [p for p in gated if p > rel_gate]
    if not final:
        final = gated
    ms = float(np.mean(final))
    if ms <= 1e-12:
        return None
    return float(-0.691 + 10.0 * math.log10(ms))


def measure_file_lufs(file_path: str) -> float | None:
    """Decode audio and measure integrated LUFS."""
    if not file_path or not os.path.isfile(file_path):
        return None
    try:
        from app.time_utils import allows_bpm_loudness_analysis

        if not allows_bpm_loudness_analysis(file_path):
            return None

        from app.audio_loader import load_mono_samples

        samples = load_mono_samples(
            file_path,
            sample_rate=LOUDNESS_SAMPLE_RATE,
            max_seconds=LOUDNESS_MAX_SECONDS,
        )
        if samples is None or len(samples) == 0:
            return None
        return measure_integrated_lufs(samples, LOUDNESS_SAMPLE_RATE)
    except Exception as exc:
        print(f"Loudness analysis error for {file_path}: {exc}")
        return None
