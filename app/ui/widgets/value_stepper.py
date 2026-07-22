from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from app.ui.icon_loader import icon_size, load_icon
from app.ui.tokens import get_token_int


class ValueStepper(QWidget):
    """Vertical up/down arrows with a value in a control box."""

    valueChanged = pyqtSignal(int)

    def __init__(
        self,
        host: QWidget,
        minimum: int,
        maximum: int,
        value: int,
        *,
        step: int = 1,
        box_size: int | None = None,
        value_width: int | None = None,
        editable: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._editable = editable
        self._step = max(1, step)
        stepper_btn = get_token_int("sizes.stepper_btn", 12)
        stepper_icon = get_token_int("sizes.stepper_icon", 8)
        arrow_spacing = get_token_int("spacing.xs", 2)
        arrow_stack_height = stepper_btn * 2 + arrow_spacing
        if box_size is None:
            box_size = get_token_int("sizes.stepper_value", arrow_stack_height)
        box_size = max(box_size, arrow_stack_height)
        label_width = value_width if value_width is not None else box_size
        self._spin = QSpinBox()
        self._spin.setRange(minimum, maximum)
        self._spin.setSingleStep(self._step)
        self._spin.setValue(value)
        if not editable:
            # Hidden spin must not steal ↑/↓ from playlists via tab/focus.
            self._spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        arrows = QVBoxLayout()
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(arrow_spacing)

        btn_up = QPushButton()
        btn_up.setObjectName("stepperBtn")
        btn_up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_up.setAutoDefault(False)
        btn_up.setDefault(False)
        btn_up.setFixedSize(stepper_btn, stepper_btn)
        btn_up.setIcon(load_icon(host, "up", stepper_icon))
        btn_up.setIconSize(icon_size(stepper_icon))

        btn_down = QPushButton()
        btn_down.setObjectName("stepperBtn")
        btn_down.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_down.setAutoDefault(False)
        btn_down.setDefault(False)
        btn_down.setFixedSize(stepper_btn, stepper_btn)
        btn_down.setIcon(load_icon(host, "down", stepper_icon))
        btn_down.setIconSize(icon_size(stepper_icon))

        btn_up.clicked.connect(lambda: self._adjust(1))
        btn_down.clicked.connect(lambda: self._adjust(-1))
        arrows.addWidget(btn_up)
        arrows.addWidget(btn_down)

        layout.addLayout(arrows)

        if editable:
            self._value_label = None
            self._spin.setObjectName("stepperSpin")
            self._spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._spin.setFixedSize(label_width, arrow_stack_height)
            layout.addWidget(self._spin)
        else:
            self._spin.hide()
            self._value_label = QLabel(str(value))
            self._value_label.setObjectName("stepperValue")
            self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._value_label.setFixedSize(label_width, arrow_stack_height)
            layout.addWidget(self._value_label)

        self._spin.valueChanged.connect(self._sync_from_spin)

    def _adjust(self, delta: int) -> None:
        self._spin.setValue(self._spin.value() + delta * self._step)

    def _sync_from_spin(self, value: int) -> None:
        if self._value_label is not None:
            self._value_label.setText(str(value))
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self._spin.value()

    def set_value(self, value: int) -> None:
        self._spin.setValue(value)

    def spin_box(self) -> QSpinBox:
        return self._spin
