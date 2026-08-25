"""Parametric EQ editor (Pro-Q style): curve, nodes, spectrum overlay."""

from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.eq_curve import log_freq_axis, response_db
from app.eq_state import (
    BAND_TYPE_LABELS,
    BAND_TYPES,
    FREQ_MAX_HZ,
    FREQ_MIN_HZ,
    GAIN_MAX_DB,
    GAIN_MIN_DB,
    Q_MAX,
    Q_MIN,
    EqBand,
    EqState,
)


class _EqCanvas(QWidget):
    """Interactive frequency-response canvas."""

    bandsEdited = pyqtSignal()
    selectionChanged = pyqtSignal()
    _LEFT_AXIS_W = 34
    _SPECTRUM_DB_MIN = -90.0
    _BOTTOM_AXIS_H = 22

    # Major / labeled frequency marks (Hz)
    _FREQ_MAJOR = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
    # Minor frequency ticks between decades
    _FREQ_MINOR = (
        30, 40, 60, 70, 80, 90,
        150, 250, 300, 400, 600, 700, 800, 900,
        1500, 2500, 3000, 4000, 6000, 7000, 8000, 9000,
        12000, 15000, 16000, 18000,
    )
    _FREQ_LABELS = (
        (20, "20"),
        (50, "50"),
        (100, "100"),
        (200, "200"),
        (500, "500"),
        (1000, "1k"),
        (2000, "2k"),
        (5000, "5k"),
        (10000, "10k"),
        (20000, "20k"),
    )
    # dB: minor every 3, major (zero/emphasized) at multiples of 6
    _DB_MINOR = (-15, -9, -3, 3, 9, 15)
    _DB_MAJOR = (-18, -12, -6, 0, 6, 12, 18)

    def __init__(self, state: EqState, parent=None):
        super().__init__(parent)
        self._state = state
        self._spectrum_freqs: np.ndarray | None = None
        self._spectrum_db: np.ndarray | None = None
        self._curve_freqs = log_freq_axis(512)
        self._drag_id: str | None = None
        self._drag_q = False
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("eqCanvas")

        self._bg = QColor("#0a0a0a")
        self._grid = QColor(255, 255, 255, 28)
        self._grid_zero = QColor(255, 255, 255, 55)
        self._spectrum = QColor(56, 151, 253, 70)
        self._curve = QColor("#3897fd")
        self._curve_bypass = QColor(159, 159, 159, 120)
        self._node = QColor("#3897fd")
        self._node_sel = QColor("#ffffff")
        self._label = QColor("#9f9f9f")

    def set_state(self, state: EqState) -> None:
        self._state = state
        self.update()

    def set_spectrum(self, freqs: np.ndarray | None, db: np.ndarray | None) -> None:
        self._spectrum_freqs = freqs
        self._spectrum_db = db
        self.update()

    def _plot_rect(self) -> QRectF:
        # Left strip for gain dB labels; bottom strip for freq labels
        return QRectF(
            4 + self._LEFT_AXIS_W,
            4,
            max(1.0, self.width() - 8 - self._LEFT_AXIS_W),
            max(1.0, self.height() - self._BOTTOM_AXIS_H),
        )

    def _spectrum_y(self, db: float, rect: QRectF) -> float:
        t = (db - self._SPECTRUM_DB_MIN) / (-self._SPECTRUM_DB_MIN)
        y = rect.bottom() - t * rect.height() * 0.85
        return max(rect.top(), min(rect.bottom(), y))

    def _freq_to_x(self, freq: float, rect: QRectF) -> float:
        f = max(FREQ_MIN_HZ, min(FREQ_MAX_HZ, freq))
        t = (math.log10(f) - math.log10(FREQ_MIN_HZ)) / (
            math.log10(FREQ_MAX_HZ) - math.log10(FREQ_MIN_HZ)
        )
        return rect.left() + t * rect.width()

    def _x_to_freq(self, x: float, rect: QRectF) -> float:
        t = (x - rect.left()) / max(rect.width(), 1e-6)
        t = max(0.0, min(1.0, t))
        return 10 ** (
            math.log10(FREQ_MIN_HZ)
            + t * (math.log10(FREQ_MAX_HZ) - math.log10(FREQ_MIN_HZ))
        )

    def _db_to_y(self, db: float, rect: QRectF) -> float:
        t = (db - GAIN_MIN_DB) / (GAIN_MAX_DB - GAIN_MIN_DB)
        return rect.bottom() - t * rect.height()

    def _y_to_db(self, y: float, rect: QRectF) -> float:
        t = (rect.bottom() - y) / max(rect.height(), 1e-6)
        return GAIN_MIN_DB + max(0.0, min(1.0, t)) * (GAIN_MAX_DB - GAIN_MIN_DB)

    def _band_point(self, band: EqBand, rect: QRectF) -> QPointF:
        gain = 0.0 if band.type in ("low_cut", "high_cut") else band.gain_db
        return QPointF(self._freq_to_x(band.freq_hz, rect), self._db_to_y(gain, rect))

    def _hit_band(self, pos: QPointF, rect: QRectF) -> EqBand | None:
        best: EqBand | None = None
        best_d = 12.0
        for band in self._state.bands:
            p = self._band_point(band, rect)
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d < best_d:
                best_d = d
                best = band
        return best

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._bg)
        rect = self._plot_rect()

        # Grid — frequency (minor + major)
        minor_grid = QColor(255, 255, 255, 14)
        painter.setPen(QPen(minor_grid, 1))
        for f in self._FREQ_MINOR:
            x = self._freq_to_x(f, rect)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.setPen(QPen(self._grid, 1))
        for f in self._FREQ_MAJOR:
            x = self._freq_to_x(f, rect)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        # Grid — dB (EQ gain): every 3 dB, emphasize 0 and ±6/±12/±18
        painter.setPen(QPen(minor_grid, 1))
        for db in self._DB_MINOR:
            y = self._db_to_y(db, rect)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for db in self._DB_MAJOR:
            y = self._db_to_y(db, rect)
            pen = QPen(self._grid_zero if db == 0 else self._grid, 1)
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # Gain dB axis (left) — every 3 dB, 0 at center
        painter.setPen(self._label)
        font = QFont(self.font())
        font.setPixelSize(8)
        painter.setFont(font)
        axis_left = 1
        for db in sorted(set(self._DB_MAJOR) | set(self._DB_MINOR), reverse=True):
            y = self._db_to_y(float(db), rect)
            text = "0" if db == 0 else f"{db:+d}"
            painter.drawText(axis_left, int(y + 3), text)

        # Spectrum fill (interpolated for smoother detail)
        if (
            self._spectrum_freqs is not None
            and self._spectrum_db is not None
            and len(self._spectrum_freqs) == len(self._spectrum_db)
        ):
            freqs = np.asarray(self._spectrum_freqs, dtype=np.float64)
            db_vals = np.asarray(self._spectrum_db, dtype=np.float64)
            if freqs.size >= 2:
                freq_interp = np.geomspace(
                    max(FREQ_MIN_HZ, float(freqs[0])),
                    min(FREQ_MAX_HZ, float(freqs[-1])),
                    384,
                )
                db_interp = np.interp(freq_interp, freqs, db_vals)
            else:
                freq_interp = freqs
                db_interp = db_vals

            path = QPainterPath()
            first = True
            baseline = rect.bottom()
            for f, db in zip(freq_interp, db_interp):
                y = self._spectrum_y(float(db), rect)
                x = self._freq_to_x(float(f), rect)
                if first:
                    path.moveTo(x, baseline)
                    path.lineTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            if not first:
                path.lineTo(self._freq_to_x(float(freq_interp[-1]), rect), baseline)
                path.closeSubpath()
                painter.fillPath(path, self._spectrum)

        # EQ curve
        freqs, mag = response_db(
            self._state.bands,
            self._curve_freqs,
            bypass=self._state.bypass,
        )
        curve = QPainterPath()
        for i, (f, db) in enumerate(zip(freqs, mag)):
            x = self._freq_to_x(float(f), rect)
            y = self._db_to_y(float(db), rect)
            if i == 0:
                curve.moveTo(x, y)
            else:
                curve.lineTo(x, y)
        painter.setPen(
            QPen(self._curve_bypass if self._state.bypass else self._curve, 2.0)
        )
        painter.drawPath(curve)

        # Nodes
        for band in self._state.bands:
            p = self._band_point(band, rect)
            selected = band.id == self._state.selected_id
            r = 6.0 if selected else 5.0
            painter.setBrush(self._node_sel if selected else self._node)
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawEllipse(p, r, r)
            if selected:
                # Q handles (horizontal wings)
                wing = max(8.0, min(40.0, 28.0 / max(band.q, 0.2)))
                painter.setPen(QPen(self._node_sel, 1.5))
                painter.drawLine(QPointF(p.x() - wing, p.y()), QPointF(p.x() + wing, p.y()))

        # Axis labels (freq, bottom strip)
        painter.setPen(self._label)
        font = QFont(self.font())
        font.setPixelSize(8)
        painter.setFont(font)
        label_y = int(min(rect.bottom() + 12, self.height() - 2))
        for f, label in self._FREQ_LABELS:
            x = self._freq_to_x(f, rect)
            # Approximate center under tick
            tw = len(label) * 4.5
            painter.drawText(int(x - tw / 2), label_y, label)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._plot_rect()
        pos = event.position()
        band = self._hit_band(pos, rect)
        if band:
            self._state.select(band.id)
            self._drag_id = band.id
            # Near wing tips → adjust Q
            p = self._band_point(band, rect)
            wing = max(8.0, min(40.0, 28.0 / max(band.q, 0.2)))
            if abs(pos.x() - p.x()) > 6 and abs(pos.y() - p.y()) < 8 and abs(pos.x() - p.x()) <= wing + 4:
                self._drag_q = True
            else:
                self._drag_q = False
            self.selectionChanged.emit()
            self.bandsEdited.emit()
            self.update()
        else:
            self._state.select(None)
            self._drag_id = None
            self.selectionChanged.emit()
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self._plot_rect()
        pos = event.position()
        if self._hit_band(pos, rect):
            return
        freq = self._x_to_freq(pos.x(), rect)
        gain = self._y_to_db(pos.y(), rect)
        self._state.add_band(freq_hz=freq, gain_db=gain, type="bell")
        self.selectionChanged.emit()
        self.bandsEdited.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_id is None:
            return
        band = self._state.band_by_id(self._drag_id)
        if band is None:
            return
        rect = self._plot_rect()
        pos = event.position()
        if self._drag_q:
            p = self._band_point(band, rect)
            dist = abs(pos.x() - p.x())
            # Wider wing → lower Q
            band.q = max(Q_MIN, min(Q_MAX, 28.0 / max(dist, 4.0)))
        else:
            band.freq_hz = self._x_to_freq(pos.x(), rect)
            if band.type not in ("low_cut", "high_cut"):
                band.gain_db = self._y_to_db(pos.y(), rect)
            band.normalize()
        self.bandsEdited.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_id = None
            self._drag_q = False

    def wheelEvent(self, event: QWheelEvent) -> None:
        band = self._state.selected_band()
        if band is None:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.08 if delta > 0 else 1.0 / 1.08
        band.q = max(Q_MIN, min(Q_MAX, band.q * factor))
        self.bandsEdited.emit()
        self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            band = self._state.selected_band()
            if band:
                self._state.remove_band(band.id)
                self.selectionChanged.emit()
                self.bandsEdited.emit()
                self.update()
                event.accept()
                return
        super().keyPressEvent(event)


