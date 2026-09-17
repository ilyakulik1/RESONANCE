"""Vocal / instrumental stem separation and on-disk cache."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import get_config_path

STEM_VOCALS = "vocals"
STEM_INSTRUMENTAL = "instrumental"
STEM_IDS = (STEM_VOCALS, STEM_INSTRUMENTAL)
STEM_LABELS = {
    STEM_VOCALS: "Vocal",
    STEM_INSTRUMENTAL: "Inst",
}

# UVR MDX-Net Inst HQ 5 — primary separation backend (audio-separator).
MDX_MODEL_FILENAME = "UVR-MDX-NET-Inst_HQ_5.onnx"
MDX_MODEL_TAG = "mdx_inst_hq5"

_stems_dir_override: Path | None = None


def set_stems_dir(path: Path | str | None) -> None:
    """Use a project-local stems folder, or None for the global app cache."""
    global _stems_dir_override
    if path is None:
        _stems_dir_override = None
        return
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    _stems_dir_override = resolved


def get_stems_dir() -> Path:
    if _stems_dir_override is not None:
        _stems_dir_override.mkdir(parents=True, exist_ok=True)
        return _stems_dir_override
    path = get_config_path("stems_cache")
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


def stems_bundle_dir(file_path: str) -> Path | None:
    fp = _file_fingerprint(file_path)
    if fp is None:
        return None
    digest, mtime, size = fp
    return get_stems_dir() / f"{MDX_MODEL_TAG}_{digest}_{int(mtime)}_{size}"


def models_dir() -> Path:
    path = get_config_path("stems_models")
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class StemPaths:
    vocals: str
    instrumental: str

    def as_dict(self) -> dict[str, str]:
        return {
            STEM_VOCALS: self.vocals,
            STEM_INSTRUMENTAL: self.instrumental,
        }

    def exists(self) -> bool:
        return os.path.isfile(self.vocals) and os.path.isfile(self.instrumental)


def stem_paths_for(file_path: str) -> StemPaths | None:
    bundle = stems_bundle_dir(file_path)
    if bundle is None:
        return None
    return StemPaths(
        vocals=str(bundle / f"{STEM_VOCALS}.wav"),
        instrumental=str(bundle / f"{STEM_INSTRUMENTAL}.wav"),
    )


def is_readable_stem_wav(path: str | None) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) < 44:
            return False
        import soundfile as sf

        info = sf.info(path)
        return int(info.frames) > 0 and int(info.samplerate) > 0
    except Exception:
        return False


def load_cached_stems(file_path: str) -> StemPaths | None:
    paths = stem_paths_for(file_path)
    if paths is None:
        return None
    if not (
        is_readable_stem_wav(paths.vocals) and is_readable_stem_wav(paths.instrumental)
    ):
        return None
    return paths


def _worker_script() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "separate_stems_worker.py"


def separate_vocals_instrumental(
    file_path: str,
    *,
    progress_callback=None,
    force: bool = False,
) -> StemPaths:
    """Separate a track into vocals + instrumental using MDX-Net Inst HQ 5.

    Runs in a subprocess so ONNX/torch do not steal the UI/audio GIL.
    """
    import shutil

    cached = None if force else load_cached_stems(file_path)
    if cached is not None:
        if progress_callback:
            progress_callback(1.0)
        return cached

    paths = stem_paths_for(file_path)
    if paths is None:
        raise FileNotFoundError(file_path)

    bundle = Path(paths.vocals).parent
    if force and bundle.exists():
        shutil.rmtree(bundle, ignore_errors=True)
    bundle.mkdir(parents=True, exist_ok=True)

    script = _worker_script()
    if not script.is_file():
        raise RuntimeError(f"Stem worker script missing: {script}")

    if progress_callback:
        progress_callback(0.02)

    cmd = [
        sys.executable,
        str(script),
        "--input",
        os.path.abspath(file_path),
        "--vocals-out",
        paths.vocals,
        "--instrumental-out",
        paths.instrumental,
        "--model-dir",
        str(models_dir()),
        "--model-filename",
        MDX_MODEL_FILENAME,
    ]

    # Nice the child on Unix so air playback keeps priority.
    def _nice_child() -> None:
        try:
            os.nice(10)
        except OSError:
            pass

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=_nice_child if hasattr(os, "nice") else None,
    )
    assert proc.stdout is not None
    last_error = "Stem separation failed"
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "progress" in payload and progress_callback:
                try:
                    progress_callback(float(payload["progress"]))
                except (TypeError, ValueError):
                    pass
            if payload.get("error"):
                last_error = str(payload["error"])
        stderr = ""
        if proc.stderr is not None:
            stderr = proc.stderr.read() or ""
        code = proc.wait(timeout=60 * 60)
    finally:
        if proc.poll() is None:
            proc.kill()

    if code != 0:
        detail = last_error
        if stderr.strip():
            detail = f"{detail}\n{stderr.strip()[-500:]}"
        raise RuntimeError(detail)

    if not (
        is_readable_stem_wav(paths.vocals) and is_readable_stem_wav(paths.instrumental)
    ):
        raise RuntimeError("Stem WAV files were not written correctly")

    if progress_callback:
        progress_callback(1.0)
    return paths


@dataclass
class StemChannelState:
    """Per-stem EQ + relative gain (independent of master track gain)."""

    gain_db: float = 0.0
    eq: dict | None = None

    def to_dict(self) -> dict:
        data: dict = {"gain_db": round(float(self.gain_db), 3)}
        if isinstance(self.eq, dict) and (self.eq.get("bands") or self.eq.get("bypass")):
            data["eq"] = copy.deepcopy(self.eq)
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> StemChannelState:
        if not isinstance(data, dict):
            return cls()
        eq = data.get("eq")
        return cls(
            gain_db=float(data.get("gain_db", 0.0)),
            eq=dict(eq) if isinstance(eq, dict) else None,
        )


@dataclass
class TrackStemState:
    """Stem files + mix settings for one source track."""

    vocals_path: str
    instrumental_path: str
    vocals: StemChannelState
    instrumental: StemChannelState
    selected: str = STEM_VOCALS

    def path_for(self, stem_id: str) -> str:
        if stem_id == STEM_INSTRUMENTAL:
            return self.instrumental_path
        return self.vocals_path

    def channel(self, stem_id: str) -> StemChannelState:
        if stem_id == STEM_INSTRUMENTAL:
            return self.instrumental
        return self.vocals

    def files_ok(self) -> bool:
        return is_readable_stem_wav(self.vocals_path) and is_readable_stem_wav(
            self.instrumental_path
        )

    def to_dict(self) -> dict:
        return {
            "vocals_path": self.vocals_path,
            "instrumental_path": self.instrumental_path,
            "selected": self.selected if self.selected in STEM_IDS else STEM_VOCALS,
            STEM_VOCALS: self.vocals.to_dict(),
            STEM_INSTRUMENTAL: self.instrumental.to_dict(),
        }

    @classmethod
    def from_paths(cls, paths: StemPaths) -> TrackStemState:
        return cls(
            vocals_path=paths.vocals,
            instrumental_path=paths.instrumental,
            vocals=StemChannelState(),
            instrumental=StemChannelState(),
            selected=STEM_VOCALS,
        )

    @classmethod
    def from_dict(cls, data: dict | None) -> TrackStemState | None:
        if not isinstance(data, dict):
            return None
        vocals_path = str(data.get("vocals_path") or "").strip()
        instrumental_path = str(data.get("instrumental_path") or "").strip()
        if not vocals_path or not instrumental_path:
            return None
        if not (
            is_readable_stem_wav(vocals_path) and is_readable_stem_wav(instrumental_path)
        ):
            return None
        selected = str(data.get("selected") or STEM_VOCALS)
        if selected not in STEM_IDS:
            selected = STEM_VOCALS
        return cls(
            vocals_path=vocals_path,
            instrumental_path=instrumental_path,
            vocals=StemChannelState.from_dict(data.get(STEM_VOCALS)),
            instrumental=StemChannelState.from_dict(data.get(STEM_INSTRUMENTAL)),
            selected=selected,
        )
