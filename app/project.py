"""Project folder layout and save/load helpers.

Layout::

    MyProject/
      project.json
      media/          # audio / video / image files owned by the project
      analysis/       # BPM + waveform NPZ cache
      scene_thumbs/   # video mixer scene thumbnails
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.analysis_cache import (
    copy_cache_file_for_path,
    relocate_analysis,
    set_cache_dir,
)
from app.config import get_config_path
from app.constants import MAX_PLAYLISTS
from app.playlist_io import is_track_instance_id
from app.stems import set_stems_dir

PROJECT_FILENAME = "project.json"
MEDIA_DIRNAME = "media"
ANALYSIS_DIRNAME = "analysis"
STEMS_DIRNAME = "stems"
SCENE_THUMBS_DIRNAME = "scene_thumbs"
PROJECT_VERSION = 2


def untitled_project_root() -> Path:
    """Working folder for a project that has not been saved to a user path yet."""
    return get_config_path("untitled_project")


def default_project_root() -> Path:
    root = get_config_path("default_project")
    ensure_project_dirs(root)
    return root


def ensure_project_dirs(root: Path | str) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    (root_path / MEDIA_DIRNAME).mkdir(exist_ok=True)
    (root_path / ANALYSIS_DIRNAME).mkdir(exist_ok=True)
    (root_path / STEMS_DIRNAME).mkdir(exist_ok=True)
    (root_path / SCENE_THUMBS_DIRNAME).mkdir(exist_ok=True)
    return root_path


def project_file_path(root: Path | str) -> Path:
    return Path(root) / PROJECT_FILENAME


def media_dir(root: Path | str) -> Path:
    return Path(root) / MEDIA_DIRNAME


def analysis_dir(root: Path | str) -> Path:
    return Path(root) / ANALYSIS_DIRNAME


def stems_dir(root: Path | str) -> Path:
    path = Path(root) / STEMS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def scene_thumbs_dir(root: Path | str) -> Path:
    path = Path(root) / SCENE_THUMBS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_under_project(file_path: str, root: Path | str) -> bool:
    try:
        Path(file_path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def to_project_path(file_path: str, root: Path | str) -> str:
    """Store paths under the project as relative POSIX paths."""
    abs_path = os.path.abspath(file_path)
    root_path = Path(root).resolve()
    try:
        rel = Path(abs_path).resolve().relative_to(root_path)
        return rel.as_posix()
    except ValueError:
        return abs_path


def resolve_project_path(stored: str, root: Path | str) -> tuple[str, bool]:
    """Resolve a stored path (relative or absolute) against the project root."""
    raw = (stored or "").strip()
    if not raw:
        return "", False

    root_path = Path(root).resolve()
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(candidate: Path) -> None:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    expanded = Path(os.path.expanduser(raw))
    if not expanded.is_absolute():
        add(root_path / expanded)
        add(root_path / unicodedata.normalize("NFC", str(expanded)))
        add(root_path / unicodedata.normalize("NFD", str(expanded)))
    else:
        add(expanded)
        add(Path(unicodedata.normalize("NFC", str(expanded))))
        add(Path(unicodedata.normalize("NFD", str(expanded))))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve()), True

    fallback = candidates[0] if candidates else (root_path / raw)
    try:
        return str(fallback.resolve()), False
    except OSError:
        return str(fallback), False


def unique_media_destination(src_path: str, root: Path | str) -> Path:
    dest_dir = media_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(src_path).name
    dest = dest_dir / name
    if not dest.exists():
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 1
    while True:
        candidate = dest_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def import_track_into_project(
    src_path: str,
    root: Path | str,
    *,
    move: bool = True,
) -> str | None:
    """Copy or move an audio file into project/media/. Returns the new absolute path."""
    if not src_path or not os.path.isfile(src_path):
        return None

    root_path = ensure_project_dirs(root)
    abs_src = os.path.abspath(src_path)
    if is_under_project(abs_src, root_path):
        return abs_src

    dest = unique_media_destination(abs_src, root_path)
    try:
        if move:
            try:
                shutil.move(abs_src, dest)
            except OSError:
                shutil.copy2(abs_src, dest)
                try:
                    os.remove(abs_src)
                except OSError:
                    pass
        else:
            shutil.copy2(abs_src, dest)
    except OSError as exc:
        print(f"Error importing track into project: {exc}")
        return None

    new_path = str(dest.resolve())
    relocate_analysis(abs_src, new_path)
    return new_path


@dataclass
class ProjectPlaylistState:
    """One playlist tab."""

    title: str = ""
    font_size: int = 13
    tracks: list[dict] = field(default_factory=list)


@dataclass
class ProjectColumnState:
    """One visible column with its own tabs."""

    tabs: list[ProjectPlaylistState] = field(default_factory=list)
    active_tab: int = 0


@dataclass
class ProjectState:
    name: str = "Project"
    columns: list[ProjectColumnState] = field(default_factory=list)
    playlist_columns: int = 2
    track_timelines: dict[str, dict] = field(default_factory=dict)
    track_eq: dict[str, dict] = field(default_factory=dict)
    track_gain_db: dict[str, float] = field(default_factory=dict)
    track_lufs: dict[str, float] = field(default_factory=dict)
    track_stems: dict[str, dict] = field(default_factory=dict)
    video_mixer: dict | None = None

    @property
    def playlists(self) -> list[ProjectPlaylistState]:
        """Flat view of the active tab in each column (legacy helpers)."""
        result: list[ProjectPlaylistState] = []
        for col in self.columns:
            if col.tabs:
                idx = max(0, min(col.active_tab, len(col.tabs) - 1))
                result.append(col.tabs[idx])
            else:
                result.append(ProjectPlaylistState())
        return result


def empty_project_columns() -> list[ProjectColumnState]:
    return [
        ProjectColumnState(tabs=[ProjectPlaylistState()], active_tab=0)
        for _ in range(MAX_PLAYLISTS)
    ]


def mixer_dict_for_storage(data: dict, root: Path | str) -> dict:
    """Deep-copy mixer state with layer paths relative to the project when possible."""
    payload = copy.deepcopy(data)
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return payload
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        layers = scene.get("layers")
        if not isinstance(layers, list):
            continue
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            path = layer.get("path")
            if isinstance(path, str) and path.strip():
                layer["path"] = to_project_path(path, root)
    return payload


def resolve_mixer_dict(data: dict, root: Path | str) -> dict:
    """Deep-copy mixer state with layer paths resolved against the project root."""
    payload = copy.deepcopy(data)
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        return payload
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        layers = scene.get("layers")
        if not isinstance(layers, list):
            continue
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            path = layer.get("path")
            if isinstance(path, str) and path.strip():
                abs_path, _ = resolve_project_path(path, root)
                layer["path"] = abs_path
    return payload


def _resolve_tracks(raw_tracks, root: Path) -> list[dict]:
    if not isinstance(raw_tracks, list):
        return []
    resolved: list[dict] = []
    for track in raw_tracks:
        if not isinstance(track, dict):
            continue
        stored_path = track.get("path") or track.get("file")
        if not stored_path:
            continue
        abs_path, exists = resolve_project_path(str(stored_path), root)
        item = dict(track)
        item["path"] = abs_path
        item["_exists"] = exists
        resolved.append(item)
    return resolved


def _playlist_state_from_dict(entry: dict, root: Path) -> ProjectPlaylistState:
    return ProjectPlaylistState(
        title=str(entry.get("title") or "").strip(),
        font_size=int(entry.get("font_size") or 13),
        tracks=_resolve_tracks(entry.get("tracks"), root),
    )


def _column_state_from_dict(entry: dict, root: Path) -> ProjectColumnState:
    tabs_raw = entry.get("tabs")
    tabs: list[ProjectPlaylistState] = []
    if isinstance(tabs_raw, list) and tabs_raw:
        for tab_entry in tabs_raw:
            if isinstance(tab_entry, dict):
                tabs.append(_playlist_state_from_dict(tab_entry, root))
            else:
                tabs.append(ProjectPlaylistState())
    else:
        # Legacy flat playlist shape reused as a single tab
        tabs.append(_playlist_state_from_dict(entry, root))
    active = int(entry.get("active_tab") or 0)
    active = max(0, min(active, max(0, len(tabs) - 1)))
    return ProjectColumnState(tabs=tabs, active_tab=active)


def _stem_dict_for_storage(data: dict, root: Path | str) -> dict:
    payload = dict(data)
    for key in ("vocals_path", "instrumental_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = to_project_path(value, root)
    return payload


def _stem_dict_from_storage(data: dict, root: Path | str) -> dict:
    payload = dict(data)
    for key in ("vocals_path", "instrumental_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            abs_path, _ = resolve_project_path(value, root)
            payload[key] = abs_path
    return payload


def _instance_key_for_storage(key: str, root: Path | str) -> str:
    if is_track_instance_id(key):
        return key
    return to_project_path(key, root)


def _instance_key_from_storage(key: str, root: Path | str) -> str:
    if is_track_instance_id(key):
        return key
    abs_path, _ = resolve_project_path(str(key), root)
    return abs_path


def _playlist_dict_for_storage(pl: ProjectPlaylistState, root: Path) -> dict:
    return {
        "title": pl.title,
        "font_size": pl.font_size,
        "tracks": [
            {
                **track,
                "path": to_project_path(str(track["path"]), root)
                if track.get("path")
                else track.get("path"),
            }
            for track in pl.tracks
        ],
    }


class ProjectManager:
    """Owns the active project root and persists project.json + analysis."""

    def __init__(self, root: Path | str | None = None):
        self.root = ensure_project_dirs(root or default_project_root())
        self.name = self.root.name if self.root.name != "default_project" else "Project"
        self._activate_analysis_cache()

    @property
    def project_file(self) -> Path:
        return project_file_path(self.root)

    def _activate_analysis_cache(self) -> None:
        set_cache_dir(analysis_dir(self.root))
        set_stems_dir(stems_dir(self.root))

    def open(self, root: Path | str) -> None:
        self.root = ensure_project_dirs(root)
        self.name = self.root.name
        self._activate_analysis_cache()

    def create_new(self, root: Path | str, name: str | None = None) -> None:
        self.root = ensure_project_dirs(root)
        self.name = (name or self.root.name).strip() or "Project"
        self._activate_analysis_cache()
        self.save_raw(ProjectState(name=self.name, columns=empty_project_columns()))

    def create_untitled(self) -> None:
        """Start a fresh unnamed project in the app config directory."""
        root = untitled_project_root()
        if root.exists():
            for child in root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass
        self.root = ensure_project_dirs(root)
        self.name = "Untitled"
        self._activate_analysis_cache()
        self.save_raw(ProjectState(name=self.name, columns=empty_project_columns()))

    def is_untitled(self) -> bool:
        try:
            return self.root.resolve() == untitled_project_root().resolve()
        except OSError:
            return False

    def relocate_to(self, target: Path | str, *, name: str | None = None) -> None:
        """Move project working files into ``target`` (used by Save As from Untitled)."""
        old = self.root.resolve()
        dest = ensure_project_dirs(target).resolve()
        if old == dest:
            self.name = (name or dest.name).strip() or self.name
            return
        for dirname in (MEDIA_DIRNAME, ANALYSIS_DIRNAME, STEMS_DIRNAME, SCENE_THUMBS_DIRNAME):
            src = old / dirname
            dst = dest / dirname
            if not src.exists():
                continue
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            try:
                shutil.copytree(src, dst)
            except OSError:
                ensure_project_dirs(dest)
                if src.exists():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
        self.root = dest
        self.name = (name or dest.name).strip() or "Project"
        self._activate_analysis_cache()

    def is_path_in_project(self, file_path: str) -> bool:
        return is_under_project(file_path, self.root)

    def import_track(self, src_path: str, *, move: bool = True) -> str | None:
        return import_track_into_project(src_path, self.root, move=move)

    def export_analysis_for_paths(self, file_paths: list[str]) -> int:
        dest = analysis_dir(self.root)
        count = 0
        for path in file_paths:
            if path and copy_cache_file_for_path(path, dest):
                count += 1
        return count

    def save_raw(self, state: ProjectState) -> None:
        ensure_project_dirs(self.root)
        columns = list(state.columns) if state.columns else empty_project_columns()
        while len(columns) < MAX_PLAYLISTS:
            columns.append(ProjectColumnState(tabs=[ProjectPlaylistState()], active_tab=0))
        columns = columns[:MAX_PLAYLISTS]

        payload = {
            "version": PROJECT_VERSION,
            "name": state.name or self.name,
            "playlist_columns": state.playlist_columns,
            "columns": [
                {
                    "active_tab": col.active_tab,
                    "tabs": [_playlist_dict_for_storage(tab, self.root) for tab in col.tabs]
                    or [_playlist_dict_for_storage(ProjectPlaylistState(), self.root)],
                }
                for col in columns
            ],
            "track_timelines": {
                _instance_key_for_storage(path, self.root): data
                for path, data in state.track_timelines.items()
            },
            "track_eq": {
                _instance_key_for_storage(path, self.root): data
                for path, data in state.track_eq.items()
            },
            "track_gain_db": {
                _instance_key_for_storage(path, self.root): float(value)
                for path, value in state.track_gain_db.items()
                if isinstance(value, (int, float))
            },
            "track_lufs": {
                to_project_path(path, self.root): float(value)
                for path, value in state.track_lufs.items()
                if isinstance(value, (int, float))
            },
            "track_stems": {
                _instance_key_for_storage(path, self.root): _stem_dict_for_storage(
                    data, self.root
                )
                for path, data in state.track_stems.items()
                if isinstance(data, dict)
            },
            "video_mixer": (
                mixer_dict_for_storage(state.video_mixer, self.root)
                if isinstance(state.video_mixer, dict)
                else None
            ),
        }
        self.name = payload["name"]
        with open(self.project_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def load_raw(self) -> ProjectState | None:
        path = self.project_file
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Error loading project: {exc}")
            return None

        if not isinstance(data, dict):
            return None

        name = str(data.get("name") or self.root.name).strip() or "Project"
        self.name = name
        root = self.root

        columns: list[ProjectColumnState] = []
        raw_columns = data.get("columns")
        if isinstance(raw_columns, list) and raw_columns:
            for entry in raw_columns[:MAX_PLAYLISTS]:
                if isinstance(entry, dict):
                    columns.append(_column_state_from_dict(entry, root))
                else:
                    columns.append(ProjectColumnState(tabs=[ProjectPlaylistState()]))
        else:
            # v1: flat playlists[] → one tab per column
            raw_playlists = data.get("playlists")
            if isinstance(raw_playlists, list):
                for entry in raw_playlists[:MAX_PLAYLISTS]:
                    if isinstance(entry, dict):
                        columns.append(
                            ProjectColumnState(
                                tabs=[_playlist_state_from_dict(entry, root)],
                                active_tab=0,
                            )
                        )
                    else:
                        columns.append(ProjectColumnState(tabs=[ProjectPlaylistState()]))

        while len(columns) < MAX_PLAYLISTS:
            columns.append(ProjectColumnState(tabs=[ProjectPlaylistState()], active_tab=0))
        columns = columns[:MAX_PLAYLISTS]

        timelines: dict[str, dict] = {}
        raw_timelines = data.get("track_timelines")
        if isinstance(raw_timelines, dict):
            for key, value in raw_timelines.items():
                if not isinstance(value, dict):
                    continue
                timelines[_instance_key_from_storage(str(key), root)] = value

        track_eq: dict[str, dict] = {}
        raw_eq = data.get("track_eq")
        if isinstance(raw_eq, dict):
            for key, value in raw_eq.items():
                if not isinstance(value, dict):
                    continue
                track_eq[_instance_key_from_storage(str(key), root)] = value

        track_gain_db: dict[str, float] = {}
        raw_gain = data.get("track_gain_db")
        if isinstance(raw_gain, dict):
            for key, value in raw_gain.items():
                if not isinstance(value, (int, float)):
                    continue
                track_gain_db[_instance_key_from_storage(str(key), root)] = float(value)

        track_lufs: dict[str, float] = {}
        raw_lufs = data.get("track_lufs")
        if isinstance(raw_lufs, dict):
            for key, value in raw_lufs.items():
                if not isinstance(value, (int, float)):
                    continue
                abs_path, _ = resolve_project_path(str(key), root)
                track_lufs[abs_path] = float(value)

        track_stems: dict[str, dict] = {}
        raw_stems = data.get("track_stems")
        if isinstance(raw_stems, dict):
            for key, value in raw_stems.items():
                if not isinstance(value, dict):
                    continue
                track_stems[_instance_key_from_storage(str(key), root)] = (
                    _stem_dict_from_storage(value, root)
                )

        video_mixer = data.get("video_mixer")
        if not isinstance(video_mixer, dict):
            video_mixer = None
        else:
            video_mixer = resolve_mixer_dict(video_mixer, root)

        columns_count = int(data.get("playlist_columns") or 2)
        columns_count = max(1, min(MAX_PLAYLISTS, columns_count))

        return ProjectState(
            name=name,
            columns=columns,
            playlist_columns=columns_count,
            track_timelines=timelines,
            track_eq=track_eq,
            track_gain_db=track_gain_db,
            track_lufs=track_lufs,
            track_stems=track_stems,
            video_mixer=video_mixer,
        )
