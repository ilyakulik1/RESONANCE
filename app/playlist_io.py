import json
import os
import unicodedata

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from app.widgets.audio_waveform import TimelineState

FILE_PATH_ROLE = Qt.ItemDataRole.UserRole
DURATION_ROLE = Qt.ItemDataRole.UserRole + 1
BPM_ROLE = Qt.ItemDataRole.UserRole + 2
FILE_MISSING_ROLE = Qt.ItemDataRole.UserRole + 3
COLOR_ROLE = Qt.ItemDataRole.UserRole + 4

# Preset mark colors for playlist tracks (stored as #RRGGBB).
TRACK_COLOR_PRESETS: tuple[tuple[str, str], ...] = (
    ("Red", "#e45656"),
    ("Orange", "#e67e22"),
    ("Yellow", "#f1c40f"),
    ("Green", "#2ecc71"),
    ("Teal", "#1abc9c"),
    ("Blue", "#3897fd"),
    ("Purple", "#9b59b6"),
    ("Pink", "#e91e63"),
)


def set_item_file_path(item: QListWidgetItem, file_path: str) -> None:
    path = os.path.abspath(file_path)
    item.setData(FILE_PATH_ROLE, path)
    item.setData(Qt.ItemDataRole.ToolTipRole, path)


def get_item_file_path(item: QListWidgetItem | None) -> str | None:
    if item is None:
        return None

    for role in (FILE_PATH_ROLE, Qt.ItemDataRole.ToolTipRole):
        value = item.data(role)
        if value is not None:
            path = str(value).strip()
            if path:
                return path
    return None


def set_item_duration(item: QListWidgetItem, duration_ms: int | None) -> None:
    if duration_ms is not None and duration_ms >= 0:
        item.setData(DURATION_ROLE, int(duration_ms))
    else:
        item.setData(DURATION_ROLE, None)


def get_item_duration(item: QListWidgetItem | None) -> int | None:
    if item is None:
        return None

    value = item.data(DURATION_ROLE)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def set_item_bpm(item: QListWidgetItem, bpm: float | int | None) -> None:
    if bpm is None:
        item.setData(BPM_ROLE, None)
        return
    try:
        value = float(bpm)
    except (TypeError, ValueError):
        item.setData(BPM_ROLE, None)
        return
    if value <= 0:
        item.setData(BPM_ROLE, None)
        return
    item.setData(BPM_ROLE, value)


def set_item_file_missing(item: QListWidgetItem, missing: bool) -> None:
    item.setData(FILE_MISSING_ROLE, bool(missing))


def is_item_file_missing(item: QListWidgetItem | None) -> bool:
    if item is None:
        return False

    value = item.data(FILE_MISSING_ROLE)
    if value is not None:
        return bool(value)

    path = get_item_file_path(item)
    return bool(path) and not os.path.isfile(path)


def get_item_bpm(item: QListWidgetItem | None) -> float | None:
    if item is None:
        return None

    value = item.data(BPM_ROLE)
    if value is None:
        return None
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    if bpm <= 0:
        return None
    return bpm


