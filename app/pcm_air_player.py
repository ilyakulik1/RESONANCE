"""Air PCM player with realtime EQ (QMediaPlayer-compatible subset).

EQ is applied at sink-read time (not decode), so coefficient changes are
audible within one audio buffer (~40–80 ms).
"""

from __future__ import annotations

import os
import queue
import threading
import time

import numpy as np
from PyQt6.QtCore import QElapsedTimer, QIODevice, QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import (
    QAudioDevice,
    QAudioFormat,
    QAudioSink,
    QMediaDevices,
    QMediaPlayer,
)

from app.eq_curve import log_freq_axis
from app.eq_dsp import EqProcessor, HighCutSweep
from app.eq_state import EqState

_CHUNK_FRAMES = 2048
_QUEUE_MAX_CHUNKS = 64  # deep buffer so video-start GIL spikes don't underrun air
_TARGET_CHANNELS = 2
_FALLBACK_RATE = 48000
_DECODE_PACE_DEPTH = 36  # keep ~1.5s ahead at 48k
_DECODE_PACE_SLEEP_S = 0.004
_SPECTRUM_PCM_SAMPLES = 2048
_SPECTRUM_CAPTURE_EVERY = 10
_SPECTRUM_BINS = 128
_PREBUFFER_CHUNKS = 3
_PREBUFFER_TIMEOUT_S = 0.08
_PUT_TIMEOUT_S = 0.05
_LOW_WATER_CHUNKS = 8  # below this: never sleep / skip spectrum


def _frame_to_stereo_interleaved(arr: np.ndarray) -> np.ndarray:
    """Convert PyAV ndarray to float32 interleaved stereo.

    Handles planar (channels, samples), packed (samples, channels), and
    packed row (1, channels * samples) layouts.
    """
    a = np.asarray(arr, dtype=np.float32)
    if a.size == 0:
        return np.zeros(0, dtype=np.float32)

    if a.ndim == 1:
        if a.size % 2 == 0:
            return a.reshape(-1)
        return np.column_stack([a, a]).reshape(-1)

    if a.ndim != 2:
        return np.zeros(0, dtype=np.float32)

    rows, cols = a.shape

    if rows == 1:
        flat = a.reshape(-1)
        if flat.size % _TARGET_CHANNELS == 0:
            return flat
        return np.column_stack([flat, flat]).reshape(-1)

    if rows <= 8 and rows < cols:
        chans = [a[i] for i in range(min(rows, _TARGET_CHANNELS))]
        if len(chans) == 1:
            chans.append(chans[0])
        while len(chans) < 2:
            chans.append(chans[-1])
        return np.column_stack(chans[:2]).reshape(-1)

    if cols == 1:
        mono = a[:, 0]
        return np.column_stack([mono, mono]).reshape(-1)
    return a[:, :2].reshape(-1)


class AirAudioOutput(QObject):
    """Volume / mute / device facade matching QAudioOutput usage in player.py."""

    def __init__(self, player: "PcmAirPlayer", parent=None):
        super().__init__(parent)
        self._player = player
        self._volume = 1.0
        self._muted = False
        self._device: QAudioDevice = QMediaDevices.defaultAudioOutput()

    def setVolume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        self._player._on_output_params_changed()

    def volume(self) -> float:
        return self._volume

    def setMuted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._player._on_output_params_changed()

    def isMuted(self) -> bool:
        return self._muted

    def setDevice(self, device: QAudioDevice) -> None:
        self._device = device
        self._player._on_device_changed(device)

    def device(self) -> QAudioDevice:
        return self._device

    def effective_volume(self) -> float:
        return 0.0 if self._muted else self._volume


class _PcmBuffer(QIODevice):
    """Pull device: drains float chunks → EQ → Int16 bytes for QAudioSink."""

    def __init__(self, player: "PcmAirPlayer"):
        super().__init__()
        self._player = player
        self.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered)

    def readData(self, maxlen: int) -> bytes:  # noqa: N802
        return self._player._read_audio_bytes(maxlen)

    def bytesAvailable(self) -> int:  # noqa: N802
        return max(0, self._player._bytes_available()) + super().bytesAvailable()

    def isSequential(self) -> bool:  # noqa: N802
        return True


