from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QCloseEvent
from PyQt6.QtWidgets import QVBoxLayout, QWidget, QApplication, QLabel

from app.video_mixer.gl_widget import OutputGLWidget

if TYPE_CHECKING:
    from app.video_mixer.media_store import MediaStore
    from app.video_mixer.model import MixerModel


class OutputWindow(QWidget):
    """Fullscreen (or windowed) physical monitor output."""

    def __init__(self, model: MixerModel, media_store: MediaStore, parent=None):
        super().__init__(parent)
        self.model = model
        self.media_store = media_store
        self.setWindowTitle("Video Mixer Output")
        self.setObjectName("videoMixerOutputWindow")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.gl_widget = OutputGLWidget(model, media_store, self)
        layout.addWidget(self.gl_widget)

        self._hint = QLabel("Esc — exit fullscreen", self)
        self._hint.setObjectName("outputHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet("color: #9f9f9f; background: transparent; font-size: 11px;")
        self._hint.hide()

    def open_on_screen(self, screen_index: int | None = None) -> None:
        app = QApplication.instance()
        if app is None:
            return
        screens = app.screens()
        if not screens:
            return

        if screen_index is None:
            # Prefer a non-primary screen when available
            primary = app.primaryScreen()
            chosen = None
            for screen in screens:
                if screen is not primary:
                    chosen = screen
                    break
            if chosen is None:
                chosen = screens[0]
        else:
            if screen_index < 0 or screen_index >= len(screens):
                return
            chosen = screens[screen_index]

        self.model.output_screen_name = chosen.name()
        geo = chosen.geometry()
        self.setGeometry(geo)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        if self.isVisible():
            self.gl_widget.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.showNormal()
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()

    def release(self) -> None:
        self.gl_widget.release()
