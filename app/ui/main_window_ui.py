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
        """Корневой layout: файловый браузер | аудио-UI | Video Mixer."""
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
        window.timeline_section = self._build_timeline(window)
        window.playlist_section = self._build_playlists(window)
        audio_layout.addWidget(window.control_panel_section)
        audio_layout.addWidget(window.timeline_section)
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

        from app.video_mixer.controller import VideoMixerController

        window.video_mixer = VideoMixerController(window)
        window.video_mixer.attach_to_layout(root, left_wrap, stretch=1)

        window.audio_output.setVolume(window._playback_volume)
        window.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        window.set_playlist_columns(2)

    def _build_control_panel(self, window: AudioPlayer) -> QWidget:
        """Верхняя секция: слева трек/BPM и управление, справа блок времени."""
        section = QFrame()
        section.setObjectName("controlPanelSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)

        layout.addWidget(SectionHeader("CONTROL PANEL"))

        # --- Основная строка: [левая колонка | блок времени] ---
        main_row = QHBoxLayout()
        main_row.setSpacing(8)

        card_height = 50
        bpm_width = 70
        time_width = 200

        left_col = QVBoxLayout()
        left_col.setSpacing(6)

        # Строка: название трека + BPM
        track_bpm_row = QHBoxLayout()
        track_bpm_row.setSpacing(8)

        track_card = QFrame()
        track_card.setObjectName("trackInfoCard")
        track_card.setFixedHeight(card_height)
        track_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        track_layout = QVBoxLayout(track_card)
        track_layout.setContentsMargins(8, 8, 8, 8)
        window.track_info = QLabel("No track selected")
        window.track_info.setObjectName("trackInfo")
        window.track_info.setWordWrap(True)
        window.track_info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        track_layout.addWidget(window.track_info)
        track_bpm_row.addWidget(track_card, 1)

        bpm_card = QFrame()
        bpm_card.setObjectName("bpmCard")
        bpm_card.setFixedSize(bpm_width, card_height)
        bpm_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        bpm_layout = QVBoxLayout(bpm_card)
        bpm_layout.setContentsMargins(8, 8, 8, 8)
        bpm_layout.setSpacing(0)
        bpm_title = QLabel("BPM")
        bpm_title.setObjectName("bpmTitle")
        window.bpm_value_label = QLabel("—")
        window.bpm_value_label.setObjectName("bpmValue")
        window.bpm_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        window.bpm_label = window.bpm_value_label  # устаревший алиас, используется в player.py
        bpm_layout.addWidget(bpm_title)
        bpm_layout.addWidget(window.bpm_value_label, 1)
        track_bpm_row.addWidget(bpm_card)
        left_col.addLayout(track_bpm_row)

        # Блок воспроизведения: кнопки | режим | колонки плейлистов
        controls = QFrame()
        controls.setObjectName("controlRow")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(16)
        controls_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        playback_col = QVBoxLayout()
        playback_col.setSpacing(4)
        playback_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        playback_label = QLabel("PLAYBACK CONTROL")
        playback_label.setObjectName("sectionLabel")
        playback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        playback_col.addWidget(playback_label)

        playback_btns = QHBoxLayout()
        playback_btns.setSpacing(4)
        window.btn_previous = self._icon_button(window, "previous", window.previous_track)
        window.btn_play = self._icon_button(window, "play", window.toggle_play)
        window.btn_pause = self._icon_button(window, "pause", window.pause)
        window.btn_stop = self._icon_button(window, "stop", window.stop)
        window.btn_next = self._icon_button(window, "next", window.next_track)
        window.btn_space = QPushButton("SPACE")
        window.btn_space.setObjectName("accentButton")
        window.btn_space.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        window.btn_space.setAutoDefault(False)
        window.btn_space.setDefault(False)
        window.btn_space.setToolTip("Load preview to air (same as Space key)")
        window.btn_space.clicked.connect(window.handle_space)
        for btn in (
            window.btn_previous,
            window.btn_play,
            window.btn_pause,
            window.btn_stop,
            window.btn_next,
        ):
            playback_btns.addWidget(btn)
        playback_btns.addWidget(window.btn_space)
        playback_col.addLayout(playback_btns)

        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        mode_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        mode_label = QLabel("PLAYBACK MODE")
        mode_label.setObjectName("sectionLabel")
        mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_col.addWidget(mode_label)
        window.playback_mode_group = SegmentButtonGroup(
            [("loop", "LOOP"), ("next", "NEXT"), ("stop", "STOP")],
        )
        window.playback_mode_group.set_value("next")
        window.playback_mode_group.valueChanged.connect(window.on_playback_mode_changed)
        mode_col.addWidget(window.playback_mode_group)
        window.radio_next = None
        window.radio_stop = None
        window.radio_loop = None

        columns_col = QVBoxLayout()
        columns_col.setSpacing(4)
        columns_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        columns_label = QLabel("PLAYLISTS")
        columns_label.setObjectName("sectionLabel")
        columns_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        columns_col.addWidget(columns_label)
        window.columns_stepper = ValueStepper(window, 1, MAX_PLAYLISTS, 2)
        window.columns_spin = window.columns_stepper.spin_box()
        window.columns_value_label = window.columns_stepper._value_label
        window.columns_spin.valueChanged.connect(window.set_playlist_columns)
        columns_col.addWidget(window.columns_stepper)
        window.btn_columns_up = None
        window.btn_columns_down = None

        controls_layout.addLayout(playback_col)
        controls_layout.addLayout(mode_col)

        fade_mode_col = QVBoxLayout()
        fade_mode_col.setSpacing(4)
        fade_mode_col.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        fade_mode_label = QLabel("FADE MODE")
        fade_mode_label.setObjectName("sectionLabel")
        fade_mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fade_mode_label.setWordWrap(False)
        fade_mode_col.addWidget(fade_mode_label)
        fade_controls = QWidget()
        control_row_height = get_token_int("sizes.stepper_value", 26)
        fade_controls.setFixedHeight(control_row_height)
        fade_controls_layout = QHBoxLayout(fade_controls)
        fade_controls_layout.setContentsMargins(0, 0, 0, 0)
        fade_controls_layout.addStretch()
        window.btn_fade_mode = self._fade_mode_button(
            window,
            window.toggle_fade_mode,
            tooltip="Sequential: fade out completes before the next track fades in",
        )
        fade_controls_layout.addWidget(window.btn_fade_mode)
        fade_controls_layout.addStretch()
        fade_mode_col.addWidget(fade_controls)
        controls_layout.addLayout(fade_mode_col)

        controls_layout.addLayout(columns_col)
        controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        controls_center_row = QHBoxLayout()
        controls_center_row.setContentsMargins(0, 0, 0, 0)
        controls_center_row.addStretch(1)
        controls_center_row.addWidget(controls)
        controls_center_row.addStretch(1)
        left_col.addLayout(controls_center_row)

        # Блок времени: режим → время → fade → advance selection
        time_block = QFrame()
        time_block.setObjectName("timeBlock")
        time_block.setFixedWidth(time_width)
        time_block.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        time_layout = QVBoxLayout(time_block)
        time_layout.setContentsMargins(8, 6, 8, 6)
        time_layout.setSpacing(6)

        # 1. Elapsed / Remaining
        window.time_mode_group = SegmentButtonGroup(
            [("elapsed", "Elapsed"), ("remaining", "Remaining")],
        )
        window.time_mode_group.setObjectName("timeModeGroup")
        window.time_mode_group.set_value("elapsed")
        window.time_mode_group.valueChanged.connect(window.on_time_mode_changed)
        time_layout.addWidget(window.time_mode_group)

        # 2. Основное время (elapsed/remaining) + общая длительность
        time_values_row = QHBoxLayout()
        time_values_row.setSpacing(8)
        window.time_large_label = QLabel("--:--")
        window.time_large_label.setObjectName("timeLarge")
        window.time_large_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        window.time_duration_label = QLabel("--:--")
        window.time_duration_label.setObjectName("timeDuration")
        window.time_duration_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        time_values_row.addWidget(window.time_large_label, 1)
        time_values_row.addWidget(window.time_duration_label)
        time_layout.addLayout(time_values_row)

        # 3. Fade
        fade_row = QHBoxLayout()
        fade_row.setSpacing(8)
        fade_label = QLabel("FADE")
        fade_label.setObjectName("fadeLabel")
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
        fade_ms_label = QLabel("MS")
        fade_ms_label.setObjectName("fadeMsLabel")
        fade_row.addWidget(fade_label)
        fade_row.addWidget(window.fade_duration_stepper)
        fade_row.addWidget(fade_ms_label)
        fade_row.addStretch()
        time_layout.addLayout(fade_row)

        # 4. Advance selection on Space — индикатор и текст отдельно, без растягивания QCheckBox
        advance_row = QHBoxLayout()
        advance_row.setContentsMargins(0, 0, 0, 0)
        advance_group = QWidget()
        advance_group_layout = QHBoxLayout(advance_group)
        advance_group_layout.setContentsMargins(0, 0, 0, 0)
        advance_group_layout.setSpacing(2)
        window.space_advances_checkbox = SquareCheckBox()
        window.space_advances_checkbox.setObjectName("spaceAdvancesCheckbox")
        advance_label = QLabel("ADVANCE SELECTION ON SPACE")
        advance_label.setObjectName("spaceAdvancesLabel")
        advance_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bind_checkbox_label(window.space_advances_checkbox, advance_label)
        advance_group_layout.addWidget(
            window.space_advances_checkbox,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        advance_group_layout.addWidget(
            advance_label,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        advance_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        advance_row.addStretch(1)
        advance_row.addWidget(advance_group)
        advance_row.addStretch(1)
        time_layout.addLayout(advance_row)

        # Алиасы для совместимости со старыми путями кода в player.py
        window.time_current_label = window.time_large_label
        window.time_total_label = window.time_duration_label
        window.time_small_label = window.time_duration_label
        window.time_sep_label = QLabel("")
        window.radio_time_elapsed = None
        window.radio_time_remaining = None

        main_row.addLayout(left_col, 1)
        main_row.addWidget(time_block, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(main_row)

        return section

    def _build_timeline(self, window: AudioPlayer) -> QWidget:
        """Таймлайн: эфир и предпросмотр с отдельными waveform и устройствами вывода."""
        section = QFrame()
        section.setObjectName("timelineSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)

        layout.addWidget(SectionHeader("TIMELINE"))

        device_row = QHBoxLayout()
        device_row.setSpacing(12)
        air_device_label = QLabel("AIR OUTPUT")
        air_device_label.setObjectName("sectionLabel")
        window.air_output_combo = QComboBox()
        window.air_output_combo.setObjectName("airOutputCombo")
        window.air_output_combo.setToolTip("Audio output device for on-air playback")
        preview_device_label = QLabel("PREVIEW OUTPUT")
        preview_device_label.setObjectName("sectionLabel")
        window.preview_output_combo = QComboBox()
        window.preview_output_combo.setObjectName("previewOutputCombo")
        window.preview_output_combo.setToolTip("Audio output device for preview / PFL")
        device_row.addWidget(air_device_label)
        device_row.addWidget(window.air_output_combo, 1)
        device_row.addSpacing(8)
        device_row.addWidget(preview_device_label)
        device_row.addWidget(window.preview_output_combo, 1)
        layout.addLayout(device_row)

        layout.addWidget(SectionHeader("ON AIR"))
        layout.addLayout(self._build_waveform_row(
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
        ))

        layout.addWidget(SectionHeader("PREVIEW"))
        preview_track_row = QHBoxLayout()
        window.preview_track_info = QLabel("No track selected")
        window.preview_track_info.setObjectName("previewTrackInfo")
        window.preview_track_info.setWordWrap(True)
        preview_track_row.addWidget(window.preview_track_info, 1)
        layout.addLayout(preview_track_row)
        layout.addLayout(self._build_waveform_row(
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
        block.setSpacing(4)

        wave_row = QHBoxLayout()
        wave_row.setSpacing(6)
        if preview:
            preview_height = get_token_int("sizes.preview_waveform_height", 72)
            waveform = AudioWaveform(bar_area_height=preview_height)
            waveform.setObjectName("previewWaveform")
        else:
            waveform = AudioWaveform()
        setattr(window, waveform_attr, waveform)

        tool_px = get_token_int("sizes.tool_button", 18)
        icon_px = get_token_int("sizes.icon", 14)
        btn_h = tool_px
        btn_w = tool_px * 2

        if preview and play_slot and pause_slot and stop_slot:
            wave_tools = QGridLayout()
            wave_tools.setSpacing(4)
            wave_tools.setContentsMargins(0, 0, 0, 0)

            btn_play = QPushButton()
            btn_play.setObjectName("waveformToolButton")
            btn_play.setToolTip("Preview play")
            btn_play.setIcon(load_icon(window, "play", icon_px))
            btn_play.setIconSize(icon_size(icon_px))
            btn_play.setFixedSize(btn_w, btn_h)
            btn_play.clicked.connect(play_slot)
            self._transport_button(btn_play)
            window.btn_preview_play = btn_play

            btn_pause = QPushButton()
            btn_pause.setObjectName("waveformToolButton")
            btn_pause.setToolTip("Preview pause")
            btn_pause.setIcon(load_icon(window, "pause", icon_px))
            btn_pause.setIconSize(icon_size(icon_px))
            btn_pause.setFixedSize(btn_w, btn_h)
            btn_pause.clicked.connect(pause_slot)
            self._transport_button(btn_pause)
            window.btn_preview_pause = btn_pause

            btn_stop = QPushButton()
            btn_stop.setObjectName("waveformToolButton")
            btn_stop.setToolTip("Preview stop")
            btn_stop.setIcon(load_icon(window, "stop", icon_px))
            btn_stop.setIconSize(icon_size(icon_px))
            btn_stop.setFixedSize(btn_w, btn_h)
            btn_stop.clicked.connect(stop_slot)
            self._transport_button(btn_stop)
            window.btn_preview_stop = btn_stop

            btn_autoplay = None
            if autoplay_slot is not None:
                btn_autoplay = QPushButton("AUTO")
                btn_autoplay.setObjectName("previewAutoplayButton")
                btn_autoplay.setCheckable(True)
                btn_autoplay.setChecked(True)
                btn_autoplay.setToolTip("Auto-play preview when selecting a track")
                btn_autoplay.setFixedSize(btn_w, btn_h)
                btn_autoplay.toggled.connect(autoplay_slot)
                self._transport_button(btn_autoplay)
                window.btn_preview_autoplay = btn_autoplay

            btn_zoom_reset = QPushButton("1:1")
            btn_zoom_reset.setObjectName("previewTextToolButton")
            btn_zoom_reset.setToolTip("Reset waveform zoom")
            btn_zoom_reset.setFixedSize(btn_w, btn_h)
            btn_zoom_reset.setEnabled(False)
            btn_zoom_reset.clicked.connect(zoom_reset_slot)
            self._transport_button(btn_zoom_reset)
            setattr(window, zoom_reset_attr, btn_zoom_reset)

            btn_rewind_start = QPushButton()
            btn_rewind_start.setObjectName("waveformToolButton")
            btn_rewind_start.setToolTip("Rewind to start")
            btn_rewind_start.setIcon(load_icon(window, "back_begin", icon_px))
            btn_rewind_start.setIconSize(icon_size(icon_px))
            btn_rewind_start.setFixedSize(btn_w, btn_h)
            btn_rewind_start.clicked.connect(rewind_slot)
            self._transport_button(btn_rewind_start)
            setattr(window, rewind_attr, btn_rewind_start)

            btn_eject = QPushButton()
            btn_eject.setObjectName("waveformToolButton")
            btn_eject.setToolTip("Eject / clear preview")
            btn_eject.setIcon(load_icon(window, "eject", icon_px))
            btn_eject.setIconSize(icon_size(icon_px))
            btn_eject.setFixedSize(btn_w, btn_h)
            self._transport_button(btn_eject)
            window.btn_preview_eject = btn_eject

            btn_waveform_res = QPushButton("RES")
            btn_waveform_res.setObjectName("previewTextToolButton")
            btn_waveform_res.setCheckable(True)
            btn_waveform_res.setToolTip("Higher waveform resolution when zoomed")
            btn_waveform_res.setFixedSize(btn_w, btn_h)
            btn_waveform_res.setEnabled(False)
            btn_waveform_res.toggled.connect(res_slot)
            self._transport_button(btn_waveform_res)
            setattr(window, res_attr, btn_waveform_res)

            wave_tools.addWidget(btn_play, 0, 0)
            wave_tools.addWidget(btn_pause, 0, 1)
            wave_tools.addWidget(btn_stop, 1, 0)
            if btn_autoplay is not None:
                wave_tools.addWidget(btn_autoplay, 1, 1)
            wave_tools.addWidget(btn_zoom_reset, 2, 0)
            wave_tools.addWidget(btn_rewind_start, 2, 1)
            wave_tools.addWidget(btn_waveform_res, 3, 0)
            wave_tools.addWidget(btn_eject, 3, 1)
        else:
            wave_tools = QVBoxLayout()
            wave_tools.setSpacing(4)
            wave_tools.setContentsMargins(0, 0, 0, 0)

            btn_zoom_reset = QPushButton("1:1")
            btn_zoom_reset.setObjectName("waveformToolButton")
            btn_zoom_reset.setToolTip("Reset waveform zoom")
            btn_zoom_reset.setFixedSize(btn_w, btn_h)
            btn_zoom_reset.setEnabled(False)
            btn_zoom_reset.clicked.connect(zoom_reset_slot)
            self._transport_button(btn_zoom_reset)
            setattr(window, zoom_reset_attr, btn_zoom_reset)
            wave_tools.addWidget(btn_zoom_reset, alignment=Qt.AlignmentFlag.AlignHCenter)

            btn_rewind_start = QPushButton()
            btn_rewind_start.setObjectName("waveformToolButton")
            btn_rewind_start.setToolTip("Rewind to start")
            btn_rewind_start.setIcon(load_icon(window, "back_begin", icon_px))
            btn_rewind_start.setIconSize(icon_size(icon_px))
            btn_rewind_start.setFixedSize(btn_w, btn_h)
            btn_rewind_start.clicked.connect(rewind_slot)
            self._transport_button(btn_rewind_start)
            setattr(window, rewind_attr, btn_rewind_start)
            wave_tools.addWidget(btn_rewind_start, alignment=Qt.AlignmentFlag.AlignHCenter)

            btn_waveform_res = QPushButton("RES")
            btn_waveform_res.setObjectName("waveformToolButton")
            btn_waveform_res.setCheckable(True)
            btn_waveform_res.setToolTip("Higher waveform resolution when zoomed")
            btn_waveform_res.setFixedSize(btn_w, btn_h)
            btn_waveform_res.setEnabled(False)
            btn_waveform_res.toggled.connect(res_slot)
            self._transport_button(btn_waveform_res)
            setattr(window, res_attr, btn_waveform_res)
            wave_tools.addWidget(btn_waveform_res, alignment=Qt.AlignmentFlag.AlignHCenter)
            wave_tools.addStretch()

        wave_row.addWidget(waveform, 1)
        wave_row.addLayout(wave_tools)
        block.addLayout(wave_row)

        time_row = QHBoxLayout()
        start_label = QLabel("0:00")
        start_label.setObjectName("timelineStart")
        end_label = QLabel("0:00")
        end_label.setObjectName("timelineEnd")
        end_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        setattr(window, start_label_attr, start_label)
        setattr(window, end_label_attr, end_label)
        time_row.addWidget(start_label)
        time_row.addStretch()
        time_row.addWidget(end_label)
        block.addLayout(time_row)

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
