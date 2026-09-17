from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QWidget

from app.ui.widgets.value_stepper import _arrow_column, arrow_stack_height


class FloatValueStepper(QWidget):
    """Vertical up/down arrows with a float value (QDoubleSpinBox)."""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        host: QWidget,
        minimum: float,
        maximum: float,
        value: float,
        *,
        step: float = 1.0,
        decimals: int = 1,
        value_width: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._step = float(step)
        stack_h = arrow_stack_height()
        label_width = value_width if value_width is not None else 56

        self._spin = QDoubleSpinBox()
        self._spin.setRange(minimum, maximum)
        self._spin.setSingleStep(self._step)
        self._spin.setDecimals(decimals)
        self._spin.setValue(value)
        self._spin.setObjectName("stepperSpin")
        self._spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spin.setFixedSize(label_width, stack_h)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        arrows, btn_up, btn_down = _arrow_column(
            host,
            lambda: self._adjust(1),
            lambda: self._adjust(-1),
        )
        self._btn_up = btn_up
        self._btn_down = btn_down
        layout.addLayout(arrows)
        layout.addWidget(self._spin)
        self._spin.valueChanged.connect(self._on_spin)

    def _adjust(self, direction: int) -> None:
        self._spin.setValue(self._spin.value() + direction * self._step)

    def _on_spin(self, value: float) -> None:
        self.valueChanged.emit(float(value))

    def value(self) -> float:
        return float(self._spin.value())

    def set_value(self, value: float) -> None:
        self._spin.blockSignals(True)
        self._spin.setValue(float(value))
        self._spin.blockSignals(False)

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def spin_box(self) -> QDoubleSpinBox:
        return self._spin
