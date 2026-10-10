"""Horizontal audio mixer strip panel (Main + aux + Master)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.widgets.stem_fader import (
    STEM_FADER_DB_FLOOR,
    STEM_FADER_DB_MUTE,
    StemFader,
    clamp_stem_gain_db,
)


class MixerChannelStrip(QFrame):
    """One channel strip: caption, meter, fader, mute, optional delete."""

    faderChanged = pyqtSignal(str, float)  # channel_id, db
    muteChanged = pyqtSignal(str, bool)
    deleteRequested = pyqtSignal(str)
    midiLearnRequested = pyqtSignal(str)
    midiClearRequested = pyqtSignal(str)
    midiLearnCancelRequested = pyqtSignal(str)

    def __init__(
        self,
        channel_id: str,
        name: str,
        *,
        is_main: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.channel_id = channel_id
        self.is_main = bool(is_main)
        self.setObjectName("mixerChannelStrip")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(56)
        self.setMaximumWidth(72)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        self.name_label = QLabel(name)
        self.name_label.setObjectName("mixerStripName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.name_label.setWordWrap(True)

        self.meter = QProgressBar()
        self.meter.setObjectName("mixerPeakMeter")
        self.meter.setOrientation(Qt.Orientation.Vertical)
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setFixedWidth(8)
        self.meter.setMinimumHeight(48)

        meter_wrap = QHBoxLayout()
        meter_wrap.setContentsMargins(0, 0, 0, 0)
        meter_wrap.addStretch(1)
        meter_wrap.addWidget(self.meter)
        meter_wrap.addStretch(1)

        self.fader = StemFader(self)
        self.fader.caption.setText("")
        self.fader.fine_check.hide()
        self.fader.set_gain_db(0.0)
        self.fader.gainChanged.connect(self._on_fader)

        self.mute = QCheckBox("M")
        self.mute.setObjectName("mixerMute")
        self.mute.setToolTip("Mute")
        self.mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute.toggled.connect(self._on_mute)

        root.addWidget(self.name_label, 0)
        root.addLayout(meter_wrap, 0)
        root.addWidget(self.fader, 1)
        root.addWidget(self.mute, 0, Qt.AlignmentFlag.AlignHCenter)

        if not self.is_main:
            self.delete_btn = QPushButton("×")
            self.delete_btn.setObjectName("mixerDeleteBtn")
            self.delete_btn.setFixedSize(22, 22)
            self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.delete_btn.setToolTip("Remove channel")
            self.delete_btn.clicked.connect(
                lambda: self.deleteRequested.emit(self.channel_id)
            )
            root.addWidget(self.delete_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        else:
            self.delete_btn = None

        self.fader.slider.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fader.slider.customContextMenuRequested.connect(self._midi_menu)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._midi_menu)

        self._midi_label: str | None = None
        self._learning = False

    def _on_fader(self, db: float) -> None:
        self.faderChanged.emit(self.channel_id, float(db))

    def _on_mute(self, muted: bool) -> None:
        self.muteChanged.emit(self.channel_id, bool(muted))

    def set_fader_db(self, db: float) -> None:
        self.fader.set_gain_db(clamp_stem_gain_db(db))

    def set_mute(self, muted: bool) -> None:
        self.mute.blockSignals(True)
        self.mute.setChecked(bool(muted))
        self.mute.blockSignals(False)

    def set_peak(self, peak: float) -> None:
        self.meter.setValue(int(max(0.0, min(1.0, float(peak))) * 100))

    def set_name(self, name: str) -> None:
        self.name_label.setText(name)

    def set_midi_binding_label(self, label: str | None) -> None:
        self._midi_label = label
        tip = "Channel level"
        if label:
            tip = f"{tip} · MIDI {label}"
        self.fader.slider.setToolTip(tip)

    def set_learning(self, learning: bool) -> None:
        self._learning = bool(learning)

    def _midi_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        if self._learning:
            menu.addAction(
                "Cancel MIDI Learn",
                lambda: self.midiLearnCancelRequested.emit(self.channel_id),
            )
        else:
            menu.addAction(
                "MIDI Learn…",
                lambda: self.midiLearnRequested.emit(self.channel_id),
            )
            clear = menu.addAction("Clear MIDI mapping")
            clear.setEnabled(self._midi_label is not None)
            clear.triggered.connect(
                lambda: self.midiClearRequested.emit(self.channel_id)
            )
        global_pos = (
            self.fader.slider.mapToGlobal(pos)
            if self.sender() is self.fader.slider
            else self.mapToGlobal(pos)
        )
        menu.exec(global_pos)


class MasterMixerStrip(QFrame):
    """Master fader + limiter controls + Master EQ button."""

    masterVolumeChanged = pyqtSignal(float)  # 0..1 linear
    limiterBypassChanged = pyqtSignal(bool)
    limiterThresholdChanged = pyqtSignal(float)
    masterEqRequested = pyqtSignal()
    midiLearnRequested = pyqtSignal()
    midiClearRequested = pyqtSignal()
    midiLearnCancelRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mixerMasterStrip")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(72)
        self.setMaximumWidth(96)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        title = QLabel("MASTER")
        title.setObjectName("mixerStripName")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.fader = StemFader(self)
        self.fader.caption.setText("")
        self.fader.fine_check.hide()
        # Map 0 dB slider to ~70% default via set_master_volume
        self.fader.gainChanged.connect(self._on_fader)

        self.eq_btn = QPushButton("EQ")
        self.eq_btn.setObjectName("mixerMasterEqBtn")
        self.eq_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.eq_btn.clicked.connect(self.masterEqRequested.emit)

        self.lim_bypass = QCheckBox("LIM")
        self.lim_bypass.setObjectName("mixerLimBypass")
        self.lim_bypass.setToolTip("Limiter engaged when checked")
        self.lim_bypass.setChecked(True)
        self.lim_bypass.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lim_bypass.toggled.connect(self._on_lim_bypass)

        thr_row = QHBoxLayout()
        thr_lbl = QLabel("THR")
        thr_lbl.setObjectName("mixerLimLabel")
        self.thr_label = QLabel("−0.4")
        self.thr_label.setObjectName("mixerLimLabel")
        thr_row.addWidget(thr_lbl)
        thr_row.addWidget(self.thr_label)

        from PyQt6.QtWidgets import QSlider

        self.thr_slider = QSlider(Qt.Orientation.Horizontal)
        self.thr_slider.setRange(50, 100)  # 0.50 … 1.00
        self.thr_slider.setValue(95)
        self.thr_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.thr_slider.valueChanged.connect(self._on_thr)

        root.addWidget(title, 0)
        root.addWidget(self.fader, 1)
        root.addWidget(self.eq_btn, 0)
        root.addWidget(self.lim_bypass, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addLayout(thr_row)
        root.addWidget(self.thr_slider, 0)

        self._midi_label: str | None = None
        self._learning = False
        self._loading = False
        self.fader.slider.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fader.slider.customContextMenuRequested.connect(self._midi_menu)

    def _db_to_linear(self, db: float) -> float:
        if db <= STEM_FADER_DB_FLOOR - 1:
            return 0.0
        return float(10.0 ** (float(db) / 20.0))

    def _linear_to_db(self, linear: float) -> float:
        lin = max(0.0, float(linear))
        if lin <= 1e-6:
            return STEM_FADER_DB_MUTE
        import math

        return max(STEM_FADER_DB_FLOOR, min(0.0, 20.0 * math.log10(lin)))

    def _on_fader(self, db: float) -> None:
        if self._loading:
            return
        # Master uses 0..1; clamp fader to −∞…0 dB for master
        self.masterVolumeChanged.emit(min(1.0, self._db_to_linear(db)))

    def set_master_volume(self, volume: float) -> None:
        self._loading = True
        try:
            self.fader.set_gain_db(self._linear_to_db(volume))
        finally:
            self._loading = False

    def _on_lim_bypass(self, checked: bool) -> None:
        # Checked = limiter ON → bypass False
        self.limiterBypassChanged.emit(not bool(checked))

    def _on_thr(self, value: int) -> None:
        thr = float(value) / 100.0
        import math

        db = 20.0 * math.log10(max(1e-6, thr))
        self.thr_label.setText(f"{db:+.1f}")
        self.limiterThresholdChanged.emit(thr)

    def set_limiter(self, *, bypass: bool, threshold: float) -> None:
        self.lim_bypass.blockSignals(True)
        self.lim_bypass.setChecked(not bypass)
        self.lim_bypass.blockSignals(False)
        pos = int(round(max(0.5, min(1.0, float(threshold))) * 100))
        self.thr_slider.blockSignals(True)
        self.thr_slider.setValue(pos)
        self.thr_slider.blockSignals(False)
        import math

        db = 20.0 * math.log10(max(1e-6, float(threshold)))
        self.thr_label.setText(f"{db:+.1f}")

    def set_midi_binding_label(self, label: str | None) -> None:
        self._midi_label = label

    def set_learning(self, learning: bool) -> None:
        self._learning = bool(learning)

    def _midi_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        if self._learning:
            menu.addAction("Cancel MIDI Learn", self.midiLearnCancelRequested.emit)
        else:
            menu.addAction("MIDI Learn…", self.midiLearnRequested.emit)
            clear = menu.addAction("Clear MIDI mapping")
            clear.setEnabled(self._midi_label is not None)
            clear.triggered.connect(self.midiClearRequested.emit)
        menu.exec(self.fader.slider.mapToGlobal(pos))


class AudioMixerPanel(QFrame):
    """Strip rack: channels + add + master."""

    addChannelRequested = pyqtSignal()
    removeChannelRequested = pyqtSignal(str)
    channelFaderChanged = pyqtSignal(str, float)
    channelMuteChanged = pyqtSignal(str, bool)
    masterVolumeChanged = pyqtSignal(float)
    limiterBypassChanged = pyqtSignal(bool)
    limiterThresholdChanged = pyqtSignal(float)
    masterEqRequested = pyqtSignal()
    channelMidiLearnRequested = pyqtSignal(str)
    channelMidiClearRequested = pyqtSignal(str)
    channelMidiLearnCancelRequested = pyqtSignal(str)
    masterMidiLearnRequested = pyqtSignal()
    masterMidiClearRequested = pyqtSignal()
    masterMidiLearnCancelRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("audioMixerPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(160)

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(6)

        # Section title lives in the parent tab; keep a compact add control.
        self._strips_host = QWidget()
        self._strips_layout = QHBoxLayout(self._strips_host)
        self._strips_layout.setContentsMargins(0, 0, 0, 0)
        self._strips_layout.setSpacing(4)
        self._strips_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._strips: dict[str, MixerChannelStrip] = {}

        self.add_btn = QPushButton("+ Ch")
        self.add_btn.setObjectName("mixerAddBtn")
        self.add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_btn.setToolTip("Add mixer channel")
        self.add_btn.clicked.connect(self.addChannelRequested.emit)

        self.master_strip = MasterMixerStrip(self)
        self.master_strip.masterVolumeChanged.connect(self.masterVolumeChanged.emit)
        self.master_strip.limiterBypassChanged.connect(self.limiterBypassChanged.emit)
        self.master_strip.limiterThresholdChanged.connect(
            self.limiterThresholdChanged.emit
        )
        self.master_strip.masterEqRequested.connect(self.masterEqRequested.emit)
        self.master_strip.midiLearnRequested.connect(self.masterMidiLearnRequested.emit)
        self.master_strip.midiClearRequested.connect(self.masterMidiClearRequested.emit)
        self.master_strip.midiLearnCancelRequested.connect(
            self.masterMidiLearnCancelRequested.emit
        )

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.add_btn)
        left.addStretch(1)

        root.addLayout(left, 0)
        root.addWidget(self._strips_host, 1)
        root.addWidget(self.master_strip, 0)

    def rebuild_channels(self, channels: list[dict]) -> None:
        """channels: [{id, name, is_main, fader_db, mute}, ...]"""
        while self._strips_layout.count():
            item = self._strips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._strips.clear()
        for info in channels:
            cid = str(info["id"])
            strip = MixerChannelStrip(
                cid,
                str(info.get("name") or cid),
                is_main=bool(info.get("is_main")),
                parent=self._strips_host,
            )
            strip.set_fader_db(float(info.get("fader_db", 0.0)))
            strip.set_mute(bool(info.get("mute", False)))
            strip.faderChanged.connect(self.channelFaderChanged.emit)
            strip.muteChanged.connect(self.channelMuteChanged.emit)
            strip.deleteRequested.connect(self.removeChannelRequested.emit)
            strip.midiLearnRequested.connect(self.channelMidiLearnRequested.emit)
            strip.midiClearRequested.connect(self.channelMidiClearRequested.emit)
            strip.midiLearnCancelRequested.connect(
                self.channelMidiLearnCancelRequested.emit
            )
            self._strips[cid] = strip
            self._strips_layout.addWidget(strip)
        self._strips_layout.addStretch(1)

    def set_channel_peak(self, channel_id: str, peak: float) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_peak(peak)

    def set_peaks(self, peaks: dict) -> None:
        for cid, peak in peaks.items():
            self.set_channel_peak(str(cid), float(peak))

    def set_channel_fader_db(self, channel_id: str, db: float) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_fader_db(db)

    def set_channel_midi_label(self, channel_id: str, label: str | None) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_midi_binding_label(label)

    def set_channel_learning(self, channel_id: str, learning: bool) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_learning(learning)

    def strip_ids(self) -> list[str]:
        return list(self._strips.keys())
