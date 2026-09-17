from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from app.ui.icon_loader import icon_size, load_icon
from app.ui.tokens import get_token_int


class StepperButton(QPushButton):
    """Tiny arrow: whole rect is clickable, never steals focus."""

    def __init__(self, host: QWidget, icon_name: str, parent=None):
        super().__init__(parent)
        stepper_btn = get_token_int("sizes.stepper_btn", 12)
        stepper_icon = get_token_int("sizes.stepper_icon", 8)
        self.setObjectName("stepperBtn")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFlat(True)
        self.setAutoRepeat(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        mac_small = getattr(Qt.WidgetAttribute, "WA_MacSmallSize", None)
        if mac_small is not None:
            self.setAttribute(mac_small, True)
        self.setFixedSize(stepper_btn, stepper_btn)
        self.setIcon(load_icon(host, icon_name, stepper_icon))
        self.setIconSize(icon_size(stepper_icon))

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802
        return True


def arrow_stack_height() -> int:
    stepper_btn = get_token_int("sizes.stepper_btn", 12)
    arrow_spacing = get_token_int("spacing.xs", 2)
    return stepper_btn * 2 + arrow_spacing


def _arrow_column(host: QWidget, on_up, on_down) -> tuple[QVBoxLayout, StepperButton, StepperButton]:
    arrow_spacing = get_token_int("spacing.xs", 2)
    arrows = QVBoxLayout()
    arrows.setContentsMargins(0, 0, 0, 0)
    arrows.setSpacing(arrow_spacing)
    btn_up = StepperButton(host, "up")
    btn_down = StepperButton(host, "down")
    # pressed: tiny buttons lose `clicked` if the mouse moves 1px or focus
    # jumps back to the playlist before release.
    btn_up.pressed.connect(on_up)
    btn_down.pressed.connect(on_down)
    arrows.addWidget(btn_up)
    arrows.addWidget(btn_down)
    return arrows, btn_up, btn_down


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
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._editable = editable
        self._step = max(1, step)
        arrows, btn_up, btn_down = _arrow_column(
            host,
            lambda: self._adjust(1),
            lambda: self._adjust(-1),
        )
        stack_h = arrow_stack_height()
        if box_size is None:
            box_size = get_token_int("sizes.stepper_value", stack_h)
        box_size = max(box_size, stack_h)
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

        self._btn_up = btn_up
        self._btn_down = btn_down
        layout.addLayout(arrows)

        if editable:
            self._value_label = None
            self._spin.setObjectName("stepperSpin")
            self._spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._spin.setFixedSize(label_width, stack_h)
            layout.addWidget(self._spin)
        else:
            self._spin.hide()
            self._value_label = QLabel(str(value))
            self._value_label.setObjectName("stepperValue")
            self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._value_label.setFixedSize(label_width, stack_h)
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

    def up_button(self) -> QPushButton:
        return self._btn_up

    def down_button(self) -> QPushButton:
        return self._btn_down
