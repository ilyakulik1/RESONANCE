"""Project library browser: playlists and tracks from every project.json in a folder."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QDrag, QKeySequence, QShortcut
from PyQt6.QtCore import QMimeData
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.project_library import (
    LIBRARY_TRACKS_MIME,
    LibraryIndex,
    LibraryPlaylist,
    LibraryProject,
    scan_library,
)
from app.ui.file_dialog import pick_existing_directory

KIND_ROLE = Qt.ItemDataRole.UserRole
PAYLOAD_ROLE = Qt.ItemDataRole.UserRole + 1
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 2


class _LibraryScanThread(QThread):
    resultReady = pyqtSignal(int, object)

    def __init__(self, root: str, generation: int, parent=None):
        super().__init__(parent)
        self._root = root
        self._generation = generation

    def run(self) -> None:
        index = scan_library(self._root, self.isInterruptionRequested)
        if not self.isInterruptionRequested():
            self.resultReady.emit(self._generation, index)


class ProjectLibraryTree(QTreeWidget):
    """Drag tracks or a whole playlist, including EQ and other per-track settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectManagerTree")
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setExpandsOnDoubleClick(True)
        self.setAnimated(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)

    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if item is None:
            return
        payload = self._payload_for(item)
        if payload is None:
            return
        mime = QMimeData()
        mime.setData(
            LIBRARY_TRACKS_MIME,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _payload_for(self, item: QTreeWidgetItem) -> dict | None:
        kind = item.data(0, KIND_ROLE)
        if kind == "playlist":
            meta = item.data(0, PAYLOAD_ROLE)
            if not isinstance(meta, dict):
                return None
            tracks = []
            for index in range(item.childCount()):
                snap = item.child(index).data(0, PAYLOAD_ROLE)
                if isinstance(snap, dict):
                    tracks.append(snap)
            return {
                "kind": "playlist",
                "title": str(meta.get("title") or ""),
                "font_size": int(meta.get("font_size") or 13),
                "tracks": tracks,
            }
        if kind != "track":
            return None
        selected = self._selected_tracks_in_order()
        if not any(entry is item for entry in selected):
            selected = [item]
        tracks = []
        for entry in selected:
            snap = entry.data(0, PAYLOAD_ROLE)
            if isinstance(snap, dict):
                tracks.append(snap)
        if not tracks:
            return None
        return {"kind": "tracks", "tracks": tracks}

    def _selected_tracks_in_order(self) -> list[QTreeWidgetItem]:
        selected = {id(entry) for entry in self.selectedItems()}
        ordered: list[QTreeWidgetItem] = []

        def walk(parent: QTreeWidgetItem) -> None:
            for index in range(parent.childCount()):
                child = parent.child(index)
                if id(child) in selected and child.data(0, KIND_ROLE) == "track":
                    ordered.append(child)
                walk(child)

        root = self.invisibleRootItem()
        for index in range(root.childCount()):
            walk(root.child(index))
        return ordered


class ProjectManagerPanel(QWidget):
    """Choose a library folder, browse its projects, and drag tracks out."""

    libraryPathChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectManagerPanel")
        self._library_path = ""
        self._generation = 0
        self._threads: list[_LibraryScanThread] = []
        self._index = LibraryIndex(root="")
        self._expansion_before_search: set[int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        path_row = QHBoxLayout()
        path_row.setSpacing(4)
        self.path_label = QLabel("No library folder")
        self.path_label.setObjectName("projectManagerPath")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_row.addWidget(self.path_label, 1)

        self.btn_choose = QToolButton()
        self.btn_choose.setObjectName("projectManagerTool")
        self.btn_choose.setText("Folder")
        self.btn_choose.setToolTip("Choose a folder. Every project.json inside it is listed.")
        self.btn_choose.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_choose.clicked.connect(self._choose_folder)
        path_row.addWidget(self.btn_choose)

        self.btn_refresh = QToolButton()
        self.btn_refresh.setObjectName("projectManagerTool")
        self.btn_refresh.setText("Refresh")
        self.btn_refresh.setToolTip("Rescan the library folder")
        self.btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self.rescan)
        path_row.addWidget(self.btn_refresh)
        layout.addLayout(path_row)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("projectManagerSearch")
        self.search_edit.setPlaceholderText("Search all projects")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.tree_frame = QFrame()
        self.tree_frame.setObjectName("projectManagerFrame")
        tree_layout = QVBoxLayout(self.tree_frame)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = ProjectLibraryTree(self.tree_frame)
        tree_layout.addWidget(self.tree)
        layout.addWidget(self.tree_frame, 1)

        self.status_label = QLabel("Choose a folder to list its projects.")
        self.status_label.setObjectName("projectManagerStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(self.focus_search)

    def library_path(self) -> str:
        return self._library_path

    def set_library_path(self, path: str, *, notify: bool = True) -> None:
        folder = os.path.abspath(path) if path else ""
        if folder and not os.path.isdir(folder):
            folder = ""
        changed = folder != self._library_path
        self._library_path = folder
        self._show_path()
        self.btn_refresh.setEnabled(bool(folder))
        if folder:
            self.rescan()
        else:
            self._cancel_scan(wait=False)
            self._index = LibraryIndex(root="")
            self.tree.clear()
            self.status_label.setText("Choose a folder to list its projects.")
        if changed and notify:
            self.libraryPathChanged.emit(folder)

    def show_projects_page(self) -> None:
        """Present for the side panel; the panel itself is the projects page."""
        self.search_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def focus_search(self) -> None:
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def rescan(self) -> None:
        if not self._library_path:
            return
        self._cancel_scan(wait=False)
        self._generation += 1
        generation = self._generation
        self.status_label.setText("Scanning…")
        self.btn_refresh.setEnabled(False)
        thread = _LibraryScanThread(self._library_path, generation, self)
        thread.resultReady.connect(self._on_scan_finished)
        thread.finished.connect(lambda t=thread: self._forget_thread(t))
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)
        thread.start()

    def _choose_folder(self) -> None:
        start = self._library_path or str(Path.home())
        chosen = pick_existing_directory(self, "Project Library", start)
        if not chosen:
            return
        self.set_library_path(chosen)

    def shutdown(self) -> None:
        self._cancel_scan(wait=True)

    def _forget_thread(self, thread: _LibraryScanThread) -> None:
        try:
            self._threads.remove(thread)
        except ValueError:
            pass

    def _cancel_scan(self, *, wait: bool) -> None:
        running = [thread for thread in self._threads if thread.isRunning()]
        for thread in running:
            thread.requestInterruption()
        if not wait:
            return
        for thread in running:
            thread.wait(5000)
        self._threads = [thread for thread in self._threads if thread.isRunning()]

    def _on_scan_finished(self, generation: int, index: object) -> None:
        if generation != self._generation or not isinstance(index, LibraryIndex):
            return
        self._index = index
        self.btn_refresh.setEnabled(True)
        self._fill_tree(index)
        self._apply_filter(self.search_edit.text())

    def _show_path(self) -> None:
        path = self._library_path
        if not path:
            self.path_label.setText("No library folder")
            self.path_label.setToolTip("")
            return
        display = path
        home = str(Path.home())
        if display.startswith(home):
            display = "~" + display[len(home):]
        self.path_label.setText(display)
        self.path_label.setToolTip(path)

    def _fill_tree(self, index: LibraryIndex) -> None:
        self._expansion_before_search = None
        self.tree.clear()
        for project in index.projects:
            self.tree.addTopLevelItem(self._project_item(project))
        projects = len(index.projects)
        tracks = index.track_count
        if projects == 0 and index.skipped == 0:
            self.status_label.setText("No projects in this folder.")
            return
        text = f"{projects} project(s) · {tracks} track(s)"
        if index.skipped:
            text += f" · {index.skipped} unreadable"
        self.status_label.setText(text)

    def _project_item(self, project: LibraryProject) -> QTreeWidgetItem:
        label = project.name
        if project.track_count:
            label = f"{project.name}  ({project.track_count})"
        item = QTreeWidgetItem([label])
        item.setData(0, KIND_ROLE, "project")
        item.setData(0, SEARCH_ROLE, project.search_text)
        item.setToolTip(0, project.root)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        for playlist in project.playlists:
            item.addChild(self._playlist_item(playlist))
        return item

    def _playlist_item(self, playlist: LibraryPlaylist) -> QTreeWidgetItem:
        label = playlist.display_title
        if playlist.tracks:
            label = f"{playlist.display_title}  ({len(playlist.tracks)})"
        item = QTreeWidgetItem([label])
        item.setData(0, KIND_ROLE, "playlist")
        item.setData(
            0,
            PAYLOAD_ROLE,
            {"title": playlist.title, "font_size": playlist.font_size},
        )
        item.setData(0, SEARCH_ROLE, playlist.search_text)
        item.setToolTip(0, f"Column {playlist.column}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
        for track in playlist.tracks:
            child = QTreeWidgetItem([track.name if track.exists else f"{track.name}  (missing)"])
            child.setData(0, KIND_ROLE, "track")
            child.setData(0, PAYLOAD_ROLE, track.snapshot)
            child.setData(0, SEARCH_ROLE, track.search_text)
            path = str(track.snapshot.get("path") or "")
            child.setToolTip(0, path or track.name)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            item.addChild(child)
        return item

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").casefold().strip()
        if not needle:
            self._unhide_all()
            self._restore_expansion()
            return
        if self._expansion_before_search is None:
            self._expansion_before_search = self._capture_expansion()
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            project = root.child(index)
            project_match = needle in str(project.data(0, SEARCH_ROLE) or "")
            any_playlist = False
            for playlist_index in range(project.childCount()):
                playlist = project.child(playlist_index)
                playlist_match = needle in str(playlist.data(0, SEARCH_ROLE) or "")
                any_track = False
                for track_index in range(playlist.childCount()):
                    track = playlist.child(track_index)
                    track_match = needle in str(track.data(0, SEARCH_ROLE) or "")
                    visible = project_match or playlist_match or track_match
                    track.setHidden(not visible)
                    any_track = any_track or visible
                playlist_visible = project_match or playlist_match or any_track
                playlist.setHidden(not playlist_visible)
                if playlist_visible:
                    playlist.setExpanded(True)
                any_playlist = any_playlist or playlist_visible
            project.setHidden(not (project_match or any_playlist))
            if project_match or any_playlist:
                project.setExpanded(True)

    def _unhide_all(self) -> None:
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            project = root.child(index)
            project.setHidden(False)
            for playlist_index in range(project.childCount()):
                playlist = project.child(playlist_index)
                playlist.setHidden(False)
                for track_index in range(playlist.childCount()):
                    playlist.child(track_index).setHidden(False)

    def _capture_expansion(self) -> set[int]:
        expanded: set[int] = set()
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            project = root.child(index)
            if project.isExpanded():
                expanded.add(id(project))
            for playlist_index in range(project.childCount()):
                playlist = project.child(playlist_index)
                if playlist.isExpanded():
                    expanded.add(id(playlist))
        return expanded

    def _restore_expansion(self) -> None:
        saved = self._expansion_before_search
        if saved is None:
            return
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            project = root.child(index)
            project.setExpanded(id(project) in saved)
            for playlist_index in range(project.childCount()):
                playlist = project.child(playlist_index)
                playlist.setExpanded(id(playlist) in saved)
        self._expansion_before_search = None
