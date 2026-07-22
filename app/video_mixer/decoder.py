from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

try:
    import av
except ImportError:  # pragma: no cover
    av = None


class VideoDecoder:
    """Decode video frames via PyAV on a background thread."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._width = 0
        self._height = 0
        self._duration_ms = 0
        self._position_ms = 0
        self._fps = 30.0
        self._playing = True
        self._loop = True
        self._seek_ms: int | None = None
        self._loop_restarted = False
        self._at_eof = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

        if av is None:
            self._error = "PyAV (av) is not installed"
            return

        try:
            with av.open(self.path) as container:
                stream = container.streams.video[0]
                if stream.duration is not None and stream.time_base is not None:
                    self._duration_ms = int(float(stream.duration * stream.time_base) * 1000)
                elif container.duration is not None:
                    self._duration_ms = int(container.duration / 1000)
                rate = stream.average_rate or stream.base_rate
                if rate is not None and float(rate) > 0:
                    self._fps = float(rate)
                self._width = int(stream.width or 0)
                self._height = int(stream.height or 0)
        except Exception as exc:
            self._error = str(exc)

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def size(self) -> tuple[int, int]:
        return self._width, self._height

    @property
    def at_eof(self) -> bool:
        return self._at_eof

    @property
    def is_playing(self) -> bool:
        return bool(self._playing) and not self._at_eof

    def start(self) -> None:
        if self._error or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"VideoDecoder:{Path(self.path).name}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if playing:
            self._at_eof = False

    def set_loop(self, loop: bool) -> None:
        self._loop = loop

    def seek(self, position_ms: int) -> None:
        with self._lock:
            self._seek_ms = max(0, int(position_ms))
            self._position_ms = max(0, int(position_ms))
            self._at_eof = False

    def consume_loop_restart(self) -> bool:
        """True once after a seamless loop wrap (EOF → start)."""
        with self._lock:
            flag = self._loop_restarted
            self._loop_restarted = False
            return flag

    def get_frame(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._frame, self._position_ms

    def _run(self) -> None:
        assert av is not None
        try:
            container = av.open(self.path)
        except Exception as exc:
            self._error = str(exc)
            return

        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            frame_interval = 1.0 / max(1.0, self._fps)
            next_time = time.perf_counter()

            while not self._stop.is_set():
                seek_ms = None
                with self._lock:
                    if self._seek_ms is not None:
                        seek_ms = self._seek_ms
                        self._seek_ms = None

                if seek_ms is not None:
                    self._do_seek(container, stream, seek_ms)
                    next_time = time.perf_counter()

                if not self._playing:
                    time.sleep(0.02)
                    continue

                try:
                    frame = next(container.decode(video=0))
                except (StopIteration, av.EOFError, Exception):
                    if self._loop:
                        with self._lock:
                            self._loop_restarted = True
                            self._at_eof = False
                        if not self._restart_from_start(container, stream):
                            try:
                                container.close()
                            except Exception:
                                pass
                            try:
                                container = av.open(self.path)
                                stream = container.streams.video[0]
                                stream.thread_type = "AUTO"
                                self._do_seek(container, stream, 0)
                            except Exception as exc:
                                self._error = str(exc)
                                return
                        next_time = time.perf_counter()
                        continue
                    with self._lock:
                        self._playing = False
                        self._at_eof = True
                    time.sleep(0.02)
                    continue

                array = frame.to_ndarray(format="rgba")
                pts_ms = 0
                if frame.pts is not None and stream.time_base is not None:
                    pts_ms = int(float(frame.pts * stream.time_base) * 1000)

                with self._lock:
                    self._frame = array
                    self._width = array.shape[1]
                    self._height = array.shape[0]
                    self._position_ms = pts_ms
                    self._at_eof = False

                next_time += frame_interval
                delay = next_time - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_time = time.perf_counter()
        finally:
            try:
                container.close()
            except Exception:
                pass

    def _restart_from_start(self, container, stream) -> bool:
        return self._do_seek(container, stream, 0)

    def _do_seek(self, container, stream, seek_ms: int) -> bool:
        """Seek by stream time_base; flush and pull one keyframe neighborhood."""
        try:
            ts = int((seek_ms / 1000.0) / float(stream.time_base))
            container.seek(ts, stream=stream, any_frame=False, backward=True)
        except Exception:
            try:
                container.seek(int(seek_ms * 1000))  # microseconds fallback
            except Exception:
                return False
        try:
            for _ in range(8):
                packet = next(container.demux(stream))
                if packet.dts is None and packet.pts is None:
                    continue
                for frame in packet.decode():
                    array = frame.to_ndarray(format="rgba")
                    pts_ms = seek_ms
                    if frame.pts is not None and stream.time_base is not None:
                        pts_ms = int(float(frame.pts * stream.time_base) * 1000)
                    with self._lock:
                        self._frame = array
                        self._width = array.shape[1]
                        self._height = array.shape[0]
                        self._position_ms = pts_ms
                        self._at_eof = False
                    return True
        except Exception:
            return False
        return False
