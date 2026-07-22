from PyQt6.QtCore import QObject, QElapsedTimer


class VolumeFader(QObject):
    """Linear volume ramp; the owner must call advance() from the UI thread."""

    def __init__(self, apply_volume, parent=None):
        super().__init__(parent)
        self._apply_volume = apply_volume
        self._elapsed = QElapsedTimer()
        self._start_volume = 0.0
        self._end_volume = 0.0
        self._current_volume = 0.0
        self._duration_ms = 0
        self._on_complete = None
        self._active = False

    def cancel(self):
        self._active = False
        self._on_complete = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def current_volume(self) -> float:
        return self._current_volume

    def sync_volume(self, volume: float) -> None:
        self._current_volume = max(0.0, min(1.0, volume))

    def fade_to(self, volume: float, duration_ms: int, on_complete=None):
        self.cancel()
        target = max(0.0, min(1.0, volume))
        if duration_ms <= 0:
            self._current_volume = target
            self._apply_volume(target)
            if on_complete:
                on_complete()
            return

        self._start_volume = self._current_volume
        self._end_volume = target
        self._duration_ms = duration_ms
        self._on_complete = on_complete
        self._active = True
        self._elapsed.start()
        self._apply_volume(self._start_volume)

    def retarget(self, volume: float, duration_ms: int, on_complete=None) -> None:
        target = max(0.0, min(1.0, volume))
        if duration_ms <= 0:
            self.cancel()
            self._current_volume = target
            self._apply_volume(target)
            if on_complete:
                on_complete()
            return

        self._start_volume = self._current_volume
        self._end_volume = target
        self._duration_ms = duration_ms
        self._on_complete = on_complete
        self._active = True
        self._elapsed.restart()
        self._apply_volume(self._start_volume)

    def advance(self) -> None:
        if not self._active:
            return

        elapsed = self._elapsed.elapsed()
        if elapsed >= self._duration_ms:
            self._current_volume = self._end_volume
            self._apply_volume(self._end_volume)
            self._active = False
            callback = self._on_complete
            self._on_complete = None
            if callback:
                callback()
            return

        progress = elapsed / self._duration_ms
        volume = self._start_volume + (self._end_volume - self._start_volume) * progress
        self._current_volume = volume
        self._apply_volume(volume)
