"""Collapsible file properties panel with parametric EQ + spectrum."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.eq_state import EqState
from app.spectrum_feeder import read_audio_file_info
from app.ui.widgets.section_header import SectionHeader
from app.widgets.parametric_eq_view import ParametricEqView


class FilePropertiesPanel(QFrame):
    """Right-of-control-panel: metadata | compact EQ side by side."""

    collapseRequested = pyqtSignal()
    eqChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("filePropertiesPanel")
        self.setMinimumWidth(420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self._eq_collapsed = False
        self._file_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)
        header_row.addWidget(SectionHeader("FILE PROPERTIES"), 1)
        self.btn_eq_toggle = QToolButton()
        self.btn_eq_toggle.setObjectName("eqSectionToggle")
        self.btn_eq_toggle.setText("EQ")
        self.btn_eq_toggle.setCheckable(True)
        self.btn_eq_toggle.setChecked(True)
        self.btn_eq_toggle.setToolTip("Show / hide equalizer")
        self.btn_eq_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_eq_toggle.clicked.connect(self._toggle_eq_section)
        header_row.addWidget(self.btn_eq_toggle)
        self.btn_collapse = QToolButton()
        self.btn_collapse.setObjectName("propertiesCollapseBtn")
        self.btn_collapse.setText("«")
        self.btn_collapse.setToolTip("Collapse file properties")
        self.btn_collapse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_collapse.clicked.connect(self.collapseRequested.emit)
        header_row.addWidget(self.btn_collapse)
        root.addLayout(header_row)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)

        # --- Left: compact metadata ---
        meta_col = QFrame()
        meta_col.setObjectName("propertiesMetaFrame")
        meta_col.setFixedWidth(148)
        meta_col.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        meta_grid = QGridLayout(meta_col)
        meta_grid.setContentsMargins(6, 4, 6, 4)
        meta_grid.setHorizontalSpacing(6)
        meta_grid.setVerticalSpacing(1)

        self._meta_labels: dict[str, QLabel] = {}
        rows = (
            ("name", "NAME"),
            ("format", "FMT"),
            ("duration", "TIME"),
            ("sample_rate", "SR"),
            ("bitrate", "BR"),
            ("channels", "CH"),
            ("bpm", "BPM"),
        )
        for row, (key, title) in enumerate(rows):
            title_lbl = QLabel(title)
            title_lbl.setObjectName("propertiesMetaKey")
            value_lbl = QLabel("—")
            value_lbl.setObjectName("propertiesMetaValue")
            value_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value_lbl.setWordWrap(True)
            meta_grid.addWidget(title_lbl, row, 0, Qt.AlignmentFlag.AlignTop)
            meta_grid.addWidget(value_lbl, row, 1, Qt.AlignmentFlag.AlignTop)
            self._meta_labels[key] = value_lbl
        body.addWidget(meta_col, 0)

        # --- Right: compact EQ ---
        self.eq_body = QWidget()
        self.eq_body.setObjectName("eqSectionBody")
        self.eq_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        eq_body_layout = QVBoxLayout(self.eq_body)
        eq_body_layout.setContentsMargins(0, 0, 0, 0)
        eq_body_layout.setSpacing(2)

        eq_title = QLabel("EQUALIZER")
        eq_title.setObjectName("propertiesEqTitle")
        eq_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eq_body_layout.addWidget(eq_title)

        self.eq_view = ParametricEqView(EqState(), self, compact=True)
        self.eq_view.setMinimumHeight(140)
        self.eq_view.setMaximumHeight(180)
        self.eq_view.bandsChanged.connect(self.eqChanged.emit)
        self.eq_view.bypassChanged.connect(lambda _c: self.eqChanged.emit())
        eq_body_layout.addWidget(self.eq_view, 1)
        body.addWidget(self.eq_body, 1)

        root.addLayout(body)

    def eq_state(self) -> EqState:
        return self.eq_view.state()

    def set_eq_state(self, state: EqState) -> None:
        self.eq_view.set_state(state)

    def set_spectrum(self, freqs, db) -> None:
        self.eq_view.set_spectrum(freqs, db)

    def set_file(self, path: str | None, *, bpm: float | None = None) -> None:
        self._file_path = path
        info = read_audio_file_info(path)
        if bpm is not None and bpm > 0:
            info["bpm"] = f"{bpm:.1f}".rstrip("0").rstrip(".") if isinstance(bpm, float) else str(bpm)
        for key, label in self._meta_labels.items():
            label.setText(info.get(key, "—"))

    def set_bpm(self, bpm: float | None) -> None:
        if bpm is None or bpm <= 0:
            self._meta_labels["bpm"].setText("—")
        else:
            text = f"{bpm:.1f}".rstrip("0").rstrip(".")
            self._meta_labels["bpm"].setText(text)

    def is_eq_collapsed(self) -> bool:
        return self._eq_collapsed

    def set_eq_collapsed(self, collapsed: bool) -> None:
        self._eq_collapsed = bool(collapsed)
        self.eq_body.setVisible(not self._eq_collapsed)
        self.btn_eq_toggle.blockSignals(True)
        self.btn_eq_toggle.setChecked(not self._eq_collapsed)
        self.btn_eq_toggle.blockSignals(False)
        self.setMinimumWidth(160 if self._eq_collapsed else 420)

    def _toggle_eq_section(self) -> None:
        self.set_eq_collapsed(self.btn_eq_toggle.isChecked() is False)
        self.eqChanged.emit()


def make_properties_expand_button(parent=None) -> QPushButton:
    btn = QPushButton("»")
    btn.setObjectName("propertiesExpandBtn")
    btn.setToolTip("Expand file properties")
    btn.setFixedWidth(18)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    btn.hide()
    return btn
