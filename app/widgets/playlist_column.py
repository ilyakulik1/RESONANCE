"""One playlist column with its own tab strip (each tab = a playlist)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import MAX_TABS_PER_COLUMN
from app.ui.icon_loader import icon_size, load_icon
from app.ui.tokens import get_token, get_token_int
from app.ui.widgets.value_stepper import ValueStepper
from app.widgets.playlist_widget import PlaylistWidget

if TYPE_CHECKING:
    from app.player import AudioPlayer
    from app.project import ProjectColumnState, ProjectPlaylistState


class _RenameTabBar(QTabBar):
    """Tab bar with double-click rename."""

    tabRenameRequested = pyqtSignal(int)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.tabAt(event.position().toPoint())
            if idx >= 0:
                self.tabRenameRequested.emit(idx)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class PlaylistColumnPanel(QFrame):
    """Column panel: tabbed playlists + shared toolbar + footer for the active tab."""

    playlistsChanged = pyqtSignal()

    def __init__(
        self,
        window: "AudioPlayer",
        column_index: int,
        *,
        alloc_playlist_num: Callable[[], int],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("playlistPanel")
        self.window_ref = window
        self.column_index = column_index
        self._alloc_playlist_num = alloc_playlist_num
        self._pages: dict[int, dict] = {}  # playlist_num -> page widgets

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("playlistColumnTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        tab_bar = _RenameTabBar()
        self.tabs.setTabBar(tab_bar)
        tab_bar.tabRenameRequested.connect(self._rename_tab)
        self.tabs.tabCloseRequested.connect(self._on_tab_close)
        self.tabs.currentChanged.connect(self._on_current_changed)

        add_btn = QToolButton()
        add_btn.setObjectName("playlistAddTab")
        add_btn.setText("+")
        add_btn.setToolTip("New playlist")
        add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_btn.clicked.connect(self.add_empty_tab)

        del_btn = QToolButton()
        del_btn.setObjectName("playlistDeleteTab")
        del_btn.setText("−")
        del_btn.setToolTip("Delete current playlist")
        del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        del_btn.clicked.connect(self.delete_current_playlist)

        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(2)
        corner_layout.addWidget(del_btn)
        corner_layout.addWidget(add_btn)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        layout.addWidget(self.tabs, 1)

        # Shared toolbar for the active tab (above the tab strip + list)
        toolbar_wrap = QFrame()
        toolbar_wrap.setObjectName("playlistToolbarWrap")
        toolbar_wrap_layout = QHBoxLayout(toolbar_wrap)
        toolbar_wrap_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_wrap_layout.addStretch()

        toolbar = QFrame()
        toolbar.setObjectName("playlistToolbar")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(2)

        def add_tool(name: str, tooltip: str, slot, *, opacity: float = 1.0):
            icon_px = get_token_int("sizes.icon", 12)
            tool_px = get_token_int("sizes.tool_button", 16)
            btn = QToolButton()
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setIcon(load_icon(window, name, icon_px, opacity=opacity))
            btn.setIconSize(icon_size(icon_px))
            btn.setFixedSize(tool_px, tool_px)
            btn.setToolTip(tooltip)
            btn.clicked.connect(slot)
            tb_layout.addWidget(btn)

        add_tool("up", "Move up", lambda: self._with_current(lambda pl: pl.move_item_up()))
        add_tool("down", "Move down", lambda: self._with_current(lambda pl: pl.move_item_down()))
        add_tool("top", "Move to top", lambda: self._with_current(lambda pl: pl.move_item_top()))
        add_tool(
            "bottom",
            "Move to bottom",
            lambda: self._with_current(lambda pl: pl.move_item_bottom()),
        )
        add_tool(
            "add",
            "Add files",
            lambda: self._with_current(window.add_to_playlist_widget),
        )
        add_tool(
            "remove",
            "Remove selected track",
            lambda: self._with_current(window.remove_from_playlist_widget),
        )
        add_tool(
            "clear",
            "Clear tracks",
            lambda: self._with_current(window.clear_playlist_widget),
        )
        add_tool(
            "to_project",
            "Move selected track into project folder",
            lambda: self._with_current(window.move_selected_track_to_project),
        )
        add_tool(
            "copy_to_project",
            "Copy all tracks into project folder",
            lambda: self._with_current(window.copy_playlist_tracks_to_project),
        )
        add_tool(
            "bpm_one",
            "Re-analyze selected track (BPM + waveform)",
            lambda: self._with_current(window.analyze_selected_file),
        )

        toolbar_wrap_layout.addWidget(toolbar)
        toolbar_wrap_layout.addStretch()
        layout.insertWidget(0, toolbar_wrap)

        # Footer for active tab
        footer = QHBoxLayout()
        self.footer_label = QLabel("Total Tracks: 0   Total Time: 0:00")
        self.footer_label.setObjectName("playlistFooter")
        footer.addWidget(self.footer_label)
        footer.addStretch()
        font_label = QLabel("FONT SIZE")
        font_label.setObjectName("fontSizeLabel")
        self.font_stepper = ValueStepper(window, 8, 30, 13)
        self.font_spin = self.font_stepper.spin_box()
        self.font_spin.valueChanged.connect(self._on_font_changed)
        footer.addWidget(font_label)
        footer.addWidget(self.font_stepper)
        layout.addLayout(footer)

    def _with_current(self, fn) -> None:
        pl = self.current_playlist()
        if pl is not None:
            fn(pl)

    def current_playlist(self) -> PlaylistWidget | None:
        page = self.tabs.currentWidget()
        if page is None:
            return None
        pl = getattr(page, "playlist_widget", None)
        return pl if isinstance(pl, PlaylistWidget) else None

    def all_playlists(self) -> list[PlaylistWidget]:
        result: list[PlaylistWidget] = []
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if page is None:
                continue
            pl = getattr(page, "playlist_widget", None)
            if isinstance(pl, PlaylistWidget):
                result.append(pl)
        return result

    def add_empty_tab(self, *, title: str | None = None) -> PlaylistWidget | None:
        if self.tabs.count() >= MAX_TABS_PER_COLUMN:
            return None
        return self._add_tab(title=title or "", tracks=None, font_size=13, select=True)

    def _add_tab(
        self,
        *,
        title: str = "",
        tracks: list | None = None,
        font_size: int = 13,
        select: bool = False,
    ) -> PlaylistWidget:
        window = self.window_ref
        playlist_num = self._alloc_playlist_num()
        default_title = title.strip() or f"Playlist {playlist_num}"

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        list_frame = QFrame()
        list_frame.setObjectName("playlistListFrame")
        list_layout = QVBoxLayout(list_frame)
        list_layout.setContentsMargins(0, 0, 0, 0)

        playlist = PlaylistWidget(default_title, playlist_num)
        playlist._column_index = self.column_index
        playlist._font_size = font_size
        playlist.itemClicked.connect(
            lambda item, n=playlist_num: window.on_playlist_item_clicked(n, item)
        )
        playlist.selectionNavigated.connect(
            lambda item, n=playlist_num: window.on_playlist_item_clicked(n, item)
        )
        playlist.itemChanged.connect(
            lambda item, n=playlist_num: window.on_playlist_item_renamed(n, item)
        )
        playlist.focused.connect(window.on_playlist_focused)
        playlist.tracksChanged.connect(
            lambda pl=playlist: window.refresh_playlist_footer(pl)
        )
        list_layout.addWidget(playlist)
        page_layout.addWidget(list_frame, 1)

        page.setProperty("playlistWidget", playlist)
        page.setProperty("listFrame", list_frame)
        page.playlist_widget = playlist
        page.list_frame = list_frame

        idx = self.tabs.addTab(page, default_title)
        self.tabs.setTabToolTip(idx, "Double-click to rename")
        self._pages[playlist_num] = {
            "page": page,
            "playlist": playlist,
            "list_frame": list_frame,
            "font_size": font_size,
        }

        family = get_token("typography.font_family_ui", "Arial")
        playlist.set_playlist_font(QFont(family, font_size))

        if tracks is not None:
            from app.playlist_io import load_playlist_from_entries

            load_playlist_from_entries(
                playlist,
                tracks,
                clear=True,
                timeline_states=window._track_timeline_state,
            )

        if select:
            self.tabs.setCurrentIndex(idx)

        self.playlistsChanged.emit()
        window.refresh_playlist_footer(playlist)
        return playlist

    def clear_tabs(self) -> None:
        self.tabs.blockSignals(True)
        while self.tabs.count() > 0:
            self.tabs.removeTab(0)
        self.tabs.blockSignals(False)
        self._pages.clear()

    def load_column_state(self, state: "ProjectColumnState") -> None:
        self.clear_tabs()
        tabs = state.tabs if state.tabs else []
        if not tabs:
            from app.project import ProjectPlaylistState

            tabs = [ProjectPlaylistState()]
        for tab in tabs:
            self._add_tab(
                title=tab.title,
                tracks=tab.tracks,
                font_size=int(tab.font_size or 13),
                select=False,
            )
        active = max(0, min(int(state.active_tab or 0), self.tabs.count() - 1))
        self.tabs.setCurrentIndex(active)
        self._sync_footer_from_current()
        self.playlistsChanged.emit()

    def collect_column_state(self) -> "ProjectColumnState":
        from app.playlist_io import playlist_to_entries
        from app.project import ProjectColumnState, ProjectPlaylistState

        window = self.window_ref
        tabs: list[ProjectPlaylistState] = []
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            pl = getattr(page, "playlist_widget", None) if page else None
            if not isinstance(pl, PlaylistWidget):
                continue
            title = self.tabs.tabText(i).strip()
            default = f"Playlist {pl.playlist_num}"
            font_size = int(getattr(pl, "_font_size", 13) or 13)
            tabs.append(
                ProjectPlaylistState(
                    title="" if title == default else title,
                    font_size=font_size,
                    tracks=playlist_to_entries(pl, window._track_timeline_state),
                )
            )
        return ProjectColumnState(
            tabs=tabs,
            active_tab=max(0, self.tabs.currentIndex()),
        )

    def delete_current_playlist(self) -> None:
        self._delete_tab(self.tabs.currentIndex(), confirm=True)

    def _on_tab_close(self, index: int) -> None:
        self._delete_tab(index, confirm=True)

    def _delete_tab(self, index: int, *, confirm: bool = True) -> None:
        if index < 0 or index >= self.tabs.count():
            return
        page = self.tabs.widget(index)
        pl = getattr(page, "playlist_widget", None) if page else None
        title = self.tabs.tabText(index).strip() or "Playlist"
        track_count = pl.count() if isinstance(pl, PlaylistWidget) else 0

        if confirm:
            if track_count > 0:
                answer = QMessageBox.question(
                    self,
                    "Delete Playlist",
                    f"Delete “{title}” and its {track_count} track(s)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            else:
                answer = QMessageBox.question(
                    self,
                    "Delete Playlist",
                    f"Delete empty playlist “{title}”?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            if answer != QMessageBox.StandardButton.Yes:
                return

        window = self.window_ref
        if isinstance(pl, PlaylistWidget):
            if window.current_playing_playlist == pl.playlist_num:
                for media_player in window._players:
                    media_player.stop()
                window._cancel_crossfade()
                window.current_playing_playlist = None
                window.current_playing_item = None
                window._current_file_path = None
            if window.last_selected_playlist == pl.playlist_num:
                window.last_selected_playlist = None
            self._pages.pop(pl.playlist_num, None)
            window._playlist_titles.pop(pl.playlist_num, None)

        if self.tabs.count() <= 1:
            # Replace the only tab with a fresh empty playlist
            self.tabs.removeTab(index)
            self.add_empty_tab()
        else:
            self.tabs.removeTab(index)

        self.playlistsChanged.emit()
        self._sync_footer_from_current()
        try:
            window.project.save_raw(window._build_project_state())
        except Exception as exc:
            print(f"Project auto-save error: {exc}")

    def _rename_tab(self, index: int) -> None:
        current = self.tabs.tabText(index)
        text, ok = QInputDialog.getText(
            self,
            "Rename Playlist",
            "Name:",
            text=current,
        )
        if not ok:
            return
        name = text.strip()
        if not name:
            return
        self.tabs.setTabText(index, name)
        page = self.tabs.widget(index)
        pl = getattr(page, "playlist_widget", None) if page else None
        if isinstance(pl, PlaylistWidget):
            default = f"Playlist {pl.playlist_num}"
            if name == default:
                self.window_ref._playlist_titles.pop(pl.playlist_num, None)
            else:
                self.window_ref._playlist_titles[pl.playlist_num] = name

    def _on_current_changed(self, _index: int) -> None:
        self._sync_footer_from_current()
        pl = self.current_playlist()
        if pl is not None:
            self.window_ref.refresh_playlist_footer(pl)

    def _sync_footer_from_current(self) -> None:
        pl = self.current_playlist()
        if pl is None:
            return
        size = int(getattr(pl, "_font_size", 13) or 13)
        self.font_spin.blockSignals(True)
        self.font_spin.setValue(size)
        self.font_spin.blockSignals(False)

    def _on_font_changed(self, size: int) -> None:
        pl = self.current_playlist()
        if pl is None:
            return
        pl._font_size = int(size)
        self.window_ref.change_playlist_font(pl.playlist_num, size)
        self.window_ref.refresh_playlist_footer(pl)

    def list_frame_for(self, playlist: PlaylistWidget) -> QFrame | None:
        info = self._pages.get(playlist.playlist_num)
        if not info:
            return None
        return info.get("list_frame")
