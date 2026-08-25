from __future__ import annotations

from pathlib import Path

from app.ui.paths import ui_resources_dir

ASSETS_DIR = ui_resources_dir() / "assets"
ICONS_DIR = ASSETS_DIR / "icons"

# Logical UI names used in load_icon() -> icon file stem(s), most specific first.
ICON_ALIASES: dict[str, tuple[str, ...]] = {
    "add": ("add", "add_file"),
    "remove": ("remove", "Busket", "basket"),
    "up": ("up", "UP"),
    "bpm_all": ("gear", "bpm_all", "analyze bpm"),
    "bpm_one": ("gear", "bpm_one", "analyze bpm (1)"),
    "back": ("back",),
    "home": ("home",),
    "refresh": ("refresh",),
    "folder": ("folder",),
    "to_project": ("folder", "to_project"),
    "copy_to_project": ("folder", "to_project", "copy_to_project"),
    "eject": ("eject",),
    "back_begin": ("back_begin", "back begin"),
    "fade_mode_1": ("fade_mode_1", "fade mode 1"),
    "fade_mode_2": ("fade_mode_2", "fade mode 2"),
}

ICON_EXTENSIONS = (".png", ".svg", "@2x.png")


def asset_path(*parts: str) -> Path:
    """Return path to an asset exported from Figma (icons, images)."""
    return ASSETS_DIR.joinpath(*parts)


def _icon_stems(name: str) -> tuple[str, ...]:
    if name in ICON_ALIASES:
        return ICON_ALIASES[name]
    return (name,)


def _match_icon_file(stem: str) -> Path | None:
    for ext in ICON_EXTENSIONS:
        path = ICONS_DIR / f"{stem}{ext}"
        if path.exists():
            return path

    if not ICONS_DIR.is_dir():
        return None

    target = stem.lower()
    for path in ICONS_DIR.iterdir():
        if path.is_file() and path.stem.lower() == target and path.suffix.lower() in (".png", ".svg"):
            return path
    return None


def icon_path(name: str) -> Path:
    """Return path to ``resources/assets/icons/{name}`` (png/svg)."""
    for stem in _icon_stems(name):
        matched = _match_icon_file(stem)
        if matched is not None:
            return matched
    return ICONS_DIR / f"{name}.png"


def icon_resource_path(name: str) -> str | None:
    """Return Qt resource URL for an icon, e.g. ``:/assets/icons/play.png``."""
    path = icon_path(name)
    if not path.exists():
        return None
    rel = path.relative_to(ui_resources_dir()).as_posix()
    return f":/{rel}"


def list_icon_names() -> list[str]:
    """Return sorted icon stems available on disk."""
    if not ICONS_DIR.is_dir():
        return []
    names: set[str] = set()
    for path in ICONS_DIR.iterdir():
        if path.is_file() and path.suffix.lower() in (".png", ".svg"):
            names.add(path.stem)
    return sorted(names)
