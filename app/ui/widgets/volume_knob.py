"""Rotary master-volume knob (0–100%)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QDial, QLabel, QMenu, QSizePolicy, QVBoxLayout, QWidget


class VolumeKnob(QWidget):
    """Console-style rotary control for master air volume."""

    volumeChanged = pyqtSignal(float)  # 0.0 … 1.0
    midiLearnRequested = pyqtSignal()
    midiLearnCancelRequested = pyqtSignal()
    midiClearRequested = pyqtSignal()

    def __init__(self, parent=None, *, value: float = 0.7):
        super().__init__(parent)
        self.setObjectName("volumeKnob")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._loading = False
        self._learning = False
        self._midi_label: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 0, 2, 0)
        root.setSpacing(1)

        self.caption = QLabel("MASTER")
        self.caption.setObjectName("volumeKnobCaption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.dial = QDial(self)
        self.dial.setObjectName("volumeKnobDial")
        self.dial.setRange(0, 100)
        self.dial.setNotchesVisible(True)
        self.dial.setWrapping(False)
        self.dial.setFixedSize(26, 26)
        self.dial.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dial.valueChanged.connect(self._on_dial)
        self.dial.installEventFilter(self)
        self.dial.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dial.customContextMenuRequested.connect(
            lambda pos: self._show_midi_menu(self.dial.mapToGlobal(pos))
        )

        self.value_label = QLabel("70%")
        self.value_label.setObjectName("volumeKnobValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        root.addWidget(self.caption, 0)
        root.addWidget(self.dial, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.value_label, 0)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._show_midi_menu(self.mapToGlobal(pos))
        )
        self._refresh_tooltip()
        self.set_volume(value)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.dial and event.type() == QEvent.Type.MouseButtonDblClick:
            if isinstance(event, QMouseEvent) and event.button() == Qt.MouseButton.LeftButton:
                self.set_volume(1.0)
                if not self._loading:
                    self.volumeChanged.emit(1.0)
                return True
        return super().eventFilter(obj, event)

    def _show_midi_menu(self, global_pos) -> None:
        menu = QMenu(self)
        if self._learning:
            menu.addAction("Cancel MIDI Learn", self.midiLearnCancelRequested.emit)
        else:
            menu.addAction("MIDI Learn…", self.midiLearnRequested.emit)
            clear = menu.addAction("Clear MIDI mapping")
            clear.setEnabled(self._midi_label is not None)
            clear.triggered.connect(self.midiClearRequested.emit)
        menu.exec(global_pos)

    def set_learning(self, learning: bool) -> None:
        self._learning = bool(learning)
        self.setProperty("midiLearn", self._learning)
        self.caption.setText("LEARN" if self._learning else "MASTER")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self._refresh_tooltip()
        self.update()

    def set_midi_binding_label(self, label: str | None) -> None:
        self._midi_label = label or None
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        if self._learning:
            text = "Move a MIDI knob to assign master volume"
        elif self._midi_label:
            text = f"Master air volume · MIDI {self._midi_label} · double-click for 100%"
        else:
            text = "Master air volume · right-click to MIDI Learn · double-click for 100%"
        self.setToolTip(text)
        self.dial.setToolTip(text)

    def _on_dial(self, value: int) -> None:
        volume = max(0.0, min(1.0, int(value) / 100.0))
        self.value_label.setText(f"{int(round(volume * 100))}%")
        if self._loading:
            return
        self.volumeChanged.emit(volume)

    def volume(self) -> float:
        return max(0.0, min(1.0, self.dial.value() / 100.0))

    def set_volume(self, volume: float) -> None:
        self._loading = True
        try:
            value = max(0.0, min(1.0, float(volume)))
            self.dial.setValue(int(round(value * 100)))
            self.value_label.setText(f"{int(round(value * 100))}%")
        finally:
            self._loading = False
