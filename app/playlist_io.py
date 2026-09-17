import json
import os
import time
import unicodedata
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem

from app.widgets.audio_waveform import TimelineState

FILE_PATH_ROLE = Qt.ItemDataRole.UserRole
DURATION_ROLE = Qt.ItemDataRole.UserRole + 1
BPM_ROLE = Qt.ItemDataRole.UserRole + 2
FILE_MISSING_ROLE = Qt.ItemDataRole.UserRole + 3
COLOR_ROLE = Qt.ItemDataRole.UserRole + 4
GAIN_DB_ROLE = Qt.ItemDataRole.UserRole + 5
LUFS_ROLE = Qt.ItemDataRole.UserRole + 6
STEMS_ROLE = Qt.ItemDataRole.UserRole + 7
TRACK_ID_ROLE = Qt.ItemDataRole.UserRole + 8
MIXER_SCENE_ROLE = Qt.ItemDataRole.UserRole + 9

TRACKS_CLIPBOARD_MIME = "application/x-simpleaudioplayer-tracks"
DEFAULT_PLACEHOLDER_NAME = "New Track"

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


def new_track_id() -> str:
    return uuid.uuid4().hex


def is_track_instance_id(key: str | None) -> bool:
    text = str(key or "").strip()
    if len(text) != 32:
        return False
    if any(sep in text for sep in "/\\:"):
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def set_item_track_id(item: QListWidgetItem, track_id: str | None) -> None:
    text = str(track_id or "").strip()
    item.setData(TRACK_ID_ROLE, text if text else None)


def get_item_track_id(item: QListWidgetItem | None) -> str | None:
    if item is None:
        return None
    value = item.data(TRACK_ID_ROLE)
    text = str(value).strip() if value is not None else ""
    return text or None


def ensure_item_track_id(item: QListWidgetItem | None, preferred: str | None = None) -> str | None:
    if item is None:
        return None
    existing = get_item_track_id(item)
    if existing:
        return existing
    preferred_id = str(preferred or "").strip()
    track_id = preferred_id if preferred_id else new_track_id()
    set_item_track_id(item, track_id)
    return track_id


def clone_playlist_item(item: QListWidgetItem) -> QListWidgetItem:
    """Deep-copy a playlist row as a new independent instance."""
    clone = item.clone()
    clone.setHidden(False)
    clone.setSelected(False)
    set_item_track_id(clone, new_track_id())
    return clone


def is_item_placeholder(item: QListWidgetItem | None) -> bool:
    if item is None:
        return False
    return get_item_file_path(item) is None


def item_to_snapshot(item: QListWidgetItem | None) -> dict | None:
    if item is None:
        return None
    file_path = get_item_file_path(item)
    if file_path:
        snapshot: dict = {
            "path": os.path.abspath(file_path),
            "name": item.text().strip() or os.path.basename(file_path),
        }
    else:
        snapshot = {
            "name": item.text().strip() or DEFAULT_PLACEHOLDER_NAME,
        }
    duration_ms = get_item_duration(item)
    if duration_ms is not None:
        snapshot["duration_ms"] = duration_ms
    bpm = get_item_bpm(item)
    if bpm is not None:
        snapshot["bpm"] = _bpm_to_entry(bpm)
    color = get_item_color(item)
    if color is not None:
        snapshot["color"] = color
    gain_db = get_item_gain_db(item)
    if gain_db is not None:
        snapshot["gain_db"] = gain_db
    lufs = get_item_lufs(item)
    if lufs is not None:
        snapshot["lufs"] = lufs
    if item_has_stems(item):
        snapshot["stems"] = True
    scene_id = get_item_mixer_scene_id(item)
    if scene_id:
        snapshot["mixer_scene_id"] = scene_id
    track_id = get_item_track_id(item)
    if track_id:
        snapshot["id"] = track_id
    return snapshot


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
    if not path:
        return False
    return not os.path.isfile(path)


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


def set_item_gain_db(item: QListWidgetItem, gain_db: float | None) -> None:
    if gain_db is None:
        item.setData(GAIN_DB_ROLE, None)
        return
    try:
        item.setData(GAIN_DB_ROLE, float(gain_db))
    except (TypeError, ValueError):
        item.setData(GAIN_DB_ROLE, None)


def get_item_gain_db(item: QListWidgetItem | None) -> float | None:
    if item is None:
        return None
    value = item.data(GAIN_DB_ROLE)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def set_item_lufs(item: QListWidgetItem, lufs: float | None) -> None:
    if lufs is None:
        item.setData(LUFS_ROLE, None)
        return
    try:
        item.setData(LUFS_ROLE, float(lufs))
    except (TypeError, ValueError):
        item.setData(LUFS_ROLE, None)