def _normalize_color(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 4:
        # #RGB → #RRGGBB
        text = f"#{text[1]*2}{text[2]*2}{text[3]*2}"
    if len(text) != 7:
        return None
    try:
        int(text[1:], 16)
    except ValueError:
        return None
    return text.lower()


def set_item_color(item: QListWidgetItem, color: str | None) -> None:
    item.setData(COLOR_ROLE, _normalize_color(color))


def get_item_color(item: QListWidgetItem | None) -> str | None:
    if item is None:
        return None
    return _normalize_color(item.data(COLOR_ROLE))


def _duration_from_entry(entry: dict) -> int | None:
    if "duration_ms" not in entry:
        return None
    try:
        duration_ms = int(entry["duration_ms"])
    except (TypeError, ValueError):
        return None
    if duration_ms < 0:
        return None
    return duration_ms


def _bpm_from_entry(entry: dict) -> float | None:
    if "bpm" not in entry:
        return None
    try:
        bpm = float(entry["bpm"])
    except (TypeError, ValueError):
        return None
    if bpm <= 0:
        return None
    return bpm


def _bpm_to_entry(bpm: float) -> float | int:
    if bpm == int(bpm):
        return int(bpm)
    return round(bpm, 1)


def _timeline_from_entry(entry: dict) -> TimelineState | None:
    keys = ("range_start_ms", "range_end_ms", "fade_in_ms", "fade_out_ms")
    if not any(key in entry for key in keys):
        return None
    return TimelineState(
        range_start_ms=int(entry.get("range_start_ms", 0)),
        range_end_ms=int(entry.get("range_end_ms", 0)),
        fade_in_ms=int(entry.get("fade_in_ms", 0)),
        fade_out_ms=int(entry.get("fade_out_ms", 0)),
    )


def _timeline_to_entry(state: TimelineState) -> dict:
    return {
        "range_start_ms": state.range_start_ms,
        "range_end_ms": state.range_end_ms,
        "fade_in_ms": state.fade_in_ms,
        "fade_out_ms": state.fade_out_ms,
    }


def _color_from_entry(entry: dict) -> str | None:
    if "color" not in entry:
        return None
    return _normalize_color(entry.get("color"))


def _parse_playlist_entry(
    entry,
) -> tuple[str | None, str | None, TimelineState | None, float | None, int | None, str | None]:
    if isinstance(entry, str):
        path = entry.strip()
        return (path or None), None, None, None, None, None

    if not isinstance(entry, dict):
        return None, None, None, None, None, None

    raw_path = entry.get("path") or entry.get("file")
    if not raw_path:
        return None, None, None, None, None, None

    path = str(raw_path).strip() or None
    name = entry.get("name")
    display_name = str(name).strip() if name is not None else None
    if display_name == "":
        display_name = None
    return (
        path,
        display_name,
        _timeline_from_entry(entry),
        _bpm_from_entry(entry),
        _duration_from_entry(entry),
        _color_from_entry(entry),
    )


def _resolve_file_path(path: str) -> tuple[str, bool]:
    expanded = os.path.expanduser(path.strip())
    candidates: list[str] = []
    seen: set[str] = set()

    for candidate in (
        expanded,
        unicodedata.normalize("NFC", expanded),
        unicodedata.normalize("NFD", expanded),
    ):
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)

    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate), True

    fallback = os.path.abspath(candidates[0]) if candidates else os.path.abspath(expanded)
    return fallback, False


def _parse_playlist_file(
    path: str,
) -> tuple[
    list[tuple[str, str | None, TimelineState | None, float | None, int | None, bool, str | None]],
    int,
]:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Playlist file must contain a JSON array")

    parsed: list[
        tuple[str, str | None, TimelineState | None, float | None, int | None, bool, str | None]
    ] = []
    skipped = 0
    for entry in data:
        file_path, display_name, timeline, bpm, duration_ms, color = _parse_playlist_entry(entry)
        if not file_path:
            skipped += 1
            continue
        resolved_path, exists = _resolve_file_path(file_path)
        parsed.append(
            (resolved_path, display_name, timeline, bpm, duration_ms, exists, color)
        )
    return parsed, skipped


def _playlist_item_to_entry(
    item: QListWidgetItem,
    timeline_states: dict[str, TimelineState] | None,
) -> dict | None:
    file_path = get_item_file_path(item)
    if not file_path:
        return None

    abs_path = os.path.abspath(file_path)
    entry: dict = {
        "path": abs_path,
        "name": item.text().strip() or os.path.basename(file_path),
    }
    if timeline_states:
        state = timeline_states.get(abs_path)
        if state is not None:
            entry.update(_timeline_to_entry(state))
    duration_ms = get_item_duration(item)
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    bpm = get_item_bpm(item)
    if bpm is not None:
        entry["bpm"] = _bpm_to_entry(bpm)
    color = get_item_color(item)
    if color is not None:
        entry["color"] = color
    return entry