class PcmAirPlayer(QObject):
    """Duck-typed stand-in for QMediaPlayer used on the air path."""

    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(object)
    mediaStatusChanged = pyqtSignal(object)

    PlaybackState = QMediaPlayer.PlaybackState
    MediaStatus = QMediaPlayer.MediaStatus

    def __init__(self, parent=None):
        super().__init__(parent)
        self._output = AirAudioOutput(self, self)
        self._source = QUrl()
        self._path: str | None = None
        self._duration_ms = 0
        self._position_ms = 0
        self._playback_state = QMediaPlayer.PlaybackState.StoppedState
        self._media_status = QMediaPlayer.MediaStatus.NoMedia

        self._eq = EqProcessor(channels=_TARGET_CHANNELS, sample_rate=44100.0)
        self._eq_state = EqState()
        self._track_gain = 1.0
        self._high_cut = HighCutSweep(channels=_TARGET_CHANNELS, sample_rate=44100.0)
        self._high_cut_elapsed = QElapsedTimer()
        self._high_cut_duration_ms = 0
        self._high_cut_on_complete = None
        self._high_cut_ui_active = False

        self._sample_rate = 44100
        self._sink: QAudioSink | None = None
        self._device = QMediaDevices.defaultAudioOutput()
        self._io = _PcmBuffer(self)

        self._chunk_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        self._float_pending = np.zeros(0, dtype=np.float32)
        self._byte_pending = bytearray()
        self._frames_played = 0
        self._frames_decoded = 0
        self._seek_frame = 0
        self._generation = 0
        self._decode_eof_generation: int | None = None

        self._spectrum_enabled = False
        self._spectrum_pcm = np.zeros(_SPECTRUM_PCM_SAMPLES, dtype=np.float32)
        self._spectrum_freqs = log_freq_axis(_SPECTRUM_BINS).astype(np.float32)
        self._spectrum_db = np.full(_SPECTRUM_BINS, -90.0, dtype=np.float32)
        self._spectrum_peak = -20.0
        self._spectrum_lock = threading.Lock()
        self._chunks_since_spectrum = 0
        self._hann = np.hanning(_SPECTRUM_PCM_SAMPLES).astype(np.float32)

        self._decode_stop = threading.Event()
        self._decode_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(50)
        self._pos_timer.timeout.connect(self._emit_position)
        self._last_restart_at = 0.0
        self._seek_coalesce_timer = QTimer(self)
        self._seek_coalesce_timer.setSingleShot(True)
        self._seek_coalesce_timer.setInterval(30)
        self._seek_coalesce_timer.timeout.connect(self._flush_coalesced_seek)
        self._pending_seek_ms: int | None = None
        self._pending_seek_autoplay = False

    def set_spectrum_enabled(self, enabled: bool) -> None:
        self._spectrum_enabled = bool(enabled)
        if not self._spectrum_enabled:
            with self._spectrum_lock:
                self._spectrum_pcm.fill(0.0)
                self._spectrum_db.fill(-90.0)
            self._spectrum_peak = -20.0

    def spectrum_bins_snapshot(self) -> tuple[np.ndarray, np.ndarray] | None:
        if not self._spectrum_enabled:
            return None
        with self._spectrum_lock:
            if float(self._spectrum_db.max()) <= -89.5:
                return None
            return self._spectrum_freqs.copy(), self._spectrum_db.copy()

    def audioOutput(self) -> AirAudioOutput:
        return self._output

    def setAudioOutput(self, _output) -> None:
        return

    def source(self) -> QUrl:
        return self._source

    def setSource(self, url: QUrl) -> None:
        self.stop()
        if url is None or url.isEmpty() or not url.isValid():
            self._path = None
            self._source = QUrl()
            self._duration_ms = 0
            self._set_media_status(QMediaPlayer.MediaStatus.NoMedia)
            self.durationChanged.emit(0)
            return

        path = url.toLocalFile()
        if not path or not os.path.isfile(path):
            self._path = None
            self._source = QUrl()
            self._duration_ms = 0
            self._set_media_status(QMediaPlayer.MediaStatus.InvalidMedia)
            self.durationChanged.emit(0)
            return

        self._set_media_status(QMediaPlayer.MediaStatus.LoadingMedia)
        self._path = path
        self._source = QUrl.fromLocalFile(path)
        duration = self._probe_duration_ms(path)
        self._duration_ms = duration or 0
        self._position_ms = 0
        self._frames_played = 0
        self._seek_frame = 0
        self.durationChanged.emit(self._duration_ms)
        self._set_media_status(QMediaPlayer.MediaStatus.LoadedMedia)

    def playbackState(self):
        return self._playback_state

    def mediaStatus(self):
        return self._media_status

    def duration(self) -> int:
        return int(self._duration_ms)

    def position(self) -> int:
        return int(self._position_ms)

    def setPosition(self, position_ms: int) -> None:
        pos = max(0, int(position_ms))
        if self._duration_ms > 0:
            pos = min(pos, self._duration_ms)
        was_playing = self._playback_state == QMediaPlayer.PlaybackState.PlayingState
        self._position_ms = pos
        self.positionChanged.emit(pos)
        self._pending_seek_ms = pos
        self._pending_seek_autoplay = was_playing
        # Coalesce scrub bursts; never nest restarts on the UI thread.
        if getattr(self, "_in_restart", False) or self._seek_coalesce_timer.isActive():
            if not self._seek_coalesce_timer.isActive():
                self._seek_coalesce_timer.start()
            return
        self._flush_coalesced_seek()
        self._seek_coalesce_timer.start()

    def _flush_coalesced_seek(self) -> None:
        if self._pending_seek_ms is None:
            return
        pos = int(self._pending_seek_ms)
        autoplay = bool(self._pending_seek_autoplay)
        self._pending_seek_ms = None
        self._in_restart = True
        try:
            self._restart_pipeline(start_ms=pos, autoplay=autoplay)
            if self._pending_seek_ms is not None:
                self._seek_coalesce_timer.start(0)
        finally:
            self._in_restart = False

    def play(self) -> None:
        if not self._path:
            return
        # Promote any coalesced seek to autoplay and flush immediately.
        # Otherwise LOOP/NEXT restart (setPosition then play) can leave the
        # pipeline paused at the wrong position after the coalesce timer fires.
        if self._pending_seek_ms is not None:
            self._pending_seek_autoplay = True
            self._seek_coalesce_timer.stop()
            self._flush_coalesced_seek()
            if self._playback_state == QMediaPlayer.PlaybackState.PlayingState:
                return
        if self._playback_state == QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._playback_state == QMediaPlayer.PlaybackState.PausedState and self._sink is not None:
            try:
                self._sink.resume()
            except Exception:
                self._restart_pipeline(start_ms=self._position_ms, autoplay=True)
                return
            self._set_playback_state(QMediaPlayer.PlaybackState.PlayingState)
            self._pos_timer.start()
            return
        self._restart_pipeline(start_ms=self._position_ms, autoplay=True)

    def pause(self) -> None:
        if self._playback_state != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._sink is not None:
            self._sink.suspend()
        self._set_playback_state(QMediaPlayer.PlaybackState.PausedState)
        self._pos_timer.stop()

    def stop(self) -> None:
        self.clear_high_cut_sweep()
        self._stop_pipeline(destroy_sink=True)
        self._position_ms = 0
        self._frames_played = 0
        self._seek_frame = 0
        with self._spectrum_lock:
            self._spectrum_pcm.fill(0.0)
            self._spectrum_db.fill(-90.0)
        self._spectrum_peak = -20.0
        self._set_playback_state(QMediaPlayer.PlaybackState.StoppedState)
        self.positionChanged.emit(0)

    def set_eq_state(self, state: EqState | None) -> None:
        """Live coefficient swap — no pipeline restart."""
        self._eq_state = state or EqState()
        self._eq.set_state(self._eq_state)

    def set_track_gain_db(self, gain_db: float) -> None:
        """Per-track linear gain applied in the PCM path (after EQ)."""
        from app.loudness import clamp_gain_db, db_to_linear

        self._track_gain = db_to_linear(clamp_gain_db(gain_db))

    def start_high_cut_sweep(
        self,
        duration_ms: int,
        on_complete=None,
        *,
        q: float | None = None,
    ) -> None:
        """Sweep low-pass closed over duration_ms (with end fadeout); then on_complete."""
        self.clear_high_cut_sweep()
        duration = max(0, int(duration_ms))
        if duration <= 0:
            if on_complete:
                on_complete()
            return
        self._high_cut_duration_ms = duration
        self._high_cut_on_complete = on_complete
        self._high_cut_ui_active = True
        self._high_cut.set_sample_rate(float(self._sample_rate))
        self._high_cut.start(q=q)
        self._high_cut.set_progress(0.0)
        self._high_cut_elapsed.start()

    def clear_high_cut_sweep(self) -> None:
        self._high_cut_ui_active = False
        self._high_cut_on_complete = None
        self._high_cut_duration_ms = 0
        self._high_cut.clear()

    def is_high_cut_active(self) -> bool:
        return bool(self._high_cut_ui_active)

    def advance_high_cut_sweep(self) -> None:
        if not self._high_cut_ui_active:
            return
        elapsed = self._high_cut_elapsed.elapsed()
        duration = max(1, self._high_cut_duration_ms)
        if elapsed >= duration:
            self._high_cut.set_progress(1.0)
            self._high_cut_ui_active = False
            callback = self._high_cut_on_complete
            self._high_cut_on_complete = None
            if callback:
                callback()
            return
        self._high_cut.set_progress(elapsed / float(duration))

    def _on_output_params_changed(self) -> None:
        if self._sink is not None:
            self._sink.setVolume(self._output.effective_volume())

    def _on_device_changed(self, device: QAudioDevice) -> None:
        self._device = device
        was_playing = self._playback_state == QMediaPlayer.PlaybackState.PlayingState
        pos = self._position_ms
        if was_playing or self._playback_state == QMediaPlayer.PlaybackState.PausedState:
            self._restart_pipeline(start_ms=pos, autoplay=was_playing)

    def _set_playback_state(self, state) -> None:
        if state == self._playback_state:
            return
        self._playback_state = state
        self.playbackStateChanged.emit(state)

    def _set_media_status(self, status) -> None:
        if status == self._media_status:
            return
        self._media_status = status
        self.mediaStatusChanged.emit(status)

    def _probe_duration_ms(self, path: str) -> int | None:
        try:
            import av

            with av.open(path) as container:
                if container.duration is not None:
                    return max(0, int(container.duration / 1000))
                if container.streams.audio:
                    stream = container.streams.audio[0]
                    if stream.duration is not None and stream.time_base is not None:
                        return max(0, int(float(stream.duration * stream.time_base) * 1000))
        except Exception:
            pass
        return None

    def _preferred_format(self) -> QAudioFormat:
        device = (
            self._device
            if self._device and not self._device.isNull()
            else QMediaDevices.defaultAudioOutput()
        )
        fmt = QAudioFormat()
        preferred = device.preferredFormat()
        rate = int(preferred.sampleRate()) if preferred.sampleRate() > 0 else _FALLBACK_RATE
        if rate not in (44100, 48000, 96000):
            rate = _FALLBACK_RATE
        fmt.setSampleRate(rate)
        fmt.setChannelCount(_TARGET_CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return fmt

    def _ensure_sink(self) -> bool:
        fmt = self._preferred_format()
        rate = int(fmt.sampleRate())
        self._sample_rate = rate
        self._eq.set_sample_rate(float(self._sample_rate))
        self._eq.set_state(self._eq_state)
        self._high_cut.set_sample_rate(float(self._sample_rate))

        if self._sink is not None and getattr(self, "_sink_rate", None) == rate:
            try:
                self._sink.stop()
            except Exception:
                pass
            return True

        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink.deleteLater()
            self._sink = None

        device = (
            self._device
            if self._device and not self._device.isNull()
            else QMediaDevices.defaultAudioOutput()
        )
        self._sink = QAudioSink(device, fmt, self)
        self._sink_rate = rate
        # ~120 ms hardware buffer — absorbs brief decode stalls (e.g. video start).
        self._sink.setBufferSize(max(8192, self._sample_rate * _TARGET_CHANNELS * 2 // 8))
        self._sink.setVolume(self._output.effective_volume())
        return True

    def _clear_queue(self) -> None:
        while True:
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break
        self._float_pending = np.zeros(0, dtype=np.float32)
        self._byte_pending.clear()

    def _stop_pipeline(self, *, destroy_sink: bool = False) -> None:
        """Abort decode without blocking the UI thread.

        Old decode threads exit via generation/_decode_stop; joining them here
        used to stall seeks for up to ~1s when put() was blocked on a full queue.
        """
        self._pos_timer.stop()
        self._generation += 1
        self._decode_stop.set()
        self._decode_thread = None
        self._decode_eof_generation = None
        self._clear_queue()
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
            if destroy_sink:
                self._sink.deleteLater()
                self._sink = None

    def _enqueue_chunk(self, block: np.ndarray, generation: int) -> bool:
        """Non-blocking-ish put that abandons quickly on seek/stop."""
        while True:
            if self._decode_stop.is_set() or generation != self._generation:
                return False
            try:
                self._chunk_queue.put(block, timeout=_PUT_TIMEOUT_S)
                return True
            except queue.Full:
                continue

    def _wait_prebuffer(self, generation: int) -> None:
        deadline = time.monotonic() + _PREBUFFER_TIMEOUT_S
        while time.monotonic() < deadline:
            if generation != self._generation or self._decode_stop.is_set():
                return
            if self._chunk_queue.qsize() >= _PREBUFFER_CHUNKS:
                return
            time.sleep(0.004)

    def _restart_pipeline(self, *, start_ms: int, autoplay: bool) -> None:
        if not self._path:
            return
        self._stop_pipeline(destroy_sink=False)
        self._decode_stop.clear()
        self._decode_eof_generation = None
        self._ensure_sink()
        self._eq.reset()
        self._seek_frame = int(start_ms * self._sample_rate / 1000.0)
        self._frames_played = 0
        self._frames_decoded = 0
        self._position_ms = start_ms
        gen = self._generation
        self._decode_thread = threading.Thread(
            target=self._decode_loop,
            args=(self._path, start_ms, gen),
            name="PcmAirDecode",
            daemon=True,
        )
        self._decode_thread.start()
        now = time.monotonic()
        # During scrub bursts skip long prebuffer — keep UI responsive.
        if (now - self._last_restart_at) >= 0.1:
            self._wait_prebuffer(gen)
        self._last_restart_at = now
        if gen != self._generation:
            return
        if autoplay and self._sink is not None:
            self._sink.start(self._io)
            self._set_playback_state(QMediaPlayer.PlaybackState.PlayingState)
            self._set_media_status(QMediaPlayer.MediaStatus.BufferedMedia)
            self._pos_timer.start()
        elif self._sink is not None:
            self._sink.start(self._io)
            self._sink.suspend()
            self._set_playback_state(QMediaPlayer.PlaybackState.PausedState)
            self._set_media_status(QMediaPlayer.MediaStatus.BufferedMedia)

    def _decode_loop(self, path: str, start_ms: int, generation: int) -> None:
        try:
            import av
        except ImportError:
            return

        if self._decode_stop.is_set() or generation != self._generation:
            return

        try:
            container = av.open(path)
        except Exception:
            return

        try:
            if generation != self._generation or self._decode_stop.is_set():
                return
            if not container.streams.audio:
                return
            target_sr = max(1, int(self._sample_rate))
            resampler = av.audio.resampler.AudioResampler(
                format="fltp",
                layout="stereo",
                rate=target_sr,
            )
            try:
                # any_frame seek is faster for MP3 keyframe-ish positioning
                container.seek(max(0, int(start_ms)) * 1000, any_frame=True)
            except TypeError:
                try:
                    container.seek(max(0, int(start_ms)) * 1000)
                except Exception:
                    pass
            except Exception:
                pass

            pending_parts: list[np.ndarray] = []
            pending_size = 0
            frame_samples = _CHUNK_FRAMES * _TARGET_CHANNELS

            for frame in container.decode(audio=0):
                if self._decode_stop.is_set() or generation != self._generation:
                    break
                try:
                    frames = resampler.resample(frame)
                except Exception:
                    continue
                for rf in frames or []:
                    chunk = _frame_to_stereo_interleaved(rf.to_ndarray())
                    if chunk.size == 0:
                        continue
                    pending_parts.append(chunk)
                    pending_size += chunk.size
                    while pending_size >= frame_samples:
                        if self._decode_stop.is_set() or generation != self._generation:
                            break
                        if len(pending_parts) == 1:
                            pending = pending_parts[0]
                        else:
                            pending = np.concatenate(pending_parts)
                        block = pending[:frame_samples].copy()
                        rest = pending[frame_samples:]
                        pending_parts = [rest] if rest.size else []
                        pending_size = int(rest.size)
                        if not self._enqueue_chunk(block, generation):
                            return
                        self._frames_decoded += _CHUNK_FRAMES
                        # Only pace when comfortably buffered — never sleep near underrun.
                        if self._chunk_queue.qsize() >= _DECODE_PACE_DEPTH:
                            time.sleep(_DECODE_PACE_SLEEP_S)

            try:
                for rf in resampler.resample(None) or []:
                    chunk = _frame_to_stereo_interleaved(rf.to_ndarray())
                    if chunk.size:
                        pending_parts.append(chunk)
                        pending_size += chunk.size
            except Exception:
                pass

            if pending_size and not self._decode_stop.is_set() and generation == self._generation:
                pending = (
                    pending_parts[0]
                    if len(pending_parts) == 1
                    else np.concatenate(pending_parts)
                )
                self._enqueue_chunk(pending.copy(), generation)
        except Exception:
            pass
        finally:
            try:
                container.close()
            except Exception:
                pass
            if generation == self._generation:
                try:
                    self._chunk_queue.put_nowait(None)
                except queue.Full:
                    pass

    def _capture_spectrum(self, interleaved: np.ndarray) -> None:
        if not self._spectrum_enabled:
            return
        # Protect air against underrun when video decoder steals the GIL.
        if self._chunk_queue.qsize() < _LOW_WATER_CHUNKS:
            return
        self._chunks_since_spectrum += 1
        if self._chunks_since_spectrum < _SPECTRUM_CAPTURE_EVERY:
            return
        self._chunks_since_spectrum = 0
        try:
            mono = interleaved.reshape(-1, _TARGET_CHANNELS).mean(axis=1)
            n = min(mono.size, _SPECTRUM_PCM_SAMPLES)
            with self._spectrum_lock:
                if n >= _SPECTRUM_PCM_SAMPLES:
                    self._spectrum_pcm[:] = mono[-_SPECTRUM_PCM_SAMPLES:]
                else:
                    self._spectrum_pcm[:-n] = self._spectrum_pcm[n:]
                    self._spectrum_pcm[-n:] = mono[-n:]
                windowed = self._spectrum_pcm * self._hann
                spec = np.fft.rfft(windowed)
                mags = np.abs(spec)
                freqs = np.fft.rfftfreq(self._spectrum_pcm.size, d=1.0 / max(self._sample_rate, 1))
                ref = float(self._spectrum_pcm.size) * 0.5
                db = 20.0 * np.log10(np.maximum(mags / max(ref, 1.0), 1e-12))
                db = np.interp(self._spectrum_freqs, freqs, db, left=-90.0, right=-90.0)
                db = np.clip(db, -90.0, 0.0)
                peak = float(np.percentile(db, 95))
                if peak > self._spectrum_peak:
                    self._spectrum_peak += 0.35 * (peak - self._spectrum_peak)
                else:
                    self._spectrum_peak += 0.05 * (peak - self._spectrum_peak)
                display = np.clip(db - self._spectrum_peak, -90.0, 0.0)
                self._spectrum_db[:] = 0.65 * self._spectrum_db + 0.35 * display.astype(np.float32)
        except Exception:
            pass

    def _bytes_available(self) -> int:
        float_bytes = int(self._float_pending.size) * 2  # after int16
        queued = self._chunk_queue.qsize() * _CHUNK_FRAMES * _TARGET_CHANNELS * 2
        return len(self._byte_pending) + float_bytes + queued

    def _read_audio_bytes(self, maxlen: int) -> bytes:
        if maxlen <= 0:
            return b""

        # Refill float pending from dry decode queue.
        while self._float_pending.size * 2 < maxlen + 4096:
            try:
                item = self._chunk_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                # Remember which generation hit EOF; finish only after buffers drain.
                self._decode_eof_generation = int(self._generation)
                if (
                    self._float_pending.size == 0
                    and not self._byte_pending
                    and self._chunk_queue.empty()
                ):
                    gen = self._decode_eof_generation
                    QTimer.singleShot(0, lambda g=gen: self._on_decode_finished(g))
                break
            if self._float_pending.size:
                self._float_pending = np.concatenate([self._float_pending, item])
            else:
                self._float_pending = item

        # Convert enough float frames → EQ → int16 into byte_pending.
        need_samples = ((maxlen - len(self._byte_pending) + 3) // 4) * _TARGET_CHANNELS
        need_samples = max(0, need_samples)
        if need_samples > 0 and self._float_pending.size > 0:
            take = min(self._float_pending.size, need_samples)
            take -= take % _TARGET_CHANNELS
            if take > 0:
                dry = self._float_pending[:take]
                self._float_pending = self._float_pending[take:]
                wet = self._eq.process_interleaved(dry)
                wet = self._high_cut.process_interleaved(wet)
                gain = float(self._track_gain)
                if gain != 1.0:
                    wet = wet * np.float32(gain)
                self._capture_spectrum(wet)
                clipped = np.clip(wet, -1.0, 1.0)
                pcm = (clipped * 32767.0).astype(np.int16)
                self._byte_pending.extend(pcm.tobytes())
                self._frames_played += take // _TARGET_CHANNELS

        if not self._byte_pending:
            if (
                self._decode_eof_generation == self._generation
                and self._float_pending.size == 0
                and self._chunk_queue.empty()
            ):
                gen = self._generation
                QTimer.singleShot(0, lambda g=gen: self._on_decode_finished(g))
            return bytes(min(maxlen, 4096))

        take_b = min(len(self._byte_pending), maxlen)
        take_b -= take_b % 4
        if take_b <= 0:
            return b""
        data = bytes(self._byte_pending[:take_b])
        del self._byte_pending[:take_b]

        # Decode already ended but PCM was still draining — finish when empty.
        if (
            getattr(self, "_decode_eof_generation", None) == self._generation
            and not self._byte_pending
            and self._float_pending.size == 0
            and self._chunk_queue.empty()
        ):
            gen = self._generation
            QTimer.singleShot(0, lambda g=gen: self._on_decode_finished(g))
        return data

    def _on_decode_finished(self, generation: int | None = None) -> None:
        # Ignore stale EOF from a previous seek/restart/track (kills NEXT otherwise).
        if generation is not None and int(generation) != int(self._generation):
            return
        if self._playback_state != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._byte_pending or self._float_pending.size or not self._chunk_queue.empty():
            return
        if getattr(self, "_decode_eof_generation", None) not in (None, self._generation):
            return
        self._decode_eof_generation = None
        self._set_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self._pos_timer.stop()
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._set_playback_state(QMediaPlayer.PlaybackState.StoppedState)

    def _emit_position(self) -> None:
        if self._sample_rate <= 0:
            return
        pos = self._position_ms
        if self._sink is not None and self._playback_state == QMediaPlayer.PlaybackState.PlayingState:
            try:
                us = int(self._sink.processedUSecs())
                pos = int(self._seek_frame * 1000 / self._sample_rate) + us // 1000
            except Exception:
                pos = int((self._seek_frame + self._frames_played) * 1000 / self._sample_rate)
        else:
            pos = int((self._seek_frame + self._frames_played) * 1000 / self._sample_rate)
        if self._duration_ms > 0:
            pos = min(pos, self._duration_ms)
        if pos != self._position_ms:
            self._position_ms = pos
            self.positionChanged.emit(pos)
