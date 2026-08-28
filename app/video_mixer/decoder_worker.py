"""Child-process PyAV video decode worker (own GIL, isolated from air PCM).

Long-lived process: waits for OPEN, decodes until CLOSE, then idles for reuse.
"""

from __future__ import annotations

import queue
import time
from multiprocessing import shared_memory
from typing import Any

import numpy as np

# Command opcodes on the IPC queue
CMD_OPEN = "open"  # (CMD_OPEN, path, playing, loop, seek_ms)
CMD_CLOSE = "close"
CMD_STOP = "stop"  # shut down process
CMD_SEEK = "seek"
CMD_SET_PLAYING = "set_playing"
CMD_SET_LOOP = "set_loop"


def _open_container(path: str):
    """Open media; QuickTime files often have MacRoman/Cyrillic metadata, not UTF-8."""
    import av

    return av.open(path, metadata_errors="replace")


def _write_error(error_buf: Any, message: str) -> None:
    raw = (message or "")[:255].encode("utf-8", errors="replace")
    with error_buf.get_lock():
        n = len(error_buf)
        for i in range(n):
            error_buf[i] = 0
        for i, b in enumerate(raw):
            error_buf[i] = b


def _clear_error(error_buf: Any) -> None:
    with error_buf.get_lock():
        for i in range(len(error_buf)):
            error_buf[i] = 0


def _publish_frame(
    shm: shared_memory.SharedMemory,
    *,
    max_w: int,
    max_h: int,
    array: np.ndarray,
    position_ms: int,
    width_v: Any,
    height_v: Any,
    position_v: Any,
    active_v: Any,
    generation_v: Any,
    at_eof_v: Any,
) -> None:
    h, w = int(array.shape[0]), int(array.shape[1])
    if w > max_w or h > max_h:
        scale = min(max_w / max(w, 1), max_h / max(h, 1))
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        ys = (np.linspace(0, h - 1, new_h)).astype(np.int32)
        xs = (np.linspace(0, w - 1, new_w)).astype(np.int32)
        array = array[ys][:, xs]
        h, w = new_h, new_w

    plane = max_w * max_h * 4
    write_idx = 1 - int(active_v.value)
    offset = write_idx * plane
    dest = np.ndarray((max_h, max_w, 4), dtype=np.uint8, buffer=shm.buf, offset=offset)
    dest[:h, :w, :] = array
    if w < max_w:
        dest[:h, w:, :] = 0
    if h < max_h:
        dest[h:, :, :] = 0

    with generation_v.get_lock():
        width_v.value = w
        height_v.value = h
        position_v.value = int(position_ms)
        at_eof_v.value = 0
        generation_v.value = int(generation_v.value) + 1
        active_v.value = write_idx


def _do_seek(container, stream, seek_ms: int):
    try:
        ts = int((seek_ms / 1000.0) / float(stream.time_base))
        container.seek(ts, stream=stream, any_frame=False, backward=True)
    except Exception:
        try:
            container.seek(int(seek_ms * 1000))
        except Exception:
            return None, None
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
                return array, pts_ms
    except Exception:
        return None, None
    return None, None


def _drain_cmds(cmd_queue, playing_v, loop_v, at_eof_v):
    """Non-blocking drain. Returns (seek_ms|None, close, stop)."""
    seek_ms = None
    close = False
    stop = False
    while True:
        try:
            cmd = cmd_queue.get_nowait()
        except queue.Empty:
            break
        if not cmd:
            continue
        op = cmd[0]
        if op == CMD_STOP:
            stop = True
            break
        if op == CMD_CLOSE:
            close = True
            break
        if op == CMD_SEEK:
            seek_ms = max(0, int(cmd[1]))
        elif op == CMD_SET_PLAYING:
            playing_v.value = 1 if cmd[1] else 0
            if cmd[1]:
                at_eof_v.value = 0
        elif op == CMD_SET_LOOP:
            loop_v.value = 1 if cmd[1] else 0
        elif op == CMD_OPEN:
            # Stale open while busy — treat as close+reopen signal via close
            close = True
            # Re-queue open for idle handler
            try:
                cmd_queue.put_nowait(cmd)
            except Exception:
                pass
            break
    return seek_ms, close, stop


