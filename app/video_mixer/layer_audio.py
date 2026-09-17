"""Per-layer video audio via QMediaPlayer, synced to decoder position."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaPlayer

from app.video_mixer.model import DEFAULT_LOOP_FADE_MS, Layer, MixerModel
from app.volume_fader import VolumeFader

if TYPE_CHECKING:
    pass

DEFAULT_AUDIO_FADE_MS = DEFAULT_LOOP_FADE_MS


def _clip_envelope(
    *,
    position_ms: int,
    duration_ms: int,
    playback: str,
    loop_transition: str,
    fade_ms: int,
) -> float:
    """0..1 gain from clip end fades (no fade-in — video sync starts at current position)."""
    fade = max(0, int(fade_ms))
    if fade <= 0 or duration_ms <= 0:
        return 1.0
    pos = max(0, int(position_ms))
    dur = max(1, int(duration_ms))
    fade = min(fade, max(1, dur // 2))

    gain = 1.0
    if playback == "stop" or (playback == "loop" and loop_transition == "fade"):
        remaining = dur - pos
        if remaining < fade:
            gain = min(gain, max(0.0, remaining / float(fade)))
    return max(0.0, min(1.0, gain))


class LayerAudioPlayer:
    def __init__(
        self,
        path: str,
        *,
        output_device: QAudioDevice | None = None,
        output_blocked: bool = False,
    ):
        self.path = path
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        if output_device is not None:
            self.audio.setDevice(output_device)
        self.player.setAudioOutput(self.audio)
        self.player.setSource(QUrl.fromLocalFile(path))
        self._output_blocked = bool(output_blocked)
        self._master_volume = 1.0
        self._gate = 0.0
        self._envelope = 1.0
        self._want_audible = False
        self._fading_out = False
        self._fader = VolumeFader(self._apply_gate)
        self.audio.setMuted(True)
        self.audio.setVolume(0.0)

    def set_output_device(self, device: QAudioDevice, *, blocked: bool = False) -> None:
        self._output_blocked = bool(blocked)
        self.audio.setDevice(device)
        self._apply_output_volume()

    def set_master_volume(self, volume: float) -> None:
        self._master_volume = max(0.0, min(1.0, float(volume)))
        self._apply_output_volume()

    @property
    def is_output_active(self) -> bool:
        return self._want_audible or self._fading_out or self._gate > 0.001

    def _apply_gate(self, volume: float) -> None:
        self._gate = max(0.0, min(1.0, float(volume)))
        self._apply_output_volume()

    def _apply_output_volume(self) -> None:
        if self._output_blocked:
            self.audio.setMuted(True)
            self.audio.setVolume(0.0)
            return
        level = max(0.0, min(1.0, self._gate * self._envelope * self._master_volume))
        self.audio.setVolume(level)
        if level <= 0.0001 and not self._fader.is_active and not self._want_audible:
            self.audio.setMuted(True)
        else:
            self.audio.setMuted(False)

    def _fade_ms(self, layer: Layer) -> int:
        if layer.playback == "loop" and layer.loop_transition == "fade":
            return max(1, int(layer.loop_fade_ms) or DEFAULT_AUDIO_FADE_MS)
        return DEFAULT_AUDIO_FADE_MS

    def sync(
        self,
        layer: Layer,
        *,
        playing: bool,
        muted: bool,
        visible: bool = True,
    ) -> None:
        self._fader.advance()

        want = (
            bool(playing)
            and not bool(muted)
            and bool(visible)
            and not layer.file_missing
        )
        fade_ms = self._fade_ms(layer)
        duration = max(0, int(layer.duration_ms))
        self._envelope = _clip_envelope(
            position_ms=layer.position_ms,
            duration_ms=duration,
            playback=layer.playback,
            loop_transition=layer.loop_transition,
            fade_ms=fade_ms,
        )

        if not want:
            if self._want_audible or (
                self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and not self._fading_out
                and self._gate > 0.001
            ):
                self._want_audible = False
                self._fading_out = True
                self._fader.fade_to(0.0, fade_ms, on_complete=self._after_fade_out)
            elif not self._fading_out and not self._fader.is_active:
                if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self.player.pause()
                self.audio.setMuted(True)
                self.audio.setVolume(0.0)
            else:
                self._apply_output_volume()
            return

        if self._fading_out or not self._want_audible:
            self._fading_out = False
            self._want_audible = True
            if self._gate <= 0.001:
                self._fader.sync_volume(0.0)
            self._fader.fade_to(1.0, fade_ms)

        self._want_audible = True

        cur = int(self.player.position())
        target = max(0, int(layer.position_ms))
        if abs(cur - target) > 280:
            self.player.setPosition(target)
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()

        self._apply_output_volume()

    def _after_fade_out(self) -> None:
        self._fading_out = False
        self._gate = 0.0
        if not self._want_audible:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            self.audio.setMuted(True)
            self.audio.setVolume(0.0)

    def stop(self) -> None:
        self._fader.cancel()
        self._want_audible = False
        self._fading_out = False
        self._gate = 0.0
        self._envelope = 1.0
        self.player.stop()
        self.audio.setMuted(True)
        self.audio.setVolume(0.0)

    def release(self) -> None:
        self.stop()
        self.player.setSource(QUrl())


class LayerAudioStore:
    """Audio for unmuted video layers on the Main scene only."""

    def __init__(
        self,
        model: MixerModel,
        *,
        output_device: Callable[[], tuple[QAudioDevice, bool]] | None = None,
        master_volume: float = 1.0,
    ):
        self.model = model
        self._output_device = output_device
        self._master_volume = max(0.0, min(1.0, float(master_volume)))
        self._players: dict[str, LayerAudioPlayer] = {}

    def _resolve_output(self) -> tuple[QAudioDevice, bool]:
        if self._output_device is None:
            from PyQt6.QtMultimedia import QMediaDevices

            return QMediaDevices.defaultAudioOutput(), False
        return self._output_device()

    def apply_output_device(self) -> None:
        device, blocked = self._resolve_output()
        for player in self._players.values():
            player.set_output_device(device, blocked=blocked)

    def set_master_volume(self, volume: float) -> None:
        self._master_volume = max(0.0, min(1.0, float(volume)))
        for player in self._players.values():
            player.set_master_volume(self._master_volume)

    def _live_layer(self, layer_id: str) -> Layer | None:
        """Decoder/tick updates the scene layer; program_look is a frozen copy."""
        return self.model.find_layer(layer_id)

    def sync(self) -> bool:
        """Update players; return True if Main currently has audible video."""
        program = self.model.program_scene()
        wanted: set[str] = set()
        audible = False
        device, blocked = self._resolve_output()
        if program is not None:
            for prog_layer in program.layers:
                if (
                    prog_layer.kind != "video"
                    or not prog_layer.visible
                    or prog_layer.file_missing
                ):
                    continue
                live = self._live_layer(prog_layer.id)
                if live is None:
                    continue
                if prog_layer.muted or not live.playing:
                    player = self._players.get(prog_layer.id)
                    if player is not None:
                        player.sync(
                            live,
                            playing=False,
                            muted=True,
                            visible=prog_layer.visible,
                        )
                        if player.is_output_active:
                            audible = True
                    continue
                wanted.add(prog_layer.id)
                player = self._players.get(prog_layer.id)
                if player is None or player.path != prog_layer.path:
                    if player is not None:
                        player.release()
                    player = LayerAudioPlayer(
                        prog_layer.path,
                        output_device=device,
                        output_blocked=blocked,
                    )
                    player.set_master_volume(self._master_volume)
                    self._players[prog_layer.id] = player
                else:
                    player.set_output_device(device, blocked=blocked)
                    player.set_master_volume(self._master_volume)
                player.sync(
                    live,
                    playing=True,
                    muted=prog_layer.muted,
                    visible=prog_layer.visible,
                )
                if player.is_output_active:
                    audible = True

        program_ids = {
            layer.id for layer in program.layers if not layer.file_missing
        } if program else set()
        for lid in list(self._players.keys()):
            if lid not in program_ids:
                self._players[lid].release()
                del self._players[lid]
            elif lid not in wanted:
                layer = self._live_layer(lid)
                prog_layer = next(
                    (pl for pl in (program.layers if program else []) if pl.id == lid),
                    None,
                )
                if layer is not None:
                    self._players[lid].sync(
                        layer,
                        playing=False,
                        muted=True,
                        visible=bool(prog_layer.visible if prog_layer else layer.visible),
                    )
                    if self._players[lid].is_output_active:
                        audible = True

        return audible

    def release_all(self) -> None:
        for player in self._players.values():
            player.release()
        self._players.clear()
