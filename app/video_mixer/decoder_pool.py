"""Warm pool of long-lived video decode processes (avoid spawn-on-Take latency)."""

from __future__ import annotations

import atexit
import multiprocessing as mp
import threading
import time
from multiprocessing import shared_memory

from app.video_mixer.decoder_worker import CMD_CLOSE, CMD_OPEN, CMD_STOP, worker_main

_POOL_SIZE = 4
_MAX_W = 1920
_MAX_H = 1080

_lock = threading.Lock()
_slots: list["DecoderSlot"] = []
_free: list["DecoderSlot"] = []
_started = False
_start_method_ready = False


def _ensure_mp_start_method() -> None:
    global _start_method_ready
    if _start_method_ready:
        return
    try:
        mp.set_start_method("spawn", force=False)
    except RuntimeError:
        pass
    _start_method_ready = True


class DecoderSlot:
    """One reusable child process + SharedMemory double-buffer."""

    def __init__(self, index: int):
        _ensure_mp_start_method()
        ctx = mp.get_context("spawn")
        plane = _MAX_W * _MAX_H * 4
        self.index = index
        self.max_w = _MAX_W
        self.max_h = _MAX_H
        self.shm = shared_memory.SharedMemory(create=True, size=plane * 2)
        self.cmd_queue = ctx.Queue(maxsize=64)
        self.playing_v = ctx.Value("i", 0)
        self.loop_v = ctx.Value("i", 1)
        self.active_v = ctx.Value("i", 0)
        self.width_v = ctx.Value("i", 0)
        self.height_v = ctx.Value("i", 0)
        self.position_v = ctx.Value("i", 0)
        self.generation_v = ctx.Value("I", 0)
        self.at_eof_v = ctx.Value("i", 0)
        self.loop_restarted_v = ctx.Value("i", 0)
        self.duration_v = ctx.Value("i", 0)
        self.fps_v = ctx.Value("i", 30000)  # milli-fps
        self.busy_v = ctx.Value("i", 0)
        self.alive_v = ctx.Value("i", 0)
        self.error_buf = ctx.Array("B", 256)
        self.process = ctx.Process(
            target=worker_main,
            name=f"VideoDecodeSlot-{index}",
            args=(
                self.shm.name,
                self.max_w,
                self.max_h,
                self.cmd_queue,
                self.playing_v,
                self.loop_v,
                self.active_v,
                self.width_v,
                self.height_v,
                self.position_v,
                self.generation_v,
                self.at_eof_v,
                self.loop_restarted_v,
                self.duration_v,
                self.fps_v,
                self.busy_v,
                self.alive_v,
                self.error_buf,
            ),
            daemon=True,
        )
        self.process.start()

    def open(
        self,
        path: str,
        *,
        playing: bool,
        loop: bool,
        seek_ms: int = 0,
    ) -> None:
        self.generation_v.value = 0
        self.at_eof_v.value = 0
        self.loop_restarted_v.value = 0
        self.duration_v.value = 0
        self.playing_v.value = 1 if playing else 0
        self.loop_v.value = 1 if loop else 0
        self.position_v.value = max(0, int(seek_ms))
        with self.error_buf.get_lock():
            for i in range(len(self.error_buf)):
                self.error_buf[i] = 0
        self.cmd_queue.put(
            (CMD_OPEN, path, bool(playing), bool(loop), max(0, int(seek_ms)))
        )

    def close_session(self) -> None:
        try:
            self.cmd_queue.put_nowait((CMD_CLOSE,))
        except Exception:
            pass
        self.generation_v.value = 0
        self.busy_v.value = 0

    def shutdown(self) -> None:
        try:
            self.cmd_queue.put_nowait((CMD_STOP,))
        except Exception:
            pass
        if self.process.is_alive():
            self.process.join(timeout=0.8)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=0.4)
        try:
            self.shm.close()
        except Exception:
            pass
        try:
            self.shm.unlink()
        except Exception:
            pass


def ensure_pool(size: int = _POOL_SIZE) -> None:
    """Spawn warm workers once (call at mixer init). Blocks briefly until ready."""
    global _started
    with _lock:
        if _started:
            return
        _ensure_mp_start_method()
        for i in range(max(1, size)):
            slot = DecoderSlot(i)
            _slots.append(slot)
            _free.append(slot)
        _started = True
        atexit.register(shutdown_pool)
        slots = list(_slots)
    # Wait outside lock so workers can finish spawn + import av.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all(int(s.alive_v.value) == 1 for s in slots):
            break
        time.sleep(0.01)


def acquire_slot() -> DecoderSlot:
    ensure_pool()
    with _lock:
        if _free:
            return _free.pop()
        # Overflow: create an extra warm slot (kept for later reuse).
        slot = DecoderSlot(len(_slots))
        _slots.append(slot)
        return slot


def release_slot(slot: DecoderSlot) -> None:
    slot.close_session()
    with _lock:
        if slot not in _free:
            _free.append(slot)


def shutdown_pool() -> None:
    global _started
    with _lock:
        slots = list(_slots)
        _slots.clear()
        _free.clear()
        _started = False
    for slot in slots:
        try:
            slot.shutdown()
        except Exception:
            pass