def _run_session(
    *,
    path: str,
    shm,
    max_w: int,
    max_h: int,
    cmd_queue,
    playing_v,
    loop_v,
    active_v,
    width_v,
    height_v,
    position_v,
    generation_v,
    at_eof_v,
    loop_restarted_v,
    duration_v,
    fps_v,
    error_buf,
    start_seek_ms: int,
) -> str:
    """Decode until CLOSE/STOP. Returns 'close' or 'stop'."""
    import av

    _clear_error(error_buf)
    generation_v.value = 0
    at_eof_v.value = 0
    loop_restarted_v.value = 0

    container = _open_container(path)
    try:
        if not container.streams.video:
            _write_error(error_buf, "No video stream")
            return "close"
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"

        duration_ms = 0
        if stream.duration is not None and stream.time_base is not None:
            duration_ms = int(float(stream.duration * stream.time_base) * 1000)
        elif container.duration is not None:
            duration_ms = int(container.duration / 1000)
        duration_v.value = max(0, duration_ms)

        fps = 30.0
        rate = stream.average_rate or stream.base_rate
        if rate is not None and float(rate) > 0:
            fps = float(rate)
        fps_v.value = int(fps * 1000)  # milli-fps for int Value

        frame_interval = 1.0 / max(1.0, fps)
        next_time = time.perf_counter()

        # First frame ASAP (seek if requested)
        if start_seek_ms > 0:
            array, pts_ms = _do_seek(container, stream, start_seek_ms)
        else:
            try:
                frame = next(container.decode(video=0))
                array = frame.to_ndarray(format="rgba")
                pts_ms = 0
                if frame.pts is not None and stream.time_base is not None:
                    pts_ms = int(float(frame.pts * stream.time_base) * 1000)
            except Exception:
                array, pts_ms = None, 0
        if array is not None:
            _publish_frame(
                shm,
                max_w=max_w,
                max_h=max_h,
                array=array,
                position_ms=int(pts_ms or start_seek_ms),
                width_v=width_v,
                height_v=height_v,
                position_v=position_v,
                active_v=active_v,
                generation_v=generation_v,
                at_eof_v=at_eof_v,
            )

        while True:
            seek_ms, close, stop = _drain_cmds(cmd_queue, playing_v, loop_v, at_eof_v)
            if stop:
                return "stop"
            if close:
                return "close"

            if seek_ms is not None:
                array, pts_ms = _do_seek(container, stream, seek_ms)
                if array is not None:
                    _publish_frame(
                        shm,
                        max_w=max_w,
                        max_h=max_h,
                        array=array,
                        position_ms=int(pts_ms),
                        width_v=width_v,
                        height_v=height_v,
                        position_v=position_v,
                        active_v=active_v,
                        generation_v=generation_v,
                        at_eof_v=at_eof_v,
                    )
                next_time = time.perf_counter()
                continue

            if not playing_v.value:
                time.sleep(0.02)
                continue

            try:
                frame = next(container.decode(video=0))
            except Exception as exc:
                is_eof = isinstance(exc, (StopIteration, av.EOFError))
                if not is_eof:
                    msg = str(exc).lower()
                    # Some demuxers surface EOF as a generic AV error string.
                    is_eof = "end of file" in msg or "averror_eof" in msg
                if not is_eof:
                    # Transient decode glitch — don't treat as EOF/loop.
                    time.sleep(0.005)
                    continue
                if loop_v.value:
                    loop_restarted_v.value = 1
                    at_eof_v.value = 0
                    # Reopen is more reliable than seek-after-EOF for many codecs.
                    try:
                        container.close()
                    except Exception:
                        pass
                    array, pts_ms = None, 0
                    try:
                        container = _open_container(path)
                        stream = container.streams.video[0]
                        stream.thread_type = "AUTO"
                        array, pts_ms = _do_seek(container, stream, 0)
                        if array is None:
                            # Fallback: decode from start without seek
                            try:
                                fr = next(container.decode(video=0))
                                array = fr.to_ndarray(format="rgba")
                                pts_ms = 0
                                if fr.pts is not None and stream.time_base is not None:
                                    pts_ms = int(float(fr.pts * stream.time_base) * 1000)
                            except Exception:
                                array, pts_ms = None, 0
                    except Exception as reopen_exc:
                        _write_error(error_buf, str(reopen_exc))
                        return "close"
                    if array is not None:
                        _publish_frame(
                            shm,
                            max_w=max_w,
                            max_h=max_h,
                            array=array,
                            position_ms=int(pts_ms or 0),
                            width_v=width_v,
                            height_v=height_v,
                            position_v=position_v,
                            active_v=active_v,
                            generation_v=generation_v,
                            at_eof_v=at_eof_v,
                        )
                    else:
                        # Host can recover via seek if reopen produced no frame.
                        at_eof_v.value = 1
                    next_time = time.perf_counter()
                    continue
                playing_v.value = 0
                at_eof_v.value = 1
                time.sleep(0.02)
                continue

            array = frame.to_ndarray(format="rgba")
            pts_ms = 0
            if frame.pts is not None and stream.time_base is not None:
                pts_ms = int(float(frame.pts * stream.time_base) * 1000)
            _publish_frame(
                shm,
                max_w=max_w,
                max_h=max_h,
                array=array,
                position_ms=pts_ms,
                width_v=width_v,
                height_v=height_v,
                position_v=position_v,
                active_v=active_v,
                generation_v=generation_v,
                at_eof_v=at_eof_v,
            )

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


