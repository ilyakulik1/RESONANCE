from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle, QWidget

_QT_RESOURCES_LOADED = False
_ICON_DPR_VARIANTS = (1.0, 2.0)
_ICON_CACHE: dict[tuple, QIcon] = {}
# Cap source resolution before bbox/normalize — toolbar icons never need more.
_MAX_SOURCE_EDGE = 256


def _ensure_qt_resources() -> None:
    global _QT_RESOURCES_LOADED
    if _QT_RESOURCES_LOADED:
        return
    try:
        from app.ui.generated import resources_rc  # noqa: F401
    except ImportError:
        pass
    else:
        _QT_RESOURCES_LOADED = True


def _screen_device_pixel_ratio(widget: QWidget | None) -> float:
    if widget is not None:
        screen = widget.screen()
        if screen is not None:
            return max(1.0, screen.devicePixelRatio())
    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            return max(1.0, screen.devicePixelRatio())
    return 1.0


def _prepare_source(image: QImage) -> QImage:
    """Downscale huge assets once so bbox/normalize stay cheap."""
    if image.isNull():
        return image
    w, h = image.width(), image.height()
    edge = max(w, h)
    if edge <= _MAX_SOURCE_EDGE:
        return image
    scale = _MAX_SOURCE_EDGE / edge
    return image.scaled(
        max(1, int(round(w * scale))),
        max(1, int(round(h * scale))),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _content_bbox(image: QImage) -> tuple[int, int, int, int] | None:
    """Tight alpha bbox via raw ARGB bytes (not per-pixel QColor calls)."""
    img = image.convertToFormat(QImage.Format.Format_ARGB32)
    width = img.width()
    height = img.height()
    if width <= 0 or height <= 0:
        return None

    bits = img.constBits()
    if bits is None:
        return None
    bits.setsize(img.sizeInBytes())
    buf = bytes(bits)
    stride = img.bytesPerLine()

    min_x, min_y = width, height
    max_x, max_y = -1, -1

    for y in range(height):
        row = y * stride
        for x in range(width):
            # ARGB32 little-endian: B, G, R, A
            if buf[row + x * 4 + 3] > 16:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if max_x < min_x:
        return None
    return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1


def _normalize_image(
    image: QImage,
    physical_size: int,
    bbox: tuple[int, int, int, int] | None,
) -> QImage:
    if bbox is None:
        return image.scaled(
            physical_size,
            physical_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    x, y, bw, bh = bbox
    cropped = image.copy(x, y, bw, bh)
    scaled = cropped.scaled(
        physical_size,
        physical_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    canvas = QImage(physical_size, physical_size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.drawImage(
        (physical_size - scaled.width()) // 2,
        (physical_size - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return canvas


def _pixmap_for_dpr(
    image: QImage,
    logical_size: int,
    dpr: float,
    bbox: tuple[int, int, int, int] | None,
) -> QPixmap:
    physical_size = max(1, int(round(logical_size * dpr)))
    canvas = _normalize_image(image, physical_size, bbox)
    pixmap = QPixmap.fromImage(canvas)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _apply_opacity(image: QImage, opacity: float) -> QImage:
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity >= 0.999 or image.isNull():
        return image
    result = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(0)
    painter = QPainter(result)
    painter.setOpacity(opacity)
    painter.drawImage(0, 0, image)
    painter.end()
    return result


def _icon_from_image(
    image: QImage,
    logical_size: int,
    dpr: float,
    *,
    opacity: float = 1.0,
) -> QIcon:
    if image.isNull():
        return QIcon()

    image = _prepare_source(image)
    image = _apply_opacity(image, opacity)
    bbox = _content_bbox(image)
    icon = QIcon()
    ratios = sorted({*_ICON_DPR_VARIANTS, dpr})
    for ratio in ratios:
        icon.addPixmap(_pixmap_for_dpr(image, logical_size, ratio, bbox))
    return icon


def _icon_from_path(
    path: Path,
    logical_size: int,
    dpr: float,
    *,
    opacity: float = 1.0,
) -> QIcon:
    image = QImage(str(path))
    return _icon_from_image(image, logical_size, dpr, opacity=opacity)


def load_icon(
    widget: QWidget,
    name: str,
    size: int = 24,
    *,
    opacity: float = 1.0,
) -> QIcon:
    """Load a HiDPI-aware icon from bundled assets or fall back to standard icons."""
    from app.ui.assets import icon_path, icon_resource_path

    dpr = _screen_device_pixel_ratio(widget)
    cache_key = (name, int(size), round(float(opacity), 3), round(dpr, 2))
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    path = icon_path(name)
    if path.exists():
        icon = _icon_from_path(path, size, dpr, opacity=opacity)
        if not icon.isNull():
            _ICON_CACHE[cache_key] = icon
            return icon

    _ensure_qt_resources()
    resource_url = icon_resource_path(name)
    if resource_url:
        resource_icon = QIcon(resource_url)
        if not resource_icon.isNull():
            image = resource_icon.pixmap(
                int(round(size * dpr)),
                int(round(size * dpr)),
            ).toImage()
            if not image.isNull():
                icon = _icon_from_image(image, size, dpr, opacity=opacity)
                _ICON_CACHE[cache_key] = icon
                return icon
            _ICON_CACHE[cache_key] = resource_icon
            return resource_icon

    style = widget.style()
    mapping = {
        "play": QStyle.StandardPixmap.SP_MediaPlay,
        "pause": QStyle.StandardPixmap.SP_MediaPause,
        "stop": QStyle.StandardPixmap.SP_MediaStop,
        "next": QStyle.StandardPixmap.SP_MediaSkipForward,
        "previous": QStyle.StandardPixmap.SP_MediaSkipBackward,
        "up": QStyle.StandardPixmap.SP_ArrowUp,
        "down": QStyle.StandardPixmap.SP_ArrowDown,
        "top": QStyle.StandardPixmap.SP_ArrowUp,
        "bottom": QStyle.StandardPixmap.SP_ArrowDown,
        "back": QStyle.StandardPixmap.SP_ArrowBack,
        "home": QStyle.StandardPixmap.SP_DirHomeIcon,
        "refresh": QStyle.StandardPixmap.SP_BrowserReload,
        "folder": QStyle.StandardPixmap.SP_DirIcon,
        "eject": QStyle.StandardPixmap.SP_DialogCloseButton,
        "add": QStyle.StandardPixmap.SP_FileDialogNewFolder,
        "remove": QStyle.StandardPixmap.SP_TrashIcon,
        "import": QStyle.StandardPixmap.SP_DialogOpenButton,
        "export": QStyle.StandardPixmap.SP_DialogSaveButton,
        "clear": QStyle.StandardPixmap.SP_DialogCloseButton,
        "to_project": QStyle.StandardPixmap.SP_DirLinkIcon,
        "copy_to_project": QStyle.StandardPixmap.SP_DirLinkIcon,
        "bpm_all": QStyle.StandardPixmap.SP_BrowserReload,
        "bpm_one": QStyle.StandardPixmap.SP_FileDialogInfoView,
        "back_begin": QStyle.StandardPixmap.SP_MediaSkipBackward,
        "fade_mode_1": QStyle.StandardPixmap.SP_MediaPlay,
        "fade_mode_2": QStyle.StandardPixmap.SP_MediaPause,
    }
    std = mapping.get(name)
    if std is not None:
        icon = style.standardIcon(std)
        _ICON_CACHE[cache_key] = icon
        return icon

    return QIcon()


def icon_size(size: int = 24) -> QSize:
    return QSize(size, size)
