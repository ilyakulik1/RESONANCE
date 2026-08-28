from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QWidget


class SegmentButtonGroup(QWidget):
    """Exclusive toggle buttons; the selected option is highlighted red."""

    valueChanged = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._options = options
        self._buttons: dict[str, QPushButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for value, label in options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setObjectName("segmentButton")
            btn.setProperty("segmentValue", value)
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, v=value: self._on_clicked(v))
            self._group.addButton(btn)
            self._buttons[value] = btn
            layout.addWidget(btn)

        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if options:
            self._buttons[options[0][0]].setChecked(True)
        self._update_styles()

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _on_clicked(self, value: str) -> None:
        self._update_styles()
        self.valueChanged.emit(value)

    def _update_styles(self) -> None:
        for btn in self._buttons.values():
            btn.setProperty("segmentActive", btn.isChecked())
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_value(self, value: str) -> None:
        btn = self._buttons.get(value)
        if btn:
            btn.setChecked(True)
            self._update_styles()

    def value(self) -> str:
        checked = self._group.checkedButton()
        if checked:
            return checked.property("segmentValue")
        return self._options[0][0]
