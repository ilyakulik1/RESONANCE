"""Project folder layout and save/load helpers.

Layout::

    MyProject/
      project.json
      media/          # audio files owned by the project
      analysis/       # BPM + waveform NPZ cache
"""

from __future__ import annotations

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
from app.playlist_io import playlist_to_entries
from app.widgets.audio_waveform import TimelineState

PROJECT_FILENAME = "project.json"
MEDIA_DIRNAME = "media"
ANALYSIS_DIRNAME = "analysis"
PROJECT_VERSION = 1


def default_project_root() -> Path:
    root = get_config_path("default_project")
    ensure_project_dirs(root)
    return root


def ensure_project_dirs(root: Path | str) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    (root_path / MEDIA_DIRNAME).mkdir(exist_ok=True)
    (root_path / ANALYSIS_DIRNAME).mkdir(exist_ok=True)
    return root_path


def project_file_path(root: Path | str) -> Path:
    return Path(root) / PROJECT_FILENAME


def media_dir(root: Path | str) -> Path:
    return Path(root) / MEDIA_DIRNAME


def analysis_dir(root: Path | str) -> Path:
    return Path(root) / ANALYSIS_DIRNAME


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
class BrowserTabState:
    title: str
    path: str


@dataclass
class ProjectPlaylistState:
    title: str = ""
    font_size: int = 13
    tracks: list[dict] = field(default_factory=list)


@dataclass
class ProjectState:
    name: str = "Project"
    playlists: list[ProjectPlaylistState] = field(default_factory=list)
    playlist_columns: int = 2
    track_timelines: dict[str, dict] = field(default_factory=dict)
    browser_tabs: list[BrowserTabState] = field(default_factory=list)


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

    def open(self, root: Path | str) -> None:
        self.root = ensure_project_dirs(root)
        self.name = self.root.name
        self._activate_analysis_cache()

    def create_new(self, root: Path | str, name: str | None = None) -> None:
        self.root = ensure_project_dirs(root)
        self.name = (name or self.root.name).strip() or "Project"
        self._activate_analysis_cache()
        # Empty project file so the folder is recognized immediately
        self.save_raw(
            ProjectState(name=self.name, playlists=[ProjectPlaylistState() for _ in range(MAX_PLAYLISTS)])
        )

    def is_path_in_project(self, file_path: str) -> bool:
        return is_under_project(file_path, self.root)

    def import_track(self, src_path: str, *, move: bool = True) -> str | None:
        return import_track_into_project(src_path, self.root, move=move)

    def collect_playlist_entries(
        self,
        playlist_widgets,
        timeline_states: dict[str, TimelineState] | None,
    ) -> list[ProjectPlaylistState]:
        """Build playlist states with absolute track paths (converted on save)."""
        states: list[ProjectPlaylistState] = []
        for widget in playlist_widgets:
            entries = playlist_to_entries(widget, timeline_states)
            states.append(ProjectPlaylistState(tracks=list(entries)))
        while len(states) < MAX_PLAYLISTS:
            states.append(ProjectPlaylistState())
        return states[:MAX_PLAYLISTS]

    def export_analysis_for_paths(self, file_paths: list[str]) -> int:
        dest = analysis_dir(self.root)
        count = 0
        for path in file_paths:
            if path and copy_cache_file_for_path(path, dest):
                count += 1
        return count

    def save_raw(self, state: ProjectState) -> None:
        ensure_project_dirs(self.root)
        payload = {
            "version": PROJECT_VERSION,
            "name": state.name or self.name,
            "playlist_columns": state.playlist_columns,
            "playlists": [
                {
                    "title": pl.title,
                    "font_size": pl.font_size,
                    "tracks": [
                        {
                            **track,
                            "path": to_project_path(str(track["path"]), self.root)
                            if track.get("path")
                            else track.get("path"),
                        }
                        for track in pl.tracks
                    ],
                }
                for pl in state.playlists
            ],
            "track_timelines": {
                to_project_path(path, self.root): data
                for path, data in state.track_timelines.items()
            },
            "browser_tabs": [
                {"title": tab.title, "path": tab.path} for tab in state.browser_tabs
            ],
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

        playlists: list[ProjectPlaylistState] = []
        raw_playlists = data.get("playlists")
        if isinstance(raw_playlists, list):
            for entry in raw_playlists[:MAX_PLAYLISTS]:
                if not isinstance(entry, dict):
                    playlists.append(ProjectPlaylistState())
                    continue
                tracks = entry.get("tracks")
                if not isinstance(tracks, list):
                    tracks = []
                resolved_tracks = []
                for track in tracks:
                    if not isinstance(track, dict):
                        continue
                    stored_path = track.get("path") or track.get("file")
                    if not stored_path:
                        continue
                    abs_path, exists = resolve_project_path(str(stored_path), self.root)
                    item = dict(track)
                    item["path"] = abs_path
                    item["_exists"] = exists
                    resolved_tracks.append(item)
                playlists.append(
                    ProjectPlaylistState(
                        title=str(entry.get("title") or "").strip(),
                        font_size=int(entry.get("font_size") or 13),
                        tracks=resolved_tracks,
                    )
                )
        while len(playlists) < MAX_PLAYLISTS:
            playlists.append(ProjectPlaylistState())

        timelines: dict[str, dict] = {}
        raw_timelines = data.get("track_timelines")
        if isinstance(raw_timelines, dict):
            for key, value in raw_timelines.items():
                if not isinstance(value, dict):
                    continue
                abs_path, _ = resolve_project_path(str(key), self.root)
                timelines[abs_path] = value

        browser_tabs: list[BrowserTabState] = []
        raw_tabs = data.get("browser_tabs")
        if isinstance(raw_tabs, list):
            for tab in raw_tabs:
                if not isinstance(tab, dict):
                    continue
                tab_path = str(tab.get("path") or "").strip()
                if not tab_path or not os.path.isdir(tab_path):
                    continue
                title = str(tab.get("title") or Path(tab_path).name).strip() or "Folder"
                browser_tabs.append(BrowserTabState(title=title, path=tab_path))

        columns = int(data.get("playlist_columns") or 2)
        columns = max(1, min(MAX_PLAYLISTS, columns))

        return ProjectState(
            name=name,
            playlists=playlists,
            playlist_columns=columns,
            track_timelines=timelines,
            browser_tabs=browser_tabs,
        )
