from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import wave

from app.constants import PYDUB_AVAILABLE

_FFMPEG_PATH: str | None = None
_PYDUB_CONFIGURED = False


def find_ffmpeg() -> str | None:
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not None:
        return _FFMPEG_PATH

    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_PATH = found
        return found

    for candidate in (
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/local/bin/ffmpeg",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _FFMPEG_PATH = candidate
            return candidate

    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            _FFMPEG_PATH = bundled
            return bundled
    except ImportError:
        pass

    return None


def _configure_pydub() -> None:
    global _PYDUB_CONFIGURED
    if _PYDUB_CONFIGURED or not PYDUB_AVAILABLE:
        return

    from pydub import AudioSegment

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        AudioSegment.converter = ffmpeg
        ffprobe = ffmpeg.replace("ffmpeg", "ffprobe")
        if os.path.isfile(ffprobe):
            AudioSegment.ffprobe = ffprobe

    _PYDUB_CONFIGURED = True


def _resample(samples, orig_rate: int, target_rate: int):
    import numpy as np

    if orig_rate == target_rate or len(samples) == 0:
        return samples

    target_len = max(1, int(len(samples) * target_rate / orig_rate))
    indices = np.linspace(0, len(samples) - 1, target_len)
    return np.interp(indices, np.arange(len(samples)), samples).astype(np.float32)


def _samples_to_mono_float(raw: bytes, sample_width: int, channels: int):
    import numpy as np

    if sample_width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        return None

    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)

    return samples


def _load_wav(file_path: str, sample_rate: int, max_seconds: float):
    with wave.open(file_path, "rb") as audio_file:
        channels = audio_file.getnchannels()
        sample_width = audio_file.getsampwidth()
        frame_rate = audio_file.getframerate()
        if frame_rate <= 0:
            return None

        max_frames = min(audio_file.getnframes(), int(max_seconds * frame_rate))
        raw = audio_file.readframes(max_frames)

    samples = _samples_to_mono_float(raw, sample_width, channels)
    if samples is None:
        return None

    return _resample(samples, frame_rate, sample_rate)


def _load_with_ffmpeg(file_path: str, sample_rate: int, max_seconds: float):
    import numpy as np

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None

    cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        file_path,
        "-t",
        str(max_seconds),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        if proc.stderr:
            print(f"ffmpeg decode error for {file_path}: {proc.stderr.decode(errors='replace').strip()}")
        return None

    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _load_with_afconvert(file_path: str, sample_rate: int, max_seconds: float):
    if platform.system() != "Darwin":
        return None

    afconvert = shutil.which("afconvert") or "/usr/bin/afconvert"
    if not os.path.isfile(afconvert):
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        proc = subprocess.run(
            [
                afconvert,
                "-f",
                "WAVE",
                "-d",
                f"LEI16@{sample_rate}",
                "-c",
                "1",
                file_path,
                tmp_path,
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            if proc.stderr:
                print(
                    f"afconvert decode error for {file_path}: "
                    f"{proc.stderr.decode(errors='replace').strip()}"
                )
            return None

        return _load_wav(tmp_path, sample_rate, max_seconds)
    except Exception as exc:
        print(f"afconvert decode error for {file_path}: {exc}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _load_with_pydub(file_path: str, sample_rate: int, max_seconds: float):
    if not PYDUB_AVAILABLE:
        return None

    try:
        from pydub import AudioSegment

        _configure_pydub()
        audio = AudioSegment.from_file(file_path)
        if audio.channels > 1:
            audio = audio.set_channels(1)

        max_ms = int(max_seconds * 1000)
        if len(audio) > max_ms:
            audio = audio[:max_ms]

        if audio.frame_rate != sample_rate:
            audio = audio.set_frame_rate(sample_rate)

        samples = _samples_to_mono_float(
            audio.raw_data,
            audio.sample_width,
            1,
        )
        return samples
    except Exception as exc:
        print(f"pydub decode error for {file_path}: {exc}")
        return None


def load_mono_samples(
    file_path: str,
    *,
    sample_rate: int = 22050,
    max_seconds: float = 90,
):
    """Load mono PCM samples as float32 numpy array."""
    if not file_path or not os.path.exists(file_path):
        return None

    if file_path.lower().endswith(".wav"):
        try:
            samples = _load_wav(file_path, sample_rate, max_seconds)
            if samples is not None and len(samples) > 0:
                return samples
        except Exception as exc:
            print(f"WAV decode error for {file_path}: {exc}")

    samples = _load_with_ffmpeg(file_path, sample_rate, max_seconds)
    if samples is not None and len(samples) > 0:
        return samples

    samples = _load_with_afconvert(file_path, sample_rate, max_seconds)
    if samples is not None and len(samples) > 0:
        return samples

    return _load_with_pydub(file_path, sample_rate, max_seconds)
