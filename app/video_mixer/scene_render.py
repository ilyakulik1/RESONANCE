"""Render a mixer scene to an offscreen QImage (thumbnails / crossfade)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

if TYPE_CHECKING:
    from app.video_mixer.media_store import MediaStore
    from app.video_mixer.model import MixerModel, Scene


def _frame_to_qimage(frame: np.ndarray, *, max_edge: int | None = None) -> QImage | None:
    if frame is None or frame.ndim != 3:
        return None
    h, w, c = frame.shape
    if w <= 0 or h <= 0:
        return None
    arr = np.ascontiguousarray(frame)
    if max_edge is not None and max(h, w) > max_edge:
        scale = float(max_edge) / float(max(h, w))
        nh = max(1, int(h * scale))
        nw = max(1, int(w * scale))
        ys = (np.linspace(0, h - 1, nh)).astype(np.int32)
        xs = (np.linspace(0, w - 1, nw)).astype(np.int32)
        arr = np.ascontiguousarray(arr[ys][:, xs])
        h, w = nh, nw
    if c >= 4:
        img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    else:
        img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888)
    return None if img.isNull() else img.copy()


def render_scene_image(
    model: MixerModel,
    media_store: MediaStore,
    scene: Scene | None,
    *,
    width: int | None = None,
    height: int | None = None,
    frame_max_edge: int | None = None,
) -> QImage:
    """Compose scene into a QImage at canvas (or given) resolution."""
    w = max(1, int(width or model.canvas_width))
    h = max(1, int(height or model.canvas_height))
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#000000"))
    if scene is None:
        return image

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, frame_max_edge is None)
    sx = w / float(max(1, model.canvas_width))
    sy = h / float(max(1, model.canvas_height))
    painter.scale(sx, sy)

    for layer in scene.layers:
        if not layer.visible or layer.file_missing:
            continue
        media = media_store.get(layer.id)
        if media is None or media.width <= 0:
            continue
        t = layer.transform
        painter.save()
        painter.translate(t.x, t.y)
        painter.rotate(t.rotation)
        painter.scale(t.scale_x, t.scale_y)
        painter.translate(-t.anchor_x * media.width, -t.anchor_y * media.height)
        if media.pixmap is not None and not media.pixmap.isNull():
            painter.drawPixmap(0, 0, media.pixmap)
        elif media.frame is not None:
            frame_img = _frame_to_qimage(media.frame, max_edge=frame_max_edge)
            if frame_img is not None:
                # If downsampled, stretch to layer pixel size
                if frame_max_edge is not None and (
                    frame_img.width() != media.width or frame_img.height() != media.height
                ):
                    painter.drawImage(
                        QRectF(0, 0, media.width, media.height),
                        frame_img,
                    )
                else:
                    painter.drawImage(0, 0, frame_img)
        painter.restore()
    painter.end()
    return image


def render_scene_thumb(
    model: MixerModel,
    media_store: MediaStore,
    scene: Scene | None,
    thumb_w: int = 128,
    thumb_h: int = 72,
) -> QPixmap:
    # Small target + downsampled frames — must stay cheap on the UI thread.
    image = render_scene_image(
        model,
        media_store,
        scene,
        width=max(1, thumb_w),
        height=max(1, thumb_h),
        frame_max_edge=max(thumb_w, thumb_h) * 2,
    )
    return QPixmap.fromImage(image)