def worker_main(
    shm_name: str,
    max_w: int,
    max_h: int,
    cmd_queue,
    playing_v,
    loop_v,
    active_v,
    width_v,
    height_v,
    position_v,
    generation_v,
    at_eof_v,
    loop_restarted_v,
    duration_v,
    fps_v,
    busy_v,
    alive_v,
    error_buf,
) -> None:
    """Long-lived entry: idle → OPEN session → CLOSE → idle … until STOP."""
    try:
        import av  # noqa: F401 — fail fast
    except ImportError:
        _write_error(error_buf, "PyAV (av) is not installed")
        alive_v.value = 0
        return

    try:
        shm = shared_memory.SharedMemory(name=shm_name)
    except Exception as exc:
        _write_error(error_buf, f"SharedMemory: {exc}")
        alive_v.value = 0
        return

    alive_v.value = 1
    busy_v.value = 0
    try:
        while True:
            try:
                cmd = cmd_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if not cmd:
                continue
            op = cmd[0]
            if op == CMD_STOP:
                break
            if op != CMD_OPEN:
                continue

            path = str(cmd[1])
            playing_v.value = 1 if cmd[2] else 0
            loop_v.value = 1 if cmd[3] else 0
            start_seek = max(0, int(cmd[4]) if len(cmd) > 4 else 0)
            busy_v.value = 1
            try:
                result = _run_session(
                    path=path,
                    shm=shm,
                    max_w=max_w,
                    max_h=max_h,
                    cmd_queue=cmd_queue,
                    playing_v=playing_v,
                    loop_v=loop_v,
                    active_v=active_v,
                    width_v=width_v,
                    height_v=height_v,
                    position_v=position_v,
                    generation_v=generation_v,
                    at_eof_v=at_eof_v,
                    loop_restarted_v=loop_restarted_v,
                    duration_v=duration_v,
                    fps_v=fps_v,
                    error_buf=error_buf,
                    start_seek_ms=start_seek,
                )
            except Exception as exc:
                _write_error(error_buf, str(exc))
                result = "close"
            busy_v.value = 0
            generation_v.value = 0
            if result == "stop":
                break
    finally:
        alive_v.value = 0
        busy_v.value = 0
        try:
            shm.close()
        except Exception:
            pass
