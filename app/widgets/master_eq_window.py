"""Master equalizer floating window with named presets."""

from __future__ import annotations

import copy
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.eq_state import EqState
from app.widgets.parametric_eq_view import ParametricEqView

_PRESET_NAME_RE = re.compile(r"^[\w][\w \-.]{0,47}$", re.UNICODE)


def _normalize_preset_name(name: str) -> str | None:
    text = " ".join(str(name or "").strip().split())
    if not text or not _PRESET_NAME_RE.match(text):
        return None
    return text


class MasterEqWindow(QMainWindow):
    """Floating window with parametric EQ for the master output."""

    eqChanged = pyqtSignal()
    presetsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_ref = parent
        self.setObjectName("masterEqWindow")
        self.setWindowTitle("Master EQ")
        self.setMinimumSize(420, 460)

        self._presets: dict[str, dict] = {}
        self._loading_preset = False

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        preset_row = QWidget()
        preset_row.setObjectName("masterEqPresetRow")
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)

        preset_label = QLabel("PRESET")
        preset_label.setObjectName("sectionLabel")

        self.preset_combo = QComboBox()
        self.preset_combo.setObjectName("masterEqPresetCombo")
        self.preset_combo.setSizePolicy(
            self.preset_combo.sizePolicy().horizontalPolicy(),
            self.preset_combo.sizePolicy().verticalPolicy(),
        )
        self.preset_combo.setMinimumWidth(140)
        self.preset_combo.setEditable(False)
        self.preset_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_combo)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("masterEqPresetBtn")
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.setToolTip("Save current Master EQ as a named preset")
        self.save_btn.clicked.connect(self._save_preset)

        self.update_btn = QPushButton("Update")
        self.update_btn.setObjectName("masterEqPresetBtn")
        self.update_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.update_btn.setToolTip("Overwrite the selected preset with current EQ")
        self.update_btn.clicked.connect(self._update_preset)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("masterEqPresetBtn")
        self.delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_btn.setToolTip("Delete the selected preset")
        self.delete_btn.clicked.connect(self._delete_preset)

        preset_layout.addWidget(preset_label, 0)
        preset_layout.addWidget(self.preset_combo, 1)
        preset_layout.addWidget(self.save_btn, 0)
        preset_layout.addWidget(self.update_btn, 0)
        preset_layout.addWidget(self.delete_btn, 0)
        root.addWidget(preset_row, 0)

        self._eq_view = ParametricEqView(EqState(), self, compact=False)
        self._eq_view.bandsChanged.connect(self.eqChanged.emit)
        self._eq_view.bypassChanged.connect(self.eqChanged.emit)
        root.addWidget(self._eq_view, 1)

        self._rebuild_combo(select_name=None)
        self._sync_preset_buttons()

    def state(self) -> EqState:
        return self._eq_view.state()

    def set_state(self, state: EqState) -> None:
        self._eq_view.set_state(state)

    def set_spectrum(self, freqs, db) -> None:
        self._eq_view.set_spectrum(freqs, db)

    def presets(self) -> dict[str, dict]:
        return copy.deepcopy(self._presets)

    def set_presets(self, presets: object, *, current: str | None = None) -> None:
        cleaned: dict[str, dict] = {}
        if isinstance(presets, dict):
            for raw_name, raw_state in presets.items():
                name = _normalize_preset_name(str(raw_name))
                if not name or not isinstance(raw_state, dict):
                    continue
                # Keep only valid EQ payloads
                state = EqState.from_dict(raw_state)
                cleaned[name] = state.to_dict()
        self._presets = cleaned
        select = _normalize_preset_name(current or "") if current else None
        if select not in self._presets:
            select = None
        self._rebuild_combo(select_name=select)
        self._sync_preset_buttons()

    def current_preset_name(self) -> str | None:
        data = self.preset_combo.currentData()
        text = str(data).strip() if data is not None else ""
        return text or None

    def _rebuild_combo(self, *, select_name: str | None) -> None:
        self._loading_preset = True
        try:
            self.preset_combo.clear()
            self.preset_combo.addItem("— Custom —", "")
            for name in sorted(self._presets.keys(), key=str.casefold):
                self.preset_combo.addItem(name, name)
            if select_name and select_name in self._presets:
                idx = self.preset_combo.findData(select_name)
                self.preset_combo.setCurrentIndex(max(0, idx))
            else:
                self.preset_combo.setCurrentIndex(0)
        finally:
            self._loading_preset = False
        self._sync_preset_buttons()

    def _sync_preset_buttons(self) -> None:
        named = self.current_preset_name() is not None
        self.update_btn.setEnabled(named)
        self.delete_btn.setEnabled(named)

    def _on_preset_combo(self, _index: int) -> None:
        if self._loading_preset:
            return
        name = self.current_preset_name()
        self._sync_preset_buttons()
        if not name:
            return
        payload = self._presets.get(name)
        if not isinstance(payload, dict):
            return
        self._loading_preset = True
        try:
            self._eq_view.set_state(EqState.from_dict(payload))
        finally:
            self._loading_preset = False
        self.eqChanged.emit()

    def _save_preset(self) -> None:
        suggested = self.current_preset_name() or "Master"
        name, ok = QInputDialog.getText(
            self,
            "Save Master EQ Preset",
            "Preset name:",
            text=suggested,
        )
        if not ok:
            return
        normalized = _normalize_preset_name(name)
        if not normalized:
            QMessageBox.warning(
                self,
                "Save Preset",
                "Use a short name with letters, numbers, spaces, - or .",
            )
            return
        if normalized in self._presets:
            answer = QMessageBox.question(
                self,
                "Overwrite Preset",
                f"Preset “{normalized}” already exists. Overwrite?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._presets[normalized] = self.state().to_dict()
        self._rebuild_combo(select_name=normalized)
        self.presetsChanged.emit()

    def _update_preset(self) -> None:
        name = self.current_preset_name()
        if not name:
            return
        self._presets[name] = self.state().to_dict()
        self.presetsChanged.emit()

    def _delete_preset(self) -> None:
        name = self.current_preset_name()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Delete Preset",
            f"Delete preset “{name}”?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(name, None)
        self._rebuild_combo(select_name=None)
        self.presetsChanged.emit()
