"""File pickers that do not use the native OS panel.

macOS NSOpenPanel deactivates the app and can stall QAudioSink / CoreAudio
for the duration of the sheet. Qt's own dialog keeps air running.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFileDialog, QWidget

_DIRECTORY_OPTIONS = (
    QFileDialog.Option.ShowDirsOnly
    | QFileDialog.Option.DontUseNativeDialog
)


def pick_existing_directory(
    parent: QWidget | None, title: str, start: str = ""
) -> str:
    return QFileDialog.getExistingDirectory(
        parent, title, start, _DIRECTORY_OPTIONS
    )
