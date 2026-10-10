"""Single-sink stereo mix bus for air channels (Main A/B + aux)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PyQt6.QtCore import QIODevice, QObject, pyqtSignal
from PyQt6.QtMultimedia import QAudioDevice, QAudioFormat, QAudioSink, QMediaDevices

from app.eq_dsp import EqProcessor
from app.eq_state import EqState
from app.limiter_dsp import PeakLimiter

_TARGET_CHANNELS = 2
_FALLBACK_RATE = 48000


@dataclass
class MixBusChannel:
    """One mixer strip: one or more float sources summed under a fader."""

    channel_id: str
    name: str
    is_main: bool = False
    mute: bool = False
    fader_db: float = 0.0
    # Callables: () -> float linear gain for each source (deck volume / 1.0)
    sources: list[tuple[Callable[[int], np.ndarray], Callable[[], float]]] = field(
        default_factory=list
    )
    peak: float = 0.0


class _MixBusIO(QIODevice):
    def __init__(self, bus: "AirMixBus"):
        super().__init__()
        self._bus = bus
        self.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered)

    def readData(self, maxlen: int) -> bytes:  # noqa: N802
        return self._bus._read_audio_bytes(maxlen)

    def bytesAvailable(self) -> int:  # noqa: N802
        return max(0, self._bus._bytes_available()) + super().bytesAvailable()

    def isSequential(self) -> bool:  # noqa: N802
        return True


class AirMixBus(QObject):
    """Pull float PCM from channel sources → EQ → limiter → one QAudioSink."""

    sampleRateChanged = pyqtSignal(int)
    peaksUpdated = pyqtSignal(dict)  # channel_id -> peak 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: dict[str, MixBusChannel] = {}
        self._channel_order: list[str] = []
        self._master_gain = 1.0
        self._muted = False
        self._device: QAudioDevice = QMediaDevices.defaultAudioOutput()
        self._sink: QAudioSink | None = None
        self._sink_rate: int | None = None
        self._sample_rate = _FALLBACK_RATE
        self._io = _MixBusIO(self)
        self._byte_pending = bytearray()
        self._lock = threading.Lock()
        self._running = False

        self.master_eq = EqProcessor(channels=_TARGET_CHANNELS, sample_rate=float(_FALLBACK_RATE))
        self._master_eq_state: EqState | None = None
        self.limiter = PeakLimiter(sample_rate=float(_FALLBACK_RATE))

        self._peak_emit_counter = 0
        self._channel_peaks: dict[str, float] = {}

    # --- channel registry -------------------------------------------------

    def set_channels(self, channels: list[MixBusChannel]) -> None:
        with self._lock:
            self._channels = {c.channel_id: c for c in channels}
            self._channel_order = [c.channel_id for c in channels]

    def update_channel_fader(self, channel_id: str, fader_db: float, *, mute: bool | None = None) -> None:
        ch = self._channels.get(channel_id)
        if ch is None:
            return
        ch.fader_db = float(fader_db)
        if mute is not None:
            ch.mute = bool(mute)

    def channel_ids(self) -> list[str]:
        return list(self._channel_order)

    # --- master / device --------------------------------------------------

    def set_master_gain(self, gain: float) -> None:
        self._master_gain = max(0.0, min(1.0, float(gain)))

    def master_gain(self) -> float:
        return self._master_gain

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._sink is not None:
            try:
                self._sink.setVolume(0.0 if self._muted else 1.0)
            except Exception:
                pass

    def is_muted(self) -> bool:
        return self._muted

    def set_device(self, device: QAudioDevice) -> None:
        self._device = device
        was = self._running
        self.stop()
        if was:
            self.start()

    def device(self) -> QAudioDevice:
        return self._device

    def sample_rate(self) -> int:
        return int(self._sample_rate)

    def set_master_eq_state(self, state: EqState | None) -> None:
        self._master_eq_state = state
        if state is not None:
            self.master_eq.set_state(state)
        else:
            self.master_eq.set_state(EqState())

    def refresh_master_eq(self) -> None:
        if self._master_eq_state is not None:
            self.master_eq.set_state(self._master_eq_state)

    # --- lifecycle --------------------------------------------------------

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
        rate_changed = rate != self._sample_rate
        self._sample_rate = rate
        self.master_eq.set_sample_rate(float(rate))
        if self._master_eq_state is not None:
            self.master_eq.set_state(self._master_eq_state)
        self.limiter.set_sample_rate(float(rate))
        if rate_changed:
            self.sampleRateChanged.emit(rate)

        if self._sink is not None and self._sink_rate == rate:
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
        self._sink.setBufferSize(max(8192, self._sample_rate * _TARGET_CHANNELS * 2 // 8))
        self._sink.setVolume(0.0 if self._muted else 1.0)
        return True

    def start(self) -> None:
        self._ensure_sink()
        self._byte_pending.clear()
        if self._sink is None:
            return
        self._sink.start(self._io)
        self._running = True

    def stop(self) -> None:
        self._running = False
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass

    def destroy(self) -> None:
        self.stop()
        if self._sink is not None:
            self._sink.deleteLater()
            self._sink = None

    def ensure_running(self) -> None:
        """Keep the sink alive while any source may play (always-on mix bus)."""
        if not self._running or self._sink is None:
            self.start()

    def _bytes_available(self) -> int:
        return max(4096, len(self._byte_pending))

    def _db_to_lin(self, db: float) -> float:
        return float(10.0 ** (float(db) / 20.0))

    def _mix_float(self, n_samples: int) -> np.ndarray:
        n_samples -= n_samples % _TARGET_CHANNELS
        if n_samples <= 0:
            return np.zeros(0, dtype=np.float32)

        mixed = np.zeros(n_samples, dtype=np.float32)
        peaks: dict[str, float] = {}

        for cid in self._channel_order:
            ch = self._channels.get(cid)
            if ch is None:
                continue
            ch_buf = np.zeros(n_samples, dtype=np.float32)
            for read_fn, gain_fn in ch.sources:
                try:
                    chunk = read_fn(n_samples)
                except Exception:
                    chunk = np.zeros(0, dtype=np.float32)
                if chunk is None or chunk.size == 0:
                    continue
                if chunk.size < n_samples:
                    padded = np.zeros(n_samples, dtype=np.float32)
                    padded[: chunk.size] = chunk
                    chunk = padded
                elif chunk.size > n_samples:
                    chunk = chunk[:n_samples]
                g = float(gain_fn())
                if g != 1.0:
                    chunk = chunk * np.float32(g)
                ch_buf += chunk

            if ch.mute:
                peaks[cid] = 0.0
                continue
            fader = self._db_to_lin(ch.fader_db)
            if fader != 1.0:
                ch_buf = ch_buf * np.float32(fader)
            peak = float(np.max(np.abs(ch_buf))) if ch_buf.size else 0.0
            peaks[cid] = min(1.0, peak)
            mixed += ch_buf

        self._channel_peaks = peaks
        self._peak_emit_counter += 1
        if self._peak_emit_counter >= 8:
            self._peak_emit_counter = 0
            # Emit from audio thread — Qt queues to main if connected QueuedConnection
            try:
                self.peaksUpdated.emit(dict(peaks))
            except Exception:
                pass

        mg = 0.0 if self._muted else float(self._master_gain)
        if mg != 1.0:
            mixed = mixed * np.float32(mg)

        if self.master_eq is not None:
            mixed = self.master_eq.process_interleaved(mixed)
        mixed = self.limiter.process_interleaved(mixed)
        return mixed

    def _read_audio_bytes(self, maxlen: int) -> bytes:
        if maxlen <= 0:
            return b""
        with self._lock:
            need = max(0, maxlen - len(self._byte_pending) + 4096)
            if need > 0:
                n_samples = ((need + 3) // 4) * _TARGET_CHANNELS
                wet = self._mix_float(n_samples)
                if wet.size:
                    clipped = np.clip(wet, -1.0, 1.0)
                    pcm = (clipped * 32767.0).astype(np.int16)
                    self._byte_pending.extend(pcm.tobytes())

            if not self._byte_pending:
                return bytes(min(maxlen, 4096))

            take_b = min(len(self._byte_pending), maxlen)
            take_b -= take_b % 4
            if take_b <= 0:
                return b""
            data = bytes(self._byte_pending[:take_b])
            del self._byte_pending[:take_b]
            return data
