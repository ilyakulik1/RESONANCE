from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg"}


def is_image_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def is_video_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_media_file(path: str | Path) -> bool:
    return is_image_file(path) or is_video_file(path)


def media_kind(path: str | Path) -> str | None:
    if is_video_file(path):
        return "video"
    if is_image_file(path):
        return "image"
    return None
