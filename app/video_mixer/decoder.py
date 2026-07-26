"""Process-isolated video decoder facade (reuses warm pool workers)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.video_mixer.decoder_pool import DecoderSlot, acquire_slot, ensure_pool, release_slot
from app.video_mixer.decoder_worker import CMD_SEEK, CMD_SET_LOOP, CMD_SET_PLAYING


class VideoDecoder:
    """Decode video frames via a pooled child process (no spawn-on-start)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._duration_ms = 0
        self._fps = 30.0
        self._width = 0
        self._height = 0
        self._error: str | None = None
        self._playing = True
        self._loop = True
        self._position_ms = 0
        self._at_eof = False
        self._frame: np.ndarray | None = None
        self._frame_generation = -1
        self._pending_seek_ms = 0
        self._slot: DecoderSlot | None = None
        # No av.open on the UI/air process — metadata comes from the worker on OPEN.

    @property
    def error(self) -> str | None:
        if self._error:
            return self._error
        slot = self._slot
        if slot is None:
            return None
        with slot.error_buf.get_lock():
            raw = bytes(bytearray(slot.error_buf[:]))
        msg = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return msg or None

    @property
    def duration_ms(self) -> int:
        slot = self._slot
        if slot is not None and slot.duration_v.value > 0:
            self._duration_ms = int(slot.duration_v.value)
        return self._duration_ms

    @property
    def fps(self) -> float:
        slot = self._slot
        if slot is not None and slot.fps_v.value > 0:
            self._fps = float(slot.fps_v.value) / 1000.0
        return self._fps

    @property
    def size(self) -> tuple[int, int]:
        return self._width, self._height

    @property
    def at_eof(self) -> bool:
        slot = self._slot
        if slot is not None:
            return bool(slot.at_eof_v.value)
        return self._at_eof

    @property
    def is_playing(self) -> bool:
        slot = self._slot
        if slot is not None:
            return bool(slot.playing_v.value) and not self.at_eof
        return self._playing and not self._at_eof

    def start(self) -> None:
        if self._slot is not None:
            return
        ensure_pool()
        slot = acquire_slot()
        self._slot = slot
        self._frame = None
        self._frame_generation = -1
        slot.open(
            self.path,
            playing=self._playing,
            loop=self._loop,
            seek_ms=self._pending_seek_ms or self._position_ms,
        )

    def stop(self) -> None:
        slot = self._slot
        self._slot = None
        self._frame = None
        self._frame_generation = -1
        if slot is not None:
            release_slot(slot)

    def set_playing(self, playing: bool) -> None:
        playing = bool(playing)
        slot = self._slot
        if (
            self._playing == playing
            and slot is not None
            and bool(slot.playing_v.value) == playing
        ):
            return
        self._playing = playing
        if slot is None:
            return
        slot.playing_v.value = 1 if playing else 0
        if playing:
            slot.at_eof_v.value = 0
        try:
            slot.cmd_queue.put_nowait((CMD_SET_PLAYING, playing))
        except Exception:
            pass

    def set_loop(self, loop: bool) -> None:
        loop = bool(loop)
        slot = self._slot
        if (
            self._loop == loop
            and slot is not None
            and bool(slot.loop_v.value) == loop
        ):
            return
        self._loop = loop
        if slot is None:
            return
        slot.loop_v.value = 1 if loop else 0
        try:
            slot.cmd_queue.put_nowait((CMD_SET_LOOP, loop))
        except Exception:
            pass

    def seek(self, position_ms: int) -> None:
        pos = max(0, int(position_ms))
        self._position_ms = pos
        self._pending_seek_ms = pos
        self._at_eof = False
        slot = self._slot
        if slot is None:
            return
        slot.position_v.value = pos
        slot.at_eof_v.value = 0
        try:
            slot.cmd_queue.put_nowait((CMD_SEEK, pos))
        except Exception:
            pass

    def consume_loop_restart(self) -> bool:
        slot = self._slot
        if slot is None:
            return False
        with slot.loop_restarted_v.get_lock():
            flag = bool(slot.loop_restarted_v.value)
            slot.loop_restarted_v.value = 0
        return flag

    def get_frame(self) -> tuple[np.ndarray | None, int]:
        slot = self._slot
        if slot is None:
            return self._frame, self._position_ms

        generation = int(slot.generation_v.value)
        if generation == 0:
            return self._frame, int(slot.position_v.value)

        if generation != self._frame_generation:
            active = int(slot.active_v.value)
            w = int(slot.width_v.value)
            h = int(slot.height_v.value)
            pos = int(slot.position_v.value)
            if w > 0 and h > 0:
                plane = slot.max_w * slot.max_h * 4
                offset = active * plane
                src = np.ndarray(
                    (slot.max_h, slot.max_w, 4),
                    dtype=np.uint8,
                    buffer=slot.shm.buf,
                    offset=offset,
                )
                self._frame = np.ascontiguousarray(src[:h, :w, :])
                self._width = w
                self._height = h
                self._position_ms = pos
                self._frame_generation = generation
            else:
                self._position_ms = pos
        else:
            self._position_ms = int(slot.position_v.value)

        return self._frame, self._position_ms

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
