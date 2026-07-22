from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QFrame, QLabel, QLineEdit, QVBoxLayout


class SectionHeader(QFrame):
    """Gray title bar matching Figma section headers."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("sectionHeader")
        self.setFixedHeight(20)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(title.upper())
        self._label.setObjectName("sectionHeaderTitle")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)


class EditableSectionHeader(QFrame):
    """Section header with double-click rename."""

    titleChanged = pyqtSignal(str)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("sectionHeader")
        self.setFixedHeight(20)

        self._title = title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel(title.upper())
        self._label.setObjectName("sectionHeaderTitle")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setToolTip("Double-click to rename")
        self._label.setCursor(Qt.CursorShape.IBeamCursor)
        layout.addWidget(self._label)

        self._editor = QLineEdit(title)
        self._editor.setObjectName("sectionHeaderEditor")
        self._editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._editor.setMaxLength(48)
        self._editor.hide()
        self._editor.installEventFilter(self)
        layout.addWidget(self._editor)

        self._editor.editingFinished.connect(self._commit_edit)

    def title(self) -> str:
        return self._title

    def setTitle(self, title: str) -> None:
        text = title.strip()
        if not text:
            return
        self._title = text
        self._label.setText(self._title.upper())
        self._editor.setText(self._title)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._editor and event.type() == event.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() == Qt.Key.Key_Escape:
                self._cancel_edit()
                return True
        return super().eventFilter(obj, event)

    def _begin_edit(self) -> None:
        self._editor.setText(self._title)
        self._label.hide()
        self._editor.show()
        self._editor.setFocus()
        self._editor.selectAll()

    def _commit_edit(self) -> None:
        if self._editor.isHidden():
            return

        new_title = self._editor.text().strip()
        if not new_title:
            new_title = self._title

        self._editor.hide()
        self._label.show()

        if new_title != self._title:
            self.setTitle(new_title)
            self.titleChanged.emit(new_title)

    def _cancel_edit(self) -> None:
        self._editor.hide()
        self._label.show()
        self._editor.setText(self._title)
