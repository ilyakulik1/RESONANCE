"""Vertical mixer-style stem volume fader."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)


# Full range: -60 … +10 dB. Slider bottom (pos 0) is mute (−∞).
STEM_FADER_DB_FLOOR = -60.0
STEM_FADER_DB_MAX = 10.0
STEM_FADER_DB_MUTE = -120.0  # practical −∞ for linear gain ≈ 0
STEM_FADER_DB_MIN = STEM_FADER_DB_MUTE

# Fine trim: −6 … +6 dB, 0 in the middle (no mute detent).
STEM_FADER_FINE_MIN = -6.0
STEM_FADER_FINE_MAX = 6.0

_SLIDER_MAX = 1000
_SLIDER_AUDIBLE_MIN = 1  # 0 reserved for −∞ in full mode


def _is_mute_db(db: float) -> bool:
    """True for −∞ detent or any value below the audible floor."""
    return float(db) < STEM_FADER_DB_FLOOR - 1e-6


def _db_to_slider(db: float, *, fine: bool) -> int:
    value = float(db)
    if fine:
        value = max(STEM_FADER_FINE_MIN, min(STEM_FADER_FINE_MAX, value))
        span = STEM_FADER_FINE_MAX - STEM_FADER_FINE_MIN
        ratio = (value - STEM_FADER_FINE_MIN) / span
        return int(round(ratio * _SLIDER_MAX))
    if _is_mute_db(value):
        return 0
    value = max(STEM_FADER_DB_FLOOR, min(STEM_FADER_DB_MAX, value))
    span = STEM_FADER_DB_MAX - STEM_FADER_DB_FLOOR
    ratio = (value - STEM_FADER_DB_FLOOR) / span
    return int(round(_SLIDER_AUDIBLE_MIN + ratio * (_SLIDER_MAX - _SLIDER_AUDIBLE_MIN)))


def _slider_to_db(pos: int, *, fine: bool) -> float:
    p = max(0, min(_SLIDER_MAX, int(pos)))
    if fine:
        span = STEM_FADER_FINE_MAX - STEM_FADER_FINE_MIN
        return STEM_FADER_FINE_MIN + (p / float(_SLIDER_MAX)) * span
    if p < _SLIDER_AUDIBLE_MIN:
        return STEM_FADER_DB_MUTE
    span = STEM_FADER_DB_MAX - STEM_FADER_DB_FLOOR
    ratio = (p - _SLIDER_AUDIBLE_MIN) / float(_SLIDER_MAX - _SLIDER_AUDIBLE_MIN)
    return STEM_FADER_DB_FLOOR + ratio * span


def format_stem_db(db: float) -> str:
    if _is_mute_db(db):
        return "-∞"
    if abs(db) < 0.05:
        return "0.0"
    return f"{db:+.1f}"


def clamp_stem_gain_db(gain_db: float) -> float:
    value = float(gain_db)
    if _is_mute_db(value):
        return STEM_FADER_DB_MUTE
    return max(STEM_FADER_DB_FLOOR, min(STEM_FADER_DB_MAX, value))


class StemFader(QWidget):
    """Console-style vertical fader for one stem channel (dB)."""

    gainChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stemFader")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(44)
        self.setMaximumWidth(56)

        self._loading = False
        self._fine = False

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 0, 2, 2)
        root.setSpacing(2)

        self.caption = QLabel("STEM")
        self.caption.setObjectName("stemFaderCaption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.value_label = QLabel("0.0")
        self.value_label.setObjectName("stemFaderValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setObjectName("stemFaderSlider")
        self.slider.setRange(0, _SLIDER_MAX)
        self.slider.setValue(_db_to_slider(0.0, fine=False))
        self.slider.setTickPosition(QSlider.TickPosition.TicksRight)
        self.slider.setTickInterval(100)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.setToolTip("Stem level (−60…+10 dB, bottom = −∞) · double-click for 0 dB")
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.installEventFilter(self)

        self.unity = QLabel("0")
        self.unity.setObjectName("stemFaderUnity")
        self.unity.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.unity.setToolTip("Unity gain (double-click fader to reset)")

        self.fine_check = QCheckBox()
        self.fine_check.setObjectName("stemFaderFine")
        self.fine_check.setToolTip("Fine trim (−6…+6 dB, 0 in the middle)")
        self.fine_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fine_check.toggled.connect(self._on_fine_toggled)

        root.addWidget(self.caption, 0)
        root.addWidget(self.value_label, 0)
        root.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.unity, 0)
        root.addWidget(self.fine_check, 0, Qt.AlignmentFlag.AlignHCenter)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.slider and event.type() == QEvent.Type.MouseButtonDblClick:
            if isinstance(event, QMouseEvent) and event.button() == Qt.MouseButton.LeftButton:
                self.reset_to_unity()
                return True
        return super().eventFilter(obj, event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_to_unity()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def reset_to_unity(self) -> None:
        self.set_gain_db(0.0)
        if not self._loading:
            self.gainChanged.emit(0.0)

    def _update_tooltip(self) -> None:
        if self._fine:
            self.slider.setToolTip(
                "Fine stem level (−6…+6 dB) · double-click for 0 dB"
            )
        else:
            self.slider.setToolTip(
                "Stem level (−60…+10 dB, bottom = −∞) · double-click for 0 dB"
            )

    def _on_fine_toggled(self, checked: bool) -> None:
        previous = _slider_to_db(self.slider.value(), fine=self._fine)
        self._fine = bool(checked)
        self._update_tooltip()
        current = previous
        if self._fine:
            if _is_mute_db(previous):
                current = STEM_FADER_FINE_MIN
            else:
                current = max(STEM_FADER_FINE_MIN, min(STEM_FADER_FINE_MAX, previous))
        self._loading = True
        try:
            self.slider.setValue(_db_to_slider(current, fine=self._fine))
            self.value_label.setText(format_stem_db(current))
        finally:
            self._loading = False
        if abs(current - previous) > 1e-6:
            self.gainChanged.emit(current)

    def _on_slider(self, value: int) -> None:
        db = _slider_to_db(value, fine=self._fine)
        self.value_label.setText(format_stem_db(db))
        if self._loading:
            return
        self.gainChanged.emit(db)

    def set_caption(self, text: str) -> None:
        self.caption.setText(text)

    def gain_db(self) -> float:
        return _slider_to_db(self.slider.value(), fine=self._fine)

    def set_gain_db(self, db: float) -> None:
        self._loading = True
        try:
            value = float(db)
            if self._fine:
                value = max(STEM_FADER_FINE_MIN, min(STEM_FADER_FINE_MAX, value))
            self.slider.setValue(_db_to_slider(value, fine=self._fine))
            self.value_label.setText(
                format_stem_db(_slider_to_db(self.slider.value(), fine=self._fine))
            )
        finally:
            self._loading = False

    def set_enabled_fader(self, enabled: bool) -> None:
        self.slider.setEnabled(enabled)
        self.fine_check.setEnabled(enabled)
        self.setEnabled(enabled)
