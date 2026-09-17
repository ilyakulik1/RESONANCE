"""Master FX rack: VST/AU insert chain on the air output."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.vst_host import MAX_SLOTS, PluginSlot, pedalboard_available, scan_installed_plugins


class _SlotRow(QWidget):
    enabledToggled = pyqtSignal(str, bool)
    editClicked = pyqtSignal(str)
    moveClicked = pyqtSignal(str, int)
    removeClicked = pyqtSignal(str)

    def __init__(self, slot: PluginSlot, parent=None):
        super().__init__(parent)
        self.slot_id = slot.id
        self.setObjectName("masterFxRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(3)

        self.btn_on = QPushButton("ON")
        self.btn_on.setObjectName("masterFxBtn")
        self.btn_on.setCheckable(True)
        self.btn_on.setChecked(bool(slot.enabled) and not slot.error)
        self.btn_on.setEnabled(slot.error is None)
        self.btn_on.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_on.setToolTip("Enable this plugin")
        self.btn_on.toggled.connect(
            lambda checked, slot_id=slot.id: self.enabledToggled.emit(slot_id, checked)
        )

        self.name = QLabel(slot.name or Path(slot.path).stem)
        self.name.setObjectName("masterFxName")
        self.name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if slot.error:
            self.name.setText(f"{self.name.text()} — {slot.error}")
            self.name.setStyleSheet("color: #e45656;")
        self.name.setToolTip(slot.error or slot.path)

        self.btn_edit = QPushButton("EDIT")
        self.btn_edit.setObjectName("masterFxBtn")
        self.btn_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_edit.setEnabled(slot.error is None)
        self.btn_edit.setToolTip("Open plugin editor")
        self.btn_edit.clicked.connect(lambda: self.editClicked.emit(slot.id))

        self.btn_up = QPushButton("↑")
        self.btn_up.setObjectName("masterFxBtn")
        self.btn_up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_up.setToolTip("Move up")
        self.btn_up.clicked.connect(lambda: self.moveClicked.emit(slot.id, -1))

        self.btn_down = QPushButton("↓")
        self.btn_down.setObjectName("masterFxBtn")
        self.btn_down.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_down.setToolTip("Move down")
        self.btn_down.clicked.connect(lambda: self.moveClicked.emit(slot.id, 1))

        self.btn_remove = QPushButton("×")
        self.btn_remove.setObjectName("masterFxBtn")
        self.btn_remove.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_remove.setToolTip("Remove")
        self.btn_remove.clicked.connect(lambda: self.removeClicked.emit(slot.id))

        layout.addWidget(self.btn_on, 0)
        layout.addWidget(self.name, 1)
        layout.addWidget(self.btn_edit, 0)
        layout.addWidget(self.btn_up, 0)
        layout.addWidget(self.btn_down, 0)
        layout.addWidget(self.btn_remove, 0)


class PluginPickerDialog(QDialog):
    """Pick an installed VST3/AU or browse to a plugin file."""

    def __init__(self, parent=None, *, start_dir: str = ""):
        super().__init__(parent)
        self.setObjectName("pluginPickerDialog")
        self.setWindowTitle("Add Master FX")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMinimumHeight(360)
        self._start_dir = start_dir
        self.selected_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter plugins…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.filter_edit)

        self.list = QListWidget()
        self.list.setObjectName("pluginPickerList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemDoubleClicked.connect(self._accept_item)
        root.addWidget(self.list, 1)

        self.status = QLabel("Scanning…")
        self.status.setObjectName("masterFxHint")
        root.addWidget(self.status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        browse = buttons.addButton("Browse…", QDialogButtonBox.ButtonRole.ActionRole)
        browse.clicked.connect(self._browse)
        buttons.accepted.connect(self._accept_current)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._plugins: list[tuple[str, str]] = []
        QTimer.singleShot(0, self._load_list)

    def _load_list(self) -> None:
        self._plugins = scan_installed_plugins()
        self._apply_filter(self.filter_edit.text())
        if self._plugins:
            self.status.setText(f"{len(self._plugins)} plugin(s) found")
        else:
            self.status.setText("No plugins in standard folders — use Browse…")

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        self.list.clear()
        for name, path in self._plugins:
            if needle and needle not in name.lower() and needle not in path.lower():
                continue
            item = QListWidgetItem(name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list.addItem(item)
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _browse(self) -> None:
        start = self._start_dir or str(Path.home())
        dialog = QFileDialog(self, "Open VST3 / AU plugin", start)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if sys.platform == "darwin":
            dialog.setFileMode(QFileDialog.FileMode.Directory)
        else:
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setNameFilter("Plugins (*.vst3 *.component *.vst);;All files (*)")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        files = dialog.selectedFiles()
        if not files:
            return
        path = files[0]
        suffix = Path(path).suffix.lower()
        if suffix not in {".vst3", ".component", ".vst"} and not Path(path).is_file():
            self.status.setText("Select a .vst3, .component, or .vst plugin")
            return
        self.selected_path = path
        self.accept()

    def _accept_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.selected_path = str(path)
            self.accept()

    def _accept_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self._accept_item(item)


class MasterFxPanel(QWidget):
    addClicked = pyqtSignal()
    removeClicked = pyqtSignal(str)
    moveClicked = pyqtSignal(str, int)
    editClicked = pyqtSignal(str)
    enabledToggled = pyqtSignal(str, bool)
    bypassToggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("masterFxPanel")
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 0, 4, 2)
        root.setSpacing(3)

        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(4)

        self.btn_bypass = QPushButton("BYP")
        self.btn_bypass.setObjectName("eqBypassBtn")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_bypass.setToolTip("Bypass master FX chain")
        self.btn_bypass.toggled.connect(self._on_bypass)

        self.btn_add = QPushButton("ADD")
        self.btn_add.setObjectName("eqResetBtn")
        self.btn_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_add.setToolTip("Add a VST3 or Audio Unit effect")
        self.btn_add.clicked.connect(self.addClicked.emit)

        self.hint = QLabel()
        self.hint.setObjectName("masterFxHint")
        self.hint.setWordWrap(False)

        toolbar_layout.addWidget(self.btn_bypass, 0)
        toolbar_layout.addWidget(self.hint, 1)
        toolbar_layout.addWidget(self.btn_add, 0)
        root.addWidget(toolbar)

        self._rows = QWidget()
        self._rows.setObjectName("masterFxRows")
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("masterFxScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._scroll.setWidget(self._rows)
        self._scroll.hide()
        root.addWidget(self._scroll)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.set_slots([], bypass=False)

    def _on_bypass(self, checked: bool) -> None:
        if self._loading:
            return
        self.bypassToggled.emit(bool(checked))

    def set_slots(self, slots: list[PluginSlot], *, bypass: bool) -> None:
        self._loading = True
        try:
            self.btn_bypass.setChecked(bool(bypass))
            while self._rows_layout.count():
                item = self._rows_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

            available = pedalboard_available()
            self.btn_add.setEnabled(available and len(slots) < MAX_SLOTS)
            if not available:
                self.hint.setText("Install pedalboard to host VST3/AU plugins.")
                self.hint.show()
            elif not slots:
                self.hint.setText("No plugins")
            else:
                self.hint.setText("")
            self.hint.show()

            for slot in slots:
                row = _SlotRow(slot, self._rows)
                row.enabledToggled.connect(self.enabledToggled)
                row.editClicked.connect(self.editClicked)
                row.moveClicked.connect(self.moveClicked)
                row.removeClicked.connect(self.removeClicked)
                self._rows_layout.addWidget(row)

            if slots:
                row_h = 26
                self._scroll.show()
                self._scroll.setFixedHeight(min(78, max(row_h, len(slots) * row_h)))
            else:
                self._scroll.hide()
                self._scroll.setFixedHeight(0)
        finally:
            self._loading = False
