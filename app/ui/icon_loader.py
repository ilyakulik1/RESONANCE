from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle, QWidget

_QT_RESOURCES_LOADED = False
_ICON_DPR_VARIANTS = (1.0, 2.0)


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


def _content_bbox(image: QImage) -> tuple[int, int, int, int] | None:
    width = image.width()
    height = image.height()
    min_x, min_y = width, height
    max_x, max_y = -1, -1

    for y in range(height):
        for x in range(width):
            if image.pixelColor(x, y).alpha() > 16:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x < min_x:
        return None
    return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1


def _normalize_image(image: QImage, physical_size: int) -> QImage:
    bbox = _content_bbox(image)
    if bbox is None:
        return image

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


def _pixmap_for_dpr(image: QImage, logical_size: int, dpr: float) -> QPixmap:
    physical_size = max(1, int(round(logical_size * dpr)))
    canvas = _normalize_image(image, physical_size)
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

    image = _apply_opacity(image, opacity)
    icon = QIcon()
    ratios = sorted({*_ICON_DPR_VARIANTS, dpr})
    for ratio in ratios:
        icon.addPixmap(_pixmap_for_dpr(image, logical_size, ratio))
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

    path = icon_path(name)
    if path.exists():
        icon = _icon_from_path(path, size, dpr, opacity=opacity)
        if not icon.isNull():
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
                return _icon_from_image(image, size, dpr, opacity=opacity)
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
        "bpm_all": QStyle.StandardPixmap.SP_BrowserReload,
        "bpm_one": QStyle.StandardPixmap.SP_FileDialogInfoView,
        "back_begin": QStyle.StandardPixmap.SP_MediaSkipBackward,
        "fade_mode_1": QStyle.StandardPixmap.SP_MediaPlay,
        "fade_mode_2": QStyle.StandardPixmap.SP_MediaPause,
    }
    std = mapping.get(name)
    if std is not None:
        return style.standardIcon(std)

    return QIcon()


def icon_size(size: int = 24) -> QSize:
    return QSize(size, size)
