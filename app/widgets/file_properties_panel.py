"""Equalizer panel (next to control) + compact preview metadata bar."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.eq_state import EqState
from app.spectrum_feeder import read_audio_file_info
from app.ui.widgets.section_header import SectionHeader
from app.widgets.parametric_eq_view import ParametricEqView


class FilePropertiesPanel(QFrame):
    """Equalizer next to the control panel (air EQ / spectrum)."""

    eqChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePropertiesPanel")
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(4)
        root.addWidget(SectionHeader("EQUALIZER"))

        self.eq_body = QWidget()
        self.eq_body.setObjectName("eqSectionBody")
        self.eq_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        eq_body_layout = QVBoxLayout(self.eq_body)
        eq_body_layout.setContentsMargins(0, 0, 0, 0)
        eq_body_layout.setSpacing(2)

        self.eq_view = ParametricEqView(EqState(), self, compact=True)
        self.eq_view.setMinimumHeight(200)
        self.eq_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.eq_view.bandsChanged.connect(self.eqChanged.emit)
        self.eq_view.bypassChanged.connect(lambda _c: self.eqChanged.emit())
        eq_body_layout.addWidget(self.eq_view, 1)
        root.addWidget(self.eq_body, 1)

    def eq_state(self) -> EqState:
        return self.eq_view.state()

    def set_eq_state(self, state: EqState) -> None:
        self.eq_view.set_state(state)

    def set_spectrum(self, freqs, db) -> None:
        self.eq_view.set_spectrum(freqs, db)

    def is_bypassed(self) -> bool:
        return bool(self.eq_view.state().bypass)

    def set_bypassed(self, bypassed: bool) -> None:
        self.eq_view.set_bypass(bool(bypassed))


class PreviewPropertiesPanel(QFrame):
    """Compact metadata chips shown on the preview track-info row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPropertiesPanel")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        self._file_path: str | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._meta_labels: dict[str, QLabel] = {}
        chips = (
            ("format", "FMT"),
            ("duration", "TIME"),
            ("sample_rate", "SR"),
            ("bitrate", "BR"),
            ("channels", "CH"),
            ("bpm", "BPM"),
        )
        for key, title in chips:
            chip = QFrame()
            chip.setObjectName("previewMetaChip")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(4, 1, 4, 1)
            chip_layout.setSpacing(3)
            title_lbl = QLabel(title)
            title_lbl.setObjectName("propertiesMetaKey")
            value_lbl = QLabel("—")
            value_lbl.setObjectName("propertiesMetaValue")
            value_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            chip_layout.addWidget(title_lbl)
            chip_layout.addWidget(value_lbl)
            root.addWidget(chip)
            self._meta_labels[key] = value_lbl

    def set_file(self, path: str | None, *, bpm: float | None = None) -> None:
        self._file_path = path
        info = read_audio_file_info(path)
        if bpm is not None and bpm > 0:
            info["bpm"] = (
                f"{bpm:.1f}".rstrip("0").rstrip(".")
                if isinstance(bpm, float)
                else str(bpm)
            )
        for key, label in self._meta_labels.items():
            label.setText(info.get(key, "—"))

    def set_bpm(self, bpm: float | None) -> None:
        if bpm is None or bpm <= 0:
            self._meta_labels["bpm"].setText("—")
        else:
            text = f"{bpm:.1f}".rstrip("0").rstrip(".")
            self._meta_labels["bpm"].setText(text)
