"""Per-layer video audio via QMediaPlayer, synced to decoder position."""

from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from app.video_mixer.model import DEFAULT_LOOP_FADE_MS, Layer, MixerModel
from app.volume_fader import VolumeFader

DEFAULT_AUDIO_FADE_MS = DEFAULT_LOOP_FADE_MS


def _clip_envelope(
    *,
    position_ms: int,
    duration_ms: int,
    playback: str,
    loop_transition: str,
    fade_ms: int,
) -> float:
    """0..1 gain from clip start/end fades."""
    fade = max(0, int(fade_ms))
    if fade <= 0 or duration_ms <= 0:
        return 1.0
    pos = max(0, int(position_ms))
    dur = max(1, int(duration_ms))
    fade = min(fade, max(1, dur // 2))

    gain = 1.0
    if pos < fade:
        gain = min(gain, pos / float(fade))

    # End fade: STOP always; LOOP+FADE near wrap; LOOP+CUT stays full until hard wrap.
    if playback == "stop" or (playback == "loop" and loop_transition == "fade"):
        remaining = dur - pos
        if remaining < fade:
            gain = min(gain, max(0.0, remaining / float(fade)))
    return max(0.0, min(1.0, gain))


class LayerAudioPlayer:
    def __init__(self, path: str):
        self.path = path
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setSource(QUrl.fromLocalFile(path))
        self._gate = 0.0
        self._envelope = 1.0
        self._want_audible = False
        self._fading_out = False
        self._fader = VolumeFader(self._apply_gate)
        self.audio.setMuted(True)
        self.audio.setVolume(0.0)

    @property
    def is_output_active(self) -> bool:
        return self._want_audible or self._fading_out or self._gate > 0.001

    def _apply_gate(self, volume: float) -> None:
        self._gate = max(0.0, min(1.0, float(volume)))
        self._apply_output_volume()

    def _apply_output_volume(self) -> None:
        level = max(0.0, min(1.0, self._gate * self._envelope))
        self.audio.setVolume(level)
        if level <= 0.0001 and not self._fader.is_active and not self._want_audible:
            self.audio.setMuted(True)
        else:
            self.audio.setMuted(False)

    def _fade_ms(self, layer: Layer) -> int:
        if layer.playback == "loop" and layer.loop_transition == "fade":
            return max(1, int(layer.loop_fade_ms) or DEFAULT_AUDIO_FADE_MS)
        return DEFAULT_AUDIO_FADE_MS

    def sync(self, layer: Layer, *, playing: bool, muted: bool) -> None:
        self._fader.advance()

        want = bool(playing) and not bool(muted) and layer.visible and not layer.file_missing
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

    def __init__(self, model: MixerModel):
        self.model = model
        self._players: dict[str, LayerAudioPlayer] = {}

    def sync(self) -> bool:
        """Update players; return True if Main currently has audible video."""
        main = self.model.main_scene()
        wanted: set[str] = set()
        audible = False
        if main is not None:
            for layer in main.layers:
                if layer.kind != "video" or not layer.visible or layer.file_missing:
                    continue
                if layer.muted or not layer.playing:
                    player = self._players.get(layer.id)
                    if player is not None:
                        player.sync(layer, playing=False, muted=True)
                        if player.is_output_active:
                            audible = True
                    continue
                wanted.add(layer.id)
                audible = True
                player = self._players.get(layer.id)
                if player is None or player.path != layer.path:
                    if player is not None:
                        player.release()
                    player = LayerAudioPlayer(layer.path)
                    self._players[layer.id] = player
                player.sync(layer, playing=True, muted=False)

        main_ids = {
            layer.id for layer in main.layers if not layer.file_missing
        } if main else set()
        for lid in list(self._players.keys()):
            if lid not in main_ids:
                self._players[lid].release()
                del self._players[lid]
            elif lid not in wanted:
                layer = self.model.find_layer(lid)
                if layer is not None:
                    self._players[lid].sync(layer, playing=False, muted=True)
                    if self._players[lid].is_output_active:
                        audible = True

        return audible

    def release_all(self) -> None:
        for player in self._players.values():
            player.release()
        self._players.clear()
