AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".aac")
MAX_PLAYLISTS = 3  # visible playlist columns
MAX_TABS_PER_COLUMN = 20
APP_NAME = "RESONANCE"
# Skip BPM / loudness analysis for tracks longer than this.
ANALYSIS_MAX_DURATION_MS = 10 * 60 * 1000

try:
    from pydub import AudioSegment
    import numpy as np

    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    print("Warning: pydub not installed. Install with: pip install pydub numpy")
