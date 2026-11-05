"""Read-only index of project folders inside a library directory."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.project import PROJECT_FILENAME, ProjectState, read_project_state

LIBRARY_TRACKS_MIME = "application/x-simpleaudioplayer-library-tracks"

_TIMELINE_KEYS = (
    "range_start_ms",
    "range_end_ms",
    "fade_in_ms",
    "fade_out_ms",
)


@dataclass
class LibraryTrack:
    name: str
    exists: bool
    search_text: str
    snapshot: dict


@dataclass
class LibraryPlaylist:
    """One playlist tab. ``title`` is the stored name (empty when it was default)."""

    title: str
    display_title: str
    font_size: int
    column: int
    tracks: list[LibraryTrack] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        return self.display_title.casefold()


@dataclass
class LibraryProject:
    name: str
    root: str
    playlists: list[LibraryPlaylist] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        return self.name.casefold()

    @property
    def track_count(self) -> int:
        return sum(len(playlist.tracks) for playlist in self.playlists)


@dataclass
class LibraryIndex:
    root: str
    projects: list[LibraryProject] = field(default_factory=list)
    skipped: int = 0

    @property
    def track_count(self) -> int:
        return sum(project.track_count for project in self.projects)


def scan_library(root: str, cancelled=None) -> LibraryIndex:
    """Find folders that contain project.json. Does not enter a project once found."""
    library = Path(root)
    index = LibraryIndex(root=str(library))
    if not library.is_dir():
        return index

    def stopped() -> bool:
        return bool(cancelled and cancelled())

    for project_root in _iter_project_roots(library, stopped):
        if stopped():
            break
        state = read_project_state(project_root)
        if state is None:
            index.skipped += 1
            continue
        index.projects.append(_project_from_state(project_root, state))

    index.projects.sort(key=lambda project: (project.name.casefold(), project.root.casefold()))
    return index


def _iter_project_roots(library: Path, stopped):
    if (library / PROJECT_FILENAME).is_file():
        yield library.resolve()
        return

    for dirpath, dirnames, filenames in os.walk(library, followlinks=False):
        if stopped():
            return
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        if PROJECT_FILENAME in filenames:
            yield Path(dirpath).resolve()
            dirnames[:] = []


def _project_from_state(root: Path, state: ProjectState) -> LibraryProject:
    root_text = str(root)
    visible_columns = max(1, int(state.playlist_columns or 1))
    playlists: list[LibraryPlaylist] = []
    for column_index, column in enumerate(state.columns, start=1):
        has_tracks = any(tab.tracks for tab in column.tabs)
        if column_index > visible_columns and not has_tracks:
            continue
        for tab_index, tab in enumerate(column.tabs, start=1):
            display = tab.title.strip() or f"Playlist {tab_index}"
            tracks = [
                _track_from_entry(state, track, root_text)
                for track in tab.tracks
                if isinstance(track, dict)
            ]
            playlists.append(
                LibraryPlaylist(
                    title=tab.title.strip(),
                    display_title=display,
                    font_size=int(tab.font_size or 13),
                    column=column_index,
                    tracks=tracks,
                )
            )
    return LibraryProject(name=state.name or root.name, root=root_text, playlists=playlists)


def _track_from_entry(state: ProjectState, track: dict, source_root: str) -> LibraryTrack:
    path = str(track.get("path") or "").strip()
    name = str(track.get("name") or "").strip()
    if path:
        display = name or os.path.basename(path)
        exists = bool(track.get("_exists"))
    else:
        display = name or "New Track"
        exists = True

    track_id = str(track.get("id") or "").strip()
    snapshot: dict = {"name": display, "source_root": source_root}
    if path:
        snapshot["path"] = path

    duration = track.get("duration_ms")
    if isinstance(duration, (int, float)):
        snapshot["duration_ms"] = int(duration)
    if track.get("bpm") is not None:
        snapshot["bpm"] = track.get("bpm")
    color = track.get("color")
    if isinstance(color, str) and color.strip():
        snapshot["color"] = color.strip()

    lufs = track.get("lufs")
    if not isinstance(lufs, (int, float)) and path:
        lufs = state.track_lufs.get(path)
    if isinstance(lufs, (int, float)):
        snapshot["lufs"] = float(lufs)

    channel = track.get("audio_channel_id")
    if isinstance(channel, str) and channel.strip():
        snapshot["audio_channel_id"] = channel.strip()

    timeline = _timeline_dict(state, track, track_id, path)
    if timeline is not None:
        snapshot["timeline"] = timeline

    eq = _lookup(state.track_eq, track_id, path)
    if isinstance(eq, dict) and (eq.get("bands") or eq.get("bypass")):
        snapshot["eq"] = copy.deepcopy(eq)

    stems = _lookup(state.track_stems, track_id, path)
    if isinstance(stems, dict) and stems.get("vocals_path") and stems.get("instrumental_path"):
        snapshot["stem_state"] = copy.deepcopy(stems)
        snapshot["stems"] = True
    elif track.get("stems"):
        snapshot["stems"] = True

    filename = os.path.basename(path) if path else ""
    search = f"{display}\n{filename}".casefold()
    return LibraryTrack(name=display, exists=exists, search_text=search, snapshot=snapshot)


def _lookup(mapping: dict, track_id: str, path: str):
    if track_id and track_id in mapping:
        return mapping[track_id]
    if path and path in mapping:
        return mapping[path]
    return None


def _timeline_dict(state: ProjectState, track: dict, track_id: str, path: str) -> dict | None:
    found = _lookup(state.track_timelines, track_id, path)
    if isinstance(found, dict):
        return {key: int(found.get(key) or 0) for key in _TIMELINE_KEYS}
    if any(key in track for key in _TIMELINE_KEYS):
        return {key: int(track.get(key) or 0) for key in _TIMELINE_KEYS}
    return None


