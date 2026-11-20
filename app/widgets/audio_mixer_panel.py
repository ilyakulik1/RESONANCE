"""Console-style audio mixer: program channels, sample channels, master."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.constants import (
    CHANNEL_KIND_PROGRAM,
    CHANNEL_KIND_SAMPLE,
    MAX_AUX_CHANNELS,
    normalize_channel_kind,
)
from app.time_utils import format_time_ms
from app.widgets.stem_fader import (
    STEM_FADER_DB_FLOOR,
    STEM_FADER_DB_MUTE,
    StemFader,
    clamp_stem_gain_db,
)


def _style_fader(fader: StemFader) -> None:
    """Turn the shared stem slider into a console cap without restyling EQ faders."""
    slider = fader.slider
    slider.setProperty("mixerCap", True)
    slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
    slider.setTickInterval(200)
    slider.style().unpolish(slider)
    slider.style().polish(slider)


class _Lamp(QWidget):
    """Small on-air / playing indicator."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._lit = False
        self.setFixedSize(10, 10)

    def set_lit(self, lit: bool) -> None:
        lit = bool(lit)
        if lit == self._lit:
            return
        self._lit = lit
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color if self._lit else QColor("#3a3c42"))
        painter.drawEllipse(1, 1, 8, 8)


class _PeakMeter(QWidget):
    """Segmented LED meter beside the fader."""

    _SEGMENTS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0
        self._hold = 0.0
        self.setFixedWidth(12)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_peak(self, peak: float) -> None:
        level = max(0.0, min(1.0, float(peak)))
        if level >= self._level:
            self._level = level
        else:
            self._level = self._level * 0.45 + level * 0.55
        if self._level >= self._hold:
            self._hold = self._level
        else:
            self._hold = max(self._level, self._hold - 0.06)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.rect().adjusted(2, 4, -2, -4)
        if rect.height() <= 0:
            return
        gap = 1
        seg_h = max(2, (rect.height() - gap * (self._SEGMENTS - 1)) // self._SEGMENTS)
        lit = int(round(self._level * self._SEGMENTS))
        hold_i = min(self._SEGMENTS - 1, max(0, int(round(self._hold * self._SEGMENTS)) - 1))
        y = rect.bottom() - seg_h
        for index in range(self._SEGMENTS):
            if index >= 15:
                color = QColor("#e05a4f")
            elif index >= 12:
                color = QColor("#e0b15a")
            else:
                color = QColor("#3ecf7a")
            if index >= lit:
                color = QColor("#f2f2f4") if index == hold_i and self._hold > 0.04 else QColor("#2a2c30")
            painter.fillRect(rect.left(), y, rect.width(), seg_h, color)
            y -= seg_h + gap


class MixerChannelStrip(QFrame):
    """One console channel: lamp, meter, fader, mute, and sample transport."""

    faderChanged = pyqtSignal(str, float)
    muteChanged = pyqtSignal(str, bool)
    deleteRequested = pyqtSignal(str)
    renameRequested = pyqtSignal(str, str)
    kindRequested = pyqtSignal(str, str)
    sampleStopRequested = pyqtSignal(str)
    sampleRewindRequested = pyqtSignal(str)
    sampleSeekRequested = pyqtSignal(str, int)
    midiLearnRequested = pyqtSignal(str)
    midiClearRequested = pyqtSignal(str)
    midiLearnCancelRequested = pyqtSignal(str)

    def __init__(
        self,
        channel_id: str,
        name: str,
        *,
        kind: str,
        is_main: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.channel_id = channel_id
        self.is_main = bool(is_main)
        self.is_sample = kind == CHANNEL_KIND_SAMPLE and not self.is_main
        self._kind = CHANNEL_KIND_SAMPLE if self.is_sample else CHANNEL_KIND_PROGRAM
        self._duration_ms = 0
        self._scrubbing = False
        self._transport_title = ""
        self.setObjectName("mixerChannelStrip")
        self.setProperty("channelKind", self._kind)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(86)
        self.setMaximumWidth(96)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(3)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        lamp_color = "#e0a15a" if self.is_sample else "#3ec4d0"
        self.lamp = _Lamp(lamp_color, self)
        self.kind_badge = QLabel("SMP" if self.is_sample else "PGM")
        self.kind_badge.setObjectName("mixerKindBadge")
        self.kind_badge.setProperty("channelKind", self._kind)
        self.kind_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.lamp, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.kind_badge, 1)

        self.name_label = QLabel(name)
        self.name_label.setObjectName("mixerStripName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(32)
        self.name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_label.setToolTip("Double-click to rename")
        self.name_label.installEventFilter(self)

        self.sub_label = QLabel("")
        self.sub_label.setObjectName("mixerSubName")
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.sub_label.setWordWrap(True)
        self.sub_label.setMaximumHeight(22)

        self.meter = _PeakMeter(self)
        self.fader = StemFader(self)
        self.fader.caption.hide()
        self.fader.fine_check.hide()
        self.fader.unity.hide()
        _style_fader(self.fader)
        self.fader.set_gain_db(0.0)
        self.fader.gainChanged.connect(self._on_fader)

        meter_row = QHBoxLayout()
        meter_row.setContentsMargins(0, 0, 0, 0)
        meter_row.setSpacing(2)
        meter_row.addWidget(self.meter, 0)
        meter_row.addWidget(self.fader, 1)

        self.mute = QPushButton("MUTE")
        self.mute.setObjectName("mixerMuteBtn")
        self.mute.setCheckable(True)
        self.mute.setToolTip("Mute channel")
        self.mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute.toggled.connect(self._on_mute)

        root.addLayout(header)
        root.addWidget(self.name_label)
        root.addWidget(self.sub_label)
        root.addLayout(meter_row, 1)

        self.time_label = None
        self.seek = None
        self.cue_btn = None
        self.stop_btn = None
        if self.is_sample:
            self.time_label = QLabel("—")
            self.time_label.setObjectName("mixerTransportTime")
            self.time_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            self.seek = QSlider(Qt.Orientation.Horizontal)
            self.seek.setObjectName("mixerSampleSeek")
            self.seek.setRange(0, 1000)
            self.seek.setValue(0)
            self.seek.setEnabled(False)
            self.seek.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.seek.setToolTip("Scrub the playing sample")
            self.seek.sliderPressed.connect(self._on_seek_pressed)
            self.seek.sliderReleased.connect(self._on_seek_released)
            self.seek.valueChanged.connect(self._on_seek_moved)

            transport = QHBoxLayout()
            transport.setContentsMargins(0, 0, 0, 0)
            transport.setSpacing(3)
            self.cue_btn = QPushButton("CUE")
            self.cue_btn.setObjectName("mixerCueBtn")
            self.cue_btn.setToolTip("Rewind sample to the start")
            self.cue_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.cue_btn.setEnabled(False)
            self.cue_btn.clicked.connect(
                lambda: self.sampleRewindRequested.emit(self.channel_id)
            )
            self.stop_btn = QPushButton("STOP")
            self.stop_btn.setObjectName("mixerStopBtn")
            self.stop_btn.setToolTip("Stop sample")
            self.stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.stop_btn.setEnabled(False)
            self.stop_btn.clicked.connect(
                lambda: self.sampleStopRequested.emit(self.channel_id)
            )
            transport.addWidget(self.cue_btn, 1)
            transport.addWidget(self.stop_btn, 1)
            root.addWidget(self.time_label)
            root.addWidget(self.seek)
            root.addLayout(transport)
        else:
            self.sub_label.hide()

        root.addWidget(self.mute)

        self.delete_btn = None
        if not self.is_main:
            self.delete_btn = QPushButton("×")
            self.delete_btn.setObjectName("mixerDeleteBtn")
            self.delete_btn.setFixedSize(18, 16)
            self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.delete_btn.setToolTip("Remove channel")
            self.delete_btn.clicked.connect(
                lambda: self.deleteRequested.emit(self.channel_id)
            )
            root.addWidget(self.delete_btn, 0, Qt.AlignmentFlag.AlignHCenter)

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
        self.meter.set_peak(peak)

    def set_name(self, name: str) -> None:
        self.name_label.setText(name)
        self.name_label.setToolTip(f"{name}\nDouble-click to rename")

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.name_label
            and event.type() == QEvent.Type.MouseButtonDblClick
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._prompt_rename()
            return True
        return super().eventFilter(obj, event)

    def _prompt_rename(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        current = self.name_label.text()
        text, ok = QInputDialog.getText(
            self,
            "Rename channel",
            "Name:",
            text=current,
        )
        if not ok:
            return
        name = " ".join(text.split())
        if not name or name == current:
            return
        self.renameRequested.emit(self.channel_id, name[:32])

    def set_on_air(self, on_air: bool) -> None:
        if not self.is_sample:
            self.lamp.set_lit(bool(on_air))

    def set_transport(
        self,
        *,
        playing: bool,
        position_ms: int,
        duration_ms: int,
        title: str = "",
    ) -> None:
        if not self.is_sample:
            return
        self.lamp.set_lit(bool(playing))
        if self.cue_btn is not None:
            self.cue_btn.setEnabled(bool(playing))
        if self.stop_btn is not None:
            self.stop_btn.setEnabled(bool(playing))
        title = str(title or "")
        if title != self._transport_title and self.sub_label is not None:
            self._transport_title = title
            self.sub_label.setText(title)
        self._duration_ms = max(0, int(duration_ms))
        if self.seek is None or self.time_label is None:
            return
        self.seek.setEnabled(bool(playing) and self._duration_ms > 0)
        if not playing or self._duration_ms <= 0:
            if not self._scrubbing:
                self.seek.blockSignals(True)
                self.seek.setValue(0)
                self.seek.blockSignals(False)
            self.time_label.setText("—")
            return
        pos = max(0, min(self._duration_ms, int(position_ms)))
        if not self._scrubbing:
            slider_pos = int(round(pos * 1000 / self._duration_ms))
            if slider_pos != self.seek.value():
                self.seek.blockSignals(True)
                self.seek.setValue(slider_pos)
                self.seek.blockSignals(False)
        label = format_time_ms(pos)
        if self.time_label.text() != label:
            self.time_label.setText(label)

    def _on_seek_pressed(self) -> None:
        self._scrubbing = True

    def _on_seek_released(self) -> None:
        self._scrubbing = False
        self._emit_seek()

    def _on_seek_moved(self, _value: int) -> None:
        if self._scrubbing:
            self._emit_seek()

    def _emit_seek(self) -> None:
        if self.seek is None or self._duration_ms <= 0:
            return
        ms = int(round(self.seek.value() / 1000.0 * self._duration_ms))
        self.sampleSeekRequested.emit(self.channel_id, ms)

    def set_midi_binding_label(self, label: str | None) -> None:
        self._midi_label = label
        tip = "Channel level · double-click fader for 0 dB"
        if label:
            tip = f"{tip} · MIDI {label}"
        self.fader.slider.setToolTip(tip)

    def set_learning(self, learning: bool) -> None:
        self._learning = bool(learning)

    def _midi_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Rename…", self._prompt_rename)
        menu.addSeparator()
        if not self.is_main:
            kind_menu = menu.addMenu("Channel type")
            program = kind_menu.addAction("Program — transition")
            program.setCheckable(True)
            program.setChecked(not self.is_sample)
            sample = kind_menu.addAction("Sample — plays over program")
            sample.setCheckable(True)
            sample.setChecked(self.is_sample)
            program.triggered.connect(
                lambda: self.kindRequested.emit(self.channel_id, CHANNEL_KIND_PROGRAM)
            )
            sample.triggered.connect(
                lambda: self.kindRequested.emit(self.channel_id, CHANNEL_KIND_SAMPLE)
            )
            menu.addSeparator()
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
    """Master fader + limiter + Master EQ."""

    masterVolumeChanged = pyqtSignal(float)
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
        self.setMinimumWidth(92)
        self.setMaximumWidth(108)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(3)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.lamp = _Lamp("#e2e2e4", self)
        self.lamp.set_lit(True)
        title = QLabel("MASTER")
        title.setObjectName("mixerStripName")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.lamp, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(title, 1)

        self.fader = StemFader(self)
        self.fader.caption.hide()
        self.fader.fine_check.hide()
        self.fader.unity.hide()
        _style_fader(self.fader)
        self.fader.gainChanged.connect(self._on_fader)

        self.eq_btn = QPushButton("EQ")
        self.eq_btn.setObjectName("mixerMasterEqBtn")
        self.eq_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.eq_btn.setToolTip("Master equalizer")
        self.eq_btn.clicked.connect(self.masterEqRequested.emit)

        self.lim_bypass = QPushButton("LIM")
        self.lim_bypass.setObjectName("mixerLimBypass")
        self.lim_bypass.setCheckable(True)
        self.lim_bypass.setToolTip("Limiter")
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

        self.thr_slider = QSlider(Qt.Orientation.Horizontal)
        self.thr_slider.setObjectName("mixerThrSlider")
        self.thr_slider.setRange(50, 100)
        self.thr_slider.setValue(95)
        self.thr_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.thr_slider.valueChanged.connect(self._on_thr)

        root.addLayout(header)
        root.addWidget(self.fader, 1)
        root.addWidget(self.eq_btn)
        root.addWidget(self.lim_bypass)
        root.addLayout(thr_row)
        root.addWidget(self.thr_slider)

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
        self.masterVolumeChanged.emit(min(1.0, self._db_to_linear(db)))

    def set_master_volume(self, volume: float) -> None:
        self._loading = True
        try:
            self.fader.set_gain_db(self._linear_to_db(volume))
        finally:
            self._loading = False

    def _on_lim_bypass(self, checked: bool) -> None:
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
    """Strip rack: program channels, sample channels, master."""

    addProgramRequested = pyqtSignal()
    addSampleRequested = pyqtSignal()
    removeChannelRequested = pyqtSignal(str)
    channelRenameRequested = pyqtSignal(str, str)
    channelFaderChanged = pyqtSignal(str, float)
    channelMuteChanged = pyqtSignal(str, bool)
    channelKindRequested = pyqtSignal(str, str)
    sampleStopRequested = pyqtSignal(str)
    sampleRewindRequested = pyqtSignal(str)
    sampleSeekRequested = pyqtSignal(str, int)
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
        self.setMinimumHeight(220)
        self._on_air_ids: set[str] = set()

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._strips_host = QWidget()
        self._strips_host.setObjectName("mixerStripsHost")
        self._strips_layout = QHBoxLayout(self._strips_host)
        self._strips_layout.setContentsMargins(0, 0, 0, 0)
        self._strips_layout.setSpacing(6)
        self._strips_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._strips: dict[str, MixerChannelStrip] = {}

        scroll = QScrollArea()
        scroll.setObjectName("mixerScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._strips_host)
        scroll.viewport().setAutoFillBackground(False)

        add = QFrame()
        add.setObjectName("mixerAddStrip")
        add.setFixedWidth(64)
        add_layout = QVBoxLayout(add)
        add_layout.setContentsMargins(6, 8, 6, 8)
        add_layout.setSpacing(6)
        add_title = QLabel("ADD")
        add_title.setObjectName("mixerStripName")
        add_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.add_program_btn = QPushButton("PGM")
        self.add_program_btn.setObjectName("mixerAddBtn")
        self.add_program_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_program_btn.setToolTip(
            "Add a program channel. Tracks crossfade with the air transition."
        )
        self.add_program_btn.clicked.connect(self.addProgramRequested.emit)
        self.add_sample_btn = QPushButton("SMP")
        self.add_sample_btn.setObjectName("mixerAddBtn")
        self.add_sample_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_sample_btn.setToolTip(
            "Add a sample channel. It plays over the program and can be scrubbed or stopped."
        )
        self.add_sample_btn.clicked.connect(self.addSampleRequested.emit)
        add_layout.addWidget(add_title)
        add_layout.addStretch(1)
        add_layout.addWidget(self.add_program_btn)
        add_layout.addWidget(self.add_sample_btn)
        add_layout.addStretch(1)

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

        root.addWidget(scroll, 1)
        root.addWidget(add, 0)
        root.addWidget(self.master_strip, 0)

    def rebuild_channels(self, channels: list[dict]) -> None:
        """channels: [{id, name, is_main, kind, fader_db, mute}, ...]"""
        while self._strips_layout.count():
            item = self._strips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._strips.clear()
        extra = 0
        for info in channels:
            cid = str(info["id"])
            kind = normalize_channel_kind(info)
            if not info.get("is_main"):
                extra += 1
            strip = MixerChannelStrip(
                cid,
                str(info.get("name") or cid),
                kind=kind,
                is_main=bool(info.get("is_main")),
                parent=self._strips_host,
            )
            strip.set_fader_db(float(info.get("fader_db", 0.0)))
            strip.set_mute(bool(info.get("mute", False)))
            strip.faderChanged.connect(self.channelFaderChanged.emit)
            strip.muteChanged.connect(self.channelMuteChanged.emit)
            strip.deleteRequested.connect(self.removeChannelRequested.emit)
            strip.renameRequested.connect(self.channelRenameRequested.emit)
            strip.kindRequested.connect(self.channelKindRequested.emit)
            strip.sampleStopRequested.connect(self.sampleStopRequested.emit)
            strip.sampleRewindRequested.connect(self.sampleRewindRequested.emit)
            strip.sampleSeekRequested.connect(self.sampleSeekRequested.emit)
            strip.midiLearnRequested.connect(self.channelMidiLearnRequested.emit)
            strip.midiClearRequested.connect(self.channelMidiClearRequested.emit)
            strip.midiLearnCancelRequested.connect(
                self.channelMidiLearnCancelRequested.emit
            )
            self._strips[cid] = strip
            self._strips_layout.addWidget(strip)
        self._strips_layout.addStretch(1)
        can_add = extra < MAX_AUX_CHANNELS
        self.add_program_btn.setEnabled(can_add)
        self.add_sample_btn.setEnabled(can_add)
        self.set_program_on_air(self._on_air_ids)

    def set_channel_peak(self, channel_id: str, peak: float) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_peak(peak)

    def set_peaks(self, peaks: dict) -> None:
        for cid, peak in peaks.items():
            self.set_channel_peak(str(cid), float(peak))

    def set_channel_name(self, channel_id: str, name: str) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_name(name)

    def set_channel_fader_db(self, channel_id: str, db: float) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_fader_db(db)

    def set_channel_transport(
        self,
        channel_id: str,
        *,
        playing: bool,
        position_ms: int,
        duration_ms: int,
        title: str = "",
    ) -> None:
        strip = self._strips.get(channel_id)
        if strip is not None:
            strip.set_transport(
                playing=playing,
                position_ms=position_ms,
                duration_ms=duration_ms,
                title=title,
            )

    def set_program_on_air(self, channel_ids) -> None:
        ids = {str(cid) for cid in channel_ids}
        self._on_air_ids = ids
        for cid, strip in self._strips.items():
            if not strip.is_sample:
                strip.set_on_air(cid in ids)

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