def get_item_lufs(item: QListWidgetItem | None) -> float | None:
    if item is None:
        return None
    value = item.data(LUFS_ROLE)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def set_item_mixer_scene_id(item: QListWidgetItem, scene_id: str | None) -> None:
    text = str(scene_id or "").strip()
    item.setData(MIXER_SCENE_ROLE, text if text else None)


def get_item_mixer_scene_id(item: QListWidgetItem | None) -> str | None:
    if item is None:
        return None
    value = item.data(MIXER_SCENE_ROLE)
    text = str(value).strip() if value is not None else ""
    return text or None


def set_item_has_stems(item: QListWidgetItem, has_stems: bool) -> None:
    item.setData(STEMS_ROLE, bool(has_stems))


def item_has_stems(item: QListWidgetItem | None) -> bool:
    if item is None:
        return False
    return bool(item.data(STEMS_ROLE))


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


def _stems_from_entry(entry: dict) -> bool:
    return bool(entry.get("stems") or entry.get("has_stems"))


def _parse_playlist_entry(
    entry,
) -> tuple[
    str | None,
    str | None,
    TimelineState | None,
    float | None,
    int | None,
    str | None,
    bool,
]:
    if isinstance(entry, str):
        path = entry.strip()
        return (path or None), None, None, None, None, None, False

    if not isinstance(entry, dict):
        return None, None, None, None, None, None, False

    raw_path = entry.get("path") or entry.get("file")
    if not raw_path:
        name = entry.get("name")
        display_name = str(name).strip() if name is not None else None
        if display_name == "":
            display_name = None
        return (
            None,
            display_name or DEFAULT_PLACEHOLDER_NAME,
            _timeline_from_entry(entry),
            _bpm_from_entry(entry),
            _duration_from_entry(entry),
            _color_from_entry(entry),
            _stems_from_entry(entry),
        )

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
        _stems_from_entry(entry),
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
    list[
        tuple[
            str | None,
            str | None,
            TimelineState | None,
            float | None,
            int | None,
            bool,
            str | None,
            bool,
        ]
    ],
    int,
]:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Playlist file must contain a JSON array")

    parsed: list[
        tuple[
            str | None,
            str | None,
            TimelineState | None,
            float | None,
            int | None,
            bool,
            str | None,
            bool,
        ]
    ] = []
    skipped = 0
    for entry in data:
        file_path, display_name, timeline, bpm, duration_ms, color, has_stems = (
            _parse_playlist_entry(entry)
        )
        if not file_path:
            parsed.append(
                (None, display_name, timeline, bpm, duration_ms, False, color, has_stems)
            )
            continue
        resolved_path, exists = _resolve_file_path(file_path)
        parsed.append(
            (
                resolved_path,
                display_name,
                timeline,
                bpm,
                duration_ms,
                exists,
                color,
                has_stems,
            )
        )
    return parsed, skipped


def _timeline_for_item(
    item: QListWidgetItem,
    abs_path: str,
    timeline_states: dict[str, TimelineState] | None,
) -> TimelineState | None:
    if not timeline_states:
        return None
    track_id = get_item_track_id(item)
    if track_id and track_id in timeline_states:
        return timeline_states[track_id]
    return timeline_states.get(abs_path)


def _playlist_item_to_entry(
    item: QListWidgetItem,
    timeline_states: dict[str, TimelineState] | None,
) -> dict | None:
    file_path = get_item_file_path(item)
    track_id = ensure_item_track_id(item)
    if not file_path:
        entry: dict = {
            "name": item.text().strip() or DEFAULT_PLACEHOLDER_NAME,
        }
        if track_id:
            entry["id"] = track_id
        state = _timeline_for_item(item, track_id or "", timeline_states)
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
        gain_db = get_item_gain_db(item)
        if gain_db is not None:
            entry["gain_db"] = gain_db
        if item_has_stems(item):
            entry["stems"] = True
        scene_id = get_item_mixer_scene_id(item)
        if scene_id:
            entry["mixer_scene_id"] = scene_id
        return entry

    abs_path = os.path.abspath(file_path)
    entry = {
        "path": abs_path,
        "name": item.text().strip() or os.path.basename(file_path),
    }
    if track_id:
        entry["id"] = track_id
    state = _timeline_for_item(item, abs_path, timeline_states)
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
    gain_db = get_item_gain_db(item)
    if gain_db is not None:
        entry["gain_db"] = gain_db
    if item_has_stems(item):
        entry["stems"] = True
    scene_id = get_item_mixer_scene_id(item)
    if scene_id:
        entry["mixer_scene_id"] = scene_id
    return entry


def playlist_to_entries(
    playlist_widget,
    timeline_states: dict[str, TimelineState] | None = None,
) -> list[dict]:
    entries = []
    for i in range(playlist_widget.count()):
        if i % 16 == 0:
            time.sleep(0)
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


