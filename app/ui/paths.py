from __future__ import annotations

import sys
from pathlib import Path

_UI_DIR = Path(__file__).resolve().parent


def ui_resources_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "ui" / "resources"
    return _UI_DIR / "resources"
