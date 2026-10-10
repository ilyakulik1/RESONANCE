"""Equalizer panel (next to control) — stems + parametric EQ."""

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
from app.stems import STEM_IDS, STEM_INSTRUMENTAL, STEM_LABELS, STEM_VOCALS
from app.ui.widgets.section_header import SectionHeader
from app.ui.widgets.segment_button import SegmentButtonGroup

from app.widgets.parametric_eq_view import ParametricEqView
from app.widgets.stem_fader import StemFader


class FilePropertiesPanel(QFrame):
    """Per-track EQ / stems next to the control panel (air spectrum)."""

    eqChanged = pyqtSignal()
    stemSelectionChanged = pyqtSignal(str)
    stemGainChanged = pyqtSignal(str, float)  # stem_id, gain_db

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePropertiesPanel")
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._stem_loading = False
        self._stems_enabled = False
        self._selected_stem = STEM_VOCALS

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(4)

        self.stem_header = SectionHeader("STEMS")
        root.addWidget(self.stem_header)

        self.stem_row = QWidget()
        self.stem_row.setObjectName("stemSelectRow")
        stem_layout = QHBoxLayout(self.stem_row)
        stem_layout.setContentsMargins(4, 0, 4, 2)
        stem_layout.setSpacing(8)
        self.stem_selector = SegmentButtonGroup(
            [(STEM_VOCALS, STEM_LABELS[STEM_VOCALS]), (STEM_INSTRUMENTAL, STEM_LABELS[STEM_INSTRUMENTAL])],
            self,
        )
        self.stem_selector.valueChanged.connect(self._on_stem_selected)
        stem_layout.addWidget(self.stem_selector, 0)
        stem_layout.addStretch(1)
        root.addWidget(self.stem_row)

        root.addWidget(SectionHeader("EQUALIZER"))

        eq_row = QWidget()
        eq_row.setObjectName("eqWithFaderRow")
        eq_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        eq_row_layout = QHBoxLayout(eq_row)
        eq_row_layout.setContentsMargins(0, 0, 0, 0)
        eq_row_layout.setSpacing(6)

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

        self.stem_fader = StemFader(self)
        self.stem_fader.gainChanged.connect(self._on_stem_fader)
        self.stem_fader.set_caption("VOX")

        eq_row_layout.addWidget(self.eq_body, 1)
        eq_row_layout.addWidget(self.stem_fader, 0)
        root.addWidget(eq_row, 1)

        self.set_stems_enabled(False)

    def _on_stem_selected(self, value: str) -> None:
        if self._stem_loading:
            return
        if value not in STEM_IDS:
            return
        self._selected_stem = value
        self.stem_fader.set_caption("VOX" if value == STEM_VOCALS else "INST")
        self.stemSelectionChanged.emit(value)

    def _on_stem_fader(self, gain_db: float) -> None:
        if self._stem_loading or not self._stems_enabled:
            return
        self.stemGainChanged.emit(self._selected_stem, float(gain_db))

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

    def stems_enabled(self) -> bool:
        return self._stems_enabled

    def selected_stem(self) -> str:
        return self._selected_stem

    def set_stems_enabled(self, enabled: bool) -> None:
        self._stems_enabled = bool(enabled)
        self.stem_header.setVisible(self._stems_enabled)
        self.stem_row.setVisible(self._stems_enabled)
        self.stem_fader.setVisible(self._stems_enabled)
        if not self._stems_enabled:
            self.stem_fader.set_enabled_fader(False)
        else:
            self.stem_fader.set_enabled_fader(True)

    def set_stem_ui(
        self,
        *,
        selected: str,
        gain_db: float,
    ) -> None:
        self._stem_loading = True
        try:
            stem = selected if selected in STEM_IDS else STEM_VOCALS
            self._selected_stem = stem
            self.stem_selector.set_value(stem)
            self.stem_fader.set_caption("VOX" if stem == STEM_VOCALS else "INST")
            self.stem_fader.set_gain_db(float(gain_db))
        finally:
            self._stem_loading = False

    def set_stem_gain_db(self, gain_db: float) -> None:
        self._stem_loading = True
        try:
            self.stem_fader.set_gain_db(float(gain_db))
        finally:
            self._stem_loading = False


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
            ("lufs", "LUFS"),
            ("gain", "GAIN"),
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

    def set_file(
        self,
        path: str | None,
        *,
        bpm: float | None = None,
        lufs: float | None = None,
        gain_db: float | None = None,
    ) -> None:
        self._file_path = path
        info = read_audio_file_info(path)
        if bpm is not None and bpm > 0:
            info["bpm"] = (
                f"{bpm:.1f}".rstrip("0").rstrip(".")
                if isinstance(bpm, float)
                else str(bpm)
            )
        if lufs is not None:
            info["lufs"] = f"{float(lufs):.1f}"
        if gain_db is not None:
            info["gain"] = f"{float(gain_db):+.1f}"
        for key, label in self._meta_labels.items():
            label.setText(info.get(key, "—"))

    def set_bpm(self, bpm: float | None) -> None:
        if bpm is None or bpm <= 0:
            self._meta_labels["bpm"].setText("—")
        else:
            text = f"{bpm:.1f}".rstrip("0").rstrip(".")
            self._meta_labels["bpm"].setText(text)

    def set_loudness(self, lufs: float | None, gain_db: float | None = None) -> None:
        if lufs is None:
            self._meta_labels["lufs"].setText("—")
        else:
            self._meta_labels["lufs"].setText(f"{float(lufs):.1f}")
        if gain_db is None:
            self._meta_labels["gain"].setText("—")
        else:
            self._meta_labels["gain"].setText(f"{float(gain_db):+.1f}")