def _gain_from_entry(entry: dict | None) -> float | None:
    if not isinstance(entry, dict) or "gain_db" not in entry:
        return None
    try:
        return float(entry["gain_db"])
    except (TypeError, ValueError):
        return None


def _lufs_from_entry(entry: dict | None) -> float | None:
    if not isinstance(entry, dict) or "lufs" not in entry:
        return None
    try:
        return float(entry["lufs"])
    except (TypeError, ValueError):
        return None


def _add_placeholder_entry(
    playlist_widget,
    display_name: str | None,
    timeline: TimelineState | None,
    timeline_states: dict[str, TimelineState] | None,
    *,
    bpm: float | None = None,
    duration_ms: int | None = None,
    color: str | None = None,
    has_stems: bool = False,
    track_id: str | None = None,
    gain_db: float | None = None,
    lufs: float | None = None,
    mixer_scene_id: str | None = None,
) -> None:
    item: QListWidgetItem | None = None
    if hasattr(playlist_widget, "add_placeholder_item"):
        item = playlist_widget.add_placeholder_item(display_name=display_name)
    else:
        item = QListWidgetItem(display_name or DEFAULT_PLACEHOLDER_NAME)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        playlist_widget.addItem(item)

    if item is None:
        return
    if track_id:
        set_item_track_id(item, track_id)
    else:
        ensure_item_track_id(item)
    if duration_ms is not None:
        set_item_duration(item, duration_ms)
    if bpm is not None:
        set_item_bpm(item, bpm)
    if color is not None:
        set_item_color(item, color)
    if gain_db is not None:
        set_item_gain_db(item, gain_db)
    if lufs is not None:
        set_item_lufs(item, lufs)
    if mixer_scene_id:
        set_item_mixer_scene_id(item, mixer_scene_id)
    if has_stems:
        set_item_has_stems(item, True)

    if timeline_states is not None and timeline is not None:
        key = get_item_track_id(item)
        if key:
            timeline_states[key] = timeline


def _add_playlist_entry(
    playlist_widget,
    file_path: str,
    display_name: str | None,
    timeline: TimelineState | None,
    timeline_states: dict[str, TimelineState] | None,
    bpm: float | None = None,
    duration_ms: int | None = None,
    color: str | None = None,
    has_stems: bool = False,
    *,
    file_exists: bool | None = None,
    track_id: str | None = None,
    gain_db: float | None = None,
    lufs: float | None = None,
    mixer_scene_id: str | None = None,
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
        if track_id:
            set_item_track_id(item, track_id)
        else:
            ensure_item_track_id(item)
        if duration_ms is not None:
            set_item_duration(item, duration_ms)
        if bpm is not None:
            set_item_bpm(item, bpm)
        if color is not None:
            set_item_color(item, color)
        if gain_db is not None:
            set_item_gain_db(item, gain_db)
        if lufs is not None:
            set_item_lufs(item, lufs)
        if mixer_scene_id:
            set_item_mixer_scene_id(item, mixer_scene_id)
        if has_stems:
            set_item_has_stems(item, True)

    if timeline_states is not None and timeline is not None and item is not None:
        key = get_item_track_id(item) or os.path.abspath(file_path)
        timeline_states[key] = timeline


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
    for (
        file_path,
        display_name,
        timeline,
        bpm,
        duration_ms,
        exists,
        color,
        has_stems,
    ) in parsed:
        if not file_path:
            _add_placeholder_entry(
                playlist_widget,
                display_name,
                timeline,
                timeline_states,
                bpm=bpm,
                duration_ms=duration_ms,
                color=color,
                has_stems=has_stems,
            )
            added += 1
            continue
        _add_playlist_entry(
            playlist_widget,
            file_path,
            display_name,
            timeline,
            timeline_states,
            bpm=bpm,
            duration_ms=duration_ms,
            color=color,
            has_stems=has_stems,
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
        file_path, display_name, timeline, bpm, duration_ms, color, has_stems = (
            _parse_playlist_entry(entry)
        )
        if not file_path:
            _add_placeholder_entry(
                playlist_widget,
                display_name,
                timeline,
                timeline_states,
                bpm=bpm,
                duration_ms=duration_ms,
                color=color,
                has_stems=has_stems,
                track_id=str(entry.get("id") or "").strip() or None,
                gain_db=_gain_from_entry(entry),
                lufs=_lufs_from_entry(entry),
                mixer_scene_id=str(entry.get("mixer_scene_id") or "").strip() or None,
            )
            added += 1
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
            has_stems=has_stems,
            file_exists=exists,
            track_id=str(entry.get("id") or "").strip() or None,
            gain_db=_gain_from_entry(entry),
            lufs=_lufs_from_entry(entry),
            mixer_scene_id=str(entry.get("mixer_scene_id") or "").strip() or None,
        )
        added += 1
        if not exists:
            missing += 1
    return added, skipped, missing
