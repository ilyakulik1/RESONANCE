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
from app.loudness import (
    GAIN_DB_MAX,
    GAIN_DB_MIN,
    TARGET_LUFS,
    TARGET_LUFS_MAX,
    TARGET_LUFS_MIN,
)
from app.spectrum_feeder import read_audio_file_info
from app.stems import STEM_IDS, STEM_INSTRUMENTAL, STEM_LABELS, STEM_VOCALS
from app.ui.widgets.float_value_stepper import FloatValueStepper
from app.ui.widgets.section_header import SectionHeader
from app.ui.widgets.segment_button import SegmentButtonGroup
from app.ui.widgets.volume_knob import VolumeKnob

from app.widgets.parametric_eq_view import ParametricEqView
from app.widgets.stem_fader import StemFader


class FilePropertiesPanel(QFrame):
    """Equalizer + per-track gain next to the control panel (air EQ / spectrum)."""

    eqChanged = pyqtSignal()
    gainChanged = pyqtSignal(float)
    targetLufsChanged = pyqtSignal(float)
    masterVolumeChanged = pyqtSignal(float)  # 0.0 … 1.0
    stemSelectionChanged = pyqtSignal(str)
    stemGainChanged = pyqtSignal(str, float)  # stem_id, gain_db

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePropertiesPanel")
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._gain_loading = False
        self._target_loading = False
        self._stem_loading = False
        self._master_loading = False
        self._stems_enabled = False
        self._selected_stem = STEM_VOCALS
        self._target_lufs = TARGET_LUFS
        self._measured_lufs: float | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(4)

        root.addWidget(SectionHeader("VOLUME"))
        volume_row = QWidget()
        volume_row.setObjectName("trackGainRow")
        volume_layout = QHBoxLayout(volume_row)
        volume_layout.setContentsMargins(4, 0, 4, 2)
        volume_layout.setSpacing(8)

        gain_col = QWidget()
        gain_layout = QVBoxLayout(gain_col)
        gain_layout.setContentsMargins(0, 0, 0, 0)
        gain_layout.setSpacing(4)

        gain_row = QWidget()
        gain_row_layout = QHBoxLayout(gain_row)
        gain_row_layout.setContentsMargins(0, 0, 0, 0)
        gain_row_layout.setSpacing(8)

        gain_caption = QLabel("GAIN")
        gain_caption.setObjectName("trackLufsLabel")
        self.gain_stepper = FloatValueStepper(
            self,
            GAIN_DB_MIN,
            GAIN_DB_MAX,
            0.0,
            step=0.5,
            decimals=1,
            value_width=56,
        )
        self.gain_spin = self.gain_stepper.spin_box()
        self.gain_spin.setSuffix(" dB")
        self.gain_spin.setToolTip(
            "Per-track gain. Auto-set from loudness analysis to match the target LUFS."
        )
        self.gain_stepper.valueChanged.connect(self._on_gain_stepper)

        target_caption = QLabel("TARGET")
        target_caption.setObjectName("trackLufsLabel")
        self.target_lufs_stepper = FloatValueStepper(
            self,
            TARGET_LUFS_MIN,
            TARGET_LUFS_MAX,
            TARGET_LUFS,
            step=0.5,
            decimals=1,
            value_width=56,
        )
        self.target_lufs_spin = self.target_lufs_stepper.spin_box()
        self.target_lufs_spin.setSuffix(" LUFS")
        self.target_lufs_spin.setToolTip(
            "Target integrated loudness. Analyzed tracks are gain-matched to this level."
        )
        self.target_lufs_stepper.valueChanged.connect(self._on_target_stepper)

        self.lufs_label = QLabel("LUFS —")
        self.lufs_label.setObjectName("trackLufsLabel")
        self.lufs_label.setToolTip("Measured integrated loudness → target")

        gain_row_layout.addWidget(gain_caption, 0)
        gain_row_layout.addWidget(self.gain_stepper, 0)
        gain_row_layout.addWidget(target_caption, 0)
        gain_row_layout.addWidget(self.target_lufs_stepper, 0)
        gain_row_layout.addWidget(self.lufs_label, 1)
        gain_layout.addWidget(gain_row)
        gain_layout.addStretch(1)

        self.master_knob = VolumeKnob(self, value=0.7)
        self.master_knob.volumeChanged.connect(self._on_master_knob)

        volume_layout.addWidget(gain_col, 1)
        volume_layout.addWidget(self.master_knob, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(volume_row)

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

    def _on_gain_stepper(self, value: float) -> None:
        if self._gain_loading:
            return
        self.gainChanged.emit(float(value))

    def _on_master_knob(self, volume: float) -> None:
        if self._master_loading:
            return
        self.masterVolumeChanged.emit(float(volume))

    def _on_target_stepper(self, value: float) -> None:
        if self._target_loading:
            return
        self._target_lufs = float(value)
        self._refresh_lufs_label()
        self.targetLufsChanged.emit(float(value))

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

    def gain_db(self) -> float:
        return float(self.gain_stepper.value())

    def master_volume(self) -> float:
        return float(self.master_knob.volume())

    def set_master_volume(self, volume: float) -> None:
        self._master_loading = True
        try:
            self.master_knob.set_volume(float(volume))
        finally:
            self._master_loading = False

    def target_lufs(self) -> float:
        return float(self.target_lufs_stepper.value())

    def set_target_lufs(self, lufs: float) -> None:
        self._target_loading = True
        try:
            self._target_lufs = float(lufs)
            self.target_lufs_stepper.set_value(float(lufs))
            self._refresh_lufs_label()
        finally:
            self._target_loading = False

    def set_gain_db(self, gain_db: float, *, lufs: float | None = None) -> None:
        self._gain_loading = True
        try:
            self.gain_stepper.set_value(float(gain_db))
            self.set_lufs(lufs)
        finally:
            self._gain_loading = False

    def set_lufs(self, lufs: float | None) -> None:
        self._measured_lufs = None if lufs is None else float(lufs)
        self._refresh_lufs_label()

    def _refresh_lufs_label(self) -> None:
        if self._measured_lufs is None:
            self.lufs_label.setText("LUFS —")
            return
        self.lufs_label.setText(
            f"{self._measured_lufs:.1f} → {self._target_lufs:.1f} LUFS"
        )

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
