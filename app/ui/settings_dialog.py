"""Settings for transition-duration and high-cut Q presets."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.constants import FADE_PRESETS_MS, fade_preset_label
from app.eq_dsp import (
    HIGH_CUT_Q_MAX,
    HIGH_CUT_Q_MIN,
    HIGH_CUT_Q_PRESETS,
    high_cut_q_preset_label,
)
from app.ui.tokens import get_token

FADE_PRESET_MAX_MS = 5000


def normalize_fade_presets(values) -> list[int]:
    cleaned: list[int] = []
    for raw in values or []:
        try:
            ms = int(raw)
        except (TypeError, ValueError):
            continue
        ms = max(0, min(FADE_PRESET_MAX_MS, ms))
        if ms not in cleaned:
            cleaned.append(ms)
    cleaned.sort()
    return cleaned or list(FADE_PRESETS_MS)


def normalize_q_presets(values) -> list[float]:
    cleaned: list[float] = []
    for raw in values or []:
        try:
            q = round(float(raw), 2)
        except (TypeError, ValueError):
            continue
        q = max(HIGH_CUT_Q_MIN, min(HIGH_CUT_Q_MAX, q))
        if any(abs(q - existing) < 0.001 for existing in cleaned):
            continue
        cleaned.append(q)
    cleaned.sort()
    return cleaned or [float(value) for value in HIGH_CUT_Q_PRESETS]


class _PresetList(QWidget):
    """Editable unique list: add a number, delete the selected row."""

    def __init__(
        self,
        values: list,
        *,
        kind: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(120)
        self._list.setMaximumHeight(168)
        self._list.currentItemChanged.connect(lambda *_args: self._sync_delete())
        self._list.itemSelectionChanged.connect(self._sync_delete)

        if kind == "fade":
            self._spin: QSpinBox | QDoubleSpinBox = QSpinBox()
            self._spin.setRange(0, FADE_PRESET_MAX_MS)
            self._spin.setSingleStep(100)
            self._spin.setSuffix(" ms")
            self._spin.setValue(200)
        else:
            self._spin = QDoubleSpinBox()
            self._spin.setRange(HIGH_CUT_Q_MIN, HIGH_CUT_Q_MAX)
            self._spin.setDecimals(2)
            self._spin.setSingleStep(0.1)
            self._spin.setValue(1.0)
        self._spin.setMinimumWidth(96)
        line = self._spin.lineEdit()
        if line is not None:
            line.returnPressed.connect(self._add)

        add = QPushButton("Add")
        add.setAutoDefault(False)
        add.setDefault(False)
        add.setMinimumWidth(72)
        add.clicked.connect(self._add)
        self._delete = QPushButton("Delete")
        self._delete.setAutoDefault(False)
        self._delete.setDefault(False)
        self._delete.setMinimumWidth(72)
        self._delete.clicked.connect(self._delete_selected)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._spin, 1)
        row.addWidget(add, 0)
        row.addWidget(self._delete, 0)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.addWidget(self._list)
        root.addLayout(row)
        self._fill(values)

    def values(self) -> list:
        raw = [
            self._list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self._list.count())
        ]
        if self._kind == "fade":
            return normalize_fade_presets(raw)
        return normalize_q_presets(raw)

    def _fill(self, values: list) -> None:
        normalized = (
            normalize_fade_presets(values)
            if self._kind == "fade"
            else normalize_q_presets(values)
        )
        self._list.clear()
        for value in normalized:
            if self._kind == "fade":
                ms = int(value)
                if ms <= 0:
                    label = "CUT"
                elif ms >= 1000 and ms % 1000 == 0:
                    label = fade_preset_label(ms)
                else:
                    label = f"{ms} ms"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, ms)
            else:
                q = float(value)
                item = QListWidgetItem(high_cut_q_preset_label(q))
                item.setData(Qt.ItemDataRole.UserRole, q)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._sync_delete()

    def _add(self) -> None:
        current = self.values()
        if self._kind == "fade":
            added = int(self._spin.value())
            current.append(added)
            current = normalize_fade_presets(current)
            select = added
        else:
            added = round(float(self._spin.value()), 2)
            current.append(added)
            current = normalize_q_presets(current)
            select = added
        self._fill(current)
        for row in range(self._list.count()):
            stored = self._list.item(row).data(Qt.ItemDataRole.UserRole)
            if self._kind == "fade" and int(stored) == int(select):
                self._list.setCurrentRow(row)
                break
            if self._kind != "fade" and abs(float(stored) - float(select)) < 0.001:
                self._list.setCurrentRow(row)
                break

    def _delete_selected(self) -> None:
        row = self._list.currentRow()
        if row < 0 or self._list.count() <= 1:
            return
        self._list.takeItem(row)
        if self._list.count():
            self._list.setCurrentRow(min(row, self._list.count() - 1))
        self._sync_delete()

    def _sync_delete(self) -> None:
        self._delete.setEnabled(self._list.count() > 1 and self._list.currentRow() >= 0)


class SettingsDialog(QDialog):
    """Add and remove transition-duration and Q-res presets."""

    def __init__(
        self,
        fade_presets: list[int],
        q_presets: list[float],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)

        fade_title = QLabel("TRANSITION")
        fade_title.setObjectName("sectionLabel")
        root.addWidget(fade_title)
        self._fade = _PresetList(list(fade_presets), kind="fade")
        root.addWidget(self._fade)

        q_title = QLabel("Q-RES")
        q_title.setObjectName("sectionLabel")
        root.addWidget(q_title)
        self._q = _PresetList(list(q_presets), kind="q")
        root.addWidget(self._q)

        hint = QLabel("← → steps duration. Shift+← → steps fade mode.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {get_token('colors.text_muted', '#8c8c92')};")
        root.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def fade_presets(self) -> list[int]:
        return self._fade.values()

    def q_presets(self) -> list[float]:
        return self._q.values()