def playlist_to_entries(
    playlist_widget,
    timeline_states: dict[str, TimelineState] | None = None,
) -> list[dict]:
    entries = []
    for i in range(playlist_widget.count()):
        item = playlist_widget.item(i)
        if item is None:
            continue
        entry = _playlist_item_to_entry(item, timeline_states)
        if entry is not None:
            entries.append(entry)
    return entries


def playlist_to_file_list(playlist_widget) -> list[str]:
    if hasattr(playlist_widget, "all_file_paths"):
        return playlist_widget.all_file_paths()

    files = []
    for i in range(playlist_widget.count()):
        file_path = get_item_file_path(playlist_widget.item(i))
        if file_path:
            files.append(file_path)
    return files


def save_playlist_to_file(
    playlist_widget,
    path: str,
    timeline_states: dict[str, TimelineState] | None = None,
) -> int:
    entries = playlist_to_entries(playlist_widget, timeline_states)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    return len(entries)


def _add_playlist_entry(
    playlist_widget,
    file_path: str,
    display_name: str | None,
    timeline: TimelineState | None,
    timeline_states: dict[str, TimelineState] | None,
    bpm: float | None = None,
    duration_ms: int | None = None,
    color: str | None = None,
    *,
    file_exists: bool | None = None,
) -> None:
    if file_exists is None:
        file_exists = os.path.isfile(file_path)

    item: QListWidgetItem | None = None
    if hasattr(playlist_widget, "add_item_with_path"):
        item = playlist_widget.add_item_with_path(
            file_path,
            display_name=display_name,
            load_duration=duration_ms is None and file_exists,
            file_exists=file_exists,
        )
    else:
        item = QListWidgetItem(display_name or os.path.basename(file_path))
        set_item_file_path(item, file_path)
        set_item_file_missing(item, not file_exists)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        playlist_widget.addItem(item)

    if item is not None:
        if duration_ms is not None:
            set_item_duration(item, duration_ms)
        if bpm is not None:
            set_item_bpm(item, bpm)
        if color is not None:
            set_item_color(item, color)

    if timeline_states is not None and timeline is not None:
        timeline_states[os.path.abspath(file_path)] = timeline


def load_playlist_from_file(
    playlist_widget,
    path: str,
    *,
    clear: bool = True,
    timeline_states: dict[str, TimelineState] | None = None,
) -> tuple[int, int, int]:
    parsed, skipped = _parse_playlist_file(path)
    if clear and not parsed:
        return 0, skipped, 0

    if clear:
        playlist_widget.clear()

    added = 0
    missing = 0
    for file_path, display_name, timeline, bpm, duration_ms, exists, color in parsed:
        _add_playlist_entry(
            playlist_widget,
            file_path,
            display_name,
            timeline,
            timeline_states,
            bpm=bpm,
            duration_ms=duration_ms,
            color=color,
            file_exists=exists,
        )
        added += 1
        if not exists:
            missing += 1
    return added, skipped, missing


def load_playlist_from_entries(
    playlist_widget,
    entries: list[dict],
    *,
    clear: bool = True,
    timeline_states: dict[str, TimelineState] | None = None,
) -> tuple[int, int, int]:
    """Load tracks from already-resolved entry dicts (e.g. project.json)."""
    if clear:
        playlist_widget.clear()

    added = 0
    skipped = 0
    missing = 0
    for entry in entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        file_path, display_name, timeline, bpm, duration_ms, color = _parse_playlist_entry(entry)
        if not file_path:
            skipped += 1
            continue
        exists = entry.get("_exists")
        if exists is None:
            resolved, exists = _resolve_file_path(file_path)
            file_path = resolved
        else:
            exists = bool(exists)
        _add_playlist_entry(
            playlist_widget,
            file_path,
            display_name,
            timeline,
            timeline_states,
            bpm=bpm,
            duration_ms=duration_ms,
            color=color,
            file_exists=exists,
        )
        added += 1
        if not exists:
            missing += 1
    return added, skipped, missing
