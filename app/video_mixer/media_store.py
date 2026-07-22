from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt6.QtGui import QImage, QPixmap

from app.video_mixer.decoder import VideoDecoder
from app.video_mixer.model import Layer, MixerModel


def load_image_rgba(path: str | Path) -> np.ndarray | None:
    """Load image as contiguous RGBA uint8 (handles QImage row padding)."""
    image = QImage(str(path))
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return None

    bytes_per_line = image.bytesPerLine()
    ptr = image.bits()
    if ptr is None:
        return None
    ptr.setsize(height * bytes_per_line)
    raw = np.frombuffer(ptr, dtype=np.uint8).reshape(height, bytes_per_line)
    rgba = np.ascontiguousarray(raw[:, : width * 4].reshape(height, width, 4))
    if np.max(rgba[:, :, 3]) == 0:
        rgba[:, :, 3] = 255
    else:
        opaque_ratio = float(np.mean(rgba[:, :, 3] > 250))
        if opaque_ratio > 0.98:
            rgba[:, :, 3] = 255
    return rgba


class LayerMedia:
    """CPU-side frame buffer for one layer (image or live video)."""

    def __init__(self, layer: Layer):
        self.layer_id = layer.id
        self.path = layer.path
        self.kind = layer.kind
        self.frame: np.ndarray | None = None
        self.pixmap: QPixmap | None = None
        self.width = 0
        self.height = 0
        self.decoder: VideoDecoder | None = None
        self._loop_decoder: VideoDecoder | None = None
        self.generation = 0
        self._loop_fade_elapsed_ms = 0
        self._loop_fade_duration_ms = 0
        self._loop_fade_active = False
        self._tick_dt_ms = 33

        if layer.kind == "image":
            pix = QPixmap(layer.path)
            if not pix.isNull():
                self.pixmap = pix
                self.width = pix.width()
                self.height = pix.height()
                self.generation = 1
                self.frame = load_image_rgba(layer.path)
            else:
                self.frame = load_image_rgba(layer.path)
                if self.frame is not None:
                    self.height, self.width = self.frame.shape[:2]
                    self.generation = 1
        else:
            self.decoder = VideoDecoder(layer.path)
            if self.decoder.error is None:
                use_native_loop = layer.playback == "loop" and layer.loop_transition != "fade"
                self.decoder.set_loop(use_native_loop)
                self.decoder.set_playing(bool(layer.playing))
                if layer.position_ms > 0:
                    self.decoder.seek(layer.position_ms)
                self.decoder.start()
                if layer.duration_ms <= 0 and self.decoder.duration_ms > 0:
                    layer.duration_ms = self.decoder.duration_ms

    def sync_from_layer(self, layer: Layer) -> None:
        if self.decoder is None:
            return
        use_native_loop = layer.playback == "loop" and layer.loop_transition != "fade"
        self.decoder.set_loop(use_native_loop)
        self.decoder.set_playing(bool(layer.playing))
        if self._loop_decoder is not None:
            self._loop_decoder.set_playing(bool(layer.playing))

    def seek(self, position_ms: int) -> None:
        if self.decoder is not None:
            self.decoder.seek(position_ms)
        self._cancel_loop_fade()

    def _cancel_loop_fade(self) -> None:
        self._loop_fade_active = False
        self._loop_fade_elapsed_ms = 0
        self._loop_fade_duration_ms = 0
        if self._loop_decoder is not None:
            self._loop_decoder.stop()
            self._loop_decoder = None

    def _start_loop_fade(self, layer: Layer) -> None:
        fade_ms = max(1, int(layer.loop_fade_ms))
        if self._loop_decoder is not None:
            return
        incoming = VideoDecoder(self.path)
        if incoming.error is not None:
            if self.decoder is not None:
                self.decoder.seek(0)
            return
        incoming.set_loop(False)
        incoming.set_playing(True)
        incoming.seek(0)
        incoming.start()
        self._loop_decoder = incoming
        self._loop_fade_active = True
        self._loop_fade_elapsed_ms = 0
        self._loop_fade_duration_ms = fade_ms

    def _finish_loop_fade(self, layer: Layer) -> int:
        """Promote incoming decoder to primary. Returns incoming position."""
        in_pos = 0
        if self._loop_decoder is None:
            self._loop_fade_active = False
            return in_pos
        _, in_pos = self._loop_decoder.get_frame()
        old = self.decoder
        self.decoder = self._loop_decoder
        self._loop_decoder = None
        self._loop_fade_active = False
        self._loop_fade_elapsed_ms = 0
        use_native_loop = layer.playback == "loop" and layer.loop_transition != "fade"
        self.decoder.set_loop(use_native_loop)
        self.decoder.set_playing(bool(layer.playing))
        if old is not None:
            old.stop()
        return in_pos

    def tick(self, layer: Layer, dt_ms: int = 33) -> bool:
        """Return True if frame content changed."""
        if self.decoder is None:
            return False
        self._tick_dt_ms = max(1, int(dt_ms))
        frame, position_ms = self.decoder.get_frame()
        if frame is None and not self._loop_fade_active:
            return False

        if layer.duration_ms <= 0 and self.decoder.duration_ms > 0:
            layer.duration_ms = self.decoder.duration_ms
        duration = max(0, layer.duration_ms or self.decoder.duration_ms)

        # Metadata duration is often longer than real EOF — snap to actual end.
        if self.decoder.at_eof and position_ms > 0:
            if duration <= 0 or position_ms < duration:
                layer.duration_ms = max(1, position_ms)
                duration = layer.duration_ms

        if layer.playback == "loop" and layer.playing:
            if layer.loop_transition == "fade":
                if not self._loop_fade_active and frame is not None:
                    fade_ms = max(1, int(layer.loop_fade_ms))
                    near_end = duration > 0 and position_ms >= max(0, duration - fade_ms)
                    if near_end or self.decoder.at_eof:
                        self._start_loop_fade(layer)
            else:
                # CUT: keep native loop alive; recover if decoder stalled at EOF.
                self.decoder.set_loop(True)
                if self.decoder.at_eof:
                    self.decoder.seek(0)
                    self.decoder.set_playing(True)
                    frame, position_ms = self.decoder.get_frame()

        self.decoder.consume_loop_restart()

        display = frame
        changed = False

        if self._loop_fade_active and self._loop_decoder is not None:
            self._loop_fade_elapsed_ms += self._tick_dt_ms
            t = min(1.0, self._loop_fade_elapsed_ms / float(max(1, self._loop_fade_duration_ms)))
            incoming, in_pos = self._loop_decoder.get_frame()
            outgoing = frame if frame is not None else self.frame
            if outgoing is not None and incoming is not None and outgoing.shape == incoming.shape:
                try:
                    a = outgoing.astype(np.float32)
                    b = incoming.astype(np.float32)
                    display = (a * (1.0 - t) + b * t).astype(np.uint8)
                    changed = True
                except Exception:
                    display = incoming if t > 0.5 else outgoing
            elif incoming is not None:
                display = incoming
                changed = True
            elif outgoing is not None:
                display = outgoing

            if t >= 1.0:
                position_ms = self._finish_loop_fade(layer)
                frame2, pos2 = self.decoder.get_frame()
                if frame2 is not None:
                    display = frame2
                    position_ms = pos2
                changed = True
        else:
            if frame is None:
                return False
            if frame is not self.frame or layer.position_ms != position_ms:
                changed = True
            display = frame

        if display is None:
            return False
        if not changed and display is self.frame:
            layer.position_ms = position_ms
            return False

        self.frame = display
        self.pixmap = None
        self.height, self.width = display.shape[:2]
        self.generation += 1
        layer.position_ms = position_ms
        return True

    def release(self) -> None:
        self._cancel_loop_fade()
        if self.decoder is not None:
            self.decoder.stop()
            self.decoder = None
        self.frame = None
        self.pixmap = None


