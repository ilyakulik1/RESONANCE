from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.remote.bridge import RemoteBridge
from app.remote.lan import urls_for_port
from app.remote.server import RemoteHttpServer

if TYPE_CHECKING:
    from app.player import AudioPlayer

DEFAULT_REMOTE_PORT = 8765


class RemoteControl(QObject):
    """Owns the LAN HTTP server and pushes live player state to phones."""

    statusChanged = pyqtSignal()

    def __init__(self, player: "AudioPlayer"):
        super().__init__(player)
        self._player = player
        self.enabled = True
        self.port = DEFAULT_REMOTE_PORT
        self._bridge = RemoteBridge(player)
        self._server = RemoteHttpServer(self._bridge)
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._on_tick)

    @property
    def is_running(self) -> bool:
        return self._server.is_running

    @property
    def listen_port(self) -> int | None:
        return self._server.port

    def urls(self) -> list[str]:
        port = self._server.port or self.port
        return urls_for_port(port)

    def primary_url(self) -> str | None:
        urls = self.urls()
        return urls[0] if urls else None

    def start(self) -> bool:
        if self._server.is_running:
            return True
        actual = self._server.start(self.port)
        if actual is None:
            self.statusChanged.emit()
            return False
        self.port = actual
        self._bridge.refresh()
        self._tick.start()
        url = self.primary_url()
        if url:
            print(f"Remote control: {url}")
        self.statusChanged.emit()
        return True

    def stop(self) -> None:
        self._tick.stop()
        self._server.stop()
        self.statusChanged.emit()

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = bool(enabled)
        ok = True
        if self.enabled:
            ok = self.start()
            if not ok:
                self.enabled = False
        else:
            self.stop()
        self.statusChanged.emit()
        return ok

    def shutdown(self) -> None:
        self._tick.stop()
        self._server.stop()

    def _on_tick(self) -> None:
        self._bridge.refresh()
