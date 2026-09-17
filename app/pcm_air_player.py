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
from app.stems import STEM_INSTRUMENTAL, STEM_VOCALS

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


class _StemWavReader:
    """Sample-accurate stereo float reader for a stem WAV."""

    def __init__(self, path: str):
        import soundfile as sf

        self._path = path
        # soundfile needs a filesystem path; open after validating.
        info = sf.info(path)
        self.sample_rate = int(info.samplerate)
        self.frames = int(info.frames)
        self.channels = int(info.channels)
        if self.frames <= 0 or self.sample_rate <= 0:
            raise ValueError(f"Invalid stem WAV: {path}")
        self._file = sf.SoundFile(path, mode="r")
        self._pos = 0

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass

    def seek_frame(self, frame: int) -> None:
        frame = max(0, min(int(frame), self.frames))
        try:
            self._file.seek(frame)
            self._pos = frame
        except Exception:
            self._pos = frame

    def read_interleaved(self, n_frames: int, *, target_channels: int = 2) -> np.ndarray:
        if n_frames <= 0:
            return np.zeros(0, dtype=np.float32)
        try:
            data = self._file.read(n_frames, dtype="float32", always_2d=True)
        except Exception:
            data = np.zeros((0, max(1, self.channels)), dtype=np.float32)
        got = int(data.shape[0])
        self._pos += got
        if got == 0:
            return np.zeros(0, dtype=np.float32)

        if data.shape[1] == 1:
            stereo = np.column_stack([data[:, 0], data[:, 0]])
        else:
            stereo = data[:, :2]
        if got < n_frames:
            pad = np.zeros((n_frames - got, 2), dtype=np.float32)
            stereo = np.concatenate([stereo, pad], axis=0)
        return stereo.reshape(-1).astype(np.float32, copy=False)

    @property
    def exhausted(self) -> bool:
        return self._pos >= self.frames


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
        self._stem_enabled = False
        self._stem_paths: dict[str, str] = {}
        self._stem_eq = {
            STEM_VOCALS: EqProcessor(channels=_TARGET_CHANNELS, sample_rate=44100.0),
            STEM_INSTRUMENTAL: EqProcessor(channels=_TARGET_CHANNELS, sample_rate=44100.0),
        }
        self._stem_eq_state = {
            STEM_VOCALS: EqState(),
            STEM_INSTRUMENTAL: EqState(),
        }
        self._stem_gain = {
            STEM_VOCALS: 1.0,
            STEM_INSTRUMENTAL: 1.0,
        }
        # +1 keep vocals polarity, -1 invert. None until the first audible chunk.
        self._stem_vocals_sign: np.float32 | None = None
        self._stem_pending = {
            STEM_VOCALS: np.zeros(0, dtype=np.float32),
            STEM_INSTRUMENTAL: np.zeros(0, dtype=np.float32),
        }
        self._stem_pair_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        self._high_cut = HighCutSweep(channels=_TARGET_CHANNELS, sample_rate=44100.0)
        self._high_cut_elapsed = QElapsedTimer()
        self._high_cut_duration_ms = 0
        self._high_cut_on_complete = None
        self._high_cut_ui_active = False
        self._master_fx = None
        self._master_fx_copy = 0
        self._master_eq = None
        self._master_eq_state: EqState | None = None

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

    def set_master_fx(self, host, copy_index: int = 0) -> None:
        """Attach a MasterFxHost copy that runs after track EQ / stems / gain."""
        self._master_fx = host
        self._master_fx_copy = int(copy_index)

    def set_master_eq(self, eq_processor: EqProcessor, state_dict: dict | None = None) -> None:
        """Attach the shared EqProcessor for master EQ processing."""
        self._master_eq = eq_processor
        if state_dict and isinstance(state_dict, dict) and state_dict.get("bands"):
            self._master_eq_state = EqState.from_dict(state_dict)
        else:
            self._master_eq_state = None

    def refresh_master_eq(self) -> None:
        """Refresh master EQ state from the shared processor."""
        if self._master_eq is not None and self._master_eq._filters:
            # Create a state from current filters by reading coefficients
            # This is a fallback; real state should come from UI
            pass

    def set_eq_state(self, state: EqState | None) -> None:
        """Live coefficient swap — no pipeline restart."""
        self._eq_state = state or EqState()
        self._eq.set_state(self._eq_state)

    def set_track_gain_db(self, gain_db: float) -> None:
        """Per-track linear gain applied in the PCM path (after EQ / stem mix)."""
        from app.loudness import clamp_gain_db, db_to_linear

        self._track_gain = db_to_linear(clamp_gain_db(gain_db))

    def set_stem_mix(
        self,
        *,
        vocals_path: str,
        instrumental_path: str,
        vocals_eq: EqState | None = None,
        instrumental_eq: EqState | None = None,
        vocals_gain_db: float = 0.0,
        instrumental_gain_db: float = 0.0,
    ) -> None:
        """Enable dual-stem playback. Master track gain stays independent."""
        from app.loudness import db_to_linear
        from app.stems import is_readable_stem_wav
        from app.widgets.stem_fader import clamp_stem_gain_db

        if not (
            is_readable_stem_wav(vocals_path) and is_readable_stem_wav(instrumental_path)
        ):
            self.clear_stem_mix()
            return

        was = self._stem_enabled
        old_paths = dict(self._stem_paths)
        self._stem_enabled = True
        self._stem_paths = {
            STEM_VOCALS: vocals_path,
            STEM_INSTRUMENTAL: instrumental_path,
        }
        self.set_stem_eq_state(STEM_VOCALS, vocals_eq)
        self.set_stem_eq_state(STEM_INSTRUMENTAL, instrumental_eq)
        self._stem_gain[STEM_VOCALS] = db_to_linear(clamp_stem_gain_db(vocals_gain_db))
        self._stem_gain[STEM_INSTRUMENTAL] = db_to_linear(
            clamp_stem_gain_db(instrumental_gain_db)
        )
        if (
            was
            and old_paths.get(STEM_VOCALS) == vocals_path
            and old_paths.get(STEM_INSTRUMENTAL) == instrumental_path
        ):
            return
        if self._path and self._playback_state != QMediaPlayer.PlaybackState.StoppedState:
            autoplay = self._playback_state == QMediaPlayer.PlaybackState.PlayingState
            self._restart_pipeline(start_ms=self._position_ms, autoplay=autoplay)

    def clear_stem_mix(self) -> None:
        was = self._stem_enabled
        self._stem_enabled = False
        self._stem_paths = {}
        if was and self._path and self._playback_state != QMediaPlayer.PlaybackState.StoppedState:
            autoplay = self._playback_state == QMediaPlayer.PlaybackState.PlayingState
            self._restart_pipeline(start_ms=self._position_ms, autoplay=autoplay)

    def set_stem_eq_state(self, stem_id: str, state: EqState | None) -> None:
        if stem_id not in self._stem_eq:
            return
        # Copy so Vocal / Inst never share the live UI EqState object.
        eq_state = EqState.from_dict((state or EqState()).to_dict())
        self._stem_eq_state[stem_id] = eq_state
        self._stem_eq[stem_id].set_state(eq_state)

    def set_stem_gain_db(self, stem_id: str, gain_db: float) -> None:
        from app.loudness import db_to_linear
        from app.widgets.stem_fader import clamp_stem_gain_db

        if stem_id not in self._stem_gain:
            return
        self._stem_gain[stem_id] = db_to_linear(clamp_stem_gain_db(gain_db))

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
        for stem_id, proc in self._stem_eq.items():
            proc.set_sample_rate(float(self._sample_rate))
            proc.set_state(self._stem_eq_state.get(stem_id) or EqState())
        self._high_cut.set_sample_rate(float(self._sample_rate))
        if self._master_fx is not None:
            self._master_fx.set_sample_rate(float(self._sample_rate))
        if self._master_eq is not None:
            self._master_eq.set_sample_rate(float(self._sample_rate))
            if self._master_eq_state is not None:
                self._master_eq.set_state(self._master_eq_state)

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
        while True:
            try:
                self._stem_pair_queue.get_nowait()
            except queue.Empty:
                break
        self._float_pending = np.zeros(0, dtype=np.float32)
        self._stem_pending = {
            STEM_VOCALS: np.zeros(0, dtype=np.float32),
            STEM_INSTRUMENTAL: np.zeros(0, dtype=np.float32),
        }
        self._stem_vocals_sign = None
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

    def _enqueue_stem_pair(
        self,
        vocals: np.ndarray,
        instrumental: np.ndarray,
        generation: int,
    ) -> bool:
        while True:
            if self._decode_stop.is_set() or generation != self._generation:
                return False
            try:
                self._stem_pair_queue.put(
                    (generation, vocals, instrumental), timeout=_PUT_TIMEOUT_S
                )
                return True
            except queue.Full:
                continue

    def _wait_prebuffer(self, generation: int) -> None:
        deadline = time.monotonic() + _PREBUFFER_TIMEOUT_S
        while time.monotonic() < deadline:
            if generation != self._generation or self._decode_stop.is_set():
                return
            ready = (
                self._stem_pair_queue.qsize()
                if self._stem_enabled
                else self._chunk_queue.qsize()
            )
            if ready >= _PREBUFFER_CHUNKS:
                return
            time.sleep(0.004)

    def _decode_loop_stems(
        self,
        vocals_path: str,
        instrumental_path: str,
        start_ms: int,
        generation: int,
    ) -> None:
        vocals_reader: _StemWavReader | None = None
        inst_reader: _StemWavReader | None = None
        try:
            vocals_reader = _StemWavReader(vocals_path)
            inst_reader = _StemWavReader(instrumental_path)
            src_rate = int(vocals_reader.sample_rate) or int(inst_reader.sample_rate)
            target_sr = max(1, int(self._sample_rate))
            start_frame_src = int(max(0, start_ms) * src_rate / 1000.0)
            vocals_reader.seek_frame(start_frame_src)
            inst_reader.seek_frame(start_frame_src)

            # Read in source-rate frames, resample to target if needed.
            src_chunk = _CHUNK_FRAMES
            if src_rate != target_sr:
                src_chunk = max(64, int(round(_CHUNK_FRAMES * src_rate / target_sr)))

            pending_v = np.zeros(0, dtype=np.float32)
            pending_i = np.zeros(0, dtype=np.float32)
            frame_samples = _CHUNK_FRAMES * _TARGET_CHANNELS

            while not self._decode_stop.is_set() and generation == self._generation:
                if (
                    vocals_reader.exhausted
                    and inst_reader.exhausted
                    and pending_v.size == 0
                    and pending_i.size == 0
                ):
                    break
                v = vocals_reader.read_interleaved(src_chunk)
                i = inst_reader.read_interleaved(src_chunk)
                if (
                    v.size == 0
                    and i.size == 0
                    and pending_v.size == 0
                    and pending_i.size == 0
                ):
                    break
                n = max(v.size, i.size)
                if n:
                    if v.size < n:
                        v = np.pad(v, (0, n - v.size))
                    if i.size < n:
                        i = np.pad(i, (0, n - i.size))
                    if src_rate != target_sr:
                        v = self._resample_interleaved(v, src_rate, target_sr)
                        i = self._resample_interleaved(i, src_rate, target_sr)
                    pending_v = np.concatenate([pending_v, v]) if pending_v.size else v
                    pending_i = np.concatenate([pending_i, i]) if pending_i.size else i

                # Wait until both stems have a full frame. Slicing a short
                # instrumental buffer desyncs the pair and the minus vanishes.
                while (
                    pending_v.size >= frame_samples
                    and pending_i.size >= frame_samples
                ):
                    if self._decode_stop.is_set() or generation != self._generation:
                        return
                    vb = pending_v[:frame_samples].copy()
                    ib = pending_i[:frame_samples].copy()
                    pending_v = pending_v[frame_samples:]
                    pending_i = pending_i[frame_samples:]
                    if not self._enqueue_stem_pair(vb, ib, generation):
                        return
                    self._frames_decoded += _CHUNK_FRAMES
                    if self._stem_pair_queue.qsize() >= _DECODE_PACE_DEPTH:
                        time.sleep(_DECODE_PACE_SLEEP_S)

                if vocals_reader.exhausted and inst_reader.exhausted:
                    break

            if (
                (pending_v.size or pending_i.size)
                and not self._decode_stop.is_set()
                and generation == self._generation
            ):
                n = max(pending_v.size, pending_i.size)
                if pending_v.size < n:
                    pending_v = np.pad(pending_v, (0, n - pending_v.size))
                if pending_i.size < n:
                    pending_i = np.pad(pending_i, (0, n - pending_i.size))
                self._enqueue_stem_pair(pending_v.copy(), pending_i.copy(), generation)

            # EOF marker on both queues
            if generation == self._generation:
                try:
                    self._stem_pair_queue.put_nowait(None)
                except queue.Full:
                    pass
        except Exception:
            pass
        finally:
            if vocals_reader is not None:
                vocals_reader.close()
            if inst_reader is not None:
                inst_reader.close()

    @staticmethod
    def _resample_interleaved(interleaved: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate or interleaved.size == 0:
            return interleaved
        frames = interleaved.reshape(-1, _TARGET_CHANNELS)
        target_len = max(1, int(round(frames.shape[0] * dst_rate / src_rate)))
        x_old = np.linspace(0.0, 1.0, frames.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, target_len, endpoint=False)
        out = np.empty((target_len, _TARGET_CHANNELS), dtype=np.float32)
        for c in range(_TARGET_CHANNELS):
            out[:, c] = np.interp(x_new, x_old, frames[:, c]).astype(np.float32)
        return out.reshape(-1)

    def _restart_pipeline(self, *, start_ms: int, autoplay: bool) -> None:
        if not self._path:
            return
        self._stop_pipeline(destroy_sink=False)
        self._decode_stop.clear()
        self._decode_eof_generation = None
        self._ensure_sink()
        self._eq.reset()
        for proc in self._stem_eq.values():
            proc.reset()
        if self._master_fx is not None:
            self._master_fx.reset_copy(self._master_fx_copy)
        if self._master_eq is not None:
            self._master_eq.reset()
        self._seek_frame = int(start_ms * self._sample_rate / 1000.0)
        self._frames_played = 0
        self._frames_decoded = 0
        self._position_ms = start_ms
        gen = self._generation
        use_stems = False
        if (
            self._stem_enabled
            and bool(self._stem_paths.get(STEM_VOCALS))
            and bool(self._stem_paths.get(STEM_INSTRUMENTAL))
            and os.path.isfile(self._stem_paths[STEM_VOCALS])
            and os.path.isfile(self._stem_paths[STEM_INSTRUMENTAL])
        ):
            # Probe readers once — corrupt/empty stems fall back to original.
            try:
                probe_v = _StemWavReader(self._stem_paths[STEM_VOCALS])
                probe_i = _StemWavReader(self._stem_paths[STEM_INSTRUMENTAL])
                probe_v.close()
                probe_i.close()
                use_stems = True
            except Exception:
                self._stem_enabled = False
                use_stems = False
        if use_stems:
            target = self._decode_loop_stems
            args = (
                self._stem_paths[STEM_VOCALS],
                self._stem_paths[STEM_INSTRUMENTAL],
                start_ms,
                gen,
            )
        else:
            target = self._decode_loop
            args = (self._path, start_ms, gen)
        self._decode_thread = threading.Thread(
            target=target,
            args=args,
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
        depth = (
            self._stem_pair_queue.qsize()
            if self._stem_enabled
            else self._chunk_queue.qsize()
        )
        if depth < _LOW_WATER_CHUNKS:
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
        if self._stem_enabled:
            float_bytes = (
                min(
                    self._stem_pending[STEM_VOCALS].size,
                    self._stem_pending[STEM_INSTRUMENTAL].size,
                )
                * 2
            )
            queued = (
                self._stem_pair_queue.qsize()
                * _CHUNK_FRAMES
                * _TARGET_CHANNELS
                * 2
            )
            return len(self._byte_pending) + float_bytes + queued
        float_bytes = int(self._float_pending.size) * 2  # after int16
        queued = self._chunk_queue.qsize() * _CHUNK_FRAMES * _TARGET_CHANNELS * 2
        return len(self._byte_pending) + float_bytes + queued

    def _drain_stem_queues(self) -> bool:
        """Pull dry stem pairs into pending buffers. Returns True on EOF."""
        hit_eof = False
        while (
            min(
                self._stem_pending[STEM_VOCALS].size,
                self._stem_pending[STEM_INSTRUMENTAL].size,
            )
            * 2
            < 8192
        ):
            try:
                item = self._stem_pair_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                hit_eof = True
                break
            if len(item) == 3:
                gen, vocals, instrumental = item
                if gen != self._generation:
                    continue
            else:
                vocals, instrumental = item
            for stem_id, block in (
                (STEM_VOCALS, vocals),
                (STEM_INSTRUMENTAL, instrumental),
            ):
                pending = self._stem_pending[stem_id]
                if pending.size:
                    self._stem_pending[stem_id] = np.concatenate([pending, block])
                else:
                    self._stem_pending[stem_id] = block
        return hit_eof

    @staticmethod
    def _vocals_mix_sign(vocals: np.ndarray, instrumental: np.ndarray) -> np.float32:
        """Pick vocals polarity so the pair sums instead of cancelling.

        UVR MDX Inst HQ 5 often writes the secondary stem inverted. On tracks
        like Sexy Makeba that makes vocals ≈ −instrumental, and 0 dB + 0 dB
        plays as near-silence.
        """
        if vocals.size == 0 or instrumental.size == 0:
            return np.float32(1.0)
        energy = float(np.mean(np.square(vocals))) + float(np.mean(np.square(instrumental)))
        if energy < 1e-8:
            return np.float32(1.0)
        sum_e = float(np.mean(np.square(vocals + instrumental)))
        diff_e = float(np.mean(np.square(vocals - instrumental)))
        return np.float32(-1.0 if diff_e > sum_e * 1.05 else 1.0)

    def _mix_stems(self, n_samples: int) -> np.ndarray | None:
        """Take n interleaved samples from each stem, EQ+gain, sum."""
        n_samples -= n_samples % _TARGET_CHANNELS
        if n_samples <= 0:
            return None
        available = min(
            self._stem_pending[STEM_VOCALS].size,
            self._stem_pending[STEM_INSTRUMENTAL].size,
        )
        take = min(available, n_samples)
        take -= take % _TARGET_CHANNELS
        if take <= 0:
            return None

        processed: dict[str, np.ndarray] = {}
        for stem_id in (STEM_VOCALS, STEM_INSTRUMENTAL):
            dry = self._stem_pending[stem_id][:take]
            self._stem_pending[stem_id] = self._stem_pending[stem_id][take:]
            wet = self._stem_eq[stem_id].process_interleaved(dry)
            gain = float(self._stem_gain.get(stem_id, 1.0))
            if gain != 1.0:
                wet = wet * np.float32(gain)
            processed[stem_id] = wet

        vocals = processed[STEM_VOCALS]
        instrumental = processed[STEM_INSTRUMENTAL]
        if self._stem_vocals_sign is None:
            v_e = float(np.mean(np.square(vocals)))
            i_e = float(np.mean(np.square(instrumental)))
            if v_e > 1e-8 and i_e > 1e-8:
                self._stem_vocals_sign = self._vocals_mix_sign(vocals, instrumental)
        if self._stem_vocals_sign is not None and self._stem_vocals_sign < 0:
            vocals = -vocals
        mixed = vocals + instrumental
        # Two 0 dBFS stems sum past 1.0; hard-clip in _emit_wet_pcm then
        # buries the instrumental under the vocals. Peak-limit instead.
        if mixed.size:
            peak = float(np.max(np.abs(mixed)))
            if peak > 0.99:
                mixed = mixed * np.float32(0.99 / peak)
        return mixed

    def prefetch_pcm_ms(self, milliseconds: int) -> None:
        """Render extra output so a later GIL stall does not underrun.

        Releases the audio lock between chunks so QAudioSink can keep pulling.
        """
        if self._sample_rate <= 0 or milliseconds <= 0:
            return
        if self._playback_state != QMediaPlayer.PlaybackState.PlayingState:
            return
        target = int(self._sample_rate * milliseconds / 1000.0) * _TARGET_CHANNELS * 2
        for _ in range(48):
            with self._lock:
                if len(self._byte_pending) >= target:
                    return
                before = len(self._byte_pending)
                self._produce_pcm(max(8192, target - before))
                grew = len(self._byte_pending) > before
            if not grew:
                return
            time.sleep(0)

    def _produce_pcm(self, extra_bytes: int) -> None:
        """Append processed Int16 to byte_pending without consuming it."""
        if extra_bytes <= 0:
            return
        if self._stem_enabled and self._stem_paths:
            hit_eof = self._drain_stem_queues()
            if hit_eof:
                self._decode_eof_generation = int(self._generation)
            need_samples = ((extra_bytes + 3) // 4) * _TARGET_CHANNELS
            need_samples = max(0, need_samples)
            if need_samples <= 0:
                return
            while (
                min(
                    self._stem_pending[STEM_VOCALS].size,
                    self._stem_pending[STEM_INSTRUMENTAL].size,
                )
                < need_samples
            ):
                if not self._drain_stem_queues():
                    break
            mixed = self._mix_stems(need_samples)
            if mixed is not None and mixed.size:
                wet = self._high_cut.process_interleaved(mixed)
                gain = float(self._track_gain)
                if gain != 1.0:
                    wet = wet * np.float32(gain)
                self._emit_wet_pcm(wet)
            return

        maxlen = len(self._byte_pending) + extra_bytes
        while self._float_pending.size * 2 < maxlen + 4096:
            try:
                item = self._chunk_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._decode_eof_generation = int(self._generation)
                break
            if self._float_pending.size:
                self._float_pending = np.concatenate([self._float_pending, item])
            else:
                self._float_pending = item
        need_samples = ((extra_bytes + 3) // 4) * _TARGET_CHANNELS
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
                self._emit_wet_pcm(wet)

    def _emit_wet_pcm(self, wet: np.ndarray) -> None:
        """Spectrum (pre-FX) → master EQ → Int16."""
        frames = int(wet.size) // _TARGET_CHANNELS
        self._capture_spectrum(wet)
        # Apply master EQ
        if self._master_eq is not None:
            wet = self._master_eq.process_interleaved(wet)
        clipped = np.clip(wet, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        self._byte_pending.extend(pcm.tobytes())
        self._frames_played += frames

    def _read_audio_bytes(self, maxlen: int) -> bytes:
        if maxlen <= 0:
            return b""
        with self._lock:
            return self._read_audio_bytes_locked(maxlen)

    def _read_audio_bytes_locked(self, maxlen: int) -> bytes:
        if self._stem_enabled and self._stem_paths:
            return self._read_audio_bytes_stems(maxlen)

        self._produce_pcm(max(0, maxlen - len(self._byte_pending) + 4096))

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

    def _read_audio_bytes_stems(self, maxlen: int) -> bytes:
        self._produce_pcm(max(0, maxlen - len(self._byte_pending) + 4096))

        stems_empty = (
            self._stem_pending[STEM_VOCALS].size == 0
            and self._stem_pending[STEM_INSTRUMENTAL].size == 0
            and self._stem_pair_queue.empty()
        )

        if not self._byte_pending:
            if self._decode_eof_generation == self._generation and stems_empty:
                gen = self._generation
                QTimer.singleShot(0, lambda g=gen: self._on_decode_finished(g))
            return bytes(min(maxlen, 4096))

        take_b = min(len(self._byte_pending), maxlen)
        take_b -= take_b % 4
        if take_b <= 0:
            return b""
        data = bytes(self._byte_pending[:take_b])
        del self._byte_pending[:take_b]

        if (
            getattr(self, "_decode_eof_generation", None) == self._generation
            and not self._byte_pending
            and stems_empty
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
