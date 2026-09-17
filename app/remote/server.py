from __future__ import annotations

import json
import mimetypes
import posixpath
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from app.remote.bridge import RemoteBridge

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


def web_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "remote" / "web"
    return Path(__file__).resolve().parent / "web"


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: list[queue.Queue[str | None]] = []

    def subscribe(self) -> queue.Queue[str | None]:
        client: queue.Queue[str | None] = queue.Queue(maxsize=4)
        with self._lock:
            self._clients.append(client)
        return client

    def unsubscribe(self, client: queue.Queue[str | None]) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def publish(self, payload: str) -> None:
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(payload)
            except queue.Full:
                try:
                    client.get_nowait()
                except queue.Empty:
                    pass
                try:
                    client.put_nowait(payload)
                except queue.Full:
                    pass

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.put_nowait(None)
            except queue.Full:
                pass


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, handler, *, bridge: "RemoteBridge", static_dir: Path, bus: EventBus):
        super().__init__(addr, handler)
        self.bridge = bridge
        self.static_dir = static_dir
        self.bus = bus


class RemoteHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    server: _ReusableServer

    def log_message(self, format: str, *args) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        if path == "/api/state":
            self._send_json_text(self.server.bridge.cached_json())
            return
        if path == "/api/events":
            self._stream_events()
            return
        if path in ("/", "/index.html"):
            self._send_file("index.html")
            return
        rel = path.lstrip("/")
        if rel:
            self._send_file(rel)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/command":
            self.send_error(404)
            return
        length_raw = self.headers.get("Content-Length", "0")
        try:
            length = int(length_raw or 0)
        except ValueError:
            length = 0
        if length < 0 or length > 64_000:
            self.send_error(413)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"ok": False, "error": "Expected object"}, status=400)
            return
        action = str(payload.get("action") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if not action:
            self._send_json({"ok": False, "error": "Missing action"}, status=400)
            return
        result = self.server.bridge.run_command(action, params)
        self._send_json(result)

    def _stream_events(self) -> None:
        bus = self.server.bus
        client = bus.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors()
            self.end_headers()
            snapshot = self.server.bridge.cached_json()
            self._write_sse(snapshot)
            while True:
                try:
                    payload = client.get(timeout=20.0)
                except queue.Empty:
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except OSError:
                        break
                    continue
                if payload is None:
                    break
                if not self._write_sse(payload):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(client)

    def _write_sse(self, payload: str) -> bool:
        try:
            body = f"data: {payload}\n\n".encode("utf-8")
            self.wfile.write(body)
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _send_file(self, rel: str) -> None:
        root = self.server.static_dir.resolve()
        safe = posixpath.normpath(rel).lstrip("/")
        path = (root / safe).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            self.send_error(404)
            return
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        suffix = path.suffix.lower()
        mime = _MIME.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        self._send_json_text(json.dumps(payload, ensure_ascii=False), status=status)

    def _send_json_text(self, text: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")


class RemoteHttpServer:
    def __init__(self, bridge: "RemoteBridge"):
        self._bridge = bridge
        self._httpd: _ReusableServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None
        self.bus = EventBus()

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    def start(self, port: int) -> int | None:
        if self._httpd is not None:
            return self.port
        static = web_dir()
        last_error: OSError | None = None
        for candidate in _port_candidates(port):
            try:
                httpd = _ReusableServer(
                    ("0.0.0.0", candidate),
                    RemoteHandler,
                    bridge=self._bridge,
                    static_dir=static,
                    bus=self.bus,
                )
            except OSError as exc:
                last_error = exc
                continue
            self._httpd = httpd
            self.port = candidate
            self._thread = threading.Thread(
                target=httpd.serve_forever,
                name="resonance-remote",
                daemon=True,
            )
            self._thread.start()
            self._bridge.statePublished.connect(self.bus.publish)
            return candidate
        if last_error is not None:
            print(f"Remote control server failed to bind: {last_error}")
        return None

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        try:
            self._bridge.statePublished.disconnect(self.bus.publish)
        except TypeError:
            pass
        self.bus.close()
        httpd.shutdown()
        httpd.server_close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None
        self.port = None
        self.bus = EventBus()

    def broadcast(self, payload: str) -> None:
        self.bus.publish(payload)


def _port_candidates(preferred: int) -> list[int]:
    port = int(preferred)
    if port < 1024 or port > 65535:
        port = 8765
    candidates = [port]
    for extra in range(1, 11):
        nxt = port + extra
        if nxt <= 65535:
            candidates.append(nxt)
    return candidates