class MediaStore:
    """Keeps decoders/frames in sync with MixerModel layers."""

    def __init__(self, model: MixerModel):
        self.model = model
        self._media: dict[str, LayerMedia] = {}
        self._preview_suspended = False
        self._all_suspended = False

    def set_render_suspended(self, *, preview: bool = False, all_media: bool = False) -> None:
        """Pause decoders when the mixer panel is collapsed."""
        self._preview_suspended = preview
        self._all_suspended = all_media
        self._apply_suspend()

    def _apply_suspend(self) -> None:
        preview = self.model.preview_scene()
        preview_ids = {layer.id for layer in preview.layers} if preview else set()
        for layer_id, media in self._media.items():
            if media.decoder is None:
                continue
            layer = self.model.find_layer(layer_id)
            if layer is None:
                continue
            if self._all_suspended:
                media.decoder.set_playing(False)
            elif self._preview_suspended and layer_id in preview_ids:
                media.decoder.set_playing(False)
            else:
                media.decoder.set_playing(bool(layer.playing))
            if media._loop_decoder is not None:
                media._loop_decoder.set_playing(
                    False if self._all_suspended else bool(layer.playing)
                )

    def _hot_layer_ids(self) -> set[str]:
        ids: set[str] = set()
        for scene in (
            self.model.main_scene(),
            self.model.preview_scene(),
            self.model.pending_main_scene(),
        ):
            if scene is None:
                continue
            for layer in scene.layers:
                if layer.file_missing:
                    continue
                ids.add(layer.id)
        return ids

    def sync(self) -> None:
        live_ids = self._hot_layer_ids()
        for scene in self.model.scenes:
            for layer in scene.layers:
                if layer.file_missing:
                    existing = self._media.pop(layer.id, None)
                    if existing is not None:
                        existing.release()
                    continue
                if layer.id not in live_ids:
                    continue
                existing = self._media.get(layer.id)
                if existing is None or existing.path != layer.path or existing.kind != layer.kind:
                    if existing is not None:
                        existing.release()
                    self._media[layer.id] = LayerMedia(layer)
                else:
                    existing.sync_from_layer(layer)

        stale = [lid for lid in self._media if lid not in live_ids]
        for lid in stale:
            self._media[lid].release()
            del self._media[lid]
        self._apply_suspend()

    def tick(self, dt_ms: int = 33) -> bool:
        changed = False
        for scene in (
            self.model.main_scene(),
            self.model.preview_scene(),
            self.model.pending_main_scene(),
        ):
            if scene is None:
                continue
            for layer in scene.layers:
                if layer.file_missing:
                    continue
                media = self._media.get(layer.id)
                if media is not None and media.tick(layer, dt_ms):
                    changed = True
        return changed

    def has_playing_video(self) -> bool:
        for scene in (
            self.model.main_scene(),
            self.model.preview_scene(),
            self.model.pending_main_scene(),
        ):
            if scene is None:
                continue
            for layer in scene.layers:
                if (
                    not layer.visible
                    or layer.file_missing
                    or layer.kind != "video"
                    or not layer.playing
                ):
                    continue
                if self._media.get(layer.id) is not None:
                    return True
        return False

    def scene_media_ready(self, scene_id: str | None) -> bool:
        """True when every visible layer in the scene has a frame/pixmap."""
        scene = self.model.scene_by_id(scene_id)
        if scene is None:
            return True
        for layer in scene.layers:
            if not layer.visible or layer.file_missing:
                continue
            media = self._media.get(layer.id)
            if media is None:
                return False
            if media.pixmap is not None and not media.pixmap.isNull():
                continue
            if media.frame is not None:
                continue
            if layer.kind == "video":
                return False
        return True

    def get(self, layer_id: str) -> LayerMedia | None:
        return self._media.get(layer_id)

    def seek_layer(self, layer_id: str, position_ms: int) -> None:
        media = self._media.get(layer_id)
        if media is not None:
            media.seek(position_ms)

    def release_all(self) -> None:
        for media in self._media.values():
            media.release()
        self._media.clear()
