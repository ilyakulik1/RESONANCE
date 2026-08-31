AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".aac")
MAX_PLAYLISTS = 3  # visible playlist columns
MAX_TABS_PER_COLUMN = 20
APP_NAME = "RESONANCE"
DEFAULT_FADE_MS = 1500
FADE_PRESETS_MS = (0, 200, 400, 800, 2000)
# Air / music (Control Panel FADE)
FADE_PRESET_SHORTCUTS: tuple[tuple[str, int], ...] = (
    ("Ctrl+Shift+1", 0),
    ("Ctrl+Shift+2", 200),
    ("Ctrl+Shift+3", 400),
    ("Ctrl+Shift+4", 800),
    ("Ctrl+Shift+5", 2000),
)
# Video mixer scene crossfade (separate from air fade)
VIDEO_TRANSITION_PRESET_SHORTCUTS: tuple[tuple[str, int], ...] = (
    ("Ctrl+Alt+1", 0),
    ("Ctrl+Alt+2", 200),
    ("Ctrl+Alt+3", 400),
    ("Ctrl+Alt+4", 800),
    ("Ctrl+Alt+5", 2000),
)
# Skip BPM / loudness analysis for tracks longer than this.
ANALYSIS_MAX_DURATION_MS = 10 * 60 * 1000

try:
    from pydub import AudioSegment
    import numpy as np

    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    print("Warning: pydub not installed. Install with: pip install pydub numpy")


def fade_preset_label(ms: int) -> str:
    if ms <= 0:
        return "CUT"
    if ms >= 1000 and ms % 1000 == 0:
        return f"{ms // 1000}s"
    return str(ms)
