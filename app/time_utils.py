import os
import wave

from app.constants import ANALYSIS_MAX_DURATION_MS, PYDUB_AVAILABLE


def format_time_ms(ms: int | None) -> str:
    if ms is None or ms < 0:
        return "--:--"

    total_seconds = int(ms) // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def get_audio_duration_ms(file_path: str) -> int | None:
    if not file_path or not os.path.isfile(file_path):
        return None

    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(file_path)
        if audio is not None and audio.info is not None:
            length = getattr(audio.info, "length", None)
            if length and length > 0:
                return int(length * 1000)
    except Exception:
        pass

    if PYDUB_AVAILABLE:
        try:
            from pydub import AudioSegment

            return len(AudioSegment.from_file(file_path))
        except Exception:
            pass

    if file_path.lower().endswith(".wav"):
        try:
            with wave.open(file_path, "rb") as audio_file:
                frames = audio_file.getnframes()
                rate = audio_file.getframerate()
                if rate > 0:
                    return int(frames / rate * 1000)
        except Exception:
            pass

    return None


def allows_bpm_loudness_analysis(
    file_path: str | None = None,
    duration_ms: int | None = None,
) -> bool:
    """False for tracks longer than ANALYSIS_MAX_DURATION_MS (BPM/loudness skip)."""
    dur = duration_ms
    if dur is None and file_path:
        dur = get_audio_duration_ms(file_path)
    if dur is None:
        return True
    return int(dur) <= ANALYSIS_MAX_DURATION_MS
