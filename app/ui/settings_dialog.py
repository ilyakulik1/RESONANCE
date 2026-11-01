"""Application settings. Currently the pie-menu key."""

from __future__ import annotations

from PyQt6.QtCore import QKeyCombination, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.tokens import get_token

DEFAULT_PIE_MENU_KEY = Qt.Key.Key_QuoteLeft
_MODIFIER_KEYS = {
    int(Qt.Key.Key_Control),
    int(Qt.Key.Key_Shift),
    int(Qt.Key.Key_Alt),
    int(Qt.Key.Key_Meta),
    int(Qt.Key.Key_AltGr),
    int(Qt.Key.Key_unknown),
}


def pie_key_to_config(key: int) -> str:
    return _sequence(key).toString(QKeySequence.SequenceFormat.PortableText)


def pie_key_label(key: int) -> str:
    text = _sequence(key).toString(QKeySequence.SequenceFormat.NativeText)
    return text or "—"


def pie_key_from_config(text: str) -> int:
    seq = QKeySequence.fromString(text.strip(), QKeySequence.SequenceFormat.PortableText)
    if seq.isEmpty():
        seq = QKeySequence.fromString(text.strip(), QKeySequence.SequenceFormat.NativeText)
    if seq.isEmpty() or seq.count() != 1:
        return int(DEFAULT_PIE_MENU_KEY)
    combo = seq[0]
    if combo.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
        return int(DEFAULT_PIE_MENU_KEY)
    key = int(combo.key())
    if key in (
        int(Qt.Key.Key_Space),
        int(Qt.Key.Key_Escape),
        int(Qt.Key.Key_unknown),
        0,
    ):
        return int(DEFAULT_PIE_MENU_KEY)
    return key


def _sequence(key: int) -> QKeySequence:
    return QKeySequence(QKeyCombination(Qt.Key(int(key))))


class SettingsDialog(QDialog):
    """Assign the key that opens the control pie menu."""

    captureChanged = pyqtSignal(bool)

    def __init__(self, pie_key: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(360)
        self._key = int(pie_key) or int(DEFAULT_PIE_MENU_KEY)
        self._capturing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        title = QLabel("PIE MENU")
        title.setObjectName("sectionLabel")
        root.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._key_button = QPushButton(pie_key_label(self._key))
        self._key_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._key_button.setAutoDefault(False)
        self._key_button.setDefault(False)
        self._key_button.setMinimumWidth(88)
        self._key_button.clicked.connect(self._start_capture)
        row.addWidget(self._key_button, 0)

        hint = QLabel("Press the button, then press a key.")
        hint.setObjectName("sectionLabel")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        root.addLayout(row)

        self._status = QLabel("Space stays ON AIR. Escape stays Stop.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {get_token('colors.text_muted', '#8c8c92')};")
        root.addWidget(self._status)

        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.setDefault(False)
        close.clicked.connect(self.accept)
        root.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def pie_key(self) -> int:
        return int(self._key)

    def _start_capture(self) -> None:
        self._set_capturing(True)
        self._key_button.setText("Press a key…")
        self._status.setText("Space stays ON AIR. Escape stays Stop.")
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_capturing(self, capturing: bool) -> None:
        if self._capturing == capturing:
            return
        self._capturing = capturing
        self.captureChanged.emit(capturing)
        if not capturing:
            self._key_button.setText(pie_key_label(self._key))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._capturing:
            if event.key() == Qt.Key.Key_Escape and not event.isAutoRepeat():
                self.accept()
                return
            super().keyPressEvent(event)
            return
        if event.isAutoRepeat():
            event.accept()
            return
        key = int(event.key())
        if key in _MODIFIER_KEYS:
            event.accept()
            return
        if key == int(Qt.Key.Key_Escape):
            self._set_capturing(False)
            event.accept()
            return
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key == int(Qt.Key.Key_Space) or mods != Qt.KeyboardModifier.NoModifier:
            self._status.setText("Use one key, without Space, Escape, or modifiers.")
            event.accept()
            return
        self._key = key
        self._set_capturing(False)
        event.accept()

    def reject(self) -> None:  # noqa: N802
        # Closing the window keeps the key the user just chose.
        self._set_capturing(False)
        self.accept()
