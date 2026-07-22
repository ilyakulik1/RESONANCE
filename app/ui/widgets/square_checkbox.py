from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from app.ui.tokens import get_token, get_token_int


class SquareCheckBox(QWidget):

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._outer_size = get_token_int("sizes.indicator", 14)
        self._inner_size = get_token_int("sizes.checkbox_inner", 6)
        self.setFixedSize(self._outer_size, self._outer_size)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if self._checked == checked:
            return
        self._checked = checked
        self.update()
        self.toggled.emit(checked)

    def toggle(self) -> None:
        self.setChecked(not self._checked)

    def sizeHint(self) -> QSize:
        return QSize(self._outer_size, self._outer_size)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        frame_color = QColor(get_token("colors.bg_control", "#404040"))
        fill_color = QColor(get_token("colors.text_primary", "#ffffff"))

        # Эта строка рисует внешний квадрат чекбокса цветом frame_color, размером _outer_size × _outer_size, начиная с координаты (0, 0)
        painter.fillRect(0, 0, self._outer_size, self._outer_size, frame_color)

        # Эта строка рисует внутренний квадрат чекбокса цветом fill_color, размером _inner_size × _inner_size, начиная с координаты (offset, offset)
        if self.isChecked():
            offset = (self._outer_size - self._inner_size) // 2
            painter.fillRect(offset, offset, self._inner_size, self._inner_size, fill_color)
