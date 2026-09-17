"""On-disk cache for file analysis (BPM + canonical waveform peaks)."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.config import get_config_path

# Full-track peaks stored at this resolution; display sizes are resampled from it.
CANONICAL_WAVEFORM_BARS = 4096

_cache_dir_override: Path | None = None


def set_cache_dir(path: Path | str | None) -> None:
    """Use a project-local analysis folder, or None for the global app cache."""
    global _cache_dir_override
    if path is None:
        _cache_dir_override = None
        return
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    _cache_dir_override = resolved


def get_cache_dir() -> Path:
    return _cache_dir()


def _cache_dir() -> Path:
    if _cache_dir_override is not None:
        _cache_dir_override.mkdir(parents=True, exist_ok=True)
        return _cache_dir_override
    path = get_config_path("analysis_cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_fingerprint(file_path: str) -> tuple[str, float, int] | None:
    try:
        st = os.stat(file_path)
    except OSError:
        return None
    abs_path = os.path.abspath(file_path)
    digest = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:16]
    return digest, float(st.st_mtime), int(st.st_size)


def _cache_path(file_path: str) -> Path | None:
    fp = _file_fingerprint(file_path)
    if fp is None:
        return None
    digest, mtime, size = fp
    # Include mtime+size so edits invalidate the cache file name
    name = f"{digest}_{int(mtime)}_{size}.npz"
    return _cache_dir() / name


@dataclass
class AnalysisCacheEntry:
    bpm: float | None
    peaks: list[float]
    num_bars: int = CANONICAL_WAVEFORM_BARS
    lufs: float | None = None


def resample_peaks(peaks: list[float], num_bars: int) -> list[float]:
    """Resample peak envelope to a different bar count (max-pool down / linear up)."""
    if not peaks or num_bars <= 0:
        return []
    if len(peaks) == num_bars:
        return list(peaks)
    src = np.asarray(peaks, dtype=np.float64)
    if num_bars < len(src):
        edges = np.linspace(0, len(src), num_bars + 1).astype(int)
        out = []
        for i in range(num_bars):
            chunk = src[edges[i] : max(edges[i] + 1, edges[i + 1])]
            out.append(float(np.max(chunk)) if chunk.size else 0.0)
        return out
    x_old = np.linspace(0.0, 1.0, len(src))
    x_new = np.linspace(0.0, 1.0, num_bars)
    return np.interp(x_new, x_old, src).tolist()


def load_analysis(file_path: str) -> AnalysisCacheEntry | None:
    path = _cache_path(file_path)
    if path is not None and path.is_file():
        entry = _load_npz(path, file_path)
        if entry is not None:
            return entry
    # Also try legacy fingerprint without matching mtime (scan by digest prefix)
    entry = _load_by_digest(file_path, folder=_cache_dir())
    if entry is not None:
        return entry
    # Fall back to the global app cache when a project cache is active
    if _cache_dir_override is not None:
        global_dir = get_config_path("analysis_cache")
        if global_dir.is_dir():
            entry = _load_by_digest(file_path, folder=global_dir)
            if entry is not None:
                return entry
            fp = _file_fingerprint(file_path)
            if fp is not None:
                digest, mtime, size = fp
                candidate = global_dir / f"{digest}_{int(mtime)}_{size}.npz"
                if candidate.is_file():
                    return _load_npz(candidate, file_path)
    return None


def _load_npz(path: Path, file_path: str) -> AnalysisCacheEntry | None:
    try:
        with np.load(path, allow_pickle=False) as data:
            peaks = data["peaks"].astype(np.float64).tolist()
            bpm_raw = float(data["bpm"]) if "bpm" in data.files else -1.0
            bars = int(data["num_bars"]) if "num_bars" in data.files else len(peaks)
            # Validate fingerprint still matches file
            fp = _file_fingerprint(file_path)
            if fp is None:
                return None
            _, mtime, size = fp
            if "mtime" in data.files and abs(float(data["mtime"]) - mtime) > 0.5:
                return None
            if "size" in data.files and int(data["size"]) != size:
                return None
            bpm = None if bpm_raw < 0 else bpm_raw
            lufs = None
            if "lufs" in data.files:
                lufs_raw = float(data["lufs"])
                if lufs_raw > -120.0:
                    lufs = lufs_raw
            if not peaks:
                return None
            return AnalysisCacheEntry(bpm=bpm, peaks=peaks, num_bars=bars, lufs=lufs)
    except Exception:
        return None


def _load_by_digest(file_path: str, folder: Path | None = None) -> AnalysisCacheEntry | None:
    fp = _file_fingerprint(file_path)
    if fp is None:
        return None
    digest, mtime, size = fp
    search_dir = folder if folder is not None else _cache_dir()
    matches = sorted(
        search_dir.glob(f"{digest}_*.npz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in matches:
        try:
            with np.load(path, allow_pickle=False) as data:
                if "mtime" in data.files and abs(float(data["mtime"]) - mtime) > 0.5:
                    continue
                if "size" in data.files and int(data["size"]) != size:
                    continue
                peaks = data["peaks"].astype(np.float64).tolist()
                bpm_raw = float(data["bpm"]) if "bpm" in data.files else -1.0
                bars = int(data["num_bars"]) if "num_bars" in data.files else len(peaks)
                if not peaks:
                    continue
                lufs = None
                if "lufs" in data.files:
                    lufs_raw = float(data["lufs"])
                    if lufs_raw > -120.0:
                        lufs = lufs_raw
                return AnalysisCacheEntry(
                    bpm=None if bpm_raw < 0 else bpm_raw,
                    peaks=peaks,
                    num_bars=bars,
                    lufs=lufs,
                )
        except Exception:
            continue
    return None


def save_analysis(
    file_path: str,
    *,
    bpm: float | None,
    peaks: list[float],
    num_bars: int = CANONICAL_WAVEFORM_BARS,
    lufs: float | None = None,
) -> bool:
    path = _cache_path(file_path)
    fp = _file_fingerprint(file_path)
    if path is None or fp is None or not peaks:
        return False
    _, mtime, size = fp
    try:
        np.savez_compressed(
            path,
            peaks=np.asarray(peaks, dtype=np.float32),
            bpm=np.float64(-1.0 if bpm is None else float(bpm)),
            num_bars=np.int32(num_bars),
            mtime=np.float64(mtime),
            size=np.int64(size),
            lufs=np.float64(-200.0 if lufs is None else float(lufs)),
        )
        # Drop older cache files for same digest
        digest = fp[0]
        for old in _cache_dir().glob(f"{digest}_*.npz"):
            if old != path:
                try:
                    old.unlink()
                except OSError:
                    pass
        return True
    except Exception as exc:
        print(f"Error saving analysis cache: {exc}")
        return False


def has_waveform_cache(file_path: str) -> bool:
    entry = load_analysis(file_path)
    return entry is not None and bool(entry.peaks)


def has_loudness_cache(file_path: str) -> bool:
    entry = load_analysis(file_path)
    return entry is not None and entry.lufs is not None


def load_bpm(file_path: str) -> float | None:
    entry = load_analysis(file_path)
    return entry.bpm if entry else None


def load_peaks(file_path: str, num_bars: int | None = None) -> list[float] | None:
    entry = load_analysis(file_path)
    if entry is None or not entry.peaks:
        return None
    if num_bars is None or num_bars == len(entry.peaks):
        return list(entry.peaks)
    return resample_peaks(entry.peaks, num_bars)


def relocate_analysis(old_path: str, new_path: str) -> bool:
    """Copy analysis entry from old file path key to new path key."""
    entry = load_analysis(old_path)
    if entry is None or not entry.peaks:
        return False
    return save_analysis(
        new_path,
        bpm=entry.bpm,
        peaks=entry.peaks,
        num_bars=entry.num_bars,
        lufs=entry.lufs,
    )


def export_analysis_to_dir(file_path: str, dest_dir: Path | str) -> bool:
    """Write current analysis for file_path into dest_dir (using that dir as cache)."""
    entry = load_analysis(file_path)
    if entry is None or not entry.peaks:
        return False
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    previous = _cache_dir_override
    try:
        set_cache_dir(dest)
        return save_analysis(
            file_path,
            bpm=entry.bpm,
            peaks=entry.peaks,
            num_bars=entry.num_bars,
            lufs=entry.lufs,
        )
    finally:
        set_cache_dir(previous)


def copy_cache_file_for_path(file_path: str, dest_dir: Path | str) -> bool:
    """Copy the on-disk NPZ for file_path into dest_dir if present.

    Skip rewrite when the file already lives in ``dest_dir``. Copying a cache
    onto itself raises ``SameFileError`` and the old fallback recompressed
    every NPZ on Save — a GIL stall that underruns air.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    src = _cache_path(file_path)
    if src is not None and src.is_file():
        target = dest / src.name
        try:
            if target.exists() and os.path.samefile(src, target):
                return True
        except OSError:
            pass
        try:
            shutil.copy2(src, target)
            return True
        except shutil.SameFileError:
            return True
        except OSError:
            pass
    return export_analysis_to_dir(file_path, dest)
