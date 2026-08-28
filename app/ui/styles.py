from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from app.ui.paths import ui_resources_dir
from app.ui.tokens import apply_tokens

_STYLES_DIR = ui_resources_dir() / "styles"


def load_stylesheet(name: str) -> str:
    path = _STYLES_DIR / f"{name}.qss"
    return apply_tokens(path.read_text(encoding="utf-8"))


def load_application_theme() -> str:
    parts = (
        "base",
        "section_header",
        "main_window",
        "control_panel",
        "file_properties",
        "playlist",
        "file_browser",
        "video_mixer",
    )
    return "\n\n".join(load_stylesheet(part) for part in parts)


def apply_application_theme(app: QApplication) -> None:
    app.setStyleSheet(load_application_theme())
