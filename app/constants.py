AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".aac")
MAX_PLAYLISTS = 3
APP_NAME = "KULIK Player"

try:
    from pydub import AudioSegment
    import numpy as np

    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    print("Warning: pydub not installed. Install with: pip install pydub numpy")
