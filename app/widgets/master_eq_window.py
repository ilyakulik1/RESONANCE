"""Master equalizer floating window."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from app.eq_state import EqState
from app.widgets.parametric_eq_view import ParametricEqView


class MasterEqWindow(QMainWindow):
    """Floating window with parametric EQ for the master output."""

    eqChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_ref = parent
        self.setObjectName("masterEqWindow")
        self.setWindowTitle("Master EQ")
        self.setMinimumSize(420, 420)

        # Central widget
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # EQ view — non-compact for full controls
        self._eq_view = ParametricEqView(EqState(), self, compact=False)
        self._eq_view.bandsChanged.connect(self.eqChanged.emit)
        self._eq_view.bypassChanged.connect(self.eqChanged.emit)
        root.addWidget(self._eq_view, 1)

    def state(self) -> EqState:
        """Return current EQ state."""
        return self._eq_view.state()

    def set_state(self, state: EqState) -> None:
        """Set EQ state and update UI."""
        self._eq_view.set_state(state)

    def set_spectrum(self, freqs, db) -> None:
        """Set spectrum overlay."""
        self._eq_view.set_spectrum(freqs, db)
