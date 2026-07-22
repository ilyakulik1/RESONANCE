from pathlib import Path


def get_config_path(filename: str) -> Path:
    config_dir = Path.home() / ".simple_audio_player"
    config_dir.mkdir(exist_ok=True)
    return config_dir / filename
