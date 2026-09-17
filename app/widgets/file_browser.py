"""Left-side file browser with project tab and user browsing tabs."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPoint, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDrag, QFocusEvent, QFont, QKeyEvent, QKeySequence, QMouseEvent, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileIconProvider,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.audio_utils import is_audio_file
from app.ui.file_dialog import pick_existing_directory
from app.ui.icon_loader import icon_size, load_icon
from app.ui.tokens import get_token, get_token_int
from app.ui.widgets.section_header import SectionHeader
from app.widgets.audio_waveform import AudioWaveform

ENTRY_PATH_ROLE = Qt.ItemDataRole.UserRole
ENTRY_IS_DIR_ROLE = Qt.ItemDataRole.UserRole + 1

SORT_NAME = "name"
SORT_DATE = "date"


class FileBrowserList(QListWidget):
    """Folder listing with drag-out of audio files (and folders as URLs)."""

    navigated = pyqtSignal(str)  # directory path
    pathActivated = pyqtSignal(str)  # file or dir double-clicked
    audioSelected = pyqtSignal(str)  # audio file selected / activated
    focused = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self._root_limit: str | None = None
        self._drag_start_pos: QPoint | None = None
        self._icon_provider = QFileIconProvider()
        self._folder_icon = load_icon(self, "folder", 13)
        self._file_icon = self._icon_provider.icon(QFileIconProvider.IconType.File)
        self._sort_mode = SORT_NAME
        self._filter_text = ""
        self.setObjectName("fileBrowserList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setIconSize(icon_size(13))
        self.setFont(QFont(get_token("typography.font_family_ui", "Arial"), 13))
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.itemClicked.connect(self._on_item_clicked)

    def set_root_limit(self, path: str | None) -> None:
        """If set, navigation cannot leave this directory tree."""
        self._root_limit = os.path.abspath(path) if path else None

    def current_path(self) -> str:
        return self._current_path

    def navigate_to(self, path: str) -> bool:
        target = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(target):
            return False
        if self._root_limit is not None:
            try:
                Path(target).resolve().relative_to(Path(self._root_limit).resolve())
            except ValueError:
                target = self._root_limit
        self._current_path = target
        self._reload()
        self.navigated.emit(self._current_path)
        return True

    def go_up(self) -> None:
        if not self._current_path:
            return
        parent = os.path.dirname(self._current_path.rstrip(os.sep))
        if not parent or parent == self._current_path:
            return
        if self._root_limit is not None:
            try:
                Path(parent).resolve().relative_to(Path(self._root_limit).resolve())
            except ValueError:
                return
        self.navigate_to(parent)

    def enter_selected(self) -> bool:
        """Enter the currently selected folder. Returns True if handled."""
        item = self.currentItem()
        if item is None or not item.data(ENTRY_IS_DIR_ROLE):
            return False
        path = item.data(ENTRY_PATH_ROLE)
        if not path:
            return False
        return self.navigate_to(str(path))

    def refresh(self) -> None:
        if self._current_path:
            self._reload()

    def sort_mode(self) -> str:
        return self._sort_mode

    def set_sort_mode(self, mode: str) -> None:
        if mode not in (SORT_NAME, SORT_DATE) or mode == self._sort_mode:
            return
        self._sort_mode = mode
        self._reload()

    def filter_text(self) -> str:
        return self._filter_text

    def set_filter_text(self, text: str) -> None:
        normalized = text.strip().lower()
        if normalized == self._filter_text:
            return
        self._filter_text = normalized
        self._reload()

    def _entry_sort_key(self, entry: tuple[str, str, float]):
        name, _full, mtime = entry
        if self._sort_mode == SORT_DATE:
            # Newest first within each group (dirs / files).
            return (-mtime, name.lower())
        return name.lower()

    def _reload(self) -> None:
        self.clear()
        path = self._current_path
        if not path or not os.path.isdir(path):
            return

        dirs: list[tuple[str, str, float]] = []
        files: list[tuple[str, str, float]] = []
        needle = self._filter_text
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    if needle and needle not in name.lower():
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        mtime = entry.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    full = entry.path
                    if is_dir:
                        dirs.append((name, full, mtime))
                    else:
                        files.append((name, full, mtime))
        except OSError:
            return

        dirs.sort(key=self._entry_sort_key)
        files.sort(key=self._entry_sort_key)

        for name, full, _mtime in dirs:
            self._add_entry(full, name, is_dir=True)
        for name, full, _mtime in files:
            self._add_entry(full, name, is_dir=False)

    def _add_entry(self, full_path: str, name: str, *, is_dir: bool) -> None:
        item = QListWidgetItem(name)
        item.setData(ENTRY_PATH_ROLE, full_path)
        item.setData(ENTRY_IS_DIR_ROLE, is_dir)
        item.setIcon(self._folder_icon if is_dir else self._file_icon)
        item.setToolTip(full_path)
        self.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(ENTRY_PATH_ROLE)
        if not path or item.data(ENTRY_IS_DIR_ROLE):
            return
        if is_audio_file(str(path)):
            self.audioSelected.emit(str(path))
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(ENTRY_PATH_ROLE)
        if not path:
            return
        if item.data(ENTRY_IS_DIR_ROLE):
            self.navigate_to(str(path))
        else:
            self.pathActivated.emit(str(path))
            if is_audio_file(str(path)):
                self.audioSelected.emit(str(path))
                self.setFocus(Qt.FocusReason.OtherFocusReason)

    def focusInEvent(self, event: QFocusEvent):
        super().focusInEvent(event)
        self.focused.emit()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.currentItem()
            if item is not None and item.data(ENTRY_IS_DIR_ROLE):
                if self.enter_selected():
                    event.accept()
                    return
            elif item is not None:
                path = item.data(ENTRY_PATH_ROLE)
                if path and is_audio_file(str(path)):
                    self.audioSelected.emit(str(path))
                    self.setFocus(Qt.FocusReason.OtherFocusReason)
                    event.accept()
                    return
        if key == Qt.Key.Key_Backspace:
            self.go_up()
            event.accept()
            return
        # Keep list focused when moving selection across audio files
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row_before = self.currentRow()
            super().keyPressEvent(event)
            if self.currentRow() != row_before:
                item = self.currentItem()
                if item is not None:
                    path = item.data(ENTRY_PATH_ROLE)
                    if path and not item.data(ENTRY_IS_DIR_ROLE) and is_audio_file(str(path)):
                        self.audioSelected.emit(str(path))
                        self.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < 8:
            return super().mouseMoveEvent(event)
        self._start_drag()
        self._drag_start_pos = None

    def _start_drag(self) -> None:
        urls: list[QUrl] = []
        for item in self.selectedItems():
            path = item.data(ENTRY_PATH_ROLE)
            if not path:
                continue
            if item.data(ENTRY_IS_DIR_ROLE):
                continue
            if is_audio_file(str(path)):
                urls.append(QUrl.fromLocalFile(str(path)))
        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class FileBrowserTabPage(QWidget):
    """One browser tab: toolbar + folder list."""

    pathChanged = pyqtSignal(str)
    audioSelected = pyqtSignal(str)

    def __init__(
        self,
        initial_path: str,
        *,
        root_limit: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._root_limit = os.path.abspath(root_limit) if root_limit else None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar = QFrame()
        toolbar.setObjectName("fileBrowserToolbar")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(2)

        icon_px = get_token_int("sizes.icon", 12)
        tool_px = get_token_int("sizes.tool_button", 16)

        def tool(name: str, tip: str, slot) -> QToolButton:
            btn = QToolButton()
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setIcon(load_icon(self, name, icon_px))
            btn.setIconSize(icon_size(icon_px))
            btn.setFixedSize(tool_px, tool_px)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            tb.addWidget(btn)
            return btn

        self.btn_up = tool("back", "Go up", self._go_up)
        self.btn_root = tool("home", "Go to root", self._go_root)
        self.btn_refresh = tool("refresh", "Refresh", self._refresh)
        self.btn_open = tool("folder", "Open folder…", self._choose_folder)

        self.path_label = QLabel()
        self.path_label.setObjectName("fileBrowserPath")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.path_label.setWordWrap(False)
        tb.addWidget(self.path_label, 1)
        layout.addWidget(toolbar)

        filter_row = QFrame()
        filter_row.setObjectName("fileBrowserFilterRow")
        fr = QHBoxLayout(filter_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.setSpacing(4)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("fileBrowserSearch")
        self.search_edit.setPlaceholderText("Search…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search_edit.textChanged.connect(self._on_search_changed)
        fr.addWidget(self.search_edit, 1)

        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("fileBrowserSortCombo")
        self.sort_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sort_combo.setToolTip("Sort folder contents")
        self.sort_combo.addItem("Name", SORT_NAME)
        self.sort_combo.addItem("Date", SORT_DATE)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        fr.addWidget(self.sort_combo)
        layout.addWidget(filter_row)

        self.list_frame = QFrame()
        self.list_frame.setObjectName("fileBrowserListFrame")
        list_layout = QVBoxLayout(self.list_frame)
        list_layout.setContentsMargins(0, 0, 0, 0)

        self.list = FileBrowserList(self.list_frame)
        if self._root_limit:
            self.list.set_root_limit(self._root_limit)
            self.btn_open.setEnabled(False)
            self.btn_open.setVisible(False)
        self.list.navigated.connect(self._on_navigated)
        self.list.audioSelected.connect(self.audioSelected)
        list_layout.addWidget(self.list)
        layout.addWidget(self.list_frame, 1)

        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(self.focus_search)

        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search_edit)
        escape_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        escape_shortcut.activated.connect(self._clear_or_blur_search)

        start = initial_path
        if self._root_limit and (
            not start or not os.path.isdir(start) or not self._is_within_limit(start)
        ):
            start = self._root_limit
        if not start or not os.path.isdir(start):
            start = str(Path.home())
        self.list.navigate_to(start)

    def _is_within_limit(self, path: str) -> bool:
        if not self._root_limit:
            return True
        try:
            Path(path).resolve().relative_to(Path(self._root_limit).resolve())
            return True
        except ValueError:
            return False

    def current_path(self) -> str:
        return self.list.current_path()

    def set_root_limit(self, path: str | None, *, navigate_if_outside: bool = True) -> None:
        self._root_limit = os.path.abspath(path) if path else None
        self.list.set_root_limit(self._root_limit)
        locked = self._root_limit is not None
        self.btn_open.setEnabled(not locked)
        self.btn_open.setVisible(not locked)
        if (
            navigate_if_outside
            and locked
            and not self._is_within_limit(self.current_path())
        ):
            self.list.navigate_to(self._root_limit)

    def navigate_to(self, path: str) -> None:
        self.list.navigate_to(path)

    def refresh(self) -> None:
        self.list.refresh()

    def focus_search(self) -> None:
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def _go_up(self) -> None:
        self.list.go_up()

    def _go_root(self) -> None:
        if self._root_limit:
            self.list.navigate_to(self._root_limit)
        else:
            self.list.navigate_to(str(Path.home()))

    def _refresh(self) -> None:
        self.list.refresh()

    def _choose_folder(self) -> None:
        start = self.current_path() or str(Path.home())
        chosen = pick_existing_directory(self, "Open Folder", start)
        if chosen:
            self.list.navigate_to(chosen)

    def _on_search_changed(self, text: str) -> None:
        self.list.set_filter_text(text)

    def _on_sort_changed(self, _index: int) -> None:
        mode = self.sort_combo.currentData()
        if isinstance(mode, str):
            self.list.set_sort_mode(mode)

    def _clear_or_blur_search(self) -> None:
        if self.search_edit.text():
            self.search_edit.clear()
        else:
            self.list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_navigated(self, path: str) -> None:
        display = path
        home = str(Path.home())
        if display.startswith(home):
            display = "~" + display[len(home) :]
        self.path_label.setText(display)
        self.path_label.setToolTip(path)
        self.pathChanged.emit(path)


class FileBrowserPanel(QWidget):
    """Tabbed file browser: fixed Project tab + user folders + local preview."""

    PROJECT_TAB_ID = "project"
    audioSelected = pyqtSignal(str)
    collapseRequested = pyqtSignal()
    ejectRequested = pyqtSignal()

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.setObjectName("fileBrowserPanel")
        self.setMinimumWidth(200)
        self._project_root = os.path.abspath(project_root)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        header_row.addWidget(SectionHeader("FILES"), 1)
        self.btn_collapse = QToolButton()
        self.btn_collapse.setObjectName("fileBrowserCollapseBtn")
        self.btn_collapse.setText("«")
        self.btn_collapse.setToolTip("Collapse file browser")
        self.btn_collapse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_collapse.clicked.connect(self.collapseRequested.emit)
        header_row.addWidget(self.btn_collapse)
        layout.addLayout(header_row)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("fileBrowserTabs")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tabs.tabBar().tabMoved.connect(self._on_tab_moved)

        # Corner "+" button to add a browsing tab
        add_btn = QToolButton(self.tabs)
        add_btn.setObjectName("fileBrowserAddTab")
        add_btn.setText("+")
        add_btn.setToolTip("New folder tab")
        add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_btn.clicked.connect(self.add_user_tab)
        self.tabs.setCornerWidget(add_btn, Qt.Corner.TopRightCorner)

        layout.addWidget(self.tabs, 1)

        self._project_page = FileBrowserTabPage(
            self._project_root,
            root_limit=self._project_root,
            parent=self.tabs,
        )
        self._project_page.audioSelected.connect(self.audioSelected)
        self.tabs.addTab(self._project_page, "Project")
        # Project tab is never closable
        self.tabs.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.RightSide, None
        )
        self.tabs.tabBar().setTabButton(
            0, QTabBar.ButtonPosition.LeftSide, None
        )

        layout.addWidget(self._build_preview_panel())
        self.btn_browser_eject.clicked.connect(self._on_eject_clicked)

        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.add_user_tab)

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_app_focus_changed)

    def _on_eject_clicked(self) -> None:
        """Unload the current browser preview track."""
        self.ejectRequested.emit()

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("fileBrowserPreview")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(SectionHeader("BROWSER PREVIEW"))

        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_label = QLabel("OUTPUT")
        device_label.setObjectName("sectionLabel")
        self.browser_output_combo = QComboBox()
        self.browser_output_combo.setObjectName("browserOutputCombo")
        self.browser_output_combo.setToolTip("Audio output device for browser preview")
        device_row.addWidget(device_label)
        device_row.addWidget(self.browser_output_combo, 1)
        layout.addLayout(device_row)

        self.browser_track_info = QLabel("No track selected")
        self.browser_track_info.setObjectName("browserTrackInfo")
        self.browser_track_info.setWordWrap(True)
        layout.addWidget(self.browser_track_info)

        wave_row = QHBoxLayout()
        wave_row.setSpacing(6)

        preview_height = get_token_int("sizes.preview_waveform_height", 72)
        self.browser_waveform = AudioWaveform(
            bar_area_height=preview_height,
            simple=True,
        )
        self.browser_waveform.setObjectName("browserWaveform")

        tool_px = get_token_int("sizes.tool_button", 18)
        icon_px = get_token_int("sizes.icon", 14)
        btn_h = tool_px
        btn_w = tool_px * 2

        wave_tools = QGridLayout()
        wave_tools.setSpacing(4)
        wave_tools.setContentsMargins(0, 0, 0, 0)

        def make_btn(widget: QPushButton) -> QPushButton:
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            widget.setAutoDefault(False)
            widget.setDefault(False)
            return widget

        self.btn_browser_play = make_btn(QPushButton())
        self.btn_browser_play.setObjectName("waveformToolButton")
        self.btn_browser_play.setToolTip("Browser preview play")
        self.btn_browser_play.setIcon(load_icon(self, "play", icon_px))
        self.btn_browser_play.setIconSize(icon_size(icon_px))
        self.btn_browser_play.setFixedSize(btn_w, btn_h)

        self.btn_browser_pause = make_btn(QPushButton())
        self.btn_browser_pause.setObjectName("waveformToolButton")
        self.btn_browser_pause.setToolTip("Browser preview pause")
        self.btn_browser_pause.setIcon(load_icon(self, "pause", icon_px))
        self.btn_browser_pause.setIconSize(icon_size(icon_px))
        self.btn_browser_pause.setFixedSize(btn_w, btn_h)

        self.btn_browser_stop = make_btn(QPushButton())
        self.btn_browser_stop.setObjectName("waveformToolButton")
        self.btn_browser_stop.setToolTip("Browser preview stop")
        self.btn_browser_stop.setIcon(load_icon(self, "stop", icon_px))
        self.btn_browser_stop.setIconSize(icon_size(icon_px))
        self.btn_browser_stop.setFixedSize(btn_w, btn_h)

        self.btn_browser_autoplay = make_btn(QPushButton("AUTO"))
        self.btn_browser_autoplay.setObjectName("previewAutoplayButton")
        self.btn_browser_autoplay.setCheckable(True)
        self.btn_browser_autoplay.setChecked(False)
        self.btn_browser_autoplay.setToolTip("Auto-play when selecting a file in the browser")
        self.btn_browser_autoplay.setFixedSize(btn_w, btn_h)

        self.btn_browser_rewind_start = make_btn(QPushButton())
        self.btn_browser_rewind_start.setObjectName("waveformToolButton")
        self.btn_browser_rewind_start.setToolTip("Rewind to start")
        self.btn_browser_rewind_start.setIcon(load_icon(self, "back_begin", icon_px))
        self.btn_browser_rewind_start.setIconSize(icon_size(icon_px))
        self.btn_browser_rewind_start.setFixedSize(btn_w, btn_h)

        self.btn_browser_eject = make_btn(QPushButton())
        self.btn_browser_eject.setObjectName("waveformToolButton")
        self.btn_browser_eject.setToolTip("Eject / clear browser preview")
        self.btn_browser_eject.setIcon(load_icon(self, "eject", icon_px))
        self.btn_browser_eject.setIconSize(icon_size(icon_px))
        self.btn_browser_eject.setFixedSize(btn_w, btn_h)

        wave_tools.addWidget(self.btn_browser_play, 0, 0)
        wave_tools.addWidget(self.btn_browser_pause, 0, 1)
        wave_tools.addWidget(self.btn_browser_stop, 1, 0)
        wave_tools.addWidget(self.btn_browser_autoplay, 1, 1)
        wave_tools.addWidget(self.btn_browser_rewind_start, 2, 0)
        wave_tools.addWidget(self.btn_browser_eject, 2, 1)

        wave_row.addWidget(self.browser_waveform, 1)
        wave_row.addLayout(wave_tools)
        layout.addLayout(wave_row)

        time_row = QHBoxLayout()
        self.browser_timeline_start_label = QLabel("0:00")
        self.browser_timeline_start_label.setObjectName("timelineStart")
        self.browser_timeline_end_label = QLabel("0:00")
        self.browser_timeline_end_label.setObjectName("timelineEnd")
        self.browser_timeline_end_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        time_row.addWidget(self.browser_timeline_start_label)
        time_row.addStretch()
        time_row.addWidget(self.browser_timeline_end_label)
        layout.addLayout(time_row)

        return panel

    def project_root(self) -> str:
        return self._project_root

    @staticmethod
    def _widget_belongs_to(widget: QWidget | None, ancestor: QWidget) -> bool:
        if not isinstance(widget, QWidget) or not isinstance(ancestor, QWidget):
            return False
        current: QWidget | None = widget
        while current is not None:
            if current == ancestor:
                return True
            current = current.parentWidget()
        return False

    def _on_app_focus_changed(self, _old: QWidget | None, _new: QWidget | None) -> None:
        self._update_focus_highlight()

    def _update_focus_highlight(self) -> None:
        focused = QApplication.focusWidget()
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if not isinstance(page, FileBrowserTabPage):
                continue
            is_active = self._widget_belongs_to(focused, page.list)
            frame = page.list_frame
            previous = frame.property("browserActive")
            if bool(previous) == bool(is_active):
                continue
            frame.setProperty("browserActive", is_active)
            style = frame.style()
            style.unpolish(frame)
            style.polish(frame)
            frame.update()

    def set_project_root(
        self, path: str, title: str | None = None, *, reload: bool = True
    ) -> None:
        self._project_root = os.path.abspath(path)
        self._project_page.set_root_limit(
            self._project_root, navigate_if_outside=reload
        )
        self.tabs.setTabText(0, title or "Project")
        if reload:
            self._project_page.navigate_to(self._project_root)

    def add_user_tab(
        self,
        path: str | None = None,
        title: str | None = None,
        *,
        choose_if_missing: bool = True,
    ) -> FileBrowserTabPage | None:
        start = path
        if not start or not os.path.isdir(start):
            # Duplicate the current tab path — no folder dialog.
            current = self.tabs.currentWidget()
            if isinstance(current, FileBrowserTabPage):
                start = current.current_path()
            if not start or not os.path.isdir(start):
                start = (
                    self._project_root
                    if os.path.isdir(self._project_root)
                    else str(Path.home())
                )

        page = FileBrowserTabPage(start, parent=self.tabs)
        page.audioSelected.connect(self.audioSelected)
        label = title or Path(start).name or "Folder"
        index = self.tabs.addTab(page, label)
        self.tabs.setCurrentIndex(index)
        page.pathChanged.connect(
            lambda p, pg=page: self._update_tab_title(pg, p)
        )
        return page

    def user_tab_states(self) -> list[tuple[str, str]]:
        """Return (title, path) for closable user tabs."""
        result: list[tuple[str, str]] = []
        for i in range(1, self.tabs.count()):
            page = self.tabs.widget(i)
            if not isinstance(page, FileBrowserTabPage):
                continue
            result.append((self.tabs.tabText(i), page.current_path()))
        return result

    def restore_user_tabs(self, tabs: list[tuple[str, str]]) -> None:
        # Remove existing user tabs
        while self.tabs.count() > 1:
            widget = self.tabs.widget(1)
            self.tabs.removeTab(1)
            if widget is not None:
                widget.deleteLater()
        for title, path in tabs:
            if path and os.path.isdir(path):
                self.add_user_tab(path, title=title, choose_if_missing=False)

    def refresh_project_tab(self) -> None:
        self._project_page.refresh()

    def _update_tab_title(self, page: FileBrowserTabPage, path: str) -> None:
        index = self.tabs.indexOf(page)
        if index <= 0:
            return
        self.tabs.setTabText(index, Path(path).name or "Folder")

    def _on_tab_close_requested(self, index: int) -> None:
        if index <= 0:
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()

    def _on_tab_moved(self, _from: int, _to: int) -> None:
        # Keep project tab pinned at index 0
        if self.tabs.indexOf(self._project_page) != 0:
            self.tabs.tabBar().moveTab(self.tabs.indexOf(self._project_page), 0)
            self.tabs.tabBar().setTabButton(
                0, QTabBar.ButtonPosition.RightSide, None
            )
            self.tabs.tabBar().setTabButton(
                0, QTabBar.ButtonPosition.LeftSide, None
            )