class ParametricEqView(QWidget):
    """EQ editor: canvas + inspector (type / Q / freq / gain)."""

    bandsChanged = pyqtSignal()
    bypassChanged = pyqtSignal(bool)

    def __init__(
        self,
        state: EqState | None = None,
        parent=None,
        *,
        compact: bool = False,
    ):
        super().__init__(parent)
        self._state = state or EqState()
        self._updating = False
        self._compact = compact
        self.setObjectName("parametricEqView")
        if compact:
            self.setObjectName("parametricEqViewCompact")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2 if compact else 4)

        self.canvas = _EqCanvas(self._state, self)
        if compact:
            self.canvas.setMinimumHeight(140)
            self.canvas.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self.canvas.setToolTip(
                "Double-click: add · Drag: freq/gain · Wheel: Q · Del: remove"
            )
        else:
            self.canvas.setMinimumHeight(140)
        root.addWidget(self.canvas, 1)

        inspector = QHBoxLayout()
        inspector.setSpacing(3 if compact else 6)
        # Keep band controls clear of canvas freq labels
        inspector.setContentsMargins(0, 2 if compact else 0, 0, 0)

        self.type_combo = QComboBox()
        for key in BAND_TYPES:
            label = BAND_TYPE_LABELS[key]
            if compact:
                # Short labels for narrow strip
                short = {
                    "bell": "Bell",
                    "low_shelf": "LS",
                    "high_shelf": "HS",
                    "low_cut": "LC",
                    "high_cut": "HC",
                }
                label = short.get(key, label)
            self.type_combo.addItem(label, key)
        self.type_combo.setObjectName("eqTypeCombo")
        self.type_combo.setMinimumWidth(52 if compact else 90)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        if not compact:
            inspector.addWidget(QLabel("TYPE"))
        inspector.addWidget(self.type_combo)

        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(FREQ_MIN_HZ, FREQ_MAX_HZ)
        self.freq_spin.setDecimals(0 if compact else 1)
        self.freq_spin.setSuffix("Hz" if compact else " Hz")
        self.freq_spin.setMaximumWidth(72 if compact else 110)
        self.freq_spin.valueChanged.connect(self._on_freq_changed)
        if not compact:
            inspector.addWidget(QLabel("FREQ"))
        inspector.addWidget(self.freq_spin)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(GAIN_MIN_DB, GAIN_MAX_DB)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setSuffix("dB" if compact else " dB")
        self.gain_spin.setSingleStep(0.5)
        self.gain_spin.setMaximumWidth(64 if compact else 100)
        self.gain_spin.valueChanged.connect(self._on_gain_changed)
        if not compact:
            inspector.addWidget(QLabel("GAIN"))
        inspector.addWidget(self.gain_spin)

        self.q_spin = QDoubleSpinBox()
        self.q_spin.setRange(Q_MIN, Q_MAX)
        self.q_spin.setDecimals(2)
        self.q_spin.setSingleStep(0.1)
        self.q_spin.setPrefix("Q " if compact else "")
        self.q_spin.setMaximumWidth(58 if compact else 90)
        self.q_spin.valueChanged.connect(self._on_q_changed)
        if not compact:
            inspector.addWidget(QLabel("Q"))
        inspector.addWidget(self.q_spin)

        self.btn_bypass = QPushButton("BYP" if compact else "BYPASS")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setObjectName("eqBypassBtn")
        self.btn_bypass.setToolTip("Bypass")
        self.btn_bypass.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_bypass.toggled.connect(self._on_bypass)

        self.btn_reset = QPushButton("RST" if compact else "RESET")
        self.btn_reset.setObjectName("eqResetBtn")
        self.btn_reset.setToolTip("Reset")
        self.btn_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_reset.clicked.connect(self._on_reset)

        self.btn_delete = QPushButton("DEL")
        self.btn_delete.setObjectName("eqDeleteBtn")
        self.btn_delete.setToolTip("Delete band")
        self.btn_delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_delete.clicked.connect(self._on_delete)

        inspector.addStretch(1)
        inspector.addWidget(self.btn_bypass)
        inspector.addWidget(self.btn_reset)
        inspector.addWidget(self.btn_delete)
        root.addLayout(inspector)

        self.canvas.bandsEdited.connect(self._on_canvas_edited)
        self.canvas.selectionChanged.connect(self._sync_inspector)
        self._sync_inspector()

    def state(self) -> EqState:
        return self._state

    def set_state(self, state: EqState) -> None:
        self._state = state
        self.canvas.set_state(state)
        self.btn_bypass.blockSignals(True)
        self.btn_bypass.setChecked(state.bypass)
        self.btn_bypass.blockSignals(False)
        self._sync_inspector()
        self.canvas.update()

    def set_spectrum(self, freqs, db) -> None:
        self.canvas.set_spectrum(freqs, db)

    def set_bypass(self, bypassed: bool) -> None:
        checked = bool(bypassed)
        if self._state.bypass == checked and self.btn_bypass.isChecked() == checked:
            return
        self.btn_bypass.blockSignals(True)
        self.btn_bypass.setChecked(checked)
        self.btn_bypass.blockSignals(False)
        self._state.bypass = checked
        self.canvas.update()
        self.bypassChanged.emit(checked)
        self._emit_changed()

    def _emit_changed(self) -> None:
        self.bandsChanged.emit()
    def _on_canvas_edited(self) -> None:
        self._sync_inspector()
        self._emit_changed()

    def _sync_inspector(self) -> None:
        band = self._state.selected_band()
        enabled = band is not None
        self._updating = True
        for w in (self.type_combo, self.freq_spin, self.gain_spin, self.q_spin, self.btn_delete):
            w.setEnabled(enabled)
        if band is None:
            self._updating = False
            return
        idx = self.type_combo.findData(band.type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.freq_spin.setValue(band.freq_hz)
        self.gain_spin.setValue(band.gain_db)
        self.q_spin.setValue(band.q)
        self.gain_spin.setEnabled(enabled and band.type not in ("low_cut", "high_cut"))
        self._updating = False

    def _on_type_changed(self) -> None:
        if self._updating:
            return
        band = self._state.selected_band()
        if not band:
            return
        band.type = str(self.type_combo.currentData())
        band.normalize()
        self.canvas.update()
        self._sync_inspector()
        self._emit_changed()

    def _on_freq_changed(self, value: float) -> None:
        if self._updating:
            return
        band = self._state.selected_band()
        if not band:
            return
        band.freq_hz = value
        band.normalize()
        self.canvas.update()
        self._emit_changed()

    def _on_gain_changed(self, value: float) -> None:
        if self._updating:
            return
        band = self._state.selected_band()
        if not band:
            return
        band.gain_db = value
        band.normalize()
        self.canvas.update()
        self._emit_changed()

    def _on_q_changed(self, value: float) -> None:
        if self._updating:
            return
        band = self._state.selected_band()
        if not band:
            return
        band.q = value
        band.normalize()
        self.canvas.update()
        self._emit_changed()

    def _on_bypass(self, checked: bool) -> None:
        self._state.bypass = checked
        self.canvas.update()
        self.bypassChanged.emit(checked)
        self._emit_changed()

    def _on_reset(self) -> None:
        self._state.reset()
        self.btn_bypass.blockSignals(True)
        self.btn_bypass.setChecked(False)
        self.btn_bypass.blockSignals(False)
        self._sync_inspector()
        self.canvas.update()
        self.bypassChanged.emit(False)
        self._emit_changed()

    def _on_delete(self) -> None:
        band = self._state.selected_band()
        if not band:
            return
        self._state.remove_band(band.id)
        self._sync_inspector()
        self.canvas.update()
        self._emit_changed()
