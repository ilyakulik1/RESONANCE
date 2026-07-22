from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.ui.icon_loader import icon_size, load_icon
from app.ui.tokens import get_token_int


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
        self._step = float(step)
        stepper_btn = get_token_int("sizes.stepper_btn", 12)
        stepper_icon = get_token_int("sizes.stepper_icon", 8)
        arrow_spacing = get_token_int("spacing.xs", 2)
        arrow_stack_height = stepper_btn * 2 + arrow_spacing
        label_width = value_width if value_width is not None else 56

        self._spin = QDoubleSpinBox()
        self._spin.setRange(minimum, maximum)
        self._spin.setSingleStep(self._step)
        self._spin.setDecimals(decimals)
        self._spin.setValue(value)
        self._spin.setObjectName("stepperSpin")
        self._spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spin.setFixedSize(label_width, arrow_stack_height)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        arrows = QVBoxLayout()
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(arrow_spacing)

        btn_up = QPushButton()
        btn_up.setObjectName("stepperBtn")
        btn_up.setFixedSize(stepper_btn, stepper_btn)
        btn_up.setIcon(load_icon(host, "up", stepper_icon))
        btn_up.setIconSize(icon_size(stepper_icon))

        btn_down = QPushButton()
        btn_down.setObjectName("stepperBtn")
        btn_down.setFixedSize(stepper_btn, stepper_btn)
        btn_down.setIcon(load_icon(host, "down", stepper_icon))
        btn_down.setIconSize(icon_size(stepper_icon))

        btn_up.clicked.connect(lambda: self._adjust(1))
        btn_down.clicked.connect(lambda: self._adjust(-1))
        arrows.addWidget(btn_up)
        arrows.addWidget(btn_down)

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
