"""Разметка главного окна — Figma Music Player WPF."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.constants import MAX_PLAYLISTS
from app.ui.icon_loader import icon_size, load_icon
from app.ui.tokens import get_token_int
from app.ui.widgets.section_header import EditableSectionHeader, SectionHeader
from app.ui.widgets.segment_button import SegmentButtonGroup
from app.ui.widgets.square_checkbox import SquareCheckBox
from app.ui.widgets.value_stepper import ValueStepper
from app.widgets.audio_waveform import AudioWaveform
from app.widgets.playlist_widget import PlaylistWidget

if TYPE_CHECKING:
    from app.player import AudioPlayer


class MainWindowUi:
    """Создаёт виджеты и layout'ы, привязывает их к окну-плееру."""

    def setup_ui(self, window: AudioPlayer) -> None:
        """Корневой layout: файловый браузер | аудио-UI с плейлистами."""
        central = QWidget()
        central.setObjectName("centralWidget")
        window.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(11, 12, 11, 12)
        root.setSpacing(6)

        from PyQt6.QtWidgets import QSplitter
        from app.widgets.file_browser import FileBrowserPanel

        audio = QWidget()
        audio.setObjectName("audioColumn")
        audio_layout = QVBoxLayout(audio)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        audio_layout.setSpacing(0)
        window.control_panel_section = self._build_control_panel(window)
        window.playlist_section = self._build_playlists(window)

        from app.widgets.file_properties_panel import (
            FilePropertiesPanel,
            PreviewPropertiesPanel,
        )

        window.file_properties_panel = FilePropertiesPanel(window)
        window.file_properties_panel.eqChanged.connect(window._on_eq_changed)

        window.preview_properties_panel = PreviewPropertiesPanel(window)

        window.timeline_section = self._build_timeline(window)

        # Left stack: Control → ON AIR → PREVIEW; EQ to the right
        left_stack = QWidget()
        left_stack.setObjectName("audioLeftStack")
        left_stack_layout = QVBoxLayout(left_stack)
        left_stack_layout.setContentsMargins(0, 0, 0, 0)
        left_stack_layout.setSpacing(4)
        left_stack_layout.addWidget(window.control_panel_section, 0)
        left_stack_layout.addWidget(window.timeline_section, 0)

        main_row = QWidget()
        main_row.setObjectName("audioMainRow")
        main_row_layout = QHBoxLayout(main_row)
        main_row_layout.setContentsMargins(0, 0, 0, 0)
        main_row_layout.setSpacing(6)
        main_row_layout.addWidget(left_stack, 3)
        main_row_layout.addWidget(window.file_properties_panel, 2)
        window.audio_top_row = main_row
        window.audio_left_stack = left_stack

        audio_layout.addWidget(main_row)
        audio_layout.addWidget(window.playlist_section, 1)

        project_root = getattr(window, "_pending_project_root", None)
        if project_root is None:
            from app.project import default_project_root

            project_root = str(default_project_root())
        window.file_browser = FileBrowserPanel(str(project_root), window)
        # Expose browser preview widgets on the main window (parity with playlist preview)
        fb = window.file_browser
        window.browser_waveform = fb.browser_waveform
        window.browser_track_info = fb.browser_track_info
        window.browser_output_combo = fb.browser_output_combo
        window.btn_browser_play = fb.btn_browser_play
        window.btn_browser_pause = fb.btn_browser_pause
        window.btn_browser_stop = fb.btn_browser_stop
        window.btn_browser_autoplay = fb.btn_browser_autoplay
        window.btn_browser_rewind_start = fb.btn_browser_rewind_start
        window.browser_timeline_start_label = fb.browser_timeline_start_label
        window.browser_timeline_end_label = fb.browser_timeline_end_label

        left_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_splitter.setObjectName("audioBrowserSplitter")
        left_splitter.setChildrenCollapsible(False)
        left_splitter.addWidget(window.file_browser)
        left_splitter.addWidget(audio)
        left_splitter.setStretchFactor(0, 2)
        left_splitter.setStretchFactor(1, 5)
        left_splitter.setSizes([260, 900])
        window.audio_browser_splitter = left_splitter

        browser_expand = QPushButton("»")
        browser_expand.setObjectName("browserExpandBtn")
        browser_expand.setToolTip("Expand file browser")
        browser_expand.setFixedWidth(18)
        browser_expand.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        browser_expand.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        browser_expand.hide()
        browser_expand.clicked.connect(window.expand_file_browser)
        window.browser_expand_btn = browser_expand
        window.file_browser.collapseRequested.connect(window.collapse_file_browser)

        left_wrap = QWidget()
        left_wrap.setObjectName("audioBrowserColumn")
        left_wrap_layout = QHBoxLayout(left_wrap)
        left_wrap_layout.setContentsMargins(0, 0, 0, 0)
        left_wrap_layout.setSpacing(0)
        left_wrap_layout.addWidget(browser_expand)
        left_wrap_layout.addWidget(left_splitter, 1)
        root.addWidget(left_wrap, 1)

        window.audio_output.setVolume(window._playback_volume)
        window.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        window.set_playlist_columns(2)

    def _build_control_panel(self, window: AudioPlayer) -> QWidget:
        """Две линии: подписи сверху, виджеты снизу; время справа."""
        section = QFrame()
        section.setObjectName("controlPanelSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(2)

        control_row_height = get_token_int("sizes.stepper_value", 26)

        window.btn_previous = None
        window.btn_next = None
        window.btn_play = None
        window.btn_pause = None
        window.btn_stop = None

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        col = 0

        def add_labeled(label_text: str, widget: QWidget) -> None:
            nonlocal col
            lbl = QLabel(label_text)
            lbl.setObjectName("sectionLabel")
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            grid.addWidget(lbl, 0, col, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            grid.addWidget(
                widget, 1, col, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            col += 1

        window.btn_space = QPushButton("ON AIR")
        window.btn_space.setObjectName("accentButtonSpace")
        window.btn_space.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        window.btn_space.setAutoDefault(False)
        window.btn_space.setDefault(False)
        window.btn_space.setToolTip("Load preview to air (same as Space key)")
        window.btn_space.setMinimumWidth(108)
        window.btn_space.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        window.btn_space.clicked.connect(window.handle_space)
        # Без подписи сверху — на всю высоту панели (обе строки сетки)
        grid.addWidget(
            window.btn_space,
            0,
            col,
            2,
            1,
            Qt.AlignmentFlag.AlignVCenter,
        )
        col += 1

        window.playback_mode_group = SegmentButtonGroup(
            [("loop", "LOOP"), ("next", "NEXT"), ("stop", "STOP")],
        )
        window.playback_mode_group.set_value("next")
        window.playback_mode_group.valueChanged.connect(window.on_playback_mode_changed)
        add_labeled("PLAYBACK MODE", window.playback_mode_group)
        window.radio_next = None
        window.radio_stop = None
        window.radio_loop = None

        window.btn_fade_mode = self._fade_mode_button(
            window,
            window.toggle_fade_mode,
            tooltip="Sequential: fade out completes before the next track fades in",
        )
        add_labeled("FADE MODE", window.btn_fade_mode)

        fade_ms_wrap = QWidget()
        fade_ms_wrap.setFixedHeight(control_row_height)
        fade_ms_layout = QHBoxLayout(fade_ms_wrap)
        fade_ms_layout.setContentsMargins(0, 0, 0, 0)
        fade_ms_layout.setSpacing(4)
        window.fade_duration_stepper = ValueStepper(
            window,
            0,
            5000,
            1500,
            step=100,
            value_width=60,
            editable=True,
        )
        window.fade_duration_spin = window.fade_duration_stepper.spin_box()
        window.fade_duration_spin.setToolTip(
            "Fade out on Space/Stop; crossfade/sequential track transition duration"
        )
        fade_ms_unit = QLabel("MS")
        fade_ms_unit.setObjectName("fadeMsLabel")
        fade_ms_layout.addWidget(window.fade_duration_stepper)
        fade_ms_layout.addWidget(fade_ms_unit)
        add_labeled("FADE", fade_ms_wrap)

        window.columns_stepper = ValueStepper(window, 1, MAX_PLAYLISTS, 2)
        window.columns_spin = window.columns_stepper.spin_box()
        window.columns_value_label = window.columns_stepper._value_label
        window.columns_spin.valueChanged.connect(window.set_playlist_columns)
        add_labeled("PLAYLISTS", window.columns_stepper)
        window.btn_columns_up = None
        window.btn_columns_down = None

        advance_group = QWidget()
        advance_group.setFixedHeight(control_row_height)
        advance_group_layout = QHBoxLayout(advance_group)
        advance_group_layout.setContentsMargins(0, 0, 0, 0)
        advance_group_layout.setSpacing(2)
        window.space_advances_checkbox = SquareCheckBox()
        window.space_advances_checkbox.setObjectName("spaceAdvancesCheckbox")
        advance_label = QLabel("ADVANCE")
        advance_label.setObjectName("spaceAdvancesLabel")
        advance_label.setCursor(Qt.CursorShape.PointingHandCursor)
        advance_label.setToolTip("Advance selection on Space")
        self._bind_checkbox_label(window.space_advances_checkbox, advance_label)
        advance_group_layout.addWidget(
            window.space_advances_checkbox,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        advance_group_layout.addWidget(
            advance_label,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        add_labeled("ON SPACE", advance_group)

        window.bpm_value_label = QLabel("—")
        window.bpm_value_label.setObjectName("bpmValue")
        window.bpm_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        window.bpm_value_label.setFixedHeight(control_row_height)
        window.bpm_label = window.bpm_value_label
        add_labeled("BPM", window.bpm_value_label)

        grid.setColumnStretch(col, 1)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        grid.addWidget(spacer, 0, col, 2, 1)
        col += 1

        # Время справа: Elapsed/Remaining + крупные цифры (без FADE)
        time_wrap = QWidget()
        time_layout = QVBoxLayout(time_wrap)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(2)

        window.time_mode_group = SegmentButtonGroup(
            [("elapsed", "Elapsed"), ("remaining", "Remaining")],
        )
        window.time_mode_group.setObjectName("timeModeGroup")
        window.time_mode_group.set_value("elapsed")
        window.time_mode_group.valueChanged.connect(window.on_time_mode_changed)
        time_layout.addWidget(window.time_mode_group, 0, Qt.AlignmentFlag.AlignRight)

        time_values = QHBoxLayout()
        time_values.setSpacing(8)
        window.time_large_label = QLabel("--:--")
        window.time_large_label.setObjectName("timeLarge")
        window.time_large_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        window.time_duration_label = QLabel("--:--")
        window.time_duration_label.setObjectName("timeDuration")
        window.time_duration_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        time_values.addWidget(window.time_large_label)
        time_values.addWidget(window.time_duration_label)
        time_layout.addLayout(time_values)

        grid.addWidget(time_wrap, 0, col, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        window.time_current_label = window.time_large_label
        window.time_total_label = window.time_duration_label
        window.time_small_label = window.time_duration_label
        window.time_sep_label = QLabel("")
        window.radio_time_elapsed = None
        window.radio_time_remaining = None

        layout.addLayout(grid)
        return section

    def _build_timeline(self, window: AudioPlayer) -> QWidget:
        """Стек: ON AIR сверху, PREVIEW снизу (без заголовка TIMELINE)."""
        section = QFrame()
        section.setObjectName("timelineSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)

        # --- Air ---
        air_col = QFrame()
        air_col.setObjectName("airWaveformColumn")
        air_layout = QVBoxLayout(air_col)
        air_layout.setContentsMargins(0, 0, 0, 0)
        air_layout.setSpacing(4)

        air_header = QHBoxLayout()
        air_header.setContentsMargins(0, 0, 0, 0)
        air_header.setSpacing(6)
        air_header.addWidget(SectionHeader("ON AIR"), 1)
        window.btn_refresh_devices = QPushButton("↻")
        window.btn_refresh_devices.setObjectName("deviceRefreshButton")
        window.btn_refresh_devices.setToolTip("Refresh audio output device list")
        window.btn_refresh_devices.setFixedSize(22, 18)
        window.btn_refresh_devices.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        window.btn_refresh_devices.setAutoDefault(False)
        window.btn_refresh_devices.setDefault(False)
        window.btn_refresh_devices.clicked.connect(window.refresh_audio_output_devices)
        air_header.addWidget(window.btn_refresh_devices, 0, Qt.AlignmentFlag.AlignRight)
        air_layout.addLayout(air_header)

        air_device_row = QHBoxLayout()
        air_device_row.setSpacing(6)
        air_device_label = QLabel("AIR OUTPUT")
        air_device_label.setObjectName("sectionLabel")
        window.air_output_combo = QComboBox()
        window.air_output_combo.setObjectName("airOutputCombo")
        window.air_output_combo.setToolTip("Audio output device for on-air playback")
        air_device_row.addWidget(air_device_label)
        air_device_row.addWidget(window.air_output_combo, 1)
        air_layout.addLayout(air_device_row)

        window.track_info = QLabel("No track selected")
        window.track_info.setObjectName("previewTrackInfo")
        window.track_info.setWordWrap(True)
        window.track_info.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        air_layout.addWidget(window.track_info)

        air_layout.addLayout(self._build_waveform_row(
            window,
            waveform_attr="waveform",
            zoom_reset_attr="btn_zoom_reset",
            rewind_attr="btn_rewind_start",
            res_attr="btn_waveform_res",
            start_label_attr="timeline_start_label",
            end_label_attr="timeline_end_label",
            zoom_reset_slot=window.reset_waveform_zoom,
            rewind_slot=window.rewind_to_start,
            res_slot=window.on_waveform_res_toggled,
            play_slot=window.toggle_play,
            pause_slot=window.pause,
            stop_slot=window.stop,
        ))
        layout.addWidget(air_col)

        # --- Preview ---
        preview_col = QFrame()
        preview_col.setObjectName("previewWaveformColumn")
        preview_layout = QVBoxLayout(preview_col)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        preview_layout.addWidget(SectionHeader("PREVIEW"))

        preview_device_row = QHBoxLayout()
        preview_device_row.setSpacing(6)
        preview_device_label = QLabel("PREVIEW OUTPUT")
        preview_device_label.setObjectName("sectionLabel")
        window.preview_output_combo = QComboBox()
        window.preview_output_combo.setObjectName("previewOutputCombo")
        window.preview_output_combo.setToolTip("Audio output device for preview / PFL")
        preview_device_row.addWidget(preview_device_label)
        preview_device_row.addWidget(window.preview_output_combo, 1)
        preview_layout.addLayout(preview_device_row)

        track_meta_row = QHBoxLayout()
        track_meta_row.setContentsMargins(0, 0, 0, 0)
        track_meta_row.setSpacing(8)
        window.preview_track_info = QLabel("No track selected")
        window.preview_track_info.setObjectName("previewTrackInfo")
        window.preview_track_info.setWordWrap(True)
        track_meta_row.addWidget(window.preview_track_info, 1)
        track_meta_row.addWidget(
            window.preview_properties_panel, 0, Qt.AlignmentFlag.AlignRight
        )
        preview_layout.addLayout(track_meta_row)

        preview_layout.addLayout(self._build_waveform_row(
            window,
            waveform_attr="preview_waveform",
            zoom_reset_attr="btn_preview_zoom_reset",
            rewind_attr="btn_preview_rewind_start",
            res_attr="btn_preview_waveform_res",
            start_label_attr="preview_timeline_start_label",
            end_label_attr="preview_timeline_end_label",
            zoom_reset_slot=window.reset_preview_waveform_zoom,
            rewind_slot=window.rewind_preview_to_start,
            res_slot=window.on_preview_waveform_res_toggled,
            preview=True,
            play_slot=window.preview_play,
            pause_slot=window.preview_pause,
            stop_slot=window.preview_stop,
            autoplay_slot=window.on_preview_autoplay_toggled,
        ))
        layout.addWidget(preview_col)

        return section

    @staticmethod
    def _transport_button(btn: QPushButton) -> QPushButton:
        """Transport controls must not steal ↑/↓ focus from playlists."""
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        return btn

    def _build_waveform_row(
        self,
        window: AudioPlayer,
        *,
        waveform_attr: str,
        zoom_reset_attr: str,
        rewind_attr: str,
        res_attr: str,
        start_label_attr: str,
        end_label_attr: str,
        zoom_reset_slot,
        rewind_slot,
        res_slot,
        preview: bool = False,
        play_slot=None,
        pause_slot=None,
        stop_slot=None,
        autoplay_slot=None,
    ) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(2)

        wave_row = QHBoxLayout()
        wave_row.setSpacing(6)
        wave_height = get_token_int("sizes.preview_waveform_height", 72)
        waveform = AudioWaveform(bar_area_height=wave_height)
        if preview:
            waveform.setObjectName("previewWaveform")
        setattr(window, waveform_attr, waveform)

        tool_px = get_token_int("sizes.tool_button", 18)
        icon_px = get_token_int("sizes.icon", 14)
        btn_h = tool_px
        btn_w = tool_px * 2

        # Сетка 2 колонки, как раньше
        wave_tools = QGridLayout()
        wave_tools.setSpacing(4)
        wave_tools.setContentsMargins(0, 0, 0, 0)

        def make_icon_btn(name: str, tip: str, slot) -> QPushButton:
            btn = QPushButton()
            btn.setObjectName("waveformToolButton")
            btn.setToolTip(tip)
            btn.setIcon(load_icon(window, name, icon_px))
            btn.setIconSize(icon_size(icon_px))
            btn.setFixedSize(btn_w, btn_h)
            btn.clicked.connect(slot)
            self._transport_button(btn)
            return btn

        def make_text_btn(text: str, tip: str, *, checkable: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setObjectName(
                "previewTextToolButton" if preview else "waveformToolButton"
            )
            btn.setToolTip(tip)
            btn.setFixedSize(btn_w, btn_h)
            if checkable:
                btn.setCheckable(True)
            self._transport_button(btn)
            return btn

        row_i = 0
        if play_slot and pause_slot and stop_slot:
            btn_play = make_icon_btn("play", "Play" if not preview else "Preview play", play_slot)
            btn_pause = make_icon_btn(
                "pause", "Pause" if not preview else "Preview pause", pause_slot
            )
            btn_stop = make_icon_btn(
                "stop", "Stop" if not preview else "Preview stop", stop_slot
            )
            if preview:
                window.btn_preview_play = btn_play
                window.btn_preview_pause = btn_pause
                window.btn_preview_stop = btn_stop
            else:
                window.btn_play = btn_play
                window.btn_pause = btn_pause
                window.btn_stop = btn_stop

            wave_tools.addWidget(btn_play, row_i, 0)
            wave_tools.addWidget(btn_pause, row_i, 1)
            row_i += 1
            wave_tools.addWidget(btn_stop, row_i, 0)
            if preview and autoplay_slot is not None:
                btn_autoplay = make_text_btn(
                    "AUTO", "Auto-play preview when selecting a track", checkable=True
                )
                btn_autoplay.setObjectName("previewAutoplayButton")
                btn_autoplay.setChecked(True)
                btn_autoplay.toggled.connect(autoplay_slot)
                window.btn_preview_autoplay = btn_autoplay
                wave_tools.addWidget(btn_autoplay, row_i, 1)
            row_i += 1

        btn_zoom_reset = make_text_btn("1:1", "Reset waveform zoom")
        btn_zoom_reset.setEnabled(False)
        btn_zoom_reset.clicked.connect(zoom_reset_slot)
        setattr(window, zoom_reset_attr, btn_zoom_reset)

        btn_rewind_start = make_icon_btn("back_begin", "Rewind to start", rewind_slot)
        setattr(window, rewind_attr, btn_rewind_start)

        wave_tools.addWidget(btn_zoom_reset, row_i, 0)
        wave_tools.addWidget(btn_rewind_start, row_i, 1)
        row_i += 1

        btn_waveform_res = make_text_btn(
            "RES", "Higher waveform resolution when zoomed", checkable=True
        )
        btn_waveform_res.setEnabled(False)
        btn_waveform_res.toggled.connect(res_slot)
        setattr(window, res_attr, btn_waveform_res)
        wave_tools.addWidget(btn_waveform_res, row_i, 0)

        btn_eject = QPushButton()
        btn_eject.setObjectName("waveformToolButton")
        btn_eject.setIcon(load_icon(window, "eject", icon_px))
        btn_eject.setIconSize(icon_size(icon_px))
        btn_eject.setFixedSize(btn_w, btn_h)
        self._transport_button(btn_eject)
        if preview:
            btn_eject.setToolTip("Eject / clear preview")
            window.btn_preview_eject = btn_eject
        else:
            btn_eject.setToolTip("Eject / clear on-air")
            window.btn_air_eject = btn_eject
        wave_tools.addWidget(btn_eject, row_i, 1)

        wave_row.addWidget(waveform, 1)
        wave_row.addLayout(wave_tools)
        block.addLayout(wave_row)

        # Сохраняем для player.py, но не показываем
        start_label = QLabel("0:00")
        start_label.setObjectName("timelineStart")
        end_label = QLabel("0:00")
        end_label.setObjectName("timelineEnd")
        start_label.hide()
        end_label.hide()
        setattr(window, start_label_attr, start_label)
        setattr(window, end_label_attr, end_label)

        return block

    def _build_playlists(self, window: AudioPlayer) -> QWidget:
        """Горизонтальный ряд панелей плейлистов; видимость задаётся числом колонок."""
        container = QFrame()
        container.setObjectName("playlistSection")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        window.playlist_panels = []
        window.playlists = []
        window.font_size_spins = []
        window.playlist_footer_labels = []
        window.playlist_headers = []

        for i in range(1, MAX_PLAYLISTS + 1):
            panel = self._create_playlist_panel(window, i)
            window.playlist_panels.append(panel)
            layout.addWidget(panel, 1)

        window.playlists_layout = layout
        return container

    def _create_playlist_panel(self, window: AudioPlayer, playlist_num: int) -> QWidget:
        """Один плейлист: тулбар → список треков → футер со статистикой и размером шрифта."""
        panel = QFrame()
        panel.setObjectName("playlistPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = EditableSectionHeader(f"Playlist {playlist_num}")
        header.titleChanged.connect(
            lambda title, n=playlist_num: window.on_playlist_title_changed(n, title)
        )
        window.playlist_headers.append(header)
        layout.addWidget(header)

        # Тулбар по центру над списком
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

        playlist = PlaylistWidget(f"Playlist{playlist_num}", playlist_num)

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

        # Перемещение треков
        add_tool("up", "Move up", lambda checked=False: playlist.move_item_up())
        add_tool("down", "Move down", lambda checked=False: playlist.move_item_down())
        add_tool("top", "Move to top", lambda checked=False: playlist.move_item_top())
        add_tool("bottom", "Move to bottom", lambda checked=False: playlist.move_item_bottom())
        # Файлы / импорт-экспорт плейлиста
        add_tool("add", "Add files", lambda checked=False, pl=playlist: window.add_to_playlist_widget(pl))
        add_tool("remove", "Remove", lambda checked=False, pl=playlist: window.remove_from_playlist_widget(pl))
        add_tool("import", "Import", lambda checked=False, pl=playlist: window.import_playlist_widget(pl))
        add_tool("export", "Export", lambda checked=False, pl=playlist: window.export_playlist_widget(pl))
        add_tool("clear", "Clear", lambda checked=False, pl=playlist: window.clear_playlist_widget(pl))
        add_tool(
            "to_project",
            "Move selected track into project folder",
            lambda checked=False, pl=playlist: window.move_selected_track_to_project(pl),
        )
        add_tool(
            "copy_to_project",
            "Copy all tracks into project folder",
            lambda checked=False, pl=playlist: window.copy_playlist_tracks_to_project(pl),
        )
        # Анализ файла (BPM + аудиоволна → кеш)
        add_tool(
            "bpm_all",
            "Analyze playlist (BPM + waveform)",
            lambda checked=False, pl=playlist: window.analyze_playlist_files(pl),
        )
        add_tool(
            "bpm_one",
            "Re-analyze selected track (BPM + waveform)",
            lambda checked=False, pl=playlist: window.analyze_selected_file(pl),
            opacity=0.72,
        )
        toolbar_wrap_layout.addWidget(toolbar)
        toolbar_wrap_layout.addStretch()
        layout.addWidget(toolbar_wrap)

        # Список треков (stretch — занимает всю вертикаль)
        list_frame = QFrame()
        list_frame.setObjectName("playlistListFrame")
        list_layout = QVBoxLayout(list_frame)
        list_layout.setContentsMargins(0, 0, 0, 0)
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
        list_layout.addWidget(playlist)
        layout.addWidget(list_frame, 1)

        # Футер: число треков / общая длительность + размер шрифта плейлиста
        footer = QHBoxLayout()
        footer_label = QLabel("Total Tracks: 0   Total Time: 0:00")
        footer_label.setObjectName("playlistFooter")
        window.playlist_footer_labels.append(footer_label)
        footer.addWidget(footer_label)
        footer.addStretch()

        font_label = QLabel("FONT SIZE")
        font_label.setObjectName("fontSizeLabel")
        font_stepper = ValueStepper(window, 8, 30, 13)
        font_spin = font_stepper.spin_box()
        font_spin.valueChanged.connect(lambda v, n=playlist_num: window.change_playlist_font(n, v))
        font_spin.valueChanged.connect(lambda _v, pl=playlist: window.refresh_playlist_footer(pl))
        footer.addWidget(font_label)
        footer.addWidget(font_stepper)
        layout.addLayout(footer)

        window.playlists.append(playlist)
        window.font_size_spins.append(font_spin)
        window.change_playlist_font(playlist_num, font_spin.value())
        return panel

    @staticmethod
    def _bind_checkbox_label(checkbox: SquareCheckBox, label: QLabel) -> None:
        def on_label_pressed(event):
            if event.button() == Qt.MouseButton.LeftButton:
                checkbox.toggle()

        label.mousePressEvent = on_label_pressed

    @staticmethod
    def _fade_mode_button(window: AudioPlayer, slot, *, tooltip: str = "") -> QToolButton:
        icon_px = get_token_int("sizes.icon_button", 18)
        btn_size = get_token_int("sizes.stepper_value", 26)
        btn = QToolButton()
        btn.setObjectName("fadeModeButton")
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setIcon(load_icon(window, "fade_mode_1", icon_px))
        btn.setIconSize(icon_size(icon_px))
        btn.setFixedSize(btn_size, btn_size)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _icon_tool_button(window: AudioPlayer, name: str, slot, *, tooltip: str = "") -> QToolButton:
        icon_px = get_token_int("sizes.icon", 12)
        tool_px = get_token_int("sizes.tool_button", 16)
        btn = QToolButton()
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setIcon(load_icon(window, name, icon_px))
        btn.setIconSize(icon_size(icon_px))
        btn.setFixedSize(tool_px, tool_px)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _icon_button(window: AudioPlayer, name: str, slot) -> QPushButton:
        """Кнопка транспорта только с иконкой (play, pause, stop, …)."""
        icon_px = get_token_int("sizes.icon", 12)
        btn = QPushButton()
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        btn.setIcon(load_icon(window, name, icon_px))
        btn.setIconSize(icon_size(icon_px))
        btn.clicked.connect(slot)
        return btn
